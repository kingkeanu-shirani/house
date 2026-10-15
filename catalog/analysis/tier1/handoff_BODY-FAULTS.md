# Handoff BODY-FAULTS (stage B5, merges after BODY-CORE): the robot's sensors, faults and proprioception in perception

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.1, 1.3, 1.5, 1.9, 1.11-1.12, 1.15 and conflicts
6-9, 20-22, 39; the BODY-FAULTS row of 2.4; 2.2; 3; 3.5; 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_EMBODIMENT.md` (whole; this package is KB-4 integration: 4.10-4.14, 5.2, 5.3, 6, 8.2, 10 items 25-34).
Handoff files read (all that existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`, `handoff_PHYS-DATA.md`,
`handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md`,
`handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md` and `handoff_CAM-RENDER.md` (with their B3 merge
sections), `handoff_BODY-ENGINE.md` and `handoff_NAV-M2.md` (with their B4 merge sections), and `handoff_BODY-CORE.md` (the
parallel B5 package, read in full when it handed over, before the golden re-record). Their follow-ups addressed to
BODY-FAULTS are acted on: BODY-MODEL 3 / 9 (`perception.observe` takes `obs.robot` / `obs.battery` from `proprio.render`;
the imaging / power gates read `body.imaging`, `body.depth_ok`, `body.is_on`; messages filtered by `body.can_hear`;
realism clarity from `body.clarity`), CAM-RUNTIME 9 (room-mode gates; `obs.messages` filtered), CAM-RENDER 9 (realistic
`obs.camera.pose.cell` mirrors `obs.robot.pos`; the audit's camera checks run in both camera modes), BODY-ENGINE 9
(realistic `obs.robot.battery` is the integer gauge; the power-off and privacy gates), NAV-M2 5 (realistic gauge in
`obs.robot`; `obs.nav` stays the adoption round's), BODY-CORE 5 / 9 (the golden re-recorded once with its B-R11 classes;
`obs.messages` is the observation side of its `listen` / `ask` hearing rules).

## 1. Summary

* `perception.py` applies the robot's sensors (TIER1_INTEGRATION 1.3) in both perception modes and both camera modes:
  powered down only held / worn things are visible (no people, pets, hazards, weather, sounds, messages, observer
  channels); imaging off (privacy mode, cameras off or failed, a room privacy policy) is the darkness rule over the
  whole room in room mode, map fixtures stay targetable without being listed; hearing filters `obs.messages` (none /
  addressed only) and drops every sounds channel when deaf; `obs.dark` is true whenever the cameras give no colour view.
* `obs.robot` and `obs.battery` come from `proprio.render`: oracle = today's keys plus every registered visible robot
  field, exact; realistic = the robot's own report (integer gauge equal to `obs.battery`, estimates, buckets, detected
  faults only, `pos` None while unlocalized and `obs.camera.pose.cell` with it). Realism reads the lens clarity from the
  body and treats imaging-off rooms as dark (dark-spell shapes, no hazard avoidance from sight).
