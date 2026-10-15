# Authoring task specs for catalog entries

One spec per catalog entry, in `tasks/authored/<file>.json` as `{"specs": [...]}`. Read first:
`docs/KERNEL_API.md`, `docs/CONVENTIONS.md`, `house_sim/tasks/spec.py`, `house_sim/tasks/setup.py`,
`house_sim/tasks/runner.py`, `house_sim/tasks/skills.py`, the relevant `docs/domains/*.md`, and the domain example
files `tasks/authored/*__examples.json` (all passing; copy their patterns).

Tools:
- `python tools/show_entries.py batch N` — your entries with their analysis records (interpretation, mechanics,
  success, plausible-wrong behaviors, protected side effects, conditional branches, user-sim needs).
- `python -m house_sim --check FILE --workers 2 [--ids ID ...] [--seeds 0]` — verify (all 5 seeds by default).
- `python tools/show_house.py SEED [room ...]` — ASCII room grids; `python -m house_sim --describe ACTION`.

## Non-negotiable rules

1. **Test what the entry says, in its section's context.** "Clean sink" under Bathrooms is a bathroom sink with soap
   scum/toothpaste; under Kitchen cleaning it is food/grease. Never substitute an easier proxy. If the needed mechanic
   does not exist, do not fake it: leave the entry out and report the gap.
2. **Success = observable end state produced by real mechanics** (locations, soil, wetness, temperatures, settings,
   cycle states, reports matching ground truth, speech to the right person, approvals obtained, memory entries...).
   Never a keyword, never a flag that the robot can set directly.
3. **Don't make it worse:** add `side_effects` (`no_moves_except`, `no_damage`, `no_new_soil_except`,
   `unchanged_fields`), `preserve` paths and `failure` predicates for what the entry's analysis lists under protect.
   Global harms (damage, injury, spills, collisions, falls, breakages, fire, flood, privacy/safety/authority
   violations...) already fail every task.
4. **Randomize what matters** with setup ops: which object, where, how dirty/full/hot, distractors and look-alikes,
   who asks. Conditional entries ("when appropriate", "if needed", "safely", "when instructed") MUST use `variants`
   so that both branches occur on the 5 verification seeds (seeds 0,1,2,41,97 -> variant = seed % len(variants));
   a policy that always does (or never does) the action must fail some seed.
5. **Wrong plans (1-3) must be plausible mistakes** from the analysis `wrong` list (wrong item, wrong method/tool,
   wrong order, skipped step, unsafe shortcut, acting without permission, moving the mess elsewhere) and must fail for
   that reason. A wrong plan that crashes or does nothing is not acceptable evidence.
6. **Idle must fail:** waiting out the whole time budget must not satisfy the goal (events and systems keep running).
7. **Reference plan** uses skills/actions through `env.step`; it must pass on all 5 seeds without any refused step.
   Keep budgets realistic (`budget.steps`, `budget.time_s`).
8. **People and approvals:** risk-3 work requires the approval/presence mechanics; vague or preference-dependent
   commands put hidden intent in the user simulator (`user_sim` op; kernel v3.1: `facts`, see "People" below) and
   score questions (`questions`: `min_necessary`, `max_unnecessary`; v3.1 also `required`, `forbidden`, `ask_whom`,
   `max_total`). Asking when not needed must cost; guessing when ambiguous must fail some seed.
9. **Subjective entries** ("look nice", "comfortable", "presentable") use a `rubric` of weighted programmatic proxies
   with a threshold, calibrated so a sloppy-but-plausible plan fails and the reference passes.
10. **Phrasings:** `instruction` plus at least 3 `phrasings` (original wording, a natural variant, a casual variant;
    for vague/relative commands keep the vagueness).
11. Set `risk_level` (0-3), `fidelity` (what the verified mechanics actually decide: "S", "G" or "P-approx") and
    `target_fidelity` (from the analysis). Be honest: fidelity is what the simulator checks, not what the real skill
    needs.

## Special kinds

