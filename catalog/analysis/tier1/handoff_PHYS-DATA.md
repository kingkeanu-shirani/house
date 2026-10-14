# Handoff PHYS-DATA (stage A): physics data, properties, lawbook, forces and containment

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1, 2.3, 3, 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_PHYSICS.md` (whole; package sections 4, 5.6, 7, 10.2, 12). Handoff files read:
`catalog/analysis/tier1/handoff_TIER1-GATE.md` (the only one that existed). I also read the in-progress files of the
concurrent physics packages in the shared tree (`kernel/phys/support.py`, `impact.py`, `liquids.py`, `thermal.py`) to
check that every Phys field, floor column and function they call exists here with the meaning they expect.

## 1. Summary

* Five data files under `house_sim/data/physics/`: 42 material classes with every column of TIER1_PHYSICS 4.3 and a
  source per row, 192 aliases (all 129 registry strings plus forward-looking ones), 74 category fallbacks; 31 liquids
  and 30 aliases; 18 floor rows, 26 floor aliases and 32 foot kinds; 104 supports rows (ratings, rims, shapes, COM,
  mouths, nesting, landing hardness), fillers and people; the 23 laws with the 10.2 states and the 6.1 settle order.
* `kernel/phys/props.py` (Phys, resolution, variation, liquids, floors; 3.2 us per call), `lawbook.py`,
  `forces.py` (robot_caps, lift_plan, push, tip, friction, scratch), `contain.py` (fit, opening, nesting,
  choking), `setup.py` with the five package_data globs. Import-inert: nothing reaches the plugin tables.
* 86 tests (`tests/test_physics_data.py` 59, `tests/test_physics_forces.py` 27), green normal, strict and with
  `HOUSE_SIM_DOMAINS=core`; lift_plan is checked against today's core `pick_up` on 22 cases.
* Fix pass after the stage-A review (gate A-FIX, section 6): data anchors calibrated for the variation band and
  tested on the shipped types, crushability, foot widths, drag rows, hot-glassware rows, non-linear variation
  hash, `phys.scratch` events (deviations 20-28). Residual pass over the whole review (the first fix pass saw only
  part of it; A-FIX re-recorded on the final tree): flotation by material or sealed outline, absorption capped by
  volume and upholstery, closed-cell foam, household plastics as 100 C finishes, per-hook rail ratings, soft-landing
  cushions (deviations 29-34).

## 2. Files

Created: `house_sim/data/physics/materials.json`, `liquids.json`, `floors.json`, `supports.json`, `laws.json`;
`house_sim/kernel/phys/__init__.py`, `props.py`, `lawbook.py`, `forces.py`, `contain.py`;
`tests/test_physics_data.py`, `tests/test_physics_forces.py`; this handoff; the gate record
`catalog/analysis/tier1/gate/PHYS-DATA/` (the fix pass's combined stage-A record is `gate/A-FIX/`).
Edited: `setup.py` (package_data gains `data/physics/*.json`, `data/robot/*.json`, `data/calendar/*.json`,
`data/vocab/*.json`, `data/generator/*.json`; conflict 17). All on the PHYS-DATA list of TIER1_INTEGRATION 2.3.

## 3. APIs

**`house_sim/kernel/phys/props.py`**

```
MATERIALS, LIQUIDS, FLOORS, SUPPORTS        read-only (mapping proxies / tuples) contents of the data files
DATA_DIR, INF, SHAPES, VARIED, VARY_SPREAD, RECORD_FIELDS
Phys                                        namedtuple, fields below
props(state, eid) -> Phys                   variation applied
props_of(record, state=None, eid=None) -> Phys      ADDITION: for records not (yet) in a state (fillers, contain)
material_class(state_or_none, record) -> str        record is a dict, or an entity id when a state is given
class_known(material) -> bool               ADDITION: the string maps to a class or the category fallback (audits)
liquid_props(name) -> dict                  fresh dict, aliases resolved, "name" = canonical row ("default" if unknown)
floor_props(state, rid, cell=None) -> dict  fresh dict: floors.json row + "material" (row name) + "rug" (id or None)
floor_kind(material) -> str                 ADDITION: floors.json row name of a floor material string
rug_at(state, rid, cell) -> eid | None      ADDITION: the rug covering a floor cell
vary(state, eid, law) -> float              law in VARIED = drop, crush, rating, hang, handle, shock, heat
                                            (u from BLAKE2b, not crc32: deviation 20)
```

`Phys` fields (TIER1_PHYSICS 4.9, all kept, in this order): `cls mass_kg size_m shape com_frac brittle break_mode rigid
fragile drop_tol_m crush_kg hard wet_strength mu mu_wet waterproof absorb_l softness wsens combustible c_p heat_limit_c
melt_c shock_k rating_kg shelf_rating_kg hang_kg handle_kg rim_m mouth_frac opening_m nest_group nest_frac compressible
rollable floats anchored secured base_m crushable drag_cd cushion_m cushion_j`. **DEVIATION (additions):** `break_mode` (shatter | chip |
crack | bruise | None), `wet_strength`, `mu_wet`, `nest_group`, `nest_frac`, `compressible`; and (fix pass, appended
with namedtuple defaults None / False / None so keyword builders without them keep working) `base_m` (the foot an
upright item pivots on when narrower than its box: stemware, lamps), `crushable` (whether a load on top can crush
it at all: deviation 21) and `drag_cd` (wind drag over the outline: open frames 0.7); (residual pass, defaults None)
`cushion_m` / `cushion_j` (a soft landing's cushion depth and the energy it takes before it bottoms out: beds,
sofas, cushions; deviation 34). `floats` now means "stays afloat in fresh water" (deviation 29). Units: thresholds kg, m,
deg C, K; `inf` = never fails / no limit; ratings `None` = unrated; `opening_m` is a number (round mouth diameter), a `(w, d)` tuple or None;
`size_m` is the resting size (on its side the height swaps with the longest footprint side). `liquid_props(..)["rho"]`
is kg/m3 (divide by 1000 for kg/L, as support.py and liquids.py already do).

**`house_sim/kernel/phys/lawbook.py`**

```
LAWS                    read-only laws.json rows by law id: state, order (int or None), duplicated_by (tuple), note
STATES = ("active", "deferred")
law(law_id, order)      decorator; the law must exist; order must equal its laws.json order when it has one;
                        the same function twice is a no-op, a different one raises ValueError
active(law_id) -> bool  KeyError for an unknown law
laws() -> [(order, law_id, fn)]   registered laws sorted by (order, law_id); the caller checks active()
forced(**states)        context manager (tests only): True/False or "active"/"deferred"; nestable, restores
```

**`house_sim/kernel/phys/forces.py`**

```
robot_caps(w, eid=None) -> {"max_hand_kg", "max_mass_kg", "push_n", "pull_n", "grip_n", "pinch_n", "wrist_nm"}
lift_plan(w, eid, hands=None, helpers=()) -> {"ok", "hands", "mass_kg", "reason", "helpers"}
push_needed_n(w, eid, floor_id=None) -> float        inf for anchored or mounted items
max_push_n(w, helpers=()) -> float
tips_when_pushed(w, eid, contact_h_m) -> bool
friction(rec_a, rec_b=None, wet=None) -> float       wet: None | bool | dry, damp, wet, soapy, oily, greasy
scratch(w, floor_id, mover, distance_m, by=None) -> float   severity added (0.0: none); a visible mark is one
                                                     phys.scratch event and consequence (by, else the mover's
                                                     attribution)
foot_kind(record) -> str                             ADDITION: slider | pad kind | caster | foot material | class
FALLBACK_CAPS (humanoid_v1), G, TWO_HAND_SPAN_M, MU_CAP, SCRATCH_VISIBLE_RISK, SCRATCH_SEVERITY_PER_M, GRIT_SCRATCH
```

`lift_plan`: `hands` is `None` (any free hand, right first, as core's `free_hand`), `"left"` / `"right"` (that hand
only) or `"both"`. **DEVIATION:** a `grip=two_hand` request is passed as `hands="both"` (the frozen signature has no
grip). The result's `"hands"` is the list of hands the lift would use (`["left", "right"]`, `[hand]`, or `[]` when no
hand is free), i.e. core's own `hands` variable; `"helpers"` (ADDITION) lists the helpers counted in a team lift.
Reasons: `None`, `"hands_busy"`, `"arm_failed"`, `"over_capacity"`. Today's texts map as: over_capacity -> "Object and
contents exceed carrying capacity; get help or slide it" (safety); hands_busy / arm_failed with two hands -> "Both hands
must be free for this item"; with one hand -> "No free hand; place something first" (capability).

**`house_sim/kernel/phys/contain.py`**

```
fits(state, item, container, count_existing=True) -> (bool, reason | None)
opening_ok(item, container) -> bool          records
nested_volume_l(state, container) -> float
choking_hazard(record) -> bool
box_fits(item_size, inner_size) -> bool      ADDITION: step 1 (geometry.fits_inside, then the diagonal rule)
BOX_REASON = "It does not fit inside"; OPENING_REASON = "It does not fit through the opening";
VOLUME_REASON = "Not enough room inside"     (TIER1_INTEGRATION 1.15)
```

`item` and `container` are entity ids of the state or plain records (a record not yet in a state).

## 4. Deviations from the spec

Each one was chosen as the more realistic and simpler option; numbers are cited in the data files' `source` fields.

1. **Phys additions** (section 3): impact needs how a break shows (the spec's `brittle` column is not a boolean:
   y / chips / edges / screen / food), containment needs nesting and compression, friction needs soap's wet friction,
   and the crush and handle laws need corrugated board's wet strength as data rather than a literal 0.4.
2. **`phys.props` hooks refine the resolved values** (they receive record and phys dict after record, rows and class
   are resolved, may override any field, and run before variation). The spec ranks hooks below the record, but every
   record carries `mass_kg`, so textiles' "wet mass = dry + wetness x absorb" could never apply under first-wins.
   Hooks that want to respect an explicit record value read it from the record they receive.
3. **Row matching**: a head-noun level (the last word of the type name: `wine_bottle` -> `bottle`) between is_a and
   category, and per-field merging (for each field the most specific row that sets it). The 2,026 existing type names
   are compounds with no shared is_a for bottles, jars, bowls, cups or balls; without nouns the shape, mouth and nest
   rows would match almost nothing. Per-field merging lets a type row add a rating without hiding its category's
   landing hardness. An is_a row (`waste_container: box`) still beats a noun (`trash_can` -> can).
4. **Landing hardness** of beds, sofas, cushions, pillows, mattresses (spec floors table "bed, sofa, cushion (as
   landing) 0.15") is a supports.json `hard` row read through `Phys.hard` (impact.landing_hardness already reads
   it); floors.json holds room floors only.
5. **Aliases changed against the spec list**: `cellulose` -> foam with 15 L/kg (every cellulose type is a sponge; the
   spec's own anchor says a cellulose sponge holds 10-20x, paper holds 2x); `fiberglass` -> synthetic_fabric rated
   550 C, non-combustible (its only user is the fire blanket, which the plastic alias would melt at 120 C); `quartz`
   -> stone with a 150 C heat limit (the spec's finish anchor); `crayon` -> wax melting at 55 C (the melt anchor).
   Forward-looking aliases added (oak, pine, denim, styrofoam, memory_foam, linoleum, ...).
6. **wood mu 0.55**, manipulation's FRICTION value: the spec says friction values come unchanged from that table and
   its own table printed 0.5.
7. **category_material** is extended beyond "everything else stays plastic": electronic categories (chargers,
   detectors, audio, computers, remotes, phones, tablets, lights, switches, thermometers, ...) -> electronics;
   spills, biohazards, stains, puddles, fire, mould, holes -> intangible; pests -> organic; walls and ceilings ->
   plaster; debris and gravel piles -> granular; hooks -> steel; trim -> wood. Otherwise a smoke alarm or a lamp is a
   plastic moulding that survives 3 m drops and shrugs off water. Seven undeclared types get a row `material`
   (kitchen and luggage scales, battery tester, hair dryer -> electronics; cooling rack, sieve -> steel; crayons).
8. **fragile records**: a record marked fragile (or with the hazard "fragile") whose class never breaks gets the
   strength of annealed glass (drop 0.5 m, crush 10 kg, break mode crack); a class that does break keeps its own
   numbers. Without this `fragile: true` would mean nothing for unbreakable classes.
9. **`heat_resistant: true`** raises the heat limit to at least 400 C (the spec's heat-resistant supports) unless the
   record states a heat limit; it never lowers one (steel stays 800 C).
10. **Feet and floors**: the spec's foot factors (felt 0.5, slider 0.3 and 0.5 on soft floors, rubber 2.0 capped at
    mu 1.0, casters roll with the floor's c_rr) instead of manipulation's absolute SLIDE_MU (slider 0.08, felt 0.2,
    caster 0.04). The sofa and dresser anchors are identical; adoption will see sliders on carpet at 0.3 (realistic
    for pile drag) where manipulation had 0.08.
11. **Unknown floors** get a `default` row equal to manipulation's lookup fallbacks (hardness 0.9, scratch 0.5,
    mu 0.4) instead of manipulation's guess "wood".
12. **Scratch** keeps manipulation's visibility threshold (a risk below 0.3 leaves no mark; the spec gives only the
    0.1 x risk per metre severity) and accrues: the floor's current scratch level (read with impact.damage_map) plus
    this drag's severity, capped at 1.0, because impact.damage takes the new level.
13. **lift_plan** extras of section 3; helpers count when in the robot's room, `awake` not False, `consent` not False
    and `abilities.mobility` "full"; their capacity is their own `lift_kg` (or manipulation's `max_lift_kg`), else
    their role's (adult 23 kg NIOSH load constant, teen 15, elderly 10, child and infant 0; supports.json people).
    The same filter and table give `push_n` for max_push_n.
14. **robot_caps(w, eid=None)** accepts `eid` without using it: the contract signature, kept for BODY-ENGINE's
    position derates.
15. **Ratings added** beyond the spec list (a sofa inheriting a single chair's 113 kg would collapse under three
    adults): sofa 340, bench 225, bed 250, table 50, workbench 250; bathtub rim 0.4 m (the type's own inner depth; the
    spec's 0.5 m is the outer height); per-metre closet rods as `hang_kg: {"per_m": 25}`; a `glass_top` tag row
    (tempered, 25 kg) for authors, since no glass-top table type exists; upholstered sofas and armchairs absorb
    0.3 L per kg of the whole piece (not 2.5 L/kg of 60 kg of "fabric").
16. **Liquids**: rows added for every liquid the type files and domain code name (cream, broth, laundry detergent,
    lighter fluid, antifreeze, urine, vomit, raw egg, raw meat juice, mud water) and aliases (olive oil keeps food's
    0.91 kg/L through an alias override).
17. **Test scope** (12.1 "every type yields finite positive mass"): positive mass is required of portable types;
    built-in furniture, floors, walls and spills carry mass 0 (part of the building). The bulk-density check covers
    portable types and freestanding furniture with the exemption and owner-gap lists of the test.
18. **Variation** also scales `shelf_rating_kg` (law "rating"): a shelf rating is a rating.
19. **Containment openings**: a numeric `opening_m` is a round mouth (round items pass by their diameter, others by
    the diagonal of their two smallest sides); `[w, d]` and `inner[:2] x mouth_frac` are rectangles. Compressible
    textiles default by class (cotton, synthetic_fabric, coated_fabric, wool; `compressible: false` opts out), as 5.6
    reads; see the PHYS-INT follow-up on R3.

20. **Variation hash** (TIER1_PHYSICS 4.7 says crc32): u is the first 32 bits of BLAKE2b(salt|eid|type|law).
    crc32 is linear over GF(2): keys of equal length differ by a constant XOR mask whatever the house, so mug_1 and
    mug_2 were anti-correlated at -0.975 over 200 houses and drop vs heat of one entity at -0.885 (an agent could learn
    "if mug_1 survived, mug_2 is weak"). Now every pair stays below |0.2| (test). The salt is unchanged; factors are
    still cached per entity, so the cost is the same.
21. **Crushability** (TIER1_PHYSICS 5.3 "manipulation's max_load rules, unchanged"): `Phys.crushable` is true only for
    records that declare `crush_kg` / `max_stack_load_kg`, portable records with `max_load_kg` (which is then also
    their crush limit, manipulation's fallback), fragile or `crushable: true` records, types whose supports.json row
    sets `crush_kg` (the egg noun; food's packaging rows in the adoption round), and the classes that crumple by
    nature (`materials.json` `crushable`: cardboard, lined paperboard, eggshell). `crushable: false` opts out. A
    crushable record whose class has no limit gets 5 kg (manipulation's DEFAULT_FRAGILE). Before, every class value
    applied, so ten books crushed the bottom one and 20 plastic bins, electronics or packaged foods had limits they
    never had.
22. **Survive / break anchors hold for the whole +-15 % band** (calibration rule in props' docstring): electronics
    class and the phone row 1.0 -> 1.2 m (every phone survives 1 m: 47 % broke before on seed 0), `laptop_computer`
    0.75 -> 0.9 m (every laptop survives desk height). Break anchors already held (tumbler 0.5 x 1.15 < 0.9, plates
    0.45 x 1.15 < 0.75). Rating anchors stay nominal by decision: a bookshelf's 25 kg shelf spreads over
    21.25-28.75 kg, which `DataAnchorTests` pins as a band (the 24 / 26 kg anchor itself is tested with
    phys_exact); authors keep reference plans outside the band.
23. **Stemware and lamps on the shipped types**: `wine_glass` row com_frac 0.55, `base_m` 0.07, drop 0.25 m: it tips
    at 0.07 / (2 x 0.11) = 0.32 g and breaks from about 0.25 m (it was 0.375 g and 0.5 m, glass class); `table_lamp`
    `base_m` 0.2 (0.48 g, the spec's "about 0.5 g"; was 0.71 g) and `floor_lamp` 0.3. The geometry (`size_m`) stays
    the bounding box camera and containment use.
24. **Open frames in wind**: `chair` type row and `rack` noun row `drag_cd` 0.7, so the registry's 4 kg outdoor chair
    slides at about 9.1 m/s with no test override (Cd 1.2 gave 7 m/s). Rows only lower Cd below the bluff-body 1.2
    (tested: environment's cheap bound relies on it).
25. **Openings from noun rows**: `baby_bottle` mouth 0.8 (a 4 cm neck; the bottle noun gave 1.5 cm) and `blender_jar`
    1.0 (the jar noun gave 0.8). The other row-derived mouths are listed for PHYS-INT's R3 review (section 9).
26. **Heat-resistant glassware** (realism review: hot fills cracked jars and mugs): `mason_jar` shock 60 K (heat-treated
    canning glass), `casserole_dish` and `liquid_measuring_cup` tempered (tempered_glass 150 K), `french_press`
    160 K (borosilicate beaker; it still drops like glass). `coffee_carafe` keeps the 90 K its type declares.
27. **Ceramic class thermal shock 80 -> 100 K** (spec table 80): everyday dinnerware and mugs sold as "ceramic" are
    vitrified stoneware-like bodies (stoneware 100, porcelain 120; low-fired terracotta keeps 80). With 80 K a cold
    rinse of a just-emptied 90 C mug (80 K downshock) cracked about half the mugs; a fridge-cold dish into a 220 C oven
    (coupling 0.5, 108 K) still usually cracks, as manufacturers warn.
28. **Scratch event** (TIER1_PHYSICS 7.7, TIER1_INTEGRATION 1.11): `forces.scratch` records a visible mark as one
    `phys.scratch` event and consequence through `impact.damage(..., event="phys.scratch")` (PHYS-STATICS added the
    keyword), attributed to the drag: `by`, else the mover's attribution (`impact.robot_caused`), since dragging the
    mover makes the mark. It used to leave only a `phys.damage` record and no event, so the sounds observer's "scrape"
    and camera visual events never saw a scratch.

29. **Flotation** (review: `floats` compared the mass with the bounding box, so 311 of 397 portable metal, glass and
    ceramic types floated, a chef's knife at 667 kg/m3 of its box, while life rings sank because foam is not
    waterproof): a record's or a row's `floats` wins; a sealed hollow waterproof item floats when its mass (with its
    liquid) is less than the water it displaces, a closed container (sealed, lid on, cap on) its capacity plus its
    walls (mass / class density), a ball (category or is_a ball) or an airtight row (`pool_float`) or `inflatable`
    record its outline (box x pi/4 for cylinders, pi/6 for spheres); anything else floats when its material does
    (`materials.json` class column `floats`: wood, engineered wood, cork, wicker, wax, polyethylene film, foam
    plastic, open- and closed-cell foam; alias overrides for polypropylene, PP and HDPE, polypropylene fleece). A
    hinged lid or door (`openable` and shut) keeps things in but not water out. Rows: apples, citrus and onions float
    (air in the tissue); `flotation` category. Registry: only an empty sealed screw-lid jar floats among the portable
    metal, glass and ceramic types; knives, hammers, cans, full wine bottles, towels and fresh eggs sink; tennis
    balls, beach balls, ring buoys, life jackets, pool floats, milk jugs and wooden spoons float.
30. **Absorption** (review: a fabric dining chair held 15 L, a 10 L sandbag 25 L, closed-cell foam soaked like a
    sponge): `absorb_l` = L/kg x mass, capped at 0.9 x the outline's volume (`absorb_volume_cap`: water fills only
    pores); furniture whose class is a fabric or leather is upholstered and absorbs 0.3 L per kg of the whole piece
    (`upholstered`, the sofa rows' value: GEN's fabric dining chairs 1.8 L); `sand_bag` 0.25 L/kg (sand pores);
    new class `closed_cell_foam` (EVA, XLPE, PE foam; `nylon_foam` now maps to it) and rows `flotation`,
    `foam_play_mat`, `foam_roller` (0.05 L/kg, waterproof, floats). Feed, kibble and charcoal bags keep the paper
    rate: their contents soak up water too (dry kibble takes about 2-3x its weight), and the volume cap bounds them.
31. **Household plastics as support finishes: 100 C** (spec table 80 C; review: a mug of 85 C coffee melted plastic
    trays): the limit under a cup or dish is the softening point at negligible pressure, not the 0.45 MPa heat
    deflection (HDPE 80, PS 90-95, ABS 95-100, PP 100-120, melamine 120-150; Vicat 85-150 C). With PHYS-FLUIDS'
    contact temperature (a mug of boiling water touches at about 80 C) no tray or plastic table is marked; a hot pan
    still melts one. Film (80 C) and foam plastic (70 C) are unchanged.
32. **Per-hook rail ratings** (review: a 10-slot garden tool rack carried one coat hook's 10 kg for the whole rack):
    `hang_kg: {"per_slot": kg}` x the record's `hang_slots` (like `per_m` for rods): `hooks` category 7 kg a hook
    (a 6-hook coat rail 42 kg), `key_hook` 2 kg, `garden_tool_rack` 10 kg (100 kg). A record's own rating wins.
    Gen-2 houses (40 seeds): 2,521 rated slots, none at or above 90 % of its rating at the start.
33. **Cushion columns** in `floors.json` (`cushion_m`, `cushion_j`; null on hard floors: carpet 12 mm and 3 J, rug
    6 mm and 1 J, grass 35 mm and 10 J, soil and mulch 50 mm and 15 J, sand 40 mm and 15 J) and on the soft-landing
    rows of `supports.json` (bed and mattress 15 cm and 300 J, sofa and armchair 10 cm and 150 J, pillow 10 cm and
    20 J, cushion 6 cm and 20 J, pet bed 6 cm and 30 J). `impact_hardness` keeps manipulation's values (it is the
    compacted layer's once the cushion bottoms out); PHYS-STATICS' `impact.effective_drop_m` uses them (its
    deviation 29). Data anchor: mugs, plates and tumblers dropped from 1.5 m onto carpet survive (90 % or more of the
    variation band), eggs break on tile from 0.3 m and on carpet from 1 m, a lawn catches 30-90 % of eggs from 1 m.
34. **Phys additions** `cushion_m`, `cushion_j` (namedtuple defaults None; record keys of the same names win over the
    rows). Rows may set `floats`, `airtight` (sealed hollow), `waterproof` and `hang_kg: {"per_slot": kg}`.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim/kernel/phys/{props,lawbook,forces,contain}.py`: `forces.FALLBACK_CAPS` and
`forces.robot_caps` (TIER1-STUB(BODY-ENGINE), added in the fix pass): BODY-ENGINE makes `robot_caps` delegate to
`body.capacity_kg` / `body.limit` / `body.grip_limit_n` and deletes the humanoid_v1 table (TIER1_INTEGRATION 1.2;
`forces.py` is in BODY-ENGINE's B4 file list). Contracts consumed (frozen signatures; called lazily at call time, so the
modules import without them; the tests substitute local fakes):

* `support.subtree_mass(state, eid)` (PHYS-STATICS) in `forces._subtree_mass`: lift_plan, push_needed_n, scratch.
* `impact.damage(w, eid, kind, severity, detail="", by=None)` and `impact.damage_map(value)` (PHYS-STATICS) in
  `forces._add_damage`: scratch (with the fix pass's `event="phys.scratch", note, ids, **payload` keywords), and
  `impact.robot_caused(w, eid)` for a drag's attribution. `damage_map` is not in the frozen list; it is used
  because `damage` takes the new level, so the current one has to be read in the shared shape. Keep both names.

Contracts provided and already used by the concurrent packages' in-progress code: `props.props`, `props_of`,
`liquid_props` (rho in kg/m3), `floor_props` (`impact_hardness`, `scratch`, `mu`, `c_rr`, `absorbs`, `film_mm`),
`material_class(None, record)`, `MATERIALS["classes"][cls]["c_p"]`, `forces.friction`, `lawbook.active`, and the
Phys fields `cls mass_kg shape com_frac brittle break_mode rigid drop_tol_m crush_kg hard mu waterproof absorb_l wsens
heat_limit_c melt_c shock_k rating_kg shelf_rating_kg hang_kg handle_kg rim_m mouth_frac rollable anchored`.

## 6. Gate results

Gate id **PHYS-DATA**, recorded in `catalog/analysis/tier1/gate/PHYS-DATA/` against **A0**. Summary line:

```
TIER1-GATE PHYS-DATA [PHYS-DATA] vs A0: PASS (11 pass, 2 skip)
```

**How it was recorded.** Stage A shares one working tree in which A0's work is not committed yet and five other
packages edit files (generator, registry, core.json, body, sight, phys siblings). To measure this package alone, the
gate ran on an isolated tree in my scratch folder: `git archive HEAD` + A0's files copied from the working tree +
the PHYS-DATA files (byte-identical to the repository's, checked with `cmp` after the run) + the empty
`tasks/templates` folder that git does not carry:

```
python tools/orchestration/tier1_gate.py --root <scratch>/tree record --id PHYS-DATA --package PHYS-DATA --prev catalog/analysis/tier1/gate/A0
       --out catalog/analysis/tier1/gate/PHYS-DATA --work <scratch>/gatework2      (one command line)
python tools/orchestration/tier1_gate.py compare A0 PHYS-DATA --package PHYS-DATA        (run in the repository)
```

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  28 test files x normal+strict: 2746 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains 10/16 files ok (8 failing tests, all known from A0); kernel files with
           HOUSE_SIM_DOMAINS=core 4/5 ok (test_perception.py, known from A0; required from B1 on)
G1c  PASS  5 module(s) imported, none registers anything
G2   PASS  own mode: 242/242 specs pass (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic: 226/242 specs pass (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest
           changes; 250 realistic-spec trials identical in G2 and G3
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  layout: 240 houses, 0 problems; counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.39 ms/substep (best of 5), 1 h wait 1.113 s (seeds 0-3, best of 5); vs A0 (1.05x): within budget
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  physics: tools/physics_audit.py not present (PHYS-INT creates it)
G10  SKIP  git unavailable: not a git work tree (the isolated tree; see below)

compare A0 -> PHYS-DATA: G0 PASS (fingerprint unchanged); G2 PASS and G3 PASS (1210 trials each: 0 verdict,
0 state-digest, 0 observation-digest changes); G5 PASS (layout equal, 340/340 generation-1 digests); G4 leaks 0 -> 0;
G6 defects 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312; G7 PASS (substep 4.38 -> 4.39 ms, +0.2 %;
1 h wait 1.102 -> 1.113 s, +1.0 %); G1 new files test_physics_data.py (49) and test_physics_forces.py (26), normal
and strict. compare result: PASS.
```

**Reviewed differences:** none (no verdict, digest, fingerprint, layout or generation-1 change). **Golden:** not
applicable yet (G8). **G10 on the shared tree:** `tier1_gate.py scope --package PHYS-DATA --shared-tree` lists as
outside only A0's uncommitted files (`engine.py`, `timeline.py`, `systems/core.py`, `tests/data/gen1_digests.json`,
`tier1_gate.py`, `tools/orchestration/README.md`, the A0 handoff and gate folder) and the gate folders of the
concurrent packages (`--shared-tree` attributes their files but not their gate folders); every PHYS-DATA file is on
the list. A first record made while four other gates loaded the machine failed only on load: G7 (33.8 ms per
substep, thermal alone +7.3 ms; this package is not imported by the engine) and test_perception's timing bound, plus
test_eval_splits looking for the empty `tasks/templates` folder; the record above replaced it on a quiet machine.

**Package budgets** (TIER1_INTEGRATION 3.4, PHYS rows): `props()` 3.2 us per call with a warm per-type memo (budget
5 us; the test fails at 15 us), 3.9 us per entity over all 649 entities of the seed-0 house, about 14 us for an
entity's first call (type part and variation factors built once); data load 0.74 ms for all five files (budget
20 ms; the test fails at 60 ms). The engine budget is G7 above.

**Fix pass gate (A-FIX, 2026-10-14, re-recorded by the residual pass).** After the stage-A review the fixes of all
stage-A packages were gated together on the shared tree as `gate/A-FIX/`; the first fix pass saw only part of the
review list, so a residual pass went through all 26 findings and re-recorded the same gate on the final tree:
`tier1_gate.py record --id A-FIX --package A0+PHYS-DATA+PHYS-STATICS+PHYS-FLUIDS+CAM-SIGHT+BODY-MODEL+GEN --prev A0`
(the full gate, 25 min), then G10 again from an isolated copy (below). `compare A0 A-FIX --package <the same>`: PASS.

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  35 test files x normal+strict: 3147 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains: only the 8 failing tests known from A0; kernel files with HOUSE_SIM_DOMAINS=core 10/10
G1c  PASS  22 module(s) imported, none registers anything
G2   PASS  own mode 242/242 (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic 226/242 (1210 trials); vs A0: 0 / 0 / 0 changes; 250 realistic trials identical in G2 and G3
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  240 houses, 0 problems, counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.44 ms/substep (A0 4.38, +1.4 %), 1 h wait 1.113 s (A0 1.102, +1.0 %)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  body_audit ok (record gate/A-FIX/body_audit.json); generator compat, gen-2 tests, gen-2 layout and timing
           (1.00) ok; physics_audit and the camera tool flags not present yet
G10  PASS  151 changed file(s); 0 outside the list of the seven packages (isolated copy)
```

G10 on the shared tree lists 3 files outside every stage-A list: `.gitignore`, `catalog/analysis/tier1/stageA_review.json`
and `tests/test_perception.py`, edited by the main session after the first A-FIX record (its review file, the jsonl
ignore rule and the core-only fix that makes the kernel files 10/10). G10 was therefore recorded from an isolated copy
(`git archive HEAD` committed as the base in a throwaway repository in scratch, plus every other changed or untracked
file of the shared tree; the project repository was not committed to), as the stage-A packages did for A0's
uncommitted work. No reviewed differences (none allowed in stage A, none needed).

## 7. Known gaps

* **Domain data gaps** (TIER1_INTEGRATION 6, "mass/size"), listed in the test: implausible bulk density for
  `shred_pile` (admin_inventory), `tights` (textiles_laundry), `styrofoam_block`, `cigarette_butt`, `twist_tie`
  (cleaning), `small_parts` (manipulation), `jigsaw_blade` (tools_vehicles). Food's packaged jars, bottles and cans
  declare `organic`, so a honey jar drops like produce (bruise at 1 m); `hen_egg` is organic (the egg noun row gives
  it 3 cm, shatter); `television` declares glass (a TV row gives 0.3 m, but it shatters with sharp pieces). Each owner
  fixes its own type data in the adoption round; `tools/physics_audit.py` (PHYS-INT) should report them per domain.
* `physics_audit.py --data --bench` does not exist yet (PHYS-INT creates it); G9 skips it for this package.
* The variation salt is memoised per rooms dict: rooms and their sizes are assumed fixed within an episode (no action
  or system resizes a room today). A future room-resizing verb must clear `props._SALTS`.
* `LiftPlanMatchesCorePickUpTests` pins today's core `pick_up`. BODY-CORE changes `pick_up` (derates, grasp size); its
  derate-free cases (a healthy robot at table height) should stay green, otherwise BODY-CORE must ask the main
  session to update the test (it is not on BODY-CORE's file list).
* The gate record comes from an isolated tree (section 6) because the shared tree carries A0's uncommitted work and
  five concurrent packages; G10 there is SKIP and the shared-tree scope check is reported instead.

## 8. How to test

```
python -m unittest tests.test_physics_data tests.test_physics_forces            # 91 tests, about 3 s
HOUSE_SIM_STRICT=1 python -m unittest tests.test_physics_data tests.test_physics_forces
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_physics_data tests.test_physics_forces
python tools/orchestration/tier1_gate.py compare A0 PHYS-DATA --package PHYS-DATA
python -c "from house_sim.kernel.phys import props; from house_sim.kernel.registry import default_registry as r; print(props.props_of(r().make('wine_glass', 'kitchen')))"
```

Useful manual checks: `props.liquid_props("olive_oil")`, `props.floor_props(state, rid, cell)` on a generated house,
`lawbook.LAWS`, and `contain.fits(state, item_record, container_id)` with an `opening_m` container.

## 9. Follow-ups

* **PHYS-INT**: (a) R3 says `place` with `contain.fits` differs only with declared openings, nest groups and diagonal
  fits; compressible textiles (cotton, synthetic fabric, coated fabric, wool count volume x (1 - 0.5 softness)) widen
  it, so laundry baskets and bags take about 40 % more textiles: list it with R3 or pass records with
  `compressible: false` until reviewed. (b) `Ctx.lift_plan` should pass `hands="both"` for `grip=two_hand` and map
  the reason codes to today's texts as in section 3. (c) `physics_audit --data` can reuse `props.class_known`, the
  test's exemption and owner-gap lists, and report rows whose noun match looks wrong. (d) `core.subtree_mass` delegates
  to support (unchanged here).
* **Fix pass, for PHYS-INT (B1)**: (e) R3's list of where `contain.fits` differs from today's `place` grows by the
  openings that come from noun rows rather than a type's own field: `mason_jar`, `pet_treat_jar`, `screw_lid_jar`
  (jar 0.8), `used_oil_jug` (jug 0.3), `water_bottle` (bottle 0.3), `water_carafe` (carafe 0.4) (`baby_bottle` and
  `blender_jar` now have type rows), and by the nest groups 20 types take from the bowl and cup nouns (cereal, soup,
  serving, mixing, prep, pet water bowls; plastic, sippy, detergent, pen and measuring cups, ...).
  `physics_audit --data` should report every container whose opening or nest group comes from a row. (f) Only
  crushable items have a crush limit now (deviation 21): the crush law's adoption by manipulation keeps its
  max_load behaviour. (g) The data anchors are tested on registry types with variation on (`DataAnchorTests`).
* **Residual pass, for PHYS-INT (B1)**: `Phys.floats` is now physical (deviation 29) for the sink, bathtub and pool
  laws; `contain` and the `load` fillers are unchanged; `physics_audit --data` can list every row-derived `floats`,
  the absorption caps that bind (37 registry types, mostly paper slips and foam strips) and the upholstered furniture.
* **Residual pass, for the adoption round**: manipulation's own drop rule keeps `FLOOR_HARDNESS` x height until its
  wrappers call `impact.fall` (then soft landings follow the cushion model); textiles and cleaning read `absorb_l`
  (towels, rugs and duvets unchanged; paper slips now hold at most their own volume).
* **Fix pass, for the adoption round**: appliances_cooking updates its thermal-shock tests to ceramic 100 K (not the
  80 K the integration plan's follow-up names) and to the upshock coupling of PHYS-FLUIDS; electrical's "check the
  electronics drop rows" now reads phone 1.2 m and laptop 0.9 m nominal (every instance survives 1 m and 0.75 m);
  food's packaging rows (bread, chips, egg cartons) must set `crush_kg` (or `crushable`) for those to crush.
* **PHYS-STATICS**: forces calls `support.subtree_mass(state, eid)`, `impact.damage(...)` and `impact.damage_map(...)`
  by name. The crush and handle laws can read `Phys.wet_strength` (0.4 for cardboard) instead of a literal.
* **PHYS-FLUIDS**: `LIQUIDS["min_spill_l"]["value"]` holds the 5 ml droplet threshold; floors carry `film_mm` (3 mm
  on carpet and rugs) and `absorbs`.
* **BODY-ENGINE**: `forces.robot_caps` delegates to the body and `FALLBACK_CAPS` goes (TIER1_INTEGRATION 1.2).
* **Domains** (adoption round): the data gaps of section 7; manipulation's `slide_force_ok` and `scratch_risk` become
  wrappers of `forces.push_needed_n` / `forces.scratch` (note the slider-on-carpet change of deviation 10); people may
  set `lift_kg` / `push_n` per person; tools_vehicles' anchors (`rated_kg` per substrate) need a substrate rule if
  hang ratings should depend on the wall.
