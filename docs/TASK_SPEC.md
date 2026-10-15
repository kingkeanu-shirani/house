# Task specification

`tasks/specs.jsonl` contains one requirement record per catalog ID. A supported
record names a family, parameters, three command phrasings, a seeded generator,
predicate template, risk triage, budgets, target fidelity and current fidelity.
Unsupported records explicitly have no generator or predicate template.

`house_sim.scenarios.build(spec, seed)` materializes a concrete world and task.
Fully expanded sample bundles are in `tasks/examples/`; load them with
`py house_env.py --scenario tasks/examples/clean.json`.

A concrete task has this form:

```json
{
  "schema_version": 1,
  "catalog_id": "custom-example",
  "instruction": "Put the cup in the sink without moving the book.",
  "success": [{"path":"entities.cup.location","op":"eq","value":"sink"}],
  "failure": [{"path":"stats.damage","op":"gt","value":0}],
  "preserve": ["entities.book_1.location"],
  "step_budget": 100,
  "time_budget_s": 600
}
```

Paths use dot-separated dictionary keys. Atomic operators are `eq`, `ne`, `gt`,
`gte`, `lt`, `lte`, `in`, `contains`, and `exists`. Logical combinations are
`{"all":[...]}`, `{"any":[...]}` and `{"not":...}`. No Python evaluation occurs.
Missing paths fail ordinary predicates. All top-level success predicates must
hold; any failure predicate or changed protected property defeats success.

Room/object IDs use lowercase letters, digits and underscores. `hand` is
reserved. Structural cycles, disconnected layouts, invalid references and
invalid supported numeric fields are rejected before a run. Starting layouts
use schema version 1 for compatibility with the supplied source.

Catalog IDs hash kind, section and normalized text, with an occurrence suffix.
Duplicate wording remains separate, linked by a duplicate-group hash. Exact
source lines and byte spans remain in the normalized catalog; raw files preserve
the original bytes. Fused headings are normalized without rewriting the source.
The incomplete adaptability fragment is BLOCKED rather than silently completed.

Risk levels in requirement records are conservative keyword triage, not a
certified risk assessment. Runtime guards use actual object/context properties.
Rows whose target is G or P can be verified only as S in this release; the target
column does not assert an implemented fidelity upgrade.

## Camera mode (kernel v3.0 geometric camera)

Authored specs (`tasks/authored/*.json`, see `house_sim/tasks/spec.py`) may declare `"camera": "room"` (the default:
room-wide visibility, byte-identical to earlier kernels) or `"camera": "geometric"` (the robot's posed cameras: line
of sight, light and resolution; `face`, `peek`, the extended `look`; `camera_log` in the state). The run's camera is,
in order: the CLI's `--camera` (or `verify.run_trial(..., camera=)`, `runner.build(..., camera=)`,
`runner.make_env(..., camera=)`, `HouseEnv(camera=)`), then the spec's `"camera"`, then the default (room until the
D3 switch-over, docs/design/TIER1_INTEGRATION.md 4). The built task carries the spec's `"camera"`; each verification
trial records the mode it ran in (`"camera"`).

Setup ops for geometric specs only (SpecError in a spec without `"camera": "geometric"`; validated always, applied
only when the run's camera is geometric):

| Op | Form | Effect |
|---|---|---|
| `camera` | `{"op": "camera", "face": 90 \| "north" \| "{thing}" \| [c, r], "pan": 0, "tilt": -10, "track": ["{pid}"], "hw": {...}}` | the robot's starting body heading (grid degrees, a compass word, or toward a thing, person, pet or cell from its cell: place the robot first), head pan and tilt, the people and pets the head keeps in frame, hardware overrides (keys of `camera.HW`, same shapes). Sensor switches (`camera_on`, `mic_on`, `privacy_policy`) belong to the robot op |
| `view` | `{"op": "view", "target": "{x}", "props": {...}}` | merges validated view properties into the record's `view`: `porosity`, `below_porosity`, `clear_below_m`, `behind_gap_m`, `slab_m`, `height_m`, `opening` (front / top / none), `interior_light`, `glossy`, `front_deg`, `faces`, `text_m`, `camera` (a device camera), `beam` |
| `track_view` | `{"op": "track_view", "ids": ["{pid}", "{toy}"]}` | `camera_log.sightings` also follows these things |
| `view_watch` | `{"op": "view_watch", "name": "door_cam", "viewer": {...}, "targets": ["{x}", "robot"], "every_s": 60}` | what another viewer sees, every `every_s` (at least 10 s), in `camera_log.watch.<name>` (`visible`, `ever`, `fraction`, `clear`, `ever_clear`, `robot_in_view_s`). Viewers: `{"entity": "{device}"}` (a thing with a `view.camera`), `{"person": "{pid}", "attention": true}`, `{"room": "{r}", "cell": [c, r], "z", "yaw", "tilt", "hfov", "range_m"}`; aperture viewers arrive with camera phase 3 |

`spawn ... "in"` gains `"row": "front" | "back"` (both camera modes; inert in room mode): the item's `inner_spos` puts
it in the front row at the opening, or the back row against the back wall, of its shelf, at the leftmost free place.

Success and failure predicates may read the camera log, e.g. `{"path": "camera_log.coverage.{k}", "op": "gte",
"value": 0.9}`, `{"path": "camera_log.peeked.{washer}:behind", "op": "exists"}`, `camera_log.looked_in.<id>`,
`camera_log.max_gap_s.<pid>`, `camera_log.watch.<name>.ever_clear`.

Lint (`spec.check_structure` / `spec.camera_problems`): `"camera"` is room or geometric; a spec whose setup, plans or
predicates use the camera ops, `face` / `peek` or `camera_log` must declare `"camera": "geometric"`; a `"schema": 3`
geometric spec needs a wrong plan marked `"camera": true` that fails for a camera reason (did not look, wrong
viewpoint, dark, glare, missed the back row).

Verify one file in a forced camera mode with `python -m house_sim --check FILE --camera geometric`.
