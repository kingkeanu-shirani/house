# house_ai independent review: Phase 1 (Understand): claims register

Reviewer: Claude (Opus 5.5), subagent "p1_claims". Date: 2026-10-20 (status written at the end of Phase 1).
Code reviewed: the frozen copy of commit `bc0417b` at `scratchpad/review/tree` (no `.git`). The live repository at
`<home>\OneDrive\Desktop\house_ai` was not touched. Everything I wrote went into
`scratchpad/review/out/p1_claims_scratch/` or this file.

Evidence labels: **[ran]** = I executed it and saw the result; **[read]** = I read code or docs but did not run it;
**[inferred]** = reasoning only.

---

## Status update (end of Phase 1)

### Simple version

I read the project's own description of itself and wrote down every promise it makes that can be checked, ranked by
how much the benchmark depends on it (30 promises, listed in section 4). I also re-ran the project's own checking of
all 243 tasks on a frozen copy of the code: all 243 pass, which matches what the owner says, and a "do nothing, then
say done" robot never passed any of them. The most important problem I found is that the checker does not do
everything the project says it does: it is supposed to confirm that each deliberately wrong attempt fails *for the
right reason*, but it only confirms that it fails at all. In the re-run, about one wrong attempt in six failed only through a
general safety rule or a refused action rather than the task's own goal check, and nothing confirms that this is the
mistake the attempt was meant to show; one wrong attempt even crashes before doing its mistake and is still accepted. I also found that the
project's records overstate how "green" earlier stages were, and that the leak check behind "0 leaks" is much smaller
than the summary suggests. Nothing is blocking me.

### Advanced version

