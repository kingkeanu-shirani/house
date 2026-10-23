# Tier 1 integration plan: physics, camera, embodiment and the sweep

Status: binding plan for implementers (read-only design round, 2026-10-13). Nothing here is implemented yet.

Inputs, read in full: `docs/design/TIER1_PHYSICS.md`, `TIER1_CAMERA.md`, `TIER1_EMBODIMENT.md`, `TIER1_SWEEP.md`
(it appeared while this plan was being written and is reconciled below), the decisions in `TIER1_DECISIONS.md`, the
sweep analysis `catalog/analysis/tier1/sweep.json`, and the current code they touch (`kernel/engine.py`,
`actions.py`, `world.py`, `perception.py`, `realism.py`, `schema.py`, `registry.py`, `physics.py`, `plugins.py`,
`predicates.py`, `evaluation.py`, `systems/core.py`, `tasks/setup.py`, `skills.py`, `spec.py`, `runner.py`,
`verify.py`, `cli.py`, `generator/`, `tools/`, `setup.py`, `audit.py`).

**Authority.** TIER1_DECISIONS.md beats everything. This document beats the four layer specs wherever layers meet:
shared fields, hooks, names, engine order, file ownership and gates. The layer specs stay authoritative for
everything inside one layer: formulas, tables, anchors and tests. Every place where this document changes a layer
spec is listed in the conflict register (section 1.17) with the reason. Package ids: physics, camera and body
packages are named here; TIME-EVAL, PEOPLE-IO and GEN are the packages of TIER1_SWEEP.md section 22, with the file
reconciliation of section 2.5.

Contents: 1 cross-layer contracts, 2 work packages, 3 gate, 4 geometric-camera switch-over, 5 handoff protocol,
6 domain follow-ups, 7 decisions the user may revisit and risks.

---------------------------------------------------------------------------------------------------------------------

## 1. Cross-layer contracts

### 1.1 Who owns which robot field

"Owner" is the only code that writes the field. Everyone else reads it. "Materialized" means `schema.normalize`
writes the field into every state (from BODY-ENGINE on). Declared fields are registered in the body field registry
(`body.register_robot_field`) but appear in a state only once something writes them, so room-mode and legacy states
stay small. Proprioception tiers are the embodiment spec's (section 4.14); unregistered robot fields are hidden in
realistic mode from BODY-FAULTS on.

| Field(s) | Owner (writer) | Main readers | Materialized | Proprio tier |
|---|---|---|---|---|
| `battery`, `battery_wh`, `battery_health`, `battery_temp_c`, `battery_min_pct`, `power_state`, `standby`, `charging`, `energy_wh_used`, `energy_wh_charged`, `body_log`, `_idle_s`, `_activity`, `_held_kg`, `_slips` | body (`kernel/body.py`) | engine, core, navigation, proprio | yes | per embodiment 3.2 |
| `dock_powered`, `docked`, `home`, `dock`, `stuck`, `nav_*`, `lens_scratch`, `battery_reserve_pct` | navigation_robot (declared) | body charge source, proprio | no | per embodiment 3.3 |
| `posture` | core posture verbs; navigation (`get_up`, `climb_on`); body (`power_down` writes `"crouching"`, `body_faults.fall` writes `"lying"`) | camera (eye height), body (stance power) | existing | exact |
| `fallen`, `standing_on` | `body_faults.fall` / navigation `robot_falls`, `climb_on`, `step_down` | camera, body, core guards | existing | exact |
| `facing_deg` (body yaw, grid convention) | camera only: `camera.install`, `face`, `camera.note_motion`, the end of `approach`, `camera.watch` idle yaw, the `end_step` fallback | manipulation `world_pose`, sight, (after adoption) people and navigation | no: geometric mode only | exact |
| `view` (pan, tilt, aim, track, frames, events, step, hw) | camera only | camera | no: geometric mode only | hidden |
| `camera_on`, `mic_on`, `privacy_policy` (sensor switches) | body: `body.set_sensors(w, camera=None, mic=None, policy=None)`, called by navigation `set_privacy_mode` and the setup `robot` op | `body.imaging`, `body.can_hear` (through them camera, perception, timeline, core) | no (declared; defaults True, True, None) | exact |
| `privacy_mode` | navigation `set_privacy_mode` | body | existing | exact |
| `sensors` {camera, depth, mics, speaker}, `arm_health`, `arms_ok` (mirror), `legs_health`, `actuator_ok`, `fault_state`, `faults`, `localized`, `network`, `network_until_s`, `water_exposure_s` | body / body_faults | everyone | yes (except the optional ones) | detected / exact / hidden |
| `camera_clean` | navigation `clean_camera`; `body_faults.degrade_camera` | perception, camera `k_lens`, body precision | existing | bucket |
| `hand_soil` | body field; domains write only through `body.soil_hand` / `body.clean_hand` | body precision and grip | yes | bucket |
| `mass_kg`, `height_m`, `width_m`, `reach_m`, `max_hand_kg`, `max_mass_kg`, `body_profile`, `profile_overrides`, `grippers` | body (profile values materialized; setups may override) | physics `forces.robot_caps`, camera (robot as occluder), core | yes | exact (overrides hidden) |
| `speed_mode`, `quiet` | core `set_speed` / `set_quiet` | everyone, through `body.effective_speed` (1.7) | existing | exact |
| `headlamp_on`, `hands`, `wearing`, `room`, `pos`, `stopped` | existing owners (navigation, core); TIME-EVAL's phase relocation may set `room`/`pos`/`docked` | all | existing | exact |

Capability numbers that are not robot fields (push, pull, wrist torque, grip forces, eye heights, carry geometry,
speeds, noise) live only in the body profile (1.2) and are read through body functions.

### 1.2 The body profile is the single source of robot numbers

`house_sim/data/robot/profiles.json` (BODY-MODEL) is the embodiment spec's file with these binding changes:

* **Forces.** `push_n` 220 and `wrist_nm` 6 stay. Add `pull_n` 180 (from the physics spec). The grip limits are the
  gripper table's `grip_n` 150 and `pinch_n` 30. Physics does **not** add `max_push_n`, `max_pull_n` or `max_grip_n`
  to `schema.ROBOT_DEFAULTS`. Manipulation's 90 N "firm" grip stays a *commanded* level, clamped to
  `body.grip_limit_n`; it is not the hardware limit.
