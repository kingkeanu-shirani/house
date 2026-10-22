# house_ai independent review: final evidence audit, working record

Reviewer: Claude (Opus 5.5), subagent "p6_audit". Date: 2026-10-22, 02:30-02:55 local time (PDT).
Code checked: the frozen copy of commit `bc0417b` at
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/tree`
(no `.git`). The live repository `<home>\OneDrive\Desktop\house_ai` was not touched.

Files:

* Report audited and edited in place:
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/REVIEW_REPORT.md`
  (new section 9 "Evidence audit", plus inline "[re-ran P6-audit]" tags and the wording fixes in section 4 below).
* This record:
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p6_audit.md`
* Scratch folder (scripts, logs, outputs):
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p6_audit_scratch/`

Evidence labels: **[ran]** executed and the result seen; **[read]** code or documents read, not run; **[inferred]**
reasoning only. "P6-audit" marks what this pass did itself.

---

## Status update (end of the evidence audit), 2026-10-22 02:55 PDT

### Simple version

I went back through the final review report and re-ran the experiments behind its most serious findings on the frozen
copy of the code, to make sure every "we ran it" claim really holds. All of the serious findings came out the same
again: the cheating strategies still pass, the hidden-information leaks still work, the three real bugs are still
there, and the cost estimate is the same to the cent. I found a handful of places where the report said slightly more
than the evidence showed, and one count from an earlier phase that I could not reproduce; I corrected those in the
report and listed every change at its end. Nothing cost money, nothing left the machine, and nothing touched your real
project or the secret-key folder.

### Advanced version

* **Phase.** Final evidence audit, about 25 minutes of wall time, workers at most 3.
* **Re-ran.** Every Critical and High finding (C1, H1-H17; C2 has no separate experiment) and a sample of Medium and
  Low (M1, M2, M3, M5, M6, M7, M12, M13, M14, L1). 40 commands, table in section 2.
* **Outcome.** 0 findings deleted, 0 severities changed. 1 number replaced (P1's "854 wrong-plan objects", not
  reproducible; replaced by 508 / 782). 1 label corrected ("Opus 5.5 always thinks" is [read], not [ran]). 1
  sentence marked [inferred] (Q5). 7 wordings made exact (section 4). Hard-rule paragraph made exact.
* **Open questions for the owner.** None new. The five from P6 stand (P0 approval, core-500 as first deliverable,
  one attempt per hidden seed, needless-action policy, privacy double count).
* **Next.** None: this is the last step.

---

## 1. Environment

