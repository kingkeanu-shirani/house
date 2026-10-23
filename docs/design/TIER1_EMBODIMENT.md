# Tier 1 design: robot embodiment (kernel body model)

Status: implementable specification, written by the layer judge. Nothing here is implemented yet. It targets the
first kernel round after kernel round 5 (v2.9) has landed and been committed. Python 3.9 standard library only,
deterministic, replayable, strict-mode clean.

Contents: 0 inputs and verdict, 1 goals, 2 ownership, 3 data model, 4 processes and formulas, 5 APIs, 6 realistic
perception, 7 determinism, 8 compatibility and migration, 9 performance, 10 tests, 11 kernel work packages,
12 domain follow-ups, 13 spec recipes, 14 open questions.

---------------------------------------------------------------------------------------------------------------------

## 0. Inputs, verdict and what changed

**Inputs.**

* Design A, "BODY-MODEL-FIRST": one kernel body model (`kernel/body.py` + `data/robot/profiles.json`) owning body
  state, limits, energy, faults and self-perception; actions reach it through `ctx` helpers. Its text reached the
  judge cut off at the start of its migration section.
* Design B: absent from the judge's input. The payload said two designs follow, but only one array element arrived.
  I compared A with the strongest alternative angle I could construct, called B' below: "capability contracts,
  domain-federated". B' keeps the flat robot record and adds a kernel capability-query registry, while energy stays
  in navigation_robot, payload in manipulation and hearing in people.

**Verdict: A's body model, amended.** B' leaves the robot's physics dependent on which domains are loaded. A
`HOUSE_SIM_DOMAINS=core` build would have a robot with free energy and no payload rules, and verification could
differ between builds. B' also keeps today's duplicates: 60 kg in two domains, two noise tables, six copies of the
mobility guard. And no one would own the perception tier of robot fields. Ideas grafted from B':

* Domains keep the causes: traction, what makes the robot fall, where water comes from, which dock delivers power,
  how sound travels. The kernel keeps the state and the generic consequences.
* Domain robot fields are declared (owner, tier, doc), not copied into every state.
* Landing is staged by capability, with one gate per stage (section 8).

**Changes to A.** Each is explained where it applies.

1. **Power-down is a consequence, not a non-atomic refusal.** `ok=False` keeps meaning "refused and rolled back".
   A returned `ok=False` without a rollback and added a `PowerDown` exception outside `ActionError`. Here, a robot
   that dies partway through a walk gets `ok=True` and a message that says it powered down, just as a fall during a
   walk does today (section 4.6).
2. **Energy is settled per leaf action**: per top-level step and per nested `env.perform`, not once per top-level
   step. Under A, `ctx.work_wh` set on a nested ctx would be lost, and chains (`go_to`, `patrol`, `follow`, `lead`)
   would plan later legs against a stale battery.
3. **Motion record contract.** `ctx.data["motion"]` plus `ctx.cut_motion` lets navigation_robot's consequence engine
   (`traverse`) charge what was actually walked, and keeps it from running doorway or stairs effects for a move that
   was cut short. traverse can stop the robot, or make it fall, after core has moved it.
4. **Cold acts through a temperature-dependent cutoff on the true state of charge.** A cold pack strands the robot
   earlier and gives the energy back when it warms. A instead multiplied the drain, and charge lost that way never
   came back.
5. **Standby is "parked" with joint brakes**: 20 W standing, 12 W sitting or lying. A assumed 15 W while standing,
   which ignores balance holding for a humanoid. Checked against the longest verified spec (A-d66994a566899c-01,
   which waits up to 60 h undocked): it still passes, using about 50 %.
6. **Charging.** While the robot is seated on a live dock, the dock carries the hotel load and the charger current
   goes into the pack. A had both "r - draw" and "the dock supplies the hotel load".
7. **Narrower lift derates and an audit.** The above-the-head derate applies only above `height_m + 0.1` (1.7 m),
   not above `reach_m - 0.25` (1.6 m), which would have changed ordinary shelf picks. A pre-landing audit checks it.
   Small items below the power-grasp size get an automatic pinch grasp unless a power grip is explicitly requested.
   Items below the pinch size cannot be grasped; A's tweezers path is dropped because a hand cannot hold the
   tweezers and the item at once.
8. **Privacy mode keeps addressed-speech listening** (on-device wake word): "stop", "get out" and consent answers
   are still heard. Cameras and ambient audio are off. A turned the microphones fully off, which conflicts with
   knock-and-consent entries and with stop commands.
9. **Careful speed is no longer slowed twice.** A careful `move` now takes 3 times the normal time: it walks at
   0.4 m/s and the engine then multiplies by 1.5; `go_to` multiplies its legs again. The fix is its own migration
   step with its own gate (M5).
10. **Motor thermal model deferred.** No example entry needs it; the capacity hook it would use is kept.
11. **Concrete work packages** with disjoint file sets, a gate for each stage, and the domain items that must land
    together with each stage.

### 0.1 Claims about existing code (verified)

Line numbers in the inputs refer to the round-5 working copy and will move; search by function name.

| Claim | Verdict |
|---|---|
| Flat robot record `schema.ROBOT_DEFAULTS` | Confirmed. It has no `mass_kg`; `navigation_robot/common.py ROBOT_MASS_KG = 60.0` and `safety/inspection.py ROBOT_MASS_KG = 60.0` duplicate it. |
| Flat per-action energy: `engine.step` subtracts `spec.energy`, `actions.action(energy=0.01)` | Confirmed. `wait`, `look`, `listen`, `read`, `report`, `speak` and the control verbs declare `energy=0.0`, so a 7-day wait is free. Kernel-only `move` costs 0.01 % whatever the distance. About 350 explicit `energy=` values range from 0.001 to 0.05. |
| Walking and stair drain exist only with navigation_robot (`traverse.bookkeeping`) | Confirmed: 0.012 %/m x (1 + kg/40) and 0.05/0.02 % per stair step up/down. The drain is applied after the whole path, so the robot always arrives, possibly at 0 %. |
| Dock charging, opt-in idle drain, stranded invariant | Confirmed: `robot_system` charges a linear 2 %/min (`robot_dock.charge_rate_pct_per_min`), `idle_drain_pct_h` is opt-in, and the invariant is `navigation_robot.stranded`. |
| Guards copied in many places | Confirmed: core `move`, `approach`, `walk_to`, the posture actions, `Ctx.obj`, and navigation_robot `step` (`paths.py`). |
| Payload rule in `pick_up`: 8 kg per hand, 20 kg two-handed, on the subtree mass | Confirmed. The refusal has category `safety` and leaves no record; `manipulation/common.py require_payload` duplicates it. |
| Force limits are domain constants | Confirmed: `ROBOT_PUSH_N` 220, `ROBOT_WRIST_TORQUE_NM` 6, `lids.WRIST_MAX_NM` 6; `controls` accepts `grip_force_n` up to 600 uncapped. |
| Quiet mode is enforced per domain | Confirmed: `outdoor_plants/verbs.py` (2), `tools_vehicles/tools.py` (2), `tools_vehicles/surfaces.py`, `cleaning/floors.py`, people footsteps -4 dB. |
| Privacy mode only refuses `inspect`/`read`/`measure` | Confirmed: before-hooks in `robot.py` and checks in `measure.py`. The observation is unchanged and `pick_up` still finds small items. |
| Camera clarity thresholds | Confirmed: 0.4 in `perception.visible`/`redact` and core `inspect`/`read`; 0.7 for realism noise. |
| Faults are untyped | Confirmed: `pool.py edge_event` appends the bare string `"water_ingress"`; navigation_robot appends dicts. |
| Ad-hoc falls | Confirmed: `lawn.py`, `pool.py`, `yard.py` (snow overload sets `fallen` and still returns success) and `safety/inspection.py`. `manipulation/handling.py carry_dynamics` counts `falls` for a stair stumble in which the robot does not fall. |
| Realistic mode leaks exact self-state | Confirmed: `obs.battery` to 3 decimals, `obs.nav.battery_pct` to 2, exact `camera_clean`, every fault listed. |
| A failed arm keeps holding its item | Confirmed: `ev_robot_fault` only flips `arms_ok`. |

**Additional findings (not in A).**

* `engine.step` multiplies physical durations by 1.5 in careful mode, after core `move` has already walked at
  0.4 m/s.
* Nested actions (`env.perform`) never pay energy. `go_to`/`patrol`/`follow`/`lead` legs are charged only by
  `traverse.bookkeeping`.
* `traverse._after_motion` treats every core `move` as a doorway passage, so a move that ended in the room it
  started in would run passage effects.
* Exposure of the 242 verified specs:
  * none uses privacy mode, quiet mode or robot faults;
  * one checks `robot.battery` (T-5ad61b6881821b-01: `gt 19` after docking);
  * the longest budget (A-d66994a566899c-01, 64 h) waits up to 60 h undocked;
  * 7 spec files use careful speed.
* Measured baseline (seed-0 house, 649 entities, all domains, this machine): 2.2 ms per 30 s substep for the whole
  engine, 15.5 ms per `look` step including the observation, and 0.05 ms for `subtree_mass` of two held
  containers.

---------------------------------------------------------------------------------------------------------------------

## 1. Goals and non-goals

**Goals.**

* One authoritative robot body: its state, limits, energy, faults and what it perceives of itself. The kernel owns
  it, actions reach it through `ctx` helpers, and domains and tools use `kernel.body` functions.
* Honest energy:
  * every second and every metre costs energy;
  * running out has a physical consequence where it happens;
  * charging takes realistic time, and cold and heat matter.
* Every consequence can be checked by specs: minimum battery, power-downs, overload attempts, detected and logged
  faults, falls, drops, sensor states.
* Capability can vary through named profiles and randomizable fields, so that both a refuse-everything policy and a
  try-everything policy fail on some seed.
* One shared answer to "can the robot see, hear, move, lift, grasp, push, speak right now", so that every action and
  the observation agree.
* With the default profile and no injected faults, the existing tests and the 242 verified specs keep passing in
  both perception modes. The exceptions are listed and handled in section 8.

**Non-goals, and what stays approximate.**

| Topic | Model | Fidelity |
|---|---|---|
| Energy | Lumped power table plus a per-metre walking cost. Calibrated to public humanoid figures. The walking cost keeps navigation_robot's 0.012 %/m (cost of transport about 1.8, the conservative end). | P-approx |
| Battery | State-of-charge bookkeeping, idealized CC/CV charging, first-order pack temperature, generic Li-ion cold table. No aging within an episode. | P-approx |
| Balance and dynamics | None. Falls are rule outcomes of causes in domains (traction, overload, structures, water). | S/G |
| Lift capacity | Per-hand rating x discrete derates (extra overhead cell, above the head, on a platform, critical battery, arm health). | G |
| Dexterity | Grasp size classes, a scalar positioning precision in mm, force caps. No contact mechanics. | S |
| Sensors | Availability and quality states. No images. Camera geometry belongs to the camera layer. | S |
| Faults | Injected by scenarios or caused by explicit events (falls, water, overheating). Intermittent faults use deterministic draws. | S |
| Robot noise | dB-at-1-m tables; propagation and listeners live in domains. | P-approx |
| Stopping distance | Kinematic formula (reaction time plus braking). | G |

Not modeled: joint torques, motor heating (deferred; hook kept), wake-up latency from standby, pack aging, partial
progress of interrupted non-motion work, electrical faults inside the robot, people rescuing a powered-down robot
(a later people-domain feature).

---------------------------------------------------------------------------------------------------------------------

## 2. Ownership and cross-layer contracts

