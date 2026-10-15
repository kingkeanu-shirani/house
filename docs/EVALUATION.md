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

## Time, temporal conditions and phases (kernel v3.1)

`house_sim/kernel/evaluation.py` evaluates a task on demand; since kernel v3.1 the engine also keeps a temporal monitor
(`house_sim/kernel/monitor.py`) that samples the task's milestones and temporal keys where things happen: after each
action's settlement, after every 30 s substep (inside waits too), after each auto standing-order command and at the
end of each step. Consequences for evaluation:

* **Milestones record their true time.** A milestone reached halfway through a one-hour wait is recorded at that
  substep (it used to be the end of the step), so `deadlines` and `order` compare real times; one reached by an
  action is recorded at the action's end (its completion: a 20 s walk does not meet a 5 s deadline). A milestone that
  already holds in the initial state is reached at the first sample (time 0); the verifier warns about it
  (`temporal_warnings`), because a deadline on it is met before anything happens.
* **Conditions over time.** `never` and `always` latch the first violation inside their window; `held_for_s`,
  counted milestones, `responses` (each trigger edge opens an obligation due `within_s` later; `for_each_new`
  tracks each entity created during the episode) and the extended `deadlines` (`{"by": "HH:MM"}`,
  `{"within_s", "after"}`) are evaluated from the samples. Failure labels: `never:<label>`, `always:<label>`,
  `response:<label>` (missed), `response_open:<label>` (still open: not yet a success), `deadline:<name>`.
* **`decided`.** Every evaluation carries `"decided": null | "fail"`: "fail" once success is impossible (a latched
  violation, a missed response or deadline, the step or time budget, a global failure flag the task does not
  allow, a failed phase); the entries of a phase that ended superseded (unscored) no longer count. Schema-3 idle
  checks stop waiting at that point; schema-2 idle checks wait out the budget exactly as before. Idle checks wait in
  at most 30 waits: hour-long ones, longer ones for a budget over 30 hours, so a multi-day budget is waited out.
* **Phases.** A multi-phase task succeeds only when every scored phase succeeded (each phase is scored on the state
  at its end: its success and failure predicates, its questions inside its window, its temporal entries) and nothing
  failed at the episode level; `goal_fraction` is phases passed / phases scored, `evaluation.phases` lists one record
  per phase (`id`, `success`, `failures`, `goal_fraction`, `started_s`, `ended_s`, `steps`, `time_s`, `end_reason`,
  `scored`). A scored phase that has not started fails as `phase_not_started:<id>`; an `"optional": true` phase
  (start when / at_s) that never started is unscored. A phase's milestones are its own (sampled inside it; its
  deadlines name them first). The verifier's initial check also requires that phase 1 is not already satisfied; its
  idle check waits out each phase's budget and declares the phase done.
* **Lints.** A `never` predicate without an `after` window that holds in the initial state fails the trial
  ("temporal lint"). Schema-2 reference plans that poll hidden state with `wait_until` are counted per trial
  (`privileged_waits`; `python tools/verify.py --privileged-waits` lists them by domain as migration debt). In
  schema-3 specs `wait_until` is an agent wait the environment refuses when the condition is not perceivable. A
  schema-3 reference run must also pass the dialogue lints (`dialogue_lint`: facts well formed and reachable,
  required questions asked and answered, a wrong plan beside them, no legacy-topic ask; docs/DIALOGUE.md).
* **Memory and reports.** Predicates can read the robot's memory (`mind.*`, read-only) and typed reports (`report`,
  `report_set`; their `after` / `before_s` window bounds typed and legacy reports alike); see docs/TASK_SPEC.md and
  docs/DIALOGUE.md. With realistic perception, the typed reports the success predicates score must rest on evidence
  the reference run perceived (a gap fails the reference, listed in `perceivability_gaps`). "The requester" of
  `ask_whom` is the current request's `from` (in a multi-phase episode the phase's), the rule the dialogue uses for
  `known_by`.

The monitor's record (milestones with counts and times, violations, obligations, phase times) is saved with the
episode (`env.save`) and rebuilt identically by a replay; footprint skipping between samples (only predicates whose
sections, entity ids or types changed are evaluated again) never changes a result (tests/test_monitor.py compares it
with sampling everything).
