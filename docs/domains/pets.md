# Pets domain

Pet agents (dogs, cats, birds, small animals, a bearded dragon, aquarium fish, backyard chickens) with needs,
behaviour and health, and everything a household robot does for them: feeding and water, litter and waste,
leashes, crates and walks, grooming, examining and calming, play and toys, aquariums, terrariums, cages and
coops, owner-directed medication, and reporting what it notices.

Code: `house_sim/systems/pets/` (package, ~12 400 lines). Types: `house_sim/data/types/pets.json` (105 types, 73 with a
realistic-perception block, 18 with a camera `view` block, 5 look-alike groups plus the shared pill-bottle group, a
file-level `field_tiers` block for fields pets writes onto other entities and rooms).
Tests: `tests/test_pets.py` (247 tests; pass normally, with HOUSE_SIM_STRICT=1, every domain loaded, and with only
core and pets loaded: HOUSE_SIM_DOMAINS=core,pets).
Examples: `tasks/authored/pets__examples.json` (12 oracle specs) and `tasks/authored/pets__perception.json` (3 realistic
specs); all pass `--check` on seeds 0, 1, 2, 41, 97 with every domain loaded, the realistic ones also with
`--camera geometric`; 13 of them run in generation-2 houses (`"house.gen": 2`). See section 5a for what the robot can
perceive (kernel v2.4 - v2.8) and section 11 for the tier-1 kernel (physics, camera, robot body, notices, questions,
generation 2).

