export const meta = {
  name: 'tier1-adoption-round',
  description: 'Tier-1 adoption: 16 domain owners adopt physics, camera, robot body and the sweep (section 6 follow-ups, law readiness, geometric-camera and gen-2 re-verification, residual items from rounds 5/B/C) while two kernel agents finish the kernel follow-ups; then an integration gate',
  phases: [
    { title: 'Adopt', detail: '16 domains + 2 kernel follow-up agents, own files only' },
    { title: 'Integrate', detail: 'merged gate, kernel-side fixes, domain breakage list' },
  ],
}

const ROOT = '%PROJECT%'
const SCRATCH = '%SCRATCH%'
const ENV = (id) => `Project: a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; code stays Python 3.9-compatible; stdlib only; deterministic and replayable). The user's goal: "a real house slammed into code with no flaws" - a benchmark/training environment for household robot agents; the user asked for HIGH-QUALITY foundations before the remaining 2,478 catalog tasks are authored. Tier-1 (physics, geometric camera, robot body, the sweep: time/standing orders/temporal evaluation/phases, questions/reports/memory/requests, generator gen 2) has landed in the kernel. Git repo: never commit, push, branch, stash or reset. No network calls. Keep files LF (open(p, 'w', newline='\\n')). Write helper scripts with a file tool (bash heredocs mangle backslashes). Use ONLY your private scratch dir ${SCRATCH}/ad_${id}/. Match the surrounding code style. Other owners edit their own files concurrently; when a test or gate result looks inconsistent, re-run before concluding.`
const DOCS = `Read first: ${ROOT}/docs/design/TIER1_INTEGRATION.md (section 1 contracts, section 6 domain follow-ups - including the "Every domain also" list - section 3 gate), ${ROOT}/docs/design/TIER1_DECISIONS.md, docs/KERNEL_API.md (v3.0 physics/camera/body, v3.1 sweep sections), docs/PHYSICS.md, docs/PERCEPTION.md, docs/TASK_SPEC.md (schema 3), docs/DIALOGUE.md, docs/GENERATOR.md, docs/AUTHORING_GUIDE.md, and the package handoffs ${ROOT}/catalog/analysis/tier1/handoff_*.md. Gate usage: catalog/analysis/tier1/handoff_TIER1-GATE.md (use --shared-tree: other owners change files at the same time).`

const DOMAINS = ['navigation_robot', 'manipulation', 'people', 'safety', 'cleaning', 'plumbing_liquids', 'appliances_cooking', 'food',
  'textiles_laundry', 'admin_inventory', 'electrical', 'climate', 'mechanisms', 'tools_vehicles', 'outdoor_plants', 'pets']

const RESULT = {
  type: 'object',
  properties: {
    owner: { type: 'string' },
    done: { type: 'array', items: { type: 'string' } },
    not_done: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, why: { type: 'string' } }, required: ['item', 'why'] } },
    laws_ready: { type: 'array', items: { type: 'string' } },
    laws_not_ready: { type: 'array', items: { type: 'object', properties: { law: { type: 'string' }, why: { type: 'string' } }, required: ['law', 'why'] } },
    specs: { type: 'object', properties: { total: { type: 'integer' }, own_mode_pass: { type: 'integer' }, geometric_pass: { type: 'integer' }, gen2_pass: { type: 'integer' }, gen2_set: { type: 'integer' } }, required: ['total', 'own_mode_pass', 'geometric_pass', 'gen2_pass', 'gen2_set'] },
    tests: { type: 'object', properties: { count: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' }, isolated_passed: { type: 'boolean' } }, required: ['count', 'passed', 'strict_passed', 'isolated_passed'] },
    kernel_requests: { type: 'array', items: { type: 'string' } },
    cross_domain_bugs: { type: 'array', items: { type: 'object', properties: { owner: { type: 'string' }, problem: { type: 'string' } }, required: ['owner', 'problem'] } },
    notes: { type: 'string' },
  },
  required: ['owner', 'done', 'not_done', 'laws_ready', 'laws_not_ready', 'specs', 'tests', 'kernel_requests', 'cross_domain_bugs', 'notes'],
}

