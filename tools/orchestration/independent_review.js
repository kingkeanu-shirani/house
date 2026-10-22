export const meta = {
  name: 'house-ai-independent-review',
  description: 'Independent senior review of house_ai per the owner reviewer prompt: understand claims, attack specs with gaming policies, hunt perception leaks, assess architecture/process, judge the plan, and write a ranked, evidence-labelled report',
  phases: [
    { title: 'Understand', detail: 'claims register from the brief and repo docs' },
    { title: 'Break', detail: 'gaming policies on 15+ specs, wrong-reason passes, conditional specs, failing tests and gate' },
    { title: 'Perception', detail: 'leak hunters (two lenses) and auditor blind spots' },
    { title: 'Architecture', detail: 'duplication, hooks, deferred laws, golden brittleness, single-environment risk' },
    { title: 'Plan', detail: 'prioritisation, pilot, cost, core-first strategy, LLM pilot design' },
    { title: 'Report', detail: 'ranked report, then an evidence-label audit' },
  ],
}

const WS = '%SCRATCH%/review'
const TREE = WS + '/tree'
const OUT = WS + '/out'

const BASE = (id) => `You are part of an INDEPENDENT REVIEW of the house_ai project. Read and follow ${WS}/house_ai_reviewer_prompt.md (your role, mission, evidence labels [ran]/[read]/[inferred], quality bar) and ${WS}/house_ai_project_brief.md (the project). The owner says the prompt is guidance, not hard rules: go around it where that gives a better, more honest review. Two rules are absolute: (1) no paid API calls and nothing sent off this machine (LLM agents only with --dry-run); (2) never read, create, print or copy anything under %USERPROFILE%\\.house_ai\\ (and never run --init-split-key or anything that creates keys there).

THE CODE TO REVIEW is a frozen copy of commit bc0417b at ${TREE} (no .git; do experiments there or in your own scratch dir ${OUT}/${id}_scratch/; do NOT touch %PROJECT%, which another process is editing). Run Python from ${TREE} as the working directory (\`python -m house_sim ...\`, \`python tools/...\`). Windows; \`python\` = 3.14 (the code targets 3.9; only 3.14 is installed - you can check 3.9 syntax statically with ast.parse(src, feature_version=(3, 9)) and look for 3.10+ APIs). Machine is shared with another heavy job: keep worker counts modest (--workers 3) and expect some slowness. Be hard to impress, specific and honest; report null and negative results too. Never call something verified unless you ran it.`

const FIND = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low'] },
      title: { type: 'string' }, what: { type: 'string' }, evidence: { type: 'string' }, label: { type: 'string', enum: ['ran', 'read', 'inferred'] },
      why: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'title', 'what', 'evidence', 'label', 'why', 'fix'] } },
    strengths: { type: 'array', items: { type: 'string' } },
    could_not_verify: { type: 'array', items: { type: 'string' } },
    file: { type: 'string' },
    simple_update: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['id', 'findings', 'strengths', 'could_not_verify', 'file', 'simple_update', 'notes'],
}
const writeNote = (id) => `Write your full working record (commands run with results, every finding with evidence label, the scripts you wrote and where) to ${OUT}/${id}.md, and return the structured result (file = that path; simple_update = 3-5 plain sentences for a non-coder about what you did and found).`

phase('Understand')
const claims = await agent(`${BASE('p1_claims')}

PHASE 1 (Understand). Read the brief fully, then DECISIONS.md, docs/TASK_SPEC.md, docs/PERCEPTION.md, docs/KNOWN_LIMITS.md, docs/EVAL_CONTRACTS.md, docs/SPLITS.md, docs/design/TIER1_DECISIONS.md, docs/AUTHORING_GUIDE.md and skim docs/design/TIER1_INTEGRATION.md in the frozen tree. Produce a CLAIMS REGISTER: every testable claim the project makes about itself (verification method, 0 leaks, determinism, harm attribution, conditional variants, perceivability check, camera honesty, physics anchors, perf numbers, eval-suite guarantees...), each with where it is claimed and how a reviewer could test it, prioritised by how much the benchmark's trustworthiness depends on it. Also list the 32 authored spec files with spec counts per domain and which use realistic perception, so later phases can sample. Findings: anything already visibly wrong or contradictory in the docs. ${writeNote('p1_claims')}`, { label: 'p1:claims', phase: 'Understand', schema: FIND })

