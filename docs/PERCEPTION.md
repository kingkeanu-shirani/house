# Perception

The simulator has two perception modes, chosen per environment:

```python
env = HouseEnv(config, task, seed, perception="realistic")   # or "oracle" (default)
```

and per task spec with `"perception": "realistic"`, or for a whole verification run with
`python -m house_sim --check FILE --perception realistic` / `--verify --perception realistic`.

* **oracle** returns exact records for everything visible (true ids, exact soil/wetness/temperature numbers, owners,
  homes...). It is the default so the verification pipeline and every existing spec behave as before. Use it for
  debugging and for tasks that are not about perception.
* **realistic** returns what a camera-and-sensors robot in that room could plausibly perceive. The robot must
  approach, inspect, read, touch, measure or ask to learn more, and it can be fooled by look-alikes, darkness,
  distance, clutter, glare and a dirty camera. This is the recommended mode for evaluating agents.

Realistic perception is **symbolic**: there are no pixels and no learned detector. It decides what information
reaches the agent, at what precision, from the true state and the robot's situation. A second, independent switch
chooses the camera: `camera="room"` (the default: everything in the robot's room that containment, concealment,
darkness and the small-object model allow) or `camera="geometric"` (kernel v3.0: the robot's posed cameras, with line
of sight, light and resolution; see "Geometric camera" below). Both work with both perception modes.

## Handles

Every entity is exposed under a per-episode opaque handle such as `obj_7f3a19c2` (a hash of the seed, a digest of the
initial house that agents never see, and the true id). Handles appear as keys of `visible_entities`, in `location`
values, connection `door` fields, the known map, action messages and every observer channel. Room, person and pet
ids stay readable (the robot knows its house and residents).

`env.step` accepts handles only: a true entity id, or a handle the robot was never shown, is refused exactly like an
invisible object ("That is not visible here"). Handles inside strings (for example `watch` predicate paths) are
translated too. Results are translated back, so domains never deal with handles. Evaluators, specs, replays and
`privileged_state()` keep true ids. Entities mentioned by sound/smell channels that the robot cannot see become
"something", so channels cannot be used to identify unseen things.

## Field tiers

Each entity field is learnable at one tier:

| tier | how | examples |
|---|---|---|
| glance | seen from the room | coarse label/kind, color, rough size, relation (on/in/under), open/closed, position, obvious states (shattered, smoking, overflowing), soil and wetness as a qualitative bucket, fill as empty/low/half/full |
| inspect | `inspect` within reach (large fixtures from a few cells) | material, soil per kind (none/trace/light/moderate/heavy/caked), wetness dry/damp/wet, damage, freshness, locked, settings, contents of open containers, finer fill levels |
| read | `read`, lit, with a clean camera | text, label_text, expiration, care labels, recipient, instructions, displays, prescription names |
| measure | a tool (touch gives a coarse bucket) | temperature (thermometer), mass (scale), size (tape measure), moisture (meter), battery charge (tester) |
| never | asking or inference only | owner, home, value, sentimental/important flags, intent, decoy flags, secret_fields, underscore fields |

The kernel's defaults are by field name (`kernel/realism.py` `DEFAULT_TIERS`). Types override them in their JSON:

```json
"medicine_bottle": {"is_a": "container", "props": {...},
  "perception": {"read": ["patient", "dosage"], "measure": {"pill_count": "count"}, "hidden": ["prescriber_note"],
                 "looks_like": "pill_bottle"}}
```

Unknown fields default to the inspect tier (never glance).

Parts of dict/list fields take dotted names: `"hidden": ["mech.slams", "buttons.*.holds_n", "deep.outer.inner.x"]`
(`*` matches any key or list position; any depth), `"inspect": ["buttons.*.position_error_mm"]` (that part needs a
close look; inspect-level floats get inspect noise), `"measure": {"hopper.kg": "scale"}` (that part needs the tool;
grant it with `ctx.grant(eid, "measure", ["hopper.kg"])`), and the render kind `"count": ["leaves.healthy"]` (an
integer shown as a few/several/many at a glance, exact up to 12 on inspection; top-level fields may be counts too).
The most specific matching rule wins. An entity may carry its own `"_perception"` block of the same shape (dotted
names, `count` and `read` included); its tiers win over the type's, and `secret_fields` hide a field unless the type
names a tier for it. `realism.read_tier_fields(registry, record)` (what `read` returns) includes the entity's own
`_perception["read"]` fields, so text a domain writes onto another domain's entity can be made legible.

Hook: `@hook("perceivable_value") fn(state, eid, record, field) -> value or None` gives the perceivable part of a
record field before it is rendered (observations and inspect results), e.g. soil on visible parts only. Set
`fn.fields = ("soil",)` to be called only for those fields (otherwise it is called for every field of every
perceived entity, which costs observe time). Knowledge still follows the true value.

