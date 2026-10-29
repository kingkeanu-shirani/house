# Handoff WP5-fix (run A review findings against WP5)

Findings fixed: A-bugs-1, A-bugs-5, A-bugs-6, A-spec-5, A-spec-6, A-spec-7, A-spec-8, A-spec-11, A-gaming-3,
A-gaming-11 (`catalog/analysis/phase2/review_A_*.json`, verdicts in `verify_A_*.json`).

Files (only WP5's, P2-WP5): `house_sim/tasks/verify.py`, `house_sim/tasks/runner.py`, `house_sim/tasks/strength.py`,
`house_sim/tasks/escapes.py`, `house_sim/tasks/runcache.py`, `house_sim/cli.py`, `tests/test_verify_hardening.py`,
this handoff, `WP5-fix.json` and a pointer section in `handoff_WP5.md`. Nothing else was edited.

Each finding is fixed at its root. Each has a regression test in `tests/test_verify_hardening.py`, class
`ReviewFindingTests` (12 tests), that replays the finding's repro (its spec, record or rows) and asserts the fixed
outcome.

All 12 tests fail on the tree before the fix: 5 failures and 7 errors. That tree is a scratch copy with this run's
23 behaviour changes undone (`scratchpad/p2_WP5-fix/make_prefix_tree.py`, output in `prefix_run.txt`). The
failures are the pre-fix outputs the verifiers recorded:
* PROVISIONAL where PENDING_GATE is expected;
* a cache hit across domain sets;
* the warning instead of the error;
* steps 40 instead of 80;
* phase budgets that pass.

The errors are reads of what the fix adds: `spec_error`, `error_category`, `refused_categories`, `record["lookahead"]`,
`rec["phases"]` and `price(..., reviews=, key=)`. The class passes on the live tree. The reviewers' own repro scripts
were also re-run on the fixed tree, and their outputs flipped as expected
(`scratchpad/p2_WP5-fix/repros_after.txt`):
* `standard_gate.py`: PENDING_GATE and passes.
* `ref_specerror.py`: no harness error.
* `p_forge.py`, `v_other.py`, `r5_escapes.py`: g9 False.
* `p_wp5_units.py`: (b) formula_ok False and (c) steps 80.
* `v_phase_budget.py`: authored 1800 / 72000, formula_ok False.
* `r1b_refused_mistake.py`: G2 False.

## What landed

1. **A-bugs-1: a level is judged on the gates it runs** (`strength.py`).
   * `required_gates(record, gate_map)` returns G1-G8, minus G6 at the quick and standard levels, where S7 never
     runs (G6 is None there).
   * `verdict` uses it. A clean standard record with a projected score of at least 75 is now PENDING_GATE (or
     PENDING_REVIEW). Below the full level it is never HARDENED.
   * At the full level G6 is still required. `gates()` gives False when S7 did not run there.
   * The projected score (24 item 2): `projects_heldout(record)` holds for a quick or standard record. `score()` then
     leaves c_heldout out of the weights and renormalises over the rest (`scored["projected"]`, `basis.c_heldout`).
     At the full level a held-out stage that did not run is still 0 (P4).
   * So `--check --level standard` can exit 0, and the stop-loss can be cleared.
2. **A-bugs-5: the run-cache key holds the environment** (`runcache.environment()`, used by `verify._mode_job`). It
   covers:
   * `plugins.domain_modules()`, which `HOUSE_SIM_DOMAINS` restricts;
   * the sizes of SYSTEMS, ACTIONS and SKILLS (what this process actually loaded);
   * `HOUSE_SIM_DOMAINS`, `HOUSE_SIM_STRICT`, `HOUSE_SIM_SAFE` and `HOUSE_SIM_IDLE_PROBES`;
   * the Python version.

   A run made with other domains or switches is never reused.
3. **A-bugs-6: the reference's SpecError is the spec's error** (`reference_run`). A plan step the skill cannot take
   now records `error`, `spec_error: true` and its refusals, with no `harness_error`. That is the same as the wrong
   plans. G7 holds, G1 fails, and a held-out seed counts it as a spec failure.
   * The survey and search baselines (`_baseline_run`) treat a PlanError or SpecError as a plain error. Any other
     exception there is `harness_error`, and `_harness_errors` lists it ("seed s mode survey baseline: ...", G7,
     14.5). Before, every baseline exception was silently swallowed.
4. **A-spec-11: held-out skill debt reads WP11's `PlanError.category`.**
   * `reference_run` keeps `error_category` for a PlanError that skills.py raised itself.
   * `_skill_debt(exc_trace, refusals, category=None)` maps `"route"` to `house_sim/tasks/skills.py (route)` as skill
     debt, and `"argument"` to a spec failure.
   * `_heldout_record` passes the category, and a `spec_error` reference is always a spec failure.
5. **A-spec-5 / A-gaming-3: G9 accepts only acceptances that verify** (`escapes.py`, 10.7, decision 6).
   * **Owner rows** must carry `signature` = HMAC-SHA256 under `HMAC(split_key, "house_ai.verify.review.v1")`. That
     key is separate from the private stages' salt and is never created here. The HMAC covers
     `signed_text(row)` = JSON `[catalog_id, spec_hash, policy, category, decision]`, so tampering with the category
     or decision breaks it.
     * With the key present, an unsigned, mis-signed or tampered row does not count.
     * Without the key on this machine, an owner row cannot be verified. It does not count (P4) and is marked
       `owner_unverified`. The verdict waits in PENDING_REVIEW and the gate verifies the row.
   * **Agent rows** must name a session logged in `catalog/review/review_sessions.jsonl` with non-empty
     `session_id`, `model`, `prompt_digest` and `transcript_digest`. A missing file logs nothing, so no agent
     acceptance counts. The session must not appear in the catalog id's check log, must not be the proposer
     (`proposed_by`), and must not be `"unknown"`.
   * **Category "other"**, on either the proposal or the row, is accepted only from the owner.
   * **Blind review**: for battery and must-pass exemptions, `unrealisable` and `questions_always_required`, the row's
     category must equal the proposal's. A proposal without a category cannot be accepted.
   * Every accepted row that does not count keeps its reason in `escape["review_problem"]` and
     `price(...)["review_problems"]`. The CLI prints them.
   * `price` takes `reviews` / `reviews_path` (the logged sessions) and `key` (the split key for owner signatures).
     `verify._close` passes the verifier's `key`.
6. **A-gaming-11: a refused mistake step is unrealised** (`wrong_run`, `judge_wrong`).
   * `wrong_run` records `refused_categories` = {top-level index: [refusal categories]}.
   * `_unrealised_mistakes(rec, item)` lists the mistake steps the house refused for anything but a strict block
     (safety, privacy, social or authority) whose `strict:<category>` the plan's expect names.
   * `judge_wrong` sets `ran = false`, `error_class "UnrealisedMistake"`, `unrealised` and a `not_evidence` message
     for each such step. The review's "That exact spot is occupied" plan now fails G2.
7. **A-spec-6: dynamic lints that did not run fail G5** (P4).
   * `dynamic_facts` returns `(None, [L-DYNAMIC error])` when `lint.dynamic_facts` is missing or raises, and S8 then
     runs no dynamic pass.
   * `dynamic_lints` turns an exception from `lint.run(..., dynamic=)` into an L-DYNAMIC error. Before, it was
     raised into the verifier.
   * `DYNAMIC_LINT_ID = "L-DYNAMIC"`.
8. **A-spec-7: the look-ahead triggers from what the reference did** (6.10).
   * `operated_appliances(env, runner)` returns the restore-class appliances of `budget.json` (fridge, freezer,
     oven, dishwasher) that the run opened or operated. That is a robot write to one of the appliance's own fields
     (`env.writes.robot_wrote`, bookkeeping aside), a carried-out `open` / `close` on it, or a command that put
     something into or onto it (the trace's destination).
   * `reference_run` records the list as `operates_appliances`.
   * If no plan text or analysis record triggered the look-ahead and some reference of the S2/S3 jobs operated an
     appliance, `verify_hardened` sets `triggered` (the escape price `lookahead_short` applies). When the spec has no
     `lookahead_s` of its own, it re-runs the core-trial jobs with 21,600 s, and the first pass's CPU is booked to S1.
   * `record["lookahead"] = {"s", "triggered", "operated"}`. The CLI prints the trigger, and `draft_hardening`
     suggests it.
   * In the review's fridge case the reference now fails `later:food.cold_chain_break`: the milk left on the counter
     for 6 h is exactly the delayed harm that went unseen before.
9. **A-spec-8: the budget formula of 17, as written.**
   * `explore_v` takes the larger survey or search figure for steps and for seconds separately (a search with more
     steps keeps its steps).
   * The authored budget is the one the run uses: `runner.episode_budget(chosen, phases=None)`, which `build` now
     calls too. A phased spec without its own budget runs with the sum of its phases' budgets plus the gaps, never
     DEFAULT_BUDGET.
   * Phased specs apply the formula per phase:
     * `reference_run` records `phases` = [{"id", "steps", "time_s", "waited_s", "active_s", "ended"}] from the
       kernel's phase record at the declared-done point, with the waits inside each phase's window. The gaps belong
       to no phase.
     * `minimal_reference` keeps them (`FIGURE_KEYS`).
     * `_phase_budget_rows` checks each phase that declares a budget against the formula over that phase's figures.
       A phase no reference measured takes the whole run's figures, which is an upper bound. Each check becomes a row
       `"<variant> phase <id>"` in `budget["variants"]`, which L-BUDGET reports like a variant.
     * When every phase was measured, the episode's formula is the phases' formulas plus the gaps.
     * The reference-share check leaves the gaps out on both sides.

## Interfaces (new or changed signatures)

```python
# house_sim/tasks/strength.py
required_gates(record, gate_map) -> [gate]            # G6 dropped at quick / standard when None
projects_heldout(record) -> bool                      # S7 not run at quick / standard: c_heldout projected
score(...) -> {..., "projected": bool}                # basis["c_heldout"]: projected | held-out seeds | not run
# house_sim/tasks/escapes.py
price(spec, record=None, exemptions=None, sessions=None, exemptions_path=None, reviews=None, reviews_path=None,
      key=None) -> {..., "review_problems": {policy: why}}
review_sessions(path=None) -> {session_id: row}       # REVIEW_SESSIONS; rows need SESSION_FIELDS
sign(row, key=None) -> hex | None                     # owner signature (None without the split key)
signed_text(row) -> str                               # [catalog_id, spec_hash, policy, category, decision]
ineligible(row, sessions, category=None, reviews=None, key=None) -> why | None
eligible(row, sessions, category=None, reviews=None, key=None) -> bool   # category = the proposal's
REVIEW_SESSIONS, REVIEW_KEY_LABEL, SESSION_FIELDS, OWNER_UNVERIFIABLE, EXEMPTION_KEYS
escape dict: + "category" (exemption proposals), "review_problem", "owner_unverified"
# house_sim/tasks/runcache.py
environment() -> {"domains", "systems", "actions", "skills", "switches", "python"}; OUTCOME_ENV
# house_sim/tasks/runner.py
episode_budget(chosen, phases=None) -> {"steps", "time_s"}
# house_sim/tasks/verify.py
reference_run(...) rec: + "spec_error", "error_category", "operates_appliances", "phases"
wrong_run(...) rec: + "refused_categories"; judge_wrong: + "unrealised", error_class "UnrealisedMistake"
operated_appliances(env, runner) -> [id]; _operated_in(outs) -> [id]
_skill_debt(exc_trace, refusals, category=None)
dynamic_facts(...) -> (facts | None, rows); DYNAMIC_LINT_ID = "L-DYNAMIC"
budget_check(...): rows "<v> phase <id>" {"formula", "authored", "ok", "share_ok", "measured"}
record["lookahead"] = {"s", "triggered", "operated"}
_close(..., level, key=None)
```

## Deviations from the spec (and why)

* **The owner signature is checked here, not only by WP10's acceptance tool.** 10.7 puts the split-key check on
  `tools/battery_review.py`. A file row cannot prove which tool wrote it, though, so G9 verifies an HMAC over the
  row. Without the key the row waits (PENDING_REVIEW, `owner_unverified`) instead of counting.
* **The look-ahead trigger from the runs costs a second pass** of the core trials, and only when the plan text did
  not already show the trigger. The verdict comes from the second pass, and the first pass's CPU goes to S1.
* **The projected standard score renormalises over the components that ran.** It does not assume a held-out
  component of 1. That is the conservative choice for every pre-fix score below 90.

## Not done / refuted part

* **A-gaming-11, the do-nothing part**: a wrong plan made only of waits is still evidence. The verifier refuted that
  part (verify_A_gaming.json). Such a plan really does its named mistake (forgetting the task), fails for that
  specific reason, and is attributed by 14.4's construction, and nothing in 14 forbids it. Omission is a legitimate
  mistake (for example "waits instead of warning the child"). Its adversarial value duplicates B1, and the coverage
  belongs to G3 / G8, without which nothing is HARDENED.

## Known gaps

* A held-out seed whose reference operates an appliance that no V seed's reference touched does not re-trigger the
  look-ahead; it uses V's decision.
* `operated_appliances` counts only the restore-class names of `budget.json` `lookahead.appliances` (type, category
  or is_a). The `restore` keys of WP1's `restore.json` are not consulted.
* No exemption or review-session file exists yet (WP10). Until WP10's tool writes `review_sessions.jsonl` and signs
  owner rows with `escapes.sign`, no review escape can clear G9.

## Requests

* WP8: in L-ESCAPE (`lint_escape`), treat an escape with `owner_unverified` as a warning ("waits for the gate's
  signature check"), not "reviewer is not eligible". Use `esc["review_problem"]` as the message for the other
  ineligible rows. Document the new interfaces above (KERNEL_API "Verification core (WP5)"). `test_lint`'s
  `test_l_escape` already patches `escapes.review_sessions` and passes.
* WP10: `tools/battery_review.py accept` must append the reviewer session to `catalog/review/review_sessions.jsonl`
  with `session_id`, `model`, `prompt_digest` and `transcript_digest`. An owner acceptance must store
  `signature = escapes.sign(row, key)`, signed over the final row: catalog_id, spec_hash, policy, category and
  decision.

## Results

* `tests/test_verify_hardening.py`: 79 tests pass, both normally and with `HOUSE_SIM_STRICT=1`. That includes the 12
  new regression tests.
* Full unit suite (`green_tests.py 3`): 5,401 tests, 0 failures, normal and strict.
* All 243 authored specs pass in their own mode (`--check --workers 3`). Legacy verification is unchanged.
* `py39_lint` is clean for every file.

## How to test

```
python -m unittest tests.test_verify_hardening                      # also with HOUSE_SIM_STRICT=1
python -m unittest tests.test_verify_hardening.ReviewFindingTests   # the 12 regression tests
python -m unittest tests.test_lint tests.test_phases tests.test_verify_records
python tools/py39_lint.py house_sim/tasks/verify.py house_sim/tasks/runner.py house_sim/tasks/strength.py \
       house_sim/tasks/escapes.py house_sim/tasks/runcache.py house_sim/cli.py tests/test_verify_hardening.py
python tools/orchestration/green_tests.py 3
python -m house_sim --check <all authored specs combined> --workers 3
```
