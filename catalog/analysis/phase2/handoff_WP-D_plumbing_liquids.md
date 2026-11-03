# Handoff WP-D plumbing_liquids (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; the phase-1 carry-over)

Files (only these): `house_sim/systems/plumbing_liquids/` (`hardening.py` new; `__init__`, `common`, `fixtures`,
`leaks`, `outdoor`, `perception`, `seen`, `supply`, `systems`, `toilets`, `transfer` changed),
`house_sim/data/verify/watch/plumbing_liquids.json` (new), `house_sim/data/speech/truth_plumbing_liquids.json` (new),
`docs/domains/plumbing_liquids.md` (section 0e, with the "Latched fields" table 0e.4),
`tests/test_plumbing_liquids_hardening.py` (new, 31 tests), this handoff and
`catalog/analysis/phase2/WP-D_plumbing_liquids.json`. The type data and the vocabulary are unchanged. Nothing
committed, staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every
file LF. Scratch: `<scratch>/p2_WP-D_plumbing_liquids/` (`make_watch.py` writes the watch file; `sweep.py`,
`sweep_own.py`, `show.py`, `p3lib.py` the twin sweeps; `asif.py` the as-if schema-4 runs; `regress_before.py` the
carry-over regressions against a tree; `before_tree` = `git archive HEAD`, `after_tree` = HEAD + exactly this
package's files; `cmp_checks.py` the legacy A/B).

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
   classes). Origins are not reported (one group per kind), so every domain's flows of a kind join one balance. A
   spill keeps the strictest kind of what reached it in the hidden `_mess_kind` (turbid water named "water").
2. **Flows** (`delivery_flow`, from `transfer.deliver`: pour, empty, wring, fill overflow, drain spigots) only where
   the measures cannot see the destination: toilet / basin / bin / plant (`entity:`), floor or storm drain (`drain:`),
   soaking ground (`outside:`). Dirty, chemical or used-grease liquid from a measured vessel is logged into every such
   destination (mop water into the toilet: sunk; into the kitchen sink, the trash or the dog's bowl: wrong sink; onto
   the lawn: relocated). Liquid that was content where it came from (`created:<source>`) only where it is out of place
   (a bin; for anything but clean liquid a plant, soaking ground or a storm drain); a product poured where it is used
   (drain cleaner into a drain, bleach into a toilet) is consumption, not mess. No flow into floors, surfaces or
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
   a strained supply hose conditions with their worse direction. `water_sensitive`: none. `tools/field_census.py
   --unclassified --domain plumbing_liquids`: 0 (was 88 names / 236 pairs).
6. **Derived fields** (8.2): `coiled` (garden hoses: on a hose hanger or reel, or still where it was coiled, hidden
   `_coiled_at`, recorded at episode start for a hose put away coiled and by the place hook; a manipulation coil record
   counts) and `drained` (outdoor spigots: while the interior valve stays closed), both latched by action hooks before.
   "Latched fields" table: `docs/domains/plumbing_liquids.md` 0e.4 (`lint.latched_fields()` lists both).
7. **Settings skills** (10.3): `settings.flow` / `left` / `right` (faucets) and `settings.valve` (spigots) through
   `turn_on_water` / `turn_off_water`, `settings.position` (valves: `shut_off` / `open_valve`), `settings.supply_valve`
   (toilets), `plug` (basins: `plug_drain` for pop-up and toe-touch stoppers / `drain`), `settings.trigger` (nozzles).
   Each step reaches its stated value (tested by executing one).
8. **Truth mappings** (13): `truth_plumbing_liquids.json` for this domain's vocabulary issues: `running_toilet` (a
   worn flapper / chain / float: tools_vehicles' `running`, `_leak_lpm`; a stuck fill valve: `_tank.fill_valve_ok`
   false; a sound tank is false), `no_hot_water` (a water heater below 35 C), `frozen_pipe` (`frozen_burst`,
   `frozen_split`). `burst_pipe` is the kernel's mapping (it already reads this domain's fields).
9. **Structured readings** (15.2): a held thermometer's or moisture meter's reading in `inspect where=closely` is also
   `readings: [{"subject", "quantity": "temperature", "value", "unit": "C", "field"}]` (a moisture meter's in percent of
   saturation, without a field) in the message; `perceive.index(env).readings` picks it up (tested).
