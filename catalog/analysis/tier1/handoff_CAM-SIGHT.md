# Handoff CAM-SIGHT (stage A): the geometric sight library

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole; 1, 2.3, 3, 5 in detail), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_CAMERA.md` (whole; sections 4-7, 10.1 and 17 are this package's), the frozen `support` signatures of
TIER1_PHYSICS 5.1 / 7.1 / 14 and the embodiment profile (TIER1_EMBODIMENT 3.1). Handoff files read:
`handoff_TIER1-GATE.md` (the only one at the start); `handoff_BODY-MODEL.md` and `handoff_PHYS-DATA.md` (they appeared
during the work, read before finishing). I also read the in-progress `kernel/phys/support.py` and `kernel/body.py` of the
shared tree to align the bridges (section 5).

## 1. Summary

* `house_sim/kernel/sight.py`: the pure geometry library of the geometric camera (TIER1_CAMERA 4-7, 10.1 minus the
  phase 3 apertures): view properties and category defaults, entity boxes and samples, fronts, the occluder model, the
  interval viewshed, the relation rules, line of sight, viewers and frames, light, glare, faces, text sizes, acuity,
  carried loads, the lidar ring and best viewpoints. Import-inert; nothing calls it yet (zero behaviour change).
* `registry.py` accepts a type `"view"` block, keeps it out of `types[name]` and records, and exposes `view_props(name)`;
  `core.json` gains eight view blocks where the category defaults mislead; `tests/test_sight.py` (59 tests).
* Worked numbers are recomputed for the body profile's 1.50 m eye (conflict 4) and pinned by tests.

## 2. Files

Created: `house_sim/kernel/sight.py`, `tests/test_sight.py`, this handoff, the gate record
`catalog/analysis/tier1/gate/CAM-SIGHT/`.
Edited: `house_sim/kernel/registry.py` (the view block, `view_props`, module docstring; the working copy was CRLF and is
now LF like the index), `house_sim/data/types/core.json` (view blocks only: crib, bench, workbench, shoe_rack,
shelf_unit, gate, lamp, flashlight). All on the CAM-SIGHT list of TIER1_INTEGRATION 2.3.

## 3. APIs

`house_sim/kernel/sight.py` (all pure: they read a state dict and never write; an eye is `(rid, x, y, z[, ignore])`, an
`Eye` or a `Viewer`, and its `ignore` ids never occlude for it; `"robot" in ignore` marks the robot's own cameras, for
which held things are always visible). The 10.1 list, with **DEVIATION** where the signature differs:

```
grid_deg(compass_deg) -> float
view_props(state, eid) -> dict                                   # memoized; do not mutate
entity_box(state, eid, cmap=None) -> Box(rid, x0, y0, x1, y1, z0, z1, yaw) | None       DEVIATION: cmap (optional)
sample_points(state, eid, eye=None, cmap=None) -> [(x, y, z)]                         DEVIATION: cmap (optional)
front_deg(state, eid) -> float
occ_sig(state, rid, world=None) -> str
room_geom(state, rid, world=None) -> RoomGeom
viewshed(geom, eye, wedge=None) -> Viewshed                      # wedge = (yaw_deg, half_deg); range = eye.range_m or 12 m
point_transmission(state, geom, vs, eye, p, rel=None, cmap=None) -> float
                     DEVIATION: rel is None (a free point) or the id of the entity / person / pet p belongs to
