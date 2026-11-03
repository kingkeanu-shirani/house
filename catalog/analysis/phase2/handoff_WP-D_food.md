# Handoff WP-D food (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; the phase-1 carry-over)

Files (only these): `house_sim/systems/food/` (`hardening.py` new; `__init__.py`, `systems.py`, `model.py`,
`actions_knife.py`, `actions_measure.py`, `actions_mix.py`, `hooks.py`, `perception.py`, `percept.py`, `common.py`,
`scenarios.py` changed), `house_sim/data/types/food.json` (one word: `waste` joins the file's hidden fields),
`house_sim/data/verify/watch/food.json` (new), `house_sim/data/speech/truth_food.json` (new),
`docs/domains/food.md` (new section 13 with the "Latched fields" table), `tests/test_food_hardening.py` (new), this
handoff and `catalog/analysis/phase2/WP-D_food.json`. Nothing committed, staged, stashed or branched; nothing under
`%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch: `<scratch>/p2_WP-D_food/`
(`record_fields.py` -- every field the food tests and specs write; `make_watch.py` -- writes the watch file;
`show_classes.py`; `sweep_food.py` / `show.py` / `p3lib.py` -- the look-alike and unfamiliar-twin sweep over every
food verb; `asif.py` -- the food references built as schema 4; `make_after.sh`; `before_tree` (the working tree
before this package), `after_tree` (before_tree + exactly this package's files), `regress_tree` (after_tree with the
old crush and unfamiliar-helper code); `cmp_checks.py`).

## What landed

1. **Field classes** (6.3, L-WATCH; `watch/food.json`, written by scratch `make_watch.py` from the type data and from
   every field the food test suite and the 16 food specs write -- `record_fields.py` -- plus a static scan of the
   verbs). `tools/field_census.py --unclassified --domain food`: **0** (was 72 names / 930 (type, field) pairs). The
   food record's sub-keys are listed one by one (a domain entry for the head `food` would hide the kernel's `food.*`
   rows; `food.*` is the watched catch-all, identity): identity = what a food is and what processing made of it
   (kind, cat, phase, cut, pieces, piece_mm, peeled, parts, mix, layers, seasoned ...); content = amounts
   (`food.qty_g`, `count`, `composition`, a package's `volume_l`); process = what heat and time drive (`food.ice`,
   `cooked`, `raw`, doneness, `toast`, `ripeness`, `soggy`, `proof`, foam); condition = handling damage with its
   worse direction (`food.bruise` up, `cracked`, `torn`, `scorch` / `burnt` up, `split`, `shell_fragments`,
   `yolk_fat_g`, a knife's `sharpness` down, nonstick `coating_damage`, a smeared lid that `leaks`); bookkeeping =
   records and mirrors (`served_to`, `reported.*`, `made_on`, `derived_from`) and a bulk portion's `size_m` (it follows
   its amount and vessel). Container-derived content (WP1-fix's request, done for food): a vessel's `fill_level`
   (model.update_fill) and a grocery bag's `fill_l` / `load_kg` are bookkeeping. Contamination on food's own types:
   raw-meat juice, germs, a chemical, glass and the allergens are condition (up); kosher meat / dairy contact,
   flavour / spice taints, capsaicin and wax are process (the eater checks judge them when food is served).
   `water_sensitive`: recipe card, kitchen scale, outer packaging, parchment, grocery bag, cereal and pizza boxes,
   spice grinder. `writes`: the names food writes on other domains' things (for WP1, as manipulation's file).
2. **Refuse** (6.4, 6.5; `hardening.py`): prep scraps (peels, trimmings, shells, bones), removed outer packaging and
   a worn pair of disposable gloves carry the kernel's refuse flag `waste: true` -- before the baseline for those in
   the house (`mark_refuse`, episode_start) and from their creation for the scraps pile (`model.add_scraps`), the
   eggshell halves (`actions_mix._shell_to_hand`), the sleeve `unwrap` takes off (`hooks._after_unwrap`) and gloves
   taken off after use (`hooks._after_take_off`), with a `waste_item` mess flow from `created:<id>` (core logs every
   later move of a `waste: true` thing). Cleaning's `waste_item` measure judges them: scraps left on the board are
   `house.wrong_sink:waste_item` (a cutting board is food contact), scraps composted are sunk; `house.lost` /
   `house.removed` exempt refuse. Food registers **no mess kind** (see deviations).
3. **Writers and causes** (6.2): `food.thermal`, `food.safety`, `food.refresh` declared `writer="physical"`. Causes:
   raw meat dripping onto food below cites the dripping pack (`systems._drips`); grit settling in a soaking bowl cites
   the food it came out of. Every other physical change of a food already links to the robot through its location
   chain (`HouseEnv.robot_attributable`); a cause there could only end a chain early (an uncaused pan temperature),
   so none is added.
4. **Settings skills** (10.3): `opened` of a packaged food (`use open`, with the can opener / corkscrew it needs when
   the house has one; one way), `food.bruise` of soft produce (a firm pick-up; B14's damaged probe). The kitchen
   scale's `on` and `settings.unit` use the kernel defaults.
5. **Water meter** (6.8): a refreshed cold-water thaw bath (`thaw refresh_water=true` on an existing bath) empties and
   refills its bowl from the tap -- the litres count on `resources.json`'s meter (`hardening.count_water`).
6. **Structured readings** (15.2): `weigh` returns `reading` {subject, quantity mass (volume for ml), value, unit,
   field mass_kg}: the one thing it weighs (the single item on an un-tared scale, the single *visible* thing in a tared
   vessel), else the scale's load (subject the scale, no field); an inspection with a probe thermometer adds
   `readings` [{subject, temperature, value, C, field core_temp_c}], a cold space's dial thermometer
   `internal_temp_c`. Subjects become handles in realistic mode; `perceive.index(env).readings` picks them up.
7. **Truth mappings** (`truth_food.json`; every condition reads a field only food records carry): states frozen /
   thawed (`food.ice` >= 0.5 / <= 0.05), cooked (`food.cooked`, else not a raw animal product), raw (only a cooked
   flag that is false), ripe / unripe / overripe (`food.ripeness`); issues expired (`_food_status.days_left` < 0),
   bruised (`food.bruise` or `damage.bruised` > 0.3), bulging (`_bulging`), freezer_burn (> 0.3); act cook (end state
   cooked). "The chicken is cooked" about raw chicken is now a false claim.
8. **Latched fields** (8.2; `docs/domains/food.md` 13.6): `_report_check` (a report graded when it was made) and
   `served_to` (who was handed the food) are hook-written and never recomputed; neither can be derived from the state,
   so they are listed without a recompute and L-LATCH refuses them as schema-4 success evidence. Food's derived truth
   (`_food_status`, `_zone`, `_storage_ok`, `_fifo_ok`, `_dish`, `_recipe_check`, `_label_check`, `_policy_ok`,
   `_packaging_ok`) is recomputed by `food.refresh` after every action and `food.safety` every 300 s: no latch, no
   `@derived_field` needed.
9. **World-behaviour fixes** (P10, every schema):
   * `crush` with an improvised pounder judged its true mass, so a held **unresolved look-alike** told its members
     apart: an empty plain spray bottle (0.1 kg) was "too light to crush with" while a full cleaner crushed, a
     pre-filter panel was refused where a HEPA / carbon panel crushed. An unresolved pounder now gets the kernel's
     identify-first refusal (`Ctx.refuse_unidentified`; a first version judged it by the felt weight, which still
     split the bed_sheet and smooth_stone groups, whose held weight class differs in the kernel's observation);
     identified things keep the 0.2 kg rule.
   * The `food.kitchen` stand-in compost caddy (a storage bin labelled "compost caddy") was no waste container for the
     kernel or for cleaning (the trash stand-in had `accepts_any_waste`); it now carries `waste_bin: true`, so refuse
     composted in it is sunk mess.
10. **Phase-1 carry-over.** closeout_breakages row "navigation_robot, food, manipulation": `common.held_known` uses
    `Ctx.unfamiliar`, `common.tool` delegates to `core.require_tool` (which carries the unfamiliar rule since the
    close-out; food's own copy of the loop is gone) and `percept.unfamiliar_first` refuses through
    `Ctx.refuse_unfamiliar`. closeout_REV / VER2 findings: none lies in food's files; the look-alike and unfamiliar
    rows were checked for **every** food verb by the sweep below.
11. **WP9's request**: `food.groceries` places its items in `ctx.placer.order(items)` order (seeded for schema 4, the
    given order for older specs: no draw). No other food helper places look-alike members or takes role lists.

## The look-alike / unfamiliar sweep (every food verb)

Scratch `sweep_food.py` (adapted from cleaning's): a kitchen scene (`food.kitchen`), thing_1 on the counter, an onion
on the board as the food partner and a mixing bowl as the vessel partner; for every food verb (38) and every entity
parameter (and `portion`'s `into` list, and the tool left implicit when held) thing_1 takes that parameter, the target
otherwise being the onion and every other entity parameter the bowl; on the counter and held; a difference in
(ok, category, message) between twins is reported.

* Look-alikes: all 78 registry groups with two or more members (225 commands per member and state). **Before**
  (before_tree): 8 differences in 4 groups -- crush:tool for spray_bottle (13 members), purifier_panel, smooth_stone;
  plate / transfer / crack_egg for egg_in_shell. **After** (after_tree, all 78 groups, with the felt-weight version
  of the crush fix): 7 -- crush:tool for bed_sheet (new: the crib sheet feels light, the others medium) and
  smooth_stone, and egg_in_shell; with the final identify-first version the crush groups (spray_bottle,
  purifier_panel, bed_sheet, smooth_stone) are **0** (scratch `after_look_sub2.json`). Left, by design: egg_in_shell
  -- only the handle of the newly made portion differs (its id is named after the source's kind: an opaque handle no
  agent can read) and cracking a boiled egg shows it is cooked inside (the act itself resolves the group). The kernel's
  own observation of a held bed sheet or smooth stone shows a different weight class per member ("light" vs "medium"):
  a kernel / textiles / decor look-alike matter, reported below. The wheeled_cart group could not be built (no
  counter with room beside a curb cart in that kitchen).
* Unfamiliar twins: 58 true kinds (food items, packages, knives and kitchen tools, vessels, gloves, a scale, and
  other domains' bottles, sponges, documents, stones, pet bowls, kibble) behind one unfamiliar look, the 38 verbs in
  10 jobs (225 commands per twin and state): **0 differences** (scratch `after_novel.json`).

## Interfaces

```python
# house_sim/systems/food/hardening.py
is_refuse(rec) -> bool
mark_refuse(w, env)                                      # @hook("episode_start"): waste: true before the baseline
refuse(w, eid, created=True)                             # flag a new piece of refuse; log its created flow
log_created(w, eid)                                      # waste_item flow "created:<eid>" -> where it lies
package_open_steps(state, eid) / bruise_steps(state, eid)   # @settings_skill("opened" / "food.bruise")
count_water(w, liters)                                   # the water meter of resources.json
reading(subject, quantity, value, unit, field=None) -> dict
scale_reading(state, scale, display, known=None) -> dict | None
# house_sim/systems/food/systems.py
_cause(w, rid, *ids)                                     # World.cause guarded (no-op without tracking)
# house_sim/systems/food/common.py (unchanged signatures, kernel helpers inside)
held_known(ctx); tool(ctx, capability, tool_id=None)
# weigh's result: {"scale", "display", "reading"}; inspect details may carry "readings": [reading, ...]
```

## Deviations from the spec (and why)

* **No mess kind registered.** Food's refuse is the kernel's `waste: true` (cleaning's `waste_item` measure counts
  it; a second registration would raise). Solid food spilled soils the surface below (`house.soiled`); liquid food is
  a kernel spill (the liquid kinds are plumbing_liquids'). **`liquid:grease` is not registered**: rendered fat is a
  food entity in a pan or a jar, and the kernel's grease sink class (`grease_container`) has no household container
  type, so a jar of saved bacon fat -- a normal kitchen practice -- would be relocated mess; fat down a drain stays
  the `food.fat_down_drain` invariant in every schema.
* **Latched goal fields without a recompute** (`_report_check`, `served_to`): they record events (a report, a hand-
  over); no recompute from the state exists, so they are listed for L-LATCH to refuse rather than registered.
* **Truth: hot / warm / cold not mapped.** The only per-entity temperature field is the kernel's `temperature_c`,
  which rooms carry too ("the kitchen is cold" would be judged by food thresholds); no food-only temperature field
  is present on every food. Request to WP4 below.
* **`raw` claims are scored only on a cooked flag that is false** (raw produce carries no cooked flag, and
  `food.raw` means "raw animal product": "the carrot is raw" must not be false). A claim that produce is cooked is not
  caught (known gap, never a false alarm).

## Legacy outcomes (P10)

`before_tree` (the working tree as it stood before this package, copied without `.git`) and `after_tree` (before_tree +
exactly this package's files), `python -m house_sim --check <combined> --no-cache --json`, own mode:

* all 243 authored specs on seeds 0 and 1: **243 / 243 pass in both; 0 of 486 trials changed** in any verdict field
  (passed, initial / idle fails, reference passes, steps, time, failures, perceivable) or any wrong plan's ran /
  fails / reasons (scratch `cmp_checks.py`);
* the 16 food specs on seeds 0 1 2 41 97: **16 / 16 in both; 0 of 80 trials changed**.

**outcome_changes: none.** The world-behaviour fixes change no legacy outcome: no authored reference or wrong plan
crushes with an unresolved look-alike, and the food specs that compost into the stand-in caddy are scored on nothing
cleaning's stream judgement of the caddy now writes. Room golden (`tools/golden.py dump` / `dump --all` + `diff` on
the two trees): the 8 fixed-script runs are unchanged; **5 of 243 spec references** change their final state digest
only (steps, time and evaluation identical), one path family each:

| Entry | Paths | Why |
|---|---|---|
| specs/T-1df40780f5ddf2-01 (one of the 20 fixed specs) | `entities.eggshell_*.waste`, `logs.mess_flows`; `entities.storage_bin_3.waste_bin`, the eggshells' `in_stream` / `stream_ok` / `discarded_t` / `_cl_judged_in`, the caddy's `stream_errors` / `contaminated` / `lid_blocked`, `cleaning`, `_cleaning_watch` | eggshells are refuse from their creation; the stand-in compost caddy is a waste bin, so cleaning's stream rules judge what goes in (this household's rules want food scraps in the trash) |
| specs/T-a03bc04a3a311c-01 | the same families (crack eggs into the caddy) | the same |
| specs/T-a853f28cce9c86-01 | `entities.scraps_1.waste`, `logs.mess_flows` | the onion's scraps pile is refuse |
| specs/X-44e90cc83ba0db-01 | `entities.scraps_1.waste`, `entities.food_glove_pair_1.waste`, `logs.mess_flows` | scraps and the worn gloves are refuse |
| specs/X-befab43585cde3-01 (safety) | `entities.generic_item_1.waste`, `logs.mess_flows` | a worn pair of disposable gloves taken off is refuse |

This package does not own `tests/data/room_golden.json`: `tests/test_room_golden.py` fails on the one fixed entry
(T-1df40780f5ddf2-01) until the P2-S3 merge re-records it with the main session's signature; the other four are
`HOUSE_SIM_GOLDEN_ALL=1` rows.

## Known gaps

* The as-if schema-4 run of the 16 food references (`asif.py`, seed 0) shows the expected task deltas authors will
  put in envelopes (food cut / peeled / seasoned: `house.changed`; portions taken: `house.removed:<src>.food.qty_g`
  and `.volume_l`; groceries put away: `house.hidden`; scale settings), refuse left on a board or in a scrap bowl
  (`house.wrong_sink:waste_item`) and contamination on core counters and cleaning's trash can (kernel / cleaning
  classes; requests below).
* `spoiled`, `mold`, `open` / `sealed` of a package, `empty` and temperature quantities are the kernel's truth keys
  and read no food field (requests below); "the milk carton is empty" is judged true for a full carton today
  (`$contents` counts only children and liquid_l).
* Contamination tags on core types (counters, sinks) and cleaning's bins keep the kernel's condition class: kosher
  and flavour marks on a counter are `house.damaged` until WP1 / cleaning refine them (requests).

## Requests

* **WP4 (kernel truth.json / speech.py)**: (a) `spoiled` and `mold` read `spoiled` / `mold` fields food records do
  not carry -- add `{"field": "_spoil", "op": "gte", "value": 1.0}` and `{"field": "_rotten", "op": "is_true"}` (food's
  hidden spoilage clock and rot flag), so "the milk is fine" about spoiled milk is a false claim; (b) `$contents` counts
  only children and `liquid_l`, so "the milk carton is empty" is judged **true** for a full 1.95 kg carton (a package
  is itself a food record): count a food record's own `food.qty_g` (> 0.5 g); (c) `open` / `sealed` of a package read
  `open` / `sealed`, food packages carry `opened` (unopened = sealed); (d) `quantities.temperature` lacks
  `temperature_c` (food's, and most entities'), so a reported food temperature is unscored; (e) a way to scope a
  condition to a kind of subject (e.g. `"if": {"field": "food"}`), so food can map hot / warm / cold on `temperature_c`
  without judging rooms by food thresholds.
* **WP1 (kernel watch_fields.json / ledger)**: (a) contamination tags on core types (counters, sinks, a fridge
  handle): `contamination.kosher_meat`, `kosher_dairy`, `flavor_*`, `spice_*`, `capsaicin`, `wax` are food-law and
  taste marks the eater checks judge at serving -- process, not `house.damaged` (food's own types already do this);
  (b) apply a domain's watch-file `writes` to those names on things of any type (manipulation's request; food's
  `writes` list is in its file); (c) optional: kernel `food.*` rows mirroring `watch/food.json` for food records of
  other domains' types (pets' kibble), whose sub-keys otherwise fall to the kernel's partial `food.*` list or the
  watched default.
* **WP-D cleaning**: contamination on a waste container (`kitchen_trash_can`, `compost_caddy`, ...) is no damage --
  the as-if crack-egg reference fires `house.damaged:kitchen_trash_can_1.contamination.allergen_egg / raw_meat /
  bacteria` for binned eggshells; classify `contamination` / `contamination.*` of the bin types as bookkeeping in
  `watch/cleaning.json`.
* **Kernel realism / the owners of the bed_sheet (textiles_laundry) and smooth_stone groups**: a held unresolved
  member shows its own weight class (`mass_kg` "light" for a crib sheet or the hollow resin stone, "medium" for a
  fitted / flat sheet or the river rock) while the spray_bottle group shows one class for all; either mask the held
  weight class per group (as for spray bottles) or make the group resolve by heft.
* **WP6 (battery)**: B14's damaged probe can use `plugins.settings_steps(state, eid, "food.bruise")` (soft produce),
  and the changed probe `settings_steps(state, eid, "opened")` for packaged food.
* **WP12 food (authors)**: the as-if references need `envelope.consume` for portions and ingredients taken (the
  source's `food.qty_g` and `volume_l`), `envelope.change` for food cut / peeled / seasoned and scale settings,
  `to` scopes for groceries put away (`house.hidden` in the fridge and cabinets), `held_at_end` or a final put-down,
  and either a reference step that composts the scraps or `envelope.mess.waste_item.allow_at`.
* **Main session**: re-record the room-golden rows listed under Legacy outcomes (this package does not own
  `tests/data/room_golden.json`); `setup.py` package_data for `data/verify/watch/*.json` and `data/speech/truth_*.json`
  (already requested by WP1 / cleaning / manipulation).

## How to test

```
python -m unittest tests.test_food_hardening tests.test_food      # also HOUSE_SIM_STRICT=1 and
                                                                     # HOUSE_SIM_DOMAINS=core,food
python tools/field_census.py --unclassified --domain food           # 0 left
python tools/py39_lint.py house_sim/systems/food tests/test_food_hardening.py
python -m house_sim --check tasks/authored/food__examples.json --workers 3
python -m house_sim --check tasks/authored/food__perception.json --workers 3
python <scratch>/p2_WP-D_food/sweep_food.py lookalike OUT.json --workers 3   # and: novel OUT.json
python <scratch>/p2_WP-D_food/show.py OUT.json
```

## Verification

* `tests/test_food_hardening.py` (35 tests) with `tests/test_food.py` (174): 209 OK normally, 209 OK with
  `HOUSE_SIM_STRICT=1`, 209 OK with `HOUSE_SIM_DOMAINS=core,food` (8 skips: other domains' types, verbs or the
  waste_item kind). Regressions that fail on the old code (scratch `regress_tree` = after_tree with the old file):
  LookalikeTests 4 of 4 (crush with spray bottles, air-filter panels, sheets and stones, the identify-first refusal;
  old `actions_knife.py`), UnfamiliarHelperTests 2 of 3 (old `common.py` / `percept.py`), the stand-in caddy and the
  groceries order (old `scenarios.py`); every other new test exercises `hardening.py` / the watch and truth files,
  which do not exist before.
* `tools/field_census.py --unclassified --domain food`: 0; `--check catalog/analysis/phase2/field_census.json`: 0.
* `tools/py39_lint.py house_sim/systems/food tests/test_food_hardening.py`: 18 files, 0 violations; every file LF.
* Specs: all 243 on seeds 0 1 (before / after: 243 / 243, 0 trials changed); the 16 food specs on 0 1 2 41 97 (16 / 16,
  0 changed; re-run with the final crush code: 16 / 16, 0 changed); `food__perception.json` with `--camera geometric`
  4 / 4; `food__examples.json` with `--perception realistic` 12 / 12.
* `tools/perception_audit.py` (live tree): room camera 20 houses, 2,400 steps, 84 probes, 0 leaks, 0 crashes;
  geometric 10 houses, 800 steps, 0 leaks, 0 crashes. `tools/layout_audit.py 20 --domain food`: 120 houses, 0 problems.
  Nothing attributed to food.
* Sweeps: see "The look-alike / unfamiliar sweep" (look-alikes before 8 differences, after only egg_in_shell's
  by-design 5; unfamiliar twins 0).
* Full suite (`tools/orchestration/green_tests.py 2`, live shared tree while the other run-B packages edited their
  domains; `test_food_hardening.py` ran there at 33 tests): 172 of 184 file-mode runs ok; the 12 failing runs are 6
  files in both modes, none of them food's and none caused by food: `test_admin_inventory_hardening` (admin's own
  watch file, concurrent work), `test_dialogue` (the vocab tables after other packages' vocab edits),
  `test_ledger` (cleaning's `debris` / `waste_item` registrations, cleaning's handoff), `test_lint` (cleaning's
  derived `in_stream`), `test_verify_hardening` (WP5's WP6-absent pin), `test_room_golden` (re-records of several
  packages; food's T-1df40780f5ddf2-01 among them, see Legacy outcomes).
