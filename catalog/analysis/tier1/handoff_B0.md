# Handoff B0 (stage B baseline): the baseline gate on the committed stage-A tree

Read before starting: `docs/design/TIER1_INTEGRATION.md` (sections 1 (1.9 engine order, 1.16 import-inert, 1.17
conflicts 27 and 31), 2.1-2.4 with the B0 row, 3 (the gate, 3.4 performance budgets) and 5 (handoff protocol)),
`docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_EMBODIMENT.md` 8.1 (M0: the `body_audit --baseline` record),
8.3 and 9 (the specs and the performance baseline the body packages use). B0 has no layer spec of its own. Handoff
files read: `handoff_TIER1-GATE.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`,
`handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md` (all that existed).

## 1. Summary

* The full tier-1 gate ran as **B0** on the committed tree (HEAD `ccb8b87`, clean working tree, quiet machine):
  `TIER1-GATE B0 [B0] vs A0: PASS (11 pass, 2 skip)`.
* It equals A0: 0 verdict, 0 state-digest and 0 observation-digest changes in 1,210 + 1,210 trials, the same action
  fingerprint, layout counts equal, generation-1 digests 340/340; the benchmark is 4.38 ms per substep (A0 4.38, +0.0 %)
  and 1.1029 s for the 1 h wait (A0 1.102, +0.1 %).
* The body baseline is `catalog/analysis/tier1/gate/B0/body_audit.json` (G9). It has 0 landing hits and is identical
  to a standalone `body_audit.py --baseline` run and to the A-FIX record. No source file was edited.

## 2. Files

Created: this handoff, and the gate record `catalog/analysis/tier1/gate/B0/` (`summary.txt`, `meta.json`,
`tests.json`, `specs_own.jsonl`, `specs_realistic.jsonl`, `specs_summary.json`, `audits.json`, `bench.json`,
`extras.json`, `scope.json`, `compare.txt`, `compare.json`, `body_audit.json`). The gate folder is git-ignored
(`.gitignore`: "tier-1 gate records (local working data ...)"), so only this handoff appears in `git status`.
No other file was created or edited. G10 at record time: `0 changed file(s); 0 outside the list of B0`.

Scratch only (my private folder `.../scratchpad/t1_B0/`, not in the repository): the gate's work folder
(`gate_work/`, including `all_specs.json`), the full gate log, the standalone body audit and its log, the
import-inert re-check (`inert_out/`, `inert_work/`) and the inspection script.

## 3. APIs

None. B0 adds no code. The interfaces later packages consume from B0 are records (section 5).

## 4. Deviations from the spec

1. **Extras.** The B0 row of the gate's package table lists no extras, but the plan (2.4 B0) asks for
   `body_audit --baseline`. Gate handoff deviation 13 and BODY-MODEL deviation 21 also put the body baseline in
   `gate/B0/body_audit.json`. The record therefore ran with `--extras body,gen`: G9 wrote the body baseline into the
   gate folder, and the generator extras give stage B a GEN reference (compat and gen-2 tests, gen-2 layout audit,
   gen-2 / gen-1 timing). The physics, camera and sweep extras were not requested: their tools come later
   (PHYS-INT, CAM-RENDER, TIME-EVAL).
2. **`python tools/body_audit.py --baseline` ran twice.** It ran once inside G9 (`--out gate/B0/body_audit.json`, the
   record) and once standalone with `--out` in my scratch folder. The standalone run did not use the default
   `<temp>/house_ai_tier1/body_audit/baseline.json`, which other sessions share. Both are byte-equal after JSON
   canonicalization (section 6).
3. **G1c is SKIP in the record** because B0 lists no import-inert modules. To confirm the 1.16 property on the
   committed tree, I ran G1c separately into scratch with the stage-A list
   (`--package A0+PHYS-DATA+PHYS-STATICS+PHYS-FLUIDS+CAM-SIGHT+BODY-MODEL --only G1c --out <scratch>`). Result: PASS,
   22 modules imported and none registers anything. This check is not part of the B0 record.
4. **Work folder.** `--work` points at my private scratch folder (the default `<tmp>/house_ai_tier1/B0` is shared),
   so `meta.json`'s `human_check` names a scratch path for `all_specs.json`. To rebuild that file, rerun the gate or
   combine `tasks/authored/*.json` again.
