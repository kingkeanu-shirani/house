# Handoff TIME-EVAL (stage C): time, temporal evaluation, phases and requests

Read before starting: `docs/design/TIER1_SWEEP.md` (whole; package sections 3, 6, 8, 8.4, 9, 10.1, 11.1, 15, 16, 18,
19, 20, 21 items 1-7, 22), `docs/design/TIER1_INTEGRATION.md` (1 with 1.3, 1.8, 1.9 and its rules, 1.11-1.13, 1.17;
2.5; 3; 5), `docs/design/TIER1_DECISIONS.md`. Handoff files read (all that existed): `handoff_TIER1-GATE.md`,
`handoff_B0.md`, `handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_CAM-SIGHT.md`,
`handoff_BODY-MODEL.md`, `handoff_GEN.md`, `handoff_PHYS-INT.md`, `handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`,
`handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`, `handoff_NAV-M2.md`, `handoff_BODY-CORE.md`,
`handoff_BODY-FAULTS.md` (with their merge and STAGE-B-FIX sections), `handoff_B6.md`. Their items addressed to
TIME-EVAL are acted on: CAM-ACTIONS (`schema` in `spec.ALLOWED` arms the camera lint's schema-3 rule), GEN (`house.gen`
and `--gen`), CAM-RUNTIME (visual notices from the camera's event rule; the `camera.watch` probe keeps sight's
touch-log consumer current), PHYS-FLUIDS (exact probes for `physics.processes` / `physics.environment`), PHYS-INT
(the monitor samples after the settle pass), BODY-ENGINE (`"covered"` / `"idle"` advance modes; auto commands are
`perform` leaves), BODY-FAULTS (audio notices follow `body.can_hear`, visual ones need `body.imaging`), CAM-RENDER
(the harness keeps its camera plumbing), B6 (the comparison baseline is the B6 gate = `tests/data/spec_baseline.json`).

## 1. Summary

* Interruptible `wait(until, interrupt_on)`, auto standing orders (`watch(mode="auto", guard, max_runs, cooldown_s,
  expires_s)`) run under World savepoints during idle advances, the notice bus (`World.notice`, P-approx sound /
  visual / remote perceivability, kernel-derived notices), `timeline.perceivable` with its three fixes.
* The temporal monitor (`kernel/monitor.py`): milestones sampled where they happen (inside waits), never / always /
  held_for / counts / responses / extended deadlines, footprint skipping, `decided`; phases (`kernel/phases.py`):
  gaps, dock parking, per-phase scoring, supersedes; requests in the state; the calendar clock (holidays, DST,
  latitude, parse_when, recurrences, date formats); new predicate forms.
* Spec / runner / verifier / CLI / harness: schema 3 and its defaults, `phase_done` / `wait_for` plan steps, the
  phase loop of the harness with `on_request`, `--gen`, `tools/verify.py --privileged-waits`, `bench_systems.py
  --day`, `tools/sweep_bench.py`; 112 new tests in six files; four docs.
