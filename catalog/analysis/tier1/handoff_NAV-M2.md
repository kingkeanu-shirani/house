# Handoff NAV-M2 (stage B4, paired with BODY-ENGINE): navigation_robot on the kernel robot body

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.1, 1.4, 1.7, 1.9 and its rules, 1.10, 1.11,
1.15; 2.4 the NAV-M2 and BODY-ENGINE rows; 3; 5; 6 "navigation_robot"), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_EMBODIMENT.md` (whole; 4.3-4.6, 5, 8, 10 and 12 (M2) are this package's) and
`docs/domains/navigation_robot.md`. Handoff files read (all that existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`,
`handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`,
`handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`
(with its section 10, the B3 merge gate) and `handoff_CAM-RENDER.md`. BODY-ENGINE ran in parallel on disjoint files.
NAV-M2 was written against the frozen contracts (`ctx.motion`, `ctx.cut_motion`, `ctx.require_mobile`, the
`robot_charge_source` hook, the `robot_stranded` invariant: TIER1_INTEGRATION 1.4, 1.10, 1.11; BODY-MODEL section 3)
and tested on the shared tree once BODY-ENGINE's engine wiring had landed there.

## 1. Summary

* The kernel body owns the battery: `traverse.bookkeeping` stops draining it (odometry, coverage and docking state stay)
  and cuts the motion record to what was walked when a trip stops early or the robot falls (`ctx.cut_motion`, the new
  `traverse.walked(trip)`); `robot_system` no longer charges or applies the idle drain; the dock is the body's charge
  source (hook `robot_charge_source`, `robot.charge_source`); `navigation_robot.stranded` is gone (kernel
  `robot_stranded`).
* A battery-cut core `move` is an in-room trip (no passage, stairs or arrival; no approach manners for `approach`);
  `step` uses `ctx.require_mobile` and `ctx.motion`; `go_to` / `wait_aside` report a battery stop; `swap_battery` goes
  through `body.swap_pack`; the gait pace of the consequence engine is the record's effective speed.
* 21 new tests (`TestBodyEnergy`), two updated (`test_battery_runs_out_and_strands`, `test_swap_battery`); the domain doc
  gains section 4a. Gate `NAV-M2` vs B3-MERGE: `compare` PASS, 0 verdict changes in 2 x 1,210 trials, G7 3.04 ms /
  1.083 s; the record's FAILs are outside NAV-M2's files (G1/G1b test pins, G10 the domain doc; section 6).

## 2. Files

Edited (all on the NAV-M2 list of TIER1_INTEGRATION 2.4 except the domain doc):
`house_sim/systems/navigation_robot/traverse.py`, `robot.py`, `paths.py`, `world.py`, `tests/test_navigation_robot.py`,
and `docs/domains/navigation_robot.md` (**DEVIATION**: the task asked the navigation_robot domain agent to update its
domain doc, which is not on the 2.4 list; G10 lists it, see section 6 and request 1).
Created: this handoff, the gate record `catalog/analysis/tier1/gate/NAV-M2/`. Nothing else in the project was touched
(`common.py`, `scenarios.py` and `doors.py` hold related leftovers that are requests, section 9). The four source files
were CRLF on disk (the working copy converter) and are LF now, like the index.

## 3. APIs

"ADDITION" = not named by TIER1_EMBODIMENT 12 / TIER1_INTEGRATION 2.4; **DEVIATION** = differs from them.

**`house_sim/systems/navigation_robot/traverse.py`**

