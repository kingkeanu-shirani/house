# Handoff WP2 ATTRIBUTION, FLOWS, FINISH (VERIFY_HARDENING section 25; sections 6.2, 6.5, 8.1, 8.6, 6.10, 10.5)

Files: `house_sim/kernel/world.py` and `house_sim/kernel/engine.py` (changed), `tests/test_attribution.py` (new, 25
tests with WP2-fix's), `tests/test_finish.py` (new, 18 tests), `tests/data/room_golden.json` (re-recorded: 14 spec
entries, class `logs.gone`; the package's golden rows are `catalog/analysis/phase2/WP2_reviewed.json`, the main
session signs them).
Result: `catalog/analysis/phase2/WP2.json`. Nothing else was edited.

## What landed

1. **The last-writer log** (6.2), `env.writes` (a `world.WriterLog`, outside world state). Every write after the
   episode baseline is attributed per record field to its writer `(class, name)`:

   | Class | Writers |
   |---|---|
   | `robot` | the step's action window (handler, hooks, its own action-phase settle, the step's bookkeeping), `phase_done`, an auto standing-order command (`order:<action>`), and a substep settle-pass consequence the kernel physics attributes to the robot (`physics.settle`: impact records `by == "robot"`, i.e. what the robot arranged, the spec's "physics flagged `_robot_moved` in the same step") |
   | `physical` | every system tagged physical (`plugins.SYSTEM_WRITERS`, WP1's `@system(..., writer=)`, default physical), the substep settle pass (`physics.settle`), the kernel's own bookkeeping (`kernel`), the end-of-step perception updates (`kernel.perception`: camera, realism, Mind), every `set_quiet` write |
   | `volition` | systems tagged volition (people and pets once WP-D tags them), scheduled task events (`event:<kind>`), phase starts and ends of the task's schedule (`phase`), people's and pets' reactions to the robot's sounds (`robot_sound`) |
   | `setup` | after the baseline: the first request (`reset`); writes made outside the engine's frames (`external`: a tool or test poking `env.world`) |

   Mechanism (World): per frame (`begin()` .. `commit()`) each journaled record (entities, rooms, people, pets,
   connections) keeps one segment per change of writer; a clone of the record is taken only when the writer changes.
   At commit each segment's changed top-level fields become log entries; a robot segment that changed a placement field
   sets the entity's robot placement P_R(e). Creations and destructions are field `"$exists"`; top-level sections are
   logged per section (`("section:<name>", None)`), scalars (`time_s`, `steps`) not at all. A refused action and a
   rolled-back savepoint leave nothing (rollback clears the frame; a savepoint marks segment counts, quiet writes and
   pending records). Quiet writes are logged at the write and their fields left out of the record diffs.
2. **Causes and writer tagging** (6.2's causal link): `w.writing(cls, name=None, cause=None)` (context manager),
   `cause=` on `mut / create / destroy / set_quiet`, `w.cause(rid, *ids)` after a write. `env.robot_attributable(rid,
   field, t, causes, unknown=None)` is three-valued (WP2-fix, A-gaming-5): True when the robot wrote rid or an entity
   of its location chain by t, or the cause chain (at most 5 physical links) reaches a record one of whose fields the
   robot was the last to write when the citing write was made; False when recorded causes rule the robot out; None
   ("link unknown") when the physical write reports no cause -- 6.2's conservative default, which a charging caller
   treats as linked. WP1's ledger calls it and keeps the None.
3. **`logs.gone`** (8.6): every entity destroyed during a tracked episode leaves `{"t", "entity", "type", "location",
   "by", "name", "last"}` in state `logs.gone` (capped at 1,000), `w.gone_record(eid)` returns its final record. This
   closes the phase-1 request "a bookkeeping record of destroyed entities (world.gone_record)" (people, finish_INT2
   not_fixed; regression test `GoneAndFlowTests.test_a_destroyed_entity_keeps_its_final_record_with_its_writer`).
4. **Mess flows** (6.5): `w.mess_flow(kind, amount, src, dst, origin=None, cause=None)` and `w.mess_transform(origins,
   new_kind, amount, at=None, cause=None)` append to state `logs.mess_flows` with the current writer; identical flows
   since the last transform merge (amount summed, `n` counted, `t0` first / `t` last). WP4's core verbs already call it.
5. **`HouseEnv.finish(settle_s, lookahead_s, process)`** (8.1, 6.10): goal at D, idle settle window sampled every 30 s
   with robot-attributable regressions failing `settle:<label>` and the rest `consumed`, evaluation at F, optional
   look-ahead with `later:<label>`. Not a step; recorded in the history and replayed.
6. **`HouseEnv.fast_forward(seconds, tick=300)`** (10.5): a plain wait on coarse substeps while nothing needs fine time.
7. **Save / load / replay**: `env.save` stores the writer log, the substep index, the score and the idle
   counterfactual's digest (WP2-fix, A-spec-9); `HouseEnv.load(path, idle=None)` restores them (a pre-WP2 state loads
   with an empty log) and attaches the caller's idle after checking the digest; `HouseEnv.replay` rebuilds the log by
   re-running and repeats `finish` and `fast_forward` records.
8. `env.idle` (None): the idle counterfactual WP1's evaluation reads (WP5 / WP10 set it). A property: on an env
   loaded from a snapshot saved with one and not attached again, reading it raises `IdleMissing`; assigning checks the
   snapshot's digest (ValueError on a different counterfactual).

## Interfaces (signatures)

```python
# house_sim/kernel/world.py
World.track(log)                                   # WriterLog or None; the engine calls it at the baseline
World.writer -> (class, name); World.tracking -> bool
World.writing(cls, name=None, cause=None)          # context manager; cls in WRITER_CLASSES
World.cause(rid, *ids)                             # rid "mug_1" | "connections:<id>" | "rooms:<id>" | "people:<id>" | "pets:<id>"
World.mut(eid, cause=None); create(eid, record, cause=None); destroy(eid, cause=None)
World.set_quiet(eid, field, value, cause=None)
World.mess_flow(kind, amount, src, dst, origin=None, cause=None) -> entry or None (untracked / amount 0)
    # src: "floor:<room>" | "entity:<id>" | "drain:<id>" | "outside:<zone>" | "created:<source>" (origin defaults to
    #      "created:<step>"); dst: floor | entity | drain | outside | "loss:evaporation" | "loss:consumed:<id>";
    #      a (class, id) pair is accepted; ValueError on a bad kind, amount or key
World.mess_transform(origins, new_kind, amount, at=None, cause=None) -> entry or None
World.gone_record(eid) -> the final record or None
World.reclassify(ids, old_writer, new_writer)      # engine-internal (substep settle attribution)
WriterLog.entry(rid, field, before=None) -> (class, name, t0, t, causes) or None    # the write standing at `before`
WriterLog.during(rid, field, t0, t) -> [(class, name, t0, t, causes)]               # the writes standing in [t0, t]
WRITER_CLASSES, EXTERNAL_WRITER, PLACEMENT_FIELDS, EXISTS ("$exists"), is_bookkeeping(field), location_key(...)

class WriterLog(Mapping):                          # env.writes
    writes[(rid, field)] -> (class, name, t)       # field None for ("section:<name>", None)
    history(rid, field) -> [{"class", "name", "t0", "t", "causes"}]   # oldest first, runs merged, 64 kept
    fields(rid) -> [field]
    last(rid, field=None, before=None, ignore=None) -> (class, name, t) or None
    causes(rid, field) -> [ids]
    wrote(cls, rid, before=None, fields=None, ignore=is_bookkeeping) -> bool
    robot_wrote(rid, before=None, fields=None, ignore=is_bookkeeping) -> bool
    robot_placement(eid) -> {field: value} or None; placement_time(eid)
    created_by(rid) -> (class, name, t) or None
    dump() / WriterLog.load(data)                  # "house-writes-v1"
    recent                                         # None, or a list collecting committed entries (finish uses it)

# house_sim/kernel/engine.py
env.writes; env.idle (None; a property, see 8); env.robot_placement(eid)
env.robot_attributable(rid, field=None, t=None, causes=None, ignore=is_bookkeeping, unknown=None) -> True | False | unknown
    # field None: any of rid's fields; a placement field: any placement field. unknown: the value for a physical write
    # without a recorded cause (None = "link unknown", charged by the conservative default; finish passes False)
HouseEnv.load(path, idle=None); env.save(path)     # the snapshot keeps {"digest", "checkpoints"} of env.idle
engine.IdleMissing(RuntimeError); engine.idle_record(idle) -> {"digest", "checkpoints"}; engine.CLOCK_DIGITS (3)
env.finish(settle_s=None, lookahead_s=None, process=None) -> evaluation
    # None: the task's hardening block (settle_s, lookahead_s, process), else the task's keys, else 60 / 0 / [];
    # settle_s 0..600, lookahead_s 0..86400 (ValueError otherwise), both rounded to the clock's millisecond grid
    # (WP2-fix, A-bugs-2: an off-grid remainder used to spin the settle loop forever). The result is evaluate() at F
    # with success,
    # failures, goal_fraction, budget_exceeded, steps, time_s (D's), score, unmet (D's) decided as 8.1 says, plus
    # "declared" {"steps", "time_s"}, "end_time_s", "consumed" [{"label", "t", "writers": [[class, name]]}],
    # "settle" {"s", "samples", "regressions", "consumed"}, "lookahead" {"s", "labels"}.
env.fast_forward(seconds, tick=300) -> the wait step's result; env.last_fast_forward {"coarse", "fine", "seconds"}
engine.goal_items(task, phase=None) -> [(label, test(view), predicate)]
engine.predicate_fields(rule) -> ({eid: fields or None}, quantified fields)
engine.system_writer(name) -> (class, name)
```

## finish in detail

* Labels: a `{"label", "pred"}` item's label, else the label recorded for its index (`task["labels"]["success"]`, else
  `hardening.labels.success`, else `task["success_labels"]`: the order of WP1's `evaluation.recorded_labels`), else
  `success_<i>`; plus `rubric` and `milestone:<name>`; in a multi-phase task the active phase's, as
  `<phase id>:<label>`. For a hardened task WP1's existence guard (`goal_status` -> `<label>:absent`) makes a label
  not held. Views come from `evaluation.evaluation_view(..., task=)` (WP1's derived-field overlay) when available.
* A regression (a label held at D and not now) is robot-attributable when a write since the previous sample meets the
  label's footprint at field level (WP3's `Footprint.meets`; a fallback `predicate_fields` otherwise) and is the
  robot's (bookkeeping fields and sections aside), or physical and `robot_attributable(..., unknown=False)` is True
  (8.1 step 3: a record the robot wrote, or a recorded cause; a physical write without causes is consumed, not
  failed). Volition writes never are.
  The first regression of a label decides it (latched). `process` labels are consumed instead.
* Failures = D's failures, then F's new ones except the clock's own (`budget`, `deadline:*`: the settle window is not
  the robot's time), then `settle:` labels, then `later:` labels. success = goal at D (D's goal_fraction 1 and every
  label held) and no failure.
* The look-ahead (fine ticks, one idle frame) reports as `later:<label>` a global flag, an invariant label or a ledger
  guard of `ledger.LOOKAHEAD_GUARDS` (house.damaged, the mess guards, house.waste, house.held_open) that is in the
  evaluation at L and not at F.
* Frames: each 30 s settle sample and the look-ahead are idle frames like a phase gap (systems, events, auto standing
  orders, end-of-step perception, monitor samples), on their own substep RNG streams (`FINISH_TICK_BASE`).

## fast_forward in detail

Coarse substeps of `tick` seconds (a multiple of 30, 30..3600) unless one of these holds, then fine 30 s substeps (and
two quiet fine substeps after it): an event (at_s / next_s within the coarse step, an unfired `when` event, a due
at_step event), a standing order that can fire or is due, a phase-structured task, a person or pet that changed room
or is active in the robot's room (position, posture, activity, awake, state changed; a resident asleep there is no
activity), a physics consequence or a raised global harm flag in the last substep. Systems still run in chunks of their
`max_dt`, so the saving is the per-substep overhead (settle pass, monitor, notices, orders, probes).