* Gate `TIME-EVAL` vs B6 (isolated copy: HEAD plus this package's files): 0 state-digest and 0 observation-digest
  changes in 2,420 trials; one verdict change (reviewed S-R1, a spec fix requested); compare PASS; the one failing
  check is G1's `test_eval_splits.py`, which fails the same way at HEAD (request 2); see section 6.

## 2. Files

Created: `house_sim/data/calendar/holidays.json`, `house_sim/data/calendar/dst.json`, `tools/sweep_bench.py`,
`tests/test_timeline.py`, `tests/test_world_savepoint.py`, `tests/test_monitor.py`, `tests/test_phases.py`,
`tests/test_clock.py`, `tests/test_predicates_v3.py`, this handoff, the gate record
`catalog/analysis/tier1/gate/TIME-EVAL/`.

Edited: `house_sim/kernel/monitor.py`, `phases.py` (A0 stubs, now implemented), `timeline.py`, `engine.py`,
`world.py`, `evaluation.py`, `predicates.py`, `clock.py`, `plugins.py`, `physics.py`, `camera.py`,
`phys/observe.py`, `house_sim/tasks/spec.py`, `runner.py`, `verify.py`, `house_sim/eval/run.py`, `agents.py`,
`house_sim/cli.py`, `tools/verify.py`, `tools/bench_systems.py`, `docs/TASK_SPEC.md` (rewritten for schema 3),
`docs/EVALUATION.md`, `docs/KERNEL_API.md` (v3.1 section), `docs/EVAL_CONTRACTS.md`.

All on the TIME-EVAL list of TIER1_INTEGRATION 2.5 (`house_sim/kernel/actions.py` was not needed). All LF.

## 3. APIs

Frozen 16.1 names landed with their signatures; additions are marked ADDITION, differences **DEVIATION** (section 4).

**`kernel/world.py`**

```
w.notice(kind, room=None, db=None, source=None, to_robot=False, remote=False, visual=False, text=None, data=None)
        -> stored record | None
sp = w.savepoint(); w.rollback_to(sp); w.release(sp)                 tokens are ints (layer depth); rollback_to ends sp
w._touched_sections (set), w._quiet_touched (set, ADDITION), w._heard / w._listening (ADDITION, engine-internal)
LIST_SECTIONS += notices, order_log, report_log, dialogue_log, requests;  NOTICES_CAP = 200
EMIT_NOTICES (ADDITION): event-log kind -> fn(w, entry) called by w.emit (battery / fault notices)
```

**`kernel/timeline.py`**

```
perceivable(w, rule, realism=None) -> bool               (watch_condition_visible stays exported, unchanged)
register_notice_kind(kind, doc, implies=(), db=None, visual=False)    ADDITION: db, visual
NOTICE_KINDS (dict), GROUPS (ADDITION: {"urgent": (...)})
robot_perceives(w, notice) -> dict | None
run_auto_orders(env, idle_ctx) -> [order_log records]
ADDITION: post, store, bus_on, active, start, derive_notices, note_order_fired, describe_notice, expand_kinds,
          kinds_of, matches, sound_level, fire_orders, has_due, has_auto, IdleRun, WaitRun, wait_message
actions: wait(seconds, until=?dict, interrupt_on=?list); watch(condition, commands, persistent=False,
         mode="queue"|"auto", guard=?dict, max_runs=?int, cooldown_s=?number, expires_s=?number)
```

**`kernel/monitor.py`**: `Monitor(task)` with `sample(env, point)`, `record()`, `load(dict)`, `decided(state)`;
ADDITION: `failures(state, phase=None)`, `result(state)`, `deadline_limits(state)`, `missed_deadlines(state,
phase=None)`, `note_phase(id, start=None, end=None)`, `overlay()`, `add(entries)`, `active`; module `footprint`,
`compile_task`, `validate_task(task, phases)`, `deadline_limit`, `narrowed`.

**`kernel/phases.py`**: `PhaseController(task)` with `active()`, `end(env, reason)`, `start_next(env)`, `record()`;
ADDITION: `bind(env)`, `start_first(env)`, `tick(env, end_of_step=False)`, `gap_left(env)`, `waiting(env)`,
`history(state=None)`, `evaluate(view, state, baseline, monitor)`; module `post_request(env, request, phase_id)`,
`relocate_to_dock(env, phase_id)`, `default_request_heard(state, request)`, `GAP_TICK_BASE`.

**`kernel/clock.py`**: `holiday(state, date=None)`, `holidays_between(state, d0, d1)`, `wall(state)`,
`dst_active(state)`, `today(state)`, `tomorrow(state)`, `weekday(state, date=None)`, `is_weekend(state, date=None)`,
`parse_when(state, text)`, `occurrences(event, d0, d1)`, `format_date(state, date, style=None)`; ADDITION:
`region(state)`, `model(state)`, `easter(year)`, `declination_deg(doy)`, `day_length_h(lat, doy)`.

**`kernel/evaluation.py`**: `evaluate(task, state, baseline, milestones, steps, world=None, temporal=None,
phases=None, mind=None)` (`temporal` is the Monitor, `phases` the PhaseController, `mind` the Mind); ADDITION:
`evaluation_view(state, temporal=None, mind=None)`, `question_failures(questions, state, stats_before=None,
window=None)`, `score_phase(...)`, `decided(task, state, milestones, monitor=None, phases=None)`. The result gains
`decided` (always) and `temporal` / `phases` when the task uses them.

**`kernel/predicates.py`**: forms `report`, `report_set`, `milestone_count`, `agg`; quantifier `"in"`; `mind.` paths
through the overlay; ADDITION: `is_true` / `is_false` without `value`; `collection`, `select_in`, `anchor_s`,
`TEMPORAL_KEY` ("$temporal"), `MIND_KEY` ("mind").

**`kernel/plugins.py`**: `system(name, order=100, max_dt=60.0, period=None, idle=None)`; `SYSTEM_IDLE`, `IDLE_PROBES`
(env `HOUSE_SIM_IDLE_PROBES=0` turns probes off).

**`kernel/engine.py`**: `HouseEnv.phase_done()`, `HouseEnv.request()` (ADDITION), `env.mind`, `env._monitor`,
`env._phases`; `perform` unchanged (`_leaf` returns the leaf's Ctx and seconds).

**`kernel/physics.py`**: `environment_idle(w)`, `processes_idle(w)` (registered as the systems' probes).
**`kernel/camera.py`**: `sees_event(w, source=None, room=None, at=None, z=None, size_m=0.3)` (pure),
`watch_idle(w)`; `witness` posts a `visual` notice for a seen occurrence. **`kernel/phys/observe.py`**: `NOTICE_DB`;
`emit_consequence` posts the consequence's sound notice.

**Tasks**: `spec.ALLOWED` + `schema`; `TASK_KEYS` + `never`, `always`, `responses`, `phases`, `request`; `spec.SCHEMAS`,
`schema_of`, `phase_problems`, `request_problems`, `temporal_problems`; `runner.build(spec, seed, registry=None,
camera=None, perception=None, gen=None)`, `runner.default_requester(config)`, `Runner.privileged_waits`, plan steps
`phase_done`, `wait_for`, schema-3 `wait_until`; `verify.run_trial(spec, seed, perception=None, camera=None,
gen=None)`, `verify_spec` / `verify_many(..., gen=None)`, `verify.idle` (phase-aware, schema-3 early exit),
`verify.temporal_lint(env, task)`.

**Harness**: `Agent.on_request(request)` (optional); `run.RECORD_KEYS` + `phases`; log steps `{"phase_done": true,
"phase", "next_phase"}`; `replay_log` replays them; `ScriptedOracle` emits `done` for its shadow run's phase ends;
`agents.EVALUATOR_KEYS` + `decided`.

**CLI / tools**: `python -m house_sim --gen 1|2` (with `--check`, `--task`, `--demo`, a generated house; `--verify
--gen` needs `audit.verify(gen=)`, request 3), interactive `phase_done`; `tools/verify.py --privileged-waits
[--json]`; `tools/bench_systems.py --day [seeds] [--no-probes]`; `tools/sweep_bench.py [--seed N] [--repeats N]
[--json]`.

## 4. Deviations from the spec

1. **The notice bus is data-gated by the state's `notices` section** (present for `"schema": 3` tasks, added by
   `runner.build` to the config, or any config that carries it). Schema-2 episodes store no notices, so their states
   and observations stay byte-identical (the TIME-EVAL gate row allows no state or observation change; S-R4 "new
   state sections only when the new features are used"). A wait that listens (`interrupt_on`, or `until`) still
   perceives notices transiently in any episode, so the agent-facing feature works for every spec; only the stored
   log (and so PEOPLE-IO's `obs.notices`) needs the bus.
2. **Battery and fault notices come from the event log** (`battery_low`, `battery_reserve`, `fault_detected` through
   `World.emit` / `world.EMIT_NOTICES`), not from the `power_down` / `fault_onset` hooks of 6.1: a powered-down
   robot perceives nothing, and a latent fault is not known to the robot until it is detected.
3. **Stored notice shape**: 6.1's `{"t", "kind", "room" | "near" | "far", "source", "text", "data"}` plus `"step"`
   (so `obs.notices` can list those since the agent's previous step) and `"remote": true` for remote notices; `far`
   is `true`. A notice of the robot's own systems (`order`, `battery`, `fault`) carries the robot's room.
4. **Speech notices follow the domain's `heard_by_robot`** (and `body.can_hear`): the propagation rule only decides
   the room information and whether the words carry, so a notice never contradicts `obs.messages`.
5. **Sample times**: the "action" sample uses the state's time, which is the step's start (this simulator applies an
   action's effects at its start and then spends its duration with the systems running). A milestone reached by an
   action is therefore recorded at the step start; one reached inside a wait at the substep boundary where it first
   holds. The monitor's first sample is the first step's (there is no sample at reset, as before).
6. **Held auto orders** run at the start of the next idle advance (a wait or a phase gap), not through `run_pending`,
   which keeps serving queue-mode orders only. A refused auto command costs no time (it is rolled back); a command
   still running when the wait's time is up extends the step by the rest of its time.
7. **Limits of queue-mode orders**: an order record keeps today's six keys unless the new parameters are used;
   `max_runs`, `cooldown_s`, `expires_s` apply to queue orders only when given, the defaults (max_runs 1, persistent
   50; cooldown 60 s for persistent) only to auto orders.
8. **Phase windows**: `after: "phase:<id>"` opens at the phase start, `until: "phase:<id>"` closes at its end;
   `"phase:<id>:start"` / `":end"` are explicit. Phase-level `deadlines` numbers count from the phase start.
9. **Open obligations**: a response obligation still open when the task is evaluated is `response_open:<label>`
   (no success, but not `decided`).
10. **The idle early exit is for schema-3 specs only**: a schema-2 idle check waits out its budget as before (its
    `idle_state_hash` is a compared digest).
11. **Phase relocation** writes `robot.room`, `robot.pos`, `robot.docked` (and a standing posture), as 1.1 allows;
    navigation's own spot bookkeeping (`paths.update_robot_spot`) is not called from the kernel (follow-up: a
    `phase_start` hook). The robot stays where it is (logged with the reason) when it holds something or has
    fallen.
12. **`phase_done` is not an action**: it counts no step, is recorded in the history as `{"command": {"action":
    "phase_done"}, "phase_done": true, ...}` and replayed; the result also says `waiting` when no phase follows at once
    but one that starts on its own (at_s / when) is still to come (the harness then continues the episode).
13. **The instruction follows the active phase**: `env._task["instruction"]` is set to the phase's request text, so
    today's `obs.instruction` shows it before PEOPLE-IO renders it from `state.requests`; `HouseEnv.request()` gives
    the active request, or the `r0` default (`{"from": null, "channel": "unspecified"}`) for tasks without requests
    (nothing is written to the state for them).
14. **`is_true` / `is_false` may omit `value`** (additive): the sweep's own example `{"path": "$exists", "op":
    "is_false"}` has none.
15. **Report forms**: `predicates.py` resolves `after` (a milestone, a phase or seconds) and `before_s` (seconds after
    `after`, absolute without it) itself, by bounding `report_log` before calling `reports.match` / `match_set`, so
    those receive expectations without time keys (PEOPLE-IO's `match(state, expectation, times=None)` also accepts
    them; nothing is applied twice).
16. **`questions` 4.6 forms** read `dialogue_log` records with these fields: `matched` (fact id), `to`,
    `comprehended`, `otherwise` (true when the person answered with the fact's otherwise text), `act`, `aspect`, `t`
    (request 5).
17. **Perceivable details**: nothing is perceivable while the robot is powered down; the weather through a window
    needs imaging; under "addressed" hearing, kinds that imply `alarm` are heard too.
18. **Physics sound notices** use 45 / 60 / 80 dB for faint / noticeable / loud with the 6.2 propagation, which can
    differ from `observe.heard_sounds`' room-hop rule (a noticeable crash two open hops away is a notice but not a
    `sounds` entry).
19. **Footprints**: type selectors are narrowed to `World.of_type` candidates (types are static); category selectors
    still scan (a category can change). Quiet entity writes (kernel thermal) count as changes for entity paths.
20. **Clock data choices**: `ca` is Ontario's public holidays, `eu` Germany's nine nationwide holidays (no substitute
    days), observance rules per region (US nearest weekday, UK / CA next free weekday, JP substitute and citizens'
    holiday). Clock model 2's sun times are centred on 12:00 solar time with Cooper's declination, matching
    `generator/climate.py`. `parse_when`'s relative forms (`+2h`, `-1d 18:40`, `HH:MM`) count from the current time
    (the episode start at setup); `day+N HH:MM` from the episode's start date; wall-clock times follow DST on model 2.
21. **`tools/verify.py --privileged-waits`** lists reference and wrong plans (the sweep's 13 specs include one whose
    only `wait_until` is in a wrong plan: A-2bbd9d39ff1512-01); the verifier counts the reference plan's executed ones.
22. **Verification lint**: a `never` without an `after` window that holds in the initial state is a trial error
    (`temporal lint`); milestones that hold in the initial state are reported as `temporal_warnings` (that is what
    changed A-d9e7a4d5d112d2-01, section 6).
23. **Requests**: the default requester is the first resident with role `adult` by id (else the first person); a
    request without its own phrasings says the drawn instruction; `phrasings_by_role` applies when the requester's
    role has a list; the heard rule of 5.3 is the kernel default (intercom: always, note: never heard).
24. **A phase's budget ends it when exceeded** (steps > budget, or time > budget, at the end of that step), as the
    episode budget does: an agent that succeeds on its last allowed step and declares done passes; one that keeps
    acting past the budget fails ("Budget exhaustion ends a phase as failed").
25. **Responses are judged at samples**: an obligation is met at the first sample (every action, every substep of at
    most 30 s, every auto command, every step end) where the response holds, and missed when that first sample comes
    after its due time (a late response is not counted as met). A predicate the monitor did not evaluate at the
    previous sample (a response looked at only while an obligation is open, a window that just opened) is evaluated
    afresh instead of reusing its footprint cache.

## 5. Stubs and contracts

* Removed: every `TIER1-STUB(TIME-EVAL)` marker of A0 in `timeline.py`, `monitor.py`, `phases.py`.
* Left (PEOPLE-IO removes them when its modules land; all are guarded so the stubs keep working):
  `engine.py` `_start_mind` / `_update_mind` / `load` / `_mind_dump` (Mind.from_config, update, dump, load and
  `Perception.perceived_ids`), `monitor.py` `_Sampler.view` and `evaluation.py` `evaluation_view` (`mind.view()`),
  `predicates.py` `_check_form` (`reports.match`, `reports.match_set`). `grep -rn "TIER1-STUB" house_sim tools`.
* PEOPLE-IO's `systems/core.py` carries `TIER1-STUB(TIME-EVAL)` notes for the engine's Mind calls, which have landed.
* **Stage-C merge (section 10):** every marker above is gone; the calls go to PEOPLE-IO's modules directly.
* Consumed: PHYS-INT's touch log (named consumer "monitor"), `phys_scratch`, `settle.begin_action` / `settle`;
  CAM-RUNTIME's `camera.witness` / event points; BODY-ENGINE's advance modes, `settle_energy`, `power_guard`,
  `BodyRefusal.apply`; BODY-MODEL's `body.is_on`, `can_hear`, `imaging`, `get`; GEN's `generate_house(gen=)`,
  `clock.model` / `region` / `latitude`.

## 6. Gate results

Gate id `TIME-EVAL`, record `catalog/analysis/tier1/gate/TIME-EVAL/` (2026-10-15T16:18Z), run on an isolated copy
(`git archive HEAD` plus this package's 34 files) so PEOPLE-IO's in-progress edits on the shared tree stay out of it.
The isolated copy's code is byte-identical to the working tree's TIME-EVAL files (only `docs/TASK_SPEC.md` got one
clarifying sentence and this handoff its section 6 afterwards).

```
python tools/orchestration/tier1_gate.py --root <isolated copy> --gates-root catalog/analysis/tier1/gate record \
    --id TIME-EVAL --package TIME-EVAL --prev catalog/analysis/tier1/gate/B6 --out catalog/analysis/tier1/gate/TIME-EVAL --jobs 7
python tools/orchestration/tier1_gate.py compare B6 TIME-EVAL --package TIME-EVAL        (project root: compare result PASS)
```

Summary line: `TIER1-GATE TIME-EVAL [TIME-EVAL] vs B6: FAIL (11 pass, 1 fail, 1 skip)`.

| check | result | note |
|---|---|---|
| G0 | PASS | action fingerprint 482f9adf9717 -> 24641cb8a126 (425 actions, 111 systems; `wait`, `watch` parameters: S-R3, allowed) |
| G1 | FAIL | 51 files x normal + strict, 3,687 tests; only `test_eval_splits.py` (normal and strict) fails: `test_gitignore_covers_private_artifacts`, failing at HEAD already (request 2). The six new files pass in both modes (20 + 19 + 17 + 11 + 36 + 9 = 112) |
| G1b | PASS | isolated domains: the 8 failing tests are all known from B6; kernel files with `HOUSE_SIM_DOMAINS=core` 25 / 25 |
| G1c | SKIP | no import-inert modules for this package |
| G2 | PASS | own mode 241 / 242 specs (1,210 trials); 1 verdict change (reviewed), 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 225 / 242 (1,210 trials); the same reviewed verdict change, 0 digest changes; 250 realistic-spec trials identical in G2 and G3 |
| G4 | PASS | perception audit geometric and room: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems; generation-1 digests 340 / 340 equal |
| G6 | PASS | fuzz battery / geometric / room: 1,800 commands each, 0 defects, 0 nondeterministic (succeeded 375 -> 398: the new `wait` / `watch` parameters) |
| G7 | PASS | 3.16 ms / substep (B6 3.11, +1.6 %, limit 3.265); 1 h wait 1.119 s (B6 1.117, +0.2 %, limit 1.173); quiet day 6.573 s (`bench_systems.py --day`, seeds 0-2; budget at landing <= 7.2 s) |
| G8 | PASS | room golden, golden_all |
| G9 | PASS | `tools/sweep_bench.py` ok (below) |
| G10 | PASS | 34 changed files, 0 outside TIME-EVAL's list |

Reviewed differences against the TIME-EVAL row of TIER1_INTEGRATION 3.3:

* **S-R1** (milestones sampled inside waits): of the 7 milestone specs of sweep section 2, six keep their verdicts on
  every seed in both modes (T-8e57df5742271b-01, T-ea2be64edfb17d-01, A-8910a09f802fbf-01, T-dda4e199f3f5b0-01,
  X-c77e2cbe7181f0-01, T-1970299fc6d849-01); A-d9e7a4d5d112d2-01 seed 0 changes (table below; triaged: the kernel
  is right, the spec is fixed by request 1).
* **S-R2** (fire-time perceivability): A-8910a09f802fbf-01 and T-6203fdc5fa8986-01 unchanged on every seed, both
  modes.
* **S-R3**: the fingerprint above. **S-R4**: 0 state-digest and 0 observation-digest changes in 2,420 trials (the
  default `wait` and queue-mode `watch` are unchanged; the new sections appear only for schema-3 tasks or when used).

Benchmarks. The gate ran while PEOPLE-IO's gate loaded the same machine (100 % CPU), so wall times carry noise; an
earlier record of this package (before the last monitor / phase fixes) measured 3.12 ms / substep, 1.116 s and
6.429 s. A paired CPU-time comparison (one `wait 86400` on generated houses 0-2, HEAD and this package interleaved,
three runs each): 6.59 s vs 6.69 s per quiet day (+1.5 %: notice derivation, monitor sampling and the order check per
substep; the kernel's three probes save little because their systems are cheap). The 3.5 s target needs the
domains' probes (request 6). `tools/sweep_bench.py` (budgets of sweep 20):

| measure | ms | budget |
|---|---|---|
| notices (derivation + interrupt check per substep) | 0.0014 | 0.03 |
| until (path predicate per substep) | 0.0005 | 0.02 |
| monitor_idle (sampling, nothing touched) | 0.0028 | 0.05 |
| monitor_quant (quantified predicates over touched entities) | 0.0314 | 0.5 |
| calendar (cold data load, then a year of holidays and DST for all five regions) | 0.3445 | 20 |
| ask, report, mind_update | skipped | PEOPLE-IO's (2.0, 1.0, 1.0) |

### Reviewed differences (TIER1_INTEGRATION 3.3)

| catalog id | seed(s) | mode | old -> new | code | evidence | decision |
|---|---|---|---|---|---|---|
| A-d9e7a4d5d112d2-01 | 0 | both | passed -> failed (idle_fails true -> false) | S-R1 | variant 0 (burnt toast): the milestone `quiet` (no detector in alarm) holds in the initial state, because the detectors only start sounding at the safety system's first tick (30 s); sampled at the first step's action sample it is reached at t = 0, so its 300 s deadline is met by doing nothing. Measured: the alarm sounds 30-390 s when nobody acts; the reference has it quiet within about 2 minutes | kernel right (accurate sampling); spec fix requested from safety (request 1: the deadline becomes a response to the alarm starting; probed: passes on all five seeds, idle fails, wrong plans fail) |

## 7. Known gaps

* **Sound propagation** (6.2, P-approx) knows rooms, connections and doors only: no walls between rooms without a
  connection, no windows, floors or distance within a room; the quietest path is taken. Its thresholds are the
  sweep's assumptions; people may replace the propagation through `robot_hears`.
* **Notices nobody posts yet**: the kernel posts speech, people and pets coming and going, orders, battery, faults,
  physics sounds and the geometric camera's seen events; doorbells, knocks, alarms, phones, crying, chimes, drips
  and device notifications need their domains (adoption round, request 6). Until then `interrupt_on: ["urgent"]`
  hears only addressed speech. *Stage-C fix (section 11, T2): the kernel now bridges alarms (an alarm that starts
  sounding is an `alarm` notice), so "urgent" hears smoke and CO alarms too.*
* **`obs.notices` / `obs.request`** are PEOPLE-IO's rendering of the `notices` and `requests` sections; until it lands
  the agent sees notices only through a wait's message and the request only as `obs.instruction`.
* **The Mind**: `env.mind` stays None while `kernel/mind.py` is a stub; `mind.*` predicates then see nothing. The
  engine calls are in place and tested against the stub.
* **Report and question forms** delegate to PEOPLE-IO's `reports.match` / `match_set` and read its `dialogue_log`;
  tests patch `reports`; the field names of deviation 16 are an assumption until PEOPLE-IO confirms them.
* **Phase conditions** (`ends.when`, `start.when`) are evaluated on the plain state (no `mind.*`, no
  `milestone_count`); per-phase `milestones` share the global namespace. *Stage-C fix (section 11, T8): a phase's
  milestones are its own now ("<id>/<name>").*
* **Held auto orders** wait for the next wait or gap; an agent that never waits never runs them (`run_pending` serves
  queue orders only). Auto orders do not run during `listen` (not an idle action).
* **Calendar data**: one holiday table per region (no state, province or Land variants), today's DST rules for every
  year, equinox formula valid 1980-2099.
* **The viewer** (`house_sim/eval/viewer.py`) recomputes milestones per step with plain predicates and does not know
  the extended milestone forms or `phase_done` history records (request 4).
* **Idle probes**: only the kernel's three systems have probes; the per-day budget of 3.5 s needs the domains'
  probes (adoption round; `tools/bench_systems.py --day` lists the per-system costs, section 6).

## 8. How to test

```
python -m unittest tests.test_timeline tests.test_world_savepoint tests.test_monitor tests.test_phases tests.test_clock tests.test_predicates_v3
HOUSE_SIM_STRICT=1 python -m unittest tests.test_timeline tests.test_world_savepoint tests.test_monitor tests.test_phases tests.test_clock tests.test_predicates_v3
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_timeline tests.test_world_savepoint tests.test_monitor tests.test_clock tests.test_predicates_v3
python tools/sweep_bench.py                      # micro-benchmarks against their budgets (exit 1 above 3x)
python tools/bench_systems.py --day 0 1 2        # a quiet simulated day per seed and per-system ms per day
python tools/verify.py --privileged-waits        # migration debt M-WAITS
python -m house_sim --check tasks/authored/safety__examples.json --ids A-d9e7a4d5d112d2-01 --seeds 0
python tools/orchestration/tier1_gate.py compare B6 TIME-EVAL --package TIME-EVAL
```

Manual checks: `python -m house_sim --seed 0`, then `{"action": "watch", "condition": {"path": "time_s", "op":
"gte", "value": 120}, "commands": [{"action": "announce", "text": "two minutes"}], "mode": "auto"}` and `wait 600`
(the message lists the order that ran); `{"action": "wait", "seconds": 3600, "interrupt_on": ["urgent"]}`;
`python -c "from house_sim.kernel import clock; print(clock.holidays_between({'clock': {'region': 'jp'}},
'2026-01-01', '2026-12-31'))"`.

## 9. Follow-ups

Requests to the main session (files outside TIME-EVAL):

1. **Spec fix (safety owner), `tasks/authored/safety__examples.json`, A-d9e7a4d5d112d2-01 variant 0** (section 6):
   replace the variant's `"milestones": {"quiet": Q}` and `"deadlines": {"quiet": 300}` with
   `"milestones": {"alarm_sounding": {"exists": {"where": {"ids": "{detectors}"}, "check": {"path": "alarm_state",
   "op": "eq", "value": "alarm"}}}}` and `"responses": [{"trigger": "alarm_sounding", "response": Q, "within_s": 300,
   "each": false, "label": "quiet"}]` (Q unchanged: every detector not in alarm). The intent ("quiet within five
   minutes of the alarm") is then what is scored; probed on seeds 0, 1, 2, 41, 97: passes, idle fails (the alarm
   sounds 360 s), both wrong plans fail. Optionally the safety helpers could leave detectors already sounding when
   they stage smoke above the threshold (a consistent initial state); the verifier now warns when a milestone holds
   in the initial state (`temporal_warnings`).
2. **`tests/test_eval_splits.py`** `PackagingTests.test_gitignore_covers_private_artifacts` fails at HEAD (normal and
   strict): the stage-B commit added `demos/replays/` to `.gitignore`, and the test asserts that demo replays are not
   ignored. Align the test or `.gitignore` (main session's decision on the 226 MB of replays, B6 request 3). It is the
   only failing test file of this package's G1 and fails identically without this package.
3. **`house_sim/audit.py`**: `verify(..., gen=None)` passing `gen` to `verify_many`, so `python -m house_sim
   --verify --gen 2` works for M-GEN2 (the CLI already refuses the flag with a clear message until then).
4. **Viewer** (`house_sim/eval/viewer.py`, B-CLOSE's): the per-step milestone recomputation (around line 1649)
   should read the episode's monitor record (`env._monitor.record()` / `evaluate()["milestones"]`) instead of
   `check(state, rule)` on the task's milestones, which fails on `{"pred": ...}` forms and misses in-wait times;
   `phase_done` history records (`"phase_done": true`, command action `phase_done`) need a step-list entry; the B6
   note about `notices` / `order_log` marks applies.
5. **PEOPLE-IO** (handed over with this file; it merges second): render `obs.instruction` / `obs.request` from
   `state.requests` of the active phase or `env.request()` (which also gives the `r0` default); `obs.notices` from
   `state["notices"]` (records carry `"step"`); confirm the `dialogue_log` fields of deviation 16; the
   `TIER1-STUB(PEOPLE-IO)` guards in `engine.py`, `monitor.py`, `evaluation.py`, `predicates.py` (section 5) become
   dead code once its modules land and are removed at C-CLOSE (they are TIME-EVAL's files); `docs/AUTHORING_GUIDE.md`
   (PEOPLE-IO's) can take the sweep 23 recipes for temporal keys, phases, requests and waits (docs/TASK_SPEC.md has
   the reference).
6. **Domains (adoption round, TIER1_INTEGRATION 6)**: post notices with `w.notice` (people: doorbell, knock, crying,
   call_for_help, phone; electrical and safety: alarm, device; appliances_cooking: appliance_done, alarm;
   plumbing_liquids: drips; outdoor_plants: thunder, rain on windows; pets: barking), registering kinds beyond the
   built-ins with `timeline.register_notice_kind`; exact idle probes on their heavy systems (`@system(..., idle=fn)`,
   with a probes-on / probes-off digest test like `tests/test_timeline.py` `IdleProbeTests`); navigation_robot: a
   `phase_start` hook to refresh its spot bookkeeping after a `phase_relocate`.
7. **Dataset** (`house_sim/eval/dataset.py`, C-CLOSE): multi-phase episodes (their `done` ends a phase) when phased
   specs are exported.
8. **C-CLOSE**: the action fingerprint changed (`wait`, `watch` parameters): regenerate evidence, replays and
   datasets; refresh `tests/data/spec_baseline.json` from the C1 gate.
9. **Design docs** (main session's): TIER1_INTEGRATION 1.11 / TIER1_SWEEP 6.1 name the `power_down` / `fault_onset`
   hooks as the sources of battery and fault notices; the implementation uses the event log (deviation 2).

## 10. Stage-C merge gate (C-MERGE)

Added by the stage-C merge gate after both stage-C packages had handed over. The same section is in
`handoff_TIME-EVAL.md` and `handoff_PEOPLE-IO.md`. The merge gate read every `handoff_*.md`, TIER1_INTEGRATION (1 with
1.3, 1.8, 1.9 and its rules, 1.11-1.14; 2.5; 3; 5), TIER1_DECISIONS and TIER1_SWEEP (4.10-4.11, 5.4-5.5, 6, 7.1-7.4,
9, 10, 16-19, 21, 23). It worked on the merged tree (HEAD + TIME-EVAL + PEOPLE-IO) within the union of the two
packages' files (2.5), then ran the full gate there with `--package TIME-EVAL+PEOPLE-IO`.

**Files edited by the merge** (all on the union's list; LF): TIME-EVAL's `house_sim/kernel/engine.py`,
`evaluation.py`, `monitor.py`, `predicates.py`, `timeline.py`, `house_sim/tasks/verify.py`, `tests/test_timeline.py`,
`tests/test_phases.py`, `tests/test_predicates_v3.py`, `docs/KERNEL_API.md`, `docs/EVALUATION.md`,
`docs/TASK_SPEC.md`; PEOPLE-IO's `house_sim/kernel/perception.py`, `dialogue.py`, `reports.py`,
`tools/perception_audit.py`, `tests/test_dialogue.py`, `tests/test_reports.py`, `tests/test_mind.py`,
`tests/test_requests_obs.py`, `docs/PERCEPTION.md`, `docs/DIALOGUE.md`; and both handoffs.

**Stub calls.** Every `TIER1-STUB(PEOPLE-IO)` guard left in TIME-EVAL's files is gone; the real implementations are
called directly: `engine.py` (`_start_mind`, `_update_mind`, `load`, `_mind_dump`), `evaluation.py`
(`evaluation_view`), `monitor.py` (`_Sampler.view`), `predicates.py` (`report` / `report_set`). `docs/KERNEL_API.md`
no longer says the Mind may be a stub, and its stale stage-B sentence naming `TIER1-STUB(CAM-RENDER)` now says the
guard is gone. `grep -rn "TIER1-STUB" house_sim tools tests docs` finds nothing outside `docs/design/`.

**What did not fit together, and the fixes.** Items 1-7 have regression tests; the tests of items 1-6 and the
`lint_plan` part of item 7 were checked to fail with their fix undone (in a scratch copy of the tree); the verifier
functions of item 7 did not exist before. Item 8 is a tool change, checked by the gate's G4 run.

1. **A realistic replay lost the prior knowledge.** (`engine.py`, TIME-EVAL's; PEOPLE-IO's `Mind.from_config`.)
   `HouseEnv.replay` re-creates the Realism after the constructor's reset, but the Mind had been started (and had
   issued the prior knowledge's handles and granted its read / inspect levels) against the discarded one. The
   replayed robot no longer knew a jar it read yesterday, and its recall, Mind and realism dump all differed from the
   recorded episode. Fix: `replay` starts the Mind again right after the new Realism and `_note_view` (the order of
   `reset`). Test: `test_mind.PersistenceTests.test_replay_rebuilds_prior_knowledge_in_the_realism`.
2. **A phase without a request showed the previous phase's instruction.** (`perception.py`, PEOPLE-IO's;
   `engine.py`.) `perception.current_request` fell back to the last request of any phase, so `obs.request` and
   `obs.instruction` (and with them the LLM agent's instruction and the harness's `on_request`) still showed phase 1's
   request while `env.request()` and the task's instruction were phase 2's. Fix: one rule,
   `perception.current_request`: the active phase's latest request, or between phases the request of the phase that
   ended last, and None when that phase came without one (its instruction is then the task's); `env.request()` now
   returns that record (or the r0 default). Test: `test_requests_obs.PhaseTests.
   test_a_phase_without_a_request_shows_its_own_instruction`.
3. **Two rules for "the requester".** (`evaluation.py`, TIME-EVAL's; `dialogue.py`, PEOPLE-IO's.) The evaluation's
   `ask_whom: "requester"` took `user_simulator.intent.requester` first (the runner binds it to phase 1's requester),
   the dialogue's `known_by: "requester"` the current request's `from`. In a phase asked by someone else, a required
   question answered by that phase's requester counted as asked of someone who knows, yet failed `ask_whom`. Fix:
   `dialogue.requester` is the one rule (the current request's `from`, else `intent.requester`, else the household's
   `requester` from `schema.normalize`) and `evaluation._requester` calls it. Test: `test_requests_obs.PhaseTests.
   test_the_requester_of_a_phase_is_its_requests_from`.
4. **A phase's request was never heard as a notice.** (`timeline.py`, TIME-EVAL's; seen through PEOPLE-IO's
   `obs.notices`, `listen` and waits.) The message cursor of `derive_notices` was the boundary key (steps, time_s), so
   a request posted at that boundary after its first derivation (a phase that starts in a wait or a gap) was never
   fresh: a `wait(interrupt_on=["addressed"])` slept through the new request, and `obs.notices` never listed it,
   although the engine meant it to ("a new phase's request reaches a listening wait at this boundary"). Fix: the
   cursor also counts the messages already at its key (`_msg_cursor`), and the engine derives notices after a phase
   change at the end of a step and in `phase_done`. The episode's first request stays `obs.request` only (the cursor
   starts after it is posted). Tests: `test_requests_obs.PhaseTests.
   test_a_phase_request_is_heard_and_phase_done_shows_only_new_notices`,
   `test_a_listening_wait_hears_the_request_of_a_phase_that_starts_in_it`.
5. **`phase_done` repeated the last step's notices.** (`engine.py`; PEOPLE-IO's `perception.notices_since`.)
   `phase_done` counts no step, so its observation listed the notices stamped with the step the agent had already
   seen. Fix: with the notice bus on, `phase_done` writes `state.phase.notice_mark = {"step", "t", "n"}` and
   `notices_since` lists only what was perceived after it. Test: the first test of item 4.
6. **Report windows ignored legacy reports.** (`predicates.py`, TIME-EVAL's; PEOPLE-IO's `reports.match`.) The
   predicate bounded `report_log` by `after` / `before_s` and passed the expectation on without them, so a legacy field
   report (which `reports.records_of` also matches) counted whenever it was made (PEOPLE-IO request 3). Fix:
   `predicates.report_bounds` resolves the `after` anchor to seconds and `reports.match` / `match_set` apply the
   window to every record (a legacy report whose time the event log no longer holds is outside every window). Test:
   `test_predicates_v3.ReportFormTests.test_report_forms_bound_typed_and_legacy_reports_by_their_window` (now on the
   real module; it replaced the stub-era patched test).
7. **The verifier ran neither the dialogue lints nor the report evidence check.** (`tasks/verify.py`, TIME-EVAL's;
   PEOPLE-IO's `dialogue.lint_*` and `reports.perceivability_gaps`; sweep 4.11 and 5.5; PEOPLE-IO request 2;
   DIALOGUE.md already said the verifier called them.) Fix: `verify.dialogue_lint` runs for `"schema": 3` specs after
   the reference run (`lint_plan` on the reference, `lint_facts`, `lint_reference` for the task's and each phase's
   questions with the wrong plans); a problem fails the reference (`dialogue_lint`). With realistic perception,
   `verify.report_perceivability_gaps` checks the records the spec's `report` / `report_set` success predicates
   score (a report_set's correct ones; not under `not`) with the fields the reference run's observations showed
   (things, people and pets) and the perceived notice kinds as evidence; gaps join `perceivability_gaps`
   (`{"report": ...}`). Both read the phases' success too (`verify.success_rules`, also used by the legacy check).
   `dialogue.lint_plan` now looks inside foreach / repeat blocks and catches a do step's legacy `destination` alias;
   `reports.matching_records` / `matching_set_records` give the scored records (`match_set` shares their
   selection). Nothing changes for the 242 specs (no schema-3 spec, no report form). Tests:
   `test_dialogue.LintTests.test_reference_lints` (extended), `test_the_verifier_runs_the_lints_after_the_reference`;
   `test_reports.PerceivabilityTests.test_the_verifier_checks_the_reports_its_success_predicates_score`,
   `test_matching_records`.
8. **The perception audit never saw a notice.** (`tools/perception_audit.py`, PEOPLE-IO's.) Its houses ran without
   the notice bus (data-gated by TIME-EVAL for schema-3 episodes), so the `people` checks of `obs.notices` never ran.
   Its houses now run with the bus on and half its waits listen (`interrupt_on`); 0 leaks, 0 crashes (room,
   geometric, battery).

**End to end** (TIER1_SWEEP 21, "Integration"): three schema-3 specs that need both packages, kept as tests (seed 0,
both perception modes, generated houses with core types so they pass core-only):
`test_timeline.EndToEndTests` (an auto standing order reports Sam's status by text to the requester; a response
obligation, a `never` window before the milestone and a `report` predicate after it), `test_dialogue.EndToEndTests`
(a fact, the canonical ask skill, `questions.required` and `ask_whom: "requester"`; the schema-3 legacy-topic
variant fails `dialogue_lint`), `test_phases.EndToEndTests` (two phases with a gap, a gap event and the dock
relocation, prior knowledge, the question asked in phase 1 and not again in phase 2). On seeds 0, 1, 2, 41, 97 (their
default generation-2 houses) all three pass in their own mode and under forced realistic perception with the room
camera; with the geometric camera the preference and two-phase specs pass in both modes and the monitoring spec in
its own mode only (the first known gap below). Every wrong plan fails for its own reason
(`never:early`, `response:told`, `question_missing`, `phase_failed:p1/p2`), and the traced reference runs of 36
combinations (3 specs x oracle / realistic x room / geometric x seeds 0, 1, 41) replay, save and load to identical
states, Minds and evaluations. The sweep's third example ("where I usually put it" from `people.history`) waits for
the people domain; the two-phase question spec stands in for it.

**Gate.** Gate id `C-MERGE`, packages `TIME-EVAL+PEOPLE-IO`, previous gate `B6`, record in
`catalog/analysis/tier1/gate/C-MERGE/`, taken on the merged working tree (its `git status` lists only the union's
files, so no isolated copy was needed):

```
python tools/orchestration/tier1_gate.py record --id C-MERGE --package TIME-EVAL+PEOPLE-IO \
       --prev catalog/analysis/tier1/gate/B6 --jobs 7 --work <scratch>/gate_work
```

Summary line: `TIER1-GATE C-MERGE [TIME-EVAL+PEOPLE-IO] vs B6: FAIL (11 pass, 1 fail, 1 skip)`. The one FAIL is G1's
`test_eval_splits.py` `test_gitignore_covers_private_artifacts` (normal and strict), which fails at HEAD and in both
package gates alike (`.gitignore` ignores `demos/replays/`; TIME-EVAL request 2, PEOPLE-IO request 5). `compare`
passes against B6, TIME-EVAL and PEOPLE-IO. After this section was written, G10 was refreshed with `--only G10`
(PASS; the rest of the record kept).

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `955c5ef9ac14` (425 actions, 111 systems), PEOPLE-IO's: the merge changed no action |
| G1 | FAIL | 55 files, 3,808 tests (normal), normal + strict; only `test_eval_splits.py` fails (above). The merge's tests pass in both runs: `test_dialogue` 55, `test_mind` 19, `test_phases` 18, `test_predicates_v3` 11, `test_reports` 26, `test_requests_obs` 19, `test_timeline` 37 |
| G1b | PASS | isolated domains: the 8 failing tests known since B6, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 241/242 (1,210 trials); vs B6 the one reviewed change of TIME-EVAL's table (the safety spec, seed 0), 0 state-digest, 0 observation-digest changes |
| G3 | PASS | forced realistic 225/242; the same reviewed change, 0 / 0 digest changes; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric (now with the notice bus and listening waits): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.10 ms per substep (B6 3.11, limit 3.265), 1 h wait 1.111 s (1.117, limit 1.173), quiet day 6.329 s (PEOPLE-IO 6.331, TIME-EVAL 6.573) |
| G8 | PASS | room golden and golden_all, unchanged (not recorded) |
| G9 | PASS | `tools/sweep_bench.py`: notices 0.0016 ms (budget 0.03), until 0.0005 (0.02), monitor_idle 0.0028 (0.05), monitor_quant 0.0325 (0.5), calendar 0.337 (20), ask 0.598 (2), report 0.257 (1), mind_update 0.146 (1) |
| G10 | PASS | 56 changed files, 0 outside the union |

`compare PEOPLE-IO C-MERGE` and `compare TIME-EVAL C-MERGE` (both with `--package TIME-EVAL+PEOPLE-IO`): 0 verdict,
0 state-digest and 0 observation-digest changes in both modes; the merge's fixes touch only the new features
(phases, requests, the notice bus, report forms, schema-3 verification, realistic replays with prior knowledge),
which no authored spec uses yet. **Reviewed differences:** none new (TIME-EVAL's S-R1 row covers the one change
against B6). **Golden:** unchanged.

**Known gaps found by the merge, not fixed here** (outside what the two packages can settle alone):

* **`timeline.perceivable` is not camera- or light-aware for realistic perception.** It uses room-mode visibility
  for things and counts a person's or pet's location and posture whenever they are in the robot's room, also in the
  dark, with the cameras off and, with the geometric camera, out of view. So a `wait(until=...)` or a standing order
  can fire on what the robot does not perceive (TIER1_INTEGRATION 1.3 asks for perceivable signals only). The new
  report evidence check exposes it: the monitoring spec above fails realistic verification with the geometric camera
  (`perceivability_gaps: report 1 (status awake of sam)`: Sam stood up behind the robot, the order fired, the robot
  never saw him). Request to the main session (before D3 makes the geometric camera the realistic default): for
  realistic perception, test what the robot perceives now (in room mode the room lit, imaging on, the thing or
  person not concealed; in geometric mode the camera's current view), per referenced id so the `until` budget of
  0.02 ms per substep holds; the same for `person_enters` / `person_leaves` notices in geometric mode.
* `env.known_map()` / `env._known_map` remain the observation-time map, not a view of `mind.map` (sweep 7.1):
  `navigation_robot/paths.py` (outside the union) reads its keys and exits. PEOPLE-IO request 4 stands (adoption
  round).
* The report evidence check does not collect inspect / read results or instrument readings from action messages;
  realism's knowledge at the end of the reference run stands for them (it lapses once the thing moved, which can
  report a gap for a thing inspected and then moved).

**Requests carried over.** TIME-EVAL 1 (the safety spec fix of A-d9e7a4d5d112d2-01), TIME-EVAL 2 = PEOPLE-IO 5
(`test_eval_splits.py` vs `.gitignore`), TIME-EVAL 3, 4 and 6-9 stand. PEOPLE-IO 1 (stub markers), 2 (verifier lints
and evidence) and 3 (report windows) are done here; PEOPLE-IO 4 stands.

**How to test the merge**

```
python -m unittest tests.test_mind tests.test_requests_obs tests.test_predicates_v3 tests.test_dialogue tests.test_reports tests.test_timeline tests.test_phases
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_mind tests.test_requests_obs tests.test_predicates_v3 tests.test_dialogue tests.test_reports tests.test_timeline tests.test_phases
python tools/orchestration/tier1_gate.py compare B6 C-MERGE --package TIME-EVAL+PEOPLE-IO
python tools/orchestration/tier1_gate.py compare PEOPLE-IO C-MERGE --package TIME-EVAL+PEOPLE-IO
grep -rn "TIER1-STUB" house_sim tools tests docs | grep -v "docs/design"        # nothing
```

## 11. Stage-C fix (STAGE-C-FIX)

Added by the stage-C fix pass after the stage-C review (`catalog/analysis/tier1/stageC_review_time.json`, 11 findings;
`stageC_review_people.json`, 22 findings). The same section is in `handoff_TIME-EVAL.md` and `handoff_PEOPLE-IO.md`.
Read before starting: both review files in full, TIER1_SWEEP (4-10, 16-23), TIER1_INTEGRATION (1 with 1.3, 1.8, 1.9;
2.5; 3; 5), TIER1_DECISIONS and every `catalog/analysis/tier1/handoff_*.md` (TIER1-GATE, B0, B6, GEN, NAV-M2,
PHYS-DATA, PHYS-STATICS, PHYS-FLUIDS, PHYS-INT, CAM-SIGHT, CAM-RUNTIME, CAM-ACTIONS, CAM-RENDER, BODY-MODEL,
BODY-ENGINE, BODY-CORE, BODY-FAULTS, TIME-EVAL, PEOPLE-IO). Every finding was reproduced with the reviewers' probes
(`t1_reviewC_time/`, `t1_reviewC_people/`) before and after its fix. Work stayed inside the union of the two packages'
files (TIER1_INTEGRATION 2.5).

### Findings and what was done

TIME (stageC_review_time.json):

| # | finding (severity) | status | fix | regression test |
|---|---|---|---|---|
| T1 | until / watch / person notices use room-mode visibility; identity oracle by id in the dark (high) | fixed | `timeline.observed` / `perceivable` / `holds_perceived` judge every atom per referenced id: `entity_seen` (room camera: visible, not a dark shape, realistic not missed; geometric: `camera.entity_view_now`, the step's view at the level the field needs), `agent_seen` (realistic: lit room / identified in the view); three-valued all / any / not; `wait`'s at-once check, standing orders and guards use `holds_perceived`; notices name a source only when `source_perceived` (person_enters by `agent_seen`, person_leaves by the room rule or the camera log's sightings, so save / load stays exact); speech notices no longer carry `data.speaker`; in the dark every typed person id is refused by realism (P4) with one wording | `test_timeline.StageCFixTests`: `test_until_and_watch_need_the_thing_seen_now`, `..._a_wait_on_someone_tells_nothing_about_who_is_there`, `..._a_person_behind_the_geometric_camera_is_not_observed`, `..._a_condition_seen_hidden_does_not_end_a_wait_at_once`, `..._a_speech_notice_names_its_speaker_only_when_seen`, `..._an_any_condition_ends_on_the_branch_the_robot_sees` |
| T2 | nothing wakes a waiting robot when the smoke alarms sound (high) | fixed | kernel bridge in `derive_notices`: an entity that starts sounding (alarm_state alarm / testing, T3 / T4 pattern, beeping; static index of alarm-capable entities) posts an `alarm` notice at its `alarm_db` (default 95 dB at 1 m) through the 6.2 propagation (heard in privacy mode too); `alarm_state` joins OBSERVABLE_FIELDS and is observable by sound when compared with "alarm" | `test_smoke_alarms_post_alarm_notices_and_can_be_waited_on`, `test_an_unattended_wait_hears_the_safety_specs_smoke_alarm` (A-d9e7a4d5d112d2-01: the wait ends at 30 s) |
| T3 | "X leaves" accepted but never fires, "X comes in" refused (medium) | fixed (deviation) | a people / pets location compared (eq / ne) with the robot's room is accepted while the robot watches its room (room camera: oracle always, realistic lit with imaging; geometric: imaging) and observed as absent when the robot can tell (room camera: watching the room; geometric: the person is tracked, `view.track`). **Deviation**: the review suggested the step view's coverage for the geometric camera and footsteps by ear; a coverage taken before a long wait cannot tell who walked out behind the camera during it, and footsteps do not tell who, so geometric absence needs tracking and hearing is not used | `test_until_and_watch_on_someone_coming_in_or_leaving` (oracle and realistic, dark-room refusal) |
| T4 | milestones / responses reached by an action stamped at its start (medium) | fixed | `Monitor.sample(env, point, at=None)`: the engine passes the action's end (start + the step's seconds; a wait: its start; the partial-commit path; an auto command: its end) and what the sample finds is stamped then (milestones, counted times, held_for starts, response triggers and met responses; windows and never / always latches keep the sample time) | `test_monitor.NeverAlwaysTests.test_the_action_sample_sees_what_the_action_did` (updated pin), `test_an_action_that_ends_after_a_deadline_misses_it` |
| T5 | `decided` "fail" from a superseded (unscored) phase (medium) | fixed | `Monitor.decided(state, skip=())` skips the entries of the phases `evaluation.decided` finds ended unscored | `test_phases.StageCFixTests.test_a_superseded_phases_failures_do_not_decide_the_episode` |
| T6 | gen-2 clock shows DST while every domain keeps standard time (medium) | fixed | DST applies only with `clock.dst: true` besides clock model 2 (`clock.dst_enabled`; wall, describe, parse_when); the generator does not set the flag, so a generation-2 house shows the time its people, clock displays and collection days keep. Moving the domains to wall time (and then setting the flag) is an adoption-round follow-up (below) | `test_clock.DstFlagTests` (incl. a gen-2 house in October: obs.time = the people's hour) |
| T7 | hidden weather bookkeeping readable through until / watch (medium) | fixed | `weather.<field>`: never `perception.WEATHER_HIDDEN` or `_` keys; with the network every perceived field; from outdoors or through a window without the network, realistic perception only the `condition` word without numeric ops | `test_the_weather_bookkeeping_is_not_observable` |
| T8 | a phase's deadline met by a milestone of an earlier phase (medium) | fixed | a phase's milestones compile as `<id>/<name>`, sampled only inside the phase, and its deadlines, response triggers and window anchors name them first; `Monitor.overlay(phase)` gives the phase's predicates (milestone counts, report anchors) its own milestones under their plain names; the same name may serve several phases | `test_a_phases_deadline_needs_its_own_milestone` |
| T9 | auto orders stall inside waits; the 500-command budget disables them for the rest of a long wait; note uncapped (medium) | fixed | an order due when a listening wait ends runs before the wait ends; the budget is per step and simulated hour (logged once per window; a paused firing resumes at its next command, order key `resume`, where before it restarted its commands); the "standing orders ran" note lists at most 10 commands ("(+N more)") | `test_an_order_that_fires_in_a_listening_wait_runs_before_it_ends`, `test_the_command_budget_is_per_hour_of_a_long_wait`, `test_the_standing_orders_note_is_capped` |
| T10 | idle verification stops after 30 h (medium) | fixed | `verify._wait_out`: at most 30 waits of max(3600, ceil(budget / 30)) s (at most a week each), for the episode and each phase; budgets up to 30 h are waited exactly as before | `test_idle_verification_waits_out_a_long_budget` (40 h) |
| T11 | a contingent phase that never starts makes the episode unwinnable (low) | fixed | phase key `"optional": true` (lint: only with a when / at_s start): not started, it is unscored and keeps nobody waiting; another scored phase not started is `phase_not_started:<id>` among the episode's failures | `test_an_optional_interruption_that_never_comes_is_unscored` |

PEOPLE (stageC_review_people.json):

| # | finding (severity) | status | fix | regression test |
|---|---|---|---|---|
| P1 | asking about "request" hands over the hidden target (high) | fixed | "request" stays a request subject (kind request, id the request record's id, key `q:<aspect>:request:<rid>`); facts and routes match the thing it stands for (`dialogue.bound_subject`, `request_target`), the answer's `about` is "request", "it" is the request again; `(which, request)` reaches `which:<category, else the target's type>`; entity routes (where:, owner:) no longer see the hidden target | `test_dialogue.StageCFixTests.test_an_answer_about_the_request_never_names_its_target`, `..._a_fact_about_the_target_still_answers...`, `..._which_about_the_request_reaches_the_requesters_which_answer` |
| P2 | phone / text / app cannot reach anyone away (high) | fixed | `dialogue.reach` and `reports.deliver`: network and a phone, wherever the person is (calls: not to a deaf person; asks: awake); `skills.ask` / `tell` never walk to an absent person | `test_phone_and_text_reach_someone_away_from_home`, `test_reports.DeliveryTests.test_phone_text_and_app_reach_someone_away_from_home` (both modes) |
| P3 | prior beliefs show a look-alike's true type (high) | fixed | `setup._belief` records the prior `level` (oracle label: the label, else the type in words); `Mind.from_config` renders each prior belief's ident / label through `realism._identify` at that level | `test_mind.PriorKnowledgeTests.test_prior_look_alikes_are_remembered_at_the_level_they_were_seen` |
| P4 | in the dark a typed person id comes back as its shape handle (high) | fixed | `realism.inbound`: while shapes share the robot's dark room every person / pet id is refused in every parameter (about, subject, options, to, predicate paths) with one wording; free-text ids become "someone" | `test_a_typed_person_id_never_comes_back_as_a_shape_in_the_dark` |
| P5 | privacy mode / a dark room blocks contacting anyone (medium) | fixed (design note) | a command over a remote channel (`channel` phone, text or app) names people as they are and its reply keeps them plain (`realism._named_plain`); voice in the dark still refuses every id alike (refusing only the people in the room would itself tell who is there) | `test_privacy_mode_still_reaches_people_elsewhere_by_phone`, the dark-room test above |
| P6 | "I'm not sure." vs "I don't know." (medium) | fixed | one default, "I don't know." (an author's `otherwise` stays) | `test_someone_who_does_not_know_answers_like_an_unmodelled_question` |
| P7 | a clarify-back only when a fact exists (medium) | fixed | for entity-directed aspects (owner, where, keep, intended, ok, why, how, status) a word naming several things is asked back unless a fact about the word itself answers -- whether or not one of the things has a fact | `test_a_word_naming_several_things_is_asked_back_whether_or_not_one_has_a_fact` |
| P8 | words match any word of a compound name (medium) | fixed | head-noun rule in `names_entity` and type-fact matching ("dryer" names a clothes dryer, not dryer sheets) | `test_words_name_things_by_their_head_noun` |
| P9 | options do not resolve a clarify-back (medium) | fixed | options narrow the things a word names: one left, the question is about it; several, the person asks back among those (per-option answers are not modelled: documented) | `test_options_narrow_a_word_naming_several_things` |
| P10 | a memory key containing a fact id as a substring (medium) | fixed | whole `:` / `.`-separated segments | `test_only_a_whole_memory_key_segment_names_a_fact` |
| P11 | "who is at the door?" answered from the visitor's hidden identity before the robot looked (medium) | fixed (PEOPLE-IO part) | `dialogue.VETTED_ROUTES`: `people.visitor` answers only about a visitor the robot has perceived (Mind); the answerer's own reliance on `_identity` is the people domain's (follow-up below) | `test_who_is_at_the_door_needs_a_visitor_the_robot_has_seen` |
| P12 | questions answered through a failed speaker / a call to a deaf person (medium) | fixed (canonical path) | voice / phone questions need `body.can_speak` (else `"said": "(no answer)"`, time spent, nothing counted, the fault detected as speech detects it); a call to a deaf person is refused ("They cannot hear a call; text them"). The legacy topic path stays byte-identical (gate rule) | `test_a_question_through_a_failed_speaker_gets_no_answer`, the away-phone test (deaf) |
| P13 | obs.request misattributes relays and shows a remote sender's room (medium) | fixed | `request_view`: a relayed request's `from` is the relayer, `claims_from` the claimed requester (dialogue.requester unchanged); `room` only for in-person / overheard / intercom; `post_request` / the runner default the room to the speaker's, and the request message's room is None for remote channels | `test_requests_obs.StageCFixTests.test_a_relayed_request_is_said_by_its_relayer`, `..._a_remote_request_does_not_say_where_its_sender_is` |
| P14 | listen drops alarm notices in privacy mode (medium) | fixed | `listen` returns the stored notices as perceived | `test_listen_returns_every_perceived_notice_in_privacy_mode` |
| P15 | privacy mode drops non-speech sound notices (medium, TIME-EVAL) | fixed | `robot_perceives` under "addressed" drops only speech not addressed to the robot | `test_timeline.AudibilityTests.test_hearing_privacy_powered_down_remote_and_visual` (updated pin), the listen test |
| P16 | the request message names the sender realism anonymised (low) | fixed | realistic `obs.messages`: a request's own message names its speaker by the request's sender decision (`realism.request_sender`) | `test_the_request_message_names_its_sender_as_the_request_does` |
| P17 | a note-channel request cannot name its note (low) | fixed | `"note"` in `spec.REQUEST_KEYS` (lint: channel note only), copied by `post_request` | `test_a_note_request_names_its_note_through_a_spec` (runner level, both modes) |
| P18 | a request's gesture is not in the first observation (low) | fixed | `post_request` fires people's `people.point` at once | `test_a_requests_gesture_is_in_the_first_observation` |
| P19 | answers expose the legacy route topic (low) | fixed | `route` / topic stay in dialogue_log only | `RouteTests` (updated pins), `test_who_is_at_the_door...` |
| P20 | clarify options name look-alikes by true labels (low) | fixed (decision, docs) | decided: options are the person's own words for their things (they know a look-alike's kind) and never map to handles, so they do not tell which handle is which; DIALOGUE.md says so, and section 7's known gap of handoff_PEOPLE-IO.md is corrected | docs |
| P21 | agent docs: inaccurate or cut sentences (low) | fixed | DIALOGUE.md (options, look-alike memory, channels, request subject), the LLM prompt (count's scope optional, channels, options), ASK_DOC (default channel), `listen`'s doc (107 characters: whole in the 120-character catalog) | docs |
| P22 | the perception audit cannot see these leaks (low) | fixed | `tools/perception_audit.py`: houses get a request with a hidden target, prior look-alikes at a glance and dark rooms with people (`enrich`); the walk asks about "request" and about people present or not, reports to people away, issues wait(until) on handles in or out of view, dark shapes and people; checks (`check_people_message`, `check_typed_people`). Re-injecting the old P1 / P3 behaviour in a scratch process, the audit reports 2 dialogue and 130 memory leaks on 6 houses x 150 steps; the fixed code reports 0 | the gate's G4 |

Spot checks that the new tests catch the old code (each fix undone by monkeypatching the pre-fix function back in a
scratch process; scratch `mutation_check.py`): P8, P10, T5, T6, T7, T10 -- every one of their tests fails with the fix
undone. The reviewers' probes (all 28) were re-run: every reported symptom is gone (e.g. probe4: the 20 s walk now
misses the 5 s deadline; p10: `about` "request" for kim / ann / sam; p14: one refusal for every typed id in the dark).

### Files edited

TIME-EVAL's: `house_sim/kernel/timeline.py`, `engine.py`, `world.py`, `monitor.py`, `phases.py`, `evaluation.py`,
`clock.py`, `camera.py`, `house_sim/tasks/spec.py`, `runner.py`, `verify.py`, `tools/sweep_bench.py` (the until bench
now runs the whole per-substep check; the calendar bench uses a DST clock), `tests/test_timeline.py`,
`test_monitor.py`, `test_phases.py`, `test_clock.py`, `docs/TASK_SPEC.md`, `EVALUATION.md`, `KERNEL_API.md`,
`EVAL_CONTRACTS.md`. PEOPLE-IO's: `house_sim/kernel/dialogue.py`, `reports.py`, `mind.py`, `realism.py`,
`perception.py`, `house_sim/systems/core.py`, `house_sim/tasks/setup.py`, `skills.py`, `house_sim/eval/llm.py`,
`tools/perception_audit.py`, `tests/test_dialogue.py`, `test_reports.py`, `test_mind.py`, `test_requests_obs.py`,
`docs/DIALOGUE.md`, `PERCEPTION.md`; both handoffs; the gate record `catalog/analysis/tier1/gate/STAGE-C-FIX/`. All LF.

### API changes (additive unless marked)

* `timeline`: `perceivable(w, rule, realism=None, env=None)` (env added; the semantics are the per-id rule),
  `observed`, `holds_perceived`, `entity_seen`, `agent_seen`, `source_perceived` (replaces the private
  `_source_perceived(state, source)`), ALARM_* constants, `alarm_state` in OBSERVABLE_FIELDS; `IdleRun.paused`;
  `start(env)` keeps `w._env_ref` (a weak reference) and the alarm snapshot.
* `camera.entity_view_now(env, eid)` (acuity memoized by the watch signature + frames + containment chain: 0 mismatches
  against `acuity` in 4,331 checks of a randomized geometric walk), `camera.agent_level_now(env, aid)`.
* `Monitor.sample(env, point, at=None)`, `decided(state, skip=())`, `overlay(phase=None)`; phase milestone entries carry
  `"phase"`. `clock.dst_enabled(state)`. Phase key `optional`; request key `note`; `perception.request_view` adds
  `claims_from`.
* `dialogue`: `bound_subject`, `request_target`, `VETTED_ROUTES`, `find_route(..., env=None)`, `ENTITY_ASPECTS`,
  `UNKNOWN`, `NO_ANSWER`; **DEVIATION** of the frozen 16.1 Subject contract: a request subject's `id` is the request
  record's id (it was the bound target), so nothing shown can carry the target. `realism.request_sender(rid, state)`,
  `REMOTE_CHANNELS`.

### Gate

Gate id **STAGE-C-FIX**, packages `TIME-EVAL+PEOPLE-IO`, previous gate `C-MERGE`, record in
`catalog/analysis/tier1/gate/STAGE-C-FIX/`, taken on the working tree after every change of this pass:

```
python tools/orchestration/tier1_gate.py record --id STAGE-C-FIX --package TIME-EVAL+PEOPLE-IO        --prev catalog/analysis/tier1/gate/C-MERGE --jobs 7 --work <scratch>/gate_work
```

Summary line: `TIER1-GATE STAGE-C-FIX [TIME-EVAL+PEOPLE-IO] vs C-MERGE: FAIL (8 pass, 4 fail, 1 skip)`. Each FAIL:

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `955c5ef9ac14` unchanged (doc strings are not in it) |
| G1 | FAIL | 55 files, 3,849 tests (normal), normal + strict; only `test_eval_splits.py` `test_gitignore_covers_private_artifacts` fails, as at HEAD and in the TIME-EVAL, PEOPLE-IO and C-MERGE gates (`.gitignore` ignores `demos/replays/`; TIME-EVAL request 2 = PEOPLE-IO request 5). The stage-C files: `test_timeline` 50, `test_dialogue` 68, `test_requests_obs` 25, `test_phases` 22, `test_clock` 22, `test_monitor` 20, `test_mind` 20, `test_reports` 27 -- all green in both runs |
| G1b | PASS | isolated domains: the 8 failing tests known since B6, none new; kernel files with `HOUSE_SIM_DOMAINS=core` 29/29 |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | FAIL (by policy) | own mode 241/242 specs (1,210 trials): **0 verdict changes** vs C-MERGE, 0 observation-digest changes; 5 `idle_state_hash` changes, all A-d66994a566899c-01 (below). The one failing trial is A-d9e7a4d5d112d2-01 seed 0, unchanged since TIME-EVAL's reviewed S-R1 row (the spec fix is TIME-EVAL request 1, the safety owner's file) |
| G3 | FAIL (by policy) | forced realistic 225/242: 0 verdict changes, 0 observation-digest changes, the same 5 idle digests; the 250 realistic trials identical in G2 and G3 |
| G4 | PASS | perception audit room and geometric, with the extended people checks: 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz room, geometric and battery: 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.10 ms per substep (C-MERGE 3.10), 1 h wait 1.109 s (1.111), quiet day 6.33 s (6.329) |
| G8 | PASS | room golden and golden_all unchanged (not re-recorded) |
| G9 | PASS | `tools/sweep_bench.py`: notices 0.0024 ms (budget 0.03), until 0.0181 (0.02; the bench now runs the whole per-id check on a held condition), monitor_idle 0.0028 (0.05), monitor_quant 0.0321 (0.5), calendar 0.341 (20), ask 0.775 (2), report 0.103 (1), mind_update 0.148 (1) |
| G10 | FAIL | 58 changed files: every file of this pass is on the union's list; the two outside it are the review's own outputs, `catalog/analysis/tier1/stageC_review_people.json` and `stageC_review_time.json` (untracked inputs written by the review step, not by this pass) |

Geometric camera (not in sweep_bench): an until on a thing in view costs 0.027-0.029 ms per quiet substep with
`camera.entity_view_now`'s memo (0.35-0.46 ms before it, on gen-1 seeds 0-1).

**The 5 state-digest changes** (no package code covers them, so `compare` cannot accept them; listed for the main
session):

| catalog id | seed(s) | mode | old -> new | code | evidence | decision |
|---|---|---|---|---|---|---|
| A-d66994a566899c-01 | all | both | `idle_state_hash` changed; verdict unchanged (`idle_fails` true, passed) | T10 (stage-C fix) | the spec's budget is 64 h (230,400 s): the idle check used to stop after 30 waits of 3,600 s (108,000 s) -- the review's T10 defect (its scan missed this spec); it now waits 30 x 7,680 s = 230,400 s and still fails (`damage`) | accept: the verification is now complete; nothing else in the record moved |

### Known gaps left (not fixed here; each needs another owner or a decision)

* The geometric camera can tell that someone left only when it tracks them (T3 deviation above).
* `obs.weather` itself still shows numeric fields through a window in realistic perception (perception.py renders the
  record; realism does not coarsen it); until / watch no longer accept them without the network (T7), the observation
  should match (follow-up for the weather owners and realism).
* Clarify-back answers per option (P9) are not modelled: several options make the person ask back among them.
* A routed answer's value may name a visible thing's id, which realism shows as its handle (e.g. a which: answer about
  the requested thing in view): the answer's content, unchanged here.

### How to test

```
python -m unittest tests.test_timeline tests.test_monitor tests.test_phases tests.test_clock tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
HOUSE_SIM_STRICT=1 python -m unittest tests.test_timeline tests.test_monitor tests.test_phases tests.test_clock tests.test_dialogue tests.test_reports tests.test_mind tests.test_requests_obs
python -m unittest tests.test_timeline.StageCFixTests tests.test_phases.StageCFixTests tests.test_dialogue.StageCFixTests tests.test_requests_obs.StageCFixTests
python tools/perception_audit.py 6 150 --workers 4 [--camera geometric]
python tools/sweep_bench.py
python tools/orchestration/tier1_gate.py compare C-MERGE STAGE-C-FIX --package TIME-EVAL+PEOPLE-IO
```

### Follow-ups (requests to the main session)

1. Adoption round (TIER1_INTEGRATION 6): people (schedules), electrical (clock displays), admin_inventory (collection
   days) and climate (thermostat schedules) move to wall time; then the generator sets `clock.dst: true` for gen-2
   houses (T6).
2. people: the `visitor:` answerer should answer from what the resident knows (the expected list: "I'm expecting Dana
   -- check it's her"), not from `_identity.kind`, and grant `admit` only for an identified expected guest (P11).
3. Design docs (main session's): TIER1_SWEEP 4.3 step 5 (clarify-back decided from the word; options narrow), 4.5
   (memory keys by whole segments), 6.2 / TIER1_INTEGRATION 1.3 (the alarm bridge; privacy mode keeps non-speech
   notices: already the 1.3 wording), 9 (`optional` phases, per-phase milestone namespace), 10.2 (`claims_from`,
   remote rooms) to match the code.
4. Legacy `ask` (topic path) still answers through a failed speaker (P12): byte-identical by the gate rule; change it
   when the legacy path is next opened (with its spec re-verification).
