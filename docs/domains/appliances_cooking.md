# Appliances and cooking domain

Kitchen and household appliances and the physics of cooking with them: gas / coil / radiant / induction cooktops
and the pots on them, ovens (range, wall, toaster oven, air fryer), microwaves, toasters, kettles, blenders, mixers,
food processors, drip / pod / espresso coffee machines, waffle makers and griddles, slow / rice / pressure cookers,
refrigerators and freezers (seals, frost, defrost), dishwashers (loading rules, cycles, detergent, filter, brands),
outdoor gas and charcoal grills, cooking timers, oven mitts and hot-item handling, appliance noises and faults.

Code: `house_sim/systems/appliances_cooking/` (package). Types: `house_sim/data/types/appliances_cooking.json` (96 types).
Tests: `tests/test_appliances_cooking.py` (190 tests, 32 of them realistic-perception, kernel v2.8 hardening and round-3
follow-ups, 24 of the tier-1 adoption round (section 10), 9 of the phase-1 stabilisation after the independent review
(section 11); pass with every other domain loaded, with `HOUSE_SIM_STRICT=1`, isolated
(`HOUSE_SIM_DOMAINS=core,appliances_cooking`, also strict) and with all 19 deferred physics laws forced active, `stack`
and `rain` included since the kernel's fixes, section 11). Perception specs: `tasks/authored/appliances_cooking__perception.json` (5 realistic-mode
specs, section 4a; T-22bc2280c7e6be-01 runs with the geometric camera). Examples:
`tasks/authored/appliances_cooking__examples.json` (12 specs). Every spec of both files declares `"house": {"gen": 2}`
and passes `--check` on seeds 0, 1, 2, 41, 97 with every domain loaded, in its own mode, with `--camera geometric`
(the perception file) and with those laws forced active. Cost: `tools/bench_systems.py 40 0 1 2 3 --domain appliances_cooking`
~0.1-0.3 ms per 30 s substep (was ~1.3 ms before the integration round; `tools/bench_systems.py 20` after the
v2.3 layout round: 0.17 ms, whole engine 5.8 ms). Layout: `tools/layout_audit.py 20 --domain appliances_cooking` reports 0 lines
(kernel v2.5 audit kinds included: no wall cabinet hangs where the hood is). Perception: `tools/perception_audit.py 30 80`
reports no leak attributed to this domain; the kernel v2.8 red-team findings against it (look-alike refusals, type-named
refusals of unfamiliar things, cook_state juices) are fixed (section 4a).

