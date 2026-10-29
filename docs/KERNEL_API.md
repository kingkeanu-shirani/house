# Kernel API (v2) — contract for domain modules and task authors

Python 3.9+ standard library only. No `match`, no `X | Y` type unions at runtime, no third-party imports.

## Layout

```
house_sim/kernel/       world state, registries, actions, time, geometry, perception, physics, evaluation (owned by core)
house_sim/systems/      one module (or package) per domain; core.py holds core actions
house_sim/data/types/   object types: core.json + <domain>.json
house_sim/data/actions/ optional data-only transformations
house_sim/generator/    house layout + Placer helpers; furnishers come from domains
house_sim/tasks/        spec expansion, setup ops, reference skills, runner, verifier
tasks/authored/         one JSON file per authoring batch: {"specs": [...]} (one spec per catalog id)
tasks/templates/        optional shared spec templates
```

`kernel.plugins.load_domains()` imports every module in `house_sim/systems` (core first). A domain named `cleaning`
is `house_sim/systems/cleaning.py` (or package `house_sim/systems/cleaning/`) and its types file must be
`house_sim/data/types/cleaning.json` (same stem). During parallel development set `HOUSE_SIM_DOMAINS=cleaning`
(comma-separated; core is always loaded) so only those modules and type files load. A domain module registers
everything on import. Two modules must never register the same action, system, skill, helper or type name (import fails).

## World state

`env.world` is a `kernel.world.World`. **All mutation must go through it** so refused actions roll back exactly
and the strict-mode test (`HOUSE_SIM_STRICT=1`) passes:

| Read | Mutate |
|---|---|
| `w.ent(eid)` | `w.mut(eid)` (returns the same dict, journaled) |
| `w.room(rid)` / `w.person(pid)` / `w.pet(pid)` / `w.conn(cid)` | `w.room_m` / `w.person_m` / `w.pet_m` / `w.conn_m` |
| `w.get("weather")`, `w.s["utilities"]` | `w.sec("weather")` (whole top-level section journaled) |
| | `w.create(eid, record)`, `w.spawn(type, location, **props)`, `w.destroy(eid)` |
| | `w.set_scalar("time_s", v)`, `w.set_path("entities.x.open", True)` |
| `w.children(p)`, `w.descendants(e)`, `w.ancestors(e)`, `w.root_room(e)`, `w.within(e, a)`, `w.find(type=, room=, within=, tag=, where=)`, `w.held_items()`, `w.is_a(e, t)` | `w.emit(kind, **data)` (event log), `w.stat(name, n)`, `w.fact(k, v)`, `w.log_move(e, a, b)` |

Never keep a reference across steps; never mutate dicts obtained from `ent()`/`room()`/`w.s[...]` directly.

State shape: `rooms`, `connections` (doorways/doors/stairs/gates/hatches/elevator doors with `cells` per room), `entities`,
`people`, `pets`, `robot` (`room`, `pos` [col,row], `hands` {left,right}, `posture`, `battery`, `standing_on`, `speed_mode`,
`quiet`, `privacy_mode`, `camera_clean`, `network`, `actuator_ok`, `arms_ok`, `fallen`, `faults`, and from v3.0 the
body's fields: "v3.0 robot body"), `utilities`, `weather`,
`clock` (`start` ISO), `time_s`, `steps`, `stats`, `facts`, `reports`, `memory`, `approvals`, `orders`, `pending`,
`events`, `event_log`, `messages`, `logs.moves`, `household`, `user_simulator`, `metadata`.

Entity essentials: `type`, `is_a` (ancestors), `label`, `category`, `location` (room id, entity id, person id, pet id, or
`"robot"`), `relation` (`on` | `in` | `hung` | `under` | `behind` | `held` | `attached` | `part`), `pos` (room cell,
only when located directly in a room), `spos` ([x,y] meters on a surface), `shelf` (index into the parent's
`shelf_heights`), `size_m` [w,d,h], `mass_kg`, `volume_l`, `capacity_l`, `inner_size_m`, `material`, `color`,
`soil` (dict soil_type -> 0..1), `wetness`, `temperature_c`, `fragile`, `broken`, `portable`, `openable`, `open`,
`lockable`, `locked`, `switchable`, `on`, `settings` + `setting_options` + `control_kinds`, `appliance` (programmable
cycle), `owner`, `home`, `private`, `off_limits`, `risk`, `requires_approval`, `hazards`, `safe_grips`, `affordances`,
`tags`, `secret_fields` (ground truth never observed). Keys beginning with `_` are never observed either.

## Object types

`house_sim/data/types/<domain>.json`: `{"domain": "...", "types": {"name": {"is_a": "parent", "props": {...}, "doc": "..."}}}`.
Props deep-merge into the parent's; `"+affordances": [...]` appends. Reuse core bases: `portable_object`, `fixture`,
`furniture`, `surface_furniture`, `storage_furniture`, `openable_storage`, `carry_container`, `door`, `light`, `table`,
`cabinet`, `drawer`, `bookshelf`, `closet`... Create entities with `w.spawn(type, location, **props)` or
`Placer` helpers. Realistic dimensions, masses and materials matter: geometry, reach, fit and carrying use them.

## Actions

```python
from house_sim.kernel.actions import action
from house_sim.kernel.errors import ActionError, require

@action("wring", params={"target": "entity"}, duration=6.0, energy=0.02, noise=0.1, category="physical",
        doc="Wring water out of a held mop or cloth over a sink or bucket.")
def wring(ctx):
    mop = ctx.mobj(ctx.args["target"], touch=True)        # visibility, reach, privacy, approvals, capability
    require(ctx.holding(ctx.args["target"]), "Hold it first")
    ...
    return "Wrung out"                                     # message (str) or JSON-able dict
```

* Params: `"entity"`, `"?number"`, `"?str:a|b"`, `"cell"`, `"list"`, `"dict"`, `"bool"`, `"text"`, or a dict with
  `type`/`default`/`min`/`max`/`choices`. `target` and `destination` receive env.step's positional args.
* `duration` seconds or `fn(ctx)`; add more with `ctx.spend(s)`. `noise` adds to `stats.noise` (quietness).
* Energy (kernel v3.0): the robot body charges every action its activity power over its seconds plus its walking work
  (see "v3.0 robot body"); `energy=` is optional extra mechanical work (legacy % units), `noise_db=` the action's
  loudness at 1 m (quiet mode refuses above 55 dB), `arms=` the arms it works with.
* `inverse=fn(ctx, before)` returns the undo command; `irreversible=True` clears the undo stack.
* Refuse with `ActionError(message, category)`; categories: `precondition`, `argument`, `safety`, `privacy`,
  `social`, `authority`, `resource`, `perception`, `capability`. Refusals roll back and count in stats.
* Ctx helpers: `ctx.obj(eid, touch=, grasp=, reach=)`, `ctx.mobj(...)`, `ctx.holding(e)`, `ctx.hand_of(e)`,
  `ctx.free_hand()`, `ctx.visible(e)`, `ctx.person_here(p)`, `ctx.rng` (deterministic), `ctx.data` (scratch, e.g. path),
  `ctx.env` (do not call env.step inside a handler; use `ctx.env.perform(command, ctx)` for nested actions).
* Extend existing actions with hooks instead of re-registering them:
  `@before("open")` (raise to refuse) and `@after("move")` (apply effects, e.g. stepping on a wet floor).
* Core actions (systems/core.py): look, wait, inspect, read, report, move, approach, walk_to, crouch, kneel, stand,
  pick_up, place, hand_over, receive, open, close, lock, unlock, switch_on, switch_off, set, start, pause, cancel,
  set_mode, speak, announce, refuse, explain, confirm, handoff, listen, ask, stop, resume, set_quiet, set_speed,
  remember, forget, recall, watch, cancel_watch, run_pending, undo, snapshot, diff.
* Shared helpers in `house_sim.systems.core`: `place_item(ctx, target, dest, relation, spos, cell, shelf, orientation)`,
  `apply_setting(ctx, eid, setting, value)`, `check_handling(ctx, eid)`, `subtree_mass(w, eid)`, `say(ctx, kind, text)`,
  `release_from_hands(w, eid)`, `connection_between(w, a, b)`, `require_tool(ctx, capability, tool_id=None)` (returns the
  held tool offering a capability; refuses broken/depleted/flat tools), `add_soil(w, eid, kind, amount)`.
* Physics helpers in `house_sim.kernel.physics`: `is_powered(w, eid)`, `water_available(w, eid)`,
  `room_temperature(state, rid)`, `appliance_start(w, eid, program)`; appliance hooks
  `@hook("cycle_check:<type>")` (return an error string to refuse), `cycle_start:<type>`, `cycle_tick:<type>` (w, eid, dt),
  `cycle_complete:<type>`. Thermal: entities relax to their parent's `internal_temp_c` (ovens, fridges, burners),
  a `conducts_heat` parent's temperature (pots), else room/outdoor temperature.
* Geometry in `house_sim.kernel.geometry`: `CELL`=0.5 m, `grid_size`, `footprint`, `blocked_cells`, `hazard_cells`,
  `find_path`, `anchor`, `height_m`, `reach_problem`, `surface_spot`, `fits_inside`, `free_floor_cell`.
  Floor hazards: an entity with `floor_hazard` ("wet", "trip", "sharp", "fragile", "slip", ...) or room `wet_cells` /
  `floor_hazards`. `move`/`approach`/`walk_to` accept `avoid` (hazard kinds) and record `ctx.data["path"]`.

## Systems (time)

```python
from house_sim.kernel.plugins import system

@system("plants", order=120, max_dt=60.0)
def plants(w, dt, tick):        # called every substep (<=30 s) while simulated time advances
    for eid in w.find(type="plant"): ...
```
`tick.rng(name)` is a deterministic RNG. Keep systems cheap: iterate only relevant entities. Long waits (hours/days)
advance in 30 s substeps, so per-substep work must be small.

## Other registries (`house_sim.kernel.plugins`)

* `@furnisher(name, order)` `fn(config, rng, opts, registry)` adds the domain's items to generated houses using
  `house_sim.generator.place.Placer(config, registry, rng)`: `furniture`, `mount`, `on`, `inside`, `hang`,
  `floor_item`, `rooms(type)`, `find(type, room)`. Order: core 10, structure-dependent fixtures 20-40, appliances 40-60,
  small items 60-90, people/pets 90+. Furnish realistically (every kitchen has a sink, fridge, stove...). Never break
  doorway connectivity (Placer checks it for furniture).
* `@scenario_helper("domain.name")` `fn(setup_ctx, **args) -> dict` builds complex task setups (setup op `helper`).
* `@skill("domain.name")` `fn(runner, **args)` reference skills for plans; route with `house_sim.tasks.skills`
  (`go_room`, `approach`, `fetch`, `put`, `use`, `give`, `ask`, `reveal`) and change state only via `runner.do(...)`.
* `@observer(name)` `fn(env, w, obs)` adds observation channels (sounds, smells, displays). Only add what the robot
  could plausibly sense from its current room.
* `@inspector(name)` `fn(w, eid, record, details, ctx)` adds fields to `inspect` results.
* `@invariant(name)` `fn(state, baseline) -> label|None` global failure (e.g. food left out too long).
* `@answerer("topic_prefix")` `fn(env, w, person_id, topic, ctx) -> dict|None` dynamic resident answers.
* `@hook(event)` generic hooks; `run_hooks(event, ...)`. Event effects in scenarios: `@hook("event:<name>")`
  `fn(w, args, env)` (e.g. `event:start_rain`).

## Task specs (tasks/authored/*.json)

See `house_sim/tasks/spec.py` (schema), `house_sim/tasks/setup.py` (setup ops) and `house_sim/tasks/runner.py`
(plans). Rules for every spec:

1. It tests what the catalog entry actually says, in its section's context. No easier proxy, no keyword flag.
2. Success predicates check observable end state produced by real mechanics (location, soil, temperature, cycle,
   settings, reports, speech, approvals...). Use `side_effects` / `preserve` / `failure` for "don't make it worse".
3. Randomize what matters (positions, which item, quantities, branch of conditional entries via `variants`, distractors).
4. Reference plan passes on seeds 0, 1, 2, 41, 97; waiting does nothing useful; each wrong plan is a plausible
   mistake and must fail for the right reason (not because the plan crashed).
5. Risk 3 tasks require approval/presence mechanics; never let a task succeed by skipping safety.
6. Check with `python -m house_sim --check tasks/authored/<file>.json --workers 4` until every spec passes.

## v2.1 kernel changes (integration round)

* **Performance APIs** — `w.static_index(name, predicate, reads=None)` returns sorted ids of entities matching a
  predicate of one entity record (type, is_a, category, fixed tags, ...). Contract (phase 1, review M3; the
  docstring of `World.static_index` and docs/KNOWN_LIMITS.md section 3): the index is the set of entities whose record
  *as it was when the current step began* matches, plus or minus the entities created or destroyed since (at once). A
  field the predicate reads that changes during a step counts from the next step on, so the content is a pure
  function of the state (a saved and reloaded episode or a replay sees what the uninterrupted one does). A predicate
  reads only its own record; a caller that knows every field it reads may declare them (`reads=("type", "is_a")`).
  A declared predicate sees plain records, also inside a step, wherever the plain record is the step-start record
  for those fields (an entity not journaled this step, a journaled one's snapshot, one with quiet writes to other
  fields only): about three times cheaper than the read views (phase-1 finish). Strict mode reads every entity
  through a view, between steps too, and fails a predicate that reads beyond its declaration. `w.of_type(t)` is the
  type/is_a version. Re-check what can change within a step on the records you get back. Systems must not scan all
  entities every tick. `w.journaled_entities()` lists the entities journaled as whole records this step (mut,
  create, destroy; not the set_quiet-only ones), so `index | journaled` covers every record that can match now;
  `w.touch_log_complete()` says whether `touched_since(0)` is still the step's whole touch log (nothing trimmed
  since `begin()` / `rollback()`; `rollback_to` truncates the log without moving absolute cursors; `phys_scratch` is
  a new object after each of them; every entity write but `set_quiet` / `mut_quiet` is a touch).
* **Periodic systems** — `@system(name, order, max_dt, period=300)` runs once per `period` simulated seconds with
  `dt` = the accumulated time (the system must be correct for that large a dt: closed-form decay, internal
  sub-stepping where thresholds matter). Use it for slow processes (plants, drying, spoilage, compost, weather,
  inventory aging, wear). Keep fast, event-sensitive systems (fire, appliance cycles, pouring, agents walking)
  per-substep. Kernel thermal now runs every 120 s with exact exponential relaxation.
  Benchmark: `python tools/bench_systems.py 40 0 1 2 3 --domain <name>`; target for the whole engine < 15 ms per
  30 s substep on ~900-entity houses.
