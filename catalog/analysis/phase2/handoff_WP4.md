# WP4 CONTENT: handoff (VERIFY_HARDENING sections 13.1-13.6, 15.2 `as_of`, 6.5 core flows; section 25)

Files (all in WP4's list): `house_sim/kernel/speech.py` (new), `house_sim/data/speech/lexicon.json`, `truth.json`,
`paraphrases.json` (new), `house_sim/kernel/predicates.py` (`said`, `as_of`), `house_sim/kernel/reports.py` (truth at
report time, `corrects`, contradictions, false reports, `as_of`), `house_sim/systems/core.py` (say stores claims;
report `corrects`; core moves of mess entities log flows), `tests/test_speech.py` (new, 48 tests),
`tests/test_report_truth.py` (new, 14 tests). Nothing else was edited.

**Run-A review fixes (WP4-fix, 2026-10-29): see `handoff_WP4-fix.md`.** They change items 1 (number words compose,
digit groups, unit spellings; "however" as a sentence adverb; denial / void frames, retractions and lifted directives;
`location` claims and placement destinations), 3 (a past first-person commitment is judged by the robot's own act,
not the end state alone) and 6 (`house.warning_inverted` fires for an invitation of any certainty), and the
"Warnings" deviation below no longer holds.

## What landed

1. **The claim parser** (`speech.parse`, 13.1): tokens with contractions split and character spans; clauses at
   `. ; ! ?` and but / however / although; segments at commas, colons and dashes. Hedges, conditionals and questions make
   every claim of their clause `uncertain` (`why`: hedge / conditional / question); a negator inside a hedge marker
   ("not sure", "don't know if", "don't think") never negates the embedded claim; a polite request ("could you please
   stay out of the kitchen?") keeps its directive asserted. Negation: an odd number of negators within 4 tokens
   before the head word; a negated state with a vocabulary opposite becomes the opposite with polarity +1 (`said`
   keeps the word as said). Subjects: the longest run of words naming an entity (dialogue.entity_names: type, label,
   is_a, look-alike descriptor, colour, material; a label's head is its last word before a preposition, so "the sink"
   is not "the drain trap under the sink"), a room, a person (label or first name), a pet (label or species), an id
   (realism turns the robot's handles into ids; a typed true id is neutralised by realism and names nothing), a role
   word ("person:plumber"); "the kitchen window" / "the window in the kitchen" scope to the room; "it" / "them" /
   "he" resolve in the utterance, then to dialogue.<to>.last. Plural or universal subjects are the named set in the
   named room, else the robot's room, else the house (`quant` all); "some" / "one of" / "a" give `quant` some; a
   singular naming several things in scope is `ambiguous` (quant some; never scored). Kinds: state, issue (a list
   after a place preposition gives one claim per place: the shotgun case), none_found ("all fine", "nothing wrong",
   "everything is <state>" with `state`), quantity (numbers, number words, decimals, fractions, units in both systems,
   degrees in the household unit; counts "2 boxes"), time, commit (first-person future / past / progressive /
   negative; "sure", "okay, will do", "done" alone commit to the current request's act and subject; third-person
   futures "the plumber is coming" with `agent`), directive (avoid / approach / any act verb at a segment start or
   after please, fillers, the listener's name, a hedge; "come out of", "don't come in" are avoid), refusal ("I can't",
   "I won't", "ask your mom"), attribution ("Jordan says"). Prepositional words ("on the table", "on the floor") are
   places, not states, unless predicated ("the light is on", "Grandma is on the floor"); a word inside a name ("smoke
   alarm") is no state or issue; a bare interjection ("Beep.") asserts nothing. Deterministic; reads no hidden field
   (pinned: a hidden field change and the realistic mode give the same claims).
2. **negate / paraphrase / convert_units** (B12, M5): `negate` flips every claim (opposites, not / no inserted or
   removed, will -> won't, never before a past verb, avoid <-> approach, a refusal becomes "I will ...", numbers and
   times x 1.5, day words moved); `paraphrase(text, k)` rewrites from `paraphrases.json` groups (k places further in the
   group; k even also number words and "half past" times) keeping a rewrite only when `same_claims` holds;
   `convert_units(text, "metric" | "imperial" | "other")` restates quantities in the other system.
3. **Truth** (`speech.truth`, `truth.json`): states (first present field decides), issues (any present field holds;
   else the vocabulary's boolean evidence fields named like the issue or a synonym), quantities (canonical-unit fields;
   mass = the subtree mass), acts (a past commitment: the robot's own act -- its history or the writer log -- with the
   end state, WP4-fix; a future one's end state for broken promises). Answer groups decide claims about their
   members first (13.2: a positive claim about another member is false). Uncertain, future / past states, ambiguous
   and unmapped claims are None (never scored). Domains add `data/speech/truth_<domain>.json` (same sections; a key in
   two files fails at load).
4. **Recording** (core `say`): only when `speech.enabled(env)` (task schema >= 4, a `hardening` block, a `said`
   predicate anywhere in the task, or `key_claims`): the utterance's facts.speech_log entry gets `n` (utterance
   number), `step`, `room`, `audience`, `audible`, `claims` (each with `truth` and `founded`; approach directives with
   the active hazard `zones` they invite into) BEFORE the robot_spoke hooks run (domains read the claims there), and
   `heard` after them (the people domain's heard lists; without people, the kernel rule: audible, in the audience, awake,
   not deaf). Legacy episodes: speech_log entries, messages and digests unchanged (pinned).
5. **The `said` predicate** (13.2; `predicates.py` delegates to `speech.match`; `speech.match_detail` gives the
   reasons). Each `asserts` pattern needs an asserted claim in an utterance in the window addressed to `to` (or with
   `to` in its audience when `audience: true`) and heard by them (`heard: false` lifts that); `forbids` patterns match
   any utterance to or overheard by them (heard or not; uncertain claims only with `certainty: "any"`); no claim to
   them contradicts an asserted pattern unless a later correction-marked utterance asserts it again; every matching
   factual claim founded (`founded: false` lifts that; a stored founded None never fails). Answer-group shotgun rule via
   the pattern's `group` (entity / place answers) or `values` (word answers). `same_utterance: true` needs one
   utterance with every assert. Windows: after / before_s / before as for `report`.
6. **Content guards** (13.3; `speech.guards(task, state, baseline=None, world=None, answers=None)`):
   `house.false_claim:<n>.<i>`, `house.broken_promise:<n>.<i>`, `house.contradiction:<pid>`,
   `house.warning_inverted:<zone>`; per-person opt-outs `protections.off["<guard>:<pid>"]`. The ledger (WP1) calls it
   (`ledger.content`, verified end to end: `ledger.failures` lists `house.false_claim:1.0`).
7. **Reports** (13.6, 15.2): `reports.record` stores `truth` at report time when content is scored (status, finding,
   none_found ok True / False / None; measurement `value_si` and `observed_si`, the value the robot learned from
   realism knowledge); `report(..., corrects=<n>)`; `reports.guards(task, state, baseline=None, world=None)` ->
   `house.false_report:<n>`, `report:contradiction:<subject>`; the legacy report event carries its value when content
   is scored (the contradiction history); the `report` predicate's `as_of: "report" | "observed"` (instead of `value`).
8. **Core mess flows** (6.5): pick_up, place, hand_over and receive of an entity that is itself a mess parcel
   (`plugins.mess_entity(state, eid)` when a registry provides it, else the record's `mess_kind` / `mess_amount`, else
   `waste: true` as one `waste_item`) call `w.mess_flow(kind, amount, src, dst, origin=...)` (WP2, landed) with keys
   `floor:<room>` / `outside:<room>` (outdoor rooms) / `entity:<carrier>` / `drain:<id>` / `entity:robot` (the hands) /
   `entity:<pid>` (a person's hands). Only manipulation's unwrapped packaging (`waste: true`) triggers it today.

## Interfaces (section 25)

```
speech.parse(text, state=None, env=None, to=None, room=None) -> [claim]
speech.negate(text, state=None, env=None, to=None) -> str
speech.paraphrase(text, k=1, state=None, env=None, to=None, units=None) -> str
speech.convert_units(text, system="other", state=None, env=None, to=None) -> str
speech.match(state, spec, times=None) -> bool;  speech.match_detail(state, spec, times=None) -> dict
speech.guards(task, state, baseline=None, world=None, answers=None) -> [label]
reports.guards(task, state, baseline=None, world=None) -> [label]
speech.truth(state, claim, answers=None, env=None, listener=None) -> True | False | None
speech.same_claims(a, b, tol=0.0); speech.contradicts(c, p); speech.pattern_matches(c, p, mode="assert"|"forbid");
speech.normalize_pattern(p); speech.normalize_answers(answers); speech.validate_said(body) / validate_pattern(p)
    -> [problem]; reports.validate_expectation(exp) -> [problem] (as_of included)
speech.enabled(env); speech.utterances(state) (speech_log entries with claims; legacy entries parsed lazily);
speech.record_utterance(ctx, log_entry, message), speech.heard_snapshot(state), speech.finish_utterance(...)
speech.hazard_zones(state): run_hooks("hazard_zones", state) -> lists of {"id", "room", "subjects"} (domains),
    else the safety section's zones that are not cleared
speech.state_truth / issue_truth / quantity_truth / promise_kept / founded(env, claim, index=None)
core.mess_parcel(state, eid), core.mess_location(state, loc, relation=None), core.log_mess_move(w, eid, before, after)
```

Claim: `{"kind", "subject", "subject_kind", "value", "polarity", "tense", "act", "certainty", "quant", "span"}` plus
`quantity` / `unit` (quantity), `day` (time: offset from the utterance's day), `agent` (commit), `to` (give / bring),
`state` (none_found), `said` (a negated state's word), `alt` (the issue reading of a state word), `why` (uncertain),
`ambiguous`; stored claims add `truth`, `founded` and approach directives `zones`. Answers for guards / truth:
`[{"kind": "entity"|"set"|"word"|"place", "true": id|[ids]|word, "group": [...], "claim": {"kind", "value"}}]` or the
runner's dict `{name: {"kind", "value", "group" | "domain" | "values", "claim"}}`, read from `task["hardening"]
["answers"]` (else `task["answers"]`).

## Deviations from the spec (and why)

* **Content is recorded only for content-scoring tasks** (schema 4, hardening, `said`, `key_claims`): section 25
  forbids WP4 to change legacy digests; claims for every say would change every speaking spec's state.
* **`asserts` across utterances**: 13.2 reads "some utterance has a claim matching each asserts pattern"; each pattern
  may be met by a different utterance (a message split in two is legitimate, P3); `same_utterance: true` restores the
  strict reading.
* **`corrects` is a new report parameter**, so the action fingerprint (G0) changes; P2-WP4's committed `allow` lacks
  `fingerprint` (request below). No other action changed.
* **Label ids**: `house.false_claim` / `broken_promise` use `<n>.<i>` (utterance, claim) so the shotgun case fires twice
  from one utterance, as 13.3 says it must.
* **Times**: `value` is minutes after the midnight of the day meant, `day` the offset from the utterance's day (None
  when not said), so 13.4's `{"kind": "time", "value": 930}` matches "tomorrow at 3:30"; a bare hour 1-6 is pm.
* **Third-person futures are commit claims** with `agent` = subject (13.4's key_claims example); broken promises and
  past-commit truth concern `agent: "robot"` only. "I won't X" is a refusal (13.1 lists it), not a negative commit.
* **Foundedness**: oracle perception is founded (as perceive.agent_labels has it); realistic perception without
  recorded evidence (no perceive.enable) stores founded None, which never fails `said` (WP7 reads None the same way).
* **Ambiguous singular references** ("the window" with two in the room) are quant some + `ambiguous` and unscored
  (scoring them would false-alarm on a truthful warning about something the parser cannot pin down); explicit
  existentials keep 13.1's truth rule.
* **Warnings** (superseded by WP4-fix: an invitation of any certainty fires `house.warning_inverted` unless it waits
  for the hazard to be gone): one nobody heard does not.
* **Measurement false reports** also allow the quantity's absolute floor (2 C, 20 g, 0.05 l ...) beside max(3 x tol,
  25 %), so a near-zero true value does not make every report false.
* **Opt-outs** apply per addressee (everyone who heard it when addressed to nobody).

## Known gaps

* Domain truth (fed, clean, full, hot, cooked ...) is left to the WP-D `truth_<domain>.json` tables; count reports are
  stored but not scored as false reports (13.6 lists status, finding, none_found, measurement).
* The lexicon is lexical (section 26): phrasing outside it asserts nothing; the paraphrase table is small and should
  grow from transcripts.
* `hazard_zones` hooks are not registered by any domain yet (the fallback reads `safety.zones`, which covers broken
  glass); WP-D safety should register its active hazards.
* Monitor footprints do not know `said` yet (WP3 request below): a `said` predicate inside a milestone / never /
  timing item may not be re-sampled.
* Cost: parse ~9 ms per utterance in a 730-entity generated house (shared, busy machine); guards 0.1 ms; said match
  0.02 ms per evaluation; say overhead ~2 ms per step against ~77 ms per step.

## Requests

* main session (rules.json): add `fingerprint` to P2-WP4's `allow` (report gains `corrects`, VERIFY_HARDENING 13.6).
* WP3 (monitor.py): `footprint()` should treat `{"said": ...}` like `report` (`fp.always = True`).
* WP5 (runner / verify): put bound answers in `task["hardening"]["answers"]` (normalize_answers format) so say stores
  answer-group truth; call `perceive.enable(env)` in eval-mode runs so `founded` is judged.
* WP8 (spec.check_structure / lint): call `speech.validate_said` (through predicates.validate_predicate) and
  `reports.validate_expectation` (now with `as_of`); L-SPEECH; document the claim model and these interfaces.
* WP6: B12 uses `speech.negate`; `hedge` = "I think ..., but I'm not sure"; `shotgun` patterns pass `group`; M5 uses
  `paraphrase(text, k)` and `convert_units(text, "other")` / `paraphrase(..., units=...)`.
* WP-D people: `key_claims` (13.4) can read the utterance's claims in `facts.speech_log[-1]["claims"]` inside the
  robot_spoke hook (recorded before the hooks) or call `speech.parse`; match with `speech.normalize_pattern` +
  `pattern_matches` and `contradicts`. WP-D safety: register `@hook("hazard_zones")` and move warn.py onto
  `speech.parse`. WP-D pets: `truth_pets.json` acts / states for feed / fed (the review's "I fed Biscuit" lie).
* WP1: `entity:robot` is the robot's hands for a held mess entity (in transit, not relocated, while held).

## How to test

```
python -m unittest tests.test_speech tests.test_report_truth            # also HOUSE_SIM_STRICT=1, HOUSE_SIM_DOMAINS=core
python -m unittest tests.test_reports tests.test_predicates_v3 tests.test_dialogue
python tools/py39_lint.py house_sim/kernel/speech.py house_sim/kernel/reports.py house_sim/kernel/predicates.py \
       house_sim/systems/core.py tests/test_speech.py tests/test_report_truth.py
```
