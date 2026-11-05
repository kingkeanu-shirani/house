# Handoff WP-D mechanisms (VERIFY_HARDENING section 25 "WP-D <domain>", 23.3; sections 6.2-6.5, 8.2, 10.3, 13.3, 15.2)

Files (only these): `house_sim/systems/mechanisms/` (new `hardening.py`; changed `__init__`, `closures`, `common`,
`controls`, `coverings`, `dispense`, `garage`, `handles`, `latches`, `lids`, `scenarios`, `skills`, `world`),
`house_sim/data/verify/watch/mechanisms.json` (new), `house_sim/data/speech/truth_mechanisms.json` (new),
`docs/domains/mechanisms.md` (new section 21 with the "Latched fields" table), `tests/test_mechanisms_hardening.py`
(new), `tests/test_mechanisms.py` (pins), and in `catalog/analysis/phase2/`: `WP-D_mechanisms.json`, this handoff,
`WP-D_mechanisms_reviewed.json` (the three proposed golden rows). The type data and the vocabulary are unchanged. Nothing
committed, staged, stashed or branched; nothing under `%USERPROFILE%\.house_ai`; no network, no paid calls; every file
LF. Scratch: `<home>/.claude/projects/C--Users-USER-OneDrive-Desktop-house-ai/orchestration/p2_WP-D_mechanisms/`
(`sweep.py` the twin sweep; `asif.py` the as-if schema-4 runs; `make_watch.py` the table behind the watch file;
`mk_after.sh`: `before_tree` = `git archive HEAD`, `after_tree` = that + exactly this package's files).

## What landed

1. **Field classes (6.3, L-WATCH).** `watch/mechanisms.json` classifies every property of the domain's type data
   (`python tools/field_census.py --unclassified --domain mechanisms`: 0, was 29 names / 64 pairs) and every field its
   verbs and systems write (300 names). The ledger applies a domain's `fields` only to its own types, and most closures
   this domain works are the kernel's (`door`, `gate`, `window`, `drawer`, ...), so `types` repeats the 54 `mech.*` fields
   that are not settings for the 17 kernel and other-domain types with a mechanism kind (`mech.kind` bookkeeping, as
   safety and climate asked; `mech.bolt_throw`, `mech.ingress_l`, `coverage`, `gap_cm`, `egress_ready` ... process: the
   setting itself carries the label). `water_sensitive`: this domain's 9 electronics. `writes`: 224 names (request to
   WP1).
2. **Writers and causes (6.2).** Both systems `writer="physical"` explicitly. `common.caused(w, *ids)` around every
   time-system write of a compared field: a closer, relock, wind, garage run, a burner's gas and a touchscreen's timeout
   cite their own thing; a draught the door and the open windows of its rooms; rain the window, also on the floor cell it
   wets (climate's request); fabric heat the fabric and the flames; the sun the window and its coverings, glare its
   windows. `common.volition(w, name)`: a pet's own bolt or slip (standalone; the pets domain owns those draws when
   loaded), an agent's run under a garage door. The `pet_escape` flag keeps its own attribution.
3. **Spills are real (6.5, a world fix, P10).** A lid that pops or breaks free, a door swinging into an open cup and a
   jerked drawer took the liquid out of the world (`liquid_l` down, a `spilled_l` counter up). `common.spill_out` lands it
   through the kernel's `phys.liquids.deposit_below`: a parcel plumbing's `liquid:spill` measures and a mop can take up,
   or a film on the table it stood on; `spills` counts once (the kernel's count, else this domain's for droplets too small
   to pool). `common.fall_item` logs the flow of a mess parcel a fall moves. This domain registers no mess kind: its
   liquids are plumbing's kinds, its soil and wetness the kernel's guards; a sprayed or pumped product is consumption where
   it is used (plumbing's rule). Rain ingress stays `house.wet_room` (cited, no parcel).
4. **Meters (6.8).** A hose nozzle's squeeze and a flush handle on the house water meter (`common.count_water`:
   plumbing's accounting, else the path resources.json names); unlit gas on the gas meter (`common.count_gas`, 2.5 kW or
   `gas.kw`).
5. **Structured reading (15.2).** `pump` on an inflatable returns `{"result": text, "reading": {"subject", "quantity":
   "pressure", "value", "unit": "kPa", "field": "inflatable.pressure_kpa"}}`, picked up by `perceive.index(env).readings`.
