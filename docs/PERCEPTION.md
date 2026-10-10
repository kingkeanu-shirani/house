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

Realistic perception is **symbolic**: there are no pixels, no learned detector and no camera geometry. It decides
what information reaches the agent, at what precision, from the true state and the robot's situation.

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

## Knowledge

The robot's knowledge is tracked per entity and field (glance < inspect < touch < read < measure), outside world
state, derived from the actions that succeeded, so replays and saved states reproduce it. Handlers record what an
action revealed with `ctx.grant(eid, "inspect" | "touch" | "read" | "measure", fields=None)` (core `inspect`, `read`
and `pick_up` do this; measuring actions grant their fields). If the object moves, or a field's value changes, that
knowledge lapses back to glance: it may have changed since.

## Quantization and noise

Continuous values are rendered by knowledge level:

* soil: glance clean/light/dirty/very dirty; inspect per-kind none/trace/light/moderate/heavy/caked
* wetness: glance "wet" only when visibly wet (damp seams look dry); inspect dry/damp/wet; touch about +-0.04; meter exact
* temperature: glance (thermal channel) cold/cool/room temperature/warm/hot/very hot; thermometer about +-0.3 C
* fill: empty/low/half/full/overflowing; inspect to 5 %; measure exact
* mass: touch light/medium/heavy/very heavy; scale exact
* size: camera estimate to 5 cm; tape exact
* count: a few/several/many; inspect exact up to 12; counting exact

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
* Mirrors and reflective glass show an "open doorway" percept until inspected.
* People and pets: posture and activity at a glance; moods only as cues (crying, pacing, wincing, raised voice);
  sleeping looks like "lying still" until inspected.

## Verification in realistic mode

Specs whose skill is perception declare `"perception": "realistic"`. The verifier then runs the reference and wrong
plans in realistic mode (plans are authored with true ids; the runner translates them to handles, as an agent would
use) and adds a perceivability check: every value a success predicate compares a report against must have appeared
in what the reference actually observed. Each such spec also needs a wrong plan that acts on glance-level belief
(picks the look-alike, reports a bucket instead of a measured value) and fails.

Tools: `python tools/perception_audit.py [houses] [steps]` runs random walks in realistic mode and reports leaks (true
ids, secret or never-tier fields, exact values above the robot's knowledge, unperceived hazard sources, exact
values in action messages) attributed to the emitting module.

## Not modeled

No images or depth maps, no learned recognition errors beyond the declared look-alike groups, no camera field of
view or line-of-sight occlusion inside a room (only containment, concealment, darkness and the small-object model),
no smell/taste chemistry beyond the domains' symbolic channels.

Note: an unresolved look-alike shows the group's `kind` (group spec `"kind"`, default the group name) and only the
fields every member shows (`LOOKALIKE_FIELDS`), sorted, so neither a member's category nor its field set or field
order can identify it.
