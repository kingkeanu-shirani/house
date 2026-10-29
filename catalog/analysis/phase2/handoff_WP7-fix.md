# Handoff WP7-fix (run A review findings in WP7's files: A-spec-2, A-gaming-1, A-gaming-4)

Files: `house_sim/kernel/realism.py`, `house_sim/tasks/perceive.py`, `tests/test_perceive.py` (11 new tests, 59 in
all), `catalog/analysis/phase2/handoff_WP7.md` (superseded statements marked). Nothing else was edited. Every finding
was confirmed by its adversarial verifier and is fixed at the root; each has a regression test that replays its
repro and fails on the tree before the fix (checked in a scratch copy of the working tree with the three behaviour
changes reverted: 11 of 59 tests fail there, 0 here).

## What landed

1. **A-spec-2: forked runs judge evidence as runs from the build** (`kernel/realism.py`). `Realism.dump()` now holds
   the observation log as evaluation reads it -- `"observed": {"record": bool, "items": [...], "stamps": [[t, step]],
   "evidence": [...]}` -- and `load()` restores it as an `ObservationLog` (items exactly as recorded, stamps as
   tuples, evidence snapshots) with `record_observations` as saved. `HouseEnv.save` / `HouseEnv.load` (engine, which
   only calls `realism.dump()` / `load()`) therefore carry it; 10.5's "everything evaluation reads" now holds for
   perception evidence. The key is written only while recording is on or something was recorded, so the dump of a
   run that records nothing is exactly what it was; a dump without it restores an empty log with recording off (as
   it was saved). `track_moves` is saved when off (`"track_moves": false`) so a test switch survives a fork too.
   Repro outcomes: `p_fork_claim.py` straight founded=True (5 observations), forked founded=True (5) (was False / 0);
   `p_fork_say.py` straight and forked both founded False, said False, `['perceive:claim_unperceived:1']` (the fork
   was founded None, said True, `[]`).