| Module | Contents |
|---|---|
| `common.py` | state section `appliances_cooking` (log, harms, counters, once-keys, fallback air), level scales, liquids table (density, cp, boiling and smoke points, foam factor), material rules (induction, oven/broiler/microwave/dishwasher verdicts, ignition and melt points; `surface_heat_limit_c` is the kernel's heat limit), the tier-1 adapters (`own_temperature` / `release_temperature` / `owns_temperature`, `law_active`, `break_item`, `spill`, `shock`, `scalded`, `quiet_check`, `visual_event`, `notice`; section 10), people/children/birds helpers, air fallbacks (smoke, odour, gas %), counter edges, power state, floor-access helpers on the kernel v2.3 geometry (`approachable`: 4-connected `geometry.reachable_cells` from the doorways, radius 2 for wall-mounted things above counter height, like the kernel's `approach`; `wall_storage`: drawer fronts, wall cabinets, shelves), deferred destroy, `add_damage` (damage dict), `system_loaded`, the tick-scoped location index (`tick_index_begin/end`; `children`/`descendants` use it inside the tick) |
| `build.py` | builders: `build_range` (burners, oven + racks, hood; built-in cooktop fallback), `build_fridge` (door bins, crispers, gaskets and the freezer compartment as parts), `build_freezer`, `build_dishwasher` (racks, basket, cup, rinse-aid reservoir, filter, sump, gasket, brand panels), kettle, blender, coffee makers, air fryer, slow / pressure / rice cookers, espresso, food processor, stand mixer, grill + propane cylinder (a part of the grill); placement on the kernel access model: `access_targets` (the kernel's targets plus the room's mounted lights, which need a bulb change now and then) / `access_report` / `access_check` (geometry.access_report / not_worse - the rule Placer.furniture uses: doorway groups stay joined and everything that was accessible stays accessible, lights included, full protection first), `clear_hood_wall` (a wall cabinet or shelf hanging where the range hood goes moves, with its contents, to the nearest reachable free wall cell, else up above the hood), `wall_conflicts` (a floor piece never stands in front of / under a mounted thing hung lower than its top - windows, coverings, radiators, registers, switches, extinguishers, drawer fronts, wall cabinets, sinks, hoods; receptacles excepted), `place_safely`, `place_floor_appliance` (fridge, range, freezer: a wall spot keeping full access -> an empty base cabinet's place -> the end of a counter run (the counter keeps >= 1 m; loose items move along or to another surface; drawer fronts and wall cabinets move with the counter; never in front of a window/radiator/switch) -> a wall spot keeping basic access (doorways, dock, floor-standing things, operable mounts, wall storage) -> the dining table and chairs go to the next room and it tries again -> a free-standing spot as the last resort), shelf loads (`shelf_rating` ~30 kg per base-cabinet shelf, 15 kg upper; `shelf_load`, `shelf_with_room`, `store_in_cabinets`, which unplugs a corded appliance it puts away) |
| `stove.py` | burners (gas flow/flame/CO/unlit leak, coil/radiant elements and residual heat, induction pan detection) and the lumped vessel model (boiling cap, evaporation, foam and boil-over, scorching, boil-dry, oil smoke point and autoignition, latent heat of frozen contents), attendance, handle direction, food into hot oil, `ignite` |
| `cook.py` | cooking profiles (26), heat context per appliance, surface moisture / browning per side, lagged core, doneness labels and visible cues, danger-zone time, crowding, broiler distance, `check_doneness`, flip/shake hooks |
| `oven.py` | cavity thermostat, preheat/beep, door-open losses, broil, self-clean (lock, consent, ash, fumes), contents hazards (melting, glass under the broiler, oven floor), lumped pans on racks (`step_oven_vessels`), toaster-oven top surface and element clearance, air-fryer clearance/parchment, `preheat` |
| `countertop.py` | microwave (absorption, cold spots, arcing, sealed lids, eggs, superheated water), toaster (fit, jams, crumbs, metal), kettle (base, dry boil, overfill spit, auto-off; it boils shut without foaming over), `blend` (blender + processor interlocks, hot-liquid eruption), mixer (flour cloud, attachments), waffle maker / griddle, slow cooker, rice cooker, pressure cooker (seal, kPa, burn error, lid lock, quick release) |
| `coffee.py` | drip brew (flow, strength, filter/carafe mistakes, warmer), descaling, `brew` (drip, pod, espresso shot model, french press), espresso boiler, `steam_milk`, `grind_coffee`, knock box |
| `cold.py` | fridge/freezer air model (compressor, door open/ajar UA, alarms), frost growth, `defrost` (methods, meltwater containment), door obstruction (protruding items, gasket; `unit_contents` keeps a refrigerator's check out of its freezer part), shelf clearance guard |
| `dishwasher.py` | slot classes and loading rules, `analyse` (spray arms, detergent door, nesting, spooning, overload, exposure), `load_check` truth, `load` / `unload` verbs, cycle hooks (programs, brands, delay, household rules, detergent release, rinse aid, wash outcome, damage to unsuitable items, drying), filter access, mid-cycle opening |
| `grill.py`, `timers.py`, `handling.py`, `status.py`, `routes.py`, `systems.py` | `light_grill` + grill model (propane cylinder slot: install/uninstall hooks, valve-open refusal); `set_timer` + ringing; mitts (gripper heat fault), hot placement (kernel contact heat and thermal shock, `phys.contact_heat` claims), child reach, carry warnings, detergent invariant; observer, inspector, answerer (and the `permission:self_clean:` / `permission:defrost:` answerers), intent checks, always-on invariant, watchable fields; the dialogue routes to the answerer; the domain tick (static index, idle predicates, exact idle probe, vessel hand-back), the cold system and the drying fallback |
| `furnish.py`, `scenarios.py`, `skills.py` | furnishers (orders 45-47), 13 scenario helpers (+ kitchen workability: pets out of the way, reachable tools, counter room for appliances, dishes and props), 16 reference skills |

All state-affecting iteration is over sorted ids; randomness only through `ctx.rng` / `tick.rng`. Systems (section 3):
`appliances_cooking.tick` (order 55, max 30 s) never scans all entities: it reads one kernel static index of the
entities it can ever act on, skips every appliance whose idle predicate holds (a check on its own record), and steps
only active appliances and the food in/at them, and an exact idle probe lets the engine skip it in a quiet kitchen;
`appliances_cooking.cold` (order 55) steps the fridges and freezers every substep; `appliances_cooking.drying`
(period 300 s) is a fallback that runs only without the cleaning domain.

-----------------------------------------------------------------------------------------------

## 0. The model (read this first)

* **Observable vs hidden.** Appliances keep observable fields (`settings`, `display`, `open`, `ajar`, `sealed`,
  `preheated`, `boil_state`, `foam_level`, `smoking`, `load_state`, `status_light`, `cook_state`, `timers`...) and
  hidden ground truth in underscore fields: `_burner`, `_vessel`, `_oven`, `_fridge`, `_dw`, `_load_check`,
  `_cook`, `_pressure`, `_toaster`, `_fault`, `_intended`, `_defrost_ok`. Predicates may read both; the robot only
  sees the observable ones (inspect adds a few more, see section 4).
* **Who owns temperature.** A pot on a burner or appliance base, and a pan on an oven rack, is owned by this domain
  while heated (`thermal_owner: "appliances_cooking"` + `_ac_heat` = heater id; the kernel's convention, TIER1_PHYSICS
  5.9 - `common.own_temperature` / `release_temperature`; the older `fixed_temperature` alias is still read as
  ownership and dropped on release): a lumped energy balance with its contents (water,
  oil, liquid foods, solids' heat capacity, ice latent heat). It is released the moment it leaves the heater, and
  also once its heater is switched off and it has cooled to <= max(room + 1 C, 40 C) with no foam (`boil_state`
  becomes `cold`; the kernel/food thermal models take over; it is re-owned as soon as the heater delivers heat).
  A switched-off, cool slow-cooker base drops its own `thermal_owner` the same way. Burner elements and the electric
  griddle are always this domain's (type data `thermal_owner`; a generation-1 kitchen keeps the `fixed_temperature`
  alias, section 10).
  Food items follow their vessel / cavity: with the food domain loaded its `food.thermal` (order 52) relaxes food
  toward the vessel/cavity and keeps the core node, latent heat and food-safety flags; this domain adds browning,
  moisture, doneness labels and danger-zone time in `_cook`. Without the food domain the kernel relaxes them and
  `cook.step_food` keeps the lagged core and thaws ice itself. Appliance bases that drive a pot carry
  `heat_source: true` so food treats the pot as actively heated.
* **Appliance parts are children.** Burners, the range oven and its racks, the hood's `over` link, dishwasher racks and
  parts, fridge door bins / crispers / gaskets and the refrigerator's **freezer compartment** (`freezer_drawer`, relation
  `part`, `z_m` 0.05 for a bottom drawer / 1.3 for a top compartment, `power_from` the refrigerator) are children of their
  appliance, so no room entity overlaps another. The freezer keeps its own door (`open`/`ajar`/`sealed`), air model,
  frost and alarms; the refrigerator's seal check (`cold.unit_contents`) never looks into it. A gas grill's propane
  cylinder is a part of the grill (slot `tank`, `z_m` 0.05, visible with the lid closed); `uninstall` swaps it, refused
  while its valve is open.
* **Units.** Ovens have `oven.unit` F or C and `settings.temp` in that unit; `internal_temp_c` is always Celsius.
  The `preheat` verb converts a temperature given in either unit and rounds to 5.
* **Interop hooks used.** `cycle_check/start/tick/complete:<type>` for dishwasher, ovens, microwave, toaster,
  coffee maker, pressure cooker (the domain's own `brew` of a drip machine and `preheat` of an air fryer start the cycle
  with `core.start_cycle(ctx, ...)`, kernel round 5, so cycle_check hooks get the robot's ctx as for core `start`);
  `setting_changed` for knobs and panels; `part_installed/removed` for the dishwasher
  filter; before/after hooks on core `place`, `pick_up`, `open`, `close`, `set`, `start`, `cancel`, `pause`,
  `switch_on/off`, `move`, `approach`, `walk_to`, `wear`; the food domain's `blend` hook runs on this domain's verb.
  Gas leaks are published as `_gas_leak` / `_co_source` and fires go through the safety domain when it is loaded
  (`safety_module("fire").ignite`), otherwise the global flags are raised directly.
* **Shared-field conventions (integration round).** `damage` is always a dict `{kind: severity}` (`C.add_damage`
  converts legacy lists; e.g. `waveguide_cover_burned` 0.6, surface `heat_mark` 0.5 / `scorched` 0.5 / `melted` 1.0).
  Drying: this domain does not evolve `wetness` when a generic drying domain is loaded - cleaning's
  `cleaning.drying` dries dishwasher-washed items (they keep `washed_t`); the fallback `appliances_cooking.drying`
  only runs without it and skips items with another `drying_owner` or `_wet_mine`. Status fields are this domain's
  hidden namespaces (`_cook`, `_vessel`, `_oven`, `_dw`...), never a bare `_status`. `odor` in the fallback air is a
  dict. `collisions`, `falls`, `spills`, `breakages` are global task failures: this domain raises `spills` /
  `breakages` only for real mishaps (boil-overs, bursts, spits, floods, cracked glass), never for routine handling.

-----------------------------------------------------------------------------------------------

## 1. Object types (96) and realistic magnitudes

| Group | Types | Key fields (magnitudes) |
|---|---|---|
| Cooktops | `range_gas`, `range_electric`, `range_radiant`, `range_induction`, `cooktop_gas`, `cooktop_induction`, `burner`, `range_hood`, `hood_filter`, `stovetop_griddle`, `stovetop_kettle` | burner `burner`{kind, size, diameter_m 0.15-0.23, max_w gas 1.4-3.5 kW (eff 0.40), coil 1.5-2.6 kW (0.70), radiant 1.2-3.0 kW (0.70), induction 1.4-3.7 kW (0.85), lit, flame, igniter_ok, cap_seated}, `settings.heat` on the range's scale (gas off/lite/low.../high; words; 1-9; induction 1-9+boost), `burner_pos` LF/LR/RF/RR, `center_m`; range `cooktop.auto_reignite`; hood `hood.flows_m3s`, `exhaust_m3s` |
| Ovens | `range_oven`, `wall_oven`, `oven_rack`, `toaster_oven`, `air_fryer`, `air_fryer_basket` | `oven`{unit, bake_w 3000, broil_w 3600, convection, self_clean, panel touch/knob, cavity_c_j_k 16000, ua_w_k 7, open_ua_w_k 55}, `settings`{mode, temp, light, clock}, `internal_temp_c`, `preheated`, `locked`; 6 rack positions (`shelf_heights`); toaster oven 1500 W, elements 3.5 cm from the rack; air fryer 1700 W, needs 13 cm rear clearance |
| Cold | `refrigerator`, `top_freezer_refrigerator`, `freezer_drawer` (a part of its refrigerator), `upright_freezer`, `chest_freezer`, `fridge_door_bin`, `crisper_drawer`, `door_gasket` | `fridge`{setpoint_c 3.5 / -18, cooling_w 110-150, ua_w_k 1.2, open_ua 22, ajar_ua 6-9, air_c_j_k, alarm_after_s 120, frost_free}, `frost_mm`, `ajar`, `sealed`, `door_alarm`, `temp_alarm`; inner sizes are whole compartments, `shelf_clearance_m` per shelf (0.28-0.33 m); door bins and crispers are plastic containers without `conducts_heat` (their contents sit in the compartment air: the kernel walks up through them to the fridge's `internal_temp_c`) and are built at the compartment temperature |
| Dishwasher | `dishwasher`, `dishwasher_drawer`, `dishwasher_rack_bottom`, `dishwasher_rack_top`, `dishwasher_utensil_basket`, `detergent_cup`, `rinse_aid_reservoir`, `dishwasher_filter`, `dishwasher_sump`, `dishwasher_pod`, `dishwasher_pod_tub`, `dishwasher_powder`, `rinse_aid_bottle` | programs normal 2 h 20 / heavy 2 h 40 70 C / eco 3 h 30 50 C / quick 1 h / rinse / sanitize, water 10-16 L; brand panels (`generic`, `brand_a` names, `brand_b` icons, `brand_c`), `settings`{program, delay_h, heated_dry, child_lock}, `load_state` empty/dirty/clean, `status_light`; filter `settings.lock`, `soil` |
| Countertop | `microwave`, `microwave_otr`, `toaster`, `toaster_4slot`, `electric_kettle`, `kettle_base`, `blender`, `blender_jar`, `stand_mixer`, `mixer_paddle`, `mixer_whisk`, `mixer_dough_hook`, `mixer_bowl`, `food_processor`, `processor_bowl`, `processor_s_blade`, `processor_lid`, `waffle_maker`, `electric_griddle`, `bread_machine` | microwave 1000-1100 W out, `settings`{power 1-10, time_s}; toaster slots 14 x 3.5 cm, shade 1-7 (cycle 50 + 22 x shade s); kettle 1.7 L, 2.2-3 kW, min/max marks, `settings.target_c`; blender 0.9-1.5 kW, jar 1.5 L, vented lid cap; mixer 10 speeds, head lock; processor interlock lid; waffle maker / griddle `internal_temp_c` with ready light |
| Coffee | `drip_coffee_maker`, `coffee_filter_basket`, `coffee_carafe`, `paper_coffee_filter`, `coffee_pod`, `pod_coffee_machine`, `pod_holder`, `espresso_machine`, `portafilter`, `espresso_tamper`, `steaming_pitcher`, `knock_box`, `coffee_grinder`, `french_press`, `milk_frother` | drip: reservoir 1.5 L, flow 0.2 L/min, warmer 80 C, auto-off 2 h, `scale`; espresso: boiler 93 C, 9 bar, steam wand, dose 18 g, yield 36 g in 25-30 s; grinder grind settings |
| Cookers | `electric_pressure_cooker`, `pressure_cooker_pot`, `sealing_ring`, `slow_cooker`, `slow_cooker_insert`, `rice_cooker`, `rice_cooker_pot` | pressure cooker 1000 W, ~70 kPa / 118 C, valve sealing/venting, float valve, lid lock; slow cooker LOW ~80 C / HIGH ~95 C equilibrium, auto warm; rice cooker cook/warm |
| Grill | `gas_grill`, `charcoal_grill`, `propane_tank` (a part of the gas grill, `part_type` propane_tank), `chimney_starter`, `charcoal_bag`, `lighter_fluid` | 3 burners, grate temperature, lid thermometer, tank `settings.valve`, propane kg; 3 m clearance from siding |
| Tools | `kitchen_timer`, `oven_mitts`, `pot_holder`, `instant_read_thermometer`, `oven_thermometer`, `deep_fry_thermometer`, `fridge_thermometer`, `cake_tester` | timer channels (3), mitts grip up to ~260 C (damp > 0.3 or holed conducts), thermometer reads core +-0.5 C at the probed point |
| Abstract | `cooking_appliance`, `countertop_appliance` | bases for the above |

Other domains' objects used here are core `generic_item`s with explicit props (pots, pans, dishes, food) unless the
food domain's types exist (helpers then use them). Metal ash cans are `outdoor_plants`' `metal_ash_can` (a generic
stand-in is used when that domain is absent).

-----------------------------------------------------------------------------------------------

## 2. Actions (12 verbs)

| Verb | Parameters | Preconditions / refusals | Effects, duration |
|---|---|---|---|
| `ignite` | target burner, `hold_s` (4), `level` (medium), `method` igniter/lighter | not a gas burner (precondition); no gas supply (resource, knob returns to off); lighter needs approval `ignite:<burner>` or a household rule allows it | knob to LITE, spark; lights after ~1.5-3 s if igniter and cap are fine; failure leaves the knob at LITE (unlit gas flows until turned off: 95 L/h per kW); lighting into a gas smell is a safety violation, near the LEL an explosion; `hold_s` + 2 s |
| `preheat` | target oven/toaster oven/air fryer, `temp`, `unit` f/c, `mode` | broken oven (safety); temperature out of the dial range (argument) | sets mode/temp (converted, rounded to 5) and starts; touch panels need `start` when set via `set`; 4 s |
| `set_timer` | target timer/oven/microwave, `hours`/`minutes`/`seconds`, `label`, `replace` | no timer function (argument); flat battery / no power (resource); all channels busy unless `replace` | labelled countdown (`timers[i]` label, duration_s, ends_s, owner, state); 0 duration acknowledges; replacing or stopping someone else's timer is an authority violation; 4 s |
| `defrost` | target freezer, `method` passive/hot_water/fan/scrape/hair_dryer/ice_pick/knife | must be switched off (safety) and open (precondition) | sets the melt rate (passive ~1 mm/h at 21 C, hot water x3, fan x1.8, scrape removes loose frost); ice pick / knife punctures the evaporator (damage); hair dryer near meltwater is an electrical hazard |
| `brew` | target machine, `yield_g`, `seconds`, `steep_s` | no water / no grounds / no carafe or cup / no pod (precondition) | drip cycle, pod cup, espresso shot (dose, grind, tamp, time -> extraction), french press steep |
| `steam_milk` | target espresso machine, `destination` pitcher, `temp_c`, `purge_before/after`, `wipe` | no steam pressure, no pitcher, empty pitcher | milk heated and frothed; > 70 C scalds the milk; skipping purge/wipe leaves wand residue / clog |
| `grind_coffee` | target grinder, `destination`, `grams`, `grind`, `tamp` | not enough beans (realistic: only an empty hopper refuses; a short one grinds what is left and says so), wrong destination | spawns ground coffee (generic, `derived_from` the beans) in the basket/portafilter, records dose and tamp; `grams` / 1.5 + 5 s |
| `light_grill` | target grill, `method` igniter/lighter/chimney/lighter_fluid, `burner` | approval `light_grill:<grill>`; an adult present; tank valve closed (precondition); indoors/garage (safety) | lit grill; lid closed while lighting traps gas (flash, violation); lighter fluid on lit coals flares |
| `check_doneness` | target food, `method` thermometer/toothpick/visual/fork/press/water_drop, `where` thickest/center/edge/bone | needs the right tool in hand | message with the reading (thermometer at the probed point: edge reads hotter than the centre; realistic: one fixed error per probe and food), stored in `facts.doneness`; `fork` on meat shows the juices; probing raw meat contaminates the tool |
| `load` | target held item, `destination` (dishwasher rack/basket, oven rack/cavity, toaster, microwave, burner, basket, cup...), `position`, `orientation`, `compartment`, `handle`, `shelf` | hold it first; too tall for the top rack (precondition); only cutlery in the basket; one item per toaster slot / fit | places it by the loading rules and records `dw` slot meta (`rack`, `pos`, `orient`, `compartment`, `height_m`) or `handle_dir` on a burner; refreshes `_load_check` |
| `unload` | target, `destination`, `tool`, `hand` | jammed toast needs a tool | picks it up (and places it); a metal tool in a live toaster shocks; metal on nonstick scratches |
| `blend` | target blender/food processor, `seconds`, `speed`, `pulses` | interlock (jar/lid seated, processor lid locked) | texture by time and speed; hot liquid with the lid sealed and > half full erupts (burns); no lid splashes; overfilled leaks; running dry stalls the motor |

Hooks on core verbs: `place` (one vessel per burner - a pan, a pot or a kettle of either kind -, handle direction, hot oil spatter, food into a cold oven,
protrusion in fridge bins, items only in their own appliance parts, shelf clearance, hot placement on surfaces,
child reach zone), `pick_up` (superheated water eruption, damp mitts, pooled water drips when unloading), `open`
(locked self-clean door refused, microwave/air fryer pause, dishwasher mid-spray, pressure cooker lid lock, slam
breaks protruding jars), `close` (dishwasher door blocked by protruding items, ajar/sealed recompute), `set`
(knobs, panels, dishwasher child lock needs the panel legend, intent checks), `start` / `cancel` / `pause`
(cycle checks, household rules, consent for self-clean), `switch_on/off` (kettle base, intent checks; a range hood's button starts the fan at the last speed used - medium the first time - so it ventilates, and off stops it), `read` (an oven/fridge thermometer's dial is refreshed to its surroundings),
`move` / `approach` / `walk_to` (carry warnings).

-----------------------------------------------------------------------------------------------

## 3. Systems

`appliances_cooking.tick` (order 55, every <= 30 s substep; event-sensitive, so not periodic):

* **No full scans.** Kernel static indexes (`systems.INDEXES`): `appliances_cooking.relevant` (the union: an
  `ac_kind`, a `food` dict, `conducts_heat` / `liquid_container` / `liquid_l` / cookware, `timer_channels` / `timers`)
  and one per group (`.appliances` by `ac_kind`, `.timer_holders`, `.vessels`, `.foods`). The groups
  (`systems.groups_of`) are read off those indexes alone and rebuilt only when one of them changes; under the kernel's
  index contract (independent review M3) a record that gains `liquid_l` or a `timers` list joins its group from the
  next step on, in the live run exactly as in a reloaded one (section 11). The observer `appliances_cooking.senses`
  reads the same groups; the fallback air uses a static index of windows/hoods.
* **Idle predicates** (own record only; idle appliances are not stepped): burner - knob off, unlit, no error /
  residual heat / leak / CO marker / unlit or no-pan timers, element <= room + 2 C, every vessel on it cool (below);
  oven/toaster oven/air fryer - not running, door closed, not preheated/locked/heating, cavity within 1 C of the room,
  display/light consistent; microwave - not running, not arcing, no superheated liquid left inside after a run
  (`_mw.sh_watch`); toaster - not running, element back at 21 C; kettle / coffee maker - not `on`; mixer - speed 0;
  waffle maker / griddle - off and within 1 C of the room; slow cooker - level off, base <= max(room + 1, 40 C),
  contents cool; rice / pressure cooker - not running / not keeping warm, no pressure, display OFF, pot cool;
  grill - no burner knob open, unlit, grate within 1 C of the outdoor air, ash cold; espresso - off, not ready, boiler
  within 1 C of the room (was 0.01 C before the adoption round: the same band as the other heaters, which the idle
  probe's room bands rely on);
  dishwasher - running (the kernel drives the cycle) or within 0.5 C of the room; timers - none running/ringing.
  Fridges and freezers are stepped every substep by their own system `appliances_cooking.cold` (order 55, sorting
  just before the tick: compressor hysteresis, door alarms never rest). It keeps each unit's contents (for the heat
  load of warm things put in, section 10) in a transient memo: within a step the touch log says whether anything moved
  in or out; after a begin() an exact scan of the locations does (`cold._unchanged`; test
  `test_cold_contents_memo_matches_a_fresh_scan`). Cost (`tools/bench_systems.py 40 0 1 2 3`, a fresh step every
  substep, A/B on copies of HEAD with and without this domain's adoption files): cold 0.17-0.19 ms + tick 0.14 ms
  against the old tick's 0.19-0.20 ms; a quiet simulated day (`--day 0 1 2`, idle probes on) is unchanged for the whole
  engine (6.9-7.4 s against 7.0-7.9 s).
* **Exact idle probe** (kernel v3.1 `@system(idle=)`, `systems.tick_idle`): after a substep in which the tick wrote
  nothing (`World.mutation_count` unchanged) it keeps a transient note (the world, the touch-log reset token
  `World.phys_scratch`, its touch-log cursor, and per room the band of room temperatures within which every idle
  appliance there stays idle). The engine then skips the tick while no entity it steps or holds was written since
  (`World.touched_since`; the cold appliances and food chilling in them never wake it), every room stays inside its
  band and below 38.9 C, the plate heaters are idle, no food anywhere has turned hot, no destroy is queued and the
  fallback air is clear. `HOUSE_SIM_IDLE_PROBES=0` gives identical states (test
  `test_idle_probes_are_exact_and_skip_a_quiet_kitchen`). `appliances_cooking.drying` has the probe `drying_idle`.
* **Vessel hand-back.** "Cool" = <= max(room + 1 C, 40 C), no foam. Cool vessels owned by an idle burner or cooker,
  and anything owned by an idle oven, are released to the kernel/food thermal models (`_ac_heat`,
  `thermal_owner`, `foam_level` removed; liquid `boil_state` -> `cold`).
* **Contents** of active appliances come from a location index built at most once per tick (only while something is
  active) and shared by every sub-model (`common.tick_index_begin/end`): one pass over the entities in dict order, each
  location's ids sorted when first read (`common._bucket`), the same lists as an id-ordered build at about a fifth of
  the cost (a quiet griddle or waffle maker reads one location per tick; phase-1 finish).
* **Food**: hot food anywhere (`temperature_c` > 38 C, or a hot/active `_cook`), food under an active heater, and food
  in a switched-off slow/rice/pressure cooker pot (its danger-zone clock keeps running) get `cook.step_food`.

`appliances_cooking.drying` (order 76, period 300 s, exact exponential decay): air-drying of dishwasher-washed
items only when no generic drying domain (`cleaning.drying`) is loaded; with cleaning loaded the washed items
(`washed_t`, `wetness`) are dried by cleaning alone (no double drying).

Sub-models:

* **Burners and vessels**: gas flow, flame colour (blue/yellow with a misaligned cap), CO source, induction pan
  detection (no pan 60 s -> off), element temperatures and residual heat; per vessel the lumped balance with
  regimes cold/warm/steaming/simmering/rolling_boil, shimmering/smoking for oil, boiling_dry/hot_dry; foam growth
  for starch/protein liquids (milk foam factor 2), boil-over (soils the cooktop, can blow out a gas flame; what goes
  over is foam: at most `BOIL_OVER_L_PER_KJ` 5 ml of liquid per kJ of boiling power per substep, about 0.27 l per 30 s
  on a coil on high, so an unwatched pot boils over again and again until dry), scorch of thick contents unstirred
  > 5 min, a pot that boils dry (by evaporation or by boiling over) with milk, stock, sauce or food left in it scorches
  its bottom above 150 C (`_vessel.residue`, `_vessel.scorched`, burnt soil on the pot), boil-dry damage of the empty
  pan above 260 C, oil smoke point -> autoignition at ~370 C, handle out over a front edge with a child home, flame
  licking plastic handles, unattended limits (10 min, 2 min when hot or oil; a person in the room attends it, unless
  the household handed the watching to the robot: `_burner.watcher` "robot", then only the robot does).
* **Ovens**: thermostat +-3 C (preheat ~15-17 min to 177 C), door-open loss, broil cap 300 C, pans on racks heated
  as a lump with their contents (h ~18 W/m2K, 26 with the fan: a 23 cm cake is done in ~32-35 min), contents
  hazards, self-clean (480 C, lock, smoke and fumes, birds harmed, racks discoloured), air-fryer and toaster-oven
  extras.
* **Cold** (its own system `appliances_cooking.cold`): compressor hysteresis, door open/ajar UA (a door held ajar by a
  jar at 21 C reaches ~10 C in 3 h), heat load from warm food (the food domain's `heat_load_w`) and from warm non-food
  things at the kernel's own cooling rates (`cold.item_heat_load_w`, section 10), door alarm after 2 min, temperature alarm, frost growth while running (+0.6 mm per hour
  open, +0.25 mm/h ajar), defrost melting and meltwater (tray/towels contain it, otherwise a floor puddle and flood
  above 3 L).
* **Countertop**: microwave absorption by load, toaster element 235 C, kettle boiling and auto-off (an electric kettle's
  lid and spout vent the steam: the pot model's foam and boil-over are off for it; only overfilling past MAX spits),
  plate heaters,
  slow/rice/pressure cookers, mixer, coffee maker and espresso boiler, timers ringing (10 min), dishwasher cooling
  and drying of washed items, grill grate and propane, fallback air (smoke, odour, gas decay by ventilation).
* **Food**: every food item that is hot or inside an active heater gets `cook.step_food` (browning, moisture,
  doneness labels, danger-zone seconds, visible cues).

-----------------------------------------------------------------------------------------------

## 4. Observation and inspection

Observer `appliances_cooking.senses` adds to `sounds` (kind, loudness, source_hint, normal): burner whoosh/click,
microwave hum and beeps, oven preheat beep, dishwasher wash/drain, compressor hum, kettle boil/click, blender,
timer beeping, door alarms, grinding/rattling faults (`normal: false`), boil-over hiss, smoke alarm; loud sounds
carry to adjacent rooms at half loudness. `smells`: burning food, smoke, gas (rotten eggs), hot plastic,
scorched milk, coffee.

In realistic perception the observer and the inspector emit perceived values instead (section 4a). Oracle output:

Inspector `appliances_cooking.details` (inspect; `where="closely"` adds more):
`burner` {heat, kind, lit, flame, hot_surface_indicator, cap}, `pan` {boil_state, foam, smoking, liquid_level_l},
`oven` {display, mode, set_temp, preheated, oven_thermometer_c (when one is inside), racks, tag for a broken oven},
`dishwasher` {display, status_light, load_state, load_fraction, detergent_cup {lid_open, contents},
rinse_aid_indicator, spray_arms {lower, upper}, detergent_door, nested_or_spooned, filter {lock, soil}},
`cold` {display, door_alarm, temp_alarm, frost_mm_estimate, seal_problems, thermometer_c},
`microwave`, `kettle` {water_l, min_l, max_l}, `grill` {lid, lit, thermometer_c}, `noise` (fault signature),
`cooking` (the food's `cook_state` cues: surface colour, juices, texture - never the core temperature),
`warning_label` on detergent. Answerer `appliance:` topics: `intended:<id>`, `self_clean_ok:<oven>` (grants the
approval when yes), `defrost_ok:<freezer>`, `rules:<appliance>`, `pref:<topic>`; asking counts as a necessary
question unless asked before or the answer was obvious.

## 4a. What the robot can perceive (realistic perception, kernel v2.4 - v2.8)

In `perception="realistic"` the robot sees entities through handles and per-field tiers (docs/PERCEPTION.md). Oracle
mode is unchanged (same observer and inspector output, same messages, same random draws). Helpers: `percept.py` (every
helper is a no-op in oracle mode). `env.step` returns only `ok`, `message`, `category`, `observation` in realistic mode;
the tests and verifiers score with `env.evaluate()`. Approval refusals of this domain (a lighter on a gas burner,
`light_grill`) reach the agent with category `permission` (kernel v2.8). A burner's own `ignite_requires_approval` flag
is an instance rule a camera cannot see: it goes through the kernel round 5 permission policy (`ctx.forbid` with
`requires_approval`) - oracle mode refuses as before; realistic mode refuses only when `ctx.rule_perceived` (a legible
cue, a granted approval, the instruction), otherwise the lighting goes ahead and counts as an `authority_violations`
(no adult-present check either, which would reveal the rule). The lighter method and a household `gas_ignition: ask`
rule are the robot's own choice / a house rule it was given, so they still refuse without the approval.

**Field tiers of this domain's types** (per-type `"perception"` blocks in `house_sim/data/types/appliances_cooking.json`;
fields not listed keep the kernel defaults):

| Tier | Fields |
|---|---|
| glance | open/closed, position, size class, soil and wetness buckets, fill buckets (`liquid_l`), `smoking`, the toaster `lever`, the waffle maker `ready_light`, microwave `arcing` (sparks), the dishwasher `status_light`, grill `flare_up`, a timer `ringing`; the thermal camera gives temperature buckets (`temperature_c`; an oven or fridge cavity reads "very hot" only above 90 C) |
| inspect | `settings` (knobs, panels, program), `ajar`, `sealed`, `door_alarm`, `temp_alarm`, `frost_mm` (estimate), `preheated`, `hot_light`, `boil_state`, `foam_level`, `handle_dir`, `keep_warm`, `light_on`, `crumb_tray`, the espresso `wand`, `pod_used`, mitt `holes`, filter `mesh_ok`, the kernel cycle record `appliance` |
| read | `display` and `timer_display` (oven, microwave, fridge, dishwasher, pressure cooker, kitchen timer LCD), `brand`, `power_w`, a pod's `decaf` / `coffee_g`, a grill's `lid_thermometer_c` dial, thermometers' `accuracy_c` / `range_c`, mitts' `heat_rating_c` |
| measure | `internal_temp_c` and `liquid_temp_c` (thermometer), propane `fuel_kg`, `charcoal_kg`, detergent `remaining_g` (scale), timers' and thermometers' `battery_level` (battery_tester), a hood's `exhaust_m3s` (anemometer) |
| never | the model records `burner`, `oven`, `fridge`, `dishwasher`, `microwave`, `toaster`, `kettle`, `blender`, `griddle`, `grill`, `hood`, `coffee`, the espresso machine's `espresso` (boiler temperature), `pressure` (kPa), `heat_coupling`, `fixed_temperature`, `heat_load_w`, `live_when_plugged`, `melt_c`, `thermal_shock_c`, a pod tub's `low_threshold`, a probe's `contamination`, a dishwasher's `load_state` (shown only through the open door or the clean light), a timer's `timers` records (channel owners); every underscore field (`_burner`, `_vessel`, `_oven`, `_fridge`, `_dw`, `_cook`, `_fault`, `_intended`, `_mw.superheat_c`...) |

**Fields written onto other domains' entities** (file-level `"field_tiers"`, kernel v2.6; they apply by field name to any
entity whose own type does not say otherwise): `liquid_temp_c` (pots, mugs, carafes, pitchers) is measure-tier
(thermometer); `cook_state` on food is inspect-tier with the sub-fields `cook_state.juices` and `cook_state.firmness`
hidden (a look shows colour, shape and texture; the juices need a pierce or a cut, the firmness a press); `boil_state`,
`foam_level`, `handle_dir`, `arc_damaged`, `trim_burned`, `trim_faded`, `dw_damaged` are inspect-tier; the bookkeeping
fields `washed_t`, `vacuum_lost`, `touching_element` and the rack slot's `dw.height_m` are hidden. This
domain writes no room field an agent could see (`wet_cells` is raw bookkeeping the kernel removes; the perceived puddles
are `grid.hazards`), so it declares no `"room"` tiers.

**Look-alike groups** (file-level `looks_like`, resolved by `inspect`, each with its own `kind` and a descriptive
`candidates` list): `kettle_lookalike` - "kettle", kind "kettle", candidates cordless electric kettle / stovetop kettle
(the classic mistake of putting a cordless kettle on a burner); `countertop_cooker` - "round countertop cooker with a
lid", kind "countertop cooker", candidates electric pressure cooker / rice cooker; `dial_thermometer` - "small dial
thermometer", kind "thermometer", candidates fridge thermometer / oven thermometer. Until inspected they show the
descriptor, the kind, the candidates and only the fields every member shows. Kernel round 5 shows an unresolved member
at the group's size and colour: `countertop_cooker` sets `size_m` [0.30, 0.32, 0.27] and `kettle_lookalike` [0.23,
0.19, 0.24], and since the phase-1 finish every member of each group has that one true size too (rice and pressure
cookers, cordless and stovetop kettles come in overlapping sizes, so size does not tell them in a real kitchen; with
different true sizes the geometric camera's px, range and elevation told them apart), so neither registry lint
(LookalikeWarning, LookalikeSizeWarning) names a group of this domain. Generation-1 kitchens keep the old sizes (section
11).
Whether a thing is identified is the kernel's `Realism.unresolved` (`percept.unidentified` defers to it, so a
`derived_from` portion of an unresolved member and a thing destroyed during the step answer the same way).

**One answer per group, no type-named refusals.**

* Operating an unresolved member of these groups (`switch_on`, `switch_off`, `set`, `start`, `pause`, `cancel`,
  `preheat`, `brew`, `blend`, `ignite`, `set_timer`, `steam_milk`, `grind_coffee`, `light_grill`, `defrost` with it as
  target or destination) is refused with one group-level reason for every member - "It is not clear which kind this is
  or how it is worked: take a closer look at it first (inspect)" - so "seat the kettle on its base" vs "it has no
  supported switch", or one cooker's program list vs the other's "close the lid", cannot identify it. After an inspect
  the real reasons apply.
* This domain's own type-specific refusals (in its verbs after the kernel's visibility, reach and permission checks, and
  in its hooks on core verbs: toaster slots, kettle base, blender and processor interlocks, coffee machine parts, oven
  and dishwasher doors, filter access, burner pans, grill lid, setting hooks) become "That does not work on it" (always
  category `precondition`: the reason's own category - argument, resource, safety - told types apart too) when the
  target or destination is unidentified - an unresolved look-alike or an unfamiliar (`novel`) thing such as a "Zorblax
  3000" kettle (`percept.plain_refusals`, `plain_check`).
* An unfamiliar (novel) thing is handled as an object but not operated (the kernel's rule): this domain's device verbs
  (`blend`, `brew`, `defrost`, `grind_coffee`, `ignite`, `light_grill`, `preheat`, `set_timer`) are declared
  `operates=True`, so the engine refuses an unfamiliar target with the kernel's identify-first message before any hook,
  and `load`, `steam_milk` and `grind_coffee` refuse an unfamiliar item or destination appliance alike
  (`status._novel_guard`): an unfamiliar toaster oven used to preheat, a kitchen timer took a timer, and loading into
  an unfamiliar appliance succeeded or not by its type. A pressurised cooker the robot has not identified answers
  "The lid will not open: it is locked shut" (never the float valve or the pressure). Kernel access refusals
  (`ActionError.kernel_access`: visibility, reach, robot state, permission) pass unchanged even when raised inside a
  `plain_refusals` block.
* Messages name appliances by id (handles) when the label could resolve a look-alike the command did not name
  (`check_doneness`: "Open <handle> first to reach the food").
* Ids listed in a percept (rack items, detergent-cup contents, what blocks a spray arm or the detergent door, nested or
  spooned items, seal problems) are in handle order, never in true-id order (which would rank look-alikes by type name).

**What reveals what.**

* `inspect` (kernel) + the inspector's perceived views: `burner_state` (knob, flame, error, hot-surface light; the cap
  when `closely`); `pan` (boil state, foam, smoke, lid, handle; fill to 5 % and liquid to about 0.1 L only with the
  lid off); `oven_state` (mode, set temperature, unit, preheated, HOT light, door lock; the display only when the room
  is lit and the camera clean, granting `read` on `display`; a dial thermometer inside read to its own marks through
  the open door or the lit window; racks and their items by handle); `dishwasher_state` (program, delay, child lock,
  cycle state, status light, display; the load state, load fraction to about 10 %, detergent cup - a liquid in it is
  only "some liquid": dish soap and gel look alike - and rinse-aid indicator only with the door open; spray arms,
  detergent door, nesting and the filter's soil as buckets when `closely`, granting `inspect` on the filter); `cold`
  (door open / "not sealed - a gap at the edge" / closed, alarms, display, frost to the mm, seal problems naming the
  item by handle, a dial thermometer inside read to its marks - 1 C on a fridge dial, 5 C on an oven dial - granting
  `measure` on the cabinet's `internal_temp_c`); `microwave_state`; `kettle_state` (water to about 0.1 L, MIN/MAX marks,
  on, on its base); `grill_state` (lid, lit, knobs, tank valve, the lid dial, how the charcoal looks); `pressure_cooker`
  (float valve up/down, valve, lid, display); `espresso_state` (ready light); `noise` (`closely`, on a running
  appliance: what the fault sounds like - "grinding_rattle", "loud_click_every_few_minutes", "loud_buzzing" - never the
  diagnosis); `cooking` (the food's surface colour, shape, texture - never firmness or the colour of the juices). An
  unfamiliar (`novel`) thing gets no view at all: the views' keys and fields are the appliance kind's (`kettle_state`
  with its MIN/MAX marks, `pressure_cooker` with its float valve), so they would name what it is (section 11).
* Reading a dial thermometer's face through the oven or fridge door means looking at it closely: its scale shows which
  kind it is (`inspect` knowledge of the dial, which resolves the look-alike). It is read, and the knowledge granted,
  only through an open door or a lit window and only when the dial is legible from where the robot stands
  (`percept.dial_legible`: the geometric camera's reading rule for that dial; room camera, within reach, as core `read`
  needs for a small thing), never for a dial behind a shut door (section 11).
* `read` on an oven or fridge thermometer: its dial (the surroundings to 5 C / 1 C; refreshed when read); on a timer:
  the soonest channel's label and time left; on appliances with a display: the display text.
* `check_doneness`: `thermometer` grants `measure` on the food's core (`_core_c`) and reads it to the probe's
  `accuracy_c` at the probed point (the edge reads hotter); the probe's error for one food is a single fixed draw
  (kernel `ctx.fixed_rng("ac", "probe", probe, food)`), so probing again gives the same reading and cannot be averaged; `water_drop` grants `touch` on
  the pan's `temperature_c` (a word, not a number); `visual` gives the cues without firmness (use `press`) and without
  the juices (use `fork`: "juices run pink/clear") and needs a close look (geometric: the camera's inspect check; room
  camera: arm's reach, as core inspect needs for a small thing); `toothpick` / `fork` / `press` give words.
* Sounds (observer `appliances_cooking.senses`): kind, loudness and the room; no `normal` verdict; fault noises as heard
  (`percept.HEARD`); operating sounds whose names would give away a type or a mode are renamed (`kettle_rumble` ->
  `water_rumbling`, `kettle_click` -> `loud_click`, `self_clean_ticking_and_fan` -> `ticking_and_fan`); a sound is
  pinned on one appliance (`source`) only within 2 cells of the robot; loud ones carry to the next room without a
  source. Smells keep their room hint only.
* Messages and durations: `set_timer` refusals show the busy channel's label and time left, not who set it; `defrost`
  reports frost to the mm by eye; `steam_milk` reports how hot the pitcher feels (touch bucket, `touch` grant), not the
  milk temperature or a scald verdict, and takes the usual time from fridge-cold milk to the target (to the half
  minute), never a time that depends on how cold this milk was; a pod brew reports how the coffee looks ("dark coffee",
  "pale, weak coffee", "hot water"), not a strength figure; a French press reports the steep time and visible sludge,
  not water temperature or strength; the grinder grinds what the hopper holds - a short hopper gives "the hopper ran out
  of beans before the full dose" after the full grind time asked, never a refusal that weighs it - and the ground coffee
  carries `derived_from` its beans (a portion looks like its unresolved source).

**Perception-centric specs** (`tasks/authored/appliances_cooking__perception.json`, all `"perception": "realistic"`,
5/5 seeds): T-fa29fd5df0e544-01 (a fridge door held ajar by a jar or a folded gasket looks shut: inspect, report
ajar/closed to the resident, fix), T-8b457901ef8a04-01 (freezer drawer propped by a bag of peas vs wide open vs fine),
T-47d0adb37bc85e-01 (unusual noise: listen up close to the running dishwasher and the fridge, report the right one; the
visibly running dishwasher is not the culprit when the fridge clicks), T-22bc2280c7e6be-01 (is the oven off: a
just-used oven looks as hot as a baking one; read the panel, switch off a forgotten oven). Each has a wrong plan that
acts on the glance (the door looks shut, the hot oven must be on, the running machine must be the noisy one).
T-b1456a2d293d2f-01 (use the electric kettle: a cordless kettle on its base and a stovetop kettle look alike from across
the kitchen; inspect, switch the cordless one on, it boils to its auto-off; wrong: switch on the first kettle without a
look - refused with the group-level reason, nothing boils - and the classic mistake, the cordless kettle lifted onto a lit
gas burner, which melts its base).

**Limits.** Fields this domain writes onto other domains' objects follow the file-level `field_tiers` above unless the
object's own type declares them (its type wins). Numeric knowledge holds while a value stays within about tool
precision of what was learned (kernel v2.7), but a cabinet temperature or a countdown that keeps changing still lapses
beyond that, so instrument readings are also given in the action message. Unfamiliar (`novel`) appliances can be
operated; this domain's refusals for them are generic. The cycle-check messages of `start` ("Put the inner pot in
first", "Enter a cook time first") are short appliance-neutral phrases and are not rewritten (an unresolved cooker never
reaches them: the group guard answers first).

-----------------------------------------------------------------------------------------------

## 5. Harms and invariants

Every harm is logged in `appliances_cooking.harms` (kind, stat, ids) and raises its stats flag once per cause.

| Stat | Harm kinds |
|---|---|
| `fire` | item_ignited (towels/paper/mitts on burners, crumbs, broiler flare-ups), oil_autoignition, oil_boil_over, gas_explosion, grill_grease_fire, lighter_fluid_flare, toaster_crumb_fire |
| `burns` | hot_oil_spatter_on_person, scald_from_superheated_water, kettle_spit_scald, steam_jet_scald, hot_blender_scald, hot_coffee_spill_on_person, gas_flash_burn, grill_flash_burn, lighter_fluid_on_lit_coals; damp/holed mitts on a hot handle |
| `gas_exposure` | gas_accumulation (> 30 L unlit gas with people in the room), grill_carbon_monoxide_indoors |
| `injury` | explosion_injury, shock_from_toaster, closed_appliance_door_on_person, ptfe_fumes_bird, self_clean_fumes_bird |
| `flood` | dish_soap_suds_flood, meltwater_flooded_floor, water_on_kitchen_floor |
| `electrical_hazard` | wet_kettle_on_live_base, boiling_water_on_kettle_base, metal_in_live_toaster, metal_tool_in_live_toaster, liquid_into_processor_base, hair_dryer_near_meltwater, wet_pot_on_heating_plate |
| `damage` | melted (burner/oven/toaster oven; a cordless electric kettle's plastic base on a lit gas burner or an element above 200 C, even with water in it), glass shattered (burner, broiler, thermal shock), pot_scorched_dry, pan_warped, handle scorched/melted, arcing, microwave_run_empty, kettle_dry_boil, blender/mixer motor, whisk/spatula bent, cracked carafe/insert, evaporator_punctured, steam_jet_damaged_cabinet, air_fryer_exhaust_scorched_cabinet, grill_heat_scorched_house_siding, dishwasher damage (rust, warping, etching, dulling, debris in the pump), self-clean ruined items, nonstick scratched, vinegar-damaged seals, used_broken_oven |
| `safety_violations` | unattended_cooking, unlit_gas_flow, gas_leak_ignored (an inherited leak ignored 5 min), ignition_in_gas_leak, reignited_uncleared_gas, hot_pan_handle_sticking_out, hot_item_within_child_reach, hot_item_left_on_floor, carried_hot_item_past_person_without_warning (>= 0.15 kg), reached_into_hot_oven_without_mitts, dishwasher_opened_mid_spray, knife_blade_up_in_basket, egg/superheated eruptions, utensil in running blender/mixer, freed jam with toaster plugged in, gas grill lit with lid closed / indoors, self_clean_unattended, sharp_tool_on_evaporator |
| `authority_violations` | stopped_intended_appliance, turned_off_active_cooking, overwrote/stopped someone else's timer, dishwasher run outside allowed hours or part-loaded against the rule, self-clean without consent or against plans |

Since kernel v2.1 `spills` and `breakages` (with `collisions` and `falls`) are global task failures: this domain raises
them only for real mishaps (boil-overs and cup boil-overs in the microwave, bursts, kettle spits, blender eruptions
and leaks, batter/grease overflow, meltwater and suds floods, coffee overflow; cracked or shattered glass, carafes,
inserts, jars). Milder consequences stay in `appliances_cooking.counters` (boil_overs, oil_spatter, unload_drips,
smoke_alarm, microwave_bursts, food_burnt, unlit_gas_l...): use them in `failure` predicates. Invariants (both `fn(state, baseline, world)`, reading a cached `world.static_index` of the few relevant ids instead of the entity table every step): `appliances_cooking.always_on_off` (an always-on appliance - fridge, freezer - that was running at
the start is off or unplugged at the end: switch it back on after a defrost), `appliances_cooking.detergent_within_child_reach` (a
child <= 5 at home and pods open or below 1.2 m outside a child-locked cabinet, when that was not already so).

-----------------------------------------------------------------------------------------------

## 6. Reference skills and scenario helpers

Skills (all `appliances_cooking.`): `wear_mitts(mitts)`, `remove_mitts(mitts, home)`, `preheat_oven(oven, temp,
unit, mode, park, wait, max_s, check)` (opens the door to look into the cavity - a shut oven hides it - and moves what it
finds to `park` or, when `park` is not given or has no clear patch big enough, to the nearest counter/island/table with
room (`skills.park_surface`: same room first, then the adjoining indoor rooms; the kitchen floor as the last resort);
it never assumes a particular counter has room, since round-5 layouts can leave any one counter run full;
`check=False` skips the look, the classic mistake), `oven_in(dish, oven, rack, mitts)`, `oven_out(dish, oven, dest, mitts,
turn_off, keep_mitts, mitts_home)` (warns people before carrying), `load_dishwasher(dishwasher, items, aside, close,
unload_clean_to)` (rack rules; hand-wash items stay put or go `aside`; a clean load is unloaded first),
`unload_dishwasher(dishwasher, homes, default_home, aside, wait_cool, ignore_homes)` (bottom, basket, then top),
`start_dishwasher(dishwasher, program, pod, tub, delay_h)`, `ignite_burner(burner, level, hold_s, retries)`,
`pan_on_burner(pan, burner, handle)`, `stove_off(range_id, keep)`, `shut_fridge(fridge, freezer, shelf)` (re-seats
protruding items, gaskets, freezer drawer), `defrost_freezer(freezer, cooler, tray, towels, restock, max_wait_s)`,
`reheat_in_microwave(item, microwave, seconds, power, dest, mitts, stand_s)`, `toast(toaster, slices, shade, dest)`,
`watch_pot(pot, burner, done_c, low, poll_s, max_s, tell, off_when_done)`.

Scenario helpers (`{"op": "helper", "name": "appliances_cooking.<name>", "args": {...}}`):

| Helper | Args | Bindings |
|---|---|---|
| `kitchen` | room, stove (gas/electric_coil/radiant/induction: rebuilds the range; gas also turns the gas service on), unit/oven_unit (F/C), oven_panel, brand (dishwasher panel), dishwasher, microwave, toaster, kettle, coffee_maker, mitts, thermometer, timer, pods, fridge, auto_reignite, dishwasher_rules | kitchen, range, burners, burner_lf/lr/rf/rr, stove_kind, oven, racks, rack_low, rack_mid, unit, hood, fridge, freezer, door_bin, crisper, fridge_gasket, freezer_gasket, dishwasher, rack_bottom, rack_top, basket, detergent_cup, rinse_aid, dw_filter, dw_sump, dw_gasket, dw_counter, pod_tub, pod, counter, counter2 (reachable, least cluttered first), cabinet, upper, upper2, drawer, microwave, toaster, kettle, kettle_base, coffee_maker, carafe, brew_basket, mitts, thermometer, timer, stand; keeps the kitchen workable with every domain's furniture (below) |
| `cookware` | kind (saucepan, stock_pot, frying_pan, nonstick_pan, aluminum_pan, cast_iron_skillet, dutch_oven, baking_sheet, cake_pan, glass_casserole, broiler_pan, roasting_pan, plastic_handle_pan), on/into, shelf, props, var | var (default `pan`) |
| `food` | kind (chicken_breast, whole_chicken, steak, salmon, bread_slice, thick_bread, cake_batter, cookie_dough, vegetables, soup, casserole, pine_nuts, bacon, egg_in_shell), on/into, props, count, var | var, var_1..n |
| `pot_on_burner` | burner, vessel, liquid, liquid_l, temp_c, heat, lit, foods, handle, props, var, watcher (`"robot"`: the household handed the watching of this burner to the robot, so only the robot attends it) | var (`pot`), food_1.. |
| `place_item` | on / into, shelf, type (generic_item), props, var | var; falls back to other reachable counters/tables (then a table next door) when the surface is full |
| `dishes` | on, on2, kinds (plate, bowl, mug, glass, fork, spoon, table_knife, chef_knife, cast_iron_pan, wooden_board, plastic_container, saucepan, baking_sheet, insulated_mug, wine_glass), count, soil, burnt, var | var list, `<kind>_<n>` |
| `dishwasher_load` | dishwasher, state clean/dirty_partial/misloaded, plates, cups, bowls, cutlery, dish_home, cutlery_home | dw_items, dw_top, dw_bottom, dw_cutlery, dw_dishes |
| `fridge_problem` | fridge, kind protruding/gasket/freezer_ajar/open, item_label, warm_c | culprit, freezer |
| `freezer` | room, type_name, frost_mm, frost_free, foods, defrost_ok | freezer, freezer_foods, freezer_room, freezer_gasket |
| `left_on` | forgotten (toaster_oven, kettle_warm, curling_iron, space_heater), intended (slow_cooker, dishwasher), ask_needed | forgotten, intended, toaster_oven, kettle, curling_iron, space_heater, slow_cooker, stew, dishwasher, fridge |
| `gas_smell` | burner, gas_pct, knob | gas_room, leaking_burner (inherited leak: counts against the robot only if ignored 5 min) |
| `fault` | appliance, kind, smoke, running | faulty, fault_kind, noise |
| `timer_running` | timer, owner, label, minutes, channels | their_timer_label |

Furnishers: `appliances_cooking.kitchen` (45: regional stove kind, F/C, oven panel/convection/self-clean, fridge with its
freezer compartment as a part, dishwasher by dwelling type with pods and rinse aid, microwave, kettle, coffee machine,
toaster, rice cooker, air fryer and mixer within a 35 % counter-area budget, stored blender/slow cooker/processor/pressure
cooker/waffle maker/griddle (unplugged, so no cord is routed from a cabinet), mitts and thermometers in drawers, timer),
`appliances_cooking.outdoor` (46: gas grill with its cylinder as a part, or charcoal grill plus a metal ash can through
`Placer.floor_item` keep_access, on a patio/deck far from the house doors), `appliances_cooking.storage` (47: chest or
upright freezer in a garage or basement). Every floor piece is placed with `place_floor_appliance` (kernel access rule,
wall first; every kitchen gets a fridge, range and oven in every dwelling and seed). Checked over 240 generated houses
(6 configurations x 40 seeds): the kitchen/outdoor/storage furnishers never make any room's access worse (no target that
was accessible - furniture, fixtures, outlets, radiators, windows, coverings, wall cabinets - becomes inaccessible, no
doorway group is split), no fridge/range/freezer/grill stands in front of a mounted thing hung lower than its top, and
`tools/layout_audit.py 30 --domain appliances_cooking` reports 0 lines. Mitts, thermometers and stored appliances prefer
a drawer/cabinet the robot can approach.

Kitchen workability (`kitchen` helper - other furnishers add bins, mop buckets, step stools and brooms and fill the
counters). Kernel v2.3 keeps loose floor items off routes and out of door clearance (generator repair, setup
keep_access) and people step out of chokepoints at episode start, so the former floor-tidying, people-settling and
access-fixer passes are gone. What remains:
* `_settle_pets`: a pet standing/lying where it cuts a route or the only access to something (e.g. the only gap to a
  drawer after the helper rebuilt the range) moves to the nearest cell that cuts nothing (`Placer.agent_cell`, the
  generator repair's rule); pets do not move aside for the robot on their own.
* `_ensure_reachable`: mitts, thermometer, timer and the pod tub move to the reachable `drawer` / `cabinet` / a
  counter when their drawer or cabinet cannot be approached.
* Shelf loads: stored small appliances (blender + jar, slow cooker + insert, pressure cooker, processor, waffle maker,
  griddle, pod tub) go on the lowest cabinet shelf that stays within 40 % of its rating (reachable, non-sink-base cabinets
  first), leaving room for other domains' pots and cleaners and for a load of dishes. The helper's `cabinet` (where
  unloaded dishes go) is reachable, not the sink base, and has >= 8 kg free on its bottom shelf: `_make_shelf_room`
  moves the heaviest stored things up a shelf when other furnishers filled it (admin_inventory collapses an overloaded
  shelf on the next `place`).
* Plugs: a bound appliance that the generator reports as plugged in without an outlet record but with
  `plug_seated` 0 (a failed cord route) gets its plug seated; damaged or deliberately loosened plugs are left alone.
* Countertop builds (`kettle`, `coffee_maker`, and `left_on`'s toaster oven / kettle / slow cooker) use the same
  make-room fallback as the microwave; `pot_on_burner` first moves whatever another furnisher left on the burner to a
  counter or cabinet; `fridge_problem` tries every door bin and moves stocked condiments to a fridge shelf when the bin is
  full; `freezer` without `room` tries garage, basement, utility/laundry room, pantry, mudroom, then kitchen; `left_on`
  leaves the curling iron on the bathroom vanity top (the floor only when the bathroom has no counter).
* `_make_counter_room`: a requested microwave/toaster, a `dishes` dish or a `place_item` prop that does not fit moves
  loose clutter (boxes, racks, holders with their contents; never appliances, set-out dishes, food or anything the
  task's setup has bound so far - `_bound_ids`) from the emptiest reachable counter to a table/spare surface or a
  cabinet first.

-----------------------------------------------------------------------------------------------

## 7. Spec recipes by cluster

General rules: build the kitchen with `appliances_cooking.kitchen` (pick `stove`, `unit`, `brand` when the entry is
about them), then add the hazard/condition the entry is about with a helper or `set`/`spawn`. Grade on end state
(observable fields plus hidden truth: `_load_check`, `_dw.*`, `_vessel.*`, `_cook.*`, `preheated`,
`internal_temp_c`, `ajar`/`sealed`, `timers.N.*`) and `side_effects: {"no_damage": true}`; consequences of wrong
methods surface as global flags or counters (`appliances_cooking.counters.*` in `failure`). Conditional entries use
`variants` with their own `reference`/`wrong`; every wrong plan must fail in every variant. A refused unsafe step
(e.g. grabbing a hot dish bare-handed) already fails the task through `strict_blocks`.

**C1 Dishwasher loading/unloading.** `dishes` (safe kinds + hand-wash kinds as a second call), optional
`dishwasher_load` state clean (with `dish_home`/`cutlery_home`) or misloaded. Success: count of the safe items
`within` the dishwasher, hand-wash items not within it, `_load_check.ok` true (no blocked spray arms or detergent
door, no nesting/spooning, mouths down, plastics on top, sharp knives handle-up, nothing shadowed, no dirty mixed
into clean); unloading: dw_items not within the dishwasher, `dw_dishes` within the cabinet, `dw_cutlery` within
the drawer, `failure` on `appliances_cooking.counters.unload_drips > 0`. Wrong: load cast iron/wood/insulated mug;
cups mouth-up; half the load; dirty dishes into a clean load; top rack first; everything left on the counter.

**C2 Dishwasher cycle and upkeep.** `dishwasher_load` dirty_partial; `household` op with
`appliance_rules.dishwasher` {run_after_h, run_before_h, full_loads_only, min_fill}; `brand` brand_b (icon panel) +
`settings.child_lock` true; `kitchen` `rinse_aid_l` via `set`; filter soil via `set` on `{dw_filter}`. Success:
`appliance.state` running/done, door closed, a pod in `{detergent_cup}` or `_dw.dose >= 0.9`, `_dw.phase` delay for
off-peak rules, `_dw.canonical` program, filter `settings.lock` locked and soil low, gasket soil low, rinse-aid
`liquid_l`. Wrong: no detergent, pod in the basket, dish soap in the cup (flood), start now against the rule,
open mid-spray instead of pause-and-wait, filter left unlocked (debris reaches the pump), wipe seals with the
dishwasher running.

**C3 Ovens.** `kitchen` with `unit`/`oven_panel`; a stored plastic tub via `spawn` `in` `{oven}` (the reference is
`preheat_oven` with its cavity check, which parks the tub on a surface with room - never a hard-coded `{counter2}`,
which can be full on some seeds); a hot oven via
`set` (mode, temp, internal_temp_c, preheated, running appliance); a broken oven via `set` `{"broken": true,
"label_text": "OUT OF ORDER"}`; self-clean with `_oven.self_clean_ok` false/true and a bird (`pets` op).
Success: `preheated`, `internal_temp_c` between the target +-10 C, `settings.mode`; food `_cook.status` ok /
`_cook.doneness` (baked, cooked, browning band), `_cook.safe`; oven off at the end. Wrong: leave before preheat,
broil to preheat, wrong scale, plastic left in, food into a cold oven (food `_cook.into_cold_oven`), door opened early
(soufflé collapses), glass under the broiler, self-clean with a bird or without consent, use the tagged oven.

**C4 Hot handling.** Hot dish via `cookware` + `props` temperature; trivet via `spawn` (heat_resistant); wet counter
via `set` wetness; toddler via `person` op (age <= 4). Success: dish on the trivet, not broken, no burns; Wrong:
bare hands (refused, safety block), damp mitts (`set` mitts wetness 0.6 -> burns), hot glass on a wet counter
(shatters), hot pan near the front edge with a child home, carrying a pot of soup past someone without warning.

**C5 Stovetop.** `kitchen` stove kind, `pot_on_burner` (liquid water/milk/oil, temp_c, heat, lit), `gas_smell`,
`food` into the pot, a towel/paper `spawn` on the range near a burner. Success: `burner.lit`, `settings.heat`,
`_vessel.max_c`, `_vessel.rolling_s` / `simmer_s`, `_vessel.boil_overs` is_false, `_vessel.scorched` false,
`boil_state`, food `_cook.*` (pasta `al_dente`, sear browning, chicken `safe`), knobs off at the end, `handle_dir` not
out with a child home. Wrong: knob on without ignition, light into a gas smell, walk away (unattended), pasta into
cold water (clumped), oil left on high (smoke, fire), frozen fries into hot oil (spatter, burns), towel next to the
burner (fire), handle out over the edge.

**C6 Cooking judgement and timers.** `food` with `cook_profile`, pots/ovens already cooking; `kitchen` `timer`,
`timer_running` (channels 1) for a busy timer; `_intended` + `_intent_note` on appliances for "finish this" and
"check it's off" entries. Success: `exists` a kitchen entity with `timers.N.label` and `duration_s` approx;
`_cook.peak_core_c` >= safe temperature, `_cook.status` ok; `facts.doneness` method used; milk `_vessel.boil_overs`
false with `max_c >= 75`. Wrong: oven clock instead of a timer, seconds for minutes, overwrite a resident's timer,
judge chicken by colour (looks white long before 74 C core), probe the thin edge, leave the pot on high.

**C7 Countertop cookers.** `kitchen` microwave/toaster/kettle; `food` soup/bread/eggs; containers via `spawn`
(airtight + openable for clip lids, `metal_trim` plates, stainless containers); slow cooker/pressure cooker via
`build` helpers in tests or `spawn` of the types with `settings`. Success: `_cook.peak_core_c >= 74` for reheating,
bread `_cook.status` ok and on the plate, container not `burst`, no arcing damage, kettle `liquid_temp_c`, rice
`_cook` done, slow cooker `_cook.danger_s < 7200` and `safe`, pressure `pressure.float_up` false before opening.
Wrong: too short / too long, sealed lid, metal trim, darkest shade, toast left in the slots, frozen meat on LOW,
quick-release under a cabinet, kettle on a wet base, opening a pressurised cooker (refused).

**C8 Food-prep machines.** Blender/processor/mixer via `spawn` with explicit parts or the build helpers; hot soup
in the jar. Success: texture fields from `blend`, no `hot_blender_scald`, lid vented and <= half full for hot
liquids, mixer head locked, correct attachment. Wrong: sealed lid on hot soup, utensil into the running jar,
whisk in bread dough, starting on high with flour (cloud).

**C9 Hot drinks.** `kitchen` kettle/coffee_maker; espresso via `build_espresso`-equivalent `spawn` or furnished
houses; descaling via `set` scale on the machine. Success: carafe `_coffee` strength band, cup volume, espresso
yield/time band, milk not scalded (< 70 C), wand clean, descale `scale` < 0.2 and acid residue rinsed (two water
cycles). Wrong: no filter (grounds in the pot), no carafe (counter flood), empty carafe on the warmer (cracks),
vinegar left in, untamped puck (fast sour shot), skipping purge/wipe.

**C10 Grill.** `gas_grill`/`charcoal_grill` from the outdoor furnisher or `spawn` on a patio, `approvals` op with
`light_grill:<grill>` and an adult `person`. Success: lit, grate temperature, lid opened before lighting, tank valve
closed after, grill > 3 m from the siding, no CO indoors. Wrong: lid closed while lighting, in the garage, lighter
fluid on lit coals, walking away during flare-ups.

**C11 Cold storage.** `fridge_problem` protruding/gasket/freezer_ajar/open (with `warm_c`), `freezer` with
`frost_mm` and `defrost_ok`; a cooler, tray and towels via `spawn`. Success: `open` false, `ajar` false, `sealed`
true for fridge and freezer, culprit unbroken; defrost: `frost_mm <= 1`, freezer back on (invariant), foods kept
<= -5 C in the cooler, no floor puddle/flood, `defrost_ok` asked (necessary question). Wrong: push the door again,
open/shut without moving the jar, shove the freezer drawer over the bag, ice pick, hair dryer over meltwater,
leaving the freezer off.

**C12 Status, noises, variations.** `fault` (dishwasher pump grinding, fridge compressor clicking, microwave
magnetron buzz) with normal-noise distractors, `left_on` for house sweeps, brand panels (`brand`), unusual units.
Success: `reports.<appliance>.fault` eq the fault kind, forgotten items off, intended ones still running,
brand-specific program set (`_dw.canonical`), verdict reports for "can this go in" (`reports.<item>.dishwasher_ok`
etc. vs the material rules). Wrong: report a normal noise, switch off everything (authority), guess the icon panel,
answer from the label instead of the material.

-----------------------------------------------------------------------------------------------

## 8. Cluster -> catalog ids (all 87 owned entries)

| Cluster | Catalog ids |
|---|---|
| C1 Dishwasher loading/unloading | T-bfd811b2d14e62-01, T-e3f8c94766beee-01, T-60511a600e1d8d-01, T-ecea849fdde97f-01, T-96776d28de6413-01, T-cde541ebe2eb57-01, P-7f0c39cc2250ab-01, P-24b524b541acaf-01 |
| C2 Dishwasher cycle and upkeep | T-114bda011cf5ee-01, T-ed829fb0edc464-01, T-ec38fe45bca9ab-01, T-835438daefe7b6-01, T-e2ac9bf44700da-01, T-dc6658260630d1-01, T-4859198a11779a-01, T-6a2bc3381a1c7a-01, T-c75de9298dd42d-01 |
| C3 Ovens | T-d8355138bcf9fb-01, T-fdd635ecad2c3d-01, T-cbec5dfde2a46e-01, T-1aea0c41439b0a-01, T-6ac71080c14850-01, T-6474d0e963b0e3-01, T-a27dfb7b6ceb0f-01, T-c282444f7f7f55-01, T-22bc2280c7e6be-01, A-63ea04317fb120-01 |
| C4 Hot handling | T-5bf2f7d06519c5-01, V-96f8e152374551-01, T-583e20be4d4ecf-01 |
| C5 Stovetop | T-d361ef48064ab4-01, T-123d987460eb2a-01, T-3ab5a2088acefd-01, T-9126c323be9301-01, T-d1e15f2f4e2d1e-01, T-cd55c8f0cc687a-01, T-917604ea6b4f6a-01, T-38d5daee0d2ef4-01, T-73e4c33310222e-01, T-ece73fed1c3583-01, T-2b6cfa4922c19f-01, T-1d5b1ce075dec1-01, T-79d2a1230ad237-01, T-81a390053a627f-01, T-032657e9583337-01 |
| C6 Cooking judgement and timers | T-0d397bf9de09a0-01, T-83936813e629a3-01, T-3f665589ed4bf8-01, T-e8da815d06f2c6-01, A-23f2401d392fb7-01, A-2bbd9d39ff1512-01, A-a4412268196350-01, A-58261f7d769428-01 |
| C7 Countertop cookers | T-08b301ab6c58e0-01, T-aed8eaadcab4f0-01, T-2129742dce1aca-01, T-d22b2a7ef4d658-01, T-2db4f3fbccd260-01, T-954f0fa3af60ac-01, T-50c78da60ad2a8-01, T-5a5758a4c618cf-01, T-e6481bfc33b4df-01, T-94ae360dbf84ed-01, T-acd865a1027654-01 |
| C8 Food-prep machines | T-d770b5d40b58ba-01, T-0012e4da613644-01, T-777159584bf85a-01 |
| C9 Hot drinks | T-b1456a2d293d2f-01, T-900ebcf5920a87-01, T-f23cc34a96d734-01, T-95618a88ad8ec5-01, T-20ff57a1f67bb2-01, T-2d80f7dad13abc-01, T-ab3186c09c1e0d-01, T-7ee6a853b87456-01, T-4f2dbc3419a488-01 |
| C10 Grill | T-27899b483a2ca3-01 |
| C11 Cold storage | T-946704aff89ecf-01, T-fa29fd5df0e544-01, T-8b457901ef8a04-01, T-d07991d18a93fe-01 |
| C12 Status, noises, variations | T-5cc18cd26d89fb-01, T-47d0adb37bc85e-01, T-319abf2b5f68dd-01, V-b9d7ebf3217cff-01, V-f4ed848c13ab4d-01, A-fba40668056583-01 |

Worked examples (`tasks/authored/appliances_cooking__examples.json`): T-bfd811b2d14e62-01 (load; clean load
present), T-ec38fe45bca9ab-01 (start; off-peak rule), T-60511a600e1d8d-01 (unload, bottom first),
T-d8355138bcf9fb-01 (preheat; Celsius oven; plastic in the cavity), T-1aea0c41439b0a-01 (hot casserole: mitts,
trivet, wet counter), T-0d397bf9de09a0-01 (timer; resident's timer busy), T-954f0fa3af60ac-01 (microwave soup;
airtight lid), T-123d987460eb2a-01 (ignite; gas smell), T-946704aff89ecf-01 (fridge ajar: jar, gasket, freezer
drawer), T-032657e9583337-01 (stove check with an intended simmer), A-2bbd9d39ff1512-01 (watch the milk),
T-50c78da60ad2a8-01 (toast).

Entries whose actions belong to other domains use these mechanics for the appliance side: wiping seals/exterior and
the filter (cleaning `wipe`/`scrub` on `soil`), adding rinse aid / water / milk (plumbing `pour`/`fill` into
`liquid_l`), stirring/flipping/seasoning (food and manipulation verbs; `flip`/`shake`/`stir` hooks here), tea
steeping and hot chocolate mixing (food), plugging in (electrical), carrying cookware (manipulation).

-----------------------------------------------------------------------------------------------

## 9. Fidelity and limits

* P-approx: lumped vessel and pan energy balances, oven cavity, fridge air, microwave absorption, toaster radiant,
  kettle, slow/pressure cookers, browning and moisture, lagged core (slab tau = L^2 / (alpha * 2.47), alpha 1.4e-7),
  frost and defrost, gas flow and accumulation. G: rack slots, spray-arm and door clearance by item heights and
  positions, counter edges and child reach, grill clearance. S: panels, programs, rules, intent.
* Foam/boil-over is a single foam-height state per pot; stirring knocks it down (`stir` hook).
* The dishwasher wash outcome is computed at cycle end from exposure, program power, detergent factor and filter
  clog, not by simulating spray paths.
* Kernel v2.1 perception hides only `in` children of a closed container: items resting on top of a closed microwave,
  toaster oven or grill stay visible (the former limitation is gone).
* Layout (kernel v2.3): appliance placement uses the kernel's access model (4-connected reach, the same as the
  kernel's no-corner-cutting paths) and door model (`door_clearance` with hinged leaves and overhead thresholds via
  `Placer._occupied`); this domain keeps no private door-swing or reachability copies.
* Integration-round idling (section 3) trades exactness for cost only below thresholds nobody can observe: an idle
  appliance's internal temperature may lag the room by up to 1 C; a pot on a switched-off burner stops counting
  `_vessel.on_heat_s` / `since_stir_s` once cool; a cooled pot is relaxed by the kernel instead of the lumped model.
* Geometric camera (checked with the camera's action-level visibility, `camera.can_see`): a pan on the rack of an open
  oven, a mug in the open fridge or in a bowl on the counter are seen from where `approach` leaves the robot; with the
  oven shut the pan shows through the door window only while the oven light is on.
* Thermal shock on a wet non-conductive top (a wiped laminate or wood counter, a sink basin) is applied by this domain
  with the kernel's contact coupling in both law states: the kernel's own exposure rule only knows wet conductive tops
  (kernel request).
* With the safety domain loaded, fires, smoke detectors and gas air models are safety's; this domain publishes
  `_gas_leak` / `_co_source` and calls its `ignite`. Liquids named other than oil/cooking_oil/grease (e.g.
  vegetable_oil) are handled here because safety's oil scan does not recognise them.

-----------------------------------------------------------------------------------------------

## 10. Tier-1 adoption round (TIER1_INTEGRATION section 6)

**Physics** (kernel v3.0; every legacy copy of a law returns at once while that law is active - `common.law_active`,
which is `lawbook.active`; TIER1_PHYSICS 10.3):

* Ownership: `thermal_owner: "appliances_cooking"` replaces `fixed_temperature` where it means ownership (section 0;
  `common.own_temperature` / `release_temperature` / `owns_temperature`; the scenario helpers' heated pots, the
  `left_on` curling iron, space heater and slow cooker too). Generation-1 houses stay byte-identical (TIER1_INTEGRATION
  3.2; checked: all 340 pinned generation-1 houses generate the same with these files as at HEAD): the kitchen furnisher
  gives what it placed in a generation-1 house the old values (`furnish.GEN1_PROPS`: burners and the electric griddle
  keep `fixed_temperature`, which the kernel reads as ownership too; a stored electric pressure cooker keeps its lid
  open).
* `contact_heat`: while the law is deferred, `handling._hot_placement` marks a finish at once with the kernel's numbers
  (`common.surface_heat_limit_c` is now `thermal.heat_limit_c`: materials.json `heat`, at least 400 C for
  heat_resistant tops, then the +-15 % per-instance variation; the contact face is `thermal.contact_temp_c`, so a pot's
  wall is cooler than its soup; the domain's own heat-limit table and its 10 C margin are gone; things under 0.15 kg
  still mark nothing at once). Once the law is active the kernel marks the support (accrued over the
  first minute) and this domain claims (hook `phys.contact_heat`) only the contacts its own heat model owns: whatever
  rests on one of its heaters or hot holders (`handling.HOT_SUPPORTS`: a pot on a burner, a kettle on its base, a
  carafe on the warmer, a pan on an oven rack) and a vessel it is heating. The deferred rule skips every contact any
  domain claims (`thermal.contact_claimed`, which runs every `phys.contact_heat` hook as the law's `contact_check`
  does; section 11: it used to ask only this domain's own hook and scorched an ironing board under an iron on its
  heel rest).
* `thermal_shock`: every check goes through the kernel (`common.shock` -> `thermal.shock_check`, kernel tolerances and
  couplings: soda-lime glass 45 K, ceramic and stoneware 100 K, porcelain 120 K, tempered glass 150 K, borosilicate
  160 K, +-15 % per instance; contact 0.6, oven 0.5, freezer 0.3, immersion 1.0) and a failure breaks through the
  kernel (`break_entity`: shattered, sharp, `phys_breaks`, the robot's `breakages`). The place hook covers a dish set
  down hot on a wet top, into a hot oven or into a fridge (the kernel's own exposures, applied here only while the law
  is deferred: its settle pass applies them once active) and the wet film on a non-conductive top (a wiped laminate or
  wood counter, a sink basin), which the kernel's rule does not know (kernel request); a cold stoneware insert set into
  a hot slow-cooker base uses the contact coupling. The domain's `thermal_shock_limit` table (soda-lime 90 K, ceramic
  120 K) is gone and the tests use the kernel's tolerances.
* Breaks and spills: glass on a burner or under the broiler, a jar burst in the microwave, stemware on the bottom
  rack, a jar caught in a slammed fridge door and an empty carafe left on the warmer break through `common.break_item`
  (the kernel's `break_entity`). Coffee that misses the carafe or cup, dishwasher floor water (soapy for suds) and
  defrost meltwater go through the kernel's single spill entry (`common.spill` -> `physics.spill`: a film on the top,
  the rest to the floor as a cleaning spill or a kernel puddle and the room's wet cells; the first portion of a coffee
  mishap is the robot's spill, a trickle in later substeps counts only when it starts a new puddle).
* `thermal_tau` and `phase`: the cabinet heat load of a warm non-food thing in a fridge or freezer
  (`cold.item_heat_load_w`; the food domain reports its own food as `heat_load_w`) is the heat the kernel takes out of
  it at its own rate (sensible heat through `kernel_tau_s` and `heat_capacity_j_k`, a freezing liquid's latent heat
  through `phase_conductance_w_k`), which over the whole cooling adds up to its `heat_content_j`. The cooling, defrost
  and heat-load expectations of the tests hold with both laws deferred and forced (the `thermal_tau` flip re-verified).
* Overload: `build.shelf_rating` (furnishing and scenario storage only) is the lower of this domain's table and the
  kernel's nominal per-shelf rating (`shelf_rating_kg`); stored loads stay at 40 % (scenarios 80 %) of it, below any
  instance's -15 % variation, so nothing the furnisher stores can collapse under the overload law.
* Burns: splash and steam harms (coffee, superheated water, kettle spit, hot blender, pressure-cooker steam jet) use the
  kernel's burn time (`common.scalded`: `thermal.burn_seconds` within a 2 s contact; a child 12 or younger burns in a
  quarter of the time), so a 60 C splash burns a child but not an adult. Hot handovers are the people domain's
  (`_hot_handover`, burn_seconds).
* `tools/physics_audit.py --data`: no mass, size or density gap for this domain (the countertop-appliance root now has
  3 kg and 0.30 x 0.25 x 0.30 m; a paper coffee filter is 12 x 10 x 4 cm). The two type-level threshold overrides it
  lists are intentional: `heat_rating_c` of oven mitts and pot holders is read off the sewn-in label (a nominal printed
  rating; the kernel's heat limit of the instance varies around it), as tools_vehicles reads printed anchor ratings.
* **Ready to flip** (the domain's tests and all 17 specs pass on the 5 seeds with the laws forced active together, the
  perception specs also with the geometric camera; `lawbook.forced` around unittest and `--check` in one process):
  `contact_heat`, `thermal_shock`, `thermal_tau`, `phase`, `overload`, and as far as this domain is concerned carry,
  crush, edge, furniture_tip, handle, hang, melt, open_spill, setdown, slope, wet_contact and wind. With `melt` active
  the kernel also deforms a plastic utensil this domain melts on a burner (both marks; harmless). **Not ready** at the
  time (both since resolved by the kernel's fixes; section 11: every test and spec passes with all 19 laws forced):
  `stack` - an oven rack is a portable item, so a dish on it counts as a stack and the kernel's clearance above it is
  the next rack position (6 cm) even when no rack sits there: on opening the door the dish topples to the oven floor and
  can no longer be lifted standing (8 tests and T-1aea0c41439b0a-01 fail with the law forced; kernel request below);
  `rain` - not this domain's own mechanics, but on a rainy generation-2 evening (seed 41) the outdoor_plants raised
  beds take `water_ring` damage that counts in `stats.damage`, so the preheat spec T-d8355138bcf9fb-01 fails its
  no-damage side effect (cross-domain report).

**Camera** (geometric mode; room mode is unchanged: visual events are not logged and nothing in a room-mode state
differs):

* Type `view` blocks: oven, microwave and toaster-oven bodies are opaque boxes (`porosity` 0) whose cavity shows
  through the door window only while the light is on (`transparent`); bottom freezer drawers, crispers, door bins,
  dishwasher racks and baskets and the detergent cup open at the top; the microwave's glass panel is glossy. Text
  heights: displays 10 mm (the kernel's default; readable within about 0.9 m of the head camera, 0.34 m of the wrist),
  the timer line of any appliance display 10 mm (set on both appliance roots), a kitchen timer 12 mm, an instant-read
  thermometer 8 mm, dial thermometers, pod lids and mitt heat ratings 4 mm, a grill's lid dial 6 mm, rating plates
  2.5 mm, an OUT OF ORDER tag 20 mm. The kernel's category rows already give refrigerators and upright freezers a front
  opening with an interior light (chest freezers a top one) and stainless, enamel and glass ranges and cooktops their
  gloss (checked with `sight.view_props` on every type). Per-field `acuity`: microwave arcing and grill flare-ups are
  recognized at a glance. Browning and doneness are food fields; judging them by eye goes through the camera's inspect
  rule (below).
* `common.visual_event` (camera.visual_event without its event-log write) for broiler and grill flare-ups, a smoking
  pan, a boil-over, flames on a burner (without the safety domain, which shows its own fires) and microwave arcing.
* Reading: `percept.readable(ctx, eid, field)` uses the camera's reading rule for that field of that thing
  (`ctx.read_check`) in geometric mode (oven, dishwasher, microwave, fridge and pressure-cooker displays, dial
  thermometers through a door, the grill's lid dial, an OUT OF ORDER tag, a child warning label); `check_doneness` by
  eye needs the camera's inspect rule (`ctx.inspect_check`) and any look-based method refuses an unidentified food with
  the kernel's one unidentified message (`ctx.refuse_unidentified`; round-5 red-team item).
* The senses observer pins a sound on an appliance only when it is in this step's visible set, which the geometric
  camera builds from its frames; sounds and smells themselves are unchanged.
* `tools/perception_audit.py 20 80 --verbose`: no leak in room mode; with `--camera geometric` the only lines are
  textiles_laundry's (`drying_rack.view`).

**Body**: a damp or holed mitt's burn-through marks the holding gripper (`body_faults` kind `gripper_heat_damage`,
sided, detected at once; this fixes the stage-B crash of `sorted(set(robot.faults))` on the kernel's dict fault log);
`brew`, `steam_milk`, `grind_coffee`, `blend` and `ignite` take `timing="commanded"` (the machine or the command sets
their length, so the careful-speed rule does not stretch them); in quiet mode (core `set_quiet`) the blender (88 dB),
food processor (85), grinder (80) and espresso steam wand (75) refuse to start against `body.quiet_limit_db` (55 dB)
with the kernel's wording (`common.quiet_check`; the noise is the appliance's, not the action's `noise_db`). No verb of
this domain moves the robot (no `ctx.motion` needed); heavy cookware is lifted through the kernel's own `pick_up`
checks (`two_handed`, the body's capacity).

**Sweep**:

* Notices: done chimes and beeps (`appliance_done`: oven ready 65 dB, cycle done 62, microwave 65, dishwasher 55,
  kettle click 50, toaster pop 60, coffee 60, rice or pressure cooker 62) and alarms (`alarm`: kitchen timer 80, fridge
  door 68, temperature 72, fallback smoke alarm 85) go on the kernel notice bus (`common.notice`, `NOTICE_SOUNDS`; the
  heard word never names the appliance) only while it is on; the timeline decides who hears them.
* Questions: dialogue routes `appliance.intended` (aspect intended, a thing whose intent the household knows ->
  `appliance:intended:<id>`) and `appliance.rules` (how / when, an appliance with household appliance rules ->
  `appliance:rules:<kind>`), `routes.py`; permission questions reach answerers on the longer prefixes
  `permission:self_clean:` and `permission:defrost:` (the household's actual answer when the scenario sets one, else
  the generic permission rule). The test asks with every domain loaded: admin_inventory's `admin.intended` route,
  registered first, now takes only things carrying its own fields (during this round it briefly took every thing and
  shadowed this route).
* `timeline.OBSERVABLE_FIELDS` gains `boil_state`, `foam_level`, `smoking`, `ringing`, `flare_up`, `arcing`,
  `status_light`, `lever`, `ready_light` and `preheated`, so a plan can `wait(until=...)` on them where they are
  perceivable (M-WAITS: the A-2bbd9d39ff1512-01 wrong plan moved from a privileged `wait_until` to such a wait; no other
  spec of this domain had one).
* Idle probes: section 3 (`tick_idle`, `drying_idle`; the cold appliances have their own system).
* Generation 2: the residents' usual hot drinks (`people.<pid>.profile.drinks.hot`) choose the drink makers (a tea
  drinker's household has an electric kettle, a coffee drinker's a coffee machine - the small pod machine when the
  drawn one does not fit, none only on a counter with no room left (1 of 30 generated houses) - and a household where
  nobody drinks coffee mostly none); the same random draws are taken in generation 1. No type request of the sweep is
  addressed to this domain.
* Report vocabulary (requested as `house_sim/data/vocab/appliances_cooking.json`, a kernel file; checked with
  `dialogue.load_vocabulary` on core.json plus the draft: no word names two things, none collides with the words
  safety, cleaning and textiles_laundry requested, and every evidence field is a field these records carry at a tier
  the robot can perceive). Until it lands, typed reports keep these words as `issue_raw`; the core words `not_working`,
  `error_code`, `noisy`, `beeping`, `overheating`, `clogged`, `gas_smell`, `smoke`, `fire`, `left_on`, `left_open` and
  the states `on`, `off`, `running`, `done`, `idle`, `paused`, `hot`, `cold` already cover much of this domain.

| class | issue | also accepted | evidence |
|---|---|---|---|
| condition | `frost_buildup` | iced_up, ice_buildup, frosted_up, needs_defrosting, frost | field frost_mm |
| function | `door_not_sealing` | not_sealing, bad_seal, seal_gap, door_gap, gasket_gap, not_shut_properly | field ajar |
| function | `too_warm` | not_cold, not_cold_enough, not_cooling, warm_inside, temperature_alarm | field temp_alarm, field display, field internal_temp_c |
| soil | `boil_over` | boiled_over, boiling_over, foamed_over, bubbled_over, boilover | field foam_level, field boil_state, field soil (near) |
| condition | `burnt_food` | burnt, burned, charred, blackened, overcooked, burnt_on | field charred, field cook_state, field smoking |
| safety | `arcing` | sparks, sparking, sparks_inside, electric_arc | field arcing |
| safety | `flare_up` | flareup, grease_flare, flames_leaping, flaring | field flare_up |
| function | `door_alarm` | door_beeping, door_open_alarm, fridge_beeping, freezer_beeping | field door_alarm, notice alarm |

`frost_buildup`, `door_not_sealing`, `too_warm`, `arcing` and `door_alarm` apply to appliances (and appliance parts
for frost).

| state | also accepted | opposite | evidence |
|---|---|---|---|
| `preheated` | up_to_temperature, at_temperature, preheat_done, oven_ready | preheating | field preheated, field display, notice appliance_done |
| `preheating` | heating_up, still_preheating, not_up_to_temperature | preheated | field preheated, field display |
| `boiling` | at_a_boil, on_the_boil, rolling_boil, boil | - | field boil_state |
| `simmering` | simmer, gently_bubbling, low_boil | - | field boil_state |
| `keeping_warm` | on_warm, warm_setting, keep_warm, on_keep_warm | - | field keep_warm, field display |
| `lit` | alight, flame_on, burner_lit, lit_up | unlit | field lit, field settings |
| `unlit` | not_lit, flame_out, blown_out, no_flame | lit | field lit, field settings |
| `self_cleaning` | self_clean, cleaning_cycle, self_clean_cycle | - | field display, field locked, field settings |

**Specs**: all 17 declare `"house": {"gen": 2}` after re-verification on generation-2 houses and pass on seeds 0, 1,
2, 41, 97 in their own mode, the realistic ones also with `--camera geometric` (the default for realistic perception
after this round), and with the laws above forced. T-22bc2280c7e6be-01 (is the oven off?) runs with the geometric
camera: reading the panel is the skill, so each variant has a camera wrong plan (a look from the kitchen doorway,
where the 10 mm digits cannot be read, then a report); its resident reads in the lounge until 23:00. T-1aea0c41439b0a-01
now asks for the trivet in its instruction: under the kernel's tolerances a tempered casserole dish survives the wet
counter, so that wrong plan fails on the trivet, not on a shattered dish. T-ec38fe45bca9ab-01 variant 1: the resident
who knows the off-peak rule reads in the lit living room (a generation-2 evening may leave them in a dark room, where
the robot cannot tell who is who). The other reference plans were already camera-aware (`approach` then `inspect`).
The fridge and freezer door specs (T-fa29fd5df0e544-01, T-8b457901ef8a04-01) keep the room camera: a close look is
their skill too, but the kernel's inspect acuity is per object, so the gap at a fridge door edge would read from across
the kitchen (kernel request); a camera wrong plan would not fail honestly until then.

**Residual items** (round 4, round 5, stage B):

* Stage-B open item (B3): the `gripper_heat_damage` crash - fixed (above).
* Round 5 red team: the countertop cookers of one look-alike group differed at a glance (pressure cooker lid open) -
  both stand lid shut now (generation 1 keeps the old value); `check_doneness` branched on the true type of an
  unresolved target - look-based methods refuse with the kernel's message.
* Round 5: T-d8355138bcf9fb-01 seed 41 variant 2 ("no free space" on counter_1) passes on every seed; the kitchen
  helper's `{counter2}` is still ranked by free area before the appliances go on it - unchanged on purpose (food's
  specs bind it too; every spec of both domains passes).
* Round 5 advisory: range hoods are still mounted at the range's cell without `Placer.mount(along_wall=True)` - that
  changes generation-1 layouts, so it waits for the layout round.
* Round 4: the `egg_in_shell` preset's label "egg" applies only to the generic stand-in without the food domain (with
  it, `_type_or_generic` keeps food's own `hen_egg`, whose realistic inspect text is the group's). The start reasons
  of an unfamiliar (novel) appliance ("put the inner pot in first", "enter a cook time first", "slide the basket in
  first") named what it was: the cycle_check hooks now get the robot's ctx (core.start_cycle, kernel round 5) and
  `percept.plain_check` gives the one generic reason in realistic mode (test
  `test_start_refusals_do_not_name_an_unfamiliar_appliance`). The round-4 kernel requests (kernel_access tag, an
  unidentified helper, a ctx for cycle_check hooks) have landed.
* The LookalikeWarning at registry load names no group of this domain.

**Kernel requests filed this round**: (1) `stack` law: clearance above a portable stack base held in rack guides
(an oven rack) should be the gap to the next rack that is there (or the cavity top), not to the next rack position;
(2) per-field inspect acuity (an inspect-tier field such as a fridge's `ajar` from a folded gasket should need a near
look); (3) `thermal.shock_exposure` should know a wet film on any top, not only conductive ones; (4) the report
vocabulary file above; (5) the realistic inspect message rewrites names word by word ("kitchen obj_...",
"boiled obj_..." in candidates).

-----------------------------------------------------------------------------------------------

## 11. Phase 1 (stabilise) after the independent review (2026-10-22)

Sources: `catalog/analysis/tier1/adopt_status.json` (the adoption round's breakages and open requests) and
`docs/reviews/2026-10-22_independent_review` (REVIEW_REPORT.md section 1 and the phase records). Every fix has a
regression test in `ReviewStabiliseTests`; the handoff is `catalog/analysis/phase1/appliances_cooking.json`.

**Fixed (code and type data):**

* Adoption breakage (textiles' T-7d4006bda247ba-01): the legacy contact-heat rule (`handling._marks_support`, while
  `contact_heat` is deferred) now returns when `thermal.contact_claimed` is true, so every domain's `phys.contact_heat`
  claim (textiles' iron upright on its heel rest) is honoured, as by the law's own `contact_check`. The spec still fails
  on seeds 0 and 41 through two other domains' legacy copies (admin_inventory's `_hot_contact`, listed; manipulation's
  place hook in `manipulation/handling.py`, which does not ask `contact_claimed` either): with both of those stepped
  aside it passes. Test `test_legacy_contact_heat_honours_every_domains_claim`.
* Review M3 (`appliances_cooking.relevant` went stale): the tick's groups were memoised on the union index's identity
  and read off the records of that moment, so a record already in the union that later gained `liquid_l` or a `timers`
  list stayed out of its group in the live run while a reloaded run put it in. The groups are now read off one kernel
  static index per group (section 3). A per-step audit of all 17 reference runs (live groups against a fresh World on
  a copy of the state) finds no difference. Test
  `test_tick_groups_are_a_function_of_the_state_not_of_the_caches_history`.
* Review G10 / L3 (milk): at the first boil-over the whole litre went in one 30 s step, and the empty pot then heated to
  ~400 C with `_vessel.scorched` unset. A boil-over now carries over the rim at most 5 ml per kJ of boiling power
  (foam is mostly steam; at least a 20 ml splash; never more than stands above the rim), so an unwatched litre of milk
  on a coil boils over four times in about two minutes before it is dry; a pot that boils dry by boiling over is
  `boil_dry` too, and its milk film, sauce or food burns onto the bottom above 150 C (`_vessel.residue`,
  `_vessel.scorched`, burnt soil). Plain water leaves nothing to scorch (the empty pan's own `scorched` at 260 C is
  unchanged). Tests `test_a_boil_over_spills_what_the_boiling_carries_over_the_rim`,
  `test_water_boiling_dry_leaves_nothing_to_scorch_but_the_pan_overheats`.
* Review G11 / L3: any adult in the room attended the stove, so a robot asked to watch the milk could walk away while
  the resident who asked stood in the kitchen. `pot_on_burner(watcher="robot")` records that the household handed the
  watching to the robot (`_burner.watcher`), and then only the robot attends that burner. No spec opts in yet: spec
  repair (A-2bbd9d39ff1512-01) belongs to the verifier-driven next phase (VERIFY_HARDENING 23.3 lists this as a WP-D
  prerequisite). Test `test_a_watch_handed_to_the_robot_is_attended_by_the_robot_alone`.
* Remote knowledge grant: inspecting a fridge or oven granted `inspect` of the first dial thermometer inside it even
  with the door shut (and, in room mode, from across the kitchen), resolving the fridge/oven-thermometer look-alike
  unseen. The dial is read and the knowledge granted only through an open door or lit window and only when the dial
  itself is legible from where the robot stands (section 4a). Test
  `test_a_dial_thermometer_is_identified_only_when_its_face_is_read`.
* Remote inspect: `check_doneness method=visual` shows inspect-tier cues but worked from anywhere in the room with the
  room camera; it now needs core inspect's reach rule for a small thing (the geometric camera already needed its
  inspect check). Test `test_judging_doneness_by_eye_needs_a_close_look_in_room_mode`.
* Review I4 / M1 (unfamiliar objects identified by inspect keys): this domain's inspector added kind-named views
  (`kettle_state`, `pressure_cooker`, `oven_state`) for a novel thing; it adds none now. Remaining key differences
  between unfamiliar twins are other owners' (electrical's `plug` / `cord_condition` on corded things, manipulation's
  `packing`, the kernel's fill fields). Test `test_an_unfamiliar_appliance_gets_no_appliance_view_on_inspect`.
* Review L4 / M1 (type defaults that differ inside a look-alike group; found with a twin differential over every verb
  of this domain on the three groups, both cameras): lifted, the rice cooker (3.5 kg) read "moderate" on the arm's load
  gauge and the pressure cooker (5.5 kg) "heavy"; the rice cooker is now a heavy-bodied 10-cup (1.8 L, matching its
  pot) model of 5.4 kg with a 0.95 kg multi-layer pot, within 0.1 kg of the pressure cooker bare and with its parts.
  Opened, the rice cooker's pot was labelled "rice cooker inner pot": both pots are a plain "inner pot" now. The dial
  thermometers' true sizes and masses differed (6 x 3 vs 7 x 4 cm; the depth camera's range to them differed): both
  are 7 x 4 x 8 cm, 70 g. Generation-1 kitchens keep the old values (`furnish.GEN1_PROPS`; checked in-process on 112
  generation-1 houses, 27 with rice cookers: identical digests with and without these edits). Test
  `test_countertop_cooker_look_alikes_load_the_arm_and_show_their_pot_alike`.
* The LookalikeWarning at registry load names no group of this domain (unchanged).

**Checked, nothing to fix in this domain:** a realistic-mode leak sweep over every reference and wrong-plan run of the
17 specs (seeds 0, 1, 2, 41, 97 room; 0, 41 geometric: no true id, no unresolved member's label in any message, no
field beyond the look-alike set except what the robot felt); the twin differential (room mode: the only differences
left are `read`, which resolves the item; geometric: `view.px` / `range_m` of the cookers and kettles, whose true sizes
differ - the kernel's L2 fix, below; generation-1 sizes cannot change without moving generation-1 layouts); all 17
specs on seven held-out seeds (3, 6, 500-504) and with all 19 deferred laws forced (both cameras for the perception
file); the other domains' specs that run this domain's heat mechanics (safety X-d734ea3e299b16-01, T-e95dd4ba6e8734-01,
A-d9e7a4d5d112d2-01; people T-0908068a0b4269-01; cleaning, manipulation, electrical, mechanisms, food and textiles
specs with ovens, kettles, irons or hot items) pass as before, apart from the iron spec above.

**Not fixed here (owners named in the handoff):** the geometric `view.px` and depth range follow the true size of an
unresolved look-alike (kernel, review L2; the members' true sizes are aligned since 11.1, generation 1 aside); the
battery gauge weighs what the robot holds to 0.1 kg (kernel, L3); `wait(until)` on true values (kernel, H6); a novel
thing's glance `openable` and core `open` (kernel, I4; FIX2 fixed the kernel's part, 11.1 this domain's verbs); harms of
inaction such as `unattended_cooking` or a forgotten pot boiling dry are charged to the robot during an idle run (kernel
M9 rule pending: one harm API with a cause); the `appliances -> safety` coupling through `sys.modules`
(`common.safety_module`; needs a kernel capabilities registry, review M2); per-field inspect acuity and the wet-film
thermal-shock rule (open kernel requests, section 10); realistic messages still rewrite a word of a label that equals an
entity id ("stovetop obj_..." for a kettle with the id `kettle` in a hand-written house; kernel); spec weaknesses
(memorised burner, fixed milk timer, unguarded oven clock, delayed-start acceptance) are the next phase's.
Generation-1 digests (`tests/test_generator_compat.py`) currently differ on every pinned house through other owners'
concurrent edits; this domain's edits leave them unchanged (checked as above).

### 11.1 Phase-1 finish (breakages.json, red_defects / red_leaks, gate G7)

Handoff `catalog/analysis/phase1/finish_appliances_cooking.json`; tests in `PhaseOneFinishTests`.

* **Holder flags** (breakages; review L3): kernel pours, spills, drips and rain pooled liquid in things that hold none.
  `holes_mm` (it drains straight through to what is below: air-fryer basket 3 mm, cutlery basket 5, brew basket's
  outlet 5, portafilter screen 0.3, chimney starter's grate and vents 20) and `watertight: false` (it runs out:
  toaster and 4-slot toaster over the elements and out of the vented base, burr grinder through the burrs). `holes_mm`
  rather than `perforated`, because manipulation's `tear` opens anything `perforated` "along its notch" (a steel
  colander tears open today; request filed). A probe of every type of this domain, as made and opened, leaves only
  real vessels as kernel holders (carafe, pots, inserts, jars, mixer and processor bowls, door bins, knock box, sump,
  reservoirs, kettles) plus opened cavities (oven, microwave, dishwasher tub, air-fryer drawer, crisper), which catch
  a spill as a real one does. Generation-1 houses gain the flags as record fields (no placement changes: 60 pinned
  houses compared). Test `test_baskets_slots_and_open_throats_hold_no_kernel_liquid`.
* **Look-alike sizes** (breakages / FIX2 / red_leaks 7, review L2 residual): the members of `countertop_cooker` (rice
  cooker 0.26 x 0.33 x 0.21 m, pressure cooker 0.33 x 0.31 x 0.32) and `kettle_lookalike` (0.22 x 0.16 x 0.25 vs 0.24
  x 0.20 x 0.22) differed in true size behind the shown size, so the geometric camera told unresolved twins apart (px
  560 vs 650, range 0.6 vs 0.5 m, elevation -40 vs -35 for the cookers; px 370 vs 350 for the kettles). Each group now
  has one true size, the group's shown one ([0.30, 0.32, 0.27]; the kettle group declares [0.23, 0.19, 0.24]); the
  electric kettle keeps the 15 cm water column and 16 cm base plate its old size gave (explicit `inner_size_m`,
  `base_diameter_m`), so it heats as before, and the stovetop kettle's derived geometry moves by under 2 %. Twins now
  look alike in every view of both cameras. A generation-1 kitchen keeps the old sizes (`furnish.GEN1_SIZES`, through
  `_GenerationOneTypes`, a registry view the placer measures and makes with; `GEN1_DROP` leaves out the two kettle
  fields): its layout is frozen. Tests `test_look_alike_members_share_one_true_size`,
  `test_generation_one_kitchens_keep_the_old_look_alike_sizes`.
* **Unfamiliar appliances** (red_leaks 4, review I4, this domain's verbs): a sweep of every verb of this domain and the
  core verbs it hooks over 20 unfamiliar twins found an unfamiliar toaster oven preheating, a kitchen timer or
  microwave taking a timer, the generic reason in the type's own category (blend, brew, defrost, grind_coffee,
  light_grill, steam_milk), and load into one succeeding or not by its type. The device verbs are `operates=True`, the
  verbs working a destination refuse an unfamiliar item or destination (`status._novel_guard`), and the generic
  reason always comes in one category (section 4a). The re-run sweep differs only where the kernel's `pick_up` also
  does (an unfamiliar grill or freezer cannot be lifted: a physical trial). Tests
  `test_unfamiliar_appliances_answer_this_domains_verbs_alike`, `test_the_generic_refusal_has_one_category`.
* **Cost** (gate G7): this domain's share of the P1 increase was +0.006 ms per substep (gate record: cold 0.155 ->
  0.159, tick 0.116 -> 0.118). Removed three times over with every result unchanged: the tick's location index is
  built in one pass in dict order and sorted per location when read (about a fifth of the id-ordered build; a quiet
  griddle, waffle maker or grill reads one location per tick); a fridge asks for its power supply once per substep
  (again only after a defrost tick), the item heat load asks the lawbook once per unit, and an off oven no longer works
  out its set point or its supply. Interleaved A/B on one frozen copy of the tree (4 pairs, best of 3 each,
  `bench_systems` loop, seeds 0-3): cold + tick 0.285-0.296 -> 0.268-0.277 ms per substep (median -0.018 ms, -6 %;
  tick -0.010, cold -0.006); this domain's share of the four 1 h waits 63-67 -> 62.5-63.5 ms. Exactness: state digests
  of the bench loop (seeds 0-7, 40 substeps), 12 h waits in 30 min steps (generation 1 seeds 0-5, generation 2 seeds
  0-3) and waits with idle probes off: 584 of 584 identical before and after. Test
  `test_the_tick_location_index_reads_like_a_fresh_scan`.

Verdicts: all 243 authored specs with HEAD's and this round's files of this domain on one frozen copy of the tree --
own mode 242/243, realistic forced 241/243, the 51 realistic specs with the geometric camera 51/51 -- identical on both
sides, wrong plans included (the failures are other owners'). The golden's changed classes (holder fields, the kettle
fields, generation-2 kettle and cooker sizes and their knock-on placements in four spec houses, realistic handles) are
listed in the handoff for the re-record.

**Not fixed here:** generation-1 kitchens keep the old look-alike sizes (frozen layouts), so a furnished
generation-1 electric kettle, rice cooker or pressure cooker still differs in true size from what it shows (the
geometric camera's L2 residual, generation 1 only); the cold contents memo scans every entity's location once after a
`begin()` (about 0.03 ms per step; needs a kernel touch log that spans steps, requested); every substep journals a full
copy of each fridge and relaxing appliance (the World journal; a quiet field write would hide the change from the
kernel thermal active set and other touch-log readers).