`p6_audit_scratch/env.sh` (sourced before every command): `USERPROFILE` and `HOME` point at the empty
`p6_audit_scratch/fakehome`; `HOUSE_AI_FORBID_NETWORK=1`; `PYTHONDONTWRITEBYTECODE=1`; `PYTHONIOENCODING=utf-8`;
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HOUSE_AI_SPLIT_KEY`, `HOUSE_AI_VERIFIER_KEY`, `HOUSE_AI_LLM_BASE_URL`,
`HOUSE_SIM_DOMAINS` and `HOUSE_SIM_STRICT` unset. `Path.home()` printed the fake home [ran]. The house_sim code finds
`~/.house_ai` through `Path.home()` (`eval/splits.py:65-68`, `eval/leaderboard.py:1077`, `eval/run.py:75`) [read], so
the redirect covers it. Working directory: the frozen tree. Python 3.14 (the only one installed).

## 2. Commands run and results

Logs are in `p6_audit_scratch/logs/`. "Same" means identical to the phase record's figure.

| # | Command (batch script) | Result | Finding | Label |
|---|---|---|---|---|
| 1 | `python -W ignore $R/p2_tests_gate_scratch/hand_limit_probe.py` (b1) | one-hand `pick_up`: 7.5 kg held; 7.9 and 8.0 kg "Picked up item \| item fell", `robot_drops` 1; 8.01 kg lifted with both hands and held. Same (bisection to 7.645 kg not re-run) | H11 | [ran] |
| 2 | `wrapped_glass_probe.py` (b1) | all domains loaded: unwrapped and fully wrapped glass both `shattered`, 15 shard entities, 7 floor hazards. Same | H11 | [ran] |
| 3 | `privacy_count_probe.py` (b1) | dressing and showering: 1 violation in room mode, 2 in geometric (`sensor_capture` mic). Same | H11 | [ran] |
| 4 | `python -m unittest tests.test_physics_forces tests.test_physics_statics tests.test_camera tests.test_sight tests.test_body_core` (b1) | 288 tests, 8 failures: forces 1 (hand limit), statics 1 (wrapped glass), camera 3, sight 1 (`glass_shard` doc), body_core 2 (grip pins). Same tests as P2-gate | H11 | [ran] |
| 5 | `python -m unittest tests.test_generator_compat` (b2) | 6 tests, 2 failures (`test_generation_one_houses_are_unchanged`, `test_explicit_gen_1_is_the_default`). Same | H11, M5 | [ran] |
| 6 | `python -m unittest tests.test_room_golden` (b2) | 7 tests, 2 failures (script and reference-plan digests). Same. Rows 4-6: 12 distinct failing tests, as P2-gate | H11 | [ran] |
| 7 | `python -m unittest tests.test_eval_splits.VerifiedSpecsTests.test_report_evidence_must_be_current` (b2) | ERROR `FileNotFoundError ... tree\tasks\templates`; the folder is absent in the tree. Same | M6 | [ran] |
| 8 | `python heldout_targets.py` (b3, my script) | `T-c49fadcea69182-01` seed 500: wrong plan "crouches by each bin..." PASSES; `A-926bc611ae9158-01` seed 6: "left in privacy mode..." PASSES; `A-d9e7a4d5d112d2-01` seed 6: `idle_fails=False`; `T-5ad61b6881821b-01` seed 502: `TypeError: can only concatenate str (not "NoneType") to str`; `T-92f2736b7f949c-01` seed 501: reference "Robot cannot move"; control `T-c49f` seed 0 passes. Winder stairs: seeds 0, 1, 2, 41, 97 have 0 in gen 1 and gen 2; seeds 501 and 502 have 1 in gen 1, 0 in gen 2 | H10 | [ran] |
| 9 | `python trial_detail.py` (my script, inline) | `T-92f2` seed 501 end state: "fell on the stairs: heel came off a winder tread's narrow end", `falls` 1. `T-c49f` on all 5 seeds: wrong plan 1 has `ran: true`, error "Nothing observable at visible_entities.kitchen_trash_can_1.fill_level", `goal_fraction=0.0`; spec passes on every seed | H10, H17 | [ran] |
| 10 | recount of `p2_conditional_scratch/heldout_verify.jsonl` and `heldout2_verify.jsonl` (inline) | 229/243 and 232/243; union of failing specs 18, so 225/243; 16 + 11 = 27 failing trials; 19 reference failures (12 "cannot move" falls in 8 specs), 5 setup or plan errors, 2 wrong-plan passes, 1 idle pass. Same | H10 | [ran] |
| 11 | `python -m house_sim --check <scratch spec> --workers 3` on 8 files (b5, b11): P2-c `pets__examples__games.json`, `outdoor_plants__examples__games.json`; P2-a `g_P-386d0f28709b05-01.json`, `g_X-c77e2cbe7181f0-01.json`, `g_A-d66994a566899c-01.json`; P2-b `shed.json`, `vents.json`, `rain_windows.json` | all FAIL, with the same games passing: chocolate held 5/5, trash-cart game seeds 1, 2, 41; cookie and nonsense-word games seeds 1, 41, 97; false and inverted feeding reports seeds 1, 41, 97; memorised weight 5/5 (constant 742 g on seed 0); valves 3/5 (seeds 1, 41, 97); false burst-pipe reports on seeds 0, 2 and 1, 41, 97; shed timer 5/5; vents 5/5; fern and violet (two games) 5/5; tarp to living room and to driveway 5/5; flat-dry 5/5 (hang-dry 2/5); 11-minute close 5/5 (25-minute close 3/5). Summaries by `parse_check.py` | C1, H1-H4 | [ran] |
| 12 | `--check $R/p2_gaming_b_scratch/specs/modem.json` (b5) | PASS (null result, same) | C1 | [ran] |
| 13 | recount of every lens `--check` log (`p2_gaming_a_scratch/official_check.log`, `p2_gaming_b_scratch/results/check_*.txt`, `p2_gaming_c_scratch/logs/*.cli.txt`) | 44 distinct spec ids; 39 FAIL, 5 PASS (P2-a 11/11, P2-b 14/16, P2-c 14/17) | C1 | [ran] |
| 14 | `cd p3_leaks_identity_scratch && python gaming_mail_sort.py` (b6) | 10/10 seeds, 1 envelope and 0 trays read, inferred variant = true variant. Same | H5 | [ran] |
| 15 | `gaming_glass_position.py` (b6) | room 10/10 reading only the third bottle; geometric 4/10 (6 seeds "only 0 bottles seen"). Same as P3-id's record; the report had dropped "room" | H4 | [ran] |
| 16 | `repro_count_inspect_geo.py` (b6) | 2 cartons seen; `inspect` "Too far to make out the details"; `count` turns them into milk and oat milk (true types). Same | H7 | [ran] |
| 17 | `repro_handle_memo.py` (b6) | episode 2: remembered handle not in frame and never issued; approach, pick_up, read ok; looks 0, success. Same | H8 | [ran] |
| 18 | `python $R/p3_leaks_side_scratch/probe_reconstruct.py` (b6) | 4 candidates tried, 1.1 s offline, matched (T-3fb8a84f20876c-01, 41), 32 visible handles decoded correctly, 9 bottles decoded unseen. Same | H8 | [ran] |
| 19 | `probe_px_size.py 0` (b6) | px-based size: bleach 0.306, detergent 0.286, booster 0.206 m while all show 0.30 m; same pattern for cartons and granule bags. Same | M1 | [ran] |
| 20 | `probe_energy_scale.py room 0 1.3,2.7` (b6) | estimated 1.30 and 2.71 kg. Same | M1 | [ran] |
| 21 | `python $R/p5_plan_scratch/scripts/p5_dry.py $A/dry_opus_dev_full_room.json llm:anthropic:claude-opus-5-5` (b6; the script asserts the network ban, the fake home and no keys) | 1,215 episodes, 114,650 steps, expected $3,758.22, worst $39,063.50, 65 s, offline. Same | H16 | [ran] |
| 22 | pilot totals recomputed from P5's arm rows (inline arithmetic) | A-D at 400 tokens: 89.22 + 2.65 + 15.66 + 2.57 = $110.10; at 3,000: 227.54 + 6.11 + 39.58 + 6.03 = $279.26; caps 150 + 15 + 40 + 10 = $215; episodes 48 + 48 + 8 + 48 = 152. The report's table also listed P0 above "Total" without saying it was excluded | H16, Q7 | [ran] |
| 23 | `python subset_verify.py ... --ids <8 D1 specs>` with no law, `--laws crush,edge,stack,wet_contact,open_spill,handle`, and `--laws stack` (b7, my script) | 8/8, 0/8, 0/8; failing trials 5+5+5+4+5+5+5+5 = 39 in both forced runs. Same | H14 | [ran] |
| 24 | `subset_verify.py ... --perception realistic --camera geometric` on 12 specs (b7) | 8 sampled D3 failures fail on 5, 5, 5, 5, 4, 3, 1, 1 seeds (P5's counts); 4 controls pass | H9 | [ran] |
| 25 | `python beep13.py` (my wrapper of P5's `metamorphic.job`, check `beep`, 13 specs x 5 seeds) | 11/13 flagged, identical seed sets to P5 (`P-47196e0991cdf4-01` and `T-f036622c9240b7-01` not flagged). The first attempt crashed (pickling of a module loaded by path); fixed by importing it normally | C1, H1 | [ran] |
| 26 | `python $R/p4_arch_scratch/saveload_probe.py $A/saveload_T50c78.jsonl --seed 41 --filter T-50c78da60ad2a8-01` (b7) | 2 save points; k=12 state and observation differ, verdict equal. Same as P4 for this spec (the other 469 save points not re-run) | M3 | [ran] |
| 27 | `python -W ignore $R/p3_leaks_side_scratch/probe_until_oracle.py geometric 0` (b10) | wetness "0.1732" in 18 one-step waits; asleep "Waited 0 s", lying awake "Waited 1 s"; bucket "not visible". P3-side's own record says the level was shown in room mode only; the report had said "all of it in both camera modes" | H6 | [ran] |
| 28 | `python $R/p2_tests_gate_scratch/gatelab.py $T $A/gatelab` (b9) | E0 PASS, E1 PASS, E2a FAIL, E2b PASS, E3 PASS, E4 PASS, E5 FAIL, E6 PASS with G4/G6 INFO. Same | H12 | [ran] |
| 29 | `coverage_audits.py fuzz 12 300 ...` and `perception 40 80 ...` (b9) | 46 of 427 actions succeed (852 of 3,600 commands ok, 24 %); 32 of 427 (1,836 of 3,200 ok). Same | H13 | [ran] |
| 30 | `python tools/perception_audit.py 40 80 --workers 3` (b9) | 40 houses, 3,200 steps, 0 leaks, 0 crashes, on the tree where rows 14-20 and 27 leak | H13 | [ran] |
| 31 | `python gen1_all.py` (b11, my script; same digest call as the unit test) | 340 pinned entries, 0 match | M5 | [ran] |
| 32 | `python $R/p2_gaming_a_scratch/exposure_census.py $A/exposure_census.json` | totals 243 specs, 46 with a relocation or collateral rule (197 without), 86 structurally constant, 54 with 3 variants. Same | C1 | [ran] |
| 33 | `cd out && python p5_plan_scratch/scripts/reharden_union.py` | 198 of 243 with at least one issue; `protect_unguarded` 96. Same. Note: this script rewrites `p5_plan_scratch/reharden_union.json` (see section 6) | C1 | [ran] |
| 34 | plugin census (inline, after `load_domains()`) | `ACTIONS` 427, `HOOKS` 266 registrations, `place` 79 handlers (10 before, 69 after), `pick_up` 65 (19, 46); all upper-case tables 3,489 entries, minus `OWNERS` 1,091, `SYSTEM_PERIODS` 30, `SYSTEM_IDLE` 34, `IDLE_PROBES` 1 = 2,333. Same | M2 | [ran] |
| 35 | catalog, line, batch and spec counts (inline) | 2,720 entries; 128 duplicate groups with 278 entries, 2,570 distinct texts; `house_sim` 363 files, 216,794 lines; 52 batch files, 2,478 ids, 1 already authored (2,477 left); 243 specs, 32 files, 51 realistic; variant counts 0:104, 2:83, 3:54, 4:2 | M13, M14, H3, H5 | [ran] |
| 36 | wrong-plan key census (inline) | 508 wrong-plan objects at spec level, 782 including variant-level lists; keys `name`, `plan`, `camera` (14). Merging each variant with the project's `spec.merge` gives 1,108 entries. **P1's 854 "over merged variants" was not reproduced** under either reading | H17 | [ran] |
| 37 | budget ratios from P1's `verify_own.jsonl` and P5's dry-run rows (inline) | time budget / reference time: median 22.2x per trial (23.1x per spec); step budget / largest reference steps: median 5.0, 184 budgets cut by `max(30, 3*ref+10)` (P5's definition). With per-spec median reference steps: 6.0x and 197 | M12 | [ran] |
| 38 | idle goal fraction from P1's `idle_probe.jsonl` (inline) | mean 0.303 over 1,215 trials; 103 specs at 0.5 or more on some seed; wait-then-done 0/1,215. Same | L1, Q1 | [ran] |
| 39 | `ast.parse(src, feature_version=(3, 9))` over every `.py` file in the tree (inline) | 447 files, 0 failures (P1 counted 441, P4 444, over different file sets) | M7 | [ran] |
| 40 | hard-rule and side-effect checks: `find tree -newer START_MARKER`; `find out -newer START_MARKER`; listing of every phase's fake home; grep of every phase's saved scripts for split-key and non-dry LLM commands; timestamps of files in the tree | section 6 | hard rules | [ran] |

Code read in this pass [read]: `tasks/verify.py:112-200` (lines 182, 185 and 188 confirmed);
`systems/tools_vehicles/perception.py:153-162` (the only Python writer of `sorted_ok`); `systems/core.py:1770-1778`;
`systems/people/comms.py:40-127`; `systems/safety/people_care.py:258-283` (every active hazard kind marks the
audience as alerted, whatever the text); `eval/run.py:276-282` (`verify_many` gets no `camera`);
`generator/__init__.py:76`; `tasks/spec.py:149`; `eval/splits.py:36-39`; `kernel/timeline.py:66`;
`systems/admin_inventory/inventory.py:226-230`; `kernel/evaluation.py:57-85`; `systems/navigation_robot/stairs.py:128`;
`tasks/skills.py:312-315`; `tools/orchestration/tier1_adopt.js:46, 76`; `tier1_stage_a.js:95, 105, 106`;
`author_wf.js` (no `--check` executed by the script; `verified_passing` is a self-reported field; prompt cites
KERNEL_API "v2.1" through "v2.8"; no mention of camera, geometric, schema 3 or physics); `tier1_gate.py:28, 31`
(G4 6x150, G6 6x300); `catalog/analysis/tier1/gate/C1/meta.json` (overall FAIL from G1 `test_eval_splits` and G10
`coverage.csv`; G1c SKIP); `catalog/analysis/tier1/` holds no adoption handoff.

## 3. Every final finding, with its audit status

| ID | Sev. | Audit status | Label after audit |
|---|---|---|---|
| C1 | Critical | reproduced: 39/44 recount; 11 more FAIL specs and a PASS control re-run with `--check`; M-BEEP; censuses | [ran] |
| C2 | Critical | no separate experiment; rests on C1 [ran] and the plan text [read] | [read] + [ran] via C1 |
| H1 | High | reproduced (rows 11, 25; code read) | [ran] |
| H2 | High | reproduced (row 11; code read) | [ran] |
| H3 | High | reproduced for the shed, window and flat-dry games and the seed arithmetic; the 10/22 table and cross-variant replay not re-run | [ran] |
| H4 | High | reproduced (rows 11, 15); glass wording fixed to room mode | [ran] |
| H5 | High | reproduced (row 14; code read); private-range cost estimate not re-run | [ran] / estimate |
| H6 | High | reproduced in geometric mode (row 27; P6 did room mode); bucket-level wording fixed | [ran] |
| H7 | High | reproduced in geometric mode (row 16; P6 did room mode) | [ran] |
| H8 | High | reproduced (rows 17, 18) | [ran] |
| H9 | High | reproduced on a 12-spec sample (row 24); full D3 and forced-realistic runs not re-run | [ran] |
| H10 | High | reproduced (rows 8-10) | [ran] |
| H11 | High | reproduced (rows 1-7) | [ran] |
| H12 | High | gatelab reproduced (row 28); P6 had re-run the exit-2 command; the full gate at `bc0417b` not re-run | [ran] |
| H13 | High | reproduced (rows 29, 30) | [ran] |
| H14 | High | reproduced on the 8 specs (row 23); all-19-laws run not re-run | [ran] |
| H15 | High | scripts read (section 2, code read); batch token load not re-run | [read] + [ran: P5] |
| H16 | High | dry run reproduced (row 21); pilot arithmetic (row 22); "always thinks" relabelled [read] | [ran] / [read] |
| H17 | High | reproduced (rows 9, 36); 854 replaced by 508 / 782 | [ran] + [read] |
| M1 | Medium | px and battery rows reproduced (rows 19, 20); other rows not re-run | [ran: P3] |
| M2 | Medium | registration counts reproduced (row 34); tie-order probe not re-run | [ran] |
| M3 | Medium | the divergent save point reproduced (row 26) | [ran] |
| M4 | Medium | not re-run | [ran: P4] |
| M5 | Medium | 0/340 reproduced (rows 5, 31) | [ran] |
| M6 | Medium | clean-checkout failure reproduced (row 7); timing and pin counts not re-run | [ran] |
| M7 | Medium | grammar check reproduced (row 39); numeric nudges not re-run | [ran] |
| M8 | Medium | not re-run (performance on a shared machine) | [ran: P2-gate, P4] |
| M9 | Medium | not re-run | [ran: P1] |
| M10 | Medium | not re-run; code-reading finding (hard rule 2) | [read] |
| M11 | Medium | not re-run | [ran: P2] |
| M12 | Medium | recomputed (row 37) | [ran] |
| M13 | Medium | line and batch counts reproduced (row 35); C1 gate FAIL read and qualified | [ran] / [read] |
| M14 | Medium | duplicate count reproduced (row 35) | [ran] |
| L1 | Low | recomputed (row 38) | [ran] |
| L2-L8 | Low | not re-run | as in the report |

## 4. Changes made to REVIEW_REPORT.md

1. **C1 and H1, M-BEEP wording.** "Still passes 11 of 13 speech-scored specs" overstated: the prototype flags a spec
   when the "Beep." mutant succeeds on at least one seed, and only 4 of the 11 pass on all five seeds; the 13 are specs
   whose reference contains a speech step. Reworded; the fire-alert seeds named.
2. **H4, glass slot game.** P3-id measured 10/10 in room mode and 4/10 in geometric mode (its naive search failed on 6
   seeds); the report said 10/10 without the mode. Qualified.
3. **H6, camera modes.** "All of it in both camera modes" overstated: P3-side's own record says the bucket level was
   tested in room mode only, and row 27 confirms the geometric probe never saw the bucket. Reworded.
4. **H17, wrong-plan count.** P1's "854 wrong-plan objects" (from unsaved inline code) could not be reproduced; the
   re-count is 508 / 782, and the conclusion (no expected-reason field) is unchanged. Replaced.
5. **H16, label.** "Opus 5.5 always thinks" came from P5 reading the API documentation, not from a run. Relabelled
   [read].
6. **Q7, pilot table.** "Total" sat under a P0 row but excluded P0. Renamed "Total A-D (P0 not included)".
7. **M13, fairness.** The C1 gate record is overall FAIL, but its two FAILs are the clean-checkout test and one
   out-of-scope file. Named.
8. **Section 6.** "0/243 changed by reversed plugin tie order" now says final states or verdicts, because M2 reports
   observation-content changes in 9 specs.
9. **Section 4.7.** The protect-map lint count 96 and C1's 94 use different definitions (any variant unguarded
   versus no variant guarded). Stated.
10. **Q5.** "Specs written earlier would have been rewritten anyway" is an inference from measured facts; marked
    [inferred].
11. **Hard-rule paragraph.** It said every phase redirected the home directory; only P2-gate, P3-id, P4, P5 and P6 did
    (the others' saved scripts call no evaluation, split or leaderboard code). It omitted the 329 bytecode caches
    written into the tree on 2026-10-21 around 23:00 (rows 40, section 6). Both made exact.
12. **Tags.** "[re-ran P6-audit]" added where this pass reproduced a result; section 9 "Evidence audit" added; the
    evidence table in section 8 gained a row for this record; the label legend explains "P6-audit".

No finding was deleted and no severity changed: every Critical and High result that was re-run reproduced, with the
same numbers and the same seeds.

## 5. Scripts I wrote (all in `p6_audit_scratch/`)

| File | Purpose |
|---|---|
| `env.sh` | fake home, network ban, no keys, no bytecode; defines `A`, `R`, `T` |
| `b1_defects.sh`, `b2_tests.sh` | H11 probes and the 7 pinned test files, the clean-checkout test |
| `heldout_targets.py`, `b3_heldout.sh` | H10 single held-out trials and winder census |
| `trial_detail.py` | H10 fall cause; H17 wrong-plan errors of `T-c49fadcea69182-01` |
| `b5_checks.sh`, `b11_more.sh`, `parse_check.py` | official `--check` on earlier phases' gaming specs, and a log summariser |
| `b6_perception.sh` | P3 perception probes (unchanged) and the dry-run estimator |
| `subset_verify.py`, `b7_previews.sh` | named-subset verification with forced perception, camera, generator or laws (H9, H14); M-BEEP; save/load |
| `beep13.py` | P5's `metamorphic.job` on the 13 speech specs only |
| `b9_gate_cov.sh` | gatelab, audit action coverage, perception audit |
| `gen1_all.py` | all 340 generation-1 digests |
| `logs/` | every output; `logs/b*_fakehome_after.txt` lists the fake home after each batch (empty) |
| outputs | `d1_*.jsonl`, `d3_subset.jsonl`, `saveload_T50c78.jsonl`, `dry_opus_dev_full_room.json`, `cov_*.json`, `exposure_census.json`, `gatelab/` (copies of the C1 record with synthetic edits) |

## 6. Hard-rule compliance and side effects

* **Rule 1 (no paid calls, nothing off the machine).** No LLM call. The one LLM-agent command was the dry-run
  estimator (row 21), with `HOUSE_AI_FORBID_NETWORK=1`, no keys, and the script's own assertions. No network use.
* **Rule 2 (`%USERPROFILE%\.house_ai\`).** Nothing under it was read, created, listed or copied; I did not even test
  whether it exists. No split-key command. The fake home was empty after every batch.
* **Earlier phases, from their artifacts [ran].** No saved script in any phase folder contains `init-split-key` or
  `init_split_key`; no saved script or log contains `--run-agent llm` without `--dry-run`; P5's LLM outputs all say
  "DRY RUN" or are estimator rows. Fake homes: P2-gate, P3-id, P5 and P6 empty; P4 holds only an empty
  `AppData\Roaming` folder. P1, P2-a, P2-b, P2-c, P2-cond and P3-side did not redirect the home directory; their saved
  scripts import no evaluation, split or leaderboard code. Limit: I can see only saved artifacts, not the phases'
  inline commands.
* **The frozen tree.** I wrote nothing in it (`find -newer`: 0 files). Files in it newer than the copy: the C1 gate
  record (written at copy time, 18:28:10, same second as the folder), `tools/orchestration/green_tests.json`
  (2026-10-20 20:02, P2-gate's test runner, already disclosed), and 329 `__pycache__` files all from
  2026-10-21 23:xx (a phase without `PYTHONDONTWRITEBYTECODE`). Now disclosed in the report.
* **Other phases' folders.** One write: re-running P5's `reharden_union.py` (row 33) rewrote
  `p5_plan_scratch/reharden_union.json`. Its inputs were unchanged (no input file is newer than my start marker), and
  it printed the same totals, so the content should be the same [inferred]; I could not diff it because the old copy
  was overwritten. Also `REVIEW_REPORT.md`, as instructed.
* **The live repository** `<home>\OneDrive\Desktop\house_ai` was not touched.
* **Load.** At most 3 workers per batch. Batches ran one at a time, except twice when a single-process probe ran
  beside a 3-worker batch (b6 beside b5; the geometric oracle probe beside b7).

## 7. What this audit could not check

* Anything that needs the hidden key or a real LLM run (hard rules).
* The full-catalog runs listed as "not re-run" in section 3 and in REVIEW_REPORT section 9 (each 20-40 minutes); they
  rest on the phase that ran them, and the samples above agree with them.
* Inline commands that phases did not save (for example P1's 854 count): only their saved scripts and logs could be
  checked.
