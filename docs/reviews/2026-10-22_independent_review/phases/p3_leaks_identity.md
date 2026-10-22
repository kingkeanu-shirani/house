# house_ai independent review: Phase 3 (perception honesty), identity lens

Reviewer: Claude (Opus 5.5), subagent "p3_leaks_identity" (third session on this task; the first two were cut off).
Code under review: the frozen copy of commit `bc0417b` at
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/tree`
(no `.git`). Python 3.14.8 on Windows. Scripts and logs:
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p3_leaks_identity_scratch/`
(below `S/`); this session's re-run outputs are in `S/rerun3/`.

Hard rules kept: nothing in `<home>/OneDrive/Desktop/house_ai` was edited; no network or paid call; nothing
under `%USERPROFILE%\.house_ai\` was read, created or listed (the one script that imports the viewer points
`USERPROFILE`/`HOME` at `S/fake_home` first and asserts it); no `--init-split-key`.

Evidence labels: **[ran]** executed in this session and the result seen; **[ran, earlier attempt]** executed by the
interrupted earlier session of this task, log file named, not re-run now; **[read]** code or docs read, not run;
**[inferred]** reasoning only.

Agent discipline. In every attack, the agent acts only through `env.step(...)` with `perception="realistic"`, in room
mode or geometric camera mode, using handles taken from observations. Where stated, the handles come from an earlier
episode of the same seed. The analyst side (`S/p3lib.py`) may build counterfactual twins, read the true state to score
a result, or trigger an off-screen event, and each case says so. That truth is never given back to the agent. For twin
tests the handle salt is patched to a constant, so twins that differ in one hidden fact get the same handles. This is
a measuring aid only. Two older twin scripts (`twins_portions.py`, `twins_lookalike.py`) take the target's handle
straight from `realism.handles` instead of from an observation. That is a shortcut on the measuring side only; the
handle is the one the observation shows.

What this session did: it re-ran all 22 short probes, the two scans (position-role and echo-variant) and the project
auditor in both camera modes. Every re-run output matches the earlier log line for line (`diff`, 0 lines, timing
lines excluded), and section 2 marks each one "same". The one exception is `seed_search_cost.py --time`, where only
the timings differ (0.353 vs 0.377 s per seed). Two new probes were added
(`probe_echo_contents.py`, `check_sleep_state.py`). One earlier claim was **wrong and is retracted** (S2: the
"sleeping person reported awake" result came from a twin that the probe's own `speak` had already woken).

---

## 0. Status (end of Phase 3, identity lens), 2026-10-22 PDT

### Simple version

I wrote small robot programs that play the game the way an evaluated robot would: they use only what the robot is
allowed to see. They tried to learn things the robot should not know, such as which of several identical-looking
bottles is which, whose private paper something is, what an unfamiliar gadget really is, what is inside a closed
parcel, and which hidden version of a task is running. I found eleven ways to do it. The worst three:

- the street address printed on mail gives away the hidden version of a task, even on the secret test seeds;
- things a task sets out always sit in the same order, so "the third bottle from the left" is always the glass cleaner;
- the "count" command quietly looks closely at everything in a room from across the room.

Using these, my programs passed two realistic tasks 10 times out of 10. They read 1 label where the honest solution
reads 4 or 8. The project's own leak checker still reports zero leaks on the same code. By its design it cannot see
any of these.

### Advanced version

* Headline results [ran]:
  * mail-sort spec `T-c35156368a8a4b-01`: **10/10** seeds passed, reading 1 of 5 envelopes and 0 of 3 trays (the
    reference reads 8);
  * glass-cleaner spec `T-3fb8a84f20876c-01`: **10/10** (room mode), reading only the third bottle (the reference
    reads 4);
  * `count <room>` resolved 3/3 inspect-resolved cartons about 5 m away in room mode, and 2/2 in geometric mode,
    where `inspect` is refused;
  * a geometric episode passed the glass-cleaner spec with **0 looks**, acting on a handle remembered from an earlier
    episode that this episode had never issued;
  * free text shows how many items are inside each closed parcel, on 3/3 seeds in both camera modes (new this
    session).
* The project auditor on this tree reports **0 leaks, 0 crashes** with `perception_audit.py 6 150 --workers 3`, in
  both room and geometric mode [ran]. An earlier attempt got the same with `20 150` in room mode [ran, earlier
  attempt].
* Overlap with the side-channel lens (`p3_leaks_side.md`) is not counted again here:
  * L2: `view.px` and lidar ranges give true size;
  * L4: amounts and masses that are type defaults;
  * L7: offline rebuild on public seeds.

  My twin sweeps (E1) reproduced the same tells. I1 extends L7 to the hidden test split.
* Open questions for the owner:
  1. Is more than one attempt per hidden test seed allowed? If yes, I5 becomes High.
  2. Is `count` meant to need reach?

---

## 1. Findings (ranked)

Severity reflects what a cheating or merely over-fitted agent gains on the benchmark, not code quality.

### I1 (High). The street address encodes the seed, and with it the hidden variant of every conditional spec

* **What is wrong.**
  * The generator writes `address="{} Example Lane".format(100 + seed % 900)` (`house_sim/generator/__init__.py:76`)
    [read].
  * Mail pieces, periodicals, package labels and envelopes carry the address at read tier. The alarm-dispatch message
    also prints it (`house_sim/systems/safety/alarms.py:603`) [read].
  * Variants are chosen by `index = seed % len(variants)` (`house_sim/tasks/spec.py:149`) [read].
  * Hidden test seeds are raw seeds in `[900_000_000, 2**31-1)`, drawn by HMAC (`house_sim/eval/splits.py:38-39,
    225`) [read]. HMAC hides which seed it is, but the seed still flows into the same two formulas.
  * Every one of the 139 authored specs with variants has 2, 3 or 4 of them [ran: census], and 900 is divisible by 2,
    3 and 4. So `(address - 100) % n` is the variant index exactly, on any seed, including the hidden test seeds.
* **Minimal repro** [ran]: `cd S && python probe_address.py` (`S/rerun3/probe_address.out`). The envelope shows `100,
  101, 102, 141, 197, 323` on seeds `0, 1, 2, 41, 97, 1000123`, and `(n - 100) % 3` equals the true variant on 6/6.
* **End to end** [ran]: `python gaming_mail_sort.py` (`S/rerun3/gaming_mail_sort.out`) passes "Sort mail by
  recipient" **10/10** on seeds 0, 1, 2, 41, 97, 3, 8, 13, 24, 55. It needs only:
  * the address from 1 envelope;
  * the public spec's variant table (who letter_k is for);
  * I2, the fixed order of letters and trays.
* **Reach** [ran]: `python address_reach.py` (`S/rerun3/address_reach.out`). At seed 0, 5 of the 243 specs put the
  address in the world, and all 5 have variants. In the 2 realistic ones a naive agent read the variant off the
  address after 1 to 3 reads (6/6 episodes).
* **Second stage: seed recovery.**
  * The address cuts the seed space by 900. [ran] `python seed_recovery_demo.py` (`S/rerun3/seed_recovery_demo.out`):
    the hidden seed was drawn from a 9,000-seed window, which the address cut to 10 candidates and the start room's
    id, size and exits cut to 1. A rebuild from the public spec then recomputed the handle salt and mapped 5/5
    envelope handles to their true ids and recipients, in 5 to 7 s in total.
  * On the private range, each residue leaves 1,386,092 candidates. The full generator costs 0.353 s per seed [ran:
    `seed_search_cost.py --time`, `S/rerun3/seed_search_cost_time.out`], about 136 single-core hours per residue
    (**estimate**, trivially parallel). The layout-only prefilter costs 0.0008 s per seed [ran].
  * Not verified: that the prefilter can be matched against what an agent observes, and how much exploration makes the
    fingerprint unique among 1.4M candidates.
* **What is learned.** The hidden branch of a conditional spec, which is exactly what the "always-do and never-do must
  each fail some seed" rule relies on. For mail and package specs it gives every item's role. After seed recovery, it
  gives every true id, type, owner and hidden value in the house.
* **Why it matters.** It hits the specs whose honesty rests on variation across seeds, and it survives the hidden
  split. Many mail, package, paperwork and emergency-call entries are still to be authored.
* **Smallest fix.** Draw the printed address (and any other seed-derived printed text) from a separate stream that
  is not `seed mod k`, for example `int(sha256("address", seed)) % 900`. Choose variants by
  `int(sha256("variant", seed)) % n` rather than `seed % n`. Then add check R2 below.

### I2 (High). Authored placement is deterministic, so position tells spec roles without any reading

* **What is wrong.** Spec setups and helpers place staged things at fixed positions in creation order. Examples: the
  explicit `spos` in the glass-cleaner spec, and the order of letters and trays in `admin_inventory.mail_batch`. The
  same role sits in the same visible slot on every seed, and the agent sees `spos`/`pos`.
* **Minimal repro** [ran]: `python gaming_glass_position.py` (`S/rerun3/gaming_glass_position.out`).
  `T-3fb8a84f20876c-01` passes **10/10 in room mode** by taking the bottle third from the left and reading only that
  one (the reference reads 4). The `spos` values are `[0.08, 0.30, 0.42, 0.54]` on every seed.
  * Geometric mode: 4/10. The 6 failures are my naive agent not finding the bottles ("only 0 bottles seen"); it was
    never refused on identity grounds.
* **Scale** [ran]: `python position_role_scan2.py`, 243 specs x 10 seeds (`S/rerun3/position_role_scan2.out`).
  * 26 same-label groups of at least 2 staged things.
  * **16 groups in 15 specs (5 realistic) have their roles fixed by position on every seed**: letter trays, cloths,
    batteries, alarm bases, bulbs, wine glasses, stones, film rolls, plant saucers, plants, books.
  * This is a lower bound, because the scan looks only at the reset view.
* **What is learned.** Which look-alike is the target, the decoy or the trap (in the glass spec, which bottle holds
  the decanted bleach), and which letter is whose, without perceiving it.
* **Why it matters.** Any agent trained or tuned on public seeds learns "slot k = role k". The 5 verification seeds
  cannot catch this, because the reference plan passes either way.
* **Smallest fix.** Have the setup runner shuffle the positions of same-label staged things per episode, with a
  seeded shuffle from the episode stream. Forbid authored `spos` orderings that correlate with role. Add check R3.

### I3 (High). `count` is a remote inspect: it grants inspect-tier knowledge of everything it counts, at any distance

* **What is wrong.** `house_sim/systems/admin_inventory/inventory.py`, `count()`, lines 226-230 [read]:
  `ctx.grant(scope, "inspect")` and `for eid in counted_ids + unidentified: ctx.grant(eid, "inspect")`.
  * There is no reach check and no geometric `inspect_check` gate. A room id is a valid scope.
  * Lines 180 and 205 also grant read tier (`pack_count`, `sku`, `label_text`) for packs counted from their printed
    label, again at any distance in a lit room.
* **Minimal repro** [ran]: `python repro_count_inspect.py` and `python repro_count_inspect_geo.py`
  (`S/rerun3/repro_count_inspect*.out`).
  * Room mode: `inspect <carton>` returns "Approach it first; it is out of arm's reach". Then `count living_room_1`
    from the start cell (the table is about 5 m away; the command takes 22 s) turns the three unresolved "paper drink
    carton" records into "milk", "oat milk" and "heavy cream", which matches the truth. 26 of 52 visible records gain
    fields.
  * Geometric mode: `inspect` returns "Too far to make out the details; approach it", but `count` resolves both
    cartons in view ("milk", "oat milk").
* **What is learned.** True identity in every look-alike group resolved by inspect, and every inspect-tier field
  (material, soil per kind, damage, freshness, locked, settings, fine fill levels) for a whole room, in one command,
  from the doorway.
* **Why it matters.** It breaks the documented tier model ("inspect within reach") and the camera's acuity gate. Any
  agent that tries `count` on a room will find it. The auditor cannot see it, because the knowledge was granted
  "legitimately" (section 4, class B).
* **Smallest fix.** Grant inspect only for items within reach that pass `camera.inspect_check`. Count the others at
  their current knowledge, with unresolved members reported as "unidentified", as `count` already does for groups
  resolved by read. Better: route every `ctx.grant` through one per-tier gate (R4).

### I4 (Medium). Novel ("unfamiliar") objects are identified by the fields inspect returns and by type-specific outcomes

* **What is wrong.** `docs/PERCEPTION.md:117` says novel types are "described only by visual attributes ... never by
  name" [read]. Yet:
  * the glance record keeps the true type's `openable`;
  * inspect keeps the true type's domain fields;
  * several actions answer according to the true type.
* **Minimal repro** [ran]: `python repro_novel_inspect.py` (`S/rerun3/repro_novel_inspect.out`). Six twins, all shown
  as "unfamiliar medium object" with the same visual props, in both camera modes. All six end up in different
  classes: five have their own tell, and the knife is the one with none:

  | True type | What gives it away |
  |---|---|
  | kettle | glance `openable=True`; `open` → "Opened" |
  | smartphone | inspect has `battery_display`; `take_photo` → "Nobody to photograph here" |
  | hair dryer | inspect has `cord_condition` and `plug`; `switch_on` → "No power" |
  | bleach bottle | inspect has `fill_level` and `liquid_l` |
  | remote | inspect has `battery_compartment` |

  `repro_small.py` part C [ran] adds a third tell: the
  `connect_cable` refusal lists the true ports (`ports: charge` only for the phone).
* **What is learned.** The true class of the "unfamiliar" thing, which is exactly what novel-object entries are meant
  to withhold.
* **Smallest fix.** For a novel type, project glance and inspect onto a fixed key set (visual attributes plus
  material), and route the action-applicability refusals through one generic "you do not know how this works" message
  until the thing is identified.

### I5 (Medium; High if repeated attempts per hidden seed are allowed). Handles are stable across episodes, and geometric-mode actions accept things never framed

* **What is wrong.**
  * Handles are `obj_` + `sha256("handle", seed, salt, id)[:8]`. The salt depends on the seed and the initial house,
    so it is the same in every episode of the same spec and seed (`house_sim/kernel/realism.py:306-337`) [read].
  * `Handles.resolve` resolves any existing entity's handle lazily [read].
  * In geometric mode, action-level visibility is `camera.can_see`: "detectable from the current eye at any pan,
    tilt or body yaw" (`house_sim/kernel/camera.py:999-1022`) [read]. It is not "shown in a frame".
* **Minimal repro** [ran]: `python probe_handle_reuse.py` and `python repro_handle_memo.py`
  (`S/rerun3/probe_handle_reuse.out`, `S/rerun3/repro_handle_memo.out`).
  * The handles are identical across two episodes of the same spec and seed, for 2 geometric specs.
  * Episode 2 of the glass-cleaner spec, seed 41, geometric mode: the agent uses `go_to kitchen_1` and **no** look,
    face or peek. A handle remembered from episode 1 is not in the current frame and has never been issued in
    episode 2. It is still accepted by `approach`, `pick_up` and `read`, and the spec passes with **0 looks**.
* **What is learned.** Identity carried over from earlier episodes of the same seed, and direct action on things the
  camera has not shown in this episode.
* **Why it matters.** On public seeds this is plain memorisation; L7 already makes it trivial. On hidden seeds it
  matters if any agent may run twice on the same test seed, or if the seed can be recovered (I1). The geometric gate
  also lets an agent skip the looking that geometric-camera specs are meant to test.
* **Smallest fix.**
  * Salt handles with a per-episode nonce. Replays already store the handle map, so this needs no recomputation.
  * In geometric mode, accept an entity argument only if it was issued this episode or is in the current step view.
  * State a one-attempt-per-hidden-seed rule in `docs/SPLITS.md`.

### I6 (Medium). Free text and handle-accepting text parameters are an existence oracle: hidden contents, off-screen events, spec variants

* **What is wrong.** `Realism.inbound` [read, `realism.py:1795-1890`] treats words in two different ways:
  * It neutralises an id-shaped word to "something" whenever it is a current or past entity id.
  * It neutralises it whether or not it exists only when the word is built on a registry type name.

  Ids not built on a type name are therefore echoed verbatim when absent and as "something" when present. This covers
  spec-authored ids (`letter_6`, `puddle_of_water_1`, `tray_resident_1`, `parcel_1_item_1`) and 17 runtime id stems
  (`pet_accident`, `spilled_pet_food`, `weed`, `egg`, `sleeper`, `doc_`, ... [ran: census with a registry lookup]).

  Separately, a handle inside a text-like parameter (`recall`, `remember`, `explain`, `ask about`, `speak`, `wait`
  predicate) is accepted while its thing exists, even out of view, and refused once it is destroyed.
* **Minimal repros** [ran]:
  * **Hidden contents (new).** `python probe_echo_contents.py` (`S/rerun3/probe_echo_contents.out`), spec
    `T-8fb945d433137c-01`, seeds 0, 1, 2, room and geometric. `speak "is parcel_1_item_1 here"` comes back as
    "something" for every parcel's first item and verbatim for `_item_2`. All parcels are closed and nothing inside is
    in view (analyst check). So the echo counts the items inside each closed parcel.
  * **Off-screen event.** `python repro_runtime_event_echo.py`. The robot stays in the kitchen while the pets system
    makes the dog have an accident in the living room (triggered by the analyst). The echo goes from `'did
    pet_accident_1 happen'` to `'did something happen'`.
  * **Spec variant.** `python probe_echo_variant.py`. `puddle_of_water_1` exists exactly on variant 0 of
    `T-089c40fb507289-01`, on all 5 verification seeds. `python echo_variant_scan2.py` (`S/rerun3/echo_variant_scan2.out`)
    searched all 139 variant specs: only this 1 spec (oracle mode) and 0 realistic specs leak the variant this way.
  * **Destroyed vs alive.** `python probe_refusal.py` and `repro_small.py` part E. `recall`, `remember`, `explain`,
    `ask`, `wait` and `speak` on the handle of an out-of-view apple: ok. On a destroyed apple: "That is not visible
    here".
