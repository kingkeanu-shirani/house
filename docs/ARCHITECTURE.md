# Architecture

The world is a connected room graph plus a containment tree. Entity locations
are room IDs, other entity IDs, or the single robot hand. Carrying a container
carries its descendants. Closed containers hide their contents. Observations
are deep copies of the current room and visible objects; the known map contains
only visited rooms and their observed exits.

The action engine reads world state and guarded action definitions. It never
calls the reference policy or reads task goals to select effects. Evaluation
separately tests success predicates, failure predicates, protected properties
and budgets. There is no “complete this task” action or arbitrary property-setter
in the agent interface.

`scenarios.build(spec, seed)` produces a world, a concrete task, and an oracle
context. Give only observations and action results to an evaluated agent. The
oracle context and `privileged_state()` are for tests/debugging, not agents.

The three available interfaces are primitive actions, external scripted skill
composition, and a small natural-language grammar. Compound reference skills
are ordinary callers of `step`; they do not bypass preconditions. Grammar
resolution supports visible labels/IDs and an optional pointing ID. It does not
parse the full catalog or provide open-ended language understanding.

People are symbolic residents with roles, consent, scopes, location and
preferences. Hidden answers are supplied by a scripted user simulator through
`ask`; they are not included in observations. No external person is contacted.
Events are trusted scenario-side changes triggered by simulated steps or time.
Seeded generators choose layouts, objects, initial conditions and event timing.

State files preserve household state, event progress, memories, orders, episode
baseline and history. Reset normally restores the initial household. A Python
caller can use `reset(preserve_world=True)` to continue the same household with
new episode accounting; the current task may already be complete, so this is
not a fresh training sample. There is no general task scheduler or skill learner.

Dimensions, materials and many resident/robot attributes are metadata. Doors,
locks, capacity, carried mass, utilities and certain hazards have symbolic
effects. The project provides no continuous geometry, trajectories, sensors,
contact, thermal dynamics, cloth or fluid physics. Only symbolic/language
observation modalities are accepted; unsupported sensors raise an error.
