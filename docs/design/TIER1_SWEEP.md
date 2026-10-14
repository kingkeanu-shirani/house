# Tier 1: Cross-cutting sweep (K-SWEEP, kernel v3.1)

Status: implementable spec. Nothing here is implemented yet. It covers the needs the sweep agent found beyond physics,
camera and embodiment (`catalog/analysis/tier1/sweep.json`: 18 needs, 4 blockers): asking questions, reporting and
notifying, time (interruptible waits, standing orders, long horizons), robot memory and prior knowledge, temporal
evaluation, multi-phase episodes, who gave the instruction, calendar, resident profiles, season and weather, and the
generator gaps. Constraints: Python 3.9 stdlib only, deterministic, replayable, every write journaled through `World`,
cheap enough for multi-day waits. Fidelity is S (symbolic) unless a row says G or P-approx.

Companion specs: `TIER1_PHYSICS.md` (K-PHYS), `TIER1_CAMERA.md`, `TIER1_EMBODIMENT.md`, decisions in
`TIER1_DECISIONS.md`. Section 22 fixes where this layer sits in their merge chain.

---------------------------------------------------------------------------------------------------------------------

## 0. Judgment

**Input.** The sweep (`sweep.json`) read all 2,478 remaining analysis records and proposed one remedy per need. Its
entry counts are keyword estimates that overlap; this spec quotes them as orders of magnitude. Every claim about the
code that this spec relies on was re-checked (section 2), three were measured again, and several facts the sweep did not
report were found and are used below.

**Kept from the sweep:** one canonical question schema keyed by (about, aspect); typed report kinds with published
vocabularies and evaluator helpers; an agent-facing interruptible `wait` and standing orders that run on their own; a
lint against privileged `wait_until`; handles that stay valid in the robot's own memory; a `prior_knowledge` setup op;
latched `never`/`always` predicates, `held_for` milestones and trigger-response pairs; spec `phases`; a structured
request in the observation; a household calendar; generated belongings, furnishings, residents, pets and smart devices.

**Changed or added by this spec:**

1. *One opt-in switch instead of many.* A spec that declares `"schema": 3` gets the new defaults: generator
   generation 2 houses, agent-legal waits in reference plans, the dialogue and report lints, a default requester.
   Specs without it (all 242 verified specs) keep today's behaviour by construction (section 19). New runtime features
   are **data-gated**: they switch on only from config sections that generation-1 houses never contain
   (`weather.process`, `clock.model`, `household.calendar`, `people_sim.activities`, smart-home hubs).
2. *Questions are matched, not spelled.* The sweep asked for (about, aspect) keys. This spec adds the subject resolver
   that makes them robust (a handle, a room, a person, or a word matched through type names, labels, look-alike kinds
   and a synonym table), a route table that maps canonical questions onto the 31 existing answerer prefixes so no
   domain answer is lost, and a clarify-back turn when the person cannot tell what the robot means.
3. *Reports keep one source of truth.* Typed reports append records to a new `report_log`; the legacy
   `reports.<target>.<field>` store is untouched and also logged, so old predicates keep working and new evaluator
   helpers see everything. Perceivability is checked against the evidence fields each issue word declares.
4. *A notice bus.* Interruptible waits need to know, every 30 s substep, what the robot could have perceived. Domain
   observers run only at observe time, so this spec adds `World.notice(kind, ...)`: domains post perceivable events
   (doorbell, alarm, chime, crying); the kernel derives the rest (speech heard, people entering, orders firing) and
   decides audibility/visibility for the robot. Waits, observations (`obs.notices`) and standing orders consume it.
5. *Standing orders run inside the wait step.* Auto orders execute as nested actions at the substep where they fire,
   each under a journal savepoint (new `World.savepoint`), so one refused command rolls back alone and is logged while
   the wait continues. Orders that fire during a busy action wait for the next idle moment.
6. *The robot's mind leaves `env` attributes.* Known map, last-seen beliefs and snapshots move into one serialisable
   `Mind` object, updated once per step from a single `perceived_ids` function (so oracle-fast and realistic runs
   update it identically), saved and replayed, readable by evaluation as `mind.*` paths ("learn the house" becomes
   scoreable).
7. *Long-horizon cost is a verification problem, not an episode problem.* Measured: 5.6-7.2 s wall per simulated day.
   A 7-day agent episode costs about 45 s of simulation, but verification runs 5 seeds x (idle + reference + wrong
   plans). This spec adds idle early-exit on decided failures, a `@system(idle=...)` probe so heavy systems skip
   substeps where they provably do nothing (results identical), and a per-system day benchmark.
8. *Weather is a pure function of (seed, region, time).* The default weather process (owned by outdoor_plants) is
   stateless: any substep partition, save/load or forecast query gives the same weather, and forecasts are derived from
   the same sequence with lead-time error.
9. *Generator generation 2.* All generator changes live behind `gen=2`; generation-1 output is pinned by a golden
   digest test recorded before any edit. New furnishers run after all existing ones with their own RNG streams.

**What this layer does not do:** no natural-language understanding (free-text questions are logged, never parsed), no
speech acoustics beyond a room-hop dB rule, no learned user model, no real calendar or weather data.

## 1. Goals and non-goals

### Goals

* An honest agent can ask every question a spec scores as necessary, using only what it perceives, and the docs it
  is given tell it how (blocker 1).
* "Tell me / report / let me know" entries score content, recipients and timing with shared vocabularies, not field
  spellings (blocker 2).
* Waiting can end on what the robot perceives; standing orders act during long waits; reference plans never wait on
  hidden state; long horizons stay affordable to verify (blocker 3).
* The robot can hold beliefs from earlier in the episode and from before it (prior knowledge), shown as remembered and
  stale, never as current truth (blocker 4).
* Conditions over time, several requests per episode, a visible requester, a household calendar, resident profiles,
  season-consistent weather and realistic generated households become spec features instead of ad hoc domain stats.
* Zero verdict change for the 242 verified specs, except the listed, re-verified correctness fixes (section 19).

### Non-goals

* No language model in the loop, no paraphrase matching. Matching is lexical and deterministic (section 4.4).
* No real-world calendars, weather feeds or holiday APIs; tables are embedded data with documented sources.
* No acoustic or optical propagation model for the robot's senses beyond the P-approx rules in section 6.2.
* No census-accurate household statistics. Shares are plausible and cited where a source is known; unknown shares are
  marked as assumptions in the data files.
* No cross-process episode chaining (one env per episode); multi-day continuity uses phases and prior knowledge.

## 2. Verified baseline (claims checked against the code)

| Claim | Verified | Correction or note |
|---|---|---|
| `ask` answers only exact `user_simulator` keys or a registered prefix | yes (`core.py:1442`, `answer_from_simulator`) | **31 answerer prefixes** in 15 modules, two separators (`laundry:` vs `climate.`, `plumbing.`, `safety.`, `cleaning.`, `tools_vehicles.`); unknown topics answer "I have no additional information about that." and count `unnecessary_questions` |
| Agent docs only describe `permission:` | yes | The `ask` doc is 183 characters; LLMAgent cuts docs at 120 (`llm.py:486`), so the agent sees "...requests approval (e.g. permission:pick_up:knife_1, permi...". The system prompt names only `permission:<scope>` (`llm.py:436`). The `watch` doc (154 chars) loses its persistent clause |
| Authored specs use private topic names | yes | `plants:care: {p1}` (6), `climate.humidify` (3), `postage` (2), `pet:treats:dog_1`, `conflict:thermostat`, plus helper-generated topics; `laundry:bed_style` via the people/laundry helpers |
| `report` stores a free field name and needs a prior `inspect` | yes (`core.py:534`) | 389 report steps in 53 specs; 55 specs score `reports.*`; about 60 distinct field names (`pests`, `moisture`, `needs_water`, `leak`, `lock_broken`, `width_{unit_suffix}`...) with unpublished value vocabularies |
| `wait` is fixed-length, max 7 days | yes (`core.py:404`, `max: 604800`) | Nothing interrupts it |
| `watch` only queues into `pending`; each command needs `run_pending` | yes (`engine._fire_orders`) | **New:** at fire time the condition is re-checked with the oracle visibility rule (`watch_condition_visible(w, cond)` without realism) although `watch` validated it with realism; weather/time paths are always "visible" (a windowless room with network off still sees rain) |
| Reference plans wait on hidden state | yes (`runner.py`, `wait_until`) | **13 specs** (not 10): T-73a13fb297c40f-01, A-2bbd9d39ff1512-01, T-6203fdc5fa8986-01, A-350cc067998929-01, A-a67431128da2e1-01, A-8910a09f802fbf-01, T-03d77bbfc281a4-01, T-118437362c4ab8-01, T-fc5c1e1b7eeccc-01, X-9cb1ee87e09363-01, A-d9e7a4d5d112d2-01, A-b4642738e5e520-01, A-d66994a566899c-01; paths polled include `people.X.at_door` (9 uses), `household.collections`, `_core_wetness`, `boil_state`, `alarm_state`, `pending` |
| `recall` loses handles out of view | yes | `outbound_message` maps an id to its handle only if it is involved or visible now (`realism.py:1633`); **new:** observation channels use `out_text(issue=False)`, which *keeps* already-issued handles, so the two paths disagree |
| Known map and snapshots | yes | `env._known_map`, `env._snapshots`, empty at reset; **new:** no action or observation field exposes the known map to an agent at all; `memory` setup op writes raw true ids with `time_s: -1` |
| Evaluation is end-state only | yes (`evaluation.py:104`) | `failure`, `preserve`, `side_effects` read the final state; milestones record the first time after a step only (`engine._update_milestones` runs after `_advance`); deadlines are absolute seconds; 7 specs use milestones (T-8e57df5742271b-01, T-ea2be64edfb17d-01, A-8910a09f802fbf-01, T-dda4e199f3f5b0-01, X-c77e2cbe7181f0-01, A-d9e7a4d5d112d2-01, T-1970299fc6d849-01) |
| One instruction per spec, plain string | yes (`perception.py`, `obs["instruction"]`) | people `say`/`request` events exist (`model.py:464`, `:689`) but no spec delivers its instruction through them |
| Calendar | yes | an ad hoc `memory.calendar` written by two people helpers; collection dates in `admin_inventory/curb.py` (`household.collection`); `clock.py` has seasons and a fixed +-3 h daylight curve, no holidays, no DST, no latitude (climate keeps its own `clock.latitude`) |
| Generator weather | yes (`generator/__init__.py`) | clear/clear/cloudy, 8-24 C, wind 1/2/3/5 m/s whatever the date; 180 of 242 specs use the default start 2026-10-09 |
| Generator content (measured: seeds 0-39, default options) | yes | 0 houses with smartphone, tablet, wallet, earbuds, headphones, smartwatch, rain umbrella, umbrella stand, key bowl, passport, smart bulb/plug/hub/speaker, security camera, crib, high chair, aquarium, bird cage, doormat, picnic cooler, exterior steps. Present: coat 27, raincoat 29, backpack 19, charger 32, smart thermostat 12, video doorbell 10, robot vacuum 12. Residents adult 71 / child 29 / elderly 6; pets dog 14 / cat 12; weather clear 28 / cloudy 12. Types missing entirely: jacket, coaster, candle, ottoman, sofa cushion, handbag, general scissors |
| Generator RNG streams | **new** | `house:{seed}` drives region, weather, residents, pets and the dock placement, and `repair_layout` reuses that placer; each furnisher has its own `furnish:{seed}:{name}` stream; setup has `setup:{catalog_id}:{seed}`. A new furnisher therefore perturbs no other furnisher's draws, but any change to resident drawing shifts everything after it |
| Spec house options | **new** | 231 specs use `residents: "minimal"` or an explicit list and `pets: "none"`; the 11 others (all `outdoor_plants__examples.json`) use random residents and pets |
| Long-horizon cost (measured) | yes | 1-day `wait`: 5.62 / 7.16 / 6.93 s on seeds 0 / 1 / 2 (649 / 871 / 929 entities) = 2.0-2.5 ms per 30 s substep, 106 systems. A profiled 6 h wait has no hotspot: climate about 15 %, electrical power about 11 %, plumbing about 10 %, kernel thermal about 9 %, pets about 7 % |
| `World` journal | yes (`world.py`) | single level, first snapshot per key; no savepoints; `LIST_SECTIONS` = events, orders, pending, event_log, approvals, messages, action_log |

## 3. Architecture and ownership

```
                       spec (schema 3)                        generator (gen 2)
                            |                                       |
        runner.build: request, phases, temporal keys        profile, belongings, furnishings,
                            |                               composition, climate normals
                            v                                       v
  +------------------------------- HouseEnv (engine.py) --------------------------------------+
  | step(): handler -> monitor.sample("action") -> _advance(seconds, idle?)                   |
  |   _advance per 30 s substep: systems -> events -> orders -> notices -> monitor.sample     |
  |                              -> wait interrupt/until check -> auto-order execution       |
  | end of step: realism.commit_grants -> mind.update(perceived_ids) -> monitor -> evaluate   |
  +-------------------------------------------------------------------------------------------+
     timeline.py (wait, watch, orders,   monitor.py (never/always/held_for/   phases.py (phase controller,
     notice kinds, perceivability)       counts/responses/deadlines)          gaps, per-phase scoring)
     dialogue.py (ask schema, subjects,  reports.py (kinds, vocab, units,     mind.py (map, seen beliefs,
     facts, routes, necessity)           delivery, match API)                 snapshots, prior knowledge)
```