* **What is learned.** How many items are inside closed parcels, how many letters exist, whether an off-screen event
  happened, whether a thing seen earlier has since been eaten, thrown out or merged, and (in 1 spec) the variant.
* **Smallest fix.** In free text, neutralise every id-shaped word (`[a-z]+(_[a-z0-9]+)*_\d+`) whether or not it
  exists. In non-entity parameters, accept a handle only if it was issued this episode, and answer the same for a
  destroyed thing as for an absent one (accept both, or refuse both).

### I7 (Medium). Private documents give away the never-tier `private` and `owner` flags through inspect keys and the read refusal

* **Minimal repro** [ran]: `python repro_small.py` part A (`S/rerun3/repro_small.out`). The same document three ways:

  | Variant | Inspect has `title` | `read` |
  |---|---|---|
  | plain | yes | ok |
  | private, owned by resident_2 | no | refused, category `permission`, "That needs the household's permission first" (`people/care.py:340`) |
  | private, owned by the requester | no | ok |

  [ran, earlier attempt] `twins_generic.py private --sweep` (`S/e2_private.log`): of 7 private-capable types only
  `document` differs (41 battery and 2 sweep differences). Notebook, wallet, jewelry box, mail piece, smartphone and
  laptop differ only in `ask owner` answers, which is a legitimate channel.
