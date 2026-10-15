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

* **Performance APIs** — `w.static_index(name, predicate)` returns sorted ids of entities matching a predicate over
  *static* fields (type, is_a, category, fixed tags); cached until any entity is created or destroyed. `w.of_type(t)`
  is the type/is_a version. Re-check dynamic fields on the records you get back. Systems must not scan all
  entities every tick.
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
  `state["action_log"]` ({step, time_s, action, target, ok}) supports process-order predicates. `wait` accepts up to
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
  estimate that must not be redrawn on every step (`ctx.rng` is seeded per step and action and averages out).
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
(`camera.mode(state)`). Realistic perception with the geometric camera raises NotImplementedError until CAM-RENDER
(TIER1-STUB(CAM-RENDER) in `engine.py`). `env.action_names()` hides `camera.GEOMETRIC_ONLY` (`face`, `peek`; added by
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
everything; one per person per private moment; `@hook("camera_capture") fn(w, info)` decides what it costs, else the
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
