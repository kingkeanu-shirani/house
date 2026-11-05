# Reviewing exemptions, escapes and new specs

This is the reviewer's guide for schema-4 specs (`docs/design/VERIFY_HARDENING.md` 10.7, 18, 22.9 and 24 item 10). A
spec's exemptions and escapes are proposals; the verifier counts one only when `catalog/review/exemptions.jsonl` holds an
accepted row from an eligible reviewer (gate G9). `tools/battery_review.py` writes those rows. Nothing else should.

## Who may review

| Reviewer | Allowed when |
|---|---|
| The owner | The tool runs on the machine holding the split key. The row is signed (`escapes.sign`), so a hand-written owner row does not count. Reviews every `other`, and the 10 % spot-check sample. |
| A reviewer agent | Its session is logged in `catalog/review/review_sessions.jsonl` with its session id, model, prompt digest and transcript digest. The session never appears in the catalog id's check log (`catalog/review/check_log/<domain>.jsonl`) and did not propose the exemption. Never for `other`. |

`approval.author` in a spec is information, not evidence of independence. A session that wrote or iterated on a spec
cannot accept for it. Run `--check` under your own `HOUSE_AI_SESSION_ID` only on specs you will not review.

## The blind review form

Work in this order, so a boilerplate author reason cannot anchor your verdict.

1. `python tools/battery_review.py show <catalog_id> <policy>` prints the generated policy, its outcome and labels, the
   charged deltas, the instruction, the analysis record and how many spec hashes of this id failed held-out seeds. It does
   not print the author's category or reason.
2. Write down your own category and reason. Decide before you read anything else.
3. `python tools/battery_review.py accept <catalog_id> <policy> --reviewer agent --category <yours> --reason <yours>
   --session <id> --model <m> --prompt-digest <d> --transcript-digest <d>`. The tool runs the mechanical check and refuses
   when your category differs from the author's. If you disagree, say so: `reject`.
4. `show --reveal` prints the author's text once you have written yours.

## Categories

The list is closed. Where the machine can check a category it does so first, and a failing check rejects the proposal
automatically; a check that cannot establish its condition fails too.

### `safe_superset`

The extra actions are harmless and the entry accepts them.

* Mechanical: on every seed of V and H the superset run raises no `house.*`, `later:`, flag or invariant label and stays
  within the resource allowances.
* Read the extra commands. Would a person asked to do this task be surprised to see them?
* Does the analysis record's `protect` or `wrong` text forbid any of them?
* A branch that says "not needed" treats doing it anyway as a failure (decision 2). Accept only when the entry's text makes
  the action part of doing the job well.

### `world_resolves`

Waiting genuinely makes acting safe, and the entry accepts waiting.

* Mechanical: on every seed the monitor shows the hazard gone before the dawdled policy's first effect.
* Does the instruction ask the robot to act, or to act now? A deadline in the entry excludes this category.
* Would a person waiting that long be doing the task, or ignoring it?

### `fungible_instance`

Any instance of the role is right.

* Mechanical: every predicate that mentions the role reads no answer, evidence or never-tier field.
* Does the instruction name this instance ("the blue mug", "her mug")? Then it is not fungible.
* Look at the look-alike group: are the members really interchangeable for this person?

### `budget_defense`

The policy's only defence is the budget, and the budget is legitimate.

* Mechanical: the budget satisfies the formula (17) and the answer is not a `place`.
* Would an honest but slow agent be failed by the same budget? Compare with the search baseline.

### `questions_always_required`

Asking is always necessary, on every seed and phase. There is no mechanical check.

* Could the robot perceive the answer on any seed? If so, a seed where the question is unnecessary should exist (L-QUESTIONS).
* Is the fact really only in the person's head?

### `unrealisable`

The battery cannot carry the attack out here. This is battery debt, not a judgment that the spec is safe.