const ctx = `The claims register from phase 1 is ${OUT}/p1_claims.md - read it first.`
phase('Break')
const GAMING = [
  { id: 'p2_gaming_a', pick: 'cleaning, plumbing_liquids, textiles_laundry, food, appliances_cooking' },
  { id: 'p2_gaming_b', pick: 'admin_inventory, manipulation, mechanisms, tools_vehicles, electrical, climate' },
  { id: 'p2_gaming_c', pick: 'people, pets, safety, navigation_robot, outdoor_plants, and the realistic-perception (__perception.json) specs' },
]
const breakJobs = [
  ...GAMING.map(g => () => agent(`${BASE(g.id)}

PHASE 2 (Break the benchmark) - GAMING lens. ${ctx}
Pick at least 6 authored specs (tasks/authored/*.json) from these domains: ${g.pick} - vary size and kind (task, primitive, variation, adaptability, appendix where present). For EACH spec write 2-3 gaming policies that should NOT count as doing the task (move the mess elsewhere; act on the wrong instance or a look-alike; do the easy half; do nothing cleverly / exploit the time budget; exploit seed-specific layout; report success without acting; brute force such as opening/picking everything; skip a required permission/safety step; in realistic mode, act on glance-level belief). Run them: copy the spec into a scratch file in your scratch dir, add your plans as extra entries in its "wrong" list, and run \`python -m house_sim --check THAT_FILE --workers 3\` (a wrong plan that PASSES is reported as a failure of the spec - that is a successful game). Record per spec: each policy, whether it passed on which seeds, and why. Also look for specs that pass for the wrong reason (success conditions satisfiable by an unrelated state, milestones true at t=0, deadlines met by idling, side effects not protected). ${writeNote(g.id)}`, { label: g.id, phase: 'Break', schema: FIND })),
  () => agent(`${BASE('p2_conditional')}

PHASE 2 (Break) - CONDITIONAL & STRUCTURAL lens. ${ctx}
(a) Find every authored spec whose catalog entry is conditional ("if needed", "when appropriate", "safely", "when instructed", vague/adaptability) - catalog/entries.jsonl + catalog/analysis/part_*.jsonl give the entry text and analysis. For a large sample (at least 15 if that many exist), write an always-do policy and a never-do policy (and a ask-always / never-ask policy where questions are scored) and check each fails on at least one of the 5 seeds; report specs where a constant policy passes all seeds. (b) Static scan of all 243 specs for structural weaknesses: success predicates that idle or initial state could satisfy, missing side_effects/preserve where the analysis lists protect items, variants that do not actually change what the robot must do, phrasings that leak the answer, budgets so generous they allow brute force, wrong plans that fail for an unrelated reason (crash/refusal instead of the stated reason) - verify a sample by running. (c) Held-out seeds: run a sample of specs (at least 20) on seeds other than 0,1,2,41,97 (e.g. 3-12, 500-505) with \`--seeds\` and report how many reference plans fail or idle passes - this measures overfitting to the 5 verification seeds. ${writeNote('p2_conditional')}`, { label: 'p2:conditional', phase: 'Break', schema: FIND }),
  () => agent(`${BASE('p2_tests_gate')}

PHASE 2 (Break) - TESTS & GATE lens. ${ctx}
Run the full unit suite (\`python tools/orchestration/green_tests.py 3\` from the tree) and report exactly which tests fail (the brief says 14 tests in 7 kernel files pin pre-adoption behaviour) - for each failing test decide whether it is a stale pin (the new behaviour is right) or a genuine defect, with evidence. Run the gate tool (tools/orchestration/tier1_gate.py; read catalog/analysis/tier1/handoff_TIER1-GATE.md for usage; a C1 baseline is in catalog/analysis/tier1/gate/C1) as far as it works in this copy, and assess what the gate really protects and what it cannot catch. Run tools/fuzz.py 12 300 and a perception audit (tools/perception_audit.py 40 80) and layout audit (tools/layout_audit.py 20) and report results. Check determinism yourself: replay one reference plan in two fresh processes with different PYTHONHASHSEED and compare final state digests. ${writeNote('p2_tests_gate')}`, { label: 'p2:tests-gate', phase: 'Break', schema: FIND }),
]
const breaks = (await parallel(breakJobs)).filter(Boolean)
log(`break phase: ${breaks.map(b => b.id + ' ' + b.findings.length).join(', ')}`)

phase('Perception')
const LEAKS = [
  { id: 'p3_leaks_identity', text: 'IDENTITY lens: recover true entity ids, true types/labels of unresolved look-alikes and novel objects, owners/private flags, hidden contents, through: action messages and refusal wording (diff the outputs for each member of every look-alike group under identical actions), JSON key/list ORDER, handle stability/patterns, speech echo of typed ids, ask/report suggestions and answers, recall/memory, derived portions and contents_from, dark/backlit people, the camera_log and lidar channels, the viewer data.' },
  { id: 'p3_leaks_side', text: 'SIDE-CHANNEL lens: recover hidden numeric values or hidden state through: action durations / clock deltas (even rounded), energy/battery deltas (realistic obs.robot), success/failure of probing actions (pick_up refused vs ok, lift plans, place refusals), affordance lists, error messages with numbers, step counts, notice timing, wake-on-event timing, rendering noise that can be averaged (repeat inspect, move away and back, different robot poses), physics consequences visible earlier than they should be, observation size/field presence differences.' },
]
const leaks = (await parallel(LEAKS.map(l => () => agent(`${BASE(l.id)}

PHASE 3 (Perception honesty). ${ctx}
Write your own leak-hunting agents (scripts) that act ONLY through env.step in perception="realistic" (both camera modes: room and geometric) with handles taken from observations, exactly as an evaluated agent would. ${l.text}
For each leak you find give a minimal repro script and what exactly is learned. Treat the project's "0 leaks" as a claim to challenge: also list the leak CLASSES the automated auditor (tools/perception_audit.py) structurally cannot detect, with a concrete example of each. ${writeNote(l.id)}`, { label: l.id, phase: 'Perception', schema: FIND })))).filter(Boolean)

