# Handoff: review of run A, spec-conformance lens (review_A_spec)

The structured result is `catalog/analysis/phase2/review_A_spec.json`. It holds 14 findings: 2 high, 6 medium and 6 low.
Each finding names the package whose files must change and gives a repro I ran. No project file was edited. The
repro scripts are in the scratch dir
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/p2_REV-A-spec/`.
Run each one with `python <script>` from the project root.

| Id | Owner | Sev. | What |
|---|---|---|---|
| A-spec-1 | WP1 | high | The content class (6.3: removed / consume) is never charged. Emptying the milk, a jar or a pill bottle fires no label (`p_ledger_content.py`) |
| A-spec-2 | WP7 | high | `Realism.dump/load` drop the ObservationLog and `record_observations`. In a forked run, claims founded on earlier sightings become unfounded, and new claims are never gated, so `said` passes an unfounded claim (`p_fork_claim.py`, `p_fork_say.py`) |
| A-spec-3 | WP1 | medium | `house.lost` checks the loss location and the drain relation only for valuables. A non-target item can be binned or drained inside its move scope (`p_ledger_lost.py`) |
| A-spec-4 | WP4 | medium | "two hundred fifty grams" parses as 50 g and "1,250 grams" as 250 g. "kilograms", "grammes", "millilitres" and "centimeters" parse to nothing (`p_said_num.py`, `p_speech3.py`) |
| A-spec-5 | WP5 | medium | G9 accepts an owner row with no key or signature, and an agent session that is not in `review_sessions.jsonl`. It never compares the reviewer's category with the author's (`p_forge.py`) |
| A-spec-6 | WP5 | medium | When `lint.dynamic_facts` fails, the result is only a warning: G5 holds and a vacuous spec passes the quick level (`p_dynfail.py`) |
| A-spec-7 | WP5 | medium | The look-ahead trigger reads only the plan's placeholders, so a fridge opened by `fetch` does not trigger it (`p_lookahead.py`) |
| A-spec-8 | WP5 | medium | The budget formula takes explore steps from the baseline with more seconds, so a search task loses its search steps. Phase budgets are never checked (`p_wp5_units.py`) |
| A-spec-9 | WP2 | low | `env.save` omits `env.idle`. The fork test covers only legacy specs, with no `finish`, no ledger and no eval-mode evidence (`p_fork_idle.py`) |
| A-spec-10 | WP4 | low | A "however" in mid-sentence drops the claim's subject, so a lie phrased that way escapes `house.false_claim` (`p_however.py`) |
| A-spec-11 | WP5 | low | Skill-debt classification ignores `PlanError.category`, so a skill's route failure counts as a spec failure (`p_wp5_units.py`) |
| A-spec-12 | WP8 | low | The docs still list WP5 and WP11 as "in progress", and their interfaces are undocumented |
| A-spec-13 | WP1 | low | A task whose only temporal key is `timing` gets no `result["temporal"]` (`p_timing_block.py`) |
| A-spec-14 | WP8 | low | L-ENVELOPE and L-PROTECT do not check that selectors, scopes and narrowed bindings resolve (`p_lint.py`, `p_protect.py`) |

Conformant:
- Every section-25 interface name and signature (`sigs.py`).
- The 21.1 CLI options.
- The protections validators, the placement guards and the ambient revocation.
- Most lints of 20.2.
- The held-out seed formula of 16.1.
- The budget formula and L-BUDGET; containment, variation, G2 and the crash policy (`p_dyn.py`, `p_book.py`).
- The claim parser's hedges, plurals, shotgun lists and directives (`p_speech.py`).

Out of scope: WP6 and WP10, and the WP-D data. No mess kinds, derived fields or volition tags are registered yet.
