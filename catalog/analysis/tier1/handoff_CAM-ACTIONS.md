# Handoff CAM-ACTIONS (stage B3): the camera verbs, the geometric inspect / read rules and the camera plumbing

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1 with 1.3, 1.9, 1.10, 1.13, 1.15 and conflicts 4-9,
19-21; 2.2 / 2.4 the B3 row; 3; 5), `docs/design/TIER1_DECISIONS.md`, `docs/design/TIER1_CAMERA.md` (whole; this
package is WP-3: 9.1, 9.2, 10.5, 14, 15 and the WP-3 parts of 17), the sensor / eye-height parts of
`TIER1_EMBODIMENT.md`. Handoff files read (all that existed): `handoff_TIER1-GATE.md`, `handoff_B0.md`,
`handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`,
`handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md` (its section 9
"CAM-ACTIONS" items are all acted on: frames through `camera.add_frame`, turns through the camera's pose writer,
`camera.note_peek`, `wrist_access`, `read_check` / `inspect_check`, the setup ops writing the config fields `install`
keeps, `HouseEnv(camera=)` taking the CLI value). CAM-RENDER ran in parallel on disjoint files.

## 1. Summary

* New core verbs `face` and `peek` and the extended `look` (forward, around with an optional low row, up / down /
  left / right / behind, at a thing / person / pet / cell, head pan / tilt, tracking) with the 9.1 times; their
  mechanics in `kernel/camera.py` (wrist peeks with per-`where` poses, lifting light supports, `revealed` within the
  wrist's depth, wrist detections in the observation). Room mode: `look` unchanged, `face` / `peek` refuse.
* Geometric `inspect` / `read` (9.2): head acuity or the wrist (+2 s), peeks for under / behind / inside, face-gated
  readable fields only, grants of only what was read, people only in line of sight.
* Setup ops `camera`, `view`, `track_view`, `view_watch` and `spawn ... "row"`; spec key `"camera"` with its lint;
  camera-mode plumbing through `build` / `make_env` / `run_trial` / `verify_many` / the CLI (`--camera`); geometric
  branches of the `approach`, `reveal` and `fetch` skills; docs (ACTION_API, TASK_SPEC, AUTHORING_GUIDE).
* Sight fixes the verbs needed: solid seats (sofas, armchairs) are solid only to the seat with a back strip above,
  things on things on furniture are tested against the furniture exactly, an eye can ignore a lifted support.
* 54 new tests (`tests/test_camera_actions.py`), green normal, strict and core-only. Room mode unchanged: `compare
  CAM-RUNTIME CAM-ACTIONS --package CAM-ACTIONS` PASS (only the allowed action fingerprint changes; 0 verdict / state /
  observation digest changes over 2 x 1210 trials). The recorded summary line reads `TIER1-GATE CAM-ACTIONS
  [CAM-ACTIONS] vs CAM-RUNTIME: FAIL (8 pass, 3 fail, 2 skip)`; its three FAILs are on the shared tree and outside
  this package (section 6).

## 2. Files

Created: `tests/test_camera_actions.py`, this handoff, the gate record `catalog/analysis/tier1/gate/CAM-ACTIONS/`.
Edited: `house_sim/systems/core.py`, `house_sim/kernel/camera.py` (CAM-RUNTIME's new file), `house_sim/kernel/sight.py`,
`house_sim/tasks/setup.py`, `house_sim/tasks/spec.py`, `house_sim/tasks/runner.py`, `house_sim/tasks/verify.py`,
`house_sim/tasks/skills.py`, `house_sim/cli.py`, `docs/ACTION_API.md`, `docs/TASK_SPEC.md`, `docs/AUTHORING_GUIDE.md`.
All on the CAM-ACTIONS list of TIER1_INTEGRATION 2.4. `house_sim/kernel/actions.py` (also on the list) needed no
change: CAM-RUNTIME's Ctx helpers cover the verbs.

## 3. APIs

"ADDITION" = not in TIER1_CAMERA 9 / 10 or TIER1_INTEGRATION 1; **DEVIATION** = differs from them (section 4).

**Actions** (`house_sim/systems/core.py`; the action fingerprint changes, as allowed: 423 -> 425 actions,
`3e6e94c37334` -> `482f9adf9717`)

```
look    target ?str, pan ?number [-180, 180], tilt ?number [-90, 90], rows int 1-2 (default 1), track ?bool
        duration = ctx.data["seconds"] (default 1.0); category perception; stopped_ok; energy 0
face    target ?str, deg ?number [-360, 360]                 duration |turn| / 90 + 0.3 s; physical; energy 0.005
peek    target entity, where str:under|behind|inside|top|closely   6 s, +2 under / behind, +1 lifting; perception;
        energy 0.005
inspect (unchanged params) geometric: inspect_check or the wrist (+2 s); where under / behind / inside = wrist peek
        (+2 s, +1 lifting); where closely on a held item +3 s
read    (unchanged params) geometric: read_check (head, wrist +2 s, held +1 s); returns and grants only what it read
core.wrist_peek(ctx, target, where) -> (ids seen, lifted)   ADDITION: the guards + camera.peek, shared by peek and
        inspect
core.LOOK_MESSAGES, PEEK_MESSAGES, _camera_category(reason) ADDITION
```

Refusals (all `kernel_access`): room mode `face` / `peek` "Camera control needs camera: geometric" (capability); cameras
off "Cameras are off; turn privacy mode off to look closely" (perception, 1.15); `face` "Robot cannot move"
(capability: fallen, actuator fault, flat battery) and "Step down first" (safety: on a climbing aid; the interim guards
of conflict 19, same semantics as the motion verbs); `peek` arm guards as `Ctx.obj` touch ("Actuator fault; request
human assistance", "Battery depleted; dock or request assistance", "The robot has fallen; recover first"), reach
(the reach_problem text), the 9.1 texts ("No gap to look under it", "Cannot see behind it from here", "Too high to look
on top", "It is closed; open it first"), ADDITION "There is nothing to look inside", "Put it down first" (under /
behind a held thing), "No free hand; place something first" (lifting), the private / permission rules; look / face
targets not in the room "That is not visible here" (perception); argument errors for target + pan / tilt, target +
deg, neither, `rows=2` without `around`.

**`house_sim/kernel/camera.py`** (all no-ops or room-mode answers outside geometric mode)

```
ROOM_MODE_TEXT, NOTHING_INSIDE_TEXT, PUT_DOWN_TEXT; LOOK_WORDS, LOOK_AROUND_PANS (-150..150 every 60),
LOOK_FORWARD_S 1.0, LOOK_AROUND_S 5.4, LOOK_AROUND_ROWS2_S 8.3, FACE_SETTLE_S 0.3, COMPASS_DEG, RELATIVE_DEG,
PEEK_WHERE, PEEK_S 6, PEEK_DEEP_EXTRA_S 2, PEEK_LIFT_EXTRA_S 1, INSPECT_WRIST_S 2, INSPECT_HELD_CLOSELY_S 3,
READ_HELD_S 1, PEEK_MAX_FRAMES 12, PEEK_INSIDE_STANDOFF_M 0.35, ... (pose constants), PEEK_RELATION
direction_deg(state, word) -> grid yaw | None          compass words, forward / right / back / behind / left
parse_cell(value) -> [c, r] | None                       "[c, r]", "c, r" or a list
view_target(state, target, perception=None) -> (kind, id, point) | None      L0 (contained), in the room; or a cell
can_turn(state) -> bool                                  body can turn: not fallen / actuator fault / aid / stopped
agent_visible(state, aid, world=None) -> bool            ADDITION: can_see for people and pets (LOS, light / IR)
look(ctx, target=None, point=None, pan=None, tilt=None, rows=1) -> seconds  frames + pose (9.1)
face(ctx, yaw, point=None) -> seconds                    body yaw, pan 0, tilt at a target; one frame
set_track(w, ids) -> tracked ids; agents_in_frames(state, world=None) -> ids
peek_access(state, eid, where) -> (ok, reason, lift)     the 9.1 rules (wrist_access with where) and lifting
peek_poses(state, eid, where, lift=False) -> [(x, y, z, yaw, tilt)]          the wrist poses (section 4)
peek(ctx, eid, where, lift=False) -> ids seen            wrist frames, revealed, note_peek
add_frame(w, kind, ..., extra=None)                      ADDITION extra: a peek frame's 10th element
read_check(...)                                          DEVIATION: head + wrist union (section 4)
wrist_access(state, eid, where=None)                     DEVIATION: depth rule, free floor behind (section 4)
```

Wrist peek frames are `[rid, x, y, z, yaw, tilt, "wrist", "peek", None, {"target", "where", "lift"}]`; the step view
renders them with the head's frames (their indices join `StepView.current`, their ViewInfos say `via: "wrist"`),
`in_frame` and `acuity` count them, the logs (seen cells, looked-in, sightings, captures) read them with the wrist's
optics, LED and short viewshed; frames without the 10th element (written earlier) still render.

**`house_sim/kernel/sight.py`**: `row_spos(state, cid, eid, row)` (ADDITION: the inner_spos of a front / back row);
`SEAT_BACK_M`, `SEAT_MIN_DROP_M`, `_seat_back`, `_body_parts` (seats; memoized per children-map snapshot like the
places and layouts); `_Rel.support` is now a tuple of exact parts `((x0, y0, x1, y1, spans), ...)` instead of one part
(a seat's seat and back strip, and the furniture under a thing resting on a thing on furniture); `_with_ignore` (an eye
that ignores a thing's support drops that support's exact test).

**`house_sim/tasks`**: `spec.ALLOWED` gains `"camera"`; `spec.CAMERA_MODES`, `CAMERA_SETUP_OPS`, `CAMERA_VERBS`,
`camera_problems(spec)` (run by `check_structure`); `setup.OPS` gains `camera`, `view`, `track_view`, `view_watch`
(+ `CAMERA_OP_KEYS`, `SENSOR_KEYS`, `VIEW_FIELDS`, `VIEWER_KEYS`); `SetupCtx(..., camera=None, perception=None)` and
`apply_setup(..., camera=None, perception=None)` (the run's camera mode: `ctx.camera`); `spawn ... "row"`;
`runner.build(spec, seed=0, registry=None, camera=None, perception=None)` (task carries the spec's `"camera"`),
`runner.make_env(..., camera=None)`; `verify.run_trial(spec, seed, perception=None, camera=None)` (trial records
`"camera"`), `verify_spec(..., camera=None)`, `verify_many(..., camera=None)`.

**`house_sim/cli.py`**: `--camera room|geometric` (interactive / task / demo / scenario / house / generated envs,
`--check`, `--verify` when `audit.verify` takes `camera`); `command_of(line)` (ADDITION: the interactive parser, with
`face DEGREES|WORD|ID`, `peek ID WHERE`, `inspect ID WHERE`); the camera pose line in `show()`.

## 4. Deviations from the spec

Each chose the more realistic or simpler option.

1. **look around pans -150, -90, -30, 30, 90, 150** (the spec's camera offsets 0..300 rotated by 30 degrees): an offset
   of 180 needs a pan beyond the 150-degree limit; these six cover 369 degrees with the 69-degree field of view without
   a body turn, as the spec intends. The head returns straight ahead after the sweep (end_step then adds its resting
   frame there). Times are the spec's constants (5.4 s, 8.3 s).
2. **look forward takes an "idle" frame** (the resting frame end_step would record), not a "look" frame: CAM-RUNTIME's
   test pins that a plain `look` step ends with an idle frame.
3. **Directional looks keep the other axis**: up / down set only the tilt, left / right only the pan; `behind` turns
   the body 180 degrees, or, when the body cannot turn (fallen, actuator fault, on an aid, stopped), pans the head to its
   limit. Aims and turns cost the slowest axis at the hardware rates plus `HW.capture_s` (0.4 s); explicit pan / tilt
   beyond the pan limit turn the body when it can.
4. **face deg is the absolute grid heading** (as `obs.camera.pose.facing_deg`), not a relative turn; relative turns are
   the words left / right / back. Targets also take cells, people and pets. Message "Facing <compass> (<deg> degrees)".
   face is category physical (careful speed slows it today; BODY-CORE's own-seconds rule applies later).
5. **peek's `where` is required**; peeking under / behind a held thing is refused, inside a non-container is refused.
   Lifting a light support needs a free hand and the touch permission. inspect where=closely on a non-held item is a
   plain inspect (the 9.2 head / wrist rule).
6. **Wrist poses** (9.1 says only "from the reachable edge"): under: 15 cm outside the support's near edge at half the
   clearance (3-12 cm), looking straight in; behind: slid into the gap from its open end nearest the robot at 15 cm,
   looking along it (above the gap, looking down, when walls close both ends); inside: 0.35 m in front of a front opening
   per shelf band and per bay of the opening (the wrist's 87 degrees frame about 0.6 m of opening from there; at most 12
   frames), above a top opening's rim high enough to frame the rim (0.15-0.5 m); top: 0.25 m above the near edge;
   closely: mid-height; lifting: 0.15 m above the lifted support. The wrist range is `deep_m + standoff` (1.15 m; it
   was capped at 1.0 m, which fell 0.15 m short of "1.0 m past the reachable edge").
7. **Depth limit in wrist_access**: a thing under or behind a support more than `deep_m` (1.0 m) from the support's edge
   nearest the robot is not wrist-accessible, so `can_see` agrees with "deeper items stay hidden". **Free floor
   behind**: a support standing out in the room with a free cell at its back passes the behind rule (9.1's "free floor
   cell on the back side within reach"; reach is checked first).
8. **read_check reads with both cameras**: the head reads what it can; when it leaves requested fields unread and the
   wrist is within reach, the wrist reads too and the union is returned (via "wrist", +2 s) -- the spec's "first that
   passes" would return only a display and never the 4 mm label next to it. Single-field reads are unchanged.
9. **read in geometric mode needs no reach** (a big display or sign reads from across the room, 9.2); `read` reads only
   its legible fields (`display`, `label_text`, ...; a `sign` field is not one of them in `read`). Held items +1 s.
10. **inspect** shows printed fields only when `read_check` can read them, lists `contents` the cameras can see or the
    wrist reach (`Perception.visible`), and inspects people only in line of sight (`agent_visible`), turning the head to
    them (auto-gaze). Domain inspectors that copy printed fields themselves (manipulation.details) still show them
    (follow-up).
11. **Held items' ViewInfo faces**: the faces the head sees (a held item's front faces the robot) instead of none;
    every declared face after a peek closely this step (4.6 "held items show all faces").
12. **Sight fixes** (sight.py was CAM-ACTIONS' to fix in B3): (a) a solid entity holding nothing inside whose surface is
    at least 0.1 m below its top (a sofa, an armchair) is solid only up to the seat and, above it, along a 0.2 m back
    strip: CAM-SIGHT's full box hid everything on a sofa seat from every eye (porous chairs keep their box, so
    test_sight's chair line of sight stays 0.6); (b) a thing resting on (or under) a thing on furniture skips the
    furniture cell by cell and is tested against it exactly through its support (a remote on a cushion on a sofa is
    seen from the front and hidden by the back from behind); (c) an eye that ignores a thing's support drops that
    support's exact test (the lifted cushion).
13. **Setup**: the camera ops are validated always but applied only when the run's camera is geometric (a geometric spec
    forced to `--camera room` skips them; a spec without `"camera": "geometric"` is a SpecError, whatever the run).
    `camera.face` takes degrees, a compass word, a thing / person / pet id or a cell (from the robot's configured cell);
    the sensor keys are rejected with a pointer to the robot op (TIER1_INTEGRATION 1.13). `view_watch` rejects the
    phase-3 aperture viewer (it would silently never see) and `every_s` under 10 s. `spawn row` takes the leftmost free
    place in the row given how the shelf is laid out now (explicit and automatic places), SpecError when it is full.
14. **Lint marker**: a schema-3 geometric spec marks its camera wrong plan with `"camera": true` (the spec gives no
    marker). The rule is dormant until TIME-EVAL adds `"schema"` to `spec.ALLOWED`; the other two rules (valid value;
    camera ops / verbs / `camera_log` paths need `"camera": "geometric"`) apply to every spec now (none trips them).
15. **Skills**: `reveal` peeks only when the thing is out of sight after approaching its support (a thing under a light
    cushion is already wrist-accessible, so fetch grabs it without kneeling); it kneels for under-peeks that need a low
    posture; `fetch` stands up after a reveal that knelt. `approach` walks to the nearest of `sight.best_viewpoints`
    (else next to the target) when the target is out of sight.
16. **CLI**: `--camera` with `--load-state` / `--replay` is an error (the file records its mode); `--verify --camera`
    needs `audit.verify(camera=)` (request 2) and says so.

## 5. Stubs and contracts

* No `TIER1-STUB` marker added or left (`grep -rn "TIER1-STUB" house_sim --include=*.py` lists only BODY-ENGINE's and
  stage C's).
* Consumed: CAM-RUNTIME's camera runtime (`add_frame`, `_set_pose`, `gaze_to` / `auto_gaze`, `wrist_access`,
  `inspect_check`, `read_check`, `note_peek`, `step_view`, the Ctx helpers, `GEOMETRIC_ONLY`, `resolve_mode`);
  CAM-SIGHT's sight library; BODY-MODEL's sensors (`body.imaging` through `camera_active`); PHYS-INT's setup ops and
  physics lint (untouched).
* For later packages: section 9.

## 6. Gate results

Recorded on the final code (`record --id CAM-ACTIONS --package CAM-ACTIONS --prev CAM-RUNTIME --shared-tree --jobs 6`,
2026-10-14T22:09Z; record in `catalog/analysis/tier1/gate/CAM-ACTIONS/`):

```
TIER1-GATE CAM-ACTIONS [CAM-ACTIONS] vs CAM-RUNTIME: FAIL (8 pass, 3 fail, 2 skip)

G0   PASS  action fingerprint 482f9adf9717 (425 actions, 110 systems); changed vs CAM-RUNTIME (allowed)
G1   FAIL  41 test files x normal+strict: 3401 tests (normal), 4 failing run(s): test_camera.py (normal),
           test_camera.py (strict), test_textiles_laundry.py (normal), test_textiles_laundry.py (strict)
G1b  FAIL  isolated domains 9/16 files ok (9 failing test(s) ...; all known from CAM-RUNTIME); kernel files with
           HOUSE_SIM_DOMAINS=core 14/15 ok (failing: test_camera.py)
G1c  SKIP  no import-inert modules listed for this package
G2   PASS  own mode: 242/242 specs pass (1210 trials); vs CAM-RUNTIME: 0 verdict / 0 state-digest / 0 observation-digest
           change(s)
G3   PASS  forced realistic: 226/242 specs pass (1210 trials); vs CAM-RUNTIME: 0 / 0 / 0 change(s)
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  layout: 240 houses, 0 problems; counts equal; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  3.06 ms/substep, 1 h wait 1.103 s; within budget (1.05x CAM-RUNTIME)
G8   PASS  room golden: golden ok, golden_all ok
G9   SKIP  no extras for this package
G10  FAIL  59 changed file(s); 24 outside the list of CAM-ACTIONS; 20 attributed to concurrent packages (--shared-tree)

compare CAM-RUNTIME -> CAM-ACTIONS (packages: CAM-ACTIONS; allowed: fingerprint; codes: none)
G0   PASS  action fingerprint changed 3e6e94c37334 -> 482f9adf9717 (allowed for this package)
G2   PASS  1210 trials vs 1210: 0 verdict change(s), 0 changed state digests, 0 changed observation digests
G3   PASS  1210 trials vs 1210: 0 verdict change(s), 0 changed state digests, 0 changed observation digests
G5   PASS  layout counts equal (0 problems); generation-1 digests 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 375
G7   PASS  substep_ms: 2.98 -> 3.06 (+2.7%; limit 3.129); wait_1h_s: 1.0622 -> 1.1027 (+3.8%; limit 1.115)
G1   INFO  new test file test_camera_actions.py (normal / strict): 54 tests; test_camera_realism.py: 46 tests
compare result: PASS
```

The three FAILs, none caused by this package:

* **G1 / G1b `test_camera.py`**: `ModeTests.test_realistic_perception_with_the_geometric_camera_waits_for_cam_render`
  expects the interim realistic + geometric guard that CAM-RENDER removed (its job) on the shared tree (request 1).
  **`test_textiles_laundry.py`**: PHYS-INT's known R7 (subtree mass with `liquids.json` densities). The G1b isolated
  domain failures are the ones CAM-RUNTIME's gate lists. `test_camera_realism.py` failed in an earlier record of this
  package (its replay test: `HouseEnv.replay` recreated Realism after reset and lost the reset-time handles, exposed
  by the look-around frames); CAM-RENDER has since fixed `engine.replay` on the shared tree and it passes in this
  record (46/46; a replay after `look around` now matches state, observation and realism dumps).
* **G10** on the shared tree counts PHYS-INT's / CAM-RUNTIME's / CAM-RENDER's uncommitted files. From an isolated
  copy (HEAD + every other uncommitted file of the shared tree as the base, this package's 14 files on top;
  `scope --package CAM-ACTIONS`): `G10 PASS 14 changed file(s); 0 outside the list of CAM-ACTIONS`.

Reviewed differences: only the action fingerprint (G0: `face`, `peek` added; `look` gains `pan` / `tilt` / `rows` /
`track`; inspect / read unchanged params). G6's success count changes because the room fuzz draws from the new action
list (face / peek are refused in room mode). No verdict, state or observation digest changes.

**Benchmarks against 3.4.** G7 is room mode, where none of this package's code runs (sight is used only by the
geometric camera; the room branches of look / inspect / read are unchanged); the +2.7 % / +3.8 % is machine load (an
earlier record of the same room-mode code measured 2.98 ms / 1.063 s, +0.0 % / +0.1 %; a rerun of `bench_systems.py
40 0 1 2 3` right after gave 3.14 ms on this tree and 3.15 ms on the pre-package tree). The machine was about 1.7x
slower than in CAM-RUNTIME's measurements throughout (a room `look` step 21-27 ms here vs 10.7-12.8 ms there), so the
camera numbers below are A/B against the pre-package tree in the same conditions (best of 7-9, ms):

| Measure (budget) | Pre-package | CAM-ACTIONS |
|---|---|---|
| `end_step` one frame, generated house seeds 0 / 7 (<= 3 ms, CAM-RUNTIME's) | 3.43 / 4.05 | 3.62 / 4.18 (+0.13-0.19: seat parts, wrist bookkeeping) |
| fresh geometric observe seeds 0 / 7 | 2.63 / 2.66 | 2.76 / 2.83 |
| look around, extra over one frame (<= 4 ms) | - | +3.38 / +1.69 |
| look around rows=2 (12 frames), extra | - | +5.22 / +2.65 (8.3 s action; twice the frames) |
| peek under (kneeling, lab), end_step | 0.38 (one frame) | 0.54 |
| peek inside a shelf unit (10 wrist frames), end_step | 0.33 (one frame) | 1.32 |

Scaled by the machine factor the one-frame `end_step` is about 2.1-2.5 ms, inside CAM-RUNTIME's 3 ms; the 3x test
bounds of `PerformanceTests` (look-around extra < 12 ms, end_step < 9 ms) pass.

Other checks on the final code: `tools/fuzz.py 6 200 --first 20 --camera geometric` (mixed perception, strict): 1200
commands, 0 defects, 0 nondeterministic; `tools/fuzz.py` room default 12 x 300: 0 defects; a strict verb fuzz of look /
face / peek / inspect / read (6 seeds x oracle / realistic x 120 commands, each run twice and compared): no internal errors, deterministic.

## 7. Known gaps

* **A thing under a light support is wrist-accessible without a peek** (CAM-RUNTIME's C9 rule in `wrist_access`), so
  `can_see` and pick-up work within reach before anything was looked at; only `revealed` and the observation need the
  peek.
* **Wrist poses are geometric approximations** (no arm kinematics, TIER1_CAMERA 1): the side entry behind furniture
  assumes the arm fits the gap; a peek inside very wide or very deep containers frames at most 12 bays x bands and 1.15
  m deep; the lifted support is "put back" (nothing moves).
* **look around with hardware overrides** that lower the pan limit below 150 degrees leaves gaps (the pans are clamped,
  the body does not turn).
* **Domain inspectors** that copy printed fields (manipulation.details: `label_text`) bypass the geometric face / reach
  gating of inspect; the navigation_robot `_no_camera` before-hooks still answer privacy mode with their own wording
  ("Cameras are off (privacy mode); turn it off to look closely") on `inspect` / `read` (adoption round).
* **The schema-3 camera lint** is dormant until TIME-EVAL; `--verify --camera` waits for `audit.verify(camera=)`.
* **Seats**: the back strip is on the side opposite the seat's front (`sight.front_deg`); a free-standing sofa faces the
  room centre by rule 3.4, which may put its back on the wrong side (a `view.front_deg` fixes it per type or record).
* Mic gating of `ask` / `listen` is BODY-CORE's (not in this package, 2.4).

## 8. How to test

```
python -m unittest tests.test_camera_actions                  # 54 tests
HOUSE_SIM_STRICT=1 python -m unittest tests.test_camera_actions
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_camera_actions
python -m unittest tests.test_camera tests.test_sight tests.test_room_golden
python tools/orchestration/tier1_gate.py compare CAM-RUNTIME CAM-ACTIONS --package CAM-ACTIONS
python -m house_sim --check tasks/authored/<file>.json --camera geometric --seeds 0   # a whole file in the camera
```

Manual checks:

```python
from house_sim.engine import HouseEnv
env = HouseEnv(seed=7, camera="geometric")
env.step("look", "around", rows=2); env.observe()["camera"]
env.step("face", "north"); env.step("look", "", pan=-40.0, tilt=-30.0)
# beside a bed: env.step("kneel"); env.step("peek", "<bed id>", where="under"); env.observe()["visible_entities"]
env.world.s["camera_log"]["peeked"], env.world.s["robot"]["view"]["frames"]
```

Interactive: `python -m house_sim --camera geometric` then `look around`, `face 90`, `peek bed_1 under`.

## 9. Follow-ups

Requests to the main session (files outside CAM-ACTIONS):

1. `tests/test_camera.py::ModeTests::test_realistic_perception_with_the_geometric_camera_waits_for_cam_render` fails on
   the shared tree because CAM-RENDER removed the guard (its job); the test belongs to no B3 package -- update it when
   CAM-RENDER lands (it is not caused by this package).
2. `house_sim/audit.py`: `verify(..., camera=None)` forwarding to `verify_many(..., camera=camera)`, so
   `python -m house_sim --verify --camera geometric` works (the CLI already passes it when the signature takes it).
3. `docs/KERNEL_API.md` ("v3.0 camera", owned by BODY-ENGINE next): a paragraph on the camera verbs' API (section 3 of
   this handoff: `camera.look / face / peek / peek_access / peek_poses / set_track / agents_in_frames / agent_visible /
   view_target / direction_deg / parse_cell`, the peek frames' 10th element, `read_check`'s union, `sight.row_spos`, the
   seat model and `_with_ignore`).
4. `house_sim/kernel/engine.py` (CAM-RENDER in B3, BODY-ENGINE in B4): `affordances()` in realistic mode lists `peek` in
   room mode; filtering by `action_names()` would match what agents are offered.

For later packages:

* **BODY-CORE**: replace the interim guards of `face` (`ctx.require_mobile`) and `peek` / `core.wrist_peek`
  (`ctx.require_arm`) and the `look` / `peek` / `inspect` / `read` camera-off checks (`ctx.require_imaging`; same text);
  the careful-speed factor will no longer apply to `face` (own seconds rule).
* **CAM-RENDER / D3**: wrist detections arrive in the step view with `via: "wrist"`; the realistic rendering of a peek's
  detections follows the same ViewInfo rules; SearchAgent can use `look around` (rows=2 for small targets), `kneel` +
  `peek ... under`.
* **TIME-EVAL**: add `"schema"` to `spec.ALLOWED` to arm the camera lint's schema-3 rule; `runner.build` already takes
  `camera` / `perception`.
* **Domains (adoption round)**: manipulation.details gates printed fields through `ctx.read_check` in geometric mode;
  navigation_robot's `_no_camera` hooks give way to `ctx.require_imaging` (and extend to `peek`); seat types whose back is
  not opposite their front (chaises, corner sofas) set `view.front_deg`; pantry / fridge specs stage rows with
  `spawn ... "row"`.

## 10. B3 merge gate (B3-MERGE)

Added by the B3 merge gate after both B3 packages had handed over. The same section is in `handoff_CAM-ACTIONS.md`
and `handoff_CAM-RENDER.md`. The merge gate read every `handoff_*.md`, TIER1_INTEGRATION (1.9, 2.4 B3, 3, 5),
TIER1_DECISIONS and TIER1_CAMERA. It then ran two things on the merged tree, with the union of both packages:

* the full gate;
* the cross-package checks of TIER1_CAMERA 17 and 18.1 phase 2: the camera test files, the performance tests,
  `perception_audit` and `fuzz` with `--camera geometric`, the SearchAgent and the golden. It also checked the seams
  between the two packages.

Two places did not fit together. Both are fixed inside the union of the two packages' files, each with regression
tests:

1. **Things packed into one box hid each other.** File: `house_sim/kernel/sight.py`, which CAM-ACTIONS owns in B3.
   This is CAM-RENDER's request 2 and known gap.
   * Cause: an overfull shelf keeps its extra rows at the back, so two things in one container can share one box.
     Each of them then blocked every ray to the other. A wrist peek inside never showed either thing. This is why the
     SearchAgent (CAM-RENDER) could not find the cup in test_kernel's fetch houses that put it into a cabinet, even
     with CAM-ACTIONS' `peek ... inside`.
   * Fix: for a sample of a thing "in" a container, `_sample_transmission` now skips a neighbour whose box contains
     that sample, because a thing cannot be in front of a point inside itself. A proper front row still hides the
     back row.
   * Scope: geometric mode only, because room mode never calls sight.
   * Tests:
     * `tests/test_camera_actions.py` `SightFixTests.test_things_packed_into_one_box_do_not_hide_each_other`. It fails
       on the pre-merge rule, which gives a visible fraction of 0.25 instead of 1.0.
     * `tests/test_camera_realism.py` `SearchAgentTests.test_the_search_agent_finds_a_cup_inside_a_cabinet`, on
       seeds 1 and 3. The oracle case uses test_kernel's 10 cm cabinets and fails before the fix. The realistic case
       uses a 0.4 m cabinet.
   * Docs: `docs/KNOWN_LIMITS.md` now states the packing rule.
2. **The harness rebuilt episodes without the run's camera.** Files: `house_sim/eval/run.py`, `agents.py`,
   `leaderboard.py`, `viewer.py` and `dataset.py`. They belong to CAM-RENDER, which wrote them on a tree that did not
   yet have CAM-ACTIONS' `runner.build(..., camera=, perception=)`.
   * Cause: `build` applies a geometric spec's camera setup ops (`camera`, `view`, `track_view`, `view_watch`) only
     in a geometric run, and it decides this from the run's camera. The verifier passes the run's camera to `build`;
     the harness did not. A `--camera room` episode of a geometric spec therefore kept the `view` op's changes to
     entities, which the verified run of the same spec does not have.
   * Fix: every harness rebuild now passes the run's camera and perception. That covers the episode
     (`_run_episode`), re-scoring (`replay_log`), the dry-run estimate, the scripted oracle's shadow
     (`ScriptedOracle.prepare`), the leaderboard's `replay_episode`, the viewer's rebuild and the dataset exporter
     (perception "realistic").
   * Effect today: no authored spec declares the geometric camera, so no current result changes.
   * Test: `tests/test_camera_realism.py`
     `HarnessTests.test_the_harness_builds_episodes_in_the_run_camera_like_the_verifier`. It fails before the fix.
   * Docs: step 1 of the episode protocol in `docs/EVAL_HARNESS.md` is updated.

`house_sim/kernel/perception.py` was CRLF on disk. It was converted to LF with no content change.

**Gate.** The record is in `catalog/analysis/tier1/gate/B3-MERGE/`. It was taken on an isolated copy so that G10
sees only B3's changes:

* The base commit is the shared tree as CAM-RUNTIME handed it over: CAM-RENDER's 12:38 snapshot, which matches
  CAM-ACTIONS' pre-package copies of its own files.
* The working tree is byte-identical to the project tree, which was checked.

Command:

```
python tools/orchestration/tier1_gate.py --root <iso> --gates-root catalog/analysis/tier1/gate record --id B3-MERGE
       --package CAM-ACTIONS+CAM-RENDER --prev catalog/analysis/tier1/gate/CAM-RUNTIME
       --out catalog/analysis/tier1/gate/B3-MERGE --work <scratch>/gate_work --jobs 7
```

Summary line: `TIER1-GATE B3-MERGE [CAM-ACTIONS+CAM-RENDER] vs CAM-RUNTIME: FAIL (10 pass, 2 fail, 1 skip)`.

* **G0** PASS. The action fingerprint is `482f9adf9717` (allowed: CAM-ACTIONS).
* **G1** FAIL. There are 3,404 tests. `test_camera_actions.py` (55 tests) and `test_camera_realism.py` (48 tests)
  are green in both normal and strict runs. Exactly two tests fail, normal and strict, and both are outside the
  union:
  * `test_camera.py` `ModeTests.test_realistic_perception_with_the_geometric_camera_waits_for_cam_render`. It pins
    the guard that CAM-RENDER removed. This is request 1 of both packages.
  * `test_textiles_laundry.py`'s jug bound, PHYS-INT's R7 (PHYS-INT request 1).
* **G1b** FAIL. Of the kernel files run with core only, 14 of 15 are green; the one failure is the same
  `test_camera.py` pin. The 9 isolated-domain failures are known from CAM-RUNTIME.
* **G2** PASS. In own mode, 242 of 242 specs pass. Against CAM-RUNTIME there are 0 verdict, 0 state-digest and
  0 observation-digest changes.
* **G3** PASS. With realistic perception forced, 226 of 242 specs pass, with 0, 0 and 0 changes.
* **G4** PASS. The perception audit finds 0 leaks in both room and geometric mode.
* **G5** PASS. Layout counts are equal, and the generation-1 digests are 340 of 340.
* **G6** PASS. The fuzz runs 1,800 commands in each of room and geometric mode, with 0 defects and 0
  nondeterministic runs.
* **G7** PASS. 3.05 ms per substep (+2.3 %) and 1.081 s for the 1 h wait (+1.8 %), against limits of 3.129 ms and
  1.115 s.
* **G8** PASS. The room golden is byte-identical. It was not re-recorded.
* **G9** PASS. The camera tools ran.
* **G10** PASS. 34 changed files, none outside the union.

`compare CAM-RUNTIME -> B3-MERGE`: PASS.

**Cross-package checks beyond the gate.** They ran on the merged tree; the scripts are in the merge gate's scratch
folder.

* **The camera verbs under realistic geometric rendering, with every step leak-checked.** Each step went through
  `tools/perception_audit.py`'s `check_obs`, `check_message` and physics-note checks. The run covered 8 generated
  houses with 200 steps each, which is 1,600 commands: 334 peeks (all five `where` values), 138 geometric
  inspects, 127 reads, 160 looks (around with rows 2, directions, things, pan and tilt, track), 86 faces and the
  postures. Result: 0 leaks, 0 errors and 0 internal refusals.
* **Replay determinism of episodes driven by the camera verbs.** Seeds 0-3, realistic geometric, 80 steps each.
  `HouseEnv.replay` in the same process, and in a fresh process with `PYTHONHASHSEED=987`, reproduces the state, the
  final observation and `realism.dump()` exactly. Saves made mid-episode load.
* **`read` grants end to end** with the real realism, not a stub. From 1.5 m only the 3 cm display is read, shown
  and kept. Within reach the wrist also reads the 4 mm label.
* **Every argument form of `look`, `face`, `peek` and `inspect where`** (compass, cell, degrees, handle, pan and
  tilt, track, rows) gives the same results in oracle and realistic mode.
* **The harness end to end.** `python -m house_sim --run-agent search --split dev --limit 2 --camera geometric
  --allow-unverified` runs, and the run, records and logs say `geometric`. `--viewer` exports the log, and
  `replay_log` re-scores it with the same step count.
* **SearchAgent in geometric mode.**
  * All 8 of test_kernel's fetch houses pass in oracle mode, including the four with the cup in a cabinet; before
    the fix those four failed.
  * The realistic mode passes the open-cup houses, and the cabinet houses when the cabinet is a real size.
  * The realistic mode still fails test_kernel's 10 cm cabinet: the 10 cm cup sticks out above it, stays at
    "recognize" and reads as "object". That is a toy-geometry case.
  * Seed 7 in realistic mode needs more than the test's 600 steps; it is a 22-room house.

**Geometric preview of G3 for D3.** This is information only; it is not part of the B3 gate. All 242 specs ran
with realistic perception and `--camera geometric`, through CAM-ACTIONS' `run_trial(camera=)` and CAM-RENDER's
rendering. The command was `record --only G3 --camera geometric`, and the record is in scratch.

* There were 1,210 trials with 0 exceptions.
* 173 of 242 specs pass, against 226 of 242 with the room camera.
* 55 specs fail only with the geometric camera. In 54 of them the reference plan is refused, most often with
  "That is not visible here" on `approach`; in one, a wrong plan passes. These failures are the D3 triage list.
* Main finding for the camera owner, recorded but not fixed because it is outside B3's fit:
  * The open-door leaf occluder is a solid block. TIER1_CAMERA 4.4 and 5.1 specify it as "[0, 2.0] over the
    `geometry.door_swing` leaf cells" with porosity 0, which makes it a 0.5 x 1.0 m block next to the doorway.
  * So a robot that has just walked through an open hinged door and stands in the doorway cell sees almost nothing
    of the room.
  * Example: A-c83efc7adcd8f5-01, seed 2, `master_bedroom_1`. `door_2`'s leaf over cells [1,0] and [1,1] hides 61 of
    81 cells; the observation lists only `robot_headlamp_1`. `approach door_3` (4 m away along the same wall) is
    refused.
  * A thin leaf at its open position, or a doorway eye that sees past it, would remove most of these refusals.

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
