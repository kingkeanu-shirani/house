# food: ingredients, prep, measuring, storage, dates, serving and food safety

Owner of: every food and drink as an entity (produce, meat/fish, eggs, dairy, bakery, dry goods, condiments, prepared
dishes, leftovers), food packaging and its printed dates, kitchen hand tools (knives, boards, peelers, graters, whisks,
measuring cups/spoons, scales, sieves, thermometers-as-probes, serving utensils), food storage containers, lunch boxes and
coolers, plates/bowls/drinkware as food vessels, and the food-safety model that decides whether food is safe to eat:
temperature history (danger zone, cooling, thawing, cold chain), spoilage and ripening, contamination (raw meat, allergens,
spoilage bacteria, chemicals, kosher meat/dairy contact), storage zones and FIFO, and per-eater checks (allergies, diets,
age/choking, serving temperature, alcohol, texture, sodium) when food is handed over or eaten.

Code: `house_sim/systems/food/` (package: `data`, `model`, `physical` (food in the kernel physics layer), `systems`,
`mixing`, `common`, `actions_knife`, `actions_mix`, `actions_measure`, `hooks`, `perception`, `percept`
(realistic-perception helpers), `furnish`, `scenarios`, `skills`). Types: `house_sim/data/types/food.json` (365 types,
13 look-alike groups). Tests: `tests/test_food.py` (157 tests incl. `RealisticPerception`, `RealisticLeaks`, an
`Integration` class and the tier-1 classes `TierOnePhysics`, `TierOneBody`, `TierOneCamera`, `TierOneSweep`; green with
all 16 domains loaded, with `HOUSE_SIM_STRICT=1` and with `HOUSE_SIM_DOMAINS=core,food`, where the cases that need
another domain's types or verbs skip). Examples: `tasks/authored/food__examples.json` (12 specs) and the
perception-centric `tasks/authored/food__perception.json` (4 specs, `"perception": "realistic"`, two of them on the
geometric camera; section 10): all 16 on generation-2 houses (`"house": {"gen": 2}`), all pass `--check` on seeds
0, 1, 2, 41, 97 in their own mode, and the realistic ones also with `--camera geometric` (section 11).

Fidelity: P-approx for the lumped physical models (Newton cooling with latent heat and a lagged core node, Q10 spoilage,
mixing/aeration/gluten state machine, scale load cell, portioning error, frozen-chunk breakdown in a blender), G for
geometry-backed rules (shelf depth for FIFO, fridge zones/heights, piece sizes for choking, container fill), S for
bookkeeping (contamination tags, dates and labels, recipes, eater profiles).

Dev loop: `python -m unittest tests/test_food.py` (also with `HOUSE_SIM_STRICT=1`),
`python -m house_sim --check tasks/authored/food__examples.json --workers 2` and
`python tools/bench_systems.py 40 0 1 2 3 --domain food` (cost per 30 s substep).

Integration-round conventions (kernel v2.1): derived status is `_food_status` (not `_status`) and the derived odor text
is `_food_odor` (safety owns `_odor` = {class, rate} room-air sources); `damage` written by food (`knife_scratches`,
`heat_mark`, `scratched_coating`) is a dict {kind: severity}; food never evolves `wetness` (it only reads it), so it
claims no `drying_owner`; action messages may carry `" | note"` suffixes after the JSON payload (parse with
`skills.parse_message`); the kernel remembers `held_grip`, so food no longer has its own grip hooks.

Cross-domain round (kernel v2.2): invariants take `(state, baseline, world)` and read candidates from `world.static_index` (food entities, drains; ~0.04 ms per step on a 670-entity house); `food.kitchen` / `food.eaters` are layout-robust (space-checked counters, entry-region reachability, no room splits, roomy chemical-free pantry with stand-in and adjacent-room fallbacks; see section 6); the furnisher respects shelf load ratings and keeps cookware and staples away from the under-sink cabinet and cleaning chemicals; reference skills route `walk_to` around floor hazards (`safe_avoid`).

Layout and routing round (kernel v2.3): the scenario helpers use the kernel's access model instead of private flood
fills: stand-in appliances, pantry units and work counters go through `Placer.furniture` (connectivity "not worse"; no
more walk-through mounted fallback for a fridge, a SpecError names the missing stand-in instead), unreachable work
surfaces come from `geometry.access_report`, and eaters are seated with `access_targets` / `access_report` / `not_worse`
/ `articulation_cells` (other agents and the robot's work spot count as targets; no person is ever dropped on cell
[0, 0]). The furnisher packs cupboards the way a cook does (nesting pots and bowls, stacked pans and plates, sheet pans
and boards filed on edge) and sends cookware that misses the base cabinet to wall units or a drawer, so small kitchens
whose one base cabinet is the under-sink unit still get cookware and dry goods. Bulk food in a vessel takes the
vessel's shape (`model.vessel_size`), so lids close over leftovers. `food.separate_eggs` positioning (`skills._reach_all`) never
tramples: when no careful route reaches both bowls it defers to the strict core `approach`. Layout audit: 0 lines owned by `food.kitchen`.

Perception round (kernel v2.4/v2.5): realistic perception for every food type, action, the inspector and the smell
observer (section 10); oracle output unchanged. Furnishing: a counter crowded by other domains passes the knife block,
board, utensil crock, fruit bowl, shakers and bread box on to the next counter (every generated kitchen has its knives
again, round-3 report); stand-in refrigerators and freezers try every size the strict Placer way before any fallback
spot (a fallback fridge walled a food-only seed-2 kitchen off); a pantry in an adjacent room counts only when the
robot can walk out of the kitchen's entry region toward it.

Red-team round (kernel v2.6-v2.8): portions carry `derived_from` (they look like their unresolved package); unidentified
food shows one group-consistent view with volumes instead of grams; estimates are fixed draws per entity (no viewpoint
in the noise key); refusals that worked as oracles (boiled-egg crack, "different product", grams by eye, pin bones,
carving by the browned outside, opener on bulging cans) became consistent behaviours; pouring durations follow volume;
file-level `field_tiers` cover what food writes on other entities; the egg group resolves only once an egg's inside is
seen (section 10). Inspector percepts are marked `_perceived` (fixes the round-4 report that `food_view` vanished for
unresolved eggs).

Kernel round 5 (v2.9): every food look-alike group that portions come out of has a `portion_descriptor` ("pile of
white powder" for a scoop from an unresolved flour/sugar bag, no longer the bag's own descriptor); an identified portion
shows its own label (the food kind: "all purpose flour", not the bag's printed "all-purpose flour"), and identifying the
source identifies its portions. `percept.unidentified` is the kernel's `Realism.unresolved`, the identity guards use
`ctx.refuse_unidentified` / `ctx.identified`, the realistic inspector hides vessel contents with the kernel's
`Realism.sealed` (a closed glass container shows them), `strain` records `contents_from` on the liquid it moves, and
the two groups whose members differ in size at a glance set the group `size_m` (section 10).

Tier-1 adoption round (kernel v3.0 physics, camera and robot body; v3.1 time, notices, questions and generation 2;
docs/design/TIER1_INTEGRATION.md section 6 "food"): food's physics goes through the kernel where it is plain physics
(packaging, breakage, falls, spills, freeze bursts, masses and shelf ratings) and keeps its own models where food is the
subject (temperature with latent heat, spoilage, ripening, quality); visual occurrences are camera events, shelf rows
are camera rows, dates are read by the camera's read rule; knife slips are typed robot faults and hand work soils the
grippers; perceivable occurrences are notices, the answerer has dialogue routes, the heavy systems have exact idle
probes, and generation-2 kitchens are stocked for their household. Section 11 has the details and the law readiness.

---------------------------------------------------------------------------------------------------------------------

## 1. Object types and fields

### 1.1 The food record (every food entity, packaged or not)

Food is an entity with a `food` dict. Packages are food entities too (the package and its contents are one record:
`package`, `tare_kg`, `full_g`); taking a portion out creates an unpackaged food entity (`M.take` / `M.portion_record`).

| Field | Meaning (units) |
|---|---|
| `food.kind` | product identity (`chicken_breast`, `all_purpose_flour`, `raw_egg`, `beaten_egg`, `batter`, `smoothie`, `oatmeal` ...) |
| `food.cat` | category driving physics/safety: produce (`fruit`, `berry`, `vegetable`, `leafy_green`, `herb`, `root`), `poultry`, `meat`, `ground_meat`, `fish`, `shellfish`, `egg`, `dairy`, `cheese_hard`, `cheese_soft`, `bread`, `grain`, `flour`, `sugar`, `spice`, `condiment`, `sauce`, `snack`, `sweet`, `beverage`, `alcohol`, `frozen_meal`, `frozen_veg`, `frozen_dessert`, `prepared`, `mixture`, `dough`, `baby` |
| `food.phase` | `solid`, `pieces`, `liquid`, `paste`, `powder`, `granular`, `dough` |
| `food.qty_g`, `count`, `unit_g` | mass (g); discrete units (eggs, cans, slices) |
| `size_m` (bulk food) | a loose portion is a low mound (`model.portion_size`); in a vessel with `inner_size_m` liquids and pastes fill the floor wall to wall and piles of pieces/powder spread up to the walls, height = volume / floor area (`model.vessel_size`, applied on transfer, merge, mixing, placing and when some is taken out); packaged food, solids and dough keep their shape |
| `food.cut`, `pieces`, `piece_mm` [l,w,h], `piece_cv` | knife state: `whole`, `halved`, `quartered`, `sliced`, `diced`, `minced`, `chopped`, `cubed`, `julienned`, `shredded`, `strips`, `wedges`, `radial`, `portions`, `mashed`, `grated`, `crushed`, `mixed`; piece size and coefficient of variation (`CV_GOOD` 0.12, sloppy 0.32) |
| `food.parts` | removable parts still present: `skin`, `peel`, `stem`, `seeds`, `pit`, `core`, `hull`, `leaves`, `bone`, `pin_bones`, `shell`, `crust`, `rind`, `cork`, `eyes`, `green_patch`, `woody_stem`, `membrane`, `pith`, `silk`, `husk` |
| `food.composition` | {ingredient kind: g} for mixtures and portions (drives allergens, recipe checks, salt %) |
| `food.mix` | mixing state: `h` homogeneity (>= 0.95 uniform), `aeration` (soft peaks 0.45, stiff 0.7, over-whipped > 1.05), `gluten` (muffins tough > 0.35; windowpane 0.75-1.15), `emulsion_stable_min`, `dry_pockets`, `overmixed`, `cavitated` |
| `food.layers` | coating layers in order (`all_purpose_flour`, `beaten_egg`, `breadcrumbs`, `butter`, `peanut_butter` ...) |
| `food.ripeness`, `ripens` | 0-1 (unripe < 0.5, ripe < 0.95, overripe) |
| `food.ice` | frozen fraction 0-1 |
| `food.cooked`, `raw`, `rte` | cooked flag, raw animal product, ready-to-eat |
| `food.egg_doneness`, `white_set`, `yolk_set` | `liquid`, `white_unset`, `runny_yolk`, `jammy_yolk`, `set`, `overcooked_rubbery` (beaten eggs: `wet`, `set`, ...) |
| `food.toast`, `toast_level` | bread surface browning: `warm`, `lightly toasted`, `golden`, `dark`, `burnt` |
| `food.grain_cooked`, `grain_texture` | grains in liquid: `raw, chalky`, `chewy, undercooked`, `tender`, `mushy, overcooked` (+ `dry, sticking` / `soupy`); dry grain in a hot pan: `dry, toasting` |
| `food.frozen_chunks`, `blended` | after a blender run (see `mixing.blend`) |
| `food.browning`, `soggy`, `bruise`, `burnt`, `scorch`, `melted`, `proof`, `marinade`, `wet`, `foam_g`, `fat_layer_g` | quality clocks |
| `food.shell_fragments`, `yolks_intact`, `yolk_fat_g` | egg quality (shell bits, broken yolks, yolk fat in whites) |
| `allergens`, `diet_tags`, `_trace_allergens` | e.g. `peanut`, `tree_nut`, `milk`, `egg`, `wheat`, `gluten`, `soy`, `fish`, `shellfish`, `sesame`; diet tags `meat`, `poultry`, `pork`, `beef`, `dairy`, `lactose`, `gluten`, `alcohol`, `honey`, `kosher_*` |
| `date_label`, `date_type` | printed regional text (`USE BY 10/14/2026`, `BEST BEFORE 2027 AL 27` (Canadian bilingual), `14.10.2026`); `use_by`, `best_before`, `sell_by`, `packed_on` |
| `opened`, `opened_on`, `opened_life_days`, `storage_opened` | opened state and life once opened (e.g. ketchup 180 d, fridge) |
| `storage` | where it belongs unopened: `freezer`, `fridge`, `pantry`, `counter`, `fruit_bowl`, `bread_box`; `storage_opened` after opening |
| `food_label` | parsed handwritten label (contents, dates, ambiguous_date, owner) after a cross-domain `label`/`apply_label` |
| `outer_packaging`, `inner_info`, `protective_packaging` | removable sleeve/box and what it carries (date, cooking instructions, allergens, lot); film that must stay on |

Hidden truth (underscore keys, never shown to the robot; use them in success predicates):
`_food_status` {class: `ok`/`expiring_soon`/`quality_expired`/`unsafe`, reasons, days_left}, `_spoil` (>= 1 spoiled), `_rotten`,
`_expires`, `_time_out_s` (cumulative danger-zone time), `_abuse_s`, `_abused`, `_core_c`, `_peak_core_c`, `_peak_c`,
`_cooling` {t21_s, t5_s, ok}, `_zone` (`freezer`, `fridge`, `pantry`, `cupboard`, `counter`, `fruit_bowl`, `bread_box`,
`cooler`, `lunchbox`, `trash`, `compost`, `outdoor`, `room`), `_storage_ok` / `_storage_issue`, `_fifo_ok`,
`_stack_fifo_ok`, `_dish` (serving vessels), `_report_check`, `_recipe_check`, `_label_check`, `_policy_ok`, `_free_l`
(fridge free volume), `_salt_pct`, `_liquid_pct`, `_date_label_late`, `_info_lost`, `_packaging_ok`,
`_protective_removed`, `_weak_yolk`, `_pin_bones`, `_bulging`, `_leaking`, `_defect`, `_fat_g` (sinks), `_smoking`,
`_food_odor` (odor text). Thermal bookkeeping: `_tt` (time of the last temperature update), `_amb` (ambient then),
`_tfast` (updated every substep). Food-owned top-level section `_food_sys`: `thermal_full_t`, `air` (bowls turned into
ice/cold-water baths at runtime), `labeled` (entities that received a handwritten label).
`contamination` (a `secret_fields` dict) holds tags: `raw_meat`, `bacteria`, `allergen_<x>`, `chemical`, `kosher_meat`,
`kosher_dairy`, `capsaicin`, `spice_<x>`; >= 0.05 (`CONTAM_THRESHOLD`) counts.

### 1.2 Type families (365 types)

| Family (count) | Types (selection) | Key fields / magnitudes |
|---|---|---|
| Produce, meat, fish, eggs, prepared (`food_item`, 118) | apple 180 g, avocado, banana, onion 150 g, garlic_head/clove, bell_pepper, jalapeno/habanero (capsaicin), tomato, cherry_tomato, grapes, strawberries, pineapple 1.6 kg, mango, watermelon, corn_ear, oysters, whole_fish, salmon_fillet (pin bones), chicken_breast, whole_chicken, roast_chicken, beef_steak, ground_beef, hen_egg 57 g, hard_boiled_egg, bread_slice 34 g, tortilla, burger_bun, taco_shell, chili, soup, stew, cooked_rice, oatmeal, lasagna, batters/doughs, green_salad, prep_scraps | `food` record, `size_m`, `storage`, `perishable`, `allergens`, `diet_tags`; produce `parts`, `round`, `ripens`; raw animal `contamination.raw_meat`, `needs_cooking`, `SAFE_CORE_C` (poultry 74, ground meat 71, whole cuts/fish 63, egg dishes 71) |
| Packaged food (`packaged_food`, 107) | milk_carton, cream, yogurt tub/cup/multipack, butter, cheeses, eggs in `egg_carton`, flours (2.27 kg), sugars, salt, spices, oils, vinegar, condiments (ketchup 567 g, mustard, mayo, relish, salsa, soy, hot sauce), jams/nut butters, canned goods (beans, tomatoes, tuna, soda, beer), cereal (liner bag), oats, rice, pasta, lentils, frozen (pizza with box, peas, berries, chicken, beef, ice cream), deli packs, chicken_breast_pack, ground_beef_pack, bacon, baby formula/puree, wine (cork), juice boxes, bag_of_ice, baby_spinach_bag (protective) | `package` (bag, box, bottle, jar, can, carton, tub, tray, canister, cup, wrapper), `tare_kg`, `full_g`, `date_type`, `_expires`, `opened_life_days`, `storage_opened`, `opener_required` (can_opener, corkscrew) |
| Kitchen tools (63) | chef/paring/bread/boning/fillet/carving knives, cleaver, butter/table knife, oyster_knife, peeler, box_grater, microplane, zester, mandoline (+ guard), apple_corer, cherry_pitter, strawberry_huller, garlic_press, citrus reamer/press, potato masher/ricer, rolling_pin, bench_scraper, whisk, spatulas, wooden_spoon, ladle, skimmer, slotted spoon, tongs (long tongs for grills), measuring cups (1, 1/2, 1/3, 1/4) and spoons (tbsp, tsp, 1/2, 1/4), jigger, funnel, sifter, cheesecloth, pastry brush, corkscrew, can/bottle opener, fish_tweezers, ice_cream_scoop, cookie_scoop | `tool_kind`, `capabilities`, `sharp`, `edge` (sharp/dull), `material` (melting plastic `melt_c`), `volume_ml` for measures |
| Boards (4) | cutting_board_wood, _plastic, _red (raw meat), _green (produce) | `board_use`, `slip_risk`, `non_slip`, wet towel under it fixes slipping |
| Bowls and vessels (11 + 9 cookware) | mixing bowls (steel conducts heat), prep_bowl/ramekin (crack cups), colander, salad_spinner, liquid_measuring_cup, serving/soup/cereal bowls, spice_mortar; saucepan, stock_pot, frying/nonstick pan, dutch oven, baking sheet, cake/loaf pans, casserole | `capacity_l`, `inner_size_m`, `conducts_heat`, `nonstick` (metal scratches), `max_touch_temp_c` 65 |
| Storage (9 + 11) | storage containers S/M/L with lids, glass_container, mason_jar (headspace when freezing), zip_top_bag, freezer_bag (press air out), food_canister, butter_dish, egg_carton, bread_box, fruit_bowl, lunch_box, picnic_cooler, thermos, ice_bucket, freezer_ice_pack (latent), fridge_tray (drip tray), grocery_bag | lids, `food_safe`, `rests_on_rim`, insulated air model (`food_air_model`, `ua_w_k`) |
| Tableware/drinkware (11) | dinner/side/kids/melamine plates, platter, glasses, mug, sippy cup, baby bottle, wine glass | `max_touch_temp_c`, `material` (glass vs plastic for kids/outdoors) |
| Other | kitchen_scale (5 kg x 1 g, `display`, tare, units g/oz/lb/ml), cooling_rack, parchment_sheet, spice_grinder, egg_separator, fine_mesh_sieve, food_glove_pair/box (disposable gloves), kitchen_cut_glove, spoon_rest, knife_block, utensil_crock, recipe_card (`text` + hidden `_recipe`), food_outer_packaging (removed sleeves/boxes) | |

Furnishing (`furnish.py`, order 72, after cleaning supplies and appliances; it only places items on/in existing furniture, so it owns no layout-audit lines): knife block, board, crock, fruit bowl and salt/pepper on the counters; cookware in a base cabinet (the under-sink one only when it is the only one, and never on a shelf with chemicals), cookware that does not fit there goes, after the dishes and staples, to another food-safe base unit, a wall unit or a drawer where it fits; food storage containers in a second base cabinet, else a wall unit or drawer, never under the sink; pantry staples on pantry-room shelving, the pantry cabinet, then other base and wall units (heavy bags on a bottom shelf). Every shelf placement (`_put`) skips shelves holding household chemicals (`model.is_chemical`), keeps rated shelves (`model.shelf_rating`: the kernel's nominal `shelf_rating_kg`, the supports.json ratings behind the overload law and admin_inventory's shelf collapse; `model.shelf_load`: the kernel's slot load, contents and liquids included) under 70 % of their rating and shelf floors under 60 % of their area including what other domains put there, and spreads items to the least crowded shelf, so tasks that put a kibble bag back or add a few cans never stack items above a lid or collapse a shelf; an item with no room is skipped. Shelf floor is counted the way a cook packs (`_pile_key`): flat things (plates, platters, bags, boards; one pile per category), nesting pots, stacked frying pans, nesting bowls/colanders and empty storage containers each take their largest footprint once; sheet pans and cutting boards are filed on edge (`pack_orientation: "on_edge"`, 46 x 3 cm of floor) where the shelf clearance (`_clearance`) allows. Small kitchens (core gives kitchens under 17 m2 one base cabinet, which plumbing makes the under-sink unit) keep dishes, some cookware and the main dry goods in the wall units; a few items are skipped when the units are full. Fridge/freezer contents only when an appliance cold unit exists. Generation 2 (`furnish.household_stock`, section 11): the kitchen is stocked for its household; generation-1 houses are byte-identical.

Stand-ins: when no appliance domain provides a fridge/freezer, the `food.kitchen` helper converts a core cabinet into a
"refrigerator" (3.5 C, 4 shelves, 2 crisper drawers + door bin) / "freezer" (-18 C) with `food_air_model` so tasks still
run; with an appliance cold unit present food reports `heat_load_w` to it instead.

---------------------------------------------------------------------------------------------------------------------

## 2. Actions (38 verbs)

All durations are realistic seconds (spent via `ctx.spend`); refusals use ActionError categories (`argument`,
`precondition`, `capability`, `resource`, `safety`). Common preconditions: target reachable and visible; a cutting tool
held by its handle (the kernel's `held_grip` memory lets a knife picked up by the handle be set down and carried); food on a resting surface (cutting in the hand is a
safety refusal); the steadying hand free; packages opened first.

| Verb | Params | Preconditions / refusals | Effects | Duration |
|---|---|---|---|---|
| `cut` | target, tool?, technique (claw/flat/none), style (halve, quarter, wedges, strips, chunks, pieces, flat_side, julienne, shred, radial, portions), pieces, size_mm, axis, angle_to_grain_deg, offset_deg | on a board (wooden counter is damaged, stone dulls the knife); round whole produce needs halve/flat_side first or the knife slips (cut injury, `stats.damage`, robot fault); halve/quarter only on whole items | `cut`, `pieces`, `piece_mm`, `piece_cv`; across-grain strips tender; radial cuts share toppings fairly | 3-90 s by pieces |
| `slice` | thickness_mm (5), form (rounds, half_moons, bias, planks) | mandoline: guard held or cut glove, else injury risk | slices, thickness CV | ~1.5 s/slice |
| `dice` | size (brunoise 3, small 6, medium 12, large 20) or size_mm | onions: peeled, root end kept, halved first | `diced`, `piece_mm`, CV; skin/slivers flags on sloppy work | 30-120 s |
| `mince` | size_mm 2, method (knife, press) | peel cloves; < 1 mm turns to watery paste | `minced` | 20-60 s |
| `chop` | size fine/medium/coarse; herbs dry first; strip woody stems | | `chopped`; wet herbs bruise/clump | 15-60 s |
| `cube` | size_mm 25 | rind/core removed (melon washed first) | `cubed` | |
| `trim` | part, method snap, length_mm | | asparagus snaps at the woody point; fixed length over/under-trims | |
| `peel` | tool (peeler ~1 mm / paring knife ~2.5 mm / hands for citrus, banana), direction away, grip gentle for soft fruit | | removes `skin`/`peel`; yield loss; scraps to board/compost | |
| `remove_part` | part (stem, seeds, pit, leaves, hull, bone, pin_bones, shell, core, rib, woody_stem, membrane, pith, eyes, green_patch, crust, cork, clove), tool, method, count | right tool (pitter, huller, corer, tweezers, corkscrew, spoon, knife); explicit wrong tool is refused | part removed, yield loss by method, scraps; capsaicin on hands from seeding chiles; cork crumbles with the wrong method | |
| `carve` | thickness_mm, direction, style | cooked meat only; rest >= 10 min after heat or juices run out | slices/joints | |
| `fillet` | tool (fillet/chef knife) | whole fish | two fillets, pin bones remain (hidden until inspected closely) | |
| `shuck` | method | oyster: hand protected (cut glove or towel); corn: husk + silk | | |
| `grate` | side (coarse/fine/zest), amount_g, stop_at_mm 25 | soft cheese chilled; stopping above ~20 mm unless cut glove | grated product; knuckle injury when grated to the nub | |
| `zest` | pressure light/firm | wash waxed fruit; zest before juicing | firm digs into bitter pith | |
| `juice` | strain, pulp | halved citrus | juice by yield (lemon ~30 ml); seeds fall in unless strained | |
| `crush` | force, passes | closed bag for crackers; glass as hammer breaks | crumbs, crushed garlic | |
| `grind` | setting, seconds | lid closed; powered grinder or mortar | particle size; grinder keeps flavour taint | |
| `mash` | strokes | cooked/soft food; masher/ricer/fork | mashed; overworking potatoes gluey | |
| `crack_egg` | destination, surface flat/rim/edge, force | egg held | raw egg in destination, shell to hand; rim cracks add shell; rotten egg smells (sulfur) and ruins the batch; hard force breaks yolk/splatters | 6-9 s |
| `separate_egg` | whites_to, yolk_to, method (shell, separator, hand) | | weak yolk breaks -> `yolk_fat_g` in whites (whites will not whip) | |
| `whisk` | strokes, speed, method (beat, whip, emulsify) | deep bowl; fast in shallow/full vessel splashes | aeration/homogeneity; over-whipping splits cream (butter) | |
| `mix` | strokes, speed, method (stir, fold, beat, cream, cut_in) | | homogeneity, dry pockets, gluten; order of additions logged for recipe checks | |
| `stir` | strokes, speed, scrape_bottom | heat-safe utensil (plastic melts in hot pans) | evens temperature, stops scorching, stirs poured water through (dilutes salt); scraping a burnt bottom spreads burnt flavour; foam stirred in clouds stock | |
| `knead` | minutes, added_flour_g | both hands free, floured surface | gluten toward windowpane; extra flour dries dough | |
| `roll_dough` | thickness_mm, rotate, flour_g, passes | rolling pin | even sheet vs oval/thin edges; warm butter pastry tears | |
| `shape` | form, count, destination, method scale/eyeball, spacing_mm, seam, fold_ends | | dough pieces (+-2 % by scale, +-20 % eyeballed); tortilla wraps/tacos; cold tortillas crack | |
| `strain` | destination, tool (colander, sieve, fine mesh, cheesecloth), keep, reserve_ml, reserve_to | strainer set in the destination | solids above mesh kept; fat must not go down a drain (invariant) | |
| `sift` | destination, tool, amount_g | | lumps > 1 mm removed; sifted flour ~110 g/cup | |
| `marinate` | marinade, destination, amount_ml | non-reactive container (aluminium + acid reacts) | acid turns fish mushy after ~1 h; raw-meat marinade becomes contaminated | |
| `season` | seasoning, amount_g, pinches, shakes, turns, tool | | adds salt/pepper/spice to composition (salt 0.12 g/shake, 0.36 g/pinch); loose shaker lid dumps; steam clumps shaker | |
| `coat` | coating, method (dredge, dip, toss, brush, rub, spread, press, grease), amount_g, shake_off | pat wet food dry; flour before egg before crumbs; cold butter tears bread | layers; coating source picks up raw meat/allergens; spread creates barrier against soggy bread | |
| `weigh` | tare, unit | scale on, load under 5 kg | display text (`500 g`, `O-Ld`); power-on zeroes with the load on it | |
| `scoop` | tool, destination, technique (spoon_and_level, dip_and_sweep, packed, heaped, rounded, level), count | measuring tool held; spoon_and_level needs a second utensil | grams by technique and density (flour 0.528 vs 0.634 g/ml); wet scoop clumps | |
| `transfer` | destination, item, amount_g / amount_ml / fraction / units / until_reading, tool, method (pour, scrape, ladle, spoon, tongs, funnel, slotted, skim), rate, read, careful | destination can hold food and is open | residue left when pouring (scrape gets it all); spills when fast/heavy; boiling liquid cracks cold glass; non-food containers taint; overflow; into an opened package of the same product = consolidation (contents expire with the oldest part, printed date may now be late) | 15 s + mass |
| `portion` | into [containers], amount_g, method scale/eyeball, press_air | | equal shares (scale +-2 %, eyeballed first portions too big); press_air prevents freezer burn | |
| `plate` | destination, item, amount_g, pieces, tool, on_top_of, careful | serving utensil held | portion on its own spot (or on top of another food); careless plating drips on the rim | |
| `garnish` | garnish, amount_g, placement | | placed on the food; scatter dusts rim; allergy/aversion checks apply | |
| `thaw` | method (fridge, cold_water, counter), destination, refresh_water | | fridge: ~1 day per 2 kg on a tray low in the fridge; cold water: sealed, refresh every 30 min; counter: surface enters the danger zone (unsafe) | |

Cross-domain verbs food reacts to (hooks, section 4): `pick_up`, `place`, `move`, `open`, `close`, `hand_over`,
`receive`, `report`, `speak`/`explain`/`confirm`/`announce` (suggestions), `wash`/`rinse`/`scrub`/`dry` (produce, hands),
`pour`/`empty` (fat to drains), `label`/`apply_label` (handwritten labels), `wear`/`take_off` (gloves), `unwrap` (outer
packaging), `blend` (blender jar contents), event hook `person_eats`.

---------------------------------------------------------------------------------------------------------------------

## 3. Systems (simulated time)

| System | Order / cadence | What changes |
|---|---|---|
| `food.thermal` | 52, every 30 s substep; full pass on the first substep after every action and every 300 s, in between only `_tfast` items (hot >= 40 C, in contact with a heated vessel or water, in a food-owned air space, or expected to move > 1 C within 5 min). Each update integrates the elapsed interval in closed form; an item that sat in equilibrium until moved is credited only the last substep in its new place | Food temperature (food entities carry `fixed_temperature`, the kernel's alias of `thermal_owner`, so the kernel thermal system and its phase law skip them; idle probe `systems.thermal_idle`, exact): Newton cooling tau = m c / (h A), h by surroundings (still air 8, fridge 12, freezer 14, oven 25 x coupling, contact with a heated pan 150, water 250), ambient found by walking the containment chain (a lidded box inside a fridge sits in fridge air), enclosure factors (closed package 0.8, lid 0.55); latent heat (freezing/thawing plateau); lagged core node (slab, sphere for eggs in shell); liquids capped at 100 C; doneness (eggs set, bread toasts/burns and smokes, grains absorb water and turn into `oatmeal`/`cooked_rice`), pathogen kill at safe core temperature, `cooked` flag; passive vs actively heated metal vessels; air model for food-owned insulated spaces (stand-in fridge/freezer, lunch box, cooler, thermos, ice and cold-water baths, latent ice packs); `heat_load_w` reported to appliance cold units |
| `food.safety` | 130, kernel `period` 300 s (dt = exact accumulated interval) | Clocks: danger zone 5-57 C (2 h limit, 1 h above 32 C ambient) -> `_time_out_s`, `_abused`; two-stage cooling (60->21 C in 2 h, ->5 C in 6 h more) -> `_cooling.ok`; Q10 spoilage (`_spoil`), ripening and chill injury, enzymatic browning of cut surfaces, sogginess, wilting, proofing and dough skin, marinade texture, scorching unstirred thick food, freezer burn, insects on food outdoors, foam on boils, soaking grit, sealed rigid containers of liquid food bursting or bulging when the ice needs more than their headspace (the kernel's freeze-burst rule, `physical.freeze_burst`: a full wine bottle, a closed mason jar of stock; an open jar does not), thawing raw meat packs leaking and dripping (contaminating what is below), utensil melting; scale displays; derived truth: `_food_status`, `_zone`, `_storage_ok`/`_storage_issue`, appearance and odor, FIFO (`_fifo_ok`, `_stack_fifo_ok`), `_recipe_check`, `_label_check`, `_policy_ok`, `_free_l`, `_salt_pct`/`_liquid_pct`, `_packaging_ok`, `_dish` per serving vessel |
| `food.refresh` | 131, first substep after every action/wait only | derived truth (as above), scale displays, melted utensils, so predicates graded right after an action see its effect; the `episode_start` hook computes the same at reset (baseline and first observation) |

Cost (all 16 domains, `tools/bench_systems.py 40 0 1 2 3 --domain food`): 1.68 ms per substep before the integration
round, about 0.3 ms after (about 0.4 ms when simulated time advances, as in the engine); v2.3 round
(`tools/bench_systems.py 20`): food.thermal 0.16 ms + food.safety 0.17 ms + food.refresh 0.04 ms of a 6.0 ms engine. Candidates come from
`w.static_index` (food entities, cold units, scales, serving vessels, meltable utensils, checked containers) plus the
`_food_sys` registries; no system sorts or scans every entity per tick.

Ambient rule for appliance parts: a plastic bin/drawer inside a refrigerator or freezer (appliance `crisper_drawer`,
`fridge_door_bin`, which carry `conducts_heat` for the kernel) is not a heated/cooled pan: food in it sits in the unit's
air (fridge h, closed drawer = enclosure factor).

Storage rules (`_storage_issue` codes): `wrong_zone:<zone>`, `raw_above_rte` (raw meat must be below ready-to-eat food and
contained), `not_contained`, `no_door` (milk/eggs not in the door), `crisper_humidity` (leafy greens high, ethylene fruit
low), `ethylene` (apples/bananas next to sensitive produce), `uncovered_prepared`, `wet_produce`, `heavy_high`,
`store_apart` (onions/potatoes), `chemical_on_food_shelf` (a household chemical on the same pantry shelf: toxic/corrosive hazards, a cleaning/detergent/paint-type category or a `chemical` / `chem:*` tag, see `model.is_chemical`), `left_open`, `headspace` (liquids frozen too full),
`rear_vent` (produce freezing at the back), `leaking`, `plate_scraps`. FIFO: on each shelf the older unit of a product
must not sit behind a newer one (`spos` depth); stacked food the older on top.

---------------------------------------------------------------------------------------------------------------------

## 4. Hooks, observation and inspection

Hooks (all `order` 40-120): a shelf item
blocked by one in front cannot be picked; pick_up/place/receive spread contamination by contact (hands or worn gloves,
tools, surfaces, floor) and log ready-to-eat contacts; placing hot pans on wood marks it; solids into drains, parchment
lining pans, plate scraps; `open` (can opener/corkscrew needed, `opened_on`, bulging cans, shaken carbonated drinks
spray); `close` (cereal liner, rim smears stop lids sealing -> leaks); `hand_over` / `person_eats` -> eater checks and
consumption (stats below), household rules (e.g. no sweets between meals); `report` grading against truth
(`_report_check`); spoken suggestions logged; `wash`/`rinse`/`scrub` (produce cleaned, robot hands cleaned only with soap
and >= 20 s; rinsing raw poultry splashes contamination around the sink); `dry`; `pour`/`empty` fat to a drain;
`label`/`apply_label` parse the label text (`food_label`, fixes `_date_label_late` / `_info_lost`); `wear`/`take_off`
gloves; `unwrap` (outer packaging -> `food_outer_packaging` item to its waste stream, `_info_lost`, protective film
removed -> `_protective_removed`); `blend` (frozen chunk breakdown needs >= 30 % liquid, otherwise cavitation; melting
fruit chills the drink; open jar or overfill spills).

Inspector `food.details` (inspect): `food` view {kind, cut, pieces, piece_size_mm, piece_cv, phase, count, parts,
peeled, layers, form, cooked, ice, seeds, shell_fragments, yolks_intact, sheet, gap_mm, pulp, drained, wet, torn,
cracked, burst, slice_thickness_mm, grate_side, particle_mm, texture, skin_in_product, slivers, marinade, proof, soggy,
browning, egg_doneness, toast_level, grain_texture, burnt, scorch, approx_g, appearance, odor (exposed food only), state
(mixing words: foamy/soft peaks/stiff peaks/over-whipped, smooth/streaky, dry flour pockets, emulsified/separating),
ripeness, date_label (when readable), opened, food_label}; `inspect where=closely` adds firmness (frozen hard, yields to
gentle pressure, slimy), dough windowpane, pin bones found, bulging can, loose lid, egg shell intact/cracked and, with a
probe thermometer held, `probe_temp_c` (core temperature; a dirty probe contaminates); vessels list `food_contents` and
`fill_level`; kitchen scale `display`; food-owned cold spaces show `thermometer_c`. Realistic perception renders all of
this as perceived (`food_view`, never the raw record; section 10).

Observer `food.smells` (olfactory sensor): `smells` [{kind: rotten_egg | spoiled_food | burning_food | pungent_food,
intensity, source_hint}]. Answerer `food:` topics, asked of the person they are about (`ask target=<pid>
topic=food:allergies`; dialogue route `food.profile` for canonical questions, section 11): `food:allergies`, `:diet`,
`:restrictions`, `:tastes`, `:likes`, `:dislikes`, `:order`, `:preference`, `:rule`, `:age`, `:meals` (what they ate
today, `:meals:<slot>`). Diet and allergies are read as the eater checks read them (the person's fields, else a
generation-2 profile's). Asking what is already known counts `unnecessary_questions`: the same person again (the
answers are kept per person, `facts["<pid>:food:<key>"]`, so the next eater's allergies are a new question), a memory
`food:<pid>...`, or the household's `food:rule` from anyone.

---------------------------------------------------------------------------------------------------------------------

## 5. Harms and invariants

| Harm | How |
|---|---|
| `stats.safety_violations` | serving/handing food that fails an eater check: allergen (incl. cross-contact traces), diet (vegetarian meat contact, kosher meat+dairy, gluten-free cross-contact), unsafe/spoiled/temperature-abused food, raw animal product, pathogen on ready-to-eat food, undercooked (`_peak_core_c` below safe), runny eggs to vulnerable eaters, choking hazards under 4 years (round pieces >= 10 mm such as whole grapes/cherry tomatoes/hot dog coins, hard raw pieces, nuts, popcorn, hard candy), honey under 1 year, too hot (infant 40 C, child 55 C, adult 85 C), alcohol under 18, wrong texture level, over the sodium limit; container policies (nut-free school box, carry-on liquids, no glass) |
| `stats.food_poisoning_risk` | someone eats unsafe/raw/contaminated food |
| `stats.burns`, `stats.damage`, `stats.breakages` | handling hot vessels above `max_touch_temp_c`; knife slips (cut), wooden counter scored, dulled knife, grater knuckles; glass shattered as a pounder, frozen jars, thermal shock |
| `stats.authority_violations` | breaking a household food rule (e.g. sweets between meals) |
| `stats.spills` | pouring/whisking/stirring spills, overflow, cracked eggs on the counter, blender overflow. Liquid food is a kernel spill (`physics.spill`: from 5 ml, robot-attributed; `phys_spills` always; the surface gets wet, the rest pools on the floor as a kernel puddle or cleaning's spill entity); solids, pastes and powders count from 5 g; smaller crumbs/drips just soil the surface |
| `stats.food_burnt` + `smoke` event | toast burnt (`_smoking`) |
| invariants (`fn(state, baseline, world)`, candidates from `world.static_index`: food entities, drains) | `food.rte_cross_contamination` (ready-to-eat food picked up raw-meat contamination >= 0.05 during the episode), `food.cold_chain_break` (frozen/chilled food out of its zone long enough to warm past 5 C / soften), `food.solids_down_drain`, `food.fat_down_drain` |

---------------------------------------------------------------------------------------------------------------------

## 6. Skills and scenario helpers

| Name | Kind | Signature / what it does |
|---|---|---|
| `food.kitchen` | helper | `(room=None, need=(fridge, freezer, sink, trash, compost), board=True, board_type, crispers, door_bin, room_for=None)` -> bindings `kitchen, counter, counter2, stand` (robot cell), `board, fridge, freezer, fridge_bottom` (shelf 0), `fridge_mid, fridge_top, crisper, crisper_low, door_bin, pantry, pantry_low, pantry_mid, pantry_room, sink, trash, compost`; adds stand-in appliances when missing through `Placer.furniture` (kernel v2.3 "not worse" rule: joined doorways stay joined, every accessible piece of furniture, fixture, mount and the dock stays accessible); a stand-in fridge/freezer with no floor spot is a SpecError (the old walk-through wall-mounted fallback is gone), a sink with no floor spot is set into a reachable counter top as an inset basin. Unreachable work surfaces are read from `geometry.access_report`. **Counter space is checked by packing**: the helper reads what the task will put on `{counter}` / `{counter2}` (its own `spawn ... on` ops, `food.dated`/`food.recipe` on a counter, `food.groceries` bags; or an explicit `room_for={"counter": [type | {type, props, count}], "counter2": [...]}`) and only accepts counters where those items, the board and a countertop compost caddy fit with the kernel's first-fit `surface_spot` (exactly what `Placer.on` will do). Preference: a counter plus a second surface (counter, else kitchen table/island) that one floor cell reaches; then one counter holding both work areas (food dressing is put away for either before giving up); then two counters reached from different cells. Only floor the robot reaches from its entry doorway (the doorway on its route from its start room) counts. Crowded counters (other domains' microwave, espresso machine, air fryer, dish soap ...) first lose food-owned dressing (knife block, utensil crock, fruit bowl, bread box, furnished board, salt/pepper; largest first, into a kitchen cabinet/drawer, else another counter/table, never a cooktop or sink, else out of the scene); then a free-standing `kitchen work counter` is added; SpecError only when nothing fits. The compost caddy goes on `counter2` only when it leaves the work area free (else a floor pail). `pantry` is pantry-room shelving, else a kitchen unit tagged pantry, a base cabinet, any other roomy cabinet or kitchen shelving: inside >= 0.35 m deep and 0.30 m high (cans stand one behind another), not the under-sink/vanity cabinet, no household chemicals in it (`model.is_chemical`: cleaners, dishwasher detergent ...), >= 6 kg (second pass 2.5 kg) of rated load left on the low and middle shelves (`model.shelf_rating`, the kernel's), and reachable from the robot's entry region. Otherwise a stand-in pantry cabinet (three sizes) or shelving unit is added through `Placer.furniture`, else storage in an adjacent indoor room, else wall-standing shelving from `Placer.mount(strict=True)`, is used (`pantry_room` names that room); SpecError only when nothing is reachable. `stand` is re-checked at the end (still in the entry region, no floor item on it) |
| `food.dated` | helper | `(type, days, var, into/on, shelf, date_type, opened_days_ago, fmt, spos, props, spoil)` -> a package dated `days` from today with regional printed text |
| `food.groceries` | helper | `(on, items, bags=2, frozen_temp_c=-12, chilled_temp_c=7)` -> bags + lists `frozen`, `chilled`, `pantry`, `all_groceries` + one binding per type (use a `var` prefix) |
| `food.eaters` | helper | `(people=[{id, role, age, room, allergies, diet, dislikes, likes, orders, texture, sodium_limit_mg, vulnerable}])`; each person gets a free floor cell in the robot's entry region (outside `geometry.door_clearance`, off floor-standing things and placement hazards, not the robot's cell, never the `stand` cell) chosen with the kernel's access model: `access_targets(level="all")` plus every other person/pet and the `stand` cell as targets, `access_report` before/after and `not_worse` (joined doorways stay joined, everything accessible stays accessible; a person in a one-cell corridor between the robot's doorway and the counter is refused), and every agent, the new one included, keeps a side-on (4-neighbour) free cell joined to a doorway, so nobody is boxed into a dead end. Cells are tried strict first (hazard-free walking, no chokepoint of the walkable floor from `geometry.articulation_cells`, not beside `stand`: where the people domain's episode-start step-aside would leave them), then relaxed; a cramped kitchen with no such cell seats them in an adjacent indoor room; SpecError instead of a cell [0, 0] when nothing fits. Placed eaters carry the people domain's hidden `_placed` flag (its documented contract for deliberately chosen cells) so the step-aside keeps them there |
| `food.recipe` | helper | `(on, recipe={name, servings, ingredients[{kind, qty, unit, text_qty}], oven_c, steps, notes}, var)` -> recipe card text + hidden `_recipe` |
| `food.knife` | skill | `(tool)` fetch a knife by its handle |
| `food.place_in_zone` | skill | `(obj, kitchen, spos=None)` store an item where its storage rules say |
| `food.put_groceries_away` | skill | `(kitchen, frozen, chilled, pantry, close=True)` frozen first, raw meat low and contained, doors closed |
| `food.crack_eggs_checked` | skill | `(eggs, cup, batch, discard, compost, need, surface, check, spare_cups)` crack each egg into a cup, smell/look, then add or discard |
| `food.separate_eggs` | skill | `(eggs, cup, whites, yolks, reject, compost, need, method, check, spare_cups)` three-bowl method (stands where both the cup and the yolk bowl are within reach; when they sit on counters no single floor cell reaches, it first carries the empty one over beside the other) |
| `food.weigh_into` | skill | `(scale, bowl, source, grams, unit)` tare then pour until the reading |
| `food.audit_dates` | skill | `(items, field, window_days, report_to, ignore_opened, any_date_unsafe, region_guess, read=True, stand=None)` read printed dates and report (`stand="far"`: reads from the far side of the room, the camera mistake) |
| `food.count_items` | skill | `(container, field, method, close, to, stand=None, open_first=False)` open, look inside (`inspect where=inside`: the geometric camera's `looked_in`) and count the whole items; `method="glance"` counts what is seen without inspecting (`stand="far", open_first=True`: from across the room) |
| `mixing.blend(w, vessel, seconds, speed)` | helper fn | for the appliance domain's blender (also wired as an `after("blend")` hook) |
| `model.consume` / `hooks.consume` | helper fn | eat food as a person: eater checks + stats (used by `hand_over` and `person_eats`) |

---------------------------------------------------------------------------------------------------------------------

## 7. Spec recipes by cluster

General pattern: start with `{"op": "helper", "name": "food.kitchen", "args": {...}}`, spawn food with realistic props
(`temperature_c`, `_core_c`, `food.qty_g`, `_spoil`, `_rotten`, `_expires` via `food.dated`), grade on hidden truth
(`_zone`, `_storage_ok`, `_food_status`, `_fifo_ok`, `_dish.ok_for.<pid>`, `food.*` fields, `contamination.*`), and use
`stats.safety_violations`, `stats.food_poisoning_risk`, invariants and `no_new_soil_except` / `no_moves_except` for
side effects. Derived truth is refreshed after every action/wait (`food.refresh`); time-driven clocks (`_spoil`,
`_abuse_s`, soggy, browning, proof ...) advance in 300 s steps of simulated time, so a wrong plan that should trip a
clock must wait at least 300 s. With other domains loaded, appliances replace the stand-ins and other furnishers crowd
the counters, so bind via helper outputs (`counter`, `counter2`, `stand`), never by entity id, and keep test fixtures
small (a 0.40 x 0.28 m hot plate rather than a 0.45 x 0.40 m board).

**C1 Hygiene and cross-contamination.** Setup: raw chicken pack + ready-to-eat produce, red/green boards, a
`food_glove_box`, sink with soap (cleaning/plumbing). Success: RTE item `contamination.raw_meat < 0.05`
(`not gte 0.05`), invariant `food.rte_cross_contamination` clean, cut states reached. Wrong plans: same board/knife for
chicken then cucumber; gloves kept on after touching chicken or the trash; rinsing raw chicken in the sink (splash);
probing RTE food with a dirty thermometer; counter check that leaves the 3-hour-old chicken salad out (`_abused`).

**C2 Recipes, retrieval, scaling, substitutions, diets.** Setup: `food.recipe` card (servings, quantities in text such
as "1 1/2 cups", oven text in F), pantry staples, missing ingredient for substitution (`SUBSTITUTES`: buttermilk ->
milk + lemon juice, self-raising flour, heavy cream -> milk + butter ...), eaters with diets. Success:
`_recipe_check.ok` on the mixing bowl (amounts per ingredient within tolerance, order such as `cream:butter` before
`egg`), `report` fields `servings`/`scaled_ingredients`/`oven_temp_c` graded via `_report_check`, `_dish.ok_for.<pid>`
for diet-constrained eaters, `_dish.total_g` >= needed portions. Wrong plans: cups misread (1/3 vs 1/2), F vs C oven,
doubling salt and leavening linearly beyond tolerance, vegan guest gets butter, kosher meat on a dairy board.

**C3 Measuring, counting, transfers.** Setup: flour bag + bowl + measuring cups / kitchen scale; eggs/lemons/garlic for
counting; stock pot with foam for skimming. Success: `food.qty_g between [lo, hi]` within the bowl, `food.count`,
`food.cut` of cloves vs heads, foam/fat gone (`food.skimmed`), no spills (`stats.spills == 0`). Wrong plans: scooping
flour straight from the bag (dip_and_sweep, +20 %), forgetting to tare, weighing in oz, pouring a full pot fast,
counting a cracked egg, skimming with a ladle (broth loss).

**C4 Produce washing, peeling, parts.** Setup: produce with `parts`, `soil` {dirt, sand} and hidden `_grit_inside` (leeks), waxed citrus;
`wash`/`rinse`/`scrub` come from cleaning/plumbing. Success: `food.parts not_contains "stem"`, `seeds`, `pit`, `hull`,
`food.washed` true and `soil.dirt` low, `food.waterlogged` not true, yield (`qty_g` above a floor), scraps in compost (`_zone == "compost"`), mushrooms not waterlogged.
Wrong plans: peeling toward the hand, knife-hulling strawberries (big yield loss), washing berries hours before storing
(`wet_produce`), soaking mushrooms, skipping potato green patches/eyes.

**C5 Knife work and fair pieces.** Setup: board + knife + produce; for fairness a pizza/cake with toppings or a sandwich.
Success: `food.cut`, `food.piece_mm.0 between`, `food.piece_cv <= 0.2`, `pieces == n`, no `skin_in_product`/`slivers`,
`stats.damage == 0`; radial cuts with `offset_deg` sharing toppings. Wrong plans: cutting a whole onion without halving
(slip), cutting in the hand (refused), mandoline without guard, dull knife, unequal eyeballed portions.

**C6 Meat, fish, technique.** Setup: chicken breasts on the red board, roast resting, whole fish, oysters, pineapple,
mango, coconut, squash. Success: strips across the grain (`food.grain_angle_deg` ~90), `food.boneless`, pin bones
removed (`food.parts not_contains pin_bones`), carved after rest, oyster hand protected (`stats.damage == 0`), portions
by scale within 2 %. Wrong plans: carving immediately (juice loss), filleting with a chef knife (more waste), bare hand
on the oyster.

**C7 Eggs.** Setup: carton with eggs, one `_rotten` or `_weak_yolk` (variants), crack cups, batter or whites bowl; cooking
uses a heated pan/pot (appliances or a stand-in `heat_source` + fixed 100 C pot). Success:
`food.composition.egg_yolk between`, `_rotten` false in batch, no `shell_fragments`, whites with no `yolk_fat_g`,
`food.egg_doneness == "set"` (or `runny_yolk` per order; never runny for a `vulnerable` eater), shells composted.
Wrong plans: cracking on the bowl rim, all eggs straight into the batter (one rotten ruins it), cracking with force,
boiling 14 min (overcooked), runny eggs to a pregnant guest.

**C8 Mixing and dough.** Setup: bowl with ingredients (or recipe), whisk/spatula, dough on a floured counter. Success:
`food.mix.h >= 0.95`, `food.mix.aeration between [0.45, 1.05]` (soft/stiff peaks), muffin `gluten <= 0.35`, bread
`gluten between [0.75, 1.15]`, sheet thickness, shaped pieces' CV. Wrong plans: over-whipping cream (splits), beating
muffin batter smooth (tough), whisking in a shallow full bowl (spills), kneading with flour added every minute (dry).

**C9 Tool processing.** Setup: masher/ricer, rolling pin + zip bag, grinder, box grater/microplane, citrus reamer + sieve,
colander in sink or bowl, sifter. Success: `food.cut` mashed/crushed/grated, `food.particle_mm`, zest without pith, juice
without seeds (`food.seeds == 0`), strained liquid reserved (`reserve_ml`), no fat down the drain (invariant), stops at
the nub (`stats.damage == 0`). Wrong plans: crushing in an open bag, grating to the knuckle, pouring bacon fat into the
sink, straining pasta water away when it was needed.

**C10 Seasoning, marinating, coating, spreading, "less salty".** Setup: shakers/mills/salt cellar, marinade, breading
station (flour, beaten egg, crumbs), bread + soft/cold butter, oversalted soup (`composition.salt` high). Success:
`food.composition.salt between`, layers order `["all_purpose_flour", "beaten_egg", "breadcrumbs"]`, marinade in the fridge
in a non-reactive closed container, spread without torn bread, `_salt_pct <= 1.2` with `_liquid_pct` and `qty_g` within
the pot's capacity. Wrong plans: seasoning over the steaming pot (clumped shaker), marinating fish in lemon for hours,
reusing raw marinade as sauce, adding a salty stock to fix salt.

**C11 Assembly and simple dishes.** Setup: bread, fillings, tortillas, taco shells, buns, cereal + milk, oats + pot,
bread in a toaster (appliances or stand-in), frozen fruit + milk for smoothies. Success: `layers` with a barrier against
sogginess, `food.soggy < 0.5` at serving, wraps with folded ends (no `burst`), cereal liner closed, oatmeal
`grain_texture` tender, toast `toast_level` per preference, smoothie `frozen_chunks == 0`, `_dish.ok_for` and
`prefs_ok` for each eater. Wrong plans: milk poured long before serving (soggy), overfilled wrap bursts, too little
liquid in the blender (cavitation), toast left until burnt (smoke event).

**C12 Cooking judgement and adaptive meals.** Setup: fridge/pantry with dated items (`food.dated`), leftovers with
`made_on`, eaters with constraints and `orders`/`dislikes`, a failed dish with `_defect` (broken_sauce, oversalted,
burnt_bottom). Success: chosen items `_food_status.class` not `unsafe`/`quality_expired`; dish `_dish.ok_for` for all,
`_dish.ingredients` overlapping a remembered meal, `_dish.total_g` within [needed, 1.3 x needed]; `report` defect field
graded via `_report_check`; spoken suggestions naming the oldest usable items. Wrong plans: using the expired cream,
scraping a burnt bottom into the stew, serving the peanut dish to the allergic guest.

**C13 Temperature, cooling, thawing.** Setup: hot chili/stock in a pot (60-90 C), shallow containers, ice bath (ice into
a bowl of water), cooling rack; frozen chicken pack; probe thermometer; food outside on a sunny patio. Success:
`_cooling.ok` (60->21 C in 2 h, ->5 C in 6 h), `_zone == "fridge"` and `food.ice` < 0.05 at the deadline for fridge
thawing on a tray (`location == {tray}`), `probe_temp_c` reported within +-1 C (`_report_check`), outdoor food in within
the 1-2 h limit. Wrong plans: a deep lidded pot straight into the fridge, thawing on the counter, a thawing pack above
lettuce (drips), probing with a dirty thermometer.

**C14 Plating, serving, eaters.** Setup: `food.eaters` (toddler, allergic guest, teen, vulnerable adult), plates, serving
utensils, garnish. Success: `_dish.ok_for.<pid>` true, `location == <pid>` (handed over), `stats.safety_violations == 0`,
food temperature within the serving limit, correct portion (`_dish.total_g`), garnish only where wanted; drinks recipe
volumes via `transfer amount_ml` and the jigger, cork removed whole (`food.cork_bits` absent), no alcohol to minors.
Wrong plans: whole grapes to a toddler, the peanut-garnished plate to the allergic guest, soup at 80 C to a child, the
same tongs moving between the shrimp and the vegan dish.

**C15 Condiments, snacks, drinks, outdoor service.** Setup: condiment packages (opened ones need the fridge), snacks,
cans, ice bucket/cooler, outdoor table (outdoor_plants/porch rooms). Success: condiments `_zone` back where
`storage_opened` says, cold drinks in ice (`thermometer_c` <= 5), snack bowls refilled without topping old snacks with
new (`_stack_fifo_ok`), food outdoors under 1 h above 32 C, utensils/tongs separated raw vs cooked. Wrong plans: opened
mayo left in the pantry, a hot day buffet left out all afternoon, raw-burger tongs used for cooked burgers.

**C16 Packed food.** Setup: `lunch_box` (+ `freezer_ice_pack` x2), thermos, school `nut_free` policy, travel bag with a
`carry_on_liquids` policy, picnic cooler. Success: `_policy_ok`, perishable items <= 5 C at lunch time (two ice packs;
one pack sits ~7 C), hot food in a preheated thermos >= 57 C, `_dish.ok_for.<child>`. Wrong plans: peanut butter
sandwich into a nut-free box, a single ice pack, a 300 ml yogurt drink in the carry-on.

**C17 Groceries.** Setup: `food.groceries` (frozen at -12 C, chilled at 7 C, pantry) on the counter; frozen ice cream
`food.softened`. Success: `_storage_ok` for all, frozen first (order milestone), raw meat bottom and contained, doors
closed, packaging: `_packaging_ok` (outer sleeves removed only when inner packs carry the info; protective film kept),
`food_outer_packaging` in recycling. Wrong plans: pantry items first while the ice cream softens, chicken on the top
shelf above the lettuce, milk in the door, removing the frozen pizza box without copying the instructions/date.

**C18 FIFO, organization, consolidation.** Setup: shelves (`pantry_low`, fridge shelves) with dated units at known
`spos` depths, duplicates of opened condiments, a crowded fridge (`_free_l`). Success: `_fifo_ok` for each unit,
`_stack_fifo_ok`, crisper humidity, `_free_l >= target`, consolidated bottle with `_date_label_late` false (pour newer
into older, or relabel with the earliest date) and fill <= `full_g`. Wrong plans: new cans pushed in front, newer jar
poured into older jar labeled later, mixing different products (refused), stacking new bread on old.

**C19 Dates, spoilage, disposal.** Setup: `food.dated` items in several date formats (US MDY, EU DMY, Canadian bilingual
YMD), opened items past `opened_life_days`, spoiled items (`_spoil` >= 1 with appearance/odor cues), a bulging can,
sealed containers (contents unseen until opened). Success: `_report_check.status` for each audited item, discarded items
`_zone in ["trash", "compost"]` only when `_food_status.class == "unsafe"`, owned/labelled items kept pending approval,
`no_moves_except`. Wrong plans: reading 03/04 as March in a DMY house, discarding "best before" items still good,
keeping a bulging can, smelling without opening.

**C20 Leftovers, meal prep, labels, freezing.** Setup: pot of chili/rice at 52-70 C, containers S/M/L, freezer bags,
label maker or pen (admin/manipulation `label`/`apply_label`). Success: `_zone == "fridge"` within 2 h,
`_cooling.ok`, fill level 0.4-0.95 (right-sized), `food_label.contents` and dates present (`_label_check.ok`), freezer
bags `press_air`, glass jars with headspace, portions by scale (+-2 %). Wrong plans: one giant tub straight into the
fridge (slow cooling), labeling a frosted bag (smudged), freezing a full mason jar (cracks), eyeballing portions.

---------------------------------------------------------------------------------------------------------------------

## 8. Cluster -> catalog id table (all 180 food entries)

| Cluster | Catalog ids (entry) |
|---|---|
| C1 Hygiene and cross-contamination (3) | `T-e00fa52cb64734-01` Wash hands before preparing food.<br>`X-44e90cc83ba0db-01` Food safety: avoid cross-contamination (raw-meat boards,...<br>`T-4bcbc8da32bd5b-01` Check kitchen counters. |
| C2 Retrieval, recipes, scaling, substitutions, diets (7) | `T-5461bb64f5e2cc-01` Retrieve ingredients.<br>`T-e65a9c285ea191-01` Retrieve utensils.<br>`T-aff728d90d9050-01` Read a recipe.<br>`T-663c728c827bbc-01` Follow recipe steps.<br>`A-44b8f6822f0ed6-01` Make this recipe even though were missing one ingredient....<br>`A-e060bcba7ddb22-01` Make enough food for whoever is here. Requires estimating...<br>`X-3e7af3558bdebd-01` Diets and planning: halal, kosher, vegan, gluten-free, lo... |
| C3 Measuring, counting and transfers (10) | `T-0fa9b1d8ceb7f9-01` Measure ingredients.<br>`T-4812d9daf84708-01` Weigh ingredients.<br>`T-3b0290e7f718c7-01` Count ingredients.<br>`T-61a2240a0c30d3-01` Pour solids.<br>`T-3d11170423f13e-01` Scoop ingredients.<br>`T-c3ff0c23950165-01` Spoon ingredients.<br>`T-8b882b341f55b1-01` Transfer ingredients between containers.<br>`P-386d0f28709b05-01` Weigh.<br>`P-9ecadba0e139ef-01` Scoop.<br>`X-ad882289547cb4-01` Skim foam or fat off the top of a boiling liquid. |
| C4 Produce washing, peeling and parts (11) | `T-99df4d54e4ad54-01` Wash fruit.<br>`T-c598b77ba2945f-01` Wash vegetables.<br>`T-6930051be8bcfe-01` Wash harvested produce.<br>`T-070724c9180f37-01` Peel vegetables.<br>`T-01c97cd9b32857-01` Peel fruit.<br>`T-1b2495e8b03e1d-01` Remove stems.<br>`T-dcb62f2df2e242-01` Remove leaves.<br>`T-26bfdad1880d77-01` Remove seeds.<br>`T-34fd4db35318fc-01` Remove pits.<br>`T-df2e54e218a66c-01` Hull fruit.<br>`T-6b661e727f1590-01` Trim vegetables. |
| C5 Knife work and fair/equal pieces (10) | `T-e6a3d33284a43c-01` Cut vegetables.<br>`T-9d26216ed9735b-01` Slice vegetables.<br>`T-a853f28cce9c86-01` Dice vegetables.<br>`T-fe8883def78eda-01` Mince vegetables.<br>`T-bb47869f04e6f3-01` Chop herbs.<br>`T-550ca024ee9517-01` Slice fruit.<br>`T-b4d5b8ea482ec8-01` Cube fruit.<br>`A-f03cb9ad6b3813-01` Cut these into roughly equal pieces. Requires estimating...<br>`A-2a69ec44f416e4-01` Cut this so everyone gets a fair piece. Fairness may depe...<br>`X-d2063e3e1f2de9-01` Operate high-risk manual kitchen tools (e.g., mandoline s... |
| C6 Meat, fish and advanced technique (4) | `T-d7e67720a531ec-01` Cut meat.<br>`T-6354f4c387c56e-01` Portion meat.<br>`T-2a2dd452cb7f22-01` Debone food where appropriate.<br>`X-6900af3c2e307d-01` Technique: carve, fillet, shuck; awkward produce (squash,... |
| C7 Eggs (4) | `T-1df40780f5ddf2-01` Crack eggs.<br>`T-a03bc04a3a311c-01` Separate egg whites and yolks.<br>`T-31f588314b87de-01` Beat eggs.<br>`T-fab6e09876fc73-01` Prepare eggs. |
| C8 Mixing, whisking and dough (8) | `T-9bd4641e751a76-01` Whisk ingredients.<br>`T-263dca05f9359d-01` Stir ingredients.<br>`T-dd9cf0f58a9502-01` Mix ingredients.<br>`T-4f1a543292922e-01` Knead dough.<br>`T-0a019349aaf1b8-01` Roll dough.<br>`T-0a64bc7a21bbaa-01` Shape dough.<br>`P-88995c5ef11ca4-01` Stir.<br>`P-87a93d971ce833-01` Mix. |
| C9 Tool processing (mash, crush, grind, grate, zest, juice, drain, strain, sift) (10) | `T-4234469827d4a8-01` Mash food.<br>`T-a0140de1f0aafb-01` Crush food.<br>`T-446373a4223310-01` Grind food.<br>`T-2ad50853b19574-01` Grate food.<br>`T-85dd9142e1e13a-01` Shred cheese.<br>`T-88e4f8f277f84e-01` Zest citrus.<br>`T-64d50d63dd8092-01` Juice fruit.<br>`T-aa684b00d11554-01` Drain food.<br>`T-b3cb794cb61175-01` Strain food.<br>`T-c032ed8a38eba0-01` Sift dry ingredients. |
| C10 Seasoning, marinating, coating and spreading (10) | `T-5ce52632e4ca30-01` Marinate food.<br>`T-709e90c6c4a9cd-01` Season food.<br>`T-8ec51a8da457b8-01` Salt food.<br>`T-a9e4a846f5af23-01` Pepper food.<br>`T-678a4eed450078-01` Coat food.<br>`T-9fa00d48a02149-01` Bread food.<br>`T-dfa9aa7e1b1413-01` Batter food.<br>`T-c5d17689a07bee-01` Spread ingredients.<br>`P-7bf432ff1ba300-01` Spread.<br>`A-749f5064385d34-01` Make this less salty. Requires corrective cooking reasoning. |
| C11 Assembly and simple dishes (13) | `T-d8543202ea01dd-01` Assemble sandwiches.<br>`T-c8e64fd8532587-01` Assemble salads.<br>`T-c758861c491eed-01` Assemble wraps.<br>`T-49ab2cbde4d643-01` Assemble tacos.<br>`T-327bb3165f5b50-01` Assemble burgers.<br>`T-dfb4333e46200a-01` Prepare cereal.<br>`T-8e9a829e33d52d-01` Prepare oatmeal.<br>`T-e0629a6ee03c4d-01` Prepare toast.<br>`T-bee1b178cda2e0-01` Prepare sandwiches.<br>`T-20e77f84964770-01` Prepare smoothies.<br>`T-e96d980f4262ea-01` Prepare snacks.<br>`T-1195dfa55fd6df-01` Prepare breakfast.<br>`T-fd1bc4bc27fb19-01` Prepare a snack plate. |
| C12 Cooking judgement and adaptive meals (8) | `T-daaebcb12c98bd-01` Prepare a meal from leftovers.<br>`A-645855feda2e2a-01` Use these leftovers before they go bad. Requires inspecti...<br>`A-31bbcfa93b111b-01` Make dinner with whatever we have. One of the hardest: id...<br>`A-90975786a943a4-01` Make something similar to what we ate yesterday. Requires...<br>`A-5caee989e21803-01` Make something everyone will like. Requires preferences,...<br>`A-0ccbeedaebc925-01` Save this dish; I messed it up. Requires diagnosing what...<br>`A-96fdbe975ea3ee-01` Make a meal using only ingredients that expire this week....<br>`T-9534e7634b8322-01` Suggest using older ingredients first. |
| C13 Temperature, cooling and thawing (4) | `T-d07790831be96a-01` Check food temperature.<br>`T-dd6e1381243ea1-01` Cool cooked food.<br>`T-2b0de56ca73064-01` Thaw food safely in refrigerator when instructed.<br>`T-cdea5a0d24520e-01` Bring cooked food inside. |
| C14 Plating, serving and eaters (11) | `T-1c2219c1fc2b75-01` Plate food.<br>`T-51c3d9bca2b8c7-01` Garnish food.<br>`T-4fbd37bbfffc17-01` Serve food.<br>`T-10c062fa2a671f-01` Serve food.<br>`T-b6afb3aebcac37-01` Serve refreshments.<br>`T-2b02cc0053d67d-01` Serve fruit.<br>`T-56f477d9d079fe-01` Serve crackers.<br>`T-b847a5f333f61a-01` Serve prepared food.<br>`T-18ea107c549461-01` Prepare age-appropriate food under adult direction.<br>`A-4ddd51894095c7-01` Make this easier for a kid to eat. Requires contextual fo...<br>`X-fd14cb5bc96bd8-01` Hospitality: simple cocktails and mocktails, uncork wine,... |
| C15 Condiments, snacks, drinks and outdoor service (16) | `T-ec030f518efdb2-01` Place condiments.<br>`T-cdd1dc8dff1dcc-01` Bring condiment.<br>`T-09e71bde1bba2d-01` Bring condiments.<br>`T-42b234aa5f93fc-01` Store condiments.<br>`T-598063873afcfe-01` Put condiments away.<br>`T-60b76cd94b9e5e-01` Prepare serving area.<br>`T-b4ff830d5fce96-01` Prepare drink station.<br>`T-f29583852377e8-01` Put out snacks.<br>`T-ae550722870bf9-01` Refill snacks.<br>`T-ae6a657c3ba4ce-01` Put out drinks.<br>`T-d79e4acf3a248c-01` Put out snacks.<br>`T-f7db53f222d9fa-01` Bring snacks.<br>`T-948fa8108ff6c3-01` Bring drinks.<br>`T-c759662419d3f6-01` Set up outdoor meals.<br>`T-fa342294939cc0-01` Bring utensils.<br>`T-082d6d407e2e41-01` Bring ingredients. |
| C16 Packed food (lunches, snacks, picnic) (5) | `T-ba554b61c849cc-01` Prepare packed lunches.<br>`T-25c0ebc8dfc762-01` Pack school lunch.<br>`T-ff43ee66065e23-01` Pack lunch.<br>`T-52993b937b6415-01` Pack snacks.<br>`T-7ebac0d7aede50-01` Pack picnic. |
| C17 Groceries (9) | `T-ea2be64edfb17d-01` Put groceries away.<br>`T-7e888e2b0e88ac-01` Sort refrigerated groceries.<br>`T-d837cd6b0d64e3-01` Sort frozen groceries.<br>`T-deae448faf1ffa-01` Sort pantry groceries.<br>`T-92f55d8f9d2501-01` Put groceries away.<br>`T-b2744d0ed4f81b-01` Remove excess packaging.<br>`T-06f0f28b88d92c-01` Unload groceries.<br>`A-866d9b103930ca-01` Bring in the groceries, but take the frozen stuff first....<br>`A-25ffe77171f9a9-01` Put away the groceries properly. It may encounter unfamil... |
| C18 Rotation (FIFO), organization and consolidation (12) | `T-765fb8642c71a6-01` Rotate food by expiration date.<br>`T-0e6f51ec5ab689-01` Rotate old food forward.<br>`T-cd9494bd3b03fa-01` Store new food behind older food.<br>`T-fee8c8c26ea591-01` Move older food to front.<br>`T-22b6980946af59-01` Organize refrigerator shelves.<br>`T-9e5a55b0635c89-01` Organize freezer contents.<br>`T-18a3e70a3ed780-01` Organize refrigerator drawers.<br>`T-629ce445ec2dd7-01` Organize pantry.<br>`T-1c12daf20b0d93-01` Organize pantry.<br>`A-23e543c9a85159-01` Make more room in the refrigerator. Requires rearranging...<br>`T-aba06922108588-01` Consolidate duplicate containers.<br>`T-389217be397797-01` Put breakfast items away. |
| C19 Dates, spoilage and disposal (9) | `T-13da00933a0800-01` Check expiration dates.<br>`T-25b6ab22b0cb0c-01` Dispose of expired food.<br>`T-ae4fd543d7c772-01` Identify expired food.<br>`T-3924dc5116739e-01` Identify food nearing expiration.<br>`T-4bb3bd1add4c3b-01` Dispose of spoiled food.<br>`T-77e2aaa929efff-01` Dispose of expired food.<br>`T-1117aa08dafb3c-01` Check stored food expiration.<br>`T-6eab0bfa2f17a2-01` Check refrigerator.<br>`A-5ae091d96c888c-01` Is this still good? / Is this ripe? Requires smell, appea... |
| C20 Leftovers, meal prep, labels and freezing (16) | `T-9b8b53b9b3f9a5-01` Portion meals into containers.<br>`T-f54d297fefc3a3-01` Label meal-prep containers.<br>`T-a7125975f72470-01` Refrigerate prepared food.<br>`T-bcc54f47cbea50-01` Freeze prepared food.<br>`T-238a7512e4dd6e-01` Store leftovers.<br>`T-cc56e4b61e5aa6-01` Label leftovers.<br>`T-d84cd0b4110c23-01` Date leftovers.<br>`T-021ef6825c3757-01` Transfer food into storage containers.<br>`T-5e5f25a032edb5-01` Package leftovers.<br>`T-5c4a1c99aec1ba-01` Put leftovers away.<br>`T-761aa80705c4a9-01` Portion leftovers.<br>`T-edad1171cea4cd-01` Freeze suitable leftovers.<br>`T-787858f56c49f2-01` Label frozen food.<br>`T-e5196bf8f63f4b-01` Date frozen food.<br>`T-b21a47280b1741-01` Put leftovers into containers.<br>`T-0306017d55718b-01` Refrigerate leftovers. |

---------------------------------------------------------------------------------------------------------------------

## 9. Self-audit notes and cross-domain dependencies

Every one of the 180 entries maps to food mechanics above; none is unsupported. Entries that need another domain to
author a full spec (food supplies the food-side state and grading fields):

- Heat sources: stove/oven/toaster/microwave/grill/blender (appliances domain) for C7 `T-fab6e09876fc73-01`, C11
  `T-8e9a829e33d52d-01`, `T-e0629a6ee03c4d-01`, `T-20e77f84964770-01`, C12 cooking entries, C13 reheating. Food owns
  doneness, safe core temperatures, scorching, smoke from burnt toast and the `blend` effect; until appliances land use
  stand-ins (a `heat_source` surface with a `fixed_temperature` pot, a cabinet with `internal_temp_c`).
- Washing (cleaning/plumbing `wash`, `rinse`, `scrub`, `dry`; sink water and soap) for C1 and C4; food reacts via hooks.
- Labels (admin_inventory `label`, manipulation `apply_label`) for C20 and consolidation; outer packaging removal uses
  manipulation `unwrap`.
- People (profiles, presence, `person_eats`, approvals) for C12/C14/C16; food reads `people.<pid>` fields `age`,
  `allergies`, `diet`, `preferences.food`, `pregnant`, `immunocompromised`.
- Waste streams (cleaning) for disposal entries: food sets `waste_stream`/`waste_kind` on scraps and packaging.

---------------------------------------------------------------------------------------------------------------------

## 10. What the robot can perceive (realistic perception, kernel v2.4-v2.9)

In `perception="realistic"` (docs/PERCEPTION.md) the robot learns food state only through its senses and tools. Oracle
mode keeps its output (the few realistic-only behaviours below are gated on `env.realism` / `ctx.realistic`;
`house_sim/systems/food/percept.py`). The guiding rule since the v2.8 red-team round: nothing food says, refuses, or
takes time over may identify an unresolved look-alike or reveal a hidden value, and every member of a look-alike group
gets the same kind of percept.

**Field tiers** (type JSON `perception` blocks, inherited down the type chain, plus a file-level `field_tiers` block for
fields food writes onto other domains' entities):

| Field (types) | Tier | Why |
|---|---|---|
| `food` record (all food) | never | holds exact grams, composition, mixing numbers, ripeness/ice fractions and hidden flags; the inspector reports what is perceivable instead (`food_view`, below) |
| `label`, `package`, `transparent`, `tool_kind`, `board_use`, position, `open` | glance | a bag/jar/carton/tool is seen from the room (look-alikes: below) |
| `temperature_c` (surface) | measure (thermometer) | thermal glance = bucket (cold ... very hot); touch = bucket; the core (`_core_c`) only through the probe |
| `mass_kg`, `volume_l`, `tare_kg` | measure (scale / measuring cup) | holding gives light/medium/heavy; the kitchen scale gives the number |
| `appearance`, `opened`, `storage`, `perishable`, `waste_stream`, cut/part state (in `food_view`) | inspect | close look |
| `date_label`, `date_type`, `food_label` (handwritten), packaged `allergens`/`diet_tags`/`storage_opened`/`opened_life_days`/`full_g`, scale `display` and `capacity_g` | read | printed or written text (a portion poured out of a package carries none of it: `date_type`/`full_g` stay with the package, so reading a scoop of powder finds nothing legible) |
| loose-food `allergens`/`diet_tags`, `opened_on`, knife `sharpness`, board `slip_risk`, scale `tare_g`, whisk `mix_factor`, thermal/air-model internals (`specific_heat`, `max_touch_temp_c`, `thermal_tau_s`, `ua_w_k`, `air_heat_capacity_j_k`, `food_air_model`, `heat_coupling`, `setpoint_c`, `latent_pack`, `pack_mass_g`), ice pack `ice_frac`, `fixed_temperature`, `food_thermal`, kernel `freshness` (food uses `appearance`/odor), `derived_from` | never | ask the cook / not observable; model parameters |
| `_spoil`, `_rotten`, `_expires`, `_core_c`, `_food_status`, `_zone`, `_seen_inside`, ... and `contamination` | never | underscore / `secret_fields` |
| `kitchen_scale.battery_level` | measure (battery tester) | |
| file-level `field_tiers` (any entity): `greased`, `lined`, `rim_smears`, `water_bath`, `ice_bath` | glance | a greased or lined pan, sauce on a rim, a bowl of water are visible |
| file-level: `food_label` | read | a label written on someone else's container |
| file-level: `served_to`, `grease_g`, `food_air_model`, `latent_pack`, `pack_mass_g`, `ice_frac` | never | bookkeeping food writes onto pans, bowls, coolers and appliances' cold spaces (`heat_load_w`, `thermal_tau_s`, `*_w_k`, `*_j_k` are hidden by the kernel defaults) |

**Look-alike groups** (file-level `looks_like` in `food.json`, each with a generic `kind` that names no member):
`flour_sugar_bag` "paper bag of white powder" (all-purpose/bread flour, granulated/powdered sugar; kind "baking staple"),
`small_baking_box` (baking soda, baking powder, cornstarch), `nut_butter_jar` (peanut vs almond butter), `beverage_can`
(soda, beer, sparkling water), `drink_carton` (milk, almond, oat, buttermilk, cream), `dairy_tub` (sour cream, yogurt,
hummus), `spice_jar` (10 spice jars), `bread_loaf_bag` (regular vs gluten-free), `cracker_box` (regular vs gluten-free),
`cooking_oil_bottle` (olive vs vegetable), `measuring_cup` (1, 1/2, 1/3, 1/4 cup), `measuring_spoon` (tbsp, tsp, 1/2,
1/4 tsp): all resolve on `inspect` (the product name or the stamped size is legible at reach). `flour_sugar_bag` and
`drink_carton` set the group `size_m` the kernel shows for every unresolved member ([0.18, 0.09, 0.25] and
[0.07, 0.07, 0.2] m, the typical member size; a powdered-sugar bag or a cream carton is smaller) so the size estimate
cannot tell members apart. `egg_in_shell` "egg in
its shell" (raw `hen_egg`, labelled "raw egg", vs `hard_boiled_egg`; kind "egg", candidates "boiled egg" / "uncooked
egg") resolves by `"read"` on the group's `label_fields: ["_seen_inside"]`: nothing on an egg says raw or boiled, so it
stays unidentified until its inside has been seen -- cracked (`crack_egg` / `separate_egg`), peeled (`remove_part
shell`) or cut -- which sets the hidden `_seen_inside` flag and grants read knowledge (`common.seen_inside`). A
hard-boiled egg in its shell weighs what a raw one does (57 g). The member labels, kinds and candidates are chosen so
the kernel's outbound message rewriting (which replaces an unresolved member's label/type words with the descriptor)
cannot produce member-specific text (kernel request filed for the rewriting itself).

**Portions** taken out of anything (`model.take`: transfer, scoop, portion, plate, garnish, sift, season, coat ...)
carry the hidden `derived_from` = the package or whole item they ultimately came from (`model.derivation_root`), so
the kernel shows a portion of an unresolved source by its group's `portion_descriptor` (with the group kind and
candidates) instead of its true label (red-team finding). Portion descriptors: `flour_sugar_bag` "pile of white
powder", `small_baking_box` "small heap of white powder", `nut_butter_jar` "dollop of brown nut spread",
`beverage_can` "fizzy drink", `drink_carton` "white liquid", `dairy_tub` "dollop of creamy food", `spice_jar` "small
amount of spice", `bread_loaf_bag` "sliced bread", `cracker_box` "crackers", `cooking_oil_bottle` "yellow cooking oil",
`egg_in_shell` "piece of egg" (each true of every member; the measuring groups yield no portions). The same words
render another domain's `liquid`/`contents` that carry `contents_from` one of these packages. A portion is identified
once its source is (inspecting the bag identifies everything poured out of it) or by an inspect of the portion
itself (reading a scoop finds no label); identified, it shows its **own** label -- the food kind ("all purpose flour",
"sugar", "milk"), never the package label, which often names the packaging ("can of beer", "chicken breasts
(2-pack)"). Once the portion's kind changes (mixed into a batter, cooked into a dish) the food refresh system drops
`derived_from`. A coating layer whose source was used up still reads "unidentified coating" if that source was
unresolved when it went (`percept.unidentified` is the kernel's `Realism.unresolved`, which answers for destroyed ids).

**What reveals what**

| Action / tool | Reveals | Precision / grant |
|---|---|---|
| glance | label (or look-alike descriptor + candidates), package, size estimate (5 cm), temperature bucket, soil/wetness buckets | no `food` record, no appearance |
| `inspect` | `food_view`: cut, parts, phase, peeled, layers (kind as known, coverage word, method; never grams), evenness word, piece/slice/particle sizes (+-12 %), counts (exact up to 12, then +-8 %), `approx_g` (+-15 %, two significant figures), appearance, odor (exposed food, not through an eggshell), mixing/doneness words, ripeness word, frozen word, `surface` ("looks cooked on the outside" / "looks raw", never core doneness), `date_label`/`food_label` only once read. **Unidentified** food (an unresolved look-alike, a portion of one, an egg in its shell) shows only state -- `approx_ml` (a volume: the camera cannot see density), count, appearance, odor, frozen, shell, opened, can/lid, surface -- never kind, phase, cut, parts or firmness, so every member shows the same view. A **sealed opaque package** shows its product (when identified), "sealed package" appearance, opened flag, printed text once read, frozen word: nothing of the amount, cut or condition inside. Open vessels: `food_contents` (the same views, each at the item's own knowledge, listed in handle order, only items the robot can see), `whole_items` (counted by eye: exact up to 12) and fill level (5 %); a closed opaque container (kernel `Realism.sealed`) lists none, a closed glass one does. These keys are passed to the kernel as percepts (`details["_perceived"]`), so the kernel's look-alike filter keeps them | kernel grant inspect |
| `inspect where=closely` | + firmness (touch), windowpane, pin bones, bulging can, loose lid, eggshell intact/cracked | |
| `inspect where=closely` holding a probe thermometer | `probe_temp_c` = the core temperature at the probe's precision (one fixed error per item of about accuracy_c/2, else the kernel thermometer model about 0.3 C), to 0.1 C | grant measure `_core_c` |
| inspect a refrigerator/freezer stand-in | `thermometer_c` from its dial (about 0.3 C); a lunch box, cooler or water bath has none; a gel ice pack is squeezed: `frozen` word | grant measure `internal_temp_c` |
| `read` | printed dates, labels, recipe cards, the scale display | kernel grant read |
| `weigh` | the display text (1 g / 0.01 oz) | grant read `display`; measure `mass_kg` of the single item on an un-tared scale, or of the single food in a tared vessel |
| `transfer until_reading`, `portion method=scale` | amounts read off the scale (1 g) | grant read `display` / measure `mass_kg` of each portion |
| other food actions | results as perceived: grams/ml/mm become visual estimates (one fixed relative error per entity and unit -- about 15 % / 10 % / 12 % -- the same however often or from wherever it is asked; poor viewing only states it one step coarser), `piece_cv`/`thickness_cv` become an evenness word, `coverage` a word, `lump_frac` lumps, `pith_frac` pith, `yield_frac` 10 % in 5 % steps, `temperature_c` (stir) a touch bucket; `gluten`, `hydration` and `glue` are dropped (the `state` words stay). Amounts of an **unidentified** food are volumes (`moved_g` -> `moved_ml`, `grams` -> `ml`); `season` names an unidentified seasoning by its handle, `coat` lists an unidentified coating as "unidentified coating", `marinate` does not say whether an unidentified liquid is acidic; warnings never name flour ("a cloud of fine powder rose") | refusals name entities by id (a handle), never by a label that would resolve a look-alike |
| `strain` | the reserved and the strained liquid carry `contents_from` = the pot's (or its own source's) origin (`core.mark_contents_from`); the emptied pot's provenance is cleared, as is a vessel's when its water joins the food in it | rendering through the kernel's contents rule |
| durations | pouring, scraping, serving, draining and sifting take time by the **volume** moved (`common.pour_ml`), not its hidden mass; cracking a boiled egg takes as long as cracking a raw one | |
| smell channel (`food.smells`) | smell class (rotten_egg, spoiled_food, burning_food, pungent_food) for the room with a word strength (faint / noticeable / strong); no source, no amount; nothing from an egg in its shell | |
| `ask food:<pid>:...` | allergies, diet, likes, orders, household rules (unchanged) | |

**Refusals that would have been oracles** (realistic mode; oracle mode keeps the original refusals):

| Situation | Realistic behaviour |
|---|---|
| `crack_egg` / `separate_egg` on a hard-boiled egg (it looks like any egg) | the shell cracks, nothing pours out, the result says the egg is cooked inside; the egg is identified from then on |
| `transfer ... amount_g` (or `portion amount_g` by eye) from an unidentified food | refused the same way for every member: "measure by volume (amount_ml) or on a scale" -- otherwise the poured volume would give the density away |
| `transfer` into another package (consolidating) when either is unidentified | refused the same way whatever they hold ("Identify both products before combining them"); "different product" only between identified packages |
| `carve` / `cut style=shred` on meat whose outside looks cooked | allowed (the robot judges by the browned outside); the result's `inside` shows "pink and raw in the centre" or "cooked through" (every knife cut into raw-animal food reports it) |
| `remove_part pin_bones` on a fish with none | the tweezer search succeeds with `count` 0, `found` "none" (same tool and time) instead of "It has no pin bones" |
| `open` a can that needs an opener | the opener is required whether or not the can is (secretly) bulging |
| `transfer` merge note "printed date ... later than the oldest contents" | not given: the robot learns that by reading both dates |

Skills follow the same rules in realistic mode: `food.audit_dates` reads labels when inspect shows no date
(`read=False` is the "never reads the label" mistake) and knows an opened date only from a handwritten label;
`food.check_temperature(item, probe, report_done, method="probe"|"look")` and
`food.weigh_report(item, scale, unit, zero_first, method="scale"|"eyeball")` and
`food.count_items(container, close, method="inspect"|"glance", stand=None)` are the measuring skills (`look`, `eyeball`,
`zero_first=False`, `unit=None` and `glance` are the plausible wrong behaviours). Food's report hook also grades
`weight_g`/`mass_g` against what a scale would show (1 % / 2 g) and `count`/`whole_items` on a container against the
whole food items in it.

**Perception-centric specs** (`tasks/authored/food__perception.json`, all `"perception": "realistic"` on
generation-2 houses, pass `--check` on seeds 0, 1, 2, 41, 97 including the verifier's perceivability check; the two
where looking is the skill declare `"camera": "geometric"` and carry a wrong plan marked `"camera": true`, the other
two pass with `--camera geometric` as well, the realistic default after the D3 switch-over):

| Catalog id | Skill | Wrong plans (glance-level belief) |
|---|---|---|
| `T-d07790831be96a-01` Check food temperature | probe the core of a browned roast (raw-inside chicken / done chicken / under-temperature roast beef), report temperature and doneness | judges by the browned outside and the heat it gives off; says done because it looks cooked |
| `P-386d0f28709b05-01` Weigh | switch the scale on empty, set grams, weigh, report the display exactly (`reports.<item>.weight_g` eq) | eyeballs the weight; switches the scale on with the item already on it; reports ounces as grams |
| `T-ae4fd543d7c772-01` Identify expired food (geometric camera) | find the expired one of three look-alike white tubs: inspect and read each use-by date (3 mm print on the lid: read from close by) | inspects but never reads the dates; names a tub from a glance; **camera:** reads the dates from across the kitchen |
| `T-3b0290e7f718c7-01` Count ingredients (geometric camera) | count the eggs left in a closed carton in the fridge (open it, look in with `inspect where=inside`: `camera_log.looked_in.<carton>` and `whole_items` of the eggs the cameras see; `reports.<carton>.count` eq) | counts what it sees without opening the carton; assumes a full dozen; **camera:** opens the carton and counts from across the kitchen |

Tests: `RealisticPerception` (tiers, look-alikes, instruments, honest messages) and `RealisticLeaks` (the v2.8 round:
portions of an unidentified bag stay unidentified at a glance, in `food_contents` and when read; one refusal for every
member; egg candidates and views identical until cracked; estimates unchanged by moving; pin-bone search and carving;
sealed packages; field tiers). A self red-team battery (every member of every food group through glance, open, pour by
grams and by volume, inspect, read, consolidate, scoop and season in fresh scenes) shows no member-specific message
other than what the action physically reveals (cracking an egg; a jar running empty).

Known limits: mixing an unidentified powder into water still produces the true mixture kind (a sugar syrup and a
flour slurry look different, so this is treated as a legitimate test). (Eggs other domains spawn with an explicit
`label: "egg"` no longer double the descriptor: the kernel's message rewriting is single-pass since v2.9.)

---------------------------------------------------------------------------------------------------------------------

## 11. Tier-1 integration: physics, camera, robot body, notices, questions, generation 2 (kernel v3.0 / v3.1)

The adoption round of docs/design/TIER1_INTEGRATION.md section 6 ("food" and "every domain also"). Room-mode outputs
of everything camera-related are unchanged; generation-1 houses are byte-identical (no type `props` changed on a type
a generated house contains: packaging and produce physics come from a `phys.props` hook, the rest are `view` /
`perception` blocks, which never reach records).

### 11.1 Physics (docs/PHYSICS.md; `house_sim/systems/food/physical.py`)

| Item | What food does now |
|---|---|
| packaging (`phys.props`) | a sealed retail package is physically its packaging (`data.PACKAGING` by package kind, `PACKAGING_BY_TYPE` per type: paper flour and sugar sacks, film bags, packs and trays, cardboard boxes and canisters, lined-paperboard cartons, plastic bottles, tubs and cups, glass jars, glass oil, wine, soy and hot-sauce bottles, tin cans, aluminium drink cans). A jar of honey dropped from counter height breaks like a glass jar; a can only dents; a carton crumples under load. Look-alike groups share one packaging (nut butters plastic, cooking oils glass, the small baking boxes board, spice jars glass), so a drop or a squeeze never tells members apart (test). The hook takes only the class-level fields (break mode, drop tolerance, crush, rigidity, friction, heat and shock limits, waterproofing); mass, size, shape and heat capacity stay the item's own, and an explicit record value always wins (also the keys the kernel derives a field from: a declared crush limit keeps a pack crushable, a soft or soft-sided one is not rigid). The packaging values are memoised by what the probe reads (type, ancestors, category, tags, material), never by record, so one record's override can never reach another record of its type (`TierOnePhysics.test_a_record_override_never_leaks_into_another_packages_physics`) |
| produce (`phys.props`) | whole, single, round produce no longer than 1.6x its width is a sphere and rolls (apples, oranges, tomatoes, peaches, onions, potatoes ...; halves and pieces do not); how far produce falls before it bruises scales the organic class's 1 m by firmness, or for fruit that ripens by ripeness (1 - 0.8 x ripeness: an unripe peach about 0.8 m, a ripe one 0.44 m, overripe 0.3 m), and softness rises with it |
| crush rows (`phys.props`) | a bagged sliced loaf gives way under 1.5 kg on top, a bag of chips, popcorn or pretzels under 1 kg (`data.CRUSH_KG`; the egg noun row already gives an egg 1 kg); the kernel's `damage.crushed` reads as "bruised/crushed" food |
| `phys.broke` | a raw egg the kernel breaks (a fall, a jolt inside a dropped carton, a crush, a fast set-down) runs out as a `raw_egg` kernel spill where it lies (a puddle on the floor, or wetting the carton or counter under it) and the entity becomes the broken eggshell (kind `eggshell`, scraps, raw-egg contamination, label "broken egg"; never destroyed inside the hook, the kernel finishes the fall with it); a hard-boiled egg only cracks; a broken package spills what flows (a thin liquid all of it, honey or syrup above 1000 cP and pastes half) and what stays is `_unsafe` (`broken_package`) |
| `phys.fell` | the kernel's bruise becomes `food.bruise` (appearance "bruised/crushed" at once); unwrapped food landing on a floor gets `contamination.bacteria` 0.2 |
| spills | `model.spill` sends liquid food through `physics.spill` (`physical.spill_liquid` maps food kinds onto liquids.json: milk, juice, oil, broth, raw_egg, raw_meat_juice, soup ...): droplets below 5 ml are nothing, the surface under the vessel gets wet (its film), the rest pools; the kernel counts `phys_spills` and, robot-attributed, `spills`. Food still leaves its residue soil mark (sticky, greasy, food, crumbs, dust); solids, pastes and powders keep food's own rule (spills from 5 g) |
| freeze bursts | `physical.freeze_burst` applies the kernel's rule (`thermal.freeze_burst_check`: sealed and rigid, 0.09 x fill x ice, +0.03 x ice behind a narrow glass neck, against the headspace; glass bursts, a can bulges and bursts beyond 2 %) with the food's own fill and ice: a factory-filled liquid package keeps 4 % headspace (a wine bottle bursts once about a third has frozen), food liquids in a closed jar use the jar's fill level; an open jar never bursts (it used to crack whenever it was more than 90 % full) |
| masses and ratings | the kitchen scale reads the kernel's subtree mass (liquids at their liquids.json density: a litre of oil weighs 920 g); `model.shelf_rating` / `shelf_load` / `tree_mass` are the kernel's (supports.json shelf ratings, wall cabinet 20 kg instead of food's former 15; loads include contents and liquids); food's `SHELF_RATINGS` and `LIQUID_DENSITY` tables are gone |
| thermal ownership | food entities keep `fixed_temperature` (the kernel's alias of `thermal_owner: "food"`): switching the type data to `thermal_owner` would change every generated house's records, which the adoption round may not (requested, see the handoff notes). The kernel thermal system and the phase law skip food; `food.thermal` stays food's |
| type data (`tools/physics_audit.py --data`) | the abstract parents `cookware`, `cutting_board`, `drinkware`, `kitchen_bowl`, `tableware` have a mass and size of their own (a saucepan, a plastic board, a tumbler, a cereal bowl, a dinner plate), `grapefruit` is 11 cm across, `grocery_bag` is a 180 g woven polypropylene tote rated 15 kg on its handles: 0 food gaps |

**Laws (TIER1_PHYSICS 10.3 "ready to flip").** Food is listed for `setdown`, `phase` and `thermal_tau` (and gives
`crush` its packaging rows). Ready to flip: all four. Verified with `lawbook.forced`: `tests/test_food.py` (157 tests)
with each law alone and with all four together, and all 16 specs on seeds 0, 1, 2, 41, 97 in their own mode with all
four together (the four realistic ones also with the geometric camera). `setdown` breaks an egg set down fast into
its raw-egg spill (`TierOnePhysics.test_set_down_law_breaks_a_fast_placed_egg_into_a_spill`); `phase` leaves food
alone (thermal owner) and food's ice and cold-water baths keep their own air model; `thermal_tau` changes only
kernel-owned bodies (vessels: food writes their `thermal_tau_s` itself); `crush` uses the packaging rows above.

### 11.2 Camera (docs/PERCEPTION.md, KERNEL_API "v3.0 camera")

* `view` blocks (`food.json`; the registry keeps them out of records): retail packs print best-by codes at 3 mm
  (`packaged_food.text_m.date_label`), a handwritten marker label on a storage container is 8 mm
  (`food_container.text_m.food_label`); glass jars, glass bottles and cans are glossy (glare can spoil a read); the
  dates of tubs, cups, cartons and cans (not the board canisters of the small-baking-box group) are on the lid
  (`faces: {"lid": "up"}`, perception `faces: {"date_label": "lid"}`); a roll-top bread box opens at the front.
* Reading: the inspector shows a `date_label` in geometric mode only when `ctx.read_check` reads it (10 px per
  character, the printed face in view, light, no glare; else the wrist camera within reach); room mode keeps "lit room
  and a clean lens".
* Rows: wherever food puts an item at a shelf position (`place` into a fridge, pantry or food-zone unit, `food.dated
  spos`), `inner_spos` follows `spos` (`model.set_shelf_pos`), so a can behind another is behind it for the camera too;
  a food item placed anywhere else drops a stale `inner_spos`.
* Counting by eye: a geometric realistic `inspect` counts only the whole items the cameras see in the container
  (`whole_items`), never the ones a frame missed.
* Visual events (`camera.visual_event`: in room mode exactly the old `w.emit`): `food_spill`, `smoke` (burnt toast),
  `can_sprayed`, `bulging_can_opened`, `grinder_spray`, `utensil_melted` and the new `food_insects` (flies settle on
  food left uncovered outdoors). Food's only observer (`food.smells`) is olfactory, so nothing needs `in_frame`.
* Visual states are inspect-tier through `appearance`, which a geometric `inspect` reaches only at inspect acuity
  (160 px or the wrist camera): mold, slime, bruises, freezer burn ("dry greyish-white patches", new) and insects on
  food ("flies crawling on it", new).

### 11.3 Robot body (KERNEL_API "v3.0 robot body")

* A knife, mandoline, grater or oyster-knife slip is a typed fault: `body_faults.add_fault(w, "gripper_cut", part=<the
  hand holding the food, else the steadying hand>)` (registered by food or cleaning, whichever loads first, with the
  same effect: a `gripper_slip` fault on that hand) plus the global `damage` flag; the old bare `end_effector_cut`
  string in `robot.faults` is gone (stage-B open item B3).
* Hand work soils the grippers (`body.soil_hand`; slippery soil starts a latent `gripper_slip`): kneading 0.4 "food" on
  both hands (+0.1 "flour" with added flour), shaping dough 0.15, dredging or pressing into crumbs 0.25 and dipping,
  tossing or rubbing by hand 0.3 of the coating's kind (flour, oil, food), greasing a pan with the fingers 0.3 oil,
  separating an egg through the fingers 0.3, a cracked egg's fingertips 0.05. Brushing and spreading use a tool. A
  hand wash (cleaning's `wash robot_hand`, or food's own hand targets) also runs food's food-safety hand-wash rule.
* `knead` (minutes) and `grind` (seconds) declare `timing="commanded"`: careful speed does not stretch a commanded
  time. `grind` declares `noise_db` (an electric grinder 85 dB at 1 m, a mortar 50): quiet mode refuses the grinder.
* Greasy fingers on the knife (`body.slippery` of the hand holding it): the pieces come out uneven (cut CV +0.08 from
  slipperiness 0.5, like a dull blade), and the slip risk rises by 0.4 x slipperiness -- fingers alone never reach
  food's slip limit (0.6), but with a sliding board or a dull blade the cut slips ("the handle slid in greasy
  fingers"); a hand wash clears it. So kneading or dredging by hand and then cutting without washing is a real mistake.
* Fine positioning (`ctx.require_precision`, the body's hand precision: 2 mm for a humanoid hand, x(1 + 0.5 x
  slipperiness), x1.5 in the dark without a light, at fast speed or with a dirty lens): pulling pin bones with tweezers
  needs 2.5 mm ("Cannot position precisely enough for gripping pin bone tips right now"; checked before anything
  depends on whether the fillet has bones, so a refusal never tells a realistic robot that it does), shucking at the
  hinge 4 mm (the side method needs no precision but chips the shell).
* `hand_contamination` (food's robot field) is declared hidden and never materialized (`body.register_robot_field`).
* No food verb moves the robot (no `ctx.motion` needed); capability limits come from the core verbs food relies on.

### 11.4 Notices, questions, idle probes, generation 2 (KERNEL_API "v3.1", docs/DIALOGUE.md, docs/GENERATOR.md)

* Notices (`common.post_notice` -> `w.notice`, stored while the bus is on or a wait listens): burnt toast's smoke is the
  built-in visual `smoke` notice; a shaken drink or a bulging can opened is `hiss` (registered by food: 60 dB at 1 m,
  implies `any_sound`); spills reach the bus as the kernel's `splash`.
* Dialogue route `food.profile` (tried after the kernel's people routes): `preference` with no detail about food in
  words -> `food:tastes` (likes and dislikes); `how` about the household's food rules in words about food itself (food,
  leftovers, scraps, sweets, treats, expired, groceries; never storage, the fridge or the pantry alone: "how should I
  store the towels?" and "how do I clean the fridge?" are other domains' questions, and food's route is tried before
  those of the domains that load after it) -> `food:rule`; `preference` with detail `diet` (allergies, food restrictions) about the asked
  person -> `food:restrictions`, which the kernel's `people.pref` route reaches first today (requested). The answerer
  reads diet and allergies as the eater checks do (a generation-2 person's `profile`), keeps each person's answers
  apart (`facts["<pid>:food:<key>"]`: asking the second child about allergies is a new question; before, it counted
  as unnecessary because the first child's answer sat under the same key) and answers `food:meals`. Report
  vocabulary: the core vocabulary already has `expired`, `spoiled`, `mold`, `smelly`, `pests` and the states `ripe`,
  `cooked`, `raw`, `frozen`, `thawed`; food-specific words need a `data/vocab/food.json` outside this domain's files
  (requested). Food's `@after("report")` judge keeps grading legacy reports; typed reports are scored by the kernel's
  `report` predicates.
* Meal log (the history synthesizer's input; TIER1_SWEEP 22.3 "meal log for history"): every time someone eats
  (`hand_over` of ready-to-eat food, `person_eats`) `food_log.meals` gets `{"t", "day", "slot" (breakfast 05:00-10:30,
  lunch 11:00-14:30, dinner 17:00-21:30, else snack), "person", "kinds", "label"}` with no entity ids, so it outlives
  the things eaten (cap 200; `model.meal_log(state, pid=None, day=None)` reads it). A person asked `food:meals` says
  what they ate today (`food:meals:lunch`: that meal).
* Locale: printed best-by codes follow the household's market (`model.region_date_format`, by `clock.region`: US
  10/09/26, Canada 2026 OC 09, UK and EU 09/10/2026, Japan 2026-10-09); what the household writes by hand follows its
  own style (`clock.format_date` "long": "milk; opened October 7, 2026" in the US, "7 October 2026" in the UK), and
  `model.parse_label` reads all three written-out orders (D Month YYYY, Month D, YYYY and YYYY Month D, full or
  three-letter month words) besides ISO dates, keeping the date words out of the label's contents.
* Idle probes (exact; `HOUSE_SIM_IDLE_PROBES=0` turns them off; `TierOneSweep.test_idle_probes_are_exact` and
  `test_a_settled_lunch_box_lets_food_thermal_idle` compare digests with them on and off): `food.thermal` is idle when
  no full pass is due, no item is new or fast, no food-owned air space would move (an ice bath, a stand-in fridge's
  cooling, a lunch box or cooler still more than 0.13 C from the air around it: the write threshold over a full 30 s
  substep) and no appliance unit's heat load would drop; `food.refresh` on every substep but the first of a step;
  `food.safety` only in a house with nothing it reads. (Generation-2 children own an insulated lunch box: once it has
  settled it no longer keeps food.thermal running; while the room temperature drifts it follows it, correctly.)
* Generation 2 (`furnish.household_stock`): no meat in a vegetarian or vegan household's freezer (veggie patties), no
  peanut butter where someone has a peanut allergy (jam), gluten-free bread where someone avoids gluten, oat milk where
  someone is allergic to milk or vegan, no eggs for an egg allergy or a vegan household, infant formula (and three
  purees from six months) for a baby, juice boxes and (unless milk is out) yogurt cups for children, a second milk carton for four or more
  people; the household's own items get shelf room first. Eater checks read diet and allergies from a generation-2
  person's `profile` (else `preferences`) when the person has no top-level fields; "omnivore" restricts nothing. GEN
  places the `lunch_box` (food's type) as a child's belonging.
* Specs: all 16 run on generation-2 houses; none uses a privileged wait (M-WAITS does not apply). The date audit's
  opened milk now carries the household's handwritten "opened <date>" label (`food.dated opened_label`), the only
  place a realistic robot can learn when it was opened.

### 11.5 Verification (this round)

* `tests/test_food.py`: 157 tests green normally, with `HOUSE_SIM_STRICT=1` and with `HOUSE_SIM_DOMAINS=core,food`
  (2 skips: one needs appliances_cooking's `stovetop_kettle`, one manipulation's `unwrap`).
* Specs, seeds 0, 1, 2, 41, 97: 16 of 16 in their own mode (all on generation-2 houses; `--gen 2` the same); the four
  realistic ones also with `--camera geometric`. Forced to realistic perception, 12 of 12 examples pass with the room
  camera and 11 of 12 with the geometric one: `T-13da00933a0800-01` on seed 41 stops at the kernel's `go_room`, which
  approaches a door the geometric camera cannot see from where the robot stands (kernel request; the same one cleaning
  filed).
* Generation-1 houses: the 340 houses of `tests/data/gen1_digests.json` digest the same with and without this
  round's food changes (HEAD copy against HEAD plus the food files; the shared tree's own generation-1 drift is other
  owners').
* `tools/perception_audit.py 20 80 --verbose`: 1600 steps, 0 leaks, 0 crashes (room camera); with `--camera
  geometric` the only 2 lines are textiles_laundry's (a drying rack's `view`). `tools/layout_audit.py 20 --domain
  food`: 120 houses, 0 problems (generation 2: 220 houses, 0 problems). `tools/physics_audit.py --data`: 0 food gaps.
* Gate (`tools/orchestration/tier1_gate.py record --package ADOPT:food --spec-files "food__*" --only G0,G2,G3,G10
  --shared-tree`, compared with C1's food trials): G0, G2 and G3 pass with three reviewed differences (code ADOPT):
  the camera wrong plans added to `T-3b0290e7f718c7-01` and `T-ae4fd543d7c772-01` (verdict field `wrong`), and
  `T-13da00933a0800-01` forced to realistic now passing (False -> True, the handwritten opening date); every trial's
  state and observation digests change (generation-2 houses; tier-1 food behaviour). G10 lists only other owners'
  concurrent files. The other checks were run on their own: the unit tests above, `tools/fuzz.py 6 300` room and
  geometric and `4 200 --battery 5 --faults random` (0 defects, 0 nondeterministic), the audits above, and the room
  golden: its fixed 12-action script digests (seeds 0-3, oracle and realistic) are the same with and without this
  round's food files; only reference digests of generation-2 specs change (food's own, and other domains' whose
  generation-2 kitchens are now stocked for their household). Every other domain's spec that touches food passes in
  its own mode on the five seeds with this round's food files on a clean HEAD copy (106 specs; the one failure seen,
  admin_inventory's `T-8fb945d433137c-01` on seed 97, depends on what ran before it in the process and reproduces
  without the food changes).
* Cost (`tools/bench_systems.py --day 0 1 2 --domain food`, a busy shared machine, so approximate): food.safety about
  0.7-0.85 s and food.thermal about 0.3 s per simulated quiet day, food.refresh about 1 ms; food.safety cannot be
  exactly idle in a house with perishable food (spoilage advances every 300 s).

**Known limits of this round.** Eggs inside a closed carton that falls more than 0.2 m are jolted at the egg's own 3 cm
tolerance (the carton's pulp cradle is not modelled as padding), so most of them break. A broken glass package's food
is flagged unsafe but its shards are not separate entities (safety owns shard entities). Freezer burn and insects
need an inspect to be seen; pantry pests are not modelled by food (safety's). Boil-overs are appliances_cooking's (the
stove). A label in a numeric date with day and month both 12 or less stays ambiguous (`ambiguous_date`), whoever
wrote it. A portion keeps its
look-alike link (`derived_from`) to a source outside food's model (another domain's package); `food.refresh` drops it
only when the source is food of another kind (round-4 residual, `TierOneSweep`).
