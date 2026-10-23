# cleaning: soil, surfaces, floors, dishes, waste streams, cleaning chemistry

Owner of: soil on every surface and floor cell (what is on it, where, how stuck), cleaning tools (cloths, sponges,
pads, brushes, dusters, brooms, dustpans, mops, buckets, squeegees, scrapers, vacuums and their parts), cleaning
products and their chemistry (dwell, contact time, dilution, incompatible mixes, finish damage), spills and wet
floors, drying (water spots, rust, mildew, water damage), hand dish-washing and soaking, waste containers, liners and
local waste-stream rules (trash / recycling / compost / yard waste / hazardous), and the harms that come with cleaning
(toxic gas, slips, cross-contamination, scratched or etched finishes, liquid in electronics, noise at night).

Code: `house_sim/systems/cleaning/` (package: `model`, `state`, `engine`, `surfaces`, `floors`, `dishes`, `waste`,
`systems`, `perception`, `helpers`, `skills`, `furnish`, `routes`, `history`). Types:
`house_sim/data/types/cleaning.json` (192 types, 24 perception blocks, 14 look-alike groups, file-level `field_tiers`).
Tests: `tests/test_cleaning.py` (209 tests, 26 of them realistic perception, 15 for the tier-1 adoption and 16 for the
2026-10-22 review, section 16; pass with every domain loaded, with `HOUSE_SIM_STRICT=1` and with
`HOUSE_SIM_DOMAINS=core,cleaning`). Examples:
`tasks/authored/cleaning__examples.json` (13 specs) and `tasks/authored/cleaning__perception.json` (3 realistic-mode
specs with the geometric camera, sections 13 and 15); all 16 run on generation-2 houses (`"house": {"gen": 2}`) and
pass `--check` with every domain loaded on seeds 0, 1, 2, 41, 97. Layout: `python tools/layout_audit.py 20 --domain
cleaning` reports 0 problems on 120 houses (220 with `--gen 2`). Perception: `python tools/perception_audit.py 20 80
--verbose` reports no line attributed to cleaning (sections 13-15).

Fidelity: P-approx for the lumped physics (soil removal per pass = tool mechanics x wetness x chemistry x dwell;
liquid films and evaporation; spill spread and wicking; dust fallout; heat transfer for thermal shock), G for
everything tied to the grid (which floor cells a tool head can reach, under-furniture clearance, rugs, edges and
corners, doorways), S for the bookkeeping (waste-stream rules, zones, product logs).

Dev loop: `HOUSE_SIM_DOMAINS=cleaning python -m unittest tests/test_cleaning.py`, the same with `HOUSE_SIM_STRICT=1`,
and `HOUSE_SIM_DOMAINS=cleaning python -m house_sim --check tasks/authored/cleaning__examples.json --workers 2`.
Integration (all domains): the same commands without `HOUSE_SIM_DOMAINS`, plus
`python tools/bench_systems.py 40 0 1 2 3 --domain cleaning` (budget <= 0.8 ms per 30 s substep for the domain; measured
~0.09 ms, whole engine ~5 ms with every domain loaded).

---------------------------------------------------------------------------------------------------------------------

## 0. The soil model (read this first)

* **Kinds** (`model.SOILS`), each with a removal profile (mechanical need, needs water, which chemistry dissolves it,
  dwell time, what it smears into):
  * loose: `dust crumbs hair pet_hair lint sand dirt leaves litter ash pollen cobweb sawdust drywall_dust glitter cat_litter salt`
  * film: `fingerprints streaks grease hairspray sunscreen scuff crayon`
  * adhered: `food protein burnt sticky mud toothpaste bird_droppings sap wax glue paint_acrylic paint_oil paint_dried gum`
  * mineral: `soap_scum limescale water_spots rust`
  * bio: `mildew mold algae urine biohazard garbage_juice`
  * stains: `stain_wine stain_coffee stain_blood stain_oil stain_ink stain_grass stain_food stain_juice`
* Levels are 0..1 per kind. **Clean means below 0.05** (`CLEAN`). Write "clean" predicates as
  `{"not": {"path": "entities.X.soil.crumbs", "op": "gte", "value": 0.05}}` - a missing key then counts as clean,
  while a plain `lt` fails on a missing key.
* **Where soil lives** (all kept in sync by the domain; set them in setup, the domain reconciles on first contact):
  * `soil` - the true aggregate (max over all parts). Use it in predicates.
  * `soil_faces` `{face: {kind: level}}` for entities with `faces` (plate top/bottom, toilet bowl/under_rim/seat_top,
    stove cooktop/igniters, door `side:<room>`). Predicate a face as `entities.X.soil_faces.bowl.limescale`.
  * `_shadows` `{item_id: {kind: level}}`: soil under items standing on a surface. Wiping around an item leaves its
    shadow; lifting the item exposes it (the observer then shows it).
  * Floors: `_cells` `{"c,r": {kind: level}}` per 0.5 m cell plus `_cell_counts` `{kind: dirty cell count}`; the
    floor's `soil` is the max over cells. Cells under rugs, low furniture (`clearance_m` < 0.35) and blocked
    footprints are part of `soil` but not visible.
* `contamination` `{bacteria, raw_meat, biohazard, chemical}` (0..1) and per-face `cont_faces`; `_chem_residue`
  `{class: level}` (cleaner left on a food-contact surface); `wetness` 0..1; `water_film` mm per floor cell in the
  room record (`rooms.<id>.water_film`, kept in sync with the shared `wet_cells` list: see section 11).
* Observations show only **visible** soil (shadows, under-rug and under-furniture cells hidden); `inspect` shows the
  faces; crouch + `inspect target where=under` reveals floor cells under furniture.

## 1. Object types (selected fields; realistic magnitudes)

