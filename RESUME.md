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
