# Handoff WP5 VERIFY CORE (VERIFY_HARDENING section 25; sections 5, 8.1, 8.6, 8.7, 9.3-9.5, 12, 14-19, 21, 22)

Files (only WP5's): `house_sim/tasks/verify.py`, `house_sim/tasks/runner.py` (changed); `house_sim/tasks/seeds.py`,
`strength.py`, `escapes.py`, `private.py`, `checklog.py`, `runcache.py`, `house_sim/data/verify/budget.json`,
`expected_families.json`, `tools/seed_features.py`, `catalog/analysis/seed_features.json`,
`tests/test_verify_hardening.py` (new, 67 tests); `house_sim/cli.py`, `tools/verify.py` (changed). This handoff and
`WP5.json`. Nothing else was edited.

## What landed

1. **The schema-4 build** (`runner.build`). A `"schema": 4` spec (or any spec built with `hardening={...}`: the
   as-if builds of R0 and `--draft-hardening`) gets the hardening block WP1/WP2/WP3/WP4 read: `settle_s` (60),
   `lookahead_s` (0 unless triggered), `process`, `absent_ok`, `envelope`, `protections`, `track_open` (true),
   `hold_limits`, `labels`, `answers` (bound: `perceive.resolve_answers`, `"_branch"` = the variant's `branch`, else
   its index; also `task["answers"]`, the runner dict `speech.normalize_answers` reads). Bindings are substituted;
   an envelope key bound to a list becomes its JSON text (`ledger.ids_of`). `ledger.validate_envelope` /
   `validate_protections` problems are a `SpecError`. Labelled predicates `{"label", "pred"}` are unwrapped in
   success, failure and every phase; labels go to `task["labels"]` / `phase["labels"]` aligned by index (the strict
   blocks unlabelled); a repeated, empty or colon-bearing label is a `SpecError`. `timing` passes through (bindings
   substituted). `build(..., audit=True)` uses the setup's audit fast path. A legacy build is byte-identical.
2. **The trace** (`Runner.trace`, 9.5): one record per env.step a plan issued -- `{"i", "top", "skill", "action",
   "target", "destination", "args"` (true ids, never handles), `"prov"` (per argument `binding:<n>` / `save:<n>` /
   `literal` / `skill:<name>`; a skill's command inherits the provenance of the skill argument it equals), `"ok",
   "category", "t0", "t1", "steps", "room"`, and for waits `"wait": {"requested_s", "waited_s", "conditioned"}}`;
   `Runner.tops` per top-level step (`k, kind, first, last, t0, t1, error`), `Runner.saves` (`name, top, from, value,
   after, frozen`), `Runner.waits` (every observation-conditioned wait: `ordinal, top, kind, max_s, waited_s, fixed`),
   `Runner.refusal_tops` (aligned with `refusals`). `Runner(..., frozen={name: value})` replaces those save results
   (B5a / B5b: the answer at its source), `fixed_waits={ordinal: s}` replaces conditioned waits by plain waits (B7
   timer, B13), `snapshot(k, runner)` runs before every top-level step (forking, 10.5). `capture_run(env, plan,
   bindings, ...)` -> `(runner, reached, exc)` (14.5). A plan step naming an argument the skill does not take is a
   `SpecError` (`skill put: ...`), not a harness error.
3. **The hardened pipeline** (`verify.verify_hardened`, through `verify_spec(spec, level)` for schema 4; the legacy
   path is unchanged). Stages S0 (spec.check_structure and WP8's static `lint.run`; an error stops the spec), S1 (coverage seeds),
   S2 own and S3 eval (eval-geom, and eval-room until D3) core trials, S5 / S6 / S7p through WP6 when installed, S6
   variation (9.3 over V), S7 held-out (full), S8 escapes / strength / gates / verdict / check log. Per seed and mode
   (`mode_trials`): initial fails; **idle** (`idle_rollout`: budget + settle + look-ahead, no early exit, B1 success
   at every `k x max(60 s, budget/48)`, a passing sample confirmed by `finish()` on a saved-and-loaded copy, the
   counterfactual projections every `counterfactual.interval_for(budget)`, `env.idle` set); **reference** strict with
   its trace, `HouseEnv.finish`, containment (`envelope_violation:<label>` for every `house.*` / `later:` label),
   perceivability (`perceive.report_gaps`) and gating (`perceive.act_gaps`, the branch diff set computed from every
   seed's trace for branch answers) in the evaluation mode, its own waste / hold-open labels re-judged with its own
   use; then, with the allowances known (`reference_resources`, `reference_open_s`: the references' largest), every
   applicable **wrong plan** (`variants`, `modes`) run (`wrong_run`) and judged (`judge_wrong`, 14.3: ran fully, a
   refusal only in `mistake` or with a `strict:` expect, fails after settle / look-ahead with the agent rule's
   `perceive:*` labels in eval, a specific expect matches -- the 14.2 grammar in `canonical_labels` /
   `pattern_matches` --, not on the budget alone), and in eval-geom its **attribution run w'** (`attributed_plan`:
   `mistake_fix`, else the LCS alignment with the reference over (skill or action, role)); eval-geom also runs the
   **survey** and **search** baselines (`skills.survey`) and the **minimal-reference trim** (17), from which
   `budget_check` applies the formula per variant (`formula_budget`; authored budget <= formula, the reference <= 80 %
   of it on every seed of V u H) and sets the horizon tier. Also: `timing_vacuity` (WP3's vacuous-trigger gap: an
   L-TIMING error when a reference ended before the seed's drawn event), `protect_record` (L-PROTECT rule 1 and
   liveness from verified wrong plans), `escape_facts`, `envelope_classes` (never-touch / restore-class change
   entries, consume / deliver selectors that name a valuable), WP8's dynamic lints at S8 (`lint.run(spec,
   analysis, dynamic=lint.dynamic_facts(spec, V) + budget_check + coverage + the look-ahead trigger,
   record=<escape_facts>)`), the held-out stage (`seeds.heldout_public` or `private.heldout`, feature seeds, infeasible seeds
   replaced, skill debt classified by 16.3, the tolerances of 16.4).
4. **Seeds** (`tasks/seeds.py`): `verification_seeds` / `coverage` (9.4, L-COVER), `heldout_public` /
   `heldout_stream` / `select_heldout` (16.1), `audit_seeds`, `feature_seeds` / `wanted_features` /
   `load_features`, `answer_values`. `tools/seed_features.py` wrote `catalog/analysis/seed_features.json` (seeds
   200000-200999, generations 1 and 2; 2,000 houses, 1,120 s on 2 workers; every feature present in both
   generations except small kitchens, which the generator never makes at 9 m^2 or less).
5. **Private stages** (`tasks/private.py`): `salt` (HMAC of the existing split key), `available`, `heldout`
   (12.1, hidden range, test candidates skipped), `pool`, `sample_pool`, `rng`, `write_details` (never inside the
   repository, never into a missing `%USERPROFILE%\.house_ai`), `details_digest`, `public_summary`, `NOT_RUN`.
   Nothing creates the private folder or a key; tests use throwaway keys and a temporary home.
6. **Check log** (`tasks/checklog.py`): `append` (one line per schema-4 check: catalog_id, spec_hash, level,
   verdict, gates, date, fingerprint, session_id from `HOUSE_AI_SESSION_ID`, cpu_s, plus cpu_by_stage, failing,
   heldout_failed) to `catalog/review/check_log/<domain>.jsonl` (`HOUSE_AI_CHECK_LOG_DIR` redirects), `entries`,
   `sessions`, `heldout_rolls`, `stop_loss` (22.8).
7. **Escapes** (`tasks/escapes.py`): the price list of 18 (WP8's `escapes.json` overrides it), `detect`, `price`
   (quota / review, G9: unreviewed, > 15 points, > 2 accepted exemptions), `accepted_records`
   (`catalog/review/exemptions.jsonl`, last decision wins), `eligible` (owner; an agent session not in the check log
   and not the proposer; `other` owner-only), `exemption_count`.
8. **Strength** (`tasks/strength.py`): `score` (the nine components of 19.1, P4: a battery that did not run is
   0), `gates` (G1-G9), `verdict` (HARDENED / PENDING_REVIEW / PENDING_GATE / PROVISIONAL / FAIL),
   `expected_families` (`expected_families.json` rule table over the analysis record and entry text), `kind_class`,
   `level_passes`.
9. **Budget data** (`house_sim/data/verify/budget.json`): formula constants, trims, idle sampling, the look-ahead
   trigger and appliances, the four horizon tiers with their reduced run plans and caps, the planning figures.
10. **Run cache** (`tasks/runcache.py`): mode jobs keyed by the spec as runs read it (`sim_spec`: expect,
    protect_map, roles and the escape keys left out), seeds, mode, generation, allowances, flags, look-ahead and the
    code fingerprint; a hit is re-judged (`verify.rejudge`), never re-run; `reports/cache/runs/`, 2 GB LRU.
11. **CLI** (`python -m house_sim`): `--check FILE [--ids] [--level] [--battery] [--mustpass] [--modes own,eval]
    [--seeds] [--heldout N] [--explain DIR] [--json OUT] [--workers N] [--no-cache]` (schema-4 specs print the block of
    21.2 and exit by the level's gates; legacy specs print as before), `--draft-hardening FILE --ids ID [--out]`,
    `--verify [--level]` (schema-4 default full; `HOUSE_SIM_VERIFY_LEVEL`). `tools/verify.py` passes options through.

## Interfaces (signatures)

```python
# house_sim/tasks/verify.py
verify_spec(spec, level=None, seeds=None, perception=None, camera=None, gen=None, **options)
    # schema 4 -> verify_hardened (options: modes, heldout, battery, mustpass, workers, key, check_log, log_root,
    # progress, cache, explain); legacy -> the legacy record (a tuple as 2nd positional = seeds, as before)
verify_hardened(spec, level="standard", seeds=None, modes=None, heldout=None, battery="all", mustpass="all",
                workers=None, gen=None, key=None, check_log=True, log_root=None, progress=None, cache=False,
                explain=None) -> evidence record v2 ("format": "house_ai.verification/2", "passed" = level gates)
run_trial(spec, seed, perception=None, camera=None, gen=None, capture=None, mode=None)   # mode: own|eval|eval-geom|eval-room
verify_many(specs, seeds=SEEDS, workers=None, progress=None, perception=None, camera=None, gen=None, level=None,
            **options)                     # options["hard_seeds"]: schema-4 debug seeds
mode_trials(spec, seeds, mode, gen=None, extras=None, attribution=False, baselines=False, trim=False, lookahead=None,
            capture=None) -> {"mode", "trials", "references": {seed: {"variant", "branch", "bindings", "answers",
            "trace", "tops", "saves", "waits", "reference", "idle" (Projections dict with capture), "idle_digest",
            "envelope_classes"}}, "extras", "trims"}
idle_rollout(env, task, horizon_s=None) -> (Projections, {"fails", "samples", "projections", "passes_at",
                                                          "unconfirmed", "horizon_s", "digest"})
reference_run(config, task, oracle, seed, mode, idle, branch_diff=None, plan=None, snapshot=None) -> (rec, env, runner)
wrong_run(config, task, oracle, seed, mode, item, idle, branch_diff=None, plan=None) -> (rec, env)
judge_wrong(rec, item, said=()) -> rec;  rejudge(spec, mode_out) -> mode_out
canonical_labels(failures, unmet=(), said=()); pattern_matches(pattern, canon); expect_problems(item);
matched_patterns(item, failures, unmet, said=())
default_mistake_fix(plan, mistake, reference) -> {block start: [steps]}; attributed_plan(item, reference) -> plan
formula_budget(references, explores) -> {"steps", "time_s"}; budget_check(spec, mode_out, heldout_trials=())
variation(spec, mode_out); timing_vacuity(mode_outs); protect_record(spec, analysis, mode_outs)
escape_facts(spec, analysis, mode_outs, expected, lookahead_triggered); envelope_classes(spec, oracle, env)
branch_diff_sets(traces); survey_baseline(...); search_baseline(...); minimal_reference(spec, built, trials, mode)
draft_hardening(spec, seeds=None, gen=None, mode="eval-geom") -> suggestions (nothing written)
eval_modes(); mode_settings(mode); mode_of(perception, camera); is_hardened(spec)
LEVELS, DEFAULT_LEVEL, OWN, EVAL_GEOM, EVAL_ROOM, EVIDENCE_FORMAT, LEVEL_ENV
# house_sim/tasks/runner.py
build(spec, seed=0, registry=None, camera=None, perception=None, gen=None, hardening=None, audit=False)
Runner(env, bindings, strict=True, frozen=None, fixed_waits=None, record=True, snapshot=None)
    .trace .tops .saves .waits .refusal_tops .top_step(k, raw) .run_top(plan)
run_plan(env, plan, bindings, strict=True, **runner_kwargs); capture_run(env, plan, bindings, strict=False,
                                                                         runner=None, **kwargs)
hardened_schema(spec); unwrap_labelled(items, where); bind_answers(raw, bindings, chosen=None, variant=None);
branch_value(chosen, variant); HARDENED_SCHEMA, HARDENING_SPEC_KEYS
# house_sim/tasks/seeds.py
verification_seeds(spec, values=None); coverage(spec, values=None); heldout_public(spec, spec_hash=None, skip=());
heldout_stream(spec, spec_hash=None); select_heldout(stream, catalog_id, n_variants, n=10, cap=16, skip=());
heldout_seed(cid, spec_hash, i); audit_seeds(n=200); feature_seeds(spec, features, exclude=(), index=None, limit=2);
wanted_features(spec, floors=(), actions=()); load_features(path=None); answer_values(spec, seed)
# house_sim/tasks/private.py
salt(key=None); available(key=None); heldout(spec, key=None, spec_hash=None, n=10, cap=16, skip=());
heldout_stream(spec, key=None, spec_hash=None); pool(key=None, n=2000); sample_pool(spec, key=None, n=200);
rng(salt, catalog_id, seed, family); write_details(catalog_id, rows, root=None); details_digest(rows);
public_summary(results); store_path(catalog_id, root=None); NOT_RUN
# house_sim/tasks/strength.py
score(record, spec=None, escapes=None); gates(record); verdict(record, scored=None, gate_map=None);
expected_families(analysis, entry_text=None, entry_kind=None); kind_class(expected); level_passes(record, level)
# house_sim/tasks/escapes.py
price(spec, record=None, exemptions=None, sessions=None, exemptions_path=None); detect(spec, record=None);
price_list(reload=False); accepted_records(catalog_id, spec_hash, path=None); eligible(row, sessions, category=None);
exemption_count(priced)
# house_sim/tasks/checklog.py
append(record, root=None, domain=None, level=None, cpu_s=None) -> (path, entry); entries(catalog_id=None, root=None,
domain=None); sessions(catalog_id, root=None); heldout_rolls(catalog_id, root=None); stop_loss(catalog_id, root=None,
allowance=0); session_id(); domain_of(catalog_id)
# house_sim/tasks/runcache.py
key(kind, payload); get(key, root=None); put(key, value, root=None, max_bytes=MAX_BYTES); prune(...);
sim_spec(spec); enabled()
```

**What WP6 receives** (S5 calls, when `house_sim/tasks/battery` exists): `battery.run(spec, builds, traces, seeds,
mode, level)` with `builds = {"mode": "eval-geom", "references": <mode_trials references>, "extras": {allowances}}`
and `traces = <mode_trials references>` ({seed: {"trace", "tops", "saves", "waits", "bindings", "answers", "variant",
"branch", "reference", "idle_digest", ...}}); expected back `{"ran": True, "runs": [{"id", "family", "seed", "mode",
"outcome", "labels", "hint", ...}], "summary": {...}}` (a list of runs is accepted). `battery.mustpass(...)` the same
with `"summary": {"applicable", "realised", "passed", "failed_unexempt"}`; `battery.audit(spec, n=200)` (full level);
`battery.private(spec, references, seeds, mode, level, salt=...)` -> `{"results": [{"family", "outcome",
"label_class"}], "runs": [...]}`. The record's `coverage.applicable_families` is the families with a non-`skipped:na`
run. To fork, WP6 can build the env with `verify._hard_env`, run the reference with `Runner(snapshot=...)` or re-use
`reference_run(..., snapshot=)`.

## Deviations from the spec (and why)

* **One idle rollout per mode** (22.5 mitigation (2) not taken): correctness first; the shared rollout needs the
  first-seed projection audit across processes. Cost only (about 1 s per seed and mode at H0).
* **The idle loop is the verifier's own** (`idle_rollout`): projections at `interval_for(budget)` like
  `counterfactual.rollout`, but its waits also stop at the B1 sample points (`max(60, budget/48)`), so the step
  boundaries differ from a bare `rollout`. WP10's harness should take the counterfactual from `idle_rollout` (or the
  verifier's cached projections) so an agent is judged against the same idle house (request).
* **S7 runs while S5 is unavailable**: 22.6 runs S7 only when every gate but G6 and G9 holds; until WP6 lands G3 and
  G8 can never hold, so S7 runs when the gates that *ran* hold (core, wrong plans, harness, lints). G3 / G8 are
  `False` at standard / full whenever the battery did not run, so no spec can be HARDENED or PENDING_* yet (P4).
* **Allowances per mode job**: `reference_resources` / `reference_open_s` are the largest over that mode's seeds (the
  held-out seeds use the eval-geom ones); the reference's own `house.waste` / `house.held_open` labels are re-judged
  with its own use (an exposure-based hold-open, a gate held while a toddler roams, still fails it).
* **The check log is written only by schema-4 verification**: the legacy `--check` / `--verify` behave exactly as
  before (and the 243 legacy specs never write WP10's `catalog/review/check_log/`).
* **The run cache re-judges, it does not re-evaluate**: edits to expect, the protect map, roles and the escape keys
  reuse the stored runs (most authoring edits); an edit of the envelope, protections or predicates re-runs (22.8
  re-evaluates those too; that needs every end state stored, and is a later optimisation). w' runs for every wrong
  plan that ran and failed, whatever its expect, so a re-judge never needs a run. `--verify` never uses the cache.
* **Protect liveness** counts only source (a), verified wrong plans; battery runs and probes (b, c) are WP6's.
  L-PROTECT itself is WP8's lint (G4 reads its errors); the verifier's rule-1 check stands in without that module.
* **Lints in two passes**: WP8's `lint.run` statically at S0 (an error stops the spec, 22.6) and again at S8 with
  the dynamic facts (`dynamic_lints`); a dynamic error fails G5 but does not stop the stages that already ran. The
  verifier's own L-COVER and L-PROTECT rule-1 checks stand in only when WP8's module is absent; its VARIATION (9.3
  over V), L-TIMING vacuity and L-BUDGET "step not needed" findings are added beside WP8's.
* **consume / deliver of a non-target is priced only when it names a valuable** (`envelope_classes`
  `valuable_selectors`, `ledger.is_valuable` at the baseline); a `type:` / `category:` consumable is free (6.6's own
  example consumes `type:paper_towel`; WP8's request). Without the verifier's facts a bound selector is priced.
* **Variation 9.3 over V** is a lint error (`VARIATION`), L-COVER likewise; the 200-build shares are WP6's audits.
* **Skill argument errors** are `SpecError`s at the runner boundary (a wrong keyword raised `TypeError`, a harness
  error, before); no legacy outcome changes (none of the 243 specs has one).
* **Expected families** are a heuristic regular-expression table (`expected_families.json`); 19.3 and 22.6
  recalibrate them from R0.
* **verify_spec's signature** is `(spec, level=None, seeds=None, ...)`; a tuple or list as the second positional
  argument is still the seeds (the old positional form).
* **Feature seeds** come from the default-house index; a spec with house requirements builds another house on that
  seed, so the feature is best-effort there (the trial records the house it got).

## Known gaps (and who owns them)

* WP6: the batteries, must-pass, audits and private families (S5, S6 audits, S7 rate families, S7p): recorded "not
  run"; `--explain` has nothing to write until then; forking snapshots are taken only when a consumer passes
  `snapshot=`.
* WP8 landed during this run (spec.py schema 4, lint.py, escapes.json): the tests' `schema4_keys()` shim is a
  no-op now; `lint.dynamic_facts` is called without the verifier's builds (they live in the mode jobs'
  processes), so it builds each seed of V once more (about 1-3 CPU-s per seed).
* Multi-phase schema-4 idle runs declare each phase done at the end of its cumulative budget (like the legacy idle,
  without the phases' own start rules).
* `catalog/analysis/seed_features.json` is keyed to `disk_cache.fingerprint()`, which covers the kernel and domains
  too: `tools/seed_features.py --check` reports it stale after any such edit; regenerate when the tree settles.
* cpu_by_stage counts the main process plus each job's own `process_time` (worker CPU is reported by the jobs).

## Requests

* WP8 (done by WP8's landing; one option left): `lint.dynamic_facts(..., builds=)` could take the verifier's
  builds if the mode jobs ever return them (they are built in worker processes today).
* WP6: the call shapes above; read the tier's run plan from `budget.json` (`verify._tier_of(record["budget"]
  ["formula_time_s"])`).
* WP10: the gate verifies schema-4 specs with `verify_spec(spec, "full", cache=False, key=<split key>)` and reads
  `record["verdict"]`; `audit.report` should call a schema-4 record DONE only when `verdict == "HARDENED"` (`passed`
  is the level's exit criterion: PENDING_GATE passes `--check` on an authoring machine); the harness's cached idle
  counterfactual should come from `verify.idle_rollout`; `tier1_gate.py` reads legacy trial fields from `run_trial`
  (a schema-4 trial nests the reference record).
* Main session: add `house_sim/data/verify/budget.json` and `expected_families.json` (with WP1's files) to
  setup.py's package_data; regenerate `catalog/analysis/seed_features.json` (`python tools/seed_features.py --workers
  3`) once the generator fingerprint is final.

## Phase-1 carry-over

None of the open rows lies in WP5's files: the 10 rows of `closeout_breakages.json` are domain-side, the findings
of `closeout_REV.json` confirmed in `closeout_VER2.json` are the gate tool's, py39_lint's and the domains', and
`closeout_INT3.json`'s requests go to the main session, the domains and WP9 (searched for verify.py, runner.py,
cli.py, tools/verify.py and the verifier's behaviour).

## Cost (section 22)

The small schema-4 task of the tests: quick level, one seed, the three modes: S1 0.8, S2 1.3, S3 5.5 CPU-s (eval-geom
with attribution runs, the survey and the trims, plus eval-room); standard level on two seeds: S0 0.5 (WP8's static
lints), S1 0.3, S2 3.0, S3 10.0 CPU-s (19 s wall, serial); the full level with 2 held-out seeds adds S7 8.7 CPU-s.
About 7-8 CPU-s per seed for the core trials against the plan's 121 CPU-s for the quick level of a 6-seed H0 spec
(22.4); the batteries' share (S5-S7p) is WP6's.

## Legacy invariance

The legacy path of `verify.py` is the old code (run_trial, wrong_plan_record, idle, verify_spec's legacy record), and
runner.py's `Runner` executes the same steps in the same order. Differential (scratch `p2_WP5/legacy_diff.py`): 30
authored specs sampled across the files, `run_trial(spec, 0)` with this tree and with a copy whose runner.py and
verify.py are HEAD's -- 30 / 30 trial records identical (initial and reference state digests, wrong-plan labels and
records included). All 243 authored specs in their own mode: see WP5.json.

## How to test

```
python -m unittest tests.test_verify_hardening                 # also HOUSE_SIM_STRICT=1
python -m unittest tests.test_verify_records tests.test_phases tests.test_dialogue tests.test_timeline
python tools/py39_lint.py house_sim/tasks/verify.py house_sim/tasks/runner.py house_sim/tasks/seeds.py \
       house_sim/tasks/strength.py house_sim/tasks/escapes.py house_sim/tasks/private.py house_sim/tasks/checklog.py \
       house_sim/tasks/runcache.py house_sim/cli.py tools/seed_features.py tools/verify.py tests/test_verify_hardening.py
python tools/orchestration/green_tests.py 3
python -m house_sim --check <all authored specs combined> --workers 3
```

## Fix round (WP5-fix, run A review findings)

`handoff_WP5-fix.md` has the details. These interfaces changed:
* `escapes.price(..., reviews=None, reviews_path=None, key=None)` and `escapes.eligible(row, sessions,
  category=None, reviews=None, key=None)`. Owner rows need `signature = escapes.sign(row, key)`. Agent rows need a
  session logged in `review_sessions.jsonl`. The reviewer's category must equal the proposal's.
* `strength.verdict` judges a level on the gates it runs (`required_gates`). At standard, G6 is not required and
  the score is projected (`projects_heldout`).
* `runcache.environment()` is in the mode-job key.
* `reference_run` records `spec_error`, `error_category`, `operates_appliances` and `phases`.
* `wrong_run` records `refused_categories`, and `judge_wrong` adds `UnrealisedMistake`.
* `_skill_debt(..., category=None)`.
* `dynamic_facts` returns `(None, [L-DYNAMIC error])` on failure.
* `budget_check` adds per-phase rows `"<v> phase <id>"` and takes explore steps and seconds separately.
* `runner.episode_budget(chosen, phases=None)`.
* `record["lookahead"]`, and the look-ahead now triggers from the references' runs.
