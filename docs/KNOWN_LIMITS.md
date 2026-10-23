# Known limits and outstanding scope

House AI is a pure-Python (standard library only), deterministic household simulator for evaluating and training
household robot agents. This file says what it does not do, where it approximates, and what is still unbuilt. Fidelity
labels follow the specs: **S** symbolic state and rules, **G** grid and box geometry, **P-approx** lumped physical
models with cited numbers. Nothing here is evidence that a real robot can do these chores.

Contents: 1 coverage and evidence, 2 the house model, 3 physics, 4 perception and the camera, 5 the robot body,
6 people, pets and communication, 7 time, events and evaluation, 8 evaluation tools and data, 9 not claimed, 10 next.

## 1. Coverage and evidence

* The catalog has 2,720 entries (`catalog/entries.jsonl`). 242 are DONE: an authored spec whose reference plan
  passes and whose initial state, idle run and every plausible wrong plan fail on seeds 0, 1, 2, 41 and 97, recorded in
  `reports/verification.jsonl` for the current code fingerprint (`python -m house_sim --verify`). 2,477 have no
  spec yet and 1 is BLOCKED (the truncated adaptability entry A-ce36af8e040d38-01, whose original intent is missing).
  `coverage.csv`, `reports/COVERAGE.md` and `reports/pending.jsonl` give every entry's status and reason.
* Demonstrations (`python -m house_sim --demo`, `demos/manifest.json`): 67 of the 105 catalog sections have a
  replay-verified reference run on the held-out seed 701; the manifest lists the other sections and the reference plans
  that fail on that seed (T-94a0a940224c22-01 closes a gate that seed 701 generates closed).
* DONE means verified inside the simulated distribution of the spec: five seeds of procedurally generated houses, the
  spec's own perception mode (oracle for most), its scripted residents and events. Five seeds do not cover every home,
  object or interpretation, and a label match plus a successful script is not evidence of semantic understanding.
* Under forced realistic perception 226 of the 242 authored specs pass; the other 16 were authored for oracle
  verification (handoff_TIER1-GATE.md lists them). With realistic perception and the geometric camera, about 178 pass
  (stage-B preview); the rest are the D3 triage list (section 4).
* Evidence is keyed to `house_sim.audit.fingerprint()` (simulation code and data); any simulator change makes it stale
  until `--verify` runs again. Verification checks the authored plans on five seeds; it cannot prove that no
  unintended plan also passes.

## 2. The house model

* Houses are generated (`house_sim/generator`): generation 1 by default; generation 2 (`gen=2`, docs/GENERATOR.md:
  household composition, resident profiles and schedules, belongings with owners and homes, common furnishings, weather
  from the climate normals of five regions, porch steps) is opt-in (stage C makes it the default for schema-3 specs).
  Apartments, shared laundry rooms, lobbies and elevators exist. Types a domain has not added yet are skipped by the
  generation-2 furnishers with a note, never faked (docs/GENERATOR.md).
* Every room is its own grid of 0.5 m cells, joined to other rooms only through connections (doorways, doors, stairs,
  elevators). Entities are axis-aligned boxes on cells; things on surfaces have axis-aligned spots (`spos`), things
  inside containers have shelves and rows, other contents have no position. There is no continuous geometry, wall
  thickness, collision mesh, trajectory or reachability volume beyond the grid, reach heights and step stools.
  Outdoor rooms are grids with an optional slope, not terrain.
* Cloth, bags, cables and foam are materials with properties, not meshes; knots, folding, draping and wrinkles are
  abstract states. Food chemistry (cooking, spoilage) is rule-based state, not chemistry.
* Domain mechanics (16 domains, docs/DOMAINS.md) are symbolic or lumped models; each domain document lists its own
  fidelity limits (`docs/domains/<domain>.md`, the "limits" section).
* Furniture anchoring: a generation-2 house straps an assumed 25 % of its tall free-standing storage furniture (dressers,
  wardrobes, bookshelves, shelf units, filing and chemical cabinets of 0.686 m or more) to the wall
  (mechanisms.furnish ANCHORED_SHARE; TIER1_DECISIONS) until a cited survey figure is found; generation-1 houses keep
  mechanisms' older convention (anchored), since drawing it there would re-record the generation-1 digests.