```
Trip(...).crossed, Trip(...).arrival_fell      ADDITION: the passage started; it fell on the landing cell beyond the door
battery_cut(ctx) -> bool                       ADDITION: the action's motion record is cut "battery"
pace(ctx) -> "careful" | "normal" | "fast"     ADDITION: the record's (effective) speed, else body.effective_speed of the
                                               requested speed (TIER1_INTEGRATION 1.7); the gait pace of every trip
walked(trip) -> (meters, (steps_up, steps_down))   ADDITION: what the robot actually walked (body.motion's measure)
bookkeeping(ctx, trip)                         no battery writes; odometry, ctx.cut_motion(done, "fell" | "stopped",
                                               stairs_done) on a stop or fall, incident battery_depleted while the record
                                               still exhausts the pack, docking state (robot.seated_dock), coverage, spot
_after_motion(ctx, kind)                       a battery-cut move (or one that ends in its own room) is an in-room trip
```

**`house_sim/systems/navigation_robot/robot.py`**

```
seated_dock(state) -> dock id | None           ADDITION: the dock whose contacts the robot sits on (robot.dock, then
                                               robot.home, then any robot dock when neither is set)
charge_source(w) -> %/min | None               hook robot_charge_source: the seated dock's charge_rate_pct_per_min
                                               (robot_dock 2.0) while that dock is live (dock_powered, judged now)
robot_system(w, dt, tick)                      no charging, no idle drain; robot.dock_powered follows the seated dock
dock(ctx)                                      no `charging` write (the body sets it); message "the battery is too
                                               cold|hot to charge yet" outside the pack's charging window (ADDITION)
undock(ctx)                                    no `charging` write
swap_battery(ctx)                              DEVIATION (pulled forward from "later"): body.swap_pack(w, charge,
                                               temp_c=pack temperature)
battery_needed(state, meters, steps_up=0, steps_down=0, speed=None, load_kg=0.0) -> gauge points   DEVIATION: was
                                               0.012 %/m + 0.05 %/step; now the body's walking, stair and hotel energy
```

**`house_sim/systems/navigation_robot/paths.py`**

```
step(ctx)                                      ctx.require_mobile("Robot cannot move") + ctx.motion([c], start,
                                               speed=requested, extra_s=deliberate-step time); pace from T.pace; an
                                               unaffordable step leaves the robot in place: body.BATTERY_CUT_TEXT
go_to(ctx)                                     ADDITION: "Stopped in <room> (battery_depleted)" when the last leg's
                                               battery cut powered the robot down (was "Arrived at ...")
wait_aside(ctx)                                ADDITION: "Stopped at [c, r] (battery_depleted)" when the walk to the spot
                                               was cut
```

**`house_sim/systems/navigation_robot/world.py`**: the invariant `navigation_robot.stranded` is removed (kernel
`robot_stranded`, registered by BODY-ENGINE).