* **Hooks** — `@hook("episode_start")` `fn(w, env)` runs at reset before the baseline (derive fields, refresh flows).
  `@hook("visible_veto")` `fn(state, eid, entity) -> True` hides an entity from perception (and therefore from every
  action's visibility check). `@hook("path_blocked")` `fn(state, room, avoid_kinds) -> cells` adds no-go cells to
  path planning. Before/after hooks are arity-checked at registration (before: (ctx), after: (ctx, result)).
* **Perception** — a closed opaque container hides only children with relation `in` (items `on` top or `hung` stay
  visible); installed parts behind a closed `access: "cover"` slot are hidden; `submerged` children of a
  `liquid_opaque` parent are hidden; people/pets with `concealed` (not `revealed`) are not observed.
* **Grip memory** — `pick_up(grip=...)` stores `held_grip` on the item; `check_handling` uses it for later
  move/place/hand_over, so sharp items can carry `hazards: ["sharp"]` + `safe_grips` and still be set down.
* **Harms** — `collisions`, `falls`, `spills`, `breakages` are now global failure flags (with damage, injury, fire,
  flood, gas_exposure, burns, privacy_violations, safety_violations, authority_violations, pet_escape,
  food_poisoning_risk, electrical_hazard). Only increment them for genuinely harmful outcomes; tasks may list
  tolerated flags in `allow`.
* **Engine** — notes added with `ctx.note(text)` are appended to the action message. A bounded action trail
  `state["action_log"]` ({step, time_s, action, target, ok}; the last 300 actions; `target` is the true id in every
  perception mode, the agent's handle resolved first: phase 1) supports process-order predicates. `wait` accepts up to
  604800 s (7 days). Param `choices` are compared after lowercasing string values — use lowercase choices.
* **Core actions** — `place` onto the floor picks a reachable cell whose whole footprint fits; `install` accepts
  `orientation`; `set` works without reach for `control_kinds: "app"` settings on `smart`/`remote_status` devices
  when the robot has network; light switches wired to the same lights (3-way, one per doorway in generated houses)
  stay in sync; `core.OBSERVABLE_FIELDS` / `core.AGENT_OBSERVABLE_FIELDS` may be extended by domains for `watch`.
* **Geometry** — loose portable items on the floor are hazard kind `floor_item` (avoidable); wall-mounted things above
  0.85 m (and their contents) are reachable from one extra cell away (reaching over a counter); a held tool's
  `reach_extension_m` (reachers) adds to reach height; `rect_overlap` has an epsilon.
* **Generator** — furniture placement keeps every doorway and every blocking piece reachable; ceiling lights, wall
  switches and windows are `mounted`; apartments with required stairs raise a clear error.
* **Skills** — core `go_room`/`approach` route around hazards (`tasks.skills.SAFE_AVOID`) when a clear route exists,
  and use a held flashlight for dark rooms without a doorway switch. Top-level `"+setup"` etc. now merge in specs.

## v2.2 kernel changes (cross-domain bug round)

* `geometry.free_floor_cell(state, rid, near, ignore, record=None, clearance=True, reachable=True)` keeps doorway cells
  and their four neighbours (door swing) clear, checks the record's whole footprint, and only returns cells adjacent
  to doorway-reachable floor. `Placer.floor_item` uses it, so furnished floor items no longer sit in door swings.
  `geometry.door_clearance(state, rid)` and `geometry.reachable_cells(state, rid)` are public.
* `Placer.mount` (no explicit cell) picks a wall cell whose footprint fits, does not overlap blocking furniture or
  other floor-standing mounts, is outside doorway clearance (low mounts), and is reachable (one extra cell for mounts
  >= 0.85 m). Floor-standing mounts (mount height < 0.5 m, e.g. closet shelving) count as occupied for later furniture.
* `approach` targets cells within reach radius (radius 2 for overhead-mounted targets, matching reach_problem).
* `hazard_cells` includes ground-layer entities that declare `floor_hazard` (puddles) and skips entities hidden by a
  `visible_veto` hook (no privileged steering around unseen hazards).
* Core `_switch` no longer syncs switches with `three_way_with` (mechanisms owns real 3-way handle pairs).
* Core `place` applies the waste-stream rule only to real receptacles (`systems.core.is_waste_container`).
* `check_handling` ignores hazards of items enclosed inside the carried item (closed container in between, or
  `sheathed`/`stowed`/`parked`); privacy still applies.
* Handler notes are added as a `notes` list when the result is JSON (dict or JSON text), else appended as text.
* Invariants may take a third argument: `fn(state, baseline, world)` -> use `world.static_index(...)`. Invariants run
  on every step; keep them cheap.
* Setup: `remove_pets` also removes items located on the pets; a robot placed by room only gets a reachable cell.
* Skills: `go_room` raises PlanError if a move leaves the robot in the wrong room (wrong plans stop cleanly instead of
  KeyError); `safe_avoid` relaxes hazard lists step by step; SAFE_AVOID includes `tail` and `dock_zone`.
* `tools/bench_systems.py` advances simulated time like the engine.

## v2.3 kernel changes (layout and routing round)

Layout guarantees (generator): every generated house passes `generator/repair.py` after all furnishers. Per room, the
robot dock is in the walkable area joined to the doorways (the dock is placed first, before every furnisher), no
loose floor item cuts a hazard-free route or the only hazard-free access to anything (offenders are re-placed near
where they were, or overflow into a connected room), and people/pets stand on free cells outside doorway clearance
that cut no route. `python tools/layout_audit.py [n] --verbose [--domain NAME]` sweeps seeds and attributes the
problems repair cannot fix (overlaps, door clearance, unreachable fixtures, hazard-only access, chokepoints) to the
furnisher that created the entity; a furnisher is done when it owns no audit lines.

* Shared door model in `kernel.geometry`: `swing_room(state, door)` (explicit `mech.swing_into`/`swing_into`; else
  the indoor side of an exterior door; else the private side of a door onto an escape-route room; else the second
  room), `door_swing(state, door) -> (room, leaf cells)` (inward cell + hinge-side diagonal, + second inward cell for
  leaves > 0.95 m; `hinge: "far"` mirrors), `overhead_door_cells(state, door) -> {room: threshold run}`,
  `wall_inward(room, cell)`, `doorway_cells(state, rid)`. Domains must use these instead of private copies.
  `door_clearance(state, rid)` = doorway cells + 4 neighbours + leaves swinging into the room + overhead thresholds
  and the row inside them.
* `geometry.floor_standing(entity)`: takes floor cells (furniture, appliances, loose items, mounts at floor level
  taller than 0.05 m such as closets and furnaces, low mounts >= 0.25 m deep). Thin low wall mounts (radiators,
  registers, photo eyes), flush floor registers, switches, windows, lights, outlets, hooks, rugs and doors do not.
* Access: `access_targets(state, rid, level)`, `access_report(state, rid, blocked, targets)`, `not_worse(before,
  after, new_keys)`, `components(cells)`, `articulation_cells(cells)` (4-connected, matching find_path's no-corner-cut
  moves), `placement_hazards(state, rid)` = hazard_cells + `@hook("derived_hazards")` fn(state, rid) -> iterable of
  (cell, kind, source) for hazards a system only derives at run time (dock keep-clear zones, cord runs): register one
  so generation/setup placement can see them.
* `free_floor_cell(..., strict=None, keep_access=False)`: never returns a cell in door clearance when strict
  (default strict for records >= 0.15 m tall or path-blocking); keep_access (generation/setup) refuses cells that cut
  a hazard-free route or the only access to something, and requires the new thing itself to be reachable without
  stepping on anything. Returns None when nothing fits (no silent fallback into door swings for tall items).
* Placer: `furniture` keeps connectivity "not worse" (doorway groups, the dock, furniture, fixtures and mounts that
  were accessible stay accessible; agents ignored; loose items ignored) trying full protection first, then basic
  (doorways, dock, floor-standing things); `floor_item(..., cell=None, keep_access=True, strict=None)` uses the
  keep_access rule (pass `blocks_path=True` in props for path-blocking floor items, never flip it afterwards);
  `agent_cell(rid, near=None)`; `mount(..., strict=False)` relaxes reach before a last-resort in-grid cell (strict ->
  None); `inside` records `pack_orientation: "flat"` when an item only fits lying down.
* Setup: `spawn floor` accepts `cell` and `obstruct: true` (deliberate obstruction, skips keep_access); `move` to a room
  uses keep_access (or `obstruct`) and raises SpecError when nothing fits; `person`/`pet` accept `near` (cell or
  entity id) and raise SpecError instead of dropping agents on cell [0, 0]; `robot` by room gets a non-cutting cell.
* Generator: `require: ["patio"]` (or `["deck"]`, or both) builds exactly that terrace; overhead garage doors get a
  doorway cell whose whole threshold run fits one wall; bedroom closets/wardrobes are placed before beds.
* Evaluation: an invariant with a third positional parameter receives the World even when it has a default
  (`fn(state, baseline, world=None)` now works).
* Routing: `geometry.find_path(..., ignore_sources=())`; `geometry.target_sources(state, target)`; the core
  `approach` action ignores hazard rings sourced by its target and what the target rests on (a sleeping pet's ring,
  the hot stove under a pot). Skills: `safe_avoid(state, room, start, goals, requested=None, ignore_sources=(),
  strict=False)`; core go_room/approach use strict=True (PlanError rather than walking over sharp/fragile things,
  sleeping animals or people); `room_route` is cost-based (locked doors without key or inside thumb turn +20, outdoor
  detours on indoor trips +0.5); go_room passes a door held partly open (open_angle_deg < 75) at careful speed.
* `@hook("path_blocked")` functions may take a 4th parameter `ignore_sources` (tuple of ids): find_path passes the
  approach target and what it rests on; a hook that derives rings around agents/objects (sleeping, tail, fragile,
  hot) must not block cells whose ring comes from one of those sources. 3-parameter hooks keep working.

## v2.4 realistic perception (see docs/PERCEPTION.md)

* `HouseEnv(..., perception="oracle" | "realistic")` (default: the task's `"perception"` field, else oracle). In
  realistic mode `env.realism` (kernel/realism.py) translates handles in/out of `env.step`, renders observations by
  field tier and knowledge, and tracks knowledge outside world state. Oracle mode is unchanged.
* Domain API (all no-ops / identity in oracle mode):
  - `ctx.realistic` -> bool.
  - `ctx.grant(eid, level, fields=None)`: what the action let the robot learn ("inspect", "touch", "read",
    "measure"); applied only if the step succeeds. Measuring actions grant the measured fields, e.g.
    `ctx.grant(food, "measure", ["internal_temp_c"])` after a thermometer reading.
  - `ctx.perceived(eid, details, level="inspect")`: render a result dict about eid as perceived (use for JSON
    results that echo entity fields).
  - `ctx.percept_value(eid, field, value, level="glance")`: one perceived value (bucket / noisy / exact) or None.
  - Observers: `env.realism` is not None in realistic mode; use `env.realism.render(...)` or the ctx helpers'
    logic (`realism.context(state, eid, record)`, `realism.knowledge.level(eid, record, field)`) to emit perceived
    values instead of raw truth. Ids in observer output are translated automatically (unseen things -> "something").
  - Messages: refer to entities by id (translated to handles) rather than by label when the label could identify
    an unresolved look-alike.
* Type JSON: per type `"perception": {"glance": [...], "inspect": [...], "read": [...], "measure": {field: tool},
  "hidden": [...], "looks_like": "group"}`; file-level `"looks_like": {"group": {"descriptor": "...", "resolve":
  "inspect|read|never", "members": [type names]}}`. Entities may also carry `"looks_like": "group"` and
  `"novel": true`.
* Specs: `"perception": "realistic"`; `--check/--verify --perception realistic` forces it. The verifier's
  perceivability check fails a reference whose reported value never appeared in its observations.
* Tools: `tools/perception_audit.py` (leaks), `tools/fuzz.py` (crashes, determinism).

## v2.5 layout changes (round-2 requests)

* `geometry.blocks(entity)`: floor-standing mounts taller than 0.3 m (closet shelving, furnaces, built-in closets)
  block paths regardless of blocks_path (the robot dock stays standable).
* `geometry.front_clear(entity)`: valves, meters, breaker/access panels, cleanouts below 1.6 m keep their front cell
  free of floor items and furniture (they do not block walking). `geometry.access_fixture(entity)`: openable or
  `access_target` ground-layer fixtures (drain grates, floor drains, hatches) are access targets and are never covered.
* `door_swing`: no leaf for `mech.kind` sliding/pocket/bypass/overhead; double/French doors (or `mech.leaves >= 2`)
  sweep both hinge-side diagonals.
* `target_sources(person)` includes their bed (`in_bed`/`bed`) and others lying in it.
* `free_floor_cell(..., extra_used=(), avoid=(), prefer_not=(), ring=None)`: stand-in cells, hard and soft no-go cells,
  and the keep-out ring the placed thing will create (default 1 for fragile or >= 50 C items). People and pets are not
  obstacles in access checks; an agent placement must be on 4-connected floor joined to the doorways; the robot's
  cell is never used; path-blocking records prefer cells that wall off no free floor.
* Placer: `state()` includes the configured robot cell; `furniture(..., strict=False)` (strict keeps every mount
  accessible or returns None) checks access with current hazards impassable first; furniture and floor-standing mounts
  avoid hiding windows, blinds, shelves and other wall mounts below their top (falls back only if no spot exists);
  `agent_cell(rid, near=None, ring=0)`; `floor_item(..., avoid=(), prefer_not=(), plain=True)`;
  `mount(..., near=None, clearance=True)` honours explicit `mounted`/`blocks_path` props, never uses doorway cells,
  avoids door clearance, cells behind taller floor-standing pieces and wall space taken by overlapping deep mounts.
* Setup: `person`/`pet` with an explicit `cell` get `"_placed": True` (settling logic leaves them); sleeping/lying
  agents are placed so their keep-out ring cuts no route.
* Generator repair keeps items whose own spot cuts nothing, never moves plugged-in devices, and judges door-clearance
  strictness by stack height (a saucer carrying a plant is tall). Layout audit adds "hidden mount" and "mount overlap".
* Core furnishing: kitchens always get an under-sink cabinet and a food-safe cupboard; small bedrooms skip optional
  pieces that would leave less than about half the floor free; coat closets are a rod with a hat shelf; upper and
  medicine cabinets have consistent interiors.

## v2.6 perception refinements (domain-sweep requests)

* Knowledge: `inspect`/`read` grants (fields=None) raise only what they can reveal -- visible detail to inspect level,
  read-tier text to read level -- never touch- or measure-gated values; knowledge of things the robot itself carried
  or set down survives the move (engine calls `realism.begin_step()`); unseen moves still make it lapse.
* Tiers: type perception blocks override `secret_fields` in realistic mode; file-level `"field_tiers"` in a domain's
  type JSON apply by field name to any entity (fields a domain writes onto others' entities); dotted names
  (`"mech.slams"`) hide sub-fields of dict fields; name patterns of simulation internals are hidden by default
  (`*_until_s`, `*_since_s`, `*_at_s`, `*_tau_s`, `*_w_k`, `*_j_k`, `cl_*`, `sim_*`, `model_*`, `cont_*`) plus a list
  of thermal/fuel/contamination bookkeeping; obvious large-scale states (tipped_over, toppled, fallen, ...) are glance.
* Rendering: printed (read-tier) numbers are exact; exact zeros stay zero; non-negative quantities never go negative;
  fractions stay in [0, 1]; liquid without a known capacity shows "some liquid"/"a lot of liquid" (never
  "overflowing"); closed opaque containers show neither fill nor contents until opened (or hefted: touch);
  freshness renders only for perishables.
* Look-alikes: `"never"` groups never resolve; any non-empty read-tier field (or a group's `"label_fields"`) counts as
  a label; groups may set `"kind"` and a descriptive `"candidates"` list; inspect results of unresolved members are
  restricted like observations; an entity with `"derived_from": <id>` (a portion) looks like its unresolved source.
* `ctx.perceived(eid, details, level, exact=[keys])` (or `details["_perceived"] = [keys]`): instrument readings pass
  through unchanged; grants made earlier in the same step count when rendering that step's result.
* Observations: keys observers add to `visible_entities` records are kept; person cues are merged; visitors are
  "unfamiliar person" (no role) until recognised; abilities show only a visible mobility aid; sleeping activity reads
  "lying still"; raw room bookkeeping (floor_hazards, wet_cells) is removed from `obs.room` -- `grid.hazards` is the
  perceived list, minus invisible kinds (damp films, black ice...), hazards of un-inspected mirrors, and anything a
  `@hook("visible_floor_hazard") fn(state, rid, cell, kind, source) -> False` vetoes.
* Messages are id-translated token by token; plan strings (colon topics included) are translated by the runner.
* `read` returns every read-tier field the type declares (`realism.read_tier_fields(registry, record)`).
* Verifier: report recipients (`reports.*._to.*`) are not perceivability-checked; `approx`/`between` use their
  tolerance; JSON action messages are parsed. `Runner.skill` accepts skills with a parameter called `name`.
* Geometry: `door_swing` tolerates dict `mech.leaves`; `front_clear` only for fixtures at or below 0.9 m;
  `floor_vent` (flush registers/vents/grates/drains) is never covered by furniture; tall floor items (> 0.5 m) avoid
  hiding wall mounts; the audit uses the floor as the base of non-mounted things (z_m can be a handle height).

## v2.7 perception/layout fixes

* Wall, ceiling and floor surface entities are never floor obstacles (a wall face mounted at 0 m no longer blocks the
  perimeter row).
* Grants are committed after the action's own duration, and numeric knowledge holds while the value stays within
  about tool precision (max(0.5, 2 %)) of what was learned: a thermometer reading shows in the next observation.
* An explicit type tier ("read"/"glance"/"inspect") for a special field (battery_pct, mass_kg, ...) wins over the
  kernel heuristics; entities may carry `"_perception"` (same shape as a type block) for per-entity tiers.
* Underscore keys are stripped at every nesting level of rendered dict/list fields; glance dict fields honour dotted
  hidden sub-fields; an unresolved look-alike keeps what the robot itself felt or measured.
* Rooms: `obs.room` shows ROOM_GLANCE fields (layout, materials, light), air temperature to 0.5 C and humidity as
  dry/comfortable/humid (onboard sensing); everything else is hidden unless a domain declares
  `"field_tiers": {"room": {"glance": [...], "hidden": [...]}}`. `realism.render_room(rid, field, value)`.
* Core `light` types: `powered` (circuit live) is measure-tier with a voltage tester.

## v2.8 perception hardening (red-team findings)

* `env.step` in realistic mode returns only `ok`, `message`, `category`, `observation` (no evaluation, reward or
  termination; harnesses and verifiers call the privileged `env.evaluate()`; the task runner also gets
  `raw_message`/`evaluation` via `env.raw_results`).
* `env.affordances(handle)` in realistic mode lists which actions take an object (no sandboxed availability probes,
  no refusal reasons); stray grants are dropped at the start of each step.
* Inbound arguments: handles in any letter case; a true id (any letter case) in an entity-valued parameter is
  refused like an invisible object, anywhere else it is replaced by "something" (free text cannot be used as an
  id-to-handle oracle). Outbound messages give handles only for things perceived now or named in the command;
  other ids become "something"; true labels of unresolved look-alikes or unfamiliar things named in the command
  become their descriptor.
* Refusals are scrubbed: hazard refusals do not name the hazard class or grips; privacy/off-limits/approval
  refusals use generic wording and category "permission" (the engine still counts privacy/safety/authority blocks
  internally). Picking up a closed container never refuses because of what is inside it (realistic mode).
* Noise is a fixed draw per (entity, field, knowledge level): moving or waiting does not redraw it; worse
  conditions only coarsen the reading. Lengths ending in `_m` (glance) are camera estimates to ~5 cm.
* Knowledge that lapses shows the remembered value unless the change is visible at a glance (no change oracle).
* Doors' `locked` state appears in connections only after the robot inspected or tried the door. People and pets
  in the dark are shapes; asleep and awake lying look the same ("lying"; pets "resting"; hazard kind "lying").
  Private things look like anything else (v2.9 red-team fix: their text and contents render like anyone's; reading,
  inspecting, opening or taking them is what privacy rules govern).
  Unresolved look-alikes do not show `count`; domain inspector extras named after the true type are dropped for
  unfamiliar/unresolved things. A container the robot missed hides what is on/in it. Mirrors appear as an extra
  doorway onto the same room in `connections` until inspected (its handle is the connection's `door`).
* Standing orders (`watch`) in realistic mode: glance-tier fields only, no numeric thresholds, people/pets only
  `location`/`posture`, no `ref` operands.
* Room `light_level` is a bucket (dark .. very bright).

## v2.9 kernel round 5

Kernel requests from domain round 4 plus kernel-owned cross-domain bugs (item list:
`catalog/analysis/kernel_r5_issues.json`, items R1-R18, C1-C9, G1-G7). Oracle mode behaves as before unless noted.
Regression tests: `tests/test_perception.py` `KernelRound5RealismTests` (realism), `tests/test_kernel.py`
`KernelRound5Tests` (core, Ctx, engine), `tests/test_kernel_layout.py` (layout, routing, audit tools). The perception model itself is described in docs/PERCEPTION.md.

### Identification and look-alikes (kernel/realism.py, kernel/registry.py)

* **`realism.unresolved(eid, record=None) -> bool`** (public): True while the robot's current knowledge leaves eid an
  unresolved look-alike (its identity carries `candidates`), an unidentified `derived_from` portion, or a novel
  object; False for unknown ids; a destroyed id answers as it was when it went (`realism.gone`, saved with
  dump/load). **`realism.sealed(record)`** (public; `_sealed` kept as an alias): a closed opaque container (fill and
  contents not visible). `ctx.unresolved/identified/refuse_unidentified` (below) wrap them for handlers.
* **Look-alike group keys**: `descriptor`, `resolve`, `kind`, `candidates`, `label_fields`, and new
  `portion_descriptor`, `color`, `size_m`, `size_class` (`registry.GROUP_KEYS`). An unresolved member shows the
  group's color and size at a glance (the group keys, else the most common member color and the per-dimension median
  member size, as the usual camera estimate) -- not the values its own type gives it. A color/size the instance was
  given itself (different from its type default, e.g. a red sock) stays unless the group sets that key; a
  tape-measured size stays measured. `realism.group_glance(name, group)` returns what is shown.
* **Registry lint**: `registry.lookalike_groups()` (merged group specs), `registry.lookalike_types()` (group -> member
  types), `registry.lookalike_conflicts()`; at load `registry.lookalike_warnings` lists groups whose members differ at
  a glance (different colors, or sizes more than max(5 cm, 10 %) apart) without the matching group key, and one
  `LookalikeWarning` is emitted per process. A warning never blocks loading. Domains: align the member types or set
  the group's `color` / `size_m` (20 groups warn at the end of round 5: bubble_level, cleaning_bottle,
  combination_wrench, countertop_cooker, dog_food_bag, drink_carton, flat_blade_screwdriver, flour_sugar_bag,
  granule_bag, kitchen_sponge, laundry_jug, outdoor_cover, pill_bottle, plastic_food_tub, rubber_plunger, snow_shovel,
  soil_bag, spray_bottle, water_valve, wheeled_cart).
* **Content fields are not labels**: reading identifies a member only through a label field; `liquid`, `contents`,
  `product`, `chemical`... (core `CONTENT_FIELDS`) never count, so an unlabelled member with a liquid stays unresolved.
* **Portions** (`derived_from: <source>`): identified when the source is identified at the source's own knowledge
  level, or by the portion's own label / an inspect-resolved look at it (reading a label-less portion identifies
  nothing). Unresolved, a portion shows the group's `portion_descriptor` (default `"contents of <descriptor>"`), never
  the package descriptor; identified, its **own** label and category (not the source's). A novel source makes the
  portion read as unfamiliar. Domains that create portions should give them a meaningful own `label` and give their
  groups a `portion_descriptor` (e.g. "pile of white powder", "small white tablet").
* **Contents provenance (`contents_from`)**: a destination that received liquid / contents / product carries
  `contents_from = <source id>` (written with `core.mark_contents_from`, see below; `contents_from` and `liquid_from`
  are never-tier). While the source is unresolved and the destination has no label the robot has read, the
  destination's `liquid` / `contents` / `product` render as the source group's `portion_descriptor`, else
  `"unidentified liquid from <descriptor>"` / `"unidentified contents from <descriptor>"` (`"unidentified liquid"`
  for a novel source); a closed opaque container still shows nothing and the field's tier still applies. It clears
  once the source is identified; a destroyed source answers from `realism.gone`. `filter_details` applies the same
  rule to a `contents` detail equal to the record's field.
* **Inspect extras of unresolved things**: a domain extra is dropped only for words that tell members apart (true
  type, category, label words, ancestors), not for words every member of the group shares (`realism._shared_words`).
* **Knowledge of displaced things**: when a successful step sets `displaced: true` on an entity (it was not before)
  and the robot's knowledge matched the old placement, the knowledge follows the thing (as for carried items).

### Perception rendering (kernel/realism.py)

* **`@hook("perceivable_value") fn(state, eid, record, field) -> value or None`**: the perceivable part of a record
  field, consulted by `perceive_record` (every rendered field, glance fields and the remembered-value comparison
  included) and by `filter_details` (detail values equal to the record's). Return None to render the true value. Set
  `fn.fields = ("soil", ...)` to be called only for those fields (keeps observe fast). Knowledge still uses the true
  value. Example: cleaning reports soil on visible parts only.
* **Dict bucket rendering**: `odor {kind: intensity}` renders faint/noticeable/strong and `damage {kind: severity}`
  light/moderate/severe (edges 0.3/0.6; amounts below 0.02 are not perceptible; fixed draws), never exact floats --
  exact only when the type makes the field measure-tier and it is measured.
* **Dotted sub-field rules, wildcards and nesting**: every dict/list field (measure branch and glance fast path
  included) goes through one walker. Rules come from dotted names in the type's `perception` block / domain
  `field_tiers` and from the entity's own `_perception`: `"hidden": ["buttons.*.holds_n", "a.b.c"]` (`*` = any key or
  list index, any depth), `"inspect"`, `"read"`, `"measure": {"hopper.kg": "scale"}` and the new render kind
  `"count": ["leaves.healthy"]` (also for top-level ints, per type or per entity). The most specific rule wins.
  Inspect-tier sub-floats get inspect noise; measure-tier sub-fields need measure knowledge of the parent or a dotted
  grant (`ctx.grant(eid, "measure", ["hopper.kg"])`; `Knowledge.grant/level` resolve dotted paths).
  `realism.subfield_hidden` includes per-entity hidden names.
* **Per-entity tiers everywhere**: `perceive_record` decides each field's tier with `realism.tier(record, field)`
  whenever the entity has its own `_perception` or `secret_fields` (an explicit type tier now overrides
  `secret_fields`, as v2.6 documented); `read_tier_fields(registry, record)` includes the entity's own
  `_perception["read"]` fields, so text one domain writes onto another domain's entity can be made legible.
* **Mass includes liquid**: `mass_kg` / `weight_kg` at TOUCH (bucket) and MEASURE (value) add `liquid_l x
  liquid_density` (default 1.0), as core carry-mass does; a type that declares the printed weight (explicit read
  tier) is unchanged.
* **Fractions stay in [0, 1]**: the noisy-float clamp covers `remaining`, `shine`, `wrinkled`, `pilling`, `faded`,
  `dye_damage`, `fullness`, `progress`, `coverage`, the existing suffix names, and any float whose type default is in
  [0, 1].
* **Every thermometer field**: a measure-tier `*_temp_c` field whose tool is a thermometer (or that names no tool)
  gets the same fixed-draw thermometer error (about +-0.3 C) as `temperature_c` at MEASURE; below MEASURE it stays
  hidden.
* **Postures**: a `person_down` grid hazard of someone who is not `fallen` renders `"lying"` (as sleeping does); a
  visible resting pet gets a `"lying"` marker (subject to `visible_floor_hazard` vetoes); a person's posture becomes
  `"lying"` only when truly lying/sleeping -- a seated or standing person with a sleeping/resting activity keeps the
  posture and shows no activity (pets likewise with `"resting"`).

### Messages (kernel/realism.py)

* **Single-pass label substitution**: true labels / type names of unresolved look-alikes and novel things the command
  named are replaced by their perceived label with one alternation regex, so an inserted descriptor is never
  rescanned ("egg in its shell in its shell" is gone). The kernel's own identity strings (descriptor, portion
  descriptor, kind, "unidentified <kind>", candidates) are protected; JSON values under `label` / `descriptor` /
  `kind` / `candidates` equal to the kernel's rendering are left alone; placeholder names ("object", "generic item")
  are never rewritten. Members of a group get byte-identical messages for the same action unless the domain puts
  member-specific instance values (e.g. a different `liquid_l`) in them. Since the red-team fix pass the rewritten
  names of a look-alike are those of its whole group, and the agent's own echoed words are protected (see below).
* **Destroyed ids**: ids of things that existed earlier in the episode or were created/destroyed within the step
  (`realism._ever`, read from the world journal before commit) become their issued handle or `"something"` in
  messages and observation text; `inbound` neutralises such ids in free text. A record destroyed during the step
  keeps its label substitution for that step's message.
* **Handle order**: JSON messages are parsed, translated value by value (keys get id translation only) and
  re-serialised with `sort_keys=True`, so id-keyed dicts come out in handle order; lists of ids (or of records with an
  `id`/`object`/`item`/`entity`/`eid`/`target` key) that were in true-id order are re-sorted by handle, except under
  keys naming an order (`order`, `sequence`, `path`, `route`, `stack`, `queue`, `steps`, `history`, `trail`, `rank`).
  Two keys that both become "something" are disambiguated with " (2)". `out_obj` re-orders id-keyed dicts in
  observation channels the same way. Domains: do not rely on the textual layout of JSON messages; emit an explicit
  position when a list order matters and the key does not say so.

### Core actions, Ctx and engine (kernel/actions.py, systems/core.py, kernel/engine.py)

* **Identification helpers** (`kernel/actions.py`): `ctx.unresolved(eid)` is True when, with realistic perception, the
  robot's knowledge leaves eid unidentified (an unresolved look-alike, a `derived_from` portion of one, a novel
  object); False in oracle mode and for unknown ids. It defers to `Realism.unresolved(eid, record)` when the realism
  object has it (identity/candidates fallback otherwise). `ctx.identified(eid)` is its negation.
  `ctx.refuse_unidentified(eid, message=None)` raises one generic refusal ("You cannot tell what it is yet: identify
  it first (inspect it or read its label)", category precondition) while eid is unresolved -- call it before any
  guard whose outcome depends on what the thing really is, so every member of a group gets the same answer.
* **`ctx.fixed_rng(*parts)`**: a `random.Random` seeded from (episode seed, parts) only, for instrument error or an
  estimate that must not be redrawn on every step (`ctx.rng` is seeded per step and action and averages out). An
  episode nonce (`task["episode_nonce"]`, set by an agent harness; phase 1) is mixed in, so the draws are
  episode-secret; without one they are exactly as before.
* **`ActionError.kernel_access`**: True on refusals raised by the kernel's own access checks -- `Ctx.obj` visibility,
  robot state (actuator, battery, fallen) and reach (`geometry.reach_problem`), `ctx.check_permission` /
  `ctx.forbid`, `core.visible_or_fail` -- and False (class default) on every other ActionError. A domain that wraps
  a handler to make its own refusals generic for unidentified targets can re-raise these unchanged.
  `actions.access_error(message, category)` / `actions.require_access(...)` build such errors.
* **Hazard guard of unresolved look-alikes** (`core.check_handling`, used by pick_up, place, move with held items,
  receive, uninstall and domain verbs): in realistic mode an unresolved look-alike must be treated as possibly
  hazardous. Its rule is the strictest of its group: every hazard rule (hazards x safe_grips / handled_with) of the
  group's member **types** must be met by the grip the command gave (or the item is held with) or the gear worn
  (`core.lookalike_hazard_rules`, `core.handling_rule_met`). The list is identical for every member and does not
  depend on any hidden instance (red-team fix: in-house instances no longer count), so the outcome cannot tell
  members apart; the refusal (`core.UNIDENTIFIED_HAZARD`, category safety) names no hazard. Once the member is
  identified (read/inspect as its group requires), the rule the robot can perceive applies (`core.perceived_rule`,
  see the red-team section). A novel object (no group to compare with) is never refused for its hidden hazards.
  Whenever the act goes ahead and the item's own rule is not met, the exposure counts as a `safety_violations`
  (event `unsafe_handling`). Specs that pick up a hazardous look-alike either identify it first or use a grip/gear
  safe for every member. The same rule shape is available for other guards: `core.lookalike_rules(ctx, eid,
  relevant)` returns the member-type props that satisfy `relevant(rule)` (None for a novel object or a portion of
  one). Core `hand_over` to a child uses it with `core.child_unsafe`: an unresolved thing is refused ("You cannot tell
  whether that is safe for a child: identify it first") if any member type could be unsafe, and a novel thing always
  is.
* **Permission rules the robot cannot perceive** (`ctx.check_permission`, `check_handling`'s contained-item rules,
  core `inspect` of a private thing): `private`, `off_limits`, `requires_approval` and `risk >= 3` are never-tier,
  so a refusal would be an oracle. In realistic mode each rule goes through `ctx.forbid(eid, record, flag, message,
  category)`: it still refuses (kernel_access; the realistic scrub makes the wording generic) when
  `ctx.rule_perceived(eid, record, flag)` -- the type/entity gives the flag a perceivable tier the robot knows it at;
  a printed cue ("PRIVATE", "do not touch", ... in label_text/text/sign/warning/...) on it or on what holds it is
  legible at what the robot knows (read-tier text: after `read`); an approval the robot was granted names it (a
  question about it -- even a refused permission request -- does not: red-team fix); the task instruction or speech
  the robot heard pairs a cue word with what the robot takes it to be; or
  every kind of thing the robot takes it for carries the rule at type level (all member types of an unresolved
  group, or the resolved type: e.g. circuit breakers need approval). Otherwise the act proceeds and the violation is
  recorded once per entity and rule within the action: `privacy_violations` (private, off_limits) or
  `authority_violations` (requires_approval, risk), both global failure flags; event `permission_violation`. Oracle
  mode refuses exactly as before. Room privacy flags are glance-tier and refuse as before.
* **People and pets in the dark** (core `inspect`, realistic mode): when the robot's room is unlit (no flashlight),
  inspecting a person or pet returns only `{"agent", "label": "person"|"animal", "note"}` -- no name, role, wakefulness,
  activity, mood cues or domain inspector extras (breathing, thermal cues) -- and records no inspection
  (`facts.inspected`), since nothing was seen to report on. The shape is addressed (and reported, `agent`) by its
  dark-spell handle, never its id (red-team section). With the geometric camera the same shape answer, with the
  note `camera.WHO_TEXT`, comes in a lit room for a person or pet the camera does not identify from where the robot
  stands (`camera.agent_identified`: level "identify", 64 px, light 0.2, not backlit -- the level at which the
  observation names them); naming such a person by id is refused with `realism.FAR_WHO` (stage-B review).
* **Appliance start hooks get ctx**: core `start` now calls `core.start_cycle(ctx, eid, program)` -- the same checks
  in the same order as `physics.appliance_start` -- whose `cycle_check:<type>` hooks receive ctx when they accept it:
  `fn(w, eid, ctx)` / `fn(w, eid, *args)` positionally, `fn(w, eid, *, ctx=None)` by keyword; `fn(w, eid)` as
  before (`core.hook_takes_ctx`, `core.run_cycle_checks`). Domains that start cycles from their own actions can call
  `core.start_cycle(ctx, ...)` to forward ctx; `physics.appliance_start` (no ctx) is unchanged.
* **Observation clock** (`env.observe`, realistic mode): `time_s` reads whole seconds (half up), the calendar view
  `time` is computed from that reading, and clock readings in observer channels and messages (`time_s`, `since_s`,
  `at_s`, `*_time_s`, `*_since_s`, `*_at_s`, ...) are rounded the same way, so exact step durations computed from
  hidden quantities cannot be read off clock differences. Oracle observations keep the exact clock. Since the
  red-team fix pass the computed parts of a realistic step's duration are whole seconds in the world itself (see the
  red-team section). Durations a domain prints in its own messages are the domain's to round.
* **Contents provenance** (contract `contents_from`): `core.mark_contents_from(w, dest, source)` writes
  `dest["contents_from"]` = the origin of what arrived (`core.contents_origin`: the source's own `contents_from` when
  it holds something from elsewhere, so chains stay one deep); `core.clear_contents_from(w, eid)` drops it when dest
  is emptied. Last transfer wins. The field is never-tier (`data/types/core.json` file-level `field_tiers`); realistic
  perception renders dest's liquid / contents / product as the origin's look-alike descriptor while the origin is
  unresolved (K-REALISM). Core has no action that moves liquid or contents itself; domain pour/fill/refill/transfer
  verbs adopt the helper.

### Layout, routing and placement (kernel/geometry.py, kernel/perception.py, generator/)

* **People and pets are obstacles in placement access checks** (as core routing and the reference skills'
  `safe_avoid(strict)` treat them): `geometry.free_floor_cell(..., keep_access=...)` and `Placer._connectivity` count
  the agents already standing in the room (`geometry.agent_cells(state, rid, ignore=())`, also used by
  `blocked_cells`), so a new item, piece of furniture or agent never walls a doorway pocket off together with them.
  `Placer.agent_cell` falls back to a cell that cuts no plain route (`keep_access="plain"`) before any free
  non-doorway cell.
* **Avoid alias `"lying"`**: `geometry.AVOID_ALIASES = {"lying": ("sleeping", "person_down")}`;
  `geometry.expand_avoid(kinds)` adds them, `find_path` applies it and passes the expanded kinds to `path_blocked`
  hooks. Domains that match `hazard_cells` kinds against an avoid list themselves should call `expand_avoid` too.
* **Concealed agents**: `perception.observe` lists no grid hazards of people/pets that are concealed and not revealed.
* **Windows run along their wall**: `geometry.along_wall(room, record)` lays a wall-mounted record with an oblong
  footprint along its wall (rot 90 on west/east walls, 0 on north/south; `wall_inward` decides corners) and clamps its
  start so the footprint stays on the grid; `layout.finish_structure` uses it for windows. This changes generated
  layouts (furniture avoids the real window cells now), so seed-specific layout assumptions in domain specs/tests may
  break.
* **Serviceable outdoor lights**: `geometry.overhead_light(entity, room)` -- only indoor lights mounted >= 2.0 m are
  ladder-serviced ceiling lights; outdoor wall lights (flag `outdoor`, the `outdoor_light` family, or any light in an
  outdoor room) up to `SERVICE_LIGHT_MAX_M` (2.5 m) are access targets that nothing may seal off. Core outdoor
  furniture (porch/deck/patio/balcony chairs and tables) is placed `strict=True` (keeps every mount serviceable or is
  left out).
* **Loose-item repair**: `generator/repair._fix_loose_items` places a tall loose item (over 0.5 m with what stands on
  it) on a cell of its room that hides no wall mount, else in a neighbour room on a cell that hides none, and only then
  in front of a mount (here, then next door) -- before the last-resort nearest free cell (reported).

### Audit tools

* `tools/perception_audit.py` also flags (a) ids of entities that existed at the start of the step and were destroyed
  during it (kind `id`) and (b) id-keyed JSON in action messages and observer channels that stays in true-id order
  across a run with enough distinct id sets to rule out chance (kind `order`); (c) the `tier` rule no longer flags a
  value Realism shows by design from memory (`realism.knowledge.stale(eid, field)`: the robot learned that same value
  at the field's tier and the knowledge lapsed only because the thing moved unseen).

### What domains should adopt

* Before any guard whose outcome depends on what an unidentified thing really is, call `ctx.refuse_unidentified(eid)`
  or branch on `ctx.unresolved(eid)` and give every member of the group the same answer; re-raise
  `exc.kernel_access` errors unchanged when wrapping refusals.
* Use `ctx.fixed_rng(*parts)` (not `ctx.rng`) for instrument error and estimates that must not average out.
* Pour / fill / refill / transfer / tip verbs: call `core.mark_contents_from(w, dest, source)` (and
  `core.clear_contents_from(w, dest)` when emptied); give look-alike groups a `portion_descriptor` and portions their
  own label.
* Unresolved hazardous look-alikes need a grip/gear safe for every member (or identification) before pick_up/place;
  references that picked them up unread must read/inspect first or pass the safe grip.
* Realistic mode: a never-tier permission rule the robot cannot perceive no longer refuses -- it is recorded as a
  privacy/authority violation. Specs that expect a refusal must give the robot a perceivable cue (a legible
  "PRIVATE"/"do not touch" label it reads, an instruction, an approval it was granted).
* Clock values the agent sees are whole seconds in realistic mode; round durations your own messages print.
* Cycle-check hooks may take ctx (`fn(w, eid, ctx)` or `fn(w, eid, *, ctx=None)`); start cycles from domain actions
  with `core.start_cycle(ctx, eid, program)` to forward it.
* Use the `perceivable_value` hook (with `fn.fields`) for values only partly visible; use dotted `_perception` /
  `field_tiers` rules (wildcards, `count`) for dict sub-fields; use `odor`/`damage` dict shapes to get word buckets.
* Fix the look-alike lint warnings listed above; avoid member-specific instance values (capacity, fill) that differ
  by type within one group.
* Do not depend on a seed's exact layout (windows, counters, stove position): look surfaces up by free space and
  keep scenario combustibles/obstacles where the spec needs them.

### Red-team fix pass (kernel round 5 red team: identity & messages, privacy, darkness, routing, clock, layout)

Each item names the finding it closes; regression tests: `tests/test_perception.py` `KernelRound5RedTeamFixTests`,
`tests/test_kernel.py` `KernelRound5RedTeamTests`, `tests/test_kernel_layout.py` `WallMountOrientationTests`,
`AuditFollowUpTests`. Oracle mode is unchanged except where noted (core `read` of a private thing). Generated layouts
are unchanged (byte-identical houses).

* **The agent's own words are never an oracle** (realism `inbound` / `outbound_message`; findings "one speak step
  reveals the true type", "id-existence oracle"):
  * free text and other non-entity string arguments neutralise to `"something"` every true entity id **and every word
    shaped like a generated id on a registry type name** (`fabric_softener_1`, `dishwasher_1_basket`;
    `realism.id_shaped`) whether or not such a thing exists, so the echo of `speak` / `remember` text is the same
    either way. Rooms, people and pets keep their ids; words naming a part of the command's own target (dict keys and
    string values of its record, e.g. slot `layer_2`, key `burner_1`) are kept even when id-shaped; other words
    (`layer_2` without such a target, hand-written names) are left alone.
  * label substitution in messages rewrites, for an unresolved look-alike, the names of its **whole group** (every
    member type's label and type name, the group's listed members, every in-house member's own label and type), so a
    word that equals one member's name is rewritten for every member alike (finding "R1 still not byte-identical").
    An unfamiliar (novel) thing's own label/type is rewritten in domain text but **not inside the agent's own echoed
    arguments** (`realism._agent_text`, recorded by `inbound`) nor in a message value that is exactly a text the agent
    wrote earlier in the episode (`realism._agent_said`, e.g. a label it wrote, read back later; saved with
    dump/load): rewriting a guessed name would confirm it.
* **Novel objects belong to no look-alike group** (`actions.lookalike_group` returns None for a novel record or a
  portion of one): the hazard guard never refuses them for hidden hazards and `hand_over` to a child always refuses
  them, whatever group their hidden type is in.
* **Hazard and child-safety guards judge what the robot can perceive** (`core._hazard_guard`, `core.hand_over`;
  findings "safe_handling picks the grip from TRUE safe_grips", "C1 group rule includes instance hazards", and the
  C1 clause "or its hazards are below INSPECT knowledge"):
  * unresolved look-alike: type-level member rules only (`core.lookalike_rules`); the grip that counts is the one
    the command gave (`ctx.given`, a copy of the arguments taken before the handler runs) or the item's `held_grip` --
    a grip a handler filled into `ctx.args` from the item's hidden `safe_grips` is ignored;
  * identified thing: `core.perceived_rule(ctx, eid, record)` -- the instance's own rule once its hazards are known
    (`core.hazards_known`: the `hazards` field at its tier, i.e. after an inspect, or a visible `shattered`/`smashed`),
    else the hazards and risk its registry type gives that kind of thing (`core.type_props`) held with the item's own
    safe grips / gear; refusal `core.PERCEIVED_HAZARD` (generic);
  * hand_over to a child: refused when the perceived rule is child-unsafe (type hazards / type risk, known instance
    hazards); a hidden instance `risk` or unseen instance hazard does not refuse;
  * whenever the act goes ahead and the true record's rule is not met, the exposure is recorded
    (`core._exposure`: `safety_violations`, events `unsafe_handling` / `unsafe_handover`).
* **Privacy is a rule about acting, not about seeing** (finding "the never-tier private flag is readable"): with
  realistic perception a private thing renders exactly like any other -- printed text after a read, the contents of an
  open private box (`perception.visible` hides children of private things in oracle mode only). Core `read` of a
  private thing without a `touch:`/`read:` approval goes through `ctx.forbid` (oracle mode: refused; realistic: refused
  only when the robot can perceive the rule, else the read proceeds and counts as a privacy violation); reading a
  printed cue ("PRIVATE", "do not open") on it is no violation. `ctx.rule_perceived` no longer counts questions the robot
  asked (an `ask` naming the thing, a refused permission request): only approvals it was granted.
* **People and pets in the dark are shapes keyed by handles** (finding "observations and inspect still key them by
  readable ids"): while the robot's room is dark, `obs["people"]` / `obs["pets"]` are keyed by a per-dark-spell handle
  (`realism._dark_agents`; a new spell starts whenever the robot's view of a room turns dark, so a shape can be tracked
  while it stays dark but not recognised later), listed in handle order; messages and channels translate those
  agents' ids to the same handle (`inspect` returns it as `agent`). Inbound resolves a current shape handle to the
  agent; naming any person or pet by id in an entity argument while the robot's room is dark is refused with
  `realism.DARK_WHO` (the same answer whether or not they are in the room). The spell counter is saved with
  `Realism.dump/load`.
* **Realistic route planning uses perceived hazards only** (G2/G3 follow-up: a seated sleeper's hidden ring, avoid
  "sleeping" telling asleep from awake, concealed agents and dark rooms all steered the planner):
  `geometry.find_path(..., hazard_filter=fn)` lets the caller replace the kind matching; core `move` / `approach` /
  `walk_to` pass `ctx.route_filter` (`Realism.route_hazards` in realistic mode, None in oracle mode). It avoids nothing
  in a room dark for the robot; treats `sleeping`, `person_down` and `lying` alike as "anyone who looks like lying
  down" (cell plus ring; fallen people's markers), ignoring seated sleepers and concealed agents; counts object hazards
  only from visible sources and visible kinds (no black ice, film, damp...); and passes path_blocked hooks the avoid
  list without the agent kinds. People and pets remain obstacles as bodies. Realistic observations likewise drop
  agent-sourced sleeping/person_down markers of someone who does not look like lying. Domain motion verbs that call
  `find_path` for the robot should pass `hazard_filter=ctx.route_filter`.
* **Durations: whole seconds for computed parts** (finding "C8 rounding defeated by offsetting with actions of known
  duration"): in realistic mode the engine rounds a callable action duration and the time a handler added
  (`ctx.extra_time`) to whole seconds (half up) before advancing the world; declared constant durations are kept.
  No hidden quantity behind a computed duration can be read below one second, offsets or not. Oracle mode keeps exact
  durations (realistic-mode worlds therefore keep slightly different clocks from oracle runs of the same commands).
* **Wall mounts along their wall -- opt-in only** (G5 follow-up: other oblong wall mounts -- core wall cabinets,
  closets, closet shelves and rods, hook rails, and domain shelves/racks/hoods/pipes -- still run perpendicular into
  the room in generated houses): `Placer.mount(..., along_wall=True)` lays an oblong footprint along a wall of each
  candidate cell (`place.wall_layouts`: one option per wall the cell is on, moved back onto the grid and flush with that
  wall) and records the `rot`; `_mount_cell(..., orient=True)` is the same search for callers that move a mount. The
  default stays the old behaviour, so generated houses are unchanged. Turning it on for the core furnishers was
  measured in this pass: it cleared the perpendicular core mounts but reshuffled layouts, broke layout-dependent domain
  tests (people, pets, outdoor_plants) and exposed domain placement gaps in the layout audit (appliances_cooking moving
  upper cabinets into each other, climate/people placing a radiator and a night light into a pocket core furniture had
  sealed); enabling it needs a coordinated layout round (core furnishers plus a no-dead-pocket rule for furniture).
* **Audit** (`tools/perception_audit.py`): the `id` rule also flags ids destroyed in any earlier step
  (`realism._ever`) and the ids of people/pets that are shapes in a dark observation; a plain word under a descriptive
  key (label, kind, category...) that merely equals a hand-written id such as `sink` is not flagged.

What domains should adopt (red-team fix pass):

* Never pick a grip, tool or setting from an item's hidden values (`safe_grips`, true type) for an unidentified item:
  use the command's grip (`ctx.given`) or `held_grip`; the kernel ignores a filled-in grip for unresolved items
  (manipulation `common.safe_handling` should stop reading `rec["safe_grips"]`).
* Specs/tests that expected a refusal for an instance hazard the robot never inspected, a hidden `risk`, an `ask`
  naming a private thing, a private text being hidden, or an id-named person in the dark: the act now proceeds (with a
  recorded violation) or must use the shape handle.
* Motion verbs: pass `hazard_filter=ctx.route_filter` to `geometry.find_path` for the robot's own motion.
* Placing or moving oblong wall mounts (appliances_cooking range hoods and `clear_hood_wall` / `_counter_end`,
  cleaning tool rails, admin_inventory wall shelves, outdoor_plants tool racks, plumbing pipes, which also run off the
  grid in utility closets): keep the footprint on the grid, along its wall and clear of other deep mounts
  (`Placer.mount(along_wall=True)` / `Placer._mount_cell(..., orient=True)`, then copy `rot`), ready for the layout
  round that turns orientation on for every mount.

## v3.0 physics (tier 1; docs/PHYSICS.md, docs/design/TIER1_PHYSICS.md)

Kernel layout additions: `house_sim/kernel/phys/` (the physics layer: props, lawbook, forces, contain, support,
statics, impact, liquids, thermal, environment, processes, settle, events, observe), `house_sim/data/physics/` (its
data), `house_sim/kernel/timeline.py` (the time verbs `wait`, `watch`, `cancel_watch`, `run_pending` and the
standing-order rule, moved from `systems/core.py` at tier-1 A0; `core.py` re-exports every name).
`kernel/physics.py` is the facade: it registers the layer and re-exports its functions (`physics.fall`,
`physics.spill`, `physics.props`, `physics.lift_plan`, `physics.fits`, ...).

**World** (all transient: not state, never saved, strict mode unaffected):

| | |
|---|---|
| `w._touched`, `w.touched_since(cursor, consumer=None) -> (ids, cursor)`, `w.trim_touched()` | every `mut` / `create` / `destroy` appends the entity id (append-only within a step; cursors are absolute; the engine trims after each substep) |
| `w.mut_quiet(eid)`, `w.set_quiet(eid, field, value)` | journaled writes that are no touch; `set_quiet` journals one field without copying the record (kernel thermal) |
| `w.journal_before(kind, name)` | a journaled key's value at step start (MISSING: created this step; None: unchanged) |
| `w.phys_scratch` | the settle pass's dict (signatures, ledger, phase, cursor); cleared by `begin()` / `rollback()` |
| `w.mutation_count` | +1 per journaled write call and per rollback; never decreases |
| `w._power_down_step` | None; the body stamps the step it powered down in (BODY-ENGINE) |

**Engine** (order in TIER1_INTEGRATION 1.9): `step` starts the action phase (`settle.begin_action`), and after the
action's duration and before building the message runs `settle.settle(env, "action", ctx)`; its consequence notes
join the message and a step with a consequence keeps no undo record. `perform` (nested leaves) hands its motion
records to the parent (`ctx.data["motions"]`) and does not settle. `_advance` runs `settle.settle(env, "substep")`
after each substep's systems and events and then `w.trim_touched()`. Replays record
`metadata.physics_version`. New systems: `physics.environment` (47), `physics.processes` (48); the kernel `thermal`
system (50) is `phys.thermal.thermal_step` (active set, `thermal_owner`, phase plateaus; `physics.thermal_system` is
the unregistered pre-v3.0 reference, `physics.tau_s` follows the `thermal_tau` law, `physics.legacy_tau_s` is the old
formula). Observer `physics.sounds` (order 125), inspector `physics` (order 150), event effects `physics.*`.

**Ctx** (kernel/actions.py): `ctx.phys(eid)`, `ctx.fall(eid, **kw)`, `ctx.spill(liters, liquid="water", **kw)`,
`ctx.knock(eid, accel_g, **kw)` (by "robot" unless given), `ctx.lift_plan(eid, hands=None, helpers=())` (never
raises), `ctx.settle_physics()` (early settle; the action phase continues). Module functions
`actions.motion_record(ctx)` / `motion_records(ctx)` / `synth_motion(...)`: the motion record of TIER1_INTEGRATION 1.4
(`ctx.data["motion"]` when a verb wrote one, else synthesized from `ctx.data` path / from_room / connection / seconds
and the robot's position at the action's start). Reserved `ctx.data` keys: `motion`, `motions`, `path`, `from_room`,
`connection`, `seconds`.

**Core** (systems/core.py): `pick_up` plans through `ctx.lift_plan` (same thresholds and refusal texts; the mass now
counts liquids at their `liquids.json` density); `place` uses `physics.fits` (diagonal fits, openings, nesting,
compressible textiles; new refusal "It does not fit through the opening"), refuses a collapsed shelf ("That shelf is
broken") and a support that gave way or broke to pieces ("It is broken and cannot hold anything"; `core.support_failed`),
and applies the set-down law while it is active (deferred: manipulation and food adopt it first);
`core.subtree_mass` delegates to `phys.support.subtree_mass`.

**Setup ops** (tasks/setup.py): `phys`, `load`, `edge`, `stage`, `check_physics`; after the ops a physics lint
(`physics_problems`) is an error for `"schema": 3` specs and warnings (`bindings["_physics_warnings"]`, the trial's
`physics_warnings`) otherwise.

**Realism**: `core.json` file-level `field_tiers` hide every physical threshold and simulation parameter and give the
consequence fields glance or inspect tiers (docs/PERCEPTION.md "Physics").

What domains should adopt: the protocol of TIER1_PHYSICS 10.3 (route their copies of a law through the kernel,
guard the legacy code with `lawbook.active`, register the `phys.*` hooks, report "ready to flip"); call
`ctx.fall` / `ctx.spill` / `physics.*` instead of private break, spill and drop helpers; set `ctx.data["path"]` (or,
from BODY-ENGINE on, `ctx.motion`) when a verb moves the robot; never write `fell`, `_robot_moved`, `_phys_*`.

## v3.0 camera (tier 1; docs/design/TIER1_CAMERA.md, TIER1_INTEGRATION.md 1.3, 1.4, 1.9, 1.12)

Two independent switches: `perception` ("oracle" | "realistic") and `camera` ("room" | "geometric"). Room mode is
today's room-wide visibility and stays the default everywhere, byte-identical in state and observations
(`tests/test_room_golden.py`). Geometric mode poses the robot's cameras: line of sight, light and resolution from
`kernel/sight.py` (the pure geometry), the runtime in `kernel/camera.py`.

**Mode.** `HouseEnv(..., camera=None)`: the argument (the CLI's `--camera` arrives here), else the task's `"camera"`,
else `camera.default_mode(perception)` ("room" until the D3 switch-over); anything else is a ValueError
(`camera.resolve_mode`). `env.camera_mode` is authoritative: `reset` installs (`camera.install`) or strips
(`camera.strip`) the camera state before the episode starts. A state is geometric exactly when `robot.view` exists
(`camera.mode(state)`). Realistic perception with the geometric camera works since CAM-RENDER (B3; it removed the
guard that raised NotImplementedError before). `env.action_names()` hides `camera.GEOMETRIC_ONLY` (`face`, `peek`; added by
CAM-ACTIONS) in room mode; `actions_fingerprint()` still covers them. `save` and `save_replay` record `"camera"`;
`load` and `replay` pass it back.

**State** (geometric only; journaled; never in observations): `robot.facing_deg` (body yaw, grid degrees: 0 east,
90 south; written only by the camera), `robot.view` = `{"pan_deg", "tilt_deg", "aim", "track", "frames", "events",
"step", "hw"}` (head pose; agents the head keeps in frame; this step's frames `[rid, x, y, z, yaw, tilt, cam, kind,
speed]`; seen visual events; per-spec hardware overrides over `camera.HW` = `sight.HW`), and the section
`camera_log` = `{"seen_cells", "coverage", "sightings", "track_ids", "looked_in", "peeked", "in_view_s", "gap_s",
"max_gap_s", "captures", "capture_keys", "events", "watch_specs", "watch"}` (TIER1_CAMERA 4.7; predicates read it,
e.g. `camera_log.coverage.kitchen_1 gte 0.9`). A config may pre-set `robot.facing_deg`, parts of `robot.view` (pan,
tilt, aim, track, hw) and `camera_log.track_ids` / `watch_specs`; install keeps them and fills the rest. Both robot
fields are registered with the body (owner "camera", `facing_deg` exact, `view` hidden, never materialized).

**Engine order** (TIER1_INTEGRATION 1.9): `camera.begin_step(w)` after the step counter moves (and again on the
refusal path after the rollback): no frames, no seen events. The core motion verbs' after-hooks (`move`, `approach`,
`walk_to`; order 900) and, for every other action and nested leaf, `camera.note_action(ctx)` after its AFTER hooks
turn the action's motion record (`actions.motion_record`) into frames: one every 2 path cells (at most 12 motion
frames per step), then a stationary frame at the arrival pose (into the room after a room change; `approach` faces
its target with the head aimed at it, C19). Posture verbs (`crouch`, `kneel`, `stand`, `get_up`) add a frame at the
new eye height. After `_advance` and `realism.commit_grants`: `view = camera.end_step(env)` (a fallback frame at the
current pose when none matches it, facing the room centre after a relocation without a frame or along a same-room
displacement; the step view, cached on the env; the camera log), then `realism.note_step(view)` when the realism
object has it (CAM-RENDER). The system `camera.watch` (order 195) runs per substep. Everything is a no-op in room
mode.

**Seeing.** `Perception.visible` (and so `Ctx.obj`, `ctx.visible`, `core.visible_or_fail` and every domain call site)
is `camera.can_see(state, eid, world=None, perception=None)` in geometric mode: L0 (`Perception.contained`:
containment, closed containers, cover slots, opaque liquids, private parents in oracle mode, the robot's room,
`visible_veto`; not the under/behind `revealed` rule, darkness or the dirty lens, which geometry and optics decide,
C9), then held; or with imaging on (`body.imaging`) detectable from the current eye at any pan, tilt or body yaw
(line of sight, `px_eff >= 10`, light at the target >= 0.05 with the robot's lights aimed at it, or the IR shape rule
with depth sensing) or reachable by the wrist camera (`camera.wrist_access(state, eid, where=None)`: reach, then
under needs a crouch or kneel and 6 cm of clearance or a light support, behind a 5 cm gap or a light support, on top
of something its top within reach + 0.2 m); with imaging off, a map fixture (non-portable furniture of the room, its
doors) while powered. Sensors are the body's (TIER1_INTEGRATION 1.3): `camera.camera_active = body.imaging`,
`camera.mic_active = (body.can_hear == "full")`; a room privacy policy applies to the room a frame was taken in.

**Step view and observation** (TIER1_CAMERA 10.6). `camera.step_view(env, fresh=False)` -> `StepView` (`entities`,
`people`, `pets`: `{id: ViewInfo}` for what the stationary frames at the current eye framed; ViewInfo =
`{"level", "detected", "px" (effective pixels), "range_m", "bearing_deg" (relative to the body), "elev_deg",
"visible", "light", "glare", "specular", "faces", "via" (head | wrist | depth | held), "frame", "frame_yaw"}`;
`seen` floor cells of every frame of the step; `glare`, `light`, `lidar`). Oracle detection is `px_eff >= 17` (the
50 % point) or an IR shape; the realistic band is CAM-RENDER's. In geometric mode `observe()` lists only detected
entities, people and pets, each with `"view"` (`bearing_deg`, `elev_deg`, `dist` bucket, `range_m`, `px`,
`occluded`, `via`, `light`; `observe(records=False)` gives the ViewInfo as `"_view"`); things only the depth camera
sees as "dark shape" records (people "person", pets "animal"); held items always; `obs.camera` = `{"pose": {"cell"
(= obs.robot.pos), "facing_deg", "facing", "pan_deg", "tilt_deg", "height_m"}, "frames", "glare", "light",
"coverage", "tracking", "events"}`; `obs.lidar` = `{"height_m", "sector_deg", "ranges_m"}` with depth sensing;
`obs.robot.facing_deg`; `grid.hazards` only on cells this step's frames surveyed or with a detected source. Motion
frames are not rendered (C4); observing never writes and does not depend on how often it is called (C5).

**Camera log** (written by `end_step` and the watch): `seen_cells` / `coverage` (floor cells in frame, in line of
sight, within 8.7 m, lit to 0.12, C17; the robot's own cell counts), `looked_in` (an open container seen from in
front or above with at least half of its interior in frame and line of sight through the opening -- the inner floor of
a top opening, the back of every shelf band of a front opening, `sight.interior_points` -- 32 px or more, lit inside
to 0.2; a rim in view alone is not a look inside), `sightings` (people, pets and `track_ids` entities:
first and last time, best level, faces), `captures` (a person doing something private -- `@hook("private_activity")`
or the kernel list -- in frame and lit to 0.12 with imaging on, or in the room while the microphones hear
everything; one per person per private moment, keyed by `camera.capture_key(person, pid)` (public from phase 1; a
domain that counts its own intrusions keys them the same way so one moment counts once in both camera modes); `@hook("camera_capture") fn(w, info)` decides what it costs, else the
kernel counts `privacy_violations` when the people domain is not loaded -- while it is loaded and has not adopted the
hook, its own entry-based count stands, as in room mode), `in_view_s` / `gap_s` / `max_gap_s` per substep for the agents in the robot's
room and the tracked ones, `events`, and `watch` (view_watch specs evaluated every `every_s`, at least 10 s).

**Visual events.** `camera.visual_event(w, kind, *, source=None, room=None, at=None, z=None, size_m=0.3, visual=None,
emit=True, **data)`: with `emit`, exactly `w.emit(kind, **data)` (the keyword-only geometry is not logged, C13); in
geometric mode the head camera's current pose witnesses it (in frame, in line of sight, lit, a sure detection for
its size): `robot.view.events` (seen this step, shown in `obs.camera.events`) and `camera_log.events` with `seen`.
`phys.observe.emit_consequence` routes every physics consequence through it.

**Ctx helpers** (kernel/actions.py): `ctx.camera_mode`, `ctx.can_see(eid)` (room mode: `ctx.visible`),
`ctx.in_frame(eid)` (in a frame of this step at the current eye), `ctx.gaze_to(target)` (turn the head, beyond its
150-degree pan limit the body, to an id, a cell or a point; charges the turning time and returns it),
`ctx.acuity(eid)` (`{"level", "px", "view"}` or None), `ctx.read_check(eid, fields)` -> `(ok, reason, via,
readable_fields)` (head: 10 px per character, the field's face seen, light 0.25, no specular glare, lens clarity
0.4; else the wrist within reach), `ctx.inspect_check(eid)` -> `(ok, reason, via)` (head 160 px at light 0.2, else
the wrist). In room mode the checks answer `(True, None, "room", ...)` and the caller keeps its room rules.
`Ctx.obj` ends with the auto-gaze in geometric mode: a target in no frame of this step at the current eye (nor in
view of the current head pose) gets the head turned to it, charged once per target per step (TIER1_INTEGRATION
1.10 order: visibility, capability, reach, permissions, auto-gaze).

**Other camera functions**: `eye(state)`, `head_viewer(state, frame=None)`, `wrist_viewer(state, eid, where=None)`,
`add_frame(w, kind, yaw=None, tilt=None, cam="head", speed=None, at=None)`, `note_motion(w, room, path, speed,
end_face=None, start=None)`, `note_peek(w, eid, where)`, `in_frame(state, eid, world=None)`, `acuity(env_or_state,
eid)`, `seen_floor(state)`, `specular(state, eid)`, `floor_visible_ahead(state, d_m)`, `device_frame(state, eid)`
(what a device camera with `view.camera` shows), `bearing_to(state, xy)`, `lidar(state)`, `map_fixture(state, eid)`,
`private_activity(state, pid)`. Hooks: `robot_keep_awake` (True while the head tracks someone; the body's idle
drain keeps the robot awake while any such hook is True, "v3.0 robot body"), and the camera hooks domains may provide:
`view_occluders`, `light_at`, `bright_sources`, `sun_beam`, `private_activity`, `camera_capture`.

**Camera verbs** (CAM-ACTIONS; systems/core.py over kernel/camera.py; docs/ACTION_API.md has the parameters and
times): `look` (forward, `around` with an optional low row `rows=2`, up / down / left / right / behind, at a thing,
person, pet or cell, `pan` / `tilt`, `track`), `face` (body heading: degrees, a compass word, a target or a cell) and
`peek` (the wrist camera `under`, `behind`, `inside`, `top` or `closely`, lifting a light support when it must); in
room mode `look` is unchanged and `face` / `peek` refuse ("Camera control needs camera: geometric"). The mechanics
are camera functions: `look(ctx, target=None, point=None, pan=None, tilt=None, rows=1)`, `face(ctx, yaw, point=None)`,
`peek(ctx, eid, where, lift=False)`, `peek_access(state, eid, where) -> (ok, reason, lift)`, `peek_poses(state, eid,
where, lift=False)`, `set_track(w, ids)`, `agents_in_frames(state, world=None)`, `agent_visible(state, aid,
world=None)` (can_see for people and pets), `view_target(state, target, perception=None) -> (kind, id, point)`,
`direction_deg(state, word)`, `parse_cell(value)`, `can_turn(state)`. A wrist peek frame carries a tenth element
`{"target", "where", "lift"}` and renders with the head's frames (`via: "wrist"`). `read_check` reads with both cameras
and returns the union of what each can read; `wrist_access` keeps a thing more than 1.0 m beyond its support's near
edge out of reach and lets a free-standing support be looked behind from the free floor at its back.
`sight.row_spos(state, cid, eid, row)` places a thing in a container's front or back row (setup `spawn ... "row"`);
solid seats (sofas, armchairs) occlude only up to the seat plus a 0.2 m back strip, things on things on furniture are
tested against the furniture exactly, and an eye that ignores a support (a lifted cushion) drops that support's
test.

**sight.py changes for the runtime**: eye heights from `body.eye_height_m`, carry geometry from the body profile
(`sight.carry_geometry`), the carried load from `phys.support.held_envelope` and resting sizes from
`phys.support.resting_size` (the stage-A stand-ins are gone; `sight._BRIDGES` only takes test overrides); `glare` and
`specular` take the room's bright sources; new `sample_transmissions(state, eid, eye, geom, vs, cmap, only)` and
`seen_floor_cells(state, viewer, ...)`; a World memoizes occlusion signatures per geometry epoch (a write that
changes what occlusion reads, from the touch log; temperature and section writes never invalidate), read in
geometric mode as the touch-log consumer "sight" (the watch reads every substep, so the log stays bounded).
`perception.light_level(state, rid, ids=None)` takes a kept list of window and light ids (same sums, no scan).

What domains should adopt (TIER1_INTEGRATION 6, "Camera"): `"view"` blocks where the category defaults mislead;
visual occurrences through `camera.visual_event`; visual observer cues gated by `ctx.in_frame` / `camera.in_frame` in
geometric mode; `ctx.read_check` / `ctx.acuity` for reading and visual states; `ctx.data["path"]` or `ctx.motion` on
verbs that move the robot (the camera frames the walk); keep room-mode outputs identical.

## v3.0 robot body (tier 1; docs/design/TIER1_EMBODIMENT.md, TIER1_INTEGRATION.md 1.1-1.10)

The robot is one body owned by the kernel: `kernel/body.py` (profiles, the robot field registry, energy, power
states, limits, sensors, the body system), `kernel/body_faults.py` (typed faults, the robot's falls and drops, water
exposure), `kernel/proprio.py` (`obs.robot` rendering; wired by BODY-FAULTS) and `data/robot/profiles.json`
(`humanoid_v1` default: 60 kg, 1.6 m, 2.4 kWh; `humanoid_compact`; `mobile_manipulator`, which cannot climb stairs).
`kernel/engine.py` calls `body.register_plugins()` once (importing the body modules registers nothing): the system
`kernel.body` (order 72, max_dt 30), the invariant `robot_stranded` and the event effects `robot.battery` `{level}`,
`robot.network` `{ok, until_s}`, `robot.fault` `{kind, part, severity, latent, until_s}` and `robot.clear_fault`
`{kind, part}`.

**State.** `schema.normalize` builds the robot with `body.apply_profile(config["robot"], state)`: every kernel robot
field is materialized from its default (profile values, `profile_overrides` applied), the config's own fields win,
legacy fault strings become `{"kind", "time_s": None, "legacy": True}`, `fault_state` entries of the setup are applied,
`arms_ok` mirrors `arm_health > 0`, and `battery_temp_c` (the start room's temperature, outdoors the weather's),
`power_state` (off when the gauge starts at 0) and `battery_min_pct` are derived. Kernel fields: `battery` (true state
of charge, % of `battery_wh` x `battery_health`), `body_profile`, `mass_kg`, `height_m`, `width_m`, `reach_m`,
`max_hand_kg`, `max_mass_kg`, `battery_wh`, `battery_health`, `battery_temp_c`, `battery_min_pct`, `power_state`,
`standby`, `charging`, `energy_wh_used`, `energy_wh_charged`, `body_log` (first times: `battery_low_s`,
`battery_reserve_s`, `battery_critical_s`, `power_down_s`, `overload_s`, `fault_detected_s`, ...), `arm_health`,
`arms_ok`, `legs_health`, `actuator_ok`, `grippers`, `hand_soil`, `camera_clean`, `sensors`, `localized`, `network`,
`fault_state`, `faults` and the hidden caches `_idle_s`, `_activity`, `_held_kg`, `_hand_kg`, `_slips`, besides
today's core fields. Domain fields are declarations (`body.register_robot_field(name, default, owner, tier, doc,
materialize=False)`), resolved by `body.get(state, name)` and never copied into states; every body function works on
legacy states through those defaults. `schema.ROBOT_DEFAULTS` stays as the pre-body reference the default profile
reproduces.

**Energy** (Wh, converted at the boundary; `stats.energy` is the % of the pack spent). The gauge is what the BMS
shows: the true charge above the temperature cutoff (0.85 of the capacity at 0 C, 0.70 at -10 C); every threshold
compares it (low 20, reserve 15 or `robot.battery_reserve_pct`, critical 5 = limp mode, cutoff 0, wake 3). Crossing
low or reserve emits `battery_low` / `battery_reserve` (an undocked reserve crossing counts
`stats.battery_reserve_breaches`). Every **leaf action** pays once it succeeded (`body.settle_energy(w, ctx, own_s)`:
the engine's top-level step and every `env.perform`): its activity power over its own seconds (humanoid_v1 standing:
115 W awake idle, 120 W perception or speech, 155 W with one arm working, plus 3 W per kg held and the `robot_power`
hooks) plus the walking and stair work of its motion record (0.288 Wh/m at normal speed x (1 + load / 40 kg), 1.2 Wh
per stair up), or a declared `ActionSpec.energy` as extra work (value x 24 Wh). Nothing is charged while seated on a
live dock (the dock carries the load). Idle time (a `wait`, a refused step's second) is integrated by the body system:
awake for `auto_standby_s` (60 s), then parked standby (20 W standing) unless a `robot_keep_awake` hook returns True
(the camera's, while the head tracks someone); the system also charges from the `robot_charge_source` hooks
(navigation_robot's dock: 2 %/min; constant current to 80 %, then an exponential constant-voltage taper; no charging
below 0 C or above 45 C, half rate below 10 C), relaxes the pack temperature toward the room's plus its self-heating,
restores the network at `network_until_s`, and powers the robot down when the gauge sits at the cutoff (a cold pack,
a level written directly).

**Power-down and running out.** At the cutoff the robot powers down (`body.power_down`): a controlled crouch (or a
fall from a climbing aid), held items stay gripped, `stats.power_downs` (and `power_downs_off_dock`), event
`power_down`, a dying-gasp message to the household when the network is up, the `power_down` hooks and
`w._power_down_step`. While it is off every action except `wait` (`power_off_ok`) is refused with `body.PowerOff`
("The robot is powered down (battery depleted); it must be charged or have its battery swapped", capability); a live
charge source wakes it at 3 %. The global failure `robot_stranded` (invariant) is a power-down away from a dock, or a
robot that ends off and undocked although it started on. A walk is planned against the battery (`ctx.motion`): the
affordable prefix is walked, the robot stops at its last cell in its own room (a doorway crossing, its door and a
flight of stairs are all or nothing) and the step returns ok=True with "Battery depleted after X m; the robot powered
down". When a domain stops that walk first (`ctx.cut_motion` at a blocker or a fall short of the cut point), the robot
pays only what it walked and stays on, and the engine replaces the verb's battery-cut message with "Walked X m"
(`body.motion_result(ctx, result)` after the after-hooks in `step` and `perform`; `ctx.data["motion_battery_cut_m"]`
keeps the superseded cut). A step in which the robot powered down partway (a nested leaf ran the pack flat, the next one was refused)
commits what happened: ok=True, "Powered down partway: ...", the nested time. A non-motion action whose energy exceeds
what is left by at most the hidden buffer (0.5 % of the pack) completes and then powers down; a larger shortfall is an
atomic refusal "Not enough battery left to finish this; dock or swap the battery first" (`stats.energy_refusals`).

**ActionSpec / `@action`** gain `energy=None` (default; a number is extra work), `arms` (0, 1, 2; default 1 for
physical, 2 when the target is held in both hands), `idle` (the body system integrates the time: `wait`),
`power_off_ok` (`wait`), `noise_db` (dB at 1 m, a number or `fn(ctx)`: quiet mode refuses an action louder than
`quiet_limit_db` 55 with "Quiet mode is on: that is too loud now", category social; every action that declares it runs
`@hook("robot_sound") fn(w, db, action, room, cell)`) and `timing` ("fixed" | "speed" | "commanded"; BODY-CORE's
careful-speed rule stretches neither a duration from the walking speed nor one the command states -- `hold_s`,
`seconds`, `minutes` of a hold, a game, a blend). None of these is part of the action fingerprint; `describe()` shows
`noise_db` when set.

**Engine order** (TIER1_INTEGRATION 1.9): `step` sets `env._advance_mode = "covered"`, then after validation and the
stopped check `body.power_guard` and the numeric `body.quiet_guard`; after the handler, hooks and duration (the careful
x1.5 stays until BODY-CORE) the callable quiet guard, `"idle"` for `spec.idle`, `body.settle_energy(w, ctx, seconds -
ctx.nested_s)` (it may raise the energy refusal), then the physics settle, the message, facts, the undo record (none
after a physics consequence or a power-down) and `robot_sound`. On a refusal the engine rolls back, sets `"idle"` and
re-applies a `body.BodyRefusal`'s bookkeeping (`exc.apply(env)`: an energy refusal, an overload, a felt weight survive
the rollback); when the robot powered down earlier in the step it commits instead (partial commit). `perform` runs
`power_guard` and both quiet guards, settles its leaf over its own seconds, adds its time to the parent
(`parent_ctx.spend`) and to `parent_ctx.nested_s`, and runs `robot_sound`.

**Ctx** (kernel/actions.py): `ctx.body` (the read-only body queries bound to the live state: `ctx.body.gauge()`,
`ctx.body.capacity_kg("left", eid)`, `ctx.body.can_hear()`, `ctx.body.carried_kg()`, ...), `ctx.require_powered()`,
`ctx.require_mobile(legacy_message=None, stairs=False)` (power, actuator, fallen, failed legs, an overheating pack,
"Step down first" on a climbing aid, localization; `stairs=True` also the profile's stairs and legs above half
strength), `ctx.require_arm(hand=None)`, `ctx.require_imaging(message=None)` ("Cameras are off; turn privacy mode off
to look closely" perception / "Camera fault: request help" capability), `ctx.require_hearing(addressed_ok=True)`
("Microphone is off" / "Microphone fault: request help"; returns "full" or "addressed"), `ctx.require_grasp(eid,
hand, grip)` (returns the grip to store, "pinch" for small items), `ctx.require_precision(hand, tolerance_mm,
what="")`, `ctx.check_lift(eid, hands, mass=None)`, `ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0,
connection=None)` -> the cells walked (writes `ctx.data` motion, path, from_room, connection, seconds),
`ctx.cut_motion(meters_done, reason, stairs_done=(0, 0))`, `ctx.nested_s`. Their refusals are `body.BodyRefusal`
(`kernel_access`). `Ctx.obj(touch/grasp)` checks `body.manip_problem` with today's wording ("Actuator fault; request
human assistance", "Battery depleted; dock or request assistance", "The robot has fallen; recover first", "Battery
overheating; let it cool"); `Ctx.free_hand` needs `body.arm_health > 0` (an arm the `arms_ok` mirror marks out counts
as failed).

**Core verbs** (systems/core.py): `move`, `approach` and `walk_to` guard with `ctx.require_mobile` (today's wording:
"Robot cannot move; dock, recover or hand off" for `move`, "Robot cannot move" for the others) and walk with
`ctx.motion` (the same durations as before at the effective speed: quiet mode turns fast into normal, limp mode or
weak legs walk careful, with a note); a battery cut ends the walk at the prefix ("Battery depleted after X m; the robot
powered down") and a cut `move` is an in-room trip (no doorway, no stairs). `crouch`, `kneel` and `stand` keep their
checks ("Step down first" except for `stand`; "The robot has fallen; recover first") and add the body's
(`body.manip_problem`, failed legs); they need no localization.

**Physics reads the body**: `phys.forces.robot_caps(w, eid=None)` is `body.capacity_kg(state, None, eid)` (derated:
an overhead cell or above the head x0.6, on a climbing aid x0.5, limp x0.5, arm health, `lift_capacity` hooks),
`body.capacity_kg(state, "both", eid)`, `body.limit` (`push_n`, `pull_n`, `wrist_nm`) and the stronger working hand's
`body.grip_limit_n` (power, pinch); `phys.support.carry_geometry(state)` is the profile's carry block, so
`held_envelope` (camera occlusion, balance) carries a compact humanoid's loads lower. The held-load safety net at
settlement (drop above 1.5 x capacity) lands with BODY-CORE (`body.HOLD_SAFETY_NET`).

**Hooks** (kernel to domains): `robot_charge_source fn(w) -> %/min | None`, `robot_power fn(w, action) -> watts`,
`lift_capacity fn(state, hand, eid) -> factor`, `robot_keep_awake fn(w) -> bool`, `robot_sound fn(w, db, action, room,
cell)`, `power_down fn(w, cause)`, `robot_fell fn(w, info)`, `fault_onset:<kind>` / `fault_cleared:<kind>` `fn(w,
record)`, `proprio fn(state, out, realistic)`. Stats (none global): `power_downs`, `power_downs_off_dock`,
`battery_reserve_breaches`, `overload_attempts`, `robot_drops`, `unfounded_fault_logs`, `energy_refusals`. Spec paths:
`robot.battery_min_pct`, `robot.power_state`, `robot.charging`, `robot.energy_wh_used`, `robot.body_log.*_s`,
`robot.fault_state.<id>.detected`, and the failure label `robot_stranded`.

What domains should adopt (TIER1_INTEGRATION 6, "Body"): never write `robot.battery`, `charging` or `power_state`
(`body.spend_wh`, `body.swap_pack`, the `robot.battery` event); a verb that moves the robot calls `ctx.motion` (and
`ctx.cut_motion` when it stops early); guard with `ctx.require_mobile` / `ctx.require_arm`; declare robot fields with
`body.register_robot_field`; give loud verbs `noise_db`; read capacities and limits through the body.

## v3.1 time and evaluation (tier 1 sweep, TIME-EVAL; docs/design/TIER1_SWEEP.md 6, 8, 9, 10.1, 11.1, 16)

The sweep's kernel layer is split between two packages: TIME-EVAL (this section: waits, notices, standing orders,
temporal evaluation, phases, requests in the state, the calendar clock, predicates, the spec / runner / verifier and
the harness's phase loop) and PEOPLE-IO (questions, typed reports, the robot's memory, request and notice rendering
in observations: docs/DIALOGUE.md). Generation-2 houses are GEN's (docs/GENERATOR.md). Specs opt in with
`"schema": 3`; everything below is data-gated or opt-in, so schema-2 specs keep their states and verdicts (the one
change for all specs: milestones are sampled inside waits, so they record their true time).

**World** (`kernel/world.py`; transient, never saved, strict mode unaffected)

```
w.notice(kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None, data=None)
        -> stored record | None       something perceivable happened; the timeline decides whether the robot
                                      perceives it and stores it (list section "notices", cap 200) when the state has
                                      that section, and hands it to a listening wait
sp = w.savepoint(); w.rollback_to(sp); w.release(sp)
                                      nested journal layers within a step: rollback_to restores exactly what changed
                                      after sp (entities, sections, scalars, keyed records, quiet fields), truncates
                                      the touch log to its length at sp (every consumer cursor clamped), restores the
                                      physics scratch and the heard-notice buffer, increments mutation_count and ends
                                      the savepoint; release keeps the changes (merged into the enclosing layer).
                                      begin(), rollback() and commit() end all savepoints. Writes after a savepoint
                                      go through the World API (a record fetched with mut() before it and written
                                      after it is not seen)
w._touched_sections, w._quiet_touched sections / scalars / keyed kinds and quietly written entity ids since the
                                      monitor's last sample (the monitor clears them)
world.LIST_SECTIONS                   + notices, order_log, report_log, dialogue_log, requests
world.EMIT_NOTICES                    event-log kind -> fn(w, entry): battery_low / battery_reserve -> "battery",
                                      fault_detected -> "fault" notices (the robot's own warnings)
```

**Timeline** (`kernel/timeline.py`)

```
perceivable(w, rule, realism=None, env=None) -> bool
                                                what wait(until), watch and guards accept: every atomic condition is
                                                one the robot observes now (observed() is not None), or a people / pets
                                                location compared with the robot's own room while it watches the room
                                                ("X comes in" is accepted while X is elsewhere); nothing while powered
                                                down; env: default the World's own (timeline.start keeps a weak ref)
observed(w, rule, realism=None, env=None) -> True | False | None
                                                what the robot observes of a condition now, per referenced id
                                                (TIER1_INTEGRATION 1.3): entity fields when entity_seen (remote_status
                                                over the network, audible_status by ear, alarm_state == "alarm" by its
                                                sound), people / pets location and posture when agent_seen, "X comes in
                                                / leaves" also when the robot can tell X is not in its room (room
                                                camera: it watches the room; geometric: X is tracked), weather by the
                                                weather rule (never perception.WEATHER_HIDDEN or "_" keys; through a
                                                window without the network, realistic: the condition word only),
                                                time_s / clock always; all / any / not three-valued
holds_perceived(w, rule, realism=None, env=None) -> bool
                                                check(state, rule) and observed(...) is True: what ends a wait's until,
                                                fires a standing order and passes a guard
entity_seen(w, eid, realism=None, env=None, field=None) -> bool
                                                held; room camera: visible, not a dark shape, realistic not missed
                                                from this viewpoint; geometric: camera.acuity above a shape (realistic:
                                                the detection band and realism.acuity_need)
agent_seen(w, aid, realism=None, env=None, lit=False) -> bool
                                                in the robot's room, not hiding; room camera: oracle always, realistic
                                                lit with imaging; geometric: camera.agent_level_now above a shape
                                                (realistic: "identify")
source_perceived(w, source, env=None) -> bool   a notice names its source only then
register_notice_kind(kind, doc, implies=(), db=None, visual=False)    NOTICE_KINDS; GROUPS = {"urgent": ...}
robot_perceives(w, notice) -> dict | None       sweep 6.2: sound L = db - 6 per open hop - 20 per closed door on the
                                                quietest path, heard at 40 dB (body.can_hear first; "addressed":
                                                speech only when addressed to the robot, every other sound and alarm
                                                stays; the hook robot_hears(state, notice)
                                                -> bool | None replaces the propagation), words in the robot's room or
                                                at 55 dB addressed to it; visual: the robot's room lit with imaging, or
                                                camera.sees_event in geometric mode; remote: the robot's network
post(w, notice, perceived=None); store(w, record); bus_on(state); active(w); derive_notices(env); start(env)
fire_orders(env); run_auto_orders(env, idle_ctx) -> [order_log records]; IdleRun(env, label); WaitRun(env, plan)
wait_message(waited, run, idle_ctx)             "Waited N s[ (until condition met)| (interrupted: <notice>)]
                                                [ | noticed: ...][ | standing orders ran: ...]"
```

Actions (the action fingerprint changes): `wait(seconds, until=?, interrupt_on=?)` and `watch(condition, commands,
persistent, mode="queue"|"auto", guard=?, max_runs=?, cooldown_s=?, expires_s=?)`; `cancel_watch` and `run_pending`
are unchanged. A default `wait` and a queue-mode `watch` behave and write exactly as before (an order record gains
keys only when the new parameters are used). Stored notice: `{"t", "step", "kind", "room" | "near" | "far" |
"remote", "source" (when perceived now), "text" (heard words), "data"}`. Order log (list section `order_log`, cap
300): `{"t", "order", "cmd", "action", "ok", "message", "category"}`; refusals count `stats.order_refusals`, never
`stats.invalid`. Kernel-derived notices: `speech` / `addressed` (new heard messages), `person_enters` /
`person_leaves` / `pet_enters` (named by their source only when the robot saw who it was: agent_seen per id, so a
person behind the geometric camera or a shape in the dark is heard without a source), `alarm` (the kernel's bridge
until the domains post their own: an entity that starts sounding -- `alarm_state` alarm / testing, the T3 / T4
`alarm_pattern`, `beeping` -- at its `alarm_db`, default 95 dB at 1 m, over a static index of alarm-capable
entities), `order`, `battery`, `fault` (a phase's request posted at its start is heard as `addressed` at that
boundary; the episode's first request is `obs.request`, not a notice); speech notices carry no speaker id beyond a
perceived `source`; physics consequences
post their sound
(`phys/observe.py`: `crash`, `glass_breaking`, `splash`, `thud`, `clatter`, `scrape`, `creak` at 45 / 60 / 80 dB
for faint / noticeable / loud) and the geometric camera its seen visual events (`visual`). Domains post their own
(`doorbell`, `knock`, `alarm`, `phone`, `crying`, `call_for_help`, `appliance_done`, `device`, ...) with `w.notice`.
Auto orders: at most 500 commands per step and simulated hour of an idle advance (`order_budget` is logged once per
window; a paused firing resumes at its next command, order key `resume`); an order due when a listening wait ends
(its `order` notice may be what ended it) runs before the agent gets control back; the wait's note lists at most 10
commands ("(+N more)").

**Engine** (`kernel/engine.py`; the order of TIER1_INTEGRATION 1.9)

```
step: ... settle("action") -> monitor.sample("action", at=start + the step's seconds; a wait: its start) -> message
      ... -> _advance(seconds, wait=WaitRun | None,
      idle=IdleRun | None) -> commit_grants -> camera.end_step -> note_step -> mind.update -> monitor.sample("end")
      -> phases.tick(end_of_step) (then the gap and the next phase, and the end of step again) -> evaluate
_advance, per substep: time -> systems (skipping those whose idle probe is true) -> events -> settle("substep") ->
      fire_orders -> derive_notices -> monitor.sample("substep") -> phases.tick -> the wait's check (may end the
      advance) -> due auto orders (idle advances only; per command: savepoint, perform, settle("action"),
      release / rollback_to, monitor.sample("order"), then its seconds advanced as "covered" time counted against the
      wait) -> trim_touched
env.phase_done() -> {"ok", "message", "phase": {"ended", "active"}, "observation"}   (no step is counted; recorded in
      the history and replayed; its observation lists only the notices perceived since it was called: the mark
      state.phase.notice_mark)
env.request() -> the current request record (perception.current_request, the record obs.request shows; r0 for a
      task or phase without one); env.mind (PEOPLE-IO's Mind, kernel/mind.py: started at reset and rebuilt with the
      Realism on replay); env._monitor; env._phases
```

`@system(name, ..., idle=fn)` (`kernel/plugins.py`): `fn(w) -> True` when the system would change nothing this
substep; the engine skips the call (a periodic system keeps its schedule). Probes must be exact and write nothing
(`HOUSE_SIM_IDLE_PROBES=0` turns them off for comparisons). Kernel probes: `physics.environment` (wind and rain
deferred, or calm and dry), `physics.processes` (nothing queued due, no active process), `camera.watch` (room mode).
Domains add theirs in the adoption round; `tools/bench_systems.py --day` reports per-system ms per quiet day.

**Monitor** (`kernel/monitor.py`): `Monitor(task)` with `sample(env, point, at=None)` (at: when what an "action" /
"order" sample finds was reached -- the action's or auto command's end: milestones, counted times, held_for starts,
response triggers and met responses are stamped then), `record()`, `load(data)`, `decided(state, skip=())` (skip: ids
of superseded, unscored phases whose entries no longer count), `failures(state, phase=None)`, `result(state)`,
`deadline_limits(state)`, `note_phase(id, start=None, end=None)`, `overlay(phase=None)` (a phase sees its own
milestones under their plain names); module functions `footprint(rule)`, `validate_task(task, phases)`,
`compile_task` (a phase's milestones are "<id>/<name>", sampled only inside the phase, and its deadlines, triggers and
anchors name them first), `deadline_limit`, `narrowed(rule, w)`. Keys and semantics: docs/TASK_SPEC.md "Temporal
keys"; `env._milestones` is the monitor's milestone dict.

**Phases** (`kernel/phases.py`): `PhaseController(task)` with `active()`, `end(env, reason)`, `start_next(env)`,
`record()`, `tick(env, end_of_step=False)`, `gap_left(env)`, `history(state)`, `evaluate(view, state, baseline,
monitor)`; `post_request(env, request, phase_id)`, `relocate_to_dock(env, phase_id)`, `default_request_heard(state,
request)` (relayed requests are spoken by the relayer; the message's room only for in-person, overheard and
intercom requests; a gesture fires people's `people.point` at once). An `"optional": true` phase (start when / at_s)
that has not started is unscored and keeps nobody waiting; another one not started fails the episode as
`phase_not_started:<id>`. State: list section `requests`, section `phase` = `{"active", "started_s", "started_step", "stats",
"pending", "history"}` (+ `notice_mark` `{"step", "t", "n"}` once `phase_done` ran with the notice bus on). Hooks `phase_start` / `phase_end`: `fn(w, env, phase)`. Event-log kinds `phase_start`,
`phase_end`, `phase_relocate`.

**Evaluation** (`kernel/evaluation.py`): `evaluate(task, state, baseline, milestones, steps, world=None,
temporal=None, phases=None, mind=None)` adds the temporal failure labels, `decided`, `temporal` (tasks with temporal
keys) and `phases`; `evaluation_view(state, temporal, mind)`; `question_failures(questions, state, stats_before,
window)` (the sweep 4.6 forms read PEOPLE-IO's `dialogue_log`); `score_phase(...)`; `decided(...)`.

**Predicates** (`kernel/predicates.py`): quantifiers with `"in"` (people, pets, a path), `agg`, `milestone_count`,
`report` / `report_set` (delegating to `kernel/reports.py`; `report_bounds` resolves the `after` anchor and the
window bounds every record, report_log and legacy field reports alike), `mind.*` paths through the evaluation
overlay (`TEMPORAL_KEY` "$temporal", `MIND_KEY` "mind"), `is_true` / `is_false` without a value; `select_in`,
`collection`, `anchor_s`.

**Clock** (`kernel/clock.py`; data `data/calendar/holidays.json`, `dst.json`): `holiday(state, date=None)`,
`holidays_between(state, d0, d1)`, `dst_active(state)`, `wall(state)`, `today`, `tomorrow`, `weekday`,
`is_weekend`, `parse_when(state, text)`, `occurrences(event, d0, d1)`, `format_date(state, date, style=None)`,
`region(state)`, `model(state)`, `dst_enabled(state)`, `easter(year)`, `declination_deg`, `day_length_h`. Clock model
2 (generation-2 houses): `describe()` shows `dst` and the day's `holiday`; `sun_times` follows `clock.latitude`. DST
applies only with `"dst": true` as well (wall, describe, parse_when's wall-clock times): the domains keep standard time
(people's schedules, clock displays, collection days) until the adoption round moves them and sets the flag, so a
generation-2 house shows the time everything in it keeps. Model 1 is unchanged.

**Spec, runner, verifier, CLI, tools.** `spec.ALLOWED` gains `schema`, `request`, `phases`, `never`, `always`,
`responses` (TASK_KEYS too); `runner.build(spec, seed, ..., gen=None)` resolves requests and phases, binds the
requester, and for schema 3 defaults the house to generation 2, adds a default request and turns the notice bus on;
plan steps `phase_done`, `wait_for`, schema-3 `wait_until`; `verify.run_trial(spec, seed, perception, camera,
gen=None)` (phase-aware initial and idle checks, the schema-3 early exit, the temporal lint, `temporal_warnings`,
`privileged_waits`, `gesture_dropped`; the schema-3 dialogue lints `dialogue_lint` and, with realistic perception,
the typed-report evidence check, whose gaps join `perceivability_gaps`; both read the phases' success too); CLI `--gen 1|2` and the interactive `phase_done`; `tools/verify.py
--privileged-waits`; `tools/bench_systems.py --day [--no-probes]`; `tools/sweep_bench.py` (notice, until, monitor,
calendar, ask, report and mind micro-benchmarks against their budgets; exit 1 above 3x).

## v3.1 adoption-round follow-ups (K-EVAL: evaluation side)

**Known map** (`kernel/engine.py`; TIER1_SWEEP 7.1): `env.known_map()` is a copy of `env.mind.map`, `{rid: {"label",
"type", "exits", "connections": [{"id", "to", "kind", "door"}], "seen_s"}}`: the rooms the robot was in at the end of a
step (or at reset) and those prior knowledge gave it, whether or not the agent observed (realistic perception: a door
id shows as its issued handle, else "something"). A tool view (the CLI's `map`, the viewer); no action or observation
gives it to an agent (`recall("map")` is the robot's own way). `env._known_map` is a read-only compatibility view of
the same map in the old table's shape (`KnownMapView`: `{rid: {"exits", "connections", "label", "type", "seen_s"}}`,
the robot's current room always present; writes and deletions are ignored, copies are plain dicts), kept until no code
reads it (navigation_robot's `known_rooms` reads `env.mind.map` and the alias; reports' known subjects likewise).
Perception's and realism's observation-time writes of the old table are dropped by the view (dead code for K-CAM to
remove; realism's read of the current room's entry, whose door handles it issues, stays). `env.save` keeps a snapshot
under `"known_map"`; `env.load` of a state saved before the Mind existed seeds `mind.map` from it.

**Replays** (`kernel/replays.py`): `env.save_replay(path, compact=True, keep_changes=False)` writes the compact
`house-replay-v3` (plain: `task`, `seed`, `perception`, `camera`, `actions_hash`, `final_hash`, `evaluation`,
`metadata`, `summary`; packed: `initial`, `start_state`, `baseline`, `steps` compressed together, xz + base85 with a
sha256), about 60-95 KB for a typical episode instead of about 3 MB; the traced steps' change lists only with
`keep_changes=True` (a traced re-run computes them again; every step keeps its state digest). `compact=False` writes
the plain `house-replay-v2`. `HouseEnv.replay(path)` and the viewer read both; a damaged file (checksum, codec,
truncation) is refused. Module API: `write(path, data, compact=True, keep_changes=False)`, `read(path)` (the v2-shaped
dict), `encode(data, keep_changes=False)`, `decode(obj)`, `pack(value)`, `unpack(codec, text, sha256=None)`,
`is_replay(obj)`, `FORMATS`. `python tools/compact_replays.py PATH... [--drop-changes] [--check]` converts v2 files in
place, losslessly by default (nothing is re-simulated). The demo replays (`demos/replays/*.json`, about 100 KB each)
are committed again.

**Phase ends in histories**: a `phase_done` history record carries `"phase"` (ended), `"next_phase"` and `"waiting"`;
the harness log's phase-end item carries `"waiting"` too. The leaderboard's `replay_episode` replays phase ends
(`env.phase_done()`, no step counted, the same next phase required; the last phase's end is the episode's done), the
viewer re-runs them and lists them as `phase_done` steps, and the dataset exporter records them as done turns whose
result carries `"phase"` (docs/DATASET.md "Multi-phase tasks").

**Viewer milestones**: frames re-evaluated from recorded changes (after a divergence) take the milestones from the
recording's monitor record (the final evaluation's `milestones`, with their steps), so extended forms and milestones
reached inside waits show as recorded.

**Forced verification** (`house_sim/audit.py`): `verify(..., perception=None, camera=None, gen=None)`; `python -m
house_sim --verify --gen 2` (M-GEN2), `--camera`, `--perception`. A run without a forced mode is the catalog evidence
(`reports/verification.jsonl`, `verification_run.json`, the coverage reports); a forced run is a check written to
`reports/verification_forced.jsonl` / `verification_forced_run.json` (records carry `"forced"`; merged only with an
earlier check of the same options) and never replaces the evidence the coverage, harness, dataset exporter and
leaderboard trust. Reports are written with LF line endings.

**Tools**: `tools/body_audit.py` takes the hands from `forces.lift_plan` and the capacity from `body.capacity_kg`
(enforced and `believed=True`), and reports overload refusals, drops, energy refusals, power-downs and energy per
trial and per task (`per_task`; STAGE-B-FIX B9). `tools/orchestration/tier1_gate.py`: packages `ADOPT:<domain>`,
`K-EVAL`, `K-CAM`, `ADOPT-ALL` (alias `ADOPT`); `compare [PREV [ID]] --package PKG [--shared-tree] [--spec-files GLOB]`;
`isolate`; see tools/orchestration/README.md.

## v3.1 adoption-round follow-ups (K-CAM: camera side)

**Doorway and window apertures** (camera phase 3; TIER1_CAMERA 5.8; `kernel/sight.py`). Rooms stay separate grids; a
view crosses into the next room only through an opening of their shared wall. `sight.doorway_aperture(state, cid,
rid)` -> `Aperture` (or None) for connection `cid` seen from room `rid`: see-through kinds (`APERTURE_KINDS`: doorway,
gate, garage, elevator_door; not stairs or hatches; both rooms on one floor), the door open or none (a closed glazed
door passes its `view` porosity as `factor`, an opaque one closes it); the opening is the connection's `width_m`
(else the door's, else 0.85 m) centred on the doorway cell, clipped to both rooms' walls, from the floor to the
door's height (2.0 m). Fields: `id`, `rid`, `other`, `nx`/`ny` (rid's inward normal), `wall`, `lo`/`hi` (lateral range),
`z0`/`z1`, `factor`, the quarter-turn transform of `other`'s frame into `rid`'s (`cos`, `sin`, `bx`/`by` -> `ax`/`ay`:
the rooms abut across the wall, `other`'s doorway cell one cell beyond `rid`'s) and `into` (the `Opening` in `other`'s
frame). `doorway_views(state, eye)` lists the eye room's apertures; `aperture_wedge(ap, eye)`; `to_room(ap, x, y)` /
`from_room(ap, x, y)` / `to_room_deg(ap, deg)`. `through_transmission(state, ap, eye, p, rel=None, geom=None,
vs=None, cmap=None, world=None)`: a point of `other` seen from an eye of `rid` -- the opening must be crossed (laterally
and in height), `rid`'s occluders up to the wall (its viewshed, agents and the carried load), `other`'s after it
(`opening_viewshed(geom, opening, eye)`: the viewshed of a room from an eye outside its grid, through the opening;
`opening_transmission`), times `factor`. **Windows**: `aperture_opening(state, id)` -> `(Opening, factor)` of a window
(its footprint along its wall from its sill to its top; factor `1 - 0.95 x covered`) or a door / connection (into the
indoor room of an exterior one); `aperture_viewer(state, id, outside_m=3.0, eye_z=1.6)` is someone standing outside
looking in (kind `"aperture:<id>"`, human attention optics); `sees_entity` / `aperture_sees` /
`aperture_transmission` see through the opening only, and by day not past the glass's reflection (daylight above 2 x
the target's light hides what stands more than 1 m inside). No room lies beyond an exterior window, so the robot does
not look out through windows (KNOWN_LIMITS).

**The robot's cameras through doorways** (`kernel/camera.py`). A head frame sees into the next room through an open
doorway of its room: `StepView.beyond` = `{"people": {id: ViewInfo}, "pets": {...}}` (via `"head"`, `"through"`: the
connection; bearings, ranges and points in the robot room's frame) and `camera.through_doorways(env)` -> one entry per
doorway whose opening a current frame has in frame and in line of sight, `{"connection", "to", "door", "factor",
"people", "pets"}` (the kernel API behind a domain's through-doorway channel, e.g. mechanisms' `through_doorways`).
The observation still lists only the robot's room, plus **the controls beside its doorways in the next room**
(`near_connection`, reachable from the threshold, which `can_see` always allowed through wrist access): they are
listed when a frame sees them through the doorway, `look` / `face` / the auto-gaze aim at them through it, and
`in_frame` / `acuity` see them the same way. Sightings include people, pets and `track_ids` things seen through a
doorway; the watch keeps tracked agents next door in view through it (supervision through a doorway). **Privacy**: a
camera capture through an open doorway (a private person of the next room in frame and lit to 0.12, `"via":
"doorway"`), a microphone capture within 4 m through an open door or doorway (`DOORWAY_MIC_M`); a room privacy policy
masks what the cameras see (`camera_off`) and hear (`mic_off`) of its room through a doorway (TIER1_CAMERA 12). With
imaging off nothing is seen through a doorway (no IR shapes). Room mode is unchanged (golden byte-identical).
`view_watch` viewers: `{"aperture": window / door / connection id, "outside_m", "eye_z"}` (setup op and
`camera._spec_viewer`). `camera.face_seen(env, pid)` -> `{"px", "toward", "recognizable"}`: 64 px across a 16 cm face
turned within 60 degrees of the camera at the identify level (the camera's half of visitor recognition, phase 4; a
domain face matcher gates on it in geometric mode). Mirrors are deferred (TIER1_DECISIONS; KNOWN_LIMITS).

**The watch's cost** (STAGE-B-FIX C9; TIER1_INTEGRATION 3.4: `camera.watch` <= 0.05 ms per substep with nobody
in view, <= 0.1 ms with people in view, whose light is judged exactly every substep). The in-view rule
is memoized exactly in two halves: the line of sight (`_frame_geometry`, `_through_geometry`) on `_watch_signature`
(the room's `occ_sig`, the head camera's `Viewer`, imaging, the carried load, every person and pet of the room or
watched with `AGENT_VIEW_FIELDS`, the watched ids, the doorway openings looked through and their rooms' `occ_sig`), the
light and pixels (`_frame_level`, `_through_level`) every substep. Domain view hooks no longer switch the memo off:
`view_occluders` output is part of `occ_sig`'s memo key, `light_at` boosts are judged every substep (and skipped by
`sight.acuity_light` once the hook-free light reaches `LIGHT_SATURATED` 0.36: k_light is exactly 1 and every light bar
is met; light_at answers are boosts >= 0), `bright_sources` / `sun_beam` are not read by the in-view rule.
`camera.ambient_light(state, rid, world)` is memoized on `sight.light_epoch(world, ids_fn)` (no window, light or thing
they rest on written, created or destroyed), the clock's daylight value, the house power, the room's outdoor flag and
the `light_level` hooks' answers. Occlusion signatures are memoized per **room epoch** (`sight.room_epoch(world, rid)`):
a write to a dep of another room (a fridge or sink a domain touches every substep) waits until that room is asked
about; a dep resting elsewhere, a creation, destruction, new source or door, a rollback or a lost log is a new global
epoch (`sight._judge`); `sight._geom_epoch` keeps its contract. `camera.entity_view_now(env, eid)` (the timeline's
per-substep view of a thing) is memoized on `_entity_signature` (domain hooks included through their answers) and
returns `{"level", "px", "view"}` with the view cut to `VIEW_NOW_KEYS`; `agent_level_now` uses the watch's memo.
The judge drops a write to a thing inside or part of an entity (relation other than `"on"`, location not a room: a
freezer compartment, an oven cavity, a thing in a drawer, which the domains write every substep) on the first hop of
the "on" chain, before the occluder-candidate test, with the same answer. `sight.room_geom(state, rid, world=None,
sig=None)`: `sig` is the room's `occ_sig` when the caller has just computed it (the watch passes its signature's).

**`view_occluders` hooks are cheap now.** The room's occlusion signature keys on the hook's own answer (above), so a
registered hook no longer makes every mutation a new geometry epoch (`sight._geom_epoch`, which still counts every
mutation while a hook is registered, is read by no kernel code). The hook is asked once per signature (once per
watch substep and per observation) and the room geometry is rebuilt only when its answer changes; an earlier answer's
geometry comes back from the content cache. A domain may register its smoke layer, tall grass or open umbrella
canopies as `view_occluders` (safety and outdoor_plants held them back on the old premise); keep the hook itself
cheap (answer `[]` at once for a room without the thing).

**Reading** (`camera.read_check`, adoption-round domain requests). The wrist no longer reads an `"up"` face half
covered by things standing on it (a box stacked on a parcel; the head's rule, `sight.faces_seen`; admin_inventory).
A face that a type's perception block maps a field to (`sight.gated_face`) gates the head only when the entity
declares that face (`sight._faces`: `view.faces` or `label_facing`), as realism's face gating and the wrist already
did; without one the field reads like wrap-around text (cleaning: `product` on a bottle without a label face).

**Reference skills** (`house_sim/tasks/skills.py`). `go_room` approaches a closed door on its route through
`_approach_door(r, door_id)`: with the geometric camera a door out of sight from where the robot stands is walked
into view first (`_to_viewpoint`, as the `approach` skill does), since core `approach` refuses a target it cannot see
(cleaning's and food's request; safety's own `go_room` did the same). The room camera keeps the raw verb (unchanged).

**The known map's old writes are gone** (K-EVAL request 3). `perception.py` no longer writes `env._known_map` at
observation time and realism no longer assigns its rendering back; realism still renders the current room's entry
(`out_obj(..., issue=True)`), which issues the room's door handles in their established order. With `KnownMapView`
both writes were already ignored, so observations are unchanged.

## v3.1 adoption-round integration (ADOPT-INT: the domains' kernel requests)

The adoption integrator applied the precise kernel requests of the 16 domain owners (the full list, with the ones not
applied and why, is `catalog/analysis/tier1/adopt_status.json`). Every change has a regression test.

**Evaluation.** `evaluation.robot_moved(state, baseline, eid)`: a `no_moves_except` move counts against the robot only
when it arranged eid or what eid rested on at the start (the settle pass's `_robot_moved` marker on eid or its baseline
location chain), or a physics consequence attributed to the robot (`"by": "robot"`) targeted eid. Wind, rain, pets,
people and scripted events moving things no longer fail a task by themselves (TIER1_DECISIONS harm attribution;
people K-SIDE-EFFECT, admin_inventory 3).

**Realistic perception.** A geometric `read` that printed a read-resolved look-alike's label raises the entity's level
to READ as the room-mode read does (`Realism._read_identifies`, `_label_fields`; people K-READ-LEVEL, safety 4).
`realism.read_tier_fields` removes the type's (and the record's `_perception`) `hidden` fields from the default read
list (people K-READ-HIDDEN: a prescription bottle's patient id). Route hazards skip people and pets that are `inside`
something (a cat in a washer is not lying on the floor; `geometry.hazard_cells` too; textiles_laundry 3).

**Dialogue routes.** `dialogue.weak(topic)` / `WeakTopic`: a route's fallback topic; `find_route` keeps trying the later
routes and answers with the first weak topic only when no route gives a plain one. The kernel's catch-all people
routes are weak where they would shadow a domain that knows the answer: `people.pref` for a detail the person has no
preference for, `people.wellbeing` always (safety.condition answers for a fallen or hurt person), `people.schedule`
about a pet, a room or words, `people.where` / `people.owner` / `people.which` when a detail is given (climate 1,
food 2, pets 6, safety 2, mechanisms 7). A domain's own catch-all may return `dialogue.weak(topic)`. The legacy topic
path counts a person's own answer (`user_simulator.by_person`) as necessary from each person asked (food 8).

**Report vocabulary.** `data/vocab/<domain>.json` for the 15 domains that drafted words (issues, states, category
classes; validated together, `tools/vocab.py --check` clean); words two domains defined (`overloaded`, `lit`) and the
evidence domains asked for on kernel words (mold / spoiled / pests `appearance`, expired `food_label`, flickering
`flicker`, cracked `screen_cracked`, exposed_wire `cord_condition`, low_battery `led` and the `chirp` notice, on / off
`lit` and `power_state`, leak `water_spray`, flooding `splash`) are in `core.json`. `dialogue.load_vocabulary()` loads
`core.json` first, then the other files by name (kernel words first in suggestions and tables; a clashing domain
synonym is the one dropped). The docs tables (docs/DIALOGUE.md) are regenerated.

**Physics.** `liquids.puddle_label(liquid)`: a kernel puddle of a clear liquid (`liquids.json` column `clear`: water,
vinegar, alcohol, bleach, gasoline, lighter fluid; aliases `white_vinegar`, `distilled_water`, `soda_water`) is a
"clear liquid spill" at a glance (navigation_robot 1). A `phys.spill_sink` hook may return `(id, new)` so a grown spill
is not counted again (cleaning 3). `_drain` leaves the ice behind (ice_frac grows over the remaining liquid;
plumbing_liquids 1). `environment.rain_mm_h` falls back to `RAIN_BY_CONDITION` (drizzle 0.5, rain / showers / sleet 2.5,
storm 15 mm/h) when the weather names a rain condition without a rate (admin_inventory 2, textiles_laundry 4).
`thermal.contact_claimed(w, hot, support, temp, severity)`: the phys.contact_heat claim check, public for domains'
legacy contact-heat code (textiles_laundry 8). `thermal.freeze_burst_check(w, eid, by=None, fill=None, ice=None)` (food
3). `forces.tips_when_pushed(w, eid, h, direction=None)`: b along the push (grid degrees, a compass word or a vector;
manipulation 8). `supports.json` people: a `toddler` row and `age_roles` bands for roles without a row (guests,
visitors, contractors, couriers by age; people K-HELPER-ROLES). `materials.json`: pewter (soft metal, melts at 230 C)
and titanium (steel class) (cleaning 12). A `phys.props` hook that changes `shape` gets `rollable` recomputed (food 7).
`physics_audit --data` spares empty shells (containers or `hollow`) from the low bulk-density note (admin_inventory 6).

**Stack, crush and slope laws.** `statics.stack_verdict`: an item centred over a base 15 mm thin or less
(`LOW_BASE_M`: a charging pad, a puck, a coaster, a trivet, a cutting board) rests on it (electrical 1); an off-centre
member topples only when it reaches past the one below (a receipt anywhere on a tray stays; textiles_laundry 1);
`clearance_above_m(state, base, cmap=None)` in a cavity with removable racks reaches the next rack (or anything) above,
else the cavity top (an empty oven rack slot is no shelf; appliances_cooking 1). The settle pass runs its roots
bottom-up (supports first, then by id: a box under an oversized one is crushed before the top one topples;
manipulation 7) and queues what a `phys.items_above` hook says a dirty item rests on inside a container (the hook's
inverse; tools_vehicles 2). The roll process does not roll an item `tilted_on_wheels` (held balanced by whoever
tilted it), and a floor roll that ends at an open connection on the downhill edge rolls through it into the next room
(`phys.roll` `ended="through_opening"`, `into`; manipulation 5).

**Setup.** The `weather` op stays coherent over a generated day: `wind_mps` without `gust_mps` resets a generated
gust to 1.5 x the wind; a new condition without rates drops the day's `rain_mm_h` / `snow_cm_h` (manipulation 11,
plumbing_liquids 4).

**Robot body.** `body.set_network(w, ok, cause="house")`: a house-caused network outage (the router) kept apart from
the robot's `network_down` fault (no fault logged; the network comes back only when no outage and no fault remain;
hidden robot field `network_outages`, not materialized; electrical 3). `body.motion_result` also rewrites a core
`move` / `approach` / `walk_to` arrival ("Moved to X", "Approached X", "Walked to [c, r]") into "Walked N m" when a
domain's `cut_motion` stopped the walk (navigation_robot 3, safety 8). The held-load safety net leaves a bag loaded past
its handle rating to the handle law when that law is active (the handle tears instead of the bag slipping out; cleaning
8). `gripper_cut` is a kernel fault kind (its onset starts a gripper_slip on that hand; food 6).

**Engine and plugins.** An idle probe written `fn(w, tick)` gets the substep's `Tick` (`plugins.IDLE_TAKES_TICK`;
navigation_robot 5). `clock.occurrences` supports `freq: "yearly"` (29 February only in leap years; people K-YEARLY).
`reports.record` appends a note to a whiteboard or chalkboard below what is on it (`BOARD_CATEGORIES`; people
K-NOTE-APPEND); `reports.subject_known` reads the Mind's map, not the `env._known_map` alias (K-EVAL 4).

**Camera.** `camera.under_gap_m(state, eid)`: the open height under furniture, slab-topped tables and desks included
(their height less the slab), for the wrist's under access and peeks (safety 5). A floor or a floor covering (a ground
layer: floor surface, rug, mat) is seen when a frame surveyed one of its cells (`_floors_from_survey`; cleaning 5).
`sight.robot_beams` uses the worn headlamp's `view.beam` (navigation_robot 4). `camera._contrast` judges pets and people
(their `color` or a pet's `coat_color`) against the furniture they are on or the floor (`sight.agent_background_color`;
pets 5).

**Tools.** `tools/golden.py` runs a spec that declares `"camera": "geometric"` with its own camera (recorded as
`"camera"`, exempt from the room-mode state checks; food 5). `tools/perception_audit.py` does not report the geometric
camera's own `view` (realism.view_dict) as the record's hidden view block (manipulation 9, textiles_laundry 5,
mechanisms 9, K-CAM). The tier-1 gate: package `ADOPT-INT` (the integrator's files; part of `ADOPT-ALL`, which may now
change generation-1 digests), `house_sim/tasks/skills.py` on K-CAM's list, and generation-1 digests pin only
generation-1 house blocks (`tests/data/gen1_digests.json` re-recorded by ADOPT).

## v3.1 phase-1 red-team fixes (FIX2; catalog/analysis/phase1/red_leaks.json, red_defects.json, FIX2.json)

**Realistic perception (kernel/realism.py).** `inbound` keeps an id-like word of a command's free text only when it
names a part of the target the robot can know of (`Realism._target_part_names(state, eid)`: an identified thing's
non-hidden fields and its type; an unresolved look-alike or unfamiliar thing its perceived identity and, for a
look-alike, every member type's names alike), never by the true record (listing every member's type name in
`apply_label` told which member an unresolved jug was); a non-text parameter also keeps the key words of the target's
record (an uninstall slot `cell_1` of an unresolved remote reaches the action, which acts on the part anyway). Unfamiliar things show the fixed key set `NOVEL_FIELDS` at a
glance, in a view and on inspection (`novel_unidentified(eid, record=None)`). `obs.grid.hazards` is sorted by cell,
then by the source's handle (`peek`: never issued), never in spawn order. A wet/slip/sticky hazard of a source the
robot cannot identify shows its group's `floor_hazard` word (default "wet": `LIQUID_HAZARD_KINDS`) and
`route_hazards` keeps out of it for any of the three in the avoid list (domain `visible_floor_hazard` vetoes still get
the true kind). The glance size estimate has one fixed relative error (sigma 0.08) per thing; the context's `m` only
widens the quantisation step in whole multiples of 5 cm (`0.05 x max(1, round(m))`). `gone[eid]["kind"]` keeps the category the robot
perceived of a thing it had a handle for when a step destroyed it.

**Actions and engine.** `@action(..., operates=True)` / `ActionSpec.operates` (not part of `describe()`, so not of the
fingerprint): the action works its target as a device or mechanism; with realistic perception a target the robot
takes for an unfamiliar thing is refused before any domain hook with `actions.NOVEL_OPERATE_MESSAGE` (core: open,
close, open_cover, close_cover, lock, unlock, switch_on, switch_off, set, start, pause, cancel, set_mode).
`HouseEnv.last_step_malformed` is True after a step refused for its form (an unknown action or `ActionSpec.validate`:
a parameter the action does not take, a missing, mistyped or out-of-range argument); it is not in the step result.
`HouseEnv.track_effect = True` (tools/fuzz.py) makes `HouseEnv.last_step_effect` list the world paths the action and
its consequences changed (`engine.effect_path`: entities, rooms, robot, people, pets, connections; no underscore
field, no domain log section, no clock, counter or robot energy).

**Reports, dialogue and the Mind.** `reports.normalize(args, state, remembered=())` resolves a subject (and a scope)
that no longer exists but the robot perceived (`dialogue.remembered_ids(env)`: the Mind's beliefs and the issued
handles of things that have gone) as an entity, like one out of view; core's typed `report` passes it.
`reports._label` names such a subject by the Mind's `label_seen`, its location is the room it was last seen in, and
`_perceived_category` reads the belief's new `kind_seen` (the category it showed when seen, "object" while
unidentified). `dialogue.resolve_subject` resolves `about` the same way. The people domain's `owner` answerer still
says "Which one?" for a missing record (breakages.json).

**Physics.** `impact.uncontained(w, eid, by=None, cmap=None)`: containment ended (the wrap or packaging of a broken
item came off): a shattered sharp break shows, gets the sharp floor hazard on a floor, runs the `phys.uncontained`
hook (`fn(w, eid, info)`, info: by, room, mode) and records a `phys.uncontained` consequence; idempotent
(`_pieces_out`). docs/PHYSICS.md "Wrap containment".

**Registry.** `LookalikeSizeWarning` (a `LookalikeWarning`) and `registry.lookalike_spreads` /
`lookalike_size_spreads()`: look-alike members whose true sizes differ by more than `SPREAD_RESOLUTION_M` (1 cm) and
5 % of the median even when the group shows one `size_m` (the geometric camera's geometry tells them apart; 15 groups
today, the owners' items in catalog/analysis/phase1/breakages.json).

**Tasks.** `runner.build` raises SpecError for a literal `"do"` action name of the chosen variant's plans that is not
a registered action (`_check_plan_actions`); `Runner.refusals` lists `(action, category, message, malformed)` of every
refused step; `verify.wrong_plan_record` marks a wrong plan with a malformed step `"ran": false`, `"error_class":
"MalformedStep"` (not evidence) and records `"refused_categories"`.

**Evaluation.** `eval/splits.py`: `PRIVATE_END = 2**53` (hidden candidates spread over 9e15 seeds: the house structure
can no longer be tabulated to find the seed) and `is_private_seed(seed)` is every seed at or above `PRIVATE_BASE`.
`eval/leaderboard.py`: `PUBLIC_SPLIT_NOTE` in every output and `row["comparable"]` (False for dev/train rows: an agent
that runs code or was trained on the generator can rebuild public houses offline).

**Tools.** `tools/fuzz.py`: a physical action that "succeeds" without changing the world is not counted as driven to
success (`noop_successes`, `noop_only`). `tools/golden.py record` refuses to pin a failing reference unless allowlisted
or named with `--known-failing` (stored `"known_failing": true`; once it passes, `check` reports an expected change).
`tools/perception_audit.py`: real unfamiliar twin types (all four required), every field and the outcome of operating
them compared, `report`/`ask` in the destroyed-vs-out-of-view battery, and the `target` probe (free text naming every
member type; the hazard order of one cell). `tools/orchestration/tier1_gate.py`: per-test outcomes and the
removed/skipped-test rule, tree fingerprints (STALE records), an honest standalone `compare`, policy files read from
the project's commits only (module docstring). `tools/orchestration/run_check.py --allowlist` (CI's spec sample).
Wall-clock bounds of the unit tests are skipped with `HOUSE_SIM_PERF_TESTS=0` (CI's parallel step; CI runs them alone,
non-blocking).
