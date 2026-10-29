# Physics (kernel v3.0)

The kernel's physics layer gives every object physical properties and applies common physical laws the same way
whichever verb, system or event caused a change. It is a **lumped, rule-based** model (fidelity P-approx): rated
loads, drop tolerances, friction thresholds, Newton cooling, scalar liquid volumes. There is no rigid-body dynamics,
no trajectories and no fluid field (`docs/KNOWN_LIMITS.md`). The design is `docs/design/TIER1_PHYSICS.md`; the
integration contracts are `docs/design/TIER1_INTEGRATION.md`.

```
house_sim/data/physics/   materials.json  liquids.json  floors.json  supports.json  laws.json
house_sim/kernel/phys/    props lawbook forces contain support statics impact liquids thermal environment processes
                          settle events observe
house_sim/kernel/physics.py   the facade: wires the layer into the engine and re-exports its functions
```

`from house_sim.kernel import physics` gives `physics.fall`, `physics.spill`, `physics.lift_plan`, `physics.props`,
`physics.fits`, `physics.wind_effect`, ... (the module list at the end of `kernel/physics.py`).

## 1. Properties

`physics.props(state, eid)` returns a `Phys` record: material class, mass and resting size, shape, centre of mass,
strength (`drop_tol_m`, `crush_kg`, break mode), friction, waterproofing and absorption, heat limits, melting and
thermal-shock tolerance, ratings (`rating_kg`, `shelf_rating_kg`, `hang_kg`, `handle_kg`), rims, openings and
nesting, rolling, flotation, anchoring. Nothing is added to the entities: each field resolves, most specific first,
from

