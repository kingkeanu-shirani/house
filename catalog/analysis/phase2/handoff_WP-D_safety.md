# Handoff WP-D safety (VERIFY_HARDENING section 25 "WP-D safety", 23.3; phase-1 carry-over; run A's requests)

Files (only these): `house_sim/systems/safety/` (new `hardening.py`; changed `__init__`, `air`, `bio`, `chemicals`,
`child`, `devices`, `disposal`, `evac`, `fire`, `glass`, `hazards`, `helpers`, `people_care`, `security`, `skills`,
`walkers`, `warn`, `water`), `house_sim/data/verify/watch/safety.json` (new), `house_sim/data/speech/truth_safety.json`
(new), `docs/domains/safety.md` (section 14, the Latched fields table), `tests/test_safety_hardening.py` (new),
this handoff and `WP-D_safety.json`. Nothing committed, staged, stashed or branched; nothing under
`%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch: `p2_WP-D_safety/` (probes below).

## What landed

1. **Field classes** (6.3, L-WATCH): `watch/safety.json` classifies every property name of every safety type by the
   domain itself (`fields`, 190 names), with per-type overrides where a name means something else (`types`): a fire's
   label / size / mass follow its flames; a puddle's and a spill's `liquid_l`, `depth_mm`, `area_m2` (and a puddle's
   `size_m`) are mess loads (`guard: mess`: a mop-up is the mess balance's, never `house.removed:<puddle>.liquid_l`);
   dried / cleaned labels are process; a puddle's `floor_hazard` mirrors `spotted` (bookkeeping); traps' `caught` /
   `catches` are their function; an extinguisher's `tamper_seal` is `worse: change` (**hole closed**: the kernel's
   `worse: false` never fired for "intact" -> "broken"); `generic_item.biohazard_waste_kg` (paper towels a wipe
   fills). `water_sensitive`: 13 safety types. `field_census.py --unclassified --domain safety`: 0;
   `--check`: 0 written fields without a class.
2. **Writer classes and causes** (6.2; WP1-fix / P2-S2-merge request): `safety.evac`, `safety.walkers`, `safety.child`,
   `safety.responders` are `writer="volition"`; `safety.security` writes visitors, swimmers and pets at open doors as
   volition (`w.writing`). Causes: `fire.causes_of(state, fid)` on every write a fire makes by itself (the fire for a
   robot fire, its fuel for a scene / household fire; flashover: the room's fires; oil autoignition and scorching: the
   pan), devices (the source and its support), chemical leaks (the container), puddle growth (its leak source), shard
   fields (the broken item), battery shorts (the cells, the metal, the holder). As-if schema-4 run of the 15 safety
   references (scratch `asif_safety.py`, seed 0): the `safety.evac` / `safety.fire` false alarms WP1-fix saw on
   `X-bf82170244be22-01` are gone (left: the evacuees' `house.agent_moved` -- the instruction's targets, free -- and
   the panel's `house.touched`, both the spec's envelope work); no label of any reference traces to a safety system's
   uncaused write.
3. **Mess** (6.5): `@mess_kind("biohazard", unit="g", sinks=["waste", "hazardous_waste"], eps=1.0, rho=0.0)`: unresolved
   spills at their place and carriers' `biohazard_waste_kg`; flows from `clean_biohazard` scoop / wipe / vacuum
   (`hardening.carry`). **World fix**: absorbing keeps the mass (`solids += liquid x fraction`, was 0.3 x). Chemical
   leaks log `liquid:chemical` flows (origin `chemical:<container>`; the kind's measure is its owner's). The gas a leak
   lets out is metered (`safety.meter.gas_m3` / `gas_kwh`, 10.55 kWh/m3).
4. **Derived field** (8.2): `@derived_field("resolved", types=<biohazard spills>, floor_puddle)` from the record's own
   amounts (the same rules that set it). "Latched fields" table in docs/domains/safety.md (L-LATCH reads it).
   **World fixes** (latches made state): a glass zone's `cleared` is judged every substep (pieces tipped back onto the
   floor reopen it); a pan's `covered_by` is cleared once its cover is gone (it stayed "covered" for good).
5. **Settings skills** (10.3): `pin_in` (extinguishers), `armed` (traps), `gas_valve_open` (meter, appliance valve),
   `locked` (chemical / flammables cabinets), `latched` (safety gates), `sealed` (sealable bag); the one-way ones
   through the new skill `safety.set_field(target, field, value)`.
6. **Truth mappings** (`truth_safety.json`): states sounding / silent / hushed; issues carbon_monoxide, biohazard,
   venomous, end_of_life, structural_damage, fallen, unresponsive, trapped, suspicious_person, electrical_burning.
7. **Hazard zones** (`@hook("hazard_zones")`, WP4's request): uncleared glass zones, fire rooms (subjects fires and
   fuels), dense smoke / CO / gas / toxic / flood rooms, live electrical markers, a threatening visitor's room; spills,
   puddles and other markers only as the thing itself. Burnt-toast haze is no zone.
8. **Invitations** (13.5 world behaviour; WP4-fix's any-certainty guard): warn.py reads hedged and question
   invitations ("maybe come and look", "can you come into the kitchen?", "why don't you come and see") as invitations
   that send the listener in; statements ("I'll come", "I think I'll go in") and negated ones still do not. Agreement
   with `speech.zones_invited` pinned on ten utterances.
9. **Identify-first on every safety verb** (closeout_REV finding 6): `hardening.identify_first` (order-1 before hook,
   installed by `__init__` after every module): `ctx.refuse_unfamiliar` on every entity parameter, and
   `ctx.refuse_unidentified` except `IDENTIFY_EXEMPT` (clean_biohazard's product / tool and smother's tool, which use an
   unread look-alike with its true physics and one message -- an unread flour jar still flares a grease fire, pinned by
   test_safety's `test_smothering_and_sweeping_report_only_what_is_seen`).
10. **Reference skills and helpers** (WP11's held-out requests): `_go_room` hop by hop with fresh door checks
    (`_open_way`); `evacuate_house` takes everyone in the room it is in in one trip (`_more_here`, same-room first);
    `clean_glass` fetches supplies from outside the glass room through a glass-free doorway and round the other rooms
    (`_fetch_round`, `_leave_glass_room`, gloves first when reachable glass-free) and sets gloves and dustpan down at
    the end; the supplies helper prefers a glass-free region whose doorway leads on (`helpers._onward_doors`);
    `clean_biohazard` wipes droppings into the bag (`step=wipe destination=<bag>`) and bags used disposable gloves.
    WP9: `placement_order` uses `ctx.placer.order` for schema 4; `safety.chemicals` takes `items: "random"` / a sample
    binding from `pool` (`ctx.sample_roles`, `k`).

## Interfaces (signatures)

```python
# house_sim/systems/safety/hardening.py
biohazard_parcels(state) -> [((cls, id), None, grams)]     # @mess_kind("biohazard", unit="g", sinks=[...], eps=1.0, rho=0.0)
place_key(state, rec) -> (cls, id) | None; bio_grams(rec) -> float
log_flow(w, spill, grams, dst_entity); carry(w, spill, carrier, kg)      # biohazard_waste_kg + world.mess_flow
derived_resolved(state, eid) -> bool | MISSING              # @derived_field("resolved", types=BIO_TYPES + PUDDLE_TYPES)
pin_steps / trap_steps / shutoff_steps / cabinet_lock_steps / gate_latch_steps / bag_seal_steps(state, eid)
    -> [(value, plan step)]                                  # @settings_skill pin_in / armed / gas_valve_open / locked / latched / sealed