Contracts consumed (BODY-ENGINE / BODY-MODEL): `ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0,
connection=None)`, `ctx.cut_motion(meters_done, reason, stairs_done=(0, 0))`, `ctx.require_mobile(legacy_message,
stairs=False)`, `ctx.data["motion"]` (fields `cut`, `exhausts`, `meters`, `speed`, `start`, `path`), hook
`robot_charge_source` (`fn(w) -> %/min | None`), `body.swap_pack`, `body.effective_speed`, `body.is_on`,
`body.BATTERY_CUT_TEXT`, `body.CONNECTION_CROSSING_M`, the profile tables (`walk_wh`, `stairs_wh`,
`activity_power_w`, `full_wh`, `cutoff_soc`, `profile`), the kernel invariant `robot_stranded` and the engine order of
TIER1_INTEGRATION 1.9 (energy settled after the AFTER hooks, so traverse's cut lands before the charge).

## 4. Deviations from the spec

Each is the more realistic and simpler option, or a fix needed to make the M2 items hold.

1. **The domain doc** (`docs/domains/navigation_robot.md`) is updated although 2.4 does not list it: section 4a "The
   robot body", the step / dock / undock / swap_battery / go_to rows, the energy paragraph of section 3, the systems
   table, the harms and recipe J. The gate's ADOPT rows already give domain docs to their domains.
2. **What a stop or a fall cut walks.** TIER1_EMBODIMENT 12 says only "calls ctx.cut_motion when a trip stops early or the
   robot falls". `walked(trip)` measures it like `body.motion` measures the record: the in-room path up to the stop or
   fall cell; once the passage started (`trip.crossed`), the whole path plus the 1.0 m crossing and the flight; a fall on
   the stairs pays half the flight (the fall's drop height already assumes mid-flight). Reasons: `"stopped"`, `"fell"`.
   A fall on the landing cell after a doorway (`trip.arrival_fell`) cuts the record too (the whole walk). The action's
   duration is unchanged (the robot also spends time stopping, stumbling or falling); only the walking work shrinks.
3. **`charging` is no longer written by this domain** (`dock`, `undock`, `bookkeeping`): TIER1_INTEGRATION 1.1 makes the
   body its writer. The body system sets it at the next substep, which is inside the same step (the dock's 20 s, the
   undock's 6 s), so observations and evaluations see the same value as before. `robot_falls` (common.py, not this
   package) still writes it (request 3).
4. **`swap_battery` through `body.swap_pack`** (TIER1_EMBODIMENT 12 lists it under "later"). After BODY-ENGINE the body is
   the battery's only writer (1.1); a raw write would leave `battery_min_pct`, the power state and threshold crossings
   wrong (a swapped-in empty pack would leave the robot "on" at 0 %). The new pack is a healthy one at the pack's own
   temperature; its capacity stays the installed pack's (the 2.4 kWh pack decision is the later item).
5. **The gait pace is the effective speed** (`traverse.pace`): the record's speed when the action wrote one, else
   `body.effective_speed` of the requested speed, for every trip, for `_pre_motion`'s low-light rule and for `step`'s
   duration. TIER1_INTEGRATION 1.7 lists every speed consumer; the consequence engine is one, and BODY-ENGINE's core
   travel time already follows the record. It differs from the requested speed only in limp mode (gauge at or below
   5 %, weak legs) or quiet mode at fast; no reference run reaches either (section 6).
6. **The charge source is the dock the robot sits on, judged now.** `seated_dock` tries `robot.dock` (the last dock
   used), then `robot.home`, then any robot dock; before, `robot_system` charged only on `dock_of` (home first), so a
   robot docked on a second dock never charged (pre-existing bug, fixed with a test). The hook computes the dock's power
   on each call (an outage or a tripped breaker stops the current at once, and a robot docked at reset is on its charge
   source from its first action) instead of reading `robot.dock_powered`, which `robot_system` keeps for the observation.
7. **go_to and wait_aside report a battery stop.** A cut on go_to's last leg used to end in "Arrived at X"; a cut on the
   way to the waiting spot in "Waiting aside at ...". Both now say "Stopped in <room> (battery_depleted)" / "Stopped at
   [c, r] (battery_depleted)" (ok, powered down). A cut on an earlier leg already ended in go_to's existing "Stopped in
   <room> (<last incident>)", whose incident is now `battery_depleted`.
8. **bookkeeping keeps the incident `battery_depleted`** (room and cell, as before) when the record still exhausts the
   pack after any stop or fall, so `obs.nav.last_incident` and go_to's message name it. Dropped with the drain: the
   `nav_stranded` stat, the `{"kind": "battery_depleted"}` fault entry and the `battery_below_reserve` log (the body's
   `power_down` / `battery_reserve` events, `stats.power_downs*` and `battery_reserve_breaches` replace them).
9. **A core move that ends in its own room for any reason** is an in-room trip, not only a battery cut (TIER1_EMBODIMENT
   0.1 found that `_after_motion` treated every move as a doorway passage).
10. **`step` keeps its durations** (0.5 m / speed x 1.2, x 1.6 over; the diagonal step takes as long as an orthogonal
    one, as before). The record gets the deliberate step's extra time as `extra_s` (never negative), so a step whose
    walk is affordable but whose handling time is not walks and then powers down. An unaffordable step takes 0 s (the
    record's) and leaves the robot where it stands. The legacy `energy=0.002` is ignored (a motion record exists).
11. **`battery_needed`** returns gauge points by the body's rules (so a planner compares it with `body.gauge`), with
    `steps_down`, `speed` and `load_kg`; it had no caller.
12. **`dock` in the cold.** The message says "the battery is too cold|hot to charge yet" when the dock is live but the
    battery management refuses current (pack below 0 C or above 45 C, or overheating); it said "; charging".

## 5. Stubs and contracts

No `TIER1-STUB` marker added or left by NAV-M2 (`grep -rn "TIER1-STUB" house_sim/systems/navigation_robot` is empty).
Contracts consumed: section 3. For later packages:

* **NAV-M5** (B5, `paths.py`, `follow.py`): `timing="speed"` on `step`, `go_to`, `follow`, `lead`, `patrol`. `step`'s
  duration still uses the 0.4 / 0.8 / 1.2 m/s gait table of `common.SPEED_MPS` at the effective pace; with
  `timing="speed"` it should move to `body.speed_mps` (the profile's speeds) so a `mobile_manipulator` steps at its own
  pace.
* **BODY-CORE / BODY-FAULTS**: `obs.nav.battery_pct` is still the true state of charge (equal to the gauge at and above
  15 C); the realistic gauge belongs to the adoption round's `obs.nav` through proprio.
* **Adoption round (navigation_robot)**: the "later" items of TIER1_EMBODIMENT 12 except `swap_battery`, which landed.

## 6. Gate results

Gate id **NAV-M2** (`catalog/analysis/tier1/gate/NAV-M2/`), previous gate **B3-MERGE**, recorded on an isolated copy so
that G10 sees only stage B4: the B3 tree (BODY-ENGINE's 16:17 snapshot of the shared tree, checked identical to the
shared tree outside the B4 files) committed as the base of a throwaway repository in my scratch folder, then the final
versions of the 21 B4 files (BODY-ENGINE's and NAV-M2's) on top; byte-identical to the shared tree at hand-over (every
B4 file compared). The project repository was only read.

```
python tools/orchestration/tier1_gate.py --root <scratch>/t1_NAV-M2/b3tree --gates-root catalog/analysis/tier1/gate
       record --id NAV-M2 --package NAV-M2 --prev B3-MERGE --shared-tree --jobs 7
       --out catalog/analysis/tier1/gate/NAV-M2 --work <scratch>/t1_NAV-M2/gate_work_final
```

Summary line: `TIER1-GATE NAV-M2 [NAV-M2] vs B3-MERGE: FAIL (8 pass, 3 fail, 2 skip)`. `compare` passes; the three
FAILs are outside NAV-M2's files:

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 482f9adf9717 unchanged (425 actions, 111 systems: + `kernel.body`) |
| G1 | FAIL | 42 files, 3,471 tests. `test_navigation_robot.py` 180 tests green normal and strict. Failing (10 runs, all outside NAV-M2): `test_body_model.py` (2 tests: proprio keys, legacy-state defaults), `test_physics_forces.py` (robot-record force overrides), `test_camera_actions.py` (`face` with `battery 0.0` now meets PowerOff): BODY-ENGINE's requests 1-3; `test_camera.py` (CAM-RENDER's removed guard, since B3) and `test_textiles_laundry.py` (PHYS-INT's R7 jug bound, since B1) |
| G1b | FAIL | the 9 isolated-domain failures known from B3-MERGE (navigation_robot's one is A0's `test_furnished_robots_sit_on_their_docks_inside_the_grid`); kernel files with core only 12/16: the four pinned files above |
| G2 | PASS | own mode 242/242 (1,210 trials); vs B3-MERGE 0 verdict, 1,210 state-digest (allowed), 1,186 observation-digest (allowed) changes |
| G3 | PASS | forced realistic 226/242 (as B3-MERGE); 0 verdict, 1,210 state-digest, 1,185 observation-digest changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room and geometric: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.04 ms per substep (B3-MERGE 3.05, limit 3.203), 1 h wait 1.083 s (1.081, limit 1.135); `navigation_robot.robot` 0.009 ms (unchanged), `kernel.body` 0.023 ms (BODY-ENGINE's) |
| G8 | PASS | room golden and golden_all (re-recorded by BODY-ENGINE with NAV-M2's code in the tree, B-R5) |
| G9 | SKIP | no extras for NAV-M2 (BODY-ENGINE's union record runs `body_audit`: ok) |
| G10 | FAIL | 21 changed files: NAV-M2's 5 listed files, 15 attributed to BODY-ENGINE (`--shared-tree`), 1 outside: `docs/domains/navigation_robot.md` (deviation 1, request 1) |

`python tools/orchestration/tier1_gate.py compare B3-MERGE NAV-M2 --package NAV-M2`:

```
compare B3-MERGE -> NAV-M2 (packages: NAV-M2; allowed: obs,state,verdict; codes: B-R1,B-R2,B-R3,B-R4,B-R5,B-R6)
G0   PASS  action fingerprint unchanged (482f9adf9717)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1210 with changed state digests (allowed), 1186 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1210 with changed state digests (allowed), 1185 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 375 -> 375
G7   PASS  substep_ms: 3.05 -> 3.04 (-0.3%; limit 3.203 = prev x 1.05)
           wait_1h_s: 1.081 -> 1.083 (+0.2%; limit 1.135 = prev x 1.05)
           largest per-system increases: kernel.body +0.023 ms
G1   INFO  new test file test_body_engine.py (normal): 46 tests
           new test file test_body_engine.py (strict): 46 tests
           test_navigation_robot.py (normal): ran 159 -> 180, skipped 1 -> 1
           test_navigation_robot.py (strict): ran 159 -> 180, skipped 1 -> 1
compare result: PASS
```

The record is identical in every check to BODY-ENGINE's union record (`gate/BODY-ENGINE/`, `--package
BODY-ENGINE+NAV-M2`, its second run on the same final tree: compare PASS, G7 3.03 ms / 1.083 s). An earlier NAV-M2 run
(17:10 tree) overlapped BODY-ENGINE's first record; that overlap is the likely reason its first G7 read 3.22 ms (over
budget). Both final records were taken alone.

### Reviewed differences (3.3)

No verdict changed in either mode, so no row is needed. Digest changes, all allowed classes:

| Code | Catalog ids | Mode | Trials | What changed (NAV-M2's part) |
|---|---|---|---|---|
| B-R2 | all specs | both | state, obs | battery trajectories: navigation no longer drains the battery a second time (0.012 %/m and 0.05 %/step), the body charges each walk once from its motion record (walking and stair work plus hotel load), stops and falls pay only what was walked, the dock charges through the body (CC/CV, the dock carries the load, so docked actions are free); `obs.nav.battery_pct`, `obs.robot.battery` and `obs.battery` follow |
| B-R3 | (none in reference runs) | both | - | the failure label `robot_stranded` replaces `navigation_robot.stranded`; no verdict depends on it: the lowest battery of any reference run is 17.69 % (T-5ad61b6881821b-01 seed 1, BODY-ENGINE's G9 `body_audit`) |
| B-R6 | T-5ad61b6881821b-01, A-d66994a566899c-01 | both | hand check | all 5 seeds pass in both modes. T-5ad61b6881821b-01: battery at the end 22.36-22.53 % (min 17.69-17.86 %; success needs > 19), `charging` true, 112 Wh charged in the 140 s on the dock (the old robot_system gave the same 2 %/min). A-d66994a566899c-01: seed 0 waits 42 h undocked, 857 Wh used (35.7 %), ends at 64.3 %; the other seeds end at 96.8-98.8 % |

B-R1 (materialized keys), B-R4 (legacy faults) and B-R5 (golden re-record) are BODY-ENGINE's.

### Golden (3.5)

BODY-ENGINE dumped the golden before B4 and re-recorded it with NAV-M2's code in the tree (G8 PASS on the final tree).
To separate NAV-M2's share I dumped the final B4 tree with and without NAV-M2's four source files (`tools/golden.py dump
--all` on two copies of the isolated tree) and diffed them (`golden.py diff`):

```
274 file(s) compared, 250 changed, 12 path pattern(s)
   242  robot.battery_min_pct          239  robot.battery           26  robot.energy_wh_used
    26  stats.energy                     8  obs.battery               8  obs.nav.battery_pct
     8  obs.robot.battery                4  robot.battery_temp_c      1  robot._idle_s
     1  robot.charging                   1  robot.energy_wh_charged   1  robot.standby
```

Every path is battery bookkeeping (B-R2): the removed second drain (`robot.battery`, `battery_min_pct`: run 0 oracle
ends at 98.774 % instead of a double-drained 98.639 %), the first actions of a robot that starts on its live dock being
free (`energy_wh_used` / `stats.energy` -0.033 Wh: the 1 s `look` of the golden script), the pack's self-heating, and
T-5ad61b6881821b-01 charging through the body (`charging`, `energy_wh_charged` 112 Wh, `standby`, `_idle_s`). No
`nav.*`, entity, room or other domain path changed: the consequence engine behaves as before on every golden run. The
union diff B3 -> B4 (BODY-ENGINE's `golden_before` vs the final dump) has 30 patterns: the B-R1 keys, these B-R2 paths,
and one `people.*._space_jn` journal-length marker (BODY-ENGINE's).

### Other checks (beyond the gate)

* `python -m house_sim --check tasks/authored/navigation_robot__examples.json --workers 4`: 12/12 pass.
* Low-battery navigation fuzz (scratch `lowbat_fuzz.py`): generated houses seeds 0-5 x oracle/realistic x room/
  geometric x battery 0.6 / 3.0 %, 60 random motion and dock verbs each (`step`, `go_to`, `move`, `walk_to`,
  `approach`, `dock`, `undock`, `wait`, `wait_aside`, `patrol`), `HOUSE_SIM_STRICT=1`, each run twice: 48 runs, 0
  internal errors, 0 nondeterministic runs, the battery never rose without a dock.
* Low-battery realistic leak audit (scratch `lowbat_audit.py`, `tools/perception_audit.py`'s `check_message` /
  `check_obs` / `check_camera_obs` on every step): 8 houses x room/geometric x 80 steps with an undocked robot at
  1-3 %: 0 leaks, 0 errors, 281 battery-cut or powered-down messages checked.
* Micro-benchmarks (scratch `bench.py`, seeds 0-3): `charge_source` 0.0006 ms per call docked or not (the body calls it
  once per substep and once per settlement), `robot_system` 0.0011 ms per substep in isolation (the B4 per-substep budget for body work is 0.02 ms;
  navigation's own system did not grow in G7: 0.009 ms, as in B3-MERGE).

## 7. Known gaps

* **`obs.nav.battery_pct`** shows `robot.battery` (the true state of charge), not the gauge; they differ only with the
  pack below 15 C (adoption round: `obs.nav` through proprio).
* **Domain code outside this package still touches body fields** (requests 2-4): `doors.ev_robot_fault` kind `battery`
  sets `robot.battery` directly (should be the kernel's `robot.battery` event), `common.robot_falls` writes
  `charging` / `docked` and appends a legacy fault dict (later: `body_faults.fall`), `scenarios.dock_setup(idle_drain=)`
  still writes the deprecated `idle_drain_pct_h`, and `common.BATTERY_PCT_*` / `BATTERY_RESERVE_PCT` are unused.
* **`step`'s duration** uses `common.SPEED_MPS` (humanoid_v1 speeds) and an orthogonal step's time for a diagonal step,
  as before; the motion record and the energy use the profile (NAV-M5 / adoption).
* **`wait_aside`** is priced as an awake control action (115 W standing over all its seconds) because it "stays
  responsive"; a parked variant would be `ActionSpec(idle=True)` with the nested walk's seconds excluded from the idle
  integration (a kernel rule; documented in the domain doc, not changed).
* **Durations of cut walks**: a stopped or fallen walk keeps its planned duration (hotel energy over it); only a battery
  cut shortens the time (body.motion's prefix seconds).
* **Battery cuts during `follow` / `lead` / `patrol`**: the nested leg powers the robot down and the next leg is refused
  (`PowerOff`), so the step commits with the engine's "Powered down partway: ..." message (TIER1_EMBODIMENT 4.6), not a
  domain message like go_to's.

## 8. How to test

```
python -m unittest tests.test_navigation_robot                         # 180 tests, about 1 min
HOUSE_SIM_STRICT=1 python -m unittest tests.test_navigation_robot
HOUSE_SIM_DOMAINS=navigation_robot python -m unittest tests.test_navigation_robot   # 1 known failure since A0
python -m unittest tests.test_navigation_robot.TestBodyEnergy tests.test_navigation_robot.TestDockBattery   # NAV-M2's own
python -m house_sim --check tasks/authored/navigation_robot__examples.json --workers 4
python tools/orchestration/tier1_gate.py compare B3-MERGE NAV-M2 --package NAV-M2
```

Manual checks (oracle):

```python
from house_sim.generator import generate_house
from house_sim.kernel.engine import HouseEnv
env = HouseEnv(seed=0)                  # generated robots start seated on their live dock: actions there are free
env.step("undock"); env.step("go_to", "<another room>")
env.world.s["robot"]["battery"], env.world.s["robot"]["energy_wh_used"]   # the body's Wh, paid once per leg
cfg = generate_house(0); cfg["robot"].update(battery=0.04, docked=False)
env = HouseEnv(cfg, seed=0); env.step("go_to", "<a far room>")
# ok, "Stopped in <room> (battery_depleted)"; robot.power_state "off"; env.evaluate()["failures"] has robot_stranded
```

## 9. Follow-ups

Requests to the main session (files outside NAV-M2):

1. **Add `docs/domains/navigation_robot.md` to NAV-M2's file list** in `tools/orchestration/tier1_gate.py` (PACKAGES) and
   TIER1_INTEGRATION 2.4 (the domain agent keeps its doc current; the ADOPT rows already include it). Until then G10
   names it as the one file outside the list.
2. **`house_sim/systems/navigation_robot/doors.py` `ev_robot_fault`**: kind `battery` writes `robot.battery` directly;
   it should set the gauge through the body (`body.ev_battery(w, {"level": ...})`, the kernel event `robot.battery`) so
   the lowest gauge, threshold crossings and power states follow. Adoption round (TIER1_INTEGRATION 6 already lists
   "ev_robot_fault delegates to robot.fault").
3. **`common.py`**: `robot_falls` writes `charging` (body-owned) and a legacy fault dict (later item: `body_faults.fall`);
   `BATTERY_PCT_PER_M`, `BATTERY_PCT_PER_STEP_UP/DOWN` and `BATTERY_RESERVE_PCT` have no reader any more; the module
   docstring's "0.012 % per meter" line is stale. Adoption round.
4. **`scenarios.py` `dock_setup(idle_drain=...)`** writes the deprecated `idle_drain_pct_h` (ignored now); drop the
   argument or map it to a profile override of the standby power. No authored spec passes it.

For later packages: NAV-M5 and BODY-CORE / BODY-FAULTS items in section 5. Domain follow-ups: none new beyond the
TIER1_INTEGRATION 6 navigation_robot list (all its "later" body items except `swap_battery`, which landed here).

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