6. **Derived fields (8.2).** `@derived_field`: `mech` (recomputes `gap_cm`, `overlap_now_cm`, `coverage`, `exposed_frac`,
   `privacy_ok`, `view_in`, `clear_opening_m2`, `egress_ready`, `test_held`, `propped_by`, `clearance_m`,
   `hazards_in_reach` from the state), `covered` (window types), `props_open`, `propping`. "Latched fields" table in
   docs section 21.1 (L-LATCH reads it; every row registered). **World fixes:** latch part=guard refreshes `egress_ready`
   at once (a stale True on HEAD), and a wedge carried off by anything lets the door go (`common.prop_in_place`,
   `common.release_prop`; it stayed `propped` for good when the wedge left by any verb but `pick_up`).
7. **Settings skills and damaging verbs (10.3, B14).** `latched`, `child_lock`, `mech.panels`, `mech.lift_frac`,
   `mech.tilt_deg`, `mech.cord_cleated`, `mech.open_frac`, `mech.hasp`, `pump.locked`, `trigger.nozzle`, `mech.mode` (pet
   doors); damage: `mech.strain`, `mech.jammed` (curtains), `mech.skew_cm` (cord-lock blinds), `broken` (panes). Every step
   ran through the runner in a test.
8. **Truth mappings (13.3).** `truth_mechanisms.json`: states `raised`, `lowered`; issues `latch_misaligned`,
   `broken_latch`, `strike_misaligned`, `lock_jammed`, `sticking`, `off_track`, `rod_down`, `cross_threaded`,
   `sensor_misaligned`, `cord_hazard`, `gas_unlit`. No record -> unknown. No acts (they need lexicon verbs).
9. **Hazard zones (13.5).** Gas flowing unlit (the room), a garage door coming down (the door).
10. **Phase-1 carry-over.** See the table below.
11. **WP9's request.** `scenarios._spawn_items` places a helper's item list in `ctx.placer.order(...)` (seeded in schema 4,
    as given otherwise, so legacy builds are unchanged) and returns it in the order asked. No helper of this domain takes
    role lists.

Not applicable (with reasons, also in docs section 21): `hazard_created` types (no verb creates an entity; a fall's
shards are the kernel's), zones and passability (no agents of its own; pet doors in `ledger.passable` are a request to
WP1), a mess kind (item 3).

## Phase-1 rows

| Row | Fix | Regression test (fails on HEAD) |
|---|---|---|
| closeout_breakages row 5 (the test swing's direction) | `handles._test` obeys `_direction_ok`: a test against the stop moves nothing whatever the latch does, answers the same words, records and teaches nothing; `lock_verified`, `secure_gate`, `inspect_report` test the way the door swings (`skills._test_verb`) | `PhaseOneCarryOverTests.test_a_pull_test_of_a_door_that_opens_by_pushing_swings_nothing`, `test_a_push_test_of_a_door_that_opens_by_pulling_swings_nothing`, `test_the_wrong_way_test_tells_nothing_about_the_latch` |
| closeout_breakages row 6 (water sensitivity, the readers in this domain) | `dispense` and rain on a sill ask `common.water_sensitive` (`ledger.water_sensitive`) | `test_water_sensitivity_is_the_ledgers_one_predicate`, `test_rain_on_the_sill_takes_water_sensitive_gear` |
| closeout_REV 6, confirmed in VER2 (latch's 'Hold the padlock'; WP7's request) | `latches._padlock_on`: `ctx.refuse_unfamiliar(pad, host)` first | `test_an_unfamiliar_padlock_is_refused_like_any_unfamiliar_thing` (room and geometric, both slots) |
| Look-alike rule, every verb (the sweep found these) | `turn setting=` and `move_lever setting=` named a thing's controls before it was resolved: `ctx.refuse_unidentified` (and `set_control` resolves first); `pump destination=` told the absorbent member apart by "(it drips)": said only of an identified thing, the dose lands by what the thing is | `test_the_controls_of_an_unresolved_member_are_not_named`, `test_a_dose_onto_an_unresolved_bottle_runs_off_alike`, `test_the_set_control_skill_resolves_a_look_alike_first` |
| A crash the sweep found | `push_handle where=glass` raised `UnboundLocalError` for everything of glass (a local import made `add_soil` local to the verb) | `test_pushing_on_a_pane_answers` |

closeout_INT3's requests: its rows are the two above. closeout_REV's other findings and VER2's name no file of this
domain.

## Requests fulfilled

* WP-D safety (`gate_2.mech.kind`), WP-D climate (wet cell cites the window; `mech.bolt_throw`, `mech.ingress_l`,
  `mech.kind`), WP-D electrical (`dispense`, `world.py` ask `ledger.water_sensitive`), WP-D admin_inventory (a door's
  `shifted`), WP7 (latch's unfamiliar destination), WP9 (`ctx.placer.order`), WP1 / WP8 / P2-S2-merge (`@derived_field`,
  `@settings_skill`, watch file, truth file, Latched fields table, `hazard_zones`; no people or pets systems here).

## Interfaces (signatures)

```python
# house_sim/systems/mechanisms/common.py
caused(w, *ids)                    # context manager: a time system's writes cite ids; no-op in a robot action / untracked
volition(w, name="mechanisms.agents")   # context manager: a person's or pet's own move
water_sensitive(rec) -> bool       # ledger.water_sensitive plus electronics, tv, computer, screen, book, paper categories
spill_out(w, eid, lost, cause="spill", **data) -> litres   # liquid leaves eid in a robot action: a real spill, `spills` once
count_water(w, liters); count_gas(w, kwh)
prop_in_place(state, eid, rec=None) -> bool; release_prop(w, door, wedge)
# house_sim/systems/mechanisms/closures.py
secure_now(rec) -> bool            # a closed closure would hold a gentle test now
# house_sim/systems/mechanisms/hardening.py
# derived fields (plugins.DERIVED_FIELDS): mech covered props_open propping
# settings skills (plugins.settings_steps(state, eid, field)): module doc
# hook hazard_zones: mechanism_hazard_zones(state) -> [{"id", "room", "subjects"}]
@skill("mechanisms.operate") operate(r, target, verb, **args)
@skill("mechanisms.force_wrong_way") force_wrong_way(r, target)
# house_sim/systems/mechanisms/skills.py
_test_verb(r, target) -> "pull_handle" | "push_handle"
# pump's result on an inflatable: {"result": text, "reading": {...}}; entity record _pushed_aside (hidden) replaces a door push's shifted
```

## Deviations from the spec

* **No mess kind registered** (23.3 lists kinds "for what your domain creates or moves"): what it creates are spills of
  plumbing's kinds, and no flow into floors (plumbing's measured rule: a flow there would outlive a mop-up another domain
  logs no flow for).
