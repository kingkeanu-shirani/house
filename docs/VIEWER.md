# Episode replay viewer

`house_sim/eval/viewer.py` turns one episode into **one self-contained offline HTML file**: the episode data is
embedded as JSON, CSS and JavaScript are inline, there is no CDN, font, image or network request of any kind, and
the page works by double-clicking it on Windows (`file://`). Leaderboard and demo pages link to these files.

```
python -m house_sim --viewer SOURCE [--out PATH] [--view both|oracle|perceived] [--private] [--verify-spec]
python tools/export_viewer.py SOURCE [--out PATH] [...]          # same thing (thin wrapper)
```

The command writes the page and prints its path. Exit codes: 0 written, 1 unusable source, 2 test-split episode
refused (see below).

```python
from house_sim.eval.viewer import export_viewer, extract_data
path = export_viewer("results/run1/episodes.jsonl#3")            # -> Path of the html
data = extract_data(path.read_text(encoding="utf-8"))            # the embedded data object (schema below)
```

`export_viewer(source, out_path=None, view="both", private=False, verify_spec=False) -> Path` (the contract in
docs/EVAL_CONTRACTS.md section E, plus the two keyword options).

## Sources

| source | how frames are obtained |
|---|---|
| env replay `HouseEnv.save_replay(path)` (the compact `house-replay-v3`, or a `house-replay-v2` file from before it; kernel/replays.py; oracle or realistic) | re-run from the replay's start state, command by command (as `HouseEnv.replay` does; a multi-phase episode's `phase_done` records call `env.phase_done()`); diffs from the recorded per-step changes when the replay kept them (`keep_changes=True`, v2 files), else from the re-run's trace |
| harness command log `results/<run_id>/logs/<catalog_id>__<seed_tag>.json` (`house-harness-log-v1`) | if the log names its saved replay (`"replay"`), that replay is re-run; otherwise the episode is rebuilt with `tasks.runner.build(spec, seed)` from the authored spec and every command the harness **sent** is re-issued. `done` declarations, discarded commands and simulator errors are not environment steps; a `done` that ended a phase of a multi-phase episode (`"phase_done": true`) is re-run as `env.phase_done()` and listed as a `phase_done` step (ended phase -> next phase) |
| an episodes.jsonl record | `path/episodes.jsonl` (exactly one record), `path/episodes.jsonl#INDEX` (0-based), `path/episodes.jsonl#CATALOG_ID` or `#CATALOG_ID__SEED_TAG`, a record dict, or inline JSON text; its `"log"` path is resolved next to the episodes file |

A bare log file finds its record in `../episodes.jsonl` (agent, success, done flag). Harness logs carry the raw seed
for dev/train episodes; test-split logs do not, and the viewer then recovers the seed from the seed tag with the
split key and the private manifest (`splits.resolve_test_tag`, only with `--private`).

Default output: next to the source. A replay `X.json` gives `X.html`; a log or an episodes.jsonl record of run
`results/<run_id>/` gives `results/<run_id>/viewer/<log file stem>.html` (Windows-safe names: the harness writes
`dev:0` as `dev-0`). Private pages get a `__PRIVATE` suffix; a private page made from a log or record of
`results/<run_id>/` is written to `%USERPROFILE%/.house_ai/private_results/<run_id>/viewer/` (where the harness
keeps that run's private replays) rather than into the publishable results directory.

### Re-running and divergence

Every source is re-run in the current simulator, deterministically (realistic handles resolve the same way, the
environment observes after every step as the harness does). Each step's `ok` (and the recorded state hash, when the
replay was traced) is compared with the recording:

* a replay with recorded changes that diverges continues from them (the true state stays exactly what
  happened); the perceived view and live evaluation stop at that step and the page says so; the evaluations of
  those steps take the milestones from the recording's monitor record (the final evaluation's `milestones`, each
  with the step it was reached at: also milestones reached inside waits and the extended forms `{"pred", "count"}`,
  `{"pred", "held_for_s"}` and a phase's `<phase>/<name>`, which a predicate check on the state cannot reproduce);