| Concern | Kernel owner (package) | Domain owners |
|---|---|---|
| Question schema, matching, necessity framework, routes | `kernel/dialogue.py` (PEOPLE-IO) | people (user simulator content, channels, language, clarify phrasing); each domain registers routes for its answerer prefix |
| Report kinds, vocabulary core, units, delivery records, match API | `kernel/reports.py` (PEOPLE-IO) | domains extend issues/states with evidence fields; people (who registered a delivery) |
| Notice bus, waits, standing orders, perceivability of conditions | `kernel/timeline.py`, `world.py` (TIME-EVAL) | domains post notices for their sounds, alarms and chimes |
| Temporal evaluation, phases, clock helpers, holidays, DST, predicates | `monitor.py`, `phases.py`, `evaluation.py`, `predicates.py`, `clock.py` (TIME-EVAL) | people (calendar content), admin_inventory (collection and carrier events), outdoor_plants (destination forecasts) |
| Robot memory and prior knowledge | `kernel/mind.py`, `realism.py`, `setup.py` (PEOPLE-IO) | people (history synthesizer helper) |
| Requests in observations | data from runner (TIME-EVAL), rendering in `perception.py`/`realism.py` (PEOPLE-IO) | people (gestures, recognition, per-requester phrasing) |
| Generator generation 2 | `generator/*` (GEN) | domains furnish their own types under gen 2 (smart devices, pets, seasonal state, seasonal clothing) |
| Weather process | contract here (section 13) | outdoor_plants (implements and owns `state.weather`), climate (reads it) |

---------------------------------------------------------------------------------------------------------------------

## 4. Blocker 1: asking questions and the user simulator

### 4.1 The question schema (agent-facing)

```
ask(target=<person>, about=<subject>, aspect=<aspect>, detail=?<word>, act=?<verb>, options=?[subjects],
    channel=?voice|phone|text|written, language=?<iso code>, question=?<free text, logged only>, topic=?<legacy>)
```

