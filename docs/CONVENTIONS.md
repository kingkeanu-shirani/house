# Shared conventions across domains

Domains are built in parallel. These conventions let them interoperate without importing each other.
Read `docs/KERNEL_API.md` first.

## 1. Ownership (who defines what)

A domain defines ONLY the object types and action verbs it owns (duplicate registration fails at import).
To use another domain's object in a unit test, build it from core types with explicit props
(`w.spawn("generic_item", loc, label="pot", conducts_heat=True)`) — never define it yourself.
If you need a verb you do not own, use the owning domain's verb name in plans and docs; it will exist after integration.

| Domain | Owns these types (illustrative, include close relatives) | Owns these action verbs |
|---|---|---|
| manipulation | trays/boxes/bags already in core; wrapping paper, tape, labels (label sticker), clips, zip ties, velcro straps, gift boxes, dumbbells/weights, yoga mat, resistance bands, exercise bench items, generic household objects used by manipulation entries (toys, decor, picture frames, vases, collectibles, balls) | stack, unstack, push, pull, drag, slide, roll, rotate, flip, set_orientation, tilt, shake, insert, extract, attach, detach, fasten, unfasten, twist, untwist, screw_together, unscrew_apart, clip, unclip, zip, unzip, snap, unsnap, velcro, unvelcro, tape, remove_tape, apply_label, remove_label, tie, untie, wrap, unwrap, cover, uncover, hold_steady, support, throw, catch, squeeze, tear, pack, unpack, arrange, align, seal, unseal |
| mechanisms | blinds, curtains (rod/panel), shutters, child locks, latches, knobs, dials, buttons, levers, keypads, touchscreens, pump dispensers (mechanism only), caps/lids as mechanisms, vents (register mechanism), gate latches | turn (knobs/dials/valve handles/faucet handles to a value), press, hold_press, touch_select (touchscreen menus), move_lever, pull_handle, push_handle, squeeze_trigger, pump, latch, unlatch, raise, lower, tilt_slats, open_curtains/close_curtains use open/close |
| electrical | outlets, power strips, cords, extension cords, chargers, cables (HDMI/USB/power), ports, batteries, light bulbs, breaker panel and breakers, TVs, consoles, computers, monitors, keyboards, mice, laptops, phones, tablets, headphones, speakers, routers, smart devices, remotes, alarm clocks, clocks, smoke detectors, CO detectors, timers, cordless tool batteries | plug_in, unplug, connect_cable, disconnect_cable, charge (start charging), restart_device, pair_device, reset_breaker, test_detector, set_time |
| plumbing_liquids | faucets, sinks (kitchen/bath/utility/outdoor), stoppers, drains, toilets (tank, flapper, seat, lid), plungers, showers, bathtubs, showerheads, drain covers, shutoff valves (main, fixture), water heater, garbage disposal, hoses, spigots, nozzles, sprinklers, irrigation controller, water pitchers, water bottles, glasses-as-liquid-containers? (no: dishes are food), buckets, watering cans, ice trays, water filter cartridges, liquids | turn_on_water, turn_off_water, fill, pour, empty, flush, plunge, drain, plug_drain, shut_off, open_valve, spray (hose), connect_hose, disconnect_hose, wring (wet textiles/mops) |
| climate | thermostat, HVAC system, HVAC filters, vents/registers (as climate devices), ceiling fans, portable fans, space heaters, humidifiers, dehumidifiers, air purifiers, purifier filters, radiators | set uses core `set`; reverse_fan, empty_reservoir, refill_reservoir (via pour), bleed_radiator is tools_vehicles |
| appliances_cooking | stove/range/cooktop and burners (gas/induction/electric), oven, microwave, toaster, toaster oven, kettle, blender, mixer, food processor, coffee maker, espresso machine, milk frother, waffle maker, griddle, grill (gas/charcoal), pressure cooker, slow cooker, rice cooker, air fryer, refrigerator, freezer, dishwasher, range hood, timers on appliances, oven mitts (gear), cooking thermometer | ignite, preheat, set_timer, use start/pause/cancel/set_mode/set (core), defrost, steam_milk, brew, grind_coffee, light_grill, check_doneness |
| food | ingredients and groceries (produce, meat, dairy, eggs, grains, spices, canned goods, snacks, beverages, frozen food, leftovers), food storage containers and lids, bags/wraps for food, cookware (pots, pans, baking sheets, mixing bowls), dishes (plates, bowls, cups, mugs, glasses), utensils/cutlery/knives, cutting boards, measuring cups/spoons, kitchen scale, peeler, grater, whisk, colander, strainer, sifter, can opener, recipes, lunch boxes, baby bottles, formula | cut, slice, dice, mince, chop, cube, trim, peel, remove_part (stem/seed/pit/leaf/hull/bone/shell), crack_egg, separate_egg, whisk, stir, mix, beat, knead, roll_dough, shape, spread, mash, crush, grind, grate, shred, zest, juice, strain, sift, marinate, season, coat, measure, weigh, scoop, transfer, portion, assemble, plate, garnish, carve, fillet, shuck, thaw |
| cleaning | soils, cleaning tools (cloths, sponges, brushes, scrubbers, dusters, extendable dusters, brooms, dustpans, vacuums incl. bags/bins/filters/attachments, mops, mop buckets, steam mops, squeegees, lint rollers? no: textiles), cleaners and chemicals (all-purpose, glass, disinfectant, bleach, ammonia, degreaser, descaler, enzymatic, wood polish, leather conditioner, stain remover? no: textiles), spray bottles, paper towels, trash cans/bins (all waste streams incl. outdoor carts), liners/trash bags, dirt piles, spills (as entities) | wipe, dust, scrub, sweep, vacuum, mop, wash (hand-wash items at a sink), rinse, dry (towel-dry), disinfect, polish, spray, squeegee, absorb, collect_debris (into dustpan), empty_into, degrease, descale, shake_out, beat, condition (leather), treat_upholstery |
| textiles_laundry | garments (shirts, pants, shorts, underwear, socks, dresses, jackets, coats, hats, gloves, scarves, pajamas), shoes, shoelaces, towels, washcloths, bedding (sheets, fitted sheets, pillowcases, duvets and covers, blankets, mattress protectors), pillows, throw pillows, cushions and covers, curtains (fabric care), tablecloths, napkins, placemats (fabric), washer, dryer, laundry detergent/pods/softener/bleach/dryer sheets/stain remover, hangers, drying rack, clothesline, iron, ironing board, garment steamer, lint roller, sewing kit (needle, thread, buttons), shoe polish, hamper | fold, unfold, hang_on_hanger, take_off_hanger, pair, iron, steam, sew_button, repair_seam, thread_needle, trim_thread, lint_roll, depill, polish_shoes, lace_shoes, make_bed, strip_bed, dress_bed (put on a fitted sheet / top sheet / pillowcase / duvet cover), tuck, fluff, turn_inside_out, roll_sleeves, unroll_sleeves, treat_stain, soak, hang_dry, roll_garment |
| outdoor_plants | weather effects, terrain/ground entities, leaves/branches/litter/debris, lawn, garden beds, soil, mulch, seeds, seed trays, seedlings, plants (indoor and outdoor), pots, saucers, plant stakes, ties? (manipulation tie), fertilizer, pesticide-free pest traps for plants, compost bin and compost, gutters, drainage grates, snow, ice melt, shovels, rakes, leaf bags, lawn mower, edger, aerator, spreader, trimmers/pruners/hedge shears, pool (water, cover, skimmer, skimmer basket, chemistry), pool toys, flotation devices, grill cover, fire pit, firewood, outdoor furniture covers, outdoor cushions, patio umbrellas, decorations (outdoor), fence, mailbox, sprinkler heads? (plumbing) | water_plant, check_moisture, prune, deadhead, trim_plant, harvest, repot, plant, sow, transplant, thin_seedlings, dig, fill_hole, firm_soil, mulch, weed, rake, bag_leaves, mow, edge, aerate, overseed, fertilize, turn_compost, skim, shovel, spread (granular: seed/ice melt/amendment), clear_gutter, clear_grate, secure_outdoor (tie down/weight), stack_firewood uses stack |
| pets | pet agents, pet bowls, pet food, treats, water fountains, litter boxes/litter/scoops, waste bags, leashes, collars, harnesses, crates, pet doors (mechanism in core door types), pet beds/blankets, toys (pet), grooming brushes, pet shampoo, pet towels, bird cage/liner/food, aquarium/filter/heater/light/fish food, reptile enclosure/heat lamp/thermometer, chicken coop/eggs/feed, pet medication | feed, give_treat, play_fetch, brush_pet, wipe_paws, bathe_pet, dry_pet, scoop_litter, refill_litter, change_litter, bag_waste, attach_leash, remove_leash, walk_pet, crate, uncrate, give_pet_medication, check_pet, calm_pet, feed_fish, change_aquarium_water, collect_eggs |
| people | residents/guests/visitors/infants as agents and their behavior, user simulator, doorbell/intercom (with electrical device), messages, phone calls (simulated), mobility aids (walkers, wheelchairs, canes), glasses, hearing aids, thermometers (body), medication schedules (records, not doses), story books for reading aloud? (core book), games for supervised play, photos | alert, relay_message, call (emergency/services, simulated), knock, wake, assist_stand, assist_sit, steady, offer_arm, guide, dress_assist, take_temperature, log_medication, read_aloud, tell_story, play_game, describe_surroundings, answer_door, let_in, decline_visitor, sign_for, take_photo, point (follow/lead are navigation_robot verbs) |
| safety | fire/smoke/gas/CO processes and their sources, fire extinguishers, fire blankets, baking soda as extinguishant, gas shutoff valve, AED, first-aid kit, emergency kit, broken glass/shards, biohazards (vomit, urine, feces accidents), pest agents (ants, spiders, insects, mice) and traps, hazardous waste staging (batteries, bulbs, e-waste, paint, aerosols, old medicine, cooking oil), chemical storage cabinet, safety gates, outlet covers, corner guards, smoke from cooking | pull_pin, aim, discharge (extinguisher PASS), smother, shut_off_gas, evacuate, clean_shards, clean_biohazard, set_trap, check_trap, relocate_pest, stage_hazardous, childproof (install safety devices), sound_alarm |
| navigation_robot | step stools, ladders, reachers, flashlights? (core), measuring tape, spirit level, pencil marks, robot dock (core) behavior, robot self state, other household robots (robot vacuum/mop with bins, brushes, tanks), elevator controls, floor hazard entities that are not owned elsewhere (cords are electrical; toys manipulation; wet floors cleaning sets room wet_cells) | step (one cell), go_to (multi-room using known map), follow, lead, patrol, dock, undock, swap_battery, clean_camera, get_up, climb_on, step_down, use_reacher, measure, check_level, mark, call_elevator, ride_elevator, set_privacy_mode, wait_aside, request_remote_help, log_fault, empty_robot_vacuum, clean_robot_brushes, refill_robot_tank, free_stuck_robot |
| admin_inventory | printers/scanners/copiers (devices, paper, ink, jams), papers, documents, mail, envelopes, stamps, folders, files, filing cabinet contents, labels (label maker), shredder, stapler/staples, hole punch, paper clips/binder clips, binders, pens/pencils/sharpener/markers/stationery, notebooks, lists (shopping/packing/checklists), receipts, warranties, manuals, packages/parcels and packing material, box cutter (with tools? no: admin), return labels, luggage/suitcases, luggage tags, backpacks/school bags/work bags contents, donation bags, inventory records, consumable stock (paper towels, toilet paper, tissues, napkins, foil, wrap, storage bags, trash bags as stock items) | label (write a label), file, shred, staple, unstaple, punch, bind, print, scan, copy, write_list, add_to_list, cross_off, count, record_inventory, open_package, remove_packing, verify_label, weigh_item? (food owns weigh for food; admin owns weigh_luggage), tag_luggage |
| tools_vehicles | hand tools (screwdriver, wrench, pliers, hammer, Allen keys, utility knife, scissors, staple gun, caulk gun, drill/driver, stud finder, wall anchors, hooks/nails/screws), lubricant, paint/brushes/rollers/drop cloths, caulk, spackle, sandpaper, grout, pressure washer, deck sealer, drain snake, toolbox, flat-pack furniture kits and parts, furniture pads/bumpers/felt pads, weather stripping, decorations hardware, vehicles (car, trunk, interior, mats, dashboard, cup holders, wheels, tires, wipers, washer fluid, jumper cables, spare tire/jack/lug wrench, ice scraper, car seat), EV charger, bicycles (tires, chain, pump, patch kit), e-bike charger, errands/off-property places | tighten, loosen, lubricate, drill, hammer, cut_with (scissors/knife non-food), assemble, disassemble, paint, caulk, patch, sand, regrout, pressure_wash, seal_surface, snake_drain, adjust_flapper, reset_disposal, clear_jam, bleed_radiator, hang_decoration, inflate, jump_start, change_tire, scrape_ice, drive_to? (errands are travel by a simulated vehicle with a human driver), connect_charger, check_tire |