## 3. Physics (kernel v3.0, docs/PHYSICS.md)

The physics layer is lumped and rule-based (P-approx): rated loads with a scene allowance, drop tolerances, crush
limits, tip / slide / roll thresholds, Newton cooling with latent heat, scalar liquid volumes and films, a drag / lift
threshold model for wind. Thresholds are deterministic with a hash-derived per-instance spread of about +-15 %
(`phys_exact` turns it off), never random breakage.

Not modeled:

* No rigid-body dynamics, trajectories, contact solver, friction cones or collision geometry. Falls land on the floor
  beside the support (or on the shelf below a collapsing shelf), with no intermediate collision, bounce or ricochet,
  and a falling thing never damages what already lies on the floor; people and pets in the landing cell are reported
  to the domains' hooks, which decide injuries.
* Liquids are scalar volumes: a puddle's area is its volume over a film thickness; there is no flow field, splash
  distance or runoff direction except down a support chain to the floor. The physics layer does not dry its own
  puddles: drying is the cleaning domain's, and until cleaning adopts the kernel's spills a kernel puddle stays until
  something absorbs it.
* No deformables, no granular flow (pouring rice or sand), no structural mechanics beyond rated loads.
* Thermal is lumped Newton relaxation per entity with latent heat for water and contact checks; no conduction field
  or radiation geometry. Burns use an interpolated time-temperature table.
* Wind is a scalar speed and direction at the item's height and shelter; there are no gusts unless scheduled (or in
  the weather process) and no sheltering geometry beyond flags and hooks. Sound is a loudness word per consequence
  event (faint / noticeable / loud), not acoustics.

Approximations to know about:

* A soft landing (carpet, rug, grass, bed, sofa, cushion) absorbs a fixed energy whatever the falling thing's
  footprint, so heavy flat or long things (a 1.2 kg vase falling from a table onto carpet) land harder than they would.
* Wind slides objects only with its sustained speed; a gust lifts sheets and tips tall things over (a wheeled bin
  blows over), but does not shove a light low object along, so light things blow about less often than in gusty
  reality.
* A tipped piece of furniture keeps its upright footprint; a roll ends at the room's downhill edge without checking
  furniture in the way; sliding on a sloped floor is not modeled.
* One settle pass handles at most 64 consequences before it stops (a law call that crosses the budget finishes; a long
  shelf cascade is one call).
* Harm attribution: a consequence counts against the robot when its action or motion caused it directly, when it
  arranged the thing that failed (or a support in its location chain), or when a support failed under load it added.
  Exogenous consequences (scripted events, pets, people) are recorded in the `phys_*` statistics and never fail a task
  by themselves. This holds for every kernel path (`tests/test_physics_settle.py` checks it metamorphically: gale
  gusts and bumps leave an idle robot's harm flags unchanged); harm flags that domain modules raise directly are
  attributed per site until the house ledger of the verification redesign (independent review M9; known cases: a door
  slammed by the wind counted as robot damage, a pet escaping through a door the robot never touched).
* A stack member is judged against the top of the one below it (its declared surface, else its footprint) by the part
  that bears on it (its footprint or its foot); the centre of mass of the members above is not summed, so a stack is
  judged member by member. Folding furniture has its in-use size only through `open` and `open_size_m`.

Laws that duplicate a domain's own rules are deferred until the domain adopts them (`data/physics/laws.json`; stage D1
flips them): active today are only orphan (an item whose support vanished falls), overflow, drips and staged creep.
Overload, hang, crush, handle, edge, slope, stack, furniture tipping, open spills, contact heat, wet contact, thermal
shock, phase change, melting, set-down, carry dynamics, the recalibrated thermal time constant, wind and rain are
wired but deferred: the kernel applies them automatically only after the flip, while perturbation events and domains
can call the functions now. Until then the domains' own rules decide those outcomes.

Physical data: 42 material classes cover the 129 material strings of the registry; some types still have default
mass or size or an implausible bulk density (`tools/physics_audit.py --data` lists them per domain for the adoption
round).

