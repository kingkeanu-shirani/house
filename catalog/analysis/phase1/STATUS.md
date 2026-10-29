# Phase 1 (stabilise) -- final integration status, 2026-10-28

Integrator of the phase-1 finish: INT2. Inputs: the 17 finish results in this folder (`finish_<domain>.json` for the 16
domains, `finish_K-GATE2.json`), the earlier round's `breakages.json`, `P1_signoff_draft.json` and this file's previous
version (INT, 2026-10-22). Full result: `finish_INT2.json`; domain-side work: `finish_breakages.json` (10 rows);
verified second signatures: `P1_signoff_verified.json`; the package's first signature: `P1F_reviewed.json`; evidence:
`INT2_evidence/`.

## Numbers (final tree)

| Check | Result |
|---|---|
| Unit suite `green_tests.py 5`, normal + `HOUSE_SIM_STRICT=1` | 4,819 tests per mode, 65 files, 0 failing runs (the domains' finished tree before INT2: 4,811, 4 failing runs -- the two pending re-records) |
| All authored specs, own mode (combined file, `--check --workers 4`) | 243 / 243 (also 243 / 243 on the domains' tree before INT2; INT: 242, K-GATE2 mid-round: 239) |
| The 51 realistic specs with `--camera geometric` (each spec's own generation) | 51 / 51 |
| `perception_audit.py 60 80` | walks: 0 leaks, 0 crashes in all four runs (room / geometric x gen 1 / gen 2); the new read probe: 3 leaks (room) and 5 (geometric), all domain type data (below) |
| `layout_audit.py 30` | gen 1: 180 houses, 0 problems; gen 2: 330 houses, 0 problems, 0 domain follow-ups (INT: 4), 709 notes (sofa cushions 330, no `indoor_mat` type 275, diaper bag 40, key bowl 29, stroller 26, ...) |
| `fuzz.py 12 300` | 3,600 commands, 0 defects, 0 nondeterministic replays (46 of 427 actions succeed); `--gen 2` 8 x 300: 0 / 0 |
| `fuzz.py --coverage` | 44,296 commands, 0 defects (admin_inventory's heft defect gone), 0 nondeterministic (plumbing's work-in-progress replays gone), 278 of 427 actions driven to success; no-op only: clean_camera, hold_steady, loosen, open_valve, squeeze, steady, support |
| Bench (G7, alone) | 2.69 ms per substep (ADOPT 3.21, limit 3.371), 1 h wait 1.020 s (ADOPT 1.133, limit 1.189); 27 wall-clock bounds alone, normal + strict: 0 failing every attempt |
| Gate record `record --id P1F --package P1 --prev ADOPT` | FAIL before the main session's signatures; see below |

## What INT2 changed (each with a regression test; `finish_INT2.json` lists them)

* Kernel requests applied: `World._matching` evaluates a declared-reads predicate on plain records inside a step too
  wherever the plain record is the step-start record (seven domains' request; a busy-step build 0.393 -> 0.222 ms on
  929 entities; exact: golden identical), and strict mode checks every declaration, also between steps (electrical);
  `World.journaled_entities()` (cleaning) and `touch_log_complete()` with the touch-log contract in KERNEL_API
  (textiles_laundry); public `environment.cannot_move` / `free_to_move` (outdoor_plants); `pool_l` for cavities
  (appliances_cooking); `@hook("phys.liquid_arrives")` before the kernel pools a pour, drip or overflow
  (outdoor_plants); `contact_heat` duplicated_by lists every legacy copy (food); `read` of an unfamiliar thing shows
  its printed text only (food, review I4).
* Auditor: the read probe (people's request) twins every member of every look-alike group and reads it and reads it
  aloud to a resident; the unfamiliar-twin battery reads too. `RememberedSubjectTests` asks about the owner (people).
* Re-records after attribution on three frozen trees (HEAD / the domains' finished tree / integrated):
  `tests/data/gen1_digests.json` (60 entries; 3 record-field classes, no placement), `tests/data/room_golden.json`
  (43 path patterns, each an owner's reported class -- one class plumbing_liquids did not list, attributed by A/B: water
  vessels' wetness in T-9a6cc7b8c7cefc-01 and T-a56d991fe37401-01; T-7d4006bda247ba-01's known-failing pin dropped,
  it passes); integrated vs finished tree: 0 of 275 golden files changed. `tasks/specs.jsonl` recompiled (the 5
  specs edited this round). docs/GENERATOR.md lists `navigation_robot.dock_check`.

## Gate record P1F

`python tools/orchestration/tier1_gate.py record --id P1F --package P1 --prev ADOPT` on the quiet final tree (2,921 s,
tree unchanged during the run; record in `catalog/analysis/tier1/gate/P1F/`): **FAIL** (7 pass, 5 fail, 1 n/a).

| Check | Result |
|---|---|
| G0 | PASS: action fingerprint unchanged (427 actions) |
| G1 / G8 | PASS: 4,819 tests x normal + strict; golden and golden-all green |
| G1b | FAIL only on 8 count-based skip increases vs ADOPT (ADOPT has no per-test ids): each is a new phase-1 test that skips without other domains (skip reasons read); signed as test-removal rows |
| G2 | FAIL only on signatures: own mode 243/243; 210 verdict changes (all reviewed, code P1), 14 edited specs |
| G3 | FAIL on signatures: forced realistic 242/243 (T-dda4e199f3f5b0-01 seed 1, the pets urgency judgement, as before); 218 verdict changes, 217 reviewed |
| G4 | FAIL: the read probe's domain leaks (room 3, geometric 5); walks 0 leaks, 0 crashes |
| G5 | PASS: layout 240 houses 0 problems; gen-1 digests 60/60 |
| G6 | PASS: 0 defects, 0 nondeterministic replays in every variant (battery, coverage, geometric, room); 281 of 427 actions succeed over all variants |
| G7 | PASS: 2.69 ms/substep, 1 h wait 1.020 s; perf job 0 failing |
| G9 | FAIL only through G4 (the camera extra requires G4 and G6); physics, body, sweep, gen-2 timing, gen-2 layout, generator compat all ok |
| G10 | PASS: 256 changed files, 0 outside P1-ALL |

**Sign-off verification.** `signoff ADOPT P1F --package P1` drafted 428 verdict and 14 edit rows; each was verified
against the trials of both records (`INT2_evidence/verify_signoff.py`, per-row report `INT2_evidence/signoff_report.json`):
409 wrong-list rows (PASS -> PASS, only the wrong-plan list differs, every wrong plan of the new record ran and fails, the
reference passes, initial and idle states fail; cause: another variant on that seed -- K-LEAK's hashed variants -- or a
spec edit), 18 FAIL -> PASS fixes (4 own, 14 forced realistic: the 14 INT listed and T-7d4006bda247ba-01 seeds 0
and 41 in both modes, manipulation's finish), 14 edits (hash = the authored spec's, base = ADOPT's version at 42533e2 matched by hash,
edited JSON paths listed, own mode 5/5), plus 8 test-removal rows. One row is rejected: T-dda4e199f3f5b0-01 seed 1,
forced realistic, fails on both sides (the claim holds; signing it would accept a failing trial, so the exact row is
given for the main session to add if it accepts it). The P1 draft's 415 claimed classes checked against P1F: 410 hold;
2 hold with a changed digest (T-0908068a0b4269-01 seed 97, people's edit: P1's signature would not apply); 2 no longer
hold because they were fixed (T-7d4006bda247ba-01 seed 41, claimed both-fail, now FAIL -> PASS); 1 holds as both-fail
(not signed). With `P1F_reviewed.json` in the record folder `compare ADOPT P1F --package P1` lacks only the main
session's signatures (and G4). Once `P1_signoff_verified.json` is committed as
`tools/orchestration/gate_policy/signoffs/P1F.json`, P1F fails only G4 / G9 (domain leaks) and G3's one unsigned row
(simulated with `INT2_evidence/simulate_signed.py`, which reads the file as if committed: G1b / G2 PASS, G3 217 of 218
signed, G4 FAIL).

## What remains

* Domain side (`finish_breakages.json`): the read probe's leaks -- admin_inventory (felt_marker: washable_marker),
  navigation_robot (robot_vacuum), mechanisms with cleaning (spray_bottle: trigger_bottle); manipulation (a perforated
  colander tears 'along its notch'; unfamiliar things' 'packing' inspector extra and shake; ask about a destroyed thing,
  aspects keep / intended / how / status); pets (a 10 % test crack lets a pet bolt); admin_inventory (count of an
  unfamiliar pot's contents); electrical (drip ingress per device accumulates; stale open items in its doc).
* Kernel, deferred with reasons (`finish_INT2.json` not_fixed): microphone captures in room mode (KNOWN_LIMITS 6;
  room-mode states carry no camera_log), two-handed lifts on slippery surfaces, the wrist depth under a lifted cover
  (K-CAM), puddle kinds of unresolved sources, touch-log cursors across begin() and quiet room writes (optional
  performance), label substitution and size-spread resolution (imprecise), a record of destroyed entities and the
  capability registry (design), generation-2 key bowl / sofa cushions / indoor mat (a generation-2 re-verification).
* For the verifier-hardening phase (docs/design/VERIFY_HARDENING.md, not started here and not contradicted): the 14.3
  rule 1 for refused steps (the interim NeverActed rule stays), the ledger (harms counted once and attributed against
  the idle counterfactual: the room-mode microphone question, harms of inaction, pets' escape corner cases,
  conservation of poured liquids), typed reports and judgement fields (the forced-realistic T-dda4e199f3f5b0-01),
  WP9 seeded arrangements (look-alike roles fixed by position: review I2, red_leaks 3) and the spec repair the review
  listed for every domain; record forgery stays outside the gate (the main session's own record is the hand-over).
* Main session: review and commit `P1_signoff_verified.json` as `gate_policy/signoffs/P1F.json` (decide the one
  rejected row); commit phase 1; push for the first CI run (Linux / Python 3.9 still unverified here).