1. the entity record (the type's props plus setup and generator overrides; names in section 8 of the setup table),
2. the `phys.props` hooks (domain refinements such as ripeness; a hook that changes `shape` gets `rollable`
   recomputed from it unless it sets `rollable` itself, and sets any other derived field it affects: `rigid`,
   `break_mode`, `crushable`),
3. the `supports.json` rows of its type, ancestors, head noun, category and tags,
4. its material class (`materials.json`: 129 registry material strings map to 42 classes),
5. the global default.

**Per-instance variation.** Strength thresholds and ratings vary deterministically by about +-15 % per instance
(a hash of the house, the id and the type: no stored state, the same in every process and replay). `phys_exact:
true` on a record turns it off (tests, setups that need an exact threshold). Keep reference plans well away from
thresholds.

## 2. Laws and their states

A **law** is the kernel applying a rule automatically. The functions behind every law are always callable by
domains and events; `laws.json` says whether the kernel applies it on its own. A law that duplicates a domain's
existing behaviour starts **deferred** so the verified specs do not change; it is switched on (stage D1) after every
domain listed for it has routed its own code through the kernel (`docs/design/TIER1_PHYSICS.md` 10.3).

| law | state | what it does |
|---|---|---|
| orphan | active | an item whose support was destroyed falls from the height it rested at |
| overflow | active | liquid above a holder's capacity runs out where it stands, down its support chain |
| drip, creep | active | processes started explicitly (drips) or staged by a spec (an overloaded shelf giving way at its creep time) |
| setdown | deferred | `place` sets the item down: a drop of 0 / 2 / 6 cm (careful / normal / fast, the effective speed) onto the support. Deferred because manipulation's `after_placement` already chips fragile ware set down fast on hard surfaces (one fast set-down counted twice) and food keeps a broken egg looking intact until it adopts `phys.broke` |
| overload, hang, crush, handle, edge, slope, stack, furniture_tip, open_spill, contact_heat, wet_contact, thermal_shock, phase, melt, carry, thermal_tau, wind, rain | deferred | see TIER1_PHYSICS 5.2-5.10 |

`physics.law_active(name)`; tests switch deferred laws on with `lawbook.forced(crush=True): ...`.

## 3. When physics runs: the settle pass

The engine runs `settle(env, "action", ctx)` once per step, after the action (its handler, hooks and energy) and
before its message is built, and `settle(env, "substep")` after every 30 s substep's systems and events. Nested
actions (undo, scheduled commands, domain chains) are covered by the enclosing step's settle.

* **What it looks at.** Every entity write (`w.mut`, `create`, `destroy`) is logged as a touch. Settle reads the
  touches since it last looked, keeps those whose structural signature changed (location, relation, shelf, spos,
  pos, orientation, rot, open, mass, liquid, size, anchored, secured, tilt) and destroyed entities, and applies every
  active law, in the order of `laws.json`, to them, their support chains, what rests on them and the orphans of
  destroyed supports. What a consequence moves joins the queue. Kernel thermal writes are not touches.
* **Never a refusal.** Settle never raises; a refused action rolls back with its touches, so nothing settles from it.
* **Budget.** 64 consequences per settle call: the pass stops after the call that crosses it, emits `phys.budget` and
  counts `stats.phys_budget_hits`. The same input stops at the same place every time.
* **Notes.** In the action phase every consequence adds an id-only note to the step's message ("cup_1 fell and
  broke", "a shelf of bookcase_1 gave way"); realistic perception turns the ids into handles or "something". Notes
  never carry numbers.
* **Undo.** A step that caused a physical consequence leaves no undo record: a broken glass cannot be undone.
* **Early settle.** A verb that wants its consequences in its own message calls `ctx.settle_physics()`.

### Attribution (who caused it)

A consequence is the **robot's** when it happens in the action phase (the action and its settle), or when the robot
arranged the entity: the entity itself or a support in its location chain (what it rests on or in, up to the
furniture) carries `_robot_moved`, an underscore marker the action-phase settle writes on everything an action placed,
moved, filled, resized or fastened (opening or closing alone writes none; never observed). A support failure
(a collapsing or creeping slot, a hanger pulling out, a handle tearing, a crushed body, a toppling stack, overbalanced
furniture) is also the robot's when the load that made it fail carries the marker: the failing support carries load
the robot added. Siblings under the same furniture never count, so a scripted bump that knocks a centrepiece off the
table the robot just set is exogenous, while the plate the robot set there counts. Only the robot's consequences raise
the global harm flags (`breakages`, `spills`, `damage`); **exogenous** ones (scripted events, pets, people) are counted
in `phys_*` statistics and events only. Kernel calls take `by="robot"` or `by="exogenous"` to decide it explicitly.

## 4. Consequences (observable state)

| consequence | fields written | event |
|---|---|---|
| fall | `fell`, new location (floor cell or a lower surface), `orientation: on_side` for tall items | `phys.fall` |
| break | `broken`, `condition: broken`, `damage.broken`; glass and ceramics: `shattered`, hazard `sharp`, a sharp floor hazard (not for a contained break, below); liquid runs out | `phys.break` |
| chip / bruise / crush / dent / scratch | `damage.<kind>` levels 0..1 (never lowered) | `phys.chip`, `phys.crush`, `phys.dent`, `phys.scratch` |
| shelf or surface collapse, pull-out, tear | `collapsed_shelves`, `damage.collapsed`, `damage.pulled_out` / `hole`, `torn` | `phys.collapse`, `phys.pullout`, `phys.tear` |
| tip, slide, roll | `tipped_over`, `orientation`, `spos`; `in_motion: rolling` while rolling | `phys.tip`, `phys.slide`, `phys.roll` |
| spill, overflow, drip, leak | `liquid_l`, `wetness`, kernel puddles (`generic_item`, `category: spill`, room `wet_cells`) | `phys.spill`, `phys.overflow` |
| heat, wet contact, shock, phase, melt | `damage.scorch` / `melted` / `ring_stain` / `swollen` / `water_damage`, `ice_frac`, breaks | `phys.scorch`, `phys.melt`, `phys.ring`, `phys.shock`, `phys.burst` |
| wind and rain | `blown_away`, new cell, `wetness`, `moisture`, `soaked` | `phys.blown` |
| cues | `sagging` (a rated slot above 90 % of its rating), `wobbly` (a slender stack) | - |

Statistics: `phys_falls`, `phys_breaks`, `phys_spills`, `phys_tips`, `phys_collapses`, `phys_blown`, `phys_rolls`,
`phys_budget_hits`. Hooks a domain may register: `phys.props`, `phys.spill_sink`, `phys.wetted`, `phys.broke`,
`phys.fell`, `phys.tipped`, `phys.collapsed`, `phys.contact_heat`, `phys.carry_motion`, `phys.tip_loads`,
`phys.items_above`, `phys.exposed`, `phys.settle_extra` (signatures in TIER1_PHYSICS 7.3), `phys.uncontained`
(`fn(w, eid, info)`, below), `phys.liquid_arrives` (`fn(w, eid, liters, liquid, source, by)`, section 5). Inside a
`phys.wetted` hook `liquids.wetness_before(w, eid)` is the wetness the thing had before the arrival the hook sees (the
kernel writes the new wetness first; every kernel arrival -- `liquids.wet`, rain, a wet item's seep -- runs the hooks
through `liquids.run_wetted`), so a domain adding up arrivals since a thing was last dry can tell it dried in between
(phase-1 close-out, electrical's request); None outside such a call.

**Wrap containment (a contract for every domain).** A break inside a full wrap (`wrapped.coverage` >= 0.95, any
material, or a plain `wrapped` flag) or inside intact packaging (`packaged`) is *contained*:
`physics.impact.contained(record)` is true, the pieces stay in the wrap, the item is sharp to handle (`hazards`,
`handled_with`) but no floor hazard, and `phys.broke` hooks and the `phys.break` event carry `contained: true` (the key
is absent otherwise). While it holds, no domain may scatter, spread or spawn the pieces (no shard field, no shard
outside the wrap, no shard spreading on a walk); once the wrap or the packaging comes off they are the domain's to
scatter. An opaque wrap also hides the break (`shattered` stays off); a see-through one (bubble wrap, cling film)
contains the pieces but shows them. A fallen fully wrapped container keeps its loose contents (they are jolted, not
thrown out); liquid soaks through. The cross-layer test is `tests/test_physics_settle.py`
`CrossLayerContractTests` (independent review 2026-10-22, H11 defect 2). **Containment ends** with
`physics.impact.uncontained(w, eid, by=None)`, which the domain that removes the wrap or the packaging calls after
clearing `wrapped` / `packaged` (phase-1 red team: an unwrapped broken glass left no shards and no floor hazard): a
shattered sharp break shows (`shattered`), gets the sharp floor hazard on a floor, runs the `phys.uncontained` hook
(info: by, room, mode; a domain answers it with what it scatters for an open break, e.g. safety's shard field) and is
recorded as a `phys.uncontained` consequence; idempotent (`_pieces_out`). Hooking it up is manipulation's (unwrap)
and safety's (shards) -- catalog/analysis/phase1/breakages.json.

**Stacks.** Each member of a stack is judged against the top of the one below it: its declared `surface_size_m` (a
kitchen scale's weighing platform, a trivet, a charging pad, an ironing board's top) while it stands upright, else its
footprint, in that member's own frame. The oversize rule compares the part that bears on it (its footprint, or its foot
`base_m` when it stands on one), so a 0.29 m mixing bowl rests on a scale with a 0.32 m platform and a bowl on its foot
ring rests on a small saucer; a 0.5 m tray on a dinner plate still rocks off. Something that opens out into another
shape has its in-use size while it is open (`open_size_m`: an ironing board set up on its legs, an opened drying
rack); folded it offers only its folded footprint (independent review H14: forcing the `stack` law broke 8 specs, the
bowl on the scale among them).

## 5. Processes, heat, wind and rain

* `physics.processes` (system order 48): timed checks (wet-contact rings, staged creep, handle creep) and continuous
  processes (rolling, dripping, leaking, contact heat, freezing and thawing, melting). The state section `physics`
  appears only once a process exists. `physics.drip_start(w, eid, l_per_h, until_l=None, by=None)` and `phys.liquids.leak_start(...,
  by=None)` take who started them: with `by="robot"` every portion they deposit is the robot's (a pot it over-watered
  drains into a puddle as its spill), with `by=None` each portion is judged like any consequence.
* Kernel `thermal` (order 50, every 120 s): Newton relaxation of every entity toward its ambient (parent heat source,
  conducting or insulated parent, else room air or the weather outdoors), skipping bodies within 0.05 K of it and
  anything a domain owns (`thermal_owner`, or the older `fixed_temperature`). The time constant is the legacy
  300 sqrt(m) + 60 s until the `thermal_tau` law (lumped m c / h A) is switched on with the cooking and plumbing
  adoption.
* `physics.environment` (order 47): wind and rain on exposed outdoor objects while the `wind` / `rain` laws are active.
  The weather's wind is the 10 m open-terrain value; at about 1 m in a yard an item feels 0.4 of the mean and 0.6 of
  the gust (balconies 0.6 / 0.75; a room's `wind_exposure` overrides). A gust lifts sheets and tips tall things over;
  sliding needs the sustained wind (a calibration choice kept from the stage-A review; it under-predicts how often
  light things blow about in gusty weather, see `docs/KNOWN_LIMITS.md`). Wheeled things stand on rubber tyres: they
  skid only at rubber's friction on the floor, so a wheelie bin blows over in a gust (about 19 m/s at 10 m) instead of
  skidding away; free-rolling casters roll at the floor's rolling resistance. A blown thing travels along the downwind
  line and comes to rest in the last cell before the room's edge, a doorway's clearance or a path-blocking footprint
  (its own support excepted; phase 1, outdoor_plants' request: it used to land on the doorway cell and cut every
  route through it); one already pressed against such a cell stays.
* Liquid holders (`phys.liquids.holds_liquid`, phase 1, review L3; the rule plumbing_liquids' verbs use): an open,
  waterproof (or explicitly `watertight`) holder with a capacity that is liquid-tight (`liquids.liquid_tight`): an
  explicit `watertight` decides; perforated (`perforated`, `holes_mm` > 0) or open-weave bodies (wire, coated-wire
  racks, mesh, wicker: `OPEN_WEAVE_MATERIALS`) let poured liquid run through to what is below (`drains_through`);
  porous or soft-sided ones, open shelving (`open_shelving`: a shelving unit's capacity is storage) and those opening
  at the front, side, back or bottom (the type's view `opening`) are not holders (poured liquid is spilled onto
  them). Types that are open frames or vented (a pet crate, a firewood rack, a toaster) need their own flags in the
  type data. A holder pools up to `liquids.hold_capacity_l`: its `pool_l` when it has one (a cavity whose
  `capacity_l` is storage, not a vessel's: an opened oven holds about a litre on its floor; `pool_l: 0` holds
  nothing; phase-1 finish, appliances_cooking's request), else `capacity_l`, else top area x `rim_m`. A domain that
  models a holder's contents itself (outdoor_plants' potting soil) takes what arrives first through
  `@hook("phys.liquid_arrives")` `fn(w, eid, liters, liquid, source, by) -> litres taken or None`: pours
  (`pour_in`) and spills, drips and overflows landing in it (the support-chain walk) ask it before pooling the rest
  (`liquids.arrives`; phase-1 finish, outdoor_plants' request).
* Wind gates: `environment.free_to_move(state, eid)` (what can move at all) and `environment.cannot_move(rec, v,
  gust)` (the cheap bound before any property lookup) are public (phase-1 finish, outdoor_plants' request: its
  deferred wind copy gates on them); the former private names stay as aliases.

## 6. Perturbation events

Schedule them with the ordinary `event` setup op (`at_s`, `at_step` or a `when` predicate, optional `repeat_s`):

```json
{"op": "event", "when": {"path": "entities.{glass}.location", "op": "eq", "value": "{table}"},
 "effect": "physics.bump", "args": {"target": "{table}", "g": 0.3}, "repeat_s": 300}
```

| effect | args | models |
|---|---|---|
| `physics.bump` | `target` (item, or a surface: everything on it) or `room`; `g` (0.3); `direction` (`toward_edge` or degrees); `max_h_m` | a dog or person brushing past; a cat's tail at most 0.6 m up |
| `physics.nudge` | `target`, `newtons`, `height_m`, `direction` | a push on one item: it tips if the push's moment beats its weight's first, else it slides |
| `physics.pull` | `target` furniture, `kg` or `newtons`, `height_m` or `at: "top_drawer"` | a child pulling or climbing (27 kg: ASTM F2057) |
| `physics.load_test` | `target`, `slot` or `shelf`, `kg` | a test load, someone leaning on a shelf |
| `physics.wind_gust` | `room` or `outdoor: true`, `mps` (10 m gust; `local: true` for the speed at the item), `sustained_mps`, `direction_deg` | a gust |
| `physics.rain` | `mm_h`, `seconds`, `room` | a shower (the weather state is not changed) |
| `physics.transport` | `target`, `drop_m` (0.3; parcels 0.76), `drops`, `jostle` | moving or shipping a packed box |
| `physics.brake` | `target`, `accel_g` (0.4), `direction` | a car or cart stopping |
| `physics.tilt` | `target`, `deg`, `direction_deg` | tilting a tray, shelf or cart: things slide, roll, spill |
| `physics.drop` | `target`, `height_m` | someone drops it |

Events are exogenous: they count against the robot only for things it arranged, or for a support failing under load
it added ("the glass the robot left at the edge fell when the dog brushed the table"); `"by": "robot"` in args
overrides that.

## 7. Setup ops for physical scenes

| op | shape | does |
|---|---|---|
| `phys` | `{"op": "phys", "target": "{x}", "props": {...}}` | validated physical overrides (`tasks.setup.PHYS_FIELDS`; ranges checked) |
| `load` | `{"op": "load", "target": "{shelf}", "shelf": 2, "kg": 18 \| "fraction": 0.8, "items": "books\|cans\|boxes\|towels\|auto", "var": "v"}` | fills one slot with filler items to exactly that mass (fraction: of its nominal rating), placed like `spawn` |
| `edge` | `{"op": "edge", "target": "{glass}", "on": "{table}", "margin_m": 0.02, "side": "front\|back\|left\|right"}` | puts an item that far from an edge; negative margins overhang, down to 30 % of its footprint (still stable) |
| `stage` | `{"op": "stage", "target": "{shelf}", "state": "overloaded", "creep_s": 600}` | no scene allowance for the support (its true rating applies); with `creep_s` a slot still over its rating then collapses |
| `check_physics` | `{"op": "check_physics", "strict": true}` | refuses a physically impossible staged scene |

**The physics lint.** After the setup ops the verifier's build checks what the setup created or changed (and the
supports under it): a slot over its rating, an item on something that is no surface, larger than its surface or with
its centre of mass off it, an item that does not fit its container, an overfull container, more liquid than a holder
holds, an open container lying with liquid in it, a hot item marking its support. It is an **error** for a spec with
`"schema": 3` (tier-1 specs) and a warning otherwise (the trial's `physics_warnings`). An entity marked
`_phys_staged` (the `stage` op) is exempt.

The scene allowance: a slot the generator or the setup loaded at episode start holds that load plus 0.5 kg before its
own rating applies; only weight the robot adds can make it give way.

## 8. Core verbs

* `pick_up` plans the lift with `physics.lift_plan` (two hands above 8 kg, for anything wider than 0.7 m or marked
  `two_handed`; at most 20 kg) and refuses with the same texts as before. The mass is the kernel's one subtree mass:
  liquids weigh their `liquids.json` density (a jug of bleach 1.1 kg/L, honey 1.42).
* **Lift monotonicity (a contract for every domain).** A lift the plan accepts holds: a domain's grip model may not
  drop a clean, dry item the plan gives one hand with the default grip (the one-hand squeeze rises to what holds the
  hand's rated load, `body.hold_capacity_kg`, clamped to `body.grip_limit_n`); a wet, oily or soapy surface,
  slippery fingers, a `gripper_slip` fault or a grip the command chose (pinch, gentle) may still let it slip. So for
  masses m1 < m2 within the robot's capacity, if `pick_up` holds m2 it holds m1 (independent review H11 defect 1: a
  50 N normal grip dropped 7.65-8.0 kg items the plan handed to one hand, while 8.01 kg was held with two). The
  cross-layer test is `tests/test_physics_forces.py` `LiftMonotonicityTests`.
* `place` checks containment with `physics.fits` (box with diagonal fits, the opening, nested and compressed volume:
  "It does not fit inside", "It does not fit through the opening", "Not enough room inside"), refuses a collapsed shelf
  ("That shelf is broken") and a support that gave way or broke to pieces ("It is broken and cannot hold anything"),
  and sets the item down (the set-down law, while it is active: deferred until manipulation and food adopt it).
* `core.subtree_mass(w, eid)` is `physics.subtree_mass`.

Ctx helpers for domain verbs: `ctx.phys(eid)`, `ctx.fall(eid, **kw)` (an object falling; the robot's own fall is
the body's), `ctx.spill(liters, liquid, **kw)`, `ctx.knock(eid, accel_g, **kw)` (all attributed to the robot),
`ctx.lift_plan(eid, hands=None, helpers=())`, `ctx.settle_physics()`. Motion: `actions.motion_record(ctx)` and
`actions.motion_records(ctx)` give the step's walking (TIER1_INTEGRATION 1.4); verbs that move the robot without
core motion should set `ctx.data["path"]` so the carry law sees the trip.

## 9. Perception

* Every physical threshold and simulation parameter is **hidden** in realistic perception (`core.json`
  `field_tiers`): drop tolerances, crush and load ratings, friction, heat limits, openings, nesting, drag, cushions.
  The robot learns them by touch, inspection, measuring, reading a printed rating (`load_rating_text`, read tier) or
  trying.
* Consequences show at a glance (`fell`, `collapsed_shelves`, `sagging`, `wobbly`, `in_motion`, `blown_away`, `torn`,
  `tipped_over`, `shattered`) or on inspection (`damage`, `broken`, `anchored`, feet and pads).
* The observer `physics.sounds` adds this step's physics sounds to `obs.sounds`: `{"kind": crash | thud |
  glass_breaking | splash | scrape | creak | clatter, "source_hint": here | nearby | elsewhere, "loudness": faint |
  noticeable | loud}` (from the impact energy or the volume; faint sounds are heard only in the robot's room,
  noticeable ones next door too, loud ones anywhere; sounds in the robot's room name their `source`).
* The inspector `physics` adds `feel` ("hard, rigid", "soft, flexible", ...) once the robot has touched the thing
  (a domain inspector's own, more specific feel, such as laundry's "damp", is kept), and,
  on a close look (`inspect` with `where="closely"`), `looks` ("sturdy" / "flimsy") for a shelf, surface, hook or
  handle, from its type's rating class. Neither carries a number.

## 10. Determinism, replays, tools

* No random numbers: variation is a hash, cascades run in sorted order with a fixed budget, process queues are
  ordered by (due time, sequence). Caches are pure functions of the state.
* `env.save_replay` records `metadata.physics_version` (a digest of `data/physics/*.json`); replaying under other
  physics data warns that outcomes may differ.
* `python tools/physics_audit.py --data --bench`: unmapped materials, law registrations, data gaps per domain, domain
  tier overrides of hidden thresholds, row-derived openings and nest groups, every physics consequence and lint
  warning in the authored reference runs, and the cost budgets.
* `python tools/golden.py ...`: the room-mode golden of the tier-1 gate (`tests/test_room_golden.py`).

Costs (TIER1_INTEGRATION 3.4): a settle with nothing touched about 0.01 ms, a typical action well under 1 ms, the
64-consequence worst case a few ms; the kernel thermal system writes temperatures through a field-level journal
(`World.set_quiet`), which made it several times cheaper than the pre-v3.0 system.
