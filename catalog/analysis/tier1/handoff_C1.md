# Handoff C1 (C-CLOSE): closing stage C

Read before starting: `docs/design/TIER1_INTEGRATION.md` (1 with 1.3, 1.8, 1.9 and its rules; 2.2, 2.5 with the C1
row; 3; 5), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_SWEEP.md` (18, 19, 20, 22). Handoff files read (all
that existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`,
`handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`,
`handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`,
`handoff_NAV-M2.md`, `handoff_BODY-CORE.md`, `handoff_BODY-FAULTS.md`, `handoff_B6.md`, `handoff_TIME-EVAL.md`,
`handoff_PEOPLE-IO.md` (with their merge, STAGE-B-FIX, C-MERGE and STAGE-C-FIX sections). Their items addressed to
C-CLOSE are acted on: B6 9 ("C-CLOSE (C1)": re-record `tests/data/spec_baseline.json` from the C1 gate with a
`history` row; its appendix writer is reused), TIME-EVAL 9 item 8 and PEOPLE-IO 9 ("C-CLOSE": the action fingerprint
changed, so evidence, replays and datasets are regenerated). Items that need files outside the C1 list are passed on
(section 9).

## 1. Summary

* Regenerated at code `9fa0c9852da0` (actions `955c5ef9ac14`, harness `7fe761bf713a`, the STAGE-C-FIX gate's): the
  verification evidence (`python -m house_sim --verify`: 241 / 242 candidate entries verified, catalog 241 DONE, 1 STUB,
  2,477 TODO, 1 BLOCKED), the demo replays (66 / 105 sections), the dataset sample (31 records, validates with 0 errors
  and 0 warnings) and the leaderboard (4 baselines x 55 dev episodes, all verified). `%USERPROFILE%\.house_ai` did not
  exist before any step and does not exist after any step.
* The one entry that is no longer DONE is A-d9e7a4d5d112d2-01 (seed 0 fails): TIME-EVAL's reviewed S-R1 change, whose
  spec fix (TIME-EVAL request 1, the safety owner's file) is still open. Its section has no demo until then.
* `tests/data/spec_baseline.json` re-recorded from the C1 gate (one spec line changes: that trial), `history` row added.
* Gate C1 vs STAGE-C-FIX: `compare` PASS (0 verdict, 0 state-digest and 0 observation-digest changes in 2 x 1,210
  trials). The record's FAILs are the known `test_eval_splits.py` `.gitignore` test (G1) and `coverage.csv`, which
  `--verify` writes but the C-CLOSE list lacks (G10; B6 request 2).

## 2. Files

Regenerated (C-CLOSE list): `reports/verification.jsonl`, `reports/verification_run.json`,
`reports/coverage_summary.json`, `reports/COVERAGE.md`, `reports/pending.jsonl` (all by `--verify`);
`reports/leaderboard.md`, `.csv`, `.json`, `.html` (by `regen_leaderboard.sh`); `demos/manifest.json` and
`demos/replays/*` (66 rewritten by `--demo`; git-ignored since the stage-B commit); `datasets/sample/manifest.json`,
`sft.jsonl`, `pairs_trajectory.jsonl`, `pairs_step.jsonl`, `critique.jsonl`; `tests/data/spec_baseline.json`.
Created: this handoff, the gate record `catalog/analysis/tier1/gate/C1/` (git-ignored).
Outside the C-CLOSE list, written by the same `--verify` (deviation 1): `coverage.csv` (one row: the STUB entry).
`--verify` also rewrites `tasks/specs.jsonl`, `tasks/id_to_family.jsonl`, `catalog/entries.jsonl` and
`catalog/manifest.json`; they came out unchanged.
Moved, not deleted: `demos/replays/A-d9e7a4d5d112d2-01.json` (deviation 3).
Regenerated outside the repository's tracked files: `results/base-{random,search,oracle,mock}-dev/` (git-ignored, the
leaderboard's runs).
Not edited: any source, test, spec, doc or tool file. `reports/unit_tests.txt` and the other prototype-era reports are
untouched (request 6).
Line endings: every regenerated text file is LF (the CLI's `write_text` writes CRLF on Windows; the files were
converted afterwards, as B6 did), except `coverage.csv`, which the `csv` module writes and the index stores with CRLF.

## 3. APIs

No code changed. Formats are unchanged: `house-dataset-v3` (stage C adds no observation key to legacy episodes, so no
format bump is needed), `house-viewer-v1`, the demo manifest, the verification records, `house-spec-baseline-v1`.
`tests/data/spec_baseline.json` changes only in content: `gate` "C1", `recorded`, the fingerprints, `summary`, the
`about` text (now names the recording gate instead of B6 only) and a second `history` row:

```
{"by": "C1", "gate": "C1", "date": "2026-10-15", "reason": "stage C closed (kernel v3.1: time, temporal evaluation,
 phases, questions, reports, memory); vs B6 one reviewed verdict change (A-d9e7a4d5d112d2-01 seed 0, TIME-EVAL S-R1)
 and A-d66994a566899c-01's idle_state_hash (STAGE-C-FIX T10; not a field of this file)"}
```

The writer (B6's appendix, extended to keep the existing `history` and append a row; `--by` / `--reason` arguments)
is reproduced in the appendix.

## 4. Deviations from the spec

1. **`coverage.csv` is outside the C-CLOSE list.** `audit.verify` ends with `audit.report`, which rewrites
   `coverage.csv`; with A-d9e7a4d5d112d2-01 now a STUB its row changes (`status`, `fidelity`, `world`, `actions`,
   `verification`, `seeds`, `reason`). Restoring the old file would contradict `reports/*` and `demos/` (`audit.demo`
   reads it), so it stays regenerated and G10 lists it (B6 deviation 1 and request 2, still open: request 2 below).
2. **The gate ran on an isolated copy** (as B6's did), so that G10 sees only C1's changes: the base commit is the
   project tree as STAGE-C-FIX left it (every tracked and untracked, not ignored file, copied before C1's first write
   and committed in a throwaway repository in this round's scratch folder, `core.autocrlf input` like the project); its
   working tree holds C1's regenerated files. The project repository was only read by git. The first record failed one
   extra test, `test_eval_splits.VerifiedSpecsTests.test_report_evidence_must_be_current` (`FileNotFoundError`
   copying `tasks/templates`): git does not carry empty directories, and the project has two (`tasks/templates`,
   `house_sim/data/actions`). Both were created in the copy and G1 re-run (`--only G1`); with them the test passes, as
   it does in the project. The G2 / G3 trials and the code fingerprint do not depend on them (empty globs).
3. **The stale replay is moved, not deleted.** `audit.demo` only overwrites the replays it writes; the replay of
   A-d9e7a4d5d112d2-01 (B6's, old action fingerprint, refused by `HouseEnv.replay`) is no longer in the manifest. It was
   moved to this round's scratch folder (`t1_C1/stale_replays/`) so `demos/replays` holds exactly the 66 replays the
   manifest lists (B6 deleted stale replays; deleting is left to the main session).
4. **Gate extras.** C-CLOSE lists none; the record ran `--extras physics,camera,body,gen,sweep` so the close record
   carries every audit (B6 ran all but `sweep`, which did not exist then). G9's `physics_audit.py --data --bench`
   writes its JSON to `%TEMP%\house_ai_tier1\physics_audit.json` (the gate passes no `--out`), outside this round's
   scratch folder; every earlier gate did the same (request 5).
5. **`regen_leaderboard.sh`** still starts with `cd %PROJECT%` (B6 request 4 open); it was run unchanged except for
   that line, with the project path substituted, from a copy in scratch.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim tools tests docs` finds nothing outside `docs/design/` (unchanged since C-MERGE).
Contracts consumed: `audit.verify` / `report` / `demo`, `house_sim.eval.dataset.export` and `validate`, the harness and
leaderboard CLI, the gate's trial records (`specs_own.jsonl`, `specs_realistic.jsonl`, `meta.json`) for the spec
baseline.

## 6. Gate results

Gate id **C1**, package **C-CLOSE**, previous gate **STAGE-C-FIX**, record in `catalog/analysis/tier1/gate/C1/`
(2026-10-15, 7 jobs, 34 min; G1 re-run afterwards, deviation 2), taken on the isolated copy:

```
python tools/orchestration/tier1_gate.py --root <scratch>/t1_C1/iso --gates-root catalog/analysis/tier1/gate record --id C1
       --package C-CLOSE --prev catalog/analysis/tier1/gate/STAGE-C-FIX --out catalog/analysis/tier1/gate/C1
       --work <scratch>/t1_C1/gate_work --jobs 7 --extras physics,camera,body,gen,sweep
(the same with --only G1 after the two empty directories were added; --only G10 after this handoff and
 tests/data/spec_baseline.json were copied into the isolated copy)
```

Summary line: `TIER1-GATE C1 [C-CLOSE] vs STAGE-C-FIX: FAIL (10 pass, 2 fail, 1 skip)`; `compare` passes.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `955c5ef9ac14` unchanged (425 actions, 111 systems) |
| G1 | FAIL | 55 files, 3,849 tests (normal), normal + strict; only `test_eval_splits.py` `PackagingTests.test_gitignore_covers_private_artifacts` fails (both runs), as in every stage-C gate (`.gitignore` ignores `demos/replays/`; request 3). `test_eval_dataset.py` passes with the regenerated sample in both runs. The first record also failed `VerifiedSpecsTests.test_report_evidence_must_be_current`: an artefact of the isolated copy (deviation 2), gone in the re-run |
| G1b | PASS | isolated domains: the 8 failing tests known since B6, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 |
| G1c | SKIP | no import-inert modules |
| G2 | PASS | own mode 241/242 (1,210 trials; the known A-d9e7a4d5d112d2-01 seed 0); vs STAGE-C-FIX 0 verdict, 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 225/242 (1,210 trials); 0 / 0 / 0 changes; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit room and geometric: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic (395 succeeded, as before) |
| G7 | PASS | 3.17 ms per substep (STAGE-C-FIX 3.10, +2.3 %, limit 3.255), 1 h wait 1.121 s (1.109, +1.1 %, limit 1.164), quiet day 6.453 s (6.33, +1.9 %, limit 6.647); the simulation code is identical to STAGE-C-FIX's, so this is machine noise |
| G8 | PASS | room golden and golden_all byte-identical (not re-recorded) |
| G9 | PASS | `physics_audit --data --bench` ok (warnings above budget, not over the fail line: `environment_windy_ms` 0.351 / 0.3, `settle_worst_ms` 10.78 / 10.0; B6 had the same two warnings, 0.363 and 10.34); `body_audit --baseline` ok (0 landing hits); gen-2 / gen-1 time 1.01, `layout_audit 40 --gen 2` ok, `test_generator_compat` and `test_generator_gen2` ok; `sweep_bench` ok: notices 0.0024 ms (0.03), until 0.0180 (0.02), monitor_idle 0.0029 (0.05), monitor_quant 0.0329 (0.5), calendar 0.347 (20), ask 0.587 (2), report 0.070 (1), mind_update 0.147 (1) |
| G10 | FAIL | 18 changed files (the 16 the four commands regenerated, `tests/data/spec_baseline.json` and this handoff); 1 outside the C-CLOSE list: `coverage.csv` (deviation 1, request 2) |

`python tools/orchestration/tier1_gate.py compare STAGE-C-FIX C1 --package C-CLOSE`:

```
compare STAGE-C-FIX -> C1 (packages: C-CLOSE; allowed: nothing; codes: none)
G0   PASS  action fingerprint unchanged (955c5ef9ac14)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 395 -> 395
G7   PASS  substep_ms: 3.1 -> 3.17 (+2.3%; limit 3.255 = prev x 1.05)
           wait_1h_s: 1.1088 -> 1.1214 (+1.1%; limit 1.164 = prev x 1.05)
           day_s: 6.33 -> 6.453 (+1.9%; limit 6.647 = prev x 1.05)
           largest per-system increases: food.safety +0.059 ms
G1   INFO  test counts unchanged
compare result: PASS
```

**Reviewed differences:** none (none allowed, none needed: no verdict, state or observation digest changed).
**Golden:** unchanged, not recorded (the close steps may not record).

**Regenerated evidence** (all at code `9fa0c9852da0`, actions `955c5ef9ac14`, harness `7fe761bf713a`, the gate's):

| Evidence | Command | Result |
|---|---|---|
| `reports/verification.jsonl`, `verification_run.json`, coverage reports | `python -m house_sim --verify --workers 7` (8.5 min; exit 1 because one entry fails) | 241 / 242 candidate entries verified on seeds 0 1 2 41 97 in their own mode; failed: A-d9e7a4d5d112d2-01 (seed 0: `idle_fails` false). Catalog 2,720: 241 DONE (S 84, G 77, P-approx 80), 1 STUB, 2,477 TODO, 1 BLOCKED (B6: 242 DONE). The 1,210 trial verdicts equal the STAGE-C-FIX gate's G2 records one for one, and every reference state digest equals B6's evidence |
| `demos/manifest.json`, `demos/replays/*` | `python -m house_sim --demo` (1.9 min) | 66 of 105 sections demonstrated and replay-verified on seed 701 (B6: 67; the lost section is "Who's asking, and should I? (must-have)", whose only DONE entry was A-d9e7a4d5d112d2-01); 1 failure, unchanged (T-94a0a940224c22-01, "close gate_1 failed: It is already closed"); every other scenario's evaluation identical to B6's; 66 replays, 223 MB |
| `datasets/sample/*` | the `docs/DATASET.md` command (`tools/export_dataset.py ... --seeds-per-entry 1 --verify-now --overwrite`, 34 s), then `tools/validate_dataset.py datasets/sample` | 4 entries verified now, 31 records (sft 4, pairs_trajectory 9, pairs_step 9, critique 9); validation OK, 0 errors, 0 warnings. Against the stage-B sample only `meta.code_fingerprint` and `meta.harness_fingerprint` of each record and the manifest's fingerprints and hashes changed |
| `reports/leaderboard.*` | `tools/orchestration/regen_leaderboard.sh` (6.6 min; deviation 5) | every run complete and verified (`PASSED ... 55/55 episodes verified` x 4); oracle [privileged] 96.4 % [85.5, 100.0], mock / random / search 0.0 %, as at B6. Random's other columns changed (unnecessary questions 7.3 % -> 1.8 %, mean steps 33.7 -> 34.3, sim time 45.1 -> 72.1 s): it samples parameters from the action catalog, whose `ask`, `report`, `wait` and `watch` parameters changed (S-R3, S-R6). 0 absolute home paths in `leaderboard.json` |
| `tests/data/spec_baseline.json` | the appendix writer on `gate/C1` | 242 specs; own 1,209 / 1,210 trials (241 specs on every seed), realistic 1,159 / 1,210 (225); against B6's file only A-d9e7a4d5d112d2-01's own and realistic seed-0 trials differ (`passed` and `idle_fails` true -> false; digests unchanged), plus the header |

`%USERPROFILE%\.house_ai`: checked with `ls` before and after the verification, the demo, the dataset export, the
leaderboard script (its own last line too) and the gate run; it never existed.

**A finding while cross-checking the evidence against the gate** (no defect in the simulator; request 4): for
X-c7a6de4ed9ba04-01 (pets) the gate's `reference_state_hash` differs from the verifier's on all five seeds, at B6 as
now, with identical verdicts. The gate writes spec chunks with `json.dump(..., sort_keys=True)`; the spec's reference
calls the `pets.report` skill with a `fields` dict, which reports in dict order, so the gate runs the reports in sorted
order (the final states differ only in the order of three `event_log` entries). Probed in scratch: the authored spec
gives the verifier's digest in a fresh process, with or without the gate's run-plan spy, after other specs and with
warnings ignored; the key-sorted reference alone gives the gate's.

## 7. Known gaps

* A-d9e7a4d5d112d2-01 stays a STUB in `coverage.csv` / `reports/*` and its section has no demo until the safety spec
  fix (request 1) lands and the entry is re-verified.
* `demos/replays` (223 MB) is git-ignored since the stage-B commit, so the committed `demos/manifest.json` points to
  files a fresh clone does not have (`python -m house_sim --demo` rebuilds them); `test_eval_splits.py` still expects
  them tracked (request 3).
* The dataset exporter does not split multi-phase episodes (TIME-EVAL request 7); no phased spec exists yet, so the
  sample is unaffected. The viewer does not know the extended milestone forms or `phase_done` records (TIME-EVAL
  request 4). Both files are outside C1's list.
* Prototype-era files in `reports/` describe the first prototype (request 6).

## 8. How to test

```
python tools/orchestration/tier1_gate.py compare STAGE-C-FIX C1 --package C-CLOSE
python tools/validate_dataset.py datasets/sample
python -m unittest tests.test_eval_dataset tests.test_eval_leaderboard
python -c "import json; d=json.load(open('tests/data/spec_baseline.json')); print(d['gate'], d['summary']['own']['failing'], d['history'][-1]['by'])"
python -c "import json; print(json.load(open('reports/verification_run.json'))['failed_entries'])"
python -m house_sim --replay demos/replays/T-ce25a2474ffb3d-01.json      # any replay listed in demos/manifest.json
```

## 9. Follow-ups

Requests to the main session (files outside C1):

1. **Safety spec fix (TIME-EVAL request 1, still open)**: `tasks/authored/safety__examples.json`,
   A-d9e7a4d5d112d2-01 variant 0 (milestone `alarm_sounding` + a response `quiet` within 300 s, the exact text is in
   TIME-EVAL's request 1). Then `python -m house_sim --verify-only A-d9e7a4d5d112d2-01`, `python -m house_sim --demo`,
   and re-record `tests/data/spec_baseline.json` with the appendix writer from a gate that includes the fix (a
   main-session commit naming the reason), so the catalog is back to 242 DONE and 67 demonstrated sections.
2. **G10 lists for the close steps** (B6 request 2, still open): add `coverage.csv`, `tasks/specs.jsonl` and
   `tasks/id_to_family.jsonl` to the B-CLOSE and C-CLOSE file lists in `tools/orchestration/tier1_gate.py` (and
   TIER1_INTEGRATION 2.4 / 2.5); `python -m house_sim --verify` writes them.
3. **`tests/test_eval_splits.py` vs `.gitignore`** (TIME-EVAL request 2 = PEOPLE-IO request 5 = B6 request 3): the
   test asserts that demo replays are not ignored; `.gitignore` ignores `demos/replays/`. Decide the replays' home
   (ignored and rebuilt, archived, or compact replays from `audit.demo`) and align the test.
4. **Gate spec serialization**: `tools/orchestration/tier1_gate.py` `write_json` sorts keys, which reorders dicts whose
   order a skill uses (`pets.report` `fields`), so gate digests can differ from the verifier's (section 6). Writing the
   spec chunks (`all_specs.json`, `chunks/*.json`) without `sort_keys` keeps the authored order; it changes
   X-c7a6de4ed9ba04-01's gate digests once (the next gate records them; its verdicts do not change).
5. **Gate G9 physics extra**: pass `--out <gate folder>/physics_audit.json` (or the work folder) to
   `tools/physics_audit.py`, so a gate run writes nothing to `%TEMP%\house_ai_tier1\` and the record keeps the audit.
   The isolated-copy recipe of B6 / C1 also needs the project's empty directories (`tasks/templates`,
   `house_sim/data/actions`; deviation 2).
6. **Stale prototype reports**: `reports/unit_tests.txt`, `unit_test_summary.json` (35 tests, Python 3.12),
   `cli_checks.json`, `package_checks.json`, `package_build.txt`, `compilation_summary.json` (723 candidates) date from
   the first prototype (2026-10-07); no current tool writes them, and they contradict `reports/coverage_summary.json` and
   the gate. Delete them or regenerate equivalents (the gate's `tests.json` has the current test counts). Left in place:
   removing tracked files is the main session's decision.
7. **`regen_leaderboard.sh`**: replace `cd %PROJECT%` with `cd "$(dirname "$0")/../.."` (B6 request 4).
8. **Archive snapshot**: TIER1_INTEGRATION 2.5 lists a snapshot for C1; this round was told not to snapshot or commit,
   so `tools/orchestration/snapshot.py tier1_C` (and the stage-C commit) are the main session's.
9. Still open from earlier handoffs and not C1's: TIME-EVAL requests 3, 4, 6, 7, 9 and the STAGE-C-FIX follow-ups;
   PEOPLE-IO request 4; B6 request 5 (stale `README.md`, `FINAL_REPORT.md`, `docs/ARCHITECTURE.md`; `docs/GENERATOR.md`
   still lists `ottoman` as missing).

For later packages: the adoption round and stage D compare with gate **C1** (`--prev catalog/analysis/tier1/gate/C1`)
or with `tests/data/spec_baseline.json`, which now holds C1's verdicts and digests.

## Appendix: the spec-baseline writer (C1 version)

```python
"""Write tests/data/spec_baseline.json from a tier-1 gate record (B6's writer, handoff_B6.md appendix, extended by C1).

usage: python write_spec_baseline.py GATE_DIR OUT_JSON --by ID --reason TEXT

One line per spec (sorted by catalog id) so the file diffs well; the trial fields are the gate's G2 / G3 verdicts and
reference digests (tools/orchestration/tier1_gate.py trial records). The `history` rows of an existing OUT_JSON are
kept and one row is appended for this recording.
"""
import argparse
import datetime
import json
from pathlib import Path

VERDICT = ("passed", "initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran", "perceivable")
DIGESTS = ("reference_state_hash", "reference_obs_hash", "reference_steps", "reference_time_s")
MODES = (("own", "specs_own.jsonl"), ("realistic", "specs_realistic.jsonl"))
FIELDS = VERDICT + DIGESTS + ("wrong", "error")


def trial_of(record):
    """One trial as a list in FIELDS order (null when the gate did not record the field; "error" only on errors)."""
    out = [record.get(key) for key in VERDICT + DIGESTS]
    out.append([[bool(w.get("fails")), bool(w.get("ran"))] for w in record.get("wrong") or []])
    out.append(str(record["error"])[:200] if record.get("error") else None)
    return out


def main(gate_dir, out_path, by, reason):
    gate = Path(gate_dir)
    out_path = Path(out_path)
    meta = json.loads((gate / "meta.json").read_text(encoding="utf-8"))
    specs, summary = {}, {}
    for mode, name in MODES:
        rows = [json.loads(line) for line in (gate / name).read_text(encoding="utf-8").splitlines() if line.strip()]
        trials = passed = 0
        failing = {}
        for row in rows:
            cid = row["catalog_id"]
            spec = specs.setdefault(cid, {"file": row.get("file"), "spec_hash": row.get("spec_hash"),
                                          "own_mode": row.get("own_mode")})
            if spec["spec_hash"] != row.get("spec_hash"):
                raise SystemExit("spec hash differs between modes for " + cid)
            spec.setdefault(mode, {})[str(row["seed"])] = trial_of(row)
            trials += 1
            passed += bool(row.get("passed"))
            if not row.get("passed"):
                failing.setdefault(cid, []).append(row["seed"])
        summary[mode] = {"trials": trials, "passed_trials": passed, "specs": len({r["catalog_id"] for r in rows}),
                         "specs_passing_every_seed": len({r["catalog_id"] for r in rows}) - len(failing),
                         "failing": {cid: sorted(seeds) for cid, seeds in sorted(failing.items())}}
    history = []
    if out_path.exists():
        history = list(json.loads(out_path.read_text(encoding="utf-8")).get("history") or [])
    history.append({"by": by, "gate": meta.get("id"), "date": datetime.date.today().isoformat(), "reason": reason})
    fps = meta.get("fingerprints") or {}
    head = {
        "about": "Verdicts and reference digests of every authored spec on seeds 0, 1, 2, 41, 97 in its own perception "
                 "mode ('own') and with perception forced to realistic ('realistic'), recorded from the tier-1 gate "
                 "record named in 'gate' (first B6, B-CLOSE; re-recorded by C1, C-CLOSE). It is the baseline of "
                 "TIER1_SWEEP 19.2: a later package compares its verdicts and reference state digests with these "
                 "(the gate's compare does it from the gate records; this file is the committed copy, since gate "
                 "folders are not committed). Only the close steps (B6, C1) or a main-session commit naming the round "
                 "and the reason re-record it; 'history' lists every recording.",
        "format": "house-spec-baseline-v1",
        "gate": meta.get("id"),
        "recorded": (meta.get("finished") or meta.get("recorded") or datetime.date.today().isoformat()),
        "fingerprints": {"actions": fps.get("actions_fingerprint"), "code": fps.get("code_fingerprint"),
                         "harness": fps.get("harness_fingerprint")},
        "seeds": meta.get("seeds"),
        "modes": {"own": "each spec's own perception mode (oracle unless the spec declares realistic), room camera",
                  "realistic": "perception forced to realistic, room camera"},
        "trial_fields": list(FIELDS),
        "trial_doc": "specs.<id>.<mode>.<seed> = a list in trial_fields order: the verdict fields, the reference "
                     "plan's final state and observation digests (house_sim.kernel.predicates.digest), its steps and "
                     "simulated seconds, wrong = [fails, ran] per wrong plan in spec order, error (null when none)",
        "summary": summary,
        "history": history,
    }
    lines = ["{"]
    for key in ("about", "format", "gate", "recorded", "fingerprints", "seeds", "modes", "trial_fields", "trial_doc",
                "summary", "history"):
        lines.append(" {}: {},".format(json.dumps(key), json.dumps(head[key], sort_keys=True, ensure_ascii=False)))
    lines.append(' "specs": {')
    items = sorted(specs.items())
    for i, (cid, spec) in enumerate(items):
        lines.append("  {}: {}{}".format(json.dumps(cid), json.dumps(spec, sort_keys=True, separators=(",", ":")),
                                         "," if i < len(items) - 1 else ""))
    lines.append(" }")
    lines.append("}")
    text = "\n".join(lines) + "\n"
    json.loads(text)
    out_path.write_bytes(text.encode("utf-8"))
    print("wrote", out_path, len(text), "bytes;", len(items), "specs;", json.dumps(
        {m: {k: v for k, v in s.items() if k != "failing"} for m, s in summary.items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("gate_dir")
    parser.add_argument("out_json")
    parser.add_argument("--by", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    main(args.gate_dir, args.out_json, args.by, args.reason)
```
