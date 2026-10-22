# house_ai independent review: Phase 4 (Architecture and process)

Reviewer: Claude (Opus 5.5), subagent "p4_arch". Date: 2026-10-22, about 00:00-01:15 local time.
Code reviewed: the frozen copy of commit `bc0417b` at `scratchpad/review/tree` (no `.git`). The live repository at
`<home>\OneDrive\Desktop\house_ai` was not touched. All my scripts and outputs are in
`scratchpad/review/out/p4_arch_scratch/` (called `S/` below; full path
`<home>\AppData\Local\Temp\claude\C--Users-USER-OneDrive-Desktop-house-ai\e86e18f6-923d-4bcc-93e1-ba70c8993bc5\scratchpad\review\out\p4_arch_scratch`).
Nothing was written inside the tree (I set `PYTHONDONTWRITEBYTECODE=1`; my scripts write only to `S/`).

Evidence labels: **[ran]** = I executed it and saw the result; **[read]** = I read code or docs but did not run it;
**[inferred]** = reasoning only.

Hard-rule compliance: every command ran after `source S/env.sh`, which points `USERPROFILE` and `HOME` at
`S/fakehome`, sets `HOUSE_AI_FORBID_NETWORK=1` and removes every API, split and verifier key variable. I never read,
listed or created anything under the real `%USERPROFILE%\.house_ai\`, never ran `--init-split-key`, ran no LLM agent
(not even `--dry-run`) and made no network call. Worker counts: 3 for the two spec-verification runs, 1 for everything
else. The machine was shared with another heavy job (CPU load 100 %, 22 other python processes when I checked), so
every timing below is inflated and noisy.

---

## Status update (end of Phase 4)

### Simple version

I measured how the project is built and how its AI-agent work process can let mistakes through. The most important
result: the planned next step of "switching on" the new physics rules is not as safe as the records say. When I
switched on only the six rules that the project's own notes call ready, 8 of the 243 tasks stopped working, because
one rule (stacked things fall over) now makes a mixing bowl fall off a kitchen scale and a shirt fall off an ironing
board. With all 19 waiting rules on, 10 tasks fail (the same 8, plus 2 the notes already predicted or that belong to a part not yet converted), and every one of the 1,215 test runs ends in a slightly different house state. The code is large (about 217,000 lines) and its 16 parts talk to each
other through more than 2,300 plug-in connections; one action, "put something down", runs 79 separate pieces of code
from all 16 parts, which is where the three real bugs found in the previous phase came from. Good news: results do not
change when I imitate an older Python's arithmetic, saving and reloading mid-task almost always gives the same result,
the four copies of the room-brightness formula agree, and I found no Python 3.9 problems. The plan for having AI agents
write the remaining 2,477 tasks is risky as written: its instructions are out of date, it trusts each agent's own
report that its work passes, and two of the round scripts still cut the reports they pass on. Nothing is blocking me.

### Advanced version

* Phase 4, about 1 h 15 min elapsed (00:00-01:15, 2026-10-22).
* Commands and results: section 1. Measurements: section 2. Process review: section 3. Findings with severity and
  evidence label: section 4. New checks: section 5.
* Headline findings: A1 (High) D1 preview: forcing the 6 "ready" laws breaks 8/243 specs (39/1,215 trials), all via
  `stack`, all in domains that `laws.json` does not list for that law; all 19 laws: 233/243 pass, every end state
  changes [ran]; A2 (High) plugin sprawl: 2,333
  registrations, `place` runs 79 before/after handlers from 16 domains, tie order decided by module names, observation
  content changes in 9 specs when tie order is reversed [ran]; A3 (High) the authoring and adoption workflows trust
  self-reports, are stale against kernel v3, and still truncate handoffs [read]; A4 (Medium) hidden state outside the
  journal: a stale `static_index` makes save/load diverge (1/470 save points) [ran]; A5 (Medium) duplicated mechanics:
  19/23 laws deferred, 10 domains have no law guards at all, sounds appended by 14 domain modules (11 domains) plus the kernel, in 2 schemas [ran]; A6
  (Medium) brittle tests: 25 wall-clock tests in the correctness suite, about 2,280 whole-state digest pins [ran];
  A7 (Medium) performance and replay size [ran, noisy].
* Null and positive results: Python 3.9 deep scan clean (444 files) [ran]; 3.9-style float `sum()` changes 0/243 final
  states [ran]; sparse 1-ulp libm differences change 0/243 [ran] (dense ones 18/243, no verdict change); light-level
  copies agree on 10,520 room comparisons [ran]; reversing plugin tie order changes no final state or verdict [ran];
  no static cross-domain imports [ran]; no locale/timezone/wall-clock dependence in simulation code [ran grep];
  private paths use `Path.home()` (cross-platform) [read].
* Open questions for the owner: (1) Should D1 be blocked until every law is verified against all 243 specs (not only
  the listed owners' specs) and the `duplicated_by` lists are regenerated from code? (2) Do you want the authoring
  workflow rewritten before the pilot (re-run `--check` in the workflow itself, a third-party sample audit, files not
  inline JSON)?
* Next: phase 5 (plan judgement) belongs to another reviewer; my items for it are in section 4 "smallest fix" lines.

---

## 1. Commands run and results

All from the tree as working directory after `source S/env.sh`.

| # | Command | Result |
|---|---|---|
| 1 | `python S/loc.py T S/loc.json` | `house_sim` 216,794 physical lines (216,341 without `legacy_v1`; 172,555 lines of code without blanks, comments and docstrings). Kernel 23,706 + `kernel/phys` 7,398 = **31,104 (14 %)**; the 16 domains **163,970 (76 %)**; eval 11,288; generator 3,248; tasks 3,188; `systems/core.py` 2,063. 10,514 functions, 102 over 100 lines, 1 over 300 (`eval/dataset.py validate`, 341). Tests 55 files / 71,456 lines; tools 23 files / 6,165 lines; type data 1.07 MB |
| 2 | `python S/registries.py S/registries.json` (after `load_domains()`) | 427 actions, 162 before, 579 after, 111 systems, 42 furnishers, 280 scenario helpers, 264 skills, 32 observers, 50 inspectors, 89 invariants, 31 answerers, 266 hook registrations on **171 hook events** = **2,333 registrations**. `place` has 79 before/after handlers from all 16 domains, `pick_up` 65, `open` 53, `move` 35. 23 hook names are run but never registered (plus 4 dynamic prefixes) (e.g. `light_level` at 4 call sites, `phys.wetted` at 4, `robot_fell`, `power_down`); `person_eats` is registered (food) but never run |
| 3 | inline: order ties in the plugin lists | 741 before/after handlers carry hand-picked order numbers (only 1 uses the default); `place`'s 69 after-handlers use 47 distinct numbers; **187 handlers share an order value with another handler of the same action**, so their sequence is decided by `"module.function"` alphabetical order; 255 of 266 hook registrations use the default order 100 (alphabetical by module); 47 of 111 systems share an order value |
| 4 | `python S/tie_order_probe.py S/ties_all.jsonl` (reverse the name tie-break, keep numeric order; before/after, hooks, systems, observers, inspectors), then compare with `S/perturb_none.jsonl` (same harness, unmodified) | 49 lists reordered; 243 reference plans, seed 41: **0 final states, 0 evaluations, 0 verdicts differ**; observation key order differs in 81, observation content in 9, step-result content in 2 (`T-3b1a4024d46110-01`, `T-1cd8e5b7e25846-01`) |
| 5 | `python S/ties_obsdiff.py T-33ad442208b4b7-01 T-3b1a4024d46110-01 T-7d4006bda247ba-01 T-af96a334296669-01` | the content difference is the order of `obs.sounds` (14 modules in 11 domains plus `kernel/phys/observe.py` append to it); the two sound records have different schemas (`loudness` 0-1 + `source_hint` vs `loudness_db` + `direction` + `pattern`) |
| 6 | `python S/d1_preview.py run S/d1_ready.jsonl --laws ready --workers 3` (forces `crush, edge, stack, wet_contact, open_spill, handle`: every domain in their `duplicated_by` is adopted and documents them as ready), then `compare` with Phase 1's own-mode baseline at the same fingerprint `c72c3376...` | **235/243 specs pass; 8 newly failing (39 of 1,215 trials)**, 0 fixed; reference end state changed in 55 trials; 0 wrong-plan verdict flips. Failing: `T-1aea0c41439b0a-01` (appliances_cooking), `T-dd0bf4a1707041-01` (electrical), `T-4812d9daf84708-01`, `X-44e90cc83ba0db-01`, `P-386d0f28709b05-01` (food), `T-7d4006bda247ba-01`, `T-ae4651b88eca7d-01`, `T-6eed74e7a248a5-01` (textiles_laundry). 1,550 s |
| 7 | `python S/d1_perlaw.py crush,edge,stack,wet_contact,open_spill,handle <the 8 ids>` | **`stack` alone breaks all 8**; each of the other five breaks none. Later runs of the same script: `setdown` alone breaks `T-ce25a2474ffb3d-01` (seeds 0, 1, 97); `rain` alone breaks `T-118437362c4ab8-01` (seeds 0, 1, 2); the other 12 laws break neither |
| 8 | `python S/stack_law_probe.py T-4812d9daf84708-01 mixing_bowl_2 kitchen_scale` and `... T-7d4006bda247ba-01 ironing_board dress_shirt_2` | with `stack` active, `place mixing_bowl_2 kitchen_scale_2` says "mixing_bowl_2 fell \| mixing_bowl_2 toppled off the stack" (bowl 0.29 m wide on a 0.22 x 0.18 m scale); `pick_up clothes_iron_1` makes the shirt on the ironing board topple, and the iron placed on the board topples too (the ironing board type declares `open_size_m` [1.2, 0.38, 0.9] and `surface_size_m`, but the kernel reads `size_m`, which is the folded shape 0.38 x 0.10 x 1.37 m; no kernel module reads `open_size_m` [ran grep]) |
| 9 | `python S/d1_preview.py run S/d1_all.jsonl --laws all --workers 3` (all 19 deferred laws) and `compare` | **233/243 pass; 10 newly failing**, 0 fixed; 42 reference verdict flips and 3 wrong-plan flips (`T-ce25a2474ffb3d-01` "rushes and sets it down hard on marble" now passes on seeds 0, 1, 97); **the reference end state changed in all 1,215 trials**. The 10: the 8 `stack` specs, `T-ce25a2474ffb3d-01` (`setdown` alone breaks it, matching manipulation's documented "not ready") and `T-118437362c4ab8-01` (outdoor_plants, `damage` on seeds 0-2; `rain` alone breaks it; outdoor_plants is listed for `rain` and not adopted). 1,425 s |
| 10 | `python S/py39_deep.py house_sim tools tests setup.py house_env.py setup_project.py` (self-tested on planted cases first: `S/py39_selftest.py`) | 444 files: **0** syntax errors under `feature_version=(3, 9)`, **0** PEP 701 f-strings, 0 runtime PEP 604 unions, 0 forward-referenced or TYPE_CHECKING-only annotations, 0 hits of a 3.10+ API list. The code base has **0 annotations, 23 f-strings and 3,576 `.format` calls**, which is why. Note: on Python 3.14, `ast.parse(..., feature_version=(3, 9))` **accepts** 3.12-only f-strings (same-quote reuse, comments, nesting) [ran], so the Phase 1 scan alone could not have caught them |
| 11 | `python S/perturb_run.py none\|sum39\|ulp\|ulp:0.1\|ulp:0.01 ...` then P2's `det_replay.py compare` (243 reference plans, seed 41) | `sum39` (naive left-to-right float `sum`, the 3.9-3.11 behaviour): 196,856 float sums, **52,570 give a different float**, **0/243** final states, observations or step results differ. `ulp` (every one of 5,495,722 transcendental results nudged up by one ulp): **18/243 final states differ, 0 verdicts**. `ulp:0.1` (600,603 nudged) and `ulp:0.01` (68,055 nudged): **0/243** differ |
| 12 | `python S/ulp_statediff.py 1.0 T-032657e9583337-01 T-259d38eb6707f0-01 T-94a0a940224c22-01 T-0caf47d0f789f5-01` | differences are last-digit floats (`_vessel/max_c` 91.93000888454951 vs ...52) and **discrete visibility**: `nav/seen` bitmaps of two yards change and `nav/visits/backyard_1/coverage` goes 0.736 -> 0.732 |
| 13 | `python S/saveload_probe.py S/saveload.jsonl` (243 specs, seed 41; save + `HouseEnv.load` at 1/3 and 2/3 of each reference, continue, compare with an uninterrupted run) | 470 save points: **1 final state and observation differ** (`T-50c78da60ad2a8-01`, save at step 12 of 18), 0 verdicts, 0 step messages; 1,462 s |
| 14 | `python S/saveload_diff.py T-50c78da60ad2a8-01 12` | after the reload, `toaster_1` gains `_free_l` 1.77 and `heat_load_w` 13.56 that the uninterrupted run never writes. Cause [read]: food's `_cold_ids` uses `w.static_index("food.cold", lambda r: "internal_temp_c" in r ...)`, a cache valid "until an entity is created or destroyed", over a field a toaster gains at run time |
| 15 | `python S/static_index_audit.py S/static_index_audit.json` (every static index used by every reference run, recomputed after every step) | **10 static indexes go stale** during reference runs, in 6 domains: `electrical.markers` (119 specs, stale from step 1), `food.checked` (5), `appliances_cooking.relevant` (2), `safety:liquid_holders` (2), `food.cold`, `safety:device_heat` (a flashlight switched on gains `heat_w`, which the "static superset" predicate reads), `safety:air_sources`, `admin_inventory.child_candidates`, `cleaning.floor_cover_candidates`, `cleaning.liquid_holders` (1 each); 334 s. `S/stale_effect.py` (same run with the named indexes never cached): `food.cold` plus `food.checked` reproduce exactly the 2 leaves of the save/load divergence (`toaster_1.heat_load_w` None -> 13.56, `_free_l` None -> 1.77), so the live run is the stale one; `device_heat`, `electrical.markers`, `relevant`/`liquid_holders` change nothing in the specs I tried (null) |
| 16 | `python S/light_diff.py 12 20` | the kernel's `light_level` and the three domain copies (climate `light_level_fast`, electrical `_room_level`, people `light_level`) on 24 houses (gen 1 and 2) under random lights, dimming, burn-outs, power cuts, curtains, clock, moved and carried lamps: **10,520 room comparisons, 0 mismatches** (null result) |
| 17 | `python S/dup_mechanics.py S/dup_mechanics.json` (function-name and field-write census; a noisy proxy: names like `add_breaker` match "break") | domain functions per concept vs kernel: thermal 118 (kernel 22), breakage 97 (21), liquids 119 (30), statics 129 (50), visibility 92 (70), wetness 107 (10), friction 55 (25); direct field writes: `temperature_c` 83 sites in 15 domains, liquid fields 138, `wetness` 135 |
| 18 | greps: legacy constants | the legacy thermal time constant `300*sqrt(m)+60` in `kernel/physics.py:112` and `plumbing_liquids/ice.py:415`; gravity literal 9.8x in 8 domain files and 6 kernel files; water heat capacity literal in 7 domain files and the kernel; the window factor `0.45 * (1 - covered)` in 4 files |
| 19 | `grep lawbook.active / kernel_owns` per domain | guards exist only in 5 of the 6 adopted domains (manipulation 22, plumbing 4, safety 4, navigation 2, cleaning 1; people is in no `duplicated_by` list) plus core 1; **0 in the 10 unadopted domains**, 9 of which appear in some law's `duplicated_by` |
| 20 | `python S/global_state.py S/global_state.json` | 790 module-level mutable objects, 79 of them mutated inside functions in 47 modules, 6 `global` rebinds, 10 `WeakKeyDictionary` memos keyed by `World` in kernel camera, sight and environment; 146 import-time statements (registrations) |
| 21 | `python S/test_brittleness.py S/test_brittleness.json` plus an AST pass for wall-clock tests | 3,985 tests, 14,838 asserts; **25 tests in 17 files assert wall-clock budgets** (list in section 2.6); 633 `assertEqual` on float literals; 506 assertions on prose strings of 12+ characters; 107 registry mutations in tests; digest pins: 1,804 in `tests/data/gen1_digests.json` and 473 in `room_golden.json` |
| 22 | `python tools/bench_systems.py 40 0 1 2 3 --camera geometric`, the same without `--camera`, and `--day 0 1 2` (loaded machine) | 7.60 ms / 7.49 ms per 30 s substep (112 systems; C1 gate: 3.17 ms); 1 h wait 0.53-0.57 s; observe on seed 0: oracle 1.79 ms, realistic geometric 9.36 ms, realistic room 11.6 ms; **quiet day 14.4 s mean** (12.5-15.9 s; C1: 6.45 s; target 3.5 s). Top systems per day: climate.house 1.96 s, electrical.power 0.93 s, thermal 0.84 s, plumbing water 0.81 s, people.senses 0.79 s |
| 23 | inline: `save_replay` of 5 reference episodes, sizes | 3.25-3.85 MB per episode of 10-37 steps; `initial` (about 1 MB), `start_state` (about 1 MB) and the baseline are near-copies of the same house; per-step `changes` 0.26-0.78 MB; **gzip: 0.24-0.28 MB (about 14x)**; no compact format in `bc0417b` [ran grep] |
| 24 | inline: generated houses, seeds 0-3 | 649-948 entities, state JSON 0.72-1.09 MB, **1,287 distinct entity field names** in a house; 1,738 distinct prop names across 2,037 types; no field ownership or schema |
| 25 | inline: catalog under LF line endings (an LF copy in `S/lf/`, deleted afterwards) | `catalog/raw/household_catalog.txt` is CRLF and there is no `.gitattributes`; with LF, all 2,720 ids are unchanged (good) but all 2,720 `byte_start`/`byte_end` and the manifest `source_sha256` change (the evidence manifest in `audit.py` records that hash) |
| 26 | greps: CI, Windows, encoding, process | no CI configuration of any kind; 3 `open()` without encoding, no shell=True, no drive letters, `os.name` branches only for case-insensitive paths; `Path.home()` for private files; 110 broad `except Exception` handlers, 51 of them silent (pass / return / continue); 7 cross-domain couplings through `sys.modules` or another domain's module path |
| 27 | `python -X importtime` and a timed `load_domains()` | importing the engine 0.37 s, `load_domains()` 0.23 s, 335 `house_sim` modules; `STRICT`, `SAFE`, `IDLE_PROBES` and `HOUSE_SIM_DOMAINS` are read once at import |
| 28 | reads: `tools/orchestration/*.js`, `README.md`, `catalog/analysis/tier1/*open*.json`, `round4/5_results.json` | section 3 |
| 29 | `python S/hook_profile.py S/hook_profile.jsonl` (every before/after handler, hook and system wrapped with a timer; 243 references, seed 41; loaded machine) | 4,450 steps, 68.2 s in `env.step`: systems 44 %, hooks 9.7 %, after-handlers 8.2 %, before-handlers 0.7 % (hooks called inside systems count in both). Most expensive: `climate.house` 6.5 s, `food.thermal` 4.8 s, `food.refresh` 3.2 s, `electrical.power` 2.2 s (8,232 substeps each); navigation's after-handlers on `approach` and `move` 3.0 s together; `episode_start` hooks of outdoor_plants, people and electrical about 1 s each over 243 resets |

## 2. Measurements

### 2.1 Size and shape [ran]

| Part | Files | Lines | Share of `house_sim` |
|---|---|---|---|
| kernel (incl. `phys/`) | 41 | 31,104 | 14 % |
| 16 domains | 281 | 163,970 | 76 % |
| eval | 10 | 11,288 | 5 % |
| generator, tasks, `core.py`, `systems/__init__.py`, top level | 28 | 9,979 | 5 % |
| `legacy_v1` | 3 | 453 | 0.2 % |

Largest domains: safety 14,326; outdoor_plants 12,554; appliances_cooking 11,766; people 11,737. Largest kernel
modules: realism 2,948; camera 2,916; sight 2,738; body 1,887; dialogue 1,731; phys/impact 1,453. The brief's
"about 222,000 lines" is about 2.5 % high (216,794).

### 2.2 Plugins and hooks [ran]

* 2,333 registrations in 12 registries; 171 named hook events.
* Every one of the 16 domains wraps `pick_up` and `place`; `place` runs 79 handlers. A domain's change to a core verb
  is invisible from that verb's code.
* Ordering is a global, hand-maintained integer schedule (47 different numbers on `place`), with 187 ties broken by
  module and function names. Reversing the tie-break changed no state (good), but changed observation content in 9
  specs (sound order) and key order in 81 [ran].
* 23 hook names are run with no listener (extension points for the 10 unadopted domains, or dead), and 1 listener
  (`food: person_eats`) is never run, although food's docstring says "people's `person_eats` hook" [read].
* Hidden coupling without imports: electrical skips its three-way-switch rule if a mechanisms module hooks
  `switch_on`; plumbing changes pour behaviour if a cleaning module hooks `pour`; safety decides who counts an injury by
  whether a hook's module name starts with `house_sim.systems.people.`; three more reach into another domain through
  `sys.modules` [read]. A module rename changes behaviour silently.

### 2.3 Duplicated mechanics and deferred laws [ran unless marked]

* `laws.json`: 23 laws, **19 deferred**, 4 active. Blocking counts: manipulation 13, plumbing_liquids 5, admin 4,
  mechanisms 4, outdoor 3, appliances 3, food 3, cleaning 2, climate 2, and one each for safety, navigation, pets,
  textiles, electrical.
* Guards that let legacy code step aside exist only in adopted domains; the 10 unadopted ones have none, so flipping a
  law listed for them would apply the kernel rule and the domain's own copy together today [inferred from the grep].
* `duplicated_by` is incomplete: `stack` lists only manipulation, but textiles has its own stack code
  (`fold.py _stack_root`, `textile_stack_fit`) [read], and forcing `stack` breaks specs in four unlisted domains
  (rows 6-8).
* Concrete copies: the room light formula in 4 places (agree today, row 16); the legacy thermal time constant in 2;
  sound observations appended by 14 modules in 11 domains plus the kernel, with 2 incompatible record schemas (row 5, grep); the
  function-name census (row 17) is a noisy upper bound and should be read as "every domain has its own physics
  vocabulary", not as a count of bugs.
* Readiness was verified per owner on the owner's own tests and specs (manipulation docs: "over
  `tests/test_manipulation.py` and both spec files") [read], which is why the cross-domain breakage was not seen.

### 2.4 Global state and caches [ran]

790 module-level mutable objects; 79 mutated at run time (registries, memos, caches); 10 `WeakKeyDictionary` memos keyed
by `World`; 68 `static_index` call sites whose predicates must only read static fields, which nothing enforces. The
save/load probe (row 13) and the staleness audit (row 15) are the behavioural tests of this.

### 2.5 Python 3.9, cross-version and cross-platform [ran unless marked]

* Static: clean (row 10). Runtime on 3.9: unverifiable here (only 3.14.8 installed; no WSL).
* Arithmetic: the 3.12 `sum()` change is harmless for every reference run (row 11). Libm differences matter only when
  dense (row 11): 18/243 final states, including discrete `nav/seen` cells, so cross-platform replay digests and
  golden pins can break on a different C library [inferred from ran]; verdicts held in every case. `**` and
  `math.sqrt` were not perturbed.
* Windows assumptions are few (row 26); the CRLF catalog is the only line-ending dependency found (row 25).

### 2.6 Tests [ran]

Wall-clock tests (25): `test_body_core` (1), `test_body_engine` (2), `test_body_model` (1), `test_body_perception`
(1), `test_camera` (2), `test_camera_realism` (1), `test_dialogue` (2), `test_generator_gen2` (1), `test_mind` (1),
`test_perception` (1), `test_physics_data` (2), `test_physics_fluids` (2), `test_physics_settle` (3),
`test_physics_statics` (2), `test_physics_thermal` (1), `test_reports` (1), `test_sight` (1). Phase 2 saw two of them
fail under load (`test_mind`, `test_dialogue`). Whole-state digest pins: about 2,280, all of which changed at the
adoption checkpoint (Phase 2 T6).

### 2.7 Performance and replay size [ran, loaded machine]

See rows 22-23. Relative numbers are more trustworthy than absolute ones here: realistic observation costs 5-6x
oracle; climate, electrical power, thermal and plumbing water dominate a quiet day; a replay is three copies of a
1 MB house plus per-step diffs and gzips 14x.

## 3. Process review: where regressions or dishonest specs can slip through

All [read] from `tools/orchestration/*.js`, the README and the analysis records, unless marked.

1. **The authoring workflow trusts what agents say.** `author_wf.js` returns each agent's own `verified_passing` list
   and the reviewer's own list; the script never runs `--check` itself. Phase 2 showed the adoption agents reported
   "green" after being told to run a gate command that exits with an argparse error (T4) [ran by P2].
2. **The reviewer has the last word and edits the spec.** The adversarial reviewer may tighten or remove specs and is
   the last verifier; nobody checks the reviewer's edits or the "removed" and "gap" decisions. Dropping a hard entry as
   a "gap" is the cheapest way to finish a batch, and the workflow does not audit gap legitimacy.
3. **Gaming plans are tuned on the verification seeds.** Reviewers add the gaming plans they found to the wrong plans
   and re-verify on seeds 0, 1, 2, 41, 97; there is no held-out set of gaming policies or seeds, so a spec can be
   tightened just enough to beat the plans its reviewer happened to think of [inferred].
4. **The workflow templates are stale.** `author_wf.js` tells authors to read KERNEL_API "v2.1 through v2.8" and has
   no mention of the camera, generation 2, schema 3, physics or `--camera geometric` (grep counts 0) [ran grep],
   although the plan is to author in schema 3 with the geometric camera as the realistic default. `gapfix_wf.js`
   points at "v2.3".
5. **Handoffs are still truncated inline.** `tier1_adopt.js` passes the 18 agents' reports to the integrator as
   `JSON.stringify(...).slice(0, 30000)`; `tier1_stage_a.js` uses `.slice(0, 16000)` and `.slice(0, 5000)`. The brief
   says this problem was "fixed by passing findings as files"; stage B and C do pass files, the adoption round
   (the template for the next 10 domains) does not.
6. **Concurrent edits in one tree.** 16 domain owners and 2 kernel agents edit the same working tree at once; each
   "GREEN before returning" run is against a tree others are changing, so it is not reproducible; the gate's
   `--shared-tree` only attributes files [read]. Git worktrees per agent would isolate them.
7. **Per-owner verification misses cross-domain effects.** Law readiness, adoption greens and gap fixes are checked on
   the owner's own tests and specs (row 6-8 is the concrete consequence).
8. **Open items live in per-round JSON files, not in a ledger.** Stage B: 53 not done, 66 requests, 107 deviations;
   stage C: 21 / 29 / 49 (its 33 review fixes are all marked fixed); round 4: 56 not fixed; round 5: 19 not fixed
   [ran count]. The adoption prompt asks each owner to "search for <domain>" in these files; nothing records closure.
9. **Failure classification is a single person's call.** The checkpoint's 14 failing tests were labelled "pins to
   re-record"; 3 were real defects (Phase 2 T1). A re-record of goldens would have baked them in.
10. **No CI.** Everything above runs on one Windows machine with Python 3.14; there is no clean-clone run, no 3.9 run
    and no Linux run (row 26).

What the process does well: package scopes (which files each agent may edit), structured result schemas, per-package
handoffs for stages A-C, the gate's digest and verdict comparison, stage-B/C review findings passed as files, and a
`golden.py dump/diff` tool designed for exactly the attribution problem Phase 2 hit (it just was not used).

## 4. Findings (ranked)

### A1 (High). Switching the physics laws on (D1) will break specs; the readiness protocol cannot see it

* **What is wrong.** The plan flips a deferred law once every domain in its `duplicated_by` list reports "ready"
  (TIER1_INTEGRATION D1). Six laws meet that rule today. Forcing just those six on breaks 8 of 243 specs, and the lists
  that decide who must be ready are incomplete.
* **Evidence.** [ran] rows 6-8: 8 specs / 39 trials fail, all through `stack`; the failing specs are in
  appliances_cooking, electrical, food and textiles_laundry, none of which `laws.json` lists for `stack` (only
  manipulation). Mechanism [ran, read `kernel/phys/statics.py:346-378`]: `STACK_OVERSIZE = 1.25`, so a 0.29 m mixing
  bowl on a 0.22 m kitchen scale "toppled off the stack"; the kernel reads the ironing board's `size_m`, its folded
  shape (0.38 x 0.10 x 1.37 m), and ignores the type's own `open_size_m`, so anything laid on it exceeds `STACK_TOPPLE`. [read] textiles has its own stack code
  (`fold.py _stack_root`, `textile_stack_fit`) that the list omits; manipulation's readiness was checked "over
  `tests/test_manipulation.py` and both spec files" only. [ran] row 9: with all 19 deferred laws forced, 233/243 pass; the extra two failures are expected by the protocol (`setdown`, documented not ready; `rain`, owner not adopted), and every trial's end state changes.
  [read] manipulation's own docs record that a spec's wrong plan depends on a legacy rule the kernel law does not
  reproduce (`setdown`, `T-ce25a2474ffb3d-01`).
* **Why it matters.** D1 is step 2 of the owner's plan. It changes the physics under every domain at once; each owner
  only looked at their own specs; and the first breakages are everyday cooking and laundry steps, i.e. the kernel rule
  and some type data are not yet physically right for configurations the legacy code never asked about.
* **Smallest fix.** Make readiness global: before a law flips, `d1_preview.py --laws <law>` over all specs and all
  unit tests (about 25 min with 3 workers) must show no verdict change, or every change is reviewed by the owning
  domain of that spec. Regenerate `duplicated_by` from that run. For `stack`: judge stability by centre of mass over the
  support (a wide bowl centred on a small scale is stable), and give folding furniture its in-use size.

### A2 (High). The plug-in surface is where the real defects come from, and nothing maps it

* **What is wrong.** Domains change core verbs through 2,333 registrations; ordering is a global hand-kept integer
  schedule with name-based tie-breaks; several domains change behaviour depending on whether another domain's module is
  loaded, by module name.
* **Evidence.** [ran] rows 2-5: `place` runs 79 handlers from 16 domains, `pick_up` 65; 187 handler ties are broken by
  `"module.function"` order and 255/266 hook registrations use the default order; reversing ties changed observation
  content in 9 specs and key order in 81 (no state change). 23 hooks are run with no listener; `person_eats` has a
  listener and no caller. [read] 7 couplings through `sys.modules` or module-name prefixes (electrical -> mechanisms,
  plumbing -> cleaning, safety -> people twice, appliances -> safety, mechanisms -> safety, and safety's generic
  `domain_loaded` prefix check). [ran by Phase 2]
  all three genuine defects at this checkpoint are cross-domain hook interactions (manipulation slip vs the kernel lift
  plan, safety `phys.broke` vs the kernel wrap, people `camera_capture` double count).
* **Why it matters.** At 2,477 more specs, the interactions no owner tested will be exercised by authors who cannot
  see them. A file rename can change simulation output without an import error.
* **Smallest fix.** (1) Generate an interaction map (per verb and hook: handlers, owner, order) from the registries,
  commit it, and make the gate fail on unreviewed changes to it. (2) Refuse a registration whose order value is already
  used for the same action, event or system list (forces an explicit decision). (3) Replace module-name checks with
  capabilities registered in the kernel (`plugins.provides("three_way_switching")`). (4) Delete dead hooks or document
  them as extension points.

### A3 (High). The authoring process, as scripted, can produce dishonest specs without anyone noticing

* **What is wrong / evidence.** Section 3, items 1-7 [read; item 1 also ran by Phase 2]: the workflow aggregates
  agents' self-reported `verified_passing`; the adversarial reviewer edits the spec and is the last verifier; gaps and
  removals are not audited; gaming plans are tuned on the verification seeds; `author_wf.js` is stale against kernel
  v3 (0 mentions of camera, generation 2, schema 3, physics); the adoption round passes reports as
  `JSON.stringify(...).slice(0, 30000)` (stage A: 16,000 and 5,000) although the brief says truncation was fixed by
  files; 16-18 agents edit one working tree concurrently.
* **Why it matters.** The authoring wave is 91 % of the catalog. Every one of these gaps lets a weak or wrong spec,
  or a dropped hard entry, reach "verified" with no independent signal.
* **Smallest fix.** The orchestrator itself runs `--check` on every returned batch (and records the fingerprint) and
  rejects the batch on any mismatch with the agent's list; a fresh third agent audits a random 10-20 % of each batch
  with held-out gaming families and two extra seeds; a domain owner signs each "gap"; rewrite `author_wf.js` against
  TASK_SPEC schema 3 and the current camera default; pass every report as a file path; one git worktree per agent;
  one open-item ledger with status.

### A4 (Medium). State outside the journal changes outcomes after save/load

* **What is wrong.** Caches keyed by the `World` object or by `static_index` are not in the saved payload; when they
  are stale, a reloaded or replayed episode computes something the live one does not.
* **Evidence.** [ran] rows 13-15: 1 of 470 save points diverges (`T-50c78da60ad2a8-01`): the reloaded run writes
  `toaster_1._free_l` and `heat_load_w`, the live run never does, because food's `food.cold` and `food.checked` static indexes are keyed
  on `internal_temp_c`, which a toaster gains at run time [read `systems/food/systems.py:392-397, 922`,
  `kernel/world.py:467-476`]; running with those two indexes never cached reproduces the reloaded result exactly, so it
  is the uninterrupted (normal) run that is wrong [ran]. [ran] row 15: 10 static indexes in 6 domains go stale during reference runs (`electrical.markers` in 119 of 243 specs); only the two food indexes were shown to change a final state (row 15, `S/stale_effect.py`); no verdict changed. [ran] 790 module-level mutable objects, 79 mutated at run time,
  10 `World`-keyed memos (row 20).
* **Why it matters.** Replays start from saved states, savepoints are a stage-C feature, and the leaderboard re-scores
  by replay; a stale cache means two runs of "the same" episode can differ, and the bug class is invisible to
  determinism tests that always start from reset.
* **Smallest fix.** Make `static_index` enforce its contract (evaluate predicates on a copy of the record with only
  type-level fields, or let a predicate declare dynamic fields and key the cache on `mutation_count`); add the
  save/load and static-index checks of section 5 to the gate.

### A5 (Medium). Duplicated mechanics are real, and most of them cannot be switched off yet

* **Evidence.** [ran] rows 16-19 and section 2.3: 19 of 23 laws deferred; 0 law guards in the 10 unadopted domains
  (9 of them listed in some `duplicated_by`); the light formula in 4 places (agreeing today); the legacy thermal
  formula in 2; 14 modules in 11 domains plus the kernel append sound records in 2 schemas; the name census shows each domain carrying
  its own thermal, liquids, statics and wetness vocabulary (a noisy proxy).
* **Why it matters.** Until the laws flip, a "physics" claim in a spec is decided by whichever domain's copy runs, and
  the two can disagree (A1).
* **Smallest fix.** Flip laws one at a time with the global check of A1; move sound and light into kernel channels
  with one schema each; add a test that every law's legacy copy is guarded (`kernel_owns`) before the law can flip.

### A6 (Medium). Test brittleness

* **Evidence.** [ran] row 21, section 2.6: 25 wall-clock tests inside the correctness suite (2 seen flaking under load
  by Phase 2); about 2,280 whole-state digest pins, all of which changed together at the adoption checkpoint (Phase 2
  T6); 633 exact float equality assertions; 107 registry mutations in tests (Phase 2 found an isolation failure from
  one).
* **Why it matters.** The gate's G1 is absolute; flaky timing tests either block it or train people to ignore red;
  whole-state digests cannot tell intended from unintended changes, so the usual response is a re-record.
* **Smallest fix.** Move timing tests behind an opt-in benchmark flag with their own gate; pin goldens per change class
  (`golden.py diff` already groups paths) and re-record only reviewed classes; a fixture that snapshots and restores
  the plugin tables around every test that registers anything.

### A7 (Medium). Performance and replay size

* **Evidence.** [ran, loaded machine] row 22: 7.5 ms per substep (C1 3.17), quiet day 14.4 s (C1 6.45, target 3.5),
  realistic observation 9.4-11.6 ms vs oracle 1.8 ms. Row 23: replays 3.3-3.9 MB, three near-copies of a 1 MB house,
  gzip 14x; no compact format yet. [inferred] The G7 budget of 1.05x the previous gate cannot be meaningful on a
  shared desktop when load alone moves the numbers 2x.
* **Smallest fix.** gzip replays now (stdlib); then store generator version, seed and setup instead of `initial`,
  `start_state` and `baseline` copies (KB-scale); run G7 only on a quiet machine or with CPU-time instead of wall
  time.

### A8 (Medium-Low). One machine, one Python, no CI

* **Evidence.** [ran] row 26: no CI files. Rows 10-12: the code is statically 3.9-clean and the 3.12 `sum()` change
  is harmless, but dense libm differences change 18/243 final states including discrete visibility cells
  (`nav/seen`, coverage 0.736 -> 0.732), so replay digests and goldens recorded on Windows may not reproduce on Linux
  [inferred from ran]. Row 25: the CRLF catalog makes byte spans and the evidence manifest's `source_sha256`
  platform-dependent.
* **Smallest fix.** A CI matrix (3.9 and 3.14; Windows and Linux) running the unit suite, one seed of every spec and
  the golden check; round angles and distances before grid-threshold comparisons in `sight.py`/navigation coverage;
  `.gitattributes` with `catalog/raw/*.txt -text`.

### A9 (Low). Silent exception handling

* **Evidence.** [ran] 110 broad `except Exception` handlers, 51 that only pass, return or continue (26 in eval, 7 in
  the kernel, the rest in 9 domains), including cross-domain calls ("another domain's recognition must never break this
  view", `safety/security.py`) [read].
* **Fix.** Re-raise under `HOUSE_SIM_STRICT=1` and count swallowed exceptions in the episode stats.

### A10 (Low). Field sprawl without ownership

* **Evidence.** [ran] 1,287 distinct entity fields in one generated house; 1,738 prop names across 2,037 types;
  `temperature_c` written at 83 sites in 15 domains.
* **Fix.** A generated field registry (owner, unit, perception tier) with a test that every written field is
  registered; it also gives realism its tier table for free.

### A11 (Low). Import-time process-global modes; size claim

* [ran] `STRICT`, `SAFE`, `IDLE_PROBES` and `HOUSE_SIM_DOMAINS` are read once at import and the registries are process
  globals, so one process can hold only one domain set and mode (tests work around it with subprocesses). Import cost
  is small (0.6 s). Brief: "about 222,000 lines"; measured 216,794.

## 5. New checks (exact definitions)

1. **D1-GLOBAL (per law, before a flip).** For deferred law L: run `verify_spec` on every authored spec, seeds 0, 1,
   2, 41, 97, with only L forced active in every worker (`S/d1_preview.py run OUT --laws L`), plus every unit test
   under `lawbook.forced(L=True)`. L may flip only if no spec verdict changes and no test fails, or each change is
   listed in a reviewed file signed by the owner of the affected spec's domain. Would have caught: `stack` breaking 8
   specs in 4 unlisted domains (A1).
2. **SAVELOAD-EQ.** For every spec's reference on seed 41: run the command list uninterrupted (A); run it to step
   k = floor(n/3) and floor(2n/3), `save`, `HouseEnv.load`, finish (B). FAIL if any final state digest, observation
   digest, verdict or step message differs (`S/saveload_probe.py`, about 25 min single process). Caught:
   `T-50c78da60ad2a8-01` (A4).
3. **STATIC-INDEX.** After every step of every reference, recompute every static index the run used; FAIL if any
   differs from its cached value (`S/static_index_audit.py`). Caught: 10 stale indexes in 6 domains, two of which (food) change a final state (A4).
4. **TIE-ORDER.** Reverse the name tie-break of every plugin list (numeric order kept) and run every reference; FAIL if
   any final state, evaluation, or sorted observation digest changes (`S/tie_order_probe.py`). Caught: the order of
   `obs.sounds` in 9 specs (A2). Simpler alternative: refuse duplicate order values at registration.
5. **NUMERIC-ROBUSTNESS.** Run every reference with all `math` transcendental results nudged one ulp up; FAIL if any
   verdict changes, WARN listing every changed non-float leaf (bitmaps, ids, counts). Caught: `nav/seen` cells and
   coverage changing in 2 specs (A8); verdicts held.
6. **ORCHESTRATOR-VERIFY.** After each author/review pair, the workflow script (not an agent) runs
   `python -m house_sim --check tasks/authored/batch_NN.json --workers 2`, stores the output with the code
   fingerprint, and fails the batch if its passing set differs from the agent's `verified_passing`. Would have caught:
   the adoption agents' green claims made with a gate command that cannot run (Phase 2 T4).

## 6. What I could not verify

* Behaviour on Python 3.9-3.11, Linux or macOS (only 3.14.8 on Windows; no WSL installed). My 3.9 evidence is static
  plus arithmetic emulation (naive `sum`, ulp nudges of `math` functions; not `**`, not other C-library calls).
* Absolute performance: the machine was at 100 % CPU from another job; I report relative costs and flag absolute
  numbers as inflated. The G7 performance gate itself was not run.
* Whether the kernel's `stack` rule or the domain data is "right" for the bowl-on-scale and ironing-board cases in
  general: I showed the mechanism on two specs and the attribution on eight; a fix decision needs the owner.
* The process as it actually ran: I read the workflow scripts and the records in the tree; I have no transcripts of
  the agent runs and did not look at the live repository's git history.
* Unit-test line coverage (not measured; Phase 2 measured action coverage).

## 7. What is strong (do not touch)

* Domain isolation at the import level: zero static imports between domains [ran]; package scopes and result schemas
  in every workflow [read].
* Determinism engineering survives numeric stress: 3.9-style `sum`, sparse libm differences and reversed plugin tie
  order change no final state; save/load continues identically at 469 of 470 save points [ran].
* The lawbook design (data-driven states, `forced()` for previews, laws registered in their own table) made the D1
  preview a 100-line script [ran].
* No Python 3.9 hazards at all in 444 files, by a deliberately conservative style (no annotations, `.format`) [ran].
* `HouseEnv.save` / `load` carry the realism, mind and monitor state; the one divergence found is a cache bug in a
  domain, not in the engine [ran].
* Verdicts are robust to the physics flip except for the `stack` false positives: with all 19 deferred laws on, 233 of
  243 specs still pass although every end state changes, and the two other failures were predicted by the project's
  own readiness notes [ran].

## 8. Scripts I wrote (all in `S/`)

| Script | Purpose |
|---|---|
| `env.sh` | fake home, network forbidden, keys removed, cwd = tree; sourced before every command |
| `loc.py` | lines per group, kernel module, file and function |
| `registries.py` | registry and hook census after `load_domains()`; cross-domain wraps; dead hooks |
| `tie_order_probe.py`, `ties_obsdiff.py` | reverse plugin tie order; run all references; show which observation fields change |
| `d1_preview.py`, `d1_perlaw.py`, `stack_law_probe.py` | verify all specs with deferred laws forced active (in every worker); attribute breakages to single laws; trace the `stack` mechanism |
| `py39_deep.py`, `py39_selftest.py` | PEP 701 f-strings, runtime unions, forward-referenced annotations, 3.10+ APIs (with planted self-test) |
| `perturb_run.py`, `ulp_statediff.py` | 3.9-style `sum`, dense and sparse one-ulp libm nudges; leaf diffs of the final state |
| `saveload_probe.py`, `saveload_diff.py` | save/load equivalence at 1/3 and 2/3 of every reference; leaf diff of a divergence |
| `static_index_audit.py`, `stale_effect.py` | recompute every static index after every step of every reference; report stale ones; rerun a spec with chosen indexes never cached and diff the end state |
| `hook_profile.py` | share of step time in systems, hooks and before/after handlers |
| `d1_failing_detail.py` | domain and first failure reason of each failing spec in a D1-preview output |
| `light_diff.py` | differential test of the four light-level copies |
| `dup_mechanics.py` | duplicated-mechanics proxy census |
| `global_state.py` | module-level mutable state, caches, import-time statements |
| `test_brittleness.py` | digest pins, prose pins, float pins, wall-clock reads, registry mutations in tests |

Outputs: `hook_profile.txt`, `d1_ready.jsonl`, `d1_ready_compare.txt`, `d1_perlaw.txt`, `d1_all.jsonl`, `d1_all_compare.txt`,
`perturb_*.jsonl/.log`, `ties_all.jsonl`, `saveload.jsonl/.log`, `static_index_audit.json/.txt`, `light_diff.txt`,
`bench_geom.txt`, `bench_room.txt`, `bench_day.txt`, `py39_deep.txt`, `loc.json`, `registries.json`,
`dup_mechanics.json/.txt`, `global_state.json/.txt`, `test_brittleness.json/.txt`.
