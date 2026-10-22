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