## Knowledge

The robot's knowledge is tracked per entity and field (glance < inspect < touch < read < measure), outside world
state, derived from the actions that succeeded, so replays and saved states reproduce it. Handlers record what an
action revealed with `ctx.grant(eid, "inspect" | "touch" | "read" | "measure", fields=None)` (core `inspect`, `read`
and `pick_up` do this; measuring actions grant their fields). If the object moves, or a field's value changes, that
knowledge lapses back to glance: it may have changed since. Knowledge of things the robot carried, set down, or
displaced itself during the step (a `"displaced": true` marker the step set, e.g. navigation_robot kicking a toy
aside) survives the move.

## Quantization and noise

Continuous values are rendered by knowledge level:

* soil: glance clean/light/dirty/very dirty; inspect per-kind none/trace/light/moderate/heavy/caked
* wetness: glance "wet" only when visibly wet (damp seams look dry); inspect dry/damp/wet; touch about +-0.04; meter exact
* temperature: glance (thermal channel) cold/cool/room temperature/warm/hot/very hot; thermometer about +-0.3 C
* fill: empty/low/half/full/overflowing; inspect to 5 %; measure exact
* mass: touch light/medium/heavy/very heavy; scale exact -- both include the liquid inside (`liquid_l` x
  `liquid_density`, default 1 kg/l), as core carry-mass does
* size: camera estimate to 5 cm; tape exact
* count: a few/several/many; inspect exact up to 12; counting exact
* odor `{kind: intensity}`: faint/noticeable/strong; damage `{kind: severity}`: light/moderate/severe (amounts below
  2 % are not perceptible); exact only with a tool the type declares (`"measure": {"odor": ...}`)
* any measure-tier `*_temp_c` field read with a thermometer: the thermometer's fixed draw (about +-0.3 C)
* noisy fractions stay in [0, 1]: `*_level`/`*_fraction`-style names, `remaining`, `shine`, `wrinkled`, `pilling`,
  `faded`, `dye_damage`, and any float whose type default lies in [0, 1]

Uncertainty grows with distance, low light, a dirty camera, fast motion, glare (low sun through an uncovered window)
and steam or smoke. Noise and bucket slips are a pure function of (seed, entity, field, knowledge level, viewpoint),
so repeated glances from the same spot give the same estimate (no free averaging).

## Detection, look-alikes, novel objects, reflections

* Small objects (< 15 cm) can be missed at a glance; the chance rises with distance, clutter on the same surface,
  darkness and tiny size, and is stable per viewpoint. Stepping within a cell, or inspecting the surface or
  container, finds them.
* Look-alike groups (salt/sugar/baking soda jars, similar pill bottles, keys, remotes, unlabeled spray bottles,
  water/vinegar, socks...) show a shared descriptor and a candidate list until resolved by inspect or read as the
  group requires; unlabeled members of read-resolved groups stay unidentified ("unidentified chemical"). Domains add
  groups with `"looks_like"` in their type JSON (file-level `"looks_like": {"group": {"descriptor", "resolve",
  "members"}}` or per type `perception.looks_like`).
* Types with `novel: true` are described only by visual attributes (size class, shape, color; material after
  inspection), never by name.