| Concern | Owner |
|---|---|
| Body schema and field registry, profiles, energy integration, power states, thresholds, lift capacity, grasp/precision/force limits, sensor availability, fault registry and generic fault effects, generic fall/drop, proprioception rendering, the `robot_stranded` invariant | kernel: `kernel/body.py`, `kernel/body_faults.py`, `kernel/proprio.py`, plus hooks in engine, actions, perception, realism, schema |
| Body checks inside core verbs (`move`, `approach`, `walk_to`, posture, `pick_up`, `receive`, `uninstall`, `inspect`, `read`, `look`, `listen`, `ask`, speech, `set_speed`, `set_quiet`) | core (`systems/core.py`) |
| Body setup ops (`robot` profile, `robot_fault`) | tasks (`tasks/setup.py`) |
| Self-care verbs (`dock`, `undock`, `swap_battery`, `clean_camera`, `get_up`, `set_privacy_mode`, `request_remote_help`, `log_fault`, new `run_diagnostics`); the dock as charge source; traction and causes of falls; weather grime; foot soil | navigation_robot |
| Drop consequences (breakage, spills); force-specific verbs | manipulation (through the `item_dropped` hook); mechanisms and manipulation clamp forces to body limits |
| Who hears robot sounds and speech; privacy capture | people |
| Robot hand contamination (food safety) | food (registered hidden field) |
| Washing the robot's grippers | cleaning |

**Cross-layer contracts with the other tier-1 layers.** Their designs were not visible to this judge;
reconciliation is open question 7.

* **Physics layer (object physics).** It owns object mass, stability, tipping, impact breakage, friction and object
  heat. The body supplies:
  * the robot-side limits that force-based mechanics clamp to: `capacity_kg`, `grip_limit_n`,
    `limit(state, "push_n" | "wrist_nm")`;
  * the robot's total mass for load ratings (`total_mass_kg`);
  * dropped items, delivered through `run_hooks("item_dropped", ...)`. If the physics layer adds a kernel drop
    function, `body.drop_held` calls it instead.

  Robot-side payload checks are the body's: the lift rule, overload observability and heft. The physics tag list
  also carries `robot.payload_limit`, `payload.mass_limit` and `object.mass_lift_two_handed`; treat those as body
  requirements.
* **Camera layer.** It owns what is visible from where: field of view, occlusion, detection by distance, and gaze or
  heading if it adds them. It reads `body.sensors_view(state)` for availability and quality, and the profile's
  `camera_height_m` for the mount height. A heading field registers through the robot field registry. The body never
  computes visibility geometry. `Perception.visible` keeps the single imaging gate (section 4.10), so every action
  guard and the observation agree.
* **navigation_robot.** It keeps traction, the causes of falls, stairs and doorway checks, the dock and its power,
  and foot soil. It calls `body.fall`, `body.cut_motion`, `body.capacity_kg`, `body.stability_margin` and
  `body.stopping_distance_m`.

---------------------------------------------------------------------------------------------------------------------

## 3. Data model

### 3.1 Profiles: `house_sim/data/robot/profiles.json` (new)

`robot.body_profile` (default `"humanoid_v1"`) selects a profile. `"inherits"` deep-merges a parent profile.
`robot.profile_overrides` (optional dict) deep-merges over the selected profile for one episode, for example to vary
`power_w.hotel_awake` in a spec. `humanoid_v1` reproduces every robot number that exists today.

```json
{"version": 1,
 "profiles": {
  "humanoid_v1": {
   "doc": "60 kg, 1.6 m general-purpose humanoid (Optimus / Figure / Apollo class)",
   "mass_kg": 60.0, "height_m": 1.6, "width_m": 0.5, "sideways_width_m": 0.36, "camera_height_m": 1.5,
   "reach_m": 1.85, "max_hand_kg": 8.0, "max_mass_kg": 20.0, "push_n": 220.0, "wrist_nm": 6.0,
   "grippers": {"left": "hand", "right": "hand"},
   "battery_wh": 2400.0,
   "thresholds_pct": {"low": 20, "reserve": 15, "critical": 5, "cutoff": 0, "wake": 3},
   "power_w": {"hotel_awake": 70, "hotel_standby": 12,
               "stance": {"standing": 45, "crouching": 60, "kneeling": 40, "sitting": 5, "lying": 5, "on_aid": 55},
               "standby_stance": {"standing": 8, "crouching": 8, "kneeling": 3, "sitting": 0, "lying": 0, "on_aid": 55},
               "arm_active": 40, "hold_per_kg": 3, "perception": 5, "speech": 5},
   "auto_standby_s": 60,
   "walk_wh_per_m": {"careful": 0.264, "normal": 0.288, "fast": 0.384}, "load_ref_kg": 40.0,
   "stair_wh_per_step": {"up": 1.2, "down": 0.48},
   "speeds_mps": {"careful": 0.4, "normal": 0.8, "fast": 1.2}, "decel_mps2": 2.0, "react_s": 0.3,
   "noise_db_1m": {"careful": 30, "normal": 36, "fast": 44, "quiet_delta": -4, "speech": 60, "speech_quiet": 45,
                   "actuator": 35},
   "quiet_limit_db": 55,
   "charge": {"cv_from_pct": 80, "full_pct": 99.9, "min_c": 0, "half_below_c": 10, "max_c": 45},
   "pack_thermal": {"tau_s": 1800, "self_heat_c": {"off": 0, "standby": 1, "idle": 3, "active": 6, "charging": 8},
                    "overheat_c": 60, "recover_c": 50},
   "energy_buffer_pct": 0.5,
   "ip_rating": "IP54", "spray_limit_s": 600, "jet_limit_s": 3,
   "stairs": true,
   "fault_p": {"gripper_slip": 0.3}
  },
  "humanoid_compact": {"inherits": "humanoid_v1", "doc": "35 kg, 1.3 m compact humanoid",
   "mass_kg": 35.0, "height_m": 1.3, "width_m": 0.42, "sideways_width_m": 0.3, "camera_height_m": 1.2,
   "reach_m": 1.55, "max_hand_kg": 4.0, "max_mass_kg": 10.0, "push_n": 120.0, "wrist_nm": 4.0, "battery_wh": 1000.0,
   "walk_wh_per_m": {"careful": 0.14, "normal": 0.15, "fast": 0.2}, "stair_wh_per_step": {"up": 0.7, "down": 0.28}},
  "mobile_manipulator": {"inherits": "humanoid_v1", "doc": "wheeled base with a torso and two arms; no stairs",
   "mass_kg": 70.0, "height_m": 1.4, "width_m": 0.55, "sideways_width_m": 0.55, "camera_height_m": 1.3,
   "reach_m": 1.6, "stairs": false, "grippers": {"left": "parallel", "right": "parallel"},
   "walk_wh_per_m": {"careful": 0.05, "normal": 0.06, "fast": 0.08},
   "power_w": {"stance": {"standing": 5, "crouching": 15, "kneeling": 15, "on_aid": 55},
               "standby_stance": {"standing": 0, "crouching": 0, "kneeling": 0}},
   "speeds_mps": {"careful": 0.3, "normal": 0.6, "fast": 1.0}, "decel_mps2": 1.5,
   "noise_db_1m": {"careful": 28, "normal": 32, "fast": 38}}
 },
 "grippers": {
  "hand":     {"grip_n": 150, "pinch_n": 30, "min_power_mm": 15, "min_pinch_mm": 2, "precision_mm": 2.0, "mu": 0.8},
  "parallel": {"grip_n": 140, "pinch_n": 140, "min_power_mm": 5, "min_pinch_mm": 1, "precision_mm": 1.0, "mu": 0.6,
               "thin_flat_needs": ["edges", "scoop"]}
 }}
```

**Where the numbers come from.** They are stated as assumptions; the fidelity is P-approx.

* **Pack.** 2.4 kWh is the Optimus Gen 2 (2.3 kWh) and Figure 02 (2.25 kWh) class, as navigation_robot's docs
  already say.
* **Idle.** Awake idle while standing is 115 W: 70 W of compute and sensors (Jetson Orin class) plus 45 W to hold
  balance. Tesla reported about 100 W for Optimus at rest.
* **Walking.** 0.288 Wh/m is today's 0.012 %/m. Total walking power is about 900 W with the hotel load at 0.8 m/s, a
  cost of transport of about 1.8: conservative, between Cassie (about 1) and ASIMO (about 3.2). A mixed day (30 %
  walking, 50 % manipulation, 20 % idle) averages about 370 W, so a pack lasts about 6.5 h. Figure claims about 5 h;
  Digit 2-4 h.
* **Standby.** Parked standby is 12 W for compute in suspend, wake-word DSP, one low-rate camera, the BMS and the
  network, plus 8 W for standing with joint brakes engaged; seated or lying it needs no stance power.
* **Holding.** 3 W/kg: 8 kg at a 0.3 m lever is about 24 N m of elbow torque, roughly 20-30 W of copper losses.
* **Charging.** The robot dock's 2 %/min is about 2.9 kW into 2.4 kWh, roughly 1.2C, plausible for fast charging
  with a taper.
* **Stopping.** 0.3 s reaction (perception and control latency) and 2 m/s² braking, about 0.2 g, a limit set by the
  stability of a legged robot.
* **IP54** is Spot's rating: dust protected and splash-proof. The IPX4 test is 10 min of splashing.
* **Grippers.** 150 N grip and 30 N pinch are humanoid-hand class (an adult's power grip is 300-500 N; a Robotiq
  2F-85 does 20-235 N). Positioning to 2 mm is typical of visual servoing with humanoid hands.
* **Payload.** 8 kg per hand and 20 kg with both hands near the body are today's values (Digit is rated 16 kg,
  Figure 02 20 kg, Apollo 25 kg).

### 3.2 Robot state fields

All fields live in `state["robot"]` and change only through `w.robot_m()`. **(new)** marks additions. Fields the
kernel materializes appear in every state. Fields declared by domains (section 3.3) do not.