* **What is learned.** Whether a document is private (from one inspect), and whether its owner is the requester or
  someone else (from the read refusal). Both are never-tier facts in the field-tier model.
* **Smallest fix.** Show the same inspect keys for private and non-private documents (a visible "PRIVATE" stamp is
  fine if it is an authored, perceivable mark). Make the permission check a decision the agent must make: let the read
  happen, and log a privacy violation if it was not allowed.

### I8 (Low). Precondition wording separates the plain spray bottle from the cleaner bottles while all are unresolved

* **Minimal repro** [ran]: `repro_small.py` part B. All three are shown as "spray bottle of liquid", unresolved:

  | True type | `empty_reservoir` | `refill_reservoir` |
  |---|---|---|
  | glass_cleaner, mildew_remover | "That has no reservoir to empty" | "That has no water reservoir" |
  | spray_bottle | "Hold it first (uninstall it from the unit)" | "Hold the tank (or stand it in the sink) to fill it" |

  The type check runs before the hold check (`climate/moisture.py:600-601`) [read].
* **What is learned.** Which unresolved member is the plain (water) spray bottle. In the glass-cleaner spec it is one
  of the four look-alikes.
* **Root cause shared with I4.** Action applicability is decided on the true type, and the refusal says so. The E1
  sweep (597 commands x 9 groups) found only this one and physics-driven ones (L4) [ran, earlier attempt,
  `S/e1_sweep.log`].
