# Progress

Started UTC: 2026-10-07T07:33:27.074933+00:00

- Source files preserved; current Python environment reviewed.
- Catalog and evaluation architecture in progress.

## Core milestone

- Preserved and indexed 2,719 entries across 105 sections.
- Built 19 symbolic scenario families and 53 guarded action names.
- Added interactive/headless CLIs, observation-only search baseline, limited language
  grammar, permissions, events, appliance cycles, undo, snapshots and replay.
- Initial catalog sweep found two nested-light transition errors; both repaired.
- Adversarial tests found label/ID ambiguity; natural language now asks rather than
  preferring an object whose ID happens to equal the shared label.
- Final verification, docs and packaging remain. No five-day completion is claimed.
- Checkpoint UTC: 2026-10-07T13:53:20.515155+00:00

## Verification and packaging milestone

- 35 regression tests pass, covering all 19 families and adversarial behavior.
- Tested optional offline wheel build/install and six CLI/export/save/replay paths.
- Tightened undo guards, private snapshot diffs, repeated-question scoring, carried
  liquid mass, fixed-surface cleaning and reward accounting.
- Each mapped entry is being rechecked on five seeds against the final source hash.
- Full-catalog implementation is still partial; remaining requirements are retained.
- Checkpoint UTC: 2026-10-07T14:09:19.489882+00:00

## Delivered prototype milestone

- Final source verification: 723 / 723 implemented entries pass
  on all five seeds (3615 trials), each with negative baselines.
- Regression tests: 35 passed, zero failures/errors.
- Section demos: 75 / 105, all saved and replay-verified.
- Coverage: {'DONE': 723, 'TODO': 1995, 'BLOCKED': 1}; all implemented fidelity is S.
- Original source bytes verified unchanged; all entries retained.
- Known gaps and next steps recorded in FINAL_REPORT.md and docs/KNOWN_LIMITS.md.
- Report checkpoint UTC: 2026-10-07T14:16:23.321734+00:00.

# v2 rebuild (realistic household simulation, full catalog)

Started UTC: 2026-10-09T08:21:08Z (user request: "a real house slammed into code", all catalog tasks, up to 5 days).

## Milestone 0 — foundation (in progress)

- Backed up v0.3.0 to archive/v0.3.0_baseline.zip; original catalog preserved as catalog/raw/household_catalog.v1.txt.
- Added the user's new adaptability entry "Find me my prescribed medicines." (Emergencies) -> 2,720 entries.
- New kernel: journaled world state (exact rollback, strict mutation checks), type registry, action/hook/system
  plugin registries, simulated calendar time and daylight, room occupancy grids with A* paths, reach heights,
  crouching, doorway switches, lighting/darkness, perception redaction, lumped thermal model, programmable
  appliance cycles, installable part slots, worn protective gear, evaluation with side effects, milestones,
  ordering, deadlines, rubrics and question scoring, persistence and replay.
- Procedural generator: apartments, townhouses and 1-3 floor houses with basements, attics, garages, sheds, pools,
  yards, gardens, doors (knob/lever/sliding/garage/gates), windows, lights, switches, residents and pets.
- Data-only task specs with setup ops, variants for conditional entries, reference/wrong plans; multi-process verifier.
- Catalog analysis workflow interpreting all 2,720 entries (catalog/analysis/).
- 24 kernel/catalog tests pass on Windows, Python 3.14.8.

## Milestone 1 — mechanics domains (2026-10-09)

- Catalog analysis: 2,720/2,720 entries interpreted into testable records (catalog/analysis/, split by owning
  domain in catalog/analysis/by_domain/). Owned entries: cleaning 515, manipulation 377, admin_inventory 341,
  textiles_laundry 237, outdoor_plants 208, food 180, mechanisms 128, electrical 113, navigation_robot 105,
  people 96, tools_vehicles 90, appliances_cooking 87, safety 79, plumbing_liquids 70, pets 59, climate 35.
- 16 domains built in parallel (house_sim/systems/<domain>/, ~133k lines; 2,013 object types; 400+ actions;
  91 time systems), each with unit tests (~1,700 total, passing in isolation incl. strict journaling mode),
  a reference page (docs/domains/) and 12-13 verified example specs (193 total).
- Kernel v2.1 from the builders' requests: grip memory, container visibility by relation, cover/submerged/agent
  concealment, visibility/path/episode-start hooks, static entity index and periodic systems for speed, extra
  global harm flags, action trail, reachable floor placement, multi-way switches, generator accessibility checks.
- In progress: integration round (all domains loaded together) + performance optimization.

## Milestone 2 — integration, fix rounds and layout guarantees (2026-10-09)

- Integration round: all 16 domains load together; each domain's tests and examples pass with everything loaded.
- Fix round 1 (16 owners, ~63 cross-domain bugs): 1,903 domain tests pass (normal and strict); 193/193 examples
  pass on the 5 verification seeds. Engine cost for a 1 h wait: 9.5 s -> 0.24 s.
