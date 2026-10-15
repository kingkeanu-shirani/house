# Handoff BODY-CORE (stage B5, with NAV-M5): the robot body in the core verbs

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.1-1.10, 1.13, 1.15 and conflicts 1-3, 5-8,
18-20, 30; the BODY-CORE and NAV-M5 rows of 2.4; 3; 5), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_EMBODIMENT.md`
(whole; this package is M3, the core parts of M4, the setup ops and M5: KB-3 and the M3 / M5 parts of KB-2, test plan
items 16 and 19-24). Handoff files read (all that existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`,
`handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`,
`handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md` and
`handoff_CAM-RENDER.md` (with their B3 merge sections), `handoff_BODY-ENGINE.md` and `handoff_NAV-M2.md` (with their B4
merge sections). Their follow-ups addressed to BODY-CORE / NAV-M5 are acted on: BODY-ENGINE 9 (the held-load safety net
switched on with team carries excluded and `uninstall` / `receive` lifting through `check_lift`; `stairs=True` when
`move` crosses stairs; the interim `face` / `wrist_peek` guards replaced; the careful x1.5 moved to own seconds;
`body_audit --against gate/B0`), BODY-MODEL 9 (`pick_up` per 1.5), CAM-ACTIONS 9 (the camera verbs' guards and the
camera-off checks through `ctx.require_imaging`, `face` under the own-seconds rule), NAV-M2 5 (`step` at the profile's
speeds with `timing="speed"`), B0 9 (the 14 careful files re-checked; the five picks with the least margin), PHYS-DATA 7
(`LiftPlanMatchesCorePickUpTests` stays green). BODY-FAULTS ran in parallel on disjoint files.

## 1. Summary

* `pick_up`, `receive` and `uninstall` lift per TIER1_INTEGRATION 1.5 (`ctx.lift_plan` -> `ctx.require_grasp` ->
  `ctx.check_lift`): derated capacity, total carry, counted and felt overloads, latent weak arms found, grasp size
  classes (auto pinch, refused power grips, too small, thin items slid to the edge); the held-load safety net is on
  (`body.HOLD_SAFETY_NET`), with a co-carried end and a towed cart counting only the robot's share (`body.load_share`).
* `look` / `inspect` / `read` / `peek` need imaging (both camera modes), `look` relocalizes in a lit room, `listen` / `ask`
  need hearing (privacy mode: addressed speech), `say()` stores `audible` (a failed speaker is inaudible and detected);
  `set_speed fast` refused while quiet or limp; stairs need a climbing body; `face` / `peek` use the body's guards.
* M5: the careful factor applies to a leaf's own seconds only and skips `timing="speed"` (core `move` / `approach` /
  `walk_to`, NAV-M5's `step` / `go_to` / `follow` / `lead` / `patrol`); setup ops `robot` (profile, overrides, sensors) and
  `robot_fault`; skills stop with `PlanError("robot powered down")`.
* 35 tests in `tests/test_body_core.py` (normal, strict, core-only); docs `ACTION_API.md` ("Robot body"),
  `AUTHORING_GUIDE.md` ("Robot body in specs"). Gate: 0 verdict changes in both modes, `body_audit` 0 landing hits.

## 2. Files

Created: `tests/test_body_core.py`, this handoff, the gate record `catalog/analysis/tier1/gate/BODY-CORE/`.
Edited: `house_sim/systems/core.py`, `house_sim/kernel/engine.py` (the careful factor only), `house_sim/kernel/body.py`,
`house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`, `docs/AUTHORING_GUIDE.md`, `docs/ACTION_API.md`; NAV-M5:
`house_sim/systems/navigation_robot/paths.py`, `house_sim/systems/navigation_robot/follow.py`.
All on the BODY-CORE / NAV-M5 lists of TIER1_INTEGRATION 2.4. Not changed although listed: `house_sim/data/robot/profiles.json`
(no fix was needed), `tests/test_kernel.py` (green unchanged), `tasks/authored/*.json` (`body_audit` found no reference
pick to fix and no verdict changed, so no spec was edited). All files LF.

## 3. APIs

"ADDITION" = not in TIER1_EMBODIMENT 5 / TIER1_INTEGRATION 1; **DEVIATION** = differs from them (section 4).

**`house_sim/systems/core.py`**

```
pick_up(ctx)        lift_plan(hands="both" for grip=two_hand, else hand) -> take_hold -> check_handling; held_grip = the
                    grip used (the command's, or the grasp planner's "pinch" / "edges")
receive(ctx)        ctx.require_arm(); the free hand; take_hold (both hands for a large or heavy item); the second hand
                    closes in the after-hook _second_hand (order 990)           DEVIATION 4
uninstall(ctx)      free_hand(prefer) as before; take_hold (a heavy part comes out into both hands)
take_hold(ctx, eid, plan, no_hand_text, grip=None) -> (hands, grip)          ADDITION: the shared lift / grasp / check_lift
                    step (TIER1_INTEGRATION 1.5) with today's hand texts
look(ctx)           ctx.require_imaging() (both camera modes, conflict 20), then _relocalize (body.relocalize in a lit room;
                    a note in the dark)
peek / wrist_peek   ctx.require_arm() + ctx.require_imaging() (conflict 19)
inspect, read       ctx.require_imaging() first (both camera modes)
face(ctx)           body.mobile_problem(state, CANNOT_MOVE, localized=False)  DEVIATION 3
move(ctx)           timing="speed"; ctx.require_mobile(MOVE_BLOCKED, stairs=True) for a stairs connection
approach, walk_to   timing="speed"
listen(ctx)         ctx.require_hearing(); "addressed": only addressed_to_robot(m) messages
ask(ctx)            ctx.require_hearing(addressed_ok=True) after the topic check
say(ctx, ...)       entry["audible"] = body.can_speak(state); inaudible: audience [] and a latent speaker_failed detected
                    ("speech") when the microphones work
set_speed(ctx)      body.speed_problem -> BodyRefusal (precondition)
set_quiet(ctx)      a note when fast walking turns to normal
addressed_to_robot(message) -> bool                                        ADDITION (to == "robot" or addressed_robot)
_travel_seconds(ctx, meters)   body.effective_speed + body.speed_mps (the robot's own profile)
SPEEDS              the default profile's speeds (compatibility; nobody reads it)
```

**`house_sim/kernel/body.py`** (changes only)

```
HOLD_SAFETY_NET = True                          the TIER1-STUB(BODY-CORE) is gone
TOWED_SHARE = 0.15; CAREFUL_FACTOR = 1.5        ADDITIONS
load_share(state, eid) -> float                 ADDITION: 1, 1 / len(co_held_by) for a co-carried end, TOWED_SHARE for a
                                                towed cart; held_loads / carried_kg / refresh_held / check_lift / the safety
                                                net use the share (DEVIATION 5)
mobile_problem(state, legacy_message=None, stairs=False, localized=True)     ADDITION: localized
speed_problem(state, mode) -> (message, "precondition") | None               ADDITION (set_speed)
relocalize(w, how="look") -> bool               ADDITION: clears localization_lost (else sets localized), body_log
                                                relocalized_s
careful_seconds(state, spec, seconds, nested_s=0.0) -> seconds               ADDITION: the M5 rule (engine)
check_policy(policy)                            public (was _check_policy; set_sensors and the setup robot op)
QUIET_FAST_TEXT, LIMP_FAST_TEXT, LEGS_FAST_TEXT, NO_STAIRS_TEXT, WEAK_LEGS_STAIRS_TEXT     ADDITIONS (texts)
```

**`house_sim/kernel/engine.py`**: `step` applies `body.careful_seconds(state, spec, seconds, nested)` (nested = `ctx.nested_s`,
whole seconds in realistic perception) instead of `seconds *= 1.5`; `perform` applies it to the leaf's own seconds
(**DEVIATION 1**). Nothing else changed.

**`house_sim/tasks/setup.py`**

```
robot        keys op, room, near, cell, profile, profile_overrides, sensors, props, hold (others: SpecError); order
             profile -> profile_overrides (validated through body.profile) -> sensors {camera_on, mic_on, privacy_policy}
             -> props; hold on a failed arm is a SpecError                   ROBOT_OP_KEYS, ROBOT_SENSOR_KEYS
robot_fault  {kind, part, severity 0-1, latent, at_s, until_s} or {choose: [...], var}; at reset into
             config.robot.fault_state (normalize applies it; water_ingress brings camera_failed and actuator_failed), at
             at_s the event robot.fault, until_s the event robot.clear_fault (network_down: the record's until_s); var binds
             the option and var + "_id" the fault id                        ROBOT_FAULT_KEYS, FAULT_IMPLIES
```

**`house_sim/tasks/skills.py`**: `POWERED_DOWN = "robot powered down"`, `_powered(r)`; `go_room` checks before each hop
and after each `move`, `approach` after its `approach`.

