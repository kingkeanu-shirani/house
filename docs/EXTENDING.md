# Extending the prototype

For a new household, export JSON, edit rooms/entities and load it with `--house`.
Object properties and existing affordances are data-only additions. New fixed
world/task bundles can be added in JSON and loaded with `--scenario`. No engine
edit is needed to compose existing predicates and actions into a new task.

For a randomized task within an existing family, add a spec with the same family
and different parameters, then call `scenarios.build`. `tasks/id_to_family.jsonl`
documents catalog bindings. The compiler regenerates generated catalog files,
so keep custom task files separate from its output.

For a new transformation, add a definition to a custom copy of `actions.json`
and pass `action_file=...` to `HouseEnv`. Definitions declare an affordance,
effects, preconditions, tool, duration/noise and optional water/detergent/power
requirements. Structural/security fields cannot be changed by a declarative
transformation. Do not add effects that simply declare a task complete.

New mechanics, appliance types beyond the three cycle machines, new predicate
operators and genuinely new scenario families still require Python changes.
The brief's promise that every new appliance/task/command needs only data is
not fully achieved. No unsafe arbitrary predicate-plugin loader is provided.

A contribution is ready only when it has a concrete initial-state generator,
independent goals and side-effect predicates, a reference policy, failing idle
and plausible-wrong policies, source-specific regression cases and honest
fidelity labels. An easier proxy must not be silently substituted for a complex
source command. Use TODO/STUB until the source meaning is actually represented.

For later Isaac Lab integration, keep this decision/action schema and evaluator
behind an adapter. Replace symbolic movement/manipulation with measured simulator
outcomes and sensors. Do not treat successful symbolic folding or pouring as
physical validation. The adapter and physical assets are not included here.