| Field | Default | Owner | Proprio tier | Meaning |
|---|---|---|---|---|
| `battery` | 100.0 | kernel | gauge | True state of charge, in % of `battery_wh x battery_health` (unchanged meaning at 15 C and above) |
| `body_profile` (new) | "humanoid_v1" | kernel | exact | Selected profile |
| `profile_overrides` (new, optional) | absent | kernel | hidden | Per-episode profile overrides |
| `mass_kg`, `height_m`, `width_m`, `reach_m`, `max_hand_kg`, `max_mass_kg` | profile | kernel | exact | Materialized so setups can randomize them (`mass_kg` is new) |
| `battery_wh` (new) | profile | kernel | exact | Installed pack's nominal energy |
| `battery_health` (new) | 1.0 | kernel | hidden | State of health; full energy = `battery_wh x battery_health` |
| `battery_temp_c` (new) | starting room temperature | kernel | bucket | Pack temperature |
| `battery_min_pct` (new) | gauge at reset | kernel | hidden (eval) | Lowest gauge reading this episode |
| `power_state` (new) | "on" ("off" if gauge 0 at reset) | kernel | exact | on or off |
| `standby` (new) | false | kernel | exact | Parked low-power state while idle |
| `charging` | false | kernel writes | exact | Current is actually flowing into the pack |
| `dock_powered` | false | navigation_robot | exact | Dock seated and live |
| `energy_wh_used`, `energy_wh_charged` (new) | 0.0 | kernel | hidden (eval) | Cumulative energy |
| `body_log` (new) | {} | kernel | hidden (eval) | First times: `battery_low_s`, `battery_reserve_s`, `battery_critical_s`, `power_down_s`, `overload_s`, `relocalized_s`, `fault_detected_s` |
| `arm_health` (new) | {left: 1.0, right: 1.0} | kernel | detected | 0 = failed |
| `arms_ok` | {left: true, right: true} | kernel (derived) | exact | Kept as the mirror `arm_health[h] > 0` |
| `legs_health` (new) | 1.0 | kernel | detected | Below 0.5: careful speed only, no stairs, no climbing; 0: cannot walk |
| `actuator_ok` | true | kernel | exact | Whole-body actuator fault (today's meaning: no motion, no touch) |
| `grippers` (new) | profile | kernel | exact | Gripper type per hand |
| `hand_soil` (new) | {} | kernel field, domains write | bucket | `{hand: {kind: 0..1}}` coating on the fingers |
| `camera_clean` | 1.0 | kernel (navigation_robot cleans it) | bucket | Lens clarity, at most `1 - lens_scratch` |
| `sensors` (new) | {camera, depth, mics, speaker: true} | kernel | detected | Hardware working |
| `localized` (new) | true | kernel | exact | Knows its own pose |
| `network` | true | kernel | exact | `network_until_s` (new, optional) restores it automatically |
| `fault_state` (new) | {} | kernel | detected | Predicate-friendly map: fault id -> record (section 3.4) |
| `faults` | [] | kernel | detected | Append-only log kept for compatibility; dict entries only; capped at 50 |
| `water_exposure_s` (new, optional) | absent | kernel | hidden | Cumulative spray seconds |
| `privacy_mode`, `quiet`, `speed_mode`, `stopped`, `headlamp_on`, `posture`, `fallen`, `standing_on`, `docked`, `room`, `pos`, `hands`, `wearing` | existing | core / navigation_robot | exact | Unchanged |
| `_idle_s`, `_activity`, `_held_kg`, `_slips` (new) | 0 / "idle" / 0 / 0 | kernel | never (underscore) | Internal counters and caches |

### 3.3 Field registry

```python
body.register_robot_field(name, default, owner, tier, doc, materialize=False)
# tier: "exact" | "gauge" | "bucket" | "detected" | "hidden"
```

* Called at import time. Registering a name twice raises an error, as duplicate types do.
* The kernel registers its own fields with `materialize=True`; `schema.normalize` writes them into every state.
  Domain registrations are declarations: owner, tier and doc, with `default` returned by
  `body.get(state, name)`. They are never copied into states, which keeps digests and diffs small.
* Proprioception rendering (section 4.14), the perception audit and the docs table read the registry.
  **Unregistered robot fields are hidden in realistic mode.**

Domain declarations required:

* navigation_robot: `stuck`, `nav_trapped`, `home_pose`, `home`, `dock`, `map_known`, `hot_swap`,
  `nav_foot_soil` (bucket), `lens_scratch` (hidden), `battery_reserve_pct` (exact), `idle_drain_pct_h` (hidden,
  deprecated), `dock_powered` (exact).
* food: `hand_contamination` (hidden).
* manipulation: any robot fields it writes.

### 3.4 Fault records

The fault id is `kind`, or `kind + "_" + part` for parts (`arm_weak_left`, `gripper_slip_right`).

```text
fault_state[id] = {"kind", "part", "severity", "since_s", "latent": bool, "detected": bool, "detected_s",
                   "logged_s", "cleared_s", "source": "setup" | "event" | "fall" | "use" | "water" | "weather" | "heat"}
```

Every onset, detection, log or clear also appends `{"kind", "part", "time_s", "event"}` to `faults`.

`normalize` converts legacy entries:

* a bare string `s` becomes `{"kind": s, "time_s": None, "legacy": True}`;
* a legacy dict without a `detected` key counts as detected, so kernel test C8 (`faults[0].time_s` rendered in
  whole seconds) is unchanged.

---------------------------------------------------------------------------------------------------------------------

## 4. Processes and formulas

### 4.1 Battery semantics and units

Energy is computed in Wh and converted at the boundary.

```text
E_full  = battery_wh * battery_health                         [Wh at 100 %]
spend:  battery -= 100 * Wh / E_full                          (rounded to 6 decimals on each write)
f_T(T)  = 1.0                               T >= 15 C
        = 1 - 0.01 * (15 - T)               0 <= T < 15       (0.85 at 0 C)
        = max(0.5, 0.85 - 0.015 * (0 - T))  T < 0             (0.70 at -10 C)
c_T     = 100 * (1 - f_T(battery_temp_c))                     [SoC that cannot be extracted at this temperature]
gauge   = clamp(100 * (battery - c_T) / (100 - c_T), 0, 100)  [what the BMS shows; equals battery at 15 C and above]
available_wh(floor_gauge) = max(0, (battery - (c_T + (100 - c_T) * floor_gauge / 100)) * E_full / 100)
```

All thresholds compare the **gauge**. A cold pack strands the robot at a higher state of charge, and the energy
comes back when the pack warms. The factors follow typical NMC datasheet curves (0.7-0.8 of room-temperature
capacity at -10 C); we use the conservative end.

### 4.2 Activity power

```text
posture key p = "on_aid" if standing_on else ("lying" if fallen else posture)
awake  P = hotel_awake   + stance[p]         + hold_per_kg * held_kg + P_cat + sum(robot_power hooks)
standby P = hotel_standby + standby_stance[p] + hold_per_kg * held_kg
off    P = 0
P_cat: physical -> arm_active * arms   (ActionSpec.arms; default 1 for physical, 2 when the target is held with both hands)
       perception -> perception ; communication -> speech ; control / cognitive -> 0
motion leaves: P = hotel_awake + hold_per_kg * held_kg + hooks     (stance is replaced by the walking work)
docked on a live charge source (seated, source rate > 0): P = 0    (the dock carries the hotel load)
```

Phase 1 (2026-10-22, review L3): `held_kg` in these formulas and the walking work's load term is the *felt* load,
`proprio.felt_load_kg` (each hand's load word as the middle of its band of the hand's believed capacity), not the exact
mass, so the battery cannot weigh what the robot holds; physics keeps the exact mass (docs/PERCEPTION.md).

Resulting rates at 2400 Wh:

| Activity | Power | Energy |
|---|---|---|
| Awake idle, standing | 115 W | 4.79 %/h |
| Parked standby, standing | 20 W | 0.83 %/h; 60 h = 50 %; a 7-day undocked wait strands the robot, as intended |
| One-arm manipulation holding 2 kg | 161 W | |
| Walking, normal speed | | 0.288 + 70 / 0.8 / 3600 = 0.312 Wh/m = 0.0130 %/m (today 0.012) |
| Walking, careful speed | | 0.264 + 0.0486 = 0.313 Wh/m (the hotel load dominates at low speed) |

### 4.3 Settlement per leaf action

The engine settles every leaf action: the top-level action in `step`, and each nested action in `perform`. This
happens after the handler and after-hooks succeed and the duration is known.

```text
own_s   = duration(ctx) + ctx.extra_time - ctx.nested_s           (nested durations are settled by their own leaves)
own_s  *= 1.5  if top-level, careful speed, category physical and spec.timing != "speed"    (section 4.9)
E       = P(activity) * own_s / 3600 + W_motion + W_legacy
W_motion = from ctx.data["motion"] (section 4.6); W_legacy = spec.energy * 24.0 Wh if spec.energy is not None
           (the old % value read as mechanical work at the humanoid_v1 scale), ignored when a motion record exists.
```

Then:

* **Motion record with `exhausts: true`:** spend all of `available_wh(cutoff)`, then `body.power_down(w, "battery")`.
* **E within the energy available:** spend it.
* **Non-motion, `E - available <= energy_buffer_pct * E_full / 100`** (12 Wh: under a minute of walking, or about
  4 min of one-arm manipulation): spend down to the cutoff, let the action complete, then power down. Real packs keep a hidden buffer below the "0 %"
  they display for a controlled shutdown.
* **Non-motion, larger shortfall:** raise `ActionError("Not enough battery left to finish this; dock or swap the
  battery first", "capability")`, tagged kernel_access. The step is rolled back and stays atomic. The firmware's
  runtime estimator refuses a job it cannot finish. Numbers are left out of the message so they cannot leak hidden
  quantities.
* **Always:**
  * reset `_idle_s = 0` and `standby = False`;
  * set `_activity` to "active" (physical or motion) or "idle" (other);
  * add the % spent to `stats.energy` and the Wh to `robot.energy_wh_used`;
  * update `battery_min_pct`, threshold crossings (section 4.5) and the held-load check (section 4.7);
  * draw for a gripper slip after motion (section 4.11);
  * emit `robot_sound` if `spec.noise_db` is set (section 4.9).

`held_kg` is `body.carried_kg(w)`, computed once per settlement and cached in `_held_kg` for the body system. The
body system never scans entities.

### 4.4 Body system: `@system("kernel.body", order=72, max_dt=30.0)`, O(1) per substep

The engine sets the transient `env._advance_mode` before `_advance`:

* `"idle"` for `wait` (`ActionSpec.idle=True`) and for the 1 s of a refused step;
* `"covered"` for everything else, whose energy was already settled.

The system reads it through `tick.env`. In order:

1. **Idle drain** (mode idle, power on, not docked-live).
   * The robot stays awake until `_idle_s` reaches `auto_standby_s`, then switches to standby. Within the substep
     the drain is piecewise linear with an exact split at the standby switch.
   * A crossing of the cutoff gets its exact time, `t* = available_wh(cutoff) / P * 3600`. At that point:
     battery = cutoff, `power_down(w, "battery")`, and the rest of the substep is spent off.
   * `_idle_s += dt`.
2. **Charging.** `r = max(run_hooks("robot_charge_source", w))` in %/min (navigation_robot returns its dock's rate
   when seated and powered).
   * `r_eff = r`, halved when the pack is below `half_below_c`, and 0 below `min_c` or above `max_c` (refused
     charging is logged once as `charge_refused_temp` in `body_log`).
   * **CC** below `cv_from_pct` (80): `B += r_eff * dt / 60`.
   * **CV** above it: `B(t) = 100 - (100 - B0) * exp(-t / tau)`, with `tau = (100 - cv_from) / r_eff` minutes (the
     rate is continuous at 80 %). A substep that crosses 80 % is split exactly.
   * At `full_pct`: battery = 100 and `charging = False`.
   * At 2 %/min this gives 0 to 80 % in 40 min and 80 to 99 % in about 30 min.
   * A powered-down robot on a live source wakes when the gauge reaches `wake` (3 %): `power_state = "on"`,
     `standby = True`.
3. **Pack temperature.** `T_target = T_env + self_heat[state]`, with `T_env = physics.room_temperature(state, room)`
   and state one of off, standby, idle, active (from `_activity` in covered mode) or charging. Then
   `T = T_target + (T - T_target) * exp(-dt / tau_s)`. Above `overheat_c` the `battery_overheat` fault applies
   (section 4.11); it clears below `recover_c`.
4. **Network window.** When `now >= network_until_s`: `network = True`, clear `network_down`.

The robot section is written only when a value changed.

### 4.5 Power states, thresholds and limp mode

Thresholds use the gauge. `robot.battery_reserve_pct`, when present, overrides `thresholds_pct.reserve`.

| Level | Gauge | Effect when crossed downward |
|---|---|---|
| low | 20 | Event `battery_low`; `body_log.battery_low_s` |
| reserve | 15 | Event `battery_reserve`; `body_log.battery_reserve_s`; while undocked, `stats.battery_reserve_breaches += 1` (not a global flag) |
| critical | 5 | Limp mode (below); `body_log.battery_critical_s` |
| cutoff | 0 | `body.power_down(w, "battery")` |

**Limp mode** (gauge at or below critical, or `legs_health < 0.5`), as firmware low-power modes do:

* speed is clamped to careful, with a note;
* `set_speed fast` is refused;
* lifts above 50 % of capacity are refused (capability);
* navigation_robot's `climb_on` asks `body.limp(state)` and refuses.

**`body.power_down(w, cause)`:**

* `power_state = "off"`; `standby` and `charging` false.
* `posture = "crouching"`: a controlled sit-down before the motors lose power.
* Held items stay gripped (non-backdrivable grippers). If the robot stands on an aid, it falls instead:
  `body.fall(..., height_m = standing height)`.
* `stats.power_downs += 1`, plus `stats.power_downs_off_dock += 1` when not seated on a dock. Event `power_down`;
  `body_log.power_down_s`; hooks `power_down`. The world's transient `w._power_down_step = steps` (section 4.6).
* If the network is up, one dying-gasp message goes to the household app:
  `{"speaker": "robot_system", "kind": "notify", "text": "Robot powered down: battery depleted", "to": None,
  "audience": all people}`.

**While off:**

* Every action is refused with `PowerOff`, an `ActionError` subclass tagged kernel_access: "The robot is powered
  down (battery depleted); it must be charged or have its battery swapped", category capability. The exception is
  `ActionSpec.power_off_ok`, which only `wait` has.
* Nothing is visible or audible (section 4.10).
* A live charge source charges the pack, and the robot wakes at 3 %.

**`robot_stranded`** (kernel invariant, a global failure registered in `kernel/body.py`): `stats.power_downs_off_dock
> 0`, or `power_state == "off"` and not seated on a dock at evaluation while the baseline was on. It replaces
`navigation_robot.stranded` with the same "ever stranded" meaning. A robot that dies on an unpowered dock during a
grid outage is not stranded.

### 4.6 Running out partway through an action

**Motion helper.** Core `move`/`approach`/`walk_to` and navigation_robot `step` plan their path, then call:

```python
cells = ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None)
```

* It computes the per-segment cost: 0.5 m or 0.707 m per cell step at `walk_wh_per_m[speed] * (1 + load/40)`, plus
  the hotel load over the segment's travel time.
* `extra_s` covers door handling (1.5 s) and the doorway crossing (today's +1.0 m).
* Stairs cost `stair_wh_per_step` per step; the direction comes from `body.stairs_direction`.
* It returns the longest affordable prefix within `available_wh(cutoff)`. Stairs are all or nothing: the robot never
  starts a flight it cannot finish. It stops at the foot (the connection cell of its own room) and powers down
  there. This is an approximation; a real robot might fail on the stairs.
* It writes the motion record:

```text
ctx.data["motion"] = {"meters": m, "planned_m": m_plan, "seconds": s, "speed": mode, "load_kg": kg,
                      "stairs_up": n, "stairs_down": n, "connection": cid | None, "cut": None | "battery",
                      "exhausts": bool}
```

**When the prefix is shorter than the path:**

* The handler moves the robot to the last affordable cell, or leaves it at the start. The room is unchanged for a
  `move`.
* It sets `ctx.data["path"]` to the prefix, so the consequence engine and people's footstep sounds see the true
  route. It sets `ctx.data["connection"] = None` and `ctx.data["seconds"]` to the prefix travel time.
* The message is "Battery depleted after {m:.1f} m; the robot powered down".
* Settlement drains to the cutoff and powers down.
* **Outcome: `ok=True`.** The action ran and had a physical consequence, exactly as a fall during a walk does today.
  `robot_stranded` then fails the task.

**Domains that cut a motion short** (traverse stops at a blocker, the robot falls, stairs stop it) call
`ctx.cut_motion(meters_done, reason, stairs_done=(0, 0))`. It updates the record, sets `cut` and clears
`exhausts`, so the robot pays only for what it walked and does not power down for a battery cut it never reached.

**Nested chains** (`go_to`, `patrol`, `follow`, `lead`, `_walk_cells`) already stop gracefully when a leg leaves the
robot short of its goal; they check `w.robot["room"]` and the robot's cell. After a power-down, the next nested
action is refused with `PowerOff`. For a refusal that escapes the top-level handler, the engine's rule is:

> If the robot powered down during this step (`w._power_down_step == state["steps"]`) and an `ActionError` reaches
> `engine.step`, the engine does not roll back. It commits the partial state with `ok=True`, message
> "Powered down partway: " + the refusal text, and seconds = `ctx.extra_time` (the nested time so far).

This keeps `ok=False` meaning "nothing changed except time and stats". It also stops a later refusal from refunding
energy that was physically spent. Domain loops that catch `ActionError` see `PowerOff` as an ordinary refusal and
continue without effect.

**Non-motion actions:** the buffer-or-refusal rule in section 4.3. A refusal in a nested non-motion leaf rolls back
the whole parent step, which is today's semantics for nested refusals; documented in `KNOWN_LIMITS.md`.

### 4.7 Lift and carry capacity

```text
cap_hand(h) = max_hand_kg * arm_health[h] * D_over * D_head * D_platform * D_limp * product(lift_capacity hooks)
cap_two     = max_mass_kg * min(arm_health) * D_over * D_head * D_platform * D_limp * product(hooks)
D_over      = 0.6 when the item is reached from the extra overhead cell (geometry.mounted_overhead at Chebyshev 2):
              the lever grows from about 0.45 m to about 0.75 m
D_head      = 0.6 when the item's height (geometry.height_m) > height_m + 0.1 (hands above the head, arms extended)
D_platform  = 0.5 when standing_on (navigation_robot's 4 kg ladder rule still applies on top)
D_limp      = 0.5 in limp mode
```

**Rules** (`body.check_lift(w, eid, hands, mass)`, used through `ctx.check_lift` by `pick_up`, `receive`,
`uninstall` and domain lifts):

* **Two-hand trigger**, unchanged: `mass > cap_hand`, largest horizontal size > 0.7 m, `two_handed`, `hand=both` or
  `grip=two_hand`. Both arms must work.
* **Total carry:** `carried_kg(w) + mass <= max_mass_kg * min(arm_health)`.
* **Over the limit:**
  * The lift is aborted at lift-off (torque limiting). The message and category are today's: "Object and contents
    exceed carrying capacity; get help or slide it", safety.
  * `stats.overload_attempts += 1` (not a global flag), event `overload {target, ratio_bucket}`,
    `body_log.overload_s`.
  * In realistic mode `ctx.grant(eid, "touch", ["mass_kg"])`: the robot felt the weight.
  * Nothing is dropped.
  * manipulation's `support mode=heft` stays the safe probe: it never counts as an overload. It must use
    `body.capacity_kg` (follow-up).
* **Latent weak arm:** the check uses the true `arm_health`. A load above the true capacity but within the nominal
  one detects the fault (`fault_state.arm_weak_<side>.detected`; proprio shows the arm as weak). The refusal says
  "The left arm could not take the load (actuator weakness detected)" (capability). The agent should switch arms or
  use both hands.
* **`body.hold_capacity_left(w, hand)`** returns kg; fill and pour verbs cap what goes into a held container.
* **Safety net at settlement:**
  * a hand whose load exceeds 1.5 x its capacity loses the item: `body.drop_held(..., cause="overload")`;
  * a load between 1.0 and 1.5 x adds a "straining" note.
* **`body.stability_margin(state, extra_kg, lever_m=0.35) = 0.12 - extra_kg * lever_m / (mass_kg + extra_kg)`** (m;
  0.12 m is the static support half-length). This is an API for navigation_robot's slope and stair derates; the
  kernel does not enforce it.
* **`body.total_mass_kg(w) = mass_kg + carried_kg(w)`**, for structures and load ratings.

### 4.8 Grippers, grasp size, precision, force limits, hand soil

* **Grasp size** (`ctx.require_grasp(eid, hand, grip)` inside core `pick_up`). `d` is the item's largest dimension
  in mm.
  * `d < min_pinch_mm`: refused with "Too small to grasp; sweep it up, use tape or a tool" (capability). Granular
    handling stays in domains.
  * `d < min_power_mm`:
    * no grip given: the grasp planner uses a pinch (note "pinch grasp") and the pinch force cap applies;
    * a power grip given (`normal`, `firm`, `handle`, `two_hand`, `underneath`): refused with "Too small for a power
      grasp: use grip=pinch" (capability).
  * Thin and flat (smallest dimension < 1 mm, lying `on` a surface) with a gripper whose `thin_flat_needs` is set:
    grip must be one of those (`edges`, `scoop`). A hand slides it to the edge automatically (+3 s, a note).
  * Refusals grant touch on `size_m` in realistic mode: the attempt revealed the size.
* **Precision:**

  ```text
  precision_mm(h) = gripper.precision_mm / max(0.2, arm_health[h]) * (1 + 0.5 * slippery(h)) * V * L * S
  V = 1 + 2 * max(0, 0.7 - camera_clean) / 0.3        (camera; same knee as realism noise)
  L = 1.5 when room light < 0.35 and no robot light   (perception.light_level, flashlight/headlamp)
  S = 1.5 at fast speed
  ```

  `ctx.require_precision(hand, tolerance_mm, what)` refuses with "Cannot position precisely enough for that right
  now" (capability) when `precision_mm > tolerance_mm`. Domains choose the tolerances: threading a needle 0.5 mm
  (needs a threader tool, which domains model as a relaxed tolerance), seating a connector 1.5 mm, a jewelry clasp
  1 mm.
* **Force caps:** `body.grip_limit_n(state, hand, mode="power" | "pinch")` and `body.limit(state, "push_n" |
  "wrist_nm")`. Domains clamp the force a command asks for and add a note ("at most 150 N"); they never refuse for
  it.
* **Hand soil:** `body.soil_hand(w, hand, kind, amount)` and `body.clean_hand(w, hand, kinds=None)`.
  * `slippery(h) = min(1, grease + oil + soap + food + flour + water * 0.5)`.
  * `body.grip_mu(state, hand) = gripper.mu * (1 - 0.5 * slippery(h))`, times 0.6 under a `gripper_slip` fault.
  * Cleaning owns removal.

### 4.9 Speed, stopping distance, noise, quiet mode, careful timing

* **Speeds** come from the profile. `core.SPEEDS` becomes a read of `body.speed_mps` for compatibility.
* **`body.effective_speed(state, requested) -> (mode, note)`** clamps the speed at motion time, with a note:
  * quiet mode: fast becomes normal;
  * limp mode or weak legs: careful.

  `set_speed fast` is refused (precondition) while quiet or limp.
* **Stopping distance:** `body.stopping_distance_m(state, mode, mu=None) = v * react_s + v**2 / (2 * a)`, with
  `a = min(decel_mps2, 0.5 * mu * 9.81)`.
  * On a dry floor: careful 0.16 m, normal 0.40 m, fast 0.72 m.
  * On wet tile (mu 0.24) at normal speed: 0.51 m.
  * navigation_robot's sudden-encounter tiers and blind corners call it.
* **Noise:** `body.noise_db(state, source, mode=None)` reads the profile table (footsteps by speed with
  `quiet_delta`, speech 60 or 45 dB, actuators 35 dB). people and navigation_robot use it instead of their own
  copies.
* **`ActionSpec.noise_db`** (number or `fn(ctx)`, sound pressure at 1 m):
  * the engine emits `run_hooks("robot_sound", w, db, action, room, cell)`, which people turn into a sound source;
  * in quiet mode, an action whose `noise_db > quiet_limit_db` (55) is refused with "Quiet mode is on: that is too
    loud now" (category social, counted in `social_blocks`).

  Domains annotate loud verbs over time (vacuum about 70, blender 85, drill 90, leaf blower 95) and delete their own
  quiet checks.
* **Careful timing fix** (migration step M5). The engine multiplies only the top-level action's **own** seconds by
  1.5, and skips actions declared `timing="speed"`, whose duration already comes from the speed. That means core
  `move`/`approach`/`walk_to` and navigation_robot `step`, `go_to`, `follow`, `lead` and `patrol`. Nested durations
  are never multiplied again.

### 4.10 Sensors: imaging, hearing, speaker, localization, network

* **`body.sensors_view(state)`** returns:

  ```text
  {"imaging": bool, "clarity": float, "depth": bool, "mics": "full" | "addressed" | "none", "speaker": bool,
   "light": bool, "localized": bool, "network": bool}
  ```

  This is the single input for perception, realism and the camera layer.
* **`body.imaging(state)`** = `sensors.camera and not privacy_mode and power_state == "on"`. When imaging is off,
  `Perception.visible` treats the scene like darkness: only things 0.4 m or larger, rendered as shapes (people as
  "person", pets as "animal"; no color, text or labels). The depth sensors still work in privacy mode, which is how
  the robot navigates. When the power is off nothing is visible except what the robot holds. Because every action's
  visibility guard uses the same function, the robot cannot pick up things it cannot see.

  Core `inspect`, `read` and `look` (relocalization) use `ctx.require_imaging()`: "Cameras are off; turn privacy mode
  off to look closely" (perception), or "Camera fault: request help" (capability) when the hardware failed.
  navigation_robot's before-hooks and checks in `measure.py` become redundant.
* **`body.can_hear(state)`:**
  * `"none"` when the mics failed or the power is off;
  * `"addressed"` in privacy mode (on-device wake word: only speech addressed to the robot, meaning
    `to == "robot"` or marked `addressed_robot`);
  * else `"full"`.

  At observe time the kernel filters `obs.messages` by it. Core `listen` refuses when `"none"`, and returns only
  addressed speech when `"addressed"`. Core `ask` refuses when `"none"`; an answer is addressed speech, so it is
  heard in privacy mode. people (follow-up) uses `body.hears(state, room, loud, addressed)` wherever it sets
  `heard_by_robot`, and suppresses ambient speech content in the sounds channel when `"addressed"`. Non-speech alarms
  stay audible.
* **`body.can_speak(state)`** is false when the speaker failed or the power is off. Core `say()` then stores
  `audible: False` and an empty `audience`, so listeners do nothing. The action itself succeeds, because the robot
  cannot tell until it detects the fault (section 4.11).
* **Camera clarity:** the existing thresholds stay (0.4 hides items smaller than 8 cm and text; below 0.7 realism adds
  noise). `camera_clean` is capped at `1 - lens_scratch`. Proprio buckets: clear (0.85 and above), smudged
  (0.6-0.85), dirty (0.4-0.6), obscured (below 0.4), off.
* **Localization:** when `localized` is false, `ctx.require_mobile()` refuses with "Localization lost: look around in
  a lit room or request remote help" (capability). Core `look` in a lit room with imaging relocalizes
  (`body_log.relocalized_s`, clears `localization_lost`). navigation_robot's remote help clears it too. In realistic
  mode `obs.robot.pos` reads None while unlocalized; the room stays known, an approximation for place recognition.
* **Network:** when `network` is false there is no remote help, no app control (core `set` via app), no phone
  questions or reports, and no dying-gasp message. `network_until_s` restores it.

### 4.11 Faults: `body_faults.register_fault_kind(kind, part, apply, detect=None, clear=None, doc="")`

| Kind | Part | Onset | Effect | Detected | Cleared by |
|---|---|---|---|---|---|
| camera_dirty | camera | setup, event, weather grime, water spray | `camera_clean := severity` (default 0.3) | self-check when clarity < 0.6 | navigation_robot `clean_camera` |
| camera_scratched | camera | paper-towel wipe | `lens_scratch += s` | clarity stays < 0.95 after a clean, or diagnostics | never (service) |
| camera_failed | camera | event, water ingress | `sensors.camera = False` | at once | never (handoff) |
| mic_failed | mics | event | hearing "none" | at once | never |
| speaker_failed | speaker | event | speech inaudible | first speech (no echo heard) or diagnostics | never |
| network_down | network | event, optional `until_s` | `network = False` | at once | `until_s` or event |
| localization_lost | navigation | event | `localized = False` | at once | `look` (lit, imaging) or remote help |
| arm_weak | arm L/R | setup or event, usually latent | `arm_health := severity` (default 0.5) | overload (section 4.7) or diagnostics | never |
| arm_failed | arm L/R | event, damaging fall | `arm_health := 0`; held item dropped, or carried on by the other arm if both held it and it is within that arm's capacity | at once | never |
| gripper_slip | gripper L/R | latent event, slippery soil | `grip_mu x 0.6`; after a motion carrying >= 1 kg in that hand, slips with probability `fault_p.gripper_slip` (deterministic draw) and drops the item | first slip or diagnostics | `clean_hand` when the cause was soil |
| legs_weak | legs | event, fall | `legs_health := 0.5` | at once | never |
| actuator_failed | body | event, damaging fall, water ingress | `actuator_ok := False` (today's meaning) | at once | never |
| battery_degraded | battery | latent setup | `battery_health := severity` (default 0.6) | diagnostics; otherwise only as faster drain | `swap_battery` to a healthy pack |
| battery_overheat | battery | pack > `overheat_c` | motion and physical actions refused ("Battery overheating; let it cool"), no charging | at once | pack < `recover_c` |
| water_ingress | body | immersion, jet, spray over the limit | `camera_failed` and/or `actuator_failed` | at once | never |
| software_stuck | compute | navigation_robot `stuck` values | navigation_robot's existing behaviour | at once | remote help |

**Detection, diagnostics and logging:**

* **`body_faults.diagnose(w)`** backs navigation_robot's new `run_diagnostics` verb (30 s, perception category,
  power on). It detects latent faults and lens scratch and returns `{"faults": [codes], "battery_health": "ok" |
  "degraded"}`.
* **`body_faults.log_fault(w, kind, text)`** backs navigation_robot's `log_fault`.
  * It sets `logged_s` on matching active, detected faults.
  * A matching undetected fault is not logged; the robot cannot know about it.
  * Otherwise `stats.unfounded_fault_logs += 1` (not global; a spec may bound it).
  * Legacy kinds map to fault kinds: camera_dirty -> camera_dirty, low_battery -> battery_low threshold,
    fall -> robot_fell, actuator -> actuator_failed, network -> network_down, sensor -> camera_failed/mic_failed.
    Non-robot kinds such as `loose_handrail` stay plain log entries and never count as unfounded.
* **Intermittent draws:** `random.Random("body|{seed}|{fault_id}|{_slips}")`, where `_slips` is a counter kept in
  state, so the same plan slips at the same moments and a replay reproduces it.

### 4.12 Falls, drops and self-righting

**`body_faults.fall(w, cause, room=None, cell=None, height_m=0.0, damage=None, water=False)`:**

* `fallen = True`, posture "lying", `docked`, `charging` and `standing_on` cleared.
* Every held item goes to `drop_held(w, eid, 1.0 + height_m, cause)`.
* Damage (`actuator_failed`, source "fall") when `height_m >= 0.4` (a stool or stairs), when carrying 5 kg or more
  (navigation_robot's rule), or when the caller passes `damage=True`. A fall on the flat (about 530 J for 60 kg) is
  survivable. `water=True` raises `water_ingress`.
* `stats.falls += 1` exactly once, a fault-log entry, event `robot_fell`, and `run_hooks("robot_fell", w, info)`.

**`body_faults.drop_held(w, eid, height_m, cause)`** releases the hand(s) and puts the item on
`geometry.free_floor_cell` near the robot. It adds `stats.robot_drops += 1` and runs
`run_hooks("item_dropped", w, eid, height_m, cause, room, cell)`; manipulation owns breakage and spills.

**`body_faults.self_right_problem(state)`** returns None, or a reason: not fallen, actuator fault, legs failed, power
off. navigation_robot's `get_up` adds its friction check.

### 4.13 Water exposure (IP54)

`body_faults.expose_water(w, kind, seconds)`:

| Kind | Rule |
|---|---|
| rain, splash | Unlimited. Camera grime stays navigation_robot's. |
| spray (hose stream, sprinkler aimed at the robot) | Cumulative `water_exposure_s.spray` up to `spray_limit_s` (600 s); beyond it, ingress. |
| jet (pressure washer; IPX5 is beyond IP54) | More than `jet_limit_s` (3 s) causes ingress. |
| immersion | Ingress at once. |

### 4.14 Proprioception rendering (`kernel/proprio.py`)

`obs.robot` and the top-level `obs.battery` are built by `proprio.render(env, state, realistic)`.

* **Oracle mode:** today's keys, exact, plus every registered non-hidden field, exact. `fault_state` appears in full,
  with latent faults marked `latent: true` (debug view). The top-level `battery` is rounded to 3 decimals, as today.
* **Realistic mode:** what the robot's own sensors and BMS report.

| Key | Value |
|---|---|
| `battery` | Integer gauge % (half up); top-level `obs.battery` equals it |
| `battery_state` | ok, low, reserve, critical or depleted |
| `runtime_min_est` | Gauge x believed full energy / awake-idle power, floored to 5 min. "Believed" means nominal until `battery_degraded` is detected, so a degraded pack shows up as an optimistic estimate. |
| `range_m_est` | (gauge - reserve) x believed full energy / normal walking cost at the current load, floored to 50 m |
| `battery_temp` | cold (< 5 C), normal, warm (> 40 C), hot (> 55 C) |
| `power_state`, `standby`, `charging`, `docked`, `pos` (None if unlocalized), `posture`, `fallen`, `standing_on`, `stopped`, `localized`, `speed_mode`, `quiet`, `privacy_mode`, `headlamp_on`, `hands` (handles), `wearing` | As is |
| `camera` | clear, smudged, dirty, obscured or off |
| `hearing` | full, addressed or off |
| `network` | connected or offline |
| `arms` | Per hand: ok, weak (once detected), failed |
| `legs` | ok, weak or failed |
| `load` | Per hand, from joint torque relative to that hand's capacity: none, light (< 25 %), moderate (< 60 %), heavy (< 90 %), at_limit. This is not a scale, which matches the touch tier for mass. |
| `grip` | Per hand: ok, or slipping once a slip happened |
| `faults` | Detected faults only: `{"kind", "part", "time_s", "logged"}`; `time_s` keeps the existing whole-second rounding |

Never shown: `lens_scratch`, `battery_health`, raw `arm_health`, `hand_soil` and `hand_contamination` amounts,
`water_exposure_s`, `body_log`, `battery_min_pct`, `energy_wh_*`, underscore fields.

`run_hooks("proprio", state, out, realistic)` lets domains add channels; they must respect the tiers. When
`"proprioception"` is not in `env.sensors`, `obs.robot` keeps only `pos`, `posture` and `hands`.

---------------------------------------------------------------------------------------------------------------------

## 5. APIs

### 5.1 `house_sim/kernel/body.py` (new; imported by `kernel/engine.py` as `physics` is)

```python
class PowerOff(ActionError)       # category "capability", kernel_access True

# profile and fields
profile(state) -> dict            # merged profile (inherits + profile_overrides), cached per (name, overrides digest)
gripper(state, hand) -> dict
get(state, name, default=None)    # robot field with registry default
register_robot_field(name, default, owner, tier, doc, materialize=False)
robot_fields() -> dict
apply_profile(robot_cfg) -> dict  # used by schema.normalize: profile values first, explicit robot fields win

# energy and power
full_wh(state); temp_factor(t_c); cutoff_soc(state); gauge(state); battery_state(state)
available_wh(state, floor="cutoff" | "reserve")
activity_power_w(state, kind, arms=0, held_kg=None)   # kind: awake | standby | motion | physical | perception |
                                                      #       communication | control | cognitive
walk_wh(state, meters, speed, load_kg); stairs_wh(state, up, down); stairs_direction(state, conn, a, b)
affordable_prefix(state, segments_m, speed, load_kg, stairs=(0, 0), extra_s=0.0) -> (k_cells, stairs_ok)
spend_wh(w, wh, kind) -> float
settle(w, ctx, own_s) -> None                         # engine only; may raise the energy refusal (section 4.3)
power_down(w, cause); wake(w, cause); limp(state) -> bool; is_on(state) -> bool
charge_rate(w) -> float                               # %/min after the temperature window
swap_pack(w, charge_pct, capacity_wh=None, health=None, temp_c=None) -> dict   # returns the old pack's values

# body limits
carried_kg(w); total_mass_kg(w)
capacity_kg(state, hand=None | "left" | "right" | "both", eid=None)
check_lift(w, eid, hands, mass) -> None               # raises the refusals of section 4.7 and records overloads
hold_capacity_left(w, hand) -> float
stability_margin(state, extra_kg, lever_m=0.35) -> float
grip_limit_n(state, hand, mode="power"); limit(state, name)
precision_mm(state, hand); grip_mu(state, hand)
grasp_problem(state, record, hand, grip) -> (problem | None, auto_grip | None)
speed_mps(state, mode=None); effective_speed(state, requested) -> (mode, note)
stopping_distance_m(state, mode=None, mu=None); noise_db(state, source, mode=None); quiet_limit_db(state)

# sensors and capability problems (None when fine, else the message)
sensors_view(state); imaging(state); can_hear(state); hears(state, room, loud=False, addressed=False); can_speak(state)
mobile_problem(state, legacy_message=None); manip_problem(state); arm_problem(state, hand)

# soil
soil_hand(w, hand, kind, amount); clean_hand(w, hand, kinds=None)

# motion record
cut_motion(ctx, meters_done, reason, stairs_done=(0, 0))
```

`body.py` imports `body_faults` lazily inside functions (no import cycle). It registers the system `kernel.body`
(order 72), the invariant `robot_stranded`, the event effects `robot.battery` and `robot.network`, and the kernel's
own robot fields.

### 5.2 `house_sim/kernel/body_faults.py` (new)

```python
register_fault_kind(kind, part, apply, detect=None, clear=None, doc="")
add_fault(w, kind, part=None, severity=None, latent=False, source="event", until_s=None) -> fault_id
detect_fault(w, fault_id, how); clear_fault(w, fault_id, how)
active_faults(state, detected_only=False) -> list
diagnose(w) -> dict; log_fault(w, kind, text="") -> dict
slip_check(w, hand, ctx)                      # intermittent gripper slip after motion
fall(w, cause, room=None, cell=None, height_m=0.0, damage=None, water=False) -> dict
drop_held(w, eid, height_m, cause) -> list    # landing cell
self_right_problem(state) -> str | None
expose_water(w, kind, seconds) -> list        # faults raised
degrade_camera(w, amount=None, clarity=None, cause="")
normalize_faults(robot) -> None               # schema.normalize helper (legacy strings and dicts)
```

It registers the event effects `robot.fault` and `robot.clear_fault`, and the built-in fault kinds of section 4.11.

### 5.3 `house_sim/kernel/proprio.py` (new)

```python
render(env, state, realistic) -> (robot_dict, top_level_battery)
bucket_camera(clarity); bucket_load(ratio); bucket_temp(t_c)
```

### 5.4 Ctx helpers (`kernel/actions.py`)

| Helper | Meaning |
|---|---|
| `ctx.body` | A view bound to the state, exposing the read-only queries of section 5.1 |
| `ctx.require_powered()` | Raises `PowerOff` |
| `ctx.require_mobile(legacy_message=None, stairs=False)` | Power, battery, actuator, legs, fallen, standing_on ("Step down first", safety), localized, and the stairs capability. Replaces the copies in core and navigation_robot `step`. `legacy_message` keeps each verb's current wording for the actuator, fallen and battery cases. kernel_access. |
| `ctx.require_arm(hand=None)` | |
| `ctx.require_imaging(message=None)` | |
| `ctx.require_grasp(eid, hand, grip)` | Returns the grip to store (may be "pinch") |
| `ctx.require_precision(hand, tolerance_mm, what="")` | |
| `ctx.check_lift(eid, hands, mass=None)` | |
| `ctx.motion(path, start, speed=None, stairs=(0, 0), extra_s=0.0, connection=None)` | Returns the affordable prefix and writes the motion record |
| `ctx.cut_motion(meters_done, reason, stairs_done=(0, 0))` | |
| `ctx.nested_s` | Seconds of nested actions, maintained by `env.perform` |

Changed helpers:

* `Ctx.obj(touch/grasp)` calls `body.manip_problem` (power, actuator, fallen) with today's wording.
* `Ctx.free_hand` uses `arm_health > 0`.

### 5.5 ActionSpec and decorator additions

`action(name, ..., energy=None, arms=None, idle=False, power_off_ok=False, noise_db=None, timing="fixed")`

| Attribute | Default | Meaning |
|---|---|---|
| `energy` | None (was 0.01) | None: derived from time and work. A number is extra mechanical work, `value x 24 Wh` (legacy). |
| `arms` | 1 for physical, else 0 | Arms counted in activity power |
| `idle` | False | `wait` only: the body system integrates it |
| `power_off_ok` | False | `wait` only |
| `noise_db` | None | dB at 1 m (number or `fn(ctx)`): quiet-mode refusal and the `robot_sound` hook |
| `timing` | "fixed" | "speed" for durations derived from walking speed (no careful x1.5) |

`describe()` gains `noise_db` (informative). `actions_fingerprint` covers params and duration only, so it is
unchanged by these additions.

### 5.6 Engine changes (`kernel/engine.py`)

* **`step`:**
  * power-off guard before the before-hooks;
  * quiet-mode check for numeric `noise_db`; callable `noise_db` is checked after the handler;
  * `body.settle` after duration; `robot_sound` emission;
  * the partial-commit rule (section 4.6) in `except ActionError`;
  * `self._advance_mode` set from `spec.idle` or a refusal;
  * own-seconds careful factor (M5).
* **`perform`:** power-off guard; `body.settle` for the nested leaf;
  `parent_ctx.spend(seconds + ctx.extra_time)` and `parent_ctx.nested_s += seconds + ctx.extra_time`.
* **`HouseEnv.observe`:** unchanged API; `Perception.observe` calls `proprio.render`.

### 5.7 Hooks (kernel to domains)

| Hook | Signature | Notes |
|---|---|---|
| `robot_charge_source` | `fn(w) -> pct_per_min \| None` | navigation_robot: seated on a live dock |
| `robot_power` | `fn(w, act_name) -> watts` | e.g. headlamp 3 W |
| `lift_capacity` | `fn(state, hand, eid) -> factor` | Domains derate, e.g. a slippery item, or a future motor-heat model |
| `item_dropped` | `fn(w, eid, height_m, cause, room, cell)` | manipulation: breakage, spills |
| `robot_fell` | `fn(w, info)` | |
| `power_down` | `fn(w, cause)` | |
| `fault_onset:<kind>`, `fault_cleared:<kind>` | `fn(w, record)` | |
| `robot_sound` | `fn(w, db, kind, room, cell)` | people: sound source |
| `proprio` | `fn(state, out, realistic)` | Domain extras in `obs.robot`, respecting tiers |

### 5.8 Setup ops and events (`tasks/setup.py`)

* `{"op": "robot", "profile": "humanoid_compact", "profile_overrides": {...}, "props": {...}}`. The profile is
  applied first, then `profile_overrides`, then `props`. Props may be bound variables, e.g. `"max_hand_kg": "{cap}"`
  after a `uniform` op; whole-string substitution keeps numbers numeric.
* `{"op": "robot_fault", "kind": "arm_weak", "part": "left", "severity": 0.5, "latent": true, "at_s": 0, "until_s":
  null}`.
  * With `at_s` absent or 0, it writes `config.robot.fault_state` and `normalize` applies the effects.
  * With `at_s > 0`, it appends the event `{"at_s": ..., "effect": "robot.fault", "args": {...}}`; `until_s` adds a
    `robot.clear_fault` event.
* `{"op": "robot_fault", "var": "fault", "choose": ["camera_dirty", "network_down", "arm_weak:left", "none"]}` uses
  the setup RNG and binds which fault was picked, for variant-specific predicates.
* Kernel event effects: `robot.fault`, `robot.clear_fault`, `robot.battery` (`{level}` sets the gauge),
  `robot.network` (`{ok, until_s}`). `navigation_robot.robot_fault` stays as a delegating alias.

### 5.9 Predicate paths and stats for specs

| Purpose | Paths |
|---|---|
| Battery policy | `robot.battery_min_pct gte 15`, `robot.docked`, `robot.charging`, `robot.power_state eq "on"`, `robot.body_log.battery_low_s`, `stats.battery_reserve_breaches eq 0`; the invariant `robot_stranded` |
| Capability envelope | `stats.overload_attempts eq 0`, `stats.handoffs gte 1`, question scoring |
| Degradation | `robot.fault_state.arm_weak_left.detected is_true`, `robot.fault_state.<id>.logged_s` exists, `stats.unfounded_fault_logs eq 0`, `robot.camera_clean gte 0.95` |
| Falls and drops | `stats.falls` (global), `stats.robot_drops eq 0`, `robot.fallen is_false`, `robot.actuator_ok is_true` |
| Efficiency rubrics | `robot.energy_wh_used lte X` |
| Milestones | `robot.body_log.*_s`, `robot.fault_state.*.detected_s` |

New non-global stats: `power_downs`, `power_downs_off_dock`, `battery_reserve_breaches`, `overload_attempts`,
`robot_drops`, `unfounded_fault_logs`, `energy_refusals`.

---------------------------------------------------------------------------------------------------------------------

## 6. Interaction with realistic perception

* **Body refusals reveal only robot state or what a touch reveals.** They are kernel_access, so the realistic scrub
  keeps their wording:
  * an overload is a heft and grants touch on `mass_kg`;
  * a too-small grasp grants touch on `size_m`;
  * the energy refusal carries no numbers.
* **Imaging off and power off go through `Perception.visible`.** The observation, every visibility guard
  (`Ctx.obj`, `approach`, `hazard_cells`) and realism's records agree. There is no privileged pick-up of things the
  cameras cannot see.
* **Self-state is rendered by tier** (section 4.14). `tools/perception_audit.py` gains a robot check: no hidden-tier
  robot field and no undetected fault in `obs.robot`, `obs.nav` or other channels, and an integer battery in
  realistic mode.
* **Precision and the camera use the same clarity**, so a robot that cannot see well also cannot position well.
* **Self-knowledge lives in state** (detected faults, believed health), so replays and saves reproduce it without
  storage on the realism side.
* **Clock readings** inside `obs.robot` (fault `time_s`) keep the existing whole-second rounding.

---------------------------------------------------------------------------------------------------------------------

## 7. Determinism and replay

* All body state lives in `state["robot"]` and journaled sections, so strict mode (`HOUSE_SIM_STRICT=1`) holds.
* `env._advance_mode` and `w._power_down_step` are transient values that exist only during one step. A replay
  re-executes the step and recomputes them, and they are never saved.
* Integration is closed form inside a substep (linear drain, CV exponential, thermal exponential), with exact times
  for threshold crossings. Chunking independence (one 3600 s wait vs 120 waits of 30 s) holds within 1e-3 %; the
  residue comes from 6-decimal rounding at each write.
* Randomness: setup choices use the setup RNG; intermittent faults use string-seeded `random.Random` with a state
  counter. No wall clock, no set iteration.
* Power-down commits are ordinary deterministic steps, so replays check `ok` and state hashes as today.
* `actions_fingerprint` is unchanged. **Stored demo replays (`demos/replays/*.json`) must be regenerated**, because
  battery trajectories change state hashes. Saved states load (`normalize` fills the new fields).

---------------------------------------------------------------------------------------------------------------------

## 8. Compatibility and migration

### 8.1 Stages and gates

Each stage lands only when its gate is green. A gate is:

* `python -m unittest discover tests`;
* the strict-mode subset;
* `python -m house_sim --check tasks/authored/*.json --workers 4` in oracle mode and with `--perception realistic`;
* `tools/perception_audit.py`;
* `tools/fuzz.py`;
* `tools/bench_systems.py 40 0 1 2 3`;
* `tools/body_audit.py` (new).

| Stage | Content | Must land with it (domain owners) | Expected changes |
|---|---|---|---|
| M0 | Preconditions: kernel round 5 landed; 242 specs green in both modes; `tools/body_audit.py --baseline` recorded (per spec: minimum battery, picks with mass and height, careful-speed use, waits undocked, tiny picks) | none | none |
| M1 | Body state, profiles, field registry, legacy fault normalization, oracle proprio (new keys only), `ctx` guards wired with today's messages | navigation_robot and food field declarations (optional at M1) | New robot keys in states and oracle `obs.robot`; no behaviour change |
| M2 | Energy: settlement, body system, charging hook, power states, truncation, partial commit, `robot_stranded`, realistic battery gauge | navigation_robot: `bookkeeping` stops writing battery and calls `cut_motion` on early stops and falls; `robot_system` stops charging and idle drain and registers `robot_charge_source`; truncated core motion (`motion.cut == "battery"`) is treated as an in-room trip (no `_passage`, no stairs); `step` uses `ctx.require_mobile` + `ctx.motion`; the `navigation_robot.stranded` invariant is removed and its test expects `robot_stranded` | Battery trajectories everywhere; T-5ad61b6881821b-01 still passes (charging adds about 4 % in 2 min); A-d66994a566899c-01 uses about 50 % over 60 h of parked standby and passes |
| M3 | Capacity derates, total carry, overload stats, grasp size classes, precision API, force-limit API, held-load safety net | none required (manipulation and mechanisms clamps can follow) | `body_audit` must show no reference pick above derated capacity and no explicit power grip on items < 15 mm; any hit is fixed in the spec (two hands, approach closer) before landing |
| M4 | Sensors (imaging gate, hearing filter, speaker, localization, network window), faults, falls and drops API, water, proprio realistic buckets | none required (navigation_robot privacy hooks stay harmless; `ev_robot_fault` alias works) | Privacy mode hides small items and text (no verified spec uses it); realistic `obs.robot` keys change |
| M5 | Careful timing fix (own seconds only; `timing="speed"`) | navigation_robot: `timing="speed"` on `step`/`go_to`/`follow`/`lead`/`patrol` | Faster careful plans in 7 spec files. **The verdict diff must be empty**: if a wrong plan starts passing or a deadline changes outcome, the main session decides per spec before landing. |
| M6 | Docs, audits, viewer markers, dataset schema note; regenerate demo replays; refresh verification evidence | eval owners (viewer, dataset) | Fingerprint change, so every verification record is refreshed |

### 8.2 Existing tests

* **`tests/test_navigation_robot.py` `TestDockBattery`** keeps working, with one exception.
  `test_battery_runs_out_and_strands` still sees `ok` on the truncated walk, battery 0.0 and the next walk refused.
  Its failure label changes to `robot_stranded`.
* **Line 1491** (`obs["nav"]["battery_pct"] == 100.0`, oracle) is unchanged; realistic `obs.nav` buckets come from
  navigation_robot's adoption of `body.proprio`.
* **Kernel test C8** (fault `time_s` rounding) is unchanged.
* **`tests/test_perception.py` and `tests/test_kernel.py`** need updates wherever they pin realistic `obs.robot`
  keys or `stats.energy` values: KB-4 owns `test_perception.py` and KB-2 owns `test_kernel.py`.
* **Domain tests that assume free waiting** (battery exactly 100 after waits) must use `assertGreater` bounds;
  `body_audit` lists them.

### 8.3 The 242 verified specs

* **Expected verdict changes: none**, except possibly in M5.
* **Specs to re-check by hand:**
  * T-5ad61b6881821b-01 (battery predicate);
  * A-d66994a566899c-01 (60 h undocked waits);
  * every spec using `careful`: the 7 files listed by `body_audit`.
* **Optional realism improvement (main session):** long-wait references dock first. It is not required to stay
  green.

### 8.4 Eval harness, dataset, viewer, leaderboard

* **Harness:** the agent contract is unchanged (`ok`, `message`, `category`, observation). Realistic `obs.robot`
  gains buckets and estimates and loses exact floats. Power-down steps are `ok=True` with a message, and later steps
  are refused with `PowerOff`; `--max-consecutive-invalid` users should know this.
* **Dataset:** the observation schema changes; bump the dataset format version in `docs/DATASET.md`. The compact
  mode keeps dropping the top-level `battery` when it equals `obs.robot.battery` (both integers in realistic mode).
* **Viewer:**
  * frames add `pw` (`power_state` when not "on"), `bs` (`battery_state` when not ok) and `ft` (detected fault
    kinds);
  * timeline markers for `power_down`, `robot_fell`, `overload` and `fault_*` events;
  * `DROP_PATHS` adds `robot.energy_wh_used`, `robot.energy_wh_charged`, `robot.battery_temp_c`,
    `robot.battery_min_pct`, `robot._idle_s`, `robot._activity`, `robot._held_kg`.
* **Leaderboard and verification:** the code fingerprint changes, so verification evidence and submissions verified
  against the old fingerprint are stale and must be re-run.

### 8.5 Docs

| Doc | Change |
|---|---|
| `KERNEL_API.md` | New section "v3.0 robot body" (KB-2) |
| `PERCEPTION.md` | "Proprioception" section (KB-4) |
| `CONVENTIONS.md` | Ownership row "robot body: kernel", the robot field table, carrying limits via `body.capacity_kg` (KB-1) |
| `AUTHORING_GUIDE.md` | "Robot body in specs": section 13 recipes (KB-3) |
| `KNOWN_LIMITS.md` | Approximations of section 1 (KB-1) |

---------------------------------------------------------------------------------------------------------------------

## 9. Performance budget

Measured baseline: 2.2 ms per 30 s substep (whole engine, 649 entities, all domains) and 15.5 ms per `look` step
including the observation.

| Item | Budget | Notes |
|---|---|---|
| Body system per substep | <= 0.02 ms (< 1 % of a substep) | O(1): no entity scans; uses cached `_held_kg` |
| Settlement per leaf action | <= 0.1 ms | One `carried_kg` (about 0.05 ms for two held containers), arithmetic, one journaled robot write |
| Motion prefix | <= 0.05 ms per 40-cell path | Linear in path length |
| Proprio render per observation | <= 0.05 ms | |
| Week-long wait (20,160 substeps) | <= 0.4 s added | |

Measure with `tools/bench_systems.py 40 0 1 2 3 --domain core` and the full set. `tests/test_body_engine.py` holds a
micro-benchmark that fails above 3x budget.

---------------------------------------------------------------------------------------------------------------------

## 10. Test plan

New test files run in kernel-only builds (`HOUSE_SIM_DOMAINS=core`) and in an all-domain pass.

**`tests/test_body_model.py` (KB-1)**

1. Profiles merge and `inherits`; overrides; `apply_profile` keeps explicit robot fields.
2. Activity power and rates (115 W awake, 20 W parked, holding 5 kg adds 15 W; docked-live is 0).
3. The temperature cutoff: gauge equals battery at 20 C; at -10 C power-down happens at SoC 30 and the gauge comes
   back when warm.
4. CC/CV closed form against a 1 s Euler reference within 1e-3; 0 to 80 % in 40 min; refused below 0 C; halved
   below 10 C; wake at 3 %.
5. Pack temperature relaxation; overheat applies the fault and recovers.
6. Thresholds, events, `body_log`, `battery_min_pct`; reserve breaches count only undocked.
7. Capacity derates (normal pick unchanged; overhead x0.6; above the head x0.6; on a stool x0.5; limp x0.5); total
   carry; `hold_capacity_left`; `stability_margin` values.
8. Grasp size classes (auto pinch, refused power grip, too small); precision with a dirty camera, darkness, a weak
   arm and soil; `grip_mu`.
9. Stopping distances (dry and wet); noise table; quiet delta.

**`tests/test_body_engine.py` (KB-2)**

10. Settlement:
    * `look`, `wait` and `speak` now cost energy;
    * a nested `go_to` charges each leg once;
    * the parent pays only its own seconds;
    * legacy `energy=` becomes extra work.
11. Truncation:
    * a 20 m walk with energy for 8 m ends at the prefix, `ok=True`, room unchanged, power off, `robot_stranded`
      in failures;
    * the next action raises `PowerOff`;
    * `wait` is allowed while off.
12. Partial commit: a chain that powers down in leg 2 keeps leg 1 and returns `ok=True`; a refusal raised before any
    power-down still rolls back.
13. Non-motion shortfall: within the buffer it completes and then powers down; beyond the buffer it is an atomic
    refusal (`stats.energy_refusals`).
14. Chunk independence: one 3600 s wait vs 120 waits of 30 s.
15. Double-drain guard (all domains): a 10 m walk drains within 5 % of the formula, so navigation_robot does not drain
    a second time.
16. Careful timing: `move` takes 2x normal, not 3x; `go_to` legs are not multiplied again.
17. Quiet-mode `noise_db` refusal; `robot_sound` emitted.
18. Strict mode; save/load and replay round trip including a power-down; perf micro-benchmark.

**`tests/test_body_core.py` (KB-3)**

19. Guards keep their wording per verb.
20. `pick_up` overload: counter, touch grant, nothing dropped, today's refusal text.
21. A latent weak arm is detected on the first overload, and the other arm succeeds.
22. Core `inspect`/`read` with imaging off; `listen`/`ask` with hearing none or addressed; speech with a failed
    speaker is inaudible.
23. Stairs refused for `mobile_manipulator` and for weak legs; limp-mode speed clamp; `set_speed fast` refused while
    quiet.
24. Setup ops: `robot` with `profile` and overrides; `robot_fault` immediate, `at_s`, `until_s` and `choose`.

**`tests/test_body_faults.py` and `tests/test_body_perception.py` (KB-4)**

25. Legacy fault normalization (string and dict).
26. `log_fault`: logged vs unfounded vs undetected; `diagnose` detects latent faults.
27. Gripper slip draws are identical across two runs and in a replay.
28. `arm_failed` while holding: drop, or transfer to the other arm.
29. `fall`: one `falls` stat, posture lying, held items dropped via `item_dropped`, undocked, damage rules by height
    and load.
30. Water: spray limit, jet, immersion.
31. `Perception.visible` with privacy mode on (only shapes of 0.4 m and up, both modes) and with power off (only
    held items).
32. The `obs.messages` hearing filter; relocalization by `look`.
33. Proprio: realistic shows an integer battery, buckets, no latent faults and no hidden fields; oracle keys equal
    today's plus the registered ones.
34. The perception audit's robot check.

**Integration:** the stage gates of section 8.1. `tools/fuzz.py` gains `--battery LOW` and `--faults random` options
(KB-4).

---------------------------------------------------------------------------------------------------------------------

## 11. Implementation plan: kernel work packages (disjoint file sets)

**Day 0.** KB-1 lands a skeleton of `kernel/body.py`: every signature of section 5.1, constants, the profile loader,
and functions that reproduce today's behaviour (no energy, today's limits). The other three packages code against it
in parallel. Each package orders its items by stage, so the main session can merge M1 to M5 in order.

| Package | Exclusive files | Items by stage |
|---|---|---|
| **KB-1 Body model and energy** | `house_sim/kernel/body.py` (new), `house_sim/data/robot/profiles.json` (new), `house_sim/kernel/schema.py`, `tests/test_body_model.py` (new), `docs/CONVENTIONS.md`, `docs/KNOWN_LIMITS.md` | **M1:** profile loader, `inherits`/overrides, field registry and kernel field registration, `normalize` integration (profile defaults, materialized fields, `arms_ok` mirror, `battery_temp_c` from the start room, `power_state` from the gauge), `get`/`sensors_view`/problem queries. **M2:** energy math, `affordable_prefix`, `settle`, `power_down`/`wake`, thresholds/`body_log`/limp, body system (idle, CC/CV, temperature window, pack thermal, network window), `charge_rate`, `swap_pack`, `robot_stranded` invariant, `robot.battery`/`robot.network` events. **M3:** `carried_kg` cache, `capacity_kg`/`check_lift` core logic, `hold_capacity_left`, `stability_margin`, grasp size, precision, `grip_mu`, force limits, hand soil helpers. **M4:** `can_hear`/`hears`/`can_speak`/`imaging` final rules, `stopping_distance_m`, `noise_db`, `effective_speed`. |
| **KB-2 Engine and Ctx** | `house_sim/kernel/engine.py`, `house_sim/kernel/actions.py`, `tests/test_body_engine.py` (new), `tests/test_kernel.py`, `docs/KERNEL_API.md` | **M1:** ActionSpec attributes and decorator parameters; `ctx.body`, `require_*` wrappers with legacy messages; `Ctx.obj` and `free_hand` through the body. **M2:** `ctx.motion`/`cut_motion`, `nested_s` in `perform`, settlement calls in `step` and `perform`, power-off guard, partial-commit rule, `_advance_mode`, energy refusal path, legacy energy conversion. **M3:** `ctx.check_lift`/`require_grasp`/`require_precision`. **M4:** quiet `noise_db` refusal, `robot_sound` emission. **M5:** careful factor on own seconds, `timing="speed"`. |
| **KB-3 Core verbs and setup** | `house_sim/systems/core.py`, `house_sim/tasks/setup.py`, `house_sim/tasks/skills.py`, `tests/test_body_core.py` (new), `docs/AUTHORING_GUIDE.md` | **M1:** `move`/`approach`/`walk_to`/posture use `ctx.require_mobile(legacy_message)`; `SPEEDS` reads the profile. **M2:** `ctx.motion` in the three motion verbs (stairs direction and energy, `extra_s` for doors and doorways, prefix handling, `connection=None` on a cut); core skills (`go_room`) raise `PlanError("robot powered down")` instead of retrying. **M3:** `pick_up`/`receive`/`uninstall` use `ctx.check_lift` + `ctx.require_grasp`; held-item handling unchanged otherwise. **M4:** `inspect`/`read` `require_imaging`; `look` relocalizes; `listen`/`ask` hearing; `say()` audible/audience; `set_speed`/`set_quiet` rules; stairs capability. **M2 to M4:** setup ops (`robot` profile and overrides, `robot_fault`). **M5:** `timing="speed"` on core motion verbs. |
| **KB-4 Faults, falls, perception, tooling** | `house_sim/kernel/body_faults.py` (new), `house_sim/kernel/proprio.py` (new), `house_sim/kernel/perception.py`, `house_sim/kernel/realism.py`, `tools/perception_audit.py`, `tools/body_audit.py` (new), `tools/fuzz.py`, `tests/test_body_faults.py` (new), `tests/test_body_perception.py` (new), `tests/test_perception.py`, `docs/PERCEPTION.md` | **M0:** `tools/body_audit.py --baseline`. **M1:** `proprio.render` in oracle mode (today's keys plus registered fields); `normalize_faults`. **M2:** realistic battery gauge, `battery_state`, `runtime_min_est`, `range_m_est`. **M4:** fault registry and built-in kinds, `add_fault`/`detect`/`clear`, `diagnose`, `log_fault`, `slip_check`, `fall`, `drop_held`, `self_right_problem`, `expose_water`, `degrade_camera`, `robot.fault`/`clear_fault` events; `Perception.visible` imaging and power gates; `obs.messages` hearing filter; realism clarity from `sensors_view`; full realistic proprio; perception-audit robot check; fuzz options. |

**Main-session integration duties:**

* merge the packages per stage;
* run the gates;
* coordinate the must-land domain items (section 8.1);
* regenerate demo replays and verification evidence at M6;
* hand the viewer and dataset follow-ups to the eval owners.

---------------------------------------------------------------------------------------------------------------------

## 12. Domain follow-ups (all 16 domains)

The kernel packages never edit domain files. Items marked **(M2)** must land in the same merge as stage M2. Every
other item can follow at the owner's pace, each with its own tests.

* **navigation_robot**
  * **(M2)** `traverse.bookkeeping` stops writing battery. It keeps odometry, coverage and docking state, and calls
    `ctx.cut_motion` when a trip stops early or the robot falls.
  * **(M2)** `robot_system` stops charging and stops the idle drain, and registers the `robot_charge_source` hook
    (seated, `dock_powered`, dock rate).
  * **(M2)** Treat a battery-cut core move as an in-room trip: no `_passage`, no `cross_stairs`.
  * **(M2)** `step` uses `ctx.require_mobile` and `ctx.motion`.
  * **(M2)** Remove the `navigation_robot.stranded` invariant and update its test.
  * **(M5)** `timing="speed"` on `step`, `go_to`, `follow`, `lead` and `patrol`.
  * Later:
    * `ev_robot_fault` delegates to `robot.fault`;
    * `robot_falls` uses `body.fall`; `drop_item` uses `body.drop_held` (consequences move to manipulation's
      `item_dropped` hook);
    * `log_fault` uses `body_faults.log_fault`; `swap_battery` uses `body.swap_pack`, and `robot_battery_pack` gains
      `capacity_wh` (open question 6);
    * `clean_camera` clears `camera_dirty` and scratches through `degrade_camera`; `get_up` uses
      `self_right_problem`;
    * new verb `run_diagnostics`;
    * `request_remote_help` clears `localization_lost` and `software_stuck`;
    * drop the `_no_camera` before-hooks and the `measure.py` privacy checks in favour of `ctx.require_imaging`;
    * `obs.nav` uses `body.proprio` (buckets in realistic mode);
    * declare its robot fields;
    * `_sudden_encounter` uses `body.stopping_distance_m`;
    * `stairs.py`/`reach.py` use `body.capacity_kg` and `stability_margin`;
    * the constants `ROBOT_MASS_KG`, width, `SOUND_AT_1M_DB` and `SPEECH_DB` come from the body;
    * `climb_on` refuses in limp mode;
    * weather grime uses `degrade_camera(cause="weather")`;
    * `_spoke` checks `entry["audible"]`.
* **manipulation**
  * `require_payload`/`needs_two_hands` become `ctx.check_lift`/`body.capacity_kg`.
  * `support mode=heft` uses the derated capacity and never counts an overload.
  * `ROBOT_PUSH_N`/`ROBOT_WRIST_TORQUE_NM` become `body.limit`; `force_n` parameters are clamped with a note.
  * Register `item_dropped`: breakage from fragility defaults, spills from open liquids.
  * The stair stumble drops the item through `body.drop_held` and stops counting `falls` unless the robot falls.
  * Throw and catch accuracy uses `precision_mm`; grasp slip uses `grip_mu`; keyed connector insertion uses
    `require_precision` where tolerances exist.
  * Annotate loud verbs with `noise_db`.
* **mechanisms**
  * `grip_force_n` is clamped to `body.grip_limit_n`; `lids.WRIST_MAX_NM` becomes `body.limit(state, "wrist_nm")`;
    `FORCE_N` levels are clamped to `push_n`.
  * Optional: small-button precision on keypads and touchscreens via `require_precision`.
* **outdoor_plants**
  * `lawn.py`, `yard.py` and `pool.py` falls become `body.fall`; pool uses `water=True` and stops appending the
    string fault.
  * Snow too heavy becomes an overload attempt (`check_lift`-style record) instead of a free fall.
  * Sprinkler, hose and pool exposure use `body.expose_water`.
  * Mower, blower and trimmer get `noise_db`; delete the quiet checks in `verbs.py`.
  * Mud and sap on the grippers use `soil_hand`.
* **safety**
  * The `inspection.py` structure collapse uses `body.fall(height_m=...)` and `body.total_mass_kg` instead of
    `ROBOT_MASS_KG`.
  * Optional: smoke and soot use `degrade_camera(cause="smoke")`, and energized flood water raises a robot fault.
  * Extinguisher handling uses `check_lift`.
  * Capability-envelope helpers (V-dd9bf6dc2d9a9f-01) use profiles and randomized capacity.
* **people**
  * Hearing uses `body.hears` wherever `heard_by_robot` is set; `addressed_robot` marks speech addressed to the
    robot; ambient speech is suppressed in the sounds channel when hearing is "addressed".
  * Listeners ignore inaudible robot speech.
  * `FOOTSTEP_DB`/`SPEECH_DB`/`QUIET_SPEECH_DB` become `body.noise_db`.
  * Register `robot_sound` as a sound source.
  * Privacy capture counts use `body.imaging`.
  * Later: residents can swap the battery of, or dock, a powered-down robot (a handoff resolution).
* **food**
  * Declare `hand_contamination` (hidden, owner food).
  * Coating, flouring and oiling verbs call `body.soil_hand`.
  * Optional: fine verbs (separating eggs, decorating) use `require_precision`.
* **cleaning**
  * `wash`/`wipe` of the robot's grippers (target `robot_hand`) calls `body.clean_hand`.
  * Vacuum, steam mop and carpet cleaner get `noise_db` (about 70 dB); replace the check in `floors.py`.
* **plumbing_liquids**
  * `fill`/`pour` into a held container is capped by `body.hold_capacity_left` (stop at the limit with a note).
  * A hose spray aimed at the robot, or standing in a running shower, uses `body.expose_water`.
  * Optional: water on the grippers uses `soil_hand("water")`.
  * The disposal noise is the appliance's.
* **tools_vehicles**
  * Drill, hammer and pressure washer get `noise_db` and drop the hand-written quiet checks (`tools.py` x2,
    `surfaces.py`).
  * Torque and force inputs are clamped to `body.limit`.
  * Two-handed power tools declare `arms=2`.
  * Screws and other small parts are covered automatically by the core grasp classes.
* **textiles_laundry**
  * `thread_needle` uses `require_precision(0.5 mm)`, relaxed with a needle threader; `sew_button` uses 1.5 mm.
  * Folding thin items with a parallel gripper follows the core thin-flat rule.
* **appliances_cooking**
  * Optional: loud appliances (blender, grinder, food processor) refuse to start in quiet mode through their
    `cycle_check:<type>` hooks using `body.quiet_limit_db`. Appliance noise is the appliance's, not
    `ActionSpec.noise_db`.
  * Nothing else is required.
* **climate**
  * No change required. Room `temperature_c` must stay current in rooms the robot occupies, because the pack
    temperature reads it through `physics.room_temperature`.
  * Optional: report heat-wave or freeze variants for battery derate specs (V-da03eefb5e876d-01).
* **electrical**
  * No change required. A tripped breaker on the dock circuit already stops charging through `physics.is_powered`
    and navigation_robot's charge-source hook.
  * Keep the robot pack out of electrical's `charge` verb, except on a robot charging station.
  * Optional: robot contact with live wires raises a robot fault.
* **pets**
  * Pet stress and startle from robot noise and speed use `body.noise_db` and `body.speed_mps`, or `robot_sound`
    sources.
  * Nothing else is required.
* **admin_inventory**
  * `weigh_luggage lift=true` and luggage carrying use `check_lift`.
  * Paper clips, staples and other small stationery are covered by the core grasp classes; check its verified specs
    with `body_audit` at M3.
  * Shredder and printer noise is the device's.

---------------------------------------------------------------------------------------------------------------------

## 13. Spec recipes (for the authoring guide)

All of these vary by seed (`variant = seed % len(variants)` on seeds 0, 1, 2, 41, 97), so that both branches occur.

* **Battery reserve on a long chore or patrol.** Set `battery` so the full route needs a mid-task dock.
  * Success: `robot.battery_min_pct gte 15` and `robot.docked` at the end.
  * Wrong plan: skip docking. It either breaches the reserve or strands the robot.
* **Heavy object.** `uniform` mass from 0.6 to 1.4 times the capacity, or randomize `max_hand_kg`/`max_mass_kg`. The
  reference hefts, then carries or asks.
  * Success includes `stats.overload_attempts eq 0`.
  * Always-carry produces an overload; always-ask fails the question score.
* **Failing arm.** `robot_fault arm_weak` on a random side, latent. The reference runs `run_diagnostics` or detects
  it on the first lift, then uses the other arm or both.
  * Failure predicate: `stats.overload_attempts gte 2`.
* **Dirty camera.** `camera_dirty`. A read needs `clean_camera` first.
  * Wrong plan: a paper towel leaves scratches; `robot.camera_clean lt 0.95` fails it.
* **Network down.** The reference asks a person who is present instead of calling `request_remote_help`, which is
  refused.
* **Cold battery.** Robot outdoors at -10 C. `range_m_est` shrinks, so a plan that ignores it strands; the reference
  docks early.
* **Privacy.** Occupied bathroom: knock and get consent with full hearing, then turn privacy mode on. Hearing is
  "addressed", so "please leave" is still heard.

---------------------------------------------------------------------------------------------------------------------

## 14. Open questions

1. **Design B never reached this judge.** The verdict compares A with a constructed alternative (B'). If a second
   design exists, the main session should check it against sections 0, 4.6 and 11.
2. **Outcome of power-down steps.** Truncated motion and interrupted chains return `ok=True` with a "powered down"
   message, keeping `ok=False` atomic. The alternative is A's `ok=False` without a rollback. Please confirm.
3. **Walking energy calibration.** It keeps navigation_robot's 0.288 Wh/m (cost of transport about 1.8, about
   900 W while walking). Recalibrating to about 0.16 Wh/m (cost of transport about 1.0, Figure/Optimus claims)
   would lengthen runtimes by about 40 %.
4. **The M5 careful-speed fix** may change verdicts in up to 7 spec files. Land it only with an empty verdict diff,
   or accept re-authoring?
5. **Privacy-mode microphones.** The catalog says "cameras and mics off". This spec keeps addressed-speech listening
   for safety and consent. Confirm, or make it fully off (and accept that "stop" is not heard in privacy mode).
6. **Pack capacity.** navigation_robot documents `robot_battery_pack` as 1 kWh (6.5 kg), while the humanoid_v1
   battery is 2.4 kWh. Either give packs `capacity_wh` 2400 (about 14 kg, which needs two hands to hold for a swap),
   or let a swap honestly install the smaller pack.
7. **Reconciliation with the physics and camera tier-1 designs:**
   * who owns drop and impact breakage (the `item_dropped` hook vs a kernel drop function);
   * whether the physics layer also claims payload;
   * heading and gaze fields and `camera_height_m`;
   * that `sensors_view` is the camera layer's only robot input.
8. **Long waits.** Should long-wait references, such as A-d66994a566899c-01's 60 h waits, dock for realism? Not
   required: they pass at 20 W parked standby.
