# Handoff WP-D climate (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; the phase-1 carry-over; run A's requests)

Files (only these): `house_sim/systems/climate/` (`hardening.py` new; `__init__`, `airclean`, `attribution`, `fans`,
`heaters`, `hvac`, `model`, `moisture`, `perception`, `scenarios`, `skills` changed), `house_sim/data/types/climate.json`
(two `perception` / `view` entries for `readings`), `house_sim/data/vocab/climate.json` (`backwards`,
`installed_backwards` for `reversed_filter`), `house_sim/data/verify/watch/climate.json` (new),
`house_sim/data/speech/truth_climate.json` (new), `docs/domains/climate.md` (section 19, with the "Latched fields" table),
`tests/test_climate_hardening.py` (new), this handoff and `catalog/analysis/phase2/WP-D_climate.json`. Nothing committed,
staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch:
`<scratch>/p2_WP-D_climate/` (`make_watch.py` writes the watch file; `sweep.py` the twin sweep; `asif.py` the as-if
schema-4 runs; `writes_probe.py` the field probe; `cmp_checks.py` the legacy A/B; `before_tree` = `git archive HEAD`,
`after_tree` = HEAD + exactly this package's files).

## What landed

1. **Field classes** (6.3, L-WATCH): `watch/climate.json` classifies every property name of every climate type and every
   field climate's verbs, hooks and systems write onto its own types (a write probe over `tests/test_climate.py` and the
   14 specs on seeds 0 and 1): 205 field names, 13 per-type refinements, 11 water-sensitive base types.
   `field_census.py --unclassified --domain climate`: 0 (was 52 names / 133 pairs); `--check`: 0. Ratings, kinds, wiring,
   print: identity. Switches, covers, plugs, settings, slots, a filter's wrap and facing: setting. What the model derives
   or what only follows a setting (call, stage, running, rpm, display, `readings`, `hold_until*`, `tank_seated`,
   `mineral_ppm`, `intake_blocked`, `setting_options` after a unit change, a cord's route, `placement_issues`): process.
   Damage and faults: condition with a worse direction (`fault`, `heat_exchanger_stressed`, `compressor_damaged`,
   `motor_damaged`, `wobble`, `reversed`, `layer_order_ok`, `blocked`, `cord_crosses_walkway`, `cord_under_rug`;
   `dirtiness` tol 0.05, `bypass_gap_mm` tol 6 mm, `duct_dust`, `wick_scale`, `contamination.*`). Stamps and counters:
   bookkeeping. A tank's water: content. **A dehumidifier bucket's condensate: content with `guard: mess`** (WP1-fix's
   request: a load to pour away, not goods). Per type: the window types' `covered`, `condensation`, `rain_in_l`,
   `water_damage` (fields climate writes on them); a filter's `open` (its wrap decides). As-if references (scratch
   `after_asif.json`, seed 0, empty envelope) now show only real task deltas plus mechanisms' `mech.*` (request).
2. **Causes** (6.2): all three systems tagged `writer="physical"`. Every physical write of a compared field cites what it
   follows from: `attribution.hvac_causes(w, sid)` (system, filter, thermostats, outdoor unit, registers, returns, what a
   vent is under) for `fault`, `limit_trips`, `heat_exchanger_stressed`, `duct_dust`, `compressor_damaged`;
   `attribution.room_causes(w, rid)` (windows, outside doors, the room's devices, the house's equipment) for a burst pipe;
   a heater for what it scorches and the cord it overheats; the fans blowing for a blown paper, a guttering flame, wax and
   what catches fire; the humidifier (and tank) for mineral dust, mist, a wet thing, the tank's water; the dehumidifier
   for its bucket and its hose's wet floor; the purifier or system for filter dust; the window for rain and its wet cell;
   the thing itself for a schedule's setpoint, a hold's expiry, a toppled fan, a slow process. Results: a household's
   heater, window, fan or dampers answer `robot_attributable` **False** (was None, "link unknown", charged), the robot's
   answer True. A census test pins that no compared field is written without a cause in a harm scene.
