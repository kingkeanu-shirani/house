export const meta = {
  name: 'tier1-stage-b',
  description: 'Tier-1 stage B: baseline B0, then integration in merge order (physics, camera runtime, camera actions || rendering, body engine + nav M2, body core + nav M5 || body faults), merge gates, three-lens review, fixes, B-CLOSE',
  phases: [
    { title: 'B0', detail: 'baseline gate + body_audit baseline' },
    { title: 'B1', detail: 'PHYS-INT' },
    { title: 'B2', detail: 'CAM-RUNTIME' },
    { title: 'B3', detail: 'CAM-ACTIONS || CAM-RENDER, then merge gate' },
    { title: 'B4', detail: 'BODY-ENGINE || NAV-M2, then merge gate' },
    { title: 'B5', detail: 'BODY-CORE + NAV-M5 || BODY-FAULTS, then merge gate' },
    { title: 'Review', detail: 'physics / camera / body lenses' },
    { title: 'Fix', detail: 'confirmed findings' },
    { title: 'B6', detail: 'B-CLOSE' },
  ],
}

const ROOT = '%PROJECT%'
const SCRATCH = '%SCRATCH%'
const ENV = (id) => `Project: a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; code stays Python 3.9-compatible: no match statements, no PEP 604 unions or dict | dict at runtime; stdlib only; deterministic and replayable). The user's goal: "a real house slammed into code with no flaws" - a benchmark/training environment for household robot agents; the user asked for HIGH-QUALITY foundations (basic physics, camera, robot body, other necessities) before the remaining 2,478 catalog tasks are authored. Git repo: never commit, push, branch, stash or reset (the main session commits between stages). No network calls. Keep files LF (open(p, 'w', newline='\\n')). Bash heredocs can mangle backslashes: write helper scripts with a file tool. Use ONLY your private scratch dir ${SCRATCH}/t1_${id}/ (others share the parent folder; never delete outside your dir). Match the surrounding code style.`
const BINDING = `BINDING DOCS (read before coding): ${ROOT}/docs/design/TIER1_INTEGRATION.md - section 1 (contracts, especially 1.9 the order of work in engine.step/perform/_advance and the rows tagged with your package), your package row in section 2.4, the gate in section 3, the handoff protocol in section 5 - it wins over the layer specs where they differ; ${ROOT}/docs/design/TIER1_DECISIONS.md; the layer spec(s) for your package (docs/design/TIER1_PHYSICS.md / TIER1_CAMERA.md / TIER1_EMBODIMENT.md); and EVERY ${ROOT}/catalog/analysis/tier1/handoff_*.md (read them all first - they record what earlier packages actually landed and their deviations). Gate usage: catalog/analysis/tier1/handoff_TIER1-GATE.md.`
const QUALITY = `Quality bar: implement the package's full scope at production quality; follow the frozen contracts exactly (names, signatures, ordering); every behaviour change is either required by your package (and listed as a reviewed difference per section 3.3) or a bug; tests per the layer spec's test plan with meaningful assertions (behaviour, edge cases, determinism, legacy states, both perception modes, room and geometric camera modes where relevant); measure perf against section 3.4 budgets; update the docs your row lists. Where the spec is ambiguous or wrong, choose the more realistic and simpler option and record it as a deviation in your handoff.`

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

const pkg = (id, extra) => `${ENV(id)}

You implement tier-1 stage-B package **${id}** (TIER1_INTEGRATION.md section 2.4). ${BINDING}
${extra || ''}
${QUALITY}

YOU MAY EDIT ONLY the files listed for ${id} in section 2.4 (plus catalog/analysis/tier1/handoff_${id}.md and your gate output). Requests for other files go in requests.

Before returning: your tests normal + HOUSE_SIM_STRICT=1; \`python tools/orchestration/tier1_gate.py compare --package ${id}\` must PASS with only the reviewed differences your package is allowed; write ${ROOT}/catalog/analysis/tier1/handoff_${id}.md (what landed, APIs, reviewed differences, deviations, known gaps, how to test). Return the structured result (gate = the gate summary line).`

const merge = (stage, ids) => `${ENV('merge_' + stage)}

Sub-stage ${stage} ran packages ${ids.join(' and ')} IN PARALLEL on disjoint files. ${BINDING}
You are the ${stage} MERGE GATE: run the full gate on the merged tree (\`python tools/orchestration/tier1_gate.py\` with the union of those packages - see the gate handoff for union/--shared-tree usage) and the cross-package checks the plan lists for ${stage}. If anything fails because the two packages do not fit together, fix it within the union of their allowed files (section 2.4) with a regression test, and note it in both handoff files. Return the structured result with id "${stage}-MERGE" (gate = final summary line; landed = what you fixed).`

