# Handoff WP7 PERCEPTION EVIDENCE (VERIFY_HARDENING section 25; sections 15.2, 15.3, 9.1, 13.2, 10.3)

Files: `house_sim/kernel/realism.py` (changed), `house_sim/tasks/perceive.py` (new), `house_sim/data/verify/derivations.json`
(new), `tools/perception_audit.py` (changed), `tests/test_perceive.py` (new, 48 tests). Nothing else was edited.

## What landed

1. **Act log** (15.3, `kernel/realism.py`, hidden, outside world state). `Realism.acts`: one record per action, taken in
   `inbound` (after the handles are resolved, before the action runs) and completed in `commit_grants` from the world's
   action trail (the action as run, a scheduled `run_pending` included):
   `{"step", "t", "action", "ok", "args" (converted, true ids), "ids" (entity ids named, in argument order),
   "targets": {eid: {"unresolved", "group", "novel", "level", "ident", "seen", "fields": {field: level > glance},
   "values": {field: true value}}}}`. An availability probe (`env.affordances`) and `phase_done` log nothing; a command
   refused before it named anything is logged with `ids: []`, `ok: false`.
2. **Grant log** (`Realism.grants_log`): `{"step", "t", "id", "level", "values": {field: true value}}` per grant a
   successful step applied (prior knowledge at setup included, step 0). Never-tier fields are not logged.
3. **Timed observation log**: `Realism.observed` is now an `ObservationLog` (a list: every reader that walks it sees
   exactly the items it always did) with `stamps[i] = (time_s, step)` per item and, for an observation,
   `evidence[i] = {eid: {"levels": {field: level > glance}, "truth": {field: true value, "label": ...}}}`. True values
   are kept as scalars, short scalar lists, or `"#<digest>"`.
   Acts and grants are in `Realism.dump()` / `load()` (keys `acts`, `grants`) only when non-empty; the observation log
   is not (as before). **Superseded by WP7-fix (A-spec-2):** the observation log (items, stamps, evidence) and
   `record_observations` are in the dump (key `observed`) whenever recording is on or anything was recorded; see
   `handoff_WP7-fix.md`.
