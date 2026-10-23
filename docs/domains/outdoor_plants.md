# Domain: outdoor_plants

Plants (indoor and outdoor), soil and water, gardens and lawns, yard debris, weather effects on the
outdoors, patio furniture/cushions/umbrellas/covers, grill covers and fire pits, firewood, compost,
pools (skimming, baskets, covers, toys, flotation), gutters/drains, snow and ice, fences, roofs seen
from the ground, outdoor decorations and cut flowers.

Code: `house_sim/systems/outdoor_plants/` (package). Types: `house_sim/data/types/outdoor_plants.json`.
Tests: `tests/test_outdoor_plants.py`. Example specs: `tasks/authored/outdoor_plants__examples.json` (12) and
`tasks/authored/outdoor_plants__perception.json` (3 realistic-perception specs, two of them with the geometric camera).
Tier-1 adoption round (kernel v3.0 physics, camera and body, v3.1 sweep): section 0b.

| module | contents |
|---|---|
| `common.py` | constants, realistic-perception helpers (`realistic`, `seen_keys`, `drop_keys`, `mass_feel`, `temp_feel`, `fill_word`, `unresolved`, `require_identified`, `gone_refs`, `approx`, `amount_word`, `water_word`, `perceived_liters`; `find_held`/`nearby_entity` take `by_identity`), shared helpers (`set_status`, cached indexes `type_index`/`find_types`/`kids_of_type`/`static_ids`/`outdoor_map`, `reach_with_tool`, weather/wind helpers, `add_spill` (the kernel's spill), `drop_cell`, `pinfo`, house-wall geometry), tier-1 helpers (`visual` visual events, `notice` notices, `seen_cells` the geometric camera's surveyed floor, `soil_hands` / `soil_from` / `gloved` hand soil through the body), harm attribution (`harm`, `robot_caused`, `mark_robot` / `drop_robot`, `ROBOT_MARK`; section 0c) |
| `species.py` | growing media table (saturation / field capacity / wilting point), ~75 species profiles |
| `weather.py` | weather events, the generation-2 weather process and its forecasts (`process_value`, `process_forecast`, `forecast_for`), rain/thunder notices, forecast observer, wind (loose things through the kernel's `environment.wind_effect`; umbrellas, covers, plant covers and leaf piles this domain's; `rest_cell`: where a blown thing comes to rest), rain/snow wetting, drying, mildew, drip trails, `phys.exposed`, outdoor haze for the geometric camera |
| `plants.py` | container water balance, light/heat zones, plant status, health/pests/lean, inspector, answerer, one-time derivation |
| `garden.py` | beds, digging/planting/filling/firming, mulch grids, sowing/germination/thinning/hardening, weeds, supports, covers, produce |
| `lawn.py` | 1 m patch grid: height, moisture, leaves, clippings, seed, amendments, aeration, edges, emitters, mowing |
| `yard.py` | paved surfaces (leaves/snow/ice/de-icers), gutters, downspouts, drain grates, downed lines, fences, roofs, decorations |
| `patio.py` | covers, umbrellas, cushions, fire pit ash, firewood stacks, grill siting, propane invariant |
| `pool.py` | pool debris, skimming zones, skimmer basket, toys/flotation, pool chemical invariant |
| `compost.py` | compost pile model (C:N, moisture, O2, temperature, maturity, contaminants, worms), smells observer, realistic-only compost inspector and turning feel |
| `verbs.py` | the 30 domain actions |
| `furnish.py` | generation furnishers (yard, houseplants, outdoor items, finish), the shared floor placement (`floor_place`/`floor_spot`, hidden-mount avoidance) and the serviceable-light guard (`guarded`), `fence_extent` (a fence record's box is its yard) |
| `season.py` | generation 2: the season ground state (snow and ice from the process's last week, leaf litter, pool season, garden stage, frost) and the porch steps of the exterior stairs |
| `helpers.py` | scenario helpers |
| `skills.py` | reference skills |
| `routes.py` | dialogue routes of canonical `ask` questions to the `plants:` answerer |

## 0b. Tier-1 adoption (kernel v3.0 physics, camera and body; v3.1 sweep)

What changed in the adoption round (docs/design/TIER1_INTEGRATION.md section 6, "outdoor_plants" and "Every domain
also"), and how this domain now meets the kernel's physics, cameras, body and timeline.

**Physics (TIER1_PHYSICS 10.3: route through the kernel, guard the legacy code, register hooks, report ready).**

| Law (laws.json) | What this domain did | Ready to flip |
|---|---|---|
| `wind` | `weather._object_wind` (loose objects) returns at once while the law is active: the kernel's environment system moves loose outdoor items then, with the wind at the item (0.4 of the 10 m mean, 0.6 of the gust in a yard), the sustained wind sliding and gusts lifting sheets and tipping tall things. Since phase 1 the deferred copy is the kernel's own model too (`environment.wind_effect` at `object_wind_mps`), plus this domain's rest rule (section 0c), so the flip changes nothing for loose things. Umbrella canopies (`umbrella_tip_check`: canopy moment against base, ballast and table; the 10 m gust calibration of "close an umbrella above about 9 m/s" kept), covers laid over furniture, plant covers over beds and leaf piles stay this domain's at any law state: the kernel models none of them (its record of a deployed cover is the folded bundle). Every drag force uses the kernel's air density (`C.RHO_AIR = environment.RHO_AIR`, 1.2 kg/m3, was 1.225). `secure_outdoor method=weight` writes no hold-down rating while the law is active (the kernel counts the ballast's own mass resting on the item; this domain's copy counted a rating). Scripted `wind` / `storm` / `start_rain` events change `state.weather`, which the kernel's system reads: they feed the kernel's laws without a bridge. | yes (kernel calibration request below: a seat cushion never lifts under the kernel's sheet-only lift model) |
| `rain` | `precip_effects` no longer wets foreign or own objects with rain while the law is active (the kernel's `rain_wet`: surfaces fully wet within 120 s, absorbent bodies soak, paper soaked to 0.3 is water-damaged, `phys.wetted`); this domain keeps the ground surfaces (wetness, snow and ice of outdoor floors, walkways and steps: kernel-excluded ground layers), snow on objects (the kernel has no snow), the rain totals that feed soil water, cover pooling, and its drying bookkeeping (`_mark_kernel_wet`: an own-drying item the kernel wetted gets `_wet_mine` / `drying_owner`, anything else outdoors `_wx_wet`). The paper/electronics "ruined" rule runs only for this domain's copy. `@hook("phys.exposed")` keeps plants and the soil-water holders (pots, saucers, pebble and seed trays, beds, compost bins, the pool, the fire pit, planting holes, lawns) out of the kernel's environment law: their rain is this domain's soil-water balance (the kernel would pour it into a pot as free liquid). | yes |
| `slope` | no copy of the law here (the lawn's per-patch slopes only make a pushed mower slide, a robot fall). The wheelbarrow rests on its legs: `chocked: true` (the kernel's roll rule leaves chocked things); the garden cart rolls on a sloped paved floor as the kernel says. | yes |

Verified with `lawbook.forced` (each law alone and all three together): the 128 unit tests and both spec files (15
specs x 5 seeds, own modes, generation 2) pass. Spills: `C.add_spill` (an overflowing saucer, water dripping off a
rain-soaked thing carried indoors, a saucer tipped by lifting a heavy pot) is the kernel's `physics.spill` by the
robot (droplets below 5 ml; onto a piece it and the supports under it catch a film; cleaning's spill entity through
`phys.spill_sink`); a wooden piece keeps this domain's water mark. Pot drainage stays immediate (within the watering
action): a `drip_start` process would lose the robot's attribution, because the kernel's drip has no `by` and soil
water is no structural change the settle marks (kernel request). `python tools/physics_audit.py --data` attributes
two bulk-density notes to this domain, both true: an empty open paper leaf bag (0.25 kg in its filled 0.42 x 0.3 x 0.9 m
box, 2 kg/m3) and the plastic contractor bag (1 kg/m3); their fill mass is added as they fill. The PHYS-INT request
"decide gust-driven sliding with outdoor_plants' wind adoption": keep the kernel's sustained-only sliding (its
calibration against hourly generated weather; this domain's legacy model, which slid things with the full 10 m gust,
over-predicted), so the two pinned kernel tests stand.

**Camera (geometric mode; room mode outputs unchanged).**

* `"view"` blocks: hedges pass 30 % of the view (dense clipped foliage; the large-plant default is 0.5), a tree's
  footprint is its opaque trunk, compost bins, tumblers, worm bins, deck boxes and the fire pit open at the top, a
  gutter's trough opens to the sky with the face `trough` up, a row label's marker writing is 8 mm.
* Gutters: from the ground the inspector still reports what shows over the lip; with the trough face in view (an eye
  above the gutter: a ladder) it reports the trough itself (`yard.gutter_seen_from_above`).
* Roof faces: the yard's sight line is the field `sightline` (clear | partial | blocked), no longer `view`, the camera's
  name for per-entity view properties (CAM-RENDER request; since phase 1 generation-1 houses carry it too: the
  `gen1_records` shim is gone, adoption request outdoor_plants-5; `yard.roof_sightline` still reads an old saved
  state's `view` word).
* Surveys see only what the cameras covered: the lawn inspector's `issues_seen` / `seen_patches` and the fence
  inspector's panels need floor cells the camera log surveyed (`C.seen_cells`: camera_log.seen_cells plus this step's
  frames); the `outdoor_plants.survey` skill looks all round with the low row (`look target=around rows=2`) at each
  spot first.
* Small things in tall grass: a thing under 15 cm and no longer than a 1 m lawn patch lying on a lawn patch whose grass
  is taller than it is hidden from a robot more than 1.5 m away (`@hook("visible_veto") lawn.hidden_in_grass`,
  geometric only); fixtures, path-blocking and mounted things never are (phase 1: a scenario helper's 3 m garden bed,
  5 cm tall, was hidden).
* Fences: a fence record's box is the yard it runs round (`furnish.fence_extent`, a ground layer: it occludes nothing
  and takes no floor cells), so the geometric camera's close-look check passes from anywhere in the yard; the fence
  inspector still reports only panels within 4 m that the camera's frames covered (phase 1: the 1 m stub in the corner
  was "too far to make out the details" from most of the yard).
* Leaf undersides: `inspect where=closely` shows underside pests once the wrist camera has looked closely at the plant
  (`peek where=closely`, camera_log.peeked); the kernel's `inspect where=under` is a look beneath the pot.
* Visual events (`C.visual` = `camera.visual_event`, the event log identical to before): covers blown across the yard,
  an umbrella whipped open, tipped or collapsing down its pole, a plant cover blown off, hot ash flaring in the wind,
  rain ruining something; a loose thing the wind moves is the kernel's `phys.blown` consequence (its own visual
  event).
* Haze: falling rain or snow (or a fog condition) gives exposed outdoor rooms a `fog` level for the camera's `k_haze`
  (`fog = sigma / 0.3`, sigma = 3.912 / visibility; assumed visibilities: about 13 km in 1 mm/h rain, 3 km at 10 mm/h,
  1.3 km in 1 cm/h snow, 300 m in fog), written only in geometric mode and only where this domain set it.
* No observer of this domain is a visual cue to gate: the forecast is the weather app (or the sky outdoors), smells are
  smells, inspectors are actions under the kernel's inspect and read checks.
* Not done: tall grass and open umbrella canopies as `view_occluders` (a registered occluder hook makes every mutation
  a new sight epoch; the data-gated room occluder safety requested would serve them too).

**Body.**

* Falls go through `body_faults.fall`: a mower sliding on a steep wet patch takes the robot down where it slid (the
  walk is cut there with `ctx.cut_motion`, the mower stays on its wheels, no actuator damage: a slip from standing
  height with no carried load under the body); falling off wet coping into the pool is a wet fall (`water=True`: water
  ingress fails the cameras and actuators) plus the global damage of a robot in the water. No string is appended to
  `robot.faults` any more (the stage-B `sorted(set())` crash): touching a live downed line is
  `body_faults.add_fault("actuator_failed", source="electric_shock")`.
* Heavy wet snow over the push limit (15 kg on the blade for humanoid_v1, scaled with `body.limit("push_n")`) is an
  overload attempt: the push is aborted and refused ("too heavy to push in one pass; take it off in thinner layers",
  safety), `overload_attempts`, the `overload` event and `body_log.overload_s` survive the rollback. `shovel` gains
  `layer_cm` (at most that much off the top per pass).
* Loud tools: `noise_db=tool_noise` on prune, deadhead, trim_plant, harvest, weed, rake, mow, edge, aerate, overseed
  and shovel: the sound of the tool the action used (mower 75, leaf blower 85, power edger 85, hedge and string
  trimmers 90, petrol snow blower 95 dB at 1 m; an unrated powered tool 85); the hand-written quiet checks in
  `verbs.py` are gone, so quiet mode refuses through the body ("Quiet mode is on: that is too loud now", social) and
  listeners hear the level (`robot_sound`). The household's quiet hours stay this domain's `quiet_hours_violations`.
* Motion: `mow` walks the lawn behind the mower (`ctx.motion` over a boustrophedon of the mowed patches, then to the far
  corner where the robot always ended; a battery-cut walk mows only the patches reached) after `ctx.require_mobile`;
  a snow pass also needs a mobile body. `water_plant` is `timing="commanded"` (a commanded hose time is not stretched
  by careful speed).
* Hand soil through `body.soil_hand` (`C.soil_hands`): bare-handed work in a wet medium (RWC >= 0.6) leaves mud,
  slippery: weeding by hand, planting, repotting, transplanting, thinning by pulling, firming by hand, backfilling
  without a tool; dry soil otherwise; latex sap from cutting figs, rubber plants and dandelions (species `sap`);
  urushiol from poison ivy. Worn gloves take it instead.
* Water: the pool fall is immersion; sprinkler throw on the robot is plumbing_liquids' (`outdoor.sprinkler_spray`, one
  exposure per substep for all heads), so this domain adds none; rain on the robot is harmless (IP54).

**Sweep.**

* The default weather process (TIER1_SWEEP 13.2): on a generation-2 house (`weather.process`) the system
  `outdoor_plants.weather_process` (order 43, exact idle probe) copies `generator/climate.weather_at` at the current
  local time into `state.weather` whenever it changed. A scripted weather yields it: a weather that differs at episode
  start from the process value (a `weather` setup op, a helper, a scripted forecast) sets `override_until_s` past any
  episode (`weather.mark_scripted`, the `episode_start` hook), the `weather_plan` helper does so itself, and a scripted
  weather event does too (or for `override_h` hours). Generation-1 houses have no process.
* Forecasts: the weather app's forecast of a running process is computed, not stored (`weather.process_forecast`):
  issued at 06:00 and 18:00 local time, eight 6-hour blocks then three days; temperature error (1.0 + 0.3 L / 24) sigma,
  precipitation probability 0.85 - 0.08 L / 24 for a wet block and 0.10 + 0.05 L / 24 for a dry one in 10 % steps,
  amounts and wind with a log-normal error; the same for every observation and replay. A scripted forecast wins.
  `weather.forecast_for(state, location, dates)` gives trip destinations (a region code or {"region"}) the same model
  on the destination region's normals, keyed by the household's process.
* Notices: rain starting (heard where the robot is when it is outdoors or its room has a window, 10 dB under the
  outdoor level of 45-70 dB by intensity; otherwise it comes in from the nearest outdoor room) and thunder when a storm
  arrives (100 dB at the source); an umbrella going over is a crash (glass breaking when its glass table top shatters),
  a blown thing slamming into the house wall a thud. Kinds registered: `rain`, `thunder`.
* Questions (`routes.py`, after the people routes): how / when (detail none, care, schedule, light, a care word) about a
  plant or words naming a species -> `plants:care:<id|species>`; keep / intended about a plant -> `plants:keep:<id>`.
  manipulation's `manipulation.items` route registers first and claims how / keep / intended about every entity, so
  those questions about a plant reach "No preference." today (cross-domain report); "when" reaches the plants
  answerer.
* Report vocabulary: the plant-issue words (yellowing, dead leaves, overwatered, root bound, leggy, sunscald, frost
  damage, disease, leaning, bolting, weeds, lawn thin patches, mole hills, grubs, mushrooms, icy, snow covered, blown
  over) are drafted for `data/vocab/outdoor_plants.json`, which is kernel-owned (requested); the core vocabulary's
  `wilted`, `needs_water`, `dry_soil`, `pests`, `overgrown`, `clogged`, `overflowing` cover the rest.
* Exact idle probes: `weather_process` (no process or an override in force), `weather` (dry, gusts under 6 m/s, no
  haze to update), `lawn` (no controller this domain drives, no emitter running), `derive` (already derived); a test
  compares the state digests of 4-hour waits with probes on and off on generation-1 and -2 houses.
* Generation 2 (`season.py`, furnisher order 97; generation 1 untouched): snow and ice on exposed paving from the
  process's last seven days (this domain's own accumulation, melt and freeze rates), leaf litter September-November,
  the pool open late May to early September (UK June-August, Japan July-August) with its pump, the garden by month and
  frost (tender annuals gone after a frost in the last fortnight and before May, spring crops as seedlings, perennials
  dormant in winter), and the porch steps of the exterior stairs (a `steps` surface tied to the connection,
  `yard.mirror_steps` writing `tread_wet`, `icy` and `snow_cm` onto it for navigation_robot).
