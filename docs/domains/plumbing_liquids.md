# Plumbing and liquids domain (`plumbing_liquids`)

Water supply and shutoffs, faucets, sinks and stoppers, drains and clogs, toilets, tubs and showers, leaks and
water damage, the water heater and meter, hoses/spigots/nozzles, irrigation, liquid containers (fill, pour,
empty, transfer), spills, wet things and ice. Package `house_sim/systems/plumbing_liquids/`, types
`house_sim/data/types/plumbing_liquids.json` (43 types), tests `tests/test_plumbing_liquids.py`, examples
`tasks/authored/plumbing_liquids__examples.json` (12 oracle specs) and `tasks/authored/plumbing_liquids__perception.json`
(3 realistic-perception specs). Perception hardening round (kernel v2.4-v2.8 realistic perception, v2.5 layout), every domain loaded: `--check`
passes all 12 examples and all 3 perception specs on seeds 0, 1, 2, 41, 97; `tools/perception_audit.py 30 80` and
`tools/layout_audit.py 20 --domain plumbing_liquids` attribute no line to this domain. See section 4b for what the
robot can perceive (liquid names, durations and instrument draws were hardened after the red-team round).
Kernel round 5 pass: spec items start on a counter with room (`counter_items`, section 6; seed 1's short, crowded
counter had no room for two ice trays), exposed supply pipes run along their wall on the grid (`furnish.lay_pipe`),
the `water_valve` / `rubber_plunger` groups set one glance size, and identification, sealed containers, instrument
draws and liquid provenance use the kernel helpers (`ctx.unresolved`, `Realism.sealed`, `ctx.fixed_rng`,
`core.mark_contents_from`); `--check` passes both spec files on the five seeds (also with HOUSE_SIM_STRICT=1 for the
unit tests) and `tools/perception_audit.py 20 80` attributes nothing to this domain.
Tier-1 adoption round (kernel v3.0 physics, camera and body, v3.1 sweep; section 0b): the domain's copies of the carry,
open_spill, phase and thermal_tau laws step aside once the kernel's law is active (all five of its laws, handle
included, reported ready to flip: the unit tests and both spec files pass with each forced on), pours use the kernel's
transfer rules (opening and viscosity, heat-capacity mixing, the thermal-shock check, unfrozen liquid only), held
containers fill up to the hand's limit, water on the robot goes through the body, visual occurrences go through the
camera and sounds through the notice bus, the water system has an exact idle probe, and the three perception specs
declare the geometric camera. Every spec passes in its own mode on the five seeds on generation-2 houses (now their
house; the realistic three in the geometric camera they declare); `tools/perception_audit.py 20 80` and `tools/layout_audit.py 20 --domain
plumbing_liquids` attribute nothing to this domain; the unit tests pass normal, strict and with only `core,plumbing_liquids`
loaded.
Phase 1 (stabilise, after the independent review of 2026-10-22; section 0c): only liquid-tight containers hold what is
poured (a bread box, a colander and a napkin holder no longer do) and dirty, chemical or sewage water poured over food
contaminates it; the global harm flags this domain raises (`flood`, `damage`, `burns`) count only harms the robot caused
(exogenous ones are recorded in `plumbing.exogenous_harms`); the pour no longer changes behaviour by another module's
name (the cleaning solution travels under a `ctx.data` contract and is counted once); realistic valve messages count
turns to half a turn; the stain description has its own inspect key; `plumbing_liquids.goto` brings its target into
view before approaching through a wet room with the geometric camera (T-3b1a4024d46110-01 seed 97). 168 unit tests
pass normal, strict and with only `core,plumbing_liquids`; both spec files pass on the five seeds in their own modes.
Phase 1 finish (the domain's breakage rows and red-team findings; section 0d): a closed sprayer, pump head, cap, seal or
lid pours nothing ("Open it first", one wording); every splash, spill, overflow, drip and hose spray of this domain
wets through the kernel's `phys.liquids.wet`, so `phys.wetted` hooks (electrical's ingress and attribution, food's
contamination) see it and the amount that reaches a thing decides its wetness and water damage; food, mail and packing
material are no waste bins; the garbage disposal drains (`watertight: false`; a pour into it goes down its sink); an
unfamiliar thing gets the kernel's one identify-first refusal from `fill`, `pour` and `empty`; cup and flange plungers
are one size and the house main has a look-alike group of its own (no size spread left in this domain's groups); a
close look feels a vessel only when a hand can reach it, and a product bottle like any vessel (cleaning's request); a
cramped extra bathroom goes without a stall rather than hide a window blind or stand under a wall cabinet; the water
system's per-substep cost is lower (section 3) with identical states. 179 unit tests pass normal, strict and with only
`core,plumbing_liquids`; both spec files pass on the five seeds in their own modes (the perception specs also with the
geometric camera); over all 243 authored specs x 5 seeds no verdict changes (own mode, and the 51 realistic ones with
the geometric camera).

| Module | Contents |
|---|---|
| `common.py` | liquid property table (`LIQUIDS`), domain log/section, water accounting, spills/floor water/flood, pollution, outdoor ground |
| `liquids.py` | container model: composition `_mix`, dissolved `cl_chem`, potability, staleness, displacement, overflow parcels |
| `supply.py` | main / fixture stops / heater inlet, line pressure factors, water heater, meter, `water_available` hook |
| `fixtures.py` | faucets (single lever, two-handle, thermostatic, touchless, spigot), basins, `turn_on_water`, `turn_off_water`, `plug_drain`, `drain` |
| `valves.py` | `shut_off`, `open_valve` (valves, fixtures, toilet stop or float) |
| `transfer.py` | `pour`, `fill`, `empty`, `wring`; destination (wastewater) rules; product checks; fizz; water near live electrics |
| `toilets.py` | tank/bowl/clog model, `flush`, `plunge`, objects in the bowl, sewer backups |
| `outdoor.py` | spigots, hoses, nozzles, `spray`, `connect_hose`, `disconnect_hose`, hose storage, irrigation controller, freezing |
| `ice.py` | ice cube trays, freezer ice makers, ice in drinks, filter pitchers |
| `leaks.py` | hidden leaks, ceiling stains, water damage, condensation, drips onto catchers, water rings, closed-wet mildew, carry slosh, appliance hoses |
| `perception.py` | sounds observer, inspector (oracle form and realistic form), `plumbing.` answerer and its dialogue routes, invariants |
| `seen.py` | realistic-perception helpers: stream/temperature/fill words, amount estimates, held thermometer / moisture meter readings |
| `systems.py` | time systems `plumbing_liquids.water` (order 100, every substep, exact idle probe `water_idle`), `.slow` (101, period 120 s), `.census` (102, period 600 s); `episode_start` hook |
| `furnish.py`, `scenarios.py`, `skills.py` | furnishers (incl. the late `plumbing_liquids.reach` pass and the generation-2 `plumbing_liquids.household`), scenario helpers, reference skills |

Fidelity: P-approx throughout (lumped volumes and flows with real magnitudes), G for placement/reach/slopes,
S for controller modes. Hidden ground truth uses `_` keys (`_leak`, `_clog`, `_worn`, `_hot_handle`, `_seized`,
`_supply_seized`, `_nonpotable`, `_defect`, `_mix`) and is discovered with `inspect` (often `where="closely"`),
`read`, the thermal sensor, sounds, the water meter, or by asking (`plumbing.where_noticed`).

---------------------------------------------------------------------------------------------------------------

## 0. The model in one page

* **Supply.** `utilities.water`, `utilities.main_water_open` (kept in sync with the `main` valve), and per-fixture
  line factors: a fixture's hot or cold line flows only if the main, every stop valve whose `feeds` names the
  fixture (`line` hot/cold/both), its own `settings.supply_valve` (toilets) and, for hot, the water-heater inlet
  stop (`feeds_hot`) are open. Gate valves pass most of the flow until nearly closed (`min(1, 2.2 x position)`).
  `kernel.physics.water_available(w, fixture)` honours all of it (hook `water_available`), so other domains'
  washing actions stop when stops are closed.
* **Faucets.** Each tap is a record with `faucet` + `settings` + `control_kinds` (physical controls: core `set`
  refuses them - use `turn_on_water`/`turn_off_water` or mechanisms' `turn`/`move_lever`). Flow = handle opening x
  `max_lpm` x line factors; outlet temperature mixes 12 C cold with tank water that only arrives after `warm_up_s`
  (10-40 s) of hot flow. Derived fields: `water_running`, `flow_lpm`, `outlet_temp_c`, `dripping`, `drip_lpm`.
* **Basins.** `basin_l` to the rim, `plug` open/closed (`plug_kind` popup | basket (needs a seated `sink_stopper`) |
  toe_touch | none), `drain_capacity_lpm` reduced by clogs (`_clog` {kind, amount} shared with tools_vehicles'
  `snake_drain`; cleaning's float `clog`), overflow outlets (`overflow_hole_lpm` above `overflow_level`), displacement
  by items in the basin (material volume = mass / density, Archimedes), overflow onto the floor (spill, wet cells,
  `flood` from 4 L per source), opacity of soapy/dirty water deeper than 4 cm (`opaque_cover`: contents hidden).
* **Liquids in containers.** `liquid`, `liquid_l`, `capacity_l` (basins: `basin_l`), `fill_line_l`, `temperature_c`,
  hidden composition `_mix` {liquid: L}, dissolved products `cl_chem` {product: ml} (cleaning's solution model)
  with derived `soap_ml_per_l` and `chem_ml_per_l`, `potable`, `filled_s` (staleness clock: water older than 24 h
  is not drinking water; containers left with water/sugary drinks grow `mildew`), `solids`, `ice_g`/`ice_cubes`,
  `foam_l`, `fizz`. A container emptied to 0 L keeps `liquid` as the name of the residue it holds.
* **Where water goes.** Every liter is accounted: `utilities.water_l` and `plumbing.meter_l` decrease/increase;
  floors get spill entities (cleaning's `spill` type when loaded, else `generic_item` category spill) and
  `rooms.<id>.wet_cells`; outdoor grass/soil soaks it (`plumbing.ground_soak_l`), hardscape puddles (ice at or
  below 0 C); storm drains and gardens record pollution.

---------------------------------------------------------------------------------------------------------------

## 0b. Tier-1 adoption (kernel v3.0 physics, camera and body; v3.1 sweep)

What changed in the adoption round (docs/design/TIER1_INTEGRATION.md section 6, "plumbing_liquids" and "Every domain
also"), and how this domain now meets the kernel's physics, cameras, body and timeline.

**Physics (TIER1_PHYSICS 10.3: route through the kernel, guard the legacy code, register hooks, report ready).**

| Law (laws.json) | What this domain did | Ready to flip |
|---|---|---|
| `carry` | the legacy carry hook (`leaks._carry_hook` on move / approach / walk_to) returns at once while the law is active; while it is deferred it runs only when manipulation's model did not (`carry_slosh_applied`, read only by this deferred path) and uses the kernel's model: `phys.liquids.slosh_spill_l` on the domain's capacity and `carry_motion` on the step's motion record (`actions.motion_record`: the effective speed; stairs x1.8 until navigation registers `phys.carry_motion`); no slosh without travel; an ice tray sloshes cell by cell (3.5 cm cells, 2.5 cm walls); the `phys.settle_extra` hook `tray_follows_its_water` keeps a tray's `cell_fill` equal to its water when the kernel's law moved it | yes |
| `open_spill` | `ice.tray_placed` only writes `tilt_deg` (8 degrees on a soft or lumpy support) and the `uneven` flag while the law is active; the kernel's law spills from the tilted tray; the same settle hook records what ran out of a tray in a freezer as `frozen_spill_l` on the freezer (or the shelf item), as the deferred rule does | yes |
| `phase` | `ice.freeze_step` freezes ice trays only (their cells, cubes and slabs are this domain's model) while the law is active; every other container freezes by the kernel (`ice_frac` in `liquid_l`, its latent plateau and sealed-container burst rule). While the domain freezes a holder it owns the plateau (`thermal_owner: "plumbing_liquids"`, handed back once frozen through or taken out), so neither the kernel thermal system nor the phase law touches it; a half-frozen tray taken out of the cold thaws back | yes |
| `handle` | no copy of the law here; the handled containers carry `handle_rating_kg` (bucket 25, watering can 12, pitchers 4, plungers 3) well above what they can hold (19 / 7.5 / 2 L) | yes |
| `thermal_tau` | the time-constant override of a holder with floating ice (`ice.ice_tau_s`: the kernel knows `liquid_l`, not this domain's `ice_g`) is the lumped law with the ice counted while the law is active and the legacy formula before; it is marked `_tau_by_plumbing`, never replaces a type's or a setup's own `thermal_tau_s` (an insulated tumbler) and goes with the ice. Releasing cubes now depends on how long a frozen tray has been out of the cold (`fresh_from_cold`, `_warm_s` < 90 s), not on the bulk temperature, whose time constant the law sets (17 min for a full tray under the lumped law, 4.5 min under the legacy formula) | yes |
| `overflow` (active) | this domain never leaves liquid above a holder's `capacity_l` (basins run over at `basin_l`, below their `capacity_l`; a joined tray holds at most 0.423 L of its 0.42 L) | - |

Verified: the unit tests and both spec files (5 seeds, own modes) pass with each of the five laws forced on
(`lawbook.forced`) and with all five together. Transfers (`do_pour` stays the verb, TIER1_PHYSICS 5.7): the pour rate
is the commanded rate (slow 0.06, normal 0.2, fast 0.5 L/s) capped by the source's opening
(`transfer.pour_opening`: a narrow neck 0.25, a spout 0.5, a wide mouth 2 L/s) times the viscosity factor of
liquids.json (`common.flow_factor`: min(1, sqrt(20 cP / viscosity)); dish soap and detergent about 0.26, oils 0.58;
a liquid liquids.json has no row for keeps this domain's factor); only unfrozen liquid pours (`liquids.free_l`) and
the ice stays (`ice_frac` grows; a source holding only ice, or a few drops of melt, refuses: "What is in it is frozen"); temperatures mix by heat capacity, the vessel's body included (`liquids.mixed_temp_c`,
the rule of `phys.thermal.mix_in`: 85 C coffee into a 21 C mug settles near 72 C); a pour or a tap fill runs the
kernel's thermal-shock check of the receiving vessel (`phys.thermal.pour_shock_check`, law-gated: it cracks a cold
glass under boiling water once `thermal_shock` is active). Densities come from liquids.json (`common.density_kg_l`;
the domain table lost its density column). Absorption: `wring` and the absorbers under a leaking sink or a drip use
the kernel's capacity (`phys.liquids.absorb_capacity_l`: absorption x dry mass, a 0.55 kg bath towel about 1.4 L; a mop,
whose record describes its plastic handle, keeps this domain's 1 L) and the kernel's `wet`. `python
tools/physics_audit.py --data` attributes no data gap to this domain. Spills stay this domain's records of cleaning's
`spill` type, the same entity cleaning's `phys.spill_sink` makes of a kernel floor spill: they are not routed through
`physics.spill`, since its 5 ml droplet limit drops the 1-5 ml a slow leak adds per substep and the flood accounting
(`wet_cells`, `floor_hazards`, the downhill slope) is this domain's.

**Camera (geometric mode; room mode unchanged).** Visual occurrences go through `camera.visual_event` (the event log
is the same in both modes; the geometric camera logs whether the robot saw it and a seen one becomes a "visual"
notice): a new spill, a basin or toilet starting to run over, a spraying leak starting, a tap starting to drip
(2 cm: only a close camera catches it) and a ceiling stain starting to drip. `"view"` blocks where the category
defaults mislead: the toilet opens at the top (bowl with the seat lid up, tank with its lid off), a water heater
stands 10 cm off the wall (look behind it) with 3 mm rating-plate print, a shower stall's glass passes 60 % of the
view, a water meter's register digits are 5 mm (read from about half a metre), a controller's program lines 6 mm and a
filter cartridge's print 3 mm. Under-sink cabinets keep the core cabinet defaults (front opening, no interior light:
a wrist peek's LED or the headlamp lights them). The sounds observer is not camera-gated (sound); the inspector is
action-driven (the kernel's inspect and read checks apply). Floods and puddles are spill entities, so the camera
shows them only where its frames reach; the room fields `wet_cells` and `floor_hazards` stay hidden.

**Body.** Filling or pouring into a container the robot holds stops where its hand reaches its holding limit
(`transfer.hand_room_l` over `body.hold_capacity_left`; one hand holds about 8 kg, a bucket and 7 L: "stopped where the
hand could hold no more"; poured from another held container only the receiving hand counts). Water sprayed over the
robot by a coupling or a hose worked on under pressure goes through the body (`body_faults.degrade_camera` for the
lens, `body_faults.expose_water(w, "spray", s)` for the IP54 body); a running sprinkler whose throw reaches the robot
sprays it every substep (`outdoor.sprinkler_spray`: about 10 minutes in total are harmless, then water ingress). The
robot is never inside a running shower (a stall blocks its cell). `spray` and `flush` declare `timing="commanded"` (a
commanded spray time or handle hold is not stretched by careful speed). No verb of this domain moves the robot (its
walks are core verbs), and no robot field is declared. Optional `soil_hand("water")` is not used: the body has no drying
for hand soil, so water on the grippers would stay slippery forever.

**Sweep.** Notices (`common.notice`, from the time systems only; kinds registered here): "drip" (45 dB at 1 m: a tap
starts dripping, a slow leak starts running, a ceiling stain starts dripping), "water_spray" (65 dB: a burst pipe, a
snapped stem, a split hose or an open supply line starts spraying) and the kernel's "splash" (a basin or toilet starts
running over). A spray-rate leak shows `leaking: true` (the kernel's glance tier) while it runs, the report vocabulary's
leak evidence; a slow leak shows only to a close look and by the wet things around it. Routes for the `plumbing.`
answerer (CONVENTIONS 8): `plumbing.main_shutoff` (where, about the main water shutoff or the main valve itself),
`plumbing.where_noticed` (where, about water, a leak, a puddle or a stain, asked of a person who noticed it),
`plumbing.watering_rules` (how / when / ok, about watering, hosing or sprinklers). The water system has an exact idle
probe (`systems.water_idle`: the heater settled, every tap shut and not dripping, basins empty, toilets full and calm,
spigots and hoses slack, no controller watering, no leak running, nothing freezing, melting, foaming, fizzing or
crossing the hot-hazard line, nothing dripping, no sprinkler on, the meters current); a test runs waits with probes on
and off on generated and busy houses and compares the state digests. A house with a dripping tap (about a quarter of
generated houses) is never idle: the drip's water goes through the meter every substep. Generation 2
(`plumbing_liquids.household`, order 93, returns at once for generation 1): a home without gas service gets an electric
water heater (generation 1 keeps its draw, see section 9), and in winter 70 % of households have put the garden water
away (hoses disconnected, drained and coiled, standard spigots shut inside and drained, sprinklers off). Report
vocabulary words need `data/vocab/plumbing_liquids.json`, a kernel file (requested; the core vocabulary's `leak`,
`water_damage`, `flooding`, `condensation`, `clogged` and `stuck` cover this domain's findings today). No spec of this
domain used privileged waits (M-WAITS); every spec passes on generation-2 houses and declares `"house": {"gen": 2}`.

**Specs.** The weather ops of the two hose specs set the gust and no precipitation (a generation-2 house keeps its
generated 10.2 m/s gust under a 1-2 m/s wind otherwise: on seed 97 the outdoor_plants wind model moved the coiled hose
1.1 m and the mechanisms wind slams counted 99 `damage`). The three perception specs
declare `"camera": "geometric"` (looking is the skill): the under-sink check crouches and peeks inside each dark
cabinet before the close looks and needs both cabinets in `camera_log.looked_in`; the stain check looks up at each
stain from close by and needs both identified in `camera_log.sightings` (`track_view`); the faucet check takes a close
wrist-camera look at each spout (`peek closely`) and needs both in `camera_log.peeked`. Each has a wrong plan marked
`"camera": true` that reports the right answer without looking and fails.

---------------------------------------------------------------------------------------------------------------

## 0c. Phase 1 (stabilise) after the independent review of 2026-10-22

What the review (docs/reviews/2026-10-22_independent_review) found in this domain's code, and the root fix. Each has
a regression test in `tests/test_plumbing_liquids.py` (`ReviewPhase1`, plus the tests named below).

| Finding | Fix |
|---|---|
| L3 / p2_gaming_a: a bread box, a colander and a napkin holder "held" 7 L of bleach mop water; the loaf in the bread box ended wet with no contamination | `liquids.holds_liquid` keeps a vessel only when its walls are liquid-tight (`liquids.liquid_tight`): an explicit `watertight` decides; `perforated` or `holes_mm` (a colander), `porous` or `soft_sided` never hold; a container that opens at the front, side or back (its record's or its type's `view.opening`: the roll-top bread box) cannot pool liquid; a `dispenser` holds liquid only when it dispenses one (`product`, `product_tag`, `liquid`, `foaming`, `dispenser_spigot`: not a napkin holder). Over all registered types exactly the bread box, the colander and the napkin holder changed. A solid ceramic fruit bowl still holds water (it does). Poured over a container that cannot hold it, the liquid runs over it and its contents ("it ran out over it (it does not hold liquid)") and on to what it stands on; poured into a vessel, it reaches what the vessel holds. Either way `transfer.reached_contents` contaminates food and food-contact things it reaches, and a food-contact holder, by what the liquid carries (`transfer.contamination_of`: sewage and toilet water germs and biohazard, dirty water, mop water and condensate germs, a chemical its chemical; plain, soapy, hose and rain water nothing); only a liquid that ran over them wets the contents (plain water poured into a pot of pasta stays the food domain's business) |
| M2 (p4_arch 2.2): the pour changed behaviour when a module named `house_sim.systems.cleaning` hooked `pour` | `transfer.solution_carried_by_hook`: the dissolved-solution model's owner stamps `ctx.data["cl_src_l"]` in its `before("pour")` hook and moves the solution after the pour; with the stamp this domain carries only liquid (and leaves `cl_soil_g` to the hook too, which it used to count twice: once in the parcel and once in the hook), without it this domain carries the solution itself. The parcel still knows what it carries (`named_chem`), so bleach mop water poured with cleaning loaded is `mop_water` everywhere (the kitchen-sink rule, spills, contamination), as it was without cleaning. After the hook (`after("pour", order=130)`, `transfer.solution_moved`) the name, potability and strengths of both containers are re-derived (`liquids.refresh_derived`). `common.after_hook_present` is gone |
| M9 / VERIFY_HARDENING_DECISIONS 3: exogenous harms (a pre-existing leak, a scenario's running tap, a freeze) raised `flood` and `damage` and so failed tasks | One rule (`common.harm_by` / `common.harm`): whatever a robot action does is the robot's (the kernel's action phase); a consequence of the time systems is the robot's only when the robot set up its cause: a robot action made the leak (`_leak.by: "robot"`, `common.robot_leak`: snapped stems, an unscrewed live appliance hose, a hose punctured on a nail, strained appliance hoses), arranged the source (the kernel's `_robot_moved` on it or on what it rests in: a cold drink it set on wood, a hose it handled and left full), or opened, turned up or plugged a water control that feeds it (`_robot_water` {"flow" / "plug" / "supply"} on the tap, basin, toilet, valve or spigot: written by the `setting_changed` hook `fixtures.robot_water_setting`, `plug_drain` and `flush`; cleared when the control is shut, the plug pulled or a flush's refill done; `common.water_controls` lists the controls of a source: a supply-side leak runs whatever its fixture's tap does, so only the stops, the heater inlet and the main count for it). Exogenous harms are counted in `plumbing.exogenous_harms` {flag: n}; floor water keeps its per-source total in `plumbing.floor_water` and the robot's share in `plumbing.robot_floor_water`, whose crossing 4 L raises `flood`. A spec about stopping an exogenous harm bounds `plumbing.floor_water.<source>` or the damaged thing's fields (all this domain's specs already do). The spills rule is unchanged: `spills` counts what an action spills; water the time systems put on floors is flood accounting |
| Leak water was "caught" by a fixture standing on the drip cell (a sink basin above a snapped under-sink stop) | `leaks._catcher_at` on a floor cell takes only a portable catch container (bucket, pan), never a basin, toilet or mounted fixture; a snapped stop inside a cabinet sprays into the cabinet (`common.leak_landing`) |
| Realistic `shut_off` / `open_valve` messages gave the turns to 0.01 (the hidden `turns_to_close` and a part-open valve's position to 1 %) | `seen.turns_value` / `turns_word`: turns counted to the nearest half turn ("about 2 turns", "a quarter turn"), and the action's seconds follow the counted turns. Turning a valve still tells a gate main (about 6 turns) from a fixture stop (about 2.5): a hand counts turns, a legitimate tactile percept |
| The perception audit's physics rule read this domain's stain description in the physics inspector's `looks` key | The stain description is `stain_look` |
| T-3b1a4024d46110-01 failed on seed 97 with the geometric camera: `plumbing_liquids.goto` approached a sink base it had never seen in a wet kitchen | `skills._into_view`: in a wet room with the geometric camera it first walks to the nearest viewpoint that sees the target (`sight.best_viewpoints`), dry when it can, carefully through the water otherwise, as the core approach skill does in dry rooms |

Look-alike groups: no LookalikeWarning names this domain's groups. A twin probe of `water_valve` (main vs fixture stop)
and `rubber_plunger` (flange vs cup) found identical glance records, refusals and clocks for every non-resolving action
in both camera modes except the counted turns above and, with the geometric camera, the `view.px` / `elev_deg` of the
members' true sizes; the phase-1 finish (section 0d) removed the size spreads at the root (same-size plungers, the
main in a group of its own), so neither group is in `registry.lookalike_spreads` any more.

## 0d. Phase 1 finish: the domain's breakage rows and red-team findings

`catalog/analysis/phase1/breakages.json` rows owned by this domain, the red team's look-alike size finding
(`red_leaks.json`, review L2 residual) and cleaning's request of this phase. Regression tests:
`tests/test_plumbing_liquids.py` `Phase1Finish` (each fails on the phase-1 tree, except the guard that spouted and
loose-lidded vessels still pour with the lid on) and `CounterSpaceAndPipes.test_look_alike_groups_show_one_size_at_a_glance`.

| Finding | Fix |
|---|---|
| Cleaning's request: pouring from a closed trigger sprayer or pump head poured its 0.75 L, unlike every closed container | `transfer.closed_for_pouring`: the kernel's closed container (`phys.liquids.container_closed`: a closed lid or cap, a seal, a closed zipper or closure, a trigger or pump head) pours nothing from `pour`, `empty` and `fill` (from a held container: `fill` never checked its source before); an open pour spout (a carton, the filter pitcher) and a spouted or loose-lidded vessel whose only closure is its lid (a teapot or kettle) still pour. One refusal for all of them, "Open it first", so a look-alike's members (every `spray_bottle` member has a trigger) answer alike |
| Electrical's request: this domain's spills and splashes wrote `wetness` directly, so no `phys.wetted` hook ran (T-686df131c7c736-01 seed 1: three appliances on the counter reached wetness 0.5, a breaker tripped 8 s later and the fridge's power loss read as exogenous) | `common.wet(w, eid, liters, liquid, source)` is the kernel's `phys.liquids.wet` (a soaking body saturates, anything else takes a film of at most 1.5 mm over its top; every `phys.wetted` hook sees the litres and the source). Every wetting of this domain goes through it with the litres that reach the thing: a spill on a surface reaches what stands on it by its footprint's share of the top (`common.share_on`), a pour over a thing or a container's contents all of it (shared among the contents), a tap stream splashes about a third of the outer film (at most 10 ml), a shaken drink sprays a quarter of its spray onto what the other hand holds (`liquids.split_parcel`), an overflow runs down the basin's cabinet, a tilted ice tray onto its shelf, drips onto what they land on (gear and water-sensitive things are no drip catchers, whatever their case: `leaks._water_sensitive`), and a hose spray onto its target (`outdoor.rinse_entity`, which then leaves the faces it hit drenched as before). A water-sensitive thing is water damaged only when at least `common.WATER_DAMAGE_L` (0.02 L, electrical's `INGRESS_L`) reached it or gathered on it: a glug's few ml beside a toaster wet it a little and damage nothing, a litre run over the counter does |
| Food's request: dumping a bucket onto a slice of bread "emptied" the litres into it (`liquid_in_bin_l`, `leaking`) and they vanished | `transfer.is_bin`: a bin is a record with a `waste_stream` that core takes for a real waste container (`core.is_waste_container`: waste-container types and categories that accept items, `waste_bin`, wheeled carts); food, mail, packing material and batteries carry `waste_stream` only as the stream they go to. The bucket now wets the bread, contaminates it by what it carries and runs off onto the counter and the floor |
| Kernel holders: the garbage disposal pooled kernel pours, drips and spills (it drains) | Type data: `garbage_disposal` `watertight: false` (kernel `holds_liquid` and this domain agree). This domain's `pour` / `empty` into the disposal go in through its sink's drain opening (`transfer.disposal_sink`: the disposal hangs from the sink and the sink names it), not onto the cabinet floor |
| Red team novel sweep (review I4): `fill` told an unfamiliar kettle and bleach jug ("Hold the container you are pouring from") from the rest ("That is not a water or liquid source") | `pour`, `empty` and `fill` declare `operates=True` (the kernel refuses an unfamiliar target before any hook) and refuse an unfamiliar source (`fill`) or destination (`pour`, `empty`) with the kernel's `NOVEL_OPERATE_MESSAGE` (`seen.refuse_novel`); oracle mode is unchanged |
| Red team / review L2 residual: look-alike members differ in true size behind a group `size_m` (rubber_plunger 0.45-0.55 m, water_valve 0.06-0.12 m), so the geometric camera's elevation, occlusion and pixels told them apart | Cup and flange plungers are both 0.15 x 0.15 x 0.5 m (the group's size; real cup and flange plungers both come with 0.45-0.55 m handles; the flange's extra rubber stays in its mass and volume). The house main is really larger than a fixture stop (a 3/4 in gate or ball valve on the service line against a 3/8 in angle stop), so it has its own group `main_water_valve` ("large water valve", its true 0.12 x 0.12 x 0.15 m, resolved by `inspect`; the type's perception `looks_like`), and `water_valve` shows the stops' true size 0.06 x 0.06 x 0.08 m and no longer lists "main water shutoff" among its candidates. `registry.lookalike_spreads` lists none of this domain's groups |
| Cleaning's phase-1 request: the realistic inspector felt a vessel's temperature (and granted the touch) on any close look, also one the geometric head camera makes from out of arm's reach (the kernel's grant gate refused the touch, so strict mode raised), and only for vessels holding litres, so a product bottle (an amount) was felt unlike its look-alike refill | Every feel of `plumbing_liquids.details` (the stream, the basin water, the vessel, a damp board pressed) needs a hand on the thing: `seen.can_touch` asks the kernel's grant gate (`Realism.grant_problem` for a touch) first, and a close look from afar feels nothing. A product bottle holding an amount is felt like any closed vessel (`perception._product_amount`) |
| Generation-2 layout follow-ups: a shower stall stood in front of a window blind (apartment seed 29) and under an upper cabinet (house seed 20 with laundry room, basement and nursery) | `furnish.furniture_keeping_reach` also refuses a spot in front of a wall mount the piece would hide (`furnish.hidden_cells`: the layout audit's "hidden mount" rule), judged per try so the spots are shuffled with the same randomness as before; when the required stall fallback would still stand under a wall cabinet or hide a mount (`furnish.unsound_spot`) in a house with another bathroom, that cramped bathroom goes without (`_ensure_bathing` puts the plain stall back if no bathroom of the house has a tub or shower). Only those two audit houses change; every generation-1 house and every authored spec house (generation 2 included) keeps its placements |

Generation-1 digests: the type-data changes alter record fields of generated houses (never a placement): `size_m` of
`cup_plunger` and `flange_plunger`, and the new `watertight` of `garbage_disposal` (requests in
`catalog/analysis/phase1/finish_plumbing_liquids.json`).

---------------------------------------------------------------------------------------------------------------

## 1. Object types (43) and realistic magnitudes

| Type | Key fields (defaults) | Notes |
|---|---|---|
| `plumbing_fixture` | `plumb.kind` | base; the `plumb` marker means this domain simulates it |
| `kitchen_sink` | basin_l 32, drain 30 L/min, plug basket, strainer (`strainer_g`), faucet single_lever 6.8 L/min (1.8 gpm), `sink_zone` kitchen, `food_prep` | mounted over its base cabinet (`cabinet` link) at counter height; stops and trap inside the cabinet (shelf 0), the disposal installed in it (below) |
| `bathroom_sink` | basin_l 7, overflow 4 L/min at 85 %, drain 18, plug popup, faucet two_handle 4.5 L/min, marks {left: hot}, hidden `_hot_handle`, `_worn`, `_nonpotable` | furnisher makes 45 % single-lever, 15 % reversed plumbing, 12 % worn washer, 10 % non-drinking tap |
| `utility_sink` | basin_l 45, drain 30, two_handle 8.3 L/min, `accepts_wastewater`, free-standing on legs (`mount_height_m` 0.55, `blocks_path`) | laundry tub: a right place for mop water; as an obstacle, later furniture keeps a reachable cell beside it |
| `bathtub` | basin_l 190, overflow 18 L/min at 75 %, drain 25, toe_touch, spout 15 L/min, `settings.diverter` tub/shower (gravity diverter drops back) | |
| `shower_stall` | thermostatic valve (temp_c 20-49), 7.6 L/min, pan 25 L, door `open` | |
| `toilet` | tank_l 6 (1.6 gpf), bowl_l 1.9 (normal), bowl_rim_l 6.5, fill 9 L/min, `settings.supply_valve`, `_supply_seized`, `_tank` (tools_vehicles contract incl. `fill_valve_ok`, `stuck_lpm`), `_clog`, `cover_open` (tank lid), `open` (seat lid), bowl `inner_floor_m` 0.25 | `flush_handle` so mechanisms' levers flush it |
| `outdoor_spigot` | faucet spigot 18 L/min, `settings.valve`, `frost_free`, `interior_valve` (standard), `drained` | mounted 0.45 m on the house wall |
| `shutoff_valve` | `valve_kind` multi_turn_stop/quarter_turn_stop/gate/ball, `turns_to_close` 2.5, `settings.position` 0..1, `valve_open`, `line`, `feeds`, `feeds_hot`, `_seized`, `mech.rotary` (mechanisms' `turn` works) | |
| `main_shutoff_valve` | gate 6 turns (or ball 0.25), `main`, label_text "MAIN" | 0.12 x 0.12 x 0.15 m, larger than a 0.06 x 0.06 x 0.08 m fixture stop: its own look-alike group `main_water_valve` (section 4b) |
| `water_meter` | `display` (cumulative L), `flow_indicator` | readable with `read` |
| `water_heater` | tank_l 190, `tank_temp_c`, `settings.temp_c` 49 (dial), recovery 9 kW gas / 4.5 kW electric; wall-hung `tankless` units (`furnish.TANKLESS`: 0.5 L exchanger, 44 kW gas / 24 kW electric) | drawing hot water cools the tank; it reheats. A tankless unit delivers the setpoint on demand while it has gas/power and stores nothing (cools within ~5 min of a cut). Tanks stand in a basement, utility closet, garage, or a laundry room/pantry of at least 6 m2 that still has a washer spot; never in a kitchen - otherwise the house gets a tankless unit |
| `water_pipe` | exposed run; `condensation_prone`, may hold `_leak` | runs along its wall at 2.1 m (`furnish.lay_pipe`: rot 90 on the west/east walls; a run longer than its wall - 2 m in a 1.5 m utility closet - is cut to the wall's length, mass in proportion) |
| `drain_trap` | P-trap under a basin (drain-side leaks) | |
| `floor_drain`, `storm_drain` | `floor_drain` (sanitary), `storm_drain` ("NO DUMPING - DRAINS TO CREEK") | |
| `garbage_disposal` | `_jam` (tools_vehicles' `reset_disposal` contract), switchable, 5.5 kg, `watertight: false` (it drains: no liquid pools in it; a pour into it goes down its sink) | hangs from the sink's drain flange: an installed part of the sink base (`relation` part, cabinet `slots.disposal` access open, `hangs_from` the sink, `z_m` ~0.3 m), so it is hidden while the doors are closed, cannot be picked up, and never loads the cabinet shelf (admin_inventory's shelf rating counts only items `in` it); the sink's `disposal` names it |
| `irrigation_controller` | `driver` plumbing_liquids, settings mode auto/off/rain_delay, rain_delay_h 24/48/72 (app controls: core `set` works), `zones`, `program`, `rain_sensor`, `delay_until_s`, `display` | |
| `sprinkler_head`, `oscillating_sprinkler` | outdoor_plants emitter contract: `irrigation_emitter`, `on`, precip 40 / 12 mm/h, radius 4 / 6 m, 7.5 / 12 L/min | the oscillating one attaches to a hose |
| `rain_barrel` | 200 L, rainwater (non-potable), bottom spigot (`dispenser_spigot`) | |
| `freezer_ice_maker` | on + `settings.arm` down, 115 g per 90 min (~1.8 kg/day), bin 3 kg, needs its water-line valve, freezer <= -10 C, unobstructed | `status` (indicator text) and the canonical derived `_plumbing_liquids_status`, refreshed by the slow system; `inspect` computes it live |
| `ice_storage_bin`, `ice_cube_tray` | tray 14 x 28 ml, `cell_fill`, `frozen_frac`, `joined`, `tilt_deg`, `cracked`, snap-on lid (`open`) | |
| `garden_hose` | 15 m, 3 L inside, `attached_to`, `nozzle`, `pressurized`, `kinks`, `coiled`, `coupling_washer`, `cross_threaded`, `joint_spraying`, `grit` | one-hand carry (2.5-5.5 kg) |
| `hose_nozzle` | settings pattern (jet/full/flat/shower/cone/mist/soaker/angle), trigger, `washer` | |
| `hose_hanger`, `hose_reel` | `hose_storage` (placing a hose `hung` coils it) | |
| `hose_washer` | part_type hose_washer_3_4 | |
| `flange_plunger`, `cup_plunger` | `plunger_kind` flange/cup, handles; neutral labels "flange plunger" / "cup plunger" (look-alike group `rubber_plunger`); both 0.15 x 0.15 x 0.5 m (flange 0.45 kg / 2 L, cup 0.35 kg / 1.5 L) | |
| `sink_stopper` | `stopper_for` basket | |
| `utility_bucket` (19 L, fill line 15), `watering_can` (7.5 L, rose), `water_pitcher` (2 L), `drink_pitcher`, `filter_pitcher` (reservoir 1.1 L -> 1.4 L at 0.12 L/min, cartridge slot, indicator), `pitcher_filter_cartridge` (150 L, `discard_fills`, `hot_damaged`), `water_bottle` (750 ml, cap = openable; residents' bottles carry a read-tier `name_label` sticker, label "water bottle"), `water_carafe` (1 L), `drink_dispenser` (7.5 L with spigot) | liquid containers with `has_handle`/`handle_rating_kg` where they have handles | |
| `appliance_supply_hose` | behind its appliance (relation behind), hose_kind, `slack_m`, date stamp, hidden `_defect` | |
| `water_stain` | ceiling/wall stain: `stain_m2`, `wet`, `dripping`, `ceiling_water_l`, `moisture` | |

Liquids (`common.LIQUIDS`): water, filtered/distilled/ice water, soapy/dirty/mop water, condensate, hose water,
rainwater, toilet water, sewage, milk, oat milk, juices, lemonade, iced tea, tea, coffee, soda, sparkling water,
beer, wine, sparkling wine, sangria, broth, cooking oil, grease, dish/hand/foaming soap, detergent, softener, bleach,
drain cleaner, liquid fertilizer, fertilizer solution, vinegar, rinse aid - each with density, pour-rate factor,
potable/drinkable/alcohol/foam/grease/chemical/biohazard flags and the residue it leaves.

---------------------------------------------------------------------------------------------------------------

## 2. Actions (15 verbs)

| Verb | Parameters | Preconditions (refusal category) | Effects | Duration |
|---|---|---|---|---|
| `turn_on_water` | target tap/spigot/nozzle; flow 0.05-1 (0.6); temp cold/cool/warm/hot; mix -1..1; temp_c; handle left/right/both; open; diverter tub/shower | reach (precondition/capability); it must have a tap (argument) | sets the handle settings via `apply_setting` (fires `setting_changed`); "no water comes out" when supply is shut; two-handle `temp=hot` opens the handle MARKED hot; scald check for people `using` it | 2 s |
| `turn_off_water` | target; handle left/right/both (both); force gentle/normal/firm | reach | closes; a worn washer keeps dripping; force=firm on compression (two-handle, spigot) crushes the washer: `damage`, bigger drip; gravity diverter drops to tub | 2 s |
| `plug_drain` | target basin; tool (sink stopper) | basin has a stopper (argument); basket drains need a held stopper (capability) | plug closed (stopper attached) | 3 s |
| `drain` | target basin / garden hose / container with drain spigot; tool (tongs/hook) | plug closed; hose: spigot off and pressure relieved (precondition) | opens the plug; groping for a basket stopper in murky water with a sharp item in it = `safety_violations`; hose: laid out straight (kinks 0 if the room is >= 6 m) and emptied where you stand | 4 s / 60 s |
| `fill` | target; source tap/dispenser/container/ice source; amount_l / to_frac / until fill_line\|brim; temp; flow; count; tool; keep_running | container held at the tap or in its basin (precondition); open (precondition); a source container held and not closed (precondition, as `pour`); an unfamiliar target or source: the kernel's identify-first refusal (precondition) | opens a closed tap at temp/flow and closes it after; water identity (hose water from a spigot hose, filtered water and carbon fines from filter hosts, bathroom-tap truth); fill error +-2 % slow / 5 % full flow (`until=brim` only overshoots: the rim is the stop signal, so a brim fill runs over / joins tray cells); overflow into the basin or onto what it stands on; ice trays set `cell_fill` (> 100 % joins the cells); filter pitchers fill the reservoir (hot water damages the cartridge); basins (no source): attended fill from the running tap, closed at the level or after 90 s if the drain is open; ice: count cubes with a scoop/tongs, else the glass (chips, glass shards in the bin) or a hand (bacteria), displacement overflow | 2 s + volume/flow (realistic: whole seconds, see 4b) |
| `pour` | target held source; destination; amount_l / to_frac / until; rate slow/normal/fast; spout up/down; through funnel/strainer; tilt_glass; hold_lid | source held and not closed (precondition: "Open it first" for a closed lid, cap, seal, zipper or closure and for a trigger or pump head; an open pour spout, a spout or a loose lid pours with the lid on); an unfamiliar source or destination: the kernel's identify-first refusal (precondition) | see transfer model below; food liquids (cartons with `food`) delegate to food's `transfer` | 2 s + volume/rate (realistic: from the estimated volume, whole seconds; see 4b) |
| `empty` | target; destination; force | held, open (precondition); destination given (argument) | pours everything; ice trays release cubes (cold tray + firm = cracked, gentle/normal leave some stuck; over the floor: a melting slip hazard); `target=sink` dumps the strainer into a bin | 5-10 s |
| `wring` | target wet cloth/towel/mop; destination; method hand/wringer | wet enough (precondition) | water (dirty/mop water if soiled/chemical) into the destination or onto the floor; wetness 0.3 / 0.15 | 6 s |
| `shut_off` | target valve / fixture / toilet; force; turns; direction cw/ccw; method valve/float | reach; stops must be visible (open the cabinet: perception); seized stems refuse unless force=firm (precondition) | closes; partial turns, ccw does nothing; force=firm on a seized stem snaps it (`broken`, spraying `_leak`); toilet float propping needs the tank lid off | 1.5 s + ~1.2 s/turn |
| `open_valve` | target; fraction; force; method | as above | opens | idem |
| `flush` | target toilet (or hose: flush grit); hold_s (1) | | full or partial flush; clear trap siphons, a clog raises the bowl and overflows | 4 s + hold |
| `plunge` | target toilet/basin; tool plunger; strokes (15); force gentle/normal/vigorous | plunger held (capability); lid up; water over the cup (precondition) | flange 15 %/stroke, cup 2 % in a toilet; displacement overflow of a brim-full bowl; vigorous splash; object clogs pushed deeper; drain cleaner splash (`burns`, `safety_violations`); sewer backups come up the tub | 5 s + 1.5 s/stroke |
| `spray` | target object / room / floor / person; tool hose; pattern; seconds (60); face; toward away/lawn/street/drain/house; cells | hose held, connected, spigot on (resource) | removes rinsable soil per face/cell (top-down runoff, slope runoff on driveways), wets the target, open windows let water in, oil washed to the street = pollution, ice below 0 C, watering-rule violations, people/pets sprayed | seconds + 2 |
| `connect_hose` | target hose/nozzle/sprinkler; destination spigot/hose; start aligned/angled; torque hand/firm/wrench | one part held (precondition) | attaches; running water sprays the robot (camera); angled start cross-threads (joint leak); missing washer leaks | 12 s |
| `disconnect_hose` | target nozzle/hose/appliance hose | free hand for a nozzle (capability) | detaches; under pressure: spray + washer lost; appliance hose with live valve: spray + 8 L/min leak | 10 s |

Transfer model (`pour`): rate 0.06/0.2/0.5 L/s, capped by the source's opening (narrow neck 0.25, spout 0.5, wide 2 L/s),
x the liquid's viscosity factor from liquids.json (oils 0.58, dish soap and detergent 0.26; section 0b); requested amounts miss
by a clipped zero-mean error (1.5/4/10 %; graduated destinations halve it); a full rigid bottle/carton poured
fast, or a carton poured spout-down, glugs (5-9 % splash); narrow necks < 30 mm without a funnel lose 4-25 %;
carbonated drinks raise foam (x0.25 tilted) that runs over; sticky liquids drip down bottle lips (soil on the
bottle); a loose teapot lid falls into the cup unless `hold_lid` (needs a free hand); tea leaves/pulp pass unless
poured through a strainer; hot liquid into a vessel a person holds = `safety_violations` (and `burns` if it
spills); pouring into a glass someone covers or for a diner who declined = `plumbing_liquids.unwanted_pour`.

Destination rules (`transfer.deliver`): a vessel is a liquid-tight container (`liquids.holds_liquid`, section 0c); a
container that is not (a roll-top bread box, a colander) or a surface lets it run over it and its contents onto what it
stands on; liquid that reaches food or food-contact things contaminates them by what it carries
(`transfer.reached_contents`); grease/oil into any drain grows a `_clog`; anything but plain water into a
storm drain or onto soil/plants = pollution; dirty/mop/toilet water or chemicals into a kitchen sink contaminate it
and the dishes in it (`plumbing_liquids.food_zone`); liquid into a trash bin (`leaking`, `plumbing.counts.bad_disposal_l`;
a bin is a real waste receptacle, `transfer.is_bin`: food, mail or packing material that names its waste stream is a
surface the liquid runs over); poured or emptied into the garbage disposal it goes down the disposal's sink;
what liquid reaches gets wet through the kernel's wetting (`common.wet`, section 0d);
products: `product_tag` mismatch sets `product_mismatch`, undiluted liquid soap in a `foaming` pump sets `clogged`,
`requires_liquid` (distilled water appliances) sets `wrong_liquid`; plants delegate to outdoor_plants'
`water_plant` when loaded.

Hooks on other verbs: `place` (displacement overflow when lowering items into a full basin; hose coiling on
`hose_storage`; tray tilt on soft/lumpy supports), `pick_up` (gloves for anything in a toilet bowl - safety;
retrieving the clog object clears it; reaching into standing water next to a live plugged device = `electrical_hazard`
(`transfer.standing_water`: a flooded room, a puddle/spill at least 0.5 mm deep, a cleaning `water_film` of at least
0.5 mm, or a wet cell listed without a film depth; drips and mop films of 0.01-0.3 mm are not standing water)),
`open` (a shaken carbonated drink sprays unless its `fizz` settled ~90 s), `shake` (manipulation: sets `fizz`),
`move`/`approach`/`walk_to` (carry slosh of open containers when manipulation's carry dynamics did not run; a
connected hose cannot leave the spigot's area), `turn_on_water` / `shut_off` / `open_valve` (standard spigot drained
when its interior valve is closed and the tap opened), `pull`/`drag`/`slide`/`push`/`pull_handle` (manipulation /
mechanisms: pulling an appliance strains its supply hoses into leaks), `setting_changed` (faucets, valves, the
controller), `mechanisms.flushed` (lever flushes).

---------------------------------------------------------------------------------------------------------------

## 3. Systems

Kernel v2.1: no system scans every entity each tick. Every loop runs over `w.static_index` lists (cached until an
entity is created or destroyed; dynamic fields are re-checked on the records): `common.mine_ids`, `common.kind_ids`
(`plumb.kind`: sink, toilet, water_heater, water_meter, ice_maker...), valves (`valve_kind`), taps, hoses, spigots,
controllers, leak hosts (`_leak` key or this domain's entities: the only runtime `_leak` additions are on its own
entities), drippers (`drip_lpm` key, other domains' items), condensation-prone pipes, water-sound sources, and liquid
holders (`liquids.holder_ids`: type/key-based candidates plus `plumbing.holders_extra`, ids that received liquid at
runtime although their type gave no hint; `write_state` adds them immediately, the census finds liquid other domains
wrote directly). Benchmark (`python tools/bench_systems.py 40 0 1 2 3 --domain plumbing_liquids`): 2.13 ms per 30 s
substep before the integration round, ~0.37 ms after, ~0.30 ms in fix round 2 (water 0.26, slow 0.045 amortized,
census 0.004; whole engine ~5 ms per substep with every domain loaded). Phase-1 finish (same states, digests
compared): a shut tap with nothing to drip reads neither the supply factors nor the tank (`fixtures.tap_state`), the
stops are tabled once per substep (`supply._stop_factors`), `ice.freeze_step` looks up the ambient only for water it may
freeze, ice it may melt or a part-frozen holder, and the displacement scan is one list comprehension: 0.376 -> 0.334 ms
per substep for the three systems on one frozen tree (water 0.320 -> 0.278; ADOPT's files 0.365), a 1 h wait's plumbing
work 37.5 -> 31.8 ms with probes (details in `catalog/analysis/phase1/finish_plumbing_liquids.json`, `perf`). Every invariant (run on every step) takes the
World as a third argument (`fn(state, baseline, world=None)`): `nonpotable_drink` checks only the static index of
drinking vessels, `alcohol_minor` returns at once when no minor is present and otherwise checks indexed liquid holders
and food items, the rest compare counters in the domain section. The standing-water test after `pick_up` reads spills
from a static index.

The water system's idle probe (`systems.water_idle`, kernel v3.1) skips a substep only when no step would change
anything (section 0b); `tests/test_plumbing_liquids.py` (`Tier1Sweep`) compares states with probes on and off.

| System | Order / cadence | Work |
|---|---|---|
| `plumbing_liquids.water` | 100, every <= 30 s substep | heater recovery; taps (warm-up, touchless auto-off, scald, water use, where the stream goes); basins (drain, overflow outlet, displacement overflow - items in the basin are only scanned once it is over half full -, opacity, greasy ring after dirty water, attended-fill auto-off, mirror of `standing_l` for tools_vehicles' drain model); toilets (refill 9 L/min with 15 % to the bowl, stuck fill valve, slow bowl drain); spigots and hoses (filling, pressurizing, open ends flooding hardscape, joint leaks); irrigation controllers (program windows, rain delay that resumes, minutes in rain or on saturated soil); leaks; filter pitchers; foam; freezing/melting of liquid in containers (latent heat plateau; ~2.9 h for 28 ml cubes at -18 C); fizz decay; hot-liquid `hazards`; drips onto catchers; the water meter display |
| `plumbing_liquids.slow` | 101, `period=120` (dt = accumulated time, up to 600 s on long waits) | staleness/biofilm of standing drinks; ice makers (`_cycle_s += dt`, one 115 g harvest per 90 min cycle, loops for large dt); freeze damage (frost dose `plumbing.freeze_dose` += dt x degrees below 0 C, reset by a thaw above +2 C: hoses holding water split after 6 C*h with the air at -2 C or colder; trapped spigots burst inside the wall with a 12 L/min leak after 12 C*h while the air is at or below -6 C - an hour at -2 C does neither); condensation (a sweating pipe keeps wetness >= 0.4 and owns that film's drying: `drying_owner` plumbing_liquids + `_wet_mine` 0.4, both cleared when it stops); water rings (dt / 1800 s per period) and closed-wet mildew (`_closed_wet_s` += dt, 1 h) - one cheap pass over wet (>= 0.3) or liquid-holding entities; fallback evaporation/drying only when the cleaning domain is not loaded (skips entities with `_wet_mine` or a foreign `drying_owner`) |
| `plumbing_liquids.census` | 102, `period=600` | full pass for liquid/ice that other domains wrote into things with no liquid hint; keeps `plumbing.holders_extra` |

Every system runs inside `common.system_context()`: floor water it produces (pre-existing leaks, scenario overflows,
open hose ends, drips, melt) counts per source in `plumbing.floor_water`, never in the global `spills` flag, and
raises `flood` only from 4 L of the robot's own water per source (`plumbing.robot_floor_water`; harm attribution,
section 0c): a leak or overflow the robot did not set up is recorded in `plumbing.exogenous_harms`. Hook `episode_start` refreshes derived tap state (`water_running`, `flow_lpm`, `outlet_temp_c`,
drips) and drain rates before the baseline, so scenarios that only write handle settings start consistent.

---------------------------------------------------------------------------------------------------------------

## 4. Observation and inspection

* Observer `plumbing_liquids.sounds` (`obs["sounds"]`): water_running, basin_filling, hose_running, dripping,
  hissing_spray (bursts, joint leaks), toilet_refilling, toilet_running, drain_gurgling (slow drains, sewer
  backups), sprinkler_ticking, appliance_filling (dishwasher/washer cycles) - from the robot's room and, at 40 %
  loudness, from adjoining rooms through open doorways (`source_hint` = room label).
* Inspector `plumbing_liquids.details`: taps (`water_running`, `flow_lpm`, `dripping`, `faucet_kind`,
  `handle_marks`, `outlet_temp_c` with the thermal sensor); basins (`basin` level/plug/draining, `water` liters,
  temperature, suds, murky, dissolved products, `strainer_g`, `drain_speed` while draining, contents hidden under
  murky water); valves (handle orientation, kind; `where=closely`: crusted stem when seized); toilets (bowl state,
  supply valve, visible object with the lid up, chemical-looking water, tank state with the lid off); hoses;
  nozzles; leaks (active: severity; `where=closely` or spray: component; inactive: evidence/mineral trail);
  condensation (sweating, no drip point); stains (wet vs dry, dripping); moisture; water damage (swollen); liquid
  containers (`fill_frac`, `liquid_temp_c`, `water_age_h`, carbon specks, ice cubes, foam, pitcher reservoir and
  filter indicator); ice trays; ice makers (status); controllers; appliance hoses (`where=closely`: condition).
* Answerer `plumbing.`: `plumbing.where_noticed` (hint stored by `hidden_leak`), `plumbing.main_shutoff`,
  `plumbing.watering_rules`; canonical questions reach it through the routes of section 0b.
* Notices (kernel v3.1): "drip", "water_spray" and "splash" when the time systems start a drip, a spray or an overflow
  (section 0b); visual occurrences go through `camera.visual_event` (geometric camera only; the event log is unchanged).

---------------------------------------------------------------------------------------------------------------

## 4b. What the robot can perceive (realistic perception, kernel v2.4-v2.8)

In `perception="realistic"` (docs/PERCEPTION.md) the robot sees handles, field tiers and its own knowledge. Oracle mode
is untouched: every message and inspect result below keeps its exact oracle text; the realistic variants live behind
`ctx.realistic` (helpers in `seen.py`). Since kernel v2.8 `env.step` in realistic mode returns no evaluation or reward
(tests and verifiers call `env.evaluate()`), permission-type refusals share the category "permission", and the kernel
scrubs labels of unidentified things named in a command, so this domain's refusals use plain labels again.

**Field tiers** (type JSON `perception` blocks; the `plumbing_fixture` block is inherited by every fixture):

| Tier | Fields |
|---|---|
| glance | `water_running` (a running tap is seen from the room), `joint_spraying`; hoses also `attached_to`, `nozzle`, `coiled`; kernel defaults: `liquid_l`/`fill_level` as fill words, `overflowing`, `open`, soil/wetness buckets |
| inspect | handle `settings`, `valve_open`, `line`, `feeds`, `plug`, `bowl_waste`, `cell_fill`, `frozen_frac`, `kinks`, `pressurized`, `coupling_washer`, `liquid`, stain `wet` / `dripping` / `stain_m2` (estimate), and every unlisted field |
| read | `display` (meter, controller), `reading_l`, `program`, `remote_status`, `label_text` ("MAIN", heater and cartridge labels), cartridge `rated_l` / `discard_fills`, heater `recovery_kw`, `power_w`, a personal water bottle's `name_label` (whose bottle it is) |
| measure | `flow_lpm`, `drip_lpm` (measuring_cup), `outlet_temp_c`, `temperature_c`, `reservoir_temp_c` (thermometer), `cl_chem`, `soap_ml_per_l`, `chem_ml_per_l` (test_strip), `ice_g` (scale), `length_m` (tape_measure), stain `moisture` (moisture_meter) |
| hidden | `_leak`, `_clog`, `_worn`, `_hot_handle`, `_seized`, `_supply_seized`, `_nonpotable`, `_defect`, `_mix`, `_robot_water` (underscore), `faucet` (max L/min, warm-up), `mech` (rotary turns per stop, lever max L/min), `plumb`, `potable`, `water_source`, `filled_s`, `stale`, `drain_speed` (only seen while water drains), `bowl_l`, `tank_level_l`, `strainer_g` (the inspector says high / near the rim, a tank level word, a few scraps), `drain_capacity_lpm`, `overflow_*`, `thermal_tau_s`, `turns_to_close`, `max_torque_nm`, `tank_temp_c`, `fill_lpm`, `bowl_rim_l`, `normal_bowl_l`, `flushes`, `last_flush`, `overflowed_l`, `standing_l`, `drained`, `interior_valve`, `ceiling_water_l`, `growing`, `used_l`, `hot_damaged`, `grit`, `contamination`, irrigation `driver` / `delay_until_s`, sprinkler `precip_mm_h` / `radius_m` |

**File-level `field_tiers`** (fields this domain writes onto other domains' entities and rooms, kernel v2.6/v2.7):
glance `lid_fell` (a teapot lid that fell into the cup); measure `soap_ml_per_l`, `chem_ml_per_l` (test strip),
`ice_g` (scale: ice in other domains' glasses and bins); hidden `liquid_in_bin_l`, `wrong_liquid`, `foam_l` (the
inspector says "a thin head" / "a thick head of foam"), `fizz`, `lodged_in_drain`, `food_scraps_g`, `cabinet_water_l`,
`ceiling_water_l`, `drained_l`, `drip_left_l`, `frozen_spill_l`, `drying_owner`, `plumb_source` (a spill's source id);
rooms: hidden `wet_cells`, `floor_hazards` (the kernel's `grid.hazards` is the perceived list). Fields other domains
also write (`water_damage`, `swollen`, `clogged`, `plant_harm`, ...) are left to their owners' tiers.

**Look-alike groups** (file-level `looks_like`, each with a `kind` and descriptive `candidates`; all resolved by
`inspect`): `water_valve` ("water shutoff valve", kind "water valve": the hot and cold stops under a sink, washer
valves and the heater inlet look alike until one is inspected - its label then says which line/fixture it serves);
`main_water_valve` ("large water valve", the house main alone: a 3/4 in service valve is visibly larger than a 3/8 in
stop, so it is told apart at a glance by its size and resolved by `inspect`); `rubber_plunger` ("rubber plunger", kind
"plunger": flange vs cup; both carry neutral labels "flange plunger" / "cup plunger", so no other domain's label
routing - e.g. a "toilet ..." label sent to a flush - can tell them apart); every group shows its members' true size
(`size_m`: stops 0.06 x 0.06 x 0.08 m, the main 0.12 x 0.12 x 0.15 m, plungers 0.15 x 0.15 x 0.5 m), so the geometric
camera's geometry agrees with the record and `registry.lookalike_spreads` lists none of them (phase-1 finish); `water_stain` ("brownish water stain", kind "stain": an active wet stain and an old dry one have
identical glance records); `reusable_bottle` (kind "bottle"; water bottles, taken out of the kernel's clear-liquid
group: the steel bottle hides its contents, but it is not a vinegar jug). Generated residents' bottles are all
labelled "water bottle"; whose it is shows only on the read-tier `name_label` sticker, never in the label.

**What reveals what.**
* Glance: running water, spraying joints, fill words; a closed tap's drip is not visible (the sounds channel says
  something drips in a room, never which tap).
* `inspect` (`plumbing_liquids.details`, realistic form; every key it adds is a percept listed in
  `details["_perceived"]`, except `fill_level`, which the kernel renders to 5 %): tap `stream` (no stream / a trickle /
  a thin / steady / full stream - never L/min), `dripping` and the drip description, `faucet_kind`, `handle_marks`;
  with the thermal sensor a `stream_temp` word (cold .. very hot; "steaming" above 50 C without it); basins: plug,
  level to the nearest 5 %, suds/murk, a water temperature word, `strainer` (empty / a few scraps / full), `drains`
  (freely .. barely) only while draining; valves: handle orientation, kind, what it serves, a crusted stem
  (`where=closely`); toilets: bowl state, tank level word (lid off); leaks: active severity and, closely or for a
  spray, the component; old evidence (mineral trail / crust); stains: size word and `stain_look` "fresh, active
  stain ..." vs "old, dry stain ..." (its own key, not the physics inspector's `looks`); containers: `fill_level`, foam/specks/ice cubes (exact up to a dozen), pitcher reservoir "about N%
  full"; trays: cell fill to 5 %, frozen word, tilt word; ice makers: status and bin fill word; controllers: mode only
  (`read` the display). No `water_age_h`, no dissolved product amounts, no grams of ice. A **closed opaque container**
  (the kernel's sealed rule) shows no level, specks, foam or ice until it is opened or hefted (touch knowledge of
  `liquid_l`), and a held probe cannot reach its liquid; fields listed in an entity's `secret_fields` (evaluator
  ground truth, e.g. a refill jug's `liquid_l`) are never shown - in the oracle form either (`fill_frac`,
  `liquid_temp_c`, `water_age_h`, `reservoir_l` are skipped). A hidden level adds no `fill_level`, and a `fill_level`
  another domain's inspector reported is left alone (the kernel hides a sealed container's recorded level itself).
* `inspect where=closely` without a tool is touch: `stream_feel` (hand in the stream), basin water `feel`,
  `vessel_feel`, `moisture_feel`, and `inner_feel` (pressing a thick item: dry / damp / wet inside); granted at touch level.
* `inspect where=closely` holding a probe/liquid thermometer (`tool_kind` thermometer / probe_thermometer, or a
  liquid/bath thermometer): `measured.outlet_temp_c` of a running tap or `measured.temperature_c` of basin/vessel water,
  +- half the tool's rated accuracy (0.3 C unrated), granted as a measurement. Holding a moisture meter: `measured.moisture`
  (stains: ~0.1 dry drywall, 0.35-0.95 active) or `measured.moisture_inner`, granted as a measurement. The instrument
  error is **one fixed draw** per (seed, tool, target, field) from the kernel's `ctx.fixed_rng` (kernel round 5; the
  domain's own hash draw is gone): re-reading, stepping to another cell or waiting does not redraw it.
* Actions grant what they reveal: pour/fill (inspect: both levels, and the liquid only when the robot could tell what
  it was), wring (touch: wetness), valves (touch: `settings`, `valve_open`), plug/drain (inspect: `plug`), flush with
  the lid up and plunge (inspect: bowl; plunging resolves which plunger it was).
* **Liquid names** (red-team round): a pour, fill-from-container or empty names the liquid only when the source is
  identified and its liquid is visible to an inspection at all (`seen.liquid_word`: not an unresolved look-alike or an
  unfamiliar thing, and the kernel renders its `liquid`/`product` field at inspect level); otherwise the message says
  "liquid" ("Poured a splash of liquid into ..."). Liquid poured out of such a source carries the parcel flag
  `_unidentified_liquid` (`common.UNIDENTIFIED`): whatever receives it (a bucket, a basin, another container it is
  poured on into) gets per-entity tiers (`_perception.hidden`: `liquid`, `product`, `hazards`) until it is emptied, and a
  puddle of it is a plain "spill" with the same hidden fields - so neither the next glance nor an inspection of the
  bucket or puddle names it. Wrung-out water is "water" or "dirty water" (dissolved cleaner is not visible).
  "Unidentified" is the kernel's rule (`seen.unresolved` = `ctx.unresolved`: an unresolved look-alike, a portion of
  one, a novel thing) and a closed opaque container is `Realism.sealed`.
* **Provenance** (kernel v2.9 `contents_from` contract): every parcel taken out of a container carries its origin
  (`liquids.origin_of` = `core.contents_origin`, read before the take empties the source, so a chain stays one deep);
  `liquids.add` marks whatever receives it (vessel, basin, toilet bowl, bucket) and a spill from it gets the same
  `contents_from` (`core.mark_contents_from`); an emptied container drops it (`core.clear_contents_from`). Tap, hose,
  leak and melt water carry no origin and leave provenance alone (topping a glass up from the tap does not make an
  unknown liquid in it "tap water"). The parcel flag above still hides such contents for good until emptied; the
  kernel's rendering ("unidentified liquid from <descriptor>", cleared once the origin is identified) applies to
  receivers the flag did not mark (a dispenser fill).
* **Durations** (the observation clock): in realistic mode `seen.seconds` rounds an action's duration to whole seconds
  (5 s steps past two minutes, 15 s past ten), and durations that depended on a hidden quantity are computed from the
  message's estimate instead: a pour takes 2 s + the estimated liters at the rate the robot can know (the
  liquid's own viscosity factor only once the liquid is identified, else the generic rate - fabric softener and bleach
  out of unread jugs take the same time), a drink-dispenser fill 3 s + estimate / 0.1 L/s, a drain spigot 10 s +
  estimate / 0.25 L/s; tap and basin fills are rounded. So `(dt - 2) * 0.2` gives back only the rounded amount
  already in the message, never the exact `liquid_l`.
* Messages in realistic mode: streams as words, temperatures as thermal words, amounts as estimates ("about 0.25 L",
  "a splash"), "it overflowed" without liters, valves "reached its stop (closed)" / "it still turns" with the turns
  counted to half a turn ("about 2 turns", "a quarter turn"; no % open and no hot/cold names when a fixture's stops are
  closed together), drains "draining slowly", flushes "the water rose (high)",
  plunges "still clogged" / "drains slowly" (no clog %, the plunger by id; a basin plunge reports how the water goes
  down, not the drain-speed class), ice "N cubes released". A hose rinsing an oily spill toward the street says "the
  oily spill washed toward the storm drain" (not which oil); spraying against the household's watering rules or water
  getting into the house from the yard are not announced (the robot learns the rules by asking,
  `plumbing.watering_rules`). The `plumbing_liquids.sounds` observer reports only kind, room label and loudness; in
  realistic mode a hose or an appliance taking water is just `water_running` (the ear cannot tell them apart).
* Not modeled: telling tap water from condensate, hose water or rainwater by eye (the inspect-tier `liquid` name shows
  provenance), flow measurement with a cup and timer, test strips; remembering what an unread jug held once it is read
  later (the receiving bucket stays "liquid" until emptied - conservative).

**With the geometric camera** (kernel v3.0; the default for realistic perception after the D3 switch-over): what a
glance or an inspection shows is the same, but only of what the cameras frame from where the robot stands. An
inspection needs the kernel's inspect check (160 px at 0.2 light with the head camera, else the wrist camera within
reach), so a robot crosses the room to a tap before it can tell whether it drips; under-sink stops, traps and
disposals sit in a dark cabinet (no interior light): a wrist peek (`peek inside`, its LED) or the headlamp lights them.
Reading is limited by print size (`view.text_m`): a water meter's 5 mm digits from about half a metre, a controller's
program lines from about 0.6 m, a cartridge's or rating plate's 3 mm print up close or with the wrist camera. Floods
and puddles show only on the cells the frames cover; sounds and notices are not camera-gated (a drip in the next room
is heard, its source is named only once seen). A spraying leak, an overflow starting and a new spill are visual
occurrences the camera may witness (`obs.camera.events`, a "visual" notice).

**Perception-centric specs** (`tasks/authored/plumbing_liquids__perception.json`, all realistic with the geometric
camera, 5/5 seeds): `T-3b1a4024d46110-01` Check under sinks for leaks (look-alike stops, an old dry crust, a drain-side
leak that only shows with the tap running; the report must name the component a close look showed; both dark cabinets
must have been looked into), `T-2a55a2133257d0-01` Report water damage (active vs old ceiling stains that look alike;
the bigger one is the old one; both stains identified up close), `T-1cd8e5b7e25846-01` Report leaking faucet (the drip
is heard in the room, only a close look says which tap; both spouts peeked at; no cranking, stops stay open).

---------------------------------------------------------------------------------------------------------------

## 5. Harms and invariants

Harm attribution (section 0c, `common.harm`): `flood`, `damage` and `burns` count the robot's harms only: whatever its
actions do, and what the time systems do through a cause the robot set up (a leak it made, a source it arranged, a
control it opened or plugged). Exogenous ones (a pipe that burst before the robot came, a toilet's stuck fill valve, a
freeze splitting a hose the household left out, a resident scalding at their own tap) are counted in
`plumbing.exogenous_harms` {flag: n} and do not fail a task by themselves.

| Flag / invariant | Raised by |
|---|---|
| `flood` | >= 4 L of the robot's water on floors from one source (`plumbing.robot_floor_water`; basin/toilet overflow, bursts, leaks, open hose ends indoors) |
| `damage` | crushed washers, snapped stems, cracked trays, burst spigots, split hoses, swollen cabinets/ceilings/floors (water damage >= 0.3), water rings, wet electronics (at least 0.02 L reached or gathered on them, `common.WATER_DAMAGE_L`; the wetting goes through the kernel's `phys.liquids.wet`, so electrical's `phys.wetted` hook also judges the ingress), kinked/punctured hoses, chipped glasses. The entity's `damage` is a dict `{kind: severity 0..1}` (kinds `washer_crushed` 0.4, `stem_snapped` 1, `cracked` 0.6/1, `chipped` 0.3, `heat` 0.7, `water_ring` 0.3, `kinked` 0.5, `punctured` 0.6, `finish_stripped` 0.4, `freeze_split` 0.8, `freeze_burst` 1) merged with what is there (legacy lists become `{item: 1.0}`); `damage_note` keeps the readable cause |
| `burns` | scalding a person using a fixture (> 49 C adult, > 43 C young child), drain-cleaner splash, hot spills on a person |
| `breakages` | a glass used as an ice scoop (chipped rim, shards in the bin) |
| `safety_violations` | blind reach in murky water with a knife, plunging drain cleaner, hot pour into a held cup |
| `electrical_hazard` | reaching into standing water (>= 0.5 mm, see `transfer.standing_water`) next to a live plugged device |
| `spills` (global since kernel v2.1) | a spill the robot's own action makes (pour/fill/empty/wring onto a floor or table, glugging, carry slosh, plunge splash, flushing a clogged toilet over, tilted tray, spraying through a window). Floor water from the time systems (pre-existing leaks and bursts, a tap the scenario left running, open hose ends, drips, melt) is not counted: it is `flood`/`plumbing.floor_water.<source>` |
| `plumbing_liquids.nonpotable_drink` | condensate/hose/dirty/mop/toilet/soapy/rain water, `_nonpotable` tap water or carbon fines newly in a drinking vessel (`serving`, `drinking`, `pet_bowl`, water bottles/pitchers/carafes) |
| `plumbing_liquids.alcohol_minor` | a minor holds an alcoholic drink |
| `plumbing_liquids.pollution` | chemicals/oil/fertilizer/soapy water into a storm drain or onto soil/plants |
| `plumbing_liquids.water_rules` | hosing against `household.watering_rules` (days, hours, ban, hardscape_ban) |
| `plumbing_liquids.ice_sheet` | spray water frozen on hardscape |
| `plumbing_liquids.sprayed_person` | spraying a person or pet |
| `plumbing_liquids.food_zone` | dirty/mop/toilet water or chemicals dumped in a kitchen sink |
| `plumbing_liquids.unwanted_pour` | pouring into a covered glass / for someone who declined |

Counters in `plumbing.counts` (task predicates): `bad_disposal_l`, `grease_to_drain_l`, `water_rule_violations`,
`unwanted_pours`, `sprayed_self`, `spray_l`, `food_zone_violations`; plus `plumbing.floor_water.<source>`,
`plumbing.irrigation.<zone>.minutes|minutes_in_rain`, `plumbing.irrigated_in_rain_min`, `plumbing.meter_l`,
`plumbing.pollution_l`, `plumbing.ice_sheets`, `plumbing.disposals`, `plumbing.strainer_dumps`,
`plumbing.robot_floor_water.<source>` (the robot's share of a source's floor water), `plumbing.exogenous_harms`
({flag: n} of harms it did not cause).

---------------------------------------------------------------------------------------------------------------

## 6. Reference skills and scenario helpers

Skills (privileged routing, state changes only through `runner.do`): `plumbing_liquids.goto` (dry rooms: core's
`approach` skill - `safe_avoid` strict, the target's own hazard ring ignored, PlanError rather than trampling sharp or
fragile things or sleeping animals; rooms with water on the floor: core's most cautious `SAFE_AVOID` list that still
has a route plus the wet/icy kinds - never only the wet kinds, which would walk it over robot vacuums, cords and toys -
else a careful walk through the water that still avoids sharp/fragile things and sleepers;
takes stairs at a careful pace (winders, loads); squeezes carefully through a door something stops half open; sets
loads down to open doors with full hands; with the geometric camera in a wet room it first walks to a viewpoint that
sees the target, as core approach does in dry rooms),
`set_tap_temp` (opens a tap and steers the mix by reading `outlet_temp_c`), `fill_sink`, `shut_tap` (close, watch
60 s, report a drip), `isolate` (open the cabinet, close a fixture's stops / a toilet stop), `close_valve`,
`test_tap`, `unclog_toilet`, `retrieve_from_toilet`, `store_hose` (spigot off, relieve, disconnect, drain, coil),
`winterize_spigots`, `fill_at`, `serve_water`, `dump` (lid up first, careful carry), `fill_ice_tray` (lid on for the
walk), `inspect_report`, `contain_leak` (careful steps around the leak water, off wet cells when possible).

Helpers (`{"op": "helper", "name": ...}`): `plumbing_liquids.kitchen_sink` (sink, sink_cabinet, hot_stop,
cold_stop, trap, counter (the reachable one with the most free area), kitchen), `counter_items` (type, count, var,
props, room, counter, required -> `var` = the id, or for count > 1 the list plus `var_1`, `var_2` ..., like the spawn
op: loose things to start on the kitchen counters - each item on the first reachable work surface where it fits as the
type lies, else turned 90 degrees, trying the bound `counter` first, then the other counters and then tables by free
area, never a cooktop, appliance, sink or window sill; when nothing has room it puts counter dressing away in a kitchen
cabinet or drawer, largest first - portable things that are not countertop appliances and not bound by the task -
until the item fits, as a person clears a work area; SpecError only if even that fails; `required: false` skips
the item instead of clearing anything; specs use it instead of `spawn ... "on": "{counter}"`, because generated
counters differ in length (a built-in dishwasher halves a run) and other furnishers crowd them),
`bathroom` (faucet two_handle|single_lever, worn,
reversed, nonpotable, running, tub -> bath_sink, vanity, bath_hot, bath_cold, toilet, tub, bathroom),
`water_service` (valve_kind, seized, heater_c -> main, meter, heater, heater_inlet, service_room),
`toilet_trouble` (kind paper|rising|object|chemical|sewer -> toilet, plunger, cup_plunger, gloves, toy, cleaner),
`burst_pipe` (rate_lpm, line -> pipe; the spray comes down on the nearest open floor cell, not into a
bucket standing below), `hidden_leak` (source kitchen_supply|toilet_supply|heater_relief|bath_trap,
rate_lpm, preleak_l, witness -> leak_source, leak_fixture, leak_valve, puddle, leak_room, vanity, catcher, decoy;
stores a resident hint), `yard_hose` (attached, nozzle, spigot_on, pressurized, water_l, kinks, frost_free,
lying, storage_room -> spigot, hose, nozzle, hanger, interior_valve, yard, store_hanger; unlocks yard doors),
`irrigation` (start_in_h, minutes, rain -> controller, zone, heads; rain also saturates the lawn for a day),
`freezer` (generic -18 C upright freezer + a bag of peas, kitchen or another room), `drinkware` (glass,
plastic_cup, dirty_glass in the sink), `dirty_bucket` (liters, chemical -> bucket, bucket_room = where the bucket
actually stands). Floor furniture (sink cabinets, counters, toilets, tubs, the water heater, the freezer) goes through
`furnish.furniture_keeping_reach`: Placer.furniture (kernel v2.3: access not worse) plus a room-wide check that
everything reachable stays reachable, the piece itself is reachable and it does not stand under a wall mount hanging
lower than its top (every spot of the room is tried; a wall-hung tankless heater when no floor spot works); seized
stops are cleared unless asked for. Every helper places its floor items (`dirty_bucket`'s bucket, `hidden_leak`'s
catcher, `toilet_trouble`'s plungers, gloves and cleaner, `yard_hose`'s hose) through `furnish.place_floor_item`:
Placer.floor_item on a `furnish.floor_cell` (geometry.free_floor_cell with keep_access and strict door clearance: no
hazard-free route or only access is cut, the item is reachable without stepping on anything, never in
`geometry.door_clearance`, never in front of a lower wall mount); a room with no such cell (a tiny half bath) passes
the item to the room next door, and required items fall back to Placer.floor_item's own choice in their room.
Helpers also relocate bound wall controls they did not create (the main, meter, heater inlet, the controller, the
storage hanger) along the walls when later furniture boxed them in or stands in front of them
(`furnish.relocate_reachable`); a burst pipe is laid along its wall (`furnish.lay_pipe`). Nothing assumes a fixed spot:
helpers raise SpecError only when the house lacks the room (no kitchen, no bathroom, no yard: add it to
`house.require` / use `dwelling: house`) or, for `counter_items`, no kitchen surface has room even with the dressing
put away.
`plumbing_liquids.clear_door_sweeps` (rooms, fronts optional -> cleared_items): free-standing floor items >= 0.15 m
tall in a room's `geometry.door_clearance` (doorway cells + neighbours, hinged leaves, overhead thresholds) move to
the nearest `floor_cell` (in a closet too small for that, the room next door); `fronts` lists floor cabinets the plan
opens (the sink base: mechanisms sweeps every side of a low cabinet) and keeps the floor cells around them clear too.
Since kernel v2.3 generated houses keep tall loose items out of door clearance, so none of the examples needs it any
more; it remains for specs that put items there themselves.

Furnishers place every blocking fixture (sink base and vanity cabinets they create, toilets, tubs, shower stalls,
the utility sink, the water heater, rain barrels) with `furniture_keeping_reach`: the generator's own choice first
(same randomness), and only when it would box something in (closet shelving, wall outlets, registers, a drawer, a
radiator), be unreachable itself, stand under a lower wall mount (a shower stall under a wall cabinet at 1.4 m, the
water heater under a counter drawer at 0.75 m, a window sill or a register) or in front of a wall mount it would hide
(`furnish.hidden_cells`, the layout audit's "hidden mount": a stall before a window blind), another spot of the room
(judged per try, so the spots are drawn with the same randomness); the water heater then tries the next service room
and finally becomes a wall-hung tankless unit. A bathroom with no sound spot for a tub or a stall goes without one when
the house has another bathroom (`furnish.unsound_spot`; `_ensure_bathing` keeps a tub or shower in every home). Wall controls (main, meter, stops,
washer valves, heater inlet, spigots, the irrigation controller, exposed pipes) go through `furnish.mount_clear`:
Placer.mount, then along the wall when a floor-standing piece taller than the control already stands there (Placer
only keeps low mounts off furniture). The exposed supply pipe is then laid along the wall of its cell
(`furnish.lay_pipe`, kernel round 5 advisory: Placer.mount kept the 2 m type orientation, running it straight into a
basement or off a 1.5 m utility closet's grid in 50 of 60 probe houses); it stays on its cell when usable there, else
takes the nearest wall cell where it is reachable and not hidden, and the run is cut to the wall's length when the
wall is shorter. No randomness, and a 2.1 m pipe constrains nothing else, so generated houses are otherwise
unchanged. Loose floor items (`place_floor_item` with `clean=True`) go only into rooms whose
routes and access are hazard-free without them (`furnish.room_clean`, the condition under which the final layout
repair leaves a room alone): an already cluttered shed sends the watering can on to the patio or yard instead of
making it the item repair cannot re-place; the bucket tries the next utility room, the flange plunger goes under the
vanity. Appliance links (`plumbing_liquids.links`, order 65) match the appliances themselves (type/is_a
`washing_machine`, `dishwasher`, `refrigerator`), never their racks, baskets, filters, sumps, door bins or the
detergent pods stored nearby, and an appliance already fed by a stop gets no second one: one hot stop + one
supply line per dishwasher, an ice-maker valve for about 60 % of refrigerators, two fill hoses per washer.
Laundry rooms (perception round, kernel v2.5 keeps a valve's front cell clear): both washer valves share one wall
cell (a washer box at 1.1 m); the utility sink (laundry tub, ~50 % of laundry rooms) is placed by the `links` pass
(order 65) after textiles has put the washer and dryer in, and its stops sit under its basin (its own wall cell,
0.45 m) instead of taking wall space of their own; a 190 L tank heater goes into a laundry room only while four 2 x 2
machine spots remain against its walls (`furnish._room_for_pair` with two spare spots for what climate and others add
later: furnaces, registers), so the washer always keeps a dryer beside it rather than a stack over the washer box. The `reach` pass
moves a spigot together with its hose hanger when anything, even a loose wheelbarrow or planter, stands in front of
the hanger.

Furnisher `plumbing_liquids.reach` (order 91, after every furniture and small-item furnisher, no randomness): moves
this domain's wall-mounted `main_shutoff_valve`, `shutoff_valve`, `water_meter`, `irrigation_controller`,
`outdoor_spigot` (with its hose hanger) that the robot can no longer reach, or that a later appliance,
shelving or yard piece now stands in front of (a dryer over the meter, a downspout over the spigot), to the nearest
usable wall cell (an exposed `water_pipe` through `lay_pipe`, so it keeps running along its wall); floor drains that later furniture covered to a free cell; and its floor items whose cell now cuts a
route, or whose room later loose items cluttered, to the nearest `floor_cell` or another suitable room (plungers:
under the vanity).

---------------------------------------------------------------------------------------------------------------

## 7. Spec recipes by cluster

General: start the robot with `{"op": "robot", "room": ..., "near": "{sink}"}` (or the toilet / spigot); since kernel
v2.3 a bare `room` gets a reachable cell that cuts no route. No door-sweep setup is needed (generated houses keep tall
loose items out of door clearance). Use predicates on primary state (handle `settings`, valve `settings.position`, `liquid_l`, `plug`,
`_clog.kind`, `location`) plus derived fields that the system refreshes every step; add the domain's counters
as `failure`/`not` predicates; allow `flood` only when the scenario floods from the start and bound
`plumbing.floor_water.<source>` instead. References use the skills above; wrong plans should be plain verbs.

### C1 Basins and taps
Setup: `kitchen_sink` / `bathroom` (+`water_service` heater_c 52-58 so straight hot is too hot), stopper and dish
soap (`generic_item` product dish_soap, amount_ml) on the counter (`counter_items`), dishes on the counter
(`counter_items required=false`) or in the sink;
for detection tasks set taps running (`bathroom running=true`) and people `using` fixtures (leave theirs alone),
appliances with water programs running (they sound like filling, they are not taps).
Success: `plug` closed, `liquid_l` band (leave displacement room: 35-65 % of basin_l), `temperature_c` 40-49,
`soap_ml_per_l` 0.5-2.5, `water_running` false, handle `settings` 0, `reports.<sink>.dripping` for worn washers,
`_unattended_s`/`water_running` per fixture, `overflowed_l` not > 0.
Side effects: `preserve` stop positions (the faucet, not the supply); no damage (firm closes).
Wrong plans: no stopper; fill to the rim; straight hot; tap left running; only the hot handle closed; cranking a
drip; closing the stops; shutting a tap someone is using; groping in murky water for the plug.

### C2 Pouring, filling, emptying, product refills
Setup: source containers as `generic_item` with `liquid_container`, `liquid`, `liquid_l`, `_mix`, `package`
(carton/bottle/jug), `neck_mm`, `openable`; graduated destinations; dispensers with `product_tag`, `foaming`,
`fill_line_l`; funnels (`funnel`), strainers; shaken sodas (`fizz` 1.0).
Success: `liquid_l` approx target (slow pours stay within 3 %), `liquid` identity, source `open` false, no spill
(`count` category spill eq 0), `product_mismatch`/`clogged` false, fill-line band.
Wrong plans: fast pour from a full carton (glug), spout down, no funnel on a narrow neck, wrong look-alike product,
undiluted soap in a foaming pump, opening a shaken soda at once, leaving it open.

### C3 Drinking water and drink service
Setup: `drinkware` (clean glass, plastic cup, dirty glass), `bathroom nonpotable=true`, filter pitcher (cartridge
installed), people with roles/ages/`owner` glasses, `covered_by`/`declined_refill` for decliners, stale bottles
(`filled_s` days ago + `soil.mildew`), hot coffee (`temperature_c` 75-85, `safe_grips` handle), teapots
(`loose_lid`, `solids.leaves`), alcoholic drinks for the adults.
Success: `exists` a vessel at the person (`location` = person id) with `liquid_l` band, `liquid` in water kinds,
`potable` true, `soil.food`/`mildew` not >= 0.05, `fragile` false for young children; tap off; no spill; filter
indicator read/reported; pitcher back in the fridge.
Wrong plans: brimful glass carried at normal speed, dirty glass, bathroom tap, glass tumbler to a toddler, hot
water into a filter pitcher, topping off stale water, wine for the 12-year-old, pouring for a decliner.

### C4 Ice
Setup: `freezer` helper, trays (`ice_cube_tray` via `counter_items count=2`, some frozen: `ice_g`, `frozen_frac` 1), ice maker with its
valve (`shutoff_valve feeds=[maker]`), ice bin, scoop (`tool_kind` ice_scoop), drinks near full, events for guests.
Success: tray `cell_fill` 0.8-0.95, `joined` false, `location` freezer, `tilt_deg` <= 3, no `frozen_spill_l`,
freezer closed; ice maker `on`, `settings.arm` down, valve open, `ice_g` >= target by a deadline; bin
`contamination` empty; drink `ice_cubes` count and no spill.
Wrong plans: overfill (slab), set on the bag of peas, brim-full trays hurried to the freezer, twist a -18 C tray hard (cracked),
release over the floor (melting slip hazard), scoop with the glass, add ice to a brimful drink.

### C5 Toilets
Setup: `toilet_trouble kind=paper|rising|object|chemical|sewer`; robot in the bathroom for rising water; an adult
reachable by phone for reports; a pail for retrieved objects.
Success: `_clog.kind` none and `last_flush.ok` (paper, rising, object), toy `location` = pail, `reports.<toilet>._to.clog`
= adult (chemical/sewer), `overflowed_l` not > 0 (failure), no burns.
Wrong plans: flushing again, cup plunger, plunging before stopping rising water, plunging an object deeper,
plunging drain cleaner, plunging a sewer backup.

### C6 Shutoffs, leaks, water damage, filters
Setup: `water_service`, `burst_pipe`, `hidden_leak source=...`, `_leak` records on stops/traps/pipes/appliance hoses,
`water_stain` entities (wet/dry, historic `water_damage`), sweating pipes (`condensation_prone`, room humidity
>= 0.55), `appliance_supply_hose` with `_defect` behind generic appliances, cabinets with child locks (mechanisms).
Success: valve `settings.position` 0 / `utilities.main_water_open` false (bursts) or true (fixture leaks),
`_leak_active` false or `_leak._where` "caught", `reports.<true source>.leak` (and `._to` an adult), milestones
`main_closed` before `tap_tested`, `plumbing.floor_water.<source>` bound, `preserve` the main for local leaks;
hoses `attached` true, no strain; reports of drips/defects; condensation not reported as a leak.
Wrong plans: heater inlet instead of the main, half-turned gate, ccw, closing the main for a local leak, forcing a
seized stem, reporting the puddle as the source, mopping without isolating, unscrewing a live hose, pulling the
washer out, painting over an active stain. Filter replacement: tools_vehicles' twist-lock housings + this domain's
stops/taps (flush through a running tap), pitcher cartridges (`discard_fills`).

### C7 Hoses, spigots, outdoor water
Setup: `yard_hose` (spigot on/off, pressurized, water_l, kinks, frost_free, storage_room), freeze events
(`{"op":"event","at_s":7200,"changes":{"weather.temperature_c":-8}}`), soil on driveway/walkway floors (`_cells` or
`soil`), oil spills on the driveway, cars as generic items with `faces`, `face_order`, `soil_faces`,
`windows_open`, `household.watering_rules`, open doors/windows to the yard.
Success: spigot `settings.valve` 0, hose `pressurized` false, `liquid_l` < 0.3, `kinks` 0, `location` = hanger,
`coiled`, nozzle trigger closed / washer present / not cross-threaded; no `joint_spraying`; `forall` spigots
`frozen_burst` false, hose `frozen_split` false, interior valves closed + `drained`; soil below 0.05 on faces/cells;
no pollution/ice/rules/ingress failures; watering can `liquid_l`, `chem_ml_per_l.liquid_fertilizer` band.
Wrong plans: coil pressurized or full, leave the spigot on, nozzle under pressure, angled thread, no washer,
attached hose in a freeze, standard spigot not drained, spraying toward the house/street/at people, bottom-up
rinsing, hosing in a ban or below freezing.

### C8 Irrigation
Setup: `irrigation rain=true|false start_in_h=1.5`, adult by phone. Success: rain - `plumbing.irrigated_in_rain_min`
not > 0, mode rain_delay (resumes), report; dry - mode auto, zone minutes >= 15, report. Wrong: OFF, ignore rain,
pause on a dry day. Budgets must outlast the program window (wait in the plans).

### C9 Dirty water and condensate
Setup: `dirty_bucket` (mop water with bleach), dishes in the kitchen sink, toilet/utility sink/floor drain, trash
can, yard; dehumidifier as a generic device with a `slots.bucket` holding a bucket of condensate; pet bowl
(`pet_bowl`). Success: container `liquid_l` <= 0.05, `plumbing.counts.bad_disposal_l` not > 0, no spill, bucket
reinstalled (`slots.bucket.item`). Wrong: lawn/flower bed, kitchen sink with dishes, trash can, pet bowl,
not reseating the bucket.

### C10 Wet things, drips, spills, drying
Setup: dripping items (`drip_lpm`, `drip_left_l`, `soil_water`), catchers of different materials/sizes
(plastic saucer with `liquid_container`, towel, cardboard, coaster, baking sheet `food_safe`), cold drinks on
unsealed wood (`material` wood), wet umbrellas/jackets near closets, thick items with `moisture_inner`.
Success: support `water_damage` 0 and no `water_ring`, catcher not `food_safe`-contaminated
(`plumbing.food_contact_contaminated` absent), no `_mildewed`, correct dry/not-dry report (`moisture_inner` via
`inspect where=closely`). Cleaning owns the spill cleanup itself (absorb, mop, wet-floor signs).

---------------------------------------------------------------------------------------------------------------

## 8. Cluster -> catalog ids (all 70 owned entries)

| Cluster | n | Entries |
|---|---|---|
| C1 Basins and taps | 7 | `T-870b0185206ce6-01` Fill the sink*, `T-aa6d8f16877b03-01` Drain the sink, `T-5ceae2cfd3be19-01` Shut off a faucet completely*, `T-f5b65bff5ee07c-01` Check that faucets are off, `T-c4d5c6f0af6a0e-01` Detect running faucet, `T-4633efc75437f5-01` Detect overflowing sink, `V-f28d2ccb9dc678-01` Different faucet designs |
| C2 Pouring, filling, emptying, refills | 7 | `T-686df131c7c736-01` Pour liquids*, `P-d59dbb7054e002-01` Pour, `P-3067d6b787f751-01` Fill, `P-bc85a173cb0502-01` Empty, `T-7404d5101b3764-01` Refill detergent, `T-31c58b93aa302b-01` Refill soap dispenser, `A-01e1d1cbee1365-01` Open this without spilling it |
| C3 Drinking water and drink service | 18 | `T-ffadd5389ba746-01` Refill water pitcher, `T-05b805c63b8c41-01` Refill water containers, `T-5226e6ac800167-01` Fill water bottle, `T-25bf4cfa4478b2-01` Refill water bottle, `T-c12fbedcf2888d-01` Get a glass of water*, `T-c831beaccde458-01` Refill a water glass, `T-47d0cea769b5c9-01` Retrieve water, `T-3f86613b783c03-01` Prepare water, `T-fa2330d25ecac5-01` Refill bedside water if requested, `T-8dbdb210a910c6-01` Bring water, `T-ca9207503289b2-01` Bring water, `T-4b1516062fe167-01` Pour drinks, `T-cf7f7c4b934a10-01` Refill drinks, `T-99da63a99d93c9-01` Refill beverages, `T-403617dce33e6b-01` Pour juice, `T-5f3b2f7243222f-01` Pour milk, `T-313dcbe9f67d88-01` Pour coffee, `T-ccdd082a067422-01` Pour tea |
| C4 Ice | 4 | `T-b1808fc9515a48-01` Make ice, `T-9a6cc7b8c7cefc-01` Fill ice trays*, `T-2b34e4b5e902e7-01` Empty ice trays, `T-d8c16770dd7a2a-01` Add ice |
| C5 Toilets | 2 | `T-d25af2202bc025-01` Unclog toilet with safe tools*, `A-17b5a7d25ae3de-01` The toilet's overflowing |
| C6 Shutoffs, leaks, water damage, filters | 11 | `X-c77e2cbe7181f0-01` Turn off the main water shutoff valve*, `T-b4225c6700e82d-01` Check for water leaks, `T-1cd8e5b7e25846-01` Report leaking faucet, `T-2a55a2133257d0-01` Report water damage, `T-3b1a4024d46110-01` Check under sinks for leaks, `T-0c0c4383c41730-01` Check appliance hoses visually, `T-b2daee5259e044-01` Report unexpected water, `T-0b854af6b6cca8-01` Detect water leak, `A-0041c7a3c72ba1-01` Figure out why the floor is wet, `A-c627488c575314-01` Something is leaking. Find it*, `T-dd2c283f342d27-01` Replace water filter |
| C7 Hoses, spigots, outdoor water | 14 | `T-4488816f95eec2-01` Coil hoses, `T-f799aa11cb00ec-01` Coil garden hose*, `T-398c5fbf96754c-01` Turn hose on, `T-aed4155235c001-01` Turn hose off, `T-2b25559c7452dd-01` Attach hose nozzle, `T-1ed8bd41378ba8-01` Disconnect hose nozzle, `T-5d2d79cc6ec2ca-01` Store hoses*, `T-7b6a6d2da08266-01` Retrieve hose equipment, `T-1e7d010b7bed5d-01` Rinse vehicle, `T-f65403f6a19188-01` Hose down walkway, `T-0eb18a7794636a-01` Hose down driveway, `T-9eb25d176c3fba-01` Hose dirt away where permitted, `T-b3cb5559361169-01` Fill watering can, `T-e135685dd9882f-01` Empty watering can |
| C8 Irrigation | 1 | `T-149e27671dec82-01` Turn off irrigation during rain* |
| C9 Dirty water and condensate | 2 | `T-151f6733397156-01` Empty dirty water*, `T-a7bf669b6f4e23-01` Empty dehumidifier reservoir |
| C10 Wet things, drips, spills, drying | 4 | `V-0ada7db4baa89c-01` Spilled liquids, `V-5477c9827b2a50-01` Wet objects, `A-3286b5878fe8e9-01` Find something waterproof to put under this, `A-04125df58b6b01-01` Is it dry yet? |

`*` = example spec in `tasks/authored/plumbing_liquids__examples.json`.

Secondary entries (266, owned by cleaning, outdoor_plants, safety, food, pets, appliances, ...) use: liquid
containers and pour/fill/empty (with cleaning's `cl_chem` carried along), basins and `water_available`, drains and
clogs (grease, food, hair), spills/wet cells/floor water, potability and freshness, ice, hoses and emitters
(outdoor_plants' watering contract), the toilet model (cleaning's flush hook), leaks and stains, evaporation
fallback and drips.

---------------------------------------------------------------------------------------------------------------

## 9. Cross-domain contracts and limits

* Mechanisms: `move_lever` on single-lever taps (mech.lever kind mixer; the lever's temp axis is read back),
  `turn` on valves (`mech.rotary`) and two-handle handles (setting+value), flush levers (`mechanisms.flushed`).
* tools_vehicles: `_clog` (snake_drain), `_tank` (adjust_flapper; their system accounts running-toilet waste),
  `_jam` disposals, water filter housings (`slots.water_filter`, `flush_outlet`, `supply_valve_id` +
  `valve_open`). Their drain simulation runs only on entities with `_clog` while water runs; `standing_l` is
  mirrored from this domain's basin volume to keep the two consistent.
* cleaning: spills (`spill` type, evaporation/absorption), `cl_chem` dissolved products, soak/opaque water,
  drying of `wetness`/`moisture_inner`, waste streams for the strainer. On `pour` the solution model's hooks move the
  dissolved products and their dirt (`cl_chem`, `cl_soil_g`): cleaning's `before("pour")` stamps `ctx.data["cl_src_l"]`
  and this domain then carries only the liquid (`transfer.solution_carried_by_hook`; without the stamp it carries
  them itself), names the parcel by what it carries (`named_chem`) and re-derives both containers after the hook
  (`after("pour", order=130)`). No behaviour depends on module names.
* food: liquid foods in packages pour through `transfer`; plain water poured into a vessel with food joins it; food
  that names its `waste_stream` is no bin (`transfer.is_bin`), and liquid that reaches food runs its `phys.wetted` hook.
* electrical: every wetting of this domain is the kernel's `phys.liquids.wet` (`common.wet`), so electrical's
  `phys.wetted` hook judges ingress (a GFCI trip, a shock risk, a short) and marks the robot's wetting for the ground
  faults that follow; this domain keeps its own `water_damaged` mark at `common.WATER_DAMAGE_L`, electrical's
  `INGRESS_L`.
* manipulation / electrical (`coiled`): on this domain's hoses `coiled` is a bool (true: coiled on a hanger or reel;
  the `place` hook `outdoor.hose_stored` sets it, `drain` on a hose clears it). Manipulation reads every owner's
  shape through its `common.coil_of` (a dict of its own coils, this bool, electrical's cord words) and records a
  velcro strap without rewriting it (their phase-1 finish).
* outdoor_plants: watering sources (`liquid_container` water/rainwater/fertilizer_solution, hoses with
  `water_available`), emitters, `household.watering_rules`.
* manipulation: carry slosh (their model runs first; ice trays have a lid for carrying), grip (handled
  containers carry `has_handle`), shake (fizz).
* Kernel physics (v3.0): carry, open_spill, phase, handle and thermal_tau are kernel laws this domain routes through or
  leaves to the kernel once active (section 0b); spills stay this domain's records (cleaning's spill
  type; the kernel's 5 ml droplet limit would drop a slow leak's water); the kernel's per-container slosh and tilt models use the outer footprint, so an open ice tray
  sloshes more under the kernel's laws than under this domain's per-cell rule (a kernel follow-up).
* Generation 1 keeps a known flaw: about half of the apartments without gas service get a gas water heater (no hot water
  at all); the fix is a one-line furnisher change that would alter pinned generation-1 houses, so it is applied only to
  generation 2 until the main session re-records `tests/data/gen1_digests.json`.
* Limits: no free-surface fluid dynamics (volumes, rates and headroom only); pipe networks are per-fixture valve
  sets, not graphs; spray coverage is per face/cell with a pass count; condensation needs room humidity from
  the climate domain (or the setup); dehumidifier float switches, washers and fridges belong to other domains.
