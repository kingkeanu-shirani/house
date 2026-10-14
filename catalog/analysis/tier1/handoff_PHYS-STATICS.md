# Handoff PHYS-STATICS (stage A): support graph, statics, consequences and the statics laws

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1, 2.3, 3, 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_PHYSICS.md` (whole; package sections 5.1-5.5, 6.4, 7, 12). Handoff files read:
`handoff_TIER1-GATE.md` (at the start); `handoff_PHYS-DATA.md`, `handoff_PHYS-FLUIDS.md`, `handoff_BODY-MODEL.md`,
`handoff_CAM-SIGHT.md` (written while this package was in progress; read before hand-over and acted on: deviations 1,
7, 18, 21 and section 5). I also read the in-progress sibling code that calls this package (`phys/props.py`,
`lawbook.py`, `forces.py`, `liquids.py`, `processes.py`, `thermal.py`, `environment.py`, `kernel/body_faults.py`,
`kernel/sight.py`) and aligned every call they make with the signatures here.

## 1. Summary

* `kernel/phys/support.py`: the derived support graph (`support_of`, children map, slots, top support), masses (the
  kernel's only `subtree_mass`, bit-exact with core's sum order), loads, ratings, the episode-baseline allowance,
  composite centre of mass, resting size and the carried-load envelope `held_envelope` (1.6).
* `kernel/phys/statics.py`: pure thresholds: tip / slide / roll, overhang and edge margin, room-frame positions, stack
  verdicts, furniture tip margin, slot overload with sagging, carried handles, crush limits, padding, free-standing.
* `kernel/phys/impact.py`: every consequence (fall, impact, break, chip, bruise, crush, shelf and surface collapse,
  pull-out, tear, tip-over, knock, set-down, damage), harm attribution (6.4), the idempotency ledger and step
  consequence list, the nine settle laws (`LAWS`) and the two timed checks (`PROCESS_HANDLERS`). Import-inert.
* 73 tests (`tests/test_physics_statics.py`), green normal and strict, every function executed (statement coverage
  support 95 %, statics 96 %, impact 95 %); a probe of all nine laws over every entity of 20 generated houses raises
  no error and finds them statically stable (section 7).
* Fix pass after the stage-A review (gate A-FIX): fallen and torn-out things rest where they land, one ledger per
  settle phase, only crushable items crush, set-down at the effective speed, cycle guards, foot widths, damage
  events for callers with their own kind (deviations 22-28). Residual pass over the whole review (A-FIX re-recorded):
  soft landings catch light things and bottom out under heavy ones (deviation 29).

## 2. Files

Created: `house_sim/kernel/phys/support.py`, `house_sim/kernel/phys/statics.py`, `house_sim/kernel/phys/impact.py`,
`tests/test_physics_statics.py`, this handoff, the gate record `catalog/analysis/tier1/gate/PHYS-STATICS/`. No
existing file was edited. All on the PHYS-STATICS list of TIER1_INTEGRATION 2.3 (G10 PASS, section 6).

## 3. APIs

Signatures of TIER1_PHYSICS 7.1 are kept (a trailing `cmap=None` keyword passes settle's children map, kept current
by every move). "ADDITION" = not in 7.1; **DEVIATION** = differs from 7.1 or TIER1_INTEGRATION.

**`house_sim/kernel/phys/support.py`** (pure; never writes)

```
support_of(state, eid) -> (sid, slot) | None   slots ("shelf", k) ("in",) ("surface",) ("hung",) ("floor", (c, r) | None)
                                                ("held",); None for attached / part / under / behind / worn / mounted / doors
children_map(state) -> {location: [ids sorted]}
subtree_mass(state, eid, cmap=None) -> kg      liquid: record liquid_density, else liquids.json rho / 1000, else water
load_on(state, sid, slot, cmap=None) -> kg
rating(state, sid, slot) -> kg | None          props value (variation applied); None: floor, held, a portable support's
                                                surface or bottom at rest (a carried one fails by the handle law)
baseline_load(env, sid, slot) -> kg            from env._baseline, memoised on env (env._phys_baseline_memo)
com_height(state, eid, cmap=None) -> m         composite COM above eid's own base (a location cycle is cut where it
                                                closes, as in subtree_mass)