**NAV-M5** (`paths.py`, `follow.py`): `timing="speed"` on `step`, `go_to`, `patrol`, `follow`, `lead`; `step` walks at
`body.speed_mps(state, pace)` (the profile's speeds; humanoid_v1 equals `common.SPEED_MPS`).

## 4. Deviations from the spec

Each is the more realistic or the simpler option.

1. **The careful factor applies to every leaf, nested ones included** (TIER1_EMBODIMENT 4.9 says "only the top-level
   action's own seconds"; TIER1_INTEGRATION 1.9 places it in `step` only). `perform` multiplies a nested physical leaf's
   own seconds too, and the parent adds the result as nested time, never multiplying it again. Otherwise a careful
   `pick_up` would be slow at the top level and fast inside a domain chain (or an `undo`), and chains whose parent is
   physical would lose the slowdown of their non-walking legs that they have today. "Never multiplied twice" holds.
2. **The careful trigger stays the user's setting** (`robot.speed_mode == "careful"`, as today), not the effective speed
   nor a per-action `speed` argument: limp mode slows the walk (effective speed), not the arms; a verb whose duration
   comes from its own speed argument declares `timing="speed"`.
3. **`face` uses the body's mobility guard without the localization check** (`body.mobile_problem(..., localized=False)`
   instead of `ctx.require_mobile`, conflict 19): a robot that lost its pose turns in place to look around and find it
   again; everything else (power, actuators, fallen, failed legs, overheating pack, "Step down first") is the motion
   verbs'. Same reasoning as BODY-ENGINE's deviation 2 for the posture verbs.
4. **`receive` takes the item with the free hand and closes the second hand in a late after-hook** (`_second_hand`, order
   990) when the lift needs both. manipulation's own `_after_receive` (order 40) moves an item heavier than 8 kg into the
   other hand when it is free and drops it otherwise ("unexpectedly heavy"); with the item already in both hands it
   would drop every two-hand hand-over. The capacity is checked for both hands before anything moves; when the other hand
   is busy a two-hand item is refused at lift-off ("Both hands must be free for this item"), like `pick_up`. `receive`
   also gains `ctx.require_arm()` (it had no body guard at all).
5. **Load shares** (`body.load_share`), needed before the safety net could go on: the robot's end of a co-carried load
   (`co_held_by` lists the robot and its partners) counts an equal share, a towed cart (`towed`, admin_inventory's
   `wheel_cart`, up to 120 kg on its wheels) 15 % (the handle load of a tipped-back two-wheeled cart, an assumption).
   They apply to the hand loads, the hold power, the walking load and the total carry.
6. **`say()` stores `audible` on every speech entry** (True or False) and detects a latent `speaker_failed` when the
   speech is inaudible and the microphones work ("no echo heard"); the `robot_spoke` hooks still run (listeners ignore
   inaudible speech in the adoption round, TIER1_INTEGRATION 6 people / navigation_robot).
7. **The grasp planner's grip is stored** in `held_grip` (`pinch`, `edges`), but it does not satisfy a hazard's
   `safe_grips` in `check_handling` (that guard reads the command's grip on purpose: realistic look-alike rules depend on
   what the robot chose). With manipulation loaded its pick_up after-hook overwrites `held_grip` with its own default.
8. **Setup op validation**: the robot op refuses unknown keys (no authored spec uses others), unknown profiles,
   overrides that leave the profile incomplete and bad sensor values; `robot_fault` refuses unknown kinds, a sided fault
   without left / right, `latent` on a fault the robot notices at once, severities outside 0-1, `until_s` before `at_s`
   and `arm_failed` on a hand that holds something (in either op order). Reset-time faults get the consequences of their
   onset only for water_ingress (the other onsets act on held items or other faults that cannot exist at reset).
9. **`look` relocalizes with any look** (forward, around, at a thing) in a lit room, in both camera modes; in the dark it
   succeeds with a note and does not.
10. **`set_speed` texts**: quiet "Quiet mode is on: fast walking is too loud; turn quiet mode off first", weak legs "Weak
    legs: fast walking is not safe now", battery "Battery critical: fast walking is not available; charge first"
    (precondition). `set_quiet on` with fast keeps `speed_mode` fast (the user's setting) and notes the slower walk.
11. **NAV-M5 `step`** takes its duration from the body profile's speed at the effective pace (NAV-M2's suggestion), so a
    mobile manipulator steps at its own 0.6 m/s.
12. **`PlanError("robot powered down")`** is raised by `go_room` (before each hop and after each move) and `approach`;
    the other skills reach it through them or stop at the next refused step as before.

## 5. Stubs and contracts

* Removed: `TIER1-STUB(BODY-CORE)` on `body.HOLD_SAFETY_NET`. `grep -rn "TIER1-STUB" house_sim --include=*.py` lists only
  stage C's markers (TIME-EVAL, PEOPLE-IO).
* Consumed: BODY-MODEL / BODY-ENGINE's body (`check_lift`, `grasp_problem`, `GRASP_AUTO`, `capacity_kg`, the problem
  functions, `effective_speed`, `can_speak`, `can_hear`, `is_latent`, `settle_energy`'s
  hold check), the Ctx helpers of BODY-ENGINE (`require_imaging`, `require_hearing`, `require_arm`, `require_grasp`,
  `check_lift`, `require_mobile`), PHYS-INT's `ctx.lift_plan` / `forces.lift_plan` (reasons and hand lists), CAM-RUNTIME /
  CAM-ACTIONS' camera verbs.
* For BODY-FAULTS (parallel, merges second): the golden diff classes of section 6 (B-R11); `say()`'s `audible` and the
  hearing rules here are the action side of its `obs.messages` filter.

## 6. Gate results

Gate id **BODY-CORE**, packages **BODY-CORE+NAV-M5**, previous gate **B4-MERGE**, record in
`catalog/analysis/tier1/gate/BODY-CORE/`. Taken on an **isolated copy** so that G10 and every check see only this
package: the base commit is the shared tree as B4 handed it over (snapshot taken before this package's first edit and
before BODY-FAULTS touched anything: NAV-M2's b3tree base plus the B4 tree, committed in a throwaway repository in my
scratch folder), with BODY-CORE's and NAV-M5's files on top. BODY-FAULTS edited `perception.py`, `realism.py`,
`body_faults.py`, `proprio.py`, its tests and tools on the shared tree in parallel; none of those entered the gated copy.
After the run every BODY-CORE / NAV-M5 file of the project was compared with the gated copy: identical (except this
handoff). `tests/test_body_core.py` (35 tests) and BODY-FAULTS' current `tests/test_body_faults.py` /
`tests/test_body_perception.py` also pass on the shared tree with both packages' work in it.

```
python tools/orchestration/tier1_gate.py --root <scratch>/iso --gates-root catalog/analysis/tier1/gate record
       --id BODY-CORE --package BODY-CORE+NAV-M5 --prev catalog/analysis/tier1/gate/B4-MERGE
       --out catalog/analysis/tier1/gate/BODY-CORE --work <scratch>/gate_work --jobs 7
(then the same with --only G7 on a quiet machine; the first G7, taken while other sessions loaded the machine, read
 3.14 ms / 1.121 s, +4.3 % / +3.5 %, also within budget)
```

