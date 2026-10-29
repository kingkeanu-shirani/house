# Orchestration scripts (used by the main session)

Workflow scripts and helpers used to run the multi-agent rounds. Replace `%PROJECT%` with the project folder
(and `%SCRATCH%` with a scratch folder) before running.

- `author_wf.js` — authoring wave: one agent per catalog batch (`catalog/analysis/batches/batch_NN.json`) writes and
  verifies `tasks/authored/batch_NN.json`, then an adversarial reviewer tries to game each spec. args: `{batches: [..]}`.
- `gapfix_wf.js` — domain owners implement gaps reported by authors (`catalog/analysis/gaps_open.json`).
  args: `{domains: [..]}`.
- `round4_wf.js` — the last domain fix round (template for the next one).
- `green_tests.py` — every tests/test_*.py, normal and HOUSE_SIM_STRICT=1, in parallel (`[jobs] [pattern]
  [--modes normal,strict] [--tests-dir DIR] [--json OUT] [--registry FILE|committed|none]`), through the gate's
  `_unittest` worker with the wall-clock bounds relaxed; exit status 1 when any run fails, a bound skips its body or a
  perf mark is unregistered (CI reads it). `--perf` asserts the bounds alone (best of 3).
- `wallclock.py` — the wall-clock bound mechanics (K-GATE3): perf marks by AST, the committed registry, the relax plan,
  the counting clock, and the helper `wall_clock_bound(self, elapsed, limit)` a bound calls instead of `assertLess`
  (`from tools.orchestration.wallclock import wall_clock_bound`: asserted in the perf job, recorded otherwise).
- `run_check.py` — orchestrator-run spec checks (`run FILE ... | --all`, `verify [--rerun N]`, `show`): the workflow
  or the main session runs `--check` itself and appends the verdicts to a hash-chained check log
  (`catalog/review/check_log/orchestrator.jsonl`); agents never self-certify. See below.
- `gate_policy/` — the gate's main-session policy files: the rules `rules.json` (the package table, sub-stages,
  budgets, features, protected paths), the dated allowlist `known_failing.json`, the wall-clock bound registry
  `perf_tests.json` and the second signatures `signoffs/<ID>.json` (read only as committed; see `gate_policy/README.md`).
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
python tools/orchestration/tier1_gate.py signoff [<PREV_ID> [<ID>]] [--package <PKG>] --by NAME --out FILE
python tools/orchestration/tier1_gate.py policy           # the committed allowlist and signatures, as read
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
| G1 | every `tests/test_*.py`, normal and `HOUSE_SIM_STRICT=1` (one process per file and mode; wall-clock bounds relaxed), and `tools/py39_lint.py` | all green, except unexpired allowlist entries; every perf mark registered, none skipping its body; no Python 3.9 violation |
| G1b | each domain's test file with `HOUSE_SIM_DOMAINS=<domain>`; kernel test files with `HOUSE_SIM_DOMAINS=core` | all green (absolute from phase 1; kernel files from B1 on), except allowlist entries |
| G1c | imports the package's library modules after every domain is loaded | nothing registered (TIER1_INTEGRATION 1.16); N/A without such modules |
| G2 | every authored spec in its own perception mode, seeds 0 1 2 41 97 (`verify.run_trial`) | G2-ABS: every spec passes on every seed (new ones included) unless allowlisted; every verdict change reviewed by the package (bound to its files) and signed by the main session; every edited spec signed; no digest change the package may not make |
| G3 | the same with perception forced to realistic | as G2 (recorded, not required to pass, without a previous gate); realistic specs run in both G2 and G3 and must give identical records |
| G4 | `tools/perception_audit.py 6 150 --workers 4` (+ `--camera geometric` once available) | exit 0; in `compare` also no increase and no dropped variant |
| G5 | `tools/layout_audit.py 40` and the generation-1 digests of `tests/data/gen1_digests.json` | counts equal to the previous gate; every pinned digest equal |
| G6 | `tools/fuzz.py 6 300 --workers 4` (+ `--camera geometric`, `--battery 5 --faults random` once available) and `tools/fuzz.py --coverage --workers 4` | exit 0 (no defect, deterministic replay of every house and walk); per-action success coverage reported, an action that stops succeeding needs a signature |
| G7 | `tools/bench_systems.py 40 0 1 2 3` and a 1 h wait on seeds 0-3, best of 5; then the wall-clock bounds alone | ms/substep and the wait within the package budget (rules.json: default 1.05 x previous gate; PHYS-INT 1.0 x A0; P2-WP2 1.15); no bound failing all 3 attempts |
| G8 | `tests.test_room_golden`, also with `HOUSE_SIM_GOLDEN_ALL=1` | green (required from B1 on); every golden entry re-recorded vs the base reviewed and signed (kind `golden`) |
| G9 | package extras: physics_audit, body_audit, generator compat + gen-2 layout + gen-2 timing, sweep_bench, geometric camera tools | exit 0 / within budget |
| G10 | `git diff --name-only HEAD` plus untracked files | every file in the package's list, its handoff or its gate folder; no `gate_policy/` file (main session only); with `--shared-tree` a file goes to a running peer whose scope has it, a protected path never; FAIL without git (an isolated copy needs none); N/A without `--package` |
| GATE | the gate code of the record (meta `gate`) | equal to the code committed at the record's commit, or a gate-tool package's with a `gate-change` signature |

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
python tools/orchestration/tier1_gate.py record --id ADOPT_cleaning --package ADOPT:cleaning --only G0,G2,G3,G10 --spec-files 'cleaning__*' --shared-tree
                                     # quick: the domain's own specs and the scope (ends INCOMPLETE)
