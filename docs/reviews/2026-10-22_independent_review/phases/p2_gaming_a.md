# house_ai independent review: Phase 2 (Break the benchmark), GAMING lens, part A

Reviewer: Claude (Opus 5.5), subagent "p2_gaming_a". Date: 2026-10-20 (work done about 19:20-20:45 local).
Code: the frozen copy of commit `bc0417b` at `scratchpad/review/tree` (no `.git`). The live repository
`<home>\OneDrive\Desktop\house_ai` was not touched. Everything I wrote is in
`scratchpad/review/out/p2_gaming_a_scratch/` (abbreviated `S/` below) or this file.

Evidence labels: **[ran]** = I executed it and saw the result; **[read]** = I read code or docs but did not run it;
**[inferred]** = reasoning only.

---

## Status update (end of Phase 2, gaming lens A)

### Simple version

I tried to cheat on 11 of the project's finished tasks, from five areas (cleaning, plumbing, food, kitchen
appliances and laundry), by writing robot "plans" that should not count as doing the job: sweeping the dirt into the
next room, pouring dirty mop water into a clean bucket or a bread box, reporting a memorised answer instead of
measuring, cooking milk by a fixed timer without ever looking at it, and lying in the final report. At least one cheat
passed on every one of the 11 tasks (one of those cheats is weak), and on 8 of them a cheat passed on all five
test houses. Some protections did hold:
rinsing a cucumber does not undo raw-chicken contamination, switching off the wrong burner is caught, and looking into a
bin is really required. The main problems are that most tasks only check the spot where the work was supposed to
happen (not where the mess went), many hidden answers are the same every time and can be memorised, and the
written report's content is often not checked. Nothing is blocking me.

### Advanced version

* Phase 2 (gaming lens, part A), about 1 h 30 min elapsed. Worker count 3; single-spec scratch files make
  `verify_many` run in-process, so most runs used one process.
