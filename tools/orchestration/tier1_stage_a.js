export const meta = {
  name: 'tier1-stage-a',
  description: 'Tier-1 foundations stage A0 + A: gate tool and sweep phase 0, then six libraries in parallel (physics data/statics/fluids, camera sight, body model, generator gen 2), two-lens review, fixes',
  phases: [
    { title: 'A0', detail: 'gate tool + sweep phase 0 (timeline carve-out, stubs, gen-1 digests, baseline)' },
    { title: 'Stage A', detail: 'six library packages in parallel, zero behaviour change' },
    { title: 'Review', detail: 'spec conformance/correctness lens + physical realism/perf/determinism lens' },
    { title: 'Fix', detail: 'confirmed review findings, then the gate' },
  ],
}

const ROOT = '%PROJECT%'
const SCRATCH = '%SCRATCH%'
const ENV = (id) => `Project: a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14, but code must stay Python 3.9-compatible: no match statements, no PEP 604 unions or dict | dict at runtime, stdlib only, deterministic and replayable). The user's goal: "a real house slammed into code with no flaws" - a benchmark/training environment for household robot agents; the user asked for HIGH-QUALITY foundations (basic physics, camera, robot body, other necessities) before the remaining 2,478 catalog tasks are authored. The folder is a git repo: never commit, push, branch or reset (the main session commits between stages). Never make network calls. Keep files LF (Python's Path.write_text writes CRLF on Windows: use open(p, 'w', newline='\\n')). Bash heredocs can mangle backslashes: write helper scripts with a file tool. Use ONLY your private scratch dir ${SCRATCH}/t1_${id}/ for temporary files (other agents share the parent folder - never delete anything outside your own dir). Match the surrounding code style (docstrings, naming, comment density).`

const BINDING = `BINDING DOCS (read before coding): ${ROOT}/docs/design/TIER1_INTEGRATION.md (section 1 contracts, your package row in section 2, the gate in section 3, handoff protocol in section 5 - it wins over the layer specs where they differ), ${ROOT}/docs/design/TIER1_DECISIONS.md, the layer spec for your package (docs/design/TIER1_PHYSICS.md / TIER1_CAMERA.md / TIER1_EMBODIMENT.md / TIER1_SWEEP.md), and every ${ROOT}/catalog/analysis/tier1/handoff_*.md already written.`

const RESULT = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    landed: { type: 'array', items: { type: 'string' } },
    deviations: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, why: { type: 'string' } }, required: ['what', 'why'] } },
    not_done: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, why: { type: 'string' } }, required: ['what', 'why'] } },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests: { type: 'object', properties: { new_tests: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' } }, required: ['new_tests', 'passed', 'strict_passed'] },
    gate: { type: 'string' },
    handoff: { type: 'string' },
    requests: { type: 'array', items: { type: 'string' } },
  },
  required: ['id', 'landed', 'deviations', 'not_done', 'files_changed', 'tests', 'gate', 'handoff', 'requests'],
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
const FIX = {
  type: 'object',
  properties: {
    fixed: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, how: { type: 'string' } }, required: ['title', 'how'] } },
    rejected: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, why: { type: 'string' } }, required: ['title', 'why'] } },
    gate: { type: 'string' },
    tests_total: { type: 'integer' }, tests_failed: { type: 'integer' }, specs_passed: { type: 'integer' },
    notes: { type: 'string' },
  },
  required: ['fixed', 'rejected', 'gate', 'tests_total', 'tests_failed', 'specs_passed', 'notes'],
}

