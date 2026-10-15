# Handoff PHYS-INT (stage B1): the physics layer wired into the kernel

Read before starting: `docs/design/TIER1_INTEGRATION.md` (whole: 1, 2.4, 3, 5), `docs/design/TIER1_DECISIONS.md`,
`docs/design/TIER1_PHYSICS.md` (whole; package sections 6, 7, 8, 9, 11). Handoff files read: `handoff_TIER1-GATE.md`,
`handoff_PHYS-DATA.md`, `handoff_PHYS-STATICS.md`, `handoff_PHYS-FLUIDS.md`, `handoff_BODY-MODEL.md`,
`handoff_CAM-SIGHT.md`, `handoff_GEN.md`, `handoff_B0.md`. Their follow-ups addressed to PHYS-INT are acted on
(section 5) except the ones listed under Known gaps.

## 1. Summary

* The settle pass (`kernel/phys/settle.py`) runs after every action and every substep: touch log, structural
  signatures, `_robot_moved` attribution markers, FIFO over the active laws (orphan, overflow, setdown, drip, creep at
  landing; the others stay deferred), the 64-consequence budget, id-only notes, no undo after a consequence.
* World gains the touch log, `phys_scratch`, `set_quiet` / `mut_quiet` and `mutation_count`; Ctx gains the physics
  helpers and the motion record; core `pick_up` / `place` go through `lift_plan`, `contain.fits`, the set-down law and
  the broken-support refusals; the kernel `thermal` system is `phys.thermal.thermal_step` (active set, quiet writes).
* New: `phys/observe.py` (sounds, feel, looks, the one `emit_consequence`), `phys/events.py` (ten perturbation
  effects), setup ops `phys` / `load` / `edge` / `stage` / `check_physics` with the physics lint, `core.json`
  field_tiers, `tools/physics_audit.py`, `tools/golden.py` with the room golden, perception-audit physics checks, docs.
* 73 new tests (`test_physics_settle.py` 39, `test_physics_setup.py` 27, `test_room_golden.py` 7), green normal,
  strict and core-only. `compare B0 PHYS-INT --package PHYS-INT`: PASS, 0 verdict changes, G7 3.00 ms per substep
  (A0 4.38) and 1.079 s per 1 h wait (A0 1.102): kernel `thermal` went from 1.69 to 0.22 ms. The record's FAILs are
  outside PHYS-INT's files: one R7 test bound in textiles (G1, G1b) and the main session's untracked
  `handoff_B0.md` (G10) (section 6).

## 2. Files

Created: `house_sim/kernel/phys/settle.py`, `house_sim/kernel/phys/observe.py`, `house_sim/kernel/phys/events.py`,
`tools/golden.py`, `tools/physics_audit.py`, `tests/test_physics_settle.py`, `tests/test_physics_setup.py`,
`tests/test_room_golden.py`, `tests/data/room_golden.json`, `docs/PHYSICS.md`, this handoff, the gate record
`catalog/analysis/tier1/gate/PHYS-INT/`.

Edited: `house_sim/kernel/world.py`, `house_sim/kernel/engine.py`, `house_sim/kernel/actions.py`,
`house_sim/kernel/physics.py`, `house_sim/kernel/phys/impact.py`, `house_sim/kernel/phys/processes.py`,
`house_sim/kernel/phys/liquids.py`, `house_sim/kernel/phys/thermal.py`, `house_sim/kernel/phys/environment.py`,
`house_sim/systems/core.py`, `house_sim/tasks/setup.py`, `house_sim/tasks/verify.py`,
`house_sim/data/types/core.json`, `tools/perception_audit.py`, `docs/KERNEL_API.md`, `docs/CONVENTIONS.md`,
`docs/AUTHORING_GUIDE.md`, `docs/PERCEPTION.md`, `docs/KNOWN_LIMITS.md`.

All on the PHYS-INT list of TIER1_INTEGRATION 2.4 (G10, section 6). No `data/physics/*.json` file changed.

## 3. APIs

"ADDITION" = not in TIER1_PHYSICS 7 / TIER1_INTEGRATION 1; **DEVIATION** = differs from them.

**`house_sim/kernel/world.py`** (all transient: not state, never saved, strict mode unaffected)

```
w.touched_since(cursor, consumer=None) -> (ids, cursor)   ids written since cursor (absolute, append-only per step)
w.trim_touched()                                         engine, after each substep (keeps the log bounded)
w.mut_quiet(eid) -> record                               journaled write that is no touch
w.set_quiet(eid, field, value)                           ADDITION: journals one field without copying the record
w.journal_before(kind, name)                             a journaled key at step start (MISSING: created this step)
w.phys_scratch                                           dict of the settle pass (sig, cursor, phase, ledger, ...)
w.mutation_count                                         +1 per journaled write call and per rollback
w._power_down_step                                       None (BODY-ENGINE stamps it)
```

`begin()` and `rollback()` reset the transient part; `changes()` and `rollback()` cover quiet field writes.

**`house_sim/kernel/phys/settle.py`**

```
SIG_FIELDS, BUDGET = 64, CONSUMER = "settle"
signature(record) -> tuple
begin_action(w)                                          engine: phase "action" from the step's start
settle(env, phase, ctx=None, final=True) -> [records]    DEVIATION: `final` (ctx.settle_physics passes False)
travel_of(env, ctx=None) -> motion record | None         merged legs of the step (carry law)
```

**`house_sim/kernel/phys/observe.py`**: `emit_consequence(w, kind, target, **data)` (the single route, replaces the
stage-A shims in impact and processes), `heard_sounds(state, step=None)`, `sounds(env, w, obs)` (observer
`physics.sounds`, order 125), `feel_of(phys)`, `looks_of(record)`, `inspect(w, eid, rec, details, ctx)` (inspector
`physics`, order 150); constants `SOUND_OF`, `LOUDNESS`, `FAINT_J` 1.0, `LOUD_J` 15.0, `LOUD_TIP_KG` 10,
`FEEL_SOFT` 0.6, `FEEL_FIRM` 0.3, `STURDY_KG` 15.

**`house_sim/kernel/phys/events.py`**: `bump`, `nudge`, `pull`, `load_test`, `wind_gust`, `rain`, `transport`,
`brake`, `tilt`, `drop`, each `fn(w, args, env=None)`, registered as `event:physics.<name>` hooks (`EFFECTS`).

**`house_sim/kernel/phys/impact.py`**: ADDITION `slide(w, eid, accel_g, direction=None, cause="nudge", by=None,
cmap=None)` (the knock's slide branch on its own, used by `physics.nudge` / `brake`); `_scratch` uses
`w.phys_scratch`; `emit_consequence` is observe's.

**`house_sim/kernel/phys/environment.py`**: ADDITION `contact_mu(state, eid)` (wheels: tyre friction on floors;
casters: rolling resistance unless chocked, braked or secured), `CASTER_CRR_LOWEST`, `TYRE_FOOT`.

**`house_sim/kernel/phys/thermal.py`**: `thermal_step(w, dt, tick=None, mut=None, setter=None)` (ADDITION `setter`:
`w.set_quiet`).

**`house_sim/kernel/physics.py`** (the facade): `register_laws()` (ADDITION, see deviation 1), `physics_version()`,
`legacy_tau_s(entity)`, `tau_s(entity)` (follows the `thermal_tau` law), `PHYSICS_DATA`; systems
`physics.environment` (47, max_dt 30), `physics.processes` (48, max_dt 30), `thermal` (50, period 120: 
`thermal_step(..., setter=w.set_quiet)`); `thermal_system` kept unregistered as the pre-v3.0 reference; re-exports
of the layer (`physics.fall`, `physics.spill`, `physics.props`, `physics.fits`, `physics.settle`, ...).

**`house_sim/kernel/engine.py`**: `step` calls `settle.begin_action(w)` after `begin()` and
`settle.settle(self, "action", ctx)` after the action's energy settlement and before the message (notes); a step with
a consequence keeps no undo record; `perform` hands its leaves' motion records to the parent (`ctx.data["motions"]`);
`_advance` runs `settle.settle(self, "substep")` after the substep's events and `w.trim_touched()` after its orders;
`save_replay` writes `metadata.physics_version`, `replay` warns on a mismatch.

**`house_sim/kernel/actions.py`**

```
Ctx.phys(eid); Ctx.fall(eid, **kw); Ctx.spill(liters, liquid="water", **kw); Ctx.knock(eid, accel_g, **kw)
Ctx.lift_plan(eid, hands=None, helpers=())              never raises
Ctx.settle_physics()                                     early settle; the action phase continues
CONNECTION_CROSSING_M = 1.0
synth_motion(state, from_room, start, path, connection=None, seconds=0.0, speed=None) -> motion record
motion_record(ctx) -> record | None                      DEVIATION: None when ctx.data has "motions" (deviation 8)
motion_records(ctx) -> [records]
```

**`house_sim/systems/core.py`**: `subtree_mass` delegates to `phys.support.subtree_mass`; `pick_up` plans with
`ctx.lift_plan(target, hands="both" for grip=two_hand, else hand)` and keeps today's texts (`LIFT_OVER_CAPACITY`,
`LIFT_BOTH_HANDS`, `LIFT_NO_HAND`); `place` refuses through `support_failed(parent, relation, shelf=None)`
(`SHELF_BROKEN` "That shelf is broken", `SUPPORT_BROKEN` "It is broken and cannot hold anything"), checks
`contain.fits` (new refusal "It does not fit through the opening") and runs `impact.setdown` while the law is active.