* **Derived `mech` is the whole record** with its derived sub-keys recomputed: a derived field is a plain name, overlaid at
  the top level, and the goal fields live under `mech`.
* **`secured` is not derived**: `outdoor_plants` registers that name (one global registration per name); both of the verbs
  that change a pet door's panel or dial already keep it true.
* **A wrong-way test answers the same for a latched and an unlatched door** (the row asked only for no swing): "It holds"
  for a latched door would tell what the door cannot show.
* **A door's push writes `_pushed_aside`, not `shifted`** (a kernel condition field): `shifted` showed at a glance and
  fired `house.damaged` on references that only opened a door. Clamshell contents keep `shifted` (a real disturbance).
* **Per-type copies of `mech.*`** in the watch file until WP1 reads `writes`.

## Known gaps

* `latch_misaligned` reads the sag and the keeper offset one by one (6 mm each); the world adds them, so a 4 + 4 mm gate is
  misaligned yet the claim scores false. No scene of this domain mixes the two.
* `mech.*` of types outside the 17 listed fall to the kernel table or its `mech` default (`setting`): a false alarm at
  worst, never a hole.
* A mixer tap's running flow is metered only on plumbing's taps; a bare test device with `mech.lever` has no supply.
* A spilled mixture's `_mix` is not scaled with `liquid_l` (as this domain's dose code always was); the spill keeps the
  source's liquid name.
* References that leave a restore-class door open on their route (`house.left_open:sliding_door_1`,
  `exterior_door_1`) and specs' real task deltas are WP12's envelope work; as-if labels below.

## Requests

VERIFICATION_REQUESTS_PLACEHOLDER

## Legacy outcomes (P10)

LEGACY_PLACEHOLDER

## Verification

VERIFICATION_PLACEHOLDER

## How to test

```
python -m unittest tests.test_mechanisms_hardening tests.test_mechanisms             # also HOUSE_SIM_STRICT=1
HOUSE_SIM_DOMAINS=core,mechanisms python -m unittest tests.test_mechanisms_hardening tests.test_mechanisms
python tools/field_census.py --unclassified --domain mechanisms                      # 0 names
python tools/py39_lint.py house_sim/systems/mechanisms/*.py tests/test_mechanisms_hardening.py tests/test_mechanisms.py
```