Decided in the adoption round and left as they are (catalog/analysis/tier1/adopt_status.json has the reasoning):
a record flagged `fragile` takes the annealed-glass tolerance only when its class never breaks (a fragile plastic or
electronics item keeps its class's 3.0 / 1.2 m: 62 calibrated types would otherwise break from a table, so a fragile
picture frame needs its own `drop_tolerance_m` or a glass material); a fast set-down is a 6 cm drop, which chips no
brittle ware (rim strikes against a counter edge are not modelled); thermal shock from a water film counts only on
conductive tops (stone, metal, ceramic: TIER1_PHYSICS 5.9; a wet wooden board does not quench); a wet textile's held
water does not add to the mass the lift plan and the support laws use (support.own_mass; textiles_laundry models wet
laundry mass in its own verbs).

Kernel caches (phase 1, independent review M3): a `World.static_index` is the set of entities whose record as it was
when the current step began matches its predicate, plus or minus the entities created or destroyed since. A field
that changes during a step counts from the next step on (a whole-day `wait` is one step), so a domain system that
needs a field the same step must re-check it on the returned records, as the contract always said. The content no
longer depends on the cache's history: a saved and reloaded episode or a replay computes what the uninterrupted one
does. Each World learns the fields its indexes read (about 20 ms per episode for the first lookups); a predicate must
read only its own record.

## 4. Perception and the camera

* **Room mode** (the default camera) has no field of view or line-of-sight occlusion inside a room: what the robot
  perceives is decided by containment, concealment, darkness, the lens and the small-object model
  (docs/PERCEPTION.md).
* **Realistic perception** shows opaque handles, glance-level buckets and look-alike groups until the robot inspects,
  reads, measures or touches. Handles follow true ids: a thing swapped while nobody looked keeps its handle (handle
  epochs are deferred). There are no images, learned recognition errors beyond the declared look-alike groups, or
  smell and taste chemistry beyond the domains' symbolic channels.
