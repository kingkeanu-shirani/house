# House generator

`house_sim.generator.generate_house` builds a complete household: the structure (rooms, floors, stairs, doors,
windows, lights, switches, floor surfaces), the core furniture, every domain's fixtures, appliances and items, the
residents and pets, and the robot on its dock. It is deterministic: the same arguments give the same house in every
process (all draws come from `random.Random` streams seeded by strings, never from hashes of Python objects).

```python
from house_sim.generator import generate_house
config = generate_house(seed, gen=1|2, dwelling=None, floors=None, bedrooms=None, bathrooms=None,
                        features=(), require=(), residents="random", pets="random",
                        start="2026-10-09T10:00:00", region=None, furnish=True)
```

Results are cached per argument set and returned as deep copies. Options:

| option | values |
|---|---|
| `gen` | `1` (default) or `2`, see below |
| `dwelling` | `house`, `apartment`, `townhouse` (drawn when omitted) |
| `floors`, `bedrooms`, `bathrooms` | counts (drawn when omitted) |
| `features` / `require` | rooms and features the house must have (`garage`, `porch`, `nursery`, `basement`, ...) |
| `residents` | `random`, `minimal` (one adult), `none`, a list of person records (`{"id", "role", "age", ...}`, kept as given), or in generation 2 `{"composition": preset}` |
| `pets` | `random`, `none`, or a list of pet records |
| `start` | local standard time the episode starts at (ISO) |
| `region` | `us`, `uk`, `eu`, `jp`, `ca` (drawn when omitted): units, voltage, appliances, shoes-off custom, climate |
| `furnish` | `False` stops after the structure, the residents and the dock |

`generator._generate(seed, report=[...], trace={...}, **opts)` (uncached) also reports what the final repair pass
could not fix (`report`) and which furnisher created each entity (`trace`); `tools/layout_audit.py` uses both.

## Pipeline

```
structure        layout.build_structure + finish_structure       stream "layout:{seed}"
region, utilities, weather, residents, pets, robot dock          stream "house:{seed}"
furnishers       @furnisher(name, order), in order               stream "furnish:{seed}:{name}" each
repair           repair.repair_layout (dock reachable, loose items off routes, agents on free cells; audit)
```

Each furnisher has its own stream, so adding one never changes another's draws. Furnishers receive
`opts = dict(options, plan=..., region=..., gen=...)`; in generation 2 also `season` and `notes` (see below).

## Generation 1 and generation 2

Generation 1 is what every verified spec uses. Its output is pinned: `tests/data/gen1_digests.json` holds the digest
of every default house of seeds 0-19 and of every distinct `house` block of the authored specs on seeds 0, 1, 2, 41
and 97, and `tests/test_generator_compat.py` (plus gate check G5) keeps them equal. Only a main-session commit that
names the round and the reason may re-record that file (`tools/orchestration/tier1_gate.py gen1-digests --write`).

Generation 2 (`gen=2`; design: `docs/design/TIER1_SWEEP.md` sections 12-14) makes the household realistic:

```
rng = Random("house:{seed}")
region = region or rng.choice(regions)                                  (first: pet shares and names need it)
composition = profile.compose(Random("profile:{seed}"), opts)           residents, pets, bedrooms needed
build_structure(..., min_bedrooms, exterior_steps), finish_structure
utilities (house rng); weather and clock (climate.apply, hash draws)
people_records, pet_records (house rng picks the rooms)
apply_profiles(Random("profile:{seed}:people"))                         profiles, bedrooms, schedules, household.profile
dock; furnishers (domains 10-99 with gen.bedrooms at 60; gen.furnishings 101, gen.belongings 102, gen.residents 103;
                  navigation_robot.dock_check 120: a dock whose keep-clear zone cuts a hazard-free route moves)
repair_layout; metadata.gen = 2
```

Every generation-2 path sits behind `gen >= 2`: the generation-2 furnishers are registered for every house but
return at once for generation 1, and the structure changes (bedrooms, exterior steps) only happen when the composition
passes them.

### Household composition (`generator/profile.py`, `data/generator/households.json`)

Presets with weights from the US Census CPS 2023 household tables (single adult, single senior, couple, couple with
children, single parent, roommates, multigenerational) plus named-only presets (`couple_with_infant`,
`couple_with_toddler`, `couple_with_teens`). Children's ages are uniform over 0-17; their role follows the people
domain (infant under 1, toddler 1-3, child 4-12, teen 13-17); parents are 22-38 years older than their oldest child;
everyone aged 70 or more is `elderly` (grandparents and householders alike: a single senior of 85 is not an `adult`),
with abilities drawn by age band (mobility devices from about 15 % at 70-74 to half at 85+, cane more often than walker;
low vision and reduced hearing rising with age; NHATS and NHANES shares in `households.json`). An elderly householder
keeps authority 2 and shares the primary bedroom with a partner. Ids follow generation 1: `resident_N` householders
(adults and elderly householders), `child_N` minors (oldest first), `elder_N` grandparents. `residents: "minimal"` stays one adult aged 28-60; an explicit
list is kept exactly (only missing profile fields are filled, and listed people are never put to bed).