visible_fraction(state, eid, eye, geom=None, vs=None, cmap=None) -> (fraction, best_point)   DEVIATION: cmap
in_frame(viewer, p) -> bool;  bearing_elev(viewer, p) -> (rel_bearing_deg, elev_deg)
sees_entity(state, viewer, eid, geom=None, cmap=None) -> float                         DEVIATION: geom, cmap
view_cells(state, viewer, z=0.02, geom=None) -> set of (c, r)                          DEVIATION: geom
los(state, rid, p0, p1, ignore=(), geom=None) -> float                                 DEVIATION: geom
light_at(state, rid, p, beams=(), ambient=None, geom=None) -> float                    DEVIATION: ambient, geom
bright_sources(state, rid) -> [{"x", "y", "z", "radius_m", "level", "kind", "id"}]
glare(state, viewer, p, radius_m=0.0, light=None) -> {"sun", "sun_cone", "backlit"}    DEVIATION: radius_m, light
specular(state, viewer, eid, face=None) -> bool
faces_seen(state, viewer, eid, cmap=None) -> [names]                                   DEVIATION: cmap
carried_occluder(state) -> {"items", "z_bottom", "z_top", "x_front", "half_w", "half_angle_deg", "floor_hidden_m"} | None
lidar_ring(state, eye, facing_deg, geom=None) -> [float | None] x 24                   DEVIATION: geom; eye has the room
person_eye(state, pid) -> (x, y, z);  agent_top(state, aid) -> float                   # aid: person, pet or "robot"
person_viewer(state, pid, attention=False) -> Viewer
device_viewer(state, eid) -> Viewer | None
best_viewpoints(state, rid, eid, k=3, eye_z=None) -> [(c, r)]       DEVIATION: eye_z default = the standing eye height
CATEGORY_DEFAULTS, DARK_COLORS, PERSON_EYE, PET_HEIGHT, EPS
```

Additions (sections 4-7 data and the pure math the camera runtime composes; each tested):

```
Box, Viewer(rid x y z yaw tilt hfov vfov range_m focal_px kind ignore), Eye(rid x y z ignore=()), Beam, RoomGeom, Viewshed
HW (the robot's optics: TIER1_CAMERA 4.2 minus the head heights; camera.HW should be this table), focal_px(res_w, hfov)
PX, LIGHT, K_MOTION, TEXT_M, LABEL_FACING, LABEL_GATED_FIELDS, CATEGORY_NAMES, VIEW_DEFAULTS, EYE_HEIGHT_M, CARRY
wrap_deg(deg); quarter(fraction); eye_height_m(state, posture=None); children_map(state) (a snapshot, see below)
make_beam(origin, yaw_deg, tilt_deg, power, half_deg); beam_light(beam, p); robot_beams(state, eye, yaw_deg, tilt_deg)
text_m(state, eid, field); read_range_m(text_height_m, focal=None); gated_face(state, eid, field)
px_on_target(size_m, distance_m, focal); k_lens(clean); k_light(light); k_motion(speed=None); k_haze(room, d);
k_contrast(target_color, back_color, light); background_color(state, eid); effective_px(px, visible, lens, light,
motion, haze, contrast); acuity_level(px_eff, light, sun_cone=False, backlit=False, detect_px=None);
detect_probability(px_eff, clutter=0); inspect_capable(px_eff, light); ir_shape(size_m, d, rel_bearing_deg, light);
readable(text_height_m, d, focal, light, camera_clean=1.0, motion=1.0)
carried_hides(occ, eye_z, rel_bearing_deg, distance_m, z)          # for camera.floor_visible_ahead
```

`children_map(state)` returns `{parent: [children]}` that also memoizes placements and container layouts for the state
it was built from; pass it as `cmap` to every call of one step view (build a new one after any change; a plain dict or
`None` works too, just slower). With it, `visible_fraction` costs 7-15 us per entity (section 6).

`house_sim/kernel/registry.py`: a type may carry `"view": {...}` (validated as an object); `TypeRegistry.view` holds the
raw blocks; `TypeRegistry.view_props(name)` merges them along `is_a` (farthest ancestor first; nested dicts merge,
`"+key"` lists append) without their `"doc"` notes; `{}` for unknown types. Records never gain a `view` key.

## 4. Deviations from the spec

Every number is cited in a comment of `sight.py` or a `"doc"` note of `core.json`.

1. **Eye heights are the body profile's** (TIER1_INTEGRATION 1.2, conflict 4): `HW` keeps optics only (focal lengths are
   computed from resolution and FOV: 931.2 / 446.8 / 337.2 px) and `eye_height_m` bridges to `body.eye_height_m`. The
   worked numbers for 1.50 m (pinned by tests): under-bed visibility (5 cm toy 0.5 m inside, clearance 0.25 m) from
   d_near > 0.875 m kneeling, 1.75 m crouching, 3.125 m standing; a wardrobe top (2.0 m) hides a 0.1 m box 0.3 m back
   until d_edge > 3 m; a floor toy behind a sofa needs a top above 0.79 m; a 2 cm item 0.5 m from the robot covers
   11.9 px standing (p 0.14), 17.6 crouching (p 0.55) and 24.3 kneeling (sure) at its top centre (the integration
   plan's 11.8 / 17 / 24 are measured to the floor); the low row (tilt -45) sees the floor from 0.66 to 3.32 m and the
   level look (tilt -10) from 2.46 m; the basket hides the floor within 2.357 m.
2. **One exact support test replaces the cell-quantized rules 5.4 a, b, c and d.** A sample's own support is skipped in
   the viewshed and tested exactly against the part of the ray that lies between the eye and the sample. This is the
   near-edge rule (with the support's true near edge, not the cell entry E(c)), the line over the back edge for things
   behind, and the clearance rule under furniture, in one formula that also works from beside a support.
3. **Samples**: things of 0.3 m and more get top centre, centre, the two upper corners of the side facing the eye and
   the middle of the far upper edge, at 0.9 h (the spec named six points and said five: the far pair is collapsed to its
   midpoint); things under 0.1 m are sampled at their top centre, the point rule d uses. The visible fraction is the
   mean over all samples, a sample failing its relation rule counting 0.
4. **Containers** (5.4 e): each sample ray must enter through the opening (front: the eye within 75 degrees of the
   front and the ray crossing the front plane inside the opening, between the sample's shelf board and the next; top:
   the eye above the rim and the ray crossing the rim plane inside it) and pass the neighbours, which are exact 3D boxes.
   This replaces the aperture-corner test and the back-row / lateral-overlap formulas and reproduces them for the
   axis-aligned cases. Glass containers (porosity >= 0.5) are seen through their walls at their porosity. Container data
   is computed per query (memoized in the snapshot), not stored in RoomGeom; container frames snap the front to the grid.
   `inner_spos` is the item's front-left corner `[u, v]`: u across from the left edge as seen from the front, v back
   from the opening.
5. **Placement where the spec is silent**: things under a support are centred under it unless they have `spos`; a
   support with something behind it stands at least that thing's depth off the wall (the grid puts wall-side furniture
   flush with the wall, so it gives up that depth); hung things hang below the hook line; parts are centred in their
   parent; held things sit at the carry position in front of the robot; things on a carried load travel with it.
6. **The lidar sees walls** (open doorway cells let the beam out). The spec's ring saw only spans and agents, which
   would report "nothing within 8 m" toward a wall 1 m away.
7. **Viewshed cache key**: eye x and y to 1 mm, z to 1 cm, range and wedge (spec: the eye cell), because device eyes
   sit anywhere in a cell; inputs are rounded before computing, so the cache never changes a result. Eyes at a cell
   centre (the robot, people) reuse per-offset ray templates (pure geometry), which halves the cost of a full viewshed;
   other eyes take the same walk uncached. An eye exactly on a grid line enters the next cell at distance 0; the walk
   clamps entry distances to 1 mm so slopes stay finite (a randomized check over 70,000 cells, lattice and corner eyes
   included, and a regression test). The wedge test compares squared values (no square root per cell).
8. **Category defaults** are keyed by category with a name table matched on `[type] + is_a + [category]` (the nearest
   name wins: a sofa is_a chair but is a sofa). Rows beyond 4.4 for existing types: chest freezers and top-load washers
   open at the top, cages and crates are 0.7 porous, sheer curtains 0.6, pools are sunken (no occluder); plants are 0.5
   porous at every size (4.4 said > 0.8 m; small plants are foliage too).
9. **`core.json` view blocks** only where a core type's row would be wrong (crib slats, a backless bench, a workbench's
   lower shelf, open racks and wire shelving, picket gates, lamps, the flashlight beam); blocks on bed, sofa, chair,
   table, ... would duplicate the rows and give one number two sources.
10. **Shade** (6.1): under any solid span whose bottom is above 3 cm (beds, sofas, table tops, wall cabinets); inside any
    entity that holds things inside (openable, shelved, or with an opening row), 1.0 with an interior light when open
    and powered; behind: the slot between a floor-standing piece's back and the wall when it is at most 0.3 m deep.
11. **Glare**: a specular source counts within 10 degrees plus its angular radius (an extended window) and only in front
    of the face; `glare()` takes the target's radius (backlight overlap) and light (computed when not given).
12. **People**: lying eyes are 0.2 m above the surface lain on (the people model's 0.8 m assumed a bed); a seated
    child's eye scales with the standing one (1.15 / 1.55); kneeling and crouching people count as seated. The robot as
    an occluder is its height scaled with its eye height by posture.
13. **Faces**: "down" is seen only on hung, held or carried things from below; side faces within 0.05 m of a wall or of
    a neighbour with the same relation are blocked (axis-aligned normals); a held item's front faces the robot.
14. **Occluders**: things under 3 cm tall never occlude; things resting on surfaces must be 0.25 m tall and wide, and
    any depth of "on" chain qualifies (stacked boxes); open door leaves of glazed doors are capped at porosity 0.6.
15. **`carried_occluder`** adapts `held_envelope["blocks_view"]` (PHYS-STATICS computes it with the 5.6 rule, trays
    excluded) instead of re-applying the thresholds, and adds `half_angle_deg`, `floor_hidden_m` and `carried_hides`.
16. **`view_cells`**: a cell counts when its centre floor point has any transmission (porous occluders included).
17. **`best_viewpoints`**: "visible" means a visible fraction of at least 0.25 and at least 24 px across with the head
    camera (a sure detection in good light); walking cost from the robot, else from the room's doorways.
18. **Static index** `"sight.sources"` covers occluders and containers; one pass sorts all candidates into their rooms
    and is memoized with the per-room signature by `World.mutation_count` once B1 adds it.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim/kernel/sight.py` lists the stand-ins for not-yet-merged stage A siblings, all for
**CAM-RUNTIME** (B2, after every stage A package is merged) to delete together with the local branches of the bridges:

* `EYE_HEIGHT_M` (the 1.2 table) and the local branch of `eye_height_m` -> `body.eye_height_m(state)` (BODY-MODEL).
* `CARRY`, `_local_held_envelope` and `_load_top` -> `phys.support.held_envelope(state)` (PHYS-STATICS).
* the local branch of `_dims` -> `phys.support.resting_size(state, eid)` (PHYS-STATICS).

The bridges (`_bridge`) import the sibling lazily and use it when it exists. Contracts consumed:
`body.eye_height_m(state) -> m`; `phys.support.held_envelope(state)` fields `items, two_handed, bottom_m, top_m,
front_m, width_m, depth_m, height_m, blocks_view`; `phys.support.resting_size(state, eid) -> [w, d, h]` in the entity's
own frame (orientation applied, rot not). On 2,505 checks over three generated houses (every entity upright and on its
side, 90 random held-load combinations, five postures) the stand-ins equal the in-progress sibling implementations.
`tests/test_sight.py` switches the bridges off (it tests the stand-ins), and `BridgeTests` checks the delegation with
fakes.

Contracts provided for later packages: the API of section 3, `registry.view_props`, and these conventions: eyes carry
their `ignore` ids, a wedge is `(yaw, hfov / 2)` (sight adds the 15-degree margin and every cell within 1.5 m), the
`children_map` snapshot is per unchanged state, and all angles are grid degrees.

## 6. Gate results

Gate id **CAM-SIGHT**, recorded in `catalog/analysis/tier1/gate/CAM-SIGHT/` against **A0**. Final line:
`TIER1-GATE CAM-SIGHT [CAM-SIGHT] vs A0: PASS (12 pass, 1 skip; this run: G7)`.

**Isolated tree.** A0 is not committed, and five other stage A packages were editing the shared tree, so the record was
taken on an isolated copy in my scratch folder (as PHYS-DATA did): `git archive HEAD`, plus A0's files (engine.py,
systems/core.py, timeline.py, the five kernel stubs, tier1_gate.py and its README, gen1_digests.json, gate/A0, the A0
handoff) committed as the base of a throwaway scratch repository, plus the two empty directories git does not carry
(`tasks/templates`, `house_sim/data/actions`), plus this package's four files on top. GEN's in-progress
`generator/profile.py` replaced A0's stub in the shared tree; nothing in HEAD + A0 imports it, so it was left out. G10 in
that tree sees exactly this package's files.

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  27 test files x normal+strict: 2730 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains 10/16 files ok (8 failing tests, all known from A0); kernel files with
           HOUSE_SIM_DOMAINS=core 3/4 ok (test_sight.py green; test_perception.py is A0's known one)
G1c  PASS  1 module(s) imported, none registers anything
G2   PASS  own mode: 242/242 specs pass (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic: 226/242 specs pass (1210 trials); vs A0: 0 / 0 / 0 changes; 250 realistic trials identical
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  layout: 240 houses, 0 problems; counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.41 ms/substep, 1 h wait 1.121 s (A0: 4.38 / 1.102; limit x 1.05)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  camera: tools have no --camera yet
G10  PASS  16 changed file(s) (4 package files + the gate folder); 0 outside the list of CAM-SIGHT
compare A0 -> CAM-SIGHT: G0, G2, G3, G5, G7 PASS; compare result: PASS
```

`python tools/orchestration/tier1_gate.py compare A0 CAM-SIGHT --package CAM-SIGHT` on the project tree prints the same
comparison and `compare result: PASS`. Reviewed differences: none (no verdict, digest, fingerprint, layout or
generation-1 change). Golden: not applicable before B1.

**Run history** (for the record's partial re-runs): the first full run (about 1 h 40 min while other agents' gates
saturated the CPU) failed G1 on `test_eval_splits.py` strict (the empty `tasks/templates` directory was missing from my isolated
tree) and on `test_perception.py`'s realistic-observe timing test (10.7 / 12.0 ms against its 10 ms bound under load;
it passes alone, and nothing in the simulation imports `sight`), and G7 at 5.13 ms / 1.88 s. After creating the
directories and a last sight change (the boundary-eye fix and the cheaper wedge test, deviation 7), G1, G1b, G1c and G10
were re-run (`--only`, all PASS), and G7 was re-run in a quiet window (PASS above). An interleaved A/B of
`bench_systems.py 40 0 1 2 3` on the isolated base without this package and with it gave 5.05 / 5.20 / 4.94 vs
5.04 / 4.87 / 4.94 ms per substep under the same background load: no difference. `sight` is not imported by a
simulation step (checked), and the registry's view blocks are read only by `view_props`.

**Package micro-benchmarks** (this machine, quiet, best of 5; seeds 0 and 7, every room, eyes at both corners and the
centre; budgets TIER1_CAMERA 16 / TIER1_INTEGRATION 3.4, tests fail at 3x):

| item | budget | measured |
|---|---|---|
| viewshed, full, indoor (worst room and eye) | <= 0.3 ms | 0.18 ms |
| viewshed, full, outdoor (worst, 19 x 18 and 28 x 19 yards) | <= 1.5 ms | 0.31 ms |
| viewshed, one-frame wedge | "about 20 % of full" | 0.19 / 0.33 ms (see section 7) |
| lidar ring (24 rays) | <= 0.4 ms | 0.07 ms |
| `visible_fraction` per entity, warm, one snapshot per step (the `can_see` core) | <= 0.05 ms | 0.007-0.014 ms (0.023-0.036 without a snapshot) |
| `view_cells`, one frame | - | 0.23 ms |
| `RoomGeom` cold build (once per occlusion change) | - | <= 2.2 ms per room |
| `occ_sig` without a mutation counter (stage A only) | - | <= 0.46 ms per call |

Viewshed caches: 64 room geometries, 2,048 viewsheds (64 rooms x 32 eyes), 256 container layouts, ray templates per
eye-to-cell offset (at most 49 x 49 within 12 m).

## 7. Known gaps

* Phase 3 and 4 items are not here (TIER1_CAMERA 5.8, 5.9): doorway and window apertures, `aperture_viewer`,
  `doorway_views`, mirrors. Views end at the room's walls.
* Occlusion is at cell-and-box resolution: crossed occluders are taken per 0.5 m cell along the ray to the target cell's
  centre (5.2); only a sample's own support, its container's neighbours and agents are tested exactly.
* Light is scalar: beams cast no shadows, ambient is room-uniform (plus the `light_at` hook), and a backlight source's
  own line of sight is not tested.
* Things under or behind a support without `spos` take a fixed position (centred / mid-gap); setups that care should
  give `spos`. A free-standing piece's front points at the room centre whatever its `rot` (spec rule 3.4).
* Faces with non-axis normals are never blocked; the front cone of a container is measured from its centre.
* Cost without a World mutation counter (stage A): `occ_sig` 0.3-0.5 ms per call and a cold `RoomGeom` build up to
  about 2.5 ms per room; from B1 the signature is computed once per mutation count.
* The ray-template cache grows with the offsets used (at most 49 x 49 within the 12 m range, a few MB).
* A one-frame wedge viewshed now saves little (0.19 vs 0.18 ms indoors): with ray templates the per-cell bookkeeping,
  not the ray walk, dominates. Both are far inside the viewshed budget; a row-range prefilter could make wedges cheaper
  if CAM-RUNTIME needs many per step.

## 8. How to test

```
python -m unittest tests.test_sight                       # 59 tests, 5-15 s (two subprocess determinism runs)
set HOUSE_SIM_STRICT=1 & python -m unittest tests.test_sight
set HOUSE_SIM_DOMAINS=core & python -m unittest tests.test_sight
python tools/orchestration/tier1_gate.py compare A0 CAM-SIGHT --package CAM-SIGHT
python tools/orchestration/tier1_gate.py gen1-digests     # the view blocks never reach houses: 340/340 equal
```

Useful manual checks:

```python
from house_sim.engine import HouseEnv
from house_sim.kernel import sight
st = HouseEnv(seed=7).world.s
rid, (c, r) = st["robot"]["room"], st["robot"]["pos"]
eye = sight.Eye(rid, (c + .5) * .5, (r + .5) * .5, sight.eye_height_m(st), ("robot",))
cmap = sight.children_map(st)
{e: sight.visible_fraction(st, e, eye, cmap=cmap)[0] for e in cmap.get(rid, [])}
sight.lidar_ring(st, eye, 90.0); sight.bright_sources(st, rid); sight.view_props(st, "bed_1")
```

## 9. Follow-ups

* **CAM-RUNTIME (B2)**: `camera.HW = sight.HW`; head and wrist viewers as `sight.Viewer` with `ignore=("robot",)`;
  pass the World to `room_geom` / `occ_sig` (the memo) and one `children_map` snapshot per step view; `can_see`
  composes `visible_fraction` (LOS), `px_on_target` / `effective_px` (>= 10 px, k_motion 1) and `ir_shape`;
  `floor_visible_ahead` uses `carried_occluder` and `carried_hides`; coverage applies the 0.12 light rule to
  `view_cells`; delete the stand-ins of section 5; `register_robot_field("facing_deg" | "view", ...)` (BODY-MODEL
  pre-declared both identically).
* **CAM-ACTIONS (B3)**: the setup op `row` writes `inner_spos` in the convention of deviation 4; the `view` op merges
  into `record["view"]`, which `view_props` reads (the memo keys on its content).
* **CAM-RENDER (B3)**: per-target context from `light_at` (with `ambient` precomputed per observe), `acuity_level`,
  `detect_probability`, `gated_face` and `faces_seen`; RECOGNIZE_FIELDS as in TIER1_INTEGRATION 1.12.
* **Domains (adoption round)**: the view blocks of TIER1_INTEGRATION 6 ("Camera"); the kernel defaults now cover
  fridges and freezers (front, interior light), chest freezers and top-load washers (top), cages and crates (0.7),
  sheer curtains (0.6), pools (sunken) and plants (0.5), so domain blocks are needed only where these mislead (for
  example hedges 0.3, glass door windows of ovens and washers, porous ladders).
* **Main session**: on the shared tree `tier1_gate.py scope --package CAM-SIGHT --shared-tree` lists A0's uncommitted
  files as outside the package (G10 in section 6 is from an isolated tree with A0 as its base commit). The stray
  `occ.out` that PHYS-DATA's and BODY-MODEL's handoffs mention was a profiler output of this package; it is deleted.
