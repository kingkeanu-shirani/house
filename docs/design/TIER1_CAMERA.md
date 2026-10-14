# Tier 1: camera and visual perception (geometric camera)

Status: implementable spec (judge output, read-only round, 2026-10-13). Owner: the main session (kernel, core,
generator, tasks, eval). Domain adoption is in section 19. When phase 2 lands, this spec replaces the camera bullets
under "Not modeled" in docs/PERCEPTION.md.

## 0. Verdict, verification and corrections

The judge received one candidate design, "geometry-first": a symbolic but metric robot camera with an opt-in
`"camera": "geometric"` mode. The input announced two designs, but the JSON held only one, and its compatibility list
was cut off. So this spec is that design, checked against the working tree, corrected, trimmed and completed. Its
architecture is sound and is adopted:

- Keep today's containment and concealment layer.
- Add a pure geometry library.
- Add a camera runtime that writes logs the evaluator can see.
- Let realistic rendering cap information by optical acuity.
- Put all of this behind a per-spec switch that leaves today's behaviour byte-identical.

### 0.1 Claims checked against the code

| claim | result |
|---|---|
| Room-wide gate in `kernel/perception.py`: `visible` l.74 (containment, concealment until `revealed`, private parents, cover slots, opaque liquids, `visible_veto`); darkness < 0.2 without a robot light hides < 0.4 m; `camera_clean` < 0.4 hides < 8 cm and text; `observe` scans every entity | confirmed |
| `privacy_mode` removes nothing from observations | confirmed: `visible()` never reads it. Only navigation's `_no_camera` before-hooks on inspect/read refuse |
| `realism.room_context`/`context` (l.582-619): m += 0.12 per Chebyshev cell beyond 1; `_glare` (l.621): hours 7-9 and 16-18 plus any uncovered window in the room; `missed` (l.1181); mirror phantom doorway (l.1662) | confirmed |
| navigation `see_from`: 8 m lit, 4 m with own light, 0.75 m dark, 2 m dirty lens; blockers are `blocks_path` >= 1.2 m or `blocks_view`; results in `nav.seen` / `nav.visits[room].coverage` after move/look | confirmed (paths.py l.38-104) |
| private LOS/FOV copies: electrical `line_of_sight` (>= 1.2 m), people `_line_blocked` (>= 1.0 m) + `frame_analysis`, navigation `view_blocking_items` vs manipulation `_blocks_view` (different predicates), traverse `_floor_in_view`, `camera_frame` (60 degrees, 4 m, no LOS), `startle` (> 110 degrees) | confirmed |
| other copies the design did not list | people/social.py `frustum_items`/`private_in_frame` (78-degree laptop frustum, no LOS) and `eye_point`/`aim_at`/`ray_candidates`; safety/security.py `outside_view` observer and `doorbell_camera` inspector; cleaning's floor-cell visibility (`visible_soil`, `wet_floor_seen`); three domain copies of kernel `light_level` (climate model.py l.1185, electrical harms.py l.240, people common.py l.452) |
| 423 registered actions; `turn`, `tilt`, `scan`, `aim`, `point`, `sweep`, `rotate` are taken; `face` and `peek` are free | confirmed by loading the registry |
| `manipulation.world_pose` reads `robot.facing_deg` (default 0); nothing writes it | confirmed |
| climate window `facing_deg` is compass (0 = north wall, y = 0); grid angle = (compass - 90) % 360 | confirmed (sun.py header) |
| people already have an eye model | people/social.py: standing 1.55 m, seated 1.15, lying 0.8, child 0.6 + 0.07 x age |
| corpus | 242 specs in 32 files; 50 realistic specs in 16 files; only 2 uses of `look` in all specs |
| observe cost (measured on this machine, Python 3.14) | seed 7 (853 entities): oracle 2.4 ms, realistic 5.6 ms. Seed 0 (649 entities): 3.5 / 8.5 ms. The realistic overhead already uses 3.2-5.0 ms of the 10 ms bound in `test_realistic_observe_overhead_is_small` |
| viewshed cost (pure-Python DDA prototype in the scratch dir) | 0.10 ms for 10x11 cells, 0.13 ms for 11x12, 0.96 ms for 28x19, 1.37 ms for 30x24. Matches the design's 0.13 / 1.35 ms |

Engine facts the design missed (they change the plan):

1. **Action fingerprint.** `engine.actions_fingerprint()` hashes every action's params. `load()`/`replay()` and the
   viewer refuse a mismatch. New verbs or params therefore invalidate saved states, replays and the dataset
   fingerprint in every mode.
2. **Type props.** Type `props` are deep-merged into every spawned record. View defaults placed in props would change
   every room-mode state digest.
3. **No mutation counter.** `World` only bumps `_version` on create, destroy and rollback.
4. **Registry keys.** The registry rejects type keys other than `is_a`, `props`, `doc` and `perception`.
5. **Spec keys.** `tasks/spec.py` `ALLOWED` must gain `"camera"`.
6. **Verification evidence.** `reports/verification.jsonl` is keyed to the simulation code fingerprint, so any kernel
   change requires re-running `--check`.

### 0.2 Binding corrections to the design

- **C1 Body yaw.** Body yaw lives at `robot.facing_deg`, the field `world_pose` already reads, not inside `robot.view`.
- **C2 View properties.** View properties are type metadata: a per-type `"view"` block parsed by the registry like
  `"perception"`, never copied into records. Per-entity overrides exist only as an explicit `record["view"]`, written
  by geometric setup ops or domains.
- **C3 Hardware constants.** Camera hardware constants are module constants (`camera.HW`), not world state. A spec
  may override them through `robot.view["hw"]`.
- **C4 What observe() renders.** `observe()` renders only frames taken at the current eye position. Motion frames feed
  the logs only. This bounds observe cost to one viewshed.
- **C5 Identity tracking.** Tracked identity is updated once per step by the engine (`camera.end_step`), never inside
  `observe()`. Observations must not depend on how often `observe()` is called.
- **C6 Order of end_step.** `end_step` runs after `_advance`, not before. Its cached step view then matches the
  observation and the evaluation of that step.
- **C7 Person heights.** Person eye heights come from the people model (1.55 / 1.15 / 0.8 m; child 0.6 + 0.07 x age,
  max 1.55). Body top = eye + 0.12 m. The design's table (1.7 m; child 0.6 + 0.06 x age) put a child's eye above
  their head.
- **C8 Target size.** The target size `s` in the pixel formula is the largest dimension. The design's 0.8 factor
  contradicted its own range table.
- **C9 Under/behind concealment.** In geometric mode, concealment under or behind a support is decided by geometry,
  not by the `revealed` flag. Otherwise the under/behind formulas would never apply. Light portable supports
  (cushions, pillows, rugs, mats, laundry, mass <= 5 kg) are lifted by `inspect`/`peek where=under`, with no gap
  needed. Otherwise searching under cushions would become impossible.
- **C10 Wrist access.** Actions may target reachable things through the wrist camera and its LED. Observations list
  only what a camera framed this step: head frames, peeks, held items.
- **C11 IR shapes.** The IR "dark shape" rule keeps today's 0.4 m threshold (the design proposed 0.3 m).
- **C12 Top-opening containers.** Boxes, bins, drawers, baskets and pots get a rim-occlusion test. The design only
  modelled front apertures.
- **C13 visual_event.** `camera.visual_event` keeps `event_log` entries byte-identical to the `w.emit` calls it
  replaces. Geometry arguments are keyword-only and are not logged.
- **C14 Fingerprint scope.** "Byte-identical" means state digests and observation digests. The action fingerprint
  changes once, and saved states, replays, dataset fingerprints and verification evidence are regenerated
  (section 15).
- **C15 Cache invalidation.** `World` gains a mutation counter so occluder caches invalidate cheaply.
- **C16 Dropped and kept sensors.** `camera_strip` is dropped (non-goal). The 24-sector lidar ring is kept: it is
  cheap and lets the robot move with cameras off.
- **C17 Coverage needs light.** Floor coverage requires light >= 0.12 at the cell. Darkness scanned by IR alone does
  not count as surveyed.
- **C18 Look defaults.** `look` frames use tilt -10 (the design used -20, which cut off thermostats and wall cabinets).
  `rows=2` adds a lower row at -45 for near-floor coverage, instead of an upper row.
- **C19 Approach.** `approach` ends with the body facing the target and the head aimed at it, at no extra time.
- **C20 Viewshed geometry.** The viewshed stores merged blocked slope intervals per cell, not only over/under maxima.
  A ray that passes under a table and then over a low box is visible.
- **C21 Under examples.** The under-furniture examples are recomputed (5.4); the design's numbers did not follow from
  its formula.
- **C22 Domain list.** Section 19 lists every private copy in 0.1 for migration.

## 1. Goals and non-goals

### Goals

- **G1** Line of sight inside a room, with partial occlusion by furniture, people, pets, open door leaves and carried
  loads. This includes seeing under, behind and on top of furniture and inside containers, with honest absence
  claims (an item that was never in view was never seen).
- **G2** A posed camera: body yaw, head pan and tilt, a field of view, eye height by posture and step stool, and
  scanning actions with real time costs.
- **G3** Optical acuity: a pixels-on-target model decides detect, recognize, identify, inspect and read. It covers
  small and low-contrast items, distance, light, a dirty lens, motion and haze.
- **G4** A per-target light field: ambient light, shading under, behind and inside furniture, lamp pools, sun patches,
  robot beams (headlamp, flashlight, wrist LED) and appliance interior lights. Plus directional glare: sun in frame,
  backlight and specular glare.
- **G5** Logs the evaluator can see, so camera-dependent catalog entries are checked on world state: coverage,
  looked-in containers, peeks, sightings, supervision time and gaps, frame-based privacy captures, and visual events
  seen or missed.
- **G6** One shared sight API for every viewer: the robot, people, device cameras, and viewers outside windows and
  doors. The private copies are retired.
- **G7** Room mode (today) stays byte-identical in state and observation digests. Oracle verification and the 242
  verified specs are unaffected.
- **G8** Deterministic, replayable, pure Python 3.9 standard library, and inside the performance budget (section 16).

### Non-goals and honest limits (documented in PERCEPTION.md and KNOWN_LIMITS.md)

- No pixels, images, depth maps, learned detectors, image noise, motion blur or rolling shutter. Detection is a
  closed-form function of geometry.
- **Approximate acuity.** The thresholds are engineering approximations (Johnson-style criteria; COCO-style small
  objects under 32 px are unreliable). They are not calibrated against a real detector. Fidelity: G for line of sight
  and occlusion at cell-and-box resolution; P-approx for detection, light and acuity.
- **Simple shapes.** Occluders are axis-aligned boxes on the 0.5 m grid, with height spans and porosity. Entities are
  boxes with at most 5 sample points. Partial occlusion is reported in quarters.
- **Scalar light.** Light is a scalar per point. There is no colour temperature, no cast shadows beyond the
  under/behind/inside shading factors, and no indirect bounce beyond room ambient.
- **Separate rooms.** Rooms remain separate grids. Views across rooms go only through doorway and window apertures
  (phase 3). There is no outdoor facade or roof geometry beyond `faces` and eye height.
- **Simplified hardware.** The wrist camera has no arm kinematics; reach is `geometry.reach_problem`. Head and body
  motion use fixed rates.
- **Handles follow true ids.** A detect-level "object" can be told apart by its stable handle after the robot once
  identified it, even if it was swapped unseen. This limit exists today and is amplified here; see open questions.
- Sound direction is out of scope (only `camera.bearing_to` is offered for domains). Mirrors are optional (phase 4).