const a0Prompt = `${ENV('A0')}

You implement stage **A0 (TIER1-GATE + sweep Phase 0)**. ${BINDING} For Phase 0 read TIER1_SWEEP.md sections 14.1, 16.1 and 22.1.

Deliver, exactly as specified (section 2.3 "A0" and section 3 of TIER1_INTEGRATION.md):
1. tools/orchestration/tier1_gate.py: the gate runner G1-G10 (unit tests normal + HOUSE_SIM_STRICT=1, isolated domains, all authored specs in their own mode and forced realistic with verdict + reference state-hash comparison against a recorded baseline, perception_audit, layout_audit, fuzz with determinism, bench/perf budgets, room-mode golden hook for later, scope check of changed files against a package's allowed list), with \`record\` and \`compare\` modes, a --package ID option that applies that package's allowed files and reviewed differences, and clear PASS/FAIL output. Document it in tools/orchestration/README.md. It must run on this Windows machine within a reasonable time (use worker pools; reuse tools/orchestration/green_tests.py ideas).
2. Sweep Phase 0: move wait/watch/cancel_watch/run_pending/watch_condition_visible (+ helpers) from systems/core.py into a new house_sim/kernel/timeline.py imported by engine.py, with re-exports in core.py (behaviour-preserving); stub modules with the frozen signatures of sweep 16.1 (kernel/dialogue.py, reports.py, mind.py, monitor.py, phases.py, generator/profile.py: plain functions raising NotImplementedError, registering nothing, import-inert); tests/data/gen1_digests.json (generation-1 golden digests per sweep 14.1) plus whatever spec baseline file the gate needs.
3. Record the A0 baseline with the gate into ${ROOT}/catalog/analysis/tier1/gate/A0/ and confirm the tree is green (all tests pass, 242/242 specs in own mode; forced-realistic results are recorded as the baseline, not required to pass).
4. Write ${ROOT}/catalog/analysis/tier1/handoff_TIER1-GATE.md (how to run the gate, what each check does, where baselines live, the package scope table it enforces).

YOU MAY EDIT ONLY the A0 files listed in TIER1_INTEGRATION.md section 2.3 (and the gate/baseline outputs). Return the structured result (gate = the final gate summary line).`

const PKGS = [
  { id: 'PHYS-DATA', spec: 'TIER1_PHYSICS.md sections 4 and 5.6 (plus 7 for contracts, 12 for tests)' },
  { id: 'PHYS-STATICS', spec: 'TIER1_PHYSICS.md sections 5.1-5.5 and 6.4 (plus 7, 12)' },
  { id: 'PHYS-FLUIDS', spec: 'TIER1_PHYSICS.md sections 5.7-5.10, 6.2-6.3 (plus 7, 12)' },
  { id: 'CAM-SIGHT', spec: 'TIER1_CAMERA.md sections 4-7 and 10.1 (plus 17 for tests)' },
  { id: 'BODY-MODEL', spec: 'TIER1_EMBODIMENT.md sections 3-5 (plus 10 for tests)' },
  { id: 'GEN', spec: 'TIER1_SWEEP.md sections 12-14 and 22.2' },
]
const pkgPrompt = (p) => `${ENV(p.id)}

You implement tier-1 stage-A work package **${p.id}**. ${BINDING} Your layer spec sections: ${p.spec}. The A0 handoff (catalog/analysis/tier1/handoff_TIER1-GATE.md) explains the gate.

Stage A packages develop IN PARALLEL against the frozen contracts (the other five stage-A packages are being written right now; if you call another package's module, code against its frozen signature and keep your own tests independent of not-yet-landed packages - use small local fakes where needed). Stage-A libraries are import-inert and cause ZERO behaviour change to the running simulator (GEN: generation-1 output stays byte-identical; everything new is behind gen >= 2).

Quality bar: implement the full scope of your package as specified, at production quality - real-world-justified numbers in the data tables (cite the source or reasoning in a comment/field), clear docstrings, no dead code, deterministic, efficient (respect the spec's perf budget; measure it), every function covered by the spec's test plan with meaningful assertions (behaviour, edge cases, determinism), not just smoke tests. Where the spec is ambiguous or wrong, choose the more realistic and simpler option and record it under deviations.

YOU MAY EDIT ONLY the files listed for ${p.id} in TIER1_INTEGRATION.md section 2.3 (plus your handoff file). Requests for other files go in requests.

Before returning: run your tests normally and with HOUSE_SIM_STRICT=1; run \`python tools/orchestration/tier1_gate.py compare --package ${p.id}\` (see the A0 handoff for exact usage) and make it PASS; write ${ROOT}/catalog/analysis/tier1/handoff_${p.id}.md (what landed, APIs with signatures, deviations from the spec, known gaps, how to test). Return the structured result (gate = the gate summary line).`