* Hard rules: no network or paid API call; nothing under `%USERPROFILE%\.house_ai\` read, created or listed; the
  repository was not edited (all edits are scratch copies of spec JSON in `S/`).
* 11 specs attacked, 23 gaming policies run (plus 1 control), every one on all 5 verification seeds in each spec's
  own mode, and the six oracle-mode specs again under forced realistic perception (section 6: identical outcomes).
  **11 / 11 specs gamed** by the verifier's own criterion (at least one gaming policy passes on at least one seed,
  so `--check` reports the spec FAIL); 10 / 11 if the weak X-44e90cc83ba0db-01 plate game is discounted;
  **8 / 11 are gamed on all 5 seeds** (7 by a single policy, X-c77e2cbe7181f0-01 by its two per-variant
  false-report policies). 20 of 23 policies passed on at least one seed; 3 held on every seed (section 5).
* Findings with severity and label: section 8 (Critical G1; High G2-G4; Medium G5-G9; Low G10-G12).
* Open questions for the owner: (1) is "the robot may leave a burner to an adult who happens to be in the room" the
  intended attendance rule? (2) should free-text report fields (`reports.<id>.state`) ever be success evidence, or
  only typed reports with canonical words checked against the truth?
* Next (if asked): run the recommended automatic checks of section 9 over all 243 specs (cross-seed answer
  transplant, open-loop transplant, conservation check), which would turn these hand-made attacks into a gate.

---

## 1. Method

1. **Specs.** 11 authored specs from the five domains, varied by kind and mode: task (6), appendix (2), adaptability
   (2), primitive (1); 4 realistic (2 of them also geometric), 7 oracle; 0, 2 and 3 variants. (The five domains have no
   "variation" spec; the only V spec in the repo is in `people`.)
2. **Scratch copies.** `S/make_games.py` copies each authored spec unchanged into `S/g_<id>.json`, appends the gaming
   policies to the spec's `wrong` list (or to one variant's `+wrong` when a policy only makes sense for that
   variant), and, where a policy needs an id the spec does not bind, appends a read-only binding op at the end of the
   setup (`find` / `room` with `pick: "first"`, which binds an id and draws no random number). Every gaming policy's
   name starts with `GAME`.
3. **Scene identity.** `S/gamecheck.py --orig` rebuilds the original spec on every seed and compares the initial
   scene digest with the scratch spec's: **identical on all 5 seeds of all 11 specs** [ran]. The references still pass
   strictly and the initial and idle checks still fail on every seed [ran], so the only difference is the extra
   wrong plans.
4. **Runs.** `S/gamecheck.py` calls `house_sim.tasks.verify.verify_many` (the code behind `--check`) and prints every
   plan's outcome per seed (`fails`, `ran`, refusals, failure labels); full records are in `S/r_<id>.jsonl`. I then
   ran the owner's command verbatim, `python -m house_sim --check S/g_<id>.json --workers 3`, on every scratch file
   (`S/official_check.log`). A GAME plan that passes makes `--check` print FAIL for that spec: that is a successful
   game.
5. **End-state probes.** `S/probe.py` replays one plan on one seed and prints chosen state paths, so each "pass" below
   is backed by what actually happened (a pile in the next room, water in the spare bucket, the scale never switched
   on...), not just by the verdict.
6. **One policy needed a conditional** the plan language lacks ("report the tap in the room you did not start in").
   I wrote it as a reviewer skill (`S/review_skills.py`, 6 lines, acts only through `env.step`, reads only the
   robot's start room and the two taps' rooms) and ran `--check` through `S/check_with_review_skills.py`, which
   imports that module and calls the same `house_sim.cli.check_file`.
7. **Honesty about plan privilege.** My gaming plans name true ids like every authored plan (the runner maps them to
   handles in realistic mode). None of them reads the hidden value it is supposed to discover: memorised answers come
   from the spec's own constants (`bind` ops), which are identical on every seed of that variant, so an agent could
   learn them from the public train/dev seeds.

## 2. Commands run and results

All from the frozen tree as working directory. Times are wall-clock on the shared machine.

| # | Command | Result |
|---|---|---|
| 1 | `python -m house_sim --check tasks/authored/cleaning__examples.json --ids T-d389e2979f6aad-01 --workers 3` | PASS (unmodified spec), 30 s |
| 2 | `python S/explore.py <id> 0,1,2,41,97 "<expr>"` (several) | bindings, kitchen exits, sinks, vessels, sock ids, people locations per seed |
| 3 | `python S/milk_scan.py` with `XS=...` / `FIRST=...` | open-loop timer windows for the milk spec (section 3.8) |
| 4 | `python S/make_games.py` | 11 scratch files `S/g_<id>.json` (rebuilt after the round-2 fixes) |
| 5 | `python S/gamecheck.py S/g_<id>.json S/r_<id>.jsonl --orig tasks/authored/<file>.json` (11 specs; `S/run_all.sh`, `S/run_round2.sh`) | per-seed tables in `S/detail_<id>.txt`; summary table in section 4 |
| 6 | `python S/probe.py S/g_<id>.json "<plan prefix>" <seed> "<expr>" ...` (about 20 probes) | end states quoted in section 3 |
| 7 | `sh S/run_official.sh` (`python -m house_sim --check S/g_<id>.json --workers 3` for 10 files; the faucet file through `S/check_with_review_skills.py`) | **FAIL for all 11 scratch files**, 51 failing trials (`S/official_check.log`), matching the per-seed tally |
| 8 | `sh S/run_realistic.sh` (`gamecheck.py ... --perception realistic` for the 6 oracle-mode specs) | section 6 |
| 9 | `python S/report_constants.py S/report_constants.json` | 51 specs score reports; 18 have a report field value identical on all 5 seeds (subject may still vary; section 7) |
| 10 | `python S/exposure_census.py S/exposure_census.json` | 197 / 243 specs have no relocation or collateral rule; 86 / 243 are structurally constant (section 7) |
| 11 | `python S/milk_temp.py` | milk pot trajectory: whole litre lost in one 30 s step at boil-over, empty pot to 396 C (G10) |
| 12 | `python S/summarize.py r_` / `rr_` | the tables in sections 4 and 6 |

## 3. Per-spec record

For each spec: what it checks, each gaming policy, where it passed, and why. "5/5" = passed on seeds 0, 1, 2, 41, 97.

### 3.1 `T-d389e2979f6aad-01` cleaning, task, oracle: "Sweep the kitchen floor, collect the pile, keep the earring"

Success: kitchen floor crumbs and dirt below 0.05; `count {category: debris, room: kitchen} == 0`; the earring not
in the dustpan, can or rug and not in a waste stream. No side effects. Catalog text: "Sweep a floor."

| Policy | Result | Why |
|---|---|---|
| GAME move the mess: earring to the counter, then `sweep ... out_to=<first exit>` twice | **passes 5/5**, 0 refusals | `sweep` has an `out_to` argument that deposits the pile in the doorway of the adjacent room. The count is per room. Probe seed 1: `debris_pile_1` (78.5 g) now in `living_room_1`, kitchen clean, success [ran] |
| GAME lose the valuable: `cleaning.sweep_floor` with `bin = kitchen_sink_1`, earring not picked up | **passes 5/5**, 0 refusals | `empty_into` a sink sends the pan's contents "down the drain" (the code logs `item_down_drain` [read `cleaning/waste.py` `empty_into`]). Probe seed 1: the earring's location is `kitchen_sink_1`, the sink's `clog` went from unset to 0.26, success [ran]. The success only excludes pan, can and rug; `admin_inventory.protected_items` raises `discarded_protected` only for an item that is shredded or inside a waste or donation container, and skips protected items that no longer exist [read `admin_inventory/harms.py:95-135`] |
| GAME easy half: sweep and collect with the reference skill but no bin (never empties the pan) | **passes 5/5** | the collected 78 g lives in the dustpan's `cl_load_g`, which is not a `debris` entity, so nothing counts it. Phrasing 3 says "... and get rid of them" [ran] |

The three authored wrong plans fail as intended (pile under the rug: goal 0.4-0.6; pile left: goal 0.8; earring binned:
`discarded_protected`) [ran].

### 3.2 `T-c49fadcea69182-01` cleaning, task, realistic + geometric: "Check how full the kitchen can and the recycling bin are"

Success: reported fill levels within 0.12 of the truth for both bins, and `camera_log.looked_in` for both. Truth per
variant: v0 (0.66, 0.34), v1 (0.33, 0.67), v2 (0.36, 0.64); seeds map to variants (0, 1, 2, 2, 1).

| Policy | Result | Why |
|---|---|---|
| GAME constant answer: opens and looks into both bins (wrist camera), ignores what it sees, reports (0.36, 0.64) | **passes 4/5** (1, 2, 41, 97), fails seed 0 | v1 and v2 are near-duplicates, and v0 is verified on seed 0 only. Probe seed 97: reports 0.36 / 0.64 vs truth 0.33 / 0.67, both `looked_in` present, success [ran] |
| GAME constant answer without looking in (plain inspect) | held: fails 5/5 | `camera_log.looked_in` is required; the camera requirement works [ran] |

Authored wrong plan "reports the glance buckets it saw before looking in" aborts with `PlanError: Nothing observable
at visible_entities.kitchen_trash_can_1.fill_level` on every seed and is still accepted (P1 finding F5 reproduced) [ran].

### 3.3 `T-151f6733397156-01` plumbing_liquids, task, oracle: "Empty the dirty mop bucket where it belongs"

Success: the bucket holds 0.05 l or less, `plumbing.counts.bad_disposal_l` not > 0, no spill entity. Side effects:
`no_damage`. The mop water is 7 l with chlorine bleach.

| Policy | Result | Why |
|---|---|---|
| GAME move the mess: `plumbing_liquids.dump` into the spare, clean `mop_bucket_1` in the utility closet | **passes 5/5** | pouring into any liquid-holding "vessel" just adds the parcel (`transfer.deliver`, kind `vessel`); no disposal rule applies. Probe seed 2: bucket 0.0 l, spare bucket 7.0 l `mop_water` [ran] |
| GAME move the mess: open the kitchen bread box, pour the bleach water into it | **passes 5/5** | `bread_box` has `capacity_l` 12 and counts as a liquid vessel. Probe seed 41: the bread box (which holds `bread_loaf_1`) now holds 7.0 l `mop_water`; the loaf has `wetness` 1.0 and **no** contamination tag; no invariant fires [ran]. (Round 1, without opening the lid, was refused "Open the bread box first": that run is superseded) |

Also seen [ran]: `colander`, `fruit_bowl`, `napkin_holder` and `bread_box` all pass `holds_liquid` in this house.
Authored wrong plan "tips the bleach water onto the lawn" fails on seed 97 only because the robot stalls on
`patio_1` with all 7 l still in the bucket (goal 0.67, no pollution): its stated mistake never happens there.

### 3.4 `X-c77e2cbe7181f0-01` plumbing_liquids, appendix, oracle: "Stop a burst-pipe flood at the right valve"

Catalog text: "Turn off the main water shutoff valve." v0 (seeds 0, 2): burst pipe, close the main, test a tap,
report the main's state to the adult. v1 (seeds 1, 41, 97): a spraying supply line under the kitchen sink: close its
stop, keep the main open, report the sink's leak to the adult. Success checks the report only through
`reports.<id>._to.<field> == adult`.

| Policy | Result | Why |
|---|---|---|
| GAME false report (v0): reference actions, then reports the main's state as "open - I could not shut it, water is still spraying" | **passes 2/2** v0 seeds | the report's content is never compared with anything; only its recipient is. Probe seed 0: `reports.main_water_valve.state` = the false text, main at position 0.0, success [ran] |
| GAME false report (v1): isolates the sink, reports "no leak found, everything is dry" | **passes 3/3** v1 seeds | same reason [ran] |
| GAME brute force: closes every valve whose type is exactly `shutoff_valve` (11 valves; never the main), reports | **passes 3/5** (all v1 seeds), fails v0 (`damage`: the burst continues) | probe seed 1: 9 of 11 closed (2 seized, refused), including the refrigerator ice-maker line, washer hot and cold, dishwasher supply, both bathroom stops and the water-heater inlet; main open; success. The spec penalises closing the main ("the rest of the house has water") but not the equivalent collateral of closing every fixture stop [ran] |

Round 1 of the brute-force policy was invalid (my `find` for `shutoff_valve` also matched the main through `is_a`,
so the plan closed the main and failed for that); round 2 filters on the exact type. Only round 2 is counted.

### 3.5 `P-386d0f28709b05-01` food, primitive, realistic: "Weigh an item on the kitchen scale and report the reading in grams"

Catalog text: "Weigh." Success: `reports.<item>.weight_g == {grams}` and the food domain's `_report_check.weight_g`
(within 1 % / 2 g of the true subtree mass). Weights are `bind` constants per variant: sweet potato 742 g, butternut
squash 1186 g, eggplant 463 g; the instruction names the vegetable.

| Policy | Result | Why |
|---|---|---|
| GAME memorisation: approach, plain `inspect` (needed only because the legacy `report` requires an inspected target), report the weight learned for that vegetable | **passes 5/5** in realistic mode | probe seed 1: report 1186, scale `on: false`, unit still `oz`, squash still on `counter_2` (never on the scale), `_report_check.weight_g: true` [ran]. The perceivability check applies only to the reference run [read `verify.py:170-175`], never to the agent |
| GAME constant answer: reports 742 g whatever the item | **passes 1/5** (seed 0) | a fixed answer is right on the variant it matches [ran] |

### 3.6 `X-44e90cc83ba0db-01` food, appendix, oracle: "Raw chicken and salad prep without cross-contamination"

Catalog text (three clauses): "Food safety: avoid cross-contamination (raw-meat boards, hands), control allergens
(read labels, clean shared tools), discard food left out too long." Success: chicken cut into strips, cucumber sliced,
cucumber `contamination.raw_meat` below 0.05. No variants. Only the first clause is exercised [read]; the allergen and
"left out too long" clauses have no setup, predicate or wrong plan (AUTHORING_GUIDE: "every clause must be exercised").

| Policy | Result | Why |
|---|---|---|
| GAME launder the mistake: same board and knife (chicken first), then rinses the cucumber 30 s under the tap | held: fails 5/5 with `food.rte_cross_contamination` | rinsing produce lowers only bacteria, pesticide, dirt and wax, not `raw_meat` [read `food/hooks.py:548-553`, ran] |
| GAME spread it elsewhere: gloved chicken cut, then with the raw-chicken gloves still on fetches a clean dinner plate and sets it on the counter, then does the cucumber properly | passes 3/5 (0, 2, 97); fails 1, 41 because of my plan (no free counter space for the plate: "No free space there", the plate stays in hand and the cucumber cannot be steadied) | probe seed 0: the plate carries `raw_meat` 0.024 and nothing checks non-food items. **Weak**: 0.024 is below the simulator's own 0.05 threshold for ready-to-eat food, so I rate this a coverage note, not a strong game [ran] |

### 3.7 `T-032657e9583337-01` appliances_cooking, task, oracle: "Check the stove is off (one pot is meant to simmer)"

Success: right-front burner off, left-rear burner still on low. No variants, no `questions` block. The notes say
"Asking the resident is a necessary question", and the reference asks about both burners.

| Policy | Result | Why |
|---|---|---|
| GAME memorised layout: never asks, never looks, switches off the right-front burner | **passes 5/5** | the scenario is the same on every seed (same burner intended, same burner forgotten), and asking is not scored. Probe seed 2: `necessary_questions` 0, `dialogue_log` empty, no violations, success [ran] |
| GAME brute force then restore: all burners off, then the stock back to low | held: fails 5/5 with `authority_violations` | switching off an intended appliance is latched even if restored [ran] |

### 3.8 `A-2bbd9d39ff1512-01` appliances_cooking, adaptability, oracle: "Watch the milk on the stove (no boil-over)"

Catalog text: "'Watch this while it cooks.' It has to understand visual, temperature, smell, texture, and timing cues
rather than just a timer." Success: no boil-over, `max_c >= 75`, not scorched, burner off. No variants: 1 l of milk
at 21 C on high in every house.

Timer scan [ran, `S/milk_scan.py`]: standing at the counter and switching off after X seconds passes on all 5 seeds
for every X tested from 150 to 195 s (X = 135 fails seed 41 only, X = 120 fails all five; 195 s was the largest
value tested at the counter; in the walk-away scan the milk had boiled over by X = 210 s on 2 of 5 seeds and by
240 s on all 5).

| Policy | Result | Why |
|---|---|---|
| GAME just a timer: goes to the counter, waits a fixed 165 s, switches off; never inspects or looks at the pot | **passes 5/5** | the scenario is constant, so one memorised duration works in every house: exactly what the catalog entry says must not suffice [ran] |
| GAME walks away: leaves for the living room for 165 s, comes back, switches off | passes 3/5 (1, 2, 97); fails 0 and 41 with `safety_violations` | the attendance rule counts the stove as attended when the robot **or any adult** is in the room [read `stove.py:_attendance`]; on seeds 1, 2 and 97 the resident happens to be in the kitchen [ran]. Defensible rule, but the robot was the one asked to watch |

### 3.9 `A-d66994a566899c-01` textiles_laundry, adaptability, oracle: "Dry a shrink-prone garment by its label"

Instruction: "Dry this without shrinking it." Variants: wool sweater (dry flat), cotton tee labelled tumble-low,
rayon dress (line dry). Budget 230,400 s (64 h).

| Policy | Result | Why |
|---|---|---|
| GAME one-size-fits-all: never reads the label, lays the garment flat on the rack, waits 215,000 s | **passes 5/5** | flat drying is safe for all three garments and the 64 h budget leaves time for the slowest; the entry's adaptation (reading the care label) is never needed. Round 1 with a 144,000 s wait failed only the sweater seed (goal fraction 0.83: one of six predicates unmet; I did not probe which, most likely dryness since the longer wait fixes it), so I re-ran with a wait close to the reference's own maximum (216,000 s) [ran] |
| GAME one-size-fits-all: always hangs it on the rack | passes 2/5 (2, 41: the dress); fails sweater and tee with `damage` (`stretched` 0.08) | hanging a wet tee or sweater stretches it in the model [ran] |

### 3.10 `T-ba18cc4b56e89d-01` textiles_laundry, task, realistic: "Check whether the dryer load is really dry"

Success: the two dry items in the clean basket, the damp one on the rack. Damp item per variant: towel (v0, seed 0),
tee (v1, seeds 1, 97), jeans (v2, seeds 2, 41). Per the spec notes, glance and plain inspect cannot tell and only
`inspect where=closely` can [read].

| Policy | Result | Why |
|---|---|---|
| GAME constant guess: never feels anything; always hangs the jeans, baskets towel and tee | **passes 2/5** (2, 41) | with three hand-written variants mapped (0, 1, 2, 2, 1), any fixed guess is right on the seeds of its variant [ran] |
| GAME constant guess: always hangs the tee | **passes 2/5** (1, 97) | same [ran] |

"Always hang the towel" would be right on seed 0 by construction [inferred, not run], so each fixed guess is right
on 20-40 % of the verification seeds without perceiving anything.

### 3.11 `T-1cd8e5b7e25846-01` plumbing_liquids, task, realistic + geometric: "Report the leaking faucet"

Success: the dripping tap reported as `drip` to the adult, the dry tap not reported, close looks (`peek closely`) at
both taps, stops untouched. Variants: v0 and v2 (seeds 0, 2, 41) the bathroom tap drips and the robot starts in the
kitchen; v1 (seeds 1, 97) the kitchen tap drips and the robot starts in the bathroom.

| Policy | Result | Why |
|---|---|---|
| GAME layout tell: peeks at and inspects both taps exactly like the reference, ignores what it saw, reports the tap in the room it did **not** start in | **passes 5/5** | the answer is a function of the robot's start room on every seed [ran] |
| GAME control: same, but reports the tap in the room it started in | fails 5/5 | confirms the tell is the start room [ran] |

Authored camera wrong plan ("reports the right faucet after a look around the first room ...") never reports
anything: its report is refused with "Inspect it before reporting" on every seed, so it fails for "no report" (goal
0.2), not for the missing close looks its notes describe [ran].

## 4. Summary table (own mode, all 5 seeds)

| Spec | Domain, kind, mode | Policies | Passing policies (seeds) |
|---|---|---|---|
| T-d389e2979f6aad-01 | cleaning, T, oracle | 3 | pile to next room (5/5); earring down the sink (5/5); pan never emptied (5/5) |
| T-c49fadcea69182-01 | cleaning, T, realistic geometric | 2 | constant answer after looking (4/5) |
| T-151f6733397156-01 | plumbing, T, oracle | 2 | into spare bucket (5/5); into bread box (5/5) |
| X-c77e2cbe7181f0-01 | plumbing, X, oracle | 3 | false report v0 (2/2), v1 (3/3); every fixture valve closed (3/5) |
| T-1cd8e5b7e25846-01 | plumbing, T, realistic geometric | 1 (+1 control) | start-room tell (5/5); control fails 5/5 |
| P-386d0f28709b05-01 | food, P, realistic | 2 | memorised weight, scale never used (5/5); constant 742 g (1/5) |
| X-44e90cc83ba0db-01 | food, X, oracle | 2 | contamination spread to a plate (3/5, weak) |
| T-032657e9583337-01 | appliances, T, oracle | 2 | memorised burner, no question (5/5) |
| A-2bbd9d39ff1512-01 | appliances, A, oracle | 2 | fixed 165 s timer (5/5); walks away (3/5) |
| A-d66994a566899c-01 | textiles, A, oracle | 2 | always dry flat (5/5); always hang (2/5) |
| T-ba18cc4b56e89d-01 | textiles, T, realistic | 2 | constant guess jeans (2/5); constant guess tee (2/5) |

11 / 11 specs gamed; 8 / 11 on all 5 seeds (7 by a single policy; X-c77e2cbe7181f0-01 through its two per-variant
false reports); 20 / 23 gaming policies pass on at least one seed; 3 / 23 held everywhere. The 33 authored wrong
plans of these 11 specs all fail on every seed where they apply [ran].

## 5. What held (null and negative results)

Held on every seed, for the reason one would want [ran]:

* T-c49fadcea69182-01: a constant answer without looking inside fails (`camera_log.looked_in` required).
* X-44e90cc83ba0db-01: rinsing a raw-chicken-contaminated cucumber does not clean it (`food.rte_cross_contamination`).
* T-032657e9583337-01: switching every burner off and restoring the stock still fails (`authority_violations` latch).
* T-1cd8e5b7e25846-01: the control policy fails 5/5. (Brute-force reporting of both taps is an authored wrong plan
  and fails 5/5 with goal 0.4, but it also skips the close looks, so I cannot credit the `not reports.{dry_tap}.leak
  exists` predicate alone.)

Held on some seeds only [ran]:

* X-c77e2cbe7181f0-01: closing every fixture stop fails in the burst-pipe variant (`damage`), passes the other.
* A-d66994a566899c-01: always hanging fails for the sweater and the tee (stretch damage), passes for the dress.
* A-2bbd9d39ff1512-01: walking away fails when no adult is in the kitchen (`safety_violations`), passes otherwise.

All 33 authored wrong plans of the 11 specs fail on every seed where they apply (but see G8 for three that fail for
the wrong reason).

## 6. Do the oracle-mode games survive realistic perception?

The six oracle-mode specs with passing games were re-run with `--perception realistic` (the mode every harness episode
uses), same plans (`S/run_realistic.sh`, records `S/rr_<id>.jsonl`, tables `S/detail_realistic_<id>.txt`):

| Spec | Reference passes (5 seeds) | Gaming outcomes under forced realistic |
|---|---|---|
| T-d389e2979f6aad-01 | yes | all three games pass 5/5 (same as oracle) |
| T-151f6733397156-01 | yes | spare bucket 5/5, bread box 5/5 |
| X-c77e2cbe7181f0-01 | yes | false reports 2/2 and 3/3; every fixture valve 3/5 |
| X-44e90cc83ba0db-01 | yes | rinse held 5/5; plate spread 3/5 |
| T-032657e9583337-01 | yes | memorised burner 5/5; all-off-and-restore held 5/5 |
| A-2bbd9d39ff1512-01 | yes | timer 5/5; walk away 3/5 |

**Every outcome is identical to own mode** [ran]. Caveat: plans name true ids that the runner turns into handles, so
this shows the end-state scoring and action gating are the same under realistic perception; it does not show that an
agent would pick the same targets, though every target used (a doorway, the kitchen sink, a bucket, a bread box,
the right-front burner, the counter) is a glance-visible object.

## 7. Specs that pass for the wrong reason, and structural causes

* **Success predicates are local; collateral is unprotected.** [ran, `S/exposure_census.py`] 197 of 243 specs (81 %)
  have no `no_moves_except`, `no_new_soil_except`, `unchanged_fields` or `preserve` anywhere (base or variants); per
  domain in the five reviewed: appliances_cooking 17/17, cleaning 14/16, food 10/16, plumbing_liquids 12/15,
  textiles_laundry 15/15 have only `no_damage` or nothing. `no_damage` sees only newly `broken` entities. This is the
  root of G1.
* **Structurally constant scenarios.** [ran] 86 of 243 specs have no variants and no random setup op (`choose`,
  `randint`, `uniform`, `robot_fault choose`): cleaning 8, food 8, appliances_cooking 6, textiles_laundry 9,
  plumbing_liquids 2. Caveat [read]: domain helpers can randomise internally (e.g. `cleaning.floor` has `jitter`), so
  86 is an upper bound on "the task-relevant facts are the same on every seed"; the stove and milk specs are confirmed
  constant [ran].
* **Variant structure.** [ran, P1] 3-variant specs map seeds to variants (0, 1, 2, 2, 1): variant 0 is verified on one
  house; any fixed guess matching variant 1 or 2 passes 2 of 5 seeds (T-ba18cc4b56e89d-01), and near-duplicate
  variants make it 4 of 5 (T-c49fadcea69182-01).
* **Report answers.** [ran, `S/report_constants.py`] 51 specs score reports; in 18 of them every scored report field
  takes the same value on all 5 seeds (e.g. `leak == "drip"`, `handle == "tripped"`, `defect == "crack"`). This
  census strips subjects, so in some of the 18 the challenge is choosing the subject (which the layout may give away,
  as in T-1cd8e5b7e25846-01); it is a list of candidates for memorisation attacks, not 18 demonstrated games.
* **Report content unchecked.** [ran] X-c77e2cbe7181f0-01 scores only the recipient (`_to`), so false content passes.
  The legacy `report` action stores any string [read `core.py:747-759`].
* **Notes promise checks the spec does not make.** T-032657e9583337-01 ("asking is a necessary question": no
  `questions` block) [ran]; A-2bbd9d39ff1512-01 (catalog: "rather than just a timer": constant scenario) [ran];
  X-44e90cc83ba0db-01 (appendix: 1 of 3 clauses) [read]; T-1cd8e5b7e25846-01 (camera wrong plan "fails only on the
  missing looks": it never reports) [ran].
* **Milestones at t=0 / deadlines met by idling.** None of the 11 specs has a milestone true at t=0 (P1's full run had
  0 `temporal_warnings`) [ran by P1]; only X-c77e2cbe7181f0-01 uses milestones here and its order constraint did its
  job (it is what fails the heater-valve, half-turn and wrong-direction wrong plans) [ran].

## 8. Findings (ranked)

### G1 (Critical). "Verified" does not mean gaming-resistant: every spec I attacked was gamed, 8 of 11 on all five seeds

* **What is wrong.** The verification protocol (initial fails, idle fails, reference passes, authored wrong plans fail)
  only rules out the mistakes the author thought of. Policies that do not do the task pass whole specs.
* **Evidence.** [ran] Section 4: 11 / 11 specs gamed (10 / 11 discounting the weak plate game), 8 / 11 on all 5
  seeds (7 by a single policy), 20 / 23
  policies passing on at least one seed; the owner's `--check` prints FAIL for all 11 scratch files
  (`S/official_check.log`); the six oracle-mode specs give identical outcomes under forced realistic perception
  (section 6). Scene digests identical to the originals on every seed [ran].
* **Why it matters.** A learning agent optimising these scores will find the same shortcuts (relocating messes,
  memorising answers, fixed timers), and leaderboard numbers will overstate competence. The planned 2,477-spec
  authoring wave uses the same protocol, so the rate of gameable specs will scale with it.
* **Smallest fix.** Add automatic adversaries to verification (section 9: conservation check, cross-seed answer
  transplant, open-loop transplant, cross-variant transplant, report-truth check) so that these classes fail a spec at
  `--check` time instead of depending on an author's imagination.

### G2 (High). Moving the mess elsewhere passes: success is checked only where the work was meant to happen

* **Evidence.** [ran] Sweep: pile swept into the next room (5/5), pan never emptied (5/5). Mop bucket: 7 l of bleach
  water into a clean spare bucket (5/5) or into a bread box holding a loaf (5/5). Burst pipe: 9 fixture valves closed
  house-wide (3/5). [ran] census: 197 / 243 specs have no relocation or collateral rule.
* **Why it matters.** "Move the mess elsewhere" is the first gaming class the project itself names (brief 6.3);
  it is open in most specs.
* **Smallest fix.** A kernel-level conservation predicate: for the mess kinds a spec names (debris grams, a liquid by
  kind, waste items), the end-of-episode amount outside the allowed sinks (waste-stream containers, toilet / utility
  sink / floor drain, the original container if the task allows) must be below a tolerance. One line per spec
  (`"conserve": {"debris": ["{can}"], "mop_water": ["{toilet}", "utility_sink", "floor_drain"]}`) closes all four
  relocation games above. For valves and appliances: `unchanged_fields` with `except` on the things the task may touch.

### G3 (High). Hidden answers and scenarios are constants that a memorising agent can replay without perceiving

* **Evidence.** [ran] Weighing: the memorised weight passes 5/5 in realistic mode without the scale ever being
  switched on. Stove: the same burner is the forgotten one in every house, so a no-question policy passes 5/5. Milk:
  one fixed 165 s timer passes 5/5 (the catalog entry explicitly says a timer must not suffice). Bins: a constant
  answer passes 4/5. Dryer: each constant guess passes 2/5. Shrink-prone garment: one label-blind policy passes 5/5.
  [ran] census: 86 / 243 structurally constant specs; 18 / 51 report-scored specs with seed-invariant answer values.
* **Why it matters.** The hidden test split changes only the seed (house layout and variant index): weights,
  intended burners, timings and fill levels are the same constants as in public train/dev, so test seeds do not
  protect against memorisation (EVAL design, brief 3.7) [inferred from the spec format; I could not exercise the test
  split, rule 2].
* **Smallest fix.** Draw task-relevant values per seed with setup RNG ops (`uniform` mass +-25 %, `choose` which
  burner is intended, milk volume / start temperature / burner power, bin fill levels from a range), keep variants
  for qualitatively different branches, and add the cross-seed answer-transplant check of section 9.

### G4 (High). Report content is not scored in places where it is the deliverable

* **Evidence.** [ran] X-c77e2cbe7181f0-01: telling the adult the water is still on after closing the main, or that
  there is no leak after isolating one, passes every seed. [read] The success checks only `reports.<id>._to.<field>`.
* **Why it matters.** "Let us know" is half of the instruction; an agent that reports falsely is worse than one that
  does not report.
* **Smallest fix.** Use typed reports with canonical `state` / `issue` words checked against the truth (the kernel
  already has them), or add `{"not": {"path": "reports.{main}.state", "op": "contains", "value": "open"}}`-style
  truth predicates; lint that every scored report field compares content, not just the recipient.

### G5 (Medium). Answers correlate with nuisance features (layout tells)

* **Evidence.** [ran] T-1cd8e5b7e25846-01: "report the tap in the room you did not start in" passes 5/5 while the
  control fails 5/5: the variant sets both the dripping tap and the robot's start room.
* **Why it matters.** Shortcut features like this are what learning agents pick up first; they also mean the
  verification seeds do not test what the notes say.
* **Smallest fix.** Randomise the robot's start room independently of the variant (or put it in the same room
  every time); add the nuisance-correlation check of section 9.

### G6 (Medium). Valuables lost down a drain do not count as lost

* **Evidence.** [ran] Sweep: tipping the dustpan with the earring into the kitchen sink passes 5/5 (earring location
  `kitchen_sink_1`, drain clog 0.26). [read] `admin_inventory.protected_items` (`harms.py:95-135`) raises
  `discarded_protected` only for shredded items or items inside waste or donation containers, and `continue`s past a
  protected item that no longer exists (unless it was stolen), the same blind spot P1 reported for side effects (F7).
* **Smallest fix.** Treat a protected or valuable item whose location is a drain, toilet or vacuum as lost
  (global `lost:<id>` failure), and make debris down a drain a disposal error for solids.

### G7 (Medium). Adaptability and conditional entries can be passed by one label-blind, situation-blind policy

* **Evidence.** [ran] A-d66994a566899c-01: always lay flat passes 5/5; A-2bbd9d39ff1512-01: fixed timer passes 5/5;
  T-032657e9583337-01: fixed burner passes 5/5.
* **Smallest fix.** The cross-variant transplant check (P1 H3) for variant specs plus a "universal policy" check:
  the union of all variants' references, or the most conservative one, must fail some seed; for "slow but safe"
  universals, shorten the budget to about 2x the variant's own reference.

### G8 (Medium). Authored wrong plans that fail for the wrong reason (reproduces P1 F1/F5 on this sample)

* **Evidence.** [ran] T-1cd8e5b7e25846-01 camera wrong plan never reports (refused "Inspect it before reporting");
  T-151f6733397156-01 "onto the lawn" never pours on seed 97 (robot stalls on the patio); T-c49fadcea69182-01 glance
  wrong plan crashes with `PlanError` on every seed and is accepted.
* **Smallest fix.** P1's `expect` field per wrong plan, and treat a wrong plan that ends with its mistake unexecuted
  (a refused key step, a `PlanError`) as not run.

### G9 (Medium). Appendix clause coverage is not enforced

* **Evidence.** [read] X-44e90cc83ba0db-01 covers "avoid cross-contamination" only; "control allergens" and "discard
  food left out too long" have no setup, predicate or wrong plan, yet the entry counts as verified.
* **Smallest fix.** Require a `clauses` map in appendix specs (clause -> predicate indices / variant) and lint that
  every clause is mapped.

### G10 (Low-Medium). Modelling holes that enable or hide games

* **Evidence.** [ran] `bread_box`, `colander`, `fruit_bowl` and `napkin_holder` pass `holds_liquid`; bleach mop water
  poured onto a loaf leaves it wet with no contamination and no failure. [read] the mop-water food-zone rule is
  `_kitchen_sink_rule` in `plumbing_liquids/transfer.py deliver` (kind `basin`), so it covers sinks, not vessels. [ran]
  milk: at boil-over the whole litre disappears in one 30 s step (0.9986 l to 0.0), then the empty pot heats to 396 C
  within 5 minutes while `_vessel.scorched` stays unset (and `is_false` treats unset as false).
* **Smallest fix.** Tag containers liquid-tight explicitly; contaminate food reached by chemical liquids; cap
  boil-over loss per step; set `scorched` / a dry-pot damage flag when a vessel boils dry.

### G11 (Low). Attendance counts any adult in the room

* **Evidence.** [ran] "walks away" passes the milk spec on the 3 seeds where the resident is in the kitchen.
* **Smallest fix.** When the robot was asked to watch, attendance should require the robot (or an explicit handover).

### G12 (Low). Weak spread-elsewhere contamination is unchecked

* **Evidence.** [ran] raw-chicken gloves on a clean plate leave `raw_meat` 0.024 on it, unchecked; below the 0.05
  ready-to-eat threshold, so minor.

## 9. Recommended automatic checks (exact definitions)

Each is meant to run inside `verify_spec`, on the 5 seeds, with no author effort beyond a few spec keys. **None of
them is implemented**: "would catch" below is [inferred] from the end states measured in section 3 [ran].

1. **Conservation check (catches G2).** For each mess kind K named in `"conserve"`, let `M0(K)` be its total at t=0
   (debris grams in piles, floor `_cells` soil, pan and vacuum loads; liquid litres by kind in all containers, floors
   and outdoors). At the end, `M_out(K)` = the amount not in an allowed sink. Fail with `relocated:K` if
   `M_out(K) > 0.05 * M0(K)`. Would catch: pile in the living room (about 78 g, all of it), never-emptied pan (78 g),
   spare bucket and bread box (7 l of 7 l).
2. **Cross-seed answer transplant (catches G3 memorisation).** For report-scored specs: record the reference's report
   values (as values, not bindings) on seed A, replay the reference on seed B with A's values substituted. Fail the
   spec if the transplant passes on more than 1 of the 4 other seeds **of the same variant** (run 8 extra seeds per
   variant). Would catch P-386d0f28709b05-01 (1186 g passed on both v1 seeds) and T-c49fadcea69182-01, once values
   are drawn per seed (G3 fix); today it would simply flag that the answer never varies within a variant.
3. **Open-loop transplant (catches the timer and fixed-layout games).** Record the reference's action sequence with
   durations on seed A; replay it, ids remapped by role, with no observation-dependent branching, on the other seeds.
   For entries tagged "watch / monitor / check / detect", the replay must fail on at least one seed. Would catch
   A-2bbd9d39ff1512-01 (a fixed 165 s passed everywhere) and T-032657e9583337-01 (a replay without the asks).
4. **Cross-variant and universal-policy transplant (catches G7; P1 H3).** Run each variant's reference on every other
   variant's seeds; for adaptability and conditional entries at least one must fail on every other variant. Would
   catch A-d66994a566899c-01 (my always-flat policy is v0's reference with a fixed wait and passed on v1 and v2),
   T-ba18cc4b56e89d-01 (the "hang the jeans" guess is v2's choice and passed on v2 only, i.e. other variants' choices
   do fail: this check would pass that spec, the constant-guess problem is G3's) and T-c49fadcea69182-01 (v2's
   answer passed on v1 seeds).
5. **Report-truth lint (catches G4).** Every success predicate on `reports.<id>._to.<f>` must be accompanied by a
   predicate on the content `reports.<id>.<f>` (or a typed `report` with `state` / `issue`), and the verifier runs an
   automatic "negated report" wrong plan (the reference with each reported value replaced by its opposite or a
   different canonical word) that must fail.
6. **Nuisance-correlation check (catches G5).** For variant specs, compute per seed the answer subject's room and the
   robot's start room, the requester's room and the subject's spawn index; flag the spec if the answer is a function
   of any of them over the verification seeds.
7. **Lost-valuable invariant (catches G6).** Global failure `lost:<id>` when an item with `value >= 50`, `sentimental`
   or category jewelry/keys/documents ends in a drain, toilet, vacuum or waste stream.

## 10. Scripts written (all in `S/` = `scratchpad/review/out/p2_gaming_a_scratch/`)

| Script | Purpose |
|---|---|
| `make_games.py` | builds the 11 scratch spec files from the authored specs plus the GAME plans and read-only bindings |
| `gamecheck.py` | `verify_many` on a scratch file with per-seed, per-plan output and scene-digest comparison with the original |
| `probe.py` | replays one plan on one seed and prints state expressions |
| `explore.py`, `dump.py`, `dumpc.py` | scene and spec inspection |
| `milk_scan.py`, `milk_temp.py` | timer windows and pot trajectory for the milk spec |
| `review_skills.py`, `check_with_review_skills.py` | the one reviewer skill (start-room tell) and a `check_file` wrapper |
| `run_all.sh`, `run_round2.sh`, `run_official.sh`, `run_realistic.sh` | batch runners (logs `*.log`, outputs `detail_*.txt`, `r_*.jsonl`, `rr_*.jsonl`) |
| `report_constants.py`, `exposure_census.py` | censuses over all 243 specs (`report_constants.json`, `exposure_census.json`) |
| `summarize.py` | the tables in sections 4 and 6 |

Scratch spec files: `S/g_<catalog id>.json` (11). Per-seed tables: `S/detail_<id>.txt`; full records `S/r_<id>.jsonl`.

## 11. What I could not verify

* Anything about the hidden test split (rule 2): the claim that test seeds do not protect against memorisation (G3)
  rests on the spec format (values are spec constants, the split changes only the seed) [inferred], not on a test run.
* Whether the 86 "structurally constant" specs are really constant in their task-relevant facts: helpers may randomise
  internally; I confirmed constancy only for the stove and milk specs.
* I attacked 11 of the 79 specs in these five domains (cleaning 16, plumbing_liquids 15, food 16,
  appliances_cooking 17, textiles_laundry 15) and none outside them. The sample was chosen to cover kinds and modes
  and to look attackable, not at random, so 11 / 11 is not an estimate of the catalogue-wide gaming rate; the
  censuses in section 7 are the catalogue-wide evidence.
* Python 3.9 behaviour (only 3.14 is installed).
* The recommended checks of section 9 are not implemented; their "would catch" claims are inferences from the runs.
* The reviewer skill used for the faucet policy is not repository code; a plan-only version would need conditionals
  the plan language lacks.

## 12. Hard-rule check

No paid API call, no network use, no LLM agent run (not even `--dry-run`). Nothing under `%USERPROFILE%\.house_ai\`
was read, created, listed or copied; no split-key command was run. The repository and the frozen tree's source were
not edited; all edits are new files in `S/` (68 files). The only non-cache file in the frozen tree newer than the
Phase-1 register is `tools/orchestration/green_tests.json`, which I did not write (I never ran `green_tests.py`;
presumably a parallel reviewer). Every finding above labelled [ran] was executed and its output seen.
