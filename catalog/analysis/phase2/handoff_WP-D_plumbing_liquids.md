# Handoff WP-D plumbing_liquids (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; the phase-1 carry-over)

Resumed package: an earlier agent was cut off by a usage limit (its work is commit 6a9e3e8); this agent kept what was
sound, finished what was missing and re-verified everything. New in the resumption: the `mess_parcels` hook, flow
origins, the wrung-cloth and double-log fixes, damaging verbs, hazard zones, three field-class corrections, the drain
relation, and two look-alike tells the wider sweep found (2, 6, 7, 10, 11 below); the legacy A/B and the verification.

Files (only these): `house_sim/systems/plumbing_liquids/` (`hardening.py` new; `__init__`, `common`, `fixtures`,
`leaks`, `outdoor`, `perception`, `seen`, `skills`, `supply`, `systems`, `toilets`, `transfer` changed),
`house_sim/data/verify/watch/plumbing_liquids.json` (new), `house_sim/data/speech/truth_plumbing_liquids.json` (new),
`docs/domains/plumbing_liquids.md` (section 0e, with the "Latched fields" table 0e.4),
`tests/test_plumbing_liquids_hardening.py` (new, 54 tests), this handoff and
`catalog/analysis/phase2/WP-D_plumbing_liquids.json`. The type data and the vocabulary are unchanged. Nothing
committed, staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every
file LF. Scratch: `<home>/.claude/projects/C--Users-USER-OneDrive-Desktop-house-ai/orchestration/p2_WP-D_plumbing_liquids/`
(`sweep.py` the twin sweep; `asif.py` the as-if schema-4 runs; `regress_before.py TREE` the carry-over regressions as
probes against a tree; `ab.py` / `cmp_ab.py` the legacy A/B; `mk_after.sh`: `before_tree` = `git archive 6a9e3e8` with
this package's files as of bc1dc72, `after_tree` = that + exactly this package's current files). The first agent's
scratch did not survive the interruption, so its sweep numbers were re-measured with a rewritten, wider sweep.

## What landed