**Equivalence and rate (10.5, 22.1)** -- measured on the shared, busy machine (scratch `ff_equiv.py`, `ff_why.py`):

| Run | Result |
|---|---|
| the 14 authored specs whose references wait >= 600 s, seeds 0 and 1: reference (its waits >= 600 s fast-forwarded) and idle | 28 / 28 trials: identical success and failure labels for the reference and for the idle run; the fast-forwarded waits ran 1,022 coarse and 312 fine substeps where fine ticks take about 10,500; wall 103.7 s fine vs 80.1 s fast-forwarded (whole trials, builds and the non-wait steps included) |
| quiet 6 h day on generated houses 0-2 | 72-71 coarse + 2-14 fine substeps (vs 720 fine); 3.1-4.2 s vs 4.4-6.3 s fine (0.52-0.69 vs 0.73-1.05 CPU-s per simulated hour on the loaded machine; the plan's ceiling is 0.27 measured on a quiet one) |

`tests/test_finish.py FastForwardTests` pins: the same outcome as the fine wait on a quiet house with fewer substeps
than half, an event fired at the same time, determinism, replay, and the equivalence check on two authored specs.

## Overhead (6.2: at most 15 % of simulation time)

In-process A/B (tracking on vs `env.world.track(None)`, best of 3 on seeds 0-3, interleaved):

| Workload | Off | On | Overhead |
|---|---|---|---|
| G7's 1 h wait (one frame, 120 substeps), sum of seeds 0-3 | 1.206 s | 1.265 s | +4.9 % (first run +4.5 %) |
| 120 one-substep waits (one frame each: the per-frame diff) | 3.711 s | 3.978 s | +7.2 % |

`tools/bench_systems.py 40 0 1 2 3` A/B against a tree with HEAD's engine and world was dominated by the machine's
load (before 16.3-16.7 ms/substep, after 13.3-14.2; 1 h wait 0.97-1.01 s vs 1.12-1.18 s); the gate's G7 (run alone)
measures it against the 1.15 x prev budget the committed policy gives P2-WP2.

## Golden re-record (G8: the package's reviewed rows; the main session signs)

A/B on frozen trees (the live tree of 2026-10-29 with engine.py and world.py at HEAD 81e7a91 vs with WP2's):
`tools/golden.py dump --all` differs in 14 of 275 files, one path pattern only: `logs.gone` (null -> the list).
Re-recorded on HEAD 81e7a91 plus WP2's two files alone (`golden.py record --by WP2 --classes logs.gone`): only the final
state digest of these entries changed (steps, time and evaluation unchanged; the 8 script runs unchanged):

| Entry | Class | Code |
|---|---|---|
| specs/A-afe8d8c6d6d883-01 | logs.gone | P2 |
| specs/T-0adf708ce0749a-01 | logs.gone | P2 |
| specs/T-1df40780f5ddf2-01 | logs.gone | P2 |
| specs/T-238a7512e4dd6e-01 | logs.gone | P2 |
| specs/T-28ff695081cd2c-01 | logs.gone | P2 |
| specs/T-5c9694a5a29d0e-01 | logs.gone | P2 |
| specs/T-73a13fb297c40f-01 | logs.gone | P2 |
| specs/T-7e93003b0dcefa-01 | logs.gone | P2 |
| specs/T-a03bc04a3a311c-01 | logs.gone | P2 |
| specs/T-cc81e71dc2182e-01 | logs.gone | P2 |
| specs/T-d182fc681f310d-01 | logs.gone | P2 |
| specs/T-d389e2979f6aad-01 | logs.gone | P2 |
| specs/X-bc4e8d373e62c5-01 | logs.gone | P2 |
| specs/X-befab43585cde3-01 | logs.gone | P2 |

The same 14 rows as JSON: `catalog/analysis/phase2/WP2_reviewed.json` (pass with `--reviewed`). No verification
outcome changes (the writer log is outside the state; logs.gone / logs.mess_flows change digests only).

## Deviations from the spec (and why)

* **Segments instead of cursor ranges plus a step-end diff.** 6.2 sketches a cursor range per system call and one diff
  per journaled entity at step end, assigning post-window changes to the last system that touched the entity. The
  World instead splits each record's writes by writer as they happen (a clone only on a change of writer) and diffs
  segment by segment: every field gets its own writer even when two systems write one record in one substep, and the
  cost stays proportional to the records touched.
* **Quiet writes** (`set_quiet`) are logged at the write as class physical (6.2) and their fields left out of that
  frame's record diffs, so a field written both quietly and through `mut` in one frame keeps only its quiet writer
  (process fields, never charged end to end).
* **Robot attribution in the substep settle** comes from the kernel physics' own consequence records (`by == "robot"`,
  `impact.robot_caused`: the robot arranged the item or its support) rather than re-reading `_robot_moved` markers.
* **Kernel writer names** the spec does not fix: `kernel`, `kernel.perception` (physical), `phase`, `robot_sound`,
  `event:<kind>` (volition), `reset`, `external` (setup).
* **Extra keys**: logs.gone entries carry `"name"`; mess-flow entries `"op"`, `"t0"`, `"n"`, `"name"` and an optional
  `"cause"`, plus the classes `created:` (source) and `loss:` (sink) for 6.5's `created:<step>` origins and losses.
* **finish keeps D's failures** (a failure predicate or invariant that held when the robot declared done stays a
  failure even if it clears during settle) and drops F's clock-only labels (`budget`, `deadline:*`).