python tools/orchestration/tier1_gate.py compare --package ADOPT:cleaning --shared-tree     # vs C1 (its prev)
```

**isolate.** On a shared tree a package can record on an isolated copy instead: `isolate --package PKG --out DIR`
copies the base commit's tree (`git archive`; nothing in the project is committed or changed), the empty
directories git does not carry (`tasks/templates`, `house_sim/data/actions`) and the package's changed files; the copy
records its base files' hashes, so G10 in it lists exactly that overlay. Record it with `record --root DIR
--gates-root <project>/catalog/analysis/tier1/gate --id <PKG>`; `isolate --out DIR2` without a package is the
pristine base to compare with. G9's physics audit writes `physics_audit.json` into the gate folder (as G9's body
audit does); spec chunks keep the authored key order.

## Gate hardening (phase 1 of the stabilisation, 2026-10-22; K-GATE)

The independent review (docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md H12, H13, H15, H17, M7;
phases/p2_tests_gate.md section 5.2) showed that "green" meant "no new difference from the previous gate": a new spec
failing on every seed passed, a package could review its own (or another domain's) regression, a spec could be cut to
a one-step reference, audit regressions were INFO, G10 passed without git, and nothing recorded why a wrong plan
failed. What the gate does now:

* **Absolute rules beside the delta rule.** G2-ABS: every authored spec passes in its own mode on every seed, new
  specs included (`record` and `compare`). G1 / G1b / G8: every test passes. The only exceptions are unexpired entries
  of `gate_policy/known_failing.json` (kind test or spec, owner, expiry, reason).
* **Two signatures for every difference.** A reviewed row (the first signature) is bound to the files its package
  owns: `ADOPT:cleaning` reviews `tasks/authored/cleaning__*.json` only; a kernel package may review any spec. Every
  verdict change, every edited spec (`spec_hash` changed) and every action the coverage fuzz no longer drives to
  success also needs the main session's row in `gate_policy/signoffs/<ID>.json`, naming the exact new verdict digest,
  spec hash or action (`signoff` drafts the file). Policy files count only as committed (agents never commit), and
  G10 fails a package that edits them.
* **Wrong plans.** Trial records keep each wrong plan's reason classes (`reasons`: the failure labels' classes plus
  `goal`), first labels, goal fraction, whether it ran fully, its error class and steps run. A change of `reasons`
  is a verdict change (compared when both records have them). A wrong plan that raises fails its spec
  (`house_sim/tasks/verify.py`).
* **SKIP counts as FAIL** (`N/A` marks a check that does not apply). G10 fails without git (an isolated copy needs
  none). `compare` fails on any G4 leak or crash and any G6 defect or nondeterministic replay, on every increase
  against the previous gate, on a variant the previous gate ran and this one did not, on failing tests outside the
  allowlist, on the record's own G1c / G9 / G10 failures, and when `--package` is not the record's own package.
  A partial or narrowed record ends `INCOMPLETE` (exit 0, `meta.json` `"handover": false`): an iteration aid, never
  a hand-over. `record` passes only when no check FAILs or SKIPs and its comparison passes.
* **Coverage.** G6 also runs `tools/fuzz.py --coverage` and reports per-action success coverage over every fuzz
  variant; the fuzzer replays every house (not the first 4). On 2026-10-22 (all 243 specs, 4 workers, a fully
  loaded 8-core machine, 37 min) it drove 283 of the 427 registered actions to success (the random fuzz: 46),
  replayed all 486 walks identically and found 2 strict-mode defects; the 144 actions it never drove to success
  are listed with their most common refusals in the JSON (`never_succeeded`, `refusals`).

The phase-1 package `K-GATE` (stage `P1`, previous gate `ADOPT`) lists the tooling files; the domain owners fixing
their own files in phase 1 use their `ADOPT:<domain>` packages (both are in sub-stage `P1` for `--shared-tree`);
`P1-INT` covers the phase-1 kernel agents K-FIX and K-LEAK and the integrator (ADOPT-INT's files plus
`tests/test_leaks_review.py`, `catalog/analysis/phase1/*` and the folder keepers). The
main session's merged record is `record --id P1 --package P1 --prev ADOPT` (`P1-ALL`: every domain's files, the
kernel side and K-GATE's; it may change the policy files, and every verdict change and edit still needs a reviewed
row and a committed signature).
The supported forms for a domain owner:

```
python tools/orchestration/tier1_gate.py record --id ADOPT_cleaning --package ADOPT:cleaning --prev ADOPT --shared-tree
python tools/orchestration/tier1_gate.py record --id ADOPT_cleaning --package ADOPT:cleaning --prev ADOPT \
       --only G0,G2,G3,G10 --spec-files 'cleaning__*' --shared-tree           # quick; ends INCOMPLETE at best
python tools/orchestration/tier1_gate.py compare --package ADOPT:cleaning --shared-tree     # vs the record's prev
python tools/orchestration/tier1_gate.py signoff --package ADOPT:cleaning --by main --out draft.json  # main session
```

## Phase-1 red team: FIX2 and the second gate pass (K-GATE2, 2026-10-28)

The red team (catalog/analysis/phase1/red_defects.json) asked who can make the gate say PASS. FIX2 (its fix pass;
tier1_gate.py's docstring has the rules) added per-test outcomes, tree fingerprints, an honest standalone compare and
policy files read from the project's commits only. K-GATE2 (catalog/analysis/phase1/finish_K-GATE2.json) closed what
those fixes left open, each with a regression test in tests/test_gate_tool.py (`SecondPassGateTests`):

* **Hand-over base.** A record or a comparison is a hand-over only against the package's base: the stage's default
  previous gate or the base of a sub-stage the package runs in (`SUBSTAGE_BASE`; phase 1: ADOPT for every
  `ADOPT:<domain>` and the P1 packages). `record --prev none` while a base exists, `--prev` naming the package's own
  earlier record (which already carries the regression), or a copy of the record itself end `INCOMPLETE`.
  `--stage` belongs to main-session ids without a package; `--seeds` fewer than 0 1 2 41 97 and `--extras` fewer
  than the package's narrow the record.
* **One tree.** `compare` fails TREE for a record whose tree is neither the project nor an isolated copy made from
  one of its commits (`record --root <any folder with its own git history>` passed its own TREE and G10 before).
* **What failed the record's own G3 / G6** is recomputed: the G2/G3 determinism check of the realistic specs,
  spec-worker crashes and the required audit variants (coverage, geometric, battery) even when the previous record
  lacks them too. Per-test ids, once a record has them, are required (a count hides a deleted test replaced by
  another).
* **Wall-clock bounds out of the correctness suite.** G1, G1b, G8 and the G9 test extras run with
  `HOUSE_SIM_PERF_TESTS=0` (each bound skips with "wall-clock bound off"; the `_unittest` worker records outcome
  `perf`); G7 runs them alone after the benchmarks, normal and strict, each passing when one of 3 attempts meets it
  (`green_tests.py --perf`), into tests.json `perf`. A bound moved there is excused against the previous record only
  when that job ran it; one failing every attempt fails G7 (compare: `PERF`). `green_tests.py` itself turns the bounds
  off unless the caller sets the switch; CI runs the perf job separately and informationally.
* **Coverage fuzz.** The head camera's pose and per-step logs (`robot.view`) are bookkeeping: a physical action that
  changed only them (open_valve on an open valve in a geometric spec) is a no-op success, not coverage.
* **Golden.** `tools/golden.py record` pins a failing reference only when the main session accepted it: on the
  committed allowlist or already pinned known-failing in the committed golden; `--known-failing` is checked against
  those, never a recorder's own excuse.
* **Verifier (interim, VERIFY_HARDENING 14.3 rule 1 is the full rule).** A wrong plan that never acts -- every action
  it attempted refused, waiting aside, a physical success that changed nothing counting as no act -- is not
  evidence (`NeverActed`); a strict-block refusal (safety, privacy, social, authority) and a declared `mistake` are
  left to the full rule.

## Phase-1 close-out: the gate's rules in committed policy (K-GATE3, 2026-10-28)

The independent verifier (catalog/analysis/phase1/finish_VER.json) found four holes that still got through; each is
closed with a regression test in tests/test_gate_tool.py (`CloseOutGateTests`) that replays the attack:

* **The rules lived in the unprotected gate script** (two edited lines lowered G7's budget and a 20 % slower record
  compared PASS). Every rule that decides a verdict is `gate_policy/rules.json` now, read from git HEAD (format:
  `gate_policy/README.md`; the former values are kept exactly and pinned by digest). Every record's `meta.json` `gate`
  lists the blob hashes of the gate code that made it and of `tools/orchestration/**`; check GATE fails a record made
  by code that differs from the committed one, unless its package is a gate-tool package with the main session's
  `gate-change` signature. A comparison run by a differing script is INCOMPLETE, as is one with rules or a bound
  registry that are not committed yet.
* **G10 --shared-tree attributed protected files and other owners' tests to peers.** A protected path
  (`tools/orchestration/**` with the policy, `.github/**`, the room golden, the generation-1 digests) is never
  attributed; another file only to a peer of a running sub-stage (rules `running_substages`) whose own scope contains
  it; every attribution is listed in the note, and one in a sub-stage that is not running makes the record no
  hand-over. On a shared tree with the gate tool's files changed, record on an isolated copy (`isolate --package`).
* **A hand-edited golden neutralised any pin.** `tools/golden.py` honours a known-failing pin only while the committed
  allowlist excuses it, and G8 lists every entry that differs from the base (the previous record's entries, else the
  base commit's) as a re-record needing a reviewed and a signed row of kind `golden`.
* **A failing test disguised as a wall-clock bound kept CI green.** The correctness runs (gate G1 / G1b / G8, CI's unit
  suite via green_tests.py) run every perf-marked test relaxed through the `_unittest` worker (`wallclock.py`): its body
  and functional assertions run, its timing cannot fail (a counting clock; or the `wall_clock_bound` helper, which
  records instead of asserting). Every perf mark is registered in `gate_policy/perf_tests.json`; an unregistered one
  fails the run, and a bound that still skips its body counts as a skip unless the registry gives it a dated exception
  pinned to its source (one today: the thermal relative bound, until 2026-11-30).

Also: `tools/py39_lint.py` (stdlib, Python 3.9 grammar and 3.10+ runtime features) runs in CI and in G1 (the gate's own
copy over the tree); the close-out package **P1C** and the phase-2 packages **P2-S1 ... P2-S6, P2-ALL** (VERIFY_HARDENING
section 25) are in the rules. The close-out record: `record --id P1C --package P1C --prev P1F` (reviewed rows cite
`P1C`; the integrator's non-domain fixes join with `--package P1C+P1C-INT`).

## run_check.py (orchestrator-verified checks)

```
python tools/orchestration/run_check.py run tasks/authored/batch_07.json --workers 2 --role author --batch 07 \
       --agent author:07 --claimed-file claimed.json        # or --claimed ID ...; --all for every spec file
python tools/orchestration/run_check.py verify [--rerun N]  # the chain, and re-run the last N entries
python tools/orchestration/run_check.py show [--last N]
```

`run` verifies every spec of the files itself (the same `verify_many` as `python -m house_sim --check`), compares the
passing set with the agent's claimed list, and appends an entry (files with their sha256, options, code
fingerprints, per-spec verdicts with reasons, passing / failing, the claim mismatch) to
`catalog/review/check_log/orchestrator.jsonl`. Each entry carries the sha256 of its canonical JSON and the previous
entry's digest, so an edited, removed or inserted entry breaks the chain; `verify --rerun N` re-runs the last N
entries whose files and code are unchanged and fails when a verdict differs. The last printed line is a one-line JSON
summary for a workflow agent to return verbatim; exit 1 when a spec fails or the claim does not match.
`author_wf.js` runs it after every author and review step (a checker agent that only runs this command) and reports
the tool's passing set, not the agents'.

## Tier-1 foundation workflows (2026-10-13..)

- `tier1_stage_a.js` - A0 (gate tool, timeline carve-out, sweep stubs) and the six stage-A libraries, review, fixes.
- `tier1_stage_b.js` - B0..B6: physics, camera and robot body integrated in merge order with merge gates, review, B-CLOSE.
- `tier1_stage_c.js` - TIME-EVAL || PEOPLE-IO (the sweep), merge, review, C-CLOSE.
- `tier1_adopt.js` - adoption round: 16 domain owners plus two kernel follow-up agents, then an integration gate.
Plans and contracts: docs/design/TIER1_*.md; package handoffs: catalog/analysis/tier1/handoff_*.md.
- `independent_review.js` - the independent review (claims, gaming policies, leak hunters, architecture, plan, report,
  evidence audit) run on a frozen copy; report in docs/reviews/2026-10-22_independent_review/.
- `verify_hardening_design.js` - design, adversarial critique and finalisation of docs/design/VERIFY_HARDENING.md.