- Kernel v2.3 (layout and routing): shared door-swing model; access/chokepoint analysis (4-connected components,
  cut cells, "not worse" placement rule); the robot dock is placed first and is always leaveable; final layout
  repair pass (loose items re-placed so they cut no hazard-free route; agents never in doorways or chokepoints);
  strict door clearance for tall items; setup ops fail loudly instead of dropping things on cell [0, 0];
  invariants with a defaulted `world` parameter get the World; routing ignores the target's own hazard rings,
  avoids locked doors and passes partly-open doors carefully. tools/layout_audit.py attributes remaining layout
  problems to furnishers: 1,688 problems in 30 houses before -> 715 in 180 houses after, all domain-owned.
- Next: fix round 2 (domains adopt v2.3, clear their audit lines), then authoring of the remaining catalog entries.

## Milestone 3 — perception, layout v2.5 and evaluation suite (2026-10-09/10)

- Fix round 2 (16 owners): all domain tests (normal + strict) and 193/193 examples green; layout audit clean of
  domain-owned lines except navigation false positives (since fixed in the audit).
- Realistic perception (kernel/realism.py, docs/PERCEPTION.md): opaque salted handles with central translation,
  field tiers and knowledge outside world state, seeded buckets/noise with degradation, small-object misses,
  look-alike groups, novel objects, mirrors, mood cues; perception-aware verifier (perceivability check) and
  `--perception realistic`; leak auditor (tools/perception_audit.py); SearchAgent works in both modes.
- Kernel v2.5 layout requests (floor-standing mounts block paths, operable fixtures protected, hidden/overlapping
  wall mounts prevented and audited, keep-out rings, 4-connected agents, strict furniture, smarter repair).
- Running: perception domain sweep (tiers, look-alikes, measuring tools, observers, perception-centric specs) and
  the evaluation suite build (hidden test split, agent harness + LLM providers, leaderboard, dataset exporter,
  replay viewer) with integration, red team and fixes. No network calls are made by any of it.

## Paused (2026-10-10, 10:35 UTC) — before the authoring wave

Done since Milestone 3:
- Perception domain sweep, red team (kernel v2.8 hardening) and domain fix round 4 (16 owners): every domain's
  perception leaks, cross-domain bugs and known test/spec failures handled. Each domain reports its tests green
  (normal + strict), its example/perception specs 100% on the 5 seeds, 0 perception-audit and 0 layout-audit lines.
- Evaluation suite (hidden test split, agent harness + LLM providers, leaderboard, dataset exporter, replay viewer):
  red-team round fixed (20/20 original repros now rejected or labelled; independent acceptance re-check passed).
- Full check at pause: **2,570 unit tests pass** (normal and HOUSE_SIM_STRICT=1); **242/242 authored specs pass**
  `--check` on seeds 0,1,2,41,97 (each in its own perception mode; 49 are `"perception": "realistic"`); duplicate
  A-c83efc7adcd8f5-01 resolved (only in people__perception.json). No network or paid API calls were ever made; no
  split key has been created (%USERPROFILE%\.house_ai does not exist).
- Authoring prepared, not launched: `python tools/make_batches.py 45 30` -> 52 batches / 2,478 entries in
  catalog/analysis/batches/; prompt finalized in tools/orchestration/author_wf.js.

Open items:
- 53 kernel requests and 55 cross-domain bug reports from round 4: catalog/analysis/round4_results.json
  (kernel_requests, new_cross_domain_bugs, not_fixed per domain). Not yet triaged.
- Eval residuals (low): `--allow-incomplete` completeness is count-only (run.json does not list skipped
  episodes); the dataset validator's perception-tier downgrade is global, not per field; the viewer does not
  cross-check a replay's start state against a deliberately rewritten seed.
- Stale generated files: the leaderboard (moved to archive/stale_leaderboard_2026-10-09/, regenerate with
  tools/orchestration/regen_leaderboard.sh); reports/COVERAGE.md, coverage_summary.json and verification.jsonl
  (2026-10-07, regenerate with --verify/--report); the search baseline scores 0% (toy instruction parser); replays
  are ~2.5 MB per episode; the LLM system prompt is ~31k tokens (compact the action catalog before paid runs).
- User actions: `--init-split-key` and `--build-test-manifest` (then back up %USERPROFILE%\.house_ai); a human-written
  private paraphrase pool for the test split.
- Tier 0 acceptance report (time, perf numbers, leak counts, tagged entries) and the eval deliverable summary with
  the dry-run cost estimate are still to be given to the user. Ask before any paid run.
- Layout: 3 residual problems in 36 houses (wide window shades in small bedrooms).

Next steps, in order:
1. Triage and apply the round-4 kernel requests; route the 55 cross-domain bugs (fix round 5, one agent per domain).
2. Full green check: tools/orchestration/green_tests.py; all authored specs; perception_audit 100 houses;
   layout_audit; fuzz with determinism. Archive a snapshot.
3. Regenerate the leaderboard and the coverage report.
4. Authoring pilot (6-10 diverse batches) with tools/orchestration/author_wf.js, then the remaining batches, gap-fix
   rounds (gapfix_wf.js) and re-authoring until dry.
5. Final verification of all 2,720 entries, docs rewrite, packaging, final report.
