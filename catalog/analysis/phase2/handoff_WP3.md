# Handoff WP3 TIMING (VERIFY_HARDENING section 25; sections 8.5, 6.9, 8.1)

Files: `house_sim/kernel/monitor.py` (changed), `tests/test_timing.py` (new, 44 tests). Nothing else was edited.

## What landed

1. **The `timing` key** (8.5) for a task, a variant (through the merged task) and a phase. Each item is sampled at
   every monitor sample point (action / substep / order / end; 30 s substeps inside waits): its trigger and act
   (a predicate or a milestone name) are evaluated, and the monitor records the maximal intervals `[a, b]` during which
   each held (`b` None while it still holds), stamped like milestones (an action's sample is stamped at the action's
   end). `t_T` is the start of the trigger's first interval.
   * A top-level item's window starts at the episode start; what held then is read from the reset baseline
     (`env._baseline`), so `initially` works from t=0 although the first sample comes after the first action.
   * A phase's item (labels `<phase id>/<label>`, milestone names resolved to the phase's own milestones first) is
     evaluated at every sample but recorded only while its phase runs; what held at the last sample before the phase
     started opens its first intervals; its verdict is the phase's (`score_phase` -> the phase record's failures).
   * Labels `timing:<label>:early|late|unheld|unneeded|no_trigger` come out of `Monitor.failures(state[, phase])`, so
     `evaluation.evaluate` and `score_phase` (which already call it) list them with no change on their side;
     `Monitor.decided` returns `"fail"` once a timing outcome cannot change (early at once; late after `hi`; unheld when
     the answer ends or ends short; unneeded once a later trigger would make the act early). Battery early stops (10.5)
     read `result["decided"]` as before.
2. **Open intervals of restore-class items** (6.9), for a task with a `hardening` block (schema 4; off with
   `"hardening": {"track_open": false}`): for every item of the restore class
   (`house_sim/data/verify/restore.json`, WP1's data; matched with the ledger's own rule, and open as the ledger's
   `is_open`: an `open_fields` field true or `open_frac` above 0) the monitor records
   `{"start", "end", "by"}` per open interval, `by` = `"robot"` (found at an action or standing-order sample: what
   changed since the previous sample was written in the robot's own window), `"other"` (a substep or end-of-step
   sample: people, pets, systems, events) or `"setup"` (open at the reset baseline); closed by anyone. For every item
   matching a `zero_when_protected` class (child, pool, pet gates) each robot-opened interval also carries
   `"unattended_s"` (seconds, summed between samples, during which a protected agent was in the gate's connected zone
   and the robot was farther than `near_m` from it) and `"unattended_at"`. WP1's ledger already reads this
   (`Ledger.held_open`, `house.held_open:<id>`): verified end to end (toddler + propped gate + robot away -> fires;
   robot near, or adults only -> does not).
3. **Field-level footprints** for the settle window (8.1) and the battery's "about X" (10.4): `Footprint.fields` and
   `Footprint.meets(eid, field, record=None)` (conservative: never False for a write that can change the value).
   The sampling footprint (sections, ids, any_entity, types, always) is unchanged, except that WP4's `said` form
   (it reads the speech log, which no entity write touches) is now an always-sampled form like `report` (WP4's
   request in handoff_WP4.md; no legacy spec uses `said`).
4. **The derived-field overlay in temporal sampling** (8.2): for a hardened task the monitor's predicates (milestones,
   never / always, responses, timing) read `evaluation.derived_view(state, task)` (WP1), and a predicate that reads a
   registered derived field is re-evaluated at every sample (its inputs lie outside its own footprint). Legacy tasks
   read the plain state as before.
5. **A pure verdict** `timing_outcome(...)` so the battery's fixed-timer exposure (9.7, B7, P7) is arithmetic over
   drawn event times (pinned by `TimingOutcomeTests.test_fixed_timer_exposure_is_arithmetic`).

## Interfaces (all in `house_sim/kernel/monitor.py`)

```python
validate_timing(items, names=(), strict=True) -> [problem strings]      # [] when valid
#   names: the milestone names a trigger / act may name; strict (the authoring lint, spec.check_structure): also the
#   sample-resolution rules (window within_s - not_before_s >= 120 s, not_before_s a multiple of 30 s); HouseEnv
#   (validate_task) runs strict=False. validate_task(task, phases) now also raises ValueError for a malformed item.
normalize_timing(raw, prefix="", ref=None, phase=None) -> item           # defaults applied
timing_outcome(item, trigger, act, t0=0.0, now=None, init=False)
    -> {"labels": [...], "decided": bool, "t_trigger": t_T or None, "response": [start, end] or None}
Monitor.intervals(label, which="act") -> [[start, end_or_None], ...]     # which="trigger"; "open:<eid>" -> robot open
                                                                         # intervals; KeyError for an unknown label
Monitor.timing_verdicts(state, phase=None) -> {label: timing_outcome(...)}
Monitor.open_intervals(eid=None, robot_only=True) -> {eid: [interval]} or [interval]
Monitor.open_items() -> {eid: restore class name}
Monitor.longest_open(eid, now=None, robot_only=True) -> seconds
Monitor.derived(state) -> the state temporal predicates read (derived overlay for hardened tasks)
Monitor.track_open, Monitor.hardened                                     # bools from the task's hardening block
restore_rules(reload=False), restore_classes(record, rules=None), restore_class(record, rules=None),
restore_ids(state, rules=None), is_open(record, rules=None), protected_agent(kind, record, rules=None),
unattended_exposure(state, eid, rules=None)
Footprint.fields, Footprint.meets(eid, field, record=None)
# constants: TIMING_KEYS, TIMING_OUTCOMES, IF_NO_TRIGGER, INITIALLY, RESOLUTION_S (30), MIN_WINDOW_S (120)
```