* **Smallest fix.** Check "holding" (or another type-agnostic precondition) first, and give unresolved look-alikes
  one generic refusal.

### I9 (Low). Glance-tier `openable` and `open` differ inside three look-alike groups

* **Minimal repro** [ran]: `repro_small.py` part D, reset observation, no action:

  | Group label | Member | open | openable |
  |---|---|---|---|
  | "round countertop cooker with a lid" | electric pressure cooker | True | True |
  | | rice cooker | False | True |
  | "file folder" | accordion file | False | True |
  | | file folder | False | False |
  | "flat air-filter panel" | purifier carbon filter | False | True |
  | | purifier prefilter | False | False |

  [ran, earlier attempt] the E1 room battery (`S/e1_room_s5.log`) also showed, while unresolved:
  * `pos` and `temperature_c` for `egg_in_shell`;
  * floor-hazard cells for `snow_shovel`;
  * `liquid_l`/`fill_level` for `laundry_jug` (L4).
* **What is learned.** Which member, from one glance.
* **Smallest fix.** Have the registry's look-alike consistency check (which already warns about size and colour)
  also align `openable` and the defaults of the recognize-tier states, or override them per group.

### I10 (Low, arguably legitimate). Room mode, dark room: `point` tells whether a dark shape is the requester

* **Minimal repro** [ran]: `python twins_dark_people.py` (`S/rerun3/twins_dark_people.out`). The twins differ only in
  which person stands as the dark shape. If it is the requester, `point` gets a spoken answer; otherwise it is refused
  with "Nobody here to confirm". Geometric mode: 0 differences.
