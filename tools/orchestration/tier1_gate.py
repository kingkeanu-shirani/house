"""Tier-1 gate: the checks every tier-1 package runs before hand-over (docs/design/TIER1_INTEGRATION.md section 3).

usage:
  python tools/orchestration/tier1_gate.py record --id ID [--package PKG[+PKG]] [--prev ID|DIR|none]
                [--extras physics,camera,body,sweep,gen] [--camera room|geometric] [--only G2,G3] [--skip G7]
                [--jobs N] [--seeds 0 1 2 41 97] [--shared-tree] [--reviewed FILE] [--bench-repeats N]
                [--root DIR] [--out DIR] [--work DIR]
  python tools/orchestration/tier1_gate.py run ...          the same as record (the name used in TIER1_INTEGRATION 3.1)
  python tools/orchestration/tier1_gate.py compare [PREV [ID]] [--package PKG] [--shared-tree] [--spec-files GLOB]
                [--reviewed FILE]   (ID: the package's own record; PREV: the gate ID names, else the stage default)
  python tools/orchestration/tier1_gate.py scope --package PKG [--id ID] [--shared-tree]
  python tools/orchestration/tier1_gate.py isolate --out DIR [--package PKG] [--files PATH ...] [--base REV]
  python tools/orchestration/tier1_gate.py packages [PKG ...]
  python tools/orchestration/tier1_gate.py gen1-digests [--write] [--jobs N]
  python tools/orchestration/tier1_gate.py signoff [PREV [ID]] [--package PKG] [--by NAME] [--out FILE]
  python tools/orchestration/tier1_gate.py policy        (the committed allowlist and second signatures, as the gate
                                                          reads them)

A package id with a colon (ADOPT:cleaning) is written with "_" in folder names (gate/ADOPT_cleaning,
handoff_ADOPT_cleaning.md). On a shared tree (the adoption round) a package records on an isolated copy
(`isolate --package PKG --out DIR`, then `record --root DIR --gates-root <project>/catalog/analysis/tier1/gate`), or
records in place with --shared-tree, which attributes the files of the packages running beside it (every
ADOPT:<domain>, K-EVAL, K-CAM) instead of failing G10.

`record` runs the checks below on the working tree and writes catalog/analysis/tier1/gate/<ID>/ (meta.json,
summary.txt, tests.json, specs_own.jsonl, specs_realistic.jsonl, specs_summary.json, audits.json, bench.json,
extras.json, scope.json and, when there is a previous gate, compare.json / compare.txt). The last line printed is the
gate summary; the exit status is 0 only for PASS.

  G0   action fingerprint (house_sim.kernel.engine.actions_fingerprint) and code fingerprints
  G1   every tests/test_*.py, normal and HOUSE_SIM_STRICT=1 (one process per file and mode, like green_tests.py)
  G1b  per domain d: HOUSE_SIM_DOMAINS=d python -m unittest tests.test_<d>; kernel test files with
       HOUSE_SIM_DOMAINS=core (required from B1 on, recorded before)
  G1c  import-inert modules (TIER1_INTEGRATION 1.16): importing the package's library modules after every domain is
       loaded registers nothing in kernel.plugins
  G2   every tasks/authored spec in its own perception mode on the seeds (verify.run_trial per spec and seed)
  G3   the same with perception="realistic" forced; the specs whose own mode is realistic run in both and must give
       identical records (a free cross-process determinism check)
  G4   python tools/perception_audit.py 6 150 --workers 4 (also --camera geometric once the tool has it)
  G5   python tools/layout_audit.py 40 (counts equal to the previous gate) and the generation-1 house digests of
       tests/data/gen1_digests.json (TIER1_SWEEP 14.1)
  G6   python tools/fuzz.py 6 300 --workers 4 (strict plus a cross-process replay of every house; also --camera
       geometric and --battery 5 --faults random once the tool has them) and python tools/fuzz.py --coverage
       --workers 4 (spec-seeded walks toward every action's success path; per-action success coverage)
  G7   python tools/bench_systems.py 40 0 1 2 3 and the 1 h wait on seeds 0-3 (best of --bench-repeats, run alone)
  G8   python -m unittest tests.test_room_golden, also with HOUSE_SIM_GOLDEN_ALL=1 (required from B1 on)
  G9   package extras: physics_audit --data --bench, body_audit --baseline, generator compat and gen-2 layout,
       sweep_bench, geometric camera tools
  G10  scope: changed and untracked files (git) against the package's file list, its handoff and its gate folder;
       the gate's policy files (gate_policy/) are the main session's alone; FAIL when git is unavailable

Comparison with the previous gate (TIER1_INTEGRATION 3.2-3.4): a verdict change (G2/G3) passes only when a reviewed
row covers it (a row of the package's handoff table, gate/<ID>/reviewed.json or --reviewed FILE naming the catalog
id, the seeds, the mode and one of the package's R codes); digest changes (reference_state_hash and the other state
digests, the reference observation digest), an action fingerprint change and layout or generation-1 changes pass only
for packages whose row allows them; benchmarks may not exceed the reference by more than the package's factor.
The package table (PACKAGES) is TIER1_INTEGRATION section 2 transcribed; `packages` prints it.

Hardening (phase 1 of the stabilisation; REVIEW_REPORT H12, top-10 item 3; phases/p2_tests_gate.md T3-T5):
* Absolute rules beside the delta rule. G2-ABS: every authored spec passes in its own mode on every seed (new specs
  included, in `record` and in `compare`). G1 / G1b / G8: every test passes. The only exception is an entry of the
  dated allowlist tools/orchestration/gate_policy/known_failing.json (kind "test" or "spec", owner, expiry date,
  reason); an expired or malformed entry excuses nothing.
* Second signature. A reviewed row is bound to the files its package owns (a domain package reviews only its own
  tasks/authored/<domain>__*.json specs), and every verdict change, every edited spec (spec_hash changed) and every
  action the coverage fuzz no longer drives to success also needs a row of tools/orchestration/gate_policy/signoffs/
  <ID>.json naming the exact new verdict digest / spec hash / action (`signoff` drafts it). Policy files count only
  as committed (git HEAD; in an isolated copy, unchanged since its base commit): agents never commit, so only the main
  session signs, and G10 fails any package that edits them.
* Wrong plans: each trial record keeps every wrong plan's reason classes ("reasons"), labels, goal fraction, whether
  it ran fully and its error class; a change of the reasons is a verdict change (compared when both records have
  them). A wrong plan that raises fails its spec (house_sim/tasks/verify.py).
* SKIP counts as FAIL; a check that does not apply to the package is "N/A". G10 without git (and not an isolated
  copy) FAILS. `compare` fails on any G4 leak or crash and any G6 defect or nondeterminism (absolute, and on every
  increase or missing variant), and on the current record's own G1c / G9 (and G10) failures.
* G6 also runs `tools/fuzz.py --coverage` (every registered action driven toward success from every authored spec's
  setup) and reports per-action success coverage; actions that stop succeeding need a signature.

Phase-1 red team (catalog/analysis/phase1/red_defects.json; FIX2):
* Per-test outcomes. Every test process runs through the `_unittest` worker (the same report and exit status as
  `python -m unittest`) and tests.json keeps each test id and every outcome that is not "ok" (skip, xfail, xpass,
  fail, error). Against the previous record a test id that disappears, becomes skipped or an expected failure, a test
  file that is gone, or (when either side lacks per-test ids) a file that ran fewer tests or skipped more fails G1 /
  G1b / G8 in `record` and in `compare`, unless the committed allowlist has a dated entry of kind "skip" naming the
  test (or its class or module) or the record's committed signoff has a row {"kind": "test-removal", "test", "mode"?}.
* Tree fingerprints. Every check result carries the fingerprint of the tree it ran on (tree_fingerprint: the content
  of house_sim, tasks, tests, tools, docs and the CI files) and the record keeps the tree it was written on. A record
  whose checks ran on different trees (a partial re-run merged with an earlier run, or files edited while the gate
  ran) is STALE: never a hand-over, exit status 1. `compare` recomputes the fingerprint of the record's tree and
  fails a record that does not match it (STALE); a record without one is INCOMPLETE; --any-tree turns the check off
  for historical comparisons.
* Honest compare. Every check a record lists in "recorded" whose data is missing is SKIP (FAIL); the record's own
  statuses of G0, G1c, G4, G5, G7, G9 and G10 count (G1 / G1b / G8, G2, G3 and G6 are recomputed with the current
  allowlist and signatures); G2-ABS runs over the record's full own-mode rows whatever the previous record ran; a
  partial or narrowed previous record makes the result INCOMPLETE, and `record --prev` refuses one.
* Policy root. The allowlist and the second signatures are read only from the commits of the project this script
  belongs to (policy_root), never from --root: an isolated copy (whose manifest and files are writable) or any other
  folder is never a policy source, and G10 in an isolated copy compares its files with the project's base commit
  through git, not with the copy's own manifest. `compare` refuses a --root other than the project unless
  --foreign-root is given; meta.json records the policy root.

Stdlib only; Python 3.9+. No network. Worker processes import house_sim from --root.
"""
import argparse
import fnmatch
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent.parent
GATE_DIR = "catalog/analysis/tier1/gate"
HANDOFF_DIR = "catalog/analysis/tier1"
SEEDS = (0, 1, 2, 41, 97)
CHECKS = ("G0", "G1", "G1b", "G1c", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10")
DOMAINS = ("admin_inventory", "appliances_cooking", "cleaning", "climate", "electrical", "food", "manipulation",
           "mechanisms", "navigation_robot", "outdoor_plants", "people", "pets", "plumbing_liquids", "safety",
           "textiles_laundry", "tools_vehicles")
# kernel test files that must also pass with HOUSE_SIM_DOMAINS=core (G1b, TIER1_INTEGRATION 3.1)
KERNEL_TESTS = ("test_kernel*.py", "test_perception.py", "test_physics_*.py", "test_sight.py", "test_camera*.py",
                "test_body_*.py", "test_timeline.py", "test_world_savepoint.py", "test_monitor.py", "test_phases.py",
                "test_clock.py", "test_predicates_v3.py", "test_dialogue.py", "test_reports.py", "test_mind.py",
                "test_requests_obs.py")
RUNTIME_ENV = ("HOUSE_SIM_DOMAINS", "HOUSE_SIM_STRICT", "HOUSE_SIM_SAFE", "HOUSE_SIM_GOLDEN_ALL")
TRANSIENT = ("tools/.fuzz_replay_*.json",)        # written and removed by tools/fuzz.py while G6 runs
CATALOG_ID = re.compile(r"\b[A-Z]-[0-9a-f]{14}-\d{2}\b")
CODE = re.compile(r"(?<![\w-])(?:[A-Z]-)?R\d+(?![\w-])|(?<![\w-])(?:D[123]|ADOPT|P1)(?![\w-])")
# the gate's policy files (main session only): the dated allowlist of known failures and the second signatures. They
# count only as committed (trusted_json); no package may change them (G10) except K-GATE, which defines their format
POLICY_DIR = "tools/orchestration/gate_policy"
KNOWN_FAILING = POLICY_DIR + "/known_failing.json"
SIGNOFF_DIR = POLICY_DIR + "/signoffs"
PROTECTED = (POLICY_DIR + "/*",)
# what a gate record's checks depend on (tree_fingerprint, FIX2): code, data, specs, tests, tools, docs and the CI files --
# not the gate records, results and analysis reports written beside them
FINGERPRINT_PATHS = ("house_sim", "tasks", "tests", "tools", "docs", ".github", "setup.py", "setup_project.py",
                     "house_env.py", "MANIFEST.in")
# the record's own statuses compare honours (the other checks it recomputes, with the allowlist and signatures as
# committed now): FIX2
OWN_STATUS = ("G0", "G1c", "G4", "G5", "G7", "G9", "G10")
KNOWN_FAILING_FORMAT = "house-gate-known-failing-v1"
SIGNOFF_FORMAT = "house-gate-signoff-v1"
# statuses: PASS, FAIL, INFO (recorded, no condition), SKIP (a check that should have run did not: counts as FAIL),
# N/A (the check does not apply to this package: counts as passing)
FAILING = ("FAIL", "SKIP")
TEST_MODES = ("normal", "strict", "isolated", "core", "golden", "golden_all")

# ----------------------------------------------------------------------------------------------------------------
# packages (TIER1_INTEGRATION.md sections 2.2-2.6, 3.2 and 3.4)
#
# files    globs of the files the package may create or edit (G10); its handoff and gate folder are always allowed
# allow    difference classes it may change against the previous gate:
#            fingerprint  the action fingerprint
#            state        reference_state_hash and the other state digests (idle, wrong plans, initial config, task),
#                         reference_steps and reference_time_s
#            obs          the digest of the reference run's final observation
#            verdict      G2/G3 verdicts, each one only with a reviewed row citing one of `codes`
#            layout       layout_audit counts;  gen1  generation-1 house digests
# bench    (factor, reference): G7 primary metrics may not exceed reference * factor ("prev" or a gate id)
# extras   G9 extras run by default;  inert  module globs that must import without registering anything (G1c)
# requires features that must exist for this package (golden, kernel_core, camera_tools, fuzz_battery, bench_day)

SWEEP_LIB_TESTS = ("tests/test_monitor.py", "tests/test_phases.py")
# files `python -m house_sim --verify` rewrites besides reports/*: the coverage table (audit.report) and the compiled
# catalog (compiler.compile_specs); the close steps regenerate them (B6 / C1 handoffs, request 2)
VERIFY_OUTPUTS = ("coverage.csv", "tasks/specs.jsonl", "tasks/id_to_family.jsonl", "catalog/entries.jsonl",
                  "catalog/manifest.json")
# the compiled specs follow tasks/authored/*.json: a domain that edits its specs and recompiles rewrites them
COMPILED_SPECS = ("tasks/specs.jsonl", "tasks/id_to_family.jsonl")
# empty directories the project needs that git does not carry (an isolated copy made from `git archive` lacks them:
# audit.fingerprint globs tasks/templates and tests copy it; house_sim/data/actions holds optional action files)
EMPTY_DIRS = ("tasks/templates", "house_sim/data/actions")


def _pkg(stage, files, allow=(), codes=(), extras=(), inert=(), bench=(1.05, "prev"), requires=(), aka=(), note="",
         policy=False):
    """policy: the package may change the gate's policy files (PROTECTED; only K-GATE, which defines their format)."""
    return {"stage": stage, "files": list(files), "allow": list(allow), "codes": list(codes), "extras": list(extras),
            "inert": list(inert), "bench": list(bench), "requires": list(requires), "aka": list(aka), "note": note,
            "policy": bool(policy)}


PACKAGES = {
    "A0": _pkg("A0", [
        "tools/orchestration/tier1_gate.py", "tools/orchestration/README.md", "house_sim/kernel/timeline.py",
        "house_sim/systems/core.py", "house_sim/kernel/engine.py", "house_sim/kernel/dialogue.py",
        "house_sim/kernel/reports.py", "house_sim/kernel/mind.py", "house_sim/kernel/monitor.py",
        "house_sim/kernel/phases.py", "house_sim/generator/profile.py", "tests/data/gen1_digests.json"],
        allow=["fingerprint"],
        inert=["house_sim/kernel/dialogue.py", "house_sim/kernel/reports.py", "house_sim/kernel/mind.py",
               "house_sim/kernel/monitor.py", "house_sim/kernel/phases.py", "house_sim/generator/profile.py"],
        aka=["TIER1-GATE"], note="the timeline move changes the action fingerprint (wait's duration module) only"),
    # ---- stage A: libraries, zero behaviour change
    "PHYS-DATA": _pkg("A", [
        "house_sim/data/physics/materials.json", "house_sim/data/physics/liquids.json",
        "house_sim/data/physics/floors.json", "house_sim/data/physics/supports.json", "house_sim/data/physics/laws.json",
        "house_sim/kernel/phys/__init__.py", "house_sim/kernel/phys/props.py", "house_sim/kernel/phys/lawbook.py",
        "house_sim/kernel/phys/forces.py", "house_sim/kernel/phys/contain.py", "setup.py",
        "tests/test_physics_data.py", "tests/test_physics_forces.py"],
        inert=["house_sim/kernel/phys/*.py"], extras=["physics"]),
    "PHYS-STATICS": _pkg("A", [
        "house_sim/kernel/phys/support.py", "house_sim/kernel/phys/statics.py", "house_sim/kernel/phys/impact.py",
        "tests/test_physics_statics.py"], inert=["house_sim/kernel/phys/*.py"], extras=["physics"]),
    "PHYS-FLUIDS": _pkg("A", [
        "house_sim/kernel/phys/liquids.py", "house_sim/kernel/phys/thermal.py", "house_sim/kernel/phys/environment.py",
        "house_sim/kernel/phys/processes.py", "tests/test_physics_fluids.py", "tests/test_physics_thermal.py"],
        inert=["house_sim/kernel/phys/*.py"], extras=["physics"]),
    "CAM-SIGHT": _pkg("A", [
        "house_sim/kernel/sight.py", "house_sim/kernel/registry.py", "house_sim/data/types/core.json",
        "tests/test_sight.py"], inert=["house_sim/kernel/sight.py"], extras=["camera"]),
    "BODY-MODEL": _pkg("A", [
        "house_sim/kernel/body.py", "house_sim/kernel/body_faults.py", "house_sim/kernel/proprio.py",
        "house_sim/data/robot/profiles.json", "tools/body_audit.py", "tests/test_body_model.py"],
        inert=["house_sim/kernel/body.py", "house_sim/kernel/body_faults.py", "house_sim/kernel/proprio.py"],
        extras=["body"]),
    "GEN": _pkg("A", [
        "house_sim/generator/profile.py", "house_sim/generator/belongings.py", "house_sim/generator/furnishings.py",
        "house_sim/generator/climate.py", "house_sim/data/generator/households.json",
        "house_sim/data/generator/belongings.json", "house_sim/data/generator/furnishings.json",
        "house_sim/data/generator/climate_normals.json", "house_sim/generator/__init__.py",
        "house_sim/generator/layout.py", "house_sim/generator/place.py", "house_sim/generator/repair.py",
        "tools/layout_audit.py", "tools/show_house.py", "tests/test_generator_gen2.py",
        "tests/test_generator_compat.py", "docs/GENERATOR.md"], extras=["gen"],
        bench=(1.05, "prev"), note="furnishers registered but returning at once for gen 1 are allowed (1.16)"),
    "TIME-EVAL-LIB": _pkg("A", [
        "house_sim/kernel/monitor.py", "house_sim/kernel/phases.py", "house_sim/data/calendar/*.json",
        "tests/test_monitor.py", "tests/test_phases.py"],
        inert=["house_sim/kernel/monitor.py", "house_sim/kernel/phases.py"],
        note="TIME-EVAL's new library files during stages A/B (2.5); hand-over happens once, in stage C"),
    "PEOPLE-IO-LIB": _pkg("A", [
        "house_sim/kernel/dialogue.py", "house_sim/kernel/reports.py", "house_sim/kernel/mind.py",
        "house_sim/data/vocab/*.json", "tools/vocab.py", "tests/test_dialogue.py", "tests/test_reports.py",
        "tests/test_mind.py"],
        inert=["house_sim/kernel/dialogue.py", "house_sim/kernel/reports.py", "house_sim/kernel/mind.py"],
        note="PEOPLE-IO's new library files during stages A/B (2.5); hand-over happens once, in stage C"),
    # ---- stage B (integration, merge order)
    "B0": _pkg("B0", [], note="main session: must equal A0 except benchmark noise"),
    "PHYS-INT": _pkg("B1", [
        "house_sim/kernel/world.py", "house_sim/kernel/engine.py", "house_sim/kernel/actions.py",
        "house_sim/kernel/physics.py", "house_sim/kernel/phys/*", "house_sim/systems/core.py",
        "house_sim/tasks/setup.py", "house_sim/tasks/verify.py", "house_sim/data/types/core.json",
        "house_sim/data/physics/*.json", "tools/physics_audit.py", "tools/golden.py", "tools/perception_audit.py",
        "tests/test_physics_settle.py", "tests/test_physics_setup.py", "tests/test_room_golden.py",
        "tests/data/room_golden.json", "docs/KERNEL_API.md", "docs/PHYSICS.md", "docs/CONVENTIONS.md",
        "docs/AUTHORING_GUIDE.md", "docs/PERCEPTION.md", "docs/KNOWN_LIMITS.md"],
        allow=["state", "obs", "verdict"], codes=["R%d" % i for i in range(1, 11)], extras=["physics"],
        bench=(1.0, "A0"), requires=["golden", "kernel_core"],
        note="R1-R10; whole engine must not exceed A0 at all (3.4)"),
    "CAM-RUNTIME": _pkg("B2", [
        "house_sim/kernel/camera.py", "house_sim/kernel/engine.py", "house_sim/kernel/perception.py",
        "house_sim/kernel/actions.py", "house_sim/kernel/sight.py", "house_sim/kernel/phys/observe.py",
        "tests/test_camera.py", "docs/KERNEL_API.md"], bench=(1.05, "prev"),
        note="nothing may change in room mode"),
    "CAM-ACTIONS": _pkg("B3", [
        "house_sim/systems/core.py", "house_sim/kernel/camera.py", "house_sim/kernel/sight.py",
        "house_sim/kernel/actions.py", "house_sim/tasks/setup.py", "house_sim/tasks/spec.py",
        "house_sim/tasks/runner.py", "house_sim/tasks/verify.py", "house_sim/tasks/skills.py", "house_sim/cli.py",
        "tests/test_camera_actions.py", "docs/ACTION_API.md", "docs/TASK_SPEC.md", "docs/AUTHORING_GUIDE.md"],
        allow=["fingerprint"]),
    "CAM-RENDER": _pkg("B3", [
        "house_sim/kernel/realism.py", "house_sim/kernel/engine.py", "house_sim/kernel/perception.py",
        "house_sim/agent.py", "house_sim/eval/run.py", "house_sim/eval/agents.py", "house_sim/eval/llm.py",
        "house_sim/eval/dataset.py", "house_sim/eval/viewer.py", "house_sim/eval/viewer_template.html",
        "house_sim/eval/leaderboard.py", "tools/perception_audit.py", "tools/fuzz.py", "tools/bench_systems.py",
        "tests/test_camera_realism.py", "docs/PERCEPTION.md", "docs/VIEWER.md", "docs/EVAL_HARNESS.md",
        "docs/KNOWN_LIMITS.md"], extras=["camera"], requires=["camera_tools"]),
    "BODY-ENGINE": _pkg("B4", [
        "house_sim/kernel/engine.py", "house_sim/kernel/actions.py", "house_sim/kernel/schema.py",
        "house_sim/kernel/timeline.py", "house_sim/kernel/body.py", "house_sim/kernel/body_faults.py",
        "house_sim/kernel/phys/forces.py", "house_sim/kernel/phys/support.py", "house_sim/data/robot/profiles.json",
        "house_sim/systems/core.py", "tests/test_body_engine.py", "tests/test_kernel.py",
        "tests/data/room_golden.json", "docs/KERNEL_API.md", "docs/CONVENTIONS.md", "docs/KNOWN_LIMITS.md"],
        allow=["state", "obs", "verdict"], codes=["B-R%d" % i for i in range(1, 7)], extras=["body"],
        note="must merge together with NAV-M2 (--package BODY-ENGINE+NAV-M2)"),
    "NAV-M2": _pkg("B4", [
        "house_sim/systems/navigation_robot/traverse.py", "house_sim/systems/navigation_robot/robot.py",
        "house_sim/systems/navigation_robot/paths.py", "house_sim/systems/navigation_robot/world.py",
        "tests/test_navigation_robot.py"], allow=["state", "obs", "verdict"],
        codes=["B-R%d" % i for i in range(1, 7)], note="navigation_robot domain agent, paired with BODY-ENGINE"),
    "BODY-CORE": _pkg("B5", [
        "house_sim/systems/core.py", "house_sim/kernel/engine.py", "house_sim/kernel/body.py",
        "house_sim/data/robot/profiles.json", "house_sim/tasks/setup.py", "house_sim/tasks/skills.py",
        "tests/test_body_core.py", "tests/test_kernel.py", "docs/AUTHORING_GUIDE.md", "docs/ACTION_API.md",
        "tasks/authored/*.json"],
        allow=["state", "obs", "verdict"], codes=["B-R%d" % i for i in range(7, 12)], extras=["body"],
        note="specs re-authored for B-R7/B-R9 are edited by their owners (listed in the handoff); "
             "must merge together with NAV-M5"),
    "NAV-M5": _pkg("B5", [
        "house_sim/systems/navigation_robot/paths.py", "house_sim/systems/navigation_robot/follow.py"],
        allow=["state", "obs", "verdict"], codes=["B-R%d" % i for i in range(7, 12)]),
    "BODY-FAULTS": _pkg("B5", [
        "house_sim/kernel/perception.py", "house_sim/kernel/realism.py", "house_sim/kernel/body_faults.py",
        "house_sim/kernel/proprio.py", "tools/perception_audit.py", "tools/fuzz.py", "tests/test_body_faults.py",
        "tests/test_body_perception.py", "tests/test_perception.py", "tests/data/room_golden.json",
        "docs/PERCEPTION.md", "docs/EVAL_HARNESS.md"],
        allow=["obs"], codes=["B-R%d" % i for i in range(12, 16)], extras=["body"], requires=["fuzz_battery"]),
    "B-CLOSE": _pkg("B6", [
        "house_sim/eval/viewer.py", "house_sim/eval/viewer_template.html", "house_sim/eval/dataset.py",
        "docs/DATASET.md", "docs/VIEWER.md", "docs/KNOWN_LIMITS.md", "house_sim/data/types/core.json",
        "demos/replays/*", "demos/manifest.json", "reports/*", "tests/data/spec_baseline.json"] + list(VERIFY_OUTPUTS),
        aka=["B6"], note="main session; regenerated evidence only, no verdict or digest of an existing spec changes"),
    # ---- stage C
    "TIME-EVAL": _pkg("C", [
        "house_sim/kernel/monitor.py", "house_sim/kernel/phases.py", "house_sim/data/calendar/*.json",
        "house_sim/kernel/timeline.py", "house_sim/kernel/engine.py", "house_sim/kernel/world.py",
        "house_sim/kernel/evaluation.py", "house_sim/kernel/predicates.py", "house_sim/kernel/clock.py",
        "house_sim/kernel/plugins.py", "house_sim/kernel/actions.py", "house_sim/kernel/physics.py",
        "house_sim/kernel/camera.py", "house_sim/kernel/phys/observe.py", "house_sim/tasks/spec.py",
        "house_sim/tasks/runner.py", "house_sim/tasks/verify.py", "house_sim/eval/run.py", "house_sim/eval/agents.py",
        "house_sim/cli.py", "tools/verify.py", "tools/bench_systems.py", "tools/sweep_bench.py",
        "tests/test_timeline.py", "tests/test_world_savepoint.py", "tests/test_monitor.py", "tests/test_phases.py",
        "tests/test_clock.py", "tests/test_predicates_v3.py", "docs/TASK_SPEC.md", "docs/EVALUATION.md",
        "docs/KERNEL_API.md", "docs/EVAL_CONTRACTS.md"],
        allow=["fingerprint", "verdict"], codes=["S-R1", "S-R2", "S-R3", "S-R4"], extras=["sweep"],
        requires=["bench_day"], note="merges first in stage C"),
    "PEOPLE-IO": _pkg("C", [
        "house_sim/kernel/dialogue.py", "house_sim/kernel/reports.py", "house_sim/kernel/mind.py",
        "house_sim/data/vocab/*.json", "tools/vocab.py", "house_sim/systems/core.py", "house_sim/kernel/realism.py",
        "house_sim/kernel/perception.py", "house_sim/tasks/setup.py", "house_sim/tasks/skills.py",
        "house_sim/eval/llm.py", "tools/perception_audit.py", "tests/test_dialogue.py", "tests/test_reports.py",
        "tests/test_mind.py", "tests/test_requests_obs.py", "tests/test_perception.py", "docs/DIALOGUE.md",
        "docs/PERCEPTION.md", "docs/EVAL_HARNESS.md", "docs/AUTHORING_GUIDE.md", "docs/CONVENTIONS.md"],
        allow=["fingerprint", "obs"], codes=["S-R5", "S-R6", "S-R7"], extras=["sweep"]),
    "C-CLOSE": _pkg("C1", [
        "demos/replays/*", "demos/manifest.json", "reports/*", "datasets/*", "tests/data/spec_baseline.json"] +
        list(VERIFY_OUTPUTS), aka=["C1"], note="main session; regenerated evidence only"),
    # ---- stage D (after the adoption round; TIER1_INTEGRATION 2.6 and 4)
    "PHYS-FLIP": _pkg("D1", ["house_sim/data/physics/laws.json", "docs/PHYSICS.md"],
                      allow=["state", "obs", "verdict"], codes=["D1"], aka=["D1"], extras=["physics"],
                      note="per-spec triage rows cite D1; spec fixes are made by their owners"),
    "M-GEN2": _pkg("D2", ["tasks/authored/*.json", "catalog/analysis/gen2_migration.json"],
                   allow=["state", "obs", "verdict"], codes=["D2"], aka=["D2"], extras=["gen"]),
    "CAM-FLIP": _pkg("D3", [
        "house_sim/kernel/camera.py", "docs/PERCEPTION.md", "docs/EVAL_HARNESS.md", "docs/AUTHORING_GUIDE.md",
        "docs/KNOWN_LIMITS.md", "tasks/authored/*.json"],
        allow=["state", "obs", "verdict"], codes=["D3"], aka=["D3"], extras=["camera"]),
}
for _d in DOMAINS:
    PACKAGES["ADOPT:" + _d] = _pkg("ADOPT", [
        "house_sim/systems/{}/*".format(_d), "house_sim/data/types/{}.json".format(_d), "tests/test_{}.py".format(_d),
        "docs/domains/{}.md".format(_d), "tasks/authored/{}__*.json".format(_d)] + list(COMPILED_SPECS),
        allow=["state", "obs", "verdict", "fingerprint"], codes=["ADOPT"],
        note="domain adoption round (section 6): the domain's own files (and the compiled specs its spec edits "
             "rewrite)")
# the adoption round's two kernel follow-up agents (tools/orchestration/tier1_adopt.js): K-EVAL owns the evaluation
# side (eval, audit, verify, predicates, engine, mind, tools) and may change no verdict or digest; K-CAM owns the
# camera side and may change behaviour behind reviewed rows (code ADOPT), like the domain packages it runs beside
PACKAGES["K-EVAL"] = _pkg("ADOPT", [
    "house_sim/eval/*", "house_sim/audit.py", "house_sim/cli.py", "house_sim/tasks/verify.py",
    "house_sim/kernel/predicates.py", "house_sim/kernel/engine.py", "house_sim/kernel/mind.py",
    "house_sim/kernel/replays.py", "tools/*", "tests/test_eval_*.py", "tests/test_kernel*.py", "tests/test_mind.py",
    "tests/test_predicates_v3.py", "tests/test_monitor.py", "tests/test_phases.py", "tests/test_body_audit.py",
    "tests/test_camera_realism.py", "docs/KERNEL_API.md", "docs/VIEWER.md", "docs/DATASET.md",
    "docs/EVAL_HARNESS.md", "docs/EVAL_CONTRACTS.md", "docs/KNOWN_LIMITS.md", "docs/TASK_SPEC.md",
    "docs/design/TIER1_INTEGRATION.md",
    ".gitignore", "demos/replays/*", "demos/manifest.json", "catalog/analysis/tier1/handoff_TIER1-GATE.md",
    "reports/unit_tests.txt", "reports/unit_test_summary.json", "reports/cli_checks.json",
    "reports/package_checks.json", "reports/package_build.txt", "reports/compilation_summary.json"],
    note="adoption-round kernel follow-ups (evaluation side): no verdict, digest or fingerprint may change (its "
         "gate runs on an isolated copy, `isolate --package K-EVAL`, against a pristine one)")
PACKAGES["K-CAM"] = _pkg("ADOPT", [
    "house_sim/kernel/sight.py", "house_sim/kernel/camera.py", "house_sim/kernel/perception.py",
    "house_sim/kernel/realism.py", "house_sim/kernel/timeline.py", "house_sim/tasks/setup.py", "house_sim/agent.py",
    "house_sim/tasks/skills.py",
    "tests/test_camera*.py", "tests/test_sight.py", "tests/test_perception.py", "tests/test_timeline.py",
    "tests/test_kernel.py", "docs/PERCEPTION.md", "docs/KERNEL_API.md", "docs/KNOWN_LIMITS.md", "docs/TASK_SPEC.md"],
    allow=["state", "obs", "verdict", "fingerprint"], codes=["ADOPT"], extras=["camera"],
    note="adoption-round kernel follow-ups (camera side)")
# the adoption integrator (tools/orchestration/tier1_adopt.js, phase Integrate): the kernel-side fixes and kernel
# requests of the round (with regression tests), the round's re-records (generation-1 digests, room golden, the
# compiled specs) and the regenerated evidence; may change generation-1 digests (domain type data, reviewed)
PACKAGES["ADOPT-INT"] = _pkg("ADOPT", [
    "house_sim/kernel/*", "house_sim/systems/core.py", "house_sim/tasks/*", "house_sim/generator/*",
    "house_sim/eval/*", "house_sim/audit.py", "house_sim/cli.py", "house_sim/agent.py",
    "house_sim/data/types/core.json", "house_sim/data/physics/*", "house_sim/data/robot/*", "house_sim/data/vocab/*",
    "house_sim/data/generator/*", "house_sim/data/calendar/*",
    "tests/test_kernel*.py", "tests/test_physics_*.py", "tests/test_camera*.py", "tests/test_body_*.py",
    "tests/test_sight.py", "tests/test_generator*.py", "tests/test_clock.py", "tests/test_reports.py",
    "tests/test_dialogue.py", "tests/test_mind.py", "tests/test_timeline.py", "tests/test_room_golden.py",
    "tests/test_perception.py", "tests/test_eval_*.py", "tests/test_requests_obs.py", "tests/test_world_savepoint.py",
    "tests/test_monitor.py", "tests/test_phases.py", "tests/test_predicates_v3.py", "tests/data/*", "tools/*",
    "docs/[A-Z]*.md", "docs/design/TIER1_INTEGRATION.md", "catalog/analysis/tier1/adopt_status.json",
    "reports/*", "demos/*", "datasets/*", "coverage.csv", "catalog/entries.jsonl", "catalog/manifest.json"]
    + list(COMPILED_SPECS),
    allow=["state", "obs", "verdict", "fingerprint", "gen1"], codes=["ADOPT"],
    extras=["physics", "camera", "body", "sweep", "gen"],
    note="the adoption round's integrator: kernel-side fixes, re-records and evidence (docs/domains/* and the "
         "domains' files stay the domains')")
# the adoption integrator's union (`record --id ADOPT`): every adoption package at once
PACKAGES["ADOPT-ALL"] = _pkg("ADOPT", sorted({f for pid, pkg in PACKAGES.items() if pkg["stage"] == "ADOPT"
                                              for f in pkg["files"]}),
                             allow=["state", "obs", "verdict", "fingerprint", "gen1"], codes=["ADOPT"], aka=["ADOPT"],
                             extras=["physics", "camera", "body", "sweep", "gen"],
                             note="the merged adoption round (16 domains, K-EVAL, K-CAM)")
# phase 1 of the stabilisation after the independent review (REVIEW_REPORT top-10 item 3): the gate and process
# tooling. Its verify.py change (a wrong plan that raises is not evidence) changes verdicts of specs it does not own:
# each one needs a reviewed row (code P1) and the main session's signature like any other package's
PACKAGES["K-GATE"] = _pkg("P1", [
    "tools/orchestration/*", "tools/fuzz.py", "house_sim/tasks/verify.py", "house_sim/eval/run.py",
    ".github/workflows/ci.yml", "tests/test_gate_tool.py", "tests/test_verify_records.py", "docs/EVAL_HARNESS.md",
    "catalog/analysis/phase1/K-GATE.json", "catalog/analysis/tier1/handoff_K-GATE.md"],
    allow=["verdict"], codes=["P1"], policy=True,
    note="phase-1 kernel agent: gate and process tooling; may write the policy files' format (the main session "
         "commits them)")
# the phase-1 kernel agents K-FIX and K-LEAK and the phase-1 integrator (INT: kernel requests of the round, the
# re-records, the phase reports in catalog/analysis/phase1/): the kernel side (ADOPT-INT's files) plus the files those
# packages add outside it -- K-LEAK's tests/test_leaks_review.py, the integrator's results and folder keepers, the
# embodiment design note on the felt load and the verifier-hardening decision record committed with this phase
PACKAGES["P1-INT"] = _pkg("P1", sorted(set(PACKAGES["ADOPT-INT"]["files"]) | {
    "tests/test_leaks_review.py", "catalog/analysis/phase1/*", "docs/design/TIER1_EMBODIMENT.md",
    "docs/design/VERIFY_HARDENING_DECISIONS.md", "tasks/templates/.gitkeep", "house_sim/data/actions/.gitkeep"}),
    allow=["state", "obs", "verdict", "fingerprint", "gen1"], codes=["P1"],
    extras=["physics", "camera", "body", "sweep", "gen"],
    note="phase-1 kernel agents (K-FIX, K-LEAK) and the phase-1 integrator: kernel-side fixes and requests, re-records")
# the phase-1 red team's fix pass on the non-domain files (FIX2: catalog/analysis/phase1/FIX2.json): the kernel side
# (P1-INT's files), the gate and process tooling it hardened (K-GATE's), the CI workflow and the one factual update of
# the verifier-hardening design (the hidden seed range)
PACKAGES["P1-FIX2"] = _pkg("P1", sorted(set(PACKAGES["P1-INT"]["files"]) | set(PACKAGES["K-GATE"]["files"]) | {
    "docs/design/VERIFY_HARDENING.md"}),
    allow=["state", "obs", "verdict", "fingerprint", "gen1"], codes=["P1"],
    extras=["physics", "camera", "body", "sweep", "gen"],
    note="phase-1 red-team fix pass (non-domain files): kernel leaks, gate honesty, verifier interim rule, CI, tools")
# the main session's phase-1 integration record (`record --id P1 --package P1 --prev ADOPT`): the domains' fixes
# (their ADOPT:<domain> files), the kernel side (ADOPT-INT's, P1-INT's and P1-FIX2's files) and K-GATE's; it may change
# the policy files (the main session signs) and, like every package, needs reviewed rows plus signatures for each
# verdict change and edit
PACKAGES["P1-ALL"] = _pkg("P1", sorted(set(PACKAGES["ADOPT-ALL"]["files"]) | set(PACKAGES["K-GATE"]["files"])
                                       | set(PACKAGES["P1-INT"]["files"]) | set(PACKAGES["P1-FIX2"]["files"])),
                          allow=["state", "obs", "verdict", "fingerprint", "gen1"], codes=["ADOPT", "P1"], aka=["P1"],
                          extras=["physics", "camera", "body", "sweep", "gen"], policy=True,
                          note="phase 1 of the stabilisation, merged (16 domains' fixes, kernel agents, K-GATE)")
STAGE_ORDER = ("A0", "A", "B0", "B1", "B2", "B3", "B4", "B5", "B6", "C", "C1", "ADOPT", "P1", "D1", "D2", "D3")
DEFAULT_PREV = {"A": "A0", "B0": "A0", "B1": "B0", "ADOPT": "C1", "P1": "ADOPT"}
# packages that run in parallel inside one sub-stage (G10 --shared-tree attributes their files instead of failing)
SUBSTAGES = {"A": ("PHYS-DATA", "PHYS-STATICS", "PHYS-FLUIDS", "CAM-SIGHT", "BODY-MODEL", "GEN", "TIME-EVAL-LIB",
                   "PEOPLE-IO-LIB"),
             "B3": ("CAM-ACTIONS", "CAM-RENDER"), "B4": ("BODY-ENGINE", "NAV-M2"),
             "B5": ("BODY-CORE", "NAV-M5", "BODY-FAULTS"), "C": ("TIME-EVAL", "PEOPLE-IO"),
             "ADOPT": tuple(["ADOPT:" + _d for _d in DOMAINS] + ["K-EVAL", "K-CAM", "ADOPT-INT"]),
             # phase 1: the 16 domain owners fix their own files (their ADOPT:<domain> packages) beside K-GATE
             "P1": tuple(["ADOPT:" + _d for _d in DOMAINS] + ["K-GATE"])}


def gate_dirname(name):
    """A gate id as a folder name (package ids like ADOPT:cleaning carry a colon, which Windows paths forbid)."""
    return str(name).replace(":", "_")
# feature -> (stage that introduces it, introducing package, what it is); required for later stages
FEATURES = {
    "golden": ("B1", "PHYS-INT", "tests/test_room_golden.py (G8)"),
    "kernel_core": ("B1", "PHYS-INT", "kernel test files green with HOUSE_SIM_DOMAINS=core (G1b)"),
    "camera_tools": ("B3", "CAM-RENDER", "--camera geometric in tools/perception_audit.py and tools/fuzz.py (G4, G6)"),
    "fuzz_battery": ("B5", "BODY-FAULTS", "--battery N --faults random in tools/fuzz.py (G6)"),
    "bench_day": ("C", "TIME-EVAL", "--day in tools/bench_systems.py (G7)"),
}
EXTRA_TOOLS = {"physics": ("tools/physics_audit.py", "PHYS-INT"), "body": ("tools/body_audit.py", "BODY-MODEL"),
               "gen": ("tests/test_generator_compat.py", "GEN"), "sweep": ("tools/sweep_bench.py", "TIME-EVAL"),
               "camera": ("tools/perception_audit.py", "CAM-RENDER")}


def stage_index(stage):
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def resolve_package(name):
    """Package id (or alias) -> canonical id; None when unknown."""
    if not name:
        return None
    upper = name.strip()
    for pid, pkg in PACKAGES.items():
        if upper == pid or upper in pkg["aka"]:
            return pid
    return None


def policy_for(names, stage=None):
    """Union policy of one or more packages ("BODY-ENGINE+NAV-M2"). names: list of canonical ids (may be empty)."""
    pol = {"packages": list(names), "files": [], "allow": set(), "codes": set(), "extras": [], "inert": [],
           "bench": [1.05, "prev"], "requires": set(), "stage": stage or "A0", "policy": False}
    stages = []
    for pid in names:
        pkg = PACKAGES[pid]
        pol["files"] += pkg["files"]
        pol["policy"] = pol["policy"] or pkg.get("policy", False)
        pol["allow"] |= set(pkg["allow"])
        pol["codes"] |= set(pkg["codes"])
        pol["extras"] += [e for e in pkg["extras"] if e not in pol["extras"]]
        pol["inert"] += pkg["inert"]
        pol["requires"] |= set(pkg["requires"])
        if pkg["bench"][0] < pol["bench"][0] or pkg["bench"][1] != "prev":
            pol["bench"] = list(pkg["bench"])
        stages.append(pkg["stage"])
    if stages and not stage:
        pol["stage"] = max(stages, key=stage_index)
    for feature, (fstage, introducer, _) in FEATURES.items():
        if stage_index(pol["stage"]) > stage_index(fstage) or introducer in names:
            pol["requires"].add(feature)
    return pol


# ----------------------------------------------------------------------------------------------------------------
# small helpers

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path, data, sort_keys=True):
    """sort_keys=False for specs: a spec's dict order can matter to a skill (pets.report reports a fields dict in
    order), so spec files and chunks keep the authored order (C1 handoff, request 4)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=sort_keys, default=str)
        f.write("\n")


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except OSError:
        pass
    return rows


def child_env(root, extra=None):
    """Environment for worker processes: no inherited runtime switches, utf-8 output, house_sim from root."""
    env = dict(os.environ)
    for key in RUNTIME_ENV:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if extra:
        env.update(extra)
    return env


def tail(text, n=3000):
    text = text or ""
    return text[-n:]


def short(value, n=400):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= n else text[:n] + "..."


def match_any(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def source_has(root, rel, needle):
    try:
        return needle in (Path(root) / rel).read_text(encoding="utf-8")
    except OSError:
        return False


# ----------------------------------------------------------------------------------------------------------------
# the gate's policy files: read as committed (agents never commit, so only the main session can sign)

def policy_root():
    """The project whose committed policy files apply: the repository this gate script belongs to (FIX2). Never a
    record's or a comparison's --root: an isolated copy carries a writable manifest and files, and any other folder
    could be a git repository with forged commits. When this script itself runs from a copy that is no git work tree,
    no policy file is trusted (fail closed)."""
    return DEFAULT_ROOT


def trusted_bytes(root, rel):
    """(bytes or None, note) of a policy file as the main session committed it in the git work tree `root`: the blob
    at HEAD (working-tree edits do not count until committed; the note says when there are some). An isolated copy is
    never a policy source (FIX2: its manifest is writable, so it could vouch for forged files); elsewhere: None. The
    gate reads policy files from policy_root()."""
    root = Path(root)
    if (root / ISOLATE_MANIFEST).exists():
        return None, "an isolated copy is never a policy source (the gate reads them from the project: policy_root)"
    try:
        probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, "git unavailable ({})".format(exc)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None, "not a git work tree: policy files cannot be trusted"
    show = subprocess.run(["git", "-C", str(root), "show", "HEAD:" + rel], capture_output=True)
    if show.returncode != 0:
        return None, "not committed"
    note = "committed at HEAD"
    try:
        local = (root / rel).read_bytes()
        if local.replace(b"\r\n", b"\n") != show.stdout.replace(b"\r\n", b"\n"):
            note += " (working-tree edits ignored until the main session commits them)"
    except OSError:
        note += " (deleted in the working tree; the committed version counts)"
    return show.stdout, note


def trusted_json(root, rel):
    """(data or None, note): trusted_bytes parsed as JSON."""
    raw, note = trusted_bytes(root, rel)
    if raw is None:
        return None, note
    try:
        return json.loads(raw.decode("utf-8")), note
    except ValueError as exc:
        return None, "unreadable JSON ({})".format(exc)


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def load_known_failing(root, today=None):
    """The dated allowlist (KNOWN_FAILING) as committed: {"entries": usable entries, "expired": [...], "invalid":
    [(entry, why)], "note"}. An entry is {"kind": "test", "test": "tests.test_x.Class[.test_name]", "modes": [...]
    (default all), ...} or {"kind": "spec", "catalog_id", "seeds": [..] or "all", "mode": "own" | "realistic" | "both"
    (default own), ...}, each with "owner", "expires" (YYYY-MM-DD; the entry excuses nothing after that day) and
    "reason". FIX2: kind "skip" ({"kind": "skip", "test": "tests.test_x.Class[.test_name]", "modes"?}) excuses a test that
    became skipped or an expected failure, or was removed (test_regressions)."""
    today = today or _today()
    data, note = trusted_json(root, KNOWN_FAILING)
    out = {"entries": [], "expired": [], "invalid": [], "note": note}
    if data is None:
        return out
    if not isinstance(data, dict) or data.get("format") != KNOWN_FAILING_FORMAT:
        out["invalid"].append((short(data, 120), "not a {} file".format(KNOWN_FAILING_FORMAT)))
        return out
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            out["invalid"].append((short(entry, 120), "not an object"))
            continue
        missing = [k for k in ("kind", "owner", "expires", "reason") if not entry.get(k)]
        what = {"test": "test", "spec": "catalog_id", "skip": "test"}.get(entry.get("kind"))
        if what is None:
            missing.append("kind test|spec|skip")
        elif not entry.get(what):
            missing.append(what)
        if not missing and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry["expires"])):
            missing.append("expires as YYYY-MM-DD")
        if missing:
            out["invalid"].append((short(entry, 160), "missing " + ", ".join(missing)))
        elif str(entry["expires"]) < today:
            out["expired"].append(entry)
        else:
            out["entries"].append(entry)
    return out


def test_allowed(allow, test_id, mode):
    """The allowlist entry excusing a failing test (a dotted id; a class or module entry covers its tests), or None."""
    for entry in (allow or {}).get("entries", []):
        if entry.get("kind") != "test":
            continue
        name = str(entry["test"])
        if test_id != name and not test_id.startswith(name + "."):
            continue
        if mode in (entry.get("modes") or TEST_MODES):
            return entry
    return None


def skip_allowed(allow, test_id, mode):
    """The allowlist entry of kind "skip" excusing a removed, skipped or expected-failure test (a dotted id; a class or
    module entry covers its tests), or None (FIX2)."""
    for entry in (allow or {}).get("entries", []):
        if entry.get("kind") != "skip":
            continue
        name = str(entry["test"])
        if test_id != name and not test_id.startswith(name + "."):
            continue
        if mode in (entry.get("modes") or TEST_MODES):
            return entry
    return None


def spec_allowed(allow, catalog_id, seed, mode):
    """The allowlist entry excusing a failing spec trial (mode "own" or "realistic"), or None."""
    for entry in (allow or {}).get("entries", []):
        if entry.get("kind") != "spec" or entry.get("catalog_id") != catalog_id:
            continue
        seeds = entry.get("seeds", "all")
        if seeds not in ("all", "*") and not (isinstance(seeds, list) and seed in seeds) and seeds != seed:
            continue
        if entry.get("mode", "own") in ("both", mode):
            return entry
    return None


def load_signoff(root, gate_id):
    """The second signatures of one gate record (SIGNOFF_DIR/<gate id>.json) as committed: {"verdict": {(key, seed,
    mode): digest}, "edit": {key: spec_hash}, "coverage": set(actions), "tests": set((test id, mode or None)), "note",
    "by"}."""
    rel = "{}/{}.json".format(SIGNOFF_DIR, gate_dirname(gate_id))
    data, note = trusted_json(root, rel)
    out = {"verdict": {}, "edit": {}, "coverage": set(), "tests": set(), "note": "{}: {}".format(rel, note), "by": None}
    if not isinstance(data, dict) or data.get("format") != SIGNOFF_FORMAT:
        if data is not None:
            out["note"] = "{}: not a {} file (ignored)".format(rel, SIGNOFF_FORMAT)
        return out
    out["by"] = data.get("signed_by")
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind == "verdict" and row.get("key") is not None:
            out["verdict"][(row["key"], row.get("seed"), row.get("mode"))] = row.get("digest")
        elif kind == "edit" and row.get("key") is not None:
            out["edit"][row["key"]] = row.get("spec_hash")
        elif kind == "coverage" and row.get("action"):
            out["coverage"].add(row["action"])
        elif kind == "test-removal" and row.get("test"):
            out["tests"].add((str(row["test"]), row.get("mode")))     # FIX2: a test removed or skipped on purpose
    return out


# ----------------------------------------------------------------------------------------------------------------
# a small weighted job scheduler (subprocesses in threads; longest expected job first)

class Job:
    def __init__(self, name, check, cmd, env, weight=1, est=30.0, timeout=3600, meta=None):
        self.name, self.check, self.cmd, self.env = name, check, cmd, env
        self.weight, self.est, self.timeout, self.meta = weight, est, timeout, meta or {}
        self.result = None


def run_job(job, cwd):
    start = time.perf_counter()
    try:
        proc = subprocess.run(job.cmd, cwd=str(cwd), env=job.env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=job.timeout)
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out, err = -9, exc.stdout or "", (exc.stderr or "") + "\nTIMEOUT after {} s".format(job.timeout)
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
    except OSError as exc:
        rc, out, err = -1, "", "could not start: {}".format(exc)
    return {"rc": rc, "stdout": out, "stderr": err, "secs": round(time.perf_counter() - start, 1)}


def schedule(jobs, slots, cwd, progress=None):
    """Run jobs with at most `slots` weight units at once. Returns when all are done (job.result filled)."""
    pending = sorted(jobs, key=lambda j: -j.est)
    cond = threading.Condition()
    state = {"free": slots, "running": 0, "done": 0}

    def worker(job):
        job.result = run_job(job, cwd)
        with cond:
            state["free"] += min(job.weight, slots)
            state["running"] -= 1
            state["done"] += 1
            if progress:
                progress(job, state["done"], len(jobs))
            cond.notify_all()

    with cond:
        while pending or state["running"]:
            started = False
            for job in list(pending):
                need = min(job.weight, slots)
                if need <= state["free"]:
                    pending.remove(job)
                    state["free"] -= need
                    state["running"] += 1
                    threading.Thread(target=worker, args=(job,), daemon=True).start()
                    started = True
            if not started:
                cond.wait(timeout=5.0)


# ----------------------------------------------------------------------------------------------------------------
# specs (G2, G3) and generation-1 digests (G5): the data the workers need

def collect_specs(root):
    """[(key, file, index, spec)] for every tasks/authored/*.json spec, in file order."""
    out, seen = [], Counter()
    for path in sorted(glob.glob(str(Path(root) / "tasks" / "authored" / "*.json"))):
        data = read_json(path, {})
        specs = data.get("specs", []) if isinstance(data, dict) else data
        for i, spec in enumerate(specs):
            key = spec.get("catalog_id") or "{}#{}".format(Path(path).name, i)
            seen[key] += 1
            if seen[key] > 1:
                key = "{}#{}".format(key, seen[key])
            out.append((key, Path(path).name, i, spec))
    return out


def own_mode(spec):
    return "realistic" if spec.get("perception") == "realistic" else "oracle"


GEN1_FILE = "tests/data/gen1_digests.json"
GEN1_DEFAULT_SEEDS = tuple(range(20))


def gen1_entries_from_specs(root, seeds=SEEDS):
    """TIER1_SWEEP 14.1: seeds 0-19 with default options, and every distinct generation-1 house block of the authored
    specs on the verification seeds (a block with "gen" 2 or more is a generation-2 house, which this file does not
    pin: the adoption round moved most specs there). Returns [{"house", "seed", "specs"}] (defaults first, then sorted
    by house json and seed)."""
    sys.path.insert(0, str(root))
    from house_sim.tasks.spec import choose_variant, expand
    groups = defaultdict(list)
    for key, _, _, spec in collect_specs(root):
        try:
            full = expand(spec)
        except Exception:                                   # noqa: BLE001  (a broken spec is G2's business)
            continue
        for seed in seeds:
            chosen, _ = choose_variant(full, seed)
            house = chosen.get("house", {}) or {}
            try:
                if int(house.get("gen") or 1) >= 2:
                    continue
            except (TypeError, ValueError):
                pass
            groups[(json.dumps(house, sort_keys=True), seed)].append(key.split("#")[0])
    entries = [{"house": {}, "seed": s, "specs": []} for s in GEN1_DEFAULT_SEEDS]
    for (house, seed), keys in sorted(groups.items()):
        entries.append({"house": json.loads(house), "seed": seed, "specs": sorted(set(keys))})
    return entries


def gen1_entries(root):
    """The entries pinned by tests/data/gen1_digests.json when it exists, else the recording procedure's entries."""
    data = read_json(Path(root) / GEN1_FILE)
    if data:
        return [{"house": e.get("house", {}), "seed": e["seed"], "specs": e.get("specs", []), "digest": e["digest"]}
                for e in data.get("defaults", []) + data.get("spec_houses", [])], True
    return gen1_entries_from_specs(root), False


def gen1_key(entry):
    return "{}|{}".format(json.dumps(entry.get("house", {}), sort_keys=True), entry["seed"])


# ----------------------------------------------------------------------------------------------------------------
# worker entry points (run as `python tier1_gate.py _<name> ...` in a child process with house_sim on the path)

TRIAL_KEYS = ("seed", "perception", "variant", "passed", "initial_fails", "idle_fails", "reference_passes",
              "reference_steps", "reference_time_s", "reference_state_hash", "reference_obs_hash", "idle_state_hash",
              "initial_hash", "task_hash", "perceivable", "wrong_fails", "wrong_ran")


def _quiet_imports(root):
    import warnings
    warnings.simplefilter("ignore")
    sys.path.insert(0, str(root))
    os.chdir(str(root))


def _safe_digest(digest, fn):
    try:
        return digest(fn())
    except Exception as exc:                                  # noqa: BLE001
        return "error:{}".format(type(exc).__name__)


WRONG_KEYS = ("fails", "ran", "refusals", "reasons", "goal_fraction", "error_class", "steps_run", "steps_total")


def wrong_item(record, env=None, digest=None):
    """The compact trial-record form of one wrong-plan record of verify.run_trial: its verdict fields (fails, ran
    fully, the sorted reason classes, the goal fraction), the error class and message when it raised, its first
    failure labels, and the digest of its final state when the environment was captured."""
    item = {k: record[k] for k in WRONG_KEYS if k in record}
    item["name"] = short(str(record.get("name", "")), 60)
    if record.get("error"):
        item["error"] = short(record["error"], 200)
    if record.get("labels"):
        item["labels"] = [short(str(label), 80) for label in record["labels"][:12]]
    if record.get("harness_error"):
        item["harness_error"] = True
    if env is not None and digest is not None:
        item["state_hash"] = _safe_digest(digest, lambda: env.world.s)
    return item


def worker_trials(chunk_path, out_path):
    """Run verify.run_trial for every (spec, seed) of a chunk; append one compact JSON line per trial. The reference,
    idle and wrong-plan environments are captured (run_trial(capture=...); with an older verify.py by wrapping
    verify.run_plan / verify.idle) to add their digests."""
    chunk = read_json(chunk_path)
    _quiet_imports(chunk["root"])
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    import inspect
    from house_sim.kernel.plugins import load_domains
    from house_sim.kernel.predicates import digest
    from house_sim.tasks import verify
    load_domains()
    params = inspect.signature(verify.run_trial).parameters
    takes_camera, takes_capture = "camera" in params, "capture" in params
    captured = {"plans": [], "idle": None}
    if not takes_capture:
        run_plan, idle = verify.run_plan, verify.idle

        def run_plan_spy(env, plan, bindings, strict=True):
            captured["plans"].append((strict, env))
            return run_plan(env, plan, bindings, strict=strict)

        def idle_spy(env, task):
            result = idle(env, task)
            captured["idle"] = env
            return result

        verify.run_plan, verify.idle = run_plan_spy, idle_spy
    with open(out_path, "w", encoding="utf-8", newline="\n") as out:
        for job in chunk["jobs"]:
            for seed in chunk["seeds"]:
                captured["plans"], captured["idle"] = [], None
                capture = {}
                kwargs = {"capture": capture} if takes_capture else {}
                start = time.perf_counter()
                if chunk.get("camera") and not takes_camera:
                    raw = {"seed": seed, "passed": False,
                           "error": "camera={} requested but verify.run_trial has no camera parameter".format(chunk["camera"])}
                elif chunk.get("camera"):
                    raw = verify.run_trial(job["spec"], seed, chunk["perception"], camera=chunk["camera"], **kwargs)
                else:
                    raw = verify.run_trial(job["spec"], seed, chunk["perception"], **kwargs)
                trial = {k: raw[k] for k in TRIAL_KEYS if k in raw}
                if takes_capture:
                    ref_env, idle_env, wrongs = capture.get("reference"), capture.get("idle"), capture.get("wrong") or []
                else:
                    refs = [env for strict, env in captured["plans"] if strict]
                    ref_env, idle_env = (refs[-1] if refs else None), captured["idle"]
                    wrongs = [env for strict, env in captured["plans"] if not strict]
                if ref_env is not None:
                    trial["reference_obs_hash"] = _safe_digest(digest, ref_env.observe)
                if idle_env is not None:
                    trial["idle_state_hash"] = _safe_digest(digest, lambda: idle_env.world.s)
                for key in ("error", "reference_error"):
                    if raw.get(key):
                        trial[key] = short(raw[key], 400)
                if raw.get("reference_failures"):
                    trial["reference_failures"] = [short(f, 200) for f in raw["reference_failures"][:5]]
                if raw.get("perceivability_gaps"):
                    trial["perceivability_gaps"] = short(raw["perceivability_gaps"], 300)
                records = raw.get("wrong", [])
                envs = wrongs if len(wrongs) == len(records) else [None] * len(records)
                trial["wrong"] = [wrong_item(record, env, digest) for record, env in zip(records, envs)]
                trial.update(key=job["key"], catalog_id=job["spec"].get("catalog_id"), file=job["file"],
                             spec_hash=job["spec_hash"], mode=chunk["mode"], own_mode=job["own_mode"],
                             secs=round(time.perf_counter() - start, 2))
                out.write(json.dumps(trial, sort_keys=True, default=str) + "\n")
                out.flush()


def worker_unittest(out_path, target):
    """Run one test target exactly as `python -m unittest <target>` does (the same report on stderr, exit status 0 /
    1, and 5 when no test ran) and write every test's outcome to out_path: {"tests": {test id: "ok" | "skip" | "xfail"
    | "xpass" | "fail" | "error"}, "ran"} (FIX2: the gate only read exit codes, so a failing test skipped with
    @unittest.skip, or a deleted test file, passed). Returns the exit status."""
    import unittest
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != HERE]
    sys.path.insert(0, os.getcwd())          # like `python -m unittest`: the project (cwd) first, not this folder
    outcomes = {}

    def note(test, outcome):
        tid = test.id() if hasattr(test, "id") else str(test)
        if outcomes.get(tid) not in ("fail", "error"):
            outcomes[tid] = outcome

    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            note(test, "ok")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            note(test, "fail")

        def addError(self, test, err):
            super().addError(test, err)
            note(test, "error")

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            note(test, "skip")

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            note(test, "xfail")

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            note(test, "xpass")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                note(test, "fail" if issubclass(err[0], test.failureException) else "error")

    class Runner(unittest.TextTestRunner):
        resultclass = Result

    program = unittest.main(module=None, argv=["python -m unittest", target], testRunner=Runner, exit=False)
    result = program.result
    write_json(out_path, {"tests": dict(sorted(outcomes.items())), "ran": result.testsRun})
    if result.testsRun == 0 and not result.skipped:
        return 5
    return 0 if result.wasSuccessful() else 1


def worker_gen1(root, out_path, part, parts):
    """Digest generate_house(seed, **house) for entries[part::parts]."""
    _quiet_imports(root)
    from house_sim.generator import generate_house
    from house_sim.kernel.plugins import load_domains
    from house_sim.kernel.predicates import digest
    load_domains()
    entries, _ = gen1_entries(root)
    result = {}
    for entry in entries[part::parts]:
        try:
            result[gen1_key(entry)] = digest(generate_house(entry["seed"], **entry.get("house", {})))
        except Exception as exc:                              # noqa: BLE001
            result[gen1_key(entry)] = "error:{}: {}".format(type(exc).__name__, short(str(exc), 200))
    write_json(out_path, result)


def worker_inert(root, out_path, modules):
    """Import every module after all domains, the engine and the task runner are loaded; report what each registered."""
    _quiet_imports(root)
    import importlib
    from house_sim.kernel import plugins
    plugins.load_domains()
    import house_sim.kernel.engine  # noqa: F401
    import house_sim.tasks.runner  # noqa: F401

    def sizes():
        out = {}
        for name in ("ACTIONS", "SYSTEMS", "SYSTEM_PERIODS", "FURNISHERS", "SCENARIO_HELPERS", "SKILLS", "OBSERVERS",
                     "INSPECTORS", "INVARIANTS", "ANSWERERS", "VALIDATORS", "OWNERS"):
            out[name] = len(getattr(plugins, name))
        for name in ("BEFORE", "AFTER", "HOOKS"):
            table = getattr(plugins, name)
            out[name] = sum(len(v) for v in table.values())
        return out

    report = {}
    for module in modules:
        before = sizes()
        try:
            importlib.import_module(module)
            after = sizes()
            report[module] = {"ok": True, "registered": {k: after[k] - before[k] for k in after if after[k] != before[k]}}
        except Exception as exc:                              # noqa: BLE001
            report[module] = {"ok": False, "error": "{}: {}".format(type(exc).__name__, short(str(exc), 300))}
    write_json(out_path, report)


def worker_info(root, out_path):
    """G0: fingerprints and registry sizes."""
    _quiet_imports(root)
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import actions_fingerprint
    info = {"actions_fingerprint": actions_fingerprint(), "actions": len(plugins.ACTIONS),
            "systems": len(plugins.SYSTEMS)}
    try:
        from house_sim import audit
        info["code_fingerprint"] = audit.fingerprint()
        info["harness_fingerprint"] = audit.harness_fingerprint()
    except Exception as exc:                                  # noqa: BLE001
        info["fingerprint_error"] = "{}: {}".format(type(exc).__name__, exc)
    write_json(out_path, info)


def worker_bench_wait(root, out_path, seeds, repeats):
    """The engine cost of a 1 h wait on fresh houses, best of `repeats` per seed (G7; room mode, oracle)."""
    _quiet_imports(root)
    from house_sim.generator import generate_house
    from house_sim.kernel.engine import HouseEnv
    from house_sim.kernel.plugins import load_domains
    load_domains()
    per_seed = {}
    for seed in seeds:
        times = []
        for _ in range(repeats):
            env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False)
            start = time.perf_counter()
            env.step("wait", seconds=3600)
            times.append(time.perf_counter() - start)
        per_seed[str(seed)] = round(min(times), 4)
    write_json(out_path, {"per_seed_s": per_seed, "wait_1h_s": round(sum(per_seed.values()), 4)})


def worker_gen_timing(root, out_path, repeats):
    """GEN extra: generate_house(gen=2) vs generation 1 on seeds 0-9 (best of repeats, cache bypassed)."""
    _quiet_imports(root)
    import inspect
    from house_sim import generator
    from house_sim.kernel.plugins import load_domains
    load_domains()
    takes_gen = "gen" in inspect.signature(generator._generate).parameters or source_has(
        root, "house_sim/generator/__init__.py", "gen >= 2")
    if not takes_gen:
        write_json(out_path, {"skipped": "generate_house has no gen option yet"})
        return
    result = {}
    for gen in (1, 2):
        best = None
        for _ in range(repeats):
            start = time.perf_counter()
            for seed in range(10):
                generator._generate(seed, gen=gen)
            spent = time.perf_counter() - start
            best = spent if best is None else min(best, spent)
        result["gen{}_s".format(gen)] = round(best, 3)
    result["ratio"] = round(result["gen2_s"] / max(1e-9, result["gen1_s"]), 3)
    write_json(out_path, result)


# ----------------------------------------------------------------------------------------------------------------
# parsing tool outputs

RAN = re.compile(r"^Ran (\d+) tests?", re.M)
VERDICT_LINE = re.compile(r"^(?:OK|FAILED)\b.*$", re.M)
SKIPPED = re.compile(r"skipped=(\d+)")
FAILED_TEST = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", re.M)


def test_row(job):
    res = job.result
    text = (res["stderr"] or "") + (res["stdout"] or "")
    m = RAN.search(res["stderr"] or "") or RAN.search(text)
    verdicts = VERDICT_LINE.findall(res["stderr"] or "")
    s = SKIPPED.search(verdicts[-1]) if verdicts else None
    row = {"file": job.meta["file"], "mode": job.meta["mode"], "rc": res["rc"], "ran": int(m.group(1)) if m else 0,
           "skipped": int(s.group(1)) if s else 0, "secs": res["secs"]}
    # per-test outcomes written by the _unittest worker (FIX2): every test id, and the ids of each outcome that is not
    # "ok"; absent when the process died before writing them (then only the counts are compared)
    data = read_json(job.meta["outcomes"]) if job.meta.get("outcomes") else None
    if isinstance(data, dict) and isinstance(data.get("tests"), dict):
        row["ids"] = sorted(data["tests"])
        quiet = defaultdict(list)
        for tid, outcome in sorted(data["tests"].items()):
            if outcome != "ok":
                quiet[str(outcome)].append(tid)
        row["outcomes"] = dict(sorted(quiet.items()))
    if res["rc"]:
        row["tail"] = tail(text, 3000)
        failed = set()
        for name, where in FAILED_TEST.findall(res["stderr"] or ""):
            failed.add(where if where.endswith("." + name) else "{}.{}".format(where, name))
        row["failed_tests"] = sorted(failed) or ["{} (rc {}, no test failure parsed)".format(job.meta["file"], res["rc"])]
    return row


def _module_of(file):
    """The dotted module of a tests/ file name ("test_mind.py" -> "tests.test_mind")."""
    return "tests." + file[:-3] if str(file).endswith(".py") else str(file)


def _named(test, names):
    return any(test == n or test.startswith(n + ".") for n in names)


def test_regressions(prev, cur, allow=None, signoff=None, checks=("G1", "G1b", "G8")):
    """Tests that went quiet between two tests.json records (FIX2: a failing test skipped with @unittest.skip, or a
    test file deleted, passed both record and compare): ([(check, file, mode, test, what)] not excused, [...] excused).
    With per-test ids on both sides: every id of the previous record that is gone, or that became skipped or an
    expected failure; otherwise the counts: a file that is gone, ran fewer tests or skipped more. Excused by an
    allowlist entry of kind "skip" or a committed signoff row {"kind": "test-removal", "test", "mode"?} naming the
    test, its class or its module. A check missing from `cur` is not compared here (missing data is reported)."""
    signed = (signoff or {}).get("tests") or set()
    bad, excused = [], []
    for check in checks:
        a_rows, b_rows = (prev or {}).get(check) or [], (cur or {}).get(check)
        if not a_rows or not b_rows:
            continue
        a = {(r["file"], r["mode"]): r for r in a_rows}
        b = {(r["file"], r["mode"]): r for r in b_rows}
        for key in sorted(a):
            file, mode = key
            old, new = a[key], b.get(key)
            found = []
            if new is None:
                found.append((_module_of(file), "test file gone ({} test(s) before)".format(old.get("ran"))))
            elif old.get("ids") is not None and new.get("ids") is not None:
                before, now = set(old["ids"]), set(new["ids"])
                quiet_old = {t for k in ("skip", "xfail") for t in (old.get("outcomes") or {}).get(k, [])}
                quiet_new = {t for k in ("skip", "xfail") for t in (new.get("outcomes") or {}).get(k, [])}
                found += [(t, "removed") for t in sorted(before - now)]
                found += [(t, "now skipped or an expected failure") for t in sorted(quiet_new - quiet_old)
                          if t in before]
            else:
                if int(new.get("ran") or 0) < int(old.get("ran") or 0):
                    found.append((_module_of(file), "ran {} -> {} test(s)".format(old.get("ran"), new.get("ran"))))
                if int(new.get("skipped") or 0) > int(old.get("skipped") or 0):
                    found.append((_module_of(file), "skipped {} -> {}".format(old.get("skipped"), new.get("skipped"))))
            for test, what in found:
                ok = skip_allowed(allow, test, mode) is not None or \
                    _named(test, [t for t, m in signed if m in (None, mode)])
                (excused if ok else bad).append((check, file, mode, test, what))
    return bad, excused


def describe_regressions(items, n=600):
    return short(["{} ({} {}): {}".format(test, check, mode, what) for check, _, mode, test, what in items], n)


def new_failures(rows, prev_rows, mode):
    """Failing tests of `rows` (one mode) that were not failing in `prev_rows`; None when there is no previous record."""
    if prev_rows is None:
        return None
    known = defaultdict(set)
    for r in prev_rows:
        if r["mode"] == mode:
            known[r["file"]] |= set(r.get("failed_tests") or ([] if not r["rc"] else ["?"]))
    return [(r["file"], f) for r in rows if r["mode"] == mode and r["rc"]
            for f in (r.get("failed_tests") or ["?"]) if f not in known[r["file"]]]


def parse_bench(text):
    out = {"systems": {}}
    m = re.search(r"total ms/substep: ([0-9.]+)", text)
    if m:
        out["substep_ms"] = float(m.group(1))
    m = re.search(r"engine: 1 h wait = ([0-9.]+) s", text)
    if m:
        out["bench_wait_1h_s"] = float(m.group(1))
    for line in text.splitlines():
        m = re.match(r"\s*([0-9.]+) ms\s+(\S+)", line)
        if m:
            out["systems"][m.group(2)] = float(m.group(1))
    m = re.search(r"day[^=\n]*= ([0-9.]+) s", text)
    if m:
        out["day_s"] = float(m.group(1))
    return out


def summarize_leaks(data):
    data = data or {}
    leaks = data.get("leaks", [])
    by = Counter("{} {}".format(l.get("kind"), l.get("owner")) for l in leaks)
    return {"leaks": len(leaks), "errors": len(data.get("errors", [])), "by_kind_owner": dict(sorted(by.items())),
            "examples": [short(l, 300) for l in leaks[:10]] + [short(e, 300) for e in data.get("errors", [])[:5]]}


def summarize_fuzz(data, text):
    """A fuzz run's summary: defects, nondeterministic replays, examples, the command counts and (tools/fuzz.py from
    phase 1 on) the per-action success coverage: registered and succeeding counts and the sorted action names."""
    data = data or {}
    m = re.search(r"houses (\d+)\s+commands (\d+)\s+succeeded (\d+)", text or "")
    out = {"defects": len(data.get("defects", [])), "nondeterministic": len(data.get("nondeterministic", [])),
           "examples": [short(d, 400) for d in data.get("defects", [])[:8]] + [short(n, 300) for n in data.get("nondeterministic", [])[:4]]}
    if m:
        out.update(houses=int(m.group(1)), commands=int(m.group(2)), succeeded=int(m.group(3)))
    actions = data.get("actions")
    if isinstance(actions, dict) and actions:
        ok = sorted(a for a, v in actions.items() if isinstance(v, (list, tuple)) and v and v[0] > 0)
        out.update(actions_registered=len(actions), actions_succeeded=len(ok), succeeded_actions=ok,
                   never_actions=sorted(set(actions) - set(ok)))
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    if coverage.get("noop_only") is not None:
        # actions whose only "successes" changed nothing (tools/fuzz.py from FIX2 on): not counted as succeeding
        out["noop_only"] = list(coverage.get("noop_only") or [])
    return out


def action_coverage(variants):
    """Per-action success coverage over every fuzz variant of a gate: {"registered", "succeeded": sorted names,
    "never": sorted names}; None when no variant reports actions."""
    names, ok = set(), set()
    for summary in (variants or {}).values():
        if isinstance(summary, dict) and summary.get("succeeded_actions") is not None:
            ok |= set(summary["succeeded_actions"])
            names |= set(summary["succeeded_actions"]) | set(summary.get("never_actions") or [])
    if not names:
        return None
    return {"registered": len(names), "succeeded": sorted(ok), "never": sorted(names - ok)}


def summarize_layout(rows):
    counts = Counter("{} | {}".format(r.get("problem", "").split(" ", 1)[0], r.get("owner")) for r in rows or [])
    return {"problems": len(rows or []), "by_kind_owner": dict(sorted(counts.items())),
            "examples": [short(r, 300) for r in (rows or [])[:10]]}


# ----------------------------------------------------------------------------------------------------------------
# reviewed differences (TIER1_INTEGRATION 3.3)

def parse_reviewed_markdown(text):
    """Rows of markdown tables that name a catalog id and a difference code."""
    rows = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        ids = CATALOG_ID.findall(line)
        codes = CODE.findall(line)
        if not ids or not codes:
            continue
        seeds, mode = "all", "both"
        for cell in cells:
            low = cell.lower().strip("` ")
            if re.fullmatch(r"\d+(\s*,\s*\d+)*", low):
                seeds = [int(x) for x in re.findall(r"\d+", low)]
            elif low in ("own", "oracle"):
                mode = "own"
            elif low in ("forced", "realistic", "forced realistic"):
                mode = "realistic"
            elif low in ("both", "all modes"):
                mode = "both"
        for cid in ids:
            rows.append({"catalog_id": cid, "seeds": seeds, "mode": mode, "codes": codes, "source": "handoff"})
    return rows


def load_reviewed(root, gate_dir, packages, extra_file=None):
    """Reviewed rows (the first signature): the rows of each package's handoff table (bound to that package), of
    gate/<ID>/reviewed.json and of --reviewed FILE (bound to the row's "package" when it names one of `packages`,
    else to all of them). Every reviewed difference also needs the main session's second signature (load_signoff)."""
    rows = []
    for pid in packages:
        for name in [pid] + PACKAGES[pid]["aka"]:
            path = Path(root) / HANDOFF_DIR / "handoff_{}.md".format(name.replace(":", "_"))
            if path.exists():
                rows += [dict(row, packages=[pid]) for row in parse_reviewed_markdown(path.read_text(encoding="utf-8"))]
    for path in [Path(gate_dir) / "reviewed.json"] + ([Path(extra_file)] if extra_file else []):
        data = read_json(path)
        for item in data or []:
            if not isinstance(item, dict):
                continue
            code = item.get("code") or item.get("codes") or []
            named = resolve_package(item.get("package")) if item.get("package") else None
            rows.append({"catalog_id": item.get("catalog_id"), "seeds": item.get("seeds", item.get("seed", "all")),
                         "mode": item.get("mode", "both"), "codes": code if isinstance(code, list) else [code],
                         "source": str(path.name),
                         "packages": [named] if named in packages else list(packages)})
    return rows


def spec_globs(pid):
    """The tasks/authored globs a package owns ([] for a package that owns no spec file: a kernel package)."""
    return [g for g in PACKAGES[pid]["files"] if g.startswith("tasks/authored/")]


def package_covers_spec(pid, spec_file):
    """Review codes are bound to the files a package owns (REVIEW_REPORT H12): a package that owns spec files reviews
    only those (ADOPT:cleaning: tasks/authored/cleaning__*.json); a kernel package, whose changes reach every domain's
    specs, may review any spec, behind the main session's second signature like everyone else."""
    globs = spec_globs(pid)
    return not globs or (bool(spec_file) and match_any("tasks/authored/" + str(spec_file), globs))


def reviewed_covers(rows, cid, seed, mode, codes, spec_file=None):
    """The first reviewed row covering one trial's difference: same catalog id, seed and mode, one of the policy's
    codes, and a package that owns the spec's file (spec_file None: no binding, for callers without the file)."""
    for row in rows:
        if row["catalog_id"] != cid:
            continue
        seeds = row["seeds"]
        if seeds not in ("all", "*") and not (isinstance(seeds, list) and seed in seeds) and seeds != seed:
            continue
        if row["mode"] not in ("both", mode):
            continue
        if not any(c in codes for c in row["codes"]):
            continue
        if spec_file is not None and row.get("packages") and \
                not any(pid in PACKAGES and package_covers_spec(pid, spec_file) for pid in row["packages"]):
            continue
        return row
    return None


# ----------------------------------------------------------------------------------------------------------------
# comparison (shared by `record` and `compare`)

VERDICT_KEYS = ("passed", "initial_fails", "idle_fails", "reference_passes", "perceivable", "wrong_fails", "wrong_ran")
STATE_KEYS = ("reference_state_hash", "idle_state_hash", "initial_hash", "task_hash", "reference_steps",
              "reference_time_s")


def has_reasons(t):
    """True when every wrong plan of a trial record carries its reason classes (records from phase 1 on)."""
    return all("reasons" in w for w in t.get("wrong", []))


def trial_verdict(t, reasons=True):
    """The verdict fields of a trial record; with reasons, each wrong plan's sorted reason classes too (a wrong plan
    that used to fail on its goal and now fails only on a harm flag is a verdict change)."""
    v = {k: t.get(k) for k in VERDICT_KEYS}
    v["error"] = bool(t.get("error"))
    v["wrong"] = [(w.get("name"), w.get("fails"), w.get("ran")) + ((tuple(w.get("reasons") or ()),) if reasons else ())
                  for w in t.get("wrong", [])]
    return v


def verdict_digest(t):
    """The digest a second signature names for a trial's new verdict (16 hex digits of sha256)."""
    import hashlib
    text = json.dumps(trial_verdict(t, reasons=has_reasons(t)), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def trial_state(t):
    v = {k: t.get(k) for k in STATE_KEYS}
    v["wrong"] = [w.get("state_hash") for w in t.get("wrong", [])]
    return v


def index_trials(rows):
    return {(r["key"], r["seed"]): r for r in rows}


def failing_specs(rows, mode, allow=None):
    """G2-ABS: ({key: [seeds]} of the trials of `rows` that did not pass and are not excused by the allowlist, every
    key counted, new ones included; {key: [seeds]} of the excused ones)."""
    failing, excused = defaultdict(list), defaultdict(list)
    for r in rows:
        if r.get("passed") is True:
            continue
        if spec_allowed(allow, r.get("catalog_id") or str(r["key"]).split("#")[0], r["seed"], mode):
            excused[r["key"]].append(r["seed"])
        else:
            failing[r["key"]].append(r["seed"])
    return dict(failing), dict(excused)


def describe_failing(failing, n=12):
    return ", ".join("{} (seeds {})".format(k, ",".join(map(str, v))) for k, v in sorted(failing.items())[:n]) + \
        (", ..." if len(failing) > n else "")


def compare_specs(prev_rows, cur_rows, mode, policy, reviewed, signoff=None):
    """Verdict and digest changes of one mode. Returns a dict with lists and a status. A verdict change passes with a
    reviewed row (bound to the files its package owns) AND the main session's signature of its exact new verdict; an
    edited spec (spec_hash changed) needs a signature of its new spec hash. signoff None checks no signature (only a
    caller that judges nothing may omit it)."""
    prev, cur = index_trials(prev_rows), index_trials(cur_rows)
    sign = signoff if signoff is not None else {"verdict": {}, "edit": {}}
    out = {"mode": mode, "verdict_changes": [], "state_changes": [], "obs_changes": [], "new": [], "removed": [],
           "edited_specs": set(), "unsigned_edits": []}
    edited = {}
    for k in sorted(set(prev) | set(cur), key=lambda x: (x[0], x[1])):
        a, b = prev.get(k), cur.get(k)
        if a is None:
            out["new"].append("{} seed {}".format(*k))
            continue
        if b is None:
            out["removed"].append("{} seed {}".format(*k))
            continue
        if a.get("spec_hash") != b.get("spec_hash"):
            out["edited_specs"].add(k[0])
            edited[k[0]] = b.get("spec_hash")
        reasons = has_reasons(a) and has_reasons(b)
        va, vb = trial_verdict(a, reasons), trial_verdict(b, reasons)
        if va != vb:
            what = [x for x in va if va[x] != vb[x]]
            row = reviewed_covers(reviewed, b.get("catalog_id") or k[0], k[1], mode, policy["codes"],
                                  spec_file=b.get("file") or "") if "verdict" in policy["allow"] else None
            digest_now = verdict_digest(b)
            signed = signoff is not None and sign["verdict"].get((k[0], k[1], mode)) == digest_now
            out["verdict_changes"].append({"key": k[0], "seed": k[1], "passed": [a.get("passed"), b.get("passed")],
                                           "fields": what, "reviewed": bool(row), "signed": signed,
                                           "digest": digest_now, "codes": row["codes"] if row else [],
                                           "detail": short({x: [va[x], vb[x]] for x in what}, 300)})
        sa, sb = trial_state(a), trial_state(b)
        if sa != sb:
            out["state_changes"].append({"key": k[0], "seed": k[1], "fields": [x for x in sa if sa[x] != sb[x]]})
        if a.get("reference_obs_hash") != b.get("reference_obs_hash"):
            out["obs_changes"].append({"key": k[0], "seed": k[1]})
    out["edited_specs"] = sorted(out["edited_specs"])
    out["edits"] = edited
    if signoff is not None:
        out["unsigned_edits"] = sorted(key for key, spec_hash in edited.items() if sign["edit"].get(key) != spec_hash)
    problems = []
    unreviewed = [c for c in out["verdict_changes"] if not c["reviewed"]]
    unsigned = [c for c in out["verdict_changes"] if c["reviewed"] and not c["signed"] and signoff is not None]
    if unreviewed:
        problems.append("{} unreviewed verdict change(s)".format(len(unreviewed)))
    if unsigned:
        problems.append("{} reviewed verdict change(s) without the main session's second signature".format(
            len(unsigned)))
    if out["unsigned_edits"]:
        problems.append("{} edited spec(s) without the main session's second signature".format(
            len(out["unsigned_edits"])))
    if out["state_changes"] and "state" not in policy["allow"]:
        problems.append("{} trial(s) with changed state digests".format(len(out["state_changes"])))
    if out["obs_changes"] and "obs" not in policy["allow"]:
        problems.append("{} trial(s) with a changed observation digest".format(len(out["obs_changes"])))
    if out["removed"]:
        problems.append("{} trial(s) missing".format(len(out["removed"])))
    out["problems"] = problems
    return out


def bench_reference(policy, prev_dir, gates_root):
    factor, ref = policy["bench"]
    if ref == "prev":
        return factor, prev_dir, "prev"
    path = Path(gates_root) / ref
    return factor, (path if (path / "bench.json").exists() else prev_dir), ref


def restrict_rows(rows, pattern):
    """Trial rows of the spec files matching `pattern` (a --spec-files glob; None keeps every row)."""
    if not pattern:
        return list(rows)
    return [r for r in rows if fnmatch.fnmatchcase(str(r.get("file") or ""), pattern)]


def spec_scope(prev_meta, cur_meta, extra=None):
    """The spec-file globs a comparison is restricted to: a record made with --spec-files only ran those files, so the
    other side is compared on the same files (never reported as missing trials); `extra` (compare --spec-files)
    narrows both. Returns a list of globs (each side's rows must match all of them)."""
    return [g for g in ((prev_meta or {}).get("spec_files"), (cur_meta or {}).get("spec_files"), extra) if g]


def scoped_rows(rows, globs):
    for pattern in globs:
        rows = restrict_rows(rows, pattern)
    return rows


def unexplained_test_failures(rows, allow, core_required=True):
    """(unexplained, excused) failing tests of tests.json rows (G1, G1b, G8): [(file, mode, test id)] not excused by
    the allowlist (a failing run whose failures could not be parsed is never excused), and the excused ones.
    core_required False: HOUSE_SIM_DOMAINS=core failures are recorded only (stage A packages, before B1)."""
    unexplained, excused = [], []
    for r in rows or []:
        if not r.get("rc"):
            continue
        if r.get("mode") == "core" and not core_required:
            continue
        failed = r.get("failed_tests") or []
        parsed = [f for f in failed if " (rc " not in f]
        if not parsed:
            unexplained.append((r["file"], r["mode"], (failed or ["rc {}".format(r["rc"])])[0]))
            continue
        for test_id in parsed:
            entry = test_allowed(allow, test_id, r["mode"])
            (excused if entry else unexplained).append((r["file"], r["mode"], test_id))
    return unexplained, excused


def audit_regressions(check, prev, cur):
    """G4 / G6 in compare: (lines, bad). Absolute: no leak or crash (G4), no defect or nondeterministic replay (G6), a
    tool exit status of 0; delta: no increase and no variant the previous gate ran that this one did not."""
    keys = ("leaks", "errors") if check == "G4" else ("defects", "nondeterministic")
    lines, bad = [], False
    for variant in sorted(set(prev or {}) | set(cur or {})):
        a, b = (prev or {}).get(variant), (cur or {}).get(variant)
        if not isinstance(b, dict):
            if isinstance(a, dict):
                bad = True
                lines.append("{}: MISSING (the previous gate ran it)".format(variant))
            continue
        problems = []
        for k in keys:
            now, before = int(b.get(k) or 0), int((a or {}).get(k) or 0)
            if now > 0:
                problems.append("{} {}".format(now, k))
            if a is not None and now > before:
                problems.append("{} {} -> {}".format(k, before, now))
        if b.get("rc"):
            problems.append("exit status {}".format(b["rc"]))
        bad = bad or bool(problems)
        parts = ", ".join("{} {} -> {}".format(k, (a or {}).get(k), b.get(k)) for k in keys)
        if check == "G6":
            parts += ", commands {} -> {}, succeeded {} -> {}".format((a or {}).get("commands"), b.get("commands"),
                                                                    (a or {}).get("succeeded"), b.get("succeeded"))
            if b.get("actions_succeeded") is not None:
                parts += ", actions succeeding {} -> {} of {}".format((a or {}).get("actions_succeeded"),
                                                                   b["actions_succeeded"], b.get("actions_registered"))
        lines.append("{}: {}{}".format(variant, parts, ("  [" + "; ".join(problems) + "]") if problems else ""))
    return lines, bad


def coverage_loss(prev, cur, signoff=None):
    """Actions the previous gate's coverage fuzz drove to success that this gate's does not (G6 "coverage" variant),
    minus actions no longer registered: ([lost, unsigned], [lost, signed])."""
    a, b = (prev or {}).get("coverage"), (cur or {}).get("coverage")
    if not isinstance(a, dict) or not isinstance(b, dict) or a.get("succeeded_actions") is None or \
            b.get("succeeded_actions") is None:
        return [], []
    registered = set(b.get("succeeded_actions") or []) | set(b.get("never_actions") or [])
    lost = sorted(x for x in set(a["succeeded_actions"]) - set(b["succeeded_actions"]) if x in registered)
    signed = (signoff or {}).get("coverage") or set()
    return [x for x in lost if x not in signed], [x for x in lost if x in signed]


RECORD_ONLY = ("G1c", "G9", "G10")       # checks compare does not recompute at all (their own status: OWN_STATUS)


def compare_dirs(prev_dir, cur_dir, policy, reviewed, gates_root, spec_files=None, root=None, signoff=None,
                 allow=None, shared_tree_scope=False):
    """Full comparison of two gate folders. Returns (sections, ok) where sections is a list of dicts
    {"check", "status", "lines"}. Spec trials are compared on the spec files both records ran (spec_scope).
    root: the project whose committed policy files apply (default: DEFAULT_ROOT); signoff / allow: the second
    signatures of the current record and the allowlist (default: read from root). shared_tree_scope: the caller
    checks G10 on the working tree itself (the record's own G10 is not used)."""
    prev_dir, cur_dir = Path(prev_dir), Path(cur_dir)
    root = Path(root) if root is not None else DEFAULT_ROOT
    if signoff is None:
        signoff = load_signoff(root, cur_dir.name)
    if allow is None:
        allow = load_known_failing(root)
    sections = []

    def add(check, status, lines):
        sections.append({"check": check, "status": status, "lines": lines})

    pm, cm = read_json(prev_dir / "meta.json", {}), read_json(cur_dir / "meta.json", {})
    globs = spec_scope(pm, cm, spec_files)
    recorded = cm.get("recorded") or list(CHECKS)
    # a record lists the checks it recorded: missing data of one of them is SKIP (FIX2); older synthetic records
    # without the list are not held to it
    explicit = "recorded" in cm

    def missing(check, what):
        """A check without results in this record: not recorded (a partial record: noted, never a hand-over) or
        SKIP (it was recorded and left nothing: counts as FAIL)."""
        if check not in recorded:
            add(check, "N/R", ["{} not recorded in this (partial) record".format(what)])
        else:
            add(check, "SKIP", ["no {} in this gate (SKIP counts as FAIL)".format(what)])
    # G0 fingerprints
    pf, cf = pm.get("fingerprints", {}), cm.get("fingerprints", {})
    if pf.get("actions_fingerprint") and cf.get("actions_fingerprint"):
        if pf["actions_fingerprint"] != cf["actions_fingerprint"]:
            ok = "fingerprint" in policy["allow"]
            add("G0", "PASS" if ok else "FAIL", ["action fingerprint changed {} -> {} ({})".format(
                pf["actions_fingerprint"][:12], cf["actions_fingerprint"][:12],
                "allowed for this package" if ok else "not allowed for this package")])
        else:
            add("G0", "PASS", ["action fingerprint unchanged ({})".format(cf["actions_fingerprint"][:12])])
    elif explicit and "G0" in recorded and not cf.get("actions_fingerprint"):
        missing("G0", "action fingerprint")
    # G2 / G3: the delta rule, and G2-ABS (every spec passes in its own mode)
    for check, mode, name in (("G2", "own", "specs_own.jsonl"), ("G3", "realistic", "specs_realistic.jsonl")):
        a, b = scoped_rows(read_jsonl(prev_dir / name), globs), scoped_rows(read_jsonl(cur_dir / name), globs)
        where = " for spec files " + " & ".join(globs) if globs else ""
        if not b:
            missing(check, "{} trials{}".format(mode, where))
            continue
        problems, lines = [], []
        if a:
            res = compare_specs(a, b, mode, policy, reviewed, signoff)
            lines.append("{} trials vs {}: {} verdict change(s) ({} reviewed, {} signed), {} with changed state "
                         "digests{}, {} with a changed observation digest{}".format(
                             len(b), len(a), len(res["verdict_changes"]),
                             sum(c["reviewed"] for c in res["verdict_changes"]),
                             sum(c["reviewed"] and c["signed"] for c in res["verdict_changes"]),
                             len(res["state_changes"]), " (allowed)" if "state" in policy["allow"] else "",
                             len(res["obs_changes"]), " (allowed)" if "obs" in policy["allow"] else ""))
            if globs:
                lines.append("  spec files: {} (both records compared on these files only)".format(" & ".join(globs)))
            for c in res["verdict_changes"][:40]:
                state = "reviewed {} and signed".format(",".join(c["codes"])) if c["reviewed"] and c["signed"] else \
                    ("reviewed {}, UNSIGNED (digest {})".format(",".join(c["codes"]), c["digest"]) if c["reviewed"]
                     else "UNREVIEWED")
                lines.append("  verdict {} seed {}: passed {} -> {} [{}] {}{}".format(
                    c["key"], c["seed"], c["passed"][0], c["passed"][1], ",".join(c["fields"]), state,
                    "" if c["reviewed"] else " " + c["detail"]))
            if res["state_changes"] and "state" not in policy["allow"]:
                for c in res["state_changes"][:15]:
                    lines.append("  state {} seed {}: {}".format(c["key"], c["seed"], ",".join(c["fields"])))
            if res["obs_changes"] and "obs" not in policy["allow"]:
                for c in res["obs_changes"][:15]:
                    lines.append("  obs {} seed {}".format(c["key"], c["seed"]))
            if res["new"]:
                lines.append("  {} new trial(s) (new specs or seeds; they must pass: G2-ABS)".format(len(res["new"])))
            if res["removed"]:
                lines.append("  missing: " + ", ".join(res["removed"][:10]))
            if res["edited_specs"]:
                lines.append("  edited specs (spec_hash changed): " + ", ".join(res["edited_specs"][:20]))
            if res["unsigned_edits"]:
                lines.append("  edited specs without a signature: " + ", ".join(res["unsigned_edits"][:20]))
            problems += res["problems"]
        else:
            lines.append("{} trials; no previous {} trials{} (the absolute rule only)".format(len(b), mode, where))
        if check == "G2":
            # G2-ABS over the record's full own-mode rows, whatever the previous record ran (FIX2: a narrowed previous
            # record narrowed the absolute rule too)
            failing, excused = failing_specs(read_jsonl(cur_dir / name) if not spec_files else b, "own", allow)
            if failing:
                problems.append("G2-ABS: {} spec(s) fail in their own mode: {}".format(len(failing),
                                                                                    describe_failing(failing)))
            if excused:
                lines.append("  allowlisted failing specs ({}): {}".format(len(excused), describe_failing(excused)))
        for p in problems:
            lines.append("  problem: " + p)
        add(check, "FAIL" if problems else "PASS", lines)
    # G5 layout and generation 1
    pa, ca = read_json(prev_dir / "audits.json", {}), read_json(cur_dir / "audits.json", {})
    if pa.get("G5") and ca.get("G5"):
        lines, bad = [], False
        pl, cl = pa["G5"].get("layout", {}), ca["G5"].get("layout", {})
        if pl and cl:
            if pl.get("by_kind_owner") != cl.get("by_kind_owner"):
                bad = bad or "layout" not in policy["allow"]
                lines.append("layout counts changed: {} -> {} problems".format(pl.get("problems"), cl.get("problems")))
                keys = sorted(set(pl.get("by_kind_owner", {})) | set(cl.get("by_kind_owner", {})))
                for k in keys:
                    x, y = pl.get("by_kind_owner", {}).get(k, 0), cl.get("by_kind_owner", {}).get(k, 0)
                    if x != y:
                        lines.append("  {}: {} -> {}".format(k, x, y))
            else:
                lines.append("layout counts equal ({} problems)".format(cl.get("problems")))
        pg, cg = pa["G5"].get("gen1", {}).get("digests", {}), ca["G5"].get("gen1", {}).get("digests", {})
        common = sorted(set(pg) & set(cg))
        diff = [k for k in common if pg[k] != cg[k]]
        if common:
            if diff:
                bad = bad or "gen1" not in policy["allow"]
            lines.append("generation-1 digests vs previous gate: {}/{} equal".format(len(common) - len(diff), len(common)))
            lines += ["  changed: " + k[:160] for k in diff[:10]]
        add("G5", "FAIL" if bad else "PASS", lines)
    elif not ca.get("G5") and (pa.get("G5") or (explicit and "G5" in recorded)):
        missing("G5", "layout / generation-1 results")
    # G4 / G6: absolute, and every increase or dropped variant fails (they were INFO before phase 1)
    for check, label in (("G4", "perception audit"), ("G6", "fuzz")):
        if not ca.get(check):
            if pa.get(check) or (explicit and check in recorded):
                missing(check, label + " results")
            continue
        lines, bad = audit_regressions(check, pa.get(check), ca.get(check))
        if check == "G6":
            cov = ca[check].get("coverage")
            if isinstance(cov, dict) and cov.get("actions_registered"):
                lines.append("coverage: {} of {} registered actions succeed at least once; never: {}".format(
                    cov.get("actions_succeeded"), cov.get("actions_registered"),
                    short(", ".join(cov.get("never_actions") or []) or "none", 600)))
            unsigned, signed = coverage_loss(pa.get(check), ca.get(check), signoff)
            if signed:
                lines.append("coverage lost (signed): " + ", ".join(signed[:30]))
            if unsigned:
                bad = True
                lines.append("coverage LOST without a signature (actions the previous gate drove to success): " +
                             ", ".join(unsigned[:30]))
        add(check, "FAIL" if bad else "PASS", ["{}: {}".format(label, lines[0] if lines else "no variant")] + lines[1:])
    # G7 bench
    factor, ref_dir, ref_name = bench_reference(policy, prev_dir, gates_root)
    rb, cb = read_json(Path(ref_dir) / "bench.json", {}), read_json(cur_dir / "bench.json", {})
    if rb.get("G7") and cb.get("G7"):
        lines, bad = [], False
        for metric in ("substep_ms", "wait_1h_s", "day_s"):
            x, y = rb["G7"].get(metric), cb["G7"].get(metric)
            if x is None or y is None:
                continue
            limit = x * factor
            over = y > limit + 1e-9
            if metric in ("substep_ms", "wait_1h_s") and over:
                bad = True
            lines.append("{}: {} -> {} ({:+.1%}; limit {:.4g} = {} x {}){}".format(
                metric, x, y, (y - x) / x if x else 0.0, limit, ref_name, factor, "  OVER BUDGET" if over else ""))
        rs, cs = rb["G7"].get("systems", {}), cb["G7"].get("systems", {})
        moves = sorted(((cs.get(k, 0) - rs.get(k, 0)), k) for k in set(rs) | set(cs))
        worst = [m for m in moves[::-1][:5] if m[0] > 0.01]
        if worst:
            lines.append("largest per-system increases: " + ", ".join("{} {:+.3f} ms".format(k, d) for d, k in worst))
        add("G7", "FAIL" if bad else "PASS", lines)
    elif explicit and "G7" in recorded and not cb.get("G7"):
        missing("G7", "benchmark results")
    # tests: every test passes except the dated allowlist (absolute); no test removed, skipped or turned into an
    # expected failure against the previous record (FIX2); the counts (informational)
    pt, ct = read_json(prev_dir / "tests.json", {}), read_json(cur_dir / "tests.json", {})
    if any(ct.get(c) for c in ("G1", "G1b", "G8")) or pt.get("G1") or explicit:
        lines, bad = [], False
        if pt.get("G1") and not ct.get("G1"):
            if "G1" in recorded:
                bad = True
                lines.append("no unit-test results in this gate (SKIP counts as FAIL)")
            else:
                lines.append("unit tests not recorded in this (partial) record")
        for check in ("G1", "G1b", "G8") if explicit else ():
            if check in recorded and not ct.get(check) and not (check == "G1" and pt.get("G1")) and \
                    (cm.get("checks") or {}).get(check, {}).get("status") != "N/A":
                bad = True
                lines.append("no {} results in this record although it lists {} as recorded (SKIP counts as "
                             "FAIL)".format(check, check))
        quiet, quiet_ok = test_regressions(pt, ct, allow, signoff)
        if quiet:
            bad = True
            lines.append("{} test(s) removed, skipped or turned into expected failures vs {} without an allowlist "
                         "'skip' entry or a signature: {}".format(len(quiet), prev_dir.name,
                                                                  describe_regressions(quiet)))
        if quiet_ok:
            lines.append("{} test(s) removed or quiet, allowlisted or signed: {}".format(
                len(quiet_ok), describe_regressions(quiet_ok, 300)))
        rows = [r for c in ("G1", "G1b", "G8") for r in ct.get(c) or []]
        unexplained, excused = unexplained_test_failures(rows, allow, "kernel_core" in policy["requires"])
        if unexplained:
            bad = True
            lines.append("{} failing test(s) not on the allowlist: {}".format(len(unexplained), short(
                ["{} ({})".format(t, m) for _, m, t in unexplained], 600)))
        if excused:
            lines.append("{} allowlisted failing test(s): {}".format(len(excused), short(
                ["{} ({})".format(t, m) for _, m, t in excused], 400)))
        if pt.get("G1") and ct.get("G1"):
            pr = {(r["file"], r["mode"]): r for r in pt["G1"]}
            cr = {(r["file"], r["mode"]): r for r in ct["G1"]}
            for k in sorted(set(pr) | set(cr)):
                a, b = pr.get(k), cr.get(k)
                if a is None:
                    lines.append("new test file {} ({}): {} tests".format(k[0], k[1], b["ran"]))
                elif b is None:
                    lines.append("test file {} ({}) is gone".format(k[0], k[1]))
                elif a["ran"] != b["ran"] or a.get("skipped") != b.get("skipped"):
                    lines.append("{} ({}): ran {} -> {}, skipped {} -> {}".format(k[0], k[1], a["ran"], b["ran"],
                                                                                 a.get("skipped"), b.get("skipped")))
        status = "FAIL" if bad else ("N/R" if not rows and "G1" not in recorded else "PASS")
        add("G1", status, lines or ["every test passes; test counts unchanged"])
    # the record's own statuses that count (OWN_STATUS: G1c, G9 and G10 are not recomputed at all; G0, G4, G5 and G7
    # are, from the same data) -- G10 unless checked live
    own = cm.get("checks") or {}
    rec = [c for c in OWN_STATUS if c in own and not (c == "G10" and shared_tree_scope)]
    if rec:
        failing = [c for c in rec if own[c].get("status") in FAILING]
        add("REC", "FAIL" if failing else "PASS", ["this record's own {}: {}".format(
            ", ".join(rec), ", ".join("{} {}".format(c, own[c].get("status")) for c in rec))] +
            ["  {} {}: {}".format(c, own[c].get("status"), short(own[c].get("note", ""), 300)) for c in failing])
    ok = not any(s["status"] in FAILING for s in sections)
    return sections, ok
def record_incomplete(meta):
    """Why a record can never be a hand-over (some checks not recorded, or the specs narrowed), or None."""
    meta = meta or {}
    recorded = meta.get("recorded") or list(CHECKS)
    why = []
    if recorded != list(CHECKS):
        why.append("recorded " + ",".join(recorded))
    if meta.get("spec_files"):
        why.append("specs limited to " + meta["spec_files"])
    return "; ".join(why) or None


def compare_verdict(ok, incomplete):
    """(word, exit status): PASS 0, FAIL 1, INCOMPLETE 0 (nothing failed in what was recorded, but the record is
    partial or narrowed: an iteration aid, never a hand-over; meta.json "handover" is false)."""
    if not ok:
        return "FAIL", 1
    if incomplete:
        return "INCOMPLETE", 0
    return "PASS", 0


def render_compare(prev_dir, cur_dir, policy, sections, ok, incomplete=None):
    head = "compare {} -> {} (packages: {}; allowed: {}; codes: {})".format(
        Path(prev_dir).name, Path(cur_dir).name, "+".join(policy["packages"]) or "none",
        ",".join(sorted(policy["allow"])) or "nothing", ",".join(sorted(policy["codes"])) or "none")
    lines = [head]
    for s in sections:
        lines.append("{:4} {:5} {}".format(s["check"], s["status"], s["lines"][0] if s["lines"] else ""))
        lines += ["           " + l for l in s["lines"][1:]]
    word, _ = compare_verdict(ok, incomplete)
    lines.append("compare result: {}{}".format(word, " (partial record, not a hand-over: {})".format(incomplete)
                                                if word == "INCOMPLETE" else ""))
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# G10 scope

ISOLATE_MANIFEST = ".tier1_isolate.json"
ISOLATE_FORMAT = "house-tier1-isolate-v1"
ISOLATE_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def _file_sha(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _ignore_rules(root):
    """The plain patterns of root/.gitignore (no negations): enough to skip what git would ignore in an isolated copy
    (results/, *.pyc, gate records, ...)."""
    rules = []
    try:
        text = (Path(root) / ".gitignore").read_text(encoding="utf-8")
    except OSError:
        return rules
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!")):
            rules.append(line)
    return rules


def _ignored(rel, rules):
    """gitignore-like test of a posix path: a pattern ending in "/" names a directory anywhere on the path (or the
    path prefix when it contains a "/"); others match the whole path or its last component."""
    parts = rel.split("/")
    for rule in rules:
        anchored = rule.startswith("/")
        pattern = rule.strip("/")
        if rule.endswith("/"):
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts))]
            if "/" in pattern or anchored:
                if any(fnmatch.fnmatchcase(p, pattern) for p in prefixes):
                    return True
            elif any(fnmatch.fnmatchcase(part, pattern) for part in parts[:-1]):
                return True
        elif fnmatch.fnmatchcase(rel, pattern) or (not anchored and "/" not in pattern and
                                                    fnmatch.fnmatchcase(parts[-1], pattern)):
            return True
    return False


def tree_files(root, rules=None):
    """{posix path: sha256} of every file under root that git would see (no .git, caches or ignored files)."""
    root = Path(root)
    rules = _ignore_rules(root) if rules is None else rules
    out = {}
    for folder, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in ISOLATE_SKIP_DIRS)
        base = Path(folder)
        for name in sorted(files):
            path = base / name
            rel = path.relative_to(root).as_posix()
            if rel == ISOLATE_MANIFEST or name.endswith(".pyc") or _ignored(rel, rules):
                continue
            out[rel] = _file_sha(path)
    return out


def _git_blob_sha(path):
    """The git blob id of a file's content (sha1 of "blob <size>\\0" + content)."""
    import hashlib
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def isolated_changes(root):
    """(changed, error) of an isolated copy (cmd_isolate): every file added, edited or deleted since the copy was made
    from its base commit. The base is read from the project's own git object store (policy_root(): `git ls-tree` of
    the manifest's base commit), never from the copy's manifest, whose file list the copy could rewrite to hide an
    edit (FIX2). A base commit the project does not have fails the scope check."""
    manifest = read_json(Path(root) / ISOLATE_MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("format") != ISOLATE_FORMAT:
        return None, "not an isolated copy"
    base = str(manifest.get("base") or "")
    project = policy_root()
    listing = subprocess.run(["git", "-C", str(project), "ls-tree", "-r", "-z", "--full-tree", base],
                             capture_output=True) if base else None
    if listing is None or listing.returncode != 0:
        return None, "the isolated copy's base commit {!r} is not a commit of the project {}".format(base, project)
    rules = _ignore_rules(root)
    committed = {}
    for item in listing.stdout.decode("utf-8", "replace").split("\0"):
        head, _, rel = item.partition("\t")
        parts = head.split()
        if len(parts) == 3 and parts[1] == "blob" and rel and not _ignored(rel, rules):
            committed[rel] = parts[2]
    now = {}
    for folder, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in ISOLATE_SKIP_DIRS)
        for name in sorted(files):
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            if rel == ISOLATE_MANIFEST or name.endswith(".pyc") or _ignored(rel, rules):
                continue
            now[rel] = _git_blob_sha(path)
    changed = {rel for rel, sha in now.items() if committed.get(rel) != sha} | {rel for rel in committed if rel not in now}
    return sorted(f for f in changed if not match_any(f, TRANSIENT)), None


def tree_fingerprint(root):
    """sha256 over the content of every file a gate record's checks depend on (FINGERPRINT_PATHS as git sees them: no
    caches, ignored or transient files): the tree a check ran on (FIX2)."""
    import hashlib
    root = Path(root)
    rules = _ignore_rules(root)
    digest = hashlib.sha256()
    for top in FINGERPRINT_PATHS:
        base = root / top
        items = []
        if base.is_file():
            items.append((top, base))
        elif base.is_dir():
            for folder, dirs, files in os.walk(str(base)):
                dirs[:] = sorted(d for d in dirs if d not in ISOLATE_SKIP_DIRS)
                for name in sorted(files):
                    path = Path(folder) / name
                    items.append((path.relative_to(root).as_posix(), path))
        for rel, path in items:
            if rel.endswith(".pyc") or _ignored(rel, rules) or match_any(rel, TRANSIENT):
                continue
            try:
                sha = _file_sha(path)
            except OSError:
                sha = "unreadable"
            digest.update("{}\0{}\n".format(rel, sha).encode("utf-8"))
    return digest.hexdigest()


def tree_check(meta, root, any_tree=False):
    """(section or None, incomplete reason or None) of a record against the tree it describes (FIX2): the
    fingerprint of the record's tree now (meta "tree_root", else `root`) must equal the record's, and every check
    result must carry it. STALE (FAIL) otherwise; a record without a fingerprint, or whose tree is gone, cannot be
    verified (INCOMPLETE). any_tree: historical comparisons, not checked."""
    tree = (meta or {}).get("tree")
    if any_tree:
        return {"check": "TREE", "status": "N/A", "lines": ["tree not checked (--any-tree): not a hand-over check"]}, \
            "tree not checked (--any-tree)"
    if not tree:
        return {"check": "TREE", "status": "N/R", "lines": ["the record carries no tree fingerprint (recorded before "
                                                            "FIX2, or files changed while it ran): cannot be verified"]}, \
            "no tree fingerprint"
    where = Path((meta or {}).get("tree_root") or root)
    if not where.exists():
        return {"check": "TREE", "status": "N/R", "lines": ["the record's tree {} is gone".format(where)]}, \
            "the record's tree is gone"
    now = tree_fingerprint(where)
    stale = sorted(c for c, v in ((meta or {}).get("checks") or {}).items()
                   if isinstance(v, dict) and v.get("tree") != tree)
    lines = []
    if now != tree:
        lines.append("STALE: {} has changed since the record was made (fingerprint {} -> {})".format(
            where, tree[:12], now[:12]))
    if stale:
        lines.append("STALE: check(s) {} ran on another tree than the record's (a partial re-run merged with an "
                     "earlier run)".format(",".join(stale)))
    if lines:
        return {"check": "TREE", "status": "FAIL", "lines": lines}, None
    return {"check": "TREE", "status": "PASS", "lines": ["the record was made on this tree ({}; {})".format(
        tree[:12], where)]}, None


def changed_files(root):
    """(changed, error): files changed against HEAD plus untracked files, posix paths. An isolated copy (cmd_isolate)
    reports the files changed since it was made from its base commit."""
    if (Path(root) / ISOLATE_MANIFEST).exists():
        return isolated_changes(root)

    def git(*args):
        return subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    probe = git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None, "not a git work tree"
    files = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        res = git(*args)
        if res.returncode != 0:
            return None, res.stderr.strip()
        files |= {line.strip() for line in res.stdout.splitlines() if line.strip()}
    return sorted(f for f in files if not match_any(f, TRANSIENT)), None


def scope_check(root, policy, gate_id, shared_tree=False):
    """G10: every changed file in the package's list, its handoff or its gate folder (with --shared-tree, files of
    the packages running beside it are attributed to them). Without git (and outside an isolated copy) the scope
    cannot be checked: FAIL, never a silent skip. The gate's policy files (PROTECTED) are the main session's: a
    package that changes them fails, whatever its globs say (K-GATE, which defines their format, excepted)."""
    if not policy["packages"]:
        return {"status": "N/A", "note": "no --package given (main-session gate: no package scope to check)"}
    files, error = changed_files(root)
    if files is None:
        return {"status": "FAIL", "note": "scope cannot be checked: {} (a gate record needs git or an isolated copy; "
                                          "SKIP counts as FAIL)".format(error)}
    allowed = list(policy["files"])
    for pid in policy["packages"]:
        names = [pid] + PACKAGES[pid]["aka"]
        members = SUBSTAGES.get(PACKAGES[pid]["stage"], ())
        if members and pid not in members:
            # a union package (ADOPT-ALL): its member packages' handoffs and gate records are its own
            for member in members:
                names += [member] + PACKAGES[member]["aka"]
        for name in names:
            allowed.append("{}/handoff_{}.md".format(HANDOFF_DIR, gate_dirname(name)))
            allowed.append("{}/{}/*".format(GATE_DIR, gate_dirname(name)))     # a package's own gate records
    allowed.append("{}/{}/*".format(GATE_DIR, gate_dirname(gate_id)))
    outside, attributed = [], {}
    peers = []
    if shared_tree:
        for members in SUBSTAGES.values():
            if any(p in members for p in policy["packages"]):
                peers += [m for m in members if m not in policy["packages"] and m not in peers]
    protected = []
    for f in files:
        guarded = match_any(f, PROTECTED)
        if match_any(f, allowed) and (not guarded or policy.get("policy")):
            continue
        # a concurrent package's files, handoff and gate folder (its own gate record) are attributed to it (a policy
        # file only to a package that may write them)
        owner = next((p for p in peers if (not guarded or PACKAGES[p].get("policy")) and match_any(f, PACKAGES[p]["files"] + [
            "{}/handoff_{}.md".format(HANDOFF_DIR, gate_dirname(name)) for name in [p] + PACKAGES[p]["aka"]] + [
            "{}/{}/*".format(GATE_DIR, gate_dirname(name)) for name in [p] + PACKAGES[p]["aka"]])), None)
        if owner:
            attributed[f] = owner
        else:
            outside.append(f)
            if guarded:
                protected.append(f)
    status = "FAIL" if outside else "PASS"
    note = "{} changed file(s); {} outside the list of {}".format(len(files), len(outside), "+".join(policy["packages"]))
    if protected:
        note += "; {} of the gate's policy files changed (main session only: {})".format(len(protected), POLICY_DIR)
    if attributed:
        note += "; {} attributed to concurrent packages (--shared-tree)".format(len(attributed))
    return {"status": status, "note": note, "changed": files, "outside": outside, "attributed": attributed,
            "protected": protected}


# ----------------------------------------------------------------------------------------------------------------
# record

class Gate:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.root).resolve()
        self.gates_root = Path(args.gates_root).resolve() if args.gates_root else self.root / GATE_DIR
        self.id = args.id
        self.out = Path(args.out).resolve() if args.out else self.gates_root / gate_dirname(self.id)
        self.work = Path(args.work).resolve() if args.work else \
            Path(tempfile.gettempdir()) / "house_ai_tier1" / gate_dirname(self.id)
        names = []
        for raw in re.split(r"[+,]", args.package or ""):
            if raw.strip():
                pid = resolve_package(raw)
                if pid is None:
                    raise SystemExit("unknown package {!r} (see: tier1_gate.py packages)".format(raw))
                names.append(pid)
        if not names and resolve_package(self.id):
            names = [resolve_package(self.id)]
        stage = args.stage or (None if names else (self.id if self.id in STAGE_ORDER else "A0"))
        self.policy = policy_for(names, stage)
        self.selected = self._selected(args.only, args.skip)
        self.seeds = tuple(args.seeds) if args.seeds else SEEDS
        self.jobs_n = max(1, args.jobs or max(1, (os.cpu_count() or 2) - 1))
        self.results = {}       # check -> {"status", "note", ...}
        self.data = {"tests": {}, "audits": {}, "bench": {}, "extras": {}, "scope": {}}
        self.python = sys.executable
        self.script = str(Path(__file__).resolve())
        self.started = time.perf_counter()
        self._allow = self._signoff = None

    @property
    def allow(self):
        """The dated allowlist as committed (read once per run)."""
        if self._allow is None:
            self._allow = load_known_failing(policy_root())
        return self._allow

    @property
    def signoff(self):
        """The main session's second signatures for this record, as committed (read once per run)."""
        if self._signoff is None:
            self._signoff = load_signoff(policy_root(), self.id)
        return self._signoff

    @staticmethod
    def _selected(only, skip):
        chosen = [c.strip() for c in (only or "").split(",") if c.strip()] or list(CHECKS)
        skipped = {c.strip() for c in (skip or "").split(",") if c.strip()}
        bad = [c for c in chosen + list(skipped) if c not in CHECKS]
        if bad:
            raise SystemExit("unknown check(s): " + ", ".join(bad))
        return [c for c in CHECKS if c in chosen and c not in skipped]

    def log(self, text):
        print("[{:6.0f}s] {}".format(time.perf_counter() - self.started, text), flush=True)

    def set(self, check, status, note, **extra):
        self.results[check] = dict(status=status, note=note, **extra)

    def py(self, *args):
        return [self.python] + list(args)

    def env(self, extra=None):
        return child_env(self.root, extra)

    # ---- previous gate
    def prev_dir(self):
        arg = self.args.prev
        if arg and arg.lower() == "none":
            return None
        if arg:
            path = Path(arg)
            if not path.is_absolute() and not path.exists():
                path = self.gates_root / arg
            if not (path / "meta.json").exists():
                raise SystemExit("previous gate {} has no meta.json".format(path))
            narrowed = record_incomplete(read_json(path / "meta.json", {}))
            if narrowed:
                # FIX2: a partial or narrowed previous record silently narrowed the comparison (G2-ABS included)
                raise SystemExit("previous gate {} is partial or narrowed ({}): compare with a complete record".format(
                    path, narrowed))
            return path.resolve()
        # default: stage A packages and B0 compare with A0, B1 with B0, the adoption round with C1 (TIER1_INTEGRATION
        # 2.4, 3.1); any other gate with the latest complete gate of an earlier stage (parallel packages of one
        # sub-stage never with each other)
        return default_prev_dir(self.gates_root, self.policy["stage"], exclude=self.out)

    # ---- jobs
    def test_estimates(self):
        est = {}
        for path in (self.root / "tools" / "orchestration" / "green_tests.json",):
            for r in read_json(path, []) or []:
                est[(r["file"], "strict" if r.get("strict") else "normal")] = r.get("secs", 30.0)
        prev = self.prev_dir_cached
        if prev:
            for check in ("G1", "G1b"):
                for r in (read_json(prev / "tests.json", {}) or {}).get(check, []):
                    est[(r["file"], r["mode"])] = r.get("secs", 30.0)
        return est

    def test_jobs(self):
        tests = sorted(Path(p).name for p in glob.glob(str(self.root / "tests" / "test_*.py")))
        est = self.test_estimates()
        jobs = []
        outcomes = self.work / "tests"

        def unit(check, target, name, mode):
            # each test process runs through the _unittest worker: `python -m unittest`'s report and exit status plus
            # every test's outcome (FIX2)
            path = outcomes / "{}_{}_{}.json".format(check, name[:-3], mode)
            return self.py(self.script, "_unittest", str(path), target), {"file": name, "mode": mode,
                                                                          "outcomes": str(path)}
        if "G1" in self.selected:
            for name in tests:
                for mode in ("normal", "strict"):
                    extra = {"HOUSE_SIM_STRICT": "1"} if mode == "strict" else None
                    cmd, meta = unit("G1", "tests/" + name, name, mode)
                    jobs.append(Job("G1 {} {}".format(name, mode), "G1", cmd, self.env(extra),
                                    est=est.get((name, mode), est.get((name, "normal"), 30.0)) * 1.1,
                                    timeout=self.args.test_timeout, meta=meta))
        if "G1b" in self.selected:
            for d in DOMAINS:
                name = "test_{}.py".format(d)
                if name in tests:
                    cmd, meta = unit("G1b", "tests.test_" + d, name, "isolated")
                    jobs.append(Job("G1b {} isolated".format(name), "G1b", cmd, self.env({"HOUSE_SIM_DOMAINS": d}),
                                    est=est.get((name, "isolated"), est.get((name, "normal"), 30.0) * 0.7),
                                    timeout=self.args.test_timeout, meta=meta))
            for name in tests:
                if any(fnmatch.fnmatchcase(name, p) for p in KERNEL_TESTS):
                    cmd, meta = unit("G1b", "tests/" + name, name, "core")
                    jobs.append(Job("G1b {} core".format(name), "G1b", cmd, self.env({"HOUSE_SIM_DOMAINS": "core"}),
                                    est=est.get((name, "core"), est.get((name, "normal"), 30.0) * 0.5),
                                    timeout=self.args.test_timeout, meta=meta))
        if "G8" in self.selected and (self.root / "tests" / "test_room_golden.py").exists():
            for mode, extra in (("golden", None), ("golden_all", {"HOUSE_SIM_GOLDEN_ALL": "1"})):
                cmd, meta = unit("G8", "tests.test_room_golden", "test_room_golden.py", mode)
                jobs.append(Job("G8 test_room_golden.py {}".format(mode), "G8", cmd, self.env(extra),
                                est=est.get(("test_room_golden.py", mode), 60.0), timeout=self.args.test_timeout,
                                meta=meta))
        return jobs

    def spec_jobs(self):
        specs = collect_specs(self.root)
        if self.args.spec_files:
            specs = [s for s in specs if fnmatch.fnmatchcase(s[1], self.args.spec_files)]
        self.spec_count = len(specs)
        self.spec_modes = {key: own_mode(spec) for key, _, _, spec in specs}
        sys.path.insert(0, str(self.root))
        try:
            from house_sim.tasks.verify import spec_hash
        except Exception:                                     # noqa: BLE001
            spec_hash = None
        all_specs = {"specs": [s for _, _, _, s in specs]}
        self.work.mkdir(parents=True, exist_ok=True)
        write_json(self.work / "all_specs.json", all_specs, sort_keys=False)
        jobs, chunk_specs = [], max(1, self.args.chunk_specs)
        for check, mode, perception in (("G2", "own", None), ("G3", "realistic", "realistic")):
            if check not in self.selected:
                continue
            for n in range(0, len(specs), chunk_specs):
                part = specs[n:n + chunk_specs]
                chunk = {"root": str(self.root), "mode": mode, "perception": perception, "seeds": list(self.seeds),
                         "camera": self.args.camera if (check == "G3" and self.args.camera) else None,
                         "jobs": [{"key": key, "file": fname, "own_mode": own_mode(spec), "spec": spec,
                                   "spec_hash": self._spec_hash(spec_hash, spec)} for key, fname, _, spec in part]}
                cpath = self.work / "chunks" / "{}_{:04d}.json".format(mode, n)
                write_json(cpath, chunk, sort_keys=False)
                opath = self.work / "chunks" / "{}_{:04d}.jsonl".format(mode, n)
                jobs.append(Job("{} specs {}-{}".format(check, n, n + len(part) - 1), check,
                                self.py(self.script, "_trials", str(cpath), str(opath)), self.env(),
                                est=len(part) * len(self.seeds) * 2.5, timeout=self.args.chunk_timeout,
                                meta={"out": str(opath), "keys": [p[0] for p in part]}))
        return jobs

    @staticmethod
    def _spec_hash(fn, spec):
        if fn is None:
            return None
        try:
            return fn(spec)
        except Exception:                                     # noqa: BLE001
            return None

    def tool_jobs(self):
        jobs = []
        audit = self.work / "audits"
        geometric = self.args.camera == "geometric" or "camera_tools" in self.policy["requires"] or "camera" in self.extras
        if "G4" in self.selected:
            jobs.append(Job("G4 perception_audit room", "G4", self.py("tools/perception_audit.py", "6", "150", "--workers",
                            "4", "--json", str(audit / "g4_room.json")), self.env(), weight=4, est=60.0,
                            meta={"variant": "room", "json": str(audit / "g4_room.json")}))
            if geometric and source_has(self.root, "tools/perception_audit.py", "--camera"):
                jobs.append(Job("G4 perception_audit geometric", "G4", self.py(
                    "tools/perception_audit.py", "6", "150", "--workers", "4", "--camera", "geometric", "--json",
                    str(audit / "g4_geometric.json")), self.env(), weight=4, est=90.0,
                    meta={"variant": "geometric", "json": str(audit / "g4_geometric.json")}))
        if "G5" in self.selected:
            jobs.append(Job("G5 layout_audit", "G5", self.py("tools/layout_audit.py", "40", "--json",
                            str(audit / "g5_layout.json")), self.env(), weight=1, est=120.0,
                            meta={"json": str(audit / "g5_layout.json")}))
            parts = 4
            for i in range(parts):
                jobs.append(Job("G5 gen1 digests {}/{}".format(i + 1, parts), "G5",
                                self.py(self.script, "_gen1", str(self.root), str(audit / "g5_gen1_{}.json".format(i)),
                                        str(i), str(parts)), self.env(), est=45.0, meta={"part": i}))
        if "G6" in self.selected:
            variants = [("room", [])]
            if geometric and source_has(self.root, "tools/fuzz.py", "--camera"):
                variants.append(("geometric", ["--camera", "geometric"]))
            if "fuzz_battery" in self.policy["requires"] and source_has(self.root, "tools/fuzz.py", "--battery"):
                variants.append(("battery", ["--battery", "5", "--faults", "random"]))
            for name, flags in variants:
                path = audit / "g6_{}.json".format(name)
                jobs.append(Job("G6 fuzz {}".format(name), "G6", self.py("tools/fuzz.py", "6", "300", "--workers", "4",
                                *flags, "--json", str(path)), self.env(), weight=4, est=120.0,
                                meta={"variant": name, "json": str(path)}))
            if source_has(self.root, "tools/fuzz.py", "--coverage"):
                # every registered action driven toward its success path from every authored spec's setup (phase 1;
                # REVIEW_REPORT H13): the per-action success coverage the gate reports and compares
                path = audit / "g6_coverage.json"
                jobs.append(Job("G6 fuzz coverage", "G6", self.py("tools/fuzz.py", "--coverage", "--workers", "4",
                                                                  "--json", str(path)), self.env(), weight=4, est=2400.0,
                                meta={"variant": "coverage", "json": str(path)}))
        if "G1c" in self.selected:
            modules = self.inert_modules()
            if modules:
                jobs.append(Job("G1c inert imports", "G1c", self.py(self.script, "_inert", str(self.root),
                                str(audit / "g1c.json"), *modules), self.env(), est=20.0,
                                meta={"json": str(audit / "g1c.json"), "modules": modules}))
        if "G9" in self.selected:
            jobs += self.extra_jobs(quiet=False)
        return jobs

    def inert_modules(self):
        mods = []
        for pattern in self.policy["inert"]:
            for path in sorted(glob.glob(str(self.root / pattern))):
                rel = Path(path).relative_to(self.root).as_posix()
                if rel.endswith(".py"):
                    mod = rel[:-3].replace("/", ".")
                    if mod.endswith(".__init__"):
                        mod = mod[:-9]
                    if mod not in mods:
                        mods.append(mod)
        return mods

    @property
    def extras(self):
        if self.args.extras is not None:
            return [e.strip() for e in self.args.extras.split(",") if e.strip()]
        return list(self.policy["extras"])

    def extra_jobs(self, quiet):
        jobs, audit = [], self.work / "audits"
        for extra in self.extras:
            if extra == "physics" and quiet and (self.root / "tools/physics_audit.py").exists():
                # the record goes into the gate folder (kept with the gate), never into the temp folder
                jobs.append(Job("G9 physics_audit", "G9", self.py("tools/physics_audit.py", "--data", "--bench",
                                                                   "--out", str(self.out / "physics_audit.json")),
                                self.env(), est=60.0, meta={"extra": "physics"}))
            elif extra == "body" and not quiet and (self.root / "tools/body_audit.py").exists():
                # the record goes into the gate folder (B0's is the baseline BODY-CORE checks --against), never over
                # the default file in the temp folder that people use for their own runs
                jobs.append(Job("G9 body_audit", "G9", self.py("tools/body_audit.py", "--baseline", "--out",
                                                                str(self.out / "body_audit.json")), self.env(),
                                est=120.0, meta={"extra": "body"}))
            elif extra == "gen" and not quiet:
                for name in ("test_generator_compat", "test_generator_gen2"):
                    if (self.root / "tests" / (name + ".py")).exists():
                        jobs.append(Job("G9 {}".format(name), "G9", self.py("-m", "unittest", "tests." + name),
                                        self.env(), est=60.0, meta={"extra": "gen", "test": name}))
                if source_has(self.root, "tools/layout_audit.py", "--gen"):
                    jobs.append(Job("G9 layout_audit gen 2", "G9", self.py("tools/layout_audit.py", "40", "--gen", "2",
                                    "--json", str(audit / "g9_layout_gen2.json")), self.env(), est=150.0,
                                    meta={"extra": "gen", "json": str(audit / "g9_layout_gen2.json")}))
            elif extra == "gen" and quiet:
                jobs.append(Job("G9 gen-2 timing", "G9", self.py(self.script, "_gentiming", str(self.root),
                                str(audit / "g9_gentiming.json"), str(self.args.bench_repeats)), self.env(), est=60.0,
                                meta={"extra": "gen", "json": str(audit / "g9_gentiming.json")}))
            elif extra == "sweep" and quiet and (self.root / "tools/sweep_bench.py").exists():
                jobs.append(Job("G9 sweep_bench", "G9", self.py("tools/sweep_bench.py"), self.env(), est=120.0,
                                meta={"extra": "sweep"}))
        return jobs

    # ---- evaluation of finished jobs
    def collect_tests(self, jobs, prev=None):
        """G1, G1b, G8: every test passes (absolute) except entries of the dated allowlist; a new failure against the
        previous gate is named in the note."""
        rows = defaultdict(list)
        for job in jobs:
            if job.check in ("G1", "G1b", "G8"):
                rows[job.check].append(test_row(job))
        for check in rows:
            rows[check].sort(key=lambda r: (r["file"], r["mode"]))
        self.data["tests"].update(rows)
        allow = self.allow
        # tests that went quiet against the previous record (FIX2): removed, skipped, expected failures, files gone
        prev_tests = read_json(prev / "tests.json", {}) if prev else None
        quiet, quiet_ok = test_regressions(prev_tests, rows, allow, self.signoff)
        quiet_by = defaultdict(list)
        for item in quiet:
            quiet_by[item[0]].append(item)

        def quiet_note(check):
            items = quiet_by.get(check)
            if not items:
                return ""
            return "; {} test(s) removed or quiet vs {} without an allowlist 'skip' entry or a signature: {}".format(
                len(items), prev.name, describe_regressions(items, 400))

        def excused_note(excused):
            return "; {} allowlisted failing test(s) ({})".format(len(excused), short(
                ["{} ({})".format(t, m) for _, m, t in excused], 300)) if excused else ""

        if "G1" in self.selected:
            g1 = rows.get("G1", [])
            bad = [r for r in g1 if r["rc"]]
            unexplained, excused = unexplained_test_failures(g1, allow)
            files = len({r["file"] for r in g1})
            self.set("G1", "FAIL" if unexplained or not g1 or quiet_by.get("G1") else "PASS",
                     "{} test files x normal+strict: {} tests (normal), {} failing run(s){}{}{}{}".format(
                         files, sum(r["ran"] for r in g1 if r["mode"] == "normal"), len(bad),
                         (": " + ", ".join("{} ({})".format(r["file"], r["mode"]) for r in bad[:8])) if bad else "",
                         "; NOT on the allowlist: {}".format(short(["{} ({})".format(t, m) for _, m, t in unexplained],
                                                                   400)) if unexplained else "",
                         excused_note(excused), quiet_note("G1")))
        if "G1b" in self.selected:
            g1b = rows.get("G1b", [])
            iso = [r for r in g1b if r["mode"] == "isolated"]
            core = [r for r in g1b if r["mode"] == "core"]
            bad_iso = [r for r in iso if r["rc"]]
            bad_core = [r for r in core if r["rc"]]
            required = "kernel_core" in self.policy["requires"]
            prev_rows = (read_json(prev / "tests.json", {}) or {}).get("G1b") if prev else None
            fresh_iso = new_failures(g1b, prev_rows, "isolated")
            # absolute from phase 1 on: every isolated-domain test and (from B1 on) every kernel test with
            # HOUSE_SIM_DOMAINS=core passes, except allowlisted ones (a carried failure is no longer excused)
            unexplained, excused = unexplained_test_failures(g1b, allow, core_required=required)
            status = "FAIL" if unexplained or not g1b or quiet_by.get("G1b") else "PASS"
            if not required and bad_core and status == "PASS":
                status = "INFO"
            n_iso = sum(len(r.get("failed_tests", [])) for r in bad_iso)
            note = "isolated domains {}/{} files ok{}; kernel files with HOUSE_SIM_DOMAINS=core {}/{} ok{}".format(
                len(iso) - len(bad_iso), len(iso),
                (" ({} failing test(s) in {}{})".format(
                    n_iso, ", ".join(r["file"] for r in bad_iso[:8]),
                    "; NEW vs {}: {}".format(prev.name, short([f for _, f in fresh_iso], 300)) if fresh_iso else ""))
                if bad_iso else "",
                len(core) - len(bad_core), len(core),
                ("" if not bad_core else " (failing: {}; {})".format(
                    ", ".join(r["file"] for r in bad_core[:8]),
                    "required from B1 on" if required else "recorded only: required from B1 on")))
            if unexplained:
                note += "; NOT on the allowlist: {}".format(short(["{} ({})".format(t, m) for _, m, t in unexplained],
                                                                 400))
            self.set("G1b", status, note + excused_note(excused) + quiet_note("G1b"))
        if "G8" in self.selected:
            g8 = rows.get("G8", [])
            if not g8:
                required = "golden" in self.policy["requires"]
                self.set("G8", "FAIL" if required else "N/A",
                         "tests/test_room_golden.py not present ({})".format(
                             "required from B1 on" if required else "PHYS-INT creates it in B1"))
            else:
                unexplained, excused = unexplained_test_failures(g8, allow)
                self.set("G8", "FAIL" if unexplained or quiet_by.get("G8") else "PASS", "room golden: {}{}{}".format(
                    ", ".join("{} {}".format(r["mode"], "FAIL" if r["rc"] else "ok") for r in g8),
                    excused_note(excused), quiet_note("G8")))

    def gather_specs(self, jobs):
        rows = {"own": [], "realistic": []}
        crashed = []
        for job in jobs:
            if job.check not in ("G2", "G3"):
                continue
            got = read_jsonl(job.meta["out"])
            mode = "own" if job.check == "G2" else "realistic"
            done = {(r["key"], r["seed"]) for r in got}
            if job.result["rc"] != 0:
                crashed.append("{}: rc {} {}".format(job.name, job.result["rc"], tail(job.result["stderr"], 600)))
            for key in job.meta["keys"]:
                for seed in self.seeds:
                    if (key, seed) not in done:
                        got.append({"key": key, "seed": seed, "mode": mode, "passed": False, "wrong": [],
                                    "error": "worker failed: rc {} {}".format(job.result["rc"],
                                                                              short(tail(job.result["stderr"], 300), 300))})
            rows[mode] += got
        summary = {}
        for mode in rows:
            rows[mode].sort(key=lambda r: (r["key"], r["seed"]))
            specs = defaultdict(list)
            for r in rows[mode]:
                specs[r["key"]].append(r)
            failing = {k: [r["seed"] for r in v if not r.get("passed")] for k, v in specs.items()}
            failing = {k: v for k, v in failing.items() if v}
            summary[mode] = {"specs": len(specs), "passed": len(specs) - len(failing), "trials": len(rows[mode]),
                             "failing": failing}
        self.spec_rows, self.spec_summary, self.spec_crashes = rows, summary, crashed
        # determinism: realistic-own specs appear in both runs and must give identical records
        mismatch = []
        if rows["own"] and rows["realistic"]:
            own = index_trials(rows["own"])
            for r in rows["realistic"]:
                a = own.get((r["key"], r["seed"]))
                if a is None or a.get("perception") != "realistic":
                    continue
                strip = ("mode", "secs")
                if {k: v for k, v in a.items() if k not in strip} != {k: v for k, v in r.items() if k not in strip}:
                    mismatch.append("{} seed {}".format(r["key"], r["seed"]))
        self.spec_mismatch = mismatch

    def evaluate_specs(self, prev):
        """G2 / G3: the delta rule against the previous gate (verdict changes reviewed by the package and signed by
        the main session, digests only where allowed, no missing trial) and, for G2, the absolute rule G2-ABS (every
        spec passes in its own mode on every seed, new specs included, unless allowlisted)."""
        reviewed = load_reviewed(self.root, self.out, self.policy["packages"], self.args.reviewed)
        self.reviewed = reviewed
        for check, mode in (("G2", "own"), ("G3", "realistic")):
            if check not in self.selected:
                continue
            s = self.spec_summary[mode]
            note = "{}: {}/{} specs pass ({} trials)".format("own mode" if mode == "own" else "forced realistic",
                                                             s["passed"], s["specs"], s["trials"])
            status = "PASS"
            prev_rows = read_jsonl(prev / "specs_{}.jsonl".format(mode)) if prev else []
            # a record narrowed by --spec-files is compared on those files only (and a narrowed previous gate on its)
            globs = spec_scope(read_json(prev / "meta.json", {}) if prev else {}, {"spec_files": self.args.spec_files})
            prev_rows = scoped_rows(prev_rows, globs)
            problems = []
            if prev_rows:
                res = compare_specs(prev_rows, scoped_rows(self.spec_rows[mode], globs), mode, self.policy, reviewed,
                                    self.signoff)
                note += "; vs {}: {} verdict change(s) ({} reviewed, {} signed), {} state-digest change(s), {} " \
                        "observation-digest change(s)".format(
                            prev.name, len(res["verdict_changes"]), sum(c["reviewed"] for c in res["verdict_changes"]),
                            sum(c["reviewed"] and c["signed"] for c in res["verdict_changes"]),
                            len(res["state_changes"]), len(res["obs_changes"]))
                problems += res["problems"]
            elif mode == "own":
                note += " (no previous gate)"
            else:
                status = "INFO"
                note += " (baseline recorded; forced-realistic results are not required to pass)"
            if mode == "own":
                failing, excused = failing_specs(self.spec_rows[mode], "own", self.allow)
                if failing:
                    problems.append("G2-ABS: {} spec(s) fail in their own mode: {}".format(
                        len(failing), describe_failing(failing, 8)))
                if excused:
                    note += "; {} allowlisted failing spec(s)".format(len(excused))
            if problems:
                status = "FAIL"
                note += " [" + "; ".join(problems) + "]"
            if self.spec_crashes:
                status = "FAIL"
                note += "; {} worker crash(es)".format(len(self.spec_crashes))
            if check == "G3" and self.spec_mismatch:
                status = "FAIL"
                note += "; NONDETERMINISTIC: {} realistic-spec trial(s) differ between the G2 and G3 runs ({})".format(
                    len(self.spec_mismatch), ", ".join(self.spec_mismatch[:5]))
            elif check == "G3" and self.spec_rows["own"]:
                same = sum(1 for r in self.spec_rows["own"] if r.get("perception") == "realistic")
                note += "; {} realistic-spec trials identical in G2 and G3".format(same)
            self.set(check, status, note)

    def collect_tools(self, jobs, prev):
        audits = defaultdict(dict)
        for job in jobs:
            res, meta = job.result, job.meta
            if job.check == "G4":
                summary = summarize_leaks(read_json(meta["json"]))
                summary.update(rc=res["rc"], secs=res["secs"])
                if res["rc"] and not summary["leaks"] and not summary["errors"]:
                    summary["tail"] = tail(res["stderr"] + res["stdout"], 1500)
                audits["G4"][meta["variant"]] = summary
            elif job.check == "G6":
                summary = summarize_fuzz(read_json(meta["json"]), res["stdout"])
                summary.update(rc=res["rc"], secs=res["secs"])
                if res["rc"] and not summary["defects"] and not summary["nondeterministic"]:
                    summary["tail"] = tail(res["stderr"] + res["stdout"], 1500)
                audits["G6"][meta["variant"]] = summary
            elif job.check == "G5" and "part" not in meta:
                layout = summarize_layout(read_json(meta["json"], []))
                layout.update(rc=res["rc"], secs=res["secs"])
                m = re.search(r"houses: (\d+)", res["stdout"] or "")
                layout["houses"] = int(m.group(1)) if m else None
                if res["rc"] and not layout["problems"]:
                    layout["tail"] = tail(res["stderr"] + res["stdout"], 1500)
                audits["G5"]["layout"] = layout
            elif job.check == "G5":
                part = read_json(self.work / "audits" / "g5_gen1_{}.json".format(meta["part"]), None)
                audits["G5"].setdefault("gen1_parts", []).append(
                    {"part": meta["part"], "rc": res["rc"], "digests": part or {},
                     "tail": tail(res["stderr"], 800) if res["rc"] else ""})
            elif job.check == "G1c":
                self.data["tests"]["G1c"] = read_json(meta["json"], {}) or {"error": tail(res["stderr"], 1500)}
            elif job.check == "G9":
                self.record_extra(job)
        for check in ("G4", "G6"):
            if check in self.selected:
                variants = audits.get(check, {})
                bad = [v for v, s in variants.items() if s["rc"]]
                label = "perception audit" if check == "G4" else "fuzz"
                parts = []
                for v, s in sorted(variants.items()):
                    if check == "G4":
                        parts.append("{}: {} leaks, {} crashes".format(v, s["leaks"], s["errors"]))
                    else:
                        parts.append("{}: {} commands, {} defects, {} nondeterministic{}".format(
                            v, s.get("commands"), s["defects"], s["nondeterministic"],
                            ", {} of {} actions succeed".format(s["actions_succeeded"], s.get("actions_registered"))
                            if s.get("actions_succeeded") is not None else ""))
                missing = []
                if check == "G6" and source_has(self.root, "tools/fuzz.py", "--coverage") and "coverage" not in variants:
                    missing.append("the coverage fuzz (--coverage) did not run")
                if check == "G4" and "camera_tools" in self.policy["requires"] and "geometric" not in variants:
                    missing.append("--camera geometric missing from tools/perception_audit.py")
                if check == "G6" and "camera_tools" in self.policy["requires"] and "geometric" not in variants:
                    missing.append("--camera geometric missing from tools/fuzz.py")
                if check == "G6" and "fuzz_battery" in self.policy["requires"] and "battery" not in variants:
                    missing.append("--battery/--faults missing from tools/fuzz.py")
                coverage = ""
                if check == "G6":
                    union = action_coverage(variants)
                    if union:
                        coverage = "; per-action success coverage (all variants): {} of {} registered actions " \
                                   "succeed at least once; never: {}".format(
                                       len(union["succeeded"]), union["registered"],
                                       short(", ".join(union["never"]) or "none", 500))
                        audits["G6"]["union"] = {"registered": union["registered"],
                                                 "succeeded": len(union["succeeded"]), "never": union["never"]}
                self.set(check, "FAIL" if bad or missing or not variants else "PASS",
                         "{} {}{}{}".format(label, "; ".join(parts) or "did not run",
                                            (" [" + "; ".join(missing) + "]") if missing else "", coverage))
        if "G5" in self.selected:
            g5 = audits.get("G5", {})
            digests = {}
            part_fail = []
            for part in g5.pop("gen1_parts", []):
                digests.update(part["digests"])
                if part["rc"]:
                    part_fail.append(part["tail"])
            entries, pinned = gen1_entries(self.root)
            expected = {gen1_key(e): e.get("digest") for e in entries}
            errors = [k for k, v in digests.items() if str(v).startswith("error:")]
            g5["gen1"] = {"pinned_file": pinned, "entries": len(entries), "digests": digests}
            notes, bad = [], bool(part_fail or errors)
            layout = g5.get("layout", {})
            notes.append("layout: {} houses, {} problems".format(layout.get("houses"), layout.get("problems")))
            prev_layout = (read_json(prev / "audits.json", {}) or {}).get("G5", {}).get("layout") if prev else None
            if prev_layout is not None:
                same = prev_layout.get("by_kind_owner") == layout.get("by_kind_owner")
                notes.append("counts {} {}".format("equal to" if same else "DIFFER from", prev.name))
                if not same and "layout" not in self.policy["allow"]:
                    bad = True
            else:
                notes.append("counts recorded")
            if pinned:
                diff = [k for k, d in expected.items() if digests.get(k) != d]
                g5["gen1"]["changed"] = diff
                notes.append("generation-1 digests {}/{} equal to {}".format(len(expected) - len(diff), len(expected), GEN1_FILE))
                if diff and "gen1" not in self.policy["allow"]:
                    bad = True
            else:
                notes.append("generation-1 digests computed for {} entries ({} not present)".format(len(digests), GEN1_FILE))
            if errors:
                notes.append("{} generation error(s)".format(len(errors)))
            if part_fail:
                notes.append("{} digest worker failure(s)".format(len(part_fail)))
            self.set("G5", "FAIL" if bad else "PASS", "; ".join(notes))
        if "G1c" in self.selected:
            report = self.data["tests"].get("G1c")
            modules = self.inert_modules()
            if not modules:
                self.set("G1c", "N/A", "no import-inert modules listed for this package")
            elif not report or "error" in report:
                self.set("G1c", "FAIL", "inert-import worker failed: {}".format(short((report or {}).get("error", "no report"), 300)))
            else:
                bad = {m: r for m, r in report.items() if not r.get("ok") or r.get("registered")}
                self.set("G1c", "FAIL" if bad else "PASS", "{} module(s) imported{}".format(
                    len(report), "; registering or failing: " + short(bad, 400) if bad else ", none registers anything"))
        self.data["audits"] = dict(audits)

    def record_extra(self, job):
        res, meta = job.result, job.meta
        entry = {"rc": res["rc"], "secs": res["secs"], "out": tail(res["stdout"], 2500)}
        if res["rc"]:
            entry["tail"] = tail(res["stderr"] + res["stdout"], 2000)
        if meta.get("json"):
            entry["json"] = read_json(meta["json"])
        self.data["extras"][job.name.replace("G9 ", "")] = entry

    def evaluate_extras(self):
        if "G9" not in self.selected:
            return
        extras = self.extras
        if not extras:
            self.set("G9", "N/A", "no extras for this package")
            return
        notes, bad = [], False
        for extra in extras:
            tool, introducer = EXTRA_TOOLS.get(extra, (None, None))
            required = introducer in self.policy["packages"] or (
                introducer in PACKAGES and stage_index(self.policy["stage"]) > stage_index(PACKAGES[introducer]["stage"]))
            ran = {k: v for k, v in self.data["extras"].items() if self._extra_of(k) == extra}
            if extra == "camera":
                ok = self.results.get("G4", {}).get("status") != "FAIL" and self.results.get("G6", {}).get("status") != "FAIL"
                has = source_has(self.root, "tools/perception_audit.py", "--camera")
                if not has:
                    notes.append("camera: tools have no --camera yet" + (" (REQUIRED)" if required else ""))
                    bad = bad or required
                else:
                    notes.append("camera: geometric tool runs in G4/G6 {}".format("ok" if ok else "failed"))
                    bad = bad or not ok
                continue
            if not ran:
                missing = tool and not (self.root / tool).exists()
                notes.append("{}: {}{}".format(extra, "{} not present".format(tool) if missing else "nothing to run",
                                               " (REQUIRED)" if required and missing else ""))
                bad = bad or (required and missing)
                continue
            for name, entry in sorted(ran.items()):
                status = "ok" if entry["rc"] == 0 else "FAILED"
                if name == "gen-2 timing" and isinstance(entry.get("json"), dict) and entry["json"].get("skipped"):
                    status = "skipped ({})".format(entry["json"]["skipped"])
                elif name == "gen-2 timing" and isinstance(entry.get("json"), dict) and entry["json"].get("ratio"):
                    ratio = entry["json"]["ratio"]
                    status = "gen2/gen1 time {:.2f} {}".format(ratio, "ok" if ratio <= 1.2 else "OVER 1.20")
                    if ratio > 1.2:
                        bad = True
                notes.append("{}: {}".format(name, status))
                bad = bad or entry["rc"] != 0
        self.set("G9", "FAIL" if bad else "PASS", "; ".join(notes))

    @staticmethod
    def _extra_of(name):
        if name.startswith("physics"):
            return "physics"
        if name.startswith("body"):
            return "body"
        if name.startswith("sweep"):
            return "sweep"
        return "gen"

    def run_bench(self, prev):
        if "G7" not in self.selected:
            return
        repeats = max(1, self.args.bench_repeats)
        runs = []
        for i in range(repeats):
            res = run_job(Job("G7 bench_systems", "G7", self.py("tools/bench_systems.py", "40", "0", "1", "2", "3"),
                              self.env(), timeout=1800), self.root)
            if res["rc"]:
                self.set("G7", "FAIL", "bench_systems failed: " + short(tail(res["stderr"], 400), 400))
                return
            runs.append(parse_bench(res["stdout"]))
        day = None
        if source_has(self.root, "tools/bench_systems.py", "--day"):
            res = run_job(Job("G7 bench day", "G7", self.py("tools/bench_systems.py", "--day"), self.env(),
                              timeout=3600), self.root)
            day = parse_bench(res["stdout"]).get("day_s") if res["rc"] == 0 else None
        wait_path = self.work / "audits" / "g7_wait.json"
        res = run_job(Job("G7 wait", "G7", self.py(self.script, "_benchwait", str(self.root), str(wait_path), "0,1,2,3",
                                                 str(repeats)), self.env(), timeout=1800), self.root)
        wait = read_json(wait_path, {}) if res["rc"] == 0 else {}
        bench = {"repeats": repeats, "substep_ms": min(r.get("substep_ms", 1e9) for r in runs),
                 "substep_ms_runs": [r.get("substep_ms") for r in runs],
                 "bench_wait_1h_s": min(r.get("bench_wait_1h_s", 1e9) for r in runs),
                 "systems": {k: min(r["systems"].get(k, 1e9) for r in runs) for k in runs[0]["systems"]},
                 "wait_1h_s": wait.get("wait_1h_s"), "wait_per_seed_s": wait.get("per_seed_s")}
        if day is not None:
            bench["day_s"] = day
        if "bench_day" in self.policy["requires"] and day is None:
            self.set("G7", "FAIL", "tools/bench_systems.py --day missing or failed (required from TIME-EVAL on)")
        self.data["bench"]["G7"] = bench
        factor, ref_dir, ref_name = bench_reference(self.policy, prev, self.gates_root) if prev else (None, None, None)
        ref = (read_json(Path(ref_dir) / "bench.json", {}) or {}).get("G7") if ref_dir else None
        note = "{:.2f} ms/substep (best of {}), 1 h wait {:.3f} s (seeds 0-3, best of {})".format(
            bench["substep_ms"], repeats, bench["wait_1h_s"] or -1, repeats)
        if ref:
            over = []
            for metric in ("substep_ms", "wait_1h_s"):
                if ref.get(metric) and bench.get(metric) and bench[metric] > ref[metric] * factor + 1e-9:
                    over.append("{} {:.4g} > {} x {} = {:.4g}".format(metric, bench[metric], ref_name, factor,
                                                                      ref[metric] * factor))
            note += "; vs {} ({}x): {}".format(ref_name if ref_name != "prev" else Path(ref_dir).name, factor,
                                               "OVER: " + "; ".join(over) if over else "within budget")
            if self.results.get("G7", {}).get("status") != "FAIL":
                self.set("G7", "FAIL" if over else "PASS", note)
        elif self.results.get("G7", {}).get("status") != "FAIL":
            self.set("G7", "INFO", note + " (baseline recorded)")

    def run_info(self):
        path = self.work / "info.json"
        res = run_job(Job("G0 info", "G0", self.py(self.script, "_info", str(self.root), str(path)), self.env(),
                          timeout=600), self.root)
        info = read_json(path, {}) if res["rc"] == 0 else {"error": tail(res["stderr"], 1500)}
        self.fingerprints = info
        if "G0" in self.selected:
            if "error" in info:
                self.set("G0", "FAIL", "could not import the simulator: " + short(info["error"], 400))
            else:
                self.set("G0", "PASS", "action fingerprint {} ({} actions, {} systems)".format(
                    info["actions_fingerprint"][:12], info["actions"], info["systems"]))

    # ---- main
    def run(self):
        prev = self.prev_dir()
        self.prev_dir_cached = prev
        self.out.mkdir(parents=True, exist_ok=True)
        if self.work.exists():
            shutil.rmtree(str(self.work), ignore_errors=True)
        (self.work / "audits").mkdir(parents=True, exist_ok=True)
        old_meta = read_json(self.out / "meta.json", {}) if self.selected != list(CHECKS) else {}
        self.log("tier-1 gate {} (packages: {}; stage {}; previous gate: {}; {} jobs; root {})".format(
            self.id, "+".join(self.policy["packages"]) or "none", self.policy["stage"], prev.name if prev else "none",
            self.jobs_n, self.root))
        started = now_iso()
        for rel in EMPTY_DIRS:          # an isolated copy made from `git archive` lacks the empty directories
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        # the tree the checks run on (FIX2): compared with the tree when the record is written; any change between
        # (another owner's edit, a test writing into the project) makes this run's results STALE
        self.tree_start = tree_fingerprint(self.root)
        self.run_info()
        if "G0" in self.selected and prev and "G0" in self.results and self.results["G0"]["status"] == "PASS":
            pf = (read_json(prev / "meta.json", {}) or {}).get("fingerprints", {}).get("actions_fingerprint")
            if pf and pf != self.fingerprints.get("actions_fingerprint"):
                allowed = "fingerprint" in self.policy["allow"]
                self.set("G0", "PASS" if allowed else "FAIL", self.results["G0"]["note"] + "; changed vs {} ({})".format(
                    prev.name, "allowed" if allowed else "NOT allowed for this package"))
            elif pf:
                self.set("G0", "PASS", self.results["G0"]["note"] + "; unchanged vs " + prev.name)
        # phase 1: tests and spec trials share the pool
        jobs = self.test_jobs() + self.spec_jobs()
        if jobs:
            self.log("phase 1: {} jobs (unit tests G1/G1b/G8, spec trials G2/G3) on {} slots".format(len(jobs), self.jobs_n))
            schedule(jobs, self.jobs_n, self.root, self.progress)
        self.collect_tests(jobs, prev)
        if any(c in self.selected for c in ("G2", "G3")):
            self.gather_specs(jobs)
            self.evaluate_specs(prev)
        # phase 2: audits and tool checks
        tools = self.tool_jobs()
        if tools:
            self.log("phase 2: {} jobs (G1c, G4, G5, G6, G9)".format(len(tools)))
            schedule(tools, max(self.jobs_n, 4), self.root, self.progress)
        self.collect_tools(tools, prev)
        # phase 3: benchmarks alone
        quiet = self.extra_jobs(quiet=True) if "G9" in self.selected else []
        if "G7" in self.selected or quiet:
            self.log("phase 3: benchmarks (alone)")
            self.run_bench(prev)
            for job in quiet:
                job.result = run_job(job, self.root)
                self.record_extra(job)
        self.evaluate_extras()
        # phase 4: scope
        if "G10" in self.selected:
            scope = scope_check(self.root, self.policy, self.id, self.args.shared_tree)
            self.data["scope"]["G10"] = scope
            self.set("G10", scope["status"], scope["note"] + (": " + ", ".join(scope.get("outside", [])[:12])
                                                              if scope.get("outside") else ""))
        self.write(prev, started, old_meta)
        return 0 if self.passed else 1

    def progress(self, job, done, total):
        res = job.result
        if job.check in ("G2", "G3"):
            if res["rc"] or done == total or done % max(1, total // 20) == 0:
                self.log("  {:4d}/{} {} {} ({:.0f}s){}".format(done, total, "ok  " if res["rc"] == 0 else "FAIL",
                                                              job.name, res["secs"], "" if res["rc"] == 0 else " rc {}".format(res["rc"])))
        else:
            self.log("  {:4d}/{} {} {} ({:.0f}s)".format(done, total, "ok  " if res["rc"] == 0 else "FAIL", job.name, res["secs"]))

    # ---- output
    def write(self, prev, started, old_meta):
        partial = self.selected != list(CHECKS)
        before = old_meta.get("recorded") or (list(CHECKS) if old_meta and not old_meta.get("partial")
                                              else old_meta.get("partial") or [])
        recorded = sorted(set(before) | set(self.selected), key=CHECKS.index) if partial else list(CHECKS)
        out = self.out

        def merged(name, new):
            if not partial:
                return new
            old = read_json(out / name, {}) or {}
            old.update({k: v for k, v in new.items() if v})
            return old

        if "G2" in self.selected or "G3" in self.selected:
            for mode in ("own", "realistic"):
                check = "G2" if mode == "own" else "G3"
                if check in self.selected:
                    with open(out / "specs_{}.jsonl".format(mode), "w", encoding="utf-8", newline="\n") as f:
                        for row in self.spec_rows[mode]:
                            f.write(json.dumps({k: v for k, v in row.items() if k != "secs"}, sort_keys=True) + "\n")
            old = read_json(out / "specs_summary.json", {}) if partial else {}
            old = old or {}
            for mode in ("own", "realistic"):
                if ("G2" if mode == "own" else "G3") in self.selected:
                    old[mode] = self.spec_summary[mode]
            write_json(out / "specs_summary.json", old)
            timing = defaultdict(float)
            for mode in self.spec_rows:
                for r in self.spec_rows[mode]:
                    timing[r["key"]] += r.get("secs", 0.0)
            write_json(self.work / "spec_timing.json", dict(timing))
        write_json(out / "tests.json", merged("tests.json", self.data["tests"]))
        write_json(out / "audits.json", merged("audits.json", self.data["audits"]))
        write_json(out / "bench.json", merged("bench.json", self.data["bench"]))
        write_json(out / "extras.json", merged("extras.json", self.data["extras"]))
        write_json(out / "scope.json", merged("scope.json", self.data["scope"]))
        tree_now = tree_fingerprint(self.root)
        tree = tree_now if tree_now == getattr(self, "tree_start", tree_now) else None
        for result in self.results.values():
            result["tree"] = tree           # None: the tree changed while this run's checks ran
        checks = dict(old_meta.get("checks", {})) if partial else {}
        checks.update(self.results)
        statuses = {c: checks[c]["status"] for c in CHECKS if c in checks}
        failing = [c for c, s in statuses.items() if s in FAILING]
        counts = Counter(statuses.values())
        # FIX2: every check of a hand-over ran on the tree the record describes (a partial re-run merged with an
        # earlier run on another tree, or edits while the gate ran, are STALE)
        stale = [c for c in recorded if c in checks and checks[c].get("tree") != tree_now]
        incomplete = record_incomplete({"recorded": recorded, "spec_files": self.args.spec_files})
        if prev is not None and record_incomplete(read_json(prev / "meta.json", {})):
            incomplete = "; ".join(x for x in (incomplete, "previous record {} is partial or narrowed".format(
                prev.name)) if x)
        files, _ = changed_files(self.root)
        isolated = read_json(self.root / ISOLATE_MANIFEST)
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True)
        if isinstance(isolated, dict) and isolated.get("base"):
            head = subprocess.CompletedProcess([], 0, stdout=isolated["base"])       # an isolated copy's base commit
        meta = {"id": self.id, "packages": self.policy["packages"], "stage": self.policy["stage"],
                "prev": prev.name if prev else None, "prev_path": str(prev) if prev else None,
                "started": old_meta.get("started", started) if partial else started, "finished": now_iso(),
                "secs": round(time.perf_counter() - self.started, 1),
                "partial": None if recorded == list(CHECKS) else recorded, "recorded": recorded,
                "last_run": self.selected,
                "git": {"head": head.stdout.strip() if head.returncode == 0 else None, "changed": files},
                "python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count(),
                "jobs": self.jobs_n, "seeds": list(self.seeds), "camera": self.args.camera, "extras": self.extras,
                "spec_files": self.args.spec_files,
                "fingerprints": self.fingerprints or old_meta.get("fingerprints", {}), "checks": checks,
                "tree": tree_now, "tree_root": str(self.root), "tree_changed_during_run": tree is None,
                "stale": stale,
                "allowed": sorted(self.policy["allow"]), "codes": sorted(self.policy["codes"]),
                "requires": sorted(self.policy["requires"]),
                "policy": {"root": str(policy_root()), "known_failing": self.allow["note"],
                           "allowlist_entries": len(self.allow["entries"]),
                           "allowlist_expired": len(self.allow["expired"]),
                           "allowlist_invalid": len(self.allow["invalid"]), "signoff": self.signoff["note"],
                           "signed_by": self.signoff["by"]},
                "human_check": "python -m house_sim --check {} --workers 4{}".format(
                    (self.work / "all_specs.json").as_posix(), "")}
        write_json(out / "meta.json", meta)            # compare_dirs reads it (the record's own statuses)
        lines = ["tier-1 gate {}  ({})".format(self.id, meta["finished"]),
                 "packages: {}   stage: {}   previous gate: {}   seeds: {}   jobs: {}".format(
                     "+".join(self.policy["packages"]) or "none", self.policy["stage"], meta["prev"] or "none",
                     " ".join(map(str, self.seeds)), self.jobs_n),
                 "allowed differences: {}   reviewed codes: {}".format(
                     ", ".join(meta["allowed"]) or "none", ", ".join(meta["codes"]) or "none"),
                 "policy: allowlist {} ({} entries, {} expired, {} invalid); second signatures {}".format(
                     self.allow["note"], len(self.allow["entries"]), len(self.allow["expired"]),
                     len(self.allow["invalid"]), self.signoff["note"]), ""]
        for check in CHECKS:
            if check in checks:
                lines.append("{:4} {:5} {}".format(check, checks[check]["status"], checks[check]["note"]))
        compare_ok = True
        if prev:
            reviewed = getattr(self, "reviewed", None)
            if reviewed is None:
                reviewed = load_reviewed(self.root, self.out, self.policy["packages"], self.args.reviewed)
            sections, compare_ok = compare_dirs(prev, out, self.policy, reviewed, self.gates_root, root=self.root,
                                                signoff=self.signoff, allow=self.allow)
            text = render_compare(prev, out, self.policy, sections, compare_ok, incomplete)
            write_text(out / "compare.txt", text)
            write_json(out / "compare.json", {"prev": prev.name, "sections": sections, "ok": compare_ok})
            lines += ["", text]
        self.passed = not failing and compare_ok and not stale
        word = "FAIL" if failing or not compare_ok else ("STALE" if stale else ("INCOMPLETE" if incomplete else "PASS"))
        summary = "TIER1-GATE {}{}{}: {} ({})".format(
            self.id, " [{}]".format("+".join(self.policy["packages"])) if self.policy["packages"] else "",
            " vs {}".format(prev.name) if prev else " (no previous gate)", word,
            ", ".join("{} {}".format(counts[s], s.lower()) for s in ("PASS", "FAIL", "SKIP", "INFO", "N/A")
                      if counts[s]) +
            ("; failing: " + ",".join(failing) if failing else "") +
            ("; compare FAIL" if not compare_ok else "") +
            ("; this run: " + ",".join(self.selected) if partial else "") +
            ("; record incomplete: " + ",".join(recorded) if recorded != list(CHECKS) else "") +
            ("; specs limited to " + self.args.spec_files if self.args.spec_files else "") +
            ("; STALE (checks run on another tree: {}{})".format(",".join(stale), "; the tree changed while the gate "
                                                                "ran" if tree is None else "") if stale else "") +
            ("; never a hand-over" if incomplete and self.passed else ""))
        meta.update(summary=summary, verdict=word, handover=word == "PASS", compare_ok=compare_ok)
        write_json(out / "meta.json", meta)
        lines += ["", summary]
        write_text(out / "summary.txt", "\n".join(lines))
        print("\n".join(lines), flush=True)


# ----------------------------------------------------------------------------------------------------------------
# commands

def _package_names(text):
    names = [resolve_package(p) for p in re.split(r"[+,]", text or "") if p.strip()]
    if None in names:
        raise SystemExit("unknown package in {!r} (see: tier1_gate.py packages)".format(text))
    return names


def default_prev_dir(gates_root, stage, exclude=None):
    """The default previous gate of a stage (TIER1_INTEGRATION 2.4, 3.1): A / B0 -> A0, B1 -> B0, the adoption round
    -> C1; otherwise the latest complete, unnarrowed gate of an earlier stage."""
    gates_root = Path(gates_root)
    fixed = DEFAULT_PREV.get(stage)
    if fixed and (gates_root / fixed / "meta.json").exists():
        return (gates_root / fixed).resolve()
    best = None
    if gates_root.exists():
        for meta_path in gates_root.glob("*/meta.json"):
            if exclude is not None and meta_path.parent.resolve() == Path(exclude).resolve():
                continue
            meta = read_json(meta_path, {})
            if meta.get("partial") or meta.get("spec_files") or \
                    stage_index(meta.get("stage")) >= max(1, stage_index(stage)):
                continue
            key = meta.get("finished") or ""
            if best is None or key > best[0]:
                best = (key, meta_path.parent.resolve())
    return best[1] if best else None


