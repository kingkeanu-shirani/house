# Handoff BODY-MODEL (stage A): the robot body model as a library

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_EMBODIMENT.md` (whole; sections 3-5 and 10 are this package's), the physics spec's frozen
`support` / `impact` signatures (TIER1_PHYSICS 5.1, 5.5, 7.1). Handoff files read: `handoff_TIER1-GATE.md` (the only
one at the start); `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`
and `handoff_GEN.md` appeared during the work and were checked for the body contracts before finishing
(`robot_caps` delegation, the `CARRY` stand-ins, `eye_height_m` bridging, the identical `facing_deg` / `view`
re-declaration).

## 1. Summary

* `house_sim/kernel/body.py`: profiles, the robot field registry, energy (Wh, temperature cutoff, gauge, thresholds,
  limp, power states), leaf settlement, the motion record helpers, the body system's closed forms (idle drain,
  CC/CV charging, pack temperature, network window), lift capacity and the lift check, grasp, precision, force
  limits, hand soil, speed / stopping / noise / eye height, the sensor rules of TIER1_INTEGRATION 1.3.
* `body_faults.py` (typed faults, diagnostics, fault log, slips, falls, drops through `impact.fall`, IP54 water,
  lens degradation), `proprio.py` (oracle and realistic `obs.robot`), `data/robot/profiles.json` (three profiles with
  sourced numbers), `tools/body_audit.py --baseline`, `tests/test_body_model.py` (93 tests).
* Import-inert: nothing is registered in `kernel.plugins`; zero behaviour change (nothing calls the modules yet).
* Fix pass after the stage-A review (gate A-FIX): a `body_audit` run narrowed by `--spec-files` or `--seeds` no
  longer overwrites the full baseline; the gate's G9 keeps its record in the gate folder (deviation 21).

## 2. Files

Created (all on the BODY-MODEL list of TIER1_INTEGRATION 2.3): `house_sim/kernel/body.py`,
`house_sim/kernel/body_faults.py`, `house_sim/kernel/proprio.py`, `house_sim/data/robot/profiles.json`,
`tools/body_audit.py`, `tests/test_body_model.py`, this handoff, and the gate record
`catalog/analysis/tier1/gate/BODY-MODEL/`. No existing file was edited. (`data/robot/*.json` is packaged by the
`setup.py` glob PHYS-DATA adds, conflict 17.)

## 3. APIs

`body.py` (all functions take a state dict or a journaled World; every one works on legacy states through
`get()` defaults). Marked **DEVIATION** where the signature differs from TIER1_EMBODIMENT 5.1 / TIER1_INTEGRATION.

```
class BodyRefusal(ActionError)   # kernel_access; .effects (fn(w)), .grants ((eid, level, fields)); .apply(env)  DEVIATION (new)
class PowerOff(BodyRefusal)      # POWER_OFF_TEXT, capability
# profiles and fields
profile_names(); profile(state) -> dict (cached, read-only); gripper(state, hand) -> dict
register_robot_field(name, default, owner, tier, doc, materialize=False)   # default may be fn(state)
robot_fields() -> {name: {default, owner, tier, doc, materialize}}; ROBOT_FIELDS (the table)
get(state, name, default=None)
apply_profile(robot_cfg, state=None) -> robot dict      DEVIATION: state (rooms, weather, start_room, time_s) for the start temperature
# faults as read by the body
fault(state, fault_id) -> active record | None; is_latent(state, fault_id)
# battery and power
full_wh(state); temp_factor(t_c); cutoff_soc(state); gauge(state); threshold_pct(state, name); battery_state(state)
available_wh(state, floor="cutoff" | "reserve"); is_on(state); limp(state); posture_key(state)
activity_power_w(state, kind, arms=0, held_kg=None)     # awake|standby|off|motion|physical|perception|communication|control|cognitive
walk_wh(state, meters, speed, load_kg); stairs_wh(state, up, down); stairs_direction(state, conn, a, b) -> "up"|"down"|None
motion_work_wh(state, record)
affordable_prefix(state, segments_m, speed, load_kg, stairs=(0, 0), extra_s=0.0, extra_m=0.0, power_w=None) -> (k, finish_ok)
                                                         DEVIATION: extra_m (doorway crossing), power_w (hotel incl. hooks)
spend_wh(w, wh, at_s=None) -> % spent                    DEVIATION: no `kind`; at_s stamps a cutoff inside a substep
power_down(w, cause, at_s=None) -> bool; wake(w, cause, at_s=None) -> bool; charge_rate(w) -> %/min
swap_pack(w, charge_pct, capacity_wh=None, health=None, temp_c=None) -> old pack dict
log_first(w, key, now)                                   # body_log first times
# carried load (mass rule = physics support.subtree_mass, one children_map per pass)
held_hands(state) -> [(eid, hands)]; held_loads(state, cmap=None) -> [(eid, hands, kg)]
carried_kg(w, cmap=None); total_mass_kg(w); refresh_held(w) -> held_loads (writes _held_kg, _hand_kg when changed)
# limits
arm_health(state, hand, believed=False); capacity_kg(state, hand=None|"left"|"right"|"both", eid=None, believed=False)
check_lift(w, eid, hands, mass=None)        # hands: "left"|"right"|"both"|[hands]|1|2 (lift_plan's form is not frozen)
hold_capacity_left(w, hand); stability_margin(state, extra_kg, lever_m=0.35)
grip_limit_n(state, hand, mode="power"); limit(state, "push_n"|"pull_n"|"wrist_nm")
slippery(state, hand); grip_mu(state, hand); clarity(state); precision_mm(state, hand)
grasp_problem(state, record, hand, grip) -> (problem | None, auto_grip | None); GRASP_AUTO {grip: (note, extra_s)}
speed_mps(state, mode=None); effective_speed(state, requested) -> (mode, note); stopping_distance_m(state, mode=None, mu=None)
noise_db(state, "footsteps"|"speech"|"actuator", mode=None); quiet_limit_db(state); aid_height_m(state); eye_height_m(state)
# sensors (TIER1_INTEGRATION 1.3)
policy_off(state, "camera"|"mic"); imaging(state); depth_ok(state); can_hear(state) -> "none"|"addressed"|"full"
hears(state, room, loud=False, addressed=False); can_speak(state); sensors_view(state)
set_sensors(w, camera=None, mic=None, policy=None) -> {camera_on, mic_on, privacy_policy}   # policy False/{} clears
# capability problems: None or (message, category)       DEVIATION: tuples, not bare messages
manip_problem(state); arm_problem(state, hand=None); mobile_problem(state, legacy_message=None, stairs=False)
# soil
soil_hand(w, hand, kind, amount); clean_hand(w, hand, kinds=None)      # hand may be "both"
# motion record (TIER1_INTEGRATION 1.4)
motion(ctx, path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None) -> walked cells   DEVIATION (new)
cut_motion(ctx, meters_done, reason, stairs_done=(0, 0)) -> record | None
# engine entry points (BODY-ENGINE calls / registers them)
power_guard(state, spec); quiet_guard(state, spec, ctx=None); settle_energy(w, ctx, own_s)
system_tick(w, dt, tick)            # register as @system("kernel.body", order=72, max_dt=30.0)
robot_stranded(state, baseline)     # register as @invariant("robot_stranded")
ev_battery(w, args, env=None); ev_network(w, args, env=None)   # event effects robot.battery, robot.network
constants: POWER_OFF_TEXT, ENERGY_TEXT, OVERLOAD_TEXT, WEAK_ARM_TEXT, LIMP_LIFT_TEXT, QUIET_TEXT, OVERHEAT_TEXT,
           LOCALIZATION_TEXT, TOO_SMALL_TEXT, POWER_TOO_SMALL_TEXT, THIN_FLAT_TEXT, BATTERY_CUT_TEXT (core's cut message),
           POWERED_DOWN_NOTE, POWER_GRIPS, HANDS
```

`body_faults.py`

```
register_fault_kind(kind, part, apply, detect=None, clear=None, doc="", onset=None)   DEVIATION: onset; apply/clear(robot, record)
fault_id(kind, part=None); FAULT_KINDS; LATENT
add_fault(w, kind, part=None, severity=None, latent=False, source="event", until_s=None) -> fault id
severity_or_default(kind, severity, robot); detect_fault(w, fid, how); clear_fault(w, fid, how)
active_faults(state, detected_only=False); diagnose(w) -> {"faults", "battery_health"}
log_fault(w, kind, text="") -> {"kind", "status": logged|undetected|unfounded|note, "faults"}
slip_check(w, hand, ctx) -> dropped item | None
drop_held(w, eid, height_m, cause) -> landing cell     # impact.fall(w, eid, height_m=, cause=, at=(room, cell), by="robot")
fall(w, cause, room=None, cell=None, height_m=0.0, damage=None, water=False) -> info
self_right_problem(state); expose_water(w, "rain"|"splash"|"spray"|"jet"|"immersion", seconds) -> fault ids
degrade_camera(w, amount=None, clarity=None, cause="", scratch=0.0)                  DEVIATION: scratch
normalize_faults(robot, now=0.0)                                                     DEVIATION: now
ev_fault(w, args, env=None); ev_clear_fault(w, args, env=None)   # event effects robot.fault, robot.clear_fault
```

Built-in kinds (4.11): camera_dirty, camera_scratched, camera_failed, mic_failed, speaker_failed, network_down,
localization_lost, arm_weak, arm_failed, gripper_slip, legs_weak, actuator_failed, battery_degraded,
battery_overheat, water_ingress, software_stuck.

`proprio.py`: `render(env, state, realistic) -> (obs_robot, obs_battery)`; `bucket_camera(clarity)`,
`bucket_load(ratio)`, `bucket_temp(t_c)`, `bucket_soil(slipperiness)`; `LEGACY_KEYS`.

Robot fields declared (TIER1_INTEGRATION 1.1): every body field (materialized: battery ... `_slips` plus
`_hand_kg`), the existing core / navigation fields of `schema.ROBOT_DEFAULTS` (materialized, same defaults; `room`,
`pos`, `hands`, `wearing` not, since normalize writes them), the body's declared switches (`camera_on`, `mic_on`,
`privacy_policy`, `profile_overrides`, `network_until_s`, `water_exposure_s`), navigation's declarations (`stuck`,
`nav_trapped`, `nav_climbed_from`, `home`, `home_pose`, `dock`, `map_known`, `hot_swap`, `nav_foot_soil` bucket,
`lens_scratch` hidden, `battery_reserve_pct`, `idle_drain_pct_h` hidden, `dock_powered`), camera's (`facing_deg`
exact, `view` hidden, both owner "camera", not materialized) and food's (`hand_contamination` hidden).
`apply_profile({}, state)` reproduces `schema.ROBOT_DEFAULTS` value for value (tested).

How later packages use it:

* **BODY-ENGINE**: `schema.normalize` sets `data["robot"] = body.apply_profile(cfg, data)` (after entities and
  rooms are normalized, before positions); registers the system, invariant and the four event effects; calls
  `power_guard` / `quiet_guard` / `settle_energy` per 1.9; on `except ActionError` after the rollback, calls
  `exc.apply(self)` for a `BodyRefusal` (never on the partial-commit path, which rolls nothing back); counts
  nothing else for the energy refusal (its effect adds `stats.energy_refusals`); `ctx.motion = body.motion(ctx, ...)`
  and `ctx.cut_motion = body.cut_motion(ctx, ...)`; `ctx.require_*` raise `BodyRefusal(*problem)` from the problem
  tuples; `ctx.require_grasp` raises `BodyRefusal(problem, "capability", grants=((eid, "touch", ("size_m",)),))` and
  applies `GRASP_AUTO[auto]` (note, extra seconds); `forces.robot_caps` delegates to `capacity_kg` / `limit` /
  `grip_limit_n`.
* **CAM-RUNTIME** may call `register_robot_field("facing_deg", None, "camera", "exact", ...)` / `("view", None,
  "camera", "hidden", ...)`: identical re-declarations are no-ops. `camera.camera_active = body.imaging`,
  `mic_active = can_hear == "full"`, eye height `body.eye_height_m`.
* **BODY-FAULTS**: `perception.observe` takes `obs.robot` / `obs.battery` from `proprio.render`.

## 4. Deviations from the spec

1. **Battery rounding 9 decimals, not 6** (`DIGITS`). Six decimals under-spend a constant drain every substep (a
   60 h parked wait ended 3.2e-3 % high, beyond the spec's own 1e-3 % chunk-independence bound); nine keep the error
   below 1e-5 % over a week and digests stay stable. `battery_temp_c` is kept at 2 decimals so it settles.
2. **Legs threshold inclusive** (`legs_health <= 0.5`): `legs_weak` sets exactly 0.5 and must give "careful only, no
   stairs" (spec 3.2, test 23); "below 0.5" would make the fault inert.
3. **Problem functions return `(message, category)`**: `standing_on` is a safety refusal, the rest capability.
4. **Identical robot-field re-declaration is allowed** (different default/owner/tier/materialize still raises), so
   the camera and the adoption-round domains can declare the fields this package pre-declares (conflict 5 and
   section 6) without a crash.
5. **Body refusals survive the rollback**: `check_lift` (overload stat, event, `body_log.overload_s`, weak-arm
   detection), the energy refusal (`stats.energy_refusals`) and the too-small grasp carry their bookkeeping as
   `effects` and `grants`; the spec asked for these records but a refused step rolls everything back.
6. **Limp-mode lifts** are a capability refusal with their own text and no overload count (4.5); `capacity_kg`
   still includes the x0.5 limp derate (4.7).
7. **A latent weak arm detected by a lift counts as an overload attempt** (the "failing arm" recipe's failure
   predicate `overload_attempts gte 2` needs it).
8. **Total carry uses the loaded hands' arm health** (`max_mass_kg x min(health of hands holding something)`); the
   spec's `min(arm_health)` would forbid a one-armed robot from carrying anything.
9. **`log_fault`**: a match the robot has not detected counts as unfounded too (no evidence); the result carries a
   status. Legacy names map as in 4.11 and are also founded by the robot's own state (gauge low, fallen, ...).
10. **Water ingress fails both cameras and actuators** (the spec's "and/or" resolved to one deterministic rule).
11. **Motion**: `body.motion` holds ctx.motion's logic; the doorway crossing (+1.0 m), door time and a flight of
    stairs form one all-or-nothing terminal block (the robot never starts a crossing it cannot finish); the record
    gains `start` (needed to trim the path in `cut_motion`); stairs are derived from a stairs connection unless given;
    durations equal core's today to the bit (tested).
12. **Profile data** (TIER1_INTEGRATION 1.2 applied): `camera_height_m` replaced by `eye_height_m`; `carry` added;
    `pull_n` 180 (compact 100, same ratio); new `support_half_m` (0.12 / 0.10 / 0.25 m; the spec fixed 0.12 for all,
    wrong for a wheeled base) and `stair_s_per_step` 0.8 (today's core stair time); a `sources` block justifies every
    number.
13. **Soil**: `mud` counts 0.5 towards slipperiness (wet soil, like water); soil at slipperiness >= 0.5 starts a latent
    `gripper_slip` (source "soil") that `clean_hand` clears.
14. **`_slips` counts draws** (a counter that only counted slips would repeat the same draw forever).
15. **New cache `_hand_kg`** (hand -> [item, kg], hidden) beside `_held_kg`, so proprio's per-hand load needs no
    entity scan; proprio recomputes when the hands changed since.
16. **Seated on a live dock nothing is charged** at settlement (also legacy work): the dock carries the load.
17. **Event `power_up`** when the robot wakes (not in the 1.11 list; useful for viewer markers).
18. **Realistic proprio**: registered exact fields pass through (so `facing_deg`, `stuck`, ... appear); `hand_soil`
    renders clean / soiled / slippery; `faults` also lists the robot's own log notes and legacy domain entries (kernel
    test C8); "no proprioception" keeps pos, posture and hands in both modes.
19. **`fall()`** drops held items with cause `robot_fell`; the fall's own cause is in the `robot_fell` info. A fall
    while already down does nothing.
20. **Privacy policy without "when"** applies when the room is occupied (the 1.3 formula).

21. **`body_audit` output files** (fix pass): the default output is `<temp>/house_ai_tier1/body_audit/<mode>.json` only
    for a full run (every spec file, seeds 0 1 2 41 97); a run narrowed by `--spec-files` or `--seeds` defaults to
    `<mode>_partial.json` and its record carries `"partial": true`. Running the quick check of section 8 replaced the
    full baseline with a 14-trial one before. The baseline the body packages compare against is the B0 gate's record:
    `tier1_gate.py`'s G9 now runs `body_audit.py --baseline --out <gate folder>/body_audit.json`, so B0 leaves it in
    `catalog/analysis/tier1/gate/B0/body_audit.json` and BODY-CORE runs `--against` that file.

## 5. Stubs and contracts

No `TIER1-STUB` markers. Contracts consumed (frozen, TIER1_PHYSICS 7.1), imported lazily so the modules stay
import-inert: `phys.support.children_map(state)`, `phys.support.subtree_mass(state, eid, cmap=None)`,
`phys.impact.fall(w, eid, height_m=, cause=, at=(room, cell), by="robot")` (its result's `"cell"`). The unit tests
replace them with local fakes; a one-off check against the in-progress PHYS-STATICS modules (carried mass equals
core's, a dropped ceramic mug breaks, a dropped open kettle spills) passed.

## 6. Gate results

Gate id **BODY-MODEL**, recorded in `catalog/analysis/tier1/gate/BODY-MODEL/` on the shared stage A tree
(`record --id BODY-MODEL --shared-tree`), previous gate A0. Result of `compare A0 BODY-MODEL --package BODY-MODEL`:

```
G0   PASS  action fingerprint unchanged (3e6e94c37334)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G5   PASS  layout counts equal (0 problems); generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312
G7   PASS  substep_ms: 4.38 -> 4.43 (+1.1%; limit 4.599 = prev x 1.05); wait_1h_s: 1.102 -> 1.1368 (+3.2%; limit 1.157)
compare result: PASS
```

The record's checks: G0, G1 (34 files x normal + strict, 3,081 tests, 0 failing runs; `test_body_model.py` 92 tests in
both), G1b (only the failures known from A0; `test_body_model.py` green with `HOUSE_SIM_DOMAINS=core`), G1c (body,
body_faults, proprio import without registering anything), G2-G6, G7 and G9 (`body_audit --baseline` ok) PASS; G8
SKIP (PHYS-INT creates the golden).

* **G7** was first measured while four sibling gates ran on the same machine (7.07 ms per substep, all systems
  slower, thermal the largest share); it was re-run alone (`record --id BODY-MODEL --only G7 --shared-tree`) once
  the machine was idle and passed as above. The body modules are not on any simulator path in stage A.
* **G10** fails on the shared tree only because of files that belong to no stage A package: A0's still uncommitted
  work (`engine.py`, `timeline.py`, `systems/core.py`, `tests/data/gen1_digests.json`, `tier1_gate.py`, the
  orchestration README, `handoff_TIER1-GATE.md`, `gate/A0/*`) and PHYS-DATA's gate folder. On an isolated scratch
  clone with A0 committed as the base (as CAM-SIGHT did), `tier1_gate.py --root <clone> scope --package BODY-MODEL`
  gives `G10 PASS 19 changed file(s); 0 outside the list of BODY-MODEL` (the six package files, this handoff and the
  gate folder).
* No reviewed differences (none allowed or needed), no golden record, no benchmark budget other than G7's.
  Body micro-benchmarks: see section 7.

**Fix pass gate (A-FIX, 2026-10-14, re-recorded by the residual pass).** After the stage-A review the fixes of all
stage-A packages were gated together on the shared tree as `gate/A-FIX/`; the first fix pass saw only part of the
review list, so a residual pass went through all 26 findings and re-recorded the same gate on the final tree:
`tier1_gate.py record --id A-FIX --package A0+PHYS-DATA+PHYS-STATICS+PHYS-FLUIDS+CAM-SIGHT+BODY-MODEL+GEN --prev A0`
(the full gate, 25 min), then G10 again from an isolated copy (below). `compare A0 A-FIX --package <the same>`: PASS.

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  35 test files x normal+strict: 3147 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains: only the 8 failing tests known from A0; kernel files with HOUSE_SIM_DOMAINS=core 10/10
G1c  PASS  22 module(s) imported, none registers anything
G2   PASS  own mode 242/242 (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic 226/242 (1210 trials); vs A0: 0 / 0 / 0 changes; 250 realistic trials identical in G2 and G3
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  240 houses, 0 problems, counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.44 ms/substep (A0 4.38, +1.4 %), 1 h wait 1.113 s (A0 1.102, +1.0 %)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  body_audit ok (record gate/A-FIX/body_audit.json); generator compat, gen-2 tests, gen-2 layout and timing
           (1.00) ok; physics_audit and the camera tool flags not present yet
G10  PASS  151 changed file(s); 0 outside the list of the seven packages (isolated copy)
```

G10 on the shared tree lists 3 files outside every stage-A list: `.gitignore`, `catalog/analysis/tier1/stageA_review.json`
and `tests/test_perception.py`, edited by the main session after the first A-FIX record (its review file, the jsonl
ignore rule and the core-only fix that makes the kernel files 10/10). G10 was therefore recorded from an isolated copy
(`git archive HEAD` committed as the base in a throwaway repository in scratch, plus every other changed or untracked
file of the shared tree; the project repository was not committed to), as the stage-A packages did for A0's
uncommitted work. No reviewed differences (none allowed in stage A, none needed).

## 7. Known gaps

* Settlement with held items costs one physics `children_map` (one O(N) pass; physics estimates 0.15 ms on 900
  entities), above the 0.1 ms settlement budget while carrying. Measured here with the fake mass function: system
  tick 8.7 us idle / 2.4 us covered (budget 20 us), settlement 14 us (100 us), 40-cell prefix 2.7 us (50 us),
  realistic proprio 18 us (50 us). BODY-ENGINE should measure with real physics and, if needed, reuse settle's map.
* `hears()` is the same-room / loud rule; sound propagation is TIME-EVAL's (sweep 6.2).
* Not modeled (spec 1): motor heating (the `lift_capacity` hook is where it would go), wake latency, pack aging,
  partial progress of interrupted non-motion work, people rescuing a powered-down robot.
* Threshold crossings caused by an action are stamped at settlement time (the start of the action's duration);
  only the cutoff inside an idle substep has an exact time.
* `body_audit.py` traces core `pick_up` / `receive` / `uninstall` (nested ones included) but not domain verbs that
  lift without them; the masses are core's current rule (PHYS-INT makes it physics').

## 8. How to test

```
python -m unittest tests.test_body_model                       # 93 tests, about 1 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_body_model
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_body_model
python tools/body_audit.py --baseline                         # all specs x 5 seeds, about 4 min; JSON in <temp>/house_ai_tier1/body_audit/baseline.json
python tools/body_audit.py --baseline --spec-files "manipulation__*" --seeds 0 --workers 1   # seconds; writes baseline_partial.json
python tools/body_audit.py --against catalog/analysis/tier1/gate/B0/body_audit.json   # check mode (BODY-CORE): exit 1 on landing hits
python tools/orchestration/tier1_gate.py record --id BODY-MODEL --shared-tree
python tools/orchestration/tier1_gate.py compare A0 BODY-MODEL --package BODY-MODEL
```

Baseline facts at A0 code (1,210 trials, 0 errors): 2,108 executed picks, none over the derated capacity, no
explicit power grip under 15 mm, none under 2 mm, no auto pinch; lowest battery 17.75 % (T-5ad61b6881821b-01 seed 1,
the one spec with a battery predicate); most time waiting undocked 42 h (A-d66994a566899c-01 seed 0); careful speed
in 14 spec files (examples and perception files of electrical, manipulation, navigation_robot, people, pets,
plumbing_liquids, safety, textiles_laundry, tools_vehicles).

## 9. Follow-ups

* **BODY-ENGINE**: the wiring listed under section 3; `actions.py` ActionSpec defaults (`energy=None`, `arms`,
  `idle`, `power_off_ok`, `noise_db`, `timing`) are read with `getattr` defaults here, so B4 may add them in any order.
  `settle_energy` expects `own_s` without the nested seconds (1.9).
* **BODY-CORE**: `pick_up` order per 1.5 with `check_lift(w, eid, plan["hands"], plan["mass_kg"])`; core `move`
  message on a cut: `BATTERY_CUT_TEXT.format(record["meters"])`.
* **BODY-FAULTS**: perception's imaging / power gates read `body.imaging`, `body.depth_ok`, `body.is_on`; messages
  filtered by `body.can_hear`; realism clarity from `body.clarity`.
* **Domains (adoption round)**: declare the robot fields this package did not (electrical `static_v`, pets
  `pet_waste_on_feet`, safety `carrying_pet`, `gloves_contaminated`); cleaning, appliances_cooking and manipulation
  still append bare strings to `robot.faults` (`gripper_cut`, `gripper_heat_damage`, `gripper_damage`):
  `normalize_faults` only converts them at reset, so they should become typed faults; navigation_robot's
  `log_fault` / `clean_camera` / falls move to `body_faults` (section 6).
* **Main session**: G10 on the shared tree needs A0 committed (section 6). Until BODY-CORE's M5 lands, B4's engine
  still multiplies careful physical seconds by 1.5, so settlement charges that extra hotel time on careful walks; a
  walk planned as affordable can then end in the hidden-buffer power-down instead of completing (rare: it needs a
  nearly empty pack).
