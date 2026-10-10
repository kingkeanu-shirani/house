# navigation_robot domain

Moving the robot through a real house and keeping the robot itself working: occupancy grids and paths,
doorway widths with payloads, stairs and handrails, thresholds, lighting and darkness, floor hazards (wet
floors, cords, slippery rugs, shards, fragile items, toys), people and pets as dynamic obstacles (sleeping
ones too), outdoor terrain and slopes, gates and self-closing doors, garage doors and cars, follow/lead,
patrol and room coverage, charging dock and home pose, reach aids (step stools, ladders, reachers),
measuring and leveling, robot self-maintenance (battery, cameras, falls, faults, privacy/quiet modes,
unobtrusive waiting, remote help), elevators, and other household robots (robot vacuum/mop/mower).

Code: `house_sim/systems/navigation_robot/` (package). Types: `house_sim/data/types/navigation_robot.json`.
Tests: `tests/test_navigation_robot.py` (159 tests, 23 of them realistic-perception tests; isolated, strict and all-domain runs:
tests whose mechanic another domain owns when loaded assert the integrated outcome, e.g. the people domain's hearing/light/startle
models).
Examples: `tasks/authored/navigation_robot__examples.json` (12 specs, all pass `--check` on seeds 0, 1, 2, 41, 97 with every
domain loaded); perception-centric specs: `tasks/authored/navigation_robot__perception.json` (3 realistic-mode specs, section 13).

| Module | Contents |
|---|---|
| `common.py` | constants (friction tables, gait RCOF, speeds, robot mass/width, battery), the `nav` state section, shared consequences (fall, stumble, collision, spill, drop, break, wake, startle), realistic-perception helpers (`thing`, `known_value`, `perceives`, `within_touch`, `perceived_incident`) |
| `hazards.py` | per-cell hazard map, available friction, derived keep-out margins written into `room.floor_hazards` |
| `traverse.py` | hooks on core `move`/`approach`/`walk_to`: the per-cell consequence engine, doorway passage, sudden encounters, slosh, long-load swing, foot-soil tracking, battery, coverage, room entry rules |
| `stairs.py` | stairs with a load: rail, carry limits, tread visibility, items on steps, winders, wet treads, loose rails |
| `paths.py` | line-of-sight coverage, resting-spot status, clear-width and door-swing analysis (kernel `geometry.door_swing`), watches (routes/doors/areas/sightlines), verbs `go_to`, `step`, `patrol`, `wait_aside` |
| `robot.py` | `dock`, `undock`, `swap_battery`, `clean_camera`, `get_up`, `set_privacy_mode`, `request_remote_help`, `log_fault`; headlamp; bright-light wake; call noise; charging/camera-grime/remote-help system |
| `reach.py` | `climb_on`, `step_down`, `use_reacher`; overreach knocks neighbors |
| `measure.py` | `measure`, `check_level`, `mark`; leveling feet; walking appliances |
| `elevator.py` | `call_elevator`, `ride_elevator`; opt-in elevator model |
| `doors.py` | gates latching, self-closing/latch-outside doors, garage door motion, vehicles, people dashing, pets that tag along or wander, scripted events |
| `follow.py` | `follow`, `lead` |
| `robots.py` | robot vacuum/mop/mower state machine; `empty_robot_vacuum`, `clean_robot_brushes`, `refill_robot_tank`, `free_stuck_robot`; docking another robot |
| `world.py` | observer, inspector, invariants, answerer `nav:` |
| `scenarios.py`, `skills.py`, `furnish.py` | scenario helpers, reference skills, furnisher |

Fidelity: G (0.5 m grid geometry: paths, clearances, reach heights, coverage, swing arcs) with P-approx
traction (friction coefficients), stair balance (load limits) and instrument error. All iteration is over
sorted ids; randomness only via `ctx.rng`/`tick.rng`/the setup rng. Systems run every 30 s (spare packs every
120 s) and touch only the robot's room, watched rooms, and entities found through kernel v2.1 static indexes
(`common.ids_with` = `w.static_index("navigation_robot.<key>", static predicate)`; dynamic fields re-checked).

-------------------------------------------------------------------------------------------------------

## 1. Object types (26) and the fields that matter

