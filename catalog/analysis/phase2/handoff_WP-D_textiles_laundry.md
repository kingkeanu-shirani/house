# Handoff WP-D textiles_laundry (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; the phase-1 carry-over)

Files (only these): `house_sim/systems/textiles_laundry/` (`hardening.py` new; `__init__.py`, `model.py`, `care.py`,
`dry.py`, `wash.py`, `fold.py`, `bedding.py`, `helpers.py`, `sewing.py`, `skills.py` changed),
`house_sim/data/types/textiles_laundry.json` (`lint_wad` gains `waste: true`; new type `lint_roller_sheet`),
`house_sim/data/verify/watch/textiles_laundry.json` (new), `house_sim/data/speech/truth_textiles_laundry.json` (new),
`docs/domains/textiles_laundry.md` (section 12, with the "Latched fields" table; header test list),
`tests/test_textiles_laundry.py` (one test rewritten: it pinned a tell), `tests/test_textiles_laundry_hardening.py`
(new, 41 tests), this handoff and `catalog/analysis/phase2/WP-D_textiles_laundry.json`. `house_sim/data/vocab/
textiles_laundry.json` is unchanged (its words already cover the truth table). Nothing committed, staged, stashed or
branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch:
`<scratch>/p2_WP-D_textiles_laundry/` (`before_tree` = HEAD 6a9e3e8 with this package's files as of bc1dc72, `final_tree`
= HEAD + exactly this package's files; the twin sweep `sweep_tl.py` / `show2.py`; `asif.py`, the references built as
schema 4; `cmp_checks.py`). This package was resumed after a usage-limit cut-off: the first run's scratch is gone and its
own "Verification" was never written, so the figures below were re-measured in the resumed run unless they say otherwise.

## What landed