const LENSES = [
  { key: 'physics', text: 'PHYSICS INTEGRATION: settle ordering (1.9) and touch-log cursors; laws active at landing (orphan, overflow, setdown) vs deferred (laws.json) - no deferred law has effect; harm attribution (robot-caused vs exogenous) on real scenes; hidden thresholds never reach observations or messages in realistic mode; physics_audit over reference runs; perf of settle and processes per substep; replays deterministic with physics_version.' },
  { key: 'camera', text: 'CAMERA: room mode byte-identical to the golden; geometric mode correctness (FOV, occlusion behind sofas/under beds/top shelves with the 1.50 m eye, inside closed/open containers, darkness, glare, dirty lens), face/peek/look semantics and time costs, acuity limits on identity and reading, camera_log and privacy captures, realistic rendering (no leaks through camera/lidar channels), SearchAgent and harness in geometric mode, observe overhead budget.' },
  { key: 'body', text: 'ROBOT BODY: energy accounting for every action incl. nested ones, charging/docking, power-down semantics (ok=True with commit, ok=False atomic), payload/lift/grasp limits and derates, careful-speed fix, sensors/privacy/hearing model, faults and falls, proprioception rendering in realistic mode (no hidden robot fields), navigation_robot M2/M5 consistency, body_audit, perf.' },
]
const review = (lens) => `${ENV('review_' + lens.key)}

Tier-1 stage B just integrated physics, camera and robot body into the kernel (packages PHYS-INT, CAM-RUNTIME, CAM-ACTIONS, CAM-RENDER, BODY-ENGINE, NAV-M2, BODY-CORE, NAV-M5, BODY-FAULTS). ${BINDING}
You are an independent REVIEWER with one lens: ${lens.text}
Probe the real code (generate houses, HouseEnv in both perception modes and both camera modes, act through env.step as an agent would; write probes in your scratch dir). Report only verified findings (evidence: probe output, line refs, numbers) with package, severity (high = wrong behaviour, contract violation, leak or implausible result that will mislead tasks; medium; low) and a concrete fix; list what you verified as correct. Do not edit project files. ALSO write your full findings as JSON to ${ROOT}/catalog/analysis/tier1/stageB_review_${lens.key}.json (same shape as your structured result) so the fix pass reads them without truncation.`

const fixP = `${ENV('fixB')}

Stage-B review findings are in ${ROOT}/catalog/analysis/tier1/stageB_review_physics.json, stageB_review_camera.json and stageB_review_body.json (read ALL of them). ${BINDING}
For every finding: verify it; fix real ones at the root with a regression test (you may edit any file owned by a stage-B package in section 2.4, plus the handoffs); reject false positives with evidence; mark anything you cannot fix safely as still_open with a precise description. Then run the full gate and confirm all tests pass (normal + strict), 242/242 specs in own mode, the room-mode golden holds (or its re-record is a reviewed difference), perception audit 0 leaks. Return the structured result.`

const close = `${ENV('B6')}

You perform **B6 B-CLOSE** (TIER1_INTEGRATION.md section 2.4 "B6"). ${BINDING}
Do the full B6 row: viewer/dataset updates for physics events and body markers, consolidated docs/KNOWN_LIMITS.md rewrite, the ottoman core type for GEN, regenerate demos/replays and demos/manifest.json, regenerate reports/verification.jsonl and verification_run.json (\`python -m house_sim --verify\`), regenerate the leaderboard with tools/orchestration/regen_leaderboard.sh (it uses a throwaway verifier key from the environment - it must create NOTHING under %USERPROFILE%/.house_ai; check before and after), record tests/data/spec_baseline.json, and run the B6 gate. Do NOT create the archive snapshot or commit (the main session does). YOU MAY EDIT ONLY the B6 files. Return the structured result with id "B6".`

phase('B0')
const b0 = await agent(`${ENV('B0')}

You perform **B0** (TIER1_INTEGRATION.md section 2.4 "B0"; no source edits). ${BINDING}
Run the gate as \`B0\` on the committed tree (record mode), confirm it equals A0 except benchmark noise (compare), run \`python tools/body_audit.py --baseline\` and record the benchmark baseline as the plan says. Write catalog/analysis/tier1/handoff_B0.md with the results. Return the structured result with id "B0".`, { label: 'B0', phase: 'B0', schema: RESULT })
if (!b0) return { error: 'B0 failed' }

