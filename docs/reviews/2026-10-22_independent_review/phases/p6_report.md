# house_ai independent review: Phase 6 (Report), working record

Reviewer: Claude (Opus 5.5), subagent "p6_report". Date: 2026-10-22, about 02:05-02:45 local time.
Code reviewed: the frozen copy of commit `bc0417b` at
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/tree`
(no `.git`). The live repository at `<home>\OneDrive\Desktop\house_ai` was not touched.

Outputs:

* Final report:
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/REVIEW_REPORT.md`
* This record:
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p6_report.md`
* Scratch folder (scripts and logs):
  `<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p6_report_scratch/`

Evidence labels:

* **[ran]**: executed, and the result was seen;
* **[read]**: code or documents read, not run;
* **[inferred]**: reasoning only.

Labels carried over from earlier phases keep their phase name ("[ran: P2-a]"). **P6** marks what this phase re-ran
or re-read itself.

---

## Status update (end of Phase 6), 2026-10-22 about 02:45 PDT

### Simple version

I read every earlier phase's record and combined them into one final report. The report starts with a plain summary,
then ranks every problem by how much it matters and says how to fix each one. It answers your eight questions, gives a
"top 10 things to do next" list, and spells out the exact new automatic checks to add. Before writing it, I re-ran
eight of the most important findings myself on the frozen copy of the code, and all eight came out exactly as the
earlier phases reported. For example, the robot's "wait until" command still gives away hidden numbers, and a
"warning" that invites a child into broken glass still counts as a warning. Nothing cost money, nothing touched your
real project or the secret-key folder, and nothing is blocking me.

### Advanced version

* **Phase.** Phase 6 (Report), about 40 minutes.
* **Read.** The reviewer prompt, the brief and all 10 phase records (`p1_claims.md`, `p2_conditional.md`,
  `p2_gaming_a.md`, `p2_gaming_b.md`, `p2_gaming_c.md`, `p2_tests_gate.md`, `p3_leaks_identity.md`,
  `p3_leaks_side.md`, `p4_arch.md` and `p5_plan.md` in full).
* **Commands.** Section 2. Every independent re-run reproduced the earlier phase's result (8/8 spot checks), and no
  file was written in the tree or in any other phase's folder (checked with `find -newer`).
* **Final ranking** (deduplicated):
  * 2 Critical: C1 "verified" is not gaming-resistant; C2 the plan scales authoring before fixing that.
  * 17 High: H1-H17.
  * 14 Medium: M1-M14.
  * 8 Low: L1-L8.
  * The mapping from every phase finding to the final IDs is in REVIEW_REPORT section 7; section 4 below lists the
    severity changes made here.
* **Open questions for the owner** (carried from the phases):
  1. Approve or decline the $10-capped P0 calibration run.
  2. Is a core-500 v1 release an acceptable first deliverable?
  3. Is "one attempt per hidden test seed" the intended rule?
  4. Should doing an action that was not needed fail a conditional spec, or only lower its score?
  5. Should a privacy intrusion plus a microphone capture of the same moment be one violation or two?
* **Next.** None: this is the last phase.

---

## 1. What I read

* `scratchpad/review/house_ai_reviewer_prompt.md` and `house_ai_project_brief.md` (in full).
* All phase records in `scratchpad/review/out/`, in full: `p1_claims.md` (434 lines), `p2_conditional.md` (327),
  `p2_gaming_a.md` (526), `p2_gaming_b.md` (511), `p2_gaming_c.md` (615), `p2_tests_gate.md` (409),
  `p3_leaks_identity.md` (515), `p3_leaks_side.md` (348), `p4_arch.md` (435) and `p5_plan.md` (897).
* Code, read to spot-check the earlier phases' citations [read: P6]:
  * `house_sim/tasks/verify.py` lines 132-194, the trial verdict, `ran` on `PlanError`, `fails`, `why`;
  * `house_sim/generator/__init__.py:76`, the address;
  * `house_sim/tasks/spec.py:149`, the variant index;
  * `house_sim/eval/splits.py:37-38`, `PRIVATE_BASE`;
  * `house_sim/systems/admin_inventory/inventory.py:180, 201, 205, 228, 230`, the grants;
  * `house_sim/kernel/timeline.py:66, 85, 299, 311`, `NUMERIC_OPS`;
  * `house_sim/systems/people/comms.py:42` (`told_count`) and `:93-121` (`deliver_content`, `contains_phrase`,
    `attributed`);
  * `house_sim/eval/run.py:276-282`, where `verify_on_the_fly` passes no camera;
  * `house_sim/kernel/evaluation.py:53-90`, where side effects skip missing entities (lines 59, 69, 84);
  * `tools/orchestration/tier1_adopt.js:13, 46, 57, 76`, the gate command and the truncation;
  * `tools/orchestration/tier1_stage_a.js:95, 105, 106`, the truncation.

All citations matched what the phases reported. One precision was added: `no_damage` also skips destroyed entities
(line 69, `if eid in now and ...`). P1 had named all three rules; the report now cites all three lines.

## 2. Commands run and results

Every command ran after `source p6_report_scratch/env.sh`. That script points `USERPROFILE` and `HOME` at the empty
`p6_report_scratch/fakehome`, sets `HOUSE_AI_FORBID_NETWORK=1` and `PYTHONDONTWRITEBYTECODE=1`, and unsets every
API, split and verifier key variable. The working directory was the frozen tree. Logs are in
`p6_report_scratch/logs/`.

| # | Command | Result | Confirms |
|---|---|---|---|
| 0 | `python -c "import pathlib; print(pathlib.Path.home())"` | the fake home path | hard rule 2 setup [ran: P6] |
| 1 | `python tools/orchestration/tier1_gate.py compare --package ADOPT:cleaning --shared-tree` (the command `tier1_adopt.js:46` tells every adoption owner to run) | `error: the following arguments are required: prev, cur`, **exit 2** | H12 / P2-gate T4 [ran: P6] |
| 2 | `python -W ignore out/p3_leaks_side_scratch/probe_until_oracle.py room 0` | glance shows "looks dry" and "half"; `wait(until startswith ...)` recovers wetness **"0.1732" in 18 steps** and level **"0.4371" in 20 steps**; asleep gives "Waited 0 s (until condition met)", lying awake gives "Waited 1 s"; observations show "lying" for both | H6 / P3-side L1 [ran: P6] |
| 3 | `cd out/p3_leaks_identity_scratch && python -W ignore probe_address.py` | read envelope shows 100, 101, 102, 141, 197, 323 on seeds 0, 1, 2, 41, 97, 1000123; `(n-100) % 3` equals the true variant on **6/6** | H5 / P3-id I1 [ran: P6] |
| 4 | `python -W ignore out/p2_gaming_c_scratch/tryplan.py tasks/authored/people__examples.json P-47196e0991cdf4-01 out/p2_gaming_c_scratch/plans/p47196.json` | inverted message, keyword salad and wrong-day message: **success=True on all 5 seeds each**, 0 failures, 0 refusals | H1 / P2-c F1 [ran: P6] |
| 5 | `python -W ignore out/p2_gaming_c_scratch/tryplan.py tasks/authored/safety__examples.json X-bc4e8d373e62c5-01 out/p2_gaming_c_scratch/plans/x_bc4e.json` | "Hi Ava! Dinner soon!" and "Ava, come look at this!": **success=True 5/5 each**; dustpan-on-the-floor variant fails 5/5 (seed 0 `hazard_spread`; seeds 1, 2, 41, 97 `failure_predicate_1` with the place refused by the sharp-grip rule), i.e. the incidental-reason failure P2-c described | H1, H17 / P2-c F1, F8 [ran: P6] |
| 6 | `python -W ignore -m house_sim --check out/p2_gaming_a_scratch/g_T-151f6733397156-01.json --workers 2` (writes no evidence) | **FAIL T-151f6733397156-01**: both GAME plans (dirty bleach water into the spare clean bucket; into the bread box) `fails: false`, `goal_fraction=1.0` on all 5 seeds; reference, initial and idle checks as expected; 26 s | C1, H2 / P2-a G2 [ran: P6] |
| 7 | `python -W ignore -m house_sim --check out/p2_gaming_b_scratch/specs/screws.json --workers 2` | **FAIL T-f7f4105ce49060-01**: "sort, then take two piles back out and hold them" and "sort, then throw one pile" both `fails: false`, `goal_fraction=1.0` on all 5 seeds (realistic mode); 66 s | C1, H2 / P2-b F1 [ran: P6] |
| 8 | `cd out/p3_leaks_identity_scratch && python -W ignore repro_count_inspect.py` | room mode: `inspect obj_54798938` refused "Approach it first; it is out of arm's reach"; `count living_room_1` (22 s) turns the three unresolved "paper drink carton" records into milk, oat milk and heavy cream (matching the true types); 26 of 52 records gained fields. The geometric half of this script saw no cartons (P3-id's separate `repro_count_inspect_geo.py`, not re-run, is the geometric evidence) | H7 / P3-id I3 [ran: P6, room mode only] |
| 9 | `ls -la p6_report_scratch/fakehome` (after each batch) | empty | hard rule 2 [ran: P6] |
| 10 | `cd tree && find . -newer p6_report_scratch/env.sh -type f \| wc -l`; same over `out/` excluding `p6_report_scratch` | **0** and **0**: nothing written in the tree or in another phase's folder | non-interference [ran: P6] |
| 11 | `grep` and `sed` of the code lines listed in section 1 | all citations confirmed | [read: P6] |

## 3. Scripts I wrote (all in `p6_report_scratch/`)

| File | Purpose |
|---|---|
| `env.sh` | fake home, network forbidden, keys unset, no bytecode; defines `P6`, `R` (out) and `T` (tree) |
| `spotchecks.sh` | rows 0-5 of section 2; outputs in `logs/00_home.txt` to `logs/05_tryplan_xbc4e.txt` |
| `spotchecks2.sh` | rows 6-8 (two official `--check` runs on earlier gaming scratch specs, and the `count` repro); outputs in `logs/06_check_mop_bucket_games.txt`, `logs/07_check_screws_games.txt`, `logs/08_count_inspect.txt` |
| `logs/99_fakehome_after*.txt` | fake home listing after each batch (empty) |

No new analysis script was needed. The spot checks reuse the earlier phases' probes unchanged, read from their folders
and run with the tree as the working directory, and their output goes only to this phase's logs.

## 4. Every final finding, with evidence labels and sources

Severity is the final, deduplicated severity. "Sources" lists the phase findings merged into it. Full text, evidence
and fixes are in REVIEW_REPORT section 1.

| ID | Sev. | Finding | Strongest evidence (label) | Sources |
|---|---|---|---|---|
| C1 | Critical | "Verified" is not gaming-resistant | 39/44 attacked specs FAIL `--check` with gaming plans [ran: P2-a/b/c]; 10/22 conditional specs passed by constant policies [ran: P2-cond]; M-BEEP 11/13 [ran: P5]; 198/243 specs with a known issue [ran: P5]; 4 games re-confirmed [ran: P6] | P2-a G1, P5-1 (part) |
| C2 | Critical | The plan scales authoring before fixing verification and without measuring an agent | plan text [read: P5]; C1 [ran]; workflow [read: P4] | P5-1 |
| H1 | High | Speech and reports are scored by recipient, not content (including safety warnings) | 5 games 2/2-5/5 [ran: P2-c]; false burst-pipe reports [ran: P2-a]; M-BEEP [ran: P5]; re-run [ran: P6]; code [read: P2-c, P6] | P2-c F1, F7; P2-a G4 |
| H2 | High | Local or proxy predicates: relocation, do-then-undo, proxies, unprotected collateral, destroyed items invisible | many 5/5 games [ran: P2-a/b/c/cond]; bucket and screws re-run [ran: P6]; `evaluation.py:59, 69, 84` [read: P1, P6] | P2-a G2, G6; P2-b F1, F2, F7; P2-c F2; P2-cond F-C4, F-C5; P1 F7 |
| H3 | High | Conditional and judgement entries do not test their condition | 10/22 [ran: P2-cond]; timer, early-close and bogus-order games [ran: P2-b]; cross-variant 13/85 [ran: P2-cond]; seed imbalance [ran: P1, P2-b] | P2-cond F-C1; P2-b F3-F6, F8, F9; P2-c F6, F10; P2-a G7; P1 F6 |
| H4 | High | Constants and fixed positions allow memorisation | weight, burner, timer, plants, tape [ran: P2-a, P2-c]; position 10/10 [ran: P3-id]; 26/40 middle bottle [ran: P2-c]; start-room tell [ran: P2-a]; glare 40/40 [ran: P2-b] | P2-a G3, G5; P2-b F10; P2-c F3, F4, F5; P3-id I2, I11 |
| H5 | High | The street address encodes seed mod 900, and so the hidden variant | mail sort 10/10 [ran: P3-id]; 6/6 [ran: P6]; code [read: P3-id, P6]; private-range recovery cost only [ran: P3-id, estimate] | P3-id I1 |
| H6 | High | `wait(until)`, `watch` and guards are exact-value oracles | 0.1732 and 0.4371 recovered; asleep vs awake [ran: P3-side, P6]; code [read: P6] | P3-side L1 |
| H7 | High | `count` is a remote inspect | room 3/3 and geometric 2/2 [ran: P3-id]; room re-run [ran: P6]; code [read: P6] | P3-id I3 |
| H8 | High | Handles and noise computable from public data; handles persist across episodes | 32/32 handles decoded on a dev seed [ran: P3-side]; 0-look pass with a remembered handle [ran: P3-id] | P3-side L7; P3-id I5 |
| H9 | High | Verified in a different mode from the one scored; privileged references | D3 192/243 [ran: P5]; forced realistic 230/243 [ran: P2-gate]; `verify_on_the_fly` drops camera [read: P5, P6]; manifest own-mode [read: P1] | P1 F2, F8; P5-4 (part), P5-6 |
| H10 | High | Held-out seeds break references and discrimination | 225/243, 27/2,430 trials, 2 wrong-plan passes, 1 idle pass, 12 winder falls [ran: P2-cond]; D2 15 specs [ran: P5] | P2-cond F-C2 |
| H11 | High | 3 "pending re-record" test failures are real defects | 3 probes [ran: P2-gate] | P2-gate T1; P5-5 (part) |
| H12 | High | Gate blind spots; adoption not gated | gatelab E1-E6 [ran: P2-gate]; argparse exit 2 [ran: P2-gate, P6]; full gate FAIL [ran: P2-gate] | P2-gate T3, T4, T5; P1 F3 (part); P5-5 (part) |
| H13 | High | "0 leaks / 0 defects" is absence of evidence | 46/32 of 427 actions succeed; 198 never [ran: P2-gate]; auditor 0 leaks while leaks reproduce [ran: P3] | P2-gate T2; P1 F4 |
| H14 | High | D1 law flip breaks specs; readiness lists incomplete | 8/243 via `stack`; all 19 laws 233/243 [ran: P4] | P4 A1; P5-4 (part) |
| H15 | High | The authoring workflow trusts self-reports; stale; truncates | scripts [read: P4, P5, P6]; batch load [ran: P5]; adoption "green" from a broken command [ran: P2-gate] | P4 A3; P5-2 |
| H16 | High | No real agent measured; the cost input is unmeasured | dry runs $3,758 / pilot $110-279 [ran: P5, estimates]; baselines [ran: P5] | P5-3 |
| H17 | High | Wrong plans not checked for their reason; crash accepted | `verify.py:185, 188` [read: P1, P6]; crash on every seed [ran: P1]; mistake never executed in 3 more plans [ran: P2-a, P2-c, P6]; correction [ran: P2-cond] | P1 F1, F5; P2-cond F-C6; P2-a G8; P2-c F8 |
| M1 | Medium | Look-alike and hidden-field side channels (px size, battery scale, type defaults, novel ids, echo, private docs, refusal wording, derived hazards, averaging) | each [ran: P3-id, P3-side] | P3-side L2-L5, L8; P3-id I4, I6-I10 |
| M2 | Medium | The plugin surface is unmapped | registry census, tie-order probe [ran: P4] | P4 A2 |
| M3 | Medium | State outside the journal (stale static indexes) | 1/470 save points; 10 stale indexes [ran: P4] | P4 A4 |
| M4 | Medium | Duplicated mechanics and deferred laws | censuses [ran: P4] | P4 A5 |
| M5 | Medium | Generation-1 houses changed, unattributed | 0/340 digests [ran: P2-gate] | P2-gate T6 |
| M6 | Medium | Clean-checkout failure, timing tests, digest pins | [ran: P2-gate, P4] | P2-gate T7; P4 A6 |
| M7 | Medium | One machine, no CI; libm sensitivity; CRLF | [ran: P4]; cross-platform [inferred] | P4 A8; P1 F14 |
| M8 | Medium | Probable `wait(until)` / `ask` regression; replay size | sweep_bench 3 runs [ran: P2-gate]; loaded-machine timings [ran: P4, not comparable] | P2-gate T8; P4 A7 |
| M9 | Medium | Harm attribution decided per site | 62 idle trials [ran: P1] | P1 F9 |
| M10 | Medium | Hidden test split and leaderboard edges | code [read: P1]; not exercised (rule 2) | P1 F15, C11, C12 |
| M11 | Medium | Who-asked not observable; role tags; raw ids | [ran: P2-cond, P2-c] | P2-cond F-C3; P2-c F9 |
| M12 | Medium | Generous budgets | 22x time, 5x steps [ran: P1, P5] | P1 F11; P2-cond F-C7; P5-7 |
| M13 | Medium | Records and claims overstate status | C1 gate record [read: P1]; stale evidence [ran: P1] | P1 F3, F10, F13; P2-gate T11; P5-13 |
| M14 | Medium | Duplicates, feasibility, gap loop | catalog profile [ran: P5] | P5-9, P5-10 |
| L1 | Low | Raw goal fraction inflates idle agents | 0.303 / 0.356 [ran: P1, P5] | P1 F10; P5-11 |
| L2 | Low | Appendix clauses not enforced | [read: P2-a] | P2-a G9 |
| L3 | Low | Modelling holes | [ran: P2-a, P3-id] | P2-a G10-G12; P3-id S1, S2 |
| L4 | Low | One bad wrong plan hides the others | [ran: P2-b, P2-cond] | P2-b F11 |
| L5 | Low | Audit tool gaps | [read, ran: P2-gate] | P2-gate T9, T10 |
| L6 | Low | Code hygiene | [ran: P4] | P4 A9-A11 |
| L7 | Low | Verification compute growth | [inferred from ran: P5] | P5-8 |
| L8 | Low | Tier-1 evaluation features unused | [ran: P1]; [read: P1] | P1 F12, C28 |

## 5. Severity decisions made in this phase (and why)

* **P2-a G1 (Critical) and P5-1 (Critical)** became C1 and C2. G1 is the defect. P5-1 is the plan decision that would
  scale it. Both stay Critical.
* **P1 F1 (High)** became H17 (High), on the corrected basis of P2-cond F-C6. The measured incidence of wrong plans
  failing for the wrong reason is small (14 guard-only plans, all legitimate; 1 crash accepted; 3 more plans that never
  execute their mistake). It stays High for two reasons: the check is absent at authoring scale, and the gate cannot
  see reason drift. It is ranked last among the Highs, and its text says so plainly.
* **P4 A2 (High)** became M2 (Medium). It is the root-cause class of the 3 real defects, but its effect on benchmark
  validity is indirect. Q4 still leads with it.
* **P3-side L2 (Medium-High)** went into M1 (Medium), marked "blocks D3". It becomes High at the moment geometric
  becomes the default.
* **P1 F3 (High, "green at every stage" contradicted)** went into M13 (Medium). It concerns the credibility of the
  records, not benchmark scores. The gate-mechanics part went into H12.
* **P3-id I5 (Medium; High if repeated attempts are allowed)** was merged with **P3-side L7 (High for public seeds)**
  into H8 (High), because together they make dev-split scores untrustworthy for code-executing agents.
* **P2-a G4 (High, report content)** was merged into H1, because it is the same mechanism class (content-blind
  scoring of what the robot says or reports).
* **P2-b F7 (proxy predicates, Medium)** was merged into H2 (High) with relocation and stale flags, as one class:
  end-state predicates that do not pin the outcome.
* **P2-b F8 (guess floors, Medium design)** went into H3 (seed imbalance) and H4 (guess floor).

## 6. Conflicts and corrections reconciled

* **Gaming headline.** "39 of 44" counts specs that end FAIL under `--check` (11/11 + 14/16 + 14/17). About 30 of
  them had a policy that plainly does not do the task pass on every seed it targeted: 8 by P2-a's own count; 11 by
  P2-b's ("in 11 of the 18, at least one cheat was scored as a complete success on every test house it was tried
  on"); 11 by my count of P2-c's tally table, excluding the contradictory-report game it rated Low. 3 FAILs (P2-b) came
  only from lucky guesses. The report states all of this and that the sample was chosen for attackability.
* **P1's 124 "guard-only" wrong plans.** This was a logging artifact (P2-cond F-C6). The report uses the corrected
  numbers.
* **P3-id S2** ("sleeping person shown awake"). Retracted by P3-id itself; not used.
* **Forced-realistic numbers.** C1 225/242 (older code) against 230/243 at `bc0417b` [ran: P2-gate]. The report uses
  the current number and says which is which.
* **Geometric failures.** The brief says "about 55"; P5 measured 51 (192/243). P2-gate's 220/243 under realistic plus
  geometric was a lenient single-seed run, not verification, and is not used as a pass count.
* **Performance.** P4's 14.4 s per quiet day was measured at 100 % CPU and is not used as evidence of a regression. Only
  P2-gate's within-run `sweep_bench` comparison supports a "probable" regression.
* **Failing tests.** "14" are file-runs, not tests: 12 distinct tests, plus `test_eval_splits` (×2) and the
  `test_mind` flake on a fresh checkout.
* **Lines of code.** The brief says about 222k; P1 and P4 measured 216,794.

## 7. Final check (reviewer prompt: "re-read and downgrade inferred-as-proven")

I re-read every finding in REVIEW_REPORT.md against its source label. These points are kept explicitly as [inferred]
or as estimates:

* test seeds do not protect against memorisation (H4);
* the closing-time window in the rain spec;
* the seed-recovery cost on the private range (estimate, not demonstrated);
* cross-platform digest breakage (M7);
* "unadopted domains would double-apply laws" (in M4 only as "0 guards", which was measured);
* the geometric bearing tell;
* every "would catch" claim for a check that is not implemented (each marked [inferred] in section 4's status column);
* time and token estimates;
* Q2's realism judgement.

Changes made during this re-read:

* the G2-ABS, R2 and V-HELD status cells were split into what ran and what is inferred;
* H8's handle formula was relabelled [read] (the reconstruction itself is [ran]);
* the H9 "why it matters" was marked [inferred].

## 8. Hard-rule compliance

* **Rule 1.** No paid API call and no network use. No LLM agent was run in this phase, not even with `--dry-run`.
  `HOUSE_AI_FORBID_NETWORK=1` was set and every API key variable was unset.
* **Rule 2.** Nothing under the real `%USERPROFILE%\.house_ai\` was read, created, listed or copied, and no split-key
  command was run. `Path.home()` resolved to the empty fake home, which was still empty after each batch.
* **No edits outside scratch.** The live repository was not touched. Nothing was written in the frozen tree or in any
  other phase's folder (`find -newer`: 0 files). All writes went to `p6_report_scratch/`, `REVIEW_REPORT.md` and this
  file.
* **Load.** Worker counts were 1-2, and all spot checks together took about 3 minutes of wall time.