## 2. Architecture

| layer | module | role |
|---|---|---|
| L0 (kept) | `kernel/perception.py` | containment, closed containers, cover slots, private parents, opaque liquids, `visible_veto`, people/pets `concealed` |
| L1 (new) | `kernel/sight.py` | pure geometry: room frame, occluder spans, interval viewshed, sample points, relation rules, viewers and FOV, LOS, light field, glare, faces, carried loads, lidar, apertures |
| L2 (new) | `kernel/camera.py` | the robot's cameras: mode, pose and frames, auto-gaze, step view, acuity, `can_see`, logs, watch system, privacy captures, visual events, device feeds |
| L3 (extended) | `kernel/realism.py` | acuity caps tiers and identity, per-target context, detection band, face gating, tracked identity |

Two independent switches:

- `perception`: `oracle` or `realistic`, as today.
- `camera`: `room` or `geometric`. Default `room` everywhere.

Precedence for the camera mode: CLI `--camera`, then `HouseEnv(camera=...)`, then the spec's `"camera"`, then `"room"`.

In room mode, `engine.reset` installs nothing, and every camera function returns at its first line. The kernel tests
the mode with `camera.mode(state)`, which returns `"geometric"` exactly when `"view" in state["robot"]`.
`env.camera_mode` is authoritative: reset installs or strips `robot.view` and `camera_log` to match it.

## 3. Conventions

- **Room frame.** Metres. x is the column direction (east), y is the row direction (south), z is up. Cell (c, r) has
  centre ((c + 0.5) x 0.5, (r + 0.5) x 0.5). This is the frame of climate/sun.py and manipulation.world_pose.
- **Angles.** The grid convention `deg = atan2(dy, dx)`: 0 = +x (east), 90 = +y (south), clockwise on a north-up
  map. This is the convention of people, navigation and manipulation. Compass fields (window `facing_deg`) convert
  with `sight.grid_deg(compass) = (compass - 90) % 360`. Fields are not renamed.
- **Relative bearings** shown to agents are relative to the body heading, wrapped to (-180, 180]. Positive is to the
  robot's right. Elevation is positive up.
- **Entity yaw** is `yaw_deg` if present, else `rot` (manipulation.yaw_of).
- **Entity front**, `sight.front_deg(state, eid)`, the first rule that applies:
  1. explicit `view.front_deg`;
  2. located in or on a parent with a front: the parent's front + the entity's own yaw;
  3. room-located and touching the perimeter: the inward normal of that wall (`geometry.wall_inward`; at a corner,
     the wall along the longer footprint side);
  4. otherwise: toward the room centre.
- **Numerics.** Positions are rounded to 1e-3 m and stored angles to 0.01 degrees. Slope and dot-product comparisons
  use EPS = 1e-9. Decisions use dot and cross products against precomputed cos/tan of the FOV constants; trigonometry
  is used only for reported values.

## 4. Data model

### 4.1 Robot fields (geometric mode only; written by `camera.install` at reset)

```
robot.facing_deg = 90.0      # body yaw (grid convention). Initial: toward the room centre from the robot cell (90 if at the centre)
robot.view = {
  "pan_deg": 0.0,            # head pan relative to the body, [-150, 150]
  "tilt_deg": -10.0,         # head tilt, + up, [-60, 35]
  "aim": null,               # [x, y, z] the head points at (look target / auto-gaze); null = free
  "track": [],               # agent ids the head keeps in frame while the action is idle (look track=true)
  "frames": [],              # frames of this step: [rid, x, y, z, yaw, tilt, cam, kind, speed] (cam head|wrist; kind look|gaze|motion|idle|peek)
  "events": [],              # visual events seen this step (perceived form, <= 20)
  "step": 0,                 # the step the frames and events belong to
  "hw": {}                   # optional per-spec hardware overrides (deep-merged over camera.HW)
}
robot.camera_on = True       # privacy_mode == True forces camera and mic off (legacy flag kept)
robot.mic_on = True
robot.privacy_policy = None  # or {"camera_off": [room types], "mic_off": [room types], "when": "occupied"|"always"}
```

- **Eye height** = `HW.head.height_m[posture]` + `standing_on.standing_height_m`. Posture is `"fallen"` when
  `robot.fallen`.
- **Head yaw** = `facing_deg + pan_deg`.
- `ROBOT_DEFAULTS` in `schema.py` is not changed. Room-mode records never gain these keys.

### 4.2 Hardware constants (`camera.HW`)

Constants follow an RGB-D head camera (Intel RealSense D435 class) and a wrist camera (D405 class).

```
HW = {
  "head":  {"hfov_deg": 69, "vfov_deg": 42, "res_px": [1280, 720], "focal_px": 931,
            "height_m": {"standing": 1.35, "crouching": 0.85, "kneeling": 0.55, "fallen": 0.30},
            "pan_dps": 120, "tilt_dps": 90, "pan_limit_deg": 150, "tilt_limits_deg": [-60, 35], "max_range_m": 12.0},
  "depth": {"hfov_deg": 87, "focal_px": 447, "range_m": [0.2, 6.0], "ir_range_m": 4.0},
  "wrist": {"hfov_deg": 87, "focal_px": 337, "min_focus_m": 0.07, "led": {"power": 0.12, "half_deg": 45}, "deep_m": 1.0},
  "lidar": {"height_m": 0.25, "range_m": 8.0, "sectors": 24},
  "body_turn_dps": 90, "capture_s": 0.4,
  "tilt": {"look": -10, "walk": -15, "low_row": -45, "up": 20, "down": -45}
}
```

Focal length in pixels: `focal_px = res_w / (2 tan(hfov / 2))`. Head 931, wrist 337, depth 447.

### 4.3 Entity view properties (type metadata, never-tier)

How properties are resolved, `sight.view_props(state, eid)`, in increasing precedence:

1. `sight.CATEGORY_DEFAULTS` (4.4);
2. the type's `"view"` block, merged along the `is_a` chain (`registry.view_props(type_name)`);
3. the record's own `"view"` dict.

The result is memoized per (type, record view digest).

| key | meaning | default |
|---|---|---|
| `porosity` | fraction of light passing through the body (0 = opaque) | 0. `transparent` or material glass/clear_plastic/acrylic: 0.9 |
| `clear_below_m` | open height under the piece (legs) | 0 |
| `below_porosity` | transmission of the band under it | 1.0 |
| `slab_m` | top slab thickness when the sides are open (tables, desks) | null |
| `behind_gap_m` | gap to the wall behind a wall-adjacent piece | 0.05 |
| `opening` | `"front"` (cabinets, wardrobes, fridges, shelving), `"top"` (boxes, bins, drawers, baskets, pots, tubs) or `"none"` | `"front"` for storage furniture; `"top"` for portable containers and drawers |
| `interior_light` | lights its interior when open (fridge, closet with light on) | false |
| `front_deg` | explicit front | derived (section 3) |
| `faces` | `{face: local yaw deg relative to front, or "up" / "down" / "all"}` | `{}` |
| `text_m` | `{field: character height in m}` for read-tier fields | by field (4.5) |
| `glossy` | specular surface (screens, glass, glossy print, polished stone, stainless steel) | false |
| `camera` | device camera spec `{hfov_deg, vfov_deg, range_m, focal_px, facing_deg, height_m, ir}` | absent |
| `beam` | light beam `{power, half_deg}` | flashlight `{1.8, 15}`, headlamp `{0.45, 30}` |
| `height_m` | occluder height override (agents, dynamic things) | derived |

