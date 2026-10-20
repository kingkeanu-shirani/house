export const meta = {
  name: 'tier1-stage-c',
  description: 'Tier-1 stage C: the sweep packages TIME-EVAL || PEOPLE-IO (time, standing orders, temporal evaluation, phases; questions, reports, memory, requests), merge gate, two-lens review, fixes, C-CLOSE',
  phases: [
    { title: 'C', detail: 'TIME-EVAL || PEOPLE-IO (TIME-EVAL merges first)' },
    { title: 'Merge', detail: 'merge gate, stub removal' },
    { title: 'Review', detail: 'time/evaluation lens + dialogue/reports/memory lens' },
    { title: 'Fix', detail: 'confirmed findings' },
    { title: 'C1', detail: 'C-CLOSE' },
  ],
}

const ROOT = '%PROJECT%'
const SCRATCH = '%SCRATCH%'
const ENV = (id) => `Project: a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; code stays Python 3.9-compatible; stdlib only; deterministic and replayable). The user's goal: "a real house slammed into code with no flaws" - a benchmark/training environment for household robot agents; the user asked for HIGH-QUALITY foundations before the remaining 2,478 catalog tasks are authored. Git repo: never commit, push, branch, stash or reset. No network calls. Keep files LF (open(p, 'w', newline='\\n')). Write helper scripts with a file tool (bash heredocs mangle backslashes). Use ONLY your private scratch dir ${SCRATCH}/t1_${id}/. Match the surrounding code style.`
const BINDING = `BINDING DOCS (read before coding): ${ROOT}/docs/design/TIER1_SWEEP.md (your package's sections), ${ROOT}/docs/design/TIER1_INTEGRATION.md (section 1 contracts incl. 1.3 sensors, 1.8 World, 1.9 step/substep order; section 2.5 your package row; section 3 gate; section 5 handoff), ${ROOT}/docs/design/TIER1_DECISIONS.md, and EVERY ${ROOT}/catalog/analysis/tier1/handoff_*.md (stages A and B landed physics, camera and robot body - read what they actually built and their deviations). Gate usage: handoff_TIER1-GATE.md.`
const QUALITY = `Quality bar: full scope at production quality; frozen contracts exactly (sweep 16.1); behaviour changes only where your package requires them (reviewed differences per section 3.3; the sweep lists the milestone/perceivability verdict risks - triage each spec whose verdict changes and fix the spec or the code, never silently); tests per the sweep's test plan with meaningful assertions (determinism, both perception modes, room and geometric camera, long waits, legacy specs); perf against the sweep's budgets (long horizons: idle probes); docs your row lists. Ambiguity: choose the more realistic and simpler option and record it as a deviation in your handoff.`

const RESULT = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    landed: { type: 'array', items: { type: 'string' } },
    reviewed_differences: { type: 'array', items: { type: 'string' } },
    deviations: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, why: { type: 'string' } }, required: ['what', 'why'] } },
    not_done: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, why: { type: 'string' } }, required: ['what', 'why'] } },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests: { type: 'object', properties: { new_tests: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' } }, required: ['new_tests', 'passed', 'strict_passed'] },
    gate: { type: 'string' },
    requests: { type: 'array', items: { type: 'string' } },
  },
  required: ['id', 'landed', 'reviewed_differences', 'deviations', 'not_done', 'files_changed', 'tests', 'gate', 'requests'],
}
const FINDINGS = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', properties: {
      package: { type: 'string' }, severity: { type: 'string' }, title: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
      required: ['package', 'severity', 'title', 'evidence', 'fix'] } },
    verified_ok: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['lens', 'findings', 'verified_ok', 'notes'],
}
const FIXR = {
  type: 'object',
  properties: {
    items: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, status: { type: 'string', enum: ['fixed', 'rejected', 'still_open'] }, detail: { type: 'string' } }, required: ['title', 'status', 'detail'] } },
    gate: { type: 'string' }, tests_total: { type: 'integer' }, tests_failed: { type: 'integer' }, specs_passed: { type: 'integer' }, notes: { type: 'string' },
  },
  required: ['items', 'gate', 'tests_total', 'tests_failed', 'specs_passed', 'notes'],
}

const pkg = (id, partner) => `${ENV(id)}

You implement tier-1 stage-C package **${id}** (TIER1_INTEGRATION.md section 2.5; TIER1_SWEEP.md). ${BINDING}
${partner} runs IN PARALLEL with you on disjoint files; code against the frozen 16.1 contracts (A0 stubs until your partner hands over; mark remaining stub calls TIER1-STUB(<ID>)). ${id === 'TIME-EVAL' ? 'You merge first.' : 'You merge second: when your work is ready, wait until catalog/analysis/tier1/handoff_TIME-EVAL.md exists, re-run your tests and the gate on the merged tree, and adapt.'}
${QUALITY}

YOU MAY EDIT ONLY the files listed for ${id} in section 2.5 (plus catalog/analysis/tier1/handoff_${id}.md and your gate output). Requests for other files go in requests.

Before returning: tests normal + HOUSE_SIM_STRICT=1; \`python tools/orchestration/tier1_gate.py compare --package ${id}\` PASS with only allowed reviewed differences; write the handoff. Return the structured result (gate = summary line).`