Bedrooms: the structure gets at least the bedrooms the household needs by the people domain's rule (the first two
adults, elderly householders included, share the primary bedroom, everybody else has one; roommates sleep apart), at
most 5 (larger households share).
A household that needs four or more bedrooms never draws an apartment. When a spec fixes `bedrooms`, random draws are
limited to households that fit when minors share two to a room and infants sleep in their parents' room.

Pets: per-region shares (dogs and cats; fish, birds, small mammals, reptiles and backyard chickens are rarer, sources
in the data file). Dogs and cats become pet records; caged pets are listed in `household.profile.pets_pending` for the
pets domain's generation-2 furnisher (habitats are its follow-up).

### Profiles, schedules and the household profile

Each resident gets `profile` {tidiness 0..1, chronotype, diet, allergies, drinks {hot, cold}, fold_style, bed_style,
pillow_layout, tech_level, weekly_pattern (office/remote/shift/home/retired/school), relation}, `home_room`,
`privacy_zones`, a `schedule` in the people domain's format (sleeping by chronotype or age, away for work or school,
working at home for remote workers, naps for the youngest and the elderly) and matching `preferences` (`diet`,
`allergies`, `drink`, `cold_drink`, `fold_style`, `bed_style`, `pillow_layout`, `storage`). These are written before
the furnishers run, so the people and textiles domains keep them (they only fill what is missing); every one of them
is never perceived (people records are rendered from a fixed field list). The first adult's fold style becomes
`household.fold_style`.

`household.profile` = {gen, composition, relation, members {pid: role, age, relation, home_room}, sharing,
overflow, season, hemisphere, region, smart_home (none/basic/connected, 55/30/15 %), pets_pending}. Domains furnish
their own generation-2 content from it (`opts["gen"]`, `opts["season"]`, `config["household"]["profile"]`).

Residents asleep at the start time (their sleeping or napping block covers it) lie in a bed of their bedroom
(`in_bed`, `pos` on the bed, posture `sleeping`, as the people domain's sleeper helper does); infants lie in their crib,
asleep or awake by their schedule. The repair pass keeps people in bed on their bed cell and does not count a
sleeper's keep-out ring as a layout problem.

### Belongings (`generator/belongings.py`, `data/generator/belongings.json`)

| who | items |
|---|---|
| adult | phone (0.97) and charger (0.95 of phone owners), wallet (0.9), key ring (0.95), work bag (workers) or handbag (others) (0.7), coat or jacket by season (0.9), raincoat (0.4), 2-3 pairs of shoes by season, earbuds or headphones (0.6), tablet (0.4), smartwatch (0.25), sunglasses (0.5) |
| teen | phone (0.95), earbuds (0.8), backpack (0.95), sneakers, hoodie or jacket |
| child (4-12) | backpack (0.95 from age 5), lunch box (0.8), jacket, tablet (0.4) |
| elderly | phone (0.85), wallet (0.9), key ring (0.9), handbag (0.7, retired), coat or jacket (0.9), shoes, tablet (0.35), sunglasses (0.4); glasses and canes come from the people domain |
| everyone in a shoes-off region | house slippers at the door |
| household | 1-2 umbrellas (in the umbrella stand, else the coat closet), passports for everyone (0.6), scissors in the junk drawer |

Every item carries `owner` and `home` (its habitual spot: an entity id, or a room for floor spots), phones also
`phone_of`. A key ring's `opens` names the door it opens by id (`door_named`, phase 1: "front door" becomes the id of
the house's front door, an apartment's door included; another name the lockable entity with that label), and core
lock / unlock accept a held key whose `opens` names the lock. Homes depend on the role, the season, the region (shoes at the entry where shoes come off) and the time
of day (a phone charges on the nightstand at night and lives on the kitchen counter or a desk by day). An item with
alternatives is out of place with probability `0.05 + 0.35 x (1 - tidiness)`; the alternative is drawn by weight among
those valid at that time of day (sofa, bed, coat pocket, bag, kitchen table, floor). Coat pockets are textiles'
`garment_pocket` parts with the item marked `in_pocket`. Each resident gets `habits` {kind: {home, alt [[spot, weight
(, period)]], items, and day/night homes for phones}} and `preferences.storage` {item: home}, which the people
domain's `where:` answerer reads. Items the domains already gave a resident (textiles' coat and shoes, admin's
backpack) count instead of being duplicated; `has_phone` and `reachable_by_phone` follow the phone.