* **fast_forward is a step** (it replaces a WAIT plan step), and "activity in the robot's room" is a change of an
  agent's position, posture, activity, awake or state flag there (not presence: a resident asleep in the room no longer
  forces fine ticks); the spec's "watched zone" is not modelled (the per-spec equivalence check decides).

## Known gaps (and who owns them)

* **People and pets are physical until WP-D tags them** `@system(..., writer="volition")` (WP1's registry default is
  physical). Until then a resident moving the robot's plate during settle is a physical write on a robot-written
  entity: a `settle:` false alarm for a hardened spec. Requested from WP-D people / pets.
* **User-simulator answers and dialogue effects inside a robot action** are robot-window writes (6.2 lists them as
  volition). `kernel/dialogue.py` is in no package of section 25; the fix is `with w.writing("volition",
  "user_simulator"):` around a person's answer effects. Requested from the main session.
* **Causes**: no domain reports `cause=` yet. (Corrected by WP2-fix: this line used to claim such writes were charged;
  `robot_attributable` answered False and they never were, A-gaming-5.) It now answers None (link unknown) for them,
  WP1-fix's ledger keeps the None, so they are charged when they diverge from idle (and `display_time` is class
  process: before that it was the one false alarm the default added to references in a census of 8 houses). WP-D
  packages pass causes.