2. **A-gaming-1: the robot's own words never tell it a never-tier fact** (`tasks/perceive.py`, `Index.told`).
   `told(value, before_step, subject=None)` now reads only a person's own words (`Index.person_statements`):
   * heard messages (`obs.messages` whose speaker is not the robot), and
   * the answer fields (`REPLY_KEYS`: value, said, picked, answer, reply, refers_to) of a JSON reply that names who
     answered (`"by"`: a person, not the robot) to one of the robot's COMM commands -- never the fields that carry
     the question back (about, aspect, detail, key, topic, a clarify-back's options) nor `by` itself.
   A speech command's message (speak / announce / confirm / explain / refuse / handoff echo the robot's text), a
   typed report's receipt (it echoes the reported value) and a call's or alert's receipt are not a person's words.
   In a person's words, a stretch of `ECHO_WORDS` (3) or more consecutive words repeating one of the robot's own
   utterances or string arguments up to that step is its words quoted back and is left out, and naming two or more
   of the candidates of one list the robot offered (a list argument: ask's `options`) singles none out (those
   mentions are left out). `subject` (supports() passes the report's subject): a reply to a question that named only
   other things (its act's `ids`) tells nothing about this one. Words compare case-insensitively with a possessive
   's dropped ("Sam's" is "sam"), as whole words or phrases. Repro outcomes: `r3_told.py` told(sam/ann/mia) False after
   the robot's own question, supports() None (was Obs(source='told')); `r4_report_gating.py`
   `['perceive:report_unperceived:mug_1.owner']` with and without the robot's speech (was `[]` with it).
3. **A-gaming-4: an agent's branch decision is gated whatever verb it uses** (`tasks/perceive.py`, `kernel/realism.py`).
   `act_gaps(..., agent=False)`; `agent_labels` calls it with `agent=True`. With a branch diff set, a decisive act is
   (as before) a successful command matching a diff command exactly, and for an agent also any successful command
   that **does what an effectful diff command does** (`_does_what`):
   * **placement**: the act put the diff deposit's target at the diff deposit's destination, whatever its verb or
     arguments -- from the act log's new `"placed": {id: location}` (realism `_placements`: every thing whose location
     a successful step changed, read from the world journal before commit; absent when nothing moved);
   * **role**: the same kind of effect (`_effect_kind`: "report"; "deposit" = a DEPOSIT command or an EFFECT command
     naming a destination, e.g. manipulation's stack / insert; "effect" = any other EFFECT command), the same target
     and destination, and no argument both commands name set to another value (a common `set(thermostat, 20)` does not
     stand for a branch's `set(thermostat, 22)`; `adjust(thermostat, value=22)` does).
   Not decisive: perception, navigation, speech, waits (even when someone else moves the thing meanwhile), a refused
   command, fetching the thing (`pick_up` is an effect, the diff command a deposit), a deposit at another destination.
   The reference rule (`act_gaps` default, which WP5's `reference_run` / `mode_trials` call) is unchanged: the
   reference's own diff commands, exactly. Repro outcomes: `r8c_branch_bypass.py 0 1 3` intact seeds 0 and 3:
   'raw approach + stack' fails=True `['perceive:branch_unperceived:intact']` (was fails=False, `[]`); torn seed 1
   unchanged (fails on its goal); `r8_branch_gate.py 0 1 3`: every reference passes and is gated_ok, every blind policy
   fails. (r8c's diagnostic tail calls `perceive.act_gaps` directly -- the reference rule -- and still prints
   `gaps: []` for stack; the verifier's agent path is `wrong_run -> _finish -> agent_labels`, where the fix applies.)

## Interfaces (changed or new)

```python
perceive.act_gaps(env, task, answers, index=None, branch_diff=None, bindings=None, agent=False)  # agent: the agent rule
perceive.agent_labels(env, task, answers, index=None, branch_diff=None, bindings=None)          # unchanged; agent=True
Index.told(value, before_step, subject=None) -> bool
Index.person_statements(before_step, subject=None) -> [(step, text)]   # heard texts and reply answer leaves
perceive.ECHO_WORDS = 3; perceive.REPLY_KEYS
realism act log record: + "placed": {id: location}  (successful steps that moved things only)
Realism.dump(): + "observed": {"record", "items", "stamps", "evidence"} (recording on or anything recorded),
                + "track_moves": False (only when off); Realism.load() restores both
```

## Deviations from the reviewer's suggested fixes

* A-gaming-4: the reviewer suggested matching "any EFFECT / DEPOSIT / report command whose target or destination
  plays a role that a diff command names (or that the branch evidence names)", with WP5 passing a role-level set.
  Taken literally that makes fetching the branch's thing decisive (the book is the diff `place`'s target, and
  `pick_up(book)` is common to every branch), so every reference and every legitimate agent that picks the thing up
  before inspecting it would fail. The rule here matches by effect kind, target and destination, and by where the
  act put the thing; it is derived inside perceive from the diff set WP5 already passes (no WP5 change needed).
* The reference rule stays exact (15.3 defines the reference's decisive commands as its diff set; they are its own
  commands, so exact matching is complete for it).
* A-gaming-1: beyond the reviewer's fix (`speaker != robot`, the reply part of an ask), the reply's `by` (who
  answered) is excluded too -- before, asking Mia anything founded `owner = mia` through `"by": "mia"` -- and replies
  are scoped to the subject (an ask about another thing tells nothing about this one).

## Known gaps

* `told` stays lexical inside a person's words: a reply that mentions a name for another reason ("That's not my
  request; ask Sam.") founds it for a question about this thing or about nothing in particular; heard messages carry
  no subject and are not scoped. A person quoting three or more of the robot's words in a row ("Is this box Sam's?
  Yes.") founds nothing from the quoted stretch (the simulator's answerers do not quote; a possible false negative).
* The role rule covers locations and same-role commands. A diff deposit without a destination (`dispose(milk)`)
  matches other destination-less deposits of the same thing, but a `put(milk -> bin)` for it is not matched, and a
  liquid moved by another verb with another destination (`dump(jug)` for `pour(jug -> sink)`) is not either: the
  diff set carries no outcome of the reference's command. Closing it needs each diff command's outcome (request to
  WP5 below).
* Save files of recording runs are larger by the observation log: T-8fb945d433137c-01's 57-step realistic reference
  dumps 1.4 MB of observations (of a 4.4 MB state file) in 0.08 s; B1's `_confirm_sample` saves idle runs (small).

## Legacy outcomes (WP7 may change none)

* Nothing here writes world state: the dump / load change only adds hidden bookkeeping to snapshots, `placed` is
  hidden act-log data, `told` and the agent rule are read only by schema-4 evaluation (`report_gaps`, `agent_labels`
  in a non-own mode), and no authored spec is schema 4.
* Unit suite on the shared tree (`green_tests.py 3`, 156 file runs): 5,401 tests per mode, 0 failures, normal and
  strict. All 243 authored specs in their own mode (combined file, `python -m house_sim --check <all> --workers 3`):
  243 passed, 0 failed. `tests/test_perceive.py`: 59 OK normal, strict and `HOUSE_SIM_DOMAINS=core` (7 skips there:
  scenes needing domain types, people's owner answer or manipulation's stack). py39_lint clean for every WP7 file;
  all files LF.

## Requests

* WP5: to close the remaining role-rule gap, give each branch-diff command the outcome of its step in the reference
  (its act-log `"placed"`, or the fields its step changed), so perceive can match an agent's act by what it did to a
  liquid or a destination-less deposit too (an optional key on the diff command; perceive does not read one yet --
  the matching side is a small addition to `_does_what` once the key exists).
* WP8: document in docs/PERCEPTION.md / KERNEL_API.md: the observation log in `Realism.dump()` (`observed`,
  `track_moves`), the act log's `placed`, `Index.told`'s person-words rule (REPLY_KEYS, ECHO_WORDS, subject scoping)
  and the agent rule's `act_gaps(agent=True)` (`_does_what`: placement and role matching).

## How to test

```
python -m unittest tests.test_perceive                    # 59 tests; also HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core
python -m unittest tests.test_perceive.ToldTests tests.test_perceive.ForkEvidenceTests tests.test_perceive.BranchAgentRuleTests
# the reviewers' repros (scratch p2_REV-A-gaming / p2_REV-A-spec), from the project root:
python <scratch>/p2_REV-A-spec/p_fork_claim.py ; python <scratch>/p2_REV-A-spec/p_fork_say.py
cd <scratch>/p2_REV-A-gaming && python r3_told.py && python r4_report_gating.py && python r8c_branch_bypass.py 0 1 3
```
