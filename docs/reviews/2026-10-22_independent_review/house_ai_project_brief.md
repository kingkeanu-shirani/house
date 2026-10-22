# house_ai: project brief for an independent AI reviewer

This brief describes the **house_ai** project as of 2026-10-20, for another AI model asked to review it. It covers:

- what the project is for;
- how it is built and how work is organised;
- where it stands;
- what is weak or risky;
- the questions the owner wants answered.

Numbers come from the repository unless marked as estimates.

---

## 1. Purpose and goal

**house_ai** is a household simulator written in pure Python (standard library only). It is meant as a benchmark and training environment for household robot agents: humanoid-style robots that tidy, clean, cook, do laundry, look after people and pets, keep the home safe, run errands around the house, and so on.

The owner's standing goal, in their words: *"a real house slammed into code with no flaws"*. They have said there are "no limitations on changes".

The concrete target is a catalog of **2,720 household tasks** (`catalog/entries.jsonl`), grouped into 105 sections such as "Kitchen: dishes", "Laundry", "Pet care", "Home security routines" and "Garage". Every entry must become an honest, **verified task spec** that a learning agent can be scored on.

| Kind | Count | What it is |
|---|---|---|
| task | 2,240 | Concrete chores, e.g. "Clean the coffee maker", "Fold towels" |
| adaptability | 253 | Vague or judgment commands, e.g. "Make this place look nice", "Find something heavy enough to hold this down" |
| primitive | 108 | Action vocabulary, e.g. Identify, Count, Verify task completion, Ask for clarification |
| variation | 74 | Randomisation axes every task must handle: door-handle types, floor materials, children present, dirty camera, low battery |
| appendix | 45 | Compound paragraphs whose every clause must be exercised |

Each entry has a hand-written **analysis record** (`catalog/analysis/part_*.jsonl`) containing:

- the interpretation in its section's context;
- the objects and mechanics needed, with about 9,500 distinct mechanics tags;
- the success condition;
- plausible wrong behaviours;
- what must be protected;
- the target fidelity, risk level and approval needs;
- the user-simulator needs.

### What "verified" means

A spec counts as done only when all of these hold on the 5 fixed seeds 0, 1, 2, 41 and 97:

- the initial state fails;
- waiting out the whole time budget (idle) fails;
- the reference plan passes strictly, with no refused step;
- each of 1–3 *plausible* wrong plans fails, and for the stated reason.

Conditional entries ("if needed", "when appropriate") must vary across seeds, so that always-do and never-do policies both fail some seed. Each spec also carries an honest fidelity label:

- **S:** simulated mechanics decide it;
- **G:** geometric or graph-level decision;
- **P-approx:** a deliberately approximate proxy.

### Owner's constraints

