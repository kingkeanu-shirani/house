# REV-A-bugs: independent review of run A (correctness lens)

Structured result: `catalog/analysis/phase2/review_A_bugs.json` (7 findings, A-bugs-1 .. A-bugs-7). Read-only review:
no project file edited except these two; every repro script and its output is in the scratch folder
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/p2_REV-A-bugs/`
(`head_tree/` there is `git archive HEAD house_sim tools`, the A side of the A/B runs).

## Findings by owner

| id | owner | severity | what |
|---|---|---|---|
| A-bugs-1 | WP5 | high | `strength.verdict` needs G6 (held-out, full level only), so a standard-level check is at best PROVISIONAL and `level_passes(.., "standard")` is never true; once WP6 lands every `--check` fails and `checklog.stop_loss` parks every spec after 8 checks (section 24 item 2 excludes G6) |
| A-bugs-2 | WP2 | medium | `HouseEnv.finish` loops forever when `settle_s` is off the clock's 1 ms grid (`settle_s: 30.0004` passes every validator) |
| A-bugs-3 | WP1 | medium | `restore.json` exterior_door selects `exterior: true`, which every window type has: windows get left_open / held_open (300 s) and a priced hold_open escape for `envelope.change` (WP3's request, not actioned) |
| A-bugs-4 | WP1 | medium | ambient lights cover only `on` / `brightness` of light types: light switches and `lit` are charged, so geometric (scored-mode) references that switch a light on to see fail `house.changed` |
| A-bugs-5 | WP5 | medium | the run-cache key has no domain set: a `HOUSE_SIM_DOMAINS=core` check's runs are reused by a full check |
| A-bugs-6 | WP5 | low | `reference_run` marks the new SpecError (bad skill argument) as a harness error (G7), unlike `wrong_run` |
| A-bugs-7 | WP8 | low | `spec.label_problems` raises TypeError for a non-list `success` / `failure` (HEAD returned problems); `compile_specs` catches only SpecError |

## What was checked and held

* py39_lint clean (507 files); full unit suite 5,337 tests per mode, 0 failures, normal and strict; all 243 authored
  specs in their own mode (see the JSON's notes for the count); the 12 new test files normal and strict.
* Legacy A/B (HEAD tree vs working tree, `verify.run_trial`) on 122 spec-trials in own, realistic/room and
  realistic/geometric: outcomes identical; only WP2's listed `logs.gone` digests differ.
* Cross-process determinism of `verify_spec` (PYTHONHASHSEED), save/load forking through `fast_forward` + `finish` in
  all perception x camera modes (normal and strict), speech parser fuzzing, malformed-spec fuzzing, degenerate
  `verify_spec` inputs, as-if schema-4 core trials on 32 authored specs in three modes.
* Pre-existing, not run A's: replay of realistic/geometric episodes diverges at step 3 on HEAD as well
  (T-1df40780f5ddf2-01 seed 0, `replay_plain.py`); check_structure crashes on `wrong: null` / `variants: -1` at HEAD.
* New test files that fail under `HOUSE_SIM_DOMAINS=core` (fixture dependencies on domain types; not in the gate's
  core list): test_counterfactual, test_ledger, test_lint, test_timing, test_verify_hardening.

## How to re-run a repro

`python <scratch>/<script>.py ...` as given in each finding's `repro`; the scripts import the project from
`<home>/OneDrive/Desktop/house_ai` (cs_ab.py takes the tree as its argument) and write only to scratch.