* **Assessment.** The requester answering by voice is a perceivable channel. The tell is the silent refusal when
  someone else is present. Low.
* **Fix.** In the dark, let a non-requester answer too, or answer with silence as a normal outcome rather than a
  refusal.

### I11 (Low). `obs.grid.hazards` lists the sources in one cell in insertion order (room mode)

* **Minimal repro** [ran]: `python repro_hazard_cell_order.py`. Two unresolved laundry jugs on one floor cell: the
  listed order flips when the spawn order flips, with handles unchanged. Geometric mode: not listed, so no tell.
* **What is learned.** Spawn order, which in authored specs follows role order (as in I2). The auditor's order rule
  only tests true-id order, so it cannot see this.
* **Fix.** Sort the sources within a cell by handle.

### Correctness side findings (not leaks)

* **S1 (Low).** In geometric mode, `read` returns `ok=True` and the label text, but the record stays unresolved
  [ran: `repro_scrubbed_read.py`, seeds 41 and 1]. The product name inside `label_text` is replaced by the group
  descriptor ("spray bottle of liquid with Ammonia-D. Spray on glass and mirrors..."), but the usage text still
  identifies the product. In room mode the same read resolves and returns the text verbatim. So the two camera modes
  disagree on what reading does, and in geometric mode the scrubbing protects nothing.
* **S2 (corrected; Low).** The earlier draft said inspect reports `"awake": true` for a sleeping person. **That was
  wrong.** [ran: `check_sleep_state.py`] A sleeper set up with `awake: False` is inspected as `awake: false` in both
  modes. In the twin script, the `speak ... to resident_1` step had woken her: true `awake` flips to True at that step.
  The residual defect: after waking, `activity` stays `"sleeping"` while `awake` is true, so inspect shows the two
  inconsistently.

---

## 2. Commands run and results

Each command is `cd S && python -W ignore <script>`; every script `chdir`s into the frozen tree. This session's
outputs are in `S/rerun3/<script>.out`, and the earlier outputs sit next to the scripts in `S/`. "Same" means this
session's output matches the earlier one line for line after removing timing lines (`diff`, 0 lines).

