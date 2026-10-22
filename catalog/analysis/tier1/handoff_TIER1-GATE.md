# Handoff TIER1-GATE (A0): the tier-1 gate and the sweep's Phase 0

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_SWEEP.md` (3, 6.3-6.6, 7, 8.2, 9, 12, 14.1-14.3, 16, 18, 19, 22.1). Handoff files read: none
existed (`catalog/analysis/tier1/handoff_*.md` was empty; A0 is the first package).

## 1. Summary

* `tools/orchestration/tier1_gate.py`: the gate G0-G10 of TIER1_INTEGRATION section 3 with `record` (alias `run`),
  `compare`, `scope`, `packages` and `gen1-digests`; `--package` applies a package's file list, allowed difference
  classes, R codes, benchmark budget, extras and import-inert modules (the table in appendix A).
* Sweep Phase 0: `wait`, `watch`, `cancel_watch`, `run_pending`, `watch_condition_visible` and their helpers moved
  unchanged from `systems/core.py` to the new `house_sim/kernel/timeline.py` (imported by `engine.py`; `core.py`
  re-exports every public name). Six stub modules with the frozen sweep 16.1 signatures; `tests/data/gen1_digests.json`.
* The A0 baseline is recorded in `catalog/analysis/tier1/gate/A0/` and compared with the same gate run on a pristine
  copy of HEAD from before the move: no verdict, state-digest or observation-digest change; only the action
  fingerprint changed (the allowed A0 difference).

## 2. Files

Created: `tools/orchestration/tier1_gate.py`, `house_sim/kernel/timeline.py`, `house_sim/kernel/dialogue.py`,
`house_sim/kernel/reports.py`, `house_sim/kernel/mind.py`, `house_sim/kernel/monitor.py`, `house_sim/kernel/phases.py`,
`house_sim/generator/profile.py`, `tests/data/gen1_digests.json`, `catalog/analysis/tier1/gate/A0/*` (gate output),
`catalog/analysis/tier1/handoff_TIER1-GATE.md` (this file).
Edited: `house_sim/systems/core.py` (the moved code removed, re-exports added, module docstring),
`house_sim/kernel/engine.py` (imports `timeline`; `_fire_orders` calls `timeline.watch_condition_visible` instead of
importing it from `systems.core`), `tools/orchestration/README.md` (gate section).
All are on the A0 list of TIER1_INTEGRATION 2.3; G10 confirms it (section 6).

## 3. APIs

**`house_sim/kernel/timeline.py`** (owner from now on: BODY-ENGINE edits `wait` in B4, TIME-EVAL owns it in stage C)

* Moved unchanged (code, parameters, doc strings, messages, registration names): actions `wait`, `watch`,
  `cancel_watch`, `run_pending`; `watch_condition_visible(w, rule, realism=None)`, `_visible(w, eid)`; the sets
  `OBSERVABLE_FIELDS`, `AGENT_OBSERVABLE_FIELDS`, `REALISTIC_WATCH_AGENT_FIELDS` and the tuple `NUMERIC_OPS`.
* Stage C contract of TIER1_SWEEP 16.1, marked `TIER1-STUB(TIME-EVAL)`: `NOTICE_KINDS = {}`;
  `perceivable(w, rule, realism=None)` (returns today's rule, `watch_condition_visible`, until TIME-EVAL adds the
  three fixes of sweep 6.5); `register_notice_kind(kind, doc, implies=())`, `robot_perceives(w, notice)`,
  `run_auto_orders(env, idle_ctx)` raise `NotImplementedError`. Nothing calls them.

**`house_sim/systems/core.py`** re-exports `AGENT_OBSERVABLE_FIELDS`, `NUMERIC_OPS`, `OBSERVABLE_FIELDS`,
`REALISTIC_WATCH_AGENT_FIELDS`, `cancel_watch`, `run_pending`, `wait`, `watch`, `watch_condition_visible` (the same
objects: `core.OBSERVABLE_FIELDS.add(...)` still extends the rule the timeline uses).

**Stubs** (sweep 16.1 frozen signatures; every function or method raises `NotImplementedError`; nothing registered;
nothing imports them; G1c checks they are import-inert):

* `kernel/dialogue.py` (PEOPLE-IO): `Subject = namedtuple("Subject", "kind id words")` (defaults `id=None`,
  `words=()`); `resolve_subject(env, pid, about)`, `match_fact(state, subject, aspect, detail)`,
  `necessary(env, pid, fact, subject)`, `route(name, aspects, fn)`, `answer(ctx, args)`,
  `canonical_key(aspect, subject, detail)`, `vocabulary()`.
* `kernel/reports.py` (PEOPLE-IO): `normalize(args, state)`, `record(ctx, record)`, `match(state, expectation)`,
  `match_set(state, spec)`, `convert(value, unit, quantity)`, `format_quantity(state, value, quantity)`,
  `issue_words(category)`.
* `kernel/mind.py` (PEOPLE-IO): `class Mind` with `from_config(cls, config, env)` (classmethod), `update(env,
  perceived_ids)`, `view()`, `recall(prefix)`, `snapshot(name, env)`, `diff(name, env)`, `dump()`, `load(data)`.
* `kernel/monitor.py` (TIME-EVAL): `class Monitor(task)` with `sample(env, point)`, `record()`, `load(data)`,
  `decided(state)`.
* `kernel/phases.py` (TIME-EVAL): `class PhaseController(task)` with `active()`, `end(env, reason)`,
  `start_next(env)`, `record()`.
* `generator/profile.py` (GEN): `compose(rng, opts)` (sweep 14.2). The profile and habit functions are GEN's to add
  (no signature is frozen for them; only the generator calls them).

**`tests/data/gen1_digests.json`** (format `house-gen1-digests-v1`): `defaults` = `[{"seed", "digest"}]` for seeds
0-19 with no options; `spec_houses` = `[{"house", "seed", "specs", "digest"}]` for the 66 distinct `house` blocks of
the 242 authored specs (after `spec.expand` and `spec.choose_variant(spec, seed)`) on seeds 0, 1, 2, 41, 97 (320
entries); `digest = house_sim.kernel.predicates.digest(generate_house(seed, **house))` with every domain loaded;
`history` lists each recording. GEN's `tests/test_generator_compat.py` must reproduce every entry (calling
`generate_house(seed, **house)`, or with `gen=1` once that option exists). `tier1_gate.py gen1-digests` checks the file
(it is also part of G5); `gen1-digests --write --by <ID> --reason TEXT` re-records it (main session or a round that
changes houses on purpose, sweep 14.1).

**`tools/orchestration/tier1_gate.py`** (main session; packages run it, never edit it)

```
python tools/orchestration/tier1_gate.py record --id <ID> [--package <PKG>[+<PKG>]] [--prev <ID>|<DIR>|none]
       [--extras physics,camera,body,sweep,gen] [--camera room|geometric] [--only G2,G3] [--skip G7] [--jobs N]
       [--seeds 0 1 2 41 97] [--shared-tree] [--reviewed FILE] [--bench-repeats 5] [--spec-files GLOB]
       [--root DIR] [--out DIR] [--work DIR]
python tools/orchestration/tier1_gate.py run ...                       (alias of record; the 3.1 name)
python tools/orchestration/tier1_gate.py compare <PREV> <ID> [--package <PKG>] [--reviewed FILE]
python tools/orchestration/tier1_gate.py scope --package <PKG> [--shared-tree]        (G10 alone, seconds)
python tools/orchestration/tier1_gate.py packages [<PKG> ...]                         (appendix A)
python tools/orchestration/tier1_gate.py gen1-digests [--write --by <ID> --reason TEXT]
```

What each check does (the pass rules with a previous gate are those of TIER1_INTEGRATION 3.2-3.4):

| Check | Runs | Pass condition |
|---|---|---|
| G0 | `engine.actions_fingerprint()`, `audit.fingerprint()`, `audit.harness_fingerprint()` | action fingerprint unchanged vs the previous gate unless the package may change it (A0, CAM-ACTIONS, TIME-EVAL, PEOPLE-IO, adoption) |
| G1 | every `tests/test_*.py` as `python -m unittest tests/<file>`, normal and `HOUSE_SIM_STRICT=1` | every file green in both runs |
| G1b | `HOUSE_SIM_DOMAINS=<d> python -m unittest tests.test_<d>` for the 16 domains; kernel test files (`test_kernel*`, `test_perception`, `test_physics_*`, `test_sight`, `test_camera*`, `test_body_*`, the sweep's tests) with `HOUSE_SIM_DOMAINS=core` | no failing test that was not failing in the previous gate (test level); kernel files all green from B1 on (recorded before) |
| G1c | imports the package's library modules (`inert` globs) in a process where every domain, the engine and the runner are loaded, and diffs the sizes of every `kernel.plugins` table | nothing registered, no import error (1.16) |
| G2 | `verify.run_trial(spec, seed)` for the 242 specs x seeds 0 1 2 41 97 in their own perception mode, 3 specs per worker process | no unreviewed verdict change; digest changes only for packages allowed to make them; with no previous gate every spec must pass |
| G3 | the same with `perception="realistic"` (and `--camera geometric` through `run_trial(camera=)` once CAM-ACTIONS adds it) | as G2; recorded only when there is no previous gate. The 50 specs that are realistic in their own mode run in both G2 and G3: their records must be identical (cross-process determinism) |
| G4 | `tools/perception_audit.py 6 150 --workers 4 --json`; also `--camera geometric` when the tool has it and the package needs it | exit 0 (no leak, no crash) |
| G5 | `tools/layout_audit.py 40 --json` and `generate_house` for every entry of `tests/data/gen1_digests.json` (4 worker processes) | layout counts by (kind, furnisher) equal to the previous gate; every generation-1 digest equal to the file |
| G6 | `tools/fuzz.py 6 300 --workers 4 --json` (strict, with its cross-process replay); also `--camera geometric` and `--battery 5 --faults random` when the tool has them | exit 0 (no defect, no nondeterministic replay) |
| G7 | alone, after everything else: `tools/bench_systems.py 40 0 1 2 3` five times (best total ms per substep and per system) and the gate's own 1 h wait on fresh houses of seeds 0-3 (best of five per seed, summed); `bench_systems.py --day` once it exists | `substep_ms` and `wait_1h_s` <= reference x factor (default previous gate x 1.05; PHYS-INT A0 x 1.0); recorded only without a previous gate |
| G8 | `python -m unittest tests.test_room_golden`, then with `HOUSE_SIM_GOLDEN_ALL=1` | green; required from B1 on (skipped before: PHYS-INT creates it) |
| G9 | extras: `physics` `tools/physics_audit.py --data --bench` (run with the benchmarks); `body` `tools/body_audit.py --baseline --out <gate folder>/body_audit.json` (B0's is the body baseline); `gen` `tests.test_generator_compat`, `tests.test_generator_gen2`, `layout_audit.py 40 --gen 2` when supported and the gen-2 / gen-1 generation time on seeds 0-9 (<= 1.20); `sweep` `tools/sweep_bench.py`; `camera` the geometric G4/G6 variants | exit 0 / within budget; a missing tool fails only for its creator and later packages |
| G10 | `git diff --name-only HEAD` plus `git ls-files --others --exclude-standard` | every changed file is in the package list, its handoff, this gate's folder or the package's own gate folder; with `--shared-tree` a concurrent package's files, handoff and gate folder are attributed to it |

A trial record (one JSON line in `specs_own.jsonl` / `specs_realistic.jsonl`) holds: `key`, `catalog_id`, `file`,
`spec_hash`, `mode`, `own_mode`, `seed`, `perception`, `variant`; the verdict fields `passed`, `initial_fails`,
`idle_fails`, `reference_passes`, `perceivable`, `wrong_fails`, `wrong_ran`, `wrong[].fails/ran/refusals/name`; the
digests `reference_state_hash`, `reference_obs_hash` (digest of the reference env's final `observe()`),
`idle_state_hash`, `wrong[].state_hash`, `initial_hash`, `task_hash`, plus `reference_steps`, `reference_time_s` and
truncated error texts. Difference classes used by `compare`: **verdict** (any verdict field), **state** (every state
digest, steps, time), **obs** (the observation digest), plus **fingerprint**, **layout** and **gen1**.

Reviewed differences (3.3) are read from the package's handoff: any markdown table row naming a catalog id and one of
the package's codes (columns: catalog id | seed(s) or `all` | mode `own` / `realistic` / `both` | old -> new | code |
evidence | decision); or from `catalog/analysis/tier1/gate/<ID>/reviewed.json` / `--reviewed FILE`
(`[{"catalog_id", "seeds": [..] | "all", "mode", "code", "decision"}]`). A row only counts when its code belongs to
the package (appendix A); a verdict change without a covering row fails G2/G3.

Default previous gate: stage A packages and B0 compare with A0, B1 with B0, everything else with the latest complete
gate of an earlier stage (packages of one sub-stage never with each other). The summary line names the gate used;
pass `--prev` when in doubt.

## 4. Deviations from the spec

1. **Gate commands.** `record` is the primary name (the task brief's word); `run` is an alias so the command lines of
   TIER1_INTEGRATION 3.1 work unchanged. Added `scope`, `packages` and `gen1-digests`.
2. **Two checks beyond G1-G10.** G0 makes the "action fingerprint must not change" rule of 3.2 checkable; G1c makes the
   import-inert rule of 1.16 checkable. Both are cheap.
3. **G2/G3 do not call `verify_many`.** They call the same per-trial function, `verify.run_trial`, in the gate's own
   worker pool (3 specs per process, shared with the unit tests), so the trial results are the verifier's; the gate
   wraps `verify.run_plan` / `verify.idle` inside the worker only to read the extra digests. The human-readable
   equivalent is `python -m house_sim --check <work>/all_specs.json --workers 4` (the gate writes that file).
4. **More digests per trial** than 3.1 lists (`reference_obs_hash`, `idle_state_hash`, wrong-plan `state_hash`,
   `initial_hash`, `task_hash`), grouped into the classes *state* and *obs*. The mapping of the 3.2 rows to classes is
   my reading: PHYS-INT, BODY-ENGINE(+NAV-M2), BODY-CORE(+NAV-M5) and the stage D / adoption packages may change both;
   BODY-FAULTS and PEOPLE-IO only *obs* (realistic `obs.robot`, `obs.notices` / `obs.request`); TIME-EVAL neither
   (S-R4: new sections only when new features are used); everyone else nothing.
5. **G1b known failures.** At HEAD 8 domain tests in 6 files fail when their domain is loaded alone, and
   `test_perception.py` fails with `HOUSE_SIM_DOMAINS=core` (section 9 lists them). They are not A0's (the same gate
   run on a pristine HEAD copy shows the same failing tests) and A0 may not edit domain tests, so G1b compares at test
   level: a failing test that already failed in the previous gate is carried as known; any new failing test fails
   the gate. The kernel-only files must still be fully green from B1 on (PHYS-INT is blocked by the pre-existing
   `test_perception.py` failure unless the main session fixes it first: section 9).
6. **G7.** `bench_systems.py` prints its 1 h wait with two decimals on one seed, too coarse for a 5 % budget, so the
   gate adds its own 1 h wait on fresh houses of seeds 0-3 (best of five per seed, summed: `wait_1h_s`). Both
   bench_systems metrics are recorded (best of five); the budget applies to `substep_ms` and `wait_1h_s`.
7. **G5 also checks generation-1 houses** against `tests/data/gen1_digests.json` (3.2 says every package keeps
   generation-1 houses; until GEN's compat test exists this is the only check).
8. **`tests/data/spec_baseline.json` is not written at A0.** 2.4 gives it to B6 ("the B6 gate's verdicts and
   reference digests in the sweep's format"); the gate's baseline is `catalog/analysis/tier1/gate/A0/specs_*.jsonl`.
   B-CLOSE can derive it from its own gate record.
9. **Timeline move scope.** Besides the names listed in sweep 22.1, `OBSERVABLE_FIELDS`, `AGENT_OBSERVABLE_FIELDS`
   and `_visible` moved too (they are only used by `watch_condition_visible`; keeping them in `core.py` would make
   the kernel import a domain module). `core.py` re-exports the public names; `_visible` is private and not
   re-exported. The `validate_predicate` import in `core.py` is now unused and was left in place (like the already
   unused `MISSING` and `differences`) to keep the edit minimal.
10. **Stage C names in `timeline.py`.** The sweep 16.1 timeline names exist as `TIER1-STUB(TIME-EVAL)` stubs;
    `perceivable` already returns today's rule so callers written in stages A/B get today's behaviour.
11. **Package table interpretations** (appendix A): BODY-CORE and CAM-FLIP may edit `tasks/authored/*.json` (B-R7 says
    reference hits are fixed in their specs before landing; D3 fixes specs or pins them); `TIME-EVAL-LIB` and
    `PEOPLE-IO-LIB` are ids for the stage A/B library work of 2.5; `ADOPT:<domain>`, `PHYS-FLIP` (D1), `M-GEN2` (D2)
    and `CAM-FLIP` (D3) cite the codes `ADOPT`, `D1`, `D2`, `D3` in their reviewed rows; NAV-M2 / NAV-M5 run together
    with their body package as `--package BODY-ENGINE+NAV-M2` / `BODY-CORE+NAV-M5`. The main session can change any
    row in `PACKAGES` at the top of the gate.
12. **Default previous gate** (not specified in the doc): stage A and B0 use A0, B1 uses B0, every other gate the
    latest complete gate of an earlier stage.

13. **Stage-A fix pass changes** (2026-10-14, after the stage-A review; this file is A0's): G9 runs
    `body_audit.py --baseline --out <gate folder>/body_audit.json`, so a gate never overwrites the default file people
    use for their own runs and B0's record is the body baseline (`gate/B0/body_audit.json`, which BODY-CORE checks
    `--against`); G10 allows each listed package's own gate folder (`gate/<package or aka>/`) besides the current gate
    id's, and `--shared-tree` attributes a concurrent package's gate folder to it, as it already did its files and
    handoff (PHYS-DATA's request: A0's own uncommitted files were the only thing left outside). The fix pass recorded
    `gate/A-FIX/` with `--package A0+PHYS-DATA+PHYS-STATICS+PHYS-FLUIDS+CAM-SIGHT+BODY-MODEL+GEN`: PASS vs A0
    (12 pass, G8 skip; 0 verdict, state-digest or observation-digest changes; generation-1 340/340; G7 +0.5 %).
    A residual pass over the whole review re-recorded `gate/A-FIX/` on the final tree (full gate; PASS, 12 pass,
    G8 skip; G7 +1.4 % / +1.0 %; 3,147 tests; core-only kernel files 10/10) and took G10 from an isolated copy:
    on the shared tree G10 lists the main session's later edits (`.gitignore`, `stageA_review.json`,
    `tests/test_perception.py`), which belong to no stage-A package. The gate tool itself is unchanged.
    G9's `body_audit.json` is about 1.3 MB per gate folder, next to the 3.4 MB of trial records.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim` lists: `kernel/dialogue.py`, `kernel/reports.py`, `kernel/mind.py`
(PEOPLE-IO removes them), `kernel/monitor.py`, `kernel/phases.py`, the stage C block of `kernel/timeline.py`
(TIME-EVAL), `generator/profile.py` (GEN). Every one waits for its package's implementation; none is called by
anything yet. Contracts consumed from earlier packages: none (A0 is first).

## 6. Gate results

Gate id **A0**, recorded in `catalog/analysis/tier1/gate/A0/` (`summary.txt` is the human report; `meta.json`,
`tests.json`, `specs_own.jsonl`, `specs_realistic.jsonl`, `specs_summary.json`, `audits.json`, `bench.json`,
`scope.json`, `compare.txt` / `compare.json`). This is the baseline every stage A package and B0 compare against.

To prove the move behaviour-preserving, the same gate was first run (G0, G1b, G2-G7) on a pristine copy of HEAD
(`git archive HEAD`, before any A0 edit) as `A0-pre` in my scratch folder, and A0 was recorded with `--prev` = that
run (its summary and meta are kept as `gate/A0/pre_move_summary.txt` and `pre_move_meta.json`; the full A0-pre record
is not in the repository, so later gates use A0 itself as their baseline). The A0 record's comparison:

```
G0   PASS  action fingerprint changed 145dbcf6086d -> 3e6e94c37334 (allowed for this package)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G5   PASS  layout counts equal (0 problems); generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312
G7   PASS  substep_ms: 4.43 -> 4.38 (-1.1%); wait_1h_s: 1.1203 -> 1.102 (-1.6%)
```

The only action-spec difference is the module of `wait`'s callable duration (`house_sim.systems.core` ->
`house_sim.kernel.timeline`), checked separately by diffing every action's `describe()`, every BEFORE/AFTER hook
label, the systems and the hooks before and after the move: one line differs. Saved states and replays recorded before
A0 are refused by `HouseEnv.load` / `replay` (the expected effect of a fingerprint change; regenerated at B6/C1).

A0 checks (final record):

| Check | Status | Result |
|---|---|---|
| G0 | PASS | action fingerprint 3e6e94c37334 (423 actions, 107 systems); changed vs A0-pre (allowed) |
| G1 | PASS | 26 test files x normal + strict: 2,671 tests each, 0 failing runs (9 skipped) |
| G1b | PASS | isolated domains 10/16 files green; 8 failing tests in 6 files, all failing identically on pristine HEAD (known); kernel files with core only 2/3 (test_perception.py, recorded only until B1) |
| G1c | PASS | the six stub modules import without registering anything |
| G2 | PASS | own mode 242/242 specs pass (1,210 trials) |
| G3 | PASS | forced realistic 226/242 specs pass (1,210 trials; recorded as the baseline, not required to pass); 250 realistic-spec trials identical in G2 and G3 |
| G4 | PASS | perception audit 6 houses x 150 steps: 0 leaks, 0 crashes |
| G5 | PASS | layout audit 240 houses: 0 problems; generation-1 digests 340/340 equal to `tests/data/gen1_digests.json` |
| G6 | PASS | fuzz 6 x 300: 1,800 commands, 0 defects, deterministic replays |
| G7 | PASS | 4.38 ms per substep (best of 4.38-4.42), 1 h wait 1.102 s summed over seeds 0-3 (0.23 / 0.30 / 0.30 / 0.27 s) |
| G8 | SKIP | `tests/test_room_golden.py` does not exist yet (PHYS-INT creates it) |
| G9 | SKIP | no extras for A0 |
| G10 | PASS | every changed file is on the A0 list |

Forced-realistic failures recorded in the baseline (specs authored for oracle verification; not A0's to fix):
A-8220afe27c03e7-01, A-b4642738e5e520-01, T-13da00933a0800-01, T-cc81e71dc2182e-01, T-e40eb6a314e5b6-01,
X-404f39e74f6d5f-01 (all seeds); T-79a861877a2a67-01, T-8b4654687e040f-01, T-8e57df5742271b-01 (seeds 1, 41, 97);
T-dda4e199f3f5b0-01 (0, 1, 97); T-3c0e5bb4fb4063-01, X-fee35ef3aac659-01 (0, 2); T-28ff695081cd2c-01 (1);
T-c072906a11f55c-01 (0); T-ec38fe45bca9ab-01 (41); X-bf82170244be22-01 (97). These matter at D3 (section 4).

Benchmark baseline (3.4, conflict 27): 4.38 ms per 30 s substep (bench_systems, seeds 0-3, 107 systems; largest:
kernel thermal 1.69 ms, climate.house 0.58 ms, plumbing water 0.24 ms, electrical power 0.21 ms) and 1.102 s for a
1 h wait over seeds 0-3, measured on this machine (Windows 11, 8 cores, Python 3.14.8). A full gate run took about
23 minutes with 7 slots (unit tests and spec trials about 20 minutes, audits 1.5, benchmarks 1).

## 7. Known gaps

* G9 runs `tools/physics_audit.py --data --bench`, `tools/body_audit.py --baseline` and `tools/sweep_bench.py` by
  exit status only; those tools do not exist yet, so their output formats are unknown. Their packages should make the
  exit status meaningful (non-zero on a budget or baseline breach) or tell the main session what to parse.
* `--camera geometric` for G3 needs `verify.run_trial(..., camera=)` (CAM-ACTIONS adds the plumbing); until then every
  G3 trial records "camera=geometric requested but verify.run_trial has no camera parameter" and fails.
* G7 budgets are 5 % on a Windows desktop. Five repeats spread about 1 % here, but background load can push a run
  over; re-run only the benchmark with `--only G7`. PHYS-INT's "must not exceed A0 at all" (factor 1.0) is the
  tightest rule and the most likely to flap.
* G10 sees the whole working tree. If stage A packages share one tree, pass `--shared-tree` (files of the other
  packages of the same sub-stage are attributed, not failed); a package editing a sibling's file is then not caught.
* The trial records are about 1.6 MB per mode (3.4 MB per gate folder). If that is too much for git, the main
  session can commit only `summary.txt`, `meta.json`, `compare.*` and the json summaries and keep the jsonl files in
  `archive/`.
* No unit tests for the gate itself; it was exercised end to end (A0-pre, A0, a partial re-run, `gen1-digests` in two
  processes with different `PYTHONHASHSEED` and worker partitions: 340/340 equal).

## 8. How to test

```
# the whole gate (about 25 minutes on 8 cores); prints the table and the summary line, exit 0 on PASS
python tools/orchestration/tier1_gate.py record --id <PKG>            # --package defaults to the id
python tools/orchestration/tier1_gate.py record --id BODY-ENGINE --package BODY-ENGINE+NAV-M2
python tools/orchestration/tier1_gate.py record --id B0                # main session; previous gate A0
# re-run one flaky check and keep the rest of the record
python tools/orchestration/tier1_gate.py record --id <PKG> --only G7
# while iterating: one spec file only (a partial record, never a baseline)
python tools/orchestration/tier1_gate.py record --id try --out <scratch> --only G2,G3 --spec-files "cleaning__*"
# comparisons and quick checks
python tools/orchestration/tier1_gate.py compare A0 <PKG> --package <PKG>
python tools/orchestration/tier1_gate.py scope --package <PKG>
python tools/orchestration/tier1_gate.py gen1-digests
python tools/orchestration/tier1_gate.py packages <PKG>
```

Phase 0 checks: `python -m unittest tests.test_kernel` (wait/watch/run_pending tests), the G2/G3 comparison above, and
`python -c "from house_sim.systems import core; from house_sim.kernel import timeline; assert core.wait is
timeline.wait and core.OBSERVABLE_FIELDS is timeline.OBSERVABLE_FIELDS"`.

## 9. Follow-ups

* **Before B1 (main session):** `tests/test_perception.py` fails with `HOUSE_SIM_DOMAINS=core` at HEAD
  (`test_generated_houses_leak_no_ids`, `test_realistic_observe_overhead_is_small`: KeyError 'handrail'; the file
  imports `house_sim.systems.navigation_robot.hazards` at line 666, which registers navigation's furnisher in a
  core-only process whose registry lacks navigation's types). G1b requires the kernel files to pass with core only
  from B1 on, and `test_perception.py` belongs to no B1 package; fix the test's guard (or the furnisher's) first.
* **Domain follow-ups (adoption round or earlier):** tests that fail when their domain is loaded alone because they
  use another domain's types or furnishings without a skip guard:
  `test_admin_inventory` (`CrossDomainRoundTests.test_carts_live_next_to_a_garage_with_no_room_for_them`,
  `test_wall_mount_and_surface_fallbacks_stay_reachable`: no toilet), `test_food`
  (`Transfers.test_strained_liquid_keeps_its_provenance`: `stovetop_kettle`), `test_manipulation`
  (`KernelRound5AdoptionTests.test_an_unidentified_item_held_by_a_safe_grip_is_judged_by_that_grip`,
  `test_safe_handling_never_picks_a_grip_from_an_unidentified_items_hidden_safe_grips`: `antifreeze_jug`),
  `test_navigation_robot` (`TestKernelV23Layout.test_furnished_robots_sit_on_their_docks_inside_the_grid`),
  `test_people` (`ReferenceTests.test_important_items_may_move_but_not_be_thrown_away`: `kitchen_trash_can`),
  `test_plumbing_liquids` (`PerceptionHardening.test_pouring_from_an_unread_lookalike_names_no_liquid_and_takes_the_same_time`).
  The gate carries them as known; fixing them removes them from the next record.
* `docs/KERNEL_API.md` does not mention `kernel/timeline.py` yet (A0 may not edit docs); the next package that owns
  `KERNEL_API.md` (PHYS-INT in B1) should add one line, and TIME-EVAL documents the module fully.
* GEN: `tests/test_generator_compat.py` reads `tests/data/gen1_digests.json` (format in section 3); G5 already checks
  the same digests for every package.
* B-CLOSE: `tests/data/spec_baseline.json` can be written from the B6 gate record (`specs_*.jsonl`).
* B6/C1: `demos/replays/*` and saved states were recorded with the old action fingerprint and are refused until
  regenerated (expected; 2.3 "A0").

## Appendix A: the package table the gate enforces

`python tools/orchestration/tier1_gate.py packages` prints it from `PACKAGES` in the gate. "May change" lists the
difference classes (section 3) and the codes a reviewed row must cite; every package may always write its own
`catalog/analysis/tier1/handoff_<ID>.md` and `catalog/analysis/tier1/gate/<ID>/`. Requirements by stage: the golden
(G8) and the core-only kernel tests (G1b) from B1; the `--camera geometric` tool variants (G4, G6) from CAM-RENDER;
`--battery`/`--faults` fuzz from BODY-FAULTS; `bench_systems.py --day` (G7) from TIME-EVAL. A missing extra tool fails
only for the package that creates it and later stages.
| Package | Stage | May change (reviewed codes) | Bench budget | Extras | Import-inert | Files (G10) |
|---|---|---|---|---|---|---|
| A0 (TIER1-GATE) | A0 | fingerprint | 1.05 x prev | - | yes | `tools/orchestration/tier1_gate.py`<br>`tools/orchestration/README.md`<br>`house_sim/kernel/timeline.py`<br>`house_sim/systems/core.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/dialogue.py`<br>`house_sim/kernel/reports.py`<br>`house_sim/kernel/mind.py`<br>`house_sim/kernel/monitor.py`<br>`house_sim/kernel/phases.py`<br>`house_sim/generator/profile.py`<br>`tests/data/gen1_digests.json` |
| PHYS-DATA | A | nothing | 1.05 x prev | physics | yes | `house_sim/data/physics/materials.json`<br>`house_sim/data/physics/liquids.json`<br>`house_sim/data/physics/floors.json`<br>`house_sim/data/physics/supports.json`<br>`house_sim/data/physics/laws.json`<br>`house_sim/kernel/phys/__init__.py`<br>`house_sim/kernel/phys/props.py`<br>`house_sim/kernel/phys/lawbook.py`<br>`house_sim/kernel/phys/forces.py`<br>`house_sim/kernel/phys/contain.py`<br>`setup.py`<br>`tests/test_physics_data.py`<br>`tests/test_physics_forces.py` |
| PHYS-STATICS | A | nothing | 1.05 x prev | physics | yes | `house_sim/kernel/phys/support.py`<br>`house_sim/kernel/phys/statics.py`<br>`house_sim/kernel/phys/impact.py`<br>`tests/test_physics_statics.py` |
| PHYS-FLUIDS | A | nothing | 1.05 x prev | physics | yes | `house_sim/kernel/phys/liquids.py`<br>`house_sim/kernel/phys/thermal.py`<br>`house_sim/kernel/phys/environment.py`<br>`house_sim/kernel/phys/processes.py`<br>`tests/test_physics_fluids.py`<br>`tests/test_physics_thermal.py` |
| CAM-SIGHT | A | nothing | 1.05 x prev | camera | yes | `house_sim/kernel/sight.py`<br>`house_sim/kernel/registry.py`<br>`house_sim/data/types/core.json`<br>`tests/test_sight.py` |
| BODY-MODEL | A | nothing | 1.05 x prev | body | yes | `house_sim/kernel/body.py`<br>`house_sim/kernel/body_faults.py`<br>`house_sim/kernel/proprio.py`<br>`house_sim/data/robot/profiles.json`<br>`tools/body_audit.py`<br>`tests/test_body_model.py` |
| GEN | A | nothing | 1.05 x prev | gen | - | `house_sim/generator/profile.py`<br>`house_sim/generator/belongings.py`<br>`house_sim/generator/furnishings.py`<br>`house_sim/generator/climate.py`<br>`house_sim/data/generator/households.json`<br>`house_sim/data/generator/belongings.json`<br>`house_sim/data/generator/furnishings.json`<br>`house_sim/data/generator/climate_normals.json`<br>`house_sim/generator/__init__.py`<br>`house_sim/generator/layout.py`<br>`house_sim/generator/place.py`<br>`house_sim/generator/repair.py`<br>`tools/layout_audit.py`<br>`tools/show_house.py`<br>`tests/test_generator_gen2.py`<br>`tests/test_generator_compat.py`<br>`docs/GENERATOR.md` |
| TIME-EVAL-LIB | A | nothing | 1.05 x prev | - | yes | `house_sim/kernel/monitor.py`<br>`house_sim/kernel/phases.py`<br>`house_sim/data/calendar/*.json`<br>`tests/test_monitor.py`<br>`tests/test_phases.py` |
| PEOPLE-IO-LIB | A | nothing | 1.05 x prev | - | yes | `house_sim/kernel/dialogue.py`<br>`house_sim/kernel/reports.py`<br>`house_sim/kernel/mind.py`<br>`house_sim/data/vocab/*.json`<br>`tools/vocab.py`<br>`tests/test_dialogue.py`<br>`tests/test_reports.py`<br>`tests/test_mind.py` |
| B0 | B0 | nothing | 1.05 x prev | - | - | none |
| PHYS-INT | B1 | state, obs, verdict (R1-R10) | 1.0 x A0 | physics | - | `house_sim/kernel/world.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/actions.py`<br>`house_sim/kernel/physics.py`<br>`house_sim/kernel/phys/*`<br>`house_sim/systems/core.py`<br>`house_sim/tasks/setup.py`<br>`house_sim/tasks/verify.py`<br>`house_sim/data/types/core.json`<br>`house_sim/data/physics/*.json`<br>`tools/physics_audit.py`<br>`tools/golden.py`<br>`tools/perception_audit.py`<br>`tests/test_physics_settle.py`<br>`tests/test_physics_setup.py`<br>`tests/test_room_golden.py`<br>`tests/data/room_golden.json`<br>`docs/KERNEL_API.md`<br>`docs/PHYSICS.md`<br>`docs/CONVENTIONS.md`<br>`docs/AUTHORING_GUIDE.md`<br>`docs/PERCEPTION.md`<br>`docs/KNOWN_LIMITS.md` |
| CAM-RUNTIME | B2 | nothing | 1.05 x prev | - | - | `house_sim/kernel/camera.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/perception.py`<br>`house_sim/kernel/actions.py`<br>`house_sim/kernel/sight.py`<br>`house_sim/kernel/phys/observe.py`<br>`tests/test_camera.py`<br>`docs/KERNEL_API.md` |
| CAM-ACTIONS | B3 | fingerprint | 1.05 x prev | - | - | `house_sim/systems/core.py`<br>`house_sim/kernel/camera.py`<br>`house_sim/kernel/sight.py`<br>`house_sim/kernel/actions.py`<br>`house_sim/tasks/setup.py`<br>`house_sim/tasks/spec.py`<br>`house_sim/tasks/runner.py`<br>`house_sim/tasks/verify.py`<br>`house_sim/tasks/skills.py`<br>`house_sim/cli.py`<br>`tests/test_camera_actions.py`<br>`docs/ACTION_API.md`<br>`docs/TASK_SPEC.md`<br>`docs/AUTHORING_GUIDE.md` |
| CAM-RENDER | B3 | nothing | 1.05 x prev | camera | - | `house_sim/kernel/realism.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/perception.py`<br>`house_sim/agent.py`<br>`house_sim/eval/run.py`<br>`house_sim/eval/agents.py`<br>`house_sim/eval/llm.py`<br>`house_sim/eval/dataset.py`<br>`house_sim/eval/viewer.py`<br>`house_sim/eval/viewer_template.html`<br>`house_sim/eval/leaderboard.py`<br>`tools/perception_audit.py`<br>`tools/fuzz.py`<br>`tools/bench_systems.py`<br>`tests/test_camera_realism.py`<br>`docs/PERCEPTION.md`<br>`docs/VIEWER.md`<br>`docs/EVAL_HARNESS.md`<br>`docs/KNOWN_LIMITS.md` |
| BODY-ENGINE | B4 | state, obs, verdict (B-R1-B-R6) | 1.05 x prev | body | - | `house_sim/kernel/engine.py`<br>`house_sim/kernel/actions.py`<br>`house_sim/kernel/schema.py`<br>`house_sim/kernel/timeline.py`<br>`house_sim/kernel/body.py`<br>`house_sim/kernel/body_faults.py`<br>`house_sim/kernel/phys/forces.py`<br>`house_sim/kernel/phys/support.py`<br>`house_sim/data/robot/profiles.json`<br>`house_sim/systems/core.py`<br>`tests/test_body_engine.py`<br>`tests/test_kernel.py`<br>`tests/data/room_golden.json`<br>`docs/KERNEL_API.md`<br>`docs/CONVENTIONS.md`<br>`docs/KNOWN_LIMITS.md` |
| NAV-M2 | B4 | state, obs, verdict (B-R1-B-R6) | 1.05 x prev | - | - | `house_sim/systems/navigation_robot/traverse.py`<br>`house_sim/systems/navigation_robot/robot.py`<br>`house_sim/systems/navigation_robot/paths.py`<br>`house_sim/systems/navigation_robot/world.py`<br>`tests/test_navigation_robot.py` |
| BODY-CORE | B5 | state, obs, verdict (B-R7-B-R11) | 1.05 x prev | body | - | `house_sim/systems/core.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/body.py`<br>`house_sim/data/robot/profiles.json`<br>`house_sim/tasks/setup.py`<br>`house_sim/tasks/skills.py`<br>`tests/test_body_core.py`<br>`tests/test_kernel.py`<br>`docs/AUTHORING_GUIDE.md`<br>`docs/ACTION_API.md`<br>`tasks/authored/*.json` |
| NAV-M5 | B5 | state, obs, verdict (B-R7-B-R11) | 1.05 x prev | - | - | `house_sim/systems/navigation_robot/paths.py`<br>`house_sim/systems/navigation_robot/follow.py` |
| BODY-FAULTS | B5 | obs (B-R12, B-R13, B-R14, B-R15) | 1.05 x prev | body | - | `house_sim/kernel/perception.py`<br>`house_sim/kernel/realism.py`<br>`house_sim/kernel/body_faults.py`<br>`house_sim/kernel/proprio.py`<br>`tools/perception_audit.py`<br>`tools/fuzz.py`<br>`tests/test_body_faults.py`<br>`tests/test_body_perception.py`<br>`tests/test_perception.py`<br>`tests/data/room_golden.json`<br>`docs/PERCEPTION.md`<br>`docs/EVAL_HARNESS.md` |
| B-CLOSE (B6) | B6 | nothing | 1.05 x prev | - | - | `house_sim/eval/viewer.py`<br>`house_sim/eval/viewer_template.html`<br>`house_sim/eval/dataset.py`<br>`docs/DATASET.md`<br>`docs/VIEWER.md`<br>`docs/KNOWN_LIMITS.md`<br>`house_sim/data/types/core.json`<br>`demos/replays/*`<br>`demos/manifest.json`<br>`reports/*`<br>`tests/data/spec_baseline.json` |
| TIME-EVAL | C | fingerprint, verdict (S-R1, S-R2, S-R3, S-R4) | 1.05 x prev | sweep | - | `house_sim/kernel/monitor.py`<br>`house_sim/kernel/phases.py`<br>`house_sim/data/calendar/*.json`<br>`house_sim/kernel/timeline.py`<br>`house_sim/kernel/engine.py`<br>`house_sim/kernel/world.py`<br>`house_sim/kernel/evaluation.py`<br>`house_sim/kernel/predicates.py`<br>`house_sim/kernel/clock.py`<br>`house_sim/kernel/plugins.py`<br>`house_sim/kernel/actions.py`<br>`house_sim/kernel/physics.py`<br>`house_sim/kernel/camera.py`<br>`house_sim/kernel/phys/observe.py`<br>`house_sim/tasks/spec.py`<br>`house_sim/tasks/runner.py`<br>`house_sim/tasks/verify.py`<br>`house_sim/eval/run.py`<br>`house_sim/eval/agents.py`<br>`house_sim/cli.py`<br>`tools/verify.py`<br>`tools/bench_systems.py`<br>`tools/sweep_bench.py`<br>`tests/test_timeline.py`<br>`tests/test_world_savepoint.py`<br>`tests/test_monitor.py`<br>`tests/test_phases.py`<br>`tests/test_clock.py`<br>`tests/test_predicates_v3.py`<br>`docs/TASK_SPEC.md`<br>`docs/EVALUATION.md`<br>`docs/KERNEL_API.md`<br>`docs/EVAL_CONTRACTS.md` |
| PEOPLE-IO | C | fingerprint, obs (S-R5, S-R6, S-R7) | 1.05 x prev | sweep | - | `house_sim/kernel/dialogue.py`<br>`house_sim/kernel/reports.py`<br>`house_sim/kernel/mind.py`<br>`house_sim/data/vocab/*.json`<br>`tools/vocab.py`<br>`house_sim/systems/core.py`<br>`house_sim/kernel/realism.py`<br>`house_sim/kernel/perception.py`<br>`house_sim/tasks/setup.py`<br>`house_sim/tasks/skills.py`<br>`house_sim/eval/llm.py`<br>`tools/perception_audit.py`<br>`tests/test_dialogue.py`<br>`tests/test_reports.py`<br>`tests/test_mind.py`<br>`tests/test_requests_obs.py`<br>`tests/test_perception.py`<br>`docs/DIALOGUE.md`<br>`docs/PERCEPTION.md`<br>`docs/EVAL_HARNESS.md`<br>`docs/AUTHORING_GUIDE.md`<br>`docs/CONVENTIONS.md` |
| C-CLOSE (C1) | C1 | nothing | 1.05 x prev | - | - | `demos/replays/*`<br>`demos/manifest.json`<br>`reports/*`<br>`datasets/*`<br>`tests/data/spec_baseline.json` |
| PHYS-FLIP (D1) | D1 | state, obs, verdict (D1) | 1.05 x prev | physics | - | `house_sim/data/physics/laws.json`<br>`docs/PHYSICS.md` |
| M-GEN2 (D2) | D2 | state, obs, verdict (D2) | 1.05 x prev | gen | - | `tasks/authored/*.json`<br>`catalog/analysis/gen2_migration.json` |
| CAM-FLIP (D3) | D3 | state, obs, verdict (D3) | 1.05 x prev | camera | - | `house_sim/kernel/camera.py`<br>`docs/PERCEPTION.md`<br>`docs/EVAL_HARNESS.md`<br>`docs/AUTHORING_GUIDE.md`<br>`docs/KNOWN_LIMITS.md`<br>`tasks/authored/*.json` |
| ADOPT:&lt;domain&gt; (e.g. ADOPT:cleaning) | ADOPT | state, obs, verdict, fingerprint (ADOPT) | 1.05 x prev | - | - | `house_sim/systems/<domain>/*`<br>`house_sim/data/types/<domain>.json`<br>`tests/test_<domain>.py`<br>`docs/domains/<domain>.md`<br>`tasks/authored/<domain>__*.json` |

## Appendix B: adoption-round update (K-EVAL, 2026-10-20)

Added by the adoption round's kernel follow-up K-EVAL (details: `handoff_K-EVAL.md`, tools/orchestration/README.md).

* **Packages**: `ADOPT:<domain>` (also the compiled `tasks/specs.jsonl` / `tasks/id_to_family.jsonl` its spec edits
  rewrite), `K-EVAL` (may change nothing), `K-CAM`, and `ADOPT-ALL` (alias `ADOPT`: the union, for the integrator's
  `record --id ADOPT`); the adoption round's default previous gate is C1. Folder names write a package id's colon as
  `_` (`gate/ADOPT_cleaning`, `handoff_ADOPT_cleaning.md`). B-CLOSE / C-CLOSE also list what `--verify` rewrites
  (`coverage.csv`, `tasks/specs.jsonl`, `tasks/id_to_family.jsonl`, `catalog/entries.jsonl`, `catalog/manifest.json`).
* **`--shared-tree`** attributes every adoption package running beside the current one (`SUBSTAGES["ADOPT"]`).
* **The supported `compare` forms**: `compare PREV ID` as before; `compare [PREV] --package PKG [--shared-tree]
  [--spec-files GLOB]` takes the package's own record and the previous gate it names (else the stage default); with
  `--shared-tree` it also checks the working tree's scope (G10) now; without a record it prints the `record` command
  to run (exit 2). A record made with `--spec-files` is compared on those files only (no "missing" trials).
* **`isolate --out DIR [--package PKG] [--files ...]`**: an isolated copy (base commit via `git archive`, the empty
  directories `tasks/templates` and `house_sim/data/actions`, the package's changed files) whose G10 lists exactly that
  overlay; nothing is committed. `record` also creates the empty directories in any root it checks.
* G9's physics audit writes `physics_audit.json` into the gate folder; spec chunks and `all_specs.json` keep the
  authored key order (the gate's digests now equal the verifier's for X-c7a6de4ed9ba04-01; C1 request 4).

## Appendix C: adoption-round integrator update (ADOPT-INT, 2026-10-22)

* **Packages**: `ADOPT-INT` (the integrator: kernel, core, tasks, generator, eval, data/physics, data/vocab, tools,
  kernel tests, `tests/data/*`, top-level docs, `adopt_status.json`, the compiled specs and regenerated evidence) is part
  of `ADOPT-ALL`, which may now also change generation-1 digests (`gen1`); `house_sim/tasks/skills.py` is on K-CAM's
  list. A union package (`ADOPT-ALL`) owns its member packages' handoffs and gate folders in G10.
* **Generation-1 digests** pin generation-1 houses only: `gen1-digests --write` skips spec house blocks with `gen` 2 or
  more (the adoption round moved 233 of 243 specs to generation 2). Re-recorded by ADOPT: 20 default and 40 spec-house
  entries (record fields from domain type data; no placement change).
* **Gate ADOPT** (`gate/ADOPT/`, previous C1, reviewed rows in `gate/ADOPT/reviewed.json`; the domain breakages that
  remain are in `catalog/analysis/tier1/adopt_status.json`).