def resolve_records(args, verb="compare"):
    """(root, gates_root, names, cur, prev, meta, policy) for compare / signoff, or an exit status (2) after
    printing how to make the missing record."""
    root = Path(args.root).resolve()
    gates_root = Path(args.gates_root).resolve() if args.gates_root else root / GATE_DIR
    names = _package_names(args.package)
    shared_tree = getattr(args, "shared_tree", False)

    def locate(name):
        path = Path(name)
        if not (path / "meta.json").exists():
            path = gates_root / gate_dirname(name)
        if not (path / "meta.json").exists():
            raise SystemExit("no gate record at {}".format(path))
        return path.resolve()

    if args.cur:
        cur = locate(args.cur)
    else:
        candidates = [gates_root / gate_dirname(n) for pid in names for n in [pid] + PACKAGES[pid]["aka"]]
        cur = next((c.resolve() for c in candidates if (c / "meta.json").exists()), None)
        if cur is None:
            pid = names[0] if names else None
            print("{}: no gate record for {} in {}.".format(verb, "+".join(names) or "this package", gates_root))
            if pid is not None:
                print("Record one first (a domain package can narrow it to its own spec files), e.g.:\n"
                      "  python tools/orchestration/tier1_gate.py record --id {} --package {}{}\n"
                      "  python tools/orchestration/tier1_gate.py record --id {} --package {} --only G0,G2,G3,G10{}"
                      "{}".format(gate_dirname(pid), "+".join(names), " --shared-tree" if shared_tree else "",
                                  gate_dirname(pid), "+".join(names),
                                  " --spec-files '{}__*'".format(pid.split(":", 1)[1]) if pid.startswith("ADOPT:")
                                  else "", " --shared-tree" if shared_tree else ""))
            if shared_tree and names:
                scope = scope_check(root, policy_for(names), gate_dirname(names[0]), True)
                print("G10  {:5} {}".format(scope["status"], scope["note"]))
                for f in scope.get("outside", []):
                    print("           outside: " + f)
            return 2
    meta = read_json(cur / "meta.json", {})
    names = names or meta.get("packages", [])
    policy = policy_for(names, meta.get("stage") if not names else None)
    if args.prev:
        prev = locate(args.prev)
    else:
        prev = None
        for candidate in (meta.get("prev_path"), meta.get("prev") and str(gates_root / gate_dirname(meta["prev"]))):
            if candidate and (Path(candidate) / "meta.json").exists():
                prev = Path(candidate).resolve()
                break
        prev = prev or default_prev_dir(gates_root, policy["stage"], exclude=cur)
        if prev is None:
            raise SystemExit("no previous gate for {} (pass PREV)".format(cur.name))
    return root, gates_root, names, cur, prev, meta, policy