5. **No reviewed differences.** None are allowed for B0, and none were needed.

## 5. Stubs and contracts

B0 leaves no `TIER1-STUB` marker. The markers on the tree are stage A's, unchanged:
`kernel/phys/impact.py` (2) and `phys/processes.py` (2) for **PHYS-INT**, `kernel/sight.py` (4) for
**CAM-RUNTIME**, `phys/forces.py` (2) and `phys/support.py` (1) for **BODY-ENGINE**, `kernel/timeline.py`,
`monitor.py` and `phases.py` for **TIME-EVAL**, and `kernel/dialogue.py`, `reports.py` and `mind.py` for
**PEOPLE-IO**. The list comes from `grep -rn "TIER1-STUB(" house_sim`.

Records later packages consume:

* **`catalog/analysis/tier1/gate/B0/`** is B1's previous gate. The gate's default (`DEFAULT_PREV["B1"] = "B0"`) picks
  it, so PHYS-INT's verdict and digest comparisons run against B0.
* **`catalog/analysis/tier1/gate/A0/bench.json`** remains PHYS-INT's benchmark reference: `bench=(1.0, "A0")`, as
  3.4 says. Every other package's reference is the previous gate x 1.05. B0 confirms on the committed tree that A0's
  numbers still hold (section 6).
* **`catalog/analysis/tier1/gate/B0/body_audit.json`** is the body baseline. BODY-CORE runs
  `python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json`, and BODY-ENGINE and
  BODY-FAULTS compare their G9 records with it.

## 6. Gate results

Command (repository root, nothing else running; 25.5 min with 7 slots):

```
python tools/orchestration/tier1_gate.py record --id B0 --extras body,gen --work <scratch>/t1_B0/gate_work
```

Result: `TIER1-GATE B0 [B0] vs A0: PASS (11 pass, 2 skip)`. HEAD `ccb8b87b0cac`, 0 changed files, Python 3.14.8,
Windows 11, 8 CPUs.

