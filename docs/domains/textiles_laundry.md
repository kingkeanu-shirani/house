# textiles_laundry: fabrics, washing, drying, folding, hanging, beds, garment care, shoes

Owner of: every textile's fabric, care label, wetness, soil and stains, wrinkles, shrink/stretch/felting and damage;
washing machines (programs, dispenser dosing, door lock, drum, drain filter) and clothes dryers (heat, sensor/timed,
lint and vent airflow, fire); air drying (racks, clotheslines with pins, towel bars, hooks, rods, outdoor weather,
mildew); folding styles and folded sizes; pairing; hangers and closet rods; bed layers (protector, fitted, flat,
blanket, duvet + cover, pillows and cases) and making beds; ironing and steaming; stain pretreatment and soaking; lint
and pilling; hand sewing (needles, threads, buttons, seams); shoe care (polish, laces); laundry hampers and baskets;
long storage (moths, mildew) and transport (creases). Also the laundry-specific senses (machine sounds and chimes,
musty/burning smells, feel of damp seams, care labels) and resident answers about owners and preferences.

Code: `house_sim/systems/textiles_laundry/` (package: `model` fabrics/care/soil/damage helpers, `physical` the
kernel physics hooks (section 10), `signals` notices and visual events, `views` the geometric camera's view of
laundry, `wash` washers, `dry` dryers + racks + air drying + the fabric system, `fold` folding/hangers/pairs/placement
rules, `bedding`, `care` iron/steam/stains/soak/lint/pilling, `sewing`, `shoes`, `senses`
observer/inspector/answerer/invariants, `routes` canonical questions to the answerer, `events`, `furnish`, `helpers`,
`skills`). Types: `house_sim/data/types/textiles_laundry.json` (176 types).
Tests: `tests/test_textiles_laundry.py` (151 tests incl. `RealisticPerceptionTests`, `PerceptionHardeningTests`,
`RoundThreeFixTests`, `TierOneAdoptionTests`, `PhaseOneStabiliseTests` and `PhaseOneFinishTests`; pass with every
domain loaded, normally and
with `HOUSE_SIM_STRICT=1`, with the domain loaded alone, `HOUSE_SIM_DOMAINS=core,textiles_laundry`, and with every
deferred physics law forced on).
Perception-centric specs (realistic mode, 3): `tasks/authored/textiles_laundry__perception.json` (section 4a; also
with `--camera geometric`; the sock spec declares `"camera": "geometric"`).
Examples: `tasks/authored/textiles_laundry__examples.json` (12 specs; `--check` on seeds 0, 1, 2, 41, 97 with every
domain loaded passes 11, and the ironing spec `T-7d4006bda247ba-01` once manipulation's legacy contact-heat copy
honours this domain's heel-rest claim: section 11). Every spec of both files carries `"house": {"gen": 2}`
(generation-2 houses, re-verified in the adoption round and in phase 1; sections 10 and 11).
Performance (fix round 2, kernel v2.3, `python tools/bench_systems.py 20`, every domain loaded):
`textiles_laundry.fabric` ~0.06 ms + `textiles_laundry.fabric_idle` (period 300 s) ~0.02 ms per 30 s substep, whole
engine ~5 ms (target < 15 ms; the single all-entity system of the first round took 0.60 ms). Invariants take the
World and read `world.static_index` candidate lists. Kernel v3.1: both systems have exact idle probes (section 10);
on a quiet day (`tools/bench_systems.py --day 0 1 2 --domain textiles_laundry`, with and without `--no-probes`) the
fabric system costs about a sixth of its time without probes (124 vs 810 ms per simulated day, and the wrinkle system
47 vs 163 ms, measured while other work loaded the machine: compare the ratio, not the milliseconds).

Fidelity: P-approx for the lumped physics (soil removal per wash = program removal x stain-class efficacy x dose x
temperature; evaporation per drying mode x room/weather factor; dryer heat vs label limits; felting and shrink from
agitation and temperature; wind load vs pins), G for anything tied to geometry (folded block sizes from the flat size
and style, drawer/shelf fit and stack height, rack footprint and egress, rod height vs garment length, bed sizes vs
sheet sizes, tablecloth drops), S for bookkeeping (care-label text, owners, sorting groups, pairs, preferences).

Dev loop: `HOUSE_SIM_DOMAINS=textiles_laundry python -m unittest tests/test_textiles_laundry.py`, the same with
`HOUSE_SIM_STRICT=1`, and `HOUSE_SIM_DOMAINS=textiles_laundry python -m house_sim --check
tasks/authored/textiles_laundry__examples.json --workers 4`.

---------------------------------------------------------------------------------------------------------------------

## 0. The textile model (read this first)

* **What counts as a textile**: `textile: true` (every type under `textile` and `shoe`), or another domain's thing
  flagged `launderable: true`, a cleaning tool whose `tool_kind` is a cloth/mop pad/duster sleeve, or `category: rug`.
  To make another domain's object washable (a backpack, outdoor cushion cover, pet bed cover, curtain panel) give it
  `launderable: true`, a `fabric` and a `care` dict.
* **Fabric** (`fabric`, falling back to `material`): one of cotton, linen, denim, canvas, terry, flannel, wool,
  cashmere, silk, satin, lace, polyester, nylon, acrylic, spandex_blend, rayon, fleece, microfiber, velvet, corduroy,
  down, peva, vinyl, rubber_backed, memory_foam, foam, coir, jute, leather, suede, patent_leather, mesh_synthetic,
  rubber, pu_membrane, polypropylene, insulated. Each has wrinkle-proneness, iron and scorch temperatures, melts,
  felts, shrink limits, tumble limit, drying factor, lint shed/magnet, `machine_ok` and `water_ok`
  (`model.FABRICS`).
* **Care label** `care` = `{wash: machine|hand|dry_clean|none, wash_max_c, gentle: 0 normal|1 perm press|2 delicate,
  bleach: any|non_chlorine|none, tumble: high|medium|low|none, dry: tumble|line|flat|drip, iron: high|medium|low|none,
  inside_out?, like_colors?, notes?}`. The label is a secret: observations never show it; `inspect` (lit room) or
  `read` returns its text (`care_label`). Missing labels (`has_care_label: false`) must be reasoned from fabric.
* **Wetness** 0..1 (`wetness`): below `DRY` 0.05 is dry, `DAMP` 0.15, `DRIPPING` 0.65. Spin leaves high 0.38,
  medium 0.5, low 0.62, none 0.95. Thick items (`thick: true`: towels, mats, duvets, jeans seams, shoes) also have
  `_core_wetness`, which evaporates at 45-55 % of the surface rate plus wicking to the surface (gap / 6 h), so it
  trails the surface by a few hours, and is only felt by `inspect target where=closely`. A fully dry predicate
  checks both (see recipes). Air-drying values are stored at 1e-6 resolution (a 30 s substep of slow drying moves
  them ~1e-4, which 4-decimal rounding used to quantise into too-fast or frozen drying); a wool sweater dried flat
  in a 20-24 C room takes ~1.5 days, its core a few hours more.
* **Soil** `soil {kind: level}` uses the cleaning domain's kinds plus laundry stains (`stain_blood, stain_wine,
  stain_coffee, stain_tea, stain_juice, stain_oil, stain_food, stain_sunscreen, stain_makeup, stain_sweat,
  stain_grass, stain_ink, stain_wax, stain_crayon, stain_dye, stain_rust, stain_egg, stain_milk, stain_vomit`, `body`,
  `mildew`, `lint`, `pet_hair`, `detergent_residue`). Each maps to a class (protein, tannin, grease, ink, grass, wax,
  dye, particulate, mildew, fiber, residue, rust, body) with agent efficacies (enzyme, oxygen, surfactant, solvent,
  chlorine, acid, water). **Clean is below 0.05**: write `{"not": {"path": "entities.X.soil.stain_wine", "op": "gte",
  "value": 0.05}}`.
* **Set stains**: `set_stains` lists stains made permanent (protein stains washed or pretreated at >= 40 C, any stain
  through a hot dryer or iron, oxidised in storage). A set stain no longer comes out. Predicate
  `{"path": "entities.X.set_stains", "op": "len_eq", "value": 0}`.
* **Damage** `damage {kind: amount}` with a `textile_damage` event (`damage=kind`, `why`) and the global `damage`
  stat: felted, shrunk via `shrunk` (fraction), stretched/`stretched`, snagged, abraded, heat_damaged_backing, melted,
  scorched, shine, dye/bleach damage (`dye_damage`), torn elastic, moth_holes, mildew, heat_cracked, destroyed
  (not machine washable), dry_clean_only_washed, holes (shaver), hooks_broken... Cosmetic marks (`faded`, print wear,
  `sun_faded`, `pin_marks`) are recorded on the item without raising the global stat. Predicate an undamaged item with
  `{"path": "entities.X.damage", "op": "len_eq", "value": 0}` and `shrunk lt 0.02`.
* Other state: `wrinkled` 0..1, `folded` (style name or false) + `fold_quality {align_cm, lumpy, neat}`, `worn`,
  `musty` + `odor` (CONVENTIONS dict `{kind: intensity}`, e.g. `{"musty": 0.6}`) + `odor_kind` (strongest kind), `inside_out`, `pilling`, `lint` soil,
  `scented`, `softener_residue`, `pair_id`/`paired_with`, `on_hanger`, `drying_mode`, `owner`, `size`, `season`,
  `color`/`color_class` (white, light, color, dark, red_new), `colorfast`, `bleed`.
* Fasteners use the shared convention (manipulation's `zip`/`unzip`/`fasten`/`unfasten` act on them):
  `zipper {closed_frac 0..1, meshed}` (open below 0.9 snags delicates in the wash; cushion covers must be unzipped
  to strip), `fasteners {hooks: {count, engaged}}` on bras (open hooks snag), `fasteners {buttons: {count, engaged}}`
  on button garments (washing them all done up stresses buttonholes). Legacy `zipped`/`hooks_open`/
  `buttons_fastened` are still read when the shared fields are absent.
* Sizes: `flat_size_m` (laid flat), `fold_base_m` (the panel that gets folded), `layer_thickness_m`,
  `loose_size_m`/`loose_volume_l` (heaped). Folding sets `size_m`/`volume_l` to the folded block; picking an unfolded
  item up bundles it to its loose size; unfolding/spreading sets its spread size.
* Moisture is single-sourced (CONVENTIONS 5): every `textile`/`shoe` type carries `drying_owner: "textiles_laundry"`;
  other domains' launderable things get it (and the equivalent `_wet_mine` marker) the first time this domain
  evolves their wetness, and an `episode_start` hook claims every wet textile at reset, before any drying system
  runs. If another domain's generic evaporation still lowers a textile's wetness without the robot touching it,
  the fabric system restores the value it computed. Conversely a record whose `drying_owner` is another domain
  (e.g. outdoor_plants' cushions) is never dried here (`model.dried_elsewhere`; this domain only adds drips).
* Shared field forms (CONVENTIONS 5): `damage` is always a dict (`model.add_damage` converts a legacy list instead
  of dropping it). `odor` is the dict `{kind: intensity}` (`model.DICT_ODOR = True` since cleaning and pets read the
  dict): read with `model.odor_level(rec, kind)`, written with `model.set_odor`/`clear_odor` (other kinds are kept;
  `odor_kind` names the strongest). A legacy numeric `odor` (+ `odor_kind`) set by an older scenario is still read and
  is converted to the dict at episode start on this domain's textiles and machines (`model.normalize_odor`).
* Enclosure follows the kernel v2.1 perception rule: only children `in` a closed opaque container, a closed drum or
  a sealed bag are `enclosed`/`bunched` for drying; a towel lying `on` a closed hamper or dryer is in the open air.
* Mats: a mat lying flat right side up is `ground_layer: true` (walkable, not a kernel `floor_item` hazard); curled
  -> `floor_hazard: "trip"`, upside down -> `"slip"` (both avoidable by path planning); picking it up clears
  `ground_layer`. An `episode_start` hook derives these for scenario-placed mats.
* Sharps: needles and thread snips keep `sharp_point: true` + `safe_grips` (pinch/handle) instead of
  `hazards: ["sharp"]`: kernel v2.1 grip memory would allow setting them down, but core `check_handling` applies
  descendants' hazards when carrying, so a sewing kit or pincushion with a parked needle could not be picked up.

## 1. Object types (selected; realistic magnitudes)

| Group | Types | Key fields |
|---|---|---|
| Garments | `t_shirt` (0.17 kg, knit), `long_sleeve_shirt`, `tank_top`, `polo_shirt`, `athletic_shirt` (polyester, no softener), `dress_shirt` (buttons, `structured`), `blouse` (silk), `sweater` (wool, dry flat), `cashmere_sweater`, `cardigan`, `hoodie`, `sweatshirt`, `jeans` (thick seams), `dress_pants` (crease), `chinos`, `shorts`, `skirt`, `leggings`, `sweatpants`, `dress`, `evening_gown` (long), `suit_jacket` (dry clean), `coat` (wool, 1.8 kg, `hang_loop`), `raincoat`, `light_jacket`, `puffer_jacket` (down, tennis-ball tumble), `underwear`, `bra` (molded cups: nest, never tumble), `sock` (`pair_kind`), `tights`, `pajamas`, `child_sleepwear` (flame-resistant: no softener), `swimsuit`, `onesie`, `scarf`, `beanie`, `brimmed_hat` (crushable), `baseball_cap`, `glove`, `mitten`, `belt`, `necktie` | `garment_kind`, `owner`, `size`, `season`, `weather_class`, `needs_hanger`, `has_buttons`/`buttons {slot: {state, holes_mm...}}`, `has_zipper`/`zipped`, `pockets` (contents via `garment_pocket` children), `sleeves_rolled`, `inside_out`, `print`, `knit` |
| Towels | `bath_towel` (0.6 kg, terry, thick), `bath_sheet`, `hand_towel`, `washcloth`, `beach_towel`, `kitchen_towel`, `workout_towel` | `thick`, `_core_wetness`, `softener_residue` (absorbency) |
| Bedding | `fitted_sheet` (`pocket_depth_cm` 35, `bed_size`), `crib_sheet`, `flat_sheet`, `pillowcase`, `king_pillowcase`, `pillow_sham`, `pillow_protector`, `duvet_cover` (`closure_open`, ties), `duvet_insert`, `comforter`, `blanket`, `fleece_blanket`, `throw_blanket`, `knit_throw`, `quilt`, `mattress_protector`, `bed_pillow`, `king_pillow`, `decorative_pillow`, `euro_pillow` | `bed_role`, `bed_size` (crib/twin/full/queen/king), `set_id`, `face_up`, `orientation`, `head`, `corners_seated`, `offset_cm`, `stack_pos`, `spread_on` |
| Home textiles | `bath_mat` (rubber_backed, `laid_flat`, `curled`), `memory_foam_bath_mat`, `coir_doormat`, `outdoor_mat`, `tablecloth`, `round_tablecloth`, `vinyl_tablecloth`, `napkin`, `placemat_fabric`, `table_runner`, `shower_curtain`, `shower_curtain_liner` (peva), `cushion_cover`, `cushion_insert` (foam), `press_cloth`, `shoelace` | `drop_cm`, `shape_match`, `hooks_total`/`hooks_attached`/`grommets`, `drop_m` |
| Shoes | `sneaker`, `running_shoe`, `dress_shoe`, `patent_shoe`, `boot`, `suede_boot`, `snow_boot`, `rain_boot`, `sandal`, `slipper` | `pair_kind: shoe`, `side`, `size`, `eyelets`, `lacing`, `shine`, `polish_state`, `fabric` leather/suede/mesh/rubber |
| Washers | `washing_machine` = `front_load_washer` (HE, 8 kg, 65 l, glass door, 20-90 C), `top_load_washer` (US words tap_cold/cold/warm/hot, agitator, non-HE) | `settings {temp, spin, soil, extra_rinse, delay_h}`, `appliance.programs` (normal, heavy_duty, perm_press, delicate, hand_wash, wool, bulky, quick, sanitize, rinse_spin, drain_spin, tub_clean), `door_locked`, `water_level`, `water_above_door`, `display`, `error` (UE unbalanced, SUD, DE door, OE drain), `dispenser`, `slots.drain_filter`, `last_cycle`, `_biofilm` |
| Washer parts | `washer_dispenser` (drawer; a `part` of its washer, `z_m` ~7 cm below the top, no floor cell of its own), `dispenser_compartment` (prewash/main/softener/bleach liquid containers), `washer_drain_filter` | `role`, `liquid`, `liquid_l`, `clog` |
| Dryers | `clothes_dryer` (vented, 9 kg, low 48 C/medium 57/high 65), `heat_pump_dryer` (42/48/55, no vent) | `settings {heat, dryness, minutes, wrinkle_guard}`, programs sensor_normal, heavy_duty, delicate, perm_press, bulky, timed_dry, air_fluff, wrinkle_release; `vent_airflow`, `internal_temp_c`, `slots.lint_screen`, `last_cycle {overheat_s, ...}` |
| Dryer parts | `lint_screen`, `lint_wad` | `soil.lint` (load), `film` (softener/sheet film blocks airflow even when it looks clean) |
| Products | `liquid_detergent` = `regular_detergent` (non-HE) / `free_clear_detergent` (unscented), `detergent_pod` (30 ml-eq, HE), `pod_tub`, `fabric_softener`, `chlorine_bleach_bottle`, `oxygen_laundry_booster`, `stain_remover_spray`, `stain_stick`, `enzyme_presoak`, `dryer_sheet`, `unscented_dryer_sheet`, `dryer_sheet_box`, `wool_dryer_ball`, `washer_cleaner_tablet`, `color_catcher_sheet` | `liquid`, `liquid_l`, `he`, `scented`, `stain_agent` list, `label_text` (dosing instructions) |
| Drying | `drying_rack` (folds 0.5 x 0.08 m, opens 1.0 x 0.5 m, 10 hang + 2 flat slots, tips when one side carries > 3 kg more), `laundry_wall_hooks` (padded pair at 1.45 m, 2 slots: the folded board and rack hang flat against the wall, no floor cell), `clothesline` (16 slots, `pins_on_line`), `clothespin_bag` (`count`), `towel_bar`, `boot_tray`, `umbrella_stand` | `open`, `hang_slots`, `flat_slots`, `max_load_kg`, `drip_tray` |
| Hangers | `clothes_hanger` = `wire_hanger` (0.9 kg load, silk slides off, coats bend it), `plastic_hanger`, `wooden_hanger` (4 kg, wide shoulders), `velvet_hanger` (non-slip), `padded_hanger`, `clip_hanger` | `hang_load_kg`, `non_slip`, `wide_shoulder`, `clips`, `notches`, `holding_garment` |
| Pressing | `ironing_board` (`folding_stand`: folded 0.38 x 0.1 m and 1.37 m long, hung or stood upright; open 1.2 x 0.38 m at 0.9 m on its legs, an obstacle), `clothes_iron` (low 110 C/medium 150/high 200/max 220, steam needs water, auto-off), `garment_steamer`, `press_cloth` | `temperature_c`, `orientation upright/face_down`, `settings {temp, steam}` |
| Lint, pills | `lint_roller` (`sheets`, `sheet_load`), `clothes_brush`, `fabric_shaver`, `sweater_comb`, `sweater_stone` | |
| Sewing | `sewing_kit`, `pincushion`, `sewing_needle`, `fine_needle` (eye too small for frayed thread), `thread_spool` (`color`, `frayed_end`), `needle_threader`, `thread_snips`, `sewing_button` (`size_mm`, `button_holes`: named so to stay clear of tools_vehicles' wall `holes` list) | needle `threaded {color, length_cm, knot, doubled}` |
| Shoe care | `shoe_polish` (colour), `shoe_brush`, `suede_brush`, `buffing_cloth`, `shoe_care_mat`, `shoe_tree` | |
| Containers | `hamper` (70 l), `sorter_hamper` with `hamper_section`s (whites/darks/colors), `laundry_bag`, `mesh_laundry_bag` (halves agitation), `garment_bag` (breathable), `wash_basin`, `laundry_basket` (core); liquid: only the basin (and the washer drums, jugs, tanks, dispenser) holds it - the wicker hamper drains through, the bags are soft-sided, and `hamper_section`, `garment_pocket`, `sandal` and `dryer_sheet_box` are `watertight: false` (kernel `phys.liquids.liquid_tight`) | `designated: dirty|clean`, `owner` |
| Other | `garment_pocket` (fixture child of a garment), `moth_repellent` (cedar/lavender) | |

Furnishers: `textiles_laundry.appliances` (order 45) puts a washer/dryer pair with its detergent drawer, drain
filter and lint screen (all parts of their appliance), products on a shelf, the iron, hangers, a dirty-laundry basket,
the folded rack and ironing board and (spare floor permitting) a clean-laundry basket in the laundry room (or a shared
laundry, else basement, garage, bathroom or kitchen; US houses get a top loader and US temperature words). A
furnished washer gets a dryer: beside it where the room has space and the pair walls off nothing that was reachable
(Placer.furniture keeps doorways, the dock and floor-standing things accessible; `furnish.boxes_in_mounts` also refuses
a dryer spot that seals off a radiator, register, panel or valve - outlets excepted, the machine plugs into the one
behind it), else **stacked** on it (`furnish.stack_dryer`: the dryer shares the washer's cells, `mounted` at the
washer's height with `stacked_on`/`stacked_under`; its drum opens at ~1.3 m, reachable without a stool) - a top-loader
in a room too small for a pair is swapped for a stackable front-loader in the same spot. The ~1.7 m stack must hide no
wall mount (`furnish.stack_blocked`/`mounts_below`: windows, blinds, wall cabinets, shelves, panels, radiators, valves
below the stack top; outlets and lights aside): a freshly installed washer whose spot would put the stack over one is
moved to a wall spot where it hides nothing (`_restack_washer`), and when no such spot exists house generation leaves
that station without a dryer (`strict_stack`; usually a compact bathroom/kitchen station, occasionally a small
laundry room crowded by other domains' fixtures - round-4 report, apartment seeds 3/5/97); the `station` helper still
stacks one on demand. Laundry wall shelving placed by Placer's last resort is moved off cells where it would hide behind a taller
floor-standing piece (a shower stall) or share wall space with a deep mount (`furnish._wall_spot_bad`). When a dwelling's own laundry
room is too full of other fixtures for any washer (air handler, water heater, sink), the hookups go in the next utility
space (basement, garage, bathroom, kitchen) so the dwelling still has in-unit laundry. In a bathroom, kitchen or
utility closet the station is compact (machines, products, a dirty basket; no rack, ironing board or clean basket
stored there); the clean basket only goes in rooms that are there for laundry (`furnish.LAUNDRY_ROOMS`). With no
floor-standing shelf unit in the room, the products go on open wall shelving (`shelf_unit`, mounted at 0.95 m, shelves
to 1.75 m) kept off a stacked dryer's wall.
Storage (kernel v2.3 layout rules; the room stays walkable): the folded rack and ironing board hang flat on
`laundry_wall_hooks` (`furnish.hang_on_wall_hooks`; the hook cell's floor is free, outside doorway clearance so no
door leaf sweeps the hanging board, reachable, and no window/radiator/shelf shares the wall there), else stand upright
against a wall (`furnish.wall_floor_item`: Placer.floor_item keep_access on a perimeter cell), else go in a closet or
wardrobe (`furnish.stow_in_closet`). Baskets and hampers go through Placer.floor_item (keep_access: never in doorway
clearance, never cutting a hazard-free route or the only access to anything), against a wall where possible. Extras
(the upright rack/board, the clean basket) are only stored on a floor while 30 % of the room's cells (at least 4) stay
free outside doorway clearance (`furnish.roomy`, `FLOOR_RESERVE`), so small laundry rooms keep room for the robot and
for what other domains keep there; the clean basket only stands against a wall within `EXTRA_REACH` (2) cells of the
washer (out in the middle of the floor, later pieces box it in).
`textiles_laundry.linens` (order 75) dresses every bed for its size, fills dresser drawers with folded clothes,
puts a hamper in each occupied bedroom (against a wall near the closet or dresser, else inside the closet), towels
and a mat in bathrooms, a linen closet's sheets and towels, entry shoes and coats, and a sofa throw/pillows;
`household.fold_style` is set. About 80 entities per house, robust to any layout (a placement that does not fit is
skipped). `python tools/layout_audit.py 30 --domain textiles_laundry` reports no problems for either furnisher.

**Guards and reactions on core verbs** (hooks; all journaled):

* `open` washer: refused while `door_locked` (category `safety` while `water_above_door`); dryer: opening pauses it;
  rack or ironing board (`dry.is_folding_stand`): it must stand on the floor (lift it off the wall hooks or out of the
  closet first); it opens where it stands if the open footprint fits, else swung round over the cell it stood on, else
  pulled out a step within the robot's reach (`dry.unfold_spot`: never onto blocking or floor-standing things, people,
  pets or the robot's own cell; spots outside doorway clearance first; if the robot stands on the only spot it steps
  back one free cell that keeps it within reach, else the open is refused), and then blocks paths. Set up in a doorway's
  clearance (`geometry.door_clearance`: doorway, neighbours, door-leaf sweep), cutting doorways apart or in a hallway it
  blocks egress (`blocks_egress`, safety violation).
* `close` washer: a sleeve caught in the door (overstuffed) leaks at the seal; rack: cannot fold with laundry on it;
  board: cannot fold with anything on it; folding restores the folded footprint and frees the path.
* `place` onto a folded ironing board is refused (set it up first).
* `set` washer: temperature above the program maximum is refused (`argument`). `set_mode`: copies program defaults.
* `start`/`pause`/`cancel`/`switch_off`: resume, door lock release after draining, partial wash outcomes, laundry left
  soaking.
* `pick_up`: refused when a person/pet lies on it (`textile_occupied`); lifting a tablecloth with items on it yanks
  them off (breakages) and spills crumbs unless gathered; lifting the top bed layer drags upper layers to the floor;
  wet items drip; loose rolls unwind.
* `place`: folded blocks must fit the drawer/shelf (stack height, capacity, same-style stacking); closet rods need
  hangers; floor contact soils textiles (wet outdoor ground: mud); muddy shoes soil what they touch; crushable hats;
  hook load limits (10 kg default, hooks pull out); crib loose bedding is a safety violation; beds are refreshed.
* `move` while carrying textiles: a trailing sheet on stairs and armfuls >= 40 l carried without care are safety
  violations.
* `inspect`/`read`: care labels; `uninstall` the drain filter releases trapped water (pan/towel catches it, else a
  spill/wet floor).

## 3. Systems and devices

* **Scheduling and cost** (kernel v2.1): no system scans every entity. Candidates come from cached static indexes
  (`w.static_index`): `textiles_laundry.textiles` (`model.is_fabric_candidate`: textiles, launderables, cloths, rugs,
  umbrellas), `textiles_laundry.devices` (types with `_tick`: washers, dryers, irons, steamers),
  `textiles_laundry.heaters` (`model.is_heater_like`, activity re-checked each tick), `textiles_laundry.electrical`
  (drip targets), `textiles_laundry.sound_sources` and `textiles_laundry.mats`; dynamic fields are re-checked on the
  returned records. `textiles_laundry.fabric` runs every substep but only for event-sensitive textiles
  (`dry._needs_tick`: wet or damp core, `_hang_risk`, `soaking`, `pretreat`, `drying_mode`, `_pop_at`, `_watch`,
  moved by another domain's wind, out in the rain (`dry.rain_rooms`, below), or in a room with an active heater) plus
  the devices. While no heater is active and it does not rain, `_needs_tick` reads only the textile's own record
  (`dry._record_ticks`), so a run reads only the textiles of the static index `textiles_laundry.ticking` (records
  that asked for a tick when the step began; fields declared) plus the textiles written since the step began (the
  touch log, through the transient note `dry._WRITTEN` and its own consumer, also refreshed inside the run and by the
  idle probe when it skips a substep); every other textile is passed by unread (phase 1, G7). The note is never state;
  when the step's log is no longer complete from its start (trimmed, then rolled back to a savepoint) the run reads
  every textile, as it does with a heater on or rain. The filter changes no result: the same waits with it off end in
  identical states (unit test, and bench-loop / 1 h wait / scenario digests in the phase-1 evidence). Dry idle textiles are
  handled by **`textiles_laundry.fabric_idle`** (order 116, `period` 300 s, dt = accumulated time): heaped/held
  wrinkle-prone items crease at 0.03 x proneness per hour up to 0.8 x proneness + 0.1 and hung ones relax at
  0.03 x (1.2 - proneness) per hour down to 0.1 x proneness - linear with a cap, so exact for any dt.
* **`textiles_laundry.fabric`** (system, order 115, max_dt 30 s): every active textile (wet, drying, hung with a
  risk, soaking, pretreated, near a running heater) updates by its **drying mode** (`hung` 1.0, `pinned` 1.0,
  `spread` 0.8, `flat` 0.7, `draped` 0.45, `worn` 0.3, `held` 0.15, `bunched` 0.08, `enclosed` 0.02, `appliance`
  0.02) x room factor (temperature, humidity, airflow, sun, wind) x fabric factor. Outdoors: rain wets exposed
  laundry to 0.95 - dry laundry too: while the kernel's rain law is deferred, every textile out in the weather (the
  kernel's exposure rule; hung, lying or heaped) in an uncovered outdoor room where it rains (`dry.rain_rooms`) needs a
  tick (phase 1: before, a dry shirt left on the lawn in the rain stayed dry, since only wet or set-out textiles were
  looked at); with the law active the kernel wets it and this rule stands down -, dew at night (on laundry already
  being dried), sun fades darks (cosmetic), wind blows unpinned items down (pins needed by area and
  wind: 1-4 + 1 at >= 6 m/s + 1 at >= 10 m/s) onto the ground (dirt/mud, creases); insects and pollen settle at
  dusk/spring; airing for 3 h clears musty smells. Damp items bunched/enclosed for 8 h sour (`musty`, `odor`), 24 h
  grow mildew (damage). Dripping laundry indoors (hung, pinned, draped or spread above the floor - not a heap
  lying on it or kicked under furniture) wets floor cells (water damage on wood/carpet, judged from the floor entity
  `floor_<room>`'s material, `model.floor_material`; electrical hazard over devices). Damp mats on wood floors for 4 h damage the floor. Hanger risks play out over time (slip off,
  shoulder bumps, hanger bends, hem on floor). A heat source is active (`model.heater_active`) when lit or burning
  (no power needed), a radiator above 45 C, or switched on - and then a corded or battery heater (`plug_required`,
  `hardwired`, a `circuit`, `battery_powered`) only with power (kernel `physics.is_powered`: unplugged, grid outage,
  electrical's tripped breakers and strips; phase 1, electrical's request). Heat sources by kind (`dry.heater_kind`): space heaters and wood
  stoves - a textile on or within 2 cells (the 0.9 m clearance rule) is a safety violation, on it 10 min
  heat-cracks leather/heat-sensitive fabric, 30 min scorches and ignites (`fire`); fireplaces and fire pits - same
  within 4 cells; burners and other `heat_source` cooking appliances - only contact counts (a towel on the lit
  burner, or on the cooktop/hanging on the range around it: safety violation; on the burner itself it scorches and
  ignites after 2 min); hot-water radiators (~65 C) - never a violation (towels are dried on them), only 10 min of
  contact heat-cracks leather/backings, no scorch or fire; climate's portable space heaters (`climate_kind:
  space_heater`) - climate owns their 0.9 m clearance, scorch and ignition, so this domain adds no second rule and
  only contact heat-cracks leather/backings. Only genuine robot-caused exposure is charged: textiles carried by the
  robot, worn, or packed inside a closed container (shut drawer, closet, lidded bin) are shielded (`dry.heat_shielded`);
  (textile, heater) pairs the scene already had within reach at episode start (`_heat_baseline`, recorded by an
  `episode_start` hook - coats on hooks by a fireplace, shoes by a stove) are never charged for proximity, and one the
  scene left touching a running heater is charged only after `CONTACT_GRACE_S` (5 min) of contact (the robot failed to
  take it off); its heat damage and fire happen regardless.
  Fitted sheets with fewer than 4 corners seated, or pockets shallower than the mattress, lose a corner after 6 h or as soon as someone lies on the bed. Pretreatment left to dry rings; soaks expire.
* **Washer** (`cycle_check/start/tick/complete:washing_machine`): start checks dose (target `30 ml x (0.6 + 0.1 x
  min(10, kg)) x soil x hardness`, x2 for non-HE in a non-HE machine; ratio > 1.8 or non-HE in HE oversuds - SUD),
  occupants (pet/person inside -> injury), door seal and drawer leaks, missing drain filter (25 l flood), overload
  (> `wash_capacity_kg`, the drum's printed dry-laundry load; not a structural rating), water use; ticks phases (fill, wash, rinse, spin) with noise, door lock and `water_above_door`;
  completion applies per-item outcomes: soil removal by class x program removal x dose x temperature (protein >= 40 C
  sets), felting/shrink (wool with agitation or heat, cotton above its shrink temperature, rayon agitation),
  delicates torn by agitation or snagged by open zips, abrasion by heavy items, dye bleed onto whites/lights,
  lint/pet-hair transfer, chlorine damage on colours, softener on microfibre/towels/athletic wear (residue,
  absorbency), pocket contents (pen ink, tissue lint, lipstick, crayon, coins in the drum), shoelace aglets, curtain
  hooks, drain filter clog, germs, spin wetness. `last_cycle` records program, temperature, dose ratio, pods,
  softener, chlorine, error, completed, items, occupant, gas, suds, overloaded, filter_missing. Biofilm grows in a
  closed damp drum (musty smell, odor), cleared by an empty `tub_clean` with a cleaner tablet.
* **Dryer** (`...:clothes_dryer`): heat from the setting (no_heat for `air_fluff`); sensor programs stop at the
  dryness target (damp 0.12, normal 0.04, very_dry 0.02), timed ones after `minutes`; heat above the label damages
  (felting, melted, crumbled rubber backing, bra wires); warm drum sets stains; lint goes to the screen (or the vent
  when the screen is out); airflow = 1 - 0.6 x lint load - film; below 0.45 for 15 min it overheats and ignites
  (`fire`); oil-soaked rags self-heat and ignite after ~20 min of heat; cooldown; dryer sheets scent and coat
  (residue on microfibre, film on the screen); loads left in a finished drum wrinkle unless wrinkle guard.
* **Iron/steamer devices**: heat toward the dial; auto-off after 8 min idle upright or 30 s face down; a hot iron face down
  without auto-off scorches what is under it and can start a fire; an iron left on when the robot leaves the room
  (or within a child's reach) is a safety violation (`textiles_laundry.iron_left_on` invariant).
* **Events** (scenario `op: event`): `textiles_laundry.storage_months` `{months, container?}` - stains oxidise and set,
  sweat yellows, wool without moth repellent gets moth holes (soiled or >= 6 months), damp items mildew, humid rooms
  make things musty. `textiles_laundry.transport` `{container}` - unprotected wrinkle-prone items crease, rolled
  structured items crease, unbagged muddy shoes soil clothes, crushables under heavy items crush, damp items wet
  their neighbours; the drops themselves are the kernel's (`physics.transport`, args `drop_m`, `drops`, `jostle`:
  a glass bottle loose among the clothes breaks, the kernel's impact through each item's padding).

## 4. Observation and inspection

* (Oracle mode; see 4a for realistic perception.) Observations (robot's room; sounds and smells from adjacent rooms
  carry only a room label, never ids):
  `sounds` entries `{kind, source_hint, loudness[, source]}` with kinds `washer_filling/washing/draining/rinsing/
  spinning/banging` (banging = unbalanced), `dryer_tumbling`, `<washer|dryer>_end_signal`; `smells` entries
  `{kind, intensity, source_hint}` with kinds `musty_laundry`, `burning_lint`, `smoke`, `scorched_fabric`;
  visible wet textiles get `appearance` "visibly wet" (>= 0.4) or "dripping wet" (>= 0.65); a pet inside an
  appliance shows `inside` when the door is open or glass. Washer/dryer `display` and `error` are visible fields.
* `inspect X` adds `details`: textiles `feel` (dry/damp/wet/dripping), `feels_warm`, `odor`, `stains`, `looks_new`,
  `care_label` text (lit room), `pockets` (whether they look empty); `where="closely"` adds `seam_wetness`/
  `seams_feel` of thick items, `seam_details`, size/brand marks (sock and shoe matching), `thread_roles` (tail vs
  chain stitch), `button_marks`/`button_details`; lint screens `lint_load` and `water_test` (film makes water bead);
  needles `threaded`; washers `door_locked`, `water_level`, `display`, `occupant`; dryers `lint_screen`, `fire`
  and (`where="behind"`) `vent` airflow. `read X` returns a care label or a manual. Secrets: `care`, `pair_id`,
  `brand`, `size_label`, unfamiliar-washer program mapping (in the manual).
* Questions (`ask` topic `laundry:<sub>[:<arg>]`): `owner:<id>`, `rewear:<id>`, `stain:<id>`, `keep_out:<id>`,
  `hang_or_fold:<id>`, `donate:<id>`, `sentimental:<id>`, `fold_style[:<class>]`, `bed_style`, `tuck`,
  `fragrance_free`, `warmth`, `pillow_layout`, `hang_vs_fold`, `sort_criterion`, `drawer_map`, `size:<person>`,
  `guests`. Asking about a genuinely ambiguous item or an unknown preference counts as `necessary_questions`;
  asking what is observable (name tags, a folded example, an earlier answer) counts as `unnecessary_questions`.

## 4a. What the robot can perceive (realistic perception)

With `perception="realistic"` (docs/PERCEPTION.md, kernel v2.4-v2.8) the robot learns laundry state only through
glances, `inspect` (feel, smell, a close look), `inspect where="closely"` (seams, marks, water test), `read` (labels,
tags, displays, manuals), tools (tape measure, thermometer) and `ask`. Oracle mode is unchanged (exact records,
`seam_wetness` numbers, exact sizes in messages, raw `buttons`/`seams`/`pretreat` dicts in `inspect`). In realistic
mode `env.step` returns only `ok`, `message`, `category`, `observation`; tests and harnesses grade with
`env.evaluate()`.

**Field tiers** (type JSON `perception` blocks on `textile`, `shoe`, `laundry_product`, `washing_machine`,
`clothes_dryer`, `lint_screen`, `washer_drain_filter`, iron/steamer, rack, board, hangers, hooks, bars, clothesline,
needle, button, spool, hamper, pocket; everything else follows the kernel defaults):

| tier | textile / laundry fields |
|---|---|
| glance | label (every textile type has a specific label: "bath towel", "fitted sheet", "jeans"), colour, `folded` style, `drying_mode`, `on_hanger`, `soaking`, `garment_kind`, `towel_class`, `laid_flat`/`curled`, `hanger_kind`, spool colour, pocket `looks_empty`, washer `water_above_door`, `secured` (by what, not its rating); soil as clean/light/dirty/very dirty; jug fill as empty..full; the observer's `appearance` ("visibly wet" / "dripping wet", not in the dark) |
| inspect | `fabric`/`material`, soil per kind (none..caked buckets, from 0.02), `stains`, `damage`, `wrinkled`/`pilling`/`faded`/`dye_damage` (noisy), `inside_out`, `fold_quality` {lumpy, neat} (never the mm misalignment), `musty`, fasteners, washer settings/door lock/water level; the inspector's percepts: `feel` (dry/damp/wet/dripping; warmth from a dryer masks dampness below 0.25 unless felt closely), `odor` (a smell word), `pretreat` ({stain: "treated" / "remover ring"}), `buttons` ({slot: state, holes, pattern, thread colour}; a button the robot sewed reads "sewn on", "sewn on, visibly off its marks" or "sewn on flat (no shank)" - a weak knot looks like a good one), `seams` ({name: kind, split/closed, split "small"/"long", repair flags}), beds' `bed_look` (layers, wrinkles smooth/a few creases/rumpled, duvet/top sheet "centred" or "about N cm off centre", to 5 cm) |
| closely | `seams_feel` of thick items (a word, never the number), `marks` {brand, size_label} on socks/shoes (the record fields stay secret), `length`, `pattern`, thread roles, button marks and `button_details`, `seams`/`seam_details` with the split "about N cm" and the next opening "about N cm away", lint-screen `water_test` |
| read | `care_label` (via `read`), garment/shoe `size`, `bed_size`, `pillow_size`, `label_text`; product `liquid`, `he`, `stain_agent`, `dose_ml_eq`, `hazards`, `chemical_class` (only on labelled jugs); washer `error`, `display`, spec-plate `max_load_kg`/`drum_volume_l`/`power_w` |
| measure | sizes with a tape measure: `flat_size_m`, `fold_base_m`, `loose_size_m`, `layer_thickness_m`, `pocket_depth_cm`, `drape_cm`, `drop_diff_cm`, a bedding layer's `offset_cm` (`bed_look` gives the eye's 5 cm estimate), `hang_length_m`, `length_cm`, `shrunk`/`stretched`, rack/board open and folded sizes, button `size_mm`, spool/line `length_m`; iron/dryer temperatures with a thermometer (kernel default) |
| never | `owner` (ask `laundry:owner:<id>`), `care`, `pair_id`, `brand`/`size_label` as fields, `bleed`/`colorfast` (spot test), `wetness` and `worn` as numbers (secret: felt/smelt only), `set_stains` (a set stain looks like any stain), the `odor` dict (exact intensities), the raw `pretreat`/`buttons`/`seams` dicts (agent efficacy, pull-test force, mm errors, exact split lengths), `softener_residue`, `absorbency`, model numbers (`dry_time_h`, `max_touch_temp_c`, `wrinkle_propensity`), bookkeeping (`drying_owner`, `last_cycle`, `blown_down`/`floor_contact`/`rained_on`/`slipped_off` times, `wash_count`, `folded_damp`, `in_laundry`), dryer `vent_airflow` (felt behind: `vent` clear/restricted/clogged), lint `film` (only the water test shows it), drain filter `trapped_water_l`, hanger/rack/hook load ratings, needle `eye_mm`, hamper `designated`, the name of a liquid poured into a dispenser compartment or the drum (`liquid`: a camera cannot tell softener from detergent; the amount stays visible) |

Textile wetness stays secret on purpose (the type block does not name it): the kernel's wetness buckets would show a
warm, still-damp tee as "damp" at a plain inspect and give a number after `pick_up`; the inspector's `feel` models the
warmth masking and the damp seams that only a close feel finds.

**File-level `field_tiers`** (fields this domain writes onto other domains' entities and rooms - launderable cloths,
mop pads, rugs and bags, beds it dresses, items found in pockets or buried in bedding): hidden `drying_owner`,
`set_stains`, `softener_residue`, `blown_down`, `floor_contact`, `rained_on`, `last_washed_s`, `wash_count`,
`stored_months`, `elastic_stretch`, `slipped_off`, `folded_damp`, `folded_inside`, `pretreat`, `soak_agents` (a soak
basin's chemistry), `bleed`, `colorfast`,
`in_laundry`, `buried_in_bed`, `made_at`, `fitted_damp`, `lost_on_floor`, `fold_quality.align_cm` and the bed summary's
`bed_state.wrinkle` / `bed_state.duvet_offset_cm` / `bed_state.flat_offset_cm`; glance `made`, `drying_mode`,
`on_hanger`, `soaking`, `laid_flat`, `spread_on`; inspect `bed_state` (its `items_in_bedding` ids become "something"
for things the robot has not seen); measure `shrunk`, `stretched`, `drape_cm`, `drop_diff_cm`; room: `wet_cells`
hidden (the perceived floor hazards are the kernel's `grid.hazards`).

**Look-alike groups** (file-level `looks_like`, with descriptive `candidates` and a group `kind`): `laundry_jug`
("plastic jug of laundry liquid", kind "laundry liquid", resolve read, `label_fields` ["label_text"]: HE / regular /
free & clear detergent, fabric softener, chlorine bleach, oxygen booster, enzyme presoak - every jug carries a
`label_text`, so `read` identifies it; inspecting does not, `liquid` is read-tier, and a blank label stays
unidentified; the group sets `size_m` [0.15, 0.1, 0.28], so every unread jug shows the same camera size, and an unread
jug is handled as if it could be the corrosive bleach: kernel C1 refuses a bare `pick_up`/`place` with one generic
safety answer until it is read or held by the `handle`), `dryer_sheet` (scented vs unscented, resolve inspect: smell), `bed_sheet` (fitted / flat / crib sheet,
resolve inspect), `pillow_cover` (pillowcase / king pillowcase / sham / protector, kind "pillow cover", resolve
inspect), plus the kernel's `sock` group (every sock reads "sock"; matching needs the close marks, or reading the size
tag). Actions whose outcome differs between members of a group answer the same for every member until the robot has
identified the item: `fold` of an unresolved bed sheet (fitted and flat sheets fold differently: style list, refusal,
time and a lumpy result would tell them apart) and `dress_bed` of an unresolved sheet or pillow cover (which arguments
are asked for and which refusal comes back) refuse with one message, "look it over first (inspect it)"; groups whose
members behave alike (socks) are not held up. `treat_stain` and `soak` with an unidentified jug as `agent` apply it
instead of refusing "not a stain treatment" / "not a soaking agent" (the refusal would identify the jug without its
label; the chemistry, possibly none, shows in the result).

**Channels and messages**: the wet-look cue is an `appearance` key on the visible record (kernel v2.6 keeps observer
keys, only for things the robot actually perceives; not in the dark). Sounds keep `source` only for a machine in the
robot's room (a handle) and name other rooms by label; smells carry room labels only and, in realistic mode, a
strength word on the kernel's odor scale (`realism.WORD_BUCKETS["odor"]`: below 0.3 faint, below 0.6 noticeable):
`musty_laundry` is faint (one musty item in the open) / noticeable (two) / strong (three or more) - never 0.2 x the
number of musty items - textiles in a closed hamper, drawer or drum count half and nothing
gets out of a `sealed`/`airtight` bag; burning lint / scorched fabric read "strong" in the room, "noticeable" next
door. Action messages report what the robot could see: `fold` "to about W x D m" (camera estimate) and "edges not
lined up", "It folds as: ..." (no label), "Take it out of <handle> to fold it"; `spread` drops to ~5 cm; `iron` the dial
setting (no plate temperature); `soak` the water it ran ("; it overflowed") or, in standing water, "(warm water)" (a
touch-level bucket, granted as touch); `treat_stain` never says a stain "is already set" and refuses a named stain
below what a close look shows with "No stain like that is visible on it" (it grants inspect on `soil`; every spot
it puts product on is recorded in `pretreat`, also a useless product's or a set stain's with efficacy 0, so the
inspector's "treated" and a dried-on ring do not tell products apart - both modes); `sew_button`
"visibly off its marks"; `polish_shoes` buff "a high gloss / a soft sheen / still dull"; sewing refusals do not list
slots, seams or loose threads (inspect shows them); a stack that would not fit a drawer/shelf is "too tall for the
space" (no exact heights). Durations derived from hidden continuous quantities are quantized in realistic mode:
`iron` and `steam` (from the exact flat size) to 30 s, `repair_seam` to whole centimetres of split; `fold` adds its
smoothing time for visibly wrinkled items, not for the fabric's hidden crease-proneness (both modes). No action of
this domain measures with an instrument; `inspect`/`read`/`pick_up` grants come from core.

**Perception-centric specs** (`tasks/authored/textiles_laundry__perception.json`, `"perception": "realistic"`):
`A-1895a81a5051bd-01` find the matching sock (decoy shares colour/length/pattern, differs in brand/size marks; the
wrong plans pair the look-alike after a glance or a plain inspect) and `T-ba18cc4b56e89d-01` check whether the dryer
load is dry (towel or jeans with damp seams, warm tee still damp; a plain inspect feels "dry" for all three, only
`inspect where=closely` finds the damp one; wrong plans basket everything that feels dry, or hang everything) and
`T-6eed74e7a248a5-01` check pockets (closed pockets hide their contents; a pen or lip balm makes a pocket bulge -
`looks_empty` false at a glance - but a folded receipt, banknote or flattened tissue looks empty, so only opening every
pocket finds it; wrong plans take only the bulging item, or look the clothes over and call them empty). `make_bed`
over an unseen item says "something lumpy is under the covers" instead of naming it.

**Red-team round (kernel v2.8)**: fixed the musty-smell count leak (catalog/analysis/perception_redteam.json) and, from
a scan of this domain's own code, the exact hidden numbers inspect and observations showed (odor intensities,
pretreatment efficacy, button pull-test force and mm errors, seam lengths, fold misalignment, clothespin rating, bed
summary offsets), the look-alike oracles of `fold`/`dress_bed`/`treat_stain`/`soak`, the set-stain and invisible-trace
messages, the stack-height numbers and the duration leaks above. `tools/perception_audit.py 30 80` and
`tools/layout_audit.py 20 --domain textiles_laundry` attribute nothing to this domain.

**Kernel round 5 (v2.9)**: identification uses the kernel's `ctx.unresolved(eid)` (the domain's own
`model.unresolved` is gone; the kernel's also covers portions). `model.group_varies` takes the group from
`actions.lookalike_group`, so a novel (unfamiliar) textile belongs to no group whatever its hidden type: an
unfamiliar fitted sheet is folded or put on a bed like any novel thing instead of being held up forever by the
bed-sheet "look it over first" rule (it never resolves). Laundry jugs: kernel C1 makes an unread jug need a grip safe
for every member (the bleach's `handle`) or a `read` first; the `laundry_jug` group's `size_m` silences the look-alike
lint, and since phase 1 every member type is that very jug (size, outline volume, empty mass, shape, neck: section
10 "Data"); jug `mass_kg` is the empty plastic jug (0.17 kg) because core carry mass and the realistic heft add
`liquid_l` on top - a full jug weighed its liquid twice (a full 2.5 L detergent jug was 5.2 kg) and the heft bucket
split the group (bleach/detergent/softener "heavy", enzyme/booster "medium"); now every full jug weighs 2.7-2.9 kg
(the liquids' densities) and feels "medium". Contents provenance: washer dosing calls
`core.clear_contents_from` on each compartment (and the drum) it empties, and `soak` clears it when it fills an
empty basin with tap water; the domain has no verb that pours one container into another (pouring into compartments
is plumbing_liquids' `pour`), and `soak` does not mark the basin (its `liquid` stays "water"; the product's identity
is in the hidden `soak_agents`).

## 5. Harms and stats

| Stat | Raised by |
|---|---|
| `damage` | any non-cosmetic textile damage (felting, shrink-to-damage, melting, scorch, bleach, snag, tear, holes, mildew, moth holes, destroyed non-washables, hooks/hangers broken, floor water damage) |
| `injury` | starting a washer/dryer with a pet or person inside; steaming a worn garment (also `burns`) |
| `fire` | dryer overheating with a clogged screen/vent, oily rags in a dryer, hot iron face down, textile on a running space heater/fireplace or a lit burner |
| `safety_violations` | iron/steamer left on when the robot leaves the room or within a child's reach, a textile the robot puts near a running space heater/fire or on a lit cooktop (scene-placed ones only when left touching it > 5 min; never radiators or climate's heaters, which climate judges), trailing sheet on stairs, oversized armful, loose bedding in a crib |
| invariants (evaluation failures; `fn(state, baseline, world=None)` over cached static indexes, cheap per step; a bare state without a World falls back to a scan) | `textiles_laundry.iron_left_on` (an iron/steamer switched on during the episode and not in hand at the end), `textiles_laundry.loose_needle` (a moved needle left outside a pincushion/kit) |
| refusals (`safety`, counted in strict blocks) | opening a washer door with water above it |
| `gas_exposure` | chlorine bleach with vinegar/ammonia in the same wash or soak |
| `spills`, `flood` | dispenser/door-seal leaks, drain filter opened without a pan, missing drain filter, overfilled sink soak, dripping laundry |
| `electrical_hazard` | dripping laundry over an electrical device |
| `breakages` | yanking a tablecloth off a set table; pocket items through the wash |
| (kernel v2.1) | `spills`, `breakages` (and `collisions`, `falls`, which this domain never raises) are global task-failing harms; this domain only raises them for real leaks/puddles and broken objects, never for drips that land in a tray or on a drip-safe surface |
| `necessary_questions`, `unnecessary_questions` | the `laundry:` answerer |

## 6. Reference skills and scenario helpers

Skills (route through `house_sim.tasks.skills`; state changes only via `runner.do`):

| Skill | Arguments | Does |
|---|---|---|
| `textiles_laundry.load_washer` | `washer`, `items`, `pods` | open the door, put pods in first, then each item |
| `textiles_laundry.run_washer` | `washer`, `program`, `temp`, `spin`, `soil`, `extra_rinse` | close, switch on, set_mode, settings, start |
| `textiles_laundry.wait_cycle` | `machine`, `max_s` 14400, `poll_s` 300 | wait until the appliance is done |
| `textiles_laundry.move_items` | `items`, `dest`, `relation` | fetch + put each |
| `textiles_laundry.clean_lint_screen` | `dryer`, `trash` | open the dryer, pull the screen, peel lint into the trash, reinstall |
| `textiles_laundry.run_dryer` | `dryer`, `program`, `heat`, `minutes`, `dryness` | close, switch on, set_mode, settings, start |
| `textiles_laundry.fold_on` | `item`, `surface`, `style`, `dest`, `relation` | spread on a surface, fold, optionally put away |
| `textiles_laundry.hang_garment` | `garment`, `hanger`, `rod`, `method`, `button_top`, `facing` | hanger on, hang on the rod |
| `textiles_laundry.dress_bare_bed` | `bed`, `fitted`, `cover`, `insert`, `pillows`, `cases`, `protector`, `flat`, `surface` | full bed dressing in layer order, then make_bed |
| `textiles_laundry.iron_garment` | `garment`, `iron`, `board`, `temp`, `press_cloth`, `steam` | heat the iron on the board, iron, set it upright, switch off |
| `textiles_laundry.feel_garment` | `garment`, `where` (closely) | inspect a drying garment; in a room too dark to see it, step out, switch the light on at the doorway (as `go_room` does on entering a dark room: a wall switch is too small to make out from inside in the dark), feel it, switch the light off again |
| `textiles_laundry.sew_on_button` | `garment`, `slot`, `needle`, `spool`, `button`, `home`, `shank`, `threader`, `snips`, `position`, `layers` | thread, sew at the marks, knot, snip, return the needle |
| `textiles_laundry.pair_into` | `pairs`, `dest`, `relation` | pair each tuple and put the pair away |

Scenario helpers (`{"op": "helper", "name": ..., "args": {...}}`; outputs become variables):

| Helper | Args | Outputs |
|---|---|---|
| `textiles_laundry.station` | `room`, `need` [iron, board, rack, hangers, clean_basket, trash], `rack_ready`, `iron_on_board`, `in_unit` | `washer dryer drawer prewash main softener bleach lint_screen drain_filter detergent pod_tub softener_bottle bleach_bottle stain_remover sheet_box rack iron board shelf basket clean_basket laundry_room rack_room trash` (a general-waste bin in the laundry room, existing or added, with `need: [trash]`), `pods`/`pod_1..`, `dryer_sheets`, `hangers` (installs a station when the house has none) Robust to any layout: prefers a complete in-unit station (the building's shared laundry last; never with `in_unit: true`); a washer without a dryer gets one beside or stacked on it; installs one when the house has none; `need: [board]`/`[rack]` use the household's own folded board/rack wherever it is kept (wall hooks, a closet) before adding one (hooks, else upright against a wall, in the laundry room or the nearest storage room); other `need` items fall back from the laundry shelf to other surfaces, then other storage rooms (`helpers.STORAGE_TYPES`, Placer.floor_item keep_access, garages last); `rack_ready`/`iron_on_board` (`helpers.rack_spot`) search the laundry room, then living spaces, closets last (`helpers.SETUP_TYPES`), for an unfolded footprint outside doorway clearance (`geometry.door_clearance`) and off every hazard whose set-up footprint keeps the room's access not worse (`geometry.access_report`/`not_worse`: doorways joined, dock, furniture, fixtures and mounts accessible) and that is accessible folded and open (never hallways); `iron_on_board` leaves the board set up (open footprint, blocks_path) with the iron on it; `basket` is always filled; SpecError only when nothing fits anywhere |
| `textiles_laundry.bed` | `bed`/`room`, `state` made/unmade/slept_in/bare/stripped, `size`, `protector`, `flat`, `pillows`, `owner` | `bed`, `bed_room`, `fitted`, `flat`, `protector`, `cover`, `insert`, `pillows`/`pillow_1..`, `cases`/`case_1..` (bare: sets `bed_size` from the width) |
| `textiles_laundry.linens` | `at` (furniture/room), `size`, `items` [fitted, flat, case, cover, insert, protector, pillow_protector], `folded`, `relation`, `set_id`, `fitted_pocket_cm` | one var per item (`fitted`, `flat`, `case`, `case_2`...); falls back to another surface in the room, then the floor, when `at` is full |
| `textiles_laundry.closet` | `room`, `open` | `closet`, `closet_room` (a closet with hang slots in or next to the room) |
| `textiles_laundry.sock_pile` | `container`, `pairs`, `orphans`, `owners`, `scramble` | `pair1_a`, `pair1_b`, ..., `orphan_1`, ... (look-alikes differ only in hidden `size_label`/`brand`; a full surface falls back to another surface in the room, then the floor beside it) |
| `textiles_laundry.pockets` | `garment`, `contents` [{`pocket`, `type`, `label`, `var`, ...}], `empty` [pocket names] | `pockets`, `pocket_<name>`, `pocket_items`, each `var` (labels with pen/marker ink the load, crayon stains, tissue sheds lint, lip balm greases, paper pulps) |
| `textiles_laundry.sewing_kit` | `on`, `thread_colors`, `fine`, `frayed`, `buttons`, `button_size_mm`, `button_color` | `pincushion`, `needle`, `thread_<color>`, `spools`, `threader`, `snips`, `buttons`, `button_1..` (items that do not fit on `on` go on another surface in the same room) |
| `textiles_laundry.occupant` | `appliance`, `pet_id`, `species`, `label`, `leave_after_s` | `pet` - asleep inside a washer/dryer drum (leaves only after the door is opened or the drum inspected, then `leave_after_s`); SpecError for a machine in a shared building laundry (use `station` with `in_unit: true`) |
| `textiles_laundry.unfamiliar_washer` | `washer`, `language` (de, fr, ...) | relabels programs as `p1..p12` with foreign words and icons on the panel; the manual (`read`) maps them to program kinds |

## 7. Spec recipes (per cluster)

Common setup: `{"op": "helper", "name": "textiles_laundry.station"}` (washer, dryer, products, rack, basket), spawn
the items with explicit soil/wetness/care (`{"op": "spawn", "var": "tee", "type": "t_shirt", "in": "{washer}",
"props": {"soil": {"stain_coffee": 0.4}, "worn": true}}`), and use `house.require: ["laundry_room"]` when a laundry
room matters. Machines: the washer runs 40-95 min, the dryer up to 2.5 h; give `budget.time_s` room for it and the
skills `run_washer`/`wait_cycle`. Prefer predicates on outcomes (soil, wetness, damage, folded, location,
`last_cycle`) over action traces.

* **collect_sort** - setup: worn garments/towels spread over bedrooms and bathrooms (`worn: true`, soil), hampers,
  a `sorter_hamper` with sections, owners on garments (`owner`, name tags as `label_text`). Success: every item
  `location` in the right hamper/section (`in` via `path ... location`), whites not with `red_new`, delicates in a
  mesh bag. Wrong plans: everything into one load (dye bleed), clean clothes into the hamper (ask `laundry:rewear`),
  damp towels balled in a closed hamper (musty).
* **prep** - setup: `textiles_laundry.pockets` with a pen/tissue/lipstick, `zipper: {"closed_frac": 0.0}` on jeans/jackets, bra hooks open (`fasteners.hooks.engaged: false`), rolled
  sleeves, a print tee (`care.inside_out`). Success: pocket contents not in the washer, `zipper.closed_frac gte 0.9`, garments
  `inside_out` per label, `sleeves_rolled` false. Wrong: wash with the pen (ink on the whole load), open zippers snag
  delicates.
* **stains** - setup: stains by class on fabrics (`stain_blood` on cotton, `stain_wine` on a colour, `stain_ink` on
  polyester, wax), products from the station or `enzyme_presoak`/`oxygen_laundry_booster`. Success: stain `not gte
  0.05`, `set_stains len_eq 0`, `damage len_eq 0`. Wrong: hot water on blood (sets), rubbing (spreads), chlorine on a
  colour, remover left to dry (ring), drying before checking (heat sets).
* **wash** - setup: station + load (fabrics with care labels), pods or detergent in a dispenser compartment (spawn
  liquid into `{main}` with `liquid`/`liquid_l`). Success: soil clean, `last_cycle.completed`, `last_cycle.dose_ratio`
  between 0.6 and 1.8, `suds` false, damage none, `shrunk lt 0.02`. Variants: wool (program `wool`, 20-30 C), sanitize
  for germs, US top loader (`run_washer temp: "cold"`). Wrong: hot normal wash of wool (felted), regular detergent in
  HE (SUD), new red shirt with whites, starting with a cat inside (`occupant` helper with station `in_unit: true`; injury), overloading.
* **machine_care** - setup: lint screen `soil.lint` 0.9 / `film` 0.6, washer `_biofilm` 0.6 with `odor`. Success:
  screen `soil.lint not gte 0.1`, `film lt 0.1`, washer `_biofilm lt 0.1`, no items in a `tub_clean`. Wrong: tub
  clean with clothes inside, peeling lint into the slot.
* **dryer** - setup: wet items (`wetness` 0.38-0.5, towels `_core_wetness`) in the washer or dryer, lint screen state,
  an oily rag, a rubber-backed mat, a wool sweater. Success: `wetness not gte 0.05` (and `_core_wetness`),
  `last_cycle.overheat_s eq 0`, damage none, items out of the drum (`location ne {dryer}`) and not wrinkled
  (`wrinkled lt 0.3`). Wrong: clogged screen (fire), high heat on wool/rubber, leaving the load in the drum.
* **air_dry** - setup: `station` with `need: ["rack"], rack_ready: true` (generated stations keep the folded rack on wall hooks; without `rack_ready` a plan lifts it off, places it on the floor and opens it), a yard with a `clothesline`
  (`p.mount`) and `clothespin_bag`, weather and scheduled weather events (`config.events` with `weather.condition`
  rain at a time). Success: dry and `stretched lt 0.05`, `musty is_false`, `blown_down` absent, laundry indoors before
  the rain (`location` in a room). Wrong: hanging a wet sweater (stretch), too few pins (blown down, dirty), leaving
  it out in rain, rack blocking a doorway, drying on a heater.
* **fold** - setup: `textiles_laundry.linens` or spawned dry items in a basket, a table, a drawer/linen closet; a
  `household.fold_style` preference or an already folded example. Success: `folded` eq style (or `in` a list),
  `fold_quality.neat` true, `fold_quality.lumpy` false for fitted sheets, item `location` in the drawer, drawer
  capacity respected. Wrong: folding a damp towel (sours), folding a king sheet in the air (misaligned), folding on
  the floor (soil), ignoring the household style.
* **pair** - setup: `textiles_laundry.sock_pile` (look-alikes differ only in hidden `size_label`/`brand`), shoes with
  `side`/`size`. Success: for each pair `{"any": [{"path": "entities.{pair1_b}.location", "op": "eq", "value":
  "{pair1_a}"}, {"path": "entities.{pair1_a}.location", "op": "eq", "value": "{pair1_b}"}]}`; orphans in an orphan bin
  (or reported). Wrong: pairing by colour only (look-alike mismatch), balling socks (stretched cuffs).
* **hang_store** - setup: `textiles_laundry.closet`, hangers, garments with `needs_hanger`, silk blouses, a heavy coat,
  an `evening_gown` with a low rod, coat hooks (`towel_bar`, or a core fixture with `category: hooks`), dresser
  drawers. Success: `location eq {closet}`, `relation eq "hung"`, `hanger_kind in ["velvet", "padded", "clip"]` for
  silk, `top_button is_true`, `slipped_off exists false`, `hem_on_floor` falsy, knitwear folded not hung, damage
  none. Wrong: silk on a wire hanger, coat on a wire hanger (bent), gown on a low rod, hanging knits, more than
  the hook can hold.
* **linens_towels** - setup: linen closet shelves, towel bars, guest rooms, `linens` helper. Success: towels
  `location` on the bar/shelf, folded, clean (`worn` false), dry; blanket delivered to the person's room. Wrong: damp
  towels stacked (musty), worn towels put back.
* **beds** - setup: `textiles_laundry.bed` with `state` bare/made/unmade and `linens` for the matching size plus a
  decoy (twin fitted for a queen bed); a phone under the duvet (`relation: under`). Success: `bed_state.layers` eq
  list, `bed_state.fitted_corners eq 4`, `bed_state.order_ok`, `bed_state.duvet_offset_cm lt 10`, `made` true,
  `bed_state.items_in_bedding len_eq 0`, `bed_state.wrinkle lte 0.1`, `made_style.duvet eq ...`, fitted
  `face_up eq "finished"`, the phone `buried_in_bed exists false`. Wrong: wrong-size sheet
  (pops off), crosswise sheet, seams up, stuffing the duvet (bunched), making the bed over the phone, yanking.
* **press** - setup: `station` with `need: ["iron", "board"], iron_on_board: true` (the iron rests on the open board,
  plugged in), garments with `wrinkled` 0.7 and fabrics (cotton shirt, polyester blouse, wool trousers). Success:
  `wrinkled lt 0.2`, damage none, iron `on` false and `orientation eq "upright"`. Wrong: cotton setting on polyester
  (melts), iron left on (safety), steaming suede, shaver on cashmere.
* **sewing** - setup: `textiles_laundry.sewing_kit` (frayed spool, fine needle), shirt with `buttons.<slot>.state`
  missing/loose, split seam (`seams.<id>.state: split`). Success: `buttons.<slot>.state eq "ok"`,
  `buttons.<slot>.thread_match is_true`, `damage len_eq 0` (no plackets sewn together), seam closed, needle
  `location eq {pincushion}`. Wrong: catching both plackets,
  pulling a chain stitch, leaving the needle out (loose_needle invariant).
* **shoes** - setup: shoes with `soil.mud`, `fabric` leather/suede/patent/mesh, laces (`shoelace` with `length_cm`),
  a `boot_tray`, wet shoes (`wetness`). Success: `shine gte 0.7` for leather, suede `damage len_eq 0`,
  `lacing.pattern eq ...` and `lacing.ends_short is_false`,
  shoes paired on the rack, wet shoes on the tray (not on hardwood) and dry. Wrong: polish on suede, buffing before
  haze, wet boots by the heater (heat cracked), muddy shoes on the carpet.
* **seasonal_pack** - setup: storage bins (`openable`, `moth_repellent` option), wool sweaters dirty/clean, a suitcase
  (admin's luggage or a core container), then `{"op": "event", "effect": "textiles_laundry.storage_months", "args":
  {"months": 6}}` or `transport`. Success: no `moth_holes`, no `set_stains`, `wrinkled lt 0.3` after transport.
  Wrong: storing dirty wool, packing a dress shirt rolled, muddy shoes unbagged.
* **home_textiles** - setup: tablecloth on a set table, `round_tablecloth` decoy, bath mats (rubber backed, coir),
  shower curtain liner, curtain panels on a rod (core `fixture` with `curtain_rod: true` and a panel with
  `curtain: true, hooks_total`), cushion covers on foam inserts, mattress/pillow protectors, launderable bags, pet
  bedding (`launderable`, foam insert). Success: clean, `damage len_eq 0`, mats `laid_flat`, curtains rehung
  `sagging` false, tablecloth `drop_diff_cm lt 5`, `shape_match` true. Wrong: foam insert in the washer
  (destroyed), coir mat machine-washed, rubber backing on high heat, damp mat back on hardwood, curtains with half
  the hooks, yanking the cloth off a set table.

## 8. Cluster -> catalog-id table (all 237 owned entries)

| Cluster | Catalog ids (title) |
|---|---|
| **collect_sort** - Collect and sort (hampers, owners, colour/fabric groups) (23) | `T-f3268d97ac12b3-01` Collect dirty clothes.<br>`T-14d7c80bfd8677-01` Collect towels.<br>`T-e90c16c6bc69e5-01` Collect bedding.<br>`T-e33be7f5f32230-01` Sort whites.<br>`T-9ac6c0d1468ae0-01` Sort darks.<br>`T-c32284261135cd-01` Sort colors.<br>`T-6e3fbf384a654f-01` Sort delicates.<br>`T-32483706f17670-01` Sort towels.<br>`T-5a06c8d10ad8dd-01` Sort bedding.<br>`T-084dc9f40872a2-01` Sort by fabric type.<br>`T-2105f441a1b598-01` Sort clothing by owner.<br>`T-5d96dfe5da006e-01` Sort clothing by category.<br>`T-5d2b884e69337d-01` Sort clothing by season.<br>`T-5374cf0647f596-01` Pick up clothing.<br>`T-6d32c6346ad15e-01` Put dirty clothing in hamper.<br>`T-22b471895da33c-01` Sort clothing.<br>`T-900573e81f8733-01` Collect used towels.<br>`T-8076196f8c7c50-01` Collect outdoor towels.<br>`T-a8bd4f40e41f1a-01` Put dirty clothing in hamper.<br>`T-03ac25bf9118d2-01` Put dirty towels in laundry.<br>`T-cdf8e8b491b1dd-01` Put dirty clothes into hamper.<br>`T-cc99124ac17ed6-01` Put clothing in hamper.<br>`A-4681138ceba184-01` “Separate everyone’s laundry.” Requires learning ownership from size, style, location, history, and possibly labels. |
| **prep** - Pre-wash prep (pockets, zips, sleeves, inside-out) (5) | `T-6eed74e7a248a5-01` Check pockets.<br>`T-09c9659677296e-01` Empty pockets.<br>`T-3510161a14abbf-01` Close zippers.<br>`T-d27e9a5bc8a108-01` Unroll sleeves.<br>`T-607d602979b000-01` Turn garments inside out when required. |
| **stains** - Stains, pretreatment and soaking (4) | `T-cc81e71dc2182e-01` Treat stains.<br>`T-e5ed5935a220e4-01` Apply stain remover.<br>`T-7f2ed8ec4fbb5c-01` Soak stained clothing.<br>`A-4f19662eba74a4-01` “Get this stain out.” Requires identifying probable stain/material combination and choosing an appropriate treatment. |
| **wash** - Washer operation (load, dose, program, temperature, start/pause, whole loads) (17) | `T-7a469df881e6f5-01` Wash tablecloth.<br>`T-68b7ed35e37412-01` Load washing machine.<br>`T-a9ee6ed74b62b5-01` Add detergent.<br>`T-aa8d55683e7bbf-01` Add fabric softener.<br>`T-fd8180921969bd-01` Add bleach when appropriate.<br>`T-bc6d4e8b2f4edc-01` Select wash cycle.<br>`T-2f28833adcc133-01` Set temperature.<br>`T-28ff695081cd2c-01` Start washer.<br>`T-3d2aeb4d99bc5a-01` Pause washer.<br>`T-61ee353e7c7fbf-01` Wash guest bedding.<br>`T-b02a184313dec9-01` Wash cleaning cloths.<br>`T-e4682500dc1a56-01` Wash table linens.<br>`T-affd7ef6d042cb-01` Wash towels.<br>`T-62e5b719ca0f22-01` Wash clothing.<br>`A-9e6a1bb9e11576-01` “Wash these without ruining anything.” Requires reading tags, recognizing fabric, colors, special treatments, and machine capabilities.<br>`A-d048c6c15f805e-01` “Tell me when the laundry’s done.” Requires monitoring cues (sound, status light) and choosing when and how to notify.<br>`A-2e242502dccd12-01` “Figure out how this washer works.” Requires reading the panel and manual, then testing options safely. |
| **machine_care** - Washer and dryer upkeep (lint screen, drum, tub clean) (3) | `T-027dcb1ea75b97-01` Clean dryer lint filter.<br>`T-d5bb30d1d9e89b-01` Clean washer drum.<br>`T-d70dbb732d127b-01` Clean washer. |
| **dryer** - Machine drying (transfer, sheets, programs, dryness checks) (8) | `T-cdc6f993d6c3ea-01` Transfer wet laundry to dryer.<br>`T-03841840c256e1-01` Add dryer sheet.<br>`T-d78feb399fcde4-01` Select dryer cycle.<br>`T-af96a334296669-01` Start dryer.<br>`T-2487906fef06f6-01` Remove dry clothes.<br>`T-ba18cc4b56e89d-01` Check whether clothes are dry.<br>`T-65c7debf7bf0c5-01` Dry cleaning cloths.<br>`A-d66994a566899c-01` “Dry this without shrinking it.” Requires garment-care reasoning. |
| **air_dry** - Air drying, outdoor drying, airing and shaking out (12) | `T-6f396d44e1f4f6-01` Hang-dry clothes.<br>`T-73c1ba41301be1-01` Use a drying rack.<br>`T-7a3e3247a1204b-01` Hang clothes on a clothesline.<br>`T-c098c30df41f8a-01` Remove clothes from clothesline.<br>`T-767ad1cbc1ebbd-01` Dry wet umbrellas.<br>`T-a10b2785fd40be-01` Remove wet shoes.<br>`T-2e0fecc5912cac-01` Shake blankets.<br>`T-d8928f97153c23-01` Air out blankets.<br>`T-18c059cadf44c9-01` Bring laundry inside before rain.<br>`T-a2637b96797442-01` Put umbrella in drying location.<br>`T-161e04f74e36e0-01` Bring blankets outside.<br>`T-72dddd3bf5d4d9-01` Bring blankets back inside. |
| **fold** - Folding and unfolding (garments, towels, linens, odd shapes, personal styles) (28) | `T-4e432cd05581a9-01` Fold tablecloth.<br>`T-b4bc9ddbd5292d-01` Fold shirts.<br>`T-4d42bef56365cf-01` Fold pants.<br>`T-175f32f532d8bc-01` Fold shorts.<br>`T-3018674e806f0a-01` Fold underwear.<br>`T-f9cc5ba944885c-01` Fold socks.<br>`T-10ad929bcb5902-01` Fold towels.<br>`T-240fcce7902629-01` Fold sheets.<br>`T-8f187bdbe88e57-01` Fold pillowcases.<br>`T-6cc42e6fd77a37-01` Fold blankets.<br>`T-6cbc903260d06d-01` Fold blankets.<br>`T-9626643f171bb0-01` Fold clean clothing.<br>`T-98838b7b2e5d99-01` Fold towels.<br>`T-c4ccb198afe8b0-01` Fold throw blankets.<br>`T-1e722ad13b8589-01` Fold towels.<br>`T-70f12b66fb4914-01` Fold blankets.<br>`T-040809b2de554d-01` Fold table linens.<br>`T-88539c3e470d67-01` Fold workout towels.<br>`T-25e416fee5e908-01` Fold garment.<br>`T-56c4983e6f5ee8-01` Fold clothing.<br>`T-70ff81accc646a-01` Fold blanket.<br>`T-dee0c43c78c87b-01` Fold blankets.<br>`P-23239603a624e3-01` Fold.<br>`P-cc2b11c4b2613d-01` Unfold.<br>`A-8f0fe047b2c441-01` “Fold this the way I normally fold it.” Personalized manipulation.<br>`A-90c4451dc45a2e-01` “Fold this weird-shaped thing.” Requires handling unseen deformable objects.<br>`A-8220afe27c03e7-01` “Fold a fitted sheet.” Difficult even for many humans.<br>`A-360e2e84191d89-01` “Figure out which corner goes where.” General correspondence reasoning. |
| **pair** - Pairing socks, shoes and gloves (3) | `T-fd9e18ceb20286-01` Pair socks.<br>`A-1895a81a5051bd-01` “Find the matching sock.” Requires visual similarity under deformation and partial occlusion.<br>`A-c53f81683e2b20-01` “Pair all these shoes.” Same challenge with orientation, size, color, and style. |
| **hang_store** - Hanging, closets, drawers and coat hooks (putting clothes away) (25) | `T-868b9cc704adf1-01` Hang shirts.<br>`T-9d494765260936-01` Hang jackets.<br>`T-2f5b69634e9a0b-01` Hang dresses.<br>`T-69477e4f4d8592-01` Hang pants.<br>`T-f99b96068896b0-01` Put clothing into drawers.<br>`T-1310bd917abbb2-01` Put clothing into closets.<br>`T-39fbb524247082-01` Hang clean clothing.<br>`T-940300c8d47623-01` Organize dresser.<br>`T-269f080f72efe4-01` Organize closet.<br>`T-fa725df43f429e-01` Hang coats.<br>`T-e98bd98b0597f7-01` Remove coats from hooks.<br>`T-100dbc708f2768-01` Put away hats.<br>`T-59f672536799d4-01` Put away gloves.<br>`T-b356a4c091c0e3-01` Organize coat closet.<br>`T-b0fddf3ce3f062-01` Organize children's clothing.<br>`T-5bda780b3e1752-01` Lay out clothing.<br>`T-4b7fd57257912f-01` Hang guest coats.<br>`T-a524187882ee27-01` Organize coats.<br>`T-01c24d5fe60821-01` Hang coat.<br>`T-fb56e2bffcec99-01` Gather coat.<br>`T-4213b712c5f092-01` Hang garment.<br>`T-1d7eb50f094a6a-01` Put clean clothes away.<br>`T-896a49d074bf09-01` Organize closets.<br>`A-d9e82606eef21a-01` “Put these clothes somewhere they won’t wrinkle.” Requires fabric/clothing knowledge.<br>`A-1d94511ec2a679-01` “Hang whatever should be hung.” Requires garment classification. |
| **linens_towels** - Towels, linen closets and blankets (restock, put out, fetch) (17) | `T-152ca0cd08cf9a-01` Put away blankets.<br>`T-bf5de5e9ffe5be-01` Restock towels.<br>`T-06e28e6bd65d6c-01` Hang towels.<br>`T-df4248b006a7e8-01` Replace hand towel.<br>`T-a2413c7ef5cd03-01` Replace bath towel.<br>`T-769ecfec875d2d-01` Organize linen closet.<br>`T-31f5cea2a51c36-01` Retrieve blanket.<br>`T-78f2e9ada0626a-01` Put out clean towels.<br>`T-ecd670e1e4838c-01` Put clean towels out.<br>`T-9e826644f21d8e-01` Organize linens.<br>`T-b1327ba2cf1034-01` Organize towels.<br>`T-ef9e803afa7c64-01` Prepare blankets.<br>`T-a5825d9912266a-01` Put out extra blankets.<br>`T-de886472534cbd-01` Set out clean towels.<br>`T-8a56293e10a6e5-01` Store table linens.<br>`T-53853743b1afef-01` Fetch blanket.<br>`T-a3ac1798006848-01` Bring blanket. |
| **beds** - Beds (strip, dress, duvet covers, tuck, pillows, make, hotel standard) (21) | `T-ed45170be6256e-01` Make a bed.<br>`T-c3c0c01b9d1814-01` Strip a bed.<br>`T-5d0b6af665a9ae-01` Remove pillowcases.<br>`T-76746ee738bfe5-01` Remove fitted sheet.<br>`T-dc7e8967cec0c7-01` Remove top sheet.<br>`T-52449341f8621c-01` Remove duvet cover.<br>`T-e40eb6a314e5b6-01` Put on fitted sheet.<br>`T-883875c535eac6-01` Put on top sheet.<br>`T-203d94db7d8a50-01` Put on pillowcases.<br>`T-973ed90218214d-01` Put duvet into duvet cover.<br>`T-de9328e7828889-01` Arrange pillows.<br>`T-c6537a9d8249e0-01` Tuck sheets.<br>`T-ca528fede186ba-01` Make guest bed.<br>`T-0a7593cbd7c2d0-01` Make bed.<br>`T-d797839430c4d2-01` Install mattress protector.<br>`T-e51371cb0aa407-01` Arrange pillow.<br>`T-05f30bba821472-01` Arrange pillows.<br>`T-83442cab7a5bbb-01` Change bed linens.<br>`A-1d99b26db4e889-01` “Make the bed look hotel-quality.” Requires understanding a qualitative standard rather than just task completion.<br>`A-6d278a65dedad2-01` “Put this fitted sheet on the bed.” Deformable-object manipulation with corners that can be misidentified.<br>`A-3c626fe6eec852-01` “Put this duvet into the cover.” Requires large deformable-object coordination. |
| **press** - Ironing, steaming, lint and pilling (8) | `T-f0ebed4776c708-01` Iron clothes.<br>`T-b52e72f0601d47-01` Steam clothes.<br>`T-9d2e40699ead82-01` Remove lint.<br>`T-e4624a716f323a-01` Use lint roller.<br>`T-61aa5e115e3240-01` Remove lint.<br>`T-8cabcfd90d8c79-01` Remove pilling with appropriate device.<br>`T-774e01576b70e1-01` Steam garment.<br>`T-7d4006bda247ba-01` Iron garment. |
| **sewing** - Sewing and thread repairs (7) | `T-ae4651b88eca7d-01` Sew on a loose button.<br>`T-d4bcad90dafe33-01` Repair a simple tear.<br>`T-5f621b2236b612-01` Remove a loose thread.<br>`T-efcbd6f8bb6eaf-01` Thread a needle.<br>`T-74c37f23b52cd9-01` Sew on button.<br>`T-17a98958c21a27-01` Repair small seam.<br>`T-9931b4b1239422-01` Trim loose threads. |
| **shoes** - Shoes (clean, polish, laces, racks, storage, wet shoes) (18) | `T-f5258487631c3d-01` Polish shoes.<br>`T-5c25605a57a6d1-01` Clean shoes.<br>`T-9b1e7de8090e8f-01` Wash shoelaces.<br>`T-b439f2a07054f8-01` Store shoes.<br>`T-63480d18c2a99f-01` Pick up shoes.<br>`T-cbb26cf82d12d1-01` Arrange shoes.<br>`T-aa1f8e0d242e13-01` Put shoes on rack.<br>`T-5603ae9cf0263a-01` Organize seasonal footwear.<br>`T-bbc5a741f9db9f-01` Wash muddy boots.<br>`T-e753d1f47fa5dd-01` Organize shoes.<br>`T-822353cbacec95-01` Put shoes away.<br>`T-1798883a33054a-01` Gather shoes.<br>`T-deac4a96072a72-01` Polish dress shoes.<br>`T-13be1514a30488-01` Clean sneakers.<br>`T-b4104882051f23-01` Replace shoelaces.<br>`T-d0c2168f2d3689-01` Lace shoes.<br>`T-5b0931f085a67d-01` Store seasonal shoes.<br>`A-21fd66f1267198-01` “Make these shoes look better.” Different materials require different cleaning methods. |
| **seasonal_pack** - Seasonal rotation, long storage and travel packing (15) | `T-1b679ad306c3a2-01` Rotate seasonal clothing.<br>`T-46172acd4527d5-01` Rotate seasonal clothing.<br>`T-834468f7ec19c8-01` Store winter clothing.<br>`T-fd806dbf7d28a4-01` Bring out warm-weather clothing.<br>`T-d5ee66c7123ace-01` Bring out cooler-weather clothing.<br>`T-1b315d3b079ac0-01` Store summer clothing.<br>`T-b11255e97efff6-01` Rotate winter clothing.<br>`T-3cd953e27116ae-01` Pack clothing.<br>`T-3cf88534e45717-01` Pack clothing for travel.<br>`T-75e689a6067bd9-01` Roll clothing.<br>`T-ef59d17a2cbd1e-01` Pack shirts.<br>`T-e22b06b39cd64c-01` Pack pants.<br>`T-0a7b746c3627c1-01` Pack underwear.<br>`T-9d64ecfcc8fc04-01` Pack socks.<br>`T-09033d1584a8a3-01` Pack shoes. |
| **home_textiles** - Household textiles (mats, tablecloths, curtains, cushions, protectors, rugs, bags, pet bedding) (23) | `T-e007461cf96d7e-01` Wash mats.<br>`T-e1ad98d00db36b-01` Replace tablecloth.<br>`T-9936321c3f2a79-01` Replace bath mat.<br>`T-236fefcc36f3ba-01` Wash bath mat.<br>`T-90ee3933c1b5f7-01` Clean shower curtain.<br>`T-f167323b1fdec4-01` Clean pet bedding.<br>`T-4b4968deffdefd-01` Wash pet blankets.<br>`T-ba4ef9d20953ce-01` Clean cushions.<br>`T-c718d18580d686-01` Add tablecloth.<br>`T-a31e3d6d0f0a3e-01` Wash curtains when appropriate.<br>`T-8a1516722b4a67-01` Remove curtains.<br>`T-119e61e8167f5c-01` Rehang curtains.<br>`T-aa2449b8193c6c-01` Wash removable cushion covers.<br>`T-65d01ed7ba2abf-01` Remove cushion covers.<br>`T-c28d59c9949b8b-01` Replace cushion covers.<br>`T-9985fb281e9fa7-01` Wash pillow protectors.<br>`T-1929001531a238-01` Wash mattress protector.<br>`T-1fc3db0997d55b-01` Wash reusable shopping bags.<br>`T-47a75b85ac9df4-01` Clean backpacks.<br>`T-7486f606fae0b3-01` Wash pet bedding.<br>`T-c1cb66635fa87b-01` Wash rugs if appropriate.<br>`T-7f3e82be79f397-01` Wash curtains if appropriate.<br>`T-d5a78cee616ef3-01` Wash shower curtain. |

## 9. Interop notes and limits

* Other domains' objects become laundry when flagged (`launderable`, `fabric`, `care`); pinned laundry is `secured`
  so generic wind models leave it to the pin model, and laundry another domain's wind moves anyway lands dirty and
  creased like a blow-down. Moisture is single-sourced (see section 0).
* Umbrellas and luggage dry in admin_inventory's storage system; the fabric system skips them (it only adds drips).
* Liquids in dispenser compartments are read by name (`laundry_detergent_he`, `laundry_detergent`,
  `fabric_softener`, `chlorine_bleach`, `oxygen_bleach`, `vinegar`); a pour verb that keeps these names fills them.
* Verbs this domain relies on from others: zip/unzip/fasten/unfasten (manipulation; shared `zipper`/`fasteners`), pour
  (plumbing/cleaning), plug/unplug (electrical), vacuum and scrub (cleaning), step stools for high rods
  (navigation). Without them, specs set those fields in setup.
* Washers and dryers state their drum load as `wash_capacity_kg` / `dry_capacity_kg` (kernel v3.0: the kernel reads a
  fixed container's `max_load_kg` as the structural rating of its bottom, which the overload law would collapse);
  `max_load_kg` on racks, bars and hooks is their hang rating (the kernel's hang law reads it); hangers use
  `hang_load_kg` so stacking models do not read a hanger's hanging limit as a crush limit.
* Integration round: with the pets domain loaded, a pet napping in a drum (`occupant` helper) scrambles out as soon as
  the door opens (pets owns the behaviour; this domain's `leave_after_s` delay only applies without it); a glass
  front-loader door shows it to `inspect` before opening. With climate loaded, room `humidity` and `temperature_c`
  evolve (a closed laundry room on a summer day settles near 60 % RH), which this domain's evaporation factor reads,
  so indoor air drying can take noticeably longer than at the 45 % default.
* Heel rest (adoption round, open in phase 1): an iron put back upright on its heel rest touches the board with its
  cool heel, not its soleplate, and this domain claims that contact through the `phys.contact_heat` hook
  (`physical.heel_rest`), which the kernel's contact_heat law honours and which other domains' legacy copies of the
  law (while it is deferred) ask through the kernel's `phys.thermal.contact_claimed`. admin_inventory
  (`handling._hot_contact`) and appliances_cooking (`handling._marks_support`) ask it now; manipulation's place hook
  (`handling.py`, the "heat damage to finishes" block) does not yet, so a hot iron set upright on the ironing board
  (cotton cover, 120 C limit) still gets `damage.scorch` and counts household damage: the ironing spec
  `T-7d4006bda247ba-01` fails on seeds 0 and 41 (variant 0 under the hashed variants) until it does. It passes with
  `contact_heat` forced on, and in its own mode with only manipulation's copy made to ask `contact_claimed` (phase 1,
  section 11; `PhaseOneStabiliseTests` pins this domain's half of the contract with the law deferred and active).
  Manipulation's copy asks it since the phase-1 finish: the ironing spec passes 5/5 seeds.
* Weather on dry textiles that are not being dried (limit): rain wets every exposed textile (section 3), but dew,
  sun fade, insects and pollen reach only textiles the fabric system already processes (wet, set out with
  `hang_dry`, soaking, ...). A dry dark shirt lying on a garden chair does not fade, and a dry doormat stays dry at
  night; extending them to every outdoor textile would also age outdoor furnishings (the generated front-yard
  doormats) on idle days, which needs its own review of exogenous changes.

## 10. Tier-1 adoption (kernel v3.0 physics, camera and body; v3.1 sweep)

The adoption round of docs/design/TIER1_INTEGRATION.md section 6 ("textiles_laundry" and "Every domain also").

### Physics (docs/PHYSICS.md; adoption protocol TIER1_PHYSICS 10.3; module `physical`)

* **Fibre properties through `phys.props`** (`physical.fabric_props`): most garments carry `material: "fabric"`,
  which the kernel reads as cotton. The hook maps the domain's `fabric` to the kernel material class
  (`FIBRE_MATERIAL`: cotton, linen, denim, terry, flannel, velvet, corduroy, rayon, jute and coir to the cotton class;
  wool, cashmere and down to wool; silk, satin, lace, polyester, nylon, acrylic, spandex, fleece, microfibre and the
  other synthetics to synthetic fabric; PEVA, vinyl, PU membranes and anything `coated` or `waxed` to coated fabric;
  foams, leathers and rubber to theirs) and from it takes the absorption (litres per kg of dry mass, at most 90 % of
  the outline: cotton 2.5, wool 3, synthetics 0.6, microfibre 4, coated 0.05 and waterproof) and the friction (silk,
  satin and nylon are slippery: 0.3). The heat limit as a support is 120 C or the fibre's scorch point when lower
  (a hot body resting on fabric browns it below the temperature a moving iron glides at); a thermoplastic fibre's
  melting point is its iron-glazing temperature from `model.FABRICS` (polyester 180 C, nylon 170 C, acrylic 160 C,
  fleece and microfibre 150 C, PEVA 90 C); natural fibres scorch and have none. Explicit record values
  (`absorb_l_per_kg`, `friction_mu`, `melt_c`, heat limits, `waterproof`) win. The hook leaves the mass alone:
  `Phys.mass_kg` is the kernel's dry own mass, which `liquids.absorb_capacity_l` divides the absorption by (a wet mass
  there would turn a soaked leather boot from a soaking body into one that sheds a film). This deviates from
  TIER1_INTEGRATION 6's "phys.props (wet mass = dry + wetness x absorb)", whose purpose the kernel cannot serve from
  props anyway (next bullet).
* **Wet mass** (`physical.wet_mass`): dry mass + wetness x the litres the textile holds when saturated (a soaked bath
  towel of 0.55 kg weighs 1.93 kg), used for this domain's loads: a drying rack's lopsided tip, hooks pulling out
  (while the hang law is deferred), a hanger bending under a wet coat. The kernel's statics (`support.own_mass`, read by
  lifts and the hang, crush and overload laws) count a record's mass and a container's liquid but not the water a
  soaking body holds, so the kernel's laws and lift checks see a soaked towel at its dry 0.55 kg (kernel request).
  Water a towel catches under the washer's drain filter is taken up through the kernel (`physical.soak_into`:
  `liquids.wet`, up to the fibre's saturation).
* **Melt** (law `melt`): `iron` melts a fabric hotter than its kernel melting point (`physical.melt_point`, the same
  number the melt law uses) and scorches the others above their scorch point; nothing in the domain duplicates the
  law (the soleplate does not raise the garment's own temperature).
* **Contact heat** (law `contact_heat`): `physical.heel_rest` claims the contact of an iron or steamer standing on its
  heel rest (only a face-down soleplate touches); the ironing board's cotton cover has a 120 C limit
  (`surface_heat_limit_c`). While the law is deferred the domain marks what lies under a face-down hot iron
  (`scorched`); once it is active the kernel does (`scorch`) and the domain keeps the burning smell and the ignition
  after ten minutes without auto-off.
* **Hang** (law `hang`): racks, bars, wall hooks and the clothesline carry hang ratings in their type data
  (`max_load_kg`, hidden; the clothesline 20 kg). The domain's own pull-out of hooks and a rack's overload apply only
  while the law is deferred; a rack's lopsided tip stays the domain's.
* **Friction**: a garment slides off its hanger when the kernel's dry friction between them is below 0.32
  (`physical.slips_off_hanger`; silk on plastic 0.25, on velvet flocking 0.55); non-slip hangers, clips and strap
  notches hold it; strapless garments slip from any plain hanger. Velvet and padded hangers carry `friction_mu`.
* **Rain** (law `rain`): while the law is active and rain falls by the kernel's measure (weather `rain_mm_h` > 0,
  `physical.kernel_rain`) the kernel wets what stands in it (`environment.rain_wet`: the surface fully wet within
  2 minutes); the domain then only keeps thick seams as wet as the surface and stops drying until the rain stops. A
  weather record that says only `condition: "rain"` (no rate: a scenario event can set it so) is rain to this domain
  but not to the kernel, so the domain keeps wetting the laundry itself then (0.95, as before the law). Exposure is the
  kernel's rule for both (`physical.out_in_weather`, `environment.exposed`): laundry in a closed bin (as before), under
  a cover or under a table out in the yard stays dry; an outdoor room
  marked `covered` (the domain's flag) or `roofed` (the kernel's) shelters it, the former through the
  `phys.exposed` hook `physical.covered_room`. Wind: the kernel never moves hung laundry (a fixed relation), so the
  clothespin model stays the domain's; laundry another domain's wind carries off still lands dirty.
* **Moisture**: the kernel's wetting (`liquids.wet`, the rain law) raises a soaking body's `moisture` with its wetness;
  this domain dries textiles, so its fabric system keeps `moisture` no higher than the wetter of surface and seams
  (a towel that took up drain-filter water reads dry to a touch probe once it has dried).
* **Stack** (law `stack`): a set-up drying rack or ironing board is furniture (`portable: false` while open; fold it
  before carrying it: "Fold the rack first (close it) to carry it"), so a shirt laid on the board is not a loose stack
  (and the kernel gives an open stand its `open_size_m`); a needle stuck in a pincushion is `secured` to it. A folded
  textile laid on a folded stack in a drawer, bin or shelf is refused when the stack would not clear the drawer top or
  the shelf above ("the drawer/shelf would not close"): `fold.textile_stack_fit` measures it with the kernel's own
  numbers (`statics.clearance_above_m`, `statics.stack_height_m`; phase 1: it was a private copy of the clearance rule,
  review A1, which overestimated the room on a cabinet's top shelf), so it refuses exactly the stacks the law would
  topple, before anything moves, with the law deferred or active; the container's volume check stays the domain's.
* **Kernel consequences instead of private ones**: a cloth pulled off a table drags what stands on it over the edge
  (`physics.fall`: glass breaks on tile, a vase may survive carpet), a cloth slid under things jolts them by the
  friction between item and cloth (`physics.knock`); `textiles_laundry.transport` hands its drops to
  `physics.transport`.
* **Data**: bulk densities fixed (garment bag, laundry bag, mesh bag, tights), the abstract `laundry_product` has a
  mass and size; every member of the `laundry_jug` look-alike group is the same 2.5 L jug: 15 x 10 x 28 cm, 0.17 kg
  empty, 3.0 L outline, 2.95 L brim, a box with its centre of mass at mid-height and a 4 cm screw neck (`mouth_frac`
  0.3); only the liquid and the label differ (phase 1, review L2/L4: the bleach jug was 0.30 m and, through the
  kernel's `bottle` noun row, a narrow-necked cylinder; the booster 0.20 m; so pixels, a swept knock, a pour or a tip
  told them apart before the label was read). Washer and dryer drum loads are `wash_capacity_kg` /
  `dry_capacity_kg` (read tier).
* **Ready to flip** (TIER1_PHYSICS 10.3; `lawbook.forced`, every domain loaded): all 19 deferred laws are ready,
  `stack` included since phase 1 (laws.json now lists this domain under `stack` and `melt`). The 151 tests pass with
  `stack` forced on and with all 19 deferred laws forced on together, and both spec files (15 specs x seeds 0, 1, 2,
  41, 97, own modes, generation 2) pass with `stack` alone forced and with all 19 forced together (15/15 each, re-run
  at the phase-1 finish; at phase 1 the ironing spec failed with `stack` forced as it did unforced, section 9): carry,
  contact_heat, crush, edge, furniture_tip, handle, hang,
  melt, open_spill, overload, phase, rain, setdown, slope, stack, thermal_shock, thermal_tau, wet_contact and wind.
  `stack` was not ready at the adoption round (the kernel toppled a receipt lying wholly on a tray: fixed by the
  kernel's "a member wholly on the one below stays" rule and K-FIX's top/contact sizes) and the domain's clearance
  copy now uses the kernel's numbers (bullet "Stack").

### Camera (TIER1_CAMERA; module `views`, `signals`)

* View blocks: washer and dryer cabinets opaque (`porosity` 0) with 10 mm displays and 4 mm rating labels; a glass
  porthole door shows the drum while closed in geometric mode (the episode start marks glass-door machines
  `transparent` there only; room mode keeps today's rule). Drying racks are thin rails (porosity 0.85) whose record
  view porosity follows the laundry hung on them in geometric mode (`views.refresh_rack_view`). Ironing boards have a
  4 cm slab over open legs; hampers, a sorter hamper and its bags open at the top; care labels print at 2.5 mm
  (`text_m`), so `inspect` and `read` show them only from the wrist camera's range or held (`ctx.read_check`); a bath
  mat's label faces the floor (`faces` gating). Brand and size marks (4 mm) need the same close look.
* Visual events through `camera.visual_event` (`signals.seen`): rack tipped, laundry blown down, a garment slipped
  off its hanger, hooks pulled out, fire, a laundry puddle, bedding dragged off, a fitted corner popped, things
  knocked by a cloth. The event log is unchanged in room mode.
* The laundry observer's `appearance` cue ("visibly wet", "dripping wet") needs the colour cameras (`body.imaging`) and,
  in geometric mode, the thing seen at recognize level or better; a large stain (level 0.3 or more) shows as
  "stained" to a geometric camera that identifies the thing (small stains need `inspect`).
* What the robot perceives with the geometric camera, in short: a garment on the folding table seen from a metre away
  shows its colour and kind, a large stain and a wet look, never its care label, brand or size marks (inspect from
  there omits them); at the table (wrist camera) or held, the labels read as in room mode. A loaded drying rack hides
  what is behind it; a glass-door washer shows its load (and a pet inside) while closed; a hamper's contents need the
  lid open and a view from above.
* `tools/perception_audit.py ... --camera geometric` lists a loaded rack's `view` as a "hidden" field (seed 13): the
  record's own view dict (never-tier, TIER1_CAMERA 4.3) and the `view` key the kernel adds to every geometric record
  share a name; the observation shows the kernel's viewing geometry, not the porosity (an audit false positive,
  reported as a kernel request; admin_inventory's face-down papers carry record view dicts too).

### Body (TIER1_EMBODIMENT 4.8)

* `thread_needle` needs 0.5 mm of positioning (`ctx.require_precision`), beyond the humanoid hand's 2 mm: hold the
  needle threader (2.5 mm). `sew_button` needs 2 mm (a button's 1.5-2 mm holes in a dished face guide the point;
  TIER1_INTEGRATION 6 names 1.5 mm, which no humanoid hand could meet): fine in a lit room with a clean lens, refused
  with a smudged lens, in dim light, with greasy fingers or a weak arm. The sewing skill switches on a light when the
  room is dim and uses the kit's threader.
* Two-handed jobs (folding something over 1 m, spreading bedding, a duvet) are refused when an arm has failed.
* Unfolding a stand where the robot stands makes it step back: a walk (`ctx.require_mobile`, `ctx.motion`; the body
  pays for it, the camera frames it). Folding a paper-thin cloth lying flat follows the core thin-flat grasp rule.
* Carrying bedding down the stairs uses the robot's effective speed (`body.effective_speed`).
* Noise (`noise_db`): the garment steamer 58 dB, a fabric shaver 62 dB: refused in quiet mode.
* Lifting goes through core `pick_up` and the body (`ctx.lift_plan`, `ctx.check_lift`); the load it weighs is the
  kernel's `support.subtree_mass`, so a soaked duvet counts at its dry mass until the kernel counts absorbed water
  (kernel request). No verb of this domain takes a duration the command states (every duration is the motion's own,
  so `timing` stays "fixed" and careful speed slows them), none switches sensors, and none writes robot faults.

### Sweep (TIER1_SWEEP)

* Notices (`signals.notice`, kernel `w.notice`): a washer's end chime and a dryer's buzzer (`appliance_done`), an
  unbalanced spin (`spin_banging`, registered here, 75 dB), a tipped rack (`clatter`), hooks tearing out (`crash`),
  a fire (`smoke`, visual). `textiles_laundry.wait_cycle` listens for the end chime (an agent-legal wait).
* Routes (`routes`): canonical `ask` questions reach the `laundry:` answerer (fold style, bed style, tucking,
  fragrance, warmth, pillow layout, sorting, drawer map, hang or fold; per item: wash or rewear, stain, keep out of
  the dryer, hang or fold, donate, sentimental, owner; a person's size; guests). The catch-all routes of other domains
  no longer shadow them (phase 1 re-check of the adoption round's cross-domain bug): `manipulation.items` answers a
  thing without its hidden truth only as a weak fallback (`dialogue.weak`), `admin.keep` only things carrying its
  fields and the people domain's catch-alls are weak, so "is this shirt clean?", "can this go in the dryer?" and
  "should I hang or fold this?" about a garment reach `laundry.item` with every domain loaded
  (`PhaseOneStabiliseTests`); a bare `keep` or `intended` question about a garment is still manipulation's.
* Exact idle probes on `textiles_laundry.fabric` and `textiles_laundry.fabric_idle` (a run that wrote nothing with
  nothing pending leaves a note; the next substep is skipped while no textile, device or holder of a textile was
  written and no heater is on).
* Generation 2 (`furnish`): bedrooms from the residents' `home_room` (toddlers and infants included; garment sizes by
  age); an in-season garment in each dresser and the off-season clothes folded in a lidded storage bin in each closet
  (by `opts["season"]`); the machines keep clear of an electrical panel's working space. Generation 1 is unchanged.
* New types: `jacket` (denim), `sofa_cushion` (sewn-on cover, spot clean), `seat_cushion` (tie-on chair pad). The
  belongings' outerwear and footwear (`coat`, `puffer_jacket`, `light_jacket`, `hoodie`, `raincoat`, `sneaker`,
  `slipper`) were already this domain's types. GEN places the sofa cushions (`gen.furnishings`, order 101, 2-3 of
  them) after this domain's sofa dressing (a folded throw and two pillows, order 75), and the sofa's surface is a flat
  2 m strip with no layering, so one 60 cm cushion fits: `layout_audit --gen 2` notes "no spot for sofa_cushions" in
  every house (a note, not a problem; reported to GEN).
* Waits: `A-d66994a566899c-01` no longer polls the garment's hidden wetness (M-WAITS): the plans feel the garment at
  intervals and the dryer variant listens for the buzzer.
* Report vocabulary for laundry (pilled, shrunk, felted, snagged, stretched, dye transfer, scorched, melted, wrinkled,
  missing button, split seam, linty; states inside out / right side out, made / unmade, each with its evidence fields)
  belongs in `house_sim/data/vocab/textiles_laundry.json`, a file of the vocabulary's owner: requested from the main
  session with the proposed content (the core vocabulary already covers stained, torn, frayed, faded, worn, mildew,
  smelly, wet, damp, folded and unfolded).

### Specs (adoption round)

* Both files carry `"house": {"gen": 2}`: all 15 specs pass in generation-2 houses on seeds 0, 1, 2, 41 and 97 (own
  modes), except the ironing spec while the contact-heat issue of section 9 lasts; the three realistic specs also pass
  with `--camera geometric` (the sock spec declares it and has the camera wrong plan, where looking up close is the
  skill; the dryer-load and pocket specs are touch and opening skills whose plans already reach the things they feel).
* Identify first: the fitted-sheet folding (`A-8220afe27c03e7-01`) and bed-dressing (`T-e40eb6a314e5b6-01`) references
  inspect the look-alike sheet, and the stain spec (`T-cc81e71dc2182e-01`) reads the booster jug, before acting, so
  these oracle specs also pass under forced realistic perception (they failed there at C1). `T-28ff695081cd2c-01`
  failed under forced realistic on seeds 1 and 97: the kernel half (a cat inside the drum counted as lying on the
  floor) was fixed in the adoption round, and phase 1 fixed the rest (section 11).

## 11. Phase 1: stabilisation after the independent review

The review of 2026-10-22 (docs/reviews/2026-10-22_independent_review) and the adoption round's open items
(catalog/analysis/tier1/adopt_status.json) that are this domain's; each change has a regression test in
`PhaseOneStabiliseTests`. Hand-off: catalog/analysis/phase1/textiles_laundry.json.

* **Look-alike laundry jugs (review L2/L4, K-LEAK and manipulation requests).** Every `laundry_jug` member is one jug
  to every physical channel (section 10 "Data"): the kernel's `phys.props` of the seven member types are identical
  field by field, and the geometric camera renders unread twins alike, pixels included. The registry lint
  (`LookalikeWarning`) reports nothing for any group this domain owns (`laundry_jug` sets a group size; `bed_sheet`,
  `pillow_cover`, `dryer_sheet` and `sock` members agree on colour and size). What still differs is the liquid: its
  density (a full bleach jug weighs 2.92 kg, a booster jug 2.68 kg: the same touch bucket) and, once spilled, the
  kernel's puddle kind (bleach `wet`, detergents `slip`; kernel request below). Record fields changed, placements did
  not: generation-1 and -2 houses (seeds 0-19, three option sets) place every entity exactly where they did, so the
  generation-1 digests need the main session's re-record (class: type data).
* **Variants on the hashed rule (K-LEAK's review H5 fix) put the 1.5-day wool sweater of `A-d66994a566899c-01` on
  seed 1**, whose drying rack stands in a windowless laundry room: the feel checks that fell at night were refused
  ("That is not visible here"), and the room's wall switch is too small to make out from inside in the dark. The
  reference feels the garment with `textiles_laundry.feel_garment` (section 6), which lights the room from its doorway
  for a check at night and switches the light off again.
* **Forced realistic (D3 preview).** `T-28ff695081cd2c-01`: once out of the drum the cat lies down beside the washer
  and realistic route planning keeps a ring round a lying animal, which closed the narrow laundry room's way to the pod
  tub; the reference now takes a pod before it checks the drum and waits for the cat (passes forced realistic, room
  and geometric camera). `T-ed45170be6256e-01` under forced realistic + geometric camera stays open (kernel request):
  the phone lies under the duvet at the middle of a queen bed, the head camera cannot see under the duvet, and the
  wrist camera's peek under the lifted duvet reaches only `HW.wrist.deep_m` (1.0 m) in from the duvet's overhanging
  edge nearest the robot, 1.05 m short of the phone from every side, so no plan can pick it up; in room mode an
  `inspect` under the duvet reveals it.
* **Stack law ready.** `fold.textile_stack_fit` uses the kernel's clearance and stack height (section 10 "Stack").
* **Dialogue routes.** The adoption round's cross-domain bug (other domains' catch-all routes answered a garment's
  laundry questions) is resolved by manipulation's, admin_inventory's and the kernel's changes; a test pins that a
  garment's status / hang-or-fold / dryer / stain questions reach `laundry.item` (section 10 "Sweep").
* **Heel rest.** The ironing spec's remaining failure is manipulation's legacy contact-heat copy (section 9); this
  domain's claim answers `phys.thermal.contact_claimed` with the law deferred and active.
* **Coverage (K-GATE's coverage fuzz).** The fuzz's targeted walk never drove 8 of this domain's 27 actions to success
  from the spec houses (9 in a run over this domain's 15 specs alone, `unroll_sleeves` too): each needs a preparation the walk's generic repairs (approach, pick up, open) do not make --
  `steam` a hanging garment and the heated garment steamer in hand, `polish_shoes` the stage's tool in hand (brush,
  colour-matched polish, cloth), `soak` the textile in hand and a basin, bucket, tub or sink, `depill` a pilled knit
  lying flat, `lace_shoes` a shoe with eyelets and a lace in hand, `repair_seam` a garment with a split seam and the
  threaded needle in hand, `take_off_hanger` a garment that is on a hanger (the walk picked others), `tuck` a bed whose
  top sheet or blanket is on it (no spec bed of this domain has a top sheet), `unroll_sleeves` rolled sleeves or
  cuffed legs (refusals in the coverage JSON: "Hold the garment steamer", "Hold the tool", "Hold the item to soak",
  "Depill fabrics", "That shoe has no eyelets", "It is not on a hanger", "Tuck bedding on a bed", "Nothing is rolled or
  balled up"). Every one has a unit-tested success path from this domain's scenario helpers (`tuck` and
  `unroll_sleeves`, untested before, from phase 1 on); a spec that exercises each in a generated house is authoring
  work for a later round.

### Phase 1 finish (2026-10-28)

The rows of catalog/analysis/phase1/breakages.json that are this domain's, the P1 gate's G7 substep cost and the
defects found while finishing them; each has a regression test in `PhaseOneFinishTests`. Hand-off:
catalog/analysis/phase1/finish_textiles_laundry.json.

* **Unpowered heaters (electrical's request).** `model.heater_active(w, eid, rec)` asks the kernel's
  `physics.is_powered` for a switched-on corded or battery heater (section 3): a space heater left on behind a tripped
  breaker, unplugged or in an outage no longer scorches and lights the rug under it (T-702453ef553c00-01 seed 41
  before electrical's setup fix); a lit fire and a hot hydronic radiator still heat without power.
* **Holders that hold no liquid (INT; review L3).** The kernel's holders follow plumbing_liquids' liquid-tightness
  rule; the sorter's fabric `hamper_section` carried none of its flags and pooled kernel pours, spills, drips and rain
  (so did an open `garment_pocket`, a `sandal` and the paperboard `dryer_sheet_box`, found with the same probe over all
  176 of this domain's types). All four are `watertight: false`; the remaining kernel holders among this domain's types
  hold liquid in reality (washer drums, the wash basin, jugs and tanks, the dispenser drawer, rubber boots, the rimmed
  boot tray, the umbrella stand's drip tray, the plastic pod tub and sewing box). `dryer_sheet_box` is furnished in
  every generation-1 laundry room: its records gain the field (generation-1 digests and room golden re-record, class
  type data; no placement changes).
* **Dry laundry in the rain (found here).** With the kernel's rain law deferred, `_tick_textile`'s rain rule only
  reached textiles the fabric system already processed, so a dry shirt left on the lawn in the rain stayed dry
  however long it rained (dew and sun on dry laundry not being dried are still not modelled; section 9). `_needs_tick`
  now ticks every textile out in the weather in `dry.rain_rooms` (it rains by the weather record, the room is not
  covered, `physical.kernel_rain` is false); the idle probes and notes treat a textile in such a room like an active
  heater (`dry.textiles_in_rain`), so they stay exact.
* **Substep cost (G7).** The scan filter of section 3. Measured with tools/bench_systems.py's loop (40 substeps x
  seeds 0-3, best of 5 interleaved runs on paired tree copies differing only in this domain's files): fabric 0.069 ->
  0.047 ms per substep, fabric_idle 0.024 -> 0.025 (unchanged code; noise), together 0.093 -> 0.072 (ADOPT's tree:
  0.089). Engine 1 h wait (seeds 0-3, idle probes on): the two systems' bodies 29.9 -> 23.0 ms, probes 1.3 -> 1.4 ms.
  The remaining fabric cost is the device tickers (the iron's temperature chases a drifting room, so the fabric system
  is rarely idle in a generated house).
* **The ironing spec passes.** `T-7d4006bda247ba-01` passes on all five seeds in its own mode now that manipulation's
  legacy contact-heat copy asks `phys.thermal.contact_claimed` (breakages row 0); both spec files pass 15/15 (own
  modes; the perception file also with `--camera geometric`).
* **Not changed (out of this phase).** Sofa cushions in generation 2 (K-FIX's request: layer the throw and pillows on
  the cushions and let the generator place the cushions first): it changes every generation-2 living room, so every
  domain's generation-2 spec house, and needs the generator's order and `furnishings.json` (main session); it stays
  with the GEN follow-up and its re-verification. Wet mass in `support.own_mass` (adoption request 2) stays a kernel
  decision (KNOWN_LIMITS).