| # | Command | Result | Label |
|---|---|---|---|
| 1 | `tools/perception_audit.py 6 150 --workers 3` (room) and `... --camera geometric` (run in the tree) | `houses 6 steps 900 leaks 0 crashes 0`, both modes (`rerun3/audit_room_6x150.log`, `rerun3/audit_geo_6x150.log`), about 9 s each | [ran] |
| 2 | `tools/perception_audit.py 20 150 --workers 3` | `houses 20 steps 3000 leaks 0 crashes 0` (`S/audit_room_20.log`) | [ran, earlier attempt] |
| 3 | `repro_small.py` | Parts A-E as in I7, I8, I4, I9 and I6; same | [ran] |
| 4 | `probe_refusal.py` | Entity parameters (inspect, pick_up, approach, read, open) give the same "That is not visible here" (category `perception`) for 7 target states: out of view, destroyed, inside a closed jar, moved unseen, never issued, true id, garbage. That is 35/35 identical. Non-entity parameters accept an existing unseen thing and refuse a destroyed one (I6). Same | [ran] |
| 5 | `probe_echo.py`, `probe_echo_variant.py` | The echo shows existence for ids not built on a type name; the variant tracks `puddle_of_water_1`; same | [ran] |
| 6 | `probe_echo_contents.py` (new) | Closed parcels: item counts read off the echo, 3 seeds x 2 camera modes, 42/42 rows match the truth. `ask` for the address returned "ask requires target"; not retried | [ran] |
| 7 | `repro_runtime_event_echo.py` | An off-screen pet accident flips the echo; same | [ran] |
| 8 | `echo_variant_scan2.py` (139 variant specs) | 1 spec whose variant is read off the echo (`T-089c40fb507289-01`, `puddle_of_water_1`), 0 of them realistic; same as `S/echo_variant_scan2.log` (`rerun3/echo_variant_scan2.out`, 4 min 40 s) | [ran] |
| 9 | `grep new_id(` census plus registry check | 42 runtime id stems, 17 of them not registry type names (re-counted this session with a registry lookup; matches `S/new_id_prefixes.txt`) | [ran] |
| 10 | `probe_address.py` | `(n-100) % 3` equals the variant on 6/6 seeds; same | [ran] |
| 11 | `address_reach.py` (243 specs) | 5 specs carry the address, all with variants; in the 2 realistic ones the variant came from the address in 6/6 episodes; same | [ran] |
| 12 | `gaming_mail_sort.py` | **10/10**, 1 read per episode; same | [ran] |
| 13 | `seed_recovery_demo.py` | Seed recovered from a 9,000-seed window; 5/5 handles mapped to true ids; same | [ran] |
| 14 | `seed_search_cost.py --time` | Full generator 0.353 s/seed, layout-only 0.0008 s/seed, 1,386,092 candidates per residue, 0 duplicate structure fingerprints in 300 seeds | [ran] |
| 15 | Variant census over `tasks/authored/*.json` | 243 specs: 104 with no variants, 83 with 2, 54 with 3, 2 with 4. Realistic specs: 11 with none, 6 with 2, 34 with 3. All 139 variant counts divide 900 | [ran] |
| 16 | `position_role_scan2.py` (243 specs x 10 seeds) | 26 same-label groups in 24 specs; 16 role-by-position groups in 15 specs, 5 of them realistic (some groups appear on only 3-9 of the 10 seeds); same as `S/position_role_scan2.log` (`rerun3/position_role_scan2.out`, 9 min 38 s) | [ran] |
| 17 | `gaming_glass_position.py` | **10/10** room, 4/10 geometric (agent search failures); same | [ran] |
| 18 | `repro_count_inspect.py`, `repro_count_inspect_geo.py` | Room 3/3 and geometric 2/2 cartons resolved by `count` after `inspect` was refused; same | [ran] |
| 19 | `repro_novel_inspect.py` | 5 of 6 novel twins tell themselves apart, both modes; same | [ran] |
| 20 | `probe_handle_reuse.py`, `repro_handle_memo.py` | Handles stable across episodes; a handle from an earlier episode accepted; glass spec passed with 0 looks; same | [ran] |
| 21 | `repro_scrubbed_read.py` | S1; same | [ran] |
| 22 | `twins_dark_people.py` | Room: `point` differs, requester vs other (I10). Geometric: 0 differences. Same | [ran] |
| 23 | `twins_sleep.py` and `check_sleep_state.py` (new) | Asleep vs awake: obs0, wait and speak identical; `point`/`listen` differ (legitimate). The earlier "awake: true" result came from `speak` waking the sleeper (S2 corrected) | [ran] |
| 24 | `repro_hazard_cell_order.py` | Room: the order within a cell follows spawn order (I11); same | [ran] |
| 25 | `key_order_twins.py` | 0 order-only key differences across all look-alike groups and 8 novel types; same | [ran] |
| 26 | `viewer_leak_check.py` (fake HOME) | Perceived-only page: 0 true entity ids, no true name of the unresolved jug or the novel kettle, no hidden key, owner, `private` or `secret_fields`; same | [ran] |
| 27 | `twins_lookalike.py` seed 5, 83 groups, room and geometric (E1) | Room: differences while unresolved in 6 groups (I9, L4). Geometric: 49/83 groups differ, mostly `robot.facing_deg`/`camera.pose` after look/face, `lidar.ranges_m` and `view.px` (true size: L2, not counted here), plus `openable` in 6 groups (I9) (`S/e1_room_s5.log`, `S/e1_geo_s5.log`) | [ran, earlier attempt] |
| 28 | `twins_generic.py lookalike_sweep --sweep` (9 groups x 597 commands) | Differences only in read/read_aloud (they resolve the thing: legitimate), inspect `liquid_l` (L4), the spray-bottle reservoir actions (I8), touch weight (L4) and rotate knock-over (physics on true size/mass) (`S/e1_sweep.log`) | [ran, earlier attempt] |
| 29 | `twins_generic.py private --sweep` | Only `document` differs (I7) (`S/e2_private.log`) | [ran, earlier attempt] |
| 30 | `twins_generic.py contents --sweep` | Closed boxes: only `shake` differs ("feels empty" vs "something shifts inside"): legitimate (`S/e2_contents.log`) | [ran, earlier attempt] |
| 31 | `twins_generic.py novel_sweep --sweep` | 28 of 597 commands differ across the six novel types (`S/e3_novel_sweep.log`); confirmed by row 19 | [ran, earlier attempt] |
| 32 | `twins_portions.py` | Pouring from an unresolved plastic jug into a mug gives identical mug records and messages for every member. The laundry and garden groups could not be poured (pick_up refused by the hazard guard), so they are untested (`S/portions_out.txt`) | [ran, earlier attempt] |
| 33 | `order_scan.py` (51 realistic specs x 2 seeds, reference runs) | Every handle-keyed dict and list is in handle order, except `obs.grid.hazards` (I11) (`S/e5_order_room.json`) | [ran, earlier attempt] |