def cmd_compare(args):
    """compare [PREV [CUR]] [--package PKG] [--shared-tree] [--spec-files GLOB] [--reviewed FILE]

    CUR defaults to the package's own record (gate/<PKG>, a colon written as "_"), PREV to the previous gate that
    record names, else the stage's default (the adoption round: C1); one positional names PREV. --shared-tree also
    checks the scope (G10) of the working tree now, attributing the files of the packages running concurrently in the
    same sub-stage (the adoption round: every ADOPT:<domain>, K-EVAL and K-CAM) instead of failing them. --spec-files
    narrows the spec trials of both records to the matching files (a record made with --spec-files is always compared
    on its own files only). The rules are the record's own package's: --package naming other packages than the
    record was made for fails. Exit 0 on PASS (and on INCOMPLETE: nothing failed, but the record is partial or
    narrowed, never a hand-over), 1 on FAIL, 2 when there is no record to compare."""
    if Path(args.root).resolve() != DEFAULT_ROOT and not getattr(args, "foreign_root", False):
        # FIX2: --root chose where reviewed rows, the scope and the tree came from; the main session passes
        # --foreign-root when it means it (policy files always come from policy_root())
        print("compare: --root {} is not this project ({}); pass --foreign-root to compare from another folder".format(
            Path(args.root).resolve(), DEFAULT_ROOT))
        return 2
    resolved = resolve_records(args, "compare")
    if isinstance(resolved, int):
        return resolved
    root, gates_root, names, cur, prev, meta, policy = resolved
    reviewed = load_reviewed(root, cur, names, args.reviewed)
    sections, ok = compare_dirs(prev, cur, policy, reviewed, gates_root, spec_files=args.spec_files,
                                root=policy_root(), shared_tree_scope=args.shared_tree)
    tree_section, tree_incomplete = tree_check(meta, root, getattr(args, "any_tree", False))
    sections.insert(0, tree_section)
    ok = ok and tree_section["status"] not in FAILING
    recorded_for = meta.get("packages") or []
    if recorded_for and set(recorded_for) != set(names):
        sections.insert(0, {"check": "POL", "status": "FAIL", "lines": [
            "the record was made for {}; compare it with its own package's rules, not {}".format(
                "+".join(recorded_for), "+".join(names))]})
        ok = False
    if args.shared_tree:
        scope = scope_check(root, policy, cur.name, True)
        lines = [scope["note"]] + ["outside: " + f for f in scope.get("outside", [])[:40]]
        if scope.get("attributed"):
            owners = Counter(scope["attributed"].values())
            lines.append("attributed: " + ", ".join("{} {}".format(k, v) for k, v in sorted(owners.items())))
        sections.append({"check": "G10", "status": scope["status"], "lines": lines})
        ok = ok and scope["status"] not in FAILING
    incomplete = record_incomplete(meta)
    if args.spec_files:
        incomplete = "; ".join(x for x in (incomplete, "compared on spec files " + args.spec_files) if x)
    prev_narrow = record_incomplete(read_json(Path(prev) / "meta.json", {}))
    if prev_narrow:
        incomplete = "; ".join(x for x in (incomplete, "previous record {} is partial or narrowed ({})".format(
            Path(prev).name, prev_narrow)) if x)
    if tree_incomplete:
        incomplete = "; ".join(x for x in (incomplete, tree_incomplete) if x)
    print(render_compare(prev, cur, policy, sections, ok, incomplete))
    return compare_verdict(ok, incomplete)[1]