- **Never-tier.** `view` and `inner_spos` are added to realism `DEFAULT_TIERS["hidden"]`.
- **Registry.** Accepts a type key `"view"` (validated as a dict) and exposes `registry.view_props(name)`.
- **No collision with existing fields.** The existing entity field `camera` (safety's doorbell test) and `fov_deg` /
  `camera_facing_deg` (people) remain domain data until those domains migrate. The kernel only reads `view.camera`.

### 4.4 Category defaults (`sight.CATEGORY_DEFAULTS`, overridable per type)

Overrides are needed where `is_a` misleads: sofa is_a chair, nightstand is_a table.

| category / type | top | clear_below_m | porosity | notes |
|---|---|---|---|---|
| bed | size h | 0.25 (divan / storage beds: type override 0) | 0 | under-bed shading x0.3 |
| sofa, armchair, recliner | size h | 0.10 (skirted types: 0) | 0 | |
| chair (dining/office), stool, bench | size h | 0.42 | 0.6 | open back and legs |
| table, desk, coffee table, side table | size h | h - slab | slab 0.04; below 0.85 (desk 0.4) | items under tables stay visible |
| nightstand, dresser, cabinet, wardrobe, counter, vanity | size h | 0 | 0 | front opening |
| bookshelf, open shelving | size h | 0 | 0 (back panel) | front opening, always open |
| fridge, freezer | size h | 0 | 0 | front opening, `interior_light` true, `behind_gap_m` 0.08 |
| washer, dryer, dishwasher, oven, microwave | size h | 0 | 0; door window `transparent` per type | `behind_gap_m` 0.08 |
| large plant (> 0.8 m) | size h | 0 | 0.5 | |
| window covering | at the window | | 1 - 0.95 x covered (sheer types 0.6) | read from the window's `covered` |
| person | standing: eye + 0.12; sitting: 1.15 + 0.12; lying: surface + 0.25 | 0 | 0.3 | dynamic; eyes per C7 |
| pet | dog small 0.35 / medium 0.55 / large 0.7; cat 0.3; other 0.2 | 0 | 0.5 | dynamic |
| robot (for other viewers) | `robot.height_m` (1.6) | 0 | 0.4 | dynamic |
| open door leaf | 2.0 over the `geometry.door_swing` leaf cells | 0 | 0 (glass panel types 0.6) | |

### 4.5 Text sizes (reading needs >= 10 px per character)

Defaults by field. Override with `view.text_m`.

- Head-camera read range = `931 x text_m / 10`.
- Wrist read range = `337 x text_m / 10`, never below the 0.07 m minimum focus.

| field(s) | text_m | head range | wrist range |
|---|---|---|---|
| label_text, address, recipient, expiration, expires, best_by, use_by, date_label, warning | 0.004 | 0.37 m | 0.13 m |
| text (documents), instructions, directions, dosage, steps, patient, prescribed_to | 0.003 | 0.28 m | 0.10 m |
| care_label, serial, sku, barcode, model | 0.0025 | 0.23 m | 0.08 m |
| display (appliances, thermostats), keypads | 0.010 | 0.93 m | 0.34 m |
| title (book spines), name_label, brand | 0.008 | 0.74 m | 0.27 m |
| sign, poster; screens (TV, monitor) | 0.030 | 2.79 m | |

### 4.6 Faces

- **Naming.** `view.faces` names sides in the entity's local frame, for example
  `{"label": 0, "rim": "all", "bottom": "down"}`, with yaw values relative to the front.
- **label_facing.** The admin_inventory field maps automatically: `top`/`up` = up, `bottom`/`down` = down,
  `front`/`out` = front (0), `back`/`in`/`wall` = 180, `left` = 270, `right` = 90.
- **Default gating.** When an entity has `label_facing` (or a `"label"` face), that face gates `label_text`,
  `recipient`, `address`, `barcode` and `order_id`.
- **Explicit gating.** A type perception block may gate fields by face:
  `"perception": {"faces": {"label_text": "label", "damage.chip": "rim"}}`.
- **No faces declared:** no gating (wrap-around labels).

When a face is seen in a frame (`sight.faces_seen`):

- **Side faces.** The face's world normal must be within 80 degrees of the vector from the target to the eye
  (cos > 0.17).
- **"up"** is seen when eye z > the entity's top + 0.05, unless another entity rests on it covering >= 50% of its top
  area (stacked boxes).
- **"down"** is never seen while the entity rests on something.
- **Blocked faces.** A face whose normal points into a solid within 0.05 m (a wall behind, a neighbour on the shelf,
  the support below) is blocked for every camera.
- **Held items.** `inspect`/`peek where=closely` on a held item sees all faces (+3 s).

### 4.7 `camera_log` (new top-level section; geometric mode only; journaled with `w.sec("camera_log")`)

```
camera_log = {
  "seen_cells": {rid: "0110..."},          # floor cells (row-major bits) seen in any frame this episode
  "coverage":   {rid: 0.73},               # seen / free floor cells (geometry.blocked_cells(include_agents=False) excluded)
  "sightings":  {id: {"first_s": t, "last_s": t, "best": "detect"|"recognize"|"identify", "faces": [...]}},
                                           # people, pets and ids in track_ids only
  "track_ids":  [ids],                     # from setup op track_view
  "looked_in":  {container_id: time_s},    # interior framed (5.4) with >= 32 px aperture and interior light >= 0.2
  "peeked":     {"<id>:under|behind|inside|top|closely": time_s},
  "in_view_s":  {agent: s}, "gap_s": {agent: s}, "max_gap_s": {agent: s},
  "captures":   [{"person", "room", "time_s", "sensor": "camera"|"mic", "via": "direct"|"doorway"|"window", "activity"}],  # <= 50
  "capture_keys": ["pid:activity:start_s", ...],   # one capture per person per activity episode
  "events":     [{"step", "time_s", "kind", "source", "seen": bool}],   # <= 50
  "watch_specs": {name: {...}},            # from setup op view_watch
  "watch":      {name: {"visible": [ids], "ever": [ids], "fraction": {id: f}, "clear": bool, "ever_clear": bool,
                        "robot_in_view_s": s}}
}
```

## 5. Geometry engine (`kernel/sight.py`)

### 5.1 Room geometry and caches

`room_geom(state, rid, world=None) -> RoomGeom` collects four kinds of occluder from the room.

**Static spans per cell:** a list of `(z_lo, z_hi, porosity, eid)`. Sources:

- floor-standing entities (`geometry.floor_standing`): span `[clear_below, top]`, plus the porous band below if
  `clear_below > 0`;
- tables: `[top - slab, top]` plus the band below with `below_porosity`;
- wall mounts at least 0.25 m deep (wall cabinets): `[mount, mount + h]`;
- items resting on a room-located surface that are at least 0.25 m tall and wide (TVs, monitors, boxes):
  `[surface top, surface top + h]` at their `spos` cell;
- open door leaves: `[0, 2.0]` over the `door_swing` leaf cells.

The room's walls are the grid boundary.

**Dynamic agents:** a list of `(cell, z_top, porosity, id)` for people, pets and (for viewers other than the robot)
the robot. They are tested per ray, not cached.

**Opening containers:** `{eid: (cells, front_deg, opening, inner box, shelf z list, open)}`.

**Derived occluders:** `@hook("view_occluders") fn(state, rid) -> [(cells, z_lo, z_hi, porosity, source)]`, for example
a smoke layer, steam, a drying rack with laundry, tall grass or an open umbrella.

**Caching.**

- **`occ_sig(state, rid)`.** A digest of `(eid, location, pos, rot, yaw_deg, size_m, view, open, covered,
  door_swing state)` over the span sources, plus the derived occluders.
- **Span sources** come from `world.static_index("sight.occluders", ...)` (static predicate: largest dimension
  >= 0.25 m, not ground layer, not wall/floor/ceiling surface), filtered dynamically by room.
- **Memo.** Inside one World the signature is memoized by `(rid, world.mutation_count)`. This is the counter added
  by C15: it is incremented by every journaled write (`mut`, `*_m`, `sec`, `set_scalar`, `set_path`, `create`,
  `destroy`, `robot_m`, `rollback`).
- **LRU caches.** Module-level, content-keyed caches: RoomGeom by `occ_sig` (64 rooms) and viewsheds by
  `(occ_sig, eye cell, round(eye z, 2))` (64 x 32). They are pure, safe across envs and processes, and never change
  results.

### 5.2 Interval viewshed (one per eye, independent of heading)

For eye E = (ex, ey, ez) and each cell c within `max_range_m` (head 12 m), the Amanatides-Woo DDA walks the cells
strictly between E's cell and the centre of c. Each crossed cell b has horizontal entry and exit distances
`d_in`, `d_out` along the ray. Each span (z_lo, z_hi, p) in b blocks the slope interval:

```
hi = (z_hi - ez) / (d_out if z_hi <= ez else d_in)        # highest slope the top subtends over b
lo = -inf if z_lo <= 0 else (z_lo - ez) / (d_in if z_lo <= ez else d_out)
```

- Spans with p < 0.5 are solid; their intervals are merged into a sorted list `B(c)`.
- Porous spans (p >= 0.5) go to `P(c)` as `(lo, hi, p)`.
- The viewshed also stores `E(c)`, the entry distance into c.

A point p = (x, y, z) in cell c, at horizontal distance d and slope `s = (z - ez) / d`, has:

```
transmission(p) = 0 if s lies inside any interval of B(c) (EPS-inclusive)
                  else product of p_k over P(c) intervals containing s  x  product over dynamic-agent hits
```

- **Dynamic agents.** An agent in a crossed cell blocks `[-inf, (z_top - ez)/d]` with its porosity (a person is 0.3,
  so it attenuates rather than blocks).
- **Own cell.** Spans in c itself that do not belong to the point's own support or container block with
  `d_in = E(c)` and `d_out = d`. An entity never occludes itself.
- **Single-frame wedge.** For a single frame, only cells whose centre bearing lies within hfov/2 + 15 degrees of the
  camera yaw are computed (about 5x cheaper). A look-around computes the full viewshed.

### 5.3 Entity placement and sample points (`sight.entity_box`, `sight.sample_points`)

**Position:**

- room-located entities: footprint rectangle (`geometry.footprint_m`), z from `geometry.height_m` to + h;
- on a surface: parent origin + `spos` (rotated as people.aim_at does) at z = `height_m(state, eid)`;
- hung: at the hang height;
- in containers: 5.4e;
- on people or pets: their position at z 0.9 / 0.2;
- held: the robot (always visible);
- doors and windows (`connects`, mounted): their wall cell, z at mid-height.

**Samples:**

- largest dimension >= 0.3 m: 5 samples (top centre, centre, and the two upper corners of the side facing the eye at
  0.9 h, plus the opposite pair at 0.9 h);
- 0.1-0.3 m: 2 samples (top centre, centre);
- under 0.1 m: 1 sample (centre).

**Visible fraction** = the mean transmission of the samples that pass the relation rule, rounded to the nearest
quarter (half up). `occluded = 1 - visible_fraction`.

### 5.4 Relation rules (applied before the viewshed test of each sample)

**a. On a floor or surface.** The support's own spans are skipped when `z >= support top - 0.02`. A top above the
eye uses the near-edge rule: the point is visible only if `s >= (T - ez) / E(c)`. Example: a wardrobe top at 2.0 m,
eye 1.35, item 0.3 m back from the front edge hides items shorter than `(T - ez) x g / d_edge`: 0.39 m at 0.5 m from
the front, 0.065 m at 3 m.

**b. Behind** (relation `behind`, the point sits in the support's back gap g = `behind_gap_m`, support top T_S < ez).
The point is visible over S iff

```
z_top >= ez - (ez - T_S) x d_t / (d_t - g)
```

d_t is the horizontal distance to the item. If T_S >= ez, it is never visible from the room. The crossed-cell test
excludes S's own cells. Example: a sofa (0.85 m), g = 0.1, eye 1.35, d_t = 1.2 m requires z_top >= 0.80 m, so a toy
on the floor behind a sofa needs a peek.

**c. Top** (an item on a support whose top is above the eye) uses rule a. A step stool raises the eye.

**d. Under** (relation `under`, support S with clearance L = `clear_below_m`). Let d_near be the horizontal distance
to S's near edge along the ray. The point is visible iff `ez - (ez - z_top) x d_near / d_t <= L`, no blocker solid
to the floor lies on the ray before S, and L > 0. Shading x0.3 (6.2). For L = 0.25 m and a 5 cm item 0.5 m inside
the edge:

| posture | eye height | needs d_near >= |
|---|---|---|
| kneeling | 0.55 m | 0.75 m |
| crouching | 0.85 m | 1.5 m |
| standing | 1.35 m | 2.75 m |

With the item 1 m inside, kneeling needs d_near >= 1.5 m. Light under the bed is a third of ambient, so a realistic
under-bed search needs kneeling plus a light, or a peek.

**e. In a container** (an L0-visible child: open, transparent or open shelving):

- **front opening:**
  - The eye must be in front: the angle between (eye - container) and `front_deg` <= 75 degrees.
  - The aperture centre and its 4 corners must pass the viewshed test.
  - **Interior rows.** `inner_spos` [x, y] is explicit (setup `row`) or deterministically packed. Items per shelf are
    ordered by blake2b(eid) and filled left to right; a new row behind starts when the summed widths exceed the
    inner width.
  - **Back item behind a front item.** A back item j behind a front item k (row gap g, front top h_k above the shelf
    base z_s, horizontal eye distance D) is visible above `z_s + h_k - g x (ez - z_s - h_k) / D`, clipped by the next
    shelf board.
  - **Lateral overlap** uses interval coverage.
  - **Occluded fraction** = 1 - (lateral visible share x vertical visible share).
- **top opening:**
  - The eye must be above the rim R.
  - With d_r the horizontal distance to the near rim and g the item's horizontal offset beyond it, the item is
    visible above `R - (ez - R) x g / d_r`. Example: a 0.3 m deep box on a counter (rim 1.2 m), eye 1.35 at
    d_r = 0.5 m: an item 0.2 m past the rim is visible only if it rises above 1.14 m, so it must be at least 0.22 m
    tall.

**f. Held:** always visible. **Hung:** treated as "on" at the hang height. **Part/attached:** treated as the parent
at its own size, with L0 slot rules.

**g. People and pets** are sampled at head and torso (2 samples). L0 `concealed` still applies.

**Interaction with `revealed`.** In geometric mode, L0's "under/behind until `revealed`" rule is replaced by b and d.
`peek`/`inspect where=` still set `revealed` (domains read it). It means "the robot looked there"; it does not make
the item visible from everywhere.

### 5.5 Viewers and frames

`Viewer(rid, x, y, z, yaw, tilt, hfov, vfov, range_m, focal_px, kind, ignore)` is a namedtuple. A point is in frame
iff all of these hold:

- `|wrap(bearing - yaw)| <= hfov/2`;
- `|elev - tilt| <= vfov/2`;
- 0.05 <= distance <= range.

Factories:

| factory | definition |
|---|---|
| `camera.head_viewer(state, frame=None)` | head camera at the frame's pose (default: current pose) |
| `camera.wrist_viewer(state, eid, where)` | at the target's reachable edge, looking along the access direction |
| `sight.person_viewer(state, pid, attention=False)` | eye by posture and age (C7); hfov 200 for awareness, 120 for attention; vfov 120; range 15 m; yaw = person `facing_deg` |
| `sight.device_viewer(state, eid)` | from `view.camera` (doorbells, webcams, security cams, a phone on a tripod) |
| `sight.aperture_viewer(state, aperture_id, outside_m=3.0, eye_z=1.6)` | phase 3: a viewer outside a window or glass door |

### 5.6 Carried loads (`sight.carried_occluder`)

There is one predicate, replacing navigation's and manipulation's two heuristics. A load blocks the view when the
item is held with `held_hand == "both"` and its horizontal dimensions are >= 0.35 x 0.25 m and its height is
>= 0.25 m, or when it has `blocks_view`. Trays (`carry_level`) are excluded.

**Model:** the load bottom is at 0.85 m (hip carry), its top at z_L = 0.85 + h, its front at x_f = 0.35 + d/2 (d is
the smaller horizontal dimension, carried across the body), and its half-width half_w = w / 2.

**Effect:** points within `|bearing| <= atan(half_w / x_f)` and below the line from the eye over the load's top are
hidden. For the floor that means `d < ez x x_f / (ez - z_L)`. Example: a 0.6 x 0.4 x 0.3 m basket gives z_L = 1.15
and x_f = 0.55, so the floor is hidden within 3.7 m inside +-28.6 degrees. `camera.floor_visible_ahead(state, d_m)`
uses this.

### 5.7 Lidar ring (`sight.lidar_ring`)

24 sectors of 15 degrees relative to the body heading, at 0.25 m. Each value is the free distance to the first span
covering 0.25 m, or to an agent, up to 8 m, rounded to 0.25 m; `null` means nothing within range. It does not depend
on light or privacy mode. Cost: 24 DDA rays, about 0.3 ms.

### 5.8 Doorway and window apertures (phase 3)

- **Doorways.** An open doorway (an open door or no door) is an aperture 0.85 m wide (or `width_m`) and 2.0 m high.
  Room B's frame is placed against room A by the rigid transform that abuts the two doorway cells across the wall:
  the rotation maps B's inward normal to -(A's inward normal), and the translation puts B's doorway cell one cell
  beyond A's along -(A's inward normal). Rays from the eye that cross the aperture segment continue into B. B is
  viewshed from the transformed eye, restricted to the aperture wedge and to rays whose height at the wall line lies
  within [0, 2.0]. B's open door leaves occlude. This replaces mechanisms' 3x3 glimpse. It enables captures through
  doorways, supervision through a doorway, and "keys visible from the entry".