---

## 3. Checks I recommend (exact definitions)

* **R1: Twin differential audit.**
  * *Twins:* for each look-alike group member, novel type, never-tier flag (`private`, `owner`, `off_limits`,
    `requires_approval`, `valuable`) and closed-container content, build two episodes that differ only in that
    hidden fact, with the same ids and a constant handle salt.
  * *Run:* a fixed battery, plus every registered action with the target in each entity parameter.
  * *Fail:* any agent-visible difference (ok flag, category, message, observation, time) at a step where the thing is
    unresolved in both twins before and after the step.
  * *Allow-list:* the legitimately resolving actions only (read and inspect for their tier, ask owner, shake).
  * *Catches:* I4, I7, I8, I9 and I10, which the current auditor cannot.
* **R2: Seed-correlation test for conditional specs.**
  * *Run:* for each spec with variants, 30 seeds. For every perceivable scalar or short text the reference episode
    reads (read- and glance-tier strings, digits inside them), compute how well it predicts the variant index.
  * *Fail:* any feature that predicts the variant on more than 90% of seeds while not being an authored cue (the
    authored cues are listed in the spec).
  * *Catches:* I1 (the address residue predicts the variant on 100%).
* **R3: Permutation metamorphic test.**
  * *Run:* the setup runner shuffles the positions and spawn order of same-label staged things per episode.
  * *Fail if:* the reference no longer passes on all seeds, or a "slot policy" fitted on seed 0 (position k means
    role k) still passes on more than 1/n of the shuffled seeds.
  * *Catches:* I2 (the glass slot policy passes 10/10 today) and I11.
* **R4: Grant gate under strict mode.**
  * Every `ctx.grant(eid, tier)` must satisfy the same predicate as the matching action: inspect needs reach and
    `camera.inspect_check`; read needs light and a clean lens.
  * Violations raise in strict mode, and the gate's fuzz and tests run every action that grants.
  * *Catches:* I3.
* **R5: Cross-episode handle refusal.**
  * *Run:* two episodes of the same spec and seed.
  * *Fail:* any handle collected in episode 1 and not issued in episode 2 that episode 2 accepts.
  * *Catches:* I5.
* **R6: Echo invariance.**
  * *Run:* twins with and without an entity whose id is not built on a type name.
  * *Fail:* `speak`, `ask`, `report`, `remember` and `wait` with id-shaped words reply differently between the twins.
  * *Catches:* I6.

---

## 4. Leak classes `tools/perception_audit.py` structurally cannot detect

The auditor runs random agents, one episode at a time, and checks each observation and message against per-value
rules: true ids, underscore and secret fields, never-tier values, exact values above the robot's knowledge, true-id
order, hazards the robot cannot perceive, camera and robot contracts [read: the docstring at
`tools/perception_audit.py:1-60`]. "0 leaks" means none of these rules fired. It is not evidence about the classes
below.

