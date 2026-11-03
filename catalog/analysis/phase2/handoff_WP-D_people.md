# Handoff WP-D people (VERIFY_HARDENING section 25 row "WP-D <domain>", 23.3; phase 2 run B)

Files (only these): `house_sim/systems/people/{calendar_model, care, children, comms, door, harms, helpers, knowledge,
memory, metrics, model, reach, routine, senses}.py`, `house_sim/data/verify/watch/people.json` (new),
`house_sim/data/speech/truth_people.json` (new), `house_sim/data/vocab/people.json` (new), `docs/domains/people.md`
(section 14 with the "Latched fields" table, header line), `tests/test_people_hardening.py` (new, 25 tests), this
handoff and `WP-D_people.json`. `house_sim/data/types/people.json` and `tests/test_people.py` are unchanged.

## What landed

1. **Field classes for every people type property** (6.3, L-WATCH part 2): `watch/people.json` classifies 102 field
   names on the people types (`fields`) and refines 7 types (`types`): intrinsic properties identity; `folded`,
   `height_setting_cm`, `child_resistant_closed`, `bookmarked`, `open_face_down`, `remote_status` setting;
   `scratched`, `page_lost`, `wax_blocked`, `burned`, `floor_hazard` condition (true worse), `smudge` condition (up,
   tol 0.05); `doses_left` content; a foldable aid's `size_m` and a wheelchair's `seat` process (they follow
   `settings.folded`); `in_use_by`, `offered`, `hidden_by`, `held_grip`, `unfolded_size_m`, `doc_hint` bookkeeping;
   overrides of kernel name classes that misfit people types (`folded` is no laundry condition here, `wheel_count` /
   `battery_kind` / `battery_powered` / `accuracy_c` / `read_time_s` / `nozzle_temp_c` / `hazards` are identity, a
   place card's / storybook's / library book's `text` identity). 21 water-sensitive types. `tools/field_census.py
   --unclassified --domain people` lists none (186 rows before); `--check` of the census stays 0.
2. **Writer classes and causes** (6.2): every people system tagged (`@system(..., writer=)`): behavior, routine,
   wandering, visitors, senses, children, watch volition; care, calendar, reach, room_metrics physical. people.care's
   heating-pad and hearing-aid writes cite the device (`w.mut(eid, cause=eid)`), its people-state writes (bath, bedtime
   settling, a person without their powered device) are volition; the calendar render cites the artifact. Inside robot
   actions the people's own acts are volition: the patient's dose from a handed-over bottle (`doses_left`,
   `med_doses`, writer `people.patient`), the `offer:` answer and `step_aside` (writer `user_simulator`).
3. **Truth mappings** (13.3): `truth_people.json` states `home` / `away` (person-only through `role`), issue `fever`
   (`_body_temp_c` >= 38.0), act `let_in` (fields `admitted`, lenient); `vocab/people.json` adds the issue word `fever`
   (person only). Shared person/pet words (sick, injured, distressed) are left to the kernel's fallback (no collision
   with pets / outdoor_plants); safety maps fallen / unresponsive. `speech.truth_tables()` loads with every domain's
   file present (checked with cleaning, manipulation, navigation_robot, safety).
4. **Claim relays and attribution claims** (13.4): `comms._deliver_claims` for messages with `key_claims` (the
   `people.message` helper takes and validates them): `complete` = every key claim asserted by the heard words and none
   contradicted; `attributed` only by an attribution claim naming the sender (`sender=` ignored);
   `received.<key>` gains `claims_heard`, `claims_missing`, `contradicted_claims`, `contradicted`. Legacy `key_facts`
   unchanged.
5. **Structured instrument readings** (15.2): `take_temperature` returns `reading` {subject, quantity temperature,
   value, unit C (, field)}; `perceive.index(env).readings` picks it up.
6. **Settings skills** (10.3): `settings.dim` of the smart bulb lamp (a range); every other people setting is a
   kernel-default option list (tested).
7. **World-behaviour fixes** (23.3, P10): attendance only by the robot when asked to watch (`needs_watch`;
   `watch_by: "anyone"` keeps the old rule); a mobility aid's `floor_hazard` cleared when it is picked up or put on
   furniture (a latch); the latched `oriented_to` listed in people.md's "Latched fields" table (L-LATCH).
8. **Seeded placement** (WP9's request): `people.medicine_cabinet` (the look-alike bottles), `people.glasses_request`
   (which nightstand is whose) and `people.ownership_pile` (the garments) place in `ctx.placer.order` -- a seeded
   order in schema 4, the given order otherwise (no draw, legacy builds identical).
9. **Phase-1 carry-over**, each with a regression test that fails on HEAD (25 new tests: 23 fail on a `git archive
   HEAD` copy, the other 2 are guards -- legacy key_facts and the helper's validation):
   * closeout_breakages row 1 (read_aloud of unfamiliar things): `_printed_fields` reads an unfamiliar thing's print
     alone (`core.NOVEL_PRINT` + `printed_value`), no story time or bedtime credit for an unfamiliar storybook.
   * row 2 and the people part of closeout_REV 6 (read_aloud before read tells spray-bottle look-alikes apart): a blank
     label reads as core read answers it (ok, `blank_label`, read granted); `perception_audit.py 0 0
     --read-aloud-first`: 162 probes, 0 leaks (WP7 reported 2 before).
   * row 5 (pill-bottle pour by member): the people side is one rule for every member -- every medicine look-alike
     named by `pour`, `empty_into`, `fill` or `refill` gets the open guard's refusal (safety) before the verb's checks.
   * row 9 (two copies of the known-record rule): `knowledge._known_record` calls `dialogue.known_record`.
   * Every people verb checked over every registry look-alike group (78 groups, 28 commands each: every entity
     parameter of every people verb, on the table and held) and 64 unfamiliar twins (scratch `probe_people.py`,
     closeout_REV's probe_six with the people verbs and a person argument, room camera). On HEAD: read_aloud split 6
     groups (spray_bottle, plastic_jug, laundry_jug, white_powder_jar, adhesive_hook, robot_vacuum: ok vs refused), and
     the unfamiliar twins were split by read_aloud (story time, true print), knock, answer_door, log_medication and
     take_temperature (tool and target). After the fixes: no ok / category split in any group or twin set; what
     remains is read_aloud's duration after it read the label (words / 2.5 s: the label the robot now knows, as core
     read returns the text) and, for unfamiliar twins, read_aloud succeeding exactly where core read does (printed
     text: pinned by a test) and the kernel's pick_up of fixtures (not a people verb). New identify-first refusals:
     `knock`, `answer_door`, `let_in`'s door, `log_medication` (log and medication), `take_temperature` (tool and
     measured thing; a held unfamiliar thermometer is passed over), `sign_for`'s named package. The geometric-camera
     sweep is not usable (the probe's prep step already differs between twins there: "not visible" for all but the
     first twin of each group, a probe artifact before any people verb runs); the geometric camera is covered by the
     tests (read_aloud of blank labels, pour).

## Interfaces

```python
# house_sim/systems/people/comms.py
heard_claims(state, text, pid) -> [claim]                 # speech.parse with the robot's room as scope
deliver_content(w, pid, text, channel, sender=None, key=None)   # key_claims messages via _deliver_claims
# user_simulator.messages.<key>: {"from", "to", "text", "key_facts", "urgent", "key_claims": [pattern]}
# people.<pid>.received.<key> (key_claims): {"complete", "attributed", "claims_heard", "claims_missing",
#                                            "contradicted_claims", "contradicted", "channel", "time_s", ...}
# house_sim/systems/people/helpers.py
@scenario_helper("people.message") message(ctx, key, sender, to, text, key_facts=(), urgent=False, key_claims=None)
# house_sim/systems/people/care.py
reading_record(subject, value, field=None) -> {"subject", "quantity": "temperature", "value", "unit": "C"(, "field")}
MED_REFUSAL; MED_POUR_PARAMS = {"pour": (target, destination), "empty_into": (...), "fill": (target, source), "refill": (...)}
# take_temperature result: {..., "reading": reading_record(...)}; read_aloud of a blank label: {"read", "blank_label", "seconds"}
# children: people.<pid>.watch_by = "anyone" lets an adult in the room count as watching a needs_watch child
# harms.py: @settings_skill("settings.dim", types=("smart_bulb_lamp",))
```

## Deviations from the spec

* **No `@derived_field`.** The derived-field overlay covers entities only, so people-record latches cannot be
  recomputed: `oriented_to` is listed as latched (L-LATCH makes it a schema-4 error), and the one entity latch found
  (`floor_hazard` of mobility aids) is fixed in the world (cleared when the aid leaves the floor) rather than
  registered -- a derived field is registered globally by name, and `floor_hazard` is written by other domains too (a
  second registration would fail at import).
* **No mess kinds, no `hazard_created` types.** No people verb or system creates or destroys an entity or moves a
  registered mess kind (the pocket spill moves entities: placement guards judge it).
* **Zones and passability**: the ledger's reachability is WP1's (`agents.json`, `ledger.passable`); people records
  already carry what it reads. Two mismatches with the people model are requests (below), not changes in my files:
  people walk through any door (`tasks.skills.room_route`: a locked door costs more but is passed; residents hold keys
  and inside locks open by hand), while the ledger lets only authority-2 adults pass a locked door.
* **Relay attribution** follows 13.4 for `key_claims` messages only; legacy `key_facts` messages keep `sender=` (P10:
  evaluation semantics are schema-gated; no legacy message has key_claims).
* **Watch attendance** applies to every schema (a world-behaviour fix): no authored spec reads `watch_s` /
  `unwatched_s`.
* `sick`, `injured`, `distressed` (core words for persons and pets) are not mapped here (a key may live in one truth
  file only; their pet readings are the pets domain's call).

## Known gaps

* A person's body temperature claim ("Riley's temperature is 38.6") is unscored: `quantities.temperature` is the
  kernel's key in truth.json and does not read `_body_temp_c` (request to WP4).
* Fields the people verbs write on other domains' types (`in_use_by`, `offered`, `hidden_by`, `page_lost`,
  `open_face_down`, `bookmarked`, garments' `laced` / `on_feet`) are classified only on people types; elsewhere they
  default to `setting` (watched: a possible false alarm, never a hole). `offered` and `in_use_by` are written as
  volition, so they are never charged.
* `oriented_to` stays a latch in the world (the people model does not clear it when a person turns away).
* The probe covers people verbs with their required and optional entity parameters; people's hooks on other domains'
  verbs (place, open, pick_up, hand_over, set, read, lock, switch_on, wipe) are covered only by the existing tests and
  the medicine guards.

## Requests

* WP1 (ledger.passable / agents.json): a locked exterior door is passable from inside for any person who can work its
  lock (deadbolts open by hand from inside; `lock_kind` "key" on generated front doors is the outside lock), and from
  outside for a resident carrying its key; today only authority-2 adults pass, so locking up confines an elderly
  resident (authority 1) or a school-age child (`house.confined`). Count `abilities.cognition` in (mild_dementia,
  dementia) as "may wander" (the people domain's rule) beside the wandering flags.
* WP1 (watch_fields.json): kernel classes for fields people verbs write on any type: `in_use_by`, `offered`,
  `hidden_by`, `held_grip` bookkeeping; `page_lost`, `floor_hazard` condition (true worse); `open_face_down`,
  `bookmarked`, `laced`, `on_feet` setting.
* WP4 (truth.json): add `_body_temp_c` to `quantities.temperature` so a reported body temperature is scored.
* WP-D pets and WP-D safety: `sealed: true` on `pet_medication` and `expired_medication` (closeout_breakages row 5's
  data half: one rule for every capped amber bottle; people's `medication_container` already has it). The people
  guard makes pour / empty_into / fill / refill alike whatever the data says; other verbs that read `sealed` may
  still split the group.
* WP10 / main session (G4): `tools/perception_audit.py --read-aloud-first` can join the gate (0 leaks now).
* WP12 people: migrate `P-47196e0991cdf4-01` (the review's inverted relay) to `key_claims` through `people.message`;
  specs reading `oriented_to` need the person's pose instead.
* Main session: regenerate docs/DIALOGUE.md's vocabulary tables (`python tools/vocab.py --markdown`) after merging
  the WP-D vocab files: `vocab/people.json` (issue `fever`) changes the generated tables and their source list, so
  `tests.test_dialogue.VocabularyTests.test_the_docs_tables_are_generated_from_the_data` fails until then (other
  WP-D vocab edits do the same).
* Main session (kernel/dialogue.py, WP2's request): user-simulator answers inside `ask` as volition generally (people's
  `offer:` and `step_aside` already write as volition).

## Legacy outcomes (P10)

No legacy spec's outcome changes (outcome_changes: none). The world-behaviour fixes touch no authored spec: none reads
aloud, watches a child (`needs_watch` / `supervise_kids`), places a mobility aid, pours / empties / fills / refills a
medicine container or holds an unfamiliar thing; placement order is unchanged below schema 4; writer classes live
outside world state. Re-verified: the 15 people specs (`--check`, own mode, seeds 0 1 2 41 97) pass, and the full
catalog run (see Verification).

## Verification

* `tests/test_people_hardening.py` (25 tests): OK normal, `HOUSE_SIM_STRICT=1` and `HOUSE_SIM_DOMAINS=core,people` (one
  skip there: the pour test needs plumbing's verb and another domain's pill bottle). On a `git archive HEAD` copy 23 of
  the 25 fail (the 2 others are guards).
* `tests/test_people.py` (190 tests): OK normal, strict and `HOUSE_SIM_DOMAINS=core,people`.
* All 243 authored specs, own mode, one combined file (`python -m house_sim --check <all> --workers 3 --no-cache`):
  243 passed, 0 failed; the 15 people specs also file by file.
* `tools/perception_audit.py 0 0 --read-aloud-first`: 162 probes, 0 leaks. `tools/field_census.py --unclassified
  --domain people`: none; `--check catalog/analysis/phase2/field_census.json`: 0. `tools/vocab.py --check`: 0
  problems. `tools/py39_lint.py` on the people files and the new test: 0 violations.
* Full suite (`tools/orchestration/green_tests.py 3`, the shared working tree with other WP-D packages mid-edit):
  172 runs; test_people and test_people_hardening OK in both modes. Five files fail, none from the people
  changes except the generated-docs check: `test_dialogue` (docs/DIALOGUE.md's vocabulary tables must be
  regenerated after the WP-D vocab files land, `vocab/people.json` among them: request), and `test_ledger`
  (PlacementTests.test_waste_at_the_baseline_may_go; MessTests errors only in the full file order),
  `test_lint` (ReviewCaseTests, the cleaning spec T-d389e2979f6aad-01), `test_room_golden` (T-089c40fb507289-01,
  T-48266979ae98d3-01, X-bf82170244be22-01: cleaning, navigation_robot, safety) and `test_verify_hardening`
  (HeldoutTests private stage) -- each reproduced in a scratch copy of the same tree with HEAD's people files and
  without the three new people data files, so they belong to the concurrent packages.

## How to test

```
python -m unittest tests.test_people_hardening tests.test_people          # also HOUSE_SIM_STRICT=1, HOUSE_SIM_DOMAINS=core,people
python tools/field_census.py --unclassified --domain people --json         # {"people": []}
python tools/perception_audit.py 0 0 --read-aloud-first                    # 0 leaks
python -m house_sim --check tasks/authored/people__examples.json --workers 3
python -m house_sim --check tasks/authored/people__perception.json --workers 3
python tools/py39_lint.py house_sim/systems/people tests/test_people_hardening.py
```
