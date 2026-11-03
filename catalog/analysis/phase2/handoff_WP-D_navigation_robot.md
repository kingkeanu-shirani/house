# Handoff WP-D navigation_robot (VERIFY_HARDENING section 25 row "WP-D <domain>", section 23.3)

Files (only these): `house_sim/systems/navigation_robot/{__init__, common, doors, elevator, measure, robot, robots,
scenarios, skills, traverse}.py` (changed), `house_sim/systems/navigation_robot/hardening.py` (new),
`house_sim/data/verify/watch/navigation_robot.json` (new), `house_sim/data/speech/truth_navigation_robot.json` (new),
`house_sim/data/vocab/navigation_robot.json` (changed), `docs/domains/navigation_robot.md` (section 4f, the Latched
fields table, the module table), `tests/test_navigation_robot_hardening.py` (new, 23 tests), this handoff and
`WP-D_navigation_robot.json`. `house_sim/data/types/navigation_robot.json` and `tests/test_navigation_robot.py` are
unchanged. Nothing was committed, staged or stashed; nothing was written under `%USERPROFILE%\.house_ai`.

## What landed

1. **Field classes for every type property** (6.3, L-WATCH part 2). `watch/navigation_robot.json` classifies all 82
   properties of the 26 types and every sub-key of their dict fields (`rv.*`, `appliance.*`, `settings.*`):
   `python tools/field_census.py --unclassified --domain navigation_robot` -> 0 field names, 0 (type, field) pairs
   (was 43 names / 77 pairs). Specs and dimensions are `identity`; `open` (an unfolded climbing aid), `on`
   (headlamp), `plugged_in`, `docked`, `settings.spreader_lock`, `rv.schedule` `setting`; a household robot's run
   state, charging, errors and timers `process` (its own state machine); `rv.bin_fill` and an auto-empty dock's
   `bag_fill` debris loads (`content`, guard `mess`: the mess balance judges them, not the content guard);
   `rv.mop_tank_l` / `rv.tank_liquid` content; hair on the brushes (`up`), `rv.bumper_ok` (`false` worse),
   `rv.damaged`, `rv.pad_damage` conditions; counters and the derived `rv.dock_zone_clear` bookkeeping. Per type:
   `robot_battery_pack.health` / `capacity_wh` are `process` (swap_battery writes the old pack's charge, capacity,
   health and temperature onto the record, so a lower health there is no damage; `swollen` / `damaged` stay
   conditions). `standing_height_m` is `identity` (the kernel's name rule said placement). `water_sensitive`: the robot
   vacuums and their docks, the headlamp, the laser meter, the digital level, the pack, its charger and the elevator
   panels. Census check (`--check`): 0 written fields without an explicit class.
2. **Writer classes and causes** (6.2). Every system stays `physical` (`plugins.SYSTEM_WRITERS`); inside
   `navigation_robot.doors` a resident's car finishing its manoeuvre (`navigation_robot.vehicles`), residents walking
   their routes and stopping (`navigation_robot.people`) and pets following the robot or slipping through an open door
   (`navigation_robot.pets`) are written `volition` (`World.writing`). Causes: a household robot's whole substep
   (`robots._tick_robot`) runs under `cause=<robot>` -- the floor it cleans, cords it chews and the devices it
   unplugs, rugs it wets -- so `env.robot_attributable` is True when the robot started it (a robot write of its
   `appliance`) and False for a run its schedule started; a self-closing door and a motor-driven door cite the door; a
   walking appliance cites itself; a spare pack charging cites its station and itself; an elevator car's arrival cites
   its room record (the call).
3. **Mess flows with origins** (6.5, `world.mess_flow`). A robot vacuum's run moves `debris` grams (the loose floor
   soil it lifts, at cleaning's g/m2 over the room) from `floor:<room>` into `entity:<rv>` (a `vacuum`: an accepting
   sink) and keeps the bin's tally in `rv._bin_g` (an underscore key: never watched); an auto-empty dock takes the bin
   into `entity:<dock>` (`_bag_g`); `empty_robot_vacuum` tips the bin or the bag into `entity:<trash>` (writer robot);
   things a bin swallowed, a brush caught or a bin emptied move as mess parcels when they are ones
   (`common.log_mess_move` -> core.log_mess_move). The robot's feet: soil tracked indoors is created mess on that floor
   (`created:robot_feet` -> `floor:<room>`, `debris`; pet waste stepped in `pet_waste`, origin `pet_waste:robot_feet`),
   a wet footprint `liquid:spill` (0.03 mm film x the cell). Liquid the robot's motion slops or spills
   (`common.slop`, `common.spawn_spill`: stumbles, sloshing, an overfilled mop tank) flows from `created:<container>`
   to the floor with origin `<class>:<container>` by `common.liquid_mess` (chemical, grease, dirty water, the
   pet-waste class, food, water from the kernel's liquids table), so `group_class` judges it by its liquid. The domain
   registers no mess kind: `debris`, `liquid:*` and `pet_waste` are cleaning's, plumbing's and pets' (6.5 table).
4. **Truth mappings** (13.1-13.3). `truth_navigation_robot.json` with vocabulary words in `vocab/navigation_robot.json`:
   states `docked` / `undocked` (a household robot's `docked`, else `rv.docked`), issues `bin_full` (rv.bin_fill or
   an auto-empty bag at 95 %: the level at which the robot stops with "Please empty the bin"), the patrol words
   `water_on_floor` (a kernel puddle, or a thing that is a wet or slippery floor hazard -- paths.anomalies' rule) and
   `unexpected_person` (role intruder, stranger or unknown). "The robot vacuum is docked" / "is off its dock" / "'s bin
   is full" parse and score True / False against the state (TruthTests).
5. **Derived field** (8.2): `@derived_field("docked", types=("robot_vacuum",))` (`hardening.docked_now`): seated on its
   own dock -- same room, resting on the floor, footprint on or touching the dock's, and its state machine docked. A
   robot vacuum carried off or shoved by any verb while the stored flag stays true is not docked in a schema-4 view;
   legacy tasks are untouched (`derived_view` returns the state itself). The leveling feet's `_tilt_deg` /
   `_wobble_mm`, latched by the `setting_changed` hook alone until now, are kept true by `navigation_robot.leveling`
   whenever the feet change by any path (`measure._stale`; the idle probe sees a stale one).
6. **Settings skills** (10.3): `plugged_in` of docks and the battery charging station (core plug_in / unplug through
   the use skill), `docked` of a household robot (the dock verb with the robot as target, or fetch to lift it off).
   The kernel defaults cover a climbing aid's `open`, the headlamp's `on` and the ladder's spreader lock.
7. **Structured instrument readings** (15.2): `measure` returns `readings` -- one per value: `{"subject", "quantity":
   "length", "value", "unit", "dimension"}` plus `"field"` when it read a length field of the thing (drape_cm,
   cord_length_m ...) or `"to"` for a distance -- and `check_level` its two tilts (`quantity` "angle", `unit` "deg").
   `tasks/perceive.py`'s reading index picks them up from the result JSON. Only the returned result carries them;
   `facts.nav_last_measure` and `nav.measurements` (state) are unchanged.
8. **World-behaviour fixes and carried-over rows**:
   * `navigation_robot.dock_setup` (WP11's held-out finding, T-5ad61b6881821b-01 seed 502): the generated house had
     the robot's own dock behind a coffee table, so no front cell was free, no basket was placed and `{blocker}` bound
     None. The helper now reseats the dock where a front cell can be reached (its own room first) when none can, and
     refuses to bind no blocker. A house whose dock fronts are free is untouched (seeds 0 1 2 41 97 unchanged).
   * `navigation_robot.go` (23.3, careful speed on winder stairs): with no pace given, a route descending a winder
     flight the robot cannot take guided (`tasks/skills._unguided_winders`: a free working hand, a usable rail, light)
     is walked at the careful pace, as core go_room does since WP11.
   * `mark` refuses an unfamiliar target identify-first (closeout_REV 7 / VER2 6: "Hold a pencil" was rewritten to
     "Hold a unfamiliar medium object" for an unfamiliar pencil alone).
   * `common.unfamiliar` / `refuse_unfamiliar` delegate to the kernel's `Ctx.unfamiliar` / `Ctx.refuse_unfamiliar`
     (closeout_breakages' tenth row, the optional clean-up; same behaviour).
9. **Docs**: `docs/domains/navigation_robot.md` section 4f and a `Latched fields` table (`docked`, with its registered
   recompute) that L-LATCH reads; the leveling tilt, the watches, the robot's dock state and the history records
   (`nav.stairs.*.rail_contact`, `nav.follow.*`, counters, `placed_on_wet`) are documented as not latched.

## Interfaces (signatures)

```python
# house_sim/systems/navigation_robot/hardening.py
seated_on_dock(state, eid) -> bool
docked_now(state, eid) -> bool | MISSING          # @derived_field("docked", types=("robot_vacuum",))
plug_steps(state, eid) -> [(value, step)]          # @settings_skill("plugged_in", types=docks + battery_charging_station)
dock_steps(state, eid) -> [(value, step)]          # @settings_skill("docked", types=("robot_vacuum",))
# house_sim/systems/navigation_robot/common.py
SOIL_G_M2, DEFAULT_G_M2, BIN_DEBRIS_G_PER_L (200 g/L), FOOT_SOIL_KINDS
soil_grams(kind, level, area_m2) -> g; floor_area_m2(state, rid); floor_key(state, rid) -> ["floor"|"outside", rid]
mess_flow(w, kind, amount, src, dst, origin=None, cause=None)     # world.mess_flow when tracked; amount > 0 only
log_mess_move(w, eid, before, after, before_relation=None)        # core.log_mess_move
liquid_mess(liquid) -> (mess kind, origin class)
log_spill_flow(w, liquid, liters, source, spill_id, rid=None)
# house_sim/systems/navigation_robot/robots.py
bin_grams(rec) -> g (rv._bin_g, else bin_fill x capacity x 200 g/L); bag_grams(rec) -> g (_bag_g, else bag_fill ...)
# house_sim/systems/navigation_robot/measure.py
length_readings(state, target, result) -> [reading]               # measure's result["readings"]
# house_sim/systems/navigation_robot/skills.py
_winders_on_route(r, target) -> bool                              # navigation_robot.go's careful rule
```

## Deviations from the spec (and why)

* **No `@mess_kind` registration.** 6.5's table gives `debris`, `liquid:*` and `pet_waste` to cleaning, plumbing and
  pets (one registration per name, plugins.mess_kind). This domain logs flows of those kinds where it moves them.
* **`_tilt_deg` / `_wobble_mm` are not registered derived fields**: tools_vehicles' fasteners write the same two names
  (a second registration is a load-time ValueError). The leveling system keeps them true instead (a write of the feet
  by any path is healed within the substep), and the request below asks tools_vehicles to own the one registration.
* **No meters.** The docks' and the charger's draw is not booked in `climate.energy`: a `standby_w` on the types would
  run climate's meter in every generated house with a dock (35 % of them) and move every such energy digest.
* **Angle readings carry no SI value**: the kernel vocabulary has no `angle` quantity; adding one in this domain's
  vocabulary file could collide with another domain's (a load-time error), so it is a request.
* **`water_on_floor` truth reads entities only** (speech.issue_truth aggregates a room over its things); a room's wet
  cells alone leave the claim unknown (never false).
* **hazard_created and zones: nothing to add.** The domain's verbs create no entity (spills are mess parcels, shards
  the kernel impact model's), and it owns no people or pets.
* **WP9's scenario-helper requests do not apply**: no helper of this domain places look-alike members or takes a role
  list (`ctx.placer.order` / `ctx.sample_roles`); `ctx.audit` is optional and unused.

## Legacy outcomes (P10) and digests

* Changed verification outcome: **T-5ad61b6881821b-01, held-out seed 502: FAIL -> PASS** (the dock_setup fix). Its
  verification seeds 0 1 2 41 97 pass before and after. A/B of both navigation_robot files on seeds 500-509, HEAD vs
  HEAD + this package's files (scratch `heldout_ab.sh`): every other result identical (T-9615ae9f7570b4-01 seed 502
  and A-926bc611ae9158-01 seed 506 fail on both trees: known gaps).
* All 15 navigation_robot specs pass `--check` on their own seeds; all 243 authored specs pass (Verification).
* Room golden (`tools/golden.py check --all` on HEAD + this package's files only): **2 of 243 reference final-state
  digests differ, by `logs.mess_flows` only (and `rv._bin_g`)**: `specs/T-89530941a65283-01` (safety: wet
  footprints in the shed, `liquid:spill` flows from `created:robot_feet`) and `specs/X-f05f58a6388176-01` (the bin
  emptied: one `debris` flow, the bin tally). Steps, time and evaluation unchanged; neither is a fixed spec, so
  `tests/test_room_golden.py` passes; G8 needs the main session's re-record (this package does not own the golden).

## Known gaps

* A-926bc611ae9158-01, held-out seed 506 (pre-existing on HEAD): the privacy-mode wrong plan passes -- the single-cell
  glass panel of `hazard_band(kind=glass)` is walked round once the robot has approached it from the side. A binding
  for the band's start-side cell did not settle it (on 506 the planner still found an equally short path round), so
  it is left to WP12 (the wrong plan) / a later helper change.
* T-9615ae9f7570b4-01, held-out seed 502: core `spawn ... floor: {room}` lands on an unreachable cell (WP9's op).
* Spills the kernel makes inside its own calls (impact.fall of an open container knocked off, body_faults.drop_held)
  log no flow; a robot vacuum-mop wetting a rug logs no liquid flow (`house.wet` judges it).
* The mower and its dock are water-sensitive by the type-name rule (they inherit robot_vacuum / robot_vacuum_dock).
* Fields this domain writes on other domains' types fall to the kernel's default `setting` (marks, tangled_in,
  placed_on_wet, open_angle_deg, vehicle.*, moving, away, scuffed): per-field entries of a domain file only cover its
  own types, so they are requested from WP1.
* `docs/DIALOGUE.md`'s generated vocabulary tables are stale after the vocabulary change (people's new vocabulary file
  too): `tests/test_dialogue.py` test_the_docs_tables_are_generated_from_the_data fails until `python tools/vocab.py
  --markdown` is rerun (main session; not this package's file).

## Phase-1 carry-over

* closeout_REV 7 (VER2 6, CONFIRMED), the `mark` half: fixed (`test_mark_refuses_an_unfamiliar_target_identify_first_
  whatever_it_is`, fails on HEAD). REV's novel_wording repro now answers the identify-first message for every kind.
* closeout_REV 5 (VER2 4, CONFIRMED), the navigation_robot half (robot_vacuum group): no change needed in this domain's
  files -- REV's probe (scratch `probe_six.py`, restricted to this domain, room and geometric) over every verb x every
  entity parameter, on the table and held: 0 differences for the robot_vacuum and bubble_level groups and 0 for 39
  unfamiliar twins. Over every registered verb (`fixed` mode, room) only inspect / read / read_aloud differ (the
  group's resolving acts; each model's printed label), cleaning's absorb / mop tells are gone with cleaning's own fix.
  The probe's geometric look-alike run first showed 38 "differences" that were its own artefact (the analyst's handle
  call registered the handle in the first twin only); with the handle registered in every twin: 0. Pinned by
  `test_robot_vacuum_look_alikes_answer_alike_to_every_verb_of_the_domain_until_inspected` (a guard: it also passes on
  HEAD).
* closeout_breakages tenth row (navigation_robot's optional clean-up): done (delegation to the kernel helpers).
* closeout_INT3's requests: the domain rows are the closeout_breakages rows above; nothing else is this domain's.

## Requests

* main session: re-record the room golden's `specs/T-89530941a65283-01` and `specs/X-f05f58a6388176-01` (class
  `logs.mess_flows`, and `entities.robot_vacuum_mop_1.rv._bin_g` in the second); rerun `python tools/vocab.py
  --markdown` for docs/DIALOGUE.md.
* WP1: (a) let `waterproof: true` (or `weatherproof: true`) on a record win over the `water_sensitive` type list
  (robot_mower / robot_mower_dock inherit robot_vacuum / robot_vacuum_dock); (b) kernel classes for fields
  navigation_robot writes on other domains' types: `marks` (condition, worse up or change), `tangled_in` (condition),
  `placed_on_wet` (bookkeeping), `open_angle_deg` (setting, with `open` in the interior-door ambient allowance and the
  restore open paths), `vehicle.*`, `moving`, `away` (process), `scuffed` (condition, true worse); (c) an `angle`
  quantity (deg, rad) in the kernel vocabulary for tilt readings.
* WP-D tools_vehicles: register one `@derived_field` each for `_tilt_deg` and `_wobble_mm` whose recompute hands a
  record with `leveling` to `navigation_robot.measure.compute_level` (navigation_robot registers neither, to avoid a
  double registration).
* WP-D cleaning: if the debris measure should see robot-vacuum bins and auto-empty bags as parcels, read `rv._bin_g`
  and `_bag_g` (grams); and count a floor entity's top-level `soil` loose kinds as `floor:<room>` parcels at the same
  g/m2 x room area (the robot vacuum lifts from it).
* WP4: `truth.json`'s `stuck` could also read `rv.state eq stuck`, and `folded` / `unfolded` a climbing aid's `open`
  (a domain file cannot redefine a kernel word).
* WP6: a damaging-verb registry for B14's `damaged` probe; navigation_robot's: `free_stuck_robot method=drag` (the
  bumper switch), `refill_robot_tank` with a forbidden liquid (the tank).
* WP9: T-9615ae9f7570b4-01 held-out seed 502 -- `spawn ... floor: {room}` must land on a cell the robot can reach.
* WP12 navigation_robot: A-926bc611ae9158-01 held-out seed 506 -- the privacy-mode wrong plan does not realise its
  mistake (see Known gaps).

## Verification (shared live tree, 2026-10-29, other WP-D / WP6 packages editing concurrently)

* `tests/test_navigation_robot_hardening.py` 23 + `tests/test_navigation_robot.py` 232: OK normal, `HOUSE_SIM_STRICT=1`,
  `HOUSE_SIM_DOMAINS=core,navigation_robot` (normal and strict). On a `git archive HEAD` copy 20 of the 23 new tests fail.
* `python tools/orchestration/green_tests.py 3`: 5,640 tests per mode; both navigation_robot files ok in both modes.
  Ten failing runs, attributed on a copy of HEAD plus only this package's files (`mk_mine.sh`): test_dialogue's
  generated-docs test (this package's vocabulary words and people's new vocabulary file: docs/DIALOGUE.md needs
  `tools/vocab.py --markdown`, request below); test_ledger (cleaning's `@mess_kind("debris")` collides with the
  test's own registration; passes on HEAD + this package), test_lint's T-d389e2979f6aad-01 case (cleaning; passes on
  HEAD + this package), test_room_golden's fixed specs (T-089c40fb507289-01 differs by cleaning's `waste` flag on a
  puddle, X-bf82170244be22-01 / T-48266979ae98d3-01 by other packages; the test passes on HEAD + this package),
  test_verify_hardening's private held-out test (WP6's private families).
* `tools/perception_audit.py 6 150`: 0 leaks, 0 crashes, room and geometric camera. `tools/layout_audit.py 40`: 240
  houses, 0 problems. `tools/field_census.py --check`: 0. `tools/py39_lint.py` on the domain and its tests: 0.
* All 243 authored specs, own mode, one combined file (`python -m house_sim --check <all> --workers 3`, live tree): 243
  passed, 0 failed; the 15 navigation_robot specs pass on seeds 0 1 2 41 97.

## How to test

```
python -m unittest tests.test_navigation_robot_hardening tests.test_navigation_robot        # also HOUSE_SIM_STRICT=1
HOUSE_SIM_DOMAINS=core,navigation_robot python -m unittest tests.test_navigation_robot_hardening tests.test_navigation_robot
python tools/field_census.py --unclassified --domain navigation_robot      # 0
python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python -m house_sim --check tasks/authored/navigation_robot__examples.json --ids T-5ad61b6881821b-01 --seeds 0 1 2 41 97 502
python tools/perception_audit.py 6 150 --workers 3 [--camera geometric]   # 0 leaks
python tools/layout_audit.py 40                                            # 0 problems
python tools/py39_lint.py house_sim/systems/navigation_robot/*.py tests/test_navigation_robot*.py
```

Scratch (`p2_WP-D_navigation_robot/`): `probe_six.py` / `p3lib.py` / `novel_wording.py` (REV's probes, restricted to
this domain; run with `REV_TREE=<tree>`), `heldout_ab.sh` + `mk_mine.sh` (the A/B on HEAD vs HEAD + this package),
`spec_final.py` (the golden state diff), `before/` (HEAD archive: 20 of the 23 new tests fail there).
