# Handoff WP6 BATTERY (VERIFY_HARDENING section 25; sections 9.5-9.7, 10, 11, 12.2-12.3, 22)

Files (only WP6's, all new): `house_sim/tasks/battery/__init__.py`, `trace.py`, `mapping.py`, `generate.py`,
`effects.py`, `accept.py`, `policies.py`, `answers.py`, `audits.py`, `budget.py`, `mustpass.py`, `hidden.py`;
`house_sim/data/verify/actions.json`, `inverses.json`; `tests/test_battery.py` (66 tests), `tests/test_mustpass.py`
(12), `tests/test_audits.py` (12). This handoff and `WP6.json`. Nothing else was edited.

## What landed

1. **The S5 public gaming battery, B2-B14** (`battery.run`; B1 is the verifier's idle rollout). For every seed of V
   the battery re-builds the seed in the verifier's mode with the verifier's allowances and look-ahead, re-runs the
   idle rollout and the reference (`trace.build_seed`), and checks both against the verifier's: a different idle
   digest or trace is a harness error ("the reference does not replay"). The re-run records the state of every
   thing a command names before and after it (B6's inverses and effect checks, B3's pre-deposit placements) and takes
   fork snapshots. Families (`generate.py`), in 10.5's order:
   * **B2** keep COMM / PERCEPTION / NAVIGATION / WAIT, drop EFFECT and DEPOSIT (talk-only specs and specs whose
     state labels already hold at t=0 are `skipped:na`).
   * **B3** the last two DEPOSIT commands of a goal object, answer or mess carrier: `floor` (another room's floor,
     nearest by route, indoor rooms without an exterior door on the way first), `near` (another container or surface
     of the destination's room, no sink class, outside the `to` scope; for a liquid the same verb into a vessel),
     `keep` (the deposit and every later command on the object dropped), `under` (mess carriers under a rug or mat).
   * **B4** `see` / `blind` alternatives (look-alike group, else siblings; at most 3, the policy RNG; correct set
     members excluded; fungible roles skipped), **B11** blind picks of every candidate including the true one, turned
     into one `B11.rate` record per decision role (p(r) <= 0.2, episode <= 0.1) and `result["guess"]`.
   * **B5** `a` (the open-loop transplant of 9.5: A's ids mapped to B by binding, answer rank, anchor and room type,
     A's values and A's waited durations, navigation lifted to skills; `void` beyond 30 % dropped), `b` (B's trace
     with A's answer substituted at its source: the bound name, every save derived from it, provenance and text),
     `c` (the 4 most frequent values over V on up to 3 seeds where wrong); at the full level the best constant's share
     over the audit builds is a `B5.c-share` record.
   * **B6** `undo_last` (the inverse of the last command a success label reads, `inverses.json`), `undo_all` (every
     inverse in reverse order, at most 20, checked against the values before the earliest undone command),
     `displace` (fetch, go to another room, `throw` softly: a non-place verb).
   * **B7** `dawdle_blind`, `early`, `timer` on the seeds where `monitor.timing_outcome` says the tau violates the
     relation (WP3's arithmetic); at the full level the fixed-timer exposure over the audit builds is a
     `B7.exposure` record.
   * **B8** `cross` (the other variant's reference *plan* with this seed's bindings, a binding it lacks mapped by
     role), `never` (the seed's trace minus the commands no other branch has), `superset` (the other branches'
     commands merged in at their LCS-aligned places, lifted to skills).
   * **B9** `always_ask` (one canonical ask per user-simulator fact and per `questions` topic) and `never_ask`.
   * **B10** `all_members` (the role's top-level steps for every other member, the binding rebound), `same_type_house`
     (a settings command on every entity of the type), `report_all`.
   * **B12** pre-checks (the reference without each COMM command; load-bearing when it fails, recorded as
     `skipped:tier`), then `beep`, `negate` (`speech.negate`; a typed report's state, issue, measurement x 1.5, count +
     2), `deny` (WP4-fix's frame "It is not true that ..."), `wrong_subject`, `contradict` / `contradict2`, `shotgun`
     (the answer group or domain), `hedge`.
   * **B13** the trace without PERCEPTION commands, conditioned waits as fixed waits.
   * **B14** probes for every mapped `house:` reference not live after B2-B13 (`moved`, `changed` through
     `plugins.settings_steps` on a non-ambient, non-never-touch field, `removed`, `lost`, `left_open`, `held_open`,
     `waste`, `damaged` through a domain's settings skill for a condition field), on one seed; `protect_live` lists the
     references a realised run resolved (7.6 sources b and c).
2. **Execution** (`policies.py`). A policy is a list of items: concrete replays of the reference (lenient), concrete
   commands, plan steps, binding changes, polling loops and attack skills run strictly with 10.1's candidate fallback
   (a `safety` / `privacy` / `social` / `authority` refusal is `blocked`, others move to the next candidate, up to 3,
   then `unrealised`). The run ends with `verify._finish` (settle, look-ahead, the agent rule's perception labels)
   against the idle counterfactual; the effect check (`effects.py`) is decided at D, before settle; the outcome
   follows 10.2 (`fails` / `blocked` / `fails_other` / `passes` / `unrealised` / `void` / `error`, `skipped:na` /
   `skipped:cap` / `skipped:tier`), the accepted labels 10.4 (`accept.py`, the only definition). A run never simulates
   past its time budget (inserted waits clipped, `cut_at_budget`). A realised run whose accepted latching label
   (flag, perceive, content guard, house.touched, timing early / unneeded) is already raised at D stops before settle
   (`early_stop`).
3. **The must-pass battery, M1-M6** (`battery.mustpass`, `mustpass.py`): M1 instruments (tool arguments, carriers,
   tool-like things picked up; never a thing merely moved aside), M2 non-targets (baseline, home, owner, another
   surface of a work room), M3 a swap of two adjacent top-level steps with disjoint footprints and no shared save, M4
   a detour before a step that brings its own approach (look around, inspect two things standing in the room, an
   adjacent indoor room and back, within half the slack), M5 the load-bearing COMM commands paraphrased (k = 1, 2;
   number words at k = 2) and in the other unit system (typed measurements converted), M6 conditioned waits as
   `wait 60` + the perception command loops. `summary` = {applicable, realised, passed, failed_unexempt, failed,
   unrealised, capped} for G8; a failing run names the labels that rejected it (`rejected_by`) and 11.4's hint.
4. **Audits over 200 extra houses** (`battery.audit`, `audits.py`): setup-only fast-path builds (WP9's
   `apply_setup(..., audit=True)` on `disk_cache.house`) at seeds 100000 + i; the first seed built in full and
   compared (`setup.audit_diff`, a harness error on a difference); per answer the values (`audit_values`), the best
   constant's share (entity answers by rank in their group, place answers by place type), branch balance, set
   membership, number spread; positional tells under four orderings with the exact binomial bound; the fixed-timer
   exposure; a pure-Python depth-2 decision tree over the nuisance features of 9.7 (declared `informative` features
   left out); an `L-AUDIT` error per failed bound (`lint`).
5. **The private families P3, P4, P5, P7, P8, PM** (`battery.private`, `hidden.py`) with the salt in every draw;
   results and runs carry family, outcome and label class only, the instance details go to `details` for the
   private store; PM's runs are `mustpass_runs`. Nothing runs without the salt (`private.NOT_RUN`). Tests use a
   throwaway key; nothing reads or creates `%USERPROFILE%\.house_ai`.
6. **Caps and costs** (`budget.py`): 10.5's family caps, 150 / 200 runs per spec (standard / full), 30 must-pass
   runs, 30 private runs (the tier's), the tier's CPU cap (with the core trials' CPU when the verifier passes
   `builds["cpu_s"]`) and simulated-hours cap; a cut run is `skipped:cap` (unmet) with its reason. The tier comes from
   `builds["budget"]` (the verifier's formula budget) when given, else 22.2's estimate from the references.
7. **Robustness**: a stage that raises (`run`, `mustpass`, `private`) returns an `error` record and a harness error
   instead of stopping the verifier; a generator that raises is an `error` record for its family and the other
   families still run; an audit that raises is an `L-AUDIT` error and a harness error. The verifier's own idle
   projections are reused when the references carry them (`mode_trials(..., capture={})`), else the idle rollout
   is re-run and its digest checked; the allowances are recomputed from the references when a caller passes none
   (`battery.allowances`). Mess carriers come from the domains' registered mess measures (`plugins.MESS_KINDS`:
   the kinds a DEPOSIT's object carries just before it, `trace.carried_kinds`), beside the record fields.
8. **Mitigations and their audits** (10.5, 22.5): forking from reference snapshots (`env.save` / `HouseEnv.load`)
   when the reference prefix costs at least 0.6 CPU-s; the first forked run and 5 % of them re-run from the build
   (state digest, labels, outcome; a mismatch disables forking, re-runs the forked runs and is a harness error);
   fast-forwarded waits for H1-H3 specs after the equivalence check, one and 10 % of them re-run fine; the
   breadth-first sweep at the standard level; the decided-early stop; WP7's `perception_audit.audit_run` over the
   first generated run of each family in a realistic mode (a leak is a harness error).
9. **Data**: `actions.json` (every one of the 427 registered actions classed; perceive.command_class reads it;
   `content_deposits`, `route_skills`), `inverses.json` (55 inverses: opposite verbs, setting restores, the replace
   form for deposits).

## Interfaces (signatures)

```python
from house_sim.tasks import battery
battery.run(spec, builds, traces, seeds, mode, level, explain=None, families=None, root=None)
    # builds: {"mode", "references", "extras"[, "budget": verify's budget record, "expected": E(entry),
    #          "cpu_s": the core trials' CPU]}; traces: {seed: mode_trials references}; seeds: V
    # -> {"ran", "runs": [run record], "summary": {"families": {f: {runs, counted, met, outcomes, met_share}},
    #     "passes", "unmet", "budget", "forked", "fork_audits", "forking"}, "applicable_families", "guess":
    #     {"roles", "rate", "episode"}, "b5a_discordant_failure_share", "protect_live", "load_bearing",
    #     "rates" (full), "lint" (full: L-AUDIT), "harness_errors", "notes", "tier", "fast_forward", "cpu_s"}
battery.mustpass(spec, builds, traces, seeds, mode, level, prechecked=None, root=None)
    # -> {"ran", "runs", "summary": {"applicable", "realised", "passed", "failed_unexempt", "failed",
    #     "unrealised", "capped", "budget"}, "harness_errors", "tier"}
battery.audit(spec, n=200, builds=None)          # audits.audit, cached per (spec hash, n) in the process
battery.private(spec, references, seeds, mode, level, salt=None, key=None, pool=None)
    # -> {"ran", "results": [{"family", "outcome", "label_class", "met"}], "runs": [{"id", "family", "outcome",
    #     "label_class"}], "mustpass_runs": [...], "details": [full records], "harness_errors", "summary"}
battery.heldout_rates(spec, builds, traces, seeds, mode, level)   # S7's rate families (B4, B5, B7, B8)
battery.prepare(spec, builds, traces, seeds, mode, snapshots=True, root=None) -> (seeds' trace.Seed, errors)
battery.write_explain(folder, policies); battery.summarise(runs); battery.guess_rates(records)
# run record (10.6): {"id", "family", "sub", "seed", "mode", "outcome", "realised", "labels", "accepted",
#   "goal_fraction", "unmet", "dropped_steps", "fallbacks", "plan_digest", "cpu_s"} + why, hint, refusals,
#   blocked_by, effect, early_stop, forked, fast_forward, audit, rate, rejected_by, perception_audit ...
# ids: <family>.<variant>@<seed>#<index> (B2@0#0, B3.floor@41#4, B5.a@1#0, B8.cross@2#0>2, B11.rate@all#r0,
#   B5.c-share@audit#<answer>, B7.exposure@audit#<answer>, P3.random@<seed>#<n>, PM.M2.home@...)
audits.audit(spec, n=200, informative=None, seeds=None, house=None) -> {"ran", "n", "answers": {name: {"kind",
    "audit_values", "best_constant_share", "bound", "k", ...}}, "tells", "exposure", "tree", "failures", "ok",
    "lint", "harness_errors", "cpu_s"}
audits.rate_records(audit) -> [B5.c-share / B7.exposure records]; audits.tell_check(ranks, k, orderings);
audits.tree_check(X, y); audits.exposure(times, items, budget_s, tol=60.0); audits.binom_tail(count, n, p)
generate.Context(spec, builds, ctxs, mode, level, tier, analysis=None, salt=None); generate.B2 ... B14(context)
    -> (policies, skips); generate.lift(entry) -> skill steps; generate.violates(item, t_trigger, tau, budget_s)
generate.mapped_refs(spec_full); generate.ref_label(ref, bindings); generate.live_refs(refs, runs, bindings)
policies.Policy(pid, family, sub, seed, items, effect=None, accept=None, meta=None, mustpass=False, dropped=0,
                effectful_total=0); policies.execute(policy, ctx, mode, plan_ctx, branch_diff=None, fork=True,
                ff=False) -> (record, env)
accept.accepted(policy, ctx, labels, unmet, plan_ctx); accept.about(label, ids, ctx, plan_ctx); accept.latches(l)
effects.check(policy, ctx, env, runner) -> (realised, detail)
mapping.Mapper(src, dst).map_id / map_value / map_entry; mapping.transplant(src, dst, entries=None);
mapping.canon(entry, bindings); mapping.lcs_merge(base, other, key)
answers.substituted(ctx, name, value) -> (items, changed); answers.by_rank; answers.domain; answers.value_on
budget.Budget(level, tier, what, budget_s, caps=None, spec_runs=None, cpu_spent=0).allow / spend / summary;
budget.tier_of(builds, ctxs); budget.family_seeds(ctxs, n); FAMILY_CAPS, MUSTPASS_CAPS, PRIVATE_CAPS
trace.build_seed(spec, seed, ref, mode, extras=None, snapshots=False, root=None) -> Seed;
trace.command_class(entry, state=None); trace.action_class(action); trace.replay_step(entry)
hidden.run(...); hidden.label_class(labels); hidden.pool_from_salt(salt, n); hidden.sample_pool(spec, salt, ...)
```

`house_sim.tasks.battery.mustpass` (the module) is shadowed by the function `battery.mustpass` (section 25 gives
both the name): import the module with `importlib.import_module("house_sim.tasks.battery.mustpass")` or `from
house_sim.tasks.battery.mustpass import GENERATORS`.

## Deviations from the spec (and why)

* **Skill-level generation is used throughout, not only for edited steps** (10.1): retargeted roles rebind the
  reference *plan* (skills route, reveal and approach), B8 `cross` runs the other variant's plan with this seed's
  bindings, and concrete commands moved to another place or house (B5a, superset additions, retargets without a
  binding) are lifted to skills (`generate.lift`: pick_up -> fetch, place -> put, effects -> use, approach -> the
  approach skill). Replaying concrete footsteps in another layout was refused ("Too small to read from here",
  "Approach it first") and made most of these runs `unrealised` battery debt. B5a stays open loop: A's ids, values and
  waited durations; only the walking is B's.
* **B6 `undo_last` undoes the last command a success label reads** (a command whose changed fields meet a goal
  footprint). Undoing the last command blindly undid `put(close=True)`'s cabinet door and failed the bleach task on
  `house.changed` alone (`fails_other`, a false alarm against a correct spec). `undo_all` is checked against the
  values before the earliest undone command (open + close of the same door ends closed).
* **B11 is one rate record per decision role** (`B11.rate@all#r<k>`); its blind picks are kept as diagnosis with
  `skipped:tier` (their requirement is the rate, not each pick). The B12 pre-checks and a breadth-first sub-policy's
  later seeds are `skipped:tier` too (not counted, recorded with their reason).
* **B5c's best constant and B7's fixed-timer exposure become battery records** (`B5.c-share`, `B7.exposure`) at the
  full level, where `run()` computes the 200-build audit first; `battery.audit` returns the cached record to S6.
  Entity answers' constants are judged by rank in their group (ids differ from house to house).
* **B7 without a timing key**: every confirming run must fail (for the 3-4 runs of a seed subset that is the same as
  a pass share of at most 0.2).
* **Forking only when it pays** (`trace.FORK_MIN_CPU_S` = 0.6 CPU-s of reference prefix): a snapshot is 4 MB and
  takes 0.07 s to save and 0.27 s to load, more than re-running a short prefix. Snapshots live in the system's
  temporary folder (or `HOUSE_SIM_FORK_CACHE`), not `reports/cache/forks/` inside the OneDrive-synced project, and
  are deleted after each stage.
* **The fast-forward equivalence check** replays the reference on the first seed with and without fast-forwarded
  waits (the idle half of 10.5's check is the verifier's idle rollout, which never fast-forwards).
* **The decided-early stop is checked at D** (after the plan, before settle and look-ahead), not after every command:
  the saving is the look-ahead, and a mid-plan check would cost an evaluation per command.
* **M2's `surface` alternative excludes the object's baseline place** (that is the `baseline` alternative); **M1**
  takes instruments only (tool arguments, carriers, tool-like types): a non-target moved aside is M2's.
* **M5 is one run per seed** rewriting up to two commands (k = 1 and k = 2, each in the other unit system), as
  11.4's "both paraphrases and the unit change in one run".
* **P7's confirming runs use V's seeds**: `battery.private` receives V's references only (request to WP5 for the
  private held-out references). The private audit pool comes from the salt (`hidden.pool_from_salt`, the same
  formula as `private.pool`); the spec's hidden test candidates are skipped only when the verifier passes `key=`.
* **PM's runs are `mustpass_runs`**, not `runs`: strength counts a PM pass as met in `c_mustpass`
  (`record["private_mustpass"]`), and a pass in `private_battery` would fail G3.
* **B14 `damaged` uses a domain's settings skill for a condition field** (WP-D manipulation registered `broken` and
  `torn`); the kernel has no damaging-verb registry and `plugins.py` is WP1's.
* **The positional tells use four orderings** (left to right, surface then shelf, distance from the start, bearing
  from the start pose); "handle order" is not computed: handles are issued by realism at run time, not at setup.

## Known gaps (and who owns them)

* The verifier does not yet read several of the battery's outputs (requests below): the guess rate, the B5a
  discordant-failure share and the audit values (c_guess, c_random), the battery's harness errors and lint rows (G7,
  G5), `protect_live` (G4's liveness from sources b and c), PM's runs (c_mustpass), `load_bearing` (M5's
  pre-check), `--explain`, and S7's rate families (`battery.heldout_rates` is ready; verify does not call it).
* Fast-forwarded waits are implemented and audited but only H0 fixtures are tested (the tests' tasks are short).
* B9 is tested by generation only (no fixture with user-simulator facts in this package's tests); B12 `shotgun`
  needs an answer group or domain and is tested by generation only.
* Prep is repeated per stage: `run` and `mustpass` each re-build every seed and re-run its idle rollout and
  reference (about 1.5-2 CPU-s per seed, 22.1); a shared prepared context is a later optimisation.
* Attacks that the house refuses for precondition / capability reasons on every candidate are `unrealised` (battery
  debt, unmet, P4). The generators were tuned on the fixtures here; new domains will surface new debt, which is WP6's
  to fix as generator work (10.6's hint names it).
* `actions.json` must be regenerated when the registry gains actions (`tests/test_battery.py`
  `DataTests.test_every_registered_action_has_a_class` fails until then; the generator script is
  `scratchpad/p2_WP6/gen_actions.py`, its class lists are in the file's `about`).

## Requests

* **WP5** (`verify.py`):
  1. after S5 copy `battery["guess"]` to `record["guess"]` (c_guess) and `battery["b5a_discordant_failure_share"]`
     to `record["randomisation"]["b5a_discordant_failure_share"]`; after S6 copy each
     `audit["answers"][name]["audit_values"]` into `record["randomisation"]["answers"][name]` (c_random reads them);
  2. extend `record["harness_errors"]` with `battery` / `mustpass` / `private` `harness_errors` (G7) and
     `record["lint"]` with `battery["lint"]` and `audit["lint"]` (L-AUDIT, G5);
  3. `record["private_mustpass"] = {"runs": private["mustpass_runs"]}` (c_mustpass), and pass `key=` (the split key:
     hidden test candidates skipped from the private pool) and the private held-out references to
     `battery.private` (P7 confirms on private seeds);
  4. merge `battery["protect_live"]` into `record["protect"]` liveness (7.6 sources b and c);
  5. pass `builds["budget"]` (`budget_check`'s record: formula_ok, tier, formula_time_s), `builds["expected"]`
     (E(entry)) and `builds["cpu_s"]` (the core trials' CPU, 22.6's cap) to `battery.run`, `explain=` from
     `--explain`, and `prechecked=battery["load_bearing"]` (seed keys as ints) to `battery.mustpass`;
  6. call `battery.heldout_rates(spec, builds, held_out_references, H[:4], EVAL_GEOM, level)` in S7 and count its
     passes in the held-out battery pass share (16.2, c_heldout).
* **WP8**: document the battery's interfaces, run records, policy ids and the two data files in docs/EVALUATION.md
  and KERNEL_API.md ("Battery (WP6)").
* **WP10**: the gate's S5 / S6 / S7p stages read the records above; `tools/battery_review.py` keys exemptions on the
  policy id without the seed index (`B8.superset`, `M2.surface`); `tools/harden_audit.py --cost` can read
  `summary.budget` (runs, CPU, simulated hours, cuts per family).
* **WP-D navigation_robot** (and any domain with damaging verbs): register `@settings_skill(<condition field>)` for
  them (as manipulation did for `broken` / `torn`); B14's `damaged` probe uses those (there is no separate registry).
* **WP5** (`tests/test_verify_hardening.py`): `HeldoutTests.test_private_heldout_seeds_stay_out_of_the_record` pins
  `assertFalse(self.private["private"]["ran"])  # the private families are WP6's`, true only while WP6 was absent;
  with the battery installed the private families run (salted, on V) and the assertion fails (normal and
  strict). Replace it with `assertTrue(self.private["private"]["ran"])` and check that every row of
  `self.private["private_battery"]["runs"]` has only family, outcome and label_class (verified on a copy of the
  test: the class passes, scratch `p2_WP6/tvh_copy.py`). Optionally run the eval-geom mode job with `capture={}` so the
  references carry the idle projections (`battery.run` reuses them instead of a second idle rollout per seed).
* **Main session**: `setup.py` package_data must include `house_sim/data/verify/actions.json` and `inverses.json`
  (the `data/verify/*.json` glob already requested covers them).

Requests to WP6 fulfilled: WP5 (the call shapes, the tier's run plan from budget.json); P2-S2-merge item 9
(actions.json, B12 denial frames and hedges, M5 number words: paraphrase k = 2); WP4 / WP4-fix (negate, hedge,
shotgun with group patterns, paraphrase, convert_units, the denial frame); WP11 (`put(close=True)` in B3 `near` and
M2 `home`; survey's places are the verifier's search baseline, not needed here); WP3 (B7 / P7 exposure by
`timing_outcome`; a policy that declares done before the event is B7 `early` at min t_T - 120 and B1); WP7 / WP7-fix
(`audit_run` over generated runs, agent labels through `verify._finish`, actions.json in perceive's `classes` form,
forked runs keep the perception evidence: the fork audit passes); WP9 (audit builds on `disk_cache.house` with the
fast path, `audit_diff` on the first seed, the cache's own once-per-process check); WP-D manipulation (B14 `damaged`
through `settings_steps`); WP-D cleaning (mess carriers from the registered measures `plugins.MESS_KINDS`, beside
`load` / `contents` / `debris`: `trace.carried_kinds`, test `DataTests.test_mess_carriers_come_from_the_registered_measures`);
WP-D safety (its settings skills are what B14's `changed` and `damaged` probes use, through `plugins.settings_steps`). Not fulfilled: WP-D navigation_robot's damaging-verb registry (kernel file; the settings
skill route above serves it).

## Phase-1 carry-over

None of the open rows lies in WP6's files (all new): the rows of `closeout_breakages.json`, the findings of
`closeout_REV.json` confirmed in `closeout_VER2.json` and `closeout_INT3.json`'s requests name domain, gate-tool,
py39_lint and kernel files (searched for the battery files, actions.json, inverses.json and the test files).

## Legacy outcomes

New files only. `actions.json` changes `perceive.command_class` (WP7 read the 10.1 lists until it existed): it is
read only by schema-4 evidence gating and report perceivability, which no legacy verification uses. All 243 authored
specs pass in their own mode (see WP6.json); `tests/test_perceive.py` passes with the file. outcome_changes: none.

## Cost (section 22)

Measured on the shared machine (other agents running): one generated run costs 0.5-1.0 CPU-s on the fixtures
(H0, budgets of 4-10 minutes), the per-seed preparation 1.5-2 CPU-s; the book task's whole standard battery on 3
seeds 21.6 CPU-s (45 runs), the bleach task's 45.7 CPU-s (about 50 runs), the two-branch task's on 4 seeds 39.5
CPU-s; must-pass 0.7-1.2 CPU-s per run; `verify_hardened(book, "standard", seeds=[0])` spent 10 CPU-s in S5 (battery
and must-pass). 22.4 plans 71 + 16 CPU-s for S5 of a 6-seed H0 spec. An audit build on a reused house costs about
0.1 CPU-s (200 builds about 20 CPU-s against 9.7's 12; the disk cache's first fill is WP9's 12 CPU-minutes per
generator fingerprint). Fork snapshots: 0.07 s to save, 0.27 s to load, 4 MB each.

## Results (shared tree, 2026-10-29, with run B's WP-D packages editing their domains concurrently)

* `tests/test_battery.py` 66, `tests/test_mustpass.py` 12, `tests/test_audits.py` 12: all pass, normal and
  `HOUSE_SIM_STRICT=1` (`green_tests.py 3 "test_[bma][aug][tsd]*.py"`: 90 tests, 0 failures; 368 / 383 s, 95 / 91 s,
  23 / 16 s).
* `py39_lint`: the 15 files, 0 violations.
* All 243 authored specs in their own mode (combined file, `--check --workers 3`): 243 passed, 0 failed.
* Full unit suite (`green_tests.py 3`): 5,647 tests per mode; 5 files fail, in both modes. One is this package's
  landing: `test_verify_hardening.HeldoutTests.test_private_heldout_seeds_stay_out_of_the_record` asserts the
  private families did *not* run ("the private families are WP6's"), which held only while WP6 was absent (request
  to WP5 above; the replacement assertion passes on a copy). The other four are the concurrent WP-D edits, not
  WP6's files: `test_dialogue` (the vocabulary tables in docs need `tools/vocab.py --markdown` after people's
  `data/vocab/people.json`), `test_ledger` (cleaning's new `hardening.py` registers mess kind `debris`, which the
  test registers too), `test_lint` (`T-d389e2979f6aad-01`'s L-LATCH case: cleaning now derives `in_stream`),
  `test_room_golden` (cleaning, navigation_robot and safety specs' digests: their re-record rows).

## How to test

```
python -m unittest tests.test_battery tests.test_mustpass tests.test_audits     # also HOUSE_SIM_STRICT=1
python -m unittest tests.test_battery.GeneratorTests tests.test_battery.AcceptTests tests.test_audits.StatisticsTests
python tools/py39_lint.py house_sim/tasks/battery/*.py tests/test_battery.py tests/test_mustpass.py tests/test_audits.py
python -m unittest tests.test_perceive tests.test_verify_hardening
python tools/orchestration/green_tests.py 3
python -m house_sim --check <all authored specs combined> --workers 3
```