* Perception audit kind `robot` (proprioception tiers, latent faults, hidden fields in channels, the sensor gates);
  `--battery PCT --faults random` on `tools/fuzz.py` (the gate's G6 variant) and `tools/perception_audit.py`
  (`body_faults.sample_fault_events`). 32 tests (`test_body_perception.py` 21, `test_body_faults.py` 11), golden re-recorded
  once for both B5 packages, docs `PERCEPTION.md` ("Proprioception and the robot's sensors"), `EVAL_HARNESS.md`
  ("Power-down steps").

## 2. Files

Created: `tests/test_body_faults.py`, `tests/test_body_perception.py`, this handoff, the gate record
`catalog/analysis/tier1/gate/BODY-FAULTS/`.
Edited: `house_sim/kernel/perception.py`, `house_sim/kernel/realism.py`, `house_sim/kernel/body_faults.py`,
`house_sim/kernel/proprio.py`, `tools/perception_audit.py`, `tools/fuzz.py`, `tests/data/room_golden.json` (re-recorded for
both B5 packages), `docs/PERCEPTION.md`, `docs/EVAL_HARNESS.md`.
All on the BODY-FAULTS list of TIER1_INTEGRATION 2.4. Not changed although listed: `tests/test_perception.py` (green
unchanged: no test pins realistic `obs.robot`). All files LF.

## 3. APIs

"ADDITION" = not in TIER1_EMBODIMENT 5 / TIER1_INTEGRATION 1; **DEVIATION** = differs from them (section 4).

**`house_sim/kernel/perception.py`**

```
SHAPE_M = 0.4; DIRTY_LENS = 0.4; SOUND_CHANNELS = ("sounds", "mechanism_sounds")            ADDITIONS (constants)
ROBOT_KEYS = proprio.LEGACY_KEYS + ("facing_deg",)    kept for tools; obs.robot is proprio.render's now
heard_messages(state, hearing=None) -> [messages]     ADDITION: body.can_hear filter ("none" [], "addressed" to == "robot"
                                                      or addressed_robot, "full" all); the caller keeps the heard_by_robot
                                                      / step window
drop_sounds(obs)                                      ADDITION: removes sounds, mechanism_sounds and a channel's own "sounds"
Perception.senses(state) -> (powered, imaging)        ADDITION (body.is_on, body.imaging; fixed while one observe() runs)
Perception.lens(state) -> clarity                     ADDITION (body.clarity; fixed while one observe() runs)
Perception.seen_dark(state, rid) -> bool              ADDITION: imaging off, or the room unlit without a robot light
Perception.visible(state, eid)                        room mode: held -> powered (else only location "robot") -> doors,
                                                      near-connection controls -> containment -> imaging off: >= 0.4 m or
                                                      a map fixture of the room / imaging on: darkness and lens (body
                                                      clarity) as before; geometric: camera.can_see (unchanged)
Perception.observe(env, records=True)                 obs.robot, obs.battery = proprio.render(env, state, not records);
                                                      obs.dark includes imaging off; geometric obs.camera.pose.cell =
                                                      obs.robot.pos; weather needs power (and imaging through a window);
                                                      powered down: no people / pets / observers; messages through
                                                      heard_messages; drop_sounds when hearing is "none"
```

**`house_sim/kernel/realism.py`**

```
BLIND_WHO = "With the cameras off it is too dark to tell who is who: address the shape you see"   ADDITION
_lens(state) -> body.clarity(state)                   the lens term of room_context / _geo_global_m / the context keys
Realism._dark_agents / route_hazards                  dark = Perception.seen_dark (imaging off counts as dark)
Realism.inbound                                       naming a shape by id: DARK_WHO, or BLIND_WHO with imaging off
```

**`house_sim/kernel/proprio.py`** (fixes)

```
REALISTIC_KEYS                                        ADDITION: the keys the realistic view renders itself (audit)
SHOWN_LOG_EVENTS = (None, "logged", "fall")           realistic faults also list the robot's falls   DEVIATION 7
_copy(value)                                          deep-copies only dicts and lists (oracle render 0.013 ms)
```

**`house_sim/kernel/body_faults.py`** (fixes and additions)

```
sample_fault_events(rng, count=3, horizon_s=3600.0, kinds=None) -> [event dicts]   ADDITION (fuzz / audit faults)
SAMPLE_SEVERITY                                       ADDITION: the severity range per kind of the random events
normalize_faults(robot, now=0.0)                      a setup record without "kind" is named by its id ("network_down",
                                                      "arm_weak_left" -> kind arm_weak, part left)   ADDITION
```

**`tools/perception_audit.py`**: leak kind `robot`, `check_robot_obs(env, obs, leaks, where)` (called by `check_obs`),
`SOUND_KEYS_OBS`, options `--battery PCT --faults random|none` (through `tools.fuzz.robot_options`).
**`tools/fuzz.py`**: options `--battery PCT --faults random|none` (default perception `mixed` with them),
`robot_options(config, seed, battery=None, faults=None)`, `BATTERY_SHARES = (1.0, 0.25, 0.05)`, `FAULT_HORIZON_S = 1800`,
`replay(seed, opts, log, camera=None, perception=None, battery=None, faults=None)`; replay files carry both options.

## 4. Deviations from the spec

Each is the more realistic or the simpler option.

1. **`obs.dark` means "no colour view"**: true in a dark room without a robot light (as before) and whenever imaging is
   off, in both camera modes (1.3 says room mode applies the darkness rule to the whole room). Every observer that keys
   visual percepts on `obs.dark` (people gestures and poses, cleaning soil, navigation hazards, laundry looks) then
   suppresses them with the cameras off, and realism treats the room as dark (dark-spell shapes for people and pets,
   `BLIND_WHO` for naming one, no hazard avoidance from sight; `Perception.seen_dark`). In geometric mode imaging-off
   people were keyed per sighting (CAM-RENDER); they are now keyed per dark spell like in a dark room (both are
   unlinkable; the camera's depth-shape rendering of things is unchanged).
2. **Map fixtures in room mode with imaging off**: 1.3 lists "map fixtures stay targetable by actions" among the rules
   identical in both camera modes; room mode applies it as the camera's `map_fixture` does (non-portable things standing
   in the room, doors): `visible()` is True for them, the observation lists only those of 0.4 m and up (as shapes).
3. **Powered down, in both perception modes**: only what the robot holds or wears (camera's `_held`: hands or location
   "robot") is visible or targetable; no people, pets, hazards, weather or lidar, and no observer channel at all (no
   sensor feeds one: sounds, smells, thermal, domain channels). The robot's map (room record, exits, connections,
   blocked cells), its proprioception, `instruction` and `last_answer` stay. The oracle affordance probe on a non-held
   thing then raises "That is not visible here" (BODY-ENGINE's test pinned the probe's PowerOff reasons: request 1).
