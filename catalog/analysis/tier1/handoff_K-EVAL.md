# Handoff K-EVAL (adoption round): kernel follow-ups, evaluation side

Read before starting: `docs/design/TIER1_INTEGRATION.md` (1 with 1.4, 1.8-1.14; 2.4-2.6; 3; 5; 6 with "Every domain
also"), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_SWEEP.md` (7.1-7.5, 19), `docs/KERNEL_API.md` (v3.0,
v3.1), and the handoffs: `handoff_TIER1-GATE.md`, `handoff_TIME-EVAL.md`, `handoff_C1.md` in full; the follow-up and
fix sections of `handoff_PEOPLE-IO.md` (9-11) and `handoff_B6.md`; the STAGE-B-FIX B9 item of `handoff_BODY-CORE.md`
/ `handoff_BODY-ENGINE.md` / `stageB_open.json`; the other handoffs searched for items addressed to K-EVAL's files.

## 1. Summary

* **Requests closed**: TIME-EVAL 3 (`audit.verify(gen=)`; `--verify --gen 2` works as a forced check that never
  replaces the catalog evidence), 4 (viewer: monitor milestones, `phase_done` steps, time marks), 7 (multi-phase
  dataset export); PEOPLE-IO 4 (`env.known_map()` is a view of `env.mind.map`; `env._known_map` a read-only alias);
  PEOPLE-IO 2 and 3 were already closed at C-MERGE (verified, unchanged); STAGE-B-FIX B9 (body audit); C1 requests
  2, 3, 4, 5, 6, 7; the adoption-round gate findings (compare restriction, `--shared-tree` attribution, `compare
  --shared-tree`).
* **Compact replays** (`house-replay-v3`, `kernel/replays.py`): about 30 times smaller (typical 60-95 KB instead of
  about 3 MB), the states and command log kept exactly; old v2 files load; viewer, harness, CLI and demos use it. The
  demo replays are committed again (66 files, 5.9 MB; `.gitignore` entry removed, the packaging test restored).
* **Gate tool**: adoption packages (`ADOPT:<domain>`, `K-EVAL`, `K-CAM`, `ADOPT` union), `compare [PREV [ID]]
  --package --shared-tree --spec-files`, `isolate`, close-step lists, physics audit `--out`, spec chunks in authored
  order. Leaderboard and viewer replay multi-phase logs.