resting_size(state, eid) -> [w, d, h]          own frame, orientation applied, rot not (as CAM-SIGHT expects)
top_support(state, eid) -> eid
held_envelope(state, cmap=None) -> dict | None {"items", "two_handed", "bottom_m", "top_m", "front_m", "width_m",
                                                "depth_m", "height_m", "mass_kg", "com_m", "blocks_view"}; None: empty hands
ADDITIONS: phys(state, eid), liquid_rho(name), floor_at(state, rid, cell=None), contact_mu(state, eid)   (the only four
  calls into the data layer: props.props / liquid_props / floor_props, forces.friction)
  move_child(cmap, eid, old, new); slot_children(state, sid, slot, cmap=None); slots(state, sid, cmap=None) -> {slot: ids}
  descendants(state, eid, cmap=None) (World.descendants order); liquid_density(rec); own_mass(rec)
  load_above(state, eid, cmap=None) -> kg (items on eid recursively + phys.items_above hook ids)
  surface_height(rec, h=None); hang_height(rec, h=None); child_base_m(parent, parent_h, child, child_h)
  anchor_cells(state, eid, rid); num(v, default); finite(v); dims(rec); yaw_of(rec)
  CARRY (humanoid_v1 carry geometry, TIER1-STUB(BODY-ENGINE)), INNER_FLOOR_M, BASELINE_MARGIN_KG
```

**`house_sim/kernel/phys/statics.py`** (pure; never writes)

```
tip_accel_g(state, eid, axis=None, cmap=None) -> g     b / 2z; axis: push direction (room-frame degrees) or None (narrow side)
                                                       (fix pass) an upright item pivots on its foot, Phys.base_m