def signoff_draft(prev, cur, policy, reviewed, by=None, root=None):
    """The second-signature file a main session would commit for record `cur` against `prev`: one row per verdict
    change (with the digest of its new verdict, whether the package reviewed it and the codes), per edited spec (its
    new spec hash) and per action the coverage fuzz no longer drives to success. Rows without the package's review
    stay unaccepted even when signed (both signatures are needed)."""
    pm, cm = read_json(Path(prev) / "meta.json", {}), read_json(Path(cur) / "meta.json", {})
    globs = spec_scope(pm, cm)
    rows, edits = [], {}
    empty = {"verdict": {}, "edit": {}, "coverage": set()}
    for mode, name in (("own", "specs_own.jsonl"), ("realistic", "specs_realistic.jsonl")):
        a = scoped_rows(read_jsonl(Path(prev) / name), globs)
        b = scoped_rows(read_jsonl(Path(cur) / name), globs)
        if not a or not b:
            continue
        res = compare_specs(a, b, mode, policy, reviewed, empty)
        for c in res["verdict_changes"]:
            rows.append({"kind": "verdict", "key": c["key"], "seed": c["seed"], "mode": mode, "digest": c["digest"],
                         "passed": c["passed"], "fields": c["fields"], "reviewed_by_package": c["reviewed"],
                         "codes": c["codes"], "detail": c["detail"]})
        edits.update(res.get("edits") or {})
    for key in sorted(edits):
        rows.append({"kind": "edit", "key": key, "spec_hash": edits[key]})
    pa, ca = read_json(Path(prev) / "audits.json", {}), read_json(Path(cur) / "audits.json", {})
    unsigned, _ = coverage_loss(pa.get("G6"), ca.get("G6"), empty)
    rows += [{"kind": "coverage", "action": action} for action in unsigned]
    return {"format": SIGNOFF_FORMAT, "gate": Path(cur).name, "prev": Path(prev).name, "signed_by": by,
            "date": _today(),
            "about": "Second signatures of gate record {} (tools/orchestration/tier1_gate.py). Drafted by `signoff`: "
                     "the main session reviews every row, deletes the rows it does not accept and commits this file; "
                     "only the committed version counts.".format(Path(cur).name),
            "rows": rows}


