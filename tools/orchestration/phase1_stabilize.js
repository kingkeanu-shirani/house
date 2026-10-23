export const meta = {
  name: 'phase1-stabilize',
  description: 'Finish-the-project phase 1: 16 domain owners fix their adoption breakages and the review findings in their code, three kernel agents close the review perception leaks, the real defects and harden the gate/CI; integrate; two-lens red team re-runs the review probes; fix; repair domains',
  phases: [
    { title: 'Fix', detail: '16 domains + K-LEAK + K-FIX + K-GATE, disjoint files' },
    { title: 'Integrate', detail: 'merged gate, kernel-side fixes, domain breakage list' },
    { title: 'Red team', detail: 'leaks re-probed with the reviewers scripts; defects/gate honesty' },
    { title: 'Fix2', detail: 'confirmed findings' },
    { title: 'Repair', detail: 'only domains still failing' },
  ],
}

const ROOT = '%PROJECT%'
const SCRATCH = '%SCRATCH%'
const REVIEWDIR = SCRATCH + '/review/out'
const OUT = ROOT + '/catalog/analysis/phase1'
const ENV = (id) => `Project: house_ai, a pure-Python (stdlib, Python 3.9-compatible, deterministic, replayable) household simulator at ${ROOT} (Windows; \`python\` = 3.14). Goal (owner): "a real house slammed into code with no flaws" - a trustworthy benchmark for household robot agents. Owner's standing rules: free rein, quality first; NO paid API calls or data sent off the machine; never create anything under %USERPROFILE%\\.house_ai\\ (the split key is created only after telling the owner). Git repo: never commit, push, branch, stash or reset (the main session commits). Keep files LF (open(p, 'w', newline='\\n')). Write helper scripts with a file tool (bash heredocs mangle backslashes). Use ONLY your private scratch dir ${SCRATCH}/p1_${id}/. Match the surrounding code style. Other owners edit their own files concurrently; re-run before concluding when results look inconsistent.

CONTEXT: an independent review just found the benchmark not yet trustworthy: ${ROOT}/docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md (ranked findings, section 1; top-10 plan, section 3) with phase records in phases/. The adoption round left 24 domain breakages and open requests: ${ROOT}/catalog/analysis/tier1/adopt_status.json. Kernel contracts: docs/KERNEL_API.md, docs/PERCEPTION.md, docs/PHYSICS.md, docs/design/TIER1_INTEGRATION.md. The verifier anti-gaming redesign (docs/design/VERIFY_HARDENING.md) is the NEXT phase - do not implement it now, but do not contradict it.

HANDOFF: write your full structured result as JSON to ${OUT}/<your id>.json (create the folder) in addition to returning it.`

const DOMAINS = ['navigation_robot', 'manipulation', 'people', 'safety', 'cleaning', 'plumbing_liquids', 'appliances_cooking', 'food',
  'textiles_laundry', 'admin_inventory', 'electrical', 'climate', 'mechanisms', 'tools_vehicles', 'outdoor_plants', 'pets']

const RESULT = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    fixed: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, how: { type: 'string' }, test: { type: 'string' } }, required: ['item', 'how', 'test'] } },
    not_fixed: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, why: { type: 'string' } }, required: ['item', 'why'] } },
    specs_changed_outcome: { type: 'array', items: { type: 'string' } },
    tests: { type: 'object', properties: { count: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' }, isolated_passed: { type: 'boolean' } }, required: ['count', 'passed', 'strict_passed', 'isolated_passed'] },
    specs: { type: 'object', properties: { total: { type: 'integer' }, passed: { type: 'integer' } }, required: ['total', 'passed'] },
    requests: { type: 'array', items: { type: 'object', properties: { owner: { type: 'string' }, request: { type: 'string' } }, required: ['owner', 'request'] } },
    notes: { type: 'string' },
  },
  required: ['id', 'fixed', 'not_fixed', 'specs_changed_outcome', 'tests', 'specs', 'requests', 'notes'],
}
const FIND = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', properties: { owner: { type: 'string' }, severity: { type: 'string' }, title: { type: 'string' }, repro: { type: 'string' }, fix: { type: 'string' } }, required: ['owner', 'severity', 'title', 'repro', 'fix'] } },
    closed: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['lens', 'findings', 'closed', 'notes'],
}