const COMPLETED = ['navigation_robot', 'manipulation', 'people', 'safety', 'cleaning', 'plumbing_liquids']
const PARTIAL = ['admin_inventory', 'appliances_cooking', 'electrical', 'food', 'textiles_laundry', 'climate']
const GATE_NOTE = '\n\nGATE USAGE (fixed in phase 1, see tools/orchestration/README.md "Gate hardening"): record first, then compare: `python tools/orchestration/tier1_gate.py record --id ADOPT_<domain> --package ADOPT:<domain> --shared-tree` (or the quick form with `--only G0,G2,G3,G10 --spec-files \'<domain>__*\'`, which ends INCOMPLETE and is never a hand-over), then `python tools/orchestration/tier1_gate.py compare --package ADOPT:<domain> --shared-tree`. The gate is absolute now: every spec passes in its own mode and every test passes (or is on the main session\'s dated allowlist), and every verdict change or spec edit needs your reviewed row AND the main session\'s signature - list the rows you need in your handoff; never write tools/orchestration/gate_policy/.'
const extraNote = (d) => {
  if (COMPLETED.includes(d)) return ''
  let note = '\n\nRESUMED RUN: an earlier run of this round was interrupted by a usage limit.'
  if (PARTIAL.includes(d)) note += ' Its partial edits to your files are ALREADY IN THE TREE (an exact backup is archive/partial_adopt_' + d + '.patch). Review them first (git diff -- your files): keep what is correct and complete, fix or finish what is half done, and do not redo finished work.'
  else note += ' It never reached your domain, so start fresh.'
  note += ' Six domains (navigation_robot, manipulation, people, safety, cleaning, plumbing_liquids) already finished their adoption and are committed; their handoff notes are in their docs/domains/*.md "tier-1" sections.'
  return note + GATE_NOTE
}
const domainPrompt = (d) => `${ENV(d)}

You own the **${d}** mechanics domain (house_sim/systems/${d}/). This is the tier-1 ADOPTION round for your domain. ${DOCS}${extraNote(d)}

WORK LIST (all of it; quality over speed):
1. Your section 6 block in TIER1_INTEGRATION.md ("**${d}**") and the "Every domain also" list: physics adoption protocol (physics 10.3: route your physics through the kernel functions, guard legacy code with lawbook.active, register hooks, update tests that pin old constants, fix the type data \`python tools/physics_audit.py --data\` attributes to ${d}, then decide per law whether you are "ready to flip" - verify by running your tests and specs with the law forced on (lawbook.forced) - and report laws_ready / laws_not_ready); camera items ("view" blocks, camera.visual_event, camera.in_frame gating, room mode identical); body items (capability limits via the body, sensors via body.set_sensors, ctx.motion/require_mobile for verbs that move the robot, timing="commanded" where a duration is commanded, fault writes via body_faults); sweep items (notices via w.notice, ask answerer routes and report vocabulary, exact idle probes on heavy systems, gen-2 furnishing of your types, the type requests addressed to you).
2. Residual items for ${d} in: ${ROOT}/catalog/analysis/round5_results.json, catalog/analysis/tier1/stageB_open.json and stageC_open.json (search for "${d}" in owner/package/text fields), the 5 still-open stage-B review items if they name your files, catalog/analysis/round4_results.json not_fixed items still real today, and the LookalikeWarning printed at registry load for groups you own (align members or set group "color"/"size_m").
3. Tests that fail when your domain is loaded alone (HOUSE_SIM_DOMAINS=core,${d}): add the missing cross-domain skip guards (A0 handoff section 9 lists them).
4. Specs (tasks/authored/${d}__examples.json and ${d}__perception.json): all pass in their own mode on all 5 seeds; your realistic specs ("perception": "realistic") also pass with the geometric camera (\`--check FILE --camera geometric\`; reference plans become camera-aware: approach/face/look/peek as a real robot would; add a camera wrong plan where looking IS the skill) - geometric becomes the default for realistic perception after this round; re-verify every spec with \`--gen 2\` and set "house": {"gen": 2} on those that pass honestly (fix the ones that should); migrate your privileged-wait specs to schema-3 waits (M-WAITS) if you have any.
5. Update docs/domains/${d}.md (what changed, physics/camera/body/sweep integration, what the robot perceives).

GREEN before returning: \`python -m unittest tests/test_${d}.py\` normal, HOUSE_SIM_STRICT=1 and HOUSE_SIM_DOMAINS=core,${d}; your spec files in own mode (5 seeds), realistic ones with --camera geometric, gen 2 where set; \`python tools/perception_audit.py 20 80 --verbose\` and \`python tools/layout_audit.py 20 --domain ${d}\` with no lines attributed to ${d}; \`python tools/orchestration/tier1_gate.py record --id ADOPT_${d} --package ADOPT:${d} --shared-tree\` then \`python tools/orchestration/tier1_gate.py compare --package ADOPT:${d} --shared-tree\` with only reviewed differences your work requires (list them in notes and in your handoff table). Also write your full structured result as JSON to ${ROOT}/catalog/analysis/tier1/adopt/${d}.json (the integrator reads the files, not a truncated summary).

YOU MAY EDIT ONLY: house_sim/systems/${d}/**, house_sim/data/types/${d}.json, tests/test_${d}.py, docs/domains/${d}.md, tasks/authored/${d}__examples.json, tasks/authored/${d}__perception.json. Kernel needs go to kernel_requests (precise, with repro); other domains' bugs to cross_domain_bugs. Return the structured result.`