* Gate on an isolated copy (HEAD `bc0417b` + this package's 106 files) against a pristine copy of HEAD: 0 verdict,
  0 state-digest and 0 observation-digest changes in 2 x 1,215 trials, action fingerprint unchanged; every failing
  test also fails on pristine HEAD (the adoption checkpoint's pending pins) or is a timing bound on a saturated
  machine; G7 PASS on a quiet machine (resumed run, section 10); G10 PASS. 45 new regression tests.

## 2. Files

Created: `house_sim/kernel/replays.py`, `tools/compact_replays.py`, `tests/test_eval_replays.py`,
`tests/test_eval_audit.py`, `tests/test_eval_tier1_gate.py`, `tests/test_body_audit.py`, this handoff.
Edited: `house_sim/kernel/engine.py`, `house_sim/audit.py`, `house_sim/cli.py`, `house_sim/eval/viewer.py`,
`house_sim/eval/viewer_template.html`, `house_sim/eval/dataset.py`, `house_sim/eval/leaderboard.py`,
`house_sim/eval/run.py`, `tools/body_audit.py`, `tools/orchestration/tier1_gate.py`, `tools/orchestration/README.md`,
`tools/orchestration/regen_leaderboard.sh`, `tests/test_eval_viewer.py`, `tests/test_eval_dataset.py`,
`tests/test_eval_leaderboard.py`, `tests/test_eval_splits.py`, `tests/test_mind.py`, `tests/test_camera_realism.py`
(one line: the replay is read with `replays.read`; K-CAM edits the same file), `docs/KERNEL_API.md` (new section
"v3.1 adoption-round follow-ups (K-EVAL)"), `docs/VIEWER.md`, `docs/DATASET.md`, `docs/EVAL_HARNESS.md`,
`docs/EVAL_CONTRACTS.md`, `docs/KNOWN_LIMITS.md`, `docs/TASK_SPEC.md`, `docs/design/TIER1_INTEGRATION.md` (2.4 / 2.5
close-step lists), `.gitignore` (the `demos/replays/` entry removed), `catalog/analysis/tier1/handoff_TIER1-GATE.md`
(appendix B: the adoption-round gate forms).
Converted in place (were git-ignored, now committable): `demos/replays/*.json`, 66 files, house-replay-v2 223 MB ->
house-replay-v3 5.9 MB (largest 108 KB), content unchanged apart from the dropped change lists (deviation 7).
Removed from `reports/` (recoverable from HEAD; copies in `ad_K-EVAL/stale_prototype_reports/`): `unit_tests.txt`,
`unit_test_summary.json`, `cli_checks.json`, `package_checks.json`, `package_build.txt`, `compilation_summary.json`.
All text files LF.

## 3. APIs

**`house_sim/kernel/engine.py`**

* `HouseEnv.known_map() -> {rid: {"label", "type", "exits", "connections": [{"id", "to", "kind", "door"}],
  "seen_s"}}`: a copy of `env.mind.map` (rooms the robot was in at a step's end or at reset, and prior knowledge),
  whether or not the agent observed; realistic: door ids as issued handles, else "something". **DEVIATION** (from the
  old behaviour, as TIER1_SWEEP 7.1 asks): it was the observation-time table (rooms observed, connection
  `width_m` / `cell` / `open` / `locked` of the moment, handles issued at observe time).
* `HouseEnv._known_map` -> `KnownMapView(env)`: read-only `MutableMapping` over `mind.map` in the old shape
  `{rid: {"exits", "connections", "label", "type", "seen_s"}}`, the current room always present, writes / deletions /
  `clear` / `update` ignored, copies plain dicts; the setter ignores assignments (reset, load).
* `HouseEnv.save_replay(path, compact=True, keep_changes=False)`; `HouseEnv.replay(path)` reads v2 and v3.
* `phase_done()` history records add `"phase"`, `"next_phase"`, `"waiting"`.
* `HouseEnv.load`: a payload without `"mind"` seeds `mind.map` from its `"known_map"`; `save` writes a snapshot of
  the view under `"known_map"`.

**`house_sim/kernel/replays.py`** (new): `FORMAT_V2`, `FORMAT_V3`, `FORMATS`, `BULKY`, `LZMA_PRESET`; `pack(value)
-> (codec, text, sha256)`, `unpack(codec, text, sha256=None)`, `encode(data, keep_changes=False)`, `decode(obj)`,
`write(path, data, compact=True, keep_changes=False)`, `read(path)`, `load_json(path)`, `is_replay(obj)`.

**`house_sim/audit.py`**: `verify(root, seeds, emit, only, workers, perception=None, camera=None, gen=None)`;
constants `EVIDENCE`, `EVIDENCE_RUN`, `FORCED_EVIDENCE`, `FORCED_RUN`. **CLI**: `--verify` / `--verify-only` pass
`--perception`, `--camera`, `--gen`.

**`house_sim/eval`**: `viewer.REPLAY_FORMATS`, `viewer.TIME_MARKS`, `viewer._milestone_names(task)`; page meta
`phases`; steps `phase_done` (tg ended, de next); marks `notice`, `phase_start`, `phase_end`, `phase_relocate`,
`standing_order_fired` (template class `.mk.time`). `leaderboard.PHASE_DONE`; `parse_log` steps carry
`phase_done`, `phase`, `next_phase`, `waiting`. `run.py` logs `"waiting"` on phase ends. `dataset._is_phase_end`,
`dataset.phases_of`, `dataset._unmet_success`; sft/pairs/critique phase-end turns; meta `phases`; outcome `phases`.

**Tools**: `tools/compact_replays.py PATH... [--drop-changes] [--check]` (new); `tools/body_audit.py`: picks gain
`plan_ok`, `plan_reason`, `cap_believed_kg`; trials gain `overload_refusals`, `robot_drops`, `energy_refusals`,
`power_downs`, `energy_wh`; record `per_task`; `summary.body`; `hits.body_events`; `compare` adds
`body_events_changed`; `per_task_body(trials)`, `BODY_STATS`. `tools/orchestration/tier1_gate.py`: `VERIFY_OUTPUTS`,
`COMPILED_SPECS`, `EMPTY_DIRS`, `gate_dirname`, `restrict_rows`, `spec_scope`, `scoped_rows`, `default_prev_dir`,
`tree_files`, `isolated_changes`, `ISOLATE_MANIFEST`, packages `K-EVAL`, `K-CAM`, `ADOPT-ALL` (aka `ADOPT`),
`SUBSTAGES["ADOPT"]`, `DEFAULT_PREV["ADOPT"] = "C1"`, `write_json(path, data, sort_keys=True)`; commands `compare
[PREV [CUR]] [--package] [--shared-tree] [--spec-files]` (exit 2 without a record) and `isolate --out [--package]
[--files] [--base]`.

## 4. Deviations from the spec

1. **Forced verification is a check, not evidence.** TIME-EVAL request 3 asked for `audit.verify(gen=)` so that
   `python -m house_sim --verify --gen 2` works. Passing it through as-is would overwrite `reports/verification.jsonl`
   with forced-mode records, which the coverage reports, the harness, the dataset exporter, the leaderboard and the
   viewer all trust as evidence of the specs as authored (a spec that fails under gen 2 and stays gen 1 would turn
   STUB). A run with `--gen`, `--camera` or `--perception` therefore writes `reports/verification_forced.jsonl` /
   `verification_forced_run.json` (records carry `"forced"`; a narrowed run merges only with a check of the same
   options) and leaves the evidence and coverage alone. This also changes `--verify --perception realistic`, which
   used to overwrite the evidence (nothing relied on that; no domain behaviour changes).
2. **Compact replays keep the states and the log, not the per-step change lists** (by default). The task suggested
   either re-simulation from config + log + digests or gzip. Measured on the 66 demo and 220 leaderboard replays:
   compressing the initial config, start state, baseline and steps together with xz finds the near-identical states
   (2.8 MB -> about 65 KB); dropping the start state and baseline (re-simulation) saves only about 5 % more, while
   keeping them lets a replay start exactly where it started. The traced steps' change lists dominate long traced
   episodes (300 steps: 14.5 MB of changes, 466 KB compressed vs 92 KB without), and a traced re-run computes them
   again, which is what the viewer does; so v3 drops them unless `keep_changes=True` (kept for the viewer's
   recorded-change fallback when the current code can no longer reproduce a recording). Every step keeps its state
   digest; `HouseEnv.replay` checks them all. zlib is the fallback codec when Python lacks `lzma` (about three times
   larger).
3. **`env._known_map` is a read-only view, not a second table.** "Keep env._known_map working as a compatibility
   alias": it now shows `mind.map` (the old table's shape); perception's and realism's observation-time writes are
   ignored (they are K-CAM's files: request below). Navigation's `known_rooms` (already the union of `mind.map` and
   `_known_map`) and reports' known subjects see the same rooms as before (`mind.map` was already a superset).
   Realism's handle issuance at observe time is unchanged (it reads the current room's entry, whose door ids are the
   same), so realistic observations are byte-identical (G3).
4. **The viewer's fallback milestones** come from the recording's final evaluation (`milestones` with their steps),
   not from `env._monitor.record()`: a replay file does not carry the monitor record, and the final evaluation's
   milestones are the monitor's (with step and time), which is what a frame needs. Live frames use the re-run's
   `env.evaluate()` (the monitor) as before.
5. **Phase ends in the leaderboard.** Not in the task list but required for "update the leaderboard consumers":
   `parse_log` treated a phase-ending done as the episode's end, so a multi-phase episode could not be re-scored
   (its later steps counted "after the end", `env.phase_done()` never called). Fixed with a test; the harness log's
   phase-end item gains `"waiting"` so the parser knows whether a last item ended the episode.
6. **Prototype-era reports moved, not deleted**: the six files are recoverable from HEAD (`git checkout HEAD --
   reports/<file>`) and copied to this round's scratch folder (`ad_K-EVAL/stale_prototype_reports/`). Nothing read
   them (grep over the repository: only the round's task texts and C1's handoff mention them).
7. **The stale demo replays were converted, not regenerated.** `demos/replays/` held 66 C1-era v2 replays (223 MB,
   git-ignored, refused by the current action fingerprint). Un-ignoring them as they were would have put 223 MB into
   `git status`; regenerating them mid-round would record a shared tree in flux. They were converted in place with
   `tools/compact_replays.py --drop-changes` (nothing re-simulated: 5.9 MB, largest 108 KB) and stay exactly as
   stale as before; the close step regenerates them (`python -m house_sim --demo`, which now writes v3).
8. **The gate handoff got an appendix** (`handoff_TIER1-GATE.md`, appendix B) documenting the supported `compare`
   forms, as the item allowed; the tool also accepts them.

## 5. Stubs and contracts

No `TIER1-STUB` markers added or left (`grep -rn "TIER1-STUB" house_sim tools tests docs | grep -v docs/design`:
none). Contracts consumed: PEOPLE-IO's `Mind` (`map`, `map_entry`), TIME-EVAL's `phase_done` / `_phases` /
`Monitor` milestones, physics' `forces.lift_plan`, the body's `capacity_kg(believed=)` and stats, `verify.idle` /
`success_rules` / `report_perceivability_gaps`.

## 6. Gate results

Sixteen domain owners and K-CAM edited the same tree while this ran, so the package was gated on isolated copies
(the new `isolate` command; nothing committed): `iso_base` = pristine HEAD `bc0417b`, `iso_keval` = HEAD + this
package's 106 changed files. Records: `ad_K-EVAL/gates/K-EVAL-base/` (scratch; G0, G2, G3, G7) and
`catalog/analysis/tier1/gate/K-EVAL/` (git-ignored; all checks, previous gate = the base record).

```
python tools/orchestration/tier1_gate.py isolate --out <S>/iso_base
python tools/orchestration/tier1_gate.py isolate --package K-EVAL --out <S>/iso_keval
python tools/orchestration/tier1_gate.py --root <S>/iso_base --gates-root <S>/gates record --id K-EVAL-base \
       --stage ADOPT --prev none --only G0,G2,G3 --jobs 4                  (later --only G7 --bench-repeats 3)
python tools/orchestration/tier1_gate.py --root <S>/iso_keval --gates-root catalog/analysis/tier1/gate record \
       --id K-EVAL --package K-EVAL --prev none --skip G7 --jobs 6
       (then --only G10 / --only G7 with --prev <S>/gates/K-EVAL-base, which links the record to the base)
python tools/orchestration/tier1_gate.py compare --package K-EVAL --shared-tree
```

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `d50fc386d967` (427 actions, 112 systems), unchanged vs pristine HEAD |
| G1 | FAIL (pre-existing) | 59 files, 4,028 tests (normal), normal + strict. Failing tests, each also failing on pristine HEAD (run in `iso_base`): `test_body_core` GraspTests x2, `test_camera` CaptureTests x3, `test_generator_compat` x2, `test_physics_forces` LiftPlanMatchesCorePickUp, `test_physics_statics` break modes, `test_room_golden` x2, `test_sight` view-block keys (the adoption checkpoint's "14 kernel-test pins pending"); timing bounds under 100 % CPU: `test_camera_realism` geometric observe overhead (also on HEAD), `test_mind` update budget (normal only), `test_perception` realistic observe overhead (strict only). Paired, interleaved re-runs: realistic observe 5.37-5.63 ms (HEAD) vs 5.56-5.62 ms (K-EVAL), oracle 2.35-2.42 ms both; the two timing tests pass on both copies when the machine allows. Every K-EVAL test file is green in both modes (`test_eval_*`, `test_mind` functional tests, `test_body_audit`) |
| G1b | PASS (known) | isolated domains 14/16 files (the 3 failing tests in `test_admin_inventory` / `test_food` known since A0); kernel files with `HOUSE_SIM_DOMAINS=core` 30/30 |
| G2 | PASS | own mode 243/243 specs (1,215 trials); vs pristine HEAD 0 verdict, 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 230/243 (1,215 trials); vs pristine HEAD 0 / 0 / 0 changes; the 255 realistic-spec trials identical in G2 and G3 (so the known-map alias left realistic observations byte-identical) |
| G4 | PASS | perception audit room and geometric: 0 leaks, 0 crashes |
| G5 | FAIL (pre-existing) | layout 240 houses, 0 problems; generation-1 digests 0/340 equal to `tests/data/gen1_digests.json` -- also 0/340 on pristine HEAD (`tier1_gate.py --root <iso_base> gen1-digests`): the adoption checkpoint changed generation-1 houses and the file awaits its re-record |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | re-recorded on a quiet machine in the resumed run (section 10), both copies back to back with `--bench-repeats 5`: `substep_ms` 3.18 -> 3.13 (-1.6 %), `wait_1h_s` 1.110 -> 1.124 (+1.2 %, limit 1.166), `day_s` 6.53 -> 6.39 (-2.0 %). (The first record, under 100 % CPU from sixteen concurrent owners, had `wait_1h_s` 1.21 -> 3.90, a load spike; interleaved `_benchwait` runs then gave HEAD 1.268 s, K-EVAL 1.271 s.) Nothing on the wait path changed: the known-map view is read only when observing, the replay code only when saving |
| G8 | FAIL (pre-existing) | room golden and golden_all fail on pristine HEAD too (pending re-record of the adoption checkpoint) |
| G9 | SKIP | no extras for this package |
| G10 | PASS | isolated copy: 106 changed files, 0 outside K-EVAL's list. On the shared tree (`compare --package K-EVAL --shared-tree`): 328 changed files, 219 attributed to the ten domain packages and K-CAM, 2 outside every package: `docs/design/VERIFY_HARDENING.md` and `catalog/analysis/verify_hardening_critique.json` (another session's design work, not an adoption package) |

`compare --package K-EVAL --shared-tree` (project root, resumed run): G0 PASS, G2 PASS, G3 PASS (0 / 0 / 0), G7
PASS, G10 FAIL only for files no adoption package owns (the main session's `RESUME.md` and the two
verification-hardening design files, and `house_sim/tasks/skills.py`, changed concurrently during the resumed run:
section 10); `compare result: FAIL` for that reason only.

**Reviewed differences**: none (none needed: no verdict, state or observation digest changed). **Golden**: not
recorded (it fails on HEAD already).

Other measurements: compact replay of a 4-step realistic episode 82.6 KB vs 2.34 MB as v2 (save 0.2 s, replay
identical); a 300-step traced episode 92 KB (466 KB with `keep_changes`, about 17 MB as v2); a leaderboard run's 55
replays (`results/base-*-dev/replays`) about 4 MB instead of about 140 MB (measured mean about 75 KB per replay).

## 7. Known gaps

* `reports/verification.jsonl` / `verification_run.json` are still C1's evidence; every adoption package changes
  the code fingerprint, so they are regenerated at the adoption round's close (`python -m house_sim --verify`), not
  mid-round on a shared tree. The same for `demos/` (stale v2 content, now compact) and `datasets/sample`.
* `obs`-side consumers of the known map: none (the sweep's rule: no observation shows it). `perception.py` /
  `realism.py` still write `env._known_map` (ignored).
* A compact replay recorded with `keep_changes=False` cannot be shown frame-exactly once the current code diverges
  from it (the viewer then shows what the current simulator does, with a warning) -- the same as for harness logs
  without a replay. Record with `keep_changes=True` where that matters.
* The dataset exporter records a wrong plan that stops where the reference ends a phase without a step pair (both
  sides would be the same `done`); its critique and trajectory pair are kept.
* `compare` without a record cannot run spec trials itself (a full record takes 25-60 minutes); it prints the record
  command (exit 2).

## 8. How to test

```
python -m unittest tests.test_eval_replays tests.test_eval_audit tests.test_eval_tier1_gate tests.test_body_audit
python -m unittest tests.test_eval_viewer tests.test_eval_dataset tests.test_eval_leaderboard tests.test_mind
python -m unittest tests.test_eval_leaderboard.RegenScriptTests      # regen_leaderboard.sh's cd line (needs bash)
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_mind tests.test_body_audit
python -m house_sim --verify-only T-ce25a2474ffb3d-01 --gen 2          # forced check -> reports/verification_forced.jsonl
python tools/compact_replays.py results/ --check                        # what converting old run replays would save
python tools/orchestration/tier1_gate.py compare --package K-EVAL --shared-tree
python tools/orchestration/tier1_gate.py isolate --package K-EVAL --out <scratch>/iso
```

## 9. Follow-ups

Requests to the main session / adoption integrator (files outside K-EVAL, or work for the round's close):

1. **Adoption close**: regenerate the evidence (`python -m house_sim --verify`), the demos (`python -m house_sim
   --demo`: compact v3 replays, about 90 KB each, now committable), the dataset sample and the leaderboard
   (`bash tools/orchestration/regen_leaderboard.sh` now runs from any directory); re-record the room golden and
   `tests/data/gen1_digests.json` (both fail on HEAD already). `python tools/compact_replays.py results/` shrinks
   old run replays losslessly if they are kept.
2. **Integrator's gate**: `record --id ADOPT` uses the new `ADOPT-ALL` union (every adoption package's files and
   allowances) and compares with C1 by default; domain owners' narrowed records compare on their own spec files.
3. **K-CAM** (agreed with K-CAM's request 4): `kernel/perception.py`'s `env._known_map[rid] = {...}` write in
   `_observe` is dead code now (the alias ignores writes) and can go; in `kernel/realism.py` only the assignment back
   into `env._known_map` is dead -- the `out_obj(km, ..., issue=True)` call on the read entry issues the current
   room's door handles and must stay (it reads the same door ids from the alias as from the old table, which is why
   realistic observations are byte-identical, G3). K-CAM's request 1 (its file list in `PACKAGES["K-CAM"]`) is done.
4. **navigation_robot** (`systems/navigation_robot/paths.py` `known_rooms`): `env.mind.map` alone gives the same
   rooms; the `env._known_map` read can go, after which the alias can be removed (engine.py, K-EVAL's).
5. **people / reports**: `kernel/reports.py` falls back to `env._known_map` for room subjects after `mind.map`; the
   fallback is now the same table and can go with the alias.
6. **Gate table**: K-CAM's file list in `PACKAGES` is this package's reading of the round's split (camera kernel
   files, `tasks/setup.py`, `agent.py`, their tests and docs); correct it if K-CAM reports otherwise. The main
   session's verification-hardening design files are attributed to nobody (G10 lists them).
7. **Design docs** (main session's): TIER1_SWEEP 7.1 could note that `env._known_map` is a read-only alias now;
   TIER1_INTEGRATION 3.1 could name `isolate` as the shared-tree recipe.

For later packages: `audit.verify` forced runs are checks (`reports/verification_forced.jsonl`); M-GEN2 uses
`python -m house_sim --verify --gen 2` and records its decisions in `catalog/analysis/gen2_migration.json`.

## 10. Resumed run (2026-10-22)

The workflow resumed this package after the handoff above was written (its structured result had not been
returned). Nothing in sections 1-9 was redone; this run re-checked the package against the tree as it is now, re-ran
G7 and added one test.

* **Package intact.** Every code file of the isolated copy `iso_keval` is byte-identical to the shared tree (100
  overlay files; only `docs/KERNEL_API.md`, `docs/KNOWN_LIMITS.md`, `docs/TASK_SPEC.md` and
  `tests/test_camera_realism.py` differ, by K-CAM's concurrent sections), so the isolated records stay valid.
  PEOPLE-IO 2 and 3 re-checked in place (`verify.py` runs `dialogue.lint_plan` / `lint_facts` / `lint_reference` on
  schema-3 specs and adds `report_perceivability_gaps` to the realistic check; `predicates.report_bounds` passes
  `after` through to `reports.match` / `match_set`).
* **New test**: `tests/test_eval_leaderboard.py` `RegenScriptTests` (2 tests): `regen_leaderboard.sh` has one `cd`
  line, relative to the script, with `|| exit 1`, and running that line under bash from a temporary directory, the
  project root and `tests/` (script given by absolute, relative and `../` paths) lands in the project root. It fails
  on HEAD's `cd %PROJECT%`. Skipped without a POSIX bash (the WSL launcher in System32 is not one).
* **G7 re-recorded** on a quiet machine (5 % CPU before it started), base then K-EVAL back to back, `--bench-repeats
  5`: PASS (section 6). Records: `ad_K-EVAL/gates/K-EVAL-base/bench.json`, `catalog/analysis/tier1/gate/K-EVAL/`.
* **Full suite on the shared tree** (61 files, 4,280 tests in the normal run, normal + strict, 6 jobs): every
  K-EVAL test file green in both modes. 7 files fail in both modes: the five pending pins above (`test_body_core`
  GraspTests x2, `test_generator_compat` x2, `test_physics_forces` LiftPlanMatchesCorePickUp, `test_physics_statics`
  break modes, `test_room_golden` x2; also on pristine HEAD), and two new ones caused by adoption work (both pass on
  pristine HEAD and on HEAD + K-EVAL, `iso_base` / `iso_keval`):
  * `test_generator_gen2` `Houses.test_pending_pets_have_no_records_or_habitats_yet`: generation-2 houses now hold
    pets other than dogs and cats (`systems/pets/gen2.py`, ADOPT:pets). The test's premise ("pending pets ... not
    yet") is outdated if that is intended: pets' owner or GEN updates the test, else the generator stops adding them.
  * `test_physics_settle` `WorldTouchLogTests.test_the_touch_log_stays_bounded_over_a_week_long_wait` (peak 24 > 16
    ids): `textiles_laundry.fabric_idle` (ADOPT:textiles_laundry) reads the world touch log through a named cursor
    only every 300 s, so `World.trim_touched` keeps up to ten substeps of touches (traced with a probe: the cursor
    stays at 22 while `settle` and the others reach 41-45, the log drops when it reads). Bounded, not a leak; either
    the reader keeps its cursor at the log's end between reads (as appliances_cooking's `cold.release_cursor`) or the
    test's bound is widened by its owner.
* **G10 on the shared tree**: 333 changed files, 222 attributed to the ten domain packages and K-CAM; outside every
  package: `RESUME.md`, the two verification-hardening files (main session) and `house_sim/tasks/skills.py`
  (`_approach_door`: go_room walks a door into view in geometric mode; changed 2026-10-21 23:50 by a concurrently
  running owner, probably K-CAM; add it to that owner's `PACKAGES` entry once confirmed).
* **Still open** (unchanged, for the round's close): the evidence (`reports/verification.jsonl`,
  `verification_run.json`), demos and dataset sample are regenerated once the adoption tree stops changing (section 7);
  K-CAM's request 4 (`perception.py`'s dead `_known_map` write) is still in the tree (K-CAM's file).