const domainPrompt = (d) => `${ENV(d)}

You own the **${d}** mechanics domain (house_sim/systems/${d}/). Phase 1 (stabilise) for your domain:
1. Every breakage and open request attributed to ${d} in catalog/analysis/tier1/adopt_status.json (breakages, not-applied requests that need domain work, cross-domain bugs) - fix at the root, with a regression test each.
2. Every finding in the review (REVIEW_REPORT.md section 1 and the phase records docs/reviews/2026-10-22_independent_review/phases/*.md) whose owner or root cause is your domain's CODE or TYPE DATA (search for "${d}" and for your type and file names): e.g. perception leaks through your messages, refusals, inspect key sets, glance fields, type defaults that differ inside look-alike groups, remote knowledge grants, wrong physics interplay, exogenous harms charged to the robot, missing visual events, direct writes that should go through kernel helpers. Fix each at the root with a regression test. Spec weaknesses (gaming of your specs) are NOT for this phase - the hardened verifier (next phase) will drive spec repair - unless a spec now FAILS because of a code fix you made (then fix the spec minimally and list it in specs_changed_outcome).
3. The LookalikeWarning printed at registry load for groups you own: align members or set group color/size_m.
Anything that needs kernel/core/generator/tasks code goes to requests (owner "kernel") with a precise repro - do not edit those files.

GREEN: \`python -m unittest tests/test_${d}.py\` normal, HOUSE_SIM_STRICT=1, and HOUSE_SIM_DOMAINS=core,${d}; \`python -m house_sim --check tasks/authored/${d}__examples.json --workers 2\` and the __perception file (5 seeds, own mode); realistic files also with \`--camera geometric\`; \`python tools/perception_audit.py 20 80 --verbose\` and \`python tools/layout_audit.py 20 --domain ${d}\` attribute nothing to ${d}. Update docs/domains/${d}.md.
YOU MAY EDIT ONLY: house_sim/systems/${d}/**, house_sim/data/types/${d}.json, house_sim/data/vocab/${d}.json, tests/test_${d}.py, docs/domains/${d}.md, tasks/authored/${d}__examples.json, tasks/authored/${d}__perception.json. Return the structured result (id "${d}").`

const K_LEAK = `Close the review's KERNEL perception leaks (REVIEW_REPORT.md sections 1 and 4; phases/p3_leaks_identity.md and p3_leaks_side.md have exact repros and the scripts are in ${REVIEWDIR}/p3_leaks_identity_scratch/ and p3_leaks_side_scratch/ - re-run them against the live tree before and after):
- L1: wait(until)/watch/guards must evaluate on what the robot perceives (rendered values / realism knowledge), never on true state, in realistic mode; no exact-value or asleep/awake oracle.
- I1: the street address must not encode the seed, and the variant choice must not be derivable from any observable: derive both from independent keyed hashes (variant from a hash of spec id + seed with a domain-separated salt; address from a separate stream). Keep oracle verification deterministic. List every spec whose outcome changes.
- I5: handles and fixed-draw noise get a per-episode nonce (episodes of the same spec and seed must not share handles or noise), geometric-mode actions accept only handles issued in THIS episode and things the robot has actually detected.
- I6: free-text echo and handle-accepting text parameters must not act as an existence oracle (unseen live vs destroyed vs never-existing ids must be indistinguishable).
- L2: geometric view.px from the displayed (group/estimated) size, not true size. L3: battery readout quantised and energy accounting must not make the robot a scale (decouple or coarsen; document the choice).
- I3-kernel side: ctx.grant must refuse inspect-tier grants beyond reach/acuity for any domain (the admin_inventory owner fixes count itself; add the kernel guard).
- Auditor: upgrade tools/perception_audit.py with differential twin-scene tests (look-alike members, novel objects), cross-episode handle checks, and oracle probes for until/watch, so these classes are detected from now on; it must report the pre-fix leaks on a pre-fix tree (prove it with a stash-free copy in your scratch dir) and 0 after.
YOUR FILES: house_sim/kernel/realism.py, timeline.py, perception.py, camera.py, sight.py, proprio.py, actions.py (grant guard only), house_sim/generator/__init__.py (address only), house_sim/tasks/spec.py (variant derivation only), tools/perception_audit.py, tests/test_perception.py, tests/test_leaks_review.py (new), docs/PERCEPTION.md.`

