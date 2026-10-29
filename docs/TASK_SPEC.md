# Task specification (schema 2, 3 and 4)

An authored spec is one JSON object in `tasks/authored/<domain>__<topic>.json` (`{"specs": [...]}`), one per catalog
entry. `house_sim/tasks/spec.py` validates and expands it, `house_sim/tasks/runner.py` builds a concrete episode
(`build(spec, seed) -> (config, task, oracle)`) and runs plans, `house_sim/tasks/verify.py` verifies it on seeds 0, 1,
2, 41 and 97. This page is the reference for the spec format; `docs/AUTHORING_GUIDE.md` has the recipes, the setup
ops and the domain helpers; `docs/EVALUATION.md` what verification checks.

`"schema": 3` marks a spec authored under tier 1 (kernel v3.1). Specs without it are schema 2 and keep exactly the
behaviour they were verified with. Schema 3 turns on, for that spec only:

* generation-2 houses (`"house": {"gen": 1}` keeps generation 1; `python -m house_sim --check FILE --gen 1|2`
  forces one for every spec);
* agent-legal waits in plans: `wait_until` becomes the agent action `wait(seconds=max_s, until=P)`, which the
  environment refuses when P is not perceivable ("privileged wait");
* the notice bus (`notices` in the state: doorbells, alarms, speech and people coming and going reach the robot as
  notices, `obs.notices`);
* a default requester when the spec names none (the first adult resident, in person when they share the robot's
  room, else through the app);
* the schema-3 lints: the physics setup lint is an error, a geometric spec needs a camera wrong plan, idle checks
  stop early once success is impossible (`decided: "fail"`), the reference run must pass the dialogue lints
  (docs/DIALOGUE.md "Spec scoring and lints"), and with realistic perception scored typed reports need perceived
  evidence.

`"schema": 4` turns on verification hardening (docs/design/VERIFY_HARDENING.md; section "Schema 4" below): the house
ledger, labelled predicates, the declared-done goal with settle and look-ahead, content scoring, evaluation-mode
verification with evidence gating, and the batteries. **Every new spec is schema 4**: a spec whose catalog id is a
catalog entry outside the frozen list `catalog/review/legacy_ids.json` (the 243 ids authored before the switch) and
that does not declare `"schema": 4` is invalid (lint L-SCHEMA in `spec.check_structure`, so the compiler marks it
INVALID). Schema 2 and 3 specs keep their semantics and evidence until they are migrated; the schema-4 keys they may
already carry (the R1 drafts) are validated but inert.

## The spec object