Summary line: `TIER1-GATE BODY-CORE [BODY-CORE+NAV-M5] vs B4-MERGE: FAIL (9 pass, 3 fail, 1 skip)`. `compare` passes; the
three FAILs are the golden this package may not record (B-R11) and tests outside both packages' files (section 9):

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 482f9adf9717 unchanged (425 actions, 111 systems; ActionSpec `timing` is not in it) |
| G1 | FAIL | 43 files, 3,509 tests (normal); `test_body_core.py` 35 green normal and strict. 14 failing runs (7 files x normal + strict): new and expected `test_camera_actions.py` `LookTests.test_cameras_off_refuse_look_in_geometric_mode_only` (B-R8, request 1), `test_manipulation.py` `HandOverTests.test_receiving_heavy_bag_uses_both_hands` (B-R7, request 2), `test_room_golden.py` (B-R11, request 3); known since B4-MERGE: `test_body_model.py` (2), `test_physics_forces.py` (1), `test_camera_actions.py` `FaceTests.test_arguments_and_guards` (BODY-ENGINE requests 1-3), `test_camera.py`, `test_textiles_laundry.py` |
| G1b | FAIL | isolated domains: the 9 known failures plus the same `test_manipulation` test (request 2); kernel files core-only 13/17: the four files pinned since B4-MERGE (`test_body_core.py` green core-only) |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 242/242 (1,210 trials); 0 verdict changes; 128 state-digest and 57 observation-digest changes (allowed: below) |
| G3 | PASS | forced realistic 226/242 (as B4-MERGE); 0 verdict changes; 128 / 52 digest changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz, room and geometric: 1,800 commands each, 0 defects, 0 nondeterministic (375 succeeded, as before) |
| G7 | PASS | 3.03 ms per substep (B4-MERGE 3.01, limit 3.16) and 1.095 s for the 1 h wait (1.084, limit 1.138) |
| G8 | FAIL | room golden: the fixed specs differ in 1 spec (X-bf82170244be22-01, a speech entry's `audible`), golden_all in 21 specs; the 8 scripted runs are unchanged. BODY-FAULTS re-records (B-R11, classes below) |
| G9 | PASS | `body_audit --baseline`: ok, 0 errors |
| G10 | PASS | 11 changed files, 0 outside the BODY-CORE+NAV-M5 list |

`python tools/orchestration/tier1_gate.py compare B4-MERGE BODY-CORE --package BODY-CORE+NAV-M5`:

```
compare B4-MERGE -> BODY-CORE (packages: BODY-CORE+NAV-M5; allowed: obs,state,verdict; codes: B-R10,B-R11,B-R7,B-R8,B-R9)
G0   PASS  action fingerprint unchanged (482f9adf9717)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 128 with changed state digests (allowed), 57 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 128 with changed state digests (allowed), 52 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 375 -> 375
G7   PASS  substep_ms: 3.01 -> 3.03 (+0.7%; limit 3.16 = prev x 1.05)
           wait_1h_s: 1.0835 -> 1.0946 (+1.0%; limit 1.138 = prev x 1.05)
G1   INFO  new test file test_body_core.py (normal): 35 tests
           new test file test_body_core.py (strict): 35 tests
compare result: PASS
```

### Reviewed differences (3.3)

No verdict changed in either mode (careful plans included: they only got faster), so no row is needed. Every changed
trial was classified (31 specs, 128 trials per mode, the same in both modes; reference time and digests per trial from
the gate records, paths from `tools/golden.py dump --all` before the first edit and at hand-over, seed 0):

| Code | Catalog ids | Mode | What changed |
|---|---|---|---|
| B-R9 | A-8220afe27c03e7-01, A-c0f8c53da99d24-01, T-151f6733397156-01, T-5fccf30a680f1b-01, T-9a6cc7b8c7cefc-01, T-c12fbedcf2888d-01, T-d901f3b7dda58a-01 (seeds 2, 41), T-e111147fc8a148-01, T-f036622c9240b7-01 (seeds 1, 41, 97), X-bc4e8d373e62c5-01 | both | careful plans take 2x instead of 3x for their walks: reference time -1.0 to -94.5 s; every clock-derived path follows (action_log / facts / logs times, room climate, food and thermal state, battery and energy) |
| B-R7 | T-847e9e300bdea8-01 (all seeds), T-dde0975c7a26b8-01 (seeds 0, 2) | both | papers lying flat are slid to the edge before the lift: +3 s each (+12 s / +6 s) and the clock-derived paths |
| B-R7 | T-73a13fb297c40f-01 | both | a wheeled bin (admin_inventory `wheel_cart`, towed) counts 15 % of its mass in the hands: less walking and holding energy (`robot.battery`, `energy_wh_used`, `stats.energy`), nothing else |
| B-R8 | A-338a38bf770c8e-01, T-1983509f271ebc-01, T-1aea0c41439b0a-01, T-7e93003b0dcefa-01, T-89a369ff52000f-01, T-954f0fa3af60ac-01, V-10424aa789320a-01, X-1742e6de23d6be-01, X-bf82170244be22-01, X-d734ea3e299b16-01, P-6f82b375df3cc1-01 (seeds 2, 41), T-123d987460eb2a-01, T-457afb032d7be7-01, T-e9cf6ca709ec77-01 (seeds 1, 41, 97); wrong plans only: A-d9e7a4d5d112d2-01, T-1970299fc6d849-01, T-47d0adb37bc85e-01, T-e95dd4ba6e8734-01 | both | the robot's speech entries carry `audible: true` (`messages.*.audible`) |

Not seen in any reference run: an overload, a refused grasp, a drop or strain of the safety net, a stairs refusal, a
sensor refusal, a powered-down skill. B-R10
(the setup ops) is unused by authored specs. B-R11: section "Golden" below.

### body_audit (G9 and the landing check)

`python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json` on the gated copy: 1,210 trials,
0 errors, 2,108 executed picks (the same count as B0: no reference pick was refused), **0 over the derated capacity, 0
explicit power grips under 15 mm, 0 under 2 mm, 0 auto pinch**; lowest battery 17.69 % (T-5ad61b6881821b-01 seed 1);
careful speed in the same 14 files. So no spec needed fixing and no `tasks/authored/*.json` file was edited. The five
picks with the least margin (B0 section 9) still pass: none of them is above the head, on an aid or in limp mode.

### Golden (3.5; B-R11, handed to BODY-FAULTS)

`tools/golden.py dump --all` before the first edit (on the base snapshot) and at hand-over, then `golden.py diff`:

```
274 file(s) compared, 21 changed, 129 path pattern(s)
  8 scripted runs (seeds 0-3 x oracle / realistic): unchanged (digests and every observation)
 21 specs (seed 0): 8 careful-timing specs (B-R9), 2 paper-grasp specs (B-R7, +3 s per paper), 1 towed-cart spec
    (B-R7, battery only), 10 speech-only specs (B-R8, messages.*.audible; X-bc4e8d373e62c5-01 also has it)
  largest patterns: entities.*.temperature_c 1152, entities.*._tt 272, action_log.*.time_s 238, rooms.*.pollen 125,
    rooms.*.co2_ppm 123, ... (all clock-derived), messages.*.audible 17, robot.battery / battery_min_pct /
    energy_wh_used / stats.energy 11 each
```

No path outside these classes changed: no hands, held item, entity placement, refusal, stat other than `stats.energy`,
or observation of the scripted runs. BODY-FAULTS records the golden once for both B5 packages with these classes.

### Benchmarks against 3.4 (this machine, best of 7)

| Measure | Budget | BODY-CORE |
|---|---|---|
| settlement per leaf carrying two items, safety net on (649 / 853 entities) | <= 0.1 ms | 0.069 / 0.086 ms (off: 0.063 / 0.081; the net adds about 0.005 ms) |
| `careful_seconds` per leaf | - | 0.00016 ms |
| the lift path of `pick_up` (lift plan + grasp + check_lift) | - | 0.076 / 0.104 ms (a step is about 10 ms) |
| whole engine (G7) | <= B4-MERGE x 1.05 | 3.03 ms per substep, 1.095 s per 1 h wait (+0.7 % / +1.0 %) |

`tests/test_body_core.py` `PerformanceTests` fails at 3x the settlement budget. The body system per substep is unchanged
(BODY-ENGINE's 0.01-0.02 ms).

## 7. Known gaps

* **The two-hand decision of `forces.lift_plan` uses the true capacity** (`robot_caps` -> `body.capacity_kg` without
  `believed=True`): with both arms latently weak, "Both hands must be free for this item" can be the first sign of the
  weakness before any lift was tried (request 4). A single latent weak arm is found by `check_lift` as specified.
* **Domain verbs that put things into the hands without `check_lift`** (tools_vehicles `hanging._take_down`, electrical
  cords, plumbing nozzles, admin_inventory restock / packages, ...) are caught only by the safety net at settlement
  (strain above 1x, drop above 1.5x). Adoption round: lift through `ctx.check_lift`.
* **manipulation** still has its own receive rule (`_after_receive`: the "unexpectedly heavy" drop when the other hand is
  busy is now unreachable, the refusal comes first) and overwrites `held_grip` after `pick_up` (deviation 7).
* **navigation_robot's `_no_camera`** before-hooks answer privacy mode on `inspect` / `read` with their own wording
  ("Cameras are off (privacy mode); turn it off to look closely") before core's `require_imaging`.
* **Walking durations computed by domain verbs themselves** (not through `ctx.motion` / nested core moves) still get the
  careful x1.5 on their own seconds unless the verb declares `timing="speed"` (adoption round; e.g. manipulation's
  co-carry `support mode=follow`, people / pets escort verbs).
* **`listen`'s addressed filter** knows `to == "robot"` and `addressed_robot`; the people domain marks addressed speech
  (follow-up, TIER1_INTEGRATION 6).
* Not in this package: the perception side of privacy mode / power-off visibility, realistic `obs.robot` (BODY-FAULTS);
  `run_diagnostics`, `clean_camera` / `log_fault` through `body_faults` (navigation_robot adoption).

## 8. How to test

```
python -m unittest tests.test_body_core                      # 35 tests, about 3 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_body_core
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_body_core     # NavigationTimingTests skip without navigation_robot
python -m unittest tests.test_body_engine tests.test_kernel tests.test_navigation_robot
python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json --out <scratch>/body_audit.json
python tools/orchestration/tier1_gate.py compare B4-MERGE BODY-CORE --package BODY-CORE+NAV-M5
```

Manual checks (the hall of `tests/test_body_core.config`):

```python
from tests.test_body_core import make, thing
env = make(entities={"crate": thing("crate", 25.0, [0.5, 0.4, 0.4])})
env.step("pick_up", "crate")      # refused: "Object and contents exceed carrying capacity; get help or slide it" (safety)
env.world.s["stats"]["overload_attempts"]                          # 1
env = make({"speed_mode": "careful"}); env.step("walk_to", cell=[9, 1]); env.world.s["time_s"]   # 10.2 (2x, not 3x)
make({"privacy_mode": True}).step("look")["message"]               # "Cameras are off; turn privacy mode off to look closely"
```

## 9. Follow-ups

Requests to the main session (files outside BODY-CORE / NAV-M5):

1. `tests/test_camera_actions.py` `LookTests.test_cameras_off_refuse_look_in_geometric_mode_only` (line 270) pins "room
   mode: unchanged until BODY-CORE"; conflict 20 makes `look` need imaging in both modes. Replace lines 269-270 with:

   ```python
           room = env_of(camera_mode="room", pos=(5, 5), robot={"privacy_mode": True})
           result = room.step("look", "around")                          # BODY-CORE: both camera modes (conflict 20)
           self.assertEqual((result["ok"], result["message"], result["category"]),
                            (False, camera.CAMERA_OFF_TEXT, "perception"))
   ```
   (and rename the test `test_cameras_off_refuse_look_in_both_camera_modes`). BODY-ENGINE's request 3 (`face` with a flat
   battery) is still open in the same file.
2. `tests/test_manipulation.py` `HandOverTests.test_receiving_heavy_bag_uses_both_hands` (lines 458-461): receiving a
   9.2 kg bag with the left hand busy is now refused at lift-off ("Both hands must be free for this item", B-R7: a lift
   over the capacity is aborted, nothing is dropped) instead of being taken and dropped. Replace the last two lines with:

   ```python
           refused = e.step("receive", "bag")                            # BODY-CORE: refused at lift-off, not dropped
           self.assertEqual((refused["ok"], refused["message"]), (False, "Both hands must be free for this item"))
           self.assertEqual(ent(e, "bag")["location"], "ana")
   ```
   The first half (both hands free: `held_hand` both) passes unchanged.
3. `tests/data/room_golden.json`: BODY-FAULTS re-records it once for both B5 packages (B-R11); BODY-CORE's classes are in
   section 6 (until then `tests/test_room_golden.py`'s reference-plan test fails on 20 changed spec digests of seed 0).
4. `house_sim/kernel/phys/forces.py` `lift_plan` (no B5 owner): decide two hands from the believed capacity
   (`body.capacity_kg(state, None, eid, believed=True)`), so a latent weakness is found by `check_lift`, not by the plan
   (Known gaps).
5. `docs/KERNEL_API.md` "v3.0 robot body" (no B5 owner): line 992 "(the careful x1.5 stays until BODY-CORE)" -> "(the
   careful factor: `body.careful_seconds`, the leaf's own seconds x1.5, skipped for `timing="speed"`, also for nested
   leaves in `perform`)"; line 1029 "The held-load safety net at settlement (drop above 1.5 x capacity) lands with BODY-CORE
   (`body.HOLD_SAFETY_NET`)." -> "The held-load safety net at settlement strains above 1x and drops above 1.5x a hand's
   capacity (`body.HOLD_SAFETY_NET` True); a co-carried end and a towed cart count `body.load_share` of their mass."; and
   list `body.load_share`, `speed_problem`, `relocalize`, `careful_seconds`, `check_policy`, `core.take_hold`,
   `core.addressed_to_robot` and the setup ops `robot` (extended) / `robot_fault`.
6. `docs/domains/navigation_robot.md` (on no list; NAV-M2's request 1 is the same file): `step`, `go_to`, `follow`, `lead`
   and `patrol` are `timing="speed"` (careful speed slows them once; `step` walks at the body profile's speed).
7. Still open from earlier packages (unchanged here): `tests/test_body_model.py`, `tests/test_physics_forces.py`
   (BODY-ENGINE requests 1-2), `tests/test_camera.py` (CAM-RENDER), `tests/test_textiles_laundry.py` (PHYS-INT R7),
   `liquids.carry_hand_z_m` / `impact.tear` (BODY-ENGINE request 4).

For later packages:

* **BODY-FAULTS (B5, merges second)**: re-record the golden with the B-R11 classes of section 6 plus its own; a speech
  entry's `audible` false means nobody heard it; `listen` here already filters addressed speech, `obs.messages` is yours.
* **TIME-EVAL / PEOPLE-IO (C)**: `ask` / `listen` now call `ctx.require_hearing` first (PEOPLE-IO owns `core.py` in stage C:
  keep it); `addressed_to_robot` is the wake-word rule the notices can share.

Domain follow-ups (adoption round, TIER1_INTEGRATION 6), new or sharpened by this package:

* **manipulation**: `_after_receive` does nothing when the item is already held with both hands (the kernel decides the
  hands); keep the `held_grip` the core grasp planner stored; `require_payload` / `needs_two_hands` through
  `ctx.check_lift` / `body.capacity_kg`.
* **navigation_robot**: drop the `_no_camera` before-hooks (core `require_imaging`); `docs/domains/navigation_robot.md`
  (request 6).
* **people**: mark speech to the robot `addressed_robot` (or `to: "robot"`) everywhere; ignore `audible: false` robot
  speech.
* **every domain** that puts things into the robot's hands: lift through `ctx.check_lift` (and `ctx.require_grasp` for
  small things); verbs whose duration comes from a walking speed declare `timing="speed"`; carts and team carries set
  `towed` / `co_held_by` so the body counts the right share.

## 10. B5 merge gate (B5-MERGE)

Added by the B5 merge gate after both B5 packages had handed over. The same section is in `handoff_BODY-CORE.md` (which
also covers NAV-M5) and `handoff_BODY-FAULTS.md`. The merge gate read every `handoff_*.md`, TIER1_INTEGRATION (1.1,
1.3, 1.5, 1.9 with its rules, 1.10, 1.12, 1.13, 1.15, conflicts 6-9, 18-20 and 30, the BODY-CORE, NAV-M5 and
BODY-FAULTS rows of 2.4, 3, 3.5, 5), TIER1_DECISIONS and TIER1_EMBODIMENT (4.7-4.14, 5.4-5.8, 6, 8.1-8.3, 10). It then
ran two things on the merged tree, with the union of the three packages (`--package BODY-CORE+NAV-M5+BODY-FAULTS`):

* the full gate;
* the cross-package checks the plan lists for B5: the `body_audit` landing check against B0 (2.4, BODY-CORE), the M5
  verdict review and the careful specs (TIER1_EMBODIMENT 8.1 M5, 8.3; B-R9), the one golden record for both packages
  (3.5; B-R11 and B-R15), and the test-plan items whose action side is BODY-CORE's and whose observation side is
  BODY-FAULTS' (TIER1_EMBODIMENT 10 items 22, 24 and 31-33). It also probed every seam between the two packages.

**No behaviour misfit was found.** One documentation misfit is fixed inside the union, with regression tests:

1. **PERCEPTION.md described a shape inspection that core refuses with the cameras off.** File: `docs/PERCEPTION.md`
   (BODY-FAULTS').
   * Cause: BODY-FAULTS wrote that people and pets "in an unlit room (or any room while the cameras are off ...)" are
     shapes, and that "inspecting a shape gives only `label` person/animal and a note". That is true in an unlit room.
     With the cameras off, BODY-CORE's `inspect` refuses first with "Cameras are off; turn privacy mode off to look
     closely" (TIER1_INTEGRATION 1.15, conflict 8). Both packages follow the plan; only the sentence read as if both
     cases gave the label. The sensor-gate table also described the observation side only.
   * Fix: the sentence now names the unlit room and the refusal. The sensor-gate table now says what the verbs do in
     each row: with imaging off `look`, `inspect`, `read` and `peek` are refused; with hearing "addressed" `listen`
     applies the same filter and `ask` still works; with hearing "none" `listen` and `ask` are refused.
   * Tests: `tests/test_body_perception.py` `MergeSeamTests`, realistic perception, both camera modes, green in normal,
     strict and core-only runs:
     * `test_a_shape_is_inspected_in_the_dark_but_not_with_the_cameras_off`;
     * `test_setup_sensors_and_faults_reach_the_verbs_and_the_observation_alike`. BODY-CORE's setup ops (`robot`
       `sensors.mic_on: false`, `props.privacy_mode`; `robot_fault` latent `speaker_failed` and `localization_lost`)
       go through `apply_setup`. Each must reach both BODY-CORE's verbs and BODY-FAULTS' observation: hearing "off",
       no position and no messages; `listen` refused; speech of a deaf robot leaves the latent speaker fault unknown and
       unshown; `look` relocalizes and the position (and in geometric mode the camera pose cell) comes back. In privacy
       mode `obs.messages` and `listen` agree on the addressed speech; `ask` by name is refused with `BLIND_WHO` and
       works on the shape's handle.
   * Neither package's tests covered these seams end to end. The tests also pass on the tree as handed over (no code
     changed).

**Gate.** The record is in `catalog/analysis/tier1/gate/B5-MERGE/`. It was taken on an isolated copy so that G10
sees only B5's changes:

* The base commit is the shared tree as B4 handed it over: BODY-FAULTS' "B4 base" snapshot, which both B5 packages
  started from. It was compared file by file with the B4 merge gate's handed-over tree: all 801 files are equal.
* The working tree is the project tree (CRLF checkouts as LF). Its `git status` lists exactly the 23 B5 files: the
  18 edited files, the two handoffs and the three new test files.

Command:

```
python tools/orchestration/tier1_gate.py --root <iso> --gates-root catalog/analysis/tier1/gate record --id B5-MERGE
       --package BODY-CORE+NAV-M5+BODY-FAULTS --prev catalog/analysis/tier1/gate/B4-MERGE
       --out catalog/analysis/tier1/gate/B5-MERGE --work <scratch>/gate_work_final --jobs 7
```

G7 was then re-run alone on a quiet machine (`--only G7`, which keeps the rest of the record); the full run had read
3.13 ms / 1.106 s, also within budget. After this section was written, G10 was refreshed with `--only G10`.

Summary line: `TIER1-GATE B5-MERGE [BODY-CORE+NAV-M5+BODY-FAULTS] vs B4-MERGE: FAIL (10 pass, 2 fail, 1 skip)`. The two
FAILs are tests in files outside the union. Each pins behaviour that a package deliberately changed, and each has an
open request with a patch verified below. `compare` passes.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `482f9adf9717`, unchanged (425 actions, 111 systems) |
| G1 | FAIL | 45 files, 3,543 tests (normal); `test_body_core.py` (35), `test_body_faults.py` (11) and `test_body_perception.py` (23, two new) green in normal and strict runs. There are 14 failing runs (7 files x normal + strict), the same tests as `gate/BODY-FAULTS/`. Three are B5's own, with open requests: `test_camera_actions.py` `LookTests` and `test_manipulation.py` `HandOverTests` (BODY-CORE requests 1-2), and `test_body_engine.py` `TruncationTests` (BODY-FAULTS request 1). The rest are known since B4-MERGE: `test_body_model.py` (2 tests), `test_physics_forces.py`, `test_camera_actions.py` `FaceTests` (BODY-ENGINE requests 1-3), `test_camera.py` (CAM-RENDER) and `test_textiles_laundry.py` (PHYS-INT R7) |
| G1b | FAIL | isolated domains: the 9 failures known from B4-MERGE, plus `test_manipulation` `HandOverTests` (BODY-CORE request 2); kernel files with core only 14/19: the four files pinned since B4-MERGE plus `test_body_engine.py` (BODY-FAULTS request 1). All B5 test files are green core-only |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 242/242 (1,210 trials); against B4-MERGE 0 verdict changes, 128 state-digest changes (BODY-CORE's B-R7 / B-R8 / B-R9 trials) and 1,210 observation-digest changes (B-R12) |
| G3 | PASS | forced realistic 226/242 (as B4-MERGE); 0 verdict changes; 128 / 1,210 digest changes; the 250 realistic trials are identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric (with the `robot` checks): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery (`--battery 5 --faults random`): 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.10 ms per substep (B4-MERGE 3.01, limit 3.16; BODY-CORE 3.03, BODY-FAULTS 3.04) and 1.098 s for the 1 h wait (1.084, limit 1.138); the largest per-system increase is `climate.house`, +0.02 ms, which no B5 file touches |
| G8 | PASS | room golden and golden_all (BODY-FAULTS' record for both packages; this merge does not touch it) |
| G9 | PASS | `body_audit --baseline`: ok |
| G10 | PASS | 23 changed files, none outside the union |

Other comparisons:

* `compare BODY-CORE -> B5-MERGE --package BODY-FAULTS`: 0 verdict changes and 0 state-digest changes; every trial's
  observation digest changes (B-R12).
* `compare BODY-FAULTS -> B5-MERGE`: 0 verdict, 0 state-digest and 0 observation-digest changes in both modes. The
  merge's own changes are a test and a document.

**Reviewed differences.** None are needed, because no verdict changed. The digest classes are BODY-CORE's (B-R7, B-R8,
B-R9) and BODY-FAULTS' (B-R12), as their handoffs classify them; the golden check below confirms it.

**Cross-package checks beyond the gate.** They ran on the merged tree; the scripts are in the merge gate's scratch
folder (`t1_merge_B5/`).

* **`body_audit` landing check**
  (`python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json`): 1,210 trials, 0 errors,
  2,108 picks. Found: 0 over the derated capacity, 0 explicit power grips under 15 mm, 0 under 2 mm, 0 auto pinches,
  0 new hits against B0. Lowest battery 17.6921 % (T-5ad61b6881821b-01 seed 1). Careful speed is in the same 14 spec
  files.
* **M5 verdict review and the careful specs.** G2 and G3 have no verdict change. The careful specs of BODY-CORE's B-R9
  rows pass on every seed in their own mode, and under forced realistic perception their verdicts equal B4-MERGE's
  (A-8220afe27c03e7-01 fails there, as it has since A0). `compare` shows that the merged tree's state digests equal
  `gate/BODY-CORE/`'s in every trial: BODY-FAULTS changes observations only, so BODY-CORE's timing review stands as
  recorded.
* **Golden.** G8 is green (golden and golden_all) with BODY-FAULTS' record (`history`: BODY-FAULTS, classes B-R7, B-R8,
  B-R9, B-R12). BODY-FAULTS' union diff runs from the B4 tree to the merged tree (274 files, 37 changed, 168 path
  patterns). Every changed path belongs to one of these classes:
  * clock-derived paths of the careful and paper-grasp timing (B-R9, B-R7);
  * `messages.*.audible` (B-R8);
  * `robot.battery` / `battery_min_pct` / `energy_wh_used` / `stats.energy` (timing and the towed cart, B-R9 / B-R7);
  * `obs.robot.*` and `obs.battery` (B-R12).

  These are BODY-CORE's B-R11 classes plus BODY-FAULTS' own.