- **primitive** entries (action vocabulary): a micro-scenario where the verb is required and correct use is checked;
  wrong plans use a near-miss verb/parameter. Cognitive primitives: Plan/Sequence -> order constraints via
  `milestones` + `order`; Prioritize -> `deadlines` or order by urgency; Compare/Categorize/Identify/Recognize/Read/
  Count/Measure/Weigh -> `report` of a ground-truth value that must be discovered; Monitor/Detect -> a standing order or
  timely report after an event; Verify task completion -> a first attempt that silently fails (e.g. door bounces open)
  so success needs inspect + redo; Ask for clarification -> question scoring; Wait/Stop/Resume -> timing and stop
  semantics.
- **variation** entries (randomization axes): variants spanning the axis values where each value changes what the robot
  must do (e.g. door-handle types need different actions; floor materials need different cleaning tools; children
  present changes safety rules). A policy that ignores the axis must fail at least one seed.
- **adaptability** entries (vague/judgment commands): hidden intent in the user simulator, inspection-driven state,
  rubrics where subjective; success must depend on interpreting the situation correctly across variants.
- **appendix** entries (compound paragraphs): every clause must be exercised by at least one variant; combine several
  clauses per episode when there are more clauses than seeds allow.
- The INCOMPLETE fragment ("intentionally left out, and where everything belongs.") is implemented against its
  documented reconstruction: tidy items to where they belong while leaving intentionally-left-out items in place.

## Templates

Near-duplicate entries (e.g. the same chore in several sections) may share a template in
`tasks/templates/<your_file>.json` (`{"templates": {"<prefix>.name": {... "{{param}}" ...}}}`, names prefixed with your
file stem) and use `"extends"` + `"params"`; each entry still gets its own spec, context and phrasings.

## Setup placement notes (v2.3)

- `spawn floor` never cuts a hazard-free route or stands in a door swing; to stage a deliberate obstruction (e.g.
  "clear the toys blocking the hallway") pass `"obstruct": true` and usually an explicit `"cell"`.
- `person`/`pet` accept `"near": "<entity id or [x, y]>"`; they are never placed in doorways or chokepoints.
- Setup failures raise SpecError with the reason: if a helper needs a room the generated house may lack, add
  `house.require` (e.g. `["garage"]`, `["laundry_room"]`, `["patio"]`).
- `python tools/show_house.py SEED room ...` shows the grid; `python tools/layout_audit.py` checks generator health.

## Gaps

If an entry needs a mechanic that does not exist (or exists but cannot decide success honestly), do not fake it
and do not edit simulator code. Leave the entry out of your file and report a gap: the entry id, the owning domain
(from the analysis record), and precisely what is missing (state fields, verbs, systems, scenario helpers), with
what a correct implementation must make observable. Owners implement gaps between authoring waves.

## Perception-centric entries (realistic mode)

Entries where perceiving IS the skill must set `"perception": "realistic"` (see docs/PERCEPTION.md): Identify /
Recognize / Detect / Inspect / Compare / Count / Measure / Weigh / Read primitives, every "Check / Verify / Detect /
Report / Identify ..." entry, judging-state entries ("Is this still good?", "Is it dry yet?", "Is that pan still
hot?"), find/search entries with look-alikes, and anything about medicines or labels. Then:

- Plans still name true ids (the runner translates them to handles); the env shows the agent only perceived values.
- The reference must gather the information with real actions (approach + inspect / read / a measuring tool / ask)
  before acting or reporting; the verifier fails it if a value it reports never appeared in what it observed.
- At least one wrong plan must act on glance-level belief (pick the look-alike, report a bucket where a measurement
  is needed, trust a dry-looking surface, skip reading the label) and fail on some seed.
- Use look-alike groups and distractors (setup `looks_like`, label_text, novel objects) so a policy that does not
  look properly fails.
- Other entries stay in oracle mode (no field needed).

## Physical scenes (kernel v3.0; docs/PHYSICS.md)

Physics consequences are ordinary state: check them with path predicates (`entities.{glass}.broken`,
`entities.{shelf}.collapsed_shelves`, `stats.breakages`, `entities.{mug}.liquid_l`, `rooms.{k}.wet_cells`). Recipes:

* **"Is it stable / secured / packed safely?"** Perturb after completion instead of writing a keyword predicate:
  `{"op": "event", "when": <the goal arrangement>, "effect": "physics.bump", "args": {"target": "{table}", "g": 0.3},
  "repeat_s": 300}`; the wrong plan (glass at the edge, top-heavy shelf, unpadded box) fails through the real
  consequence. Other perturbations: `physics.nudge`, `pull` (27 kg on an open drawer), `load_test`, `wind_gust`,
  `rain`, `transport` (drops of a packed box), `brake`, `tilt`, `drop`.