| Group | Types | Key fields |
|---|---|---|
| Cloths, wipes | `microfiber_cloth` (capture 0.92, absorbs 0.15 l, lint-free), `cotton_rag`, `chamois`, `paper_towel_roll` (`sheets` 80; 0.05 l per sheet), `disinfecting_wipes` (`count`, quat, 4 min contact) | `wetness`, `soil_load`, `wear` (>0.7 weakens), `cl_chem` {product: ml} loaded on the tool, `cl_used_zones`, `cl_zone`, `color`, `protected` (good towels) |
| Sponges, pads | `sponge` (scrub side abrasion 0.55), `non_scratch_sponge`, `scouring_pad` (0.7), `steel_wool_pad` (0.9, metal: arcs in microwaves), `melamine_sponge` (micro-abrasive), `pumice_stone` | as above; `side` soft/scrub at use time |
| Brushes, scrapers | `scrub_brush`, `grout_brush`, `toilet_brush` (toilet zone only), `bottle_brush`, `dish_brush`, `detail_brush`, `soft_brush`, `deck_brush`, `wire_grill_brush` (sheds bristles when worn), `nylon_grill_brush`, `plastic_scraper`, `razor_scraper` (low angle on glass cooktops), `litter_picker`, `rug_beater` | |
| Dusters | `feather_duster` (capture 0.35, flicks dust down), `microfiber_duster`, `extendable_duster` (+1.6 m reach), `flexible_duster` (vents, blinds), `compressed_air_can` (`hold_fans`, never inverted) | |
| Floor tools | long-handled tools (`broom` family, mops, `steam_mop`, `deck_brush`, `extendable_duster`) are stored and carried `orientation: "upright"` (a vertical handle does not swing through turns: navigation's long-load rule); `broom` (head 0.3 m, edge factor 0.6), `angled_broom` (corners), `push_broom` (outdoor), `hand_brush`, `dustpan`, `long_handled_dustpan` (no crouch), `dust_mop`, `flat_mop` (5 cm head: under furniture), `string_mop`, `steam_mop` (+ `steam_mop_pad`), `mop_bucket` (12 l; `liquid_l`, `cl_chem`, `cl_soil_g` turbidity), `squeegee`, `wet_floor_sign`; `cleaning_tool_holder` (wall-mounted spring-grip rail at 1.2 m, 5 grips, category `hooks`: takes no floor; furnished brooms, mops, dusters, a dustpan or a stick vacuum hang on it, `relation: "hung"`, and are fetched like anything else) | |
| Vacuums | `upright_vacuum` (13 cm head, bin), `stick_vacuum`, `canister_vacuum` (bag), `handheld_vacuum`, `shop_vacuum` (fine-dust rated with cartridge) | slots `bin`/`bag`, `filter`, `tool`, `wand`; `settings` {mode carpet/hard_floor, suction low/medium/high}; `brushroll_wrap`, `jammed`, `overheated`, `battery_level`, `bag_model` |
| Vacuum parts | `vacuum_bin` (250 g), `vacuum_bag` (`model`, seal), `vacuum_filter_foam` (washable), `vacuum_filter_hepa` (paper: never wet), `vacuum_filter_hepa_washable`, `shop_vac_cartridge_filter`, `shop_vac_foam_filter`, attachments `crevice_tool`, `dust_brush_attachment`, `upholstery_tool`, `pet_hair_tool`, `low_profile_head`, `extension_wand` | `cl_load` {kind: g}, `cl_load_g`, `fill_level`; filter `clog`, `washable`, `wetness` |
| Products (40) | `all_purpose_cleaner`, `dish_soap`, `degreaser`, `glass_cleaner` (ammonia), `glass_cleaner_ammonia_free`, `screen_cleaner`, `isopropyl_alcohol` (70%, kills in 30 s), `disinfectant_spray` (quat, 10 min), `bleach` (6%: 20 ml/l, 5 min), `hydrogen_peroxide`, `oxygen_bleach`, `cleaning_vinegar`, `citric_descaler`, `toilet_bowl_cleaner` (acid), `cream_cleanser`, `cleaning_baking_soda`, `oven_cleaner` (lye), `enzymatic_cleaner`, `wood_polish`, `leather_conditioner`, `stone_cleaner`, `wood_floor_cleaner`, `floor_cleaner`, `upholstery_cleaner`, `upholstery_solvent`, `mineral_spirits`, `stainless_polish`, `metal_polish`, `mildew_remover`, `grout_cleaner`, `deck_cleaner`, `patio_cleaner`, `grill_cleaner`, `ammonia_cleaner`, `bird_safe_cleaner`, `oil_absorbent`, `distilled_water`, `spray_bottle` (decant/dilute) | `product`, `label_text` (read it: dilution, contact time, never-mix, surfaces to avoid), `amount_ml`, `chem_hazards` (corrosive, toxic, flammable), tags `chem:<class>`; hidden `_known_contents` for unlabeled bottles. One container per look (section 13): the bottle 0.1 x 0.06 x 0.26 m, 750 ml, 0.85 kg full; the half-gallon jug (bleach, vinegar, distilled water, ammonia, deck and patio cleaner) 0.12 x 0.08 x 0.3 m, 1.9 l, 2 kg; dish soap its own 500 ml squeeze bottle. A product's `mass_kg` and hidden `fill_level` (amount over the full amount) follow its `amount_ml` (`S.set_product_ml`); every spray (and the refill bottle, 0.1 kg empty) carries a trigger head (`trigger`), so it is closed unless squeezed |
| Protective gear | `rubber_gloves`, `disposable_gloves`, `dust_mask`, `safety_goggles`, `cleaning_caddy` | `gear_kind`; wearing gloves allows biohazard handling |
| Waste containers | `wastebasket`, `bathroom_wastebasket` (private contents), `kitchen_trash_can` (50 l pedal can), `office_paper_bin`, `recycling_bin`, `compost_caddy` (+ `charcoal_filter`), `trash_cart`, `recycling_cart`, `yard_waste_cart`, `organics_cart`, `backyard_compost_bin` | `waste_stream`, `stream_label`, `color`, `nominal_capacity_l`, `fill_level`, `lid_blocked`, `stream_errors` [{item, issue, belongs}], `contaminated`, `odor` (dict, kind `garbage`), `fruit_flies`, slot `liner`, `lockable` (raccoon latch). A waste container is an entity with `waste_stream` AND (`accepts_any_waste`, `waste_bin`, `wheeled_cart`, is_a `waste_container`/`waste_cart`/`compost_bin`, or a bin category): other domains put `waste_stream` on ITEMS (the stream an egg or a battery belongs to), which never makes them bins (`state.is_waste_container`) |
| Liners | `trash_liner_4gal` / `_8gal` / `_13gal`, `contractor_bag` (heavy), `cart_liner`, `compostable_liner`, `liner_box` | `liner_l`, `max_load_kg` (what the bag bears before tearing; CONVENTIONS 5, formerly `load_rating_kg`, still read), `opened`, `seated`, `rim_folded`, `slipped`, `torn`, `leaking`, `double_bagged` |
| Waste items (50) | `waste_item` base; `food_wrapper chip_bag used_tissue paper_napkin soda_can plastic_bottle milk_jug yogurt_cup detergent_bottle takeout_container black_plastic_tray glass_jar glass_bottle broken_drinking_glass pizza_box cardboard_box paper_scrap newspaper thermal_receipt wrapping_paper foil_wrapping_paper plastic_bag bubble_wrap styrofoam_block aluminum_foil food_carton aerosol_can trigger_sprayer toothpaste_tube toilet_paper_core pla_cutlery dead_battery cigarette_butt bottle_cap wooden_skewer paper_plate paper_cup diaper food_scraps coffee_grounds eggshells fruit_peel meat_scraps chicken_bones fish_scraps cheese_rind corn_cob tea_bag yard_debris`; look-alikes `gift_bag twist_tie broken_pen` | `waste_kind`, `material`, `resin_code`, `form` (film, rigid...), `residue` 0..1, `has_cap`, `cap_loose`, `has_sticker`, `greasy`, `flattened`, `liquid_l`; hidden `_disposition` (keep/trash/...), `_reason`, `_ambiguous`; judged: `in_stream`, `stream_ok` |
| Scene entities | `spill` (`liquid`, `liquid_l`, `area_m2`, `residue`, `on_carpet`, `soaked_l`, `spread_factor`, `floor_hazard` wet/slip), `debris_pile` (`composition` {kind: g}, pushed small items inside), `baseboard`, `room_ceiling` (corners, cobwebs), `stair_run` (treads/risers), `dish_drying_rack` | |

Props-based mechanics (work on any domain's entities, set them in setup or in your own types):
`faces`, `face_materials` {face: material}, `liquid_sensitive_faces` (igniters, keypads, burner ports, vents: liquid
there = `liquid_ingress`), `spray_exposed_faces`, `drip_to` {face: lower_face}, `hazard_faces`, `water_safe` false,
`electronic`, `motor_base`, `vents`, `fans`, `input_device` + `host`, `locked_screen`/`input_locked`,
`unplug_before_cleaning` / `pinch_hazard_when_powered` / `exposed_element` + `plug_required` / `plugged_in` / `on`,
`rotating` + `on` / `spinning_until` (fans), `bulb_type` (halogen 250 C, incandescent 150 C, cfl 70 C, led 55 C),
`airflow_on` / `airflow_from` (a running HVAC fan behind a grille), `temperature_c`, `max_touch_temp_c`,
`thermal_shock_c`, `care_code` W / S / W-S / X (fabric cleaning code), `delicate`, `nap`, `seasoned`, `stainless`,
`clearance_m`, `ground_layer` (rugs), `fringe`, `washable`, `filter_kind`, `vacuum_insulated`, `paint_curing_until`,
`paint_wet_until`, `protected` / `good_towel`, `private_contents`, `toilet` / `water_source` / `drain` / `sink_zone`
kitchen/bathroom/utility/toilet/outdoor, `storm_drain` / `water_body` / `floor_drain` / `floor_vent` (special floor
cells), `hidden_in_water`, `odor`.

Materials (`model.MATERIALS`, 120 finishes) carry abrasion tolerance, water tolerance, the chemistries that damage them
(`bad` {class: (damage, severity)}), `glossy`, `grain`, `porous`, `steam` ok, `thermal_shock_c`, `acid_limit_s`,
`slippery_wet`, `rusts`. Examples: `marble` / `travertine` / `limestone` / `cultured_marble` etch with acid; `granite`
dulls with repeated acid; `sealed_wood` takes water only briefly (`hardwood` floors: water damage from standing water);
`laminate` and `mdf` swell; `unsealed_wood` and `oiled_wood` stain with water; `nonstick` scratches above abrasion 0.3;
`cast_iron` loses seasoning to lye and long soaks and rusts when left wet; `screen_glass` / `screen_matte` /
`screen_oled` lose coatings to ammonia/solvents and scratch with paper towels; `tinted_glass` (car) loses tint to
ammonia; `silver_plate` tarnishes with chlorine; `gold_rim_china` loses gilding to abrasives; `door_gasket` degrades
with lye/chlorine; `eps_foam` (helmets) melts with solvents; `leather`/`suede` with water and polish.

## 2. Actions

All refusals roll back; categories in brackets. The domain never refuses for safety reasons: unsafe choices are
consequences (stats), so a plan can make the mistake and the spec can fail on it. No cleaning action is undoable
(physical changes); `place`, `install`, `uninstall`, `set` are core and undoable. Durations scale with area and faces.

| Verb | Params | Preconditions (refusal) | Effects | Duration / noise |
|---|---|---|---|---|
| `apply_cleaner` | target, tool (held bottle), amount_ml?, face?, method spray/squeeze/pour/paste/fill/wrap, cells? | bottle not empty [resource]; target visible [perception]; face reachable (open covers, crouch for low faces, mounted items) [precondition] | onto a face: product dwells (`cl_applied` with `wet_until`, strength); onto a held tool: loads `cl_chem`; into water (bucket, basin, spray bottle, kettle): doses `cl_chem`/`cl_strength`. Checks: incompatible classes already on the face/in the water -> **gas event** (`gas_exposure`, `cleaning.toxic_gas` invariant); finish damage (`bad` chemistry); spray onto liquid-sensitive faces/electronics -> `liquid_ingress`; overspray onto items around; spraying into open water contaminates it; every product is logged in `cleaning.products_used` / `products_on` | 3-8 s, 0.02 |
| `wipe` | target, tool, face?, cells?, into?, direction inward/outward/with_grain/against_grain/circular/s_pattern/top_down/bottom_up, force light/normal/hard, around? | hold a cloth/sponge/paper/wipe [capability]; reach (stand on the floor, extendable tools for high faces) [capability/precondition] | removal per pass from tool mech x force x wetness x chemistry x dwell; crumbs into `into` (dustpan, bin, sink) else to the floor ring around the furniture; wet tool on dry grit makes mud; dirty tool redeposits; zone transfer (toilet->kitchen) adds contamination + `zone_violations`; drips onto `drip_to` faces; items left standing keep their shadows (or topple if knocked: `around` avoids it); live machines -> `safety_violations`; keyboards -> `accidental_inputs`; wet cloth on electronics -> ingress; glossy finishes show lint/streaks; wood finishes streak against the grain | 4 + area s, 0.02 |
| `scrub` | as wipe + side soft/scrub | as wipe; brush, pad, steel wool allowed | stronger mech; `check_abrasion`: tool abrasion above the material tolerance -> `scratched` (nonstick, acrylic, glass cooktop, stainless against the grain, enamel...); seasoned cast iron stripped by pads; worn wire brush sheds bristles (`hazards` wire_bristles) | 6 + area s, 0.08 |
| `degrease` | target, tool, face?, cells?, direction?, force?, side? | a degreasing chemistry on the face, tool or soak water [precondition] | grease/burnt removal with alkali/surfactant; cold polymerized grease needs dwell | 8 + area s, 0.05 |
| `dust` | target, tool (duster/cloth/brush/air), face?, around?, hold_fans?, inverted? | duster-type tool [capability]; reach (extendable duster for ceilings, fans, door tops) | captured dust loads the tool; the rest falls on furniture below / around the piece on the floor; damp cloth on heavy dust streaks; hot bulb -> `burns` (damp cloth on halogen shatters it); spinning fan -> `blade_bent`, dust flung over the room; running HVAC (`airflow_on`) keeps redepositing; compressed air: inverted -> `propellant_frost`, fans not held -> `fan_overspun`; live machines -> `safety_violations` | 3 + area s, 0.02 |
| `polish` | target, tool (cloth with polish), face?, direction? | polish loaded [precondition] | finished wood/stainless/metal: `polished`, fingerprints and spots gone; over dust -> `scratched`; on fabric/leather -> stained; on floors/handrails/steps -> `slippery` | 4 + area s |
| `condition` | target, tool (cloth with conditioner/oil), face?, amount light/normal/heavy | conditioner loaded [precondition] | leather `conditioned`; dry cast iron `seasoned`; suede/nubuck -> `dark_stain`; over dirt -> `sealed_dirt`; heavy -> sticky | 8 + area s |
| `squeegee` | target, tool, face?, direction top_down/bottom_up/horizontal, toward? floor | squeegee [capability] | wet glass top-down: dry, no streaks; other directions leave streaks/spots; dry or gritty glass: streaks / `scratched`; `toward` pushes the water onto a floor (film) | 6 + area s |
| `rinse` | target, destination? (sink/tub/toilet), tool?, face?, cells?, temp cold/warm/hot, seconds?, method running/dip | running water [precondition/resource]; rinse cloth damp with clean water and not holding cleaner [precondition] | held item under a tap: residue and suds off (dip in soapy basin water: still soapy); fixed surface with a damp cloth: residue off; toilet: flush rinse; outdoor: hose (cushions left on get soaked); the robot's own grippers (`robot_hand`) at a sink; toilet gear in a kitchen/bathroom basin -> sink contamination + `zone_violations`; non-washable filters break; electronics ingress; thermal shock through the kernel (`physics.shock_check`: the material's rated difference, water at cold 12 / warm 38 / hot 55 C); a hot thin metal pan more than 150 K from the water warps (the kernel rates no shock for metals); soap left on food items -> contamination | `timing="commanded"`: the `seconds` asked for (default a standard rinse by item), 0.05 |
| `dry` | target, tool (dry towel/cloth), face?, cells?, method towel/buff/heat | | wetness 0 (no spots, no rust); saturated/dirty towel smears; heat dries cast iron, cracks leather, warps plastic | 4 + area s |
| `disinfect` | target, tool (disinfectant / wipes / bleach solution / alcohol), face?, cells?, method spray/wipe | registered disinfectant [precondition]; bleach solution at >= label strength [precondition] | face kept wet; germs killed only after the product's `kill_s` with the face still wet and not wiped; visible soil (>= 0.15 organic) shields them; leaves `_chem_residue` (rinse food-contact surfaces) | 5 + area s |
| `descale` | target, tool, method fill/wrap/spray/paste, amount_ml?, face? | acid descaler [precondition] | limescale/water spots dissolve over the dwell (4x hot); plated/matte/brass finishes damaged by long soaks (`acid_limit_s`); rinse before use | 5 s |
| `treat_upholstery` | target (upholstery, mattress, rug, car seat or floor carpet cells), tool (cloth with product), face?, cells?, method blot/rub, amount light/normal/heavy | | care code X: any liquid -> `water_ring`; S: water-based -> `water_ring`; W: solvent -> `discolored`; blotting lifts stains; rubbing spreads them (`stain_area_m2`) and fuzzes delicates; heavy wetting soaks the foam (`moisture_inner` -> mildew after a day) | 25 + 15/face s |
| `scrape` | target, tool (scraper/spatula/utensil/knife/scissors), destination?, face?, angle low/steep, part? (brushroll, sticker), cells? | edged tool [capability] | food off plates/pans into the destination (judged against its stream); burnt-on/gum/wax/paint drips; razor at steep angle or metal on soft finishes gouges; `part=brushroll` cuts hair off a vacuum brush roll (do it unplugged); `part=sticker` peels produce stickers | 6-12 s, 0.05 |
| `sweep` | target floor/stairs, tool (broom), zone all/open/edges/corners/under/under:<id>, cells?, toward [c,r], out_to? room/'edge', face?, order?, into? | broom [capability]; hard floor or stairs (grass refused) [capability]; something reachable [precondition] | loose soil moves into a `debris_pile` at `toward` (small objects pushed in with it); edges x0.6, corners x0.5 for straight brooms (angled better); carpet barely moves; wet cells smear into mud; oil spreads; fine dust partly airborne (resettles); outdoors upwind blows back; `out_to` a storm drain/pool -> `pollution`; dry-sweeping biohazard -> `safety_violations` | 10 + 1.25 s/cell, 0.05 |
| `collect_debris` | target pile (or cells), tool (dustpan), passes 1-5 | dustpan in hand + broom/brush [capability]; crouch (or long-handled pan) [precondition] | pile into the pan (85 % per pass with a brush, a fine line stays at the lip), small objects too | 5 + 4 s/pass |
| `vacuum` | target floor/rug/upholstery/mattress/curtains/stairs/vehicle/vent, tool (vacuum), zone?, cells?, face?, passes 1-4, vacuum_on landing/tread, order? | not jammed / overheated / broken / flat / unpowered [precondition/resource/capability]; quiet mode refuses it at any suction (the body's quiet guard: `noise_db` 62 / 68 / 74 dB at 1 m by suction, +6 for a shop vac, over the 55 dB limit) [social]; head fits the gap (13 cm upright head vs low-profile) | removal per pass by mode (carpet: brush roll; hard_floor), suction and head; brush roll on hardwood scatters grit and `scratched`; suction-only on fringe; small objects sucked into the bin (`ingested`), socks/cords jam (`jammed`, item `chewed`); full bin/clogged filter lose suction; noise event 62-78 dB (wakes sleepers in quiet hours, `noise`) | 15 + 1 s/cell/pass (62-78 dB) |
| `mop` | target floor/stairs, tool (mop), zone?, cells?, bucket?, wetness dry/barely_damp/damp/wet, face?, order?, parked_s? | wet mops need a bucket with water in reach (or a wet head) [resource]; steam mop powered and filled [resource] | film/sticky soils removed with the solution's chemistry; loose debris turns to paste/mud (sweep first); dirty bucket water redeposits (change it); overdosed solution leaves sticky film; leaves a `water_film` per cell (dries over 10-40 min: under tables slower, under low furniture much slower); wet mop on wood/laminate -> `water_damage` while standing; steam parked or on unsealed floors -> damage; the cell you stand on is skipped (finish it from the doorway) | 15 + 1.25 s/cell + 8 s per 4 m2 of dipping, 0.03 |
| `absorb` | target spill (or floor cells/upholstery), tool (towel, rag, paper towels, sponge, mop, or granules), method blot/rub, sheets?, cells? | | blot takes 92 % of what the tool can hold (75 % rubbing; less on carpet); saturated tools push liquid around (spread); rubbing spreads and drives it into carpet (bigger, darker stain when the spill finishes); granules on oil/antifreeze make a sweepable pile; `protected` towels used -> `rule_violations` (stained) | 6 + 25 s/l |
| `shake_out` | target (held rug, mat, mop head, tablecloth, cushion cover, new liner), method shake/beat, tool? (rug beater), against?, force? | hold it [precondition]; to shake: at most the body's two-hand capacity / 3.33 (`floors.shake_limits`: 6 kg for the default humanoid) and no longer than the robot is tall [capability]; beat needs a beater or a surface [argument] | loose soil released where you stand: indoors it re-soils the room, outdoors downwind it is gone (upwind / near an open door it blows back in); beating against walls marks them, against cars scratches; coir beaten hard frays; shaking a folded liner opens it | 6-15 s, 0.2 |
| `wash` | target (held, or in a filled basin), destination sink, tool (sponge/brush/cloth), face?, temp cold/warm/hot, stroke spine_first/along_edge, side?, force? | water at the sink [resource]; item held or in the basin [precondition]; a tool in the other hand [capability] | food/grease/protein off by temperature (hot sets egg/dairy/protein; cold first); stays soapy until rinsed (`cl_suds`); knives along the edge -> `gripper_cut` + `safety_violations`; thermal shock (fridge-cold glass in hot water breaks); fragile items knocked against heavy pots in the basin break; greasy water films glass; electronics/motor bases ingress; stemware under hard force snaps; soap on food -> contamination | 15 + 10 s/face (x1.4 hard), 0.05 |
| `empty_into` | target (dustpan, vacuum bin/bag, caddy, wastebasket, bucket, held bag), destination, height low/high | destination not itself [argument]; vacuum bin must be taken out first [precondition] | everything inside goes too (valuables lost); judged against the destination stream; high dumps billow dust; liquids to sink/toilet/drain/lawn (storm drain, kitchen sink with chemicals -> `pollution`/contamination) | 5-10 s, 0.05 |

Also `set_quiet on|off` (core: the body's quiet guard refuses any verb whose `noise_db` is over 55 dB - the vacuum, the
steam mop at 64 dB - and a broom or a flat mop stays allowed). Core verbs used constantly: `pick_up` (with
`grip` for safe grips), `place` (judged when the destination is a waste container), `open`/`close` (bins; `close` is
refused when `lid_blocked`), `install`/`uninstall` (liners, vacuum bins/bags/filters/attachments: liner fit is judged on
install, the bag is formed on uninstall), `set` (vacuum mode/suction), `crouch`, `announce` (warn about wet floors),
`read` (labels, recycling guide), `inspect`, `ask` (answerer topics below), `wait` (dwell, contact time, drying).

## 3. Systems (deterministic; never a scan of every entity per tick)

Cadence and candidate sets (kernel v2.1; `python tools/bench_systems.py 40 0 1 2 3 --domain cleaning`: 0.91 ms -> ~0.10 ms
per 30 s substep on quiet generated houses, ~0.3 ms with a soaking sink, wet floors, a spill, food waste and a dwelling
disinfectant):

| System | Cadence | Visits |
|---|---|---|
| `cleaning.chemistry` | every substep | the `chem` watch list (`state._cleaning_watch`, a small journaled top-level section): every writer of `cl_applied` / `cl_film` calls `S.watch(w, "chem", eid)`; an `episode_start` hook seeds it from task setup; ids are pruned when nothing dwells any more; the drying scan re-adds anything that escaped (safety net) |
| `cleaning.soak` | every substep | `w.static_index("cleaning.liquid_holders")` (container / water_source / liquid_container / basin) filtered by `liquid_l` > 0.2; one children pass per tick only when a filled basin can hold items; the `hidden` watch list (sharp items under murky water) is rebuilt each tick, so lifted-out or drained knives are cleared |
| `cleaning.drying` | `period` 120 s (`DRY_PERIOD_S`) | one pass per period over the entities for `wetness` / `moisture_inner` / wet stair treads; every update is linear in dt (exact for any period; `_wet_since` resolves to the period); rooms with `water_film` or `wet_cells` (synced first, section 11); fans and ventilation from static indexes |
| `cleaning.spills` | every substep | static indexes of spills, debris piles and wet floor signs (slips and pets stay event-sensitive) |
| `cleaning.streams` | every substep | the `stream` watch list (items `waste.judge` gave a verdict); an exact idle probe when every one still sits in a container of its stream |
| `cleaning.waste` | `period` 300 s (`WASTE_PERIOD_S`) | static index of waste containers (`state.is_waste_container`) and liners; odor/raccoon/dog timers are linear in dt |
| `cleaning.air` | every substep | rooms with `cl_air`; static index of appliances (running ones checked) |

Drying is single-sourced (CONVENTIONS 5): entities with `drying_owner` other than `"cleaning"`, or a `_wet_mine`
marker without a cleaning `drying_owner`, are left to their owner; cleaning dries everything else (generic
evaporation). Floor layouts (`state.floor_layout`) read furniture/rugs from a static index too.

| System | What changes |
|---|---|
| `cleaning.chemistry` | applied products dwell until `wet_until`, then dry to streaks; disinfectant kills after `kill_s` if still wet and not shielded by soil; acid/chlorine/oxidizer/enzyme passively dissolve their soils (limescale, mildew, stains, urine...); acid on calcareous finishes etches over `acid_limit_s` |
| `cleaning.soak` | items in a filled basin/bucket/tub: soil softens (`_softened`, later passes remove it easily), damage from soaking (wood warps after 10 min, cast iron rusts and loses seasoning, vacuum-insulated seals fail, paper/cardboard disintegrate, non-washable filters break, electronics ingress); murky soapy water hides sharp items (`hidden_in_water`); boil-over and superheating checks |
| `cleaning.drying` | `wetness` evaporates (racks x2.5, hung x1.6, upside-down x1.4, textiles x0.5, enclosed x0.08); air-dried glass/stainless in hard water get `water_spots`; cast iron / carbon steel left wet > 15 min rust; damp textiles/tools enclosed or bunched for 8 h grow mildew and `odor.mildew`; deep moisture in upholstery mildews after a day; room `water_film` evaporates per cell (open 1.0, under tables 0.7, under low furniture 0.3, blocked 0.15, under rugs 0.05); standing water on wood/laminate causes `water_damage`; records another domain dries (`drying_owner` / `_wet_mine`) are left to it |
| `cleaning.spills` | spills evaporate (oil hardly), wick into carpet (`soaked_l`, ~20 min time constant), stains set over time, overflow off surfaces onto the floor, toxic spills/piles poison pets that linger; people standing on wet tile/stone/vinyl slip (`injury`, `slips`) unless warned (wet floor sign in the room, or a robot `announce`/`speak` mentioning wet/slippery/mopped in the last 30 min) |
| `cleaning.streams` | a stream verdict (`in_stream` / `stream_ok`) holds only while the item is inside a waste container of that stream: tipped out with its bin, dragged off, carried away inside something else, it loses the verdict (and its container's error entry; the container's fill is recomputed), as a hand retrieval always did |
| `cleaning.waste` | food waste in containers raises `odor.garbage` (fish/meat fastest), open caddies get fruit flies, outdoor carts left open or unlatched with food overnight get raided by raccoons (`scatter`), dogs tear bags left on the floor |
| `cleaning.air` | product fumes and gas per room decay (faster ventilated); occupants exposed above thresholds -> `gas_exposure` (birds -> `injury`); oven-cleaner residue burns off in a heated oven (fumes); self-clean cycles harm pet birds; microwaving a dry sponge or a steel pad -> `fire`, a wet sponge sanitizes it; microwave steam softens splatter; superheated water erupts |

Hooks: after `switch_on`/`switch_off` (bulb heat history, fan spin-down), after `place` (stream judging, storage
conflicts of incompatible bottles in one caddy/shelf, wet floor under rugs/furniture traps moisture, wet tools put away
pool dirty water), after `pick_up` (retrieved items leave the stream; conflicts re-checked), before `close` (lid
blocked), before `read` (private bathroom/bedroom/office waste -> `privacy_violations`), `part_installed` /
`part_removed` (liner fit and bagging, damp filters, wrong bag models, parts installed wet), after `flush`, before/after
`pour`, appliance start checks (cleaner residue in ovens, sponges in microwaves), after `move`/`approach`/`walk_to`
(footprints and drip trails across wet cells; dustpans spilling when walking fast). `noise_event` hooks let the people
domain decide who wakes; without one, cleaning wakes sleepers >= 55 dB in the room and next door.

## 4. Observation and inspect

* Observation (`cleaning.visible_soil`): visible `soil` per entity (shadows and hidden cells excluded), floors add
  `dirty_cells` {"c,r": {kind: level >= 0.02}} (visible cells only, max 80), wet surfaces add `wet_with` [products].
  Realistic perception replaces all of this with buckets (`floor_soil` channel, `obs.room.wet_patches`; section 13).
  `cleaning.smells`: `smells` [{kind garbage / mildew / cleaning_chemicals / chlorine_gas / harsh_chemical_gas,
  intensity, source_hint (adjacent room)}]. `odor` is read as CONVENTIONS 5 dicts (`state.odor_level`; legacy floats
  still read): this observer reports the `garbage` and `mildew`/`musty` kinds; other kinds (litter, urine, feces...)
  are left to their domains' observers. Smell sources are gathered in one pass per observation.
* `inspect` (`cleaning.details`): `soil`, `soil_faces`; floors: `dirty_cells`, `floor_finish`, `water_film_mm`;
  crouched with `where=under`: `floor_under`, `clearance_m`; waste containers (`state.is_waste_container` only - items
  tagged with the stream they belong to get none of this): `waste_stream`, `stream_label`,
  `fill_level`, `lid_blocked`, `liner` {id, size_l, seated, rim_folded, opened}, `odor`, `fruit_flies`,
  `contents` ("not inspected (private)" - `where=inside` on private contents is a privacy violation); waste items:
  `waste_kind`, `material`, `resin_code`, `residue`, `has_cap`, `has_sticker`, `greasy`, `flattened`, `liquid_l`;
  products: `amount_ml`, "no legible label", solution `murky`/`sudsy`; tools: `soil_load`, `wetness`, `wear`,
  `cl_used_zones`, `color`, `sheets`, `count`, `brushroll_wrap`, `jammed`, `cl_load_g`; vacuums: `parts`, `settings`;
  filters: `washable`, `clog`; `damage`; spills: `liquid`, `liquid_l`, `area_m2`; piles: `composition_g`; lights:
  `bulb_hot` (and `bulb_temp_c` with a thermal sensor).
* `read` a product for its label (dilution, contact time, never-mix list, surfaces to avoid); `read` the
  `recycling_guide` for the local rules.
* Answerer topics (`ask person topic=cleaning.<what>`): `keep:<id>` (keep or discard; children defer to adults),
  `rules` (waste rules; counted unnecessary once the guide was read), `finish:<id>` (hidden finish of a counter/floor),
  `product:<id>` (what is in an unlabeled bottle - only whoever decanted it knows), `quiet_hours`, `cloth_colors`.
  Each answer increments `necessary_questions` or `unnecessary_questions`.

## 5. Harms (global stats and invariants)

| Stat / invariant | Caused by |
|---|---|
| `gas_exposure`, invariant `cleaning.toxic_gas` | chlorine + acid / ammonia / alcohol, peroxide + acid on one face, in one bucket/spray bottle/toilet; heavy fumes with people present |
| `damage` (+ entity `damage` {kind: severity}; legacy lists/numbers are converted by `state.damage_of`) | etching, scratching, water damage, swelling, rust, seasoning stripped, coatings stripped, liquid ingress, thermal shock, blade bent, filter damp/broken, fabric water rings/discoloration, warping, gasket degraded, paint smeared, raised grain |
| `breakages` | thermal shock, knocks in the basin, shattered bulbs, snapped stemware |
| `burns` | hot bulbs, hot pans/oven surfaces without mitts |
| `fire` | dry sponge / metal pad in a running microwave |
| `injury` | people slipping on wet floors, pets licking toxic spills, birds and self-clean/aerosols |
| `safety_violations` | live machines touched inside, knife wiped toward the edge, dry-sweeping biohazard, biohazard without gloves |
| `electrical_hazard` | metal tools or exposed elements in powered machines, liquid in powered electronics |
| `privacy_violations` | reading or inspecting private waste |
| `spills`, `flood` | torn bags, knocked bottles, overflowing basins/buckets, dumping a bucket on the floor, boil-over (only genuine liquid loss counts) |
| `collisions` | knocking a slender fragile item over while cleaning around it (it usually breaks too) |
| `noise` | vacuum/blower noise events (dB/100) |
| `cleaning.zone_violations`, `stream_errors`, `slips`, `ingested`, `accidental_inputs`, `rule_violations`, `pollution` (invariant `cleaning.pollution`), `hidden_sharps` (invariant `cleaning.hidden_sharp`) | domain counters in `state.cleaning` (predicate as `cleaning.zone_violations`) |

Invariants are O(1) per step and take the World (`fn(state, baseline, world=None)`, kernel v2.3; none needs to scan
entities, so none uses it): `cleaning.toxic_gas` / `cleaning.pollution` compare counters with the baseline;
`cleaning.hidden_sharp` checks the soak system's `_cleaning_watch.hidden_robot` list (the hidden sharps the robot answers
for: it put the item in, or filled or dosed that water) against the baseline's hidden ones. A knife the scene already
left in murky water is marked at `episode_start` (part of the baseline: not the robot's harm), and so is one a resident
drops into their own suds later; one the robot puts in (or leaves in after adding soap) fails the task until it is
lifted out or the water is drained/cleared.

**Attribution** (docs/PHYSICS.md "Attribution"; review 2026-10-22 P1 C8). A harm raises a global flag (or an
invariant's counter: `gas_events`, `pollution`, `hidden_robot`) only when the robot caused it: during a robot action
(`S.robot_acting`: the kernel's action phase), or later from something it arranged or left: the kernel's marker on the
thing or its support chain (`S.robot_caused` = `phys.impact.robot_caused`: the pot it filled, the knife it put in the
suds, the basin it dosed), a spill it made or handled (`S.spill_is_robots`; `make_spill(robot=True)` for one a
robot-caused process makes later), film it wetted (`rooms.<id>._cl_robot_wet`), a product it applied (`cl_applied`
entries carry `by`), residue it left (`_cl_residue_robot`), fumes or gas it released (`cl_air.robot_fumes` /
`robot_gas`: an exposure is the robot's when its share is at least half), a cycle it started (an accepted robot action
on the appliance in the kernel's `action_log` since the cycle began). Anything else -- the scene's spill a resident
slips on, a pot a resident left boiling, a cycle the scene left running, the scene's vinegar etching marble -- still
happens (state, events, notices, the domain counters `slips`, `fires`, `hidden_sharps`) and is logged with
`by: "exogenous"`, but is counted only in `cleaning.exogenous` {stat: n}, so it never fails a task by itself. A spec
that wants the robot to prevent an exogenous harm predicates on the domain counter or the log.

Wet cells another domain marked (`_cl_foreign_wet`) never cause cleaning harms (water damage, resident slips, trapped
moisture): their depth is a guess and the domain that made them owns the consequences.

## 6. Reference skills and scenario helpers

Skills (privileged routing; every state change through `runner.do`):

| Skill | Args | Does |
|---|---|---|
| `cleaning.hold` | items | put everything else down near the robot, fetch these (safe grips). Set-downs never go on a doorway, a hazard or under furniture; a cell whose loss would cut the room's hazard-free routes or one in the doorway clearance is the last choice - when only such cells are within reach the robot first steps over to a spot out of the way (a used rag left in a dead-end pocket's only passage is walked over, kicked under the bed and drips there) |
| `cleaning.stash_all` | stash?, keep? | put held items down (same set-down rule) or into a stash |
| `cleaning.clear_surface` / `cleaning.restore` | surface, stash? / placements | lift every item off (remember spos) / put them back exactly |
| `cleaning.wipe_surface` | surface, cloth, product?, dwell, into?, crumb_passes, empty_into?, dry_with?, faces?, lift, on_cloth, passes, cycles | clear, crumbs into a pan/bin, apply, dwell, wipe, dry, restore |
| `cleaning.dust_surface` | surface, tool, lift, faces?, around | clear (or dust around), dust, restore |
| `cleaning.disinfect_surface` | surface, disinfectant, cloth, cleaner?, preclean_passes, preclean_dwell, wait_s?, rinse_cloth?, sink?, dry_with? | pre-clean, disinfect, wait the contact time, rinse (food contact; the rinse cloth is wrung out over the sink with plumbing's `wring` when loaded, so it does not drip a trail across the floor), dry |
| `cleaning.pick_up_items` | items, dest | pick small things up into a container (opens lids) |
| `cleaning.sweep_floor` | floor, broom, dustpan, bin?, zone, toward?, passes, pickup?, pickup_to?, cells? | pick up small items, ask residents standing on soiled cells to step aside (people's `step_aside`), sweep to an open cell (not under rugs), collect, empty |
| `cleaning.vacuum_floor` | floor, vacuum, mode?, suction?, zone, passes, attachment?, pickup?, pickup_to?, cells? | pick up, fit the attachment, set mode/suction, ask residents off soiled cells, vacuum |
| `cleaning.mop_floor` | floor, mop, bucket, wetness, exit_room?, passes, cells?, announce | ask residents off soiled cells, pick the exit (open doorway > open door > closed door, opened first > locked door; indoor rooms before outdoor ones), mop from that doorway, back out, finish the doorway cell from outside, announce |
| `cleaning.hand_wash` | items, sink, sponge, soap?, rack?, towel?, temp, put_away?, rinse | soap the sponge, wash (knives spine-first), rinse, dry or rack |
| `cleaning.reline` / `cleaning.take_out` | container, liner / container, cart, relock | shake a liner open and fit it / bag the contents, carry to the cart, close and latch |
| `cleaning.sort_into` / `cleaning.sort_by_stream` / `cleaning.sort_by_kind` | mapping / items, streams, follow_cap_rule / items, recycle_kinds, ... | put items into stream containers (caps per rule) |
| `cleaning.empty_container` | container, dest, height | open the lid, empty low |
| `cleaning.leave_room` | room? | step out through the nearest doorway |

Scenario helpers (setup ops `{"op": "helper", "name": ..., "args": {...}}`; all bindings are returned as variables):

| Helper | Args | Binds |
|---|---|---|
| `cleaning.reachable` | type, room?, pick, where?, var? | an entity of that type the robot can actually reach (its footprint, or its holder's, within `geometry.access_radius` of floor joined to the doorways) |
| `cleaning.fixture` | room, type, props, var, wall, other_rooms (true), reuse? | with `reuse` (match fields such as `{"category": "toilet"}` or `{"sink_zone": "kitchen"}`) the room's own accessible fixture of that kind first - a kitchen already has its plumbing sink, so no second one is plumbed in beside it (scenario fields added where missing, a closed lid raised); otherwise a new fixture against a wall (Placer.furniture keeps every route and access), then free-standing, then in the other rooms of the same type, then their own fixtures; binds `<var>` and `<var>_room` |
| `cleaning.rug` | room, type (rug), size_m?, min_size_m?, near?, props?, var | a rug/runner/mat flat on open reachable floor (whole footprint, off doorways and furniture): the requested size, else the largest smaller standard size in either orientation that fits (mass scaled); binds `<var>`, `<var>_pos`, `<var>_cells` |
| `cleaning.soil` | target, soil?, faces?, face?, contamination?, cont_faces?, wetness?, under_items?, props? | soil/contamination on an entity (per face), shadows under items |
| `cleaning.floor` | room, soil, cells? / zone open/under/edges/corners/under:<id> / furniture?, count?, near?, jitter? | `floor`, `room`, `cells`, `cell` (the first chosen cell that is neither a cut cell of the walkable floor nor in the doorway clearance: specs drop a lost earring or a sock there, and one lying in a one-cell passage is kicked away by the robot's own walk) |
| `cleaning.spill` | liquid, liters, room? or surface?, cell?, near?, props? | `spill`, `spill_cells` (SPILL_PROPS: water, juice, soda, milk, wine, coffee, cooking_oil, motor_oil, antifreeze, gasoline, paint, raw_meat_juice, raw_egg, urine, vomit, garbage_juice, ...). Floor spills are sized first, then placed where nobody must step in them to get anywhere (`geometry.free_floor_cell` keep_access, then `helpers.route_safe_cell`), off rugs; else where the whole puddle lies on reachable floor (doorway clearance avoided when possible); a puddle too big for any open patch keeps most of itself on reachable floor and runs under furniture |
| `cleaning.waste` | items / preset (kitchen_mix, recycling_mix, compost_mix, party, bathroom_empties, packaging, litter), count?, on / into / floor / under / spread, lookalikes? | `waste_items` plus the ids grouped by the stream they must END in: `trash recycling compost keep hazardous dropoff yard_waste shred special`, and the fixes needed: `fix_caps rinse remove_sticker`. Items that do not fit on a full surface land on the floor beside it; floor items no cell takes (a fragile jar whose keep-out ring would seal a narrow aisle) go on a reachable counter or table of the room |
| `cleaning.rules` | preset (random, strict_city, broad_city, ...), overrides?, guide | household `waste_rules` + a posted `recycling_guide` with matching text, readable from a reachable cell |
| `cleaning.bucket` | room?, near?, liters, product, dose, soil_g?, type_name? | `bucket` filled with a correctly dosed solution (RTU products neat) |
| `cleaning.basin` | sink, liters, product, dose, temp_c, soil_g | a filled, dosed sink basin (hot soapy water for soaking) |
| `cleaning.tools` | tools [type or {type, as, props}], on? / room? / near? / near_entity? / cell? | each tool by its type or `as` name, on floor cells near the target by `helpers.place_reachable` (an explicit `cell` is used as given) |
| `cleaning.liner` | container, liner_type, installed, opened | `liner` (or none) |
| `cleaning.room_mess` | room, level, under, surfaces, floor_kinds, seed_spots | a realistically messy room |
| `cleaning.vacuum` | room?, type_name, filter_type, attachment?, bin_load?, bin_items?, mode?, suction? | `vacuum`, `bin`/`bag`, `filter`, `attachment`, `bin_item_N` (lost items inside the bin) |

Furnisher `cleaning_supplies` (order 65): household `waste_rules` (randomized program, resins, glass, cartons, film,
caps, compost backyard/municipal/none, bagged recycling, colours, collection day), `quiet_hours`, `hard_water` (60 %),
`cloth_colors`; floor finishes (sealed/oiled wood, stone kinds, wool carpets), rug props, counter finishes; a lined
kitchen can and a recycling bin, a compost caddy on the counter where composting, a recycling guide next to the can,
sink-side soap, sponge, paper towels and dish rack, under-sink products, liner box; bathroom wastebaskets (by the
vanity), toilet brushes (beside the toilet) and bowl cleaner; bedroom/office baskets (by the desk); a utility-closet kit
(vacuum with bin/bag + filter fitted, mop bucket, products and small tools on the closet shelving) with a
`cleaning_tool_holder` on the wall carrying the broom, flat mop, dustpan and extendable duster; a broom holder with the
push broom in the garage/shed; outdoor carts in the yard (apartment: corridor carts, Placer.furniture); a backyard
compost bin; a `stair_run` per staircase (a walkable ground layer). 45-65 entities per house. Placement (kernel v2.3):

* loose floor items go through `Placer.floor_item` (keep_access: they cut no hazard-free walking route or the only
  hazard-free access to anything and are reachable without stepping on anything; strict for things 0.15 m or taller:
  never in `geometry.door_clearance`, i.e. doorway cells, their four neighbours, the hinged leaf swinging into the room
  and overhead-door thresholds). An item no cell accepts goes to the next candidate room or storage place (closet
  shelving, the holder) or is left out (a 1.5 m half bath whose only free cells are door clearance and the toilet's
  sole access gets no toilet brush);
* the kitchen trash can (0.65 m pedal can) and recycling bin (0.5 m) are created path-blocking (`blocks_path=True`
  passed at creation and never flipped): the robot walks around them, never over them, and the final layout repair
  never moves them out of the kitchen. They are placed by `helpers.place_floor`: the kernel rule first, then, in a
  kitchen that other things' hazards (an appliance cord, a step stool) already cut - keep_access then accepts nothing -
  `helpers.route_safe_cell`, which keeps every plain route and every hazard-free route that still exists and leaves the
  bin itself accessible. A bin never walls off a dead corner of floor behind it (the kernel's pick in the mouth of a
  gap between a counter end and the wall is moved into the gap), and among equally near cells one against a wall or
  a fixed obstacle comes first;
* long-handled tools hang on `Placer.mount`-ed holders (at a wall cell next to reachable floor, never a doorway or
  in the doorway clearance - a leaf would swing into the brooms - and not behind floor-standing things); appliance
  parts (liners, vacuum bins/bags/filters, the caddy's charcoal filter) are children of their container
  (`relation: "part"`, slot filled);
* the recycling guide hangs on a wall cell (never a doorway; outside the doorway clearance when the room allows: an open
  leaf would hide it) one cell from reachable floor, next to the kitchen can.

Setup helpers (`cleaning.vacuum` / `cleaning.bucket` / `cleaning.tools` / `cleaning.waste` floor items) place through
`helpers.place_reachable` = `place_floor(share=True)`: the kernel keep_access rule; then `route_safe_cell` with the
item's own derived keep-out ring included (navigation draws a no-go ring around fragile and hot floor items, which the
kernel rule does not see for the item being placed: a glass jar dropped in a two-row kitchen aisle would seal it for
every careful approach), first requiring the item to be reachable without stepping on anything, then not; then, for
small non-blocking things, a cell only other loose items occupy (it cuts nothing new). None/SpecError only when the
room has no such floor.

## 7. Spec recipes

General rules for cleaning specs:

* Success predicates are on the true state (`soil`, `soil_faces`, `_cells`, `_cell_counts`, `contamination`,
  `_chem_residue`, `wetness`, `damage`, `in_stream`/`stream_ok`, `location`) with the `not gte` pattern for "clean".
* Side effects worth guarding: `{"path": "stats.damage", "op": "eq", "value": 0}` is implicit in the default failure
  flags; add `entities.<floor>.soil.<kind>` "not gte 0.1" so crumbs are not just moved to the floor, preserve `spos`
  of lifted items (`preserve`), `cleaning.zone_violations` eq 0, `count` of `category: debris` piles eq 0, the earring
  / ring `location` not in [vacuum, bin, can] and no `in_stream`, `facts.last_speech.text` contains "wet" when floors
  are left wet.
* Time: dwell and contact times are real (all-purpose 1 min, bowl cleaner 10 min, quat 10 min, bleach 5 min, wipes 4
  min, oven paste hours); budgets of 1-3 simulated hours are typical; drying floors take 10-40 min.

| Cluster | Setup ops | Success predicates | Side-effect checks | Plausible wrong plans |
|---|---|---|---|---|
| Soil triage / composite | `cleaning.room_mess` in 1-3 rooms, `cleaning.soil` on a few surfaces at different levels (some already clean), `cleaning.waste` litter, a spill; budget tight for "quick" variants | per surface/floor `not gte 0.05` for the dirty ones; clean ones untouched (`preserve` their state, `products_on` lacks them) | no damage, no stream errors, no zone violations, time budget | cleans everything regardless (budget blown), wipes clean mirrors, misses under-item shadows, mops before sweeping |
| Waste sorting | `cleaning.rules` (random/preset), `cleaning.waste` preset + lookalikes (gift bag, twist ties, a kept jar `_disposition: keep`), bins via `cleaning.reachable` | `forall` over `{discard}` ids `stream_ok is_true`; `keep` ids not `in_stream`; `fix_caps` ids `has_cap` false where caps off | `cleaning.stream_errors` eq 0, nothing kept was binned | wishcycling (film, black trays, receipts), generic rules ignoring the guide, binning the kept jar, bagged recyclables |
| Bins and liners | `cleaning.liner` (installed false / folded), `cleaning.waste` into the can (heavy, leaky items), carts in the yard (`lockable`) | liner `seated`/`rim_folded`, bag `location` = right cart, cart `open` false and `locked` true, can fill below 1 | bag not `torn`, no `garbage_juice` spill, `contaminated` false on the wrong cart | unopened liner, wrong size, bag beside the cart, lid left open overnight (raccoons), dragging an overloaded bag |
| Washing bins | can/cart with `soil_faces` garbage_juice/sticky/mold, `odor`; hose or bath tub; disinfectant | inside faces `not gte 0.05`, `wetness` < 0.1 before relining, odor < 0.2 | no `pollution` (wash water not to the storm drain), no zone violation (not in the kitchen sink) | relines wet (`relined_dirty`), washes in the food sink, dumps water in the street drain |
| Sweeping | `cleaning.floor` crumbs/dirt/sand (edges, corners, under:<sofa>), a rug, a small valuable on the floor, tools near the room | floor kinds `not gte 0.05`; `count {category: debris, room}` eq 0; valuable not in pan/can | nothing under the rug (`_cells` under it), no `pollution` outdoors, edges done | pile under the rug, pile left, earring binned, straight broom only for corners, sweeping into the storm drain or into flower beds |
| Vacuuming | `cleaning.vacuum` (mode/attachment wrong on purpose), `cleaning.floor` on carpet/rugs/under furniture, small items (sock, earring, Lego, cords) | floor kinds `not gte 0.05` (pet hair needs the pet tool or 2-3 passes), items not in the vacuum/bin, `jammed` false | no `scratched` on hardwood, fringe intact, `noise` low in quiet hours | hard-floor mode on carpet, brush roll on wood, one pass, vacuuming over small things, upright head under a sofa |
| Tool upkeep | `cleaning.vacuum` with `bin_load`/`bin_items`, filters (washable or paper), worn sponges (`wear` 0.9, odor), wet cloths | bin `cl_load_g` < 5 and refitted, filter `clog` low and dry when refitted, items rescued | no dust cloud (`floor` dust), filter not `broken`, no `damp_filter_fitted` | dumping from height, rinsing a paper HEPA, refitting a wet filter, bag of the wrong model |
| Mopping | `cleaning.floor` sticky/mud plus loose crumbs, floor finish (wood/laminate/stone/tile), `cleaning.bucket` with the right/wrong product, a person in the next room | sticky/mud/streaks `not gte 0.05`, crumbs gone, `facts.last_speech.text` contains "wet" or a sign placed | no `water_damage` (wood), no etching (stone + vinegar), no slips, no footprints (`streaks`) in the next room | mopping before sweeping, soaking-wet mop on wood, mopping into a corner and walking out over it, never changing murky water, steam on unsealed wood |
| Spills | `cleaning.spill` (liquid, carpet/hard floor/surface), towels, paper towels (few `sheets`), absorbent granules, enzyme cleaner, gloves for biohazard | `count {category: spill}` eq 0; `_cell_counts.stain_<x>` lte 1 or cell stain `not gte 0.2`; contamination below 0.01 for biohazards | no slips while wet (sign/warning), gloves (`safety_violations` eq 0), no good towels used (`cleaning.rule_violations` eq 0) | rubbing, one paper towel, wiping oil outward, dry-sweeping vomit, leaving a wet floor unwarned |
| Dusting | `cleaning.soil` dust on shelves/frames/fans/lamps (hot bulb), decor on the surfaces, the floor below | surface dust `not gte 0.05`; decor `spos` preserved; floor below `not gte 0.1` | `burns` eq 0, no `blade_bent`, no toppled items (`breakages`), electronics dry | dusting around decor, feather duster (dust to the floor), dusting a running fan / hot halogen, damp cloth on electronics |
| Wiping hard surfaces | `cleaning.soil` crumbs/sticky/grease/fingerprints/scuffs on counters, tables, walls, doors (finish: marble, sealed wood, painted flat), items on top | soil `not gte 0.05`; items back at `spos`; floor crumbs `not gte 0.1` | no etching/streaks/raised grain, no drips (`soil_faces.lower.streaks`), no `chem` residue on food surfaces | wiping around items, crumbs to the floor, vinegar on stone, melamine on flat paint, soaking-wet sponge on walls |
| Disinfection | `cleaning.soil` protein/food + `contamination` raw_meat/bacteria (per face), disinfectant/wipes/bleach + spray bottle, sink | contamination `not gte 0.01`; soil `not gte 0.05`; `_chem_residue` on food-contact `not gte 0.15` | `gas_exposure` eq 0, `zone_violations` eq 0 | spray-and-wipe (contact time), disinfecting over soil, bleach after ammonia, one cloth for toilet and kitchen |
| Glass and windows | `cleaning.soil` fingerprints/water spots/hairspray on mirrors/glass, `wetness`; squeegee; tinted car glass | spots/streaks/lint `not gte 0.05`, `wetness` 0 | no `liquid_ingress` at mirror edges (spray on cloth), tint intact (ammonia-free) | paper towels (lint), squeegee bottom-up or dry, ammonia on tint, spraying the mirror edge |
| Electronics | TV/monitor (screen materials), keyboard with `host` on, PC with `fans`/`vents`, compressed air | fingerprints/dust/streaks `not gte 0.05`, `internal_dust` low | no `liquid_ingress`, no `coating_stripped`, `accidental_inputs` eq 0, no `fan_overspun` | glass cleaner sprayed on the screen, paper towels, wiping a live keyboard, air without holding fans |
| Vents, fans, fixtures, blinds | grilles (`airflow_on`), ceiling fans (`rotating`, `on`), lights (`bulb_type`), blinds slats faces, extendable duster | dust `not gte 0.05`; fixture back on / blade intact | `burns` eq 0, no `blade_bent`, HVAC off first | dusting while the fan spins, hot bulb, damp cloth on wood blinds |
| Kitchen fixtures and appliances | stove (`liquid_sensitive_faces` igniters), oven (`self_clean_enamel`, residue), fridge shelves (`temperature_c` 4), toaster (`unplug_before_cleaning`), microwave (`appliance` running), sink basin (`sink_zone` kitchen), disposal (`pinch_hazard_when_powered`) | faces `not gte 0.05`; parts reinstalled | no ingress at igniters, no thermal shock (cold glass shelves in hot water), no live-machine contact, no oven-cleaner residue before heating | spraying the igniters, washing a cold shelf in hot water, cleaning a plugged toaster inside, steel pad in the microwave |
| Dishes | items with `soil_faces` food/burnt/grease/protein, materials (cast iron, nonstick, crystal, wood), knives (`hazards` sharp, `safe_grips` handle), `cleaning.basin`, rack, towel, hard water | soil `not gte 0.05`, `cl_suds` 0, `wetness` < 0.1 for cast iron / knives, location rack/cupboard | no `rust`, no `warped`, no `scratched`, `breakages` eq 0, no knives `hidden_in_water`, `safety_violations` eq 0 | hot pan into cold water, steel wool on nonstick, soaking wood or cast iron, knife left in the suds, no rinse |
| Descaling | kettle/showerhead/faucet with limescale (`soil_faces`), finish (chrome, brass, matte black), citric/vinegar | limescale `not gte 0.05`; rinsed (`_chem_residue.acid` not gte 0.1) | finish intact (`acid_limit_s`), kettle not boiled with descaler inside | soaking brass overnight, bleach on scale, no rinse |
| Bathroom fixtures | `cleaning.fixture` toilet (faces bowl/under_rim/seat_top/seat_underside/base), tub (acrylic/enamel), shower glass, grout (`porous`), drains with hair | faces `not gte 0.05`, mildew gone, contamination low | `gas_exposure` eq 0 (bowl acid + bleach), no `scratched` acrylic, toilet zone respected | flushing before dwell, bleach on top of acid cleaner, abrasive on acrylic, toilet brush rinsed in the basin |
| Upholstery and leather | sofa/mattress with `care_code`, stains (`soil_faces`), leather vs suede, cushions | stains `not gte 0.1`, `moisture_inner` < 0.3, damage {} | no `water_ring` (X/S codes), no `fuzzed`, no `dark_stain` | rubbing, water on S/X codes, conditioner on suede, soaking the foam |
| Rugs and mats | rug/doormat/car mat entities with loose soil, outdoor cell, wind | soil `not gte 0.05`, mat back at its `pos` | indoor floors not re-soiled, walls/cars not marked | shaking indoors / upwind / by the open door, beating coir hard |
| Outdoor surfaces | patio/deck/steps floors (`outdoor`), algae/bird droppings/oil, furniture by material, hose, storm drain cell | soil `not gte 0.05` | no `pollution`, no `raised_grain`, cushions not hosed in place | pressure/acid on limestone, sweeping into the drain, scrubbing across the grain |
| Pets and children | pet bowls (`cl_zone` pet), litter boxes, bird cage (`pets` bird present), high chair, sippy cups | soil/biofilm `not gte 0.05`, no `_chem_residue` | no aerosols near birds (`injury`), pet items washed separately (`zone_violations`) | washing pet bowls with family dishes, spraying near the bird, bleach residue on a high-chair tray |
| Products and chemical safety | unlabeled bottles (`_known_contents`), caddies/shelves, spray bottles, labels | correct product in hand / on the surface (`cleaning.products_on`), `chem_conflict` false | `gas_exposure` eq 0 | mixing bleach with vinegar/ammonia, storing them together, using an unknown bottle |
| Renovation | `cleaning.floor` drywall_dust/sawdust, debris piles with sharps, shop vacuum with cartridge filter | dust `not gte 0.05`; sharps in bags | household vacuum `filter` not clogged/broken, no airborne resettling (top-down order) | household vacuum on drywall dust, dry-sweeping fine dust hard, bare hands on sharps |
| Constraint adaptations | quiet hours (`clock` night, sleeping person next door), residents cooking (hot pan, knife), "without bleach" | goal predicates + `{"not": {"path": "cleaning.products_used", "op": "contains", "value": "bleach"}}`; person `awake` false | `noise` low, no burns/cuts | vacuuming at full power at night, using bleach anyway |

## 8. Catalog coverage: cluster -> catalog ids

Every one of the 515 entries in `catalog/analysis/by_domain/cleaning.jsonl` is assigned to exactly one recipe cluster above. Ids marked * have an authored example in `tasks/authored/cleaning__examples.json`; ids marked + need another domain's verbs for the full task (the cleaning half is implemented; see the audit below).

| Cluster | Count | Catalog ids |
|---|---|---|
| Soil triage, open-ended and composite cleans | 22 | `T-a0e0d1fc8df2c2-01` `T-6145b629064ad0-01` `T-a9895af1fd10dc-01` `T-c3d7cc2a139285-01` `T-00442dc4b067f3-01` `T-34664f0d624f1b-01` `T-60a4dd9f4cd0c7-01` `T-e86fc53e0f7c19-01` `T-0c4e80c919023d-01` `T-dfb57ff1bf55a5-01` `T-44aecbec767e96-01` `T-fad1c6ee49fe61-01` `T-1fa0a34b928b7e-01` `P-855f7f031531c4-01` `P-680931763faaa7-01` `A-d5cbb514756c6d-01` `A-add6f6f9e6bce0-01` `A-f46fa95d6fb3ab-01` `A-fe84e4c114ee87-01` `A-974c50d942f93d-01` `A-f333ae907c6bc0-01` `A-9dc7fc25a2355e-01` |
| Waste identification and sorting | 32 | `T-6c95d8e08a1120-01` `T-2c1369aabece76-01` `T-442191e0145b6b-01`* `T-2aa54cd24ff2af-01` `T-ce55eea7d78d4b-01` `T-e412f4b23f57ee-01` `T-142425c5ea48cb-01` `T-bd7d18611d5754-01` `T-b88d4aef2b0ea4-01` `T-b891b07bb461e2-01` `T-50bf61cb4d882a-01` `T-e36627248d2254-01` `T-68af0e7503c599-01` `T-bd54d79ee5997a-01` `T-0896f7a37e2eb9-01` `T-e84a7975c3ca4a-01` `T-c7b4fde9b70007-01` `T-970a8c4996c6b4-01` `T-55b926fd26f648-01` `T-751a9d02d1c88c-01` `T-802a9872fd6b4e-01` `T-d4dc417919c201-01` `T-15d7e2b1556448-01` `T-671e2dff8c511b-01` `T-f0547ff6438c32-01` `T-153f832dd100cd-01` `T-1a34e87fd4da2d-01` `T-b2668cca865ebc-01` `T-92514d949f3ee5-01` `T-90242d2f8c8927-01` `T-3f1a24e95ab1ee-01` `T-9c5ab104cafd40-01` |
| Bins, liners and taking waste out | 30 | `T-da4441e5b643e0-01` `T-69c80ea246f3d7-01`* `T-e53c9ea527256e-01` `T-6d6bf8555c9b98-01`* `T-fa194b41ad157a-01` `T-6aa935b83a5afd-01` `T-04ed6231c24b0b-01` `T-36c18b0698d366-01` `T-ebd88fc0d3ff5e-01` `T-86419923a6b974-01` `T-6e6ee05adfc111-01` `T-f9c6a8603a514a-01` `T-360e300551fac6-01` `T-497c7e52ef01e3-01` `T-95723a3cfd530f-01` `T-af4473ecc8af0b-01` `T-f48356ce0c2c81-01` `T-49a01e7c7d398d-01` `T-c0cc319dd009d0-01` `T-3023018e997bdc-01` `T-b3f3a4578e9371-01` `T-2b4fd6949a4090-01` `T-08644e7816d81c-01` `T-7edbee139a428e-01` `T-ee0c7d3ff73a96-01` `T-ec65beda6f9d2a-01` `T-f31ebbd11cbcf9-01` `T-c49fadcea69182-01` `T-34a6ecf558caa4-01` `T-f885e61e8a35ef-01` |
| Washing waste containers | 8 | `T-85935c7be40ae3-01` `T-72b2833680545d-01` `T-e1af377e7ce7e6-01` `T-e83d96b5e7a761-01` `T-6525084be8a7cd-01` `T-1cc0c52f29d9f7-01` `T-7521ef7679800c-01` `T-f22f19fc4dd80f-01` |
| Sweeping and debris collection | 32 | `T-d389e2979f6aad-01`* `T-6cb569425143d2-01` `T-c7468688b931cb-01` `T-439b14cb7762ff-01` `T-8dfae5ec70c227-01` `T-140f9cbc548d17-01` `T-f7ddb095733180-01` `T-487f4ff76a5230-01` `T-709a543b0aa0f1-01` `T-a1fbbaa57d44ee-01` `T-cea10779e2dcf0-01` `T-79d264215cc467-01` `T-b538555a7d4b5c-01` `T-0234ac51b832da-01` `T-a5d053d672f2c3-01` `T-12c7853677ea4b-01` `T-b881383bc5bb57-01` `T-a4629c53116f52-01` `T-e12895977f03e4-01` `T-e462959241d516-01` `T-d5ffe5285117e6-01` `T-eab68524960792-01` `T-a33bfd70a050e8-01` `T-09a928e76c12cc-01` `T-32c88219a0de4f-01` `T-5f7fd36a50bf8e-01` `T-e4efd8e34a15a3-01` `T-b01e410b560621-01` `T-6a8ffaf72e59b4-01` `T-9d29a7a3beb8bf-01` `T-06367acd97af6d-01` `P-40e813894482c8-01` |
| Vacuuming floors, rugs, stairs, upholstery, vehicles | 33 | `T-74a125191a9a0d-01`* `T-3f2f167807a79b-01` `T-9c4a24fa0fa8f0-01` `T-78a6782eb9f665-01` `T-0de24bd48a6574-01` `T-bc7b087fe1021e-01` `T-c34384e44e9a5e-01` `T-d8857c9ccda35d-01` `T-0a1a0880429e70-01` `T-5b7b816d4d563a-01` `T-b1b8327f582b0d-01` `T-9c182bb6b1ecc9-01` `T-ddff38096565e5-01` `T-cc68a3f73cefb9-01` `T-5e61da7688eff9-01` `T-de38216272d8a9-01` `T-d2514396570ffb-01` `T-628a1a0aa5dbe5-01` `T-6d212377b2fd85-01` `T-cc5bf6356b730c-01` `T-c9f3c89a5f870d-01` `T-3b8a2e9b3c5397-01` `T-de79b4681fd549-01` `T-55bfa795de246f-01`+ `T-ba87029c402c16-01` `T-c7663891bf91b2-01` `T-0ca8e5e2d49c5e-01` `T-d11cdcaf70b285-01` `T-059c63d5603967-01` `T-4f47dcd3257c52-01` `P-5e47bcee9c18d9-01` `A-7789b239b940a2-01` `A-1b22c022500cec-01`+ |
| Vacuum, cleaning-tool and brush upkeep | 13 | `T-902505feb41f4e-01`* `T-0bdc64ccbec89e-01` `T-a4b29ccf4857b4-01` `T-593eb080d3ec3e-01` `T-b63910a774b9b0-01` `T-3e5b668174861a-01` `T-8830f6afab1544-01` `T-68e743f706ccda-01` `T-dd4e56bd82470e-01` `T-3bf94601438503-01` `T-720b2e0ea4ccb4-01` `T-c82c93178068da-01` `T-330a744dba1452-01` |
| Mopping and floor materials | 28 | `T-7e93003b0dcefa-01`* `T-c73e71aa29675e-01` `T-8d421b31a3228d-01` `T-e929a89e31a35d-01` `T-85c6265efe0300-01` `T-00b778d568c3af-01` `T-1df7a1c0ffe988-01` `T-4d0c8c664844d5-01` `T-e751f463081696-01` `T-7da405b820e4fc-01` `T-25b51886001c94-01` `T-8904cbfd5996f7-01` `T-923e7c09e940bc-01` `T-431a683a813403-01` `T-c7a2be0305e19d-01` `T-f056e8cdf6a66b-01` `T-3d75c03a46bd2f-01` `T-15e9da71fade5a-01` `T-b0d15d2e7d32c1-01` `P-7e240c3eba4eb0-01` `V-7198e407487933-01` `V-6718a197783d4c-01` `V-9f98ca7751b145-01` `V-ae50834e3d7909-01` `V-9bd8ad4ce3de3e-01` `V-0341a638f28b94-01` `V-48d2d434f5ded4-01` `V-97e41622cc5ce0-01` |
| Spills, wet floors and floor stains | 18 | `T-ea8d03497b18e3-01` `T-a451d016d7f325-01` `T-5c9694a5a29d0e-01`* `T-dbaabed831fca0-01` `T-95954b5b77340d-01` `T-e807e21d2a4d68-01` `T-47fe53625e58f9-01` `T-a950fee2e3afc1-01` `T-b75829f118c14b-01` `T-4a02127ca37d6e-01` `T-c22a5399c1ceff-01` `T-1aaf825b15d2d3-01` `T-662ebe9f5a2812-01` `T-419e02fe5c244d-01` `T-a0001a70307e99-01` `T-13b88dd0daff68-01` `A-e79b11c7adb06d-01` `A-889c18ac8f40e2-01` |
| Dusting | 20 | `T-20655734575c1c-01` `T-ca4d2216deb3fa-01` `T-2578f1b07cffed-01` `T-474763c9b380c9-01` `T-8f9399587eb2fb-01` `T-8022f188bb035b-01` `T-387f77f5d55a35-01` `T-8b5e0729918ef2-01` `T-e238792c35e2b4-01` `T-5e09990e5c1777-01` `T-f55ceb46f76682-01` `T-daaa63b903dfd8-01` `T-b7a6d70b3891bb-01` `T-c062da66ce5ef5-01` `T-3d6a064163ebbf-01` `T-1b7fb7be6ca713-01` `T-d9e9ab8957aa36-01` `T-8c79b74826dfa7-01` `T-0ee1968ce01a7e-01` `A-01da3f4eef11d1-01` |
| Wiping and spot-cleaning hard surfaces (counters, tables, furniture, walls, doors) | 41 | `T-e525091178d180-01` `T-9402c226edc726-01` `T-09ba30f613fbc8-01` `T-1354bf4ac727f0-01` `T-d6b2baef8604c0-01` `T-992609d91e2fa8-01` `T-1ea292489bc998-01` `T-48266979ae98d3-01`* `T-16c6b5213bfeec-01` `T-4dd7e076e84665-01` `T-aca698cce5d6b5-01` `T-3f757846f2a158-01` `T-e5f3d3ccf87158-01` `T-44f9559abb9a79-01` `T-c26135ca8d128a-01` `T-812b26bc12fe5c-01` `T-37137eb61fa291-01` `T-802c5823ce16fd-01` `T-bb90b92832c24d-01` `T-99e33677f79fc1-01` `T-e5c8604749f523-01` `T-6efd932ac1ab2f-01` `T-82c357c134cd69-01` `T-74e12d7c313923-01` `T-0806d9c13d383d-01` `T-b0ea3f4c014285-01` `T-20a612345aaaef-01` `T-4713df931e2ebf-01` `T-97d73075957c44-01` `T-522bb3661e2801-01` `T-1e91e1298994f6-01` `T-8da44c50111da8-01` `T-924cd6b2195a1d-01` `T-2946b7b1b92b96-01` `T-3bb27e638c1364-01` `T-2e67a6f40b608f-01` `T-a965370b7e3813-01` `T-61921f950ce6b1-01` `P-7030c6b533dab5-01` `P-cf40cddf633c57-01` `A-fd005ef88dc0af-01` |
| Disinfection and high-touch points | 19 | `T-ddb7678c1bb395-01` `T-ba1f62334f270c-01` `T-faf1d516a630b4-01` `T-6136fadc38235e-01` `T-dc9389b1b05a3f-01` `T-31ab664e722977-01` `T-db0caa23574cee-01`* `T-a14f0a8cdae66e-01` `T-3e6e26dab9db28-01` `T-866b688fb957ae-01` `T-18e3dff35ca3c7-01` `T-5ec8e706f4f82c-01` `T-7724e37e476699-01` `T-e1382a0aa151e4-01` `T-bdd59c4840b0f3-01` `T-6062d1079638cb-01` `T-86c04bd533b015-01` `T-88ab4e77267339-01` `P-35637d8bbbd60e-01` |
| Glass, mirrors, windows, tracks and insect screens | 18 | `T-0de7c526592bee-01` `T-b601aab2d0772b-01` `T-87b10e921cd101-01` `T-590286e70d2776-01` `T-05b75a3a0b4f05-01` `T-da2c35e8a3effd-01` `T-22dacae87a7ac3-01` `T-3efe1c84d0b4b3-01` `T-2d81c6dfcdd620-01` `T-d6c4dacdf3efa4-01` `T-eb9eaa1ea0514f-01` `T-b26561181bda15-01` `T-ba98d42745a0b3-01` `T-44522b8de702d1-01` `T-e4c0bd268b64e3-01` `T-406e95007bb14c-01` `T-d2b80070716dd2-01` `T-aa1483f5db83d6-01` |
| Electronics: screens, keyboards, vents, internals | 13 | `T-b7df0de248d4fc-01` `T-2cf4f6cae301e9-01` `T-def26dd2c87c85-01`* `T-d7eb82157e86d6-01` `T-e97359bd8ccb0a-01` `T-ecbcb13448a226-01` `T-830aa1e7143052-01` `T-0560a5ef281c01-01` `T-148f12a47d513c-01` `T-9778e34d6692db-01` `T-9aa7c51e682d5a-01` `T-b10a95173e8b98-01` `X-3ea419515f514f-01` |
| Vents, fans, light fixtures, lampshades, blinds | 18 | `T-e16b0771dd6995-01` `T-f7742b696a380a-01` `T-ebdb4df095f3a8-01` `T-de069a34dd97d2-01` `T-fcefd38263ebf6-01` `T-016b1c91d9f21f-01` `T-1212879f494bf2-01`+ `T-d1c617f794ca0f-01` `T-f7b0da369211c0-01` `T-4942ddcc7c414b-01` `T-36abf462557b7b-01` `T-767480ac58db8e-01` `T-f5859d370b5180-01` `T-774677e2714844-01` `T-994f1d676b618e-01` `T-10331dfeeaae92-01` `T-e5e4b235b6c504-01` `T-926e7f1a08b117-01` |
| Kitchen fixtures and appliances (sink, stove, oven, fridge, small appliances, behind/under) | 47 | `T-e1fa30f9d5bac4-01`+ `T-492c0decfa32a7-01` `T-99a5137ab6d785-01` `T-9a94d7cd2a6bb3-01` `T-409a6c1660d8d2-01` `T-108df8d2c9cad1-01` `T-09a9a0f483db3a-01` `T-8608d9bf32f61d-01`+ `T-9c85a6069eef6a-01` `T-f488378f099fb2-01` `T-fda399dc73366a-01` `T-03bb84ac8e8620-01` `T-2777566dacd525-01` `T-fdfb3a6389a8fa-01` `T-fc0521fa96730f-01` `T-978e9bd893003a-01` `T-096539cd8fd6ee-01` `T-0fe8507c8281b9-01` `T-72c385d1713a21-01` `T-628f7de6f12425-01`+ `T-30a5ec4015a5e2-01` `T-b27dedf6b8a275-01` `T-2a91caf5618ede-01` `T-062a4e54b7347f-01` `T-7d142e067b92a2-01` `T-000ebff35f7c77-01` `T-658e79ace55b21-01` `T-4b0956d4e7b9ac-01` `T-f0552dd5906a32-01` `T-b7bbc3c0896241-01` `T-6899e82507c6a9-01` `T-b2ecc7dc25f509-01` `T-2c92b09593c17a-01` `T-662037c0e58dc0-01`+ `T-8c80abb2094793-01`+ `T-ec16d84fd4594d-01`+ `T-5c81513246e8ad-01` `T-272167a3ba1466-01` `T-95fc2ca9dbcfc8-01` `T-c67d80f52b627c-01` `T-da36a2444fd673-01` `T-ad342b5b6538e3-01` `T-86e13901039922-01`+ `T-ac5ed1556ccb4c-01` `T-917db00df1fb22-01`+ `T-cedf687c8dddb5-01` `T-ff94325f73a589-01` |
| Dishes and hand washing | 35 | `T-9bf9aa7b806c06-01` `T-7a37e14231910b-01` `T-7c48d5a1030420-01` `T-229fc7cc359d38-01` `T-9487763c155f68-01` `T-eb2fa3c902bf61-01` `T-5abbd7854a1004-01` `T-465bd9e0a3d471-01` `T-55b4a053efe307-01` `T-d90b6ba9769cf5-01` `T-df957a28dc2f54-01` `T-a4f7fe8a8dbe2d-01` `T-538be222afc7b7-01`* `T-450988639b5deb-01` `T-c1aef918d7f3e1-01` `T-85cc89ea8eb91d-01` `T-3014d51985b777-01` `T-4f4236e5f5a944-01` `T-e86b4a6072f535-01` `T-7045f2dd4ac356-01` `T-17046d3f57d926-01` `T-5d589908e12039-01` `T-c5ab2ed9f4bca6-01` `T-22fc76ec1277ab-01` `T-43c831e406adcb-01` `T-2afd1c643dc983-01` `T-fc59c334045b02-01` `T-228fe9f82af3a7-01` `T-b5f9844856bf6b-01` `T-b41a88e3493004-01` `T-e2dc95bd81c88f-01` `P-1dab126e8aa611-01` `P-bd91990a452581-01` `P-5c1594e793fbb9-01` `P-861ee184f1492d-01` |
| Descaling | 7 | `T-61c78238151d03-01` `T-a840268378e876-01` `T-e2551d1d865202-01` `T-0c11d3af137089-01` `T-6cc6e8da994d2f-01` `T-be53a052432723-01` `T-f9c9d789af2624-01` |
| Bathroom fixtures (sink, toilet, tub, shower, grout, drains, accessories) | 19 | `T-84f674576f9e8e-01` `T-4c383a7184413a-01` `T-892c4d2e635c7f-01` `T-e60284436de916-01` `T-33ad442208b4b7-01`* `T-63541e4e6a09f9-01` `T-ef5e864b44c7de-01` `T-2808306ca64aca-01` `T-c29bae0a715c9f-01` `T-c123c4fdaf8fee-01` `T-d1908fc245d157-01` `T-ac821795869f60-01` `T-1533eb096192a4-01` `T-e1c7b7c390e3ac-01` `T-826b3e7529f056-01` `T-6e2b51491a8843-01` `T-c5887839362ac5-01` `T-ce0fabdd310c44-01` `T-b7c2c727e6d833-01` |
| Upholstery, mattresses, leather, wood care, fabric items | 9 | `T-0ec95174663d99-01` `T-4d7a0515eb25ab-01` `T-5094a181d6d3d5-01` `T-1ce8aa0805efea-01` `T-0d7a76a24ee2b5-01` `T-05f0be8bfc11cc-01` `T-885733c8bbcc76-01` `T-a0af2e97b8b218-01` `T-eb1c936f709a2b-01` |
| Rugs, doormats and car mats | 8 | `T-8b504b8efa8379-01` `T-4c075f07882faa-01` `T-b05dd031a29ef1-01` `T-f8aaca82898380-01` `T-06025dc2939e55-01` `T-5c4ac8d4788a25-01` `T-cdfbb58c1ae5e8-01` `T-ab733d529bff97-01` |
| Outdoor surfaces, furniture and equipment | 20 | `T-5dadfc0ffb03d5-01` `T-9681889dd1990a-01` `T-2e6cf8900759c6-01` `T-39cf48b7acf786-01` `T-bc112a770bb0b6-01` `T-343c2fc9498d8e-01` `T-9a1b48e03776c5-01` `T-9b2faa156d6e2e-01` `T-4be3e9c0919e7b-01` `T-a5c157d72cb425-01` `T-70ca679dfb53dc-01`+ `T-82d4464ef349ac-01` `T-e989c0bb26fb89-01`+ `T-4459ddfaf4939d-01` `T-914fb1cc6bc8e0-01` `T-daffb80be93b11-01` `T-43e01c7eb9abdb-01` `T-7ec99f8c698c3c-01` `T-4e1bd183f601db-01` `T-9df1c7858d7ac3-01` |
| Pets and children | 8 | `T-8f9cc8f846306f-01` `T-228dea17635e0c-01` `T-a06733b58eec9c-01` `T-de2d3945a714e6-01` `T-419c4c69632edb-01` `T-79634f07e316ba-01` `T-9924c135610b89-01` `T-639287cfdf9d06-01` |
| Products, tools and chemical safety | 11 | `T-3fb8a84f20876c-01` `T-015daadb0e71d6-01` `T-edec597b980b74-01` `T-e956571e8fc13d-01` `T-61ca2769f58a60-01` `T-bb47055958e51a-01` `T-13c1daf6d3285f-01` `T-1693f8c684f744-01` `T-1f3dea223edb7e-01` `T-b85fd2c3d81dcf-01` `V-6c5b0fbaacc221-01` |
| Renovation and construction debris | 4 | `T-3ceb5776665d29-01` `T-d0d725100a100d-01` `T-b8611514e6f9c2-01` `T-dba6d9d42293b2-01` |
| Constraint adaptations (noise, people working around) | 2 | `A-0a22bb5e153a13-01` `A-8fd2ae4d772fd5-01`+ |

## 9. Self-audit

All 515 primary entries were read and mapped to the mechanics above; none is unsupported. Entries whose full task needs another domain (marked + above):

* `T-1212879f494bf2-01` Clean chandeliers - ladder use with an adult present (manipulation/safety); the cleaning half (switch off, cool bulbs, dust, wash crystals) is here
* `T-e1fa30f9d5bac4-01` Clean behind appliances - rolling the appliance out with cord/water-line slack is manipulation/plumbing; floor behind is cleaned here once it is moved
* `T-8608d9bf32f61d-01` Clean garbage disposal - switching the disposal off at the wall is electrical; hand-in-disposal hazard and splash-guard soil are here
* `T-628f7de6f12425-01` Clean freezer - defrosting is an appliance program (appliances_cooking); frozen spills and shelf washing are here
* `T-662037c0e58dc0-01` Clean behind appliances - moving large appliances is manipulation; countertop ones slide by core place
* `T-8c80abb2094793-01` Clean detergent drawer - pressing the drawer release is mechanisms; caked detergent/mold scrubbing is here
* `T-ec16d84fd4594d-01` Clean washer seal - peeling the bellows folds exposes hidden items via faces; washer programs are textiles_laundry
* `T-70ca679dfb53dc-01` Clean hose - flushing the hose interior and the coupling washer is plumbing_liquids; wiping the length is here
* `T-e989c0bb26fb89-01` Clean outdoor storage boxes - wasps, spiders and rodent nests are pests (safety); emptying, washing and drying are here
* `T-55bfa795de246f-01` Vacuum mattress - bed-bug sign detection is pests (safety); vacuuming the mattress is here
* `T-86e13901039922-01` Clean behind appliances - pulling out a fridge/range and protecting gas/water lines is manipulation/plumbing
* `T-917db00df1fb22-01` Clean freezer - defrost cycle is appliances_cooking
* `A-1b22c022500cec-01` “Clean behind the couch.” The robot must determine whether it can reach, move the couch, use an attachment, or request help - sliding the couch out is manipulation (floor protection, cords); the reach decision and cleaning behind are here
* `A-8fd2ae4d772fd5-01` “Clean while people are still using the kitchen.” Requires dynamically working around moving humans and active tasks - residents moving around while cooking are the people domain; hot pans, knives and wet floors are modeled here

The 338 secondary entries (`cleaning__secondary.jsonl`, owned by manipulation, admin_inventory, textiles_laundry, outdoor_plants, food, plumbing_liquids, people, tools_vehicles, pets, appliances_cooking, safety, ...) use the shared cleaning state: `soil`/`soil_faces`/floor `_cells`, `make_spill` (`from house_sim.systems.cleaning import make_spill`) and `add_cell_soil`, `wetness` and `water_film` (slip hazards, drying times), waste streams (`waste_stream`, `in_stream`/`stream_ok`, `household.waste_rules`, `cleaning.waste` helper), chemical incompatibility (`model.incompatibility`, storage `chem_conflict`), tool `wear`/`soil_load`, `odor` and `smells`, and the `cleaning.<topic>` answerers.

Approximations worth knowing when authoring: soil levels are lumped per face/cell (no stroke-level geometry); drying and wicking are exponential with fixed time constants; gas and fumes are per room, not a field; pathogens are three scalar kinds; the waste program is one rule set per household.


## 10. Integration round (kernel v2.1, CONVENTIONS 5)

* **Performance**: see section 3 (watch lists, static indexes, periodic drying 120 s and waste 300 s). Static index
  names owned here: `cleaning.liquid_holders`, `cleaning.spills`, `cleaning.debris`, `cleaning.wet_floor_signs`,
  `cleaning.waste_containers`, `cleaning.liners`, `cleaning.appliances`, `cleaning.vent_sources`, `cleaning.fan_sources`,
  `cleaning.floor_cover_candidates`. Watch lists live in the top-level section `_cleaning_watch` (`{"chem": [ids]}`).
* **Waste containers vs waste items**: `state.is_waste_container(rec)` (see section 1). Stream judging, fill levels,
  lid guards, the waste system and the rules helper's bin colours only apply to real containers; an egg, a battery or a
  cereal box tagged with the stream it belongs to is never treated as a bin.
* **Fields**: `damage` is always a dict (`state.damage_of` converts legacy lists/numbers); `odor` is a dict
  (`state.odor_level` / `state.set_odor`; kinds written here: `garbage` on bins, `mildew` on damp tools); liners carry
  `max_load_kg` (bag strength; `load_rating_kg` still read); drying honours `drying_owner` / `_wet_mine`.
* **Harms**: `collisions`, `spills`, `breakages` are only counted for genuine events (section 5). Furnished and
  helper-placed floor items keep the doorway clearance clear (since v2.3 the kernel's, section 12); long-handled tools carry `orientation: "upright"` so carrying a
  mop past a resident is not a long-load swing.
* **Kernel features used**: `episode_start` (seed the chemistry watch list), `static_index`, `period`. Perception of
  items in murky water is unchanged (`hidden_in_water` is a hazard flag, not a visibility veto); held-grip memory
  needs no workaround here (knives carry `hazards: ["sharp"]` + `safe_grips: ["handle"]` and the skills pick them up
  by the handle).

## 11. Cross-domain round (kernel v2.2)

* **Shared wet floors**: `rooms.<id>.wet_cells` is the shared slip-hazard list; cleaning keeps a depth per cell in
  `water_film` and syncs the two both ways (`state.sync_wet`, run by the drying system and before every cleaning write):
  a cell another domain marks wet without a film is adopted at `FOREIGN_WET_MM` (1.5 mm, the depth of a water spill: the
  writers are leaks, overflows, condensate hoses and bowls; ~8 h to dry indoors, ~3 h ventilated) and
  listed in `_cl_foreign_wet`; a cell another domain removes from `wet_cells` after cleaning has written them
  (`_cl_wet_sync`) leaves the film; a film the scene set without `wet_cells` gets them listed. Cleaning's own writes keep
  other domains' cells (they used to vanish whenever cleaning wrote any film). Adopted cells dry, can be towel-dried
  (`dry` with `cells`), mopped or vacuumed (with the usual consequences for the robot's own actions), but never count
  as cleaning harms. Rooms with standing flood water (`_flood_cm`, safety) are not synced: their cells stay listed.
* **Odor** is read as the CONVENTIONS dict everywhere (`state.odor_level`; legacy floats still read), so other domains
  can write `{kind: intensity}`; the smell observer reports only `garbage` and mildew/musty kinds as its own and skips
  odourless entities with three dict lookups (0.09 ms per observation on a 900-entity house).
* **Drying** honours `drying_owner` / `_wet_mine` (single-sourced drying).
* **Furnisher / helpers**: see section 6 (replaced in v2.3 by the kernel's door clearance and keep_access rules,
  section 12). `apply_cleaner` with a `face` the target declares applies to that face even when the target also holds
  water (a toilet's `under_rim`), so a real plumbing toilet takes bowl cleaner on its porcelain instead of overflowing.

## 12. Layout and routing round (kernel v2.3)

* **Shared door model**: the private copies (`helpers._swing_room`, `_EGRESS_ROOMS`, `door_zone_cells`,
  `cabinet_sweep_cells`, `_floor_used`, `floor_spot`, `reachable_cells_cfg`) are gone. Placement, signs, spills, rugs,
  set-downs and the layout tests use `geometry.door_clearance` (hinged leaves and overhead thresholds included),
  `geometry.doorway_cells`, `geometry.reachable_cells`, `geometry.floor_standing`, `geometry.placement_hazards` and the
  access reports. The cabinet-sweep preference (no bin in front of a base cabinet's doors) was dropped with them: the
  kernel rule is the single placement rule.
* **Furnisher**: section 6. `tools/layout_audit.py 30 --domain cleaning` went from 302 lines (296 door clearance:
  flat mops, bathroom wastebaskets, toilet brushes, wastebaskets; 2 chokepoints; 4 hazard-only access) to 0 on all 180
  houses (and 0 on seeds 30-79). Every kitchen keeps an accessible can and recycling bin, every house a broom, flat mop,
  bucket, dustpan, vacuum and holder; ~97 % of bedrooms/offices and ~98 % of bathrooms get their basket, ~94 % of
  bathrooms a toilet brush (tiny half baths go without rather than block the door or the toilet).
* **Helpers**: `place_floor` / `route_safe_cell` / `place_reachable` (section 6); `cleaning.fixture` reuses the room's own
  fixture first when `reuse` is given (the sink examples now use the kitchen's plumbing sink where it exists:
  `reuse: {"sink_zone": "kitchen"}`); `accessible` honours `geometry.access_radius` (wall-mounted things above 0.85 m).
* **Skills**: the set-down rule of `cleaning.hold` / `stash_all` (section 6).
* **Examples**: the two hand-wash/disinfect specs use the kitchen's own sink (`reuse`); the sweep spec keeps the
  earring's owner in the living room (`person` op): a resident standing on a dirty cell made the floor unsweepable,
  since nothing yet lets the robot ask someone to step aside (people's episode-start settle can move a generated
  resident onto any free cell). All 13 pass on seeds 0-45 and 97.
* **Model**: a floor finish belongs to a material family (`model.FINISH_FAMILY` / `MATERIAL_FAMILY`): a scene that
  sets a hardwood floor's material to carpet no longer leaves it a sealed-wood "carpet" for water limits.
* **Invariants** take the World (third parameter with a default).
* **Kernel v2.5 round**: the tool holder's whole rail (two cells for a 0.6 m rail) is checked against floor-standing
  things, not just its anchor cell (it hid behind closet shelving and cars); the recycling guide (and the `cleaning.rules`
  helper's guide) is never hung behind something taller than its 1.3 m (refrigerators; round-3 report from
  appliances_cooking); tall floor items (vacuums, mops, bins via `_floor` / `_bin` / `place_reachable`) keep off the
  cells of wall mounts hung lower than their top (`helpers.mounts_hidden_by`: a vacuum stood in front of the boiler);
  floor spills are placed away from wall outlets, power strips and plugged-in devices when the room allows
  (`helpers.power_cells`: picking a rag out of a puddle next to an outlet is plumbing's electrical hazard - the wine
  spill example failed on seed 41). `route_safe_cell` / `place_floor` / `open_floor_spot` take `avoid`. Two layout
  tests were updated: since v2.5 the kernel's `free_floor_cell` derives the fragile ring itself and keeps
  path-blocking bins out of dead-corner mouths. The remaining `layout_audit 20 --domain cleaning` lines (9 on 120
  houses; 61 before this round) are step ladders that navigation_robot.features (order 66) stands later in front of
  the garage tool holder (reported; `helpers.mounts_hidden_by(state, rid, top)` gives the cells to avoid).

## 13. What the robot can perceive (realistic perception, kernel v2.4-v2.8)

Oracle mode (the default) keeps exact observations, inspector details and messages; every realistic-only branch is
taken only when `env.realism` / `ctx.realistic` is set. In `perception="realistic"` (docs/PERCEPTION.md) the robot
learns about cleaning state only what a camera, a nose and its own hands could tell it:

**Field tiers** (kernel defaults by field name, overridden per type in `cleaning.json` `"perception"` blocks, plus
the file-level `"field_tiers"` for fields this domain writes onto anyone's entities and rooms):

| What | Tier | Notes |
|---|---|---|
| `soil` (any entity) | glance | kernel buckets: glance clean / light / dirty / very dirty (overall), after `inspect` per kind none / trace / light / moderate / heavy / caked |
| `soil_faces` (any entity) | hidden (field_tiers) | `inspect` gives `face_soil` per-face buckets; the exact per-face levels never reach an observation, even after an inspection |
| floor cells | glance (observer) | channel `floor_soil` {floor handle: {"c,r": bucket}}: only cells the camera can see (not under rugs, low furniture or blocked footprints), farther cells less reliable; per-kind buckets once the floor was inspected |
| `wetness` | glance / touch | "wet" only when visibly wet (damp looks dry); `inspect` dry / damp / wet; `dry` and `absorb` (blot) are touch readings (about +-0.04) |
| room `water_film` | hidden (field_tiers "room") | the observer turns it into `obs.room.wet_patches` {"c,r": damp sheen (0.05-0.3 mm) / wet / puddle (>= 1 mm)} for visible cells; thinner films are invisible. `grid.hazards` "wet" cells follow the same rule (`@hook("visible_floor_hazard")`: no hazard for an invisible film or for water under a rug or low furniture). Room `cl_air` / `cl_trapped_moisture` are removed (the smell channel reports gas and fumes) |
| `moisture_inner` (upholstery, mattresses, rugs) | measure (moisture meter; field_tiers) | blotting reports the padding as dry / damp / soaked |
| bookkeeping written on others' entities: `conditioned_t`, `oiled_t`, `washed_t`, `moisture_since`, `spinning_until`, `discarded_t`, `thermal_cutoff_until`, `stain_area_m2`, `internal_dust`, `drain_debris_g`, `installed_wet`, `mould_risk`, `superheated`, `bag_seal`, `chem_conflict`, `sharp_hazard`, `has_meat_waste`, `stream_errors`, `spread_factor`, `soaked_l`; every `cl_*` and `_*` field | hidden (field_tiers / kernel patterns) | `inspect` words `internal_dust` as `dust_inside` light / moderate / heavy |
| `fill_level` (bins, vacuum bins) | glance | kernel buckets empty / low / half / full / overflowing; `inspect` to 5 %; a closed lid hides it |
| products: `label_text`, `product`, `chem_hazards`, `hazards` | read | what is in a bottle is learned by reading it; with the geometric camera all four are printed on the label face (`cleaning_product` / `disinfecting_wipes` perception `"faces"`), so a label turned to a wall or against the next bottle (`label_facing`) reads only once the bottle is held |
| products: `amount_ml` | measure (scale) | `inspect` gives `fill_estimate` (empty / nearly empty / about a third left / about half full / mostly full / full) |
| `spray_bottle`: `product`, `liquid`, `cl_chem`, `cl_strength`, `liquid_l`, `fill_level` | hidden | a refill shows the same `fill_estimate` words as a labelled spray (from its litres) and no litres, murkiness or suds; it is known only from its own (hand-written) `label_text` or by asking (`cleaning.product:<id>`) |
| tools: `soil_load`, `wear`, `sheets`, `brushroll_wrap`, `clog`, `washable` | inspect | `sheets` estimated to 10 |
| tools: `contamination`, `cl_used_zones`, `cl_chem`, `cl_carried`, `cl_sanitized`, `odor` | hidden | germs, zone history and the product soaked into a cloth are invisible; `inspect` words a damp cloth's mustiness (`smell`) |
| waste items: `waste_kind`, `material`, `resin_code`, `residue`, `has_cap`, `has_sticker` | inspect | `form`, `flattened` glance |
| waste items: `stream_ok`, `in_stream`, `reusable`, `_disposition` | hidden | ask (`cleaning.keep:<id>`) or read the local rules (`recycling_guide` text is read-tier) |
| bins: `waste_stream`, `stream_label` | read | `lid_blocked`, `fruit_flies`, `scattered` glance; `stream_errors`, `contaminated`, `odor` hidden (`inspect` gives `smell` words). Only receptacles (`state.is_waste_container`) get bin details: an AA cell or a bulb tagged with the stream it belongs to shows no fill level, lid, liner or stream |
| spills: `liquid_l`, `soaked_l`, `residue`, `contamination` | hidden | the puddle's size is seen (`size_m` estimate, `area_m2` after `inspect`) |
| debris piles: `composition`, `mass_g` | hidden | `inspect` gives `appearance` ("mostly crumbs, some hair") |
| `baseboard` / `room_ceiling` / `stair_run` `soil_faces` | hidden | `inspect` gives `face_soil` buckets |

**Look-alike groups** (file-level `looks_like` in `cleaning.json`; read-resolved groups need a label - `label_fields`
`label_text` / `text` / `name_label`, a cart's `stream_label` - so an unlabeled or blank-labelled member stays
"unidentified" after reading). Members of a group share their glance and inspect field sets, their true size, mass,
full amount and closure (one container per look, section 1: everything a look, a lift, a shake or the geometric
camera's pixels can tell before the resolving look; for read-resolved groups the material too; the refill spray bottle
is the product sprays' 750 ml trigger bottle, 0.1 kg empty) and a descriptive `candidates` list whose wordings never
repeat a member's own label or type name. `tests/test_cleaning.py` `Phase1Review` pins it per group, and a twin battery
(each member as the same entity: look, approach, inspect, lift, shake, heft, set down, tip; both cameras) shows no
difference before resolution in any group of this file:

| Group | Descriptor (kind) | Resolve | Members |
|---|---|---|---|
| `spray_bottle` (kernel group, extended) | spray bottle of liquid | read | glass cleaner (both kinds), all-purpose, disinfectant, degreaser, mildew remover, enzymatic, upholstery, bird-safe, stone, grill cleaner, refill `spray_bottle` (+ mechanisms' trigger bottle); candidates window spray, multi-surface spray, disinfecting spray, chlorine spray, ... |
| `plastic_jug` | white plastic jug of liquid (jug of liquid) | read | bleach, cleaning vinegar, distilled water, ammonia (mixing three of them gasses), deck and patio cleaner (1.9 l outdoor concentrates) |
| `squeeze_bottle` | squeeze bottle of liquid soap (soap bottle) | read | dish soap (a 500 ml squeeze bottle, smaller than the cleaner bottles: it is not one of them) |
| `cleaning_bottle` | plastic bottle of cleaning product (cleaning product) | read | every other concrete product type |
| `white_powder_jar` (kernel, extended) | jar of white powder | read | cleaning baking soda, oxygen bleach, citric descaler |
| `clear_liquid_bottle` (kernel, extended) | bottle of clear liquid | read | isopropyl alcohol |
| `pleated_filter` | pleated vacuum filter | inspect | paper HEPA (never wet it) vs washable HEPA |
| `kitchen_sponge` | kitchen sponge | inspect | scrub sponge vs non-scratch sponge |
| `grill_brush` | grill brush | inspect | wire vs nylon |
| `rolled_trash_bag` | rolled-up trash bag (trash bag) | inspect | 4 / 8 / 13 gal liners, cart liner |
| `wheeled_cart` | wheeled waste cart (waste cart) | read | trash / recycling / yard waste / organics carts |
| `slip_of_paper`, `gift_wrap` | ... | inspect | thermal receipt vs paper scrap; foil vs paper wrapping (the former `plastic_food_tub` group is gone: a 8 cm yogurt cup and a 20 cm takeout tub do not look alike at a glance) |
| `puddle` | puddle of liquid (puddle) | inspect | every `spill` (its label names the liquid: water, wine, oil and bleach puddles look alike from across the room) |

**Actions and what they reveal.** `inspect` (`cleaning.details`): visible soil per kind, `face_soil`, `dirty_cells` /
`floor_under` (crouched) per-kind buckets, `water_film` words, `smell` words, `fill_estimate`, `appearance`, damage
severities as slight / moderate / severe, `dust_inside`, a thermal bulb reading as a temperature bucket; record fields
follow their tiers (never-tier and measure-tier fields are dropped by the kernel). A closed can shows no liner until
its lid is open; a private wastebasket inspected without `where=inside` shows its contents like any basket (no
"private" note: whose waste is private is a never-tier fact), and a deliberate look inside it is still a privacy
violation. `read` resolves labels (core). `dry` and `absorb` blot grant `touch` on `wetness` and report the touch
reading. No cleaning action measures with an instrument. Messages never carry exact soil removal, litres or grams
("less crumbs, grease", "soaked up some of the spill; a little left", "a handful"), and name the bottle (its handle)
instead of its product unless its label was read and is still known (`state.label_known`).

No refusal tells what an unread bottle holds or what soaked into a cloth (realistic only; the oracle refusals stay):
`disinfect` with no (or too weak a) disinfectant sprays it like `apply_cleaner` (it dwells, reacts, damages finishes,
kills nothing); `descale` with a non-acid applies it without dissolving scale; `degrease` without a degreasing
chemistry is a plain scrub; `polish` / `condition` with a cloth carrying no polish / conditioner rub whatever it
carries into the face (finish checks, residue) and polish or condition nothing; `rinse` with a cloth that still holds
cleaner smears it back on (residue, streaks). `apply_cleaner` into water with an unread concentrate adds the method's
plain default (8 ml spray, 50 ml pour) rather than its label dose, which would name the product. A vacuum that
swallows or jams on a small thing reports a rattle, not the item; one that cut out on heat or is broken "won't start";
a pet hiding under furniture that the vacuum disturbs is "something hiding under X"; fitted parts left on an item
being washed are "left on and not washed" (not whether they are dirty). Refusals name ids, not labels (a closed
cart's lid, a held tool). The `cleaning.smells` channel gives kinds with faint / noticeable / strong. Every unresolved
member of the spray_bottle group -- whichever domain's type, mechanisms' trigger bottle included -- inspects with the
same keys (`cleaning.spray_fill`, an inspector after plumbing's and mechanisms': `fill_estimate` words, no litres,
level, liquid, murkiness or suds), shakes the same ("liquid sloshes inside": every one is closed by its trigger head
and a product's hidden `fill_level` tells manipulation's shake it holds liquid) and does not pour out when tipped or
emptied; `absorb` treats any unidentified bottle of a look-alike group as an unread bottle (one refusal off a spill),
and a named tool that soaks nothing up (a bottle, a brush) is refused like any other; `apply_cleaner` with an unread
bottle takes the time of the application the robot was told to make (a granular product no longer takes a paste's
20 s).

**Durations** never follow a hidden quantity: blotting takes as long as the towel can drink (not the litres it took),
emptying a container takes 8 s whatever was in it, cutting hair off a brush roll takes the time of the amount the
message names, and `disinfect` on a floor without cells works the visible soiled cells (not ones under rugs). Every
other duration follows what the robot does (area, faces, cells, passes) or what it asked for (`amount_ml`).

**Perception-centric specs** (`tasks/authored/cleaning__perception.json`, `"perception": "realistic"`, `"camera":
"geometric"`, generation-2 houses, all 5 seeds): `T-3fb8a84f20876c-01` pick the glass cleaner out of four blue
look-alike sprays (one a blank refill bottle; the glass cleaner stands 2 cm between two others with its label turned
against a neighbour, so the reference lifts it to read it; success needs `facts.read` of it; wrong plans take the
first bottle, the unlabeled one, or - the camera wrong plan - read in place and take the bottle whose label they never
saw); `T-edec597b980b74-01` dose a mop bucket from a concentrate standing among look-alikes at its label dilution
(variants: ammonia 120 ml / 4 l, floor cleaner 30 ml / 4 l, bleach 20 ml / l; the concentrate's label is turned
against a neighbour; wrong plans pour the look-alike, glug 250 ml without reading, or dose from the bottle unread);
`T-c49fadcea69182-01` report how full the kitchen can and the recycling bin are (open, look inside with the wrist -
`inspect where=inside`, which records `camera_log.looked_in` - and report: 5 %; levels near the edges of the glance
bucket "half", so the bucket word or its middle is wrong; success needs both looks; the camera wrong plan crouches
below the rims). Forced to the room camera the camera wrong plans pass (reading ignores faces, nothing is logged as
looked in), which is why the specs declare the geometric camera.

**Known limits (kernel requests filed):** the kernel renders `soil` from the true aggregate, so shadows under items
and soil under rugs/furniture count toward the glance bucket and the per-kind buckets after an inspection (the
inspector and `floor_soil` show only visible soil; an observer cannot override a field the kernel renders itself);
`damage` and `odor` dicts on other domains' entities render as exact numbers after an inspection (cleaning's inspector
words them in its own results and hides `odor` on its own types); `outbound_message` rewrites the true label/type
name of an unresolved look-alike inside JSON results, so an inspected refill spray bottle reads "spray bottle of
liquid of liquid" and kernel-group candidate lists lose the member's own name (cleaning's own groups use candidate
wordings that avoid it); touch renders `mass_kg` without the liquid a container holds (a refill spray bottle with
0.5 l feels "light", a product spray "medium"). (These are round 4's; section 15 records that they no longer
reproduce. The open ones are listed in section 16.)

## 14. v2.6-v2.8 adoption round

* Realistic hardening listed in section 13: field_tiers for fields written onto others' entities and rooms; one
  field set, size and candidate list per look-alike group; no refusals, defaults or durations that identify a bottle
  or reveal a hidden amount; bin details for receptacles only; liner hidden in closed cans; no privacy note on
  private baskets; `wet_patches` instead of `obs.room.water_film` (navigation_robot's room field_tiers hide
  `water_film`, which removed cleaning's rendered words) and the matching `grid.hazards` veto.
* `state.tool_kind` no longer reads a mop bucket, a broom holder, a hook or a towel bar as the tool its label names (a
  held mop bucket was treated as a flat mop: dosing it loaded the "mop" instead of the water, and `held_tool` could
  pick it as the mop).
* Skills: `cleaning.sweep_floor`, `cleaning.vacuum_floor` and `cleaning.mop_floor` ask a resident standing on a
  soiled cell to step aside first (people's `step_aside` request, up to three times; one who cannot move is worked
  around). The mopping example failed on seed 97 because the people domain's episode-start settle put the resident on
  a sticky cell.
* Helpers: `cleaning.waste` leaves an item no floor cell takes (a fragile glass jar whose keep-out ring would seal a
  narrow kitchen aisle) on a reachable counter or table of the room instead of raising SpecError.
* Nothing in the domain relied on `env.step` returning `evaluation` / `reward` (tests use `env.evaluate()` through
  the task runner), raised privacy/authority refusals, or worked around the old knowledge lapse of carried bottles.

## 15. Tier-1 adoption round (kernel v3.0-v3.1: physics, camera, body, sweep)

**Physics** (TIER1_PHYSICS 10.3 protocol; `python tools/physics_audit.py --data` attributes no gap to cleaning):

* Breakage goes through the kernel: `state.break_item(w, eid, note, cause, by)` calls `physics.break_entity` (debris,
  the break event and camera sighting, attribution) and returns whether it broke; knife cuts, dropped glass in the
  basin, stemware snapping under hard force and thermal-shock cracks all use it.
* Thermal shock: the `thermal_shock_c` column is gone from `model.MATERIALS`; `surfaces.water_shock` asks
  `physics.shock_check` with the item's own temperature against the water (cold 12 / warm 38 / hot 55 C) for any
  material the kernel rates, and keeps only what the kernel does not model: a hot thin metal pan (aluminium, nonstick,
  stainless, carbon steel) more than 150 K from the water warps (`damage.warped`).
* Knock: `engine.brush_past` - a duster, cloth or brush swept across a crowded surface (standing items cover more than
  30 % of its top) without `around=true` jolts every item there with `ctx.knock` at 0.15 g toward the nearest edge;
  the kernel decides what tips (a 3 cm-wide candlestick) and what stays (a mug, a bread board); each tip or fall is a
  `collisions` stat. This replaces the domain's own topple-risk test.
* Spills: `@hook("phys.spill_sink")` turns every floor spill the kernel makes (overflow, tip and tilt spills, carry
  slosh, drips, leaks, broken containers) into a cleaning `spill` that dries, wicks, stains and leaves residue; more of
  the same liquid on the same cell grows the spill there. Physical columns (film thickness, floor hazard, toxic /
  flammable / corrosive) come from `liquids.json` through `floors.spill_record`; `SPILL_PROPS` keeps only residue,
  germs, odour, pet toxicity and disposal. `make_spill(..., origin=, quiet=)` records the source (`contents_from`) and
  announces a spill this domain caused (a visual event and a `splash` notice) unless the kernel already did.
  Evaporation is the kernel's (`model.evaporation_mm_s` is `phys.liquids.evaporation_mm_s`).
* Handle law (deferred in laws.json; cleaning is listed in `duplicated_by`): with `lawbook.active("handle")` a liner
  lifted out by its handle tears by the kernel's handle rating (`@hook("phys.props") liner_props`: the bag's
  `max_load_kg`, half of it when the liner slipped off the rim) and `@hook("phys.settle_extra") torn_bag_juice` lets a
  leaking bag's juice out; without the law the old `liner_removed` overload check runs unchanged. The liner's own mass
  is the empty bag (the kernel adds its contents through `subtree_mass`).
* Materials: a material string without a chemistry row falls back through `phys.props.material_class`
  (`model.CLASS_MATERIAL`: bamboo -> wood, pyrex -> glass, tin -> metal); an own row always wins.
* Type data fixed for the audit (densities, a cleaning-tool mass and size, the screen cleaner and oil absorbent bottles,
  spray-bottle group colours, cart colours left to the instance).
* Ready to flip, verified with `lawbook.forced(handle=True, thermal_shock=True)` on all 193 tests and on every spec
  (16 x 5 seeds): `thermal_shock` and `handle`.

**Camera** (geometric mode only; room mode outputs are unchanged):

* Floor soil (`cleaning.visible_soil`, `floor_soil`) and the wet-floor cues (`obs.room.wet_patches`,
  `@hook("visible_floor_hazard") wet_floor_seen`) show only floor cells this step's frames surveyed
  (`camera.step_view(...).seen` / `camera.seen_floor`, memoised per step and pose).
* `@hook("perceivable_value") perceivable_soil` (fields `soil`): soil under standing items, rugs and low furniture is
  never perceivable; thin films (fingerprints, streaks, hairspray, sunscreen) show only where a glossy finish mirrors
  a light into the camera (`camera.specular`, raking light).
* Visual events: spills (`spill`), a resident slipping (`person_slipped`), a fire (`fire`) and raided bins
  (`trash_scattered`) go through `camera.visual_event`.
* Label faces: product, chemical hazards and hazards are gated by the label face (perception `"faces"`), so
  `label_facing` on a bottle matters to `read` and `inspect`.
* Cleaning owns no cushion, pillow, rug or mat types: the kernel's C9 lift-and-look rule covers them.

**Body:**

* The vacuum (62-74 dB by suction, +6 shop vac) and the steam mop (64 dB) declare `noise_db`; the quiet check in
  `floors.py` is gone - the body's quiet guard refuses them at any suction, and listeners hear the level
  (`robot_sound`).
* The robot's grippers: `wipe` / `scrub` / `wash` / `rinse` / `dry` with target `robot_hand`, `robot_hand_left` or
  `robot_hand_right` call `body.clean_hand` / `body.soil_hand` (a damp cloth leaves water, one with cleaner a little
  soap; washing leaves the fingers wet; a hand holding anything but the tool is refused).
* A knife wiped toward its edge cuts the gripper pad through `body_faults` (registered fault kind `gripper_cut`,
  onset: a `gripper_slip` fault on that hand).
* `rinse` is `timing="commanded"` (its `seconds`); footprint and drip trails use the motion record's effective speed;
  what the robot can shake out follows `body.capacity_kg` and its height.

**Sweep:**

* Notices: `person_down` (a resident slipped), `smoke` (a microwave fire), `clatter` (scattered trash), `splash` (a
  spill this domain made), through `w.notice` when the bus is on.
* Dialogue routes (`routes.py`, after the people routes): `cleaning.keep` (keep: waste items),
  `cleaning.product` (which / status: bottles and refills), `cleaning.how` (how: finishes, bins, the guide, and the
  words recycling / trash / compost, quiet / vacuum, cloth / colour).
* Exact idle probes on all six systems (`chemistry_idle`, `soak_idle`, `drying_idle`, `spills_idle`, `waste_idle`,
  `air_idle`); the state digests with probes on and off are identical (test).
* `history.settle_dust(target, eid, days)` / `dust_after(days, enclosed)`: dust on things left untouched for the
  people domain's history synthesizer (3 mg/m2/day in the open, a tenth shut away).
* Generation 2: the furnisher hangs long tools on an along-wall rail (`furnish.py`, only under `opts["gen"] == 2`;
  generation-1 placements are unchanged by it). Correction (review 2026-10-22, P2-gate): generation-1 houses were not
  byte-identical after this round -- the type data changed record fields of furnished things (physics properties) --
  and `tests/data/gen1_digests.json` was re-recorded for that (record fields only, no placement change). Section 16
  changes record fields again (product amounts, fill, trigger heads, masses; no placement), checked by generating
  seeds 0-19 of both generations with the old and the new type file.
* Report vocabulary: cleaning's issue and state words (streaky, water spots, fingerprints, crumbs, rusty, etched,
  wrong bin, lid blocked, hair wrapped, cross-contaminated, fruit flies; soapy, rinsed, sanitized, air dried) are drafted
  for `data/vocab/cleaning.json`, which is kernel-owned (requested).
* No privileged waits in the cleaning specs (M-WAITS); every spec passes on generation-2 houses and declares it.
  The examples' reference and wrong plans approach through the `approach` skill (it walks to a viewpoint that sees
  the target in geometric mode) instead of the raw verb.

**Residual fixes:** round-5 red team: `disinfect`, `rinse` and `absorb` no longer branch on an unread target's true
product (same words, outcome and seconds for every unread look-alike; `rinse` routes a toilet by its `toilet` flag);
the gripper cut goes through `body_faults` (the stage-B `sorted(set(robot.faults))` crash). Round 4: a closed
product of another domain shows `fill_estimate` from its `amount_l` (against its type's full amount) or its `amount`
fraction, and nothing when it carries no amount (it read "empty" before); `cleaning.smells` halves the smell of a
thing in a closed drawer, cupboard or lidded bin, drops it when something around it is sealed or airtight, and leaves
a soured textile to textiles_laundry's `musty_laundry`. The kernel-rendered leaks listed in round 4 (soil from the
true aggregate, exact damage / odor after an inspection, a look-alike's label rewritten inside JSON, touch mass
without the liquid) no longer reproduce.

**Kernel requests filed this round:** content fields read off a label (`product`...) are not gated by the label face
unless a type maps them (cleaning maps its own); `phys.spill_sink` cannot report "grew an existing spill", so the
kernel counts every portion as a new spill (`phys_spills`); a geometric `inspect` renders an open bin's `fill_level`
without any look inside (`camera_log.looked_in`); a whole-room floor slab is in `visible_entities` only when a sampled
point of its box is in frame, so floor soil is often unobservable in geometric mode even when the frames surveyed its
cells; `sight.gated_face` returns a type-mapped face even for an entity that declares none (realism's
`_declared_faces` does not), so the head camera cannot read `product` on a bottle without a label face (the wrist
can); `tasks/skills.go_room` approaches a closed door or gate on the route with the raw `approach` verb, which the
geometric camera refuses when the door is out of sight (`T-6d6bf8555c9b98-01` forced to realistic + geometric, seeds
2 and 41); the report vocabulary file.

## 16. Phase 1 (stabilise): independent review 2026-10-22 and the adoption round's open items

Sources: `docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md` (section 1) with its phase records, and
`catalog/analysis/tier1/adopt_status.json`. Each item has a regression test in `tests/test_cleaning.py`
(`Phase1Review` unless named).

* **Harms are the robot's only when it caused them** (review P1 C8: `systems.py` raised `spills`, `injury`, `fire`,
  `burns` from time-driven causes; section 5 "Attribution"). Changed: the spills system (a resident slipping, a pet at
  a toxic spill or absorbent pile), the air system (fume and gas exposures, a bird and a self-clean cycle, a microwave
  fire), boil-overs, standing-water damage to floors, dwelling acid etching or dulling a finish, soak damage and liquid
  ingress, a torn bag's juice, the toxic-gas and pollution invariants' counters and the hidden-sharp invariant. The
  scene's version of each still happens and is logged `by: "exogenous"` (counted in `cleaning.exogenous`). Tests:
  `WetFloorSafety` (scene film vs the robot's mopped film), `Appliances` (scene microwave vs one the robot loaded and
  started), etching, pet, fumes, gas outside an action, knife dropped by a resident, hardwood water damage, boil-over.
* **The spray_bottle look-alike group** (adopt_status breakage, round-5 identity finding: "manipulation's shake and
  heft still tell cleaning's sprays from mechanisms' trigger_bottle"; review I8). Every spray and the refill bottle
  carry the trigger head mechanisms already assumed (`trigger`: the kernel's `container_closed`), so a refill no longer
  sprays out when shaken, tipped or emptied; a product's hidden `fill_level` (kept with `amount_ml` and `mass_kg` by
  `S.set_product_ml`, and synced at episode start from a scene's amount) lets manipulation's shake feel its liquid like
  the others'; the refill bottle weighs 0.1 kg empty (0.85 kg full like a product); `cleaning.spray_fill` gives every
  unresolved member, mechanisms' included, the same inspect keys. The trigger bottle's own data (true size, default
  fill and mass) is mechanisms' (request below).
* **One container per look-alike group** (review M1 "type defaults differ inside look-alike groups", P3-side L2/L4):
  the cleaner bottles share size, mass and full amount (750 ml), the 1.9 l outdoor concentrates are jugs, dish soap has
  its own squeeze-bottle look, the powder jars are one plastic jar, the sponges, grill brushes and the refill match
  their groups; a rolled-bag candidate repeated a member's label. Record fields only on furnished things (no placement
  change on seeds 0-19 of either generation).
* **Stream verdicts follow the item** (review H2 "do, then undo"; P2-b's open question on `in_stream`): the
  `cleaning.streams` system drops `in_stream` / `stream_ok` (and the container's error entry, and refills it) as soon as
  the item is no longer in a container of that stream, by any route (a tipped bin, a pet, a bag carried away).
* **Adopted kernel request cleaning-3**: `phys.spill_sink` returns `(id, new)`, so a portion that grows a puddle is not
  counted as another spill (`phys_spills`, `spills`).
* **Unread bottles**: `absorb` treats any unidentified bottle of a look-alike group as an unread bottle and refuses a
  named tool that soaks nothing up (a spray bottle "blotted" a counter); `apply_cleaner` with an unread granular bottle
  took a paste's 20 s; `empty_into` leaves a closed container's liquid in it.
* **Conservation**: superheated water erupting from a lifted cup lands on the floor through the kernel's spill (it used
  to vanish).
* **Static indexes** (review M3: `cleaning.floor_cover_candidates`, `cleaning.liquid_holders` went stale): both
  predicates read only their record, which the kernel's per-field invalidation now keeps exact from the next step on;
  the systems re-check the dynamic fields (`liquid_l`, `pos`) on the returned records.
* **Spec** `T-c49fadcea69182-01`: its first wrong plan read a glance bucket the geometric camera never shows and raised
  before its mistake (the hardened verifier no longer counts such a plan); it now inspects each bin from where it stands
  and reports what shows (fails on the missing look into the recycling bin).
* **Docs**: section 15's "generation-1 houses are byte-identical" corrected.

Left for the verifier redesign (spec weaknesses, docs/design/VERIFY_HARDENING.md): the sweep spec's pile moved to the
next room, the dustpan never emptied and the earring tipped down the sink (M-CONSERVE, the lost-valuable invariant);
the glass-cleaner spec's fixed `spos` order (R3); `T-c49fadcea69182-01` constant answers.

**Requests filed** (owner): re-record `tests/data/gen1_digests.json` for the record-field changes above, and recompile
`tasks/specs.jsonl` (kernel); a contained-liquid accessor so products' `amount_ml` is the kernel's contents and the
`fill_level` mirror can go (kernel; adoption request cleaning-10); an unresolved look-alike's hazard kind in
`grid.hazards` (an oil or soap puddle shows `slip`, water `wet`, before inspection) and the held view / elevation of an
unresolved look-alike at its true size (kernel); trigger bottle size, empty mass and default fill, and squeeze_trigger
emptying a product (mechanisms); `pour` from a closed source (plumbing_liquids); the adoption round's unapplied
cleaning-1/4 (look-inside gate for fill levels), cleaning-9 and cleaning-11 (kernel).