const mergeP = `${ENV('C_merge')}

Stage C ran TIME-EVAL and PEOPLE-IO in parallel on disjoint files. ${BINDING}
You are the stage-C MERGE GATE: remove every remaining TIER1-STUB(TIME-EVAL) / TIER1-STUB(PEOPLE-IO) call (wire the real implementations), run the full gate on the merged tree (union of both packages), and fix anything that does not fit together within the union of their allowed files (section 2.5), with regression tests; note fixes in both handoffs. Return the structured result with id "C-MERGE".`

const LENSES = [
  { key: 'time', text: 'TIME & EVALUATION: interruptible waits and wake-on-event using only perceivable signals; standing orders/monitors executing during long waits; notices; temporal evaluation (failure/preserve/side_effects over the whole episode, milestones/deadlines sampled correctly, no verdict flips from sampling artefacts); multi-phase episodes (instruction changes, per-phase success/questions/deadlines, harness done protocol); clock/calendar/holidays/DST; savepoints; long-horizon perf (idle probes; a simulated week); determinism and replay.' },
  { key: 'people', text: 'QUESTIONS, REPORTS & MEMORY: ask/user-simulator matching (free-form topics, synonyms, multi-turn, preferences; necessary vs unnecessary questions scored fairly; no hidden-intent leaks through answers or refusals), the shared report vocabulary and report predicates, memory/recall/known map in realistic mode (survives outbound translation honestly, no id oracles), request rendering (requester, channel, location, gesture) in observations, listen/hearing with privacy mode, the LLM agent prompt/docs (accurate, compact), perception audit clean.' },
]
const review = (lens) => `${ENV('reviewC_' + lens.key)}

Tier-1 stage C just landed the sweep packages TIME-EVAL and PEOPLE-IO. ${BINDING}
You are an independent REVIEWER with one lens: ${lens.text}
Probe the real code as an agent would (env.step with handles from observations; both perception modes; geometric camera too). Report only verified findings (evidence) with package, severity (high/medium/low) and a concrete fix; list what you verified as correct. Do not edit project files. ALSO write your full findings as JSON to ${ROOT}/catalog/analysis/tier1/stageC_review_${lens.key}.json.`

const fixP = `${ENV('fixC')}

Stage-C review findings: ${ROOT}/catalog/analysis/tier1/stageC_review_time.json and stageC_review_people.json (read ALL). ${BINDING}
For every finding: verify; fix real ones at the root with a regression test (any file owned by TIME-EVAL or PEOPLE-IO in section 2.5, plus handoffs); reject false positives with evidence; mark still_open what you cannot fix safely. Run the full gate: all tests normal + strict, 242/242 specs in own mode (or each verdict change triaged and fixed), perception audit 0 leaks. Return the structured result.`

const closeP = `${ENV('C1')}

You perform **C1 C-CLOSE** (TIER1_INTEGRATION.md section 2.5 "C1"). ${BINDING}
Regenerate verification evidence (\`python -m house_sim --verify\`), demo replays and the dataset sample (the action fingerprint changed), refresh tests/data/spec_baseline.json, regenerate the leaderboard with tools/orchestration/regen_leaderboard.sh (it must create NOTHING under %USERPROFILE%/.house_ai - check before and after), run the C1 gate. Do NOT snapshot or commit. Return the structured result with id "C1".`

phase('C')
const pk = (await parallel([
  () => agent(pkg('TIME-EVAL', 'PEOPLE-IO'), { label: 'C:TIME-EVAL', phase: 'C', schema: RESULT }),
  () => agent(pkg('PEOPLE-IO', 'TIME-EVAL'), { label: 'C:PEOPLE-IO', phase: 'C', schema: RESULT }),
])).filter(Boolean)
phase('Merge')
const mg = await agent(mergeP, { label: 'C:merge', phase: 'Merge', schema: RESULT })
phase('Review')
const reviews = (await parallel(LENSES.map(l => () => agent(review(l), { label: `reviewC:${l.key}`, phase: 'Review', schema: FINDINGS })))).filter(Boolean)
phase('Fix')
const fix = await agent(fixP, { label: 'fix:stage-C', phase: 'Fix', schema: FIXR })
phase('C1')
const c1 = await agent(closeP, { label: 'C1:close', phase: 'C1', schema: RESULT })
const brief = (r) => r && ({ id: r.id, gate: r.gate, tests: r.tests, reviewed: r.reviewed_differences, not_done: r.not_done, requests: r.requests })
return {
  packages: pk.map(brief), merge: brief(mg),
  review: reviews.map(r => ({ lens: r.lens, n: r.findings.length, high: r.findings.filter(f => f.severity === 'high').map(f => f.title) })),
  fix: fix && { gate: fix.gate, tests_total: fix.tests_total, tests_failed: fix.tests_failed, specs: fix.specs_passed, still_open: fix.items.filter(i => i.status === 'still_open') },
  c1: brief(c1),
}
