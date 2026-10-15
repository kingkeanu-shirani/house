# Handoff CAM-RUNTIME (stage B2): the geometric camera at runtime

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.3, 1.4, 1.9-1.13 and conflicts 4-9, 11-13,
19-22, 26, 35, 38, 42, 43; 2.4 B2 row; 3; 5), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_CAMERA.md` (whole;
this package is WP-2 minus the golden and `world.py`: sections 2, 4.1, 4.7, 8, 9.3, 10.2, 10.3, 10.6, 12, 13, 16,
17), `docs/design/TIER1_EMBODIMENT.md` (the sensor, eye-height and standby parts). Handoff files read (all that
existed): `handoff_TIER1-GATE.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`,
`handoff_CAM-SIGHT.md`, `handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_B0.md`, `handoff_PHYS-INT.md`. Their
follow-ups addressed to CAM-RUNTIME are acted on: CAM-SIGHT 5/9 and PHYS-STATICS 9 (the sight stand-ins are gone,
`camera.HW = sight.HW`, viewers with `ignore=("robot",)`, the World memo, one children map per step view, can_see as
LOS + px + IR, `floor_visible_ahead`, coverage with the 0.12 light rule, the robot-field registrations), BODY-MODEL 3
(`camera_active = body.imaging`, `mic_active = can_hear == "full"`, eye height from the body), PHYS-INT 5 (motion
frames read `actions.motion_record(s)`; `emit_consequence` routed through `camera.visual_event`).

## 1. Summary

* New `house_sim/kernel/camera.py`: the camera mode and its install / strip, the step's frames (begin / end of step,
  motion and posture frames from motion records, the relocation fallback, auto-gaze), `can_see` with the body's
  sensor rules, wrist access, the step view and ViewInfo, the camera log (coverage, looked-in, sightings, frame-based
  privacy captures with the kernel fallback), the `camera.watch` system, visual events, read / inspect checks,
  device feeds, lidar, the `robot_keep_awake` hook.
* Engine, perception and Ctx wired per TIER1_INTEGRATION 1.9 / 1.10 / 1.12: `HouseEnv(camera=)`, the geometric
  observation (only detected things, `view` dicts, `obs.camera`, `obs.lidar`, `obs.robot.facing_deg`), save / load /
  replay of the mode, `action_names()` hiding `face` / `peek` in room mode, the realistic + geometric guard.
* `sight.py` reads the body and the physics layer directly (stand-ins deleted) and memoizes occlusion per geometry
  epoch from the touch log; `phys/observe.emit_consequence` reports every physics consequence to the camera.
* Room mode is untouched: golden, verdicts and digests identical (section 6). 81 tests in `tests/test_camera.py`,
  green normal, strict and core-only.

## 2. Files

Created: `house_sim/kernel/camera.py`, `tests/test_camera.py`, this handoff, the gate record
`catalog/analysis/tier1/gate/CAM-RUNTIME/`.
Edited: `house_sim/kernel/engine.py`, `house_sim/kernel/perception.py`, `house_sim/kernel/actions.py`,
`house_sim/kernel/sight.py`, `house_sim/kernel/phys/observe.py`, `docs/KERNEL_API.md` (section "v3.0 camera").
All on the CAM-RUNTIME list of TIER1_INTEGRATION 2.4 (G10, section 6).

## 3. APIs

"ADDITION" = not in TIER1_CAMERA 10.2 / 10.3 or TIER1_INTEGRATION 1.10; **DEVIATION** = differs from them.

**`house_sim/kernel/camera.py`**

```
HW (= sight.HW), GEOMETRIC_ONLY = {"face", "peek"}, MODES, DEFAULT_VIEW, LOG_DEFAULTS, constants (section 4)
default_mode(perception=None) -> "room"                       # D3 switches realistic to "geometric"
resolve_mode(camera=None, task=None, perception=None) -> mode # ADDITION: the 1.13 precedence; ValueError otherwise
mode(state) -> "room" | "geometric"                           # geometric exactly when robot.view is a dict
install(state, overrides=None); strip(state); complete_log(log) -> camera_log  # complete_log: ADDITION
hw(state) -> merged hardware; eye(state) -> (rid, x, y, z); facing(state); head_yaw(state); head_tilt(state)
head_viewer(state, frame=None) -> sight.Viewer; wrist_viewer(state, eid, where=None) -> Viewer | None
camera_active(state) == body.imaging(state); mic_active(state) == (body.can_hear(state) == "full")
begin_step(w); add_frame(w, kind, yaw=None, tilt=None, cam="head", speed=None, at=None)      # at: ADDITION
note_motion(w, room, path, speed, end_face=None, start=None)                                 # start: ADDITION
note_action(ctx)                     # ADDITION: motion frames of any action / nested leaf that moved the robot
gaze_to(ctx, target) -> seconds; auto_gaze(ctx, eid) -> seconds                             # auto_gaze: ADDITION
can_see(state, eid, world=None, perception=None) -> bool     # DEVIATION: world / perception (memo, L0 privacy rule)
wrist_access(state, eid, where=None) -> (ok, reason); map_fixture(state, eid) -> bool         # map_fixture: ADDITION
in_frame(state, eid, world=None, current_pose=False) -> bool                                 # DEVIATION: world, current_pose
acuity(env_or_state, eid) -> {"level", "px", "view"} | None
step_view(env, fresh=False) -> StepView; compute_view(state, world=None, perception=None, frames=None)  # compute_view: ADDITION
end_step(env) -> StepView | None
inspect_check(state, eid, world=None) -> (ok, reason, via)                                   # DEVIATION: world
read_check(state, eid, fields, world=None) -> (ok, reason, via, readable_fields)              # DEVIATION: world
visual_event(w, kind, *, source=None, room=None, at=None, z=None, size_m=0.3, visual=None, emit=True, **data) -> entry
witness(w, kind, source=None, room=None, at=None, z=None, size_m=0.3, visual=None) -> bool | None   # ADDITION
floor_visible_ahead(state, d_m); seen_floor(state, world=None) -> {(rid, (c, r))}; specular(state, eid, world=None)
device_frame(state, eid) -> [ids]; bearing_to(state, xy) -> deg; lidar(state, world=None) -> dict | None  # lidar: ADDITION
note_peek(w, eid, where)                                      # ADDITION: camera_log.peeked / looked_in for CAM-ACTIONS
private_activity(state, pid) -> bool; coverage_of(state, rid, cells); ambient_light(state, rid, world=None)  # ADDITIONS
compass(deg); view_dict(info); camera_block(state, sv); lidar_block(sv)                      # ADDITIONS (perception)
system camera.watch (order 195, max_dt 30); hook robot_keep_awake(w) -> bool; after-hooks (order 900) on move,
approach, walk_to, crouch, kneel, stand, get_up; robot fields facing_deg (exact) and view (hidden), owner "camera"
```

`StepView` slots: `key` (steps, time_s, mutation_count), `rid`, `eye`, `frames` (this step's), `current` (indices of
the rendered frames; None = the implicit frame at the current pose), `entities` / `people` / `pets` (`{id: ViewInfo}`),
`seen` (`{rid: set of cells}` surveyed by every frame), `glare`, `light`, `lidar`, `imaging`, `depth`, and after
`end_step` `looked_in`, `sightings`, `captures`; `detected_ids()`. ViewInfo (10.6): `level` (shape | detect |
recognize | identify), `detected`, `px`, `range_m`, `bearing_deg`, `elev_deg`, `visible`, `light`, `glare` (none |
frame | sun | backlit), `specular`, `faces`, `via` (head | wrist | depth | held), `frame`, `frame_yaw`.

**`house_sim/kernel/engine.py`**: `HouseEnv(..., camera=None)` and `env.camera_mode`; reset installs or strips;
`camera.begin_step(w)` after the step counter (and on both refusal paths); `camera.note_action(ctx)` after the AFTER
hooks in `step` and `perform`; `view = camera.end_step(self)` after `realism.commit_grants`, then
`realism.note_step(view)` when the realism object has it; `action_names()` hides `GEOMETRIC_ONLY` in room mode;
`save` / `save_replay` record `"camera"`, `load` / `replay` pass it (missing: "room"); the realistic + geometric guard
(`NotImplementedError`, marked `TIER1-STUB(CAM-RENDER)`).

**`house_sim/kernel/perception.py`**: `Perception.visible` is `camera.can_see` in geometric mode;
`Perception.contained(state, eid)` (ADDITION: L0, 9.3); `Perception._geometric(...)` builds the geometric
`visible_entities` / `people` / `pets` / hazards; `obs.camera`, `obs.lidar`; `ROBOT_KEYS` gains `facing_deg`;
`light_level(state, rid, ids=None)` (ADDITION `ids`: a kept window / light list, same sums).

**`house_sim/kernel/actions.py`**: `ctx.camera_mode`, `ctx.can_see(eid)`, `ctx.in_frame(eid)`, `ctx.gaze_to(target)`,
`ctx.acuity(eid)`, `ctx.read_check(eid, fields)`, `ctx.inspect_check(eid)`; `Ctx.obj` ends with `camera.auto_gaze`
in geometric mode (1.10 order).

**`house_sim/kernel/sight.py`**: `eye_height_m` = `body.eye_height_m`; `carry_geometry(state)` (ADDITION: the body
profile's carry block); the held envelope and resting sizes from `phys.support`; `glare(..., sources=None)`,
`specular(..., sources=None, cmap=None)` (ADDITION parameters); `sample_transmissions(state, eid, eye, geom=None,
vs=None, cmap=None, only=None)`, `seen_floor_cells(state, viewer, geom=None, max_m=None, min_light=None,
ambient=None, beams=(), vs=None)`, `geometry_epoch(world)`, `TOUCH_CONSUMER = "sight"` (ADDITIONS).

**`house_sim/kernel/phys/observe.py`**: `emit_consequence` writes `w.emit(...)` as before and in geometric mode calls
`camera.visual_event(..., emit=False)` with the target, the payload's room and cell, and the apparent size
(`_event_size`: the target's largest side, at least 0.3 m).

## 4. Deviations from the spec

Each was chosen as the more realistic and simpler option.

1. **Approach (C19) is the camera's after-hook.** The approach after-hook ends the walk facing the target with the head
   aimed at it (at no extra time), also when it was already within reach. CAM-ACTIONS' row lists "approach ends facing
   the target"; nothing is left for it to do there.
2. **The arrival pose is a stationary frame.** 8.2 samples "every 2 path cells plus the final pose" as motion frames.
   The robot has stopped at the end, so the final pose is an `idle` (or, for approach, `gaze`) frame without motion
   blur; that is the frame `observe()` renders after a walk (motion frames are never rendered, C4).
3. **Motion frames for every verb.** Besides the after-hooks on `move` / `approach` / `walk_to` (10.2), the engine
   calls `camera.note_action(ctx)` after every action's AFTER hooks (top level and nested leaves), so a domain verb
   that sets `ctx.data["path"]` (or, from BODY-ENGINE on, `ctx.motion`) gets motion frames as 1.4 asks. Nested core
   moves are framed by their own after-hook; a composite verb whose leaves walked has no own record (PHYS-INT
   deviation 8), so nothing is framed twice.
4. **Arrival facing after a room change**: the arrival cell's `wall_inward` when it is on the perimeter (a doorway),
   else toward the room centre (a relocation into the middle of a room).
5. **Auto-gaze costs nothing for what the current head pose already frames** (with line of sight), not only for what
   a frame recorded this step; at the start of a step the head has not moved, so a target in view needs no turn.
   "Once per target per step" is kept outside the state (per World and step number), so a gaze that could not frame
   its target (pan limit with a fallen robot) is not charged twice.
6. **No frame yet means the current pose.** `in_frame`, `acuity` and the observation use an implicit frame at the
   current pose when the step has no stationary frame at the current eye (`observe()` right after `reset`, or before
   any action wrote a frame); `end_step` always records a frame at the final pose.
7. **ViewInfo `px` is the effective pixel count** (after lens, light, haze, contrast and the visible fraction; the
   detection band and levels use it); `via` is "held" for held and worn items (always seen, `frame` None).
8. **Oracle numbers**: the oracle `view` dict and `obs.camera.light` carry the exact light value (3 and 2 decimals)
   rather than a bucket word (10.6 "Oracle: exact values"); CAM-RENDER buckets them for realistic perception.
   `obs.camera.frames` counts every frame of the step.
9. **Captures**: one per person per private moment whichever sensor took it first (spec key `pid:activity:start_s`
   has no sensor). People records carry no activity start, so the episode is `activity_start_s` when a domain records
   it, else `activity_until`, else "-" (one per activity). Each capture also writes an event-log entry
   `camera_capture` (person, room, sensor) for the viewer and audits.
10. **Watch accounting**: monitored agents are the people and pets in the robot's room plus `view.track` and
    `camera_log.track_ids`; an agent is "in view" at the oracle detection rule in the current head frame; `gap_s` is
    the running gap and `max_gap_s` its maximum. Tracking picks the head yaw that frames the most tracked agents (ties
    rotate every 30 s of simulated time); the body turns only during idle actions (`wait`, `look`, `listen`, the
    speech verbs, `ask`, `watch`) and when it can turn; otherwise the pan is clamped.
11. **view_watch** entries also keep `last_s`; `robot_in_view_s` adds `every_s` per evaluation in which the robot is
    in the viewer's frame; `clear` means no target is visible; aperture viewers (phase 3) are ignored.
12. **looked_in** is judged for the frames taken in the robot's current room (motion frames of the room just left are
    not checked for containers).
13. **Seen cells, sightings and captures** are judged at the end of the step with the state then; the robot's imaging
    is evaluated per frame room (a room privacy policy applies to the room a frame was taken in). Hazards are listed
    on cells any frame of this step surveyed in the current room (motion frames included).
14. **can_see details**: the IR shape rule drops its bearing condition (the robot can turn, as for `head_detectable`);
    with imaging off, map fixtures need the robot powered (1.3: power off leaves only held items); the light at the
    target counts the robot's lights aimed at it.
15. **sight stand-ins deleted, `_BRIDGES` kept for tests.** `test_sight.py`'s `setUpModule` sets the three bridge keys
    to None to test the stand-ins; None now means "the real sibling", so those tests check `body.eye_height_m`,
    `phys.support.held_envelope` / `resting_size` (all 59 pass unchanged) and `BridgeTests`' fakes still override.
    Held items in sight boxes use the body profile's carry geometry, and a tray carried level rides at hand height (as
    in `held_envelope`), not on the hips.
16. **Occlusion memo per geometry epoch**, not per `mutation_count`: the kernel thermal system's quiet writes bump the
    mutation count every substep, so the per-count memo recomputed the room's occlusion signature (0.25-0.45 ms) on
    every query. The epoch reads the touch log (named consumer "sight" in geometric mode; the watch reads every
    substep, so the log stays bounded) and changes only when a write changed the fields occlusion reads
    (`GEOM_FIELDS` / `DOOR_FIELDS` snapshots of every occlusion source, the chain it rests on and the doors: exactly
    what `occ_sig` digests), a thing becomes a source (a candidate now reaching its room through "on" relations) or
    disappears, or a rollback, savepoint rollback or lost read happened; while a `view_occluders` hook is registered
    every mutation counts (hooks may read room fields). Results are unchanged (tested: an occluder move changes the
    signature, a rollback restores it, the memo equals a fresh computation). In a 1 h wait on seed 0 the epoch moved
    once (an appliance was moved) where the per-count memo recomputed 120 times.
17. **`perception.light_level(state, rid, ids=None)`**: the camera passes the World-kept list of windows and lights
    in state order, so the per-substep ambient light costs no scan of every entity and sums bit-identically.
18. **wrist viewer**: at the target's (or, under / behind, its support's) footprint edge nearest the robot, 0.15 m
    short, at 5-12 cm under furniture, else the target's middle, looking at it (no arm kinematics, as 1 says).
19. **read / inspect checks**: a field without a `TEXT_M` row reads as 4 mm print; the wrist cannot read a face
    resting down or pressed within 5 cm of a wall or neighbour (sight's blocked-face rule); a glossy item not held is
    judged from 0.3 m, 20 degrees off its face normal on the robot's side. Refusal texts are generic (11.2): "You
    cannot see it from here", "Too small to read from here", "Too dark", "Glare on it; change the angle", "Too far to
    make out the details; approach it", "Camera too dirty to read text; clean the camera", and 1.15's camera-off text.
20. **No `camera.set_sensors`** (conflict 6: `body.set_sensors`); `begin_step` applies no policy (the body derives the
    effective sensors on every call).
21. **`HouseEnv.load` / `replay` of a file without "camera"** run in room mode (older files are room-mode files).
22. **Where a visual event happens**: an explicit `at` (a cell, or a point) wins over the source's box centre (a dent
    in a floor, where a fallen thing landed); a physics consequence is witnessed at its payload's cell when it has one
    and is judged at least 0.3 m across (motion and splash make an occurrence bigger than the thing at rest; 8.6's
    default size); `phys.budget` (no target, no cell) is not a visual occurrence and is not logged.
23. **The step view culls frames per entity** with a conservative bounding-cone test before the per-sample tests, and
    judges glare only for the frames that could still be the best one (glare can only lower a level). Neither changes
    a result (tested against the unculled computation on random poses).

## 5. Stubs and contracts

* Removed: the four `TIER1-STUB(CAM-RUNTIME)` markers in `sight.py` (with `EYE_HEIGHT_M`, `CARRY`,
  `_local_held_envelope`, `_load_top` and the local `_dims` rule).
* Added: `TIER1-STUB(CAM-RENDER)` in `engine.py` (the realistic + geometric `NotImplementedError`, conflict 21).
  `grep -rn "TIER1-STUB" house_sim --include=*.py` lists it besides BODY-ENGINE's (`phys/forces.py`,
  `phys/support.py`) and stage C's.
* Consumed: `body.imaging`, `depth_ok`, `can_hear`, `is_on`, `clarity`, `eye_height_m`, `profile(state)["carry"]`,
  `register_robot_field` (BODY-MODEL); `phys.support.held_envelope`, `resting_size`, `dims` (PHYS-STATICS);
  `actions.motion_record` and the touch log, `mutation_count`, `journal_before` (PHYS-INT); `phys.impact.fall` in
  tests; the sight API (CAM-SIGHT).
* For later packages: section 9.

## 6. Gate results

Gate id **CAM-RUNTIME** (`catalog/analysis/tier1/gate/CAM-RUNTIME/`), previous gate PHYS-INT, recorded on the shared
tree with `record --id CAM-RUNTIME --prev PHYS-INT --work <scratch>` (2026-10-14, 7 jobs, about 24 min). Summary line:
`TIER1-GATE CAM-RUNTIME [CAM-RUNTIME] vs PHYS-INT: FAIL (9 pass, 2 fail, 2 skip)`; both FAILs are outside this
package (below) and `compare` passes.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 3e6e94c37334 unchanged; 110 systems (PHYS-INT 109: + `camera.watch`) |
| G1 | FAIL | 39 files, 3,301 tests (normal); the only failing runs are `test_textiles_laundry.py` normal and strict, one test: `PerceptionHardeningTests.test_unread_jugs_of_every_kind_..._weigh_what_a_full_jug_weighs`, PHYS-INT's R7 jug bound (its deviation 13 / request 1), failing identically in the PHYS-INT record. `test_camera.py` 81 tests green in both runs |
| G1b | PASS | the 9 isolated-domain failures known from PHYS-INT (A0's 8 plus the same jug test); kernel files with `HOUSE_SIM_DOMAINS=core` 13/13 (test_camera.py included) |
| G1c, G9 | SKIP | no import-inert modules, no extras for this package |
| G2 | PASS | own mode 242/242 (1,210 trials); vs PHYS-INT 0 verdict, 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (as PHYS-INT); 0 / 0 / 0 changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit (room): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz (room) 1,800 commands, 0 defects, 0 nondeterministic, 312 succeeded (as before) |
| G7 | PASS | 2.98 ms per substep (PHYS-INT 3.00), 1 h wait 1.062 s (PHYS-INT 1.079); `camera.watch` 0.000 ms in room mode |
| G8 | PASS | room golden and golden_all: byte-identical (not re-recorded; CAM-RUNTIME may not record) |
| G10 | FAIL (shared tree) / PASS (isolated) | on the shared tree it lists PHYS-INT's uncommitted B1 files and `handoff_B0.md` / `handoff_PHYS-INT.md` (27 files), none of them touched by this package. From an isolated copy (`git archive HEAD` plus every other uncommitted file of the shared tree committed as the base in a throwaway repository in scratch, then this package's files on top; the project repository was only read): `G10 PASS 9 changed file(s); 0 outside the list of CAM-RUNTIME` |

`python tools/orchestration/tier1_gate.py compare PHYS-INT CAM-RUNTIME --package CAM-RUNTIME`:

```
compare PHYS-INT -> CAM-RUNTIME (packages: CAM-RUNTIME; allowed: nothing; codes: none)
G0   PASS  action fingerprint unchanged (3e6e94c37334)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 0 with changed state digests, 0 with a changed observation digest
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312
G7   PASS  substep_ms: 3.0 -> 2.98 (-0.7%; limit 3.15 = prev x 1.05)
           wait_1h_s: 1.0789 -> 1.0622 (-1.5%; limit 1.133 = prev x 1.05)
