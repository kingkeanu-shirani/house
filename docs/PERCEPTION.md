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
* People and pets in an unlit room are shapes keyed by a handle that lasts one dark spell (`obs["people"]` /
  `obs["pets"]` keys, messages, `inspect`'s `agent`); naming any person or pet by id while the robot's room is dark is
  refused ("It is too dark to tell who is who here..."); inspecting a shape gives only `label` "person"/"animal" and a
  note and records no inspection.
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

Tools: `python tools/perception_audit.py [houses] [steps]` runs random walks in realistic mode and reports leaks (true
ids -- including ids of things destroyed during the step or earlier in the episode, and the ids of people/pets that
are shapes in a dark observation; a plain descriptive word that equals a hand-written id is not one --, id-keyed
JSON that stays in true-id order, secret or
never-tier fields, exact values above the robot's knowledge unless remembered by design, unperceived hazard sources,
exact values in action messages) attributed to the emitting module.

## Not modeled

No images or depth maps, no learned recognition errors beyond the declared look-alike groups, no camera field of
view or line-of-sight occlusion inside a room (only containment, concealment, darkness and the small-object model),
no smell/taste chemistry beyond the domains' symbolic channels.

Note: an unresolved look-alike shows the group's `kind` (group spec `"kind"`, default the group name) and only the
fields every member shows (`LOOKALIKE_FIELDS`), sorted, so neither a member's category nor its field set or field
order can identify it.