phase('Architecture')
const arch = await agent(`${BASE('p4_arch')}

PHASE 4 (Architecture and process). ${ctx}
Quantify what you can: lines per kernel module/domain, number of hooks/registries and their users, duplicated mechanics across domains (e.g. grep each domain for its own mass/fragility/spill/temperature/stacking/visibility logic and compare with kernel/phys/*, kernel/sight.py, kernel/body.py), deferred physics laws (data/physics/laws.json) and which domains block them, global/mutable module state, test brittleness (golden digests, exact-string pins, timing tests), import-time side effects, Python 3.9 compatibility (parse every .py with ast.parse(feature_version=(3,9)); grep for 3.10+ APIs such as zip(strict=), str.removeprefix is 3.9 ok, match, | in isinstance, typing features), Windows-only assumptions (paths, CRLF), absence of CI, performance hot spots (measure a few: observe cost realistic vs oracle, substep cost, a 1-day wait), replay size. Also assess the process: AI multi-agent rounds, gates, handoffs - where can regressions or dishonest specs slip through? ${writeNote('p4_arch')}`, { label: 'p4:architecture', phase: 'Architecture', schema: FIND })

phase('Plan')
const plan = await agent(`${BASE('p5_plan')}

PHASE 5 (Judge the plan). Read the phase outputs so far in ${OUT}/ (p1_claims.md, p2_*.md, p3_*.md, p4_arch.md) and the brief's sections 5-7. Evaluate: the order of work (foundations before authoring); the remaining plan (adoption integration, D1-D3 switch-overs, red team, authoring pilot of 8 batches, 52-batch wave, gap rounds, final verification); the cost and time estimates; whether authoring should be cut or tiered (design a concrete core-first strategy: which ~500 entries, how chosen, what quality bar, how audited); how to sample-audit AI-authored specs (exact procedure, sample sizes, acceptance thresholds, metamorphic tests, held-out seeds, randomised distractors - write exact definitions); and what the FIRST real LLM pilot should measure (design it, with a --dry-run cost estimate you actually run, e.g. \`python -m house_sim --run-agent llm:anthropic:claude-opus-5-5 --split dev --limit 10 --dry-run\` - never a real call). Give a recommended sequence. ${writeNote('p5_plan')}`, { label: 'p5:plan', phase: 'Plan', schema: FIND })

phase('Report')
const report = await agent(`${BASE('p6_report')}

PHASE 6 (Report). Read every phase record in ${OUT}/ (p1_claims.md, p2_*.md, p3_*.md, p4_arch.md, p5_plan.md). Write the final review to ${OUT}/REVIEW_REPORT.md following the reviewer prompt's quality bar: ranked findings (Critical/High/Medium/Low) each with what is wrong, evidence + label, why it matters, smallest fix; a direct answer to each of the owner's eight questions in its first two sentences, then support; "Top 10 things to do next, in order"; "What I could not verify"; "What is strong" (short, specific); exact definitions of every recommended new check (held-out seeds, metamorphic tests, distractors...) and which found failure it would catch. Deduplicate across phases; keep the strongest evidence; never upgrade an [inferred] item to [ran]. Start the report with a one-paragraph plain-language summary for a non-coder. ${writeNote('p6_report')}`, { label: 'p6:report', phase: 'Report', schema: FIND })

const audit = await agent(`${BASE('p6_audit')}

FINAL CHECK. Read ${OUT}/REVIEW_REPORT.md. For every finding labelled [ran], spot-check the evidence: re-run the cited command or script (from the phase records in ${OUT}/) for at least the Critical and High findings and a sample of the rest; downgrade or delete anything that does not reproduce or is [inferred] but written as proven; make sure no claim of verification is unsupported, no numbers are invented, and the hard rules were respected. Edit ${OUT}/REVIEW_REPORT.md in place (add a short "Evidence audit" section at the end listing what you re-ran and what changed). ${writeNote('p6_audit')}`, { label: 'p6:evidence-audit', phase: 'Report', schema: FIND })

return {
  report: OUT + '/REVIEW_REPORT.md',
  counts: [claims, ...breaks, ...leaks, arch, plan, report, audit].filter(Boolean).map(r => ({ id: r.id, findings: r.findings.length,
    critical: r.findings.filter(f => f.severity === 'Critical').length, high: r.findings.filter(f => f.severity === 'High').length })),
  audit_notes: audit && audit.notes,
}
