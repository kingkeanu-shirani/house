# Handoff GEN (stage A): generator generation 2

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole; 1, 2.3 GEN row, 3, 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_SWEEP.md` (0-3, 11-14, 16-22; GEN's sections 12, 13.1, 14 and 22.2), `catalog/analysis/tier1/sweep.json`
(the generator needs). Handoff files read: `handoff_TIER1-GATE.md`, `handoff_PHYS-DATA.md`, `handoff_BODY-MODEL.md` (the
ones that existed; none changes a GEN contract; PHYS-DATA's `setup.py` already packages `data/generator/*.json`).

## 1. Summary

* `generate_house(seed, gen=2, ...)`: household composition before the structure (`profile.compose`), bedrooms the
  household needs, porch steps, the sweep 13.2 weather process and clock model 2 (`climate.py`, 1991-2020 normals of five
  cities), resident profiles, schedules, bedrooms and `household.profile`, belongings with owners, homes, habits and
  tidiness-driven displacement, common furnishings, cribs and high chairs, and residents asleep in bed at night.
* Generation 1 is byte-identical: 340/340 digests of `tests/data/gen1_digests.json` (also with an explicit `gen=1`);
  every generation-2 path sits behind `gen >= 2` (the four `gen.*` furnishers return at once for generation 1).
* Gate GEN vs A0: PASS (isolated record; 0 verdict/digest changes, 340/340 generation-1 digests, gen2/gen1 time
  1.01). 45 + 6 new tests (normal and strict); `layout_audit.py 40 --gen 2` (440 houses) 0 generator problems;
  perception audit and fuzz on generation-2 houses clean.
* Stage-A review residual pass (gate A-FIX re-recorded; generation 2 only, generation-1 digests unchanged): everyone
  from 70 is elderly with abilities by age band, elderly householders keep the house; the normals are interpolated
  mean-preserving; snow at or below 1.0 C with calibrated wet-day temperature offsets, so snow days follow each
  region's normal (deviations 15-17). 50 tests in `test_generator_gen2.py`.

## 2. Files

Created: `house_sim/generator/belongings.py`, `house_sim/generator/furnishings.py`, `house_sim/generator/climate.py`,
`house_sim/data/generator/households.json`, `belongings.json`, `furnishings.json`, `climate_normals.json`,
`tests/test_generator_gen2.py`, `tests/test_generator_compat.py`, `docs/GENERATOR.md`, this handoff, the gate record
`catalog/analysis/tier1/gate/GEN/`.
Edited: `house_sim/generator/profile.py` (A0 stub replaced, TIER1-STUB removed), `house_sim/generator/__init__.py`,
`layout.py`, `place.py`, `repair.py`, `tools/layout_audit.py`, `tools/show_house.py`.
All are on the GEN list of TIER1_INTEGRATION 2.3 (G10 below). No other file was touched.

## 3. APIs

**`house_sim/generator/__init__.py`**
* `generate_house(seed=0, **opts)` / `_generate(seed, registry=None, furnish=True, start=..., region=None, report=None,
  trace=None, gen=1, **opts)`; `gen` in `GENERATIONS = (1, 2)` (anything else: `ValueError`). Generation 2 adds
  `metadata.gen = 2`; its furnishers receive `opts["gen"]` (every house, 1 or 2), `opts["season"]` and `opts["notes"]`
  (the `report` list: `"note gen2 ..."` lines). `residents` also accepts `{"composition": preset}` in generation 2.

**`house_sim/generator/profile.py`** (sweep 12, 14.2-14.3)
* (residual pass) `householder(person, relation=None)` (role adult, or elderly and self / partner / roommate),
  `elderly_abilities(rng, age)` (households.json roles.elderly_abilities bands), `HOUSEHOLDER_RELATIONS`.
* `compose(rng, opts) -> {"preset", "relation", "people": [{"id", "role", "age", "label", "relation", ("abilities")}],
  "pets": [records], "pets_pending": [{"id", "species", "habitat", "needs"}], "bedrooms", "fixed_bedrooms"}`, `rng` =
  `Random("profile:{seed}")` (the frozen A0 signature).
* `bedrooms_needed(members, relation=None, share=False)`, `role_of_age(age)`, `age_of(person)`, `presets()`,
  `households()`; `MAX_BEDROOMS = 5`.
* `people_records(rng, composition, rooms, start_room)`, `pet_records(rng, composition, rooms)` (house rng picks rooms).
* `assign_bedrooms(composition, people, rooms, start_room) -> (homes, sharing, overflow)`.
* `profile_person(rng, person, region, household_diet=None)`, `weekly_pattern(rng, person)`,
  `sleep_window(rng, person, profile)`, `schedule(rng, person, profile, home, work_room)` (people's block format),
  `asleep_at(blocks, when)`.
* `apply_profiles(rng, config, composition, start, season, region, beds=True)`: writes per person `profile`
  {tidiness, chronotype, diet, allergies, drinks {hot, cold}, fold_style, bed_style, pillow_layout, tech_level,
  weekly_pattern, relation}, `home_room`, `privacy_zones`, `schedule`, `preferences` {storage, diet, allergies, drink,
  cold_drink, fold_style, bed_style, pillow_layout}; `household.fold_style`; `household.profile` {gen, composition,
  relation, members, sharing, overflow, season, hemisphere, region, smart_home, pets_pending}.

**`house_sim/generator/climate.py`** (sweep 13.1-13.2; pure functions of (seed_key, region, local time))
* `normals()`, `regions()`, `latitude(region)`, `season(start, hemisphere="north")` (the kernel clock's rule),
  `normal_value(region, field, d)` (mean-preserving: residual pass), `day_number(date)`, `declination_deg(d)`,
  `day_length_h(lat, d)`, `sunrise_h(lat, d)`; field `wet_day_offset_c` (residual pass).
* `day_weather(seed_key, region, d) -> {high_c, low_c, anomaly_c, sunrise_h, wet, spell_start_h, spell_h,
  intensity_mm_h, cloudy, wind_mps, ...}` (cached), `temperature_c(key, region, d, h)`, `precipitation_mm_h(...)`,
  `wind_mps(...)`, `weather_at(seed_key, region, when) -> {condition, temperature_c, wind_mps, gust_mps, rain_mm_h,
  snow_cm_h}` (the fields domains read; `when` a datetime or ISO string).
* `seed_key(seed)` (opaque hash), `apply(config, seed, region, start) -> season`: `weather` = the process value at
  the start plus `weather.process = {"model": "normals_v1", "region", "seed_key"}`; `clock.model = 2`, `clock.region`,
  `clock.latitude`; outdoor rooms take the outdoor temperature.

**`house_sim/generator/furnishings.py`** (sweep 14.5; also the shared placement helpers)
* Furnishers: `gen.bedrooms` (order 60), `gen.furnishings` (101), `gen.residents` (103); all return at once for
  generation 1. Helpers: `Index(config)` (`room_of`, `add`, `find(room, kinds, where=None)`), `put`, `floor`,
  `furniture` (strict="hazards"), `front_door(config)`, `rooms_of`, `first_known`, `weighted`, `note`, `gen2`.

**`house_sim/generator/belongings.py`** (sweep 14.4)
* Furnisher `gen.belongings` (order 102); `displacement_probability(tidiness) = 0.05 + 0.35 (1 - tidiness)`;
  per resident `habits` {kind: {"home", "alt": [[spot, weight(, period)]], "items": [ids], ("day", "night")}} and
  `preferences.storage` {item: home}; items carry `owner`, `home`, phones `phone_of` (+ a per-entity hidden tier).

**`house_sim/generator/layout.py`**: `exterior_steps(seed, porch, room)`; `plan_house` honours `min_bedrooms`
(generation 2) and never draws an apartment for four or more; `build_structure` joins porch and front yard with the
steps when `opts["exterior_steps"]`. **`place.py`**: `Placer.furniture(..., strict="hazards")`. **`repair.py`**:
people in bed keep their bed cell; `_layout_hazards` leaves sleepers' keep-out rings out of the loose-item fix and the
audit (a no-op without anyone in bed, so generation 1 is unchanged).

**Tools**: `tools/layout_audit.py [n] --gen 2` (generation-2 configurations; notes and confirmed domain follow-ups
counted apart; exit 1 only on generator problems; `--json OUT` also writes `OUT_followups.json`);
`tools/show_house.py SEED [ROOM ...] --gen 2` (prints the household).

## 4. Deviations from the spec

1. **Region before the composition** (14.2 draws it after the structure): the house rng's first generation-2 draw, so
   the composition can use per-region pet shares and names.
2. **Furnisher names and orders** (14.2: belongings 100, furnishings 101, infant items 102): `gen.bedrooms` at 60 (cribs
   and shared beds after the domains' fixtures and wall mounts, which strict placement keeps accessible, and before
   textiles dresses beds and cribs at 75; at 100 cribs had no sheets, at 11 a later radiator ended up sealed behind a
   crib), `gen.furnishings` 101 (with the high chair), `gen.belongings` 102 (after the key bowl and umbrella stand it
   uses as homes), `gen.residents` 103 (sleepers into bed). Each has its own stream, so no other furnisher's draws move.
3. **Bedrooms** = max(the generation-1 draw, the household's need), the need following the people domain's
   one-bedroom-each rule (so its `bedroom_owners` agrees with `home_room`), capped at 5; minors share only when a spec
   fixes the count or the cap applies (14.2 says "needed"; a single adult in a 3-bedroom house is realistic).
4. **Habits are written by the belongings furnisher** (spot ids exist only after furnishing), with `items`, optional
   `day`/`night` homes and period-limited alternatives added to the 12 format.
5. **Caged pets** (fish, birds, small mammals, reptiles, chickens) go to `household.profile.pets_pending`, not pet
   records: the pets domain must materialize them with their habitats (its follow-up); a fish placed like a dog would be
   wrong. Chickens are dropped without a backyard.
6. **Smart-home tier** is drawn and recorded only; electrical places the devices (14.7, its follow-up).
7. **Weather** (13.2): the wind is normalised so its mean is the normal mean wind; the dry-day cloud share is derived
   from the sunshine fraction (P(cloudy) = 1 - sunshine); a rate under 0.05 mm/h is a trace (cloudy, no rain); spell
   durations are uniform on [1, 2M - 1] h with a per-region-month mean M (assumption, in the data); wet-day counts keep
   each source's threshold (NOAA 0.25 mm, ECCC 0.2 mm, Met Office/DWD/JMA 1 mm); `seed_key` is an opaque hash (the
   weather section is observed whole, see requests); forecasts are not implemented (outdoor_plants' part of 13.2).
8. **Exterior steps** only between porch and front yard (14.6), with `upper` = porch (navigation's same-floor stair
   direction), a handrail from four risers (IRC), `winders: 0`, `run_m` 0.28, tread material of the porch.
9. **Residents asleep at the start time lie in bed** and infants in their crib (not in the spec; the realistic state
   at night); explicit resident lists are never put to bed; unfurnished houses (`furnish=False`) have no sleepers.
10. **Schedules** are written by GEN in the people domain's block format so they match the weekly pattern and
    chronotype (otherwise people's own 35 % work-from-home draw would contradict the profile).
11. **Outdoor rooms' `temperature_c`** = the outdoor temperature (generation 1 leaves 21 C in every room).
12. **Layout audit `--gen 2`**: problems that involve only domain furnishers and persist when the house is generated
    again without the `gen.*` furnishers are listed as domain follow-ups and do not fail the audit (section 9).
13. **Pet shares per region** (14.3 anchors them on the US APPA survey): dogs/cats per region from the national surveys
    named in the data file; the household presets stay US-anchored for every region.
14. A tablet is never left on the floor (a fragile floor item's keep-out ring could cut a room's hazard-free routes).
15. **Everyone from 70 is elderly** (residual pass; review: residents 65+ outside multigenerational homes were role
    `adult` with no abilities, 1,188 of 6,566 adults in 4,000 US compositions; the people domain's elderly traits:
    slower gait, fall risk, reach band, heat vulnerability, early chronotype, a 10 kg / 80 N helper instead of
    23 kg / 200 N): `households.json` `roles.elderly_from_age` 70 (where the presets' and generation 1's elderly
    already start). Abilities are drawn by age band from NHATS and NHANES shares (mobility device about 15 % at 70-74
    rising to half at 85+, cane before walker; low vision 5-20 %; reduced hearing 45-80 %) instead of generation 1's
    uniform draw, which gave a walker to 1 in 3. An elderly householder (self, partner, roommate) keeps authority 2,
    a `resident_N` id, the primary bedroom with a partner, and counts as an adult for bedrooms, the household diet and
    fold style; a grandparent keeps authority 1 and `elder_N`. Elderly residents carry keys (0.9), a handbag (0.7,
    retired), a tablet (0.35) and sunglasses (0.4) besides phone and wallet; those under 75 follow the 65+ work row,
    from 75 they are retired. Generation 1's draw (`layout.default_residents`) is untouched.
16. **Mean-preserving normals** (residual pass; review: plain interpolation between the monthly normals damped the
    annual cycle, Philadelphia's January +0.56 C from interpolation alone): knots at the month midpoints solved so
    each month's average of the piecewise-linear curve equals its normal (a 12 x 12 cyclic system, cached per
    region and field); counts clamp at 0.
17. **Snow days follow the normals** (residual pass; 13.2 says snow at or below 0.5 C and draws temperature and rain
    independently; the fix pass found counts up to 2x off): snow at or below 1.0 C, the air temperature at which half
    the precipitation over land is snow (Jennings et al. 2018; Dai 2008: 1.2 C); and a per-region, per-month
    `wet_day_offset_c` (wet days that much warmer or colder, dry days the opposite share so the monthly normals
    hold), calibrated in the cold months on 480 simulated seasons against each source's own snow-day definition,
    capped at +-4 C: Philadelphia +3.3 to +4 (warm-sector and coastal-storm rain; its coldest days are dry), Toronto
    +1.3 to +2.2, Tokyo -0.6 to -4 (cold air under south-coast cyclones; sunny dry winter days), London and
    Frankfurt 0. December-March on the test's 200 seasons: Philadelphia 1.14x its normal (capped; December 2.25
    against 1.5), Toronto 1.01x, Frankfurt 0.93x, Tokyo 0.90x, London 0.74x (the Met Office counts sleet and showers
    on days below 1 mm, which the process does not make); before (snow at 0.5 C, plain interpolation, no offset): Philadelphia 1.6x, Tokyo 0.24x, London 0.63x.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim/generator` is empty (A0's `TIER1-STUB(GEN)` in `profile.compose` is implemented with
the frozen signature). Contracts consumed: A0's `tests/data/gen1_digests.json` (unchanged) and the stub signature;
kernel `clock.season`, `geometry`, `registry`, `plugins.furnisher`, `Placer`; the people domain's schedule format,
`bedroom_owners` rule, sleeper convention (`in_bed`, pos on the bed) and field names (`home_room`, `privacy_zones`,
`preferences.storage`, `phone_of`, `in_pocket`); textiles' `garment_pocket` and shoe-pair convention. No other stage-A
package is imported. Contract offered: `climate.weather_at` / `day_weather` for outdoor_plants' runtime process.

## 6. Gate results

Gate id **GEN**, recorded in `catalog/analysis/tier1/gate/GEN/` as an **isolated record**: stage A runs on a shared
tree where A0's work is uncommitted and five other packages are editing, so the gate ran with `--root` on a private
tree = `git archive HEAD` + A0's uncommitted files (its 2.3 list, `gate/A0`) + exactly the GEN files of section 2
(plus the two empty directories git does not carry, `tasks/templates` and `house_sim/data/actions`). Every GEN file
in that tree was byte-compared with the repository (equal up to line endings). Command:
`python tools/orchestration/tier1_gate.py --root <tree> record --id GEN --package GEN --prev catalog/analysis/tier1/gate/A0
--out catalog/analysis/tier1/gate/GEN`, then `--only G1` (the first G1 failed only because the archive lacked the
empty `tasks/templates` that `test_eval_splits` copies) and `--only G7` on a quiet machine (the first two G7 runs, at
+7.8 % and +7 %, ran while four other gates were running; G7 times generation-1 houses after generation, which GEN does
not touch).

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  28 test files x normal+strict: 2722 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains 10/16 files ok (8 failing tests in 6 files, all known from A0); core-only kernel files 2/3 (test_perception.py, known)
G1c  SKIP  no import-inert modules listed for this package
G2   PASS  own mode: 242/242 specs pass (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic: 226/242 specs pass (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  layout: 240 houses, 0 problems; counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.51 ms/substep, 1 h wait 1.125 s (A0: 4.38 / 1.102; +3.0 % / +2.1 %, limit +5 %)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  gen-2 timing: gen2/gen1 time 1.01; layout_audit gen 2: ok; test_generator_compat: ok; test_generator_gen2: ok
G10  SKIP  git unavailable (isolated tree); see below
compare A0 -> GEN: PASS
TIER1-GATE GEN [GEN] vs A0: PASS (10 pass, 3 skip; this run: G7)
```

`python tools/orchestration/tier1_gate.py compare A0 GEN --package GEN` -> **compare result: PASS**.

Stage-A fix gate `gate/A-FIX/` (re-recorded on the final tree by the residual pass, all seven stage-A packages with
A0): PASS vs A0 (12 pass, G8 skip): 0 verdict, state-digest or observation-digest changes on 1,210 + 1,210 trials,
generation-1 digests 340/340, G9 generator compat, gen-2 tests (50), `layout_audit.py 40 --gen 2` and gen2/gen1
time 1.00 ok; G10 from an isolated copy (the shared tree's main-session edits `.gitignore`, `stageA_review.json` and
`tests/test_perception.py` are outside every stage-A list). Details in the PHYS-DATA handoff, section 6.

G10 on the shared tree (`scope --package GEN --shared-tree`): every GEN file is on the GEN list; the "outside" files
are A0's uncommitted work (`engine.py`, `timeline.py`, `systems/core.py`, `tools/orchestration/*`,
`tests/data/gen1_digests.json`, `handoff_TIER1-GATE.md`, `gate/A0/*`) and the other packages' gate folders, none
GEN's.

Reviewed differences: none (no verdict, digest, fingerprint or layout change). Golden: not applicable (G8 from B1).

Benchmarks against 3.4: `generate_house(gen=2)` / generation 1 on seeds 0-9 = 1.01 (budget 1.20; the test fails at
1.6); generation-1 engine benchmarks within 5 % of A0 (they do not involve the generator).

Further evidence (not gate checks): `tools/layout_audit.py 40 --gen 2`: 440 houses, 0 generator problems, 4 domain
follow-ups (each confirmed by regenerating the house without the `gen.*` furnishers; section 9), 1,635 notes
(missing types, full spots); `tools/perception_audit.py` on 9 generation-2 houses (3 of them infant, winter-night and
three-generation households) x 150 steps: 0 leaks, 0 crashes; `tools/fuzz.py` on the same 9 x 300 commands: 0
defects, 0 nondeterministic replays; generation 2 with `HOUSE_SIM_DOMAINS=core` generates and steps (missing types
noted). Tests: `test_generator_gen2.py` 45 tests (about 18 s), `test_generator_compat.py` 6 tests (about 30 s), both
normal and strict.

## 7. Known gaps

* Climate normals and survey shares were transcribed offline from the named sources and rounded; spot-check before
  quoting a number. `temp_sd_c` and `spell_h` are estimates/assumptions (marked in the file).
* Weather: a wet day's temperature differs from a dry day's only by the monthly `wet_day_offset_c` (no separate
  diurnal-range change under cloud); no fog, humidity or UV; sunrise in local solar time (no longitude, equation of time or DST). `weather.process` (opaque key) shows in
  `obs.weather` until perception renders only perceivable weather fields (request 2); a code-aware agent could compute
  the weather ahead from it.
* Everyone is at home at the start (an office worker at 10:00 on a Friday is not away): start-time activity placement
  is the people domain's activity engine (sweep 15).
* Personal key rings carry `opens` labels but cannot unlock doors: core `lock`/`unlock` match one key id per lock
  (`house_key_1`); chargers sit unplugged on the nightstand (electrical's charging spots).
* Domains still use their own rules where they ignore `home_room`: people `bedroom_owners` and textiles
  `_bedroom_owners` put roommates 1 and 2 in the primary bedroom and assign rooms one each (textiles also skips
  toddlers), so clothes and nightstand items can land in a different room than GEN's `home_room` for roommates and
  shared rooms; they count only role `adult` for the primary bedroom, so the second partner of an older couple
  (both or one 70+) gets its glasses, linens and clothes in another bedroom although GEN's `home_room` puts the
  couple together (deviation 15).
* Mean household size of the random presets is about 2.3 against 2.5 for the US (CPS 2023): the presets have no
  adult children living with their parents (about a third of 18-34 year-olds) or other adult relatives; adding them
  needs a preset with its own sourced weight.
* Missing types are skipped with a note (sofa_cushion, coaster, candle, ottoman, indoor mat, handbag, stroller,
  diaper_bag, document_box) until their owners add them; caged pets wait for the pets domain; smart devices for
  electrical.
* `tests/test_generator_compat.py` checks 86 of the 340 pinned houses by default (every default seed and one seed of
  every distinct spec house block, about 30 s on an idle machine); `HOUSE_SIM_GEN1_ALL=1` checks all 340, as G5 does
  on every gate.
* The domain-follow-up rule of the gen-2 audit compares exact problem lines with and without the `gen.*` furnishers;
  a line that changes (entity ids shifting) counts as a generator problem, never the other way round.

## 8. How to test

```
python -m unittest tests/test_generator_gen2.py tests/test_generator_compat.py     # 56 tests
set HOUSE_SIM_STRICT=1 & python -m unittest tests/test_generator_gen2.py tests/test_generator_compat.py
set HOUSE_SIM_GEN1_ALL=1 & python -m unittest tests/test_generator_compat.py      # all 340 generation-1 houses
python tools/layout_audit.py 40 --gen 2 --verbose                                  # 440 houses, about 4 min
python tools/layout_audit.py 40                                                    # generation 1: 0 problems
python tools/show_house.py 3 master_bedroom_1 --gen 2                              # a household and a room
python tools/orchestration/tier1_gate.py gen1-digests
python tools/orchestration/tier1_gate.py compare A0 GEN --package GEN
```

Useful manual checks: `generate_house(7, gen=2, start="2026-01-14T03:00:00", region="ca")` (winter night: everyone in
bed, boots and coats, sub-zero weather), `generate_house(3, gen=2, residents={"composition": "couple_with_infant"})`
(dressed crib, high chair), `climate.weather_at(climate.seed_key(0), "uk", "2026-10-09T15:00")`,
`profile.compose(random.Random("profile:0"), {"region": "jp"})`.

## 9. Follow-ups

**Requests to the main session**
1. Add the core types `ottoman` (B6, conflict 37) and an indoor entry mat (`indoor_mat`); the furnisher picks them up
   with no code change.
2. `kernel/perception.py` (PEOPLE-IO in stage C): render `obs.weather` from its perceivable fields only (drop
   `process`, `override_until_s`): generation-2 houses carry `weather.process`.
3. TIME-EVAL: `runner.build` passes `house.gen` (2 for schema-3 specs) and `--gen` (sweep 14.1, 19.3) as planned.

**Domain follow-ups (adoption round, TIER1_INTEGRATION section 6)**
* people: read `home_room` / `household.profile` in `bedroom_owners` (roommates apart, shared rooms, the nursery,
  elderly partners together: `profile.householder`); a caregiver contact for an elderly resident living alone;
  toddler-aware schedules are already written by GEN; the activity engine places residents by schedule at the start;
  stroller and diaper_bag types (or name their owner).
* textiles_laundry: `_bedroom_owners` likewise (and include toddlers); types `sofa_cushion`, `seat_cushion`, `jacket`
  (GEN uses `light_jacket`/`puffer_jacket` meanwhile); seasonal clothing in storage.
* pets: materialize `household.profile.pets_pending` with habitats (aquarium, bird cage, small-animal cage, terrarium,
  coop) under generation 2.
* electrical: smart devices by `household.profile.smart_home`; plug chargers in at the nightstand (charging spots).
* outdoor_plants: run the weather process from `weather.process` with `climate.weather_at`, forecasts, season ground
  state; snow and ice on the porch steps.
* admin_inventory: types `handbag`, `document_box` (passports move in with no code change).
* manipulation: `coaster`; safety: `candle`.
* core/mechanisms: a lock opened by copies of its key (personal key rings).
* navigation_robot: same-floor exterior stairs read `upper`; its handrail goes on the yard side (`rooms[0]`).
* Generation-2 layout follow-ups (the same classes appear in generation 1 on seeds 40-79, which A0's audit does not
  cover): climate.fans tower and pedestal fans placed in front of a window sill or beside a robot vacuum dock zone
  (hazard-only access), plumbing_liquids shower stalls placed in front of a venetian blind or a medicine cabinet,
  navigation_robot's robot mower dock zone (generation 1 seed 78).