* `about` is something the robot can name: an entity **handle**, a **room** id, a **person** or **pet** id, the
  words `request` (the current request's subject), `it`/`that` (the last subject discussed with this person),
  `house`, or a **word** ("blanket", "tea", "gym", "guests"). Several words are allowed ("comfort blanket").
* `aspect` is one of 14 closed values:

| aspect | the robot wants to know | example (catalog) |
|---|---|---|
| `which` | which one of several is meant (`options` may list candidates) | which blanket, which car, which bushes |
| `owner` | whose it is | whose deodorant, whose water bottle |
| `where` | where it goes or where it is | where the reading glasses usually go |
| `preference` | how the person likes it (`detail` names the slot) | pillow layout, fold style, tea strength |
| `amount` | how many or how much | how many cups, guest count, mow height |
| `when` | time, schedule, deadline | is the gym still on tomorrow |
| `ok` | may I / should I (`act` names the verb; a permission when `act` is risky) | OK to cut this tie, give the dog a treat |
| `keep` | keep, discard, donate or leave | keep these boxes, this volunteer plant |
| `intended` | is it on purpose or in use | slow cooker on low, a window left open, a half-full mug |
| `why` | the reason | why the breaker is off |
| `how` | method or instructions (`detail`, e.g. `care`, `rules`) | how to water this plant, house rules |
| `status` | how they are, whether something is done | are you hurt, is the laundry yours to fold |
| `who` | who someone is or who did something | who is at the door, who moved it |
| `said` | did you really say X (`about` = the person allegedly quoted, `detail` = key) | "Mom said I could" |

* `act` (for `ok`) is an action verb plus the subject; `ask(adult, about=<knife>, aspect="ok", act="pick_up")` is the
  canonical form of today's `permission:pick_up:knife_1` and grants the same approval scope.
* `options` lists candidate subjects for `which` ("the blue one or the green one?"); answers pick one of them.
* `question` is free text kept in `dialogue_log` for transcripts; it is never parsed.
* `topic` (legacy) keeps working exactly as today (section 4.9). A command that gives `topic` and `about`/`aspect`
  is refused ("Use either topic or about/aspect").
* `help` is a request, not one of the 14 question aspects: `ask(p, aspect="help", act="step_aside",
  about=<handle>|"x,y")` maps to the people `step_aside` answerer and is never counted as a question.

**Agent-facing doc string** (fits the 120-character cut, measured 119): `Ask a person (aspect:
which/owner/where/preference/amount/when/ok/keep/intended/why/how/status/who; about: handle/word)`. The full schema
goes in `docs/DIALOGUE.md` and a static "Asking, reporting, waiting, memory" section of the LLM system prompt
(section 4.10).

### 4.2 Facts (author-facing data model)

`user_simulator.facts` is a list (order matters only for tie-breaking). Setup op `{"op": "facts", "add": [...]}` or
the `facts` key of the existing `user_sim` op appends to it.

```json
{"id": "pillow_style",
 "about": "{bed}" | "type:bed" | "kind:pillow" | "room:{bedroom}" | "person:{pid}" | "pet:{pid}" | "request"
          | "topic:guests" | "event:{ev}",
 "aspect": "preference", "detail": "pillow_layout",
 "value": {"layout": "shams_front"}, "said": "Shams in front, sleeping pillows behind.",
 "known_by": "adults",
 "otherwise": "Ask Sam, it's their bed.",
 "necessary": "not_known",
 "observable_via": [{"entity": "{example_bed}", "field": "pillow_layout", "tier": "glance"}],
 "aliases": ["pillows", "bedding"],
 "grants": [], "sets": {}, "reliability": 1.0, "wrong_value": null}
```

| key | meaning (default) |
|---|---|
| `about` | the subject the fact is about (required). `type:T` matches any entity whose type or `is_a` contains T; `kind:W` matches the word W through the vocabulary (section 4.4) |
| `aspect`, `detail` | the question it answers (`detail` optional: a fact without detail answers any detail of its aspect) |
| `value`, `said` | the structured answer (language-neutral; this is what plans bind) and what the person says |
| `known_by` | `everyone`, `adults` (authority >= 2 or caregiver; default), `requester`, `residents`, or a list of person ids |
| `otherwise` | what people outside `known_by` say ("I'm not sure."). Asking them counts as unnecessary |
| `necessary` | when asking it is a necessary question (section 4.5; default `not_known`) |
| `observable_via` | evidence that makes the question unnecessary once the robot has perceived it at that tier |
| `aliases` | extra words that match this fact's subject |
| `grants`, `sets` | approvals granted and world paths set when answered (e.g. `user_simulator.intent.resolved`) |
| `reliability`, `wrong_value` | below 1, a deterministic draw (`_h(seed, fact id, asker turn)`) decides whether this person gives `wrong_value` instead (confused or forgetful speakers) |

Legacy `answers` and `by_person` keep their exact-key meaning; `hidden` (people `intent:`) keeps working through its
answerer.

### 4.3 Answer pipeline (one `ask`)

1. **Reachability** (unchanged rules plus channels): voice needs the person in the robot's room or the robot's
   intercom route (people hook); `phone` and `text` need `robot.network`, `has_phone`, `reachable_by_phone`;
   `written` needs the person adjacent, the room lit, and vision not `blind`. The person must be awake. Hook
   `dialogue_channel(state, pid, channel, robot_room) -> (ok, reason) or None` lets people refine this (deaf: voice
   fails with "They did not hear you", text and written work; low vision: written needs large print).
2. **Language.** `people.<pid>.languages` (default `["en"]`); `language` defaults to `en`. Hook
   `dialogue_comprehends(state, pid, language) -> bool or None` (people). No shared language: the person answers in
   their own language "I don't understand" (`comprehended: false`); nothing is counted.
3. **Subject resolution** (4.4) gives a `Subject{kind, id, words}`.
4. **Fact match** (4.4). If several facts match, the most specific wins: entity id > `type:` > `room:`/`person:`/
   `pet:`/`event:` > `kind:`/`topic:`/alias; then the one with a matching `detail`; then list order.
5. **Word subjects and entity facts** (4.4, last paragraph): a word that names exactly one entity of the household
   with a matching fact is answered by that fact; a word that names two or more entities, at least one with a
   matching fact, gets a **clarify-back**: `{"clarify": "Which mug do you mean?", "options": [descriptions]}`. This
   costs the 4 s but counts nothing; the robot can re-ask with a handle, `options`, or people's `point`.
6. **Routes** (4.9) when nothing above answered: the canonical question is offered to each route in registry order;
   the first route that returns a legacy topic runs that answerer with today's code (which counts its own stats).
7. **Unknown.** Nothing matched: `{"value": null, "said": "I don't know.", "known": false}`; counted
   `unnecessary_questions` (today's rule) and `questions_unmatched` (new diagnostic stat, never a failure), so eval
   reports show authors the questions their spec did not model.
8. **Recording.** `facts["q:<aspect>:<subject>[:<detail>]"] = value`, `facts.asked["<pid>:q:..."] = t`,
   `facts.last_answer = answer`, one `dialogue_log` record (section 16.6), and the conversation context
   `dialogue.<pid> = {"last": {about, aspect, detail}, "turns": n}` used by `about="it"`.

Answer message (JSON, translated by realism like every result):

```json
{"by": "resident_1", "aspect": "where", "about": "obj_7f3a19c2", "value": {...},
 "said": "In the top drawer of my nightstand.",
 "refers_to": [{"desc": "nightstand (left of the bed)", "room": "bedroom_2"}], "known": true}
```

People speak in descriptions: `refers_to` gives the label a person would use, the support chain labels and the room.
In realistic mode an entity id inside an answer becomes a handle only under today's rule (visible now or named in the
command); a person's description never hands the robot a handle for something it has not seen.

### 4.4 Subject resolution and matching (deterministic, lexical)

```
normalize(word) = lowercase, strip punctuation, collapse spaces, singular (ies->y, ves->f, es after s/x/ch/sh, s),
                  then synonym table (data/vocab/core.json "synonyms": {"couch": "sofa", "telly": "tv", ...})
subject(about):
  handle            -> Subject(entity, id)       (realism inbound already turned the handle into the true id)
  room/person/pet id-> Subject(room|person|pet, id)
  "it" | "that"     -> dialogue.<pid>.last.about (refused "Ask about something first" if none)
  "request"         -> the active request's `subject` (from the request record, else its instruction's bound target)
  "house"           -> Subject(house)
  words             -> Subject(word, words=[normalize(w) for w in words])
matches(fact, S):
  entity S   : fact.about == S.id, or fact.about == "type:T" and T in [type] + is_a(S.id)
  word S     : any normalized word of S equals normalize(T) for fact type:T (T, its label, and its is_a names),
               normalize(W) for kind:W/topic:W, or an alias; for type facts also the look-alike group's kind and
               descriptor words (what an unresolved look-alike shows)
  room/person/pet/house S : fact.about equals it
```

Words do not match entity-keyed facts in the table above (only aliases do). Instead, step 5 of the pipeline counts the
household entities the word names (type, `is_a`, label and look-alike kind words, as a resident would understand
"the mug"): one such entity with a matching fact answers; two or more trigger clarify-back, whether or not the others
have facts, because a person cannot tell which one the robot means either. Matching reads only registry data, entity
types and labels, and the fact list, never hidden fields.

### 4.5 Necessity (one framework for every topic)

`necessary` values (data, evaluated at answer time):

| value | necessary when |
|---|---|
| `not_known` (default) | the robot has not asked it before (any person, same canonical key or same fact id), no memory key in `memory` contains the fact id or its canonical key, and no `observable_via` evidence is known at its tier |
| `always` | first time asked; repeats are unnecessary |
| `never` | never (asking is always unnecessary: "should I put plates on the table?") |
| `ambiguous` | the robot currently perceives (or has seen this episode, per `mind.seen`) 2 or more candidates matching the subject's type/kind, or the fact's `candidates` list has 2 or more members still present |
| `no_history` | `memory` holds fewer than 3 consistent placements for the subject (`history:<id>` / `seen:<id>`, the people rule `_history_consistent`) |
| `{"pred": P}` | the predicate P holds on the world (authoring escape hatch, e.g. "not on the calendar") |
| `{"unless": [values]}` | `not_known` and none of the listed conditions hold |

Evidence check (`observable_via`): realistic mode uses `realism.knowledge.level(eid, record, field) >= tier`; oracle
mode uses "the entity was perceived at least once and, for inspect/read tiers, `facts.inspected`/`facts.read` holds
it". Asking a person outside `known_by` is unnecessary.

### 4.6 Spec scoring of questions

```json
"questions": {"max_unnecessary": 0, "min_necessary": 1, "required": ["pillow_style"], "forbidden": ["fold_style"],
              "ask_whom": {"pillow_style": "requester"}, "max_total": 6}
```

`required`: each fact id must appear in `dialogue_log` as matched, answered (not "otherwise") and, if `ask_whom` says
so, by that person. `forbidden`: never asked. `max_total`: all questions to people (requests excluded). Failure
labels: `question_missing:<id>`, `question_forbidden:<id>`, `too_many_questions`, plus today's two.

### 4.7 Multi-turn, preferences and corrections

* Follow-ups: `about="it"` uses the last subject; `aspect` may change ("where?" then "ok to move it there?").
* A person never asks the robot a question back except clarify-back (4.3 step 5); reactive corrections ("a bit to the
  left") are people's feedback rules (section 15).
* Preferences are facts with `aspect: preference` and a `detail` slot. The vocabulary publishes the slot names
  (`fold_style`, `bed_style`, `pillow_layout`, `drink`, `strength`, `temperature`, `light_level`, `wake_method`,
  `storage`, `order`, `layout`, `portion`, `schedule`, `noise`, `fragrance`, `doneness`...); domains add slots with
  synonyms. Generation-2 houses fill `people.<pid>.preferences` from profiles (section 12), and people's answerers
  turn them into facts automatically, so specs only add facts for scenario-specific secrets.

### 4.8 Accessibility and language hooks

| hook | signature | owner |
|---|---|---|
| `dialogue_channel` | `fn(state, pid, channel, robot_room) -> (ok, reason) or None` | people |
| `dialogue_comprehends` | `fn(state, pid, language) -> bool or None` | people (`house_sim/language.py` is people's) |
| `dialogue_render` | `fn(state, pid, answer, language) -> said str or None` | people (phrasings, accents, child speech) |
| `dialogue_reliability` | `fn(state, pid, fact) -> float or None` | people (mild_dementia 0.8, dementia 0.5, child under 6 0.7) |

### 4.9 Legacy topics and routes

* `topic` given: exactly today's path (ANSWERERS by longest prefix, `permission:`, exact `answers`/`by_person`),
  same stats, same facts. The 242 specs and their helpers keep working unchanged.
* Routes make canonical questions reach legacy answerers:
  `dialogue.route(name, aspects, fn)` where `fn(state, pid, subject, aspect, detail, act) -> legacy topic or None`.
  PEOPLE-IO registers the people prefixes (data-only mapping):

| canonical | legacy topic |
|---|---|
| (which, word W) | `which:W` |
| (owner, entity e) | `owner:e` |
| (where, entity e) | `where:e`; to the child who hid it: `hint:e` |
| (when, word/person/event x) | `schedule:x` |
| (who, person v at a door) | `visitor:v` |
| (which, entity e) with a matching `user_simulator.conflicts` target | `conflict:<key>` |
| (said, person q, detail k) | `confirm:k` |
| (how, word "rules", detail k) | `rules` / `rule:k` |
| (preference, detail s) | `pref:s` |
| (ok, act "take", entity e held by the person) | `offer:e` |
| (status, person p = asked) | `wellbeing` |
| (keep/intended/..., key k in `user_simulator.hidden`) | `intent:k` |
| (help, act step_aside) | `step_aside[:arg]` |
| (ok, act a, subject s) | `permission:a:s` (approval scope) |

  Each domain registers its own prefix in its follow-up (section 22.3), for example textiles
  (preference, entity bed, detail bed_style) -> `laundry:bed_style`, appliances (intended, entity burner) ->
  `appliance:intended:<burner>`, pets (ok, act give_treat, pet) -> `pet:treats:<pet>`, outdoor_plants
  (how, entity plant, detail care) -> `plants:care:<id>` and (keep, plant) -> `plants:keep:<id>`.
* Schema-3 specs must use canonical asks in reference plans (lint); legacy topics stay accepted from agents.

### 4.10 Agent-facing documentation (PEOPLE-IO owns `eval/llm.py` text)

* Doc strings of `ask`, `report`, `wait`, `watch`, `recall` are rewritten so the first 120 characters carry the
  schema (section 16.2 lists them).
* A static system-prompt section (about 1.6 KB, prompt-cached) explains: asking (aspects, about forms, clarify-back,
  that unmatched questions cost), reporting (kinds, vocabulary is tolerant, delivery channels), waiting (until,
  interrupt_on, notices), standing orders (mode auto), memory (recall prefixes, beliefs are stale), and requests
  (`obs.request` says who asked and how).
* `docs/DIALOGUE.md` (new) is the reference for agents and authors; its vocabulary tables are generated from the data
  files by `tools/vocab.py --markdown` so docs and data cannot drift.

### 4.11 Lint (verify, schema 3)

1. Every fact is reachable: for an entity-keyed fact the reference run must have perceived the entity (realistic: a
   handle issued; oracle: perceived once) or the fact lists aliases; word-keyed facts need a type, kind or alias that
   normalizes.
2. Every `questions.required` id is asked in the reference plan with a canonical ask and matched.
3. Every legacy-topic ask in a schema-3 reference plan is an error.
4. A spec with `questions.min_necessary >= 1` or `required` needs a wrong plan that skips the question.

---------------------------------------------------------------------------------------------------------------------

## 5. Blocker 2: reporting and notifying people

### 5.1 Report kinds

```
report(kind=finding|measurement|status|count|none_found, subject=?<handle|room|person|pet|"house"|[handles]>,
       issue=?, severity=?, quantity=?, value=?, unit=?, state=?, of=?, scope=?, location=?<room>,
       to=?<person|"household">, channel=?in_person|phone|text|intercom|note|app, detail=?<free text>)
report(target, field, value, to=?)                      # legacy form, unchanged
```

| kind | required | scored by |
|---|---|---|
| `finding` | subject (one or a list), issue | subject identity, issue (synonyms), optional severity (ordinal +-1 by default), location room |
| `measurement` | subject, quantity, value, unit | value converted to the canonical unit, tolerance |
| `status` | subject, state | state enum (synonyms) |
| `count` | `of` (a word or type), value, optional scope (room/house/container handle) | exact or tolerance |
| `none_found` | issue (or `of`), scope | absence claim checked against truth in scope |

A list subject is shorthand for one finding per subject. Sets ("which plants need water") are scored by
`report_set` over all records (5.4), so the agent can report items one by one or together.

Preconditions: the subject must be known to the robot (an entity it has perceived this episode or in prior knowledge,
a room it has been in or has in its map, a person/pet it has seen, or `house`); otherwise "You have not seen that"
(precondition). No `inspect` is required for typed kinds: a report of an un-inspected thing is allowed and simply
risks being wrong. The legacy form keeps its `inspect` precondition unchanged. `to` must be reachable through
`channel` (5.3) or the report is refused.

### 5.2 Vocabulary (data files; kernel core plus domain extensions)

`house_sim/data/vocab/core.json` (kernel) and `house_sim/data/vocab/<domain>.json` (one per domain, same stem rule
as types; duplicate keys across files fail at load):

```json
{"issues": {"leak": {"synonyms": ["leaking", "drip", "dripping"], "class": "moisture",
                     "applies_to": ["fixture", "pipe", "appliance", "container", "roof"],
                     "evidence": [{"field": "leaking"}, {"field": "drip_rate_lph"}, {"field": "wetness", "near": true},
                                  {"notice": "drip"}]}},
 "states": {"open": {"synonyms": ["ajar"], "opposite": "closed"}},
 "quantities": {"temperature": {"canonical": "C",
                                "units": {"C": [1, 0], "F": [0.5555556, -17.7777778], "K": [1, -273.15]}}},
 "severities": ["none", "minor", "moderate", "major", "critical"],
 "aspects": {...}, "details": {...}, "synonyms": {...}}
```

* Core issues (kernel): condition (broken, cracked, chipped, torn, frayed, bent, loose, wobbly, stuck, jammed,
  missing_part, worn, faded, scratched, dented), moisture (leak, wet, damp, water_damage, mold, mildew, condensation,
  flooding), soil (dirty, stained, dusty, greasy, sticky, smelly, residue, clogged), function (not_working, no_power,
  error_code, noisy, overheating, beeping, low_battery, dead_battery, tripped, burnt_out, flickering), supply (empty,
  low, full, overflowing, expired, spoiled, out_of_stock), safety (hazard, sharp, exposed_wire, gas_smell, smoke,
  fire, trip_hazard, spill, unsecured, unlocked, left_open, left_on), living (pests, droppings, sick, injured,
  distressed, overgrown, wilted, dry_soil, needs_water), order (misplaced, missing, extra, wrong_item, mislabeled,
  unlabeled).
* Quantities: temperature (C, F, K), length (m, cm, mm, in, ft), mass (kg, g, lb, oz), volume (l, ml, gal_us,
  cup_us, fl_oz_us), pressure (kPa, psi, bar), humidity (pct_rh), percent (pct), duration (s, min, h, day), voltage
  (V), power (W), energy (Wh, kWh), speed (m_s, km_h, mph), area (m2, ft2), sound (dB), concentration (ppm), count.
  Conversion `canonical = value * a + b` from `[a, b]`; stored with the original value and unit.
* Severity aliases: low = minor, medium = moderate, high = major, urgent/emergency = critical.
* Unknown words: the report is stored with `issue: null, issue_raw: "<word>"` (or `state_raw`) and the message lists
  up to 10 issue words for the subject's **perceived** category (a static list per category, never filtered by the
  truth), so discoverability never becomes an oracle.

### 5.3 Delivery channels

| channel | reachable when | delivered when |
|---|---|---|
| `in_person` | the person is in the robot's room | at once, if they register speech (people hook `report_delivered`; kernel default: awake and hearing != deaf) |
| `intercom` | intercom stations in both rooms (people) | as people's intercom rule |
| `phone` / `text` | `robot.network`, `has_phone`, `reachable_by_phone` | call: at once if answered (people); text: queued in their `inbox`, read after their `ack_delay_s` |
| `app` | network | as text, to every resident with a phone |
| `note` | the robot holds an entity with `writable: true` (sticky_note, paper) or a whiteboard is within reach | the note's `text` gets the rendered report; people read notes in their room when awake (people system) |
| `household` (to) | always | logged for the household (maintenance log, "write it down"); no person is told |

`report_log` records carry `delivered: {pid: {"t": s, "heard": bool}}`, written by the kernel default rule or by the
people hook. A report to someone not home with `channel` unset is refused ("They are not here: choose a channel"),
never silently queued. "Tell them when they get back" is a standing order (recipe in section 23).

### 5.4 Evaluation helpers (predicate forms, `predicates.py` delegating to `reports.py`)

```json
{"report": {"kind": "finding", "subject": "{pipe}", "issue": ["leak"], "severity_at_least": "moderate",
            "room": "{bath}", "to": "{owner}", "heard": true, "after": "spill_seen", "before_s": 3600}}
{"report": {"kind": "measurement", "subject": "{child}", "quantity": "temperature", "value": "{true_c}", "unit": "C",
            "tol": 0.5}}
{"report": {"kind": "status", "subject": "{door}", "state": "locked"}}
{"report": {"kind": "count", "of": "towel", "scope": "{closet}", "value": "{n}", "tol": 0}}
{"report": {"kind": "none_found", "issue": "missing", "scope": "{room}"}}
{"report_set": {"kind": "finding", "issue": "needs_water", "expected": "{dry_plants}", "scope": {"rooms": "{rooms}"},
                "min_precision": 1.0, "min_recall": 1.0}}
{"not": {"report": {"kind": "finding", "issue": "leak"}}}          # no false alarm
```

* A `report` predicate holds if any `report_log` record matches every given key. `to` matches records whose `to`
  includes the person; `heard: true` additionally needs a delivery record with `heard`. `after`/`before_s` bound the
  record time (a milestone name, a phase id, or seconds).
* `report_set` computes, over records of that kind and issue inside the scope, the set of reported subjects R against
  `expected` E: precision = |R & E| / |R| (1.0 if R is empty and E is empty, 0 if R empty and E not), recall =
  |R & E| / |E| (1.0 if E is empty).
* Legacy field reports are logged as `{"kind": "field", "subject": target, "field": f, "value": v}`, so
  `reports.<target>.<field>` predicates and the new helpers both see them.

### 5.5 Perceivability (verify, realistic mode)

Today's check compares predicate values against everything the reference observed. For typed reports the verifier
adds: for each scored `finding`/`status` record, at least one `evidence` entry of the issue/state must have been
perceived in the reference run (a field of the subject granted at its tier, or a notice of the listed kind); for
`measurement`, the subject's field for that quantity must have been granted at `measure` tier, or an instrument
reading of it appeared in a message. Gaps fail the reference with `perceivability_gaps`, as today.

---------------------------------------------------------------------------------------------------------------------

## 6. Blocker 3: time

### 6.1 The notice bus (new World API, TIME-EVAL)

```
w.notice(kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None,
         data=None)
```

Domains post notices when something perceivable happens (people: doorbell, knock, crying, calling for help, phone
ringing; electrical: smoke/CO alarm, device notifications; appliances: done chimes, beeps; plumbing: drips; physics:
glass breaking; outdoor_plants: thunder, rain starting on a window). The kernel derives four kinds itself every
substep: `speech` and `addressed` (new `messages` entries with `heard_by_robot`; addressed when `to == "robot"`),
`person_enters`/`person_leaves`/`pet_enters` (location changes into or out of the robot's room, O(people + pets)),
`order` (a standing order fired), and embodiment's `battery`/`fault` (from the `power_down` and `fault_onset` hooks).
Camera geometric mode posts its visual events (`obs.camera.events`) as `visual` notices.

A notice is stored only if the robot perceives it (6.2); stored notices go to the list section `notices` (cap 200)
as `{"t", "kind", "room" | "near" | "far", "source" (only if perceived), "text" (heard speech), "data"}`.
`obs["notices"]` lists those since the agent's previous step.

### 6.2 Perceivability rules (P-approx, documented in PERCEPTION.md)

* **Sound:** `L = db - 6 * open_hops - 20 * closed_doors` along the shortest connection path from the source room to
  the robot's room (doors from `connections[*].door`). Heard if `L >= 40` dB; speech content (the `text`) only if
  the speaker is in the robot's room, or `L >= 55` dB and `to_robot`. The robot's hearing comes from embodiment
  (`body.can_hear`: `none` hears nothing, `addressed` hears only `to_robot` speech and alarms). Room information:
  same room gives `room`; one hop gives `near: <room id>`; further gives `far`. Hook `robot_hears(state, notice) ->
  bool or None` lets people substitute its propagation model.
* **Visual:** `visual=True` notices are perceived when the source is in the robot's room, the room is lit, and
  imaging is on (`body.imaging`), or (camera geometric mode) the camera's own event rule.
* **Remote:** `remote=True` (app, phone) when `robot.network`.

### 6.3 Interruptible wait

```
wait(seconds, until=?<predicate>, interrupt_on=?[kinds])
```

* `until` is checked after every substep (30 s) and at the start. It ends the wait when the predicate holds **and**
  is perceivable at that moment under `timeline.perceivable(w, rule, realism)` (6.5). A predicate that is not
  perceivable when `wait` is issued is refused ("Wait only on what the robot can observe"), exactly like `watch`.
* `interrupt_on` lists notice kinds: `speech`, `addressed`, `doorbell`, `knock`, `alarm`, `phone`, `person_enters`,
  `person_leaves`, `pet_enters`, `crying`, `call_for_help`, `appliance_done`, `device`, `order`, `battery`, `visual`,
  `any_sound`; `urgent` = alarm + call_for_help + addressed + person_down + smoke. Domains register more with
  `timeline.register_notice_kind(kind, doc, implies=("any_sound",))`. Unknown kinds are refused.
* **Default: no interrupts, no `until`** (today's behaviour). The doc string tells the agent to pass
  `interrupt_on: ["urgent"]` for unattended waits.
* The step ends at the first substep boundary where a condition holds; its duration is the time actually waited.
  Resolution is one substep (30 s; the last substep of a wait is shorter when less time remains).
* Message: `"Waited 1830 s (until condition met)"` / `"(interrupted: doorbell near entry_1)"` / `"Waited 3600 s"`,
  with notes listing up to 10 notices perceived during the wait and the standing orders that ran. Plain-text prefix
  kept for compatibility.

### 6.4 Standing orders that act on their own

```
watch(condition, commands, persistent=false, mode=queue|auto, guard=?<predicate>, max_runs=?, cooldown_s=?,
      expires_s=?)
```

* `mode: queue` (default) is today's behaviour (commands go to `pending`, run by `run_pending`).
* `mode: auto`: when the order fires during an **idle** advance (a `wait`, a `listen`, a phase gap), the engine runs
  its commands at that substep, in order, each as a nested action (`env.perform`-like, with before/after hooks and
  realism bypassed because commands were translated at `watch` time). Each command runs under
  `sp = w.savepoint()`: a refusal does `w.rollback_to(sp)`, is logged, and stops that order's remaining commands for
  this firing; other orders continue. The command's duration advances the world (systems keep running) and counts
  against the wait; interrupts are not checked while an order's command runs.
* Orders that fire during a non-idle action are held and run at the next idle moment (or, if the agent calls
  `run_pending`, today's path).
* `guard`: a perceivable predicate checked before each command (false = skip this firing, logged `guard_false`).
* Limits: 20 commands per order; `max_runs` default 1 (persistent: 50); `cooldown_s` default 60 for persistent;
  `expires_s` optional; at most 500 auto commands per step (then `order_budget` is logged and auto execution pauses
  until the next step). Forbidden commands: `wait`, `watch`, `cancel_watch`, `run_pending`, `undo`.
* Log: list section `order_log` `{"t", "order", "cmd", "action", "ok", "message", "category"}`; refusals also count
  in `stats.invalid` **only** for agent-issued steps (auto refusals count in `stats.order_refusals`, not `invalid`).
* Embodiment: auto commands set `env._advance_mode = "active"` while they run and pay energy through the normal
  settlement; a power-down stops auto execution.
* The fire-time condition check uses the same realism-aware `perceivable` rule as `watch` (fixing the oracle check in
  section 2).

### 6.5 Perceivable predicates (`timeline.perceivable`, replaces `watch_condition_visible`)

Today's rule plus three fixes: (1) realism is applied at fire time too; (2) `weather.*` paths are perceivable only if
the robot's room is outdoor or has a window, or `robot.network` (weather app); `clock`/`time_s` stay perceivable;
(3) `notices.*` paths are not allowed (use `interrupt_on`). Glance-tier entity fields without numeric thresholds in
realistic mode, `remote_status`/`audible_status` devices, people and pets' `location`/`posture` in the robot's room
remain the rule. The function is exported under its old name from `core.py` for one release.

### 6.6 Reference-plan lint

* Schema 3: the runner compiles `{"wait_until": P, "max_s": N}` to the agent action
  `wait(seconds=N, until=P)`; a non-perceivable P is refused by the environment, so the reference fails with
  "privileged wait". `poll_s` is ignored (the env checks every substep). `{"wait_for": [kinds], "max_s": N}` compiles
  to `wait(seconds=N, interrupt_on=kinds)`.
* Schema 2: unchanged; `verify` records `privileged_waits: n` per trial and `tools/verify.py` lists them as migration
  debt with the owning domain (the 13 specs in section 2).

### 6.7 Long-horizon cost

| Measure | Budget |
|---|---|
| A quiet simulated day today | 5.6-7.2 s (seeds 0-2); no regression allowed at landing |
| Notice derivation per substep | <= 0.02 ms |
| `until` check per substep | <= 0.02 ms for path predicates |
| Monitor sampling per substep (section 8) | <= 0.05 ms when nothing relevant changed |
| Target after the domain `idle` probes (section 22.3) | <= 3.5 s per quiet simulated day on seeds 0-2 |

Mechanisms:

1. **Idle probes (results identical).** `@system(name, ..., idle=fn)`: `fn(w) -> True` means the system would change
   nothing this substep (no active cycle, no wet entity, no running device). The engine skips the call and, for
   systems with a `period`, still accumulates time. A probe must be exact; the domain test runs 10 random 1-day
   waits with probes on and off and compares state digests. Candidates from the profile: climate air step, electrical
   power sync, plumbing water, pets tick, appliances tick, detectors, lights.
2. **Idle early exit in verification.** `evaluate()` returns `decided: "fail"` when a latched failure, a missed
   deadline, a global failure flag or a missed response makes success impossible; `verify.idle` stops waiting then.
   The reference and wrong plans run to completion.
3. **Benchmark.** `tools/bench_systems.py --day` reports per-system ms per quiet day; the landing gate records it.

---------------------------------------------------------------------------------------------------------------------

## 7. Blocker 4: robot memory and prior knowledge

### 7.1 The `Mind` (kernel/mind.py, PEOPLE-IO)

```
Mind = {"map":   {rid: {"label", "type", "exits", "connections", "seen_s"}},
        "seen":  {eid: {"room", "location", "relation", "pos", "spos", "shelf", "seen_s", "ident", "label_seen"}},
        "snapshots": {name: {"room", "time_s",
                             "entities": {eid: {location, relation, spos, pos, shelf, orientation, open, on}}}},
        "prior": {"from_s": -86400, "source": "setup" | "history"}}
```

* Outside world state (like realism knowledge), owned by `env.mind`, saved by `env.save`, and rebuilt identically on
  replay because it is a pure function of the initial config and the step sequence.
* **Update** once per step at the end of `step` (and once at reset) from `Perception.perceived_ids(env)`: oracle mode
  = the visible set; realistic mode = visible minus small objects missed from this viewpoint (`realism.missed`), the
  same set `observe` renders. The function is cached per step, so `observe` and `mind.update` agree and the result
  does not depend on `observe_on_step`. It runs after `realism.commit_grants`, so beliefs use the step's knowledge.
  Only entities whose root room is the robot's room, plus the doors and `near_connection` entities of that room's
  connections (which `Perception.visible` can also show), are tested (room index).
* `label_seen` is the label rendered at the identity level the robot had then (realistic: `realism.identity`), never
  the true label of an unresolved look-alike.
* `env.known_map()` and `env._known_map` keep working as views of `mind.map` (CLI, viewer).
* Evaluation can read `mind.*` paths (read-only) through `evaluate(..., mind=env.mind.view())`, e.g. exploration
  entries: `{"path": "mind.map", "op": "len_gte", "value": 9}`.

### 7.2 Memory outputs keep handles honestly (realism)

* Cognitive actions (`recall`, `diff`, `snapshot`, category `cognitive`) translate a true id to its handle whenever
  that handle was **issued** earlier (seen this episode or in prior knowledge), visible or not. The content is the
  robot's own memory, so this tells nothing about the present.
* All other messages keep today's rule (an unseen thing is "something").
* At reset in realistic mode, handles are issued for every entity id that appears in `memory` values, `mind.seen` and
  `mind.snapshots` (the robot knew them before).

### 7.3 Recall forms

| call | returns |
|---|---|
| `recall` / `recall(prefix)` | stored memories as today, now `{key: {"value", "stored": "<when>", "stored_s"}}`; ids as issued handles |
| `recall("map")` | the known map: rooms with label, type, exits, connections, `seen` (when) |
| `recall("seen:")` / `recall("seen:<handle>")` | last-seen beliefs: `{"label", "room", "on"/"in": handle, "seen": "yesterday 18:40", "seen_s", "stale": true}` |
| `recall("seen:room:<rid>")` | everything last seen in a room |
| `recall("snap:")` | snapshot names and times (`diff` compares as today) |

`stale` is true unless the entity is perceived now. Beliefs are never refreshed by anything the robot did not perceive.

### 7.4 Setup op `prior_knowledge` (PEOPLE-IO, `tasks/setup.py`)

```json
{"op": "prior_knowledge",
 "map": "all",
 "seen": [{"entity": "{mug}", "at": "-1d 18:40", "level": "glance"}],
 "seen_rooms": {"rooms": ["{kitchen}", "{living}"], "at": "-2h", "level": "glance", "min_size_m": 0.15},
 "snapshots": {"living_tidy": {"room": "{living}", "at": "-1d"}},
 "memory": {"rule:no_vacuum_after_21": {"value": true, "at": "-3d"}}}
```

* The op records beliefs from the config **as it stands when the op runs**; later setup ops (moves, removals) are the
  changes the robot does not know about. So "what changed since yesterday" = spawn/arrange, `prior_knowledge`, then
  move.
* `at` accepts negative seconds or `-Nd HH:MM` / `-Nh` relative to the episode start.
* `level` grants realism knowledge at that tier for the fields of that moment (the knowledge signature lapses as soon
  as the entity has moved, which is today's rule and exactly the staleness wanted).
* Output: config section `prior_knowledge` (consumed by `Mind.from_config` at reset), plus `memory` entries.
* The people domain's history synthesizer (section 12) emits this op's data for "learn the house", "where I usually
  put it" and "last seen" entries.

### 7.5 Continuity across days

Multi-day continuity inside one episode uses phases (section 9): mind, memory, realism knowledge and world state carry
over. `Mind.dump()` / `prior_knowledge {"import": <dump>}` exist for tools (e.g. building a dataset of "the next day"
episodes), but a verified spec must be reproducible from its own setup, so specs use phases or synthesized history.

---------------------------------------------------------------------------------------------------------------------

## 8. Temporal evaluation (high; TIME-EVAL)

### 8.1 Spec keys

```json
"never":  [P, {"pred": P, "after": "lit", "until": "extinguished", "label": "stove_unattended"}],
"always": [{"pred": P, "after": "phase:p1"}],
"milestones": {"door_open": P, "kept_dry": {"pred": P, "held_for_s": 600}, "chime": {"pred": P, "count": true}},
"responses": [{"trigger": "chime", "response": P, "within_s": 120, "each": true, "label": "notified"},
              {"for_each_new": {"category": "spill"}, "response": {"path": "$exists", "op": "is_false"},
               "within_s": 600}],
"deadlines": {"door_answered": 120, "dressed": {"by": "07:30"}, "cleaned": {"within_s": 600, "after": "spill_seen"}}
```

* `never`: a violation is latched the first time P holds at any sample inside its window. A spec whose `never`
  predicate holds in the initial state is rejected by the lint (unless `"after"` excludes the start).
* `always`: latched the first time P is false inside its window.
* Windows: `after`/`until` take a milestone name, `phase:<id>`, or seconds.
* `held_for_s`: reached at the first sample where P has been continuously true for that long.
* `count: true`: the milestone records `count` (rising edges) and up to 50 `times`; predicate form
  `{"milestone_count": "chime", "op": "gte", "value": 2}`.
* `responses`: each rising edge of the trigger (a milestone or an inline predicate) opens an obligation with
  deadline `t + within_s`; it closes at the first sample where `response` holds (immediately if it already holds);
  a missed deadline latches `response:<label>@<t>`. `each: false` uses only the first trigger.
  `for_each_new`: an entity selector; every entity matching it that is created during the episode opens an
  obligation whose `response` is evaluated relative to that entity (`$exists` is false once it is destroyed).
* `deadlines`: a number keeps today's meaning (absolute seconds); `{"by": "HH:MM"}` is the first occurrence of that
  local time at or after the episode start (`"day+1 07:30"` for later days); `{"within_s", "after"}` is relative to a
  milestone or phase start.

### 8.2 Sampling (`kernel/monitor.py`)

Sample points: after the action handler (before time advances), after every substep, and at the end of each step
(after the mind update). Auto-order commands inside a wait are sampled after each command as well.
Milestones are now sampled at the same points, so a milestone reached inside a long wait records its true time
(a correctness fix that applies to all specs; section 19 lists the 7 affected specs).

Cost control: each temporal predicate gets a static footprint (top-level sections it reads, entity ids in direct
paths, `entities:*` for quantifiers, `time_s` for clock reads). Between samples the monitor re-evaluates only
predicates whose footprint intersects what changed: entity ids from the physics touch log (`w._touched`, TIER1_PHYSICS
6.1) and a new `w._touched_sections` set filled by `World.sec`/`set_scalar`/`set_path`. Predicates reading `time_s`
are always re-evaluated. Without the touch log (physics not landed) every predicate is sampled every substep.
Quantifiers use `w.find` (type/category index) for `type`/`category` selectors instead of scanning all entities.

### 8.3 Evaluation result additions

`evaluate()` adds `temporal: {"violations": {label: t}, "responses": {label: {"open", "met", "missed"}},
"milestones": {name: {"t", "count", "times"}}}` and `decided: null | "fail"` (6.7). Failure labels:
`never:<label|i>`, `always:<label|i>`, `response:<label>`, `deadline:<name>`.

### 8.4 Predicates over people and totals (medium; same package)

* Quantifiers take `"in": "people" | "pets" | "<path to a dict or list>"` (default entities); the relative record
  gets `$id` and `$room` as for entities.
* Aggregates: `{"agg": {"fn": "sum"|"min"|"max"|"avg"|"count", "in": ..., "where": SEL, "check": P_rel,
  "field": "portion_g"}, "op": OP, "value": V}`; the comparison operand may also be `"ref": path` or
  `"ref_agg": {...}` ("enough portions for whoever is here": sum of portions >= count of people present).
* Empty selections: sum 0, count 0, min/max/avg MISSING (the comparison is false).

---------------------------------------------------------------------------------------------------------------------

## 9. Multi-phase episodes (high; TIME-EVAL)

```json
"phases": [
  {"id": "p1", "request": {"from": "{mom}", "channel": "in_person", "phrasings": ["...", "...", "..."]},
   "success": [...], "never": [...], "deadlines": {...}, "questions": {...}, "budget": {"steps": 150, "time_s": 3600},
   "ends": "done"},
  {"id": "p2", "start": {"after": "p1", "gap_s": 50400, "robot": "dock",
                         "events": [{"at_gap_s": 3600, "effect": "people.use", "args": {...}}]},
   "request": {"from": "{mom}", "channel": "app", "phrasings": [...]},
   "success": [...], "questions": {"max_unnecessary": 0}, "ends": "done"},
  {"id": "p3", "start": {"when": P}, "supersedes": "p2", "request": {...}, "success": [...]}
]
```

* A spec has either top-level `instruction`/`success` or `phases` (lint). Top-level `never`, `always`, `failure`,
  `side_effects`, `preserve` apply to the whole episode; phase keys apply inside the phase window.
* **Phase end** (`ends`): `"done"` (the agent declares done: harness calls `env.phase_done()`; plans use
  `{"phase_done": true}`), `{"at_s": N}` relative to the phase start, or `{"when": P}`. Budget exhaustion ends a
  phase as failed.
* **Start**: the first phase starts at reset. Later phases start `{"after": id}` (when that phase ends, then after
  `gap_s`), `{"at_s": N}` (episode time) or `{"when": P}` (exogenous, may interrupt the current phase; `supersedes`
  closes the superseded phase as `superseded`, not scored).
* **Gap**: the engine advances `gap_s` in idle mode with every system running, auto standing orders active and the
  gap's `events` scheduled (`at_gap_s` relative to the gap start); `robot: "dock"` parks the robot on its dock first
  (a scripted relocation logged as `phase_relocate`, allowed only when its hands are empty; otherwise the robot stays
  where it is and the log says so). The gap costs simulation time like a wait (14 h is about 3.5 s today).
* **Requests** become the observation's request (section 10); `obs.instruction` becomes the phase's text.
* **Scoring**: each phase's success, questions, deadlines and temporal keys are evaluated over its window; success
  predicates are evaluated at the phase end on the state at that moment (stored in `evaluation.phases`). Episode
  success = every scored phase succeeded and no episode-level failure. `goal_fraction` = phases passed / phases scored.
* **Harness** (TIME-EVAL edits `eval/run.py`, `eval/agents.py`; contract update in `EVAL_CONTRACTS.md`): a `done`
  with phases left calls `env.phase_done()` and continues the episode; agents get the optional hook
  `on_request(request)`; `LLMAgent` reads the current instruction from the observation when present (PEOPLE-IO,
  `llm.py`). Records gain `phases: [{id, success, steps, time_s, end_reason}]`.
* **Verification**: idle = for each phase, wait out its budget and declare done; it must fail. Initial state must not
  satisfy phase 1. Reference and wrong plans contain `phase_done` steps.

---------------------------------------------------------------------------------------------------------------------

## 10. Requests: who gave the instruction (high)

### 10.1 Spec and state

```json
"request": {"from": "{pid}", "channel": "in_person"|"phone"|"intercom"|"app"|"note"|"overheard", "room": "{rid}",
            "gesture": {"point_at": "{eid}"} | {"place": "{surface}", "spos": [0.4, 0.2]}, "relayed_by": "{child}",
            "subject": "{target}", "phrasings_by_role": {"child": ["..."]}}
```

* The runner (TIME-EVAL) writes `state.requests` (list section): `{"id": "r1", "phase": "p1", "from", "channel",
  "room", "t", "text", "subject", "gesture"}` and binds `user_simulator.intent.requester` to `from` when unset.
* At reset (and at each phase start) the engine appends the request to `messages` itself (`speaker` = `from`,
  `to: "robot"`, `request: "r1"`, `channel`, `heard_by_robot` by the channel rule of 5.3), so no domain is required;
  for a gesture it schedules people's `people.point` event at t = 0 when that handler is registered (people's
  gesture observer renders the ray); without the people domain the gesture is dropped and the verify record says so.
* Defaults: schema 2 without `request`: `{"id": "r0", "from": null, "channel": "unspecified"}` (today's plain
  instruction). Schema 3 without `request`: the first adult resident by id, `channel: "in_person"` if they are in the
  robot's room, else `"app"`. Nobody is moved.
* `phrasings_by_role` lets a child's phrasing differ from an adult's; the runner draws from the requester's role list
  if present, else `phrasings`.

### 10.2 Observation (PEOPLE-IO, `perception.py`/`realism.py`)

`obs["request"] = {"id", "from", "from_label", "channel", "room", "time_s", "text"}` plus `obs.instruction` (text).
Realistic: `from` is the person id when the robot can tell who it is (in person, lit, a resident or recognized
visitor), `"person"`/the dark-spell shape handle in the dark, `"unfamiliar person"` for an unrecognized visitor, the
contact name for phone/app (a known contact), `"note"` for notes (with the note's handle). The gesture appears in
`obs.gestures` as today (noisy ray). Later requests in the same phase (amendments: "actually, use the blue one") are
people speech with `request` ids and appear in `obs.messages` and as `addressed` notices.

---------------------------------------------------------------------------------------------------------------------

## 11. Household calendar, schedules and holidays (high)

### 11.1 Kernel clock side (TIME-EVAL, `clock.py`)

* `holiday(state, date=None) -> name | None`, `holidays_between(state, d0, d1)`, from
  `data/calendar/holidays.json` per region: fixed dates, nth-weekday rules ("4th Thursday of November"),
  last-weekday rules, Easter-relative (Anonymous Gregorian computus), observed-day shifts (weekend -> Monday where the
  region does so). Regions: us, uk, eu (a documented German baseline), jp, ca.
* DST: `data/calendar/dst.json` (us/ca: second Sunday of March 02:00 to first Sunday of November 02:00; uk/eu: last
  Sunday of March 01:00 UTC to last Sunday of October; jp: none). `clock.now()` stays the monotonic local
  standard-time view; `clock.wall(state)` returns the wall-clock reading with DST applied. Only `clock.model >= 2`
  (generation-2 houses) uses `wall` in `describe()`; model 1 keeps today's output.
* Latitude: with `clock.model >= 2` and `clock.latitude`, `sun_times` uses the standard declination formula
  (day length = 24/pi * acos(-tan(lat) tan(decl))); model 1 keeps the fixed +-3 h curve.
* Helpers: `today(state)`, `tomorrow(state)`, `weekday(state, date=None)`, `is_weekend`, `parse_when(state, "tomorrow
  07:30" | "-1d 18:40" | "+2h")`, and `occurrences(event, d0, d1)` for recurrence rules (`freq` daily/weekly/monthly,
  `interval`, `byday`, `bymonthday`, `until`, `count`, `except`).

### 11.2 `household.calendar` (people owns semantics; setup op `calendar` in PEOPLE-IO)

```json
{"events": [{"id": "ev_school", "who": ["child_1"], "kind": "school", "title": "School", "start": "2026-10-12T08:15",
             "end": "2026-10-12T15:00", "location": "school",
             "attributes": {"pe": true, "library": false}, "hidden": {"slip_unsigned": true},
             "recurrence": {"freq": "weekly", "byday": ["MO", "TU", "WE", "TH", "FR"], "until": "2026-12-18"},
             "status": "confirmed", "visible_on": ["wall_calendar", "app"], "known_by": "adults"}],
 "sources": {"wall_calendar": "{eid}", "whiteboard": "{eid}", "app": true}}
```

* Kinds (people data): school, work_office, work_remote, appointment, service_visit (arrival window), delivery,
  carrier_pickup, collection (admin_inventory), trip (destination, dates, travellers), practice/lesson, party/guests,
  birthday, holiday.
* The robot reads it: from the wall calendar or whiteboard (read tier: people renders the visible events of the
  displayed month into the artifact's `text`), from the app (people action `check_calendar`, needs network), or by
  asking (`aspect: when`, answered from events and `hidden`).
* Event needs: `data/people/event_needs.json` maps kind + attributes to required item kinds (school: backpack, lunch
  box; pe: sneakers; swim: swimsuit, towel; office: laptop, badge, charger; trip: passport per international traveller,
  charger, toiletries). A people helper returns the item ids and a `needs_ok` predicate binding, as
  `ownership_pile` returns `sort_ok` today.
* Schedules: people derives `away`/`working` blocks from events for `follow_schedule` residents.
* admin_inventory exports collection days (its `household.collection` rule) and carrier pickups as events;
  outdoor_plants provides `forecast_for(location, dates)` for trip destinations (section 13).
* The existing `memory.calendar` convention stays readable by the `schedule:` answerer.

---------------------------------------------------------------------------------------------------------------------

## 12. Resident profiles, habits and history (high)

* **GEN (generation 2)** writes per person `profile` {tidiness 0..1, chronotype, diet (omnivore/vegetarian/vegan/
  pescatarian), allergies (list), drink orders, fold/bed/pillow styles, storage habits, tech level, weekly pattern
  (office/remote/shift/retired/school)} and `habits` {item kind: {"home": spot id, "alt": [[spot id, weight], ...]}},
  plus matching `preferences` (the fields people's answerers already read: `storage`, `wake_method`, `pref:` slots).
  All are never-tier (people fields are hidden unless listed in the people view).
* **people (follow-up)** turns profiles into facts automatically (`where`, `preference`, `owner` answers need no
  spec facts) and owns the **history synthesizer**, a scenario helper `people.history {days, residents, robot_present,
  observed_fraction}`:
  * simulates N past days from schedules and habits deterministically (`_h(seed, "history", day, pid, kind)`): who
    used which item when and where they left it, usage counts (`use_count`, hidden), a meal log, guests;
  * leaves evidence: final placements, dust on rarely used items (cleaning's soil API), wear;
  * writes what each resident can answer (facts with `known_by`);
  * writes the part the robot observed (when `robot_present`) as `prior_knowledge` data and `history:<id>` memories,
    so `no_history` necessity and "where I usually put it" are grounded in the same synthetic past.
* Kernel support needed: none beyond `prior_knowledge`, facts and `clock.parse_when`.

---------------------------------------------------------------------------------------------------------------------

## 13. Season, date and weather consistency (high)

### 13.1 Generator (GEN, generation 2)

* `data/generator/climate_normals.json`: per region and month, mean daily high/low (C), wet-day frequency, mean
  precipitation per wet day (mm), snow-day frequency, mean wind (m/s), each with `source`. Representative cities:
  us = Philadelphia, uk = London, eu = Frankfurt, jp = Tokyo, ca = Toronto (1991-2020 normals from NOAA, Met Office,
  DWD, JMA, ECCC). Order-of-magnitude anchors to check against the sources: London January about 8/2 C and July
  about 23/14 C; Toronto January about -1/-8 C; Tokyo August about 31/24 C.
* Initial `weather` from the process at the start time; `weather.process = {"model": "normals_v1", "region",
  "seed_key"}`; `clock.model = 2`, `clock.region`, `clock.latitude` (us 40 N, uk 51.5 N, eu 50 N, jp 35.7 N, ca 43.7 N).
* Season context `household.profile.season` (from the date and hemisphere) and `opts["season"]` for furnishers.

### 13.2 The default weather process (contract; outdoor_plants implements)

Pure function of (seed_key, region, local time); nothing is stored except what scripted events override.

```
day d (days since 2000-01-01), u(...) = uniform from _h(seed_key, ...), z(...) = inverse-normal of u
temperature anomaly  a_d = sum_{k=0..20} phi^k * sigma * sqrt(1 - phi^2) * z("T", d - k),  phi = 0.7,
                     sigma = region-month std of daily mean (data; about 2.5-3.5 C)
daily high/low       H_d = Nhigh(doy) + a_d,  L_d = Nlow(doy) + a_d + 0.5 * z("dL", d)
                     (normals interpolated by day of year)
diurnal              T(h) = L_d + (H_d - L_d) * 0.5 * (1 - cos(pi * (h - sr) / (15 - sr)))  for sr <= h <= 15
                     linear from H_d at 15:00 to L_{d+1} at the next sunrise otherwise   (sr = sunrise hour)
wet days             2-state Markov with frequency f and persistence r = 0.3: p_ww = f + r(1 - f), p_wd = f(1 - r);
                     state_d found by iterating 30 days from the stationary draw (cached per day)
rain spell           start hour and duration from u("spell", d); intensity mm/h exponential with mean
                     (mm per wet day / mean spell hours)
snow                 precipitation with T(h) <= 0.5 C
condition            storm (intensity >= 15 mm/h), rain, snow, cloudy (wet day outside spell or u < cloud share), clear
wind                 mean(region, month) * exp(0.35 * z("W", d)) * (1 + 0.3 * afternoon); gust = 1.5 x
forecast             issued 06:00 and 18:00; lead L h: T_fc = T + e,  e = (1.0 + 0.3 * L / 24) * z("fcT", issue, target)
                     precipitation probability: wet -> 0.85 - 0.08 * L / 24, dry -> 0.10 + 0.05 * L / 24
```

The temperature error has sigma 1.3 C at day 1 and 2.5 C at day 5, a mean absolute error of about 1.0 C and 2.0 C
(MAE is about 0.8 sigma), which matches the order of public NWS verification statistics; outdoor_plants calibrates
and cites. Scripted weather events set `weather.override_until_s`
(or permanent) and the process yields while it lasts; generation-1 houses have no `weather.process`, so nothing
changes for them. Climate reads the outdoor temperature from `weather` (its `diurnal` option remains for
generation-1 specs). `forecast_for(location, dates)` uses the destination's region normals with the same formulas.

### 13.3 Season-aware state (domain follow-ups, generation 2 only)

outdoor_plants: snow and ice on exterior surfaces in winter by recent weather, leaf litter in autumn, pool open
roughly May-September by region, garden stage by month. textiles_laundry: in-season clothing in closets, off-season
in storage bins. climate: thermostat mode heat/cool/off by season. admin_inventory: holiday decor bins and displayed
decor within the weeks around holidays.

---------------------------------------------------------------------------------------------------------------------

## 14. Generator generation 2 (high/medium; GEN)

### 14.1 Switch and protection

* `generate_house(seed, gen=1|2, ...)`. `gen` defaults to 1 in the generator; `runner.build` passes `house.gen` if the
  spec sets it, else 2 for schema-3 specs and 1 otherwise. Tools (`HouseEnv()` without a config, demo, layout audit)
  move to gen 2 after the landing gate (open question 3).
* Generation-1 output is pinned: before any GEN edit, Phase 0 records `tests/data/gen1_digests.json` = digests of
  `generate_house` for seeds 0-19 with default options **and** for every distinct `house` block of the 242 specs on
  seeds 0, 1, 2, 41, 97. `tests/test_generator_compat.py` must stay green; only a main-session commit naming the
  round and reason may re-record it (other tier-1 rounds that change houses on purpose, e.g. physics' per-instance
  `anchored`, re-record it themselves).
* Every gen-2 code path is behind `if gen >= 2`; new furnishers (orders 100-104) return at once for gen 1; layout
  changes (exterior steps) and composition draws exist only in the gen-2 branch.

### 14.2 Pipeline (gen 2)

```
rng = Random("house:{seed}")
composition = profile.compose(Random("profile:{seed}"), opts)      # before structure: bedrooms needed
build_structure(seed, opts + {"bedrooms": needed unless the spec fixed it}), finish_structure; exterior steps (14.6)
    (a spec that fixes `bedrooms` restricts the draw to compositions that fit: children may share, roommates may not)
region (house rng); weather, clock (climate.py, hash-based); residents/pets from composition (house rng for rooms)
profiles, habits, household.profile (Random("profile:{seed}:people"))
dock; furnishers 10..99 (domains read opts["gen"], opts["season"], config["household"]["profile"])
gen.belongings (100), gen.furnishings (101), gen.infant_items (102)
repair_layout; metadata.gen = 2
```

### 14.3 Household composition (`data/generator/households.json`)

Presets with weights (plausible shares; each row carries `source` or `"assumption"`): single adult, couple, couple +
children, single parent + children, roommates (2-3 unrelated adults, separate bedrooms, strict ownership), multi-
generational (+ elderly). Children's ages span infant (0-1), toddler (1-3), child (4-12), teen (13-17); roles use the
people domain's values (infant, toddler, child, teen). `residents: "minimal"` stays one adult (now with a profile and
belongings); `"random"` uses the weights; `{"composition": "couple_with_toddler"}` picks a preset; an explicit list is
kept and only missing profile fields are filled. Pets: dog, cat, freshwater fish (aquarium), bird (cage), small mammal
(cage), reptile (terrarium), chickens (coop, houses with a backyard only); weights are an assumption anchored on the
US APPA survey order of magnitude (dogs and cats far ahead, then fish, small animals, reptiles, birds) until cited.
`pets: "none"` stays none.

### 14.4 Personal belongings (`belongings.py`, `data/generator/belongings.json`)

| Who | Items (probability) | Home spot | Habit alternatives |
|---|---|---|---|
| adult | smartphone (0.97) + charger (0.95), wallet (0.9), key ring (0.95), work bag or handbag (0.7), coat or jacket by season (0.9), raincoat (0.4), 2-3 pairs of shoes by season, earbuds or headphones (0.6), tablet (0.4), smartwatch (0.25), sunglasses (0.5) | phone/charger: nightstand by night, kitchen counter or desk by day; keys: key hook or key bowl; wallet: entry surface or dresser; bag: entry hook/bench/floor; outerwear: coat hooks or coat closet; shoes: shoe rack (always the entry where `shoes_off`) | phone: sofa, kitchen counter, bed, bathroom vanity, coat pocket; keys: kitchen counter, coat pocket, bag; glasses: side table, vanity |
| teen | smartphone (0.95), earbuds (0.8), backpack (0.95), sneakers, hoodie or jacket | own bedroom, entry for shoes | floor, desk, kitchen table |
| child (4-12) | backpack (0.95 from age 5), lunch box (0.8), jacket, tablet (0.4) | entry hook or own bedroom | living room floor |
| toddler/infant | household items instead (stroller, diaper bag) | entry, nursery | - |
| elderly | phone (0.85), glasses (people already furnishes), cane per abilities (people) | nightstand, side table | sofa side table |
| household | rain umbrella 1-2 (in an umbrella stand when furnished, else coat closet), passports for each traveller in a document box (0.6), scissors in the junk drawer | | |

* Every item gets `owner` (never tier), `home` (its home spot, never tier) and, for phones, `phone_of` (people's
  field), so people `call` finally rings a real phone.
* Displacement: P(away from home) = 0.05 + 0.35 * (1 - tidiness); the alternative is drawn by its weight and the
  time of day; pockets use people/textiles' `in_pocket` convention.
* Types come from their owners (electrical: smartphone, tablet, smartwatch, wireless_earbud, over_ear_headphones,
  chargers; admin_inventory: wallet, backpack, work_bag, passport, rain_umbrella, key_bowl; textiles: coat, raincoat,
  shoes, boots, sneakers; food: lunch_box). Missing types (jacket, handbag, general scissors) are requested from their
  owners; the furnisher skips a type the registry lacks and `tools/layout_audit.py` reports it.

### 14.5 Common furnishings (`furnishings.py`, order 101)

Living room: sofa cushions (textiles type), throw pillows (`decorative_pillow`), throw blanket (`knit_throw`),
coasters (0.5, manipulation type), an unlit candle (0.4, safety type), ottoman (0.3, core type). Entry: indoor mat,
`coir_doormat` outside the front door, `key_bowl` (0.4), umbrella stand (0.5). Garage: `picnic_cooler` (0.5). Infants:
`crib` in the parents' room or a nursery, `high_chair` (kitchen/dining); toddlers: high chair (0.6). Dining chairs:
material/color variants through props of existing types. Placement uses `Placer` with `keep_access`; failures are
reported, never forced.

### 14.6 Exterior steps

A porch is joined to its yard by a `stairs` connection with `exterior: true`, `steps` 2-4 and `rise_m` 0.17 on the
same floor index (layout validators accept same-floor stairs when `exterior`). navigation_robot traverses them like
stairs (follow-up); physics/embodiment stair costs apply; "salt the porch steps" has a real target (outdoor_plants
puts ice and snow on them).

### 14.7 Smart-home presence

GEN draws `household.profile.smart_home` = none / basic / connected (assumption: 55 / 30 / 15 %, to be cited or
revised); electrical's furnisher (gen 2) places devices accordingly (basic: smart speaker, 1-3 smart bulbs or plugs;
connected: hub, bulbs, plugs, 1-2 security cameras; mechanisms adds a smart lock). The hub model is electrical's
(section 15).

---------------------------------------------------------------------------------------------------------------------

## 15. Medium and low items (brief designs and owners)

| Need | Design | Owner |
|---|---|---|
| Residents' activity leaves consequences (50) | opt-in `household.people_sim.activities: true`; scheduled activities (meals, cooking, homework, play, getting ready, coming home) take items, use them through domain effects (dishes soiled via food/cleaning, towels wet, toys out, shoes and bags at the entry, mail and parcels via admin) and leave them per habits; interruptions logged; deterministic per (seed, day, person, activity); the same engine feeds the history synthesizer | people (+ food, cleaning, textiles_laundry, admin_inventory effects) |
| Who lives in the house (62) | section 14.3 plus role behaviours: infants (crying notices, crib), toddlers (people children system), teens (authority 0-1, privacy), roommates (ownership, no_enter_rooms) | GEN + people + pets |
| Smart-home layer (45) | electrical hub: `smart_home` section {devices, scenes, automations [{trigger time/sunset/state/away, actions}], away_mode}; app-visible state through `set` (control kind app) and `remote_status`; device notifications as `device` notices to the robot and app messages to residents; data-gated on a hub entity | electrical (+ mechanisms smart locks, climate thermostats) |
| Common furnishings (150) | section 14.5; the types: textiles (sofa_cushion, seat_cushion, jacket), manipulation (coaster), safety (candle with lit state and fire rules), core (ottoman, via GEN) | GEN + type owners |
| Predicates over people and totals (65) | section 8.4 | TIME-EVAL |
| Reactive user simulator (31) | people feedback rules `user_simulator.feedback: [{"watch": P, "after": milestone, "say": {"vocab": "relative", "frame": "person"}, "tolerance", "max_rounds", "patience_s"}]`: the person compares the result with their target in their own frame (left/right from `facing_deg`), says a relative correction ("a little to the left", "gentler", "more"), repeats until within tolerance or out of rounds/patience, and records `satisfaction` (0-1) under `people_log.feedback` | people |
| Locale and language (20) | `clock.format_date(state, date, style)` (MDY for us, DMY for uk/eu/ca-en defaults, YMD for jp) and `reports.format_quantity(state, value, quantity)` used by food/admin when writing label text (`metadata.units` exists); people renders non-English phrasings through `dialogue_render`; realism needs no change (labels are read-tier text) | TIME-EVAL (clock helper), PEOPLE-IO (format_quantity), food + admin_inventory (labels), people (language) |

---------------------------------------------------------------------------------------------------------------------

## 16. APIs (frozen contracts for the work packages)

### 16.1 Modules and signatures

```
timeline:  perceivable(w, rule, realism=None) -> bool          (watch_condition_visible re-exported by core.py)
           register_notice_kind(kind, doc, implies=())       NOTICE_KINDS (dict)
           robot_perceives(w, notice) -> dict | None          (6.2; applies the robot_hears hook)
           run_auto_orders(env, idle_ctx) -> list             (engine-internal)
world:     w.notice(kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None,
                    data=None)
           sp = w.savepoint(); w.rollback_to(sp); w.release(sp)
           w._touched_sections (set; cleared by monitor.sample)
monitor:   Monitor(task); sample(env, point) ; record() -> dict ; load(dict) ; decided(state) -> None|"fail"
phases:    PhaseController(task); active() -> phase ; end(env, reason) ; start_next(env) ; record() -> dict
clock:     holiday(state, date=None); holidays_between(state, d0, d1); wall(state); dst_active(state); today(state);
           tomorrow(state); weekday(state, date=None); is_weekend(state, date=None); parse_when(state, text);
           occurrences(event, d0, d1); format_date(state, date, style=None)
evaluation:evaluate(task, state, baseline, milestones, steps, world=None, temporal=None, phases=None, mind=None)
predicates:new forms: report, report_set, milestone_count, agg; quantifier "in"; "mind." paths (read-only view)
dialogue:  resolve_subject(env, pid, about) -> Subject ; match_fact(state, subject, aspect, detail) -> fact|None
           necessary(env, pid, fact, subject) -> bool ; route(name, aspects, fn) ; answer(ctx, args) -> dict
           canonical_key(aspect, subject, detail) -> str ; vocabulary() -> dict
reports:   normalize(args, state) -> record ; record(ctx, record) -> dict ; match(state, expectation) -> bool ;
           match_set(state, spec) -> (precision, recall, detail) ; convert(value, unit, quantity) -> float ;
           format_quantity(state, value, quantity) -> str ; issue_words(category) -> [str]
mind:      Mind.from_config(config, env) ; update(env, perceived_ids) ; view() -> dict ; recall(prefix) -> dict ;
           snapshot(name, env) ; diff(name, env) ; dump() -> dict ; load(dict)
perception:Perception.perceived_ids(env) -> frozenset   (cached per step)
```

### 16.2 Actions (changed or new)

| action | params (new in bold) | doc string (first 120 chars carry the schema) |
|---|---|---|
| `ask` | target person, **about, aspect, detail, act, options, channel, language**, question, topic (legacy) | "Ask a person (aspect: which/owner/where/preference/amount/when/ok/keep/intended/why/how/status/who; about: handle/word)" |
| `report` | **kind, subject, issue, severity, quantity, value, unit, state, of, scope, location, channel, detail**, to; legacy target, field | "Report (kind finding/measurement/status/count/none_found; subject; issue/state/quantity+value+unit; to+channel)" |
| `wait` | seconds, **until, interrupt_on** | "Let time pass; until=observable condition, interrupt_on=[urgent, doorbell, addressed, ...] ends it early" |
| `watch` | condition, commands, persistent, **mode, guard, max_runs, cooldown_s, expires_s** | "Standing order on an observable condition; mode auto runs the commands itself while idle, queue needs run_pending" |
| `recall` | target (prefix; **map, seen:, seen:room:, snap:**) | "Recall memories: prefix, map (known rooms), seen:<handle> (last seen where/when, may be stale), snap: (snapshots)" |
| `listen` | (unchanged) | returns recent speech and the latest notices |

`actions_fingerprint` changes (new parameters), so saved states and replays recorded before v3.1 are refused; the
landing regenerates evidence (as every kernel round does).

### 16.3 Hooks

| hook | signature | purpose |
|---|---|---|
| `dialogue_channel`, `dialogue_comprehends`, `dialogue_render`, `dialogue_reliability` | section 4.8 | people |
| `report_delivered` | `fn(w, record, pid, channel) -> {"heard": bool} or None` | people registers who heard |
| `robot_hears` | `fn(state, notice) -> bool or None` | people's propagation for the robot |
| `phase_start` / `phase_end` | `fn(w, env, phase)` | domains reset per-phase logs |
| `mind_seen` | `fn(state, eid, record, belief) -> belief` | domains add a glance field to beliefs (e.g. fill bucket) |

### 16.4 Setup ops (PEOPLE-IO, `tasks/setup.py`)

`prior_knowledge` (7.4), `facts` (`{"op": "facts", "add": [...]}`), `calendar` (`{"op": "calendar", "events":
[...], "sources": {...}}` appends to `household.calendar`), and `user_sim` gains a `facts` key.

### 16.5 Spec fields (TIME-EVAL, `tasks/spec.py`)

`ALLOWED` gains: `schema` (2 default, 3), `request`, `phases`, `never`, `always`, `responses`; `milestones`,
`deadlines`, `questions` accept the extended forms; `house.gen`. `TASK_KEYS` gains `never`, `always`, `responses`,
`phases`, `request`. `merge` treats `never`/`always`/`responses` as lists (`+never` appends). Plan steps gain
`{"phase_done": true}` and `{"wait_for": [kinds], "max_s": N}`.

### 16.6 State sections and stats

| section | shape | written by |
|---|---|---|
| `notices` (list, cap 200) | 6.1 | kernel |
| `order_log` (list, cap 300) | 6.4 | engine |
| `report_log` (list) | `{"n", "t", "kind", "subject": {"kind", "id"}, "issue", "issue_raw", "severity", "quantity", "value_si", "value", "unit", "state", "of", "scope", "location", "to", "channel", "delivered", "detail"}` | core `report` |
| `dialogue_log` (list, cap 300) | `{"t", "to", "about", "aspect", "detail", "act", "matched", "route", "counted", "said", "channel", "comprehended"}` | core `ask` |
| `dialogue` (dict) | `{pid: {"last": {...}, "turns": n}}` | core `ask` |
| `requests` (list) | 10.1 | runner/engine |
| `phase` (dict) | `{"active": id, "started_s", "history": [...]}` | phases |

All list sections join `World.LIST_SECTIONS`. New stats: `questions_unmatched`, `order_refusals`,
`reports_unclassified` (none of them is a global failure flag).

---------------------------------------------------------------------------------------------------------------------

## 17. Realistic perception (cross-cutting rules)

* Questions: `about` handles are translated by `inbound` (already true for strings); answers follow today's
  outbound rule; clarify-back options are descriptions, never handles of unseen things; refusal wording for
  unreachable people is the same for "asleep in another room" and "not home" ("That person is not available here").
* Reports: unknown-word suggestions depend only on the perceived category; the report message never says whether the
  report is correct.
* Notices: only perceived notices exist; `source` is a handle only if the source is perceived now; speech text only
  under 6.2; the dark-spell rule applies to speakers in a dark room.
* Waits and orders: conditions follow `perceivable`; `until` cannot test hidden fields, numeric thresholds or other
  rooms; the wait message lists perceived notices only.
* Memory: section 7.2. Beliefs are rendered at the identity level of the time they were formed.
* Requests: section 10.2.
* `tools/perception_audit.py` (PEOPLE-IO) gains checks: no true id in `dialogue_log`-derived messages, no
  unperceived notice in `obs.notices`, no handle in a recall result that was never issued, report suggestions equal for
  every member of a look-alike group.

## 18. Determinism and replay

* No new RNG streams except hash draws `_h(seed, purpose, ...)` (dialogue reliability, history, weather, belongings
  displacement); generator streams are separate (`profile:{seed}`, `profile:{seed}:people`, `furnish:{seed}:gen.*`).
* Notices are processed in emission order within a substep, kernel-derived notices after domain notices, each list
  sorted by id where ids are involved.
* Auto orders run in order index order; savepoints make a refused command's rollback exact; the order log is state.
* The monitor's record, the phase record and the mind are saved by `env.save` and rebuilt on replay; replays compare
  state digests per step as today (auto-order commands happen inside their wait step, so step counts are unchanged).
* Facts are matched in list order; vocabulary loading is sorted by file name and duplicate keys fail.
* Weather is a pure function of (seed_key, region, time), so any substep partition gives the same values.
* Tests: two processes with different `PYTHONHASHSEED` give identical digests; save/load and replay roundtrips for a
  wait with interrupts, an auto order with a refused command, a two-phase episode, and prior knowledge;
  `HOUSE_SIM_STRICT=1` over every new test.

---------------------------------------------------------------------------------------------------------------------

## 19. Compatibility and migration (the 242 verified specs)

### 19.1 Protections

| Change | Why existing specs are unaffected |
|---|---|
| `ask` canonical path | `topic` keeps the old code path byte for byte (same stats, facts, answers) |
| `report` typed kinds | legacy form writes `reports.<target>.<field>` exactly as today; the extra `report_log` record is a new section no legacy predicate reads |
| `wait` | defaults: no `until`, no interrupts, same duration; the message keeps its "Waited N s" prefix (notes appended); no authored spec saves from a wait message (landing lint asserts it) |
| `watch` | `mode: queue` default; firing still queues into `pending` |
| Fire-time perceivability | uses realism at fire time and the weather rule; with `robot.network` true by default, weather stays perceivable, so the only changes are in network-off scenes; affected candidates re-verified: A-8910a09f802fbf-01, T-6203fdc5fa8986-01 |
| Temporal keys | `never`, `always`, `responses`, extended deadlines are opt-in |
| Milestones sampled inside waits | correctness fix for all specs; the 7 milestone specs are re-verified (section 2 list); a verdict change is triaged like a physics flip (correctness over stability, TIER1_DECISIONS) |
| Observation additions | `request`, `notices` are new keys; the realistic perceivability check only gains observations |
| Memory rendering | only cognitive-action messages change in realistic mode; no realistic spec uses recall/diff (the two recall specs run in oracle mode) |
| Mind | outside world state; state digests unchanged |
| Generator | gen 1 default for schema-2 specs; golden digests (14.1); gen-2 furnishers return for gen 1; runtime features data-gated (`weather.process`, `clock.model`, `household.calendar`, `people_sim.activities`, a smart hub) |
| Clock | holidays, DST and latitude only with `clock.model >= 2` |
| Predicates | additive forms; existing operators unchanged |
| Actions fingerprint | changes; saved states and replays are regenerated at landing |

### 19.2 Landing gate (each package, then the integration)

1. All suites, full and per-domain isolated, also with `HOUSE_SIM_STRICT=1`.
2. `python -m house_sim --check` on all 242 specs, seeds 0, 1, 2, 41, 97, oracle and realistic; verdicts and
   reference state digests compared with `tests/data/spec_baseline.json` (recorded in Phase 0). Allowed differences:
   the re-verified lists above (milestones, fire-time perceivability), each triaged and signed off.
3. `tests/test_generator_compat.py` green.
4. `tools/perception_audit.py`: no new leaks; `tools/fuzz.py`: no crashes, deterministic.
5. Performance budget (section 20).

### 19.3 Migration packages (after landing)

* **M-WAITS**: the 13 privileged-wait specs move to schema-3 waits (`wait_for` doorbell instead of `at_door`, `until`
  on a perceivable cue, auto orders), owned by their domains.
* **M-GEN2**: each domain re-verifies its specs with `--gen 2` (CLI flag forcing the house generation); passing specs
  get `"house": {"gen": 2}`; failing ones stay gen 1 or are fixed. Tracked in `catalog/analysis/gen2_migration.json`.
  Gen 1 is retired only when no spec uses it.
* **M-TOPICS**: specs using private ask topics or field reports may adopt canonical forms when touched; not required.

## 20. Performance budget

| item | budget |
|---|---|
| quiet simulated day (seeds 0-2) | no regression at landing (<= 7.2 s); <= 3.5 s after the idle probes |
| `ask` | <= 2 ms (subject resolution + fact match + route) |
| `report` | <= 1 ms |
| notice derivation + interrupt check per substep | <= 0.03 ms |
| monitor sampling per substep, nothing touched | <= 0.05 ms; with quantified predicates over touched entities <= 0.5 ms |
| `mind.update` per step (900-entity house) | <= 1.0 ms (room index, perceived set shared with observe) |
| auto orders | the cost of their commands only |
| phase gap | the same as an equivalent wait |
| `generate_house(gen=2)` | <= +20 % over gen 1 (today about 0.48 s per house) |
| vocabulary/holiday data load | <= 20 ms once per process |

`tools/bench_systems.py --day` and a new `tools/sweep_bench.py` (ask/report/monitor/mind micro-benchmarks) report
these; the landing gate records them.

---------------------------------------------------------------------------------------------------------------------

## 21. Test plan

**TIME-EVAL**

1. `tests/test_timeline.py`: notice audibility (same room, one open hop -6 dB, closed door -20 dB, threshold 40 dB,
   speech text rule); `wait(until=...)` ends at the first substep where a visible condition holds and not when the
   robot is in another room; non-perceivable `until` refused; `interrupt_on` doorbell/addressed/person_enters/urgent;
   default wait identical to today (state digest equal); auto order runs during a wait, a refused command rolls back
   alone (savepoint) and is logged, later commands of that order skipped, other orders continue; held orders during a
   busy action run at the next idle moment; limits (`max_runs`, `cooldown_s`, `expires_s`, 500-command budget,
   forbidden commands); fire-time realism check; weather perceivability rule.
2. `tests/test_world_savepoint.py`: nested savepoints, rollback_to restores entities/sections/scalars created and
   mutated after the savepoint only; strict mode.
3. `tests/test_monitor.py`: `never` latched by a transient violation inside a wait; `always`; windows; `held_for_s`;
   `count`; responses each/first, `for_each_new` spills; extended deadlines (`by`, `within_s`/`after`); footprint
   skipping gives the same verdicts as sampling everything (randomised comparison); `decided: "fail"`.
4. `tests/test_phases.py`: two-phase episode with a gap, events in the gap, robot docked, mind and memory carried,
   per-phase scoring, `supersedes`, idle verification fails, harness `done` advances phases (spy agent), records.
5. `tests/test_clock.py`: holidays per region (US Thanksgiving 2026-11-26, UK Easter Monday 2026-04-06, JP fixed
   dates), DST transitions (us 2026-03-08 and 2026-11-01), `parse_when`, recurrence expansion, latitude day length
   (London 7.6 h at the December solstice by the geometric formula, no refraction), model 1 output unchanged.
6. `tests/test_predicates_v3.py`: `in: people/pets/path`, aggregates, `milestone_count`, `mind.` paths, `report` and
   `report_set` forms delegating to `reports` (with the PEOPLE-IO stub until it lands).
7. Runner/verify: schema-3 `wait_until` compiles to an env wait and fails when privileged; schema-2 counts
   `privileged_waits`; idle early exit.

**PEOPLE-IO**

8. `tests/test_dialogue.py`: every aspect; subject forms (handle, room, person, words with synonyms and plurals,
   `it`, `request`); specificity order; clarify-back with three same-type facts; `known_by`/`otherwise`; each
   `necessary` mode including `observable_via` at inspect/read tier and `no_history`; routes reach every people
   prefix with identical stats to the legacy topic; legacy `topic` path byte-identical; channels (deaf voice vs text,
   phone needs network, written needs adjacency and light); language mismatch counts nothing; `questions.required`,
   `forbidden`, `max_total`; realistic: answers never give a handle for an unseen entity.
9. `tests/test_reports.py`: every kind; synonyms and unknown words (suggestions identical across a look-alike group);
   unit conversions (F to C, lb to kg, gal to l) and tolerance; severity ordinal; list subjects; `report_set`
   precision/recall edge cases (empty sets); delivery per channel including note and household; legacy form writes
   both stores; perceivability evidence check in realistic verification.
10. `tests/test_mind.py`: beliefs update only from perceived entities, identical in oracle-fast and realistic modes;
    recall keeps issued handles out of view and stays "something"-free; stale flag; `prior_knowledge` (map all, seen,
    seen_rooms, snapshots, memory), changes after the op stay unknown; save/load/replay; `mind.` evaluation paths.
11. `tests/test_requests_obs.py`: `obs.request` in both modes for in_person, phone, app, note, dark room, unfamiliar
    visitor; gesture appears in `obs.gestures`; schema-3 default requester.
12. Perception audit extensions (section 17).

**GEN**

13. `tests/test_generator_compat.py`: generation-1 digests equal the Phase 0 recording.
14. `tests/test_generator_gen2.py`: composition shares over 200 seeds within tolerance of the data weights; every
    adult has a phone with `phone_of`, keys, wallet with owners and homes; displacement rate tracks tidiness;
    seasonal outerwear (January vs July start); infants get a crib, toddlers a high chair; aquarium/bird cage/coop only
    with those pets (once pets lands); exterior steps connect porch and yard and keep connectivity; smart-home tiers;
    weather and clock fields consistent with region and date; determinism across processes; layout audit clean.

**Integration (main session):** the landing gate (19.2), the perf budget, and three end-to-end schema-3 example specs
(one per blocker family: a monitoring spec with an auto order and `responses`, a preference question with
`questions.required`, a two-phase "where I usually put it" with prior knowledge from `people.history` once people
lands) verified on 5 seeds in both perception modes.

---------------------------------------------------------------------------------------------------------------------

## 22. Implementation plan

### 22.1 Ordering

* **Phase 0 (main session, behaviour-preserving)** starts after the kernel round now in progress has landed (it is
  editing `core.py`, `engine.py`, `realism.py`, `perception.py`, the generator and type files), so the recordings
  below capture its final state:
  1. Move `wait`, `watch`, `cancel_watch`, `run_pending`, `watch_condition_visible`, `REALISTIC_WATCH_AGENT_FIELDS`
     and `NUMERIC_OPS` from `systems/core.py` to a new `kernel/timeline.py` (imported by `engine.py` so the actions
     register); `core.py` re-exports the names. This makes the package file sets disjoint.
  2. Record `tests/data/gen1_digests.json` and `tests/data/spec_baseline.json` (verdicts and reference digests of
     the 242 specs, 5 seeds, both modes).
  3. Commit stub modules with the frozen signatures of section 16.1 (`dialogue.py`, `reports.py`, `mind.py`,
     `monitor.py`, `phases.py`, `generator/profile.py`) so packages code against each other from day one.
* **Libraries** (new files of all three packages) start in parallel right after Phase 0, alongside the other tier-1
  work.
* **Shared-file integration** comes after the tier-1 chain in `TIER1_DECISIONS.md` (physics integration, camera
  runtime, camera actions/rendering, body engine, body core verbs/faults/perception), because the sweep touches the
  most shared files (`engine.py`, `world.py`, `core.py`, `realism.py`, `perception.py`, `setup.py`, `spec.py`,
  `runner.py`, `verify.py`, `eval/*`) with the least semantic overlap. Order: **TIME-EVAL, then PEOPLE-IO**. GEN edits
  no shared kernel file except `data/types/core.json` (merged after physics WP-4 and camera WP-1, which also edit it)
  and can land any time after Phase 0.
* Every merge keeps all tests (normal and strict) and all 242 specs green in both perception modes.

### 22.2 Kernel work packages (disjoint file sets)

**TIME-EVAL (agent K-TIME):** waits, notices, standing orders, temporal evaluation, phases, clock, predicates, spec,
runner, verifier, harness phase loop.

* Files: new `house_sim/kernel/monitor.py`, `house_sim/kernel/phases.py`, `house_sim/data/calendar/holidays.json`,
  `house_sim/data/calendar/dst.json`; edit `house_sim/kernel/timeline.py`, `house_sim/kernel/engine.py`,
  `house_sim/kernel/world.py`, `house_sim/kernel/plugins.py` (`@system(idle=...)`), `house_sim/kernel/evaluation.py`,
  `house_sim/kernel/predicates.py`,
  `house_sim/kernel/clock.py`, `house_sim/tasks/spec.py`, `house_sim/tasks/runner.py`, `house_sim/tasks/verify.py`,
  `house_sim/eval/run.py`, `house_sim/eval/agents.py`, `house_sim/cli.py`, `tools/verify.py`,
  `tools/bench_systems.py`; tests `tests/test_timeline.py`, `tests/test_world_savepoint.py`, `tests/test_monitor.py`,
  `tests/test_phases.py`, `tests/test_clock.py`, `tests/test_predicates_v3.py`; docs `docs/TASK_SPEC.md` (rewrite for
  schema 3; it still describes v1), `docs/EVALUATION.md`, `docs/KERNEL_API.md` (v3.1 section linking DIALOGUE.md and
  GENERATOR.md), `docs/EVAL_CONTRACTS.md` (phases, `on_request`).
* Items: sections 6 (except the people-side notice posting), 8, 8.4, 9, 10.1 (runner/state), 11.1, 16.5, the
  engine calls into `mind.update` and `Mind.from_config`, the `--gen` forcing flag, the `idle=` system probe, the
  `format_date` helper (section 15).

**PEOPLE-IO (agent K-IO):** questions, reports, memory, request rendering, setup ops, agent docs.

* Files: new `house_sim/kernel/dialogue.py`, `house_sim/kernel/reports.py`, `house_sim/kernel/mind.py`,
  `house_sim/data/vocab/core.json`, `tools/vocab.py`; edit `house_sim/systems/core.py`, `house_sim/kernel/realism.py`,
  `house_sim/kernel/perception.py`, `house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`, `house_sim/eval/llm.py`,
  `tools/perception_audit.py`; tests `tests/test_dialogue.py`, `tests/test_reports.py`, `tests/test_mind.py`,
  `tests/test_requests_obs.py`; docs `docs/DIALOGUE.md` (new), `docs/PERCEPTION.md`, `docs/EVAL_HARNESS.md`,
  `docs/AUTHORING_GUIDE.md`, `docs/CONVENTIONS.md`.
* Items: sections 4, 5, 7, 10.2, 16.2 (`ask`, `report`, `recall`, `listen` and the doc strings of the time verbs,
  which it hands to TIME-EVAL as text), 16.3 dialogue/report hooks, 16.4, 17, the people-prefix route table.

**GEN (agent K-GEN):** generation 2.

* Files: new `house_sim/generator/profile.py`, `house_sim/generator/belongings.py`,
  `house_sim/generator/furnishings.py`, `house_sim/generator/climate.py`, `house_sim/data/generator/households.json`,
  `house_sim/data/generator/belongings.json`, `house_sim/data/generator/furnishings.json`,
  `house_sim/data/generator/climate_normals.json`; edit `house_sim/generator/__init__.py`,
  `house_sim/generator/layout.py`, `house_sim/generator/place.py`, `house_sim/generator/repair.py`,
  `house_sim/data/types/core.json` (ottoman; `exterior` on stairs connections), `tools/layout_audit.py`,
  `tools/show_house.py`; tests `tests/test_generator_gen2.py`, `tests/test_generator_compat.py` (the digest file is
  recorded in Phase 0); docs `docs/GENERATOR.md` (new).
* Items: section 14, 13.1, 12 (profile and habit generation).

### 22.3 Per-domain follow-ups (after their kernel dependencies land)

| domain | work |
|---|---|
| people | route its prefixes if not done by PEOPLE-IO; channel/language/render/reliability hooks; `report_delivered`; `robot_hears`; notices (doorbell, knock, crying, call for help, phone ringing); request delivery (gestures for requests, recognition, per-requester phrasing); calendar model, calendar artifacts, `check_calendar`, schedules from events, `event_needs.json` and helper; profiles to facts; `people.history` synthesizer; feedback rules; activity engine (medium); infant/toddler/teen/roommate behaviours for gen 2 |
| admin_inventory | collection days and carrier pickups as calendar events; holiday decor by date (gen 2); belongings data (wallet, backpack, work bag, passport, umbrella, key bowl homes and sizes); report vocabulary (missing, low_stock, expired, overdue...); route `admin:`; locale date formats on labels |
| outdoor_plants | stateless weather process and forecasts (13.2) with `override_until_s`; `forecast_for`; season ground state (gen 2); exterior steps (ice, snow, doormat outside); notices (thunder, rain on windows); vocabulary (plant issues); routes `plants:` |
| electrical | smart-home furnishing by tier (gen 2) and the hub model (section 15); device notices; smoke/CO alarms as `alarm` notices; phones and chargers for belongings (`phone_of`, charging spots); DST-aware clocks; vocabulary; idle probes for power, lights, detectors |
| pets | gen-2 pet furnishing (aquarium, bird cage, small animal cage, terrarium, coop) from the composition; pet notices (barking at the door, whining, bird alarm calls); symptom vocabulary; route `pet:`; idle probe for the pets tick |
| climate | outdoor temperature from the weather process when present; thermostat mode by season (gen 2); route `climate.`; idle probe for the air step |
| textiles_laundry | types jacket, sofa_cushion, seat_cushion (and hoodie); seasonal clothing placement (gen 2); outerwear/footwear data for belongings; vocabulary (stains, tears, pilling); route `laundry:` |
| safety | candle type (lit state, fire rules); alarms as notices; hazard vocabulary; route `safety.` |
| navigation_robot | exterior same-floor stairs traversal; route `nav:` |
| appliances_cooking | done chimes and beeps as `appliance_done`/`alarm` notices; fault vocabulary; route `appliance:`; idle probe |
| food | diet and allergies from profiles in serving checks; meal log for history; locale best-by formats; route `food:` |
| cleaning | soil/stain vocabulary; dust on rarely used items for the history synthesizer; route `cleaning.` |
| plumbing_liquids | drips and leaks as notices; leak vocabulary with evidence fields; route `plumbing.`; idle probe for the water system |
| mechanisms | smart lock for the connected tier; routes `code:`, `key_location:` |
| manipulation | coaster type (with physics wet_contact); route `manip:` |
| tools_vehicles | tool fault vocabulary; route `tools_vehicles.` |

## 23. Authoring recipes

| Entry pattern (sweep count) | Mechanism | Example |
|---|---|---|
| "Tell me when the laundry's done" (130) | auto `watch` on the washer's glance state + `report(kind=status, to=requester, channel=...)`; `responses` trigger = cycle done, within 120 s; `never` notify during their call | A-d048c6c15f805e-01: wrong plans notify at the spin pause (response fires early: `never` on report before done), shout while on a call (people interruption), miss the chime (response missed) |
| Hidden preference, learned once (589) | fact `{about: type:bed, aspect: preference, detail: pillow_layout}`; `questions.required`; phase 2 next day with `max_unnecessary: 0` | T-0a7593cbd7c2d0-01 "Make bed": wrong plans skip the question (required missing), ask the child (`otherwise`), ask again next day |
| "Put it where I usually put it" (155) | `people.history` with `robot_present` -> prior knowledge and `history:` memories; request `from` the speaker; success = the speaker's modal spot | A-498dc58563286f-01: wrong plans use the generic home, the other resident's habit, or the last-seen spot |
| Inspection report (290) | `report_set` with precision/recall over findings; vocabulary issues with evidence; perceivability check | T-8adcc7a83550d2-01 "Check filters": every filter's condition as findings, `none_found` not allowed when one is loaded |
| "What changed since I left" (155) | spawn/arrange, `prior_knowledge` snapshot `-1d`, then move items; success via `report_set` of `misplaced` findings | A-8525895aaadf82-01 |
| Throughout / never (100) | `never` with windows; `responses` with `for_each_new` | "keep the floor dry while mopping": every new spill gone within 600 s |
| Several requests (60) | `phases` with gaps and per-phase questions | V-defec9c1f9917e-01 "finish after they leave": phase 2 starts when the guest leaves (`when`) |
| Who is asking (76) | `request.from` + `user_simulator.intent.requester`; authority checks by people | A-5b33cd69ede05a-01 child's command refused or confirmed with `said` |
| Calendar (100) | `calendar` op events with `hidden`; wall calendar artifact; `event_needs` helper | A-eede65634caaa7-01 "Is the gym still on tomorrow?" necessary only when the event is `tentative` |
| Weather and season (120) | gen-2 houses with the weather process; forecasts; season state | V-68a0baf1a10293-01 week-long: rain days come from the process, not a scripted event |
| Belongings (210) | gen-2 belongings with owners, homes and displacement | T-17bf62f781f86e-01 find the phone: real target and distractors (another resident's identical phone) |

## 24. Open questions

1. **Unmatched questions** count as unnecessary (today's rule, kept) plus a diagnostic stat. Alternative: neutral
   (only time cost). The proposal keeps the penalty so random asking does not pay; the lint and generous matching
   protect honest agents.
2. **Default interrupts for `wait`**: none (compatibility) with a doc hint to pass `urgent`. Alternative: `urgent` by
   default for agent episodes only, which would make behaviour depend on who calls; not proposed.
3. **Gen 2 as the tool default** (HouseEnv without a config, demo, layout audit): proposed after the landing gate;
   specs follow `schema`.
4. **Household and pet shares** are assumptions until GEN cites sources in the data files; the user may prefer
   uniform presets over census-like weights for benchmark balance.
5. **Robot memory visible to evaluation** (`mind.*`): proposed yes, read-only, to score exploration; it is the
   robot's state, not hidden world truth.
6. **Episode chaining across processes** is deferred; phases and the history synthesizer cover the catalog entries
   found so far.
7. **Notice audibility thresholds** (40 dB heard, 55 dB intelligible, -6 dB per open hop, -20 dB per closed door) are
   P-approx assumptions; people may replace them through `robot_hears` with its own propagation model.
