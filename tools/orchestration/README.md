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
- `tier1_gate.py` — the tier-1 gate (docs/design/TIER1_INTEGRATION.md section 3), run by every tier-1 package before
  hand-over and by the main session at B0 / B6 / C1. See below.

## tier1_gate.py (tier-1 gate)

```
python tools/orchestration/tier1_gate.py record --id <ID> [--package <PKG>[+<PKG>]] [--prev <ID>|none]
                                         [--extras physics,camera,body,sweep,gen] [--camera room|geometric]
                                         [--only G2,G3] [--skip G7] [--jobs N] [--shared-tree] [--reviewed FILE]
python tools/orchestration/tier1_gate.py run ...              # same as record (the name used in TIER1_INTEGRATION)
python tools/orchestration/tier1_gate.py compare [<PREV_ID> [<ID>]] [--package <PKG>] [--shared-tree]
                                          [--spec-files GLOB] [--reviewed FILE]
python tools/orchestration/tier1_gate.py scope --package <PKG> [--shared-tree]      # G10 alone
python tools/orchestration/tier1_gate.py isolate --out DIR [--package <PKG>] [--files PATH ...] [--base REV]
python tools/orchestration/tier1_gate.py packages [<PKG> ...]                       # the scope / policy table
python tools/orchestration/tier1_gate.py gen1-digests [--write --by <ID> --reason TEXT]
```

`record` writes `catalog/analysis/tier1/gate/<ID>/` and prints a PASS/FAIL table; its last line is the gate summary
(`TIER1-GATE <ID> [<PKG>] vs <PREV>: PASS (...)`) and the exit status is 0 only for PASS. `--package` defaults to the
id when the id is a package (`PHYS-INT`, `CAM-SIGHT`, ...); `--prev` defaults to the most recently recorded gate.
A full run takes about 25 minutes on the 8-core integration machine (`--jobs` defaults to cpus - 1): unit tests
and the 2,420 spec trials share one pool of worker processes, the audits run next, the benchmarks run alone.
The A0 baseline and the handoff with the package table are in `catalog/analysis/tier1/` (`gate/A0/`,
`handoff_TIER1-GATE.md`).

| Check | What it runs | Pass condition |
|---|---|---|
| G0 | `actions_fingerprint()` and the code fingerprints | the action fingerprint is unchanged unless the package may change it |
| G1 | every `tests/test_*.py`, normal and `HOUSE_SIM_STRICT=1` (one process per file and mode) | all green |
| G1b | each domain's test file with `HOUSE_SIM_DOMAINS=<domain>`; kernel test files with `HOUSE_SIM_DOMAINS=core` | domains green; kernel files green from B1 on (recorded before) |
| G1c | imports the package's library modules after every domain is loaded | nothing registered (TIER1_INTEGRATION 1.16) |
| G2 | every authored spec in its own perception mode, seeds 0 1 2 41 97 (`verify.run_trial`) | no unreviewed verdict change and no digest change the package may not make; without a previous gate every spec must pass |
| G3 | the same with perception forced to realistic | as G2 (recorded, not required to pass, without a previous gate); realistic specs run in both G2 and G3 and must give identical records |
| G4 | `tools/perception_audit.py 6 150 --workers 4` (+ `--camera geometric` once available) | exit 0 |
| G5 | `tools/layout_audit.py 40` and the generation-1 digests of `tests/data/gen1_digests.json` | counts equal to the previous gate; every pinned digest equal |
| G6 | `tools/fuzz.py 6 300 --workers 4` (+ `--camera geometric`, `--battery 5 --faults random` once available) | exit 0 (no defect, deterministic replay) |
| G7 | `tools/bench_systems.py 40 0 1 2 3` and a 1 h wait on seeds 0-3, best of 5 | ms/substep and the wait within the package budget (default 1.05 x previous gate; PHYS-INT 1.0 x A0) |
| G8 | `tests.test_room_golden`, also with `HOUSE_SIM_GOLDEN_ALL=1` | green (required from B1 on) |
| G9 | package extras: physics_audit, body_audit, generator compat + gen-2 layout + gen-2 timing, sweep_bench, geometric camera tools | exit 0 / within budget |
| G10 | `git diff --name-only HEAD` plus untracked files | every file in the package's list, its handoff or its gate folder |