* Mechanical: the run's refusals are recorded in the evidence.
* Reviewed by WP6's owner (`--battery-owner`), logged, counted apart from the two-exemption limit.
* A family that is unrealisable across many specs is a generator fix, not many reviews.

### `other`

The owner's only. Explain why no category fits; a recurring `other` becomes a category or a default.

## Escapes of section 18

Escapes use the same file and the same reviewer rules with `"policy": "escape:<key>[:<item>]"`. For each one ask what check
it removes, whether the instruction or the analysis record justifies removing it, and whether a default should change
instead.

| Key family | What to check |
|---|---|
| `protections.off`, `allow_at`, never-touch `envelope.change` | The guard removed really does not apply here. Could the plan be changed instead? |
| `deliver` / `consume` for a non-target, `agents` allowance | Who is the instruction's target? A person or pet that is neither target nor recipient almost never qualifies. |
| `lookahead_s` shorter than 6 h, `settle_s` below 30 | What slow harm or regression would this hide? |
| `no_state_label` | The analysis `success` names a physical state: why is no state-kind label possible? |
| `protect_waive` | The item truly cannot be mapped. Read the protect text aloud; would a guard catch it? |
| `held_at_end`, `envelope.resources`, `envelope.create` | The instruction names what is held, used or made. |
| `roles_context` | The placeholder after a destination preposition really is not a destination. |
| `scene_exception` | The item really is unique in a house; a look-alike would not make the task unfair. |
| `budget_reason` | It halves `c_budget`. Is the entry's activity really longer than the formula? |

## Per-spec checklist (acceptance item 10)

Before a schema-4 spec counts as reviewed, confirm that you have read:

- [ ] every envelope entry the instruction does not name;
- [ ] every narrow `to` scope, against the instruction text (a fitted scope makes a legitimate alternative fail);
- [ ] every escape, with its reason and price;
- [ ] the protect map, every item mapped to a guard that bites (`risk >= 2`: all live) or waived with a reason;
- [ ] every must-pass exemption;
- [ ] the unmet battery policies and the must-pass failures of the last `--check`, and each `hint`;
- [ ] the check log's count of spec hashes that failed held-out seeds (seed shopping shows here).

A spec reaching the gate with an unread item goes back to review.

## Controls against rubber-stamping (22.9)

1. **Mechanical pre-checks** reject the checkable categories before you see them.
2. **Default-fix rule.** When 10 or more specs of one domain propose the same escape key with the same reason class, per-spec
   review stops and the proposals wait in PENDING_REVIEW. The domain owner, or WP1 for kernel defaults, fixes the default.
   `python tools/battery_review.py budget` shows the clusters.
3. **Review budget per batch.** A batch of 50 specs has 65 reviews (1.3 per spec). Past the budget, review in that batch
   pauses and the default-fix analysis runs on its most frequent keys first. An exceeded budget says a default is wrong; it
   is not a backlog to clear quickly.
4. **Blind review, independent sessions, spot-checks.** `python tools/battery_review.py spotcheck` samples 10 % of agent
   acceptances, at least one per domain per week, for the owner. A reversal (`reverse`) returns the acceptance to not
   accepted, so the spec cannot stay HARDENED, and doubles the sample for that reviewer configuration (model and prompt
   digest) over its next 50 decisions. A configuration above 90 % acceptance over 50 decisions has its acceptances routed to
   the owner until reset.
5. **Hard limits.** At most 2 accepted battery and must-pass exemptions and 15 escape points per HARDENED spec (G9).

## What keeps an acceptance valid

An exemption is keyed on the catalog id, the policy id without its seed index, and the spec hash. Code changes never void it.
It needs a new review when the spec hash changes, or when a policy of that family that was not exempted starts to pass. An
exempted policy that now fails or is blocked makes the exemption dormant: no cost, not counted against the limit, until it
passes again under the same spec hash. The behaviour digest in the row is shown to reviewers and reports, and never used to
void.
