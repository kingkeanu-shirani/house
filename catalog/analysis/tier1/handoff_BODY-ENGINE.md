# Handoff BODY-ENGINE (stage B4): the robot body wired into the engine

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.1-1.10, 1.15, 1.16 and conflicts 1-3, 5-6, 10,
11, 13, 16, 30, 32, 34, 43; the B4 row of 2.4; 3; 5), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_EMBODIMENT.md`
(whole; this package is M1 + M2: KB-2 and the M1/M2 parts of KB-3, test plan items 10-18). Handoff files read (all that
existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`,
`handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`,
`handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`. Their follow-ups addressed to BODY-ENGINE
are acted on: BODY-MODEL 3 / 9 (normalize through `apply_profile`, the registrations, `power_guard` / `quiet_guard` /
`settle_energy` per 1.9, `exc.apply(env)` after the rollback and never on the partial-commit path, `ctx.motion` /
`cut_motion`, `ctx.require_*` from the problem tuples, `require_grasp` with the size grant and `GRASP_AUTO`,
`robot_caps` delegation, measuring settlement with real physics), PHYS-DATA 9 and PHYS-STATICS 9 (`FALLBACK_CAPS` and
the `support.CARRY` stub), PHYS-INT 5 (`ctx.motion`, `_power_down_step`, `robot_caps`), CAM-RUNTIME 9 (the
`robot_keep_awake` consumer), CAM-ACTIONS request 3 (the camera verbs' paragraph in `docs/KERNEL_API.md`). NAV-M2 ran
in parallel on the navigation_robot files; the gate below is the union of both packages.

## 1. Summary

* The kernel body is live: `schema.normalize` materializes the robot through `body.apply_profile`, the engine registers
  the body once (`body.register_plugins()`: system `kernel.body` 72, invariant `robot_stranded`, event effects
  `robot.battery` / `robot.network` / `robot.fault` / `robot.clear_fault`) and follows every BODY-ENGINE line of
  TIER1_INTEGRATION 1.9: power-off and quiet guards, per-leaf energy settlement before the physics settle, idle /
  covered advance modes, the partial-commit rule, `BodyRefusal.apply` after a rollback, `robot_sound`.
* `ActionSpec` / `@action` gain `energy=None` (default), `arms`, `idle`, `power_off_ok`, `noise_db`, `timing` (not in the
  action fingerprint); `wait` is `idle` and `power_off_ok`. Ctx gains `ctx.body` and the `require_*` / `check_lift` /
  `motion` / `cut_motion` / `nested_s` helpers; `Ctx.obj` and `free_hand` read the body.
* Core `move` / `approach` / `walk_to` guard with `ctx.require_mobile` (today's wording) and walk with `ctx.motion`
  (a battery cut stops at the affordable prefix, ok=True); posture verbs add the body's guards. Physics reads the body
  (`forces.robot_caps`, `support.carry_geometry`).
* 46 tests in `tests/test_body_engine.py` (normal, strict, core-only); golden re-recorded (B-R1, B-R2); docs
  `KERNEL_API.md` ("v3.0 robot body", plus the camera verbs' paragraph CAM-ACTIONS asked for), `CONVENTIONS.md`
  (section 7), `KNOWN_LIMITS.md`.

## 2. Files

Created: `tests/test_body_engine.py`, this handoff, the gate record `catalog/analysis/tier1/gate/BODY-ENGINE/`.
Edited: `house_sim/kernel/engine.py`, `house_sim/kernel/actions.py`, `house_sim/kernel/schema.py`,
`house_sim/kernel/timeline.py`, `house_sim/kernel/body.py`, `house_sim/kernel/body_faults.py`,
`house_sim/kernel/phys/forces.py`, `house_sim/kernel/phys/support.py`, `house_sim/systems/core.py`,
`tests/data/room_golden.json`, `docs/KERNEL_API.md`, `docs/CONVENTIONS.md`, `docs/KNOWN_LIMITS.md`.
All on the BODY-ENGINE list of TIER1_INTEGRATION 2.4. Not changed although listed: `house_sim/data/robot/profiles.json`
(no fix was needed), `tests/test_kernel.py` (green unchanged: 44 tests). All files written LF.

## 3. APIs

"ADDITION" = not in TIER1_EMBODIMENT 5 / TIER1_INTEGRATION 1; **DEVIATION** = differs from them (section 4).

**`house_sim/kernel/actions.py`**

```
action(name, ..., energy=None, arms=None, idle=False, power_off_ok=False, noise_db=None, timing="fixed")
ActionSpec(name, fn, params, duration, energy, noise, category, reversible, inverse, stopped_ok, doc, irreversible,
           arms=None, idle=False, power_off_ok=False, noise_db=None, timing="fixed")   # keywords last: positional
                                                       # construction (test_kernel) unchanged; bad values ValueError
ActionSpec.describe()["noise_db"]                      # only when set; "depends on arguments" for fn(ctx)
TIMINGS = ("fixed", "speed")
class BodyView                                         # ctx.body: STATE_QUERIES / WORLD_QUERIES of body.py bound to
                                                       # the live state; anything else AttributeError (read-only)
Ctx.nested_s                                           # seconds of nested leaves (engine.perform adds them)
Ctx.body; Ctx.require_powered(); Ctx.require_mobile(legacy_message=None, stairs=False); Ctx.require_arm(hand=None)
Ctx.require_imaging(message=None)                      # PowerOff / CAMERA_FAULT_TEXT (capability) / IMAGING_OFF_TEXT
                                                       # or message (perception)
Ctx.require_hearing(addressed_ok=True) -> "full" | "addressed"   # MIC_OFF_TEXT / MIC_FAULT_TEXT (capability);
                                                       # ADDITION: addressed_ok=False refuses MIC_PRIVACY_TEXT /
                                                       # MIC_POLICY_TEXT (perception)
Ctx.require_grasp(eid, hand, grip) -> grip             # BodyRefusal with the touch grant on size_m; GRASP_AUTO note
                                                       # and seconds for an automatic pinch / edges grasp
Ctx.require_precision(hand, tolerance_mm, what="")     # PRECISION_TEXT "Cannot position precisely enough for {what
                                                       # or 'that'} right now" (capability)
Ctx.check_lift(eid, hands, mass=None)                  # body.check_lift
Ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None) -> walked cells   # body.motion
Ctx.cut_motion(meters_done, reason, stairs_done=(0, 0)) -> record | None                         # body.cut_motion
Ctx.obj(touch/grasp)                                   # body.manip_problem (today's wording) -> reach -> permissions
Ctx.free_hand(prefer=None)                             # body.arm_health(state, hand) > 0
constants IMAGING_OFF_TEXT, CAMERA_FAULT_TEXT, MIC_OFF_TEXT, MIC_FAULT_TEXT, MIC_PRIVACY_TEXT, MIC_POLICY_TEXT,
          PRECISION_TEXT
```

**`house_sim/kernel/engine.py`**: `body.register_plugins()` at import (idempotent; test_kernel reloads the engine);
`HouseEnv._advance_mode` ("covered" | "idle", transient); `HouseEnv.perform` (power guard, both quiet guards, the
leaf's settlement over its own seconds, `parent_ctx.spend` + `parent_ctx.nested_s`, `robot_sound`);
`HouseEnv._robot_sound(ctx)`, `HouseEnv._note_facts(action, args, seconds)` (ADDITIONS, internal); `step` per 1.9
(the "Engine order" paragraph of `docs/KERNEL_API.md` "v3.0 robot body"); `_affordances` applies the power and
quiet guards before probing a handler (ADDITION: the probe agrees with `step`).

**`house_sim/kernel/body.py`** (BODY-MODEL's library; changes only):

```
register_plugins() -> bool               # DEVIATION: a function the engine calls, not an import-time block
SYSTEM_NAME, SYSTEM_ORDER, SYSTEM_MAX_DT = "kernel.body", 72, 30.0
depleted(state) -> bool                  # ADDITION: off, or the gauge at the cutoff (the "battery depleted" guards)
settle_energy(w, ctx, own_s, hold_check=True)          # DEVIATION: hold_check (the engine passes HOLD_SAFETY_NET)
HOLD_SAFETY_NET = False                  # TIER1-STUB(BODY-CORE)
LEGS_FAILED_TEXT                         # ADDITION (the existing text as a constant)
arm_health(state, hand, believed=False)  # an arms_ok mirror marked False counts as failed
system_tick                              # also powers down a robot that is on at the cutoff (the BMS); reads the
                                         # charge sources only when they matter (deviation 14)
motion(...)                              # meters from geometry.path_length_m (see deviation 7)
```

**`house_sim/kernel/body_faults.py`**: `register_plugins()` (event effects `robot.fault`, `robot.clear_fault`; called by
`body.register_plugins`).

**`house_sim/kernel/schema.py`**: `normalize` builds `data["robot"] = body.apply_profile(config robot, data)` (then
room, hands and positions as before); `ROBOT_DEFAULTS` kept as the reference record.

**`house_sim/kernel/timeline.py`**: `wait` gains `idle=True`, `power_off_ok=True`.

**`house_sim/kernel/phys/forces.py`**: `robot_caps(w, eid=None)` delegates to the body (`capacity_kg(state, None,
eid)`, `capacity_kg(state, "both", eid)`, `limit(...)`, the stronger working hand's `grip_limit_n` power / pinch);
`FALLBACK_CAPS` deleted; ADDITION `CAP_KEYS`.

**`house_sim/kernel/phys/support.py`**: ADDITION `carry_geometry(state)` (the body profile's carry block; `CARRY`, the
humanoid_v1 values, for a state without a robot and state-less callers); `held_envelope` uses it; `children_map`
builds the same map about a quarter faster (it sorts the child lists, not every id).

**`house_sim/systems/core.py`**: `move` / `approach` / `walk_to` through `ctx.require_mobile(MOVE_BLOCKED |
CANNOT_MOVE)` and `ctx.motion`; ADDITIONS `posture_guard(ctx, posture)`, `MOVE_BLOCKED`, `CANNOT_MOVE`, `DOOR_S`,
`APPROACH_SETTLE_S`, `WALK_TO_SETTLE_S`; `_travel_seconds` kept for domains.

## 4. Deviations from the spec

Each was chosen as the more realistic or the simpler option.

1. **The held-load safety net is off in the engine until BODY-CORE** (`body.HOLD_SAFETY_NET = False`,
   `TIER1-STUB(BODY-CORE)`; `settle_energy(..., hold_check=)`, library default True so BODY-MODEL's tests keep
   exercising it). TIER1_EMBODIMENT 8.1 puts the safety net in M3 (BODY-CORE's stage), and running it here broke two
   domain flows (their domain tests failed; such changes are not among B-R1-B-R6): manipulation's co-carry job (the
   robot grips its end of a 40 kg dresser, the body counted all 40 kg in its hands and dropped it at 2.0 x capacity:
   `test_manipulation JobTests.test_lifting_out_of_level_or_walking_off_alone`) and core `uninstall` of a 17 kg
   two-handed propane tank into one hand (`test_appliances_cooking LayoutRoundTests.test_propane_tank_is_a_part_of_the_grill`).
   BODY-CORE switches it on once team carries are excluded and `uninstall` / `receive` go through `check_lift`.
2. **Posture verbs keep their own guard** (`core.posture_guard`) instead of `ctx.require_mobile(legacy_message)`: on a
   climbing aid only `stand` is allowed ("Step down first"), a fallen robot gets "The robot has fallen; recover
   first" (today's checks in today's order), then `body.manip_problem` (actuator fault, flat battery, overheating pack)
   and failed legs. `require_mobile` would refuse `stand` on a stool and refuse every posture change while unlocalized,
   neither of which is realistic.
3. **Registration is a function** (`body.register_plugins()`, called by `engine.py` at import; `body_faults.register_
   plugins()` through it) rather than a block that runs when `body.py` is imported: `test_body_model` pins that
   importing the body registers nothing (TIER1_INTEGRATION 1.16's reason), and test_kernel reloads the engine (the
   call is idempotent).
4. **Nested leaves get the quiet guards and `robot_sound` too** (1.9 lists only the power guard and settlement for
   `perform`): a loud verb inside a chain must not bypass quiet mode, and listeners hear it. No action declares
   `noise_db` yet, so nothing changes today.
5. **Core travel time follows the effective speed from B4** (1.7 says "from BODY-CORE"): the motion verbs take their
   seconds from `ctx.motion`, which plans at `body.effective_speed`. Quiet mode turns a fast walk into a normal one and
   limp mode or weak legs into a careful one, with a note in the message. Planning energy at one speed and timing the
   walk at another would be inconsistent; no verified spec uses quiet mode or reaches limp mode (lowest battery 17.8 %).
6. **"Battery depleted" guards also hold at the cutoff** (`body.depleted`: powered down, or switched on with the gauge
   at the cutoff -- a cold pack, or a battery level a domain wrote directly), and the body system's tick powers such a
   robot down (the BMS); a settlement that drains the pack to the cutoff powers down even when nothing was left to
   spend. Before, a domain writing `battery = 0` left a robot that was "on" at 0 %, which `Ctx.obj` / the motion
   verbs refused (`battery > 0`) but the body did not.
7. **Motion lengths use `geometry.path_length_m`** (left-to-right sum) instead of `sum()`: from Python 3.12 `sum()` of
   floats is compensated (Neumaier), so diagonal walks came out a few ULP different from core's durations and the
   3-decimal clock drifted by 1-3 ms (the first golden diff of this package: 183 `action_log.*.time_s` values and the
   clock fields that follow them).
   BODY-MODEL's "durations to the bit" holds now on every Python version.
8. **`body.arm_health` treats an `arms_ok` mirror marked False as a failed arm** (navigation_robot's `ev_robot_fault`
   still writes only `arms_ok`), so `Ctx.free_hand` and the capacities agree with today's arm checks.
9. **Partial commit details** (1.9 says only settle, ok, message, seconds): a scheduled `run_pending` command that ran
   partly is popped (not run again), `facts.last_action` / `action_times` are written, the message carries the
   step's notes, and realistic perception keeps whole seconds as for computed durations. A step that powered the
   robot down keeps no undo record.
10. **`support.CARRY` stays** as the humanoid_v1 carry geometry for callers without a state; the profile's block is
    `support.carry_geometry(state)` and `held_envelope` uses it. `impact.tear` and `liquids.carry_hand_z_m(state)`
    still read the constant (their files are not BODY-ENGINE's; request 4). Same numbers for humanoid_v1.
11. **`robot_caps`** takes `max_hand_kg` as the stronger single hand (`capacity_kg(state, None, eid)`) and `grip_n` /
    `pinch_n` as the stronger working hand's, since the frozen signature names no hand.
12. **`support.children_map`** is built without sorting every id (identical output, about 25 % faster): the body builds
    one per settlement while the robot carries something, which put settlement at the 0.1 ms budget on 850 entities.
13. **ActionSpec validates the new attributes** (arms 0-2, numeric or callable `noise_db`, a known `timing`), and
    `describe()` shows `noise_db` only when it is set (the agent catalog does not copy it).
14. **The body system reads the charge sources only when they matter** (a pack below full, a powered-down robot, an
    idle advance): a full robot whose time is covered needs neither charging nor the idle-drain decision. It halves the
    system's cost on docked robots and stops `body_log.charge_refused_temp_s` from being written for a full pack that
    needs no current.

## 5. Stubs and contracts

* Removed: `TIER1-STUB(BODY-ENGINE)` in `phys/forces.py` (`FALLBACK_CAPS`, `robot_caps`) and `phys/support.py`
  (`CARRY`). `grep -rn "TIER1-STUB(BODY-ENGINE" house_sim --include=*.py` is empty.
* Added: `TIER1-STUB(BODY-CORE)` on `body.HOLD_SAFETY_NET` (deviation 1); BODY-CORE removes it when it switches the
  safety net on. The other markers on the tree are stage C's (TIME-EVAL, PEOPLE-IO).
* Consumed: BODY-MODEL's body library (`apply_profile`, `power_guard`, `quiet_guard`, `settle_energy`, `system_tick`,
  `robot_stranded`, `ev_*`, `motion`, `cut_motion`, the problem functions, `check_lift`, `grasp_problem`,
  `precision_mm`, `BodyRefusal.apply`), PHYS-INT's motion-record contract (`ctx.data["motion"]`,
  `actions.motion_record(s)`) and `w._power_down_step`, CAM-RUNTIME's `robot_keep_awake` hook and motion frames from
  motion records (a cut walk is framed: tested), PHYS-STATICS' `held_envelope` / `children_map`.
* Provided to NAV-M2 (paired): `ctx.require_mobile(legacy_message=)`, `ctx.motion(...)`, `ctx.cut_motion(...)`, the
  `robot_charge_source` consumer, `robot_stranded`. NAV-M2's files as gated (sha256, first 16 hex):
  `paths.py` 42d04f80de7fabb8, `robot.py` 56cabb9164ac7045, `traverse.py` c0b9e704d812a1bc, `world.py` f5e6faa96e2f7c2a,
  `tests/test_navigation_robot.py` 2a1ccec36456d63e.

## 6. Gate results

Gate id **BODY-ENGINE**, packages **BODY-ENGINE+NAV-M2** (they land in one merge), previous gate **B3-MERGE**, record in
`catalog/analysis/tier1/gate/BODY-ENGINE/`. Taken, like CAM-RENDER's and B3-MERGE's, on an **isolated copy** so G10
sees only B4's changes: the base commit is the shared tree as B3 handed it over (snapshotted before this package's
first edit and before NAV-M2's), with this package's and NAV-M2's current files on top; after the run the shared tree's
files were compared with the gated copy: identical except `docs/domains/navigation_robot.md` (NAV-M2's doc, edited
again after the snapshot). Command:

```
python tools/orchestration/tier1_gate.py --root <scratch>/iso --gates-root catalog/analysis/tier1/gate record
       --id BODY-ENGINE --package BODY-ENGINE+NAV-M2 --prev catalog/analysis/tier1/gate/B3-MERGE
       --out catalog/analysis/tier1/gate/BODY-ENGINE --work <scratch>/gate_work2 --jobs 7
```

Summary line: `TIER1-GATE BODY-ENGINE [BODY-ENGINE+NAV-M2] vs B3-MERGE: FAIL (9 pass, 3 fail, 1 skip)`; the three FAILs
(G1, G1b, G10) are in files outside both packages' lists (section 7, requests 1-3, 5, 6) and `compare` passes.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 482f9adf9717 unchanged (ActionSpec additions are not in it); 111 systems (B3-MERGE 110: + `kernel.body`) |
| G1 | FAIL | 42 files, 3,471 tests (normal); `test_body_engine.py` 46 tests green normal and strict; 10 failing runs (5 files x normal + strict): `test_body_model.py` (2 tests, request 1), `test_physics_forces.py` (1, request 2), `test_camera_actions.py` (1, request 3), and the two known since B3-MERGE: `test_camera.py` (CAM-RENDER's removed guard) and `test_textiles_laundry.py` (PHYS-INT's R7 jug bound) |
| G1b | FAIL | the 9 isolated-domain failures known from B3-MERGE, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 12/16, the four failing ones are the same four files (`test_body_engine.py` green core-only) |
| G1c | SKIP | no import-inert modules for this package |
| G2 | PASS | own mode 242/242 (1,210 trials); 0 verdict changes; 1,210 state-digest and 1,186 observation-digest changes (allowed: B-R1, B-R2) |
| G3 | PASS | forced realistic 226/242 (as B3-MERGE); 0 verdict changes; 1,210 / 1,185 digest changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit room and geometric: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room and geometric: 1,800 commands each, 0 defects, 0 nondeterministic (375 succeeded, as before) |
| G7 | PASS | 3.03 ms per substep (B3-MERGE 3.05), 1 h wait 1.083 s (1.081) |
| G8 | PASS | room golden and golden_all ok (re-recorded by this package, below) |
| G9 | PASS | `body_audit --baseline`: ok; 2,108 picks, 0 landing hits (as B0); lowest battery 17.69 % (B0 17.75 %, T-5ad61b6881821b-01 seed 1) |
| G10 | FAIL | 21 changed files, 20 on the BODY-ENGINE+NAV-M2 lists (and this handoff); outside: `docs/domains/navigation_robot.md`, NAV-M2's |

`python tools/orchestration/tier1_gate.py compare B3-MERGE BODY-ENGINE --package BODY-ENGINE+NAV-M2`:

```
compare B3-MERGE -> BODY-ENGINE (packages: BODY-ENGINE+NAV-M2; allowed: obs,state,verdict; codes: B-R1,B-R2,B-R3,B-R4,B-R5,B-R6)
G0   PASS  action fingerprint unchanged (482f9adf9717)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1210 with changed state digests (allowed), 1186 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1210 with changed state digests (allowed), 1185 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 375 -> 375
G7   PASS  substep_ms: 3.05 -> 3.03 (-0.7%; limit 3.203 = prev x 1.05)
           wait_1h_s: 1.081 -> 1.0827 (+0.2%; limit 1.135 = prev x 1.05)
           largest per-system increases: kernel.body +0.023 ms
G1   INFO  new test file test_body_engine.py (normal): 46 tests
           new test file test_body_engine.py (strict): 46 tests
           test_navigation_robot.py (normal): ran 159 -> 180, skipped 1 -> 1
           test_navigation_robot.py (strict): ran 159 -> 180, skipped 1 -> 1
compare result: PASS
```

A first full record of the same code (before the last edits: the lazy charge-source read, the probe guards, test
additions) had the same G0-G6 / G8-G10 results and failed only G7 (+5.6 % / +5.1 %) while NAV-M2's gate loaded the
machine; interleaved runs of the B3 base and this tree in that window measured 3.26-3.28 vs 3.18-3.20 ms per substep
and 1.131-1.134 vs 1.123-1.135 s per 1 h wait (no difference beyond noise); the record above was taken on a quiet
machine.

### Reviewed differences (3.3)

No verdict changed (G2, G3), so no row is needed to pass. Every state and observation change was classified with the
golden (`tools/golden.py dump --all` before the first edit, on the snapshot, and again at hand-over; seed 0, all 242
reference plans and the 8 scripted runs):

```
274 file(s) compared, 266 changed, 30 path pattern(s)
   258  robot._activity, robot._hand_kg, robot._held_kg, robot._idle_s, robot._slips, robot.arm_health,
        robot.battery_health, robot.battery_wh, robot.body_log, robot.body_profile, robot.energy_wh_charged,
        robot.fault_state, robot.grippers, robot.hand_soil, robot.legs_health, robot.localized, robot.mass_kg,
        robot.power_state, robot.sensors, robot.standby                      (B-R1: materialized robot keys)
   258  robot.battery_min_pct, robot.battery_temp_c, robot.energy_wh_used     (B-R1 keys, B-R2 values)
   249  stats.energy                                                          (B-R2: now % of the pack from Wh)
   247  robot.battery                                                         (B-R2: battery trajectories)
    51  robot.charging                                                        (B-R1: now materialized)
     8  obs.battery, obs.robot.battery, obs.nav.battery_pct                   (B-R2)
     1  people.*._space_jn.*   A-d66994a566899c-01: 635 -> 637               (B-R2, see below)
```

`people.resident_1._space_jn` is the people domain's cursor into the step's journal keys (`senses.py`); during that
spec's long waits the body's idle drain journals the robot and stats sections, so the key count grows by two. No
clock, entity or room path changed (an earlier dump of this package showed 1-3 ms clock drifts from `sum()` in
`body.motion`; deviation 7 removed them). Classes not seen in any reference run: B-R3 (no reference plan runs the
battery out), B-R4 (no spec carries legacy fault strings). B-R5: the golden was re-recorded
(`history`: `{"by": "BODY-ENGINE", "classes": ["B-R1", "B-R2"]}`). B-R6, checked by hand (reference plans, 5 seeds):

| Spec | Result |
|---|---|
| T-5ad61b6881821b-01 (battery predicate `gt 19` after docking) | passes on all seeds in both modes: lowest 17.69-17.86 %, final 22.36-22.53 % (docked, charging True; 112 Wh charged in the 120 s wait at 2 %/min) |
| A-d66994a566899c-01 (42 h undocked waits on seed 0) | passes on all seeds in both modes: seed 0 ends at 64.3 % after 857 Wh (parked standby 20 W); the other seeds wait 1-2.5 h and end at 96.8-98.8 % |

### Benchmarks against 3.4 (this machine, quiet, best of 5-7)

| Measure | Budget | BODY-ENGINE |
|---|---|---|
| body system per substep, idle (a wait) | <= 0.02 ms | 0.0097 ms (seeds 0 and 7); covered 0.0032 ms |
| body system in `bench_systems` (a step per substep, docked robots) | <= 0.02 ms | 0.023 ms in G7's record (each substep there starts a new step, so the pack-temperature write clones the robot record every substep; a reproduction of that loop measures 0.009 ms) |
| settlement per leaf | <= 0.1 ms | 0.007 ms empty-handed; 0.066 / 0.082 ms carrying two items (649 / 853 entities: one physics children map) |
| motion prefix, 40 cells | <= 0.05 ms | 0.0029 ms |
| a week-long wait (20,160 substeps of the body system) | <= 0.4 s added | 0.20 s |
| whole engine (G7) | <= B3-MERGE x 1.05 | 3.03 ms per substep, 1.083 s per 1 h wait |

`tests/test_body_engine.py` `PerformanceTests` fail at 3x these budgets.

### Audits

`perception_audit` room and geometric: 0 leaks; `fuzz` room and geometric: 0 defects, deterministic cross-process
replays; `body_audit --baseline` (G9): 0 landing hits.

## 7. Known gaps

* **G1 fails in five test files outside BODY-ENGINE's list** (requests 1-3; `test_camera.py` and
  `test_textiles_laundry.py` fail as in B3-MERGE): `test_body_model.py` builds its "legacy" robot with
  `schema.normalize`, which now materializes the body (2 tests); `test_physics_forces.py` pins stage A's robot-record
  force overrides, which TIER1_INTEGRATION 1.2 removes (1 test); `test_camera_actions.py` pins "Robot cannot move" for
  `face` with a flat battery, which is now a powered-down robot refused by the engine's guard (1.15; 1 test). Each
  requested patch was applied to a scratch copy of the file and passes against this tree.
* **Not in this package** (BODY-CORE, B5): derated lifts in `pick_up` / `receive` / `uninstall` through `check_lift` and
  `require_grasp`, the held-load safety net (`HOLD_SAFETY_NET`), the stairs capability in `move`
  (`require_mobile(stairs=True)`; only the mobile_manipulator profile and weak legs lack it, and no spec uses either),
  `look` / `inspect` / `read` / `listen` / `ask` through the sensor guards, `say()`'s audibility, `set_speed` /
  `set_quiet` rules, the careful-speed
  fix (the engine still multiplies careful physical actions by 1.5, so settlement charges that hotel time too), the
  setup ops `robot` / `robot_fault`, the interim `face` / `wrist_peek` battery checks. BODY-FAULTS (B5): the
  perception gates (power off still shows the room; privacy mode still shows small things) and realistic `obs.robot`.
* **Realistic `obs.robot.battery`** is the exact true charge (9 decimals; `obs.battery` 3) until BODY-FAULTS renders
  the integer gauge; its drops follow power x time, so a careful reader could infer a held load's mass from them
  (3 W per kg). Not a new leak class (the battery was exact before), but a richer one.
* **A nested leaf's energy refusal** is raised after its handler ran; when the whole step is refused that rolls back,
  but a domain loop that catches `ActionError` around `env.perform` keeps the leaf's effects without paying for them
  (needs a nearly flat pack; TIER1_EMBODIMENT 4.6 accepts nested refusals rolling back the parent).
* **Domain after-hooks on core `move`** (cleaning, electrical, manipulation, mechanisms, outdoor_plants, people) have not
  been written for a battery-cut move (`ctx.data["motion"]["cut"] == "battery"`: the robot stayed in its room,
  `ctx.data["connection"]` is None); navigation_robot's are (NAV-M2). Only a nearly flat pack makes the case.
* **Settlement while carrying** builds one physics children map per leaf (an O(N) pass): about 0.06-0.08 ms on 650-850
  entities after the `children_map` speed-up, near the 0.1 ms budget; empty-handed settlement is under 0.01 ms.
* **Threshold crossings during an action** are stamped at the action's start (settlement time); only the cutoff inside
  an idle substep gets its exact time (BODY-MODEL's gap, unchanged).

## 8. How to test

```
python -m unittest tests.test_body_engine                    # 46 tests, about 2 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_body_engine
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_body_engine
python -m unittest tests.test_body_model tests.test_kernel tests.test_navigation_robot tests.test_room_golden
python tools/golden.py check --all                           # the re-recorded golden
python tools/orchestration/tier1_gate.py compare B3-MERGE BODY-ENGINE --package BODY-ENGINE+NAV-M2
```

Manual checks (a hand-built hall: see `tests/test_body_engine.config`):

```python
from tests.test_body_engine import make, battery_for
env = make({"battery": battery_for(8.0)})
env.step("move", "kitchen")       # ok, "Battery depleted after 8.0 m; the robot powered down | Battery critical: ..."
env.world.s["robot"]["power_state"], env.evaluate()["failures"]     # "off", ["robot_stranded"]
env.step("look")                  # refused: "The robot is powered down (battery depleted); ..."
env = make(); env.step("wait", seconds=600); env.world.s["robot"]["energy_wh_used"]   # 60 s awake, then standby
```

## 9. Follow-ups

Requests to the main session (files outside BODY-ENGINE; each patch was checked on a scratch copy against this tree):

1. `tests/test_body_model.py` (BODY-MODEL's): its `world(materialize=False)` relies on `schema.normalize` leaving the
   robot record legacy, which BODY-ENGINE's normalize no longer does (2 failing tests:
   `Registry.test_get_falls_back_to_declared_defaults_on_legacy_states`,
   `Proprioception.test_oracle_shows_today_keys_plus_registered_visible_fields_exactly`). Replace the two lines after
   `state = normalize(config, REG)` with:

   ```python
       if materialize:
           state["robot"] = body.apply_profile(state["robot"], state)
       else:
           # a legacy robot record: normalize materializes the body from BODY-ENGINE on, so keep only the keys a
           # pre-body state had (schema.ROBOT_DEFAULTS, the position and hands, the config's own fields)
           keep = set(ROBOT_DEFAULTS) | {"room", "pos", "hands"} | set(cfg)
           state["robot"] = {k: v for k, v in state["robot"].items() if k in keep}
   ```
2. `tests/test_physics_forces.py` (PHYS-DATA's): `RobotCapsTests.test_robot_fields_override_and_bad_values_fall_back`
   pins stage A's robot-record force overrides (`push_n` 120 on the robot record); TIER1_INTEGRATION 1.2 makes the
   body profile the only source. Replace it with:

   ```python
       def test_the_body_profile_is_the_only_source(self):
           # BODY-ENGINE (TIER1_INTEGRATION 1.2): robot_caps delegates to the body; the materialized ratings and the
           # profile (with profile_overrides) count, a force field on the robot record does not
           w = world(robot={"max_hand_kg": 4.0, "push_n": 120, "grip_n": "strong",
                            "profile_overrides": {"push_n": 140.0, "wrist_nm": 5.0}})
           caps = forces.robot_caps(w, "anything")
           self.assertEqual((caps["max_hand_kg"], caps["push_n"], caps["wrist_nm"], caps["grip_n"], caps["pinch_n"]),
                            (4.0, 140.0, 5.0, 150.0, 30.0))
   ```
3. `tests/test_camera_actions.py` (CAM-ACTIONS'): `FaceTests.test_arguments_and_guards` expects "Robot cannot move" for
   `face` with `battery 0.0`; a robot that starts flat is now powered down and the engine refuses every action but
   `wait` with the PowerOff text (TIER1_INTEGRATION 1.15). Replace the loop with:

   ```python
           for robot in ({"fallen": True}, {"actuator_ok": False}):
               refused = env_of(pos=(5, 5), robot=robot).step("face", "north")
               self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                                (False, "Robot cannot move", "capability"), robot)
           flat = env_of(pos=(5, 5), robot={"battery": 0.0}).step("face", "north")    # powered down (BODY-ENGINE)
           self.assertEqual((flat["ok"], flat["message"], flat["category"]),
                            (False, "The robot is powered down (battery depleted); it must be charged or have its "
                                    "battery swapped", "capability"))
   ```
4. `house_sim/kernel/phys/liquids.py` `carry_hand_z_m(state)` and `house_sim/kernel/phys/impact.py` `tear` (robot holder):
   read `support.carry_geometry(state)["hand_z_m"]` instead of `support.CARRY["hand_z_m"]`, so a compact humanoid's
   carried items fall from its own hand height (same numbers for humanoid_v1; no current spec changes).
5. `docs/domains/navigation_robot.md` was edited by NAV-M2 and is on neither package's list (G10).
6. Still open from earlier packages (unchanged here): `tests/test_camera.py` pins CAM-RENDER's removed guard;
   `tests/test_textiles_laundry.py` pins the pre-R7 jug mass (PHYS-INT request 1).

For later packages:

* **BODY-CORE (B5)**: switch `body.HOLD_SAFETY_NET` on (and remove its `TIER1-STUB(BODY-CORE)`) once team carries are
  excluded (manipulation's co-carry: the robot holds its end of a dresser) and `uninstall` / `receive` lift through
  `ctx.check_lift`; pass `stairs=True` to `ctx.require_mobile` when `move` crosses stairs; replace the interim
  `face` / `wrist_peek` battery checks (`robot.get("battery") > 0`) with `ctx.require_mobile` / `ctx.require_arm`;
  M5 moves the careful x1.5 to the action's own seconds (`timing="speed"` exists on ActionSpec; core motion verbs
  still default to "fixed"). `body_audit --against catalog/analysis/tier1/gate/B0/body_audit.json`: this package's
  G9 record still has 0 landing hits (2,108 picks).
* **BODY-FAULTS (B5)**: realistic `obs.robot.battery` is still the exact float (now 9 decimals) until proprio renders
  it; the power-off and privacy gates of `Perception.visible` are yours.
* **TIME-EVAL (C)**: `env._advance_mode` is "covered" / "idle"; an auto-order command runs as `perform` (it settles its
  own energy) under "covered", as 1.9 says.
* **Domains (adoption round)**: after-hooks on core `move` / `approach` / `walk_to` should check
  `ctx.data["motion"]["cut"]` (a battery cut: the robot stayed in its room); `pets/common.py` keeps a
  `robot.get("battery", 100) > 0` guard (the engine's PowerOff already covers a powered-down robot); domains that write
  `robot.battery` or `arms_ok` directly should go through the body (TIER1_INTEGRATION 6 "Body").

## 10. B4 merge gate (B4-MERGE)

Added by the B4 merge gate after both B4 packages had handed over. The same section is in `handoff_BODY-ENGINE.md` and
`handoff_NAV-M2.md`. The merge gate read every `handoff_*.md`, TIER1_INTEGRATION (1.1, 1.4, 1.7, 1.9 with its rules,
1.10, 1.15, the B4 and NAV-M2 rows of 2.4, 3, 5), TIER1_DECISIONS and TIER1_EMBODIMENT (4.3-4.6, 8.1-8.3, 10, 12).
It then ran two things on the merged tree, with the union of both packages:

* the full gate;
* the cross-package checks the plan lists for B4: the double-drain guard with every domain loaded (TIER1_EMBODIMENT 10
  item 15), the M2 row of 8.1 and the B-R6 hand checks, the navigation tests of 8.2, the golden (3.5). It also probed
  the seams between the two packages: the body's PowerOff guard on nested leaves against navigation's chains, the
  motion record against `traverse`'s cuts, the dock as the body's charge source, and save / load / replay.

Two places did not fit together. Both are fixed inside the union of the two packages' files, each with regression
tests:

1. **A patrol that ran the battery out crashed.** File: `house_sim/systems/navigation_robot/paths.py` (NAV-M2's).
   * Cause: once the robot powers down, every later nested leaf meets BODY-ENGINE's `PowerOff` guard in
     `engine.perform`. TIER1_EMBODIMENT 4.6 says domain loops that catch `ActionError` then go on without effect.
     `patrol` does catch the refusals of its `inspect` / `report` legs, but its handler logged them with
     `C.log(w, "patrol_unreported", target=eid, kind=kind)`. `C.log`'s first parameter is itself named `kind`, so the
     call raised `TypeError` (not an `ActionError`). The whole `env.step` raised; with `HOUSE_SIM_SAFE=1` it is an
     internal error that fails the task.
   * The bug was latent before B4: any refused `inspect` or `report` inside a patrol reached it. No authored spec, unit
     test or gate fuzz ever did. After B4 it was the normal outcome of a patrol whose sweep runs the pack flat with an
     anomaly in view. The merge gate's cross-package fuzz hit it at once (seed 0, 0.6 %, step 5, all four modes).
   * Fix: the anomaly is logged under its own key, `C.log(w, "patrol_unreported", target=eid, anomaly=kind)`. Nothing
     reads that log entry's fields.
   * Test: `tests/test_navigation_robot.py`
     `TestBodyEnergy.test_a_patrol_that_runs_the_battery_out_goes_on_without_effect`, in oracle and realistic mode.
     With one room the patrol stays ok, reports its two findings, powers down, logs both anomalies as unreported, and
     the robot is stranded; the next patrol is refused with the PowerOff text. With a second room the `go_to` leg meets
     `PowerOff` outside any catch, so the step commits as "Powered down partway: ...". The test fails on the pre-fix
     merged tree with the `TypeError` and passes in normal and strict runs, also with only navigation_robot loaded.
   * Effect on specs: none. No authored spec uses `patrol`.
   * `docs/domains/navigation_robot.md` needs no change: it does not describe that log entry.
2. **A walk stopped short of its battery cut still claimed a power-down.** Files: `house_sim/kernel/body.py` and
   `house_sim/kernel/engine.py` (BODY-ENGINE's).
   * Cause: core `move`, `approach` and `walk_to`, and navigation's `step`, return "Battery depleted after X m; the
     robot powered down" in the handler, as soon as `ctx.motion` cuts the walk. That is before the after-hooks run.
     Navigation's `traverse` (an after-hook) can then stop the robot earlier, at a blocker it will not step over or by a
     fall. It calls `ctx.cut_motion`, which clears `exhausts` (TIER1_EMBODIMENT 4.6), so the robot pays only what it
     walked and stays on. The message still said it had powered down.
   * Example: the navigation tests' Mini house, careful pace, 0.035 %. The message was "Battery depleted after 2.5 m;
     the robot powered down | Stopped: flat-pack carton lies in the way ...", but the robot was on at [5, 3] after
     1.0 m, with 0.019 % left.
   * The spec covers the energy of this case but not the message. The contradiction exists only with both packages
     merged.
   * Found by a targeted probe of the seam: a stop inside the affordable prefix. The fuzz never produced it.
   * Fix:
     * `body.cut_motion` records a battery cut it supersedes in `ctx.data["motion_battery_cut_m"]`.
     * The engine calls `body.motion_result(ctx, result)` right after a leaf's after-hooks, in `step` and in `perform`.
       It replaces exactly that verb text (`BATTERY_CUT_TEXT` with the superseded meters) with
       `body.STOPPED_FIRST_TEXT`, "Walked X m", using the meters actually walked. The domain's notes say why the robot
       stopped.
     * Every other result is unchanged. A battery cut that nobody stopped keeps its text and still powers the robot
       down.
   * Tests, both in normal and strict runs, oracle and realistic mode. Both fail on the pre-fix merged tree with the
     false message.
     * `tests/test_body_engine.py`
       `TruncationTests.test_a_domain_stop_before_the_battery_cut_keeps_the_robot_on_and_the_message_true`, also with
       `HOUSE_SIM_DOMAINS=core`. An after-hook stops a `walk_to` that the battery cuts at 8 m after 3 m. The robot pays
       for 3 m and the hotel load over the step's 20 s, stays on and is not stranded; the message is "Walked 3.0 m |
       ...". The uncut and the affordable cases are unchanged.
     * `tests/test_navigation_robot.py` `TestBodyEnergy.test_a_stop_before_the_battery_cut_keeps_the_robot_on_and_says_so`,
       also with only navigation_robot loaded. It uses the real `traverse` and the carton: the robot pays for 1.0 m,
       there is no `battery_depleted` incident, and the robot is not stranded.
   * Docs: `docs/KERNEL_API.md` ("Power-down and running out") and `docs/CONVENTIONS.md` (Energy).
   * Effect on specs: none. No reference run powers down (below).
   * API addition: `body.motion_result(ctx, result)`, `body.STOPPED_FIRST_TEXT`, and the reserved `ctx.data` key
     `motion_battery_cut_m`.

G2 and G3 with both fixes are digest-identical to `gate/BODY-ENGINE/` and to the same gate on the pre-fix tree (below).

**Gate.** The record is in `catalog/analysis/tier1/gate/B4-MERGE/`. It was taken on an isolated copy so that G10
sees only B4's changes:

* The base commit is the shared tree as B3 handed it over: BODY-ENGINE's 16:17 snapshot, which is also NAV-M2's base
  (commit `115bcb9` of NAV-M2's scratch repository).
* The working tree is the project tree (CRLF checkouts as LF). Its `git status` lists exactly the 19 B4 files, the two
  handoffs and `tests/test_body_engine.py`. Every B4 file except these two handoffs is byte-identical to the project.

Command:

```
python tools/orchestration/tier1_gate.py --root <iso> --gates-root catalog/analysis/tier1/gate record --id B4-MERGE
       --package BODY-ENGINE+NAV-M2 --prev catalog/analysis/tier1/gate/B3-MERGE
       --out catalog/analysis/tier1/gate/B4-MERGE --work <scratch>/gate_work_final2 --jobs 7
```

My own checks were running while the full record took its benchmarks. G7 was therefore re-run alone on a quiet machine
(`--only G7`), which keeps the rest of the record.

Summary line: `TIER1-GATE B4-MERGE [BODY-ENGINE+NAV-M2] vs B3-MERGE: FAIL (9 pass, 3 fail, 1 skip; this run: G7)`.
The three FAILs are all in files outside the union. `compare` passes.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `482f9adf9717`, unchanged (425 actions, 111 systems) |
| G1 | FAIL | 42 files, 3,474 tests (normal); `test_body_engine.py` (47, one new) and `test_navigation_robot.py` (182, two new) green in normal and strict. 10 failing runs, the same tests as `gate/BODY-ENGINE/`: `test_body_model.py` (2 tests), `test_physics_forces.py` (1) and `test_camera_actions.py` (1) are BODY-ENGINE's requests 1-3; `test_camera.py` (CAM-RENDER's removed guard) and `test_textiles_laundry.py` (PHYS-INT's R7) are known since B3-MERGE |
| G1b | FAIL | the 9 isolated-domain failures known from B3-MERGE, none new (navigation_robot's one is A0's); kernel files core-only 12/16: the same four pinned files |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 242/242 (1,210 trials); 0 verdict changes; 1,210 state-digest and 1,186 observation-digest changes (allowed: B-R1, B-R2) |
| G3 | PASS | forced realistic 226/242 (as B3-MERGE); 0 verdict changes; 1,210 / 1,185 digest changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz, room and geometric: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.01 ms per substep (B3-MERGE 3.05, limit 3.203) and 1.083 s for the 1 h wait (1.081, limit 1.135); `kernel.body` +0.022 ms. The measurement that overlapped my checks was 3.10 ms / 1.107 s, also within budget |
| G8 | PASS | room golden and golden_all (BODY-ENGINE's re-record; neither fix touches it) |
| G9 | PASS | `body_audit --baseline`: ok |
| G10 | FAIL | 22 changed files; 1 outside the union: `docs/domains/navigation_robot.md` (NAV-M2's request 1) |

`compare BODY-ENGINE -> B4-MERGE` and `compare <the same gate on the pre-fix tree> -> B4-MERGE`: 0 verdict,
0 state-digest and 0 observation-digest changes in both modes. The failing tests of G1 and G1b are exactly those of
`gate/BODY-ENGINE/`.

**Reviewed differences.** None are needed: no verdict changed. The digest classes are B-R1 and B-R2, as BODY-ENGINE's
golden diff (section 6) shows. Neither fix changes a golden path.

**Cross-package checks beyond the gate.** They ran on the merged tree; the scripts are in the merge gate's scratch
folder (`t1_merge_B4/`).

* **Energy identity over every reference plan.** Every authored spec, seeds 0 1 2 41 97, own mode: 1,210 trials, all
  passing. After every step, `battery x full_wh / 100 + energy_wh_used - energy_wh_charged` stays constant, so nobody
  but the body writes the battery. This is the double-drain guard (TIER1_EMBODIMENT 10 item 15) with every domain
  loaded. Also after every step: energy is charged only on a live seated dock, and a switched-on robot is never left at
  the cutoff. Result: 0 violations, 0 power-downs, 0 `robot_stranded`. The lowest battery is 17.692 %
  (T-5ad61b6881821b-01, seed 1).
* **B-R6, by hand.**
  * T-5ad61b6881821b-01 passes on all seeds in both modes (G2 / G3). Its lowest battery is 17.69-17.86 % and its final
    one 22.36-22.53 %; it charges 112.0 Wh on the dock. These are BODY-ENGINE's and NAV-M2's numbers.
  * A-d66994a566899c-01 passes on all seeds. Seed 0 uses 857.25 Wh over its 42 h undocked and ends at 64.281 %; the
    other seeds end at 96.8-98.8 %.
* **Cross-package fuzz** (`xfuzz.py`). Generated houses with every domain loaded and `HOUSE_SIM_STRICT=1`, in all four
  modes (oracle / realistic x room / geometric), 60 steps per run, two runs per case plus a save / load in the middle.
  * Verbs: `step`, `go_to`, `move`, `walk_to`, `approach`, `dock`, `undock`, `wait`, `wait_aside`, `patrol`, `follow`,
    `lead`, the postures, `look`, `pick_up`, `place`, `undo` and `set_speed`.
  * Per step it checks the energy identity, charging only on a live dock, the `charging` flag, no robot left on at the
    cutoff, PowerOff for everything but `wait` while off, the `robot_stranded` label against the body's rule, motion
    messages against the robot's room, a valid pose, and no internal error or exception.
  * Runs: seeds 0-7 at batteries 0.6 % and 3 % undocked, 25 % docked and 100 % (128 runs, 7,680 steps, 4,656 of
    them ok, 98 runs charging on the dock), and seeds 0-7 at 0.02-0.3 % undocked with no waits, so the first walk of
    a run is cut mid-way (128 runs, 7,680 steps).
  * What powered the robot down first in the cut runs:
    * a cut, "Battery depleted after ...": `walk_to` 20, `move` 4, `approach` 2, `step` 2;
    * `go_to`: "Stopped in ... (battery_depleted)" 34, "Powered down partway" 2;
    * `patrol`: 36 ending after its `go_to` leg stopped short, 4 "Powered down partway";
    * `lead`: 6 with status "stopped";
    * `wait_aside`: 16 completing within the hidden buffer, with the "Battery depleted as it finished" note;
    * 2 runs never powered down.
  * Result on the final tree: 0 problems. Every run is deterministic, and every save / load continues identically.
  * Before fix 1: the `patrol` `TypeError` above.
  * The cut runs never stopped a walk before its cut point, so fix 2 came from the targeted probe, not from this fuzz.
  * One more finding, not a B4 seam: a fall on the way during a core `move` leaves the robot where it fell, yet the
    message stays "Moved to X" (seed 3, 100 %, a cord snag in the living room). The B3 tree does exactly the same. It
    is listed under follow-ups.
* **Realistic leak audit at low battery.** This is NAV-M2's `lowbat_audit.py`, extended with `patrol`, `follow`, `lead`,
  `undo`, the postures and a docked 20 % robot (charging and waking). It ran `tools/perception_audit.py`'s
  `check_message`, `check_obs`, `check_physics_notes` and `check_camera_obs` on every step: 8 houses x room / geometric
  x 80 steps. Result: 0 leaks, 0 errors; 315 battery-cut or powered-down messages checked; 329 steps observed while
  powered down.
* **BODY-ENGINE's requests 1-3, checked.** The three patches of BODY-ENGINE section 9 were applied to copies of
  `test_body_model.py`, `test_physics_forces.py` and `test_camera_actions.py` in a repository copy with the merged tree.
  All three files pass in normal and strict runs and with `HOUSE_SIM_DOMAINS=core`. With them, and with the two
  earlier requests (`test_camera.py`, `test_textiles_laundry.py`), G1 and the core-only part of G1b would pass.

**Seams checked and found consistent** (no change needed):

* Chains after a cut on a nested leg (the tally above): `go_to`, `wait_aside`, `lead` and `patrol` stop where the
  walk was cut. When a later leg meets `PowerOff` outside a catch, the engine commits "Powered down partway".
* A cut core `move`: the motion record stays in-room; `traverse` gets no passage and no arrival; the incident is
  `battery_depleted`.
* Stops and falls cut the record before settlement (`cut_motion` clears `exhausts`), so a stop inside the affordable
  prefix pays only the cells walked and does not power down. This is checked with a carton; the message is fix 2.
* The dock: docking is free; the body charges from the next substep; an unpowered dock is a dock without charge, and a
  robot that dies on it is not stranded.
* `navigation_robot.stranded` is referenced nowhere outside the docs.
* A nested energy refusal swallowed by a domain loop (BODY-ENGINE's known gap) cannot happen in `patrol`. Its legs
  need far less than the 12 Wh hidden buffer.

**Still open for the main session** (files outside the union):

* BODY-ENGINE's requests 1-3 (patches verified above), 4 (`liquids.carry_hand_z_m` / `impact.tear` through
  `support.carry_geometry`) and 6 (`test_camera.py`, `test_textiles_laundry.py`).
* NAV-M2's request 1 (`docs/domains/navigation_robot.md` on NAV-M2's list; until then G10 fails) and requests 2-4.

**Follow-ups for the navigation_robot adoption round** (new, both pre-dating B4):

* When `traverse` leaves the robot where it fell during a core `move`, the step's message still says "Moved to X".
  Only `robot.fallen` and `obs.nav.last_incident` tell the truth. A note such as "fell before the doorway" would make
  the message honest too.
* `patrol`'s result does not say that it ended early when a `go_to` leg stopped short, for a battery cut or a blocked
  route. Only `obs.nav` and the robot's room show it. NAV-M2 already made `go_to` and `wait_aside` report a battery
  stop.

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