* **The geometric camera** (kernel v3.0, opt-in with `camera="geometric"` until the switch-over D3 makes it the
  default for realistic perception) is closed form: G for poses, frames, occlusion and line of sight at cell-and-box
  resolution; P-approx for acuity, light and glare. Its non-goals:
  * no pixels, images, depth maps, learned detectors, image noise, motion blur or rolling shutter; detection is a
    function of geometry;
  * acuity thresholds (Johnson-style pixel criteria, the 10-24 px detection band, COCO-style small objects) are
    engineering approximations, not calibrated against a real detector;
  * occluders are axis-aligned boxes on the 0.5 m grid with height spans and porosity; entities are boxes with at most
    five sample points; partial occlusion is reported in quarters;
  * light is a scalar per point: no colour temperature, no cast shadows beyond the under / behind / inside shading, no
    bounce beyond room ambient; an interior light multiplies the room's ambient rather than adding its own (an open lit
    fridge in a dark kitchen is dark inside); beams cast no shadows;
  * rooms are separate grids joined only through openings (camera phase 3): a head frame sees into the next room
    through an open doorway (or a closed glazed door) of see-through kind, the two rooms abutting rigidly across
    the doorway; the opening is clipped to where both rooms' walls overlap (a doorway between two corner cells whose
    rooms run apart is one cell wide). The observation still lists only the robot's room and the controls beside its
    doorways that it can reach from the threshold (listed when a frame sees them through the doorway); people and
    pets seen through a doorway go to `StepView.beyond`, the sightings, the watch of tracked agents and privacy
    captures, and to domain channels through `camera.through_doorways` (mechanisms' through_doorways renders them),
    not to `obs.people`. Floor cells seen through a doorway do not count toward the next room's coverage, nothing is
    seen through a doorway with the cameras off (no IR shapes), and a thing seen through a doorway is acted on only
    from its own room (the controls by the doorway excepted). Stairs and hatches between floors are not openings;
  * windows are openings only for a viewer outside looking in (`sight.aperture_viewer`: its coverings pass 5 %, and
    by day the glass reflects what stands more than 1 m inside a room lit below half the daylight): no room lies
    beyond an exterior window in the house model, so the robot does not look out of windows, and interior windows
    between two rooms are not modelled (no generated house has one);
  * a privacy policy masks what the cameras see (`camera_off`) and the microphones hear (`mic_off`) of its room
    through a doorway; a microphone takes a private moment through an open door or doorway within a straight 4 m of
    the robot (no sound propagation model in this rule);
  * no mirror reflections: deferred by TIER1_DECISIONS until the glare / mirror catalog entries (mirrors appear only
    in the 18-entry glare group, TIER1_CAMERA 21.6) need them during authoring; a mirror shows nothing it reflects and
    an un-inspected mirror still looks like a doorway while in frame. The opening viewshed of phase 3 is the machinery
    a mirror would reuse (the eye reflected across the mirror plane, clipped to the mirror's rectangle);
  * visitor recognition: the kernel says whether the frames show a face well enough (`camera.face_seen`: 64 px across
    a 16 cm face, turned within 60 degrees of the camera, at the identify level); the matching itself (enrolled faces,
    disguises, confidence) is the people domain's, which gates on it in its adoption; sound direction is out of
    scope;
  * the wrist camera has no arm kinematics (it looks from the support's reachable edge); head and body turn at fixed
    rates.
* Camera approximations:
  * an open hinged door is a 4 cm leaf standing at 90 degrees at its hinge jamb (no partly open doors);
  * things in a container are packed in rows from the opening back; an overfull shelf keeps its extra rows at the back,
    where things may share space and then do not hide each other (the front row still hides the back);
  * the wrist camera reaches under a light cushion, or under a bed beside a kneeling robot, without a look first:
    `can_see` and `pick_up` allow it, while a plain `inspect` lists only what a frame saw and realistic perception
    names nothing unseen;
  * tracked identity follows a thing's placement signature, so a thing moved unseen and moved back is re-identified;
    face gating judges the faces seen from the current eye only; observer percepts a domain adds to an entity are shown
    only at the identify level; a thing seen while its support is not still names its support's handle in `location`;
    people and pets below identify are anonymous shapes per sighting, including an identified resident who walks out
    of range (the kernel's face_seen tells a domain matcher when a face is seen well enough);
  * visual events are witnessed by the head pose at the moment they happen (during an action: the pose before its
    motion; during a wait: the action's final pose); the people and navigation domains' entry-based captures still
    count in geometric mode until their adoption;
  * open adoption-round requests (K-CAM handoff): a large flat floor covering (a whole-room floor slab, a big rug)
    counts as seen only when one of its five sample points is in frame and in line of sight, so soil on it may go
    unobserved while the frames survey its cells (cleaning); a geometric `inspect` renders an open bin's fill level
    without a look inside (cleaning); inspect acuity is per thing, not per field, so a fridge door's `ajar` gap reads
    from across the kitchen (appliances_cooking); a record's own `view` dict and the `view` key the camera adds to a
    geometric record share a name, which the perception audit reports as a hidden field (textiles_laundry,
    mechanisms: an audit false positive);
  * the per-substep watch (TIER1_INTEGRATION 3.4: 0.05 ms with nobody in view, 0.1 ms with people in view) is exact
    and memoized on what its line of sight reads, with the light judged every substep: measured inside real 2 h waits
    of generated houses with people in the robot's room (the merged adoption round, a loaded machine, A/B against the
    stage-C code) 0.045-0.05 ms with nobody in view and 0.075 ms with one person in view, from 0.13-0.18 ms (K-CAM
    handoff). The rest is exact bookkeeping, not geometry: judging the 15-25 domain writes of each substep for the
    room's geometry epoch (parts and things inside furniture are dropped on the first hop) and, per person in view,
    the light and pixels (the daylight and the domains' light_at hooks change unseen); a domain light_at hook is
    asked per person in view while the room is lit below 0.36.

* Open perception items after phase 1 (catalog/analysis/phase1/INT.json): an unresolved look-alike puddle shows its
  liquid's hazard kind in `grid.hazards` ("slip" vs "wet"; the fix needs the domains' path_blocked and
  visible_floor_hazard hooks to follow); core place's "Protected or valuable item must not be discarded" refusal is
  decided by the never-tier `sentimental` / `value` flags, so trying to bin something tells whether it is protected
  (letting the discard happen needs the verifier ledger's lost-item rule); how an unidentified unfamiliar thing may be
  operated is undecided (review I4).

## 5. The robot body (kernel v3.0, docs/KERNEL_API.md "v3.0 robot body")

| Topic | Model | Fidelity |
|---|---|---|
| Energy | a power table per activity and posture plus a per-metre walking cost: humanoid_v1 0.288 Wh/m at normal speed (a cost of transport of about 1.8, the conservative end for current humanoids), 2.4 kWh pack | P-approx |
| Battery | state-of-charge bookkeeping, idealized CC/CV charging, a first-order pack temperature and a generic Li-ion cold table; no aging within an episode | P-approx |
| Balance and dynamics | none: falls are rule outcomes of causes in domains (traction, overload, structures, water) | S/G |
| Lift capacity | a per-hand rating times discrete derates (an extra overhead cell, above the head, on a platform, critical battery, arm health) | G |
| Dexterity | grasp size classes, a scalar positioning precision in mm, force caps; no contact mechanics | S |
| Sensors | availability and quality states (imaging, depth, hearing full / addressed / none, speaker, localization, network); camera geometry is the camera layer's | S |
| Faults | injected by scenarios or caused by explicit events (falls, water, overheating); intermittent faults use deterministic draws | S |
| Noise and stopping | dB-at-1-m tables (propagation and listeners live in the domains); a kinematic stopping distance | P-approx / G |

Not modeled: joint torques, motor heating (a hook is kept), wake-up latency from standby, pack aging, partial progress
of an interrupted non-motion action, electrical faults inside the robot, people rescuing a powered-down robot (docking
it or swapping its pack is a people follow-up).

Approximations to know about:

* A walk the battery cannot finish stops at the last affordable cell, and the robot never starts a doorway, door or
  flight of stairs it cannot finish (it powers down at the foot; a real robot might fail on the stairs). A power-down
  is a consequence, not a refusal: the step returns ok with a "powered down" message and keeps what happened.
* An interrupted non-motion action is all or nothing: a shortfall within the pack's hidden buffer completes it, a
  larger one refuses it, and a refusal in a nested non-motion leaf rolls the whole parent step back (a power-down
  partway through a chain commits what already happened).
* A domain verb that moves the robot without recording its route (a guide, a mow) is charged a straight-line walk
  from where it started to where it ends, through the doorway, on top of its own activity power, until the domain
  records the route (`ctx.motion`); a body that could not walk is refused.
* The held-load safety net keeps a grip closed on what the arm can hold (limp mode and climbing aids limit new lifts
  only), strains above 1x and drops a load above 1.5x a hand's capacity; a co-carried end counts an equal share and a
  towed cart 15 % (an assumption). Domain verbs that put things into the hands without the lift check are caught only
  by this net.
* Careful speed slows a leaf's own physical seconds by 1.5 (walking verbs declare `timing="speed"` and walk at the
  careful speed instead); domain verbs whose duration is an argument (`hold_steady`, `knead`, `blend`, ...) still get
  the factor until they declare `timing="commanded"`.
* Hold power and the walking work's load term follow each hand's load word, not the exact mass (phase 1, review L3:
  `proprio.felt_load_kg`, the middle of the word's band of the hand's believed capacity), so the battery cannot weigh
  what the robot holds; the energy is off by at most about an eighth of a hand's capacity times 3 W/kg of hold power
  (physics -- stairs, floors, the safety net -- keeps the exact mass).
* Proprioception: the battery estimates use the nominal empty-handed powers (they never weigh what the robot holds);
  navigation's `obs.nav` still shows the exact battery and lens values until its adoption; hearing "none" also empties
  network messages (calls), which a real robot's link would still receive; observers that do not key on `obs.dark` may
  still add visual percepts with the cameras off.
* Threshold crossings during an action are stamped at the action's start (only the cutoff inside an idle substep has
  its exact time).

## 6. People, pets and communication

* Residents, guests and pets are rule-based agents (roles, authority and consent, abilities, schedules, needs, moods,
  sleep; docs/domains/people.md and pets.md), not learned or psychologically realistic behaviour. Many norms are
  opt-in per scenario.
* `ask` answers through a scripted user simulator: exact topics and 31 domain answerer prefixes; an unknown topic gets
  "I have no additional information about that." There is no open-ended dialogue, speech recognition or language
  understanding beyond the command grammar and these topics. `report` writes free field names into `reports`; their
  value vocabularies are not published yet.
* The robot's known map, beliefs and snapshots live in its Mind (`env.mind`, kernel v3.1), saved with the episode but
  not in the world state: specs score them only through `mind.*` evaluation paths, and the agent sees the map only
  through `recall("map")` (`env.known_map()` is a tool view of it).
* Privacy mode turns the cameras off and stops recording, while speech addressed to the robot is still heard (a user
  decision, so "stop" and consent answers work).
* Privacy counting differs between the camera modes in two cases: in room mode the people domain counts entering a
  room where someone is in a private moment, with the geometric camera what the sensors capture. Walking in counts one
  violation in both modes (`tests/test_kernel_contracts.py` CameraModeHarmTests); a robot already in the room when the
  moment begins, and a robot looking through an open doorway into the room (waiting in the hall facing a bathroom
  where someone showers: phase-1 red team), count none in room mode and one with the geometric camera. Both stay
  people-domain items until room mode counts them (catalog/analysis/phase1/breakages.json). Without the people domain
  the kernel counts captures in geometric mode only (TIER1_DECISIONS: its fallback; room mode has no capture rule of
  its own).

## 7. Time, events and evaluation

Until stage C (kernel v3.1, docs/design/TIER1_SWEEP.md) lands:

* `wait` is fixed-length (at most 7 days) and nothing interrupts it; `watch` (standing orders) only queues commands
  into `pending`, and each needs `run_pending`; there is no week-long autonomous manager. 13 reference plans wait on
  hidden state (a privileged `wait_until`); they move to agent-legal waits in the migration M-WAITS.
* Events are scripted state changes with seeded timing, not a continuous stochastic process. The clock has seasons
  and a fixed daylight curve, no holidays, daylight saving or latitude (generation 2's climate normals give weather by
  region and date; generation-1 weather is clear or cloudy at 8-24 C whatever the date).
* Evaluation is end-state: failure, preserve and side-effect predicates read the final state, and milestones are
  recorded at the end of a step only, not inside a wait. One instruction per spec, delivered as a plain string.
* There is no notice bus: the robot cannot be woken by a doorbell or an alarm during a wait.

## 8. Evaluation tools and data

* The offline baselines (random, search, scripted oracle, mock LLM) are references, not agents to beat: the search
  agent is a toy fetch policy, the oracle is privileged. LLM agents run only with an explicit `--allow-network` and a
  configured provider (an API key or a local server); token counts in estimates are a characters-per-token rule.
* Episodes always run in realistic perception while verification uses each spec's own mode, so even the privileged
  oracle cannot reach 100 % over the whole dev split (`docs/EVAL_HARNESS.md` "Limits").
* Dataset demonstrations come from privileged reference plans (routes and `avoid` lists computed from true state);
  wrong plans are the spec authors' plausible mistakes, not a sample of agent failures (docs/DATASET.md).
* JSON integrity hashes and fingerprints detect accidental changes, not malicious code or signed provenance; a hostile
  in-process agent could read private attributes, so run untrusted agents in a separate process.

## 9. Not claimed

No learned policy or LLM training, no sim-to-real transfer, no perceptual recognition from sensor data, no general
common sense or open-ended language understanding, no language or accent generalization. Fire, smoke, gas, flooding
and power faults are domain rule models (safety, plumbing_liquids, electrical), not combustion, fluid or circuit
simulations; injuries are recorded by rules, not biomechanics. Quietness and resource use are symbolic tables.

## 10. Next

The tier-1 plan (docs/design/TIER1_INTEGRATION.md): stage C adds interruptible waits, notices, standing orders that
run themselves, temporal evaluation, phases, canonical questions and reports and the robot's memory; the domain
adoption round routes each domain through the kernel's physics, camera and body; then the law flips (D1), generation 2
for the verified specs (D2) and the geometric camera as the realistic default (D3), before the remaining catalog
entries are authored as schema-3 specs.
