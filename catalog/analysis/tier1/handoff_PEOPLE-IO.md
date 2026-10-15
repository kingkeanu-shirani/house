# Handoff PEOPLE-IO (stage C, merges after TIME-EVAL): questions, reports, the robot's memory and requests

Read before starting: `docs/design/TIER1_SWEEP.md` (whole; package sections 4, 5, 7, 10.2, 15 (format_quantity),
16, 17, 18, 19, 20, 21 items 8-12, 22), `docs/design/TIER1_INTEGRATION.md` (1 with 1.3, 1.8, 1.9 and its rules,
1.11-1.14, 1.17; 2.2, 2.5; 3; 5), `docs/design/TIER1_DECISIONS.md`. Handoff files read (all that existed):
`handoff_TIER1-GATE.md`, `handoff_B0.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`,
`handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md`,
`handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`, `handoff_NAV-M2.md`, `handoff_BODY-CORE.md`,
`handoff_BODY-FAULTS.md` (with their merge and STAGE-B-FIX sections), `handoff_B6.md`, and `handoff_TIME-EVAL.md` (the
partner that merged first). Their items addressed to PEOPLE-IO are acted on: BODY-FAULTS 9 (`heard_messages` kept for
`obs.messages`; `perceived_ids` while powered down is what the robot holds; the LLM prompt), BODY-CORE 9 (`ask` / `listen`
keep `ctx.require_hearing` first; `addressed_to_robot` is the wake-word rule the notices share), CAM-RENDER 9
(`perceived_ids` in geometric realistic mode is `realism.perceived_in(step_view)`; people the camera could not identify
are no one in the Mind), GEN 9 request 2 (`obs.weather` without `process` / `override_until_s`), TIME-EVAL 4-5 (the
notice record carries `step`; `questions` 4.6 reads `dialogue_log` fields `matched`, `to`, `comprehended`, `otherwise`,
`act`, `aspect`, `t`; the engine's Mind calls; `TIER1-STUB(TIME-EVAL)` notes in `systems/core.py` removed).

## 1. Summary

* **Questions** (`kernel/dialogue.py`): the canonical `ask(target, aspect, about, detail, act, options, channel,
  language, question)` beside the byte-identical legacy `topic`: reachability by channel (voice / phone / text /
  written), languages, subject resolution (handles, ids, `it`, `request`, `house`, words through the vocabulary),
  facts with specificity, clarify-backs, one necessity framework, known_by / otherwise, reliability, grants / sets,
  calendar-implied facts, the people-prefix route table (15 routes), `dialogue_log` / `dialogue` / `facts` records,
  the people hooks, fact validation and the schema-3 lints.
* **Reports** (`kernel/reports.py`, `data/vocab/core.json`): typed `report(kind=finding|measurement|status|count|
  none_found, ...)` beside the unchanged legacy form; the vocabulary (76 issues in 8 classes, 43 states, 17
  quantities with exact unit factors, severities), unknown words with category suggestions, delivery over 6 channels
  plus `household`, `report_log`, `match` / `match_set` for TIME-EVAL's predicate forms, units for labels,
  perceivability evidence; `tools/vocab.py` (check, markdown, json).
* **Memory** (`kernel/mind.py`): the Mind (map, last-seen beliefs of things, people and pets, snapshots, prior),
  updated once per step from `perception.perceived_ids` (exactly the ids `observe()` renders, both perception and
  camera modes), `recall` forms (`map`, `seen:`, `seen:<handle>`, `seen:room:`, `snap:`), `snapshot` / `diff` in the
  Mind, realistic memory messages keeping issued handles, the `prior_knowledge` / `facts` / `calendar` setup ops.
* **Observations and agents**: `obs.request`, `obs.notices`, `obs.instruction` from the current request, `obs.weather`
  without the process bookkeeping; realistic sender rendering; `listen` returns the latest notices; the LLM prompt's
  "Asking, reporting, waiting, memory" section and whole-sentence doc cuts; skills `ask` (canonical) and `tell`;
  perception-audit kind `people`. 4 test files (110 tests), docs `DIALOGUE.md` (new) and four updated
  (`AUTHORING_GUIDE.md` also takes the sweep's recipe table, TIME-EVAL request 5).
* Gate `PEOPLE-IO` vs `TIME-EVAL` on the merged tree: `compare` PASS (0 verdict, 0 state-digest, 0
  observation-digest changes in 2,420 trials); the record's one FAIL is G1's `test_eval_splits.py`, failing
  on HEAD and in TIME-EVAL's gate alike (request 5).

## 2. Files

Created: `house_sim/data/vocab/core.json`, `tools/vocab.py`, `tests/test_dialogue.py`, `tests/test_reports.py`,
`tests/test_mind.py`, `tests/test_requests_obs.py`, `docs/DIALOGUE.md`, this handoff, the gate record
`catalog/analysis/tier1/gate/PEOPLE-IO/`.

Edited: `house_sim/kernel/dialogue.py`, `reports.py`, `mind.py` (A0 stubs, now implemented),
`house_sim/kernel/perception.py`, `house_sim/kernel/realism.py`, `house_sim/systems/core.py`,
`house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`, `house_sim/eval/llm.py`, `tools/perception_audit.py`,
`docs/PERCEPTION.md`, `docs/EVAL_HARNESS.md`, `docs/AUTHORING_GUIDE.md`, `docs/CONVENTIONS.md`.

Not edited (on the list, not needed): `tests/test_perception.py`. All on the PEOPLE-IO list of TIER1_INTEGRATION 2.5
(G10). All LF.

## 3. APIs

Frozen 16.1 names landed with their signatures; additions are marked ADDITION, differences **DEVIATION** (section 4).

**`kernel/dialogue.py`**

```
Subject(kind, id=None, words=())           kinds: entity | room | person | pet | event | house | request | word
resolve_subject(env, pid, about) -> Subject
match_fact(state, subject, aspect, detail, env=None) -> fact | None      env: registry names for type facts (ADDITION)
necessary(env, pid, fact, subject) -> bool
route(name, aspects, fn)                   fn(state, pid, subject, aspect, detail, act) -> legacy topic | None
answer(ctx, args) -> dict                  the whole pipeline of one canonical ask (core's ask serializes it)
canonical_key(aspect, subject, detail) -> "q:<aspect>:<subject>[:<detail>]"
vocabulary() -> dict                       loaded once from data/vocab/*.json; "_index" lookup tables
ADDITION: load_vocabulary(paths=None), singular, words_of, phrase, ngrams, canonical_aspect / detail / act,
  authority, is_minor, is_adult, is_resident, current_request, requester, knows, entity_names, names_entity,
  named_entities, describe, appearances, subject_text, calendar_facts, facts_of, history_consistent,
  perceived_ever, evidence_known, answerer_for, register_people_routes, find_route, reach, comprehends,
  validate_fact, lint_facts, lint_reference, lint_plan; ROUTES, ASPECTS, CHANNELS, NOT_AVAILABLE, ASLEEP
```

**`kernel/reports.py`**

```
normalize(args, state) -> record           subject: {"kind", "id"} or a list of them
record(ctx, record) -> {"records", "channel", "note", "suggest", "text"}
match(state, expectation, times=None) -> bool          times: {name: seconds} (ADDITION)
match_set(state, spec, times=None) -> (precision, recall, detail)
convert(value, unit, quantity) -> float    canonical = value * a + b; quantity may be None when the unit names one
format_quantity(state, value, quantity) -> str
issue_words(category) -> [str]             an issue class, or an entity category (category_classes)
ADDITION: issue_of, state_of, severity_of, severity_rank, unit_of, quantity_of, from_canonical, household_unit,
  state_words, subject_known, render_text, deliver, records_of, validate_expectation, evidence_perceived,
  perceivability_gaps; KINDS, CHANNELS, HOUSEHOLD, NOT_HERE, SUGGESTIONS
```

**`kernel/mind.py`**: `Mind.from_config(config, env)`, `update(env, perceived_ids)`, `view()`, `recall(prefix)`,
`snapshot(name, env)`, `diff(name, env)`, `dump()`, `load(data)` (returns the Mind); ADDITION: `of(env)`,
`map_entry(state, rid, seen_s)`, `Mind.belief(id)`, `Mind.when(t)`, `Mind.perceived_scene(env)` (static), fields
`map`, `seen`, `snapshots`, `prior`, `start`, `time_s`, `now`; `BELIEF_FIELDS`, `SNAPSHOT_FIELDS`.

**`kernel/perception.py`**: `Perception.perceived_ids(env) -> frozenset` (cached per step); ADDITION: module
`perceived_ids(env)`, `current_request(state)` (the active phase's latest request, else the last),
`request_view(state)`, `notices_since(state)`, `perceived_weather(weather)`, `WEATHER_HIDDEN`.

**`kernel/realism.py`** ADDITION: `perceived_room(state, base, dark)`, `render_request(request, state)`,
`grant_prior(eid, record, level)`, `memory_output()`, `version`; `dump()` gains `"requests"` (only when a request
was rendered).

**`systems/core.py`**: `ask` params + `about, aspect, detail, act, options, channel, language`; `report` params
`target, field, value` optional + `kind, subject, issue, severity, quantity, unit, state, of, scope, location,
channel, detail`; `recall` forms; `listen` adds notices; `snapshot` / `diff` through the Mind; ADDITION:
`legacy_answer(ctx, pid, topic)` (the unchanged legacy answer chain, used by the routes), `ASK_DOC`, `REPORT_DOC`,
`RECALL_DOC`.

**`tasks/setup.py`**: ops `facts`, `prior_knowledge`, `calendar`; `user_sim` gains `facts`.
**`tasks/skills.py`**: `ask(person, topic=None, **question)`, ADDITION `tell(person=None, channel=None, **report)`.
**`eval/llm.py`**: the system prompt section; `_cut_doc`; `render_turn` takes the observation's instruction.
**`tools/vocab.py`** `[--check] [--markdown [OUT]] [--json OUT] [--words CATEGORY]`;
**`tools/perception_audit.py`** leak kind `people` (`check_people_message`, `check_people_obs`, `people_command`).

Hooks (16.3): `dialogue_channel`, `dialogue_comprehends`, `dialogue_render`, `dialogue_reliability`,
`report_delivered`, `mind_seen` (signatures in docs/DIALOGUE.md and CONVENTIONS 8).
State sections (16.6): `dialogue_log` (cap 300), `dialogue`, `report_log` (cap 500); stats `questions_unmatched`,
`reports_unclassified`.

## 4. Deviations from the spec

1. **Legacy reports are not copied into `report_log`** (sweep 5.4 logs them as `{"kind": "field", ...}`): that would
   change the state of all 53 specs that use the legacy form, and the PEOPLE-IO gate row allows no state change ("the
   legacy report store must not change"). `reports.records_of` synthesizes the same records from the legacy store
   (time from the event log while it holds the entry), so `match` / `match_set` see both.
2. **The legacy `topic` path writes no `dialogue_log` record** (byte-identical legacy path, 3.2). `questions.required`
   / `forbidden` therefore concern canonical asks; `max_total` (evaluation.py's) counts dialogue_log records.
3. **"Asked before" means the robot has the answer** (necessity): a previous canonical answer of the same key (its
   value is in `facts`) or of the same fact. Asking someone who answered "otherwise" (did not know) does not make the
   next person's answer unnecessary (sweep 4.5 counted any earlier ask).
4. **Word subjects naming one room, person or pet by label are that room, person or pet** (sweep 4.4 keeps words as
   word subjects): "ask Sam about the living room" reaches `room:` facts.
5. **Routes see the thing a word names**: a word no route takes is retried as the household thing it names when it
   names exactly one; when it names several and a route would answer one of them, the person asks back which one
   (sweep 4.3 step 5 describes the clarify-back for facts only).
6. **Clarify options describe things by appearance** (label, then colour, then material), never by where they are
   now: describing current locations would hand the agent the hidden position of things it has not found (the sweep's
   example used support and room). `refers_to` (the places an answer names) keeps support and room: that is the
   answer's content.
7. **Kernel-default calendar answers**: each `household.calendar` event is a `when` fact and each `hidden` key a
   `status` fact (sweep 11.2 gives calendar semantics to people); without it, asking about an event would have needed
   the people follow-up first. People may replace it by routes.
8. **Hearing is required for voice and phone questions only**; text and written questions work with the microphones
   off (the answer is read). The 1.15 refusal wording stays for voice.
9. **`to` is a list in report records** (`["resident_1"]`, `["household"]`), so app reports to every resident fit;
   a household-only report has channel None. A typed report refused for an unknown subject id says "You have not seen
   that" (precondition) whatever the reason (an unseen thing, a true id typed in realistic mode).
10. **Delivery defaults without the people hook**: in person / intercom heard when awake, not deaf and the robot's
    speaker works; phone heard when awake and not deaf; text and app queued with `read_s` = now + `ack_delay_s`
    (default 300 s) and count as heard once the clock passes it; intercom needs an intercom station in both rooms; a
    note needs a held writable thing or a whiteboard within reach and writes the robot's perceived wording.
11. **Domain report hooks**: a typed report sets `ctx.args["target"]` and `["field"]` to None (food, safety and
    tools_vehicles judge legacy fields in `@after("report")` hooks and would otherwise crash); a routed canonical ask
    sets `ctx.args["topic"]` to its legacy topic so domain `@after("ask")` hooks judge it the same way (safety's
    approval policy).
12. **The Mind remembers people and pets too** (beliefs with `"agent"`), so "a person it has seen" (report
    precondition) and `recall("seen:<pid>")` work; people the robot could not identify (dark, too far) are no one.
13. **`perceived_ids` excludes un-inspected mirrors** (they render as openings) and, with realistic perception in the
    dark, people and pets (shapes). It is exactly the rendered set (a randomized test compares them in both modes and
    cameras).
14. **`snapshot` / `diff` keep the observation's values** (what was perceived, as before kernel v3.1) with true ids in
    the Mind; realistic messages translate them back to the issued handles, so oracle and realistic messages are as
    before (realistic: in handle order).
15. **`recall` prints times in whole seconds** with realistic perception (memory messages are action messages, which
    the engine's observation clock rounding does not reach).
16. **`listen`** returns `{"messages": [...], "notices": [...]}` only when the episode stores notices (schema 3);
    otherwise exactly as before.
17. **The LLM catalog cuts a long doc at the last sentence end within the limit** when that keeps three quarters of
    it (else as before, with "..."): the schema sentences of ask, report and recall (and TIME-EVAL's wait / watch)
    arrive whole. The doc strings themselves are longer than 120 characters (their first sentence carries the schema).
18. **`obs.notices` lists the notices stamped with the current step** (TIME-EVAL stamps `step`; unstamped records by
    time, strictly after the step started); realistic notices round `t` to whole seconds.
19. **Prior knowledge records** carry the op-time copy of each record granted above glance (`records`), so the
    knowledge signature is the op's moment and lapses once the thing moved; `at` defaults to "-1d"; a top-level `at`
    applies to the map.
20. **Question scoring** (sweep 4.6) is evaluation.py's `question_failures` (TIME-EVAL): this package writes the
    records it reads instead of a second implementation. The record fields TIME-EVAL assumed (its deviation 16:
    `matched`, `to`, `comprehended`, `otherwise`, `act`, `aspect`, `t`) are the ones written (confirmed).
21. **The current request** is the active phase's latest request (`state.phase.active`), else the last record:
    during a gap the previous phase's request stays shown.

## 5. Stubs and contracts

* Removed: every `TIER1-STUB(PEOPLE-IO)` marker of A0 in `dialogue.py`, `reports.py`, `mind.py`; the
  `TIER1-STUB(TIME-EVAL)` fallbacks in `systems/core.py` (an engine without a Mind) after TIME-EVAL landed.
* Left in TIME-EVAL's files, naming PEOPLE-IO (request 1; the code they guard is dead now that the modules are
  implemented): `engine.py` `_start_mind` / `_update_mind` / `load` / `_mind_dump`, `evaluation.py`
  `evaluation_view`, `monitor.py` `_Sampler.view`, `predicates.py` `_check_form` (`reports.match` / `match_set`).
  `grep -rn "TIER1-STUB" house_sim tools` lists exactly these.
* **Stage-C merge (section 10):** those markers are gone (request 1 done); requests 2 and 3 are done there too.
* Consumed: TIME-EVAL's engine calls (`Mind.from_config(world.s, env)` at reset, `mind.update(env,
  perception.perceived_ids(env))` after `note_step`, Mind dump / load in save / load / replay), `world.LIST_SECTIONS`
  (report_log, dialogue_log), the stored notice record (with `step`), `state.requests`, `evaluation.question_failures`,
  `clock.parse_when`; BODY-CORE's `ctx.require_hearing` / `require_imaging`, `body.can_speak`; CAM-RENDER's
  `realism.perceived_in`, `geo_detected`, `_view_info`; realism's handles, knowledge and identity.

## 6. Gate results

Gate id **PEOPLE-IO**, package PEOPLE-IO, previous gate **TIME-EVAL** (the partner merged first; comparing with it
isolates this package's differences), record in `catalog/analysis/tier1/gate/PEOPLE-IO/`, taken on the merged
working tree (HEAD + TIME-EVAL + PEOPLE-IO) with `--shared-tree` (TIME-EVAL's 34 files are attributed to it):

```
python tools/orchestration/tier1_gate.py record --id PEOPLE-IO --package PEOPLE-IO \
       --prev catalog/analysis/tier1/gate/TIME-EVAL --shared-tree --jobs 7
python tools/orchestration/tier1_gate.py record --id PEOPLE-IO --package PEOPLE-IO \
       --prev catalog/analysis/tier1/gate/TIME-EVAL --shared-tree --jobs 5 --only G1    # G1 again, quieter
python tools/orchestration/tier1_gate.py compare TIME-EVAL PEOPLE-IO --package PEOPLE-IO
```

Summary line: `TIER1-GATE PEOPLE-IO [PEOPLE-IO] vs TIME-EVAL: FAIL (11 pass, 1 fail, 1 skip; this run: G1)`;
`compare TIME-EVAL -> PEOPLE-IO`: PASS (0 verdict, 0 state-digest, 0 observation-digest changes).

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `24641cb8a126` -> `955c5ef9ac14` (S-R6: `ask`, `report` parameters; allowed) |
| G1 | FAIL | 55 files, 3,797 tests (normal), normal + strict: the only failing runs are `test_eval_splits.py` `test_gitignore_covers_private_artifacts` (normal and strict): `.gitignore` line 34 ignores `demos/replays/`, a file neither stage C package touches (TIME-EVAL's gate fails the same way; request 5). Two timing tests flaked once each under the 7-job gate and passed in the recorded G1 run (5 jobs) and alone: `test_perception` realistic observe overhead (10.35 ms vs its 10 ms bound; 2.7 ms alone) and `test_generator_gen2` gen-2 cost ratio (1.68 vs 1.6; GEN's code, untouched here) |
| G1b | PASS | isolated domains: the 8 failing tests known since B6 (none new); kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 (the four new files included) |
| G2 | PASS | own mode 241/242 (the known A-d9e7a4d5d112d2-01 seed 0, TIME-EVAL's reviewed S-R1); vs TIME-EVAL 0 verdict, 0 state-digest, 0 observation-digest changes in 1,210 trials |
| G3 | PASS | forced realistic 225/242; vs TIME-EVAL 0 / 0 / 0 changes; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes (the walk now asks, reports, recalls, snapshots and diffs; leak kind `people`) |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.06 ms per substep (TIME-EVAL 3.16, limit 3.318, -3.2 %), 1 h wait 1.116 s (1.119, limit 1.175, -0.3 %), quiet day 6.331 s (6.573, limit 6.902, -3.7 %); an earlier run on a busier machine measured +2.9 / +3.1 / +3.7 %, within the limits too |
| G8 | PASS | room golden and golden_all byte-identical (not re-recorded) |
| G9 | PASS | `tools/sweep_bench.py`: ask 0.677 ms (budget 2), report 0.089 ms (1), mind_update 0.141 ms (1); TIME-EVAL's rows unchanged |
| G10 | PASS | 56 changed files: 22 PEOPLE-IO's (all on its list), 34 TIME-EVAL's (attributed) |

`compare TIME-EVAL PEOPLE-IO --package PEOPLE-IO`:

```
compare TIME-EVAL -> PEOPLE-IO (packages: PEOPLE-IO; allowed: fingerprint,obs; codes: S-R5,S-R6,S-R7)
G0   PASS  action fingerprint changed 24641cb8a126 -> 955c5ef9ac14 (allowed for this package)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 398 -> 395
G7   PASS  substep_ms: 3.16 -> 3.06 (-3.2%; limit 3.318 = prev x 1.05)
           wait_1h_s: 1.1192 -> 1.1158 (-0.3%; limit 1.175 = prev x 1.05)
           day_s: 6.573 -> 6.331 (-3.7%; limit 6.902 = prev x 1.05)
G1   INFO  new test file test_dialogue.py (normal): 53 tests
           new test file test_dialogue.py (strict): 53 tests
           new test file test_mind.py (normal): 18 tests
           new test file test_mind.py (strict): 18 tests
           new test file test_reports.py (normal): 24 tests
           new test file test_reports.py (strict): 24 tests
           new test file test_requests_obs.py (normal): 15 tests
           new test file test_requests_obs.py (strict): 15 tests
compare result: PASS
```

**Reviewed differences:** none needed. No verdict, state digest or observation digest of the 242 specs changed in
either mode: every new observation key appears only with the new features (requests, the notice bus), the legacy
`topic` / `report` paths are byte-identical (a test swaps the pre-v3.1 `ask` handler in and compares messages and
state digests), the Mind lives outside the state, and the realistic handles the Mind issues are the ones the
observation issues anyway (realistic runs observe every step). **Golden:** unchanged, not recorded.

**Budgets** (TIER1_INTEGRATION 3.4; best of 5, this machine; scratch `bench_people.py`, generated houses of 649 / 871 /
929 entities, oracle and realistic):

| item | budget | measured |
|---|---|---|
| `ask`, a word matched to a fact / a word naming many things / a handle | 2 ms | 0.57-0.78 / 1.13-1.65 / 0.06 ms |
| `report` (normalize + record, three subjects) | 1 ms | 0.03 ms |
| `mind.update` per step (900-entity house) | 1.0 ms | 0.15-0.23 ms warm, 0.21-0.29 ms cold |
| `perception.perceived_ids` (computed once per step, shared by the update and the memory checks) | (part of the update) | 0.40-0.54 ms oracle, 0.73-0.97 ms realistic room camera, 0.01 / 0.1 ms geometric |
| vocabulary load | 20 ms | 1.9 ms |
| realistic observe overhead (test_perception's target 3 ms) | 3 ms | 2.7 ms in isolation |

The Mind's update (perceived set + beliefs, about 0.4-1.2 ms) runs once per step, so a long wait (one step) pays it
once: the substep, 1 h wait and quiet-day benchmarks are within noise of TIME-EVAL's.

## 7. Known gaps

* **Look-alike answers.** A person's answer naming an unresolved look-alike's true type that the command itself named
  passes through realism's label substitution and reads as the group's descriptor. *Corrected by the stage-C fix
  (section 11, P20):* clarify-back options are not rewritten -- they are the person's own words for their things
  ("milk", "oat milk, 2 alike", unseen ones counted too), decided to be household knowledge a resident has; they are
  descriptions, never handles, so they do not tell which handle is which.
* **`env.known_map()` stays the observation-time legacy map** (engine.py, TIME-EVAL's); the Mind's map is the
  per-step one (`recall("map")`, `mind.map`). Sweep 7.1 wants the former as a view of the latter (request 4).
* **Legacy report records ignore predicate time bounds**: TIME-EVAL's `predicates._report_world` bounds only
  `report_log` before calling `reports.match` (legacy field records have no reliable time); harmless for every
  current spec (no spec mixes `after` with the legacy form), request 3.
* **The verifier does not call the dialogue lints or `reports.perceivability_gaps` yet** (verify.py, TIME-EVAL's):
  the helpers exist and are tested (request 2). No schema-3 spec exists yet.
* **Word understanding is lexical**: synonyms, plurals and stop words only; "the thing next to the TV" names
  nothing. Persons understand words through types, labels, colours, materials and look-alike descriptors.
* **Delivery is the kernel default** until people's `report_delivered` (who reads texts and notes, intercom stations
  as people model them); text messages count as read after `ack_delay_s` whether or not the person looks at a phone.
* **Calendar facts are a kernel default** (when / hidden status); calendar artifacts, `check_calendar` and schedules
  from events are people's (adoption round).
* `ask` costs up to about 1.6 ms for a word naming many household things (a scan of every entity's names with a
  memo per name signature); an index per World version would make it constant if catalog authoring shows a need.

## 8. How to test

```
python -m unittest tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs   # 110 tests, ~15 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
python tools/vocab.py --check                      # 0 problems; --markdown regenerates DIALOGUE.md's tables
python tools/perception_audit.py 6 150 --workers 4 [--camera geometric] [--battery 5 --faults random]
python tools/sweep_bench.py                        # ask / report / mind_update rows
python tools/orchestration/tier1_gate.py compare TIME-EVAL PEOPLE-IO --package PEOPLE-IO
```

Manual checks:

```python
from house_sim.kernel.engine import HouseEnv
env = HouseEnv(seed=0, perception="realistic")
pid = next(p for p, r in env.world.s["people"].items() if r["location"] == env.world.s["robot"]["room"])
env.step("ask", pid, aspect="where", about="mug")["message"]          # a clarify-back or a routed answer
env.step("report", kind="finding", subject=sorted(env.observe()["visible_entities"])[0], issue="dripping", to=pid)
env.step("recall", "map")["message"]; env.step("recall", "seen:")["message"]
```

## 9. Follow-ups

Requests to the main session (files outside PEOPLE-IO):

1. **Remove the `TIER1-STUB(PEOPLE-IO)` markers** in TIME-EVAL's files (the modules they guard are implemented):
   `house_sim/kernel/engine.py` (`_start_mind`, `_update_mind`, `load`, `_mind_dump`: the `NotImplementedError`
   guards and the `perceived_ids` getattr), `evaluation.py` (`evaluation_view`), `monitor.py` (`_Sampler.view`),
   `predicates.py` (`_check_form`). PEOPLE-IO may not edit them (2.5); the code is dead, behaviour does not change.
2. **Verifier** (`house_sim/tasks/verify.py`): for `"schema": 3` specs run `dialogue.lint_facts(state, env,
   reference_env)`, `dialogue.lint_reference(task, reference_state)` and `dialogue.lint_plan(reference_plan)` (sweep
   4.11, errors), and in the realistic perceivability check add `reports.perceivability_gaps(reference_env,
   scored typed records, notice kinds perceived, grants)` (sweep 5.5) for the records a spec's `report` / `report_set`
   predicates match.
3. **`predicates._report_world`**: bound the legacy `reports` store too (or pass `after` / `before_s` through:
   `reports.match` resolves them itself, the `$temporal` overlay included).
4. **`engine.known_map()`** as a view of `env.mind.map` (sweep 7.1); `navigation_robot/paths.py` reads
   `env._known_map` keys and exits (keep them or move it to the Mind in the adoption round).
5. `tests/test_eval_splits.py` `test_gitignore_covers_private_artifacts` fails: `.gitignore` line 34 ignores
   `demos/replays/`, which the test expects to stay tracked (TIME-EVAL's gate shows the same failure); not a stage C
   file.

For the adoption round (TIER1_INTEGRATION 6):

* **people**: routes for the remaining prefixes and channel / language / render / reliability hooks; the
  `report_delivered` hook (who heard, read a text, found a note); profiles to facts (`preferences` already reach
  `pref:`); the history synthesizer emitting `prior_knowledge` data and `history:` memories; calendar artifacts and
  `check_calendar`; mark speech to the robot `to: "robot"`.
* **every domain with an answerer prefix**: register its route (CONVENTIONS 8); domains add issue / state words with
  evidence fields in `data/vocab/<domain>.json` (`python tools/vocab.py --check`, then `--markdown` for the docs).
* **domains with `@after("report")` judges** (food, safety, tools_vehicles): judge typed reports from `report_log`
  if their entries move to typed reports.
* **C-CLOSE**: the action fingerprint changed again (S-R6): regenerate saved states and replays with TIME-EVAL's.

## 10. Stage-C merge gate (C-MERGE)

Added by the stage-C merge gate after both stage-C packages had handed over. The same section is in
`handoff_TIME-EVAL.md` and `handoff_PEOPLE-IO.md`. The merge gate read every `handoff_*.md`, TIER1_INTEGRATION (1 with
1.3, 1.8, 1.9 and its rules, 1.11-1.14; 2.5; 3; 5), TIER1_DECISIONS and TIER1_SWEEP (4.10-4.11, 5.4-5.5, 6, 7.1-7.4,
9, 10, 16-19, 21, 23). It worked on the merged tree (HEAD + TIME-EVAL + PEOPLE-IO) within the union of the two
packages' files (2.5), then ran the full gate there with `--package TIME-EVAL+PEOPLE-IO`.

**Files edited by the merge** (all on the union's list; LF): TIME-EVAL's `house_sim/kernel/engine.py`,
`evaluation.py`, `monitor.py`, `predicates.py`, `timeline.py`, `house_sim/tasks/verify.py`, `tests/test_timeline.py`,
`tests/test_phases.py`, `tests/test_predicates_v3.py`, `docs/KERNEL_API.md`, `docs/EVALUATION.md`,
`docs/TASK_SPEC.md`; PEOPLE-IO's `house_sim/kernel/perception.py`, `dialogue.py`, `reports.py`,
`tools/perception_audit.py`, `tests/test_dialogue.py`, `tests/test_reports.py`, `tests/test_mind.py`,
`tests/test_requests_obs.py`, `docs/PERCEPTION.md`, `docs/DIALOGUE.md`; and both handoffs.

**Stub calls.** Every `TIER1-STUB(PEOPLE-IO)` guard left in TIME-EVAL's files is gone; the real implementations are
called directly: `engine.py` (`_start_mind`, `_update_mind`, `load`, `_mind_dump`), `evaluation.py`
(`evaluation_view`), `monitor.py` (`_Sampler.view`), `predicates.py` (`report` / `report_set`). `docs/KERNEL_API.md`
no longer says the Mind may be a stub, and its stale stage-B sentence naming `TIER1-STUB(CAM-RENDER)` now says the
guard is gone. `grep -rn "TIER1-STUB" house_sim tools tests docs` finds nothing outside `docs/design/`.

**What did not fit together, and the fixes.** Items 1-7 have regression tests; the tests of items 1-6 and the
`lint_plan` part of item 7 were checked to fail with their fix undone (in a scratch copy of the tree); the verifier
functions of item 7 did not exist before. Item 8 is a tool change, checked by the gate's G4 run.

1. **A realistic replay lost the prior knowledge.** (`engine.py`, TIME-EVAL's; PEOPLE-IO's `Mind.from_config`.)
   `HouseEnv.replay` re-creates the Realism after the constructor's reset, but the Mind had been started (and had
   issued the prior knowledge's handles and granted its read / inspect levels) against the discarded one. The
   replayed robot no longer knew a jar it read yesterday, and its recall, Mind and realism dump all differed from the
   recorded episode. Fix: `replay` starts the Mind again right after the new Realism and `_note_view` (the order of
   `reset`). Test: `test_mind.PersistenceTests.test_replay_rebuilds_prior_knowledge_in_the_realism`.
2. **A phase without a request showed the previous phase's instruction.** (`perception.py`, PEOPLE-IO's;
   `engine.py`.) `perception.current_request` fell back to the last request of any phase, so `obs.request` and
   `obs.instruction` (and with them the LLM agent's instruction and the harness's `on_request`) still showed phase 1's
   request while `env.request()` and the task's instruction were phase 2's. Fix: one rule,
   `perception.current_request`: the active phase's latest request, or between phases the request of the phase that
   ended last, and None when that phase came without one (its instruction is then the task's); `env.request()` now
   returns that record (or the r0 default). Test: `test_requests_obs.PhaseTests.
   test_a_phase_without_a_request_shows_its_own_instruction`.
3. **Two rules for "the requester".** (`evaluation.py`, TIME-EVAL's; `dialogue.py`, PEOPLE-IO's.) The evaluation's
   `ask_whom: "requester"` took `user_simulator.intent.requester` first (the runner binds it to phase 1's requester),
   the dialogue's `known_by: "requester"` the current request's `from`. In a phase asked by someone else, a required
   question answered by that phase's requester counted as asked of someone who knows, yet failed `ask_whom`. Fix:
   `dialogue.requester` is the one rule (the current request's `from`, else `intent.requester`, else the household's
   `requester` from `schema.normalize`) and `evaluation._requester` calls it. Test: `test_requests_obs.PhaseTests.
   test_the_requester_of_a_phase_is_its_requests_from`.
4. **A phase's request was never heard as a notice.** (`timeline.py`, TIME-EVAL's; seen through PEOPLE-IO's
   `obs.notices`, `listen` and waits.) The message cursor of `derive_notices` was the boundary key (steps, time_s), so
   a request posted at that boundary after its first derivation (a phase that starts in a wait or a gap) was never
   fresh: a `wait(interrupt_on=["addressed"])` slept through the new request, and `obs.notices` never listed it,
   although the engine meant it to ("a new phase's request reaches a listening wait at this boundary"). Fix: the
   cursor also counts the messages already at its key (`_msg_cursor`), and the engine derives notices after a phase
   change at the end of a step and in `phase_done`. The episode's first request stays `obs.request` only (the cursor
   starts after it is posted). Tests: `test_requests_obs.PhaseTests.
   test_a_phase_request_is_heard_and_phase_done_shows_only_new_notices`,
   `test_a_listening_wait_hears_the_request_of_a_phase_that_starts_in_it`.
5. **`phase_done` repeated the last step's notices.** (`engine.py`; PEOPLE-IO's `perception.notices_since`.)
   `phase_done` counts no step, so its observation listed the notices stamped with the step the agent had already
   seen. Fix: with the notice bus on, `phase_done` writes `state.phase.notice_mark = {"step", "t", "n"}` and
   `notices_since` lists only what was perceived after it. Test: the first test of item 4.
6. **Report windows ignored legacy reports.** (`predicates.py`, TIME-EVAL's; PEOPLE-IO's `reports.match`.) The
   predicate bounded `report_log` by `after` / `before_s` and passed the expectation on without them, so a legacy field
   report (which `reports.records_of` also matches) counted whenever it was made (PEOPLE-IO request 3). Fix:
   `predicates.report_bounds` resolves the `after` anchor to seconds and `reports.match` / `match_set` apply the
   window to every record (a legacy report whose time the event log no longer holds is outside every window). Test:
   `test_predicates_v3.ReportFormTests.test_report_forms_bound_typed_and_legacy_reports_by_their_window` (now on the
   real module; it replaced the stub-era patched test).
7. **The verifier ran neither the dialogue lints nor the report evidence check.** (`tasks/verify.py`, TIME-EVAL's;
   PEOPLE-IO's `dialogue.lint_*` and `reports.perceivability_gaps`; sweep 4.11 and 5.5; PEOPLE-IO request 2;
   DIALOGUE.md already said the verifier called them.) Fix: `verify.dialogue_lint` runs for `"schema": 3` specs after
   the reference run (`lint_plan` on the reference, `lint_facts`, `lint_reference` for the task's and each phase's
   questions with the wrong plans); a problem fails the reference (`dialogue_lint`). With realistic perception,
   `verify.report_perceivability_gaps` checks the records the spec's `report` / `report_set` success predicates
   score (a report_set's correct ones; not under `not`) with the fields the reference run's observations showed
   (things, people and pets) and the perceived notice kinds as evidence; gaps join `perceivability_gaps`
   (`{"report": ...}`). Both read the phases' success too (`verify.success_rules`, also used by the legacy check).
   `dialogue.lint_plan` now looks inside foreach / repeat blocks and catches a do step's legacy `destination` alias;
   `reports.matching_records` / `matching_set_records` give the scored records (`match_set` shares their
   selection). Nothing changes for the 242 specs (no schema-3 spec, no report form). Tests:
   `test_dialogue.LintTests.test_reference_lints` (extended), `test_the_verifier_runs_the_lints_after_the_reference`;
   `test_reports.PerceivabilityTests.test_the_verifier_checks_the_reports_its_success_predicates_score`,
   `test_matching_records`.
8. **The perception audit never saw a notice.** (`tools/perception_audit.py`, PEOPLE-IO's.) Its houses ran without
   the notice bus (data-gated by TIME-EVAL for schema-3 episodes), so the `people` checks of `obs.notices` never ran.
   Its houses now run with the bus on and half its waits listen (`interrupt_on`); 0 leaks, 0 crashes (room,
   geometric, battery).

**End to end** (TIER1_SWEEP 21, "Integration"): three schema-3 specs that need both packages, kept as tests (seed 0,
both perception modes, generated houses with core types so they pass core-only):
`test_timeline.EndToEndTests` (an auto standing order reports Sam's status by text to the requester; a response
obligation, a `never` window before the milestone and a `report` predicate after it), `test_dialogue.EndToEndTests`
(a fact, the canonical ask skill, `questions.required` and `ask_whom: "requester"`; the schema-3 legacy-topic
variant fails `dialogue_lint`), `test_phases.EndToEndTests` (two phases with a gap, a gap event and the dock
relocation, prior knowledge, the question asked in phase 1 and not again in phase 2). On seeds 0, 1, 2, 41, 97 (their
default generation-2 houses) all three pass in their own mode and under forced realistic perception with the room
camera; with the geometric camera the preference and two-phase specs pass in both modes and the monitoring spec in
its own mode only (the first known gap below). Every wrong plan fails for its own reason
(`never:early`, `response:told`, `question_missing`, `phase_failed:p1/p2`), and the traced reference runs of 36
combinations (3 specs x oracle / realistic x room / geometric x seeds 0, 1, 41) replay, save and load to identical
states, Minds and evaluations. The sweep's third example ("where I usually put it" from `people.history`) waits for
the people domain; the two-phase question spec stands in for it.

**Gate.** Gate id `C-MERGE`, packages `TIME-EVAL+PEOPLE-IO`, previous gate `B6`, record in
`catalog/analysis/tier1/gate/C-MERGE/`, taken on the merged working tree (its `git status` lists only the union's
files, so no isolated copy was needed):

```
python tools/orchestration/tier1_gate.py record --id C-MERGE --package TIME-EVAL+PEOPLE-IO \
       --prev catalog/analysis/tier1/gate/B6 --jobs 7 --work <scratch>/gate_work
```

Summary line: `TIER1-GATE C-MERGE [TIME-EVAL+PEOPLE-IO] vs B6: FAIL (11 pass, 1 fail, 1 skip)`. The one FAIL is G1's
`test_eval_splits.py` `test_gitignore_covers_private_artifacts` (normal and strict), which fails at HEAD and in both
package gates alike (`.gitignore` ignores `demos/replays/`; TIME-EVAL request 2, PEOPLE-IO request 5). `compare`
passes against B6, TIME-EVAL and PEOPLE-IO. After this section was written, G10 was refreshed with `--only G10`
(PASS; the rest of the record kept).

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `955c5ef9ac14` (425 actions, 111 systems), PEOPLE-IO's: the merge changed no action |
| G1 | FAIL | 55 files, 3,808 tests (normal), normal + strict; only `test_eval_splits.py` fails (above). The merge's tests pass in both runs: `test_dialogue` 55, `test_mind` 19, `test_phases` 18, `test_predicates_v3` 11, `test_reports` 26, `test_requests_obs` 19, `test_timeline` 37 |
| G1b | PASS | isolated domains: the 8 failing tests known since B6, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 241/242 (1,210 trials); vs B6 the one reviewed change of TIME-EVAL's table (the safety spec, seed 0), 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 225/242; the same reviewed change, 0 / 0 digest changes; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric (now with the notice bus and listening waits): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.10 ms per substep (B6 3.11, limit 3.265), 1 h wait 1.111 s (1.117, limit 1.173), quiet day 6.329 s (PEOPLE-IO 6.331, TIME-EVAL 6.573) |
| G8 | PASS | room golden and golden_all, unchanged (not recorded) |
| G9 | PASS | `tools/sweep_bench.py`: notices 0.0016 ms (budget 0.03), until 0.0005 (0.02), monitor_idle 0.0028 (0.05), monitor_quant 0.0325 (0.5), calendar 0.337 (20), ask 0.598 (2), report 0.257 (1), mind_update 0.146 (1) |
| G10 | PASS | 56 changed files, 0 outside the union |

`compare PEOPLE-IO C-MERGE` and `compare TIME-EVAL C-MERGE` (both with `--package TIME-EVAL+PEOPLE-IO`): 0 verdict,
0 state-digest and 0 observation-digest changes in both modes; the merge's fixes touch only the new features
(phases, requests, the notice bus, report forms, schema-3 verification, realistic replays with prior knowledge),
which no authored spec uses yet. **Reviewed differences:** none new (TIME-EVAL's S-R1 row covers the one change
against B6). **Golden:** unchanged.

**Known gaps found by the merge, not fixed here** (outside what the two packages can settle alone):

* **`timeline.perceivable` is not camera- or light-aware for realistic perception.** It uses room-mode visibility
  for things and counts a person's or pet's location and posture whenever they are in the robot's room, also in the
  dark, with the cameras off and, with the geometric camera, out of view. So a `wait(until=...)` or a standing order
  can fire on what the robot does not perceive (TIER1_INTEGRATION 1.3 asks for perceivable signals only). The new
  report evidence check exposes it: the monitoring spec above fails realistic verification with the geometric camera
  (`perceivability_gaps: report 1 (status awake of sam)`: Sam stood up behind the robot, the order fired, the robot
  never saw him). Request to the main session (before D3 makes the geometric camera the realistic default): for
  realistic perception, test what the robot perceives now (in room mode the room lit, imaging on, the thing or
  person not concealed; in geometric mode the camera's current view), per referenced id so the `until` budget of
  0.02 ms per substep holds; the same for `person_enters` / `person_leaves` notices in geometric mode.
* `env.known_map()` / `env._known_map` remain the observation-time map, not a view of `mind.map` (sweep 7.1):
  `navigation_robot/paths.py` (outside the union) reads its keys and exits. PEOPLE-IO request 4 stands (adoption
  round).
* The report evidence check does not collect inspect / read results or instrument readings from action messages;
  realism's knowledge at the end of the reference run stands for them (it lapses once the thing moved, which can
  report a gap for a thing inspected and then moved).

**Requests carried over.** TIME-EVAL 1 (the safety spec fix of A-d9e7a4d5d112d2-01), TIME-EVAL 2 = PEOPLE-IO 5
(`test_eval_splits.py` vs `.gitignore`), TIME-EVAL 3, 4 and 6-9 stand. PEOPLE-IO 1 (stub markers), 2 (verifier lints
and evidence) and 3 (report windows) are done here; PEOPLE-IO 4 stands.

**How to test the merge**

```
python -m unittest tests.test_mind tests.test_requests_obs tests.test_predicates_v3 tests.test_dialogue tests.test_reports tests.test_timeline tests.test_phases
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_mind tests.test_requests_obs tests.test_predicates_v3 tests.test_dialogue tests.test_reports tests.test_timeline tests.test_phases
python tools/orchestration/tier1_gate.py compare B6 C-MERGE --package TIME-EVAL+PEOPLE-IO
python tools/orchestration/tier1_gate.py compare PEOPLE-IO C-MERGE --package TIME-EVAL+PEOPLE-IO
grep -rn "TIER1-STUB" house_sim tools tests docs | grep -v "docs/design"        # nothing
```

## 11. Stage-C fix (STAGE-C-FIX)

Added by the stage-C fix pass after the stage-C review (`catalog/analysis/tier1/stageC_review_time.json`, 11 findings;
`stageC_review_people.json`, 22 findings). The same section is in `handoff_TIME-EVAL.md` and `handoff_PEOPLE-IO.md`.
Read before starting: both review files in full, TIER1_SWEEP (4-10, 16-23), TIER1_INTEGRATION (1 with 1.3, 1.8, 1.9;
2.5; 3; 5), TIER1_DECISIONS and every `catalog/analysis/tier1/handoff_*.md` (TIER1-GATE, B0, B6, GEN, NAV-M2,
PHYS-DATA, PHYS-STATICS, PHYS-FLUIDS, PHYS-INT, CAM-SIGHT, CAM-RUNTIME, CAM-ACTIONS, CAM-RENDER, BODY-MODEL,
BODY-ENGINE, BODY-CORE, BODY-FAULTS, TIME-EVAL, PEOPLE-IO). Every finding was reproduced with the reviewers' probes
(`t1_reviewC_time/`, `t1_reviewC_people/`) before and after its fix. Work stayed inside the union of the two packages'
files (TIER1_INTEGRATION 2.5).

### Findings and what was done

TIME (stageC_review_time.json):

| # | finding (severity) | status | fix | regression test |
|---|---|---|---|---|
| T1 | until / watch / person notices use room-mode visibility; identity oracle by id in the dark (high) | fixed | `timeline.observed` / `perceivable` / `holds_perceived` judge every atom per referenced id: `entity_seen` (room camera: visible, not a dark shape, realistic not missed; geometric: `camera.entity_view_now`, the step's view at the level the field needs), `agent_seen` (realistic: lit room / identified in the view); three-valued all / any / not; `wait`'s at-once check, standing orders and guards use `holds_perceived`; notices name a source only when `source_perceived` (person_enters by `agent_seen`, person_leaves by the room rule or the camera log's sightings, so save / load stays exact); speech notices no longer carry `data.speaker`; in the dark every typed person id is refused by realism (P4) with one wording | `test_timeline.StageCFixTests`: `test_until_and_watch_need_the_thing_seen_now`, `..._a_wait_on_someone_tells_nothing_about_who_is_there`, `..._a_person_behind_the_geometric_camera_is_not_observed`, `..._a_condition_seen_hidden_does_not_end_a_wait_at_once`, `..._a_speech_notice_names_its_speaker_only_when_seen`, `..._an_any_condition_ends_on_the_branch_the_robot_sees` |
| T2 | nothing wakes a waiting robot when the smoke alarms sound (high) | fixed | kernel bridge in `derive_notices`: an entity that starts sounding (alarm_state alarm / testing, T3 / T4 pattern, beeping; static index of alarm-capable entities) posts an `alarm` notice at its `alarm_db` (default 95 dB at 1 m) through the 6.2 propagation (heard in privacy mode too); `alarm_state` joins OBSERVABLE_FIELDS and is observable by sound when compared with "alarm" | `test_smoke_alarms_post_alarm_notices_and_can_be_waited_on`, `test_an_unattended_wait_hears_the_safety_specs_smoke_alarm` (A-d9e7a4d5d112d2-01: the wait ends at 30 s) |
| T3 | "X leaves" accepted but never fires, "X comes in" refused (medium) | fixed (deviation) | a people / pets location compared (eq / ne) with the robot's room is accepted while the robot watches its room (room camera: oracle always, realistic lit with imaging; geometric: imaging) and observed as absent when the robot can tell (room camera: watching the room; geometric: the person is tracked, `view.track`). **Deviation**: the review suggested the step view's coverage for the geometric camera and footsteps by ear; a coverage taken before a long wait cannot tell who walked out behind the camera during it, and footsteps do not tell who, so geometric absence needs tracking and hearing is not used | `test_until_and_watch_on_someone_coming_in_or_leaving` (oracle and realistic, dark-room refusal) |
| T4 | milestones / responses reached by an action stamped at its start (medium) | fixed | `Monitor.sample(env, point, at=None)`: the engine passes the action's end (start + the step's seconds; a wait: its start; the partial-commit path; an auto command: its end) and what the sample finds is stamped then (milestones, counted times, held_for starts, response triggers and met responses; windows and never / always latches keep the sample time) | `test_monitor.NeverAlwaysTests.test_the_action_sample_sees_what_the_action_did` (updated pin), `test_an_action_that_ends_after_a_deadline_misses_it` |
| T5 | `decided` "fail" from a superseded (unscored) phase (medium) | fixed | `Monitor.decided(state, skip=())` skips the entries of the phases `evaluation.decided` finds ended unscored | `test_phases.StageCFixTests.test_a_superseded_phases_failures_do_not_decide_the_episode` |
| T6 | gen-2 clock shows DST while every domain keeps standard time (medium) | fixed | DST applies only with `clock.dst: true` besides clock model 2 (`clock.dst_enabled`; wall, describe, parse_when); the generator does not set the flag, so a generation-2 house shows the time its people, clock displays and collection days keep. Moving the domains to wall time (and then setting the flag) is an adoption-round follow-up (below) | `test_clock.DstFlagTests` (incl. a gen-2 house in October: obs.time = the people's hour) |
| T7 | hidden weather bookkeeping readable through until / watch (medium) | fixed | `weather.<field>`: never `perception.WEATHER_HIDDEN` or `_` keys; with the network every perceived field; from outdoors or through a window without the network, realistic perception only the `condition` word without numeric ops | `test_the_weather_bookkeeping_is_not_observable` |
| T8 | a phase's deadline met by a milestone of an earlier phase (medium) | fixed | a phase's milestones compile as `<id>/<name>`, sampled only inside the phase, and its deadlines, response triggers and window anchors name them first; `Monitor.overlay(phase)` gives the phase's predicates (milestone counts, report anchors) its own milestones under their plain names; the same name may serve several phases | `test_a_phases_deadline_needs_its_own_milestone` |
| T9 | auto orders stall inside waits; the 500-command budget disables them for the rest of a long wait; note uncapped (medium) | fixed | an order due when a listening wait ends runs before the wait ends; the budget is per step and simulated hour (logged once per window; a paused firing resumes at its next command, order key `resume`, where before it restarted its commands); the "standing orders ran" note lists at most 10 commands ("(+N more)") | `test_an_order_that_fires_in_a_listening_wait_runs_before_it_ends`, `test_the_command_budget_is_per_hour_of_a_long_wait`, `test_the_standing_orders_note_is_capped` |
| T10 | idle verification stops after 30 h (medium) | fixed | `verify._wait_out`: at most 30 waits of max(3600, ceil(budget / 30)) s (at most a week each), for the episode and each phase; budgets up to 30 h are waited exactly as before | `test_idle_verification_waits_out_a_long_budget` (40 h) |
| T11 | a contingent phase that never starts makes the episode unwinnable (low) | fixed | phase key `"optional": true` (lint: only with a when / at_s start): not started, it is unscored and keeps nobody waiting; another scored phase not started is `phase_not_started:<id>` among the episode's failures | `test_an_optional_interruption_that_never_comes_is_unscored` |

PEOPLE (stageC_review_people.json):

| # | finding (severity) | status | fix | regression test |
|---|---|---|---|---|
| P1 | asking about "request" hands over the hidden target (high) | fixed | "request" stays a request subject (kind request, id the request record's id, key `q:<aspect>:request:<rid>`); facts and routes match the thing it stands for (`dialogue.bound_subject`, `request_target`), the answer's `about` is "request", "it" is the request again; `(which, request)` reaches `which:<category, else the target's type>`; entity routes (where:, owner:) no longer see the hidden target | `test_dialogue.StageCFixTests.test_an_answer_about_the_request_never_names_its_target`, `..._a_fact_about_the_target_still_answers...`, `..._which_about_the_request_reaches_the_requesters_which_answer` |
| P2 | phone / text / app cannot reach anyone away (high) | fixed | `dialogue.reach` and `reports.deliver`: network and a phone, wherever the person is (calls: not to a deaf person; asks: awake); `skills.ask` / `tell` never walk to an absent person | `test_phone_and_text_reach_someone_away_from_home`, `test_reports.DeliveryTests.test_phone_text_and_app_reach_someone_away_from_home` (both modes) |
| P3 | prior beliefs show a look-alike's true type (high) | fixed | `setup._belief` records the prior `level` (oracle label: the label, else the type in words); `Mind.from_config` renders each prior belief's ident / label through `realism._identify` at that level | `test_mind.PriorKnowledgeTests.test_prior_look_alikes_are_remembered_at_the_level_they_were_seen` |
| P4 | in the dark a typed person id comes back as its shape handle (high) | fixed | `realism.inbound`: while shapes share the robot's dark room every person / pet id is refused in every parameter (about, subject, options, to, predicate paths) with one wording; free-text ids become "someone" | `test_a_typed_person_id_never_comes_back_as_a_shape_in_the_dark` |
| P5 | privacy mode / a dark room blocks contacting anyone (medium) | fixed (design note) | a command over a remote channel (`channel` phone, text or app) names people as they are and its reply keeps them plain (`realism._named_plain`); voice in the dark still refuses every id alike (refusing only the people in the room would itself tell who is there) | `test_privacy_mode_still_reaches_people_elsewhere_by_phone`, the dark-room test above |
| P6 | "I'm not sure." vs "I don't know." (medium) | fixed | one default, "I don't know." (an author's `otherwise` stays) | `test_someone_who_does_not_know_answers_like_an_unmodelled_question` |
| P7 | a clarify-back only when a fact exists (medium) | fixed | for entity-directed aspects (owner, where, keep, intended, ok, why, how, status) a word naming several things is asked back unless a fact about the word itself answers -- whether or not one of the things has a fact | `test_a_word_naming_several_things_is_asked_back_whether_or_not_one_has_a_fact` |
| P8 | words match any word of a compound name (medium) | fixed | head-noun rule in `names_entity` and type-fact matching ("dryer" names a clothes dryer, not dryer sheets) | `test_words_name_things_by_their_head_noun` |
| P9 | options do not resolve a clarify-back (medium) | fixed | options narrow the things a word names: one left, the question is about it; several, the person asks back among those (per-option answers are not modelled: documented) | `test_options_narrow_a_word_naming_several_things` |
| P10 | a memory key containing a fact id as a substring (medium) | fixed | whole `:` / `.`-separated segments | `test_only_a_whole_memory_key_segment_names_a_fact` |
| P11 | "who is at the door?" answered from the visitor's hidden identity before the robot looked (medium) | fixed (PEOPLE-IO part) | `dialogue.VETTED_ROUTES`: `people.visitor` answers only about a visitor the robot has perceived (Mind); the answerer's own reliance on `_identity` is the people domain's (follow-up below) | `test_who_is_at_the_door_needs_a_visitor_the_robot_has_seen` |
| P12 | questions answered through a failed speaker / a call to a deaf person (medium) | fixed (canonical path) | voice / phone questions need `body.can_speak` (else `"said": "(no answer)"`, time spent, nothing counted, the fault detected as speech detects it); a call to a deaf person is refused ("They cannot hear a call; text them"). The legacy topic path stays byte-identical (gate rule) | `test_a_question_through_a_failed_speaker_gets_no_answer`, the away-phone test (deaf) |
| P13 | obs.request misattributes relays and shows a remote sender's room (medium) | fixed | `request_view`: a relayed request's `from` is the relayer, `claims_from` the claimed requester (dialogue.requester unchanged); `room` only for in-person / overheard / intercom; `post_request` / the runner default the room to the speaker's, and the request message's room is None for remote channels | `test_requests_obs.StageCFixTests.test_a_relayed_request_is_said_by_its_relayer`, `..._a_remote_request_does_not_say_where_its_sender_is` |
| P14 | listen drops alarm notices in privacy mode (medium) | fixed | `listen` returns the stored notices as perceived | `test_listen_returns_every_perceived_notice_in_privacy_mode` |
| P15 | privacy mode drops non-speech sound notices (medium, TIME-EVAL) | fixed | `robot_perceives` under "addressed" drops only speech not addressed to the robot | `test_timeline.AudibilityTests.test_hearing_privacy_powered_down_remote_and_visual` (updated pin), the listen test |
| P16 | the request message names the sender realism anonymised (low) | fixed | realistic `obs.messages`: a request's own message names its speaker by the request's sender decision (`realism.request_sender`) | `test_the_request_message_names_its_sender_as_the_request_does` |
| P17 | a note-channel request cannot name its note (low) | fixed | `"note"` in `spec.REQUEST_KEYS` (lint: channel note only), copied by `post_request` | `test_a_note_request_names_its_note_through_a_spec` (runner level, both modes) |
| P18 | a request's gesture is not in the first observation (low) | fixed | `post_request` fires people's `people.point` at once | `test_a_requests_gesture_is_in_the_first_observation` |
| P19 | answers expose the legacy route topic (low) | fixed | `route` / topic stay in dialogue_log only | `RouteTests` (updated pins), `test_who_is_at_the_door...` |
| P20 | clarify options name look-alikes by true labels (low) | fixed (decision, docs) | decided: options are the person's own words for their things (they know a look-alike's kind) and never map to handles, so they do not tell which handle is which; DIALOGUE.md says so, and section 7's known gap of handoff_PEOPLE-IO.md is corrected | docs |
| P21 | agent docs: inaccurate or cut sentences (low) | fixed | DIALOGUE.md (options, look-alike memory, channels, request subject), the LLM prompt (count's scope optional, channels, options), ASK_DOC (default channel), `listen`'s doc (107 characters: whole in the 120-character catalog) | docs |
| P22 | the perception audit cannot see these leaks (low) | fixed | `tools/perception_audit.py`: houses get a request with a hidden target, prior look-alikes at a glance and dark rooms with people (`enrich`); the walk asks about "request" and about people present or not, reports to people away, issues wait(until) on handles in or out of view, dark shapes and people; checks (`check_people_message`, `check_typed_people`). Re-injecting the old P1 / P3 behaviour in a scratch process, the audit reports 2 dialogue and 130 memory leaks on 6 houses x 150 steps; the fixed code reports 0 | the gate's G4 |

Spot checks that the new tests catch the old code (each fix undone by monkeypatching the pre-fix function back in a
scratch process; scratch `mutation_check.py`): P8, P10, T5, T6, T7, T10 -- every one of their tests fails with the fix
undone. The reviewers' probes (all 28) were re-run: every reported symptom is gone (e.g. probe4: the 20 s walk now
misses the 5 s deadline; p10: `about` "request" for kim / ann / sam; p14: one refusal for every typed id in the dark).

### Files edited

TIME-EVAL's: `house_sim/kernel/timeline.py`, `engine.py`, `world.py`, `monitor.py`, `phases.py`, `evaluation.py`,
`clock.py`, `camera.py`, `house_sim/tasks/spec.py`, `runner.py`, `verify.py`, `tools/sweep_bench.py` (the until bench
now runs the whole per-substep check; the calendar bench uses a DST clock), `tests/test_timeline.py`,
`test_monitor.py`, `test_phases.py`, `test_clock.py`, `docs/TASK_SPEC.md`, `EVALUATION.md`, `KERNEL_API.md`,
`EVAL_CONTRACTS.md`. PEOPLE-IO's: `house_sim/kernel/dialogue.py`, `reports.py`, `mind.py`, `realism.py`,
`perception.py`, `house_sim/systems/core.py`, `house_sim/tasks/setup.py`, `skills.py`, `house_sim/eval/llm.py`,
`tools/perception_audit.py`, `tests/test_dialogue.py`, `test_reports.py`, `test_mind.py`, `test_requests_obs.py`,
`docs/DIALOGUE.md`, `PERCEPTION.md`; both handoffs; the gate record `catalog/analysis/tier1/gate/STAGE-C-FIX/`. All LF.

### API changes (additive unless marked)

* `timeline`: `perceivable(w, rule, realism=None, env=None)` (env added; the semantics are the per-id rule),
  `observed`, `holds_perceived`, `entity_seen`, `agent_seen`, `source_perceived` (replaces the private
  `_source_perceived(state, source)`), ALARM_* constants, `alarm_state` in OBSERVABLE_FIELDS; `IdleRun.paused`;
  `start(env)` keeps `w._env_ref` (a weak reference) and the alarm snapshot.
* `camera.entity_view_now(env, eid)` (acuity memoized by the watch signature + frames + containment chain: 0 mismatches
  against `acuity` in 4,331 checks of a randomized geometric walk), `camera.agent_level_now(env, aid)`.
* `Monitor.sample(env, point, at=None)`, `decided(state, skip=())`, `overlay(phase=None)`; phase milestone entries carry
  `"phase"`. `clock.dst_enabled(state)`. Phase key `optional`; request key `note`; `perception.request_view` adds
  `claims_from`.
* `dialogue`: `bound_subject`, `request_target`, `VETTED_ROUTES`, `find_route(..., env=None)`, `ENTITY_ASPECTS`,
  `UNKNOWN`, `NO_ANSWER`; **DEVIATION** of the frozen 16.1 Subject contract: a request subject's `id` is the request
  record's id (it was the bound target), so nothing shown can carry the target. `realism.request_sender(rid, state)`,
  `REMOTE_CHANNELS`.

### Gate

Gate id **STAGE-C-FIX**, packages `TIME-EVAL+PEOPLE-IO`, previous gate `C-MERGE`, record in
`catalog/analysis/tier1/gate/STAGE-C-FIX/`, taken on the working tree after every change of this pass:

```
python tools/orchestration/tier1_gate.py record --id STAGE-C-FIX --package TIME-EVAL+PEOPLE-IO        --prev catalog/analysis/tier1/gate/C-MERGE --jobs 7 --work <scratch>/gate_work
```

Summary line: `TIER1-GATE STAGE-C-FIX [TIME-EVAL+PEOPLE-IO] vs C-MERGE: FAIL (8 pass, 4 fail, 1 skip)`. Each FAIL:

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `955c5ef9ac14` unchanged (doc strings are not in it) |
| G1 | FAIL | 55 files, 3,849 tests (normal), normal + strict; only `test_eval_splits.py` `test_gitignore_covers_private_artifacts` fails, as at HEAD and in the TIME-EVAL, PEOPLE-IO and C-MERGE gates (`.gitignore` ignores `demos/replays/`; TIME-EVAL request 2 = PEOPLE-IO request 5). The stage-C files: `test_timeline` 50, `test_dialogue` 68, `test_requests_obs` 25, `test_phases` 22, `test_clock` 22, `test_monitor` 20, `test_mind` 20, `test_reports` 27 -- all green in both runs |
| G1b | PASS | isolated domains: the 8 failing tests known since B6, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | FAIL (by policy) | own mode 241/242 specs (1,210 trials): **0 verdict changes** vs C-MERGE, 0 observation-digest changes; 5 `idle_state_hash` changes, all A-d66994a566899c-01 (below). The one failing trial is A-d9e7a4d5d112d2-01 seed 0, unchanged since TIME-EVAL's reviewed S-R1 row (the spec fix is TIME-EVAL request 1, the safety owner's file) |
| G3 | FAIL (by policy) | forced realistic 225/242: 0 verdict changes, 0 observation-digest changes, the same 5 idle digests; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit room and geometric, with the extended people checks: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.10 ms per substep (C-MERGE 3.10), 1 h wait 1.109 s (1.111), quiet day 6.33 s (6.329) |
| G8 | PASS | room golden and golden_all unchanged (not re-recorded) |
| G9 | PASS | `tools/sweep_bench.py`: notices 0.0024 ms (budget 0.03), until 0.0181 (0.02; the bench now runs the whole per-id check on a held condition), monitor_idle 0.0028 (0.05), monitor_quant 0.0321 (0.5), calendar 0.341 (20), ask 0.775 (2), report 0.103 (1), mind_update 0.148 (1) |
| G10 | FAIL | 58 changed files: every file of this pass is on the union's list; the two outside it are the review's own outputs, `catalog/analysis/tier1/stageC_review_people.json` and `stageC_review_time.json` (untracked inputs written by the review step, not by this pass) |

Geometric camera (not in sweep_bench): an until on a thing in view costs 0.027-0.029 ms per quiet substep with
`camera.entity_view_now`'s memo (0.35-0.46 ms before it, on gen-1 seeds 0-1).

**The 5 state-digest changes** (no package code covers them, so `compare` cannot accept them; listed for the main
session):

| catalog id | seed(s) | mode | old -> new | code | evidence | decision |
|---|---|---|---|---|---|---|
| A-d66994a566899c-01 | all | both | `idle_state_hash` changed; verdict unchanged (`idle_fails` true, passed) | T10 (stage-C fix) | the spec's budget is 64 h (230,400 s): the idle check used to stop after 30 waits of 3,600 s (108,000 s) -- the review's T10 defect (its scan missed this spec); it now waits 30 x 7,680 s = 230,400 s and still fails (`damage`) | accept: the verification is now complete; nothing else in the record moved |

### Known gaps left (not fixed here; each needs another owner or a decision)

* The geometric camera can tell that someone left only when it tracks them (T3 deviation above).
* `obs.weather` itself still shows numeric fields through a window in realistic perception (perception.py renders the
  record; realism does not coarsen it); until / watch no longer accept them without the network (T7), the observation
  should match (follow-up for the weather owners and realism).
* Clarify-back answers per option (P9) are not modelled: several options make the person ask back among them.
* A routed answer's value may name a visible thing's id, which realism shows as its handle (e.g. a which: answer about
  the requested thing in view): the answer's content, unchanged here.

### How to test

```
python -m unittest tests.test_timeline tests.test_monitor tests.test_phases tests.test_clock tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
HOUSE_SIM_STRICT=1 python -m unittest tests.test_timeline tests.test_monitor tests.test_phases tests.test_clock tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
python -m unittest tests.test_timeline.StageCFixTests tests.test_phases.StageCFixTests tests.test_dialogue.StageCFixTests tests.test_requests_obs.StageCFixTests
python tools/perception_audit.py 6 150 --workers 4 [--camera geometric]
python tools/sweep_bench.py
python tools/orchestration/tier1_gate.py compare C-MERGE STAGE-C-FIX --package TIME-EVAL+PEOPLE-IO
```

### Follow-ups (requests to the main session)

1. Adoption round (TIER1_INTEGRATION 6): people (schedules), electrical (clock displays), admin_inventory (collection
   days) and climate (thermostat schedules) move to wall time; then the generator sets `clock.dst: true` for gen-2
   houses (T6).
2. people: the `visitor:` answerer should answer from what the resident knows (the expected list: "I'm expecting Dana
   -- check it's her"), not from `_identity.kind`, and grant `admit` only for an identified expected guest (P11).
3. Design docs (main session's): TIER1_SWEEP 4.3 step 5 (clarify-back decided from the word; options narrow), 4.5
   (memory keys by whole segments), 6.2 / TIER1_INTEGRATION 1.3 (the alarm bridge; privacy mode keeps non-speech
   notices: already the 1.3 wording), 9 (`optional` phases, per-phase milestone namespace), 10.2 (`claims_from`,
   remote rooms) to match the code.
4. Legacy `ask` (topic path) still answers through a failed speaker (P12): byte-identical by the gate rule; change it
   when the legacy path is next opened (with its spec re-verification).