### Furnishings (`generator/furnishings.py`, `data/generator/furnishings.json`)

* `gen.bedrooms` (order 60: after the domains' fixtures and wall mounts, which it keeps accessible, and before
  textiles dresses beds and cribs at 75): a crib for every infant (the nursery's, else a new one in its own bedroom,
  else in its parents' room), a second bed in a shared bedroom.
* `gen.furnishings` (order 101): sofa cushions, throw pillows and a throw (counting textiles' ones), coasters (0.5),
  an unlit candle (0.4), an ottoman (0.3), an indoor mat, a coir doormat outside the front door (0.85), a key bowl
  (0.4, on an entry bench or side table, else the shoe rack, a console top or a wall shelf: `SPOT_FALLBACKS`, or an
  item's own `fallback_spot` list; phase 1), an umbrella stand (0.5), a picnic cooler in the garage (0.5), a high chair for an infant
  (or 0.6 for a toddler), and one material and colour per set of dining chairs.

Everything is placed through `Placer` with access kept (strict for furniture, no plain-route fallback for floor items).
A type the registry lacks (today: `sofa_cushion`, `coaster`, `candle`, `ottoman`, `indoor_mat`, `handbag`,
`stroller`, `diaper_bag`) or a piece that fits nowhere is skipped and reported as a layout-audit note (`note gen2 ...`
in the `report` list), never forced; the owner domain adds the type. Passports wait for a `document_box` type the same
way: until it exists they go to the office filing cabinet, else the primary bedroom's top dresser drawer, else the
kitchen junk drawer.

### Exterior steps

A porch is joined to its front yard by a `stairs` connection on the same floor: `exterior: true`, `upper` = the
porch, 2-4 risers of 0.17 m on 0.28 m treads, a handrail from four risers up (IRC R311.7.8), no winders, treads of
the porch's material. Core `move` charges the stair time; navigation_robot's stair rules read `upper` for same-floor
stairs.

### Season, weather and clock (`generator/climate.py`, `data/generator/climate_normals.json`)

Monthly 1991-2020 normals of one city per region (Philadelphia, London, Frankfurt, Tokyo, Toronto: high and low,
precipitation, wet days, snow days, wind, sunshine, daily-temperature spread). The weather process of sweep 13.2 is a
pure function of (seed key, region, local time): normals interpolated mean-preserving (each month's average of the
daily curve is its normal; plain interpolation through the monthly values damped the annual cycle by about 0.5 C), an
AR(1) temperature anomaly, a cosine diurnal curve from sunrise to 15:00, a two-state Markov chain of wet days with one
rain spell each, exponential intensities, a wet-day temperature offset (calibrated per region and month: Philadelphia's
winter precipitation is mostly warm-sector rain, Tokyo's comes with cold air; the dry days take the opposite share),
snow at or below 1.0 C (the 50 % rain-snow air temperature over land), log-normal wind with an afternoon peak. Snow
days then follow each region's normal under the source's own definition (`tests/test_generator_gen2.py`), except
London, whose Met Office count includes sleet and flurries on days below the 1 mm wet-day threshold that the process
does not make. `climate.weather_at(key, region, when)` gives the fields domains read
(`condition`, `temperature_c`, `wind_mps`, `gust_mps`, `rain_mm_h`, `snow_cm_h`); generation 2 starts the house with
the process's value at the start time and records `weather.process = {"model": "normals_v1", "region", "seed_key"}`
(an opaque key; outdoor_plants runs the process during the episode). The clock gets `model: 2`, `region` and
`latitude`; outdoor rooms take the outdoor temperature; `household.profile.season` and `opts["season"]` follow the
kernel clock's meteorological seasons.

## Tools

```
python tools/layout_audit.py 40 [--gen 2] [--verbose] [--json OUT]   problems by kind and furnisher; exit 1 on a problem
python tools/show_house.py SEED [ROOM ...] [--require garage] [--gen 2]   ASCII room grids (and the household)
python tools/orchestration/tier1_gate.py gen1-digests               generation-1 digests vs the pinned file
```

With `--gen 2` the layout audit also covers households with an infant, a toddler, roommates and three generations,
winter and night starts, and lists the generation-2 notes and the domain follow-ups (problems that involve only domain
furnishers) separately; it fails only on problems that involve the generator.

## Tests

```
python -m unittest tests/test_generator_compat.py      generation 1 unchanged (HOUSE_SIM_GEN1_ALL=1: all 340 entries)
python -m unittest tests/test_generator_gen2.py        generation 2
```

Both also run with `HOUSE_SIM_STRICT=1`.
