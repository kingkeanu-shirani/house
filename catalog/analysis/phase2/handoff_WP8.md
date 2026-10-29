# Handoff WP8 SCHEMA 4 + LINTS (VERIFY_HARDENING section 25; sections 5.4, 7, 8.6, 9.1, 13.2-13.8, 14, 15.4, 17, 18, 20)

Files (only WP8's): `house_sim/tasks/spec.py` (changed), `house_sim/tasks/lint.py` (new), `tools/protect_map.py`
(new), `house_sim/data/verify/protect_rules.json` (new), `house_sim/data/verify/escapes.json` (new),
`tools/show_entries.py` (changed), `catalog/review/legacy_ids.json` (new), `docs/TASK_SPEC.md`,
`docs/AUTHORING_GUIDE.md`, `docs/EVALUATION.md`, `docs/KERNEL_API.md`, `docs/PERCEPTION.md`, `docs/EVAL_HARNESS.md`,
`docs/KNOWN_LIMITS.md` (changed), `tests/test_lint.py` (new, 37 tests), this handoff and `WP8.json`.

## What landed

1. **Schema 4 in `spec.py`** (5.4, 20.1). `SCHEMAS = (2, 3, 4)`, `NEW_SPEC_SCHEMA = 4`, `HARDENED_SCHEMA = 4`,
   `TIER1_SCHEMA = 3` (the schema-3 rules -- the geometric camera's camera wrong plan -- keep keying on 3).
   `HARDENING_KEYS` (answers, envelope, absent_ok, protections, protect_map, instruction_roles, timing, settle_s,
   lookahead_s, process, fungible, informative, free_ok, battery_exempt, mustpass_exempt, budget_reason, clauses,
   scene_exception, track_open, hold_limits, branch) are in `ALLOWED`; `timing` is in `TASK_KEYS` (runner.build
   copies it, bindings substituted), `LIST_KEYS` and `PHASE_KEYS`; `merge` dict-merges `envelope` like `side_effects`
   (`DICT_MERGE_KEYS`) and appends every `+key` (`+timing`, `+battery_exempt`, `+mustpass_exempt`).
2. **L-SCHEMA where it bites.** `spec.schema_problems(spec)` (called by `check_structure`, so the compiler marks such a
   spec INVALID, and by `lint.run`): `schema` is 2, 3 or 4, and a spec whose catalog id is a catalog entry
   (`catalog/entries.jsonl`) outside the frozen `catalog/review/legacy_ids.json` (the 243 authored ids at 81e7a91)
   must declare schema 4. Ids that are no catalog entry (test and custom specs) are exempt; the compiler already
   refuses authored specs with such ids. No file: no rule (an installed package without the catalog).
3. **`check_structure` calls the WP1 / WP3 / WP4 validators** (section 25 interface): `ledger.validate_envelope`,
   `ledger.validate_protections`, `monitor.validate_timing(items, milestone names, strict=True)` per scope (the spec
   and each phase, `phase <id>: ` prefixes), and for schema 4 `predicates.validate_predicate` on every success and
   failure predicate (which reaches `speech.validate_said`) plus `reports.validate_expectation` on every `report` /
   `report_set` body. It also checks the forms of answers (kinds, groups, evidence tiers from WP7's
   `derivations.json`, `derive` ops), label lists, settle 0..600, look-ahead 0 or 3,600..86,400, roles, the protect
   map, exemption proposals (categories of 10.7 and 11.3, policy ids, reasons >= 30 characters), informative,
   budget_reason, scene_exception, clauses, track_open, hold_limits, wrong-plan fields (expect, mistake indices into
   the plan, mistake_fix, variants that exist, modes own / eval, expect_budget's reason), labelled predicates (14.2)
   and, for schema 4, at least 2 wrong plans with expect and mistake and only known keys in variants. The schema-4
   keys are validated in every schema (R1 drafts live in schema 2/3 specs, inert); predicate validation runs for
   schema 4 only, so no legacy compile status can move (all 243 authored specs: `check_structure` and `lint.run`
   give `[]`, pinned).
4. **`house_sim/tasks/lint.py`**, every lint of 20.2: L-SCHEMA, L-PROTECT (7.5 rules 1-6), L-ENVELOPE, L-WATCH
   (`watch_problems`, wrapping WP1's census), L-LATCH, L-KIND, L-VACUOUS, L-WEAK, L-EXISTS, L-ANSWERS, L-SCENE,
   L-BRANCH, L-QUESTIONS, L-TIMING, L-SPEECH, L-ROLES (13.7 defaults), L-REQUEST, L-PRIV, L-EXPECT, L-NAMEDROOM,
   L-CLAUSES, L-BUDGET, L-COVER, L-ESCAPE. Each lint runs on every variant merged with the base; a problem found in
   several variants is reported once (`variants 0, 2: ...`). Output `[{"id", "severity", "message"}]`, deduplicated
   and sorted (severity, lint order, message): deterministic. A schema 2/3 spec gets only L-SCHEMA (legacy outcomes
   never move); `as_if=True` lints it as if it were schema 4 (the R0 audit: 0.4 s for all 243 specs).
5. **Dynamic facts** (`lint.dynamic_facts(spec, seeds)`): one setup build and one environment per seed (about 1.3 s
   per seed): variant, branch, bindings and their types (`type:<t>` / `room:<t>`), drawn instruction texts, which
   success predicates hold at t=0 (WP1's `goal_status` on the hardened labels; a legacy spec is built as if with
   `hardening={}`), which timing acts hold at t=0, look-alike group sizes of the success roles, instance counts of the
   envelope's `type:` selectors, people. The verifier's own facts (`budget`, `coverage`, `branch_cross`,
   `lookahead_triggered`) are read from the same `dynamic` dict. The facts do not depend on the spec's declared
   perception or camera (pinned in all four combinations).
6. **The protect classifier** (7.4): `house_sim/data/verify/protect_rules.json` (73 rules: regular expressions to
   candidate references of the 7.3 grammar, the restore revocations for lights and doors, a waive hint for social
   items); `lint.classify_protect_item(text)`, `lint.draft_protect_map(catalog_id=None, record=None)`;
   `tools/protect_map.py ID [--json]`, `--coverage`, and `draft(catalog_id)` (what WP5's `--draft-hardening` imports).
   **5,716 of the 6,355 analysis protect items (89.9 %) get a candidate** (the design's 30-rule prototype: 72.9 %);
   every candidate reference resolves (pinned). `tools/show_entries.py batch N` prints the draft protect map beside
   each entry's analysis record (5.4 item 5; `--no-protect` leaves it out).
7. **The price list** `house_sim/data/verify/escapes.json` (section 18): the 34 escape keys of WP5's
   `escapes.DEFAULT_PRICES` with points and review class, quota 3, max 15 points, 2 exemptions (pinned against the
   table of section 18 and against WP5's keys; `escapes.price_list()` reads it).
8. **Docs**: TASK_SPEC.md (section "Schema 4": every key, the failure-pattern and protect-reference grammars, roles,
   the lint table, timing defaults; schema in the key table and the lint list; the built task's hardening block),
   AUTHORING_GUIDE.md (what a new spec declares; the randomising setup ops of WP9), EVALUATION.md (the hardened
   pipeline, every new label family, package status), KERNEL_API.md ("v3.2 verification hardening": the interfaces of
   WP1, WP2, WP3, WP4, WP7, WP9 and WP8), PERCEPTION.md (WP7's act / grant / observation logs, the split signature,
   twin-invariant names, the gating rules), EVAL_HARNESS.md (finish, idle, agent gating; what WP5 / WP10 still owe),
   KNOWN_LIMITS.md (section 7a: section 26's limits and the open gaps; "Next" now says schema 4).

## Interfaces

```python
# house_sim/tasks/spec.py
SCHEMAS = (2, 3, 4); NEW_SPEC_SCHEMA = HARDENED_SCHEMA = 4; TIER1_SCHEMA = 3; HARDENING_KEYS; VARIANT_KEYS
DICT_MERGE_KEYS; REASON_MIN = 30; ROLES; ANSWER_KINDS; EXEMPT_CATEGORIES; MUSTPASS_CATEGORIES; LEGACY_IDS_FILE
check_structure(spec) -> [problem]        # now also the validators above
schema_problems(spec) -> [problem]        # "L-SCHEMA: ..." for a new catalog id without schema 4
legacy_ids(reload=False) -> frozenset | None; catalog_ids(reload=False) -> frozenset | None; derivations()
timing_problems(spec); hardening_problems(spec); answers_problems(answers); protect_map_form(items)
exemption_problems(key, items, categories); clauses_form(clauses); wrong_problems(spec); label_problems(spec)
predicate_problems(spec); variant_key_problems(spec); labelled_items(items) -> [(label | None, pred)]
milestone_names(spec)

# house_sim/tasks/lint.py
run(spec, analysis_record=None, dynamic=None, entry=None, record=None, as_if=False)
    -> [{"id": "L-...", "severity": "error" | "warning", "message": str}]
errors(problems); dynamic_facts(spec, seeds, gen=None, hardening=None, builds=None) -> facts (module docstring;
#   builds: {seed: (config, task, oracle)} the verifier already built, reused)
draft_protect_map(catalog_id=None, record=None) -> {"catalog_id", "items", "protect_map", "restore", "covered",
                                                    "unmapped", "note"}
classify_protect_item(text) -> {"by", "rules", "restore", "waive_hint"}; protect_rules(reload=False)
protect_ref_problem(ref, scope, lookahead_on=True, dynamic=None, index=None) -> str | None; split_ref(ref) -> (body, variants | None)
expect_pattern_problem(pattern, scope, item) -> str | None; normalise_item(text) (the verifier's rule)
roles(scope, dynamic=None) -> ({placeholder: role}, after_preposition); clauses_of(text); latched_fields()
watch_problems(census=None, domains=None); analysis_record(catalog_id); catalog_entry(catalog_id)
protective(rule); weak(rule); LINT_IDS; SEVERITIES

# tools/protect_map.py
draft(catalog_id) -> lint.draft_protect_map(...) | None; coverage() -> {...}; main(argv)
```

`dynamic` keys: `seeds`, `variants` {s: index}, `branches` {s: value}, `bindings` {s: {name: value}},
`binding_types` {s: {name: kind}}, `instructions` {s: [texts]}, `free` {s: {label: bool}}, `timing_initial`
{s: {label: bool}}, `scene` {s: {placeholder: size}}, `type_counts` {s: {type: n}}, `people` {s: [ids]}, plus the
verifier's `budget` (verify.budget_check), `coverage` (seeds.coverage), `branch_cross` ([[a, b, passes], ...]) and
`lookahead_triggered`. Every key optional; a lint without its facts is skipped.

## Deviations from the spec (and why)

* **L-SCHEMA lives in `spec.check_structure` too** (not only in lint.py): the verifier's S0 runs only for schema-4
  specs, so a new spec authored as schema 3 would never meet the lint; in check_structure the compiler marks it
  INVALID. "Catalog id" = an id of `catalog/entries.jsonl`.
* **L-ESCAPE: a review escape without its accepted record is a warning**, limits and ineligible signatures are
  errors. Read literally (every review escape needs an accepted record, error) a spec could never reach
  PENDING_REVIEW, because S0 stops at a lint error; G9 already decides the review state.
* **New spec key `scene_exception`** (a reason string): section 18 prices "L-SCENE exception" and 20.2 says "unless an
  accepted escape says why", but 20.1 names no key for it. WP5's escape detection reads a `scene_exception` fact;
  the key is where the author states it (request below).
* **L-ANSWERS compares house-structural bindings by type**: a binding of a `room`, `find`, `spawn`, `person`, `pet`
  or `robot` op whose id differs between houses only by numbering (`bookshelf_1` vs `bookshelf_2`) is not a hidden
  answer; drawn bindings (choose, randint, uniform, sample, shuffle, time, arrange, helpers) and spawn-count members
  are compared by value.
* **L-VACUOUS**: a seed on which every success predicate already holds is its own error ("every success predicate
  already holds at t=0"); 8.6's "the residual consists only of weak predicates" read with an empty residual would
  contradict the free_ok warning for a single positive predicate.
* **L-NAMEDROOM takes no reason** (20.2 says "needs a reason" but names no key): the room must be a work room
  (`envelope.work_rooms`) or named in the instruction.
* **L-KIND**: no domain declares a counter's condition yet; every `stats.*` path in a success predicate is an error
  unless `plugins.STAT_CONDITIONS` (read if a later package defines it) declares it.
* **L-LATCH's table format**: a domain page's heading naming "latched fields" followed by rows whose first cell is
  the field in backticks; plus the review's `sorted_ok`, `stream_ok`, `in_stream`.
* **L-CLAUSES' clause list** is `lint.clauses_of(entry text)` (parenthesised examples and a short heading dropped,
  split at punctuation, commas and "then"); `clauses` must cover exactly those normalised texts.
* **L-PRIV's privileged wait** is a `wait_until` with `poll_s` (the schema-2 chunked form); in schema 4 `wait_until`
  compiles to the agent-legal wait (runner), so the plain form is legal.
* **L-PROTECT rule 6** has no analysis branch data: an item covered in some variants must be covered in every
  variant with the same `branch` value (error), and coverage of a strict subset of branches is a warning.

## Known gaps

* WP3's vacuous-trigger gap (a timing trigger after the run's end) needs the episode's end time against the trigger
  time: left to WP5's runs (not a static or build-time lint).
* L-ROLES finds person placeholders from `person` setup ops and, with dynamic facts, from bindings that are people ids.
* L-KIND has no counter-condition registry to read yet; L-LATCH has no domain "Latched fields" table to read yet.
* Interplay with WP5, resolved during this run: S0 now stops a spec on any L-* error (22.6), which WP5's
  `tests/test_verify_hardening.py` already expects (its FailureCaseTests assert the L-EXPECT stop when lint.py is
  present); WP5's verifier calls `lint.run(spec, analysis, record=...)` at S0 and again at S8 with
  `lint.dynamic_facts` plus its budget and coverage, and reads `scene_exception`.

## Requests

* **WP5** (optional, cost 22.5): pass the S1 builds to `lint.dynamic_facts(spec, V, gen=gen, builds={seed: (config,
  task, oracle)})` instead of building every seed again (about 1.3 CPU-s per seed).
* **WP1** (optional): a registry for counters that count a declared condition (`plugins.STAT_CONDITIONS`), so L-KIND
  can accept state-backed counters (8.3).
* **WP-D packages**: put a "Latched fields" table (field name in backticks in the first column) in
  `docs/domains/<domain>.md` for every latched goal field (L-LATCH reads it).
* **Main session**: `setup.py` package_data for `data/verify/*.json` (escapes.json and protect_rules.json join WP1's).

## Phase-1 carry-over

No open phase-1 row lies in WP8's files: the 10 rows of `closeout_breakages.json` are domain rows (VER2: fixed),
`closeout_REV.json`'s findings (confirmed in `closeout_VER2.json`) belong to the gate tool, py39_lint, cleaning,
safety, people, mechanisms, navigation_robot and the main session's rules (and finding 7 was WP7's), and
`closeout_INT3.json`'s requests go to the main session, the domains and WP9.

## Verification of this run

* `tests/test_lint.py`: 37 tests, OK normal and HOUSE_SIM_STRICT=1 (about 10 s).
* `python tools/orchestration/green_tests.py 3` on the shared tree (other P2-S2 packages' edits present): 5,337 tests
  per mode, 0 failures, every file normal and strict (test_verify_hardening.py 67 tests ok in both).
* All 243 authored specs in their own mode (combined file, `python -m house_sim --check <all> --workers 3`): 243
  passed, 0 failed.
* `tools/py39_lint.py` on the five Python files: 0 violations.
* All 243 authored specs: `check_structure` and `lint.run` give no problem (no legacy compile status or outcome
  changes; WP8 changes no simulation or evaluation code path of a schema 2/3 spec except that `runner.build` now
  copies a `timing` key, which no legacy spec has).

## How to test

```
python -m unittest tests.test_lint                       # also HOUSE_SIM_STRICT=1
python tools/protect_map.py T-d990c58b123a5e-01          # --json, --coverage
python tools/show_entries.py ids T-56d1d7f6a17796-01
python -c "from house_sim.tasks import lint; print(lint.run({...spec...}, lint.analysis_record('T-...')))"
python tools/py39_lint.py house_sim/tasks/spec.py house_sim/tasks/lint.py tools/protect_map.py tools/show_entries.py tests/test_lint.py
```

## Run-A fixes (WP8-fix, 2026-10-29; details in handoff_WP8-fix.md)

The review of run A found four defects in WP8's files (A-spec-12, A-spec-14, A-bugs-7, A-gaming-13), all fixed with
regression tests (`tests/test_lint.py` `RunAFindingTests`, 43 tests in the file now):

* `spec.check_structure` never raises on a malformed spec (`spec.shape_problems` first; a guard behind it), so the
  compiler marks it INVALID instead of aborting; `merge` raises SpecError for a non-list `"+key"`.
* `inv:` patterns and references name a whole invariant label (`lint.invariant_problem`, `lint.invariant_labels`):
  `inv:`, `inv:s`, `inv:people` are L-EXPECT / L-PROTECT errors.
* L-ENVELOPE resolves every selector and scope of the envelope and protections (`lint.selector_problem`: forms,
  bound placeholders, registered types, literal ids with the new `ids` dynamic fact); L-PROTECT resolves narrowed
  `house:` references and every placeholder of a reference.
* The docs name WP5's and WP11's interfaces (KERNEL_API.md "Verification core", "Core skills"; EVALUATION.md
  "Running it"); only WP6 and WP10 are listed as not landed.