* Phase 1, about 1 h 45 min elapsed.
* Hard rules: no network or paid API call was made; nothing under `%USERPROFILE%\.house_ai\` was read, created or
  listed (I deliberately did not run `--split-info`, `--init-split-key`, `--build-test-manifest` or any `splits`
  function that probes the key or manifest path); the live repository was not edited. Worker count was 3.
* Commands and results: section 2. Claims: section 4. Findings with severity and evidence label: section 6
  (High: F1 wrong plans not checked for their reason; F2 verification mode differs from evaluation mode, including
  hidden-seed selection; F3 "green at every stage" contradicted by the C1 gate record. Medium-High: F4 small literal
  leak audit; F5 crashing wrong plan accepted. Medium: F6-F10. Low: F11-F15).
* Null and positive results: all 243 specs pass in their own mode at bc0417b [ran]; wait-then-done passes 0 of 1,215
  trials [ran]; only 1 idle trial fails solely on something other than the goal [ran]; static Python 3.9 scan clean
  [ran]; the never-selected variants pass when tested [ran].
* Hypotheses for the next phases: section 7.
* Open questions for the owner: (1) Is charging harms of inaction to the robot (62 idle trials in 18 specs) the
  intended rule? If so the attribution claim should say so. (2) Should the gate's "PASS" be allowed to carry known
  failures, or do you want absolute gates? (3) May later phases rehearse the test-split flow in a throwaway temporary
  home (SPLITS.md "Sandboxes"), which writes a throwaway key under a temporary folder, never under your real
  `%USERPROFILE%`? I have not done it and will not without your yes.
* Next: Phase 2 (gaming policies on 15+ specs, the 14 failing kernel tests, the gate, cross-variant always/never
  policies, forced realistic and geometric runs).

---

## 1. What I read

All of: the brief; the reviewer prompt; `DECISIONS.md`; `docs/TASK_SPEC.md`; `docs/PERCEPTION.md`;
`docs/KNOWN_LIMITS.md`; `docs/EVAL_CONTRACTS.md`; `docs/SPLITS.md`; `docs/design/TIER1_DECISIONS.md`;
`docs/AUTHORING_GUIDE.md`; `docs/EVALUATION.md`; `RESUME.md`; the tail of `PROGRESS.md`.
Skimmed: `docs/design/TIER1_INTEGRATION.md` (headers, sections 2.6, 3, 4, 7), `docs/EVAL_HARNESS.md` (Limits),
`docs/PHYSICS.md` sections 1-5, `reports/leaderboard.md`, `catalog/analysis/tier1/handoff_C1.md`, the C1 gate record
`catalog/analysis/tier1/gate/C1/*`.
Code read for claim checking: `house_sim/tasks/verify.py` (all), `house_sim/tasks/runner.py` (plan runner, build),
`house_sim/tasks/spec.py` (merge, variants), `house_sim/kernel/evaluation.py` (global flags, side effects),
`house_sim/kernel/realism.py` (`Handles`, `to_handles`), `house_sim/audit.py` (`fingerprint`),
`house_sim/eval/splits.py` (`verified_specs`, `build_test_manifest` signature), `house_sim/eval/run.py` (verify
defaults), `tools/perception_audit.py` (docstring and `main`), `tools/orchestration/tier1_gate.py` (G4/G6 commands),
several domain harm sites (`systems/pets/play.py`, `systems/people/children.py`, `systems/climate/moisture.py`,
`systems/cleaning/systems.py`, `systems/safety/fire.py`).

## 2. Commands run and results

All from the frozen tree as working directory; `S = ../out/p1_claims_scratch`.

| # | Command | Result |
|---|---|---|
| 1 | `python $S/spec_inventory.py --json $S/spec_inventory.json` | 32 files, 243 specs, 243 distinct catalog ids, no duplicates; 51 realistic, 8 geometric, 2 schema 3; kinds T 200 / A 24 / X 14 / P 4 / V 1; variants: 104 none, 83 two, 54 three, 2 four; fidelity S 84 / G 78 / P-approx 81; 106 specs label `fidelity` below `target_fidelity`; rubric in 0 specs |
| 2 | `python $S/variant_merge_stats.py` (uses `spec.merge`) | after merging variants every spec has >= 1 wrong plan (min wrong plans: 1 in 10 specs, 2 in 114, 3 in 112, 4 in 7) and >= 3 phrasings; seed->variant patterns: 2 variants -> (0,1,0,1,1) in 83 specs; 3 variants -> (0,1,2,2,1) in 54; 4 variants -> (0,1,2,1,1) in 2 |
| 3 | catalog / batch counts (inline Python) | 2,720 entries (task 2,240, adaptability 253, primitive 108, variation 74, appendix 45), 105 sections; 52 batch files hold 2,478 ids, one of which (`T-d1f9385dbb80fc-01`) is already authored in `people__perception.json`, so 2,477 remain |
| 4 | evidence freshness (inline Python, `house_sim.audit.fingerprint()`) | current fingerprint `c72c3376...`; `reports/verification.jsonl` has 242 records, **0** at the current fingerprint (all `9fa0c985...`, the C1 gate's), 241 passed / 1 failed (`A-d9e7a4d5d112d2-01`), and the 243rd spec has no record; `reports/verification_run.json` says 241/242, generated 2026-10-15 |
| 5 | read `catalog/analysis/tier1/gate/C1/{summary.txt,specs_summary.json,meta.json,audits.json}` | the last gate record in the tree (stage C close, 2026-10-15) is overall **FAIL** (G1 `test_eval_splits.py` failing in both runs; G10 scope); G2 own mode 241/242 labelled PASS; G1b "PASS" with 8 failing isolated-domain tests; G3 forced realistic 225/242 (17 specs fail: list in section 5); G4 perception audit = **6 houses x 150 steps** per camera mode (8.9 s and 10.1 s); G6 fuzz = 6 houses, 1,800 commands per mode, of which 395 (room) / 546 (geometric) / 208 (battery) succeeded |
| 6 | `python $S/variant3_probe.py` | the variant that seeds 0,1,2,41,97 never select (index 3 of the two 4-variant plumbing specs) **passes** verification on seeds 3, 7 and 11 for both specs (null result for "broken", positive for "the gap is coverage only"); in `T-d25af2202bc025-01` variant 3 the wrong plan "plunges with the flat sink plunger, which cannot seal" fails with `burns`, `safety_violations` (the chemical scenario), not for its stated reason |
| 7 | `python $S/py39_scan.py house_sim tools tests` | 441 files, **0** syntax errors under `ast.parse(feature_version=(3, 9))`, 0 PEP 604 unions in runtime-evaluated annotations, no 3.10+ stdlib API found by the heuristic list (hits were false positives: `int.from_bytes(..., "big")`, a format string, `fromisoformat` on plain ISO strings) |
| 8 | `python $S/size_counts.py` | `house_sim` 363 .py files / 216,794 lines (216,341 without `legacy_v1`); 55 test files / 71,455 lines; 17 type files with 2,037 entries under `"types"`; 427 registered actions after `load_domains()`; 16 domains |
| 9 | wrong-plan key census (inline Python over merged variants) | 854 wrong-plan objects; keys used: `name` 854, `plan` 854, `camera` 14; **no key states an expected failure reason** |
| 10 | analysis-record cross-check (inline Python) | 103 authored specs have a non-null `conditional` in their analysis record; **25 of them have no `variants`**; 12 specs set a `risk_level` that differs from the analysis record's risk (e.g. `T-6d58f62bb5256f-01` 0 vs 2, `T-b1456a2d293d2f-01` 1 vs 2) |
| 11 | scoring-feature census over merged variants | side_effects 109 specs, failure predicates 46, preserve 13, milestones 7, questions 7, deadlines 4, order 3, allow 3, responses 1, required_milestones 1; never / always / phases / rubric / request: 0 |
| 12 | `grep` of harm-stat increments in `house_sim/systems` | about 40 domain modules increment global harm stats (`spills`, `breakages`, `damage`, `injury`, `falls`, `collisions`, `flood`, `fire`, `burns`) directly, including time-advance systems |
| 13 | `python $S/full_verify.py $S/verify_own.jsonl --workers 3` (fresh own-mode verification at bc0417b, full trial records) | **243 / 243 specs pass**, 1,215 trials, 1,510 s wall time, fingerprint `c72c3376...`. 255 realistic trials, all `perceivable`; 0 trials with `temporal_warnings`; 37 trials with physics-lint warnings (schema 2: warnings only); 27 trials in 7 specs used a privileged `wait_until` on hidden state (`T-73a13fb297c40f-01`, `T-6203fdc5fa8986-01`, `A-350cc067998929-01`, `T-118437362c4ab8-01` on all 5 seeds; `A-d66994a566899c-01` 3 seeds; `A-8910a09f802fbf-01`, `T-fc5c1e1b7eeccc-01` 2 seeds). `A-d9e7a4d5d112d2-01`, which failed at C1, now passes |
| 14 | `python $S/analyze_verify.py $S/verify_own.jsonl $S/verify_own_analysis.json` (output in `verify_own_analysis.txt`) | 3,109 wrong-plan runs, 770 distinct (spec, wrong plan) pairs, all fail. Failure categories (a run can have several): goal shortfall 1,947 runs, global harm flag 645, domain invariant 290, authored failure predicate 184, side effect 153, strict-block refusal 105, question 31, temporal 21, budget 10. **404 runs (97 distinct plans) fail only through a global harm flag; 92 runs (27 distinct plans) fail only through a strict-block refusal** (the unsafe step was refused, so the plan did nothing else wrong); 358 runs had at least one refused step. **40 plans fail for a different category on different seeds; 9 fail on the goal on some seeds but only on a generic label (harm flag, refusal or budget) on others** (e.g. `T-851aa9f03b6e44-01` "restarts only the router": goal on seeds 0, 2; refusal only on 1, 41, 97). **One wrong plan aborts with a plan error on every seed and is still accepted**: `T-c49fadcea69182-01` "reports the glance buckets it saw before looking in" raises `PlanError: Nothing observable at visible_entities.kitchen_trash_can_1.fill_level` before it reports anything |
| 15 | `python $S/idle_probe.py $S/idle_probe.jsonl --workers 3` (602 s) | **Wait-then-done: 0 of 1,215 trials** reach success at any sample (every max(300 s, budget/48)) of a do-nothing run (null result, good for the project at this granularity). **Goal met but idle "fails" for another reason: 1 trial** (`A-d9e7a4d5d112d2-01` seed 0: every success predicate holds after waiting; only `response:quiet`, the response deadline, fails it). 62 idle trials in 18 specs end with a global harm flag raised while the robot only waited (e.g. `T-123d987460eb2a-01` gas_exposure + safety_violations; `T-032657e9583337-01` damage + safety_violations on all 5 seeds). A do-nothing agent's mean goal fraction is 0.303 (same at t=0 and at the end); in 103 specs at least one idle trial ends with goal fraction >= 0.5 |
| 16 | budget slack (inline Python over row 13 + `runner.build`) | time budget / reference time: median 22x, 10th percentile 2.6x, 90th 69x, max 900x; 913 of 1,215 trials have more than 10x slack. Step budget / reference steps: median 6x (10th percentile 3.1x) |

## 3. Scripts I wrote (all in `scratchpad/review/out/p1_claims_scratch/`)

| Script | Purpose |
|---|---|
| `spec_inventory.py` | per-file and per-spec inventory (perception, camera, schema, variants, wrong plans, fidelity, scoring keys); flags variants the fixed seeds never select; writes `spec_inventory.json` |
| `variant_merge_stats.py` | per-variant merged counts via `house_sim.tasks.spec.merge`; writes `variant_merge_stats.json` |
| `variant3_probe.py` | verification trials of the never-selected variant 3 on seeds 3, 7, 11; writes `variant3_probe.json` |
| `py39_scan.py` | static Python 3.9 compatibility scan |
| `size_counts.py` | code, test, type and action counts |
| `full_verify.py` | fresh `verify_many` over all authored specs, keeping every trial record including each wrong plan's `why` |
| `analyze_verify.py` | pass/fail summary and classification of why each wrong plan fails (goal shortfall, authored failure predicate, strict-block refusal, side effect, global flag, invariant, temporal, question, budget), plus cross-seed reason changes |
| `idle_probe.py` | two attacks on the idle/initial checks: wait-then-declare-done (samples `evaluate()` during an idle run) and "idle fails for a reason other than the goal" |

---

## 4. Claims register

Ordered by how much the benchmark's trustworthiness depends on the claim. "Status after P1" says what Phase 1 already
established; every "how to test" is concrete enough to run in a later phase.

### P0: the benchmark is only as good as these

| ID | Claim (as the project states it) | Where claimed | Status after P1 | How a reviewer can test it |
|---|---|---|---|---|
| C1 | A spec is DONE only when, on seeds 0, 1, 2, 41, 97: the initial state fails, idle fails, the reference passes strictly with no refused step, and **each wrong plan fails "for the stated reason"** | brief s.1; `verify.py` docstring; TASK_SPEC; AUTHORING_GUIDE rules 5-7; EVALUATION.md | The initial, idle and strict-reference checks are implemented as stated [read]. **"For the stated reason" is not implemented**: wrong plans carry only `name`, `plan`, `camera` [ran: census of 854], and `run_trial` sets `fails = not outcome["success"]` [read `verify.py:188`]; the reason is only recorded (`why`, first 5 labels). Probe 6 shows a wrong plan passing verification by failing for another scenario's reason [ran]. Fresh run (rows 13-14) [ran]: 97 of 770 wrong plans fail on some seed only through a global harm flag, 27 only through a refused step, 40 change failure category between seeds, and one aborts with a `PlanError` on every seed yet counts as "ran" (`record["ran"] = isinstance(exc, PlanError)`, `verify.py:185`), contrary to AUTHORING_GUIDE rule 5. Whether a harm-flag-only failure is the "stated reason" needs a per-plan reading; the point is that nothing checks it | Add an `expect` field per wrong plan (label prefixes or predicate indices); re-run with a check that `why` intersects it. Mutation test: delete the step that embodies the named mistake from each wrong plan; if the plan still fails, the failure is not attributable to that mistake |
| C2 | "Idle fails" means a do-nothing policy cannot pass | brief s.1, AUTHORING_GUIDE rule 6 | Partly contradicted by design [read]: success is checked only at t=0 and at the end of the budget (`verify.idle`, hour-long waits), never in between, and `idle_fails` is true for any failure label, including global harm flags caused during the wait. In practice it holds [ran, row 15]: no do-nothing run reached success at any 300 s sample, and only 1 of 1,215 idle trials met every success predicate while failing on something else (a response deadline). Granularity caveat: success windows shorter than the sampling interval would be missed | `idle_probe.py` (attack 1: sample `evaluate()` every max(300 s, budget/48) during an idle run; attack 2: goal met but evaluation fails for another reason) |
| C3 | Specs are hard to game: moving the mess elsewhere, acting on the wrong instance, etc. fail through side effects, preserve, failure predicates and global harm flags | brief s.6.3, AUTHORING_GUIDE rule 3 | Coverage is thin [ran]: side_effects in 109 / 243 specs, preserve 13, failure predicates 46; whole files have no side_effects at all (climate, electrical, outdoor_plants examples, pets examples, safety, textiles examples). `side_effect_failures` **skips entities that no longer exist** (`if eid in allowed_moves or eid not in now: continue`; the same for `no_damage` and `unchanged_fields`) [read `evaluation.py:56-90`], so destroying, consuming or merging a protected item is invisible to these rules | Phase 2 gaming policies per spec (move the mess to another room, act on a look-alike, dispose of a protected item, wait-then-done, exploit budget). For the destroyed-entity gap: a scenario where a verb destroys a protected item (consume, merge, pour out, shred) under `no_moves_except` |
| C4 | Conditional entries vary across seeds (`variant = seed % len(variants)`) so always-do and never-do policies each fail some seed | brief s.1; AUTHORING_GUIDE rule 4; TASK_SPEC | Weaker than claimed [ran]: (a) 2 specs (`T-d25af2202bc025-01`, `A-c627488c575314-01`) have a 4th variant the fixed seeds never select (it passes on seeds 3/7/11, probe 6); (b) in all 54 three-variant specs variant 0 is verified on exactly one house (seed 0); (c) 25 of the 103 specs whose analysis record is conditional have no variants, so any branching must come from setup helpers (unverified); (d) the verifier never runs always-do / never-do policies, it relies on authors writing them as wrong plans [read] | Synthesize the cross-variant policies: run variant A's reference on variant B's seeds (the "always-do-A" policy) and check it fails; for the 25 no-variant conditional specs, record whether the reference's action sequence or the helper's branch differs across the 5 seeds |
| C5 | All 243 specs pass at bc0417b (242/242 at 0e77b67) | brief s.2, s.5 | **Confirmed for own mode at bc0417b** [ran, row 13: 243/243, 1,215 trials]. Committed evidence does not support it: `reports/verification.jsonl` is stale (0/242 records at the current fingerprint), records one failure and has no record for the 243rd spec [ran]; the C1 gate (the last gate record in the tree) says 241/242 [read] | Fresh `verify_many` (done: row 13); re-run with `--perception realistic` and `--camera geometric` |
| C6 | Realistic-mode verification shows a camera robot can solve the spec: "the reference may not report a value it never observed" | brief s.3.6; PERCEPTION "Verification in realistic mode" | Narrower than it sounds [read]: (a) the check covers only success predicates on `reports.*` paths and typed reports; (b) a number counts as observed if **any** number in **any** observation or message of the run is within tolerance, a string if it appears as any token anywhere (`verify.py:206-272`); (c) the reference's *choice of target* is privileged: plans name true ids, `to_handles` maps them, skills route on privileged state, `save_state` reads privileged state; nothing checks that the reference resolved a look-alike before acting on it; (d) 192/243 specs are verified only in oracle mode while every harness episode is realistic; 17 fail under forced realistic at C1 | (1) Re-run realistic specs with a stricter perceivability check: the value must have appeared in a record of the same entity and field. (2) For each realistic reference, list acts on look-alike members the robot had not resolved at that step. (3) Forced-realistic and forced-geometric runs of all specs |
| C7 | Realistic perception has 0 leaks (automated auditor; several red-team rounds) | brief s.3.2; PERCEPTION "Verification in realistic mode"; gate G4 | "0 leaks" is an absence of evidence from a small, literal-match audit [read]: the gate audit is 6 houses x 150 random steps per camera mode (about 10 s); it runs generated houses, never the authored spec scenarios (no spec is loaded in `tools/perception_audit.py`); it checks literal leaks (true ids, hidden fields, exact values, order of id-keyed JSON) and does **no** differential / counterfactual test (two look-alike members, two hidden values, same actions: compare observations, messages, durations, refusal categories) | Phase 3: differential leak hunters (swap a look-alike member or a hidden value and diff every observation and message byte-for-byte); timing and duration probes; cross-episode handle stability; run the auditor's checks over the 51 realistic specs' reference and wrong-plan runs |
| C8 | Harm attribution: robot-caused consequences fail tasks; exogenous ones (pets, scripted events) do not | brief s.3.5; PHYSICS s.3 "Attribution"; TIER1_DECISIONS | True for the kernel physics layer by construction (`_robot_moved` markers) [read]; **not a whole-system property**: about 40 domain modules increment the same global flags directly, several inside time-advance systems (`people/children.py` adds `injury` when a child grabs a reachable hazard during a wait; `climate/moisture.py` adds `spills`, `flood`, `damage`; `safety/fire.py` adds `breakages`, `damage`; `cleaning/systems.py` adds `spills`, `injury`, `fire`, `burns`) [ran grep, read]; whether each is robot-attributed is decided per site. Observed [ran, row 15]: in 62 idle trials (18 specs) a global harm flag (damage, injury, safety_violations, gas_exposure, spills, fire, flood, pet_poisoned...) was raised while the robot only waited, i.e. harms of inaction in exogenous scenarios (a gas leak, an unattended burner) are charged to the robot. That is a defensible benchmark rule, but it is not what "exogenous ones do not fail tasks" says | For each system-side increment, build an episode where the cause is exogenous (a pre-existing hazard, a scheduled event, a pet) and check whether an idle robot's episode fails on that flag; cross-check with attack 2 of `idle_probe.py` |
| C9 | Deterministic and replayable; cross-process determinism; fuzz 0 nondeterminism | brief s.1, s.3.8; gate G6 | Cross-process: tested by the project with different `PYTHONHASHSEED` [read `tools/fuzz.py`]; builtin `hash()` is not used [ran grep]. But the gate fuzz is small (6 houses x 300 commands per mode, only 12-30 % of commands succeed) [read]. **Cross-version determinism is untested**: Python 3.12 changed `sum()` of floats to compensated summation, and `house_sim` has 426 `sum(` calls, so state digests recorded on 3.14 may not reproduce on 3.9-3.11 [inferred] | Fuzz with more houses and only valid commands; replay every reference plan twice in separate processes and compare digests; on a 3.9 interpreter (not available here) run the golden test and compare digests |
| C10 | Python 3.9-compatible | brief s.1; EVAL_CONTRACTS | Static evidence only [ran]: 0 syntax errors with `feature_version=(3, 9)`, no PEP 604 runtime unions, no 3.10+ stdlib API found. Runtime behaviour on 3.9 is unverified (no interpreter) | CI on 3.9 and 3.12+; golden digests on both |
| C11 | Hidden test split is sound: HMAC-derived seeds, nonce-blinded commitment, private paraphrases, packaging audit, contamination guards | brief s.3.7; SPLITS; EVAL_CONTRACTS A | Not exercised (I may not create a key) [read]. Points already visible: (a) candidates are verified in **each spec's own perception mode** by default (`build_test_manifest(perception=None)`), while test episodes are always realistic, so hidden seeds can be unsolvable in the evaluation mode; (b) candidate seeds are kept only when the full trial passes, i.e. the hidden set is selected on the author's reference succeeding; (c) no private paraphrase pool exists yet, so the test split would use public phrasings; (d) `test_eval_splits.py` `PackagingTests.test_gitignore_covers_private_artifacts` failed in every stage-C gate (handoff C1) | Run `tests/test_eval_splits.py` (temporary HOME/USERPROFILE and env key, per SPLITS "Tests"); check that no code path reads the real `%USERPROFILE%` when `HOUSE_AI_SPLIT_KEY` and a temporary home are set; brute-force check of an unblinded vs blinded commitment on a toy entry |
| C12 | Leaderboard verification by replay is trustworthy | brief s.3.7; EVAL_HARNESS "Leaderboard" | The project itself says replay "cannot prove who produced them (a fully re-forged log looks like a real one)" (`reports/leaderboard.md`) [read]. The privileged label comes from what run.json and logs say about themselves [read]; handles are deterministic per (seed, initial-house digest, id) [read `realism.Handles`], so on public dev/train seeds anyone with the simulator can reproduce every handle and full state [inferred] | Forge a run: take the ScriptedOracle's dev logs, strip its privileged markers, rewrite run.json, and run `--verify-submission`; it is expected to pass as standard track |

### P1: important for credibility, not for the core scoring

| ID | Claim | Where | Status after P1 | How to test |
|---|---|---|---|---|
| C13 | The geometric camera "only ever shows less", so it is not privileged | EVAL_HARNESS "Rows, labels and tracks" | It also adds channels room mode lacks: `obs.lidar` ranges, per-record `view` with bearing, elevation, `range_m` (depth camera) and `px` to 2 significant figures [read PERCEPTION]; px plus range give a size estimate that may be finer than `size_class` [inferred] | Estimate object size from `px` and `range_m` and compare with the true size; check lidar for things the frame did not list |
| C14 | No free averaging: noise is a fixed draw per (seed, entity, field, level, viewpoint) | brief s.3.2; PERCEPTION "Quantization and noise" | By construction a new viewpoint gives a new draw, so averaging over N cells is possible [read] | Agent that inspects / glances from many cells and averages; measure error vs N |
| C15 | Opaque handles; typed true ids refused; outbound messages translated; unseen swaps keep handles (documented limit) | brief s.3.2; PERCEPTION "Handles"; KNOWN_LIMITS s.4 | Handles are `obj_` + 8 hex of sha256(seed, house digest, id) and are stable across episodes of the same spec and seed [read], so repeated attempts on a seed (public dev, or repeated test submissions) allow memorisation [inferred] | Run the same dev episode twice and compare handles; repeated test submissions policy |
| C16 | Refusal wording scrubbed (permission refusals collapse to one category); clock and durations in whole seconds | brief s.3.2; PERCEPTION | Untested in P1 | Differential test: same command on a private vs non-private look-alike, a hazardous vs safe member; compare message, category, duration, energy |
| C17 | Perceived-only viewer pages leak no unperceived rooms; dataset validator rejects true ids and secret fields | brief s.3.7; VIEWER; DATASET | Untested in P1 | Generate a viewer page from a realistic episode and grep its embedded JSON for true ids, unvisited room contents; feed the validator a planted leak |
| C18 | Physics anchors: mug survives 1.5 m onto carpet, egg breaks on hard floor; about +-15 % per-instance variation; 42 material classes for 129 material strings | brief s.3.5; PHYSICS s.1 | Untested in P1 | Direct calls to `physics.fall` / `impact` on staged items across many instances; histogram of thresholds |
| C19 | Laws duplicating domain rules are deferred until adoption (active: orphan, overflow, drips, creep) | brief s.3.5; PHYSICS s.2; KNOWN_LIMITS s.3 | Consistent across docs [read] | Read `laws.json`; run a spec with `lawbook.forced(...)` to see verdict changes (D1 preview) |
| C20 | Robot body numbers: 2.4 kWh, 0.288 Wh/m, eye 1.50 m; power-down is a consequence, not a refusal | brief s.3.5 | `profiles.json` humanoid_v1: `battery_wh` 2400, `walk_wh_per_m.normal` 0.288, `eye_height_m.standing` 1.5 [ran] | Walk until the pack is flat and check ok=True with "powered down" |
| C21 | Performance: about 3 ms per substep, 1.1 s per simulated hour, about 6.5 s per quiet day (target 3.5 s); camera watch over budget with people present | brief s.6.6 | C1 gate G7: 3.17 ms, 1.121 s, 6.453 s [read] | `python tools/bench_systems.py 40 0 1 2 3 --day` (machine is shared; numbers will be noisy) |
| C22 | Layout audit 0 problems (180 houses in the brief, 240 in the gate) | brief s.3.3; gate G5 | Gate record says 0 in 240 [read] | `python tools/layout_audit.py 30` |
| C23 | Room-mode golden byte-identical; generation-1 output byte-identical | brief s.3.3, s.3.5 | Brief s.5 says these tests now fail at bc0417b (14 failing tests in 7 kernel files, deliberately pending) | Run the 7 kernel test files and diff the golden |
| C24 | Fidelity labels are honest (S / G / P-approx), never relabelled | brief s.1; DECISIONS | 106 of 243 specs declare a fidelity below their target [ran], which is the honest direction | Sample audit: for 15 specs, read the deciding mechanic and judge the label |
| C25 | About 3,985 tests; 14 failing in 7 kernel files at bc0417b | brief s.2, s.5 | Untested in P1 | `python tools/orchestration/green_tests.py 3` |
| C26 | Evidence is keyed to a code fingerprint and goes stale on any simulation change | brief s.3.6; SPLITS | Confirmed [read `audit.fingerprint`: every `house_sim/**/*.py` except `eval/` and `legacy_v1/`, all `house_sim/data/**/*.json`, `tasks/templates/*.json`; text mode, so CRLF/LF neutral] and observed stale [ran] | done |
| C27 | LLM agents: cost caps refuse unpriced models; `--dry-run` makes no call; no network without `--allow-network` | brief s.3.7; EVAL_HARNESS | Untested in P1 | Dry-run only, with network disabled at the OS level if possible; read `llm.py` for any import-time network use |
| C28 | Temporal evaluation catches "milestone held at t=0" bugs | brief s.3.5 (sweep) | A milestone that holds at t=0 is only a **warning** (`temporal_warnings`), not a verification failure [read `verify.py:93-109`]; only a `never` without `after` holding at t=0 is an error; 7 specs use milestones [ran]; the fresh run has 0 trials with `temporal_warnings` [ran] | Make a milestone that holds at t=0 an error unless the spec opts in |
| C29 | Tier-1 sweep features (phases, temporal keys, questions, reports, rubric, geometric camera, schema 3) are part of verified authoring | brief s.3.5 | Barely exercised by the verified specs [ran]: phases 0, never/always 0, rubric 0, responses 1, questions 7, geometric 8, schema 3 in 2 | n/a (coverage fact) |
| C30 | Baseline numbers: Search 0 %, privileged oracle high | brief s.6.5 | `reports/leaderboard.md` (code `9fa0c985`, stale): dev = 55 episodes over only **11** catalog entries; oracle 96.4 %; random, search, mock 0 %; the do-nothing mock gets mean goal fraction 0.26 [read] | Re-run baselines over all verified specs |

---

## 5. The 32 authored spec files (for sampling in later phases)

Source: `spec_inventory.py` and `variant_merge_stats.py` [ran]. Totals: 32 files, 243 specs (16 domains x
`__examples` + `__perception`); 51 realistic (every `__perception` file is realistic; no `__examples` spec is);
8 geometric; 2 schema 3 (both in `safety__examples.json`). Kinds: T task, A adaptability, X appendix, P primitive,
V variation. "min wrong plans" is the smallest number of wrong plans any spec in the file has after merging variants.

| file | specs | realistic | geometric | schema 3 | with variants | kinds (T/A/X/P/V) | fidelity S/G/P | side_effects | min wrong plans |
|---|---|---|---|---|---|---|---|---|---|
| admin_inventory__examples.json | 12 | 0 | 0 | 0 | 10 | 12/0/0/0/0 | 8/3/1 | 5 | 2 |
| admin_inventory__perception.json | 3 | 3 | 0 | 0 | 3 | 3/0/0/0/0 | 3/0/0 | 2 | 2 |
| appliances_cooking__examples.json | 12 | 0 | 0 | 0 | 7 | 11/1/0/0/0 | 2/6/4 | 9 | 1 |
| appliances_cooking__perception.json | 5 | 5 | 0 | 0 | 4 | 5/0/0/0/0 | 2/2/1 | 5 | 1 |
| cleaning__examples.json | 13 | 0 | 0 | 0 | 6 | 13/0/0/0/0 | 5/2/6 | 1 | 2 |
| cleaning__perception.json | 3 | 3 | 3 | 0 | 2 | 3/0/0/0/0 | 2/0/1 | 0 | 3 |
| climate__examples.json | 12 | 0 | 0 | 0 | 4 | 11/1/0/0/0 | 3/5/4 | 0 | 2 |
| climate__perception.json | 2 | 2 | 0 | 0 | 1 | 2/0/0/0/0 | 0/1/1 | 0 | 2 |
| electrical__examples.json | 12 | 0 | 0 | 0 | 6 | 12/0/0/0/0 | 5/7/0 | 0 | 2 |
| electrical__perception.json | 4 | 4 | 0 | 0 | 4 | 4/0/0/0/0 | 3/0/1 | 0 | 3 |
| food__examples.json | 12 | 0 | 0 | 0 | 4 | 10/1/1/0/0 | 3/3/6 | 2 | 2 |
| food__perception.json | 4 | 4 | 0 | 0 | 4 | 3/0/0/1/0 | 3/0/1 | 4 | 2 |
| manipulation__examples.json | 12 | 0 | 0 | 0 | 5 | 9/3/0/0/0 | 4/3/5 | 8 | 2 |
| manipulation__perception.json | 2 | 2 | 1 | 0 | 2 | 0/2/0/0/0 | 0/0/2 | 1 | 3 |
| mechanisms__examples.json | 12 | 0 | 0 | 0 | 8 | 10/2/0/0/0 | 3/8/1 | 11 | 2 |
| mechanisms__perception.json | 3 | 3 | 0 | 0 | 3 | 2/1/0/0/0 | 1/2/0 | 3 | 2 |
| navigation_robot__examples.json | 12 | 0 | 0 | 0 | 5 | 9/1/1/1/0 | 4/7/1 | 7 | 1 |
| navigation_robot__perception.json | 3 | 3 | 1 | 0 | 1 | 2/1/0/0/0 | 0/3/0 | 2 | 2 |
| outdoor_plants__examples.json | 12 | 0 | 0 | 0 | 3 | 12/0/0/0/0 | 2/5/5 | 0 | 2 |
| outdoor_plants__perception.json | 3 | 3 | 0 | 0 | 3 | 3/0/0/0/0 | 2/0/1 | 3 | 2 |
| people__examples.json | 11 | 0 | 0 | 0 | 7 | 4/2/3/1/1 | 6/3/2 | 7 | 1 |
| people__perception.json | 4 | 4 | 0 | 0 | 3 | 2/1/0/1/0 | 2/1/1 | 3 | 2 |
| pets__examples.json | 12 | 0 | 0 | 0 | 10 | 9/1/2/0/0 | 8/2/2 | 0 | 1 |
| pets__perception.json | 3 | 3 | 0 | 0 | 3 | 3/0/0/0/0 | 3/0/0 | 1 | 2 |
| plumbing_liquids__examples.json | 12 | 0 | 0 | 0 | 7 | 10/1/1/0/0 | 1/0/11 | 12 | 2 |
| plumbing_liquids__perception.json | 3 | 3 | 3 | 0 | 3 | 3/0/0/0/0 | 2/0/1 | 3 | 3 |
| safety__examples.json | 12 | 0 | 0 | 2 | 6 | 5/2/5/0/0 | 2/5/5 | 0 | 2 |
| safety__perception.json | 3 | 3 | 0 | 0 | 1 | 3/0/0/0/0 | 2/1/0 | 0 | 2 |
| textiles_laundry__examples.json | 12 | 0 | 0 | 0 | 3 | 10/2/0/0/0 | 0/1/11 | 0 | 2 |
| textiles_laundry__perception.json | 3 | 3 | 0 | 0 | 3 | 2/1/0/0/0 | 0/2/1 | 3 | 2 |
| tools_vehicles__examples.json | 12 | 0 | 0 | 0 | 8 | 10/1/1/0/0 | 1/5/6 | 12 | 2 |
| tools_vehicles__perception.json | 3 | 3 | 0 | 0 | 0 | 3/0/0/0/0 | 2/1/0 | 3 | 2 |

Realistic specs (51): T-8fb945d433137c-01, T-c35156368a8a4b-01, T-ab9a26cd6a795e-01 (admin_inventory);
T-fa29fd5df0e544-01, T-8b457901ef8a04-01, T-47d0adb37bc85e-01, T-22bc2280c7e6be-01, T-b1456a2d293d2f-01
(appliances_cooking); T-3fb8a84f20876c-01, T-edec597b980b74-01, T-c49fadcea69182-01 (cleaning, all geometric);
T-a31398ab2abf54-01, T-94bc2f055e3996-01 (climate); T-dd81fe507682fc-01, T-e752686ae3a5fb-01, T-df60f56a4cce54-01,
T-1983509f271ebc-01 (electrical); T-d07790831be96a-01, P-386d0f28709b05-01, T-ae4fd543d7c772-01, T-3b0290e7f718c7-01
(food); A-a67431128da2e1-01, A-83f6ca95b72b85-01 (manipulation; the second geometric); A-565da7517f3394-01,
T-94a0a940224c22-01, T-1e3f1a4d26121e-01 (mechanisms); T-9615ae9f7570b4-01, T-c39a630dc5c6f6-01, A-926bc611ae9158-01
(navigation_robot; the last geometric); T-7b4f140db3b230-01, T-f7913405ff4906-01, T-e662e1ab5384e8-01
(outdoor_plants); A-c83efc7adcd8f5-01, T-5d63abe55f7147-01, P-7c2000dc580676-01, T-d1f9385dbb80fc-01 (people);
T-7ef5816c160945-01, T-2e07f1f4626671-01, T-a56d991fe37401-01 (pets); T-3b1a4024d46110-01, T-2a55a2133257d0-01,
T-1cd8e5b7e25846-01 (plumbing_liquids, all geometric); T-718ad251e38789-01, T-70a9e6a7a83f64-01, T-37ca38ac7f1389-01
(safety); A-1895a81a5051bd-01, T-ba18cc4b56e89d-01, T-6eed74e7a248a5-01 (textiles_laundry); T-f7f4105ce49060-01,
T-5a9598c809336a-01, T-b0d2729457cd9e-01 (tools_vehicles).

Sampling notes for Phase 2:
* Specs that failed under forced realistic at C1 (older code; re-check): A-8220afe27c03e7-01, A-b4642738e5e520-01,
  T-13da00933a0800-01, T-cc81e71dc2182e-01, T-e40eb6a314e5b6-01, X-404f39e74f6d5f-01 (all 5 seeds);
  T-79a861877a2a67-01, T-8b4654687e040f-01, T-8e57df5742271b-01, T-dda4e199f3f5b0-01 (3 seeds); T-3c0e5bb4fb4063-01,
  X-fee35ef3aac659-01 (2); T-28ff695081cd2c-01, T-c072906a11f55c-01, T-ec38fe45bca9ab-01, X-bf82170244be22-01,
  A-d9e7a4d5d112d2-01 (1).
* Specs with a never-selected variant: T-d25af2202bc025-01, A-c627488c575314-01 (plumbing_liquids__examples).
* Conditional (per analysis) but no variants (25): T-60511a600e1d8d-01, T-032657e9583337-01, T-69c80ea246f3d7-01,
  T-74a125191a9a0d-01, T-902505feb41f4e-01, T-5c9694a5a29d0e-01, T-9dd952bc6f470e-01, T-6203fdc5fa8986-01,
  T-60d49c00f08083-01, X-44e90cc83ba0db-01, A-4ddd51894095c7-01, T-2b0de56ca73064-01, T-457ec84b96c1ee-01,
  X-f05f58a6388176-01, T-d8d0ca913007d4-01, T-97f43b147c3c2f-01, T-118437362c4ab8-01, T-fa2552570f7b6f-01,
  T-b0ab19967436e0-01, T-151f6733397156-01, T-89530941a65283-01, X-befab43585cde3-01, X-404f39e74f6d5f-01,
  A-b4642738e5e520-01, T-9f83cbcee53083-01 (3 of them use `choose` / `randint` / `uniform` setup ops; the rest depend
  on helpers).
* Wrong plans failing only through refusals (27 distinct, e.g. `T-1aea0c41439b0a-01` "grabs the hot dish
  bare-handed", 4 refusals) or only through harm flags (97 distinct): list in `verify_own_analysis.json`.
* Specs with the least protection (no side_effects, no preserve, single wrong plan) are the natural gaming targets;
  `spec_inventory.json` has per-spec flags.

---

## 6. Findings

Ranked by impact on whether house_ai can be trusted as a benchmark. Each has what is wrong, evidence (label), why it
matters, and the smallest fix.

### F1 (High). The verifier does not check that a wrong plan fails "for the stated reason"

* **What is wrong.** The brief (s.1) and the authoring rules say each wrong plan must fail "for the stated reason".
  The code only checks that it fails. Wrong plans have no field that states a reason.
* **Evidence.** [read] `house_sim/tasks/verify.py:187-192`: `record["fails"] = not outcome["success"]`; `why` is
  recorded but never compared. [ran] census: 854 wrong-plan objects use only `name`, `plan` and (14 of them)
  `camera`. [ran] fresh analysis (row 14): of 770 distinct wrong plans, 97 fail on at least one seed only through a
  global harm flag and 27 only through a refused step; 40 fail for different categories on different seeds; 9 fail on
  the task's goal on some seeds but only on a harm flag, refusal or budget on others (`T-851aa9f03b6e44-01` "restarts
  only the router": goal on seeds 0 and 2, refusal only on 1, 41 and 97; `T-6203fdc5fa8986-01` "never closes them":
  goal on seed 0, a harm flag on the other four). [ran] probe 6: in the chemical variant of `T-d25af2202bc025-01` the
  wrong plan "plunges with the flat sink plunger, which cannot seal" fails with `burns`, `safety_violations`, i.e. not
  because the plunger cannot seal.
* **Why it matters.** Wrong plans are the benchmark's main evidence that near-miss behaviour is penalised. A wrong
  plan that fails for an incidental reason proves nothing about the near miss it names, and at the planned scale (about
  2,500 AI-authored specs) incidental failures will be common and invisible.
* **Smallest fix.** Add `"expect": [...]` to each wrong plan (failure-label prefixes such as `"moved:"`, `"burns"`, or
  `"goal"` meaning "a success predicate is false while no harm flag fired") and make `run_trial` require that `why`
  matches it on every seed where the plan applies. Back it with a mutation check (delete the step that embodies the
  named mistake: the plan should then pass or fail differently).

### F2 (High). Verification and evaluation run in different perception modes, including the hidden test seeds

* **What is wrong.** 192 of 243 specs are verified only in oracle perception, but every harness episode runs in
  realistic perception, and `build_test_manifest` verifies hidden-seed candidates in each spec's own mode by default.
* **Evidence.** [ran] inventory: 192 oracle / 51 realistic. [read] EVAL_CONTRACTS "As built" and EVAL_HARNESS
  "Limits" say so; `splits.build_test_manifest(..., perception=None)` = each spec's own mode. [read] C1 gate G3: 17 of
  242 specs fail under forced realistic, 6 of them on all five seeds (list in section 5).
* **Why it matters.** "Verified" does not mean "solvable by an agent in the evaluated mode". Unsolvable episodes
  depress every agent's score equally and make the test set noisier; hidden seeds chosen in oracle mode can be
  unsolvable in the mode they are scored in. The brief mentions the dev-split effect (s.6.4) but not the test-manifest
  default.
* **Smallest fix.** Default `build_test_manifest` and the harness's on-the-fly check to `perception="realistic"`
  (and to the D3 camera once it flips); keep oracle-verified specs out of dev and test until they pass realistically.

### F3 (High). The project's "green at every stage" history is not what its own gate records say

* **What is wrong.** Brief s.4: "Every stage ended with the full check green ... The one exception is the current
  adoption checkpoint." Brief s.5: 0e77b67 has "242/242 specs green".
* **Evidence.** [read] `catalog/analysis/tier1/gate/C1/summary.txt` (stage C close, 2026-10-15): overall **FAIL**;
  G1 `test_eval_splits.py` failing in both runs (handoff C1: "as in every stage-C gate"); G2 241/242 labelled PASS;
  G1b labelled PASS with 8 failing isolated-domain tests "known since B6"; G10 FAIL. [ran] committed
  `reports/verification.jsonl` and `verification_run.json`: 241/242, `A-d9e7a4d5d112d2-01` failing.
  The gate's own rule (TIER1_INTEGRATION 3.1) is "no change against the previous gate, never everything passes", so
  PASS means "no regression", not "green".
* **Mitigating fact.** [ran] at bc0417b all 243 specs pass in their own mode (row 13), so the current state is better
  than the last record.
* **Why it matters.** The owner and outside readers take "green" as an absolute statement; a delta gate silently
  carries known failures forward (a packaging privacy test failed through all of stage C).
* **Smallest fix.** Add absolute gates beside the delta gate: zero failing tests except an explicit allowlist with
  owner and expiry date, every spec passing; report "green" only when both hold.

### F4 (Medium-High). "0 leaks" rests on a small, literal-match audit that never runs the benchmark's scenarios

* **Evidence.** [read] C1 gate `audits.json`: G4 = `perception_audit.py 6 150` per camera mode (6 houses, 150 random
  steps, about 10 s). [read] `tools/perception_audit.py` loads no authored spec: it generates houses and walks randomly.
  Its leak classes are literal (true ids, hidden or secret fields, exact values above the knowledge tier, id-keyed
  order, hazard sources, sensor gates); there is no differential test that runs the same commands against two
  look-alike members or two hidden values and compares observations, messages, durations, energy or refusal categories.
  The brief's "100 houses" runs are not in the tree.
* **Why it matters.** The leaks that matter most for an LLM agent are side channels inside the actual task scenarios
  (look-alike groups, private items, hidden hazards are staged by specs, not by the generator).
* **Smallest fix.** Run the auditor's checks on every realistic spec's reference and wrong-plan runs and on random
  continuations from spec start states; add counterfactual-twin tests (Phase 3 will prototype these).

### F5 (Medium-High). A wrong plan that crashes is accepted as evidence

* **Evidence.** [read] `verify.py:184-186`: on `PlanError` a wrong plan gets `ran = True`. [ran] row 14:
  `T-c49fadcea69182-01` wrong plan "reports the glance buckets it saw before looking in" aborts on every seed with
  `PlanError: Nothing observable at visible_entities.kitchen_trash_can_1.fill_level`, so the mistake it names (a
  report from glance buckets) never happens; the spec still verifies.
* **Why it matters.** AUTHORING_GUIDE rule 5: "A wrong plan that crashes or does nothing is not acceptable evidence."
  The code contradicts it.
* **Smallest fix.** Treat a `PlanError` in a wrong plan as `ran = False` (or require the plan to reach its last step).

### F6 (Medium). Conditional coverage is weaker than "both branches on the 5 seeds"

* **Evidence.** [ran] `variant = seed % len(variants)` on seeds 0, 1, 2, 41, 97: the 4th variant of
  `T-d25af2202bc025-01` (drain cleaner in the toilet: "do not plunge") and of `A-c627488c575314-01` is never verified
  (it passes on seeds 3, 7, 11: probe 6); in 54 three-variant specs, variant 0 is verified on one house only. [ran] 25
  of 103 specs whose analysis record is conditional have no variants. [read] No automatic always-do / never-do check.
* **Why it matters.** The conditional entries are where "always act" and "never act" policies are supposed to fail.
* **Smallest fix.** Pick verification seeds per spec so every variant gets at least two houses (keep 0,1,2,41,97 and
  add seeds until covered), and add an automatic cross-variant check (variant A's reference run on variant B's seeds
  must fail).

### F7 (Medium). Side-effect rules cannot see a protected item that was destroyed

* **Evidence.** [read] `kernel/evaluation.py:56-90`: `no_moves_except`, `no_damage` and `unchanged_fields` all
  `continue` when `eid not in now`.
* **Why it matters.** Consuming, merging, pouring out, shredding or otherwise destroying a protected thing passes
  these rules; only a global flag or a failure predicate written for it would catch it. Not yet shown on a spec
  (Phase 2).
* **Smallest fix.** Report `removed:<eid>` for baseline entities missing at the end unless allowed.

### F8 (Medium). The realistic perceivability check is weak, and reference target choice is privileged

* **Evidence.** [read] `verify.py:206-272`: a reported number counts as observed if any number anywhere in the run's
  observations or messages is within tolerance; strings match any token. Only `reports.*` paths and typed reports are
  checked. Plans name true ids (`Runner.do` maps them with `to_handles`), skills route on privileged state, and nothing
  checks that the reference resolved a look-alike before acting on it. 7 references poll hidden state with a
  privileged `wait_until` [ran, row 13].
* **Why it matters.** "Verified in realistic mode" is the project's evidence that a camera robot can solve the task.
* **Smallest fix.** Bind the check to (entity, field) records the reference saw at the needed tier; log and fail
  reference acts on unresolved look-alikes; finish the M-WAITS migration.

### F9 (Medium). Harm attribution is a kernel-physics property, not a system property

* **Evidence.** [read] PHYSICS s.3 attribution is implemented through `_robot_moved` markers for kernel consequences;
  [ran grep] about 40 domain modules raise the same global flags directly, several in time-advance systems; [ran] 62
  idle trials in 18 specs end with a harm flag while the robot only waited.
* **Why it matters.** The brief states the rule as global. Charging harms of inaction to the robot is defensible,
  but it should be a stated rule, applied consistently, and tested.
* **Smallest fix.** Route every harm increment through one kernel API with an explicit `by` / `cause`, document the
  "failure to prevent" rule, and add a test that exogenous harms in a scenario where the robot is not responsible do
  not fail an idle run.

### F10 (Medium). Committed evidence and baselines are stale; partial credit is inflated

* **Evidence.** [ran] `reports/verification.jsonl`: 0 of 242 records at the current fingerprint; 243rd spec missing;
  so evidence-mode selection (`verified_specs()` default, harness `verify="evidence"`) would select nothing at bc0417b
  [read code]. [read] `reports/leaderboard.md`: dev baseline = 55 episodes over only 11 catalog entries; the do-nothing
  mock scores mean goal fraction 0.26. [ran] row 15: a do-nothing agent's mean goal fraction over all 243 specs is
  0.303, and 103 specs give it >= 0.5 on some seed.
* **Smallest fix.** Regenerate evidence in the checkpoint commit (or add a check that evidence fingerprint equals the
  code fingerprint); report goal progress relative to the initial state, not raw goal fraction.

### F11 (Low-Medium). Large budgets

* **Evidence.** [ran] row 16: median time budget is 22x the reference's time (913 of 1,215 trials over 10x), step
  budget 6x.
* **Why it matters.** Generous budgets let trial-and-error and exhaustive policies pass; efficiency is not scored.
  Wait-then-done found nothing (row 15), so this is a gaming surface, not a demonstrated exploit.
* **Smallest fix.** Budgets of about 3x the reference (or a time-efficiency score) unless the entry is about waiting.

### F12 (Low). Verified specs barely exercise the tier-1 evaluation features

* **Evidence.** [ran] phases 0, never/always 0, rubric 0, responses 1, questions 7, geometric 8, schema 3 in 2.
  [read] a milestone that holds at t=0 is only a warning.
* **Why it matters.** The adaptability and judgment entries (253) depend on rubrics and questions that no verified
  spec yet uses; their scoring is untested at catalog level.

### F13 (Low). Stale or contradictory documentation (each [read] unless marked)

* KNOWN_LIMITS s.7 describes the pre-stage-C world (fixed-length waits, no notice bus, milestones only at step end,
  one instruction per spec), contradicting TASK_SPEC and EVALUATION.md, which document stage C as built.
* KNOWN_LIMITS s.6: "no spec can score what the robot learned" vs TASK_SPEC / AUTHORING_GUIDE `mind.map` / `mind.seen`
  predicates; "report vocabularies not published yet" vs the shared report vocabulary.
* KNOWN_LIMITS s.1: 242 DONE, 226/242 under forced realistic, about 178 geometric passes; brief: 243, 225/242, "about
  55 fail" (242 - 178 = 64).
* Brief: 2,478 entries still to author; [ran] 2,477 (one batched entry, `T-d1f9385dbb80fc-01`, is authored).
* Brief: system prompt about 31k tokens; EVAL_HARNESS: about 25k tokens (90 KB).
* EVALUATION.md: harm flags "are not produced by a physical consequence model" (stale since kernel v3.0).
* TIER1_INTEGRATION header: "Nothing here is implemented yet" (stages A-C landed).
* RESUME.md describes the 2026-10-10 pause; DECISIONS.md says git is not installed and runtime was tested on
  Linux/Python 3.12.
* Brief s.2 "about 222,000 lines": [ran] 216,794 in `house_sim` (216,341 without `legacy_v1`); "≈ 74,000 test lines":
  71,455; "2,042 types": 2,037 entries under `"types"` keys (my count may miss other layouts). Close enough to be
  approximations, noted for precision.

### F14 (Low, inferred). Cross-version determinism is unproven

* [ran] static 3.9 scan is clean. [inferred] Python 3.12 changed float `sum()` to compensated summation and
  `house_sim` has 426 `sum(` calls, so state digests, goldens and evidence recorded on 3.14 may not reproduce on 3.9-3.11.
  Unverifiable here (no 3.9 interpreter). Fix: CI on the oldest and newest supported versions.

### F15 (Low). Hidden-test design details

* [read] Hidden seeds are kept only where the author's reference passes (selection on the reference's success); no
  private paraphrase pool exists, so the test split would use public phrasings; handles are deterministic per (seed,
  initial-house digest, id), so repeated attempts on the same hidden seed see the same handles (memorisation across
  submissions) [inferred]. Fix: limit test submissions, rotate seeds, write the paraphrase pool before any public run.

---

## 7. Hypotheses for later phases (from this register)

| # | Hypothesis | Test (phase) | What would refute it |
|---|---|---|---|
| H1 | Some specs pass a gaming policy that moves the mess elsewhere or acts on a look-alike, especially in files with no side_effects (climate, electrical, outdoor_plants, pets, safety, textiles examples) | write 2-3 gaming plans for 15+ specs from section 5 and run them through `run_plan` + `evaluate` (P2) | every gaming plan fails for the task's own reason |
| H2 | Destroying a protected item passes `no_moves_except` | find a verb that destroys (consume, merge, pour, shred) and apply it to a protected item in a spec with `no_moves_except` (P2) | the item is reported (`moved:`) or a failure predicate catches it |
| H3 | Always-do / never-do policies pass some 2-variant specs because one branch's reference also satisfies the other branch's success | cross-variant runs (reference of variant A on seeds of variant B) for all 139 variant specs (P2) | every cross-variant run fails |
| H4 | Under forced realistic and geometric camera many more specs fail than in own mode at bc0417b | `full_verify.py --perception realistic` and a `--camera geometric` variant (P2) | counts close to C1's 225/242 and the brief's "about 55" |
| H5 | Look-alike members are distinguishable through action durations, energy, refusal category or message length | counterfactual twins: same commands, member A vs B, diff everything (P3) | byte-identical observations and messages |
| H6 | Averaging noisy glance estimates over many viewpoints recovers values finer than the bucket | multi-viewpoint averaging agent (P3) | error does not fall with N |
| H7 | A forged dev run (oracle logs with privileged markers removed) verifies as standard track | `--verify-submission` on an edited copy of an oracle run in a scratch results dir (P2/P7) | verifier flags it |
| H8 | The 14 failing kernel tests at bc0417b are all "pending re-record" as the brief says | run the 7 kernel test files (P2) | a failure that is a real defect |
| H9 | The perception auditor, run on spec scenarios, finds leaks it does not find on generated houses | adapt the auditor's checks to the 51 realistic specs' runs (P3) | 0 leaks there too |
| H10 | Harm flags raised by domain systems fail tasks when the cause is exogenous and the robot could not prevent it | targeted scenarios per system-side increment (P2) | attribution holds everywhere |

## 8. What I could not verify in Phase 1

* Anything about the hidden test split that needs a key or manifest (forbidden by rule 2); I relied on code and docs.
* Runtime behaviour on Python 3.9 (only 3.14 is installed); only static checks.
* The 14 failing kernel tests, the gate tool, the perception, layout and fuzz audits: not run in Phase 1 (Phase 2/3).
* Forced-realistic and forced-geometric verification at bc0417b: not run in Phase 1 (the C1 numbers are from older code).
* Whether the 25 conditional specs without variants still branch across seeds through their setup helpers.
* Gate records other than C1 (the adoption checkpoint's gate output is not in the tree; the C1 directory is the only
  one), so the brief's claims about bc0417b audits rest on the brief alone.