const LENSES = [
  { key: 'conformance', text: 'SPEC CONFORMANCE & CORRECTNESS: for each package, check the code against TIER1_INTEGRATION.md (contracts, file scope, import-inertness, zero behaviour change) and its layer spec sections (APIs and signatures exactly as frozen, formulas implemented correctly, all listed functions present, edge cases). Run the package tests and write your own probes for formulas and edge cases (zero/negative masses, missing fields, legacy states, unknown materials, nested containment, determinism across PYTHONHASHSEED). Check the tests actually test behaviour (would they catch a wrong formula?).' },
  { key: 'realism', text: 'PHYSICAL REALISM, PERFORMANCE & DETERMINISM: are the numbers in the data tables (materials, liquids, floors, supports, robot profiles, generator household/belonging/climate distributions) physically and statistically plausible, sourced, and internally consistent? Do formulas give sane results across realistic household cases (glass vs ceramic drops at 0.3/0.9/1.5 m, shelf loads, stack toppling, pour/overflow volumes, coffee cooling time, ice melting, wind on light objects, viewshed behind a sofa / under a bed / on a 2 m shelf with a 1.50 m eye, battery runtime and charging time, lift limits, generated household compositions)? Measure CPU cost against the specs\' budgets. Check determinism (same seed -> same results in a fresh process with another PYTHONHASHSEED).' },
]
const reviewPrompt = (lens, results) => `${ENV('review_' + lens.key)}

Tier-1 stage A just landed six library packages (PHYS-DATA, PHYS-STATICS, PHYS-FLUIDS, CAM-SIGHT, BODY-MODEL, GEN) plus A0 (gate tool, timeline carve-out, sweep stubs). Package handoffs: ${ROOT}/catalog/analysis/tier1/handoff_*.md. Package reports (summary): ${JSON.stringify(results.map(r => ({ id: r.id, landed: r.landed, deviations: r.deviations, not_done: r.not_done, files: r.files_changed }))).slice(0, 9000)}

${BINDING}

You are an independent REVIEWER with one lens. ${lens.text}

Report only findings you verified (with evidence: a probe output, a line reference, a number), each with the package, severity (high = wrong behaviour or contract violation or implausible number that will mislead tasks; medium; low), and a concrete fix. Also list what you verified as correct. Do not edit project files (probes go in your scratch dir).`

const fixPrompt = (findings, results) => `${ENV('fix')}

Tier-1 stage A review findings (two lenses): ${JSON.stringify(findings).slice(0, 16000)}
Package reports: ${JSON.stringify(results.map(r => ({ id: r.id, not_done: r.not_done, requests: r.requests }))).slice(0, 5000)}

${BINDING}

You own the stage-A files for this fix pass (the files listed for A0 and the six stage-A packages in TIER1_INTEGRATION.md section 2.3, plus their handoff files). Verify each finding yourself; fix every real one at the root with a regression test; reject false positives with a reason. Address package requests that are within stage-A files. Update the affected handoff files. Then run the full gate (\`python tools/orchestration/tier1_gate.py compare\` for all stage-A packages) and confirm: all unit tests pass (normal + strict), 242/242 specs in own mode, zero behaviour change vs the A0 baseline, generation-1 digests unchanged. Return the structured result.`

phase('A0')
const a0 = await agent(a0Prompt, { label: 'A0:gate+phase0', phase: 'A0', schema: RESULT })
if (!a0) return { error: 'A0 failed' }
log(`A0: ${a0.gate}`)

phase('Stage A')
const results = (await parallel(PKGS.map(p => () => agent(pkgPrompt(p), { label: `A:${p.id}`, phase: 'Stage A', schema: RESULT })))).filter(Boolean)
log(`stage A landed: ${results.map(r => r.id).join(', ')}`)

phase('Review')
const reviews = (await parallel(LENSES.map(l => () => agent(reviewPrompt(l, [a0, ...results]), { label: `review:${l.key}`, phase: 'Review', schema: FINDINGS })))).filter(Boolean)
const findings = reviews.flatMap(r => r.findings.map(f => ({ lens: r.lens.slice(0, 20), ...f })))
log(`review: ${findings.length} findings (${findings.filter(f => f.severity === 'high').length} high)`)

phase('Fix')
const fix = await agent(fixPrompt(findings, [a0, ...results]), { label: 'fix:stage-A', phase: 'Fix', schema: FIX })

return {
  a0: { gate: a0.gate, deviations: a0.deviations, not_done: a0.not_done, requests: a0.requests },
  packages: results.map(r => ({ id: r.id, gate: r.gate, tests: r.tests, deviations: r.deviations, not_done: r.not_done, requests: r.requests })),
  missing: PKGS.map(p => p.id).filter(id => !results.find(r => r.id === id)),
  review: reviews.map(r => ({ lens: r.lens, n: r.findings.length, verified_ok: r.verified_ok })),
  findings,
  fix,
}
