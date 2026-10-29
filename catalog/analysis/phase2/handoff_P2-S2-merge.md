# Handoff P2-S2-merge (merge gate of run A: P2-S1 = WP1 WP2 WP3 WP4 WP7 WP9 WP11, P2-S2 = WP5 WP8)

Structured result: `catalog/analysis/phase2/P2-S2-merge.json`. Verified second signatures (for the main session to
countersign by committing as `tools/orchestration/gate_policy/signoffs/P2-S2.json`):
`catalog/analysis/phase2/P2-S2_signoff_verified.json`. Evidence: `catalog/analysis/phase2/P2-S2_evidence/`. The gate
record: `catalog/analysis/tier1/gate/P2-S2/` (summary copied to `P2-S2_evidence/gate_P2-S2_summary.txt`). Nothing
committed, staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file
LF.

## Verdict

The merged tree is sound: every unit test passes (5,413 per mode, normal and strict), every authored spec passes in
its own mode (243 / 243, 1,215 trials), the forced-realistic run and every audit equal P1C, and **no schema 2 / 3
outcome changed** -- every one of the 2,430 trials has identical verdict fields, wrong-plan reason classes, reference
steps and time to P1C; the 247 trials with changed state digests are all WP2's new logs (below). Every confirmed run-A
finding is fixed or justified (table below). The gate record ends FAIL on three checks, none of them a defect of the
merged code:

| Check | Status | Why, and what clears it |
|---|---|---|
| G8 | FAIL until signed | 14 room-golden spec entries re-recorded by WP2 (logs.gone). The 14 golden rows are verified and signed in `P2-S2_signoff_verified.json` (0 rejected); the main session commits it |
| G7 | FAIL (policy) | 2.95 ms/substep (+5.7 %), 1 h wait 1.085 s (+5.4 %), day 6.24 s (+9.4 %) vs P1C. The union `P2-S1+P2-S2` takes the strictest bench factor, P2-S2's 1.05 x (meant against a P2-S1 record that does not exist); P2-S1 / P2-WP2 have 1.15 x for WP2's attribution overhead (6.2: at most 15 %). The increase is the writer log: kernel thermal (quiet writes) +0.070 ms of the +0.143 ms system total, climate.house +0.019, food.thermal +0.012. A rules.json decision (request) |
| G10 | FAIL (not ours) | one file outside every running scope: `catalog/analysis/phase1/closeout_VER2.json`, the phase-1 verifier's result, untracked since the P1C commit. The main session commits it, then `record --id P2-S2 --package P2-S1+P2-S2 --prev P1C --only G10` (seconds) |

Why in place and not on an isolated copy: I first recorded on `tier1_gate.py isolate --package P2-S1+P2-S2` (which
leaves the stray phase-1 file out), but `tests/test_gate_tool.py` fails there (11 tests: an isolated copy is never a
policy source and has no git), so an isolated record can never pass G1; I stopped it and recorded in place on a quiet
machine (3,236 s, 7 jobs). Request to the gate tool's owner below.

## Gate record P2-S2 vs P1C (in place, `--reviewed catalog/analysis/phase2/WP2_reviewed.json`)