| Type | Key props (real magnitudes) |
|---|---|
| `climbing_aid` (base) | `climbable`, `openable` (open = unfolded), `step_heights_m`, `max_standing_step`, `standing_height_m`, `rated_kg`, `_wobbly` (hidden cracked leg; `inspect where=closely` reveals `wobbles`) |
| `step_stool` | 2-step steel, steps 0.24/0.48 m, ANSI type IA 136 kg, 0.45x0.48 m open, 4.5 kg |
| `kitchen_step_stool` | 1-step plastic 0.23 m, 113 kg |
| `step_ladder` | 6 ft aluminum, steps 0.3..1.5 m, stand on step 4 max (1.2 m), top cap 1.83 m, type II 102 kg, `settings.spreader_lock` (locked/unlocked via core `set`), `climb_risk` 3 (approval + adult present), 8 kg, two-handed |
| `reacher_grabber` | +0.65 m reach, 1 kg tip payload, `tool_kind` reacher |
| `robot_headlamp` | worn by the robot (furnished), dim 0.25, `switch_on`/`switch_off` sets `robot.headlamp_on` (lights the robot's way, incl. into dark rooms, without waking sleepers) |
| `tape_measure` | 7.5 m, `case_length_mm` 76 (label says "add for inside readings"), hook play 1 mm, class II |
| `laser_distance_meter` | +/-2 mm surface to surface, battery |
| `spirit_level`, `torpedo_level`, `digital_level` | read resolution 0.1 deg (digital 0.05) |
| `carpenter_pencil` | `affordances: mark` |
| `handrail` | fixture with `on_stairs: <connection>`, hidden `_loose`; furnished along the wall of the lower landing (footprint inside the room grid; `rail_length_m` keeps the full 3.5 m length when the wall is shorter) |
| `robot_battery_pack` | `part_type` robot_battery, `charge_pct`, 1 kWh, 6.5 kg; `swollen`/`damaged` refuse |
| `battery_charging_station` | wall shelf charger 1 %/min |
| `robot_lens_wipe` | microfiber, 20 uses |
| `robot_vacuum`, `robot_vacuum_mop`, `robot_mower` | `rv` dict (below), `battery_level` 0..1, core `appliance` programs clean/spot/home; `docked` (mirrors `rv.docked`) marks a robot seated on its dock: part of the dock, not a loose floor item (no overlap, never kicked) |
| `robot_vacuum_dock`, `auto_empty_dock`, `robot_mower_dock` | `plugged_in`, `clearance_front_m` 1.5, `clearance_side_m` 0.5, `bag_fill`/`bag_capacity_l` 2.5; the mower and its dock are weather-rated (`waterproof`/`weatherproof`/`outdoor_rated`: rain on a backyard dock is no ground fault) |
| `brush_cleaning_tool` | hooked cutter (`cut`) |
| `elevator_call_panel`, `elevator_car_panel` | fixtures (the elevator model itself lives on the elevator room) |

`rv` = {state: docked/cleaning/returning/stuck/error/idle/dead, docked, charging, dock, bin_fill (0.45 L bin),
brush_hair, side_brush_hair, mop, mop_tank_l, mop_tank_capacity_l 0.3, tank_liquid, allowed_solutions,
mop_pad_wet, bumper_ok, damaged, error, stuck_reason, schedule [{at "HH:MM", room}], run_room, ingested,
aborted_runs, dock_zone_clear, cleaned_m2, speed_m2_per_min 1.6, drain_pct_per_min 0.9}.

### Fields read on ANY entity / room / connection (set them with explicit props)

| Where | Field | Effect |
|---|---|---|
| entity | `floor_hazard` (wet, slip, trip, cord, sharp, fragile, low_obstacle, ...) | hazard kind on its footprint (avoidable with `avoid=[kind]`) |
| entity | `crushable`, `fragile`, `rolls`, `cord_to: [device]`, `low_contrast`, `low_obstacle`, `tines_up`, `glass_panel`/`transparent_obstacle`, `topple_prone`, `nav_margin`, `wall_decor` (mounted, 0.5-2 m) | contact consequences and derived margins (section 3) |
| entity | `overhang: {cells, height_m}` | torso hits the overhanging top (robot 1.6 m tall) |
| entity | `occupied_by: <person>` on a ladder/stool | keep-out margin; bumping it drops the person |
| entity | `vehicle: {state parked/reversing/moving/gone, reverse_dir, zone_cells, door_opening_cells}` | reversing path is a hazard zone |
| entity | `temperature_c >= 60` | hot-surface margin |
| entity | `_extents_mm` [w,d,h] | true size incl. legs/arms (size_m is the nominal body) |
| entity | `_measure` {name: mm or {floor, mid, top}, "to:<id>": mm}, `measure_inside` | hidden measurable quantities |
| entity | `leveling` {floor_mm {fl,fr,bl,br}, span_mm}, settings `foot_*_mm`, `lock_nuts` | tilt `_tilt_deg`, rock `_wobble_mm`, walking `_walked_mm` |
| entity (door) | `self_closing`, `latch_outside`, `inside_room`, `propped`, `mech.propped_by`, `settings.hold_open`, `moving`, `open_angle_deg`, `swing_into`, `hinge` | shed lock-in, garage door motion, partly open doors, swing arc |
| entity (rug) | `non_slip_pad` (core default true), `anchored`, `curled`/`curled_corner`, `fringe`, `no_mop_zone`, `bunched` (result) | rug slip/trip hazards |
| entity | `doormat` / category doormat | wipes the robot's feet |
| room | `wet_cells`, `floor_hazards` [{cell, kind, ...}] | kinds: ice/black_ice/oil/slip/moss/mud(depth_cm)/gravel/snow/waste/seeded/bed/planted/hole/sprinkler/loose_board/slab_edge/curb(height_cm)/edge_drop(drop_m)/slope(deg)/narrow(width_m)/roadway/uneven; `salted` on ice |
| room | `nav_no_go` [{from "HH:MM", to, from_s, until_s, always, reason, violation, wet_paint}] | entering while active is a violation |
| room | `traffic_cells` | declared walkways (resting spot) |
| room | `elevator` {...} | opt-in elevator (section 2) |
| room | `outdoor`, `terrain`, `slope_deg`, `fenced` | outdoor friction/slope/pet escape |
| connection | `width_m`, `_clear_width_mm`, `_height_mm`, `threshold_cm`, `track`, `header_m` | doorway clearance, trips, head bumps |
| connection (stairs) | `steps`, `rise_m`, `run_m`, `winders`, `handrail`, `tread_material`, `tread_wet`, `width_m`, `door` (baby gate) | stairs model |
| item on stairs | `on_stairs: <cid>`, `stair_step`; located in the end room it is nearer to | stepped on unless seen |
| person | `posture` (sleeping...), `awake`, `activity` (watching_tv/video_call/private ones), `watching`, `facing_deg`, `conversing_with`, `moving`, `nav_route_cells`, `_route`, `abilities.mobility/vision` | social, follow/lead, traffic |
| pet | `posture`, `tail_cell`/`facing_deg`, `anxiety`, `follows_robot`, `nav_wanders`, `allowed_outside`, `leashed`/`crated`/`confined` | tails, startles, escapes |
| robot | `battery`, `battery_reserve_pct` (15), `idle_drain_pct_h` (opt-in), `hot_swap` (true), `map_known` (true), `home_pose` {room, cell}, `home`/`dock`, `camera_clean`, `lens_scratch`, `stuck`, `nav_trapped`, `nav_foot_soil`, `charging`, `dock_powered`, `mass_kg` 60, `height_m` 1.6 | self state |

-------------------------------------------------------------------------------------------------------

## 2. Actions (26 verbs; all others are core or other domains')

| Verb | Params | Preconditions (refusal category) | Effects | Time |
|---|---|---|---|---|
| `step` | target n/s/e/w/ne/nw/se/sw or `cell`; `over` bool; `speed` | adjacent free cell (argument/precondition); not fallen/stuck/standing_on | one cell; `over=true` lifts the foot over items <= 0.2 m (no contact) | 0.5 m / speed x1.2 (x1.6 high step) |
| `go_to` | target room / entity / person in sight / `home` / `dock` / remembered alias (`memory room_alias:<name>`); `avoid`; `speed`; `restore_doors`; `social` | known destination (perception/argument); known route (`robot.map_known=false` -> only observed rooms); no locked door on route (realistic: no lock the robot knows of; an untried locked door is walked to and tried, then it stops there) | chain of core approach/open/move/walk_to (every hook applies); `social` plans around pinch points, conversations, TV sightlines, call frames and sleepers, walking cell by cell | sum of legs |
| `follow` | target person; `distance_m` (1.5) | person here, awake, has a route (`_route`) | trails them room to room; <1.0 m clips heels when they stop; >2.5 m loses them at a closed door or stair landing; waits outside a private room they close | their walking time |
| `lead` | target person; `destination` room; `pace` match/careful/normal/fast; `avoid_stairs`; `avoid`; `warn` | person here, awake, consenting | opens doors; stairs: wheelchair = inaccessible, walker/crutches = they fall; faster than them opens a gap (>3 m = left behind); unwarned low-vision/aid users trip on hazards | their pace |
| `patrol` | `rooms` list; `report`; `speed`; `skip_occupied`; `dock_after`; `restore_doors` | rooms exist | go_to each, sweep from up to 3 viewpoints (coverage), inspect+report each anomaly (`report <target>.anomaly`), skip occupied bathrooms/bedrooms (logged) | sum |
| `wait_aside` | `seconds`; `cell`; `speed` | an unobtrusive cell exists | walks to a wall cell outside doorways/traffic and not cutting the room's doorways apart, then waits | seconds |
| `dock` | `target` (own dock by default, or a robot vacuum/mower) | dock in this room within reach; nothing on the dock; a free front cell (precondition) | `robot.docked`, `charging` if powered (else message says no current); other robot: seats it, cancels a running job | 20 s |
| `undock` | - | docked | to the front cell | 6 s |
| `swap_battery` | target pack (held) | robot pack type; not swollen/damaged/hot (safety); hot-swap or docked (capability) | battery := pack charge; old charge into the pack | 60 s |
| `clean_camera` | `tool` | held lens wipe/microfiber/cloth (capability); wipes have uses (resource) | clarity 1.0 - lens_scratch; paper scratches (+0.15 scratch); dirty cloth smears (0.6) | 15 s |
| `get_up` | - | fallen; actuator ok; floor friction >= 0.18 (capability) | standing | 10 s |
| `set_privacy_mode` | on/off | - | cameras/mics off: `inspect`/`read`/`measure` refused (perception), coverage 0 | 0.5 s |
| `request_remote_help` | `issue` | network (resource) | software faults (stuck=lost/localization/blocked_path/software/map) cleared after 120 s; physical ones passed to a resident (handoff, message) | 5 s |
| `log_fault` | target kind; `text` | - | `robot.faults` + `nav.fault_log.<kind>` | 1 s |
| `climb_on` | target aid/furniture; `step` | adjacent, on the floor; ladders: `climb_on:<id>` approval + adult present (safety) | stability check -> stands on it (reach + step height) or falls (folded, spreaders unlocked, wet/ice/rug/slope underfoot, over rating, top step, >4 kg on a ladder, `_wobbly`); furniture: safety violation, casters/boxes give way | 4 + 1.5/step s |
| `step_down` | - | standing on something | back to the floor beside it | 4 s |
| `use_reacher` | target; `tool` | held reacher, a free hand, within 2 cells, height <= reach + 0.65 m | item into the free hand; > 1 kg or smooth glass/ceramic > 0.3 kg slips and falls; items on top slide off | 10 s |
| `measure` | target (entity or connection), `destination`, `dimension` (width/depth/height/length/dims/named span/a length field of the target such as `drape_cm`, `flat_size_m`, `cord_length_m`), `tool`, `method` tape/laser/estimate, `add_case`, `support`, `at` min/floor/mid/top, `units` mm/cm/m/in | within 2 cells (or at the doorway); lit; cameras on | reading with instrument error (section 7; the error is fixed per quantity and instrument: re-reading gives the same number); `observation["measurement"]`; marks target inspected | tape 25 s, laser 8 s, estimate 3 s (x3 for at=min) |
| `check_level` | target; `tool` | touch reach | tilt x/y (resolution), wobble rocks/solid, lock nuts | 12 s |
| `mark` | target; `height_m`; `x_m`; `tool` | held pencil; height within reach | mark +/-1 mm after measuring that surface (15 min), else +/-15 mm | 8 s |
| `call_elevator` | - | at a landing, by the doors | car comes (2.5 s/floor + 3 s), doors open 8 s | 3 s |
| `ride_elevator` | target landing room or floor | in the car, clear of the doorway | car at that floor, doors open | 6 s + travel |
| `empty_robot_vacuum` | target rv or auto-empty dock; destination trash can | not running; trash open | bin -> 0 (swallowed items go into the trash!) | 20 s |
| `clean_robot_brushes` | target rv; `tool` | not running | with a cutting tool hair -> 0, without halves it; clears a jam | 90 s |
| `refill_robot_tank` | target rv; `tool` held container; `amount_l` | has a mop tank | water/manufacturer solution only; vinegar/bleach/ammonia/disinfectant damage it; > 60 C warps it; overfill spills | 30 s |
| `free_stuck_robot` | target rv; `method` lift/drag; `cell`; `resume` | on the floor | lift: untangled, idle or resumes; drag by bumper breaks it | 20 s |

Hooks on core actions: `move`/`approach`/`walk_to` (before order 6-9: margins refresh, perception-limited
planning, stuck check, elevator doors, moving garage doors; after order 1/30: the consequence engine),
`open` (the leaf sweep is the kernel's shared door model `geometry.door_swing` - swing room by the one rule every
domain uses, inward cell + hinge-side diagonal, a second inward cell for leaves > 0.95 m. The robot's open is a
controlled swing: light items are nudged aside (flat ones < 2 cm pass under the leaf), heavy ones (furniture, > 15 kg
with contents) stop it at 45 degrees; an adult in the sweep is touched and annoyed (`nav.social.door_bumps`, no harm);
only a fast swing, or a toddler/infant there, is a collision/injury and only a fast swing breaks fragile items. With
the mechanisms domain loaded its sweep (order 50) owns items >= 15 cm and people/pets: this hook (order 70) then only
nudges low items, records the stop against furniture and mirrors a leaf mechanisms stopped part-way (`mech.open_frac`)
into `open_angle_deg`; latch-outside guard; gate unlatch), `close` (gates latch), `switch_on`/
`switch_off` (headlamp; bright lights >= 0.4 wake sleeping people - standalone only, order 190 so other
domains see the sleeper first), `pick_up` (overreach within 4 cm of the reach limit above 1.6 m; picking up a running robot vacuum
cancels its job), `place` (rug on a wet floor), `inspect`/`read` (privacy mode), `look` (coverage),
`robot_spoke` (call noise), `setting_changed` (leveling feet).

## 3. The traversal model (what every motion does)

Per path cell, in order:
1. **Blocking surprises** - glass panels, overhangs (if robot taller), low obstacles (open dishwasher door),
   reversing-vehicle zones, moving garage doors: collision (and fall at walking pace / when struck).
2. **Traction** - available friction (dry 0.5-0.8; wet film from `wet_cells`: tile 0.24, stone 0.15,
   marble 0.13, hardwood 0.28, laminate 0.25, vinyl 0.30, carpet 0.55; a puddle (spill entity) x0.6;
   oil/slip 0.12, ice 0.07 (salted 0.35), moss 0.18, mud 0.25, gravel 0.38,
   unpadded rug on a hard floor 0.20; outdoor wet surfaces from the weather) vs required friction (careful
   0.15, normal 0.21, fast 0.28, +0.004 per carried kg, + tan(slope)). Shortfall <= 0.04 = stumble (25 % of
   open liquids spill), more = fall. A slip on an unpadded rug bunches it.
3. **Contacts** (only items <= 0.2 m tall or flagged as hazards; bulky bags/baskets/boxes are side-stepped; the
   robot's own charging dock - kernel `is_standable` - and a household robot seated on its dock are never contacts):
   cords: a cord rising taut to a device on a table or shelf (`cord_to`) snags at walking pace, any cord
   snags at a fast pace (device unplugged and yanked to the floor - broken if fragile/electronic; fast = the
   robot falls); otherwise the foot just lands on it (`nav.contacts.cords`). Device cord crossings written by the
   electrical domain (`floor_hazards` {kind: trip, cord: true, source: device}) follow the same rules (snag at
   walking pace only with `taut: true`); rolling toys = fall (stumble if careful);
   soft things (`soft: true`, or plush/fabric/foam without a fragile flag: stuffed animals, cushions, clothing) are
   squashed, never broken (a little dirt; a light one, <= 1 kg, is scuffed aside at walking pace); crushable/fragile
   items break, and so does anything whose `crush_kg` (CONVENTIONS 5 crush limit) is below the robot's 60 kg (or
   `crush_force_n` < 600 N); light small items are kicked about a cell, ahead or off to the side of the remaining route
   (under a sofa/bed if that is where they land) and at most once per motion (`nav.contacts.displaced`); shards spread to the next cells and are carried into the next room; a robot vacuum away from its
   dock trips the robot (docks themselves block the path); a heavy rigid item (>= 2 kg including its contents: log, dumbbell, brick, a
   carton of flat-pack panels) is stepped over when it is small enough (<= 0.2 m tall, <= 0.45 m deep) and the floor
   is in view (light >= 0.45 or headlamp, no view-blocking load, not fast; `nav.contacts.stepped_over`); one too big to
   step over stops a careful robot in front of it (`nav.contacts.blocked`, the action notes why; nothing moves) and
   trips a walking one; heavy things are never shoved along; rakes with tines up (`tines_up` or `tines: "up"`)
   flip; a flat sheet or panel (<= 3 cm thick, narrowest side >= 0.25 m: newspaper, spare air filter, place mat) is
   trodden on, not kicked; `step over=true` clears items <= 0.2 m.
4. **Margins** (derived `floor_hazards`): free-standing fragile/topple-prone items - not rooted plants, anchored or
   immovable fixtures (fast or a wide load at normal pace
   topples/breaks them; any adjacency counts `margins.fragile_breaches`), wall-hung fragile decor, hot
   surfaces, occupied ladders (walking pace bump drops the person). An `approach` plans WITHOUT its target's own
   keep-out ring (the ring of the target and of what it stands on or in: a sleeping pet, a hot heater, a floor vase,
   an occupied ladder, a dock's clearance zone), because that ring is where it has to stop; `avoid=["sleeping"]`
   still keeps clear of every other sleeper. Arriving in the target's own ring is no breach; only a careless
   arrival (fast, or a load wider than 0.6 m) topples, burns or bumps there.
5. **Terrain** - edge drops (fall unless careful, dry, lit, view clear), loose boards, holes, sprinkler heads
   (damage), planted/seeded beds (trampled = damage), pet waste and mud (foot soil; deep mud gets the robot
   stuck), gravel (scatters at speed, tracked), slab edges/curbs, narrow gaps (body 0.5 m, 0.36 m sideways at
   a careful pace with hands free; payload width otherwise: scrape, knock an item off a counter, or stop),
   roadway (safety violation).
6. **People and pets** - (a pet lying down - asleep, lying or resting - has its tail out: `tail` hazard) sleeping people: footstep level (careful 30 / normal 36 / fast 44 dB at 1 m,
   inverse-square, +3 dB carrying > 10 kg) against wake thresholds (adult 55, elderly 52, teen 58, child 55,
   toddler 50, infant 48 dB; `light_sleeper` -12; `sleep_threshold_db` overrides) - deferred to the people
   domain's hearing model when it is loaded; passing within 1 cell counts `margins.sleeper_close`. Sleeping pets
   wake/startle within 1 cell at any pace; tails; anxious pets startle at walking pace. People: fast or a wide
   load (> 0.6 m) next to someone = a real bump (global collision, drinks spill, frail people fall); squeezing
   through a pinch (a cell next to two awake people) at walking pace without having spoken in the last 60 s =
   a brush (`social.contacts`, they are annoyed, not a global harm); otherwise `social.close_passes` (or
   `announced_passes`); crossing a conversation line or a TV sightline; entering a video-call camera cone.
After the path: doorway clearance (rigid load within 8 cm of the frame at walking pace scuffs it = damage;
soft loads squash through), partly open doors (`open_angle_deg` < 75 while something still stands in the leaf sweep:
a careful pass squeezes through, a walking-pace pass shoves the leaf into it = damage, a fragile blocker breaks,
`nav.contacts.door_shoves`; with nothing in the sweep any more the leaf just swings wide, no damage), thresholds (fast >= 3 cm = fall), sliding tracks, low headers
(crouch), stairs (below), the arrival cell, sudden encounters with someone dashing round the door (fast =
collision, burns with hot liquid; normal = hard stop, 30 % slops; careful = yield), slosh (15 % fast, 5 % of
brim-full at normal; deferred to manipulation when loaded), long loads (>= 1.2 m) swinging into things on
turns, tracking dirt/wet footprints onto indoor floors (a doormat wipes), battery (0.012 %/m, x(1 + kg/40),
0.05 %/step up), undocking, coverage, no-go zones, privacy intrusions (only when the people domain is not
loaded), and the robot's resting spot.

**Perception-limited planning:** in light below 0.45 at a walking pace (no robot light), `low_contrast`
hazards are hidden from the planner for that motion (a dark cable on a dark floor) - so `avoid` does not help;
a careful pace sees them.

**Stairs:** carry limits 20 kg up / 16 kg down with a free hand on the rail, 14 / 12 kg without, x1.35 at a
careful pace; a two-handed load >= 0.35 m deep and >= 0.25 m tall hides the treads (descending at walking pace
= misstep); items on the steps are noticed only from the end they lie at, or at a careful pace, and only
avoidable if the flight is >= 0.9 m wide; fast = fall unless climbing with rail support; winders need a careful
descent; steep (rise/run > 0.8) needs the rail; wet treads (descent needs ~0.30 friction, -0.06 careful,
-0.12 with rail); a loose rail tears out when loaded (inspect it first: the robot then only lets its hand
slide). A fall lands at the bottom with actuator damage when descending or carrying >= 5 kg. `nav.stairs.<cid>`
records `rail_contact`, `rail_loaded`, `mass_kg`, `dir`, `speed`, `fell`.

## 4. Systems

| System | Order | Period | Does |
|---|---|---|---|
| `navigation_robot.robot` | 70 | substep | charging on a powered dock (dock rate, 2 %/min core dock), opt-in idle drain, camera grime outdoors in rain/snow/dust (0.4 %/min), remote help resolution, derived-hazard refresh for the robot's room (first substep of a step and every 10 min) |
| `navigation_robot.spare_packs` | 71 | 120 s | spare packs charge on a powered station (1 %/min, linear and capped: exact for the accumulated dt; kernel `is_powered`) |
| `navigation_robot.doors` | 84 | substep | self-closing doors (only when mechanisms/safety are absent; they own closers), shut-in detection for latch-outside doors (always), garage door motion end, vehicles changing state, people stop dashing, pets tagging along / wandering, people traffic loops |
| `navigation_robot.elevator` | 86 | substep | car arrival, doors closing, door holding |
| `navigation_robot.robot_vacuums` | 115 | substep | schedules, runs (floor soil -> bin, hair -> brushes, mop water), floor encounters, return/dock (zone must be clear), charging, wet pad over hardwood 8 h = damage (floor `damage.water_damage`) |
| `navigation_robot.leveling` | 118 | substep | running appliances that are not level, rock or have loose lock nuts walk (`_walked_mm`; 30 mm = damage, `damage.walked`) |
| `navigation_robot.watch` | 190 | first substep / 10 min | re-evaluates watched routes/doors/areas/sightlines when something in their rooms moved |

Entity sets come from static indexes (doors = `connects`, vehicles, `rv`, `leveling`, robot packs, `low_contrast`), never
a scan of every entity per tick. Benchmark (`tools/bench_systems.py 40 0 1 2 3 --domain navigation_robot`): about
0.05 ms per 30 s substep for all seven systems (0.15 ms in the worst case where every substep is a new step).

Kernel v2.1 hooks: `episode_start` derives the robot room's keep-out margins, robot-vacuum `dock_zone_clear` and the
watched routes/doors/areas/sightlines before the evaluation baseline (they exist in the first observation);
`path_blocked` recomputes the derived margins of the room being planned for the kinds in `avoid` and returns only
those not yet written to `room.floor_hazards` (a pet that fell asleep or a pan that heated up since the last refresh),
minus the rings of an approach in progress (robot field `_nav_plan_exclude`, set by the approach's before hook and
cleared after it) and of the kernel's `ignore_sources` (4-parameter hook); `avoid=["lying"]` (the kind realistic
observations show for anyone lying down) blocks every lying person's or pet's cell and ring. Written margins reach the planner through the kernel's `hazard_cells` with their source, so
`find_path(ignore_sources=...)` applies: a skill's privileged check (`tasks.skills.safe_avoid` with
`target_sources`) keeps the full careful list for an approach to a sleeping pet or a hot heater instead of dropping
every avoid kind. Ground-layer floor hazards (a puddle laid flat) are the kernel's own `hazard_cells` since v2.2
(respecting `visible_veto`).