* **Stage the situation** with the physics setup ops: `load` (fill a shelf to a load or a fraction of its rating),
  `edge` (an item at a margin from an edge, overhanging up to 30 %), `stage` (an overloaded shelf "about to give way",
  with `creep_s`; a staged support above 0.9 x its rating shows `sagging` at a glance until it is unloaded or gives
  way, whatever the overload law's state), `phys` (validated property overrides, e.g. a fragile vase, an anchored dresser, a printed
  `load_rating_text`). Add `{"op": "check_physics", "strict": true}` at the end of setup; specs with `"schema": 3` are
  linted automatically and an impossible staged scene fails the build.
* **Keep reference plans away from thresholds.** Strength and rating thresholds vary by about +-15 % per instance;
  a plan that loads a 25 kg shelf with 24 kg may fail on some seeds. Use `phys_exact: true` (setup `phys`) when a spec
  needs the nominal number.
* **Harm attribution.** A scripted event breaking something the robot never touched does not fail the task by itself,
  even on furniture the robot used (opened, or set other things on); write a `failure` predicate on `broken` if the
  robot was supposed to prevent it. Something the robot placed (or something resting on what it placed) that an event
  then knocked over counts against the robot, and so does a shelf that gives way under load the robot added.
* **Deferred laws** (`data/physics/laws.json`) are not applied automatically yet: a spec that relies on overload,
  crush, edge, carry, wind, ... says so in its notes and is verified again after the law's flip (stage D1), or uses the
  perturbation events, which call the kernel functions whatever the laws' states.