@skill("safety.set_field") set_field(r, target, field, value)
identify_first(ctx); IDENTIFY_EXEMPT; install_identify_first()
# fire.py
causes_of(state, fid) -> [ids]
# hazards.py
@hook("hazard_zones") hazard_zones(state) -> [{"id", "room", "subjects"}]; ZONE_SMOKE_K, ZONE_CO_PPM, ZONE_GAS_PCT, ...
# air.py
meter_gas(w, m3); GAS_KWH_PER_M3          # state safety.meter {"gas_m3", "gas_kwh"}
# chemicals.py
LEAK_KIND = "liquid:chemical"; _log_leak(w, eid, holder, lost_ml)
# warn.py
INVITE_HEDGES; _invitation(seg, i, names); _why_not(seg, i)
# skills.py
_open_way(r, cid); _more_here(r, room, include_pets=True); _reach_glass_free(state, rid, item);
_leave_glass_room(r, rid) -> bool; _fetch_round(r, rid, item); _disposable(state, item); _bag_waste(r, bag, items)
# helpers.py
_onward_doors(state, rid) -> {cells}; chemicals_helper(..., pool=None, k=None); placement_order (schema 4: ctx.placer.order)
# bio.py: clean_biohazard step=wipe takes destination=<bag> (else a held bag, else the towels carry it)
# systems: SYSTEM_WRITERS["safety.evac" | "safety.walkers" | "safety.child" | "safety.responders"] == "volition"
```

## Deviations from the spec

* warn.py is **not** moved onto `speech.parse` (WP4's request): both implement 13.5's rules; warn.py's lexical
  reading is pinned by 17 tests and decides world behaviour for every legacy spoken warning, and `speech.parse` misses
  two invitation forms warn.py reads ("why don't you come and see", "you might want to come"). Agreement on the forms
  both read is pinned (`test_the_world_and_the_content_guard_agree_on_invitations`); the gap is a request to WP4.
* The biohazard kind reports no origins (one group per kind): a bag's load cannot carry its spills' ids. Relocation
  and arrival are still judged by location (floor, carrier, sink by container chain).
* The derived `resolved` and the kind are registered by name; a later domain registering the same name would raise at
  import (none does today).
* One per-type entry names a core type (`generic_item.biohazard_waste_kg`): the paper towels safety's helper makes.
* Structured readings: safety has no instrument verb (its readings are inspect fields and check_trap's grant, which
  WP7's index already reads): nothing added.

## Legacy outcomes (P10)

* No verdict change on the standard seeds (0, 1, 2, 41, 97): all 243 authored specs pass in their own mode on the live
  tree; safety 12 / 12 and 3 / 3; the examples forced realistic + geometric 12 / 12, the perception file geometric
  3 / 3.
* Held-out (3-8, 500-503): X-bc4e8d373e62c5-01 seeds 5, 6, 503, T-1970299fc6d849-01 seed 501 and
  A-d9e7a4d5d112d2-01 variant 2 seeds 3, 500 now pass their references; every safety spec passes all 15 seeds except
  A-d9e7a4d5d112d2-01 (spec debt, Known gaps).
* Changed reference traces, same verdicts: X-bf82170244be22-01 (room-golden digest: steps 18 -> 10, 126.2 -> 75.6 s
  on seed 0; A/B with safety's files at HEAD (scratch `golden_ab.py`) reproduces the golden digest exactly -- the
  main session re-records it), X-bc4e8d373e62c5-01, X-befab43585cde3-01. State digests also change where a chemical
  or gas leaks (`logs.mess_flows`, `safety.meter`). The other two golden diffs on the shared tree (T-089c40fb507289-01,
  T-48266979ae98d3-01) are identical with safety at HEAD: not safety's.

## Phase-1 carry-over

* closeout_REV finding 6 (safety's part: `discharge` told spray-bottle look-alikes apart): fixed (identify-first hook),
  `IdentifyFirst.test_discharge_does_not_tell_spray_bottle_look_alikes_apart` fails on HEAD's safety files.
* Every safety verb checked (not only the probe's path): twin sweep of every safety verb x every entity parameter x
  78 look-alike groups and 56 unfamiliar twins, on the table and held (before 1 / 0 differences, after 0 / 0), plus a
  fire scene with the held thing as the tool (it found an unfamiliar smother-tool tell, fixed:
  `test_an_unfamiliar_thing_held_at_a_fire_gets_one_refusal`). Left: pick_up's heft for look-alikes whose true masses
  differ (decor_stone / faux_stone: the registry owner's).
* closeout_breakages row 5 (pill-bottle pour; VER2: fixed): verified on the live tree, pinned for expired_medication
  (`test_pill_bottle_look_alikes_answer_a_pour_alike`). No other row, REV finding or INT3 request lies in safety's
  files (INT3's request to safety is that row).

## Verification

* `tests/test_safety.py` (205) + `tests/test_safety_hardening.py` (37): OK normal, `HOUSE_SIM_STRICT=1` and
  `HOUSE_SIM_DOMAINS=core,safety`. On a copy of the tree with safety's files at HEAD (scratch `ab_tree`) the new tests
  fail -- 28 failures and 13 errors over their subtests; only the oracle-mode guard and the row-5 pin pass there, by
  design.
* `green_tests.py 3` (5,652 tests per mode): safety's files ok in both modes; the 10 failing runs are other packages'
  in-progress work except the X-bf82170244be22-01 golden entry (above): test_ledger (cleaning's `debris` /
  `waste_item` registrations vs the test's own), test_lint (cleaning's derived `in_stream` retires an L-LATCH pin),
  test_dialogue (other domains' vocab tables), test_verify_hardening (WP6's private families now run).
* All 243 specs own mode: 243 passed. Perception audit 10 x 60, room and geometric: 0 leaks, 0 crashes. Layout audit 20
  `--domain safety`, gen 1 and gen 2: 0 problems, 0 domain follow-ups. py39_lint: 0 violations. field_census `--check`:
  0. Every file LF.

## Known gaps

* `liquid:spill` (safety's `floor_puddle`) and `liquid:chemical` are other owners' kinds (requests); until registered,
  puddles and leaked chemicals are not in the mess balance (the flows are logged).
* A-d9e7a4d5d112d2-01 still fails on held-out seeds 3 (variant 2's wrong plan "hushes the alarm without checking the
  other rooms" never acts: that alarm is quiet) and 4 / 503 (variant 0's idle passes: the burnt-toast smoke clears by
  itself within the response window) -- spec debt (WP12).
* References in the as-if run still leave exterior doors open (T-301e5aee8a71a0-01, T-89530941a65283-01) and need
  envelopes for their task deltas -- WP12's migration.

## Requests

1. **WP1 / main session (kernel ledger data)**: `location_classes.json` `hazard_created.shards`: add `sharps_bundle`
   (the wrapped glass must reach a waste container); `agents.json` / `ledger.passable`: a closed safety gate
   (category `safety_gate`, device_kind `stair_gate`) is impassable to a child under 3 and to pets; `resources.json`
   gas: add `safety.meter.gas_kwh` (leaked gas); `watch_fields.json` names for fields safety writes onto other domains'
   entities: `window_guard`, `knob_covers`, `corner_guards`, `child_latch`, `bulky_pickup_tag`, `bait`, `open_fraction`,
   `gas_valve_open` (setting); `bleached_surface`, `set_stain`, `remark_risk`, `collapsed` (condition, worse true);
   `haz_stream`, `mount_height_m` (identity); `disposed`, `captured`, `released_outdoors`, `agitated`, `odor_marker`,
   `pheromone`, `catches`, `enzyme_effective` (process); `escaped_handling`, `size_mm_estimate`, `severity_hint`,
   `edge_offset_mm` (bookkeeping); `biohazard_waste_kg` (content, guard mess).
2. **WP4 (kernel speech)**: truth.json `gas_smell` reads `gas_valve_open` (true at every normal meter: "no gas smell"
   is judged false there) -- read an active leak (`_gas_leak.m3h` > 0); `fire` reads `on_fire` and `smoke` reads
   `smoking`, which safety never writes (its fires are `category: fire` records with a `stage`; fuels carry `burning`);
   the lexicon misses "why don't you come and see" / "you might want to come" as approach directives; no acts for
   evacuate / put out (a fire `out`) / shut off the gas (`off` for a valve reads `gas_valve_open`).
3. **plumbing_liquids (WP-D)**: count safety's `floor_puddle` (category puddle; `liquid_l` by its `liquid`: water,
   coffee, juice -> liquid:spill, oil -> liquid:grease) in the `liquid:spill` measure.
4. **cleaning or plumbing_liquids (whoever registers liquid:chemical)**: count chemicals spilled from safety's
   containers (`_chem_spill` on holders) and use the leak flows (origin `chemical:<container>`).
5. **pets (WP-D)**: the `pet_waste` measure must not also count safety's biohazard spill types (`category: biohazard`;
   a pet accident made by safety's helper is `biohazard`), or both kinds see one parcel.
6. **mechanisms (WP-D)**: T-301e5aee8a71a0-01 as-if: `house.changed:gate_2.mech.kind` written by `close` in the robot's
   window -- classify `mech.kind` (identity) or do not rewrite it.
7. **Owner of decor_stone / faux_stone** (registry look-alike group smooth_stone): the members' true masses differ, so
   a pinch-grip pick_up drops one and not the other (fire-scene twin probe, `fire_tools.py`).
8. **WP6 (battery)**: safety's settings skills and `safety.set_field`; B14 damage probes for safety (discharge an
   extinguisher, pull its pin) once the probe interface exists.
9. **WP12 safety (migration)**: A-d9e7a4d5d112d2-01 (above); envelopes for the as-if deltas (scratch
   `asif_after.json`); close exterior doors the references open.
10. **Main session**: re-record the room-golden entry `X-bf82170244be22-01` (state, steps, time; class: safety
    evacuate_house's one-trip evacuation) with its signed row; `setup.py` package_data for `data/verify/watch/*.json` and
    `data/speech/truth_*.json`.

## How to test

```
python -m unittest tests.test_safety tests.test_safety_hardening      # also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core,safety
python tools/field_census.py --unclassified --domain safety && python tools/field_census.py --check catalog/analysis/phase2/field_census.json
python -m house_sim --check tasks/authored/safety__examples.json --workers 3 --no-cache
python -m house_sim --check tasks/authored/safety__examples.json --seeds 3 4 5 6 7 8 500 501 502 503 --workers 3 --no-cache
python -m house_sim --check tasks/authored/safety__examples.json --perception realistic --camera geometric --workers 3 --no-cache
python tools/py39_lint.py house_sim/systems/safety/*.py tests/test_safety*.py
# scratch probes (p2_WP-D_safety/): sweep.py lookalike|novel OUT.json --workers 3; fire_tools.py OUT.json;
# asif_safety.py OUT.json 0 2 (as-if schema 4 references)
```
