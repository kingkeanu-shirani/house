# Task specification (schema 2 and schema 3)

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

## The spec object

| key | meaning |
|---|---|
| `catalog_id`, `title` | the catalog entry (required) and a short name |
| `schema` | 2 (default) or 3 |
| `extends`, `params` | a template from `tasks/templates/*.json` and its `{{param}}` values |
| `house` | generator options: `dwelling`, `require`, `residents` (`"minimal"`, `"random"`, a list), `pets`, `start`, `region`, `gen` (1 or 2) |
| `setup` | setup ops (`house_sim/tasks/setup.py`, docs/AUTHORING_GUIDE.md); they bind variables used as `{name}` everywhere else |
| `variants` | partial specs merged per seed (`seed % len(variants)`); `"+key"` appends to a list key |
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
`side_effects`, `milestones`, `deadlines`, `questions`, and appends for `"+key"` (`"+setup"`, `"+never"`,
`"+responses"`, ...).

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

Reference plans run strictly (any refusal raises); wrong plans run leniently. Skills may read privileged state to
route but change the world only through `env.step`.

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

* the required keys (`catalog_id`, `reference`, and `instruction` + `success` or `phases`), three phrasings, a wrong
  plan with `name` and `plan`, `risk_level` 0..3, `schema` 2 or 3;
* `phases`: unique ids, three phrasings per request, success per phase, known keys, a valid `start` (`after` an
  earlier phase, with `gap_s` / `robot` / `events` only there), `ends`, `supersedes`; no top-level `instruction`,
  `success` or `rubric` beside them;
* `request`: known keys, a known channel, a gesture with `point_at` or `place`;
* temporal keys: valid predicates, windows that name milestones or phases, responses with a trigger milestone or
  predicate (or `for_each_new`) and `within_s`, deadlines in one of the three forms;
* the camera rules: `"camera"` is room or geometric; a spec whose setup, plans or predicates use the camera ops,
  `face` / `peek` or `camera_log` declares `"camera": "geometric"`; a schema-3 geometric spec has a wrong plan
  marked `"camera": true` that fails for a camera reason.

Verify one file with `python -m house_sim --check FILE [--camera geometric] [--gen 2] [--perception realistic]`.
`python -m house_sim --verify [--verify-only ID|SECTION|FILE ...]` writes the catalog evidence; with `--gen`,
`--camera` or `--perception` it is a forced check of every selected spec under that mode (the M-GEN2 migration runs
`--verify --gen 2`), written to `reports/verification_forced.jsonl` beside the evidence, never over it.

## Concrete tasks

`runner.build` returns the task the environment and the evaluator read: the spec's evaluation keys with bindings
substituted, `instruction` (the drawn phrasing), `step_budget`, `time_budget_s`, `catalog_id`, `risk_level`,
`fidelity`, `variant`, and, when the spec uses them, `perception`, `camera`, `schema`, `request`, `phases` (each
with its resolved `request` and `instruction`), `never`, `always`, `responses`. Paths use dot-separated keys and
list indices; ids are lowercase letters, digits and underscores (`hand`, `robot`, `floor`, `nobody`, `household`
are reserved). Structural cycles, disconnected layouts and invalid references are rejected before a run.