def cmd_signoff(args):
    """signoff [PREV [CUR]] [--package PKG] [--by NAME] [--out FILE | --write]

    Draft the main session's second signature for a record (signoff_draft). Printed, or written to --out FILE, or
    with --write to tools/orchestration/gate_policy/signoffs/<CUR>.json in the working tree (it counts only once the
    main session commits it)."""
    resolved = resolve_records(args, "signoff")
    if isinstance(resolved, int):
        return resolved
    root, gates_root, names, cur, prev, meta, policy = resolved
    reviewed = load_reviewed(root, cur, names, args.reviewed)
    draft = signoff_draft(prev, cur, policy, reviewed, by=args.by, root=root)
    target = Path(args.out) if args.out else (root / SIGNOFF_DIR / "{}.json".format(cur.name) if args.write else None)
    if target is None:
        print(json.dumps(draft, indent=1, sort_keys=True))
    else:
        write_json(target, draft)
        counts = Counter(r["kind"] for r in draft["rows"])
        print("wrote {} ({} verdict, {} edit, {} coverage row(s); review, then commit it)".format(
            target, counts["verdict"], counts["edit"], counts["coverage"]))
    return 0


def cmd_policy(args):
    """policy: the allowlist and the second-signature files as the gate reads them (committed versions only, from
    policy_root(): never from --root)."""
    root = policy_root()
    if Path(args.root).resolve() != root:
        print("note: --root {} is ignored; policy files are read from the project {}".format(
            Path(args.root).resolve(), root))
    allow = load_known_failing(root)
    print("policy root: {}".format(root))
    print("allowlist {}: {}".format(KNOWN_FAILING, allow["note"]))
    for entry in allow["entries"]:
        print("  {} {} owner {} expires {}: {}".format(entry["kind"], entry.get("test") or entry.get("catalog_id"),
                                                      entry["owner"], entry["expires"], short(entry["reason"], 120)))
    for entry in allow["expired"]:
        print("  EXPIRED {} {} owner {} expired {}".format(entry["kind"], entry.get("test") or entry.get("catalog_id"),
                                                         entry["owner"], entry["expires"]))
    for entry, why in allow["invalid"]:
        print("  INVALID {}: {}".format(entry, why))
    for entry in allow["entries"]:
        if entry.get("kind") == "skip":
            print("  (skip entries excuse removed, skipped or expected-failure tests: {})".format(entry["test"]))
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", "--name-only", "HEAD", SIGNOFF_DIR + "/"],
                             capture_output=True, text=True)
    names = [Path(line).stem for line in listing.stdout.splitlines() if line.endswith(".json")] \
        if listing.returncode == 0 else []
    print("second signatures committed in {}: {}".format(SIGNOFF_DIR, len(names)))
    for name in names:
        sign = load_signoff(root, name)
        print("  {}: {} verdict, {} edit, {} coverage row(s); signed by {}".format(
            name, len(sign["verdict"]), len(sign["edit"]), len(sign["coverage"]), sign["by"]))
    return 0