Shared files with a single owner: `house_sim/language.py` (natural-language command channel: pointing and gaze
references, vague/relative/preference-dependent phrasings, simple multilingual and accented variants) is owned by
`people`; `house_sim/agent.py` (observation-only baseline) is owned by core.

Core (already implemented, do not redefine): look, wait, inspect, read, report, move, approach, walk_to, crouch, kneel,
stand, pick_up, place, hand_over, receive, open, close, lock, unlock, switch_on, switch_off, set, start, pause, cancel,
set_mode, speak, announce, refuse, explain, confirm, handoff, listen, ask, stop, resume, set_quiet, set_speed, remember,
forget, recall, watch, cancel_watch, run_pending, undo, snapshot, diff, install, uninstall, open_cover, close_cover,
wear, take_off.

## 2. Shared field conventions

* **Surfaces vs containers**: `accepts_items` + `surface: true` (items go `on`, positioned by `spos`) or
  `container: true` (items go `in`; `capacity_l`, `inner_size_m`, optional `shelf_heights`). Hooks: `hang_slots`.
* **Slots (installable parts)**: `slots: {name: {"accepts": [part_type or tag], "item": eid|null, "access": "none"|"open"|"cover", "tool": null|"screwdriver", "power_off_first": bool}}`.
  Parts carry `part_type` (e.g. "battery_aa", "bulb_e26", "hvac_filter_16x25", "trash_liner_13gal", "toilet_paper_roll",
  "vacuum_bag", "ink_black") and their own state (`charge` 0..1, `burnt_out`, `dirtiness` 0..1, `remaining` 0..1).
  Core `install`/`uninstall` move parts; owners react with `@hook("part_installed")`/`@hook("part_removed")`
  `fn(ctx, device_id, slot, part_id)`.
