# Action API

```python
from house_env import HouseEnv, make_house
env = HouseEnv(make_house(room_count=20, floors=3, seed=42))
observation = env.reset()
result = env.step("move", "room_2")
```

`step(action, target='', destination='', **kwargs)` returns `ok`, `message`,
`observation`, `evaluation`, `reward`, `terminated` and `truncated`. Normal action
results also have a failure `category`. Use `terminated` or `truncated` to end
training episodes. Reward is the change in evaluation score, so repeated looks
after completion do not repeatedly earn a success reward. Score is 1 for complete
success, minus bounded invalid-action/unnecessary-question penalties.

Failed accepted actions are atomic, but consume one step and one simulated
second; scheduled external events can still happen. Non-string positional API
arguments are rejected before entering the action clock. A failed guard is
measured separately from an actual harmful world outcome.

| Actions | Main conditions/effects |
|---|---|
| `look`, `inspect`, `read` | Partial observation; private inspection needs permission; the colour cameras must be on |
| `move ROOM` | Adjacent, lit, unblocked, permitted room; connecting doors open |
| `pick_up ID`, `place ID DEST` | Visible portable object; one hand, or both for heavy or large items; derated lift capacity, grasp size, containment checks |
| `hand_over ID PERSON` | Visible consenting recipient in same room; real containment transfer |
| `open`, `close`, `lock`, `unlock` | Supported mechanism, matching held key, no obstruction or running cycle |
| `wipe`, `dust`, `scrub`, `sweep`, `vacuum`, `mop` | Compatible held tool, capacity/uses, surface affordance, utility/quiet constraints |
| `wash`, `rinse`, `dry`, `disinfect`, `polish` | Water/material/tool prerequisites; detergent and soil accounting; documented dwell time |
| `fold`, `unfold`, `flatten`, `coil`, `uncoil`, `tighten` | Appropriate symbolic affordance and state; no cloth/topology mechanics |
| `fill`, `pour`, `water_plant` | Positive bounded `amount_l`, source supply, capacity; clean water only |
| `switch_on`, `switch_off` | Supported switch; life-safety equipment cannot be disabled |
| `start`, `pause`, `set_mode` | Washer/dryer/dishwasher cycle state machine; load, door, power, water and compatibility checks |
| `ask PERSON`, `topic=...` | Scripted clarification or scoped permission; resident available; microphones on |
| `speak`, `announce`, `listen`, `refuse`, `handoff` | Simulated communication; no real network communication; `listen` needs the microphones |
| `report`, `field=...`, `value=...` | Requires inspection; evaluator checks truth independently |
| `remember`, `forget` | Supported preference memory previously supplied through clarification |
| `stop`, `resume`, `set_quiet`, `set_speed` | Robot control; appliance clocks and external events keep running; `set_speed fast` refused while quiet or limp |
| `wait`, `seconds=N` | 1–3600 simulated seconds; no real sleeping |
| `watch`, `run_pending` | Observable predicate queues commands once; each queued action uses normal guards/duration |
| `snapshot NAME`, `diff NAME`, `undo` | Snapshot comparison; guarded inverse of last reversible action |

Full executable transformation definitions are in `house_sim/data/actions.json`.
Call `env.action_names()` for supported names and `env.affordances(id)` for a
side-effect-free availability/reason probe of object interaction candidates.
Affordance queries do not enumerate every multi-parameter action combination.

Named arguments use JSON in the CLI:

```json
{"action":"water_plant","target":"target_1","destination":"watering_can","amount_l":0.25}
```

For headless evaluation, put one action object per line in a file and run
`py house_env.py --task water --headless actions.jsonl`.

Undo uses inverse actions and current preconditions. It cannot restore elapsed
time, replenish consumed water, reverse washing, move a hidden object, or cross
a door that has since closed. Irreversible operations clear the undo stack.
Snapshots are for comparisons; there is no agent action to load arbitrary state.

## Robot body (kernel v3.0)

The robot has one body (`kernel/body.py`, docs/KERNEL_API.md "v3.0 robot body"): a profile (`humanoid_v1` by default:
60 kg, 1.6 m, 2.4 kWh, two hands of 8 kg each, 20 kg with both), a battery that every action drains, sensors that can
be switched off or fail, and limits the core verbs enforce. Body refusals carry fixed texts (no hidden numbers) and
reveal only the robot's own state or what a touch revealed.

