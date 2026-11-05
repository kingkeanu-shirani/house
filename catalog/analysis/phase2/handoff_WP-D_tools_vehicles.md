# Handoff WP-D tools_vehicles (VERIFY_HARDENING section 25 "WP-D tools_vehicles", 23.3; phase-1 carry-over; run A's requests)

Files (only these): `house_sim/systems/tools_vehicles/` (new `hardening.py`; changed `__init__`, `assembly`, `common`, `fasteners`,
`fittings`, `fixes`, `hanging`, `perception`, `scenarios`, `skills`, `surfaces`, `tools`, `vehicles`),
`house_sim/data/types/tools_vehicles.json` (one word in the domain-wide hidden tier), `house_sim/data/verify/watch/tools_vehicles.json`
(new), `house_sim/data/speech/truth_tools_vehicles.json` (new), `house_sim/data/vocab/tools_vehicles.json` (states),
`docs/domains/tools_vehicles.md` (section 14, with the Latched fields table), `tests/test_tools_vehicles.py` (the identify-first
steps, the renamed invariant labels), `tests/test_tools_vehicles_hardening.py` (new), this handoff, `WP-D_tools_vehicles.json` and
`WP-D_tools_vehicles_reviewed.json` (proposed golden rows). Nothing committed, staged, stashed or branched; nothing under
`%USERPROFILE%\.house_ai`; no network, no paid calls; every file LF. Scratch: `orchestration/p2_WP-D_tools_vehicles/` (probes named below).

## What landed

