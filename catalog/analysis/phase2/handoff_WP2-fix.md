# Handoff WP2-fix (run A review findings A-bugs-2, A-gaming-5, A-spec-9; package P2-WP2)

Files: `house_sim/kernel/engine.py`, `house_sim/kernel/world.py`, `tests/test_attribution.py` (+5 tests, 25),
`tests/test_finish.py` (+1 test, 18), `catalog/analysis/phase2/handoff_WP2.md` (corrected: the false claim on causes,
the new interfaces). Result: `catalog/analysis/phase2/WP2-fix.json`. Nothing else was edited.

Every new test was run against a scratch tree with this fix's edits reversed (`make_prefix.py` in the scratch dir
`p2_WP2-fix`): each fails there (the settle test fails by its frame cap instead of hanging) and passes here.

## A-bugs-2 (finish never returned for a settle off the clock's millisecond grid) -- fixed

Root: `_substep` keeps `time_s` to 1 ms (`round(t + dt, 3)`), `finish` looped on the exact end time, so a remainder
below 0.5 ms advanced nothing and the loop ran a full idle frame forever. Besides `settle_s: 30.0004`, a clock that
starts off the grid (config `time_s: 7.0004`) with the default 60 s settle hung the same way.

Fix: `settle_s` and `lookahead_s` are rounded to the clock's grid (`CLOCK_DIGITS = 3`, also used by `_substep`), the end
time too, and the loop stops when a frame does not move the clock (P7: every run has a cap). The rounded values are
reported (`settle.s`, `lookahead.s`) and recorded in the history, so a replay repeats the run exactly.

Test: `tests/test_finish.py FinishTests.test_a_settle_off_the_clocks_millisecond_grid_ends` (both triggers; a frame
counter raises after 20 frames, so the old code fails instead of hanging).

## A-gaming-5 (physical consequences on things the robot never touched were never charged) -- fixed