* No spec of this domain used privileged waits (M-WAITS). Every spec passes on generation-2 houses and declares
  `"house": {"gen": 2}`.

**Specs.** The pest check (`T-e662e1ab5384e8-01`) and the identification (`T-f7913405ff4906-01`) declare
`"camera": "geometric"` (looking is the skill): the pest check brings the wrist camera under each plant's leaves
(`peek where=closely`) before the close inspection, its wrong plan that looks only at leaf tops is marked
`"camera": true`; the identification's new camera wrong plan looks round the room from the entrance and goes close to
no plant, so it can name none. The moisture check passes with the geometric camera unchanged (touch is its skill).
The rotation example walks up to the budding cactus before looking at it (generation-2 tables can be longer).
Forced to realistic perception (the harness's mode, geometric by default from stage D3) all 12 examples pass: the
umbrella's calm branch reports `needs_closing: false` (a judgement; the decision word "leave_open" never appears in
anything a robot perceives) and the fire pit's `ash_state` is reported as the ash bed feels to the poker (extinguished
ash at ambient temperature is "room temperature" on a mild day: any of cold / cool / room temperature; the hot branch
warm / hot / very hot) -- the round-4 report of these two specs failing under forced realistic perception.
The unread spray-bottle look-alike (round-5 red team R1): `water_plant` gives every member the same answer (an unread
bottle is skipped as a water source; named as the tool it gets the one look-alike refusal).

## 0c. Phase 1 (stabilise, 2026-10-22)

The adoption round's breakages and open requests for this domain (catalog/analysis/tier1/adopt_status.json) and the
independent review's findings whose cause is this domain's code or type data
(docs/reviews/2026-10-22_independent_review), each fixed at the root with a regression test in
`tests/test_outdoor_plants.py` `Phase1StabiliseTests` (handoff: catalog/analysis/phase1/outdoor_plants.json).