slide_accel_g(state, eid) -> g                         support.contact_mu
stability(state, eid, cmap=None) -> {"overhang", "tip_g", "slide_g", "rolls", "wobbly"}   (None for an unlimited g)
edge_margin_m(state, eid) -> m | None                  COM to the nearest edge of its surface; negative past the edge
stack_verdict(state, base_id, cmap=None) -> [(eid, "topple" | "wobbly")]
tip_margin_nm(state, furniture_id, pull_n=0.0, pull_h_m=0.0, extra=(), cmap=None) -> N m   (inf when anchored)
ADDITIONS: free_standing(state, eid); world_yaw; footprint_wd; support_tilt_deg; rolling_resistance (both used by
  processes' rolling); roll_accel_mps2 (delegates to processes.roll_accel: one rolling rule); slope_verdict -> "roll" |
  "tip" | "slide" | None; surface_frame; overhang_frac; overhang_dir_deg; room_point(state, eid) -> (x, y) m;
  stack_base; stack_chain; stack_height_m; clearance_above_m; tip_loads(state, fid) (phys.tip_loads hooks);
  overload(state, sid, slot, env=None, cmap=None) -> {"load", "rating", "limit", "sagging", "fails"} | None;
  creep_pending(state, sid); handle_status(state, eid, cmap=None) -> {"contents_kg", "limit", "ratio"} | None;
  padding_mm(rec); padding_factor(rec); crush_limit_kg(state, eid, cmap=None) -> kg | None (None unless
  Phys.crushable; x5 under a stack of its own kind); base_width_m(state, eid, b); LIKE_STACK_FACTOR
```

**`house_sim/kernel/phys/impact.py`** (consequences; every write journaled; never raises ActionError)

```
fall(w, eid, height_m=None, cause="fall", at=None, onto=None, by=None, direction=None, cmap=None) -> dict
     {"fell", "height_m", "h_eff_m", "room", "cell", "landed_on", "struck", "cause", "by", "outcome"}
     ADDITION direction (room-frame degrees to land toward); {"fell": False} when gone, already fallen from this spot
     this step, or resting on the floor without an explicit height_m (then it lands in place)
impact(w, eid, h_eff, cause, by=None, cmap=None) -> bool (broke)
break_entity(w, eid, cause, by=None, cmap=None) -> bool          idempotent; sets shattered for shattering classes
crush(w, eid, load_kg, by=None, cmap=None) -> [ids now bearing the load]   DEVIATION: returns the contents (7.1: none)
collapse(w, sid, slot, by=None, cmap=None) -> [ids moved]        DEVIATION: returns the moved ids
tear(w, eid, by=None, cmap=None) -> [ids fallen]                 DEVIATION: returns the fallen ids
tip_over(w, eid, cause, direction=None, by=None, cmap=None) -> {"tipped", "fell", "broke", "struck", "direction"}
knock(w, eid, accel_g, direction=None, cause="bump", by=None, cmap=None) -> {"tipped", "slid_m", "fell", "broke"}
damage(w, eid, kind, severity, detail="", by=None, cmap=None, event=None, note=None, ids=(), **data) -> bool
     severity is the new LEVEL (never lowers); ADDITION event: a phys.* kind emitted and recorded instead of
     "phys.damage" (forces.scratch: phys.scratch), with note, ids and the payload data
robot_caused(w, eid, cmap=None) -> bool
landing_cell(state, eid, rid, prefer=None) -> [c, r]             prefer: room-frame degrees
ADDITIONS: setdown(w, eid, speed=None, by="robot", cmap=None) -> {"h_eff_m", "outcome"} (the setdown law, core place;
  the drop follows effective_speed(state, speed));
  effective_speed(state, requested=None) -> mode (body.effective_speed, imported at call time); MOUNT_KEYS;
  once(w, eid, kind); consequences(w) -> step records {"kind" (phys.* event), "target", "ids", "by", "note", ...};
  emit_consequence(w, kind, target, **data) (TIER1-STUB(PHYS-INT)); damage_map(value); break_mode(p);
  floor_entity(state, rid); landing_hardness(state, rid, cell=None, onto=None); slide_distance_m(accel_g, mu);
  (residual pass) Landing(hardness, cushion_m, cushion_j); landing(state, rid, cell=None, onto=None) -> Landing;
  effective_drop_m(drop_m, land, mass_kg) -> the equivalent drop onto tile before padding (CUSHION_STOP_M 1 mm);
  laws fn(w, eid, env=None, cmap=None) -> [records]: orphan_law, overload_law, hang_law, crush_law, handle_law,
  edge_law, slope_law, stack_law, furniture_tip_law;
  LAWS = (("orphan", 10), ("hang", 30), ("overload", 30), ("crush", 40), ("handle", 45), ("edge", 50), ("slope", 50),
          ("stack", 55), ("furniture_tip", 60)) as (law_id, order, fn), orders equal to laws.json (tested);
  schedule_creep(w, sid, slot, due_s); creep_fire(w, eid, args); handle_creep_fire(w, eid, args);
  PROCESS_HANDLERS = {"creep": creep_fire, "handle_creep": handle_creep_fire}   (processes.run dispatches them)
```

Events emitted (event log, payloads carry oracle numbers): `phys.fall`, `phys.break`, `phys.chip`, `phys.crush`,
`phys.collapse`, `phys.pullout`, `phys.tear`, `phys.tip`, `phys.slide`, `phys.dent`. Hooks run (none registered
here): `phys.fell`, `phys.broke`, `phys.tipped`, `phys.collapsed`, `phys.items_above`, `phys.tip_loads`. Stats:
`phys_falls`, `phys_breaks`, `phys_tips`, `phys_collapses`, `phys_rolls` always; `breakages`, `damage` only when the
robot caused it. Fields written: `fell`, `broken`, `condition`, `shattered`, `hazards`, `handled_with`, `floor_hazard`,
`damage{kind}`, `orientation`, `tipped_over`, `collapsed_shelves`, `torn`, `sagging` + `_phys_sag`, `wobbly` +
`_phys_wobble`, positions (`location`, `relation`, `pos` / `spos` / `shelf`).

## 4. Deviations from the spec

Each was chosen as the more realistic and simpler option.

1. **Laws are plain functions** listed in `impact.LAWS`, registered by PHYS-INT (`lawbook.law(law_id, order)(fn)`), not
   decorated here (import-inert, 1.16). Their signature `fn(w, eid, env=None, cmap=None)` is the one PHYS-FLUIDS'
   `liquids.LAWS` and `thermal.LAWS` share; attribution comes from `robot_caused` (the action phase or `_robot_moved`).
2. **Consequence list and ledger.** Every consequence appends a record to the step's list (`impact.consequences(w)`,
   shared with PHYS-FLUIDS) with the event kind, target, affected ids, attribution and an id-only note; settle uses it
   for notes, the 64-consequence budget and the undo rule. The ledger keys a fall by the spot it falls from, so a
   cascade (shelf 4 onto 3, then 3 onto 2, then to the floor) is several falls while a duplicate call is skipped; an
   item resting on the floor does not fall again unless someone drops it from an explicit height (it lands in place).
3. **The orphan law** (6.1 order 10, active at landing) is implemented here: an item whose support was destroyed this
   step falls from the height it rested at to the floor of the room the support was in (read from the journal).
4. **Staged creep.** A staged support with a pending creep time (`phys_creep_s`, absolute simulated seconds) keeps the
   scene allowance until its creep check; otherwise the robot's first touch while unloading an "about to give way"
   shelf would collapse it at once. Past its creep time, or staged without creep, the limit is the true rating.
5. **The baseline allowance** (`limit = max(rating, baseline + 0.5 kg)`) applies only when the slot carried something
   at episode start; otherwise a 0.45 kg adhesive hook would hold 0.5 kg.
6. **Break modes** come from `Phys.break_mode` (PHYS-DATA's addition: shatter / crack / chip / bruise) rather than the
   spec's brittle column. Sharp pieces (hazard "sharp", shard gloves in `handled_with` as manipulation does, a sharp
   floor hazard) only for glass and fired ceramics: an egg or plaster breaks visibly (`shattered`) without cutting. An
   opaque full wrap or packaging hides the break (`shattered` not set, manipulation's `break_shows`). "crack"
   (electronics, plastics, wax) is broken but looks intact (1.14). Produce bruises instead of breaking (the food row
   "1.0 (bruise)"). Chip and bruise severity is `min(1, max(0.1, h_eff / tolerance - 1))`.
7. **Damage is a level** (`damage()` sets `max(current, severity)`), so repeated calls are idempotent; PHYS-DATA's
   scratch and PHYS-FLUIDS' heat and wet marks rely on it. The global `damage` flag counts each (entity, kind) the first
   time it appears when the robot caused it; a break counts `breakages` and `damage` (as manipulation's break_item).
   `damage()` emits no event: each caller emits its own kind.
8. **Landing spot.** With a direction (an overhang, a push), the first free floor cell past the support's footprint on
   the line from the item's own room position (`statics.room_point`, through nested surfaces); otherwise the ring
   search, with "in front of a wall-backed support" meaning beyond its front face (in a corner, the wall the footprint
   runs along for more cells) and ties broken nearest the support's middle before row and column. The overhang
   direction is used only when the item actually overhangs.
9. **Falling items**: an upright item taller than its narrow base lands on its side; an open container loses its liquid
   by `tip_spill_l` (lying on its side a narrow neck keeps some, otherwise everything) and the liquid runs down its
   support chain (`liquids.deposit_below`, conserved); a closed container's contents ride the impact (same h_eff, their
   own padding). Floor marks have severity `min(1, max(0.1, E / 100 J))`; tile cracks above 40 J.
10. **Wall damage.** Generated houses have no wall entities, so a fixture mounted in a room that tears out records
    `damage.pulled_out` on itself; a fixture attached to a wall entity leaves `damage.hole` 0.3 on that wall.
    Hangers: adhesive hooks come off with their load, drywall anchors pull out, other hangers bend (`collapsed`).
11. **Tear** also sets `torn: true` (the flag manipulation and navigation read) next to `damage.torn`.
12. **Tip-over** impact is the COM drop from z to half the lying thickness; furniture throws its open contents and top
    items to the floor in the tip direction (default away from the wall) and reports `struck` agents in the cells it
    sweeps (its height); closed doors keep contents in; a tipped item on a surface lies extended toward the push and
    falls when its COM passes the edge.
13. **Knock slide distance**: a bump pushes for 0.1 s (`(a - mu) g` during the push, then `mu g` braking).
14. **Stacks**: a stack is a chain of portable items with exactly one portable item on each; a secured member ends it
    (a pole in its base); a table or shelf with one item is a support, not a stack; a single tall item is never
    `wobbly`. Toppled members fall from the top down onto the stack's own support beside it, inside the same shelf or
    container, or next to it on the floor. Offsets come from `stack_offset_m`, else from `spos`.
15. **`free_standing`** (portable, wheeled or free-standing furniture, not secured / anchored / rooted / fixed in place)
    gates slopes, knocks and edges: on 20 generated houses fences, trees, gates and downspouts on a 6 degree yard
    tipped without it.
16. **Rolling** is decided by `processes.roll_accel` (one rule with the rolling process, wheeled carts included); the
    slope law counts `phys_rolls` when it starts a roll and the process emits `phys.roll` where it ends. Sliding on a
    sloped floor is not modelled; a slide off a tilted support goes toward its `tilt_dir_deg`.
17. **Furniture tip**: hook-reported loads (open drawers) are counted where they are, so their mass leaves the
    righting body; items on top count at half the depth. The law only runs when hooks report loads.
18. **`held_envelope`**: loads in the two hands sit side by side (widths add, as CAM-SIGHT's stand-in assumed); a tray
    (`carry_level`) is a hand carry; COM uses each record's `com_frac` (no data-layer call except liquid densities).
19. **Crush**: a brittle item breaks, anything else gets `damage.crushed` (plastics dent, boxes crumple); soaked
    materials use `Phys.wet_strength` (0.4 for corrugated board) instead of a literal, for crushing and handles.
20. **Test plan 12.3** "6 mm of padding at 0.3 m survives" is tested at 0.9 m (0.9 / 3 = 0.3 m): unpadded, 0.3 m
    survives anyway.
21. **`emit_consequence(w, kind, target, **data)`** has the signature of PHYS-FLUIDS' shim, so PHYS-INT replaces both
    with one `observe.emit_consequence`.

22. **One ledger per settle phase** (fix pass; 1.8 and 6.1 clear `phys_scratch` only at `begin()`): the ledger and the
    consequence list restart when the step number *or the simulated time* changes. The action phase runs at the step's
    start time and every 30 s substep advances `time_s` before its systems, events and settle, so each phase has its
    own ledger: within a week-long wait an item put back after a tip can tip again at the next `repeat_s` bump (the 7.4
    "perturb after completion" recipe), and the consequence list never spans more than one phase, so settle counts its
    64-consequence budget from the list it starts with. Nothing changes inside one phase.
23. **Consequence moves clear pinned heights and wall mountings** (fix pass): `_relocate` drops `z_m`, `mounted` and
    `mount_height_m` with the placement fields, and a fixture torn off the wall (pull-out, adhesive hook) also loses
    `anchored`. Before, a fallen item kept `z_m` (geometry.height_m reads it first) and a pulled-out wall shelf lay on
    the floor with `mounted` and `mount_height_m` 1.6 (support_of None, sight boxes at 1.6 m). The fall height is read
    before the move, so a shelf still falls from its mount height; lying on the floor it now blocks its cell like any
    floor-standing furniture.
24. **Only crushable items crush** (fix pass; TIER1_PHYSICS 5.3 keeps manipulation's max_load rule):
    `crush_limit_kg` is None unless `Phys.crushable` (PHYS-DATA deviation 21). A body under a stack of its own kind
    (same type, other than the placeholder generic_item, or same nest group, and no wider than it: plates on
    plates, bowls in bowls) bears 5x its point-load limit, the load spreading over its foot ring: 20 dinner plates
    (11.4 kg) no longer break the bottom plate, a 12 kg pot on one still does.
25. **Set-down at the effective speed** (fix pass; TIER1_INTEGRATION 1.7): `setdown` passes the requested mode (None:
    the robot's speed_mode) through `body.effective_speed`, so quiet mode sets a fast place down at normal speed and
    limp mode or weak legs at careful. The rule is idempotent, so a caller passing an already effective mode is safe.
26. **Cycle guards** (fix pass): `com_height`, `_load_top` and `_load_moment` cut a location cycle (a in b, b in a, only
    in malformed states) where it closes, as `subtree_mass`, `descendants` and `top_support` already did; they raised
    RecursionError.
27. **`damage(..., event=...)`** (fix pass): a caller with its own event kind (PHYS-DATA's scratch) gets one emitted
    event and one consequence record of that kind instead of a silent `phys.damage` record; without `event` nothing
    changes (PHYS-FLUIDS' heat and wet marks keep their own `processes.consequence`).
28. **Foot width** (fix pass): `tip_accel_g` uses `min(b, Phys.base_m)` for an upright item, so the shipped wine glass
    tips at 0.32 g on its 7 cm foot (the 9 cm bowl's box gave 0.375 g) and a table lamp at about 0.5 g.
29. **Soft landings have a cushion** (residual pass; 5.5 says h_eff = h x hardness / padding everywhere; review: from
    1.5 m onto carpet every mug and plate broke, eggs broke on grass and carpet from 0.3 m, a ceramic vase on carpet
    from 0.75 m): a hard landing keeps h x hardness; a soft one (floors.json `cushion_m` / `cushion_j`, and the
    soft-landing rows: beds, sofas, cushions) first stops the item over its cushion depth, as an equivalent drop of
    h x 1 mm / (1 mm + cushion_m) (the drop tolerances are drops onto tile, where an item stops within about 1 mm:
    peak decelerations of about a thousand g), until the cushion's energy is used up (min(h, cushion_j / (m g))); the
    rest of the drop lands on the compacted layer at the old hardness. Light things are caught, heavy ones bottom
    the cushion out (a 10 kg box on carpet lands as before). Never more than h x hardness, so every earlier anchor
    holds. `fall`, `_drop_beside`, shelf collapses, tip-overs, jolted contents and `setdown` all use
    `effective_drop_m` with the falling subtree's mass. Probe (40 varied instances, `impact.fall`): carpet at 1.5 m
    now breaks no mug, tumbler, wine glass or jar and 2 % of dinner plates (all broke before); eggs survive carpet
    from 0.3 m and break from 0.75 m, survive a lawn up to 0.9 m and 78 % from 1.0 m; a glass vase falling 1 m onto
    a bed survives (it broke at hardness 0.15).

## 5. Stubs and contracts

`grep -rn "TIER1-STUB" house_sim/kernel/phys/{support,statics,impact}.py`:

* `support.CARRY` (TIER1-STUB(BODY-ENGINE)): `body.profile(state)["carry"]` replaces the humanoid_v1 constants.
* `impact._scratch` fallback dict (TIER1-STUB(PHYS-INT)): the ledger and consequence list move into
  `World.phys_scratch` (cleared by begin() and rollback()); the code already uses it when the World has it (tested).
* `impact.emit_consequence` (TIER1-STUB(PHYS-INT)): becomes `phys/observe.emit_consequence`.

Contracts consumed (called lazily, so the modules import without them):

* PHYS-DATA (real in the tests; handed over first): `props.props` fields `cls mass_kg brittle break_mode drop_tol_m
  crush_kg hard wet_strength mu softness com_frac rating_kg shelf_rating_kg hang_kg handle_kg rollable anchored`,
  `liquid_props(name)["rho"]` (kg/m3), `floor_props(state, rid, cell)` (`impact_hardness`, `scratch`, `mu`, `c_rr`,
  `material`, `rug`), `forces.friction(rec_a, rec_b)`, `lawbook.LAWS` orders (tested).
* PHYS-FLUIDS (local fakes in the tests; hands over after this package): `liquids.container_closed`, `tip_spill_l`,
  `deposit_below(w, eid, liters, liquid, source=, by=)`; `processes.queue_add`, `queued`, `start(w, "rolling", eid)`,
  `roll_accel(state, eid)`.

Contracts provided and used by the siblings (checked against their code): PHYS-DATA `support.subtree_mass`,
`impact.damage`, `impact.damage_map`; PHYS-FLUIDS `support.support_of`, `children_map`, `subtree_mass`, `rating`,
`load_on`, `statics.edge_margin_m`, `support_tilt_deg`, `rolling_resistance`, `impact.consequences`, `robot_caused`,
`damage`, `break_entity`, `fall`, `tip_over`, `PROCESS_HANDLERS`; BODY-MODEL `support.children_map`, `subtree_mass`,
`impact.fall(..., at=(room, cell), by="robot")` and its `"cell"`; CAM-SIGHT `support.held_envelope` (its field list)
and `support.resting_size`.

## 6. Gate results

`TIER1-GATE PHYS-STATICS [PHYS-STATICS] vs A0: PASS (12 pass, 1 skip; this run: G7)`; record in
`catalog/analysis/tier1/gate/PHYS-STATICS/`, and `python tools/orchestration/tier1_gate.py compare A0 PHYS-STATICS
--package PHYS-STATICS` in the repo prints `compare result: PASS`.

```
G0   PASS  action fingerprint 3e6e94c37334 (423 actions, 107 systems); unchanged vs A0
G1   PASS  29 test files x normal+strict: 2813 tests (normal), 0 failing run(s)
G1b  PASS  isolated domains 10/16 ok (the 8 failures known from A0); HOUSE_SIM_DOMAINS=core 5/6 (test_perception, as A0)
G1c  PASS  8 module(s) imported, none registers anything
G2   PASS  own mode 242/242 specs (1210 trials); 0 verdict, 0 state-digest, 0 observation-digest changes vs A0
G3   PASS  forced realistic 226/242 (as A0); 0 verdict, 0 state-digest, 0 observation-digest changes vs A0
G4   PASS  perception audit room: 0 leaks, 0 crashes
G5   PASS  240 houses, 0 problems, counts equal to A0; generation-1 digests 340/340 equal
G6   PASS  fuzz room: 1800 commands, 0 defects, 0 nondeterministic
G7   PASS  4.54 ms/substep (A0 4.38, limit 4.599), 1 h wait 1.128 s (A0 1.102, limit 1.157)
G8   SKIP  tests/test_room_golden.py not present (PHYS-INT creates it in B1)
G9   PASS  physics extra: tools/physics_audit.py not present yet
G10  PASS  4 changed file(s); 0 outside the list of PHYS-STATICS
```

How it was recorded. A0's files were not committed when this package finished, so on the shared tree G10 would list
them (and every sibling's files) as outside this package. The gate therefore ran in an isolated copy (private scratch):
`git archive HEAD` plus A0's and PHYS-DATA's files committed as the base, this package's four files uncommitted, the
repo's empty directories (`tasks/templates`, `house_sim/data/actions`) recreated, and
`record --id PHYS-STATICS --package PHYS-STATICS --prev A0`; the record folder was then copied here and `compare` run in
the repo. G7 first measured 5.86 ms / 1.223 s while three sibling gates held the CPU at 100 %; the bench path does not
load `support`, `statics` or `impact` at all (checked: a 1 h wait on seed 0 imports only `house_sim.kernel.physics`,
the existing module), so `--only G7` was re-run once the machine was idle and passed (all other results kept).

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

* A tipped piece of furniture keeps its upright footprint (`pos`, `rot`): geometry has no lying footprint.
* No sliding on sloped floors; no bounces; a falling object never strikes things already on the floor (5.5 non-goal).
* Injuries are not decided here: struck people, pets and the robot are reported to `phys.fell` / `phys.tipped`.
* Wall holes go on the fixture when no wall entity exists (deviation 10).
* The 64-consequence budget is settle's: one law call can produce more (a 20-glass shelf cascade makes 104 records in
  2.5 ms), settle stops after the call that crosses it.
* Generated houses under the deferred laws (seeds 0-19, every law over every entity): no consequence except the ball on
  seed 13's 6 degree back lawn, which starts rolling (tan 6 = 0.105 > grass c_rr 0.10), and every generated patio
  umbrella standing in its base (seeds 1, 2, 4, 11, 12, 13, 15, 17), which shows as a wobbly two-member stack until
  outdoor_plants marks it secured.
* `held_envelope` puts a one-hand load's bottom at `hand_z - h/2` (1.6) while `geometry.height_m` places held items'
  base at 1.0 m; falls use geometry, like the rest of the kernel.
* `statics.roll_accel_mps2` is a one-line delegation to PHYS-FLUIDS' `processes.roll_accel`, faked in these tests and
  tested there.
* The soft-landing cushion is one energy budget per surface, whatever the item's footprint: a flat plate spreads its
  load over more pad than a mug's rim, so heavy flat things are treated a little harshly (P-approx; deviation 29).

## 8. How to test

```
python -m unittest tests.test_physics_statics                       # 73 tests, about 2 s
set HOUSE_SIM_STRICT=1 & python -m unittest tests.test_physics_statics
python -m unittest discover -s tests -p "test_physics_*.py"          # with the other physics packages
python tools/orchestration/tier1_gate.py compare A0 PHYS-STATICS --package PHYS-STATICS
```

What the tests pin (TIER1_PHYSICS 12.3 and more): a tumbler from 0.9 m breaks on tile and survives carpet, 0.3 m and
6 mm of padding; a 25 kg shelf holds 24 kg (sagging), collapses at 26 kg robot-added and cascades, a scene-overloaded
shelf holds its baseline and gives way 0.6 kg later; staged creep collapses on time and not when unloaded; a collapsing
top shelf drops its items onto the shelf below; three open drawers plus a 27 kg pull tip an unanchored dresser, not an
anchored one; a wine glass tips at 0.35 g and a mug does not; every stack rule; crush with padding, plates on edge and
wet cardboard; the same bump counts breakages only when the robot placed the glass; plus landing spots, robot drops,
soft landings, floor dents and tile cracks, spills and scatter, tears, pull-outs, tip-overs, knocks, set-down, the
ledger, id-only notes, the law table, cross-process determinism (two PYTHONHASHSEEDs) and the cost budgets. Every
scenario rolls its World back at the end and compares the state: a write that bypassed the journal fails.

Measured (best of N, this machine while the sibling gates ran): children map 0.10 ms on 929 entities (budget 0.15 ms);
the nine laws over a loaded bookcase's 8 nodes 0.23 ms (typical settle action 1 ms); one fall from a table with the
map given 0.17 ms; a 104-record shelf cascade 2.5 ms (worst-case settle 10 ms); `com_height` of the bookcase 0.055 ms;
`held_envelope` 0.10 ms without a map; all nine laws over all 929 entities 22 ms.

## 9. Follow-ups

**PHYS-INT (B1)**

* Fix pass: the ledger and consequence list are per settle phase (deviation 22) and settle needs no clearing of
  its own. In the action phase the list holds everything since the step began (a verb's own `ctx.fall` too), so
  the notes and the undo rule read the whole list; the 64-consequence budget counts from `len(impact.consequences(w))`
  at the settle call's start (in a substep, processes and events ran before it at the same time). The PHYS-INT
  handoff should record this choice. `core place` calls `impact.setdown(w, eid,
  ctx.args.get("speed"))`: the effective speed is applied inside (deviation 25).
* Register: `for law_id, order, fn in impact.LAWS: lawbook.law(law_id, order)(fn)`; settle calls each active law as
  `fn(w, eid, env=env, cmap=cmap)` with the phase's children map (moves keep it current), sets
  `w.phys_scratch["phase"] = "action"` during the action-phase settle (attribution) and reads `impact.consequences(w)`.
* Core `place`: `impact.setdown(w, eid, speed)` when `lawbook.active("setdown")`; refuse onto a shelf in
  `collapsed_shelves` ("That shelf is broken") and onto a broken or `damage.collapsed` support.
* Setup op `stage`: write `_phys_staged`, `phys_creep_s` as absolute simulated seconds, and `impact.schedule_creep`.
* `core.subtree_mass` delegates to `support.subtree_mass` (same float order for water: no hash change from mass).
* Replace `impact.emit_consequence` and the `_scratch` fallback (section 5).
* `core.json` field_tiers: glance `fell`, `tipped_over`, `collapsed_shelves`, `sagging`, `wobbly`, `torn`; inspect the
  damage kinds (`chipped`, `bruised`, `crushed`, `collapsed`, `pulled_out`, `hole`, `dent`, `cracked`, `torn`).
* Events `physics.bump` / `nudge` -> `impact.knock`; `physics.pull` -> `statics.tip_margin_nm(..., pull_n=, extra=)`
  then `impact.tip_over`; `physics.drop` -> `impact.fall(height_m=)`; `physics.load_test` -> the overload law.

**BODY-ENGINE**: `support.CARRY` -> `body.profile(state)["carry"]`; `body.stability_margin` can read `held_envelope`.

**CAM-RUNTIME**: delete `sight`'s stand-ins for `held_envelope` and `resting_size` (CAM-SIGHT section 5).

**Domains (adoption round)**: safety's `glass_system` skips items already `shattered`, which kernel breaks now set:
safety makes its shard field through `phys.broke` (its listed follow-up); manipulation: kernel falls clear its
`stack_member`, `nested`, `stack_offset_m`, `pack_*` and `xy_m` fields; outdoor_plants: mark an umbrella standing in its
base `secured`, and check the seed-13 ball on the sloped lawn; food: produce bruises by default (no hook needed).
