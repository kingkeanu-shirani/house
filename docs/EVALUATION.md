# Evaluation and integrity

The audit counts catalog entries, not template families. A candidate is DONE
only when every recorded seed passes all of these checks:

1. The initial state does not already satisfy the task.
2. Waiting without useful work still fails.
3. The external reference policy completes all success predicates within budget,
   without violating failures or protected properties.
4. A task-family-specific wrong policy fails: wrong item, destination, report,
   cleaner, amount, timing or appliance procedure.

Seeds 0, 1, 2, 41 and 97 vary topology, floors, item placement, initial quantities,
conditions and event timing. This is finite distribution testing, not a proof
of general household competence. Families share parameterized logic; duplicate
catalog entries are not independent skills learned by an agent.

`reports/verification.jsonl` records each trial and hashes its initial world,
task and final states. Evidence is tied to source and specification hashes.
`reports/verification_run.json` records elapsed audit time, seeds and failures.
`coverage.csv` records the verified result. Stale or failed evidence leaves a
candidate STUB. TODO means no fully grounded scenario, not verified coverage.

Regression tests exercise private/hidden objects, copied observations, invalid
arguments, graph/containment cycles, parent mass, liquid conservation, capacity,
unsafe cleaners, authority, appliance interlocks, timed events, stopping,
guarded undo, state persistence, replay, ambiguous language and reward farming.
The evaluator rejects collateral movement of a protected item even if the goal
itself is satisfied. Cleaning checks condition, not merely moving an item away.

Metrics include success, goal fraction, elapsed steps/time, invalid actions,
safety/privacy blocks, noise, energy, clarification counts, handoffs and undo.
Damage/injury/fire/flood flags can defeat success but are not produced by a
physical consequence model. Quietness is a symbolic tool noise measure. There
is no calibrated subjective or LLM judge. Most subjective tasks remain TODO.

Privileged reference policies may inspect the world graph to route between
objects. Their success tests the environment, not agent intelligence. The
separate search policy accepts only observations. Python objects are not a
security boundary: a hostile in-process agent can access private attributes.
Evaluate untrusted agents through an isolated headless process, with the trusted
controller owning world/task files. Such a process sandbox is not included.
