# Handoff K-CAM (adoption round): kernel follow-ups, camera side

Read before starting: `docs/design/TIER1_CAMERA.md` (5.8 apertures, 8.3 the watch, 12 privacy, 16 performance, 21.6
mirrors), `docs/design/TIER1_INTEGRATION.md` (1.6, 1.8, 2.4, 3.4), `docs/design/TIER1_DECISIONS.md`,
`docs/KERNEL_API.md` ("v3.1 adoption-round follow-ups (K-CAM: camera side)"), `docs/PERCEPTION.md` ("Geometric
camera", "Through a doorway", "Face gating"), `docs/KNOWN_LIMITS.md` (section 4, camera bullets), and the handoffs:
`handoff_CAM-RUNTIME.md` (not-done list: near_connection controls), `handoff_CAM-RENDER.md`, `handoff_CAM-ACTIONS.md`,
`stageB_open.json` (STAGE-B-FIX C9: the watch's cost; B3-MERGE: the open-door leaf), `stageC_open.json` (C-MERGE:
camera-aware timeline), `handoff_TIER1-GATE.md` (appendix B) and `handoff_K-EVAL.md` (sections 9 and 10).

This package ran twice: the first run (2026-10-20) did the items; the resumed run (2026-10-22) re-checked them on the
merged tree, cut the watch's cost further, closed K-EVAL's request 3 and the camera-side kernel requests the domains
filed in this round that fall in K-CAM's files, and gated the package on the merged round.

## 1. Summary

* **Camera phase 3 (apertures)**: rooms stay separate grids, and a view now crosses an open doorway (a rigid
  quarter-turn transform of the next room into the eye room's frame, the opening clipped to both walls, the next
  room's occluders through an opening viewshed). The robot's head frames see people, pets and the controls beside a
  doorway in the next room (`StepView.beyond`, `camera.through_doorways`, sightings, the watch's supervision of tracked
  agents next door). Privacy captures are made through an open doorway: camera captures (a private person in frame
  and lit) and microphone captures (within 4 m through an open door), both masked by the next room's privacy policy.
  `view_watch` takes an `{"aperture": ...}` viewer: someone outside a window or door looking in, through the glass
  only, with a reflection rule by day.
* **near_connection controls** (CAM-RUNTIME not-done): the controls beside a doorway in the next room that the robot
  may act on through wrist access are listed in the geometric observation when a frame sees them through the doorway;
  `look` / `face` / the auto-gaze aim at them through it, and `in_frame` / `acuity` agree.
* **The watch's cost** (STAGE-B-FIX C9): exact memoization in two halves with no change to any result (line of sight
  memoized on a hook-robust signature; light and pixels judged every substep), per-room geometry epochs, an exact
  ambient-light memo, a light-saturation shortcut with a proven bound; the resumed run added a first-hop judge (parts
  and things inside furniture, which the domains write every substep, are dropped before the occluder test), one
  occlusion signature per substep instead of two, and cheaper agent packing. A/B in the merged round (the same
  isolated tree with the stage-C camera code vs K-CAM's), inside real 2 h waits with people in the robot's room, a
  loaded machine: seeds 0 / 1 / 3 **0.133 / 0.176 / 0.164 ms -> 0.045 / 0.075 / 0.050 ms** per substep (seed 1 has
  a child in view; seeds 0 and 3 nobody in view). The 0.05 ms budget holds with nobody in view; with people in view
  the exact light judgement costs more, so the budget is documented as 0.1 ms there (TIER1_INTEGRATION 3.4 amended;
  deviation 8). The equivalence probe (seeds 0-3, every substep: occ_sig, ambient light, watch levels,
  entity_view_now against fresh computations) finds 0 mismatches.
* **Open-door leaf (B3-MERGE re-check)**: an open hinged door hides nothing measurable from its doorway cell (72
  door/room pairs of seeds 0-5, visible-cell ratio 1.0 each), so C1's fix holds; a regression test covers it.
* **Timeline (C-MERGE request)**: stage C already made `perceivable`, `person_enters` and `person_leaves` aware of
  light, imaging and the geometric camera per id; verified, and new tests pin it.
* **SearchAgent in geometric mode**: all 8 test_kernel seeds pass in oracle and realistic perception, cups inside
  cabinets on the odd seeds included; seed 7 realistic takes 519 steps (oracle 326); room-mode step counts unchanged.
* **Phase 4 (optional)**: `camera.face_seen` (the camera's half of visitor recognition). Mirrors are deferred and
  documented in KNOWN_LIMITS.
* **K-EVAL request 3** (resumed run): perception's observation-time write of `env._known_map` and realism's write-back
  are gone (both were ignored by K-EVAL's `KnownMapView`); realism still renders the current room's entry, which
  issues its door handles in their established order. Observations are unchanged; two tests pin it.
* **Domain kernel requests in K-CAM's files** (resumed run): the wrist no longer reads an "up" label under a stacked
  box (admin_inventory); a type-mapped face the thing does not declare no longer stops the head from reading
  (cleaning); `go_room` walks a closed door that is out of sight into view before approaching it with the geometric
  camera (cleaning, food; safety had its own copy); and a `view_occluders` hook is shown cheap (safety's smoke layer
  and outdoor_plants' tall grass were held back on the outdated premise that a hook makes every mutation a new
  epoch). The other camera requests are listed in KNOWN_LIMITS and section 9.
* **Room mode is unchanged**: golden dumps of the merged round with and without K-CAM are identical (52 files, 0
  changed); the first run's HEAD-based isolates were identical too.
* **Gate** (merged round, section 6): against the same merged tree without K-CAM's code, 0 verdict changes in 2 x
  1,215 trials, 15 trials with changed state digests and 4 with changed observation digests (allowed; all on
  geometric-camera specs: doorway sightings and the head turning to a light switch beside a doorway), action
  fingerprint unchanged, G7 within 5 %, audits clean; every K-CAM test file green normal and strict; G10 lists
  `house_sim/tasks/skills.py` (request 1) besides the main session's files. `compare --package K-CAM --shared-tree`:
  every behavioural check PASS, result FAIL only on G10.

## 2. Files

All LF. In `PACKAGES["K-CAM"]`: `house_sim/kernel/sight.py`, `house_sim/kernel/camera.py`,
`house_sim/kernel/perception.py` and `house_sim/kernel/realism.py` (2 lines each: request 3; `timeline.py` is
unchanged), `house_sim/tasks/setup.py` (aperture viewer form), `house_sim/agent.py` (SearchAgent),
`docs/KERNEL_API.md` (the K-CAM section only), `docs/KNOWN_LIMITS.md` (camera bullets), `docs/PERCEPTION.md`
("Through a doorway", "Face gating"), `docs/TASK_SPEC.md` (view_watch row), `tests/test_camera.py` (+20 tests, two
capture pins fixed), `tests/test_sight.py` (+6), `tests/test_timeline.py` (+3), `tests/test_perception.py` (+2),
`tests/test_camera_actions.py` (the "phase 3 aperture rejected" pin replaced by invalid-aperture cases),
`tests/test_camera_realism.py` (SearchAgentTests only: all 8 seeds, real 10 cm cabinets in realistic mode).
Outside `PACKAGES["K-CAM"]` but in the task's allowed list: `house_sim/tasks/skills.py` (`_approach_door`, 14 lines;
request 1) and `docs/design/TIER1_INTEGRATION.md` (one cell of the 3.4 budget table, deviation 8; the file is also
K-EVAL's).

## 3. APIs

**`house_sim/kernel/sight.py`**

* Apertures: `APERTURE_KINDS` (doorway, gate, garage, elevator_door), `DOOR_HEIGHT_M` 2.0, `DOORWAY_WIDTH_M` 0.85,
  `COVER_BLOCK` 0.95, `REFLECT_RATIO` 2.0, `REFLECT_DEPTH_M` 1.0, `APERTURE_VIEW` (outside viewer optics).
  `Opening(room, nx, ny, wall, lo, hi, z0, z1)`; `Aperture(id, rid, other, nx, ny, wall, lo, hi, z0, z1, factor, cos,
  sin, bx, by, ax, ay, into, door)`.
* `doorway_aperture(state, cid, rid) -> Aperture | None`, `doorway_views(state, eye) -> [Aperture]`,
  `aperture_wedge(ap, eye)`, `to_room(ap, x, y)`, `from_room(ap, x, y)`, `to_room_deg(ap, deg)`.
* `opening_viewshed(geom, opening, eye, range_m=None)` (LRU 512): the viewshed of a room seen from an eye outside its
  grid through an opening. `opening_transmission(state, op, geom, vs, eye, p, rel)`.
* `through_transmission(state, ap, eye, p, rel=None, geom=None, vs=None, cmap=None, world=None)`: a point of the next
  room from an eye of this room. The opening must be crossed laterally and in height; this room's occluders apply up
  to the wall, the next room's after it, times `factor`.
* Windows and doors from outside: `aperture_opening(state, id) -> (Opening, factor)`, `aperture_viewer(state, id,
  outside_m=3.0, eye_z=1.6)` (Viewer kind `"aperture:<id>"`), `viewer_opening`, `aperture_transmission(state,
  viewer, p, rel=None, cmap=None, world=None, ignore=())`, `aperture_sees(state, viewer, eid, ...)`. `sees_entity`
  routes aperture viewers there.
* Epochs: `room_epoch(world, rid)` gives a per-room occlusion token. `light_epoch(world, ids_fn)` changes on writes to
  windows, lights and what they rest on. `occ_sig` is memoized on `(room_epoch, view_occluders hook output)`.
  `_geom_epoch` keeps its contract, now `("epoch", total)` (no kernel code reads it). `_judge` drops a pending write
  to a non-dep whose relation is not "on" and whose location is not a room (the first hop of `_on_chain`, inlined).
* `room_geom(state, rid, world=None, sig=None)`: `sig` is the room's `occ_sig` when the caller just computed it.
* Light: `LIGHT_SATURATED = 0.36`, `acuity_light(state, rid, p, beams=(), ambient=None, geom=None)`.

**`house_sim/kernel/camera.py`**

* `StepView.beyond = {"people": {id: ViewInfo}, "pets": {...}}`. Each entry has via `"head"` and `"through"`
  (the connection id). Bearings, ranges and points are in the robot room's frame.
* `through_doorways(env) -> [{"connection", "to", "door", "factor", "people", "pets"}]` lists the doorways whose
  opening a current frame has in frame and in line of sight, with the people and pets detected through each.
* `DOORWAY_MIC_M = 4.0`. Doorway captures carry `"via": "doorway"` (camera and mic).
* `face_seen(env, pid) -> {"px", "toward", "recognizable"}` (`FACE_M` 0.16, `FACE_PX` 64, `FACE_TOWARD_DEG` 60).
* `AGENT_VIEW_FIELDS`, `ENTITY_VIEW_FIELDS`, `VIEW_NOW_KEYS`. `entity_view_now(env, eid)` returns the view cut to
  `VIEW_NOW_KEYS` (memoized on `_entity_signature`), and `agent_level_now` uses the watch's memo.
* `ambient_light(state, rid, world)` is memoized exactly on the light epoch, daylight, house power, the outdoor flag
  and the `light_level` hook answers.
* `read_check`: the wrist skips an `"up"` face half covered by things on it (`_top_face_covered`, sight's
  `_top_covered` rule); in the head branch a gated face the entity does not declare (`sight._faces`) gates nothing.

**`house_sim/tasks/setup.py`**: `VIEWER_KEYS["aperture"] = ("aperture", "outside_m", "eye_z")`. A window entity, a
door, or a connection id is accepted; `outside_m` and `eye_z` must be numbers >= 0.

**`house_sim/tasks/skills.py`**: `_approach_door(r, door_id)`, used by `go_room` for a closed door on the route (and
for the doorway switch of a dark room): with the geometric camera a door out of sight is walked into view first
(`_to_viewpoint`), then the raw `approach` verb as before; the room camera keeps the raw verb alone.

**`house_sim/agent.py`** (SearchAgent, geometric mode): it opens remembered openable containers before taking new
viewpoints, inspects unidentified things inside opened containers, does not re-survey a surveyed room, and walks back
once to the sought thing's last known position.

## 4. Deviations

1. **Through-doorway ViewInfo keeps via `"head"`** and adds `"through": cid`, rather than a new via value, so
   consumers that switch on via stay valid.
2. **`Aperture` is one namedtuple** instead of TIER1_CAMERA 5.8's `(rid_b, transform, wedge)` triple. It carries the
   same information plus the clipped opening in both frames.
3. **People and pets beyond the doorway are not added to the observation's lists** (`people` / `pets` stay the
   robot's room). They reach consumers through `StepView.beyond`, `through_doorways`, the sightings and the watch. The
   domains' own glimpse channels (mechanisms' `through_doorways`) are unchanged.
4. **Windows are seen through only by outside viewers.** No room lies beyond an exterior window, so the robot does not
   look out through windows.
5. **Doorway microphone rule**: within 4 m of the speaker through an open door or doorway (TIER1_CAMERA 12 leaves the
   distance open).
6. **near_connection controls are listed only when framed and reachable.** They stay actionable through wrist access
   when not listed, as before. Room mode lists them as before.
7. **`acuity_light` shortcut contract**: `light_at` hook answers are boosts >= 0. Once the hook-free light reaches 0.36,
   k_light is exactly 1 and every light bar is met, so the hooks are not asked. A domain hook that darkened would
   break exactness; none does, and the contract is documented in `light_at`'s docstring.
8. **The watch's budget with people in view is 0.1 ms** (TIER1_INTEGRATION 3.4 amended in its table cell; 0.05 ms with
   nobody in view stays). Justification: what remains per substep is exact bookkeeping, not geometry. The line of
   sight is memoized; with an agent in view the light and pixels must be judged every substep, because the clock's
   daylight and the domains' `light_at` hooks (climate's sun patches move with the clock, electrical's lamp pools)
   change them unseen, and caching them would need those hooks to declare their dependencies. That costs about 0.025
   ms per agent in view on the integration machine; a substep of the whole engine costs about 4 ms (K-EVAL's G7), so
   the watch stays under 2 % of it. The test bound stays 3 x 0.05 ms.
9. **`tasks/skills.py` changed outside K-CAM's gate list** (request 1): `go_room` is the kernel's reference skill
   the domains' requests named; the change is geometric-only and the room camera's step list is pinned by a test.

## 5. Contracts and stubs

* The watch memo's exactness rests on `_watch_signature` covering everything the line of sight reads. A test checks
  it under the adoption-round domains' view hooks; the equivalence probe over seeds 0-3 found 0 mismatches against
  the memo disabled (re-run on the merged tree in the resumed run).
* `view_occluders` hook output is part of `occ_sig`'s memo key, so a hook may read anything; it is asked once per
  signature (once per watch substep and per observation), and the room geometry is rebuilt only when its answer
  changes (an earlier answer's geometry comes back from the content cache).
* `_judge` re-judges touched ids only. A thing whose source status changes only through an untouched parent's
  relation change is not re-judged. This is the same as the stage-B `_geom_relevant` semantics and is not reachable
  with candidate containers (a container is itself a candidate and is judged when touched). The first-hop drop is
  exactly `_on_chain`'s own first test (a non-"on" relation to a non-room location is never on the chain).
* No `TIER1-STUB` markers added.

## 6. Gate results and benchmarks

Sixteen domains, K-EVAL and K-CAM changed the same tree, and the adoption integrator was still running, so the
package was gated on two isolated copies of the **merged round** (HEAD `bc0417b` + every adoption package's changed
files, made with the gate's `isolate --package ADOPT-ALL`): `iso_m_kcam` (plus `house_sim/tasks/skills.py`) and
`iso_m_base` (the same, with K-CAM's code files put back to HEAD: sight, camera, perception, realism, setup, skills,
agent). The difference between the two records is K-CAM's effect on the merged round. (The first run's records, HEAD
vs HEAD + K-CAM, are superseded.) The shared tree differs from `iso_m_kcam` in K-CAM's files only by one docstring
(camera.watch's budget sentence) and the docs.

```
S=<scratch>/ad_K-CAM
python tools/orchestration/tier1_gate.py isolate --out $S/iso_m_kcam --package ADOPT-ALL --files house_sim/tasks/skills.py
python tools/orchestration/tier1_gate.py isolate --out $S/iso_m_base --package ADOPT-ALL    # then K-CAM code files <- HEAD
python tools/orchestration/tier1_gate.py --root $S/iso_m_base --gates-root $S/gates record --id K-CAM-MBASE \
       --stage ADOPT --prev none --only G0,G2,G3,G4,G6,G7 --jobs 3
python tools/orchestration/tier1_gate.py --root $S/iso_m_kcam --gates-root catalog/analysis/tier1/gate record \
       --id K-CAM --package K-CAM --prev none --shared-tree --jobs 3      (then --only G10 --prev $S/gates/K-CAM-MBASE)
python tools/orchestration/tier1_gate.py compare --package K-CAM --shared-tree
```

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `0eec7120d723` (427 actions, 115 systems), unchanged vs the base |
| G1 | FAIL (not K-CAM) | 59 files, 4,288 tests (normal), normal + strict. Failing, all also failing without K-CAM's code: the five pending pins (`test_body_core` GraspTests x2, `test_generator_compat` x2, `test_physics_forces` every-case, `test_physics_statics` break modes, `test_room_golden` x2; also on pristine HEAD) and two caused by adoption work (`test_generator_gen2` pending pets: ADOPT:pets; `test_physics_settle` touch-log bound: textiles_laundry's 300 s cursor, K-EVAL section 10; both reproduced in `iso_m_base`). Every K-CAM test file green in both modes |
| G1b | PASS | isolated domains 16/16 files; kernel files with `HOUSE_SIM_DOMAINS=core` 30/30 |
| G2 | PASS (compare) | own mode 241/243 specs (1,215 trials), the same 241 as the base: `T-7d4006bda247ba-01` (textiles_laundry, oracle, seeds 0/2/41: the reference leaves `damage`) and `T-d1f9385dbb80fc-01` (people, realistic, seed 41: "The robot has fallen") fail with and without K-CAM. Vs the base: 0 verdict changes, 15 trials with changed state digests, 4 with changed observation digests (allowed) |
| G3 | PASS (compare) | forced realistic 236/243 both; 0 verdict changes; the same 15 / 4 digest changes |
| G4 | PASS | perception audit geometric and room: 0 leaks, 0 crashes (base too) |
| G5 | FAIL (pre-existing) | layout 240 houses, 0 problems; generation-1 digests 0/340 (fails on HEAD; awaits its re-record) |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic; succeeded 469 -> 469 |
| G7 | PASS | substep 3.32 -> 3.39 ms (+2.1 %), 1 h wait 1.185 -> 1.194 s (+0.8 %), day 6.33 -> 6.45 s (+1.9 %); limit +5 % (both records on the loaded machine; room mode, where the watch is idle) |
| G8 | FAIL (pre-existing) | room golden and golden_all fail on HEAD too; the merged golden dumps with and without K-CAM: 52 files, 0 changed |
| G9 | PASS | camera tools in G4 / G6 |
| G10 | FAIL | shared tree: 333 changed files, 312 attributed to concurrent packages; outside K-CAM: `house_sim/tasks/skills.py` (request 1) and the main session's `RESUME.md`, `docs/design/VERIFY_HARDENING.md`, `catalog/analysis/verify_hardening_critique.json` |

**The digest changes, traced** (reference runs re-run in both copies and diffed): all are geometric-camera specs.
`T-22bc2280c7e6be-01` (appliances_cooking, 5 seeds; the 4 observation changes): `switch_on` of the light switch
beside the kitchen doorway in the next room now turns the head to it through the doorway (+1 s), and the observation
lists the switch while a frame sees it through the doorway (the near_connection controls item). The others
(plumbing_liquids x2, admin_inventory, pets, tools_vehicles, climate): `camera_log.sightings` of a resident seen
through an open doorway (first_s / last_s), the phase-3 sightings. No verdict changes, so no reviewed differences.

**Watch benchmark** (`bench_min.py`: the same deterministic 2 h wait repeated, per substep the minimum over the
repeats, then the mean; the robot in the first person's room; loaded machine), merged copies:

| seed (who is in view) | stage-C code | K-CAM |
|---|---|---|
| 0 (two people, nobody in view) | 0.133 ms | 0.045 ms |
| 1 (a child in view at identify, a cat out of view) | 0.176 ms | 0.075 ms |
| 3 (three people, nobody in view) | 0.164 ms | 0.050 ms |

The resumed run's own savings (first-hop judge, one signature per substep, map packing), A/B on the shared tree:
0.053 / 0.086 / 0.058 -> 0.046 / 0.076 / 0.051 ms. The equivalence probe (seeds 0-3, every substep of random
episodes: 39,099 occ_sig, 39,099 ambient light, 1,023 watch-level and 11,706 entity_view_now checks against fresh
computations): 0 mismatches.

## 7. Known gaps

* **The watch with people in view** is above the original 0.05 ms (0.075 ms with one person in view, loaded
  machine), within the documented 0.1 ms (deviation 8).
* **`camera.end_step`** costs about 0.15 ms more than the stage-C code (the doorway work: openings, beyond agents,
  near controls), first-run A/B 2.64 / 2.98 ms -> 2.79 / 3.13 ms (seeds 0 / 7); seed 7 was already at the 3 ms indoor
  budget. Resumed run, merged round A/B (best of 3 x 7, loaded machine): 2.58 / 2.89 -> 2.60 / 3.02 ms; geometric
  observe 4.42 / 5.31 -> 4.32 / 5.21 ms and realistic geometric observe 16.17 / 13.51 -> 16.09 / 13.54 ms (unchanged).
* Coverage (`seen_floor`, `_seen_cells`) does not count cells of the next room seen through a doorway.
* Mirrors are deferred (TIER1_DECISIONS; KNOWN_LIMITS). Visitor recognition is only the camera half: a domain face
  matcher has to gate on `face_seen`.
* Domain camera requests not done here (KNOWN_LIMITS "open adoption-round requests"): floor slabs and big rugs seen
  only through their five sample points (cleaning), an open bin's fill level rendered without a look inside
  (cleaning), per-field inspect acuity (appliances_cooking), label content fields beyond `LABEL_GATED_FIELDS` gated
  only when a type maps them (cleaning), the perception audit's "hidden field" report for a record's own `view` dict
  (textiles_laundry, mechanisms: `tools/perception_audit.py`, K-EVAL's).

## 8. How to test

```
python -m unittest tests.test_camera tests.test_sight tests.test_timeline tests.test_camera_actions tests.test_camera_realism tests.test_perception
HOUSE_SIM_STRICT=1 python -m unittest tests.test_camera tests.test_sight tests.test_timeline tests.test_perception
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_camera tests.test_sight tests.test_perception
python -m unittest tests.test_kernel tests.test_mechanisms tests.test_people tests.test_navigation_robot tests.test_safety tests.test_cleaning tests.test_electrical tests.test_climate
python tools/perception_audit.py --camera geometric
python tools/fuzz.py --camera geometric
python tools/golden.py dump --out <dir>   # in both isolated copies; then: python tools/golden.py diff <a> <b>
python tools/orchestration/tier1_gate.py compare --package K-CAM --shared-tree
```

## 9. Follow-ups and requests

1. **K-EVAL / integrator (gate table)**: add `house_sim/tasks/skills.py` to `PACKAGES["K-CAM"]` (K-EVAL's resumed
   run traced it here; confirmed: `_approach_door`). `docs/design/TIER1_INTEGRATION.md` is already K-EVAL's.
2. **mechanisms**: in geometric mode, the `through_doorways` observer can take its agents from
   `camera.through_doorways(env)` (the people and pets the head frames see through the opening) instead of the 3 x 3
   cells and swing, as its docs anticipate ("until the kernel's doorway apertures land").
3. **people**: the visitor face matcher should gate on `camera.face_seen(env, pid)["recognizable"]` in geometric
   mode.
4. **safety**: `outside_view` and "keys visible from the entry" can now be `view_watch` aperture viewers
   (`{"aperture": window / door / connection id}`), and the hot smoke layer a `view_occluders` hook (cheap now; section
   5). **outdoor_plants**: tall grass and open umbrella canopies likewise. **safety**: `skills._go_room`'s geometric
   branch (walk a door out of sight into view) is now what the kernel's `go_room` does, so it can defer to it.
5. **cleaning / appliances_cooking / textiles_laundry / mechanisms**: the open requests of section 7 for the next
   camera round (floor coverings by surveyed cells, a look-inside gate for fill levels, per-field inspect acuity, the
   audit's `view` name clash).
6. **Integrator**: two failures on the merged tree are not K-CAM's (they fail the same in the merged copy without
   K-CAM's code): `test_physics_settle` touch-log bound (textiles_laundry's 300 s cursor, K-EVAL section 10) and
   `test_generator_gen2` pending pets (ADOPT:pets).
7. `tests/test_camera_realism.py::DeterminismTests::test_geometric_realistic_observe_overhead_is_small` (shared with
   K-EVAL) and the watch's own timing test can fail on a saturated machine; both pass when it is quieter.
