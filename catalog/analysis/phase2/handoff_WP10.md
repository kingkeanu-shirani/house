# Handoff WP10 REVIEW, AUDIT, GATE, SPLITS, HARNESS (VERIFY_HARDENING section 25; sections 5.4, 10.7, 12, 15.5, 18, 19.4, 21, 22.6-22.10, 23.1, 23.6)

Files (P2-S4's scope): new `tools/harden_audit.py`, `tools/battery_review.py`, `tools/evidence_replay.py`,
`house_sim/data/verify/components.json`, `catalog/review/exemptions.jsonl`, `catalog/review/review_sessions.jsonl`,
`catalog/review/check_log/README.md`, `docs/REVIEW.md`, `tests/test_review_tools.py`, `tests/test_review_cheats.py`,
`tests/test_critique_cases.py`, `tests/data/review_cheats/` (`lens_a.json`, `lens_b.json`, `lens_c.json`, `caught_as_if.json`);
changed `tools/orchestration/tier1_gate.py`, `run_check.py`, `wallclock.py`, `green_tests.py`, `README.md`, `tools/py39_lint.py`,
`tests/test_py39_lint.py`, `tests/test_gate_tool.py`, `house_sim/eval/splits.py`, `run.py`, `leaderboard.py`. Nothing committed,
nothing created under `%USERPROFILE%\.house_ai`, no network. `tools/golden.py` needed no change: no open finding lies in it.

## What landed

1. **G11, the gate's hardened stage** (`tier1_gate.py`). Every schema-4 spec runs `verify_spec(spec, "full", cache=False)`
   (battery, must-pass, audits, held-out and, with the split key, the private stages) in its own worker; HARDENED is the only pass
   (a dated allowlist entry with `"mode": "hardened"` excuses one); without the split key PENDING_GATE is INFO "not run (no split
   key)"; a catalog id outside the frozen legacy list must declare schema 4; N/A while no spec is schema 4. `compare` recomputes it
   from `hardened.json`. Records from before G11 (`checks_format` absent) stay complete. Schema-4 trials in G2 / G3 keep the
   record's flat fields (WP5's note). G4 also runs `--read-aloud-first` (WP-D people's request).
2. **Evidence reuse and release gating** (`tools/evidence_replay.py`, gate code; `components.json`). Change classes spec /
   simulation / evaluator / harness; `run_fingerprint` (trace, state after every top-level step, observation index, idle on the
   first seed, build digests; the footprint through a profile hook); `replay_status` (current / carried / overdue / stale, 14-day
   limit, components); `release_needed` (> 10 % stale); `rolling_due`, `replay_misses`, `stamp_evidence`, `write_back`. The tool
   marks each record in its file. The gate keeps its own store `<gates root>/_hardened/evidence.jsonl`; `record --release
   [--hardened-limit N]`; `tier1_gate.py rolling` (14-day run, replay-miss audit, `rolling.jsonl`); `tier1_gate.py publish` (a
   release step: the store into the tracked `reports/verification.jsonl`).
3. **`tools/battery_review.py`** (10.7, 18, 22.9). `list`, `show` (the blind form), `accept`, `reject`, `reverse`, `budget`,
   `spotcheck`. Mechanical checks reject before a reviewer's word counts, and a check that cannot establish its condition fails.
   Owner rows are signed with `escapes.sign` where the key is; agent rows log the session in `review_sessions.jsonl` and are refused
   for a session in the check log. Blind category comparison; `other` owner-only; `unrealisable` needs `--battery-owner`; default-fix
   rule (10 specs, same key and reason class) and the review budget (1.3 per spec) stop agent acceptances; `exemption_states` shows
   dormant exemptions and family flips; 10 % spot-check sample, weekly minimum, doubled after a reversal, routing above 90 %.
4. **`tools/harden_audit.py`** (R0, 23.1). Each spec as if schema 4: drafted envelope, protect map, roles, process, look-ahead,
   answers; `expect` from the observed labels only so the stages run, G2 reported "expect missing" with those labels as suggestions;
   static lints reported, not stopping; nothing written to specs or the check log. `specs.jsonl`, `summary.json`, `summary.md`.
   `--cost` is the calibration of 22.6 (alarm above 25 %, work item per owner, catalog projection); `--census` refreshes the field
   census (WP1's request).
5. **Harness** (`eval/run.py`, 8.1, 15.3, 22.10). A schema-4 episode starts with `episode_start` (the idle counterfactual, from
   `verify.idle_rollout`, cached by spec hash, seed, mode, camera, fingerprint, the `data/verify` files and a format tag, on disk only
   for public seeds; `perceive.enable`) and ends with `episode_end` (`verify._finish` with the seed's branch diff set: settle, look-ahead, the agent rule). `replay_log`
   and `leaderboard.replay_episode` do the same, so a re-score gets the same labels. `house.*`, `later:house.*` are side effects;
   `settle:`, `perceive:`, `timing:`, `goal:`, `strict:` are no harm flags. Legacy episodes unchanged.
6. **Splits** (`eval/splits.py`, 15.5, 19.4, 21.3). `hardened_problem`: verdict HARDENED, spec hash, fingerprint or carried within 14
   days; `filter_verified` / `verified_specs` apply it to schema 4 (every spec with `HOUSE_SIM_HARDENED_ONLY=1`, the R4 switch);
   `verified_counts` (two numbers, HARDENED rate per stratum); `stratified` (0.75 x catalog share, thinning in a deterministic order,
   shortfall listed); `build_test_manifest`: candidates only with HARDENED evidence, in every evaluation mode, `drop_rate` recorded,
   above 10 % excluded as skill debt.
7. **Leaderboard** (19.4). `chance_corrected` (success rate minus the record's best constant share, per spec, by kind class; specs
   with no share are counted, not corrected); column in Markdown, CSV and HTML; `spec_status` reads the HARDENED record.
8. **`run_check.py` on schema 4**. `--level`; each spec's line comes from its evidence record (`hardening`, `level_passes`, `score`,
   `gates`), with the level in the entry; the claim mismatch reads the level's gates; `verify --rerun` re-runs at the entry's level and
   compares verdict, hardening verdict and `level_passes`. Legacy lines are unchanged byte for byte.
9. **Gate-tool carry-over** (`CarryOverGateTests`, `test_py39_lint.CloseOutGapTests`; every one replays the repro). See the table.
10. **Acceptance tests of the design** (23.6). `test_critique_cases.py`: 43 cases (the critique's games and false alarms).
    `test_review_cheats.py`: every review cheat kept as data and replayed.

## Interfaces

```python
# tools/evidence_replay.py
classify(rel); changed_components(files); record_components(spec, record=None); split_classes(files)
run_fingerprint(spec, seeds=None, gen=None, footprint=False) -> {"digest", "spec_hash", "seeds", "idle", "build_generator", "footprint"?}
replay_status(record, spec, current_fingerprint, today=None, replay=None, changed=(), changed_classes=None) -> {"status", "why", "evidence"}
footprint_meets(footprint, changed); counts_as_current(record, today=None); release_needed(stale, total)
rolling_due(records, today=None, limit=None, days=14); replay_misses(carried, fresh); stamp_evidence(record, spec, fingerprint=None, today=None)
run(evidence=None, changed=None, workers=1, today=None, ids=None, specs=None, root=ROOT, current=None); write_back(path, rows)
# tools/battery_review.py
normal_policy(policy); reason_class(reason, key=None, category=None); blind_form(spec, record, policy, reveal=False)
mechanical_check(spec, record, policy, category, execute=_execute_family) -> {"ok", "why"?, "evidence_digest"}
check_safe_superset / check_world_resolves(spec, record, policy, execute=...); check_fungible_instance(spec, record, policy, role=None)
check_budget_defense(spec, record, policy); exemption_states(record, exempt) -> (states, re_review)
review_queue(items) -> {"batches", "clusters", "paused", "default_fix"}; reviewer_configs(rows); spot_sample(rows, domain_of_id=None, salt="")
# tools/harden_audit.py
as_if(spec, draft) -> (spec, notes); audit_spec(job) -> row; summarise(record, static, notes, draft); summary_of(rows)
cost_report(records=None, folder=None, planning=None, d3=False) -> {"rows", "alarms", "work_items", "projection", "battery_families", ...}
# house_sim/eval/run.py
is_hardened(raw_spec); idle_key(raw_spec, seed, mode, camera, code=None); idle_counterfactual(raw_spec, config, task, seed, mode, camera, private=False, code=None)
branch_diff_for(raw_spec, task, seed, camera, code=None); episode_start(env, raw_spec, config, task, seed, mode, camera, private=False); episode_end(env, raw_spec, task, mode, seed=None)
# house_sim/eval/splits.py
hardened_only(); is_schema4(spec); hardened_problem(proof, digest, current, today=None); verified_counts(root=None, only=None)
stratum_of(record); stratified(records, catalog=None, share=0.75); build_test_manifest(..., evidence=None); MAX_DROP_RATE
# house_sim/eval/leaderboard.py
chance_corrected(units, statuses) -> {"value", "specs", "uncorrected_specs", "by_kind"}
# tools/orchestration/tier1_gate.py
CHECKS + "G11"; LEGACY_CHECKS; CHECKS_FORMAT; HARDENED_DIR; judge_hardened(rows, allow=None); hardened_strata(rows); public_row(record, file, status)
worker_hardened(job_path, out_path); worker_hardened_report(job_path, out_path); Gate.run_hardened(prev); Gate.evaluate_hardened()
rolling_plan(store, today=None, limit=None); cmd_rolling(args); cmd_publish(args); record --release --hardened-limit N --hardened-timeout S
hidden_files(root); stale_base(where); pycache_prefix(); SOURCE_DIRS; GENERATED
# tools/orchestration/wallclock.py
perf_marks(files) (marks may carry "problem"); mark_problems(marks); registered_without_mark(marks, bounds)
```

## Carry-over (closeout_REV / closeout_VER2), each with a test that fails before the fix

| Finding | Fix | Test |
|---|---|---|
| REV 1 (ignored folder invisible to G10, TREE) | `hidden_files`, `tree_fingerprint` no longer honours ignore rules under the fingerprint paths; a hidden file fails G10 even in scope. `load_domains` already imports listed domains only (WP1) | `test_rev1_*` |
| REV 2 (a copy's own `.gitignore`) | `isolated_changes` compares every path of the base listing; new files judged by the base commit's `.gitignore` from the object store | `test_rev2_*` |
| REV 3 (unchecked-hash `.pyc`) | `PYTHONPYCACHEPREFIX` on a fresh folder for every worker and green_tests.py, `sys.pycache_prefix` in the gate process; a `.pyc` outside `__pycache__` is a hidden file | `test_rev3_*`, `test_rev1_git_info_exclude...` |
| REV 4 (stale base) | G10 lists where the copy differs from HEAD; `stale_base` fails TREE; a package's own committed files are not staleness | `test_rev4_*` |
| REV 8 (perf switch read outside a test body) | the scan follows constants, module flags, helpers, helper methods, setUp, decorators; `mark_problems` fails G1 and green_tests.py; a new skipping test is a G1 regression | `test_rev8_*` |
| REV 9 (py39_lint false negatives) | grammar checks, PEP 649 forward references, unions of own classes, import aliases, the stdlib tables | `test_py39_lint.CloseOutGapTests` |
| VER2 new (bound returning early when relaxed) | a read of the switch outside the legacy guard is a mark problem | `test_rev8_the_bound_scan...` |
| VER2 new (removing a registered bound's mark) | `registered_without_mark` in G1, compare and green_tests.py | `test_ver2_...`, HardenedStage not needed |
| Merge gate request 4 (test_gate_tool in a copy) | the suite reads the copy's policy files through a test-only source; the gate script itself still trusts no copy | run in a real copy (Results) |

Not mine and left: REV 5-7 and the 10 `closeout_breakages` rows are domain rows; REV 10 and VER2's P1C row are the main session's (rules
and a re-record); `closeout_INT3` requests go to the main session, the domains and WP9. None lies in these files.

## Deviations from the spec (and why)

* **Lens C's plan files are gone** (`S/plans/` was not kept; lens A's and B's were), and three of lens B's and one of lens A's. Those plans
  are rewritten from the reports' tables in `lens_c.json`, which says so. `A-b6bf8052cc5211-01` (the "NOT FRAGILE" label) has no kept plan and
  needs the box's packing helpers: listed in `NO_PLAN`, not replayed.
* **`test_review_cheats.py` cannot test "against the migrated specs" yet**: WP12 has not run. Its rule is 23.6's: caught, or the spec is not
  HARDENED. Until migration the replay is as-if (envelope drafted from the reference's footprint; a label the reference raises is not charged
  to the cheat); `caught_as_if.json` is a ratchet. Once a spec is schema 4 it replays against the spec itself, in the evaluation mode.
* **`test_critique_cases.py` uses three layers**: the domains' real verbs where a small scene exists (the liquids), the kernel's ledger, speech and
  perception evidence over a state written to the case, the statistics and lints for what is a matter of the spec. A case proven by state is a
  test of the evaluator's judgment, not of the domain verb that would produce that state; the domains' own hardening tests cover those.
* **The run fingerprint is computed by the tool, not the verifier**: `verify.py` writes no `evidence.run_fingerprint` and no footprint (WP5's file), so
  `stamp_evidence` runs one more reference pass after each full run at the gate (about 7.8 CPU-s per spec, 22.7's own figure). The footprint is a
  profile hook on one seed, 15-30 % over that pass rather than the design's 2 % module hook on the full run: measured once, so the cost is small.
* **G11 runs one worker per spec and the verifier parallelises nothing inside it**: decision 4's 6 workers apply across specs.
* **The private details and split key**: nothing here reads the key except through `private` / `escapes`; owner paths are tested with a throwaway key.
* **`stratified` thins over-represented strata** rather than sampling a fixed quota: 19.4 states a floor, not a draw size.
* **`world_resolves` passes only with a timing item**: a spec whose hazard is a flag subject fails the check (P4), because the monitor records intervals
  for timing items only. A real case would need the flag's own interval; none exists yet.

## Known gaps

* `reports/verification.jsonl` gets schema-4 records from `--verify` (WP5) or `tier1_gate.py publish`; the two are separate on purpose (the file is tracked,
  so a package's record must not write it).
* The chance-corrected score needs `randomisation.best_constant_share` in the record (WP6 computes it at the full level; `verify.py` must copy it: WP6's request 1 to WP5).
* The rolling run's "adds one run per family" is reported (`added_runs`), not yet acted on by the replay; 22.7's fallback needs a generator call per family.
* `run.branch_diff_for` re-runs the references of a branch spec's coverage seeds once per process and seed (the records carry no diff set: WP5 could store one); cost
  about 1 CPU-s per coverage seed, for branch specs only.

## Requests

See `WP10.json`. The ones that matter most: the **ledger crash on contradictory reports** (WP1 / WP4), `dormant_exemptions` (WP5) and `audit.report`'s DONE rule.

## How to test

```
python -m unittest tests.test_review_tools tests.test_critique_cases tests.test_py39_lint        # also HOUSE_SIM_STRICT=1
python -m unittest tests.test_review_cheats                                                       # minutes: every cheat replayed
python -m unittest tests.test_gate_tool.CarryOverGateTests tests.test_gate_tool.HardenedStageTests tests.test_gate_tool.RunCheckHardenedTests
python tools/orchestration/tier1_gate.py isolate --out DIR --package P2-S4; cd DIR; python -m unittest tests.test_gate_tool       # in a copy
python tools/harden_audit.py --ids T-d389e2979f6aad-01 --level standard --out DIR; python tools/harden_audit.py --cost
python tools/battery_review.py budget; python tools/battery_review.py spotcheck
python tools/evidence_replay.py --changed house_sim/kernel/world.py --no-write --out DIR
python tools/orchestration/run_check.py run FILE --level quick --log DIR/log.jsonl; python tools/orchestration/run_check.py verify --log DIR/log.jsonl --rerun 1
python tools/orchestration/green_tests.py 3
```
