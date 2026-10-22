# house_ai independent review: Phase 3 (Perception honesty), side-channel lens

Reviewer: Claude (Opus 5.5), subagent "p3_leaks_side". Date: 2026-10-20, about 22:00-23:30 local (about 1 h 30 min).
Code reviewed: the frozen copy of commit `bc0417b` at `scratchpad/review/tree` (no `.git`). Python 3.14 (only version
installed). The live repository `<home>\OneDrive\Desktop\house_ai` was not touched. Every script and output is
in `scratchpad/review/out/p3_leaks_side_scratch/` (below: `S/`).

Evidence labels: **[ran]** I executed it and saw the result; **[read]** I read code or docs but did not run it;
**[inferred]** reasoning only.

Hard rules kept: no network, no paid API call, no LLM agent run of any kind; nothing under `%USERPROFILE%\.house_ai\`
was read, listed, created or printed (I read the test-seed derivation code in `house_sim/eval/splits.py` only; no
split function was called); no repository edit. Worker count 3.

---

## Status update (end of Phase 3, side-channel lens)

### Simple version

I wrote my own "cheating robot" programs that are only allowed to do what a real evaluated robot can do: look,
move, pick things up, wait, and use what it is told. I tried to make them learn things they are not supposed to know.
They succeeded in several ways the project's own leak checker cannot see. A robot can ask the simulator precise
yes/no questions through its "wait until" command and get back exact hidden numbers, or whether a person lying down is
asleep, in 1 to 20 steps. With the newer camera mode it can tell which look-alike bottle is bleach just from its size
in pixels. It can also weigh what it holds by timing its battery readout. On the public practice seeds, a program with
a copy of the simulator can rebuild the whole hidden house and decode every object. The checker still reports
"0 leaks" on the same code, which shows what that number really means: the checker found nothing of the kinds it looks
for, not that nothing leaks. Several other attack routes I tried were properly closed. Nothing is blocking me.

### Advanced version

* Phase 3 side-channel lens, about 1 h 30 min elapsed.
* Commands and results: section 2. Findings ranked: section 3. Null results: section 4. Auditor blind spots: section 5.
* Findings: **L1 High** `wait(until)` / `watch` evaluate the true value and accept ops `startswith`, `in`,
  `is_true`... → exact glance-tier values (wetness 0.1732, level 0.4371) and asleep vs lying-awake, both camera modes
  [ran]. **L7 High (public seeds only)** world reconstruction from public spec plus public seed decodes every handle
  [ran]. **L2 Medium-High** geometric `view.px` follows true size, so look-alike members are told apart (bleach jug
  0.306 m vs detergent 0.286 m vs booster 0.206 m while the record shows one group size) [ran]. **L3 Medium**
  the battery readout weighs held items (1.30 kg and 2.71 kg recovered for 1.3 and 2.7 kg, both touch "medium") [ran].
  **L4 Medium** look-alike members differ in type defaults on physically sensed channels (35 of 83 multi-type groups)
  [ran census; inspect tell `liquid_l` 2.8 vs 2.5 ran]. **L5 Low-Medium** derived floor-hazard kinds reveal
  inspect-tier fields (`fragile` ring, `rug_slip` reveals the hidden pad) [ran]. **L8 Low** the camera size estimate
  can be averaged by regression over viewing conditions (single error up to 8 cm, regression error 0.4-4 cm) [ran].
* Null results [ran]: durations under hidden mass, private flag, pre-read look-alike handling, refusal texts, numbers
  in 6,000 messages, sleep ring, coverage, repeat-inspect averaging, `obs.nav`. Auditor: 0 leaks at 6×150 and
  40×100 in both camera modes on this same tree.
* Hypotheses tested: H5 (look-alikes distinguishable by durations/energy/refusals) mostly refuted for durations and
  refusals, confirmed for size (L2), mass (L3/L4) and spill hazard kind (L4); H6 (averaging) confirmed for `size_m`
  only (L8), refuted for inspect estimates.
* Open questions for the owner: (1) Is the lidar meant to report concealed people (it does, by design of
  `sight._agents`; realistic for a real 2D lidar, but it makes lidar a hidden-person detector)? (2) Should dev-split
  results from code-executing agents count at all, given L7?
* Next (for whoever continues): run the twin differ on the 51 realistic authored specs' own scenarios; fix L1 and
  re-run `probe_until_oracle.py` as a regression test.

---

## 1. Method

Every hunter has two parts:

* **Agent part**: acts only through `env.step(...)` in `perception="realistic"`, `camera="room"` or `"geometric"`,
  with handles taken from observations. It decides only from observations, messages, the clock and `obs.robot`, plus
  public knowledge: docs, type JSON, the robot profile, and the simulator code where stated (L7).
* **Harness part**: builds the scenario from a small spec dict (`S/p3lib.py: spec()`, then `runner.build` and
  `runner.make_env`, the same path the verifier uses), prints ground truth, and for twin tests maps handles back to
  true ids (`canon`) to diff two worlds. Nothing privileged flows into an agent decision.

Twin method (`S/probe_twins.py`): two or three worlds from the same seed and house that differ only in one hidden value
of one thing (`eid` fixed to `probe_item` and the floor cell pinned). The same action bank runs in each. Per step the
harness diffs ok, category, message, clock delta, battery gauge, runtime estimate, load words and the whole
canonicalised observation. A difference is a side channel; whether it is a leak is judged by asking "could a camera
robot plausibly perceive this?".

## 2. Commands run and results

All from the frozen tree as working directory; `S = ../out/p3_leaks_side_scratch`.

| # | Command | Result |
|---|---|---|
| 1 | `python $S/smoke.py` | realistic env builds in about 8 s; obs keys listed; robot starts docked in `living_room_1` (seed 0) |
| 2 | `python $S/probe_until_oracle.py room 0` / `geometric 0` | **L1**: umbrella `wetness` read as `0.1732` in 18 one-step waits (glance shows "looks dry"); bucket `level` `0.4371` in 20 steps (glance "half"; geometric scan did not find the bucket, so level was tested in room mode only); `people.sleeper.posture eq "sleeping"` → "Waited 0 s (until condition met)" when asleep, "Waited 1 s" when lying awake, while both observations show posture "lying", activity "lying down" (both camera modes) |
| 3 | `python $S/probe_watch_oracle.py room 0` / `geometric 0` | **L1b**: `watch(condition={"path":"people.p1.posture","op":"in","value":["sleeping"]}, mode="auto")` accepted; after `wait 60` the order ran only for the sleeper ("standing orders ran: order 0: look ok") in both modes |
| 4 | `python $S/lookalike_sizes.py` | 127 look-alike groups; 28 with member types of different true size, e.g. `drink_carton` heavy cream 0.13 m vs milk 0.25 m, `laundry_jug` 0.20-0.30 m, `granule_bag` 0.35-0.60 m (several of these set a group `size_m`, so the record hides the difference) |
| 5 | `python $S/probe_px_size.py 0` (output `probe_px_size.out.json`) | **L2**: geometric, record shows the group size `[0.15, 0.1, 0.3]` for every jug; `px × range_m / focal` = 0.306 / 0.286 / 0.206 m for bleach / detergent / oxygen booster (true 0.30 / 0.28 / 0.20); cream 0.131 vs milk 0.245 (true 0.13 / 0.25); lime 0.619 vs grass seed 0.347 (true 0.60 / 0.35) |
| 6 | `python $S/probe_energy_scale.py room 0 1.3,2.7` | **L3**: empty-hand standby 20.0 W measured; holding the box: P = 23.90 W and 28.12 W → estimated **1.30 kg and 2.71 kg** (true 1.3 / 2.7); touch shows "medium" for both, `load` "light" / "moderate"; about 1.5 h of simulated time, 65 s wall per pair |
| 7 | `python $S/probe_twins.py room 0 mass_in_bucket` and `python $S/probe_twins.py room 0` / `geometric 0` (outputs `twins_room_mass.json`, `twins_room_all.json`, `twins_geo_all.json`; summary `python $S/summarize_twins.py FILE`) | 8 scenarios × 2-3 twins × 17-step bank, both modes. **Nulls**: mass within a touch bucket (only the documented `load` word differs; push / pull / drag / pick_up / place durations identical); powder jar and spray bottle members identical until `read`; pill bottles identical until `read`; private vs plain box identical in every channel. **Leaks**: laundry jug `inspect` shows `liquid_l` 2.8 (bleach) vs 2.5 (detergent) before reading (same noise draw, so the difference is the type default); after a push spill the floor hazard kind is `wet` (bleach) vs `slip` (detergent); fragile mug shows a `fragile` hazard ring at step 0 (L5); in geometric mode the jug twins are detected at different scan steps (bigger px, higher detection chance) |
| 8 | `python $S/probe_fragile_hazard.py room 0` | **L5**: mug with `fragile: true` produces 8 `fragile` hazard cells in `obs.grid.hazards` at the first observation; the record has no `fragile` key; tier of `fragile` = inspect, knowledge = 0 (glance). Geometric confirmed by twin row 7 (step 0: 1 vs 9 hazards) |
| 9 | `python $S/probe_rug_pad.py 0` / `1` / `2` | **L5b**: seed 2 (hardwood): rug without pad → `rug_slip` hazard; with `non_slip_pad: true` → none; record shows no pad field (tier inspect). Seeds 0 and 1: no hazard either way (soft floors) |
| 10 | `python $S/probe_sleep_ring.py room 0` / `geometric 0` | **Null**: asleep and lying-awake people both get 9 hazard cells of kind `lying`; pets both get 10 (`lying`, `tail`). This path is closed |
| 11 | `python tools/perception_audit.py 6 150 --workers 3` (both cameras) | `houses 6 steps 900 leaks 0 crashes 0` in both modes (44 s, 35 s) |
| 12 | `python tools/perception_audit.py 40 100 --workers 3` (both cameras; `S/audit_40x100_*.txt`) | `houses 40 steps 4000 leaks 0 crashes 0` in both modes |
| 13 | `python $S/probe_reconstruct.py` (cleaning__perception `T-3fb8a84f20876c-01`, dev seed 41) | **L7**: from (instruction, first observation) the agent tried 4 (spec, dev seed) rebuilds in 1.2-4 s, matched, decoded all 32 visible handles correctly, and listed all 9 look-alike spray bottles in the house with true type and label text **before seeing any**; its three glass-cleaner handles equal the truth |
| 14 | `python $S/probe_size_regression.py 0|1|2 1.73` | **L8**: seed 1: single glance height 1.65 (error -0.08), regression intercept 1.723 (-0.007); seed 2: single 0.50 / 0.30 / 1.70 (errors +0.07 / -0.07 / -0.03), regression 0.434 / 0.385 / 1.737 (+0.004 / +0.015 / +0.007); seed 0: fixed draw near zero, no change with m, no gain |
| 15 | `python $S/probe_coverage_twin.py 0` | **Null** for coverage: `obs.camera.coverage` changed only when the item was also listed. Lidar changed for unlisted items 0.25 m or taller behind the robot (a 360° lidar: realistic, not a leak) |
| 16 | `python $S/scan_message_numbers.py 9 250`, then refined: `12 250 room 100` and `12 250 geometric 200`; `python $S/rerun_hits.py` | **Null**: 6,000 realistic messages in 24 houses; every candidate match of a message number to a hidden-tier field was a coincidence (validation texts such as "must be at most 0.5", 23 healthy leaves vs a 23 °C zone, setpoint 21.0 vs room 21.0, a programme's `water_l` 70 vs washer mass 70 kg) |
| 17 | `python $S/probe_noise_repeat.py room 1` / `geometric 1` | **Null**: inspect `liquid_l` stays 0.6 (true 1.37) from 3 different cells, after 600 s and after the bucket was moved; no averaging |
| 18 | `python $S/oracle_reach.py` | 18 authored specs mention posture/asleep/wetness/level in scoring or variants; 4 realistic. I read 2 of the 4 and they are not exploitable by L1 (`T-a31398ab2abf54-01`: resident always awake and sitting; `T-ba18cc4b56e89d-01`: textile wetness is never-tier, row 19). Not checked: `T-3b1a4024d46110-01` (plumbing, geometric, a dripping joint judged by `wetness`, possibly exposed) and `T-f7913405ff4906-01` (outdoor_plants) |
| 19 | wetness tier by type (inline, see transcript) | textiles (`t_shirt`, `bath_towel`, `jeans`, `towel`) wetness tier `never`; `rain_umbrella`, `sponge`, `floor_surface` `glance` (oracle applies) |
| 20 | catalog keyword count (inline) | of 2,720 entries: 4 mention sleep words, 28 dry/damp/wet, 76 level/full/empty/refill (a crude count of entry text, not proof of exposure) |
| 21 | `python $S/lookalike_tells.py` | **L4 census**: 83 look-alike groups with 2+ member types; 35 differ in at least one physically sensed channel: 25 true size > 5 %, 24 mass > 0.1 kg, 8 cross a touch-bucket edge, 2 default `liquid_l`, 1 spill hazard kind |
| 22 | `obs.nav` / `obs.robot` / `obs.climate` dump (inline) | **Null**: `obs.nav.battery_pct` is the integer gauge, `camera_clean` bucketed (adoption done). `runtime_min_est` 1250 and `range_m_est` 6500 at 100 % |

## 3. Findings (ranked)

### L1 (High). `wait(until)`, `watch(condition)` and `guard` are exact-value oracles

* **What is wrong.** `timeline._entity_observable` accepts an atom on a glance-tier field in `OBSERVABLE_FIELDS`
  whenever its op is not in `NUMERIC_OPS = (gt, gte, lt, lte, approx, between)`. The remaining predicate ops
  (`eq`, `ne`, `in`, `not_in`, `startswith`, `contains`, `is_true`, `is_false`, `len_*`, `subset`, `superset`) are
  accepted, and `holds_perceived` evaluates them on the **true** value (`check(w.s, rule)`), not on what the robot
  perceives. A wait whose condition already holds returns at once ("Waited 0 s (until condition met)"; `_advance`
  returns 0.0 when `wait.reason` is set), so each probe is a free one-step yes/no question. People and pets: posture
  is watchable (`REALISTIC_WATCH_AGENT_FIELDS`), but compared on the true posture, where realism renders "sleeping"
  and "lying" both as "lying" (pets: "resting").
* **Evidence.** [read] `house_sim/kernel/timeline.py:66` (`NUMERIC_OPS`), `:302-317` (`_entity_observable`),
  `:337-343` (agents), `:382-385` (`holds_perceived`); `predicates.py:37-39` (ops). [ran] row 2: exact wetness
  0.1732 in 18 steps (glance "looks dry"), exact level 0.4371 in 20 steps (glance "half"), asleep vs awake in 1 step,
  in both camera modes; row 3: the same through an auto standing order with op `in`. [read] the auditor's own
  `wait(until)` probes use only `open eq bool`, `posture eq "sitting"` and `location eq/ne`, and check only whether the
  wait was accepted (`tools/perception_audit.py:792-807, 823-832`).
* **Minimal repro** (`S/probe_until_oracle.py`, agent side):
  ```python
  h = [k for k, r in obs["visible_entities"].items() if "bucket" in r["label"]][0]
  def yes(op, v):   # one step per question
      r = env.step("wait", seconds=1, until={"path": "entities.%s.level" % h, "op": op, "value": v})
      return r["message"].startswith("Waited 0 s")
  # digit-by-digit: yes("startswith", "0.4"), yes("startswith", "0.43"), ... -> "0.4371"
  env.step("wait", seconds=1, until={"path": "people.sleeper.posture", "op": "eq", "value": "sleeping"})
  # "Waited 0 s (until condition met)" iff truly asleep; obs shows posture "lying" either way
  ```
* **What is learned.** Exact values of glance-tier `wetness` and `level` (any non-textile type: umbrellas, sponges,
  floors, buckets, containers with a `level` field); asleep vs lying awake for people; asleep vs resting for pets.
  The project protects the asleep/awake distinction explicitly (PERCEPTION.md, "Lying awake and asleep look the
  same"; `navigation_robot/hazards.py` equalises the sleep ring for the same reason).
* **Why it matters.** It is a general primitive in the documented action API, cheap (1 step per bit) and available
  in both camera modes. An LLM agent that experiments with predicate operators will find it. Today's reach on the 51
  realistic authored specs looks small: of 4 flagged specs, 2 are not exposed and 2 were not checked (row 18; the
  plumbing leak-finding spec may be exposed through `wetness`). The catalog still to be authored has entries about
  sleeping people, dryness and levels (row 20: crude counts 4 / 28 / 76) [inferred].
* **Smallest fix.** Evaluate until, watch and guard atoms on the **rendered** value at the robot's current knowledge
  (the same `Realism.render` / agent posture mapping the observation uses), and whitelist ops per rendered type
  (`eq`/`ne`/`in` over the rendered vocabulary). A narrower stop-gap is to refuse every op except `eq`/`ne` and
  compare posture after mapping; that still leaves `eq 0.0` on wetness, so only the rendered-value fix closes it.
  Add `probe_until_oracle.py` as a regression test.

### L7 (High for public seeds; not the hidden test split). Offline reconstruction decodes every handle

* **What is wrong.** Handles are `obj_` + sha256(seed, `_digest(initial house)`, true id)
  (`realism.Handles`, `realism.py:452`). The initial house is `build(spec, seed)` of a public spec file and a public
  seed (train range; dev seeds = verification seeds 0, 1, 2, 41, 97, `splits.py:35`). Anyone with the open-source
  simulator recomputes the salt. The fixed noise draws are keyed by (seed, true id) too.
* **Evidence.** [ran] row 13: the agent part received only the instruction text and the first observation, rebuilt
  4 candidate (spec, dev seed) pairs in 1.2-4 s, decoded all 32 visible handles correctly, and named the three
  glass-cleaner handles (matching the truth) plus the true type and label text of all 9 look-alike bottles before
  seeing any of them. [read] test seeds are `PRIVATE_BASE + HMAC(key, cid:i) mod PRIVATE_SPAN` (`splits.py:225-226`),
  so this attack does not reach the hidden test split as long as the key and seeds stay secret and the seed space is
  large.
* **Why it matters.** Dev-split scores and any leaderboard entry on public seeds are untrustworthy for agents that can
  run code or were trained on the generator. "Realistic perception" offers no secrecy against them. The automated
  auditor cannot see this: no single observation contains a leak.
* **Smallest fix.** Mix a per-episode secret nonce, held by the harness and stored only in the private replay log
  (the verifier needs it), into the handle salt and into the noise and detection draws. Label dev results as
  "code-executing agents not comparable" until then.

### L2 (Medium-High). The geometric camera's `view.px` reveals true size, including look-alike identity

* **What is wrong.** `sight.px_on_target` uses the true largest dimension (`sight.py:2482-2484`). Realism shows
  `px` to 2 significant figures and `range_m` to 0.1 m (`realism.py:888-907`). An unresolved look-alike's record shows
  the group's common size (PERCEPTION.md "Look-alikes"), but `px × range_m / focal_px` estimates the true size to
  about 2-4 %.
* **Evidence.** [ran] row 5: identical records for the three jugs, size estimates 0.306 / 0.286 / 0.206 m (true
  0.30 / 0.28 / 0.20). In `laundry_jug` the chlorine bleach bottle (0.30 m) is the only member of that size, so it is
  identified without reading. Cartons 0.131 vs 0.245, bags 0.619 vs 0.347. [ran] row 7 (geometric): the jug twins
  are detected at different scan steps because the detection-band chance depends on px.
* **Why it matters.** The geometric camera is the planned default for realistic perception (brief step D3). Claim
  C13 ("the geometric camera only ever shows less", EVAL_HARNESS) is false: it adds channels room mode lacks. 28
  groups have members of different true size (row 4).
* **Smallest fix.** For an unresolved look-alike, compute px from the group's displayed size, or align member sizes in
  the type data. Extend `LookalikeWarning` to flag true-size spreads even when the group sets `size_m`.

### L3 (Medium). The battery readout weighs what the robot holds ("the robot is a scale")

* **What is wrong.** Idle and standby power include `hold_per_kg × held kg` (3 W/kg against a 20 W standby
  baseline; `body.activity_power_w`, `profiles.json`). `proprio` hides load from the estimates (its docstring says
  an estimate following the load "would weigh it to about 0.2 kg, finer than the touch tier's mass bucket"), but the
  gauge itself drains faster under load. `runtime_min_est` is floored to 5 min of a 1,252-min pack, so it changes
  every ~0.4 % (a fixed ~9.58 Wh step), finer than the integer gauge.
* **Evidence.** [read] `kernel/body.py:424-447`, `proprio.py:189-201`. [ran] row 6: the agent times steps of
  `runtime_min_est` while waiting empty-handed and then holding the box: P = 20.0 / 23.9 / 28.1 W gives
  **1.30 kg and 2.71 kg** for true 1.3 and 2.7 kg. Touch shows "medium" for both.
* **Why it matters.** It breaks the documented "not a scale" invariant. Combined with L4 it resolves look-alikes that
  differ by mass (`drink_carton` 0.51 / 1.04 / 1.99 kg). Cost: about 1.5 h of simulated waiting, affordable within
  the generous budgets P1 measured (median 22× the reference time).
* **Smallest fix.** Make hold power a function of the shown `load` word (none/light/moderate/heavy/at_limit), not of
  exact kg, so the battery tells no more than `load` already does. Derive `runtime_min_est` from the integer gauge.

### L4 (Medium). Look-alike members differ in type defaults on physically sensed channels

* **What is wrong.** The registry lint (`LookalikeWarning`) checks only glance colour and size. Member types still
  differ in default `liquid_l` (shown at inspect), mass (touch buckets, `load` words, L3), true size (L2) and spill
  hazard kind.
* **Evidence.** [ran] row 7: before any read, laundry-jug `inspect` shows `liquid_l` 2.8 (bleach) vs 2.5
  (detergent); defaults bleach 2.8 L, detergents 2.5, softener 1.5, presoak and booster 1.0 (inline dump). After a
  spill, the floor hazard is `wet` (bleach) vs `slip` (detergent). [ran] row 21: 35 of 83 multi-type groups differ on at
  least one such channel.
* **Why it matters.** "Look and behave identically until identified" (brief s.3.2) does not hold. The read-resolved
  groups matter most, because tells let an agent skip reading (`laundry_jug`, `granule_bag`, `soil_bag`,
  `dog_food_bag`, `cleaning_bottle`).
* **Smallest fix.** Extend the lint to every channel reachable before resolution (true size, mass bucket and exact
  mass while L3 stands, default liquid and fill, spill `floor_hazard`). Let groups override physics props, or align
  the member types.

### L5 (Low-Medium). Derived floor-hazard kinds reveal inspect-tier fields

* **What is wrong.** `navigation_robot/hazards.derived` writes rings from hidden-tier fields (`fragile`,
  `topple_prone`, `non_slip_pad`, `anchored`), and realism passes every kind not in
  `HIDDEN_HAZARD_KINDS = {damp, film, black_ice, residue, invisible}` (`realism.py:2343-2368`).
* **Evidence.** [ran] row 8: a mug with `fragile: true` shows 8 `fragile` hazard cells at the first observation while
  its record hides `fragile` (inspect tier, knowledge 0), in both modes. [ran] row 9: `rug_slip` appears only for the
  rug without a hidden non-slip pad (hardwood seed 2).
* **Why it matters.** Low today, because fragility usually follows the type. It is a tier bypass of a class the
  auditor cannot see (it checks only whether the hazard's source is perceived), and every new derived kind a domain
  adds inherits it.
* **Smallest fix.** Show a derived kind only when its inputs are known at their tier (or are type-level knowledge).
  Add a hazard-kind tier check to the auditor.

### L8 (Low). The glance size estimate can be averaged by regression over viewing conditions

* **What is wrong.** `size_m` glance: est = round(n·(1 + g·0.08·m)·20)/20 with one fixed draw g per (seed, entity,
  field), but m = 1 + 0.12·(dist−1) + 1.0 when fast + light, lens and glare terms (`realism.py:749-794, 1031-1041`).
  The agent controls m (distance, `set_speed fast`) and can compute it from the public formula, so readings lie on a
  line whose intercept is the true size. PERCEPTION.md claims "no free averaging".
* **Evidence.** [ran] row 14: errors fall from up to 8 cm (single glance) to 0.4-4 cm (regression) on seeds 1 and 2;
  no gain on seed 0, where g ≈ 0.
* **Smallest fix.** Draw the error in a way that does not scale linearly with m (fixed relative error; let m coarsen
  only the quantisation step, as `_noisy` does).

### Minor notes (each low, [read] unless marked)

* `outdoor_plants` rounds work time up to 5 s steps in realistic (`verbs.py:2423-2432`), but `trim_plant` spends
  5 s per 5 g of trimmings (`verbs.py:824`), so its clock still reads the trimmed mass to 5 g.
* `_agent_notice`: person/pet enters/leaves is "heard" under full hearing for any agent, including concealed or silent
  ones (`timeline.py:880-893`).
* The lidar includes concealed people (`sight._agents` ignores `concealed`). That is realistic for a real 2D lidar, but
  it should be a stated design decision.
* PERCEPTION.md says noise is keyed by "(seed, entity, field, knowledge level, viewpoint)". The code keys `_noisy` by
  (seed, entity, field, level) only (`realism.py:926-937`). This is the safe direction, so only the doc is wrong
  (confirmed by row 17).

## 4. Null and negative results (each [ran] unless marked)

* **Durations:** mass 1.3 vs 2.7 kg and 4 vs 7.5 kg: pick_up, place, push (5 s), pull (4 s) and drag (5 s) identical;
  look-alike members: approach, inspect, shake and place identical (row 7). Domain duration code I read (plumbing pour,
  climate, coffee, cleaning soak, outdoor_plants) deliberately coarsens hidden-quantity durations [read].
* **Refusals:** bleach and detergent get byte-identical pick_up refusals ("You cannot tell what this is yet and it
  could be hazardous..."); pill bottles identical; body refusal texts carry no numbers [read `body.py:81-101`];
  `wheel_cart` drops the kg in realistic [read `curb.py:39-41`].
* **Private flag:** a private vs plain box is identical in every channel through open, inspect, read and close.
* **Messages:** no number in 6,000 realistic messages (24 houses, both modes) matched a hidden-tier field beyond
  coincidence.
* **Sleep ring:** equalised for asleep vs lying-awake people (9 cells) and pets (10 cells).
* **Coverage:** `obs.camera.coverage` did not reveal unlisted objects.
* **Repeat inspect:** no averaging across poses, time or moves.
* **`obs.nav`:** buckets and integer gauge (adoption done).
* **The auditor itself:** 0 leaks, 0 crashes at 6×150 and 40×100 per camera mode.

## 5. Leak classes `tools/perception_audit.py` structurally cannot detect

Read from `tools/perception_audit.py` [read], each with a concrete instance from this phase.

| # | Class | Why the auditor cannot see it | Concrete example |
|---|---|---|---|
| 1 | Information in the yes/no outcome of an accepted condition (until, watch, guard) | It checks only whether a wait is accepted while the target is not in view or dark (`check_typed_people`, l.792-807); its own until probes use `eq` on `open`, `posture eq "sitting"` and location | L1: `startswith` on `level`; `posture eq "sleeping"` |
| 2 | Cross-channel inconsistency (an auxiliary channel computed from the true value while the record shows a coarsened one) | It checks the camera `view` only for format (2 significant figures, 5° steps, range bounds: `view_problems`), never against the displayed `size_m` | L2: px of a look-alike follows true size |
| 3 | Information integrated over time in proprioception | `check_robot_obs` checks the gauge is an integer equal to `obs.battery`, not what its rate reveals | L3: battery drain weighs held items |
| 4 | Type-default tells (distinguishability rather than exactness) | `check_obs` flags a field only when the shown value equals the true value above the robot's tier (`_same`, l.271); a noisy but member-specific value passes | L4: inspect `liquid_l` 2.8 vs 2.5; spill kind `wet` vs `slip` |
| 5 | Derived channels whose content depends on hidden fields | The hazard check only asks whether the source is perceived (l.279-282); kinds are never tier-checked | L5: `fragile` ring, `rug_slip` (hidden pad) |
| 6 | Offline reconstruction from public seed and spec | Per-observation checks cannot see it: nothing secret appears in any observation | L7: all handles decoded on dev seed 41 |
| 7 | Statistical aggregation across observations | Each observation is checked alone, for exact equality | L8: size regression over m |
| 8 | Counterfactual or differential leaks in general (durations, refusal categories, field presence, message length) | No twin worlds; random walks on generated houses only | The twin tests here found none for durations or refusals (null), but the auditor could not have found one either |
| 9 | Tier gaps for booleans and integers | `informative` excludes booleans; integers are flagged only at read and measure tiers (l.268-275), so an inspect-tier `fragile: true` or `locked: true` shown at a glance would pass | No live instance found in records; structural |
| 10 | Numbers derived from hidden values in messages | `check_message` flags only measure-tier floats of the command's target with the field stem in the 40 preceding characters (l.660-679) | No live instance (row 16), structural |
| 11 | Leaks in authored scenarios | It never loads a spec; look-alike, private and hazard staging lives in specs (P1 F4) | n/a |

## 6. Claims register updates (from `p1_claims.md`)

* **C7** "0 leaks": the auditor reproduces 0 leaks on this tree [ran rows 11-12], while L1, L2, L3, L4, L5, L7 and L8
  reproduce on the same tree [ran]. "0 leaks" is an absence of evidence within the auditor's literal checks.
* **C13** "geometric only ever shows less": **refuted** [ran L2].
* **C14** "no free averaging": **refuted for `size_m`** [ran L8]; **holds for inspect-level `_noisy` estimates**
  [ran row 17].
* **C16** "refusal wording scrubbed": consistent with everything I tested [ran rows 7, 16].
* **H5** (P1): look-alikes distinguishable through durations / energy / refusals: refuted for durations and refusals,
  confirmed through size (L2), mass (L3 + L4) and spill kind (L4). **H6**: confirmed for `size_m` only.

## 7. Scripts written (`S/` = `scratchpad/review/out/p3_leaks_side_scratch/`)

| Script | Purpose |
|---|---|
| `p3lib.py` | scenario builder (`spec`, `env_for` through `runner.build`/`make_env`), agent-side `step` wrapper recording clock and battery, harness-only `canon` / `diff` |
| `probe_until_oracle.py` | L1 (wait until) |
| `probe_watch_oracle.py` | L1 (standing orders) |
| `lookalike_sizes.py`, `probe_px_size.py` (+ `.out.json`) | L2 |
| `probe_energy_scale.py` | L3 |
| `probe_twins.py`, `summarize_twins.py` (+ `twins_*.json`) | generic twin differ: L4, L5 and the nulls |
| `lookalike_tells.py` | L4 census |
| `probe_fragile_hazard.py`, `probe_rug_pad.py` | L5 |
| `probe_sleep_ring.py` | null (sleep ring) |
| `probe_reconstruct.py` | L7 |
| `probe_size_regression.py` | L8 |
| `probe_coverage_twin.py` | null (coverage) / lidar note |
| `probe_noise_repeat.py` | null (no averaging of inspect estimates) |
| `scan_message_numbers.py`, `rerun_hits.py` (+ `scan_numbers_*.json`) | null (numbers in messages) |
| `oracle_reach.py`, `list_lookalike_specs.py`, `find_types.py`, `smoke.py` | helpers |
| `audit_40x100_room.txt`, `audit_40x100_geo.txt` | auditor outputs |

## 8. What I could not verify

* Durations of the 222 actions with computed durations: I read about 30 handlers and twin-tested 9 verbs. Domain verbs
  that need staged fixtures (pour into a sink, chop, wring, vacuum...) were not twin-tested.
* L1 on authored specs: 2 of 4 flagged realistic specs read and not exposed; `T-3b1a4024d46110-01` and
  `T-f7913405ff4906-01` not checked (row 18). The exposure of future entries is [inferred] from keyword counts.
* L7 against the hidden test split: not attempted (it needs the key; rule 2). [read] code suggests it does not apply.
* L3 with the integer gauge alone, without `runtime_min_est`, is [inferred] to work with ~2.5× longer waits; not run.
* Python 3.9 behaviour: only 3.14 was available.
* I did not run anything inside the 51 realistic authored specs' own scenarios except the reconstruction probe.

## 9. Recommended checks (exact definitions)

1. **Condition-oracle check** (closes L1, regression for any future watchable field). For every field in
   `OBSERVABLE_FIELDS` and the agent posture fields, build twin worlds whose true values differ but whose rendered
   values are equal at the robot's knowledge. For every predicate op, issue `wait(seconds=1, until=...)` with values
   drawn from both true values and their string prefixes. Pass iff (accepted, message, clock delta) are identical
   across twins. It would have caught L1 on `level`, `wetness` and posture.
2. **Twin differ in the gate** (closes classes 2, 4, 5, 8). For each look-alike group, each never/inspect-tier field
   with per-instance variation, and a set of hidden physical props, run `S/probe_twins.py`-style banks in both camera
   modes. Pass iff the canonical observation, messages, clock and gauge are identical until the documented resolving
   action. It would have caught L2 (px), L4 (`liquid_l`, spill kind) and L5 (fragile ring).
3. **Secrecy check** (L7). Assert that no handle, noise draw or detection draw can be recomputed from (public spec,
   seed, code). Concretely: rebuild the env offline, compute the handle table, and require zero overlap with the
   handles the env shows.
4. **Rate check on proprioception** (L3). Two twins that differ only in held mass inside one touch bucket; wait N hours;
   pass iff the `obs.robot` sequences are identical.
5. **Averaging check** (L8). For every noisy glance or inspect field, collect estimates over all agent-controllable
   conditions. Pass iff the best linear-fit intercept is no closer to the truth than the best single reading.