Records: `Monitor.record()` gains `"timing"` (label -> `{"t0", "trigger", "act", "init"}`) only for a task with timing
items and `"open"` (eid -> `{"class", "intervals"}`) only for a task that tracks open intervals; `Monitor.result()`
gains `"timing"` (per label: `t_trigger`, `response`, `labels`, `decided`, `trigger`, `act`) and `"open"` under the
same conditions. A legacy task's record and result are unchanged (pinned by
`TimingEpisodeTests.test_a_legacy_task_is_unchanged`). Format stays `house-monitor-v1` (added keys are optional);
save / load and replay carry everything (pinned).

## Item semantics and defaults

`{"label", "trigger", "act", "not_before_s": 0, "within_s" (required), "hold": "end", "if_no_trigger": "forbid",
"initially": "false"}`; `lo = t_T + not_before_s`, `hi = t_T + within_s`.

| Label | Reported (as if the run ended now) | Decided |
|---|---|---|
| early | a non-exempt interval starts before lo | at once |
| late | t_T known and no interval answers (starts in [lo, hi], or the exempt initial one still holds at lo) | after hi |
| unheld | the answering interval ended (hold "end") or ended after less than hold seconds (an answer still open at the end counts as held; hold 0: no requirement) | when it ends |
| unneeded | no trigger, if_no_trigger "forbid", a non-exempt interval exists | when a later trigger would make it early (now > a - not_before_s); never for "allow" |
| no_trigger | no trigger, if_no_trigger "require" | not before the end (with an act interval: decided like unneeded) |