1. **Field classes** (6.3, L-WATCH): `watch/tools_vehicles.json` classifies every property name of every type of the domain and every
   field its verbs and systems write (a write probe over the domain's test file and its 15 specs' idle / reference / wrong plans:
   452 dotted fields). `field_census.py --unclassified --domain tools_vehicles`: 0 (was 139 names / 537 pairs); `--check`: 0. The kernel's
   name patterns misfit this domain's static props (`flow_lpm`, `cure_s`, `gauge`, `power_kw`, `stroke_l`, `product`): identity. Settings:
   assembly slots, mounts, inflation records, a car's `engine`, clamps. Process: what follows from a compared thing (`stack_top_m`,
   `latch_ok`, `drain_speed`, `running`, `mat_state.*`). Bookkeeping: `layers.*` (WP1-fix's container-derived request), the visible
   `fasteners` list (a close look extends it), a tool's `condition` words (a close look writes them). `types`: core things and
   generic_items that stand in for fixtures (`holes` a setting: the kernel's condition-up cannot compare a list; `dents`, `cracked_tile`,
   `gouged` conditions). `water_sensitive`: powered tools, chargers, paper.
2. **`fastener_delta`** (a hole found by reading the ledger, not on the spec's list): the truth of a fastener lives in the hidden
   `_fasteners`, which the ledger never reads, so a screw backed out of a stranger's cabinet or the leg bolts of a table passed as
   untouched. `fasteners.refresh` now writes the public `fastener_delta` (the ids whose status differs from the episode's baseline; gone
   when put back): `house.changed:<e>.fastener_delta`. Domain-wide hidden from perception.
3. **Causes** (6.2): all 15 systems tagged physical; every write of a compared field cites what it follows from. Probe
   (`causes_probe.py`, the 15 specs' idle, reference and wrong plans, seed 0): no watched field written without a cause. A tool the robot
   never touched that rains wet: `robot_attributable` False (was None, charged); one it carried out: True.
4. **Mess flows and origins** (6.5; no kind of its own: cleaning's and plumbing's kinds): dust from sanding and raking (grams on
   cleaning's scale), a snake's gunk `drain:<sink>` to `entity:<snake>` (origin `clog:<sink>`: `house.mess_in_transit:debris` when left on
   the tool), every spill this domain lets out by its liquid's class (`common.liquid_mess`) with physical ones citing their cause, a towel
   under a bleed valve, a pressure wash's run-off. World fix: a torn paper jam's scrap and sheet are refuse (`waste: true`). Shavings carry
   `mess_kind` and grams.
5. **Hazardous creation** (6.4): a dropped screw is tagged `choking_hazard` (`house.created_hazard`).
6. **Meters** (6.8): a running toilet's leak, a filter leak and flush, a pressure washer on `plumbing.meter.l`; an EV's and an e-bike's
   charger and a pressure washer's motor on `climate.energy.kwh` with `by_device`.
7. **Derived fields** (8.2, navigation_robot's request included): 27 registered recomputes over the bare state through the functions the
   verbs use (`common.StateView`, which has no `mut`), table in docs section 14.7. `_tilt_deg` and `_wobble_mm` are one registration that
   hands a record with `leveling` to `navigation_robot.measure.compute_level` when that domain is loaded. World fixes: a bumper that falls
   off clears its door's `stop_ok`; a located puncture moved out of the wheel record (a look at a tire changed the record the ledger
   compares).
8. **Settings skills and damaging verbs** (10.3, WP6's request): `fastener_delta`, `damage.stripped_hole`, `dents`, `nozzle_cut`, a car's
   `locked`, through the new skill `tools_vehicles.operate`.
9. **Truth mappings** (13): states `assembled`, `sorted`, `unsorted`, `tight` (new vocabulary); issues `dull`, `flat_tire`, `mushroomed`,
   `rounded`, `stripped`, `worn_weatherstrip`, `wobbly`, `loose`, `noisy`, `scratched`, `chipped`, `worn`, `water_damage`, `unsecured`.
10. **Structured readings** (15.2): `check_tire` and a gauged `inflate` in realistic perception answer `{"result", "reading": {subject,
    quantity pressure, value, unit bar}}`, `perceive.index(env).readings` picks them up (value_si in kPa); a held level gives `angle`
    readings, a stud finder `length` ones.
11. **Identify first** (15.3; the look-alike and unfamiliar rows; see below).
12. **Invariant labels** (P2-S2-merge request 7, WP8-fix): `tools_vehicles.mount_hazard:<e>:<why>`, `.chemical_within_reach:<e>`,
    `.tip_over_hazard:<e>`, `.child_left_in_car:<who>` (was `locked_in_car:<who>`), so `inv:tools_vehicles.<name>` matches (14.2).
13. **Hazard zones** (13.5, WP4's request): gas from a disturbed fitting, a car up on its jack, a rake left tines up.
14. **Seeded placement** (WP9's request): `toolbox`, `tool_inspection`, `decor`, `undersink_filter` place members in `ctx.placer.order`;
    `door_seals failing` takes `"random"` or a sample binding through `ctx.sample_roles`. Legacy builds unchanged (no draw).

## Interfaces (signatures)

```python
# house_sim/systems/tools_vehicles/hardening.py
identify_params(ctx); register_verbs(verbs); domain_slots(rec) -> bool; @before("install", order=6) _identify_part(ctx)
@derived_field: sorted_ok ran_ok on_outline outline_ok mat_state layers stack_top_m protrudes blocks_rear_view buried holstered
                connector_out drain_speed heat_output_frac latch_ok clear_view stop_ok assembly _tilt_deg _wobble_mm _rock_mm _rack_mm
                _short_leg _handle_play_mm _door_sag_mm _stowed _hazmat
@settings_skill: fastener_delta, damage.stripped_hole, dents, nozzle_cut (caulk_tube), locked (car, suv, electric_car)
@hook("hazard_zones") hazard_zones(state) -> [{"id", "room": None, "subjects"}]
# house_sim/systems/tools_vehicles/common.py
class StateView(state)                                # the World's read helpers over a bare state
held(ctx) -> [ids]; known_held(w, ctx=None) -> [ids]; must_identify(ctx, eid) -> bool; confusable(realism, state, eid) -> bool
identify_first(ctx, *eids); identify_fitted(ctx, tool)
mess_flow(w, kind, amount, src, dst, origin=None, cause=None); floor_key(w, room); liquid_mess(liquid) -> (kind, class)
log_spill(w, liquid, liters, source, spill_id=None, room=None, cause=None)
count_water(w, liters); book_energy(w, device, kw, seconds)
# house_sim/systems/tools_vehicles/fasteners.py
derived(w, eid) -> {derived hidden fields}; fastener_status(f) -> word; fastener_changes(rec, base) -> [ids]
# house_sim/systems/tools_vehicles/vehicles.py
trunk_view(w, trunk) -> {layers, stack_top_m, protrudes, blocks_rear_view}; footwell_state(w, fw) -> dict; pressure_message(text, eid, part, bar)
# house_sim/systems/tools_vehicles/tools.py, fittings.py, assembly.py
outline_at(rec, board) -> (hit, ok); latch_view(w, eid) -> (ok, issue, gap) | None; assembly_view(rec) -> (state, correct, flipped)
# house_sim/systems/tools_vehicles/skills.py
_resolve(r, *eids); @skill("tools_vehicles.operate") operate(r, target, verb, tool=None, **args)
```