* an untraced replay or a log that diverges keeps re-running, the first divergence is listed in the warnings and
  every mismatching step is flagged in the step list;
* a replay the current code cannot even load falls back to recorded changes (if traced) or is refused.

Other warnings: the spec hash differs from the one recorded in the episode record, action definitions changed,
the episode record's success disagrees with the re-run (taking the harness's `require_done` protocol into account),
logged commands that never reached the environment, and true ids masked out of perceived frames (see below).

## The page

* **Header**: catalog id and title, perception mode, camera mode (room or geometric, kernel v3.0), the physics data
  version the frames were computed with (`physics_version()`, kernel v3.0; when a replay was recorded with other
  physics data, or before v3.0, the badge also names the recorded one and a warning says the physical outcomes may not
  match the recording), split / seed tag (raw seed only for public splits), agent, step budget, task variant, spec
  verification status, source; warnings; the instruction.
* **Floor plan** (minimap): one tab per floor. The house layout is computed in Python: for each floor, the
  same-floor connection graph is laid out in BFS layers from the start room, ordered by neighbour barycentres,
  with room rectangles sized from `size_m`; the layer direction (columns or rows) is chosen to fit the panel.
  Doors are drawn between the doorway cells of the two rooms, stairs/elevators as floor-change markers. Visited
  rooms are tinted, the robot's room highlighted, its path drawn through the doorways up to the current step, and
  the robot shown at its cell. "Follow the robot's floor" switches tabs automatically. On perceived-only pages the
  plan holds only what the agent observed (see `--view perceived` below).
* **Room view** of the robot's current room on its 0.5 m cell grid: furniture footprints with labels (badges count
  the items on/in them), fixtures, floor items, rugs, wall-mounted things, windows along their wall, switches and
  outlets as wall dots, ceiling lights (lit when on), doors and doorways on the walls coloured open / closed /
  locked with the room they lead to, hazards and wet cells, people, pets, the robot (smaller when crouching or
  kneeling) and its trail since entering the room, and a dark overlay when the room is dark. Hover anything for a
  tooltip with its fields (and the contents of furniture and containers). Geometric-camera episodes add the camera:
  a line for the body's facing, a wedge for the head camera's field of view now and dashed wedges for the other
  frames of the step (6 m long, clipped to the room), and, in the oracle view, a tint on the floor cells the camera
  log counts as surveyed; the robot line and tooltip give the facing, head yaw, frame count and the room's coverage.
  The perceived view draws only what `obs.camera` told the agent (facing, head yaw, frame count, coverage): the
  seen-cells overlay is privileged.
  Physics and the robot body (kernel v3.0): in the oracle view a thing that shows a physics cue (sagging, wobbly,
  rolling, fell, tipped over, blown away, torn, collapsed shelves) gets a dashed amber outline and its tooltip lists
  the cues and its damage levels; a powered-down robot is drawn grey. The robot line and tooltip add the power state
  (when not on), the battery state (when not ok) and the fault kinds: in the oracle view the detected faults and,
  separately, the latent ones; in the perceived view only what `obs.robot` reported (`power_state`, `battery_state`,
  the kinds in `faults`).
* **Timeline**: scrubber, first / previous / play-pause / next / last, speed 0.25x to 8x; keys Left/Right,
  Home/End, Space. In the oracle view a strip under the scrubber marks the steps with physics consequences (amber),
  robot body events (red) and only time events -- notices, standing orders, phase boundaries (blue); hover a tick for
  the events, click it to jump there.
* **Step list**: step number, action, target/destination and parameters, ok or the refusal category, the message,
  sim time. Refused steps are highlighted, diverged steps marked; click a row to jump there. In the oracle view each
  step also lists its markers from the event log: every physics consequence (`phys.*` event kinds: fall, break, chip,
  crush, collapse, pullout, tear, tip, slide, roll, spill, overflow, scorch, ring, melt, shock, burst, blown, scratch,
  dent, budget; with the thing and whether the robot or something else caused it) and the body events `power_down`,
  `power_up`, `robot_fell`, `overload`, `fault_onset`, `fault_detected`, `fault_logged`, `fault_cleared`,
  `battery_low` and `battery_reserve`, then (blue, kernel v3.1) the phase events `phase_start`, `phase_end`,
  `phase_relocate`, the `standing_order_fired` orders and the notices the robot perceived in the step (`notice`:
  kind and text) -- at most 12 per step, then "+N". A phase end counts no step, so its row lists only the events that
  came with it (the phase's end and the next one's start), never the previous action's again.