4. **Split knowledge signature** (15.2). A move of a known thing by anyone but the robot (the robot's own carries and
   pushes already kept knowledge) is noted during the step and decided by `Realism.settle_moves()` (run at the next
   observation, the next step, a dump, and by `perceive.index`; idempotent): it is **tracked** when the robot perceived
   the thing in its own lit room when the step began and when it ended, did not change rooms, and the thing stayed in
   that room -- with the geometric camera also identified (view level identify) at both places. A tracked move keeps
   the entity's identity level and the grants of `realism.INTRINSIC_FIELDS` (read-tier print, label / name, material,
   mass, size, capacity, what it contains: `Knowledge.retain`); every other grant lapses as an unseen move makes it
   (kept under the entry's `"lapsed"` for the remembered rendering, `Knowledge.stale`). An untracked move lapses
   everything, as before. `Realism.track_moves = False` restores the old rule (tests use it).
5. **Twin-invariant names of unfamiliar things** (phase-1 carry-over: closeout_REV finding 7, confirmed by
   closeout_VER2; INT3's not_fixed row on admin_inventory's request). The outbound rewrite named an unfamiliar thing by
   its own type, so a domain's generic wording naming that type ("Hold the padlock", "Hold a pencil", "Hold a luggage
   scale") came back rewritten for that twin alone. Now an unfamiliar thing's names are those of **every type it could
   be** (`Realism.unfamiliar_names()`: every portable type's label and type name, every type's for an unfamiliar fixture;
   a trie regex built once per registry): rewritten where a phrase names a thing by one of them -- after an article or
   demonstrative in free text ("the padlock" -> "the unfamiliar medium object", "a pencil" -> "an unfamiliar medium
   object": the article follows) or as a whole JSON label / name / title / descriptor -- unless it is a name of another
   identified thing the command named (its label, a word of it, its type, its category), a room or a resident. Its own
   label is still rewritten anywhere when it is its own (not its type's default). Fixture words ("No free floor space")
   and the kernel's renderings (shape, colour, print) are left alone. Look-alike groups are unchanged.
6. **`house_sim/tasks/perceive.py`** (new): the evidence index and the gating rules; see Interfaces.
7. **`house_sim/data/verify/derivations.json`** (new): the closed table of derivations (count, sum, mean, min, max,
   argmin, argmax, diff, rate, duration, convert, any, all, none_found) with inputs, support rule, tolerance propagation
   and value rule; evidence tier words -> levels (`smell` = inspect, `measure` = 4, ...); instrument precision by
   field; the drift rule; quantity -> entity fields (with unit factors).
8. **`tools/perception_audit.py`**: a `record` rule in every observation and action message (a key naming a hidden
   evaluation record -- speech_log, mess_flows, action_log, writes, acts, grants_log, failures, unmet -- or a value that
   is an evaluator label: house.*, perceive:*, settle:, later:, strict:, timing:); `audit_run(env)` audits a finished
   run after the fact (id, secret, hidden, record and tier rules, the tier rule with the levels each observation
   recorded; messages against the act log's command of their step) -- **the battery (WP6) runs it over its generated
   runs**; `--runs N` audits the reference and wrong-plan runs of the first N realistic specs that way; a **wording
   probe** (in every default run: unfamiliar padlock / pencil / luggage scale / kettle / knife beside a mug, latch /
   mark / weigh_luggage) that catches the REV tell on HEAD's kernel (1 leak) and finds none now; `--read-aloud-first`
   (opt-in) runs the read probe in the reverse order for the people row of closeout_breakages (opt-in until that domain
   row lands: it would fail G4 on another owner's open row). The tier rule is one helper (`_tier_finding`) for the walk
   and the post-run audit.

## Interfaces (callers may code against these)

```python
from house_sim.tasks import perceive

perceive.enable(env) -> bool               # record_observations on + the first observation (False without realism)
perceive.index(env) -> perceive.Index      # also perceive.build_index
#   Index.fields[(eid, field)] -> [Obs(t, step, level, shown, truth, source)]  source "obs" | "grant" | "reading"
#   Index.views[eid] -> [View(t, step, identified, label)]; .agents, .rooms, .readings, .notices, .heard, .acts
#   Index.entries(eid, field, before_step=None, level=0); Index.seen(eid, before_step=None, identified=False)
#   Index.act_at(step); Index.step_of_time(t, action=None)
perceive.report_gaps(env, task, answers=None, index=None, bindings=None) -> [{"report", "reason"}]
perceive.act_gaps(env, task, answers, index=None, branch_diff=None, bindings=None)
#   -> [{"kind": "acted_unresolved" | "acted_unperceived" | "branch_unperceived" | "claim_unperceived",
#        "answer", "entity", "field", "step", "action"(, "branch", "n")}]
perceive.claim_founded(env, claim, index=None, step=None) -> bool
perceive.agent_labels(env, task, answers, index=None, branch_diff=None, bindings=None) -> sorted unique labels
#   perceive:acted_unresolved:<e>  perceive:acted_unperceived:<e>.<field>  perceive:branch_unperceived:<branch>
#   perceive:report_unperceived:<n>  perceive:claim_unperceived:<n>        ([] without realistic perception)
perceive.supports(idx, env, eid, field, value, before_step, tol=0.0, as_of=None, need=None) -> Obs | None
perceive.derive_support(idx, env, derive_spec, value, before_step, tol=0.0, as_of=None) -> (bool, reason)
perceive.resolve_answers(answers, bindings) -> answers with "{name}" references and "value" from "bind"
perceive.command_class(action) -> "PERCEPTION" | "NAVIGATION" | "COMM" | "DEPOSIT" | "WAIT" | "EFFECT"
perceive.tier_level(word), perceive.precision(field), perceive.tables(); perceive.DerivationError

realism.acts, realism.grants_log, realism.observed (ObservationLog: .stamps, .evidence, .note(snapshot))
realism.settle_moves(); realism.track_moves; realism.unfamiliar_names(every=False) -> (names, pattern)
Knowledge.levels(eid, record) -> {field: level}; Knowledge.retain(eid, record, keep); realism.INTRINSIC_FIELDS
perception_audit.audit_run(env) -> [(kind, owner, detail)]; perception_audit.check_hidden_records(value, leaks, where)
```

Rules, briefly:

* **Before** means an earlier step: evidence stamped step k (the observation at the end of step k, a grant committed
  in step k, a reading in step k's message) supports an act, report or claim of step k+1 or later.
* **Reports** (`report_gaps`, 15.2): legacy `reports.<target>.<field>` predicates and typed finding / status /
  measurement / count / none_found records a `report` / `report_set` predicate scores. A value is supported by an
  observation at the field's tier before the report within max(precision, tol) whose truth moved by at most tol until
  the report (the act log's values at the report) -- or any time with `"as_of": "observed"` on the predicate (legacy
  rule key or the report expectation). A report linked to an answer (`rule["answer"]`, or the answer whose value
  equals the predicate's value) uses its `derive` (derivations.json) or its `evidence` (a judged word: the evidence
  items perceived first). A report field that is no field of its subject needs one of the two. A never-tier field (an
  owner, an intent) is founded only by being told: the value said in a resident's message or in the answer of one of
  the robot's own COMM commands before the report (`Index.told`; **WP7-fix, A-gaming-1:** a person's own words only --
  never the robot's utterances echoed in a speech command's message, who answered, the question's fields carried
  back, or a stretch quoting the robot's words). A glance's bucket word ("half") founds no number.
  Findings and statuses: the vocabulary's evidence entries (field on the subject or near it, or a notice), perceived
  before the report.
* **Acts** (`act_gaps`, 15.3): for an `entity` / `set` answer with a `group`, every successful EFFECT, DEPOSIT or
  report command naming a member is decisive; it needs every `evidence` item (`{"field", "tier"}`) of that member
  perceived first -- for an `entity` answer, by elimination, every other member's -- and fires `acted_unresolved`
  when the member was an unresolved look-alike or unfamiliar thing at the act and the evidence did not settle it. A
  `branch` answer's evidence items (`{"entity", "field", "tier"}`) must be perceived before the first command of
  `branch_diff`; **without branch_diff every EFFECT, DEPOSIT and report command is decisive (strict default: pass the
  B8 diff set for references)**; **WP7-fix, A-gaming-4:** for an agent (`agent_labels`, `act_gaps(agent=True)`) also
  every command that does what an effectful diff command does whatever its verb. Claims: every factual claim of `facts.speech_log` with a truth mapping; WP4's stored
  `"founded"` is used when present (False is a gap, None never is).
* **Claims** (`claim_founded`): uncertain and non-factual claims are founded; state / issue claims need an evidence
  entry of the state / issue (or the claim's `fields`) perceived; quantity claims a measure-tier observation or a
  reading of the subject's field for the quantity within precision; none_found every thing in scope seen; a person or
  pet seen; a room observed; a person who told the robot the same claim first founds it. Without a step, mid-step
  (WP4's core say) uses the steps before this one.

## Phase-1 carry-over

* closeout_REV finding 7 (latch "Hold the padlock" / mark "Hold a pencil"), confirmed by VER2: fixed at the root in
  realism.py (item 5). Regression tests fail on HEAD's kernel (checked in a `git archive HEAD` copy: the REV repro gives
  DIFFER on HEAD, SAME now, room and geometric; the new wording probe reports 1 leak on HEAD's kernel, 0 now):
  `tests/test_perceive.py` UnfamiliarTwinWordingTests, AuditorTests.test_the_wording_probe_finds_no_twin_apart.
* closeout_INT3 not_fixed "admin_inventory's kernel request ... single-pass label substitution": the same fix
  (`luggage_scale` is a twin of the wording probe).
* closeout_breakages row 2 (people read_aloud before read, the optional perception-audit twin): `--read-aloud-first`.
  The fix itself is people's (care._printed_fields).
* Rows left to their owners: every other closeout_breakages row and REV / VER2 finding is a domain or gate-tool file.

## Legacy outcomes (WP7 may change none)

* Nothing WP7 adds to realism writes world state or issues a handle: the act, grant and observation logs are
  bookkeeping, `verify.py`'s legacy perceivability checks are untouched, and no authored spec has an unfamiliar thing
  (the name rewrite only acts for one).
* The split signature is the one behaviour change. Census (scratch `split_census.py`, every trial of every authored
  spec with perception forced realistic, room camera, and of the 51 realistic specs with the geometric camera: 1,470
  trials): the split kept knowledge in 20 trials of 4 specs (T-d990c58b123a5e-01, A-a67431128da2e1-01,
  T-868b9cc704adf1-01, T-9f83cbcee53083-01, room camera, all seeds). An A/B of those 20 trials with the split on and
  off (`Realism.track_moves`): verdict, the reference's final observation digest, the reference / idle / wrong-plan
  state digests and every step message identical; only some wrong plans' final observations (not recorded by the
  gate) differ, e.g. a hefted mass bucket ("medium") still shown after the thing was knocked aside in view. The one
  failing census trial (T-dda4e199f3f5b0-01 seed 1, forced realistic) is the allowlisted known failure.
* Unit suite on the shared tree (`green_tests.py 3`): 5,192 tests per mode, 0 failures, normal and strict.
  All authored specs in their own mode (combined file, `--check --workers 3`): 243 passed, 0 failed.
  `perception_audit.py 6 150` (G4's run): room 0 leaks 0 crashes (84 probes), geometric 0 leaks 0 crashes.
* Cost (section 22): profiled over 6 realistic references, everything WP7 adds to realism (act log, grant log,
  evidence snapshots, settle_moves, the name rewrite) is about 0.3 s of 63 s, under 0.5 %; building the index and
  the gaps of one run is a few milliseconds.

## Deviations from the spec

* `INTRINSIC_FIELDS` is realism's own list (6.3's identity class plus intrinsic physical fields), not read from WP1's
  `watch_fields.json` at run time, so realism's behaviour does not move with another package's data.
* "Tracked identity" is decided from the robot's perception at both ends of the step (the Mind's perceived ids, the
  dark flag, room, and the geometric view level), not from the identify level after the move alone: a look-alike
  identified at a new place could be its twin (that would track true identity through an unseen swap).
* Decisive acts use the 10.1 command lists until WP6's `house_sim/data/verify/actions.json` exists (then that file).
* `branch_diff` is an argument (WP5 / WP6 compute the B8 diff set); the default is strict.
* `report_gaps` replaces the legacy checks only for callers that choose it (WP5 for schema 4); `verify.py`'s
  `perceivability_gaps` / `report_perceivability_gaps` are untouched, so legacy outcomes cannot move.

## Known gaps

* The name rewrite of unfamiliar things keeps a name that is also a name of another identified thing the command named:
  a domain message naming the unfamiliar thing by its true label when that label equals the other thing's name (an
  unfamiliar mug beside a mug) still reads differently from a twin's. Domains naming things by label is the domain's
  bug (they name by id); the identify-first refusals of mechanisms / navigation_robot remain the domains' rows.
* The rewrite garbles generic wording for every twin alike ("Hold the unfamiliar medium object") -- the price of being
  the same whatever it is; a domain identify-first refusal gives the better message.
* Count derivations accept a member seen at recognize or better (geometric) or identified (room); an unresolved
  look-alike does not count.
* Index observations need `record_observations` from the start (`perceive.enable(env)`, or the verifier's flag);
  without it the index has the act and grant logs only. (A run forked from a snapshot now carries the observation log
  and the recording switch too: WP7-fix, A-spec-2.)

## How to test

```
python -m unittest tests.test_perceive                    # 48 tests; also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core
python tools/perception_audit.py 2 40 --workers 3 --runs 6   # walk + probes (wording included) + post-run audits
python tools/perception_audit.py 0 0 --workers 3 --read-aloud-first --verbose   # the opt-in read order
```