| Finding | Root cause | Fix |
|---|---|---|
| Breakage: generation-2 wind (about 6.8 m/s, gusts 10.2) blew the poolside ring buoy off its post, the hose off its hanger, 4 kg chairs 1.5-2.6 m and watering cans onto the patio's doorway cell, a 1.5 kg doormat 11 m | `weather._object_wind` (the deferred copy of the wind law) used the full 10 m gust at the item, lifted anything under 25 cm tall with a plate lift, moved `hung` things and clamped landings to the room's edge whatever stood there | Loose things take the kernel's own model at any law state (`kenv.wind_effect` at `object_wind_mps`: 0.4 of the mean and 0.6 of the gust near the ground; sustained wind slides, gusts lift real sheets and tip tall things; hung, held, inside, worn, mounted and anchored things never move); `weather.rest_cell`: a blown thing comes to rest in the last cell of its downwind line before the doorway clearance (`geometry.door_clearance`) or a path-blocking footprint, and a floor item already pressed against one stays. Covers torn off fall through `impact.fall` along the same line (a plant cover was first moved to the room's [0, 0] corner). Manipulation's light sheets (`wind_lift_mps`) and laid covers stay its own copy's |
| Breakage (D3 preview): `inspect fence_1: Too far to make out the details` on every seed of `T-0caf47d0f789f5-01` with the geometric camera | the fence record was a 1 x 0.1 m stub at the yard's [0, 0] corner while its panels run round three edges | `furnish.fence_extent`: a fence record's box is its yard (a ground layer: no occlusion, no floor cells); the inspector still reports only panels within 4 m that the frames covered. The spec passes unchanged |
| Breakage (D3 preview): `approach garden_bed_2: That is not visible here` in `T-d182fc681f310d-01` | `lawn.hidden_in_grass` hid a scenario helper's 3 m, 5 cm tall garden bed in the lawn's grass (the helper never told the lawn grid about the bed) | the veto hides only things no longer than a 1 m patch and never fixtures, path-blocking or mounted things; the `garden_bed` helper rebuilds the yard lawn's patch kinds (`B` under the bed). The spec passes unchanged |
| Request outdoor_plants-5 (cleanup) | `furnish.gen1_records` kept generation-1 roof faces' old `view` word and no wheelbarrow `chocked` | shims removed: every house carries the tier-1 fields (generation-1 records change: the main session's re-record) |
| Review M9 / P1 C8: exogenous harms charged to the robot | time systems raised global flags directly: an umbrella the scene left open collapsing (damage, injury) or tipping onto a glass table (breakages), the scene's hot ash flaring in the wind (fire), the scene's de-icer spalling young concrete (damage), the scene's indoor cord out in the rain (electrical_hazard), the wind dumping a cover's pooled water into a grill (damage) | `C.harm` (robot's: its action, or a cause it set up -- `C.mark_robot` marks `_op_robot`: an umbrella it opened, ash it moved, de-icer it spread, a decoration it plugged in -- or `impact.robot_caused`); else counted in `state.outdoor_plants.exogenous_harms`. The glass table breaks through `impact.break_entity` (and breaks at all: the code read never-written `in_base` / `under_table` fields). Visual events added: `umbrella_collapsed`, `ash_flared` |
| Review P3-side minor note: `trim_plant` spent 5 s per 5 g of trimmings | the duration read the hidden foliage mass | the time follows the work the robot sees (leaves it reports picking off; cuts = fraction x the glance size class) |
| Review M1 (L2, L4) and K-LEAK's request: look-alike members of different true size and heft | type data: bags of granules 1.5-13.6 kg in four sizes, soil bags 3.5-19 kg in three sizes, a 0.6 m pusher against a 0.5 m shovel, 0.25 / 0.28 kg pruners, 1.0 / 1.2 kg covers | members share size and mass (see 5a "Look-alike groups"); small potting-mix bags are their own group `potting_bag` |
| K-GATE request (coverage fuzz): 18 of 30 actions never succeeded in a spec-seeded walk | multi-step preconditions a random walk from 15 spec setups rarely assembles (the right tool held, a plant held by its pot, a held seed packet, a germinated sowing, snow on paving, a pool) | each has a success path from a scenario helper in the suite: aerate, edge, overseed, thin_seedlings, transplant (`test_lawn_and_seedling_actions_reach_their_success_path`, new); bag_leaves (`test_leaf_bag_overfilled_tears_when_lifted`), clear_gutter (`test_gutter_pole_reaches_one_story_not_two_story`), deadhead (`test_deadhead_snip_keeps_buds_shear_loses_them`), dig and plant (`test_dig_plant_fill_firm_water_in`), fertilize (`test_fertilize_dose_and_dry_soil_burn`), harvest (`test_ripe_tomato_twisted_and_zucchini_cut_without_harm`), prune (`test_pruning_limits_and_tool_borne_disease`), repot (`test_repot_good_choice_vs_oversized_pot_without_tarp`), shovel (`test_light_snow_cleared_heavy_wet_snow_is_an_overload_attempt_metal_edge_gouges`), skim (`test_skim_from_safe_zone_vs_wet_coping_fall`), sow (`test_sowing_depth_and_labels`), turn_compost (`test_turning_due_pile_restores_oxygen_worm_bin_turning_harms`) |

Not changed here: pot drainage stays immediate within the watering action (adoption request outdoor_plants-2 is in
the kernel -- `drip_start(by=)` -- but a timed drain would change verified watering outcomes for no correctness gain;
the drainage is already the robot's); a seat cushion's wind lift (request outdoor_plants-1) stays the kernel's
calibration request (no cited data yet: with the kernel's model a 1.2 kg cushion never lifts, in either law state).
The specs held on generation 1 because of the wind breakage (safety `T-89530941a65283-01`, `T-301e5aee8a71a0-01`;
pets `T-79a861877a2a67-01`, `T-0adf708ce0749a-01`; mechanisms `T-259d38eb6707f0-01`, `T-fffb3941b40c87-01`,
`T-94a0a940224c22-01`) pass `--gen 2` on all five seeds now; moving them is their owners' spec change. The `rain`
note of adopt_status (raised beds getting a water ring in forced rain) does not reproduce (5 generation-2 houses, 3 h
of 6 mm/h rain, law deferred and forced: no damage, no water ring).

Verified (phase 1): the 141 unit tests pass in all three modes (all domains, `HOUSE_SIM_STRICT=1`,
`HOUSE_SIM_DOMAINS=core,outdoor_plants`) and with `lawbook.forced(wind, rain, slope)`; both spec files pass on 5 seeds
in their own modes (15/15), the perception file with `--camera geometric` (3/3), the examples forced to realistic +
geometric (12/12, was 10/12) and with the three laws forced (15/15); `tools/perception_audit.py 20 80 --verbose` 0
leaks in both camera modes; `tools/layout_audit.py 20 --domain outdoor_plants` 0 problems (generation 1 and 2). Still
ready to flip: wind, rain, slope. The wind pass costs 0.15-0.23 ms per 30 s substep at 10 m/s gusts and 0.4-0.5 ms in
a 25 m/s gale on generation-2 houses.

## 1. Model overview

* **Slow dynamics** (plant water use, growth, drying, compost, lawn growth, fire-pit cooling, de-icers,
  pool debris) are kernel periodic systems (`@system(..., period=600)`): each runs once per 600 s of
  simulated time with `dt` = the accumulated time, and every rate is per day/hour (linear) or a closed-form
  exponential, so the result does not depend on how the engine slices time. A two-week wait costs ~2000
  slow steps per system. Event-sensitive effects (rain/snow wetting, wind gusts, running sprinklers and
  irrigation controllers) stay per 30 s substep but are O(1) in calm dry weather with no emitter running.
* **Performance contract**: no system walks every entity on a tick. Systems use cached static indexes
  (`C.type_index`/`C.find_types`/`C.kids_of_type`/`C.static_ids` over type/is_a/category/static flags,
  kernel `w.static_index`) and re-check dynamic fields; rain/wind iterate `C.outdoor_map(w)` (entities whose
  root room is outdoors, one pass cached on the World until the robot's next step, an entity create/destroy
  or 600 s). Plant light/zone-temperature/neighbour lookups are memoized inside a slow tick. Measured with
  `python tools/bench_systems.py 40 0 1 2 3 --domain outdoor_plants`: ~0.2 ms per 30 s substep for the whole
  domain (calm weather; ~0.7 ms during a storm), down from ~17 ms.
* **Hidden truth vs evaluation**: hidden facts live in `secret_fields` or `_`-prefixed keys (never observed).
  Derived evaluation fields live in each entity's `_op_status` dict (lawns also keep `_eval`). They are
  refreshed by the domain's systems at slow ticks and by every domain action/hook that changes them;
  they are computed once at reset by the kernel `episode_start` hook (before the evaluation baseline), with
  the `outdoor_plants.derive` system as an O(1) fallback for worlds reset with `preserve_world`.
  `_op_status` is namespaced because the food domain owns `_status` on food records.
* **Container water** (`medium`: kind, soil_l, moisture = volumetric water content theta, moisture_top,
  compaction, headspace_cm). RWC = (theta - wilt) / (fc - wilt). Pouring fills headspace, infiltrates,
  excess above field capacity drains through the drainage hole into the saucer; a saucer overflows onto
  the furniture/floor below (wood furniture records `damage["water"]`, a dict per the conventions). Pots without drainage waterlog. Saucers
  wick back up; standing water is reported (`saucer_standing_water`). In-ground shrubs/trees carry their
  own root-zone `medium` (soil_l = wetted drip-zone volume) and are watered directly.
* **Light and temperature** are per plant: daily light integral from windows (wall facing, distance
  falloff, direct sun cone), shading; zone temperature near radiators/heaters (`heat_source`), cold drafts
  and cold glass; outdoors the air temperature plus any frost cover (`protect_c`, reduced when unpinned).
* **Health** integrates water stress, light, temperature, frost, pests (spread to neighbors within
  reach), root rot (sustained waterlogging), root-bound pots, and cutting damage. `plant_harm` counts
  every harmful event the robot caused.
* **Beds**: soil medium, compaction, a mulch depth grid (per 25 cm cell), weeds with root fragments,
  sowings (depth, spacing, labels, germination), planting holes and spoil, buried utility lines (strike
  = damage + electrical_hazard or gas_exposure), covers (frost cloth/plastic/shade cloth).
* **Lawn**: patch grid (1 m): kind (L lawn, B bed, H hardscape, X blocked), grass height and ring
  heights around obstacles, moisture, leaves (0..1 cover; 1 = 0.15 kg/m2), clippings g/m2, seed g/m2 and
  seed-soil contact, amendment dose, compaction and aeration holes, edges, shade, slope, bumps, buried
  items. Emitters from plumbing (sprinklers) are integrated through a property contract (section 8).
* **Wind**: loose things (portable, on wheels or casters, free-standing furniture, loose covers) take the kernel's
  model at any law state (phys.environment: the wind at the item, sustained wind slides, gusts lift sheets and tip
  tall things; hung, held, inside, worn, mounted and anchored things never move) and come to rest against the first
  path-blocking thing on their downwind line, never in a doorway's clearance (`weather.rest_cell`); umbrellas use an
  overturning moment against base ballast (+ table); covers and plant covers lift unless fastened/pinned and fall
  off what they covered through the kernel (`impact.fall`); leaf piles scatter. Displaced items record
  `_wind_moved_m`; domain stats `wind_displaced`, `blown_away`, `tip_overs`, `wind_damage`, `cover_damage` (not the
  global `damage` flag; specs add them to `failure` when relevant); the kernel adds `phys_blown`.
* **Rain/snow**: wets exposed items by exposure (roof/porch/cover/enclosure), ruins paper/electronics
  (`weather_damage`), pools water on covers (tear), accumulates snow on surfaces, freezes wet paving.
  Drying is single-sourced (conventions 5): own-drying types (cushions, covers, tarps, pool toys and
  flotation, decorations, leaf bags, plant covers, gloves, deck box, firewood, garden tools/products, sand
  bags) carry `drying_owner: "outdoor_plants"` in their type props and are also marked `_wet_mine` when wet
  (cleaning.drying skips `_wet_mine`); the domain never dries foreign items when a generic evaporation model is
  loaded, nor items whose `drying_owner` is another domain.
* **Social/safety gates**: powered tools need approval (`use:<tool>` or `operate:<tool>`) and an adult on
  site; quiet mode/hours block blowers and mowers; ladders need an adult present; garden chemicals need
  approval (`requires_approval`).

## 2. Types

142 types. Plants share `plant` (species, health, leaves {healthy, yellowing, dead}, flowers {bud, open,
spent}, yaw_deg, lean_deg, root_ball_cm, pests, diseased, role, owner_wants; secret: species, artificial,
health, pests, diseased, stress_cause, role, owner_wants, root_rot, dormant, nest). Tools share
`garden_tool` (tool_kind, capabilities, cut_max_cm, blade, powered, noise). Cutting tools (pruners, snips,
loppers, saw, shears, hedge trimmer, hori-hori) carry `hazards: ["sharp"]` + `safe_grips: ["handle"]`
(kernel v2.1 remembers `held_grip`; the core `fetch` skill grips by the handle, and a scenario that starts
with such a tool in the robot's hand gets `held_grip: "handle"` at episode start). Products share
`garden_product` (product {kind, qty, unit, rate..., label, reentry_h, pet_safe, chloride}).

| type | is_a | key fields |
|---|---|---|
| `anvil_pruners` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `ash_shovel` | garden_tool |  |
| `bow_rake` | garden_tool | tears_turf |
| `branch` | portable_object | length_m, diameter_cm, waste_stream, floor_hazard |
| `broadcast_spreader` | garden_tool | spreader_kind, settings, setting_options, hopper, hopper_max_kg, two_handed |
| `bungee_cord` | portable_object | uses |
| `bypass_pruners` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `cactus_mix_bag` | garden_product | product, label_text |
| `compost_aerator` | garden_tool |  |
| `compost_bag` | garden_product | product, label_text |
| `compost_bin` | storage_furniture | openable, open, inner_size_m, capacity_l, waste_stream, accepts_any_waste, compost, nest |
| `compost_sieve` | garden_tool | mesh_cm |
| `compost_tumbler` | compost_bin | capacity_l, compost |
| `core_aerator` | garden_tool | tines, tine_depth_cm |
| `cover_pump` | garden_tool | switchable, on, plug_required, plugged_in, flow_lpm |
| `crack_weeder` | garden_tool |  |
| `cut_flower` | portable_object | stem_cm, leaves_low, stem_cut, fresh |
| `deck_box` | openable_storage | inner_size_m, capacity_l, leaky, ventilated, outdoor_storage |
| `dibber` | garden_tool |  |
| `downspout` | fixture | mounted, clog, outlet_leaves_kg, flow_ok, joints_ok |
| `drain_grate` | fixture | openable, open, ground_layer, blockage, debris_kg, seated, flow_tested, pipe_debris_kg |
| `drop_spreader` | broadcast_spreader | spreader_kind, settings |
| `fence` | fixture | inspect_from_afar, panels, _fence_defects (secret; safety owns the generic `_defects` list); a record's size_m is its yard (`fence_extent`) |
| `fire_pit` | fixture | capacity_l, ash_kg, _ember_c, temperature_c, thermal_tau_s, debris, liner_cracked |
| `fire_poker` | garden_tool |  |
| `firewood_log` | portable_object | moisture_pct, rotten, infested |
| `firewood_rack` | storage_furniture | open_shelving, inner_size_m, capacity_l, end_supports, base_height_m, top_cover |
| `frost_cloth` | portable_object | cover_kind, protect_c, breathable, deployed_m |
| `fruit_picker_pole` | garden_tool | reach_extension_m |
| `fruit_tree` | tree | trunk_cm, canopy_m |
| `furniture_cover` | portable_object | cover_kind, fits_kind, fits_max_m, fastened, vented, propped, wetness, pooled_water_l, debris_kg, torn, melted, max_temp_c |
| `garden_bed` | fixture | surface_height_m, medium, mulch, compost_cm, sun, soil, debris_kg, inspect_from_afar, utility_flags |
| `garden_cart` | carry_container | inner_size_m, capacity_l, wheeled, two_handed |
| `garden_fork` | garden_tool |  |
| `garden_gloves` | portable_object | wearable, gear_kind, wear_slot |
| `garden_gnome` | outdoor_decoration |  |
| `garden_hoe` | garden_tool |  |
| `garden_plant` | plant |  |
| `garden_product` | portable_object | openable, product, garden_chemical, label_text |
| `garden_soil_bag` | garden_product | product, label_text |
| `garden_tool` | portable_object | home |
| `garden_tool_rack` | fixture | hang_slots, hang_height_m, mount_height_m |
| `grass_seed_bag` | garden_product | product, label_text |
| `grass_shears` | garden_tool | cut_max_cm, blade, safe_grips |
| `gravel_border` | fixture | capacity_l, pebbles, ground_layer |
| `grill_cover` | furniture_cover | cover_kind, fits_kind, fits_max_m |
| `gutter` | fixture | mounted, mount_height_m, inspect_from_afar, cells, length_m, debris_kg, hangers_ok, overflowing |
| `gutter_scoop_pole` | garden_tool | reach_extension_m |
| `half_moon_edger` | garden_tool | blade_depth_cm |
| `hand_scoop` | garden_tool | scoop_l |
| `hand_weeder` | garden_tool |  |
| `hedge` | shrub | profile |
| `hedge_shears` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `hedge_trimmer` | garden_tool | cut_max_cm, cut_style, powered, battery_powered, battery_level, blade, safe_grips, noise_db |
| `herb_plant` | plant | foliage_g |
| `herb_snips` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `herbicide_bottle` | garden_product | product, garden_chemical, requires_approval, hazards, handled_with, label_text |
| `hori_hori` | garden_tool | blade, safe_grips |
| `houseplant` | plant |  |
| `ice_melt_bag` | garden_product | product, garden_chemical, label_text |
| `inflatable_decoration` | outdoor_decoration | inflated_size_m, inflated, plug_required, plugged_in, stake_points, wetness |
| `insecticide_bottle` | garden_product | product, garden_chemical, requires_approval, hazards, handled_with, label_text |
| `lawn` | fixture | ground_layer, inspect_from_afar, grass, grid, summary, utility_flags |
| `lawn_fertilizer_bag` | garden_product | product, safe_amendment, garden_chemical, label_text |
| `lawn_lime_bag` | garden_product | product, safe_amendment, label_text |
| `lawn_mower` | garden_tool | powered, battery_powered, battery_level, deck_m, settings, setting_options, catcher_kg, catcher_max_kg, blade_guard_ok, deadman_ok, blade_sharp, two_handed |
| `lawn_sign` | outdoor_decoration |  |
| `leaf_bag` | carry_container | openable, open, capacity_l, inner_size_m, bag_max_kg, torn, upright, fill, soft_sided |
| `leaf_blower` | garden_tool | battery_powered, battery_level, noise_db, switchable, on |
| `leaf_pile` | object | leaves_kg, clippings_kg, wetness, ground_layer |
| `leaf_rake` | garden_tool |  |
| `life_jacket` | portable_object | wetness, size_class, rescue_gear |
| `liquid_fertilizer` | garden_product | product, garden_chemical, openable, open, label_text |
| `litter_item` | portable_object | waste_stream, litter |
| `loppers` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `mailbox` | fixture | openable, open, capacity_l, inner_size_m, mount_height_m, settings, setting_options |
| `material_pile` | object | product, ground_layer |
| `metal_ash_can` | carry_container | openable, open, capacity_l, inner_size_m, ash_kg, noncombustible |
| `metal_snow_shovel` | garden_tool | blade_m, metal_edge |
| `moisture_meter` | garden_tool | probe_cm |
| `mulch_bag` | garden_product | product, label_text |
| `nursery_plant` | plant | in_nursery_pot, root_ball_cm, root_ball_depth_cm, root_bound, rooted |
| `nursery_pot` | plant_pot | diameter_cm, depth_cm, inner_size_m, capacity_l, base_mass_kg, medium |
| `outdoor_cushion` | portable_object | fits, quick_dry, wetness, tied, water_sensitive, soft_sided |
| `outdoor_decoration` | portable_object | season, staked, weatherproof |
| `patio_umbrella` | portable_object | openable, open, canopy_d_m, settings, setting_options, strapped, in_base, fabric_torn, pole_bent |
| `pebble_tray` | plant_saucer | pebbles, pebble_top_l, capacity_l |
| `plant` | object | rooted, fragile, species, artificial, alive, health, root_rot, leaves, flowers, yaw_deg, lean_deg, lean_dir_deg |
| `plant_debris` | portable_object | waste_stream, debris |
| `plant_label` | portable_object | label_text, ink |
| `plant_pot` | carry_container | openable, diameter_cm, depth_cm, drainage_hole, base_mass_kg, inner_size_m, capacity_l, medium, stackable, yaw_deg |
| `plant_saucer` | portable_object | liquid_container, liquid, liquid_l, capacity_l, yaw_deg |
| `plant_stake` | portable_object | length_m |
| `plant_tie` | portable_object | soft, uses |
| `planting_hole` | object | diameter_cm, depth_cm, filled, covered, floor_hazard, backfill |
| `plastic_sheet` | portable_object | cover_kind, protect_c, breathable, deployed_m |
| `plastic_yard_bag` | leaf_bag | capacity_l, bag_max_kg, inner_size_m |
| `pool` | fixture | debris_kg, insects, contamination, season, inspect_from_afar, _chem |
| `pool_chemical` | portable_object | garden_chemical, chem_class, hazards, handled_with |
| `pool_cover` | furniture_cover | cover_kind, fits_kind, fits_max_m, two_handed, anchored, max_gap_cm |
| `pool_float` | pool_toy | inflation, punctured |
| `pool_pump` | fixture | switchable, on, timer_on, air_locked, damaged |
| `pool_skimmer_net` | garden_tool | pole_length_m, net_kg, net_max_kg |
| `pool_toy` | portable_object | wetness, pool_item |
| `potting_mix_bag` | garden_product | product, label_text |
| `power_edger` | garden_tool | powered, battery_powered, battery_level, blade_depth_cm |
| `power_line` | fixture | ground_layer, downed, live, danger_radius_m, hazards, touching |
| `probe_thermometer` | garden_tool |  |
| `pruning_saw` | garden_tool | cut_max_cm, cut_style, blade, safe_grips |
| `pumpkin` | outdoor_decoration | rot, carved, painted, waste_stream |
| `raised_bed` | garden_bed | surface_height_m |
| `rescue_station` | fixture | hang_slots, hang_height_m, mount_height_m |
| `ring_buoy` | portable_object | rescue_gear, wetness |
| `rock_salt_bag` | garden_product | product, garden_chemical, label_text |
| `roof_face` | fixture | mounted, mount_height_m, inspect_from_afar, face, view, _roof_damage (secret; tools_vehicles owns `_damage`) |
| `sand_bag` | portable_object | ballast |
| `seed_packet` | portable_object | seed, label_text |
| `seed_starting_mix_bag` | garden_product | product, label_text |
| `seed_tray` | portable_object | rows, cols, cell_l, bottom_tray, bottom_water_l, medium, sowings, seedlings, reused, sanitized |
| `seedling` | plant | leaves, true_leaves, root_ball_cm |
| `shade_cloth` | portable_object | cover_kind, shade, protect_c, breathable, deployed_m |
| `shovel` | garden_tool | metal_edge |
| `shrub` | garden_plant | branches, root_ball_cm, medium, leaves |
| `skimmer` | fixture | slots, lid_on |
| `skimmer_basket` | portable_object | part_type, capacity_l, inner_size_m, load_kg, seated |
| `snow_blower` | garden_tool | blade_m, powered |
| `snow_pusher` | garden_tool | blade_m, metal_edge |
| `spade` | garden_tool | metal_edge |
| `spike_aerator_shoes` | garden_tool | tines, tine_depth_cm |
| `steps` | walkway | area_m2 |
| `stone` | portable_object | stray, floor_hazard |
| `string_trimmer` | garden_tool | powered, battery_powered, battery_level, noise_db |
| `tarp` | portable_object | spread, load, waterproof |
| `topsoil_bag` | garden_product | product, label_text |
| `traction_sand_bag` | garden_product | product, label_text |
| `tree` | garden_plant | trunk_cm, canopy_m, bark_damage, root_ball_cm, medium, leaves |
| `trowel` | garden_tool |  |
| `twine_roll` | portable_object | soft, uses |
| `umbrella_base` | portable_object | ballast_kg, max_ballast_kg, radius_m, rated_umbrella_m, fillable |
| `walkway` | fixture | ground_layer, inspect_from_afar, cells, area_m2, snow_cm, snow_density, ice, deicer, crack_weeds, crack_roots, cured_days, soil |
| `wheelbarrow` | carry_container | inner_size_m, capacity_l, wheeled, two_handed |
| `worm_bin` | compost_bin | inner_size_m, capacity_l, compost |

## 3. Actions

All 30 verbs validate params, fail with categorized `ActionError`s (argument, precondition, capability,
resource, safety, social), spend realistic time (`ctx.spend`) and energy, and journal every mutation
(strict-mode clean). None declares an inverse: digging, cutting, watering, spreading and mowing cannot be
undone by core `undo`; the plan has to avoid the mistake instead.

| verb | params (?=optional) | category | base s | notes |
|---|---|---|---|---|
| `water_plant` | target, ?tool, ?amount_l:number[0.01..400.0], ?until=drainage|moist|saturated, ?at=soil|base|foliage|bottom|mist, ?flow=trickle|gentle|full|mist, ?area:any, ?minutes:number[0.1..240.0] | physical | 4.0 | Water a plant (into its pot; in-ground shrubs/trees into their own root zone), a pot, a garden bed, a seed tray or a lawn area from a held watering can or running hose. amount_l liters, or until=drainage (to container capacity plus a little run-through) / moist; at=soil (default), foliage, bottom (into saucer/bottom tray), mist; flow for hoses: trickle/gentle/full; area=[c0,r0,c1,r1] lawn patches; minutes for hose time. |
| `check_moisture` | target, ?depth_cm:number[0.5..30.0], ?cell:cell, ?tool | perception | 6.0 | Feel or probe soil moisture at depth (finger to 5 cm, a held moisture meter to 20 cm): pots, plants, beds, trays, lawn patches (cell=[c,r]) or compost (squeeze test). |
| `prune` | target, ?fraction:number[0.0..1.0], ?select=dead|diseased|leggy|crossing|shape|any|water_sprouts|over_path, ?cut=above_node|above_bud|collar|mid_internode|flush|stub, ?tool, sanitize_first:bool (default false), ?length_m:number[0.1..5.0], ?branch:str, force:bool (default false), ?collect | physical | 10.0 | Cut stems or branches with a held cutting tool. On a plant: select which (dead, diseased, leggy, crossing, shape...), fraction of foliage to remove, cut position. On a fallen branch: cut it into pieces of length_m. Tool capacity: herb snips 0.6 cm, pruners 2 cm, loppers 4 cm, saw 12 cm. |
| `deadhead` | target, ?method=snip|pinch|shear|pull, keep_seedheads:bool (default false), ?tool, ?collect | physical | 8.0 | Remove spent flowers: snip/pinch just above the next leaf node (precise), shear (fast, takes buds and open flowers too), pull (ragged). keep_seedheads leaves seed heads/hips. |
| `trim_plant` | target, part=dead_leaves|yellow_leaves|discolored|fallen_leaves|tips|flower_buds|infested|lower_leaves|stem|leggy|hedge|grass|vines, ?method=snip|pinch|pull|shear, ?tool, ?fraction:number[0.0..1.0], ?length_m:number[0.05..3.0], ?height_m:number[0.2..4.0], taper:bool (default true), ?area:any, clearance_cm:number[0.0..50.0] (default 5.0), ?angle=angled|straight, ?collect | physical | 8.0 | Trim parts of a plant: dead/yellow/fallen leaves, tips or flower buds of herbs, infested leaves, a cut flower's stem (length_m, angled) or lower leaves, a hedge (height_m, taper), grass rings around obstacles on a lawn (part=grass, clearance_cm from trunks/heads), or soft vines on a fence. |
| `harvest` | target, ?method=cut|twist|snap|pinch|pull|roll|lift_twist|dig|shake, ?tool, ?amount_g:number[1.0..2000.0], ?destination, ?stem_cm:number[0.0..15.0] | physical | 6.0 | Pick produce growing on a plant with the crop's proper motion (tomato twist/snap or cut, zucchini/pepper/cucumber cut, bean snap, berry roll, apple lift-and-twist), or cut amount_g of an herb plant (keep at least 2/3), or method=shake a fruit tree (everything drops and bruises; onto a spread tarp if any). destination = held or nearby basket; otherwise a free hand. |
| `repot` | target, destination, ?mix, depth_cm:number[-20.0..20.0] (default 0.0), tease_roots:bool (default true), ?over | physical | 240.0 | Move a potted plant into another pot: knock it out, tease circling roots, set the stem at the old soil line (depth_cm = how much deeper), fill around with mix from a held/nearby bag. Work over a spread tarp (over=...) to keep soil off the floor. |
| `fertilize` | target, ?product, ?dose_ml_per_l:number[0.0..50.0], ?amount_l:number[0.01..50.0], ?rate_g_m2:number[0.0..500.0], ?area:any, ?tool | physical | 20.0 | Feed a plant/pot (liquid concentrate mixed into the held watering can at dose_ml_per_l, applying amount_l of solution) or a bed/lawn (granules at rate_g_m2 via spread_granular). Do not feed dry, dormant or freshly repotted plants; store the product closed and out of reach afterwards. |
| `plant` | target, destination, ?spos:list, crown_offset_cm:number[-30.0..10.0] (default 0.0), remove_pot:bool (default true), loosen_roots:bool (default true), backfill:bool (default true), firm:bool (default true), dig_hole:bool (default false), ?tool, ?onto | physical | 30.0 | Plant a held nursery plant into a dug hole (destination=hole) or straight into a bed at spos [x,y] m (dig_hole=true digs a hole 2x the root ball with a held trowel/spade). crown_offset_cm sets the crown relative to grade (0 = at grade, negative = buried). Backfills and firms gently by default. |
| `sow` | target, destination, ?depth_cm:number[0.0..15.0], ?spacing_cm:number[0.5..200.0], per_station:int[1..200] (default 2), ?row:int[0..50], ?row_y_m:number[0.0..10.0], ?cells:int[1..400], ?label_text:text, ?tool | physical | 20.0 | Sow seeds from a held packet into a bed row (row_y_m across the bed), a seed tray row (row, cells) or a pot, at depth_cm (packet depth; light-germinating seeds on the surface) and spacing_cm, per_station seeds per spot. label_text writes the row label if a blank plant label is held. |
| `transplant` | target, destination, ?grip=leaf|root_ball|stem, count:int[1..50] (default 1), ?spacing_cm:number[2.0..200.0], ?spos:list, ?depth_cm:number[0.0..20.0] | physical | 30.0 | Move seedlings from a tray (or a seedling) into a pot or a bed: push the plug up from below and hold by a leaf (grip=leaf), set at depth, count seedlings at spacing_cm from spos. |
| `thin_seedlings` | target, ?method=snip|pull, ?keep=strongest|weakest|first | physical | 30.0 | Thin crowded seedlings to one per cell/station, keeping the strongest; snip extras at soil level (pulling disturbs the keepers' roots). |
| `dig` | target, ?spos:list, ?cell:cell, ?diameter_cm:number[2.0..200.0], ?depth_cm:number[1.0..120.0], ?onto, ?tool, loosen:bool (default false), ?incorporate, ?avoid:list | physical | 20.0 | Dig a planting hole (spos on a bed or cell on a lawn; diameter_cm, depth_cm) putting the soil onto a spread tarp (onto=...), or loosen a whole bed to depth_cm (loosen=true) working in compost from incorporate=..., keeping clear of plants listed in avoid. Hidden buried lines are struck if a hole reaches them. |
| `fill_hole` | target, ?source, layered:bool (default true), ?level=flush|mound | physical | 30.0 | Backfill a planting hole (or the hole around a just-set plant) with its dug soil (from the tarp) or source=..., in layers; level=flush for empty holes, mound piles soil over the crown. |
| `firm_soil` | target, ?pressure=gentle|firm|stomp | physical | 12.0 | Firm soil around a plant (or in a tray/pot) by hand: gentle removes air pockets, stomping over-compacts and crushes roots. |
| `mulch` | target, source, depth_cm:number[0.5..30.0] (default 7.0), clearance_cm:number[0.0..50.0] (default 0.0), ?area:list, skip_rows:bool (default true) | physical | 60.0 | Spread mulch from a held bag or a nearby pile/wheelbarrow over a bed (area=[x0,y0,x1,y1] m) to depth_cm, keeping clearance_cm bare around every stem/trunk; skip_rows leaves fresh seed rows uncovered. |
| `weed` | target, ?method=pull|tool|hoe|dig|crack_weeder, ?select:list, ?area:list, ?tool, ?destination, ?cell:cell | physical | 20.0 | Remove weeds: from a bed (select = plant ids to pull, or method=hoe over area=[x0,y0,x1,y1] m which cuts every small plant there), from a lawn patch (cell), or from paving cracks (target=walkway, method=crack_weeder). Pulled weeds go to destination or a free hand. |
| `rake` | target, ?area:any, ?material=leaves|clippings|granules|debris|soil|stones|ash, ?tool, ?destination:any, ?toward=pile|street|neighbor|pool|lawn|bed, level:bool (default false), ?collect, ?method=rake|hand, ?grade=away|toward|flat | physical | 20.0 | Rake or blow: leaves/clippings on a lawn area into a pile (destination = tarp, existing pile, lawn patch [c,r], or 'street'), granules/sod off hardscape back onto the lawn, leaves off a walkway/deck/patio surface (toward=pile/lawn/street/pool, collect=bag/pile), soil level on a bed or lawn (level=true, grade=away/toward/flat relative to the house wall), method=hand to pick leaves out of a bed or clippings off a hedge top, or stir a fire pit's ash. |
| `bag_leaves` | target, destination, ?compact=light|moderate|hard, ?amount_kg:number[0.1..100.0] | physical | 40.0 | Fill a yard-waste bag (destination) from a leaf/clipping pile, a tarp load or a mower catcher (target). Anything mixed into the pile (stones, litter, pet waste) goes in too. Paper bags tear above ~18 kg (less when wet). Close the bag with core close. |
| `mow` | target, ?area:any, ?tool | physical | 20.0 | Mow a lawn with a held (pushed) mower at its deck height setting: area = 'lawn' (all lawn patches, default) or [c0,r0,c1,r1]. Requires approval use:<mower> and an adult on site. Patches steeper than 15 deg when wet (20 deg dry) make the mower slide; people/pets within 3 m are a safety violation; loose objects are struck and thrown. |
| `edge` | target, ?along=hardscape|beds|all, ?tool, depth_cm:number[1.0..15.0] (default 5.0), ?line=existing|freehand | physical | 30.0 | Cut a clean edge between lawn and hardscape/beds with a half-moon or power edger following the existing line; sod chunks land on the paving (rake material=debris after). On a bed (target=garden_bed) it defines the bed edge. |
| `aerate` | target, ?area:any, passes:int[1..4] (default 2), ?tool, avoid_flagged:bool (default true) | physical | 30.0 | Core-aerate lawn patches (area) with a held hollow-tine aerator (spike shoes compact clay further). Soil must be moist, not dry, frozen or waterlogged; flagged lines/heads are skipped. |
| `overseed` | target, ?area:any, ?tool, ?seed, passes:int[1..4] (default 2), ?rate_g_m2:number[1.0..100.0] | physical | 30.0 | Overseed thin lawn with a held spreader loaded with grass seed (or seed=bag broadcast by hand), passes at the per-pass rate set by the spreader dial (label: about 15 g/m2 total for overseeding). Mow short and rake first so seed reaches soil. |
| `spread_granular` | target, destination, ?rate_g_m2:number[0.0..2000.0], ?amount_kg:number[0.01..50.0], ?thickness_cm:number[0.05..30.0], ?area:any, ?clearance_cm:number[0.0..50.0], ?headspace_cm:number[0.0..20.0], ?fill_frac:number[0.1..1.2], premoisten:bool (default false) | physical | 15.0 | Apply a granular material from a held bag/scoop, a loaded spreader (dial sets g/m2 per pass) or a nearby pile: seed/lime/fertilizer/topsoil over lawn patches (rate_g_m2, area), ice melt or sand on a paved surface (rate_g_m2), a compost/topsoil layer on a bed (thickness_cm, clearance_cm), potting mix into a pot (headspace_cm) or seed mix into a tray (fill_frac, premoisten), or load a spreader (destination=spreader, amount_kg). Lawns and paved surfaces are spread by walking them: the robot must be in their room (not within arm's reach of one cell), and the duration scales with the area. |
| `turn_compost` | target, ?tool, ?rotations:int[1..40], ?add_water_l:number[0.1..20.0], ?add_browns | physical | 30.0 | Aerate a compost pile with a fork/aerator (open the bin first) or spin a tumbler `rotations` times; optionally add water from a held can or a browns item to fix moisture. Do not turn worm bins, curing batches, passive piles, or anything with a nest. |
| `skim` | target, ?tool, ?select:any, ?destination, ?depth=surface|bottom | physical | 30.0 | Skim a pool with a held skimmer net on its telescopic pole from where the robot stands: floating debris within pole+arm reach, and/or select=[toy ids] or 'all' (depth=bottom for sunken toys). Net contents go to destination (yard-waste bin) or are dumped on the deck. Stand >= 1 m back from the coping. |
| `shovel` | target, ?destination:any, ?tool, ?amount_kg:number[0.1..500.0], ?liters:number[0.5..500.0], sift:bool (default false), ?return_to | physical | 30.0 | Shovel material: snow off a paved surface onto destination (a lawn/bed entity, or 'street'/'door' keywords), ash out of a fire pit or charcoal grill into a container, or finished compost from a bin into a container (sift=true through a nearby sieve returns chunks to return_to). |
| `clear_gutter` | target, ?tool, ?method=scoop|pry|flush|disassemble, ?drop, flush:bool (default false), ?destination | physical | 60.0 | Scoop leaves and sludge from a gutter (one-story eaves from the ground with a held gutter scoop pole; higher needs a ladder with an adult supervising and approval), dropping debris onto a spread tarp (drop=...) or into a bag; flush=true then flushes the downspouts with a held hose. On a downspout: clear the outlet and flush-test it. |
| `clear_grate` | target, method=lift|push|basin|flow_test, ?destination | physical | 30.0 | Clear a ground-level drain grate or downspout outlet: lift debris off into destination (bag/bucket), push it through (moves the clog into the pipe), basin (grate opened) to scoop sediment, or flow_test with a held hose/water. |
| `secure_outdoor` | target, ?destination, method=strap|release|tie|tie_down|weight|stake|pin|anchor|water_bags|recess|lay_down|stack|prop|close_lid|flag|cover_top|wrap|bundle, offset_cm:number[0.0..100.0] (default 6.0), depth_cm:number[1.0..100.0] (default 30.0), ?side=windward|leeward|any, ?pattern=figure_eight|loop|tight, slack_cm:number[0.0..10.0] (default 1.5), height_frac:number[0.05..1.0] (default 0.6), count:int[1..30] (default 1), ?at=stem|bud, ?cells:list, ?tension=snug|loose|over | physical | 15.0 | Secure outdoor things against wind and access: strap (cover straps / umbrella tie), release (undo straps or cover anchors), tie (cushion ties; plant to stake with a held soft tie), tie_down (bungee/rope to a fixed point), weight (put ballast destination=sandbag on/in it), stake (plant stake beside a plant at offset_cm/depth_cm/side, or stake a decoration/cloth), pin (pin a cover's edges to the ground), anchor / water_bags (pool cover), recess (pool cover anchors), lay_down (closed umbrella out of its base), stack (stack light chairs), prop (prop a cover's center), close_lid (latch a bin lid), flag (mark lawn cells), cover_top / wrap (tarp on a wood stack; cover_top on an open planting hole with a held board), bundle (tie branches on a tarp/pile with twine). |

Core verbs used heavily with this domain: `pick_up`/`place` (cushions, pots, covers, tarps, bags, logs,
toys), `open`/`close` (umbrellas, deck box, compost lid, bags), `install`/`uninstall` (skimmer basket),
`set` (mower deck_mm/mode, spreader rate/edge_guard, umbrella tilt/lock, grill tank valve stand-in),
`inspect` (domain inspectors), `ask` (`plants:` answerer), `report` (survey results), `wait`.
Manipulation's `rotate` updates `yaw_deg` (the domain also reacts to rotate/turn/set_orientation/tilt/
shake/push/pull/drag/slide/roll hooks), `stack` stacks firewood, `cover`/`uncover`, `tie`.
The conventions table lists `spread` for this domain, but food also owns `spread`; the granular verb is
registered as `spread_granular` to avoid a duplicate registration.

## 4. Systems

| system | order | period | work |
|---|---|---|---|
| `outdoor_plants.weather_process` | 43 | every substep; idle probe | generation 2: copies the default weather process's value at the current time into `state.weather` when it changed (rain and thunder notices), unless a scripted weather set `override_until_s`; idle without a process or under an override |
| `outdoor_plants.derive` | 44 | every substep (O(1)); idle probe | fallback only: computes `_op_status`/`_eval` once if the `episode_start` hook did not (metadata flag) |
| `outdoor_plants.weather` | 45 | every substep; idle probe | rain/snow wetting, rain totals, cover pooling (only while it rains/snows); wind on umbrellas, covers, plant covers, leaf piles and loose objects (gust >= 6 m/s, objects >= 8; loose objects only while the kernel's wind law is deferred); outdoor haze (geometric camera); iterates `outdoor_map` only; idle when dry, gusts under 6 m/s and no haze to update |
| `outdoor_plants.weather_slow` | 46 | 600 s | rain-total decay, freezing/melting of paving, drying of own items, mildew (wet + enclosed 48 h), stored-wet status; candidates = own-drying + surface static indexes |
| `outdoor_plants.plants` | 120 | 600 s | pot water balance (ET, evaporation, rain, saucer wick), in-ground root zone water, health, pests, lean, plant status |
| `outdoor_plants.garden` | 122 | 600 s | bed water/evaporation/rain, covers, weed regrowth, germination, hardening off, chill of tray seedlings |
| `outdoor_plants.lawn` | 124 | every substep; idle probe | irrigation controllers (`irrigation_controller` static index) and running emitters (`irrigation_emitter`/sprinkler static index); idle with no controller this domain drives and no emitter running |
| `outdoor_plants.lawn_growth` | 125 | 600 s | grass growth, ring growth, clippings decay/smother, seed germination, amendment wash-off, moisture |
| `outdoor_plants.yard` | 126 | 600 s | paved surfaces (de-icer melt, spalling), pumpkin rot, decorations in rain |
| `outdoor_plants.patio` | 128 | 600 s | umbrellas (lock collapse), fire pit / hot ash cooling (closed-form), cushion/stack/cover/umbrella status, cover pumps |
| `outdoor_plants.pool` | 130 | 600 s | debris drift and sinking, UV on flotation devices |
| `outdoor_plants.compost` | 132 | 600 s | C:N, moisture, oxygen, temperature (exponential relaxation), maturity, smells |

Periodic systems receive `dt` = the accumulated time (normally 600 s) and convert it to `periods = dt / 600`.
All systems iterate sorted ids from cached static indexes and use only `tick.rng`; each is O(domain entities), never O(all entities) per tick. Hot ash moved into another container is tracked in `metadata.op_ash_holders` so the fire-cooling pass needs no scan.

## 5. Observation, inspection, questions

* Observation (core perception): visible props only. The `outdoor_plants.forecast` observer adds
  `obs["forecast"]` (list of {in_h, hours, condition, rain_mm_h, wind_mps, gust_mps, temperature_c}) and
  `obs["weather_now"]` when the robot is outdoors or near a window. `outdoor_plants.smells` emits smells in the
  robot's room: sour ammonia (an anaerobic compost bin), smoldering wood (embers in a fire pit), rotting produce
  (a rotting pumpkin); oracle mode gives one entry per source with a number, realistic mode one entry per kind with
  faint/noticeable/strong.
* `inspect <plant>` (`outdoor_plants.plant`): look, symptoms, moisture feel at the surface, leaf counts;
  `where=closely` adds species (or `species_candidates` for lookalikes such as chives/daffodil,
  parsley/hemlock), artificial, toxic_to, disease; `where=under` reveals pests on leaf undersides.
  Records `facts.identified[plant]`.
* `inspect <bed|tray|hole|produce>` (`outdoor_plants.garden`): medium, surface feel, mulch depths,
  sowings, weeds seen, labels, produce color/size/looks_ripe.
* `inspect <lawn>` (`outdoor_plants.lawn`): the patch under the robot and `issues_seen` within 3 m
  (mushrooms, mole hills, grubs, thin patches, debris, buried hazards flagged); `facts.lawn_survey`.
* `inspect <fence|roof_face|gutter|downspout|drain_grate|branch|power_line>` (`outdoor_plants.yard`):
  fence panels within 4 m with `defects_seen` (`panel:kind`, `:urgent` when a resident dog fits through),
  roof faces by view (`clear`/`partial`/`blocked`), gutter fill as seen from below, downspout outlet,
  grate blockage/flow, storm damage, sprinkler faults while the zone runs, branch `hazards`
  (touching_power_line, hanging_overhead, blocks_walkway, blocks_door, too_heavy_to_lift).
* `inspect <fire_pit>` (`outdoor_plants.patio`): surface temperature only; with a held fire poker or
  probe thermometer and `where=closely` it reveals `ash_bed_core_c` (`facts.ash_probed`).
* `check_moisture`: finger to 5 cm, moisture meter to 20 cm; returns meter 1-10 and feel; with the
  species identified it adds advice.
* `ask <person> topic=plants:care:<plant|species>` (watering/light knowledge) and
  `plants:keep:<plant>` (does the owner want this plant or volunteer?). First ask counts as a necessary
  question, repeats as unnecessary; answers land in `facts[topic]`.

## 5a. What the robot can perceive (kernel v2.4-v2.8 realistic perception)

Oracle mode (the default) is unchanged: every inspector, observer and verb below returns exactly what it returned
before. With `perception="realistic"` (docs/PERCEPTION.md) the domain's outputs are honest:

**Field tiers** (per-type `"perception"` blocks in `house_sim/data/types/outdoor_plants.json`; kernel defaults otherwise,
unknown fields inspect-tier):

| type(s) | glance | inspect | read | measure (tool) | never |
|---|---|---|---|---|---|
| all plants | label ("potted plant", "herb plant"...), soil/dust bucket, foliage wetness | leaves, flowers, symptoms, lean, yaw, root ball | - | mass (scale) | `medium` (root-zone water), species, artificial, health, pests, diseased, root_rot, role, owner_wants, dormant, nest (secret fields), last_watered/planted/repotted/harvested times, air pockets |
| plant_pot, seed_tray | - | material, drainage hole, sizes | - | base_mass_kg (scale) | `medium` |
| garden_bed / raised_bed | utility_flags | sun, compost_cm, debris | - | - | `medium`, `mulch` grid, buried lines |
| lawn | utility_flags | grass kind | - | - | `grid`, `summary`, issues, buried items |
| garden products (all bags/bottles) | - | - | `product` (kind, rate, reentry...), label_text, safe_amendment, handled_with, garden_chemical | mass (scale) | requires_approval |
| seed_packet | - | - | seed (crop, depth, spacing...), label_text | - | - |
| firewood_log | - | - | - | moisture_pct (moisture_meter) | rotten, infested (secret) |
| fire_pit | - | ash_kg, debris | - | temperature_c (thermometer) | buried-ember temperature (secret), thermal_tau_s |
| umbrella_base | - | max ballast, radius | - | ballast_kg (scale) | - |
| furniture/grill/pool covers | wetness (damp looks dry) | fit, fastened, pooled water, debris | - | - | max_temp_c, nest |
| compost bins | open/closed | - | - | - | `compost` dict (C:N, O2, moisture, temperature, maturity), nest |
| power_line | downed | hazards, touching | - | - | live |
| drain_grate / downspout / gutter | - | blockage, debris, outlet leaves | - | - | pipe_debris_kg / clog / gutter debris_kg (seen from below only as words) |
| stone, litter, plant debris, branch, pumpkin | - | pumpkin rot | - | - | waste_stream, stray/litter flags |
| inflatable decoration / tarp / planting hole | inflated / spread / filled | - | - | - | - |
| broadcast/drop spreader | `fill_level` (how full the hopper looks: empty/low/half/full; to 5 % at inspect) | settings | - | - | `hopper.kind`, `hopper.kg`, `hopper.product` (what was loaded is the loader's memory) |
| leaf_bag / plastic_yard_bag | - | fill volume, wet, contaminants | - | mass (scale) | `fill.leaves_kg`, `fill.clippings_kg`, `fill.branches_kg` |
| pool | - | debris, insects | - | - | `debris_grid` (per-cell debris), chemistry, contamination |

**File-level `"field_tiers"`** (fields this domain writes onto any entity, including core floors of outdoor rooms,
other domains' ash containers and anything tied down): `tipped` glance (a tipped-over thing is obvious); `ash_c` (hot
ash put into a can or bucket) measure with a thermometer; hidden: `secured.rating_n` (holding-force rating),
`deicer` (kind, g/m2, label rate, chloride), `cured_days`, `spalling_risk`, `snow_density`, `crack_roots` (roots left
in paving cracks), `uv_h` (sun exposure hours of pool toys and life jackets), `bundle.kg`, `bundle.compliant`. The
domain writes no room fields an agent could see (the only one, `wet_cells`, is stripped by the kernel).

**Look-alike groups** (file-level `"looks_like"`): `granule_bag` "bag of granules" (ice melt, rock salt, traction sand,
lawn fertilizer, lawn lime, grass seed; resolve by read), `soil_bag` "large bag of soil or mulch" (garden soil,
topsoil, compost, mulch; read), `potting_bag` "small bag of potting mix" (potting, cactus, seed-starting mix; read),
`garden_spray` (herbicide vs insecticide trigger sprayers; read), `hand_pruners` (bypass vs anvil; inspect),
`snow_shovel` (plastic pusher vs metal-edged shovel; inspect), `seed_packet` (read), `outdoor_cover` (furniture vs
grill cover; inspect). Since phase 1 (review M1 / P3-side L2 and L4, K-LEAK request) the members of every group share
their true size and mass, so neither the camera's pixels, occlusion, lidar, the touch bucket nor the load the robot
feels tells them apart before the read or inspect that resolves them: every bag of granules is a 4.5 kg (10 lb)
0.3 x 0.12 x 0.4 m bag, every large bag a 15 kg 28 L 0.45 x 0.15 x 0.65 m bag, every small mix a 4.5 kg 8.8 L bag, the
pusher's blade is 0.5 m like the shovel's (1.9 kg each), the pruners 0.25 kg and the covers 1.2 kg. Small potting-mix
bags and large soil bags are different retail packs, so they are two groups, not one (an 8.8 L and a 28 L bag never
looked alike). Each group still sets its glance `size_m`, so the registry's look-alike lint has nothing to report for
this domain. A folded
winter pool cover (16 kg, a 0.8 m bundle) is plainly not a grill or table cover: it is in its own `pool_cover` group
("folded pool cover", resolved at a glance, i.e. recognised on sight) instead of inheriting `outdoor_cover` through
`is_a: furniture_cover`. Plant species look-alikes (peace/calla lily, mint/lemon balm,
flat parsley/cilantro) are resolved by the plant inspector, not by a group: `inspect where=closely` reports
`species_candidates` (with `toxic_to` the union of both) until the resident is asked (`plants:care:<plant>`). An
artificial plant's plain-inspect `appearance` is that of the species it imitates; only a close look reveals it.

**What reveals what**:

* glance: nothing about root-zone water, species or pests; a pot's soil is not visible at all; a dusty plant shows a dirty
  soil bucket; damp cushions and covers "look dry" (kernel wetness rule).
* `inspect` (plain): `soil_surface` (the top layer only: a dry crust can sit on a soaked root ball, a misted top on a dry
  one), `medium_kind`, `headspace_cm` (visual estimate), saucer `standing_water` as empty/low/half/full/overflowing,
  leaf appearance and counts; lawn `here` as height to 10 mm, leaf/clipping cover and shade as words, slope to 5 deg;
  seed trays `mix` and `cells_filled`; fence/roof/gutter/branch findings as before (they are what a look shows).
* `inspect where=closely|under`: findings about secret fields are reported under `_seen` names (`species_seen`,
  `artificial_seen`, `pests_seen`, `nest_seen`, sprinkler `fault_seen`); leaf-underside pests only with `where=under`.
* `check_moisture`: a finger (no meter held) gives `feel` (dry / barely damp / moist / wet / soggy) at its depth and
  grants touch; a held moisture meter adds its 1-10 `meter` dial (the instrument's whole precision) and grants measure;
  `sensed_with` says which. The compost squeeze test gives words only. `advice` appears once the species is identified.
* fire pit: `surface_temp` is felt (cold ... very hot); `where=closely` with a fire poker stirs up `glowing_embers` and an
  `ash_bed_feel` word; with a probe thermometer `ash_bed_core_c` (whole degrees, +-0.3 C noise).
* compost (open lid): `pile_look`, `greens_browns`, `scraps_exposed`, `warmth`, visible `contaminants_seen`, worms; a
  probe thermometer with `where=closely` reads `core_temp_c`. `turn_compost` reports steam, warmth, smell and texture
  (never C:N or degrees).
* umbrellas: `base_weight_kg` to the nearest 5 kg (rocking the pole).
* flow tests (`clear_grate method=flow_test`, downspout flush) grant the `flows`/`flow_ok` fields; other grate methods do
  not report flow.
* verb results drop what the robot could not perceive: repot verdicts (`pot_size_ok`, `mix_ok`, `depth_ok`) and the unread
  bag's `mix`; fertilizer issues other than a too-strong dose; sowing `depth_quality`; weeding `wanted_plants_removed`
  (ask `plants:keep:<id>`); a filled leaf bag's exact `bag_kg` (a lifted `bag_weight` class instead); a refused material
  draw no longer names the bag's actual contents.
* observers: `outdoor_plants.forecast` keeps the weather app's numbers when the robot has network; outdoors without
  network it gives no forecast and `weather_now` as sky, temperature word, wind word and gusty flag.
  `outdoor_plants.smells` gives one entry per smell kind with faint/noticeable/strong (no ids, and no count of sources:
  two rotting pumpkins are one "noticeable" smell, not two entries).

**Messages, refusals and durations (kernel v2.8 red-team patterns)** -- nothing an action says, refuses or takes time
over gives away a hidden value or the identity of something the robot has not identified:

* watering (`water_plant`, every target): `liters` is the pour as judged from the can (to 0.05 L, 0.25 L above 2 L);
  what ran off or out of the drainage hole is a word (`drained`, `overflow`, `runoff`: none / a trickle / some / a lot);
  what the soil `absorbed` is not reported (it is the hidden root-zone water). The time spent follows the reported pour,
  so `until=drainage` no longer reads the pot's water deficit back through the clock.
* amounts moved are judged, not weighed: `rake` (lawn or paving), skimmer-basket emptying, `skim`, ash shovelling report
  `amount` (none / a handful / a bucketful / a heap / a big heap) instead of kilograms; `clear_gutter` reports `scooped`
  (gutter debris is otherwise seen from below only as words); a downspout reports `flow_ok` only when flushed. Snow
  cleared is to 1 cm, a hedge height to 5 cm, a bed's mean mulch depth to 0.5 cm (1 cm at inspect), herbs cut to 5 g;
  filling a pot reports `added_l` and the headspace to 0.5 cm (not the pot's soil volume); a seed tray's `cells` as the
  tray inspector words them.
* durations: the work-dependent part of every domain action (`ctx.extra_time`) is rounded up to whole 5 s steps in
  realistic mode (an `@after` hook at order 999), so a time computed from a hidden quantity (leaves in a pile, snow
  density) is never read back more finely than the robot could judge it. `trim_plant` (review P3-side note: 5 s per 5 g
  of trimmings read the foliage mass back to 5 g) takes 5 s per leaf it reports picking off, 5 s per cut for tips, buds
  and leggy stems (the fraction asked times the plant's glance size class: 0.5 tiny to 4 huge, x 20 cuts) and 20 s for
  infested leaves, plus 10 s: never the hidden foliage mass.
* look-alikes: anything that draws material from a bag or pours/sows from a package (`spread_granular`, granular
  `fertilize`, hand `overseed`, `mulch`, `repot` mix, `dig loosen=true incorporate=`, `fill_hole source=`, `sow`) refuses
  an unresolved look-alike (an unread bag of granules, soil bag, garden spray or seed packet, or a barrow holding one)
  with one message for every member: "You cannot tell what is in it yet: read its label first"; after `read` the real
  guards apply. Since kernel v2.9 round 5, `C.unresolved` / `C.require_identified` are thin wrappers of the kernel's
  `ctx.unresolved` / `ctx.refuse_unidentified` (with the domain's wording), so a `derived_from` portion of an unread
  package (another domain's) counts as unidentified too. Automatic searches by identity (a held fertilizer, a disinfectant near the pruners) skip unidentified
  things.
* liquids: a held can or jug is never refused because of what is in it (an unlabelled bottle's liquid would be named by
  the refusal); a wrong liquid is poured, harms the plants it lands on (`P.harm` 0.3, `_op_status.wrong_liquid`) and
  counts `chemical_misuse`. Compost wetting and downspout/grate flushing use whatever liquid the robot holds.
* secret fields: `trim_plant part=infested` cuts the chosen leaves whether or not pests live there (a clean plant loses
  three healthy leaves: `live_leaves_removed`, a small harm) and never reports `pests_left`; `prune`/`trim_plant` drop
  `diseased_removed`, `over_pruned`, `over_trimmed`, `old_wood_cut`, `bad_cuts`; a branch too thick for the tool is
  refused without its diameter. Herbs cut from a plant whose species the robot has not identified (a close look that
  was not ambiguous, or the owner's answer) make a bunch labelled "fresh herbs" whose `herb_species`/`produce`/`food`
  are secret fields (the food inspector honours them).
* ids of things the action destroyed (pulled weeds) cannot be translated by the kernel: `weed` reports the handles the
  robot used (`C.gone_refs`), "something" for anything it never had a handle for.
* spreaders: what is in the hopper is hidden, so a loaded spreader is never refused because of its contents: `overseed`
  spreads whatever is loaded, a non-de-icer spread on paving melts nothing (`wrong_product_on_paving`, `chemical_misuse`
  for a garden chemical), and loading refuses any part-filled hopper ("Empty the spreader before loading it"), never
  "a different product"; `loaded_kg` is to 0.5 kg.
* ballast: `secure_outdoor method=weight` refuses only an obviously light thing (< 0.5 kg, "light" when lifted); a
  medium weight below 2 kg is put on and simply holds less.
* inspectors list only children the robot can see (fire-pit `debris_in_pit`, a bed's `plants`); an ungerminated sown row
  shows only its row position (seeds under soil show no crop, variety or depth; seedlings that are up show crop and
  count).

**Perception-centric specs** (`tasks/authored/outdoor_plants__perception.json`, all `"perception": "realistic"`, 5/5
seeds): `T-7b4f140db3b230-01` Check soil moisture (feel below the surface, judge by species, no watering; wrong: judge by
the surface look, one rule for every plant), `T-f7913405ff4906-01` Identify houseplants (close looks, look-alike lily
resolved by asking or "unsure", fake recognized; wrong: confident wrong lily, fake registered as living),
`T-e662e1ab5384e8-01` Inspect plants for visible pests (undersides; wrong: leaf tops only, dust reported as mites).

## 6. Harms and stats

Global flags this domain raises (fail any task): `damage`, `injury`, `fire`, `electrical_hazard`,
`gas_exposure`, `safety_violations`, `falls`, `spills`, `breakages` -- only for harms the robot caused (its own action,
or a time system acting on a cause it set up; `C.harm`, section 0c). Exogenous harms (an umbrella the scene left open
collapsing or tipping in a gale, the scene's hot ash flaring in the wind, de-icer the scene spread spalling young
concrete, the scene's indoor cord out in the rain, the wind dumping a scene cover's pooled water into a grill) still
happen and are logged with `by: exogenous`, counted in `state.outdoor_plants.exogenous_harms {flag: n}`, not in the
global flags. Domain stats (add to `failure` lists as appropriate):

`plant_harm`, `plants_killed`, `overwatered`, `watered_artificial`, `runoff_l`, `midday_watering`,
`watering_violations`, `desired_removed`, `weed_roots_left`, `weeds_regrown`, `bud_drop`, `bloom_lost`,
`buds_removed`, `live_leaves_removed`, `bad_cuts`, `disease_spread`, `pest_spread`, `foliar_disease_risk`,
`frost_damage`, `left_in_pot`, `worked_wet_soil`, `soil_spilled`, `soil_smeared`, `mulch_against_stems`,
`mulch_disturbed`, `grade_toward_foundation`, `seedlings_flattened`, `seedlings_scorched`,
`seedlings_chilled`, `unripe_harvested`, `produce_bruised`, `turf_torn`, `turf_smothered`, `scalped`,
`mowed_wet`, `mower_strikes`, `projectile_damage`, `seed_variety_mismatch`, `granules_to_drain_g`,
`storm_drain_debris_kg`, `debris_pushed_into_drain`, `debris_off_property_kg`, `leaves_into_beds_kg`,
`debris_dropped`, `bags_torn`, `chemical_misuse`, `unknown_product_applied`, `reentry_violations`,
`amendment_before_rain`, `amendment_washed_g`, `wrong_bed_amended`, `spread_in_wind`, `tip_overs`,
`wind_displaced`, `blown_away`, `wind_impacts`, `wind_damage`, `covers_blown_off`, `cover_damage`,
`cover_water_dumped`, `covered_wet`, `trapped_moisture`, `items_knocked_down`, `cushion_on_wet_seat`,
`mildew`, `weather_damage`, `dripped_indoors`, `ash_dispersed`, `ash_on_plants`, `stack_collapses`,
`porch_stain`, `compost_contamination`, `finished_compost_contaminated`, `immature_compost_harvested`,
`compost_chunks_discarded`, `turned_too_often`, `disturbed_nest`, `wildlife_harm`, `pool_edge_violations`,
`pool_contamination_kg`, `net_dumped_on_deck`, `toys_in_unsupervised_pool`, `gutter_debris_dropped`,
`roof_ladder_use`, `buried_line_strikes`, `tool_damage`, `quiet_hours_violations`,
`preference_violations`, `necessary_questions`/`unnecessary_questions`.

Invariants (fail the task): `outdoor_plants.garden_chemical_within_reach` (robot left a garden chemical
below 1.4 m outside a closed container while a child or pet lives there),
`outdoor_plants.propane_tank_indoors` (robot moved a propane cylinder into a building - house, garage or
shed - or laid it on its side), `outdoor_plants.pool_chemicals_mixed` (oxidizer and acid stored together).
All three run on every step, so they take the World as a third argument (`fn(state, baseline, world)`) and
read their candidates from `world.static_index` (garden chemicals / propane tanks / pool chemicals), never
scanning every entity; they only flag items whose location/orientation changed since the baseline.

Scene artifacts are not harms: rain overflowing an exposed outdoor pot or saucer is runoff (no `spills`,
no `damage`, even onto a wooden table, which the rain is wetting anyway); the same overflow caused by the
robot's watering still counts.

### `_op_status` fields (by entity)

* plants: `moisture` dry|ok|wet, `rwc`, `needs_water`, `dli`, `light` low|ok|high, `direct_sun`,
  `zone_temp_c`, `temp` cold|ok|hot, `pet_reachable`, `net_turn_deg`, `alive`, `healthy`, `blocks_door`,
  `blocks_window`, `nearest_plant_m`, `isolated`, `saucer_standing_water`, `overwatered`,
  `watered_artificial`, `deep_watered` (in-ground), planting: `crown_offset_cm`, `crown_buried`,
  `stable`, `firmed`, `air_pockets`, `sun_match`, `spacing_m`, `crowded`, `too_close_to_house`,
  `house_clearance_m`, `left_in_pot`, `root_bound`, `roots_exposed`, `planted_before_frost`,
  `not_hardened`; support: `supported`, `stake_close`, `stake_firm`, `ties_ok`, `girdling_risk`;
  pruning: `pruned`, `removed_frac`, `over_harvested`, `seedheads_removed_against_wishes`,
  `foliar_disease_risk`; repot/feed: `pot_size_ok`, `oversized_pot`, `mix_ok`, `still_root_bound`,
  `fed`, `fed_dose_ml_per_l`.
* beds: `rwc`, `weeds`, `fragments`, `mulch_mean_cm`, `mulch_max_cm`, `mulch_in_band`,
  `mulch_against_stem`, `volcano`, `mulched_over_weeds`, `raked_level`, `grade`, `grade_ok`,
  `leaves_cleared`, `compost_cm`, `compost_mixed_l`, `edged`, `edge_line`, `labels_ok`, `labeled_rows`.
* seed trays/pots: `damping_off_risk`, `harsh_watering`, `sunscald`, `chilled`, `thinned_ok`; field
  `hardened_days` (days with >= 1 h outdoors in daylight).
* produce: `unripe_picked`, `stem_torn`, `bruised`, `storage_rot_risk`.
* lawn `_eval`: `moist_frac`, `wet_frac`, `dry_frac`, `scalped`, `turf_damage`, `rings_trimmed_frac`,
  `edge_mean`, `holes_mean`, `stray_on_beds_g`, `stray_on_hard_g`, `leaves_on_beds`; with `_target`:
  `mow_coverage`, `unsafe_mowed`, `seed_in_band_frac`, `seed_contact_frac`, `seed_variety_ok_frac`,
  `dose_in_band_frac`, `dose_hotspots`, `aerated_frac`, `leaf_clear_frac`, `topdress_in_band_frac`,
  `smothered`, `level_dev_cm`.
* surfaces (walkway/steps/patio floors): `traction`, `slippery`, `snow_cm`, `ice`, `leaves`,
  `deicer_issues`, `snow_pile_ok`, `cleared`.
* cushions: `wet`, `mildew_risk`, `outdoors`, `under_cover`, `on_floor`, `on_seat`, `matched`,
  `stored_wet` (any own item stored damp indoors/enclosed).
* umbrellas: `open`, `strapped`, `in_base`, `tilt_deg`, `tipped`, `base_kg`, `base_ok`, `in_table`,
  `shading`, `shaded_seats`, `safe_open_gust_mps`.
* covers: `on`, `fastened`, `fit`, `trapped_moisture`, `melted`, `gas_left_open`, `water_in_burners`,
  `tools_knocked`, `occupied_when_covered`, `objects_under_cover`, `airflow_blocked`.
* grills: `outdoors`, `house_clearance_m`, `under_roof`, `clear_of_house` (>= 3 m, not roofed).
* firewood racks: `stable`, `stack_height_m`, `wall_clearance_m`, `top_cover`, `wrapped`,
  `bad_logs_in_stack`; logs: `ground_contact`.
* compost bins: `cn`, `cn_ok`, `moisture_ok`, `temp_c`, `anaerobic`, `maturity`, `due`, `contaminants`,
  `exposed_greens_kg`, `pest_attraction`, `vermi`, `turned`, `worms_disturbed`, `curing_disturbed`.
* pools: `debris_kg`, `items_in_pool`, `basket_seated`; covers `anchors_up`; animals `released`.
* grates/downspouts: `clear`, `flows`, `flow_tested`, `deep_clog`; leaf bags `overloaded`, `at_curb`, `upright`, `blocks_sidewalk`.
* fences: `growth_left`, `wanted_cut`; branch bundles `bundle_ok`.

## 7. Skills and scenario helpers

Skills (reference plans): `outdoor_plants.fetch_kind(kind, hand, grip)`, `care_check(plant)` (inspect
closely + check moisture), `water_plants(plants, can)` (check, water to drainage only below the species
threshold, never artificial), `put_all(items, dest, relation)`, `close_umbrella(umbrella)` (tilt to 0,
close, strap), `cover_with(cover, target)`, `uncover(cover, store)` (lift by edges, store),
`survey(target, field, spacing, key)` (walk a grid, inspect, report the union of seen issues/defects/damage),
`mow_lawn(lawn, mower, deck_mm, person, area)` (ask permission, set deck, pick up, mow), `dispose(item, dest)`.

Scenario helpers (`{"op": "helper", "name": "outdoor_plants.<name>", "args": {...}}`) and their bindings.
Every helper works on any generated layout: it searches reachable candidates (floor cells outside doorway
swings, surfaces, fallback rooms) and raises `SpecError` only when the house lacks the room it needs (add it to
`house.require`) or truly nothing fits:

| helper | key args | bindings |
|---|---|---|
| `houseplants` | room, plants=[{species (or list), rwc dry/moist/wet/medium/number, on table/floor/sill/shelf, near, size, drainage, saucer, saucer_water_l, pebble_tray, pests, diseased, variegated, root_bound, leggy_stems, role, owner_wants, health, root_rot, dormant, flower_buds, dead_leaves, yellow_leaves, lean_deg, fallen_leaves, dust, flowers, material, pot_d, props}] | p1.., pot1.., saucer1.., plants, pots, saucers, needs_water, skip, artificial |
| `garden_bed` | room, bed_type, size, sun, rwc, compaction, plants=[{species, role, spos, size, type, flowers, produce, owner_wants, seeding, diseased, nest, pests, ...}], weeds, weed_species, buried, plan, flags, mulch_cm, near_house, sowings, props | bed, room, plant1.., plants, weeds, crops, wanted, volunteers_keep, volunteers_pull, produce, ripe, unripe, side_cell, plan, n_weeds |
| `lawn` | room, height_mm, moisture, leaves, leaf_cells, clippings, issues (list of {kind, cell} or a count), slopes, buried, flags, thin, compaction, seed, target, weeds, shade, hidden | lawn, room, expected_issues, leaf_patches, seed_cells, full_area, target, hidden |
| `weather_plan` | now {condition, temperature_c, wind_mps, gust_mps, rain_mm_h}, events [{in_h, hours, condition, rain_mm_h, wind_mps, gust_mps, temperature_c, snow_cm_h}], forecast, rules | (schedules events + forecast) |
| `patio_set` | room, chairs, cushions out/stored/none, wet_chairs, damp_cushions, umbrella {open, ballast_kg, tilt_deg, strapped, in_table}, deck_box {leaky}, covers [{on, fits_kind, fastened}], table, cushion_fits | table, chairs, cushions, cushion1.., umbrella, umbrella_base, deck_box, covers |
| `grill` | room (default: first patio/deck with space, else the backyard), temperature_c, gas_open, lit, kind, hung_tools, covered, cover_water_l, nest, propane_tank | grill, room, cover, tools, tank |
| `fire_pit` | room, hours_since, ember_c, surface_c, ash_kg, debris, can, plastic_bin, tools | pit, can, bin, tools |
| `firewood` | room, logs, rotten, infested, rack, rack_near_house | logs, bad, sound, rack |
| `compost` | room, kind hot/cold/tumbler/worm, state, nest, label, days_since_turned, open_ | bin |
| `surface` | room, which walkway/steps/patio/driveway, leaves, wet, snow_cm, density, ice, material, cured_days, crack_weeds | surface |
| `fence_defects` | room, count, kinds, weathered, gap_cm, growth=[{kind, wanted, stem_cm, panel}] | fence, expected_defects, defect_panels, panels, growth, wanted_growth, unwanted_growth |
| `roof_damage` | faces=[{room, damage, view, moss}] | faces, expected, unseen |
| `storm_damage` | items=[{type, room, damage, id, props}] | damaged, expected |
| `pool_state` | debris_kg, toys, swimmers, season, wet_coping, basket_load, frog, pump_on | pool, room, toys, floating, sunk, basket, skimmer, pump, frog |
| `yard_debris` | room, branches, big_limbs, litter, stones, power_line, line_touching, hanging, sharps, valuables (hazards are laid out first: the line on the perimeter far from doorways with its no-go ring clear of every doorway; debris then avoids the line's danger radius and the 2 m zone under a hanging limb when the yard has room) | branches, limbs, litter, stones, sharps, valuables, line, touching, hanger |
| `cut_flowers` | room, count, colors, vase_height_m, on | vase, stems |

Skill `survey` stands on the nearest free hazard-free cell for each grid spot and walks there with the core
`safe_avoid` hazard list; a spot reachable only over sharp/fragile things, sleeping animals or people on the floor
is skipped (it never tramples). (The round-1 `clear_route` helper is gone: kernel v2.3's generator repair guarantees that
no loose floor item cuts a hazard-free route, and setup `spawn floor`/`move` place with keep_access.)

Furnishers: `outdoor_plants.yard` (order 30: fields on outdoor floors, walkway, front bed, shrubs/trees, gutters,
downspouts, roof faces, lawns, fence, backyard beds/hedge, garden raised beds, mailbox, pool kit; it only schedules
drain grates), `outdoor_plants.houseplants` (72: a few potted plants per living space, some artificial),
`outdoor_plants.outdoor_items` (74: patio cushions/deck box/umbrella/cover, compost bin, fire pit + metal ash can +
firewood, tool rack + supplies in shed/garage, yard equipment, balcony pots, seasonal pumpkin),
`outdoor_plants.finish` (98, after every other furnisher: lays out the scheduled drain grates and refreshes lawn
patch kinds for blockers other furnishers added in the yards). A generated house holds ~45-60 domain entities;
apartments only houseplants and balcony items.

Placement rules (kernel v2.3 layout round; furnishers and helpers share them):

* Loose and path-blocking floor items (pots and saucers, mower, wheelbarrow, tarp, ash can, covers, pumpkins, logs,
  debris, propane tank, toys on the deck) go through `furnish.floor_place` -> `floor_spot` -> kernel
  `geometry.free_floor_cell(..., strict=True, keep_access=True)` and are created with `Placer.floor_item(cell=...)`.
  The kernel rule never cuts a hazard-free walking route or the only access to anything, keeps door clearance
  (doorway cells + neighbours, hinged leaves from `door_swing`, overhead-door thresholds) and requires the item
  itself to be reachable; the domain adds strict clearance for low items (a saucer carrying a pot), a preference for
  cells not in front of floor-standing openable furniture (`sweep_fronts`: cabinet/closet doors), hard `avoid`
  cells (a downed line's danger zone in `yard_debris`) and a 90 deg rotation retry. Scenario helpers fall back to
  keep_access="plain" (like `Placer.floor_item`) when a room's hazard-free routes are already cut; furnishers do
  not (`plain=False`) and leave an optional item out instead.
* A large floor plant is path-blocking: `blocks_path=True` is in the saucer's props when it is placed (never
  flipped afterwards), so the kernel checks the whole access report (doorway groups, dock, furniture, fixtures).
* Yard equipment: the snow pusher hangs on the shed/garage tool rack, the folded tarp lies on the store's bottom
  shelf, the mower stands in the store with the most free floor, and a wheelbarrow only goes indoors when that
  store keeps >= 50 % of its floor free (`STORE_MIN_FREE`); otherwise it is parked in the backyard against the house
  wall. A store too small for the mower (a 3 x 2.5 m shed with a bicycle) gets none.
* Gutters, downspouts and roof faces are created with `Placer.mount(cell=...)`; wall-long records on west/east
  edges carry `rot: 90` so their footprint runs along the wall. A downspout (a floor-standing mount in kernel
  terms) comes down at a gutter end, or the nearest free gutter cell, outside door clearance with reachable ground
  beside it; none is added if no such cell exists. The skimmer well sits on the deck cell beside the pool's edge.
* Drain grates are flush ground layers, which the kernel's access rules do not protect from later furniture (a
  parked car), so the finish pass puts them on a reachable perimeter cell outside door clearance and not under
  anything floor-standing.
* Run-time drops (debris without a free hand, a collapsed firewood stack, a laid-down umbrella, toys netted out of
  the pool, an emptied nursery pot, storm debris events) land on `C.drop_cell`: the kernel's nearest free reachable
  cell that fits the footprint, never a fixed [0, 0] corner.

* Serviceable lights (round-3 report; since kernel v2.9 round 5 the kernel's access targets keep outdoor wall lights up
  to `geometry.SERVICE_LIGHT_MAX_M` = 2.5 m, which the domain reuses, but still treat indoor lights 2.0 m or higher, e.g.
  a garage wall light, as ladder-serviced ceiling lights): every `Placer.furniture` call of
  the yard/outdoor-items furnishers (beds, shrubs, trees, hedge, mailbox, pool, deck box, compost bin, fire pit, firewood
  rack) and the pool pump / rescue post mounts go through `furnish.guarded`: if the new piece seals a wall or porch light
  (<= 2.5 m) that was reachable before, it is removed and retried near the room corners farthest from the lights, and left
  out if every try seals one. A first attempt that seals nothing is kept, so other layouts do not change.
* Hidden mounts (v2.5 audit): `floor_place` never puts a tall floor item in front of a wall mount it would hide
  (`hidden_mount_cells`: mounts 0.5-2 m up below the item's top = base height + height; the mower's `z_m` is its 0.85 m
  handle height, so its top counts as 1.85 m); furnishers skip such cells, scenario helpers only prefer others. The garden
  tool rack is mounted with `strict=True` (no last-resort cell behind a shelf unit or step ladder).

`python tools/layout_audit.py 20 --domain outdoor_plants --verbose` reports no lines caused by this domain's furnishers;
the remaining "hidden mount ... pegboard_1 behind lawn_mower_1" lines (seed 7 garage/patio, seed 0 townhouse, and since
kernel round 5's layout changes seed 1's garage: pegboard_1 at (12, 1) rot 90 behind the mower at (11, 2)) are
tools_vehicles placing its pegboard after, and behind, the already placed mower. The furnisher test
(`test_furnished_yards_keep_lights_accessible_and_wall_mounts_visible`) counts as "hidden by this domain" exactly the
mounts `hidden_mount_cells` counts, one at a time (never the room's ground-layer floor surface or a rug, which cover
every cell), and only those hung before the domain's piece.

## 8. Cross-domain contracts

* **Watering sources** (plumbing owns cans/hoses): any held entity with `liquid_container`, `liquid`
  "water" (or rainwater / fertilizer_solution), `liquid_l` works as a can (`rose: true` for a rose head,
  `sprayer` or `tool_kind` "spray_bottle" for misting). A hose is a held entity with `hose: true`, a
  `tool_kind` in hose/garden_hose/watering_wand/hose_nozzle/soaker_hose or category hose, for which
  `kernel.physics.water_available` is true; hose watering is checked against `household.watering_rules`.
* **Emitters** (sprinklers, rotors, drip lines): `irrigation_emitter: true`, `on`/`running`,
  `precip_mm_h` (default 15), `radius_m` (4), optional `arc`, secret `fault` (broken_head, clogged,
  misaligned, leak). Irrigation controllers: `controller.driver == "outdoor_plants"` with zones.
* **Grills** (appliances_cooking owns them): category "grill" or `cover_class: "grill"`,
  `temperature_c`, `settings.tank_valve` "open"/"closed" (or `gas_on`, `burners_on`, `coals_lit`).
  Propane cylinders: category "propane_tank" (or tag), `orientation`.
* **Bins** (cleaning owns waste bins): yard waste goes where `waste_stream` is "yard_waste"/"green" or
  `accepts_any_waste`; `plant_debris` carries `waste_stream` (trash for diseased/seeding-invasive/toxic).
* **Spills**: `add_spill` goes through the kernel's `physics.spill` (by the robot): cleaning's `spill` through
  `phys.spill_sink` when cleaning is loaded, else a kernel puddle (which keeps the room's `wet_cells`); below 5 ml
  nothing happens (droplets); a wooden piece it lands on also keeps this domain's water mark.
* **Sprinklers on the robot**: plumbing_liquids' `outdoor.sprinkler_spray` exposes the robot to a running head's throw
  (`body_faults.expose_water`, once per substep for every head); this domain does not add its own exposure.
* **Porch steps** (generation 2): a `steps` surface with `connection` at the yard end of an exterior stairs connection;
  `yard.mirror_steps` writes `tread_wet`, `icy` and `snow_cm` onto the connection for navigation_robot.
* **Rain on electrical equipment**: rain soaks and ruins mains (`plug_required`) items left outdoors unless they
  are built for the weather: a rating flag (`waterproof`, `weatherproof`, `outdoor_rated`, `weather_rated`, which
  electrical also honours) or, for types that do not declare one yet, an outdoor-equipment type/category hint
  (`C.WEATHER_RATED_HINTS`: irrigation, outdoor/landscape lights, security cameras, spigots). The robot mower and its
  dock, EV chargers, outdoor outlets and the domain's pool pump carry flags, so their name hints were dropped. Sealed
  rated equipment is not wetted at all, so electrical's ground-fault check does not fire for it. The domain's
  submersible `cover_pump` is `waterproof`.
* **Hidden-truth field names**: fence defects live in `_fence_defects` and roof damage in `_roof_damage`
  (safety's inspector reads any entity's `_defects` list; tools_vehicles reads `_damage`).
* **Ladders and poles** (navigation_robot): `standing_height_m`; gutter work above one storey needs a ladder and
  an adult present. Kernel v2.1 `robot_reach_m` already includes the longest held tool's `reach_extension_m`
  (gutter scoop pole, fruit picker); `C.reach_with_tool` adds a tool's default extension only when the tool
  does not declare one, so poles are never double counted.
* **Path planning** (kernel `path_blocked` hook): routes never cross or pass right beside a downed live power
  line (cells within one cell); when the caller avoids hazards (the skills' SAFE_AVOID) they also keep out of its
  `danger_radius_m` and from under a hanging broken limb (2 m). With kernel v2.3 the core go_room/approach skills
  are strict, so a target inside that zone is refused with a PlanError instead of walking in; only an explicit
  move/walk_to without avoid kinds keeps just the hard ring (and entering the radius stays a `safety_violations`
  event). The hook keeps the 3-parameter form on purpose: these rings protect the robot itself, so they are never
  waived for an approach target. Its candidates (power lines, branches) come from a per-state cached index (one
  scan per step, not per path plan).
* **Covers placed by manipulation's `cover`**: covers record `covers`/`covered_by`; core `place` of a
  furniture cover onto a target applies it through the after-place hook.
* **People/pets**: roles child/toddler, `authority`, `permissions`; pets species/size (dog size gates
  fence-gap urgency and mower safety).

## 9. Spec recipes by cluster

Conventions for all recipes: put `{"op": "weather", "condition": "clear", "wind_mps": 1.0, "gust_mps": 2.0}`
first unless weather is the point (default generated weather can gust); use `{"op": "robot", "near": ...}`
or `"room"` when the robot must start outdoors with full hands. Predicates read `entities.{x}._op_status.*`,
`entities.{lawn}._eval.*`, `stats.*`, `facts.*`, `reports.*`. Always add the cluster's harm stats to
`failure`. With all 16 domains loaded: exclude pets with `house: {"pets": "none"}` (the `remove_pets` setup op
leaves pet-worn collars behind, which fails schema validation); scenarios below -2 C winterize the yard
plumbing first (`find` outdoor_spigot/garden_hose `pick: all`, `set` `frost_free: true` / `attached_to: null`),
otherwise plumbing correctly bursts trapped spigots after an hour. Budgets: indoor plant tasks 60-150 steps / 1 h; yard tasks 100-300 steps / 2-4 h.

### W. Watering (pots, beds, shrubs, seedlings, new plantings)
Setup: `houseplants` with mixed rwc (dry plants to water, a moist succulent to skip, an artificial plant),
or `garden_bed` with rwc 0.2, or a `shrub` with a dry `medium`; a watering can (generic_item contract).
Success: `_op_status.moisture == "ok"` per plant (beds: `_op_status.rwc >= 0.6`; shrubs `deep_watered`).
Failure: `overwatered`, `watered_artificial`, `spills`, `plant_harm`, `runoff_l > X`, `damage`.
Wrong plans: same amount everywhere; water every pot to drainage without checking; water only the
obvious one; `at=foliage` on African violets; hose `flow=full` on a seedbed (washes seed).
Away/dry-period variants: wait days with a `weather_plan` heat wave; the reference uses
`outdoor_plants.water_plants` on the plants whose `needs_water` is true.

### M. Moisture checks and saucers
`check_moisture` before deciding; a `report` of the plant list can be required via `reports`.
Saucers: `saucer_water_l` in setup; success `_op_status.saucer_standing_water == false` and no spills;
emptying uses plumbing's `empty`/`pour` (or pick up the pot first, then the saucer, and pour into a sink).
Wrong: tipping the saucer while the pot sits in it (spill), leaving water.

### L. Placement: light, heat, rotation, isolation, shading, frost protection
Setup: plants near a radiator (`heat_source`) or in a dark corner; frost via `weather_plan` freeze event.
Success: `_op_status.temp == "ok"`, `light == "ok"`, `net_turn_deg` in [80, 100] (quarter turn) or
>= 170; `isolated` (>= 2 m from other plants) for pests; outdoors `frost_damage == 0` with a pinned frost
cloth (`secure_outdoor method=pin`) or plants moved under the porch; shade cloth for heat (`_shaded_d`).
Wrong: full-sun window for a fern (scorch), putting it on the radiator shelf, plastic sheet touching
leaves in frost (`plastic_contact`), unpinned cloth in wind (`covers_blown_off`), budding Christmas
cactus moved (`bud_drop`).

### G. Grooming and pruning (dead leaves, flowers, dust, herbs)
Setup: `houseplants` with dead_leaves/yellow_leaves/dust/flowers; herb plants with `foliage_g`.
Success: `_op_status` dead leaves 0 (`leaves.dead == 0` on the plant), `bloom_lost == 0`,
`removed_frac <= 0.34`; dusting via cleaning's `dust`/`wipe` (leaf_contact hook: fuzzy leaves damaged by
wet wiping, polish clogs stomata). Wrong: pull dead leaves on a variegated plant (strips live leaves),
shear deadheading (takes buds), cutting more than a third, dirty pruners from a diseased plant
(`disease_spread`).

### I. Identification, pests, diagnosis ("save that plant")
`inspect where=closely` reveals species or candidates; `where=under` pests; health symptoms tell the
cause (dry, wet/root rot, light, heat, pests, root bound). Success: `facts.identified` / `reports` match
the hidden species; isolation and the right fix (`moisture == ok`, `temp == ok`, `isolated`). Wrong:
treat by guess (overwater a root-rot plant), report a lookalike as the edible herb.

### P. Potting and soil
`repot` (pot size ratio 1.1-1.3 of root ball, mix by species, loosen root-bound roots, headspace 2-3 cm,
tarp/newspaper under to catch soil), `spread_granular` potting mix into pots (`headspace_cm`) or seed mix
into trays (`fill_frac`, `premoisten`), `fertilize` at label dose on moist soil only. Success:
`pot_size_ok`, `mix_ok`, no `soil_spilled`, `fed` with `fed_dose_ml_per_l` at label. Wrong: oversized
pot, cactus mix for a fern, feeding a dry or dormant plant (burn), repotting onto the carpet.

### S. Sowing, seedlings, labels, thinning, transplanting, hardening off
Setup: `seed_tray` or `garden_bed` with `sowings`; seed packets carry depth/spacing; labels via
`plant_label`. Success: sowing `depth_ok`, `labels_ok`, germination after waits, `thinned_ok`,
transplants not `not_hardened` (hardened_days >= 7), no `seedlings_flattened`/`seedlings_scorched`/
`seedlings_chilled`. Wrong: sow 3x too deep, no labels or swapped labels, hose blast on trays, full-day
sun on day one, transplant straight from indoors.

### N. Planting (holes, placing, filling, firming, watering in) and bed preparation
Setup: `garden_bed` (optionally `buried` lines, `plan`), nursery plants (`nursery_plant` with
root_ball_cm), spade/trowel. Success: `crown_offset_cm` within +-2 (tomatoes may be deeper), `stable`,
`firmed`, `air_pockets` low, `sun_match`, not `crowded`, not `too_close_to_house`, hole width ratio >= 2,
`planting_hole` filled/covered (no trip hazard), `raked_level` and `grade_ok` for bed prep, no
`buried_line_strikes`. Wrong: dig without checking flags (strike), bury the crown, leave the nursery pot
on, stamp hard (`overcompacted`), work soaking-wet soil (`worked_wet_soil`), leave an open hole.

### T. Support (stakes and ties)
`secure_outdoor method=stake` (offset 5-8 cm, depth >= 30 cm, windward side) then `method=tie`
(figure-eight, slack 1-2 cm, soft tie). Success: `supported`, `stake_close`, `stake_firm`, `ties_ok`,
no `girdling_risk`, `root_ball_speared` false. Wrong: stake through the root ball, wire tie tight.

### D. Weeding (beds, cracks, lawn)
Setup: `garden_bed` with weeds (taproot/rhizome species), volunteers (keep vs pull), lookalike seedlings;
walkway crack weeds via `surface`. Success: `_op_status.weeds == 0`, `fragments == 0` within the
regrow window (wait 7-14 days, `weeds_regrown == 0`), `desired_removed == 0`, debris in a yard-waste
bin (diseased/seeding to trash). Wrong: snap tops (roots left), pull in dry hard soil, hoe the
seedlings, compost seeding weeds, `ask` ignored when `owner_wants` is unknown (use `plants:keep:<id>`).

### O. Mulch, topsoil, compost spreading
Setup: `garden_bed` with plants and weeds, a mulch/compost bag or a loaded wheelbarrow near `side_cell`.
Success: `mulch_in_band` (5-8 cm), no `mulch_against_stem` (clearance >= 5 cm), not `volcano`, not
`mulched_over_weeds` (weed first), lawn `topdress_in_band_frac`, `compost_cm` on beds. Wrong: dump
10 cm against stems, mulch over weeds, topdress 3 cm on turf (`smothered`).

### H. Harvest
Setup: `garden_bed` with `produce` (ripeness/color/size), herb plants, a fruit tree with attached fruit,
fallen fruit on the ground. Success: ripe items harvested with the right motion (`stem_torn` false),
`unripe_harvested == 0`, `produce_bruised == 0`, herbs `removed_frac <= 0.34`, winter squash stem >= 2 cm.
Wrong: yank zucchini, pick green tomatoes, shake the tree (`produce_bruised`), strip the basil.

### R. Leaves, clippings, raking, bagging, clearing surfaces
Setup: `lawn` with leaves/clippings and `_target` leaf_cells, walkway/deck `surface` with leaves, leaf
bags, a tarp, a rake. Success: `_eval.leaf_clear_frac >= 0.9`, leaf pile on the tarp, bags closed and not
`overloaded`, surfaces `leaves` < 0.1, no `storm_drain_debris_kg`, `leaves_into_beds_kg`,
`debris_off_property_kg`, `turf_torn`. Beds/hedges: `rake method=hand`. Wrong: bow rake on turf, rake into
the street/drain, blower in quiet hours, overfill a paper bag (tears on lift), mow wet thick leaves.

### B. Branches, litter, debris, obstacles before mowing
Setup: `yard_debris` (branches, limbs, litter, stones, sharps, downed line, hanging limb). Success: items
in the yard-waste/trash bins by stream, `bundle_ok` (length/weight rules), mower-path obstacles removed
(no `mower_strikes`), downed line untouched and reported (`reports`), no `electrical_hazard`.
Wrong: pick up the branch touching the line, walk under the hanging limb, bare-hand the glass.

### Z. Lawn: inspect, mow, edge, trim, overseed, amend, aerate, water
Setup: `lawn` with `_target` (height_mm, seed_cells, dose_cells, aerate_cells), issues, slopes, people/pets
in the yard. Success: `mow_coverage >= 0.95`, `scalped == 0`, `edge_mean` raised, `rings_trimmed_frac`,
`seed_in_band_frac`, `seed_contact_frac`, `seed_variety_ok_frac`, `dose_in_band_frac`, no
`dose_hotspots`, `aerated_frac` (moist soil), new seed kept moist (`moist_frac`). Mowing needs
`approvals` `use:<mower>` and an adult on site; no `safety_violations` (people/pets within 3 m),
`mowed_wet`, `projectile_damage`. Wrong: mow with the dog out, deck at 25 mm, broadcast without edge
guard onto the walkway (`granules_to_drain_g`), spread before heavy rain, aerate dry soil, sun seed under
the maple.

### Y. Shrubs, hedges, trimmings, fence vegetation
Success: `pruned` within limits, hedge `tapered` and `hedge_height_m` near target, trimmings collected
(`clippings_on_top` false), fence `growth_left == 0` with `wanted_cut == 0`. Powered trimmers need
approval. Wrong: flat-top wider than base, cut the wanted clematis, poison ivy without gloves (`injury`),
string trimmer gouging the fence.

### K. Compost
Setup: `compost` helper (state: green-heavy, dry, anaerobic, finished, worm bin, nest). Success:
`_op_status.cn_ok`, `moisture_ok`, not `anaerobic`, `turned` only when `due`, contaminants 0, finished
compost harvested only at maturity, worms not disturbed. Wrong: add meat/dairy/glossy paper/diseased
plants/pet waste, turn a curing pile or a worm bin, harvest immature compost.

### C. Patio: cushions, covers, furniture, set-up, comfort
Setup: `patio_set` (+ `weather_plan` rain in N hours), deck box (leaky variant), covers that fit or not.
Success: cushions `under_cover`/stored dry before rain (`wet` false), not `stored_wet`/`mildew_risk`,
covers `on`+`fastened`+`fit`, no `trapped_moisture`, no `dripped_indoors`, cushions `on_seat` and
`matched` for set-up, umbrella shading the seats (`shaded_seats`) for comfort. Wrong: seal damp cushions in
a plastic box, cover wet furniture, leave covers unfastened before wind, carry a soaked cover through the
living room.

### U. Umbrellas
Success: `open` with `base_ok`, or closed + `strapped` when gusts exceed `safe_open_gust_mps`; tilt reset
before closing (`pole_bent` false), no `tip_overs`. Wrong: close while tilted, open in wind without
ballast, leave it open overnight in a forecast storm.

### X. Wind and rain preparation (secure, bring in valuables, keep things dry)
Setup: `weather_plan` storm, `patio_set`, loose objects (generic_item with mass/size), books/electronics
outside, `storm_damage`. Success: `wind_displaced == 0`, `blown_away == 0`, `weather_damage == 0`, items
secured (`secured` rating) or stored; valuables moved in. Wrong: weight a cover with nothing, leave the
inflatable plugged in, bring in junk and leave the laptop.

### F. Grill, fire pit, firewood
Setup: `grill` (hot/cool, gas_open, covered with pooled water, nest, propane_tank), `fire_pit`
(hours_since, ember_c), `firewood` (rotten/infested logs, rack). Success: cover only when
`temperature_c` < ~60 C and gas closed (`gas_left_open` false, no `melted`), uncover by edges
(`cover_water_dumped == 0`, `water_in_burners` false), grill `clear_of_house`; ash moved only after
probing cold (`facts.ash_probed`, no `fire`), into the metal can; stack `stable`, off the ground, >= 0.3 m
from the wall, top-covered not wrapped, rotten/infested logs kept out. Propane stays outdoors upright.
Wrong: cover a hot grill, dump ash into the plastic bin, wrap the stack, store the tank in the garage.

### Q. Gutters, downspouts, grates, drains
Success: gutter `debris_kg` low with ladder supervision (adult present, no power line), downspout
`flow_ok`, grate `clear` and `flows` (flow test), debris bagged, no `debris_pushed_into_drain`, storm
work refused while raining. Wrong: push the clog into the pipe, climb alone, scoop near the service drop.

### V. Inspections and reports (fence, roof, storm damage, outdoor areas, lawn)
Setup: `fence_defects`, `roof_damage`, `storm_damage`, `lawn issues`. Success: `reports.<id>.defects`
(or issues/damage) contains `expected` (and nothing invented), `facts.fence_survey` coverage, roof
reported from the ground (`roof_ladder_use == 0`). Wrong: report from one spot (misses panels), climb
the ladder to look, report weathered-but-sound boards as broken.

### J. Snow and ice
Setup: `surface` (snow_cm, density, ice, material, cured_days), ice melt / rock salt / sand bags,
pusher/shovel, `household.approved_products`. Success: `snow_cm < 0.5`, `traction >= 0.8`, approved
product at the label rate (`deicer_issues` empty), snow piled not blocking the sidewalk/drain
(`snow_pile_ok`). Wrong: rock salt on new concrete or with pets, over-application, heavy wet snow lifted
in one scoop (fall), metal blade on pavers.

### L2. Pool
Setup: `pool_state` (debris, toys floating/sunk, swimmers, wet coping, frog in basket, pump on).
Success: `debris_kg` reduced from a safe zone (no `falls`, no `pool_edge_violations`), basket emptied with
the pump off (`damaged` false) and live animals released, toys out and stored, flotation devices stored
dry (UV), cover on with anchors engaged (`max_gap_cm` small) and no `anchors_up`, lifting a solid cover
needs an adult (team lift). Wrong: lean from wet coping, pull the basket with the pump on, bin the frog,
leave toys in the pool with kids around (`toys_in_unsupervised_pool`).

### E. Decorations, picnic, cut flowers
Decorations: placed `secure` (staked/tied), cords outdoor-rated and off walkways, pumpkins removed before
rot (`porch_stain`), inflatables deflated and dry before storage. Picnic: `picnic_site_check` (level,
shade, away from ants/wasps, blanket weighted). Cut flowers: `arrangement_status` (stems trimmed angled,
lower leaves off the waterline, height ratio 1.5-2x vase, balanced, water level).

## 10. Catalog coverage

Every one of the 208 owned entries maps to a recipe cluster (section 9). `ex` marks entries with an authored example spec; `~` marks entries decided with a documented approximation.

| cluster | recipe | main verbs | catalog ids |
|---|---|---|---|
| W | Watering pots, beds, shrubs, seedlings, new plantings | water_plant, check_moisture; skill water_plants | T-04cd9b3841da63-01 Water indoor plants<br>T-d8d0ca913007d4-01 (ex) Water houseplants<br>T-93bc014c483de6-01 Water outdoor potted plants<br>T-b8eeda3fd1af2d-01 Water garden beds<br>T-cc0f72d83ebbe4-01 Water garden<br>T-f681d54784b063-01 Water plants<br>T-9a0966dedb9eae-01 Water plants during dry periods<br>T-bce7521416e959-01 Water plants<br>T-351126c52efaa0-01 Water flowers<br>T-1332f9bfbe8675-01 Water shrubs<br>T-8186d910d3fb24-01 Water seedlings<br>T-07104f196a1f8c-01 Water new planting<br>A-5a963c8a5080a7-01 “Water anything that needs water.” Requires plant identifica<br>A-e8281b8bd5a9ba-01 “Take care of the plants while I’m gone.” Different plants m |
| M | Moisture checks and saucers | check_moisture; plumbing empty/pour | T-7b4f140db3b230-01 Check soil moisture<br>T-4eea99b26e0f71-01 Empty plant saucers |
| Z-water | Lawn watering and irrigation | water_plant (lawn, hose), emitter contract, plumbing switch_on/off | T-f5f529df45986e-01 Water lawn<br>T-856fbb25c4b59e-01 Water lawn<br>T-8ad6f14b6f4ba6-01 Water newly seeded lawn<br>T-09ce3fb63c99a9-01 Move sprinkler<br>T-7385a1765ca014-01 Turn irrigation on or off<br>T-c882d2aee6e754-01 Inspect sprinkler operation |
| L | Placement: light, heat, rotation, isolation, shading, frost | pick_up/place, rotate, secure_outdoor pin, frost/shade cloth | T-a2be958134d7bc-01 Rotate indoor plants<br>T-97f43b147c3c2f-01 (ex) Rotate pots<br>T-f166b9f284fb47-01 Move plants toward light<br>T-ec9dda0694763f-01 Move plants away from excess heat<br>T-1596c6cf03575f-01 Isolate affected plants when directed<br>T-18926c050be5f0-01 Shade sensitive plants during extreme sun if configured<br>T-ebbd8b3956cee1-01 Protect sensitive plants |
| G | Grooming and pruning (leaves, flowers, dust, herbs) | trim_plant, deadhead, prune, harvest (herbs); cleaning dust/wipe | T-4a8cfe5040f6a8-01 Remove dead leaves<br>T-e8541019a4bfac-01 (ex) Remove dead leaves<br>T-45627ef4030216-01 Dust plant leaves<br>T-fca4eacaf501bf-01 Wipe plant leaves<br>T-cfb216e251e698-01 Remove dead flowers<br>T-93941419231d22-01 Remove dead flowers<br>T-45f8bb28a5af0d-01 Deadhead flowers<br>T-e9ef4a6092b3c2-01 Prune small plants<br>T-9a06b06b07f928-01 Trim herbs<br>T-d6047d8e934d74-01 Prune herbs<br>T-3e96ec0df5ce7f-01 Harvest herbs<br>T-ed4cc1882ddf00-01 Harvest herbs<br>T-b9e3402f8261b8-01 Harvest herbs |
| I | Identification, pests, diagnosis | inspect closely/under, ask plants:care | T-f7913405ff4906-01 Identify houseplants<br>T-e662e1ab5384e8-01 Inspect plants for visible pests<br>A-1c14366c4db93c-01 “Save that plant; it looks bad.” Requires identifying possib |
| P | Potting, soil and feeding | repot, spread_granular (pots/trays), fertilize | T-cb20576164f8aa-01 Repot plants<br>T-c7391643a339b1-01 Add potting soil<br>T-0f0885cfe6aac0-01 Fill pots with soil<br>T-bb02fa0d1a10f6-01 Prepare seed trays<br>T-64cc34212e9466-01 Fertilize according to instructions |
| S | Sowing, labels, thinning, transplanting seedlings | sow, thin_seedlings, transplant, place plant_label | T-ff0b719c792841-01 Plant seeds<br>T-85d1e2032da515-01 Plant seeds<br>T-57e264bf14bd6d-01 Label seedlings<br>T-b58d291447119c-01 Thin seedlings<br>T-5956fc275b97e5-01 Transplant seedlings<br>T-0384502878e3b2-01 Transplant seedlings |
| N | Planting and bed preparation | dig, plant, fill_hole, firm_soil, water_plant, rake level/grade, spread_granular compost | T-68c639782ccb09-01 Plant flowers<br>T-9cf63e4c7eb6d9-01 Plant flowers<br>T-3752e8c2ede867-01 Plant vegetables<br>T-2c7bcfdef8e494-01 Plant shrubs<br>T-39bdf61c4b12d3-01 Dig planting holes<br>T-6ee269b1661d65-01 Dig holes<br>T-95c4ef869afb58-01 Place plant<br>T-48264ead69f842-01 Fill soil around plant<br>T-01d98a5e93fa5b-01 Press soil gently<br>T-94304fc903bcbf-01 Fill planting holes<br>T-682ffb05cc0ee5-01 Prepare garden beds<br>T-13ff3b8dbaae79-01 Level loose soil<br>T-43601b848503a0-01 Remove small loose stones |
| T | Support: stakes and ties | secure_outdoor stake/tie | T-ee87a75afd9055-01 Support plants with stakes<br>T-d6de281e0bf8a7-01 Tie plants to supports<br>T-d5e9502f580c7d-01 Install plant stake<br>T-74ed2a41426aac-01 Tie plant to stake |
| D | Weeding beds, cracks, lawn; garden maintenance | weed, ask plants:keep | T-5e35902580ab2d-01 Weed flower beds<br>T-21b22c528fbc33-01 Pull weeds from cracks<br>T-abf73665d13608-01 Weed garden<br>T-787d58a7430ca6-01 Remove weeds<br>T-5a09edfd909ba6-01 Weed beds<br>T-7f710c054752af-01 Maintain garden |
| O | Mulch, soil, topsoil, compost spreading | mulch, spread_granular | T-d182fc681f310d-01 (ex) Mulch flower beds<br>T-465f0931e94b39-01 Spread mulch<br>T-e10048604b4f9a-01 Mulch beds<br>T-c4e3e06cb8c2e6-01 Add mulch<br>T-4966e98701c019-01 Spread soil<br>T-569b20363b523d-01 Spread topsoil<br>T-f7fbca55b4c4ef-01 Spread compost in garden |
| H | Harvest vegetables, fruit, fallen fruit | harvest, pick_up | T-49afec5959cc87-01 Harvest vegetables<br>T-82bd7c3583d6e4-01 Harvest fruit<br>T-91511d2b9f8ec5-01 Harvest produce<br>T-2366e654cb6691-01 Harvest remaining garden produce<br>T-f02e328b4e0042-01 Harvest vegetables<br>T-2e7e1278f1a65c-01 Harvest fruit<br>T-5d9ca1564f923e-01 Pick up fallen fruit |
| R | Leaves and clippings: rake, bag, clear surfaces, green bin | rake, bag_leaves, close, pick_up/place; skill dispose | T-b35bf0c44dfff7-01 Pick up leaves<br>T-e262f715d0bd60-01 (ex) Rake leaves<br>T-6247afa580a49f-01 Rake leaves<br>T-d082fbf246301f-01 Rake leaves<br>T-6b2884667e1490-01 Bag leaves<br>T-92acc2bfb70f3f-01 Bag leaves<br>T-63bf32cd84484f-01 Move leaf bags<br>T-57dae6e932bbf5-01 Remove leaves<br>T-6d041ab5f4607d-01 Remove leaves from deck<br>T-a70ff5c0e5c3ec-01 Clear leaves after storm<br>T-24f3bc3d4dbd02-01 Clear walkways<br>T-3757a7bdae05e5-01 Rake grass clippings<br>T-b24034caac8d23-01 Bag grass clippings<br>T-284df1a158d27a-01 Put yard waste in green bin |
| B | Branches, litter, debris, obstacles | pick_up/place, prune (branch to length), secure_outdoor bundle, report | T-a87a0cf4dfe56e-01 Pick up litter<br>T-93826506c06fba-01 Pick up branches<br>T-69bc2a6d77ec62-01 Collect branches<br>T-331d2420f84dba-01 Pick up branches after wind<br>T-d6f6da8d0040df-01 Remove small branches<br>T-8fd32093771915-01 Pick up debris<br>T-1a6aa1143b741a-01 Pick up debris near fence<br>T-5dd3f00c2c4cd8-01 Pick up obstacles before mowing<br>T-18956e35b61b6b-01 Report fallen branches |
| Z | Lawn: inspect, mow, edge, trim, seed, amend, aerate | mow, edge, trim_plant grass, overseed, spread_granular, aerate; skills survey, mow_lawn | T-c8c36db697f240-01 Inspect lawn<br>T-fa2552570f7b6f-01 (ex) Mow lawn where equipment and safety constraints allow<br>T-502643ae72a664-01 Edge lawn<br>T-2cf2758f21d6a5-01 Edge garden beds<br>T-b20bfeb41a4eb2-01 Trim grass around obstacles<br>T-6af8dc2b2d5c9a-01 Overseed lawn<br>T-e1c45f3b207dfd-01 Spread lawn seed<br>T-2c49232bef1ecc-01 Spread safe lawn amendments according to instructions<br>T-481221abd5ef2a-01 Aerate lawn with appropriate equipment |
| Y | Shrubs, hedges, trimmings, fence vegetation | prune, trim_plant hedge/vines, rake method=hand | T-26d1cb2916a8d9-01 Trim small shrubs<br>T-5bc40951b28bf3-01 Prune bushes<br>T-df8d8322f56ed9-01 Shape hedges using safe equipment where appropriate<br>T-69024119f1cb20-01 Trim small branches with appropriate safe hand tools<br>T-8f276fc6e1d715-01 Collect trimmings<br>T-5c22460524aed1-01 Collect trimmings<br>T-9e08d6a87a0783-01 Trim light vegetation away from fence using safe tools |
| K | Compost | place into bin, turn_compost, shovel (finished compost, sift) | T-898485d3cbbac5-01 Compost garden waste<br>T-68fdc267af4bf6-01 Turn compost where appropriate<br>T-756d3a2f3e7349-01 Add appropriate material to compost<br>T-b833836c4bef99-01 Compost leaves<br>T-55b9ed1bc8c3d1-01 Compost plant scraps<br>T-40f1084213220e-01 Add scraps<br>T-25a8e6d976978c-01 Add dry material<br>T-9c96b61b02d268-01 Stir small compost bin when appropriate<br>T-a0f36b02b6a70f-01 Collect finished compost |
| C | Patio: cushions, covers, furniture, set-up, comfort | pick_up/place, cover_with/uncover, secure_outdoor strap/tie | T-fcfeb1aa891c23-01 Cover outdoor furniture<br>T-36632fccb4f8d5-01 Uncover outdoor furniture<br>T-f50fc5a79847c9-01 Uncover outdoor furniture<br>T-bb60c0629d8348-01 Cover outdoor furniture<br>T-1d2078081d3917-01 Retrieve outdoor furniture covers<br>T-10c195b85cf295-01 Store cushions<br>T-e1c0f2f444159e-01 Put cushions outside<br>T-97ca30576f40a4-01 Store cushions<br>T-ad3d8e6448f54b-01 Bring in outdoor cushions<br>T-118437362c4ab8-01 (ex) Store cushions before rain<br>T-981df41c899b56-01 Bring cushions inside before rain<br>T-a38cb52522d8b4-01 Bring cushions outside<br>T-ad5e16b21c6bdc-01 Store summer furniture<br>T-d72570b1e460ea-01 Set up patio<br>A-04b02c74604123-01 (~) “Make the patio comfortable.” Requires adapting to weather, <br>A-bc3eeb90a2faee-01 “Prepare the backyard for people.” Requires inspecting and d |
| U | Patio umbrellas | open/close, set tilt_deg, secure_outdoor strap/weight; skill close_umbrella | T-ed6d93f12a5c5c-01 Fold patio umbrellas<br>T-ce1bf0dd7b0fc0-01 Open patio umbrellas<br>T-eb192450afa221-01 Secure umbrellas in wind<br>T-633ee860ead278-01 Put umbrellas up<br>T-8b4654687e040f-01 (ex) Close patio umbrella in wind |
| X | Wind/rain preparation, valuables, weather checks | secure_outdoor (weight/tie_down/stake/lay_down/stack), pick_up/place, forecast | T-7c69ccaee89232-01 Secure lightweight outdoor objects<br>A-280948d94d490e-01 “Secure everything outside before the wind gets worse.” Requ<br>A-23d5a2982bd84f-01 “Get everything outside that shouldn’t get wet.” Requires ma<br>A-86b7faf3b0281e-01 “Bring in anything valuable.” Requires semantic judgment abo<br>T-931792e500e040-01 Check weather-appropriate household items if instructed<br>V-68a0baf1a10293-01 (~) Different weather |
| F | Grill covers, grill clean-up, fire pit, firewood, winter equipment | cover_with/uncover, inspect probe, shovel ash, stack/place logs, secure_outdoor cover_top | T-fc5c1e1b7eeccc-01 (ex) Cover grill<br>T-5a0c2bee6f694d-01 Uncover grill<br>T-4d7cdd5138f5d1-01 Uncover grill<br>T-789933bbcdcb51-01 Cover grill<br>T-02839c37c6c8ec-01 Clean grill after use<br>T-a10a1e16e9d01e-01 Cover outdoor equipment<br>T-3c0e5bb4fb4063-01 (ex) Clean fire-pit area when completely cold<br>T-79f00f75d5c964-01 Stack firewood |
| Q | Gutters, downspouts, grates, drains | clear_gutter, clear_grate | T-d74a98e53df61b-01 Clean gutters only under safe, appropriate human-supervised <br>T-160e8c878a8adb-01 Clean gutters at safe accessible levels<br>T-c0abc17f7c59ec-01 Clear ground-level drainage grates<br>T-f45a67596f8dea-01 Remove leaves from drains |
| V | Inspections and reports: fence, roof, storm damage, outdoor areas | inspect, report; skill survey | T-0caf47d0f789f5-01 (ex) Check fence visually<br>T-413229f46d8aca-01 Inspect fence visually<br>T-a915d935547199-01 Report broken boards<br>T-c4417aa71a72da-01 Inspect outdoor areas<br>T-d9ec72b4438ac0-01 Inspect property for visible storm damage<br>T-6bfcd0386f887c-01 Report damaged roof material from ground-level observation<br>A-6cbad53e6bae66-01 “Clean up after the storm.” Requires adapting to unpredictab |
| J | Snow and ice | shovel, spread_granular (deicer/sand) | T-9714cdd18ebfc8-01 Clear light snow from walkway with appropriate safe tools<br>T-68c141912368a6-01 Clear light snow where applicable<br>T-b0ab19967436e0-01 (ex) Spread approved ice melt according to instructions<br>T-a3cf8ce16581f6-01 Spread approved ice melt where applicable |
| L2 | Pool | skim, uninstall/install basket, clear_grate basket, place cover, secure_outdoor anchor/water_bags, pick_up/place toys | T-773e8e632446ec-01 Pick up pool toys<br>T-a8075c2a333d73-01 Store pool toys<br>T-ea9b3a9d6682c0-01 Retrieve pool toys<br>T-afb23b8f64b32b-01 Store pool toys<br>T-f02de5156c99d3-01 Use a pool skimmer from a safe position<br>T-7c7e25205e1b35-01 Empty skimmer basket when appropriate<br>T-d6ae19df22b673-01 Cover pool<br>T-ae4420d3f6c50d-01 Uncover pool with suitable equipment<br>T-faa3a644a4ee44-01 Secure pool cover<br>T-74d8d6b6f81453-01 Store flotation devices<br>T-3fedbb1af76e83-01 Store pool equipment |
| E | Decorations, picnic, cut flowers | place, secure_outdoor stake/tie_down, trim_plant stem/lower_leaves | T-85306e3333b13f-01 Arrange outdoor decorations<br>T-2e719af7809769-01 Remove outdoor decorations<br>T-e7732176fc8110-01 Set up seasonal decorations<br>T-6794b7cf500967-01 Remove seasonal decorations afterward<br>T-7a4e95d162fa22-01 Set up picnic<br>A-e12dbb1115fb96-01 (~) “Arrange these flowers so they look nice.” Requires aestheti |

Approximations: A-04b02c74604123-01: comfort uses shade/cushion/wind/temperature proxies (no per-person thermal model); A-e12dbb1115fb96-01: aesthetics are a rule-based arrangement score; V-68a0baf1a10293-01: weather variants are produced by weather_plan for any outdoor recipe.

Secondary entries (`outdoor_plants__secondary.jsonl`, owned by other domains) reuse the same recipes: this domain supplies plants, soil, weather, yard and patio state, and its verbs, for their specs.

## 11. Fidelity and known limits

* Fidelity: container water, mulch depth, lawn patches, wind forces and fire-pit cooling are P-approx
  (physically motivated, lumped); species knowledge and owner preferences are symbolic tables.
* Comfort (`A-04b02c74604123-01`) uses shade/cushion/wind/temperature proxies, not a per-person
  thermal comfort model; flower aesthetics use a rule-based arrangement score.
* Kernel v2.1 resolved the old grip limitation: cutting tools carry the `sharp` hazard again (`blade: true` is
  kept as a descriptive flag).
* `wait` accepts up to 604800 s (7 days); the unit tests still wait in day chunks.
* Rain/wind see items carried outdoors by people/pets during a long wait at the next 600 s boundary
  (`outdoor_map` cache); robot actions invalidate the cache immediately.
* Leaf-pile scattering in wind is 15 %/30 s per 6 m/s of gust above 6 m/s, scaled by substep length.
* Tier 1: only `mow` walks the ground it works (a motion record); raking, edging, aerating, overseeding and spreading
  over an `area` still work it from where the robot stands (their time grows with the area, the walk is not
  recorded). Pot drainage is immediate within the watering action (no drip process: the kernel's drip has no robot
  attribution). Under the kernel's wind law a seat cushion never lifts off its chair (the kernel lifts only flat
  sheets; this domain's copy blows cushions off at an 8 m/s gust, too easily): kernel calibration requested.
  Irrigation programs, quiet hours and the watering rules read local standard time (clock.hour), like the rest of the
  house until the clock's `dst` flag is set. The default weather process has no fog, humidity or UV (generator/climate.py);
  scripted weather keeps those fields.
* Realistic perception: since kernel v2.9 round 5 an unread bag shows its group's glance size (section 5a), not its
  own, so a 1.5 kg seed bag and an 11 kg salt bag look alike at a glance; they still feel different when lifted (mass
  is touch-tier). An instance spawned with its own size (e.g. a small mulch bag) also shows the group size until read. A product's printed `qty` is the remaining amount (labels in this domain double as fill gauges).