| key | meaning |
|---|---|
| `catalog_id`, `title` | the catalog entry (required) and a short name |
| `schema` | 2 (default), 3 or 4 (required for every new spec, section "Schema 4") |
| `extends`, `params` | a template from `tasks/templates/*.json` and its `{{param}}` values |
| `house` | generator options: `dwelling`, `require`, `residents` (`"minimal"`, `"random"`, a list), `pets`, `start`, `region`, `gen` (1 or 2) |
| `setup` | setup ops (`house_sim/tasks/setup.py`, docs/AUTHORING_GUIDE.md); they bind variables used as `{name}` everywhere else |
| `variants` | partial specs merged per seed (`variant_index(catalog_id, seed, len(variants))`: hashed per block of n consecutive seeds, so nothing short of the whole seed predicts it; review H5); `"+key"` appends to a list key |
| `instruction`, `phrasings` | the request text and at least three phrasings (a single-phase spec) |
| `request` | who asked and how (below) |
| `success`, `failure` | predicates: all success must hold; any failure defeats success |
| `preserve`, `side_effects` | paths that must keep their baseline value; collateral-damage rules (`no_moves_except`: moves the robot caused, by the settle pass's attribution -- wind, pets, people and scripted events moving things do not fail it; `no_damage`, `no_new_soil_except`, `unchanged_fields`) |
| `milestones`, `order`, `required_milestones`, `deadlines` | named conditions with the time they were first reached, their order, required ones, deadlines (below) |
| `never`, `always`, `responses` | temporal conditions over the whole episode (below) |
| `phases` | several requests in one episode (below); a spec has either `instruction` / `success` or `phases` |
| `rubric` | `{"items": [{"pred", "weight"}], "threshold"}` |
| `questions` | `max_unnecessary`, `min_necessary`; with PEOPLE-IO's canonical questions also `required`, `forbidden`, `ask_whom`, `max_total` (docs/DIALOGUE.md) |
| `allow` | global failure flags the task tolerates (`damage`, `fire`, ...) |
| `strict_blocks` | refused unsafe / private / unauthorized attempts fail the task (default true) |
| `budget` | `{"steps": 400, "time_s": 7200}` by default; a phased spec defaults to the sum of its phases' budgets plus the gaps |
| `perception`, `camera` | `"realistic"` (default oracle); `"geometric"` (default room; section "Camera mode") |
| `risk_level`, `fidelity`, `target_fidelity`, `family`, `approval`, `tags`, `notes` | bookkeeping |
| `reference`, `wrong` | the privileged reference plan and at least one plausible wrong plan `{"name", "plan"}` |

Template, variant and `+key` merging: `merge` overwrites a key, merges the dict keys `house`, `budget`,
`side_effects`, `milestones`, `deadlines`, `questions`, `envelope`, and appends for `"+key"` (`"+setup"`, `"+never"`,
`"+responses"`, `"+timing"`, `"+battery_exempt"`, `"+mustpass_exempt"`, ...).

The schema-4 keys (`answers`, `envelope`, `absent_ok`, `protections`, `protect_map`, `instruction_roles`, `timing`,
`settle_s`, `lookahead_s`, `process`, `fungible`, `informative`, `free_ok`, `battery_exempt`, `mustpass_exempt`,
`budget_reason`, `clauses`, `scene_exception`, `track_open`, `hold_limits`; variants also `branch`) are described in
section "Schema 4".

## Predicates

Data only; no code from a spec is ever evaluated.

```
{"path": "entities.{cup}.location", "op": "eq", "value": "{sink}"}      a state path compared with a value
{"path": "...", "op": "gte", "ref": "other.path"}                         ... or with another path
{"path": "...", "op": "is_true"}                                          is_true / is_false need no value
{"all": [...]}  {"any": [...]}  {"not": P}
{"forall" | "exists": {"where": SEL, "check": P_rel, "in": ...}}         quantifiers
{"count": {"where": SEL, "check": P_rel, "in": ...}, "op": OP, "value": N}
{"agg": {"fn": "sum"|"min"|"max"|"avg"|"count", "in": ..., "where": SEL, "check": P_rel, "field": "portion_g"},
 "op": OP, "value": V | "ref": path | "ref_agg": {...}}                   aggregates (sum / count of nothing: 0;
                                                                          min / max / avg of nothing: false)
{"milestone_count": "chime", "op": "gte", "value": 2}                     rising edges of a counted milestone
{"report": {...}}, {"report_set": {...}}                                  typed reports (PEOPLE-IO, docs/DIALOGUE.md)
paths "mind.map", "mind.seen.<id>..."                                     the robot's memory, read-only (PEOPLE-IO)
```

Operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `not_contains`, `exists`, `approx`
(`[value, tolerance]`), `between` (`[lo, hi]`), `len_eq`, `len_gte`, `len_lte`, `subset`, `superset`, `startswith`,
`is_true`, `is_false`. A missing path fails every operator except `exists` (false), `is_false` (true).

Quantifiers select entities (default), people (`"in": "people"`), pets (`"in": "pets"`) or the records of any dict or
list path (`"in": "household.guests"`; a list's ids are its indices as text). `P_rel` paths are relative to the
selected record; `"$id"` is its id and `"$room"` its root room. Selector keys (`SEL`): `type` (also `is_a`; for
people and pets also `role` / `species`), `category`, `label`, `ids`, `not_ids`, `tag`, `tags_any`, `within` (a
descendant of an id; entities only), `room` (root room), and any field compared with equality or `{"op", "value"}`.

`report` holds when any `report_log` record matches its expectations; `"after"` (a milestone, `"phase:<id>"` or
seconds) and `"before_s"` (seconds after `after`, or absolute without it) bound the record times. `report_set` holds
when precision >= `min_precision` and recall >= `min_recall` (default 1.0 each). The matching itself is
`kernel/reports.py` (docs/DIALOGUE.md).

## Temporal keys (kernel v3.1, `kernel/monitor.py`)

```json
"milestones": {"door_open": P, "kept_dry": {"pred": P, "held_for_s": 600}, "chime": {"pred": P, "count": true}},
"never":  [P, {"pred": P, "after": "lit", "until": "extinguished", "label": "stove_unattended"}],
"always": [{"pred": P, "after": "phase:p1"}],
"responses": [{"trigger": "chime", "response": P, "within_s": 120, "each": true, "label": "notified"},
              {"for_each_new": {"category": "spill"}, "response": {"path": "$exists", "op": "is_false"},
               "within_s": 600, "label": "spills"}],
"deadlines": {"door_answered": 120, "dressed": {"by": "07:30"}, "cleaned": {"within_s": 600, "after": "spill_seen"}}
```

* The monitor samples after each action's settlement (before its time passes), after every 30 s substep of a wait or
  an action's duration, after each auto standing-order command and at the end of each step. A milestone records the
  first sample where it holds (`{"step", "time_s"}`), so one reached inside a long wait has its true time; one reached
  by an action (or an auto command) is stamped at its end -- an action is complete only when its time is spent, so a
  20 s walk does not meet a 5 s deadline (the same stamp opens a response obligation, meets one, and starts a
  `held_for_s`).
* `held_for_s`: reached at the first sample where the predicate has held continuously that long. `count: true`:
  `count` rising edges and up to 50 `times` (read with `milestone_count`).
* `never` / `always`: latched the first time the predicate holds / does not hold at a sample inside the window.
  Failure labels `never:<label or index>`, `always:<label or index>`. The verifier rejects a `never` without an
  `after` window whose predicate already holds in the initial state.
* Windows: `after` / `until` take a milestone name (open once it is reached / closed once it is reached),
  `"phase:<id>"` (after: from its start; until: to its end; `"phase:<id>:start"` / `":end"` are explicit) or seconds
  of episode time.
* `responses`: every rising edge of the trigger (a milestone name or an inline predicate) opens an obligation due
  `within_s` later; it closes at the first sample where `response` holds (at once if it already does); one first
  seen holding after its due time is missed, like one that never holds. A missed one fails as `response:<label>` (one still open at evaluation is `response_open:<label>`: no success, not decided).
  `each: false` takes the first edge only. `for_each_new`: every entity matching the selector that is created during
  the episode opens an obligation whose `response` is evaluated relative to it (`$exists` is false once destroyed).
* `deadlines`: a number keeps its meaning (episode seconds); `{"by": "HH:MM"}` is the first such wall-clock time at
  or after the episode start (`"day+1 07:30"` for later days; `kernel/clock.py parse_when`); `{"within_s", "after"}`
  is relative to a milestone or phase start. A milestone reached after its deadline, or not reached once the time is
  past it, fails as `deadline:<name>`.
* `evaluate()` adds `temporal` (violations, responses, milestones, deadline limits) for tasks that use these keys,
  and `decided`: `"fail"` once success is impossible (a latched violation, a missed response or deadline, the
  budget, a global failure flag the task does not allow, a failed phase), else null.

## Requests (who asked)

```json
"request": {"from": "{pid}", "channel": "in_person"|"phone"|"intercom"|"app"|"note"|"overheard"|"text",
            "room": "{rid}", "gesture": {"point_at": "{eid}"} | {"place": "{surface}", "spos": [0.4, 0.2]},
            "relayed_by": "{child}", "subject": "{target}", "note": "{note}", "phrasings_by_role": {"child": ["..."]}}
```

The runner resolves the request (ids bound; the text is the drawn instruction unless the requester's role has its
own phrasings) and binds `user_simulator.intent.requester` to `from` when unset. At reset the engine appends it to
`state.requests` (`{"id": "r1", "phase", "from", "channel", "room", "t", "text", "subject", "gesture"}`, plus
`relayed_by` and `note` when given; `room` defaults to the speaker's: the relayer's for a relayed request) and posts it
as speech to the robot in `messages` (`speaker` = `from` or `relayed_by`, `to: "robot"`, `request` id, `channel`,
`room` only for in person / overheard / intercom, `heard_by_robot` by the channel rule: in person / overheard need the
speaker in the robot's room, phone / app / text the robot's network, a note is read, not heard). `note` (channel note
only) names the written note, an entity: `obs.request` shows its handle. A gesture fires people's `people.point`
effect at once, so the request's first observation shows it (without the people domain the gesture is dropped and the
request record says `gesture_dropped`). Specs without `request` (schema 2) post nothing.

## Phases (several requests in one episode, `kernel/phases.py`)

```json
"phases": [
  {"id": "p1", "request": {"from": "{mom}", "channel": "in_person", "phrasings": ["...", "...", "..."]},
   "success": [...], "never": [...], "deadlines": {...}, "questions": {...}, "budget": {"steps": 150, "time_s": 3600},
   "ends": "done"},
  {"id": "p2", "start": {"after": "p1", "gap_s": 50400, "robot": "dock",
                         "events": [{"at_gap_s": 3600, "effect": "people.use", "args": {...}}]},
   "request": {...}, "success": [...], "questions": {"max_unnecessary": 0}, "ends": "done"},
  {"id": "p3", "start": {"when": P}, "supersedes": "p2", "request": {...}, "success": [...]}]
```

* Phase keys: `id`, `start`, `request` (or `instruction` + `phrasings`), `success`, `failure`, `never`, `always`,
  `responses`, `milestones` (the phase's own: "<id>/<name>", sampled only inside the phase; its deadlines, response
  triggers, windows, milestone counts and report anchors name them first, so the same name may serve several phases
  and an earlier phase never meets a later one's deadline), `deadlines` (numbers count from the phase start),
  `questions` (counted inside the phase), `rubric`, `budget`, `ends`, `supersedes`, `optional`, `notes`. Top-level
  `never`, `always`, `failure`, `side_effects`, `preserve` apply to the whole episode.
* Start: the first phase starts at reset; later ones `{"after": id}` (when that phase ends, then `gap_s`; the default
  for a phase without `start` is the one before it, no gap), `{"at_s": N}` (episode time) or `{"when": P}`. An
  `at_s` / `when` phase starts only while no phase is active unless it `supersedes` the active one (which closes as
  `superseded`, not scored: its own never / always / response / deadline failures no longer decide the episode).
  `"optional": true` (only with a `when` / `at_s` start) marks an interruption that may not happen: not started when
  the episode is evaluated, it is unscored and keeps nobody waiting.
* End (`ends`): `"done"` (the harness calls `env.phase_done()` when the agent declares done with phases left; plans
  use `{"phase_done": true}`), `{"at_s": N}` after the phase start or `{"when": P}` (both checked at every substep
  boundary); a phase that exceeds its budget (steps or time) ends as failed at the end of that step.
* Gap: `env.phase_done()` runs it at once: the robot is parked on its dock first when `robot` is `"dock"` (only with
  empty hands and not fallen; logged `phase_relocate`), then an idle advance of `gap_s` with every system, auto standing orders and
  the gap's `events` (`at_gap_s` after the gap start). A phase that ended inside a wait has its gap run at the end of
  that step, so the agent never acts between phases. The next phase's request is posted and becomes the instruction.
* Scoring: a phase is scored when it ends, on the state then (frozen in `state.phase.history`); the active phase is
  scored live; phases not started fail (`phase_not_started:<id>` among the episode's failures). The episode succeeds
  when every scored phase succeeded and nothing failed at the episode level; `goal_fraction` = phases passed / phases
  scored; `evaluation.phases` lists the records.
* Verification: the initial state must not satisfy phase 1; the idle check waits out each phase's budget (and the
  episode's) in at most 30 waits -- hour-long ones, longer ones for budgets over 30 hours -- and declares it done;
  reference and wrong plans contain `phase_done` steps.

## Plans

```
{"do": "open", "target": "{fridge}", ...}                one env.step; "expect": "fail" requires a refusal
{"skill": "fetch", "obj": "{plate}", ...}                a registered reference skill (privileged routing)
{"save": "answer", "from": "last_answer.value"}           bind a value from the latest observation
{"save_state": "x", "path": "entities.{p}.location"}      bind a value from privileged state (reference only)
{"foreach": "{plates}", "as": "p", "steps": [...]}       {"repeat": 3, "steps": [...]}
{"wait_until": P, "max_s": 3600, "poll_s": 60}           schema 2: poll the state in waits of poll_s (a privileged
                                                          wait, counted per trial as privileged_waits; tools/verify.py
                                                          --privileged-waits lists them); schema 3: one agent wait
                                                          wait(seconds=max_s, until=P), refused when P is not
                                                          perceivable ("privileged wait")
{"wait_for": ["doorbell", "addressed"], "max_s": 3600}    wait(seconds=max_s, interrupt_on=kinds)
{"phase_done": true}                                      end the active phase (multi-phase specs)
```

Reference plans run strictly (any refusal raises); wrong plans run leniently, but a wrong plan that raises or has a
step refused for its form (an unknown action, a parameter the action does not take, a missing or mistyped argument) is
not evidence and fails the spec; a literal `"do"` name that is no registered action is a SpecError at build (phase-1
red team). Skills may read privileged state to route but change the world only through `env.step`.

## Waiting and standing orders (agent actions)

```
wait(seconds, until=?P, interrupt_on=?[kinds])
watch(condition, commands, persistent=false, mode="queue"|"auto", guard=?P, max_runs=?, cooldown_s=?, expires_s=?)
```

`until` and the conditions of standing orders must be perceivable by the robot now, per referenced id
(`kernel/timeline.py perceivable` / `observed`): glance-tier fields of a thing it sees now (not a dark shape;
geometric camera: in its view at the level the field needs) or of devices that report remotely or audibly, an alarm's
`alarm_state` compared with "alarm" (heard), a person's or pet's location and posture while it can tell who they are
(realistic: in its lit room; geometric: identified in the view), someone coming into or leaving the robot's room (a
location compared with the robot's room: accepted while it watches the room, also while they are elsewhere), the
clock; the weather from outdoors, through a window or with the network (never its bookkeeping: `process`,
`forecast_state`, `seed_key`, `override_until_s`; through a window without the network, realistic perception tests
the condition word only); never `notices.*`; in realistic perception no numeric thresholds or `ref` operands on entity
fields. A wait ends, an order fires and a guard passes when the condition holds and the robot sees it hold (an `any`
with one observed true branch does; a `not` of what it cannot see does not). Notice kinds for `interrupt_on`:
`speech`, `addressed`, `doorbell`, `knock`, `alarm`, `phone`, `person_enters`, `person_leaves`, `pet_enters`,
`crying`, `call_for_help`, `person_down`, `smoke`, `appliance_done`, `device`, `order`, `battery`, `fault`,
`visual`, `any_sound`, the physics sounds (`crash`, `glass_breaking`, `splash`, `thud`, `clatter`, `scrape`, `creak`)
and the group `urgent` (alarm, call_for_help, addressed, person_down, smoke); domains register more. Auto standing
orders run their commands themselves while the robot is idle (waits, phase gaps), each under a savepoint; see
docs/KERNEL_API.md "v3.1".

## Camera mode (kernel v3.0 geometric camera)

Authored specs may declare `"camera": "room"` (the default: room-wide visibility, byte-identical to earlier kernels)
or `"camera": "geometric"` (the robot's posed cameras: line of sight, light and resolution; `face`, `peek`, the
extended `look`; `camera_log` in the state). The run's camera is, in order: the CLI's `--camera` (or
`verify.run_trial(..., camera=)`, `runner.build(..., camera=)`, `runner.make_env(..., camera=)`, `HouseEnv(camera=)`),
then the spec's `"camera"`, then the default (room until the D3 switch-over, docs/design/TIER1_INTEGRATION.md 4).
The built task carries the spec's `"camera"`; each verification trial records the mode it ran in (`"camera"`).

Setup ops for geometric specs only (SpecError in a spec without `"camera": "geometric"`; validated always, applied
only when the run's camera is geometric):

| Op | Form | Effect |
|---|---|---|
| `camera` | `{"op": "camera", "face": 90 \| "north" \| "{thing}" \| [c, r], "pan": 0, "tilt": -10, "track": ["{pid}"], "hw": {...}}` | the robot's starting body heading (grid degrees, a compass word, or toward a thing, person, pet or cell from its cell: place the robot first), head pan and tilt, the people and pets the head keeps in frame, hardware overrides (keys of `camera.HW`, same shapes). Sensor switches (`camera_on`, `mic_on`, `privacy_policy`) belong to the robot op |
| `view` | `{"op": "view", "target": "{x}", "props": {...}}` | merges validated view properties into the record's `view`: `porosity`, `below_porosity`, `clear_below_m`, `behind_gap_m`, `slab_m`, `height_m`, `opening` (front / top / none), `interior_light`, `glossy`, `front_deg`, `faces`, `text_m`, `camera` (a device camera), `beam` |
| `track_view` | `{"op": "track_view", "ids": ["{pid}", "{toy}"]}` | `camera_log.sightings` also follows these things |
| `view_watch` | `{"op": "view_watch", "name": "door_cam", "viewer": {...}, "targets": ["{x}", "robot"], "every_s": 60}` | what another viewer sees, every `every_s` (at least 10 s), in `camera_log.watch.<name>` (`visible`, `ever`, `fraction`, `clear`, `ever_clear`, `robot_in_view_s`). Viewers: `{"entity": "{device}"}` (a thing with a `view.camera`), `{"person": "{pid}", "attention": true}`, `{"room": "{r}", "cell": [c, r], "z", "yaw", "tilt", "hfov", "range_m"}`, `{"aperture": "{window / door / connection}", "outside_m": 3.0, "eye_z": 1.6}` (someone outside looking in through it: its coverings and, by day, the glass's reflection apply; camera phase 3) |

`spawn ... "in"` gains `"row": "front" | "back"` (both camera modes; inert in room mode): the item's `inner_spos` puts
it in the front row at the opening, or the back row against the back wall, of its shelf, at the leftmost free place.

Success and failure predicates may read the camera log, e.g. `{"path": "camera_log.coverage.{k}", "op": "gte",
"value": 0.9}`, `{"path": "camera_log.peeked.{washer}:behind", "op": "exists"}`, `camera_log.looked_in.<id>`,
`camera_log.max_gap_s.<pid>`, `camera_log.watch.<name>.ever_clear`.

## Lint (`spec.check_structure`)

`check_structure(spec) -> [problem]` never raises on a malformed spec: it first checks the container types
(`spec.shape_problems`: `success`, `failure`, `wrong`, `variants`, `phases`, `setup`, `reference`, `phrasings`,
`allow`, `order`, the temporal lists and `timing` are lists, `house`, `budget`, `side_effects`, `milestones`,
`deadlines`, `questions` and `params` objects, each variant and phase an object whose lists are lists, a `"+key"`
appends a list) and returns only those problems when there are any; a part a validator still cannot read becomes a
problem too, so the compiler marks such a spec INVALID instead of aborting the catalog. Then:

* the required keys (`catalog_id`, `reference`, and `instruction` + `success` or `phases`), three phrasings, a wrong
  plan with `name` and `plan`, `risk_level` 0..3, `schema` 2, 3 or 4; a catalog id outside
  `catalog/review/legacy_ids.json` declares schema 4 (L-SCHEMA);
* the schema-4 keys wherever present, in every schema (section "Schema 4"): the envelope and protections through
  `ledger.validate_envelope` / `validate_protections`, `timing` through `monitor.validate_timing(items, names,
  strict=True)`, answers, labels, roles, the protect map's form, exemption proposals, reasons, clauses, a wrong plan's
  `expect` / `mistake` / `mistake_fix` / `variants` / `modes` / `expect_budget`; for schema 4 also every success and
  failure predicate (`predicates.validate_predicate`, which checks `said` bodies through `speech.validate_said`;
  report bodies through `reports.validate_expectation`), at least 2 wrong plans each with `expect` and `mistake`, and
  only known keys in variants;
* `phases`: unique ids, three phrasings per request, success per phase, known keys, a valid `start` (`after` an
  earlier phase, with `gap_s` / `robot` / `events` only there), `ends`, `supersedes`; no top-level `instruction`,
  `success` or `rubric` beside them;
* `request`: known keys, a known channel, a gesture with `point_at` or `place`;
* temporal keys: valid predicates, windows that name milestones or phases, responses with a trigger milestone or
  predicate (or `for_each_new`) and `within_s`, deadlines in one of the three forms;
* the camera rules: `"camera"` is room or geometric; a spec whose setup, plans or predicates use the camera ops,
  `face` / `peek` or `camera_log` declares `"camera": "geometric"`; a schema-3 geometric spec has a wrong plan
  marked `"camera": true` that fails for a camera reason.

Verify one file with `python -m house_sim --check FILE [--camera geometric] [--gen 2] [--perception realistic]`; a
schema-4 spec takes the levels and debugging options of the hardened verifier (`--level quick|standard|full`,
`--battery`, `--mustpass`, `--modes`, `--seeds`, `--heldout N`, `--explain DIR`, `--json OUT`, `--no-cache`;
docs/KERNEL_API.md "Verification core"), and `python -m house_sim --draft-hardening FILE --ids ID [--out DRAFT.json]`
prints the drafts of its schema-4 keys.
`python -m house_sim --verify [--verify-only ID|SECTION|FILE ...]` writes the catalog evidence; with `--gen`,
`--camera` or `--perception` it is a forced check of every selected spec under that mode (the M-GEN2 migration runs
`--verify --gen 2`), written to `reports/verification_forced.jsonl` beside the evidence, never over it.

## Schema 4 (verification hardening; docs/design/VERIFY_HARDENING.md 20)

`"schema": 4` changes nothing a spec does not declare except the defaults that are on for every schema-4 spec: the
house ledger (every non-bookkeeping field of every entity is watched, harm is judged against the same seed's idle
rollout), the content guards on speech and reports, the declared-done goal with a 60 s settle window, the look-ahead
where triggered, the existence guard on protective success predicates, verification in the evaluation mode
(realistic perception) with evidence gating, the formula budgets and the batteries. The declarations below are what
the lints ask for. `python -m house_sim --draft-hardening FILE --ids ID` prints suggestions for them (it writes
nothing into the spec); `python tools/protect_map.py ID` drafts the protect map; `python tools/show_entries.py batch
N` prints the draft protect map beside each entry's analysis record.

| key | where | meaning |
|---|---|---|
| `schema: 4` | spec | turns everything on (5.4) |
| `{"label": L, "pred": P}` | items of `success`, `failure`, a phase's `success` / `failure` | labelled predicates; a label is a word without `:`, used once per list; unlabelled items are `success_<i>` / `failure_<i>` (14.2) |
| `answers` | spec, variant | `{name: {"bind", "kind", ...}}` hidden answers: kind `number` (`tol`, `min_cv`), `entity` / `set` (`group`, `evidence`), `word` (`domain`), `place` (`group`), `time` (`spread_s`), `branch` (`"bind": "_branch"`, `evidence` items naming their entity); `evidence` `{"entity"?, "field", "tier"}` with a tier word of `house_sim/data/verify/derivations.json` (glance ... measure); `derive` `{"op", "over" + "field" or "of": ["{e}.field", ...]}` for aggregates (9.1, 15.2) |
| `envelope` | spec, variant (dict merge) | what the task may change: `work_rooms`, `move` (`{selector: {"to": [scopes]}}`), `consume`, `deliver`, `consume_by`, `change`, `soil`, `wet`, `mess`, `held_at_end`, `damage`, `resources`, `agents`, `create` (6.6); selectors `{binding}`, `type:`, `is_a:`, `category:`, `tag:`, `within:`, `room:`; scopes also `baseline`, `rooms:work`, `home`, `owner` |
| `protections` | spec | `off` (`{guard: reason of 30+ characters}`; `house.lost`, `house.mess_unaccounted` and the content guards only per entity or person), `never_touch`, `restore` (`category:light`, `doors:interior`, selectors) (6.7, 6.12). Ambient by default (revoked by `restore`): lights (`on`, `lit`, `brightness`, `dim_level`, `settings.level`) and a wall switch whose loads are all lights; interior doors. Windows and skylights are ordinary settings (a window the reference opens or closes needs `envelope.change`). Using something up is `house.removed:<e>.<f>` (`house.lost:<e>.<f>` for a valuable) unless `envelope.consume` / `deliver` / `consume_by` allows it |
| `protect_map` | spec | `[{"item": <analysis protect text>, "by": [references]}` or `{"item", "waive": reason}]` (7.2; grammar below) |
| `absent_ok` | spec | success labels whose subject may legitimately disappear (8.6) |
| `instruction_roles` | spec | `{placeholder: "destination", "target", "recipient" or "context"}` (13.7; defaults below) |
| `timing` | spec, variant (`+timing`), phase | temporal relations `{"label", "trigger", "act", "within_s", "not_before_s": 0, "hold": "end", "if_no_trigger": "forbid", "initially": "false"}` (8.5, kernel/monitor.py): the window `within_s - not_before_s` at least 120 s, `not_before_s` a multiple of 30; labels `timing:<label>:early`, `late`, `unheld`, `unneeded`, `no_trigger` |
| `settle_s`, `lookahead_s` | spec | settle window 0..600 s (default 60; 30-59 and below 30 are priced), look-ahead 0 (off) or 3,600..86,400 s (default 21,600 when triggered) (8.1, 6.10) |
| `process` | spec | success labels a process the robot started may change during settle (8.1) |
| `fungible`, `informative`, `free_ok` | spec | roles where any instance is right (10.3); `{feature: reason}` features an answer may depend on (9.7); positive labels allowed to hold at t=0 on some seeds (8.6) |
| `battery_exempt`, `mustpass_exempt` | spec (`+` appends) | exemption proposals `{"policy": "B8.superset", "category", "reason", "seeds": "all"}`; battery categories `safe_superset`, `world_resolves`, `fungible_instance`, `budget_defense`, `questions_always_required`, `unrealisable`, `other`; must-pass `required_instance`, `required_place`, `order_matters`, `exact_wording`, `no_slack`, `unrealisable`, `other` (10.7, 11.3); acceptance lives in `catalog/review/exemptions.jsonl`, never in the spec |
| `budget_reason`, `scene_exception` | spec | reasons (30+ characters) for a budget looser than the formula (17) and for no look-alike group where B4 is expected (L-SCENE) |
| `clauses` | appendix specs | `[{"clause": text, "labels": [labels]}` or `{"clause", "waive": reason}]` covering `lint.clauses_of(entry text)` (L-CLAUSES) |
| `track_open`, `hold_limits` | spec | integrated open-time tracking of restore-class items (default true) and per-item hold limits in seconds (6.9) |
| `branch`, `budget` | variant | the variant's branch value (the `_branch` answer; default its index); a per-variant budget (9.1, 17) |
| `expect`, `mistake`, `mistake_fix`, `variants`, `modes`, `expect_budget` | wrong plans | required `expect` (failure patterns) and `mistake` (top-level step indices); `mistake_fix` (default: the aligned reference steps); the variants (indices) and modes (`own`, `eval`) the plan applies to; `expect_budget` a reason for a budget-only failure (14.1) |
| `key_claims` | user-simulator messages (setup) | claim patterns replacing keyword `key_facts` for relays (13.4) |
| `said` | predicate form | speech content `{"said": {"to", "asserts": [claim patterns], "forbids": [...], "heard", "audience", "after", "before_s", "same_utterance", "founded"}}` (13.2, kernel/speech.py); a claim pattern names `kind` (state, issue, none_found, quantity, time, commit, directive, refusal, attribution, location), `subject`, `value` and, for location claims and placement commits, `place`, `relation` (in, on), `place_kind` (room, entity); a factual claim (state, issue, none_found, quantity, location) counts only when founded (perceived first, 15.3); a denied ("it is not true that X"), voided or retracted claim asserts nothing |
| `"as_of": "observed"` | `report` / `report_set` bodies | compare with the truth at the supporting observation's time (15.2) |

**Timing items** (kernel/monitor.py, WP3): `trigger` and `act` are predicates or milestone names (a phase's item names
its own milestones first and is labelled `<phase id>/<label>`); `lo = t_T + not_before_s`, `hi = t_T + within_s` from
the first time t_T the trigger held. `hold`: `"end"` (default: the answering interval lasts to the end) or seconds (0:
no requirement); `if_no_trigger`: `"forbid"` (default: acting without a trigger is `unneeded`), `"require"` (no
trigger is `no_trigger`) or `"allow"`; `initially`: `"false"` (default: the act must be false at t=0 on every seed,
L-TIMING) or `"any"` (the interval holding at the window start is exempt). A negative `not_before_s` expresses
anticipation (`{"not_before_s": -900, "within_s": 0}`).

**Failure patterns** (`expect`, 14.2; a pattern p matches label L when `L == p` or L starts with `p + ":"`, for
every form `inv:` included -- never a raw prefix; the verifier's `expect_problems` and L-EXPECT reject the same
unspecific patterns):
`goal:<label>` (unmet success; `goal:<label>:absent` for the existence guard), `fail:<label>`, `flag:<name>` (a
global flag), `inv:<domain>.<name>[:<detail>]` (an invariant label: the name is a whole registered invariant name or
a label a registered invariant reports, `lint.invariant_labels()`; `inv:`, `inv:people` or `inv:people.unwanted` are
not specific), `house:<guard>:<id>` (a ledger or content guard for that entity, kind or resource), `timing:<label>[:<outcome>]`, `never:` / `always:` / `response:` / `deadline:` / `order:` + label,
`question:<label>`, `said:<label>`, `report:<kind>`, `strict:safety`, `strict:privacy`, `strict:authority`,
`perceive:<kind>[:<id>]`, `settle:<label>`, `later:<pattern>`, `budget` (only with `expect_budget`). A bare `goal`, a
`house:<guard>` without an id, an unknown label and a `flag:` pattern alone whose harm the plan's name does not
describe, and a `{placeholder}` the setup never binds are lint errors (L-EXPECT).

**Protect references** (`protect_map[].by`, 7.3): `house:<guard>[:<selector-or-id>[.<field>]]` (a ledger guard of
`kernel/ledger.py` GUARDS, optionally narrowed; not off, and not allowed by the envelope), `later:<reference>` (the
look-ahead must be on), `flag:<name>` (not in `allow`), `inv:<domain>.<name>[:<detail>]` (as in `expect`), `fail:` / `never:` /
`always:` / `timing:` / `response:` / `success:` + label, `preserve:<path>`, `question:<fact>`, `said:<label>`; a
suffix `@k,...` limits a reference to those variants. Every placeholder of a reference is bound by the setup and a
narrowed reference's selector resolves like an envelope selector (L-ENVELOPE below; the field is a field name). Every
analysis protect item appears exactly once (normalised
text); a `flag:` reference alone only for the harm its flag names; items about lights, or doors "as found", "left",
"closed" or "shut", need the matching `protections.restore` entry; a branch's items are covered in every variant of
that branch (L-PROTECT).

**Instruction roles** (13.7): a placeholder after a destination preposition (in, into, on, onto, to, under, inside,
back in) defaults to `destination` (a success predicate constrains the targets' placement to it, or their `to` scope
is exactly it); one in a success or failure predicate to `target`; a person to `recipient` (a report or `said` to
them, or a relay); `<x>_label` inherits `<x>`'s role. `context` after a destination preposition is priced and
reviewed; `answer` is never allowed.

**The lints** (`house_sim/tasks/lint.py`, `lint.run(spec, analysis_record, dynamic=None)`; each problem
`{"id", "severity", "message"}`; the verifier's S0 stops a spec on any error):

| id | rule |
|---|---|
| L-SCHEMA | a catalog id outside `legacy_ids.json` declares schema 4 |
| L-PROTECT | the protect map (above); liveness (7.6) is the verifier's |
| L-ENVELOPE | every selector and scope of the envelope and protections resolves (`lint.selector_problem`): its form is one of 6.6 (`{binding}`, an id, `type:` / `is_a:` / `category:` / `tag:` / `within:` / `room:`; `to` scopes also `baseline`, `rooms:work`, `home`, `owner`; `work_rooms` and `agents[].to` rooms), every placeholder is bound by the setup (on every seed of V with the verifier's facts), `type:` / `is_a:` name a registered type, a literal id exists on every seed of V (dynamic); a `category:` / `tag:` the registry and the setup never name is a warning; `deliver` / `consume` of a target need a success predicate naming it (of a non-target: priced); narrow `to` scopes on non-targets are flagged; a target's scope is its destination; a bare `type:` selector over more than 5 instances (warning) |
| L-WATCH | (domain tests) every written field and every type property classified (`lint.watch_problems`, `tools/field_census.py`) |
| L-LATCH | a success predicate reading a latched field (`sorted_ok`, `stream_ok`, `in_stream`, the domains' "Latched fields" tables in docs/domains) without a registered `@derived_field` |
| L-KIND | a `stats.*` counter is no success evidence |
| L-VACUOUS, L-WEAK | (dynamic) a seed whose not-yet-true predicates are only weak ones, or on which every predicate already holds; a positive predicate true at t=0 on every seed (on some seeds: a warning, `free_ok`); `orders` length without content, `contains` / `startswith` on a label text |
| L-EXISTS | every `absent_ok` label is a protective success predicate over an entity path |
| L-ANSWERS | answers are never told; entity / set / branch answers have evidence (entity / set a group); which entries must declare answers (primitives, "Check / Verify / Detect / Find / Report / Identify / Whether" entries, conditional entries a branch answer, `timing` a time answer, a binding varying over V that is a success value) |
| L-SCENE | (dynamic) a look-alike group for a success role on every seed where B4 is expected, unless `scene_exception` |
| L-BRANCH | a conditional entry has 2+ branches (each on 2+ seeds of V), a wrong plan per branch, and no two branches that pass each other's cross runs |
| L-QUESTIONS | asking scored: some seed or phase where a question is not necessary, or a `questions_always_required` exemption |
| L-TIMING | (dynamic) an `initially: "false"` act is false at t=0 on every seed |
| L-SPEECH | `told_count`, `facts.last_speech.to`, `received.*.complete` need a `said`; `reports.<id>._to` needs content on that report; free-text report fields are no evidence |
| L-ROLES | the roles above |
| L-REQUEST | no role tags ("Mia (9) says:") and no id-shaped words in instruction texts |
| L-PRIV | no `save_state` in references; `wait_until` without `poll_s` |
| L-EXPECT | the failure patterns above |
| L-NAMEDROOM | a failure predicate naming a room that is neither a work room nor in the instruction |
| L-CLAUSES | appendix entries map every clause |
| L-BUDGET, L-COVER | (dynamic) the formula budget (17) and coverage within 12 seeds (9.4) |
| L-ESCAPE | at most 15 escape points and 2 exemptions; an acceptance from an ineligible reviewer is an error (with `escapes.price`'s reason: an agent session that is not logged in `catalog/review/review_sessions.jsonl` or that checked the spec, a category other than the proposal's, a bad owner signature); a review escape without its accepted record yet, and an owner acceptance an authoring machine cannot verify (no split key; the gate checks the signature), are warnings (the verdict is then PENDING_REVIEW) |

Dynamic lints read `dynamic` (the verifier's facts over the verification seeds; `lint.dynamic_facts(spec, seeds)`
computes the build-time ones: bindings and their types, drawn instructions, which success predicates and timing acts
hold at t=0, look-alike group sizes, type counts, people, whether each literal id of the envelope, protections and
protect map exists). `lint.run(spec, record, as_if=True)` lints a legacy spec
as if it were schema 4 (the R0 audit). Escape prices are `house_sim/data/verify/escapes.json` (section 18).

## Concrete tasks

`runner.build` returns the task the environment and the evaluator read: the spec's evaluation keys with bindings
substituted, `instruction` (the drawn phrasing), `step_budget`, `time_budget_s`, `catalog_id`, `risk_level`,
`fidelity`, `variant`, and, when the spec uses them, `perception`, `camera`, `schema`, `request`, `phases` (each
with its resolved `request` and `instruction`), `never`, `always`, `responses`, `timing`. A schema-4 task also has
`labels` (the recorded success and failure labels) and the `hardening` block (`settle_s`, `lookahead_s`, `process`,
`absent_ok`, `envelope`, `protections`, `labels`, `answers`, `track_open`, ...) that the kernel's evaluation,
ledger, monitor and `HouseEnv.finish` read (runner.build, WP5). Paths use dot-separated keys and
list indices; ids are lowercase letters, digits and underscores (`hand`, `robot`, `floor`, `nobody`, `household`
are reserved). Structural cycles, disconnected layouts and invalid references are rejected before a run.