const K_FIX = `Fix the review's 3 genuine defects and the remaining kernel-side items, at the root with regression tests:
- one-hand pick-up just under the limit (7.65-8 kg) drops the item (check lift_plan vs check_lift vs the hold safety net / handle law);
- wrapped broken glass still scatters shards (kernel side: impact/break with wraps; the safety owner fixes its own hook concurrently - coordinate via the contract: a fully wrapped item does not scatter);
- a single privacy intrusion counted twice with the geometric camera (one violation per intrusion moment, per the documented rule);
- stale static-index caches outside the journal (save/load divergence, phases/p4_arch.md);
- adopt_status.json kernel items not applied yet that are precise and correct (e.g. mechanisms-6 lock/unlock with a held key whose opens names the door; the gen-2 furnishing fixes admin_inventory-5 key bowl spot and textiles sofa_cushions ordering - re-verify gen-2 specs); harm attribution for exogenous events in kernel code paths (wind slams, pets) per TIER1_DECISIONS;
- the stack law's in-use size / ironing-board and bowl-on-scale failures that broke 8 specs under the forced 'ready' laws (phases/p4_arch.md A1) - fix the kernel side so D1 can flip cleanly later.
YOUR FILES: house_sim/kernel/phys/**, house_sim/kernel/body.py, body_faults.py, world.py, engine.py, evaluation.py, house_sim/systems/core.py, house_sim/generator/ (except __init__.py), house_sim/data/physics/**, house_sim/data/robot/**, house_sim/data/types/core.json, tests/test_physics*.py, tests/test_body*.py, tests/test_kernel*.py, tests/test_generator*.py, tests/data/** (golden and digest re-records: only after confirming each change is intended; list them), docs/PHYSICS.md, docs/KNOWN_LIMITS.md.`

const K_GATE = `Harden the gate and the process tooling (REVIEW_REPORT.md top-10 item 3; phases/p2_tests_gate.md T3/T4, phases/p4_arch.md A3, phases/p5_plan.md):
- tools/orchestration/tier1_gate.py: absolute rules beside the delta rule (every authored spec passes in its own mode; every test passes except a dated allowlist file with owner and expiry); review codes bound to the files a package owns plus a second signature; wrong-plan failure labels recorded in trial records and compared; SKIP counts as FAIL; G4/G6 regressions fail compare; G10 fails without git; the adoption-style --shared-tree compare works; per-action success coverage reported.
- tools/fuzz.py: coverage-driven mode that drives every registered action to its success path at least once (report actions never succeeding) - the review measured only 46 of 427.
- house_sim/tasks/verify.py: record each wrong plan's failure labels and whether it ran fully; a wrong plan that raises (PlanError or any exception) is NOT evidence (fail the spec with a clear message); --verify-on-the-fly honours --camera (house_sim/eval/run.py).
- CI: .github/workflows/ci.yml running the unit suite and a spec sample on Python 3.9 and 3.14, Windows and Linux (no secrets, no paid services); make the suite pass on 3.9 syntax-wise (ast check in a test) and avoid Windows-only assumptions in tests.
- tools/orchestration: an authoring/orchestration helper that RUNS --check itself and records the result (agents never self-certify): tools/orchestration/run_check.py writing a signed-by-hash check log.
YOUR FILES: tools/orchestration/**, tools/fuzz.py, house_sim/tasks/verify.py, house_sim/eval/run.py (verify-on-the-fly camera only), .github/workflows/ci.yml (new), tests/test_gate_tool.py (new), tests/test_verify_records.py (new), docs/EVAL_HARNESS.md (gate section).`