1. **Liquid mess kinds** (6.5; `hardening.py`): `liquid:spill`, `liquid:mop_water` (rho 0), `liquid:dirty_water`
   (rho 0), `liquid:chemical`, `liquid:grease`, unit litres, kernel eps / sinks. Every liquid parcel belongs to exactly
   one kind by `liquid_class` (the kernel liquids.json columns where it has a row): the product itself (bleach,
   drain cleaner, ammonia, the kernel's toxic or corrosive liquids) is chemical; spilled oil and used grease grease;
   sewage, toilet water, urine and vomit dirty water; dirty, muddy and mop water and water with more than 2 g/l of dirt
   mop water; everything else (water, drinks, soapy water, a fresh cleaning solution at use dilution) `liquid:spill`,
   which counts only outside a vessel. Parcels (`parcels(state)`, read from the state only): spill and puddle
   entities (this domain's, cleaning's `spill`, the kernel's puddles, safety's `floor_puddle`) and ice dropped from a
   tray at `floor:<room>` / `outside:<room>` / `entity:<surface>`; dirty, chemical and used-grease liquid in a vessel
   at `entity:<vessel>` (never a basin's, toilet's or hose's own water, never a product's own container, never clean
   content); safety's leaked-chemical residue `_chem_spill` at `entity:<holder>` (its largest class, not the sum over
   classes); and **what other domains report through `run_hooks("mess_parcels", state, kind)`** (textiles_laundry's
   absorbed liquid at `entity:<towel>`, fulfilling its request: a spill soaked into a towel is `mess_relocated` /
   `mess_in_transit`, which an envelope can allow, not an unexplained disappearance). Parcels report no origin (one
   group per kind; a reported parcel's origin is dropped), so every domain's flows of a kind join one balance. A spill
   of a stricter kind than `liquid:spill` records it in the hidden `_mess_kind` (turbid water named "water", any dirty
   or chemical spill: textiles_laundry reads it when something soaks the spill up).
2. **Flows** (`delivery_flow`, from `transfer.deliver`: pour, empty, wring, fill overflow, drain spigots) only where
   the measures cannot see the destination: toilet / basin / bin / plant (`entity:`), floor or storm drain (`drain:`),
   soaking ground (`outside:`). Dirty, chemical or used-grease liquid from a measured vessel is logged into every such
   destination with its origin `<class>:<vessel>` (mop water into the toilet: sunk; into the kitchen sink, the trash or
   the dog's bowl: wrong sink; onto the lawn: relocated). Liquid that was content where it came from
   (`created:<source>`, the kernel's origin `created:<step>`) only where it is out of place (a bin; for anything but
   clean liquid a plant, soaking ground or a storm drain); a product poured where it is used (drain cleaner into a
   drain, bleach into a toilet) is consumption, not mess. A cloth's water is no measured parcel (a dirty dishcloth wrung
   into the kitchen sink logs nothing; into a bin it is mess), and what a textile soaked up of a spill is logged by
   textiles_laundry's wring hook, not here as well. No flow into floors, surfaces or
   vessels (measured: a flow there would outlive a mop-up another domain logs no flow for) and none from the time
   systems (an exogenous leak is the idle house's too; what the robot's doing puts on a floor is a charged end-state
   increase).
3. **The water meter** (6.8): `plumbing.meter.l` (resources.json's path) counts every litre drawn from the supply:
   this domain's draws (`common.use_water` -> `meter_add`) and other domains' counted ones (cleaning's
   `state.count_water`). `plumbing.meter_l` stays this domain's own total; the water meter's display and its idle probe
   read the house total (`common.house_meter_l`).
4. **Writers and causes** (6.2): every system stays `physical`. `common.caused(w, *ids)` (WP2's `World.writing(...,
   cause=)`, a no-op in robot actions and untracked worlds) makes the time systems' writes cite the thing whose water
   it is: a tap / basin / toilet / spigot step its fixture, a leak its host (`leaks._leak_one`), a drip what drips, a
   holder's step the holder (`systems._holder_step`), a water ring or closed-wet mildew the wet thing
   (`leaks._ring_or_mildew_one`), a frost burst the spigot, the hose on it and its interior valve, the fallback drying
   of a spill its `plumb_source`. Tested: an exogenous leak's spill is `robot_attributable` False, a stem the robot
   snapped and a tap it left running overflowing are True.
5. **Field classes** (6.3, L-WATCH; `watch/plumbing_liquids.json`, generated by scratch `make_watch.py`): 223 field
   names for every property of `data/types/plumbing_liquids.json` and the fields this domain writes onto its own
   types, plus per-type overrides (a ceiling stain's `dripping` / `wet` / `moisture` process; a bucket's `liquid_l`,
   `liquid`, `cl_chem` content with `guard: mess`). `water_running`, `dripping`, `potable`, `bowl_l`, `tank_level_l`
   are process (they follow the handles, the hidden leaks and the contents); `basin_l`, `bowl_rim_l`, `fill_line_l`
   identity; `overflowed_l`, `drained_l` bookkeeping; washers, cross-threading, kinks, frost bursts, a spraying joint,
   a strained supply hose conditions with their worse direction. Corrected in the resumption: `soil` carries the
   kernel's `guard: soiled` (a used bucket fired `house.damaged:<bucket>.soil.*` beside `house.soiled`), `settings.mix`
   is process (every reference that used a tap fired `house.changed:<sink>.settings.mix`: where a lever rests does
   nothing while it is closed), `valve_open` is process (it follows `settings.position`, which is the setting: one
   label per valve), a hose's `liquid_l` / `liquid` are process (draining a hose fired `house.removed:<hose>.liquid_l`). `water_sensitive`: none. `tools/field_census.py --unclassified --domain
   plumbing_liquids`: 0 (was 88 names / 236 pairs).
6. **Derived fields** (8.2): `coiled` (garden hoses: on a hose hanger or reel, or still where it was coiled, hidden
   `_coiled_at`, recorded at episode start for a hose put away coiled and by the place hook; a manipulation coil record
   counts) and `drained` (outdoor spigots: while the interior valve stays closed), both latched by action hooks before.
   "Latched fields" table: `docs/domains/plumbing_liquids.md` 0e.4 (`lint.latched_fields()` lists both).
7. **Settings skills** (10.3): `settings.flow` / `left` / `right` (faucets) and `settings.valve` (spigots) through
   `turn_on_water` / `turn_off_water`, `settings.position` (valves: `shut_off` / `open_valve`), `settings.supply_valve`
   (toilets), `plug` (basins: `plug_drain` for pop-up and toe-touch stoppers / `drain`), `settings.trigger` (nozzles).
   Each step reaches its stated value (tested by executing one). **Damaging verbs for B14's `damaged` probe** (a settings
   skill for a condition field; the step is the new skill `plumbing_liquids.operate`, which holds the thing and walks to
   where it is used): `damage.washer_crushed` (a firm close of a compression faucet or spigot), `broken` (forcing a
   seized valve), `cracked` (a frozen tray twisted hard straight from the freezer), `cross_threaded` and
   `coupling_washer_crushed` (a hose threaded on at an angle or with a wrench). Each answers only when the damage can
   happen; each is tested by executing its step.
8. **Truth mappings** (13): `truth_plumbing_liquids.json` for this domain's vocabulary issues: `running_toilet` (a
   worn flapper / chain / float: tools_vehicles' `running`, `_leak_lpm`; a stuck fill valve: `_tank.fill_valve_ok`
   false; a sound tank is false), `no_hot_water` (a water heater below 35 C), `frozen_pipe` (`frozen_burst`,
   `frozen_split`). `burst_pipe` is the kernel's mapping (it already reads this domain's fields).
9. **Structured readings** (15.2): a held thermometer's or moisture meter's reading in `inspect where=closely` is also
   `readings: [{"subject", "quantity": "temperature", "value", "unit": "C", "field"}]` (a moisture meter's in percent of
   saturation, without a field) in the message; `perceive.index(env).readings` picks it up (tested).
10. **Hazard zones** (13.3, 13.5; `@hook("hazard_zones")`, `hardening.water_hazard_zones`): a floor spill of 50 ml or
    more (`spill:<id>`, `ice:<id>` when it froze), dropped ice, a vessel of hot liquid (`hot:<id>`), a tap running above
    50 C (`scald:<id>`), each the thing itself and never a room, so `speech.zones_invited` reads "come and look at the
    spill" as an invitation and "come into the kitchen" not. `hazard_created`: none (this domain creates spills and
    dropped ice, which are mess parcels; zones and passability: it owns no agents).
11. **World-behaviour fixes** (P10, every schema):
    * **closeout_breakages row 7 (electrical gear water sensitivity), the reader side in this domain's files:**
      `common.water_sensitive` = the kernel's shared `ledger.water_sensitive` (water_safe false, electronic, a domain's
      water-sensitive types) or electrical gear by its features (plug_required, rechargeable, battery_powered,
      outlet_kind, hardwired, captive_connector, power_state, gear categories) unless weather-rated or IPx4+. Used by
      `leaks.drip_onto` (never a drip catcher; the drips wet it through the kernel so `phys.wetted` runs, water damage
      from 20 ml), `transfer._surface_effects` (a spill on a surface reaches it) and a pour over it. Regression: the
      row's repro (a plugged wireless charging pad under a dripping pot) fails on HEAD (`phys.wetted` never saw the
      pad) and passes now.
    * **An object plunged into the trap** (`toilets.plunge`): relation `drain` (was `part`: still listed in the bowl,
      "Detach it first", and only `house.moved`), out of sight in every mode (`perception.lodged_out_of_sight`, a
      `visible_veto` hook, with or without cleaning) and out of reach, so `house.lost` reads it (6.4).
    * **Unfamiliar things in every verb** (review I4; this package's twin sweep): an order-1 before hook on all 15 verbs
      (`hardening.unfamiliar_first`, registered by `guard_verbs(VERBS)`) gives the kernel's identify-first refusal for
      an unfamiliar thing in any entity parameter; the implicit plunger (`toilets._plunger`), hose
      (`outdoor._held_hose`), basket stopper (`plug_drain`), reacher (`toilets` pick-up guard) and instrument
      (`seen._held`: no measuring with an unfamiliar thermometer) pickers pass an unfamiliar held thing over.
    * **Liquid into an unread spray bottle** (closeout_REV 6's class, this domain's verbs; `hardening.unresolved_first`,
      order 2): `fill`, `pour`, `empty` and `wring` answered "That cannot hold water" / "it ran out over it" for an unread
      cleaner's bottle and "It is already filled" / "it overflowed" for a blank one. Now one refusal, "Find out what is
      in it first: read its label", for any unresolved member of a group that mixes vessels and product bottles (only
      `spray_bottle`, computed from the registry); after a read a labelled member answers for itself and a blank one,
      which stays unresolved because there is nothing to read, can be filled.
    * **Water over an unresolved thing**: the kernel's consequence note "X was damaged" (electrical's ingress in
      `phys.wetted`) named a NiMH cell or a digital level and never an alkaline cell or a spirit level. No verb of this
      domain names the damage to a thing it has not identified (the damage is recorded; a later close look or the
      thing's failing tells). Other domains' pours still can: request below.
12. **Phase-1 carry-over and the look-alike / unfamiliar sweep** (every verb, not only a probe's path): `sweep.py` runs
    every plumbing_liquids verb (15) x every entity parameter and implicit tool (34 commands) x four places (the thing on
    the table; in hand; on the table with a jug or rag in hand, so the receiving verbs get as far as delivering; sprayed
    by a running hose), in a fresh env per command, comparing (ok, category, message) across members:
    **78 look-alike groups, 253 members; 26 unfamiliar twins behind one look.**
    * Room camera, before the fixes above: **16 differences in 4 groups** (`spray_bottle`: `fill` on the table and in hand,
      `pour` and `empty`; `aa_cell`, `aaa_cell`, `bubble_level`: the damage note on `empty`, `fill`, `pour` and `wring`;
      `look1.json`, `look2.json`). The first agent's sweep, narrower (no thing in hand receiving a pour), had reported 0.
      Unfamiliar twins 0 (the first agent's fix of 16 holds). After: **0 differences in all 78 groups, 0 for the
      unfamiliar twins** (`look3.json`, `novel3.json`).
    * Geometric camera (the groups aa_cell, bubble_level, laundry_jug, pill_bottle, rubber_plunger, spray_bottle; the unfamiliar twins): 0 (`look_geo.json`, `novel_geo.json`).
    * Regression tests: `UnfamiliarTests`, `LookAlikeTests`, `ReadingTests.test_an_unfamiliar_held_thermometer_measures_
      nothing`, `WaterSensitiveGearTests`, `DrainRelationTests`. `regress_before.py` on `before_tree`: 6 of 6 fail; on
      `after_tree`: 6 of 6 pass.
    * closeout_breakages: row 7 is the only row naming this domain (its reader side here; electrical listed its gear in
      `watch/electrical.json` `water_sensitive`, and this domain reads `ledger.water_sensitive`). closeout_REV findings
      (VER2: 10 confirmed, 0 refuted): none owned by this domain; finding 6's class (spray-bottle look-alikes told apart
      before a read) was found in this domain's verbs by the sweep and is fixed above. closeout_INT3 requests: only the
      closeout_breakages rows.

## Interfaces

```python
# house_sim/systems/plumbing_liquids/hardening.py
SPILL, MOP, DIRTY, CHEMICAL, GREASE = "liquid:spill", "liquid:mop_water", "liquid:dirty_water", "liquid:chemical", "liquid:grease"
liquid_class(name, chem=None, liters=None, soil_g=None, mix=None) -> "chemical"|"grease"|"dirty_water"|"mop_water"|"food"|"water"
liquid_kind(name, chem=None, liters=None, soil_g=None, mix=None) -> kind;  stricter(a, b) -> kind
record_kind(rec) -> kind;  parcel_kind(parcel) -> kind
is_spill_like(rec); product_container(rec); measured_holder(rec)
parcels(state) -> {kind: [(location_key, None, litres)]}          # the five @mess_kind measures read it
reported_parcels(state, kind) -> [(location_key, None, litres)]    # run_hooks("mess_parcels", state, kind), origin dropped
log_flow(w, kind, liters, src, dst, origin=None) -> entry | None
destination_key(w, dest, dest_kind) -> location key | None
delivery_flow(w, dest, dest_kind, parcel, source) -> entry | None  # transfer.deliver calls it
derived_coiled(state, eid) / derived_drained(state, eid)            # @derived_field("coiled" / "drained")
mark_coiled_at(w, eid); on_hose_storage(state, rec); COILED_AT = "_coiled_at"
faucet_flow_steps / faucet_left_steps / faucet_right_steps / spigot_valve_steps / valve_position_steps /
toilet_supply_steps / basin_plug_steps / nozzle_trigger_steps (state, eid)   # @settings_skill
washer_crush_steps / seized_valve_steps / frozen_tray_steps / cross_thread_steps / coupling_crush_steps (state, eid)  # damaging verbs
water_hazard_zones(state) -> [{"id", "room": None, "subjects"}]    # @hook("hazard_zones")
unfamiliar_first(ctx)                                              # before(<verb>, order=1)
unresolved_first(ctx); refuse_unread_vessel(ctx, eid); group_mixes_vessels(group)   # before(<verb>, order=2)
guard_verbs(names)
# house_sim/systems/plumbing_liquids/skills.py
plumbing_liquids.operate(target, verb, hold=False, near=None, **args)   # a verb on a thing where it is, or held at near
# house_sim/systems/plumbing_liquids/common.py
caused(w, *ids)                       # context manager: a time system's writes cite ids (no-op in robot actions)
use_water(w, liters, meter=True)      # also meter_add
meter_add(w, liters); house_meter_l(state); METER_PATH = ("meter", "l")
water_sensitive(rec); water_rated(rec); GEAR_FEATURES; GEAR_CATEGORIES
make_spill(..., mess_kind=None); add_floor_water(..., mess_kind=None); outdoor_water(..., mess_kind=None); MESS_KIND = "_mess_kind"
# house_sim/systems/plumbing_liquids/perception.py
_measure(details, field, value, eid=None)      # also a structured reading under details["readings"]
```

## Deviations from the spec (and why)

* **Parcels report no origin** (one group per kind; this domain's flows do carry theirs, which the ledger then ignores):
  cleaning's flows carry no origin, navigation's `<class>:<container>`, safety's `chemical:<container>`, textiles'
  parcels the spill's `contents_from`; per-origin groups would split one liquid's removal from its arrival whenever two
  domains handle it. The per-class sinks inside `liquid:spill` (food liquids to a kitchen sink or food waste) are then
  the kind's (water's): milk on a plant is judged by `house.damaged:<plant>.wrong_liquid`, not `wrong_sink`.
* **Dilute household cleaning solutions are mop water, not chemical** (6.5: "tap water mixed with bleach is chemical
  wherever it goes"): `liquid:chemical`'s only sink is a hazardous-waste container, which made the reference of
  `T-151f6733397156-01` (bleach mop water down the toilet, the standard disposal) a wrong sink. The product itself and
  the kernel's toxic liquids stay chemical; mop water gets zero slack (rho 0, the kernel default is 0.05) since it
  carries chemicals, so the review's towel / spare bucket / bread box / dog bowl cheats on that spec all fail.
* **A fresh cleaning solution is content until used**: a bucket made up for mopping (`cleaning.bucket`) would
  otherwise be removed into the mop's film, which no measure sees, and every mopping reference failed
  `house.mess_unaccounted:liquid:mop_water` (as-if `T-7e93003b0dcefa-01`, cleaning's request); used (dirty) water in a
  bucket at the end is `house.mess_in_transit` as intended.
* **`liquid:dirty_water` covers sewage, toilet water, urine and vomit**; dirty, muddy and mop water are
  `liquid:mop_water` because cleaning's `dump_liquid` logs every dirty liquid as `liquid:mop_water`.
* **Floor wet volume (films, wet cells) is no parcel** (6.5 lists it): a mop's film comes from the bucket's content
  and dries without a logged flow, so counting it charged every mop; `house.wet_room` judges wet floors.
* **No flows into measured places and none from the time systems** (2 above): the end-state measures and the idle
  counterfactual carry those; flows go where the measures are blind.

## Legacy outcomes (P10)

`before_tree` (`git archive 6a9e3e8` with this package's files as of bc1dc72) and `after_tree` (that + exactly this
package's current files), all 243 authored specs, own mode, seeds 0 1 2 41 97 (`ab.py`, `verify_many`, 3 workers,
house cache off): **243 / 243 pass in both; 0 of 1,215 trials changed** in any verdict field (passed, initial / idle
fails, reference passes, perceivable, goal fraction, failures), reference steps or time, or any wrong plan's ran / fails
/ labels / reasons / goal fraction (`cmp_ab.py`); no initial or task hash changed. 727 trials changed their reference
state digest (every run that draws water now carries `plumbing.meter`; dirty spills `_mess_kind`, hoses `_coiled_at`,
logs `mess_flows`). **outcome_changes: none**: on these seeds no authored reference or wrong plan reaches a changed
behaviour (liquid into an unread spray bottle, an unfamiliar thing in a verb of this domain, water on electrical gear, an
object plunged into a trap).

Forced realistic perception on this domain's 15 specs and the specs that fill or pour with a spray-bottle member
(seeds 0 1, both trees, `ab_before_real.json` / `ab_after_real.json`): 15 / 15 pass in both, 0 of 30 trials changed (a text filter over the authored specs found none outside this domain
that fills or pours with a spray-bottle member).

Room golden (`tools/golden.py dump` on both trees, `diff`): 26 of 52 files differ, one path pattern, a state digest only
(steps, time and evaluation unchanged): `plumbing.meter` (null -> `{"l": ...}`: the house water meter; in
`T-28ff695081cd2c-01`, whose washer already counted onto it, `plumbing.meter.l` 50.0 -> 51.273).

| Entries | Class | Code |
|---|---|---|
| runs/0_oracle_final_state, runs/0_realistic_final_state, runs/1_oracle_final_state, runs/1_realistic_final_state, runs/3_oracle_final_state, runs/3_realistic_final_state | plumbing.meter (state digest) | P2 |
| specs/A-a67431128da2e1-01, T-0242c820d56e7e-01, T-089c40fb507289-01, T-0e1edbe2e88e09-01, T-1df40780f5ddf2-01, T-28ff695081cd2c-01, T-3b1a4024d46110-01, T-48266979ae98d3-01, T-6732cf2c68a52a-01, T-6af2ea41755bbe-01, T-718ad251e38789-01, T-7ef5816c160945-01, T-870b0185206ce6-01, T-bfd811b2d14e62-01, T-ce25a2474ffb3d-01, T-d8d0ca913007d4-01, T-dd192c914c2116-01, T-e9cf6ca709ec77-01, T-f82f0ec54a8efb-01, X-bf82170244be22-01 | plumbing.meter (state digest) | P2 |

This package does not own `tests/data/room_golden.json`: the rows need the re-record of the package that does (the P2-S3
merge) and the main session's signature; `tests/test_room_golden.py` fails on those fixed specs until then.

## Known gaps

* **Absorbed liquid in a non-textile tool is no parcel**: textiles' hook reports what a towel or rag soaked up (as-if
  `T-5c9694a5a29d0e-01`, wine soaked up with rags the reference keeps: `house.mess_in_transit:liquid:spill` with
  `mess_unaccounted`, the case `transit_ok` is for; `ReportedParcelTests` shows it clearing both on a towel), but
  a sponge's or a mop head's load is seen by no measure (cleaning's request below). Mopping with water that is
  already mop water at the baseline stays unaccounted by the film the mop lays down (WP1's transform).
* **The ledger counts physical flows without the idle comparison or a causal link**: safety's chemical leak flows
  (now judged, since this package registers `liquid:chemical`) are relocated mess whether or not the robot had anything
  to do with the leak; an end-state increase without a flow is charged when it differs from idle in either direction
  (an exogenous leak the robot stopped early leaves less water than idle and is charged). WP1 requests.
* `low_water_pressure` has no truth mapping (no fixture field holds a pressure); the phrase "no hot water" parses as
  the state `cold`, so `no_hot_water` is reached only through its vocabulary synonyms.
* The kernel's truth table judges an honest "the bathroom sink is leaking" **false** when the leak is slow (this
  domain pops `leaking` below 2 L/min and keeps `_leak_active`; the faucet's `dripping` false decides) -- WP4 request.
* Other domains' verbs that pour on gear (cleaning's `dump_liquid`, a spill from a knocked vessel) still let the kernel's
  note "X was damaged" through for an unresolved thing: the note is the kernel's and this package's guard covers its
  own verbs only (request).
* A small thing in the bowl stays there when the toilet is flushed (it is not carried down): not a hole (it stays
  `house.moved`), but a real toilet would take a ring; left as it is because no spec reaches it.

## Requests

* **WP1 (ledger)**: (a) charge a mess flow logged by a physical writer only when it is robot-attributable (its cause
  chain, `env.robot_attributable`) or absent from the idle rollout -- today every non-volition flow counts, so
  registering `liquid:chemical` makes safety's exogenous chemical leaks relocated mess; (b) charge an end-state mess
  increase without a flow only when F exceeds idle at that location (not when it merely differs: less leak water than
  idle is no relocation); (c) read `mess_transform` (a kind change: a mop film drying, a soaked towel binned); (d) a
  liquid class table in `mess_classes.json` (name -> kind, the use-dilution and turbidity rules of
  `hardening.liquid_class`) so every domain classifies one liquid alike; (e) `watch_fields.json` classes for fields
  this domain writes onto other domains' things, today unclassified: `interior_wetness` / `interior_water_damage`
  (condition up), `lodged_in_drain`, `soggy`, `wrong_liquid`, `water_ring`, `water_damaged`, `clogged`, `strained`,
  `coupling_washer_crushed`, `stale` (condition true), `lid_on` (setting), `lid_fell`, `plumb_source` (bookkeeping),
  `growing`, `fizz`, `on_carpet` (process); and refinements: `pooled_l`, `liquid_in_bin_l` (`guard: mess`),
  `frozen_spill_l` (bookkeeping, cumulative), `drip_left_l` (process).
* **WP4 (speech)**: kernel truth for this domain's records: `leak` + `{"field": "_leak_active", "op": "is_true"}` (a
  slow leak is judged false today); `clogged` + `{"field": "_clog.amount", "op": "gte", "value": 0.3}`; `running`,
  `on` / `off` + a tap's `water_running`; `open` / `closed` + a valve's `valve_open`; acts `turn_on` / `turn_off` +
  actions `turn_on_water` / `turn_off_water` (and `shut_off` / `open_valve` for close / open of a valve); act `empty` +
  this domain's `empty` verb; `flooding` + `pooled_l`; the `temperature` quantity + `temperature_c`, `outlet_temp_c`,
  `tank_temp_c`; the lexicon reads "no hot water" as the state `cold` (the vocabulary issue `no_hot_water` is
  unreachable by that phrase).
* **WP-D cleaning**: (a) `dump_liquid` logs a strong cleaning solution as `liquid:chemical`; this package counts it as
  `liquid:mop_water` (the product itself is chemical) -- log it as mop water (or by `hardening.liquid_kind`) or the
  bucket's removal and the flow fall into two kinds (unaccounted + wrong sink); (b) log what `absorb` / `mop` take from
  a spill into a tool textiles_laundry does not report (a sponge, a mop head: `floor:<room>` -> `entity:<tool>`, the
  spill's kind) so a wipe-up is a relocation an envelope can allow instead of unaccounted; (c) the mop film (your
  request) needs WP1's transform: a film is no parcel here.
* **WP-D textiles_laundry**: `hardening.spill_kind` classifies a spill without `_mess_kind` by words (`dirty_water` ->
  `liquid:dirty_water`, sewage -> `liquid:spill`), which disagrees with these measures (`liquid:mop_water`,
  `liquid:dirty_water`) for spills other domains made: use `plumbing_liquids.hardening.liquid_kind` when loaded, or the
  removal and the arrival fall into two kinds. Spills this domain makes record `_mess_kind` and agree.
* **WP1 / WP-D electrical**: the kernel's `impact.damage` default note "X was damaged" tells unresolved look-alike
  members apart for any domain's verb that wets them (electrical's ingress fires for a NiMH cell or a digital level and
  not an alkaline cell or a spirit level): damage behind a case should give no note (a `note=False`, or electrical
  passing one); this domain's verbs skip it through `Ctx._phys_noted`, a private of `phys/settle.py`.
* **WP-D navigation_robot**: `common.liquid_mess` maps soapy water to `liquid:dirty_water` and dirty / muddy water to
  `liquid:dirty_water`; this package's measures count soapy water as `liquid:spill` and dirty / muddy water as
  `liquid:mop_water`: align (soapy -> spill, dirty / mud -> mop) so a slop and its clean-up meet in one kind.
* **WP6 (battery)**: the settings skills above for B14's `changed` and `damaged` probes on taps, valves, toilet stops,
  plugs, nozzles, hoses and trays; `trace.mess_carrier` / `accept.carried_kinds` may read `hardening.parcels` for
  liquid carriers.
* **WP12 (plumbing_liquids and the mop specs)**: as-if schema 4 (seed 0, `asif1.json`): the deltas left are the tasks' own
  (`envelope.change` for the valves, handles, plugs, lids and controller settings a reference moves, `held_at_end` /
  `soil` for tools, `transit_ok` for wet rags the reference keeps, `consume` for products used); the references should put the
  toilet lid and cabinet doors back. `T-870b0185206ce6-01`'s `house.waste:water` is the as-if run lacking
  `reference_resources` (a sink of washing water is above the 10 l floor, the allowance is 2 x the reference's use).
* **Main session**: re-record the room-golden rows listed above (digest class `state`, code P2); `setup.py` package_data
  for `data/verify/watch/*.json` and `data/speech/truth_*.json` (also requested by other packages).
* WP9's helper requests: no plumbing_liquids helper places look-alike members or takes a role list
  (`hidden_leak` draws its source with the setup RNG); nothing to adopt.

## How to test

```
python -m unittest tests.test_plumbing_liquids_hardening tests.test_plumbing_liquids   # also HOUSE_SIM_STRICT=1 and
                                                                                        # HOUSE_SIM_DOMAINS=core,plumbing_liquids
python tools/field_census.py --unclassified --domain plumbing_liquids                 # 0 left
python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python tools/py39_lint.py house_sim/systems/plumbing_liquids tests/test_plumbing_liquids_hardening.py
python tools/layout_audit.py 20 --domain plumbing_liquids; python tools/perception_audit.py 20 80
python <scratch>/sweep.py lookalike OUT.json --workers 2    # and: novel OUT.json; --camera geometric --groups a,b  (TOTAL 0)
python <scratch>/asif.py OUT.json --domain plumbing_liquids # this domain's references as schema 4
python <scratch>/regress_before.py <tree>                   # the carry-over regressions as probes
python <scratch>/ab.py <tree> OUT.json --workers 3          # then cmp_ab.py BEFORE AFTER
```

## Verification

Run on 2026-11-03 under heavy load (other packages' suites sharing the 8 cores). `after_tree` = HEAD 6a9e3e8 + only this
package's files, so other agents' uncommitted edits are not in these numbers; `before_tree` = the same with this
package's files as of bc1dc72.

* `tests/test_plumbing_liquids_hardening.py` (54) + `tests/test_plumbing_liquids.py` (179): **233 OK normal, 233 OK with
  HOUSE_SIM_STRICT=1, 233 OK with HOUSE_SIM_DOMAINS=core,plumbing_liquids (6 skips: other domains' types)**, on the live
  tree. The last edit after these runs (`valve_open` to process, one assert) re-ran the 54 in all three modes: OK.
* `regress_before.py`: **6 of 6 regressions FAIL on `before_tree`, 6 of 6 PASS on `after_tree`** (row 7's water on a plugged
  charging pad; unfamiliar hose / nozzle / plunger; fill and pour into an unread spray bottle; water over an unresolved
  cell or level; an object plunged into the trap).
* Spec files: this domain's 15 specs pass in their own modes on seeds 0 1 2 41 97 (part of the 243 of the A/B above).
* Full suite, `green_tests.py 3` on `after_tree`: 5,857 tests per mode, 8 files failing in both modes, **none from this
  package**: `test_dialogue` (`docs/DIALOGUE.md` tables, the people package's vocab: main session), `test_electrical_hardening`
  (1, its Latched-fields table: `plug_in`), `test_ledger` (8 errors `mess_kind 'debris' registered twice` with cleaning, 1
  waste_item), `test_lint` (T-d389e2979f6aad-01, a debris case) -- these four files fail identically on `before_tree`
  (`before_fail.log`: 235 tests, the same 4 failures and 8 errors); `test_verify_hardening` (WP6-absent pin, a request to WP5
  since run A); `test_gate_tool` and `test_eval_tier1_gate` ("not a git work tree": an artefact of running in a copy);
  `test_room_golden` (the re-record: other packages' rows and this package's 26).
* `tools/field_census.py --unclassified --domain plumbing_liquids`: 0; `--check catalog/analysis/phase2/field_census.json`: 0
  written fields without a class. `tools/py39_lint.py` on this package's files: 0 violations.
* `tools/perception_audit.py 20 80`: houses 20, steps 1,600, probes 84, **leaks 0, crashes 0**. `tools/layout_audit.py 20
  --domain plumbing_liquids`: 120 houses, **problems 0**. Nothing attributed to plumbing_liquids.
* As-if schema 4 (references with an empty envelope, seed 0, `asif2.json`): this domain's 15 references plus
  `T-151f6733397156-01`, `T-7e93003b0dcefa-01`, `T-5c9694a5a29d0e-01`: no `house.mess_*` or `wrong_sink` label except the wet
  rags on `T-5c9694a5a29d0e-01` (`mess_in_transit`, `mess_unaccounted`, known gaps); the bleach mop bucket into the toilet is
  `liquid:mop_water entity:utility_bucket_2 -> entity:toilet_1, 7.0` and sunk. The deltas left are the tasks' own (WP12).
* Not run: the gate records (`tier1_gate.py record`, the main session's), and this package's private stages (no split key:
  it has none of its own).