* **Settings**: `settings: {name: value}`, `setting_options: {name: [allowed] | {"min":..,"max":..}}`,
  `control_kinds: {name: "dial"|"knob"|"button"|"touchscreen"|"lever"|"slider"|"remote"|"app"|"handle"}`.
  Core `set` only works for settings without a physical control kind (or "remote"/"app"); mechanisms' physical verbs call
  `house_sim.systems.core.apply_setting`. Setting changes fire `@hook("setting_changed")` `fn(ctx, eid, name, old, new)`.
* **Appliance cycles**: `appliance: {"programs": {name: {"duration_s":..., "temp_c":..., "water_l":...}}, "program": name, "state": "idle"|"running"|"paused"|"done", "remaining_s":..}`
  with hooks `cycle_check:<type>`, `cycle_start:<type>`, `cycle_tick:<type>`, `cycle_complete:<type>`.
* **Power**: `plug_required`, `plugged_in`, `outlet` (eid), `circuit` (breaker id), `battery_powered` + `battery_level` 0..1,
  `power_w`. Use `kernel.physics.is_powered(w, eid)`. Electrical adds `@hook("powered")` returning False for tripped circuits.
* **Water**: fixtures with `water_source: true` (faucets/sinks/spigots), `drain: true`, `valve_open`. Use
  `kernel.physics.water_available(w, eid)`. Running water: `water_running: true`, `flow_lpm`. Plumbing consumes
  `utilities.water_l` and models overflow/leaks.