- Pure stdlib, Python 3.9-compatible, deterministic and replayable, Windows-friendly.
- **No paid LLM API calls, and no data sent off the machine, without asking first with a cost estimate.**
- Never fake mechanics or overstate coverage. If a mechanic is missing, report it as a gap rather than writing a hollow spec.
- The hidden test-split key lives only in `%USERPROFILE%\.house_ai\`, never in the repo. It has not been created yet, and the owner wants to be told when it is, so they can back it up.
- The owner has given "free rein" on approach and token usage, with quality favoured over speed.

---

## 2. Size and shape

| Item | Value |
|---|---|
| Simulator code (`house_sim/`, excluding legacy) | ≈ 222,000 lines of Python |
| Tests | 55 files, ≈ 74,000 lines; about 3,985 tests at the last full run, each run normal and strict |
| Registered actions (verbs) | 427 |
| Object types | 2,042, across 17 type files (`house_sim/data/types/*.json`) |
| Mechanics domains | 16 |
| Authored, verified specs | 243 in 32 files (51 use realistic perception) |
| Catalog entries still to author | 2,478, prepared as 52 batches in `catalog/analysis/batches/` |
| Repository | git; GitHub `kingkeanu-shirani/house`. `main` on GitHub is the initial import; branch `tier1-foundations` holds the newer verified work |

---

## 3. Architecture

### 3.1 Kernel (`house_sim/kernel/`, owned by the "main session")

- **World** is a journaled state: every mutation is tracked, so undo, replay and strict-mode detection of untracked writes all work. Its parts are:
  - rooms with occupancy grids at 0.5 m cells;
  - entities with containment (`location` plus `relation` in/on/part);
  - people, pets and the robot.
- **Plugin registries:** actions (ActionSpec, with parameter specs, duration, energy and timing), types, hooks, systems, observers, inspectors and invariants. Domains extend the kernel only through these.
- **Engine** (`engine.py`):
  - `HouseEnv.step(action, *args)` validates, runs the action, then advances time;
  - systems run in time-advance substeps of up to 30 s;
  - after the advance comes settling, then end-of-step bookkeeping (perception grants, camera identity tracking, mind update);
  - it also handles save, load and replay.
- **Evaluation and predicates:**
  - a declarative predicate language: paths, comparison ops, forall/exists quantifiers, report predicates and more;
  - success sets, milestones with order and deadlines, rubrics for subjective entries, side effects (`no_moves_except`, `no_damage`, `unchanged_fields`), and question scoring;
  - global harm flags (damage, injury, spills, fire, flood, privacy, authority and safety violations) fail any task;
  - temporal conditions (`never`, `always`, `responses`) are checked over the whole episode.
- **Geometry:**
  - access targets, reach radius by mount height, and door swing models;
  - 4-connected components and articulation cells (chokepoints);
  - a rule that placement never makes access worse, plus route planning that respects hazards.
- **Clock:** a calendar clock with daylight, holidays and DST.

### 3.2 Realistic perception (`kernel/realism.py`)

There are two modes:

- **oracle:** used for verification, and the default.
- **realistic:** what agents are evaluated in.

In realistic mode the agent sees what a camera-and-sensors robot could plausibly perceive:

- **Opaque handles:** entity ids are replaced by salted handles. True ids typed by the agent are refused, and outbound messages are translated.
- **Field tiers:** `glance`, `inspect`, `read`, `measure` and `never`. Knowledge is tracked per field, outside world state, and is gained only through real actions: approach plus inspect, read (needs light and a clean lens), a measuring tool, or ask.
- **Coarse values:** quantised buckets with fixed-draw noise (no averaging by repeated looks), degraded by distance, darkness, glare, a dirty lens, motion and steam.
- **Look-alikes and novel objects:** look-alike groups (unread spray bottles, pill bottles and jugs look and behave identically until identified), novel "unfamiliar" objects, and mirrors.
- **No side channels:** refusal wording is scrubbed (permission refusals collapse to one category), and time is rounded to whole seconds.

An automated leak auditor (`tools/perception_audit.py`) runs random agents through 100 houses and checks every observation and message for:

- true ids;
- underscore or secret fields;
- `never`-tier values;
- values above the robot's knowledge level;
- hazard sources the robot cannot perceive.

The latest runs report **0 leaks**. Several red-team rounds found and closed dozens of subtler oracles: rewritten look-alike labels, ids of destroyed entities, JSON key order revealing true-id sort order, refusals naming hidden hazards, and so on.

### 3.3 House generator (`house_sim/generator/`)

- **What it generates:** procedural houses, apartments and townhouses, with floors, garage, patio, yard and driveway. Each domain contributes its own furnishers.
- **Repair and audit:** a final repair pass guarantees the robot dock is reachable, no doorway or chokepoint is blocked, and no wall mount is hidden. `tools/layout_audit.py` attributes any problem to the furnisher that caused it; the latest runs show 0 problems in 180 houses.
- **Generation 2** (`gen >= 2`, opt-in today):
  - household composition from data: infants, teens, roommates, elderly residents, and more pet species;
  - resident profiles and habits;
  - personal belongings (phones, wallets, keys, jackets, umbrellas), common furnishings and smart-home tiers;
  - climate normals per region and season.

  Generation 1 output is kept byte-identical by golden digests.

### 3.4 Mechanics domains (`house_sim/systems/<domain>/`)

There are 16 domains, each with its own actions, skills, types, systems, scenario helpers (setup ops), tests, docs and example specs:

| Domain | Covers |
|---|---|
| plumbing_liquids | water, flow, drains, leaks |
| cleaning | soil model per surface and cell, tools, chemistry, waste streams |
| safety | fire, smoke, broken glass, puddles, alarms, hazards |
| navigation_robot | paths, stairs, docking, battery bookkeeping |
| appliances_cooking | appliance cycles, cooking doneness, stoves, ovens |
| admin_inventory | storage, inventory, mail, curbside |
| electrical | outlets, breakers, lights, devices, smart home |
| climate | heating and cooling, humidity, windows, air quality |
| mechanisms | doors, locks, drawers, coverings, controls |
| tools_vehicles | tools, garage, car-adjacent tasks |
| textiles_laundry | washers and dryers, fabric care, folding, bedding |
| people | residents and visitors: schedules, sleep, permissions and consent, the user simulator |
| food | spoilage, portions, storage, prep |
| manipulation | grasping, carrying, stacking, wrapping, handover |
| pets | behaviour, feeding, medication, cleanup |
| outdoor_plants | garden, lawn, weather owner, wind |

`house_sim/systems/core.py` is the shared action layer, owned by the main session. It holds approach, pick_up, place, open, inspect, read, ask, report, wait, watch and similar actions.

### 3.5 Tier-1 foundations (added 2026-10-13 to 10-15)

Before mass authoring, the owner asked for foundation layers covering "basic physics, camera stuff and whatever else is necessary". The work was designed in parallel (two designers per layer plus a judge), reconciled into one integration plan, then implemented in gated stages.

The specs live in `docs/design/TIER1_{PHYSICS,CAMERA,EMBODIMENT,SWEEP,INTEGRATION,DECISIONS}.md`, about 1,000–1,400 lines each.

**Physics** (`kernel/phys/*`, `data/physics/*.json`)

- **Data tables:** materials (with aliases for every material string used by types), liquids, floors and supports, each with sourced real-world anchors.
- **Property resolution:** per-instance deterministic variation of about ±15 %, hash-derived and stateless.
- **Support graph and statics:** loads, overload and collapse, crush, edge overhang, stacks, furniture tipping.
- **Impact and breakage:** includes a soft-landing model, so mugs survive 1.5 m onto carpet while eggs break on hard floors.
- **Forces:** push, drag, friction, floor scratching, lift plans.
- **Containment:** diagonal fit, nesting, compressible items, the CPSC small-parts cylinder.
- **Liquids:** pour, overflow, spill, absorption with volume caps, drip.
- **Thermal:** a calibrated lumped time constant, contact heat (a hot mug leaves a heat ring on finished wood but does not melt plastic), thermal shock, freeze and thaw with latent heat, burns.
- **Weather on objects:** wind with height profile and gusts, and rain.
- **Laws:** each law is listed in `laws.json` and lands *deferred* until the domains that duplicate it adopt it.
- **Harm attribution:** robot-caused consequences fail tasks; exogenous ones (pets, scripted events) do not.
- **Settling:** runs after each action and each substep, driven by a touch log.

**Geometric camera** (`kernel/sight.py`, `kernel/camera.py`)

- Robot heading plus head pan and tilt; eye height of 1.50 m standing, varying by posture.
- Field of view, and an interval viewshed over the 0.5 m grid that uses occluder height spans. This covers things behind sofas, under beds, on 2 m shelves and inside open or closed containers.
- Light field with beams, glare and a headlamp.
- Acuity from pixels on target, which caps identification and reading.
- New actions `face`, `peek` and an extended `look`.
- A `camera_log` the evaluator can check: coverage, what was looked into, sightings.
- Privacy captures, so filming in bathrooms or bedrooms counts as a violation.
- A lidar ring.
- Opt-in per spec today. The owner decided it becomes the **default for realistic perception** after the domains migrate; oracle verification stays in "room mode", which is byte-identical, enforced by a golden test.

**Robot body** (`kernel/body.py`, `body_faults.py`, `proprio.py`, `data/robot/profiles.json`)

- A humanoid profile with a 2.4 kWh battery and energy charged for every action, including nested ones. Walking costs 0.288 Wh/m; parked standby is 20 W.
- Charging and docking.
- **Power-down is a consequence, not a refusal.** A walk cut short returns ok with a "powered down" message, while ok=False keeps meaning "rolled back".
- Payload, lift and grasp limits with derates (posture, overhead lifts, limping), and pinch versus power grips.
- Precision limits.
- A careful-speed mode (a double-slowdown bug was fixed).
- One sensor, privacy and hearing model: privacy mode turns cameras off, but speech addressed to the robot is still heard, so "stop" and consent answers work.
- Fault injection and falls.
- Proprioception rendered honestly in realistic mode, e.g. an integer battery percentage.

**The sweep: cross-cutting needs found by scanning all 2,478 remaining entries**

- **Time:**
  - interruptible waits and wake-on-event from *perceivable* signals only;
  - notices (doorbell, alarm, crying and so on);
  - standing orders that execute during long waits;
  - savepoints;
  - idle probes for long horizons (about 6.5 s of wall time per quiet simulated day).
- **Temporal evaluation:** `never`, `always` and `responses` with trigger and window, plus milestones and deadlines sampled per substep. This caught a spec that had been passing by accident: its "quiet within 5 min" milestone held at t=0, before the alarm had even started.
- **Multi-phase episodes:** several requests per episode, each with its own success, questions and deadlines, and changing instructions.
- **Dialogue:** free-form `ask` with fact matching and synonyms, multi-turn, preferences, and fair scoring of necessary versus unnecessary questions.
- **Reports:** a shared report vocabulary (issues and states with evidence fields), with report predicates.
- **The robot's Mind:** memory, recall and a known map that stay honest under realistic perception.
- **Requests:** who asked, over which channel, from where, and with what gesture, rendered in observations.
- **Schema 3** spec format.

### 3.6 Task specs and verification (`house_sim/tasks/`, `tasks/authored/`)

- **Spec format:** specs are data-only JSON.
  - **Setup:** generated-house options, setup ops and domain helpers (spawn, move, person, pet, `looks_like`, physics `phys`, `load`, `edge`, `stage`, robot faults), plus variants per seed.
  - **Instruction:** an instruction and at least 3 phrasings.
  - **Scoring:** success, side effects, milestones, deadlines, responses, questions, rubric, and the hidden intent for the user simulator.
  - **Plans:** a reference plan and wrong plans written as skills or actions.
  - **Labels:** fidelity, target fidelity and risk.
- **Verifier** (`python -m house_sim --check FILE`):
  - runs every spec in its own perception mode, multiprocess;
  - adds a perceivability check in realistic mode: the reference may not report a value it never observed;
  - writes evidence keyed to a code fingerprint, so evidence goes stale when simulation code changes.

### 3.7 Evaluation suite (`house_sim/eval/`)

- **Splits:** train and dev seeds are public. The hidden **test** split uses HMAC-derived seeds from a key stored outside the repo, with a public nonce-blinded commitment, keyed private digests and packaging audits so private material never ships.
- **Agent harness:** Random, Search, ScriptedOracle (privileged), Mock and **LLM agents**. The LLM agents support the Anthropic Messages API and OpenAI-compatible endpoints, using `urllib` only, with:
  - caching;
  - cost caps that refuse unpriced models;
  - a done protocol;
  - `--dry-run` cost estimates.

  No paid run has ever been made. A dry-run estimate for the full dev split was about $900–3,400 depending on model and step cap, and the system prompt is about 31k tokens.
- **Leaderboard:**
  - confidence intervals, breakdowns and a failure taxonomy;
  - `--verify-submission` re-scores by replaying command logs;
  - signed verdicts, completeness checks, a separate privileged track for oracle perception, and offline HTML output.
- **Dataset exporter:** SFT, trajectory and step preference pairs, and critique records, with a strict validator.
- **Offline replay viewer:** a single-file HTML page showing the floor plan, perceived-versus-true views, actions and scores. Perceived-only pages leak no unperceived rooms.

### 3.8 Process and tooling (how the work is done)

The project is built by one "main session" (Claude Code) that owns the kernel, core, generator, tasks and eval code. It orchestrates **multi-agent workflows** for substantive work. Typical rounds:

- one owner agent per domain, editing only that domain's files;
- independent red teams and multi-lens reviewers that try to break things;
- fix passes for whatever the reviewers confirm;
- an integration agent to merge the pieces.

Tooling that supports this:

- **The package gate** (`tools/orchestration/tier1_gate.py`, checks G0–G10) runs:
  - unit tests in normal and strict mode;
  - tests with each domain loaded alone;
  - every spec in its own mode and forced realistic, with verdict and state- and observation-digest comparison against the previous gate;
  - the perception and layout audits;
  - fuzzing with cross-process determinism;
  - performance budgets;
  - the room-mode golden comparison;
  - a file-scope check.
- **Handoffs:** each work package writes `catalog/analysis/tier1/handoff_<ID>.md`.
- **Other tools:**
  - `tools/fuzz.py`: random commands across houses; it reports crashes and nondeterminism, and the latest result is 0;
  - `tools/physics_audit.py`, `tools/body_audit.py` and `tools/golden.py`;
  - `tools/make_batches.py` and `tools/show_entries.py` for authoring.

---

## 4. History (short)

| Date (2026) | Milestone |
|---|---|
| Oct 7 | An earlier prototype by another agent, recorded as the first 4 git commits |
| Oct 9 | v2 rebuild: kernel, 16 domains, integration and fix rounds 1–2, layout guarantees |
| Oct 9–10 | Realistic perception (Tier 0), red team; evaluation suite built and red-teamed; fix round 4; first push to GitHub |
| Oct 10–13 | Paused for usage limits |
| Oct 13 | Kernel round 5 (34 kernel requests from the domains, red team, repairs); tier-1 designs |
| Oct 14 | Tier-1 stage A: libraries plus the gate tool, a 26-finding review, mutation-verified fixes |
| Oct 15 | Stage B (physics, camera and body integration, 34 findings); stage C (sweep, 33 findings) |
| Oct 15–20 | Adoption round: 6 of 16 domains finished before a weekly usage limit; the round was resumed Oct 20 |

Every stage ended with the full check green: tests, all specs, perception audit 0 leaks, layout audit 0 problems and fuzz 0 defects. The one exception is the current adoption checkpoint, covered in section 5.

---

## 5. Current state (2026-10-20)

- **Committed and verified** (branch `tier1-foundations` = commit `0e77b67`): everything through stage C, with 3,849 tests and 242/242 specs green.
- **Local checkpoint** `bc0417b`: six domains (navigation_robot, manipulation, people, safety, cleaning and plumbing_liquids) adopted the tier-1 layers.
  - Their specs now run in generation 2.
  - All 243 specs pass, and the perception, layout and fuzz audits are clean.
  - **14 tests in 7 kernel test files fail.** They pin pre-adoption behaviour, such as the golden and generation-1 digests to re-record and grip expectations, and are deliberately left for the adoption integrator to update once all domains are in.
- **In progress:** the adoption round for the other 10 domains. Six of them were interrupted mid-edit by the usage limit; their partial work was saved as patches and re-applied. The round also includes two kernel follow-up agents:
  - camera apertures and performance;
  - compact replays, eval and viewer fixes, and gate-tool fixes.
- **Next steps, in order:**
  1. Adoption integration: full gate, re-records, kernel requests.
  2. **D1:** switch the physics laws on where every owning domain is "ready to flip".
  3. **D2:** generator v2 as the default.
  4. **D3:** geometric camera as the default for realistic perception, with spec triage.
  5. A final whole-system red team.
  6. An authoring pilot of 8 diverse batches (author agent, then an adversarial reviewer who writes gaming plans).
  7. The full authoring wave of 52 batches, with gap-fix rounds in which domain owners implement mechanics that authors reported missing, and re-authoring until no gaps remain.
  8. Final verification of all 2,720 entries; rewrite of the docs (README, ARCHITECTURE, TASK_SPEC, EVALUATION, KNOWN_LIMITS); packaging.
- **Estimate:** the foundations finish about Oct 21–22. Authoring is the largest remaining cost, estimated at 50–80M agent tokens, or roughly 2–3 weekly usage allowances, so completion is around early-to-mid November.

---

## 6. Known weaknesses, approximations and risks

Do not take these as settled; the owner wants them challenged.

1. **Still symbolic.**
   - No pixels, no learned detector, no rigid-body dynamics.
   - Physics is lumped, rule-based "P-approx" on a 0.5 m grid with 30 s substeps.
   - Fine-contact skills (sewing a button, threading a needle, untangling cable) and dexterous manipulation are approximated or reported as gaps.
   - Camera geometry is a grid viewshed, not real optics. Mirrors and visitor recognition are deferred.
2. **Coverage.** Only 243 of 2,720 entries (8.9 %) are authored and verified. The pace, quality and honesty of mass authoring are not yet proven at scale; the pilot is the test.
3. **Gaming risk.** Specs are scored by end-state predicates. Weak specs could be passed by a policy that does not really do the task, for example one that moves the mess elsewhere or acts on the wrong instance. The mitigations are adversarial reviewers writing gaming plans, wrong plans, variants per seed, side-effect predicates and global harm flags, but their adequacy at scale is unproven.
4. **Perception fidelity versus solvability.**
   - Forced into realistic perception, 225 of 242 specs pass; the rest were authored for oracle mode.
   - About 55 specs fail with the geometric camera until their reference plans become camera-aware; the D3 triage may be large.
   - Hidden-information leaks are audited heuristically: 0 leaks found is not a proof.
5. **Baselines.** The Search baseline scores 0 % (a toy instruction parser), and no real LLM agent has been evaluated yet, because paid runs need approval. So agent difficulty and benchmark discrimination are unmeasured.
6. **Performance.**
   - About 3 ms per substep and about 1.1 s per simulated hour of waiting; a quiet simulated day takes about 6.5 s, with a target of 3.5 s.
   - The camera watch system is over its per-substep budget when people are in the room.
   - Replays are about 3 MB per episode; 67 demo replays total 226 MB, so they are no longer committed, and a compact format is planned.
7. **Complexity and consistency.** About 222k lines across 16 independently evolved domains. Cross-domain consistency is checked through tests, audits and look-alike warnings (about 9 look-alike groups still differ in glance size or colour), but drift is a real risk.
8. **Process risks.**
   - Work is done by many AI agents. Earlier rounds suffered prompt truncation in orchestration (fixed by passing findings as files) and interrupted runs (usage limits).
   - Gates catch regressions only where tests, specs or audits exist.
9. **Realism of numbers.** Material, liquid, floor, support, robot and household distributions are sourced or reasoned, but some are assumptions:
   - furniture anchoring rate of 25 %;
   - mean household size of 2.29 versus about 2.5 in the US;
   - a snow-day definition that differs from the climate normals.
10. **Single developer environment.** Windows, Python 3.14, while the code targets 3.9. Cross-platform runs are not tested in CI, because there is no CI.

---

## 7. What the owner would like a reviewer to assess

Please be concrete and critical. Rank the issues you find by impact.

1. **Benchmark validity:**
   - Does the verification method (initial fails, idle fails, reference passes, plausible wrong plans fail, on 5 seeds) make specs hard to game?
   - What failure modes would let a weak or cheating agent score well?
   - What extra checks would you add, such as held-out seeds, metamorphic tests or randomised distractors?
2. **Realism versus tractability:** is the symbolic, grid-level physics and camera approach the right level of abstraction for training and evaluating household robot policies? What is most likely to transfer, and what is misleading?
3. **Perception honesty:** is the realistic mode (handles, field tiers, look-alikes, geometric camera) a sound model of a camera-and-sensors robot? What leak classes might remain?
4. **Architecture:**
   - Is the kernel plus 16 plugin domains maintainable at about 222k lines?
   - Are there design smells: duplicated physics in domains, too many hooks, global state, test brittleness?
5. **Prioritisation:**
   - Was building the tier-1 foundations before authoring 2,478 specs the right order?
   - Should the authoring scope be cut or tiered, e.g. a high-quality core of about 500 entries first?
6. **Scale and cost:** is AI-agent mass authoring with adversarial review likely to produce honest specs? How would you sample-audit the quality?
7. **Evaluation suite:**
   - Is the hidden-test-split design (HMAC seeds, commitments, private paraphrases) sound?
   - Is the leaderboard verification by replay trustworthy?
   - What should the first real LLM pilot measure?
8. **Anything missing** that a "real house in code" benchmark obviously needs.

---

## 8. Where to look (repo map) and how to run

| Path | Contents |
|---|---|
| `README.md`, `PROGRESS.md`, `RESUME.md`, `DECISIONS.md` | Overview, milestone log, resume notes, decisions (README and FINAL_REPORT are partly stale; a docs rewrite is pending) |
| `docs/KERNEL_API.md` | Kernel change log v2.1 to v3.1 (contracts) |
| `docs/PERCEPTION.md`, `PHYSICS.md`, `DIALOGUE.md`, `GENERATOR.md`, `TASK_SPEC.md`, `AUTHORING_GUIDE.md`, `KNOWN_LIMITS.md` | Subsystem docs |
| `docs/EVAL_HARNESS.md`, `EVAL_CONTRACTS.md`, `SPLITS.md`, `DATASET.md`, `VIEWER.md` | Evaluation suite |
| `docs/design/TIER1_*.md` | Foundation designs, integration plan, decisions |
| `docs/domains/<domain>.md` | Each domain's actions, skills, helpers, fields and what the robot perceives |
| `house_sim/kernel/`, `house_sim/systems/`, `house_sim/generator/`, `house_sim/tasks/`, `house_sim/eval/` | Code |
| `catalog/entries.jsonl`, `catalog/analysis/part_*.jsonl` | The 2,720 entries and their analysis records |
| `tasks/authored/*.json` | The 243 verified specs (`<domain>__examples.json`, `<domain>__perception.json`) |
| `catalog/analysis/tier1/handoff_*.md`, `round*_results.json` | Per-package handoffs and round results, including open items |
| `tools/` | Audits, fuzz, gate, authoring helpers, orchestration scripts |

Commands, all offline:

```bash
python -m house_sim --seed 3                                    # interactive generated house
python -m house_sim --check tasks/authored/cleaning__examples.json --workers 2
python -m house_sim --check FILE --perception realistic --camera geometric
python tools/perception_audit.py 100 80                         # realistic-mode leak audit
python tools/layout_audit.py 30                                 # generator layout audit
python tools/fuzz.py 12 300                                     # crash / determinism fuzz
python tools/orchestration/green_tests.py 5                     # all tests, normal + strict
python -m house_sim --run-agent oracle --split dev --limit 5 --verify-on-the-fly
python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --limit 10 --dry-run   # cost estimate only
python -m house_sim --viewer results/<run>/logs/<episode>.json --out episode.html
```