def cmd_isolate(args):
    """isolate --out DIR [--package PKG[+PKG]] [--files PATH ...] [--base REV]

    An isolated copy of the project for a gate run on a shared tree: the base commit's tree (`git archive`; nothing in
    the project is committed, checked out or changed), the project's empty directories (EMPTY_DIRS), then the
    package's changed files from the working tree and any --files (a file deleted in the working tree is deleted in
    the copy). The copy records the hash of every base file (ISOLATE_MANIFEST), so a gate run in it (`record --root
    DIR --gates-root <project gate folder>`) sees exactly those files as changed (G10). Without --package and --files
    the copy is the pristine base, the reference to compare a package's copy with."""
    import io
    import tarfile
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("{} exists and is not empty; choose a new folder".format(out))
    names = _package_names(args.package)

    def git(*cmd, text=True):
        return subprocess.run(["git", "-C", str(root)] + list(cmd), capture_output=True, text=text)
    rev = git("rev-parse", args.base or "HEAD")
    if rev.returncode != 0:
        raise SystemExit("git rev-parse failed: " + rev.stderr.strip())
    base = rev.stdout.strip()
    archive = git("archive", "--format=tar", base, text=False)
    if archive.returncode != 0:
        raise SystemExit("git archive failed: " + archive.stderr.decode("utf-8", "replace").strip())
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        members = []
        for member in tar.getmembers():
            target = (out / member.name).resolve()
            if member.issym() or member.islnk() or not str(target).startswith(str(out)):
                continue            # only plain files and folders inside the copy
            members.append(member)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(str(out), members=members, filter="data")
        else:
            tar.extractall(str(out), members=members)
    for rel in EMPTY_DIRS:
        (out / rel).mkdir(parents=True, exist_ok=True)
    rules = _ignore_rules(out)
    base_files = tree_files(out, rules)
    overlay, deleted = [], []
    picks = []
    if names:
        changed, error = changed_files(root)
        if changed is None:
            raise SystemExit("cannot list the working tree's changes: " + error)
        patterns = policy_for(names)["files"]
        picks = [f for f in changed if match_any(f, patterns)]
    picks += [Path(f).as_posix() for f in (args.files or [])]
    for rel in sorted(set(picks)):
        src, dst = root / rel, out / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            overlay.append(rel)
        elif dst.is_file():
            dst.unlink()
            deleted.append(rel)
    write_json(out / ISOLATE_MANIFEST, {
        "format": ISOLATE_FORMAT, "project": str(root), "base": base, "made": now_iso(), "packages": names,
        "overlay": overlay, "deleted": deleted, "base_files": base_files})
    print("isolated copy {} (base {}; {} base files): {} file(s) copied from the working tree, {} deleted{}".format(
        out, base[:12], len(base_files), len(overlay), len(deleted),
        " for " + "+".join(names) if names else ""))
    for rel in overlay:
        print("  + " + rel)
    for rel in deleted:
        print("  - " + rel)
    return 0