`initially: "any"` exempts the interval that held at the window start; `"false"` (default) counts it (the lint's
dynamic check that act is false at t=0 on every seed is WP8's).

## Deviations from the spec (and why)

* **Window rule read as a width.** 8.5's "within_s >= 120" would reject its own anticipation example
  (`{"not_before_s": -900, "within_s": 0}`); `validate_timing` requires `within_s - not_before_s >= 120` (and
  `within_s >= not_before_s`), plus `not_before_s` a multiple of 30. Both are lint rules (strict); the engine runs any
  well-formed item.
* **Defaults the spec leaves open**: `hold` "end" (0 = none), `if_no_trigger` "forbid", and a third value
  `"allow"` (anything goes without a trigger) that the spec's table does not name; `label` and `within_s` required.
  Strict by default (P2): an author must opt out explicitly.
* **Labels are the table's, literally**: an early fixed-timer close that still holds through the window is reported
  `early` and `late` (nothing started in [lo, hi]). Battery accept sets (`timing:*`) and wrong-plan `expect` lists are
  unaffected; `late` alone never appears with `early` for an agent that answered.
* **Milestone names read the milestone's live truth** (its per-sample achieved value, held_for_s included), not its
  latched first-reach time, so "the maximal intervals during which it held" is meaningful for a named act.
* **Open-interval attribution by sample point** instead of WP2's writer log: the engine samples right after the
  robot's window (action, standing order), so "opened by the robot" is exact there, and it does not depend on when
  WP2's per-step diff lands in `env.writes` (a mid-step read could see a stale writer). `by` is "robot", "other" or
  "setup"; the ledger's `writer_of_by` maps "other" to physical (not charged).
* **Exposure** (`unattended_s`, the name WP1's ledger reads) is summed between samples at their stamps, for every item
  that matches any `zero_when_protected` class (the ledger names the first class; computing it for every match keeps
  the ledger's 0 s rule fed whichever class it picks). The connected zone is the rooms in the item's `connects` (else
  its room); "within near_m" is the Euclidean distance from the robot's cell to the gate's cells in the room it reaches
  the gate from (`geometry.anchor`), in metres of `geometry.CELL`.

## Known gaps

* **A trigger that never comes inside the run.** The relation sees only what happened: on a seed whose event is
  scheduled after the agent declares done, a `timing` item with `if_no_trigger` "forbid" or "allow" is vacuous (no
  t_T, no act interval: no label). `finish()`'s settle and a look-ahead sample it further (WP1 reports what first fails
  there as `later:`), but a short settle does not reach a late event. Requested from WP5 / WP8 / WP6 below.
* `result["temporal"]["timing"]` reaches `evaluate()`'s output only once `evaluation._has_temporal` counts the
  `timing` key (WP1's file; requested). `Monitor.result()` already has it.
* The `timing` key reaches built tasks only once `spec.py` adds it to `TASK_KEYS` / `LIST_KEYS` (`+timing`) /
  `PHASE_KEYS` and `temporal_problems` triggers on it (WP8), which `runner.build` then passes through (WP5).
* WP1's `restore.json` class `exterior_door` selects by `fields: {"exterior": true}`, which also matches windows
  (core `window` has `exterior: true`) and garden gates; the monitor follows the data (it records, the ledger judges),
  so windows are tracked and the ledger can fire `house.held_open` on a window aired for more than 300 s. Requested
  from WP1.

## Requests to other packages

* **WP1** (`evaluation.py`): count `"timing"` in `_has_temporal` so `evaluate()["temporal"]` carries the monitor's
  `timing` block for a task whose only temporal key is `timing`.
* **WP1** (`restore.json`): the `exterior_door` class's `fields: {"exterior": true}` also selects windows (core
  `window` has `exterior: true`) and garden gates (listed again under `garden_gate`); restrict it (for example
  `"is_a": ["door"]` with the field, or `exclude_fields` for windows) unless windows are meant to be restore class.
* **WP8** (`spec.py`, `lint.py`): add `timing` to `TASK_KEYS`, `LIST_KEYS` (`+timing` appends), `PHASE_KEYS` and
  `temporal_problems`' trigger list; `check_structure` calls `monitor.validate_timing(items, names, strict=True)` per
  scope (names: every milestone name of the spec and its phases); the dynamic `initially: "false"` check (act false at
  t=0 on every seed of V) belongs to `lint.run(..., dynamic=...)`; document the defaults above in TASK_SPEC.md.
* **WP5** (`runner.py`, `verify.py`): `runner.build` passes `timing` through (`TASK_KEYS`, bindings substituted) for
  the task and each phase; close the vacuous-trigger gap: on a seed whose scheduled event (setup `time` op, 9.2)
  falls after the run's end, either run the reference / agent episode through the event (the declared-done goal plus
  settle does not reach it) or have L-VACUOUS / the lint require `if_no_trigger: "require"` or a success predicate
  that needs the event.
* **WP6** (battery): B7 / P7 exposure is `monitor.timing_outcome(item, [[t_T, None]] or [], [[tau, None]], 0.0,
  budget)["labels"] == []` per drawn onset; consider a B7 variant that declares done before the seed's event (the
  vacuous-trigger hole above).
* **WP2** (optional, `engine.py`): `finish()`'s `_meets` works per record; `Footprint.meets(eid, field, record)` gives
  field-level precision with the fields `env.writes` records.

## Phase-1 carry-over

No open phase-1 row lies in WP3's files: the 10 rows of `closeout_breakages.json` are domain-side, the findings of
`closeout_REV.json` (all confirmed in `closeout_VER2.json`) are the gate tool's, py39_lint's, cleaning's, safety's,
people's, mechanisms' and navigation_robot's, and `closeout_INT3.json`'s requests go to the main session, the domains
and WP9. Nothing to close here.

## Cost (section 22)

`tools/sweep_bench.py`: monitor_idle 0.0031 ms, monitor_quant 0.036 ms (budgets 0.05 / 0.5). Micro-bench on the seed-0
generated house, per sample: a legacy task 0.0003 ms (unchanged path), two timing items over a quantifier 0.0033 ms
untouched, open tracking +0.002 ms idle and +0.004 ms when restore items are touched, hardened + a timing item with a
touched quantifier 0.03 ms. Legacy tasks pay one empty loop and one boolean test per sample.

## How to test

```
python -m unittest tests.test_timing                        # 44 tests; also HOUSE_SIM_STRICT=1
python -m unittest tests.test_monitor                       # the existing temporal tests, unchanged
python tools/py39_lint.py house_sim/kernel/monitor.py tests/test_timing.py
python tools/sweep_bench.py
```

Results on the shared tree (other P2-S1 packages' edits present), 2026-10-29: `green_tests.py 3` 4,956 tests, 0
failures (every file normal and strict); all 243 authored specs in their own mode (combined file, `--check --workers
3`) 243 passed, 0 failed; after the last change (`said` footprints, `is_open` aligned with the ledger) test_timing,
test_monitor, test_speech, test_reports, test_report_truth, test_phases, test_finish and test_ledger re-run green in
both modes.

Legacy invariance was also checked differentially: 28 authored specs (the 8 with temporal keys plus 20 sampled across
domains) run through `verify.run_trial` with HEAD's `monitor.py` and with this one gave identical trial records and
identical final evaluations and monitor records for the reference, idle and wrong plans (scratch script
`p2_WP3/legacy_diff.py`).