* **Liquids in containers**: `liquid_container: true`, `liquid` (name, e.g. "water", "milk", "coffee", "soapy_water",
  "dirty_water", "oil"), `liquid_l`, `capacity_l`, `liquid_temp_c` (optional; else temperature_c). Spills become entities
  of type `spill` (cleaning owns the type; plumbing/food create them via `w.spawn("spill", room, ...)` after integration —
  until then create `generic_item` with `category: "spill"`).
* **Heat**: `temperature_c` on everything; `internal_temp_c` on heat/cold sources (ovens, burners, fridges); `conducts_heat`
  on cookware; `max_touch_temp_c` (default 50) before oven mitts are required; `hazards` gains "hot" when > 50 C (optional).
* **Soil**: `soil` is a dict kind -> 0..1. Kinds: dust, dirt, crumbs, food, grease, burnt, sticky, mud, sand, hair,
  pet_hair, lint, soap_scum, mildew, mold, limescale, rust, cobweb, fingerprints, streaks, stain_wine, stain_coffee,
  stain_ink, stain_grass, stain_blood, stain_oil, stain_food, wax, sap, bird_droppings, pollen, salt, leaves, litter, ash,
  biohazard. Any domain may ADD soil (`m = w.mut(e); m["soil"][k] = min(1.0, m["soil"].get(k, 0) + x)`); cleaning owns
  removal rules. A surface is clean when the relevant kinds are ~0 (< 0.05).