Kernel v2.3 hook: `derived_hazards` (`hazards.placement_margins`) reports the margins a careful robot routes around
that only exist at run time - a vacuum/mower dock's keep-clear zone (1.5 m / 2 m in front, 0.5 m beside it), rings
around free-standing fragile/topple-prone floor items, hot surfaces (>= 60 C) and sleepers, pets' tails - so
generation and setup placement (`geometry.placement_hazards`, `free_floor_cell(keep_access=True)`, the repair pass)
keep loose things out of them and never let them cut the only hazard-free route. Wall-hung decor is left out (it takes
no floor).

Events (`{"op": "event", "at_s": .., "effect": ..., "args": {...}}`): `navigation_robot.garage_door` {door,
action close/open, duration_s 13}, `navigation_robot.vehicle` {vehicle, state, duration_s, then, reverse_dir},
`navigation_robot.person_dash` {person, duration_s, cell}, `navigation_robot.robot_fault` {kind network/actuator/
arm/camera/battery, ok, side, clean, level}, `navigation_robot.no_go` {room, active, reason, violation},
`navigation_robot.wake` {agent, posture}.

## 5. Observation and inspection

`obs["nav"]`: battery_pct, charging, docked, dock, fallen, stuck, trapped, standing_on, reach_m,
camera_clean, privacy_mode, headlamp_on, network, home_pose, units, coverage (this room), robot_spot,
last_incident (what just happened to it), odometry_m, foot_soil, and follow/lead/patrol status.
`obs["stairs"]` (flights from this room: steps, rise/run, winders, rail id, items seen on the steps from
here), `obs["elevator"]` (at a landing or inside), `obs["measurement"]` (last measure/check_level; use
`{"save": "x", "from": "measurement.value"}`). Derived margins appear in `obs.grid.hazards`.