**`house_sim/tasks/setup.py`**: ops `phys`, `load`, `edge`, `stage`, `check_physics` (documented in the module
docstring and AUTHORING_GUIDE); `PHYS_FIELDS` (validated keys and ranges), `LOAD_ITEMS`, `EDGE_SIDES`,
`EDGE_OVERHANG_MAX` 0.3, `physics_problems(ctx)`, `NEW_SPEC_SCHEMA = 3`; `SetupCtx.phys_base` / `physics_warnings`.
**`house_sim/tasks/verify.py`**: `trial["physics_warnings"]` (first 20 lint messages of an old-schema spec).

**`house_sim/data/types/core.json`**: file-level `field_tiers` (glance 11, inspect 4, read 1, hidden 50 fields;
docs/PERCEPTION.md "Physics").

**Tools**: `tools/golden.py record|check|dump|diff`; `tools/physics_audit.py [--data] [--bench] [--out F]`
(exit 1 when a data check fails or a benchmark is above 3x its budget); `tools/perception_audit.py` kind `physics`.

## 4. Deviations from the spec

1. **Lazy law registration.** `physics.register_laws()` registers `impact.LAWS + liquids.LAWS + thermal.LAWS` with
   the lawbook the first time a settle pass has something to do (`settle._ensure_laws`), not at import. Stage A's
   `test_registration_order_and_duplicates` patches the lawbook table and expects it empty in a process that only
   imported the kernel; registration at import would break it. Behaviour is the same (idempotent; same order).
2. **`looks` needs a close look** (`inspect` with `where="closely"`), not every inspect (7.6 "at inspect"). A plain
   inspect stays as before (the eval viewer test pins inspect messages at 600 characters; one grew to 619), and a
   construction judgement is what a close look gives. `feel` follows the spec (after a touch).
3. **A domain's own `feel` wins.** Laundry's inspector (order 120) writes `feel` as dampness ("damp", "dry"); the
   physics inspector (150) only fills `feel` when no domain did.
4. **`dripping` keeps the default inspect tier** (not glance): plumbing's faucet drip is an inspect-tier percept today,
   a kernel-wide glance tier would change plumbing's observations; a kernel drip shows by its wet spot at a glance.
5. **`World.set_quiet`** (field-level quiet journal) in addition to `mut_quiet`: `mut_quiet` copies the whole record
   on first write, which ate the thermal active-set saving; `set_quiet` journals one field. Kernel thermal uses it.
6. **Settle `final` flag**: `settle(env, "action", ctx, final=False)` from `ctx.settle_physics()` keeps the action
   phase (and its attribution) open for the rest of the handler; the engine's own call ends it.
7. **Budget accounting** (PHYS-STATICS follow-up recorded): the 64-consequence budget counts records from the settle
   call's start; the call that crosses it finishes. One collapse that cascades shelf by shelf is one call, so the
   worst-case benchmark records 354 consequences in one pass.
