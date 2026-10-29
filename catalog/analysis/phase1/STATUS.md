# Phase 1 (stabilise) -- close-out integration status, 2026-10-28

## Close-out (INT3)

Integrator of the phase-1 close-out: INT3. Inputs: the seven close-out results in this folder
(`closeout_<domain>.json` for admin_inventory, electrical, manipulation, mechanisms, navigation_robot and pets;
`closeout_K-GATE3.json`), `finish_INT2.json`, `finish_VER.json` and the phase-1 finish section below (INT2), on the
committed close-out tree 1dbc6db with K-GATE3's committed gate policy. The adversarial reviewer REV ran read-only beside
the round; its result `closeout_REV.json` appeared during it (read; its findings are for the main session, below). Full
result: `closeout_INT3.json`; domain-side work: `closeout_breakages.json` (10 rows); verified second signatures:
`P1C_signoff_verified.json`; the package's first signature: `P1C_reviewed.json` (= `gate/P1C/reviewed.json`); evidence:
`INT3_evidence/`.

### Numbers (final tree: 1dbc6db + INT3's changes)

| Check | Result |
|---|---|
| Unit suite `green_tests.py 5`, normal + `HOUSE_SIM_STRICT=1` | 4,884 tests per mode, 66 files, 0 failing runs (the committed tree before INT3: 4,874, 4 failing runs -- the two pending re-records) |
| `tools/py39_lint.py` | 480 files, 0 violations (committed tree: the same) |
| All authored specs, own mode (combined file, `--check --workers 4`) | 243 / 243 (committed tree: 243 / 243) |
| The 51 realistic specs with `--camera geometric` | 51 / 51 (committed tree: 51 / 51) |
| `perception_audit.py 60 80` | 0 leaks, 0 crashes in all four runs (room / geometric x gen 1 / gen 2; 83 probes each). The read probe over all 78 look-alike groups: 0 leaks (P1F: 3 room / 5 geometric -- the domains' rows, fixed in the close-out); the unfamiliar-twin battery, now with two blank-labelled twins: 0 |
| `layout_audit.py 30` | gen 1: 180 houses, 0 problems; gen 2: 330 houses, 0 problems, 0 domain follow-ups, 709 notes (sofa cushions 330, no `indoor_mat` type 275, diaper bag 40, key bowl 29, stroller 26, high chair 5, lunch box 4 -- unchanged) |
| `fuzz.py 12 300` | 3,600 commands, 0 defects, 0 nondeterministic replays, 46 of 427 actions succeed; `--gen 2` 8 x 300: 2,400 commands, 0 / 0, 41 succeed |
| `fuzz.py --coverage` | 44,295 commands, 0 defects, 0 nondeterministic, 278 of 427 actions driven to success; no-op only: clean_camera, hold_steady, loosen, open_valve, squeeze, steady, support (unchanged) |
| Bench (G7 of the record, alone) | 2.79 ms per substep (P1F 2.69, limit 2.825 = P1F x 1.05), 1 h wait 1.029 s (P1F 1.020, limit 1.071), day 5.70 s (limit 5.79); largest per-system increase climate.house +0.017 ms; the 26 wall-clock bounds alone, normal + strict: 0 failing every attempt |
| Gate record `record --id P1C --package P1C+P1C-INT --prev P1F` | FAIL only on the main session's signatures (10 pass, 2 fail -- G1, G8 --, 1 n/a); with `P1C_signoff_verified.json` read as committed: compare PASS |

### What INT3 changed (each with a regression test; `closeout_INT3.json` lists them)

* **Re-records after attribution.** The committed tree failed test_generator_compat (gen-1 digests 40/60) and
  test_room_golden (56 digest differences in 8 entries). A/B on frozen trees (P1F e5e8727, HEAD 1dbc6db, HEAD with
  `house_sim/data/types/navigation_robot.json` reverted to P1F's): golden dump P1F -> HEAD 18 files, 8 path patterns
  (robots' `label_text`; the realistic runs' handles re-salted by the initial-house digest), P1F -> nav-reverted 0 of
  275; gen-1 P1F 60/60, HEAD 40/60, nav-reverted 60/60, the 20 houses differing only in `entities.<robot_vacuum |
  robot_mower | robot_vacuum_mop>.label_text` (14 / 7 / 3). Every change is navigation_robot's type data; the other
  domains', K-GATE3's and INT3's changes alter no golden file and no generation-1 house. Re-recorded
  `tests/data/gen1_digests.json` (`gen1-digests --write --by INT3`, 20 changed, equal to the attributed dump 60/60) and
  `tests/data/room_golden.json` (`golden.py record --by INT3`: runs 1 and 2 oracle / realistic -- state and obs digests
  only, every step's action, ok and seconds unchanged -- and the final state digest of T-7673a125fe70cd-01,
  T-89530941a65283-01, T-fffb3941b40c87-01, X-f05f58a6388176-01; passes, steps, time and evaluation unchanged). The
  package's reviewed golden rows (code P1C, package P1C-INT): `P1C_reviewed.json`.
* **Kernel requests of the close-out results applied** (all about unfamiliar things, so no golden file, spec or
  generated house changes -- golden A/B 0 of 275): a blank label of an unfamiliar thing reads and shows like no label
  (`core.printed_value`, realism `_blank_print`; mechanisms); the print of an unfamiliar thing comes back as printed, its
  true name not rewritten inside it (`realism.keep_print`; pets); core `place_item` refuses an unfamiliar destination
  with the identify-first message for every relation, also for the domain verbs placing through it (manipulation);
  core `require_tool` refuses a named unfamiliar tool and passes an unnamed one over (navigation_robot); nested leaves
  (`env.perform`) refuse to operate an unfamiliar thing as a step does (manipulation); the wrist look inside an
  unfamiliar thing (`peek` / geometric `inspect` where=inside) is refused alike (admin_inventory, pets); new kernel
  helpers `Ctx.unfamiliar(eid)` / `Ctx.refuse_unfamiliar(*eids)` (the rule six domains copy); `liquids.run_wetted` /
  `liquids.wetness_before(w, eid)` inside a `phys.wetted` hook (electrical); `dialogue.known_record(env, state, eid)` and
  `dialogue.route(..., takes_env=True)` (manipulation); RememberedSubjectTests now also asks keep / intended / how /
  status. navigation_robot's question on the geometric read of an inspect-resolved look-alike: confirmed intended and
  documented. Docs: PERCEPTION.md, KERNEL_API.md, DIALOGUE.md, PHYSICS.md.
* **Auditor**: the unfamiliar-twin battery (`perception_audit.NOVEL_TWINS`) adds the two blank-labelled plain spray
  bottles (on HEAD's kernel it finds the blank-label read: 4 leaks per camera on seed 0; final tree 0).
* Not applied: K-GATE3's thermal bound conversion (tests/test_physics_thermal.py to `wall_clock_bound`). Done, it fails
  two pins of K-GATE3's own tests/test_gate_tool.py (the registry's skip exception must match the live source; the relax
  plan must say "skip"), which belong with the main session's removal of the skip block from
  `gate_policy/perf_tests.json`; reverted, the patch is `INT3_evidence/thermal_bound_conversion.patch` (the dated
  exception holds until 2026-11-30). The other 25 legacy-guarded bounds stay on the counting clock (optional request).

### Gate record P1C

`python tools/orchestration/tier1_gate.py record --id P1C --package P1C+P1C-INT --prev P1F --reviewed
catalog/analysis/phase1/P1C_reviewed.json` on the quiet final tree (3,212 s, tree unchanged during the run, gate code
59fabe996412 as committed; record in `catalog/analysis/tier1/gate/P1C/`): **FAIL** (10 pass, 2 fail, 1 n/a) -- only
for the main session's second signatures.

| Check | Result |
|---|---|
| GATE | PASS: made by the committed gate code (commit 1dbc6db) |
| G0 | PASS: action fingerprint 0eec7120d723 unchanged (427 actions, 117 systems: + electrical.wet_gear) |
| G1 | FAIL only on 4 test-removal rows: two tests of tests/test_gate_tool.py K-GATE3 renamed (`..._turn_the_bounds_off` -> `..._relax_the_bounds`, `..._marks_bounds_...` -> `..._relaxes_bounds_...`), normal + strict; 4,884 tests x 2, 0 failing; py39 lint 0 violations; 25 bounds relaxed, 1 with its skip exception |
| G1b | PASS: 16/16 domain files isolated, 33/33 kernel files with `HOUSE_SIM_DOMAINS=core` |
| G2 | PASS: own mode 243/243 (1,215 trials); 0 verdict changes vs P1F; 466 state-digest changes (463 trials whose house holds a household robot -- its `label_text` -- and T-686df131c7c736-01's two splashing wrong plans on seeds 0 / 41 / 97: electrical's `gathering`), 145 observation-digest changes |
| G3 | PASS: forced realistic 242/243 (T-dda4e199f3f5b0-01 seed 1, allowlisted until 2026-11-30, unchanged); 0 verdict changes; 255 realistic-spec trials identical in G2 and G3 |
| G4 | PASS: geometric 5 -> 0 leaks, room 3 -> 0, 0 crashes |
| G5 | PASS: 240 houses, 0 problems; gen-1 digests 60/60 equal to the re-recorded file (40/60 vs P1F's: the attributed class `gen1`, allowed for P1C-INT) |
| G6 | PASS: 0 defects, 0 nondeterministic in every variant (battery, coverage, geometric, room); 281 of 427 actions over all variants (P1F 281) |
| G7 | PASS: 2.79 ms/substep (limit 2.825), 1 h wait 1.029 s, perf job 0 failing |
| G8 | FAIL only on signatures: golden and golden-all green; 8 entries re-recorded vs commit 1dbc6db, all reviewed (P1C-INT), none signed yet |
| G9 | PASS: physics audit, body audit, sweep bench, gen-2 timing 1.04, gen-2 layout, generator compat, camera tools |
| G10 | PASS: 36 changed files, 0 outside P1C+P1C-INT |

**Sign-off verification.** `signoff P1F P1C --package P1C+P1C-INT` drafted 8 golden rows and nothing else (0 verdict,
0 edit, 0 coverage, 0 gate-change rows). Each golden row verified (`INT3_evidence/verify_signoff_p1c.py`, report
`INT3_evidence/signoff_report.json`): the entry is one the A/B attributed, its digest is the re-recorded file's, only
the attributed digests changed in it. The record's 4 G1 rows were added as test-removal rows after checking each test
existed at P1F, is gone at HEAD, and its named replacement ran and passed in the record's G1 in that mode
(`INT3_evidence/finish_signoff_p1c.py`). Rejected: none. `P1C_signoff_verified.json` (12 rows, signed_by
INT3-verified, gate P1C, prev P1F); with it read as committed, `compare P1F P1C --package P1C+P1C-INT` is PASS on every
check (`INT3_evidence/compare_P1C_signed_simulated.txt`; `INT3_evidence/simulate_signed_p1c.py`).

### What remains for phase 2

* **Main session:** review and commit `P1C_signoff_verified.json` as `tools/orchestration/gate_policy/signoffs/P1C.json`
  (then compare P1F P1C passes; re-record P1C with `--reviewed catalog/analysis/phase1/P1C_reviewed.json` if a PASS
  record is wanted); commit the close-out integration; set `running_substages` to `["P2-S1"]` when phase 2 starts;
  the thermal bound conversion with its policy and test pins (above); push for the first CI run (Linux / Python 3.9).
* **REV's findings** (`closeout_REV.json`, for the main session to triage): gate-tool holes -- code in a git-ignored
  folder imported as a domain, an isolated copy's own .gitignore hiding edits, planted unchecked-hash .pyc files, a
  stale isolate base; a test disguised by a module-level perf switch; py39_lint gaps -- and domain-side tells (cleaning's
  absorb / mop and the robot vacuum-mop; spray-bottle look-alikes by read_aloud, apply_cleaner, disinfect, descale,
  discharge; latch / mark messages rewritten for an unfamiliar padlock / pencil by the label substitution). INT3 changed
  no gate code (a record made by uncommitted gate code needs a gate-change signature).
* **Domain side** (`closeout_breakages.json`, 10 rows): people read_aloud of unfamiliar things and of a blank label
  before read; appliances_cooking unload and textiles_laundry strip_bed destinations; cleaning's held-tool pickers;
  the pill-bottle pour (people, safety, pets); mechanisms' test swing direction; water sensitivity of electrical gear
  (with plumbing_liquids, cleaning, mechanisms; a gen-1 re-record); electrical adopting `liquids.wetness_before`;
  manipulation / people adopting `dialogue.known_record` and `takes_env`; optional domain copies of the unfamiliar-tool
  rule onto the kernel helpers.
* **Kernel, deferred with reasons** (`closeout_INT3.json` not_fixed): the single-pass label substitution's rewrite
  tell (imprecise: domains refuse identify-first first); the deferred list of the phase-1 finish (microphone captures
  in room mode, two-handed lifts on slippery surfaces, the wrist depth under a lifted cover, puddle kinds of unresolved
  sources, touch-log cursors, size-spread resolution, gone-entity record, capability registry, generation-2 key bowl /
  sofa cushions / indoor mat).
* **VERIFY_HARDENING** (not started here, not contradicted): typed reports / judgement fields
  (T-dda4e199f3f5b0-01), the ledger (harms counted once), 14.3 rule 1, WP9 seeded arrangements -- also the setup's
  placement reading true never-tier props: the unfamiliar-twin probe on seed 1 (not the audit's seed 0) puts a novel
  kettle and a novel smartphone with the same look on different floor cells, because `geometry.free_floor_cell` keeps a
  clearance ring for a `fragile` record -- and the spec repair the review listed.

## Phase-1 finish (INT2, 2026-10-28)

Integrator of the phase-1 finish: INT2. Inputs: the 17 finish results in this folder (`finish_<domain>.json` for the 16
domains, `finish_K-GATE2.json`), the earlier round's `breakages.json`, `P1_signoff_draft.json` and this file's previous
version (INT, 2026-10-22). Full result: `finish_INT2.json`; domain-side work: `finish_breakages.json` (10 rows);
verified second signatures: `P1_signoff_verified.json`; the package's first signature: `P1F_reviewed.json`; evidence:
`INT2_evidence/`.

### Numbers (final tree)

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

### What INT2 changed (each with a regression test; `finish_INT2.json` lists them)

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

### Gate record P1F

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

### What remained after the finish (most closed by the close-out above)

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