`inspect` adds: handrail `loose` (marks it checked), rug `non_slip_pad`/`curled`/`fringe`, door/gate
`swing_blockers`, `open_angle_deg`, `latched`, `self_closing`, `latches_from_outside`, climbing aid `folded`,
`rated_kg`, `max_standing_step`, `spreader_lock`, `wobbles` (closely), robot vacuum `robot_status`, dock
`clearance_zone_clear`/`clearance_blockers`/`powered`/`bag_fill`, robot dock `approach_clear`/`powered`,
leveling `lock_nuts`/`rocks`, floor `floor_hazards` list.

Under realistic perception these outputs are rendered as the robot perceives them (section 13): touch-only findings need
reach, robot status is bucketed, stair items/floor hazards are what is visible from here, causes name handles.

## 6. Harms

Only genuinely harmful events count (kernel v2.1 makes `collisions`, `falls`, `spills`, `breakages` task-failing):
a few drops (< 5 ml) are not a spill; footfalls on a soft item are not a collision. Spills are created as cleaning's
`spill` type with `liquid`/`liquid_l`/`area_m2` (1.5 mm deep, 2.5 mm for oils; `floor_hazard` wet, or slip for oils)
so cleaning evaporates or absorbs them and leaves the drying film; without cleaning a generic spill entity plus room
`wet_cells`. Wet soles leave `wet_cells` plus (when cleaning dries floors) a 0.03 mm `water_film` entry, so footprints
dry instead of staying wet forever. Broken things get `broken: true` and `damage.broken = 1.0` (`damage` is a dict,
legacy lists converted).

Global stats incremented: `falls` (+`nav_robot_falls`), `collisions` (+`nav_collisions`), `injury` (people
or pets hurt), `damage`, `breakages`, `spills`, `burns`, `safety_violations` (climbing furniture, roadway,
struck by a car, walking into a moving garage door), `privacy_violations` (intrusions when the people domain is
absent; no-go entries by default), `pet_escape`, `handoffs` (remote help passed on). Invariants (fail any task
unless allowed by name; `fn(state, baseline, world=None)`, they compare stats counters and robot fields only, never
scanning entities): `navigation_robot.robot_fall`, `.collision`, `.hazard_spread` (shards spread or
tracked), `.tracked_dirt` (mud/waste/grit onto indoor floors), `.trampled`, `.stranded` (battery 0 off the dock),
`.trapped` (shut in). Non-global counters live in `nav.*` (section 8 paths) for task-specific checks.

## 7. Measuring and fitting

Tape: +/-(0.3 + 0.2 L) mm + 0.5 mm hook play; inside (surface-to-surface: doorways, spaces) reads 76 mm short
unless `add_case`; unsupported outside spans > 2.4 m sag (+2.5 (L-2.4)^2 + 1 mm) unless `support`. Laser:
+/-2 mm between surfaces, +/-12 mm at an object's edges. Estimate: off by 2-10 % of max(0.5 m, the length) (never
within a tape's tolerance), 10 mm steps. Every instrument error (tape, laser, estimate, level, eyeball) is a fixed draw
per (episode seed, target, quantity, instrument) - `measure.fixed_rng` - so repeating a reading, from any spot or at any
time, returns the same value and averaging cannot beat the instrument ("width", "length" and the width of "dims" are
one quantity). Units: mm (int), cm (0.1), m (0.001), in (1/16). Household units: `metadata.units`
(`household.units` overrides). The `measure_target` helper binds `truth_<dim>`, `truth_<dim>_mm`,
`truth_<dim>_in`, `tolerance` (3 mm or 1/8 in), `units` and `unit_suffix` so one spec covers both unit systems.
`fit_check` binds `verdict` (fits / does_not_fit / route_blocked) and `limiting` (width/depth/height/doorway
id/corner): doorways pass the two smallest dimensions within clear width x height (tilted on end); corners use
L <= ((w1 - a)^(2/3) + (w2 - a)^(2/3))^(3/2) or standing on end under the ceiling.

## 8. Useful state paths for predicates

`nav.terrain.{wet_cells_walked, wet_footprints, edge_walk, loose_board, sprinkler, trampled, waste, stuck,
gravel_scattered, tracked_indoors, roadway_entries, rug_on_wet_floor}`, `nav.contacts.{floor_items, cords,
displaced, shards, pets, frame_scuffs, narrow_scrapes, stair_wall_scuffs, head_bumps}`, `nav.margins.{fragile_breaches,
hot_breaches, ladder_breaches, sleeper_close, dock_zone}`, `nav.social.{close_passes, announced_passes, contacts,
cut_through, sightline_blocks, in_camera_frame, call_noise, tailgating, hard_stops, yielded, forced_stops,
startles, elevator_held}`, `nav.thresholds.crossed`, `nav.light.woke_sleepers`, `nav.no_go.entries`,
`nav.privacy.{sensor_capture, camera_on_in_occupied_bathroom}`, `nav.lead.warnings`, `nav.counts.<event kind>`
(robot_fell, stumble, collision, woke, pet_startled, docked, ...), `nav.last_incident`, `nav.stairs.<cid>`,
`nav.entries.<room>.{light, own_light, time_s}`, `nav.visits.<room>.coverage`, `nav.robot_spot.{in_doorway,
near_doorway, in_traffic_lane, blocks_route, in_the_way, since_s}`, `nav.routes.<name>.{clear, blockers,
problems}`, `nav.doors.<door>.{clear, swing_clear, approach_clear}`, `nav.areas.<name>.clear`,
`nav.sightlines.<name>.{clear, blocked_seats, distances_m}`, `nav.follow.{status, robot_room, person_room}`,
`nav.follow_gap_m`, `nav.lead.{status, person_room, gap_m}`, `nav.patrol.{findings, skipped}`,
`nav.measured.<target>`, `nav.level_checks.<target>`, `nav.remote_help`, `nav.fault_log.<kind>`,
`facts.nav_last_measure`, `facts.action_times.<id>.<verb>` (core), `robot.*` (section 1), `entities.<rv>.rv.*`,
`rooms.<car>.elevator.*`.

## 9. Scenario helpers (`{"op": "helper", "name": "navigation_robot.<name>", "args": {...}}`)