def cmd_scope(args):
    names = [resolve_package(p) for p in re.split(r"[+,]", args.package) if p.strip()]
    if None in names or not names:
        raise SystemExit("unknown package {!r}".format(args.package))
    result = scope_check(Path(args.root).resolve(), policy_for(names), args.id or names[0], args.shared_tree)
    print("G10 {} {}".format(result["status"], result["note"]))
    for f in result.get("outside", []):
        print("  outside: " + f)
    for f, owner in sorted(result.get("attributed", {}).items()):
        print("  {} (concurrent package {})".format(f, owner))
    return 1 if result["status"] in FAILING else 0


def cmd_packages(args):
    names = [resolve_package(p) for p in args.names] if args.names else list(PACKAGES)
    for pid in names:
        if pid is None:
            raise SystemExit("unknown package")
        pkg = PACKAGES[pid]
        pol = policy_for([pid])
        print("{} (stage {}{})".format(pid, pkg["stage"], "; also " + ", ".join(pkg["aka"]) if pkg["aka"] else ""))
        print("  may change: {}{}".format(", ".join(pkg["allow"]) or "nothing",
                                          "  (codes: {})".format(", ".join(pkg["codes"])) if pkg["codes"] else ""))
        print("  bench: <= {} x {}   extras: {}   requires: {}".format(
            pkg["bench"][0], pkg["bench"][1], ", ".join(pkg["extras"]) or "-", ", ".join(sorted(pol["requires"])) or "-"))
        if pkg["inert"]:
            print("  import-inert: " + ", ".join(pkg["inert"]))
        if pkg["note"]:
            print("  note: " + pkg["note"])
        for f in pkg["files"]:
            print("    " + f)
    return 0


