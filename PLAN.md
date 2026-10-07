# Household environment implementation plan

Base contract: Python 3.9+, standard library, no GPU, Isaac Lab or API key.
Preserve `py house_env.py`, generated houses, editable JSON, and existing actions.

1. Preserve source bytes; parse every nonblank catalog line, fused headings, duplicates,
   appendices, primitives, variations, and the incomplete adaptability fragment.
2. Build a declarative symbolic world and predicate evaluator with guarded actions,
   partial observations, deterministic seeds, resident permissions, persistent state,
   event logs, reversible actions, and reproducible replays.
3. Ground tasks in reusable families. Each DONE entry must have concrete initial
   state, independently evaluated goals, reference traces across randomized seeds,
   and failing idle, wrong, and adversarial baselines. Unsupported requirements stay
   TODO/STUB with explicit reasons; a proxy is never full coverage of its source.
4. Add tasks for manipulation, cleaning, laundering, appliances, outdoors, inventory,
   ambiguity, safety, privacy, memory, interruptions, and changing conditions.
5. Audit source accounting and evidence, document limitations and extension APIs,
   run test/demo/report commands, package an installable offline project.

Target fidelity in this Python-only phase is S (symbolic). Geometric/physical target
requirements are recorded separately. Do not imply real robotics generalization.
A five-day budget in the supplied brief is a ceiling, not elapsed time already spent.
No background execution after delivery is assumed.