* **Evaluation at this step**: success / in progress / not successful, goal fraction, score, failures, milestones
  (reached ones with step and sim time from the episode's temporal monitor, pending ones greyed), harm flags, safety/privacy/social/authority blocks,
  invalid steps, questions, budget; the success criteria in the oracle view.
* **Changes**: the state diff of the step (oracle view: the step's event markers first, then the recorded changes,
  most relevant first; bookkeeping such as action logs, the event log, battery and the body's energy counters, pack
  temperature, lowest gauge and first-time log, time, underscore-internal fields is dropped, small
  temperature/wetness drift ranked last, 24 rows per step with an "N more" count) or, in the perceived view, what
  changed in the agent's observation (things that appeared, disappeared or changed, holding, posture, channels).
* **View toggle** for realistic-perception episodes: **oracle view (true state)** and **agent's perceived view**
  (the observation contents: opaque handles, buckets, noisy size estimates, blocked cells, perceived hazards,
  people's cues, sounds/smells/messages channels). Oracle-perception episodes have only the oracle view.
* Light and dark themes (system preference, toggle remembered in the browser when storage is available).

`--view oracle` exports only the true state. `--view perceived` exports only the agent's view (realistic episodes):
no oracle frames, no state diffs, no event markers, no success criteria; evaluation failure labels and messages use
handles, so the file contains nothing the agent could not see apart from the evaluation outcome.

That includes the floor plan: a perceived-only page never ships the true layout. Its plan is built
(`viewer.PerceivedMap`) only from the observations the agent was given during the re-run: the rooms it observed
(`room_id`), laid out from their observed label, type, floor, outdoor flag and size (`size_m`, else the grid), the
doorways listed in those observations with the cell seen from each observed side, and, as bare ids, the rooms that
observed exits or connections named (or that the robot's known map holds) but that it never observed ("stubs": no
label, type, size or floor; the minimap draws them as dashed markers "-> id (not observed)" at the doorway, and a link
to a stub has no far-side cell). After a divergence (perceived frames unavailable) a perceived-only frame names the
robot's room only if the agent had observed it. Pages that also carry the oracle view (`both`, `oracle`) keep the full
true plan.

## Test-split safety

An episode is treated as test split when its record or log says `"split": "test"`, its seed tag starts with
`test:` (or the file-name form `test-`; both matched ignoring case and padding), its seed is in the private range
(`splits.is_private_seed`) or it is listed in the private manifest (`splits.is_test_seed`; an unreadable manifest
fails closed). Without `--private` (`private=True`) the viewer refuses before reading the command log, writes nothing
and exits with code 2. With it, the page title and a banner say **PRIVATE - test split**, the raw seed is never
embedded and the default file name ends in `__PRIVATE`. Keep such pages out of shared folders.

Seeds are normalized before any check (`viewer.normalize_seed`): an int, or a string or float that represents an
integer exactly (`"900000123"`, `" 900000123 "`, `900000123.0`, `"900000123.0"`) becomes that int, and the record's,
log's and replay's seeds are all checked as ints, so the type a seed was written with cannot bypass the guard (an env
replay carries no split or seed tag: its seed is the only signal). Any other seed (a bool, a fractional or non-finite
number, other text, a list or object) is refused as unusable (exit code 1). A replay that has a start state but no
seed cannot be shown to be public: it is refused without `--private` and exported only as a private page. The page's
`seed` is always that int, never the raw value. A private page also drops a seed tag that is not a keyed `test:` tag
(`seed:N`, `dev:N` would spell out the seed) and replaces the seed's digits in the source file name and title with
`SEED`.

## Spec status badge

When the episode's catalog id has an authored spec, the header shows whether it is verified: a passing record in
`reports/verification.jsonl` whose `implementation_hash` equals `house_sim.audit.fingerprint()` and whose spec
hash matches ("verified (current evidence)"), otherwise "unverified (no evidence / evidence failing / evidence
stale: ...)". Unverified specs are never shown as verified. `--verify-spec` instead verifies the spec now with
`house_sim.tasks.verify.verify_many` on the dev seeds, in the spec's own perception mode ("verified now" / "failed
verification now"); it takes as long as `--check` for that one spec.

## Data format (embedded JSON, `format: "house-viewer-v1"`)

`<script type="application/json" id="episode-data">` holds the object; `extract_data(html)` returns it. `<`, `>`,
`&`, U+2028/2029 and `://` are escaped so the block cannot end early and contains no URL literal; `JSON.parse`
restores every character.

```
meta     {title, catalog_id, instruction, perception, camera, view, views, source_kind, source, split, seed_tag, seed?,
          agent, privileged, private, steps, step_budget, time_budget_s, risk_level, fidelity, variant,
          milestones: [names: the task's, then each phase's as <phase>/<name>], phases?: [ids], success_rules?,
          warnings: [...], done_declared, end_reason, spec_status, diverged_at?,
          physics_version?, physics_recorded?}
layout   {rooms: {rid: {l, ty, f, o, cols, rows, w, h}},
          floors: [{f, name, W, H, rooms: [[rid, x, y, w, h], ...]}],      # meters
          links: [{id, a, ax, ay, b, bx?, by?, k}],                        # doorway cells in rooms a and b
          stubs?: [rid, ...]}                                              # perceived-only pages (see above)
steps    [{a, tg?, de?, pa?, ok, ca?, m?, am?, ts?, wl?, dv?}]            # N steps (a phase end: a "phase_done",
                                                                     #   tg the ended phase, de the next one)
frames   N+1 oracle frames, delta-encoded                                  # "both"/"oracle"
pframes  N+1 perceived frames, delta-encoded                               # realistic, "both"/"perceived"
evals    N+1 evaluations, delta-encoded {s, g, sc, f, bx, m: {name: [step, t]}, hf, bl, iv, q, rs?} or {na}
diffs    N per-step diffs {c: [[path, before, after, rank?], ...], n?}    # "both"/"oracle"
marks?   {frame index: [[event kind, detail], ...]}                       # "both"/"oracle", frames with events only
```

`physics_version` is the physics data digest (`kernel/physics.py` `physics_version()`) of the code that computed the
frames (the recording's when the current simulator cannot load a replay); `physics_recorded` appears only when a
replay was recorded with different physics data ("before kernel v3.0" for replays without `metadata.physics_version`).

Marks: for frame k (the state after step k; key "0" is the reset), the event-log entries of that step whose kind is
`phys.*` or one of the body events listed under "Step list", in log order, as `[kind, detail]` with a short detail
(the thing and `robot` / `exogenous` for physics, the cause for power and falls, `kind part (source)` for a fault
onset, `fault (how)` for detection and clearing, `kind: status` for a logged fault, the gauge for battery
thresholds); at most 12, then `["more", "+N"]`.

Layout: on pages with the oracle view, the true plan (every room and connection); on perceived-only pages, the
agent's plan (`viewer.PerceivedMap`): only rooms it observed, the links it saw (`bx`/`by` absent when the far side is
a stub) and `stubs`, the never-observed rooms its exits named (ids only, not in `rooms` or `floors`).

Steps: `a` action, `tg`/`de` target/destination as issued (handles in realistic runs), `pa` other parameters,
`ok`, `ca` refusal category, `m` the message (the simulator's raw message in oracle/both pages; the agent's
message in perceived-only pages), `am` the agent's (handle-translated) message when it differs, `ts` sim time after
the step, `wl` wall seconds (harness logs), `dv` divergence note.

**Delta encoding**: `frames[0]` is complete; `frames[k]` is a JSON merge patch (RFC 7386) over the decoded frame
`k-1`: keys present replace (objects merge recursively), `null` deletes, arrays are replaced whole.
`viewer.decode_deltas(items)` (Python) and the page's `decode()` (JavaScript) implement the same rule.

Oracle frame: `r` room, `rp` robot cell, `po` posture, `bat`, `h` {hand: entity}, `H` held entities' records, `t`
sim seconds, `clk` simulated date-time, `dk`/`sp`/`fl`/`so`/`sm` docked / stopped / fallen / standing on / speed mode,
`pw` power state when not "on", `bs` battery state (`body.battery_state`) when not "ok", `ft` / `lf` the kinds of the
active detected / latent faults (kernel v3.0 robot body),
`cam` (geometric camera only) {`fd` body facing, `hy` head yaw (grid degrees: 0 east, 90 south), `hf` horizontal field
of view, `fc` frames this step, `fy` [frame headings in this room], `sc` the room's surveyed cells (row-major "0"/"1"
from `camera_log.seen_cells`), `cv` its coverage}, and `R` {room: room state} for every room visited so far. Only the current room's state is refreshed (other rooms
keep their state from the robot's last visit), so moving around costs one room state the first time and small
patches afterwards. Room state: `e` {entity id: record} (direct children of the room with cell `x`, `y`, footprint
`w`×`d`, class `k` furn/fix/item/rug/wall/dot/win/ceil, `b` blocks path; nested items with `in`/`rel`; `l` label,
`ty`, `ca`, `sz`, `rot`, `mh`, `op`, `lk`, `on`, `br`, `st`, `ph` physics cues {sagging, wobbly, in_motion,
collapsed_shelves, fell, tipped_over, blown_away, torn: the values present}, `dm` damage levels {kind: 0-1}, `so`
soil, `we` wetness, `tc` temperature (0.5 C), `ma`, `co`, `xf` extra fields, `ch` children ids), `p`/`a` people and
pets (`l`, `x`, `y`, `po`, `ac`, `mo`, ...),
`z` hazards `[x, y, kind, source]`, `wc` wet cells, `c` connections `{id, to, k, x, y, dr?, op?, lk?}`, `lt` light.