3. **Mess** (6.5): climate owns no mess kind (deviation below). `empty_reservoir` logs its pour where the measures are
   blind (`hardening.reservoir_flow`, by plumbing_liquids' classes, origin `reservoir:<id>`): mop water or a chemical from
   a measured vessel into a toilet / basin (`entity:`), a drain (`drain:`) or the ground (`outside:`); a product from its
   own bottle only on the ground. Before, this verb poured mop water anywhere without a trace: the bread-box class of hole
   through another verb.
4. **Energy meter** (6.8; electrical's request 7): `climate.meter` books every lit light (`_bulb_w`, else `power_w`, else
   12 W, dimmed; none burnt out) and a charger's `_load_w`. Both were on no meter: a generated house has 13-14 ceiling lights
   without `power_w`. Double counting: climate devices stay in `_energy_step` only; burners and ovens carry neither
   `power_w` nor `standby_w` (appliances_cooking books them).
5. **Derived fields** (8.2): `reversed`, `bypass_gap_mm` (MISSING out of a slot), `layer_order_ok`, `tank_seated`,
   `placement_issues`, `cord_crosses_walkway`, `cord_under_rug`. Latched fields table in `docs/domains/climate.md` 19
   (`lint.latched_fields()` lists them). World fix: a paper a fan blew off loses `scattered` when the robot sets it back.
6. **Settings skills** (10.3): `settings.heat_setpoint`, `cool_setpoint`, `setpoint`, `damper`, `trv`, `target_rh`,
   `thermostat`, `aim_deg` (numeric options the kernel default cannot reach), a ceiling fan's `speed`, `light`, `direction`
   (pull chain, remote or switch), a mini-split's `mode`, `fan`, `units` (by its remote), `shrink_wrapped`; **damaging
   verbs** for B14: `motor_damaged` (a ceiling fan reversed while its blades coast), `reversed` (a fitted furnace filter
   refitted backwards), through the skill `climate.set_field`. Each tested by executing it.
7. **Truth** (`truth_climate.json`): states heating, cooling, spinning; issues blocked_vent, closed_vent, dirty_filter,
   wrapped_filter, reversed_filter, short_cycling, frozen_coil, condensation, stuffy, too_humid, too_dry, drafty; acts set,
   fill. Vocabulary: "the filter is backwards" parses now.
8. **Structured readings** (15.2): a thermostat's and a hygrometer's `read` gives `readings`
   `[{"subject": <room>, "quantity", "value", "unit"}]`; `perceive.index(env).readings` picks them up (value_si in canonical
   units; the room's temperature and humidity as evidence for a report).
9. **Hazard zones** (WP4's request): `@hook("hazard_zones")`: a burst pipe's room; a running heater with something at its
   grille or scorched (as the heater); a warm-mist humidifier with a child at home (as the humidifier).
10. **Harm labels** (WP8-fix's request): `climate.person_heat_stress` / `climate.person_cold_stress` are returned literally,
    so L-EXPECT reads them (they were composed at run time and could not be named). Every label unchanged.
11. **Identify first** (15.3; the look-alike and unfamiliar rows): an order-1 before hook on `refill_reservoir`,
    `empty_reservoir`, `reverse_fan` gives the kernel's identify-first refusal for an unfamiliar thing in any entity
    parameter; the implicit water source passes an unfamiliar one over; a held unread look-alike bottle is no jug to pour
    from (`ctx.refuse_unidentified`; a plain spray bottle vs a product spray answered differently); core `install` and
    `uninstall` refuse an unfamiliar device whatever it is, and `install` an unfamiliar part for a climate slot.
12. **Scenario helpers** (WP9's request): `climate.hvac_filter_job` and `climate.purifier` place their spares in
    `ctx.placer.order` (schema 4: seeded; older specs: unchanged, no draw); `climate.vents` takes `closed_rooms: "random"`
    (`ctx.sample_roles`).
13. **Water sensitivity** (closeout_breakages row 6's rule, in this domain's reader): `moisture.water_sensitive` also asks
    the kernel's `ledger.water_sensitive`; `watch/climate.json` lists climate's gear.

## Interfaces

```python
# house_sim/systems/climate/hardening.py
VERBS; unfamiliar_first(ctx)                                  # @before(<verb>, order=1)
climate_slots(rec) -> bool; _identify_part(ctx); _identify_device(ctx)   # @before("install" / "uninstall", order=5)
reservoir_flow(w, tid, liters, dest) -> entry | None           # dest None: the ground where the robot stands
hazard_zones(state) -> [{"id", "room", "subjects"}]            # @hook("hazard_zones")
installed_slot(state, eid) -> (device, slot) | (None, None)
derived_reversed / derived_bypass_gap / derived_layer_order / derived_tank_seated / derived_placement_issues /
derived_cord_walkway / derived_cord_rug (state, eid)           # @derived_field
wrap_steps / motor_steps / reversed_steps (state, eid)         # @settings_skill
set_field(r, target, field, value)                             # @skill("climate.set_field")
# house_sim/systems/climate/attribution.py
room_causes(w, rid) -> [ids]; hvac_causes(w, sid) -> [ids]
# house_sim/systems/climate/fans.py
live_cord_flags(state, eid) -> (crosses_walkway, under_rug); placement_issues(state, eid, cord=None)
# house_sim/systems/climate/airclean.py
bypass_gap(size, fit) -> mm; layers_in_order(entities, rec) -> bool
# house_sim/systems/climate/model.py
LIGHT_W; _metered(rec) -> bool; _light_draw(rec) -> W; _other_draw(w, eid, rec) -> W
# house_sim/systems/climate/skills.py
strain_fan_motor(r, fan); fit_filter_backwards(r, part)
```

## Deviations from the spec (and why)

* **No mess kind and no flow into floors**: clean water and condensate in a vessel are content (6.5); `liquid:*` belong to
  plumbing_liquids (a second registration raises), `debris` to cleaning. Rain and condensate on a floor stay wet cells:
  plumbing's rule (floor wet volume is no parcel; a flow into a measured floor would outlive a mop-up that logs none) and
  `house.wet_room` judges them. Filter and mineral dust and wax are soil (`house.soiled`).
* **Labels are not re-prefixed** (`climate.building:pipe_freeze`): that would change every legacy wrong plan's reasons, like
  electrical's and cleaning's; they are literal, which L-EXPECT reads.
* **Truth for hot / cold / warm is not defined**: no threshold fits every thing and listener; the claims stay unscored.
* **`ctx.audit` skipping** (WP9's "may"): not adopted: `climate.hvac` re-sites equipment with the setup RNG, so nothing a
  helper does is free of the bindings' placements.
* **`install` / `uninstall` of an unfamiliar device** is refused for every domain's device from this package's hook (a
  kernel rule in a domain's file, the rule electrical's request asks of core): the sweep found climate's verbs answering by
  true slots, and a narrower hook would tell an unfamiliar furnace from an unfamiliar kettle by the refusal.

## Legacy outcomes (P10)

`before_tree` (`git archive HEAD`) and `after_tree` (HEAD + exactly this package's files), all 243 authored specs, own mode,
seeds 0 1 2 41 97, `python -m house_sim --check <combined> --workers N --no-cache --json OUT`: **243 / 243 pass in both; 0 of
1,215 trials changed** in any verdict field (passed, initial / idle fails, reference passes, reference steps or time, any wrong
plan's ran / fails / reasons / labels / goal fraction; scratch `cmp_checks.py`). `initial_hash` and `task_hash` are unchanged
everywhere: generated houses and gen-1 digests do not move. Every reference state digest moves (the meter and the readings).
Forced realistic, the 19 specs that use climate's verbs or helpers or core install / uninstall: 19 / 19.
**outcome_changes: none**: no legacy spec's outcome reads the meter (`climate.energy` appears in no authored predicate), no
authored plan names an unfamiliar thing for a climate verb, pours from an unread look-alike bottle or relies on a paper's
`scattered` flag.

Room golden (`tools/golden.py dump --all` + `diff` on the two trees): 251 of 275 files differ, **5 path patterns**, state digests
only (steps, time, evaluation, every observation digest unchanged), for the golden owner's re-record (P2-S3 merge) and the main
session's signature:

| Pattern | Files | Why | Code |
|---|---|---|---|
| `climate.energy.by_device.*` | 762 paths | lit lights and chargers are on the meter | P2 |
| `climate.energy.kwh` | 91 | the same | P2 |
| `climate.energy.cost_usd` | 91 | the same | P2 |
| `climate.energy.peak_kwh` | 2 | the same | P2 |
| `entities.*.readings` | 252 | a thermostat's and a hygrometer's structured readings | P2 |

## Phase-1 carry-over

* closeout_breakages: no row names climate. Row 6 (water sensitivity): this domain's reader asks the shared predicate and its
  gear is listed. closeout_REV / closeout_VER2: no finding in climate's files. closeout_INT3: only a perf note names climate.
* The look-alike and unfamiliar instruction, every verb: scratch `sweep.py` -- the 3 climate verbs and every core verb climate
  hooks (37 commands: refill / empty / reverse variants, 12 `set` settings, switch, install, uninstall, place, pick_up, open,
  close, covers, wipe, dust, clean, hold_steady) x every entity parameter, with the twin on a table or held. Over 37
  unfamiliar twins before: 13 commands differ (`before_novel.json`: reverse_fan x 3 "Only ceiling fans ...", refill "That is
  not a water source" / "The jug is empty", install "It has no compatible slot" / "Open the cover" / an unfamiliar tank
  installing); after: only what is physical (a fixture cannot be picked up, a two-handed dehumidifier, and what follows from
  not holding it). Over the registry's 78 look-alike groups (253 members): one difference on the code before the last fix (`refill_reservoir` from a held unread spray bottle: a
  product spray "The jug is empty", a plain one filled), fixed and re-swept at 0 (`look2.json`, `look_geo.json`).
  Regression tests: `IdentifyFirst` (fail on HEAD).

## Known gaps

* Honest claims the **kernel's** tables misjudge (requests to WP4; they matter only for schema-4 speech): "the ceiling fan is
  off" (`on` is the wall gate), "the vent is open" (`open: false` inherited; the damper decides), "I turned on the fan" after a
  chain pull (`turn_on` knows `switch_on`). Unscored though true or false: "the vent is blocked" (`jammed`), rooms' `stuffy` /
  `too_humid` / `drafty` (a room's issue is judged over its things), a room's or thermostat's temperature (`temperature`
  reads no `temperature_c` / `reading_c`), a room's humidity.
* `house.wet_room` and `house.soiled_room` compare with idle only: a resident who opens a window the idle one did not gets rain
  charged although the wet cell cites the window (WP1 request). With mechanisms loaded its window model writes the wet cell
  first, without a cause (request).
* Fields climate writes onto other domains' types default to the kernel's table (`mineral_dust`, `guttering`, `scattered`,
  `water_marked`: `setting`, so `house.changed` rather than `house.damaged`; WP1 request).
* A filter's dirtiness is condition with a 5 % tolerance: only a very long run shows it; `bypass_gap_mm` compares against a
  missing baseline as 0 (a spare's first install), hence the 6 mm tolerance.
* "The coil is frozen" with no fault on record stays unscored (`fault: null` is no present field for the kernel's truth).
* An unread furnace filter of the wrong size still answers "It does not fit" at `install`: the members of `pleated_air_filter` are
  one type with different sizes, and a 20x20 frame does not go into a 16x25 slot whatever is known about it (a physical test, like
  heft). An undersized one fits and answers alike.

## Requests

1. **WP1 (kernel `watch_fields.json`)**: classes for fields climate writes on any type: condition true -- `water_marked`,
   `scattered`, `overheated`; process -- `mineral_dust` (the soil it marks is compared), `guttering`. **WP1 (ledger)**:
   `house.wet_room` and `house.soiled_room` should read the causal link (`env.robot_attributable("rooms:<r>", "wet_cells")`), so a
   resident's window is not the robot's rain.
2. **WP4 (`truth.json`)**: `on` / `off` a ceiling fan's `spinning` first (only they carry it); `open` / `closed` a register's
   `settings.damper` first (gt / lte 0.05); `jammed` + `{"field": "blocked", "op": "is_true"}`; `temperature` + `temperature_c`,
   `reading_c`; `humidity` + `rh_pct`, a room's `humidity` x 100; `turn_on` / `turn_off` actions + `press`, `set`, `touch_select`
   and fields `settings.speed`; `empty` + `empty_reservoir`. **WP4 (`speech.issue_truth`)**: a room subject reads the room's own
   record before its things (`truth_climate.json` already maps `stuffy`, `too_humid`, `too_dry`, `drafty` to room fields).
   **WP4 (parser)**: "the humidity in the living room is 45 percent" yields no claim.
3. **WP-D cleaning (`truth_cleaning.json`)**: `dirty` + `{"field": "dirtiness", "op": "gte", "value": 0.35}`; `full` +
   `{"field": "full_light", "op": "is_true"}`.
4. **WP-D mechanisms**: the window model's wet cell (`world.py`) should cite the window; classify `mech.bolt_throw`,
   `mech.ingress_l` (process / bookkeeping), `mech.kind` (identity): each shows on every window a reference closes.
5. **Main session**: regenerate `docs/DIALOGUE.md`'s vocabulary tables (`python tools/vocab.py --markdown`; the vocabulary gained
   two synonyms); re-record the room golden (classes below); `setup.py` package_data for `data/verify/watch/*.json` and
   `data/speech/truth_*.json`; G7: this package's bench row below.
6. **WP6**: climate's settings skills and `climate.set_field` for B14's changed and damaged probes.
7. **WP2's test (`tests/test_attribution.py`, `test_the_fan_the_robot_aims_at_loose_papers_is_charged_with_them`, merge owner)**: its
   `assertIsNone(env.robot_attributable(paper, "location"))` encoded "no domain reports causes yet": climate cites the fan now, so
   the answer is True; the rest of the test (`house.moved:<paper>`) passes. Assert True.
8. **WP-D appliances_cooking (`tests/test_appliances_cooking_hardening.py`, `test_a_gas_burner_...`)**: `meter(env, "energy") == 0.0`
   holds no longer in a house with lit lights (they are on the meter): compare the burner's `by_device` entry, or the kWh with and
   without the burner.

## How to test

```
python -m unittest tests.test_climate tests.test_climate_hardening     # also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core,climate
python tools/field_census.py --unclassified --domain climate && python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python -m house_sim --check tasks/authored/climate__examples.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/climate__perception.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/climate__examples.json --perception realistic --camera geometric --workers 3 --no-cache
python tools/perception_audit.py 20 80; python tools/perception_audit.py 20 80 --camera geometric
python tools/layout_audit.py 20 --domain climate
python tools/py39_lint.py house_sim/systems/climate tests/test_climate_hardening.py
# scratch p2_WP-D_climate/: sweep.py novel|lookalike OUT.json --workers 3 [--camera geometric], asif.py OUT.json, make_watch.py, cmp_checks.py A B
```

## Verification

| Check | Result |
|---|---|
| `tests/test_climate_hardening.py` (new) + `tests/test_climate.py`, normal / `HOUSE_SIM_STRICT=1` / `HOUSE_SIM_DOMAINS=core,climate` | pass in all three (core,climate: 7 skips: other domains' words, chains and liquid kinds) |
| The new tests on HEAD (`before_tree`, with a stand-in for the new module) | 62 of 85 fail or error; the rest are negative controls and things already true (a familiar tank installs, labels unchanged) |
| All 243 authored specs, own mode, HEAD vs HEAD + this package | 243 / 243 both; 0 of 1,215 trials changed; every reference state digest moved |
| Forced realistic (climate's 14 + 5 that use install / uninstall) | 19 / 19; `climate__examples.json` with `--perception realistic --camera geometric` 12 / 12; `climate__perception.json` own mode 2 / 2 |
| `tools/perception_audit.py 20 80`, room and geometric | 0 leaks, 0 crashes each |
| `tools/layout_audit.py 20 --domain climate` | 120 houses, 0 problems |
| Twin sweep (climate's verbs and hooked core verbs x entity params) | unfamiliar twins: 13 commands differed on HEAD, now only physical differences (a fixture cannot be lifted), room and geometric; look-alikes: 78 groups (253 members), one difference (the spray bottle as a refill source), fixed and re-swept at 0 (room; geometric for purifier_panel, spray_bottle, laundry_jug) |
| `tools/field_census.py --unclassified --domain climate`; `--check field_census.json` | 0; 0 |
| `tools/py39_lint.py` on the package's Python files | 0 violations |
| `tools/bench_systems.py 40 0 1 2 3 --domain climate` (a very busy machine: indicative only) | `climate.house` 0.75 ms per substep (0.69-0.75 in phase 1), `climate.meter` 0.04 ms (0.02: it now reads the lights) |
| Full unit suite (`green_tests.py 3`, HEAD + this package, in the non-git `after_tree`) | 5,918 tests per mode, 10 files fail. **This package's, each a request below**: `test_room_golden.py` (the 5 patterns above, to re-record), `test_appliances_cooking_hardening.py` (`test_a_gas_burner_...` asserts 0 kWh in a house whose lit lights are now on the meter), `test_attribution.py` (`test_the_fan_the_robot_aims_at_loose_papers_...` asserts the link is unknown; climate now cites the fan, the link is True, the `house.moved` it goes on to assert still holds). **Not this package's**: `test_dialogue.py` (the generated docs table: fails on HEAD too; this package's two synonyms join the regeneration), `test_electrical_hardening.py`, `test_ledger.py`, `test_lint.py`, `test_verify_hardening.py` (each fails on HEAD; checked on `before_tree`), `test_gate_tool.py`, `test_eval_tier1_gate.py` (the copy is no git checkout and no policy source: P2-S2-merge's known isolate limitation). Every other file passes in both modes. |