const kernelPrompt = (id, items) => `${ENV(id)}

You are kernel agent **${id}** in phase 1 (stabilise). 16 domain owners fix their own code concurrently; two other kernel agents work on disjoint files.
${items}
Quality bar: production quality, a regression test for every fix that fails before and passes after, deterministic, both perception modes and both camera modes where relevant, perf within docs/design/TIER1_INTEGRATION.md 3.4. Never change behaviour a domain relies on without listing it. Run the full unit suite (\`python tools/orchestration/green_tests.py 5\`) and all authored specs (combined file, \`--check --workers 4\`) at the end and report. YOU MAY EDIT ONLY the files listed above. Return the structured result (id "${id}").`

const integratePrompt = `${ENV('INT')}

Phase 1 just finished: 16 domain agents and K-LEAK, K-FIX, K-GATE changed their files concurrently. Their results are JSON files in ${OUT}/ (read them ALL). You are the INTEGRATOR.
1. Run: \`python tools/orchestration/green_tests.py 5\` (normal + strict); all authored specs in own mode (combined file, --check --workers 4) and the realistic ones with --camera geometric; \`python tools/perception_audit.py 60 80\` (the upgraded auditor); \`python tools/layout_audit.py 30\`; \`python tools/fuzz.py 12 300\` plus the coverage mode; the hardened gate record (see the gate handoff and K-GATE's notes).
2. Fix every failure whose root cause is in non-domain files (kernel, core, generator, tasks, eval, tools, kernel tests, docs except docs/domains) with regression tests; apply precise, correct kernel requests from the domain results.
3. Do NOT edit domain files: write remaining domain-side failures to ${OUT}/breakages.json as [{domain, problem, cause, change}].
4. Write ${OUT}/INT.json (your structured result) and a short ${OUT}/STATUS.md (numbers, what changed, open items).
Return the structured result (id "INT"; requests = breakages).`

const LENSES = [
  { key: 'leaks', text: `LEAKS: re-run EVERY probe script of the review's perception phases (${REVIEWDIR}/p3_leaks_identity_scratch/ and ${REVIEWDIR}/p3_leaks_side_scratch/; adapt paths to run against the LIVE tree ${ROOT}) and the findings list in phases/p3_leaks_identity.md and p3_leaks_side.md (I1-I11, L1-L8, S1): for each, closed or still open (with repro). Then hunt for NEW leaks the fixes introduced or missed, acting only through env.step in realistic mode (room and geometric camera). Also check the upgraded auditor reports each class on a deliberately re-opened copy.` },
  { key: 'defects', text: 'DEFECTS & GATE HONESTY: confirm the 3 real defects are fixed (one-hand drop near the limit, wrapped-glass shards, privacy double count) and not baked into goldens/digests; confirm every adopt_status.json breakage is closed or listed; then try to sneak a regression past the hardened gate (new failing spec, failing test, self-written review code, silent spec edit, skipped check, crashing wrong plan) and report what still gets through; check CI config runs (syntax) and the coverage fuzz numbers.' },
]
const redPrompt = (l) => `${ENV('red_' + l.key)}

You are an independent RED TEAM for phase 1. Integrator status: ${OUT}/STATUS.md and ${OUT}/INT.json; all results in ${OUT}/. Lens: ${l.text}
Do not edit project files (probes in your scratch dir). Report only reproduced findings with repro, owner (kernel file or domain) and severity; list what you confirmed closed. Write your result JSON to ${OUT}/red_${l.key}.json and return it.`