10. **World-behaviour fixes** (P10, every schema):
    * **closeout_breakages row 7 (electrical gear water sensitivity), the reader side in this domain's files:**
      `common.water_sensitive` = the kernel's shared `ledger.water_sensitive` (water_safe false, electronic, a domain's
      water-sensitive types) or electrical gear by its features (plug_required, rechargeable, battery_powered,
      outlet_kind, hardwired, captive_connector, power_state, gear categories) unless weather-rated or IPx4+. Used by
      `leaks.drip_onto` (never a drip catcher; the drips wet it through the kernel so `phys.wetted` runs, water damage
      from 20 ml), `transfer._surface_effects` (a spill on a surface reaches it) and a pour over it. Regression: the
      row's repro (a plugged wireless charging pad under a dripping pot) fails on HEAD (`phys.wetted` never saw the
      pad) and passes now.
    * **Unfamiliar things in every verb** (review I4; this package's twin sweep): an order-1 before hook on all 15 verbs
      (`hardening.unfamiliar_first`, registered by `guard_verbs(VERBS)`) gives the kernel's identify-first refusal for
      an unfamiliar thing in any entity parameter; the implicit plunger (`toilets._plunger`), hose
      (`outdoor._held_hose`), basket stopper (`plug_drain`), reacher (`toilets` pick-up guard) and instrument
      (`seen._held`: no measuring with an unfamiliar thermometer) pickers pass an unfamiliar held thing over.
11. **Phase-1 carry-over and the look-alike / unfamiliar sweep** (every verb, not only a probe's path): scratch
    `sweep.py` = every plumbing_liquids verb (15) x every entity parameter (and the implicit tool) x on the table /
    held, over every registry look-alike group with two or more registered members (78 groups, 253 members, 4,446
    commands per member set and state) and over 41 unfamiliar twins behind one look. Room camera: look-alikes **0**
    differences; unfamiliar twins 16 differences before the fix (`novel1.json`: disconnect_hose, drain, flush, plunge,
    spray, turn_on_water, turn_off_water answering an unfamiliar hose / nozzle / plunger by its type) and **0** after
    (`novel2.json`). Geometric camera: the unfamiliar twins 0 (`novel_geo.json`); the liquid look-alike groups
    (rubber_plunger, spray_bottle, laundry_jug, automotive_jug, cooking_oil_bottle, garden_spray, pill_bottle) 0
    differences except pill_bottle's 59 in `look_geo.json`, all downstream of the harness artifact cleaning's handoff
    describes (the constant handle salt makes the second geometric env in a process lose its sightings: "That is not
    visible here" at the approach); `sweep_own.py` (own handles, role tokens) shows pill_bottle, rubber_plunger and
    spray_bottle at **0** (`geo_own.json`). Regression tests: `UnfamiliarTests`, `ReadingTests.test_an_unfamiliar_held_
    thermometer_measures_nothing`, `WaterSensitiveGearTests` (`regress_before.py <before_tree>`: 3 of 3 fail on HEAD;
    pass now). closeout_REV findings: none is this domain's (VER2: 10 confirmed, all gate-tool or other domains');
    closeout_INT3 requests: only the closeout_breakages rows, of which row 7 lies (partly) in this domain.

## Interfaces

```python
# house_sim/systems/plumbing_liquids/hardening.py
SPILL, MOP, DIRTY, CHEMICAL, GREASE = "liquid:spill", "liquid:mop_water", "liquid:dirty_water", "liquid:chemical", "liquid:grease"
liquid_class(name, chem=None, liters=None, soil_g=None, mix=None) -> "chemical"|"grease"|"dirty_water"|"mop_water"|"food"|"water"
liquid_kind(name, chem=None, liters=None, soil_g=None, mix=None) -> kind;  stricter(a, b) -> kind
record_kind(rec) -> kind;  parcel_kind(parcel) -> kind
is_spill_like(rec); product_container(rec); measured_holder(rec)
parcels(state) -> {kind: [(location_key, None, litres)]}          # the five @mess_kind measures read it
log_flow(w, kind, liters, src, dst) -> entry | None
destination_key(w, dest, dest_kind) -> location key | None
delivery_flow(w, dest, dest_kind, parcel, source) -> entry | None  # transfer.deliver calls it
derived_coiled(state, eid) / derived_drained(state, eid)            # @derived_field("coiled" / "drained")
mark_coiled_at(w, eid); on_hose_storage(state, rec); COILED_AT = "_coiled_at"
faucet_flow_steps / faucet_left_steps / faucet_right_steps / spigot_valve_steps / valve_position_steps /
toilet_supply_steps / basin_plug_steps / nozzle_trigger_steps (state, eid)   # @settings_skill
unfamiliar_first(ctx); guard_verbs(names)                         # before(<verb>, order=1)
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

* **No origins** (one group per kind): cleaning's flows carry no origin, navigation's `<class>:<container>` and
  safety's `chemical:<container>`; per-origin groups would split one liquid's removal from its arrival whenever two
  domains handle it. The per-class sinks inside `liquid:spill` (food liquids to a kitchen sink or food waste) are then
  the kind's (water's).
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

(filled in by the final A/B below)

## Known gaps

* **Absorbed liquid is no parcel**: a spill soaked into rags or towels by cleaning's `absorb` / `mop` arrives nowhere
  the measures see, so the reference of `T-5c9694a5a29d0e-01` (wine soaked up with rags) is
  `house.mess_unaccounted:liquid:spill` as schema 4, a label no envelope entry allows. 6.11 gives absorbed parcels to
  textiles_laundry; cleaning's verbs log no flow into the tool (requests). The same reason leaves mopping with water
  that is already mop water at the baseline unaccounted (the film).
* **The ledger counts physical flows without the idle comparison or a causal link**: safety's chemical leak flows
  (now judged, since this package registers `liquid:chemical`) are relocated mess whether or not the robot had anything
  to do with the leak; an end-state increase without a flow is charged when it differs from idle in either direction
  (an exogenous leak the robot stopped early leaves less water than idle and is charged). WP1 requests.
* `low_water_pressure` has no truth mapping (no fixture field holds a pressure); the phrase "no hot water" parses as
  the state `cold`, so `no_hot_water` is reached only through its vocabulary synonyms.
* The kernel's truth table judges an honest "the bathroom sink is leaking" **false** when the leak is slow (this
  domain pops `leaking` below 2 L/min and keeps `_leak_active`; the faucet's `dripping` false decides) -- WP4 request.
* Damaging verbs for B14 probes (a firm close crushing a washer, an angled coupling cross-threading, a firm twist
  cracking a frozen tray) need multi-step setups (hold the hose at the spigot, a frozen tray fresh from the freezer),
  which a single settings step cannot express: not registered.
* Electrical gear's water sensitivity: this domain's readers now ask one shared predicate; whether electrical marks its
  gear `water_safe: false` (a generation-1 re-record) is still the main session's decision (row 7).

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
  a spill (`floor:<room>` -> `entity:<tool>`, the spill's kind) so a wipe-up is a relocation an envelope can allow
  (`envelope.mess.<kind>.sinks`) instead of unaccounted; (c) the mop film (your request) needs WP1's transform: a film
  is no parcel here.
* **WP-D textiles_laundry**: report absorbed liquid as parcels (6.11) -- the kind of what was soaked up, with the flows
  that move it (wringing is this domain's `wring`, which logs its flow).
* **WP-D navigation_robot**: `common.liquid_mess` maps soapy water to `liquid:dirty_water` and dirty / muddy water to
  `liquid:dirty_water`; this package's measures count soapy water as `liquid:spill` and dirty / muddy water as
  `liquid:mop_water`: align (soapy -> spill, dirty / mud -> mop) so a slop and its clean-up meet in one kind.
* **WP-D electrical**: list your water-sensitive gear types in `watch/electrical.json` `water_sensitive` (the kernel's
  `ledger.water_sensitive`, which this domain's drips and spills now read).
* **WP6 (battery)**: the settings skills above for B14's `changed` probes on taps, valves, toilet stops, plugs and
  nozzles; `trace.mess_carrier` / `accept.carried_kinds` may read `hardening.parcels` for liquid carriers.
* **Main session**: decide closeout_breakages row 7's type-data side; re-record the room-golden rows listed below
  (digest class `state`); `setup.py` package_data for `data/verify/watch/*.json` and `data/speech/truth_*.json` (also
  requested by other packages).
* WP9's helper requests: no plumbing_liquids helper places look-alike members or takes a role list
  (`hidden_leak` draws its source with the setup RNG); nothing to adopt.

## How to test

```
python -m unittest tests.test_plumbing_liquids_hardening tests.test_plumbing_liquids   # also HOUSE_SIM_STRICT=1 and
                                                                                        # HOUSE_SIM_DOMAINS=core,plumbing_liquids
python tools/field_census.py --unclassified --domain plumbing_liquids                 # 0 left
python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python tools/py39_lint.py house_sim/systems/plumbing_liquids tests/test_plumbing_liquids_hardening.py
python <scratch>/p2_WP-D_plumbing_liquids/sweep.py lookalike OUT.json --workers 2   # and: novel OUT.json; --camera geometric
python <scratch>/p2_WP-D_plumbing_liquids/show.py OUT.json                        # TOTAL 0
python <scratch>/p2_WP-D_plumbing_liquids/asif.py OUT.json                        # this domain's references as schema 4
python <scratch>/p2_WP-D_plumbing_liquids/regress_before.py <tree>                # the carry-over regressions
```

## Verification

(filled in below by the final runs)