const kernelPrompt = (id, items) => `${ENV(id)}

You are kernel follow-up agent **${id}** in the tier-1 adoption round (16 domain owners adopt the new layers concurrently on their own files). ${DOCS}

Your items:
${items}

Quality bar: production quality, regression tests for every change, deterministic, both perception modes and both camera modes where relevant, perf budgets of TIER1_INTEGRATION 3.4. YOU MAY EDIT ONLY non-domain files (house_sim/kernel/**, house_sim/systems/core.py, house_sim/tasks/**, house_sim/eval/**, house_sim/generator/**, house_sim/data/types/core.json, house_sim/data/physics/**, house_sim/data/robot/**, house_sim/audit.py, house_sim/cli.py, house_sim/agent.py, tools/**, tests/test_kernel*.py, tests/test_perception.py, tests/test_physics*.py, tests/test_camera*.py, tests/test_body*.py, tests/test_sight.py, tests/test_eval_*.py, tests/test_timeline.py, tests/test_monitor.py, tests/test_phases.py, tests/test_clock.py, tests/test_predicates_v3.py, tests/test_dialogue.py, tests/test_reports.py, tests/test_mind.py, tests/test_requests_obs.py, tests/test_world_savepoint.py, tests/test_generator*.py, docs/** except docs/domains/**). The other kernel follow-up agent edits files listed in its own items; coordinate by NOT editing a file named only in the other agent's list (K-CAM owns kernel/sight.py, kernel/camera.py, kernel/perception.py, kernel/realism.py, kernel/timeline.py; K-EVAL owns house_sim/eval/**, house_sim/audit.py, house_sim/tasks/verify.py, kernel/predicates.py, kernel/engine.py, kernel/mind.py, tools/**). Never change behaviour a domain relies on without a reviewed difference; when unsure, add, don't change. Run the gate (\`python tools/orchestration/tier1_gate.py record --id ${id} --package ${id} --shared-tree\`, then \`compare --package ${id} --shared-tree\`), write ${ROOT}/catalog/analysis/tier1/handoff_${id}.md and your full structured result as JSON to ${ROOT}/catalog/analysis/tier1/adopt/${id}.json, and return the structured result (owner "${id}"; laws_* and specs may be empty/zero).`

const K_CAM = `- Camera phase 3 (TIER1_CAMERA): doorway and window apertures in sight.py (aperture viewers, doorway_views) and doorway captures with privacy policy masking in camera.py; near_connection controls actionable through wrist access must be listed consistently in the geometric observation (CAM-RUNTIME not_done).
- camera.watch per-substep cost with people in the room (0.15-0.19 ms vs a 0.05 ms budget; STAGE-B-FIX C9 still_open): reach the budget without changing results (exact memoization, cheaper sampling with a proven bound, or a justified documented budget change).
- The open-door leaf occluder (B3-MERGE not_done; the stage-B review fixed part of it - re-check): an open hinged door must not block most of a room from the doorway cell.
- timeline.perceivable / person_enters / person_leaves must be light-, imaging- and geometric-camera-aware in realistic perception (C-MERGE request; check whether the stage-C fix already did it, finish it if not).
- The SearchAgent in geometric mode on test_kernel's cup-in-a-cabinet houses and seed 7 realistic (CAM-RENDER/B3-MERGE not_done).
- Visitor recognition and mirrors only if cheap (camera phase 4 optional) - otherwise document as deferred in docs/KNOWN_LIMITS.md.
- RESUMED RUN: your previous run was interrupted (the session ended). Partial edits you made may already be in the tree in your files (kernel/sight.py, camera.py, perception.py, realism.py, timeline.py and their tests). Review them first (git diff -- those files): keep what is correct, finish what is half done, do not redo finished work. All 16 domains and K-EVAL have finished; read their handoffs/notes.`
const K_EVAL = `- audit.verify(gen=) so \`python -m house_sim --verify --gen 2\` works (TIME-EVAL request 3).
- Viewer: read the episode's monitor record for milestones and show phase_done history entries (TIME-EVAL request 4); dataset export of multi-phase episodes (request 7).
- verify.py: run dialogue.lint_facts / lint_reference / lint_plan for schema-3 specs and add reports.perceivability_gaps to the realistic perceivability check (PEOPLE-IO request 2); predicates._report_world bounds the legacy reports store or passes after/before_s through to reports.match (request 3); engine.known_map() becomes a view of env.mind.map (request 4; navigation_robot adopts it concurrently - keep env._known_map working as a compatibility alias until it does).
- Compact replays: replays are about 3 MB per episode (demo replays total 226 MB, so demos/replays/ is git-ignored). Design and implement a compact replay format (e.g. initial config + seed + command log + periodic state digests, with full states reconstructed by deterministic re-simulation; or gzip), keep loading old replays, update the viewer/leaderboard/dataset consumers and docs; target well under 200 KB per typical episode. Then decide whether demos/replays can be committed again (if yes, remove the .gitignore entry and restore "demos/replays/T-x.json" to the not-ignored list in tests/test_eval_splits.py).
- tools/body_audit.py takes hands from forces.lift_plan and capacity from body.capacity_kg(..., believed=) and reports overload refusals, drops and energy per task (STAGE-B-FIX B9).
- Gate tool fixes (C1 requests): spec chunks written without sort_keys; G9 passes --out to physics_audit (nothing written to %TEMP%/house_ai_tier1); isolated copies create tasks/templates and house_sim/data/actions; add coverage.csv, tasks/specs.jsonl, tasks/id_to_family.jsonl to the B-CLOSE/C-CLOSE file lists; add an ADOPT:<domain> package family if missing.
- Gate tool (adoption-round findings): compare must restrict the previous gate to the current record's --spec-files; record --shared-tree must attribute concurrently running ADOPT:<domain> packages; accept --shared-tree on compare or document the supported form in the gate handoff.
- tools/orchestration/regen_leaderboard.sh: fix the \`cd %PROJECT%\` line so it runs from the project root wherever it lives.
- Prototype-era reports (reports/unit_tests.txt, unit_test_summary.json, cli_checks.json, package_checks.json, package_build.txt, compilation_summary.json, from 2026-10-07): delete them if nothing current reads them (grep), or regenerate them from current tools; keep reports/verification.jsonl and verification_run.json current.`

