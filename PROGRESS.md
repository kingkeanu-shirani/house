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

## Milestone 4 - tier-1 foundations, adoption round and independent review (2026-10-13 .. 10-22)

Done:
- Kernel round 5 (v2.9) and the tier-1 foundations, each stage gated and reviewed: stage A libraries (physics
  data/statics/fluids, camera geometry, robot body model, generator gen 2, gate tool), stage B integration (physics
  settle and laws, geometric camera, robot energy/limits/faults/proprioception), stage C sweep (interruptible waits,
  standing orders, temporal evaluation, multi-phase episodes, dialogue, report vocabulary, robot memory). Designs and
  plans: docs/design/TIER1_*.md; package handoffs: catalog/analysis/tier1/handoff_*.md.
- Adoption round: all 16 domains adopted physics, camera, body and sweep; two kernel follow-up agents (camera
  apertures/watch, eval/viewer/compact replays/gate fixes); the integrator applied 46 kernel requests and wrote
  catalog/analysis/tier1/adopt_status.json (law readiness, per-domain spec counts, 24 domain breakages).
- Independent review (docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md, phase records alongside): verified
  specs are NOT yet gaming-resistant (cheat policies passed 39 of 44 attacked specs; 10 of 22 conditional specs pass a
  constant policy), 13+ perception leak classes reproduce while the auditor reports 0 (wait-until oracle, seed in the
  street address, positional tells, remote count, stable handles...), 3 real defects behind "stale" test pins, the
  switch-overs (D1-D3) break more specs than planned, and the authoring workflow must not trust agent self-reports.
- docs/design/VERIFY_HARDENING.md: the verifier anti-gaming design (default whole-house protections with mess flow
  and attribution, an automatic gaming battery plus a must-pass battery, randomised answers, speech/report content
  scoring, wrong-plan expect labels, evaluation-mode verification, held-out and private seeds), critiqued (21 holes,
  all addressed).

State at the end (2026-10-22, 11:58 UTC): 4,325 unit tests, 4 failing (test_people helper capacity and
test_physics_statics wrapped-glass break, normal + strict - both known domain breakages); 241/243 authored specs pass
in their own mode (T-d1f9385dbb80fc-01 seed 41: robot falls on a toy in the gen-2 house; T-7d4006bda247ba-01:
ironing-board contact heat); D3 preview (forced realistic + geometric camera) 225/243; perception/layout/fuzz audits
report 0 (see the review for what they cannot see). No paid API call was ever made; no split key exists.

Open items: the 24 domain breakages (adopt_status.json); the review's findings (REVIEW_REPORT.md section 1); the
three real defects it named (one-hand drop near the lift limit, wrapped-glass shards, privacy double count - check
they are fixed, not baked into baselines); evidence/demos/datasets/leaderboard regeneration at the next round close.

Next steps, in order (the review's sequence, adopted):
1. Keep mass authoring frozen. Fix the 24 breakages and the 3 real defects; attribute the gen-1 digest change.
2. Harden the gate and the workflows (absolute pass rules, wrong-plan labels in records, orchestrator-run checks,
   file handoffs, CI matrix 3.9/3.14).
3. Implement VERIFY_HARDENING tier 1 (expect labels, held-out seeds, variant cross-checks, seed coverage, speech
   content, lints), then the cheap perception-leak fixes, then tier 2 (constant/ask policies, mess conservation,
   displacement, answers, chance), with the exit test "catches every Phase-2 gaming policy".
4. One re-baseline event for D1 (laws on, per-law global preview), D2 (gen 2 default) and D3 (geometric camera
   default, triage of the failing specs), with one reviewed golden re-record.
5. Re-harden the 243 specs under the new verifier as the authoring-process pilot.
6. LLM pilot (estimate about $110-279, only with the owner's explicit approval).
7. Author a core of about 500 entries (programmatically chosen, all sections and safety-critical entries), release
   core-500 v1, then decide on the long tail from measured audit and pilot results.
