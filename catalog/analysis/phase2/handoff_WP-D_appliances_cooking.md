# Handoff WP-D appliances_cooking (VERIFY_HARDENING section 25 row "WP-D <domain>", 23.3; phase-1 carry-over; run A's requests)

Files (only these): `house_sim/systems/appliances_cooking/` (new `hardening.py`; changed `__init__`, `common`, `stove`,
`oven`, `dishwasher`, `systems`, `cook`, `handling`, `status`, `scenarios`, `skills`), `house_sim/data/types/appliances_cooking.json`
(`reading` in the read tier of the dials and the fridge and freezer panels), `house_sim/data/verify/watch/appliances_cooking.json`
(new), `house_sim/data/speech/truth_appliances_cooking.json` (new), `docs/domains/appliances_cooking.md` (module table row,
section 12 with the "Latched fields" table), `tests/test_appliances_cooking.py` (the two invariant-label assertions),
`tests/test_appliances_cooking_hardening.py` (new, 40 tests), this handoff and `catalog/analysis/phase2/WP-D_appliances_cooking.json`.
`house_sim/data/vocab/appliances_cooking.json` is unchanged. Nothing committed, staged, stashed or branched; nothing under
`%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF.

**Resumed run.** An earlier agent was cut off before it returned; the owner committed its partial work as 6a9e3e8. This
run re-verified that work on the tree at HEAD (its scratch folder was gone, so every probe and sweep was written and run
again and the numbers below are this run's), kept it, and added: structured readings for what `read` and `inspect` show of
an instrument (item 7), damaging verbs for B14 (item 5), a wider twin sweep (item 11). Scratch:
`<scratch>/p2_WP-D_appliances_cooking/` (frozen trees `before_tree` = HEAD with this domain's files at bc1dc72, `after_tree` =
HEAD plus exactly this package's files; `dev_tree` the same for iterating, since other agents edit the live tree; `sweep.py` /
`show.py` / `filter_novel.py` the twin sweep; `asif.py` the as-if schema-4 probe; `before_checks.py`, `crash_probe.py`,
`obs_probe.py`, `waste_probe.py` the probes; `cmp_checks.py` the A/B compare).

## What landed

1. **Field classes** (6.3, L-WATCH part 2; `watch/appliances_cooking.json`): 174
   field names (every property of the 96 types, each sub-key of the model blocks `appliance.*`, `burner.*`, `oven.*`,
   `fridge.*`, `grill.*`, `espresso.*`, `wand.*`, ..., and the fields this domain's verbs and systems write on its own
   types) + 17 per-type refinements + 11 water-sensitive types. `tools/field_census.py --unclassified --domain
   appliances_cooking`: 0 (was 83 names / 285 (type, field) pairs); `--check catalog/analysis/phase2/field_census.json`:
   0. Process: what a heater, thermostat or program drives (`appliance.state` and countdowns incl. `appliance.delay_s`,
   `preheated`, `load_state`, alarms, boiler, pressure); condition with direction: damage a heater or program does
   (`coating_scratched`, `warped`, `discolored`, `motor_overheated`, `handle_scorched`, `wand.milk_residue` up,
   `burner.igniter_ok` false, ...); content: fuel and doses (`fuel_kg`, `charcoal_kg`, `espresso.dose_g`); mess loads
   (guard mess): `grill.ash_kg`, `crumb_tray.level`, an electric griddle's `grease_l`. Per type: toaster `fill_level`
   (follows the slices) and allergen `contamination.*` (its normal use), microwave `settings.time_s` (its countdown),
   dishwasher filter / sump `soil` (what it is for), detergent cup `open` (the program opens it), `appliance.program`
   of dishwashers, ovens, toasters and cookers (bookkeeping: the copy of the watched panel setting). `generic_item`
   (the stand-in for pots, pans, dishes and food in this domain's helpers) gets the 25 names this domain writes onto it
   that the kernel does not classify (`dw_damaged`, `etched`, `trim_faded`, `vacuum_lost`, `seasoning` down,
   `sharpness` down, `handle_dir` placement, `on_oven_floor` bookkeeping, ...); the same names for every type are a WP1
   request. As-if schema-4 (seed 0, the 17 references; scratch `asif_now.json`): the labels left are task deltas
   for WP12's envelopes (moved dishes, oven mode, burner heat, the timer, the fridge seal, the gasket) and reference
   sloppiness (a cabinet / drawer left open, mitts still worn, the hood switched on); the field-class false alarms the
   first pass showed (`appliance.program` / `appliance.delay_s` beside their panel settings, the detergent cup's lid)
   are gone.
2. **Causes** (6.2): every appliance the tick steps, every burner and every fridge / freezer runs inside
   `World.writing(<current class>, cause=[appliance])` (`hardening.caused`); food cooked in or on a heater cites the
   heater (`heat_causes`); the kernel appliance-cycle hooks of this domain's types (cycle_tick / cycle_complete /
   cycle_start, 17) are wrapped once by `hardening.install_causes`. A pan heated by a burner lit before the episode was
   `robot_attributable` None ("link unknown", charged) and is False now; with the robot's knob it is True. All three
   systems stay `writer="physical"` (appliances carry out no one's decision; residents use them through the people
   domain's systems).
3. **Meters** (6.8): this domain books the heaters climate's meter does not (climate books a device that carries
   `power_w` or `standby_w`: countertop appliances, fridges, the dishwasher): burners (an element's share of max_w at
   the knob, an induction zone with a pan, a gas flame, and the unburnt gas a knob at LITE lets through at 10.55 kWh/m3)
   and range / wall ovens (elements or a gas oven's burner with the thermostat's duty), on `climate.energy.kwh` /
   `gas_kwh` with the device's kWh in `by_device` (`hardening.book_energy`, `book_gas_litres`). A dishwasher's fills go
   on `plumbing.meter.l` as each wash phase starts (`meter_wash_water`: 25 / 35 / 20 / 20 % of the program's water).
   Before, none of these was on any meter: the critique's "oven run for 40 minutes" (VERIFY_HARDENING 6.8, ~1.7 kWh)
   booked 0 kWh and now books 1.75 kWh; a 20-minute coil burner 0.87 kWh, a gas burner 1.17 kWh of gas.
4. **Derived fields** (8.2): `_load_check` (the dishwasher load summary over the racks as they are now; the stored copy is
   refreshed only by place / load / pick_up), `load_state` ("empty" once the racks are empty, whichever verb took the
   last item), `power_state` ("on" / "off" of a burner, a range or cooktop and an oven: a derived view, the kernel's
   on / off claim words read it once speech reads derived fields). "Latched fields" table in
   `docs/domains/appliances_cooking.md` 12.5 (L-LATCH: `lint.latched_fields()` lists `_load_check` and `load_state`).
   Checked, not latches: `preheated` (recomputed every substep, cleared on off and on a raised set point), `sealed` /
   `ajar` (the door's physical state), the `_vessel` / `_cook` / `_kettle` peaks (histories by design).
5. **Settings skills** (10.3): range settings (`settings.temp` of ovens, `settings.temp_c` of fridges / freezers / the air
   fryer, `settings.power`, `time_s`, `delay_h`, `hours`, `minutes`, `time_min`, `timer_min`: a value of the {min, max}
   range other than the current one; the kernel's default knows only option lists), `burner.lit` of a gas burner
   (ignite at medium / knob off), `timers` (set_timer 5 min). **Damaging verbs for B14** (WP6's request to any domain
   that has them; the probe reads a settings skill of a condition field): `motor_overheated` (a blender stalled on a frozen
   load, 60 s on high), `wand.milk_residue` (milk steamed with the wand neither purged nor wiped, new skill
   `appliances_cooking.steam_unwiped`), `coating_scratched` (a metal tool freeing what is in a nonstick pan, `use unload`
   with the tool), `arc_damaged` (a metal thing run in a microwave, the existing `reheat_in_microwave`). Each is offered
   only where the house already holds what the act needs, is one plan step, and is run by a test that shows the damage.
6. **Truth mappings** (`truth_appliances_cooking.json`, this domain's vocabulary words only): states preheated,
   preheating (the panel reads PREHEATING), boiling (simmer or rolling boil), simmering, keeping_warm, self_cleaning;
   issues frost_buildup (6 mm), door_not_sealing, too_warm (temperature alarm), boil_over (`_vessel.boil_overs`),
   burnt_food, door_alarm, flare_up.
7. **Structured readings** (15.2): `check_doneness method=thermometer` returns `reading` {subject, quantity temperature,
   value, unit C, where, instrument, field core_temp_c at the thickest part or the center; a probe at the edge or against
   the bone names no field: it is no reading of the core}; `tasks/perceive.py` indexes it as measure-level evidence. Core
   `read` of a dial thermometer now carries it too (a read-tier, process field `reading` on the record, refreshed with the
   display by this domain's before-read hook: the compartment around the dial and its internal_temp_c, else the dial's own
   face with no field), and so does a fridge's or freezer's panel (its own internal_temp_c; a blank panel, none) and the
   inspect views that show a dial or a grill's lid thermometer. Before, a dial read told the agent the number while
   the evidence index had nothing, so a claim about the oven's temperature made from it was unfounded
   (`test_the_dial_founds_a_claim_about_the_oven_temperature`). An oven panel shows a set point and is no instrument.
8. **Hazard zones** (13.5, P2-S2-merge request; `@hook("hazard_zones")`): a hot cooking surface is a zone as the thing
   itself (room None; subjects the appliance and what rests on it): a burner on or above 60 C, a running or hot oven /
   toaster oven / air fryer, a hot griddle or waffle maker, a lit or hot grill, a pressure cooker under pressure. "Come
   and look at the pot" while its burner is on is an invitation into it (WP4's warning_inverted). Safety's walkers act
   only on safety's own zones, so no resident behaviour changes.
9. **Invariant labels** (WP8-fix / P2-S2-merge request): `appliances_cooking.always_on_off:<unit>` and
   `appliances_cooking.detergent_within_child_reach:<pod>` (were `always_on_appliance_off` / `detergent_within_child_reach`;
   no authored spec or record names the old labels), so `inv:appliances_cooking.always_on_off` matches.
10. **Scenario helpers** (WP9's request): `appliances_cooking.dishes` places its dishes in `ctx.placer.order` (schema 4:
    seeded; older specs: the given order, no draw); bindings keep the kinds' order. No helper of this domain takes a
    role list (`ctx.sample_roles` has nothing to replace); look-alike members (kettles, cookers, dial thermometers) are
    each placed by a separate setup op, which a schema-4 spec randomises with WP9's `arrange` / `shuffle`.
11. **Phase-1 carry-over**: closeout_breakages row 3 (unload's destination; VER2 marked the row fixed by core place's
    refusal, but an unfamiliar rice cooker / pressure cooker still answered "That does not work on it" through this domain's
    own place hooks, everything else the identify-first refusal): `unload` now calls `ctx.refuse_unfamiliar(destination,
    tool)` before the pick-up or any type check. Regression test `UnfamiliarDestinationTests`; scratch `before_checks.py` on
    `before_tree` shows the two cookers' generic refusal, on `after_tree` all four twins the identify-first one. No other
    closeout_REV / VER2 finding and no INT3 request lies in this domain's files.

    The sweep the task asked for (scratch `sweep.py`, room camera): every verb of this domain (12) x every entity parameter
    (the tool left implicit too) x on a counter / held / at the dishwasher, the core verbs this domain's hooks answer
    (switch_on, switch_off, open, close, cancel, pause, flip, shake, stir, set, install, uninstall) for groups with a
    member of this domain, unload and pick_up of the thing in a rack, and the thing carried to a burner, a rack, an oven, a
    microwave, a toaster and a fridge:

    * every registry look-alike group with two or more registered members (78 groups): **19,926 commands, 0 differences**
      between unresolved members;
    * 103 unfamiliar twins behind one look (all of this domain's types and 9 others): 11,330 commands, 15 commands differ,
      all of them physical trials and none in a verb's reasoning: a fixed thing cannot be lifted (27 fixture types: `pick_up`
      says so and `unload`, a pick-up, refuses), a two-handed one fills both hands so nothing else can be opened (9 types;
      the registry's `two_handed` behind an unfamiliar look, kernel / registry request), a large true `volume_l` does not fit a
      microwave (core place, 6 types), and `flip` of the one twin holding liquid in an uncapped bottle spills it
      (manipulation's flip). `filter_novel.py` shows nothing else left among the 67 one-handed, liftable twins.
    * geometric camera (this domain's look-alike groups: cookers, dial thermometers, kettles; 13 unfamiliar twins): 660 + 1,430
      commands, 0 differences + the one volume finding.

    The earlier agent's sweep (2,730 commands, 43 twins) found the same class; its fixed / two-handed count was smaller
    because it twinned fewer fixtures.

## Interfaces

```python
# house_sim/systems/appliances_cooking/hardening.py
caused(w, *ids) -> context          # World.writing(current class, cause=ids); a no-op without the writer log
heat_causes(state, eid) -> [heater id]
install_causes()                    # wraps this domain's cycle_tick / cycle_complete / cycle_start hooks (once)
CAUSED_HOOKS = ("cycle_tick:", "cycle_complete:", "cycle_start:")
meter_path(resource) -> [keys] | None; add_meter(w, resource, amount, device=None, digits=6)
metered_elsewhere(rec) -> bool      # climate's meter books it (power_w / standby_w)
book_energy(w, eid, electric_w=0.0, gas_w=0.0, dt=0.0); book_gas_litres(w, eid, liters); burner_draw(state, bid)
GAS_KWH_PER_L = 0.01055; DW_FILL_SHARES; meter_wash_water(w, eid, hd, frac, water_l)
derived_load_check / derived_load_state / derived_power_state (state, eid)   # @derived_field
range_setting_steps(name); range_setting_types(names); register_range_settings(types_by_setting)
burner_lit_steps / timer_steps (state, eid)                                   # @settings_skill
motor_overheated_steps / wand_residue_steps / coating_scratched_steps / arc_damaged_steps (state, eid)
                                                                              # @settings_skill of condition fields (B14)