4. **Weather** is seen through a window only with imaging on and felt outdoors only while powered (1.3 lists no
   weather rule; a window view is a colour percept).
5. **Hearing "none"** removes the sounds channels (an absent channel is what a silent room gives) and empties
   `obs.messages`; `obs.robot.hearing` "off" tells the agent why. "Addressed" keeps the sounds (people's follow-up
   suppresses ambient speech content in them).
6. **Clarity** is read through `body.clarity` (the value `body.sensors_view` returns as "clarity"), not the whole
   `sensors_view` (which evaluates hearing policies and lights the clarity does not need); `perception.visible` / `redact`
   use it too, so lens scratches hide small things and text as dirt does.
7. **Realistic `obs.robot.faults`** lists the detected fault records and the robot's own fault-log entries: its notes
   and `log_fault` entries (founded or not: what it wrote), legacy domain entries (kernel test C8) and its falls (it felt
   them; `body_faults.fall` logs event "fall"). 4.14 says "detected faults only"; nothing latent is shown.
8. **Oracle `obs.robot`** adds every registered non-hidden field present in the state, materialized or not (`room`,
   `dock_powered`, `home`, `camera_on`, `facing_deg`, ...), exactly as 4.14 says ("today's keys plus every registered
   non-hidden field"); B-R1's "new materialized robot keys in oracle `obs.robot`" is this.
9. **`tools/fuzz.py --battery PCT`** starts houses at PCT x (1, 1/4, 1/20) % by seed (even seeds undocked), because at a
   flat 5 % no house powered down within 300 commands (measured: 0 of 6); with the shares 4 of 6 do, odd seeds also
   charge and wake. `--battery` / `--faults` default to `--perception mixed` so the realistic proprioception and gates
   are fuzzed too. `--faults random` schedules three `robot.fault` events (first at 0 s, then within 30 min) and some
   clears (`body_faults.sample_fault_events`, a shared helper).
10. **`tools/perception_audit.py`** also takes `--battery` / `--faults` (same rules) so its robot checks see
    powered-down, deaf, blind and faulty robots; the gate runs it without them, as before.
11. **`normalize_faults`** accepts setup records named by their fault id without a "kind" (convenience for authors and
    the setup op); a sided kind without a side stays a ValueError at reset.

## 5. Stubs and contracts

* No `TIER1-STUB` marker added or left (`grep -rn "TIER1-STUB" house_sim --include=*.py`: stage C's only).
* Consumed: BODY-MODEL's body / proprio / body_faults library (`is_on`, `imaging`, `depth_ok`, `can_hear`, `clarity`,
  `ROBOT_FIELDS`, the fault records and log), BODY-ENGINE's registration (`robot.fault` / `robot.clear_fault` events,
  the materialized fields), BODY-CORE's `say()` `audible`, `look` relocalization (`body.relocalize`, tested when present)
  and setup ops, CAM-RUNTIME / CAM-RENDER's geometric gates (`camera.can_see`, the step view, `camera_block`), PHYS-INT's
  `impact.fall` (drops) and `phys.fell` hook.