G1   INFO  new test file test_camera.py (normal): 81 tests
           new test file test_camera.py (strict): 81 tests
compare result: PASS
```

**Reviewed differences:** none (none allowed: room mode changed in nothing). **Golden:** not recorded (3.5).

**Geometric checks beyond the gate** (the gate's tools have no `--camera` yet; CAM-RENDER adds them): a scratch fuzz
of `tools/fuzz.py`'s command generator against `HouseEnv(camera="geometric")` with `HOUSE_SIM_STRICT=1`, observing
after every step, seeds 0-5 x 300 commands (and again x 200 after the last change): 0 defects, every replay
deterministic; the cull-equivalence check (72 random poses and frame sets on six houses: the step view with and
without the bounding-cone cull identical; also a unit test).

### Benchmarks against 3.4 and TIER1_CAMERA 16 (this machine, quiet, best of 5; seeds 0 and 7 unless noted)

| Measure | Budget | CAM-RUNTIME |
|---|---|---|
| `end_step` indoors, one frame (step view + logs) | <= 3 ms | 2.2-2.5 ms (test bound 9 ms) |
| motion frames | <= 0.5 ms extra | about 0.2 ms for six frames at one eye; every frame at a new eye adds a wedge viewshed (0.19 ms, CAM-SIGHT) and its floor scan |
| look-around (6 frames, same eye) | <= 4 ms extra | step view 0.98 ms for one frame, 3.2 ms for six (+2.2 ms); end_step +1.3-3.0 ms |
| geometric observe overhead vs room mode | target 3 ms, bound 10 ms | fresh step view -0.5 to +0.5 ms against a room-mode oracle observe (2.2-2.7 ms); after a step `observe()` reuses the cached view (about 1.2-1.5 ms) |
| `can_see` warm | <= 0.05 ms | 0.0005 ms (memo); a cold scene after a write 0.03 ms |
| lidar | <= 0.4 ms | 0.04 ms |
| `camera.watch` per substep | <= 0.05 ms, skipped when idle | room mode 0.0005 ms; geometric, nobody around 0.006 ms; geometric with people in the robot's room 0.15-0.19 ms (over budget: section 7) |
| geometric 1 h wait (seeds 0-3, people present on 0, 1, 3) | - | 0.256 / 0.334 / 0.306 / 0.304 s vs room 0.236 / 0.312 / 0.297 / 0.280 s |
| geometric step (`look`, observe off) | - | 13.1-15.6 ms vs room 10.7-12.8 ms (end_step 2.5 ms of it) |
| occlusion signature per query | - | 0.25-0.45 ms per World mutation before (per-count memo); now only on a geometric change (one recompute in a 1 h wait on seed 0) |
| G7 whole engine (room mode) | <= PHYS-INT x 1.05 | 2.98 ms, 1.062 s (-0.7 %, -1.5 %) |

## 7. Known gaps

* **`camera.watch` with agents present costs about 0.15-0.18 ms per substep** (seeds 0, 1, 3 with people in the
  robot's room; idle: 0.006 ms), against the 0.05 ms budget of 3.4 / TIER1_CAMERA 16. It is the scene of the eye
  (body sensor reads, ambient light, the geometry-epoch judgement), one LOS and acuity test per agent, the capture
  checks and the camera-log write, every substep while someone shares the room; a quiet hour costs about 30 ms more
  than in room mode (seed 0: 0.256 s vs 0.234 s for the 1 h wait). Room mode is unaffected (0.0005 ms). Making it
  cheaper means sampling agents less often than every substep (a design change for TIME-EVAL's idle probes) or
  caching the per-agent result while nothing moved.
* **Not in this package** (TIER1_INTEGRATION 2.4): `face`, `peek`, the extended `look`, the geometric `inspect` / `read`
  rules, the setup ops and the `--camera` CLI flag (CAM-ACTIONS); realistic rendering of the step view, the detection
  band, tracked identity, the camera leak audits (CAM-RENDER); phase 3: doorway and window apertures, captures and
  views through doorways, policy masking.
* **Realistic + geometric** is refused (`NotImplementedError`) until CAM-RENDER.
* **Near-connection controls** (a switch by a doorway in the next room, `near_connection`) stay actionable in
  geometric mode through wrist access when reachable, but the geometric observation does not list them (views end at
  the room's walls until the phase 3 doorway apertures); room mode lists them.
* **Light model** (sight 6.1): an interior light multiplies the room's ambient (factor 1.0) rather than adding its own
  light, so an open, lit fridge in a dark kitchen is dark inside; beams cast no shadows.
* **Memo staleness shared with the spec's design**: memos key on `mutation_count` (scene) and the geometry epoch;
  both count journaled write *calls*, so a camera query made between `w.mut(eid)` and a later in-place write to that
  record in the same handler sees the old value until the next journaled call. Core verbs write right after `mut`;
  no camera call sits in between.
* **Captures**: mic captures only in the robot's room (no doorway propagation, phase 3); the people and
  navigation_robot entry-based `sensor_capture` counts still run in geometric mode (their adoption item), so a
  geometric episode can count an entry and a frame capture.
* **Visual events** are witnessed by the head pose at the moment they are emitted (during an action's handler: the
  pose before its motion after-hooks; during `_advance`: the action's final pose).
* **wrist viewer and wrist reading** are geometric approximations without arm kinematics (TIER1_CAMERA 1).
* **`obs.camera.pose.cell` mirrors `obs.robot.pos`**, which is the true cell until BODY-FAULTS renders `obs.robot`
  through proprio (unlocalized: None).
* Process-local, deterministic transient memos (per World: scene, geometry epoch, ambient light ids, gazed targets)
  are not part of the state; save / load / replay rebuild them.

## 8. How to test

```
python -m unittest tests.test_camera                          # 81 tests, about 9 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_camera
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_camera
python -m unittest tests.test_room_golden tests.test_sight     # room mode unchanged; sight on the real siblings
python tools/orchestration/tier1_gate.py compare PHYS-INT CAM-RUNTIME --package CAM-RUNTIME
```

Manual checks:

```python
from house_sim.engine import HouseEnv
env = HouseEnv(seed=7, camera="geometric")
obs = env.observe(); obs["camera"], obs["lidar"], {e: r["view"] for e, r in obs["visible_entities"].items()}
rid = env.world.s["robot"]["room"]; env.step("move", env.world.s["rooms"][rid]["exits"][0])
env.world.s["robot"]["view"]["frames"]; env.world.s["camera_log"]["coverage"]
from house_sim.kernel import camera; camera.step_view(env).entities; camera.can_see(env.world.s, "<id>", env.world)
```

## 9. Follow-ups

Requests to the main session (files outside CAM-RUNTIME):

1. `tests/test_sight.py`: its module docstring and `setUpModule` say the bridges are switched off to test sight's
   stand-ins; the stand-ins are gone (TIER1-STUB(CAM-RUNTIME) removed as required) and None in `sight._BRIDGES` now
   means the real sibling, so the file tests `body.eye_height_m`, `phys.support.held_envelope` and `resting_size`
   (all 59 tests pass unchanged). Only the wording needs updating (or drop `setUpModule`).

For later packages:

* **CAM-ACTIONS (B3)**: `face` / `peek` / `look` add frames with `camera.add_frame(w, kind, yaw, tilt, cam)`, turn with
  `camera._set_pose` or `camera.gaze_to(ctx, target)` (charges time), record peeks with `camera.note_peek(w, eid,
  where)`, use `camera.wrist_access(state, eid, where)` / `wrist_viewer` and `ctx.read_check` / `ctx.inspect_check`
  (both return the reason texts of 11.2); `GEOMETRIC_ONLY` and `action_names()` are ready; `approach` already ends
  facing its target with the head aimed (deviation 1); the setup `camera` op writes config `robot.facing_deg` /
  partial `robot.view` and `view_watch` / `track_view` write config `camera_log.watch_specs` / `track_ids`, which
  `install` keeps (tested); `HouseEnv(camera=)` takes the CLI value.
* **CAM-RENDER (B3)**: remove the `TIER1-STUB(CAM-RENDER)` guard; `perception.observe(records=False)` gives each
  detected entity, person and pet `{"_view": ViewInfo}` (shapes add `"label": "dark shape"`) and the step view keeps
  band candidates (`detected` False, `px` = effective pixels from 10); `realism.note_step(view)` receives the StepView
  (None in room mode); `obs.camera` / `obs.lidar` numbers are exact (deviation 8).
* **BODY-ENGINE (B4)**: the `robot_keep_awake` hook is registered (True while `robot.view.track` is non-empty); sight
  already reads the profile's carry block for held items, `support.CARRY` still feeds `held_envelope`; verbs that
  call `ctx.motion` get camera frames through `camera.note_action` without further work.
* **BODY-FAULTS (B5)**: the geometric observation already applies the 1.3 imaging / power / depth rules; room mode is
  yours; `obs.messages` is not filtered here.
* **TIME-EVAL (C)**: post `visual` notices from `camera.witness` (it returns whether the robot saw the occurrence);
  an idle probe for `camera.watch` must still call `sight.geometry_epoch(w)` every substep in geometric mode (it keeps
  the "sight" touch-log consumer current, which bounds the log), or unregister the consumer; savepoint rollbacks are
  handled (a shrunk log starts a new epoch); a phase relocation without motion gets the end_step fallback.
* **Domains (adoption round, TIER1_INTEGRATION 6 "Camera")**: people implement `private_activity` and
  `camera_capture` (the kernel otherwise counts every capture in `privacy_violations`) and skip their entry-based
  capture in geometric mode; navigation_robot skips its entry capture likewise; verbs that move the robot set
  `ctx.data["path"]` so the camera frames the walk; visual cues gated by `ctx.in_frame`.

## 10. Stage-B review fix round (STAGE-B-FIX)

Added after the three stage-B reviews (`catalog/analysis/tier1/stageB_review_physics.json`, `stageB_review_camera.json`,
`stageB_review_body.json`: 6 + 15 + 13 findings). The same section is in `handoff_PHYS-INT.md`,
`handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`,
`handoff_BODY-CORE.md` and `handoff_BODY-FAULTS.md`. Read before starting: TIER1_INTEGRATION (1 with 1.9, 2.4, 3, 5, 6),
TIER1_DECISIONS, TIER1_PHYSICS / TIER1_CAMERA / TIER1_EMBODIMENT where the findings point, and every `handoff_*.md`.
Every finding was reproduced with the reviewer's probe on the B5-merge tree first. Every fix has a regression test that
fails on a copy of that tree and passes now. Only stage-B package files (2.4) and these handoffs were edited.

### 10.1 Findings

Status: **fixed** (root cause fixed, regression test), **still open** (needs a file outside every stage-B package;
the exact change is in 10.5). No finding was rejected: all 34 reproduced.

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
| B3 | Domain code doing `sorted(set(robot.faults))` crashes on the dict fault log (body, high) | still open | domain files only (10.5) | - |
| B4 | Domain verbs relocate the robot without ctx.motion: a fallen / legs-failed / unlocalized / wheeled robot guided someone upstairs for free (body, high) | fixed (kernel backstop) | `body.unrecorded_motion(ctx)` after every leaf's AFTER hooks (engine `step` and `perform`): a move without a motion record becomes a synthesized record (straight line, the doorway, the stairs; `"synthesized": True`) paid on top of the action's own activity power, and a body that could not walk refuses (`mobile_problem` with the fallen state at the action's start). `Ctx._robot_down`, `_robot_aid`. The domains still need their own routes (10.5) | `test_body_engine.UnrecordedMotionTests` (energy, stairs, five refusals, rolled back) |
| B5 | `lift_plan` took a known weak right hand (body, medium) | fixed | `forces.lift_plan`: believed capacities (`believed=True`) decide two hands and pick the free hand that can take the mass, right first (also BODY-CORE request 4); core `receive` / `uninstall` take the plan's hand when none is named | `test_body_core`: `test_a_known_weak_arm_hands_the_lift_to_the_arm_that_can_take_it` |
| B6 | Gripper slips drawn per leaf, not per metre (body, medium) | fixed | `body_faults.slip_probability(state, meters)`: 1 - (1 - p)^(m / ref), `fault_p.gripper_slip_ref_m` 10 m (profiles.json); `slip_check(..., meters=None)` (None keeps the per-draw probability) | `test_body_faults`: `test_the_slip_hazard_is_per_metre_not_per_walk` (one 5 m walk vs ten 0.5 m: 6 / 40 and 6 / 40; was 9 vs 38) |
| B7 | Careful speed stretched commanded durations (body, medium) | still open | kernel part landed: ActionSpec `timing="commanded"` (actions.TIMINGS), skipped by `body.careful_seconds`; KERNEL_API.md. The domain verbs must declare it (10.5) | `test_body_core`: `test_careful_speed_does_not_stretch_a_commanded_duration` |
| B8 | The wheeled base climbed onto stools (body, medium) | fixed | profiles.json `climb` (false for mobile_manipulator); `body.climb_problem`, `NO_CLIMB_TEXT`; `unrecorded_motion` refuses a leaf that put a non-climbing body on an aid | `test_body_engine`: `test_a_wheeled_base_cannot_climb_onto_an_aid` |
| B9 | `tools/body_audit.py` models the lift with its own hand rule (body, medium) | still open | stage-A tool, no stage-B owner (10.5) | - |
| B10 | `power_down` set a fallen or sitting robot to crouching (body, low) | fixed | `body.power_down` lowers only a standing posture | `test_body_faults`: `test_a_power_down_lowers_the_posture_only_from_standing` |
| B11 | A power-down on an aid left it occupied and the robot in its cell (body, low) | fixed | `body_faults.fall` (`_off_the_aid`): frees the aid, lands beside it (the cell it was climbed from, else the nearest free cell) | `test_body_faults`: `test_a_power_down_on_a_climbing_aid_frees_it_and_lands_beside_it` |
| B12 | Safety-net and slip drops fell from the standing hand height (body, low) | fixed | `body.hand_drop_m(state)` (hand height x posture eye ratio + aid height) in `_hold_check`, `slip_check`, the arm-failure drop | `test_body_core`: `test_a_dropped_load_falls_from_where_the_hands_are` |
| B13 | Safety stops marked heard by a deaf robot (body, low) | still open | domain file (10.5) | - |

### 10.2 Files

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
  `test_body_perception.py`; `tests/data/room_golden.json` (re-recorded, 10.4).
* These seven handoffs (this section).

### 10.3 APIs added or changed

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

### 10.4 Gate (STAGE-B-FIX)

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
G1b failures are tests in files outside every stage-B package, each with a request (10.5 item 1); G7 is 0.1-1 % over a
fixed bound that the B5 tree also misses on this machine today (below). `compare` passes everything but G7.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `482f9adf9717`, unchanged (425 actions, 111 systems) |
| G1 | FAIL | 45 files, 3,575 tests, normal and strict. 12 failing runs: 7 tests in 6 files, the same in both modes, all outside the stage-B packages: `test_body_model.py` (2), `test_physics_forces.py`, `test_manipulation.py` `HandOverTests` (known from B5-MERGE, BODY-ENGINE requests 1-2, BODY-CORE request 2), `test_textiles_laundry.py` (PHYS-INT request 1), and two new by design: `test_physics_statics.py` (P1) and `test_physics_data.py` (P2). With the six patches of 10.5 applied to a copy of this tree, all six files pass in normal, strict and core-only runs. `test_body_engine.py`, `test_camera.py` and `test_camera_actions.py`, failing at B5-MERGE, now pass (C15). All the fix round's test files pass: `test_physics_settle` 44, `test_physics_setup` 29, `test_camera` 90, `test_camera_actions` 58, `test_camera_realism` 50, `test_body_engine` 50, `test_body_core` 39, `test_body_faults` 14, `test_body_perception` 24 |
| G1b | FAIL | isolated domains: 10 failing tests in 7 files, all known from B5-MERGE; kernel files with core only 15/19 (B5-MERGE 14/19): `test_body_model`, `test_physics_data`, `test_physics_forces`, `test_physics_statics`, all covered by the 10.5 patches |
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
  and an unseen person can be named (10.5 item 9).
* T-7ef5816c160945-01, seeds 0 and 2: behind the door the robot opens, the cat is lit at 0.135 (identify needs 0.2), so it is
  recognised, not identified, and `inspect` gives the shape answer (C4).

### 10.5 Still open, and requests to the main session

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
9. **For the camera owner (D3 triage), seen in the geometric preview of 10.4.** Two rules of other packages now
   decide specs that the blind doorway used to hide:
   * `sight.glare` adds the target's own angular radius to the backlight overlap, so a person 0.5 m from the robot is
     "backlit" by a window well off the line of sight and cannot be identified (T-702453ef553c00-01, seed 0).
   * `realism.inbound` refuses a person's id only while they are seen and unidentified; one the camera does not see at
     all can still be named.

### 10.6 How to test

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
