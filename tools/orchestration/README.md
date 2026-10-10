# Orchestration scripts (used by the main session)

Workflow scripts and helpers used to run the multi-agent rounds. Replace `%PROJECT%` with the project folder
(and `%SCRATCH%` with a scratch folder) before running.

- `author_wf.js` — authoring wave: one agent per catalog batch (`catalog/analysis/batches/batch_NN.json`) writes and
  verifies `tasks/authored/batch_NN.json`, then an adversarial reviewer tries to game each spec. args: `{batches: [..]}`.
- `gapfix_wf.js` — domain owners implement gaps reported by authors (`catalog/analysis/gaps_open.json`).
  args: `{domains: [..]}`.
- `round4_wf.js` — the last domain fix round (template for the next one).
- `green_tests.py` — every tests/test_*.py, normal and HOUSE_SIM_STRICT=1, in parallel.
- `regen_leaderboard.sh` — re-run the offline baselines on the dev subset and rebuild reports/leaderboard.* with a
  throwaway verifier key from the environment (writes nothing to ~/.house_ai).
- `snapshot.py NAME` — zip the project to archive/NAME.zip without caches, archive/ or private split material.
