# house_ai independent review: Phase 5 (Judge the plan)

Reviewer: Claude (Opus 5.5), subagent "p5_plan". Date: 2026-10-22, about 01:05-02:05 local time.
Code reviewed: the frozen copy of commit `bc0417b` at
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/tree`
(no `.git`). The live repository at `<home>\OneDrive\Desktop\house_ai` was not touched. Every script and output
is in
`<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/review/out/p5_plan_scratch/`
(called `S/` below). Nothing was written inside the tree (checked with `find -newer` after all runs).

Evidence labels: **[ran]** = I executed it and saw the result; **[read]** = I read code or docs but did not run it;
**[inferred]** = reasoning only. "P1", "P2-cond", "P2-a/b/c", "P2-gate", "P3-id", "P3-side", "P4" refer to the earlier
phase reports in `out/` (`p1_claims.md`, `p2_conditional.md`, `p2_gaming_a/b/c.md`, `p2_tests_gate.md`,
`p3_leaks_identity.md`, `p3_leaks_side.md`, `p4_arch.md`); a number taken from them keeps their label and says so.
Numbers I call **estimates** are estimates.

Hard rules: no paid API call and nothing sent off the machine. The only LLM commands I ran were `--dry-run` estimates
(the estimator builds requests and never sends them; the default transport refuses every request and
`HOUSE_AI_FORBID_NETWORK=1` was set). Every command ran after `source S/env.sh`, which points `USERPROFILE` and `HOME`
at the empty folder `S/fakehome`, forbids the network and unsets every API, split and verifier key variable; my
Python scripts assert this before importing anything. `Path.home()` resolved to the fake home [ran]; the fake home is
still empty after every run [ran], so no code path tried to create a key. I never read, listed or created anything
under the real `%USERPROFILE%\.house_ai\` and never ran `--init-split-key`, `--split-info` or any test-split command.
Worker counts were 2-3 (the machine was shared with another heavy job, including someone else's `tier1_gate.py`).

---

## Status update (end of Phase 5)

### Simple version

I judged the owner's plan for finishing the project and tested the parts I could test without spending money. The
plan's order is half right: building the physics, camera and robot foundations before writing 2,500 more tasks saved
a lot of rework, but the plan never fixes the way tasks are checked, and earlier phases showed that check lets most
attacked tasks be passed by cheating. So writing 2,500 more tasks now would mostly multiply that problem. I recommend
fixing the checker first, then repairing the 243 existing tasks, then writing a carefully chosen core of about 500
tasks (I picked them with a program: all 105 sections and every safety-critical entry are in), and only then deciding
whether the rest is worth writing. I also designed the first real AI test run: the cost estimate from the project's own
tool is about $110-280 for the whole pilot (plus a $10 warm-up), and nothing was spent.

### Advanced version

* Phase 5, about 1 h elapsed (01:05-02:05). No blockers. Nothing needs an owner decision before Phase 6; the paid pilot in
  section 11 needs the owner's explicit approval before anyone runs it.
* Commands and results: section 2. Scripts: section 3. Findings, ranked, with labels: section 12.
* Headline measurements [ran]:
  * **D3 preview** (all 243 specs verified with perception forced to realistic and the geometric camera, 1,215 trials,
    1,316 s): **192/243 pass; 51 fail (149 trials)**, every failure through the reference plan; 38 of the 51 passed
    realistic room mode in P2-gate's run; 27 fail because the reference approaches something it never looked at ("That is not visible
    here").
  * **D2 preview** (generation 2 forced, 1,226 s): **228/243 pass**; 15 specs fail on one seed each, mostly "no
    hazard-free route" in that particular house.
  * **Metamorphic prototypes** over all 243 specs x 5 seeds (593 s): utterance swap (all speech text replaced by
    "Beep.") still passes in **11 of the 13** specs that have speech steps; 9 of the 11 are real content-blind speech scoring
    (5 confirmed by my reading, 4 already by P2-c), including two fire-safety warnings; 1 is arguable, 1 benign. Keep-holding and destination-swap at "last
    placement" granularity flag 17/43 and 16/40 specs, but most flags are incidental tool returns (0 of 8 adjudicated
    keep-holding flags hit the task's deliverable): a useful null result about how such checks must be targeted.
    Label swap: 0 of 4 flagged (null; label content is checked).
  * **Re-harden workload**: 198 of the 243 existing specs carry at least one issue that this review measured or
    flagged (union of 10 lists; section 8.6).
  * **Authoring load**: the 52 prepared batches mix a median of 9 primary domains each; the reading
    `author_wf.js` prescribes comes to a median **452k tokens per agent (737k with secondary domains)**, against
    111k-131k if batches were grouped by domain (4 chars/token, an estimate).
  * **Dry-run costs** (the owner's command and variants, no calls): the requested `--limit 10` run is 10 episodes of
    only 2 specs, expected ~$23.35, worst $238.48. Full dev split (1,215 episodes): Opus 5.5 ~$3,758 expected at the
    authored step budgets (worst $39,064), Sonnet 5.5 ~$2,242, Haiku 5.5 ~$112. Pilot (section 11): ~$110 at the
    estimator's 400 output tokens per step, ~$279 at 3,000 (thinking is always on for Opus 5.5, so the 400 default is
    probably low).
  * **Free baselines on the pilot episodes**: scripted oracle 48/48, random 0/48, do-nothing mock 0/48; the do-nothing
    mock's mean raw goal fraction is 0.356.
  * **Core-500 selection** (deterministic script): 500 entries (242 existing + 258 new), all 105 sections, all 36
    risk-3 entries, 823 of 1,233 mechanics prefixes (random 500: mean 542), 51 % judgement entries.
  * Price table check [read, claude-api skill cached 2026-10-06]: `house_sim/eval/prices.json` matches the current
    list prices for Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.1.
* Open questions for the owner: (1) approve or decline the $10-capped calibration run P0 (section 11.6); (2) is a
  core-500 v1 release an acceptable first deliverable, with the long tail conditional on measured audit results;
  (3) is "one attempt per hidden test seed" the intended rule (it decides how much P3's handle findings matter).
* Next: Phase 6 (final report) should take its "Top 10" order from section 13 here.

---

## 1. What I read

All of: the reviewer prompt, the brief, every earlier phase report (`p1_claims.md`, `p2_conditional.md`,
`p2_gaming_a.md`, `p2_gaming_b.md`, `p2_gaming_c.md`, `p2_tests_gate.md`, `p3_leaks_identity.md` (sections 0-4, 6-7),
`p3_leaks_side.md` (status and findings), `p4_arch.md`). In the tree: `PROGRESS.md`, `PLAN.md`, `RESUME.md` (grep),
`tools/orchestration/author_wf.js`, `gapfix_wf.js`, `README.md` (orchestration), `tools/make_batches.py`,
`docs/AUTHORING_GUIDE.md` (structure), `docs/design/TIER1_INTEGRATION.md` lines 630-640, 936-946, 1056-1105, 1414-1424,
`house_sim/eval/run.py` lines 780-1279 (run, dry run, CLI), `house_sim/eval/llm.py` lines 1-215 and 655-1093
(prices, providers, agent, estimator), `house_sim/eval/prices.json`, `house_sim/tasks/verify.py` (function list),
`house_sim/tasks/skills.py` `put`. The claude-api skill's model and price table (cached 2026-10-06), to check
`prices.json`.

## 2. Commands run and results

All from the frozen tree as working directory after `source S/env.sh`.

| # | Command | Result |
|---|---|---|
| 1 | `python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --limit 10 --dry-run` (the brief's command, verbatim) | exit 0; "243 of 243 selected specs lack current passing verification evidence ... Estimating all of them"; **10 episodes = 2 specs x 5 seeds** (`T-ce25a2474ffb3d-01` 60 steps, `T-f14db0adf450bb-01` 80 steps); ~25.3M input tokens (mostly cached system prompt), 280k output; **expected ~$23.35, worst $238.48**. Per step: ~36.1k input tokens, of which ~31.9k system prompt and ~4.2k turn (derived from the row: 60 steps, 2,167,380 input tokens, $2.0194) |
| 2 | `python S/scripts/p5_dry.py S/dry/opus_dev_full_room.json llm:anthropic:claude-opus-5-5` (calls `eval.run.run(..., dry_run=True)`, the code behind `--dry-run`) | full dev split: **1,215 episodes, 114,650 steps, expected $3,758.22, worst $39,063.50**, 60 s |
| 3 | same, `--camera geometric` / `claude-sonnet-5-5` / `claude-haiku-5-5` / `--max-steps 40` | Opus geometric **$3,623.86** (more input tokens but a lower cost; my reading [inferred]: a larger cached system prompt and a smaller uncached turn); Sonnet 5.5 **$2,241.57**; Haiku 5.5 **$112.08**; Opus with a 40-step cap **$1,683.58** (48,100 steps) |
| 4 | `python S/scripts/catalog_profile.py S/catalog_profile.json` | 2,720 entries, 105 sections, 37 sections with no authored spec; 128 duplicate groups hold 278 entries (2,570 distinct texts); risk 0/1/2/3 = 1,723/724/237/36; 798 non-null `conditional`; target fidelity S 1,193 / G 906 / P 621; 632 need a user simulator; **10,412 distinct mechanics tags, 8,815 used once**, 1,233 distinct prefixes; analysis `objects` match registered type ids for 39.5 % of objects on average (26 entries fully); 52 batches, 18-54 entries, 2,478 ids (1 already authored) |
| 5 | `python S/scripts/batch_load.py S/batch_load.json` | prescribed core reading 75k tokens; **per batch (primary domains) median 452k, p90 574k, max 681k tokens; with secondary domains median 737k**; primary domains per batch: median 9, max 14; regrouped by primary domain: **111k-131k** (all at 4 chars/token, an estimate) |
| 6 | `python S/scripts/core_select.py S/core500 500` | core of 500 (rules R0-R6, section 8.2): 242 authored + 27 risk-3 + 163 section fill + 41 kind quota + 27 coverage; kinds T 348 / A 69 / X 33 / P 40 / V 10; 105/105 sections; risk >= 2: 108, risk 3: 36/36; judgement 257; 823 mechanics prefixes; 258 new to author; list in `S/core500.json` |
| 7 | `python S/scripts/random_baseline.py` | random 500 (50 draws): 542 prefixes (503-589), 91.1 sections (min 85), 6.6 risk-3; **two authored specs are duplicates** ("Report damaged cable." in two sections, both in `electrical__perception.json`) |
| 8 | `python S/scripts/audit_oc.py` | acceptance-sampling operating characteristics (section 9.4) |
| 9 | `python S/scripts/spec_table.py S/spec_table.json` | per spec: reference steps (P1's own-mode run) median 15, p90 44, max 135; step budget median 80, p90 160, max 400; budget / reference steps median 5.0 |
| 10 | `python S/scripts/budget_cost.py` | full dev Opus cost as authored $3,758; step budget `min(budget, max(30, 3*ref+10))` **$2,777 (74 % of steps)**; `max(20, 2*ref+10)` $2,103; cap 40 $1,601; the 3x rule would cut 184 of 243 budgets |
| 11 | `python S/scripts/full_verify_mode.py S/d3/verify_realistic_geometric.jsonl --perception realistic --camera geometric --workers 3`, then `analyze_modes.py` | **D3 preview: 192/243 pass, 51 fail, 149/1,215 trials**; all 51 fail on the reference (6 also not perceivable); 16 fail on all 5 seeds; the 13 specs that fail realistic room mode (P2-gate G3) all fail here too, **38 are new**; 27 fail with "approach X failed: That is not visible here"; 1,316 s |
| 12 | `python S/scripts/metamorphic.py S/meta/metamorphic.jsonl --workers 2`, then `analyze_meta.py` | 1,215 (spec, seed) jobs, 593 s, 0 harness errors. Flagged = mutant passes on >= 1 seed: **hold 17/43 applicable, dest 16/40, beep 11/13, label 0/4** (lists in section 10.4) |
| 13 | `python S/scripts/adjudicate_view.py hold ...` / `beep ...` / `dest ...` | read 8 hold, 7 beep, 4 dest flags (section 10.4) |
| 14 | `python S/scripts/reharden_union.py` | **198/243 specs have >= 1 known issue**; 45 have none of the 10 listed (section 8.6) |
| 15 | `python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --ids <24 pilot ids> --seeds 0 1 --max-steps 60 --agent-opt effort=medium --verify-on-the-fly --verify-perception realistic --verify-workers 3 --max-cost-usd 150 --max-episode-cost-usd 6 --dry-run` | on-the-fly realistic verification ran offline (2 min) and **kept all 24 specs**; 48 episodes, up to 2,660 steps; **expected ~$89.22**, worst $150 (run cap; uncapped $288 with the $6 episode cap) |
| 16 | same with `--agent-opt expected_output_tokens=1500` / `=3000` | **$147.74 / $227.54** expected; worst $905.58 without the episode cap |
| 17 | Haiku 5.5 arm, same episodes, `=400` / `=3000` | **$2.65 / $6.11**, worst $22.64 |
| 18 | Opus retest arm: 8 specs, seed 0, `--no-cache`, `=400` / `=3000` | **$15.66 / $39.58**, worst $156.84 |
| 19 | Haiku arm forced `--camera geometric`, `=400` / `=3000` | **$2.57 / $6.03** |
| 20 | P0 calibration: 4 specs, seed 0, `--max-steps 30`, `--max-cost-usd 10`, `=400` / `=3000` | **$4.25 / $10.49** expected (stops at $10) |
| 21 | `python -m house_sim --run-agent oracle|random|mock --split dev --ids <24 pilot ids> --seeds 0 1 --max-steps 60 --allow-unverified --no-replays --workers 3 --out S/baselines/<agent> --quiet` | **oracle (privileged) 48/48 success**; **random 0/48** (42 declared done, 6 ran out of steps); **mock (does nothing) 0/48**; mean raw goal fraction of random and mock: **0.356**; 22-80 s each |
| 22 | `python S/scripts/full_verify_mode.py S/d2/verify_gen2.jsonl --gen 2 --workers 3`, then `analyze_modes.py` | **D2 preview: 228/243 pass; 15 specs fail, one seed each** (14 reference, 1 setup error), 1,226 s (section 6.1) |
| 23 | `find . -newer ../out/p4_arch.md -type f` in the tree; `ls S/fakehome` | nothing written in the tree; fake home empty |

## 3. Scripts I wrote (all in `S/scripts/`)

| Script | Purpose |
|---|---|
| `p5_dry.py` | the project's dry-run estimator over a whole split, rows dumped to JSON (asserts fake home, no network, no keys) |
| `catalog_profile.py` | catalog, analysis, batch and authored-coverage profile |
| `batch_load.py` | prescribed reading load per authoring batch, and with batches regrouped by domain |
| `core_select.py` | deterministic core selection (rules R0-R6, section 8.2); writes `S/core500.json`, `S/core500_stats.json` |
| `random_baseline.py` | random-500 comparison; duplicate authored specs |
| `audit_oc.py` | hypergeometric / binomial operating characteristics of the audit plans |
| `spec_table.py` | per-spec table for pilot selection (reference steps, budgets, review status) |
| `budget_cost.py` | LLM cost under tighter step budgets |
| `full_verify_mode.py` | `verify_many` over all specs with forced perception, camera or generator (D2 / D3 previews) |
| `analyze_modes.py` | failing component per spec for a forced-mode run |
| `metamorphic.py` | prototype metamorphic checks: keep-holding, destination swap, utterance swap, label swap |
| `analyze_meta.py`, `adjudicate_view.py` | flag summary; what a reviewer needs to adjudicate a flag |
| `reharden_union.py` | union of the review's issue lists over the 243 specs |

Outputs: `S/dry/*.txt|json`, `S/baselines/*`, `S/d3/*`, `S/d2/*`, `S/meta/*`, `S/core500*.json`,
`S/catalog_profile.json`, `S/batch_load.json`, `S/spec_table.json`, `S/reharden_union.json`, `S/audit_oc.txt`,
`S/pilot_ids.txt`.

---

## 4. The plan under review (brief sections 5-7, restated)

1. Adoption integration (full gate, re-records, kernel requests) after the other 10 domains adopt tier-1.
2. D1: switch physics laws on where every owning domain is "ready to flip".
3. D2: generator v2 as the default.
4. D3: geometric camera as the default for realistic perception, with spec triage.
5. A final whole-system red team.
6. Authoring pilot: 8 diverse batches (author agent, then an adversarial reviewer who writes gaming plans).
7. Full wave of 52 batches, gap-fix rounds, re-authoring until no gaps remain.
8. Final verification of all 2,720 entries, docs rewrite, packaging.

Estimates in the brief: foundations finish about Oct 21-22; authoring 50-80M agent tokens, roughly 2-3 weekly usage
allowances; completion early to mid November; a full dev-split LLM run about $900-3,400.

## 5. Was "foundations before authoring" the right order? (owner question 5a)

**Partly. Building physics, camera, body and time before mass authoring was right, because the measured churn shows
that specs written earlier would have been rewritten; but the plan skipped the foundation a benchmark depends on most,
the verification method itself, and it has no step that measures a real agent before 2,477 more specs are written.**

Support:

* For the order [ran by P2-gate]: the adoption round edited 88 of 89 adopted-domain specs (`spec_hash` changed in 440
  of 445 trials) and changed every generated house. [ran, this phase] Flipping the camera default (D3) makes 51 of 243
  references fail. Specs authored before these foundations would have been re-triaged at catalog scale.
* Against the order as executed [ran by P2/P3, this phase]: the foundations were prioritised by a coverage sweep of the
  catalog ("cross-cutting needs found by scanning all 2,478 remaining entries"), not by validity. Meanwhile the
  verifier still accepts wrong plans that fail for the wrong reason or crash (P1 F1/F5), conditional specs that do not
  test their condition (P2-cond: 10 of 22 sampled), relocation and content-blind speech (P2-a/c; this phase: 11/13
  utterance-swap flags), and memorisable answers (P2-a G3, P3-id I1/I2). Tier-1 added rubric, phases, never/always
  and questions, but the verified specs barely use them (P1 F12).
* No real agent has been run [read: brief 6.5; ran: `reports/` evidence is stale, P1]. The decisive questions (is any
  agent's score meaningful, which holes do real agents find, what does a run cost) have been deferred to after
  authoring, where answers are most expensive to act on.
* The plan's own step 5 (a final red team) treats red-teaming as a one-off. This review is in effect that red team; its
  results only scale to 2,477 new specs if they become automatic checks (section 10), not hand fixes.

## 6. Step-by-step judgement of the remaining plan

| Step | Verdict | Evidence | Change |
|---|---|---|---|
| 1 Adoption integration | **Unsafe as planned** | 3 of the "re-record" failures are real defects (P2-gate T1) [ran by P2]; the adoption gate command exits with an argparse error and no adoption handoffs exist (P2-gate T4) [ran by P2]; generation-1 change unattributed (T6); `tier1_adopt.js` still truncates reports with `.slice(0, 30000)` (P4 A3) [read by P4] | fix the 3 defects first; `golden.py dump/diff` 0e77b67 vs bc0417b; correct the gate command; reports as files; then re-record once |
| 2 D1 law flips | **Under-scoped** | forcing the 6 "ready" laws breaks 8/243 specs in 4 unlisted domains via `stack` (P4 A1) [ran by P4]; readiness is checked per owner | per-law global preview over all specs and tests (P4 D1-GLOBAL); fix the stack rule and the ironing board's in-use size first |
| 3 D2 gen-2 default | **Under-scoped** | winder stairs break 8 specs' references on held-out seeds, none of the 5 verification seeds has them (P2-cond F-C2) [ran by P2]; 3 furnishing types requested but missing (P2-gate T10); the street address encodes `seed mod 900` and thus every variant (P3-id I1) [ran by P3]. D2 preview this phase: see the D2 row below | bundle every change that alters all scenes (D2, variant selection by hash, address stream, position shuffles) into ONE re-baseline event with one golden re-record |
| D2 preview [ran] | **Triage measured: 15 specs, 1 seed each** | 228/243 pass with generation 2 forced (own perception mode); 15 trials fail, 14 on the reference (7: "No route ... that avoids stepping on sharp or fragile things, sleeping animals or people"), 1 setup error ("No room for the package"); 4 of the 15 are in already-adopted domains | small triage; the failures are layout-specific, the same fragility as the held-out-seed failures (P2-cond F-C2), so V-HELD (10.1) is the durable fix |
| 4 D3 camera default | **Triage measured: 51 specs** | [ran] 192/243 pass forced realistic + geometric; 51 fail (149 trials), 38 of them new versus realistic room; 27 because the reference approaches what it never looked at; `--verify-on-the-fly` ignores `--camera` [read `run.py:276-282`], so a geometric harness run would keep episodes whose reference fails | do the triage, and use it to remove privileged references (P1 F8); pass `camera` to `verify_on_the_fly`; fix L2 (`view.px` true size) and P3-id I5 (never-framed handles accepted) before making geometric the default |
| 5 Final red team | **Wrong form** | this review found its classes by hand; one-off fixes do not protect 2,477 new specs | turn the review's attacks into the automatic suite of section 10, then red-team the suite with planted defects |
| 6 Authoring pilot (8 batches) | **Too big to be a pilot, too unmeasured to be a gate** | 8 batches = about 380 entries (15 % of the remainder); batches span median 9 domains (452k tokens of prescribed reading) [ran]; verification is self-reported, the reviewer edits the spec and is the last verifier, gaps are not audited, `author_wf.js` is stale against kernel v3 (P4 A3) [read by P4] | the pilot is the re-hardening of the 243 existing specs plus 2 domain-grouped mini-batches of new core entries, with a 100 % independent audit and planted defects (section 9) |
| 7 Full wave + gap rounds | **Do not start** | the process would scale a defect rate the review measured as high (section 8.6); gap fixes are verified per owner and the owner may edit specs (`gapfix_wf.js`) [read]; "re-author until no gaps remain" has no stopping rule | replace with core-first tiers (section 8); a stop/continue decision on measured audit results |
| 8 Final verification of 2,720 | **Compute grows** | per trial 3.25-3.7 CPU-s measured on this machine [ran: D3 run, P1]; one mode over 2,720 specs x 5 seeds is about 13 CPU-h, gate G2+G3 about 26 CPU-h (3.8 h on 7 workers) [inferred from ran]; the v2 checks of section 10 multiply that by about 5 | incremental gating by domain dependency plus a nightly full run |
| missing | **No real-agent pilot anywhere** | the plan lists no LLM run before final verification | P0 calibration now, pilot after re-hardening (section 11) |

### 6.1 D2 preview

[ran] `python S/scripts/full_verify_mode.py S/d2/verify_gen2.jsonl --gen 2 --workers 3` (1,226 s), then
`analyze_modes.py`. Generation 2 forced for every spec, each in its own perception mode:

* **228/243 specs pass; 15 fail, each on exactly one seed** (15/1,215 trials).
* 14 fail on the reference: 7 with "No route in X that avoids stepping on sharp or fragile things, sleeping animals or
  people", 5 with the reference running but success false, 1 "Approach it first; it is out of arm's reach", 1 "No
  clear path to the doorway".
* 1 fails with a setup error ("No room for the package").
* 4 of the 15 are in the six already-adopted domains.

Failing: T-7673a125fe70cd-01, T-8fb945d433137c-01, T-22bc2280c7e6be-01, T-259d38eb6707f0-01, T-fffb3941b40c87-01,
T-94a0a940224c22-01, T-97f43b147c3c2f-01, T-c072906a11f55c-01, T-5d63abe55f7147-01, T-e9cf6ca709ec77-01,
T-79a861877a2a67-01, T-0adf708ce0749a-01, T-89530941a65283-01, T-301e5aee8a71a0-01, T-108baebbfdf256-01.

Reading: D2's triage is small, but every failure is "this particular house". References depend on layout luck, the
same weakness the held-out seeds exposed. Adding V-HELD to the gate fixes this durably; triaging the 15 one by one
does not.

## 7. Cost and time estimates

### 7.1 LLM evaluation cost [ran: the project's own estimator; the figures themselves are estimates]

| Run | Episodes | Opus 5.5 | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|---|---|
| full dev split, authored step budgets, 400 output tokens/step | 1,215 | $3,758 (worst $39,064) | $2,242 | $112 |
| same, geometric camera | 1,215 | $3,624 | | |
| same, step cap 40 | 1,215 | $1,684 | | |
| same, budget rule `min(budget, max(30, 3*ref+10))` | 1,215 | ~$2,777 | | |
| core-500, 1 seed, authored-style budgets (scaled from the full-dev mean $3.09/episode) | 500 | ~$1,550 | | ~$46 |

* The brief's "about $900-3,400" for the full dev split is **low for Opus 5.5 at the authored budgets** ($3,758). It
  is inside the range only with a step cap or another model.
* The estimator assumes every episode uses its whole step budget (realistic for failing agents, pessimistic for good
  ones) and **400 output tokens per step**. Opus 5.5 cannot turn thinking off and defaults to effort `medium`
  [read: claude-api skill]; thinking is billed as output. At 1,500 and 3,000 output tokens per step the pilot estimate
  rises 1.66x and 2.55x [ran]. The real value is unknown until a calibration run.
* The estimator counts 3 characters per token on purpose (`CHARS_PER_TOKEN = 3.0`, "deliberately conservative")
  [read `llm.py:53`]; input may therefore be overestimated somewhat, output underestimated. Output dominates the
  uncertainty.
* `prices.json` (as of 2026-10-06) matches the current list prices: Opus 5.5 $4 / $20, cache read $0.20; Sonnet 5.5
  $2 / $10, cache read $0.20; Haiku 5.5 $0.10 / $0.50 (above 100k prompt tokens $0.50 / $2.50); Fable 5.1 $10 / $50
  [read: claude-api skill cache of 2026-10-06; not checked against the live pricing page, which would need the
  network].

### 7.2 Authoring token cost (the brief's 50-80M) [read, ran, inferred]

* It cannot be checked: the tree keeps no token logs of earlier rounds [read: no usage records in
  `catalog/analysis/*`, `round*_results.json` or handoffs].
* It is internally inconsistent with the prescribed workflow [ran + inferred]: one reading of the prescribed material
  is a median 452k tokens per agent; 52 authors plus 52 reviewers reading once is about 47M input tokens before a
  single spec is written or any context is re-read. The estimate holds only if the meter excludes cached context or the
  agents skip most of the reading. The second is a quality risk (authors who never read a domain's page write specs
  that misuse it).
* Grouping batches by primary domain cuts the prescribed reading to 111k-131k tokens per agent [ran] and is the
  single cheapest lever on both cost and quality.
* What to measure instead (section 9.7): tokens per **accepted** spec (input uncached, cache reads, output), wall time
  and CPU per accepted spec, and rework rate, on the re-hardening pilot. Extrapolate only from that.

### 7.3 Time [inferred, estimates]

* The brief's "foundations finish about Oct 21-22" has already slipped by the brief's own account: as of Oct 22 the
  remaining foundation work is 10 domain adoptions, integration, D1-D3 and the red team.
* Velocity anchor [read: brief s.4]: one weekly allowance covered roughly tier-1 stages A-C plus 6 of 16 domain
  adoptions. Against that, my rough sizes in allowance-weeks (estimates, low confidence): finish adoption, integration
  and fix the 3 defects 0.5-1; verification v2 and the scene-changing leak fixes 0.5-1; D1-D3 re-baseline with 51+8
  spec triage 0.5-1; re-harden 243 specs 0.5-1; author 258 core entries at the Gold bar 0.5-1.5. **Core-500 v1 is
  plausible by late November; the full catalog at a defensible bar is a January-scale effort** (estimates).
* Machine time is not the bottleneck for the core, but it becomes one for the full catalog: the gate's G2+G3 over
  2,720 specs is about 3.8 h on 7 workers, about 19 h with the v2 checks [inferred from ran per-trial cost].

## 8. Should authoring be cut or tiered? A concrete core-first strategy (owner question 5b)

**Yes: tier it, and make a high-quality core of about 500 entries the product; write the long tail only if the core's
measured audit results say the process is honest.** The core below exists as a file (`S/core500.json`), chosen by a
deterministic script so the choice can be reproduced and argued with.

### 8.1 What to cut or fold before authoring anything [ran for the counts]

* **Duplicates**: 128 duplicate groups hold 278 entries; 150 entries repeat another's text. Author one spec per group
  and add section-context variants. Two existing specs already duplicate each other ("Report damaged cable.").
* **Variation entries (74)**: they are randomisation axes (door handles, floor materials, dirty camera, low battery),
  not tasks. Implement them as perturbation operators applied to every core spec (a variation passes when the core
  still verifies under it), plus about 10 standalone specs where the entry is itself a task.
* **P-approx targets (621)**: no Gold tier; at most Silver with the label shown in every score table.
* **Fine-contact / dexterous entries** that cannot be decided by simulated mechanics: Bronze ("reported gap, not
  scored"), signed by the domain owner, so the gap loop has an end.

### 8.2 Which ~500 entries, and how they were chosen [ran]

`core_select.py` applies, in order:

* **R0** one entry per duplicate group (2,570 candidates);
* **R1** the 243 existing specs (242 after dedup) as re-harden candidates;
* **R2** every risk-3 (safety-critical) entry;
* **R3** at least 3 entries per section;
* **R4** kind quotas (>= 40 primitives, 60 adaptability, 15 appendix, 10 variation);
* **R5** at least 35 % judgement entries (non-null `conditional` or adaptability);
* **R6** fill to 500 by greedy marginal coverage of mechanics-tag prefixes, preferring target fidelity S/G, then risk.

Ties break on sha256(id).

| | Catalog (2,720) | Existing 243 | **Core 500** | Random 500 (50 draws) |
|---|---|---|---|---|
| sections covered | 105 | 68 | **105** | 91.1 (min 85) |
| risk-3 entries | 36 | 9 | **36** | 6.6 |
| risk >= 2 | 273 | 45 | **108** | |
| judgement entries | 950 | 119 | **257** | |
| mechanics prefixes (of 1,233) | 1,233 | 352 | **823** | 542 (503-589) |
| kinds T/A/X/P/V | 2,240/253/45/108/74 | 200/24/14/4/1 | **348/69/33/40/10** | |
| target fidelity S/G/P | 1,193/906/621 | 109/73/61 | **227/167/106** | |
| new specs to author | | | **258** | |

Caveats [ran + inferred]: mechanics tags are free text (8,815 of 10,412 used once), so "prefix coverage" is a proxy for
breadth, not a capability map. The analysis `objects` do not name registered types (39.5 % match), so feasibility
cannot be computed before authoring. Hence the next step: a **15-minute feasibility triage** of each of the 258 new
entries by its domain owner (S/G achievable with existing mechanics?). An infeasible entry is replaced by the
next-ranked entry of the same stratum (same R-rule, same section or kind). The R2 picks are deliberately hard (all 36
risk-3 entries, e.g. grease fires, medication, gas); several will need new mechanics.

### 8.3 The quality bar for core ("Gold") specs

A Gold spec must satisfy all of:

1. **G1 automatic suite**: passes every applicable check of section 10, in the evaluation mode (realistic perception;
   geometric camera once D3 lands), on its balanced verification seeds.
2. **G2 held-out**: on the 10 public held-out seeds, 0 idle passes, 0 wrong-plan passes, 0 adversary passes; reference
   >= 9/10 (a miss is a logged skill-debt issue; < 7/10 blocks).
3. **G3 wrong plans**: each has `expect` (failure-label classes) and `mistake_steps`. It executes its mistake (no
   `PlanError`), fails for an expected reason on every seed where it applies, and passes or fails differently when the
   mistake steps are replaced by the reference's.
4. **G4 protect map**: every analysis `protect` and `wrong` item maps to a predicate, a side effect or a wrong plan, or
   to a signed "not applicable" note.
5. **G5 judgement**: conditional and adaptability entries have >= 2 variants that stage opposite branches, with >= 2
   seeds per branch. The constant, cross-variant and superset policies each fail >= 1 seed per branch. The best
   constant policy's pass rate is recorded with the spec.
6. **G6 perception**: the reference passes the strict perceivability check, bound to entity and field. It never acts on
   an unresolved look-alike and never uses a privileged `wait_until`.
7. **G7 independent audit** of 100 % of core specs (section 9), with no critical and no major defect open.
8. **G8 owner read-through** of a random 5 % (25 specs) in a plain-language rendering: instruction, success in words,
   wrong plans. This catches "tests the wrong thing", which no tool can.
9. **G9 honest fidelity label**: the auditor names the deciding mechanic.
10. **G10 budgets**: steps <= max(30, 3 x reference + 10); time <= max(3 x reference time, reference time + 600 s),
    unless the entry is about waiting.
11. **G11 instruction lint**: no raw ids, no role tags, >= 3 phrasings that keep the entry's vagueness.

**Silver** (long tail, if pursued): G1-G6, G9-G11 plus the sampled audit of section 9.4. **Bronze**: reported gaps
and P-approx proxies, listed, never in headline scores.

### 8.4 Why not just run the 52-batch wave and fix later [inferred from ran]

The existing 243 specs were written by the domain owners who built the mechanics, with red teams and fix rounds, and
198 of them still carry a known issue (8.6). A process with less context per entry (median 9 domains per batch)
cannot be expected to do better, and fixing 2,477 specs after the fact costs more than writing 258 well.

### 8.5 How the core is audited

Section 9, census plan: every core spec gets the automatic suite, an independent auditor and planted-defect
calibration. The owner reads 25.

### 8.6 How much re-hardening the existing 243 need [ran; mixes confirmed and static upper-bound flags]

`reharden_union.py` takes the union over: unguarded protect items (static, 96), a variant verified on fewer than 2
seeds (56), D3 reference failure (51, this phase), gamed in Phase 2 (43), conditional entry without variants (33),
held-out seed failure (18, P2-cond), content-blind speech (11, this phase), raw ids in instructions (7, P2-c),
crashing wrong plan (1, P1), unscored destination (1, adjudicated this phase).

**198 of 243 specs have at least one; 45 have none of these** (issues per spec: 108 have 1, 64 have 2, 23 have 3, 3
have 4). Every spec also lacks wrong-plan `expect` fields. This is the work list for the re-hardening pilot, and its
outcome is the first real measurement of the authoring process.

## 9. How to sample-audit AI-authored specs (owner question 6)

**Whether AI mass authoring with adversarial review will produce honest specs: not with the current protocol. The
review shows the protocol misses whole classes (relocation, content-blind speech, memorisable constants, untested
conditions) even in owner-authored specs. With an automatic adversary suite, a separate auditor and planted-defect
calibration, the known classes can be held near zero, and sampling measures the unknown remainder.**

### 9.1 Defect taxonomy (exact)

* **Critical**: (C1) a policy that does not do the task passes on >= 1 verification or held-out seed; (C2) success
  contradicts the entry or its section context; (C3) success rests on a flag or keyword the robot sets directly, or on
  a hook-latched flag that goes stale (P2-b F1); (C4) in realistic mode the reference needs information a camera robot
  could not have, or the spec leaks the answer (position, address, phrasing, role tag); (C5) a safety-relevant
  protection named in the analysis is unenforced (the vents, the warning content).
* **Major**: (M1) an analysis `protect` item unmapped; (M2) a conditional or adaptability entry whose condition no
  constant policy is penalised for; (M3) a wrong plan that fails for a reason other than its stated mistake, crashes,
  or never executes its mistake; (M4) a fidelity label above what the deciding mechanic supports; (M5) an answer that
  is constant across seeds within a variant when the entry is a judgement or measurement; (M6) an appendix clause
  without a predicate or variant.
* **Minor**: (m1) budget over the G10 rule; (m2) phrasings that change meaning or vagueness; (m3) notes promising
  checks the spec does not make; (m4) tidiness gaps (the robot ends holding a carton; a tool left on another room's
  floor).

### 9.2 Who audits

* The automatic suite (section 10) runs on 100 % and blocks.
* An **independent auditor agent** gets the spec, the entry and its analysis record, the domain page, and a mandatory
  checklist (one line per taxonomy item, each with a [ran] / [read] answer: it must run at least one gaming plan per
  C1 class that applies). It does not get the author's or reviewer's transcripts or notes.
* The **orchestrator script** (not an agent) runs `--check`, the v2 suite and the sampling, and records the code
  fingerprint. Self-reported `verified_passing` lists are ignored (P4 A3).
* The **owner** reads 25 core specs (G8) and arbitrates disputes.

### 9.3 Core: census

Every core spec is audited (500, including the re-hardened 243). With 0 critical defects found in 500, the 95 %
upper bound on the critical rate is 0.6 %; with 5 found, 2.1 % [ran: `audit_oc.py`].

### 9.4 Long tail: acceptance sampling per batch [ran for the operating characteristics]

* **Lot**: one domain-grouped batch of about 40-45 specs.
* **Sample**: n = 13 specs, stratified: >= 5 judgement, >= 3 realistic, the rest random. The draw uses
  HMAC(orchestrator nonce, batch file hash), so authors cannot predict it.
* **Accept** if the sample has 0 critical and <= 1 major (planted defects excluded). Otherwise audit 100 % of the batch,
  fix the prompt or template that caused it, and draw a fresh n = 13 from every earlier batch made with the same
  template.

Probability of accepting a 45-spec lot with d defective specs [ran]:

| plan | d=1 (2 %) | d=2 (4 %) | d=3 (7 %) | d=5 (11 %) | d=7 (16 %) | d=9 (20 %) | d=14 (31 %) |
|---|---|---|---|---|---|---|---|
| n=5, c=0 | 0.89 | 0.79 | 0.70 | 0.54 | 0.41 | 0.31 | 0.14 |
| n=8, c=0 | 0.82 | 0.67 | 0.55 | 0.36 | 0.23 | 0.14 | 0.04 |
| **n=13, c=0** | 0.71 | 0.50 | 0.35 | **0.16** | 0.07 | 0.03 | 0.00 |
| n=20, c=1 | 1.00 | 0.81 | 0.58 | 0.25 | 0.09 | 0.03 | 0.00 |

Read it this way: n = 8 would let through more than a third of batches with 11 % defects. n = 13, c = 0 lets through
16 %; that is the per-batch gate. The process-level guarantee comes from pooling.

* **Process control**: track the pooled critical rate with a 95 % one-sided Clopper-Pearson upper bound. Pause the
  wave if, after >= 200 audited specs, the bound exceeds 5 % (that is, more than 5 critical in 200 [ran: x=5 in
  n=200 gives 5.2 %]) or any rolling window of 3 batches holds >= 2 critical.

### 9.5 Planted defects: auditing the auditors (exact)

* Into every audit sample the orchestrator plants **2 mutated specs** without telling the auditor. Each starts from a
  spec that passed the automatic suite and applies one mutation:
  * **PD1** delete `side_effects` and `preserve`;
  * **PD2** collapse variants to variant 0;
  * **PD3** replace a report-content predicate with its `_to` recipient predicate;
  * **PD4** delete the final-location predicate of a moved object;
  * **PD5** multiply both budgets by 20;
  * **PD6** replace a load-bearing success predicate with one that holds at t=0;
  * **PD7** delete the failure predicate guarding an analysis protect item;
  * **PD8** make a wrong plan raise `PlanError` before its mistake.
* The mutations that should also trip the automatic suite (PD1, PD3, PD4, PD6, PD8) double as regression tests of the
  suite.
* **Auditor recall**: to claim recall >= 90 % at 95 % confidence takes 29 of 29 caught (lower bound 0.902; 28 of 29
  gives only 0.847) [ran]. With 2 plants per batch, that evidence exists after 15 batches. Any miss in the first 29
  stops the wave until the checklist is fixed.

### 9.6 Agreement

On the re-hardening pilot, two independent auditors review every spec. Require Cohen's kappa >= 0.6 on the
critical/major/none label; below that, the taxonomy wording is ambiguous and is rewritten before any sampling plan is
trusted.

### 9.7 Process metrics recorded per batch

Tokens per accepted spec (uncached input, cache reads, output), wall time, CPU seconds of verification, number of
`--check` iterations, gaps reported and their legitimacy (owner-signed), defects found by the suite versus by the
auditor versus by plants, and rework rate. The cost estimate for any later wave is extrapolated from these, not
guessed.

## 10. Verification v2: exact definitions of the new checks (owner question 1, extra checks)

Each check runs inside `verify_spec`, blocks on failure unless marked "warn", and names a failure this review
reproduced. "Seeds" means the spec's balanced verification seeds (10.1) unless stated.

### 10.1 Seeds and variants

* **V-VAR (variant selection)**: `variant = int(sha256("variant" | spec_id | seed)) mod n` instead of `seed mod n`.
  Catches: P3-id I1 (the printed address `100 + seed mod 900` gives `seed mod n` for n in {2, 3, 4}; mail sort passed
  10/10 from one envelope).
* **V-SEEDS (balanced verification seeds)**: the smallest seeds, starting from 0, 1, 2, 41, 97 and continuing 3, 4,
  ..., such that every variant gets >= 2 seeds (3-variant specs then need 6 seeds, 4-variant specs 8). Catches: P1 F6
  (the never-selected 4th variant; variant 0 verified on one house in 54 specs), P2-b F8.
* **V-HELD (held-out seeds)**:
  * Public `Hpub = {3, 4, 5, 6, 7, 8, 500, 501, 502, 503}` plus 5 rotating seeds per gate run from sha256(gate id),
    unpublished in advance.
  * On each: idle must fail, initial must fail, every wrong plan and every automatic adversary must fail (blocking).
    The reference must pass on >= 9/10 of Hpub (warn below 9, block below 7). A build error blocks unless the spec
    declares house requirements that exclude that house.
  * Catches: A-d9e7a4d5d112d2-01 idle pass (seed 6), T-c49fadcea69182-01 and A-926bc611ae9158-01 wrong-plan passes,
    the 12 winder-stair reference falls, the `TypeError` in `fetch` (P2-cond F-C2).

### 10.2 Wrong-plan attribution

* **V-EXPECT**: every wrong plan carries `expect` (failure-label classes: `goal`, `side_effect`, `global:<flag>`,
  `failure:<n>`, `temporal`, `question`, `budget`) and `mistake_steps` (indices). On every seed: the `why` set must
  intersect `expect`; a `PlanError` or exception before the last mistake step means "not run" and fails the spec; the
  plan with its mistake steps replaced by the reference's corresponding steps must pass on >= 4/5 seeds. Record the full
  sorted label set in the trial record (P2-gate check 2). Catches: P1 F1/F5, P2-cond F-C6, P2-a G8.

### 10.3 Judgement and conditionals

* **V-CONST**: for entries with a non-null `conditional` or kind adaptability, synthesise and run on every seed:
  (a) each variant's reference on the other variants' seeds; (b) "never", the reference with steps tagged
  `"conditional": true` removed; (c) "superset", the union of all variants' conditional steps; (d) "ask-always" and
  "never-ask" where questions are scored. Each must fail on >= 1 seed of every branch where it is wrong. Report the best
  constant policy's pass rate with the spec. Catches: the 10 specs of P2-cond F-C1, P2-b F5/F6, P2-c F6.
* **V-ANSWER (answer variation)**: across 20 seeds, collect the set of entity roles that must change and the expected
  report values; for judgement and measurement entries, warn when they are constant within a variant. Catches:
  T-d8d0ca913007d4-01 (always the fern and the violet), T-70a9e6a7a83f64-01 (always "dishwasher"),
  T-c39a630dc5c6f6-01 (always 76 mm) (P2-c F4), P2-a G3.
* **V-CHANCE (guess baseline)**: analytic pass rate of a uniform guess over each look-alike decision (1/k per pick,
  1/k! per full sort), checked by a synthesised random-assignment plan on 20 seeds; warn above 10 %, block above 50 %.
  Catches: T-5d63abe55f7147-01 (7/10 by handle order; P2-c F5).

### 10.4 Metamorphic tests (prototypes run this phase on all 243 x 5 seeds) [ran]

| Check | Exact definition | Flagged | What the flags were, on reading |
|---|---|---|---|
| **M-BEEP utterance swap** | replace `text` of every speech step (announce, speak, refuse, confirm, handoff, alert, decline_visitor, people.deliver, ...) with "Beep."; must fail on every seed where speech is scored | **11 / 13** applicable | real, content-blind speech scoring: X-bf82170244be22-01 and X-d734ea3e299b16-01 (fire warnings: "alerted_residents" counts any utterance), T-123d987460eb2a-01 (gas explanation), T-dde0975c7a26b8-01 (refusal reason), P-6f82b375df3cc1-01 (handoff), X-9cb1ee87e09363-01 (declining a solicitor; arguable, since the decline itself is an action), plus the 4 P2-c already showed (V-10424aa789320a-01, T-457afb032d7be7-01, T-e9cf6ca709ec77-01, X-bc4e8d373e62c5-01); arguably benign: T-1983509f271ebc-01 (a courtesy announcement nobody scores) [read] |
| **M-LABEL label swap** | replace label text with "XQZ"; must fail | **0 / 4** | null result: label content is checked (the "NOT FRAGILE" substring hole of P2-b F7 needs a second mutant: prefix "NOT ") |
| **M-HOLD keep-holding** (prototype: delete the LAST top-level placement step) | must fail unless `allow_held` | 17 / 43 | of 8 read, **0 hit the task's deliverable**: the last placement is usually an incidental tool return (carton, booster, rag); 3 of 8 show the robot may end the episode holding a carton, a bowl or a ring [read] |
| **M-DEST destination swap** (prototype: LAST placement to another room's floor) | must fail unless in `allow_destinations` | 16 / 40 | mostly the same incidental placements; A-04b40946bea880-01 (clutter dumped outside the staging bin) is real and matches P2-c F2 [read] |

Lesson for the definitions [ran + read]: placement mutants must target the objects that success predicates or the
instruction name, not "the last placement". Final definitions:

* **M-HOLD**: for each object X whose location the reference changes and which the instruction or a success predicate
  names, delete X's final placement (the robot keeps X); must fail unless `allow_held: true`. Add a global end rule:
  hands empty at `done`, unless allowed.
* **M-DEST**: for the same objects, replace the final destination with (a) the floor of a different room and (b) the
  floor of the intended room; both must fail unless listed in `allow_destinations`. Catches A-04b40946bea880-01,
  T-e262f715d0bd60-01, P2-a G2.
* **M-CONSERVE (relocation)**: for each mess kind named in `conserve`, the end amount outside allowed sinks must be
  <= 5 % of the t=0 amount (P2-a check 1).
* **M-DISPLACE**: after the reference, move every success-named entity by a non-`place` verb; success must become
  false (P2-b M1; catches stale `sorted_ok`).
* **M-DAWDLE**: wait max(scripted event times) + 60 s, then run the reference with all perception steps removed; must
  fail on >= 1 seed when a person or pet is protected (P2-b M2).
* **M-EARLY**: run the acting steps before any timed trigger; must fail (P2-b M4).
* **M-SIBLING**: change every settable field of same-type entities in scope; must fail for "don't change" entries
  (P2-b M3).
* **M-NEGATE**: replace each reported value with a different canonical value; must fail (P2-a check 5). Add a
  first-report rule (P2-c F7).
* **M-TRANSPLANT**: replay seed A's reference values or open-loop timings on other seeds of the same variant; must fail
  on more than 1 of 4 (P2-a checks 2-3).

### 10.5 Randomised distractors (exact)

**D-INJECT(k = 3)** runs after setup, from its own RNG stream seeded by sha256("distractor" | spec_id | seed):

* (i) for every entity T named in a success predicate, add one same-type instance T' (an unresolved look-alike if T's
  type is in a look-alike group) on a uniformly random free, reachable surface or cell, in T's room with probability
  0.5, else in an adjacent room;
* (ii) add k clutter items from a curated distractor list on random free surfaces of the rooms the reference visits.

Requirements:

* (a) the reference still passes on >= 4/5 seeds (success is bound to the instance, the task stays solvable);
* (b) the "wrong instance" mutant (the reference with every T replaced by T') fails on every seed;
* (c) the "both instances" mutant (the reference applied to T and T') fails when the entry implies a single instance.

Catches: type-level success predicates; brute force over candidates (A-83f6ca95b72b85-01, both rolls, P2-cond F-C5).
It also dilutes placement-order tells (P2-c F3, P3-id I2) because distractors shift positions.

### 10.6 Perception checks (from Phase 3, restated as gate rules)

R1 twin differential audit on every realistic spec's scenario; R2 seed correlation (no perceivable feature predicts
the variant on > 90 % of 30 seeds); R3 permutation (shuffled same-label positions; a fitted slot policy passes <= 1/n);
R4 grant gate in strict mode; R5 cross-episode handle refusal; R6 echo invariance; L1 `wait(until)` / `watch` on
rendered values. Definitions in `p3_leaks_identity.md` section 3 and `p3_leaks_side.md` section 9.

### 10.7 Static lints (cheap, run first)

Protect map (96 specs flagged today); raw-id instruction regex `\b[a-z]+(?:_[a-z]+)*_\d+\b` (7); role tags in
instructions (2); residual-predicate lint (P2-c check 4); positive success predicate true at t=0 without an explicit
`acknowledge_t0` (P2-b F7); budget rule G10 (184 budgets would be cut); appendix clause map (P2-a G9).

### 10.8 Order of implementation (catch per effort)

1. V-EXPECT, V-HELD, V-VAR / V-SEEDS, M-BEEP plus a speech-content predicate (`speech_ok` with must_include and
   must_not_include plus polarity), static lints.
2. V-CONST, M-HOLD / M-DEST (targeted), M-CONSERVE, M-NEGATE, D-INJECT.
3. M-DISPLACE, M-DAWDLE, M-EARLY, M-SIBLING, M-TRANSPLANT, V-ANSWER, V-CHANCE, and the R1-R6 perception gate.

Compute: about 5x today's verification per spec (5 to 10 seeds, 10-15 held-out seeds, mutants that each cost about a
fifth of a trial) [inferred from ran].

## 11. The first real LLM pilot: design (owner question 7c)

**It should measure whether the scores mean anything (pass validity, discrimination, reliability) and what a run
really costs, not produce a leaderboard number.**

### 11.1 Questions and exact metrics

| # | Question | Metric (exact) | Decision rule |
|---|---|---|---|
| Q1 | Does the harness work with Opus 5.5? | per episode: invalid steps, parse retries, `stop_reason` refusal count, `max_tokens` truncations, API errors, `done` declared vs budget exhausted | any harness error or refusal > 2 % of steps: fix before Q2-Q8 |
| Q2 | What does a step really cost? | mean output tokens per step (thinking included), cache-read share of input, $ per step and per episode, actual vs dry-run | actual > 1.5x estimate: re-estimate and re-ask before the next stage |
| Q3 | Are the tasks solvable and not saturated? | success rate S per model, Wilson 95 % CI; per-spec success matrix; progress over initial PG = (gf_end - gf_t0) / (1 - gf_t0), because raw goal fraction gives a do-nothing agent 0.356 here [ran] | Opus S > 0.8: core must weight harder entries; S < 0.1 with failures dominated by interface errors: fix the agent harness first |
| Q4 | Are passes earned? | **valid-pass rate** = share of successes that a blind auditor (independent agent plus owner spot check, model name hidden) judges as genuinely doing the task; every pilot success is audited; invalid passes classified by the review's gaming classes | valid-pass < 0.9: scores are not trustworthy; the authoring freeze stands |
| Q5 | Do real agents find the leaks? | automatic transcript scan per episode for: `wait`/`watch` with `until` on fields or ops not in the rendered observation; `count` with a room scope; id-shaped words in `speak`/`ask` text; reading printed addresses; handles not issued this episode; relocation of mess; speech-only "warnings" | any use is a confirmed exploit: fix the primitive before any leaderboard |
| Q6 | Does the benchmark separate a strong and a weak model? | Opus minus Haiku success on the same 48 episodes, McNemar test; per-spec agreement | difference < 10 points with a CI spanning 0: inspect saturation or floor per spec |
| Q7 | How noisy is one episode? | test-retest on 8 episodes with `--no-cache` (identical requests would otherwise replay from the response cache [read `llm.py` docstring]): success agreement, step-count difference | disagreement > 20 %: >= 3 seeds per entry in any score |
| Q8 | Are budgets right? | steps and simulated time at first success vs budget; share of episodes ending on the budget; cost per success | most successes under 2x the reference: adopt the G10 budget rule |
| Q9 | What will D3 do to agents? | Haiku on the same 48 episodes forced `--camera geometric` vs room | large drop: D3 needs agent-facing prompt changes, not only spec triage |
| Q10 | Is the 8-step history a confound? | share of failed episodes that repeat an action already done more than 8 steps earlier (the agent is stateless apart from an 8-step window and a memory note [read `llm.py`]) | high: report it as the harness's limit, try `history=24` on a subset |

### 11.2 The episode set (24 specs x dev seeds 0 and 1 = 48 episodes) [ran: selection data; verification]

Chosen from specs whose reference has <= 25 steps and passes realistic verification. It covers all 16 domains; kinds
T 15 / A 5 / P 2 / V 1 / X 1; 10 originally realistic.

* 13 were shown gameable in Phases 2-3, to see whether a real agent finds the games: T-032657e9583337-01,
  P-386d0f28709b05-01, A-83f6ca95b72b85-01, T-fffb3941b40c87-01, T-d8d0ca913007d4-01, A-afe8d8c6d6d883-01,
  T-1cd8e5b7e25846-01, T-718ad251e38789-01, P-47196e0991cdf4-01, V-10424aa789320a-01, T-b122adaae3586f-01,
  T-c35156368a8a4b-01, T-3fb8a84f20876c-01.
* 5 held up as controls: T-851aa9f03b6e44-01, T-7e27e4a6a3b1e5-01, A-c0f8c53da99d24-01, T-0cd7f8f3019b40-01, and
  T-6d58f62bb5256f-01, where every constant policy failed.
* 6 others: 3 never attacked (A-1895a81a5051bd-01, T-1e3f1a4d26121e-01, T-8b457901ef8a04-01) and 3 with only weak or
  seed-specific findings (X-44e90cc83ba0db-01, A-d9e7a4d5d112d2-01, T-dd81fe507682fc-01).

The ids are also in `S/pilot_ids.txt`. Seeds 0 and 1 select variants 0 and 1, so every two-branch spec shows both branches. The
`--verify-on-the-fly --verify-perception realistic` dry run kept all 24 [ran]. For the geometric arm, exclude
T-fffb3941b40c87-01 and T-b122adaae3586f-01 on seed 1: their references fail there in geometric mode [ran: D3
preview].

### 11.3 Arms and estimated cost [ran: dry runs; the dollar figures are estimates]

| Arm | Model and settings | Episodes | Expected at 400 / 1,500 / 3,000 output tokens per step | Hard caps |
|---|---|---|---|---|
| P0 calibration | Opus 5.5, `effort=medium`, `--max-steps 30`, 4 specs, seed 0 | 4 | $4.25 / - / $10.49 | `--max-cost-usd 10`, `--max-episode-cost-usd 4` |
| A | Opus 5.5, `effort=medium`, `--max-steps 60` | 48 | $89.22 / $147.74 / $227.54 | run $150, episode $6 |
| B | Haiku 5.5, `effort=medium`, `--max-steps 60` | 48 | $2.65 / - / $6.11 | run $15 |
| C | Opus 5.5 retest, 8 specs, seed 0, `--no-cache` | 8 | $15.66 / - / $39.58 | run $40 |
| D | Haiku 5.5, `--camera geometric` | 48 (minus 2) | $2.57 / - / $6.03 | run $10 |
| **Total A-D** | | 152 | **~$110 / - / ~$279** | **$215 in caps** |

The estimator assumes every step budget is used, so successful episodes will cost less. Rough wall time (an estimate):
5-20 s per Opus step with thinking, so arm A takes several hours sequentially.

### 11.4 Settings and why

* Realistic perception (the harness default); each spec's own camera except arm D.
* `--verify-on-the-fly --verify-perception realistic`: committed evidence is stale (0/242 records at the current
  fingerprint, P1).
* Fallbacks **off** (the harness default), so every score belongs to one model; refusals are recorded instead.
* Effort pinned and recorded (Opus 5.5 defaults to `medium`, one level below Opus 5).
* `--workers 1` for Opus (warm 5-minute prompt cache, gentle on rate limits).
* Response cache on, except in the retest arm.
* Dev split only: never the test split, which also needs the split key, which does not exist yet.

### 11.5 Timing

* Run **P0 now** (cheap; it de-risks the harness and calibrates every later estimate).
* Optionally run **arm B now as a "before" baseline** (about $3-6).
* Run arms **A-D after the re-hardening (section 13, stage 5)**, so the pilot measures the benchmark that will
  ship. Re-running arm B then gives a before/after measure of what hardening changed for a real agent.

### 11.6 Exact commands for the owner, to run only after approval

None of these was run. Each needs `ANTHROPIC_API_KEY` in the environment and `--allow-network`. Remove `--dry-run` to
spend money.

```
python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --ids T-7e27e4a6a3b1e5-01 P-386d0f28709b05-01 T-851aa9f03b6e44-01 A-afe8d8c6d6d883-01 --seeds 0 --max-steps 30 --agent-opt effort=medium --verify-on-the-fly --verify-perception realistic --max-cost-usd 10 --max-episode-cost-usd 4 --workers 1 --run-id pilot_P0 --allow-network --dry-run

python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --ids $(cat pilot_ids.txt) --seeds 0 1 --max-steps 60 --agent-opt effort=medium --verify-on-the-fly --verify-perception realistic --max-cost-usd 150 --max-episode-cost-usd 6 --workers 1 --run-id pilot_A --allow-network --dry-run
```

Arms B, C and D change the model, ids and seeds, `--no-cache` or `--camera geometric` as in the table above.

### 11.7 What already exists for free [ran]

* Scripted oracle (privileged): 48/48.
* Random: 0/48.
* Do-nothing mock: 0/48, mean raw goal fraction 0.356.

So the success metric is sound at its two ends on this set, and raw goal fraction is not.

## 12. Findings (ranked)

### P5-1 (Critical). The plan scales authoring before it fixes the verification method that the review showed is gameable

* **What is wrong.** Steps 6-7 author about 2,477 specs with the same protocol (initial fails, idle fails, reference
  passes, author-chosen wrong plans fail on 5 fixed seeds). No step of the plan changes it.
* **Evidence.** [ran by P2] 39 of 44 attacked specs ended FAIL under the project's own `--check` once gaming plans were
  added (11/11, 14/16 and 14/17 in the three gaming lenses). Three of the 39 FAILs came only from lucky blind guesses,
  and the sample was chosen for attackability, so this is not an estimate of the rate. [ran by P2-cond] 10 of 22 sampled
  conditional specs do not test their condition. [ran, this phase] Utterance swap flags 11 of 13 speech specs; 9 are real on
  reading (5 by me, 4 by P2-c). 198 of 243 existing specs carry a known issue (section 8.6).
* **Why it matters.** The defect classes are systematic (shared predicates, fixed placements, seed-derived text), so
  they reproduce with every new spec. The leaderboard would reward talking, relocating and memorising.
* **Smallest fix.** Freeze the wave. Build Verification v2 (section 10, items 1-2 first) and the schema fields it needs
  (`expect`, `mistake_steps`, `conserve`, `allow_destinations`, `allow_held`, `clauses`, protect map). Re-harden the
  243 as the process pilot.

### P5-2 (High). The authoring workflow cannot produce or detect honest specs at scale as written

* **Evidence.** [ran] Batches mix a median of 9 (max 14) primary domains; prescribed reading is a median 452k tokens
  per agent (737k with secondary domains) versus 111k-131k grouped by domain. [read by P4, re-read here] The
  orchestrator never runs `--check` and returns agents' own `verified_passing`; the reviewer edits and is the last
  verifier; gaps are not audited; gaming plans are tuned on the verification seeds; `author_wf.js` points authors to
  KERNEL_API "v2.1 through v2.8".
* **Why it matters.** Self-reported green already failed once (the adoption agents' gate command could not run, P2-gate
  T4).
* **Smallest fix.**
  * Regroup batches by primary domain (`make_batches.py`).
  * The orchestrator runs `--check` and the v2 suite and compares the result with the agent's list.
  * An independent auditor works under the sampling plan with planted defects (section 9).
  * Rewrite the prompt against schema 3 and the post-D3 camera default; pass every report as a file.

### P5-3 (High). No real-agent measurement before the end, and the cost model's key input is unmeasured

* **Evidence.**
  * [read] The plan has no LLM run before final verification.
  * [ran] The estimator uses 400 output tokens per step, while Opus 5.5 always thinks (output-billed); the pilot
    estimate moves from $110 to $279 between 400 and 3,000 tokens.
  * [ran] Full dev on Opus 5.5 at the authored budgets is $3,758, above the brief's $900-3,400.
  * [ran] The brief's `--limit 10` example covers only 2 specs.
* **Why it matters.** Discrimination, valid-pass rate, reliability and real cost are the facts that decide whether the
  long tail is worth writing.
* **Smallest fix.** P0 calibration ($10 cap) now; the pilot of section 11 after re-hardening; pass-validity auditing
  of every pilot success.

### P5-4 (High). The switch-overs are larger than planned and are checked per owner

* **Evidence.**
  * [ran] D3 preview: 51/243 specs fail (149/1,215 trials); 38 are new beyond realistic room mode; 27 because the
    reference approaches what it never looked at, i.e. references are privileged (P1 F8).
  * [ran by P4] D1: `stack` breaks 8 specs in 4 domains not listed for it.
  * [ran by P2] D2 exposures: winder stairs, missing types.
  * [ran, this phase] D2 preview: 15/243 specs fail on one seed each (section 6.1).
* **Why it matters.** Each switch-over re-baselines every spec. Doing them one at a time, each with its own golden
  re-record, multiplies triage and hides unintended changes.
* **Smallest fix.**
  * Run a global preview per law and per switch (all specs, all tests).
  * One re-baseline event that also carries every scene-changing review fix: variant hash, address stream, position
    shuffle, handle nonce.
  * One reviewed golden diff.

### P5-5 (High). The adoption-integration step would bake real defects into the baselines, through a gate that cannot see new failures

* **Evidence.** [ran by P2-gate]
  * 3 of the 12 failing tests are genuine defects:
    * one-hand drop at 7.65-8.0 kg;
    * a wrapped glass that scatters shards;
    * a privacy double count.
  * The gate's `compare` passes a new spec that fails on every seed (E1) and accepts self-written reviews (E2b, E3).
* **Why it matters.** 2,477 new specs would pass through that gate.
* **Smallest fix.**
  * Fix the 3 defects before re-recording.
  * Add the G2-ABS absolute rule.
  * Bind review codes to the package's own files and require a second signature.
  * Record wrong-plan failure labels in trial records.

### P5-6 (Medium). `--verify-on-the-fly` ignores `--camera`

* **Evidence.** [read `eval/run.py:276-282`] `verify_on_the_fly` calls `verify_many(..., perception=perception)` with
  no camera, while `verify_many` accepts one [read `tasks/verify.py:385`]. [ran] 51 specs fail when verified in
  realistic plus geometric.
* **Why it matters.** A `--camera geometric` run (and every run after D3 flips the default) would keep episodes whose
  reference fails. Unsolvable episodes depress every agent equally and add noise.
* **Smallest fix.** Pass `camera=filters.get("camera")` through to `verify_many`, and record it in run.json.

### P5-7 (Medium). Step budgets inflate evaluation cost and allow trial and error

* **Evidence.** [ran] Budget / reference steps median 5.0. The rule `max(30, 3*ref+10)` would cut 184 of 243 budgets
  and full-dev Opus cost from $3,758 to about $2,777. P1 measured time budgets at a median 22x the reference time.
* **Why it matters.** Failing agents spend the whole budget, so budgets set the bill. Generous budgets also let brute
  force pass (P2-cond F-C7).
* **Smallest fix.** Gold rule G10, plus a time-efficiency score where speed is part of the task.

### P5-8 (Medium). Verification and gate compute grow linearly with the catalog and the planned gate cannot keep up

* **Evidence.** [ran] 3.25-3.7 CPU-s per trial on this machine. [inferred] G2+G3 over 2,720 specs is about 26 CPU-h
  (3.8 h on 7 workers), about 19 h with the v2 checks.
* **Why it matters.** Every kernel change stales the fingerprint. Per-package full gates during a wave would serialise
  everything.
* **Smallest fix.** A spec-to-domain dependency index (which domains' actions and helpers a spec uses), incremental
  gating of affected specs per package, and a nightly full run.

### P5-9 (Medium). The catalog's analysis cannot predict feasibility, so the gap loop has no end

* **Evidence.**
  * [ran] 10,412 free-form mechanics tags (8,815 used once).
  * [ran] Analysis object names match registered types for 39.5 % on average.
  * [read `gapfix_wf.js`] Gap fixes are re-checked by the owner on specs "you could affect", the owner may edit the
    domain's example specs, and "re-author until no gaps remain" has no stopping rule.
* **Smallest fix.**
  * Feasibility triage of each entry by its domain owner before it is authored.
  * Map tags onto a controlled mechanics vocabulary with owners.
  * Owner-signed "won't do" (Bronze).
  * Gap-fix verification by the global preview.

### P5-10 (Medium). Duplicates are authored as separate entries

* **Evidence.** [ran] 128 duplicate groups, 278 entries (150 redundant); two authored specs already duplicate each
  other.
* **Smallest fix.** One spec per duplicate group, with section-context variants; dedupe `make_batches.py`.

### P5-11 (Low-Medium). Raw goal fraction is not a usable partial-credit score

* **Evidence.** [ran] A do-nothing agent averages 0.356 on the pilot set; P1 measured 0.303 over all specs.
* **Smallest fix.** Report success, and progress over the initial state (PG in 11.1), never raw goal fraction.

### P5-12 (Low). Coarse metamorphic placement checks mostly flag incidental steps (null result that shapes the design)

* **Evidence.** [ran] Keep-holding and destination-swap at "last placement" granularity flag 17 and 16 specs; of 8
  adjudicated keep-holding flags, none hit the task's deliverable [read].
* **Why it matters.** A check with low precision trains authors to ignore it.
* **Smallest fix.** Target success-named objects (definitions in 10.4) and add a "hands empty at done" end rule.

### P5-13 (Low). The schedule has already slipped and the token estimate cannot be audited

* **Evidence.** [read] The brief's foundations date (Oct 21-22) has passed with D1-D3, the integration and 10 adoptions
  still open. [read] There are no token logs in the tree.
* **Smallest fix.** Record token usage and CPU per package and per accepted spec, starting with the re-hardening pilot
  (9.7).

## 13. Recommended sequence

Each stage has an exit gate. Paid steps need the owner's explicit approval with the cost estimate shown.

1. **Decide now (owner, no cost).**
   * Freeze the 52-batch wave.
   * Approve or decline P0 (section 11.6, $10 cap). P0 can run at any time from now on; it touches nothing else.
2. **Stabilise the checkpoint.**
   * Fix the 3 genuine defects and the `glass_shard` doc.
   * `golden.py dump/diff` 0e77b67 vs bc0417b to attribute the generation-1 change.
   * Fix the adoption workflow: gate command, reports as files, handoffs required.
   * Harden the gate: G2-ABS, review binding, failure labels, SKIP = FAIL, G4/G6 in `compare`, action coverage.
   * Finish the 10 adoptions under the fixed process; run the integration gate.
   * *Exit:* all tests green except a dated allowlist; 243/243 own mode.
3. **Verification v2 and schema freeze.**
   * Section 10, items 1-2.
   * The scene-changing leak fixes: variant hash, address stream, position shuffles, per-episode handle nonce,
     `count` grant gate, `wait(until)` on rendered values.
   * Speech-content predicates; the `verify_on_the_fly` camera fix; the budget rule.
   * *Exit:* the suite catches every planted defect PD1, PD3, PD4, PD6, PD8, and every gaming policy Phase 2 wrote,
     when run on copies of the specs.
4. **One re-baseline event for the switch-overs.**
   * D1 per-law global preview; fix `stack` and the ironing board.
   * D2 with careful winder descent and missing types made fatal.
   * D3 triage of the 51 specs, which also removes privileged references.
   * One reviewed golden re-record.
   * *Exit:* 243/243 in realistic plus geometric, or pinned with written reasons.
5. **Re-harden the 243 under v2 (the authoring-process pilot).**
   * Domain-grouped agents, orchestrator-run checks, two independent auditors on every spec, planted defects.
   * Measure 9.7.
   * *Exit:* every spec Gold or dropped; auditor kappa >= 0.6; auditor recall evidence started.
6. **LLM pilot** (section 11, arms A-D, about $110-279 estimated, $215 in caps; owner approval).
   * *Exit:* valid-pass rate >= 0.9, no exploit primitive in use, test-retest disagreement known, cost model refit.
7. **Core authoring: the 258 new core entries.**
   * Feasibility triage first; domain-grouped mini-batches of about 20; Gold bar; 100 % audit; owner reads 25.
8. **Release core-500 v1** with Haiku on all seeds (about $46 per seed) and Opus on one seed (about $1,150-1,550;
   owner approval) as baselines.
9. **Long-tail decision.**
   * Continue with Silver only if: pooled critical-defect upper bound <= 5 %, auditor recall >= 90 % (29/29), tokens
     per accepted spec within the budget the owner sets, and the pilot showed the benchmark discriminates.
   * Otherwise stop at the core and invest in depth: more seeds, variations as perturbations, harder judgement
     entries.
10. **Final verification and docs** for whatever is released, with the incremental gate plus a nightly full run.

## 14. What I could not verify

* Anything a real LLM run would show: output tokens per step with Opus 5.5 thinking, refusal rates, latency, actual
  cost, which exploits a model finds. Only dry runs were made.
* The live price page: price checks rest on the claude-api skill's cached table (2026-10-06).
* The authoring token estimate: no token logs exist in the tree, and I do not know how the owner's allowance meters
  cached context.
* Calendar estimates: inferred from the brief's history, low confidence.
* Precision of every metamorphic flag: I read 8 of 17 keep-holding, 7 of 11 utterance-swap and 4 of 16
  destination-swap flags.
* Whether each of the 258 new core entries is feasible with today's mechanics: the analysis records cannot tell, and I
  did not triage them.
* The hidden-test-split flow (forbidden), Python 3.9 behaviour (not installed), and other operating systems.
* The pilot's harness confounds beyond what code reading shows (8-step history window, memory note).

## 15. What is strong (keep)

* The dry-run estimator, cost caps and network refusal work entirely offline. They priced 1,215 episodes in 60 s with
  no request sent [ran]. `prices.json` is current [read].
* The LLM harness already respects Opus 5.5's API constraints: no forced `tool_choice` for models that reject it, no
  thinking parameter, no temperature unless asked, a cached system block, fallbacks opt-in [read `llm.py`].
* The offline baselines run in about a minute and bracket the score correctly: oracle 48/48, random and mock 0/48 [ran].
* `verify_many(perception=, camera=, gen=)` and the lawbook make whole-catalog switch-over previews a short script: the
  D3 preview took 22 minutes [ran].
* The analysis records cover all 2,720 entries with kind, section, risk, conditional and protect fields. That made a
  deterministic, auditable core selection possible: 105/105 sections, 36/36 risk-3, versus 91 and 6.6 for a random 500
  [ran].
* Label content is genuinely checked: 0 of 4 label specs pass with garbage text [ran].
* All 24 pilot specs verify in realistic mode on the dev seeds [ran].

## 16. Hard-rule compliance

No paid API call and no network use: dry runs only, with `HOUSE_AI_FORBID_NETWORK=1` and no API keys in the
environment. Nothing under the real `%USERPROFILE%\.house_ai\` was read, created, listed or copied: `Path.home()` was
redirected to an empty scratch folder, which stayed empty. No split-key command was run. The live repository was not
touched. Nothing was written inside the frozen tree: baseline runs used `--out` in scratch, and a `find -newer` check
found no new files. Worker counts 2-3.
