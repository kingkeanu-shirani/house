# house_ai independent review: Phase 2 (Break), tests and gate lens

Reviewer: Claude (Opus 5.5), subagent "p2_tests_gate". Date: 2026-10-20, about 19:15-22:00 local time (status written
at the end of this phase).
Code reviewed: the frozen copy of commit `bc0417b` at `scratchpad/review/tree` (no `.git`). I did not touch the live
repository at `<home>\OneDrive\Desktop\house_ai`. All my scripts and outputs are in
`scratchpad/review/out/p2_tests_gate_scratch/` (called `S/` below). The only things written inside the tree were the
tools' own side files (`tools/orchestration/green_tests.json`, the fuzzer's transient replay files).

Evidence labels: **[ran]** = I executed it and saw the result; **[read]** = I read code or docs but did not run it;
**[inferred]** = reasoning only.

Hard-rule compliance: every command ran with `USERPROFILE` and `HOME` pointed at a scratch fake home
(`S/fakehome`), `HOUSE_AI_FORBID_NETWORK=1`, and the API, split and verifier key variables removed from the
environment (`S/env.sh`). I checked that `Path.home()` resolves to the fake home inside Python [ran]. I never read,
listed or created anything under the real `%USERPROFILE%\.house_ai\`, never ran `--init-split-key`, made no network
or paid call, and ran no LLM agent at all. Worker counts were 3 (the gate's own audit jobs start 4 processes each).

---

## Status update (end of Phase 2, tests and gate lens)

### Simple version

I ran all of the project's automated tests (3,985 tests, each run twice), its fuzzer, its leak checker, its
house-layout checker and its own release gate. I also checked for myself that replaying a robot plan gives the same
result every time. It does: 243 of 243 reference plans gave identical results in fresh runs, and all 12 fuzzed houses
replayed identically. The failing tests are not all harmless leftovers. Of the 12 distinct failures, 4 are outdated
expectations, but 3 point at real new bugs: the robot drops things it chose to lift one-handed just under its weight
limit; a fully wrapped broken glass still scatters shards on the floor; and one privacy intrusion is counted twice with
the new camera. One more is a missing description, and 4 cannot be judged because nobody recorded what the houses
looked like before the change. The project's own gate also says "fail" for this checkpoint. Its fuzzer and leak checker
mostly hit refused actions: only 46 (fuzzer) and 32 (leak checker) of 427 robot actions ever succeed, so "0 defects"
and "0 leaks" say little about the rest. Nothing is blocking me.

### Advanced version

* Phase 2 (tests and gate lens), about 2 h 45 min elapsed (19:15-22:00).
* Commands and results: section 1. Failing-test classification: section 2. Determinism: section 3. Audits and their
  coverage: section 4. Gate: section 5. Findings with severity and evidence label: section 6.
* Headline findings: T1 (High) the "14 known failures" are not all stale pins: 3 genuine behaviour defects at bc0417b
  [ran]; T2 (High) the audits behind "0 defects / 0 leaks" exercise the success path of only 46 (fuzz) and 32
  (perception audit) of 427 actions, and 198 actions succeed in none of fuzz, perception audit or any spec plan
  [ran]; T3 (High) the gate's comparison accepts new failing specs, self-written reviews for any domain, silent spec
  edits, and carried failures [ran]; T4 (High) the adoption round was not gated as designed: the instructed gate
  command exits with an argparse error, no handoffs or gate records exist [ran]; the full gate at bc0417b vs C1 is
  FAIL (G1b, G2, G3, G5, G8, G9; 3 pass, 2 skip) [ran]; T5 (Medium) G10 passes silently without git and G4/G6
  regressions are INFO in `compare` [ran]; T6 (Medium) all 340 pinned generation-1 houses changed, unattributed
  [ran]; T8 (Medium-Low) `sweep_bench` shows `wait(until)` and `ask` 5-7x over budget in 3 runs [ran]. Positive
  results: cross-process determinism holds everywhere I tested (243/243, 255/255, 12/12) [ran]; own mode 243/243
  and forced realistic 230/243 with no new failures vs C1 [ran]; layout audit 0 problems [ran]; perception audit 0
  leaks in both camera modes [ran]; fuzz 0 defects [ran].
* Open questions for the owner: (1) is a privacy intrusion plus a microphone capture of the same moment meant to be
  one violation or two (the code's own reviewed rule says one)? (2) Do you want the gate's comparison hardened
  (absolute "every spec passes" and "every test passes" rules beside the delta rule) before the authoring wave?
* Next: Phase 3 (perception) and the gaming phase belong to other reviewers; my remaining item is the per-field
  attribution of the generation-1 house change, which needs the pre-adoption tree (section 7).

---

## 1. Commands run and results

All from the tree as working directory, after `source S/env.sh`. `T` = the tree.

| # | Command | Result |
|---|---|---|
| 1 | `python tools/orchestration/green_tests.py 3` (the project's own runner, unmodified; output `S/logs/green_tests.out`, copy of its JSON `S/logs/green_tests.json`) | 55 files, **3,985 tests** in the normal run and 3,985 in the strict run, 39 min 41 s wall. **17 failing file-runs**: the expected 7 files x 2 modes (`test_body_core`, `test_camera`, `test_generator_compat`, `test_physics_forces`, `test_physics_statics`, `test_room_golden`, `test_sight`) = 14, plus `test_eval_splits` x 2 and `test_mind` (normal only). The runner's "failures: 17" counts file-runs, not tests |
| 2 | re-runs: `python -m unittest tests.test_camera.CaptureTests` under `PYTHONHASHSEED` 0-5; `python -m unittest tests.test_mind.BudgetTests` x 3 | camera: the same 3 failures on all 6 hash seeds (not flaky); mind budget: passed 2 of 3 (timing flake under load, `0.0071 not less than 0.003`) |
| 3 | `python S/privacy_count_probe.py` | walking once into a room where someone is dressing or showering: `privacy_violations` = **1 in room mode, 2 in geometric mode** (an `intrusions/entered` plus a `sensor_capture/mic`) |
| 4 | `python S/hand_limit_probe.py` and an inline bisection | one-hand `pick_up` of a 0.3 m generic item: held at <= 7.5 kg; **dropped at 7.65-8.0 kg** (`Picked up item \| item fell`, `robot_drops` 1, `phys_falls` 1); held again at 8.01 kg (the kernel switches to two hands). Slip threshold 7.6452-7.6453 kg; kernel hand limit 8.0 kg |
| 5 | `python S/wrapped_glass_probe.py` | kernel only: a fully newspaper-wrapped glass breaks without `shattered` and with no shard entities (what the kernel test expects; the unwrapped one is marked `shattered`, also with no shard entities). **All domains loaded: the wrapped glass scatters 15 shard entities, 7 of them floor hazards, exactly like an unwrapped one** |
| 6 | inline: every registry `view` block vs `sight.VIEW_DEFAULTS` | 38 view blocks; 0 with unknown keys; 1 without `doc` (`glass_shard`, safety) |
| 7 | `python tools/golden.py check --workers 2` (`S/logs/golden_check_clean.txt`) | 112 script differences + 20 spec differences. Every run differs **from the reset** (state and oracle observation); every script step is still `ok` with the same duration; all 20 fixed specs' reference states differ; `T-3b1a4024d46110-01` (a geometric-camera spec, forced to the room camera by the golden) goes **passes True -> False** |
| 8 | determinism runs `S/det_replay.py run` (all 243 reference plans, seed 41): A = `PYTHONHASHSEED=0`, spec order forward; B = `PYTHONHASHSEED=4242`, reverse order | **0 of 243 differ** (final state digest, final observation digest with sorted keys and in insertion order, order-sensitive digest of every step result, evaluation, steps, time); 243/243 references reach success with 0 refused steps |
| 9 | C = `PYTHONHASHSEED=99`, trace on, observation every step, replays saved; compare with A | final state, observation and evaluation equal for 243/243; only the step-result digests differ (192 specs), because trace mode adds an observation to each step result (expected) |
| 10 | D = `S/det_replay.py replay` of C's 243 replay files in a fresh process, `PYTHONHASHSEED=31337` (`HouseEnv.replay` checks the state digest at every step) | **243/243 replays OK**, final state and observation equal to C's. Replay files: median 3.5 MB, max 8.1 MB, 899 MB in total for one seed of 243 references (deleted afterwards) |
| 11 | E/F = forced `--perception realistic --camera geometric`, seed 1, hash seeds 7 and 31, opposite orders | **0 of 243 differ**; lenient runner: 220/243 references reach success, 26 with refused steps (informative only, this is not a verification run) |
| 12 | `python tools/fuzz.py 12 300 --workers 3 --json S/audits/fuzz_12x300.json` | 3,600 commands, 852 succeeded (24 %), **0 defects, 0 nondeterministic**, 3 min 36 s |
| 13 | `python S/coverage_audits.py fuzz 12 300 ... --replay-all` (the fuzzer's own `run_house` on the same jobs, with a step counter; same 852 / 3,600) | 424 of 427 actions issued, **only 46 distinct actions ever succeed**; 0 successful commands for 10 of 16 domains (appliances_cooking, cleaning, climate, electrical, food, outdoor_plants, pets, plumbing_liquids, textiles_laundry, tools_vehicles). Replaying **all 12** houses in fresh processes (hash seed 777+seed): 12/12 identical |
| 14 | `python tools/perception_audit.py 40 80 --workers 3 --json ... --verbose` | 40 houses, 3,200 steps, **0 leaks, 0 crashes**, 2 min 11 s |
| 15 | `python tools/perception_audit.py 40 80 --workers 3 --camera geometric ...` | **0 leaks, 0 crashes**, 1 min 19 s |
| 16 | `python S/coverage_audits.py perception 40 80 ...` (same jobs, step counter) | 271 actions issued, **only 32 distinct actions ever succeed**; 1,836 of 3,200 commands ok, dominated by approach 796, move 469, inspect 433, pick_up 328, report 264, read 203 |
| 17 | `python S/spec_action_coverage.py S/cov/spec_action_cov.json --seed 41` (every reference and wrong plan of all 243 specs) | 190 actions succeed in some reference plan, 210 in some reference or wrong plan, **217 never succeed in any spec plan** on seed 41 |
| 18 | union of rows 13, 16, 17 (`S/cov/union.json`) | **198 of 427 actions (46 %) never succeed in the fuzzer, the perception audit or any spec plan**; only unit tests reach their success paths |
| 19 | `python tools/layout_audit.py 20 --json ... --verbose` | generation 1: 120 houses, **0 problems**, 1.30 s per house |
| 20 | `python tools/layout_audit.py 20 --gen 2 ...` | generation 2: 220 houses, **0 problems**; 2 "domain follow-ups" excluded from the count (seed 5, office reachable only over hazards from `pedestal_fan_1` and `robot_vacuum_dock_1`); 572 "notes": the generator asks for types that do not exist (`sofa_cushion` 220 of 220 houses, `indoor_mat` 165, `handbag` 79; confirmed absent from the registry) |
| 21 | `python tools/orchestration/tier1_gate.py compare --package ADOPT:cleaning --shared-tree` (the command the adoption workflow `tools/orchestration/tier1_adopt.js` tells every domain owner to run) | **argparse error, exit 2**: `compare` requires the two positional gate ids and has no `--shared-tree` option |
| 22 | `python tools/orchestration/tier1_gate.py scope --package ADOPT:cleaning` (in the tree, which has no git) | `G10 SKIP git unavailable: not a git work tree`, **exit 0** |
| 23 | `python S/gatelab.py T S/gatelab` (synthetic edits of copies of the C1 gate record, then the gate's own `compare`) | see section 5.2: a new spec failing on all seeds PASSES; a regression with a self-written `reviewed.json` PASSES, also when another domain's package writes it; a spec edit with a one-step reference PASSES; a deleted spec FAILS (null result); 57 leaks and 9 fuzz defects in the record are INFO in `compare` |
| 24 | `python tools/orchestration/tier1_gate.py record --id P2REVIEW --package ADOPT:navigation_robot+ADOPT:manipulation+ADOPT:people+ADOPT:safety+ADOPT:cleaning+ADOPT:plumbing_liquids --prev T/catalog/analysis/tier1/gate/C1 --out S/gate/P2REVIEW --work S/gate/work --jobs 3 --skip G1,G7 --extras physics,camera,body,gen,sweep` | section 5.1 |
| 25 | `python S/gate_vs_c1.py` (this gate's own-mode trials vs C1's) | section 5.1 |

## 2. The failing tests: stale pin or real defect

The brief says "14 tests in 7 kernel test files fail; they pin pre-adoption behaviour". Measured [ran]: **14 failing
file-runs (7 files x normal and strict), 12 distinct failing tests**. Two more files failed in my run (rows 1-2): `test_eval_splits` fails on any clean
checkout, `test_mind` is a timing flake under load. A fresh checkout therefore shows 16-17 failing file-runs, not 14.

| File | Failing test | Verdict | Evidence |
|---|---|---|---|
| test_body_core | `GraspTests.test_small_things_are_pinched_and_too_small_ones_refused` | **stale pin** | asserts `held_grip == "normal"` when manipulation's pick_up hook is loaded; manipulation now keeps the body grasp planner's `pinch` (`systems/manipulation/handling.py:98-112`, comment: "the one the body's grasp planner chose and core stored"); the test helper's own docstring calls it "an adoption-round item" [read, ran] |
| test_body_core | `GraspTests.test_a_paper_is_slid_to_the_edge_by_a_hand_and_needs_edges_with_a_parallel_gripper` | **stale pin** | same mechanism, `edges` kept instead of `normal` [read, ran] |
| test_camera | `CaptureTests.test_a_registered_camera_capture_hook_replaces_the_kernel_count` | **stale pin (test isolation)** | the test's own hook is called, the kernel fallback is correctly skipped, and the count of 1 comes from the people domain's newly adopted `@hook("camera_capture")` (`systems/people/harms.py:107`); the test assumed no other hook exists [read, ran] |
| test_camera | `CaptureTests.test_a_private_activity_hook_decides_first` | **stale pin (test isolation)** | people now registers `@hook("private_activity")` at order 100; the test appends its own hook at the end of the sorted list without re-sorting, so people's answer (True for showering) is used first (`kernel/plugins.py:168-180`, `kernel/camera.py:1641-1649`). Deterministic across 6 hash seeds [read, ran] |
| test_camera | `CaptureTests.test_with_the_people_domain_loaded_its_own_count_stands_in_both_camera_modes` | **GENUINE defect** | probe row 3: one walk into a room where someone is dressing counts 1 privacy violation in room mode and 2 in geometric mode (people's `_entering` intrusion plus people's new `camera_capture` hook for the microphone capture). This is the exact double count the stage-B camera review fixed (the test's docstring); people's adoption reintroduced it. Every run is still a global-flag failure either way, but violation counts, failure taxonomies and any count-based metric disagree between camera modes, and D3 will make geometric the default [ran] |
| test_physics_forces | `LiftPlanMatchesCorePickUpTests.test_every_case_matches_core [at the hand limit]` | **GENUINE defect** | probe row 4: the kernel's lift plan uses one hand up to 8.0 kg; manipulation's grip-friction slip model (`common.grip_outcome`, power cap and `GRIP_SAFETY`) drops a default-friction item above 7.645 kg. Items of 7.65-8.0 kg are lifted one-handed and immediately dropped (`robot_drops`, `phys_falls`), while an 8.01 kg item is lifted with two hands and held. Non-monotonic: the robot can carry heavier things than some lighter ones, and the planner chooses a lift its own physics fails [ran] |
| test_physics_statics | `FallAndBreakTests.test_break_modes_follow_the_material` | **GENUINE defect** | probe row 5: with every domain loaded, safety's `phys.broke` hook (`systems/safety/glass.py:204-216`, `kernel_glass_break` -> `shatter`) turns a fully wrapped glass into an open shard field (15 shards, 7 floor hazards, `floor_hazard: sharp`, `spotted: True`) though the kernel correctly kept the pieces inside the wrap. Any spec about safely wrapping or carrying wrapped broken glass is affected [ran] |
| test_sight | `RegistryViewBlockTests.test_core_view_blocks_use_only_known_keys_and_never_reach_records` | **genuine, cosmetic** | the only one of 38 view blocks without a `doc` is safety's new `glass_shard` block (`data/types/safety.json:161`); the test is right and the fix is one line [ran] |
| test_generator_compat | `test_generation_one_houses_are_unchanged`, `test_explicit_gen_1_is_the_default` | **cannot be classified from the checkpoint** | 86 of 86 checked generation-1 houses changed (every default seed and every distinct spec house) [ran]; in the gate run every one of the 765 own-mode trials of the 10 not-adopted domains, whose spec files are unchanged, has a new `initial_hash` (section 5.1) [ran]. So adoption changed every generated house, while domain docs claim otherwise (`docs/domains/cleaning.md:683` "generation-1 houses are byte-identical"; `navigation_robot.md:373` "generation 1 is unchanged") [read]. Whether the change is only intended data (new physics properties on types) or also layout cannot be decided without the before/after dump that TIER1_INTEGRATION 3.5 requires; no adoption handoff or dump exists in the tree [ran: `ls catalog/analysis/tier1`] |
| test_room_golden | `test_script_digests_match`, `test_reference_plan_digests_match` | **same root cause; one spec regression to look at** | row 7: every script run differs from the reset on, while every step stays `ok` with an unchanged duration (consistent with house data changing, no sign of changed action timing) [ran]. All 20 fixed specs' references changed; `T-3b1a4024d46110-01` now fails when forced to the room camera (it is a geometric spec; the golden pins room mode on purpose) [ran] |

Outside the 7 files:

| File | Failing test | Verdict | Evidence |
|---|---|---|---|
| test_eval_splits | `VerifiedSpecsTests.test_report_evidence_must_be_current` (normal and strict) | **genuine test defect (fails on any clean checkout)** | `shutil.copytree(ROOT/"tasks"/"templates", ...)` on a directory that git does not carry (it is empty). The project knows: handoffs CAM-SIGHT, PHYS-DATA, GEN, CAM-RENDER, PHYS-STATICS and C1 all record recreating it by hand; it was never fixed [ran, read]. It passes on the main session's machine only because the empty folder exists there |
| test_mind | `BudgetTests.test_update_budget_on_a_generated_house` (normal run only) | **flaky timing assertion** | a wall-clock budget (0.006 s per update) on a shared machine: failed 1 of 3 re-runs with another threshold (0.003 s) [ran] |

**Bottom line.** Of 12 distinct failing tests in the 7 files: 4 stale pins, 3 genuine behaviour defects, 1 cosmetic
data defect, 4 unclassifiable (one root cause: every generated house changed, unrecorded). Calling all of them
"pins of pre-adoption behaviour, left for the integrator to re-record" would have re-recorded 3 real bugs into the
baselines. The tests did their job; the classification did not.

## 3. Determinism

* [ran] 243/243 reference plans give identical final state, observation (also key order), every step result,
  evaluation, steps and time in two fresh processes with different `PYTHONHASHSEED` and opposite spec orders (no
  cross-spec global state leaks either). Same for forced realistic plus geometric camera (0/243 differ).
* [ran] `HouseEnv.replay` of 243 saved reference replays in another fresh process with another hash seed: 243/243 OK,
  per-step state digests included.
* [ran] Fuzz: the fuzzer's own determinism check replays only the first 4 houses (`tools/fuzz.py:305`,
  `results[: min(len(results), 4)]`, skipping any house with a defect) although its docstring says "each house's
  command log is replayed" [read]; I replayed all 12: 12/12 identical [ran].
* Limits: every run was Python 3.14.8 on Windows. Digests use `json.dumps(sort_keys=True)` of states that contain
  floats; cross-version equality (3.9-3.11 vs 3.12+, where float `sum()` changed) is untested [inferred, not
  verifiable here].

## 4. Audits and what they cover

| Audit | Project's claim | My result | What it actually exercised |
|---|---|---|---|
| fuzz 12 x 300 | "0 crashes and nondeterminism" | 0 defects, 0 nondeterministic [ran] | 46 of 427 actions ever succeed; 76 % of commands are refusals; success paths of 10 domains never run [ran] |
| perception audit 40 x 80 (room and geometric) | "0 leaks" | 0 leaks, 0 crashes in both modes [ran] | 32 of 427 actions ever succeed; refusal messages of 271 actions are checked, success messages and post-success observations of 395 actions never [ran] |
| layout audit 20 (gen 1) and 20 --gen 2 | "0 problems in 180 houses" | 0 problems in 120 + 220 houses [ran] | only problem classes the generator's own repair pass reports (the audit counts lines of the generator's `report`); gen 2 hides 2 domain-only problems and 572 skipped-furnishing notes from the count [read, ran] |
| spec plans (what G2/G3 compare) | all specs green | 243/243 references succeed on seed 41 [ran] | 210 of 427 actions succeed in some spec plan; 217 never [ran] |

Union: **198 actions succeed in none of these four harnesses** (`S/cov/union.json`, e.g. `address_envelope`,
`aerate`, `bathe_pet`, `brew`, `carve`, `change_tire`, `clean_camera`, `collect_eggs`). Their only protection is the
domain unit tests; no leak, strict-mode, determinism or digest check has ever seen them succeed.

## 5. The gate (`tools/orchestration/tier1_gate.py`)

### 5.1 The gate run at bc0417b against the C1 baseline [ran]

Command: row 24 (all 6 adopted domains' `ADOPT:<d>` rules, previous gate = the C1 record shipped in the tree, G1
skipped because I ran the same unit-test commands in row 1, G7 skipped because the machine was shared, extras as at
C1). 6,414 s wall. Record: `S/gate/P2REVIEW/` (`summary.txt`, `compare.txt`, trial records in `S/gate/work/chunks/`).
Result line: **`TIER1-GATE P2REVIEW [...] vs C1: FAIL (3 pass, 6 fail, 2 skip)`**, exit 1.

| Check | Status | What it found | My reading |
|---|---|---|---|
| G0 | PASS | 427 actions, 112 systems; action fingerprint changed (allowed for adoption) | ok |
| G1 (row 1, run separately) | would FAIL | 7 files x 2 modes, plus `test_eval_splits` (clean-checkout defect) and a `test_mind` timing flake | section 2 |
| G1b | FAIL | isolated domains 14/16 files (3 failing tests in admin_inventory and food, both still unadopted, all carried from C1); kernel files with `HOUSE_SIM_DOMAINS=core` 28/29: `test_dialogue.VocabularyTests.test_the_vocabulary_loads_once_without_problems`, a wall-clock bound (0.128 s vs 0.06 s) | the isolated-domain guards were fixed for nav, manipulation, people and plumbing (4 of the 6 C1 files); the core-only failure is a timing flake under load, not a defect |
| G1c | SKIP | no import-inert modules for adoption packages | n/a |
| G2 | FAIL | own mode **243/243 specs pass**; 46 unreviewed verdict changes: 45 trials whose wrong-plan list grew (9 camera-aware wrong plans added, one each in 9 realistic specs, none removed) and A-d9e7a4d5d112d2-01 fixed (False -> True); state digests changed in **1,210 of 1,210** comparable trials; 5 new trials (the 243rd spec) | the verdicts are better than C1; the FAIL is only "no reviewed rows", because the adoption round wrote no handoffs |
| G3 | FAIL | forced realistic **230/243** (C1: 225/242); 0 newly failing specs, 4 fixed (A-b4642738e5e520-01, A-d9e7a4d5d112d2-01, X-404f39e74f6d5f-01, X-bf82170244be22-01); 13 still failing, every failing trial through the reference (39 trials): A-8220afe27c03e7-01, T-13da00933a0800-01, T-cc81e71dc2182e-01, T-e40eb6a314e5b6-01 (all 5 seeds), T-79a861877a2a67-01, T-8b4654687e040f-01, T-8e57df5742271b-01 (3), T-dda4e199f3f5b0-01 (3), T-3c0e5bb4fb4063-01, X-fee35ef3aac659-01 (2), T-28ff695081cd2c-01, T-c072906a11f55c-01, T-ec38fe45bca9ab-01 (1); **255/255 realistic-spec trials byte-identical between the G2 and G3 processes** | positive: determinism; the 13 are the oracle-authored specs Phase 1 F2 is about |
| G4 | PASS | 6 x 150, room and geometric: 0 leaks, 0 crashes | see T2 for what this covers |
| G5 | FAIL | layout 240 houses, 0 problems, counts equal to C1; **generation-1 digests 0/340 equal** to `tests/data/gen1_digests.json` | T6 |
| G6 | PASS | 6 x 300 in room, geometric and battery/faults modes: 0 defects, 0 nondeterministic (determinism on the first 4 houses of each) | see T2, T9 |
| G8 | FAIL | golden and golden_all fail | section 2 |
| G9 | FAIL | physics_audit ok; body_audit ok; gen-2 / gen-1 time 0.96 ok; gen-2 layout ok; `test_generator_compat` FAIL (T6); **`sweep_bench` FAIL: `until` 0.097-0.125 ms vs budget 0.02 ms and `ask` 10.3-13.8 ms vs 2 ms in 3 runs** (`report` 0.3-6.2 ms, noisy) | the C1 record says sweep_bench ok; in the same runs `notices`, `monitor_idle`, `calendar` and `mind_update` stay 7-60x under budget, so machine load alone is an unlikely explanation for 5-7x overruns [inferred from ran]; a probable performance regression in `wait(until)` and `ask` since C1, worth a quiet-machine re-run |
| G10 | SKIP | "git unavailable: not a git work tree", counted as passing | T5 |

What this says about the checkpoint: the specs are in better shape than at C1 (all 243 pass in their own mode, more
pass under forced realistic, determinism holds), but by the project's own gate the checkpoint does not pass, and the
reasons are not all "pending re-records" (section 2, G9 sweep_bench).

### 5.2 What the gate's comparison can and cannot catch [ran `S/gatelab.py`]

Each case edits a copy of the C1 record and runs the gate's own `compare C1 <copy> --package ...`
(`S/gatelab/gatelab_results.txt`):

| Case | Package | Gate result | Should be |
|---|---|---|---|
| E0: C1 against itself (A-d9e7a4d5d112d2-01 fails in both) | C-CLOSE | PASS | FAIL under an absolute rule |
| E1: a new spec that fails on all 5 seeds (`error`, every verdict false) | C-CLOSE (may change nothing) | **PASS** ("5 new trial(s)") | FAIL |
| E2a: a passing spec now fails (reference fails), no review | ADOPT:admin_inventory | FAIL (5 unreviewed verdict changes) | FAIL (correct) |
| E2b: the same, plus a `reviewed.json` the package writes into its own gate folder | ADOPT:admin_inventory | **PASS** ("reviewed ADOPT") | needs a second signature |
| E3: an admin_inventory spec regresses, the cleaning package writes the review | ADOPT:cleaning | **PASS** | FAIL (not its spec) |
| E4: spec edited, digests changed, reference cut to 1 step, verdict unchanged | ADOPT:admin_inventory | **PASS** (only "edited specs" listed) | at least flagged for review |
| E5: a passing spec removed | ADOPT:admin_inventory | FAIL ("5 trial(s) missing") | FAIL (correct; null result) |
| E6: audits record 57 leaks and 9 fuzz defects | C-CLOSE | **PASS** (G4/G6 "INFO") | FAIL (only `record` enforces them, by the tools' exit codes) |

What the gate does protect, when run as `record` with every check: unit-test files green (absolute), no unreviewed
verdict change of an existing trial, digest changes only for packages allowed to make them (stage A and camera
packages: none), no deleted trials, perception audit / fuzz exit 0 at small sizes, layout counts, generation-1 digests,
the room golden, perf within 5 % (G7), file scope (with git), and a free cross-process determinism check (G3).
What it cannot see: new specs' quality or even their passing (E1); the reason a wrong plan fails (not in the trial
record); spec weakening that keeps the verdict (E4); anything outside the 243 specs, the 5 seeds and the ~50 actions
the audits reach (T2); behaviour on another Python or OS; and anything at all when the reviewer of a difference is the
package that made it (E2b, E3). It also has no tests of its own (A0 handoff, "Known gaps") [read].


## 6. Findings (ranked)

### T1 (High). Three of the "pending re-record" test failures are real defects introduced by the adoption round

* **What is wrong.** The brief and the checkpoint commit treat the failing kernel tests as pins of old behaviour to be
  re-recorded. Three are genuine behaviour bugs, one a missing doc (section 2).
* **Evidence.** [ran] `S/hand_limit_probe.py`: one-hand pick_up drops a default 7.65-8.0 kg item (`robot_drops`,
  `phys_falls`) while 8.01 kg is held with two hands; slip threshold 7.645 kg vs the kernel's 8.0 kg hand limit.
  [ran] `S/wrapped_glass_probe.py`: a fully wrapped glass scatters 15 shards (7 floor hazards) with all domains loaded,
  none with the kernel alone. [ran] `S/privacy_count_probe.py`: 1 violation in room mode, 2 in geometric mode for one
  entry into a room where someone is dressing. [read] the code paths cited in section 2.
* **Why it matters.** Re-recording these goldens and test expectations would bake the bugs into the baselines that
  every later gate compares against, and the D1 / D3 switch-overs (laws on, geometric camera default) would inherit
  them. The hand-limit drop changes outcomes for any task with a 7.65-8 kg one-hand lift; the wrapped-glass bug turns
  the correct "wrap the broken glass" behaviour into a floor full of hazards.
* **Smallest fix.** manipulation: make the one-hand slip limit agree with the kernel's `max_hand_kg` (or have the lift
  plan consult `grip_outcome` and choose two hands); safety: skip `shatter` when `wrapped.coverage >= 1` (the kernel
  already decided); people: count the camera/mic capture only when no intrusion was counted for that person and
  moment (or count both in room mode too, and document it); add `doc` to the `glass_shard` view block. Then update
  only the 4 stale tests.

### T2 (High). "0 defects" and "0 leaks" come from walks that almost never succeed at anything

* **What is wrong.** The fuzzer and the perception auditor issue random commands with naive arguments; almost all
  domain actions are refused, so their success paths, the state they write, their messages and the observations
  after them are never checked.
* **Evidence.** [ran] fuzz 12 x 300: 46 of 427 actions ever succeed (10 domains: 0 successful commands). Perception
  audit 40 x 80: 32 of 427. All authored spec plans on seed 41: 210. Union: 198 actions (46 %) succeed nowhere. The
  gate runs even smaller audits (6 x 150 and 6 x 300) [read `tier1_gate.py:1304-1331`].
* **Why it matters.** The brief's "0 leaks" and "fuzz 0 defects" are read as statements about the simulator; they are
  statements about roughly 50 core and navigation actions. The leak classes the red teams found (look-alike labels,
  destroyed-entity ids, refusal wording) live in domain success messages, exactly where the audits do not reach.
* **Smallest fix.** Print and gate on per-action success counts (`S/coverage_audits.py` does it in 100 lines). Seed
  the walks from states where actions can succeed: start from every authored spec's setup (and each domain test's
  scenario builders), and run the leak checks on every realistic spec's reference and wrong-plan runs. Gate rule:
  every registered action succeeds at least N times across fuzz plus perception audit, or is on a reviewed list.

### T3 (High). The gate's comparison cannot see several ways a package can make the benchmark worse

* **What is wrong / evidence.** [ran `S/gatelab.py`, section 5.2] With the gate's own `compare`:
  (a) a new spec that fails on every seed passes ("5 new trial(s)", PASS); (b) a passing spec that starts failing
  passes as soon as the package writes its own `reviewed.json` (rows are read from the package's own handoff or gate
  folder; nobody else signs them); (c) all 16 `ADOPT:<domain>` packages share the code `ADOPT`, so the cleaning
  package can "review" an admin_inventory regression; (d) a spec rewritten to a one-step reference with an
  unchanged verdict passes, with only an "edited specs" line; (e) a spec failing in both records passes (the C1 gate
  labelled G2 "PASS" with 241/242 specs passing) [ran E0, read C1 summary]; (f) trial
  records hold no wrong-plan failure labels (`TRIAL_KEYS`, `worker_trials` keep `fails`, `ran`, `refusals`, `name`
  only [read `tier1_gate.py:523-601`]), so a wrong plan that used to fail on its goal and now fails only on a
  harm flag is invisible (Phase 1 F1). Deleting a spec is caught (null result).
* **Why it matters.** The authoring wave will add about 2,477 specs. Under (a) none of them has to pass the gate;
  under (b)-(d) a package can weaken specs with a self-written justification. For the adoption round these were
  live: 88 of 89 adopted-domain specs were edited (`spec_hash` changed in 440 of 445 trials) [ran
  `S/gate_vs_c1.py`]; adoption only added wrong plans (9 added, none removed) [ran], which is the good direction,
  but the gate would not have noticed the opposite.
* **Smallest fix.** Add absolute rules next to the delta rule: every spec passes in its own mode (new ones included);
  every test file green except an allowlist with owner and expiry. Bind review codes to the files a package owns
  (an `ADOPT:cleaning` row may only cover `cleaning__*` specs) and require a second signature (main session) in a
  file the package cannot write. Record the sorted failure-label set of every wrong plan in the trial record and treat
  a change as a verdict change.

### T4 (High). The adoption round was not gated the way the design says

* **Evidence.** [ran] The command every adoption agent was told to run (`tier1_adopt.js`: "`python
  tools/orchestration/tier1_gate.py compare --package ADOPT:${d} --shared-tree`") exits with an argparse error (exit 2)
  and compares nothing. [ran `ls catalog/analysis/tier1`] No `handoff_ADOPT_*` file and no adoption gate record exist
  in the checkpoint (the gate folder is git-ignored, `.gitignore` line "catalog/analysis/tier1/gate/"; only C1 is in
  the frozen tree). [read TIER1_INTEGRATION 3.5] the golden protocol requires a dump before the first edit; there is
  none, which is why the generation-1 change cannot be attributed (T6). The checkpoint was committed with G1, G5, G8 and
  G9 failing (my gate run, section 5.1).
* **Why it matters.** The brief's process story (each package gated, handoffs, reviewed differences) did not happen
  for the six adopted domains, and it is the template for the next ten.
* **Smallest fix.** Correct the prompt (`compare C1 <ID> --package ADOPT:<d>`, or better `record --id ADOPT-<d>
  --package ADOPT:<d> --shared-tree`), require the handoff and the gate summary as merge artefacts, and commit gate
  `summary.txt`/`compare.txt` (small) even if the trial records stay ignored.

### T5 (Medium). Gate checks that pass by skipping

* **Evidence.** [ran] `scope` without git: "G10 SKIP git unavailable", exit 0; the record's overall PASS treats SKIP and
  INFO as passing (`Gate.write`: `passed = not any(s == "FAIL" ...)`) [read]. [ran] in `compare`, G4/G6 regressions
  (0 -> 57 leaks, 0 -> 9 fuzz defects) are INFO and the comparison PASSES; only `record` enforces them by exit code
  [read]. G1b is a delta check for isolated domains (known failures carried) [read].
* **Smallest fix.** SKIP of a required check = FAIL; make `compare` fail on any G4/G6 increase.

### T6 (Medium). Every generated house changed at adoption; nobody can say what changed

* **Evidence.** [ran] 0 of the 340 pinned generation-1 houses match `tests/data/gen1_digests.json` (gate G5; the unit
  test's sample: 86/86 differ); all 765 own-mode
  trials of the 10 not-adopted domains have a new `initial_hash` though their spec files are unchanged; golden script
  runs differ from the reset with unchanged step outcomes and durations. [read] the brief ("Generation 1 output is
  kept byte-identical by golden digests") and two adopted domains' docs claim the opposite.
* **Why it matters.** Every spec's scene changed under it; verdicts happened to hold (243/243 own mode, 230/243 forced
  realistic, section 5.1), but "byte-identical generation 1" is currently false and unexplained.
* **Smallest fix.** `tools/golden.py dump` on 0e77b67 and on bc0417b, `golden.py diff`, list the changed path
  classes per domain, fix the docs, then re-record gen-1 digests and the golden in one main-session commit.

### T7 (Medium). Tests that fail on a clean checkout or under load

* **Evidence.** [ran] `test_eval_splits.VerifiedSpecsTests.test_report_evidence_must_be_current` errors because the
  empty `tasks/templates` folder is not in git; six handoffs record working around it by hand [read]. [ran]
  `test_mind.BudgetTests` fails 1 run in 3 on a busy machine (wall-clock bound 0.003-0.006 s), and the gate's G1b
  failed on `test_dialogue`'s vocabulary-load bound (0.128 s vs 0.06 s). There is no CI, so
  nothing but the developer's machine has ever run the suite green.
* **Smallest fix.** `tasks/templates/.gitkeep` (and `house_sim/data/actions/.gitkeep`) or a guard in the test; move
  wall-clock assertions behind an opt-in benchmark flag; run the suite in a clean clone (any CI) on 3.9 and 3.12+.

### T8 (Medium-Low). Probable performance regression in `wait(until)` and `ask`

* **Evidence.** [ran] `tools/sweep_bench.py` in the gate and twice more: `until` 0.097-0.125 ms per substep (budget
  0.02 ms), `ask` 10.3-13.8 ms (budget 2 ms), above the tool's own 3x failure factor every time; the C1 record says
  `sweep_bench: ok` [read]. In the same runs `notices` 0.0024 ms, `monitor_idle` 0.003 ms, `mind_update` 0.15 ms
  stay far under budget, so general machine load is an unlikely full explanation [inferred].
* **Why it matters.** `wait(until)` is the long-horizon primitive (the brief's 6.5 s per quiet day already misses its
  3.5 s target); `ask` sits in every dialogue episode.
* **Smallest fix.** Re-run `sweep_bench.py` and `bench_systems.py --day` on a quiet machine at 0e77b67 and bc0417b; if
  confirmed, profile the people domain's adopted answerers and the camera-aware perceivability path of `until`.

### T9 (Low). The fuzzer's determinism claim is wider than its check

* **Evidence.** [read] `tools/fuzz.py:305` replays only the first 4 houses, skips houses with defects, and in the
  default (oracle) mode digests state only; its docstring says every house is replayed. [ran] I replayed all 12:
  identical, so this is a coverage gap, not a found bug.
* **Smallest fix.** Replay every house (it costs about one extra minute per 12 houses here).

### T10 (Low). Generation 2 silently skips furnishings whose types do not exist

* **Evidence.** [ran] gen-2 layout audit: `sofa_cushion` missing in 220/220 houses, `indoor_mat` in 165, `handbag` in
  79 (types absent from the registry, requested from textiles_laundry, core and admin_inventory); 2 domain-only
  hazard-access problems excluded from the count. Exit status 0.
* **Smallest fix.** Before D2 (gen 2 default), make missing types a failure of the gen-2 audit.

### T11 (Low). Smaller accuracy points

* "14 tests in 7 kernel files" = 14 failing file-runs; 12 distinct tests [ran].
* `tests/data/spec_baseline.json` (584 KB) is read by no test or tool, only listed as a file packages may write
  [ran grep].
* Replays: median 3.5 MB per reference episode, 899 MB for one seed of 243 references [ran] (the brief's 3 MB is right).
* The C1 record (the only gate record in the tree) is overall FAIL [read], consistent with Phase 1 F3.

### Recommended new checks (exact definitions, and what each would have caught here)

1. **G2-ABS (absolute spec rule).** After G2, FAIL if any key in `specs_own.jsonl` has a trial with `passed != true`,
   new keys included, unless the key is in `catalog/analysis/tier1/known_failing.json` with an owner and an expiry
   date. Catches E1 (new failing spec) and E0 (carried failure).
2. **Wrong-plan reason stability.** In `worker_trials`, store for every wrong plan `why = sorted({label.split(":")[0]
   for label in outcome["failures"]} | ({"goal"} if goal_fraction < 1 else set()))`; `trial_verdict` includes it, so
   a change of the set is a verdict change needing review. Catches Phase 1 F1-type drift (goal -> harm flag only),
   which today is invisible to the gate.
3. **Action success coverage.** Sum per-action success counts over fuzz, perception audit and all spec plans
   (`S/coverage_audits.py`, `S/spec_action_coverage.py`); FAIL if any registered action has fewer than 3 successes
   and is not in a reviewed `uncovered_actions.json`. Today it would list 198 actions (T2).
4. **Metamorphic cross-layer tests** (unit tests, all domains loaded):
   * lift monotonicity: for masses m1 < m2 <= `max_mass_kg` of the same item, if `pick_up` holds m2 then it holds m1.
     Catches the 7.65-8.0 kg drop (T1).
   * wrap containment: breaking an item whose `wrapped.coverage >= 1` creates no entity outside the wrap and no
     floor hazard. Catches the wrapped-glass shard field (T1).
   * camera-mode invariance of harm counts: the same scripted episode in room and geometric mode yields equal
     `stats` for every global harm flag (privacy_violations, injury, ...), unless the flag is listed as
     camera-dependent. Catches the privacy double count (T1).
5. **Required-check SKIP is FAIL** (G10 without git, G4/G6 missing variants), and `compare` fails on any G4/G6
   increase (T5).

## 7. What I could not verify

* **Which fields of the generated houses changed at adoption** (T6) and therefore whether the 4 generator/golden
  test failures are stale pins: this needs the pre-adoption tree (commit 0e77b67). The only copy is in the live
  repository's git history, which I was told not to touch; the main session's older snapshots (`scratchpad/snap*`) are
  from 2026-10-09 and useless for this.
* Behaviour on Python 3.9-3.11 and on Linux/macOS (only Windows and 3.14.8 here); cross-version determinism.
* G7 performance: skipped in my gate run because the machine was shared and loaded (it would only measure noise).
* Any check that needs the split key or a manifest (forbidden); `test_eval_splits` and the eval tests ran with
  throwaway keys in a fake home, as the tests themselves arrange.
* Whether the 3 genuine defects change any authored spec's verdict: all 243 specs pass at bc0417b, so no current spec
  exercises them; new specs will.
* Unit-test line coverage: I did not measure it (the stdlib `trace` module would take hours on this suite); my action
  coverage numbers exclude the unit tests.

## 8. What is strong (do not touch)

* Determinism engineering: stateless hash-derived randomness, sorted-key digests, journaled world. Every
  cross-process, cross-hash-seed, reversed-order and replay check I ran came back identical (243/243, 255/255,
  12/12) [ran].
* The unit suite is large and sharp: it caught all three real adoption bugs. The failures are informative, with
  messages that point at the cause [ran].
* `HouseEnv.replay` verifies the state digest at every step, so replay divergence is detected at the step it happens
  [read, ran].
* The gate's G3 "free determinism check" (realistic specs must be byte-identical in G2 and G3, two processes) is a
  good idea and held: 255/255 [ran].
* Adoption added 9 camera-aware wrong plans and removed none; forced-realistic passes rose from 225/242 (C1) to
  230/243 with no new failures [ran].

## 9. Scripts I wrote (all in `S/` = `scratchpad/review/out/p2_tests_gate_scratch/`)

| Script | Purpose |
|---|---|
| `env.sh` | fake home, network forbidden, keys removed; sourced before every command |
| `det_replay.py` | `run` / `replay` / `compare`: reference-plan digests (state, observation sorted and in key order, every step result) across processes, hash seeds and orders; `HouseEnv.replay` check |
| `gatelab.py` | synthetic edits of copies of the C1 gate record and the gate's own `compare` (results in `S/gatelab/gatelab_results.txt`) |
| `coverage_audits.py` | the fuzzer's / perception auditor's own `run_house` on the same jobs with a per-action success counter; `--replay-all` replays every fuzz house in a fresh process |
| `spec_action_coverage.py` | per-action success over every reference and wrong plan of all specs |
| `gate_vs_c1.py` | this gate's own-mode trials vs C1's by domain group (hash, verdict and wrong-plan changes) |
| `privacy_count_probe.py`, `hand_limit_probe.py`, `wrapped_glass_probe.py` | the three defect repros |

Outputs: `S/logs/` (unit suite, golden check), `S/det/` (determinism records), `S/audits/` (fuzz, perception,
layout), `S/cov/` (coverage), `S/gatelab/`, `S/gate/P2REVIEW/` (my gate record) and `S/gate/gate_run.log`.