| Check | Result |
|---|---|
| GATE | PASS: made by the committed gate code (f450fef, code f6f735855092) |
| G0 | PASS: action fingerprint 0eec7120d723 -> 231aec38c2e2 (WP4's `report(corrects=)`, allowed; committed in f450fef) |
| G1 | PASS: 78 test files x normal + strict, 5,413 tests (normal), 0 failing runs; 27 wall-clock bounds relaxed, 0 skip exceptions; py39_lint 507 files, 0 violations; the 13 new test files listed |
| G1b | PASS: 16 / 16 isolated domains, 33 / 33 kernel files with HOUSE_SIM_DOMAINS=core |
| G2 | PASS: 243 / 243 own mode (1,215 trials); 0 verdict changes, 123 state-digest changes, 0 observation-digest changes |
| G3 | PASS: forced realistic 242 / 243 (the allowlisted both-fail T-dda4e199f3f5b0-01 seed 1, unchanged vs P1C); 0 verdict changes, 124 state-digest changes, 0 observation-digest changes; 255 realistic-spec trials identical in G2 and G3 |
| G4 | PASS: perception audit room and geometric, 0 leaks, 0 crashes (P1C: 0 / 0) |
| G5 | PASS: layout 240 houses, 0 problems, counts equal to P1C; generation-1 digests 60 / 60 |
| G6 | PASS: fuzz battery 1,800 commands 0 defects 0 nondeterministic (39 actions succeed, P1C 38); coverage 44,295 commands, 0 / 0, 278 of 427 actions (= P1C); geometric and room 1,800 each, 0 / 0; no action stopped succeeding (0 coverage rows). The battery / room / geometric success counts moved (294 -> 297, 440 -> 487, 560 -> 512 commands), most likely because the action table changed (report's new `corrects` parameter shifts the fuzzer's seeded argument draws; not A/B'd) -- the walks replay deterministically and find no defect |
| G7 | FAIL (above) |
| G8 | FAIL until the 14 golden rows are committed (above) |
| G9 | PASS: physics, body, sweep, gen-2 layout and timing (1.04), generator tests |
| G10 | FAIL (above) |

Also run: the 51 realistic specs with `--camera geometric`: 51 / 51 (`python -m house_sim --check <realistic specs>
--camera geometric --workers 3 --no-cache`, final tree); the full unit suite once more before the record
(`green_tests.py 3`, 5,413 tests per mode, 0 failures).

## No schema 2 / 3 outcome changed (WP1, WP3, WP4, WP7, and every other package)

* Verdicts: P1C vs P2-S2, own and forced realistic, all 2,430 trials: identical verdict fields (passed, initial /
  idle fails, reference passes, perceivable, wrong fails / ran), identical wrong-plan reason classes, identical
  reference steps and time. So WP1, WP3, WP4 and WP7 (which may change none) changed no outcome, and neither did WP2
  (digests only), WP5, WP8, WP9 or WP11 (whose world fix -- careful pace on winder stairs -- changes no authored
  spec's trial on the five gate seeds).
* Observation digests: 0 changes in either mode.
* State digests (247 trials of 28 specs): `P2-S2_evidence/state_attrib.py` re-ran each changed trial on the merged
  tree with its envs captured: 243 match P1C's reference, idle and wrong-plan digests exactly once WP2's
  `logs.gone` / `logs.mess_flows` are removed (141 with the reference destroying something, 106 where only the idle
  house or a wrong plan destroys something; `mess_flows` is empty in every one: WP4's core flows fire in no legacy
  trial). The other 4 (T-7fe081a3de29f8-01 seeds 0 and 2, own and realistic) were A/B'd against a `git archive HEAD`
  tree (`diff_trials.py`): the only differing paths are `logs.gone` and `people.resident_1._space_jn[1]`, the people
  senses' cursor into the step's journal keys, which counts the logs record WP2 journals when it appends to
  logs.gone (bookkeeping, `_`-prefixed). Room golden: the 14 re-recorded entries are the seed-0 own-mode cases of the
  same thing (`golden_attrib.py`: the merged tree reproduces each entry; without logs.gone each has HEAD's digest).
* The packages' outcome_changes: WP1-11 and the fix rounds list none for legacy specs; WP2-fix's and WP5-fix's are
  schema-4 only (no authored spec is schema 4), WP2-fix's snapshot keys (`idle`, `last_score`) and finish's
  millisecond rounding change no legacy trial (G2 / G3 above). Reconciled: nothing to sign beyond the golden rows.

## What the merge gate changed (each with a regression test; all in the union of P2-S1 and P2-S2)

Stubs whose dependency landed (section 25), removed so a broken dependency fails loudly instead of silently passing:

* `house_sim/kernel/speech.py`: `founded` calls `perceive.claim_founded` directly (the "without WP7 every claim is
  founded" branch and the `_perceive() is not None` guard are gone).
* `house_sim/tasks/perceive.py`: `_parse_claims` calls `speech.parse(text, state=, env=, room=)` directly.
* `house_sim/kernel/ledger.py`: the content guard calls `speech.guards` / `reports.guards` directly;
  `held_open` calls `Monitor.open_intervals(robot_only=False)` (no older-signature fallback); the stub wording now says
  the move-log path is only for a bare state without an env (every HouseEnv has WP2's writer log).
* `house_sim/kernel/engine.py`: finish reads `ledger.LOOKAHEAD_GUARDS` (engine's duplicate tuple removed),
  `evaluation.evaluation_view(..., task=)`, `evaluation.hardened` and `goal_status` directly (`_VIEW_TAKES_TASK` and
  the getattr probes removed). No behaviour change: `derived_view` returns the state itself for a legacy task.
* `house_sim/kernel/monitor.py`: the hardened view calls `evaluation.derived_view` directly.
* `house_sim/tasks/verify.py`: `_lint_module()` imports `tasks/lint.py` directly; S0's "WP8 not landed" schema
  warning, the verifier's stand-in L-COVER and the `_lint_module() is not None` guards are gone; `_open_longest` and
  `_untriggered_timing` call WP3's monitor directly, and an exception of `Monitor.timing_verdicts` is no longer read as
  "no vacuous timing item" (P4: it is a harness error of the trial).
* `house_sim/tasks/escapes.py`: `DEFAULT_PRICES` (the built-in copy of section 18 "until WP8's escapes.json lands") is
  gone; `price_list()` reads `house_sim/data/verify/escapes.json` and raises on a missing or malformed file.
* Tests: the "has not landed" skips in tests/test_perceive.py (2) and tests/test_speech.py (2) and the no-lint branch
  of `test_a_vague_expect_is_never_verified` are removed (they could only hide a broken dependency).

Integration fixes (open halves of run-A findings and cross-package requests inside the union):

* **A-gaming-13, verifier half** (WP8-fix's request to WP5): `verify.pattern_matches` / `matched_patterns` apply 14.2's
  one rule to `inv:` too (`L == p` or `L` starts with `p + ":"`; `inv:`, `inv:s`, `inv:people` matched every
  invariant label by raw prefix), and `verify.expect_problems` rejects an `inv:` / `later:inv:` pattern that is not a
  whole invariant label name (`lint.invariant_problem`). ExpectTests' pin `("inv:people", True)` became False.
* **A-spec-14, ledger half** (WP8-fix's request to WP1): `ledger.selector_problem` refuses an unknown `<word>:` form
  (an id never contains ':'; also inside id lists and JSON-list selectors) and `scope_problem` an unknown scope form,
  so `runner.build` (validate_envelope / validate_protections) refuses what L-ENVELOPE flags (`typo:book`,
  `rooms:wrok`) instead of selecting nothing.
* **WP4-fix's request to WP7 (completes A-gaming-9)**: `perceive.FACTUAL_KINDS` includes `location`, with
  `_location_founded`: founded by where the robot last perceived the subject (an observation listing its location in
  or on the place, its own placement there from the act log's `placed`, a person or pet seen in the room); a negated
  claim by a last sighting elsewhere or a look into the denied room after the last sighting in it. Before, every
  location claim was founded, so a guessed "the crate is in the hall" satisfied a `said` assert (decision 5).
* **WP8-fix's request to WP5**: `escapes.detect` (and `_targets`, `_scopes`) read a malformed declaration (a list
  envelope.move, a string protections block) as empty instead of raising AttributeError.
* **Docs** (WP8's files; the fix round's interfaces were undocumented, the A-spec-12 class): KERNEL_API.md "Run-A fix
  round" and "Merge gate P2-S2" (WP1-fix content removals, house.lost for every entity, wall-switch lights, windows
  out of restore, WriterLog.attributable / writer_at, PROJECTED_CLASSES; WP2-fix three-valued robot_attributable,
  load(path, idle=), IdleMissing, the clock grid; WP4-fix location claims, placement commits, denial / void /
  retraction frames, number words, after_safe, agency; WP7-fix dump / load of the observation log, `placed`, told,
  the agent rule), PERCEPTION.md (location claims, the agent rule, told, forks), TASK_SPEC.md (said claim patterns
  with place / relation / place_kind, the ambient defaults and content removals, whole-name inv:), EVAL_HARNESS.md
  (idle without a counterfactual, IdleMissing, robot_attributable), KNOWN_LIMITS.md 7a (link-unknown charging,
  content-guard holes, claim limits), EVALUATION.md (status).

New tests: tests/test_verify_hardening.py `MergeGateTests` (8: whole-name inv: matching, partial inv: expects,
direct lint import, malformed escape declarations, no price-list fallback, unknown selector / scope forms, a raising
timing check, finish's look-ahead guards from the ledger) and tests/test_perceive.py `LocationClaimTests` (2: the
founding rules; a guessed location fails a `said` assert in the evaluation mode and passes in oracle); updated:
`ExpectTests.test_the_14_2_grammar`, `test_lint.test_the_price_list_is_section_18` (every escape key the verifier
emits is in escapes.json, no built-in table). LocationClaimTests fail with the old FACTUAL_KINDS restored (2
failures, run); seven of the eight MergeGateTests assert exactly what the pre-merge code did differently (raw-prefix
`inv:` matching, partial `inv:` expects accepted, AttributeError in `escapes.detect`, the silent price-list fallback,
`frob:` selectors accepted, `[]` from a raising timing check, engine's duplicate guard tuple) and so fail on it by
construction; `test_the_lint_module_is_imported_directly` pins the new direct import.

## Interfaces (new or changed)

```python
# house_sim/tasks/perceive.py
FACTUAL_KINDS = ("state", "issue", "none_found", "quantity", "location")
_location_founded(idx, env, claim, sid, step) -> bool          # via claim_founded(env, claim, index=None, step=None)
# house_sim/tasks/verify.py
pattern_matches(pattern, canon)                                # inv: whole-name rule (_label_matches)
expect_problems(item)                                           # + inv: names through lint.invariant_problem
_lint_module() -> house_sim.tasks.lint                          # always the module (never None)
# house_sim/tasks/escapes.py
price_list(reload=False)                                        # escapes.json only; OSError / ValueError when unusable
detect(spec, record=None)                                       # malformed parts read as empty
# house_sim/kernel/ledger.py
selector_problem(selector), scope_problem(scope)                # unknown "<word>:" forms are problems
# house_sim/kernel/engine.py
engine.LOOKAHEAD_GUARDS removed (ledger.LOOKAHEAD_GUARDS is the one list)
```

## Run-A review findings: every confirmed finding fixed or justified

The three reviews (`review_A_bugs.json`, `review_A_gaming.json`, `review_A_spec.json`) raised 34 findings; the
verifiers (`verify_A_*.json`) confirmed 32 (A-gaming-10 and A-gaming-12 refuted). Status on the merged tree. Every
owner fix has regression tests that fail on the pre-fix tree and pass in the green suite above; "re-run" means the
reviewer's own repro script (scratch `p2_REV-A-bugs/`, `p2_REV-A-spec/`) was re-run here on the merged tree and gives
the fixed outcome.

| Finding | Owner | Sev. | Status on the merged tree |
|---|---|---|---|
| A-bugs-1 | WP5 | high | fixed (WP5-fix: `strength.required_gates`, `projects_heldout`); re-run `standard_gate.py`: verdict PENDING_GATE at standard, `level_passes` True |
| A-bugs-2 | WP2 | low | fixed (WP2-fix: finish on the millisecond grid); re-run `finish_hang.py 30.0004`: finishes after 1 frame (60: 2 frames) |
| A-bugs-3 | WP1 | medium | fixed (WP1-fix: `restore.json` exterior_door excludes windows) |
| A-bugs-4 | WP1 | medium | fixed (WP1-fix: the light fields and wall switches are ambient) |
| A-bugs-5 | WP5 | low | fixed (WP5-fix: `runcache.environment()` in the key) |
| A-bugs-6 | WP5 | low | fixed (WP5-fix: `spec_error`; baseline exceptions are harness errors); its held-out consequence was refuted by the verifier |
| A-bugs-7 | WP8 | low | fixed (WP8-fix: `spec.shape_problems`; check_structure never raises) |
| A-gaming-1 | WP7 | high | fixed (WP7-fix: `Index.told` reads only a person's own words) |
| A-gaming-2 | WP4 | medium | fixed (WP4-fix: denial and void frames, retractions, negated needs) |
| A-gaming-3 | WP5 | medium | fixed with A-spec-5 (WP5-fix); re-run `p2_REV-A-spec/p_forge.py`: g9 False for the forged owner row and the unlogged session |
| A-gaming-4 | WP7 | high | fixed (WP7-fix: the agent rule `act_gaps(agent=True)`); the outcome-matching residual is a documented gap |
| A-gaming-5 | WP2 | medium | fixed (WP2-fix: three-valued `robot_attributable`; WP1-fix keeps "link unknown" charged) |
| A-gaming-6 | WP1 | medium | fixed (WP1-fix: left_open / held_open charge physical openings linked to the robot) |
| A-gaming-7 | WP4 | medium | fixed for `house.warning_inverted` (WP4-fix); the `said` half was refuted by the verifier (13.1 keeps uncertain claims out of contradictions; the remedy is `forbids` with certainty `any`, pinned in a test) -- justified |
| A-gaming-8 | WP4 | medium | fixed (WP4-fix: past commitments judged by the robot's own act); `said` checks what was said by design (13.2), falsehood is `house.false_claim`'s, which now fires -- justified |
| A-gaming-9 | WP4 | low | fixed (WP4-fix: location claims, placement commits) and completed by the merge gate: location claims need evidence (`perceive.FACTUAL_KINDS`), so a guessed location no longer satisfies a `said` assert |
| A-gaming-11 | WP5 | low | narrow part fixed (WP5-fix: `UnrealisedMistake`); the do-nothing part was refuted by the verifier -- justified |
| A-gaming-13 | WP8 | low | lint half fixed (WP8-fix: `lint.invariant_problem`); verifier half fixed by the merge gate (whole-name `inv:` matching, `expect_problems`) |
| A-spec-1 | WP1 | high | fixed (WP1-fix: the content guard); re-run `p_ledger_content.py`: `house.removed:jar.contents` ... `house.removed:milk.liquid_l` |
| A-spec-2 | WP7 | medium | fixed (WP7-fix: dump / load of the observation log); re-run `p_fork_say.py`, `p_fork_claim.py`: the fork judges as the straight run |
| A-spec-3 | WP1 | medium | fixed (WP1-fix); re-run `p_ledger_lost.py`: `house.lost:cup` in every row |
| A-spec-4 | WP4 | medium | fixed (WP4-fix); re-run `p_said_num.py`: 250 g for every spelling, 1,250 g |
| A-spec-5 | WP5 | medium | fixed (WP5-fix: owner HMAC, logged sessions, blind category); re-run `p_forge.py` |
| A-spec-6 | WP5 | medium | fixed (WP5-fix: L-DYNAMIC); re-run `p_dynfail.py`: G5 False, verdict FAIL |
| A-spec-7 | WP5 | medium | fixed (WP5-fix: `operates_appliances` triggers the look-ahead) |
| A-spec-8 | WP5 | medium | fixed (WP5-fix: explore steps and seconds apart, per-phase budgets) |
| A-spec-9 | WP2 | low | fixed (WP2-fix: the snapshot keeps the idle digest; `IdleMissing`) |
| A-spec-10 | WP4 | low | fixed (WP4-fix); re-run `p_however.py`: `house.false_claim:1.0` for every placement of "however" |
| A-spec-11 | WP5 | low | fixed (WP5-fix: `error_category`, `_skill_debt(category=)`) |
| A-spec-12 | WP8 | low | fixed (WP8-fix documented WP5 and WP11); the merge gate documented the fix round's interfaces that were still undocumented |
| A-spec-13 | WP1 | low | fixed (WP1-fix); re-run `p_timing_block.py`: `temporal` in evaluate() |
| A-spec-14 | WP8 | low | lint half fixed (WP8-fix); ledger half fixed by the merge gate; re-run `p_lint.py`: L-ENVELOPE errors and check_structure refuse `typo:book` / `rooms:wrok`; `p_protect.py`: the unbound `{oven}` reference is an L-PROTECT error |

Open: none. Cross-package requests of the fix round that lay in the union and were not done by their owners are
done above (A-gaming-13 verifier half, A-spec-14 ledger half, location evidence, escapes.detect, the docs); the rest
are listed under Requests.

## Deviations

* The record is in place, not on an isolated copy (test_gate_tool cannot pass in a copy); G10 therefore lists the main
  session's untracked phase-1 file.
* `escapes.price_list` raises instead of falling back: the price list is package data (setup.py package_data is the
  main session's request of WP1 / WP5 / WP8).
* `_location_founded` uses the subject's last perceived location (not any sighting) and today's containment of the
  recorded place; a location claim about a destroyed thing is judged the same way (its sightings stay in the index).
* `speech.founded` keeps WP4's `except Exception: return None` around `claim_founded` (an exception makes the claim
  "not judged", which never fails `said`); not changed after the record (see known gaps).

## Known gaps

* G7 and G10 need the main session (requests 1, 2).
* `speech.founded` turns an exception of `perceive.claim_founded` into None (lenient); P4 would make it unfounded.
  No test or trial raises there; a later package should decide (request 8).
* `perceive._count_members` probes `reports.count_members`, which no package provides (count reports are not scored
  for truth, 13.6); perceive's own type / category / label matching is what runs.
* `inv:<registered name>` for invariants that compose their labels at run time never matches (KNOWN_LIMITS 7a): the
  evaluator does not record which invariant produced a label (WP8-fix's request to WP1 / WP5, not done: it changes
  every evaluation result and belongs with WP-D's label prefixes).
* The agent rule cannot match a destination-less diff deposit or a liquid moved by another verb (WP7-fix's optional
  request to WP5: the reference's step outcomes on the branch-diff commands), `told` is lexical inside a person's
  words, and the location parser has the limits WP4-fix lists (KNOWN_LIMITS 7a).
* Replays of realistic / geometric episodes diverge at step 3 on HEAD as well (REV-A-bugs, pre-existing:
  T-1df40780f5ddf2-01 seed 0, `p2_REV-A-bugs/replay_plain.py`); kernel/replays.py is in no phase-2 package.

## Requests

1. Main session (G10): commit `catalog/analysis/phase1/closeout_VER2.json` (the phase-1 verifier's result, untracked
   since the P1C commit), then `python tools/orchestration/tier1_gate.py record --id P2-S2 --package P2-S1+P2-S2 --prev
   P1C --only G10`.
2. Main session (G7, gate_policy/rules.json): the union record against P1C takes P2-S2's 1.05 x (the strictest); the
   measured +5.7 % substep / +5.4 % wait / +9.4 % day is P2-S1's attribution overhead (6.2's 15 %; P2-S1 and P2-WP2 have
   1.15 x). Give this union a 1.15 x budget against P1C (e.g. P2-S2 `bench` [1.15, "prev"] for the merged record, or a
   P2-S1 record first and P2-S2 against it), then `--only G7`.
3. Main session: commit `catalog/analysis/phase2/P2-S2_signoff_verified.json` as
   `tools/orchestration/gate_policy/signoffs/P2-S2.json` (14 golden rows), then `compare P1C P2-S2 --package
   P2-S1+P2-S2 --reviewed catalog/analysis/phase2/WP2_reviewed.json`.
4. Gate tool (P2-S4 / WP10): `tests/test_gate_tool.py` fails in an isolated copy (no policy source, no git: 11 tests),
   so `isolate` + `record --root` can never pass G1; skip or re-root those tests in a copy.
5. Main session: `setup.py` package_data for `house_sim/data/verify/*.json`, `house_sim/data/verify/watch/*.json` and
   `house_sim/data/speech/*.json` (WP1, WP5, WP8 requests; escapes.json is now required at run time); regenerate
   `catalog/analysis/seed_features.json` when the generator fingerprint is final (WP5).
6. Main session: `kernel/dialogue.py` user-simulator answers inside a robot action as volition (WP2); the
   pre-existing realistic / geometric replay divergence (known gaps).
7. WP-D: tag the people, pets (pets.agents eating) and safety (evac) systems `writer="volition"` and pass `cause=` from
   fire spread (the link-unknown default charges them until then); watch/<domain>.json, truth_<domain>.json (pets:
   feed via served_g), Latched fields tables, `@hook("hazard_zones")`, invariant labels prefixed by their registered
   name; scenario helpers on ctx.placer.order / ctx.sample_roles (WP9); WP11's held-out reference failures
   (navigation_robot T-5ad61b6881821b-01 seed 502, T-9615ae9f7570b4-01 seed 502; safety X-bc4e8d373e62c5-01 seeds 5, 6,
   503, T-1970299fc6d849-01 seed 501, A-d9e7a4d5d112d2-01 variant 2).
8. WP4 / WP7 owners (next run): decide whether an exception in `perceive.claim_founded` should make a claim unfounded
   (P4) rather than "not judged".
9. WP6: the call shapes in handoff_WP5.md; `house_sim/data/verify/actions.json` (perceive.command_class); B12 denial
   frames and hedges, M5 number words; forked runs keep the perception evidence (WP7-fix).
10. WP10: verify schema-4 specs with `verify_spec(spec, "full", cache=False, key=...)` and read `verdict`; the harness's
    idle from `verify.idle_rollout`; `perceive.enable(env)` in eval/run.py; battery_review appends review sessions and
    signs owner rows with `escapes.sign`; invalidate idle rollouts cached before WP1-fix.

## How to test

```
python -m unittest tests.test_verify_hardening.MergeGateTests tests.test_perceive.LocationClaimTests   # + HOUSE_SIM_STRICT=1
python -m unittest tests.test_verify_hardening tests.test_lint tests.test_perceive tests.test_speech tests.test_ledger \
       tests.test_finish tests.test_timing
python tools/orchestration/green_tests.py 3 --json <scratch>/green.json
python tools/py39_lint.py
python tools/orchestration/tier1_gate.py record --id P2-S2 --package P2-S1+P2-S2 --prev P1C \
       --reviewed catalog/analysis/phase2/WP2_reviewed.json                       # quiet machine, about 55 min
python tools/orchestration/tier1_gate.py signoff P1C P2-S2 --package P2-S1+P2-S2 --by NAME \
       --reviewed catalog/analysis/phase2/WP2_reviewed.json --out draft.json
python catalog/analysis/phase2/P2-S2_evidence/verify_signoff_p2s2.py catalog/analysis/tier1/gate/P1C \
       catalog/analysis/tier1/gate/P2-S2 draft.json verified.json report.json --golden-before <HEAD room_golden.json> \
       --golden-after tests/data/room_golden.json --golden-attrib catalog/analysis/phase2/P2-S2_evidence/golden_attrib.json
python catalog/analysis/phase2/P2-S2_evidence/golden_attrib.py out.json        # the 14 golden entries' A/B
python catalog/analysis/phase2/P2-S2_evidence/state_attrib.py catalog/analysis/tier1/gate/P1C \
       catalog/analysis/tier1/gate/P2-S2 out.json --workers 4                  # the 247 state-digest changes
```