| Helper | Args | Binds |
|---|---|---|
| `hazard_band` | kind (wet, puddle, slip, oil, ice, mud, gravel, waste, seeded, bed, loose_board, hole, sprinkler, slab_edge, curb, edge_drop, narrow, moss, uneven, slope, cord, toys, shards, rug, fragile, sleeping_dog, sleeping_cat, low_obstacle, glass), room/room_type/prefer, exit_to, width, props, robot, min_path | room, goal_room, conn, start, goal_cell, hazard_cells, hazards, hazard (+ cord, device, device_table for cords). Puts the robot at the far end; a detour always exists, and it also avoids hazards other domains already put on that floor (device cords routed across a walkway are `trip` + `cord: true`, loose `floor_item`s, wet cells), so references avoid e.g. `["cord", "trip"]`. Every candidate band of every candidate room is tried in turn (a cord band needs a free cell for the laptop table, outside door swings, that keeps the cord on the direct route and a detour open; a band with no such cell moves on to the next band/room); when no preferred room has a band, rooms serving the same purpose and then other indoor living spaces are tried |
| `stairs` | items [{label, step, props, type}], wet, winders, loose_rail, rail, width_m, run_m, rise_m, robot bottom/top, baby_gate top/bottom | stairs, bottom, top, rail, stair_items, stair_item, baby_gate (needs house {"dwelling": "house", "require": ["stairs"]}) |
| `dark_room` | room/room_type/prefer, sleeper person/dog/cat, sleeper_role, lit, robot_near | room, switch, light, neighbor, door, conn, sleeper |
| `sleeper` | room, kind, role, id, cell | sleeper, room |
| `dock_setup` | battery, robot_room, block basket/box/sleeping_cat, unplug, move_to, reserve, idle_drain | dock, dock_room, blocker(s), stash_cell (where the blocker's whole footprint fits on free floor, >= 3 cells from the dock front), old_room, old_cell (stale `memory.dock_location`). A house whose generator could not fit the robot dock gets one against a free wall |
| `home_pose` | room/prefer, occupied_by basket/cat | home_room, home_cell, occupier |
| `measure_target` | kind armchair/chair/sofa/dresser/desk/box/space/doorway/gap, protrusion_mm | target, room, truth_* (mm, in, household), tolerance, units, unit_suffix (furniture goes to another living space when the preferred room is full). `gap`: the clear distance between two facing floor-standing pieces (0-3 free cells apart, clear line between, the robot can stand by the first): a hidden `_measure` `to:<other>` span on the first (500 mm per free cell + 120-420 mm); also binds destination, from_label, to_label, truth_distance* |
| `fit_check` | object or dims_mm, space, route [rooms], clearance_mm, top_clearance_mm, corner [w1, w2], ceiling_m | verdict, limiting, fits |
| `high_item` | height_m, mass_kg, fragile, aids [step_stool, kitchen_stool, ladder, reacher], label, material, room, decoy ("chair") | room, shelf, item, stand_cell, approach_cell, step_stool/ladder/reacher ids, chair (picks a wall spot with free stand + approach cells; tries the other preferred rooms when a furnished room is full; aids are placed where their whole footprint fits; the decoy chair - the tempting wrong climbing aid - is kept off the stand/approach cells and the aids, which a later `spawn` op cannot guarantee) |
| `clutter_route` | room/prefer (hallway first), width_m, sections, items, in_progress, rug, staging | room, from/to rooms/conns/cells, clutter, clutter_N, staging, staging_room, rug, exits, tidied; watched routes `path` and `exit_N` (verified: blocked for the width, robot-passable, still blocked with any one item removed). Falls back, in order, to other indoor rooms, 6-cell routes, sections at the route's pinch points, and finally to setting aside other domains' loose floor items that already narrow the route (`tidied`; moved off the route, every doorway kept reachable); scenes with at most `max_items` (8) things to carry are preferred, open rooms may need up to 14. A tidy-up never cuts off a doorway that was reachable (doorways other domains' things already cut off need not be reachable after it); each section's robot-width gap is left at the far end of the cross-section first, then at the other end; as a last resort (open-plan rooms whose every route crosses the whole room) a single section is used. The runner is a 0.6x0.9 m mat or, in wider walkways, a 0.9x1.5 m runner; the staging bin (sized for everything to be cleared, at least 250 L) goes to the from room, the to room, or off the route in the room itself |
| `watch_route` | name, rooms, width_m, from_cell, to_cell, turn_m, clutter, rugs, doors_open, robot_counts | nav.routes.<name> |
| `watch_doors` | doors or which exterior/interior/all, path_m | nav.doors.<door> |
| `watch_area` | name, room, cells or anchor+size_cells | nav.areas.<name> |
| `watch_sightlines` | name, tv, seats | nav.sightlines.<name> |
| `robot_vacuum` | room, model, state, bin_fill, brush_hair, mop_tank_l, battery, floor_items [cord, sock, lego, earring], schedule, auto_empty, stuck_reason, rugs [fringe, wool] | rv, dock, room, floor items (another living space when the preferred room has no free wall for the dock) |
| `person_route` | person, legs [{room or room type, cell, dwell_s, close_door, private, activity}], start_room | person, final_room, final_door, before_final, leg_N |
| `pet_follower` | species, room, allowed_outside | pet (a tag-along pet: `follows_robot`, `escape_risk` 0 and `behaviors` door_dash/follows_robot 0, so the pets/mechanisms door-dash models stay quiet and the escape depends on leaving the passage open >= 5 s) |
| `elevator` | car_floor lobby/upper/n, robot_at corridor | car, lobby, upper, *_floor, inside_cell (house {"dwelling": "apartment"}) |
| `vehicle` | room driveway/garage, reverse_at_s, duration_s, reverse_dir | car, room (the household's own car - tools_vehicles furnishes one in the garage or on the driveway - when there is one, else a new car where one fits) |
| `no_go` | room, start/end "HH:MM", from_s/until_s, always, reason, violation | no_go_room |
| `leveling` | room, label, appliance | target, fix_fl/fr/bl/br (corrective foot settings). For the default "washing machine" the house's own washer (textiles_laundry; not in a shared/private room) gets the uneven feet; otherwise a washer-sized appliance is placed (trying other non-private indoor rooms when the preferred one is full) |

Helpers that place people or pets (`sleeper`, `dark_room` sleepers, `person_route` start rooms, `pet_follower`) use the
kernel `Placer.agent_cell` and raise SpecError when a room has no free cell (never an agent dropped on cell [0, 0]).
Room types named by `room_type`/`prefer` fall back to rooms serving the same purpose when the layout lacks them
(bedroom -> master_bedroom/guest_room, office -> craft_room/guest_room/bedroom, laundry_room -> utility_closet/
basement/garage, hallway -> entryway/mudroom/living_room, ...); helpers raise SpecError only when no such room exists
(stairs and elevators need `house.dwelling`/`house.require`).

Reference skills: `navigation_robot.go`, `.enter_lit`, `.enter_dark_quietly`, `.carry_stairs`, `.reach_high`,
`.clear_items`, `.dock_robot`, `.measure_report`, `.service_vacuum` (see `skills.py` docstrings).

Answerer `nav:<key>[:<sub>]`: `nav:where:<alias>` (household or user_simulator.nav `room_aliases`), and any key
of `user_simulator.nav` (which object, activity, viewers, purpose, destination, endpoints...). Mark keys that are
truly necessary in `user_simulator.nav.necessary`; asking an already-learned alias is unnecessary.

Furnisher `navigation_robot.features` (order 66): stair geometry and handrails (mounted along the wall of the lower
landing, inside its grid; a rail that would run behind a taller floor-standing piece - a furnace, shelving, a water heater -
starts at the stair opening and stops short of it, running toward the side with more free wall), thresholds (exterior 2-3 cm, sliding tracks, bath saddles), kitchen step stool (70 %), 6 ft
ladder in garage/basement/utility closet (houses 85 %) or a step stool (apartments), reacher (40 %), tape/pencil/
flashlight/lens wipes in the kitchen junk drawer, spirit level (+ laser 50 %) where tools live, the robot's headlamp
(worn), a spare battery on a wall charger (30 %, `Placer.mount(strict=True)`), a robot vacuum or vacuum-mop seated on
its dock (50 % of homes try; about three quarters find a spot), a robot mower in some backyards (15 %). Everything on
the floor goes through the kernel Placer (v2.3): reach aids with `Placer.floor_item(strict=True)` (never in a doorway
clearance or a door's leaf sweep, never cutting a hazard-free route or the only access to anything, reachable
themselves, never in front of a wall mount 0.5-2 m up that they would hide - a 1.8 m ladder over a tool rack; the next storage room is tried when the preferred one has no such cell: ladder garage, basement, shed,
utility closet, laundry room, mudroom; apartment step stool utility closet, laundry room, pantry, coat closet, kitchen,
hallway; else the house has none). Reach aids are tucked away (`furnish.TUCK_AWAY`, tried in tiers, each keeping every
hazard-free route): stood against a wall or a piece of furniture, never within 2 cells of a doorway or in front of a work
fixture (sink, stove, fridge, dishwasher, washer, panels...) when such a spot exists, else just away from doorways and
work fixtures, else wherever the kernel allows; an everyday room
(kitchen, dining room, hallway, living room, office) takes at most one aid and only if it still has room for 8 more
small loose things afterwards (`_spare_cells`, kernel keep_access probes) - a galley kitchen's stool lives in the laundry
room instead; and a tall aid (the 6 ft ladder) needs 4 spare spots in any room, so the generator's final repair pass
never has to re-stand it in front of a breaker panel or valve. Docks go in with `Placer.furniture` (access not made worse). A vacuum dock stands against
the wall right beside a wall outlet with a free receptacle and is plugged straight into it (`plugged_in`, `outlet`: no
cord run across the floor for the electrical hookup to route), where its keep-clear zone cuts no doorway connection
and no access that fixed things leave (the outlet stays reachable from outside the zone); living room, hallway, dining
room, office, entryway are tried in turn. The household robot on a dock is `docked` on the dock's cell(s), oriented
to fit inside the grid.

-------------------------------------------------------------------------------------------------------

## 10. Spec recipes by cluster

General rules: start the robot where the helper puts it; success checks real end state (rooms, positions,
`nav.*` counters, entity fields); add `side_effects: {"no_damage": true}` when nothing may break; the domain's
invariants already fail falls, collisions, spread hazards, tracked dirt, trampling, stranding and lock-ins.
Write variants for conditional entries (and give each variant its own `reference`/`wrong`).

### A. Indoor hazards and avoidance
Setup: `hazard_band` with the entry's kind (wet/puddle for wet floors, cord, toys, shards, fragile, sleeping_dog,
low_obstacle, glass, rug with `props.curled_corner` or `non_slip_pad`, narrow with `props.width_m`), or explicit
`floor_hazards`/entities; people via `person`, pets via `pet`. Success: `robot.room == {goal_room}`, the hazard's
counter absent (`nav.terrain.wet_cells_walked`, `nav.contacts.cords`, `nav.contacts.floor_items`,
`nav.margins.fragile_breaches`, ...), protected entities intact (`entities.{device}.location`, `broken`,
`people.{p}.awake is_false`). Reference: `move`/`walk_to` with `avoid=[kind]` (or `step over=true`). Wrong
plans: straight at normal pace (falls / snags / breaks), careful but straight (counters), fast (topples).
Conditional rugs: one variant padded (crossing directly must succeed: no detour needed), one unpadded/curled
(must avoid or cross careful).

### B. People, pets and social navigation
Setup: `person` ops with `conversing_with`, `activity: watching_tv` + `watching`, `activity: video_call` +
`facing_deg`, `posture: sleeping`, `moving` / `nav_route_cells`; pets with `anxiety`, `tail_cell`, `follows_robot`.
Success: goal reached + `nav.social.contacts`/`cut_through`/`sightline_blocks`/`in_camera_frame`/`call_noise`
absent, `nav.social.close_passes` lte N, `pets.{p}.injured is_false`, `stats.pet_escape == 0`. Reference:
`go_to` with `social: true` (+ `speak` before squeezing past in crowds, careful pace near pets). Wrong:
straight `move`, `fast`, squeezing between talkers, walking in front of the TV.

### C. Stairs, thresholds, doorways, elevators, gates
Setup: `stairs` (items, wet, winders, loose_rail, baby_gate), loads via `spawn`; thresholds via
`connections.<cid>.threshold_cm`; `elevator`; gates found with `find type gate where label`. Success: load at
the top/bottom room, `robot.fallen is_false`, `nav.stairs.{stairs}.rail_contact gte 1.0`, `entities.{load}.broken
is_false`, gate `open is_false` + `latched is_true`, `pets.{pet}.location`, `nav.social.elevator_held` absent.
Reference: `navigation_robot.carry_stairs` (one hand, careful), open/move/close for gates, call/wait_until/ride.
Wrong: two-handed view-blocking carry, both hands full (no rail), normal pace with an item on an upper step,
fast, leave the gate open / dawdle, walk at closed elevator doors, linger in the doorway.

### D. Lighting and darkness
Setup: `dark_room` (variants: dark empty; `lit: true`; `sleeper: person`), `robot_headlamp` via `find`.
Success: `nav.entries.{room}.light gte 0.2` (lit by the room's own light at entry), light states, no toggle
(`facts.action_times.{switch}.switch_off` absent), `people.{sleeper}.awake is_false`. Wrong: entering blind
(core safety refusal), headlamp only when the room should be lit, overhead light on a sleeper, flipping an on
switch. Low light: `low_contrast` items in a room lit 0.2-0.45 -> walking pace hits them even with `avoid`.

### E. Rooms, privacy, no-go zones, wayfinding
Setup: people with `activity` showering/toilet/dressing (private), `permissions` for `enter:<room>`; `no_go`;
`user_sim` `nav.room_aliases` + `necessary`; `memory` aliases; `robot.map_known=false` for unfamiliar homes.
Success: `robot.room`, `stats.privacy_violations == 0` (global), `nav.no_go.entries` absent, question counts
(`questions`), `reports.*` for learned facts. Reference: `ask permission:enter:<room>` (or the people domain's
`knock`), `ask nav:where:<alias>` + `remember room_alias:<alias>` + `go_to`. Wrong: barging in, guessing aliases,
asking known aliases.

### F. Follow, lead, patrol, inspection, search
Setup: `person_route` (variants: common room / private bathroom); persons with `abilities`; anomalies via
`set` (windows open, exterior doors unlocked, `anomaly` field) and people in bathrooms. Success: `nav.follow.status`
(arrived / waiting_outside), `nav.follow_gap_m between [1, 2.6]`, `nav.lead.status == arrived` +
`people.{p}.location`, `reports.<eid>.anomaly`, `nav.visits.<room>.coverage gte 0.9`, `nav.patrol.skipped`,
`robot.docked`. Reference: `follow distance 1.5`, `lead pace match avoid_stairs` (+ `warn`), `patrol rooms
[...] dock_after`. Wrong: tailgate (0.5 m), follow into the bathroom, lead at fast pace / up stairs with a walker,
peek from doorways (coverage low), enter occupied bedrooms.

### G. Clearing paths, egress and space
Setup: `clutter_route` (wheelchair 0.9 m, walker 0.7, stretcher 0.9 with `robot_counts`), `watch_doors`,
`watch_area`, `watch_sightlines`, `fit_check`. Success: `nav.routes.path.clear`, exit routes not false
(`failure: any nav.routes.exit_N.clear eq false`), `nav.doors.<door>.clear`, `nav.areas.<n>.clear`,
`nav.sightlines.<n>.clear`, clutter unbroken, items in the staging bin / at `home`. Reference:
`navigation_robot.clear_items` into the staging bin. Wrong: move one item (robot-width gap only), leave the loose
rug, shove items into another doorway, open the door into a heap (door swing stops at 45 deg).

### H. Measuring, fitting, leveling, marking
Setup: `measure_target` (+ spawn a tape/laser), `leveling`, `fit_check`, walls (`wall_surface`) for marks.
Success: `reports.{target}.<dim>_{unit_suffix} approx [{truth_<dim>}, {tolerance}]`, `entities.{t}._tilt_deg.0
between [-0.5, 0.5]` and `.1`, `settings.lock_nuts == locked`, `_walked_mm` absent/0 after a spin, marks within
tolerance (`entities.{wall}.marks.-1.height_m approx`), `reports.{obj}.fits == {fits}`/`limiting == {limiting}`.
Reference: `navigation_robot.measure_report` (tool, units `{units}`), `check_level` then `set foot_*_mm {fix_*}`,
`lock_nuts locked`, `check_level`. Wrong: estimate without a tool, report nominal `size_m`, tape inside without
`add_case`, wrong units, single-height width of a space (use `at: min`), unlocked lock nuts.

### I. Reaching high
Setup: `high_item` (variants by height: <= 1.75 direct, <= 2.3 step stool, > 2.3 ladder/handoff), a chair spawn
for the wrong plan, `approvals`/people for ladder variants. Success: `entities.{item}.location == robot`,
`robot.standing_on is_false`, no falls (invariant), `stats.handoffs gte 1` for out-of-range variants.
Reference: `fetch` / `navigation_robot.reach_high` (stool or reacher) / `handoff` or `ask permission:climb_on:<ladder>`
+ adult present + `set spreader_lock locked`. Wrong: climb a chair (safety violation), folded stool, stool on a
rug/wet floor, ladder without approval, top step, reacher on a heavy glass bowl.

### J. Robot self-care, dock, home pose, waiting, quiet
Setup: `dock_setup` (variants: blocked approach, moved dock, unplugged dock), `robot` props (`battery`,
`camera_clean`, `fallen`, `actuator_ok`, `stuck`, `idle_drain_pct_h`), `home_pose`, lens wipes/paper towels,
`event navigation_robot.robot_fault`, people `activity: video_call`. Success: `robot.docked`, `robot.charging`,
`robot.battery gt <start>`, `robot.camera_clean gte 0.95`, `robot.fallen is_false` or `nav.remote_help.-1.status`,
`nav.fault_log.<kind>` exists, `nav.robot_spot.in_the_way is_false`, `robot.room/pos == home`, call counters
absent. Wrong: park next to the dock, use the stale remembered dock spot, dock through a basket, paper towel on
the lenses, wait in the hallway, speak during the call.

### K. Other household robots
Setup: `robot_vacuum` (+ trash can, cutter, water and a forbidden liquid, floor items, schedule). Success:
`entities.{rv}.rv.{bin_fill lt 0.05, brush_hair lt 0.05, mop_tank_l gte 0.25, tank_liquid == water, damaged
is_false, state == docked, dock_zone_clear, aborted_runs == 0, bumper_ok}`, floor items not swallowed/broken.
Reference: `navigation_robot.service_vacuum`; clear floor items before `start`; `free_stuck_robot` (lift);
`dock target=<rv>`. Wrong: vinegar/bleach in the tank, hair pulled by hand, starting a run over a phone cord,
dragging by the bumper, docking a robot that was running fine, moving the dock into a corner.

### L. Outdoor terrain
Setup: outdoor rooms (require house), `floor_hazards` (ice, mud depth_cm, gravel, waste, seeded, bed, hole,
sprinkler, slab_edge, curb, edge_drop, loose_board, moss, slope deg, roadway), `weather` op (rain makes surfaces
wet), `vehicle` + events, `navigation_robot.garage_door` events, shed doors with `latch_outside`,
`pet_follower`, doormats. Success: destination room/cell, no falls (invariant), `nav.terrain.*` absent,
`stats.pet_escape == 0`, door closed behind, `robot.nav_trapped` absent, indoor floor soil unchanged
(`side_effects.no_new_soil_except` or invariant `tracked_dirt`). Wrong: shortcut across beds/seeded lawn, normal
pace along an unrailed edge, walking behind a reversing car, under a closing garage door, leaving the back door open
for a following dog, skipping the doormat after mud/waste, steep wet slope.

## 11. Cluster -> catalog ids (105 owned entries)

| Cluster | Catalog ids |
|---|---|
| A. Indoor hazards and avoidance | T-cef0e80e01ea7c-01, T-3cb411b1250c88-01, T-e4a88f63458a7d-01, T-ed3a03c4010bb3-01, T-62fe1251aafeed-01, T-089c40fb507289-01, T-3947252bc4fb7c-01, T-bf7c4abfadd2f5-01, T-0383801c7466a3-01, T-acd93aa5142d6c-01, P-2f81bfa225f93d-01, P-9f25133150ed23-01, V-0d7a192faed95e-01, V-cb111f76a0f31d-01, A-926bc611ae9158-01, A-f31e9af8d87a02-01, T-9a71c2a1673bc0-01 |
| B. People, pets, social | T-7ab90d10fc324b-01, T-0f4cf54992e5aa-01, T-a4b6fe56b0ce18-01, T-1fb2fc7eb17e27-01, P-95e3dfec1c3af0-01, V-44c20bd7781053-01, V-7caf27cf2ece8f-01, A-d1959ab8f75835-01, A-812feb473ca9b9-01 |
| C. Stairs, doorways, thresholds, elevators, gates | T-0bc13d3fa88f75-01, T-e111147fc8a148-01, T-dea1156b50840c-01, T-032bf4d6e2751d-01, T-0a63bb79d5ed53-01, T-90a466003f68e9-01, T-5419ee0cd25458-01, T-c893679f0eb53d-01, T-03d77bbfc281a4-01, T-7e27e4a6a3b1e5-01, T-3fd1e06544bad4-01, V-ff2e8b587a7289-01, A-714e511e8fc953-01, A-ff464b17c247ae-01, A-5160e3090d6a7e-01 |
| D. Lighting and darkness | T-eec72ca5d21c54-01, T-e39677b939af63-01, T-d901f3b7dda58a-01, V-d6f8f56e8199d4-01 |
| E. Rooms, privacy, no-go, wayfinding | T-66f3da50af0603-01, T-fc36ce173bdb6d-01, T-54a8a8191c8930-01, T-c6a754a799fd83-01, V-b8977e1941db1f-01, V-915c5ad7fd711d-01, V-64cc5c68c1543b-01, V-e779a3128e0a42-01, A-ea605735823af4-01 |
| F. Follow, lead, patrol, inspect, search | T-09ef414351dfae-01, T-d59c30c83bf108-01, A-6f2ab64d67dd38-01, T-9c314ab844eadb-01, T-56d1d7f6a17796-01, T-ea15572dd318da-01, P-460bb5bb07329c-01, P-038b5f6b6e6b5d-01, T-c7c2ad800886b3-01 |
| G. Clearing paths, egress, space | T-fcbe4008c45007-01, T-c54fddb6a59de3-01, T-7480617226d091-01, T-82ec0b5187f9ff-01, T-44ce90d0403cd0-01, X-d70c0fc64a8a63-01, A-9455387c13dc9c-01, A-11fcca6dee9f2d-01, A-b33e80fb44e5fc-01, A-85d099fbbbd93f-01, A-04b40946bea880-01 |
| H. Measuring, fitting, leveling | T-c39a630dc5c6f6-01, T-13da1385e2f81c-01, T-9615ae9f7570b4-01, T-ae6adfd34251ec-01, T-9aaad85165c94e-01, P-cfe7e482c43cdd-01, P-2cea9d31f52189-01 |
| I. Reaching high | P-6f82b375df3cc1-01, X-e0cb4401b15112-01, V-6d03678cccb29d-01, A-c93f46cbfe9563-01 |
| J. Robot self-care, dock, home, wait, quiet | T-5ad61b6881821b-01, T-dffab47da18a22-01, X-c87789aaa8e2ac-01, P-8b5a6408e44339-01, A-385ad88f5d7d07-01 |
| K. Other household robots | T-cc1f9dc25238f8-01, X-f05f58a6388176-01 |
| L. Outdoor terrain | T-4cd2eb047314d2-01, T-6a7bf8a9821341-01, T-1d8186ab98c3aa-01, T-c873a6b0e139f5-01, T-63281545a853a0-01, T-fb37e25e7be8c8-01, T-63c56907186dcf-01, T-f9da818224ff93-01, T-4e25dd888b321a-01, T-2439cf396fdf07-01, T-46f4b575065574-01, T-b3800d6c5d3f59-01, V-a67a44bbc06577-01 |

Example specs: T-089c40fb507289 (A), T-0383801c7466a3 (A), T-e111147fc8a148 (C), T-d901f3b7dda58a (D),
T-5ad61b6881821b (J), T-13da1385e2f81c (H), A-04b40946bea880 (G), X-f05f58a6388176 (K), P-6f82b375df3cc1 (I),
T-7e27e4a6a3b1e5 (C), T-09ef414351dfae (F), T-03d77bbfc281a4 (C).

## 12. Known limits

* The grid is 0.5 m; clearances below a cell are modeled by `narrow` hazard widths and connection `width_m`.
* Tilting a carried object on end to pass a doorway is decided by `fit_check`, but core `move` still refuses a
  held load wider than the doorway (`core.held_width` ignores orientation) - see kernel requests.
* Elevators are opt-in (helper) so other domains' apartment plans keep walking through the elevator room.
* Privacy intrusions and startles defer to the people domain when it is loaded (no double counting); door closers
  and door-leaf contact with loose items and agents defer to mechanisms (safety also runs closers); carried-liquid
  slosh defers to manipulation.
* Layout audit (`tools/layout_audit.py 20 --domain navigation_robot`): clean on 20 seeds x 6 configurations (kernel v2.8).
  The round-4 "hidden mount ... behind step_ladder_1" lines came from the generator's final repair pass re-standing a ladder
  that later furnishers' loose items had boxed in (its fallback ignores hidden mounts); a tall aid now goes only to a room
  with 4 spare spots after it. Kitchens no longer lose their scarce loose-item spots to the step stool (cleaning's
  `cleaning.waste` "no room for glass_jar" on seed 0; cleaning T-7e93003b0dcefa-01 and safety T-70a9e6a7a83f64-01 on seed 97).
* With the people domain loaded, waking sleepers is its hearing model: robot footsteps are one sound at the END cell
  of each motion with no gain closer than 1 m (fast 44 dB), so passing right beside a sleeper on the way elsewhere does
  not wake them there (this domain still counts `nav.margins.sleeper_close`); a light jump only counts against the
  level its senses system last saw while they slept (initialised on its first tick, not at reset). Startles need
  `startle_prone`. Reported to the people owner.
* Floor water: with cleaning loaded it owns spills and `water_film` (which re-derives `wet_cells`); `wet_cells`
  written without a film (scenario wet bands, other domains) do not dry and are dropped the next time cleaning
  rewrites that room's film.
* Coverage counts floor cells seen within 8 m (4 m with only the robot's light) past tall furniture; the kernel's
  observation still lists everything in the room, so coverage is a quality metric, not a perception filter.

## 13. What the robot can perceive (realistic perception, kernel v2.4-v2.8)

Oracle mode (the default) is unchanged apart from the fixed instrument draws (section 7), spill labels by appearance
and tails of resting pets: exact records, labels in messages and incident causes, the full inspect details. Under
`perception="realistic"` this domain's types, actions, observer and inspector follow docs/PERCEPTION.md: entities are
handles, fields have tiers, knowledge comes only from successful actions, `env.step` returns no evaluation or reward
(tests and verifiers call `env.evaluate()`), and permission-type refusals arrive with category "permission".

### Field tiers of this domain's types (type JSON `perception` blocks)

| Type | glance | inspect | read | measure (tool) | never (ask / infer only) |
|---|---|---|---|---|---|
| climbing aids (`step_stool`, `kitchen_step_stool`, `step_ladder`) | climbable, aid_kind, steps, occupied_by, open (unfolded) | step_heights_m, standing_height_m, max_standing_step (stools), folded_size_m, non_slip_feet, two_handed, top_cap_m, settings (spreader lock) | label_text; the ladder's max_standing_step | size_m (tape_measure) | rated_kg (legible only as the ladder's label_text), climb_risk, risk, `_wobbly` |
| `reacher_grabber` | tool_kind, jaw | reach_extension_m, tip_payload_kg | - | length_m (tape_measure) | - |
| `robot_headlamp` (the robot's own gear) | wear_slot, gear_kind, switchable, battery_level, low_glare | - | - | - | - |
| `tape_measure` | tool_kind | - | label_text, accuracy_class | - | hook_play_mm; length_m and case_length_mm (their values are printed in label_text) |
| `laser_distance_meter` | tool_kind | battery_level | label_text | - | accuracy_mm, range_m |
| `spirit_level`, `torpedo_level`, `digital_level` | tool_kind | read_resolution_deg | - | length_m (tape_measure) | accuracy_deg |
| `handrail` | on_stairs, mount_height_m | - | - | rail_length_m (tape_measure) | `_loose` (felt: inspect within touch reports `loose`) |
| `robot_battery_pack` | - | part_type, swollen | - | charge_pct (battery_tester; `swap_battery` measures the removed pack) | capacity_wh, value |
| `battery_charging_station` | - | plugged_in | - | - | charge_rate_pct_per_min |
| `robot_vacuum`, `robot_vacuum_mop`, `robot_mower` | docked | remote_status, appliance | label_text (the mop's tank rules) | battery_level (battery_tester) | rv (internal state: inspect reports it as perceived, below), value |
| `robot_vacuum_dock`, `auto_empty_dock`, `robot_mower_dock` | rv_dock | clearance_front_m, clearance_side_m, plugged_in | - | - | bag_fill (inspect shows a bucket) |

Fields this domain writes onto other domains' entities (file-level `field_tiers`): glance `vehicle` (a reversing car
is obvious; its scripted next state `vehicle.then` is hidden), `moving` (a garage door in motion), `occupied_by` /
`occupied_step` (someone on a ladder); hidden `placed_on_wet`. Rooms (`field_tiers.room`, hidden): `nav_no_go`,
`traffic_cells`, `elevator`, `wet_cells`, `water_film`, `floor_hazards`. Pencil `marks` get a per-entity `_perception`
(hidden) on the marked wall or door ("marks" means other things on other types), and inspecting the surface shows each
mark located to about 5 mm (`_perceived`). Kernel defaults already make `displaced`, `bunched`, `curled`, `toppled`
glance and `scuffed`, `cracked`, `tangled_in` inspect.

Hidden truth this domain keeps in underscore fields is never shown: `_extents_mm`, `_measure` (spaces, spans, gaps),
connection `_clear_width_mm` / `_height_mm`, `_tilt_deg`, `_wobble_mm`, `_walked_mm`, `_loose`, `_wobbly`; only a
measured reading reveals them, at the instrument's precision, and the same reading every time (no averaging).

### Look-alike groups (file-level `looks_like`)

| Group | Descriptor | Resolve | Members |
|---|---|---|---|
| `robot_vacuum` | round robot vacuum | inspect | robot_vacuum, robot_vacuum_mop (only the mop model takes `refill_robot_tank`) |
| `robot_mower` | robotic lawn mower | inspect | robot_mower (its own group, so the vacuum group does not swallow it via is_a) |
| `bubble_level` | aluminum level | inspect | spirit_level, torpedo_level, digital_level |

Mirrors and reflective glass: the kernel lists an un-inspected `category: mirror` or `reflective` entity as an extra
doorway onto the same room in `connections` (its handle is the connection's `door`). This domain then (a) leaves its
derived `glass` margin out of `obs.grid.hazards` and `obs.room.floor_hazards`, (b) hides it from planning for the
motion (`traverse._pre_motion` stashes it, the consequence engine gets it back), so `avoid=["glass"]` does not help an
un-inspected panel, and (c) a collision with it grants inspect (walking into it settles what it is). `hazard_band` kind
`glass` takes `props.reflective` and `props.category: "mirror"` for such panels (default panels are unchanged).

### People and pets lying down

Asleep or awake, someone lying down looks the same. `obs.grid.hazards` shows every lying person (posture sleeping or
lying) and every pet lying, sleeping or resting with the same entries - their cell and the 8 cells around it, kind
`lying` - while this domain's internal `sleeping` ring (sleepers only) and the kernel's `sleeping`/`person_down` center
markers are removed (a fallen person stays `person_down`; someone asleep sitting up shows no ring; nothing in the dark;
concealed people show nothing). `avoid=["lying"]` is plannable: the `path_blocked` hook blocks every lying agent's cell
and ring, minus an approach target's (`ignore_sources`). Pets lying, sleeping or resting all have a `tail` cell.
Socially careful walks (`go_to social=true`) ring everyone lying down and treat everyone else as awake. `follow`
refuses a sleeper with the same words and category as someone who is not going anywhere; the inspector's vacuum-dock
`clearance_blockers` lists people and pets lying in the zone whether asleep or not.

### Actions: what they reveal and at what precision

| Action | Reveals | Grant |
|---|---|---|
| `measure` tape / laser | the reading only: tape +/-(0.3 + 0.2 L) mm + hook play, inside spans 76 mm short unless `add_case`, sag over 2.4 m; laser +/-2 mm between surfaces, +/-12 mm at edges; one fixed error per (target, quantity, tool) | measure `size_m` when the object's true size IS its size_m (no `_extents_mm`, no named `_measure` span); measure `<field>` for `dimension=<length field>` (drape_cm, flat_size_m, cord_length_m...: other domains' tape-measure tiers); readings of hidden extents/spans/doorways stay in the result and `obs.measurement` |
| `measure` estimate (no tool) | camera/odometry estimate 2-10 % off, 10 mm steps, one fixed error per (target, quantity) whatever the step or viewpoint | none |
| `check_level` | tilt at the level's resolution (0.1 deg, digital 0.05; eyeball +/-0.8 rounded), fixed per (target, axis, tool): re-reading never re-dithers; wobble rocks/solid, lock nuts | inspect `settings` (feet, lock nuts) |
| `swap_battery` | the robot's own gauge reading of the removed pack | measure `charge_pct` of the pack |
| `go_to`, `lead` | routes are planned with the locks the robot knows (inspected or tried); an untried locked door on the route is walked to and tried: "Stopped at <door> in <room>: it is locked" / lead status `door_locked` (a success, time spent) | inspect `locked` of that door; a known lock is refused up front |
| `patrol` | closed bathroom/bedroom doors are not opened (the room is skipped: someone may be inside; occupancy, sleep and activity behind a door are not known); every closed exterior door of a room is tried (approach + inspect) before "unlocked" is reported; a damp film counts as water on the floor only within 2 cells of a viewpoint the sweep stood on; findings in handle order | core inspect of what it reports |
| `follow` | a lost person's room is not reported (`person_room` null) | - |
| `lead` with `warn` | calls out only the hazards it can see (no black ice, no damp film from afar, nothing in the dark); an unseen one still trips a frail person | - |
| `inspect` (this domain's inspector) | handrail `loose`, ladder `wobbles` (where=closely), machine `rocks`: only within touch (else omitted, the rail is not marked checked); door `swing_blockers` the robot can see from where it stands; household robot `robot_status` as perceived (bin_fill empty/low/half/full/overflowing, brush hair none/some hair/heavily wrapped, mop tank to 0.05 L, tank_liquid "liquid" - water and vinegar look alike - no run counters, no `tank_damaged` error: seals fail later and out of sight) and `charge_indicator` empty/low/half/full instead of battery_level; dock `bag` bucket; vacuum-dock `clearance_blockers` and robot-dock `approach_blockers` only as seen from here (handle order); pencil `marks` to 5 mm; floor `floor_hazards` visible from here (never black ice or loose boards, damp films only within 2 cells, nothing in the dark, 5 m range) | core inspect grant |
| `read` | the ladder's rating label, the tape's case-length note, the laser's and mop tank's labels | core read grant |
| `refill_robot_tank` | names the liquid only once the source is identified (read its label); otherwise "liquid from <handle>" | - |

Messages, refusals and incident causes name things by id (handles, or "something" for things not seen), never by a
label that could resolve a look-alike (`common.thing`), and never quote a hidden value: a slip says what the floor
looked like ("slipped (wet tile)", "a slippery patch" for black ice; no friction numbers), `get_up` says only "too
slippery", a fall from a stool names an invisible floor hazard as "slippery", the ladder's approval refusal uses the
kernel's generic wording (category permission), and the reacher refusal gives the height to 0.1 m.

### Observer

`obs.nav` is the robot's self-state, exact in both modes (it knows its battery, pose, faults); `last_incident` drops
what the robot cannot identify (the liquid poured into a tank) or measure (a doorway clearance, a dropped item's mass,
how far an appliance walked, a mud depth, a call's sound level, an overreach margin, the approach angle; a spilled
volume becomes `spilled: true`), and is null for incidents it cannot notice (`rv_tank_wrong_liquid`);
`follow.person_room` is null once it lost them. `obs.stairs` lists items on the steps only when the robot perceives
them from where it stands (lit, not missed by the small-object model; handle order) and `tread_wet` is null in the
dark. `obs.grid.hazards` / `obs.room.floor_hazards` lose black ice, loose boards and the glass margins of un-inspected
mirrors/reflective panels, and show lying people and pets as above. Spills this domain creates are labelled by what
they look like (`common.spill_label`: water, vinegar, bleach... are all "clear liquid spill"; oil, coffee, juice are
named).

### Perception-centric specs (`tasks/authored/navigation_robot__perception.json`, `"perception": "realistic"`)

| Catalog id | Entry | Skill | Wrong plans on glance-level belief |
|---|---|---|---|
| T-9615ae9f7570b4-01 | Measure available space | alcove (`measure_target` kind space): read the tape's label, width at the narrowest point (`at: min`) and inside spans with the case added | camera estimate; no case length (did not read the label); width once at mid height |
| T-c39a630dc5c6f6-01 | Measure distance | clear gap between two facing pieces (`measure_target` kind gap: hidden `to:<id>` span): read the label, tape with the case added | camera estimate; no case length |
| A-926bc611ae9158-01 | Identify reflections vs. reality | a reflective glass partition / mirrored divider on the direct route looks like an opening: inspect it, then route with `avoid=["glass"]` | avoid glass without looking closer (walks into it); straight through |

Known perception limits (kernel requests): read-tier numbers render with inspect-style noise (so numbers duplicated in
a label are never-tier here and read from `label_text`); the kernel's own `hazard_cells` still tells a sleeper
(`sleeping`, shown as "lying") from someone awake lying (`person_down`) and has no plannable `lying` kind - this
domain's observer and `path_blocked` hook even them out while it is loaded, kernel-only builds would not;
`Realism.perceive_record` decides from the type table alone whether a field is shown (per-entity `_perception` tiers
apply only through `render`).
