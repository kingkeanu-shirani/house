# Handoff WP-D electrical (VERIFY_HARDENING section 25 "WP-D electrical", 23.3; phase-1 carry-over; run A's requests)

Resumed from an interrupted agent (its partial work is commit 6a9e3e8); items 14 to 18 and the runs below are the resuming
agent's, and everything else was re-verified on the final code.

Files (only these): `house_sim/systems/electrical/` (new `hardening.py`; changed `__init__`, `batteries`, `cables`,
`charging`, `common`, `cords`, `detectors`, `harms`, `hub`, `lighting`, `percept`, `physical`, `power`, `scenarios`, `skills`),
`house_sim/data/verify/watch/electrical.json` (new), `house_sim/data/speech/truth_electrical.json` (new),
`docs/domains/electrical.md` (section 16, with the Latched fields table), `tests/test_electrical_hardening.py` (new),
this handoff and `WP-D_electrical.json`. Type data and vocab unchanged. Nothing committed, staged, stashed or branched;
nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch: `p2_WP-D_electrical/`
(probes named below).

## What landed

1. **Field classes** (6.3, L-WATCH): `watch/electrical.json` classifies every property name of every electrical type
   (234 names, 143 types) and every field electrical writes onto its own types (a write probe over the electrical
   tests, the 16 specs' idle / reference / wrong plans on seeds 0 and 1 and generated-house waits: 192 fields), by the
   domain itself: 349 field names. Ratings, kinds, wiring, printed text: identity. What the robot sets (on, plugged_in,
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
   `damaged` (cables), `clock.format_24h`, `alarms.alarm_1`, via the new skill `electrical.set_field(target, field, value)`.
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
14. **Setup randomisation** (WP9's request; found open on resuming): `electrical.devices`, `electrical.overload`,
    `electrical.crowded_strip` and `electrical.bulbs` place members in `ctx.placer.order`; `electrical.cells`
    `reversed_slots` and `electrical.media` `sources` take `"random"` / a sample binding through `ctx.sample_roles`
    (a legacy list stays as given, no draw). No helper skips work in an audit build (`electrical.settle` binds and
    places nothing, but the audits read its derived fields).
15. **Loads built on core types** (found by the as-if run on resuming): `types.generic_item` in the watch file: the
    cord's derived fields process, electrical's damage fields condition (`house.changed:<heater>.cord_cells` was charged
    on `T-702453ef553c00-01`'s reference). `fill_l` / `load_kg` are bookkeeping on electrical's two containers
    (WP1-fix's request: they follow the children).
16. **Content-gated warning** (13.5, 23.3; a world fix, found on resuming): `detectors.warns_of_alarm_test(text)` and
    `detectors.warned_people(state, since)` replace the substring check `detectors.warned` ("the latest news", "I will not
    test the alarm" counted as warnings, and speech in one room warned the house). Every awake person at home must have
    heard a message that warns.
17. **A phone's inspect shows its port** (15.2-15.3, found by re-reading the sweep): the group resolves by `inspect`, whose
    output told a USB-C phone from a Lightning one by nothing; a close look in realistic perception now gives `charge_port`.
18. **Removed on resuming**: `cords._LIVE_MEMO`, a process-wide walkway memo keyed by `id(state)`, steps and time: a later
    state with a reused id could read an earlier house's walkways (nondeterminism). `live_hazards` reads only its state.

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
@settings_skill plugged_in / strain_relief_damaged / plug_damage / kinked / damaged (types=("cable",)) / clock.format_24h /
                alarms.alarm_1
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
# detectors.py
warns_of_alarm_test(text) -> bool; warned_people(state, since) -> set of person ids
# skills.py
_resolve(r, *eids)        # realistic: inspect, then read, an unresolved thing before charge / connect
```

## Deviations from the spec

* Labels are not re-prefixed with the family name (`electrical.trip_hazard:...`): that would change every legacy wrong
  plan's failure reasons (a gate verdict change per trial). The invariants name their labels literally instead, which
  L-EXPECT reads; `inv:<family>` still matches no label (request to WP8 for the classifier; WP1 / WP5 for the
  evaluator's record of which invariant produced a label).
* No electricity meter of electrical's own: `resources.json` meters `climate.energy.kwh`, and WP-D climate's working tree
  now books chargers' `_load_w` and lights without a rating there (`climate/model.py`); a second meter would double-count.
  Check the two at the merge.
* No settings skill for `clock.offset_s`: the probe's effect check needs the exact new value, and the offset set from a
  wall time depends on the second the verb runs (request to WP6). `alarms.alarm_1` has one (added on resuming).
* Identify-first is targeted (charge, connect_cable, set on electrical groups, install into electrical slots), not
  blanket: removal verbs (unplug, disconnect_cable) answer alike for every member (sweep), and the USB-C group resolves
  only by connecting.

## Legacy outcomes (P10)

A/B on frozen trees: `before_tree` = HEAD with electrical's files as at bc1dc72, `after_tree` = HEAD with this package's
(scratch `make_trees.py`, `ab_run.py`, `ab_compare.py`; verdicts, wrong-plan reasons and labels, reference steps, time and
state digests compared). The 16 electrical specs on the five verification seeds in their own mode, forced realistic (room)
and forced realistic geometric; the other 227 authored specs on seed 0 in their own mode: 275 spec records, 467 trials.

* **Verdict changes: none.** 467 of 467 trials pass on both sides.
* **Own mode: identical**, state digests included (243 of 243 specs).
* **One spec differs, forced realistic only**: `T-dd0bf4a1707041-01` (put the phone on its pad), room and geometric, all
  five seeds: the reference skill `electrical.charge` now inspects the phone before charging it (identify first): one
  more step, 3 to 5 s more, another reference state digest, more attempted steps on one wrong plan; it passes as before.
* The world fixes change no legacy outcome: no legacy spec lays a rug over a routed cord and takes it off, re-wets a dried
  device in one substep, or warns of a detector test in words the new gate refuses (their announcements are the reference
  skill's sentence). The other 227 specs were run on one seed; the gate's five-seed run covers the rest.

## Phase-1 carry-over

Row numbers count from 0 as closeout_VER2 does (the handoffs' "row 7" for water sensitivity counts from 1).

* closeout_breakages row 6 (electrical gear water sensitivity): electrical's part done (the water_sensitive list makes
  `ledger.water_sensitive` the shared predicate; test `FieldClasses.test_electrical_gear_is_water_sensitive_unless_
  rated` fails on HEAD's files); the readers in cleaning and mechanisms are theirs (requests); plumbing_liquids already
  calls the predicate.
* closeout_breakages row 7 (wetness_before): fixed; `Gathering.test_a_device_dried_earlier_in_the_substep_forgets_
  what_gathered_before` fails on HEAD's physical.py (15 + 5 ml tripped the GFCI).
* closeout_breakages row 9 (optional unfamiliar-tool copies): `percept.unfamiliar` uses `Ctx.unfamiliar`.
* Look-alike / unfamiliar rows (REV findings 4-6 are other owners'; the instruction to check every electrical verb):
  the first sweep's findings (before: a19 bulbs `set`: the smart bulb's "does not respond"; power bricks `charge`
  destination / cable: connector fit; `install` into a NiMH bay took unfamiliar AA cells) are fixed with regression tests
  (`IdentifyFirst`). The first sweep's scripts were lost with the interrupted agent's scratch, so the sweep was written
  again and re-run on the final code (scratch `sweep.py`, `sweep_extra.py`, `show.py`), room camera, realistic:
  * 7 electrical look-alike groups with two or more members (22 members) x 10 electrical verbs and the core verbs electrical
    hooks (20 verb forms alone, 8 with a partner) x each entity parameter x 12 known partners x table and held: 316 cells
    per group. Differences: `read` of the `read`-resolved groups and `inspect` of the `remote` group (their resolving
    actions, by design), and held `inspect`'s `wetness`, the kernel's per-entity touch noise on dry things (0.00 to 0.06,
    drawn by entity id): no verb tells members apart.
  * 18 unfamiliar twins (reduced from 37 true kinds for time: every electrical category) behind one look x the same verbs
    x 6 partners: 178 cells. Differences: `read` of printed text (the kernel's NOVEL_PRINT rule) and the same `wetness`
    noise.
  * Third entity parameters and slots (`charge cable=`, `plug_in tie=`, `test_detector tool=`, `press tool=`, `install` into a
    remote, a lamp, a NiMH charger, a smoke alarm), all groups and the 37 unfamiliar kinds, table and held: 0 differences.
  * Geometric camera on the final code: the `smartphone` and `power_adapter` groups (the two the identify-first fix turned
    on): the same result as the room camera.
* No closeout_REV finding and no other INT3 request lies in electrical's files.

## Verification

Frozen copies (other packages were editing the live tree): HEAD with this package's files over it; machine shared with other
packages' full suites. The gate itself was not run.

| Check | Result |
|---|---|
| `tests.test_electrical` + `tests.test_electrical_hardening` (218 tests, 34 new) | pass: normal, `HOUSE_SIM_STRICT=1`, `HOUSE_SIM_DOMAINS=core,electrical`, and that strict |
| Regressions fail before | on HEAD with electrical's files at bc1dc72, 26 of the then 28 hardening tests failed or errored (the two left are guards: every system physical, USB-C cables alike) |
| Electrical specs, own mode, 5 seeds | `electrical__examples` 12/12, `electrical__perception` 4/4 |
| Electrical specs, forced realistic room and geometric, 5 seeds | 16/16 and 16/16 |
| All authored specs, own mode | 243/243 on seed 0 (the 227 non-electrical ones not run on seeds 1, 2, 41, 97 here) |
| Legacy A/B | above: no verdict change; one spec's forced-realistic reference one step longer |
| Room golden, `tools/golden.py dump` on both trees, then `diff` | 52 files compared, 0 changed |
| `tools/perception_audit.py 20 80`, room and geometric | 0 leaks, 0 crashes each |
| `tools/layout_audit.py 20 --domain electrical` | 120 houses, 0 problems |
| Twin sweeps | see Phase-1 carry-over: only resolving actions and the kernel's wetness noise differ |
| As-if schema 4 (`draft_hardening`, the 16 references, seeds 0 and 1) | no reference failure on either seed; found `cord_cells` on `generic_item` (fixed) |
| Physical-write cause probe (16 specs' idle, reference, wrong plans, seed 0) | no watched field written without a cause (`_co_dose`, `_heat` are unwatched) |
| `tools/field_census.py --unclassified --domain electrical`; `--check field_census.json` | 0; 0 |
| `tools/py39_lint.py` on the package's Python files | 0 violations |
| `tier1_gate.py scope --package P2-WPD:electrical --shared-tree` | attributes the other files to their concurrent packages |
| Cost | a 1-hour wait under cProfile: 7.05 s before, 7.19 s after, calls +0.06 %; wall-clock benches too noisy to read on the loaded machine; G7 not run |

Full suite (`green_tests.py 3`, 184 file runs, 5,843 tests per mode), run before the last change (a phone's `charge_port`, re-checked
by the package's tests in all four modes, the seven other test files that use phones or `charge`, both perception audits, the
golden diff and the electrical specs): 7 files fail in both modes, none from this package:

* `test_ledger` (8 errors, 1 failure), `test_lint` (1), `test_dialogue` (the docs tables, regenerated by the main session after the
  vocab files merge), `test_verify_hardening` (`test_private_heldout_seeds_stay_out_of_the_record`): the same failures with
  electrical's changes taken out (scratch `base_failing.log`, `base_failing2.log`).
* `test_room_golden`: digests of HEAD's other packages (20 specs, 102 script entries); electrical changes none (the diff above).
* `test_gate_tool` (1 test), `test_eval_tier1_gate`: the gate refuses a copy that is not a git work tree.

## Known gaps

* A rug laid over a cord after it was routed raises no harm label (nothing sees the rug move); the derived
  `cord_hazard` shows it to schema-4 predicates.
* Core `install` of an unfamiliar thing into other domains' slots (filters, cartridges) still matches the true part
  type (kernel request).
* `speech.parse` reads no `no_*` issue from natural text ("the TV has no signal", "the router has no internet", "the outlet
  has no power" parse to nothing), so those claims are not scored until the kernel fix (request 3).
* `clock.offset_s` has no B14 `changed` probe (deviation above; request 9).

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
3. **WP4 (kernel speech)**: `speech.py`'s parse loop skips any index whose word is in `_NEVER_VOCAB` before trying a phrase
   there, so every vocabulary phrase starting with "no" is unreachable: `no_power`, `no_signal`, `no_internet`,
   `no_hot_water`, `no_label`, `no_paper`, `no_ink`, `no_picture`, `no_wifi`, `no_connection`, `no_appetite`; the veto
   should apply to single-word hits only. Repro: `speech.parse("the tv has no signal")` is `[]`,
   `speech.parse("the tv has a black screen")` is the `no_signal` issue. Also truth.json `flickering` should also read `flicker`; `no_power` `energized` / `live` false;
   `frayed` `_cord_damage` in fray / exposed / chewed; `working` / `not_working` an alarm's `operational` and a remote's
   `functional` before `broken` (every record has `broken: false`, so a smoke alarm with a dead sounder is judged
   working); a `charge` truth act; the lexicon has no `pair` act.
4. **WP8**: `tools/protect_map.py` / `protect_rules.json` should draft `inv:electrical.<label>` names
   (`inv:electrical.cord_trip_hazard`), not the family names, which match no label; KNOWN_LIMITS 7a: electrical's
   labels are nameable now.
5. **WP-D cleaning** (closeout_breakages row 6): `engine` (wet wipe, liquid ingress), `surfaces` (rinse_item, wipe,
   spray), `dishes`, `systems`: ask `ledger.water_sensitive(rec)` instead of `electronic or water_safe is False`.
6. **WP-D mechanisms** (row 6): `dispense._water_sensitive` and `world.py`'s wet check: ask `ledger.water_sensitive`.
7. **WP-D climate** (resource ledger 6.8): keep booking chargers' `_load_w` and unrated lights (your working tree does);
   electrical adds no meter.
8. **Kernel (core install)**: refuse an unfamiliar part before matching a slot by its true part type for every
   domain's slots (electrical's hook covers battery / bay / bulb slots).
9. **WP6**: electrical's settings skills and `electrical.set_field` serve B14's changed and damaged probes. B14 `changed`
   checks `field_is` with an exact value; a settings skill should be able to ask for `field_changed` (effects.py has it),
   so `clock.offset_s`, whose new value depends on the second the verb runs, can have a probe.

## How to test

```
python -m unittest tests.test_electrical tests.test_electrical_hardening     # also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core,electrical
python tools/field_census.py --unclassified --domain electrical && python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python -m house_sim --check tasks/authored/electrical__examples.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/electrical__perception.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/electrical__examples.json --perception realistic --camera geometric --workers 3 --no-cache
python tools/py39_lint.py house_sim/systems/electrical/*.py tests/test_electrical*.py
# scratch p2_WP-D_electrical/: sweep.py lookalike|novel OUT.json --workers 3, sweep_extra.py OUT.json, show.py OUT.json,
# make_trees.py + ab_run.py TREE OUTDIR + ab_compare.py (A/B of legacy outcomes), asif.py OUT.json (the 16 references as
# schema 4), causes_probe.py OUT.json (physical writes without a cause)
```