phase('B1')
const b1 = await agent(pkg('PHYS-INT', 'Follow the scope order in your row exactly: the FIRST edit is tools/golden.py + tests/test_room_golden.py recording tests/data/room_golden.json on the untouched tree. Also resolve two residual realism observations from the stage-A verifier (data/physics fixes are in your files): a ceramic vase breaks 100 % of the time from 0.75 m onto carpet (drop tolerance 0.15 m; check against real-world experience and the soft-landing model), and wheeled trash carts only move at about 25 m/s mean wind because gust-driven sliding/tipping is not modelled (decide whether gusts belong in the environment law; document the decision).'), { label: 'B1:PHYS-INT', phase: 'B1', schema: RESULT })
if (!b1) return { error: 'PHYS-INT failed', b0 }

phase('B2')
const b2 = await agent(pkg('CAM-RUNTIME'), { label: 'B2:CAM-RUNTIME', phase: 'B2', schema: RESULT })
if (!b2) return { error: 'CAM-RUNTIME failed', b0, b1 }

phase('B3')
const b3 = (await parallel([
  () => agent(pkg('CAM-ACTIONS', 'CAM-RENDER runs in parallel with you on disjoint files.'), { label: 'B3:CAM-ACTIONS', phase: 'B3', schema: RESULT }),
  () => agent(pkg('CAM-RENDER', 'CAM-ACTIONS runs in parallel with you on disjoint files.'), { label: 'B3:CAM-RENDER', phase: 'B3', schema: RESULT }),
])).filter(Boolean)
const m3 = await agent(merge('B3', ['CAM-ACTIONS', 'CAM-RENDER']), { label: 'B3:merge', phase: 'B3', schema: RESULT })

phase('B4')
const b4 = (await parallel([
  () => agent(pkg('BODY-ENGINE', 'NAV-M2 (the navigation_robot domain items paired with you) runs in parallel on disjoint files; both must land in the same merge.'), { label: 'B4:BODY-ENGINE', phase: 'B4', schema: RESULT }),
  () => agent(pkg('NAV-M2', 'You are the navigation_robot domain agent for this paired item; BODY-ENGINE runs in parallel on disjoint kernel files. Read docs/domains/navigation_robot.md and update it for what changes.'), { label: 'B4:NAV-M2', phase: 'B4', schema: RESULT }),
])).filter(Boolean)
const m4 = await agent(merge('B4', ['BODY-ENGINE', 'NAV-M2']), { label: 'B4:merge', phase: 'B4', schema: RESULT })

phase('B5')
const b5 = (await parallel([
  () => agent(pkg('BODY-CORE', 'You also do the paired NAV-M5 items (navigation_robot paths.py/follow.py timing="speed") in the same change; BODY-FAULTS runs in parallel on disjoint files and merges after you (it re-records the golden once for both). Before landing, body_audit must show no reference pick above the derated capacity and no explicit power grip on items under 15 mm; fix every hit in its spec first (you may edit tasks/authored/*.json for that, minimally, and list each edit).'), { label: 'B5:BODY-CORE+NAV-M5', phase: 'B5', schema: RESULT }),
  () => agent(pkg('BODY-FAULTS', 'BODY-CORE (+NAV-M5) runs in parallel on disjoint files; you merge second and re-record the room golden once for both B5 packages after BODY-CORE has finished (coordinate through the handoff files: wait for handoff_BODY-CORE.md before the re-record).'), { label: 'B5:BODY-FAULTS', phase: 'B5', schema: RESULT }),
])).filter(Boolean)
const m5 = await agent(merge('B5', ['BODY-CORE', 'NAV-M5', 'BODY-FAULTS']), { label: 'B5:merge', phase: 'B5', schema: RESULT })

phase('Review')
const reviews = (await parallel(LENSES.map(l => () => agent(review(l), { label: `review:${l.key}`, phase: 'Review', schema: FINDINGS })))).filter(Boolean)
phase('Fix')
const fix = await agent(fixP, { label: 'fix:stage-B', phase: 'Fix', schema: FIXR })
phase('B6')
const b6 = await agent(close, { label: 'B6:close', phase: 'B6', schema: RESULT })

const brief = (r) => r && ({ id: r.id, gate: r.gate, tests: r.tests, reviewed: r.reviewed_differences, not_done: r.not_done, requests: r.requests })
return {
  b0: brief(b0), b1: brief(b1), b2: brief(b2), b3: b3.map(brief), m3: brief(m3), b4: b4.map(brief), m4: brief(m4), b5: b5.map(brief), m5: brief(m5),
  review: reviews.map(r => ({ lens: r.lens, n: r.findings.length, high: r.findings.filter(f => f.severity === 'high').map(f => f.title) })),
  fix: fix && { gate: fix.gate, tests_total: fix.tests_total, tests_failed: fix.tests_failed, specs: fix.specs_passed, still_open: fix.items.filter(i => i.status === 'still_open') },
  b6: brief(b6),
}