def cmd_gen1(args):
    root = Path(args.root).resolve()
    work = Path(args.work).resolve() if args.work else Path(tempfile.gettempdir()) / "house_ai_tier1" / "gen1"
    shutil.rmtree(str(work), ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))
    entries = gen1_entries_from_specs(root) if args.write else gen1_entries(root)[0]
    spec_path = work / "entries.json"
    write_json(spec_path, entries)
    parts = max(1, args.jobs or 4)
    jobs = [Job("gen1 {}".format(i), "G5", [sys.executable, str(Path(__file__).resolve()), "_gen1", str(root),
                                            str(work / "part_{}.json".format(i)), str(i), str(parts)]
                + (["--from-specs"] if args.write else []), child_env(root), est=30.0) for i in range(parts)]
    schedule(jobs, parts, root)
    digests = {}
    for i, job in enumerate(jobs):
        if job.result["rc"]:
            print(tail(job.result["stderr"], 2000))
            return 1
        digests.update(read_json(work / "part_{}.json".format(i), {}))
    errors = {k: v for k, v in digests.items() if str(v).startswith("error:")}
    if errors:
        print("generation errors:", short(errors, 2000))
        return 1
    if not args.write:
        changed = [gen1_key(e) for e in entries if digests.get(gen1_key(e)) != e.get("digest")]
        print("generation-1 digests: {}/{} equal to {}".format(len(entries) - len(changed), len(entries), GEN1_FILE))
        for k in changed[:20]:
            print("  changed: " + k[:200])
        return 1 if changed else 0
    defaults = [dict(seed=e["seed"], digest=digests[gen1_key(e)]) for e in entries if not e["house"] and not e["specs"]]
    houses = [dict(house=e["house"], seed=e["seed"], specs=e["specs"], digest=digests[gen1_key(e)])
              for e in entries if e["house"] or e["specs"]]
    old = read_json(root / GEN1_FILE, {}) or {}
    history = list(old.get("history", [])) + [{"by": args.by, "date": now_iso()[:10], "reason": args.reason}]
    data = {"format": "house-gen1-digests-v1",
            "about": "Generation-1 house digests pinned before any tier-1 generator work (docs/design/TIER1_SWEEP.md "
                     "14.1). tests/test_generator_compat.py (GEN) must keep every entry equal; only a main-session "
                     "commit naming the round and the reason re-records this file "
                     "(python tools/orchestration/tier1_gate.py gen1-digests --write --by ID --reason TEXT).",
            "method": "digest = house_sim.kernel.predicates.digest(house_sim.generator.generate_house(seed, **house)) "
                      "with every domain loaded (HOUSE_SIM_DOMAINS unset) and the default registry; defaults: seeds "
                      "0-19 with house {}; spec_houses: every distinct `house` block of tasks/authored/*.json after "
                      "spec.expand and spec.choose_variant(spec, seed), on seeds 0, 1, 2, 41 and 97",
            "seeds": list(SEEDS), "defaults": defaults, "spec_houses": houses, "history": history}
    write_json(root / GEN1_FILE, data)
    print("wrote {}: {} default and {} spec-house digests".format(GEN1_FILE, len(defaults), len(houses)))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Tier-1 gate (docs/design/TIER1_INTEGRATION.md section 3)")
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="project root to check (default: this repository)")
    p.add_argument("--gates-root", help="folder holding gate records (default: <root>/" + GATE_DIR + ")")
    sub = p.add_subparsers(dest="cmd")
    for name in ("record", "run"):
        r = sub.add_parser(name, help="run the gate and record it")
        r.add_argument("--id", required=True, help="gate id: a package id (PHYS-INT), a stage (B0) or a free label")
        r.add_argument("--package", help="package id(s) whose rules apply, joined with + (default: --id if it is one)")
        r.add_argument("--stage", help="stage for a main-session gate id that is not a package (default: the id)")
        r.add_argument("--prev", help="previous gate id or folder, or 'none' (default: the latest recorded gate)")
        r.add_argument("--extras", help="comma list of physics,camera,body,sweep,gen (default: the package's)")
        r.add_argument("--camera", choices=("room", "geometric"), help="camera mode for forced-realistic runs and tools")
        r.add_argument("--only", help="comma list of checks to run (results of the others are kept)")
        r.add_argument("--skip", help="comma list of checks to skip")
        r.add_argument("--jobs", type=int, help="parallel slots (default: cpus - 1)")
        r.add_argument("--seeds", type=int, nargs="+", help="spec seeds (default 0 1 2 41 97)")
        r.add_argument("--shared-tree", action="store_true",
                       help="G10: attribute files of concurrent packages of the same sub-stage (the adoption round: "
                            "every ADOPT:<domain>, K-EVAL, K-CAM) instead of failing")
        r.add_argument("--reviewed", help="JSON list of reviewed differences (in addition to the handoff table)")
        r.add_argument("--bench-repeats", type=int, default=5)
        r.add_argument("--chunk-specs", type=int, default=3, help="specs per spec-trial worker")
        r.add_argument("--spec-files", help="only spec files matching this glob, e.g. 'cleaning__*' (iteration aid; "
                                            "a record made with it is partial)")
        r.add_argument("--test-timeout", type=int, default=3600)
        r.add_argument("--chunk-timeout", type=int, default=2400)
        r.add_argument("--out", help="output folder (default: <gates-root>/<id>)")
        r.add_argument("--work", help="scratch folder (default: <tmp>/house_ai_tier1/<id>)")
    c = sub.add_parser("compare", help="compare two recorded gates (default: the package's record with its previous "
                                       "gate)")
    c.add_argument("prev", nargs="?", help="previous gate id or folder (default: the one CUR names, else the stage's)")
    c.add_argument("cur", nargs="?", help="gate id or folder to check (default: the package's own record)")
    c.add_argument("--package", help="package id(s) whose rules apply, joined with + (default: CUR's)")
    c.add_argument("--reviewed")
    c.add_argument("--shared-tree", action="store_true",
                   help="also check the working tree's scope (G10), attributing concurrent packages' files")
    c.add_argument("--spec-files", help="compare only the spec trials of these spec files, e.g. 'cleaning__*'")
    c.add_argument("--any-tree", action="store_true",
                   help="do not check that the record was made on its tree as it is now (historical comparisons; "
                        "never a hand-over)")
    c.add_argument("--foreign-root", action="store_true",
                   help="allow a --root other than this project (main session only)")
    s = sub.add_parser("scope", help="G10 only")
    s.add_argument("--package", required=True)
    s.add_argument("--id")
    s.add_argument("--shared-tree", action="store_true")
    i = sub.add_parser("isolate", help="make an isolated copy (base commit + a package's changed files) for a gate run")
    i.add_argument("--out", required=True, help="new (or empty) folder for the copy")
    i.add_argument("--package", help="package id(s) whose changed files are copied from the working tree")
    i.add_argument("--files", nargs="+", help="further working-tree files to copy (or delete) into the copy")
    i.add_argument("--base", help="base revision (default HEAD)")
    k = sub.add_parser("packages", help="print the package table")
    k.add_argument("names", nargs="*")
    o = sub.add_parser("signoff", help="draft the main session's second signature for a record (verdict changes, "
                                       "edited specs, coverage losses)")
    o.add_argument("prev", nargs="?", help="previous gate id or folder (default: the one CUR names)")
    o.add_argument("cur", nargs="?", help="gate id or folder (default: the package's own record)")
    o.add_argument("--package", help="package id(s), joined with + (default: CUR's)")
    o.add_argument("--reviewed")
    o.add_argument("--by", help="who signs (the main session)")
    o.add_argument("--out", help="write the draft here (default: print it)")
    o.add_argument("--write", action="store_true", help="write it to " + SIGNOFF_DIR + "/<CUR>.json (counts once "
                                                         "committed)")
    sub.add_parser("policy", help="print the committed allowlist and second signatures as the gate reads them")
    g = sub.add_parser("gen1-digests", help="check (default) or --write tests/data/gen1_digests.json")
    g.add_argument("--write", action="store_true")
    g.add_argument("--by", default="A0")
    g.add_argument("--reason", default="initial recording (TIER1_SWEEP 14.1, sweep Phase 0)")
    g.add_argument("--jobs", type=int)
    g.add_argument("--work")
    return p


def main(argv):
    if argv and argv[0].startswith("_"):
        name, rest = argv[0], argv[1:]
        if name == "_unittest":
            return worker_unittest(rest[0], rest[1])
        if name == "_trials":
            worker_trials(rest[0], rest[1])
        elif name == "_gen1":
            if "--from-specs" in rest:
                rest = [a for a in rest if a != "--from-specs"]
                _quiet_imports(rest[0])
                entries = gen1_entries_from_specs(rest[0])
                worker_gen1_entries(rest[0], rest[1], int(rest[2]), int(rest[3]), entries)
            else:
                worker_gen1(rest[0], rest[1], int(rest[2]), int(rest[3]))
        elif name == "_inert":
            worker_inert(rest[0], rest[1], rest[2:])
        elif name == "_info":
            worker_info(rest[0], rest[1])
        elif name == "_benchwait":
            worker_bench_wait(rest[0], rest[1], [int(s) for s in rest[2].split(",")], int(rest[3]))
        elif name == "_gentiming":
            worker_gen_timing(rest[0], rest[1], int(rest[2]))
        else:
            raise SystemExit("unknown worker " + name)
        return 0
    args = build_parser().parse_args(argv)
    if args.cmd in ("record", "run"):
        return Gate(args).run()
    if args.cmd == "compare":
        return cmd_compare(args)
    if args.cmd == "scope":
        return cmd_scope(args)
    if args.cmd == "isolate":
        return cmd_isolate(args)
    if args.cmd == "packages":
        return cmd_packages(args)
    if args.cmd == "gen1-digests":
        return cmd_gen1(args)
    if args.cmd == "signoff":
        return cmd_signoff(args)
    if args.cmd == "policy":
        return cmd_policy(args)
    build_parser().print_help()
    return 2


def worker_gen1_entries(root, out_path, part, parts, entries):
    """Like worker_gen1, for an explicit entry list (used when recording)."""
    from house_sim.generator import generate_house
    from house_sim.kernel.plugins import load_domains
    from house_sim.kernel.predicates import digest
    load_domains()
    result = {}
    for entry in entries[part::parts]:
        try:
            result[gen1_key(entry)] = digest(generate_house(entry["seed"], **entry.get("house", {})))
        except Exception as exc:                              # noqa: BLE001
            result[gen1_key(entry)] = "error:{}: {}".format(type(exc).__name__, short(str(exc), 200))
    write_json(out_path, result)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