* **Realistic mode**: thresholds are hidden; the robot learns them by `read`ing a staged label, `inspect`ing
  (`where="closely"` gives a support's `looks`: sturdy or flimsy), touching (`feel`), measuring or trying. Sounds of
  falls and breaks arrive in `obs.sounds`.

## Robot body in specs (kernel v3.0; docs/KERNEL_API.md "v3.0 robot body", docs/ACTION_API.md "Robot body")

The robot is a body with a battery, two hands of rated capacity, sensors that can be off or broken, and faults
(TIER1_EMBODIMENT 13). Every action costs energy; lifts are derated; `look` / `inspect` / `read` need the colour
cameras; `listen` / `ask` need the microphones. Check outcomes on world state, never on a keyword:

| Purpose | Paths |
|---|---|
| Battery policy | `robot.battery_min_pct gte 15`, `robot.docked`, `robot.charging`, `robot.power_state eq "on"`, `robot.body_log.battery_low_s`, `stats.battery_reserve_breaches eq 0`; the global failure `robot_stranded` |
| Capability envelope | `stats.overload_attempts eq 0`, `stats.handoffs gte 1`, question scoring |
| Degradation | `robot.fault_state.arm_weak_left.detected is_true`, `robot.fault_state.<id>.logged_s` exists, `stats.unfounded_fault_logs eq 0`, `robot.camera_clean gte 0.95` |
| Falls and drops | `stats.falls` (global), `stats.robot_drops eq 0`, `robot.fallen is_false`, `robot.actuator_ok is_true` |
| Efficiency | `robot.energy_wh_used lte X` |

Setup ops (tasks/setup.py):

* `{"op": "robot", "profile": "humanoid_compact", "profile_overrides": {"max_hand_kg": 5.0}, "sensors": {"camera_on":
  false, "mic_on": true, "privacy_policy": {"camera_off": ["bathroom"], "when": "occupied"}}, "props": {...}}`: the
  profile (`humanoid_v1`, `humanoid_compact`, `mobile_manipulator`: data/robot/profiles.json), then overrides for this
  episode, then the sensor switches, then props (robot fields; `"max_hand_kg": "{cap}"` after a `uniform` op stays a
  number). Unknown keys, profiles and sensor values are SpecErrors.
* `{"op": "robot_fault", "kind": "arm_weak", "part": "left", "severity": 0.5, "latent": true}` at reset, `"at_s": 120`
  to start later, `"until_s": 600` to end it; `{"op": "robot_fault", "var": "fault", "choose": ["camera_dirty",
  "network_down", "arm_weak:left", "none"]}` draws one with the setup RNG and binds `fault` (the option) and `fault_id`
  (the fault id, for paths like `robot.fault_state.{fault_id}.detected`; null for "none"). Kinds: camera_dirty,
  camera_scratched, camera_failed, mic_failed, speaker_failed, network_down, localization_lost, arm_weak, arm_failed,
  gripper_slip, legs_weak, actuator_failed, battery_degraded, battery_overheat, water_ingress, software_stuck; only
  arm_weak, gripper_slip, battery_degraded and speaker_failed can be latent (unknown to the robot until found).

Recipes (each varies by seed, `variant = seed % len(variants)`, so that both branches occur on seeds 0 1 2 41 97):

* **Battery reserve on a long chore or patrol.** Set `battery` so the whole route needs a mid-task dock. Success:
  `robot.battery_min_pct gte 15` and `robot.docked` at the end. Wrong plan: skip docking (it breaches the reserve or
  strands the robot).
* **Heavy object.** A `uniform` mass from 0.6 to 1.4 times the capacity, or randomize `max_hand_kg` / `max_mass_kg`.
  The reference hefts (manipulation `support mode=heft`), then carries with both hands or asks for help; success
  includes `stats.overload_attempts eq 0`. Always-carry produces an overload; always-ask fails the question score.
* **Failing arm.** `robot_fault arm_weak` on a random side, latent. The reference runs diagnostics or finds it on the
  first lift, then uses the other arm or both. Failure predicate: `stats.overload_attempts gte 2`.
* **Dirty camera.** `camera_dirty`: a read needs `clean_camera` first. Wrong plan: a paper towel leaves scratches;
  `robot.camera_clean lt 0.95` fails it.
* **Network down.** The reference asks a person who is present instead of calling `request_remote_help`, which is
  refused.
* **Cold battery.** The robot outdoors at -10 C: the gauge reads lower and the robot strands earlier; the reference
  docks early.
* **Privacy.** An occupied bathroom: knock and get consent with full hearing, then turn privacy mode on; hearing is
  then "addressed", so "please leave" is still heard, while `look` / `inspect` / `read` refuse.

Authoring rules:

* **Keep reference lifts away from the capacity edges**: derates apply above the head (higher than 1.7 m), from the
  extra overhead cell, on a climbing aid and in limp mode (battery at or below 5 %). Run
  `python tools/body_audit.py --spec-files "<file>"`: it lists every reference pick over its derated capacity, every
  explicit power grip on an item under 15 mm and every item under 2 mm (all must be zero).
* **Small and thin things**: items under 15 mm are pinched automatically (do not ask for `grip=normal` on them);
  papers lying flat take 3 s more to slide to the edge.
* **Careful speed** slows a walk to half speed (2x the normal time) and other physical actions by 1.5 x; budgets and
  deadlines of careful plans are computed with that.
* **Robot predicates on the battery** are gauge readings (`robot.battery_min_pct`); keep thresholds a few percent away
  from the reference's lowest reading, since every action, wait and walk now costs energy.

## Camera-dependent entries (kernel v3.0 geometric camera; docs/TASK_SPEC.md "Camera mode")

Entries where *seeing* is the skill -- searching (under, behind, inside, the back of a shelf), surveying a room,
supervising someone, reading in poor conditions, glare, dark corners, small or low-contrast things, what a carried
load hides, privacy of what the cameras record -- declare `"camera": "geometric"`. The robot then sees only what its
cameras frame from where it stands (line of sight, light, resolution); plans use `look` (around / up / down / at a
thing, `rows=2` for the floor), `face`, `peek` (under / behind / inside / top / closely with the wrist camera),
`approach` and posture changes (a kneeling eye sees under beds). Then:

- **Success is checked on world state**, never on a "looked" keyword: `camera_log.coverage.<room>`,
  `camera_log.looked_in.<container>`, `camera_log.peeked["<id>:under"]` (path `camera_log.peeked.<id>:under`),
  `camera_log.sightings.<id>`, `camera_log.in_view_s.<pid>` / `max_gap_s.<pid>`, `camera_log.captures`,
  `camera_log.watch.<name>.*`, plus the usual reports matched against ground truth.
- **At least one wrong plan must fail for a camera reason** and is marked `"camera": true`: it did not look (a report
  without a peek), looked from the wrong place (one look from a doorway of a room made L-shaped by tall shelving;
  standing where only kneeling sees under the bed), in the dark or into glare, or missed the back row. The lint
  enforces this for `"schema": 3` specs.
- **Patterns** (TIER1_CAMERA 14): survey a room or the house (`coverage >= 0.9` per room plus the seeded anomalies
  reported); search with an honest absence claim (every candidate in `looked_in` / `peeked` before a `report ... found
  false`, ordered with `milestones` / `order`); hoses behind appliances (`peeked["washer_1:behind"]` plus the defect
  report, with `side_effects.no_moves_except` excluding the appliance so dragging it out fails); the back of a shelf
  (stage items with `spawn ... "row": "back"` behind taller front items; reports must include them); supervision
  (`max_gap_s.<child> <= 30` and `in_view_s` near the episode time; the wrong plan leaves the room or does not track);
  privacy (`privacy_violations == 0` or no `captures`; the wrong plan walks into an occupied bathroom with the camera
  on); a video call (`watch.call.robot_in_view_s == 0` with a laptop `view_watch`); labels facing out (`faces`,
  `label_facing`); low light (headlamp or careful speed decide whether a dark cable is seen); carried loads hide the
  floor ahead (a basket carried in both hands hides about 2.4 m of floor from the head camera's frames).
- **What counts as looked into**: `looked_in` needs at least half of a container's interior in view through its
  opening (a box's inner floor, every shelf band of a cabinet), not its rim; a cabinet's lower shelves often need a
  crouch. An open hinged door is a thin leaf at its hinge jamb (it hides what stands behind it, not the room), and a
  plain `inspect` lists only contents a frame saw: things under or behind a support need a `peek` first.
- **Staging**: the `camera` op sets where the robot starts looking, `view` gives a thing view properties its type
  lacks (`text_m` of a small label, `faces`, `glossy`, a device `camera`), `track_view` / `view_watch` add what the
  log should follow, `spawn ... "row"` places items in front or back rows. Keep reference plans away from the acuity
  edges: a 2 cm item 0.5 m from a standing robot covers about 12 pixels (rarely detected), from a kneeling one 24
  (surely detected).
- **Reference skills** in geometric mode: `approach` first walks to a viewpoint when the target is out of sight;
  `reveal` (used by `fetch`, `put`, `use`) kneels beside a bed or sofa and peeks under it (or lifts a light cushion,
  rug or mat), peeks behind furniture, and opens closed containers as before; `fetch` crouches for low items and
  stands up again. Room-mode plans are unchanged.
- **Realistic perception** (`"perception": "realistic"`) combines with the geometric camera: detections are then
  coarsened by optical acuity (an "object" at detect level, the category word at recognize, the full label at
  identify). After the D3 switch-over geometric becomes the default for realistic perception (docs/PERCEPTION.md).
- **Verify** with `python -m house_sim --check FILE` (the spec's own camera) and, while iterating, `--camera room` to
  compare; a geometric spec forced to the room camera skips its camera setup ops.

## People: questions, reports, memory and requests (kernel v3.1; docs/DIALOGUE.md)

Entries about asking, telling, remembering and who asked use the canonical forms. A schema-3 reference plan asks with
`about` / `aspect` (a legacy `topic` there is a lint error; agents may still use topics) and reports with `kind`.

- **Hidden information is a fact**, not an exact answer key: `{"op": "facts", "add": [{"id", "about", "aspect",
  "detail", "value", "said", "known_by", "otherwise", "necessary", "observable_via", "aliases"}]}`. Write `about` at
  the level a person would answer (a thing, `type:bed`, `kind:tea`, `room:`, `person:`, `event:`, `request`); give
  `aliases` for the words people use; set `known_by` (the child does not know, the guest says "Ask Sam") and
  `observable_via` (the answer is visible on the thing at glance or after a read: then asking is unnecessary).
  Score with `questions: {"required": [fact id], "forbidden": [...], "ask_whom": {id: "requester"}, "max_unnecessary",
  "max_total"}`. Wrong plans: skip the question (required missing), ask the wrong person (otherwise), ask again the
  next day (unnecessary), guess.
- **Ambiguity without a fact per thing**: two things a word names and one fact on one of them gives a clarify-back
  (the agent must ask again with a handle); `necessary: "ambiguous"` makes the question necessary only while two
  candidates are seen; `no_history` only while memory holds fewer than three consistent placements.
- **Permission** is `aspect ok, act <verb>, about <thing>` (the same approval scope as `permission:<verb>:<id>`).
- **Reports** are typed: findings (`report_set` with `min_precision` / `min_recall` over an `expected` list for
  inspections: every dry plant, every loaded filter; `{"not": {"report": {...}}}` for false alarms), measurements in
  any unit (score with a tolerance), status, counts and `none_found` (pair it with a truth predicate). Score delivery
  with `to` and `heard` (in person in the same room; a text counts once read), timing with `after` / `before_s`
  (milestones or phases). Issue and state words have synonyms (tables in docs/DIALOGUE.md): score the canonical
  word. Domains add their issue words with evidence fields (CONVENTIONS 8), which the realistic verification uses:
  a scored finding needs perceived evidence.
- **Memory**: "learn the house" scores `mind.map` / `mind.seen` paths; "what changed since yesterday" is spawn /
  arrange, `prior_knowledge` with a snapshot, then the setup moves (the robot does not know them; `diff` against the
  snapshot finds them); "where I usually put it" uses prior beliefs and `history:<id>` memories (the people history
  synthesizer, adoption round); prior `seen` levels inspect / read give knowledge that lapses once the thing moved.
- **Who asked**: `request` (TIME-EVAL's spec key: `from`, `channel`, `room`, `gesture`, `subject`,
  `phrasings_by_role`) reaches the agent as `obs.request`; a child's request is not an adult's (authority checks are
  people's), and with realistic perception a request in the dark comes from a shape.
- **Calendar**: the `calendar` op adds `household.calendar` events (ids unique, ISO times, recurrence, `status`
  tentative / cancelled, `hidden` values, `visible_on` sources); asking `when` about an event is answered from it, its
  hidden values by `status` with the key as detail.

## Recipes for the sweep's entry patterns (kernel v3.1; docs/TASK_SPEC.md, docs/DIALOGUE.md)

Mark new specs `"schema": 3` (generation-2 houses, agent-legal waits, a default requester, the dialogue and report
lints). Temporal keys, requests, phases and waits are in docs/TASK_SPEC.md ("Temporal keys", "Requests", "Phases",
"Waiting and standing orders"); questions, reports and memory in docs/DIALOGUE.md. The patterns that recur across the
catalog (TIER1_SWEEP 23):

| entry pattern | mechanism | wrong plans that must fail |
|---|---|---|
| "Tell me when the laundry's done" | auto `watch` on the washer's glance state + `report(kind=status, to=requester, channel=...)`; `responses` with the cycle's end as trigger, `within_s` 120; `never` a report before done | notifies at the spin pause; interrupts a call; misses the chime |
| a hidden preference learned once | a fact `{about: "type:bed", aspect: "preference", detail: "pillow_layout"}`, `questions.required`; a second phase the next day with `max_unnecessary: 0` | skips the question; asks the child (otherwise); asks again the next day |
| "put it where I usually put it" | prior knowledge from the people history synthesizer (beliefs + `history:` memories), `request.from` the speaker, success at the speaker's modal spot | the generic home; the other resident's habit; the last-seen spot |
| an inspection report | `report_set` with `min_precision` / `min_recall` over findings; issues with evidence (perceivability) | reports from the doorway without looking; `none_found` while one is bad |
| "what changed since I left" | spawn / arrange, `prior_knowledge` with a snapshot `-1d`, then the setup moves; `report_set` of `misplaced` findings | reports everything; reports nothing; restores without telling |
| throughout / never | `never` with windows; `responses` with `for_each_new` | lets one spill sit; makes a new mess |
| several requests | `phases` with gaps and per-phase questions | answers the first request twice; never ends a phase |
| who is asking | `request.from` + `user_simulator.intent.requester`; authority by people; `said` to check a relayed claim | obeys the child; never checks with the parent |
| calendar | the `calendar` op with `hidden`; necessary only while the event is tentative (`necessary: {"pred": ...}`) | asks about a confirmed event; guesses a tentative one |