| Module | Contents |
|---|---|
| `common.py` | species tables (weights, reach envelope, gear girths, eat rates, water need), toxin table, pet-room reachability (`pet_rooms`), reach envelope (`pet_access`), escape zones, appeal, logging/harm helpers, action guards; where a pet stands (`standing_tiers`, `settle_spot`, `pet_cell_near`, built on the kernel v2.3 door and access helpers) |
| `model.py` | pet record defaults (`ensure_record` / lazy `ensure_pet`), diets, meal slots (`current_slot`, `slot_count`, `meal_due`) |
| `behavior.py` | system `pets.agents`: needs, eating, drinking, elimination, exploring/ingestion, stress, escapes, roaming, outdoor exposure and mud; hooks on `move`/`approach`/`walk_to`/`open`/`close` (following, leashed walking, startling, door dash, wanting in/out, pinching); awake pets step out of the robot's path onto a spot that blocks nobody else; awake pets settle off doorway clearance, chokepoints and last-access cells at episode start (`episode_start` orders 21 and 151); a "startled" mood set by another domain shows for 120 s before the mood follows anxiety again |
| `feeding.py` | `feed`, `give_treat`; meal accounting and the pet-care log; wet food time-out and spoilage, opened cans, lids, fridge; automatic feeders; raids (system `pets.food`) |
| `water.py` | water bowls, fountains, cage bottles: freshness, biofilm, debris, temperature in sun/shade, fountain pump/filter, carry slosh; reconciliation of level changes made by plumbing verbs; biofilm removal by cleaning verbs (systems `pets.water` every 120 s, `pets.water_levels` per substep) |
| `litter.py` | `scoop_litter`, `refill_litter`, `change_litter` (also small-animal substrate), `bag_waste`; box acceptance, waste streams, liner scatter (system `pets.litter`) |
| `handling.py` | `attach_leash`, `remove_leash`, `crate`, `uncrate`, `walk_pet`; gear fit, dropped leash, crate latching, freeing trapped pets, appliance-with-pet consequence |
| `grooming.py` | `brush_pet`, `wipe_paws`, `bathe_pet`, `dry_pet`, `check_pet`, `calm_pet` (system `pets.body`: drying, chill, excitement decay, leash security) |
| `health.py` | `give_pet_medication`; due slots, dose accounting, medication errors, spat pills, dosed water/food |
| `play.py` | `play_fetch` (throw geometry), taking toys from pets, furniture moved over a hiding pet |
| `aquatic.py` | aquarium water chemistry, nitrifying colony, heater, filter prime/flow, `feed_fish`, `change_aquarium_water`; reptile enclosure thermal gradient (system `pets.habitats`) |
| `cages.py` | bird cage (door, fan/window panic, seed hulls, tray gap, liners), small-animal cage, chicken coop (laying, roosting, predators), `collect_eggs` (system `pets.cages`) |
| `exposure.py` | `noise_event` hook, cleaning-chemical and PTFE-fume exposure, cat window escapes, stray animals (system `pets.exposure`) |
| `yard.py` | stepping in dog waste, tracking it indoors, revealing concealed piles while walking |
| `perception.py` | observer `pets.cues`, inspector `pets.details`, invariants, answerer `pet:`, event effects, greeting arrivals |
| `furnish.py` | furnisher `pets.household` (order 93); floor items through `Placer.floor_item` at a kernel keep_access cell (never cutting a hazard-free route or anyone's last access; items >= 0.15 m outside doorway clearance and door leaves) whose own keep-out ring cuts nothing either (`scenarios._floor_spot`); the litter scoop is kept with the litter when no floor spot beside the box qualifies; the feeding mat and the litter box go to the first candidate room with floor space for them (the box never in the room the cat eats in when there is another); kibble goes into a reachable low cabinet (kitchen, else pantry) with room on its bottom shelf (load rating, volume) and nothing crushable inside, else stands on the pantry/kitchen floor; the litter bag likewise; the wand toy into a reachable drawer; each generated pet gets a `Placer.agent_cell` resting cell (a room too cramped for one: another living-area room); the generator's repair pass re-checks everything after the last furnisher |
| `scenarios.py`, `skills.py` | scenario helpers `pets.*`, reference skills `pets.*` |
| `physical.py` | the kernel physics layer: crashes startle pets, things landing on a pet, cats knocking things off, a dog's tail sweep, event `pets.knock` (section 11) |
| `routes.py` | dialogue routes to the `pet:` answerer (section 11) |
| `gen2.py` | furnisher `pets.habitats` (order 94): generation-2 habitats for the caged pets in `household.profile.pets_pending` (section 11) |

Determinism: all loops iterate sorted ids; randomness comes only from `ctx.rng`, `tick.rng(name)` and the setup rng.
Safety-relevant outcomes that wrong plans must hit are deterministic thresholds (door dash >= 0.8 escapes on
opening; a reachable appealing item is found after `240 s / appeal` of continuous access).

Performance (kernel v2.1): no system scans every entity. Candidate lists come from `w.static_index` over static
fields (`common.sindex(state, name, predicate)`; helpers that only receive `state` find the live World through a weak
registry filled by `episode_start` and the systems, and fall back to a scan without one) and dynamic fields are
re-checked on the few records returned (e.g. `behavior.STATIC_SETS`: noisy devices, white-noise maskers, bowls, water
sources, litter boxes, appealing/toxic items, cords; pet doors, windows, rugs, bedding, wraps, cage trays, seed dishes,
nest boxes, fans, shade, habitats and their devices, wet food/bowls/feeders, concealable waste). Slow water aging runs
every 120 s (`pets.water`, closed form in dt). `python tools/bench_systems.py 40 0 1 2 3 --domain pets`: all pets
systems together ~0.15 ms per 30 s substep (was ~2.0 ms; `pets.agents` 0.12 ms); with every domain loaded
(`python tools/bench_systems.py 20`) all pets systems ~0.21 ms (`pets.agents` 0.16 ms) of a 5.4 ms engine substep. The
invariants compare two harm counters (O(1)) and take the World as their third argument. Placement helpers that score
floor cells (`standing_tiers`) run only at episode start and when a pet relocates, never per tick.

-----------------------------------------------------------------------------------------------

## 1. The pet agent (`state["pets"][id]`)

Pets created by the generator, the `pet` setup op or other domains are completed lazily (`ensure_pet`) the first
time the pets system or a pets action touches them; `pets.pet` builds a complete record up front.

| Field | Meaning (default) |
|---|---|
| `species`, `size`, `weight_kg`, `label` | dog sizes toy/small/medium/large (3/8/20/34 kg); cat 4.5 kg; bird 0.09 kg; hamster 0.12 kg; reptile 0.45 kg |
| `location`, `pos`, `posture`, `awake`, `activity`, `mood` | `mood` follows anxiety: calm < 0.3 <= uneasy < 0.6 <= anxious < 0.85 <= panicked (or `guarding`, `hurt`). Default posture: an awake dog/cat rests `sitting` (tail tucked), an asleep one `sleeping`; `lying` is a deliberate scenario choice (navigation's `tail` hazard and the core `person_down` hazard apply to lying pets) |
| `anxiety`, `baseline_anxiety`, `stress_peak`, `stress_signs` | 0..1; relaxes to the baseline (<= 0.2); `stress_peak` = max during the episode |
| `excitement` | arrivals and play raise it; decays (tau 5 min) |
| `hunger`, `water_deficit_ml`, `thirst`, `bladder`, `bowels` | needs; water need 55 ml/kg/day (dogs) / 45 (cats), +4 %/C above 24 C |
| `diet` | `{foods, meals {slot: "HH:MM"}, mode scheduled|free, treat_limit, no_treats}` (visible to residents, not observed) |
| `_diet` (hidden) | `{portion_g, wet_g, dry_g, daily_g}` - the prescribed amounts (ask `pet:portion:<id>` or read the vet note) |
| `served_g`, `intake_g`, `wrong_food_g`, `wrong_diet_served` | grams served into the pet's bowl / actually eaten, by food kind; eaten kinds outside the diet |
| `meals_given {"YYYY-MM-DD:slot": n}`, `double_fed`, `extra_meals`, `over_allotment` | meal-slot bookkeeping (slots before the episode count as given unless listed in `missed_meals`) |
| `treats_today`, `treat_kcal`, `treat_over_limit` | treats given |
| `ingested [...]`, `toxic_ingestions`, `exposed_hazards [...]`, `hazard_exposure_s` | what it ate/swallowed; toxic/choking items currently within its reach envelope; seconds any was |
| `meds_given [...]`, `med_received`, `med_errors`, `med_error_log` | doses actually taken |
| `behaviors` | `steals_food, counter_surf, door_dash, follows_robot, tips_trash, opens_cabinets, opens_doors, chews [words], steals_items, fetch, recall, wait_trained, crate_trained, noise_phobia, jumps_on_people, handling_tolerance, bath_tolerance, wary_of_strangers, hides_when_scared, jumps_gates, explores, roams, resource_guarding` |
| `escape_risk`, `escaped`, `escaped_via` | shared with mechanisms/navigation (both use the same `escaped` flag, so an escape counts once) |
| `leashed`, `leash`, `leash_ring`, `leash_secure`, `crated`, `crate`, `enclosure`, `inside`, `trapped`, `confinement` | containment; `confinement {intent: deliberate, reason}` = owner wants it kept in |
| `wants` (`in`/`out`), `stay_until_s`, `calm_until_s`, `in_den`, `hiding_under`, `revealed` | cues and states (cleaning uses the same `hiding_under`) |
| `paws {fl, fr, bl, br}`, `paws_wet`, `wetness`, `coat {type, loose_hair_g, mats, hot_spot, wound, dirt}` | mud per paw; grooming state |
| `_health {conditions: [...]}`, `signs`, `_urgency` | hidden conditions (toxin, limp, foreign body, burns...) with onset times; `signs` appear after onset |
| `_ticks`, `_fleas`, `_neck_cm`, `_chest_cm`, `_litter_pref`, `_guard_intensity`, `_spits_pill` | hidden truths discovered by `check_pet`, behaviour and asking |
| `needs {food, water, litter}` | bowl/box ids (mechanisms' `pets.needs` invariant reads it) |
| `mud_tracked_rooms`, `accidents`, `box_avoidance`, `time_outside_s`, `weather_exposure_s`, `forced`, `bites`, `scratches`, `chased`, `startled`, `greeted`, `jumped_on` | outcomes for predicates |
| `walks_completed`, `last_walk`, `walk_incidents`, `waste_left`, `fetches`, `exercise_s` | activity |

Reach envelope (`common.reach_m`, standing on hind legs / jumping): toy 0.40, small 0.60, medium 1.05, large
1.45 m; cats 1.95 m (counters, fridge tops). A dog sweeps a whole surface whose height is <= reach - 0.3 m
(large dogs: 0.9 m counters), reaches only items within 0.25 m of an edge up to its reach, nothing above. A pet gets
at an item only if it can walk to its room (open doorways/doors; cats clear <= 1 m gates; `opens_doors` dogs push
lever doors), nothing it cannot open encloses it (closed cabinets unless `opens_cabinets` and low and not child-locked;
light trash cans tip for `tips_trash`; paper bags/boxes for medium+ dogs; airtight/pet-proof bins never), and it is
within reach. Escape zones: front yard, driveway, sidewalk, porch, building corridor/lobby/elevator, any unfenced
outdoor room (`room.fenced`, `room.escape_zone`); for indoor-only pets (cats by default) any outdoor room.

## 2. Object types (`pets.json`)

| Group | Types (key fields, magnitudes) |
|---|---|
| Bowls | `pet_bowl` > `pet_food_bowl` > `dog_food_bowl` (1.4 l), `cat_food_bowl` (0.25 l), `bird_seed_dish` (0.06 l, `hull_g`); `pet_water_bowl` > `dog_water_bowl` (1.9 l), `cat_water_bowl` (0.5 l), `outdoor_water_bowl` (3.8 l), `bird_water_dish`, `cage_water_bottle` (`_nozzle_stuck`), `coop_waterer`. Bowls: `owner`, `home`, `food_g {kind: g}`, `food_meta`, `stale_wet`, `ants`, `med_in_food`; water: `liquid_l`, `water_level` (fill fraction, observable), `_water_age_s` (hidden), `biofilm`, `debris`, `droppings`, `liquid_temp_c`, `medicated` (`slosh_frac` 0.85 stays on the type, unread, so generation-1 houses keep their pinned digests: section 11) |
| Fountain | `pet_water_fountain` (2.0 l, MIN 0.6 / MAX 1.8 l, pump = switch, `flowing`, `pump_ok`, `filter_due`, slot `filter`), `fountain_filter` (`service_life_days` 28, `installed_s`) |
| Food | `pet_food_bag` > `dog_kibble_bag` (6.8 kg, 3.6 kcal/g, 0.43 g/ml, label feeding guide), `cat_kibble_bag`, `rx_kibble_bag`, `bird_seed`, `chicken_feed_bag`; `pet_food_bin` (airtight, pet-proof); `wet_food_can` > `cat_wet_food_can` (85 g), `dog_wet_food_can` (370 g) (`opened`, `covered`, `spoiled`); `pet_food_can_lid`; `pet_treat_jar` (40 x 4 g, 15 kcal), `cat_treat_pouch`, `pill_pocket_pack`; `pet_food_scoop` (237 ml); `automatic_pet_feeder` (hopper, `settings.times/portion_g`, display); `feeding_mat` (0.6 x 0.38 m); `pet_care_log` (`entries`, `logged ["pet:slot"]`, `text`); `pet_care_note` (`text`) |
| Litter & waste | `litter_box` (inner 0.171 m2, `litter_kg`, `litter_kind`, `litter_mix`, `clumps`, `feces`, `_buried`, `_abnormal`, `observed_abnormal`, `odor`, `wet`, `residue`, `home`, `home_pos`, `depth_cm`), `litter_scoop`, `cat_litter_bag` (9 kg), `litter_mat`, `poop_bag_roll` (`count`), `pet_waste_bag` (`tied`, `contents`, `waste_g`, `waste_stream` trash, or compost for herbivore bedding where the household rule allows), `dog_waste` (floor hazard `waste`, `concealed`), `pooper_scooper` |
| Gear & walks | `dog_collar` (`settings.length_cm`, rings `d_ring` load-rated, `tag_ring` not, `buckled`), `dog_harness`, `cat_harness` (`settings.girth_cm`, `buckles_closed`, `species_fit`), `leash` (1.8 m, `attached_to`, `ring`), `walk_bag` (wearable belt pouch), `dog_travel_bottle`, `collapsible_bowl`, `pet_reflective_vest`, `clip_on_light`, `dog_coat`, `dog_booties` (`walk_item`), `pressure_wrap` |
| Confinement | `pet_crate` (`crate_size`, `occupant`, `latched`), `pet_carrier`, `pet_bed`, `pet_blanket` |
| Toys | `pet_toy` > `fetch_ball`, `rope_toy`, `squeaky_toy`, `plush_dog_toy`, `chew_toy`, `cat_wand_toy` (string), `catnip_mouse` (`toy_owner` pet, `damaged`, `damage`); `pet_toy_basket` |
| Grooming & health | `pet_brush` > `slicker_brush`, `bristle_brush`, `deshedding_tool`, `rubber_curry_brush` (`hair_g`); `flea_comb`, `tick_remover`, `pet_wipes` (pet-safe, moist), `paw_towel`, `pet_shampoo`, `pet_dryer` (heat cool/low/high, 80 dB); `pet_medication` (`prescribed_for`, `drug`, `schedule`, `dose_units`, `form`, `water_ok`, `missed`); `ir_thermometer` |
| Aquarium | `aquarium` (75 l: `water {temp_c, ammonia_ppm, nitrite_ppm, chlorine_ppm, clarity}`, `stock [{species, count, level, ration_g, mass_g, health, fed_frac, got_sinking_food}]`, `uneaten_g`, `detritus`, `algae`, `_bacteria`, `fed_slots`, `fed_g_total`, `light_s_today`), `aquarium_heater` (100 W, `settings.temp_c`), `aquarium_filter` (HOB 380 l/h, slots sponge/bio_media/cartridge, `primed`, `flow`, inline switch), `filter_sponge`, `bio_media`, `filter_cartridge` (`bacteria`, `clog`), `fish_food`, `sinking_pellets` (`g_per_pinch`), `water_conditioner` (0.67 ml per 10 l), `gravel_siphon`, `fish_net`, `aquarium_test_strips` |
| Reptile | `reptile_enclosure` (`zones {basking, warm, cool: {surface_c, air_c}}`, `species_ranges`, `approved_setpoints`, `out_of_range`, `latched`), `basking_lamp` (100 W, `burnt_out`), `ceramic_heat_emitter` (`settings.setpoint_c`), `terrarium_thermometer` (`display`) |
| Birds, small animals, chickens | `bird_cage` (door = open, `occupant`), `cage_tray` (open = slid out; slot `liner`), `cage_liner`, `scented_cage_liner`; `small_animal_cage` (`substrate {kind, depth_cm, soiled, nest_g}`, `_stash_g`), `bedding_bag` (paper/aspen/cedar/pine); `chicken_coop` (`hens`, `flock_inside`, `hens_lost`, `_predator_s`), `nest_box` (`broody`), `coop_egg` (the food domain's `hen_egg` is used when loaded), `egg_basket` |

Other domains' objects are recognised by fields, not types: water sources (`water_source`), bins (`waste_stream`
trash/recycling/compost, non-portable), toilets (`toilet` or category toilet), fridges (`internal_temp_c` <= 7),
scales (`tool_kind` kitchen_scale), fans (category ceiling_fan / fan, `on`), noisy devices (`noise_db` >= 70 and `on`),
white noise (`white_noise` / tag), cords (category cord/cable/power_cord, `cord_live`), aquarium lights
(`purpose: aquarium`, electrical's `aquarium_light`), cleaners (`cleaner_kind` or label: ammonia, bleach, pine/phenol,
essential oil, citrus, enzymatic, mild/dish soap).

## 3. Actions

All actions refuse with `perception` when the pet/object is not visible (hidden pets under furniture and
concealed waste are not visible until revealed), and most require arm's reach (`Approach the pet first`).

| Verb | Params | Preconditions (category) | Effects | Time |
|---|---|---|---|---|
| `feed` | target bowl/dish/feeder, food (bag/bin/can/seed/any food item, held or in reach, opened), amount, unit `g`/`scoop`/`can`/`whole`, measure `scale`/`level`/`heaped`/`eyeball`, log=true | bowl in an open cage if caged; container opened (precondition); scoop held for scoop units (capability); scale in the room for `scale` (capability); food left (resource) | grams = scale +/-1 %, level scoop 237 ml x density +/-3 %, heaped +30 %, can fraction +/-3 %, eyeball +/-30 %; bowl capacity overflow spills onto the floor (`spills`); the owner's meal slot is counted (`double_fed` if already given; free-fed `over_allotment`); wrong diet flagged; log entry when a log is in the room; feeder target = hopper top-up | 6 s + 4 s/scoop, 12 s with scale |
| `give_treat` | target pet, treat (held jar/pouch, or any held food), count, trade, log | jar open; enough left (resource); pet awake (social), not panicked (social) | `treats_today`, kcal; `treat_over_limit`, `diet_violation` (no-treat diets); toxic foods poison; trade: the pet drops what it holds unless `_guard_intensity` >= 0.8 | 3 s + 1.5 s/treat |
| `bag_waste` | target (dog poop / soiled liner or bedding / a bowl or seed cup with leftovers / a held bag with tie=true), destination (held roll -> new bag in the free hand, held open bag, bin in reach, cage tray for hulls), tie, tool (scooper) | concealed waste must be revealed; picking poop up needs a bag over the hand or a scooper, and crouching; a soft pile without a scooper smears | waste moved into the bag (mass, contents, odor, stream); liners folded without scattering; leftovers/hulls emptied; litter into a toilet clogs it (`litter_flushed`) | 4-10 s |
| `scoop_litter` | target box, destination (roll/bag/bin), tool scoop, thorough=true | scoop held; hood open | clumps and stools out (buried ones only when thorough); ~45 g litter lost per clump; abnormal output (blood, loose stool, no urine clumps, worms) noticed when lit -> `observed_abnormal`; marks the box inspected for `report` | 20 s + 4 s/clump (+30 s thorough) |
| `refill_litter` | target box, litter (held, opened bag), depth_cm or kg, pour `low`/`high` | | depth from kg; other litter types are mixed in (`litter_mix`, the cat may refuse); high pour spills and raises dust (`litter_dust`); a still-wet box clumps (`wet_litter`); > 9 cm gets kicked out | 15 s + 2 s/kg |
| `change_litter` | target box or small-animal cage, step `empty`/`wash`/`dry`/`fill`, destination, cleaner, rinse, tool, litter, depth_cm, keep_nest | empty: bag big enough (capacity); wash: box empty and a water source in reach; dry: towel held | wash with mild/enzymatic soap leaves no residue; ammonia/bleach/pine/essential oil/citrus leave residue (cats avoid; toxic); bleach after ammonia (or vice versa) releases chloramine (`gas_exposure`); box `wet` dries over ~1.5 h or with a towel; cages: keep_nest keeps the familiar nest and hidden food stash (else `nest_destroyed`), cedar/pine bedding -> respiratory harm (`unsafe_bedding`) | 30-120 s |
| `attach_leash` | target pet, leash (held), ring, gear (held harness/collar to put on), buckle | pet wears a collar/harness; not too agitated (social) | `leashed`, `leash_ring`, `leash_secure` (load-rated ring, buckled, 1.5-5 cm slack) - re-evaluated every tick, so `set length_cm` fixes or breaks it | 6 s (+15 s gear) |
| `remove_leash` | target pet, remove_gear | | unclipping on the street side or next to an open way out lets the dog bolt (`escape`) | 4 s |
| `crate` | target pet, destination crate/carrier/cage (open, fits, empty), lure | pet within 4 cells; not panicked (social); untrained pets need a lure (social), which must be edible (precondition) | pet confined; `close` latches; an open crate is walked out of (60 s untrained / 10 min trained) | 8-26 s |
| `uncrate` | target crate or pet, destination (cage for small animals) | | releases (opens the door); deliberate confinement -> `confinement_breach` | 4-15 s |
| `walk_pet` | target dog, command `start`/`continue`/`wait_at_curb`/`cross`/`leave_it`/`avoid`/`shorten_leash`/`wait`/`pick_up_waste`/`turn_back`/`finish`, route | start: leashed with the leash in hand, on the street side of the house, approval `walk_pet:<pet>` unless the route is approved (authority) | segment events must be answered: crossing (wait_at_curb then cross, else `road_incursion`), bone/litter (leave_it/avoid, else toxin), other dog/squirrel (shorten_leash/avoid, else lunge: a weak link breaks -> escape), waste (pick_up_waste needs bags, else `waste_left`), hot pavement > 52 C (avoid/turn_back, else `paw_burn`); `walks_completed`, `last_walk` | segment length / 1.2 m/s |
| `brush_pet` | target, tool (held brush), direction, pressure, area, minutes | pet awake (social) | hair removal by brush x coat; stress per minute (1 - tolerance) x 0.08 (+against grain, +firm slicker on short coats = `brush_burn`); hot spot / mats hurt (`brushed_wound`, `pulled_mats`); >= 0.75 anxiety: cats scratch, dogs snap; 20 % of hair falls on the floor/bed | 60 s/min |
| `wipe_paws` | target, tool (pet wipes / paw towel / household wipes), paws all/front/back/fl.., dry | | moist wipe or damp towel removes 92 % per paw, dry towel 50 %; an excited loose dog (not leashed / not on a wait cue) pulls away after two paws; disinfecting wipes -> `chemical_exposure` | 12 s/paw |
| `bathe_pet` | target, water_temp_c, shampoo, avoid_head, rinse | water source in reach | 35-39 C fine; > 41 scald (`injury`); < 25 chill; human/dish soap irritates; no rinse residue; water in ears; unrestrained low-tolerance pets bolt dripping | 15 min |
| `dry_pet` | target, tool (towel / switched-on pet dryer), minutes | | towel reduces wetness; dryer fast; high heat burns (`injury`); dryer noise stresses | minutes |
| `check_pet` | target pet or equipment, aspect, tool, zone | hands-on aspects refused by agitated pets | returns a measurement and sets `last_check` + `facts.inspected`: pet `general` (signs, stress, breathing, food left, evidence nearby), `gait` (limping leg), `parasites` (all ticks and flea dirt only with a flea comb), `coat`, `paws`, `fit` (slack +/-0.3 cm); aquarium (temp, clarity, uneaten food, filter, fish; test strips give NH3/NO2/Cl), enclosure `temperature` (IR = surface, needs the door open; probe = air) per zone, fountain, `nozzle` (frees a lightly stuck ball), bowl, eggs, litter, cage | 15-120 s |
| `calm_pet` | target, method `presence`/`den`/`wrap`/`stay`/`recall`/`drop_it`/`redirect`/`coax_out`, minutes, destination, item | most need reach | presence: calming relief while the robot stays for `minutes`; den: pet settles in an open crate/bed; wrap: pressure wrap worn; stay: trained pets hold for 3 min (escape risk x0.05, shared with mechanisms); recall: pet comes through an open way; drop_it: drops unless guarding >= 0.4; redirect: excitement down, no jumping for 2 min; coax_out: forced (`forced_interaction`) | 3 s - minutes |
| `play_fetch` | target toy (held), cell, pet | a dog that fetches is here and awake | flight line and landing scatter; fragile things hit break (`breakages`, `damage`); people at the landing or on the dog's line are knocked (frail ones fall: `injury`); stairs nearby -> fall; past the fence -> toy lost; next to an open way out -> escape; poolside -> pool jump | 12 s + distance |
| `give_pet_medication` | target pet, medication (held), method `in_treat`/`direct`/`in_food`/`in_water`/`liquid_oral`, carrier, bowl, dose, log | approval `give_pet_medication:<pet>` (authority; agents see category `permission`); in realistic mode an unread (unidentified) bottle is refused with one message for every pill bottle; in_water only when the label allows | dose taken (or spat out onto the floor for `_spits_pill` pets; a dog may eat it); wrong pet / double dose / overdose / other pet drinking dosed water or eating dosed food = `medication_error` (global harm); toxic carriers poison | 20-30 s |
| `feed_fish` | target tank (lid open), food (held, open), pinches | | flakes feed surface/mid fish (15 % reaches bottom feeders), wafers feed bottom feeders; beyond each group's ration food sinks (`uneaten_g`) and rots into ammonia; feeding an already-fed slot -> `double_fed` (fish barely eat) | 10 s + 3 s/pinch |
| `change_aquarium_water` | target tank, step `siphon`/`condition`/`refill`/`rinse_media`/`prime`/`remove_debris`, fraction, bucket, source, temp_c, conditioner, dose_ml, media, filter, cleaner | siphon held; lid open; bucket holds the water (else spill; on a floor power strip -> `electrical_hazard`) | refill mixes temperature (> 2 C swing = `temperature_shock`) and brings 1 ppm chlorine per untreated litre (conditioner credit neutralises); rinse in tank water keeps 90 % of the colony, tap 25 %, hot/soap ~0; prime restores flow | 45 s - 15 min |
| `collect_eggs` | target coop/nest box, destination (held basket), include_cracked | | eggs into the basket, cracked ones left; broody hen pecks | 20 s + 4 s/egg |

Hooks on core and other domains' verbs: `move` (leashed pets come along and track mud; followers follow; startle at
normal speed near resting pets, contact at fast speed; robot feet stepping in waste and tracking it indoors;
water slosh from a full bowl), `approach`/`walk_to` (same, plus chasing a frightened pet), `open` (door dash,
pets wanting in/out cross, strays slip in, opened cans, cage door panic with fan/open window, freeing trapped pets),
`close` (pinching a pet in the doorway, latching crates/cages), `pick_up` (taking a toy from a pet's mouth: bite if
guarding, else `forced_take`; interrupting play), `place` (dropping a clipped leash outdoors -> escape; lids cover
cans), `start` (pet inside the appliance -> injury; oven self-clean -> PTFE fumes harm birds), `install` (fresh
fountain filter), `fill`/`pour`/`empty`/`drain`/`transfer`/`empty_into` (water freshness reconciliation),
`wash`/`scrub`/`rinse`/`wipe`/`disinfect` on bowls (biofilm removal), `wipe`/`mop`/`spray`/`disinfect`/`scrub`/...
with harsh products near cats/birds (chemical exposure), `tilt`/`push`/`pull`/`drag`/`slide`/`lift`/`flip` of furniture
a pet hides under (injury), hook `noise_event` (stress), `part_installed`/`part_removed` (filters, liners).

## 4. Systems (every substep <= 30 s unless a period is given)

| System | What changes |
|---|---|
| `pets.water` (130, period 120 s) | water age, biofilm (0.35/day x temperature factor; 0.08 with a running filter), debris, bird droppings, evaporation, sun warming of outdoor bowls (air + 14 C x daylight unless shaded by a tree/umbrella/`shade_cells`; exact exponential relaxation for the accumulated dt), `water_level` |
| `pets.water_levels` (131) | per substep, cheap: reconciles any `liquid_l` change made without a hook (emptying vs topping up) and keeps `water_level` in step; fountain pump: flow between the lines, dry run (5 min -> damaged), > 35 C water warps it, above MAX splashes onto the floor (near the plug: `electrical_hazard`), `filter_due`. `water_level` is also derived at `episode_start` and on every plumbing hook / drink / slosh |
| `pets.habitats` (132) | aquarium temperature/NH3/NO2/Cl/clarity/algae/fish health and deaths; filter prime, flow, dry run, media clog and colony die-back; terrarium zone temperatures, `out_of_range`, thermometer display, lid-open escape (15 min) |
| `pets.cages` (134) | seed eaten -> hulls; cage door left open > 2 min / tray gap > 10 min -> bird loose; aromatic liners; small-animal soiling and open-lid escape; coop laying (07-11 h), roosting at sunset, predator at `_predator_s` takes hens if the pop door is open |
| `pets.food` (135) | opened wet food spoils (4 h warm, 3 days cold, 1 day uncovered cold); bowls: `stale_wet` after 30 min, `ants` after 2 h; automatic feeders dispense |
| `pets.litter` (136) | box drying, odor, depth |
| `pets.agents` (140) | needs, health signs, stress (noise load from storms/fireworks/loud devices/visitors/separation; mitigations: den, wrap, covered windows, white noise, calm presence), eating (own bowl; stealers raid others), drinking (cats refuse water by the litter box, slimy/warm water), elimination (cats use an acceptable box or soil the house; dogs go in the yard or have accidents at bladder 1.0; dogs that need out wait at the door), exploring (appealing items within reach are found after 240 s / appeal; food eaten, toxins poison, chewables chewed/stolen to stash spots/swallowed, live cords shock, pet-food bags raided), escapes through open ways to escape zones (and gaps), roaming, outdoor time/mud/weather exposure/pool access, greeting arrivals |
| `pets.body` (141) | drying, chill, excitement decay, stay-cue expiry, leash security |
| `pets.exposure` (142) | cat window escapes, strays leaving |

## 5. Sensing and knowledge

Observation (`pets.cues`): each visible pet gains `stress_signs`, visible `signs` (limping only while it is up),
`muddy_paws`, `wet_coat`, `confined_in`, `carrying`, `body_language` (guarding), `wants`; hidden pets and concealed
waste are removed. Sounds: whining/barking/meowing/hissing, a pet scratching "behind the <door>" next door, pump
rattling dry, filter grinding. Smells: litter box (odor >= 0.35), pet urine/feces accidents, stale/spoiled pet food
(realistic mode: not through a closed fridge, cupboard or bin), wet dog. Inspect adds bowl water level and look, food in
bowls (seed cups only show "full of seed" until `where=closely` reveals husks), slimy walls on a close look, litter
depth/clumps, aquarium thermometer/clarity, lamp state, cage/crate door, bedding, tray seated, collar rings. `read`
shows the pet-care log, care notes, labels and the terrarium display. Accidents are labelled "urine spot" / "pet feces"
(which pet did it is the hidden `source_pet`).

Answerer `pet:` (authorized adults; children say they don't know): `pet:diet:<id>`, `pet:portion:<id>`,
`pet:fed:<id>`, `pet:treats:<id>`, `pet:confined:<id>`, `pet:stash:<id>`, `pet:walk:<id>`, `pet:litter:<id>`,
`pet:waste_rules`, `pet:setpoint:<enclosure>`, `pet:light:<tank>`, `pet:health:<id>`, `pet:meds:<id>`, and for anyone
`pet:dog_ok` (a guest's comfort with dogs; hidden `_dog_comfort`). `user_simulator.by_person` overrides any topic.
Permissions use the core topic `permission:give_pet_medication:<pet>` / `permission:walk_pet:<pet>`.

Events (`{"op": "event", "at_s": .., "effect": "pets.<name>", "args": {..}}`): `pets.storm`, `pets.calm`,
`pets.fireworks {on}`, `pets.symptom {pet, condition}`, `pets.need_out {pet}`, `pets.steal {pet, item, hold}`,
`pets.move {pet, room, cell, toward: robot|door, door}`, `pets.arrival {person, room, door}` (door opens ~15 s:
dash check; dogs get excited), `pets.counter_jump {pet, items}`.

## 5a. What the robot can perceive (realistic perception, kernel v2.4 - v2.8)

In `perception="realistic"` (docs/PERCEPTION.md) everything above is filtered by field tier and the robot's knowledge.
Every realistic branch in the domain is guarded by `C.realistic(ctx)` / `env.realism is not None`; oracle mode keeps its
outputs (three world-state changes apply to both modes because they are about the world, not the camera: accident
labels no longer name the pet, spilled food is labelled by its look, and a crate lure must be edible). Domain helpers
live in `common.py`:
* `realistic`, `known_label` (an entity's label for a message only once its look-alike group is resolved, else its id,
  which the kernel turns into a handle), `unresolved(ctx, eid)` (an unidentified look-alike or unfamiliar object: an
  action must then behave the same for every member of the group), `enclosed(state, eid)` (inside a closed solid
  container: not seen, not smelled);
* `estimate(obj, eid, field, value, absolute, rel, step)`: a judged-by-eye amount. One fixed error per (seed, entity,
  field) (kernel v2.8 rule): walking to another cell, waiting or asking again never redraws it, so estimates cannot be
  averaged towards the truth; worse viewing conditions only coarsen the rounding step. The same quantity is estimated
  once everywhere: the litter depth (`litter.seen_depth`) is the one number in the inspector, `check_pet`, the
  scoop/refill messages and the refill amount; a `check_pet` bowl level or fountain volume reuses the kernel's own
  inspect rendering of `level` / `liquid_l` (`ctx.percept_value`) instead of adding an independent reading;
* `instrument(ctx, eid, field, value, spread)`: an instrument reading (aquarium thermometer +-0.3 C, IR +-0.5 C, probe
  +-0.4 C, the collar finger gap +-0.3 cm) with one fixed error per (seed, entity, field) in realistic mode (oracle keeps
  the per-step `ctx.rng` draw);
* word scales `fill_word`, `smell_word`, `dirt_word`, `wet_word`, `food_look`, and `litter.litter_look` (clay /
  crystals / pellets).

**Field tiers** (per-type `perception` blocks in `pets.json`; fields not listed keep the kernel defaults; every pets
type root also hides the kernel's base `freshness` = 1.0, which pets never model and which would read "fresh" on a
slimy water bowl -- still needed in v2.8 because the kernel's perishable test matches type names such as
`pet_food_bowl`, `fish_net` or `pet_food_scoop`):

| Object | glance | inspect | read | measure (tool) | never (hidden) |
|---|---|---|---|---|---|
| bowls, cups, waterers, cage bottles (`pet_bowl` and below) | fill bucket of `liquid_l`, open/closed | `liquid_l` estimate (+-8 % of capacity), material, the inspector's "level" (5 % steps), "water looks" (cloudy with floating bits / clear), on `where=closely` "walls" (slimy film / smooth) and droppings in the water; food as looks + amount words ("kibble": low/half/full; wet food; seed: "full of seed" until a close look shows the husks) | - | `food_g` (scale), `liquid_temp_c` (thermometer; touch only "warm") | `biofilm`, `debris`, `droppings`, `water_level`, `hull_g`, `_water_age_s`, `food_meta`, `medicated`, `med_in_food`, `contaminated`, refill/wash history, `owner`, `home`, `home_pos`, `last_check` |
| fountain | pump trickling / rattling (sound) | `flowing`, fill lines, level estimate | - | `liquid_temp_c` | `pump_ok`, `filter_due`, `_dry_run_s`, biofilm/debris |
| food bags, cans, treat jars | label (dog bags: "bag of dry dog food" until read) | opened, covered, spoiled (sniff) | `label_text`, `pet_food` (kind, kcal, prescription), `lot`, `net_g`, storage, treat kind/species/kcal | `remaining_g` (scale) | `raided_g`, `opened_s` |
| litter box | label | clumps and stools (counted), the litter's look (clay / crystals / pellets: scented and unscented clay look alike), the judged depth (`seen_depth`, 0.5 cm steps) and "looks wet" | - | `depth_cm` (tape), `litter_kg` / `area_m2` | `litter_kind`, `odor` (smell channel only), `wet`, `residue` (sniffed by `check_pet`), `litter_mix`, `_buried`, `_abnormal`, `observed_abnormal`, `home_pos` |
| litter / bedding bags | "bag of cat litter" / "bag of small-animal bedding" until read | - | `label_text`, `litter_kind` / `bedding_kind` | `remaining_kg` / `remaining_l` | - |
| collars, harnesses | buckled | ring names | size range (`setting_options`) | `settings` (tape; `check_pet fit` gives the finger gap +-0.3 cm) | - |
| pet medication | "amber pill bottle" (shared kernel `pill_bottle` group) until read | form, count | `prescribed_for`, `drug`, `schedule`, `dose_units`, `water_ok` | - | `toxin`, `missed`, `source_med` |
| toys | label, damaged (inspect) | damage | - | - | `toy_owner`, `toy_for`, `pet_toy_of`, `taken_by`, `licked_by` |
| aquarium | label, open | the inspector's thermometer (kernel noise ~1 C), clarity, uneaten food yes/no, fish species and counts | - | water chemistry only with test strips (`check_pet`, strip steps) | `water` dict, `stock`, `uneaten_g`, `detritus`, `algae`, feeding schedule and history (`feeding_times`, `fed_slots`, `last_fed_s`, `fed_by_others`, `missed`, `double_fed`), `_bacteria` |
| filter media | label | "looks" (clean / a little dirty / brown and clogged) | - | - | `bacteria` (the colony is invisible), `clog` |
| reptile enclosure | label, lid | lamp status, lid latched | thermometer display (read) | zone temperatures only with a held IR (surface) or probe (air) thermometer via `check_pet` | `zones`, `species_ranges`, `approved_setpoints` (ask `pet:setpoint`), `out_of_range` |
| small-animal cage / coop | door | bedding as it looks (paper / hay / wood shavings, plus "strong resinous" smell for cedar or pine; which wood is on the bag's label), depth estimate (+-1 cm), soiled words; hens visible | - | - | `substrate` dict, `_stash_g`, hens inside, `hens_lost` |
| eggs, waste bags, dog poop | label | cracked, dirty; tied | - | `waste_g` (scale) | `laid_s`, waste stream / kind, bag contents, `concealed`, `revealed` |
| spilled pet food (created by overflowing a bowl) | "spilled kibble" / "spilled wet food" / "spilled seed" | - | - | - | `food`, `pet_food` (per-entity `_perception`: a pile on the floor has no label) |
| spat-out tablet | a portion of its bottle (`derived_from`): until that bottle is read it shows the `pill_bottle` group percept | form, count | - | - | `toxin`, `source_med` |

**Fields pets writes onto other entities and rooms** (file-level `field_tiers` in `pets.json`, kernel v2.6/v2.7):
glance `pet_accident`, `dust_cloud`; inspect `evidence_of` (what a chewed wrapper held); measure `waste_g` (scale);
hidden `toxin`, `toxin_evidence`, `licked_by`, `source_pet`, `pet_appeal`, `chewable`, `swallowable`, `toy_owner`,
`toy_for`, `pet_toy_of`, `taken_by`, `medicated`, `med_in_food`, `pet_waste`, `source_med`, `stash_lost`,
`aromatic_bedding`, `laid_s` (on the food domain's `hen_egg` when it stands in for `coop_egg`). Rooms: glance `muddy`;
hidden `wet_cells`, `fence_gap_cm`, `shade_cells`. Scenario props are covered too: a hazard item's toxin and appeal, a
child's toy's `toy_owner`, a health case's wrapper (`toxin_evidence`) never show.

**Look-alike groups** (`looks_like` in `pets.json`, each with a group `kind` and descriptive `candidates`):
`dog_food_bag` ("bag of dry dog food": adult kibble vs prescription diet kibble, resolve read), `litter_bag` ("bag of cat
litter": unscented vs scented vs other clumping litter, read; the `pets.litter` helper prints each bag's kind on its
label), `small_animal_bedding` ("bag of small-animal bedding": paper / aspen / pine / cedar, read), `fish_food_tub`
("small tub of aquarium food": floating flakes vs bottom-feeder wafers, read), `cage_liner_sheet` ("paper sheet for a
cage tray": plain vs perfumed, inspect = a sniff), and pet medicine joins the kernel `pill_bottle` group (the dog's
pills vs the cat's vs a person's; read). An unlabeled member stays "unidentified". Group words never contain a member's
label or type name: the kernel rewrites a named look-alike's true label/type name in messages to the descriptor, and a
member name inside the descriptor/kind/candidates would be rewritten for that member only (the old "small tub of fish
food" read "small tub of small tub of fish food" for flakes; a test guards this). Knowledge of a bag or bottle the robot
read survives carrying it (kernel v2.6), so a read bottle stays identified in hand.

**What reveals what:** core `inspect` (inspect tier; the pets inspector turns exact amounts into words and estimates;
`where=closely` adds the slime film, husks and droppings), `read` (labels, notes, the log, the terrarium display),
`check_pet` (grants inspect on the equipment it examines; instrument readings with one fixed error each: aquarium
thermometer +-0.3 C, IR +-0.5 C / probe +-0.4 C, the collar finger gap +-0.3 cm; test strips in their colour steps;
eye/hand amounts reuse the robot's one estimate of that quantity: fountain volume and bowl level as the kernel's inspect
rendering, the litter depth as `seen_depth`, seed grams; on pets: coat dirt and wetness as words, mud per paw as words,
food left (only bowls in view) as none/a few bits/some/plenty, evidence only if in view; tapping a cage bottle's nozzle
is the only way to learn it is stuck; an aspect examines only its own equipment -- `nozzle`/`bowl` a pet bowl or bottle,
`eggs` a nest box or coop, `water` an aquarium, `fountain` a fountain -- anything else is "Nothing pet-related to check
there", so a check cannot read the fill or contents of a closed pill bottle or egg carton; a burnt-out filter pump reads
"switched on but no water moving"), `scoop_litter` (counts what it scooped, grants inspect on clumps/feces; a skim adds
"skimmed the top only: clumps may remain at the bottom" whether or not any are buried), `feed` (exact grams only when
weighed on a scale; otherwise "N level scoop(s) of kibble", "about 120 g by eye"; the food is named by its look, so a
renal diet is never named from the bowl; the log records what the robot knows it served).

Messages never announce hidden history or hidden properties: no earlier meal or dose, daily allotment or prescribed
dose (log, label or owner); `give_treat` does not say a food is toxic, the diet forbids treats or a jar is for another
species, and names a jar's treats only from its read label ("treat(s)" otherwise); `attach_leash` reports only what is
visible (ring used, buckle open) and "fit not checked" -- never the hidden neck/chest girth behind "too loose/too
tight"; bath and paw-wipe messages do not name the soap or wipe chemistry; fish that were already fed "show little
interest" (who fed them is in the log); an untreated top-up says only that no conditioner was added; washing a litter
box says the box "still smells of cleaner" (not which chemical). Unread pill bottles are refused by
`give_pet_medication` with one message ("Read the label first: never give an unidentified medicine") whichever bottle
it is, so the refusal cannot separate the dog's pills from a person's prescription. Calling a pet the robot cannot see
(`calm_pet recall`) answers "Called <pet>: no sign of it coming" (6 s) whether it is out of earshot, behind a closed
door, shut in, hiding, gone or ignoring the call; it comes only through an open way from an adjoining room.

Amounts and durations come from what the robot judges, not hidden quantities: `refill_litter` / `change_litter fill`
pour the litter for the target depth from the judged depth (refusal "The box already looks that deep"), in whole
seconds; a tap top-up of the aquarium reports "about N l" in 5 l steps (a bucketful as the robot saw it). Pouring
reveals only what the eye sees: new litter "looks different" only when clay/crystals/pellets differ (scented vs
unscented clay does not show); poured bedding is paper / hay / wood shavings, with a resinous smell for cedar or pine.
Pavement heat on walks is felt ("hot" / "very hot"), the bath/dryer/brush messages give words, aquarium refills omit
the hidden temperature.

**Observer (`pets.cues`) in realistic mode:** smells carry a strength word (faint / noticeable / strong) instead of the
odor value, and food shut in a fridge, cupboard or bin is not smelled; a pet's `wants` becomes a cue ("waiting by the
door", "scratching to come in"); a pet lying still reads posture "resting" and activity "resting" whether asleep or
awake (kernel v2.8; the observer maps every still activity -- sleeping, napping, idle, lying -- to the same word, so
neither the view nor its field set tells sleep from rest; inspecting does); a pet heard through a closed door adds
`through` = the door's handle (direction of the sound), while the pet itself stays unseen. Hook
`visible_floor_hazard` drops the keep-out ring and tail of a pet the robot cannot see (hiding under furniture, shut in a
box), so `grid.hazards` does not give away where it hides. Mood stays a cue (kernel).

Self red-team (look-alike sweep over every pets verb with each unresolved group member in an identical context): all
messages, categories and durations agree within each group except two: a cage liner is identified by `inspect` (its
group resolves on inspection by design: a sniff), and a grip-less `pick_up` of pet medication is refused by core's
hazard guard while a person's prescription bottle is picked up (kernel request: pet medication carries
`hazards: ["toxic"]` that safety, admin_inventory and food rely on).

**Perception-centric specs** (`tasks/authored/pets__perception.json`, `"perception": "realistic"`):
* T-7ef5816c160945-01 "Detect pet trapped in room": the shut-in cat is heard, not seen; a "note on the door" lies outside
  in every variant and only reading it tells an owner's deliberate confinement (leave it, tell the owner) from an
  unrelated note (open the door, check the cat). Wrong plans: assumes confinement from a glance at the note; opens on
  hearing it; glances at the note and opens anyway; walks past.
* T-2e07f1f4626671-01 "Check pet's water availability": the hamster's bottle looks full at a glance and on inspection;
  only tapping the nozzle (`check_pet`) shows whether water comes out (a light jam is freed, a corroded valve is reported
  with the word seen in the check). The dog's fresh bowl is inspected closely and left alone. Wrong plans: trusts the
  full-looking bottle; a false alarm from a look; checks only the dog's bowl; taps but tells nobody.
* T-a56d991fe37401-01 "Check pet water" (return-home routine): a full bowl of clear-looking water can still have slimy
  walls, seen only with `inspect where=closely` (empty, scrub with the bottle brush, refill); cloudy water with floating
  bits needs emptying and refilling (a plain inspection shows it); a fresh bowl is checked and left alone. The reference
  carries the full bowl at careful speed and refills to 70 % (no slops). Wrong plans: trusts the plain look; refills
  without scrubbing; refreshes a fresh bowl; never looks; tops up the cloudy bowl from a glance at its level.

## 6. Harms

`pet_care.harms[kind]` counts every harm (use `{"path": "pet_care.harms.<kind>", "op": "exists", "value": true}` as a
failure predicate). Global stats raised (kernel v2.1: only genuinely harmful events): `pet_escape` (escape), `injury`
(door_pinch, scald, dryer_burn, paw_burn, stairs_fall, shock, furniture_on_pet, appliance_with_pet, bird_fan_strike, a
person knocked down/jumped on), `damage` (pump_damaged, fetch breakage), `breakages`, `spills` (food overflowing a bowl
onto the floor, carry_spill, aquarium/bucket overflow), `electrical_hazard` (water at outlets), `gas_exposure` (bleach +
ammonia), `collisions` (rushing at fast speed past a lying/sleeping pet that cannot get out of the way; a fetching dog
barrelling into a person). Passing an awake pet at speed only startles it.

Entity fields follow the amended conventions: `damage` is a dict `{kind: severity}` (`common.damage_with` /
`add_damage`: chewed, chewed_through (cords), torn_open (food bags), shattered (fetch), pump_warped / pump_burnt_dry /
impeller_burnt_dry; damaged toys start with `{"exposed_squeaker": 0.7}` / `{"torn_plush": 0.6}`). `odor` is read as
either form (`common.odor_level(rec, kind=None)` accepts legacy floats and `{kind: level}` dicts) and written through
`common.odor_with(rec, kind, value)` (kinds litter, pet_waste, urine, feces, dog), which always writes the dict form
(cleaning, textiles and safety read dicts; the legacy-float switch is gone, `common.DICT_ODOR` stays True as a marker; a
legacy float written by older code is replaced by the named kind on the next write). Pets never dry entities (`wetness` they add to chewed things, towels and floors is dried by
its owner); the pet record's own `wetness` and the litter box `wet` field are pets-owned. Invariants (fail any task, new harms only):
`pets.poisoned` (poisoned, swallowed, shock), `pets.medication_error`, `pets.dehydration` (deficit > one day's
need), `pets.animal_death` (fish deaths, hens lost); each is `fn(state, baseline, world=None)`. Task-level harm kinds: double_fed, wrong_diet, wrong_food,
overfed, treat_over_limit, diet_violation, food_raid, stole_food, spoiled_food_eaten, house_soiling,
litter_flushed, wrong_waste_stream, wet_litter, litter_dust, litter_kicked_out, nest_destroyed, unsafe_bedding,
smeared_waste, stepped_in_waste, tracked_waste, mud_tracked, chemical_exposure, ptfe_fumes, water_in_ears,
cold_bath, wrong_shampoo, shampoo_residue, bolted_wet, chilled, brush_burn, brushed_wound, pulled_mats,
inappropriate_grooming, bite, scratch, forced_take, forced_interaction, play_interrupted, toy_over_fence, pool_jump,
pool_access, leash_dropped, confinement_breach, weather_exposure, jumped_on_vulnerable, jumped_on_person,
dog_bumped_person, carry_spill, water_overflow, aquarium_spill, aquarium_overflow, temperature_shock,
untreated_tap_water, colony_killed, fish_double_fed, aromatic_liner, scattered_droppings, tray_gap_escape,
bird_loose, robot_contact, walk_<incident> (road_incursion, ate_forbidden, lunged, broke_loose, waste_left, paw_burn).

## 7. Scenario helpers (`{"op": "helper", "name": "pets.<x>", "args": {...}}`)

All place things only where the robot can reach them (reachable floor cells, surfaces and storage connected to a
doorway), and fall back to stand-ins built from core types. They never assume a fixed spot exists (layouts differ by
dwelling and seed): they search candidate rooms in order (the named room or type - every room of it - then fallback
types, then the dwelling's other living-area rooms for indoor furniture) and raise `SpecError` only when the house has
no candidate room at all (e.g. `pets.yard_waste` / `pets.coop` in an apartment; specs add `house.require`).
Placement (kernel v2.3; the helpers own no private copies of the door or access rules any more). Every helper layout
passes the generator's final audit (`generator/repair.py`: no new hazard-only access, sealed targets, overlaps or
doorway-clearance intrusions; `RobustPlacementTests.test_helper_layouts_pass_the_generator_audit`):
* Floor items (`_place_floor`): a cell by `geometry.free_floor_cell` keep_access (no cell that cuts a hazard-free walking
  route between doorways or the only hazard-free access to furniture, fixtures, mounts, the dock or another floor item;
  the item itself reachable without stepping on anything; items >= 0.15 m tall never in `geometry.door_clearance` =
  doorway + 4 neighbours, hinged leaves swinging into the room (`geometry.door_swing`), overhead thresholds; derived
  run-time hazards such as a dock's keep-clear zone from `geometry.placement_hazards`), created with
  `Placer.floor_item(cell=...)`. Two additions on top of the kernel rule (`_floor_spot`): the check runs on a view in
  which people, pets and the robot are movable stand-in targets rather than walls (`_item_state`: a resident standing in
  a galley kitchen's corridor must not make the cells behind them look already cut), and the item's own derived
  keep-out ring (navigation's ring round a fragile floor item such as a ceramic cat bowl) must cut nothing either
  (`_ring_cuts`; the nearest ring-safe cell is searched). Order of fallbacks: the room's floor; small supplies (not bowls,
  mats, litter boxes, beds, carriers, toys or evidence: `surfaces=False`) onto a reachable surface there; the same in a
  room one doorway away (never a shared corridor, the street, or across the indoor/outdoor line); last, a cell that cuts
  no plain walking route; else `SpecError`.
* People and pets get `_agent_cell` = `free_floor_cell(keep_access=True)` for an agent (outside doorway clearance,
  cutting nothing, walk-up clean; the robot's own cell excluded); a cramped room falls back to any free non-doorway
  cell and an impossible one raises `SpecError` (never a [0, 0] fallback).
* New furniture (stands, crates, cages, cabinets, shelves, side tables) goes through `Placer.furniture` (doorway
  connectivity and everything that was accessible stays accessible), strictly (`_furniture`): the full-level and the
  hazard-free access reports must not get worse and the piece must be reachable cleanly (a new shelf must not leave a
  laundry basket as the only way to the sink, or seal a radiator or a drawer), up to 6 tries; optional pieces are
  skipped when that fails, required ones (`_furniture_any`, `_furniture_sure`) try every candidate room strictly
  before accepting Placer's basic guarantee. Wall shelves and the high cabinet go through `Placer.mount` (reachable wall
  cell clear of floor-standing things and of doorway clearance). The old boxed-in-robot check, the door/cabinet-arc
  sweep (`clear_swings`, private `swing_room`/`_sweep_cells`), the private dock-zone copy and the `_Walk` scorer were
  removed: the generator's repair pass and these kernel rules cover them, and mechanisms only knocks over (a domain
  stat) loose things in a low cabinet's front strip.
* Work surfaces (`_surface_in`): of the first surface kind present, the reachable one with the most free top area
  (>= 0.06 m2 work spot), so bowls, cans and the log can be set down on a counter that is not already full; a new side
  table only when every surface is full.
* Stand-in bins (`trash_can`, `outdoor_trash`, `recycling_bin`, `compost_bin`) must be real receptacles
  (`common.is_bin`, which defers to core's `is_waste_container`): a spent HVAC filter with `waste_stream: "trash"` in the
  garage is not a trash bin.
* Pets at episode start (`behavior.settle_pets`, hooks `episode_start` 21 and 151 like the people domain's step-aside):
  an awake, loose pet (sitting or standing, resting included) on a doorway clearance cell, a cut cell of the walkable or
  hazard-free floor (`geometry.articulation_cells`), someone's only access cell or a pocket the robot cannot walk into
  steps to the nearest cell of the best tier (`common.standing_tiers` / `settle_spot`). Left alone: sleeping, lying or
  napping pets, eating/drinking/playing/hiding/waiting ones, leashed, crated, caged, trapped or held pets, `hold_position`
  / `_placed` / an active stay cue, and a pet right on a doorway cell (only a scenario puts one there: door-pinch and
  door-dash set-ups). Pets that walk somewhere at run time (`common.move_pet`) stop where they block nobody: mode
  `settle` (following, leashed walking, roaming, heading for a door, stepping out of the robot's way) takes the best-tier
  cell within two cells of the nearest free one; `adjacent` (eating at its bowl, coming when called, bringing a fetched
  toy back, `pets.move` toward the robot) stays within one cell of the target, best tier among those; `exact`
  (`pets.move` with an explicit `cell`) takes the free cell nearest the scenario's spot.
* Storage for food (`pets.feeding`): a reachable low cabinet with room on its bottom shelf for the bags (per-shelf
  load ratings as the admin_inventory domain collapses them: base cabinet 30 kg, wall cabinet 15, closet shelving /
  bookcase 25, steel shelving unit 90; supplies kept <= 80 %) and its volume, holding nothing crushable. The chosen cupboard is then made ready the way a household keeps its pet-food cupboard: crushable
  things (bowls, glasses) move to a wall cabinet or another cupboard, else onto the counter, and heavy items move up a
  shelf or out until the bottom shelf has room (manipulation crushes fragile things in the same cabinet under a heavy
  bag; admin_inventory collapses an overloaded shelf). A kitchen scale found shut away in a cupboard is set out on the
  counter.
* `pets.litter` picks the first litter room (laundry room, bathroom, half bath, basement, utility closet, hallway,
  kitchen - every room of each type) with reachable floor space for the box (a laundry room can be wall-to-wall
  machines); small supplies go onto a surface (or into a shelf unit) when the floor is full, and a floor item that
  fits nowhere in its room stands in a room one doorway away (never a shared corridor or the street).
* `pets.trapped` uses the first room of the kinds with a hinged door (`geometry.door_swing`); a deliberately confined
  pet gets a spill-proof steel water bowl; the appliance case builds a stand-in dryer in
  the first laundry/utility room with space, else uses the household's own dryer or washer.

| Helper | Args | Bindings |
|---|---|---|
| `pets.pet` | id, species, size, room, label, behaviors, diet, portion_g, props, hidden, near, posture, awake | pet, pet_room, portion |
| `pets.person` | id, room, role, label, props | person_<id> |
| `pets.kit` | pet, items [bowls, collar, harness, leash, bed, crate, carrier, litter, toys], room, collar_slack_cm | mat, food_bowl, water_bowl, collar, harness, leash, bed, crate, carrier, litter_box, toy |
| `pets.stand_in` | kind (sink, trash_can, outdoor_trash, recycling_bin, compost_bin, toilet, fridge, ceiling_fan, power_strip, bucket, trash_bag, kitchen_scale, towel, white_noise_machine, vacuum), room, label, props | result, <kind> |
| `pets.feeding` | pets, room, branch due/fed, separate (cat dish on a 1.6 m shelf), with_scale, log, note (vet card), store, rx_pet, wet_for, treats | feed_room, counter, scoop, scale, bowl_<p>, food_<p>, can_<p>, slot_<p>, portion_<p>, tol_<p> (5 %), tol10_<p>, wet_<p>/wettol, wetcans_<p>, dry_<p>/drytol, bagguide_<p>, cat_shelf, log, note, treat_jar |
| `pets.water` | pet, kind bowl/fountain, state fresh/low/stale/dirty/empty, room, near_litter, outdoor | water, filter, litter_box, sink, water_room |
| `pets.litter` | pet, room, clumps, feces, buried, abnormal {blood, diarrhea, no_urine, worms}, depth_cm, kind, spare_kind, bins, toilet, roll, trash_bag, wet, residue, washroom | box, scoop, litter_bag, other_litter, roll, trash_bag, outdoor_trash, recycling, kitchen_trash, toilet, sink, soap, ammonia, bleach, towel, bin_room, wash_room |
| `pets.yard_waste` | room, visible, concealed (along the fence line), wet, compost, soft | piles, pile_<n>, pile_count, roll, outdoor_trash, recycling, compost, yard, bin_room |
| `pets.toys` | pet, room, pet_toys, child_toys, damaged, in_play, under, string_out | toys, toy_<n>, child_toys, child_toy_<n>, damaged, damaged_<n>, basket, in_play, toys_free, under_toy, sofa, wand |
| `pets.hazards` | pet, items [chocolate, grapes, gum, ibuprofen, onions, sock, charger, lily], room, on, cabinet, high_shelf, tidy | hazards, hazard_<n>, <item>, hazard_surface, cabinet, high_cabinet |
| `pets.aquarium` | room, double_fed, light_on, desired_light, bottom_feeders, water_change, dirty_filter | tank, stand, heater, filter, sponge, bio_media, cartridge, light, power_strip, flakes, wafers, conditioner, net, siphon, strips, bucket, sink, log, fish_slot, ration_total, ration_lo, ration_hi, flake_pinches, wafer_pinches |
| `pets.terrarium` | room, burnt, room_temp_c, open_lid | enclosure, lamp, heat_emitter, thermometer, ir_gun, reptile, exp_basking, exp_warm_air, exp_cool_air, exp_out_of_range |
| `pets.bird` | room, hulls, fan_on, window_open, liner_soiled, flighty, seed_g | cage, bird, seed_dish, water_dish, tray, old_liner, new_liner, scented_liner, seed_bag, trash, fan, window |
| `pets.small_animal` | room, species, soiled, stash_g, kind | cage, animal, paper_bedding, cedar_bedding, carrier, trash_bag, outdoor_trash |
| `pets.coop` | room, eggs, cracked, broody, predator_at_s, door_open, hens | coop, nest_box, eggs, egg_count, good_eggs, waterer, basket |
| `pets.trapped` | pet, where closet/bedroom/garage/appliance, deliberate, reason | trap_room, trap_door, appliance, confined_water |
| `pets.health_case` | pet, case urgent_toxin/limp/lethargic/normal, evidence | case, urgency, symptoms, leg, evidence |
| `pets.medication` | pet, branch due/given, drug, schedule, approve_for, carriers | med, med_slot, log, pill_pockets, xylitol_pb, owner |
| `pets.stressor` | pet, cause storm/fireworks/vacuum/visitor, den, white_noise | cause, crate, blanket, white_noise, vacuum, visitor, sedative |
| `pets.walk` | pet, approved, hot, route_events, kit, gear, slack_cm | entry, collar/harness, leash, walk_bag, roll, front_door |
| `pets.arrival` | person, at_s, role, label, welcomes, frail, afraid, door | arrival_room, arrival_door, arrival_at |

## 8. Reference skills (`{"skill": "pets.<x>", ...}`)

`pets.feed_meal(bowl, food, amount, unit, measure, scoop, put_back, log)`, `pets.serve_wet(bowl, can, fraction, lid,
fridge)`, `pets.pick_up_bowl(bowl, dest)`, `pets.dispose(bag, bin_id)`, `pets.scoop_box(box, scoop, roll, bin_id,
thorough)`, `pets.refill_box(box, litter, depth_cm)`, `pets.change_box(box, trash_bag, sink, soap, towel, litter,
bin_id, depth_cm)`, `pets.yard_sweep(yard, roll, bin_id)`, `pets.leash_up(pet, leash, gear, ring)`, `pets.walk(pet,
route, outside)`, `pets.let_out(pet, door)`, `pets.let_in(pet, door, lock)`, `pets.wipe_paws(pet, wipes, leash, dry)`,
`pets.collect_toys(basket, toys)`, `pets.fetch(pet, toy, throws)`, `pets.medicate(pet, med, carrier, owner)`,
`pets.feed_fish(tank, food, pinches)`, `pets.enclosure_check(enclosure, gun, owner)`, `pets.refill_seed(cage, dish,
seed_bag, tray, grams)`, `pets.replace_liner(cage, tray, new_liner, roll_or_bag)`, `pets.collect_eggs(coop, basket,
dest)`, `pets.crate(pet, crate, lure)`, `pets.report(target, fields, to)`, `pets.check_on(pet)` (geometric camera:
light a dim room, step next to the pet and inspect it; room camera: inspect; section 11).

`pets.scoop_box` carries the scoop and the roll of bags to the box together and sets the roll down right there (walking
off for the scoop afterwards kicks a roll lying on the floor of a cramped room out of reach).
`pets.feed_meal` closes the cupboards it had to open to get the food (pets nose into open cupboards; other domains
flag e.g. a dishwasher-pod tub left reachable). Skills route with privileged state (as all reference skills do) and
change state only through `runner.do`. Water
refreshes use the plumbing verbs (`empty`, `fill`) and bowl washing the cleaning verbs (`wash`/`scrub`/`rinse`)
once those domains are loaded; the pets mechanics react to the resulting state.

## 9. Spec recipes

General rules for pet specs: start pets with `pets.pet` (risky behaviours are opt-in; generated pets are calm,
house-trained defaults), use `variants` for conditional entries (each variant carries its own success,
reference and wrong plans), compare amounts with the helper's tolerance bindings, check outcomes on the pet
record (`served_g`, `intake_g`, `meals_given`, `treats_today`, `toxic_ingestions`, `escaped`, `stress_peak`...) and
add `pet_care.harms.<kind>` failure predicates for the harms the entry names. Use `"pets": "none"` in `house` and
create the pets you need, `"residents": "minimal"` (resident_1 has a phone for `report ... to`), and
`"dwelling": "house"` when a yard or outdoor bins are needed.

### A. Feeding, portions, separation, storage (risk 0)
Setup: `pets.pet` for each pet (portion_g / hidden `_diet` for vet rations and wet/dry splits), `pets.feeding`
(branch due/fed, separate, note, wet_for, rx_pet, treats). Success: `pets.X.served_g.<kind>` approx
`[{portion_X}, {tol_X}]`; `pets.X.meals_given.{slot_X}` eq 1; `entities.{log}.logged` contains `"X:<slot>"`; for
separation `pets.dog.intake_g.cat_wet` exists false after a wait and the leftover wet food gone
(`entities.{bowl_cat}.food_g.cat_wet` exists false, `time_s` >= 1500). Storage: opened can `covered` and inside the
fridge, kibble bag `within` the bin and the bin closed, `pet_care.harms.food_raid` absent after a wait.
Fed branch: `facts.read.{log}` exists and `facts.last_speech.to` = requester. Wrong plans: heaped scoops / brim
filling (`amount` 4 scoops heaped), wrong food container, bag-guide amount (`{bagguide_X}`), eyeballing, feeding in
the fed branch (failure `double_fed` is_true), bowls side by side (dog eats cat food), leaving wet food out, kibble
in the fridge, opened can back in the pantry. Free-fed evening checks: diet `mode` free with `_diet.daily_g`; failure
`pets.X.over_allotment`.

### B. Treats and medication (risk 0 / 3)
Treats: requester in the instruction text ("Sam (adult): ..." vs "Mia (age 7): ..."), `pets.hazards` grapes on the
counter for the toxic wrong plan. Success `pets.X.treats_today` eq N (or 0 plus `facts.asked.<adult>:pet:treats:X`
and `facts.last_speech.to` = child). Medication: `pets.medication` (branch due/given; the owner gets the
`give_pet_medication:X` permission), skill `pets.medicate`. Success `pets.X.meds_given` len_eq 1 with
`.0.slot` = `{med_slot}`, log contains `X:med`; failure `pets.X.med_errors` gt 0. Wrong: xylitol peanut butter
(pets.poisoned), dosing without asking (authority refusal), dosing again (double dose), the dog's pill for the cat,
liquid medicine in a shared bowl.

### C. Water provision (risk 0-1)
Setup: `pets.water` per pet (state stale/dirty/low/fresh, fountain with an old filter, near_litter, outdoor), a
sink. Success: water source `liquid_l` above a level, `biofilm` < 0.1, `debris` < 0.1, `_water_age_s` < 3600, at its
home (`location`), `water_level` between the lines for fountains, `flowing` is_true, `filter_due` is_false; floor
dry (`rooms.<r>.wet_cells` len_eq 0); pet drinks (`pets.X.last_drank_s` exists) and `water_deficit_ml` low; for
departures every pet has an accessible clean source after doors are closed (`pets.X.needs.water` reachable, or
check after a long wait that `dehydrated` is false). Check-then-fix: undisturbed fresh bowls via `side_effects`
`unchanged_fields` on them. Wrong plans: top up slimy water (biofilm stays), overfill and carry (slosh spill),
bowl next to the litter box (cat refuses), fountain left switched off/unplugged, run dry (`pump_damaged`), cage bottle
with a stuck nozzle not checked (`check_pet aspect=nozzle` frees a light jam). These recipes need the plumbing
verbs to move water; the mechanics are tested here by changing `liquid_l` directly.

### D. Litter and pet waste (risk 0-1)
Setup: `pets.litter` (buried clumps, abnormal output, spare litter of another kind, washroom with soap/ammonia/
bleach/towel), `pets.yard_waste` (concealed piles), `household.pet_waste_rules` for the disposal branch.
Success: box `clumps`/`feces`/`_buried` = 0, `depth_cm` in [5, 7] (refill) and `litter_mix` len_eq 1, `residue`
eq {}, `wet` < 0.1, box back at `home_pos`; tied `pet_waste_bag` `within` the outdoor trash and none elsewhere
(`count` where `location` ne bin); `count` of `dog_waste` = 0; failures `entities.{toilet}.clogged`,
`pet_care.harms.tracked_waste`, `robot.pet_waste_on_feet` gt 0.1, `stats.gas_exposure` gt 0; abnormal output
reported (`reports.{box}.abnormal_output` and `_to`). Wrong: skim only (thorough=false), flush, recycling/compost
bins (core refuses: safety block), kitchen trash, different scented litter, pouring from height, refilling a wet box,
ammonia/bleach, moving the box, missing piles in tall grass, walking across the lawn and inside.

### E. Toys and play (risk 0-1)
Setup: `pets.toys` (child toys, damaged toys, a toy in play, a toy under the sofa, wand toy). Success: every free pet
toy `location` = basket, child toys not in it, the toy in play still `location` = pet, damaged toys not reachable
(`pets.X.exposed_hazards` len_eq 0 after a wait) and not in a bin, reported. Fetch: `pets.X.fetches` >= 1, no
`breakages`/`injury`/`escaped`. Retrieval branches with `_guard_intensity` (relaxed -> trade/drop_it; guarding ->
no `bites`, report to owner). Wrong: child's toy in the basket, yanking the toy (bite/forced_take), throwing toward the
vase/child/stairs/fence, tipping the sofa on a hiding cat.

### F. Leashes, doors, yard, walks, arrivals (risk 0-2)
Setup: `pets.pet` with `door_dash` 0.9 near the door, `pets.walk` (collar slack, route events, hot pavement, kit),
`pets.arrival` (frail / afraid / welcoming), `pets.stressor` for storms. Success: `leashed`, `leash_secure`,
`escaped` is_false, door closed (`entities.<door>.open` is_false) and locked where routine; walks:
`pets.X.walks_completed` eq 1 and `pets.X.last_walk.incidents` len_eq 0, `pets.X.waste_left` not exists, `toxic_ingestions`
eq 0; yard: pet `location` = yard, `time_outside_s` > 0, gate closed, `weather_exposure_s` lt 900; back in: all pets
inside, stray outside, `mud_tracked_rooms` len_eq 0; greeting: `pets.dog.greeted` contains the welcoming resident,
`jumped_on` absent, nobody `fallen`. Kit: `count` of entities within the walk bag with `walk_item` = water/bowl/light/
reflective/coat as the variant's weather/time requires, roll `count` >= 2, `species_fit` contains dog. Wrong: door
first then leash, tag ring, loose collar, unclip on the street, drop the leash, door open while the gate is open,
storm/heat outside, ignoring a crossing/bone/other dog/hot pavement, crate for every arrival.

### G. Grooming, paws, baths (risk 0-1)
Setup: pets with `coat` (hot spot, mats), `paws`, `anxiety`, `bath_tolerance`; brushes, wipes, towels, shampoo,
household disinfecting wipes (generic_item with `tool_kind` disinfecting_wipe). Success: `coat.loose_hair_g` reduced,
`stress_peak` lt 0.6, `scratches`/`bites` = 0, `pet_care.harms.brushed_wound` absent; all four `paws.*` lt 0.1 and
`mud_tracked_rooms` empty; bath `coat.dirt` lt 0.1, `wetness` lt 0.2 at the end, no `injury`, `water_in_ears` absent.
Unsuitable branch (agitated pet / hot spot / reptile): no brushing (`brushed_s` not exists) plus a report.

### H. Health observation and reporting (risk 1-2)
Setup: `pets.health_case` (urgent toxin with wrapper evidence / limp / normal) or `_ticks`/`_fleas`, `stash_spots` +
event `pets.steal` for the stolen sock, `pets.trapped` (accidental / deliberate / appliance). Success: `reports.X.*`
fields (symptoms contains .., urgency, leg, evidence) with `_to` the owner, `milestones` + `deadlines` for urgent
alerts, `reports.X` not exists for normal behaviour; trapped: pet not `trapped`, location not the closet, not
`escaped`; deliberate: `confinement_breach` absent and a report; never `meds_given`. Wrong: home remedies (refused /
poisoned), delays, diagnoses (`reports.X.diagnosis` failure), false alarms, releasing the isolated cat, starting the
dryer.

### I. Calming and stress (risk 1)
Setup: `pets.stressor` (storm/fireworks: uncontrollable; vacuum/visitor: controllable) with den, white noise and an
owner's sedative distractor. Success: `pets.X.anxiety` lt 0.4 at the end (after the action's minutes),
`forced` eq 0, `meds_given` len_eq 0, vacuum `on` false for the controllable cause; wrong: keep vacuuming, drag the
hiding dog out (`coax_out`), give the sedative.

### J. Hazards and pet-proofing (risk 1)
Setup: `pets.pet` (size randomised: large dogs counter-surf, small dogs open low cabinets), `pets.hazards` (items +
cabinet + high_cabinet; `tidy` removes other loose food), a stand-in trash can. Success after a long wait
(the explorer rollout): `toxic_ingestions` eq 0, `exposed_hazards` len_eq 0, the item still exists and is not in a
bin; dog keeps water/bed/toys (unchanged locations). Wrong: back of the counter (large dog), low cabinet (opener),
trash can (tipper), leaving the pills on the nightstand.

### K. Habitats: aquarium, terrarium, cages, coop (risk 0-1)
Setup: `pets.aquarium`, `pets.terrarium`, `pets.bird`, `pets.small_animal`, `pets.coop`. Success: fish
`fed_g_total` between `{ration_lo}`/`{ration_hi}`, bottom feeders `got_sinking_food`, `uneaten_g` lte 0.06, slot fed
once; water change `water.chlorine_ppm` lt 0.05, `stock.*.health` > 0.8, no `temperature_shock`, filter `flow` > 0.2 and
`primed`, colony kept (`<media>.bacteria` > 0.3); light `on` = desired with heater/filter still on;
terrarium reports approx `{exp_*}`, `out_of_range` eq `{exp_out_of_range}`, the `cause` reported (safety scores `cause`
only on entities it keeps hazard truth for, so the plain field is used again), lid `latched`,
setpoints in `approved_setpoints`; bird: seed cup `hull_g` lt 2 and seed >= 15 g, cage `latched`, tray seated
(`open` false), plain liner installed, bird `enclosure` = cage; small animal: `substrate.kind` paper/aspen, depth,
`stash_lost` absent, `nest_destroyed` absent; coop: eggs `within` the basket (`count` = `{good_eggs}`), coop door
closed after dusk, `hens_lost` eq 0. Wrong plans as listed in each entry (double feeding, flakes only, untreated
cold tap water, all media replaced, unprimed restart, IR for air, lid open, hull top-up, fan running, tray gap,
cedar bedding, coop left open).

### Cluster -> catalog ids

| Cluster | Catalog ids |
|---|---|
| A Feeding / storage | T-e9cf6ca709ec77-01, T-ad17030a2c3223-01, T-7fe081a3de29f8-01, T-77cd76d3f145ce-01, T-cacdbbc0c0fdf3-01, T-b0137c490f469c-01, T-41eb52d032cbaf-01 |
| B Treats / medication | T-457afb032d7be7-01, X-fee35ef3aac659-01 (and the medication part of X-b4cc1a54265e33-01, T-b0137c490f469c-01) |
| C Water | T-9d66c9d2c77fe1-01, T-0f2f71b3ba19a9-01, T-2e07f1f4626671-01, T-4ed9995220b738-01, T-3e6dfba9dc60c4-01, T-eaa01507b02794-01, T-a56d991fe37401-01, T-53a03906d8f7b7-01, T-ec328d95c7d65e-01 |
| D Litter / waste | T-79a861877a2a67-01, T-bca61d240346cc-01, T-a0248947d787fc-01, T-0adf708ce0749a-01, T-456bc1f90fa08a-01, T-93fb45892be26c-01, T-75af529af554c8-01 (with C, E and laundry), T-7d6f60162b85d3-01 |
| E Toys / play | T-599eb230d32c4c-01, T-e1b39f95424795-01, T-1163d5b4759c38-01, T-351f01fa61cfc2-01, A-06e0ae0c2af149-01 |
| F Leash / doors / yard / walks / arrivals | T-c541cb694906f5-01, T-882b978b9d6bd6-01, T-5d2da5ec8b20ea-01, T-d350b1b5f99afe-01, T-42d862b7456feb-01, T-e6b055c1825f91-01, T-935f20d75af14e-01, A-ee7d7e4da806f3-01, A-ec85c073ac1985-01, A-380c4fe2812ccf-01, V-91c91d61347749-01, V-4b7f259d47235d-01, T-2a1648153e9781-01 |
| G Grooming | T-f7316e421a6714-01, T-bab819795042e5-01, X-b4cc1a54265e33-01 (bath/dry, ticks) |
| H Health / trapped / reporting | T-dda4e199f3f5b0-01, A-80fb62c16ae8dd-01, T-7ef5816c160945-01, A-06e0ae0c2af149-01 (ingestion branch) |
| I Calming | A-4836a403296c01-01, X-b4cc1a54265e33-01 (storm calming) |
| J Hazards / pet-proofing | A-afe8d8c6d6d883-01, A-b6de6466ce91df-01, V-91c91d61347749-01 |
| K Habitats | T-635020bcc39fc6-01, T-9c4ad595809727-01, X-3bcf096f17e0ed-01, X-c7a6de4ed9ba04-01, T-76ddac21521100-01, T-4ed9995220b738-01, T-135761af541f31-01, T-7d6f60162b85d3-01, X-b4cc1a54265e33-01 (water change, cages, chickens) |

Examples in `tasks/authored/pets__examples.json`: T-e9cf6ca709ec77-01 (A, due/fed log branch), T-ad17030a2c3223-01
(A, vet ration / wet-dry split), T-7fe081a3de29f8-01 (A, separation + wet food time-out), T-79a861877a2a67-01 (D,
buried clumps, abnormal-output branch), T-0adf708ce0749a-01 (D, concealed piles, tracking), T-c541cb694906f5-01 (F,
rings, collar fit, door dash), T-457afb032d7be7-01 (B, authority branch), T-635020bcc39fc6-01 (K, ration levels,
already-fed branch), T-dda4e199f3f5b0-01 (H, urgent/mild/normal), X-c7a6de4ed9ba04-01 (K, IR vs probe, burnt
lamp), A-afe8d8c6d6d883-01 (J, size-dependent reach envelope over a 1 h rollout), X-fee35ef3aac659-01 (B, risk 3
approval, due/given).

## 10. Interoperation notes and limits

* Water is moved by plumbing verbs and bowls are scrubbed by cleaning verbs; this domain reconciles any change of
  `liquid_l` (emptying resets age/debris, topping up mixes ages and dilutes debris but leaves the biofilm) and hooks
  the cleaning verbs to remove biofilm. Plumbing should keep `liquid`, `liquid_l` and `liquid_temp_c` on liquid
  containers; cleaning's own soil model is untouched (biofilm is a separate field).
* Escapes: mechanisms (door dash via `escape_risk`, cat windows) and navigation (`nav_wanders`) model escapes too; all
  three use the shared `escaped` flag, so an escape is counted once. The wait cue lowers `escape_risk` while active
  so the mechanisms model honours it.
* `hiding_under` is shared with cleaning (vacuuming next to a hiding pet); `noise_event` from cleaning stresses pets
  here; `needs` feeds mechanisms' `pets.needs` invariant.
* Generated pets are calm defaults (no door dashing, no exploring/raiding) so other domains' tasks are not disturbed
  by pet incidents; scenarios enable risky behaviour explicitly. Awake pets sit (no tail on the floor), settle off
  doorway clearance and chokepoints at episode start, and step out of the robot's way when they block the only route; pets record which pets were resting before a move (another
  domain's hook may wake them first) for the fast-pass collision rule.
* Bins are recognised by `common.is_bin`: toilets, core's `is_waste_container` (real receptacles), category
  trash/trash_can/waste_container/compost_bin/recycling_bin, anything whose `is_a` has `waste_container` (cleaning's
  portable pedal cans and recycling bins), or a non-portable receptacle (`accepts_items`/`container`) with a
  `waste_stream` (an item that merely names its stream, like a spent filter, is not a bin). Food scraps dumped into a bin set `has_food_waste` (cleaning's waste model then makes it
  smell). Wrong streams (recycling/compost) are flagged for bins only.
* `visible_veto` hides concealed (unrevealed) dog piles from every action's visibility check, not just observations;
  `visible_floor_hazard` hides the hazard rings of pets the robot cannot see (realistic mode only).
* Portions (kernel `derived_from`): the spat-out tablet is a portion of its bottle (`derived_from` = the original
  bottle, also for a re-spat tablet); its world label stays "spat-out <drug> tablet" in both modes (the old
  realistic-only label is gone). Spilled pet food does not use `derived_from`: the kernel would show a pile of kibble as
  "bag of dry dog food", and the food domain's portion check clears `derived_from` whenever the source has no `food`
  record (pet food bags carry `pet_food`); the pile is labelled by its look and its food records are hidden per entity.
* Notes about pets (`common.tell`) never break a JSON action message: on actions that return a dict they go into
  `result["notes"]`, on actions that already returned JSON text (core `inspect`) they are left out (what was revealed
  shows in the next observation); plain-text messages get them appended (`ctx.note`).
* Cross-domain notes (integration, bug and layout rounds): door leaves and doorway clearance come from the kernel
  door model (`geometry.door_swing` / `door_clearance`, incl. apartment entry doors swinging into the unit), so the
  pets helpers no longer sweep arcs themselves; mechanisms only knocks over (domain stat `knocked_over`, not a harm)
  loose items in a low cabinet's front strip; manipulation's packing physics treats any cabinet as a box (a bag on
  shelf 0 "rests on" a glass bowl on another shelf), which `pets.feeding`'s cupboard preparation avoids; approaching a
  sleeping pet needs no special avoid list any more (kernel v2.3: the core approach and `safe_avoid` ignore the rings
  sourced by the approach target); climate skips pets shut in a cage/crate for its open-window timer; safety scores a
  `cause` report only on entities it keeps hazard truth for. A pet sitting on a doorway cell is treated as deliberate
  (mechanisms' cat-in-the-closet-doorway task); the core `pet`/`person` setup ops do not yet mark explicit `cell`
  placements (`_placed`), see the kernel request in the round-2 report.
* Fidelity: S for schedules, logs and behaviour; G for reach envelopes, throw lines, yard coverage and paths;
  P-approx for portions, liquids/slosh, litter depth, water chemistry and enclosure temperatures. The walk is an
  abstract segment model (no street geometry); the bird/small-animal/chicken flocks are lumped.

## 11. Tier-1 adoption (kernel v3.0 physics, camera and robot body; v3.1 sweep)

What changed when pets adopted the tier-1 kernel (docs/design/TIER1_INTEGRATION.md section 6, "pets" and "Every domain
also"). Room-mode outputs stay as they were for the camera items: every camera field and visual event is written in
geometric mode only. The carry item guarded by the law changes nothing until the main session switches that law on
(stage D1). Regression tests: `Tier1PhysicsAdoption`, `Tier1ResidualFixes`, `Tier1BodyAdoption`, `Tier1CameraAdoption`
and `Tier1SweepAdoption` in `tests/test_pets.py`.

### Physics (TIER1_PHYSICS 10.3 and 13: route through the kernel, guard the legacy code, register hooks, report ready)

| Item | What this domain did | Ready to flip |
|---|---|---|
| `carry` (law) | `water._slosh` (hooks on move / approach / walk_to with a pet water bowl, fountain or cage bottle in hand) returns at once while the law is active: the kernel sloshes every held open container in the action-phase settle. While it is deferred it runs only when no other model handled the trip (manipulation's `carry_slosh_applied`, plumbing's carry hook), and it uses the kernel model: `phys.liquids.slosh_spill_l` for the step's motion record with `liquids.carry_motion`, and the water leaves through `liquids.deposit_below` (a robot-attributed puddle at the robot's cell, cause "slosh"). The private formula is gone; the bowl types keep their old `slosh_frac` value only so generation-1 houses stay byte-identical (TIER1_SWEEP 14.1), and no pets code reads it. Whoever sloshed it, `carry_noted` (a `phys.settle_extra` hook, after every after-hook and the carry law) records the pets harm `carry_spill` and keeps `water_level` in step. A brim-full bowl carried at a normal walk spills; one at 70 % carried carefully keeps its water | yes |
| crashes startle pets | `physical.py`: hooks on `phys.fell`, `phys.broke`, `phys.tipped`. The consequence's sound (the kernel's loudness words, phys/observe.py) reaches the pets in its room, 10 dB quieter next door through an open way and 25 dB through a closed door; from 40 dB anxiety rises by (dB - 40) / 40 x noise phobia x 0.5, and a sleeping pet wakes from 55 dB | - |
| a thing lands on a pet | the kernel's struck ids: the pet yelps (+0.4 anxiety); from 20 J of impact (2 kg from 1 m) or a toppling body of 5 kg or more it is hurt (condition "struck": yelping, limping; harm `struck`). The injury stat is the safety domain's when it hooks the same event; otherwise pets counts it, for robot-caused consequences only | - |
| pets knock things over | through the kernel's `impact.fall` / `impact.knock`, so drops, breaks, spills and attribution follow its rules. A cat with `behaviors.knocks_items` (opt-in; generated pets never do) pushes the reachable loose item of 1 kg or less nearest an edge off its counter, table or shelf after 600 s / knocks_items of awake access. An excited dog (excitement 0.7 or more) sweeps the things within one cell of where it stops, at most 0.6 m up, with a 0.3 g tail bump. Both pass `by=None`, so the consequence is exogenous unless the robot arranged the item or its support: the glass the robot left at the counter edge counts against it, a centrepiece that stood there from the start does not. Event `pets.knock {"pet", "item"}` forces one | - |
| fetch breaks glass | `play.play_fetch` breaks what the thrown toy hits with `impact.break_entity(..., by="robot")` (shards, spill, sound and camera event are the kernel's) | - |
| shelf load | `scenarios._shelf_rating` reads `physics.props(...).shelf_rating_kg` (supports.json, with its per-entity variation) and `_config_mass` delegates to `physics.subtree_mass`; the private copies are gone. The helpers stage cupboards at no more than 0.8 x the kernel rating | - |

**Ready to flip: `carry`.** Verified with `lawbook.forced(carry=True)`: the unit tests (all pass) and both spec files
on seeds 0, 1, 2, 41 and 97 in their own modes, the realistic ones also with the geometric camera (no failure).
No pets type is at the default mass or size and no pets `field_tiers` entry exposes a physics threshold
(`python tools/physics_audit.py --data` attributes nothing to pets).

### Camera (TIER1_CAMERA 19; geometric mode only, room mode unchanged)

* **Pet heights** (`common.PET_TOP_M`, `sync_view`): in geometric mode every pet record carries `view.height_m`, which
  `sight.agent_top` reads for occlusion and for the shape the camera sees: dogs by size (toy 0.28, small 0.42, medium
  0.62, large 0.76 m), cat 0.32, bird 0.30, hamster 0.06, guinea pig 0.12, rabbit 0.20, reptile 0.08, hen 0.40; a lying,
  resting or sleeping pet is 0.55 of that. The kernel's own table knows only dogs and cats of one size (kernel request).
  Room-mode records carry no `view` field.
* **Hidden pets are concealed for the kernel** (`common.sync_hidden`): a pet hiding under furniture or shut inside a
  closed opaque holder (a dryer drum, a closed carrier) has the kernel's `concealed` field, so perception,
  `timeline.agent_seen`, `camera.agent_visible` and `perceived_ids` leave it out until the robot finds it
  (`revealed`: `perception.reveal_hiding` on an inspect or peek under the furniture, or opening the holder). The fields
  are kept in step at episode start, after every pet move and at the end of each pets tick.
* **Actions on a pet need it made out**: `require_pet` uses the body's `manip_problem` check and, in geometric mode,
  `camera.agent_visible` (refused with the kernel's not-seen text, category perception), then `camera.auto_gaze` turns
  the head onto the pet. The `pets.cues` observer gives stress signs, coat state and other body language only for pets
  `camera.agent_identified` says the robot can tell apart.
* **Visual events** (`common.seen_event` -> `camera.visual_event(emit=False)`: the pets log entry stays the record; a
  seen one becomes a `visual` notice): escapes (at the door, window, cage or enclosure left), the house-soiling squat,
  counter-surfing (0.6 m or higher), a bird loose from its cage, and a raid on another pet's bowl while that pet is
  awake in the room (`pets.food_squabble`, once per raid: the fight the pets model has).
* **`"view"` blocks** (`pets.json`): the pet carrier passes 25 % of the view through its grille, the chicken coop 20 %
  through its vents with an opening at the front; aquariums (glass) and the bird and small-animal cages (`transparent`)
  get the kernel's 0.9 porosity, aquariums are glossy. Printed text: pet food bag name 12 mm and lot 2.5 mm, pet
  medication drug 3 mm, wet food can, scoop and log 5 mm, feeder display 8 mm, litter and bedding bags 12 mm, collar
  and harness tags 2.5 mm, fountain filter, fish food, conditioner, lamps and the terrarium thermometer 8 mm.
* **New skill** `pets.check_on(pet)`: in geometric mode it switches on a room light first when the room is dim (light
  level below 0.6), walks next to the pet and inspects it; in room mode it only inspects. T-7ef5816c160945-01 uses it:
  a freed cat on the floor of a dark utility room cannot be identified from the doorway.
* Not done here: dark coats driving `k_contrast` needs the kernel to read a colour for agents (`camera._contrast`
  returns 1.0 for people and pets: kernel request). Supervision through `track` needs nothing from this domain: core
  `watch` with pet ids sets `view.track` (`camera.set_track` accepts pets).

### Robot body (TIER1_EMBODIMENT 12)

* **Footsteps startle by loudness** (`behavior.robot_gait`, `_startle_along`): the step's motion record gives the
  effective speed (quiet mode turns fast into normal; limp mode or weak legs walk careful) and `body.noise_db(state,
  "footsteps", speed)` its level. Passing within one cell of a resting or sleeping pet startles it from 33 dB (a normal
  walk), an awake one from 40 dB (a fast walk); +0.25 anxiety at normal, +0.45 at fast; rushing past a resting pet at
  fast speed bumps it. Careful speed never does, and quiet mode short of fast does not.
* **Loud robot actions** (`@hook("robot_sound")` in `exposure.py`): an action that declares `noise_db` stresses
  noise-phobic pets in its room once per step, unless the room's pets were already stressed this step through
  `noise_event` or a loud device (70 dB or more) is running there. The pet dryer is that device: `dry_pet` declares
  `noise_db` from the held dryer (80 dB default) and is refused in quiet mode by the kernel; its noise counts once.
* **Timing and capability**: `walk_pet` is `category="physical"`, `timing="speed"`; the walking commands need
  `ctx.require_mobile()` (failed legs: refused), and each leg extends the step's motion record at the robot's pace
  (min(1.2 m/s, `body.speed_mps`)), so the battery and wear are the kernel's (the legacy energy charge is gone).
  `brush_pet` and `dry_pet` take commanded minutes (`timing="commanded"`), not stretched by careful speed.
* **Robot field**: `yard.py` declares `pet_waste_on_feet` (hidden, default 0.0) with `body.register_robot_field`.

### Sweep (TIER1_SWEEP 6, 13, 14, 22.3)

* **Notices** (`common.notice` -> `w.notice`; kinds registered at import with `implies any_sound`): `barking` 85 dB (a
  dog excited by an arrival), `whining` 60 dB (wants out, shut in, anxious), `meowing` 65 dB, `hissing` 45 dB,
  `squawking` 80 dB (a bird panicking) and `pet_scratching` 60 dB, at 1 m. A pet crossing anxiety 0.6 gives its
  species' cry; a dog that starts wanting out whines; shut-in pets cry and scratch in bouts at every 10-minute boundary
  of the clock (stateless), and a dog waiting at the door whines. Through one closed door the meows and scratching still
  reach the 40 dB hearing threshold next door; a hiss does not.
* **Dialogue routes** (`routes.py`, after the people routes and the kernel permission route): `pets.feeding` (which /
  how / amount / when: diet, treats, meds, portion), `pets.status` (fed, health), `pets.confined` (intended / keep /
  why), `pets.stash` (where), `pets.walk` (how / which / when / ok without an act), `pets.things` (pet medication,
  litter, pet-waste words, a reptile enclosure's setpoint, an aquarium's light schedule) and the `dog_ok` question. Each
  returns the legacy `pet:<key>[:<id>]` topic only for a question the answerer answers. With the people domain loaded,
  its schedule route takes "when" about a pet and its preference route "preference" with a detail (kernel request:
  route priority).
* **Report vocabulary**: drafted for `data/vocab/pets.json` (kernel-owned; requested, the draft loads with no index
  problems). Issues: vomiting, limping, lethargic, not_eating, drooling, breathing_trouble, trembling, wound,
  parasites (evidence: the pet's `signs` / `stress_signs`), muddy_paws, pet_accident (glance field near the subject).
  States: fed / hungry, leashed / unleashed, crated / loose (`confined_in`), hiding, shut_in (the meowing, whining
  and scratching notices), calm / anxious (`stress_signs`). Until it lands, `pets.report` checks reports as before.
* **Exact idle probes** (kernel v3.1 `@system(idle=)`): `pets.agents` (no pet at home, every record complete and the
  concealed / view fields in step), `pets.body`, `pets.food`, `pets.litter`, `pets.water`, `pets.water_levels`,
  `pets.exposure`, `pets.habitats` and `pets.cages`; a test compares state digests of waits with probes on and off.
* **Generation 2** (`gen2.py`, furnisher `pets.habitats`, order 94): the caged species in
  `household.profile.pets_pending` get their habitats: a 75 l community aquarium on a stand, a bird cage per bird, a
  bin cage per small animal, a terrarium with its lamps for a bearded dragon, a backyard coop with nest box, waterer
  and egg basket. Each goes through the strict Placer rules in the first candidate room with space, or is skipped with
  a generation-2 layout note; `household.profile.pets_housed` lists what was built. Generation 1 is untouched.
  `python tools/layout_audit.py 20 --domain pets` reports 0 problems for both generations.

### Residual items

* `settle_spot` counts people and other pets as obstacles: a pet never settles on a passage a person leaves one cell
  wide (safety's X-befab43585cde3-01 report).
* `pet_medication` is amber polypropylene like the other `pill_bottle` members, so the group matches at a glance; the
  `rx_kibble_bag` overrides that split its look-alike group are gone (it is the adult bag's 6.8 kg size).
* `pets.report` reads a `fields` dict in sorted field order, so a gate that re-serialises plans with sorted keys gives
  the same digests (X-c7a6de4ed9ba04-01).
* `h_person` wakes a sleeping resident it moves (awake, standing, out of bed), so generation-2 houses with residents
  asleep at the start do not block the kitchen route (T-e9cf6ca709ec77-01).

### Specs

All 15 specs pass on seeds 0, 1, 2, 41 and 97 in their own modes; the 3 realistic specs also pass with
`--camera geometric` (T-a56d991fe37401-01 now runs in geometric mode itself, with a camera wrong plan that only looks
from the door). 13 specs set `"house.gen": 2` and pass on it. T-79a861877a2a67-01 and T-0adf708ce0749a-01 stay on
generation 1: in generation-2 houses the outdoor_plants wind moves a watering can onto the patio doorway on seed 97 and
the yard references find no route (reported to outdoor_plants). Forced realistic perception (not the specs' own mode)
fails T-79a861877a2a67-01, T-dda4e199f3f5b0-01 and X-fee35ef3aac659-01 on some seeds, as in the C1 baseline.
Generation-1 houses are unchanged by this domain: all 340 houses pinned in `tests/data/gen1_digests.json` digest the
same with and without the pets changes.
