# Handoff WP8-fix (run A review findings against WP8: A-spec-12, A-spec-14, A-bugs-7, A-gaming-13)

Files (only WP8's, P2-WP8): `house_sim/tasks/spec.py`, `house_sim/tasks/lint.py`, `tests/test_lint.py`,
`docs/TASK_SPEC.md`, `docs/KERNEL_API.md`, `docs/EVALUATION.md`, `docs/EVAL_HARNESS.md`, `docs/AUTHORING_GUIDE.md`,
`docs/KNOWN_LIMITS.md`, this handoff, `WP8-fix.json`, and a pointer section in `handoff_WP8.md`. Nothing else edited.

Every finding is fixed at its root, with a regression test in `tests/test_lint.py` class `RunAFindingTests` that
replays the finding's repro. All six tests fail on the tree before the fix: a scratch copy with this run's code
changes undone (`scratchpad/p2_WP8-fix/make_prefix_tree.py`, docs as they were) gives 48 failures and 25 errors in
the class, and the class passes on the live tree.

## What landed

1. **A-bugs-7: `check_structure` never raises on a malformed spec** (`spec.py`). `spec.shape_problems(spec)` checks
   the container types the other checks iterate, in any schema: `success`, `failure`, `wrong`, `variants`, `phases`,
   `setup`, `reference`, `phrasings`, `allow`, `order`, `never`, `always`, `responses`, `timing`, `preserve`,
   `required_milestones` hold lists; `house`, `budget`, `side_effects`, `milestones`, `deadlines`, `questions`,
   `params` hold objects; each variant and phase is an object whose list keys hold lists; a `"+<key>"` appends a
   list; a wrong plan is an object with a list `plan`. `check_structure` returns those problems alone when there are
   any ("success is a list (not number -1)"). Behind them it runs the old checks inside a guard: a part a validator
   still cannot read becomes the problem `malformed spec (<Error>: ...)`, and a `SpecError` becomes its message. So
   the compiler marks such a spec INVALID and goes on, instead of aborting the whole catalog compile. `merge` raises
   `SpecError` for a `"+key"` that is not a list, or whose base is not a list. Before, `expand` raised TypeError
   there. The fix also covers the cases that already crashed at HEAD (wrong null / 0 / true, variants -1 / true, a
   phase's success 5, variants [5]). A non-list success such as `"x"` or `{...}`, which HEAD let through, is now
   flagged too. All 243 authored specs still give `[]` (no compile status moves). The same goes for the lints:
   L-ENVELOPE and L-PROTECT read a malformed `envelope` / `protections` / `protect_map` part as empty (check_structure
   names it) instead of falling back to "could not be checked". A fuzz over 21 envelopes x 7 protections x 5 protect
   maps gives no such fallback from either lint.
2. **A-gaming-13: an `inv:` pattern names a whole invariant label** (`lint.py`). `lint.invariant_problem(name)` is
   used by L-EXPECT (`expect_pattern_problem`) and L-PROTECT (`protect_ref_problem`). The part before the first `:`
   must be a whole name in `lint.invariant_labels()`. That set holds the registered `@invariant` names plus the
   `<domain>.<name>` labels a registered invariant returns literally, read from its return statements (for example
   `climate.building` reports `climate.pipe_freeze`, `climate.short_cycling`, ...: 18 such labels). These patterns
   are now errors: `inv:`, `inv:s`, `inv:people`, `inv:people.`, `inv:people.unwanted`, `inv:flag:fire` and a
   trailing `inv:people.unwanted_wake:`. These are accepted: `inv:people.unwanted_wake`,
   `inv:people.fall:resident_1` and `inv:climate.pipe_freeze` (the last was rejected before, even though the climate
   domain fires it). S0 stops a spec on L-EXPECT, so the review's r1 run now ends at S0 with verdict FAIL and G5
   false. Before, the static lint returned `[]`. L-EXPECT also rejects a `{placeholder}` the setup never binds.
3. **A-spec-14: selectors, `to` scopes and narrowed protect references resolve** (`lint.py`).
   `lint.selector_problem(selector, scope, mode="selector", dynamic=None, index=None) -> (message, severity) | None`
   takes 4 modes: `selector` (any form of 6.6), `to` (a `to` scope), `room` (`work_rooms`, `agents[].to`) and `id`
   (a deliver recipient, a consumer, an agent, an `off` key's named id, a `hold_limits` key). Each check below is an
   error unless it says warning:
   * the form is one of 6.6. An unknown `<word>:` form such as `typo:book` or `rooms:wrok` is an error, because an id
     never contains `:`.
   * a prefix must be allowed in its mode (no `type:` in a `to` scope or a room).
   * every placeholder is bound by the setup. Statically that means `bound_names`: each op's `var`, spawn-count
     members, shuffle members, a robot_fault's `var_id`, a sample's `rest` and `seed`; a scenario helper's names are
     skipped. With the verifier's facts it means bound on every seed of the variant.
   * `type:` / `is_a:` name a registered type.
   * a `category:` / `tag:` that neither the registry nor the setup names is a warning (a domain may tag at run time).
   * a literal id exists on every seed of the variant. This uses a new dynamic fact,
     `"ids": {seed: {id: exists}}`, from `dynamic_facts`, which collects ids with `spec_literal_ids`.

   L-ENVELOPE runs it over `lint.envelope_selectors(scope)`, which covers every selector and scope of the envelope
   and of protections (`never_touch`, `restore` beyond the two revocation keys, `off` named ids, `hold_limits`).
   L-PROTECT rule 2 applies these checks to each reference. Every placeholder in a reference must be bound. A
   narrowed `house:` reference's selector must resolve the same way (the restore keys `doors:interior` /
   `category:light`, the mess guards, `waste` and the content guards are exempt), and its field must be a field name.
   The review's cases now give errors: `typo:book` with `rooms:wrok` gives 2, `{nosuchbinding}` / `{nosuch2}` give 2,
   and `house:changed:{oven}.clock_offset_s` without `{oven}` gives "{oven} is not bound by the setup".
4. **A-spec-12: the landed packages are documented.**
   * KERNEL_API.md v3.2 has new "Verification core (WP5)" and "Core skills (WP11)" paragraphs. They cover every
     section-25 interface with its live signature: `verify_spec` / `verify_hardened`, the parts, `Runner.trace` and
     friends, `PlanError`, seeds (`verification_seeds`, `coverage`, `heldout_public`, ...), `private.heldout` and the
     private stages, strength, `escapes.price` / eligibility / review sessions, the check log, the run cache, and the
     21.1 CLI options. They also cover `PlanError.category`, `put(..., close=False)`,
     `survey(r, rooms=None, places=None, rows=1, strict=False)` and the no-route message. WP8's new names are there
     too. Only WP6 and WP10 are still listed as in progress.
   * EVALUATION.md has a "Running it" paragraph and a corrected package status.
   * EVAL_HARNESS.md now says what WP5 landed (`idle_rollout` for the harness) and what WP10 still owes.
   * AUTHORING_GUIDE.md has the schema-4 `--check` options.
   * TASK_SPEC.md: check_structure's shape rule, the `inv:` grammar, selector and reference resolution in L-ENVELOPE /
     L-PROTECT, the `ids` fact, and the CLI levels.
   * KNOWN_LIMITS.md: the selector limits, and the `inv:` gap below.

5. **WP5-fix's requests to WP8 (landed during this run)**:
   * L-ESCAPE reports an owner acceptance that this machine cannot verify (`owner_unverified`: no split key, so the
     gate checks the signature) as a warning, not an error. As an error, S0 would have stopped every spec carrying
     an owner-accepted escape on every authoring machine. For every other row that does not count, L-ESCAPE now
     gives `escapes.price`'s own reason ("the accepted record's reviewer is not eligible (<review_problem>; 10.7)").
   * `test_l_escape` patches `escapes.review_sessions` for its eligible agent case and checks the owner warning.
   * KERNEL_API.md also documents WP5-fix's new interfaces: `strength.required_gates` / `projects_heldout`,
     `runner.episode_budget`, `verify.operated_appliances`, `L-DYNAMIC`, the new record fields, `escapes.signed_text`
     / `review_problems` / `owner_unverified`, and `runcache.environment()`.
   * TASK_SPEC.md's L-ESCAPE row is updated to match.

## Interfaces (new or changed)

```python
# house_sim/tasks/spec.py
shape_problems(spec) -> [problem]                     # container types; check_structure returns these alone
check_structure(spec) -> [problem]                    # never raises on a malformed spec (MALFORMED, SpecError)
merge(base, override)                                 # SpecError for a non-list "+key" (or a non-list base)
type_name(value); CONTAINER_LISTS; CONTAINER_DICTS; PHASE_LISTS; MALFORMED
# house_sim/tasks/lint.py
invariant_labels(reload=False) -> frozenset           # registered names + literal labels their functions return
invariant_problem(name) -> str | None                 # the name of an inv: pattern / reference
selector_problem(selector, scope, mode="selector", dynamic=None, index=None) -> (message, severity) | None
envelope_selectors(scope) -> [(where, value, mode)]   # every selector / scope of envelope + protections
bound_names(scope) -> (names, families, complete); unbound_names(value, scope, dynamic=None, index=None) -> [names]
spec_literal_ids(scope) -> [ids]; type_vocabulary(reload=False) -> (types, categories, tags)
protect_ref_problem(ref, scope, lookahead_on=True, dynamic=None, index=None)   # dynamic / index added (optional)
lint_envelope(scope, role_map, dynamic=None, index=None)
dynamic_facts(...)["ids"] = {seed: {id: exists}}      # new fact (literal ids of envelope, protections, protect map)
INV_LABEL_RE, FIELD_RE, ID_RE, SELECTOR_MODES, FORM_TEXT, CONTENT_GUARDS
```

## Deviations from the spec (and why)

* **`inv:` also accepts the label names an invariant returns literally**, not only registered names (7.3 says "a
  registered @invariant"). `climate.building` never fires a label that starts with its own name, so under the bare
  rule `inv:climate.building` would be accepted and would never match, while `inv:climate.pipe_freeze` is the label
  the house actually reports. Every accepted name is still one whole `<domain>.<name>` word, so the review's
  partial-prefix patterns stay errors.
* **An unknown `category:` / `tag:` is a warning, not an error** (20.2 says "selectors valid": error): domains may
  set tags at run time, which the type registry does not list. Unknown forms, unbound placeholders and unknown types
  are errors.
* **Literal ids and a scenario helper's bindings are checked only with dynamic facts** (S8): they cannot be known
  statically. Statically a literal id needs only the id form.
* **`check_structure` returns the shape problems alone** when there are any, because the other checks would read a
  malformed part. The fix is fail-safe: a spec is INVALID with a named reason.

## Known gaps

* The verifier's own `verify.expect_problems` still accepts `inv:`, and `pattern_matches` matches `inv:` patterns
  by raw prefix (WP5's file). WP8's L-EXPECT now stops such a spec at S0, so the hole is closed on the verifier's
  path; the verifier's fallback (no lint module) is WP5's (request).
* An invariant that composes its labels at run time cannot be named by a label: the electrical families report
  `electrical.<kind>:<id>`, so `electrical.trip_hazard` fires as `electrical.cord_trip_hazard:<id>`. Such an
  invariant can be named only by its registered name, and the verifier's prefix match never connects that name to
  the labels it reports. So `inv:electrical.trip_hazard`, which the protect classifier drafts for trip-hazard items,
  can never be live. The root fix is that the evaluator records which invariant produced each label (requests to
  WP1 / WP5 and the domains). KNOWN_LIMITS 7a says so.
* `ledger.validate_envelope` (WP1) still accepts unknown `<word>:` selector prefixes and scope words. Only the lint
  (S0) flags them; `runner.build` does not refuse them (request).

## Requests

* WP5: in `verify.expect_problems`, reject an `inv:` pattern that is not a whole invariant label name (call
  `lint.invariant_problem`). In `pattern_matches` / `matched_patterns`, match `inv:` like every 14.2 pattern
  (`L == p` or `L.startswith(p + ":")`), not by raw prefix: today `inv:people` matches every people invariant and
  `inv:people.unwanted` matches `people.unwanted_wake:<id>`. Pass the verifier's facts through to `lint.run` at S8 as
  today; the new `ids` fact comes from `lint.dynamic_facts`. `escapes.detect` raises AttributeError on a malformed
  `envelope.move` (5) or `protections` (5). L-ESCAPE then reports "could not be checked", which is fail-safe but
  unhelpful; reading such parts as empty would leave the naming to check_structure.
* WP1: in `ledger.selector_problem` / `scope_problem`, reject an unknown `<word>:` form (an id never contains `:`),
  so `runner.build` refuses what L-ENVELOPE flags. In `evaluation._invariant_labels` (and the look-ahead's
  `_harm_labels`), record which registered invariant produced each label, so that `canonical_labels` (WP5) can add
  `inv:<registered name>` and `inv:electrical.trip_hazard` can bite.
* WP-D climate and electrical (and cleaning, whose `cleaning.hidden_sharp_in_water` / `cleaning.toxic_gas_released`
  extend the name without a `:`): either have invariant labels start with their registered name and a `:`
  (`climate.building:pipe_freeze`), or rely on WP1's record. Until then authors expect the literal label
  (`inv:climate.pipe_freeze`), which L-EXPECT accepts. Under the 14.2 rule asked of WP5 above,
  `inv:cleaning.toxic_gas` would no longer match `cleaning.toxic_gas_released`.

## Verification of this run (live shared tree, 2026-10-29)

* `tests/test_lint.py`: 43 tests (6 new in `RunAFindingTests`). They pass in the normal run and with
  `HOUSE_SIM_STRICT=1`.
* The fix-undone scratch tree (`make_prefix_tree.py`) fails all 6 new tests (48 failures and 25 errors).
* `python tools/orchestration/green_tests.py 3` ran after the last edit (the other run-A fix packages' work was in the
  tree). 5,403 tests per mode, 0 failures, all 156 file runs ok in both modes; that includes
  `test_verify_hardening.py` (79 tests) and `test_lint.py`.
* All 243 authored specs in their own mode (one combined file, `python -m house_sim --check <all> --workers 3`): 243
  passed, 0 failed.
* All 243 authored specs still give `[]` from `check_structure` and `lint.run` (pinned). The R0 as-if census is
  unchanged in kind (0.4 s; no new lint ids, and no "could not be checked"). No outcome changes.
* `tools/py39_lint.py` on `spec.py`, `lint.py`, `protect_map.py`, `show_entries.py` and `test_lint.py`: 0
  violations. Every file is LF.

## How to test

```
python -m unittest tests.test_lint                        # 43 tests, also HOUSE_SIM_STRICT=1 (about 7 s)
python -m unittest tests.test_lint.RunAFindingTests       # the four findings' regression tests
python <scratch>/p2_REV-A-bugs/cs_ab.py <root>            # every case now "problems: ...", none raises
python <scratch>/p2_VRF-A-spec/v_lint.py                  # L-ENVELOPE / L-PROTECT errors for the review's typos
python tools/py39_lint.py house_sim/tasks/spec.py house_sim/tasks/lint.py tests/test_lint.py
```
