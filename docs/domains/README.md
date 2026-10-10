# Domain reference pages

One page per mechanics domain (see docs/DOMAINS.md). Each page documents, for task-spec authors:

1. Object types and the fields that matter (with realistic magnitudes).
2. Actions: parameters, preconditions (refusal messages and categories), effects, durations, noise, undo.
3. Systems: what changes over simulated time.
4. Observation channels and inspect fields the robot can use.
5. Global harms the domain can cause (stats flags) and how.
6. Reference skills and scenario helpers.
7. Spec recipes: entry clusters -> setup ops, success predicates, plausible wrong plans.