**Lifting** (`pick_up`, `receive`, `uninstall`). Two hands are needed for an item heavier than one hand's derated
capacity, wider than 0.7 m, marked two-handed, or with `hand=both` / `grip=two_hand`; otherwise the free hand (right
first, or the one named). Capacity is derated: x0.6 for an item above the head (higher than 1.7 m) or reached from the
extra overhead cell, x0.5 on a climbing aid, x0.5 in limp mode (battery at or below 5 %, weak legs), times the arm's
health. Both hands together carry at most 20 kg. Over the limit the lift is aborted at lift-off: "Object and contents
exceed carrying capacity; get help or slide it" (safety); nothing moves or drops, `stats.overload_attempts` counts it,
the event log gets `overload`, and in realistic perception the robot has felt the weight (touch tier of `mass_kg`). A
weak arm the robot did not know about is found by the first lift it cannot take ("The left arm could not take the load
(actuator weakness detected)", capability): use the other hand or both. In limp mode a lift that only the limp derate
forbids is refused with "The battery is too low for a lift this heavy; charge first" (capability). A person's
hand-over (`receive`) and a part coming out of its slot (`uninstall`) follow the same rules; a heavy part comes out
into both hands.

**Grasp size** (humanoid hand; a parallel gripper wraps from 5 mm and pinches from 1 mm). Items smaller than 15 mm are
taken with a pinch when no grip is given (a "pinch grasp" note); `grip=normal | firm | handle | two_hand | underneath`
on them is refused ("Too small for a power grasp: use grip=pinch"); below 2 mm nothing can grasp them ("Too small to
grasp; sweep it up, use tape or a tool"). A paper-thin item lying on a surface is slid to the edge first (+3 s, a
note); a parallel gripper needs `grip=edges` or `grip=scoop` for it. Grasp refusals teach the item's size in realistic
perception.

**Holding.** A load that grows after the lift (a container being filled, limp mode) strains the hand above its
capacity (a "Straining to hold ..." note) and slips out above 1.5 x (it falls; `stats.robot_drops`). The robot's end
of a load carried together with a person, and a cart it wheels, count only the robot's share.

**Sensors.** `look`, `inspect`, `read` and `peek` need the colour cameras: with privacy mode on, the cameras switched
off or a room privacy policy they refuse with "Cameras are off; turn privacy mode off to look closely" (perception);
with the camera hardware failed, "Camera fault: request help" (capability). `look` in a lit room also lets a robot that
lost its pose find it again (a "Relocalized" note; the walking verbs refuse with "Localization lost: look around in a
lit room or request remote help" until then). `listen` and `ask` need the microphones ("Microphone is off" /
"Microphone fault: request help", capability); in privacy mode only speech addressed to the robot is heard, so `ask`
still works and "stop" is still heard. Speech through a failed speaker is inaudible (its message entry has `audible:
false` and no audience); the robot notices the fault when it hears no echo.

**Speed.** `set_speed careful | normal | fast` is the user's setting; `fast` is refused while quiet mode is on ("Quiet
mode is on: fast walking is too loud; turn quiet mode off first") or in limp mode (precondition). A walk goes at the
effective speed: quiet mode turns fast into normal, limp mode or weak legs turn any speed into careful (with a note).
Careful speed slows a walk once (0.4 m/s is already half of normal: `move`, `approach`, `walk_to` and navigation's
`step`, `go_to`, `follow`, `lead` and `patrol` are timed by their speed) and every other physical action by 1.5 x its
own seconds, nested actions included, never twice.

**Stairs.** A flight of stairs needs a body that climbs them: "This robot cannot climb stairs" (a wheeled mobile
manipulator) or "Weak legs: stairs are not safe now" (capability).

**Power.** A walk the battery cannot finish stops where the battery ran out: ok, "Battery depleted after X m; the robot
powered down". While powered down every action except `wait` is refused ("The robot is powered down (battery
depleted); it must be charged or have its battery swapped"); a robot on a live dock charges and wakes at 3 %.

## Camera verbs (kernel v3.0 geometric camera)

`HouseEnv(..., camera="geometric")` (or a spec's `"camera": "geometric"`, or `--camera geometric`) poses the robot's
cameras: what it sees and can act on follows line of sight, light and resolution from its eye (docs/KERNEL_API.md
"v3.0 camera"). Three verbs control the cameras; in room mode (the default) `look` ignores its new parameters (1 s,
"You look around") and `face` / `peek` refuse with "Camera control needs camera: geometric" (category capability;
`env.action_names()` does not offer them there). Angles are grid degrees: 0 east, 90 south (the convention of
`obs.camera.pose.facing_deg`); relative bearings in `view` dicts are positive to the robot's right.

| Action | Parameters | What it does (geometric) | Time |
|---|---|---|---|
| `look` | `target` (optional): `forward` (default), `around`, `up`, `down`, `left`, `right`, `behind`, a thing, person or pet in the room, or a cell `[c, r]`; or `pan` / `tilt` (degrees); `rows` 1-2 (`rows=2` only with `around`); `track` true / false | one frame ahead (pan 0, tilt -10); around: six frames 60 degrees apart covering the whole room (rows=2: six more at tilt -45 for the floor 0.7-3.3 m away), the head back ahead after; up / down: tilt +20 / -45; left / right: pan -90 / +90; behind: the body turns round (if it cannot, the head pans to its limit); a target: the head aims at it, the body turning beyond the 150-degree pan limit; `track=true` keeps the named person or pet (or everyone the look sees) in frame during later waits and idle actions, `track=false` stops | forward 1.0 s; around 5.4 s (rows=2: 8.3 s); aims: the slowest of the pan (120 deg/s), tilt (90 deg/s) and body turn (90 deg/s) + 0.4 s |
| `face` | `target`: north, east, south, west, left, right, back, a thing, person or pet in the room, a cell `[c, r]`; or `deg` (grid heading) | turns the body in place; the head returns to straight ahead (tilted at a target). Works with the cameras off and without a known pose. Refused when fallen, with an actuator fault or a flat battery ("Robot cannot move"), with failed legs or an overheating pack (the body's texts), on a climbing aid ("Step down first", safety) or stopped | turn / 90 deg/s + 0.3 s |
| `peek` | `target` (a thing within reach, or held), `where`: `under`, `behind`, `inside`, `top`, `closely` | the wrist camera (87 degrees, its own LED light) looks from the target's edge nearest the robot: under it (crouch or kneel first and it needs 6 cm of clearance; a light cushion, rug or mat is lifted instead), behind it (a 5 cm gap; light supports are lifted), inside it (open, glass or open shelving: one frame per shelf), on top of it (within reach + 0.2 m), or closely (a held thing is turned to show every side). Things it sees under or behind become `revealed`, within 1 m of the edge (deeper ones stay hidden); `camera_log.peeked["<id>:<where>"]` (inside: `looked_in`); its detections appear in the observation with `view.via = "wrist"` | 6 s, +2 s under / behind, +1 s lifting |

Geometric rules of the existing perception verbs (TIER1_CAMERA 9.2):

* `inspect` needs the head camera to resolve the target (160 pixels across with light 0.2: a 1 m fixture from about
  5.8 m, a 10 cm item from about 0.6 m) or the wrist within reach (+2 s); otherwise "Too far to make out the details;
  approach it" or "Too dark". `where=under|behind|inside` is a wrist peek first (+2 s, +1 s lifting) and lists only
  what the wrist saw under / behind; `where=closely` on a held item turns it in the hand (+3 s). Printed fields appear
  only when the cameras can read them from there (through the face they are printed on). People and pets must be in
  line of sight. `contents` lists what the cameras can see or the wrist reach.
* `read` reads with the head (10 pixels a character: 4 mm print from 0.37 m, 3 cm signs from 2.8 m; the printed face
  turned toward the robot, light 0.25, no glare, a clean lens) or the wrist within reach (+2 s; a held item is turned
  to a glare-free angle, +1 s); it returns, and grants, only the fields that camera can read. Big text (displays,
  signs, screens) reads from across the room; refusals: "Too small to read from here", "Glare on it; change the angle",
  "Too dark", "Camera too dirty to read text; clean the camera", "You cannot see it from here".
* Any action on a thing outside this step's frames turns the head to it first (auto-gaze, charged once per thing per
  step); `approach` ends facing its target with the head aimed at it.
* With the cameras off (privacy mode, `camera_on` false, a room policy) `look`, `peek`, `inspect` and `read` refuse
  with "Cameras are off; turn privacy mode off to look closely" (category perception), in both camera modes (see
  "Robot body"); `peek` also needs a working arm (Ctx.obj's texts for an actuator fault, a flat battery or a fallen
  robot; "Both arms have failed; request help").

The observation gains `camera` (pose, frames this step, glare, light, the room's coverage, tracking, visual events
seen) and `lidar` (24 ranges at 0.25 m), and every listed thing, person and pet carries a `view` (bearing, elevation,
distance bucket, range, pixels, occluded fraction, via head / wrist / depth / held, light). Only what a frame of this
step detected is listed.