* History readers: a finish record is `{"command": {"action": "finish"}, "finish": {...}, "ok": true, ...}` (like a
  `phase_done` record); `house_sim/eval/agents.py` replays a shadow run's `_history` commands and would send
  `{"action": "finish"}` if a shadow run ever called finish (WP10's eval/run.py; skip records with "finish").
* The phase-1 optional performance request "touch log across begin()" (appliances_cooking, finish_INT2 not_fixed) is
  not done: semantics risk on the journal and touch-log contracts for about 0.03 ms per substep; it stays optional.

## Phase-1 carry-over

Closed: the destroyed-entities record (`world.gone_record`, people's request, finish_INT2 not_fixed and closeout_INT3's
deferred list) -- regression test above. Nothing else open lies in WP2's files: the 10 rows of
`closeout_breakages.json` are domain-side; `closeout_REV.json`'s findings (all 10 confirmed in `closeout_VER2.json`)
are the gate tool's, py39_lint's and domains'; `closeout_INT3.json`'s requests go to the main session, the domains and
WP9.

## How to test

```
python -m unittest tests.test_attribution tests.test_finish          # + HOUSE_SIM_STRICT=1; HOUSE_SIM_DOMAINS=core
python -m unittest tests.test_room_golden                            # the re-recorded golden
python tools/golden.py check --all
python tools/orchestration/green_tests.py 3
python -m house_sim --check <all authored specs combined> --workers 3
```

## Verification (final tree, 2026-10-29)

| Check | Result |
|---|---|
| `tests/test_attribution.py` + `tests/test_finish.py`, normal / `HOUSE_SIM_STRICT=1` / `HOUSE_SIM_DOMAINS=core` | 37 / 37 / 37 pass (core: 3 authored-spec tests skip) |
| `python tools/orchestration/green_tests.py 3` (normal + strict) | 5,190 tests per mode, 0 failures (the run saw 19 attribution tests; the 20th passes on its own) |
| All 243 authored specs, own mode (combined file, `--check --workers 3`) | 243 / 243 |
| `tools/golden.py check --all` | equal (after the re-record) |
| `tools/py39_lint.py` on WP2's four Python files | 0 violations |