* **Eye heights.** `camera_height_m` is replaced by `eye_height_m`, keyed by the body's posture keys:

  | profile | standing | crouching | kneeling | sitting | lying |
  |---|---|---|---|---|---|
  | humanoid_v1 (1.6 m) | 1.50 | 0.95 | 0.60 | 0.95 | 0.30 |
  | humanoid_compact (1.3 m) | 1.20 | 0.75 | 0.50 | 0.75 | 0.25 |
  | mobile_manipulator (1.4 m) | 1.30 | 0.90 | 0.90 | 0.90 | 0.30 |

  `body.eye_height_m(state)` = `eye_height_m[key] + aid.standing_height_m` when `standing_on` is set (the aid record
  field that navigation's `climb_on` already writes; 0.4 m if missing). `key` is `"lying"` when `fallen`, else the
  posture, else `"standing"`. The robot never uses a `"fallen"` posture value (navigation already writes
  `"lying"`). The kernel's "kneel" is the low look-under posture (torso pitched down), which is why kneeling is
  0.60 m and not an upright kneel. Camera's `HW.head.height_m` table is deleted; `camera.HW` keeps optics only
  (FOV, resolution, focal length, pan and tilt rates and limits, range).
* **Carry geometry.** Add `"carry": {"hand_z_m": 1.0, "hip_z_m": 0.85, "front_m": 0.35}` (compact 0.8 / 0.7 / 0.3;
  mobile manipulator 0.9 / 0.8 / 0.35). `hand_z_m` is the drop height of one-hand loads (physics handle law,
  `body_faults.drop_held`); `hip_z_m` and `front_m` place two-hand bulky loads (camera 5.6).
* Everything else in the embodiment profile stays as written, including 2.4 kWh, 0.288 Wh/m walking and IP54.

`forces.robot_caps(w, eid=None)` returns `{"max_hand_kg", "max_mass_kg", "push_n", "pull_n", "grip_n", "pinch_n",
"wrist_nm"}`. In stage A it reads robot fields with a local fallback table equal to humanoid_v1. From BODY-ENGINE on
it delegates: `max_hand_kg` = `body.capacity_kg(state, hand, eid)` (derated), `max_mass_kg` =
`body.capacity_kg(state, "both", eid)`, forces from `body.limit` and `body.grip_limit_n`. Physics never reads a
capability any other way.

### 1.3 Sensors, privacy and hearing (one model for all layers)

The body owns availability; the camera owns visual geometry; the timeline (TIME-EVAL) owns sound propagation.
Effective sensors are **derived on every call**, never stored:

```
body.imaging(state)   = power on and sensors.camera and get("camera_on") and not privacy_mode
                        and not policy_off(state, "camera")
body.depth_ok(state)  = power on and sensors.depth                      # depth camera and lidar ring
body.can_hear(state)  = "none"      if power off or not sensors.mics or not get("mic_on")
                        "addressed" if privacy_mode or policy_off(state, "mic")
                        "full"      otherwise
policy_off(state, s)  = privacy_policy names the robot's current room type under "<s>_off" and
                        (when == "always" or a person is in that room)
camera.camera_active(state) == body.imaging(state)      # camera.set_sensors is removed; body.set_sensors exists
camera.mic_active(state)    == (body.can_hear(state) == "full")   # ambient audio / recording
heard(notice)               = body.can_hear permits it  and  timeline propagation says it is audible (sweep 6.2)
```

Consequences, identical in both camera modes unless stated:

* **Imaging off, depth on** (privacy mode, camera off, camera fault): depth-only percepts. Entities of 0.4 m and up
  show as `"dark shape"` records (people as "person", pets as "animal"); no colour, text, labels or captures. Room
  mode applies today's darkness rule to the whole room. Geometric mode applies the camera spec's IR shape rule (size
  >= 0.4 m, within `HW.depth.ir_range_m` in the depth FOV, line of sight) **without** its light condition. Held
  items stay visible. Map fixtures (non-portable furniture, fixtures and doors in the room) stay targetable by
  actions (camera 9.3), but are listed only if the shape rule lists them. The lidar ring stays.
* **Power off:** only held items are visible; no lidar, no sounds, no messages, no notices. Every action except
  `wait` raises `PowerOff`.
* **Hearing.** `"none"`: `obs.messages`, `obs.sounds` and audio notices are empty; `listen` and `ask` are refused.
  `"addressed"` (privacy mode or a mic policy, per the user decision): only speech addressed to the robot
  (`to == "robot"` or `addressed_robot`) is heard, in `obs.messages`, `listen` and `addressed` notices; `ask` works
  (its answer is addressed speech); non-speech sounds and alarms stay; no mic captures. `"full"`: as today. The
  sweep's `robot_hears` hook may replace the propagation model, never the body's capability.
* **Visual notices** (sweep 6.2) need `body.imaging`; in geometric mode they also need the camera's event rule.
* **Privacy captures** (camera 12): a camera capture needs `camera_active`; a mic capture needs hearing `"full"`.
  Without a registered `camera_capture` hook the kernel counts each capture in `privacy_violations` (decision). In
  room mode the existing people/navigation entry-based captures are unchanged.
* **Refusal wording** is fixed (1.15).

### 1.4 The motion record (one record, three consumers)

Every robot motion produces one canonical record. Body energy, physics carry dynamics and camera motion frames all
read it; none of them re-derives motion on its own.

```
motion = {"path": [[c, r], ...],       # cells actually walked (a prefix when cut)
          "from_room": rid, "to_room": rid, "connection": cid | None,
          "meters": m, "planned_m": m, "seconds": s,
          "speed": "careful" | "normal" | "fast",       # the EFFECTIVE speed (1.7)
          "load_kg": kg, "stairs_up": n, "stairs_down": n,
          "cut": None | "battery" | "<domain reason>", "exhausts": bool}
```

* `actions.motion_record(ctx)` (PHYS-INT) returns `ctx.data["motion"]` when present, otherwise synthesizes the
  record from today's `ctx.data` keys (`path`, `from_room`, `connection`, `seconds`) and the robot's room and
  position before and after. `actions.motion_records(ctx)` returns the step's records: the action's own plus those
  of nested leaves (`ctx.data["motions"]`, appended by `engine.perform`).
* From BODY-ENGINE on, `ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None)` writes
  `ctx.data["motion"]` (it also keeps writing `ctx.data["path"]`, `["connection"]` and `["seconds"]` for domain
  code). `ctx.cut_motion(meters_done, reason, stairs_done=(0, 0))` updates it.
* Consumers: `body.settle_energy` (walking work and truncation), `settle.travel_of(env)` (physics carry law; legs
  from `motion_records`), `camera.note_motion` (frames every 2 path cells, at most 12 per step), navigation's
  `traverse` (calls `cut_motion` on early stops and falls; NAV-M2).
* Domain verbs that move the robot without core motion must call `ctx.motion` (after BODY-ENGINE) or at least set
  `ctx.data["path"]`; otherwise physics falls back to room/position change and camera to its end-of-step fallback.
* A relocation without motion (TIME-EVAL's phase `robot: "dock"`) writes no record. Camera's fallback treats a room
  change without a record as a relocation (faces toward the room centre, as `install` does) and a same-room change
  as a step (faces along the displacement).

### 1.5 Lifting, dropping and falling (physics x body)

* **Plan vs enforcement.** `forces.lift_plan(w, eid, hands=None, helpers=())` (physics) decides *how* an item would
  be lifted: hands needed (two hands if mass > derated hand capacity, largest footprint side > 0.7 m,
  `two_handed`, `hand=both` or `grip=two_hand`), the subtree mass, and team lifts with present, awake, consenting
  helpers. It **never raises**; it returns `{"ok", "hands", "mass_kg", "reason"}` with `reason` a code
  (`"over_capacity"`, `"hands_busy"`, `"arm_failed"`, `None`). `body.check_lift(w, eid, hands, mass)` (body)
  enforces: capacity with derates, total carry, overload statistics and event, the touch grant on `mass_kg`, latent
  weak-arm detection, and the refusal text. Core `pick_up` after BODY-CORE:

  ```
  record = ctx.obj(target, touch=True, grasp=True)
  ...portable / attached checks (unchanged)
  plan = ctx.lift_plan(target, hands=ctx.args.get("hand"))
  grip = ctx.require_grasp(target, hand, ctx.args.get("grip"))    # may return "pinch"
  ctx.check_lift(target, plan["hands"], plan["mass_kg"])
  check_handling(ctx, target) ...
  ```

  Between PHYS-INT and BODY-CORE, core raises today's messages from `plan["reason"]`. The physics wording "too heavy
  to lift alone" is dropped; today's texts and categories stay: "Object and contents exceed carrying capacity; get
  help or slide it" (safety), "Both hands must be free for this item" (capability), "No free hand; place something
  first" (capability).
* **Mass.** `support.subtree_mass(state, eid, cmap=None)` is the only implementation (liquid density from the
  record's `liquid_density`, else `liquids.json` by the liquid name, else 1.0). `core.subtree_mass` and
  `body.carried_kg` delegate to it.
* **Drops.** `body_faults.drop_held(w, eid, height_m, cause)` releases the hand(s), adds `stats.robot_drops`, then
  calls `impact.fall(w, eid, height_m=height_m, cause=cause, at=(robot room, robot cell), by="robot")`. Breakage,
  spills, floor damage, landing cell and the `phys.fell` / `phys.broke` hooks are physics'. The embodiment hook
  `item_dropped` is **removed**; domains that want extra drop effects register `phys.fell` or `phys.broke` and read
  `info["cause"]` (`"robot_drop"`, `"slip"`, `"overload"`, `"power_down"`, `"robot_fell"`, `"arm_failed"`).
* **Robot falls.** `body_faults.fall(w, cause, ...)` owns the robot's own fall (`fallen`, posture `"lying"`,
  `stats.falls` once, `robot_fell` event and hook) and drops held items through `drop_held` from
  `carry.hand_z_m + height_m`. Physics never writes `fallen` or `stats.falls`; the physics field for objects is
  `fell` and its statistic is `phys_falls`.
* **Attribution.** Robot drops and robot falls pass `by="robot"`: their breakages and spills count in the global
  flags (physics 6.4).
* **Standing load.** Physics reads an explicit `rated_kg` (navigation climbing aids) as the standing rating before
  any `supports.json` row; `body.total_mass_kg(w)` is the load.

### 1.6 Held envelope and the carried-load occluder

`support.held_envelope(state) -> {"items", "two_handed", "bottom_m", "top_m", "front_m", "width_m", "depth_m",
"height_m", "mass_kg", "com_m", "blocks_view"}` (PHYS-STATICS) is the one geometry of what the robot carries
(held items plus everything on or in them). Two-hand bulky loads sit at `carry.hip_z_m`, other loads at
`carry.hand_z_m - h/2`; the front is `carry.front_m + d/2`. In stage A the carry constants are local defaults equal
to humanoid_v1; BODY-ENGINE switches them to `body.profile(state)["carry"]`.

* `sight.carried_occluder(state)` is a thin adapter over it (camera 5.6 thresholds and the `carry_level` tray
  exclusion stay), so `camera.floor_visible_ahead` uses the same box. With the 1.50 m eye the camera spec's basket
  example becomes: floor hidden within `1.50 x 0.55 / (1.50 - 1.15) = 2.36 m` (not 3.7 m).
* `body.stability_margin` and navigation's stairs/slope derates read it. Core `held_width` is **unchanged** in tier 1
  (a change would alter doorway refusals); switching it is a later, separately verified item.

### 1.7 Effective speed

`body.effective_speed(state, requested) -> (mode, note)` is the only speed rule (quiet: fast becomes normal; limp
or weak legs: careful). Every speed consumer calls it with `ctx.args.get("speed") or robot.speed_mode`: core travel
time (from BODY-CORE), the motion record's `speed`, the physics carry factor and set-down drop, camera `k_motion`,
body noise and stopping distance. `robot.speed_mode` remains the user's setting. Before BODY-CORE no limp state
exists and no verified spec uses quiet mode, so physics and camera calling it from B1/B2 changes no outcome.

### 1.8 World additions

All live in `kernel/world.py`. None is part of the state, none is saved, and strict mode is unaffected. PHYS-INT
(B1) adds the first group in one go because the touch log and the mutation counter both hook `World._snap`;
TIME-EVAL (stage C) adds the second group.

| Addition | Package | Semantics |
|---|---|---|
| `w._touched` (list) + `w.touched_since(cursor) -> (ids, cursor)` + `w.trim_touched()` | PHYS-INT | `_snap` appends the entity id on **every** `mut`, `create` and `destroy` call (not only the first journaling). The log is **append-only within a step**: `begin()` and `rollback()` clear it; consumers never clear it but read through their own cursor (`settle` keeps its cursor in `phys_scratch`; TIME-EVAL's monitor keeps its own). The engine calls `trim_touched()` after each substep's last consumer, which drops the prefix every consumer has read (bounded memory on week-long waits). |
| `w.mut_quiet(eid)` | PHYS-INT | Journals like `mut` but does not append to `_touched`. Only kernel thermal temperature-only writes use it. |
| `w.phys_scratch` (dict) | PHYS-INT | Transient settle state (signature cache, consequence ledger, phase, cursor). Cleared by `begin()` and `rollback()`. |
| `w.mutation_count` (int) | PHYS-INT (for camera) | +1 on every journaled write call (`mut`, `mut_quiet`, `room_m`, `person_m`, `pet_m`, `conn_m`, `sec` and so `robot_m`, `set_scalar`, `set_path`, `create`, `destroy`, `add_person`, `add_pet`, `remove_person`, `notice`) and on every rollback. Never decreases. Camera occluder memos key on it. |
| `w._power_down_step` | PHYS-INT adds the slot, body writes it | `None`; `body.power_down` sets it to `state["steps"]`; `begin()` resets it; read by the engine's partial-commit rule. |
| `w.notice(...)`, `w.savepoint()`, `w.rollback_to(sp)`, `w.release(sp)`, `w._touched_sections`, new `LIST_SECTIONS` | TIME-EVAL | sweep 6.1, 6.4, 8.2 and 16.6. `rollback_to(sp)` also truncates `_touched` to its length at `sp`, clamps every consumer cursor to that length, drops `phys_scratch` signature-cache entries recorded after `sp`, and increments `mutation_count`. |

### 1.9 Order of work inside `engine.step`, `engine.perform`, `_advance` and `reset`

The tags say which package adds each line. Lines without a tag exist today.

```
reset():
    normalize (body fields materialized)                                                              [BODY-ENGINE]
    camera.install / strip by camera mode                                                             [CAM-RUNTIME]
    episode_start hooks; baseline
    Mind.from_config(config, env); mind.update(env, perception.perceived_ids(env))                   [TIME-EVAL call, PEOPLE-IO impl]
    request posted (people speech) when the spec has one                                              [TIME-EVAL]

step(action, target, destination, **kw):
    argument type check; strict copy; realism.begin_step()
    w.begin()                          # also clears _touched, phys_scratch, _power_down_step        [PHYS-INT]
    resolve run_pending; steps += 1
    camera.begin_step(w)               # frames = [], events = [], view.step = steps; no-op in room mode  [CAM-RUNTIME]
    self._advance_mode = "covered"                                                                     [BODY-ENGINE]
    try:
        spec lookup, realism.inbound, validate; stopped check
        body.power_guard(state, spec)  -> PowerOff unless spec.power_off_ok                           [BODY-ENGINE]
        body.quiet_guard(state, spec)  -> numeric noise_db over the quiet limit                       [BODY-ENGINE]
        ctx = Ctx(...); undo before_state
        BEFORE hooks; handler; AFTER hooks     # camera motion/posture after-hooks run here           [CAM-RUNTIME]
        scheduled pop
        seconds = duration (+ realistic whole-second rounding) + extra time
        careful factor: today's rule until BODY-CORE; then own seconds only, skipped for timing="speed" [BODY-CORE]
        body.quiet_guard(state, spec, ctx)     # callable noise_db                                    [BODY-ENGINE]
        if spec.idle: self._advance_mode = "idle"                                                     [BODY-ENGINE]
        body.settle_energy(w, ctx, own_s=seconds - ctx.nested_s)   # may raise the energy refusal     [BODY-ENGINE]
        consequences = settle.settle(self, "action", ctx)          # never raises; adds ctx notes      [PHYS-INT]
        monitor.sample(self, "action")                                                                [TIME-EVAL]
        message = _with_notes(result, ctx.notes)
        (legacy flat energy block removed)                                                            [BODY-ENGINE]
        noise stat; facts; undo record (dropped when consequences is non-empty)                       [PHYS-INT]
        run_hooks("robot_sound", w, db, action, room, cell) when spec.noise_db                        [BODY-ENGINE]
        ok = True
    except ActionError as exc:
        if w._power_down_step == state["steps"]:     # partial commit                                 [BODY-ENGINE]
            settle.settle(self, "action", ctx); ok = True
            message = "Powered down partway: " + str(exc); seconds = ctx.extra_time
        else:
            rollback; begin; steps += 1
            camera.begin_step(w)                                                                      [CAM-RUNTIME]
            self._advance_mode = "idle"                                                               [BODY-ENGINE]
            invalid / *_blocks stats; seconds = 1.0
    except Exception (SAFE only): as a refusal, category "internal"
    undo append; action_log
    self._advance(seconds)             # may end early for an interrupted wait; the step's seconds become the time waited
    realism.commit_grants(ok)
    view = camera.end_step(self)        # fallback frame, step view cache, camera_log, captures; None in room mode [CAM-RUNTIME]
    if realism and hasattr(realism, "note_step"): realism.note_step(view)   # camera tracked identity    [CAM-RUNTIME call]
    mind.update(self, perception.perceived_ids(self))                                                 [TIME-EVAL call, PEOPLE-IO impl]
    monitor.sample(self, "end")         # replaces _update_milestones                                 [TIME-EVAL]
    evaluate; changes; strict check; w.commit(); build the result

perform(command, parent_ctx):                      # nested leaves: undo, scheduled commands, domain chains, auto orders
    body.power_guard(state, spec)                                                                     [BODY-ENGINE]
    ctx = Ctx(...); BEFORE; handler; AFTER; seconds = duration
    body.settle_energy(w, ctx, own_s=seconds + ctx.extra_time - ctx.nested_s)                         [BODY-ENGINE]
    if parent_ctx: parent_ctx.spend(seconds + ctx.extra_time); parent_ctx.nested_s += same            [BODY-ENGINE]
    if parent_ctx: parent_ctx.data.setdefault("motions", []).extend(motion_records(ctx))              [PHYS-INT]
    # no physics settle, no camera begin/end: the enclosing step covers them (auto orders: rule 3)

_advance(seconds), per 30 s substep:
    time_s += dt
    systems in order (physics.environment 47, physics.processes 48, thermal 50, ..., kernel.body 72, ..., camera.watch 195;
                      idle probes skip provably idle systems)                                         [TIME-EVAL probes]
    _fire_events()                      # physics.* perturbations, robot.fault events, domain events
    settle.settle(self, "substep")                                                                    [PHYS-INT]
    _fire_orders()                      # queue mode as today; auto orders are marked due
    notices derived (speech, addressed, person_enters/leaves, pet_enters, order, battery, fault)      [TIME-EVAL]
    monitor.sample(self, "substep")                                                                   [TIME-EVAL]
    wait until / interrupt check -> may end the advance at this boundary                              [TIME-EVAL]
    auto-order execution (idle advances only), per command:                                           [TIME-EVAL]
        sp = w.savepoint(); self._advance_mode = "covered"; env.perform(cmd, idle_ctx)
        on success: settle.settle(self, "action", cmd_ctx); on refusal: w.rollback_to(sp), log
        the command's duration advances the world (systems keep running) and counts against the wait
    w.trim_touched()                                                                                  [PHYS-INT, extended by TIME-EVAL]
```

Rules that follow from this order:

1. **Energy before physics settle.** A refused job (energy shortfall) rolls back before any consequence is
   computed; a power-down, gripper slip or overload drop caused by settlement is settled in the same action phase,
   with action attribution and notes. `monitor.sample("action")` comes after both, so temporal predicates see the
   consequences.
2. **The partial-commit path also settles physics.** Nested leaves already paid their energy; the top level pays
   nothing more.
3. **Robot actions outside the action phase are settled like actions.** Substep settle runs after events and
   before orders, so an auto-order command starts with no unread touches from systems, and the
   `settle.settle(env, "action", cmd_ctx)` that follows each successful command marks only that command's changes
   `_robot_moved`. A refused command is rolled back to its savepoint, touches included. Any other robot action
   outside `engine.step`'s action phase (a phase relocation that moves entities) follows the same rule.
4. **Advance modes.** `"idle"` (a `wait`, a refused step's second, a phase gap) lets the body system integrate idle
   drain; `"covered"` means the energy was already settled (an ordinary action's duration, an auto-order command's
   duration). The sweep's word `"active"` is not used. `settle_energy` charges no time-based energy for `spec.idle`
   actions, so an interrupted wait pays exactly the substeps that ran.
5. **End of step.** `realism.commit_grants` first (the step's grants), then `camera.end_step` (the step view, so its
   cache matches the observation and the evaluation; camera C5/C6), then `realism.note_step(view)` (geometric
   tracked identity, CAM-RENDER), then `mind.update` with `perception.perceived_ids` (sweep 7.1: the same set
   `observe` renders, which depends on post-grant knowledge and, in geometric mode, on the cached step view), then
   the monitor's end sample, then evaluation. All run on refused steps too.
6. **Standby vs tracking.** The body's auto-standby is suppressed while any `robot_keep_awake` hook returns True.
   Camera registers one that is True while `robot.view.track` is non-empty. Auto orders wake the robot (their
   settlement resets `_idle_s`); perceivable `wait until` conditions and queued orders do not prevent standby.

### 1.10 Ctx helpers

| Helper | Package | Notes |
|---|---|---|
| `ctx.phys(eid)` -> `Phys` | PHYS-INT | |
| `ctx.fall(eid, **kw)`, `ctx.spill(liters, liquid="water", **kw)`, `ctx.knock(eid, accel_g, **kw)` | PHYS-INT | object consequences, `by="robot"`. `ctx.fall` is an **object** fall; the robot's own fall is `body_faults.fall` and has no ctx helper |
| `ctx.lift_plan(eid, hands=None, helpers=())` | PHYS-INT | never raises (1.5) |
| `ctx.settle_physics()` | PHYS-INT | early settle (renamed from the physics spec's `ctx.settle()`) |
| `actions.motion_record(ctx)`, `actions.motion_records(ctx)` | PHYS-INT | module functions (1.4) |
| `ctx.camera_mode`, `ctx.can_see(eid)`, `ctx.in_frame(eid)`, `ctx.gaze_to(target)`, `ctx.acuity(eid)`, `ctx.read_check(eid, fields)`, `ctx.inspect_check(eid)` | CAM-RUNTIME | |
| `ctx.body`, `ctx.require_powered()`, `ctx.require_mobile(legacy_message=None, stairs=False)`, `ctx.require_arm(hand=None)`, `ctx.require_imaging(message=None)`, `ctx.require_hearing(addressed_ok=True)`, `ctx.require_grasp(eid, hand, grip)`, `ctx.require_precision(hand, tolerance_mm, what="")`, `ctx.check_lift(eid, hands, mass=None)`, `ctx.motion(...)`, `ctx.cut_motion(...)`, `ctx.nested_s` | BODY-ENGINE | `require_hearing` is new (this document) |

`Ctx.obj` order (final): existence and visibility (`can_see` in geometric mode, CAM-RUNTIME) -> for touch/grasp,
`body.manip_problem` with today's wording (BODY-ENGINE) -> reach -> permissions -> auto-gaze (geometric, charges
`ctx.spend` once per target per step, CAM-RUNTIME). `Ctx.free_hand` uses `arm_health > 0` (BODY-ENGINE).
Reserved `ctx.data` keys: `motion`, `motions`, `path`, `from_room`, `connection`, `seconds`. The sweep adds no Ctx
helpers; its perceivability check is `timeline.perceivable(w, rule, realism)`, re-exported by `core.py` as
`watch_condition_visible`.

### 1.11 Names: hooks, events, notices, systems, sections, stats

Checked against the current registries and against each other for collisions.

| Kind | Physics | Camera | Body | Sweep |
|---|---|---|---|---|
| Hooks | `phys.props`, `phys.spill_sink`, `phys.wetted`, `phys.broke`, `phys.fell`, `phys.tipped`, `phys.collapsed`, `phys.contact_heat`, `phys.carry_motion`, `phys.tip_loads`, `phys.items_above`, `phys.exposed`, `phys.settle_extra` | `view_occluders`, `light_at`, `bright_sources`, `sun_beam`, `private_activity`, `camera_capture` | `robot_charge_source`, `robot_power`, `lift_capacity`, `robot_fell`, `power_down`, `fault_onset:<kind>`, `fault_cleared:<kind>`, `robot_sound`, `proprio`, `robot_keep_awake` (new) | `dialogue_channel`, `dialogue_comprehends`, `dialogue_render`, `dialogue_reliability`, `report_delivered`, `robot_hears`, `phase_start`, `phase_end`, `mind_seen` |
| Event effects (`event:<name>`) | `physics.bump`, `physics.nudge`, `physics.pull`, `physics.load_test`, `physics.wind_gust`, `physics.rain`, `physics.transport`, `physics.brake`, `physics.tilt`, `physics.drop` | - | `robot.fault`, `robot.clear_fault`, `robot.battery`, `robot.network` | phase gap events reuse existing effects |
| Event-log kinds | `phys.fall`, `phys.break`, `phys.chip`, `phys.crush`, `phys.collapse`, `phys.pullout`, `phys.tear`, `phys.tip`, `phys.slide`, `phys.roll`, `phys.spill`, `phys.overflow`, `phys.scorch`, `phys.melt`, `phys.ring`, `phys.shock`, `phys.burst`, `phys.blown`, `phys.scratch`, `phys.dent`, `phys.budget` | visual events keep their domain names (camera C13) | `battery_low`, `battery_reserve`, `power_down`, `robot_fell`, `overload`, `fault_onset`, `fault_detected`, `fault_logged`, `fault_cleared` | `standing_order_fired` (existing), `phase_relocate` |
| Notices (sweep 6.1) | glass breaking, crash, splash (from `phys.observe.emit_consequence`, stage C) | `visual` (each seen visual event) | `battery`, `fault` (from the `power_down` and `fault_onset` hooks) | `speech`, `addressed`, `person_enters`, `person_leaves`, `pet_enters`, `order`, plus domain kinds |
| Systems | `physics.environment` (47), `physics.processes` (48); kernel `thermal` (50) unchanged | `camera.watch` (195) | `kernel.body` (72) | idle probes on existing systems; navigation's `navigation_robot.watch` (190) and `.robot` (70) do not collide |
| Observers / inspectors | observer `physics.sounds` (channel `sounds`), inspector `physics` (`feel`, `looks`) | camera and lidar blocks are built inside `perception.observe` | `obs.robot` built by `proprio.render` inside `perception.observe` | `obs.notices`, `obs.request` built inside `perception.observe` |
| Invariants | - | - | `robot_stranded` (replaces `navigation_robot.stranded`, NAV-M2) | - |
| State sections | `physics` (lazy: queue, active) | `camera_log` (geometric only) | none (robot fields) | `notices`, `order_log`, `report_log`, `dialogue_log`, `dialogue`, `requests`, `phase` |
| Stats | `phys_falls`, `phys_breaks`, `phys_spills`, `phys_tips`, `phys_collapses`, `phys_blown`, `phys_rolls`, `phys_budget_hits` | - | `power_downs`, `power_downs_off_dock`, `battery_reserve_breaches`, `overload_attempts`, `robot_drops`, `unfounded_fault_logs`, `energy_refusals` | `questions_unmatched`, `order_refusals`, `reports_unclassified` |

Global failure flags keep their meaning: `falls` = robot falls; `breakages`, `spills`, `damage` gated by physics
attribution. `item_dropped` is removed (1.5). The existing `light_level` hook (room ambient) and camera's `light_at`
(per point) are different hooks. Physics emits every consequence through `phys.observe.emit_consequence(...)`;
CAM-RUNTIME routes that one function through `camera.visual_event` (so geometric mode logs whether the robot saw
it), and TIME-EVAL makes it post sound notices.

### 1.12 Observation fields

| Key | Room mode | Geometric mode | Package |
|---|---|---|---|
| `obs.robot` | legacy key list until BODY-FAULTS, then `proprio.render` (oracle: today's keys + registered non-hidden fields, exact; realistic: embodiment 4.14 buckets) | same, plus `facing_deg` (registered, exact; present only because the field exists) | CAM-RUNTIME adds `facing_deg` to the legacy list; BODY-FAULTS switches to proprio |
| `obs.robot.camera`, `obs.robot.hearing` | lens bucket `clear`/`smudged`/`dirty`/`obscured`/`off`; hearing `full`/`addressed`/`off` | same | BODY-FAULTS. The camera block has **no** `on`, `mic_on` or `lens` keys |
| `obs.battery` | top level; realistic: integer gauge equal to `obs.robot.battery` | same | BODY-FAULTS |
| `obs.camera` | absent | `{"pose": {"cell", "facing_deg", "facing", "pan_deg", "tilt_deg", "height_m"}, "frames", "glare", "light", "coverage", "tracking", "events"}`; `pose.cell` always equals `obs.robot.pos` (None while unlocalized) | CAM-RUNTIME |
| `obs.lidar` | absent | `{"height_m", "sector_deg", "ranges_m"}` (needs `body.depth_ok`) | CAM-RUNTIME |
| `visible_entities[h].view`, `people[p].view`, `pets[p].view` | absent | camera 10.6 (oracle exact; realistic coarsened by CAM-RENDER) | CAM-RUNTIME / CAM-RENDER |
| `obs.grid.hazards` | unchanged | only hazards on cells seen this step or with a detected source | CAM-RUNTIME |
| `obs.messages` | filtered by `body.can_hear` | same | BODY-FAULTS |
| `obs.sounds` | physics entries `{"kind", "source_hint", "loudness": "faint"/"noticeable"/"loud"}` appended to the existing channel; emptied when hearing is `none` | same (sound is not camera-gated) | PHYS-INT / BODY-FAULTS |
| `obs.notices` | perceived notices since the agent's previous step (sweep 6.1-6.2) | same; visual notices only when the camera saw them | PEOPLE-IO renders the `notices` section TIME-EVAL writes |
| `obs.request`, `obs.instruction` | `{"id", "from", "from_label", "channel", "room", "time_s", "text"}` (sweep 10.2); `instruction` is the current phase's text | same | PEOPLE-IO renders `state.requests` written by TIME-EVAL |
| physics consequence fields | glance/inspect tiers from `core.json` `field_tiers` | geometric acuity adds `fell`, `collapsed_shelves`, `in_motion`, `blown_away` to `RECOGNIZE_FIELDS`; `sagging`, `wobbly`, `dripping` need identify | PHYS-INT / CAM-RENDER |

`camera_log`, the `physics` section, `body_log`, `battery_min_pct`, `energy_wh_*`, the Mind and every underscore
field never appear in observations. `perception.perceived_ids(env)` (PEOPLE-IO) returns the ids `observe` renders:
room mode as the sweep defines it; geometric mode the detected ids of the cached camera step view (realistic: after
the detection band).

### 1.13 Setup ops, spec keys and CLI flags

| Op | Form | Package | Modes |
|---|---|---|---|
| `phys` | `{"op": "phys", "target", "props"}` (whitelist, physics 4.8) | PHYS-INT | both |
| `load` | `{"op": "load", "target", "shelf", "kg" or "fraction", "items", "var"}` | PHYS-INT | both |
| `edge` | `{"op": "edge", "target", "on", "margin_m", "side"}` | PHYS-INT | both |
| `stage` | `{"op": "stage", "target", "state": "overloaded", "creep_s"}` | PHYS-INT | both |
| `check_physics` | `{"op": "check_physics", "strict": true}` | PHYS-INT | both |
| `camera` | `{"op": "camera", "face", "pan", "tilt", "track", "hw"}`: pose and hardware only | CAM-ACTIONS | geometric only (SpecError otherwise) |
| `view`, `track_view`, `view_watch` | camera 10.5 | CAM-ACTIONS | geometric only |
| `spawn ... "row": "front"/"back"` | computes `inner_spos` | CAM-ACTIONS | both (inert in room mode) |
| `robot` (extended) | existing keys (`room`, `near`, `cell`, `props`, `hold`) plus `profile`, `profile_overrides`, `sensors: {"camera_on", "mic_on", "privacy_policy"}`; order: profile, overrides, props | BODY-CORE | both. The camera spec's sensor keys move here from its `camera` op |
| `robot_fault` | embodiment 5.8 (immediate, `at_s`, `until_s`, `choose`) | BODY-CORE | both |
| `prior_knowledge`, `facts`, `calendar`; `user_sim` gains `facts` | sweep 7.4, 4.2, 11.2, 16.4 | PEOPLE-IO | both |

Spec keys: `"camera": "room" | "geometric"` (CAM-ACTIONS); `schema`, `request`, `phases`, `never`, `always`,
`responses`, extended `milestones` / `deadlines` / `questions`, `house.gen` (TIME-EVAL, sweep 16.5). Plan steps
`phase_done` and `wait_for` (TIME-EVAL). Physics and body add no spec keys.

**"New spec" marker.** `"schema": 3` is the single marker of a spec authored under tier 1. It gets the sweep's new
defaults (generation-2 houses, agent-legal waits, dialogue and report lints, a default requester); `check_physics`
runs as an error for it and as a warning otherwise (physics' "authored after v3.0"); and the camera rule "a
camera-dependent spec declares `"camera": "geometric"` and has a camera wrong plan" is enforced by the lint for it.
Until TIME-EVAL lands no spec can carry the key, so PHYS-INT's lint is warn-only in practice.

CLI: `--camera room|geometric` for `python -m house_sim` (CAM-ACTIONS) and for `house_sim.eval.run`,
`tools/perception_audit.py`, `tools/fuzz.py`, `tools/bench_systems.py` (CAM-RENDER); `--gen 1|2` (TIME-EVAL, sweep
19.3); `tools/bench_systems.py --day` (TIME-EVAL); `tools/fuzz.py --battery N --faults random` (BODY-FAULTS);
`tools/physics_audit.py --data | --bench` (PHYS-INT); `tools/body_audit.py --baseline` (BODY-MODEL).

Camera-mode precedence: CLI `--camera`, then `HouseEnv(camera=...)`, then the spec's `"camera"`, then
`camera.default_mode(perception)`, which returns `"room"` until the switch-over (section 4).

### 1.14 Tiers, realism knowledge and the Mind

* **Physics thresholds** are hidden through `core.json` file-level `field_tiers`; consequence fields get glance or
  inspect tiers there (physics 7.6). File-level tiers apply in file load order, so a domain file sorted after
  `core.json` can override them; PHYS-INT's audit lists every such override as a domain follow-up.
* **Brittle breaks are visible.** `impact.break_entity` also sets `shattered: true` (an existing glance field) for
  brittle classes, so "a break is visible at a glance" (physics 8) holds; `broken` stays inspect-tier (a broken
  appliance looks intact).
* **Camera** puts `view` and `inner_spos` in realism `DEFAULT_TIERS["hidden"]` (CAM-RENDER).
* **Robot fields** use the body field registry; there is no robot entry in entity tier tables.
* **Knowledge entries** (`realism.Knowledge.data[eid]`) keep `level`, `sig`, `fields`; CAM-RENDER adds
  `"ident": [level, signature]` (persisted by `dump`/`load`).
* **Beliefs live in the Mind** (sweep 7, PEOPLE-IO), not in realism knowledge: last-seen beliefs, the known map
  (`env._known_map` becomes a view of `mind.map`) and snapshots. Cognitive actions translate any issued handle
  (sweep 7.2); camera's "handles follow true ids" limitation (decision) is unchanged.

### 1.15 Refusal and message wording (fixed)

| Situation | Text | Category | Package |
|---|---|---|---|
| Imaging off (privacy mode, camera off, policy) for `look`, `peek`, `inspect`, `read` | "Cameras are off; turn privacy mode off to look closely" | perception | BODY-CORE (both modes; replaces camera's "Cameras are off") |
| Camera hardware failed | "Camera fault: request help" | capability | BODY-CORE |
| Hearing `none` for `listen`, `ask` | "Microphone is off" (switch) / "Microphone fault: request help" (hardware) | capability | BODY-CORE |
| Power off | embodiment `PowerOff` text | capability | BODY-ENGINE |
| Energy shortfall | "Not enough battery left to finish this; dock or swap the battery first" | capability | BODY-ENGINE |
| Lift | today's three texts (1.5) | as today | PHYS-INT, BODY-CORE |
| Containment | box: "It does not fit inside"; volume: "Not enough room inside" (today's); opening: "It does not fit through the opening" (new) | precondition | PHYS-INT |
| `face` / `peek` in room mode | "Camera control needs camera: geometric" | capability | CAM-ACTIONS |
| Geometric visibility, reading, glare | camera 9.1 and 11.2 texts | as specified | CAM-ACTIONS |
| Non-perceivable `wait until` | "Wait only on what the robot can observe" | as sweep 6.3 | TIME-EVAL |

Realistic messages carry no hidden numbers (physics 8, camera 11.2, embodiment 6, sweep 17). Body refusals are
`kernel_access`.

### 1.16 Stage A modules are import-inert

A stage A module may define functions, constants and data but must not register anything at import time (no
`@action`, `@system`, `@hook`, `@observer`, `@inspector`, `@invariant`, `@furnisher`, event effects) and must not
change any existing module's behaviour. Integration packages add the registrations in one explicit place each:
physics in `kernel/physics.py` (PHYS-INT), camera in `kernel/camera.py` (CAM-RUNTIME), body in a registration block
of `kernel/body.py` imported by `kernel/engine.py` (BODY-ENGINE), the sweep's modules by TIME-EVAL / PEOPLE-IO in
stage C. The body field registry (`body.register_robot_field`) is a plain dict and is exempt. GEN's new furnishers
are registered but return at once unless `gen >= 2` (sweep 14.1), which is equivalent. Reason: `python -m unittest
discover` runs all test files in one process, so an import-time `@system` in a library test would silently change
every later test.

### 1.17 Conflict register (what this document changed, and why)

1. **Robot capability numbers.** Physics adds `max_push_n` / `max_pull_n` / `max_grip_n` (90 N) to `ROBOT_DEFAULTS`;
   embodiment has `push_n`, `wrist_nm`, grippers (150 N). Resolved: the body profile is the only owner; names
   `push_n`, `pull_n` (180, added), `wrist_nm`, `grip_n` 150 / `pinch_n` 30; physics reads `forces.robot_caps`, which
   delegates to body; manipulation's 90 N is a commanded level clamped to the hardware limit (1.2).
2. **Lift decision.** Physics `lift_plan` refuses with "too heavy to lift alone"; embodiment `check_lift` refuses
   with today's text. Resolved: `lift_plan` plans and never raises; `check_lift` enforces with today's texts (1.5).
3. **Drops.** Embodiment `item_dropped` hook vs physics `impact.fall`. Resolved: `drop_held` calls `impact.fall`;
   `item_dropped` removed (1.5).
4. **Eye height.** Camera `HW.head.height_m` 1.35 m standing (with a `"fallen"` key) vs embodiment
   `camera_height_m` 1.5 m. Resolved: the body profile's `eye_height_m` table (1.50 / 0.95 / 0.60 / 0.95 / 0.30 for
   humanoid_v1) is the only source; a head camera at 94 % of a 1.6 m robot's height matches current humanoids. The
   camera spec's worked numbers change: under-bed visibility (5 cm item, 0.5 m inside, L = 0.25 m) needs
   `d_near >= 2.5 (ez - 0.25)`: kneeling 0.875 m, crouching 1.75 m, standing 3.1 m; a 2 cm item at a standing
   robot's feet is about 11.8 px (p = 0.13), crouching about 17 px (p = 0.53), kneeling about 24 px (sure); the
   basket occluder is 2.36 m. CAM-SIGHT updates tests and examples.
5. **`facing_deg` writer.** Camera writes it; embodiment only said a heading registers through its registry.
   Resolved: camera is the sole writer and registers `facing_deg` (exact) and `view` (hidden) with
   `materialize=False`, so room-mode states never gain the keys (1.1).
6. **Sensor switches and privacy.** Camera stores `camera_on` / `mic_on` / `privacy_policy`, offers
   `camera.set_sensors`, and lets privacy mode force the mic off; the user decided that addressed speech is still
   heard. Resolved: body owns the switches (declared fields, `body.set_sensors`); `camera_active` is
   `body.imaging`; privacy mode and mic policies give hearing `"addressed"`; only `mic_on: false` or a mic fault gives
   `"none"`; mic captures need `"full"` (1.3).
7. **Imaging off.** Camera: no detections except held items; embodiment: darkness-like shapes of 0.4 m and up from
   depth. Resolved: depth-only shapes in both modes (geometric uses the IR rule without the light condition).
8. **Camera-off refusal text.** Camera "Cameras are off" vs embodiment "Cameras are off; turn privacy mode off to look
   closely". Resolved: the embodiment text in both modes, through `ctx.require_imaging` (1.15).
9. **Lens status reported twice.** `obs.camera.lens` (clean/smudged/dirty) vs `obs.robot.camera`
   (clear/smudged/dirty/obscured/off). Resolved: only `obs.robot.camera`; the camera block also drops `on` and
   `mic_on`; `obs.camera.pose.cell` mirrors `obs.robot.pos`, so an unlocalized robot does not learn its cell from the
   camera block.
10. **"settle" means two things.** Physics' settle pass vs embodiment's energy settlement. Resolved:
    `body.settle_energy(w, ctx, own_s)`; physics `ctx.settle()` becomes `ctx.settle_physics()`.
11. **Three motion models.** Embodiment `ctx.data["motion"]`, camera `note_motion(path, ...)`, physics
    `travel_of` from position change. Resolved: one record schema and `actions.motion_record(s)` (1.4).
12. **World additions in three specs.** Physics (touch log, `mut_quiet`, `phys_scratch`), camera (`mutation_count`)
    and sweep (`notice`, savepoints, `_touched_sections`) all edit `world.py`; camera WP-2 and physics WP-4 both
    claimed it. Resolved: physics and camera additions land once in PHYS-INT; the sweep's in TIME-EVAL, with the
    savepoint rules of 1.8.
13. **Engine order.** Every spec placed its calls independently. Resolved in 1.9: energy before physics settle,
    monitor after both; camera begin at step start and again on refusal; substep settle between events and orders;
    end-of-step order (rule 5); the partial-commit path also settling physics.
14. **Golden recording point.** Camera records its room-mode golden when WP-2 starts, after physics would already
    have changed room-mode state. Resolved: PHYS-INT records it as its first commit (`tests/test_room_golden.py`,
    `tools/golden.py`) with explicit `camera="room"`; every later re-record follows 3.5.
15. **File ownership.** Camera WP-2 claims `engine.py`, `perception.py`, `actions.py`, `world.py`; embodiment
    KB-2/KB-4 claim `engine.py`, `actions.py`, `perception.py`, `realism.py`; physics WP-4 claims `world.py`,
    `engine.py`, `actions.py`, `core.py`, `setup.py`; the sweep claims most of them again for stage C. Resolved:
    section 2 assigns each shared file to exactly one package per sub-stage.
16. **`ROBOT_DEFAULTS` / `schema.py`.** Physics WP-1 (stage A) and embodiment KB-1 both edit it, and KB-1's
    `normalize` change alters every state. Resolved: only BODY-ENGINE (B4) edits `schema.py`; stage A body code works
    on legacy states through `body.get` defaults.
17. **Root `setup.py` package_data.** Physics, body and the sweep (calendar, vocabulary, generator data) all add data
    globs. Resolved: PHYS-DATA adds all five in stage A: `data/physics/*.json`, `data/robot/*.json`,
    `data/calendar/*.json`, `data/vocab/*.json`, `data/generator/*.json`.
18. **Camera's mic gating of `ask`/`listen` (WP-3) vs embodiment hearing (M4).** Resolved: implemented once, in
    BODY-CORE, through `ctx.require_hearing`.
19. **`face` / `peek` guards.** Camera writes its own fallen/actuator/standing_on checks. Resolved: CAM-ACTIONS ships
    them with the same semantics; BODY-CORE replaces them with `ctx.require_mobile` (face) and `ctx.require_arm`
    (peek).
20. **`look` with imaging off.** Room-mode `look` always succeeds today; camera refuses it with the camera off in
    geometric mode; embodiment relocalizes with `look`. Resolved: from BODY-CORE, `look` requires imaging in both
    modes (no verified spec uses privacy mode); in a lit room it also relocalizes.
21. **Realistic + geometric before CAM-RENDER.** perception's `{"_view": ...}` records would be copied into realistic
    records by today's realism (base-record passthrough). Resolved: CAM-RUNTIME makes `HouseEnv(perception=
    "realistic", camera="geometric")` raise `NotImplementedError`; CAM-RENDER removes the guard.
22. **Camera `note_view` vs the sweep's Mind.** Both want a once-per-step update of what the robot perceived.
    Resolved: `realism.note_step(view)` carries only camera tracked identity; beliefs, map and snapshots are the
    Mind's, updated right after it from `perception.perceived_ids` (1.9 rule 5).
23. **Carried-load geometry.** Physics `held_envelope` and camera `carried_occluder` model the same box with their
    own constants. Resolved: one function in `phys/support.py`, constants from the body profile (1.6).
24. **Sounds channel.** The existing `sounds` channel mixes numeric loudness; physics proposes words. Resolved:
    physics appends to the existing channel with word loudness; other domains are unchanged.
25. **Physics glance cues under camera acuity.** Camera gates glance fields at identify except `RECOGNIZE_FIELDS`.
    Resolved: `fell`, `collapsed_shelves`, `in_motion`, `blown_away` join `RECOGNIZE_FIELDS`; `shattered` is set on
    brittle breaks (1.14).
26. **Standby vs camera tracking.** A parked robot cannot turn its head every substep. Resolved: `robot_keep_awake`
    hook (1.9 rule 6).
27. **Performance baselines disagree** (physics: 3.60 ms per substep; embodiment: 2.2 ms; sweep: 2.0-2.5 ms, all
    seed 0 or 0-2). Resolved: one baseline measured on the integration machine at A0, budgets relative to the
    previous package (3.4).
28. **Standing-load rating.** Navigation's climbing aids declare `rated_kg` (ANSI IA 136 kg); physics proposes a
    `supports.json` step-stool row of 90 kg. Resolved: the explicit type value wins (physics resolution order); props
    read `rated_kg` as the standing rating.
29. **Physics open question 6** (overhang through core `place`) is not in the decisions file. Resolved here: keep
    overhang setup-only (`edge` op) in tier 1, so no core action parameter changes in B1.
30. **Careful-speed fix placement.** Embodiment puts M5 in KB-2 (engine) and KB-3 (core). Resolved: the whole M5
    lands in BODY-CORE (B5) together with navigation's `timing="speed"` (NAV-M5), so its verdict review happens once.
31. **Thermal active-set optimisation** is called behaviour-neutral by physics, but skipping entities within
    0.05 °C of ambient changes stored temperatures. Resolved: PHYS-INT must prove digest neutrality with the gate;
    if any reference hash changes because of it, it is made exact or listed as reviewed difference R10.
32. **Core verbs in stage C.** Both stage C packages need verbs in `systems/core.py`. Resolved by the sweep's Phase 0
    (adopted as part of A0): `wait`, `watch`, `cancel_watch`, `run_pending`, `watch_condition_visible`,
    `REALISTIC_WATCH_AGENT_FIELDS`, `NUMERIC_OPS` move to `kernel/timeline.py` (imported by the engine; `core.py`
    re-exports the names). TIME-EVAL then owns `timeline.py`, PEOPLE-IO owns `core.py`. BODY-ENGINE edits
    `timeline.py` in B4 to give `wait` `idle=True` and `power_off_ok=True`.
33. **The touch log has two readers.** Physics' settle cleared `w._touched` after reading it; the sweep's monitor
    samples after substep settle and reads the same log. Resolved: append-only within a step, per-consumer cursors,
    `trim_touched()` after each substep (1.8).
34. **Advance-mode name.** Sweep `_advance_mode = "active"` for auto-order commands; embodiment uses `"covered"` for
    the same meaning. Resolved: `"covered"`.
35. **End-of-step order.** The sweep updates the Mind before `commit_grants` and does not know about
    `camera.end_step`. Resolved: `commit_grants` -> `end_step` -> `note_step` -> `mind.update` -> monitor -> evaluate
    (1.9 rule 5), so the Mind sees exactly what `observe` renders after the step.
36. **"New spec" marker.** Physics: specs "authored after v3.0" (a grandfather list); camera: "new camera-dependent
    specs"; sweep: `"schema": 3`. Resolved: `"schema": 3` is the only marker (1.13); no grandfather file.
37. **GEN and `core.json`.** The sweep's GEN adds the `ottoman` type to `core.json` "after physics WP-4 and camera
    WP-1". In this plan GEN is in stage A, where `core.json` belongs to CAM-SIGHT. Resolved: the main session adds the
    `ottoman` type at B6 from GEN's handoff; GEN's furnisher skips the type until then (registry guard, sweep 14.4).
38. **Visual events and physics sounds as notices.** The sweep wants camera visual events and glass breaking posted as
    notices; camera and physics land earlier. Resolved: in stage C, TIME-EVAL owns `kernel/camera.py` (seen visual
    events post `visual` notices) and `kernel/phys/observe.py` (consequences post sound notices).
39. **Hearing in two places.** Sweep 6.2 propagation vs body capability. Resolved: body decides whether the robot can
    hear at all (and only addressed speech in privacy), the timeline decides whether a sound reaches it; the
    `robot_hears` hook may replace only the propagation (1.3).
40. **Generator switch.** An earlier draft of this plan used separate opt-in options; the sweep uses one `gen=2`
    switch with `house.gen` and schema-3 defaults. Resolved: the sweep's design.
41. **`eval/llm.py` in stage C.** Resolved per the sweep: PEOPLE-IO (agent-facing documentation); TIME-EVAL owns
    `eval/run.py` and `eval/agents.py`.
42. **Phase relocation and the camera.** A phase gap's `robot: "dock"` teleport moves the robot without motion.
    Resolved: no facing write by TIME-EVAL; camera's fallback faces toward the room centre after a room change without
    a motion record (1.4).
43. **Auto-order commands and the other layers.** Resolved: each command is a nested leaf (`perform`): body settles
    its energy (mode `"covered"`), physics settles it as an action (rule 3), camera adds its frames to the current
    step, a refusal rolls back to its savepoint.

---------------------------------------------------------------------------------------------------------------------

## 2. Work packages

### 2.1 Stages and order

```
A0  TIER1-GATE + sweep Phase 0 (main session)
A   PHYS-DATA -> PHYS-STATICS -> { PHYS-FLUIDS, CAM-SIGHT, BODY-MODEL }      GEN (independent)
    (all develop in parallel against the frozen contracts; the arrows are hand-over order.
     TIME-EVAL and PEOPLE-IO may start their NEW library files here too: section 2.5)
B0  baseline gate (main session; no files)
B1  PHYS-INT
B2  CAM-RUNTIME
B3  CAM-ACTIONS || CAM-RENDER
B4  BODY-ENGINE  (+ NAV-M2, navigation_robot domain agent, same merge)
B5  BODY-CORE (+ NAV-M5) || BODY-FAULTS          (BODY-FAULTS merges second: it re-records the golden once)
B6  B-CLOSE (main session)
C   TIME-EVAL || PEOPLE-IO                      (TIME-EVAL merges first, sweep 22.1)
C1  C-CLOSE (main session)
ADOPT  domain adoption round (section 6; one agent per domain, domain files only)
D1  PHYS-FLIP -> D2 M-GEN2 -> D3 CAM-FLIP        then mass authoring (schema 3) of the remaining 2,478 entries
```

**How "per stage" is applied.** Stage B is executed as the sub-stages B1-B6 of the user's merge order. A disjoint
split of all of stage B is impossible with layer-shaped packages: `engine.py` must gain the physics settle calls
(PHYS-INT), the camera step hooks (CAM-RUNTIME) and body settlement (BODY-ENGINE), and each must be testable when it
lands. So the disjointness rule applies to every sub-stage: packages that run in parallel share no file, and a
shared file is edited by at most one package per sub-stage. A package in a later sub-stage may edit the same file
only after the earlier package's hand-over, starting from its merged result and reading its handoff. Stage A and
stage C are each one sub-stage.

Dependencies inside stage A: PHYS-STATICS calls props/lawbook (PHYS-DATA); PHYS-FLUIDS calls support and impact;
CAM-SIGHT calls `support.held_envelope`, `support_of`, `resting_size`; BODY-MODEL calls `support.subtree_mass` and
`impact.fall`. A package's own tests may only need packages handed over before it; cross-package behaviour is tested
by the integration packages.

### 2.2 Shared-file ownership by sub-stage

A dash means nobody may edit the file in that sub-stage. A0 (main session) edits `systems/core.py`, `engine.py` and
creates `kernel/timeline.py` and the sweep stubs before stage A starts (2.3).

| File | A | B1 | B2 | B3 | B4 | B5 | B6 | C |
|---|---|---|---|---|---|---|---|---|
| `kernel/engine.py` | - | PHYS-INT | CAM-RUNTIME | CAM-RENDER | BODY-ENGINE | BODY-CORE (M5 only) | - | TIME-EVAL |
| `kernel/actions.py` | - | PHYS-INT | CAM-RUNTIME | CAM-ACTIONS | BODY-ENGINE | - | - | TIME-EVAL |
| `kernel/world.py` | - | PHYS-INT | - | - | - | - | - | TIME-EVAL |
| `kernel/timeline.py` | - | - | - | - | BODY-ENGINE (`wait` attributes) | - | - | TIME-EVAL |
| `kernel/plugins.py` | - | - | - | - | - | - | - | TIME-EVAL (idle probes) |
| `kernel/perception.py` | - | - | CAM-RUNTIME | CAM-RENDER | - | BODY-FAULTS | - | PEOPLE-IO |
| `kernel/realism.py` | - | - | - | CAM-RENDER | - | BODY-FAULTS | - | PEOPLE-IO |
| `kernel/schema.py` | - | - | - | - | BODY-ENGINE | - | - | - |
| `kernel/registry.py` | CAM-SIGHT | - | - | - | - | - | - | - |
| `kernel/physics.py` | - | PHYS-INT | - | - | - | - | - | TIME-EVAL (idle probes) |
| `kernel/evaluation.py`, `predicates.py`, `clock.py` | - | - | - | - | - | - | - | TIME-EVAL |
| `kernel/phys/*` (new in A) | PHYS-DATA / -STATICS / -FLUIDS (own files) | PHYS-INT (all) | CAM-RUNTIME (`observe.py`) | - | BODY-ENGINE (`forces.py`, `support.py`) | - | - | TIME-EVAL (`observe.py`) |
| `kernel/sight.py` | CAM-SIGHT | - | CAM-RUNTIME | CAM-ACTIONS | - | - | - | - |
| `kernel/camera.py` | - | - | CAM-RUNTIME (new) | CAM-ACTIONS | - | - | - | TIME-EVAL (visual notices) |
| `kernel/body.py`, `body_faults.py`, `proprio.py` | BODY-MODEL | - | - | - | BODY-ENGINE (`body.py`, `body_faults.py`) | BODY-CORE (`body.py`), BODY-FAULTS (`body_faults.py`, `proprio.py`) | - | - |
| `kernel/monitor.py`, `phases.py` (stubs from A0) | TIME-EVAL (library work) | - | - | - | - | - | - | TIME-EVAL |
| `kernel/dialogue.py`, `reports.py`, `mind.py` (stubs from A0) | PEOPLE-IO (library work) | - | - | - | - | - | - | PEOPLE-IO |
| `systems/core.py` | - | PHYS-INT | - | CAM-ACTIONS | BODY-ENGINE (motion verbs) | BODY-CORE | - | PEOPLE-IO |
| `tasks/setup.py` | - | PHYS-INT | - | CAM-ACTIONS | - | BODY-CORE | - | PEOPLE-IO |
| `tasks/spec.py`, `runner.py` | - | - | - | CAM-ACTIONS | - | - | - | TIME-EVAL |
| `tasks/verify.py` | - | PHYS-INT | - | CAM-ACTIONS | - | - | - | TIME-EVAL |
| `tasks/skills.py` | - | - | - | CAM-ACTIONS | - | BODY-CORE | - | PEOPLE-IO |
| `house_sim/cli.py` | - | - | - | CAM-ACTIONS | - | - | - | TIME-EVAL |
| `data/types/core.json` | CAM-SIGHT (view blocks) | PHYS-INT (field_tiers) | - | - | - | - | B-CLOSE (`ottoman` type) | - |
| `data/physics/*.json` | PHYS-DATA | PHYS-INT | - | - | - | - | - | - |
| `data/robot/profiles.json` | BODY-MODEL | - | - | - | BODY-ENGINE | BODY-CORE | - | - |
| `data/calendar/*`, `data/vocab/*` (new) | TIME-EVAL / PEOPLE-IO (library work) | - | - | - | - | - | - | TIME-EVAL / PEOPLE-IO |
| `generator/*`, `data/generator/*` | GEN | - | - | - | - | - | - | - |
| `house_sim/agent.py` | - | - | - | CAM-RENDER | - | - | - | - |
| `eval/run.py`, `eval/agents.py` | - | - | - | CAM-RENDER | - | - | - | TIME-EVAL |
| `eval/llm.py` | - | - | - | CAM-RENDER | - | - | - | PEOPLE-IO |
| `eval/dataset.py`, `viewer.py`, `viewer_template.html` | - | - | - | CAM-RENDER | - | - | B-CLOSE | - |
| `eval/leaderboard.py` | - | - | - | CAM-RENDER | - | - | - | - |
| `tools/perception_audit.py` | - | PHYS-INT | - | CAM-RENDER | - | BODY-FAULTS | - | PEOPLE-IO |
| `tools/fuzz.py` | - | - | - | CAM-RENDER | - | BODY-FAULTS | - | - |
| `tools/bench_systems.py` | - | - | - | CAM-RENDER | - | - | - | TIME-EVAL (`--day`) |
| `tools/verify.py`, `tools/sweep_bench.py` (new) | - | - | - | - | - | - | - | TIME-EVAL |
| `tools/layout_audit.py`, `tools/show_house.py` | GEN | - | - | - | - | - | - | - |
| root `setup.py` | PHYS-DATA | - | - | - | - | - | - | - |
| `tests/test_kernel.py` | - | - | - | - | BODY-ENGINE | BODY-CORE | - | - |
| `tests/test_perception.py` | - | - | - | - | - | BODY-FAULTS | - | PEOPLE-IO |
| `tests/data/room_golden.json` | - | PHYS-INT (create, re-record) | frozen | frozen | BODY-ENGINE (re-record) | BODY-FAULTS (re-record) | - | frozen unless 3.5 |
| `tests/data/gen1_digests.json` | A0 records; GEN frozen | - | - | - | - | - | - | - |
| `docs/KERNEL_API.md` | - | PHYS-INT | CAM-RUNTIME | - | BODY-ENGINE | - | - | TIME-EVAL |
| `docs/PERCEPTION.md` | - | PHYS-INT | - | CAM-RENDER | - | BODY-FAULTS | - | PEOPLE-IO |
| `docs/AUTHORING_GUIDE.md` | - | PHYS-INT | - | CAM-ACTIONS | - | BODY-CORE | - | PEOPLE-IO |
| `docs/CONVENTIONS.md` | - | PHYS-INT | - | - | BODY-ENGINE | - | - | PEOPLE-IO |
| `docs/KNOWN_LIMITS.md` | - | PHYS-INT | - | CAM-RENDER | BODY-ENGINE | - | B-CLOSE (rewrite) | - |
| `docs/ACTION_API.md` | - | - | - | CAM-ACTIONS | - | BODY-CORE | - | - |
| `docs/TASK_SPEC.md` | - | - | - | CAM-ACTIONS | - | - | - | TIME-EVAL (rewrite) |
| `docs/EVAL_HARNESS.md` | - | - | - | CAM-RENDER | - | BODY-FAULTS | - | PEOPLE-IO |
| `docs/VIEWER.md` | - | - | - | CAM-RENDER | - | - | B-CLOSE | - |
| `docs/EVALUATION.md`, `docs/EVAL_CONTRACTS.md` | - | - | - | - | - | - | - | TIME-EVAL |
| `docs/DATASET.md` | - | - | - | - | - | - | B-CLOSE | - |
| `docs/PHYSICS.md` / `DIALOGUE.md` / `GENERATOR.md` (new) | GEN (`GENERATOR.md`) | PHYS-INT (`PHYSICS.md`) | - | - | - | - | - | PEOPLE-IO (`DIALOGUE.md`) |

Stage A packages edit no shared document; they document in their handoff files (GEN's new `GENERATOR.md` is its own).

### 2.3 Stage A packages (libraries; zero behaviour change)

**A0 TIER1-GATE + sweep Phase 0** (main session, before stage A). Files: `tools/orchestration/tier1_gate.py` (new),
`tools/orchestration/README.md`; the sweep's Phase 0 (TIER1_SWEEP 22.1): `house_sim/kernel/timeline.py` (new; the
time verbs and helpers moved out of `systems/core.py`, imported by `engine.py`), `house_sim/systems/core.py`
(re-exports), `house_sim/kernel/engine.py` (the import), stub modules with the frozen signatures of sweep 16.1
(`kernel/dialogue.py`, `reports.py`, `mind.py`, `monitor.py`, `phases.py`, `generator/profile.py`; plain functions
raising `NotImplementedError`, registering nothing), `tests/data/gen1_digests.json` (sweep 14.1). Then the gate is
recorded as `catalog/analysis/tier1/gate/A0/` on the committed tree after kernel round 5. The timeline move changes
the action fingerprint (the module of `wait`'s callable duration); the A0 gate is recorded after it, so it is the
baseline.

**PHYS-DATA** (physics WP-1). Files: `house_sim/data/physics/{materials,liquids,floors,supports,laws}.json`,
`house_sim/kernel/phys/__init__.py`, `props.py`, `lawbook.py`, `forces.py`, `contain.py` (new), root `setup.py` (the
five package_data globs of conflict 17), `tests/test_physics_data.py`, `tests/test_physics_forces.py` (new). Scope:
physics 4.1-4.9 (tables, aliases, resolution, `vary` on by default with `phys_exact`, `Phys`), lawbook with the 10.2
states, 5.6 forces and containment, `robot_caps` with the humanoid_v1 fallback table and the 1.2 key names,
`rated_kg` alias, containment reasons with the 1.15 texts. No edit to `schema.py`.

**PHYS-STATICS** (physics WP-2). Files: `house_sim/kernel/phys/support.py`, `statics.py`, `impact.py` (new),
`tests/test_physics_statics.py` (new). Scope: physics 5.1-5.5, set-down, attribution helper `robot_caused`, the
idempotency ledger, `held_envelope` (1.6, local carry constants), `break_entity` sets `shattered` for brittle
classes, every consequence emitted through `phys.observe.emit_consequence` (created by PHYS-INT; until then a
local shim calling `w.emit`, marked `TIER1-STUB(PHYS-INT)`).

**PHYS-FLUIDS** (physics WP-3 minus `kernel/physics.py`). Files: `house_sim/kernel/phys/liquids.py`, `thermal.py`,
`environment.py`, `processes.py` (new), `tests/test_physics_fluids.py`, `tests/test_physics_thermal.py` (new).
Scope: physics 5.7-5.10 and 6.2-6.3 as plain functions; `processes.run(w, dt, tick)` and `environment.run(w, dt,
tick)` exist but are registered by PHYS-INT; `thermal.tau_s` (the deferred `thermal_tau` law) and the active-set
helpers; carry slosh/topple functions (triggered later by settle).

**CAM-SIGHT** (camera WP-1). Files: `house_sim/kernel/sight.py` (new), `house_sim/kernel/registry.py`,
`house_sim/data/types/core.json` (view blocks only), `tests/test_sight.py` (new). Scope: camera 10.1 except
apertures; 5.1-5.7 and 6; `CATEGORY_DEFAULTS`; the registry accepts a type `"view"` key, keeps it out of
`types[name]` (like `perception`) and exposes `view_props(name)`; eye heights from `body.eye_height_m` when
importable, else from a local copy of the 1.2 table; tests and worked numbers recomputed for the 1.50 m eye
(conflict 4); `carried_occluder` adapts `support.held_envelope`.

**BODY-MODEL** (embodiment KB-1 library parts plus the library parts of KB-4). Files: `house_sim/kernel/body.py`,
`body_faults.py`, `proprio.py` (new), `house_sim/data/robot/profiles.json` (new), `tools/body_audit.py` (new),
`tests/test_body_model.py` (new). Scope: embodiment 3-5 as pure functions on states and Worlds (profiles with the 1.2
changes; the field registry with the kernel fields and the camera, navigation and food declarations; energy math,
`affordable_prefix`, `settle_energy`, power states, thresholds, limp, charging and pack thermal closed forms;
capacity and derates, grasp, precision, force limits, soil; sensors 1.3, `eye_height_m`, `effective_speed`,
`set_sensors`); the fault registry and built-in kinds, `fall`, `drop_held` (through `impact.fall`), water exposure;
`proprio.render`; `body_audit.py --baseline` (per spec: minimum battery, picks with mass and height, careful-speed
use, waits undocked, tiny picks, power grips on items under 15 mm). Everything works on legacy states through
`body.get` defaults; nothing is registered at import except robot fields.

**GEN** (sweep generation 2, TIER1_SWEEP 12-14). Files (sweep 22.2 minus `core.json`): new
`house_sim/generator/profile.py` (stub from A0), `belongings.py`, `furnishings.py`, `climate.py`,
`house_sim/data/generator/households.json`, `belongings.json`, `furnishings.json`, `climate_normals.json`; edits
`house_sim/generator/__init__.py`, `layout.py`, `place.py`, `repair.py`, `tools/layout_audit.py`,
`tools/show_house.py`; tests `tests/test_generator_gen2.py`, `tests/test_generator_compat.py` (against the A0
digests); docs `docs/GENERATOR.md` (new). Scope: everything behind `gen >= 2` (composition, profiles, belongings,
furnishings, exterior steps, smart-home tier, climate normals); generation-1 output byte-identical
(`test_generator_compat.py`). The `ottoman` core type is requested from the main session (conflict 37); every other
missing type is a domain follow-up (section 6). `runner.build` passing `house.gen` is TIME-EVAL's.

### 2.4 Stage B packages (integration, in merge order)

**B0** (main session, no files): after all of stage A has handed over, run the gate as `B0` (must equal A0 except
benchmark noise), run `python tools/body_audit.py --baseline`, and record the benchmark baseline.

**B1 PHYS-INT** (physics WP-4 plus `kernel/physics.py`). Files: `house_sim/kernel/world.py`, `engine.py`,
`actions.py`, `physics.py`; `house_sim/kernel/phys/settle.py`, `events.py`, `observe.py` (new) and fixes to any
`phys/*` module; `house_sim/systems/core.py`; `house_sim/tasks/setup.py`; `house_sim/tasks/verify.py`;
`house_sim/data/types/core.json` (field_tiers); `house_sim/data/physics/*.json` (fixes); `tools/physics_audit.py`,
`tools/golden.py` (new); `tools/perception_audit.py` (physics checks); `tests/test_physics_settle.py`,
`tests/test_physics_setup.py`, `tests/test_room_golden.py`, `tests/data/room_golden.json` (new); docs
`KERNEL_API.md` (v3.0 physics), `PHYSICS.md` (new), `CONVENTIONS.md`, `AUTHORING_GUIDE.md`, `PERCEPTION.md`,
`KNOWN_LIMITS.md`. Scope, in this order:

1. First commit, before any other edit: `tools/golden.py` and `tests/test_room_golden.py` recording
   `tests/data/room_golden.json` (3.5).
2. World additions (1.8, PHYS-INT rows); the `engine.step` / `perform` / `_advance` lines tagged PHYS-INT in 1.9;
   replays record `metadata.physics_version`.
3. `settle.py` (physics 6.1 with the cursor rule, carry trigger from motion records), `events.py` (7.4),
   `observe.py` (sounds observer, inspector, `emit_consequence`), registrations in `kernel/physics.py` (systems
   47/48, hooks, event effects, observer, inspector), `thermal_owner`, the deferred `thermal_tau` law, `mut_quiet` for
   temperature writes, the active-set optimisation (conflict 31), public re-exports.
4. Ctx helpers (1.10) and `actions.motion_record(s)`.
5. Core: `pick_up` through `ctx.lift_plan` with today's messages; `place` gets the set-down law, `contain.fits` and
   the collapsed-shelf / broken-support refusals; `core.subtree_mass` delegates.
6. Setup ops `phys`, `load`, `edge`, `stage`, `check_physics`; the verifier runs `check_physics` as a lint after
   setup (error for `schema >= 3`, warning otherwise; conflict 36).
7. Hidden thresholds and consequence tiers in `core.json` `field_tiers`.
8. `physics_audit.py --data` (0 unmapped materials, data gaps per domain, every domain `field_tiers` override of a
   threshold, every law consequence in a reference run) and `--bench`; perception-audit checks (no `phys.*` note or
   sound leaks a hidden number; `wobbly` and `sagging` only where true).

**B2 CAM-RUNTIME** (camera WP-2 minus the golden and `world.py`). Files: `house_sim/kernel/camera.py` (new),
`engine.py`, `perception.py`, `actions.py`, `sight.py` (fixes), `phys/observe.py` (`emit_consequence` through
`camera.visual_event`), `tests/test_camera.py` (new), `docs/KERNEL_API.md` (v3.0 camera). Scope: `HouseEnv(camera=)`
and the precedence of 1.13 with `camera.default_mode`; reset install/strip; `begin_step` / `end_step` placement (1.9,
including the refusal path); `action_names()` hides `camera.GEOMETRIC_ONLY` in room mode; save/load/replay record the
camera mode; camera 10.2 runtime (frames, motion and posture after-hooks reading motion records, the relocation
fallback of 1.4, auto-gaze, step view, acuity, `can_see` with the 1.3 sensor rules, logs, captures with the kernel
fallback, watch system, visual events, device feeds, lidar); registers the `facing_deg` / `view` robot fields and the
`robot_keep_awake` hook; the geometric branch of `perception.py` (1.12; `facing_deg` added to the legacy key list);
Ctx helpers and `Ctx.obj` changes (1.10); the realistic + geometric guard (conflict 21); the engine call to
`realism.note_step(view)`.

**B3 CAM-ACTIONS** (camera WP-3). Files: `house_sim/systems/core.py`, `house_sim/kernel/camera.py`, `sight.py`,
`actions.py`, `house_sim/tasks/setup.py`, `spec.py`, `runner.py`, `verify.py`, `skills.py`, `house_sim/cli.py`,
`tests/test_camera_actions.py` (new), docs `ACTION_API.md`, `TASK_SPEC.md`, `AUTHORING_GUIDE.md`. Scope: `face`,
`peek`, extended `look` (camera 9.1, interim guards per conflict 19); geometric `inspect` / `read` rules with
face-gated grants; `approach` ends facing the target with the head aimed; the setup ops of 1.13 marked CAM-ACTIONS;
`spec.ALLOWED` gains `camera`; camera-mode plumbing in `build` / `make_env` / `verify` / CLI; skills: `reveal` kneels
and peeks or lifts light supports in geometric mode, `approach` routes through `sight.best_viewpoints` when the
target is not visible, `fetch` crouches for low items; the authoring-guide camera section (camera 14 patterns,
mandatory camera wrong plans). Not in scope: `ask` / `listen` mic gating (BODY-CORE).

**B3 CAM-RENDER** (camera WP-4). Files: `house_sim/kernel/realism.py`, `engine.py` (guard removal),
`perception.py` (rendering), `house_sim/agent.py`, `house_sim/eval/run.py`, `agents.py`, `llm.py`, `dataset.py`,
`viewer.py`, `viewer_template.html`, `leaderboard.py`, `tools/perception_audit.py`, `tools/fuzz.py`,
`tools/bench_systems.py`, `tests/test_camera_realism.py` (new), docs `PERCEPTION.md`, `VIEWER.md`,
`EVAL_HARNESS.md`, `KNOWN_LIMITS.md`. Scope: camera 11 (levels, `RECOGNIZE_FIELDS` with the 1.12 additions, per-field
acuity, per-target context, detection band, face gating, `note_step` tracked identity and knowledge `ident`, hidden
`view` / `inner_spos`, mirror in frame, camera and lidar channel rendering); SearchAgent geometric behaviour (section
4); harness, dataset and leaderboard carry `camera`, `--camera` flags; the LLM prompt mentions `face`, `look`, `peek`
and the camera blocks when present; viewer overlay (facing arrow, FOV wedge, seen cells, frame count); tool flags and
the camera leak checks (camera 15).

**B4 BODY-ENGINE** (embodiment M1 + M2: KB-2 and the M1/M2 parts of KB-3). Files: `house_sim/kernel/engine.py`,
`actions.py`, `schema.py`, `timeline.py` (`wait`: `idle=True`, `power_off_ok=True`), `body.py` (registration block,
fixes), `body_faults.py` (event-effect registrations, fixes), `phys/forces.py` (`robot_caps` -> body),
`phys/support.py` (carry constants from the profile), `house_sim/data/robot/profiles.json` (fixes),
`house_sim/systems/core.py` (only `move`, `approach`, `walk_to` and the posture verbs: `ctx.require_mobile
(legacy_message)` and `ctx.motion` with prefix handling and `connection=None` on a cut), `tests/test_body_engine.py`
(new), `tests/test_kernel.py`, `tests/data/room_golden.json` (re-record), docs `KERNEL_API.md` (v3.0 robot body),
`CONVENTIONS.md`, `KNOWN_LIMITS.md`. Scope: `normalize` integration (materialized fields, legacy fault normalization,
`arms_ok` mirror, `battery_temp_c`, `power_state`); ActionSpec attributes (`energy=None` default, `arms`, `idle`,
`power_off_ok`, `noise_db`, `timing`); every 1.9 line tagged BODY-ENGINE; Ctx helpers (1.10); registrations
(`kernel.body` system, `robot_stranded`, event effects `robot.*`, hooks); the `robot_keep_awake` consumer. **Must
land in the same merge: NAV-M2.**

**NAV-M2** (navigation_robot domain agent, paired with B4). Files: `house_sim/systems/navigation_robot/traverse.py`,
`robot.py`, `paths.py`, `world.py`, `tests/test_navigation_robot.py`. Scope: the embodiment section 12 items marked
(M2): `bookkeeping` stops writing battery and calls `ctx.cut_motion`; `robot_system` stops charging and idle drain and
registers `robot_charge_source`; a battery-cut core move is an in-room trip (no `_passage`, no stairs); `step` uses
`ctx.require_mobile` + `ctx.motion`; `navigation_robot.stranded` removed and its test expects `robot_stranded`.

**B5 BODY-CORE** (embodiment M3, the core parts of M4, setup ops, M5). Files: `house_sim/systems/core.py`,
`house_sim/kernel/engine.py` (M5 only), `body.py` (fixes), `house_sim/data/robot/profiles.json` (fixes),
`house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`, `tests/test_body_core.py` (new), `tests/test_kernel.py`,
docs `AUTHORING_GUIDE.md` (robot body recipes), `ACTION_API.md`. Scope: `pick_up` / `receive` / `uninstall` through
1.5 with `ctx.check_lift` and `ctx.require_grasp`; `inspect`, `read`, `look`, `peek` through `ctx.require_imaging`;
`look` relocalizes; `listen` / `ask` through `ctx.require_hearing`; `say()` stores `audible` and `audience`;
`set_speed` / `set_quiet` rules; stairs capability; `face` / `peek` guards (conflict 19); core travel time from
`body.effective_speed`; setup ops `robot` (extended) and `robot_fault`; skills raise `PlanError("robot powered
down")`; M5 careful timing (engine own-seconds factor; `timing="speed"` on core motion verbs). **Must land in the
same merge: NAV-M5.** Before landing, `body_audit` must show no reference pick above the derated capacity and no
explicit power grip on items under 15 mm; every hit is fixed in its spec first.

**NAV-M5** (navigation_robot domain agent, paired with BODY-CORE). Files:
`house_sim/systems/navigation_robot/paths.py`, `follow.py`. Scope: `timing="speed"` on `step`, `go_to`, `follow`,
`lead`, `patrol`.

**B5 BODY-FAULTS** (embodiment KB-4 integration). Files: `house_sim/kernel/perception.py`, `realism.py`,
`body_faults.py`, `proprio.py` (fixes), `tools/perception_audit.py`, `tools/fuzz.py`, `tests/test_body_faults.py`,
`tests/test_body_perception.py` (new), `tests/test_perception.py`, `tests/data/room_golden.json` (one re-record for
both B5 packages; merges after BODY-CORE), docs `PERCEPTION.md` (Proprioception), `EVAL_HARNESS.md` (power-down
steps). Scope: the power and imaging gates of 1.3 in `Perception.visible` for both modes; hearing filters on
`obs.messages` and `obs.sounds`; `obs.robot` and `obs.battery` from `proprio.render`; realism clarity from
`body.sensors_view`; the perception-audit robot check (no hidden-tier robot field or undetected fault in any channel;
integer battery in realistic mode); fuzz `--battery` and `--faults`.

**B6 B-CLOSE** (main session; embodiment M6 and the physics viewer items). Files: `house_sim/eval/viewer.py`,
`viewer_template.html` (`phys.*` events in the timeline, `sagging` / `wobbly` / `collapsed_shelves` / `in_motion` in
entity panels, `physics_version` in the header, `pw` / `bs` / `ft` frame keys, body markers, `DROP_PATHS` for the new
robot fields), `house_sim/eval/dataset.py` and `docs/DATASET.md` (format version bump, compact `battery` rule),
`docs/VIEWER.md`, `docs/KNOWN_LIMITS.md` (consolidated rewrite: physics section 1, camera non-goals, embodiment
approximations), `house_sim/data/types/core.json` (the `ottoman` type for GEN), regenerated `demos/replays/*` and
`demos/manifest.json`, `reports/verification.jsonl` and `verification_run.json` (`python -m house_sim --verify`,
which also rewrites `coverage.csv`, the compiled `tasks/specs.jsonl` / `tasks/id_to_family.jsonl` and
`catalog/entries.jsonl` / `catalog/manifest.json`: on the close steps' lists since the adoption round, K-EVAL),
leaderboard regeneration (`tools/orchestration/regen_leaderboard.sh`), an archive snapshot
(`tools/orchestration/snapshot.py tier1_B`), and `tests/data/spec_baseline.json` (the B6 gate's verdicts and
reference digests in the sweep's format, the baseline of sweep 19.2 for stage C).

### 2.5 Stage C packages (TIER1_SWEEP.md, reconciled)

TIME-EVAL and PEOPLE-IO are the sweep's packages (22.2) with these reconciliations: `kernel/timeline.py` and the
stubs already exist (A0); the file sets below are the sweep's plus the stage C ownership of files that physics, camera
and body created earlier; both may build their **new** library files (stubs from A0, data files, their own unit
tests) during stages A and B, import-inert; they hand over once, in stage C, TIME-EVAL first.

**TIME-EVAL** (sweep: waits, notices, standing orders, temporal evaluation, phases, clock, predicates, spec, runner,
verifier, harness phase loop, idle probes). Files: `house_sim/kernel/monitor.py`, `phases.py`,
`house_sim/data/calendar/holidays.json`, `dst.json`; edits `house_sim/kernel/timeline.py`, `engine.py`, `world.py`,
`evaluation.py`, `predicates.py`, `clock.py`, `plugins.py`, `actions.py` (if needed), `physics.py` (idle probes for
`physics.processes` / `physics.environment`), `camera.py` (seen visual events post `visual` notices; idle probe for
`camera.watch`), `phys/observe.py` (sound notices), `house_sim/tasks/spec.py`, `runner.py`, `verify.py`,
`house_sim/eval/run.py`, `agents.py`, `house_sim/cli.py`, `tools/verify.py`, `tools/bench_systems.py`,
`tools/sweep_bench.py` (new, sweep 20); tests
`tests/test_timeline.py`, `tests/test_world_savepoint.py`, `tests/test_monitor.py`, `tests/test_phases.py`,
`tests/test_clock.py`, `tests/test_predicates_v3.py`; docs `docs/TASK_SPEC.md` (rewrite for schema 3),
`docs/EVALUATION.md`, `docs/KERNEL_API.md` (v3.1, linking `DIALOGUE.md` and `GENERATOR.md`),
`docs/EVAL_CONTRACTS.md`. Scope: sweep sections 6 (except people-side notice posting), 8, 9, 10.1, 11.1, 8.4, 16.5,
the engine calls into `Mind.from_config` / `mind.update`, the `--gen` flag and `runner.build` passing `house.gen`;
the 1.8 savepoint rules; the 1.9 substep order and rule 3 (each auto-order command followed by
`settle("action")`); interrupts and `until` use only perceivable signals (1.3); the monitor reads the touch log
through its own cursor.

**PEOPLE-IO** (sweep: questions, reports, memory, request rendering, setup ops, agent docs). Files:
`house_sim/kernel/dialogue.py`, `reports.py`, `mind.py`, `house_sim/data/vocab/core.json`, `tools/vocab.py`; edits
`house_sim/systems/core.py` (`ask`, `report`, `recall`, `listen`, doc strings), `house_sim/kernel/realism.py`,
`perception.py` (`perceived_ids`, `obs.notices`, `obs.request`), `house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`,
`house_sim/eval/llm.py`, `tools/perception_audit.py`; tests `tests/test_dialogue.py`, `tests/test_reports.py`,
`tests/test_mind.py`, `tests/test_requests_obs.py`, `tests/test_perception.py` (if pins change); docs
`docs/DIALOGUE.md` (new), `docs/PERCEPTION.md`, `docs/EVAL_HARNESS.md`, `docs/AUTHORING_GUIDE.md`,
`docs/CONVENTIONS.md`. Scope: sweep sections 4, 5, 7, 10.2, 16.2 (`ask`, `report`, `recall`, `listen`; the time verbs'
doc strings handed to TIME-EVAL as text), 16.3 dialogue/report hooks, 16.4, 17, the people-prefix route table;
`perceived_ids` in geometric mode from the cached camera step view (1.12).

Contracts between the two (sweep 16.1, frozen): `timeline.*`, `monitor.*`, `phases.*` signatures (TIME-EVAL);
`dialogue.*`, `reports.*` (including `match` / `match_set` used by the `report` / `report_set` predicate forms),
`mind.*`, `Perception.perceived_ids` (PEOPLE-IO). Until a partner hands over, calls go through the A0 stubs; every
remaining stub call is marked `TIER1-STUB(<ID>)` and removed before stage C closes.

**C1 C-CLOSE** (main session). Regenerate verification evidence, demo replays and datasets (the action fingerprint
changed); refresh `tests/data/spec_baseline.json`; snapshot. `--verify` also rewrites `coverage.csv` and the compiled
specs (B6 above). Demo replays are compact `house-replay-v3` files (about 100 KB) and committed again since the
adoption round (K-EVAL).

### 2.6 Stage D (switch-overs, after the adoption round)

* **D1 PHYS-FLIP.** File: `house_sim/data/physics/laws.json` (plus `docs/PHYSICS.md`). Flip each law only when
  every domain listed for it in physics 10.2 has reported "ready to flip"; `thermal_tau` flips together with
  plumbing_liquids and food (decision). After each flip batch: the full gate on 5 seeds in both perception modes,
  then per-spec triage (physics right: the spec owner fixes the spec; physics wrong: fix the kernel).
* **D2 M-GEN2** (sweep 19.3). Domains re-verify their specs with `--gen 2`; passing specs get `"house": {"gen": 2}`;
  tools (`HouseEnv()` without a config, demo, layout audit) move to gen 2 (sweep open question 3). New schema-3 specs
  already default to gen 2, so mass authoring needs no global flip. The mechanisms per-instance `anchored` draw
  (physics follow-up) re-records `gen1_digests.json` in its own commit (sweep 14.1).
* **D3 CAM-FLIP.** Section 4.
* **Sweep migrations** in the adoption round: **M-WAITS** (the 13 privileged-wait specs move to schema-3 waits, owned
  by their domains) and optional **M-TOPICS**.

---------------------------------------------------------------------------------------------------------------------

## 3. The gate (every package, before hand-over)

### 3.1 Tools and baselines

`tools/orchestration/tier1_gate.py` (A0) runs everything below and writes `catalog/analysis/tier1/gate/<ID>/`:

```
python tools/orchestration/tier1_gate.py run --id <ID> [--extras physics|camera|body|sweep|gen] [--camera room|geometric]
python tools/orchestration/tier1_gate.py compare <PREV_ID> <ID>
```

`run` performs, in order:

| # | Check | Command it wraps | Pass condition |
|---|---|---|---|
| G1 | Unit tests, normal and strict | `python tools/orchestration/green_tests.py 5` | every test file ok in both runs (`HOUSE_SIM_STRICT=1` is the second run) |
| G1b | Isolated domains | per domain d: `HOUSE_SIM_DOMAINS=d python -m unittest tests.test_<d>`; kernel test files (`test_kernel*`, `test_perception`, `test_physics_*`, `test_sight`, `test_camera*`, `test_body_*`, sweep tests) with `HOUSE_SIM_DOMAINS=core` (from B1 on) | all ok (the kernel must work without domains) |
| G2 | Authored specs, own modes | combines `tasks/authored/*.json` into `%SCRATCH%/tier1/all_specs.json` (`{"specs": [...]}`) and runs `verify_many` on seeds 0 1 2 41 97 with 4 workers. Human-readable equivalent: `python -m house_sim --check %SCRATCH%/tier1/all_specs.json --workers 4` | trials recorded (below) |
| G3 | Authored specs, forced realistic | the same with `perception="realistic"` (`--perception realistic`) | same |
| G4 | Perception leaks | `python tools/perception_audit.py 6 150 --workers 4` (from CAM-RENDER on also `--camera geometric`) | exit 0 |
| G5 | Layout | `python tools/layout_audit.py 40 --json ...` | counts equal to A0 (GEN also runs its gen-2 configurations) |
| G6 | Fuzz with determinism | `python tools/fuzz.py 6 300 --workers 4` (strict plus a cross-process replay by default; from CAM-RENDER on also `--camera geometric`; from BODY-FAULTS on also `--battery 5 --faults random`) | exit 0 |
| G7 | Benchmarks | `python tools/bench_systems.py 40 0 1 2 3` (from TIME-EVAL on also `--day`) plus package micro-benchmarks | 3.4 |
| G8 | Room-mode golden | `python -m unittest tests.test_room_golden`, and the same with `HOUSE_SIM_GOLDEN_ALL=1` (from B1 on) | green, or re-recorded under 3.5 |
| G9 | Package extras | `tools/physics_audit.py --data --bench` (physics), `tools/body_audit.py` vs the B0 baseline (body), `tests/test_generator_compat.py` and gen-2 layout audit (GEN), `tools/sweep_bench.py` (stage C) | per package |
| G10 | Scope | `git diff --name-only` against the package's file list (section 2) plus its handoff file | no file outside the list |

Recorded per trial for G2/G3: `catalog_id`, `seed`, `perception`, `variant`, `passed`, `initial_fails`,
`idle_fails`, `reference_passes`, each wrong plan's `fails` and `ran`, `reference_state_hash`, `reference_steps`,
`reference_time_s`, `perceivable`. `compare` prints verdict changes, `reference_state_hash` changes, benchmark deltas
and audit deltas. The baseline is A0, re-confirmed at B0. The rule is "no change against the previous gate", never
"everything passes" (A0 records today's verdicts as they are). For stage C, the sweep's `tests/data/spec_baseline.json`
is the B6 gate in the sweep's format.

Since phase 1 of the stabilisation the gate also holds absolute rules (every test and every own-mode spec passes
unless the committed dated allowlist excuses it; second signatures for every verdict change and edit), and after the
phase-1 red team (catalog/analysis/phase1/FIX2.json): tests.json keeps every test id and outcome (a test that
disappears, is skipped or becomes an expected failure fails G1 / G1b / G8 unless an allowlist "skip" entry or a
signed "test-removal" row covers it); every check result carries the fingerprint of the tree it ran on, and a record
whose checks ran on different trees, or that `compare` finds on a changed tree, is STALE; `compare` honours the
record's own statuses and missing data, keeps G2-ABS on the record's full rows and calls a narrowed previous record
INCOMPLETE; policy files are read from the project's commits only. The module docstring of `tier1_gate.py` is the
reference.

### 3.2 What may change and what must not

Every package must keep, unless its row allows otherwise: all verdicts (G2, G3); determinism and strict mode; no
new perception leaks; layout counts; generation-1 houses; the action fingerprint; domain test suites.

| Package | May change (reviewed differences) | Must not change |
|---|---|---|
| A0 | the action fingerprint (timeline move); stubs added | any state or observation digest (the move is behaviour-preserving) |
| Stage A (all six) | nothing: new files, new tests and the listed inert edits only | any verdict or `reference_state_hash` (zero diff in both modes); generation-1 houses; benchmark beyond noise |
| PHYS-INT | R1 `_robot_moved` underscore markers on entities an action changed (reference hashes change); R2 realistic records no longer show thresholds (a spec that reported one was an oracle leak and is fixed); R3 `place` uses `contain.fits` (differs only with declared openings, nest groups, diagonal fits); R4 `place` refuses onto collapsed shelves and broken supports; R5 consequences of the laws active at landing (orphan, overflow, setdown, drip, creep), each occurrence in a reference run listed by `physics_audit`; R6 undo record dropped after a consequence; R7 subtree mass uses `liquids.json` densities (two-hand and capacity boundaries); R8 replays carry `physics_version` (old replays diverge; regenerated at B6); R9 golden re-recorded (R1); R10 only if conflict 31 cannot be made exact: temperatures within 0.05 °C of ambient | oracle observations except consequence notes; the action fingerprint; generated houses; benchmark above 3.4 |
| CAM-RUNTIME | geometric-mode behaviour (opt-in only), new tests | **anything in room mode**: zero verdict and `reference_state_hash` changes in both perception modes, golden byte-identical (state and observation digests), action fingerprint unchanged, room-mode benchmark within 5 % |
| CAM-ACTIONS | the action fingerprint (`face`, `peek`, `look` params; saved states and replays regenerated at B6); `action_names()` hides `face` / `peek` in room mode; spec key `camera`; geometric branches in skills | room-mode verdicts, hashes and golden |
| CAM-RENDER | realistic + geometric enabled; harness, dataset and leaderboard records carry `camera` (harness fingerprint changes); tool flags | room-mode realistic observations (golden), verdicts, hashes |
| BODY-ENGINE (+NAV-M2) | B-R1 new materialized robot keys in every state and in oracle `obs.robot`; B-R2 battery trajectories, `stats.energy` (now from Wh), `energy_wh_*`; B-R3 truncated walks return ok with a "powered down" message and the failure label `robot_stranded` replaces `navigation_robot.stranded`; B-R4 legacy fault strings normalized to dicts; B-R5 golden re-recorded; B-R6 T-5ad61b6881821b-01 and A-d66994a566899c-01 re-checked by hand and still passing | any other verdict; refusal wording of the motion verbs (legacy messages); the action fingerprint (ActionSpec additions are not in it) |
| BODY-CORE (+NAV-M5) | B-R7 derated lifts and grasp classes (pinch notes; power grips under 15 mm refused), every reference hit fixed in its spec before landing; B-R8 `look` / `inspect` / `read` need imaging, `listen` / `ask` need hearing; B-R9 careful-speed plans take 2x instead of 3x normal time (`reference_time_s` changes); a verdict change here is decided per spec and the spec re-authored if needed (decision: correctness over stability); B-R10 setup ops; B-R11 golden diff classes handed to BODY-FAULTS | verdicts other than reviewed B-R7 / B-R9 ones |
| BODY-FAULTS | B-R12 realistic `obs.robot` keys and buckets (integer battery, estimates, buckets); B-R13 hearing filters; B-R14 privacy-mode and power-off visibility; B-R15 golden re-recorded once for both B5 packages | oracle `obs.robot` beyond the B4 additions; verdicts |
| B-CLOSE, C-CLOSE | evidence, replays and datasets regenerated; the `ottoman` type (B6) | any verdict or digest of an existing spec |
| GEN | nothing for generation 1 | `tests/test_generator_compat.py` (generation-1 digests) |
| TIME-EVAL | S-R1 milestones sampled inside waits (the 7 milestone specs of sweep 2 re-verified; a verdict change is triaged with correctness over stability); S-R2 fire-time perceivability (A-8910a09f802fbf-01, T-6203fdc5fa8986-01 re-verified); S-R3 the action fingerprint (`wait`, `watch` params); S-R4 new state sections only when the new features are used | the default `wait` (state digest equal to today), queue-mode `watch`, any other verdict, the golden |
| PEOPLE-IO | S-R5 realistic messages of cognitive actions (recall, diff, snapshot) keep issued handles; S-R6 the action fingerprint (`ask`, `report`, `recall` params); S-R7 `obs.notices` and `obs.request` keys | the legacy `topic` path of `ask` (byte for byte), the legacy `report` store, any other verdict, the golden |

### 3.3 Reviewed-difference procedure

1. Run `compare` against the previous gate.
2. Every verdict change gets a row in the handoff's "Reviewed differences" table: spec id, seed, mode, old -> new,
   cause (one of the package's R codes), evidence (the failing predicate or the changed state path) and the decision:
   kernel fixed, spec fixed (by its owner, with the reason in the spec's `notes`), or accepted.
3. A verdict change that fits none of the package's R codes blocks the hand-over.
4. A `reference_state_hash` change is allowed only for packages whose row allows it; `tools/golden.py diff` (3.5)
   must show that the changed state paths belong to the allowed classes.
5. Domain-test changes are allowed only where a test pinned a value the package deliberately changes; each one is
   listed with the spec section that justifies it.

### 3.4 Performance budgets

The benchmark baseline is A0 on the integration machine. Room-mode whole-engine time per substep and the 1 h wait
must not exceed the previous gate by more than 5 % (noise), and PHYS-INT must not exceed A0 at all (physics claims a
net saving from the thermal active set). Micro-benchmark tests fail at 3x the budget so they are not flaky; the
handoff reports the measured numbers against these budgets.

| Package | Budget (from the layer specs) |
|---|---|
| PHYS-INT | settle substep with nothing touched <= 0.02 ms; settle action typical <= 1 ms, 64-consequence worst case <= 10 ms; `physics.processes` idle <= 0.02 ms, 10 active <= 0.2 ms; `physics.environment` windy <= 0.3 ms (0 when calm or deferred); kernel thermal <= 0.5 ms amortized; `props()` <= 5 µs; data load <= 20 ms; whole engine <= A0 and < 15 ms on any 900-entity house |
| CAM-RUNTIME, CAM-RENDER | viewshed <= 0.3 ms indoors, <= 1.5 ms outdoors; `end_step` <= 3 ms indoors (+0.5 ms motion frames); `camera.watch` <= 0.05 ms per substep and skipped when idle (K-CAM, adoption round: with nobody in view; with people in view <= 0.1 ms, the light and pixels of each agent in view being judged exactly every substep under the domains' light hooks); lidar <= 0.4 ms; `can_see` warm <= 0.05 ms; geometric realistic observe overhead target 3 ms, test bound 10 ms (seed 7, best of 5); look-around <= 4 ms extra; caches within 64 rooms x 32 eyes |
| BODY-ENGINE, BODY-FAULTS | body system <= 0.02 ms per substep; settlement <= 0.1 ms per leaf; motion prefix <= 0.05 ms per 40 cells; proprio <= 0.05 ms per observation; a week-long wait adds <= 0.4 s |
| GEN | `generate_house(gen=2)` <= +20 % over generation 1; generation 1 unchanged |
| TIME-EVAL, PEOPLE-IO | a quiet simulated day (seeds 0-2) no slower than the B6 gate, <= 3.5 s once the domain idle probes land; notice derivation and interrupt check <= 0.03 ms per substep; monitor sampling <= 0.05 ms per substep with nothing touched (<= 0.5 ms with quantified predicates over touched entities); `mind.update` <= 1.0 ms per step on a 900-entity house; `ask` <= 2 ms; `report` <= 1 ms; vocabulary and holiday data load <= 20 ms |

### 3.5 Room-mode golden protocol

* `tests/test_room_golden.py` (PHYS-INT, first commit): seeds 0-3, oracle and realistic, `camera="room"` passed
  explicitly (so the D3 default flip cannot affect it); state and observation digests after reset and after a fixed
  12-action script that includes `look`, `approach`, `pick_up`, a `careful` move, `place`, `open` / `close`,
  `inspect`, `read`, `wait 600` and `wait 3600`; plus the final state digests of the reference plans of 20 fixed specs
  (all specs with `HOUSE_SIM_GOLDEN_ALL=1`). It also asserts that room-mode states have no `camera_log` key and no
  `robot.view` / `robot.facing_deg`, and that `look` lasts 1 s.
* `tools/golden.py dump --out DIR` writes the full checkpoint states and observations as JSON;
  `tools/golden.py diff BEFORE AFTER` prints leaf differences grouped by path pattern (for example
  `entities.*._robot_moved`, `robot.battery`, `obs.robot.*`); `tools/golden.py record` rewrites the digests and
  appends `{"by": <ID>, "classes": [...], "date"}` to the file's `history`.
* A package that changes room-mode behaviour (PHYS-INT, BODY-ENGINE, BODY-FAULTS for both B5 packages, and only if
  needed TIME-EVAL or PEOPLE-IO) runs `dump` before its first edit and again before hand-over, attaches the `diff`
  summary to its handoff, and records only when every changed path belongs to its reviewed classes. CAM-RUNTIME,
  CAM-ACTIONS, CAM-RENDER, GEN and the close steps may not record at all.

---------------------------------------------------------------------------------------------------------------------

## 4. Switch-over: the geometric camera becomes the default for realistic perception

The user decided (TIER1_DECISIONS) that after the domains migrate, geometric becomes the default for realistic
perception (harness episodes and realistic specs) while oracle verification stays in room mode.

**When.** Stage D3, after: stage C; the adoption round's camera items for every domain (section 6, "Camera"); D1 and
D2. It must happen before mass authoring, so that the 2,478 new specs are verified the way the harness will run them.

**What changes in code** (D3, main session):

* `camera.default_mode(perception)` returns `"geometric"` when `perception == "realistic"`, else `"room"`. The
  precedence of 1.13 is unchanged: CLI `--camera`, `HouseEnv(camera=)`, the spec's `"camera"`, the default. Oracle
  runs stay in room mode unless a spec declares `"camera": "geometric"`.
* The golden is unaffected (it passes `camera="room"` explicitly).
* `docs/PERCEPTION.md`, `EVAL_HARNESS.md`, `AUTHORING_GUIDE.md`, `KNOWN_LIMITS.md` state the new default.

**Which specs migrate.** Every spec that is ever run in realistic perception, which means all of them: the 50
realistic specs in their own mode, and all 242 under forced realistic, because the harness runs every split in
realistic perception (`eval/run.py` forces it for dev and train; test episodes are realistic). Procedure:

1. Before flipping, run `tier1_gate.py run --id D3-pre --camera geometric` (forced realistic runs use the geometric
   camera).
2. Triage each failure: (a) the reference plan needs a camera-aware step (look around, approach, kneel and peek,
   read up close); fix it with the geometric-aware skills from CAM-ACTIONS; (b) the success predicate relied on
   something a real camera cannot see (perceivability gap): fix the spec; (c) a kernel defect: fix the kernel;
   (d) pin `"camera": "room"` only when the entry is not visual at all and geometric would only add noise; every
   pin needs a written reason in the spec's `notes` and is listed in the D3 handoff. Pins are expected to be rare.
3. Camera-dependent specs (new or existing) still declare `"camera": "geometric"`, so their oracle verification also
   uses the camera, and keep at least one wrong plan that fails for a camera reason (camera 15; enforced by the lint
   for schema-3 specs).
4. Flip only when every realistic run passes or is pinned.

**Harness** (`eval/run.py`, `agents.py`, `llm.py`): episodes pass `camera=None`, so realistic episodes become
geometric; episode records and leaderboard rows carry `camera`; `--camera room` remains for debugging; the harness
fingerprint changes and earlier leaderboard results become stale (re-run `tools/orchestration/regen_leaderboard.sh`);
the LLM prompt documents `face`, `look`, `peek`, `obs.camera` and `obs.lidar`; the spy and leak tests run in both
camera modes.

**Viewer**: shows the episode's camera mode, the facing arrow and FOV wedge, the seen-cells overlay and frame count
from privileged state; the perceived view shows only what was perceived; old room-mode episodes still load.

**SearchAgent** (`house_sim/agent.py`): in geometric mode it looks around on entering a room (`rows=2` when the target
is small), approaches "object" detections whose size class fits, kneels and peeks under beds and sofas when a room is
covered without a find, and restores what it lifted. Its fetch tests must pass in geometric mode before the flip.

**Datasets and splits**: split ids are unchanged; datasets are regenerated (the observation schema gains the camera
blocks and the perception fingerprint changes); `docs/DATASET.md` bumps the format version.

**Gate for D3**: the full gate with `--camera geometric`; `perception_audit --camera geometric` clean; `fuzz --camera
geometric` deterministic; the geometric observe-overhead test within its bound; SearchAgent passing; verification
evidence regenerated.

---------------------------------------------------------------------------------------------------------------------

## 5. Handoff protocol

**Before starting**, every package reads every existing `catalog/analysis/tier1/handoff_*.md`, this document, the
layer spec it implements and TIER1_DECISIONS.md, and lists the handoff files it read at the top of its own handoff.

**On finishing**, every package writes `catalog/analysis/tier1/handoff_<ID>.md` (LF line endings) with these
sections, in this order:

1. **Summary**: what landed, in five lines or fewer.
2. **Files**: every file created or edited (must match section 2; G10).
3. **APIs**: the public signatures that landed, grouped by module, with any difference from this document or the
   layer spec marked **DEVIATION** and explained.
4. **Deviations from the spec**: behaviour, numbers or names that differ from the layer spec, and why.
5. **Stubs and contracts**: every `TIER1-STUB(<ID>)` marker left in code (`grep -rn "TIER1-STUB"`), what it waits
   for and which package must remove it; every contract consumed from an earlier package.
6. **Gate results**: the gate id, the `compare` output against the previous gate, the reviewed-differences table
   (3.3), the golden diff summary if recorded (3.5), benchmark numbers against 3.4, audit results.
7. **Known gaps**: what is not done or is approximate, with the spec section.
8. **How to test**: the exact commands for the package's own tests and the most useful manual checks.
9. **Follow-ups**: domain follow-ups discovered (added to section 6 by the main session) and items for later
   packages.

**Rules.** A package that finds an error in this document or in an earlier package's work records it in its handoff
under Deviations and fixes it only inside its own files; anything outside them goes to the main session. Stubs must be
removed by the package named in the marker before its own hand-over. The main session merges a package only after
reading its handoff and the gate output.

---------------------------------------------------------------------------------------------------------------------

## 6. Domain follow-ups for the adoption round

Merged from physics section 13, camera section 19, embodiment section 12 and sweep section 22.3 (plus its section 14
type requests), with the changes of section 1 applied (no `item_dropped`; capability limits through the body; sensor
switches through `body.set_sensors`). Kernel packages never edit domain files; the only items that must land with a
kernel package are NAV-M2 and NAV-M5 (section 2.4).

Every domain also:

* runs the physics adoption protocol (physics 10.3: route through kernel functions, guard legacy code with
  `lawbook.active`, register hooks, update tests that pin old constants, fix the type data that `physics_audit
  --data` attributes to it, report "ready to flip");
* adds `"view"` blocks where the camera's category defaults mislead, emits visual occurrences through
  `camera.visual_event`, gates visual observer cues on `camera.in_frame` in geometric mode, and keeps room-mode
  outputs identical for the camera items;
* posts perceivable occurrences as notices (`w.notice`), registers the routes for its `ask` answerer prefix and its
  report vocabulary (issues and states with evidence fields), adds exact idle probes to its heavy systems, and
  furnishes its own types under generation 2 (sweep 22.3);
* fixes any file-level `field_tiers` entry that exposes a physics threshold;
* migrates its privileged-wait specs (M-WAITS) and re-verifies its specs with `--gen 2` (M-GEN2).

"Mass/size" counts are types that still resolve to the default 0.4 kg mass or 0.1 m size.

**admin_inventory**
* Physics: adopt overload (`storage._load_check`, `_collapse`; `SHELF_RATING` / `SURFACE_RATING` into
  `supports.json`; keep `shelf_headroom` as a wrapper) and melt (`MELT_MATERIALS`, `max_safe_temp_c`); packages
  `_break_fragile` -> `physics.transport`; restock `_spill` -> `spill`; curb `_wind` -> kernel; handle law for luggage
  handles; 5 mass.
* Camera: `text_m` / `faces` on mail, envelopes, documents, packages (`label_facing`) and printer displays;
  `verify_label`, `scan`, `count`, `record_inventory` through `camera.read_check` / `camera.acuity` (stacked boxes hide
  faces); doorbell `view.camera` and its feed through `camera.device_frame`; back-row inventory needs `looked_in` plus
  identify.
* Body: `weigh_luggage lift=true` and luggage carrying use `check_lift`; small stationery picks checked with
  `body_audit` at BODY-CORE.
* Sweep: collection days and carrier pickups as calendar events; holiday decor by date (gen 2); belongings data and
  types (wallet, backpack, work bag, passport, rain umbrella, key bowl: homes and sizes); report vocabulary (missing,
  low_stock, expired, overdue, ...); route `admin:`; locale date formats on labels.

**appliances_cooking**
* Physics: adopt contact_heat (`surface_heat_limit_c`), thermal_shock (`thermal_shock_limit`; soda-lime 90 -> 45 K,
  ceramic 120 -> 80, stoneware 120 -> 100; update tests), overload (`build.py` shelf_load copy), coffee `_spill`;
  `thermal_owner: "appliances_cooking"` where `fixed_temperature` means ownership; freezer heat load via
  `heat_content_j`; re-verify cooling times for the `thermal_tau` flip; hot handover burns via `burn_seconds`; 9 mass.
* Camera: fridge/freezer front opening with `interior_light`; oven, microwave and washer-style door windows
  transparent; dishwasher racks top-opening when open; glossy stainless and glass cooktops; displays `text_m` 0.010;
  `visual_event` for flare-ups, smoke, boil-over and arcing; per-field `acuity` for browning and doneness; the senses
  observer gated by `in_frame` (sounds unchanged).
* Body: optional: loud appliances refuse to start in quiet mode through `cycle_check:<type>` with
  `body.quiet_limit_db` (appliance noise is the appliance's, not `noise_db`).
* Sweep: done chimes and beeps as `appliance_done` / `alarm` notices; fault vocabulary; route `appliance:`; idle probe.

**cleaning**
* Physics: `state.break_item` -> kernel break; `topple_risk` -> `knock` (about 0.15 g); handle law for liners;
  register `phys.spill_sink` (`make_spill`; physical columns from `liquids.json`, residues stay); stays drying owner;
  re-key `MATERIALS` chemistry by `physics.material_class`; drop its `thermal_shock_c` column; 11 mass.
* Camera: floor-soil observer and `wet_floor_seen` restricted to `camera.seen_floor` in geometric mode; raking-light
  streaks, fingerprints and residue through `camera.specular` in `perceivable_value`; spills as visual events;
  cushions and pillows liftable (camera C9).
* Body: `wash` / `wipe` of the robot's grippers (target `robot_hand`) calls `body.clean_hand`; vacuum, steam mop and
  carpet cleaner get `noise_db` (about 70 dB) and the check in `floors.py` goes.
* Sweep: soil and stain vocabulary; dust on rarely used items for the history synthesizer; route `cleaning.`.

**climate**
* Physics: adopt furniture_tip (fan `_topple` via `com_frac`), carry (`_other_slosh_model`), `_spill_under` ->
  `spill`; `COMBUSTIBLE_MATERIALS` -> the kernel `combustible` column; keeps humidity (kernel condensation reads it);
  4 mass.
* Camera: `bright_sources` (sunlit apertures, outdoor sun) and `sun_beam` hooks; `light_at` sun patches; compass to
  grid through `sight.grid_deg`; robot screen glare through `sight.specular`; replace `light_level_fast` with the
  kernel value; view blocks for fans, radiators and heater grills.
* Body: keep room `temperature_c` current where the robot is (pack temperature); optional heat-wave and freeze
  variants for battery-derate specs.
* Sweep: outdoor temperature from the weather process when present; thermostat mode by season (gen 2); route
  `climate.`; idle probe for the air step.

**electrical**
* Physics: `phys.wetted` (liquid ingress: short or trip and `electrical_hazard` when powered, `water_damage` until
  dry otherwise); `phys.broke` (dead devices, cracked screens); bulb cool-down via kernel tau and `burn_seconds`;
  check the electronics drop rows (TV 0.3, laptop 0.75, phone 1.0 m); `SOFT_MATERIALS` -> kernel; 13 mass, 7 size.
* Camera: `light_at` lamp pools; `bright_sources` for bulbs, lamps and screens; flashlight `view.beam` {1.8, 15};
  remote IR `line_of_sight` -> `sight.los`; screens glossy with `text_m` 0.03; laptop webcam `view.camera` {78, 4 m};
  the `electrical.light` observer only for lamps in frame; replace the `light_level` copy in `harms.py`.
* Body: keep the robot pack out of the `charge` verb except on a robot charging station; optional live-wire contact
  fault.
* Sweep: smart-home furnishing by tier (gen 2) and the hub model (automations, scenes, schedules, away mode,
  app-visible state, notifications); device notices; smoke/CO alarms as `alarm` notices; phones and chargers for
  belongings (`phone_of`, charging spots); DST-aware clocks; vocabulary; idle probes for power, lights and detectors.

**food**
* Physics: adopt crush (packaging rows for bread, chips, eggs), `model.spill` -> `spill`, shelf_load copy -> kernel;
  keep food thermal and phase (`thermal_owner: "food"`); `phys.broke` (raw-egg spill), `phys.props` (ripeness ->
  softness), `phys.fell` (bruise, not break); `LIQUID_DENSITY` -> `liquids.json`; `freeze_burst_check` for food jars;
  produce shape sphere -> rollable; the `thermal_tau` flip together with plumbing_liquids; 12 mass, 6 size.
* Camera: pantry and fridge rows through `inner_spos` / setup `row`; `text_m` for expiration and best-by; glass jars
  glossy and transparent; `acuity` for freezer burn, mold and pantry pests; visual events for boil-over, spills and
  pests.
* Body: declare `hand_contamination` (hidden); coating, flouring and oiling verbs call `body.soil_hand`; optional
  `require_precision` for fine verbs.
* Sweep: diet and allergies from profiles in serving checks; meal log for history; locale best-by formats; the
  `lunch_box` type for belongings; route `food:`.

**manipulation**
* Physics: adopt crush, stack, edge, slope, carry, wind, hang, handle, contact_heat, wet_contact, open_spill and
  furniture_tip (`tip_check`); `common.py` break, spill, impact, drop, padding, slosh, topple, scratch, friction and
  `max_load` become kernel wrappers; its perturbation events delegate to `physics.*`; `manipulation.sounds` drops the
  kinds the kernel now emits; register `phys.items_above` (packing order); `grip_outcome` reads kernel mu and crush;
  fix the `docs/domains/manipulation.md` table; tests updated for glass 0.10 -> 0.5 m and crystal 0.05 -> 0.25 m;
  4 mass.
* Camera: `rotate`, `flip`, `set_orientation`, `arrange` update `yaw_deg` / orientation (and so faces);
  `_blocks_view` -> `sight.carried_occluder`; `world_pose` now gets a real `robot.facing_deg` in geometric mode: check
  robot-relative placements; decor and vases get porosity/transparent blocks.
* Body: `require_payload` / `needs_two_hands` -> `ctx.check_lift` / `body.capacity_kg`; `support mode=heft` uses the
  derated capacity and never counts an overload; `ROBOT_PUSH_N` / `ROBOT_WRIST_TORQUE_NM` -> `body.limit`, force
  parameters clamped with a note; the 90 N firm grip is clamped to `body.grip_limit_n`; the stair stumble drops the
  item through `body_faults.drop_held` and stops counting `falls` unless the robot falls; extra drop effects (if any)
  through `phys.fell` / `phys.broke`; throw/catch accuracy from `precision_mm`; grasp slip from `grip_mu`; keyed
  connector insertion through `require_precision`; `noise_db` on loud verbs.
* Sweep: the `coaster` type (it protects finishes under the wet_contact law); route `manip:`.

**mechanisms**
* Physics: adopt furniture_tip (`closures._tip_over`), hang (curtain rods), `fall_item` -> kernel `fall` (fixes the
  spill statistic with no spill), `_wind` and the wind_gust/rain events -> kernel; register `phys.tip_loads` for open
  drawers; `anchored` as an explicit per-instance furnisher draw, 25 % for tall storage furniture until a cited survey
  figure is found (decision; documented in `KNOWN_LIMITS.md`; re-records `gen1_digests.json` in its own commit);
  `SOFT_MATERIALS` -> kernel softness; 10 mass.
* Camera: sheer coverings `view.porosity` 0.6; glass doors and panels porous; the door-leaf occluder comes from the
  kernel; the `mechanisms.doorways` glimpse is replaced by sight doorway apertures in geometric mode (camera phase 3);
  keypads and touchscreens glossy with `text_m` 0.006.
* Body: `grip_force_n` clamped to `body.grip_limit_n`; `lids.WRIST_MAX_NM` -> `body.limit(state, "wrist_nm")`;
  `FORCE_N` levels clamped to `push_n`; optional keypad precision.
* Sweep: the smart lock for the connected tier; routes `code:`, `key_location:`.

**navigation_robot**
* Body, must land with BODY-ENGINE (NAV-M2) and BODY-CORE (NAV-M5): section 2.4.
* Body, later: `ev_robot_fault` delegates to `robot.fault`; `robot_falls` -> `body_faults.fall`; `drop_item` ->
  `body_faults.drop_held`; `log_fault` -> `body_faults.log_fault`; `swap_battery` -> `body.swap_pack` with packs of
  2.4 kWh, about 14 kg, two-handed (decision); `clean_camera` clears `camera_dirty` and scratches through
  `degrade_camera`; `get_up` uses `self_right_problem`; new verb `run_diagnostics`; `request_remote_help` clears
  `localization_lost` and `software_stuck`; drop the `_no_camera` before-hooks and `measure.py` privacy checks in
  favour of `ctx.require_imaging`; `set_privacy_mode` gains `camera` / `mic` / `policy` params calling
  `body.set_sensors`; `obs.nav` through proprio buckets; declare its robot fields; `_sudden_encounter` uses
  `body.stopping_distance_m`; `stairs.py` / `reach.py` use `body.capacity_kg`, `stability_margin` and
  `held_envelope`; `ROBOT_MASS_KG`, width, `SOUND_AT_1M_DB`, `SPEECH_DB` come from the body; `climb_on` refuses in limp
  mode; weather grime through `degrade_camera(cause="weather")`; `_spoke` checks `entry["audible"]`.
* Physics: adopt carry (`traverse._slosh`); `drop_item` / `break_entity` / `spawn_spill` -> kernel; remove the
  `"stack" in ACTIONS` check; register `phys.carry_motion` (stairs x1.8, threshold x1.15, slopes); robot falls drop
  held items through `body_faults` (which calls `impact.fall`); keep traction and stairs limits; climbing aids keep
  `rated_kg` (physics reads it); 5 mass.
* Camera: `nav.visits` coverage reads `camera_log` in geometric mode; patrol viewpoints through
  `sight.best_viewpoints`; `robot_headlamp` `view.beam` {0.45, 30}; `camera_frame`, `evaluate_sightlines`,
  `_floor_in_view`, `view_blocking_items` and `startle` move to sight; the entry-based privacy capture is skipped in
  geometric mode; rain and outdoor grime lower `camera_clean`; robot motion verbs produce motion frames (through
  `ctx.motion` records).
* Sweep: exterior same-floor stairs traversal (GEN's porch steps); route `nav:`.

**outdoor_plants**
* Physics: adopt wind (`_object_wind`, `_cover_wind`, `umbrella_tip_check` become the kernel environment formulas
  with its calibration rows), rain on generic objects, `add_spill` -> `spill`, slope (wheeled bins and carts on
  `slope_deg`); keeps weather, plants and soil; register `phys.exposed` (porch roof, cover, umbrella); pot drainage
  via `drip_start`; `event:wind` / `start_rain` feed the kernel when the laws are active; 23 mass.
* Camera: `view_occluders` for tall grass (porosity 0.5), hedges (0.3) and open patio umbrellas; room `fog` for haze;
  lawn survey through outdoor coverage; small parts in grass; gutters with face "up" seen only from a raised eye.
* Body: `lawn.py`, `yard.py`, `pool.py` falls -> `body_faults.fall` (pool with `water=True`, no more string fault);
  snow too heavy becomes an overload-attempt record; sprinkler, hose and pool exposure -> `body_faults.expose_water`;
  mower, blower and trimmer get `noise_db` and the quiet checks in `verbs.py` go; mud and sap -> `soil_hand`.
* Sweep: the stateless weather process and forecasts (sweep 13.2) with `override_until_s`; `forecast_for`; season
  ground state (gen 2); exterior steps (ice, snow, doormat outside); notices (thunder, rain on windows); plant-issue
  vocabulary; route `plants:`.

**people**
* Physics: `lift_kg` / `push_n` per person and a helper path for team lifts (with manipulation co-carry through
  `lift_plan(helpers=...)`); `phys.fell` / `phys.tipped` injuries for people in fall cells; burns via
  `burn_seconds`; the children system can trigger `physics.pull`; 7 mass.
* Camera: `sight.person_viewer` / `person_eye` replace `eye_point` (same constants); `startle`, approach-from-front
  and gaze use sight eye heights and LOS; `frame_analysis`, `_line_blocked`, `frustum_items`, `private_in_frame` move
  to device and person viewers; implement `private_activity` and `camera_capture` (violations by consent); skip the
  `_entering` sensor capture in geometric mode; pose and gesture observers gated by `in_frame`; child hazards as
  visual events; supervision metrics from `camera_log`; video-call `view_watch`; phase 4 visitor recognition.
* Body: hearing through `body.hears` wherever `heard_by_robot` is set; mark speech addressed to the robot
  (`addressed_robot`); suppress ambient speech content in the sounds channel when hearing is "addressed"; listeners
  ignore inaudible robot speech; `FOOTSTEP_DB` / `SPEECH_DB` / `QUIET_SPEECH_DB` -> `body.noise_db`; register
  `robot_sound` as a sound source; privacy capture counts use `body.imaging`; later, residents can swap the battery
  of, or dock, a powered-down robot.
* Sweep: routes for its prefixes if PEOPLE-IO did not; the channel, language, render and reliability hooks;
  `report_delivered`; `robot_hears` (propagation only, 1.3); notices (doorbell, knock, crying, call for help, phone
  ringing); request delivery (gestures for requests, recognition, per-requester phrasing); the calendar model,
  calendar artifacts, `check_calendar`, schedules from events, `event_needs.json` and its helper; profiles to facts;
  the `people.history` synthesizer; feedback rules; the activity engine (medium); infant, toddler, teen and roommate
  behaviours and furnishings (cribs, high chairs) for gen 2.

**pets**
* Physics: adopt carry (`water._slosh`); knocking via `knock` / `nudge` (cats push items off edges; tail sweep 0.3 g
  at <= 0.6 m); `phys.fell` / `phys.broke` -> startle; scenarios' shelf_load copy -> kernel; pet-caused consequences
  are exogenous unless the robot arranged the item; 4 mass.
* Camera: occluder heights by species and size (`view.height_m`); porous crates and cages; transparent glossy
  aquariums; visual events for escapes, accidents, counter-climbing and fights; the `pets.cues` observer gated by
  `in_frame`; dark coats drive `k_contrast` through `color`; pet supervision through `track`.
* Body: stress and startle from robot noise and speed through `body.noise_db` / `body.speed_mps` or `robot_sound`.
* Sweep: gen-2 pet furnishing (aquarium, bird cage, small-animal cage, terrarium, coop) from the composition; pet
  notices (barking at the door, whining, bird alarm calls); symptom vocabulary; route `pet:`; idle probe for the pets
  tick.

**plumbing_liquids**
* Physics: adopt carry (`leaks.slosh_spill_l`), overflow and spill (`spill_near`), handle (buckets), absorb
  (`absorb_capacity`; `wring` stays), phase (`ice.py` declares `thermal_owner` or uses kernel phase); `do_pour` uses
  `transfer`, `pour_rate_lps` and the thermal-shock check; densities from `liquids.json`; drop the
  `carry_slosh_applied` flag; the `thermal_tau` flip together with food; 4 mass.
* Camera: under-sink cabinets front-opening with no interior light (needs a beam); leaks and drips as visual events;
  water heater `behind_gap_m`; toilet tank top-opening with the lid off; floods and puddles shown only on seen cells.
* Body: `fill` / `pour` into a held container capped by `body.hold_capacity_left`; hose spray at the robot or a
  running shower -> `body_faults.expose_water`; optional `soil_hand("water")`.
* Sweep: drips and leaks as notices; leak vocabulary with evidence fields; route `plumbing.`; idle probe for the water
  system.

**safety**
* Physics: adopt furniture_tip (`events.tip_over` -> kernel: orientation `on_side`, no "toppled X" relabel),
  `event:safety_tip_over` -> `physics.pull`; `phys.broke` -> a shards entity for brittle breaks on floors;
  `phys.fell` / `phys.tipped` -> injury; `choking_hazard()` for childproofing; `phys.contact_heat` ignition
  thresholds; 6 mass.
* Camera: `view_occluders` for the smoke layer and steam; room smoke feeds `k_haze`; visual events for fire start,
  smoke and pest sightings; glass slivers glossy; `outside_view` -> aperture viewers; `doorbell_camera` ->
  `camera.device_frame` (IR at night); "keys visible from the entry" through a `view_watch` helper.
* Body: the `inspection.py` structure collapse uses `body_faults.fall(height_m=...)` and `body.total_mass_kg`;
  extinguisher handling uses `check_lift`; capability-envelope helpers use profiles and randomized capacity;
  optional: smoke and soot `degrade_camera`, energized flood water raises a robot fault.
* Sweep: the `candle` type (lit state, fire rules); alarms as notices; hazard vocabulary; route `safety.`.

**textiles_laundry**
* Physics: adopt melt (iron vs synthetic `melt_c`), `textiles_laundry.transport` -> `physics.transport`;
  `phys.props` (wet mass = dry + wetness x absorb); absorb capacities from the kernel table; ratings for drying racks,
  clotheslines and hangers (hang law); hanger slip via kernel friction; mildew timers stay; 9 mass.
* Camera: washer and dryer drums front-opening with a transparent door window; drying racks with laundry as porous
  `view_occluders`; hampers top-opening with lids; `care_label` `text_m` 0.0025 (wrist or held) with an optional
  face; stain `acuity` (identify for large stains, inspect for small); the senses observer gated by `in_frame`.
* Body: `thread_needle` `require_precision(0.5 mm)`, relaxed with a needle threader; `sew_button` 1.5 mm; folding thin
  items with a parallel gripper follows the core thin-flat rule.
* Sweep: types `jacket`, `sofa_cushion`, `seat_cushion` (and a hoodie); seasonal clothing placement (gen 2);
  outerwear and footwear data for belongings; vocabulary (stains, tears, pilling); route `laundry:`.

**tools_vehicles**
* Physics: pads, sliders, felt and casters feed kernel foot materials; wall anchors and hooks use kernel hang ratings
  (`anchor: stud | drywall_anchor | adhesive_*`); dolly or hand truck as a `lift_plan` alternative (rolling `c_rr`);
  trunk loads via `physics.brake` (0.4 g); ladder ratings (ANSI I/II/III); an anchor kit sets `anchored`; 7 mass.
* Camera: vehicle interiors and trunks get opening blocks; glass transparent; optional rear camera as a device
  viewer; tool damage `acuity` (inspect); porous ladders; top-opening toolboxes; flat-pack small parts follow the
  detection ranges.
* Body: drill, hammer and pressure washer get `noise_db` and the hand-written quiet checks (`tools.py` x2,
  `surfaces.py`) go; torque and force inputs clamped to `body.limit`; two-handed power tools declare `arms=2`.
* Sweep: tool-fault vocabulary; route `tools_vehicles.`.

**Main session (non-domain follow-ups)**
* The `ottoman` core type (B6); other core furniture variants through props of existing types (sweep 14.5).
* Camera phase 3 kernel items: doorway and window apertures (`sight.py`) and doorway captures with policy masking
  (`camera.py`); phase 4: the D3 re-verification, optional mirrors and visitor recognition.
* D1 law flips, D2 tool default gen 2, D3 camera default.

---------------------------------------------------------------------------------------------------------------------

## 7. Decisions the user may revisit, and risks

**Decisions taken in this document** (each is in the conflict register):

* The robot's head camera sits at 1.50 m standing (body profile), not 1.35 m; the camera spec's worked numbers are
  recomputed (conflict 4).
* Privacy mode and room mic policies keep addressed-speech hearing; only an explicit `mic_on: false` or a mic fault
  makes the robot deaf (conflict 6, following the user's privacy-mode decision).
* `look` is refused while the cameras are off, in both modes (conflict 20).
* Overhang through core `place` stays setup-only in tier 1 (conflict 29).
* The golden recording moves to the start of PHYS-INT (conflict 14).
* `"schema": 3` is the one marker of a tier-1 spec for every layer's authoring rule (conflict 36).
* The sweep's Phase 0 (timeline split, stubs, generation-1 digests) runs at A0, before stage A (conflict 32).

**Risks.**

1. The laws active at physics landing (orphan, overflow, setdown) can change outcomes wherever a domain leaves liquid
   above capacity or orphans an item, even briefly. PHYS-INT must scan every reference run with `physics_audit` and
   list each occurrence.
2. `_robot_moved`, the new robot fields and the battery trajectories change state digests everywhere, so every saved
   state, replay, dataset and verification record is regenerated at B6 and C1.
3. The critical path is long: B1 to B5 are sequential, two packages pair with navigation_robot, and stage C waits for
   all of them because the sweep touches the most shared files.
4. The geometric realistic observe overhead (bound 10 ms) shares budget with the realistic overhead already measured
   at 3.2-5 ms.
5. The careful-speed fix may change verdicts in about 6-7 spec files; per the decision those specs are re-authored.
6. Golden re-records could hide unintended changes; the structural diff classes of 3.5 are the mitigation.
7. The switch-over (D3) affects every spec under forced realistic, not only the 50 realistic ones; it can be a large
   triage, and reference plans built on room-mode visibility will need camera-aware steps.
8. The thermal active-set optimisation may not be exactly digest-neutral (conflict 31).
9. The action fingerprint changes at A0, B3 and C; each change invalidates saved states and replays until they are
   regenerated.
10. File-level `field_tiers` load order lets a domain re-expose a hidden threshold; PHYS-INT's audit lists them and
    each domain fixes its own.
11. The touch log becomes append-only within a step; without `trim_touched()` a week-long wait would accumulate
    entity ids. The engine must trim after every substep (1.8), and a test must bound the log on a 7-day wait.
12. TIER1_SWEEP.md was written in parallel with this plan; its sections 3 and 22 were reconciled here (conflicts
    32-43). Any later edit to it that changes file sets must be mirrored in section 2.2.
