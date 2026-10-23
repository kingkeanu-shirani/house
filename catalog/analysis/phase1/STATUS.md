# Phase 1 (stabilise) -- integration status, 2026-10-22

Integrator: INT. Inputs: the 19 phase-1 results in this folder (16 domains, K-LEAK, K-FIX, K-GATE). Full result:
`INT.json`; domain-side work: `breakages.json` (29 rows); evidence: `INT_evidence/`.

## Numbers (integrated tree)

| Check | Result |
|---|---|
| Unit suite `green_tests.py 5`, normal + `HOUSE_SIM_STRICT=1` | 4,643 tests per mode, 64 files, 0 failures (phase-1 tree before integration: 4,622 in 63 files, 4 failing runs -- the two pending re-records) |
| All authored specs, own mode (combined file, `--check --workers 4`) | 242 / 243 (before integration also 242; K-GATE's mid-phase count was 238) |
| The 51 realistic specs with `--camera geometric` | 51 / 51 |
| `perception_audit.py 60 80` | room: 0 leaks, 0 crashes; geometric: 0 leaks, 0 crashes; `--gen 2` 30 x 80: 0 / 0 |
| `layout_audit.py 30` | gen 1: 180 houses, 0 problems; gen 2: 330 houses, 0 problems, 4 domain follow-ups (breakages) |
| `fuzz.py 12 300` | 0 defects, 0 nondeterministic replays (45 of 427 actions succeed); `--gen 2` 8 x 300: 0 / 0 |
| `fuzz.py --coverage` | 286 of 427 actions driven to success (K-GATE: 283), 0 nondeterministic, 1 strict-mode defect (admin_inventory's close-look heft grant: breakages) |
| Gate record `record --id P1 --package P1 --prev ADOPT` | see below |

The only failing spec is T-7d4006bda247ba-01 (seeds 0 and 41, 'damage'): manipulation's legacy contact-heat block
does not ask `phys.thermal.contact_claimed` (proved by three domains; first row of breakages.json).

## What the integrator changed (each with a regression test; INT.json lists them)

* Kernel requests applied: action trail names true ids in realistic episodes; `camera.capture_key` public; kernel
  liquid holders follow plumbing's liquid-tightness rule (open weave and perforated bodies drain through; soft-sided,
  open shelving and side openings do not hold); core `go_room` reaches a dark doorway's switch from a safe free cell
  and descends winders carefully without hand, rail or light; the verifier counts the reset observation as observed;
  `ctx.fixed_rng` follows the episode nonce; a requester's own private thing is no privacy violation; the physics
  inspector keeps a domain's own `looks`; realistic JSON labels are not id-translated for plain words; core place
  judges a waste stream only when the robot can tell it; kernel wind stops blown things before doorways and furniture.
* Review L3 closed: hold power and walking work follow the load word (felt load), so the battery no longer weighs
  what the robot holds (K-LEAK's test was an expected failure that failed for the wrong reason; rewritten).
* Review H8 harness half: every agent episode gets a secret nonce (harness version 3); public logs keep it for
  replays, a persistent response cache keeps reruns free, test-split nonces come from the split key and are never
  logged. `eval/splits.py` now uses the hashed variant rule (it picked held-out phrasings of the wrong variant).
* Tools: `--gen 2` for the perception auditor and the fuzzer; the auditor's `looks` rule; `test_physics_fluids`
  isolated from domain hooks; gate package P1-INT so the P1 record's scope check covers K-FIX, K-LEAK and INT files.
* Re-records after a three-way golden dump (HEAD / phase-1 tree / integrated): the integrator's own changes alter 7
  path patterns (action_log targets; robot battery and energy); every other difference is a class an agent reported
  (K-LEAK address, handles and hashed variants; domain type data and bookkeeping; re-authored references; K-FIX key
  rings). `tests/data/room_golden.json`, `tests/data/gen1_digests.json` re-recorded `--by INT`; `tasks/specs.jsonl`
  recompiled (9 edited specs). Docs updated (KERNEL_API, GENERATOR, PHYSICS, PERCEPTION, EVAL_HARNESS, SPLITS,
  TASK_SPEC, AUTHORING_GUIDE, TIER1_EMBODIMENT note). `tasks/templates/.gitkeep`, `house_sim/data/actions/.gitkeep`.

## Gate record

`python tools/orchestration/tier1_gate.py record --id P1 --package P1 --prev ADOPT` (record in
`catalog/analysis/tier1/gate/P1/`): **FAIL** (7 pass, 5 fail, 1 n/a) -- as expected before the main session signs:

| Check | Result |
|---|---|
| G0 | PASS: action fingerprint unchanged (427 actions) |
| G1 / G1b / G8 | PASS: 4,643 tests x normal + strict; isolated domains 16/16, kernel files with `HOUSE_SIM_DOMAINS=core` 32/32; golden and golden-all green |
| G2 | FAIL: own mode 242/243 (G2-ABS: T-7d4006bda247ba-01 seeds 0, 41); 199 verdict changes vs ADOPT unreviewed and unsigned; 9 edited specs unsigned |
| G3 | FAIL: forced realistic 241/243 (also T-dda4e199f3f5b0-01 seed 1, the pets urgency judgement awaiting typed reports); 207 verdict changes unsigned |
| G4 | PASS: perception audit 0 leaks, 0 crashes (room and geometric) |
| G5 | PASS: layout 240 houses 0 problems; gen-1 digests 60/60 |
| G6 | FAIL: one strict-mode defect in the coverage fuzz (admin_inventory's close-look heft grant); 0 nondeterministic replays; 287 of 427 actions succeed over all variants |
| G7 | FAIL: 3.85 ms/substep and a 1.255 s hour against ADOPT's 3.21 / 1.133 (limit 1.05 x). Same-day quiet bench: HEAD 3.26, phase-1 tree 3.81, integrated 3.89; the increase is the domains' (breakages: outdoor_plants +0.19, safety +0.07, tools_vehicles +0.06, pets +0.05, ...), the integrator's +0.07 is a GC pass |
| G9 | FAIL only through G6 (the camera extra requires G4 and G6 to pass); physics, body, sweep, gen-2 timing, gen-2 layout, generator compat all ok |
| G10 | PASS: 311 changed files, 0 outside P1-ALL (with the new P1-INT package) |

The 406 verdict rows vs ADOPT (199 own + 207 realistic): 392 change only the wrong-plan list -- 389 in trials that
pass before and after, so every wrong plan still fails (K-LEAK's hashed variants run other variants on some
verification seeds; re-authored wrong plans), 3 in trials failing on both sides; 14 are FAIL -> PASS fixes of this
phase (T-d1f9385dbb80fc-01 s41, T-28ff695081cd2c-01, T-79a861877a2a67-01,
T-c072906a11f55c-01, T-dda4e199f3f5b0-01 s0/s97, X-fee35ef3aac659-01, T-7d4006bda247ba-01 seed 2);
none goes PASS -> FAIL. `catalog/analysis/phase1/P1_signoff_draft.json` drafts the
second signatures (406 verdict + 9 edit rows) for the main session to review and commit with
`tools/orchestration/gate_policy/`.

## Open items

* Domain side: `breakages.json` -- manipulation (T-7d4006bda247ba-01; stack_reason on the kernel's tops; wind lift at
  the item), admin_inventory (strict-mode heft grant; open-tray holder flags), plumbing_liquids (closed-sprayer pour,
  wetting through the kernel, food as a 'bin', garbage disposal flag, shower stall hiding mounts), mechanisms (one
  door-dash owner, escape attribution through pets), food (legacy heat mark), textiles_laundry (unpowered heater,
  hamper flag), cleaning and safety (novel-thing inspect extras), safety (chemical presets, fire attribution), people
  (Rx label, fall attribution), appliances_cooking / pets / outdoor_plants / tools_vehicles (holder flags),
  navigation_robot (gen-2 dock keeps targets reachable); outdoor_plants, safety, tools_vehicles and pets (their share of
  the G7 substep-cost increase).
* Kernel, deferred with reasons (INT.json not_fixed): look-alike puddle hazard kinds (needs domain hooks too); camera
  items for D3 (bin look-inside gate, floor-hazard memory and re-planning, wrist depth under a lifted cover, device
  cameras through doorways); the novel-thing operation rule (owner decision); one harm API (the next phase's ledger);
  capability registry; judgement fields in perceivability (V-EXPECT); look-alike lint extensions; gen-2 sofa cushions
  (GEN follow-up); core place's protected-discard refusal reveals never-tier flags in realistic mode (found here;
  needs the ledger's lost-item rule).
* Main session: commit `tools/orchestration/gate_policy/` and phase 1; review the drafted signatures; push for the
  first CI run (Linux / Python 3.9 runtime unverified here). Laws reported ready by their owners: slope and setdown
  (manipulation), stack (textiles_laundry, food; manipulation's stack_reason still open), wind / rain / slope
  (outdoor_plants), rain (admin_inventory, already in duplicated_by).