1. **Refuse (6.4, 6.5).** The domain registers no mess kind (lint and fluff are refuse items; liquids are
   plumbing_liquids' kinds). What it leaves behind carries the kernel's refuse flag `waste: true`, so cleaning's
   `waste_item` measure judges where it ends (binned: sunk; dropped, held or left in a drum: relocated / in transit)
   and `house.lost` / `house.removed` exempt it: a lint wad (the type carries the flag), a peeled lint-roller sheet
   (new type), the fuzz tipped out of a fabric shaver, a dryer sheet a cycle spent (`spent` and `waste`).
2. **Absorbed liquid (6.5, 6.11: "textiles_laundry reports absorbed liquid as parcels").** A textile used as
   cleaning's `absorb` tool records the litres the spill lost, with the spill's kind and origin, in the hidden
   `_spill`; `hardening.absorbed_parcels(state, kind)` reports what it still holds (scaled by the water it holds now
   over what it held when recorded) as `(("entity", textile), origin, litres)` through the `mess_parcels` hook for
   the domain that registers the liquid kinds. Flows: `drain:<washer>` when washed, the wring destination when wrung
   (an after-hook on plumbing's `wring`), `loss:evaporation` while drying (water class only). plumbing_liquids'
   `hardening.reported_parcels` now calls the hook (its own package, same run), and `spill_kind` takes the kind from
   plumbing's `liquid_kind` / `stricter` when that domain is loaded (`sys.modules`, so a run without it imports nothing):
   the first run's rule by name said dirty water for mud water, which the floor measure counts as mop water, so the
   removal and the arrival fell into two balances. Inert until a liquid kind is registered; tested against the real
   measures (`AbsorbedLiquidMeasureTests`) and with a stand-in registration.
3. **Derived fields (8.2)**: `made`, `bed_state`, `paired_with`, `spread_on`, `drying_mode`, `on_hanger`,
   `holding_garment` (recomputes in `hardening.py`; `bedding.bed_summary` is the pure form of refresh_bed's summary,
   which refresh_bed now uses). Latched-fields table in `docs/domains/textiles_laundry.md` 12.2 (L-LATCH reads it;
   `lint.latched_fields()` lists the seven).
4. **Field classes (6.3, `watch/textiles_laundry.json`)**: 309 field names, 21 per-type tables (among them the hangers'
   `holding_garment` bookkeeping: WP1-fix's request), 9 water-sensitive types.
   `tools/field_census.py --unclassified --domain textiles_laundry`: 0 left (the first run counted 191 names and 970
   (type, field) pairs before it).
   Overrides of kernel classes that misjudge textiles: `wrinkled` (kernel: condition true, which counts any
   textile's default 0.1 creases; now up 0.15), `shrunk` / `faded` (up), `set_stains` (condition change, was setting),
   `corners_seated` (down), `made` / `pressed` (condition false), `paired_with`, `drying_mode`, `pins`, `hanger_kind`,
   `hang_method` (placement details, were settings), a washer's `door_locked` / `water_level` (process: the machine
   writes them), `scented` (process, was identity: every wash changed it), `waste` / `spent` (bookkeeping).
   Fields this domain writes on other domains' types are refined in the same file (a per-type table may name any
   type): `bed` / `crib` (`made` condition worse false, it was a kernel setting; `made_style` setting; `made_at`,
   `bed_state` bookkeeping), `cotton_rag`, `microfiber_cloth`, `rug` (the wash's, the drying set-ups' and care's fields:
   cleaning's own classes stay), `sofa`, `armchair`, `recliner`, `ottoman` (`face_soil` process), `clothes_brush` (`soil`
   process: what it catches). This replaces the first run's request to WP1.
5. **Settings skills (10.3)**: `appliance.program` (washers, dryers: set_mode), `settings.delay_h`,
   `settings.minutes` (ranges the kernel default skips), `inside_out`, `sleeves_rolled`, `folded` (unfold: the
   damaging direction), `made` (strip / make), `zipper_hidden` (fluff); plan steps through the new reference skill
   `textiles_laundry.operate`. The damaging verb for B14 (`damaged:<e>.<f>`, WP6's request): `damage.scorched` /
   `damage.melted`, a fibre whose limit is below the dial's top ironed on the top setting with
   `textiles_laundry.iron_garment`, when a plugged-in iron and a board are in its room.
6. **Truth (`truth_textiles_laundry.json`)**: states made, unmade, inside_out, right_side_out; issues pilled, felted,
   snagged, stretched, dye_transfer, melted, linty (words from the domain's vocabulary file).
7. **Hazard zones (13.3, 13.5)**: `@hook("hazard_zones")` `hot_presses`: a hot iron or steamer (>= 60 C) is a zone
   whose subject is the device.
8. **Water meter (6.8)**: a washer's fill and a soak basin's tap water count on the meter `resources.json` names
   (`model.count_water`). Energy is climate's meter (power_w of running washers / dryers, irons on).
9. **Writers and causes (6.2)**: every system stays `physical`. The fabric system runs each textile's tick with the
   textile and the heaters in its room as cause, each device ticker with its device, a washer's completion with the
   washer; the idle creasing cites the textile (`model.caused`, `model.self_cause`).
10. **World-behaviour fixes (P10, every schema)**: lint, sheets and fuzz no longer vanish (above; a fitted screen
    wiped in place leaves half in the slot and half in hand); `depill` takes only depilling tools (a grill brush or
    snow shovel "depilled" before); `feel_garment` never lights a room over a sleeper; new `wait_dry` skill;
    `sock_pile` / `linens` place look-alikes in `ctx.placer.order(...)` order (WP9); `sock_pile(owners="random")` and
    `linens(items="random")` draw through `ctx.sample_roles`; a clothes brush keeps what it lifts in its bristles (`soil`; it vanished: known gap of the first
    run, no authored spec uses the brush).
11. **Phase-1 carry-over and look-alikes** (each with a regression test in `tests/test_textiles_laundry_hardening.py` that
    fails without its fix: Verification):
    * closeout_breakages (appliances_cooking, textiles_laundry): strip_bed's `into` answered an unfamiliar hamper,
      laundry bag, garment or roller by its true type (HEAD: stripped into the hamper and the bag, refused the
      others with type wording) -- `LookalikeAndUnfamiliarTests.test_strip_bed_into_an_unfamiliar_thing_gets_one_answer`.
    * The domain-wide rule (task: "check every verb"): an order-1 before hook on all 27 verbs
      (`hardening.unfamiliar_first`) gives an unfamiliar thing in any entity parameter the kernel's identify-first
      refusal; the implicit pickers of a held tool pass an unfamiliar held thing over (`model.held_known`: lint_roll,
      depill, iron / steam, sewing needle and snips, hang_dry's pin bag). Twin sweep (`sweep_tl.py`: one bedroom
      scene, thing_1 on a slept-in bed, every verb x every entity parameter, on the bed and held, the held state also
      with every optional entity parameter left out): 55 differences over 38 unfamiliar twins before, 0 now; the 78
      look-alike groups with two or more registered members: Verification.
    * Look-alikes the sweep found: `roll_garment` rolled an unresolved crib sheet and refused fitted / flat ("Too
      large") -- now identify-first where the group's members roll differently, as `fold` does; `depill` with an
      unresolved grill brush or snow shovel named its kind -- now refused alike (no depilling tool).
    * WP-D food's request: a held unresolved bed sheet showed its own weight class (crib "light", fitted / flat
      "medium"). `perception: {"hidden": ["mass_kg"]}` on `fitted_sheet` and `flat_sheet` (the crib sheet inherits it):
      the request's first option, masking the class per group. The hand's load reading already says "light" for all
      three, and a scale's display reads the state, not the percept (`food/actions_measure.py`, read, not run).
    * closeout_REV findings 1-10 (VER2: all confirmed) name no textiles file (gate tool, cleaning, people, safety,
      mechanisms, navigation_robot, py39_lint, rules); closeout_INT3's requests are the main session's, WP9's and the
      domains' breakage rows (the strip_bed row above).
12. **Requests addressed to WP-D** (WP1, WP1-fix, WP6, WP8, WP8-fix, WP9, P2-S2-merge, WP-D cleaning, WP-D food, WP-D
    plumbing_liquids): done as above, except the invariant labels (deviation), zones / passability (no agents) and WP9's
    optional "may skip non-binding work when ctx.audit" (not adopted, and the helpers were not audited one by one for it).

## Interfaces

```python
# house_sim/systems/textiles_laundry/hardening.py
absorbed_parcels(state, kind=None) -> [(("entity", eid), origin, litres)]
scorched_steps / melted_steps (state, eid) -> [(1.0, plan step)]              # @settings_skill("damage.scorched" / ".melted")
mess_parcels(state, kind)                    # @hook("mess_parcels"): absorbed_parcels for the liquid kinds
record_absorbed(w, eid, kind, origin, litres); held_spill(state, eid) -> {"kind|origin": litres}
release_spill(w, eid, dst, cause=None, fraction=1.0, water_only=False); spill_dried(w, eid, before_l, after_l)
spill_kind(rec) -> (kind, origin); liquid_kinds_registered() -> [kind]
LIQUID_KINDS; HOT_ZONE_C = 60.0
derived_made / derived_bed_state / derived_paired_with / derived_spread_on / derived_drying_mode /
    derived_on_hanger / derived_holding_garment (state, eid)                  # @derived_field
program_steps / delay_steps / minutes_steps / inside_out_steps / sleeves_steps / folded_steps / made_steps /
    zipper_hidden_steps (state, eid) -> [(value, plan step)]                  # @settings_skill
hot_presses(state) -> [{"id": "hot_iron:<id>", "room": None, "subjects": [id]}]   # @hook("hazard_zones")
unfamiliar_first(ctx)                                                         # @before(<every verb>, order=1)
# house_sim/systems/textiles_laundry/model.py
caused(w, *ids) -> context manager; self_cause(w, eid) -> eid | None; count_water(w, liters); held_known(ctx) -> [eid]
# house_sim/systems/textiles_laundry/bedding.py
bed_summary(state, bed, children=None) -> dict; max_wrinkle(state, bed, children=None) -> float
# house_sim/systems/textiles_laundry/skills.py
textiles_laundry.operate(target, verb, hold=False, **args)
textiles_laundry.wait_dry(garment, check_s=14400, max_checks=24, where="closely")
textiles_laundry.feel_garment(...)           # now returns the inspect result (None when it waited for a sleeper)
# house_sim/systems/textiles_laundry/care.py
DEPILL_TOOLS = ("fabric_shaver", "sweater_comb", "pumice_stone")
```

## Deviations from the spec (and why)

* **No mess kind registered.** The table of 6.5 gives this domain only a share of `liquid:spill` (absorbed liquid);
  the kind is plumbing_liquids' (one registration per name). The parcels go through the `mess_parcels` hook its
  measures call. Lint is refuse (`waste_item`, cleaning's), not a kind of its own.
* **Absorbed liquid is recorded only from cleaning's `absorb`** (before / after hooks on the verb): cleaning writes the
  tool's wetness itself, not through the kernel's `liquids.wet`, so no wetting hook sees it. Wet laundry from a wash,
  rain or a soak is not mess. `absorb` logs no flow into the towel (the measure sees the arrival, `max(net_end,
  net_flow)`, and plumbing's measure keeps no origin): the flows out are logged, as the kind's balance needs.
* **Physical flows are logged only into sinks and losses** (drain, evaporation): the ledger counts a non-volition flow
  into an `elsewhere` location as relocated without the idle comparison, so a physical flow of an exogenous drip would
  be charged; drips and leak puddles are left to the end-state measure and the counterfactual.
* **Invariant labels kept** (`loose_needle`, `iron_left_on`; WP8-fix / P2-S2-merge request): L-EXPECT accepts them
  literally and a rename changes legacy wrong plans' reason labels (the trial records the legacy comparison reads).
* **A novel textile is never folded or washed by this domain's verbs**: an unfamiliar thing never resolves (inspect
  does not identify it), so the identify-first refusal is permanent -- the only answer that does not depend on what it
  really is. The old test that folded a novel t-shirt pinned the tell and was rewritten.
* **Zones / passability and hazard_created: not applicable** (no people or pets; nothing created is sharp or
  chemical). **Structured readings: not applicable** (no measuring verb: inspect reports percepts; `shrunk` and the
  sizes are `measure` tier for another domain's tape-measure verb).
* **A sheet's weight class is masked after identification too**: a per-type perception table cannot depend on
  resolution. Nothing reads it.

## Legacy outcomes (P10)

Two trees: `before_tree` (HEAD 6a9e3e8 with this package's files as of bc1dc72, its new files removed) and `after_tree`
(HEAD + this package's files), all 243 authored specs, own mode, seeds 0 1 2 41 97, `python -m house_sim --check <combined>
--seeds 0 1 2 41 97 --workers 3 --no-cache --json OUT` (scratch `cmp_checks.py` compares every field of every trial):
**243 / 243 pass in both; 0 of 1,215 trials changed** in anything but the reference state digest. 20 digests of 4 specs
changed, each explained:

| Spec | Why its reference state digest changed |
|---|---|
| `T-28ff695081cd2c-01`, `T-cc81e71dc2182e-01` | the washer's fill on the house water meter `plumbing.meter.l` |
| `T-af96a334296669-01` | the lint wad the clean-lint-screen skill peels into the trash carries `waste: true` |
| `T-5c9694a5a29d0e-01` | the rags' hidden `_spill` and their evaporation flows (cleaning's wine spec; new since the first run measured, because plumbing_liquids' kinds are registered at HEAD) |

No initial or task hash changed. **outcome_changes: none**: no authored reference or wrong plan depills with a non-depilling
tool, uses an unfamiliar thing with a textiles verb, peels a lint-roller sheet, empties a shaver, wipes a fitted lint screen or
uses `lint_roll` at all, and the sleeper rule of `feel_garment` never triggers on the five verification seeds.

Forced realistic (`--perception realistic`) on the 16 specs that use this domain's verbs or skills, `absorb` or `wring`
(scratch `affected.py`): 16 / 16 in both. The only non-digest change: the refusal text of one step of one wrong plan,
`A-8220afe27c03e7-01` wrong[1] on all five seeds (`roll_garment` of an unresolved fitted sheet: "Too large to roll; fold it"
became the identify-first message). That plan's verdict, reasons, labels and goal fraction are unchanged. It is the
look-alike fix doing its job, and it is not a verification outcome.

The after_tree above predates one late edit (a clothes brush keeps what it lifts, and its `soil` class). `final_tree` (HEAD +
the final files) re-ran the 16 specs in own mode: identical to after_tree, digests included. No authored spec uses a clothes
brush or `lint_roll`, so the other 227 were not re-run on it.

Held-out (figures from the first run, not re-measured here; WP11's runs of `A-d66994a566899c-01`): seed 503 passes (the
reference no longer lights the master bedroom over the sleeping resident); seed 500 still fails on the reference (spec debt,
Known gaps).

Room golden (`tools/golden.py dump` on before_tree and final_tree, `diff`): 1 of 52 files differs, one path pattern, a state
digest only (steps, time and evaluation unchanged):

| Entry | Path | Value | Code |
|---|---|---|---|
| specs/T-28ff695081cd2c-01 | `plumbing.meter.l` | 1.273 -> 51.273 (the washer's fill) | P2 |

This package does not own `tests/data/room_golden.json`: the row needs the re-record of the package that does (the P2-S3
merge) and the main session's signature; `tests/test_room_golden.py` fails on that fixed spec until then.

## Known gaps

* Truth of `made` reads the raw flag (speech does not overlay derived fields: WP4 request).
* Emptying a fabric shaver with no free hand drops the wad on the garment's support (it is then refuse there).
* `missing_button` / `split_seam` claims stay unscored (per-slot records): unknown, never wrong. `linty` is unknown,
  never false, when the soil dict lacks the kind.
* A wet towel dropped into a bucket or hamper keeps its parcel at `entity:<towel>`; rinsing a rag at a sink (cleaning's
  `rinse`) moves no parcel unless its water level falls (the share scales with the water it holds).
* A-d66994a566899c-01 variant 0 still fails held-out seed 500 (a wool sweater still damp after the reference's fixed
  56 h in a 12-15 C laundry room): the world is right, the reference is WP12's (use `textiles_laundry.wait_dry`).

## Requests

* **WP1 (ledger)**: `mess_findings` counts every non-volition flow into an `elsewhere` location as relocated without
  the counterfactual check (a flow sets `flow_writer`, which skips `diverges`): a physical flow logged for a process the
  idle house has too would be charged. Judge physical flows like end-state increases (diverging and linked).
* **WP4 (speech)**: kernel truth `wrinkled is_true` / `shrunk is_true` misjudge numeric textile fields (use
  thresholds: wrinkled >= 0.3, shrunk >= 0.02); `dry` should also need a thick item's `_core_wetness` <= 0.05 (states
  read one condition: an all-of form or a `$max:` pseudo-field); speech truth should read the 8.2 derived overlay
  (`made`, `paired_with` ...), as schema-4 evaluation does.
* **WP12 textiles_laundry (authors)**: `A-d66994a566899c-01` variant 0's reference should air-dry with
  `textiles_laundry.wait_dry` instead of the fixed 14 x 4 h (held-out seed 500: still damp in a 12-15 C laundry room);
  the as-if schema-4 run of the references (scratch `asif1.json`) lists the envelope entries they need: the
  laundry moved into the washer (`to` scopes; a closed drum is `house.hidden`), washer / dryer / iron settings and
  doors (`change`), consumed pods and products (`consume`), a target sheet unfolded, a bed stripped or clothes creased
  by a wash (`damage`), binned refuse (spent dryer sheets, lint wads). A wet rag or towel needs the liquid kind's
  `transit_ok` or `sinks` (`T-5c9694a5a29d0e-01`).
* **WP-D manipulation** (owner of `decor_stone` / `faux_stone`, from WP-D food's request): the sweep's one remaining
  look-alike difference is theirs: a held unresolved smooth stone shows `mass_kg` "light" or "medium" by its true type
  (`perception: {"hidden": ["mass_kg"]}` is how this domain closed the same tell).
* **WP-D cleaning**: its request list asks for `absorb` / `mop` flows into the tool; a textile's arrival is already
  measured through the hook, so a flow is optional here and must not be logged twice into `entity:<towel>` by two hooks.
* **WP6 (battery)**: this domain's settings-skill plan steps call the reference skills `textiles_laundry.operate(target,
  verb, hold, **args)` and `textiles_laundry.iron_garment` (`damage.scorched` / `damage.melted`); every textiles verb
  refuses an unfamiliar thing identify-first in any entity parameter.
* **Main session / P2-S3 merge**: re-record the room-golden row above; `setup.py` package_data for
  `house_sim/data/speech/*.json` and `house_sim/data/verify/watch/*.json` (as other packages asked).

## How to test

```
python -m unittest tests.test_textiles_laundry_hardening tests.test_textiles_laundry   # also HOUSE_SIM_STRICT=1 and
                                                                                         # HOUSE_SIM_DOMAINS=core,textiles_laundry
python tools/field_census.py --unclassified --domain textiles_laundry                  # 0 left
python tools/py39_lint.py house_sim/systems/textiles_laundry tests/test_textiles_laundry_hardening.py
REV_TREE=<tree> python <scratch>/p2_WP-D_textiles_laundry/sweep_tl.py lookalike OUT.json --workers 3  # and: novel
python <scratch>/p2_WP-D_textiles_laundry/show2.py OUT.json                            # TOTAL 0 (novel), 1 (smooth_stone)
```

## Verification

All on the final files; `final_tree` is HEAD + exactly this package's files, used so the other packages' concurrent edits of
the shared working tree (one of them left `climate` unimportable for a while) could not touch the results.

* Package tests (`tests.test_textiles_laundry_hardening` + `tests.test_textiles_laundry`, 192 tests, 41 of them in the hardening
  file), on the shared working tree: OK normal, OK `HOUSE_SIM_STRICT=1`, OK `HOUSE_SIM_DOMAINS=core,textiles_laundry` (5
  skipped there: they need another domain's kinds or verbs). `test_the_towel_holds_what_left_the_floor...` needs
  plumbing_liquids' `reported_parcels`, which is in the working tree but not at HEAD; it skips without it.
* Full suite (`tools/orchestration/green_tests.py 3` on final_tree): 92 files, 5,843 tests per mode, 16 skipped, 9 files failing in each mode. None of it is
  this package's: `test_textiles_laundry_hardening`'s one failure was the towel test above, on a tree without plumbing's hook
  (it now skips there; re-run: 41 tests, OK, 1 skipped). `test_gate_tool` and `test_eval_tier1_gate` mostly fail because
  a tree copy is not a git work tree ("policy files cannot be trusted"), which is this run's set-up. The rest fail the same way
  on before_tree, without this package: `test_ledger` (whole file: the same 8 errors and 1 failure on both), `test_lint`
  (`T-d389e2979f6aad-01`), `test_room_golden` (both digest tests, other packages' rows besides the one below),
  `test_verify_hardening` (WP6's noted pin), `test_gate_tool`'s `test_a_package_cannot_choose_another_stage`, `test_dialogue`
  (the vocabulary tables), `test_electrical_hardening` (`plug_in` in its latched-fields table). `before_tree` was not put
  through the whole suite, only through those tests.
* All 243 specs: see Legacy outcomes.
* Field census: `tools/field_census.py --unclassified --domain textiles_laundry`: 0 left. `tools/py39_lint.py` on this
  package's code and tests: 21 files, 0 violations.
* Perception audit: `16 60` (default, 84 probes), `12 60 --gen 2` (84 probes), `12 60 --camera geometric` (84 probes),
  `12 60 --twin-fields --no-probes`: 0 leaks, 0 crashes, nothing attributed to textiles_laundry. These are smaller than the
  gate's `60 80` runs, which the gate still does.
* Layout audit: `30 --domain textiles_laundry`: 180 houses, 0 problems; `30 --domain textiles_laundry --gen 2`: 330 houses, 0
  problems, 0 domain follow-ups.
* Twin sweep (`sweep_tl.py`, rewritten in the resumed run; one bedroom scene, thing_1 on a slept-in bed, every verb x every
  entity parameter, on the bed and held, held also with every optional entity left out; what a look and the hand's load show):
  - unfamiliar twins, 38 kinds: **55 differences before, 0 now**. A twin's look (its size and shape) is the kernel's own
    rendering and is not compared; a washer and a dryer cannot be picked up, so they have no held answers.
  - look-alikes, all 78 registry groups, 253 members, after: **0 differences in any verb of this domain**; 1 left in what a look
    shows, the held weight class of the `smooth_stone` group (manipulation's types: requested). Before (only the 3 groups
    with findings were re-run, bed_sheet, grill_brush, snow_shovel): 5 (`roll_garment` twice, `depill` twice, the held sheet's
    weight class).
* Regression tests fail without their fix: scratch `mutate.py` takes every fix of the carry-over and of the resumed run out of
  a copy of final_tree (the identify-first hook, `held_known`, the roll and depill rules, the sheets' hidden weight, the kind
  mapping, the brush, the damaging verb, `"random"` roles, the foreign field classes): 14 of the hardening file's 41 tests
  then fail, one or more for each, and the rest pass. (`tests/test_textiles_laundry_hardening.py` imports `hardening.py`, so it
  cannot run on the bc1dc72 files as a file; the sweep's before figures above are that tree's evidence.)
* `T-5c9694a5a29d0e-01` as schema 4 (plumbing_liquids' known gap, "a label no envelope entry allows"; working tree, seeds 0 and
  1): bare, `house.mess_in_transit:liquid:spill` and `house.mess_unaccounted:liquid:spill`; with the three rags on the
  kind's `transit_ok`, no mess label at all.
* Not run: the gate (`tier1_gate.py`), the battery on this domain's specs (they are schema 3 until WP12) and the first run's
  held-out seeds. The kitchen-scale claim for a hidden `mass_kg` was checked by reading `food/actions_measure.py` (the display
  reads the state), not by a run: my one probe never got the sheet onto the scale.