8. **Nested motion**: `perform` (nested leaves) appends each leaf's motion record to the parent's
   `ctx.data["motions"]`; the parent's own `motion_record` is then None, so a composite verb's travel is not counted
   twice (`motion_records` returns the leaves').
9. **Broken support** (core `place`): a collapsed shelf, a support with `damage.collapsed` >= 1, or one broken to
   pieces (`shattered`) refuses; a merely `broken` appliance or device still holds what is put on or in it (domains put
   things into broken machines today).
10. **Setup lint scope**: the lint checks what the setup created or changed (structural signature against the
    generated house) and the supports under it, not the whole generated house (GEN's staging is not the spec's
    responsibility). Old-schema specs get warnings in `bindings["_physics_warnings"]` (underscore: never substituted)
    and the trial record.
11. **Wheels and casters in the wind** (realism observation 2): `contact_mu` gives wheeled things tyre (rubber)
    friction on floors (floor mu x 2, at most 1.0) and lets casters roll at their rolling resistance (lowest 0.005)
    unless chocked, braked or secured. Before, a wheeled bin used its body material's friction and only moved at about
    25 m/s mean wind; now a tall wheeled bin blows over in gusts (a local gust of 11.5 m/s, about 19 m/s at 10 m), a
    probe over a year of generated weather moves a trash cart in 0-0.3 % of daytime hours. **Gust-driven sliding is
    not added**: stage A's `test_a_gust_lifts_or_tips_but_only_a_sustained_wind_slides` (test_physics_fluids.py) and
    `test_light_outdoor_things_stay_put_on_most_daytime_hours` (test_physics_data.py) pin sustained-only sliding.
    Gusts lift sheets and tip tall things; light low things therefore move less often than in gusty reality
    (KNOWN_LIMITS). Changing it is a law change for the main session (section 9).
12. **Ceramic vase from a table onto carpet** (realism observation 1): not changed in the kernel. The vase breaks
    because `manipulation.json` gives `ceramic_vase` `drop_tolerance_m: 0.15` (and `vase` 0.12), which beats the
    ceramic class (0.45), and the carpet cushion takes a fixed 3 J whatever the footprint: 1.2 kg from 0.75 m gives an
    effective 0.19 m (0.08 x 0.25 m caught + 0.35 x 0.50 m). With the class tolerance it survives carpet and breaks
    on tile. The record field and the cushion rule are pinned by other packages' data and tests (section 9).
13. **R7 in a domain test**: `tests/test_textiles_laundry.py::test_unread_jugs_of_every_kind_..._weigh_what_a_full_jug_weighs`
    asserts every full jug under 3.0 kg; the chlorine bleach jug is 3.23 kg at bleach's 1.10 kg/L. R7 is the reviewed
    change; the test's bound is outside PHYS-INT's files (request 1).
14. **Thermal import**: `phys/thermal.py` reaches `kernel.physics` lazily (`_kernel()`), because the facade imports
    the layer at its bottom and an import-time `from .. import physics` was circular.
15. **Physics-audit worst case** times the settle pass only (not the setup of 70 glasses and the rollback) and
    reports its consequence count: 354 consequences in 10.1 ms (budget 10 ms for 64), about 0.03 ms each.
16. **Field tiers beyond 7.6's lists**: `wheeled` is glance (wheels are as visible as casters), and the top-level `torn`
    flag that `impact.tear` writes is glance (PHYS-STATICS' follow-up; the `damage.torn` level stays inspect). The
    glance fields 7.6 names that `DEFAULT_TIERS` already has (`tipped_over`, `toppled`, `overflowing`, `spilled`,
    `shape`) are not repeated in `core.json`.

## 5. Stubs and contracts

* Removed: the stage-A `TIER1-STUB(PHYS-INT)` markers (`impact.emit_consequence` and the `_scratch` fallback,
  `processes._Sibling` / `sibling` and `processes.emit_consequence`). `grep -rn "TIER1-STUB(PHYS-INT" house_sim --include=*.py`
  is empty. No new marker.
* Consumed: PHYS-STATICS' law signature `fn(w, eid, env=None, cmap=None) -> [records]`, consequence list and ledger
  per settle phase, `setdown(w, eid, speed)`, `schedule_creep`; PHYS-FLUIDS' `liquids.carry(w, travel, by, cmap)`,
  `thermal_step`, `environment.run`, `processes.run` and LAWS tables; PHYS-DATA's props, lawbook (laws.json states)
  and `contain.fits`; CAM-SIGHT's `sight.front_deg` (setup `edge`).
* For later packages: BODY-ENGINE fills `ctx.motion(...)` (actions' motion record is the contract), stamps
  `w._power_down_step` and replaces `forces.robot_caps`' stand-in; CAM-RUNTIME reads `actions.motion_records(ctx)`
  for motion frames; TIME-EVAL's monitor samples after the settle pass (engine order of TIER1_INTEGRATION 1.9); the
  laws' flips (stage D1) need no kernel change (`laws.json` state only).

## 6. Gate results

Gate id **PHYS-INT** (`catalog/analysis/tier1/gate/PHYS-INT/`), previous gate B0, recorded on the shared tree
with `record --id PHYS-INT --prev B0 --work <scratch>` (2026-10-14, 7 jobs, about 26 min).

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint 3e6e94c37334 unchanged; 109 systems (B0 107: + `physics.environment`, `physics.processes`) |
| G1 | FAIL | 3,220 tests (normal) in 38 files; the only failing runs are `test_textiles_laundry.py` normal and strict, one test: the R7 jug bound (deviation 13, request 1) |
| G1b | FAIL | the 8 isolated-domain failures known since A0, plus the same R7 jug test isolated; kernel files with `HOUSE_SIM_DOMAINS=core` 12/12 ok (the `kernel_core` requirement, including the two new physics test files) |
| G1c | SKIP | no import-inert modules for this package |
| G2 | PASS | own mode 242/242 specs (1,210 trials), 0 verdict changes, 1,042 state-digest changes, 13 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (as B0), 0 verdict changes, 1,042 state-digest changes, 74 observation-digest changes |
| G4 | PASS | perception audit (now with the physics checks): 0 leaks, 0 crashes |
| G5 | PASS | 240 houses, 0 problems; gen-1 digests 340/340 |
| G6 | PASS | fuzz 1,800 commands, 0 defects, 0 nondeterministic |
| G7 | PASS | 3.00 ms per substep, 1 h wait 1.079 s (budget 1.0 x A0: 4.38 ms, 1.102 s) |
| G8 | PASS | room golden and golden_all ok |
| G9 | PASS | `physics_audit --data --bench`: ok |
| G10 | FAIL | 31 changed files, 30 on the PHYS-INT list; the one outside is `catalog/analysis/tier1/handoff_B0.md`, the main session's B0 handoff, untracked since before this package started (commit it with B0's records) |

`python tools/orchestration/tier1_gate.py compare B0 PHYS-INT --package PHYS-INT`:

```
compare B0 -> PHYS-INT (packages: PHYS-INT; allowed: obs,state,verdict; codes: R1,R10,R2,R3,R4,R5,R6,R7,R8,R9)
G0   PASS  action fingerprint unchanged (3e6e94c37334)
G2   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1042 with changed state digests (allowed), 13 with a changed observation digest (allowed)
G3   PASS  1210 trials vs 1210: 0 verdict change(s) (0 reviewed), 1042 with changed state digests (allowed), 74 with a changed observation digest (allowed)
G5   PASS  layout counts equal (0 problems)
           generation-1 digests vs previous gate: 340/340 equal
G4   INFO  perception audit: leaks 0 -> 0, errors 0 -> 0
G6   INFO  fuzz: defects 0 -> 0, nondeterministic 0 -> 0, commands 1800 -> 1800, succeeded 312 -> 312
G7   PASS  substep_ms: 4.38 -> 3.0 (-31.5%; limit 4.38 = A0 x 1.0)
           wait_1h_s: 1.102 -> 1.0789 (-2.1%; limit 1.102 = A0 x 1.0)
           largest per-system increases: appliances_cooking.tick +0.018 ms
G1   INFO  new test file test_physics_settle.py (normal): 39 tests
           new test file test_physics_settle.py (strict): 39 tests
           new test file test_physics_setup.py (normal): 27 tests
           new test file test_physics_setup.py (strict): 27 tests
           new test file test_room_golden.py (normal): 7 tests
           new test file test_room_golden.py (strict): 7 tests
compare result: PASS
```

### Reviewed differences (3.3)

No verdict changed, so no row is needed to pass. Every state and observation change was classified: the state
changes against the golden (seed 0, all 242 plans), the observation changes by rerunning each changed trial's
reference plan on the committed B0 tree (`git archive HEAD`, read only) and on this tree and diffing the final
observations.

| Code | Catalog ids | Mode | Trials | What changed |
|---|---|---|---|---|
| R1 | (1,042 trials of most specs) | both | state | `_robot_moved: true` on entities the plan's actions changed (golden: 670 paths, the only state pattern besides R7) |
| R7 | T-92f2736b7f949c-01 | both | state, obs on seeds 0, 1, 2, 41 | robot battery (97.8975 -> 97.897 on seed 0): the carried mass counts the liquid at its `liquids.json` density, so navigation's drain changes; both observations show the battery |
| R2 | T-63b5495ec1360b-01, T-c49fadcea69182-01, T-d990c58b123a5e-01, X-bc4e8d373e62c5-01 | forced (and own for the realistic spec T-c49fadcea69182-01) | obs | `two_handed` no longer shown (a hidden threshold) |
| R2 | T-0ccc3cf843ba50-01 | forced | obs | `max_stack_load_kg` no longer shown on 3 records |
| R2 | T-0b4f248ae32c87-01, T-149e27671dec82-01, T-3c114c8e4796ec-01, T-457ec84b96c1ee-01, T-5a9598c809336a-01, T-5d2d79cc6ec2ca-01, T-702453ef553c00-01, T-79a861877a2a67-01, T-b122adaae3586f-01, T-f7f4105ce49060-01, X-404f39e74f6d5f-01 | forced (own for the realistic specs among them) | obs | `wheeled` and `torn: false` now shown at a glance (deviation 16) |
| R2 | A-c627488c575314-01, T-96e4d6cfde7e51-01, T-d182fc681f310d-01, T-fffb3941b40c87-01 | forced | obs | `wheeled` now shown at a glance |
| R2 | T-89530941a65283-01 | forced | obs | `torn: false` now shown at a glance |
| R2 | A-05a6ff57e7f3fd-01 | forced | obs | `tilt_deg` now shown at a glance |

One changed trial per spec was rerun and diffed (all 23 specs of G3, the 4 of G2); no other observation path changed.
The 13 own-mode changes are T-92f2736b7f949c-01 (oracle, R7) and the realistic specs T-5a9598c809336a-01,
T-c49fadcea69182-01 and T-f7f4105ce49060-01 (R2).

Not seen: R3 (`contain.fits` in `place`), R4 (refusals onto collapsed or shattered supports), R5 (0 of 1,210
reference runs cause a physics consequence: `physics_audit --data`), R6 (follows R5) and R10 (not needed: kernel
thermal states are identical). R3 and R4 would show as state paths (a different landing or a refused step); the golden
(seed 0, all 242 plans) has none and no verdict changed, but the 1,042 state changes on the other seeds were not
diffed path by path. R8: replays saved from now on carry `metadata.physics_version`; replaying an older one warns
("recorded with physics data from before kernel v3.0") and runs. R9: the golden was re-recorded (below).

### Golden (3.5)

Recorded on the untouched tree first (`history`: `{"by": "PHYS-INT", "classes": ["initial"]}`), then re-recorded at
hand-over (`{"by": "PHYS-INT", "classes": ["R1", "R7"]}`). `golden.py diff` of the two `dump --all` folders:

```
274 file(s) compared, 209 changed, 2 path pattern(s)
   670  entities.*._robot_moved          (R1)
     1  robot.battery                    specs/T-92f2736b7f949c-01: 97.8975 -> 97.897 (R7)
```

No observation digest of the 8 room runs (seeds 0-3 x oracle / realistic, 12-step scripts) changed.

### Benchmarks against 3.4 and TIER1_PHYSICS 11

| Measure | Budget | PHYS-INT |
|---|---|---|
| G7 substep (bench_systems 40 steps, seeds 0-3, best of 5) | <= A0 4.38 ms | 3.00 ms (runs 3.00-3.02) |
| G7 1 h wait (seeds 0-3, best of 5, summed) | <= A0 1.102 s | 1.079 s (per seed 0.224 / 0.300 / 0.285 / 0.270) |
| kernel `thermal` per substep | <= 0.5 ms | 0.216 ms (A0 1.688, B0 1.673) |
| `physics.environment` / `physics.processes` per substep | 0 calm or deferred / <= 0.02 idle | 0.001 / 0.000 ms |
| settle, substep with nothing structural touched | <= 0.02 ms | 0.0007 ms (random walks: p50 0.068, p99 0.32 ms) |
| settle, action, typical | <= 1 ms | p50 0.004 ms, p99 0.22 ms |
| settle, worst case | <= 10 ms (64 consequences) | 10.1 ms for 354 consequences (warning only) |
| `physics.environment`, windy (law forced on, seeds 0-3 in a gale) | <= 0.3 ms | 0.35-0.38 ms (warning only; known gap) |
| `props()` | <= 5 us | 4.3-4.4 us |
| data load | <= 20 ms | 1.0-1.1 ms |
| kernel thermal step (audit, seed 0 settled) | <= 0.5 ms | 0.042 ms |

The largest per-system increase is `appliances_cooking.tick` +0.018 ms (noise range of B0's table); no other system
moved by more than 0.008 ms.

### Audits

* `tools/physics_audit.py --data`: 0 unmapped materials, every law with a settle order registered, owner gaps per
  domain (bulk density, default mass and size; for the adoption round), 13 domain tier overrides of thresholds (all
  `read` of printed ratings or `inspect` of `two_handed`; listed in the record), row-derived floats 11 / nest groups
  21 / openings 19 (the R3 review list of PHYS-DATA), 1,210 reference runs with 0 physics consequences and 38 with
  lint warnings (Known gaps), 0 errors.
* `tools/perception_audit.py 6 150`: 0 leaks, 0 crashes, with the new `physics` kind (notes, sounds, feel / looks,
  wobbly / sagging).
* Tests: G1 above; the package's own files green normal, strict and core-only.

## 7. Known gaps

* Deferred laws (overload, crush, edge, stack, tip, carry, wind, rain, heat, shock, freeze, ...) are wired but not
  applied automatically until their domains adopt them (stage D1, `laws.json`); perturbation events call the kernel
  functions whatever the law states (TIER1_PHYSICS 10).
* Gust-driven sliding (deviation 11) and the footprint-independent cushion (deviation 12) are documented
  approximations (KNOWN_LIMITS).
* `physics.environment` with the wind law forced on measures 0.35 ms per substep on the busiest seed (budget 0.3;
  the law is deferred, so 0 today). Its outdoor-id fingerprint and children map are O(N) per substep; the law's owner
  should cache them on the touch log before the flip.
* `liquids.json` has no `c_p` column yet (PHYS-FLUIDS follow-up; `thermal.LIQUID_CP` stays in code), and
  `core.mark_contents_from` / `liquids._mark_provenance` are still two implementations of the v2.9 provenance rule.
* `physics_audit --data` lists row-derived floats, nest groups and openings, materials, laws, gaps and tier overrides;
  it does not yet list binding absorption caps or upholstered furniture (PHYS-DATA residual suggestion).
* The setup lint warns on 38 reference trials of 4 specs (old schema, warnings only): houseplants larger than their
  pots and overfull pots in the repotting specs T-7b4f140db3b230-01, T-97f43b147c3c2f-01, T-d8d0ca913007d4-01 (a
  root-bound plant is the point of those tasks: `_phys_staged` or a `"strict": false` check is the fix), and
  flatpack panels that do not fit `flatpack_box_1` in T-108baebbfdf256-01 (box or panel sizes).

## 8. How to test

```
python -m unittest tests.test_physics_settle tests.test_physics_setup tests.test_room_golden
HOUSE_SIM_STRICT=1 python -m unittest tests.test_physics_settle tests.test_physics_setup tests.test_room_golden
HOUSE_SIM_DOMAINS=core python -m unittest tests.test_physics_settle tests.test_physics_setup
HOUSE_SIM_GOLDEN_ALL=1 python -m unittest tests.test_room_golden         # all 242 reference plans
python tools/golden.py check                                             # the golden without unittest
python tools/physics_audit.py --data --bench --out <your scratch>/physics_audit.json   # about 2 min
python tools/perception_audit.py 6 150 --workers 4                       # now with the physics checks
python tools/orchestration/tier1_gate.py compare B0 PHYS-INT --package PHYS-INT
```

Manual checks: put a glass on a shelf, `place` 30 kg of books on the same shelf with the overload law forced
(`lawbook.forced(overload=True)`) and read the step message ("... collapsed", "... fell and broke") and `obs.sounds`;
`inspect` a shelf with `where="closely"` in realistic mode for `looks`; a setup with `{"op": "edge", ...}` and a
`physics.bump` event.

## 9. Follow-ups

Requests to the main session (files outside PHYS-INT):

1. `tests/test_textiles_laundry.py` line 2518: raise the full-jug bound from 3.0 to 3.3 kg (or compute it from the
   jug's liquid at its `liquids.json` density); R7, deviation 13. Until then G1 reports this one failure.
2. `house_sim/data/types/manipulation.json`: drop `drop_tolerance_m` from `vase` (0.12) and `ceramic_vase` (0.15) so
   the material class applies (glass, ceramic 0.45), or calibrate them to the class anchor (survives carpet from
   table height, breaks on tile). Manipulation's own drop rule and tests decide; adoption round.
3. Footprint-scaled cushion energy (a cushion absorbs per contact area) would make heavy, broad things land harder
   and light small ones softer; it needs PHYS-STATICS' pinned test (`tests/test_physics_statics.py` around line 795,
   "carpet cushion: 3 J") updated. Stage D or a physics fix round.
4. Gust-driven sliding of light low objects (deviation 11) needs the two stage-A wind tests above updated; decide with
   outdoor_plants' adoption of the wind law.
5. `tests/test_eval_viewer.py` line 455 compares the viewer's stored messages with `message[:600]`; the viewer marks
   longer messages differently, so any inspect message over 600 characters fails it (CAM-RENDER owns the viewer).

For later packages: BODY-ENGINE (`ctx.motion`, `_power_down_step`, `robot_caps`), CAM-RUNTIME (`motion_records`),
TIME-EVAL (monitor after settle), domain adoption (TIER1_PHYSICS 10.3: guard legacy copies with `lawbook.active`,
route through `ctx.fall` / `physics.*`). The admin_inventory overload hook, outdoor_plants' `_object_wind` and
manipulation's slosh are the legacy copies that interfere when the laws are forced on in a full-domain process (the
settle tests exercise those laws at World level for that reason).

## 10. Stage-B review fix round (STAGE-B-FIX)

Added after the three stage-B reviews (`catalog/analysis/tier1/stageB_review_physics.json`, `stageB_review_camera.json`,
`stageB_review_body.json`: 6 + 15 + 13 findings). The same section is in `handoff_PHYS-INT.md`,
`handoff_CAM-RUNTIME.md`, `handoff_CAM-ACTIONS.md`, `handoff_CAM-RENDER.md`, `handoff_BODY-ENGINE.md`,
`handoff_BODY-CORE.md` and `handoff_BODY-FAULTS.md`. Read before starting: TIER1_INTEGRATION (1 with 1.9, 2.4, 3, 5, 6),
TIER1_DECISIONS, TIER1_PHYSICS / TIER1_CAMERA / TIER1_EMBODIMENT where the findings point, and every `handoff_*.md`.
Every finding was reproduced with the reviewer's probe on the B5-merge tree first. Every fix has a regression test that
fails on a copy of that tree and passes now. Only stage-B package files (2.4) and these handoffs were edited.

### 10.1 Findings

Status: **fixed** (root cause fixed, regression test), **still open** (needs a file outside every stage-B package;
the exact change is in 10.5). No finding was rejected: all 34 reproduced.

| # | Finding (review, severity) | Status | What changed (file) | Regression test |
|---|---|---|---|---|
| P1 | Exogenous events charged to the robot for touching any sibling under the same furniture, or for only opening a cabinet (physics, high) | fixed | `impact.robot_caused(w, eid, cmap=None, load=())`: the robot's when the action phase runs, or when the marker is on the item itself or a support in its location chain; for support failures (collapse, creep, pull-out, tear, crush, stack, overbalanced furniture) also on the load that failed (`load=`). Siblings never count. `settle._mark` writes `_robot_moved` only for arranged changes (`settle._arranged`: anything but `open`). Docs: PHYSICS.md, CONVENTIONS.md, AUTHORING_GUIDE.md, events.py | `test_physics_settle`: `test_a_scripted_knock_of_a_centrepiece_is_exogenous_although_the_robot_set_the_table`, `test_opening_a_cabinet_does_not_make_a_scripted_drop_inside_it_the_robots` (with the robot-placed control), `test_a_support_failure_is_the_robots_only_when_its_own_load_is` |
| P2 | One fast set-down counted twice (manipulation's chip plus the kernel break) (physics, medium) | fixed | `data/physics/laws.json`: `setdown` deferred, `duplicated_by` manipulation and food (TIER1_PHYSICS 10.1: only laws no domain duplicates are active; the 10.2 table missed manipulation's `after_placement` chip). PHYSICS.md law table, KERNEL_API.md | `test_physics_settle`: `test_the_set_down_law_is_deferred_so_one_fast_set_down_counts_once` (fragile egg on stone: no break, damage at most once, manipulation's chip when loaded); the two set-down tests force the law on |
| P3 | Eggs the set-down law broke stayed intact for food (physics, medium) | fixed | the same deferral: no agent action breaks an egg through the kernel until food registers `phys.broke` (food is in `duplicated_by`) | the P2 test |
| P4 | A staged "about to give way" shelf showed no sagging cue (physics, medium) | fixed | `impact.sag_cue(w, sid, env=None, cmap=None)` (the overload law's cue without a collapse); `setup.stage_cues` after all ops (either op order); settle refreshes it for `_phys_staged` supports while the overload law is deferred; `creep_fire` refreshes it after its check. AUTHORING_GUIDE.md | `test_physics_setup`: `test_a_staged_shelf_sags_at_a_glance_before_its_creep_time_and_stops_once_unloaded` (realistic glance, both op orders, unload clears it, no collapse) |
| P5 | One collapse heard as 39 sounds (physics, low) | fixed | `observe.heard_sounds`: one entry per sound kind and direction, the loudest word, the first source from the robot's room, `count` "several" (2-4) / "many" (5+); `perception_audit` accepts and checks `count` | `test_physics_settle`: `test_a_cascade_is_heard_as_one_crash_and_one_clatter_not_one_sound_per_item` |
| P6 | `place ... orientation=upside_down` kept an open mug full (physics, low) | fixed | `liquids.orientation_spill(w, eid, by="robot")` (the open-spill rule of an orientation, independent of the deferred law, which stays deferred for the domains' orientation verbs); core `place` calls it when an orientation is given | `test_physics_setup`: `test_placing_an_open_mug_upside_down_pours_it_out` |
| C1 | An open door leaf was a solid 2 m block over its swept area: a robot in the doorway saw almost nothing (camera, high) | fixed | `sight._open_leaves`: a 4 cm leaf at 90 degrees at its hinge jamb (the jamb opposite door_swing's swept diagonal; at a wall end, the corner jamb), tested exactly on every ray (`RoomGeom.leaves` in `_transmission`, `los` boxes, the lidar); door_swing's cells stay the navigation rule. Reviewer probe p8 over seeds 0-5: all 72 door / room pairs now within 15 % of the closed-door coverage (were down to 2-15 %); A-c83efc7adcd8f5-01 seed 2 gets past `approach door_3` | `test_camera.DoorLeafTests` (only what is behind the leaf is hidden; coverage from the doorway; corner leaf flat on the side wall; lidar) |
| C2 | `looked_in` granted for a grazing look over a box rim (camera, high) | fixed | `camera._looked_in` judges the interior: `sight.interior_points` (a top opening's inner floor on a 3 x 3 grid; a front opening's back wall above every shelf band) through the opening (`sight.interior_transmission`), at least half of every group in frame and line of sight (`LOOKED_IN_SHARE` 0.5); px and light rules unchanged. KERNEL_API.md, AUTHORING_GUIDE.md | `test_camera`: `test_a_box_counts_as_looked_into_only_when_its_inside_is_seen` (0.75 / 1.25 / 4.5 m: no; 0.25 m: yes; a cabinet's lower shelf needs a crouch) |
| C3 | Geometric `inspect` listed unseen things under / behind the target (camera, high) | fixed | core `inspect`: `contents` only what a frame of this step framed (`camera.in_frame`), the where= peek saw, or an earlier look revealed under / behind | `test_camera_actions`: `test_inspect_lists_only_contents_a_frame_saw` (cushion coin, kneeling bed, oracle and realistic: no handle issued) |
| C4 | Inspecting a backlit / far person returned name, role, activity and mood (camera, high) | fixed | core `inspect` (geometric, realistic): `camera.agent_identified(env, aid)` (level identify: 64 px, light 0.2, not backlit) else the shape note `camera.WHO_TEXT`, no facts and no inspection record. The review also suggested 160 px for activity and cues; not applied, because the observation already shows both at identify level (a shape answer there contradicted it; close-range details stay the domain inspectors' own, e.g. safety's responsiveness and breathing within 2 cells). KERNEL_API.md | `test_camera_realism`: `test_inspecting_a_backlit_person_tells_nothing_about_who_it_is`, `test_a_person_the_camera_identifies_is_read_by_inspect_as_the_observation_shows_her` |
| C5 | A two-hand carried load never occluded the head camera (camera, high) | fixed | `sight.carried_view` / `carried_blocks`; `sight._occluders` adds the load for head-camera Viewers at the current eye (seen floor cells, frame-level visibility); `camera._carried_filter` for the step view, `in_frame` and `_sees`. `can_see` (any-direction, the robot can turn) is unchanged | `test_camera`: `test_a_carried_basket_hides_the_floor_ahead_from_the_head_camera` (toys at 1.0 / 1.5 m hidden, 3 m listed, coverage drops) |
| C6 | Wall fixtures on south / east walls floated 0.4-0.47 m out (camera, medium) | fixed | `sight._wall_mounted`, `sight._wall_snap` in `_place_new` (flush to the wall the footprint touches; ceiling fixtures excluded) | `test_camera`: `test_wall_mounted_fixtures_sit_flush_on_their_wall` (every wall-mounted perimeter fixture of seeds 0-3) |
| C7 | The wrist camera worked with both arms failed (camera, medium) | fixed | `camera.wrist_access`: `body.arm_problem(state, None)` refuses (held things excepted); read / inspect fall back to the head | `test_camera_actions`: `test_the_wrist_camera_needs_a_working_arm` |
| C8 | Geometric privacy violations counted twice while the people domain is loaded (camera, medium) | fixed | `camera._log_capture`: the kernel fallback counts only when the people domain is not loaded (`_people_domain_loaded`; TIER1_DECISIONS "people domain not loaded"); the capture is still logged. KERNEL_API.md | `test_camera`: `test_with_the_people_domain_loaded_its_own_count_stands_in_both_camera_modes`; the fallback tests patch the domain out |
| C9 | `camera.watch` 0.10-0.13 ms per substep with people (camera, low) | still open | improved, not within budget: `camera._watch_signature` memo (exact: geometry epoch, robot pose and sensors, the room's agents, ambient light, haze; off while a light / occluder hook is registered) reuses the last substep's answers; captures are computed only when someone is private. Lab scene 0.083 -> 0.026 ms; generated houses seeds 0 / 1 / 3 0.14-0.17 -> 0.078-0.090 ms (budget 0.05): the remaining cost is the geometry-epoch judgement of the substep's touches and the ambient light, both needed every substep | `test_camera`: `test_the_watch_memo_never_changes_the_log` (memo on vs off identical) |
| C10 | Head tracking turned an e-stopped robot's body (camera, low) | fixed | `camera._track_pose` and `gaze_to` use `can_turn` (with `stopped`) | `test_camera`: `test_tracking_never_turns_the_body_of_a_stopped_robot` |
| C11 | Sightings only at end_step (first_s at the end of a wait, in-wait sightings lost) (camera, low) | fixed | `camera.watch` notes sightings per substep (`_note_sighting`, first_s at the substep's start); `camera.note_start(env)` logs the reset view (engine `reset`, geometric only) | `test_camera`: `test_sightings_follow_the_watch_and_the_reset_view` |
| C12 | `robot_in_view_s` credited an interval ahead (camera, low) | fixed | `_watch_specs` integrates elapsed time since the previous evaluation when the robot was in frame then (`robot_seen`) | `test_camera`: `test_view_watch_specs_report_what_a_viewer_sees` (90 s: 60 s, not 90) |
| C13 | Naming an unidentified person in a lit room gave the darkness text (camera, low) | fixed | `realism.FAR_WHO` in a lit room | the C4 test |
| C14 | `peek`'s time grew by the head's auto-gaze (camera, low) | fixed | `Ctx.obj(..., gaze=True)`; `core.wrist_peek` and `inspect` with where under / behind / inside pass `gaze=False` (the wrist looks; the head stays put) | `test_camera_actions`: `test_peek_takes_its_documented_time_whichever_way_the_head_looks` |
| C15 | Three camera test pins (camera, low) | fixed | `test_camera.py` (CAM-RENDER request 1), `test_camera_actions.py` (BODY-ENGINE request 3, BODY-CORE request 1); also `test_body_engine.py` (BODY-FAULTS request 1: the powered-down probe, `load_domains()` at import) | the patched tests |
| B1 | The safety net applied lift-off derates to held loads: crossing 5 % or stepping onto a stool dropped and broke a 6.5 kg pot (body, high) | fixed | `body.hold_capacity_kg` (no limp, aid, overhead or above-head derate; hooks still apply) in `_hold_check`; `_derate(..., hold=)` | `test_body_core`: `test_a_held_load_is_not_dropped_by_limp_mode_or_a_climbing_aid` |
| B2 | `runtime_min_est` / `range_m_est` weighed a held item to 0.2 kg (body, high) | fixed | `proprio.estimates(state, level=None)`: empty-handed nominal powers; `perception_audit` checks the estimates carry no load term. PERCEPTION.md | `test_body_perception`: `test_the_battery_estimates_do_not_weigh_what_the_robot_holds` |
| B3 | Domain code doing `sorted(set(robot.faults))` crashes on the dict fault log (body, high) | still open | domain files only (10.5) | - |
| B4 | Domain verbs relocate the robot without ctx.motion: a fallen / legs-failed / unlocalized / wheeled robot guided someone upstairs for free (body, high) | fixed (kernel backstop) | `body.unrecorded_motion(ctx)` after every leaf's AFTER hooks (engine `step` and `perform`): a move without a motion record becomes a synthesized record (straight line, the doorway, the stairs; `"synthesized": True`) paid on top of the action's own activity power, and a body that could not walk refuses (`mobile_problem` with the fallen state at the action's start). `Ctx._robot_down`, `_robot_aid`. The domains still need their own routes (10.5) | `test_body_engine.UnrecordedMotionTests` (energy, stairs, five refusals, rolled back) |
| B5 | `lift_plan` took a known weak right hand (body, medium) | fixed | `forces.lift_plan`: believed capacities (`believed=True`) decide two hands and pick the free hand that can take the mass, right first (also BODY-CORE request 4); core `receive` / `uninstall` take the plan's hand when none is named | `test_body_core`: `test_a_known_weak_arm_hands_the_lift_to_the_arm_that_can_take_it` |
| B6 | Gripper slips drawn per leaf, not per metre (body, medium) | fixed | `body_faults.slip_probability(state, meters)`: 1 - (1 - p)^(m / ref), `fault_p.gripper_slip_ref_m` 10 m (profiles.json); `slip_check(..., meters=None)` (None keeps the per-draw probability) | `test_body_faults`: `test_the_slip_hazard_is_per_metre_not_per_walk` (one 5 m walk vs ten 0.5 m: 6 / 40 and 6 / 40; was 9 vs 38) |
| B7 | Careful speed stretched commanded durations (body, medium) | still open | kernel part landed: ActionSpec `timing="commanded"` (actions.TIMINGS), skipped by `body.careful_seconds`; KERNEL_API.md. The domain verbs must declare it (10.5) | `test_body_core`: `test_careful_speed_does_not_stretch_a_commanded_duration` |
| B8 | The wheeled base climbed onto stools (body, medium) | fixed | profiles.json `climb` (false for mobile_manipulator); `body.climb_problem`, `NO_CLIMB_TEXT`; `unrecorded_motion` refuses a leaf that put a non-climbing body on an aid | `test_body_engine`: `test_a_wheeled_base_cannot_climb_onto_an_aid` |
| B9 | `tools/body_audit.py` models the lift with its own hand rule (body, medium) | still open | stage-A tool, no stage-B owner (10.5) | - |
| B10 | `power_down` set a fallen or sitting robot to crouching (body, low) | fixed | `body.power_down` lowers only a standing posture | `test_body_faults`: `test_a_power_down_lowers_the_posture_only_from_standing` |
| B11 | A power-down on an aid left it occupied and the robot in its cell (body, low) | fixed | `body_faults.fall` (`_off_the_aid`): frees the aid, lands beside it (the cell it was climbed from, else the nearest free cell) | `test_body_faults`: `test_a_power_down_on_a_climbing_aid_frees_it_and_lands_beside_it` |
| B12 | Safety-net and slip drops fell from the standing hand height (body, low) | fixed | `body.hand_drop_m(state)` (hand height x posture eye ratio + aid height) in `_hold_check`, `slip_check`, the arm-failure drop | `test_body_core`: `test_a_dropped_load_falls_from_where_the_hands_are` |
| B13 | Safety stops marked heard by a deaf robot (body, low) | still open | domain file (10.5) | - |

### 10.2 Files

All LF. Owners from TIER1_INTEGRATION 2.4 (stage B columns).

* Physics (PHYS-INT): `house_sim/kernel/phys/impact.py`, `settle.py`, `events.py` (docstring), `observe.py`,
  `liquids.py`, `forces.py` (PHYS-INT / BODY-ENGINE), `house_sim/data/physics/laws.json`, `house_sim/tasks/setup.py`,
  `tools/perception_audit.py`, `docs/PHYSICS.md`, `docs/CONVENTIONS.md`.
* Camera (CAM-RUNTIME, CAM-ACTIONS, CAM-RENDER): `house_sim/kernel/sight.py`, `camera.py`, `realism.py`,
  `house_sim/systems/core.py` (`inspect`, `place`, `wrist_peek`), `house_sim/kernel/actions.py` (`Ctx.obj` gaze),
  `docs/KERNEL_API.md`, `docs/AUTHORING_GUIDE.md`, `docs/KNOWN_LIMITS.md`.
* Body (BODY-ENGINE, BODY-CORE, BODY-FAULTS): `house_sim/kernel/body.py`, `body_faults.py`, `proprio.py`,
  `engine.py` (`reset`, `step`, `perform`), `actions.py` (`TIMINGS`, `BodyView`, `Ctx` start state),
  `house_sim/data/robot/profiles.json`, `house_sim/systems/core.py` (`receive`, `uninstall`), `docs/PERCEPTION.md`.
* Tests: `tests/test_physics_settle.py`, `test_physics_setup.py`, `test_camera.py`, `test_camera_actions.py`,
  `test_camera_realism.py`, `test_body_engine.py`, `test_body_core.py`, `test_body_faults.py`,
  `test_body_perception.py`; `tests/data/room_golden.json` (re-recorded, 10.4).
* These seven handoffs (this section).

### 10.3 APIs added or changed

* `impact.robot_caused(w, eid, cmap=None, load=())`: the attribution rule of P1 (also `impact._by(..., load=)`).
  `settle` writes `_robot_moved` only for an arranged change (`_arranged`: any signature field except `open`).
* `impact.sag_cue(w, sid, env=None, cmap=None)`: writes or clears `sagging` / `_phys_sag` from `statics.overload`
  for one support. `setup.stage_cues(ctx)` runs it for every staged support after the ops.
* `observe.heard_sounds(state, step=None)`: one entry per (kind, direction); an entry merged from two or more sources
  has `count` (`COUNT_WORDS`: "several", "many"). The audit knows the key.
* `liquids.orientation_spill(w, eid, by="robot")`: pours an open container out for an orientation that spills.
* `forces.lift_plan(w, eid, hands=None, helpers=())`: decides with the believed capacities and orders the free hands
  so that one able to take the mass comes first (right first).
* `laws.json` `setdown`: `"deferred"`, `duplicated_by` manipulation and food.
* `sight`: `RoomGeom.leaves`; `interior_points(state, info)`, `interior_transmission(state, geom, vs, eye, info, p)`;
  `carried_view(state, cmap=None)`, `carried_blocks(view, ex, ey, ez, px, py, pz)`; constants `LEAF_T_M`,
  `INTERIOR_INSET_M`, `CEILING_MOUNT_M`.
* `camera`: `LOOKED_IN_SHARE`, `WHO_TEXT`, `agent_identified(env, aid)`, `note_start(env)`; `wrist_access` refuses
  with the arm problem; `watch` memo (`_WATCHED`, `_watch_signature`).
* `realism.FAR_WHO`.
* `actions.Ctx.obj(eid, touch=False, grasp=False, reach=True, action=None, mutable=False, gaze=True)`;
  `actions.TIMINGS` adds `"commanded"`; `BodyView` answers `hold_capacity_kg` and `hand_drop_m`.
* `body`: `hold_capacity_kg(state, hand)`, `hand_drop_m(state)`, `climb_problem(state)`, `NO_CLIMB_TEXT`,
  `unrecorded_motion(ctx)` (engine `step` and `perform` call it after the AFTER hooks, before `note_action`);
  `power_down` lowers only a standing posture; `careful_seconds` skips `timing="commanded"`.
* `body_faults`: `SLIP_REF_M`, `slip_probability(state, meters=None)`, `slip_check(w, hand, ctx, meters=None)`;
  `fall` frees a climbing aid.
* `proprio.estimates(state, level=None)`: the battery estimates the observation shows, without a load term.
* `profiles.json`: `climb` (humanoid true, mobile manipulator false); `fault_p.gripper_slip_ref_m` 10.

### 10.4 Gate (STAGE-B-FIX)

The record is in `catalog/analysis/tier1/gate/STAGE-B-FIX/`, taken on an isolated copy so that G10 sees only this
round's changes: the base commit is the tree the B5 merge gate handed over (the project's uncommitted tree with this
round's files taken from the copy made before the first edit), and the working tree is the project's current files
(CRLF checkouts as LF). Its `git status` lists exactly this round's 34 code, test, golden and doc files. After this
section was written, `docs/KNOWN_LIMITS.md`, the last `docs/AUTHORING_GUIDE.md` edits and the seven handoffs
were copied in and G10 was refreshed with `--only G10`. The geometric preview below then led to one refinement of C4
(`camera.agent_identified` takes the identify level alone, with a new test); G1 / G1b / G8 and G4 / G6 / G10 were
re-run on it (`--only`), and the numbers below are those runs. The code the gate ran is the project's.

```
python tools/orchestration/tier1_gate.py --root <iso> --gates-root catalog/analysis/tier1/gate record --id STAGE-B-FIX
       --package PHYS-INT+CAM-RUNTIME+CAM-ACTIONS+CAM-RENDER+BODY-ENGINE+NAV-M2+BODY-CORE+NAV-M5+BODY-FAULTS
       --prev catalog/analysis/tier1/gate/B5-MERGE --extras physics,camera,body --out catalog/analysis/tier1/gate/STAGE-B-FIX
       --work <scratch>/gate_work --jobs 7
```

Summary line: `TIER1-GATE STAGE-B-FIX [PHYS-INT+...+BODY-FAULTS] vs B5-MERGE: FAIL (9 pass, 3 fail, 1 skip)`. The G1 and
G1b failures are tests in files outside every stage-B package, each with a request (10.5 item 1); G7 is 0.1-1 % over a
fixed bound that the B5 tree also misses on this machine today (below). `compare` passes everything but G7.

| Check | Status | Note |
|---|---|---|
| G0 | PASS | action fingerprint `482f9adf9717`, unchanged (425 actions, 111 systems) |
| G1 | FAIL | 45 files, 3,575 tests, normal and strict. 12 failing runs: 7 tests in 6 files, the same in both modes, all outside the stage-B packages: `test_body_model.py` (2), `test_physics_forces.py`, `test_manipulation.py` `HandOverTests` (known from B5-MERGE, BODY-ENGINE requests 1-2, BODY-CORE request 2), `test_textiles_laundry.py` (PHYS-INT request 1), and two new by design: `test_physics_statics.py` (P1) and `test_physics_data.py` (P2). With the six patches of 10.5 applied to a copy of this tree, all six files pass in normal, strict and core-only runs. `test_body_engine.py`, `test_camera.py` and `test_camera_actions.py`, failing at B5-MERGE, now pass (C15). All the fix round's test files pass: `test_physics_settle` 44, `test_physics_setup` 29, `test_camera` 90, `test_camera_actions` 58, `test_camera_realism` 50, `test_body_engine` 50, `test_body_core` 39, `test_body_faults` 14, `test_body_perception` 24 |
| G1b | FAIL | isolated domains: 10 failing tests in 7 files, all known from B5-MERGE; kernel files with core only 15/19 (B5-MERGE 14/19): `test_body_model`, `test_physics_data`, `test_physics_forces`, `test_physics_statics`, all covered by the 10.5 patches |
| G1c | SKIP | no import-inert modules for these packages |
| G2 | PASS | own mode 242/242 (1,210 trials); against B5-MERGE 0 verdict changes, 626 state-digest changes, 48 observation-digest changes |
| G3 | PASS | forced realistic 226/242 (as B5-MERGE); 0 verdict changes; 625 / 243 digest changes; the 250 realistic trials are identical in G2 and G3 |
| G4 | PASS | perception audit, room and geometric: 0 leaks, 0 crashes (the audit now also checks `count` on sounds and that the battery estimates carry no load) |
| G5 | PASS | 240 houses, 0 problems, counts equal; generation-1 digests 340/340 |
| G6 | PASS | fuzz: battery, geometric and room, 1,800 commands each, 0 defects, 0 nondeterministic |
| G7 | FAIL | 3.07 ms per substep (B5-MERGE 3.10), 1 h wait 1.103 s; run again alone, 3.12 ms and 1.114 s. Because PHYS-INT is in the union, the budget is A0 x 1.0 (1.102 s), not the previous gate x 1.05 that B5-MERGE used (it measured 1.098). See below |
| G8 | PASS | room golden: golden ok, golden_all ok (re-recorded, below) |
| G9 | PASS | physics_audit ok; camera tools ok in G4 / G6; body_audit ok |
| G10 | PASS | 34 changed files in the full run; after the docs and handoffs were copied in (`--only G10`), 42: the 34, `docs/KNOWN_LIMITS.md` and the seven handoffs; 0 outside the union's file list |

**G7.** Interleaved A/B on this machine, one process per measurement, `PYTHONHASHSEED=0`, 10 rounds, best of 3 per seed,
the same four houses. The tree before this round has a median of 1.109 s (min 1.092). The fixed tree has a median of
1.112 s (min 1.098). That is +0.3 % at the median, and both trees are above 1.102 at the median. A cProfile of the four
waits gives equal total own time (4.43 s fixed, 4.44 s before). No function this round added or changed is among the
increases; `settle._arranged`, the only new code on the wait path, takes 0.3 ms over the four waits. Per-system substep
costs are within 0.01 ms of B5-MERGE (largest: `climate.house` +0.008). So this is noise around a fixed bound. A G7 run
on a quiet machine (`--only G7`) decides; nothing in this round's code is left to trim on that path.

**Reviewed differences (3.3).** No verdict changed in G2 or G3. The digest changes are of kinds the union's codes
already allow:
* state: R1 (no `_robot_moved` marker for an open / close-only change), R5 (the laws active at landing, now without
  set-down), B-R2 (battery and energy of trials whose domain verbs move the robot without a route);
* observations: the merged sounds with `count` (P5), the load-free battery estimates (B2), `FAR_WHO` (C13).

**Golden (3.5).** Re-recorded with `{"by": "STAGE-B-FIX", "classes": ["R1", "B-R2"]}`. `tools/golden.py diff` of the
dumps before and after: 274 files compared, 127 changed, 5 path patterns:

* 208 `entities.*._robot_moved` removals: open / close-only touches in the script and the reference plans.
* Two trials whose leaves move the robot without a motion record now pay for the walk (`robot.battery`,
  `battery_min_pct`, `energy_wh_used`, `stats.energy`):
  * T-fa2552570f7b6f-01 (`mow`): 22.22 -> 25.94 Wh.
  * X-1742e6de23d6be-01 (`guide`): 3.84 -> 5.27 Wh.

Nothing else changed.

**Geometric preview (information only, as at B3).** `record --only G3 --camera geometric` (scratch records), all 242
specs with realistic perception and the geometric camera. The tree before this round passes 173/242, this tree 178/242,
with 0 exceptions in 1,210 trials each. 8 specs now pass: A-c0f8c53da99d24-01, T-33ad442208b4b7-01, T-33e06799f951df-01,
T-8181b198c09f12-01, T-89d7a649052266-01, T-92f2736b7f949c-01, X-9cb1ee87e09363-01, X-c77e2cbe7181f0-01.
A-c83efc7adcd8f5-01 (CAM-RENDER's example) now gets past `approach door_3` on seeds 2, 41 and 97 and stops later, at a
look-alike pill bottle. 3 specs now fail, each because the robot now sees what the solid leaf used to hide:
* T-28ff695081cd2c-01, seed 1: from the doorway it now sees the laundry basket and the bucket, so `approach` with
  `avoid` finds no path. It fails the same way with the room camera (forced realistic, since B5-MERGE).
* T-702453ef553c00-01, seed 0: the resident 0.5 m in front of the robot is now seen, backlit by the window
  (`sight.glare`), so not identified, and `ask resident_1` is refused with `FAR_WHO`. Before, she was not seen at all,
  and an unseen person can be named (10.5 item 9).
* T-7ef5816c160945-01, seeds 0 and 2: behind the door the robot opens, the cat is lit at 0.135 (identify needs 0.2), so it is
  recognised, not identified, and `inspect` gives the shape answer (C4).

### 10.5 Still open, and requests to the main session

Each needs a file no stage-B package owns. The patches for the six test files were applied to a copy of the fixed tree
(`request_patches.py` in this round's scratch); that copy is green in normal, strict and core-only runs.

1. **Test pins outside the packages.** These tests pin behaviour that a stage-B package changed on purpose; each
   needs the patch below in its own file.
   * `tests/test_physics_data.py` (PHYS-DATA): `ACTIVE = {"orphan", "overflow", "drip", "creep"}`; `setdown` is
     deferred (P2).
   * `tests/test_physics_statics.py` (PHYS-STATICS): replace
     `test_robot_moved_anywhere_under_the_top_support_counts` with the P1 rule: a sibling the robot moved under the
     same table does not make the glass the robot's; the table's failure under that book (`load=["book"]`) does; a
     marker on the table itself does.
   * `tests/test_body_model.py` (BODY-MODEL): BODY-ENGINE request 1 (the legacy robot record keeps only
     `ROBOT_DEFAULTS`, room, pos, hands and the config's keys).
   * `tests/test_physics_forces.py` (PHYS-DATA): BODY-ENGINE request 2 (the profile is the only source of the force
     ratings).
   * `tests/test_manipulation.py` (manipulation): BODY-CORE request 2 (`receive` of a two-hand bag with a hand full is
     refused at lift-off, "Both hands must be free for this item", the bag stays with Ana).
   * `tests/test_textiles_laundry.py` (textiles_laundry): PHYS-INT request 1 (the jug bound 3.3 kg: bleach at
     1.10 kg/L).
2. **B3, fault writes in domain code.** The fault log is a list of dict records (BODY-ENGINE); a domain that sorts it
   as strings crashes, one that appends a bare string writes a record nothing reads. Replace each with
   `body_faults.add_fault(w, kind, part=..., source="event")` (register the kind with `register_fault_kind` if it is
   new) or `body_faults.log_fault(w, kind, text)`:
   `systems/appliances_cooking/handling.py:73` (`gripper_heat_damage`), `systems/cleaning/dishes.py:73`
   (`gripper_cut`), `systems/manipulation/handling.py:750` (`gripper_damage`) (the three `sorted(set(...))` crashes),
   `systems/food/common.py:162-165` (`end_effector_cut`), `systems/outdoor_plants/pool.py:63-65` (`water_ingress`),
   `systems/outdoor_plants/yard.py:641-643` (`electrical_shock`) (bare strings).
3. **B4, domain verbs that move the robot without a route.** The kernel backstop (`body.unrecorded_motion`) now pays
   and refuses these moves, but as a straight line, without traverse's bookkeeping (`docked`) or camera motion
   frames. The verbs the review found should call `ctx.require_mobile(stairs=...)` and `ctx.motion(path, start,
   connection=cid)` per leg (TIER1_INTEGRATION section 6, a new body item): people `guide` and `offer_arm` walking
   (`people/assist.py`), manipulation support `mode=follow` (`jobs.py`), `drag`, `roll` and `_robot_follow`
   (`motion.py`), outdoor_plants `mow` (`lawn.py`), textiles_laundry unfold step-back (`dry.py`), navigation_robot
   `dock` / `undock` (`robot.py`; NAV-M2's file, but the change belongs with the others).
4. **B7, careful speed on commanded durations.** The kernel half is in (`timing="commanded"`). These domain verbs take
   their duration from an argument and must declare it in their ActionSpec: `hold_steady`, `offer_arm`, `play_game`,
   `knead`, `dry_pet`, `brush_pet`, `blend`, `brew`, `grind`, `rinse`, `spray`, `shake`, `inflate`, `flush`,
   `ignite`, `pair_device`, `pull_handle`, `push_handle`, `squeeze_trigger`, `tilt`, `turn`, `move_lever`,
   `restart_device`.
5. **B9, `tools/body_audit.py`.** A stage-A tool with no stage-B owner. It should take the hands from
   `forces.lift_plan` and the capacity from `body.capacity_kg(..., believed=...)` instead of its own hand rule, and
   report overload refusals, drops and energy per task.
6. **B13, safety stops heard by a deaf robot.** `systems/safety/monitor.py:139` sets `heard_by_robot` from the room
   and `loud` only; it should be `body.hears(state, room, loud=bool(args.get("loud", True)), addressed=True)`.
7. **C9, `camera.watch` cost.** Still 0.078-0.090 ms per substep in generated houses with people (budget 0.05). What
   remains: deciding whether the substep's touches changed the room geometry (`sight` epoch) and the ambient light.
   A geometry epoch that settle bumps only when something in a room moves, and a light epoch bumped by whatever
   changes a lamp or the sun, would let the memo skip both. That is a cross-package change (settle, the light hooks,
   the camera) and was not attempted in a fix round.
8. **Smaller notes.** C3: `inspect` no longer lists unseen things, but `camera.can_see` still accepts
   `wrist_access` for a thing under a light portable support or under furniture within reach (CAM-ACTIONS known gap
   1), so `pick_up` of such a thing by its oracle id works without a peek. Restricting `wrist_access` to revealed
   things broke the fetch skill (its peek frame does not cover side items), so it was left. P3: food still has to
   register `phys.broke` before the set-down law can be activated. Two design docs still describe the old rules:
   TIER1_PHYSICS 6.4 (a marker anywhere in the subtree of the top-level support counts) and the TIER1_EMBODIMENT 4.11
   fault table (one slip draw after each motion carrying 1 kg or more). The design docs are the main session's.
9. **For the camera owner (D3 triage), seen in the geometric preview of 10.4.** Two rules of other packages now
   decide specs that the blind doorway used to hide:
   * `sight.glare` adds the target's own angular radius to the backlight overlap, so a person 0.5 m from the robot is
     "backlit" by a window well off the line of sight and cannot be identified (T-702453ef553c00-01, seed 0).
   * `realism.inbound` refuses a person's id only while they are seen and unidentified; one the camera does not see at
     all can still be named.

### 10.6 How to test

```
python -m unittest tests.test_physics_settle tests.test_physics_setup tests.test_camera tests.test_camera_actions tests.test_camera_realism
python -m unittest tests.test_body_engine tests.test_body_core tests.test_body_faults tests.test_body_perception
HOUSE_SIM_STRICT=1 python -m unittest <the same>
HOUSE_SIM_DOMAINS=core python -m unittest <the same>
python -m unittest tests.test_room_golden; HOUSE_SIM_GOLDEN_ALL=1 python -m unittest tests.test_room_golden
python tools/perception_audit.py 6 150 --workers 4 --battery 5 --faults random [--camera geometric]
python tools/orchestration/tier1_gate.py compare B5-MERGE STAGE-B-FIX --package <packages>
```

The reviewers' probes are the quickest way to see each fix: the door / room coverage table of C1 (seeds 0-5, open vs
closed), the box rim of C2, the cushion coin of C3, the basket of C5, the 6.5 kg pot on 5 % and on a stool (B1), the
guide upstairs with the legs failed (B4).