* **Seam probes** (`probe_seams.py`): a hand-built house with every domain loaded, all four modes (oracle / realistic
  x room / geometric), in normal and strict runs. Eight probes:
  * imaging off in five ways through the setup ops (cameras off, privacy mode, an "always" room policy, a
    `camera_failed` fault, `water_ingress`): verb refusals and texts, `obs.dark`, what is listed and targetable,
    `listen` against `obs.messages`, `ask`;
  * microphones off and failed;
  * a latent failed speaker, with and without hearing;
  * a lost pose in a lit room, a dark room and with the cameras off;
  * timed faults (`at_s` / `until_s`) against the realistic fault list and the network word;
  * a latent weak arm found by a failed lift, then shown as "weak", and the load felt;
  * a careful walk that powers the robot down;
  * determinism, save / load and replay across all of these.

  Result: 0 problems. Realistic times equal the oracle's rounded half up (`engine.whole_seconds`).
* **Cross-package fuzz** (`xfuzz_b5.py`): generated houses of seeds 0-3, every domain loaded, `HOUSE_SIM_STRICT=1`,
  all four modes.
  * Start variants: plain, privacy mode, cameras off, microphones off, a room privacy policy, a lost pose, a latent
    failed speaker (seeds 0-3), and a nearly flat pack (seeds 2-3). Each run also gets three random fault events
    (`body_faults.sample_fault_events`) and injected speech, addressed and not.
  * Each run is 40 steps of the sensor, lift and motion verbs, run twice, plus a save / load in the middle.
  * After every step it checks the S1-S7 rules in the script's docstring. Among them: the verb refusals against the
    body's sensor state; `listen` and `obs.messages` against the hearing rule; the observation's dark flag, power
    gate, camera / hearing words, position and gauge against the body; and `tools/perception_audit.py`'s
    `check_obs` / `check_message` on every realistic step.
  * Result: 120 runs, 9,600 steps (each run counted twice), 0 problems, all deterministic, and every save / load
    continues identically. Covered: 5,672 imaging-off steps, 2,168 deaf steps, 2,550 addressed-hearing steps,
    220 powered-down steps, 1,024 unlocalized steps, 48 relocalizations, 1,174 camera refusals of `look`,
    244 microphone refusals and 44 inaudible speeches.