- **Windows.** A window is an aperture from its sill to its head along its wall cells. Transmission is multiplied by
  the covering factor `1 - 0.95 x covered`.
- **Seeing in from outside.** An `aperture_viewer` stands `outside_m` outside, at `eye_z`. Day reflection: when the
  outdoor light exceeds 2 x the interior L_at of a target, targets more than 1 m inside are not visible from outside
  (glass reflects in daylight). At night, a lit interior is visible.

### 5.9 Mirrors (phase 4, optional)

The eye is reflected across the mirror plane, and the viewshed is run from the virtual eye, clipped to the mirror
rectangle. Hits are detections with `via: "mirror:<id>"` and a bearing toward the mirror. The un-inspected phantom
doorway percept stays, now only when the mirror is in frame.

## 6. Light and glare

### 6.1 Light at a point: `sight.light_at(state, rid, p, beams=())`

```
L = ambient(rid) x shade(p) + sum(hook "light_at"(state, rid, x, y, z)) + sum over active beams of beam(p), clamped to [0, 1]
```

**Ambient:** `perception.light_level(state, rid)`, unchanged. It is the room's windows and lights, without robot
lights.

**Shade:**

| where | factor |
|---|---|
| under a covering span (z < that span's z_lo) | 0.3 |
| inside a front or top opening | 0.6 |
| inside, with `interior_light` and the container open and powered | 1.0 |
| in a behind gap | 0.4 |
| elsewhere | 1.0 |

**Hooks:** `@hook("light_at") fn(state, rid, x, y, z) -> additive float`. Electrical adds lamp pools
(+0.3 x b x max(0, 1 - r/1.5)). Climate adds sun patches (+0.3 in sunlit cells). These are local boosts on top of the
uniform ambient term, documented as such.

**Beams:** if the angle from the beam axis is <= half_deg, the beam adds `power / max(r, 0.2)^2`.

| beam | power, half-angle | axis | reach |
|---|---|---|---|
| headlamp (worn, on) | 0.45, 30 degrees | head pose | 0.2 at 1.5 m |
| flashlight (held, on) | 1.8, 15 degrees | head aim (a simplification) | 0.2 at 3.0 m |
| wrist LED | 0.12, 45 degrees | during a wrist frame | >= 0.35 within 0.585 m |

Light is computed once per entity centre (not per sample) and memoized per observe or step-view by (cell, z band).

**Legacy gate.** `perception.lit()` (room ambient, or any robot light) stays the gate for move-into-dark, inspecting
people in the dark, and the `dark` observation flag.

### 6.2 Bright sources and glare

**Sources:** `@hook("bright_sources") fn(state, rid) -> [{"x", "y", "z", "radius_m", "level", "kind"}]`. Climate
provides sunlit window apertures and the outdoor sun direction; electrical provides bare bulbs, lit lamps and bright
screens. Kernel fallback when no hook answers:

- uncovered windows at `daylight x (1 - covered)`, with radius half the window width, at mid-height;
- lit lights at their mount point with level = brightness x dim_level.

**Sun in frame.** It applies when both hold:

- `@hook("sun_beam") fn(state, rid, x, y, z) -> unit vector toward the sun, or None` (climate, from `beam_at`) says
  the eye is in the direct beam;
- the sun vector is within hfov/2 + 10 degrees (+ 20 x `lens_scratch`) of the camera axis.

Without climate there is no sun-in-frame glare. Effects:

- the whole frame: m += 1.0;
- targets within 20 degrees of the sun direction: level capped at detect, with no colour or text.

**Backlight.** It applies when a source with level >= 0.5, farther away than the target, subtends the target's
direction (angular distance <= source radius + target radius) and `L_at(target) <= 0.6 x level`. Effects:

- colour, text and the soil glance are hidden;
- the level is capped at recognize;
- m += 1.5.

**Specular glare.** For `glossy` targets, reflect the view ray v about the face normal n: `r = v - 2 (v . n) n`
(n = up for horizontal faces). A bright source within 10 degrees of r makes the text unreadable ("Glare on it; change
the angle"). It also exposes the glossy soil kinds (streaks, fingerprints, residue, smudges) at identify level as
raking light. Cleaning consumes this through `camera.specular(state, eid)`.

## 7. Resolution and acuity

```
px     = s x focal_px / d               # s = largest dimension (or face size, or char height text_m); d = 3D eye distance, min 0.05
px_eff = px x k_lens x k_light x k_motion x k_haze x k_contrast x sqrt(visible_fraction)
```

| factor | definition |
|---|---|
| k_lens | 0.25 + 0.75 x camera_clean^2 (camera_clean already includes lens_scratch after cleaning) |
| k_light | clamp((L_at - 0.05) / 0.30, 0, 1)^0.5 |
| k_motion | 1.0 for stationary frames. Motion frames: careful 1.0, normal 0.8, fast 0.55 |
| k_haze | exp(-(0.6 x smoke + 0.4 x steam + 0.3 x fog) x d), from the room fields smoke / smoke_density, steam, fog |
| k_contrast | 0.5 when the target colour and the background colour (its support, else the room `floor_color` or the dark-floor materials slate, dark_wood, black_tile) are both in `sight.DARK_COLORS` and L_at < 0.45 (a black cable on a dark floor, a dark cat at dusk) |

Acuity levels:

| level | conditions | 0.1 m object, head camera |
|---|---|---|
| shape (IR) | L_at < 0.05; size >= 0.4 m; within 4.0 m in the depth hfov (87 degrees); depth px >= 24 | n/a |
| detect | px_eff >= 24 is sure; 10-24 is probabilistic (11.4); L_at >= 0.05 | sure within 3.9 m; never beyond 9.3 m |
| recognize | px_eff >= 32; L_at >= 0.12; not in the sun cone | 2.9 m |
| identify | px_eff >= 64; L_at >= 0.2; not backlit; not in the sun cone | 1.45 m |
| inspect-capable | head px_eff >= 160 and L_at >= 0.2, or wrist access (9.3) | 0.58 m (a 1 m fixture: 5.8 m) |
| read | char px (text_m x focal / d x k_lens x k_motion) >= 10; L_at >= 0.25; face seen; no specular glare; camera_clean >= 0.4 | see 4.5 |

**Oracle geometric mode has no probabilistic band:** a target is detected iff px_eff >= 17, the 50% point.

**Small items at the robot's feet.** A 2 cm item on the floor next to a standing robot is about 1.4 m from the eye:
13 px, detected with p = 0.2. Crouching (0.9 m, 21 px) gives p = 0.8, and kneeling makes it sure. A realistic search
for an earring needs a lower eye.

## 8. Per-step processes (geometric mode)

1. **`engine.step`, after `w.begin()` and the steps increment.** `camera.begin_step(w)` sets
   `view.frames = []`, `events = []` and `step = steps`, and applies `privacy_policy` to the effective sensors for the
   current room. Refused actions roll back to the previous step's frames. Their `view.step` then no longer matches,
   so they are ignored.
2. **Action.**
   - **Motion.** `move`, `approach`, `walk_to`, and nested navigation verbs through core. An after-hook in camera.py
     calls `camera.note_motion(w, room, path, speed)`.
     - Poses are sampled every 2 path cells plus the final pose, at most 12 per action (kind `motion`, k_motion).
     - Heading follows the path. The pan resets to 0 and the tilt to `HW.tilt.walk`.
     - At the end, the body faces along the last step. `move` faces `wall_inward` at the arrival doorway cell.
       `approach` faces the target and aims the head at its centre (C19).
     - Domain verbs that move the robot without core motion call `note_motion`, or the fallback in step 3 applies.
   - **`face` / `look` / `peek`** add frames and charge time (section 9).
   - **Auto-gaze.** `Ctx.obj` (touch, grasp, inspect, read and every domain verb using it) checks the target. If it
     passes `can_see` but is in no frame of this step at the current eye, the head turns to it. That costs
     `max(|dpan|/120, |dtilt|/90) + 0.2` s once per target per step (`ctx.spend`), sets `aim` and adds a `gaze`
     frame. Reference plans need no explicit look.
   - **Posture changes** (crouch, kneel, stand, get_up) add an `idle` frame at the new eye height (0 s).
3. **`_advance(seconds)`.** The system `camera.watch` (order 195, per substep) does nothing (O(1)) unless agents share
   the robot's room, or `track` is set, or watch specs exist. It:
   - keeps tracked agents in frame: it picks the pan, and, only while the action is idle (`wait`, `look`, `listen`,
     speech), the body yaw, that maximises tracked agents in frame, round-robin when they cannot all fit;
   - accumulates `in_view_s`, `gap_s` and `max_gap_s`;
   - logs frame-based privacy captures (section 12);
   - evaluates `view_watch` specs every `every_s` (default 60 s, minimum 10 s).
4. **`camera.end_step(env)`, after `_advance` and before `_update_milestones` / `evaluate`.**
   - Fallback frame: if the robot moved without motion frames, add one at the final pose facing along the
     displacement. If no frame exists, add an `idle` frame at the current pose.
   - Compute the step view (11.1), cached in `env._camera_view` with the key `(steps, time_s, mutation_count)`.
   - Write `camera_log`:
     - `seen_cells` OR the floor points (cell centre, z = 0.02) that are in frame in any frame, in LOS, within 8.7 m
       and at light >= 0.12 (C17). The robot's own cell counts as seen. `coverage` is updated for the touched rooms.
     - `looked_in`, `peeked`, `sightings` and `captures`.
   - Run `run_hooks("camera_capture", w, info)` for each new capture.
   - If realistic, call `env.realism.note_view(view)` (tracked identity, outside world state; C5).
5. **`observe()`** is pure. It uses the cached step view when its key matches, and otherwise recomputes it (no
   writes). See 10.6 and section 11.
6. **Visual events.** `camera.visual_event(w, kind, *, source=None, room=None, at=None, z=None, size_m=0.3,
   visual=None, emit=True, **data)`:
   - When `emit` is set, it calls `w.emit(kind, **data)` exactly as before (C13).
   - In geometric mode it tests the point against the current head pose (during `_advance` that is the final pose of
     the action) with the acuity rules (size `size_m`; sure detect needed).
   - Seen events go to `robot.view.events` and to `camera_log.events` with `seen: true`. Unseen events go to the log
     with `seen: false`. Events behind the robot are not seen; domains' audio channels may still report them.

## 9. Actions and visibility semantics (core.py owns the actions; names checked against the 423 registered)

### 9.1 New and extended actions

| action | params | rules (geometric mode) | duration |
|---|---|---|---|
| `face` (new) | `target` ?str (handle, person, pet, `[c, r]`, north, east, south, west, left, right, back), `deg` ?number | Turns the body. Refused if fallen, actuator fault, `standing_on` or stopped. Pan resets to 0. Adds one frame. Allowed with the camera off | abs(dyaw)/90 + 0.3 s |
| `look` (extended) | `target` ?str (around, forward, up, down, left, right, behind, handle, cell), `pan` ?number, `tilt` ?number, `rows` int 1-2 (default 1), `track` ?bool | See the list below. Refused with the camera off ("Cameras are off", perception) | forward 1.0 s; around 5.4 s (rows=2: 8.3 s); aim: pan/tilt time + 0.4 s (+ body turn) |
| `peek` (new) | `target` entity, `where` under, behind, inside, top or closely | Wrist camera; see the list below | 6 s; +2 under/behind; +1 lift |

`look` targets:

- **forward** (or none): 1 frame at pan 0, tilt -10.
- **around:** frames at camera yaw offsets 0, 60, ..., 300 at tilt -10. Pan +-150 plus half the FOV covers 369
  degrees without a body turn. `rows=2` adds the same 6 frames at -45.
- **up / down:** tilt +20 / -45. **left / right:** pan +-90. **behind:** body turn 180 plus 1 frame.
- **handle or cell:** aims at it. The target must be in this room and L0-visible; it need not be in LOS. A pan beyond
  150 degrees turns the body.
- **track=true** with agents named or in view: sets `view.track`. `track=false` clears it.

`peek` rules:

- **Common.** The target must be L0-visible and reachable (`geometry.reach_problem` is None) or held. The camera must
  be on.
- **under:** the robot crouches or kneels and `clear_below_m >= 0.06`. Alternatively the support is liftable
  (portable, mass <= 5 kg): a lift-and-look, +1 s (C9).
- **behind:** `behind_gap_m >= 0.05`, or a free floor cell on the back side within reach, or a liftable support.
- **inside:** the container is open, transparent or open shelving.
- **top:** top height <= `robot_reach_m` + 0.2 (a step stool helps).
- **closely:** a held item shows all faces; otherwise the faces toward the robot.

`peek` effects:

- **revealed.** Sets `revealed` on the under/behind children whose samples the wrist frame sees, within
  `HW.wrist.deep_m` = 1.0 m of the reachable edge. Deeper items stay hidden.
- **Logs.** Writes `peeked`. `inside` also writes `looked_in`.
- **Observation.** The wrist detections appear in the step's observation (`via: "wrist"`).

Refusals use generic wording: "No gap to look under it", "Cannot see behind it from here", "Too high to look on top",
"Cameras are off".

### 9.2 Changed rules of core actions

- **`inspect`.** Requires `camera.inspect_check`: head px_eff >= 160 with LOS and L_at >= 0.2, or wrist access
  (+2 s).
  - `where=under/behind/inside` apply the peek rules and effects first (+2 s).
  - `where=closely` on a held item sees all faces (+3 s).
  - The room-mode rule "fixtures >= 1.0 m from anywhere in the room" becomes the head acuity rule (a 1 m fixture is
    inspect-capable to 5.8 m when lit and clean).
- **`read`.** Requires `camera.read_check`, the first of these that passes:
  - head: char px >= 10, face seen, L_at >= 0.25, no specular glare, camera_clean >= 0.4;
  - wrist (+2 s): within reach, the face not down or against a solid, L_at >= 0.25 (the LED counts), camera_clean
    >= 0.4. For non-held glossy items, glare is tested for a wrist view 20 degrees off the face normal from the
    robot's side. Holding the item removes glare (free angle, +1 s).

  `read` returns, and grants (`ctx.grant(eid, "read", fields)`), only fields whose face was seen. A large-text field
  (display, sign, screen) can be read from afar by the head camera even beyond reach.
- **Mic.** `ask` and `listen` require `camera.mic_active` ("Microphone is off", capability). The observation's
  `messages` are empty while the mic is off.
- **Room mode.** `look` ignores the new params ("You look around", 1 s). `face` and `peek` refuse with "Camera control
  needs camera: geometric" (category capability). `env.action_names()` hides `camera.GEOMETRIC_ONLY = {"face",
  "peek"}` in room mode, so room-mode agents are not offered them; `actions_fingerprint()` still covers them.

### 9.3 Action-level visibility: `camera.can_see(state, eid)`

The rule is deterministic and needs no seed:

```
can_see = L0_visible(eid)
          and ( held(eid)
                or camera_active and ( head_detectable(eid) or wrist_access(eid) )
                or (not camera_active and map_fixture(eid)) )
```

- **`head_detectable`:** LOS from the current eye at any pan/tilt/body yaw (the full viewshed), px_eff >= 10 with
  k_motion = 1, and either L_at >= 0.05 or the IR shape rule.
- **`wrist_access`:** reachable, and the wrist geometry for its relation passes (under: crouching/kneeling and a gap,
  or a liftable support; behind: a gap or the back side reachable; in: open/transparent; on top: <= reach + 0.2).
  The LED guarantees light.
- **`map_fixture`:** non-portable furniture, fixtures and doors in the robot's room (navigation by the stored map
  with the camera off).

`Perception.visible` (and therefore `Ctx.obj`, `ctx.visible`, `core.visible_or_fail` and the 51 domain call sites)
returns `can_see` in geometric mode. `Perception(None).visible` (core `_visible`) detects the mode from the state.

## 10. APIs

### 10.1 `kernel/sight.py` (pure; WP-1)

```
grid_deg(compass_deg) -> float
view_props(state, eid) -> dict
entity_box(state, eid) -> Box(rid, x0, y0, x1, y1, z0, z1, yaw) | None
sample_points(state, eid, eye=None) -> [(x, y, z)]
front_deg(state, eid) -> float
occ_sig(state, rid, world=None) -> str
room_geom(state, rid, world=None) -> RoomGeom
viewshed(geom, eye, wedge=None) -> Viewshed                       # wedge = (yaw, half_deg) or None
point_transmission(state, geom, vs, eye, p, rel=None) -> float
visible_fraction(state, eid, eye, geom=None, vs=None) -> (fraction, best_point)   # heading-independent LOS
in_frame(viewer, p) -> bool;  bearing_elev(viewer, p) -> (rel_bearing_deg, elev_deg)
sees_entity(state, viewer, eid) -> float                          # LOS x FOV x range, visible fraction
view_cells(state, viewer, z=0.02) -> set
los(state, rid, p0, p1, ignore=()) -> float                       # transmission
light_at(state, rid, p, beams=()) -> float
bright_sources(state, rid) -> list
glare(state, viewer, p) -> {"sun": bool, "sun_cone": bool, "backlit": bool}
specular(state, viewer, eid, face=None) -> bool
faces_seen(state, viewer, eid) -> [names]
carried_occluder(state) -> dict | None
lidar_ring(state, eye, facing_deg) -> [float | None] x 24
person_eye(state, pid) -> (x, y, z);  agent_top(state, aid) -> float
person_viewer(state, pid, attention=False) -> Viewer
device_viewer(state, eid) -> Viewer
best_viewpoints(state, rid, eid, k=3, eye_z=1.35) -> [cell]       # free cells by path cost from which eid is visible
aperture_viewer(state, aperture_id, outside_m=3.0, eye_z=1.6) -> Viewer   # phase 3
doorway_views(state, eye) -> [(rid_b, transform, wedge)]                  # phase 3
CATEGORY_DEFAULTS, DARK_COLORS, PERSON_EYE, PET_HEIGHT, EPS
```

### 10.2 `kernel/camera.py` (WP-2)

```
HW; GEOMETRIC_ONLY
mode(state) -> "room" | "geometric"
install(state, overrides=None); strip(state)               # engine.reset, before episode_start hooks and the baseline
eye(state) -> (rid, x, y, z);  head_viewer(state, frame=None);  wrist_viewer(state, eid, where)
camera_active(state) -> bool;  mic_active(state) -> bool;  set_sensors(w, camera=None, mic=None, policy=None)
begin_step(w);  add_frame(w, kind, yaw=None, tilt=None, cam="head", speed=None)
note_motion(w, room, path, speed, end_face=None);  gaze_to(ctx, target) -> seconds
can_see(state, eid) -> bool;  wrist_access(state, eid, where=None) -> (ok, reason)
in_frame(state, eid) -> bool                               # in a frame of this step at the current eye (passive channels)
acuity(env_or_state, eid) -> {"level", "px", "view"}       # ViewInfo (10.6) or None
step_view(env, fresh=False) -> StepView                    # cached; pure
end_step(env)                                              # logs + realism.note_view
inspect_check(state, eid) -> (ok, reason, via);  read_check(state, eid, fields) -> (ok, reason, via, readable_fields)
visual_event(w, kind, *, source=None, room=None, at=None, z=None, size_m=0.3, visual=None, emit=True, **data)
floor_visible_ahead(state, d_m) -> bool;  seen_floor(state) -> set of (rid, cell)   # current step
specular(state, eid) -> bool;  device_frame(state, eid) -> [ids]                    # doorbell/webcam feed
bearing_to(state, xy) -> deg                                                        # for domain audio channels
```

`camera.py` registers `@after` hooks on `move`, `approach`, `walk_to`, `crouch`, `kneel`, `stand` and `get_up`, and
`@system("camera.watch", order=195)`. Each is an O(1) no-op in room mode.

### 10.3 Ctx helpers (actions.py; WP-2)

- `ctx.camera_mode`
- `ctx.can_see(eid)`
- `ctx.in_frame(eid)`
- `ctx.gaze_to(target)` (charges time)
- `ctx.acuity(eid)`
- `ctx.read_check(eid, fields)`
- `ctx.inspect_check(eid)`

`Ctx.obj` uses `can_see` and auto-gaze in geometric mode. Refusals stay `kernel_access`.

### 10.4 Hooks

| hook | signature | provider |
|---|---|---|
| `view_occluders` | `fn(state, rid) -> [(cells, z_lo, z_hi, porosity, source)]` | safety (smoke), outdoor_plants (grass, hedges), textiles (racks) |
| `light_at` | `fn(state, rid, x, y, z) -> float` (additive) | electrical (lamp pools), climate (sun patches) |
| `bright_sources` | `fn(state, rid) -> [dict]` | climate, electrical |
| `sun_beam` | `fn(state, rid, x, y, z) -> (ux, uy, uz) | None` | climate |
| `private_activity` | `fn(state, pid, person) -> bool | None` | people. Kernel fallback: activity in ("bathing", "showering", "toilet", "dressing", "undressing", "changing", "intimate") |
| `camera_capture` | `fn(w, info) -> None` | people (converts captures to `privacy_violations` by consent) |

### 10.5 Setup ops, spec fields, CLI (WP-3)

Setup ops (geometric specs only; SpecError otherwise):

| op | form | writes |
|---|---|---|
| `camera` | `{"op": "camera", "face": deg or target, "pan", "tilt", "camera_on", "mic_on", "privacy_policy", "track": [ids], "hw": {...}}` | config `robot.facing_deg` / `robot.view` partial and sensor fields; install completes them |
| `view` | `{"op": "view", "target": id, "props": {...}}` | merges into `record["view"]` |
| `track_view` | `{"op": "track_view", "ids": [...]}` | `config["camera_log"]["track_ids"]` |
| `view_watch` | `{"op": "view_watch", "name": n, "viewer": {...}, "targets": [ids or "robot"], "every_s": 60}` | `config["camera_log"]["watch_specs"][name]` |

- **`view_watch` viewer forms:** `{"aperture": id, "outside_m", "eye_z"}` (phase 3), `{"entity": device_id}`,
  `{"person": pid, "attention": bool}`, or `{"room": rid, "cell": [c, r], "z", "yaw", "hfov", "range_m"}`.
- **`spawn ... inside`** gains `"row": "front" | "back"`, which computes `inner_spos`.

Spec field `"camera": "room" | "geometric"` is added to `spec.ALLOWED`. Spec sensors are unchanged; geometric mode
always adds the `camera` and `lidar` observation blocks.

CLI: `--camera room|geometric` for `python -m house_sim --check/--verify/play`, `house_sim.eval.run`,
`tools/perception_audit.py`, `tools/fuzz.py` and `tools/bench_systems.py`.

Persistence: `save()`, `save_replay()` and the eval logs record `"camera"`. `load()` and `replay()` pass it to
`HouseEnv(camera=...)`.

### 10.6 Observation fields and the perception-to-realism contract (WP-2 produces, WP-4 consumes)

`ViewInfo`, the per entity, person or pet result of the step view:

```
{"level": "shape"|"detect"|"recognize"|"identify", "detected": bool, "px": float, "range_m": float,
 "bearing_deg": float, "elev_deg": float, "visible": 0.25|0.5|0.75|1.0, "light": float,
 "glare": "none"|"frame"|"sun"|"backlit", "specular": bool, "faces": [names], "via": "head"|"wrist"|"depth"|"mirror:<id>",
 "frame": index, "frame_yaw": deg}
```

`perception.observe(env, records=False)` in geometric mode returns `visible_entities[eid] = {"_view": ViewInfo}`, or
`{"label": "dark shape", "_view": ...}` for IR shapes, only for entities with `detected` true.
`observe(records=True)` (oracle) returns the redacted exact records plus an exact `"view"` dict. Then:

```
visible_entities[h]["view"] = {"bearing_deg": -15, "elev_deg": -20, "dist": "near", "range_m": 1.4, "px": 120,
                                "occluded": 0.25, "via": "head", "light": "lit"}
people[pid]["view"], pets[pid]["view"]     # same shape; people and pets out of frame are omitted
obs["camera"] = {"on": true, "mic_on": true,
                 "pose": {"cell": [3, 4], "facing_deg": 90, "facing": "south", "pan_deg": 0, "tilt_deg": -10, "height_m": 1.35},
                 "frames": 6, "lens": "clean"|"smudged"|"dirty", "glare": "none"|"sun"|"backlit", "light": "dim",
                 "coverage": 0.62, "tracking": [ids], "events": [{"what": "person fell", "id": "pid_1", "bearing_deg": 30, "dist": "mid"}]}
obs["lidar"] = {"height_m": 0.25, "sector_deg": 15, "ranges_m": [2.25, 2.5, null, ...]}
obs["robot"]["facing_deg"]                 # present only in geometric mode (the key list skips absent keys)
obs["grid"]["hazards"]                     # only hazards on cells seen this step, or with a detected source
```

- **dist buckets:** within reach <= 0.8, near <= 1.5, mid <= 3, far <= 6, very far.
- **Oracle:** exact values (0.1 degree, 0.01 m).
- **Realistic** (section 11):
  - bearing and elevation to 5 degrees;
  - `range_m` only within the depth range [0.2, 6] m with px >= 24, as a fixed draw with sigma = 0.01 + 0.005 d^2,
    rounded to 0.1 m;
  - px to 2 significant figures;
  - occluded in quarters;
  - light as a bucket.
- **`camera_log`** is world state and never appears in observations; only the current room's `coverage` appears in
  `obs.camera`.

## 11. Interaction with realistic perception (WP-4)

### 11.1 The tier x geometry contract

| level (best this step) | what realism renders | knowledge |
|---|---|---|
| none (not in frame, no LOS, px_eff < 10, or the band missed) | absent; no new handle. Remembered knowledge is kept | lapses only if moved unseen (today's rule) |
| shape (IR) | `{"label": "dark shape", "kind": "unknown", "size_class", "location", "pos", "view"}` | none |
| detect | `{"label": "object", "kind": "unknown", "size_class", "shape", "location", "relation", "pos"/"spos", "view"}` + `color` when L_at >= 0.15 and not backlit | ident >= 1 |
| recognize | `label` = the `category` word (a look-alike shows its group descriptor; a novel thing shows "unfamiliar object") + large-scale glance states (`RECOGNIZE_FIELDS`: open, lit, on_fire, smoking, steaming, overflowing, leaking, flooded, tipped_over, toppled, fallen, shattered, spilled, covered, posture) + a fill bucket if the container px >= 48 | ident >= 2 |
| identify | today's full glance rendering | ident = 3: identity persists while the knowledge signature is unchanged (tracking) |
| inspect / read / measure | unchanged tiers, granted by actions, gated by `inspect_check` / `read_check` / face gating | grants |

### 11.2 Rules

- **Handles.** A handle is issued at detect. An agent can approach "object obj_x" to identify it.
- **Look-alike field-set discipline.** At detect and recognize, the field set depends only on the level and on
  conditions, never on the type. Unresolved look-alikes intersect the level fields with `LOOKALIKE_FIELDS`.
- **Tracked identity.** At identify, tracked identity raises identity fields (label, kind, category) only, not
  condition fields. `Knowledge` entries gain `"ident": [level, signature]`, persisted by `dump`/`load`.
  `Realism.note_view(view)` updates them once per step.
- **Per-field acuity.** A type perception block may declare `"acuity": {field: "recognize"|"identify"|"inspect"}` for
  visual states. Examples: a crack 1 mm wide is inspect; heavy mold is identify. Defaults:
  - glance fields need identify, except `RECOGNIZE_FIELDS`;
  - inspect-tier fields need an inspect grant, as today.
- **Context.** In geometric mode `context()` returns per-target values:
  - `light = L_at`;
  - `dist = range_m / 0.5` (kept for compatibility);
  - `m = m_global + 0.6 x max(0, log2(128 / px_eff)) + 0.8 x occluded + (1.5 if L_at < 0.3 else 0.5 if L_at < 0.45 else 0) + glare (frame 1.0, backlit 1.5)`.

  `m_global` keeps the lens, fast-motion and steam/smoke terms of today's `room_context`, but not the cell-distance
  or hour-based glare terms. Value noise stays a fixed draw per (eid, field, level); conditions only coarsen the step.
  `test_estimates_do_not_average_out_across_viewpoints` must keep passing.
- **Detection band** (replaces `missed()` in geometric mode).
  - Probability: `p = (px_eff - 10) / 14 - 0.05 x max(0, clutter - 3)`, clamped to [0, 1], where clutter = siblings
    on the same support.
  - Draw: `_FastRng(seed, "detect", eid, eye cell, 45-degree frame-yaw sector, cam)`.
  - The result is stable for a given viewpoint. Stepping or turning re-rolls it, as stepping does today.
  - Knowledge >= inspect, and the parent-contents rule, still force detection as today.
- **Face-gated fields** are dropped unless a frame of the step saw the face or knowledge already holds the field.
- **Mirrors.** The phantom doorway appears only while the mirror is in frame.
- **Hazards.** `grid.hazards` lists only hazards on cells seen this step, or with a detected source.
- **The path planner** stays privileged; perception-limited planning is a later navigation option.
- **Messages.** Refusal wording is generic ("You cannot see it from here", "Too small to read from here", "Too dark",
  "Glare on it; change the angle"), never naming hidden objects. `view` carries no ids except translated mirror
  handles. Channel values from the `camera` and `lidar` blocks go through `out_obj` like other channels.

## 12. Privacy

- **Effective sensors.** `camera_active = camera_on and not privacy_mode and not policy_off(room, camera)`, and the
  same for `mic_active`.
- **Policy.** It is evaluated at `begin_step` and after motion for the robot's current room. `"when": "occupied"`
  means a person is in that room. In phase 3, frames that view into a policy room through a doorway are masked: no
  detections and no captures from that room.
- **Camera off:**
  - no detections except held items;
  - no people or pets except through audio channels;
  - lidar stays;
  - look, peek, inspect and read are refused (consistent with navigation's `_no_camera`);
  - map fixtures stay targetable (9.3).
- **Mic off:** `messages` are empty, and `ask`/`listen` are refused.
- **Captures are frame-based.** A camera capture happens when a person whose `private_activity` is true is in frame
  (a head or torso sample visible) at L_at >= 0.12 with the camera active. Direct views count in phase 2; doorway and
  window views count in phase 3. A mic capture happens when the mic is active and such a person is in the same room
  (phase 3: within 4 m through an open doorway). There is one capture per (person, activity episode). The kernel only
  logs; the people domain converts captures into `privacy_violations` according to consent, through
  `camera_capture`. In geometric mode the people and navigation entry-based "sensor_capture" bumps are skipped.
  Entry intrusions stay.
- **No passive reading.** Passive frames never render read-tier text. Documents and screens are read only by an
  explicit `read` (judged by the existing permission rules).
- **Seen from outside** (a lit room seen from the street, fobs through the door glass): `view_watch` with an aperture
  viewer (phase 3).

## 13. Determinism, replay, persistence

- **State.** Outcome-relevant camera state is world state: `robot.facing_deg`, `robot.view`, `camera_on`, `mic_on`,
  `privacy_policy` and `camera_log`. It is written only through `w.robot_m()` / `w.sec("camera_log")`, so strict
  mode, rollback, save/load and replays reproduce it.
- **Knowledge.** Tracked identity lives in realism knowledge, which `dump`/`load` persist. It is updated once per
  step from the step view, so it is independent of how many times `observe()` runs.
- **Caches** are in-process and keyed by content (`occ_sig`, eye, record digests) or by `World.mutation_count`. They
  never change results.
- **Randomness** is only `_FastRng` (blake2b) over (seed, kind, eid, quantised pose). There is no global RNG, no wall
  clock, and iteration is over sorted lists.
- **Floats.** Values are rounded per section 3, and comparisons use EPS. Determinism is bit-exact on one platform and
  across PYTHONHASHSEED values. Across platforms, decisions at exact boundaries are protected by EPS and stored values
  are rounded to 3 decimals.
- **Room mode** performs zero camera writes and adds no keys. State digests and observation digests equal the
  pre-change values (the golden test, 17.13).

## 14. Making success and failure observable (spec patterns)

- **Survey a room or house.** Success: `camera_log.coverage.<room> >= 0.9` for each room, plus reports matching the
  seeded anomalies. Wrong plan: one look from the doorway of a room made L-shaped by tall shelving (the generator
  makes rectangular rooms; L shapes come from partitions and shelving) gives coverage < 0.9 and misses the leak.
- **Search with an honest absence claim.** A milestone checks that every candidate is in `camera_log.looked_in` or
  `camera_log.peeked`, ordered before `reports.<target>.found == false`. Wrong plan: claims absence after scanning
  the floor only.
- **Hoses behind appliances.** Success: `camera_log.peeked["washer_1:behind"]` exists, the defect report is correct,
  and `side_effects.no_moves_except` excludes the appliance. Wrong plans: drags the washer, or reports without
  peeking.
- **Back of a shelf, expiry survey.** Items are staged with `row: "back"`. Reports must include them. Wrong plan:
  front row only.
- **Supervision.** Success: `camera_log.max_gap_s.<child> <= 30` and `in_view_s >= 0.95 x T`. Wrong plan: leaves the
  room, or does not track.
- **Privacy.** Success: global `privacy_violations == 0`, or `count(camera_log.captures) == 0`. Wrong plans: enters
  an occupied bathroom with the camera on, or stares through the open door (phase 3).
- **Video call.** Success: `camera_log.watch.call.robot_in_view_s == 0` (laptop device viewer).
- **Labels and keys.** Success: labels face out (`faces_seen`, `label_facing`). For keys:
  `camera_log.watch.entry.ever_clear` with an aperture on the front-door glass (phase 3).
- **Low-light navigation.** The collision and fall flags decide. A low-contrast cable is undetectable at normal speed
  in dim light unless the headlamp beam or careful speed raises px_eff.
- **Carried load.** The floor ahead is hidden (5.6). A toy or pet ahead is not seen and navigation contacts follow.

## 15. Compatibility and migration

| area | rule |
|---|---|
| 242 verified specs | unchanged: the default camera is room. Gate: `--check` on all 32 files (oracle) and the 16 realistic files (realistic) after each phase. `reports/verification.jsonl` evidence is regenerated because the code fingerprint changes |
| 50 realistic specs | keep room mode until phase 4. Then they are run with `--camera geometric`, and their plans are fixed (look around, approach, peek) or the spec is pinned to `"camera": "room"` with a note. Geometric becomes the default for realistic mode only after all of them pass or are pinned |
| new camera-dependent catalog specs | must declare `"camera": "geometric"`, and need at least one wrong plan that fails for a camera reason (did not look, wrong viewpoint, dark, glare, missed the back row) |
| action fingerprint | changes once (face, peek, look params, privacy params). Saved states, replays (`demos/`, `results/`) and `dataset.actions_fingerprint` are regenerated; the viewer warns for old episodes as it does today |
| state and observation digests in room mode | must equal the pre-change values. The golden data is recorded at the commit where WP-2 starts, by WP-2's first commit, before any edit. It is re-recorded only with a note when another layer intentionally changes room-mode behaviour |
| domain tests | must pass unchanged. Domains migrate in phase 3, keeping room-mode outputs identical; private LOS copies are deleted only after parity tests |
| eval harness | the spec's camera mode by default, with `--camera` to override. Episode records and the leaderboard carry `camera`. The prompt mentions `face`, `look` and `peek` and the `camera` observation block when present. The test split stays realistic |
| SearchAgent (`house_sim/agent.py`) | in geometric mode: `look around` on arriving in a room (rows=2 when the target is small); approach "object" detections whose size class fits; kneel and peek under beds and sofas when a room is fully covered without a find; restore what it lifted |
| reference skills | `reveal` kneels and peeks (or lifts light supports) in geometric mode. `approach` routes through `sight.best_viewpoints` when the target is not visible. `fetch` crouches for low items (exists) |
| viewer | from privileged state: facing arrow, head FOV wedge per step, a seen-cells overlay from `camera_log`, and the frame count. The perceived view shows only what was perceived |
| tools | `perception_audit --camera geometric` adds leak checks: no `view` for undetected things, bearings only for detections, no ids through events, no `camera_log` in observations. `fuzz --camera geometric` checks determinism. `bench_systems` covers `camera.watch` |
| cross-layer | TIER1_EMBODIMENT and TIER1_PHYSICS may also edit engine.py, actions.py, core.py and realism.py. The orchestrator serialises merges of those shared files (section 21) |

## 16. Performance budget

| item | budget | basis |
|---|---|---|
| viewshed per new eye | <= 0.3 ms indoors, <= 1.5 ms outdoors (full); wedge about 20% of that | prototype: 0.13 / 1.37 ms |
| geometric observe overhead vs realistic room mode, seed 7 | target 3 ms; test bound 10 ms (a new test, same best-of-5 shape) | measured realistic overhead is already 3.2-5 ms over oracle |
| look-around observe (6 frames, same eye) | <= 4 ms extra | one viewshed and 6 FOV passes over the candidates |
| `end_step` | <= 3 ms indoors per step (it computes the step view that observe then reuses); motion frames <= 0.5 ms extra | wedge viewsheds for motion frames |
| `camera.watch` per substep | <= 0.05 ms, skipped when no agents, track or watch specs exist | a 7-day wait is about 20k substeps |
| lidar | <= 0.4 ms | 24 rays |
| `can_see` per call (memo warm) | <= 0.05 ms | sample tests against a cached viewshed |
| caches | <= 64 rooms x 32 eyes x about 1.5 k floats | |

## 17. Test plan

New test files: `tests/test_sight.py` (WP-1), `tests/test_camera.py` and `tests/test_camera_golden.py` (WP-2),
`tests/test_camera_actions.py` (WP-3), `tests/test_camera_realism.py` (WP-4).

1. **Viewshed geometry:**
   - a sofa hides a floor toy from 1.35 m but not a 0.9 m lamp;
   - under-bed visibility kneeling at d_near >= 0.75 m, not standing at 1 m;
   - a wardrobe top hides a 0.1 m box at 0.5 m but not at 3 m;
   - items under a table are visible;
   - an open cabinet's interior is visible only from the front;
   - a taller front item occludes the back row;
   - a top-open box hides a short item at the bottom;
   - a ray under a table then over a low box is visible (C20).
2. **FOV and frames:** an entity at bearing 50 degrees is absent from a forward frame and present after
   `look around`; a high shelf is out of frame at tilt -10 until `look up`; the floor within 0.6-3 m appears with
   `rows=2`.
3. **Resolution:**
   - a 4 cm block is sure-detected at 1 m and not at 3 m;
   - the band draw is stable for repeated looks from one pose and re-rolls after a step or a turn;
   - detect, recognize and identify render "object", the category word and the label.
4. **Read:**
   - a 4 mm label: head refuses at 0.6 m, wrist succeeds within reach;
   - a 3 cm sign is readable at 2.5 m;
   - a label facing the wall is unreadable until rotated or flipped;
   - specular glare refuses and a new angle (or holding the item) succeeds.
5. **Light:** a dark room plus a flashlight lights only the cone to 3 m; the headlamp lights to 1.5 m; IR shapes
   >= 0.4 m within 4 m in pitch dark; a fridge interior is lit when open.
6. **Glare:** sun in frame (with a stub `sun_beam` hook) hides text and colour, and turning away restores them; a
   silhouette is backlit against a bright window.
7. **Lens:** camera_clean 0.3 refuses read and drops small detections; `clean_camera` restores them.
8. **Privacy:**
   - camera off gives no detections but a lidar ring;
   - a capture when a private-activity person is in frame; none when facing away;
   - a mic capture;
   - the policy auto-switches on entry;
   - phase 3: a capture through an open bathroom door from the hallway.
9. **Carried loads:** the 3.7 m basket number; `floor_visible_ahead`.
10. **Events and supervision:** a visual event behind the robot is not seen and one in front is; `in_view_s` and
    `max_gap_s` with and without `track`.
11. **Coverage:** an L shape made by tall shelving is < 0.9 from the doorway; a second viewpoint reaches >= 0.9; a
    dark room scanned by IR does not count.
12. **Determinism:** the geometric observe digest is identical across PYTHONHASHSEED 1 and 987 processes; save/load
    and replay are equal; `HOUSE_SIM_STRICT=1` passes; calling `observe()` 0, 1 or 3 times per step gives identical
    later observations (C5).
13. **Room-mode golden:** for seeds 0-3, oracle and realistic, the observation and state digests after reset and
    after a scripted 12-action sequence equal the recorded values. The final state digests of reference plans for 20
    specs (all 242 with `HOUSE_SIM_GOLDEN_ALL=1`) are equal. `look` lasts 1 s. No `camera_log` key; no
    `robot.view` key.
14. **Performance** per section 16.
15. **Leak audit:** `tools/perception_audit.py --camera geometric` is clean.
16. **Agents:** SearchAgent fetch tasks pass in geometric mode; the eval-harness spy and leakage tests run in both
    camera modes.

## 18. Implementation plan

### 18.1 Phases

- **Phase 0, prerequisites.** The current kernel round (realism, perception, engine, actions, core, geometry,
  registry, generator) lands. The orchestrator decides the merge order with the other tier-1 layers.
- **Phase 1, no behaviour change.** WP-1 lands. WP-2 first lands its golden recording, `World.mutation_count`, and
  the engine plumbing with the mode switch (geometric install only, no features).
  - Gate: full unit suite; golden test; `--check` on 32 files (oracle) and 16 files (realistic); `bench_systems`
    within 5%; `layout_audit` and `perception_audit` unchanged.
- **Phase 2, opt-in geometric.** The rest of WP-2, plus WP-3 and WP-4, in parallel.
  - Gate: the camera test files; the performance tests; `perception_audit` and `fuzz` with `--camera geometric`;
    SearchAgent; golden still green.
- **Phase 3, domains and apertures.**
  - The section 19 follow-ups, one domain agent each.
  - Doorway and window apertures and `aperture_viewer` (the WP-1 owner; sight.py only).
  - Doorway captures and policy masking (the WP-2 owner; camera.py only).
  - Gate per domain: room-mode tests unchanged, plus new geometric tests.
- **Phase 4.**
  - Re-verify the 50 realistic specs with `--camera geometric`, and flip the realistic default only when they pass
    or are pinned.
  - Mirrors (optional).
  - Face-recognition gating for visitors (optional; people domain).

### 18.2 Kernel work packages (disjoint file sets)

Merge order: WP-1, then WP-2, then WP-3 and WP-4 in either order. All packages code against the signatures in
section 10, with stubs until their dependency merges.

**WP-1: sight geometry library (kernel agent A).**

- Files: `house_sim/kernel/sight.py` (new), `house_sim/kernel/registry.py` (the type `"view"` block and
  `view_props`), `house_sim/data/types/core.json` (view blocks on core base types: bed, sofa, chair, table, desk,
  storage furniture, bookshelf, wardrobe, appliances present in core, door), `tests/test_sight.py` (new).
- Work items: everything in 10.1 except the apertures; sections 5.1-5.7 and 6; `CATEGORY_DEFAULTS`. Phase 3 adds 5.8.

**WP-2: camera runtime and engine integration (kernel agent B).**

- Files: `house_sim/kernel/camera.py` (new), `house_sim/kernel/engine.py`, `house_sim/kernel/perception.py`,
  `house_sim/kernel/actions.py`, `house_sim/kernel/world.py`, `tests/test_camera.py` (new),
  `tests/test_camera_golden.py` (new), `tests/data/camera_golden.json` (new), `docs/KERNEL_API.md` (a "v3.0 camera"
  section).
- Work items:
  - the golden recording first;
  - `World.mutation_count`;
  - `HouseEnv(camera=)` and reset install/strip;
  - `begin_step` and `end_step` placement;
  - `action_names()` filtering;
  - save/load/replay of the camera mode;
  - camera.py: section 10.2, the watch system, captures, visual events, the motion and posture after-hooks;
  - the geometric branch of perception.py (the `can_see`-based `visible`, the step-view observation, the `camera`
    and `lidar` blocks, people and pets in frame, hazards on seen cells, mic-off messages);
  - the Ctx helpers and auto-gaze in `Ctx.obj`.

**WP-3: camera actions, setup, spec plumbing, skills (kernel agent C).**

- Files: `house_sim/systems/core.py`, `house_sim/tasks/setup.py`, `house_sim/tasks/spec.py`,
  `house_sim/tasks/runner.py`, `house_sim/tasks/verify.py`, `house_sim/tasks/skills.py`, `house_sim/cli.py`,
  `tests/test_camera_actions.py` (new), `docs/ACTION_API.md`, `docs/TASK_SPEC.md`, `docs/AUTHORING_GUIDE.md`.
- Work items:
  - `face`, `peek`, the extended `look`;
  - the geometric rules of `inspect` and `read` (face-gated grants);
  - mic gating of `ask` and `listen`;
  - `approach` ending with the head aimed at the target (through `camera.gaze_to`);
  - setup ops (10.5), `row` placement, `spec.ALLOWED`, camera mode plumbing in the runner, verifier and CLI;
  - the skill updates in section 15;
  - an authoring-guide section on camera specs (section 14 patterns, mandatory camera wrong plans).

**WP-4: realistic rendering, agents, tools, eval, viewer (kernel agent D).**

- Files: `house_sim/kernel/realism.py`, `house_sim/agent.py`, `house_sim/eval/run.py`, `house_sim/eval/agents.py`,
  `house_sim/eval/llm.py`, `house_sim/eval/dataset.py`, `house_sim/eval/viewer.py`,
  `house_sim/eval/viewer_template.html`, `tools/perception_audit.py`, `tools/fuzz.py`, `tools/bench_systems.py`,
  `tests/test_camera_realism.py` (new), `docs/PERCEPTION.md`, `docs/VIEWER.md`, `docs/EVAL_HARNESS.md`.
- Work items:
  - section 11: identity caps, `RECOGNIZE_FIELDS`, per-field `acuity`, per-target context, the detection band, face
    gating, `note_view` and the knowledge `ident`, `view`/`inner_spos` in hidden tiers, mirror-in-frame, and
    rendering of the camera and lidar channels;
  - the SearchAgent and eval harness changes, the viewer overlay, and the tool flags and audits in section 15.

## 19. Domain follow-ups (phase 3; each keeps room-mode outputs identical)

Every domain also:

- adds `"view"` blocks to its furniture and appliance types where the category defaults (4.4) mislead;
- emits visual occurrences through `camera.visual_event` (same `event_log` entries);
- gates visual observer cues on `camera.in_frame` in geometric mode.

| domain | adopt or implement |
|---|---|
| admin_inventory | `text_m`/`faces` on mail, envelopes, documents, packages (`label_facing`), printer displays. `verify_label`, `scan`, `count` and `record_inventory` go through `camera.read_check` / `camera.acuity` (stacked boxes hide faces: unstack or rotate). Doorbell `view.camera` and the feed through `camera.device_frame`. Inventory counts of back rows need `looked_in` plus identify |
| appliances_cooking | Fridge and freezer: front opening with `interior_light`. Oven, microwave and washer-style door windows transparent; dishwasher racks top-opening when open. Glossy stainless and glass cooktops; display `text_m` 0.010. `visual_event` for flare-ups, smoke, boil-over and arcing. Per-field `acuity` for browning and doneness cues. The senses observer is gated by `in_frame` (sounds unchanged) |
| cleaning | The floor-soil observer and the `wet_floor_seen` hook restricted to `camera.seen_floor` in geometric mode. Raking-light streaks, fingerprints and residue through `camera.specular` in the `perceivable_value` hook. Spills as visual events. Cushions and pillows liftable (C9) |
| climate | `bright_sources` (sunlit apertures from `aperture`/`beam_at`, level from DNI; the outdoor sun) and `sun_beam` hooks; `light_at` sun patches; compass to grid through `sight.grid_deg`. Robot screen glare through `sight.specular`. Replace `light_level_fast` with the kernel value (identical). View blocks for fans, radiators and heater grills (porous) |
| electrical | `light_at` lamp pools; `bright_sources` for bulbs, lamps and screens; flashlight `view.beam` {1.8, 15}. Remote IR `line_of_sight` becomes `sight.los` at hand height. Screens glossy with `text_m` 0.03; laptop webcam `view.camera` {78, 4 m}. The `electrical.light` observer only for lamps in frame. Replace the `light_level` copy in harms.py |
| food | Pantry and fridge rows through `inner_spos` / setup `row`; `text_m` for expiration and best-by; glass jars glossy/transparent. `acuity` for freezer burn, mold and pantry pests (inspect or identify). Visual events for boil-over, spills and pests |
| manipulation | `rotate`, `flip`, `set_orientation` and `arrange` update `yaw_deg`/orientation, and therefore faces. `_blocks_view` becomes `sight.carried_occluder`. `world_pose` now gets a real `robot.facing_deg`: check robot-relative placements. Decor and vases get porosity/transparent blocks |
| mechanisms | Sheer coverings `view.porosity` 0.6; glass doors and panels porous; the door leaf occluder comes from the kernel. The `mechanisms.doorways` glimpse is replaced by sight doorway apertures in geometric mode (phase 3). Keypads and touchscreens glossy with `text_m` 0.006 |
| navigation_robot | `set_privacy_mode` gains `camera`/`mic`/`policy` (calls `camera.set_sensors`); `_no_camera` extends to `peek`. `nav.visits` coverage reads `camera_log` in geometric mode; patrol viewpoints through `sight.best_viewpoints`. `robot_headlamp` `view.beam` {0.45, 30}. `camera_frame`, `evaluate_sightlines`, `_floor_in_view`, `view_blocking_items` and `startle` move to sight (person viewer, LOS with eye heights, `carried_occluder`, `floor_visible_ahead`). The entry-based privacy capture is skipped in geometric mode. Rain and outdoor grime lower `camera_clean`. Robot motion verbs produce motion frames (nested core moves or `note_motion`) |
| outdoor_plants | `view_occluders` for tall grass (height from the lawn state, porosity 0.5), hedges (0.3) and open patio umbrellas. Room `fog` for haze. Lawn survey through outdoor coverage. Small parts in grass (occluders plus contrast). Gutters with `faces` "up" are seen only from a raised eye (ladder) |
| people | `sight.person_viewer`/`person_eye` replace `eye_point` (same constants). `startle`, approach-from-front and gaze references use sight eye heights and LOS. `frame_analysis`, `_line_blocked`, `frustum_items` and `private_in_frame` move to device and person viewers. Implement the `private_activity` and `camera_capture` hooks (violations by consent); skip `_entering` sensor_capture in geometric mode. Observers (poses, gestures) gated by `in_frame`. Child hazards (climbing, mouthing small parts, falls) as visual events. Supervision metrics from `camera_log`. Video-call `view_watch`. Phase 4: visitor recognition needs face px >= 64 with the face toward the camera |
| pets | Occluder heights by species and size (`view.height_m` override). Porous crates and cages; transparent glossy aquariums. Visual events for escapes, accidents, counter-climbing and fights. The `pets.cues` observer gated by `in_frame`. Dark coats drive `k_contrast` through `color`. Pet supervision through `track` |
| plumbing_liquids | Under-sink cabinets: front opening with no interior light (needs a beam). Leaks and drips as visual events (sounds unchanged). Water heater `behind_gap_m`; toilet tank top-opening with the lid off. Floods and puddles shown only on seen cells (aligned with cleaning and safety) |
| safety | `view_occluders` for the smoke layer (ceiling down to the smoke height, porosity by density) and steam; room smoke feeds `k_haze`. Visual events for fire start, smoke and pest sightings. Glass slivers glossy (raking light). `outside_view` becomes aperture viewers; `doorbell_camera` becomes `camera.device_frame` (IR at night). "Keys visible from the entry" through a `view_watch` helper |
| textiles_laundry | Washer and dryer drums: front opening with a transparent door window. Drying racks with laundry as porous `view_occluders`. Hampers top-opening with lids. `care_label` `text_m` 0.0025 (read by wrist or held), with an optional face. Stain `acuity` (identify for large stains, inspect for small). The senses observer gated by `in_frame` |
| tools_vehicles | Vehicle interiors and trunks: opening blocks; glass transparent; a rear camera as a device viewer (optional). Tool damage `acuity` (inspect). Porous ladders; top-opening toolboxes. Flat-pack small parts follow the detection ranges |

The generator needs no change: view defaults are type metadata.

## 20. Requirement coverage (entries from the camera tag analysis)

| need | entries | mechanism | phase |
|---|---|---|---|
| Occlusion-aware search, honest absence | 91 | viewshed, relation rules 5.4, peek, `looked_in`/`peeked` | 2 |
| Viewpoint coverage and selection | 29 | frames, look around and rows, coverage, `best_viewpoints`, faces | 2 |
| Lighting (per target, beams, interior lights) | 28 | `light_at`, beams, `interior_light`, lamp and sun hooks | 2 (hooks 3) |
| Directional glare | 18 | sun in frame, backlight, specular | 2 kernel fallback; 3 climate/electrical |
| Privacy sensing | 19 | separate camera/mic, policy, frame captures, apertures | 2 direct; 3 doorway and window |
| Supervision and event witnessing | 21 | watch system, track, `visual_event` | 2 kernel; 3 domain emitters |
| Carried-load occlusion | 10 | `carried_occluder`, `floor_visible_ahead` | 2; 3 nav and manipulation adopt |
| Small and low-contrast detection | 32 | px model, `k_contrast`, detection band, grass occluders | 2; 3 |
| Reading conditions | 10 | `text_m`, faces, specular, lens | 2 |
| Other viewers' sightlines | 13 | person, device and aperture viewers, `view_watch` | 2; 3 apertures |
| Lens degradation | 5 | `k_lens`, rain and grime | 2; 3 |
| Visual state detection | 118 | acuity levels plus per-field `acuity` | 2 kernel; 3 domains |

## 21. Open questions (for the orchestrator or the user)

1. The second candidate design was not delivered to the judge. If it surfaces, compare it only on decisions not
   already verified here.
2. **Cross-layer contract.** If TIER1_EMBODIMENT models body pose, it owns `robot.facing_deg` and posture heights, and
   the camera reads them, with a single writer. Shared files (engine.py, actions.py, core.py, realism.py) need a merge
   order across the tier-1 layers.
3. **Default flip.** Should geometric become the default for all realistic episodes after phase 4 (recommended), or
   stay opt-in? This is a user decision.
4. **Handle identity leak** across unseen swaps: accept and document, or add "handle epochs" (re-handle an entity
   that moved while in no frame) in a later realism round?
5. **Captures without the people domain.** The kernel only logs captures. Should it increment `privacy_violations`
   itself when no `camera_capture` hook is registered (dev runs with HOUSE_SIM_DOMAINS)?
6. Are mirrors (phase 4) worth their cost? Mirrors appear only inside the 18-entry glare group. Check those entries
   before building reflections beyond today's "mirror looks like a doorway" percept.