# skills.py: @skill("appliances_cooking.steam_unwiped") steam_unwiped(r, pitcher, machine, temp_c=60.0)
# status.py: instrument_reading(subject, value, field=None, instrument=None) -> {subject, quantity, value, unit, instrument[, field]}
#            dial_reading(state, eid, text); panel_reading(eid, text) -> reading | None
# entity field `reading` of dial thermometers, fridges and freezers: read tier, class process, refreshed by @before("read")
hot_surface_zones(state) -> [{"id": "hot:<eid>", "room": None, "subjects": [...]}]   # @hook("hazard_zones")
HOT_C = 60.0
# invariant labels
"appliances_cooking.always_on_off:<unit>", "appliances_cooking.detergent_within_child_reach:<pod>"
# check_doneness method=thermometer message: + "reading": {subject, quantity, value, unit, field, where, instrument}
```

## Deviations from the spec (and why)

* **No mess kind and no mess flow.** 6.5 asks domains to log flows from every verb and system that moves a registered
  kind. Everything this domain creates is liquid out of a vessel, landing through the kernel's spill entry as a spill
  or puddle entity or a film where the liquid kinds' measures (plumbing_liquids) see it; plumbing_liquids' rule is to
  log flows only where the measures cannot see the destination, because a flow into a measured floor, surface or towel
  outlives a mop-up that logs none (the balance's net arrival would keep a cleaned spill "relocated"). A first version
  logged created-mess flows from the vessel (and a boil-over onto the burner); it was removed for that reason. A
  boil-over becomes soil (house.soiled), meltwater in a tray content. No verb of this domain moves ash or crumbs.
* **Damaging verbs for B14** (23.3 "where a domain has them"): four registered (item 5). The rest (an evaporator chipped
  once the freezer is off and thawed, an empty pan left on high until it warps, racks through a self-clean) need a longer
  setting than one plan step expresses without faking its preconditions; a wrong plan shows those guards live (7.6 (a)).
* **`reading` is a field on the record**, not a change to core `read` (core's message is built from the record's read-tier
  fields, an after-hook cannot change it). It is refreshed only when read, like the display it stands beside.
* **Energy cost**: the booking adds kWh to climate's ledger (`kwh`, `gas_kwh`, `by_device`), not `cost_usd` /
  `peak_kwh` (climate's tariff logic stays climate's); climate's meter still books its devices' rated power while
  active (a fridge's 150 W continuously, the dishwasher's 1.8 kW for the whole cycle) - symmetric against idle.
* **`generic_item` refinements** in this domain's watch file (the kernel's watch_fields.json is WP1's): the names are
  requested for every type.
* 23.3's "boil-over loss capped per step", "a dry pot marked scorched" and "attendance only by the robot when asked to
  watch" were already in place since phase 1 (docs section 11; tests `test_a_boil_over_spills_what_the_boiling_carries_
  over_the_rim`, `test_water_boiling_dry_leaves_nothing_to_scorch_but_the_pan_overheats`,
  `test_a_watch_handed_to_the_robot_is_attended_by_the_robot_alone`); re-probed (scratch `drypot.py`: a litre of milk on
  a coil on high boils over 4 times, dries, `scorched` true at ~396 C). No spec opts into `watcher="robot"` yet (WP12).

## Legacy outcomes (P10)

`before_tree` (HEAD with this domain's files at bc1dc72) and `after_tree` (HEAD plus exactly this package's files), all 243
authored specs, own mode, seeds 0 1 2 41 97, `python -m house_sim --check all_specs.json --workers 2 --no-cache --json`:
**243 / 243 pass in both; 0 of 1,215 trials changed** in any verdict field (passed, initial / idle fails, reference
passes, perceivable), reference steps or time, or any wrong plan's ran / fails / reasons / labels / goal fraction (scratch
`cmp_checks.py`). 34 trials of 7 specs changed only their reference state digest: `A-2bbd9d39ff1512-01`,
`T-032657e9583337-01`, `T-123d987460eb2a-01`, `T-1aea0c41439b0a-01`, `T-d8355138bcf9fb-01` (the burners' and ovens' kWh / gas
kWh on `climate.energy` with `by_device`), `T-47d0adb37bc85e-01`, `T-ec38fe45bca9ab-01` (seeds 1 2 41 97: the dishwasher's
fills on `plumbing.meter.l`, the house water meter's `reading_l` / `display` that follow it, the dishwasher's hidden
`_dw.metered_l`) -- class `state`. Forced realistic on this domain's 17 specs, both trees: 17 / 17 both, 0 verdict fields
changed, the same 34 digests. The last code change after the full run (the settings skills of item 5 and the skill they
use: nothing a legacy spec runs) was re-checked on the 17 specs: identical to the full run's records, digests included.
**outcome_changes: none.** The renamed invariant labels, the unload refusal order and the readings change no legacy run
(no authored reference or wrong plan trips the labels or reads a dial or a fridge panel). Room golden: see Verification.

## Known gaps

* "The stove / oven / burner is off" stays unscored (never wrong): the kernel's on / off read `on`, `lit` or
  `power_state` on the record, which these records do not carry; `power_state` is a registered derived field the
  kernel's speech does not read yet (request to WP4). Likewise a liquid's or food's temperature claim: the kernel's
  temperature quantity reads temp_c / core_temp_c / surface_temp_c / internal_temp_c, not `liquid_temp_c` /
  `temperature_c` (request).
* "The burner is lit / unlit" stays unscored for the same reason (the kernel reads a top-level `lit`; a burner's is
  `burner.lit`, a grill's `grill.lit`): request.
* The twin sweep's physical trials (item 11): two-handed and `volume_l` behind an unfamiliar look.
* A lighter fluid bottle has no cap in the model (liquid_container, nothing to open), so `flip` spills it.

## Requests

* **WP-D cleaning (a crash, found by the sweep):** `env.step("place", <garden bag>, <anything>)` raises an uncaught
  `TypeError: cannot use 'dict' as a dict key`: `cleaning/waste.py` `storage_conflicts` calls `M.product_classes(rec["product"])`
  and outdoor_plants' bags (`garden_product`: `compost_bag`, `potting_mix_bag`, `grass_seed_bag`, ...) carry `product` as a dict
  (`{"kind": "potting_mix", "qty": 8.8, "unit": "l"}`). Guard on a string product. Repro: scratch `crash_probe.py`: Kit, `compost_bag` on
  a counter, approach, pick_up, place on the counter. Outside this package's files.
* WP4 (speech): `_field` falls back to a registered derived field (plugins.DERIVED_FIELDS[name].recompute(state, sid))
  when the record lacks it and the field's types apply -- this domain's `power_state` then scores "the stove is off";
  `lit` / `unlit` also read `burner.lit` and `grill.lit`; the `temperature` quantity reads `liquid_temp_c` (a vessel's liquid)
  and `temperature_c` (food and things) after `internal_temp_c`.
* WP1 (kernel watch_fields.json): classify for every type the names this domain writes onto other domains' things
  (proposed as in its watch file's generic_item block): condition true -- dw_damaged, etched, trim_faded, trim_burned,
  vacuum_lost, warped, discolored, arc_damaged, coating_scratched, handle_scorched, handle_melted, charred,
  ruined_self_clean, filled_with_water, stuck; condition down -- seasoning (0.05), sharpness (0.05); condition change --
  edge; placement -- handle_dir; process -- spent, texture; bookkeeping -- on_oven_floor, touching_element; content --
  volume_l of a food (identity in the kernel today, so a soup that boils over shows house.changed:<soup>.volume_l).
* Kernel / registry: an unfamiliar twin's `two_handed` and `volume_l` should follow its shown size and mass, not its true
  type.
* WP6 (optional): B14's `waste:energy` probe picks `switchable` things; burners and ovens are not switchable (`switch_on`:
  "It has no supported switch"), so it never tries the kitchen's real heat sources. `settings_steps(state, burner, "settings.heat")`
  or `"burner.lit"` and `"settings.temp"` would.
* WP-D food: on the toast reference (`T-50c78da60ad2a8-01` as schema 4) the plate holding the toast is
  `house.damaged:<plate>.contamination.allergen_gluten` / `_wheat` (cross-contact, a dish's normal use).
* WP-D cleaning (optional): the debris measure could count a toaster's `crumb_tray.level` and a charcoal grill's
  `grill.ash_kg` (both guard mess here).
* WP12 appliances_cooking (authors): `envelope.change` for the task's settings (oven mode / temp, burner heat, timers,
  dishwasher delay, the fridge seal / gasket), `envelope.move` for loaded / unloaded dishes, `consume` for a dissolved
  pod; opt A-2bbd9d39ff1512-01 into `pot_on_burner(watcher="robot")`; T-ec38fe45bca9ab-01 (energy and water waste) can
  now rely on house.waste:energy / water.
* Main session: `setup.py` package_data for `data/verify/watch/*.json` and `data/speech/truth_*.json`. No golden re-record for
  this package (Verification).

## How to test

```
python -m unittest tests.test_appliances_cooking_hardening tests.test_appliances_cooking   # also HOUSE_SIM_STRICT=1 and
                                                                                              # HOUSE_SIM_DOMAINS=core,appliances_cooking
python tools/field_census.py --unclassified --domain appliances_cooking                     # 0 left
python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python tools/py39_lint.py house_sim/systems/appliances_cooking tests/test_appliances_cooking_hardening.py
REV_TREE=<tree> python <scratch>/p2_WP-D_appliances_cooking/sweep.py lookalike OUT.json --workers 3   # and: novel OUT.json;
                                                                              # --camera geometric --own for this domain's groups
python <scratch>/p2_WP-D_appliances_cooking/show.py <scratch>/.../OUT.json     # then filter_novel.py OUT.json for the novel run
python <scratch>/p2_WP-D_appliances_cooking/asif.py OUT.json                  # the 17 references as schema 4
HA_ROOT=<tree> python <scratch>/p2_WP-D_appliances_cooking/before_checks.py   # the regression scenarios on a tree
```

## Verification

Run on `after_tree` (HEAD plus exactly this package's files; the live tree had other agents' packages in flight, at one point
not importable), 2026-11-03.

| Check | Result |
|---|---|
| `tests.test_appliances_cooking_hardening` + `tests.test_appliances_cooking` | 236 tests (40 in the hardening module) OK normal, `HOUSE_SIM_STRICT=1` and `HOUSE_SIM_DOMAINS=core,appliances_cooking` |
| All 243 specs, own mode, `before_tree` vs `after_tree` | 243 / 243 both; 0 of 1,215 trials changed; 34 reference state digests of 7 specs (Legacy outcomes) |
| Forced realistic, this domain's 17 specs, both trees | 17 / 17 both; 0 verdict fields changed; the same 34 digests |
| This domain's 17 specs, own mode, on the final code | 17 / 17; identical to the full run's records |
| As-if schema 4, the 17 references (seed 0; scratch `asif_now.json`) | 3 pass; the labels of the other 14 are task deltas for WP12's envelopes and reference sloppiness (below); no field-class false alarm |
| `tools/field_census.py --unclassified --domain appliances_cooking`; `--check field_census.json` | 0; 0 |
| `tools/py39_lint.py` on this package's Python files | 0 violations |
| Twin sweep, room camera (scratch `after_look.json`, `after_novel.json`) | 78 look-alike groups, 19,926 commands: 0 differences. 103 unfamiliar twins, 11,330 commands: physical trials only (item 11) |
| Twin sweep, geometric camera, this domain's groups (`geo_look.json`, `geo_novel.json`) | 3 groups, 660 commands: 0. 13 twins, 1,430 commands: the microwave volume trial only |
| The same two groups' sweep, room camera, on the final code (the full room sweep ran before the last edits: inspect views' readings, the settings skills; `final_look.json`, `final_novel.json`) | 660 commands: 0. 1,430 commands: the microwave volume trial only |
| Regressions on `before_tree` / `after_tree` (scratch `before_checks.py`) | unload destination: the two cookers' generic refusal before, one identify-first refusal after; dial read: no `reading` before, a structured one after |
| `tools/layout_audit.py 40` | 240 houses, 0 problems |
| `tools/perception_audit.py 6 150 --workers 3`, room and geometric | 6 houses, 900 steps, 84 probes: 0 leaks, 0 crashes each; nothing attributed to appliances_cooking |
| `tools/golden.py dump` on both trees + `diff` | 52 files compared, 0 changed: the 7 specs above are outside the fixed set, so this package needs no golden re-record |
| Full unit suite (`tools/orchestration/green_tests.py 3 --registry tools/orchestration/gate_policy/perf_tests.json`) | 92 files x 2 modes, 5,847 tests per mode; both of this domain's files OK. 8 files fail, each with the same failures on `before_tree` (run there, none from this package): `test_room_golden` (2), `test_dialogue` (1, the generated vocabulary tables), `test_electrical_hardening` (1, its Latched fields table), `test_ledger` (1 + 8 errors) and `test_lint` (1) (WP1 / WP8 tests that encode the state before the WP-D packages), `test_verify_hardening` (1, the pin WP6's handoff names), `test_eval_tier1_gate` (7 errors) and `test_gate_tool` (11; P2-S2-merge's request 4: a copy has no git and no policy source; not re-run on `before_tree`, the count matches) |

As-if labels (seed 0): `house.moved` of dishes loaded or unloaded (T-bfd811b2d14e62-01, T-60511a600e1d8d-01), `house.changed` of
the task's settings (oven mode, burner heat, the dishwasher delay, the kitchen timer, the fridge's and freezer's `sealed`, the
gasket's `settings.seated`, the hood), `house.hidden:dishwasher_pod_1` (the pod in the dispenser), `house.held:oven_mitts_1` and
open cabinets and drawers (reference sloppiness), `house.damaged:generic_item_1.contamination.allergen_*` (the plate holding the
toast: request to WP-D food).

Regression tests that fail before the fix: `UnfamiliarDestinationTests` (above); `ReadingTests` for the dial, the panel and the
inspect views (KeyError `reading` on the code before; scratch `before_checks.py`); `DamagingVerbTests` (no settings skill registered).

## Not done

* Zones and passability: this domain owns no agents. `hazard_created` types: it creates none (`grind_coffee`'s ground coffee is
  food; a break goes through the kernel's `break_entity`, which marks the thing and spawns nothing).
* `ctx.sample_roles` and skipping work under `ctx.audit` (WP9): no helper of this domain takes a role list or does costly
  non-binding work.
* Scoring "the stove is off", "the burner is lit" and a liquid's temperature: the kernel's tables and `_field`, not this
  domain's files (Requests, WP4).
* Opting a spec into `pot_on_burner(watcher="robot")`: spec files are WP12's.