Root (WP2's part): `env.robot_attributable` answered False ("not linked") for every physical write without a recorded
cause on a record the robot did not write -- and no domain reports causes yet -- while 6.2 says such a write is charged
whenever it diverges (the conservative default), and WP2's handoff claimed it was.

Fix: `robot_attributable(rid, field=None, t=None, causes=None, ignore=is_bookkeeping, unknown=None)` is three-valued:

* True: the robot wrote rid or an entity of its location chain by t, or the cause chain (at most 5 physical links)
  reaches a record one of whose fields the robot was the last to write **when the citing write was made**;
* False: no physical write of the field stands at t, or every branch of the recorded cause chain ends at a volition or
  setup write, an unchanged record, a physical write without causes, or after 5 links;
* `unknown` (default None, "link unknown"): the change is a physical write that reports no cause.

Two precision fixes in the same function, found while fixing it (no outcome changes today, since no domain reports
causes): (1) a cause record reached the robot only when the robot was the record's latest writer over all its fields,
so a burner the robot left on whose temperature a physical process keeps writing never linked the pan it scorched --
now any field whose robot write stood counts; (2) the chain was judged at the time of the question (the ledger asks at
F), so a resident switching the burner back later cleared the robot -- now each link is judged while the citing write
was made (`WriterLog.during`). A placement field asks about every placement field (a paper slid along the table
changes `pos`, not `location`).

`finish`'s settle rule passes `unknown=False` (8.1 step 3: only a record the robot wrote, or a recorded cause), so
settle outcomes are unchanged.

**End to end (with WP1-fix):** the ledger's `WriterLog.attributable` used to wrap the call in `bool(...)`, so the None
could never reach `charged_field` / `charged_placement` / `_destroyed` (which already treat None as charged). WP1-fix,
running concurrently, now keeps the None (ledger.py `attributable(eid, field, t=None)`: "WP2's link unknown ... is
kept") and reclassified `display_time` as `process`. On the final tree the reviewer's repro
(`p2_REV-A-gaming/r11b_fan_papers_allowed.py`) gives `finish success: False failures: ['house.changed:<paper>.scattered',
'house.moved:<paper>']`. The end-to-end test `CausalLinkTests.test_the_fan_the_robot_aims_at_loose_papers_is_charged_
with_them` runs and passes; it skips, with the reason, only if a ledger again turns the unknown into False.

**False alarms of the conservative default:** before `display_time` was reclassified, a census of the WP5 book spec's
reference with its idle counterfactual on 8 generated houses (scratch `p2_WP2-fix/ref_false_alarm.py`) showed one false
alarm class the default added: `house.changed:<clock>.display_time` (electrical clocks, smart speakers; writer
`electrical.clocks`, physical, no cause; a time readout differs from both bracketing idle checkpoints). On the final
tree the same census adds nothing (8 of 8 references pass with and without the default). Longer specs may show more
time-driven fields classed setting or condition: the field census never saw them, since the link was always False.

Tests: `CausalLinkTests.test_a_physical_change_without_a_recorded_cause_is_link_unknown_not_unlinked` (core scene: the
robot switches the lamp off, a test process blows the vase it never touched off the table), `...
test_a_cause_is_judged_by_who_wrote_it_when_the_change_was_made`, and the authored-type fan test above.

## A-spec-9 (env.save omitted env.idle; the fork test covered only legacy specs) -- fixed

Root: a snapshot did not carry the idle counterfactual, so a fork of a hardened run judged against `idle=None`
(`counterfactual.diverges`: every change diverges) unless the caller re-attached it, which was not documented or
checked. Embedding the projections costs up to ~20 MB per snapshot (2 checkpoints of a generated house are 0.42 MB as
JSON; 96+ for a 4 h budget), against 10.5's 0.5-1 MB.

Fix: `env.save` records `{"digest", "checkpoints"}` of `env.idle` (`counterfactual.Projections.digest`) and the score
the next reward starts from; `HouseEnv.load(path, idle=None)` attaches the caller's counterfactual after checking the
digest (ValueError on another one, or on one for a snapshot saved without one). Without it the fork owes one: `env.idle`
is a property that raises `IdleMissing` (a RuntimeError, so `getattr(env, "idle", None)` does not hide it) until it is
attached (`env.idle = projections`, digest-checked), so no evaluation of the fork judges against no counterfactual. A
pending env saved again keeps owing the same one. `load` restores the saved score instead of evaluating (an evaluation
would read the missing idle). `verify._confirm_sample` (load, then `copy_env.idle = env.idle`) works unchanged.

Tests: `ForkAndReplayTests.test_a_snapshot_keeps_the_idle_counterfactuals_digest_and_a_fork_must_attach_it` and
`...test_a_forked_hardened_run_finishes_like_the_uninterrupted_one_in_every_mode` (a schema-4 build, its idle
counterfactual, perceive.enable in the evaluation modes, a fork mid-reference, both ended by `finish(60, 600)`: equal
state digest, writer log, finish result and perceive evidence index in own, eval-geom and eval-room).

## Interfaces (new or changed)

```python
# house_sim/kernel/engine.py
env.robot_attributable(rid, field=None, t=None, causes=None, ignore=is_bookkeeping, unknown=None) -> True | False | unknown
env.idle                                   # property; raises IdleMissing on a fork that owes its counterfactual
HouseEnv.load(path, idle=None)             # idle: the saved env's counterfactual (digest-checked)
env.save(path)                             # payload adds "idle": {"digest", "checkpoints"} | None, "last_score"
IdleMissing(RuntimeError); idle_record(idle) -> {"digest", "checkpoints"}; CLOCK_DIGITS = 3
env.finish(...)                            # settle_s / lookahead_s rounded to the clock's grid
# house_sim/kernel/world.py
WriterLog.entry(rid, field, before=None) -> (class, name, t0, t, causes) | None
WriterLog.during(rid, field, t0, t) -> [(class, name, t0, t, causes)]
```

## Requests

* WP8: document the new interfaces (KERNEL_API.md / EVAL_HARNESS.md): the three-valued `robot_attributable`,
  `HouseEnv.load(path, idle=)` with `IdleMissing`, finish's grid rounding.
* WP1 (already done by WP1-fix on the final tree, listed for the record): keep `robot_attributable`'s None in
  `ledger.WriterLog.attributable`; `display_time` class process; `counterfactual.rollout` passed float seconds to
  `wait` (refused: "seconds must be an integer", one refused step per second) -- it now rounds to int. A field census
  with the unknown link charged would find the remaining time-driven fields classed setting or condition.

## How to test

```
python -m unittest tests.test_attribution tests.test_finish          # + HOUSE_SIM_STRICT=1; HOUSE_SIM_DOMAINS=core
python tools/orchestration/green_tests.py 3 --json <scratch>/green.json
python -m house_sim --check <all authored specs combined> --workers 3 --no-cache
```

## Verification (final tree, 2026-10-29)

| Check | Result |
|---|---|
| the 6 new tests on a scratch tree with the fix reversed | all fail (the settle test by its 20-frame cap) |
| `tests/test_attribution.py` + `tests/test_finish.py`, normal / `HOUSE_SIM_STRICT=1` / `HOUSE_SIM_DOMAINS=core` | 43 / 43 / 43 pass (core: 4 domain skips) |
| `python tools/orchestration/green_tests.py 3` (normal + strict) | 5,401 tests per mode, 0 failures |
| All 243 authored specs, own mode (combined file, `--check --workers 3 --no-cache`) | 243 / 243 |
| `tools/py39_lint.py` on the four Python files | 0 violations |
| reviewer repros: `finish_hang.py 30.0004`, `r11b_fan_papers_allowed.py`, `p_fork_idle.py` | 1 frame, ends at D + 30 s; `finish success: False` with `house.moved:<paper>`; load raises IdleMissing (attach with `idle=`) |