const fix2Prompt = `${ENV('FIX2')}

Phase-1 red-team findings: ${OUT}/red_leaks.json and ${OUT}/red_defects.json (read both fully). Fix every finding owned by non-domain files at the root with regression tests (same file permissions as the integrator: kernel, core, generator, tasks, eval, tools, kernel tests, docs except docs/domains). Append domain-owned findings to ${OUT}/breakages.json. Re-run the full checks of the integrator and write ${OUT}/FIX2.json + update ${OUT}/STATUS.md. Return the structured result (id "FIX2").`

const repairPrompt = (d) => `${ENV('rep_' + d)}

You own the **${d}** domain. Phase 1 left problems attributed to ${d} in ${OUT}/breakages.json (read the entries with domain "${d}"; context in ${OUT}/STATUS.md). Fix each at the root with a regression test; same GREEN checks and file permissions as before: house_sim/systems/${d}/**, house_sim/data/types/${d}.json, house_sim/data/vocab/${d}.json, tests/test_${d}.py, docs/domains/${d}.md, tasks/authored/${d}__examples.json, tasks/authored/${d}__perception.json. Write ${OUT}/rep_${d}.json and return the structured result (id "rep_${d}").`

phase('Fix')
const jobs = [
  () => agent(kernelPrompt('K-LEAK', K_LEAK), { label: 'K-LEAK', phase: 'Fix', schema: RESULT }),
  () => agent(kernelPrompt('K-FIX', K_FIX), { label: 'K-FIX', phase: 'Fix', schema: RESULT }),
  () => agent(kernelPrompt('K-GATE', K_GATE), { label: 'K-GATE', phase: 'Fix', schema: RESULT }),
  ...DOMAINS.map(d => () => agent(domainPrompt(d), { label: `dom:${d}`, phase: 'Fix', schema: RESULT })),
]
const first = (await parallel(jobs)).filter(Boolean)
log(`phase 1 fixes: ${first.length}/${jobs.length} returned`)

phase('Integrate')
const integ = await agent(integratePrompt, { label: 'integrate', phase: 'Integrate', schema: RESULT })

phase('Red team')
const red = (await parallel(LENSES.map(l => () => agent(redPrompt(l), { label: `red:${l.key}`, phase: 'Red team', schema: FIND })))).filter(Boolean)

phase('Fix2')
const fix2 = await agent(fix2Prompt, { label: 'fix2', phase: 'Fix2', schema: RESULT })

phase('Repair')
const domainsWithBreaks = DOMAINS.filter(d => {
  const fromInt = ((integ && integ.requests) || []).some(r => (r.owner || '').includes(d))
  const fromRed = red.some(r => r.findings.some(f => (f.owner || '').includes(d)))
  return fromInt || fromRed
})
log(`repair domains: ${domainsWithBreaks.join(', ') || 'none'}`)
const repairs = (await parallel(domainsWithBreaks.map(d => () => agent(repairPrompt(d), { label: `rep:${d}`, phase: 'Repair', schema: RESULT })))).filter(Boolean)

return {
  first: first.map(r => ({ id: r.id, fixed: r.fixed.length, not_fixed: r.not_fixed.length, tests: r.tests, specs: r.specs })),
  integrate: integ && { tests: integ.tests, specs: integ.specs, not_fixed: integ.not_fixed.length },
  red: red.map(r => ({ lens: r.lens, open: r.findings.length, closed: r.closed.length })),
  fix2: fix2 && { tests: fix2.tests, specs: fix2.specs },
  repairs: repairs.map(r => ({ id: r.id, tests: r.tests, specs: r.specs, not_fixed: r.not_fixed.length })),
}