Each trial record (`specs_own.jsonl`, `specs_realistic.jsonl`) holds the verdict fields (`passed`, `initial_fails`,
`idle_fails`, `reference_passes`, `perceivable`, every wrong plan's `fails` / `ran`) and the digests
(`reference_state_hash`, `reference_obs_hash` of the final observation, `idle_state_hash`, each wrong plan's
`state_hash`, `initial_hash`, `task_hash`, `reference_steps`, `reference_time_s`). Reviewed differences are rows of
the package's handoff table (catalog id | seed(s) | mode own/realistic | old -> new | R code | evidence | decision),
or `gate/<ID>/reviewed.json` / `--reviewed FILE` (`[{"catalog_id", "seeds": [..] or "all", "mode", "code"}]`).
`--only` re-runs some checks and keeps the recorded results of the others (for a flaky benchmark, say);
`--spec-files 'cleaning__*'` limits G2/G3 while iterating (such a record is partial and never a baseline).
`--shared-tree` lets G10 attribute files of packages running in the same sub-stage in one working tree (the
adoption round: every `ADOPT:<domain>`, `K-EVAL` and `K-CAM`).

**compare.** `compare PREV ID` compares two records. Without `ID` it takes the package's own record (`gate/<PKG>`;
a colon in a package id is written `_`: `gate/ADOPT_cleaning`), without `PREV` the previous gate that record names
(else the stage's default: the adoption round compares with `C1`); one positional names `PREV`. A record made with
`--spec-files` is compared on those spec files only (the previous gate's other trials are not "missing");
`--spec-files` on `compare` narrows both records further. `--shared-tree` also checks the working tree's scope (G10)
now, attributing the concurrent packages' files. Without a record, `compare --package PKG --shared-tree` prints the
`record` command to run (exit 2) and the scope check. The supported forms in the adoption round:

```
python tools/orchestration/tier1_gate.py record --id ADOPT_cleaning --package ADOPT:cleaning --only G0,G2,G3,G10        --spec-files 'cleaning__*' --shared-tree           # quick: the domain's own specs and the scope
python tools/orchestration/tier1_gate.py compare --package ADOPT:cleaning --shared-tree     # vs C1 (its prev)
```

**isolate.** On a shared tree a package can record on an isolated copy instead: `isolate --package PKG --out DIR`
copies the base commit's tree (`git archive`; nothing in the project is committed or changed), the empty
directories git does not carry (`tasks/templates`, `house_sim/data/actions`) and the package's changed files; the copy
records its base files' hashes, so G10 in it lists exactly that overlay. Record it with `record --root DIR
--gates-root <project>/catalog/analysis/tier1/gate --id <PKG>`; `isolate --out DIR2` without a package is the
pristine base to compare with. G9's physics audit writes `physics_audit.json` into the gate folder (as G9's body
audit does); spec chunks keep the authored key order.

## Tier-1 foundation workflows (2026-10-13..)

- `tier1_stage_a.js` - A0 (gate tool, timeline carve-out, sweep stubs) and the six stage-A libraries, review, fixes.
- `tier1_stage_b.js` - B0..B6: physics, camera and robot body integrated in merge order with merge gates, review, B-CLOSE.
- `tier1_stage_c.js` - TIME-EVAL || PEOPLE-IO (the sweep), merge, review, C-CLOSE.
- `tier1_adopt.js` - adoption round: 16 domain owners plus two kernel follow-up agents, then an integration gate.
Plans and contracts: docs/design/TIER1_*.md; package handoffs: catalog/analysis/tier1/handoff_*.md.
- `independent_review.js` - the independent review (claims, gaming policies, leak hunters, architecture, plan, report,
  evidence audit) run on a frozen copy; report in docs/reviews/2026-10-22_independent_review/.
- `verify_hardening_design.js` - design, adversarial critique and finalisation of docs/design/VERIFY_HARDENING.md.