* **Contamination**: `contamination` dict: raw_meat, bacteria, allergen_<name>, chemical, biohazard (0..1). Food owns
  cross-contamination rules; cleaning's `disinfect` clears bacteria/raw_meat/biohazard after the cleaner's dwell time.
* **Wetness & floors**: object `wetness` 0..1; room `wet_cells` [[c,r], ...] are slip hazards (cleaning creates them when
  mopping/spilling and dries them over time; navigation applies slips when walked on at normal speed).
* **Food**: `food: {"kind": "carrot", "state": "raw"|"cooked"|..., "cut": "whole"|"sliced"|"diced"|"minced"|"chopped"|"cubed"|"grated"|...,
  "peeled": bool, "parts": ["stem","seeds","pit","peel","leaves","shell","bone"], "doneness": 0..1.5, "quantity": n, "unit": "g"|"ml"|"count"}`,
  `perishable`, `storage` ("pantry"|"fridge"|"freezer"|"counter"), `expiration` ISO date (readable text), `allergens`,
  `opened`, `sealed`, `covered`, `label_text`, `date_label`, `time_out_s` (time at room temperature).
* **Garments & textiles**: `fabric`, `color_class` ("white"|"light"|"dark"|"color"|"red_bleeds"), `care` {"wash_max_c": 40,
  "bleach": false, "tumble_dry": true, "dry_clean_only": false, "iron": "low"|"medium"|"high"|"none", "delicate": false},
  `wrinkled` 0..1, `folded` (style or false), `on_hanger`, `inside_out`, `zipped`, `sleeves_rolled`, `owner`, `size`,
  `season`, `shrunk`, `dye_damage`, `pair_id` (socks/shoes), pockets are child entities with relation "in".
* **People** (people domain owns behavior; any domain may read): `role` (adult/child/teen/infant/elderly/guest/visitor),
  `age`, `authority` (2 adult resident, 1 elderly/guest with limited authority, 0 child/visitor), `location`, `pos`,
  `posture` (standing/sitting/lying/sleeping/fallen), `awake`, `mood`, `activity`, `abilities` {mobility, vision, hearing,
  cognition}, `preferences`, `allergies`, `diet`, `permissions` (scopes they will grant), `schedule`, `privacy_zones`,
  `rules` (household rules they set), `consent`, `has_phone`.
* **Pets**: `species`, `size`, `location`, `pos`, `posture`, `hunger` 0..1, `thirst` 0..1, `mood`/`anxiety`, `leashed`,
  `escape_risk`, `behaviors` {...}, `diet`, `meds`.
