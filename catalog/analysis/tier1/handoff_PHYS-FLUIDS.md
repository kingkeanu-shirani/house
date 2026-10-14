# Handoff PHYS-FLUIDS (stage A): liquids, carry dynamics, heat, wind and rain, timed processes

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1, 2.3, 3, 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_PHYSICS.md` (whole; package sections 5.7-5.10, 6.2-6.3, 7, 12). Handoff files read:
`handoff_TIER1-GATE.md` (at the start), `handoff_PHYS-DATA.md`, `handoff_BODY-MODEL.md`, `handoff_CAM-SIGHT.md`,
`handoff_PHYS-STATICS.md` and `handoff_GEN.md` (written while this package was in progress; read before hand-over).
PHYS-DATA's are acted on (deviations 19 and 23), PHYS-STATICS's contracts match this package's (section 5), CAM-SIGHT's
isolated G10 record is the method of section 6, and GEN's weather process supplies the fields `environment` reads. I also read the in-progress files of
PHYS-STATICS in the shared tree (`kernel/phys/support.py`, `statics.py`, `impact.py`) and aligned with the contracts
they established with this package (law signature, consequence records, process timers, `deposit_below`).

## 1. Summary

* Four library modules under `house_sim/kernel/phys/`: `liquids.py` (5.7 quantities, tilt/tip/slosh spills, pour
  rate, transfer and overflow, the single `spill` entry point, absorb/wet, drips and leaks; 5.8 carry dynamics),
  `thermal.py` (5.9), `environment.py` (5.10, 6.3) and `processes.py` (6.2 plus the package's shared plumbing).
  Import-inert: nothing reaches the plugin tables; PHYS-INT registers the two systems and the law table.
* `thermal.thermal_step` is a drop-in body for the kernel thermal system with the active-set optimisation: its states
  are identical to `physics.thermal_system` on generated houses (tested) and it is about 40 % faster on a settled
  house, which resolves conflict 31 exactly (no R10 needed).
* Liquid is conserved at the 4-decimal storage precision through every path (pours, overflow, run-down chains,
  drips, leaks, absorbing); 98 tests (69 + 29) are green normal, strict, core-only and in one discover process.
* Fix pass after the stage-A review (gate A-FIX): hot pours couple as an upshock and wait for the thermal_shock
  law, the phase process keeps exact progress, wind moves only free-standing things, drag rows, carry drops from
  support.CARRY (deviations 25-29). Residual pass over the whole review (A-FIX re-recorded): a vessel touches its
  support through a cooler wall and rings rather than scorches wood, latent heat at the lumped conductance, wind at
  the item's height and shelter with gusts for lift and tip, one free-standing gate for the system (deviations 30-33).

## 2. Files

Created: `house_sim/kernel/phys/liquids.py`, `house_sim/kernel/phys/thermal.py`, `house_sim/kernel/phys/environment.py`,
`house_sim/kernel/phys/processes.py`, `tests/test_physics_fluids.py`, `tests/test_physics_thermal.py`, this handoff,
the gate record `catalog/analysis/tier1/gate/PHYS-FLUIDS/`. No existing file was edited. All on the PHYS-FLUIDS list
of TIER1_INTEGRATION 2.3.

## 3. APIs

Signatures of TIER1_PHYSICS 7.1 are kept; extra keyword arguments are marked. "ADDITION" = not in 7.1.

**Consequence records and laws (shared with impact.py).** Every consequence this package causes is a record
`{"kind": "phys.<event>", "target", "by": "robot" | "exogenous", "note", ...oracle data}` appended to the step's list
`impact.consequences(w)` (which settle reads for its notes, its 64-consequence budget and the undo rule) and emitted as
the event `kind` with the same payload (`processes.consequence`). Notes name ids only, never numbers. Laws have the
statics laws' signature `fn(w, eid, env=None, cmap=None) -> [records]` and are listed for PHYS-INT in
`liquids.LAWS` and `thermal.LAWS` as `(law_id, order, fn)`.

**`house_sim/kernel/phys/processes.py`**

```
run(w, dt, tick)                          body of system physics.processes (order 48, max_dt 30); O(1) without work
queue_add(w, due_s, kind, eid, **args) -> seq        ValueError for an unknown kind
start(w, kind, eid, **values) -> bool     DEVIATION: optional parameters; the kind's begin may decline (False)
stop(w, kind, eid) -> bool
queued(state, kind, eid); cancel(w, kind, eid) -> n; active(state, kind); is_active(state, kind, eid);
params(state, kind, eid); set_params(w, kind, eid, **values); section(state)                     ADDITIONS
roll_accel(state, eid); downhill_deg(state, eid); roll_begin / roll_tick / roll_end              ADDITIONS
consequence(w, kind, target, by, note, **data); emit_consequence(w, kind, target, **data)        ADDITIONS
robot_attributed(w, eid, by=None); blame(w, eid, by=None) -> "robot" | "exogenous"               ADDITIONS
sibling(name); num; finite; dims; top_area_m2; floor_cell(state, eid); now_s(state)              ADDITIONS
QUEUE_KINDS = ("wet_contact",); ACTIVE_KINDS = ("rolling", "dripping", "leaking", "contact", "phase", "melting")
```

State section `physics` (lazy): `{"seq", "queue": [[due_s, seq, kind, eid, args], ...], "active": {kind: [ids]}}`;
a process's parameters live in the entity's `_phys_<kind>` field. Queue kinds: `wet_contact` (here) plus the statics
timers of `impact.PROCESS_HANDLERS` (`creep`, `handle_creep`), fired as `fn(w, eid, args)`.

**`house_sim/kernel/phys/liquids.py`**

```
liquid_mass(rec); fill_frac(rec); headroom_m(rec); container_closed(rec)          (container_closed: manipulation's)
tilt_spill_l(rec, deg, mouth=None); tip_spill_l(rec, orientation, mouth=None)     mouth: resolved mouth_frac (opt.)
slosh_spill_l(rec, motion); pour_rate_lps(src, opening=None)                      opening: narrow | spout | wide
transfer(w, src, dst, liters, by=None) -> (delivered, overflow_l)
overflow(w, eid, by=None) -> liters
spill(w, liters, liquid="water", at=None, onto=None, source=None, by=None, cause="spill") -> floor spill id | None
absorb(w, absorber, source=None, liters=None) -> liters
wet(w, eid, liters, liquid, source=None) -> litres kept
drip_start(w, eid, l_per_h, until_l=None) -> bool; drip_stop(w, eid)
evaporation_mm_s(room_temp_c=21.0, humidity=0.45, airflow=False, outdoor=False, wind_mps=0.0, sun=False)
ADDITIONS: droplet_l(); liquid_density_kg_l(rec); free_liquid_l(rec); inner_height_m(rec); mouth_frac(rec, value=None);
  topple_threshold(rec); hold_capacity_l(state, eid, phys=None); holds_liquid(state, eid, phys=None);
  absorb_capacity_l(phys); tilt_of(state, eid);
  pour_in(w, dst, liters, liquid="water", temp_c=None, source=None, density=None, by=None) -> (delivered, spilled)
  deposit_below(w, eid, liters, liquid, source=None, by=None, cause="spill") -> floor spill id | None
  leak_start(w, eid, l_per_h=0.5) -> bool; leak_stop(w, eid); drip_/leak_ begin, tick, end (process callbacks)
  carry_motion(w, travel); riding_items(state, holder, cmap=None); carry(w, travel, by="robot", cmap=None) -> [records]
  carry_hand_z_m(state) (fix pass: support.CARRY["hand_z_m"]; the CARRY_HAND_Z_M constant is gone)
  law_overflow, law_open_spill; LAWS = (("overflow", 20, ...), ("open_spill", 70, ...))
```

`spill(cause=...)`: "overflow" logs `phys.overflow`, every other cause `phys.spill`; "drip" and "leak" portions count
only when they start a new puddle. Kernel puddles (no `phys.spill_sink` hook) are `generic_item` records with
`category: "spill"`, `_phys_spill: true`, ids `spill_N`, merged per (room, cell, liquid), cell added to `wet_cells`.

**`house_sim/kernel/phys/thermal.py`**

```
tau_s(rec, cp=None)              the thermal_tau law formula (always callable); explicit thermal_tau_s wins
heat_limit_c(state, sid) -> degC | None
contact_check(w, hot, support, by=None) -> "scorch" | "ring_stain" | "melted" | "shock" | None
dew_point_c(t, rh); sweats(state, eid)
shock_check(w, eid, other_temp_c, coupling, by=None) -> bool
phase_step(w, eid, dt) -> bool (keep running); freeze_burst_check(w, eid, by=None) -> "burst" | "bulged" | None
melt_check(w, eid, dt) -> severity; burn_seconds(t_c, child=False); heat_content_j(rec, t_ref)
ADDITIONS: thermal_step(w, dt, tick=None, mut=None)  kernel thermal system body (mut: PHYS-INT passes w.mut_quiet)
  kernel_tau_s(rec, lumped=None)   law switch: tau_s when thermal_tau is active, else physics.tau_s (legacy)
  owned(rec); domain_owned(rec); body_cp(rec); liquid_cp(liquid); heat_capacity_j_k(rec, cp=None); relax_c(...)
  mix_in(w, eid, liters, temp_c, liquid); damage_level(rec, kind); is_wet(state, eid); wet_contact_rule(cls)
  wet_contact_check(w, item, support_id=None, by=None) -> kind | None; wet_contact_due(w, item, args)
  shock_exposure(state, eid) -> (temperature, coupling) | None; freeze_point_c(rec); phase_needed(state, eid)
  contact_/phase_/melt_ begin, tick, end (process callbacks); melt_kind(cls)
  law_contact_heat, law_wet_contact, law_thermal_shock, law_phase, law_melt
  LAWS = contact_heat 80, wet_contact 85, thermal_shock 90, phase 95, melt 96
  COUPLING_IMMERSION 1.0, COUPLING_UPSHOCK 0.6, COUPLING_CONTACT 0.6, COUPLING_OVEN 0.5, COUPLING_FREEZER 0.3
  liquid_coupling(body_temp_c, liquid_temp_c) -> 1.0 | 0.6; pour_shock_check(w, vessel, liquid_temp_c, by=None)
  -> bool (law-gated); ICE_DIGITS (fix pass)
  (residual pass) contact_temp_c(state, hot); contact_mark(cls, contact_c); CONTACT_WALL_FACTOR 0.75 (_METAL 0.95,
  _INSULATED 0.2); RING_FINISHES, RING_MAX_C 150; MARK_EVENTS; phase_conductance_w_k(rec);
  phase_rate_per_s(rec, coupling, delta_k)
```

**`house_sim/kernel/phys/environment.py`**

```
run(w, dt, tick)                 body of system physics.environment (order 47, max_dt 30)
wind_force_n(rec, v); lift_speed_mps(rec); exposed(state, eid); rain_wet(w, eid, mm_h, dt)
wind_effect(w, eid, v, direction_deg, by=None, cmap=None, gust_mps=None) -> {"effect", "force_n", "lift_n",
  "resist_n", "moved_to"}   ADDITION gust_mps (residual pass): v is the sustained wind at the item, gust_mps its gust
ADDITIONS: wind_speed_mps(weather); wind_direction_deg(weather) (grid degrees blown toward); rain_mm_h(weather);
  frontal_area_m2(rec); is_sheet(rec); lift_coefficient(rec); lift_force_n(rec, v); secured_n(rec);
  soak_damage_kind(cls); outdoor_ids(w); drag_cd(rec) (fix pass: record, else the supports.json row, else 1.2)
  (residual pass) exposure(state, rid) -> (mean, gust) factors; object_wind_mps(state, eid, weather=None) ->
  (sustained, gust) at the item; GROUND_EXPOSURE (0.4, 0.6), ROOM_EXPOSURE {"balcony": (0.6, 0.75)},
  DEFAULT_GUST_FACTOR 1.5
```

## 4. Deviations from the spec

Each was chosen as the more realistic and simpler option.

1. **Law signature and records** follow impact.py (`fn(w, eid, env=None, cmap=None)`, records in the step's shared
   list): the spec left them open, and one convention keeps settle simple.
2. **Process kinds.** Active kinds `phase` and `melting` join the spec's four (freezing and melting take time). The
   statics timers keep the names PHYS-STATICS gave them (`creep`, `handle_creep`) and their fire functions stay in
   impact.py (`PROCESS_HANDLERS`), next to the laws that own the decision; this module schedules and dispatches.
3. **Phase trigger.** The kernel thermal step stops a container's liquid at its freezing point and starts the phase
   process, whose plateau then owns the temperature (`owned()`); the settle law only covers scenes staged at or below
   the point. Without it a bottle left outside on a frosty night (no touch at all) would never freeze. Law-gated.
4. **Brittle tops under hot items** (glass) are shock-checked with coupling 0.6 instead of getting a scorch mark;
   glass does not scorch.
5. **"Absorbent" means at least 0.25 L per kg.** Finished wood (0.05), MDF (0.15), cork, terracotta and lined
   paperboard take a 1.5 mm film and are damaged by wet contact; otherwise a 30 kg table would soak up 1.5 L of spill.
6. **Rimmed supports pool liquid as `liquid_l`** up to top area x rim_m, so the pool weighs, tilts out and pours like
   any other held liquid.
7. **One count per spill event**: a new puddle or a wetted entity counts once (`phys_spills`; `spills` when the robot
   caused it); liquid joining an existing kernel puddle grows it; drip and leak portions count only for a new puddle.
8. **Spills run down the support chain** (`spill(onto=...)` and `deposit_below`): a cup overflowing on a saucer on a
   table fills the saucer, films the table, and only the rest reaches the floor (the spec's single-level "runs off to
   the floor at the support's cell" would skip the table).
9. **Drips and leaks**: the water source is fixed at the start (own liquid, else an `until_l` budget, else the item's
   wetness above 0.1); water is released into a buffer and deposited in 5 ml portions rounded to 4 decimals with the
   remainder carried, so it is conserved exactly; dripping counts as wet, so the wet-contact timer starts with the
   drip; `leak_start`/`leak_stop` added (6.2 lists leaking without a starter); default leak 0.5 L/h.
10. **wetness vs moisture**: `wetness` is the surface (film fraction, or saturation for soaking bodies, as absorb's
    "held water = wetness x absorb_l"); `moisture` is the bulk water of soaking bodies (rain: surface to 1.0 in 120 s,
    bulk at the spec's rate). `phys.wetted` runs on every wetting by liquid, and for rain only on its two transitions
    (fully wet, soaked through) instead of every substep.
11. **Rain fills open holders** through their mouth (a bucket left out collects rain; the overflow law spills it).
12. **Wind lift** is L = m g (v / v_lift)^2, consistent with the spec's lift speed. A napkin lifts at 3.5 m/s as
    anchored, and skids off a table from about 3.0 m/s because the near-lift barely presses it down (manipulation's
    napkins also go at 3 m/s). Blown items keep moving while the wind lasts until they rest against the downwind edge,
    counted once per move (the domains' `blown_away` gate is never cleared, so an item put back could never blow again).
13. **The "3 kg chair slides at about 9 m/s" anchor** holds for an open-frame chair with drag coefficient 0.7 over its
    0.55 x 0.85 m outline; with the default bluff-body Cd 1.2 the same chair slides at 7 m/s. Fix pass: the
    supports.json `chair` and `rack` rows carry Cd 0.7 (`environment.drag_cd`), so the registry's 4 kg outdoor chair
    slides at about 9.1 m/s with no override (tests/test_physics_data.py DataAnchorTests).
14. **Outdoor dew point** uses the weather's humidity (generated outdoor rooms carry a nominal 0.45 of their own).
15. **`transfer` mixes the bulk temperature** by heat capacity (pouring boiling water into a mug makes it hot) after the
    thermal-shock check, and applies the kernel v2.9 `contents_from` contract itself (the kernel never imports
    systems.core); a frozen fraction (`ice_frac`) does not pour.
16. **Liquid specific heats** live in `thermal.LIQUID_CP` (cited handbook values; liquids.json has no c_p column).
17. **`thermal.tau_s` is the law formula**, always callable; `kernel_tau_s` holds the law switch so
    `physics.tau_s` can delegate to it.
18. **Melt** reaches full severity after 600 s at 10 K or more above `melt_c` (the spec says only "accruing over
    time"); wax, soap, film and foam plastic melt, other plastics and rubber deform. The melt law has no order in
    laws.json; it is listed at 96 (after phase), which `lawbook.law` accepts.
19. **Data from PHYS-DATA**: the droplet limit is `props.LIQUIDS["min_spill_l"]["value"]` (5 ml) and the puddle film
    on absorbent floors is the floors.json `film_mm` (3 mm), as PHYS-DATA's handoff asks; no local copies.
20. **Wet contact** deepens the mark while the item stays wet (0.3, then +0.2 per period); soil (granular) only
    gets damp; leather is ring-stained like wood.
21. **Contact heat** accrues from the first substep after placement; a hot pan lifted after a few seconds leaves a
    light mark (realistic) rather than none.
22. **Rolling** runs to `statics.edge_margin_m` (the nearest edge: a tilt has no stored direction) or to the room's
    downhill edge (`tilt_dir_deg`, else the `downhill` name, else south). `processes.roll_accel` (statics'
    `support_tilt_deg` and `rolling_resistance`; wheeled carts roll with k = 1) is the one rolling rule: the slope law
    starts a roll with it (PHYS-STATICS' `statics.roll_accel_mps2` delegates to it) and the process moves the item
    with it. The slope law counts `phys_rolls` at the start; the process emits the end (speed, runaway).
23. **Chipping by `Phys.break_mode == "chip"`** (PHYS-DATA's addition: enamel, stone) in `shock_check`.
24. **Environment candidates** are a pure function of the state (fingerprint: every entity id and location plus where
    the robot, people and pets are), cached per World, and a cheap bound skips items no wind could move before any
    property lookup.

25. **Hot liquid into a colder vessel is an upshock** (fix pass; 5.9 gives 1.0 to every pour): `liquid_coupling` is
    1.0 when the liquid is colder than the vessel (downshock: the hot surface is pulled into tension) and
    `COUPLING_UPSHOCK` 0.6 when it is hotter (the heated inner surface is in compression, which glass and glazes bear
    about 1.7x better; ASTM C149 tests the downshock as the severe case); `shock_exposure` uses the same rule for a body
    in a holder's liquid. The anchor still holds for every instance: boiling soup into a 4 C soda-lime jar is
    96 x 0.6 = 58 K > 45 x 1.15. At coupling 1.0, making tea cracked about half the mugs (25 % at 95 C, 50 % at 100 C)
    and most tumblers at 70 C. Measured with the law forced on, 200 instances each: boiling water into a 21 C mug or
    soup bowl 0 %, a cold rinse of a 90 C mug 0 % (ceramic 100 K, PHYS-DATA 27), hot coffee (85 C) into a 21 C
    drinking glass 1 %, boiling water into it 71 % (section 7), anything into tempered bakeware or a French press 0 %.
26. **The pour's shock check waits for its law** (fix pass): `liquids.pour_in` (and so `transfer`) calls
    `thermal.pour_shock_check`, which does nothing while `thermal_shock` is deferred (appliances_cooking and cleaning
    adopt it, TIER1_PHYSICS 10.2): it is the law's automatic application. `shock_check` stays callable at any time.
    Before, plumbing's adoption of `transfer` would have started cracking vessels ahead of the law's flip.
27. **Exact phase progress** (fix pass; 9 rounds stored floats to 4 decimals): near the freezing point a substep moves
    `ice_frac` by about 1e-4 or less, so rounding it every substep made frozen bottles thaw 10-15 % too slowly (or too
    fast) at 1 K. The phase process keeps the progress in its parameters (`_phys_phase.ice`, 9 decimals) and writes
    `ice_frac` rounded to 4; when anything else changes `ice_frac` (a domain, a setup), that value wins.
28. **Wind moves only free-standing things** (fix pass): `_free_to_move` follows `statics.free_standing`: portable, on
    wheels or casters, free-standing furniture, or a cover, and not fixed in place, mounted, anchored, rooted, a ground
    layer or a doorway. `wind_effect` on generated yards slid floor slabs and tipped fences at 1 m/s, lifted lawns,
    tipped doors, gates and mailboxes, and slid walkways, drains, hedges and shrubs (the system's own portable filter
    hid it, but `physics.wind_gust` and every other caller of `wind_effect` did not have one).
29. **Drag coefficient rows** (fix pass): `wind_force_n` uses `drag_cd(rec)`: the record's, else its supports.json row
    (props `Phys.drag_cd`), else 1.2. The cheap bound before property lookups uses max(1.2, the record's) because rows
    only lower Cd (a data test enforces it), so it still needs no lookup. Carry drops read `support.CARRY` (the
    TIER1-STUB(BODY-ENGINE) in liquids.py is gone: support.py, which BODY-ENGINE edits in B4, holds the one stand-in).

30. **Contact heat at the contact face, and heat rings** (residual pass; 5.9 uses the hot item's temperature and
    calls every wood mark a scorch; review: a mug of 75-85 C coffee scorched every wooden table and melted plastic
    trays): a vessel holding hot liquid touches its support through its wall and foot, at ambient + 0.75 of the
    excess (ceramic, glass, plastic: 1-1.5 W/m K over 4-8 mm plus the liquid's convective film; IR readings of a
    filled mug's outside run 60-70 C with 85-90 C coffee), 0.95 through metal, 0.2 when insulated; a solid hot
    body (pan, iron, dish from the oven) touches with its own temperature. Below 150 C a wood or veneer finish gets
    a white heat ring (`damage.ring_stain`, event `phys.ring`, note "got a heat mark": the same white ring trapped
    moisture leaves; both kinds and the event are already in 7.6 and 1.11); above it, and on fabric, paper, leather
    and laminate, a scorch; plastics a melt mark. With PHYS-DATA's 100 C plastics (its deviation 31): a mug of 85 C
    coffee leaves a light ring (severity about 0.05-0.12 after 5 min) on most wooden tops, none at 65-70 C; no mug
    marks a tray or a plastic table even with boiling water; a 200 C pan still scorches wood and melts plastic.
31. **Latent heat at the lumped conductance** (residual pass; review: with phase active and thermal_tau deferred the
    rate used the legacy kernel time constant, so ice thawed about 11x too fast, and even the lumped tau was 8-23 %
    fast because it includes the ice's and the container's heat capacity): `ice_frac` moves at coupling x hA x
    |T_f - T_amb| / (m_liquid x 334 kJ/kg), hA = heat capacity / tau_s of the record with its liquid all liquid (10
    W/m2K over the box area, tau_s's clamps and an explicit `thermal_tau_s` honoured), whatever the thermal_tau
    law's state. A frozen 0.5 L bottle thaws on a 21 C floor in about 3.7 h (tested equal in both law states).
32. **Wind at the item** (residual pass; review: items felt the 10 m airport gust, 1.5 x the mean, as a sustained
    force over their box, so a 0.6 kg watering can wandered on 2/3-4/5 of daytime hours): `object_wind_mps` scales
    the weather's 10 m open-terrain mean and gust by the room's exposure: ground-level yards, patios, decks,
    driveways 0.4 of the mean (ASCE 7 power law, exposure B against C: 0.45/0.65 x (1 m / 10 m)^(1/4) = 0.39) and
    0.6 of the gust (a near-ground peak factor of about 2.2 on that mean at turbulence intensity 0.4); balconies 0.6
    and 0.75; a room's `wind_exposure` overrides. The sustained wind slides things; a gust lifts sheets and tips
    tall things over (`wind_effect(..., gust_mps=)`; without it the single-speed rule of before). A gust's brief
    shove of something that would slide is not modelled (no trajectories). Over a year of the generator's weather
    (us, ca, uk; 09:00-18:00) the exposed free-standing things of generated houses: a beach ball moves on about 40 %
    of hours, a watering can on 0.1 % or less, everything else never; a napkin left out blows away on many windy
    hours (data test). The spec anchors stay on wind_effect's local speed (napkin 3.5 m/s, 3-4 kg chair about 9 m/s,
    i.e. a 10 m storm wind of about 23 m/s).
33. **One free-standing gate for the system** (residual pass; 6.3 lists "outdoor, non-fixed, portable items plus
    covers"; review: run() never moved an outdoor chair, a wheeled bin or a deck box at any speed while wind_effect
    moved fences): `run` uses `wind_effect`'s own `_free_to_move` (portable, wheels or casters, free-standing
    furniture, covers). Tested: a storm slides a 3 kg patio chair through the system, a breezy day does not, fences
    and lawns never move.

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim/kernel/phys/{liquids,thermal,environment,processes}.py`:

* `processes._Sibling` / `sibling(...)` (TIER1-STUB(PHYS-INT)): lazy proxies for props, lawbook, support, statics and
  impact; PHYS-INT replaces them with plain `from . import ...` (they let this package import and test without the
  other stage-A packages).
* `processes.emit_consequence` (TIER1-STUB(PHYS-INT)): becomes `phys/observe.emit_consequence`.
* (fix pass) `liquids.CARRY_HAND_Z_M` and its TIER1-STUB(BODY-ENGINE) are gone: `liquids.carry_hand_z_m` reads
  `support.CARRY["hand_z_m"]`, whose TIER1-STUB(BODY-ENGINE) in support.py (BODY-ENGINE's B4 file) covers both.

Contracts consumed (tests use fakes implementing them; a scratch smoke run against the real in-tree modules passed):

* props (PHYS-DATA): `props(state, eid)` fields `cls mass_kg shape brittle break_mode rigid mu waterproof absorb_l wsens
  c_p heat_limit_c melt_c shock_k rim_m mouth_frac rollable`; `liquid_props(name)` (`rho` kg/m3, `visc_cp`,
  `freeze_c`, `floor_hazard`, `film_mm`); `floor_props(state, rid, cell)` (`c_rr`, `absorbs`, `film_mm`);
  `material_class(None, rec)`; `MATERIALS["classes"][cls]["c_p"]`; `LIQUIDS["min_spill_l"]["value"]`.
* lawbook (PHYS-DATA): `active(law_id)` for thermal_tau, phase, melt, wind, rain.
* support (PHYS-STATICS): `support_of`, `children_map`, `subtree_mass(state, eid, cmap)`.
* statics (PHYS-STATICS): `edge_margin_m` (7.1), `support_tilt_deg`, `rolling_resistance` (not in 7.1; keep the names).
* impact (PHYS-STATICS): `consequences(w)`, `robot_caused(w, eid)`, `damage(w, eid, kind, severity, detail, by)`
  (takes the new level), `break_entity(w, eid, cause, by)`, `fall(w, eid, height_m, cause, at, by)`,
  `tip_over(w, eid, cause, direction, by)`, `PROCESS_HANDLERS` (not in 7.1; keep the name).

Contracts provided and used by PHYS-STATICS (its handoff lists the same): `liquids.container_closed`, `tip_spill_l`,
`deposit_below(w, eid, liters, liquid, source=, by=)`, `spill`; `processes.queue_add` (`creep`, `handle_creep`),
`queued`, `start(w, "rolling", eid)`, `roll_accel(state, eid)`.

## 6. Gate results

Gate id **PHYS-FLUIDS**, previous gate A0, record in `catalog/analysis/tier1/gate/PHYS-FLUIDS/`:
`TIER1-GATE PHYS-FLUIDS [PHYS-FLUIDS] vs A0: PASS (12 pass, 1 skip)`.

| Check | Status | Result |
|---|---|---|
| G0 | PASS | action fingerprint 3e6e94c37334 (423 actions, 107 systems), unchanged vs A0 |
| G1 | PASS | 35 test files x normal + strict, 3,110 tests (normal), 0 failing runs |
| G1b | PASS | isolated domains: the 8 failing tests in 6 files known from A0; core-only kernel files 9/10 (`test_perception.py`, known from A0; required from B1) |
| G1c | PASS | 12 `kernel/phys/*` modules imported with everything loaded, none registers anything |
| G2 | PASS | own mode 242/242 specs (1,210 trials); vs A0 0 verdict, 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (1,210 trials); vs A0 0 / 0 / 0 changes; 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit: 0 leaks, 0 crashes |
| G5 | PASS | layout 240 houses, 0 problems, counts equal; generation-1 digests 340/340 equal |
| G6 | PASS | fuzz 1,800 commands, 0 defects, 0 nondeterministic |
| G7 | PASS | 4.50 ms per substep (A0 4.38, +2.7 %, limit 4.60); 1 h wait 1.128 s (A0 1.102, +2.4 %, limit 1.157) |
| G8 | SKIP | `tests/test_room_golden.py` does not exist yet (PHYS-INT) |
| G9 | PASS | `tools/physics_audit.py` not present yet (PHYS-INT creates it; not required of this package) |
| G10 | PASS | 7 changed files, none outside the PHYS-FLUIDS list |

`compare A0 PHYS-FLUIDS --package PHYS-FLUIDS`: G0, G2, G3, G5, G7 PASS, G4 and G6 unchanged (INFO), result PASS. No
reviewed differences (stage A may change nothing, and nothing changed).

How the record was made (three runs, all merged into the one record by `--only`):

1. The full gate on the shared tree (`record --id PHYS-FLUIDS --package PHYS-FLUIDS --shared-tree`), with every
   concurrent package's current files present (the stronger test). It ran while two other packages' gates loaded all
   8 cores, which made two timing checks fail: `test_perception.py`'s `test_realistic_observe_overhead_is_small`
   (11.5 ms against its 10 ms bound) and G7 (5.12 ms per substep, every system about 10-25 % slower, run spread
   4.82-5.44 ms against A0's 4.38-4.42). Nothing of this package is imported by the engine or perception (checked:
   with every domain loaded and steps taken, no `kernel/phys/*` module is in `sys.modules`).
2. `--only G1,G7` once the machine had been below 25 % load for a minute: G1 green (0 failing runs); G7 still 4.82 ms
   because other gates restarted during it. Then `--only G7` alone on the quiet machine: 4.50 ms, within budget.
3. G10 from an isolated copy (`--root <scratch>/iso --gates-root <repo>/catalog/analysis/tier1/gate --only G10`): the
   shared tree's A0 work is not committed yet, so on the shared tree G10 lists A0's files as outside every stage-A
   package (as PHYS-DATA and CAM-SIGHT report). The copy is `git archive HEAD` plus A0's files committed as its base
   (a throwaway repository in my scratch folder; the project repository was not committed to), with this package's
   seven files on top: 7 changed files, 0 outside. In that copy, without any other stage-A package (no
   `phys/__init__.py`, no props, support or impact), the 95 tests also pass normal and strict.

Benchmarks of this package against TIER1_PHYSICS 11 are in section 8.

**Fix pass gate (A-FIX, 2026-10-14, re-recorded by the residual pass).** After the stage-A review the fixes of all
stage-A packages were gated together on the shared tree as `gate/A-FIX/`; the first fix pass saw only part of the
review list, so a residual pass went through all 26 findings and re-recorded the same gate on the final tree:
`tier1_gate.py record --id A-FIX --package A0+PHYS-DATA+PHYS-STATICS+PHYS-FLUIDS+CAM-SIGHT+BODY-MODEL+GEN --prev A0`
(the full gate, 25 min), then G10 again from an isolated copy (below). `compare A0 A-FIX --package <the same>`: PASS.

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  35 test files x normal+strict: 3147 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains: only the 8 failing tests known from A0; kernel files with HOUSE_SIM_DOMAINS=core 10/10
G1c  PASS  22 module(s) imported, none registers anything
G2   PASS  own mode 242/242 (1210 trials); vs A0: 0 verdict, 0 state-digest, 0 observation-digest changes
G3   PASS  forced realistic 226/242 (1210 trials); vs A0: 0 / 0 / 0 changes; 250 realistic trials identical in G2 and G3
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  240 houses, 0 problems, counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.44 ms/substep (A0 4.38, +1.4 %), 1 h wait 1.113 s (A0 1.102, +1.0 %)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  body_audit ok (record gate/A-FIX/body_audit.json); generator compat, gen-2 tests, gen-2 layout and timing
           (1.00) ok; physics_audit and the camera tool flags not present yet
G10  PASS  151 changed file(s); 0 outside the list of the seven packages (isolated copy)
```

G10 on the shared tree lists 3 files outside every stage-A list: `.gitignore`, `catalog/analysis/tier1/stageA_review.json`
and `tests/test_perception.py`, edited by the main session after the first A-FIX record (its review file, the jsonl
ignore rule and the core-only fix that makes the kernel files 10/10). G10 was therefore recorded from an isolated copy
(`git archive HEAD` committed as the base in a throwaway repository in scratch, plus every other changed or untracked
file of the shared tree; the project repository was not committed to), as the stage-A packages did for A0's
uncommitted work. No reviewed differences (none allowed in stage A, none needed).

## 7. Known gaps

* Kernel puddles never dry: cleaning owns drying (`evaporation_mm_s` is kernel-owned for it to re-export). With
  cleaning not loaded a kernel puddle stays until absorbed.
* A floor roll ends at the room's downhill edge cell; furniture in the way is not checked (5.4 has no path model).
* Rain on the robot itself is the body's (`body_faults.expose_water(w, "rain", seconds)`); `environment.run` wets
  entities only.
* Rain collected by very small mouths in drizzle rounds to 0.1 ml steps per substep (4-decimal storage).
* Film water on a large top is a 4-decimal wetness fraction (a 1.44 L film resolves about 0.14 ml).
* Boiling water (100 C) poured into a room-temperature soda-lime drinking glass or wine glass cracks about 70 %
  of them once `thermal_shock` flips (79 K x 0.6 = 47 K against 38-52 K); hot coffee or tea (85 C) about 1 %. A
  lower upshock coupling (0.5) would spare more tumblers but let about 28 % of fridge-cold jars survive boiling
  soup, breaking the spec's anchor for them; household advice is not to pour boiling water into ordinary glass.
* Cost: rain over 120 exposed items is about 0.54 ms per substep (no budget is set for rain; windy is 0.20 ms against
  0.3 ms). `thermal_step` right after reset, while every generated item still relaxes from 21 degC to its room, is
  1.1-1.5 ms per 120 s run (0.3-0.4 ms per substep amortized, budget 0.5); settled houses 0.18-0.29 ms per run.
* `physics.thermal_system` (today's) does not know `thermal_owner` or the phase plateau; they take effect once
  PHYS-INT switches the system to `thermal_step`.
* G9 `tools/physics_audit.py` does not exist yet (PHYS-INT creates it).

## 8. How to test

```
python -m unittest tests.test_physics_fluids tests.test_physics_thermal              # 104 tests, about 4 s
set HOUSE_SIM_STRICT=1 & python -m unittest tests.test_physics_fluids tests.test_physics_thermal
set HOUSE_SIM_DOMAINS=core & python -m unittest tests.test_physics_fluids tests.test_physics_thermal
python -m unittest discover -s tests -p "test_physics_*.py"                           # all physics packages together
python tools/orchestration/tier1_gate.py compare A0 PHYS-FLUIDS --package PHYS-FLUIDS
```

What the tests pin (TIER1_PHYSICS 12.4 and more): a full mug spills at fast carry speed and not careful; on-side spill
for wide and narrow mouths; honey and oil pour-rate ratios; transfer overflow becomes a spill down the support chain;
a towel absorbs at most 2.5 L/kg; a drip on a tray pools and overflows its rim, on a towel passes through after
saturation, on wood leaves a ring at 600 s; a 4 degC can rings unprotected wood and not through a coaster; a 200 degC
pan scorches wood and not a trivet; 96 K breaks soda-lime and not borosilicate; freeze burst at 0 % headspace and not
10 % (a narrow glass neck bursts at 10 %); wax melts at 50 degC; the burn table; the tau anchors; a napkin blows at
3.5 m/s, a weighted one does not, a 3 kg chair slides at about 9 m/s; rain soaks paper; plus journaling (every
function rolls back exactly), cross-process determinism under two PYTHONHASHSEEDs, `thermal_step` state identity on
three generated houses, and the micro-benchmarks (fail at 3x the budget).

Measured (this machine, fakes for the siblings): `physics.processes` idle 0.00006 ms (budget 0.02), 10 drips
0.028 ms (0.2); `physics.environment` windy, 820 entities with 120 outdoors, 0.20 ms (0.3), deferred or calm about 0;
`thermal_step` vs `physics.thermal_system` per 120 s run: settled seeds 0-3 0.18 / 0.26 / 0.29 / 0.22 ms vs
0.31 / 0.43 / 0.46 / 0.36 ms.

## 9. Follow-ups

**PHYS-INT (B1)**

* Register (kernel/physics.py): system `physics.processes` (order 48, max_dt 30) -> `processes.run`;
  `physics.environment` (47, 30) -> `environment.run`; `for law_id, order, fn in liquids.LAWS + thermal.LAWS:
  lawbook.law(law_id, order)(fn)`; in the action-phase settle, once: `liquids.carry(w, settle.travel_of(env),
  by="robot", cmap=cmap)`.
* Kernel thermal: make the `thermal` system's body `thermal.thermal_step(w, dt, tick, mut=w.mut_quiet)` (identical
  states, so no R10) and `physics.tau_s` delegate to `thermal.kernel_tau_s`.
* Replace the `sibling(...)` proxies with plain imports and `processes.emit_consequence` with observe's.
* Event effects: `physics.rain` -> `environment.rain_wet` on exposed items for its seconds; `physics.wind_gust` ->
  `environment.wind_effect` on exposed items (or a room) with its direction, passing speeds at the item
  (`object_wind_mps` with the event's speeds as the weather's 10 m values, or the event's own local speeds if the
  spec states them); `physics.tilt` sets `tilt_deg` and lets
  the open_spill and slope laws run.
* `core.mark_contents_from` and `liquids._mark_provenance` implement the same v2.9 contract; one can delegate.
* The environment's outdoor set needs no invalidation hook from settle: its cache validates itself against the state
  (the spec's "rebuilt when a settle moves something outdoors" is not needed).

**PHYS-DATA / data (via PHYS-INT, which owns the data files in B1)**

* A `c_p` column in liquids.json (move `thermal.LIQUID_CP`).
* Done in the fix pass: the `drag_cd` row column (chairs and racks 0.7), read through `Phys.drag_cd`.

**BODY-ENGINE**: switching `support.CARRY` to the body profile's `carry` also moves carry drops (liquids reads it).
Robot exposure to rain
(`body_faults.expose_water`) belongs with the body system, not `environment.run`.

**TIME-EVAL / outdoor_plants (weather)**: GEN's weather process gives `wind_mps`, `gust_mps`, `rain_mm_h`; with no
`wind_from_deg` the wind blows from the west (outdoor_plants' default) and with no weather `humidity` the outdoor dew
point uses the room's nominal humidity. Adding both to the weather state makes wind direction and outdoor
condensation follow the weather.

**TIME-EVAL**: exact idle probes: `physics.processes` is idle when the `physics` section is absent or has an empty
queue and no active ids; `physics.environment` is idle while wind and rain are deferred, or the wind is below
2.5 m/s and no rain falls.

**Domains (adoption round, TIER1_INTEGRATION 6)**: plumbing_liquids routes `do_pour` through `transfer` /
`pour_in` / `pour_rate_lps`, its slosh through `carry`, ice through the phase law (or declares `thermal_owner`), leaks
through `leak_start`; cleaning registers `phys.spill_sink` and re-exports `evaporation_mm_s`; food declares
`thermal_owner` and calls `freeze_burst_check`; outdoor_plants registers `phys.exposed` and feeds weather; electrical
registers `phys.wetted`; appliances_cooking and safety use `phys.contact_heat`, `shock_check`, `burn_seconds`,
`heat_content_j`.
