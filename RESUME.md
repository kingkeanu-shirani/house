# Resume notes (paused 2026-10-10 before the authoring wave)

State: kernel v2.8 (realistic perception hardened), 16 mechanics domains after fix round 4, evaluation suite after
its red-team fixes. 2,570 unit tests pass (normal + strict); 242/242 authored specs pass on seeds 0,1,2,41,97;
2,478 catalog entries remain to be authored (52 batches prepared in catalog/analysis/batches/). Details and open
items: PROGRESS.md, section "Paused (2026-10-10)".

Rules that still apply: no paid API calls or data sent off the machine without asking first with a cost estimate;
stop and tell the user before creating the real split key (they back up %USERPROFILE%\.house_ai); honest fidelity
labels; never fake mechanics.

Next steps:
1. Round-4 follow-ups: catalog/analysis/round4_results.json (53 kernel requests, 55 cross-domain bugs).
2. Green check (commands below), snapshot (`python tools/orchestration/snapshot.py NAME`).
3. Regenerate leaderboard (tools/orchestration/regen_leaderboard.sh) and coverage (`python -m house_sim --verify`,
   then `--report`).
4. Authoring wave: tools/orchestration/author_wf.js (pilot first), gap-fix rounds with gapfix_wf.js.

Verification commands:
```
python tools/orchestration/green_tests.py 5
python -m house_sim --check tasks/authored/<file>.json --workers 3
python tools/perception_audit.py 100 80
python tools/layout_audit.py 30
python tools/fuzz.py 12 300
```

## State on 2026-10-22 (paused by the owner after finishing current work)

- Branch tier1-foundations on GitHub holds everything below; main on GitHub is still the initial import (merge via
  the PR when ready).
- Read first: PROGRESS.md "Milestone 4", docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md (sections 1-4),
  docs/design/VERIFY_HARDENING.md, catalog/analysis/tier1/adopt_status.json.
- Status: 4,325 tests (4 failing: test_people, test_physics_statics - known domain breakages); 241/243 specs; D3
  preview 225/243. Mass authoring is frozen until the verifier is hardened (review finding C1).
- Next steps, in order: PROGRESS.md "Milestone 4 / Next steps".
- Rules: no paid API calls without asking (with a cost estimate); never create the split key without telling the
  owner first; hand work between agents through files, never inline-truncated JSON.
- Local-only material: archive/review_2026-10-22_workdir.zip (the reviewers' probe scripts and logs),
  archive/partial_adopt_*.patch (now obsolete), catalog/analysis/tier1/gate/ (gate records).

## Phase 1 "stabilise" (2026-10-22, owner said "finish the project", then paused)

Plan (docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md section 3, adopted): P1 stabilise -> P2 implement
docs/design/VERIFY_HARDENING.md (decisions: VERIFY_HARDENING_DECISIONS.md) -> P3 one re-baseline for D1-D3 -> P4
re-harden the 243 specs -> P5 LLM pilot (owner approval needed) -> P6 author core-500, then the rest -> P7 final
verification, docs, final review, packaging.

P1 result (catalog/analysis/phase1/STATUS.md, FIX2.json): all 19 fix agents, integrator, two red teams and the fix
pass finished (the workflow's last return was cut by a session end; the per-domain repair step did not run).
4,681 unit tests pass (normal, strict, domain-isolated); 241/243 specs in own mode; 51/51 realistic specs with the
geometric camera; perception audit 0 (upgraded auditor), layout 0, fuzz 0, coverage fuzz 286/427 actions.
Open: catalog/analysis/phase1/breakages.json (53 domain rows, incl. T-7d4006bda247ba-01); red-team items left for P2
(placement roles by position -> WP9; public-seed rebuild; averaging/correlation audits -> WP7/WP6; gate-tool holes in
red_defects.json); G7 perf regression (3.85 vs 3.21 ms/substep, domains' share); gate sign-off draft
P1_signoff_draft.json to review.
Next: run the P1 repair step for the domains in breakages.json (tools/orchestration workflow phase1_stabilize.js,
resumable), sign the P1 gate, then start P2 (VERIFY_HARDENING WP order in its section 25).
Owner decisions pending: create the split key (private gate stages); approve the paid LLM pilot.

## State on 2026-10-28 (owner deadline: about 2026-11-02)

- Phase 1 closed: gate records P1F (2c5765c) and P1C (df21279) countersigned (gate_policy/signoffs/). 4,884 tests
  per mode green, 243/243 specs, 0 perception leaks; status in catalog/analysis/phase1/STATUS.md.
- First CI runs (GitHub Actions, private repo) found what only a real Python 3.9 / Linux / Windows runner shows:
  `Path.write_text(newline=)` (3.10+; now a py39_lint rule), Ubuntu 26.04 without Python 3.9 (pinned 24.04), the
  throwaway CI home dropping `core.autocrlf false` on Windows, one unregistered wall-clock bound (now registered).
- Phase 2 (VERIFY_HARDENING) runs in three workflow runs instead of six: A = WP1-4/7/9/11 + WP5/8 (record P2-S2,
  `--package P2-S1+P2-S2 --prev P1C`), B = WP6 + 16 WP-D + WP10 (record P2-S4, `--package P2-S3+P2-S4`, gate-change
  row), C = R0 audit + exit test, then S6 fixes (record P2-S6). The main session countersigns and commits between
  runs and moves rules.json running_substages.
- Then WP12 (re-harden the 243) and the authoring wave; with the deadline, every spec is labelled by the
  verification level it actually reached (quick / standard / full) - the owner decides on Oct 31 from R0's measured
  costs. The real split key is created only when the owner is present; the paid LLM pilot is optional.

## State on 2026-10-29

- Phase 2 run A landed (fc9eba1): WP1-4, WP7, WP9, WP11, WP5, WP8 with handoffs in catalog/analysis/phase2/; 32
  confirmed review findings fixed; gate record P2-S2 vs P1C PASS on the committed tree (signoffs/P2-S2.json: 14
  room-golden rows from WP2's logs.gone). No schema 2/3 outcome changed. 5,413 tests per mode green.
- GitHub Actions does not start jobs (account billing / spending limit, the owner's decision); verification meanwhile
  is the local gate (full suite on 3.14 + py39_lint).
- Run B (record P2-S4, `--package P2-S3+P2-S4 --prev P2-S2`, gate-change row) is next: WP6 battery, 16 WP-D, WP10
  (also run_check.py for schema-4 levels: the authoring wave's orchestrator check).