Perceived frame: `r`, `rp`, `po`, `bat`, `h` held handles, `dark`, `so`, `pw` / `bs` / `ft` (from realistic
`obs.robot`: `power_state` when not "on", `battery_state` when not "ok", the fault kinds `faults` lists), `cam`
(geometric camera only: `fd`, `hy`, `fc`, `cv` from `obs.camera`), `ch` channels (sounds, smells, messages,
last_answer, pending, weather_now, illuminance_lux; truncated JSON), and `R` {room: {e: {handle: observation
record (+ `_w`, `_d`, `_k` drawing hints from perceived size and kind)}, p, a, z (perceived hazards), b (blocked
cells), c, lt}}. Perceived frames never contain a true entity id: they are built only from `env.observe()` output,
and as defence in depth any true id found there (outside descriptive text such as labels) is replaced by `?`
and reported as a perception leak in the warnings.

## Size

A 300-step realistic episode with both views is about 0.8 MB (the content of the replay it came from is about 17 MB
when traced; the compact replay file about 92 KB, 470 KB with its recorded changes):
frames are deltas with short keys, temperatures are rounded, diffs are ranked and capped, messages truncated to 600
characters and channels to 240. The test suite checks a 300-step episode stays under 3 MB.

## Not done

* No pixels: the room view is the symbolic grid the simulator uses; the camera overlay shows poses, fields of view
  and surveyed cells, not what a frame looked like (occlusion is in the step's perceived records, not drawn).
* Items on surfaces are listed in their furniture's tooltip (and counted on its badge) rather than drawn at their
  surface position.
* The floor-plan layout is schematic (graph layers), not the house's real geometry, which the simulator does not
  model beyond per-room grids and doorway cells.
* `house-replay-v1` (legacy) replays are not supported.
* Replays, logs and records from before kernel v3.0 carry no camera mode: they re-run in room mode (the spec's own
  mode, as they ran).

Tests: `python -m unittest tests/test_eval_viewer.py` (offline; builds episodes locally).