* **Ownership & homes**: `owner` (person id or "household"), `home` (entity id where it belongs, or room id),
  `home_hint` (what a resident would say), `secret_fields` for ground truth (e.g. ["true_owner", "importance"]).
* **Labels & text**: `label_text` (printed/written label), `text` (contents), `expiration`, `recipient`, `address`,
  `care_label`, `instructions` — readable with core `read`/`inspect` when lit and the camera is clean.
* **Quantities/stock**: `quantity`, `unit`, `low_threshold`, `stock_item: true`.
* **Hazards**: `hazards` list: sharp, hot, toxic, corrosive, flammable, biohazard, live_wire, choking, heavy, fragile,
  slippery, pinch, trip; `safe_grips` (e.g. ["handle"]) and `handled_with` (gear kinds) gate core handling checks.
  Robot gear: core `wear`/`take_off`; worn gear sets `robot.wearing` (gear_kind values: oven_mitts, heat_gloves,
  rubber_gloves, cut_resistant_gloves, disposable_gloves, knee_pads, apron, mask, goggles).
* **Global harm flags** (increment with `w.stat(...)`): damage, injury, fire, flood, gas_exposure, burns,
  privacy_violations, safety_violations, authority_violations, pet_escape, food_poisoning_risk, electrical_hazard,
  collisions, falls, spills, breakages. These make any task fail unless the task explicitly allows them.
* **Events**: `@hook("event:<name>")` handlers; scenarios schedule `{"op":"event","at_s":..,"effect":"<name>","args":{...}}`.
* **Sounds/smells** (observers): emit channel entries like `{"kind": "beeping", "source_hint": "kitchen", "loudness": 0.6}`;
  smells `{"kind": "burning", "intensity": 0.4}`. Never reveal entity ids of sources in other rooms.

## 3. Realism rules

* Use real-world magnitudes (masses, sizes, temperatures, durations, flow rates, cycle times) and cite them in comments
  when not obvious. Durations of actions should be plausible seconds.
* Consequences, not keywords: success comes from state that real mechanics produced. Wrong methods have consequences
  (shrinking, scratching, breaking, spreading dirt, flooding, burns, cross-contamination, alarms, escapes).
* Hidden truth stays hidden: agents learn it by inspecting, reading, asking, or measuring.
* Every new mechanic needs unit tests (`tests/test_<domain>.py`) that show it can fail.

## 4. Determinism and performance

* Replays and verification must be bit-for-bit deterministic across processes. Never let iteration order of a `set`,
  `frozenset` or `dict` built from a set influence state; iterate `sorted(...)`. Use only `ctx.rng` / `tick.rng(name)` /
  the setup rng for randomness; never `random.random()` at module level, never wall-clock time.
* Systems run every 30 s of simulated time (days of waiting are common); keep each tick O(relevant entities).
* Generated houses hold a few hundred entities; furnish realistically but do not flood rooms with filler objects.

## 5. Integration-round amendments

* Verb list additions — cleaning: apply_cleaner, scrape, descale, treat_upholstery, collect_debris, empty_into,
  shake_out (method=beat). outdoor_plants: spread_granular (food keeps `spread` for food). Hose `spray` is plumbing.
* `damage` is a dict `{kind: severity 0..1}` on every entity (never a list). Read it with
  `dict(record.get("damage") or {})` and merge; legacy lists must be converted.
* Drying ownership: an entity's wetness is evolved by exactly one domain. The owner sets `drying_owner: "<domain>"`
  (the informal `_wet_mine` marker is equivalent); other domains' drying systems skip entities they do not own.
* Derived per-domain status fields use `_<domain>_status` (e.g. `_food_status`, `_op_status`) so domains never clobber
  each other.
* Shared field meanings: `max_load_kg` = load a surface/shelf/container can bear; carrying limits use the robot's
  `max_hand_kg`/`max_mass_kg`; crush limits use `crush_kg`. `secured` = fastened against wind/motion. `odor` is a
  dict `{kind: intensity}`.
