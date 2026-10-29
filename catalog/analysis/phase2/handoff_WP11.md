# Handoff WP11 CORE SKILLS (VERIFY_HARDENING section 25; sections 10.1, 14.5, 16, 17)

Files: `house_sim/tasks/skills.py` (changed), `tests/test_skills_robustness.py` (new). Nothing else in the project was
edited (`tests/data/room_golden.json`, also in the package's grant, did not need a re-record: see "Legacy outcomes").

## What landed

1. **PlanError instead of TypeError / KeyError on a missing binding** (14.5; review H10, F11). Every core skill checks
   the parameters that name a thing, room, person or pet before it reads them (`_named`): a value that is unbound
   (None, empty, not a string -- a list, a number, the runner's MISSING) or names nothing in the house raises
   `PlanError("<skill>: <param> is unbound (None): the binding names nothing")` /
   `PlanError("<skill>: no thing or room or person 'x' in the house")`. Covered: go_room.room, approach.target,
   reveal.obj, fetch.obj, put.obj/dest, give.obj/person, ask.person, tell.person (walked-to residents only: a remote
   channel may name a contact who does not live here; None stays "no recipient"), open / close / switch / set.target,
   use.action (and use.target when it is approached), wait.seconds, goto.target, survey.rooms/places. Optional
   parameters keep their None default (tell.person, use.target / tool, drop_all.dest). The review's crash
   (`"No such entity " + None` in fetch, T-5ad61b6881821b-01 seed 502) and put's KeyError on an unknown room are gone;
   a lenient (wrong) plan carries on with its next step, as for any refused skill.
2. **`PlanError.category`** (new attribute, set only on PlanErrors skills.py raises itself): `"argument"` for a
   binding that names nothing (a spec failure, 16.3), `"route"` for a walk with no way through (a navigation failure,
   16.3). PlanErrors from a refused step carry no attribute (their category is `runner.refusals[-1][1]`).
3. **Honest no-route message**: core go_room / approach raised "No route in R that avoids stepping on sharp or fragile
   things, sleeping animals or people" even when no path existed at all (the held-out failure of
   T-9615ae9f7570b4-01 seed 502: the setup spawns the tape where furniture walls it in). That case now says
   "No route in R at all: furniture, walls or closed things cut the way off"; the caution message stays for a way
   that only trampling would open.
4. **put opens a closed destination**, whatever keeps it closed: the destination and the closed containers around it
   (reveal) are unlocked first when locked (with the key the robot holds, a thumb turn on this side... -- the env
   refuses otherwise, as reveal always did for the containers around a thing), and when opening needs a free hand and
   none is free (a two-handed load) the robot sets what it holds down beside it, opens, and picks each thing up again
   with the hand and grip it had (`_open_for_access`). New optional `close=False`: `close=True` closes, innermost
   first, and relocks what put opened once the thing is placed (leave as found: battery and must-pass variants,
   6.7 / 6.9). put now also stands up after placing when reveal knelt to peek at the destination (geometric camera).
   Default behaviour is unchanged (opened things stay open; no extra steps on a destination that was open).
5. **Careful speed on winder stairs**: phase 1 (INT) already made go_room descend winders carefully unless a free
   working hand, a usable rail and light guide it (`_unguided_winders`). WP11 closes the remaining gap against
   navigation_robot's stairs.plan: a rail the robot inspected and knows to be loose (nav.rails_checked + `_loose`)
   guides nothing (stairs.plan's light touch), so that flight is descended carefully too. An explicit `speed` stays
   the caller's (a wrong plan that walks down keeps its mistake); straight flights and climbing are unchanged; other
   stair hazards (items on the treads, wet treads, a view-blocking load, overload) are deliberately NOT avoided
   automatically -- wrong plans written with core skills rely on them (see "Deviations").
6. **`survey` skill** (17; the interface of section 25):

   ```
   survey(r, rooms=None, places=None, rows=1, strict=False) -> {"rooms": [...], "places": [...], "skipped": {id: why}}
   plan step: {"skill": "survey", "rooms": ["kitchen_1", ...], "save": "survey"}
   ```

   * The canonical survey: from where the robot is, go_room to each room of `rooms` (default: every indoor room) and
     look around in it (`look target=around`: six head frames, 5.4 s, in the geometric camera, `rows=2` adds the low
     row; one look in the room camera). The tour is canonical whatever the order given: the room it stands in first,
     then nearest by go_room's route (room hops), ties by room id.
   * `places`: the search baseline (17, critique H19): every candidate place that exists (absent ids skipped
     silently), nearest first (room hops, then cells from the robot, then id): go_room, look around on entering a room,
     reveal (open the closed containers around it), approach, open it when closed (a locked place is reported, not
     forced), inspect it (`where=inside` for a container), without stopping at the item. A place may be a room.
   * `strict=False` (the baselines): a room or place the robot cannot reach, open or look at is recorded in
     `skipped` and the tour goes on from where the robot is; `strict=True` raises the first PlanError. Either way it
     stops when the robot powered down or fell (the rest recorded with the reason). Unknown room ids in `rooms` are a
     PlanError (category argument) -- a caller bug.
   * Cost: 0.75-1.8 CPU-s per seed on generated houses (15-19 rooms, 51-78 steps, 176-399 simulated s; room and
     geometric camera, realistic), the ~1 CPU-s of 17 / 22.4.
   * How WP5 measures `survey_s_v` / `survey_steps_v`: on the seed's evaluation-mode env from the start pose,
     `runner = Runner(env, bindings, strict=True)` (a refused step then ends that room's visit at once instead of
     trying open and move after a refused unlock), `t0, s0 = env.world.s["time_s"], env.world.s["steps"]`,
     `runner.skill("survey", rooms=work_rooms + reference_rooms)`, then the differences. The search baseline: the same
     with `places=<candidate places of the place answer>` (and `rooms` empty or the work rooms).

## Interfaces (signatures)

* `@skill("survey") survey(r, rooms=None, places=None, rows=1, strict=False) -> dict` (above).
* `@skill("put") put(r, obj, dest, relation=None, spos=None, shelf=None, cell=None, orientation=None, grip=None,
  avoid=None, close=False) -> dest`.
* Unchanged signatures of every other core skill; `reveal` still returns True when it left the robot kneeling.
* `PlanError.category in ("argument", "route")` on PlanErrors raised by skills.py itself (absent otherwise).
* Internal helpers other packages may read but should not depend on: `_named(r, skill, param, value, kinds)`,
  `ROOM, THING, PERSON, PET, ANYWHERE`, `_reveal(r, obj, opened=None, free_hand=False) -> (kneeling, opened)`,
  `_open_for_access(r, eid, opened, free_hand=False)`.

## Legacy outcomes (P10; schema 2/3)

No schema 2/3 verification outcome changes. Evidence (isolated copies of the same working-tree snapshot, A = WP11's
skills.py, B = HEAD's, everything else identical; scratch `p2_WP11/treeA`, `treeB`):

* **Own mode, 243 specs x 5 seeds (1,215 trials)**: A 243/243, B 243/243, and every trial identical -- verdict,
  reference state hash, reference steps and time, idle and initial checks, every wrong plan's fails / ran / labels
  (`ab_compare.py A_own.json B_own.json`: 0 differences of any kind). Since every reference state hash is unchanged,
  the room golden's spec digests are unchanged too (`tests/data/room_golden.json` not re-recorded by WP11; its
  test passes).
* **Forced realistic and forced realistic + geometric, 243 x 5 each**: run on an instrumented copy (`treeP`, the
  final skills.py with a probe at every branch where WP11 behaves differently from HEAD: a new argument PlanError,
  the no-route-at-all message, put unlocking its destination, put setting things down for a free hand, put standing
  after a kneel, a known-loose winder rail, the new use / wait / tell refusals). Not one probe fired in either mode,
  so both runs execute exactly HEAD's paths. Forced realistic: 242/243, the one failure T-dda4e199f3f5b0-01 seed 1
  (the pets urgency judgement, allowlisted until 2026-11-30, as at P1C). Forced realistic + geometric (the D3
  preview): 241/243, T-dda4e199f3f5b0-01 seed 1 and T-ed45170be6256e-01 (every seed: "pick_up ... That is not visible
  here", phase 1's known wrist-depth case), both on HEAD's paths.
* **Held-out seeds** (3-8, 500-503, all 243 specs, own mode, HEAD's skills -- the review's held-out set): 10 specs fail
  some seed; the ones in skills.py are fixed or made honest: T-5ad61b6881821b-01 seed 502 crashed with the review's
  TypeError and now fails as a plan ("fetch: obj is unbound (None)": the setup places no basket there -- spec debt,
  request below); T-9615ae9f7570b4-01 seed 502 said "avoids stepping on sharp..." and now "No route in living_room_1
  at all" (the tape is spawned where furniture walls it in). No winder fall remains on these seeds (phase 1's
  careful pace; the 8 specs of the review's 12 falls pass on seeds 501 and 502). The other held-out failures are not
  core-skill failures: X-bc4e8d373e62c5-01 seeds 5, 6, 503 (broken glass fences the gloves: real caution),
  T-1970299fc6d849-01 seed 501 (safety's evacuate_house makes two trips and misses the 240 s deadline),
  A-d9e7a4d5d112d2-01 (idle passes on seeds 4 and 503; variant 2's raw `open` of an already open door on 3 and 500),
  T-c49fadcea69182-01 seed 500 and A-926bc611ae9158-01 seed 6 (wrong plans pass: the review's cases),
  T-df60f56a4cce54-01 seed 503 (no thermostat), T-2a55a2133257d0-01 seed 8 (geometric ceiling-stain sighting),
  A-d66994a566899c-01 seeds 500 and 503 (the 56 h textile spec).

## Phase-1 carry-over

No open phase-1 row has its fix in WP11's files. closeout_breakages.json (10 rows: people, appliances_cooking,
textiles_laundry, cleaning, pets, safety, mechanisms, electrical, manipulation, navigation_robot, food), the ten
closeout_REV.json findings (all confirmed by closeout_VER2.json, none refuted; gate tool, cleaning, people, safety,
mechanisms, navigation_robot, py39_lint, rules) and closeout_INT3.json's requests (main session, domains) name no
change in house_sim/tasks/skills.py. The phase-1 skill requests (people's _approach_cell, navigation_robot's winder
pace) were applied by INT and are pinned by tests/test_kernel_integration.py; the review's remaining skills.py items
(H10's fetch TypeError, F11's put KeyError) are closed here with regression tests that fail on HEAD.

## Deviations from the spec

* Careful speed on winders only (as specified), not on every stair hazard: generalising go_room to the stairs model's
  full verdict (items on the treads, wet treads, a load hiding the treads, overload, a hidden loose rail) would make
  wrong plans written with core skills ("walks down with the basket", "goes down the wet stairs") pass, i.e. weaken
  existing specs; those hazards are the spec author's to stage and the reference's to handle (navigation_robot's
  carry_stairs and friends).
* Added beyond section 25's list (optional, backwards compatible): `put(close=)`, survey's `places` (the search
  baseline of 17), `PlanError.category`, and the honest no-route message.
* put does not fetch a key it does not hold (a locked destination without the held key stays a PlanError, as reveal
  behaves for the containers around a thing): fetching keys from elsewhere could recurse into other locked places.

## Known gaps

* A malformed skill call (a missing or misspelt argument: `{"skill": "fetch", "object": ...}`) still raises Python's
  TypeError from the call itself -- the runner (WP5) should check step arguments against the skill's signature and
  raise SpecError, as `_check_plan_actions` does for actions (request filed).
* Domain skills (house_sim/systems/*/skills.py) have their own None paths; only the core skills are covered.
* go_room still returns normally when the robot fell on stairs going down (it lies in the destination room); the next
  action is refused. Unchanged (wrong plans' refusal counts depend on it).

## Requests to other packages

* WP5 (runner.py): check a skill step's arguments against the skill's signature (`inspect.signature(SKILLS[name])
  .bind(runner, **args)`) and raise SpecError for a missing or unknown argument -- today a malformed step raises
  Python's TypeError from the call, which 16.3 would charge to skills.py. Read `PlanError.category` ("argument" /
  "route") when classifying held-out failures (16.3); measure `survey_s_v` / `survey_steps_v` and the search baseline
  with the survey skill as described above (strict Runner, survey strict=False).
* WP6: `put(..., close=True)` leaves a container as found (M2 "put it at its home", B3 placements where the ledger
  would otherwise charge an opened cupboard or fridge); survey's `places` is the search-baseline walk if the battery
  needs one.
* WP12 navigation_robot (or its WP-D): T-5ad61b6881821b-01 held-out seed 502 -- `navigation_robot.dock_setup` with
  `block: basket` binds `{blocker}` to None (no basket placed); the reference's `fetch {blocker}` now fails as a plan.
  The setup should always place the blocker (or the reference skip it when there is none).
* WP9 / WP12 navigation_robot: T-9615ae9f7570b4-01 held-out seed 502 -- the setup's `spawn tape_measure floor: {room}`
  puts the tape at a cell no free cell reaches (cell [3, 0] of living_room_1); the reference fails "No route ... at
  all". A spawn should land on a floor cell the robot can reach.
* WP-D safety: held-out X-bc4e8d373e62c5-01 seeds 5, 6, 503 (the glass shards fence the robot from the cut gloves:
  clean_glass gives up rather than tread on glass), T-1970299fc6d849-01 seed 501 (evacuate_house's two trips miss the
  240 s deadline); A-d9e7a4d5d112d2-01 variant 2's raw `open exterior_door_1` on seeds 3 and 500 (already open).

## Checks run at hand-over (live shared tree, 2026-10-29)

* `tests/test_skills_robustness.py`: 28/28 normal, `HOUSE_SIM_STRICT=1` and `HOUSE_SIM_DOMAINS=core` (5 winder tests
  skip there: the handrail is navigation_robot's); `tools/py39_lint.py` clean for both files; files LF.
* Full unit suite `python tools/orchestration/green_tests.py 3`: 5,233 tests, 0 failures, normal and strict (exit 0;
  other packages' work in progress included).
* All 243 authored specs, own mode, one combined file, `--check --workers 3`: 243 passed, 0 failed.
* `tier1_gate.py scope --package P2-WP11 --shared-tree`: every changed file is P2-WP11's or a concurrent package's,
  except `catalog/analysis/phase1/closeout_VER2.json` (VER2's, not this package's).

## How to test

```
python -m unittest tests.test_skills_robustness                      # 28 tests, ~5 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_skills_robustness
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_skills_robustness   # core types only; the winder tests skip
python -m unittest tests.test_kernel_integration -k winders           # phase 1's winder pin, unchanged
```

Every BINDING, PUT (unlock, free hand, close), WINDERS (loose rail) and SURVEY test fails on the tree before WP11
(checked in an isolated copy with HEAD's skills.py: 18 failures, 82 errors).