| Check | Status | Result |
|---|---|---|
| G0 | PASS | action fingerprint 3e6e94c37334 (423 actions, 107 systems), unchanged vs A0; code fingerprint cf6a7fde2e6d, harness fingerprint 37225aad8070 |
| G1 | PASS | 35 test files x normal + strict: 3,147 tests each, 0 failing runs (9 skipped) |
| G1b | PASS | isolated domains 10/16 files green. The 8 failing tests in 6 files are exactly A0's known list (compared test by test: admin_inventory 2, food 1, manipulation 2, navigation_robot 1, people 1, plumbing_liquids 1; handoff_TIER1-GATE 9 names them). Kernel files with `HOUSE_SIM_DOMAINS=core`: **10/10** green (test_body_model, test_kernel, test_kernel_layout, test_perception, the five test_physics_* files, test_sight) |
| G1c | SKIP | no import-inert modules listed for B0. The separate check passed (deviation 3) |
| G2 | PASS | own mode 242/242 specs pass (1,210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (1,210 trials); vs A0: 0 / 0 / 0 changes; 250 realistic-spec trials identical in G2 and G3. The 16 forced-realistic failures are A0's list (handoff_TIER1-GATE 6) |
| G4 | PASS | perception audit, room camera, 6 houses x 150 steps: 0 leaks, 0 crashes |
| G5 | PASS | layout audit, 240 houses: 0 problems, counts equal to A0; generation-1 digests 340/340 equal to `tests/data/gen1_digests.json` |
| G6 | PASS | fuzz, room camera, 6 x 300: 1,800 commands (312 succeeded), 0 defects, 0 nondeterministic replays |
| G7 | PASS | 4.38 ms/substep (best of 5), 1 h wait 1.1029 s (seeds 0-3, best of 5); vs A0 (1.05x): within budget |
| G8 | SKIP | `tests/test_room_golden.py` not present (PHYS-INT creates it in B1) |
| G9 | PASS | body_audit ok; gen-2 timing gen2/gen1 1.005 (3.848 / 3.830 s); `layout_audit.py 40 --gen 2` ok; `test_generator_compat` ok; `test_generator_gen2` ok |
| G10 | PASS | 0 changed files; 0 outside the list of B0 |

`compare` against A0 (`python tools/orchestration/tier1_gate.py compare A0 B0`). Its output is the same text as the
stored `gate/B0/compare.txt`. The G1 information lines (new test files since A0, all green) are summarized after the
block:

```
compare A0 -> B0 (packages: B0; allowed: nothing; codes: none)
G0   PASS  action fingerprint unchanged (3e6e94c37334)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312
G7   PASS  substep_ms: 4.38 -> 4.38 (+0.0%; limit 4.599 = prev x 1.05)
           wait_1h_s: 1.102 -> 1.1029 (+0.1%; limit 1.157 = prev x 1.05)
compare result: PASS
```

G1 lists 9 new test files since A0, each green in both modes: test_body_model 93 tests, test_generator_compat 6,
test_generator_gen2 50, test_physics_data 64, test_physics_fluids 72, test_physics_forces 27, test_physics_statics 73,
test_physics_thermal 32 and test_sight 59. `compare A-FIX B0 --package B0` also passes: 0 / 0 / 0 changes, test
counts unchanged, G7 -1.4 % / -0.9 %. The committed tree is the reviewed stage-A tree.

**Reviewed differences:** none. **Golden:** not applicable (G8 starts at B1). **Verdict changes:** none.

### Benchmark baseline (3.4, conflict 27)

The plan's benchmark baseline is A0 on this machine. B0 re-measures it on the committed tree and gets the same
numbers within noise.

| Metric | A0 | B0 | Change | Runs (B0, best of 5) |
|---|---|---|---|---|
| `substep_ms` (bench_systems 40 substeps x seeds 0-3, all 107 systems) | 4.38 | 4.38 | +0.0 % | 4.64, 4.42, 4.42, 4.41, 4.38 |
| `wait_1h_s` (gate's own 1 h wait, fresh houses, seeds 0-3 summed) | 1.1020 | 1.1029 | +0.1 % | per seed 0.2305 / 0.3030 / 0.2976 / 0.2718 s |
| bench_systems' own 1 h wait (seed 0) | 0.23 s | 0.23 s | - | - |

Largest systems (ms per 30 s substep, best of 5; B0 / A0): kernel `thermal` 1.673 / 1.688, `climate.house`
0.582 / 0.580, `plumbing_liquids.water` 0.244 / 0.241, `electrical.power` 0.210 / 0.209, `appliances_cooking.tick`
0.156 / 0.155, `food.safety` 0.146 / 0.144, `food.thermal` 0.130 / 0.131, `pets.agents` 0.103 / 0.102,
`electrical.lights` 0.094 / 0.093, `electrical.detectors` 0.077 / 0.077. No system moved by more than 0.015 ms.

Budgets for stage B taken from this record (3.4): PHYS-INT must not exceed A0 at all (4.38 ms, 1.102 s). Every
later package may exceed its previous gate by at most 5 %; for B2 (CAM-RUNTIME) that is PHYS-INT's record. No
stage-A library runs inside the timed part of G7. House generation happens before the timer starts, and the engine
imports none of the phys, sight, body or proprio modules, as the packages checked. Every stage-A record therefore
measures the same simulation path, and its spread is measurement noise:

| Gate | Where | `substep_ms` | `wait_1h_s` |
|---|---|---|---|
| A0 | shared tree | 4.38 | 1.1020 |
| PHYS-DATA | isolated copy | 4.39 | 1.1134 |
| PHYS-STATICS | isolated copy | 4.54 | 1.1279 |
| PHYS-FLUIDS | shared tree (G7 rerun alone) | 4.50 | 1.1280 |
| CAM-SIGHT | isolated copy | 4.41 | 1.1209 |
| BODY-MODEL | shared tree (G7 rerun alone) | 4.43 | 1.1368 |
| GEN | isolated copy | 4.51 | 1.1248 |
| A-FIX | shared tree | 4.44 | 1.1128 |
| B0 | committed tree, clean | 4.38 | 1.1029 |

Range: 4.38-4.54 ms (+3.7 %, median 4.43) and 1.102-1.137 s (+3.2 %, median 1.121). A0 is the lowest record of
both metrics. B0 itself would fail PHYS-INT's 1.0 x A0 rule on `wait_1h_s` (1.1029 > 1.1020) with no code change
(section 9).

### Body baseline (G9, `gate/B0/body_audit.json`, format `house-body-audit-v1`, mode baseline, not partial)

Canonical JSON digests (sha256 of the sorted dump, first 16 hex characters):

| Source | Digest |
|---|---|
| gate record | 500f816b9b7ce652 |
| standalone `--baseline` run | 500f816b9b7ce652 |
| A-FIX record | 500f816b9b7ce652 |

The audit is deterministic, and nothing it reads changed since the stage-A review.

* 242 specs x seeds 0 1 2 41 97 = 1,210 trials, 0 errors; action fingerprint 3e6e94c37334.
* 2,108 executed picks. Landing hits: 0 over derated capacity, 0 explicit power grips under the power size,
  0 picks under the pinch size, 0 auto pinch.
* Lowest battery: 17.7532 % (T-5ad61b6881821b-01 seed 1; that spec is 17.75-17.84 % on all five seeds and is the
  only spec with a battery predicate). The next lowest trial is 87.2 %.
* Longest undocked wait: 42.0 h (A-d66994a566899c-01 seed 0). 17 specs (81 trials) wait more than 1 h undocked
  (section 9).
* Careful speed appears in 14 spec files: electrical (examples, perception), manipulation (examples),
  navigation_robot (examples), people (examples, perception), pets (examples, perception), plumbing_liquids
  (examples, perception), safety (examples, perception), textiles_laundry (examples) and tools_vehicles (examples).
* 21 picks in 5 specs are at 90 % or more of the derated capacity of the hands used, all on the current, not yet
  derated rule (section 9).

## 7. Known gaps

* **No golden yet.** PHYS-INT records `tests/test_room_golden.py` / `room_golden.json` as its first commit (3.5).
  B0 has no room-mode digests beyond the trial records.
* **Missing extras.** `tools/physics_audit.py`, the camera tool flags and `tools/sweep_bench.py` do not exist yet,
  so B0 has no physics, camera or sweep extra baselines. Their creators record the first ones.
* **The gate records are git-ignored.** That includes the body baseline BODY-CORE checks against. It is
  deterministic (three equal runs), so it can be regenerated from commit `ccb8b87` with
  `python tools/body_audit.py --baseline --out catalog/analysis/tier1/gate/B0/body_audit.json`. The trial records
  (`specs_*.jsonl`) can be regenerated by rerunning the gate on that commit.
* **Benchmark noise.** Records on this desktop spread about 3-4 % between runs of an unchanged simulation path
  (section 6). Best-of-5 removes some of it, not all. The first B0 substep run was 4.64 ms (warm-up), the other
  four 4.38-4.42.
* **Known domain failures.** The 8 isolated-domain failures known since A0 remain (domain follow-ups,
  handoff_TIER1-GATE 9).

## 8. How to test

```
python tools/orchestration/tier1_gate.py compare A0 B0              # prints the comparison above; compare result: PASS
python tools/orchestration/tier1_gate.py compare A-FIX B0 --package B0
python tools/orchestration/tier1_gate.py record --id B0 --extras body,gen --work <your scratch>   # the full gate again (~25 min)
python tools/orchestration/tier1_gate.py record --id B0 --only G7 --work <your scratch>            # benchmark only, quiet machine
python tools/body_audit.py --baseline --out <your scratch>/body_audit.json                        # about 3 min; equal to gate/B0's
python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json --out <file>  # BODY-CORE's check mode
```

Use your own `--work` and `--out`: the defaults under `<tmp>/house_ai_tier1/` are shared between sessions, and a
`record --id B0` rerun overwrites the B0 record.

## 9. Follow-ups

**Main session / PHYS-INT (B1): the 1.0 x A0 benchmark rule is inside the noise.** PHYS-INT's G7 budget is
`substep_ms <= 4.38` and `wait_1h_s <= 1.102` (3.4: "must not exceed A0 at all"). Eight records of the unchanged
simulation path range from +0.0 % to +3.7 % and from +0.0 % to +3.2 % over A0. B0 itself, on the committed stage-A
tree with nothing else running, is 1.1029 s against 1.102 s. A one-shot best-of-5 G7 can therefore fail PHYS-INT
when it adds no cost at all, or pass it when it adds about 3 %. 3.4's "net saving from the thermal active set" (kernel
`thermal` is 1.67 ms of the 4.38 ms; PHYS-FLUIDS measured about 40 % less for `thermal_step` on settled houses) should
show as a clear drop. To judge it fairly, I suggest the main session decide one of these before B1 hands over (the
gate tool is the main session's; I did not change it):

* Measure B0 (`ccb8b87`) and B1 alternately in one quiet window (`record --only G7` on each tree, two or three
  rounds) and compare the paired results.
* Accept PHYS-INT when its G7 is at or below the lower of A0 and the same-window B0 run.

Either way, B1's handoff should report the per-system change of `thermal`, which is the claimed saving.

**BODY-ENGINE (B4)**: B-R2 (battery trajectories) touches every spec that waits undocked. The longest undocked
waits in the baseline, per spec (all five seeds unless noted):

| Spec | Longest undocked wait |
|---|---|
| A-d66994a566899c-01 | 42.0 h |
| T-868b9cc704adf1-01 | 4.0 h |
| T-5d2d79cc6ec2ca-01 | 3.5 h |
| T-149e27671dec82-01 | 3.0 h |
| T-b0ab19967436e0-01, T-e7a39a7a0aa001-01 | 2.0 h |
| T-9f83cbcee53083-01 | 1.7 h |
| T-af96a334296669-01 | 1.6 h |
| T-118437362c4ab8-01 | 1.5 h |
| T-cc81e71dc2182e-01 | 1.3 h |
| A-350cc067998929-01, T-28ff695081cd2c-01, T-a31398ab2abf54-01 (seed 0 only) | 1.2 h |
| A-afe8d8c6d6d883-01, A-b4642738e5e520-01, T-73a13fb297c40f-01, T-8b4654687e040f-01 | 1.0 h |

T-5ad61b6881821b-01 (battery predicate, lowest at 17.75 %) and A-d66994a566899c-01 are B-R6's hand checks.

**BODY-CORE (B5)**: the baseline has no landing hits. These picks have the least margin under the derated
capacity, and posture, reach or arm-health derates could push them over:

| Spec | Item | Mass / capacity | Hands | Seeds |
|---|---|---|---|---|
| T-151f6733397156-01 | utility_bucket | 8.0 / 8.0 kg (exactly at capacity) | right | all 5 |
| T-7e93003b0dcefa-01 | mop_bucket | 7.2 / 8.0 kg | left | all 5 |
| A-b6bf8052cc5211-01 | generic_item | 7.5 / 8.0 kg | right | 97 only |
| T-3c114c8e4796ec-01 | generic_item | 18.0 / 20.0 kg | both | all 5 |
| T-fa2552570f7b6f-01 | lawn_mower | 18.0 / 20.0 kg | both | all 5 |

TIER1_EMBODIMENT 8.3 says "every spec using careful: the 7 files listed by body_audit". The audit lists **14**
files (section 6), and the M5 re-check needs all of them.

**PHYS-INT (B1), core-only kernel tests**: G1b's kernel files are 10/10 green with `HOUSE_SIM_DOMAINS=core` on the
committed tree, so the `kernel_core` requirement that starts at B1 holds before PHYS-INT edits anything (A0's
known `test_perception.py` failure was fixed by the main session in stage A). PHYS-INT's new
`test_physics_settle.py` and `test_physics_setup.py` match the gate's `KERNEL_TESTS` pattern `test_physics_*.py`, so
they must pass core-only too. `test_room_golden.py` is not in that list; it runs under G8.

**Main session**: if gate folders may be cleaned, keep a copy of `gate/B0/` (at least `meta.json`, `summary.txt`,
`bench.json`, `body_audit.json`) in `archive/`. It is git-ignored, and B1 and BODY-CORE read it. Domain follow-ups:
none new (the 8 isolated-domain failures are listed in handoff_TIER1-GATE 9).