* **The open test-patch requests, checked.** BODY-CORE's requests 1-2 and BODY-FAULTS' request 1 were applied to a copy
  of the merged tree, together with BODY-ENGINE's requests 1-3 as the B4 merge gate patched them. Results:
  * `test_body_engine.py`, `test_body_model.py`, `test_physics_forces.py`, `test_camera_actions.py` and
    `test_manipulation.py` are green in normal and strict runs; the first four are also green core-only.
  * `test_manipulation.py` with only manipulation loaded fails only its two tests known since A0.
  * `test_body_engine` and `test_room_golden` in one process are green.

  With these patches and the two older requests (`test_camera.py`, CAM-RENDER; `test_textiles_laundry.py`, PHYS-INT
  R7), G1 and the core-only part of G1b would pass, and G1b's isolated domains would be back to the known failures.

**Seams checked and found consistent** (no change needed):

* **Imaging.** `look`, `inspect`, `read` and `peek` refuse exactly when perception gives the depth-only view; the
  camera-fault text is used when the hardware failed. The same `Perception.visible` guards `pick_up` and the other
  verbs: an unseen small thing is not targetable, a 0.4 m shape is. Realistic `obs.robot.camera` reads "off" and
  `obs.dark` is set.
* **Hearing.** `core.addressed_to_robot` and `perception.heard_messages` apply the same rule, so `listen` and
  `obs.messages` agree. `ask` in privacy mode answers through `facts.last_answer`, which is not filtered.
* **Speech.** `say()`'s `audible` comes from `body.can_speak`, and a latent speaker fault is detected only when the
  robot can hear. The robot's own speech entries carry no `heard_by_robot`, so `obs.messages` never shows an
  `audible` flag. Realistic `obs.robot.faults` lists `speaker_failed` only after detection.
* **Localization.** `look` relocalizes in a lit room using `perception.lit`, the same light rule `seen_dark` uses with
  imaging on. Realistic `pos` stays None until then, and the geometric camera pose cell mirrors it. `face` works while
  lost; the camera block's `frames` is a count, not cells.
