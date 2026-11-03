# Handoff WP-D electrical (VERIFY_HARDENING section 25 "WP-D electrical", 23.3; phase-1 carry-over; run A's requests)

Files (only these): `house_sim/systems/electrical/` (new `hardening.py`; changed `__init__`, `batteries`, `cables`,
`charging`, `common`, `cords`, `harms`, `hub`, `lighting`, `percept`, `physical`, `power`, `skills`),
`house_sim/data/verify/watch/electrical.json` (new), `house_sim/data/speech/truth_electrical.json` (new),
`docs/domains/electrical.md` (section 16, with the Latched fields table), `tests/test_electrical_hardening.py` (new),
this handoff and `WP-D_electrical.json`. Type data and vocab unchanged. Nothing committed, staged, stashed or branched;
nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch: `p2_WP-D_electrical/`
(probes named below).

## What landed

1. **Field classes** (6.3, L-WATCH): `watch/electrical.json` classifies every property name of every electrical type
   (234 names, 143 types) and every field electrical writes onto its own types (a write probe over the electrical
   tests, the 16 specs' idle / reference / wrong plans on seeds 0 and 1 and generated-house waits: 192 fields), by the
   domain itself: 347 field names. Ratings, kinds, wiring, printed text: identity. What the robot sets (on, plugged_in,
   plug_seated, outlet, cord_route, coiled, settings, clock, alarms, pairing, ends, ports, slots, captive_at, covers):
   setting. What electrical derives every refresh (energized, live, powered, handle, load_a, brightness, dim_level,
   flicker, display, display_source, audio_output, charging, battery_level, links, boot / job / transfer states, alarm
   LEDs / states, CO readings, cord_cells, cord_length_used_m): process -- the setting that caused it is compared --
   except a lamp's `lit` (the kernel's light allowance keeps it a setting). Damage and faults: condition with a worse
   direction (tripped, gfci_tripped, scorched, socket_scorched, ignited, burnt_out, plug_damage, strain_relief_damaged,
   kinked, damaged, strain_damage, leaking, battery_swollen, thermal_runaway, fried, esd_damaged, pin_damage,
   corrupted, os_corrupted, data_lost, recording_lost, job_interrupted, unsafe_removal, water_ingress, screen_cracked,
   cord_hazard, functional (false), reversed / mixed cells, cct_mismatch). Counters, test records, timestamps:
   bookkeeping (resets_after_trip was the kernel's condition). Per type: cells' `condition` word is process (wear; the
   leak is `leaking`); the core light types take burnt_out / dim_level / flicker. `field_census.py --unclassified
   --domain electrical`: 0; `--check`: 0.
2. **Water-sensitive gear** (closeout_breakages row 6, the part in electrical's files): `water_sensitive` lists the 87
   gear types `physical.electrical_gear` names that no IP / weather rating protects (a base only when every type below
   it is sensitive: not `wall_outlet` -- the outdoor outlet is one --, not `rechargeable_device` -- IP68 phones), so the
   kernel's `ledger.water_sensitive(rec)` is the one predicate for electrical gear (house.wet; plumbing_liquids'
   `common.water_sensitive` already calls it). Cleaning's and mechanisms' readers still ask `electronic or water_safe
   is False` (requests); `water_safe: false` in the type data would change every generated house (gen1, a class this
   package does not have).
3. **Causes** (6.2): every electrical system is `writer="physical"` (electrical carries out no resident's or pet's
   decision). Physical writes now cite what they follow from: `common.set_if` cites `hardening.inputs(state, eid, w)`
   (the thing, supply path, breakers, the load on a tripped breaker / strip / GFCI on that path, what it sits in or on,
   its installed parts, its switches) for every watched field (process and bookkeeping fields cite nothing: the ledger
   never compares them, and it keeps the per-substep cost off the hot path); a breaker trip cites its circuit's loads
   and shorted devices, a strip trip its loads, a GFCI the faulted device, a cord / arcing outlet `causal_ids`, a burnt
   bulb / scorched socket the fixture's inputs, a leaking cell its device and cells, a runaway battery its charger
   chain, every power-loss consequence `cut_ids`, water ingress the wet thing and the liquid's source, a broken device
   itself, an automation the hub / app. Results: a lamp a resident switches off goes dark with `robot_attributable`
   False (was None, "link unknown", charged); a breaker tripped by a heater the robot switched on is True; a staged
   overload's trip is False.
4. **Mess, hazardous creations, zones**: electrical creates no entities and moves no mess, owns no agents: no mess kind,
   no hazard_created type, no zones (stated in section 16).
5. **Derived field** (8.2): `@derived_field("cord_hazard")` = `cords.live_hazards` (the cord's hazards as the room is
   now). **World fix**: the trip / fire-risk invariants judge a cord's lasting hazard live (`harms.family_label`), so a
   rug taken off the cord ends `cord_under_rug` (it latched for good). Latched fields table in docs/domains/electrical.md
   section 16.
6. **Harm labels an expectation can name** (WP8-fix's request): each harm family's invariant names its labels
   literally, so `inv:electrical.cord_trip_hazard` etc. pass L-EXPECT (were "no registered invariant or invariant
   label") and match `electrical.cord_trip_hazard:<id>`. Labels unchanged.
7. **Settings skills** (10.3, B14 changed / damaged): `plugged_in`, `strain_relief_damaged`, `plug_damage`, `kinked`,
   `damaged` (cables), `clock.format_24h`, via the new skill `electrical.set_field(target, field, value)`.
8. **Truth mappings** (`truth_electrical.json`): states paired, unpaired, dark, dimmed, charged; issues no_signal,
   no_internet, not_charging, swollen_battery, reversed_battery, wrong_time, cracked_screen, liquid_damage, overloaded,
   loose_plug, damaged_cable, dim_light, low_battery, dead_battery, exposed_wire.
9. **Hazard zones** (WP4's request to the domains): `@hook("hazard_zones")` lists live electrical hazards (bared cord
   or half-seated plug on live mains, scorched / burning / arcing cord or receptacle still live, live gear with water
   inside, a wet live receptacle, a swollen or burning battery), each only as the thing itself.
10. **Structured readings** (15.2): the battery tester's inspect line carries `"reading": {"subject", "quantity":
    "voltage", "value", "unit": "V"}`; WP7's index records it (`Index.readings`, value_si in volts).
11. **Identify first** (15.3; phase-1 look-alike / unfamiliar rows): `charge` and `connect_cable` refuse an unresolved
    look-alike member or unfamiliar thing with the kernel's generic refusal before any type-dependent check
    (`hardening.identify_first`; test-only groups -- USB-C data vs charge-only -- exempt: they connect and charge
    alike); core `set` refuses an unresolved member of an electrical group before the smart bulb's app guard; core
    `install` refuses an unfamiliar thing for a battery / bay / bulb slot. Reference skills `electrical.charge` /
    `electrical.connect` inspect (then read) first in realistic mode (oracle unchanged).
12. **Ingress gathering** (closeout_breakages row 7): `physical._gathers` reads `liquids.wetness_before`; a device that
    was dry at the arrival forgets what reached it before it dried, also when a system ordered before
    `electrical.wet_gear` dried it in the same substep.
13. Row 9 (optional): `percept.unfamiliar` calls the kernel's `Ctx.unfamiliar` (same answer; wording unchanged).

## Interfaces (signatures)

```python
# house_sim/systems/electrical/hardening.py
inputs(state, eid, w=None) -> [ids]                       # what a derived field of eid follows from
physical_cause(w, eid, cause=None, field=None) -> [ids] | None   # None unless a physical writer in a tracked World
uncharged_field(field) -> bool                            # process / bookkeeping in watch/electrical.json
test_only(ctx, eid) -> bool; identify_first(ctx, *eids); electrical_member(ctx, eid) -> bool
electrical_slots(rec) -> bool; @before("install", order=5) _identify_part(ctx)
live_hazard(state, eid, rec) -> kind | None; @hook("hazard_zones") hazard_zones(state) -> [{"id", "room": None, "subjects"}]
@derived_field("cord_hazard") derived_cord_hazard(state, eid)
free_receptacle(state, device) -> outlet id | None
@settings_skill plugged_in / strain_relief_damaged / plug_damage / kinked / damaged (types=("cable",)) / clock.format_24h
@skill("electrical.set_field") set_field(r, target, field, value)
# common.py
set_if(w, eid, key, value, cause=None); writer_cause(w, eid, cause=None, field=None); inputs_of(state, eid)
# cords.py
live_hazards(state, device) -> [kinds]; cord_hazards(state, device, cells, walk_memo=None)
# harms.py
family_label(state, baseline, family, labels); FAMILY_CHECKS; invariants shock, fire_risk, critical_power, data_loss,
disturbance, equipment_damage, trip_hazard (registered names unchanged)
# power.py
trip_breaker(w, bid, reason, cause=None); trip_gfci(w, oid, reason, cause=None)
# skills.py
_resolve(r, *eids)        # realistic: inspect, then read, an unresolved thing before charge / connect
```

## Deviations from the spec

* Labels are not re-prefixed with the family name (`electrical.trip_hazard:...`): that would change every legacy wrong
  plan's failure reasons (a gate verdict change per trial). The invariants name their labels literally instead, which
  L-EXPECT reads; `inv:<family>` still matches no label (request to WP8 for the classifier; WP1 / WP5 for the
  evaluator's record of which invariant produced a label).
* No electricity meter: climate's `climate.meter` books electrical devices by `power_w` / `standby_w`; it misses
  chargers' `_load_w` and fixtures without `power_w` (ceiling lights). A second meter would double-count (request).
* Settings skills for `alarms.*` and `clock.offset_s` are not registered: the probe's effect check needs the exact
  new value, which a set_time at run time does not give (offset to the second) or which is a dict.
* Identify-first is targeted (charge, connect_cable, set on electrical groups, install into electrical slots), not
  blanket: removal verbs (unplug, disconnect_cable) answer alike for every member (sweep), and the USB-C group resolves
  only by connecting.

## Legacy outcomes (P10)

(filled below from the A/B run)

## Phase-1 carry-over

* closeout_breakages row 6 (electrical gear water sensitivity): electrical's part done (the water_sensitive list makes
  `ledger.water_sensitive` the shared predicate; test `FieldClasses.test_electrical_gear_is_water_sensitive_unless_
  rated` fails on HEAD's files); the readers in cleaning and mechanisms are theirs (requests); plumbing_liquids already
  calls the predicate.
* closeout_breakages row 7 (wetness_before): fixed; `Gathering.test_a_device_dried_earlier_in_the_substep_forgets_
  what_gathered_before` fails on HEAD's physical.py (15 + 5 ml tripped the GFCI).
* closeout_breakages row 9 (optional unfamiliar-tool copies): `percept.unfamiliar` uses `Ctx.unfamiliar`.
* Look-alike / unfamiliar rows (REV findings 4-6 are other owners'; the instruction to check every electrical verb):
  scratch `sweep.py` -- 7 electrical look-alike groups x 17 verbs (10 electrical, 7 core verbs electrical hooks) x
  every entity parameter x 13 partners, on the table and held: before 10 differences (a19 bulbs `set`: the smart
  bulb's "does not respond"; power bricks `charge` destination / cable: connector fit), after only `inspect` of the
  `remote` group (its resolving action: by design). Unfamiliar twins (36 true kinds behind one look) x the same verbs:
  `install` into a NiMH bay took unfamiliar AA cells (fixed: `IdentifyFirst.test_no_unfamiliar_thing_goes_into_a_
  charger_bay_by_its_true_part_type`); held `inspect` gives heft by true mass (the sweep's look sets no mass: a
  physical difference, not a tell).
* No closeout_REV finding and no other INT3 request lies in electrical's files.

## Verification

(filled below)

## Known gaps

* A rug laid over a cord after it was routed raises no harm label (nothing sees the rug move); the derived
  `cord_hazard` shows it to schema-4 predicates.
* Core `install` of an unfamiliar thing into other domains' slots (filters, cartridges) still matches the true part
  type (kernel request).
* The electricity meter gap (above).

## Requests

1. **WP1 (kernel ledger data)**: `watch_fields.json` names for fields electrical writes onto other domains' entities:
   `burnt_out`, `water_ingress`, `water_damaged`, `data_lost`, `os_corrupted`, `recording_lost`, `job_interrupted`
   (condition, worse true); `dim_level`, `flicker`, `connector_locked`, `soc`, `charge_session`, `on_backup`,
   `backup_battery_level`, `power_source`, `operational`, `remote_status` (process); `connected_to` (setting);
   reclassify `cord_length_used_m` (placement -> process: a re-routed cord is no move), `cord_cells` (setting ->
   process), `captive_at` (bookkeeping -> setting: a captive charger plug pulled out of a laptop is a change),
   `display_source` / `audio_output` / `loudness_db` / `handle` (setting -> process: derived), `resets_after_trip`
   (condition -> bookkeeping). `ambient.json`: a smart bulb's app settings (`settings.power`, `settings.brightness`,
   `settings.cct_k`) under the lights allowance. `ledger.water_sensitive`: let `waterproof` / `weatherproof` / an IP
   rating with a second digit >= 4 on the record win over the type list (navigation_robot asked the same), so
   `wall_outlet` and `extension_cord` can be listed.
2. **WP1 / WP5**: record which registered invariant produced each label so `inv:<family>` (`inv:electrical.
   trip_hazard`) can match (WP8-fix's request; electrical's labels are now nameable literally).
3. **WP4 (kernel speech)**: truth.json `flickering` should also read `flicker`; `no_power` `energized` / `live` false;
   `frayed` `_cord_damage` in fray / exposed / chewed; `working` / `not_working` an alarm's `operational` and a remote's
   `functional` before `broken` (every record has `broken: false`, so a smoke alarm with a dead sounder is judged
   working); a `charge` truth act; the lexicon has no `pair` act.
4. **WP8**: `tools/protect_map.py` / `protect_rules.json` should draft `inv:electrical.<label>` names
   (`inv:electrical.cord_trip_hazard`), not the family names, which match no label; KNOWN_LIMITS 7a: electrical's
   labels are nameable now.
5. **WP-D cleaning** (closeout_breakages row 6): `engine` (wet wipe, liquid ingress), `surfaces` (rinse_item, wipe,
   spray), `dishes`, `systems`: ask `ledger.water_sensitive(rec)` instead of `electronic or water_safe is False`.
6. **WP-D mechanisms** (row 6): `dispense._water_sensitive` and `world.py`'s wet check: ask `ledger.water_sensitive`.
7. **WP-D climate / WP1** (resource ledger 6.8): `climate.meter` misses chargers' `_load_w` and fixtures without
   `power_w`; meter electrical draw by `power.draw_w` (or read an electrical meter) without double counting.
8. **Kernel (core install)**: refuse an unfamiliar part before matching a slot by its true part type for every
   domain's slots (electrical's hook covers battery / bay / bulb slots).
9. **WP6**: electrical's settings skills and `electrical.set_field` for B14's changed and damaged probes.
10. **Main session**: `setup.py` package_data for `data/verify/watch/*.json` and `data/speech/truth_*.json`.

## How to test

```
python -m unittest tests.test_electrical tests.test_electrical_hardening     # also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core,electrical
python tools/field_census.py --unclassified --domain electrical && python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python -m house_sim --check tasks/authored/electrical__examples.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/electrical__perception.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/electrical__examples.json --perception realistic --camera geometric --workers 3 --no-cache
python tools/py39_lint.py house_sim/systems/electrical/*.py tests/test_electrical*.py
# scratch p2_WP-D_electrical/: make_watch.py (regenerates the watch file), sweep.py lookalike|novel OUT.json --workers 3,
# make_trees.py + the two --check --json runs (A/B of legacy outcomes), writes_probe.py OUT.json tests|specs|waits
```