const integrate = (results) => `${ENV('ADOPT_int')}

The tier-1 adoption round just finished: 16 domain owners and two kernel follow-up agents (K-CAM, K-EVAL) changed their files concurrently. Their full reports are files (never a truncated inline summary): ${results.map(r => `${ROOT}/catalog/analysis/tier1/adopt/${String(r.owner).replace('ADOPT:', '')}.json`).join(', ')}; read every one of them. Owners that returned: ${results.map(r => r.owner).join(', ')}.
${DOCS}

You are the ADOPTION INTEGRATOR. 1) Run the full gate on the merged tree (\`python tools/orchestration/tier1_gate.py record --id ADOPT ...\` then compare against C1; see the gate handoff), plus \`--check\` of every authored spec file in own mode, realistic files with --camera geometric, gen 2 where set. 2) Fix every failure whose cause is in non-domain files (kernel, core, tasks, eval, generator, tools, kernel tests, docs except docs/domains), including the domains' kernel_requests that are precise and correct (with regression tests). 3) Do NOT edit domain files: list domain-side failures and still-valid cross-domain bugs as breakages (domain, problem, cause, what to change). 4) Write ${ROOT}/catalog/analysis/tier1/adopt_status.json with: per-law readiness (which domains are ready / not ready and why), per-domain spec counts (own/geometric/gen2), the breakage list, and the kernel requests you did or did not apply. Return the structured result (owner "ADOPT-INTEGRATE"; put breakages in cross_domain_bugs with owner = the domain).`

phase('Adopt')
const jobs = [
  ...DOMAINS.map(d => () => agent(domainPrompt(d), { label: `adopt:${d}`, phase: 'Adopt', schema: RESULT })),
  () => agent(kernelPrompt('K-CAM', K_CAM), { label: 'adopt:K-CAM', phase: 'Adopt', schema: RESULT }),
  () => agent(kernelPrompt('K-EVAL', K_EVAL), { label: 'adopt:K-EVAL', phase: 'Adopt', schema: RESULT }),
]
const results = (await parallel(jobs)).filter(Boolean)
log(`adoption: ${results.length} of ${jobs.length} returned`)
phase('Integrate')
const integ = await agent(integrate(results), { label: 'adopt:integrate', phase: 'Integrate', schema: RESULT })
return {
  summary: results.map(r => ({ owner: r.owner, laws_ready: r.laws_ready, laws_not_ready: r.laws_not_ready.map(x => x.law), specs: r.specs, tests: r.tests, not_done: r.not_done.length })),
  kernel_requests: results.flatMap(r => r.kernel_requests.map(k => `${r.owner}: ${k}`)),
  cross_domain_bugs: results.flatMap(r => r.cross_domain_bugs.map(b => ({ reporter: r.owner, ...b }))),
  integrate: integ,
  missing: [...DOMAINS, 'K-CAM', 'K-EVAL'].filter(o => !results.find(r => r.owner === o || r.owner === `ADOPT:${o}`)),
}