* **Setup to perception.** The records written by the `robot_fault` op (including `water_ingress`'s implied faults)
  carry their `kind`, so BODY-FAULTS' kind-less `normalize_faults` path is a convenience, not a dependency.
  `robot.fault` event arguments from `robot_fault` and from `body_faults.sample_fault_events` have the same form.
* **Lifts to proprioception.** `body.load_share` (co-carry, towed cart) flows through `held_loads` into `_hand_kg` and
  so into realistic `obs.robot.load`. A latent weak arm that `check_lift` finds turns `obs.robot.arms` to "weak".
* **Power-down.** Only held or worn things are visible, and `look` and `listen` meet `PowerOff`. Realistic power,
  camera and hearing words read off / off / off, and the integer gauge equals `obs.battery`.
* **Realism names.** Naming a person while the cameras are off is refused with `BLIND_WHO`. The shape handles work for
  `ask` (and for `inspect`, which is then refused for the cameras). In the dark and in privacy mode, the speakers in
  `listen`'s output are the same shape handles as in `obs.people`.

**Still open for the main session** (files outside the union; patches verified above where they are tests):

* BODY-CORE's requests 1-2 (`test_camera_actions.py` LookTests, `test_manipulation.py` HandOverTests) and 4-6
  (`forces.lift_plan` believed capacity, `docs/KERNEL_API.md`, `docs/domains/navigation_robot.md`);
* BODY-FAULTS' requests 1-2 (`test_body_engine.py`, `docs/KERNEL_API.md`);
* the earlier ones: BODY-ENGINE 1-4, CAM-RENDER 1, PHYS-INT 1, NAV-M2 1-4.

No new domain follow-up. navigation_robot's own privacy-mode wording on `inspect` / `read` is already listed by
BODY-CORE.

## 11. Stage-B review fix round (STAGE-B-FIX)

Added after the three stage-B reviews (`catalog/analysis/tier1/stageB_review_physics.json`, `stageB_review_camera.json`,
`stageB_review_body.json`: 6 + 15 + 13 findings). The same section is in `handoff_PHYS-INT.md`,
`handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`,
`handoff_BODY-CORE.md` and `handoff_BODY-FAULTS.md`. Read before starting: TIER1_INTEGRATION (1 with 1.9, 2.4, 3, 5, 6),
TIER1_DECISIONS, TIER1_PHYSICS / TIER1_CAMERA / TIER1_EMBODIMENT where the findings point, and every `handoff_*.md`.
Every finding was reproduced with the reviewer's probe on the B5-merge tree first. Every fix has a regression test that
fails on a copy of that tree and passes now. Only stage-B package files (2.4) and these handoffs were edited.

### 11.1 Findings

Status: **fixed** (root cause fixed, regression test), **still open** (needs a file outside every stage-B package;
the exact change is in 11.5). No finding was rejected: all 34 reproduced.

| # | Finding (review, severity) | Status | What changed (file) | Regression test |
|---|---|---|---|---|
| P1 | Exogenous events charged to the robot for touching any sibling under the same furniture, or for only opening a cabinet (physics, high) | fixed | `impact.robot_caused(w, eid, cmap=None, load=())`: the robot's when the action phase runs, or when the marker is on the item itself or a support in its location chain; for support failures (collapse, creep, pull-out, tear, crush, stack, overbalanced furniture) also on the load that failed (`load=`). Siblings never count. `settle._mark` writes `_robot_moved` only for arranged changes (`settle._arranged`: anything but `open`). Docs: PHYSICS.md, CONVENTIONS.md, AUTHORING_GUIDE.md, events.py | `test_physics_settle`: `test_a_scripted_knock_of_a_centrepiece_is_exogenous_although_the_robot_set_the_table`, `test_opening_a_cabinet_does_not_make_a_scripted_drop_inside_it_the_robots` (with the robot-placed control), `test_a_support_failure_is_the_robots_only_when_its_own_load_is` |
| P2 | One fast set-down counted twice (manipulation's chip plus the kernel break) (physics, medium) | fixed | `data/physics/laws.json`: `setdown` deferred, `duplicated_by` manipulation and food (TIER1_PHYSICS 10.1: only laws no domain duplicates are active; the 10.2 table missed manipulation's `after_placement` chip). PHYSICS.md law table, KERNEL_API.md | `test_physics_settle`: `test_the_set_down_law_is_deferred_so_one_fast_set_down_counts_once` (fragile egg on stone: no break, damage at most once, manipulation's chip when loaded); the two set-down tests force the law on |
| P3 | Eggs the set-down law broke stayed intact for food (physics, medium) | fixed | the same deferral: no agent action breaks an egg through the kernel until food registers `phys.broke` (food is in `duplicated_by`) | the P2 test |
| P4 | A staged "about to give way" shelf showed no sagging cue (physics, medium) | fixed | `impact.sag_cue(w, sid, env=None, cmap=None)` (the overload law's cue without a collapse); `setup.stage_cues` after all ops (either op order); settle refreshes it for `_phys_staged` supports while the overload law is deferred; `creep_fire` refreshes it after its check. AUTHORING_GUIDE.md | `test_physics_setup`: `test_a_staged_shelf_sags_at_a_glance_before_its_creep_time_and_stops_once_unloaded` (realistic glance, both op orders, unload clears it, no collapse) |
| P5 | One collapse heard as 39 sounds (physics, low) | fixed | `observe.heard_sounds`: one entry per sound kind and direction, the loudest word, the first source from the robot's room, `count` "several" (2-4) / "many" (5+); `perception_audit` accepts and checks `count` | `test_physics_settle`: `test_a_cascade_is_heard_as_one_crash_and_one_clatter_not_one_sound_per_item` |
| P6 | `place ... orientation=upside_down` kept an open mug full (physics, low) | fixed | `liquids.orientation_spill(w, eid, by="robot")` (the open-spill rule of an orientation, independent of the deferred law, which stays deferred for the domains' orientation verbs); core `place` calls it when an orientation is given | `test_physics_setup`: `test_placing_an_open_mug_upside_down_pours_it_out` |
| C1 | An open door leaf was a solid 2 m block over its swept area: a robot in the doorway saw almost nothing (camera, high) | fixed | `sight._open_leaves`: a 4 cm leaf at 90 degrees at its hinge jamb (the jamb opposite door_swing's swept diagonal; at a wall end, the corner jamb), tested exactly on every ray (`RoomGeom.leaves` in `_transmission`, `los` boxes, the lidar); door_swing's cells stay the navigation rule. Reviewer probe p8 over seeds 0-5: all 72 door / room pairs now within 15 % of the closed-door coverage (were down to 2-15 %); A-c83efc7adcd8f5-01 seed 2 gets past `approach door_3` | `test_camera.DoorLeafTests` (only what is behind the leaf is hidden; coverage from the doorway; corner leaf flat on the side wall; lidar) |
| C2 | `looked_in` granted for a grazing look over a box rim (camera, high) | fixed | `camera._looked_in` judges the interior: `sight.interior_points` (a top opening's inner floor on a 3 x 3 grid; a front opening's back wall above every shelf band) through the opening (`sight.interior_transmission`), at least half of every group in frame and line of sight (`LOOKED_IN_SHARE` 0.5); px and light rules unchanged. KERNEL_API.md, AUTHORING_GUIDE.md | `test_camera`: `test_a_box_counts_as_looked_into_only_when_its_inside_is_seen` (0.75 / 1.25 / 4.5 m: no; 0.25 m: yes; a cabinet's lower shelf needs a crouch) |
| C3 | Geometric `inspect` listed unseen things under / behind the target (camera, high) | fixed | core `inspect`: `contents` only what a frame of this step framed (`camera.in_frame`), the where= peek saw, or an earlier look revealed under / behind | `test_camera_actions`: `test_inspect_lists_only_contents_a_frame_saw` (cushion coin, kneeling bed, oracle and realistic: no handle issued) |
| C4 | Inspecting a backlit / far person returned name, role, activity and mood (camera, high) | fixed | core `inspect` (geometric, realistic): `camera.agent_identified(env, aid)` (level identify: 64 px, light 0.2, not backlit) else the shape note `camera.WHO_TEXT`, no facts and no inspection record. The review also suggested 160 px for activity and cues; not applied, because the observation already shows both at identify level (a shape answer there contradicted it; close-range details stay the domain inspectors' own, e.g. safety's responsiveness and breathing within 2 cells). KERNEL_API.md | `test_camera_realism`: `test_inspecting_a_backlit_person_tells_nothing_about_who_it_is`, `test_a_person_the_camera_identifies_is_read_by_inspect_as_the_observation_shows_her` |
| C5 | A two-hand carried load never occluded the head camera (camera, high) | fixed | `sight.carried_view` / `carried_blocks`; `sight._occluders` adds the load for head-camera Viewers at the current eye (seen floor cells, frame-level visibility); `camera._carried_filter` for the step view, `in_frame` and `_sees`. `can_see` (any-direction, the robot can turn) is unchanged | `test_camera`: `test_a_carried_basket_hides_the_floor_ahead_from_the_head_camera` (toys at 1.0 / 1.5 m hidden, 3 m listed, coverage drops) |
| C6 | Wall fixtures on south / east walls floated 0.4-0.47 m out (camera, medium) | fixed | `sight._wall_mounted`, `sight._wall_snap` in `_place_new` (flush to the wall the footprint touches; ceiling fixtures excluded) | `test_camera`: `test_wall_mounted_fixtures_sit_flush_on_their_wall` (every wall-mounted perimeter fixture of seeds 0-3) |
| C7 | The wrist camera worked with both arms failed (camera, medium) | fixed | `camera.wrist_access`: `body.arm_problem(state, None)` refuses (held things excepted); read / inspect fall back to the head | `test_camera_actions`: `test_the_wrist_camera_needs_a_working_arm` |
| C8 | Geometric privacy violations counted twice while the people domain is loaded (camera, medium) | fixed | `camera._log_capture`: the kernel fallback counts only when the people domain is not loaded (`_people_domain_loaded`; TIER1_DECISIONS "people domain not loaded"); the capture is still logged. KERNEL_API.md | `test_camera`: `test_with_the_people_domain_loaded_its_own_count_stands_in_both_camera_modes`; the fallback tests patch the domain out |
| C9 | `camera.watch` 0.10-0.13 ms per substep with people (camera, low) | still open | improved, not within budget: `camera._watch_signature` memo (exact: geometry epoch, robot pose and sensors, the room's agents, ambient light, haze; off while a light / occluder hook is registered) reuses the last substep's answers; captures are computed only when someone is private. Lab scene 0.083 -> 0.026 ms; generated houses seeds 0 / 1 / 3 0.14-0.17 -> 0.078-0.090 ms (budget 0.05): the remaining cost is the geometry-epoch judgement of the substep's touches and the ambient light, both needed every substep | `test_camera`: `test_the_watch_memo_never_changes_the_log` (memo on vs off identical) |
| C10 | Head tracking turned an e-stopped robot's body (camera, low) | fixed | `camera._track_pose` and `gaze_to` use `can_turn` (with `stopped`) | `test_camera`: `test_tracking_never_turns_the_body_of_a_stopped_robot` |
| C11 | Sightings only at end_step (first_s at the end of a wait, in-wait sightings lost) (camera, low) | fixed | `camera.watch` notes sightings per substep (`_note_sighting`, first_s at the substep's start); `camera.note_start(env)` logs the reset view (engine `reset`, geometric only) | `test_camera`: `test_sightings_follow_the_watch_and_the_reset_view` |
| C12 | `robot_in_view_s` credited an interval ahead (camera, low) | fixed | `_watch_specs` integrates elapsed time since the previous evaluation when the robot was in frame then (`robot_seen`) | `test_camera`: `test_view_watch_specs_report_what_a_viewer_sees` (90 s: 60 s, not 90) |
| C13 | Naming an unidentified person in a lit room gave the darkness text (camera, low) | fixed | `realism.FAR_WHO` in a lit room | the C4 test |
| C14 | `peek`'s time grew by the head's auto-gaze (camera, low) | fixed | `Ctx.obj(..., gaze=True)`; `core.wrist_peek` and `inspect` with where under / behind / inside pass `gaze=False` (the wrist looks; the head stays put) | `test_camera_actions`: `test_peek_takes_its_documented_time_whichever_way_the_head_looks` |
| C15 | Three camera test pins (camera, low) | fixed | `test_camera.py` (CAM-RENDER request 1), `test_camera_actions.py` (BODY-ENGINE request 3, BODY-CORE request 1); also `test_body_engine.py` (BODY-FAULTS request 1: the powered-down probe, `load_domains()` at import) | the patched tests |
| B1 | The safety net applied lift-off derates to held loads: crossing 5 % or stepping onto a stool dropped and broke a 6.5 kg pot (body, high) | fixed | `body.hold_capacity_kg` (no limp, aid, overhead or above-head derate; hooks still apply) in `_hold_check`; `_derate(..., hold=)` | `test_body_core`: `test_a_held_load_is_not_dropped_by_limp_mode_or_a_climbing_aid` |
| B2 | `runtime_min_est` / `range_m_est` weighed a held item to 0.2 kg (body, high) | fixed | `proprio.estimates(state, level=None)`: empty-handed nominal powers; `perception_audit` checks the estimates carry no load term. PERCEPTION.md | `test_body_perception`: `test_the_battery_estimates_do_not_weigh_what_the_robot_holds` |
| B3 | Domain code doing `sorted(set(robot.faults))` crashes on the dict fault log (body, high) | still open | domain files only (11.5) | - |
| B4 | Domain verbs relocate the robot without ctx.motion: a fallen / legs-failed / unlocalized / wheeled robot guided someone upstairs for free (body, high) | fixed (kernel backstop) | `body.unrecorded_motion(ctx)` after every leaf's AFTER hooks (engine `step` and `perform`): a move without a motion record becomes a synthesized record (straight line, the doorway, the stairs; `"synthesized": True`) paid on top of the action's own activity power, and a body that could not walk refuses (`mobile_problem` with the fallen state at the action's start). `Ctx._robot_down`, `_robot_aid`. The domains still need their own routes (11.5) | `test_body_engine.UnrecordedMotionTests` (energy, stairs, five refusals, rolled back) |
| B5 | `lift_plan` took a known weak right hand (body, medium) | fixed | `forces.lift_plan`: believed capacities (`believed=True`) decide two hands and pick the free hand that can take the mass, right first (also BODY-CORE request 4); core `receive` / `uninstall` take the plan's hand when none is named | `test_body_core`: `test_a_known_weak_arm_hands_the_lift_to_the_arm_that_can_take_it` |
| B6 | Gripper slips drawn per leaf, not per metre (body, medium) | fixed | `body_faults.slip_probability(state, meters)`: 1 - (1 - p)^(m / ref), `fault_p.gripper_slip_ref_m` 10 m (profiles.json); `slip_check(..., meters=None)` (None keeps the per-draw probability) | `test_body_faults`: `test_the_slip_hazard_is_per_metre_not_per_walk` (one 5 m walk vs ten 0.5 m: 6 / 40 and 6 / 40; was 9 vs 38) |
| B7 | Careful speed stretched commanded durations (body, medium) | still open | kernel part landed: ActionSpec `timing="commanded"` (actions.TIMINGS), skipped by `body.careful_seconds`; KERNEL_API.md. The domain verbs must declare it (11.5) | `test_body_core`: `test_careful_speed_does_not_stretch_a_commanded_duration` |
| B8 | The wheeled base climbed onto stools (body, medium) | fixed | profiles.json `climb` (false for mobile_manipulator); `body.climb_problem`, `NO_CLIMB_TEXT`; `unrecorded_motion` refuses a leaf that put a non-climbing body on an aid | `test_body_engine`: `test_a_wheeled_base_cannot_climb_onto_an_aid` |
| B9 | `tools/body_audit.py` models the lift with its own hand rule (body, medium) | still open | stage-A tool, no stage-B owner (11.5) | - |
| B10 | `power_down` set a fallen or sitting robot to crouching (body, low) | fixed | `body.power_down` lowers only a standing posture | `test_body_faults`: `test_a_power_down_lowers_the_posture_only_from_standing` |
| B11 | A power-down on an aid left it occupied and the robot in its cell (body, low) | fixed | `body_faults.fall` (`_off_the_aid`): frees the aid, lands beside it (the cell it was climbed from, else the nearest free cell) | `test_body_faults`: `test_a_power_down_on_a_climbing_aid_frees_it_and_lands_beside_it` |
| B12 | Safety-net and slip drops fell from the standing hand height (body, low) | fixed | `body.hand_drop_m(state)` (hand height x posture eye ratio + aid height) in `_hold_check`, `slip_check`, the arm-failure drop | `test_body_core`: `test_a_dropped_load_falls_from_where_the_hands_are` |
| B13 | Safety stops marked heard by a deaf robot (body, low) | still open | domain file (11.5) | - |

### 11.2 Files

All LF. Owners from TIER1_INTEGRATION 2.4 (stage B columns).

* Physics (PHYS-INT): `house_sim/kernel/phys/impact.py`, `settle.py`, `events.py` (docstring), `observe.py`,
  `liquids.py`, `forces.py` (PHYS-INT / BODY-ENGINE), `house_sim/data/physics/laws.json`, `house_sim/tasks/setup.py`,
  `tools/perception_audit.py`, `docs/PHYSICS.md`, `docs/CONVENTIONS.md`.
* Camera (CAM-RUNTIME, CAM-ACTIONS, CAM-RENDER): `house_sim/kernel/sight.py`, `camera.py`, `realism.py`,
  `house_sim/systems/core.py` (`inspect`, `place`, `wrist_peek`), `house_sim/kernel/actions.py` (`Ctx.obj` gaze),
  `docs/KERNEL_API.md`, `docs/AUTHORING_GUIDE.md`, `docs/KNOWN_LIMITS.md`.
* Body (BODY-ENGINE, BODY-CORE, BODY-FAULTS): `house_sim/kernel/body.py`, `body_faults.py`, `proprio.py`,
  `engine.py` (`reset`, `step`, `perform`), `actions.py` (`TIMINGS`, `BodyView`, `Ctx` start state),
  `house_sim/data/robot/profiles.json`, `house_sim/systems/core.py` (`receive`, `uninstall`), `docs/PERCEPTION.md`.
* Tests: `tests/test_physics_settle.py`, `test_physics_setup.py`, `test_camera.py`, `test_camera_actions.py`,
  `test_camera_realism.py`, `test_body_engine.py`, `test_body_core.py`, `test_body_faults.py`,
  `test_body_perception.py`; `tests/data/room_golden.json` (re-recorded, 11.4).
* These seven handoffs (this section).

### 11.3 APIs added or changed

* `impact.robot_caused(w, eid, cmap=None, load=())`: the attribution rule of P1 (also `impact._by(..., load=)`).
  `settle` writes `_robot_moved` only for an arranged change (`_arranged`: any signature field except `open`).
* `impact.sag_cue(w, sid, env=None, cmap=None)`: writes or clears `sagging` / `_phys_sag` from `statics.overload`
  for one support. `setup.stage_cues(ctx)` runs it for every staged support after the ops.
* `observe.heard_sounds(state, step=None)`: one entry per (kind, direction); an entry merged from two or more sources
  has `count` (`COUNT_WORDS`: "several", "many"). The audit knows the key.
* `liquids.orientation_spill(w, eid, by="robot")`: pours an open container out for an orientation that spills.
* `forces.lift_plan(w, eid, hands=None, helpers=())`: decides with the believed capacities and orders the free hands
  so that one able to take the mass comes first (right first).
* `laws.json` `setdown`: `"deferred"`, `duplicated_by` manipulation and food.
* `sight`: `RoomGeom.leaves`; `interior_points(state, info)`, `interior_transmission(state, geom, vs, eye, info, p)`;
  `carried_view(state, cmap=None)`, `carried_blocks(view, ex, ey, ez, px, py, pz)`; constants `LEAF_T_M`,
  `INTERIOR_INSET_M`, `CEILING_MOUNT_M`.
* `camera`: `LOOKED_IN_SHARE`, `WHO_TEXT`, `agent_identified(env, aid)`, `note_start(env)`; `wrist_access` refuses
  with the arm problem; `watch` memo (`_WATCHED`, `_watch_signature`).
* `realism.FAR_WHO`.
* `actions.Ctx.obj(eid, touch=False, grasp=False, reach=True, action=None, mutable=False, gaze=True)`;
  `actions.TIMINGS` adds `"commanded"`; `BodyView` answers `hold_capacity_kg` and `hand_drop_m`.
* `body`: `hold_capacity_kg(state, hand)`, `hand_drop_m(state)`, `climb_problem(state)`, `NO_CLIMB_TEXT`,
  `unrecorded_motion(ctx)` (engine `step` and `perform` call it after the AFTER hooks, before `note_action`);
  `power_down` lowers only a standing posture; `careful_seconds` skips `timing="commanded"`.
* `body_faults`: `SLIP_REF_M`, `slip_probability(state, meters=None)`, `slip_check(w, hand, ctx, meters=None)`;
  `fall` frees a climbing aid.
* `proprio.estimates(state, level=None)`: the battery estimates the observation shows, without a load term.
* `profiles.json`: `climb` (humanoid true, mobile manipulator false); `fault_p.gripper_slip_ref_m` 10.

### 11.4 Gate (STAGE-B-FIX)

The record is in `catalog/analysis/tier1/gate/STAGE-B-FIX/`, taken on an isolated copy so that G10 sees only this
round's changes: the base commit is the tree the B5 merge gate handed over (the project's uncommitted tree with this
round's files taken from the copy made before the first edit), and the working tree is the project's current files
(CRLF checkouts as LF). Its `git status` lists exactly this round's 34 code, test, golden and doc files. After this
section was written, `docs/KNOWN_LIMITS.md`, the last `docs/AUTHORING_GUIDE.md` edits and the seven handoffs
were copied in and G10 was refreshed with `--only G10`. The geometric preview below then led to one refinement of C4
(`camera.agent_identified` takes the identify level alone, with a new test); G1 / G1b / G8 and G4 / G6 / G10 were
re-run on it (`--only`), and the numbers below are those runs. The code the gate ran is the project's.

```
python tools/orchestration/tier1_gate.py --root <iso> --gates-root catalog/analysis/tier1/gate record --id STAGE-B-FIX
       --package PHYS-INT+CAM-RUNTIME+CAM-ACTIONS+CAM-RENDER+BODY-ENGINE+NAV-M2+BODY-CORE+NAV-M5+BODY-FAULTS
       --prev catalog/analysis/tier1/gate/B5-MERGE --extras physics,camera,body --out catalog/analysis/tier1/gate/STAGE-B-FIX
       --work <scratch>/gate_work --jobs 7
```

Summary line: `TIER1-GATE STAGE-B-FIX [PHYS-INT+...+BODY-FAULTS] vs B5-MERGE: FAIL (9 pass, 3 fail, 1 skip)`. The G1 and
G1b failures are tests in files outside every stage-B package, each with a request (11.5 item 1); G7 is 0.1-1 % over a
fixed bound that the B5 tree also misses on this machine today (below). `compare` passes everything but G7.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `482f9adf9717`, unchanged (425 actions, 111 systems) |
| G1 | FAIL | 45 files, 3,575 tests, normal and strict. 12 failing runs: 7 tests in 6 files, the same in both modes, all outside the stage-B packages: `test_body_model.py` (2), `test_physics_forces.py`, `test_manipulation.py` `HandOverTests` (known from B5-MERGE, BODY-ENGINE requests 1-2, BODY-CORE request 2), `test_textiles_laundry.py` (PHYS-INT request 1), and two new by design: `test_physics_statics.py` (P1) and `test_physics_data.py` (P2). With the six patches of 11.5 applied to a copy of this tree, all six files pass in normal, strict and core-only runs. `test_body_engine.py`, `test_camera.py` and `test_camera_actions.py`, failing at B5-MERGE, now pass (C15). All the fix round's test files pass: `test_physics_settle` 44, `test_physics_setup` 29, `test_camera` 90, `test_camera_actions` 58, `test_camera_realism` 50, `test_body_engine` 50, `test_body_core` 39, `test_body_faults` 14, `test_body_perception` 24 |
| G1b | FAIL | isolated domains: 10 failing tests in 7 files, all known from B5-MERGE; kernel files with core only 15/19 (B5-MERGE 14/19): `test_body_model`, `test_physics_data`, `test_physics_forces`, `test_physics_statics`, all covered by the 11.5 patches |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 242/242 (1,210 trials); against B5-MERGE 0 verdict changes, 626 state-digest changes, 48 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (as B5-MERGE); 0 verdict changes; 625 / 243 digest changes; the 250 realistic trials are identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes (the audit now also checks `count` on sounds and that the battery estimates carry no load) |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz: battery, geometric and room, 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | FAIL | 3.07 ms per substep (B5-MERGE 3.10), 1 h wait 1.103 s; run again alone, 3.12 ms and 1.114 s. Because PHYS-INT is in the union, the budget is A0 x 1.0 (1.102 s), not the previous gate x 1.05 that B5-MERGE used (it measured 1.098). See below |
| G8 | PASS | room golden: golden ok, golden_all ok (re-recorded, below) |
| G9 | PASS | physics_audit ok; camera tools ok in G4 / G6; body_audit ok |
| G10 | PASS | 34 changed files in the full run; after the docs and handoffs were copied in (`--only G10`), 42: the 34, `docs/KNOWN_LIMITS.md` and the seven handoffs; 0 outside the union's file list |

**G7.** Interleaved A/B on this machine, one process per measurement, `PYTHONHASHSEED=0`, 10 rounds, best of 3 per seed,
the same four houses. The tree before this round has a median of 1.109 s (min 1.092). The fixed tree has a median of
1.112 s (min 1.098). That is +0.3 % at the median, and both trees are above 1.102 at the median. A cProfile of the four
waits gives equal total own time (4.43 s fixed, 4.44 s before). No function this round added or changed is among the
increases; `settle._arranged`, the only new code on the wait path, takes 0.3 ms over the four waits. Per-system substep
costs are within 0.01 ms of B5-MERGE (largest: `climate.house` +0.008). So this is noise around a fixed bound. A G7 run
on a quiet machine (`--only G7`) decides; nothing in this round's code is left to trim on that path.

**Reviewed differences (3.3).** No verdict changed in G2 or G3. The digest changes are of kinds the union's codes
already allow:
* state: R1 (no `_robot_moved` marker for an open / close-only change), R5 (the laws active at landing, now without
  set-down), B-R2 (battery and energy of trials whose domain verbs move the robot without a route);
* observations: the merged sounds with `count` (P5), the load-free battery estimates (B2), `FAR_WHO` (C13).

**Golden (3.5).** Re-recorded with `{"by": "STAGE-B-FIX", "classes": ["R1", "B-R2"]}`. `tools/golden.py diff` of the
dumps before and after: 274 files compared, 127 changed, 5 path patterns:

* 208 `entities.*._robot_moved` removals: open / close-only touches in the script and the reference plans.
* Two trials whose leaves move the robot without a motion record now pay for the walk (`robot.battery`,
  `battery_min_pct`, `energy_wh_used`, `stats.energy`):
  * T-fa2552570f7b6f-01 (`mow`): 22.22 -> 25.94 Wh.
  * X-1742e6de23d6be-01 (`guide`): 3.84 -> 5.27 Wh.

Nothing else changed.

**Geometric preview (information only, as at B3).** `record --only G3 --camera geometric` (scratch records), all 242
specs with realistic perception and the geometric camera. The tree before this round passes 173/242, this tree 178/242,
with 0 exceptions in 1,210 trials each. 8 specs now pass: A-c0f8c53da99d24-01, T-33ad442208b4b7-01, T-33e06799f951df-01,
T-8181b198c09f12-01, T-89d7a649052266-01, T-92f2736b7f949c-01, X-9cb1ee87e09363-01, X-c77e2cbe7181f0-01.
A-c83efc7adcd8f5-01 (CAM-RENDER's example) now gets past `approach door_3` on seeds 2, 41 and 97 and stops later, at a
look-alike pill bottle. 3 specs now fail, each because the robot now sees what the solid leaf used to hide:
* T-28ff695081cd2c-01, seed 1: from the doorway it now sees the laundry basket and the bucket, so `approach` with
  `avoid` finds no path. It fails the same way with the room camera (forced realistic, since B5-MERGE).
* T-702453ef553c00-01, seed 0: the resident 0.5 m in front of the robot is now seen, backlit by the window
  (`sight.glare`), so not identified, and `ask resident_1` is refused with `FAR_WHO`. Before, she was not seen at all,
  and an unseen person can be named (11.5 item 9).
* T-7ef5816c160945-01, seeds 0 and 2: behind the door the robot opens, the cat is lit at 0.135 (identify needs 0.2), so it is
  recognised, not identified, and `inspect` gives the shape answer (C4).

### 11.5 Still open, and requests to the main session

Each needs a file no stage-B package owns. The patches for the six test files were applied to a copy of the fixed tree
(`request_patches.py` in this round's scratch); that copy is green in normal, strict and core-only runs.

1. **Test pins outside the packages.** These tests pin behaviour that a stage-B package changed on purpose; each
   needs the patch below in its own file.
   * `tests/test_physics_data.py` (PHYS-DATA): `ACTIVE = {"orphan", "overflow", "drip", "creep"}`; `setdown` is
     deferred (P2).
   * `tests/test_physics_statics.py` (PHYS-STATICS): replace
     `test_robot_moved_anywhere_under_the_top_support_counts` with the P1 rule: a sibling the robot moved under the
     same table does not make the glass the robot's; the table's failure under that book (`load=["book"]`) does; a
     marker on the table itself does.
   * `tests/test_body_model.py` (BODY-MODEL): BODY-ENGINE request 1 (the legacy robot record keeps only
     `ROBOT_DEFAULTS`, room, pos, hands and the config's keys).
   * `tests/test_physics_forces.py` (PHYS-DATA): BODY-ENGINE request 2 (the profile is the only source of the force
     ratings).
   * `tests/test_manipulation.py` (manipulation): BODY-CORE request 2 (`receive` of a two-hand bag with a hand full is
     refused at lift-off, "Both hands must be free for this item", the bag stays with Ana).
   * `tests/test_textiles_laundry.py` (textiles_laundry): PHYS-INT request 1 (the jug bound 3.3 kg: bleach at
     1.10 kg/L).
2. **B3, fault writes in domain code.** The fault log is a list of dict records (BODY-ENGINE); a domain that sorts it
   as strings crashes, one that appends a bare string writes a record nothing reads. Replace each with
   `body_faults.add_fault(w, kind, part=..., source="event")` (register the kind with `register_fault_kind` if it is
   new) or `body_faults.log_fault(w, kind, text)`:
   `systems/appliances_cooking/handling.py:73` (`gripper_heat_damage`), `systems/cleaning/dishes.py:73`
   (`gripper_cut`), `systems/manipulation/handling.py:750` (`gripper_damage`) (the three `sorted(set(...))` crashes),
   `systems/food/common.py:162-165` (`end_effector_cut`), `systems/outdoor_plants/pool.py:63-65` (`water_ingress`),
   `systems/outdoor_plants/yard.py:641-643` (`electrical_shock`) (bare strings).
3. **B4, domain verbs that move the robot without a route.** The kernel backstop (`body.unrecorded_motion`) now pays
   and refuses these moves, but as a straight line, without traverse's bookkeeping (`docked`) or camera motion
   frames. The verbs the review found should call `ctx.require_mobile(stairs=...)` and `ctx.motion(path, start,
   connection=cid)` per leg (TIER1_INTEGRATION section 6, a new body item): people `guide` and `offer_arm` walking
   (`people/assist.py`), manipulation support `mode=follow` (`jobs.py`), `drag`, `roll` and `_robot_follow`
   (`motion.py`), outdoor_plants `mow` (`lawn.py`), textiles_laundry unfold step-back (`dry.py`), navigation_robot
   `dock` / `undock` (`robot.py`; NAV-M2's file, but the change belongs with the others).
4. **B7, careful speed on commanded durations.** The kernel half is in (`timing="commanded"`). These domain verbs take
   their duration from an argument and must declare it in their ActionSpec: `hold_steady`, `offer_arm`, `play_game`,
   `knead`, `dry_pet`, `brush_pet`, `blend`, `brew`, `grind`, `rinse`, `spray`, `shake`, `inflate`, `flush`,
   `ignite`, `pair_device`, `pull_handle`, `push_handle`, `squeeze_trigger`, `tilt`, `turn`, `move_lever`,
   `restart_device`.
5. **B9, `tools/body_audit.py`.** A stage-A tool with no stage-B owner. It should take the hands from
   `forces.lift_plan` and the capacity from `body.capacity_kg(..., believed=...)` instead of its own hand rule, and
   report overload refusals, drops and energy per task.
6. **B13, safety stops heard by a deaf robot.** `systems/safety/monitor.py:139` sets `heard_by_robot` from the room
   and `loud` only; it should be `body.hears(state, room, loud=bool(args.get("loud", True)), addressed=True)`.
7. **C9, `camera.watch` cost.** Still 0.078-0.090 ms per substep in generated houses with people (budget 0.05). What
   remains: deciding whether the substep's touches changed the room geometry (`sight` epoch) and the ambient light.
   A geometry epoch that settle bumps only when something in a room moves, and a light epoch bumped by whatever
   changes a lamp or the sun, would let the memo skip both. That is a cross-package change (settle, the light hooks,
   the camera) and was not attempted in a fix round.
8. **Smaller notes.** C3: `inspect` no longer lists unseen things, but `camera.can_see` still accepts
   `wrist_access` for a thing under a light portable support or under furniture within reach (CAM-ACTIONS known gap
   1), so `pick_up` of such a thing by its oracle id works without a peek. Restricting `wrist_access` to revealed
   things broke the fetch skill (its peek frame does not cover side items), so it was left. P3: food still has to
   register `phys.broke` before the set-down law can be activated. Two design docs still describe the old rules:
   TIER1_PHYSICS 6.4 (a marker anywhere in the subtree of the top-level support counts) and the TIER1_EMBODIMENT 4.11
   fault table (one slip draw after each motion carrying 1 kg or more). The design docs are the main session's.
9. **For the camera owner (D3 triage), seen in the geometric preview of 11.4.** Two rules of other packages now
   decide specs that the blind doorway used to hide:
   * `sight.glare` adds the target's own angular radius to the backlight overlap, so a person 0.5 m from the robot is
     "backlit" by a window well off the line of sight and cannot be identified (T-702453ef553c00-01, seed 0).
   * `realism.inbound` refuses a person's id only while they are seen and unidentified; one the camera does not see at
     all can still be named.

### 11.6 How to test

```
python -m unittest tests.test_physics_settle tests.test_physics_setup tests.test_camera tests.test_camera_actions tests.test_camera_realism
python -m unittest tests.test_body_engine tests.test_body_core tests.test_body_faults tests.test_body_perception
HOUSE_SIM_STRICT=1 python -m unittest <the same>
HOUSE_SIM_DOMAINS=core python -m unittest <the same>
python -m unittest tests.test_room_golden; HOUSE_SIM_GOLDEN_ALL=1 python -m unittest tests.test_room_golden
python tools/perception_audit.py 6 150 --workers 4 --battery 5 --faults random [--camera geometric]
python tools/orchestration/tier1_gate.py compare B5-MERGE STAGE-B-FIX --package <packages>
```

The reviewers' probes are the quickest way to see each fix: the door / room coverage table of C1 (seeds 0-5, open vs
closed), the box rim of C2, the cushion coin of C3, the basket of C5, the 6.5 kg pot on 5 % and on a stool (B1), the
guide upstairs with the legs failed (B4).