* Group keys: `descriptor`, `resolve`, `kind`, `candidates`, `label_fields`, and (round 5) `portion_descriptor`,
  `color`, `size_m`, `size_class`. An unresolved member shows the group's color and size instead of the ones its
  type gives it: the group's keys, else the most common member color and the typical (per-dimension median) member
  size, rendered as the usual camera estimate. A color or size the instance itself was given (differing from its
  type's default, e.g. a red sock among same-type socks) is what the camera sees and stays, unless the group sets that
  key; a tape-measured size stays the measured one. The registry warns (`LookalikeWarning`,
  `registry.lookalike_warnings`) when members declare colors or sizes a camera would tell apart (sizes more than
  5 cm and 10 % apart) without group keys; `registry.lookalike_groups()` / `lookalike_types()` list the merged groups
  and the types each covers.
* What a container holds (`liquid`, `contents`, `product`, `chemical`... -- core `CONTENT_FIELDS`) never counts as a
  label: an unlabelled member stays unidentified after reading.
* Portions (`"derived_from": <source id>`) are identified when the source is (at the source's knowledge level), or
  by the portion's own label / an inspect-resolved look at it -- reading a label-less portion identifies nothing.
  Unresolved, a portion shows the group's `portion_descriptor` (default "contents of <descriptor>"); identified, its
  own label and category. What the robot knew of a destroyed source is remembered.
* Provenance: a destination that received liquid/contents/product carries `"contents_from": <source id>`. While the
  source is unresolved (and the destination has no label the robot has read), its `liquid` / `contents` / `product`
  render as the source group's `portion_descriptor`, else "unidentified liquid/contents from <descriptor>"
  ("unidentified liquid/contents" for a novel source); it clears once the source is identified.
* Inspect results of unresolved members drop domain extras whose key contains a word that tells members apart
  (true type, category, label words, ancestors), not words every member of the group shares ("food").
* Public helpers: `realism.unresolved(eid, record=None)` (an unresolved look-alike, unidentified portion or novel
  thing at the robot's current knowledge; False for unknown ids) -- `ctx.unresolved/identified` wrap it --
  `realism.sealed(record)` (closed opaque container), `realism.group_glance(name, group)`.
* Mirrors and reflective glass show an "open doorway" percept until inspected.
* People and pets: posture and activity at a glance; moods only as cues (crying, pacing, wincing, raised voice);
  sleeping looks like "lying still" until inspected. Lying awake and asleep look the same: posture "lying", activity
  "lying down", grid hazard kind "lying" (a fallen person stays "person_down"); a pet lying, resting or asleep shows
  posture "resting" and a "lying" hazard marker. Someone dozing in a chair keeps posture "sitting" and shows no
  activity (sleep/rest activities of anyone not lying are not visible).

## Messages

Action messages are translated after the step: ids of things perceived now or named in the command become handles,
other ids "something" -- including ids of things the step destroyed (merged, consumed, pulled), which become their
handle if the robot had one. Free-text (and other non-entity string) arguments neutralise every true id and every
word shaped like a generated id on a registry type name (`fabric_softener_1`), whether or not such a thing exists, so
the echo of the agent's own words never tells which ids exist; words naming parts of the command's target (a slot
`layer_2`) stay. True names of unresolved look-alikes the command named -- the names of their whole group: every
member type's label and type name, every in-house member's label -- and the label/type of unfamiliar things are
replaced by their perceived label in one pass (an inserted descriptor is never re-scanned; the perceived descriptor,
kind and candidate strings are kept verbatim), so every member of a group gets byte-identical messages for the same
action. An unfamiliar thing's name is not rewritten inside the agent's own echoed words (that would confirm a
guess). JSON messages are parsed, translated value
by value (keys get id translation only; `label`/`descriptor`/`kind`/`candidates` values the kernel rendered are left
alone) and re-serialised with sorted keys: id-keyed dicts come out in handle order, and lists of ids (or of records
with an `id`/`object`/`item`/`entity`/`target` key) that were in true-id order are re-sorted by handle -- except
under keys naming an order (`order`, `sequence`, `path`, `route`, `stack`, `queue`, `steps`, `history`, `trail`,
`rank`). Emit an explicit position if a list's order matters and its key does not say so. Id-keyed dicts in
observation channels are re-ordered by handle the same way.

## Acting on what the robot cannot tell apart (kernel v2.9)

* Guards whose outcome depends on what a thing really is must answer the same for every member of its look-alike
  group until the robot identifies it: `ctx.unresolved(eid)` / `ctx.identified(eid)` (wrapping
  `realism.unresolved`) and `ctx.refuse_unidentified(eid, message=None)` (one generic refusal). Core does this for
  the hazard guard of pick_up/place/receive/uninstall (the strictest rule of any member TYPE must be met by the grip
  the command gave or the item is held with -- never one a handler picked from hidden `safe_grips` -- or by the gear;
  refusal names no hazard) and for handing things to a child. A novel thing belongs to no group: never refused for a
  hidden hazard, always refused for a child.
* An identified thing is judged by what the robot can tell: its type's hazards (and risk), plus its own hazards once
  it inspected them (or the break is visible). A hidden instance hazard or risk does not refuse. Whenever an act goes
  ahead and the true rule is not met, the exposure counts as a safety violation.
* Never-tier permission flags (`private`, `off_limits`, `requires_approval`, `risk >= 3`): refusing would reveal
  them, so in realistic mode the act proceeds and counts as a privacy/authority violation unless the robot can
  perceive the rule (a perceivable tier it knows, a legible "PRIVATE"/"do not touch" cue it has read, an approval it
  was granted naming the thing -- not a mere question, nor a refused request --, an instruction or heard speech
  pairing a cue with what the robot takes it to be, or a rule every kind it could be carries). Oracle mode refuses as
  before. Private things look and read like anything else (an open private box shows its contents; the text of a
  private note shows after a read): reading private papers without permission is itself the violation (core `read`).
* People and pets in an unlit room (or any room while the cameras are off: privacy mode, a camera switched off or
  failed, a room privacy policy) are shapes keyed by a handle that lasts one dark spell (`obs["people"]` /
  `obs["pets"]` keys, messages, `inspect`'s `agent`); naming any person or pet by id while the robot's room is dark is
  refused ("It is too dark to tell who is who here...", with the cameras off "With the cameras off it is too dark to
  tell who is who...") in every parameter -- `ask`'s about, a report's subject and to, options, a wait's or a standing
  order's predicate path -- with the same words for someone there and someone elsewhere, so a typed id never comes
  back as a shape handle; in free text (a `text` parameter) such an id becomes "someone"; a command over a remote
  channel (`channel` phone, text or app) names people as they are and its reply keeps them plain; inspecting a shape in an unlit room gives only `label` "person"/"animal" and a note and records
  no inspection (with the cameras off `inspect` is refused before that: docs/ACTION_API.md "Robot body").
* Route planning (core move/approach/walk_to with `avoid`) uses only the hazards the robot perceives: nothing in a
  dark room, anyone who looks like lying down (asleep or awake) for `sleeping`/`person_down`/`lying`, no seated
  sleeper's or concealed agent's ring, no invisible floor hazard.
* The clock the agent sees reads whole seconds (`time_s`, the calendar view, timestamps in observer channels and
  messages), and the computed parts of a step's duration last whole seconds in the realistic world itself, so a
  duration that depends on a hidden quantity cannot be measured below a second (not even by offsetting the clock with
  actions of known fractional duration). Oracle mode keeps exact durations. Domains round durations their own messages
  print.
* `ctx.fixed_rng(*parts)` gives a step-independent random stream for instrument error that must not average out.

## Verification in realistic mode

Specs whose skill is perception declare `"perception": "realistic"`. The verifier then runs the reference and wrong
plans in realistic mode (plans are authored with true ids; the runner translates them to handles, as an agent would
use) and adds a perceivability check: every value a success predicate compares a report against must have appeared
in what the reference actually observed. Each such spec also needs a wrong plan that acts on glance-level belief
(picks the look-alike, reports a bucket instead of a measured value) and fails.

Tools: `python tools/perception_audit.py [houses] [steps] [--camera room|geometric] [--battery PCT] [--faults random]`
runs random walks in realistic mode and reports leaks (true
ids -- including ids of things destroyed during the step or earlier in the episode, and the ids of people/pets that
are shapes in a dark observation; a plain descriptive word that equals a hand-written id is not one --, id-keyed
JSON that stays in true-id order, secret or
never-tier fields, exact values above the robot's knowledge unless remembered by design, unperceived hazard sources,
exact values in action messages, robot self-state beyond its proprioception tiers and broken sensor gates) attributed
to the emitting module.

## Physics (kernel v3.0)

* **Hidden thresholds.** `core.json`'s file-level `field_tiers` hide every physical threshold and simulation
  parameter (drop tolerances, crush and load ratings, hook and handle ratings, friction, heat, melting and shock
  limits, openings and mouths, nesting, absorption, flotation, drag, cushions, centre of mass, `two_handed`,
  `phys_exact`, `phys_creep_s`). A domain file sorted after `core.json` could re-expose one: `tools/physics_audit.py
  --data` lists every such override.
* **Consequences are public.** At a glance: `fell`, `tipped_over`, `toppled`, `shattered`, `collapsed_shelves`,
  `sagging`, `wobbly`, `in_motion`, `blown_away`, `torn`, `secured`, `tilt_deg`, `casters`, `wheeled`; on inspection:
  `damage` (all kinds), `broken`, `anchored`, `perforated`, `foot_material`, `pads`; read: `load_rating_text`. A slow
  drip (`dripping`) keeps the default inspect tier: it is noticed close up, and a kernel drip shows by its wet spot.
* **Feel and looks** (inspector `physics`): `feel` ("hard, rigid", "soft, flexible", ...) once the robot has touched
  the thing, unless a domain's inspector already gave its own, more specific feel (laundry's "damp" / "dry"); `looks` ("sturdy" / "flimsy") for a rated support on a close look (`inspect` with `where="closely"`), from
  its type's nominal rating class (never the per-instance threshold). No numbers.
* **Sounds** (observer `physics.sounds`, channel `sounds`): `{"kind", "source_hint", "loudness"}` with words only;
  sounds in the robot's room name their `source` (a handle, or "something" when the robot does not perceive it).
* **Notes.** Physics consequence notes in action messages name ids only ("cup_1 fell and broke"), which realistic
  mode translates; they never carry numbers. `tools/perception_audit.py` checks both rules.

## Geometric camera (kernel v3.0)

```python
env = HouseEnv(config, task, seed, perception="realistic", camera="geometric")   # or "room" (default)
```

The mode comes from the CLI `--camera`, then `HouseEnv(camera=...)`, then the spec's `"camera"`, then
`camera.default_mode(perception)` ("room" until the D3 switch-over makes it "geometric" for realistic perception;
oracle verification stays in room mode). The geometry is `kernel/sight.py`, the runtime `kernel/camera.py`
(docs/KERNEL_API.md "v3.0 camera"); this section is what an observation contains.

**What is listed.** Only what a stationary frame taken this step at the robot's current eye framed (plus what it
holds): the step view of `camera.end_step` (motion frames feed the camera log, never the observation). In oracle mode a
thing is listed when it is detected at 17 px or more (the 50 % point) or seen as a depth shape; in realistic mode the
10-24 px band is a draw (below).

**View levels** (realistic, from the best current frame; TIER1_CAMERA 11.1):

| level | needs | the record |
|---|---|---|
| shape | the depth camera only (dark, or imaging off): 0.4 m and larger within 4 m | `label` "dark shape", `kind` "unknown", `size_class`, `location`, `pos` |
| detect | 10 px or more and light 0.05 | `label` "object", `kind` "unknown", `size_class`, `shape`, `location`, `relation`, `pos`/`spos`; `color` in light 0.15 when not backlit |
| recognize | 32 px and light 0.12, not in the sun's cone | the category word as `label` and `kind` (a look-alike its group identity, a novel thing "unfamiliar object"); the large-scale states of `RECOGNIZE_FIELDS` (open, lit, on fire, smoking, steaming, overflowing, leaking, flooded, tipped over, toppled, fallen, shattered, spilled, covered, posture, fell, collapsed shelves, in motion, blown away); a container's fill bucket from 48 px |
| identify | 64 px and light 0.2, not backlit or in the sun's cone | the full glance rendering of room mode |

Below identify the field set depends only on the level and the conditions, never on the type (an unresolved
look-alike keeps only `LOOKALIKE_FIELDS`), and what an inspection, reading or measurement taught the robot (and still
holds) stays. Inspect, read and measure tiers are unchanged: they need the grants of `inspect` / `read` / tools, which
the camera's `inspect_check` / `read_check` gate (docs/ACTION_API.md).

**Detection band.** A thing 10-24 px across is detected with p = (px - 10) / 14 - 0.05 x max(0, siblings on its
support - 3), drawn once per viewpoint: a fixed draw keyed by (seed, thing, the robot's cell, the frame's 45-degree
heading sector, the camera). Repeated looks from one pose agree; stepping or turning re-rolls. A thing the robot
inspected (and has not seen move), or found by inspecting what holds it, is always detected; anything absent from every
current frame is absent (no new handle; what the robot knew is kept). `realism.missed()` follows the band.

**Per-target context.** Noise and bucket slips use the target's own conditions: light at the target, distance
(`dist` = range / 0.5 m), and m = the viewpoint terms (dirty lens, fast motion, steam or smoke) + 0.6 log2(128 / px)
below 128 px + 0.8 x occluded + 1.5 below light 0.3 (0.5 below 0.45) + glare (frame or sun 1.0, backlit 1.5). The
hour-of-day glare and cell-distance terms of room mode do not apply. Draws stay fixed per (thing, field, level).

**Per-field acuity.** A type (or an entity's own `_perception`) may say what view a glance field needs:
`"perception": {"acuity": {"crack": "inspect", "browning": "recognize"}}`. Defaults: `RECOGNIZE_FIELDS` at
recognize, every other glance field at identify. "inspect" acuity means a head view of 160 px in light 0.2 or the
wrist (a grant of the field keeps it).

**Face gating.** A field gated to a declared face (`view.faces`, `label_facing`, or a type's `"perception":
{"faces": {field: face}}`; `label_text`, `recipient`, `address`, `barcode`, `order_id` follow the label face) shows
only when a current frame saw that face, unless a grant of it still holds. Things without declared faces are never
gated.

**Tracked identity.** Knowledge entries gain `"ident": [level, placement signature]`: the best view level the thing
was seen at since it last moved (an inspection that still holds counts as identify). While it has not moved, a later,
coarser view keeps its identity fields (label, kind, look-alike candidates), never its condition. The engine updates
it once per step (`realism.note_step(view)` after `camera.end_step`, and at reset), so observations never depend on
how often `observe()` runs; `dump` / `load`, saves and replays keep it. Handles still follow true ids (an unseen swap
keeps the handle; docs/KNOWN_LIMITS.md).

**People and pets.** Identified people keep their ids and the cues of room mode. Those the camera saw but could not
identify (a depth shape with the cameras off, too far, too dim or hazy) are shapes `{"label": "person" | "animal",
"location", "pos", "view"}` keyed by a handle that lasts one sighting (a new sighting cannot be linked to the last);
naming such a person by id is refused as in the dark. In a dark room the dark-spell rule applies as in room mode.

**The view.** Every listed record has a `view` (oracle: exact; realistic: `bearing_deg` and `elev_deg` to 5 degrees,
relative to the body heading, positive to the right; `dist` (within reach, near, mid, far, very far); `range_m` only
within the depth camera's 0.2-6 m for targets of 24 px or more, a fixed draw with sigma 0.01 + 0.005 d^2 to 0.1 m;
`px` to 2 significant figures; `occluded` in quarters; `via` head / wrist / depth / held; `light` as a word, none for
depth shapes). `view` and `inner_spos` record fields are never shown (a domain's own `view` word, outdoor_plants' roof
faces, keeps its tier).

**Camera and lidar blocks.** `obs.camera` = `{"pose": {"cell", "facing_deg", "facing", "pan_deg", "tilt_deg",
"height_m"}, "frames", "glare", "light", "coverage", "tracking", "events"}`: the pose exactly (proprioception; its cell
mirrors `obs.robot.pos`), this step's frame count, sun glare, the light at the eye (realistic: a word), the current
room's floor coverage (realistic: 2 decimals), the agents the head tracks and the visual events seen this step
(realistic: bearings to 5 degrees, ids as handles or "something"). `obs.lidar` = `{"height_m", "sector_deg",
"ranges_m"}` (24 sectors, 0.25 m steps, null beyond 8 m), present while the depth sensors work. Grid hazards are
listed only on cells a frame of this step surveyed or with a detected source (realistic: after the band). An
un-inspected mirror looks like a doorway only while it is in frame. `camera_log` (coverage maps, looked-in containers,
peeks, sightings, captures, events) is world state for evaluators and never appears in observations.

**Tools.** `tools/perception_audit.py --camera geometric` adds the camera leak checks (no `_view` / `camera_log`
key; only detected things listed, each with a contract `view`; camera and lidar blocks within their contracts; no
camera data in room mode); `tools/fuzz.py --camera geometric` fuzzes oracle and realistic houses with the camera's
verbs and checks cross-process determinism; `tools/bench_systems.py --camera geometric` adds the observe cost.

## Proprioception and the robot's sensors (kernel v3.0 robot body)

The robot body (`kernel/body.py`; docs/KERNEL_API.md "v3.0 robot body") decides on every call what the robot can
sense; perception applies it in both perception modes and both camera modes (TIER1_INTEGRATION 1.3 and 1.12,
TIER1_EMBODIMENT 4.10 and 4.14).

**`obs.robot` and `obs.battery`** (`kernel/proprio.py`, built inside `perception.observe`).

* Oracle: the robot record exactly. Today's keys (`pos`, `posture`, `hands`, `battery`, `stopped`, `quiet`,
  `privacy_mode`, `speed_mode`, `standing_on`, `camera_clean`, `network`, `faults`, `fallen`, `docked`) plus every
  registered robot field that is present and not hidden: `power_state`, `standby`, `charging`, `battery_temp_c`,
  `arm_health`, `arms_ok`, `legs_health`, `sensors`, `fault_state` (latent faults marked `latent: true`, a debug view),
  the profile ratings, `facing_deg` in geometric mode, domain fields such as `dock_powered`. `obs.battery` is the true
  charge to 3 decimals.
* Realistic: what the robot's own sensors and battery management report.

| key | value |
|---|---|
| `battery` | the gauge as an integer % (half up); the top-level `obs.battery` equals it |
| `battery_state` | ok, low, reserve, critical or depleted |
| `runtime_min_est` | gauge x believed full energy / awake idle power with empty hands, floored to 5 min ("believed": nominal until a degraded pack is detected, so a weak pack shows as an optimistic estimate) |
| `range_m_est` | (gauge - reserve) x believed full energy / normal walking cost with empty hands, floored to 50 m. Neither estimate follows the carried load: an estimate that moved with what the robot holds would weigh it to about 0.2 kg, finer than the touch tier's mass bucket (the robot is not a scale) |
| `battery_temp` | cold (< 5 C), normal, warm (> 40 C), hot (> 55 C) |
| `camera` | clear (clarity 0.85 and above), smudged, dirty, obscured (below 0.4), or off while imaging is off |
| `hearing` | full, addressed or off |
| `network` | connected or offline |
| `arms` / `legs` | per hand ok, weak (once detected) or failed / ok, weak or failed |
| `load` | per hand, joint effort relative to that hand's believed capacity: none, light, moderate, heavy, at_limit (not a scale) |
| `grip` | per hand ok, or slipping once a slip happened |
| `hand_soil` | per hand clean, soiled or slippery |
| `faults` | detected faults and the robot's own fault-log entries (its notes, its falls, legacy domain entries): `{"kind", "part", "time_s", "logged"}`, the clock in whole seconds |
| `pos` | the cell, or None while the robot is unlocalized (the room stays known); in geometric mode `obs.camera.pose.cell` mirrors it |
| every other registered exact field | as it is: `posture`, `hands` (handles), `power_state`, `standby`, `charging`, `docked`, `localized`, `speed_mode`, `quiet`, `privacy_mode`, `headlamp_on`, `standing_on`, `fallen`, `facing_deg`, the nominal ratings ... |

Never shown: `battery_health`, `battery_min_pct`, `energy_wh_*`, `body_log`, `lens_scratch`, the raw `arm_health`,
`legs_health`, `sensors` and `fault_state`, hand-soil amounts, `water_exposure_s`, `profile_overrides`, underscore
fields, latent faults, and any robot field nobody registered (`body.register_robot_field`). Domains add channels with
`@hook("proprio") fn(state, out, realistic)`, respecting the tiers. Without the `"proprioception"` sensor `obs.robot`
keeps only `pos`, `posture` and `hands`. Domain channels that still show exact self-state (navigation_robot's
`obs.nav`: `battery_pct`, `camera_clean`) move to proprio buckets in the domain adoption round.

**Sensor gates.**

| robot state | what the observation and the actions' visibility checks do |
|---|---|
| imaging off: privacy mode, `camera_on: false`, a failed camera, a room `privacy_policy` naming the room under `camera_off` (`when` "always", or "occupied" while a person is there) | the depth camera alone. Room mode applies the darkness rule to the whole room: things of 0.4 m and up are `"dark shape"` records, smaller ones are not listed, people and pets are shapes keyed per dark spell in realistic mode (naming one by id is refused: "With the cameras off it is too dark to tell who is who: address the shape you see"), no grid hazards, no weather through a window. What the robot holds stays visible; the room's map fixtures (non-portable furniture and fixtures standing in it, its doors) stay targetable by actions without being listed. `look`, `inspect`, `read` and `peek` are refused ("Cameras are off; turn privacy mode off to look closely", or "Camera fault: request help" for a failed camera: docs/ACTION_API.md "Robot body"). The geometric camera lists IR depth shapes (TIER1_CAMERA 9.3) and keeps the lidar |
| powered down | only what the robot holds or wears is visible or targetable; no people, pets, hazards, weather, sounds, messages, lidar or observer channels. The map (room, exits, connections, blocked cells) and `obs.robot` stay; every action but `wait` is refused |
| hearing "addressed": privacy mode, a `mic_off` policy | `obs.messages` keeps only speech addressed to the robot (`to == "robot"`, or marked `addressed_robot`); non-speech sounds stay, in `obs.sounds` and as notices (glass breaking, a crash, an alarm: the notice bus drops only speech to others); `listen` returns only that speech and the notices as perceived, and `ask` still works (an answer is addressed speech) |
| hearing "none": `mic_on: false`, a failed microphone, powered down | `obs.messages` is empty and every sounds channel is dropped (`sounds`, `mechanism_sounds`, a channel's own `sounds` list); `listen` and `ask` are refused ("Microphone is off" / "Microphone fault: request help") |
| a dirty or scratched lens | clarity = `camera_clean` capped by 1 - `lens_scratch` (`body.clarity`, the `sensors_view` clarity): below 0.4 small things (< 8 cm) and text are not seen in room mode, and realism's lens noise term uses it |

`obs.dark` is true whenever the cameras give no colour view of the room: a dark room without a robot light, or imaging
off (`obs.robot.camera` says which). Observers key their visual percepts on it.

**Tools.** `tools/perception_audit.py` checks every realistic observation for the robot leak kind `robot`: only
registered non-hidden fields and the proprioception keys in `obs.robot`, an integer battery equal to `obs.battery`, no
position while unlocalized, no latent fault anywhere (by its id) or in `obs.robot.faults`, no hidden-tier robot field
in an observer channel, and the sensor gates above. `--battery PCT --faults random` (also on `tools/fuzz.py`) starts
the houses with an almost flat pack and random robot faults, so powered-down, deaf, blind and faulty robots are
audited and fuzzed too.

## People, requests and memory (kernel v3.1)

docs/DIALOGUE.md is the reference; what perception does for it (TIER1_SWEEP 7, 10.2, 17; TIER1_INTEGRATION 1.12):

* **What the robot perceived.** `perception.perceived_ids(env)` (`Perception.perceived_ids`) is the set of ids
  `observe()` renders as themselves, cached per step: room camera -- the visible things observe lists (dark shapes
  included; small map fixtures while the cameras are off are not), with realistic perception minus the small things
  missed from this viewpoint (and what they hold) and un-inspected mirrors (they show as openings), plus the people
  and pets of the room (none while powered down; with realistic perception none in the dark, where they are shapes);
  geometric camera -- the step view's detections (realistic: after the detection band; people and pets only when
  identified). The engine updates the robot's Mind from it once per step (after `realism.commit_grants`,
  `camera.end_step` and `realism.note_step`), so beliefs are the same whether or not the agent observes, and with
  realistic perception every perceived thing's handle is issued then.
* **Memory messages keep issued handles.** The messages of `recall`, `diff` and `snapshot` name every thing whose
  handle was issued before (perceived this episode or known from prior knowledge), in view or not; anything never
  perceived stays "something"; times read whole seconds. Every other message keeps the rule of "Messages" above. At
  reset the handles of every id in prior beliefs, prior snapshots and memory values are issued.
* **Answers** (`ask`) are messages like any other: an entity id in a person's answer becomes a handle only when the
  robot sees the thing now or named it; people speak in descriptions (`refers_to`, clarify `options`), never handles.
  Report replies never say whether a report is right; an unknown word's suggestions are the same for every member of
  a look-alike group.
* **obs.request** (episodes with requests only) is the current request with `from` as the robot can tell it: the
  person in person (a household member, or a visitor it knows), the dark-spell shape handle while its view of them is
  dark, "unfamiliar person" for a visitor it does not know, the contact on the phone or app ("unknown caller" for a
  stranger), "note" with the note's handle; decided the first time it is shown and saved with realism. A relayed
  request is said by the relayer (`from`, decided for the relayer) and `claims_from` names who the relayer says it
  comes from; `room` is shown only for a request spoken where the robot is (in person, overheard, intercom), never for
  a phone, app, text or note request. The request's own speech in `obs.messages` names its speaker by the same
  decision.
  `obs.instruction` is the request's text. In a multi-phase episode the current request is the active phase's (between
  phases, the phase that ended last); a phase that came without a request shows no `obs.request` and its own
  instruction (`perception.current_request`, the record `env.request()` returns too).
* **obs.notices** (when there are any) lists the notices stored during the current step -- only perceived notices are
  stored (the notice bus, kernel/timeline.py) -- translated like a channel (a source the robot has no handle for is
  "something"), with times in whole seconds; none while powered down. After `env.phase_done()` (which counts no step)
  only the notices perceived since it was called (the next phase's request among them, as `addressed`).
* **obs.weather** never shows the weather process's bookkeeping (`process`, `override_until_s`).
* **Waits and standing orders** (kernel/timeline.py, TIME-EVAL) end or fire only on what the robot perceives now, per
  referenced id: a thing's glance field while it is seen (not a dark shape; geometric: in the head camera's view at
  the level the field needs), a person's location or posture while the robot can tell who it is (realistic: lit room;
  geometric: identified in the view), "someone comes in / leaves" (a location compared with the robot's room) while
  it watches its room, an alarm by its sound, the weather by the weather rule (never its bookkeeping; through a window
  without the network only the condition word); a condition the robot cannot observe now is refused when issued.
  Notices name their source only when the robot perceives it.

`tools/perception_audit.py` exercises canonical questions, typed reports, recall, snapshot and diff on its walk (its
houses run with the notice bus on and half its waits listen with `interrupt_on`) and checks them (leak kind `people`): no handle in clarify options or `refers_to`; no handle in a memory message that was
neither issued before the step nor of a thing in view; report suggestions for an unresolved thing equal to the
generic list; no notice while powered down, no audio notice while the robot hears nothing, no notice source handle
that was never issued; no request naming a person who is a shape in the dark. Since the stage-C fix its houses also
carry a request with a hidden target, prior knowledge of look-alikes seen at a glance and dark rooms with people, and
it checks that an answer's `about` is the agent's own argument (never the request's target), that a recalled label is
never the true name of a thing the robot cannot tell apart unless an observation showed it, that a person's id typed
in the dark never comes back as a shape handle, and that no wait(until) is accepted on a thing not in view, on a dark
shape's field or on a person in the dark.

## Not modeled

No images or depth maps, no learned recognition errors beyond the declared look-alike groups, no smell/taste
chemistry beyond the domains' symbolic channels. Room mode has no camera field of view or line-of-sight occlusion
inside a room (only containment, concealment, darkness and the small-object model); the geometric camera's own
approximations (box-shaped occluders on the 0.5 m grid, scalar light, rooms seen only up to their walls, no mirrors)
are in docs/KNOWN_LIMITS.md.

Note: an unresolved look-alike shows the group's `kind` (group spec `"kind"`, default the group name) and only the
fields every member shows (`LOOKALIKE_FIELDS`), sorted, so neither a member's category nor its field set or field
order can identify it.