| Class | Why the auditor misses it | Findings |
|---|---|---|
| A. Differential leaks: which keys appear, which actions succeed, which refusal wording comes back | Each output is judged alone. A key that is present only for one true type, or an "ok" that comes only for one member, breaks no per-value rule. It takes twins (R1) | I4, I7, I8, I9, I10 |
| B. Knowledge laundered through a legitimate grant | The tier rule compares shown values with the knowledge store, and `count` writes inspect knowledge into that store, so the values then count as "known" | I3 |
| C. Correlations across seeds: seed-derived or authored values that predict hidden variants or roles | Each value is perceived legitimately (read-tier address, glance `spos`). The leak exists only across seeds, as a statistical relation. It takes R2 or R3 | I1, I2 |
| D. Cross-episode state | Every audit episode is fresh, and no audit agent types a handle from another episode | I5 |
| E. Existence oracles through the agent's own text | The output contains no true id ("something" is not an id). The information is in the contrast between a verbatim echo and "something", or between accepted and refused | I6 |
| F. Insertion order (not true-id order) | The order rule only flags true-id order | I11 |
| G. Rare commands and arguments | Random walks seldom issue `count` with a room scope, id-shaped words in `speak`, reservoir actions on spray bottles, or a remembered handle in a second episode. The audit's coverage of the 427 verbs times argument shapes is not measured | All of the above |
| H. Wrong but non-secret values | It audits secrecy, not correctness | S1, S2 |

---

## 5. Null results (searched for, not found)

* Refusal wording for entity parameters: one message and one category across 7 hidden states x 5 actions [ran].
* A typed true id behaves like garbage: refused in entity parameters, neutralised in free text, with an echo identical
  to garbage [ran: `probe_refusal.py`].
* JSON key order: 0 order-only differences over all look-alike groups and 8 novel types [ran]. List and dict order:
  handle order everywhere except I11 [ran, earlier attempt].
* Viewer, perceived-only page: no true ids, no hidden names or flags [ran].
* Closed-box contents through physical actions: only `shake` differs, which is legitimate [ran, earlier attempt].
  Note that the free-text echo does leak contents (I6).
* `private`/`owner` for notebook, wallet, jewelry box, mail piece, smartphone and laptop: no difference except
  `ask owner` [ran, earlier attempt].
* Portions poured from an unresolved plastic jug: identical across members [ran, earlier attempt].
* People in the dark with the geometric camera: 0 differences [ran]. Asleep vs awake: no difference in passive
  observation [ran].
* Variant leaks through the echo at scale: 1 of 139 variant specs, 0 realistic [ran] (row 8).
* Project auditor: 0 leaks in both camera modes on this tree [ran]. That is an absence of evidence only (section 4).

---

## 6. What I could not verify

* **Seed recovery on the private range.** Only the cost was measured. I did not show that the layout-only prefilter
  can be matched against what an agent observes, or how much exploration makes the fingerprint unique among 1.4M
  candidates.
* **Not re-run this session:** the large twin sweeps E1, E2 and E3 (4-8 minutes each), the order scan and the
  portions twins. They carry "[ran, earlier attempt]".
* **Not tested:**
  * the address through `ask` (my one probe was malformed: "ask requires target") or through the alarm-dispatch
    message;
  * backlit or far people in geometric mode (the FAR_WHO path);
  * pouring from the laundry and garden look-alike groups;
  * `camera_log` reachability beyond what E1 and the auditor cover.
* **I2 scope** is a lower bound: reset view only, 10 seeds.
* **I5 severity** depends on the attempt policy for hidden seeds, which the docs do not state as far as I read.

---

## 7. Simple version (for a non-coder)

The robot in this simulator is supposed to know only what its camera and sensors could tell it. I tried to cheat that
rule from the robot's side and found eleven ways. Three of them matter most:

1. **The house's street number gives away the secret version of a task.** The number comes straight from the house's
   hidden seed. On mail tasks, reading one envelope tells the robot which version it is in, even on the secret test
   houses.
2. **Things are always laid out in the same order.** "The third bottle from the left" is always the glass cleaner.
   A robot trained on practice houses learns the slot, not the skill.
3. **The "count" command looks closely at everything in the room from the doorway.** It tells identical-looking
   cartons apart without walking up to them.

Smaller leaks:

- an "unfamiliar object" can be told apart by what it can and cannot do;
- a private letter gives itself away when inspected;
- whatever the robot "says" reveals how many things are inside closed parcels and whether something happened in
  another room;
- object names stay the same from one try to the next, so a robot can remember them between tries.

Using these, my test programs passed two tasks every time while looking at far less than an honest robot must. The
project's own leak checker still says "zero leaks". By its design it checks each message on its own, so it cannot see
any of these. The fixes are small: shuffle positions, stop deriving printed text from the seed, make "count" need
reach, use one wording for every refusal, and give names that change every episode. I also recommend six new
automatic checks (section 3) that would have caught every one of these. One claim from the earlier write-up (a
sleeping person shown as awake) turned out to be my test's mistake, and I withdrew it.