* For later packages: section 9.

## 6. Gate results

Gate id **BODY-FAULTS**, package **BODY-FAULTS**, previous gate **BODY-CORE** (this package merges second: its gate runs on
the merged B5 tree and compares with BODY-CORE's), record in `catalog/analysis/tier1/gate/BODY-FAULTS/`. Taken on an
**isolated copy**: the base commit is the shared tree as B4 handed it over (snapshotted before either B5 package's first
edit), with BODY-CORE's and NAV-M5's files as handed over and this package's files on top; identical to the project tree
at hand-over (every tracked and untracked file compared, line endings normalized). G10 runs with `--shared-tree`, which
attributes BODY-CORE's / NAV-M5's files to them.

```
python tools/orchestration/tier1_gate.py --root <scratch>/b5merged --gates-root catalog/analysis/tier1/gate record
       --id BODY-FAULTS --package BODY-FAULTS --shared-tree --prev catalog/analysis/tier1/gate/BODY-CORE
       --out catalog/analysis/tier1/gate/BODY-FAULTS --work <scratch>/gate_work_final --jobs 7
```

Summary line: `TIER1-GATE BODY-FAULTS [BODY-FAULTS] vs BODY-CORE: FAIL (10 pass, 2 fail, 1 skip; this run:
G1,G1b,G8,G10)`. `compare` passes; the two FAILs (G1, G1b) are pinned tests in files outside this package's list
(request 1 and the earlier packages' open requests):

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 482f9adf9717 unchanged (425 actions, 111 systems) |
| G1 | FAIL | 45 files, 3,541 tests (normal); `test_body_faults.py` (11) and `test_body_perception.py` (21) green normal and strict; 14 failing runs (7 files x normal + strict). This package's: `test_body_engine.py` `TruncationTests.test_a_walk_the_battery_cannot_finish_stops_at_the_prefix_and_strands_the_robot`, whose last three lines pin the oracle affordance probe of a powered-down robot (now "That is not visible here": nothing is visible but what it holds, 1.3; request 1, patch verified). Known: `test_body_model.py`, `test_physics_forces.py`, `test_camera_actions.py` `FaceTests` (BODY-ENGINE requests 1-3), `test_camera.py` (CAM-RENDER), `test_textiles_laundry.py` (PHYS-INT R7), `test_camera_actions.py` `LookTests` and `test_manipulation.py` `HandOverTests` (BODY-CORE requests 1-2). `test_room_golden.py` is green again (re-recorded) |
| G1b | FAIL | isolated domains: the 10 failing tests known from BODY-CORE, none new; kernel files core-only 14/19: the four files pinned since B4-MERGE plus `test_body_engine.py` (request 1); `test_body_faults.py` / `test_body_perception.py` green core-only |
| G1c | SKIP | no import-inert modules for this package |
| G2 | PASS | own mode 242/242 (1,210 trials); vs BODY-CORE 0 verdict, 0 state-digest, 1,210 observation-digest changes (allowed: obs) |
| G3 | PASS | forced realistic 226/242 (as BODY-CORE); 0 verdict, 0 state-digest, 1,210 observation-digest changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit room and geometric (now with the `robot` checks): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and **battery** (new: `--battery 5 --faults random`, mixed perception): 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.04 ms per substep (BODY-CORE 3.03, limit 3.181), 1 h wait 1.086 s (1.095, limit 1.149); `kernel.body` 0.023 ms |
| G8 | PASS | room golden and golden_all (re-recorded by this package for both B5 packages, below) |
| G9 | PASS | `body_audit --baseline`: ok |
| G10 | PASS | 23 changed files: this package's 12 (with this handoff), 11 attributed to BODY-CORE / NAV-M5, 0 outside |

`python tools/orchestration/tier1_gate.py compare BODY-CORE BODY-FAULTS --package BODY-FAULTS`:

```
compare BODY-CORE -> BODY-FAULTS (packages: BODY-FAULTS; allowed: obs; codes: B-R12,B-R13,B-R14,B-R15)
G0   PASS  action fingerprint unchanged (482f9adf9717)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 1210 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 1210 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 375 -> 375
G7   PASS  substep_ms: 3.03 -> 3.04 (+0.3%; limit 3.181 = prev x 1.05)
           wait_1h_s: 1.0946 -> 1.0856 (-0.8%; limit 1.149 = prev x 1.05)
G1   INFO  new test file test_body_faults.py (normal): 11 tests
           new test file test_body_faults.py (strict): 11 tests
           new test file test_body_perception.py (normal): 21 tests
           new test file test_body_perception.py (strict): 21 tests
compare result: PASS
```

**After the record**: `tests/test_body_faults.py`'s hook recorder was changed from a `mock.patch.dict` of `HOOKS` to
append / remove (found running the body test files and the golden in one process: request 1), a test-only change; the
record was then refreshed with `--only G1,G1b,G8,G10` on the final tree (same command plus `--only`); the other checks
are the full run's.

**Attribution without BODY-CORE.** Before BODY-CORE handed over, G2 and G3 were also run on the B4 tree with only this
package's files (`record --id BF-try --package BODY-FAULTS --prev B4-MERGE --only G2,G3`, scratch record): 0 verdict, 0
state-digest and 1,210 observation-digest changes in both modes, `compare B4-MERGE` PASS. So this package changes
observations only, with or without BODY-CORE.

### Reviewed differences (3.3)

No verdict and no state digest changed in either mode, so no row is needed. Every observation change was classified:

| Code | Catalog ids | Mode | What changed |
|---|---|---|---|
| B-R12 | all specs (1,210 trials) | both | oracle `obs.robot` gains the registered visible robot fields (`power_state`, `standby`, `charging`, `battery_temp_c`, `arm_health`, `arms_ok`, `legs_health`, `sensors`, `fault_state`, `localized`, `actuator_ok`, `grippers`, `hand_soil`, the ratings, `room`, `home`, `dock_powered`, ...: TIER1_EMBODIMENT 4.14, the B4 keys); realistic `obs.robot` becomes the proprioception view (integer `battery`, `battery_state`, `runtime_min_est`, `range_m_est`, `battery_temp`, `camera`, `hearing`, `network` words, `arms`, `legs`, `load`, `grip`, detected `faults`; no `camera_clean`) and realistic `obs.battery` the integer gauge |
| B-R12 | 20 realistic trials of seed 0 (A-a67431128da2e1-01, T-03d77bbfc281a4-01, T-5d2d79cc6ec2ca-01, T-686df131c7c736-01, T-7e27e4a6a3b1e5-01, T-870b0185206ce6-01, ...) | realistic | `obs.nav.dock` shows the home dock's handle instead of "something": realistic `obs.robot.home` (an exact-tier field: the robot knows its own dock) issues the handle, which the navigation channel then uses. The only path outside `obs.robot` / `obs.battery` that changed (checked over the final observations of all 242 reference plans, seed 0, both modes, BODY-CORE tree vs merged tree) |
| B-R13 | (none in reference runs) | both | hearing filters: no authored spec uses privacy mode, a microphone switch or fault, or a mic policy |
| B-R14 | (none in reference runs) | both | privacy-mode / camera-off / power-off visibility: no reference run turns imaging off or powers down (BODY-CORE: lowest battery 17.69 %) |
| B-R15 | golden | - | re-recorded once for both B5 packages (below) |

### Golden (3.5; B-R15, with BODY-CORE's B-R11 classes)

Dumped (`tools/golden.py dump --all`) on the B4 tree before the first edit (`golden_before`), on B4 + BODY-CORE + NAV-M5
(`golden_core`) and on the merged tree at hand-over (`golden_after`):

```
before -> core   274 file(s) compared, 21 changed, 129 path pattern(s)    BODY-CORE's classes, exactly as its handoff
                 section 6 lists them (B-R9 careful timing, B-R7 paper grasp and towed cart, B-R8 messages.*.audible:
                 entities.*.temperature_c 1152, action_log.*.time_s 238, ..., messages.*.audible 17, robot.battery 11)
core -> after    274 file(s) compared, 16 changed, 39 path pattern(s)     this package: the 8 scripted runs' reset and
                 final observations only, no spec state
                   16  obs.robot.actuator_ok, arms_ok, battery_wh, body_profile, charging, grippers, hand_soil,
                       headlamp_on, height_m, home, localized, mass_kg, max_hand_kg, max_mass_kg, power_state, reach_m,
                       room, standby, width_m                                    (B-R12, oracle and realistic)
                    8  obs.robot.arm_health, battery_temp_c, dock_powered, fault_state, legs_health, sensors   (oracle)
                    8  obs.robot.arms, battery_state, battery_temp, camera, grip, hearing, legs, load, range_m_est,
                       runtime_min_est, network (true -> "connected"), camera_clean (removed)               (realistic)
                    4  obs.battery, obs.robot.battery   (realistic: 98.774 -> 99)
```

Recorded with `python tools/golden.py record --by BODY-FAULTS --classes B-R7,B-R8,B-R9,B-R12` (`history`:
`{"by": "BODY-FAULTS", "classes": ["B-R7", "B-R8", "B-R9", "B-R12"]}`); `tests.test_room_golden` passes with and without
`HOUSE_SIM_GOLDEN_ALL=1` on the merged tree.

### Benchmarks against 3.4 (this machine, best of 5)

| Measure | Budget | BODY-FAULTS |
|---|---|---|
| `proprio.render` per observation (seeds 0 / 7, generated houses) | <= 0.05 ms | oracle 0.013 ms, realistic 0.020 ms (`test_proprioception_stays_within_three_times_its_budget` fails above 0.15 ms) |
| `observe()` whole (seed 0 / 7; B4 tree vs this package) | - | oracle 2.85 -> 2.94 ms / 2.32 -> 2.40 ms (+3 %); realistic 6.59 -> 6.77 ms / 5.18 -> 5.36 ms (+3 %): the larger oracle robot record and the realistic translation of the proprioception block |
| whole engine (G7) | <= BODY-CORE x 1.05 | 3.04 ms per substep, 1.086 s per 1 h wait (+0.3 % / -0.8 %) |

The body system per substep, settlement and the motion prefix are untouched (BODY-ENGINE's / BODY-CORE's numbers).

### Checks beyond the gate (merged tree)

* `tools/perception_audit.py 6 150 --battery 5 --faults random`, room and `--camera geometric`: 0 leaks, 0 crashes each
  (900 steps; powered-down, deaf, blind, unlocalized and faulty robots audited with the `robot` checks).
* `tools/fuzz.py 6 300 --first 6 --battery 5 --faults random` (seeds 6-11): 1,800 commands, 0 defects, 0 nondeterministic.
* On the B4 tree with only this package (before BODY-CORE handed over): what the battery fuzz reaches (seeds 0-5,
  realistic, a scratch probe of the fuzzer's own commands): 4 of 6 houses power down (battery cuts, "Powered down
  partway", 664 steps observed while off, every realistic `obs.robot.battery` an integer), faults of 8 kinds start and
  some clear; `tools/fuzz.py 6 300 --battery 5 --faults random` room (oracle) and `--camera geometric` (mixed): 0 defects,
  deterministic; `perception_audit.py` with and without `--battery 5 --faults random`, both camera modes: 0 leaks.
* `tests/test_body_core.py` (BODY-CORE's 35 tests) green on the merged tree normal, strict and core-only.

## 7. Known gaps

* **Room fields stay while powered down** (`obs.room`: temperature bucket, light word, the map's exits and cells): the
  room record is the robot's map plus its own room sensors; only the map part is strictly known while off.
* **Domain channels with exact self-state**: navigation_robot's `obs.nav` (`battery_pct` to 2 decimals, `camera_clean`,
  `robot_spot`) are the domain's (adoption round: `obs.nav` through proprio buckets). The audit's integer-battery check
  covers `obs.robot.battery` and `obs.battery` only.
* **Observers that do not key on `obs.dark`** still add their visual percepts with the cameras off (in either camera
  mode); the ones that do (people, cleaning, navigation hazards, laundry) suppress them. Adoption round: gate visual
  cues on `body.imaging` / `ctx.in_frame`.
* **Hearing "none" empties network messages too** (a `"phone"` / `"video"` call to the robot), as 1.3 fixes it; a real
  robot's network link would not need its microphones. "Addressed" relies on speech being marked `to: "robot"` or
  `addressed_robot` (people follow-up).
* **Proprio estimates** use the awake idle power (`runtime_min_est`) and the normal walking cost at the current load
  (`range_m_est`); standby, carried-load changes and stairs are not forecast.
* **The oracle affordance probe** of a powered-down robot raises "That is not visible here" for anything it does not
  hold (deviation 3, request 1).
* Not in this package: navigation_robot's `log_fault` / `clean_camera` / `run_diagnostics` / falls through `body_faults`
  (adoption round), people marking addressed speech, the viewer's body markers (B-CLOSE).

## 8. How to test

```
python -m unittest tests.test_body_perception tests.test_body_faults        # 32 tests, about 25 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_body_perception tests.test_body_faults
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_body_perception tests.test_body_faults
python -m unittest tests.test_perception tests.test_camera_realism tests.test_kernel tests.test_body_core
python -m unittest tests.test_room_golden; HOUSE_SIM_GOLDEN_ALL=1 python -m unittest tests.test_room_golden
python tools/perception_audit.py 6 150 --workers 4 --battery 5 --faults random [--camera geometric]
python tools/fuzz.py 6 300 --workers 4 --battery 5 --faults random
python tools/orchestration/tier1_gate.py compare BODY-CORE BODY-FAULTS --package BODY-FAULTS
```

Manual checks (the lab of `tests/test_body_perception.config`):

```python
from tests.test_body_perception import make
make({"privacy_mode": True}, perception="realistic").observe()      # dark: true; crate a dark shape; Ann a "person"
make({"battery": 0.0}, perception="realistic").observe()["visible_entities"]   # only the held jar
env = make(perception="realistic"); env.observe()["robot"]          # battery 100 (int), camera "clear", hearing "full"
make({"mic_on": False}).observe().get("messages")                   # []
```

## 9. Follow-ups

Requests to the main session (files outside BODY-FAULTS):

1. `tests/test_body_engine.py` (BODY-ENGINE's): `TruncationTests.test_a_walk_the_battery_cannot_finish_stops_at_the_prefix_
   and_strands_the_robot` probes `env.affordances("hall_light")` on a powered-down robot and expects PowerOff reasons; with
   the power gate (TIER1_INTEGRATION 1.3: only held items are visible) the probe raises "That is not visible here", as it
   does in geometric mode since CAM-RUNTIME. Patch (verified normal, strict and core-only on the merged tree):

   ```python
   from house_sim.kernel.errors import ActionError                     # with the other imports
   ...
           self.assertEqual(robot(env)["battery"], 0.0)                    # nothing drains while off
           with self.assertRaises(ActionError) as caught:                 # powered down the robot senses nothing
               env.affordances("hall_light")                               # (BODY-FAULTS, TIER1_INTEGRATION 1.3)
           self.assertEqual(str(caught.exception), "That is not visible here")
   ```
   (replacing the three lines from `probes = env.affordances("hall_light")`). Second, in the same file: run
   `load_domains()` at import (`from house_sim.kernel.plugins import ..., load_domains` and a module-level
   `load_domains()` before the constants). Its `BodyTestCase.hooks()` (and one test's `AFTER` patch) swap plugin tables
   with `mock.patch.dict`, which restores the whole table; when such a test is the first in the process to build an env,
   the domains load inside the patch and their hook registrations are dropped at the restore, so any later test in the
   same process sees a house without them (`python -m unittest tests.test_body_engine tests.test_room_golden` fails the
   golden on every run's reset digest; one process per file, as G1 runs them, is fine). This package's
   `tests/test_body_faults.py` had the same pattern and now appends and removes its recorders instead. Both changes
   together were verified on a copy of the merged tree: `test_body_engine`
   normal, strict and core-only green, and `test_body_engine` + `test_room_golden` in one process green.
2. `docs/KERNEL_API.md` "v3.0 robot body" (no B5 owner): one paragraph pointing to docs/PERCEPTION.md "Proprioception
   and the robot's sensors" (`perception.heard_messages`, `drop_sounds`, `Perception.senses` / `lens` / `seen_dark`,
   `obs.dark` with imaging off, `body_faults.sample_fault_events`, `realism.BLIND_WHO`).
3. Still open from earlier packages (unchanged here): BODY-ENGINE requests 1-4, BODY-CORE requests 1-2 and 4-6, CAM-RENDER
   request 1, PHYS-INT request 1, NAV-M2 request 1.

For later packages:

* **B-CLOSE (B6)**: the viewer's `pw` / `bs` / `ft` frame keys can read realistic `obs.robot.power_state`,
  `battery_state` and `faults` directly; `docs/DATASET.md` format bump: realistic `obs.robot` keys changed and `obs.battery`
  is an integer equal to `obs.robot.battery` (the compact mode's duplicate rule now drops it in realistic records).
* **PEOPLE-IO (C)**: `perception.py` is yours next; keep `heard_messages` for `obs.messages` and the notices
  (`addressed_to_robot` in core is the same rule); `perceived_ids` while powered down is the held items; the LLM prompt
  (`eval/llm.py`) can name `obs.robot.battery_state`, `runtime_min_est`, `range_m_est`, `camera`, `hearing` and the
  power-down rule of docs/EVAL_HARNESS.md.
* **TIME-EVAL (C)**: audio notices follow `body.can_hear` (none: no notices; addressed: only addressed speech), visual
  notices need `body.imaging`; `tools/fuzz.py --battery/--faults` replays carry both options.

Domain follow-ups (adoption round, TIER1_INTEGRATION 6), new or sharpened by this package:

* **navigation_robot**: `obs.nav` through proprio buckets (`battery_pct` integer gauge or a bucket, `camera_clean` the
  `obs.robot.camera` word, nothing hidden); `log_fault` / `clean_camera` / falls through `body_faults`.
* **people**: mark speech to the robot `addressed_robot`; suppress ambient speech content in the sounds channel when
  `body.can_hear` is "addressed".
* **every domain with visual observer percepts**: return early on `obs.get("dark")` (cameras off or dark) as people,
  cleaning, navigation and laundry do, or gate on `body.imaging(state)`.

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
