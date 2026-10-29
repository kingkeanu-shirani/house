# Climate domain

Room air (temperature, humidity, CO2, particles, pollen, odour) and everything that changes it: thermostats
(modes, setpoints, holds, 5-2 schedules, units, household limits), HVAC equipment (gas furnace + AC, heat pump
with aux/emergency strips, boiler + radiators, ductless mini-splits), supply registers and return grilles, HVAC and
purifier filters, ceiling and portable fans, space heaters, humidifiers, dehumidifiers, air purifiers, ventilation
through windows and doors, weather exposure (rain driven through open windows, cold glass, frozen pipes), the sun
(position, solar gain, direct-beam glare, outdoor shade), occupant thermal comfort and the household energy ledger.

Code: `house_sim/systems/climate/` (package). Types: `house_sim/data/types/climate.json` (41 types).
Tests: `tests/test_climate.py` (112 tests: 14 realistic-perception tests, 16 for the tier-1 adoption in `Tier1PhysicsAdoptionTests`,
`Tier1CameraAdoptionTests`, `Tier1BodyAndSweepAdoptionTests`, `Tier1ResidualTests`, 10 for phase 1 in `Phase1StabiliseTests`
and 4 for its finish in `Phase1FinishTests` (section 18); green isolated
(`HOUSE_SIM_DOMAINS=core,climate`), with `HOUSE_SIM_STRICT=1`, with all 16 domains loaded, and with the `furniture_tip` and
`carry` laws forced on).
Examples: `tasks/authored/climate__examples.json` (12 specs on generation-2 houses, `"house": {"gen": 2}`; all pass `--check` on
seeds 0, 1, 2, 41, 97 with every domain loaded).
Perception-centric specs: `tasks/authored/climate__perception.json` (2 specs, `"perception": "realistic"`, `"camera": "geometric"`,
generation 2; pass `--check` on the same seeds including the verifier's perceivability check, each with a camera wrong plan).
What the robot can perceive: section 4a (kernel v2.4-v2.8) and section 17 (the geometric camera). Tier-1 adoption (kernel
v3.0 physics, camera and robot body; v3.1 sweep): section 17. Who caused a harm (only the robot's harms fail a task):
section 5 and section 18.

| Module | Contents |
|---|---|
| `common.py` | constants (cited), psychrometrics (Magnus), unit conversion, the `climate` state section, hazard/tracker/log helpers, harm attribution helpers (`robot_acting`, `robot_arranged`, `robot_enabled`, `robot_touched`, `charge`, `hazard(robot=)`), window opening/screen/facing helpers |
| `attribution.py` | the robot's operation markers on climate devices, windows and outside doors (`_cl_robot`, after-hooks on the operating verbs) and the cause sets of the time-driven harms (`robot_room(side)`, `robot_hvac`; section 5) |
| `sun.py` | sun position, irradiance, window solar gain, direct-beam ray tests (glare, sun patches), outdoor shade casters, fallback window shades; the geometric camera's `light_at`, `sun_beam` and `bright_sources` hooks |
| `hvac.py` | thermostat semantics (schedule, holds, units, deadband, limits, battery), equipment state machine (lockouts, short cycling, heat-pump COP/aux, condenser), filter state |
| `fans.py` | ceiling fans (chains, remote, gate switch, rpm, direction), portable fans (cone airflow, window fans), cords, light-object/flame disturbance, `reverse_fan` |
| `heaters.py` | space-heater clearance, ignition, supervision, outlet rules, tip-over switch |
| `moisture.py` | humidifiers, dehumidifiers, tanks/buckets/hose, water quality, mineral dust, spills, `refill_reservoir`, `empty_reservoir` |
| `airclean.py` | filter fit geometry, airflow arrow / pull tab, shrink wrap, layer order, purifier CADR and indicator |
| `comfort.py` | occupant thermal feel and state, the `climate.` answerer |
| `routes.py` | canonical questions (`ask` aspect / about / detail) routed to the `climate.` answerer (kernel v3.1, section 17) |
| `model.py` | systems `climate.house` (order 44, every substep: scan, HVAC delivery, devices, room air balance, effects, own-device energy), `climate.meter` (order 45, period 120 s: other domains' metered devices; exact idle probe), `climate.slow` (order 46, period 300 s: tank water ageing; exact idle probe) |
| `perception.py` | observer `climate.senses`, inspector `climate.details` (both realism-aware, section 4a), six invariants |
| `furnish.py`, `scenarios.py`, `skills.py` | furnishers, 19 scenario helpers, 14 reference skills |

All iteration is over sorted ids; randomness only through the setup rng (helpers/furnishers). No system walks every
entity: they read kernel static indexes (`w.static_index`, via `common.static_ids`: windows, climate devices, heat-gain
candidates, metered devices, screens, shade-watched items, path-blocking furniture/rugs, combustibles, candles/papers,
mist-sensitive electronics, floors, window covers) and re-check dynamic fields on the records. Every index predicate
declares the record fields it reads (`common.index_reads`; the World then evaluates it on the plain record, strict mode
checks the declaration), and the eight looked up on every substep (`common.PER_STEP`: windows, climate devices, heat
gains, hot showers, metered devices, screens, shade-watched items, floor cover) are built by an `episode_start` hook
(order -90) while nothing is journaled, not inside the first substep through the kernel's read views (phase-1 G7;
section 18). Groupings that only
actions change (windows per room, devices by kind, entity -> room, glazing UA, light lists, screen glare points,
register floor cover, dehumidifier intake clearance) are memoized per env step (`model.step_memo`: keyed on
`steps`, the world version and the entity count, so actions, rollbacks and created/destroyed entities refresh it).
Hot helpers have fast paths (`common.num` for plain floats, window apertures memoized per step, one diffuse-sky value
per substep, open-window areas shared between the air and effects passes).
`tools/bench_systems.py 40 0 1 2 3 --domain climate`: 1.73 ms per 30 s substep before the integration round, ~0.67 ms
after (climate.house ~0.65 + climate.meter 0.02 amortized + climate.slow < 0.01).

-----------------------------------------------------------------------------------------------

## 0. The air model (read this first)

Every indoor room is one well-mixed node. Each substep the system integrates, exactly for that substep
(`x -> a/b + (x - a/b) e^(-b dt / C)`), a heat balance, a moisture balance and pollutant balances:

| Term | Model / magnitude |
|---|---|
| Capacitance | 55 kJ/K per m2 floor (air + furnishings + interior surfaces; whole-house time constant ~10 h) |
| Envelope | UA = 0.25-0.9 W/K per m2 floor by room type (+0.35 under the roof); garage 2.5, attic 4.0, basement 0.2 + 1.2 W/K/m2 to 12 C ground; windows U 2.8 W/m2K (`u_value` 5.8 = single pane) |
| Infiltration | 0.3 ACH to outdoors |
| Interior doorways | 0.03 m3/s mixing + two-way buoyancy flow Cd W H/3 sqrt(g H dT/T) (= 0.088 sqrt(dT) for 0.85 x 2 m); closed door 0.002 m3/s; stairs 0.05 + buoyancy |
| Open windows | single-sided q = A (0.025 v_wind + 0.04 sqrt(dT)); cross ventilation when two open windows on facades >= 90 deg apart are in one open-door zone: q = 0.61 A_eff v 0.6, A_eff = (A1^-2 + A2^-2)^-0.5 (inlet/outlet rooms get q, other zone rooms 0.2 q); window fans force their flow |
| Gains | people 75 W (45 asleep), pets, lights 12 W, appliances (`power_w` when active, by category), ovens 25 %, solar through windows (SHGC 0.6 x beam x cos incidence + diffuse, interior coverings remove 55 % x covered), attic/shed roof gain, HVAC / radiators / mini-splits / heaters / dehumidifier latent heat |
| Moisture | vapour density g/m3 with 3x hygric buffering; people 55 g/h; hot showers (plumbing `water_running` >= 35 C) 0.6 g/s; humidifiers; dehumidifiers; AC coil (removes vapour above 9.4 g/m3, bypass 0.6); outdoor air at `weather.humidity`; saturation cap |
| CO2 / PM2.5 / pollen / odour | people 0.0052 L/s CO2 (0.004 asleep), outdoor 420 ppm; PM from outdoor AQI (EPA breakpoints), deposition 0.2/h, HVAC filter by MERV, purifier CADR; pollen index from `weather.pollen` (screens do not stop it, HEPA does); odour decays with fresh air and carbon filters; scenario sources `pm_source_ug_s`, `odor_source_per_h` on a room |
| Stratification | heated rooms build a warm ceiling layer (1 C + 0.8 C per m above 2.4 m); any running ceiling fan mixes it to 0.2 C; occupants feel T - 0.3 strat |

Room fields written each substep (observable on the room): `temperature_c`, `humidity` (RH 0..1), `co2_ppm`, `pm25`,
`pollen`, `odor`, `stratification_c`, `air_change_rate_ach` (outdoor air now), `fresh_air_changes` (cumulative outdoor air
volumes since the episode began), `dew_point_c`, `ventilating`, and `light_level` for rooms with glare targets or listed
in `climate.watch_rooms`. Outdoor rooms use `weather.temperature_c` (kernel). Optional diurnal outdoor cycle:
`climate.outdoor.diurnal = {min_c, max_c}` (min ~06:00, max ~15:00).

House-level bookkeeping lives in the hidden top-level section `climate`: `energy` {kwh, gas_kwh, cost_usd, hvac_kwh,
peak_kwh, by_device}, `tariff` {peak_from, peak_to, peak_usd_kwh 0.42, offpeak_usd_kwh 0.14, gas_usd_kwh 0.045},
`hazards` (exposure seconds/counts read by the invariants), `trackers` (task-facing counters, below), `log`, `sun`
{azimuth_deg, altitude_deg, dni_w_m2}, `watch_rooms`.

| Tracker (`climate.trackers.<name>`) | Meaning |
|---|---|
| `heat_run_s:<hvac>`, `cool_run_s:<hvac>`, `aux_run_s:<hvac>`, `fan_run_s:<hvac>` | seconds the equipment ran in that stage |
| `hvac_with_open_windows_s` | seconds any HVAC/mini-split heated or cooled while a conditioned room had an open window |
| `open_dry_s:<window>` / `open_in_rain_s:<window>` / `open_in_rain_s` | seconds a window was open while dry / while raining |
| `max_c:<room>` / `min_c:<room>` | extremes for rooms in `climate.watch_rooms` (set by `climate.baseline`) |
| `condensate_to_drain_l`, `condensate_on_floor_l:<hose>` | dehumidifier hose discharge |
| `schedule_edits` | count per schedule period edited |

-----------------------------------------------------------------------------------------------

## 1. Object types (41) and realistic magnitudes

Every type carries `climate_kind` (dispatch key). Other domains' objects used in climate scenarios (sinks, outlets,
TVs, candles, papers, remotes, blinds/curtains, chairs) are built from core types with explicit props.

| Type (is_a) | Key fields |
|---|---|
| `thermostat` (fixture), `smart_thermostat` (app controls, `remote_status`), `basic_thermostat` (no schedule) | 0.12 m at 1.5 m; settings `mode` off/heat/cool/auto(/emergency_heat on heat pumps), `fan` auto/on/circulate, `heat_setpoint`, `cool_setpoint` (display `units` F or C; limits F 45-90/50-92, C 7-32/10-33), `hold` none/temporary/permanent, 8 schedule periods `weekday_wake/leave/return/sleep`, `weekend_*` = {time "HH:MM", heat, cool}; derived `call` (idle/heat/cool/aux/fan), `display` (readable), `reading_c`, `active_period`, `hold_until` (ISO) / `hold_until_s`; `controls_hvac`, `zone_rooms`, `power_source` c_wire/battery (+ AA slots `cell_1/2`) |
| `hvac_system` -> `furnace` (17.6 kW gas, AFUE 0.95, 10.5 kW AC, 450 W blower, 0.55 m3/s, 16x25x1 slot), `heat_pump_air_handler` (10.5 kW, 10 kW aux strips, 20x20x1), `boiler` (24 kW, pump 80 W, radiators) | `on` = service switch (switch_off before the filter door), `cover_open` filter door, `slots.filter` {accepts hvac_filter, fit {nominal, airflow}, power_off_first}; derived `stage`, `running`, `blower`, `fault` (coil_frozen, low_pressure), `airflow_ratio`, `static_pressure`, `closed_supply_frac`, `duct_dust`, `starts`, `strain_s`, `limit_trips`, `power_w`; `condenser` |
| `outdoor_condenser` | 70 kg outdoor unit; covering it while it runs -> capacity x0.3, compressor overheats after 10 min (damage) |
| `supply_register` (floor 10x4 in), `ceiling_register` (2.4 m), `wall_register` | `settings.damper` 0..1, `design_flow_m3s` 0.05, `free_area_m2`; derived `airflow_mps`, `blowing`, `blocked`, `vent_issues` (sorted: closed / partly_closed / blocked / dusty / damaged), `report_matches`; may be hidden `under`/`behind` furniture with `floor_cell` |
| `return_grille`, `filter_grille` (20x25x1 behind the face) | never closed; blocked returns are a hazard |
| `radiator` | 1.2 kW nominal, TRV 0-5 -> 7/12/16/20/24/28 C, `_air_frac` (trapped air, tools_vehicles bleeds it), derived `heating_on`, `temperature_c` (fixed by this model, ~65 C firing), `heat_output_frac`, `output_w` |
| `mini_split` | 3.5 kW cool / 4 kW heat inverter head at 2.1 m; settings mode (off/cool/heat/dry/fan_only/auto), setpoint, fan, units; all `remote` controls (IR remote: line of sight with electrical) |
| `ceiling_fan`, `remote_ceiling_fan` | 52 in, housing 2.3 m; `on` = wall-switch power gate; settings `speed` off/high/medium/low (chain order), `direction` ccw/cw, `light` off/on; `control_kinds` pull_chain + slide_switch or remote; `chains` {speed, light} -> pull-chain entities at 1.85 m (mechanisms' `press button=pull`); derived `rpm` (190/140/80; 15 s spin-up, 25 s coast), `spinning`, `running`, `light_lit`, `power_w` (65/32/15 W + 15 W light), `motor_damaged`, `wobble` |
| `portable_fan` -> `box_fan`, `pedestal_fan`, `tower_fan`, `window_fan` | `on`, settings speed low/medium/high, oscillate, `aim_deg` (0 = -y north, 90 = +x east); v0 1.2-3.5 m/s, cone 15-25 deg; `cord_length_m` 1.8; derived `running`, `power_w`, `cord_crosses_walkway`, `cord_under_rug`, `placement_issues` |
| `space_heater` -> `ceramic_heater` (1500/750 W, grille 85 C), `oil_filled_heater` (12 kg, 70 C) | settings heat low/high, thermostat dial 1-6, aim_deg; `wall_outlet_only`, `_tip_switch_ok` (hidden), `risk` 2; derived `running`, `temperature_c`, `hazards` (+hot), `unattended_s` |
| `humidifier` -> `ultrasonic_humidifier` (0.35/0.18 L/h, label: distilled only), `evaporative_humidifier` (wick, tap OK, self-limiting), `warm_mist_humidifier` (300 W steam) | settings output, target_rh 30-70; `slots.tank`, base tray 0.3 L; derived `running`, `humidistat_on`, `empty_light`, `base_water_l`, `mineral_out_g`, `wick_scale` |
| `humidifier_tank` | 4 L, MIN 0.4 / MAX 3.8 L lines, `open` = cap off, `liquid` (water / distilled_water / filtered_water), `mineral_ppm`, `water_age_h`, `contamination.bacteria` |
| `dehumidifier` | 70-pint (33 L/day AHAM), 550 W, settings target_rh 30-80, fan, drain bucket/hose; `slots.bucket`, `hose`; derived `compressor_on`, `full_light`, `display`, `intake_blocked` |
| `dehumidifier_bucket` (6.5 L, float at 6.0 L, condensate), `drain_hose` (2 m hose end; `tethered_to`) | |
| `air_purifier` (AP-300 3-in-1 cartridge), `tower_air_purifier` (AP-500 layers prefilter -> carbon -> HEPA) | settings mode sleep/low/medium/high/auto, filter_indicator ok/replace; `filter_hours`, `filter_life_h` 4380, `cover_open` interlock, `layer_order`, slots with `fit_size_m`; derived `cadr_m3h` (60-340), `layer_order_ok` |
| `hvac_filter` | `filter_spec` {nominal, merv, arrow}, `size_m` = nominal - 1/2 in, `shrink_wrapped`, `dirtiness`, `waste_stream` trash; derived on install `reversed`, `bypass_gap_mm`, `installed_at` |
| `purifier_filter` (combo) -> `purifier_prefilter` (washable, reusable, `keep`: binning it is a loss, not a refusal), `purifier_hepa_filter`, `purifier_carbon_filter` | `filter_model`, `filter_layer`, `facing` tab_out/tab_in, `shrink_wrapped`, `dirtiness` |
| `hygrometer` | `display` "45% RH 21.5 C", the same digits as `rh_pct` / `temp_display` (read-tier numbers for realistic perception) |

Windows (core) gain `facing_deg` (furnisher, from their wall), `condensation`, `rain_in_l`, `water_damage`, optional
`child_stop` / `child_stop_frac` (0.1) / `u_value`; mechanisms' `mech.open_frac`, `mech.screen`, `mech.clear_opening_m2`
are honoured when present. Fallback window shades for climate-only scenarios are `generic_item`s with
`covers_window`, `openable`, `settings.lowered` 0..1 and `opacity` (they set the window's `covered`).

-----------------------------------------------------------------------------------------------

## 2. Actions

This domain owns three verbs; everything else uses core verbs (and mechanisms'/electrical's when loaded).

| Verb | Params | Preconditions (refusal: category) | Effects | Duration |
|---|---|---|---|---|
| `reverse_fan` | target, via switch\|remote | ceiling fan only (argument); remote held + working + fan running or remote-direction fan (precondition/resource); switch: reach to the 2.3 m housing (capability: stool/ladder), blades >= 60 rpm -> refused (safety) | flips `settings.direction`; 5-60 rpm via switch -> `motor_damaged` strained, `wobble`, damage | 4 s (+ up to 30 s spin-down via remote) |
| `refill_reservoir` | target (tank or top-fill unit), source (sink/faucet in reach or held jug), level max\|min\|half, liters | an unidentified look-alike target: the kernel's generic "identify it first" refusal (realistic); tank held or standing in a sink (precondition), cap off (precondition), water available (resource), jug holds water (precondition) | fills to the MAX line (or liters); overflow beyond capacity: into the sink at a sink, else down the tank's side through the kernel (`phys.liquids.deposit_below`: the floor at the robot's feet for a held tank, a `phys.overflow` spill); mixes minerals; a top-up of old water keeps its age/bacteria; fresh fill resets them | 5 s + volume / flow (6 L/min tap, 0.15 L/s jug) |
| `empty_reservoir` | target (held tank/bucket), destination (sink/toilet/tub/shower/drain in reach, or omitted outdoors) | an unidentified look-alike: the generic refusal (realistic); held, cap off, destination is a drain and not clogged (precondition) | liquid -> 0, age reset, bacteria x0.3 | 6 s + 2 s/L |

Hooks on core verbs:

| Verb | Hook |
|---|---|
| `set` | validates thermostat schedule periods (HH:MM, limits, unique times), emergency heat only on heat pumps, no cool mode without cooling; ceiling-fan direction only through `reverse_fan` unless remote-controlled; setting_changed: setpoint while following the schedule -> temporary hold until the next period, hold none -> resume schedule (setpoints re-applied), units -> every value converted; outside household limits (`household.thermostat_limits` or rules kind `thermostat_range`) without approval `set:<id>` -> authority_violations; purifier filter_indicator ok -> hour counter zeroed; shade `lowered` -> window covered |
| `switch_on` | space heater refuses when not upright / on a soft surface (tip-over switch); purifier refuses with the filter door open (interlock); fans/heaters refresh state and cords |
| `open` / `close` | core `open` on a shrink-wrapped filter tears the wrap off; fallback shades sync window `covered` |
| `install` / `uninstall` | filter geometry: face or thickness larger than the slot -> refused (precondition); smaller -> `bypass_gap_mm`; arrow vs slot airflow -> `reversed` (kernel v2.1 `orientation`: upside_down / reversed / flipped turns the filter over, toward_blower / away_from_blower or tab_out / tab_in holds it that way round; it ends `upright` in the slot); purifier layer order; tank installed uncapped -> the base tray fills to its 0.3 L and the rest is spilled through the kernel onto the surface below (`ctx.spill`: the support catches a film, the rest runs to a floor puddle; spills); climate adds water damage on wood/electronics and electrical_hazard with an outlet nearby; bucket/tank seating flags. Core `power_off_first`/`access` make the service switch and filter door mandatory |
| `place` | `orientation=upside_down` turns a filter over (arrow flips / pull tab flips); a dirty filter set down outside a bin/bag sheds dust there; corded climate devices: cord must reach the outlet (refused otherwise), walkway/rug crossing -> trip floor hazard; furniture placed on a `crushable` ground item crushes it (damage) |
| `pick_up` | lifting a rug/furniture that hides a register leaves the register uncovered on the floor; corded device cords cleared; spinning ceiling fans cannot be touched (safety) |
| `move`/`approach`/`walk_to` | carrying an uncapped full tank or a brim-full bucket slops water: the kernel's slosh model and spill (section 17); this hook applies them while the kernel's `carry` law is deferred and no other domain's carry model (plumbing's, manipulation's) ran for the trip |
| `wipe`/`dust`/`clean`/`hold_steady` | refused on a ceiling fan whose blades turn, or a running portable fan (safety) |

-----------------------------------------------------------------------------------------------

## 3. Systems

`climate.house` (order 44) runs every <= 30 s substep (thermostat hysteresis, compressor lockouts and blower cycling
need it); `climate.meter` (order 45, `period` 120 s) and `climate.slow` (order 46, `period` 300 s) receive the
accumulated dt (kernel v2.1 periodic systems).

1. Optional diurnal outdoor temperature; sun position + DNI into `climate.sun`.
2. Scan (static indexes + per-step memo, see the header): windows per room, climate devices by kind, device heat gains, hot showers, metered devices, glare targets, shade-watched items, candles; register floor cover (furniture/rug footprints) only for rooms with floor registers.
3. Thermostats: battery check, hold expiry / schedule period, reading (+2 C x direct sun on the thermostat), call with +/-0.5 C hysteresis, display.
4. Equipment: stage with 300 s compressor min-off / 180 s min-on, starts logged (> 4/h = short cycling), heat-pump COP 2.8 + 0.1 T_out and capacity 0.7 + 0.02 T_out, aux strips below -12 C or 15 min behind, emergency heat = strips only, boiler low-pressure lockout, gas availability, covered condenser.
5. Delivery: register flows (damper x 0.25 if blocked x 0.4 if the room is closed without a return), total flow ~ sqrt(open share) x filter factor; coil freezes after 20 min below 55 % flow in cooling (damage, cooling stops), furnace high-limit trips below 60 % (output halves, damage after 5); 3 % casing loss to the equipment room; returns mix their rooms; filter loading (~90 days to clogged); radiators with TRV taper and trapped air; mini-splits (inverter modulation, dry mode).
6. Devices: fan rpm/power, window fans, heaters (safety), humidifiers (water use, quality, placement), dehumidifiers (bucket/hose, intake), purifiers (CADR, loading, indicator), hygrometers.
7. Air balance per room (section 0).
8. Effects: window condensation (glass inner surface T_in - (T_in - T_out) U/8 below the dew point; fills in ~15 min, clears in ~30), rain ingress through open windows (rain mm/h x area x (0.03 + 0.5 windward x wind/5)): a wet sill (`rain_in_l` > 0.02 L, inspect shows it) until 0.25 L (`RAIN_PUDDLE_L`), then a floor puddle (room wet cell + `spills`), damage at 0.5 L on carpet/wood/laminate; pets at open unscreened windows (escape after 60 s if the pets domain is absent); upper-floor openings > 10 cm with a child home; HVAC with windows open; pipes below 3 C for 2 h burst (damage + flood); pets above their species' heat limit (`common.PET_HEAT_C`: dog 32, cat 33, rabbit / guinea pig 29, else 30 C); occupant comfort; glare (`screen_glare`, `screen_glare_window` on TVs/monitors/`glare_watch` items); room light levels; outdoor shade watch (`shade_frac`, `shade_min`, `shade_samples` over `shade_watch` {from_s, to_s}); fan disturbance of papers/flames/unstable fans.
9. Energy ledger (this system): this domain's devices (`power_w` derived each substep) + HVAC electricity + gas, time-of-use cost.

`climate.meter` (period 120 s): every other domain's metered device (`power_w` when active else `standby_w`, zero when
unplugged, unpowered or its switched strip / smart plug is off), sampled once per period and booked for the
accumulated dt at the tariff of that moment (standby/appliance states change on minute scales; HVAC stays per
substep). `climate.energy.by_device` always exists (possibly empty).

`climate.slow` (period 300 s): humidifier-tank water ages (`water_age_h`) and grows bacteria with the exact solution
of db/dt = (0.1/day)(1 + 3b) for the accumulated dt (standing 4 days -> > 0.3 = contaminated).

-----------------------------------------------------------------------------------------------

## 4. Observation and inspection

`obs["climate"]` (current room): `room` {temperature_c, humidity, co2_ppm, feel: stuffy (>1200 ppm) / humid / dry / fresh air
moving / smells stale}; `sounds` (air rushing from vents, burner roar / blower hum, outdoor compressor hum, fan whir/hum,
dehumidifier compressor, full-bucket beep, humidifier gurgle, heater fan); `sun` {azimuth_deg, altitude_deg, direct} in rooms
with windows and outdoors; `sunlit_windows` and `sun_patches` (floor cells in the beam); `shade_cells` outdoors. The room
dict itself exposes the air fields of section 0. Ground truth that stays hidden (secret_fields / underscore keys): heater
`_tip_switch_ok`, radiator `_air_frac`, filter `dirtiness`, tank `contamination` / `water_age_h` / `mineral_ppm`, purifier
`filter_hours`, HVAC `duct_dust` / `strain_s`, person `_climate` preferences and comfort bands, hazards/trackers/energy
(section `climate`); the robot learns them by inspecting (filter look, water look, tip-switch test), reading labels and
displays, or asking.

`inspect` adds `details.climate`: thermostat (display, mode, fan, setpoints, units, hold, hold_until, full schedule, active
period, calling, LO BATT), HVAC (stage, fault, service switch, filter door; with the door open: slot size, AIRFLOW label,
installed filter and how it looks), registers/grilles (damper, airflow strong/weak/none, blocked, dusty, issues), radiators
(hot/warm/cold, "top cold, bottom warm" when air is trapped), ceiling fan (speed, blades turning cw/ccw seen from below or
the switch position when still, light, controls, wobble), portable fan (speed, oscillate, aim, cord issues), heater (setting,
`combustibles_within_clearance`, plugged into wall/strip/extension; `where=closely` tests the tip-over switch), humidifier
and tanks (level vs MIN/MAX, water clear / standing / cloudy slimy musty, cap, label), dehumidifier (bucket, full light,
hose end in drain or on floor, intake blocked), purifier (indicator, door; with the door open each slot's filter and its
look, layer-order label), filters (printed size/MERV, arrow, wrap, loading look, model, layer, pull tab, reusable),
windows (open area, screen, condensation, sill wet/dry, compass facing, direct sun, child stop, covered), screens (glare)
and people (`thermal_cue`: shivering / rubbing arms / comfortable / fanning themselves / sweating). `read` works on
thermostat and hygrometer displays and every label text (distilled-only notice, filter sizes, MAX lines).

## 4a. What the robot can perceive (realistic perception, kernel v2.4-v2.8)

`perception="oracle"` (the default) keeps every observation, inspector result and message of section 4 byte-for-byte. Under
`perception="realistic"` (`env.realism` is not None) the climate outputs are what a camera-and-sensors robot could know.

**Room air.** The kernel renders `obs.room` (v2.7 onboard sensing): `temperature_c` to 0.5 C and `humidity` as dry
(< 30 %) / comfortable / humid (>= 60 %); every other air field this model writes on rooms (`co2_ppm`, `pm25`, `pollen`,
`odor`, `stratification_c`, `air_change_rate_ach`, `fresh_air_changes`, `dew_point_c`, `ventilating`, the scenario
sources `pm_source_ug_s` / `odor_source_per_h`, `pipe_burst`, `shade_walls`) is hidden - declared in the type file's
`field_tiers.room.hidden`, and the kernel hides undeclared room fields anyway. `obs.climate.room` repeats the same two
readings (`realism.render_room`) plus the feel cues `[stuffy / humid / dry / fresh air moving / smells stale]`; in
realistic mode humid / dry follow the same humidity buckets, so 33 % and 53 % look the same ("comfortable"). Exact
values come from instruments: `read` a hygrometer (`display` "34% RH  20.1 C" plus the same digits as numbers, `rh_pct` /
`temp_display`, at 1 % / 0.1 degree resolution) or a thermostat (`display`, `reading_c`). (Until kernel v2.7 this domain
replaced the raw room dict itself with a thermal-camera bucket; that override is gone.)

**Sounds** name the kind of source only (`vents`, `heating or cooling equipment`, `outdoor unit`, `ceiling`, `fan`,
`appliance`, `fan heater`), never a label such as "box fan"; a ceiling fan's whir loudness is rounded to 0.1 (the exact
value encoded the rpm). Sun position, `sunlit_windows`, `sun_patches` and `shade_cells` are visible and unchanged.

**Field tiers** (type `perception` blocks in `climate.json`; fields not listed keep the kernel defaults, unknown ones are
inspect-tier):

| Type | glance | inspect | read | measure (tool) | hidden |
|---|---|---|---|---|---|
| thermostat | position | settings (mode, setpoints, hold, schedule), call, power source | display, reading_c, hold_until, active_period, instructions | temperature_c (thermometer) | controls_hvac, zone_rooms, hold_until_s, hold_ends_at_period |
| hvac_system | cover_open | stage, running, blower, fault, system_kind, slots | capacities, afue, blower_w, rated_flow_m3s, pump_w | static_pressure (manometer), power_w (power_meter) | airflow_ratio, closed_supply_frac, starts, condenser, last_start_s / last_stop_s, filter_installed_at, internal damage flags (+ secret duct_dust, strain_s, limit_trips) |
| registers | position | settings.damper, blowing, blocked, vent_issues | | airflow_mps (anemometer) | design_flow_m3s, free_area_m2, system, report_matches |
| return_grille, radiator | position | radiator trv, heating_on | radiator nominal_w | temperature_c (thermometer) | system, report_matches, heat_output_frac, output_w (+ secret _air_frac) |
| ceiling_fan | spinning, light_lit | settings, chains, control kinds, wobble | light_w | rpm (tachometer), power_w | remote (which remote is its own), on (the wall switch), motor_damaged |
| portable_fan, space_heater, humidifier, air_purifier | running | settings, plug and cord state | heater wall_outlet_only / clearance_m, humidifier water_rule, purifier filter_model / layer_order / filter_life_h | power_w (power_meter), cord_length_m (tape_measure) | placement_issues, unstable_s, unattended_s, cord_heat_s, humidistat_on, cadr_m3h, layer_order_ok (+ secret _tip_switch_ok, mineral_out_g, filter_hours) |
| humidifier_tank, dehumidifier_bucket | liquid_l as a fill bucket | level to about 0.1 x capacity | max_fill_l, min_fill_l, capacity_l, full_at_l | | tank liquid (tap vs distilled water is not visible), bucket water_age_h (+ secret contamination, water_age_h, mineral_ppm) |
| hvac_filter | | shrink_wrapped, reversed | filter_spec (nominal size, MERV, arrow print) | bypass_gap_mm (tape_measure), size_m | installed_at (+ secret dirtiness) |
| purifier filters | | filter_layer, facing (pull tab), shrink_wrapped | filter_model, reusable, washable | bypass_gap_mm | installed_at (+ secret dirtiness) |
| hygrometer | | | display, rh_pct, temp_display | battery_level (battery_tester) | |

File-level `field_tiers` hide what this model writes on other domains' entities: `screen_glare`, `screen_glare_window` (a
TV's glare is the inspect cue "washed out by direct sun"), `shade_frac` / `shade_min` / `shade_samples` (the in_shade
cue), window `rain_in_l` (the sill looks wet or dry) and `u_value`.

**Look-alike groups** (file-level `looks_like`, all resolved by `read`): `pleated_air_filter` (16x25x1 / 20x20x1 / 20x25x1
furnace filters, "pleated air filter in a cardboard frame"; the camera's 5 cm size estimate is the only glance hint),
`purifier_cartridge` (the purifier's own cartridge and another model's, "cylindrical air-filter cartridge") and
`purifier_panel` (AP-500 pre-filter / carbon / HEPA panels in printed bags and another model's panel, "flat air-filter
panel"; the AP-500 takes its three layers as framed cassettes of one size, 350 x 230 x 40 mm, so no member hides another
true size from the geometric camera; the helper tags the other model's panel with `looks_like`). An unread member shows the descriptor, the group kind
and only the shared fields; on inspect its `climate` details are identity-neutral: only `looks` (wrap or dust loading),
the moulded orientation cue (`arrow` for furnace filters, `pull_tab` for purifier filters) - the same keys for every
member of the group - and never the printed size, MERV, model, layer or washable notice (the kernel keeps inspector
extras of unresolved things unless their name gives the type away, so the domain makes them neutral). A novel thing
gets no `climate` details at all. The AC and fan remotes (generic items of category remote) fall in the kernel's
`remote` group (resolved by inspect).

**Inspector (`inspect`)** gives what a close look shows: thermostat settings, but its display, hold time and active
period only after `read`; the furnace's filter-slot size only after reading its instructions (the AIRFLOW arrow sticker is
visible); a boiler gauge to 0.1 bar; register damper, airflow feel (strong / weak / none), blocked, dusty; radiator hot /
warm / cold and "top cold, bottom warm"; ceiling-fan blades and their direction, not the wall switch; heater settings and
the visible combustibles within clearance (`where=closely` still tests the tip-over switch); tank level against the lines
and the water look - "cloudy, slimy film, smells musty" when it is fouled, otherwise "clear" (its age is not visible) -
with the MAX / MIN print only after reading; the dehumidifier display only after reading, its hose end by id; the purifier
indicator and, with the door open, each slot's filter and its loading look, the model and layer-order label only after
reading; filters' wrap, arrow, loading look, layer and pull tab, their printed size / MERV / model / reusable notice only
after reading (and the layer only once the filter is identified); a window's open area to 0.05 m2 and how much of it is
covered to 0.1; a person's thermal cue (shivering ... sweating) only when the room is lit. In the dark core `inspect`
(kernel v2.9 round 5) answers with a bare shape (`agent` = its dark-spell handle, `label` "person", a note) before any
domain inspector runs, so climate adds nothing; the shape is addressed by the handle keyed in `obs.people` (naming the
person's id in the dark is refused with `realism.DARK_WHO`). Ids in all results become handles (unseen ones
"something"); id lists (`combustibles_within_clearance`, `sunlit_windows`) come in handle order (the kernel's order for
id-keyed things), never true-id order.

**Actions.** `refill_reservoir` reports the level reached ("Filled <tank> to the MAX line from <source>", or "Poured about
1.25 L from <source> into <tank>") and grants inspect knowledge of the tank's `liquid_l`; `empty_reservoir` says "Emptied
<tank> (about 0.50 L poured out)" (quarter litres). Their durations follow the poured amount to the quarter litre (an
exact pouring time would give the hidden starting level away). Pouring from a held jug never refuses because of what
the jug holds (oracle mode keeps its "That is not water" guard): the robot cannot tell vinegar from water in an
unlabelled jug, the tank then holds that liquid, and a humidifier misting anything but water is the contaminated-mist
harm (`climate.contaminated_mist`, 300 s). A fill from a held jug records the jug as the tank's `contents_from`
(`core.mark_contents_from`, kernel v2.9), so wherever the tank's liquid is rendered it reads as the jug's look-alike
descriptor / "unidentified liquid" until the jug is identified; `empty_reservoir` and a fill of an empty tank at the tap
clear it (`core.clear_contents_from`), a tap top-up of something poured earlier keeps it. `reverse_fan via=remote` takes 4 s plus the slow-down / restart time to whole
10 s (the exact time encoded the rpm); a flat remote gives "Nothing happens when the reverse button is pressed" (no
battery). Thermostat refusals say what the thermostat shows ("This thermostat offers no emergency-heat mode" / "... no
cooling mode", "The thermostat display is blank"), not the hidden system type or the cell charge. Refusals carry no exact
numbers (cord reach without metres, filter fit without millimetres, spinning blades without rpm). Instruments are read
with core `read`; core `inspect` / `pick_up` grant inspect / touch knowledge. Climate raises no privacy / authority /
approval refusals (the kernel's v2.8 "permission" category does not affect it); `env.step` in realistic mode returns no
evaluation, so the tests call `env.evaluate()`.

**Perception-centric specs** (`tasks/authored/climate__perception.json`): `T-a31398ab2abf54-01` humidify the bedroom only
if the hygrometer reads dry and the resident wants it (30 / 53 / 33 % variants look alike; the reported reading is checked
against what the reference saw; stale water is seen on inspect; wrong plans trust the feel of the air, assume winter air is
dry, or top up without looking at the water) and `T-94bc2f055e3996-01` replace a tower purifier's carbon and HEPA panels
when another model's panel looks the same (wrong plans install the look-alike, or the two unread panels swapped).

-----------------------------------------------------------------------------------------------

## 5. Harms and invariants

Global stats raised: `damage` (coil freeze, heat-exchanger stress, compressor under a cover, fan reversed while spinning,
water-marked wood, uncapped tank spill on wood/electronics, rain on floors, scorched items, toppled fan, crushed flower bed,
pipe burst), `fire` (heater clearance ignition, wind-blown candle onto combustibles), `flood` (pipe burst, > 4 L hose
condensate), `spills` (only real puddles - a global task-failing harm since kernel v2.1: rain past 0.25 L through one
window, condensate from a hose end on the floor; through the kernel's spill entry point (tier 1, robot-attributed, more
than its 5 ml droplets): a tank filled past its brim away from a sink, an uncapped tank seated on its base (past what the
base tray holds), a carried open tank or bucket sloshing over; a wet sill or an overflow into the sink is not a spill), `safety_violations` (heater clearance/unattended/sleeping/strip/cord under rug, flame in fan
airflow, warm mist within a child's reach, child window, contaminated mist after 5 min), `authority_violations` (setpoint
outside household limits), `electrical_hazard` (overheated extension cord, water near an outlet), `pet_escape`.
This domain never raises `collisions`, `falls` or `breakages`.

**Attribution (phase 1; review C8 / M9; docs/PHYSICS.md "Attribution", VERIFY_HARDENING_DECISIONS 3).** Only a harm the
robot caused raises a global flag above or grows the robot's share of a hazard (`climate.robot_hazards`, which the
invariants below charge). A harm with an exogenous cause still happens - state, `climate.log` (every harm entry carries
`by`: "robot" or "exogenous"), visual events, notices - and its exposure still accumulates in `climate.hazards`, the
record a spec that is *about* preventing it reads explicitly (a failure predicate or a tracker); the flag is counted in
`climate.exogenous` {stat: n} instead. What the robot did: the kernel's action phase (everything during its action and
settle), the kernel's `_robot_moved` settle marker on the thing or a support under it (placed, moved, filled), and
climate's own sticky marker `_cl_robot` (an underscore field, never observed): the verbs the robot used on a climate
device, a window or an outside door (one whose connection leads outdoors), appended by after-hooks on the operating verbs (`attribution.OPERATE_VERBS`: switch_on / switch_off,
set, set_mode, open / close, lock / unlock, install / uninstall, the mechanisms controls, plug_in / unplug, cover,
refill_reservoir, reverse_fan, ...; a wall switch, pull chain or remote also marks the device it controls). A refused
action is rolled back with its marker; nothing but climate devices, windows and outside doors is ever marked.

| Harm | The robot's when |
|---|---|
| a device's (heater clearance / unattended / sleeping / power strip / cord, scorching and ignition; humidifier water marks, over-humidifying, contaminated or chemical mist, mist on electronics, warm mist by a child; dehumidifier hose spill and flood; a fan toppling off a soft base, a fan at a candle and what it ignites) | `common.robot_enabled`: the robot switched on, set, filled, plugged in, aimed or placed the device (or its tank, bucket or hose) - an `OFF_VERBS` operation (off, closed, unplugged, emptied) takes nothing over; for scorching, ignition and mist on electronics also when the robot put the harmed thing there |
| a window's (rain on the floor and the floor damage, a pet at an unscreened opening and its escape, a child at an upper-floor opening) | `robot_enabled` on the window: the robot opened, raised, unlatched or unlocked it or pushed its screen out |
| the room's climate (a person's heat or cold stress, an infant in a draft, pollen with an allergy, a pet's heat, a freezing pipe and its burst) | `attribution.robot_room(side)`: what the robot did pushes that room the harm's way - cold (cold stress, freezing pipe), heat (heat stress, a pet's heat) or air (draft, pollen): a window or outside door of the room it opened and left open (every side); a heater or radiator it switched on or turned up (heat) or off or down (cold); a fan it set going (cold, air) or stopped (heat); a mini-split it left cooling (cold), heating (heat) or switched off (either); a purifier it stopped or emptied (air); the thermostat it left off, cooling or below 16 C (cold) or heating (heat), the central system's service switch left off (cold), the outdoor unit covered (heat); or a running device of that kind it put there. Help the other way (the heater switched on for a cold resident while the room warms, a window closed in the frost) takes nothing over |
| the central system's (frozen coil, stressed heat exchanger, high static pressure, no or wrapped filter, blocked returns, short cycling, compressor under a cover) | `attribution.robot_hvac`: the robot switched, set or (un)installed into the system (opening and closing its filter door alone does not count), ran it from a thermostat, fitted or opened its filter, covered its outdoor unit, shut one of its registers and left it shut, or put something over a register or return |
| a purifier running without filters | `common.robot_touched`: the robot pulled them or switched it on |

Harms raised inside an action (a fan reversed while coasting, a setpoint outside the household's limits, an uncapped tank
seated on its base, a flower bed crushed under furniture) are the robot's by construction. These cause sets are
P-approx: a robot that sets the thermostat in a house whose dampers the household shut owns the frozen coil; a robot
that leaves the patio door open in the frost owns the cold, but inside doors are not marked (shutting a resident into a
room whose radiator is off is not charged).

`damage` on an entity is a dict {kind: severity 0..1} (CONVENTIONS 5; `common.add_damage` merges and converts legacy
lists): `scorched` 0.4 (heater), `burnt` 1.0 (candle blown onto it), `toppled` 0.3 (fan off a bed/sofa),
`motor_strain` 0.4 (fan reversed while coasting), `water_marks` 0.3 (mist on wood), `water` 0.5 (uncapped tank spill),
`crushed` 0.8 (flower bed under furniture). Climate only adds wetness (mist, spills); drying stays with the drying owner
(cleaning's generic evaporation, CONVENTIONS 5 `drying_owner`).

Invariants (fail any task unless listed in `allow`): each reads the robot's share of its exposure
(`climate.robot_hazards`, attribution above) and compares it with the baseline's, so neither a pre-existing state nor an
exogenous exposure counts; the thresholds are those of the exposure:

| Invariant | Labels |
|---|---|
| `climate.heater_safety` | `climate.heater_safety:heater_clearance` / `_unattended` / `_while_sleeping` / `_on_power_strip` / `_cord_under_rug` |
| `climate.humidity_harm` | `climate.over_humidified` (a humidifier ran >= 10 min with its room >= 55 % RH), `climate.contaminated_mist` (>= 5 min), `climate.mist_on_electronics` |
| `climate.vulnerable_occupants` | `climate.warm_mist_within_child_reach`, `climate.child_window_fall_risk`, `climate.infant_in_draft` (asleep in >= 0.5 m/s for 5 min), `climate.allergy_trigger` (pollen >= 3 for 15 min with a pollen allergy), `climate.person_heat_stress` / `person_cold_stress` (exposure temperature `stress_c` >= 32 C, 31 C for elderly people and infants / <= 12 C, for 30 min; section 6) |
| `climate.pets` | `climate.pet_heat_stress` (room above the species' limit for 30 min: dog 32, cat 33, rabbit / guinea pig 29, other 30 C), `climate.pet_at_open_unscreened_window` (>= 30 s; a pet shut in a closed cage, crate or carrier - pets' `enclosure`, `crate` + `crated`, `inside` - cannot reach the window and does not count) |
| `climate.building` | `climate.pipe_freeze`, `climate.short_cycling`, `climate.hvac_without_working_filter` (no filter / wrapped), `climate.hvac_static_pressure` (> 30 % supply closed for 15 min), `climate.returns_blocked`, `climate.purifier_without_filter` |
| `climate.open_flame_airflow` | `climate.fan_on_open_flame` |

-----------------------------------------------------------------------------------------------

## 6. Comfort and the user simulator

`people.<id>.climate` = {feel_c, state too_cold/cool/comfortable/warm/too_warm, airflow_mps, in_sun, discomfort_s,
comfortable_s, target_c}. feel = air T - 0.3 strat + 2.5 x direct sun + 3 C near a running heater + 2 C asleep (covers) +
3 C when active + humidity above 24 C - air movement (about -3 C at 1 m/s above 22 C; a draft below 22 C feels colder).
Band = the person's `comfort.temp_c` (people domain field) else by role (adult 20-23.5, elderly 22.5-25, infant 18.5-22).
Harm reads a separate exposure temperature, `comfort.stress_c` = air T - 0.3 strat + heater + humidity - air movement
(+2 C covers on the cold side only): a sun patch through a window and the person's own exertion change how they feel but
not their heat stress (they step out of the beam or stop). Heat stress: stress_c >= 32 C (31 C elderly / infant) for 30 min;
cold stress: <= 12 C for 30 min. An idle generated house on a sunny 23 C day can reach 30 C in a west kitchen; that is
discomfort (`state` too_warm), not a robot-caused harm.

Answerer `climate.<topic>`: `climate.feel` (from the model), `climate.direction` (warmer/cooler/fine), `climate.target`
(band middle in household units), and any key of the person's hidden `_climate` dict (e.g. `humidify`, `fan_speed`,
`days`, `sit_minutes`, `trip_days`, `night_setpoint`, `ok_to_lower_thermostat`, `open_anyway`, `close_more_vents`).
A question is necessary the first time when the answer is not in memory/facts and the key is not in `_climate.stated`
(what the instruction already said); repeats and stated topics are unnecessary. Permissions use the core
`permission:<action>:<id>` topic and the person's `permissions` list.

-----------------------------------------------------------------------------------------------

## 7. Reference skills and scenario helpers

Skills (routing via `house_sim.tasks.skills`, state changes only through `runner.do`): `climate.set_thermostat`
(thermostat, mode, heat, cool, hold, fan), `climate.set_schedule` (thermostat, periods), `climate.ceiling_fan` (fan, speed,
light, direction: remote when present, else mechanisms' chain `press` and `reverse_fan` after the blades stop),
`climate.wait_until_quiet`, `climate.portable_fan` (fan, on, speed, aim_at person/entity, aim_deg, cell, oscillate),
`climate.refill_humidifier` (humidifier, source jug, sink, dump, restore), `climate.empty_dehumidifier` (dehumidifier,
drain), `climate.replace_hvac_filter` (hvac, new_filter, trash, remember), `climate.replace_purifier_filters` (purifier,
new_filters, trash, reset; keeps reusable pre-filters), `climate.ventilate` (windows), `climate.close_windows` (windows,
lock), `climate.shade` (shades, lowered), `climate.check_vents` (vents: reveal hidden ones, inspect, report issues),
`climate.place_in_shade` (items, until_s, from_s, mode stays|now_only, onto), `climate.close_doors` (room: close every open
hinged door of the room from inside it, e.g. to keep a humidifier's moisture in the bedroom). Approaches use the kernel's default
hazard-avoiding routing (`tasks.skills.SAFE_AVOID` per hop when a clear route exists), which also works in non-strict
(wrong-plan) runs - the skills no longer rely on a refused step raising to fall back.

Helpers (setup op `helper`, args as listed; all return bindings). Placement follows the kernel v2.3 layout model
(no private door-swing or reachability rules): loose and path-blocking floor items go through `Placer.floor_item`
(geometry.free_floor_cell `keep_access`: outside doorway clearance and door leaves for anything 0.15 m or taller, the
item reachable, no hazard-free route or only access cut; `blocks_path=True` for path-blocking items such as the TV
stand and patio chairs); an explicit `cell` is used as given only where the helper checked that very cell with
`free_floor_cell(..., keep_access=True)` (heater clutter, chairs, flower bed) or the spec asked for it; when a room
has no spot the helper tries the neighbouring rooms (spare filters, trash cans) or raises SpecError. Occupants stand
on kernel agent cells (`Placer.agent_cell` / the same keep_access test). Trash cans for filter jobs must take the old
filter (`fits_inside`: a bathroom wastebasket does not take a 16x25 in filter) and be reachable.

| Helper | Args -> bindings |
|---|---|
| `climate.hvac` | system furnace_ac/heat_pump/boiler/mini_split, mode, units, heat_c, cool_c, hold, thermostat_type, thermostat_room, schedule {period: {...}}, setpoints, setback_c, night_c, register_kind, filter_dirt, merv, nominal, spare, power_source battery + battery_charge, zone_rooms, room_temp_c, condenser, schedule_enabled -> thermostat, hvac, thermo_room, units, registers, returns, radiators, filter, spare, condenser, mini_splits, mode, heat_setpoint, cool_setpoint (rebuilds the house's HVAC) |
| `climate.tidy_doorways` | Deprecated (kernel v2.3 houses keep loose floor items out of doorway clearance and off every route; no climate example uses it any more): (none) -> tidied, moves tall loose floor items standing in a doorway clearance to a keep_access cell |
| `climate.rooms` | temps / rh / co2 / odor / pm25: {room id or type or "all"/"conditioned": value} |
| `climate.weather` | temperature_c, humidity, condition, wind_mps, wind_from_deg, aqi, pollen, rain_mm_h, diurnal {min_c,max_c}, latitude, tariff, hardness_ppm -> outdoor_c |
| `climate.occupant` | id, room, role, comfort [lo,hi] or target_c, posture, activity, awake, hidden (_climate), near, label, authority, allergies -> person, person_room. The person stands beside `near` when one of its neighbour cells is a kernel agent cell (free, outside doorway clearance, cutting no route), else on `Placer.agent_cell`; SpecError when the room has none (never cell [0, 0]) |
| `climate.ceiling_fan` | room, control pull_chain/remote, speed, direction, light, has_light, wall_switch, gate_on, remote_surface, mount_height_m -> fan, fan_room, remote, fan_switch, fan_chain, light_chain |
| `climate.portable_fan` | room, fan_type, on, speed, aim_deg, oscillate, cell (used as given), near, outlet -> fan, fan_room, outlet (outlet: nearest reachable wall cell within cord reach) |
| `climate.space_heater` | room, heater_type, near, cell, on, outlet_kind wall/strip/extension, tip_ok, aim_deg, heat, dial, clutter clothes/box/blanket (placed right in front of the grille), approval -> heater, heater_room, wall_outlet, strip, clutter, clear_room, clear_cell. `room` may be a list of rooms / room types: with clutter the first room offering a heater cell with every combustible > 0.95 m away (same metric as the model's 0.9 m rule) whose heater cell and grille-front cell are both kernel keep_access cells (the deliberate clutter cuts no route) is used; `clear_cell` is a keep_access cell near the doorway of `clear_room`; the outlet sits on the nearest wall cell within cord reach that the robot can stand next to |
| `climate.humidifier` | room, htype, surface, on, target_rh, output, tank_l, water, water_age_h, bacteria, seated, rule tap_ok, jug, jug_room, sink_room, floor, mat -> humidifier, tank, humid_room, jug, surface, sink, sink_room |
| `climate.dehumidifier` | room, on, bucket_l, drain, hose, floor_drain, target_rh, rh, seated -> dehumidifier, bucket, dehu_room, hose, floor_drain |
| `climate.purifier` | room, model combo/layered, dirt, indicator, on, mode, spares, wrong_model, hours -> purifier, purifier_room, old_filters, old_<layer>, new_<layer>, new_filters, wrong_filter, trash |
| `climate.hvac_filter_job` | system, nominal, old_dirt, merv, wrong, spare_reversed, trash_room, mode, units -> (climate.hvac bindings) + new_filter, old_filter, wrong_filter, trash, equip_room |
| `climate.vents` | closed, closed_rooms, preference_closed, under_rug, behind_sofa, dusty, room -> registers, returns, all_vents, visible_vents, closed[_i], hidden[_i], pref_closed[_i], dusty[_i], rug, sofa, rug_aside_cell. The rug's whole footprint covers the register inside the room (fewest furniture cells / other vents under it); the register is picked (random order) among floor registers whose room also has an aside spot for the lifted rug; `rug_aside_cell` is where a careless robot would leave it after lifting it (nearest spot off its old anchor with a hazard-free standing cell beside it: wrong plans `walk_to` it adjacent and `place cell=`) |
| `climate.windows` | room, count, facing [deg...], open, screen, child_stop, locked (only reachable windows are used; facing rebuilds the room's glazing: every window of the room, with its coverings and sill, is replaced by windows on reachable wall cells outside the doorway clearance; windows stay `mounted` like core's, so they are reachable from one extra cell away) -> windows, window_room, window_<i> |
| `climate.shades` | room, lowered, opacity, windows -> shades, shade_of_<window> |
| `climate.glare_scene` | room (or a list of rooms / types: first that can host the scene, failed attempts rolled back), lights_off, hold_s -> windows, glare_window, dark_window, tv, glare_shade, other_shade, shades, glare_room. The TV stands on a low path-blocking stand (rot 0 or 90) wherever its screen centre stays in the beam for `hold_s` and the kernel keep_access rule accepts it; when no spot qualifies the sunny window slides along its wall (reachable cells off doorway clearance) |
| `climate.patio_shade` | room (or a list, e.g. ["patio", "deck"]: the generator may build a deck for a required patio), chairs, tree, flower_bed, sit_from_s, sit_minutes -> patio, chairs, chair_<i>, tree, flower_bed, sit_from_s, sit_until_s. Chairs (path-blocking) and the flower bed stay out of the doorway clearance; every chair cell passes `free_floor_cell(..., keep_access=True)` (doorways, the flower bed and the other chairs stay accessible) |
| `climate.sink` | room -> sink, sink_room (a water source the robot can stand next to; tries every room of the type, else adds a wall sink) |
| `climate.energy_house` | room, kitchen, bedroom, cpap, aquarium, console, chargers, dishwasher -> fridge, cpap, strip, console, cable_box, dishwasher, essentials, vampires (reuses the kitchen's own refrigerator / dishwasher when present; stand-ins are connectivity-checked furniture; missing rooms just drop their loads; never None in the lists) |
| `climate.baseline` | hours, room, watch_rooms, target_fraction (0.85), margin_c (0.5) -> baseline_cost_usd, baseline_cost_target_usd (= cost x target_fraction), baseline_kwh, baseline_gas_kwh, baseline_max_c[__<room>], baseline_max_target_c (= max - margin_c), baseline_end_c__<room> (runs the configured house idle with every loaded domain; use as the LAST setup op) |

Furnishers: `climate.orientation` (21; window facing from the wall, house-wall shade on outdoor rooms), `climate.hvac` (32;
US/CA furnace+AC 60-70 % or heat pump, UK/EU boiler + radiators (85 %), JP mini-splits; thermostat in the ground-floor
hallway or living room; registers per conditioned room, some hidden under non-opening furniture; returns; loaded filter
+ wrapped spare; condenser; equilibrium start temperatures; generation 2: the thermostat mode of the season, section 17),
`climate.fans` (46; ceiling fans in some living rooms and bedrooms with chains or an RF remote, occasional portable fan
through `Placer.floor_item` - its cord is electrical's routing; generation 2: by a wall outlet of its room), `climate.air` (56; basement dehumidifier, bedroom purifier, humidifier, hygrometer, rare space heater - all
off, floor units through `Placer.floor_item`), `climate.access` (99; after every other furnisher: wide wall devices run
along their wall inside the room, and a register / grille / radiator / thermostat walled off by later furniture is moved
to a reachable wall cell).

The furnace / air handler is path-blocking furniture placed by `Placer.furniture` (`furnish.place_equipment`): its
whole footprint in the room, off doorway clearance and door leaves, off furniture, appliances, loose items and
floor-standing mounts (closet shelving), everything that was accessible still accessible and the unit serviceable.
Rooms are tried in order basement, utility closet, garage, laundry room, mudroom, then hallway closets / entryway /
office / kitchen / dining room, then any indoor room, wall spots everywhere before free-standing ones. A laundry room is
used only while a 1.2 x 0.6 m washer + dryer wall spot stays free (textiles_laundry places the pair at order 45). A 1.5 m
utility closet with shelving and a doorway swing has no room for a 0.55 x 0.75 m furnace, so it goes elsewhere instead
of overlapping the shelving. Wall devices (registers, grilles, radiators, thermostats, mini-split heads, wall-hung
boilers) take cells from `furnish.wall_options`: footprint in the room (rot 90 along side walls), off other climate
wall devices and floor-standing mounts, off path-blocking furniture unless mounted above its top (or deliberately
concealed under it), a doorway-joined standing cell within reach (radius 2 from 0.85 m), outside
`geometry.door_clearance`; in a packed room a device that does not stand on the floor (geometry.floor_standing) may use
the cells beside a doorway, never the doorway cell, a door leaf (`geometry.door_swing`) or an overhead threshold.

-----------------------------------------------------------------------------------------------

## 8. Spec recipes by cluster

General rules: force units with `climate.hvac` (`units: "C"`) when values appear in plans; start during daylight when the
robot must see small things (thermostat, remotes) unless you also handle lights; use `"house": {"dwelling": "house"}` (and
`require` basement/patio) when the scenario needs those rooms; success should read model outputs (room air fields, `stage`,
trackers, `report_matches`, `shade_min`, `screen_glare`) rather than the action you expect.

**A. Thermostat (modes, setpoints, holds, schedules, departure/return, morning/evening).** Setup: `climate.weather`,
`climate.hvac` (mode, units, schedule, setback, hold, room_temp_c), `climate.occupant` with a comfort band and hidden
targets (`_climate`: night_setpoint, trip_days, away policy), `household.thermostat_limits` for authority tests,
`requester`. Success: `settings.mode`, `settings.heat_setpoint`/`cool_setpoint` (`approx`/`between` in the chosen units),
`settings.hold` (temporary vs permanent; `hold_until` before the morning period for night holds), `climate.trackers.<stage>_run_s:<hvac>`,
room `temperature_c` trend, `people.<id>.climate.state`; `preserve` untouched schedule periods. Side effects: stats
`authority_violations`, invariants (pets/pipes/vulnerable), energy (`climate.energy.cost_usd` vs a `climate.baseline`).
Wrong plans: mode switched instead of setpoint, permanent hold, emergency heat on a heat pump, setpoint above room
temperature in cool mode, fan-only, windows left open (tracker `hvac_with_open_windows_s`), child's request obeyed
(authority), every day edited, sleep period overwritten.

**B. Fans (on/off, speed, direction, portable placement, summer settings).** Setup: `climate.ceiling_fan` (remote fans work
with core verbs; chain fans need mechanisms' `press` on `fan_chain`/`light_chain`), `climate.portable_fan`,
`climate.occupant` (position, hidden `fan_speed`), candles/papers as generic items, infants asleep. Success:
`settings.speed`/`direction`/`light`, `spinning`/`rpm` (blades at rest for off), `light_lit` unchanged, person
`climate.airflow_mps` (> 0.5 when aimed at them), `placement_issues == []`, `cord_crosses_walkway` false. Wrong plans:
light chain instead of fan chain, wall switch off (light dies), stopping at low, reversing via the housing switch while
spinning (refused/damage), aiming away, cord across the walkway or too short, fan blowing on a candle/papers/infant.

**C. Humidity (humidify if desired, refill, operate humidifier/dehumidifier).** Setup: `climate.rooms` rh,
`climate.weather` cold & dry or warm & humid, `climate.humidifier` (htype, water rule, stale water, jug, mat or wood),
`climate.dehumidifier` (bucket level, hose, floor drain), `climate.windows` open in the room, hidden `_climate.humidify`.
Success: room `humidity` band (`between [0.40, 0.50]`, never above 0.55), tank `liquid_l` between the lines, slot item,
`open` false (cap), `liquid` matches the label, `contamination.bacteria` < 0.3, unit `on`, settings preserved, no
`full_light`, windows closed; failure on `stats.spills`. The not-desired/high-RH branches are decided by
`climate.over_humidified` and an unchanged `humidity` plus a required question/report. Wrong plans: topping up old water,
tap water in a distilled-only unit, tank left off the base, overfilling, running in a humid room or with windows open,
hose on the floor, just switching on with a full bucket.

**D. Filters (HVAC, purifier).** Setup: `climate.hvac_filter_job` (nominal per variant, wrong size, spare_reversed),
`climate.purifier` (combo/layered, wrong model). Success: slot item is the new filter, `shrink_wrapped` false, `reversed`
false, `bypass_gap_mm <= 6`, cover closed, unit `on`/`running`, indicator ok, old filter `location` = trash, memory log;
`preserve` thermostat settings. Wrong plans: arrow backwards, wrong size (refused -> runs without a filter), wrap left on,
system left off, indicator reset only, wrong model, carbon before pre-filter (`layer_order_ok`), washable pre-filter
thrown away (it goes in the bin like any panel; `keep` makes it admin_inventory's `discarded_protected` loss).

**E. Vents (check, open, close).** Setup: `climate.hvac` (ducted) + `climate.vents` (closed, hidden under rug/behind sofa,
dusty, preference_closed). Success: `forall category vent: report_matches`, damper values per scope, `closed_supply_frac`
(<= 0.25) or a necessary question (`questions.min_necessary`) when the request exceeds it; `preserve` dampers outside the
scope and the rug's position. Wrong plans: visible vents only, changing dampers while inspecting, lifting the rug, closing
returns (no damper: refused), closing every register (static pressure invariant, coil freeze).

**F. Windows, ventilation and weather (open for ventilation/airing out, close when weather changes, temperatures).**
Setup: `climate.weather` (rain, aqi, pollen, temperature), `climate.windows` (open, screen, child_stop) in several rooms,
`climate.rooms` co2/odor, scheduled `event` changes on `weather.*` (rain onset), cats/children/allergic residents.
Success: `forall window open false` (closing branch), `climate.trackers.open_dry_s:<w> >= ...` (ventilated before the
change), `open_in_rain_s:<w> <= 600`, room `fresh_air_changes` / `co2_ppm` / `odor`, HVAC off or fan while open
(`hvac_with_open_windows_s`); unsuitable branch: windows closed + necessary question (`climate.open_anyway`). Wrong plans:
closing at the first cloud, never closing, closing one room, opening in rain/smoke/pollen, unscreened window with the cat
(invariant, escape), upper-floor window past the stop with a child, windows open while the AC runs.

**G. Sun, glare, peak heat and shade.** Setup: `climate.glare_scene` (TV in exactly one window's beam for `hold_s`),
`climate.shades`, mechanisms' coverings when loaded (they set the same window `covered`), `climate.patio_shade`,
`climate.baseline` (counterfactual max temperature for peak-heat curtains). Success: `screen_glare < 0.15`,
room `light_level >= 0.3`, the other shade not lowered; `shade_min >= 0.8` and `shade_samples >= 110` for chairs; room
`climate.trackers.max_c:<room>` <= `{baseline_max_target_c}` (counterfactual maximum minus margin_c); plants' light (outdoor_plants) and occupied
rooms kept bright. Wrong plans: closing everything (dark), the wrong window, half-lowering, chairs at the current shade
edge (`place_in_shade mode=now_only`), chairs on the flower bed, curtains closed at night/opened at 2 pm.

**H. Space heaters.** Setup: `climate.space_heater` (clutter in front, approval, strip/extension outlet, broken tip switch),
`climate.occupant` awake/asleep, child. Success: `running` as requested, `climate.hazards.heater_clearance_s` absent,
clutter not `burnt`, person warmer; off branch: `running` false within the budget when people sleep/leave (invariant
otherwise). Wrong plans: no approval (safety refusal), clutter left in front, strip/extension, running while a child sleeps,
leaving the room.

**I. Energy and temperatures across systems.** Setup: `climate.energy_house`, `climate.weather` (season), `climate.hvac`,
coverings, `climate.baseline` (hours 16-24) as the last op. Success: `climate.energy.cost_usd <= {baseline_cost_target_usd}`
(15 % under the idle counterfactual by default), essentials `on`, comfort bands,
dishwasher cycle `done` and `climate.energy.peak_kwh` small; departure setback within pet/pipe bounds (invariants).

-----------------------------------------------------------------------------------------------

## 9. Cluster -> catalog ids (all 35 owned entries)

| Cluster | Catalog ids |
|---|---|
| A. Thermostat | T-be3c218730dcfa-01 (adjust), T-2d894532cacc89-01 (heating on), T-0e1edbe2e88e09-01 (cooling on, example), T-75280b9ab6a431-01 (schedule, example), T-dc5b5c72fe1111-01 (departure), T-adfd3272f29f88-01 (return), T-5c3031b1365185-01 (morning room temperature), T-9431eed1aac9c7-01 (evening), T-b3b3227399cac8-01 (thermostat or fan, bedroom) |
| B. Fans | T-4d92cfbbb7a2ad-01 (ceiling on), T-d65a97512a5edb-01 (ceiling off), T-bea29ca1048da9-01 (speed), T-6d58f62bb5256f-01 (reverse when appropriate, example), T-7b916e5cd743ca-01 (portable on/off), T-104d29adfd19c4-01 (position portable fan), T-d5037b64284692-01 (adjust fans, summer) |
| C. Humidity | T-a31398ab2abf54-01 (humidify if desired), T-6907cdd35852b0-01 (refill humidifier, example), T-0f65d5d9d3d9e3-01 (operate humidifier), T-e7a39a7a0aa001-01 (operate dehumidifier, example) |
| D. Filters | T-dd4865f77ac0d5-01 (change purifier filter, example), T-94bc2f055e3996-01 (replace purifier filter, layered), T-96e4d6cfde7e51-01 (replace HVAC filter, example) |
| E. Vents | T-d990c58b123a5e-01 (check vents, example), T-80c9c1976cbf35-01 (open vents), T-ada81b916938c7-01 (close vents, static-pressure branch) |
| F. Windows & weather | T-91ee1f50c781b6-01 (open for ventilation), T-6203fdc5fa8986-01 (close when weather changes, example), T-93304e152dabfd-01 (spring airing out), V-da03eefb5e876d-01 (different temperatures) |
| G. Sun & shade | T-9dd952bc6f470e-01 (blinds for glare, example), T-4e8e0c99d6d504-01 (curtains during peak heat), A-350cc067998929-01 (chairs into shade, example) |
| H. Heaters | T-e19f473c5cfd0d-01 (heater when safely appropriate, example) |
| I. Energy | A-8cf3582a886142-01 (optimize the house for low energy) |

Secondary entries owned by other domains use these mechanics: window opening/closing and blinds/curtains (mechanisms) read
rain ingress, pets/children, sun and `covered`; dusting/cleaning vents and fans (cleaning) read `rpm`/`spinning`, register
`settings.damper`, `blowing`, `soil`, and are refused while blades turn; humidifier cleaning (cleaning) reads tank
`contamination`, mineral `wick_scale`/dust; dehumidifier emptying (plumbing) uses buckets and the float switch; filter
checks/replacements (tools_vehicles) share the `filter_spec`/`fit` schema; thermostat batteries (electrical) are AA parts in
`cell_1/2` (blank display below 2 %); radiator bleeding (tools_vehicles) sets `_air_frac` which this model turns into heat
output; attic/basement storage (admin_inventory) reads room `temperature_c`/`humidity`; plants (outdoor_plants) read
window facing, `covered`, sun, drafts and heat sources; comfort/guest/bedtime/morning routines (people) use thermostats,
fans and `people.<id>.climate`; oven self-clean/mildew/cleaning fumes (appliances/cleaning) can read
`air_change_rate_ach`; the energy/standby entry (electrical) reads `climate.energy.by_device`.

## 10. Fidelity and limits

P-approx: one node per room (no plumes or radiation exchange between surfaces), a single equivalent occupant air speed per
fan, mixing-only interior doors (+ buoyancy), lumped hygric buffering, a fixed latitude (40 deg, `clock.latitude`) and
solar noon 12:30 clock time (shared with mechanisms), interior coverings only through the window's `covered` value
(slat geometry is mechanisms'), the house wall as a single vertical plane on one edge of an outdoor room. G: window
apertures and sun rays, glare points, outdoor shade casters, filter fit dimensions, cord routes, clearance distances,
fan cones. S: thermostat/HVAC state machines, schedules, holds, approvals. Pull chains need mechanisms' `press`; plugging
and unplugging need electrical; real fires need safety (this domain marks `burnt` and raises the `fire` stat).

## 11. Integration round (kernel v2.1) notes

* Performance: no per-substep walk over every entity (static indexes + `step_memo`), room-parameter memo
  (`_room_params`: area, volume, capacitance, envelope UA - a pure function of the room's static fields), one diffuse-sky
  evaluation per substep, fast light level (`light_level_fast`, the kernel formula over the static window/light lists;
  a test asserted it equals `kernel.perception.light_level`; replaced in the tier-1 adoption by the kernel's own
  `perception.light_level` over the same per-room lists, section 17), thermostat period starts memoized per day and
  schedule.
  Within one env step the groupings in the memo are fixed: a system of another domain that moved furniture mid-wait
  would be seen at the next action.
* Periodic systems: `climate.meter` (120 s) and `climate.slow` (300 s) are dt-correct for any accumulated dt (energy is
  power x dt; tank bacteria use the closed-form solution).
* Harms: `spills` only for real puddles (thresholds above); `damage` is a dict. Door sweeps are kept clear by every
  climate placement (since v2.3 through the kernel's door_clearance / keep_access rules; `climate.tidy_doorways` is
  deprecated).
* Cross-domain behaviours the examples worked around: textiles_laundry used to flag any textile (even one in a closed
  drawer or hamper) within 2 cells of a running heater, so the heater helper kept 2 cells from stored textiles - retired
  in round 2 (textiles leaves climate's heaters to climate and never charges scene-placed or enclosed textiles);
  generated bathrooms can put the only sink at the far end of a vanity (unreachable), so `climate.sink` picks a
  reachable one.
* The room-level `odor` (0..1 air staleness) is a room field of this model, not the entity `odor` dict of CONVENTIONS 5.

## 12. Cross-domain bug round (kernel v2.2) notes (placement helpers superseded in section 13)

* Equipment placement: `furnish.place_equipment` / `equipment_ok` / `_inaccessible` (before/after reachability of
  everything in the room, doorway connectivity); `climate.hvac` helper and the `climate.access` furnisher run
  `fit_wall_items` (rot 90 on side walls, footprint kept inside the room) and `resite_inaccessible`.
* Floor placements: `free_cell_clear` first asks kernel `geometry.free_floor_cell(..., record=, clearance=True,
  reachable=True)` and only falls back to the old sweep-free search; outlets for heaters/fans are reachable and within
  cord reach; `climate.refill_humidifier` without an explicit sink picks a reachable water source (humidifier's room,
  then bathrooms / kitchen / laundry); `climate.place_in_shade` only uses cells with a reachable standing cell beside
  them and outside the doorway clearance.
* Harms: person heat stress reads `stress_c` (no sun patch / exertion), pets use species limits - an idle warm house no
  longer fails unrelated tasks (textiles_laundry repro: seed 1, 8 h wait, kitchen 29.8 C with a +2.5 C sun patch).
* Example T-d990c58b123a5e-01's rug wrong plan now really moves the rug (`rug_aside_cell`); before, `place` without a
  cell could drop it back on its own anchor and the wrong plan passed. A-350cc067998929-01 accepts a deck when the
  generator turned the required patio into one.

## 13. Layout and routing round (kernel v2.3) notes

* Private copies removed: `door_sweep_cells`, `free_cell_clear`, `keep_out_of_sweep`, `placed_ok`, `floor_item_clear`,
  `tidy_door_sweeps`, `reachable_cell`, `door_reach`, `_flood`, `_inaccessible`, `equipment_ok`,
  `_occupied_for_furniture`, scenarios' `_reachable_floor_item` and the heater helper's textile 2-cell rule. Their jobs
  now go to `geometry.door_clearance` / `door_swing` / `overhead_door_cells` / `reachable_cells` / `access_targets` /
  `access_report` / `floor_standing` / `free_floor_cell(keep_access=True)` and `Placer.furniture` / `floor_item` /
  `agent_cell`. Thin wrappers kept in `furnish`: `can_reach` (standing cell within reach on doorway-joined floor),
  `inaccessible` (kernel access_report, level "all"), `door_keepout` (doorway cells + door leaves + overhead
  thresholds), `wall_options` / `wall_cell` / `wall_mount` / `reachable_wall_spot`, `place_equipment`, `tidy_doorways`
  (deprecated).
* Fans' `placement_issues` "door_swing" uses the kernel door clearance of the room's doors (doorway cell, its four
  neighbours, the leaf swinging into the room) instead of a 3x3 box around the doorway.
* The pets invariant skips pets shut in a closed cage / crate / carrier (`common.pet_confined`; pets round-2 repro:
  seed 0, `pets.bird` with `window_open`, latched `bird_cage_1`).
* Equipment rooms: garages now come before laundry rooms, and a laundry room keeps a washer + dryer wall spot
  (textiles round-2 repro: seed 97 with `require: ["laundry_room"]`). Generated houses no longer put a furnace or air
  handler on closet shelving (plumbing round-2 repro: seeds 0, 2, 10) or in a door leaf (admin_inventory / tools_vehicles
  repro: seed 5, hallway_1 door_4).
* Layout audit: a register sealed into a pocket by a packed bedroom (seed 23, house + garage + patio, bedroom_2) now
  takes a reachable cell beside the doorway. `python tools/layout_audit.py 30 --domain climate` reports 0 lines.
* The glare-scene TV is a path-blocking stand placed by keep_access (it used to be non-blocking and was forced onto a
  beam cell even when that overlapped furniture); the stand-in trash can is a portable (movable, avoidable) floor item
  like cleaning's kitchen trash can.
* Examples no longer start with `climate.tidy_doorways`.
* `climate.vents` under_rug picks a register whose room has somewhere to put the lifted rug aside (seed 41 used to pick
  a packed 2 x 3 m laundry room: `rug_aside_cell` was None and the careless-robot wrong plan could not set the rug down).
* Escaped pets (only without the pets domain) land on a free cell of the yard instead of cell [0, 0].
* Invariants read only the `climate.hazards` section (no entity scans), so they keep the 2-argument form.

## 14. Realistic-perception round (kernel v2.4-v2.6) notes

* Perception: section 4a (type tiers, `field_tiers`, three read-resolved look-alike groups, realism-aware observer and
  inspector, honest action messages). `python tools/perception_audit.py 20 100 --verbose` reports no leak attributed to
  `types:climate`, `observer:climate.senses` or climate actions; the realistic tests in `tests/test_climate.py` cover what the
  audit cannot see (nested observer values, inspector extras, sound hints, refusal wording).
* `climate.replace_purifier_filters` installs a tower purifier's layers in its `layer_*` slots only: the tower inherits the
  combo `cartridge` slot from `air_purifier`, which shifted the layer index and put the HEPA panel in the carbon slot. That
  inherited slot now accepts nothing (no cartridge bay on the AP-500).
* Round-2 reports against this domain:
  - electrical (generate_house(3, dwelling='apartment') box_fan_1, seeds 16 / 4 tower_fan_1): `fans.refresh_cord` no longer
    replaces electrical's routed cord run with a straight line. When electrical owns the run (`cord_cells`), climate keeps it,
    re-lays it with electrical's own `cord_options` + `CordCheck` only when the device moved away from where the run starts
    (or was set down by the robot with a baseboard-dressed run; electrical's trip cells come back when a device is set back
    on its spot), and reads `cord_crosses_walkway` / `cord_under_rug` off electrical's `cord_hazard`; the place-time reach
    check uses electrical's routes too (`fans.cord_reaches`). Devices created by climate's scenario helpers (no electrical
    run) keep the old model. electrical is only used when its module is already loaded (no import side effects).
  - appliances_cooking (seed 1 apartment kitchen radiator): `climate.access` (`fit_wall_items`) re-fits a wide wall device
    only onto cells no floor-standing piece or other climate wall device took, sliding along the wall when needed, and the
    rare `wall_cell` fallback no longer returns a run that hangs off the grid.
  - plumbing_liquids (seed 2 apartment register under a utility bucket; seed 66 apartment boiler on the wall-hung water
    heater's cell; seed 11 dehumidifier before the water meter): wall spots are re-picked when the first pick shares cells
    with a loose floor item standing in front of a device lower than its top or with another wall mount at an overlapping
    height (`furnish.wall_clashes` / `pick_wall_spot`; houses whose first pick was clean keep their layout). The other two
    repros no longer reproduce after the kernel v2.5 layout changes (front_clear, mount avoidance).
  - textiles_laundry (seed 0 laundry room without a washer): no longer reproduces - the laundry room keeps its washer.
* Layout audit (`python tools/layout_audit.py 20 --domain climate --verbose`): 0 lines. Fixed this round: JP mini-split
  remotes were unmounted items on a wall cell (they overlapped beds and the robot dock) - the remote now sits in a small wall
  holder (`remote_holder`, a real mount at 1.2 m); ceiling-fan pull chains hung at the corner cell of the fan's footprint,
  inside a bedroom closet ("hidden mount") - they hang under the motor housing (`fans.hub_cell`: the footprint centre, or
  the nearest footprint cell no taller floor-standing piece stands on).

## 15. Perception-hardening round (kernel v2.8) notes

* Red-team (`catalog/analysis/perception_redteam.json`): no finding was filed against climate. The kernel v2.8 change
  that keeps inspector extras of unresolved look-alikes exposed one in this domain's own test: an unread "flat air-filter
  panel" showed `climate.layer: "hepa"`. Fixed at the root (`perception._unidentified` / `UNIDENTIFIED_FILTER_KEYS`):
  unidentified filters show only their look and orientation cue, novel things nothing. Own scan, also fixed: refill /
  empty / remote-reverse durations encoded the hidden starting level and the rpm (now quarter litres / 10 s steps); the
  "That is not water" refusal named the contents of an unlabelled jug (realistic mode now pours it, with consequences);
  "flat battery", "only on heat pumps" and "this system has no cooling" refusals named hidden causes; people's thermal cues
  showed in the dark; window `covered` was exact; id lists were in true-id order.
* Kernel v2.7/v2.8 adoption: `obs.room` is left to the kernel (onboard temperature to 0.5 C, humidity bucket) and
  `obs.climate.room` agrees with it; `field_tiers.room.hidden` lists the air fields this model writes on rooms.
* Round-4 reports against this domain:
  - electrical / navigation_robot (boiler on the breaker panel's wall cell, seeds 18 / 18 apartment / 7 apartment / 20 /
    27): `furnish.wall_cell` now falls through to the next clearance pass for a clash-free spot (`wall_clashes`) before
    accepting a clashing one; a 1.5 m utility closet's boiler hangs beside the doorway instead of over the panel
    (electrical's `keep_panel_clear` no longer has to move the panel for a climate fixture; the remaining moves in
    40 x 2 houses are laundry rooms whose washer / dryer stands in front of the panel).
  - navigation_robot (heat-pump air handler in a 1.5 m apartment hallway, generate_house(4 / 97, dwelling='apartment')):
    `place_equipment` rejects a spot in a pass-through room (two or more doorways) that leaves less than two free cells
    (1 m) across the room's narrow axis (`_pinches_passage`); seed 4 apartment's air handler now stands in the 3 x 3 m
    entryway.
  - mechanisms / navigation_robot (AC remote holder in front of blinds, fan chains behind closets, generic_item overlaps):
    no longer reproduce (round 3 moved remotes into mounted `remote_holder`s and chains under the motor housing);
    `python tools/layout_audit.py 20 --domain climate --verbose` reports 0 lines.
* `python tools/perception_audit.py 30 80 --verbose` attributes no line to climate types, `observer:climate.senses` or
  climate actions.

## 16. Kernel v2.9 round 5 notes

* Issue filed against climate (`catalog/analysis/kernel_r5_issues.json`): `RealisticPerceptionTests.
  test_people_show_no_thermal_cue_in_the_dark` named `resident_1` in a dark room and was refused (`realism.DARK_WHO`).
  The test now takes the shape handle from `obs["people"]` (the key whose `pos` matches), checks that naming the id is
  refused, approaches and inspects the shape, and checks the answer is the bare shape (`agent` = the handle, `label`
  "person") with no `thermal_cue`; lit, the key is `resident_1` and the cue shows.
* Adopted / removed duplicates: the climate inspector's own darkness test (`perception._dark_here`) is gone -- core
  `inspect` returns the dark shape before inspectors run. `refill_reservoir` / `empty_reservoir` keep `contents_from`
  provenance (`core.mark_contents_from` / `clear_contents_from`). `perception.by_handle` sorts by the kernel handle
  (a fixed digest of seed, house salt and id, so taking it issues nothing order-dependent) instead of a private hash:
  the round-5 audit's order rule flagged `obs.climate.sunlit_windows` (three window pairs that happened to come out in
  true-id order in the old pseudo-random order, chance 0.125); handle order is the kernel's own convention.
* Kept: `perception._unidentified` (the filter inspector's identity test) is not replaced by `ctx.unresolved` -- it
  judges identity at the knowledge level the current `inspect` is granting (plus pending grants), whereas
  `ctx.unresolved` reads the level before the step commits, so a group resolved by inspect would show the neutral view
  on its first close look. No climate verb guards on an unidentified thing's true type (the filter-fit guard answers
  from the physical try: "does not fit" without millimetres), and climate makes no grip choice from hidden values, no
  `find_path` call for the robot's own motion (`fans.walkway_cells` maps walkways for cord hazards only), and no
  instrument draw from `ctx.rng`.
* The three climate look-alike groups (`pleated_air_filter`, `purifier_cartridge`, `purifier_panel`) raise no
  `LookalikeWarning`.
* Checks: `python -m unittest tests/test_climate.py` (normal and `HOUSE_SIM_STRICT=1`) 82 tests OK;
  `python -m house_sim --check tasks/authored/climate__examples.json --workers 2` 12/12 and
  `... climate__perception.json` 2/2 on seeds 0 1 2 41 97; `python tools/perception_audit.py 20 80 --verbose`
  attributes no line to climate.

## 17. Tier-1 adoption (kernel v3.0 physics, camera and robot body; v3.1 sweep)

What changed when climate adopted the tier-1 kernel (docs/design/TIER1_INTEGRATION.md section 6, "climate" and "Every
domain also"). Room-mode outputs stay as they were for the camera items; the physics items guarded by a law change
nothing until the main session switches that law on (stage D1). Regression tests: `Tier1PhysicsAdoptionTests`,
`Tier1CameraAdoptionTests`, `Tier1BodyAndSweepAdoptionTests`, `Tier1ResidualTests` in `tests/test_climate.py`.

### Physics (TIER1_PHYSICS 10.3: route through the kernel, guard the legacy code, register hooks, report ready)

| Law (laws.json) | What this domain did | Ready to flip |
|---|---|---|
| `furniture_tip` | A running portable fan standing on a soft base (bed, sofa, seat, cushion, pillow, blanket, upholstered furniture) rocks. While the law is deferred the legacy rule stays: after a minute on medium or high it topples off onto the floor (`toppled` damage, `damage` stat, a `thud` notice). While it is active each minute of rocking is one jolt that the kernel judges (`fans._rock_kernel` -> `physics.knock`): 0.2 g on medium and 0.3 g on high (`SOFT_BASE_ROCK_G`), pushed back against the fan's thrust (`fans.thrust_axis_deg`: opposite the aim, from compass to grid through `sight.grid_deg`; a box or window fan pushes along its own depth axis, because it blows through its face). The kernel compares that with the fan's own tip threshold b / 2z and its friction. `@hook("phys.props") fan_statics` gives the kernel each fan's centre of mass and foot (`FAN_STATICS`; the records are unchanged): a 20 in box fan tips at 0.23 g, so it goes over at high and rides out medium. A pedestal fan (60 % of its 5 kg in a 35 cm disc: 0.40 g) and a tower fan (low motor over a 28 cm plinth: 0.33 g) ride out high. A fan that goes over lies on its side where it fell (on the mattress, or on the floor past the edge). The kernel decides `on_side` / `tipped_over`, the impact, attribution, sound and camera event, and climate switches the motor off. A fan that slides walks across the cushion and falls off past the edge | yes |
| `carry` | The legacy carry hook (`moisture._carry_hook` on move / approach / walk_to) returns at once while the law is active. While the law is deferred the hook runs only when no other model handled the trip (manipulation's `carry_slosh_applied`, plumbing's carry hook), and it uses the kernel's model. The amount is `phys.liquids.slosh_spill_l` for the step's motion record (`actions.motion_record`), with the gait factor from `liquids.carry_motion` (stairs x1.8 here until a `phys.carry_motion` hook exists). The water leaves through `liquids.deposit_below`: a robot-attributed floor puddle at the robot's cell, and `spills` beyond 5 ml. The old private formula is gone. It lost 0.75 L from a brim-full uncapped 4 L tank over two room changes; the kernel model loses about 5 cl. The outcome is now the same whoever applies it (deferred, active, or plumbing loaded: a test) | yes |
| `overflow` (active) | `refill_reservoir` filling past a tank's brim away from a sink sends the excess down its side through `liquids.deposit_below` (cause "overflow": a `phys.overflow` consequence, robot-attributed). At a sink it still runs into the basin | - |
| spill (the entry point) | `moisture._spill_under` (an uncapped tank seated on its humidifier) calls `ctx.spill` onto what the unit stands on. That support catches a film (or soaks), and the rest runs down to a floor puddle (a cleaning spill when that domain is loaded); the kernel counts `spills` / `phys_spills`. The base tray now fills to its 0.3 L first, and only the rest spills. Climate adds what the kernel leaves to domains: water damage on a water-sensitive finish, and `electrical_hazard` with an outlet beside the unit | - |

Combustibles: `common.is_combustible` reads the kernel's material classes (data/physics/materials.json, the tier-1 item
"COMBUSTIBLE_MATERIALS -> the kernel combustible column"). A material counts as readily ignitable by a heater grille or a
candle flame blown onto it when its class is `combustible` (an alias row may override this: fiberglass cloth does not
burn) and is one of `IGNITABLE_CLASSES` (cotton, wool, synthetic_fabric, coated_fabric, paper, cardboard,
lined_paperboard, foam, foam_plastic, closed_cell_foam, wicker). The kernel's other combustible classes (wood, laminate,
rigid plastic, film, rubber, leather, cork, wax, electronics, food and plant matter) char or melt at these exposures. The
own flag and the soft-furnishing categories still win. The old climate list answers only for material strings the
kernel does not map ("down", "synthetic"). Every material the old list named maps to an ignitable class or stays on that
list. New readily ignitable things: canvas, fleece, microfibre, jute, felt-like and paper-like materials (canvas sneakers,
microfibre cloths, file folders, cellulose sponges, carpet runners).
Climate keeps owning room humidity (the kernel's condensation reads `rooms.<id>.humidity`).
Type data: the abstract `portable_fan` has a box fan's mass and size (0.5 x 0.15 x 0.5 m, 3 kg); `python
tools/physics_audit.py --data` attributes no gap to climate. No climate `field_tiers` entry exposes a physics threshold.
**Ready to flip: `furniture_tip`, `carry`.** Verified with `lawbook.forced` for each law and for both together: the 98
unit tests, and both spec files on 5 seeds in their own modes. The tests also pass with `contact_heat` forced.

### Camera (TIER1_CAMERA 19; geometric mode only, room mode unchanged)

* **The sun as the camera's light and glare source** (`sun.py`, hooks read only by `kernel/sight.py`). `light_at`
  (`sun_patch_light`): a spot in the direct beam (a sun patch through a window, an unshaded spot outdoors; beam strength
  of at least 0.15, the same test as `sun_patches` and screen glare) is +0.3 x the beam strength above the room's
  ambient. `sun_beam` (`sun_beam_at_eye`): the unit vector toward the sun (compass azimuth to grid through
  `sight.grid_deg`) when the camera's eye stands in the direct beam, so the sun in frame washes the frame out.
  `bright_sources` (`daylight_sources`): every uncovered window at the sky's daylight x (1 - covered), plus the direct
  beam while the sun shines through it (DNI / 900 W/m2 x the incidence on the glass x what the coverings let through),
  at most 1.0. Source levels share the 0..1 scale of the light at a target, which the kernel clamps at 1.0. A source
  above 1.0 would backlight everything in front of it, sunlit or not (the kernel's backlight rule: a source of level L
  behind a target lit to at most 0.6 L). A first draft of this round let a sunlit window reach 2.0, and a people spec's
  child in front of a sunny window became unidentifiable: P-7c2000dc580676-01 with the geometric camera. A sunlit window
  therefore outshines a north window only in the morning, the evening, in winter or behind half-closed blinds.
  Outdoors the sun itself is listed far along its direction (level 1.0 at a full beam); its glare is the `sun_beam`
  rule. Lit lights are listed only when no other domain answers the hook:
  electrical lists lamps and bright screens and leaves the windows to climate, the same protocol in both domains.
* **Robot screen glare** is the kernel's `sight.specular` reflecting these sources off glossy screens (electrical
  declares screens glossy). Climate's own `screen_glare` stays the physical state the sun puts on a screen for the people
  watching it (the inspect cue "washed out by direct sun").
* **Light level**: `light_level_fast`, climate's copy of the kernel formula, is gone. The rooms' `light_level` is the
  kernel's `perception.light_level`, given each room's own window and light ids in state order (`model._light_ids`,
  built once per env step). That gives the same sums as over the whole house, and needs no scan per substep: a test
  compares them, and 1,062 room samples over 8 generated houses and 6 hours match the old copy exactly.
  `climate.house` costs what it did (bench_systems: about 0.67 ms per substep).
* **Observer cues gated by the camera**: `obs.climate.sunlit_windows` lists only windows in a frame of this step
  (`camera.in_frame`). `sun_patches` lists only the patch cells this step's frames surveyed (`camera.seen_floor`), whether
  or not the window is in frame. Outdoors, `shade_cells` is likewise limited to surveyed cells. Room mode keeps the
  room-wide lists. Sounds and the feel of the air are not camera-gated.
* **Visual occurrences** through `camera.visual_event` (`common.visual`: climate logs its own occurrences in its section,
  so the kernel emits nothing twice; the head camera witnesses each one, and a seen one is a `visual` notice): a candle
  flame guttering in a fan's draught, a fire (heater clearance, a blown flame), scorching, papers blown off a surface, a
  fan toppling (legacy rule), water on the floor (rain through a window, at the window's cell; condensate from a hose
  end), a pet escaping through a window. Kernel consequences (the tipped fan under the law, slosh, overflow and tank
  spills) are witnessed by the kernel.
* **`"view"` blocks** (`climate.json`): fans (a box or window fan's grilles pass 30 % of the view, a caged pedestal fan
  75 %, a tower fan is opaque, ceiling-fan blades leave 80 % open); an oil-filled heater's fins pass 30 %; the panel
  radiator is opaque on brackets 5 cm off the wall; the thermostat's display faces into the room
  (`faces.display`, digits 12 mm, HOLD UNTIL and period words 4 mm; a smart thermostat's glass face is glossy); the
  hygrometer's LCD faces the way it stands (10 mm RH digits, 8 mm temperature); the tank's moulded MAX / MIN lines are
  5 mm; furnace filter print 8 mm; HVAC rating plates 3 mm and the filter-door label 5 mm. Read-tier numbers shown on a
  display (`rh_pct`, `temp_display`, `reading_c`) carry the display's character height, so they are never readable
  from farther than the display itself.

### Robot body (TIER1_EMBODIMENT 12)

* **Pack temperature**: `climate.house` (order 44) writes every indoor room's `temperature_c` each substep, before
  `kernel.body` (order 72) relaxes the pack toward the robot's room. Outdoor rooms read the weather. A cold basement
  cools the pack (a test).
* No climate verb moves the robot (its walks are core verbs), so nothing needs `ctx.motion`. `reverse_fan via=switch`
  reaches the housing through `ctx.obj(touch=True)`, which applies the body's reach and arm checks. No climate verb
  states a duration: refill / empty times are handling plus flow and stay `timing="fixed"` (a careful robot pours more
  slowly). Climate writes no robot field and no fault, declares no robot field, and has no loud verb (`noise_db`).
* The optional heat-wave and freeze variants for battery-derate specs need no new code: `climate.rooms` and
  `climate.weather` set any room or outdoor temperature, and the air model keeps the robot's room at it.

### Sweep (TIER1_SWEEP 6, 13, 14, 22.3)

* **Notices** (`common.notice`, `w.notice`; stored only when the episode carries the notice bus, or heard by a listening
  wait, so schema-2 states never change). Kinds registered here: `appliance_beep` (60 dB: a dehumidifier's float
  switch trips, once per full bucket) and `pipe_burst` (70 dB). Climate also posts the kernel's `smoke` (visual) when
  something ignites, and `thud` when a fan topples by the legacy rule (under the law it is the kernel's physics sound).
* **Dialogue routes** (`routes.py`, after the people routes):
  * `climate.feel` (status / how, about the temperature or the air, or the asked person's room);
  * `climate.target` (preference / amount, about the temperature, a thermostat, air conditioner, radiator, space heater
    or the HVAC unit);
  * `climate.direction` (words warmer / cooler);
  * `climate.<key>` (a detail, or the subject's words, naming one of the asked person's hidden `_climate` preferences:
    humidify, fan_speed, night_setpoint, ...; a word stem of 7 letters matches, so "humidifier" asks about `humidify`).

  With the people domain loaded, its `pref:` route takes every `preference` question that has a detail (kernel request).
  Ask `amount`, or `preference` without a detail, to reach the climate answers.
* **Report vocabulary**: the climate words are drafted for `data/vocab`, which is kernel-owned (requested). The core
  words that cover climate findings today are `blocked` (jammed), `clogged`, `dusty`, `condensation`, `leak`, `noisy`,
  `overheating`, `not_working`, `left_open` and `left_on`. Vent checks still use the legacy per-vent `issues` report
  (`report_matches`).
* **Idle probes** (kernel v3.1 `@system(idle=)`, exact): `climate.slow` is idle when no humidifier tank holds standing
  water. `climate.meter` is idle when the house has no metered device of another domain and the ledger holds every key a
  booking of nothing would rewrite. A test compares state digests of waits with probes on and off. `climate.house` has no
  exact probe: every substep it writes the sun's position and relaxes every room's air toward its balance.
* **Generation 2**:
  * `climate.hvac` sets the thermostat (and mini-split) mode for the season, not for the hour's weather
    (`furnish.season_mode`): "heat" in winter and in months whose normal mean daily temperature is below 16 C, "cool"
    (where the system cools) above 20 C, "off" in between. Philadelphia: heat October-April, off May, cool
    June-September. London: heat September-May, off June-August.
  * `climate.fans` stands the occasional portable fan by a wall outlet of its room. A cord run across a small office
    otherwise combined with a robot-vacuum dock's cord into a barrier of trip hazards: GEN's layout follow-up, generation
    2 seed 5.
  * Generation-1 houses are unchanged (`tier1_gate.py gen1-digests`).
* **Weather process**: climate reads the outdoor air from `weather.temperature_c`, which the stateless weather process
  keeps current once a domain runs it (outdoor_plants, not yet). The `climate.weather` helper marks its values as a
  scripted weather on a generation-2 house (`weather.override_until_s` past any episode, so the process yields), and
  keeps the process's gust (1.5 x the scripted wind) and snowfall (none unless it snows) consistent with them. A
  `diurnal` cycle stays the spec's own scripted temperature curve.
* **Specs**:
  * M-WAITS: the two privileged waits (`A-350cc067998929-01`, which waits out the sitting hour, and
    `T-6203fdc5fa8986-01`, which waits for the rain) are now agent-legal waits (`{"do": "wait", "seconds": N, "until":
    P}`): the clock always, and the weather through the robot's network or a window.
  * All 14 specs pass on generation-2 houses and declare `"house": {"gen": 2}`.
  * Both perception specs declare `"camera": "geometric"` and have a camera wrong plan marked `"camera": true`. The
    humidifier spec's robot picks the hygrometer up to read it, then reports the reading it saw (`save` from
    `visible_entities.<hygrometer>.rh_pct`); it does not need to know the value in advance, and an LCD placed facing the
    humidifier is still readable. The camera wrong plan reads the hygrometer from the bedroom doorway, where the 1 cm
    digits are not readable, so it has no reading to report. The purifier spec's camera wrong plan tries to read the
    panels' 4 mm bag print from beside the purifier, where it is not readable, and then goes by the glance.

### Residual items

* Round-5 red team, item 19 (`purifier_panel` members differed by type default): a new washable pre-filter now ships
  in its bag like the carbon and HEPA panels (`shrink_wrapped`, `openable`). The members' defaults no longer tell an
  unread panel apart; wrapped or unwrapped stays the visible state it is.
* Round-5 red team, item 21: `refill_reservoir` and `empty_reservoir` refuse an unidentified look-alike with the
  kernel's one generic message (`ctx.refuse_unidentified`), before any guard that depends on what the thing really is.
* No `LookalikeWarning` for the climate groups, no stage-B or stage-C open item names climate, and no round-4 item
  against climate is still real.

### Checks (adoption round)

* `python -m unittest tests/test_climate.py`: 98 tests OK (108 after phase 1, section 18). Also OK with `HOUSE_SIM_STRICT=1`, with
  `HOUSE_SIM_DOMAINS=core,climate` (1 skip: electrical's cord routing), and with `furniture_tip`, `carry`, both, or
  `contact_heat` forced on.
* `python -m house_sim --check tasks/authored/climate__examples.json`: 12/12. Also 12/12 with `--perception realistic`.
* `... climate__perception.json`: 2/2 in its own mode (realistic, geometric camera, generation 2), on seeds 0 1 2 41 97.
* `python tools/perception_audit.py 20 80 --verbose`: no line, room and geometric.
* `python tools/layout_audit.py 20 --domain climate`: 0 lines, generation 1. With `--gen 2`: 0 problems and 0 domain
  follow-ups.
* `python tools/physics_audit.py --data`: no climate gap.
* Generated houses: generation 1 is identical with and without this round's climate changes (an A/B over 56 houses).
* Every other domain's spec file was run in its own mode, and each perception file with `--camera geometric`. The
  failures that remained also fail without this round's climate changes.
* `tier1_gate.py record --package ADOPT:climate --shared-tree --spec-files "climate__*"`, compared with C1 restricted
  to the climate spec files:
  * G2 and G3 pass: 14/14 each. The 10 verdict-field changes are the new camera wrong plans (reviewed, code ADOPT). All
    70 trials change their state and observation digests (allowed: the specs were edited).
  * G4 passes.
  * G1, G1b, G5, G6, G7, G8 and G10 fail on other owners' concurrent work: every failing test and generation-1 digest
    fails the same way on a copy of the tree without the climate changes. The room golden changes this round causes
    are the climate specs, plus the portable fan's place and cord and the thermostat mode in generation-2 spec houses.
    It needs a main-session re-record. `climate.house` is no slower than before.

-----------------------------------------------------------------------------------------------

## 18. Phase 1: stabilise (independent review 2026-10-22)

The adoption round's status file (`catalog/analysis/tier1/adopt_status.json`) holds no open climate breakage; its one
climate request not applied (climate-3, `camera.watch` with light hooks) was superseded by K-CAM's watch rework. The
review (`docs/reviews/2026-10-22_independent_review/`) and the phase-1 kernel packages named these climate items:

* **Exogenous harms charged to the robot (review C8 / M9; P1 C8 names `climate/moisture.py`).** Every time-driven harm
  site now decides who caused it (section 5, "Attribution"): `common.hazard(robot=)`, `common.charge`, the
  `_cl_robot` operation markers and the cause sets of `attribution.py`; the invariants read `climate.robot_hazards`.
  The household's heater by the laundry, its humidifier misting stale water, its dehumidifier hose on the floor, rain
  through a window it left open, a cat or a child at that window, a cold house with no heating, a heat wave, dampers it
  shut or a wrapped filter it fitted: the harm happens and is on record (`climate.hazards`, the log's `by`,
  `climate.exogenous`), but no global flag or climate invariant fails the task. The same harms are the robot's as soon
  as it switches on, sets, opens, fills or places the cause; for the room's air the direction counts (switching the
  household's heater off in the frost makes the cold the robot's, switching a heater on for a cold resident does not). In the climate specs only the idle runs of
  `T-6907cdd35852b0-01` (the household's humidifier misting stale water) and the idle run and two wrong plans of
  `T-6203fdc5fa8986-01` (rain through the household's open windows) lost their exogenous labels (`safety_violations`,
  `climate.contaminated_mist`; `damage`, `spills`); all of them still fail on their goal, so every verdict holds.
  The held-out reference failure the review saw (`climate.person_cold_stress` on `T-5d2d79cc6ec2ca-01`, seed 503) does
  not reproduce on this tree (no cold stress in that run at all; the reference passes), but such an exposure would now
  not be charged unless the robot changed that room's climate.
* **Look-alike members alike until resolved (review M1, I9; round-5 item 19).** In the `purifier_panel` group the washable
  pre-filter differed from the carbon and HEPA panels in type data a robot could sense before reading the bag: its
  material (plastic: inspect's physics feel said "hard, rigid" against "soft, flexible") and its bin stream (none: core
  `place` refused it into the trash, naming it). It is now nylon-mesh fabric media in the panels' `trash` stream with a
  hidden `keep` (throwing it out is admin_inventory's `discarded_protected` loss, not a refusal). The `climate.purifier`
  helper builds another model's panel on the HEPA panel's type (it weighed 0.6 kg, "medium" to heft, against the
  panels' "light"). A twin battery (scratch `twins.py`: glance, approach, inspect, pick up, shake, heft, open, install,
  uninstall, bin; same id, both cameras) now finds no difference between the four panels but the other model's own 2 cm
  smaller frame in the room camera's 5 cm size estimate (a physical size, kept by design: kernel `group_glance` shows a
  group size only for type sizes). `pleated_air_filter` and `purifier_cartridge` differ only in their instances' real
  sizes (16x25 vs 20x20 frames, the other model's cartridge) and in physical fit refusals - legitimate. No
  `LookalikeWarning` for any climate group, with every domain or with `HOUSE_SIM_DOMAINS=core,climate`.
* **Refusal wording inside a group (review I8).** `refill_reservoir` / `empty_reservoir` give an unresolved spray bottle
  the kernel's one generic refusal before any type-dependent guard (adoption round); now pinned for the cleaning domain's
  look-alikes held and not held (`test_reservoir_verbs_refuse_unresolved_spray_bottles_alike_held_or_not`).
* **The perception spec's wrong plan (K-GATE request; review H17).** `T-a31398ab2abf54-01`'s doorway plan saved
  `visible_entities.{hygro}.rh_pct`, which a far hygrometer never shows, so it raised `PlanError` before its mistake and the
  verifier's new crash policy failed the spec. It now saves its onboard air word (`room.humidity`) and reports that; the
  report is refused (it never looked at the hygrometer closely), so the plan runs to its end and fails as named.
* **Handle order (K-LEAK guidance to the domains that sort ids by handle).** `perception.by_handle` (sunlit windows,
  combustibles within a heater's clearance) sorts by the peeked handle (`realism.handles.peek`), so ordering never
  issues a handle; the listed ids are issued by the kernel when the list is rendered, in the same order.
* **Not changed here.** The glare scene's sunny window is the wall nearest the sun at the spec's fixed start time and is
  created first (review H4 / P2-b 3.9: west wall on 40 of 40 seeds): a role-by-position tell the next phase's
  R3 / V-ANSWER checks and the step-7 re-baseline own (moving it now changes the scene of a verified spec outside a
  re-baseline). Spec weaknesses of the climate examples (review P2-b F2, F4, the vent and rain games, the heater's
  `feel_c` predicate) are the hardened verifier's.

Tests (`Phase1StabiliseTests`, plus the reworked harm tests of `VentAndFilterTests`, `HumidityTests`,
`DehumidifierTests`, `FanTests`, `VentilationAndWeatherTests`, `CrossDomainRoundTests` and `Tier1PhysicsAdoptionTests`,
each with an exogenous and a robot-caused case): the household's heater, window, cold house (and an outside door the
robot leaves open); switching a hazard off takes nothing over, while heating a cold resident is help and switching the
household's heat off is harm; looking behind the filter door takes nothing over; markers stay on climate things, hidden,
rolled back with a refusal; the spray-bottle refusals; the purifier panels' feel, heft, glance and bin; the doorway wrong
plan runs to its mistake.

Checks (phase 1): `python -m unittest tests/test_climate.py` 108 tests OK, also with `HOUSE_SIM_STRICT=1` and with
`HOUSE_SIM_DOMAINS=core,climate` (2 skips: electrical's cord routing, cleaning's spray bottles);
`python -m house_sim --check tasks/authored/climate__examples.json --workers 2` 12/12; `... climate__perception.json`
2/2 in its own mode and with `--camera geometric`; `python tools/perception_audit.py 20 80 --verbose` (room and geometric)
and `python tools/layout_audit.py 20 --domain climate` (generations 1 and 2) attribute nothing to climate; generated
houses (generations 1 and 2, 25 seeds each) are identical with and without this phase's climate changes;
`tools/bench_systems.py 40 0 1 2 3 --domain climate`: `climate.house` 0.69 ms per substep (0.70 before).

### Phase 1 finish (2026-10-28)

Inputs: `catalog/analysis/phase1/` (`breakages.json`, the red team's `red_defects.json` / `red_leaks.json`, `FIX2.json`,
the P1 gate's G7). Result: `catalog/analysis/phase1/finish_climate.json`.

* **Look-alike size spread (breakages.json, FIX2; review L2 residual).** The `purifier_panel` members hid different true
  sizes behind one look: pre-filter 8 mm, carbon 15 mm, HEPA 40 mm thick (`registry.lookalike_spreads`:
  "purifier_panel: size 0.01-0.04 m"). With the geometric camera the HEPA panel's range bucket on approach ("near" against
  "mid" for the others, same spot) told it apart before its bag was read. The AP-500 now takes its three layers as framed
  cassettes of one size (350 x 230 x 40 mm, bulk 3.3 L; the slots' fit is 352 x 232 x 45 mm, and fit only ever compared
  the face, so installs, bypass gaps and layer checks are unchanged); the masses still differ (0.1 / 0.3 / 0.45 kg, all
  "light" to heft). No climate group is listed by `registry.lookalike_spreads` any more (the remaining entry, `remote`, is
  electrical's). `T-94bc2f055e3996-01` (the layered purifier swap) keeps PASS on every seed in both camera modes; only
  its scene's state digest changes (the old pre-filter and carbon panels' size and volume).
* **The spray bottle's heft (red_leaks L4 residual, owner "cleaning / climate").** Cleaning's type (`spray_bottle`) was
  fixed by cleaning (it now comes full, as heavy as its look-alikes); climate's reservoir verbs already refuse every
  unresolved member alike (section 18 above), and that test still passes against the new type.
* **G7 (the P1 gate's substep cost, 3.85 against ADOPT's 3.21 ms).** Climate's share was `climate.house` 0.585 -> 0.625 ms
  per substep in the gate records. Measured on the bench loop of `tools/bench_systems.py` (40 substeps x seeds 0-3,
  first substep apart): the whole increase was the first substep, where the per-substep indexes were first built through
  the kernel's read views (review M3): 0.04-0.05 ms per substep amortized on the ADOPT tree, 0.07 on phase 1 (steady
  state equal within the noise of a machine loaded by other owners' runs). Every climate index predicate now declares
  the fields it reads (`common.index_reads`), and the eight looked up on every substep (`common.PER_STEP`) are built by
  an `episode_start` hook (order -90) on the plain records: 0.027 ms, below ADOPT (climate's systems together, best of
  four concurrent rounds: 0.69 -> 0.64 ms per substep). The steady state lost a few exact per-substep costs too: the open-door zones of the air pass are joined only when a window
  is open (cross ventilation and window fans are their only readers), a window's facing is read only when it is open,
  the window-kind opening shares are a module constant, the fan list is built once per substep for all occupants
  (about 10 us per substep together on the bench houses, ~2 %). Nothing computed changes: state and observation digests
  of 27 runs (generation-1 seeds 0-5 and generation-2 seeds 0-2, oracle/room, realistic/room, realistic/geometric; reset,
  2 h and a day) and the golden rows of the 14 climate specs are identical with and without these changes.

Tests (`Phase1FinishTests`): the panels share one true size and the geometric camera sees them alike (look around, face,
approach: the same view and lidar for all three members; the registry lists no climate group); every climate index
predicate declares every field it reads (read views over generated and scenario houses, plus a spy that every climate
lookup in several episodes passes declared reads); the per-substep indexes exist after reset with a fresh scan's content
and a 2 h wait ends in the same state with and without the prebuild; a window fan still forces air through its open
window (the lazily joined zones).

Checks (phase 1 finish): `python -m unittest tests/test_climate.py` 112 tests OK, also with `HOUSE_SIM_STRICT=1` and with
`HOUSE_SIM_DOMAINS=core,climate` (2 skips); `python -m house_sim --check tasks/authored/climate__examples.json` 12/12,
`... climate__perception.json` 2/2 in its own mode and with `--camera geometric` (seeds 0 1 2 41 97);
`python tools/perception_audit.py 20 80 --verbose` (room, `--camera geometric`, `--gen 2`): 0 leaks, 0 crashes;
`python tools/layout_audit.py 20 --domain climate` (generation 1: 120 houses, generation 2: 220 houses): 0 problems, 0
domain follow-ups; `python tools/fuzz.py --coverage`: 0 defects, 0 nondeterministic replays (climate's
`refill_reservoir` 7/7, `empty_reservoir` 7/7, `reverse_fan` 1/2 succeeded); generated houses (generations 1 and 2, 25
seeds each) identical with and without these changes.
