export const meta = {
  name: 'domain-fix-round-4',
  description: 'Each domain owner fixes perception red-team leaks in its code, cross-domain bugs from the perception sweep, test/spec failures after kernel v2.6-v2.8, and adopts the new kernel perception behaviour',
  phases: [{ title: 'Fix', detail: 'one agent per domain, own files only' }],
}

const ROOT = '%PROJECT%'
const DOMAINS = ['plumbing_liquids', 'cleaning', 'safety', 'navigation_robot', 'appliances_cooking', 'admin_inventory', 'electrical',
  'climate', 'mechanisms', 'tools_vehicles', 'textiles_laundry', 'people', 'food', 'manipulation', 'pets', 'outdoor_plants']

const RESULT = {
  type: 'object',
  properties: {
    domain: { type: 'string' },
    fixed: { type: 'array', items: { type: 'string' } },
    not_fixed: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, why: { type: 'string' } }, required: ['item', 'why'] } },
    tests: { type: 'object', properties: { count: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' } }, required: ['count', 'passed', 'strict_passed'] },
    specs: { type: 'object', properties: { total: { type: 'integer' }, passed: { type: 'integer' } }, required: ['total', 'passed'] },
    leak_audit_lines: { type: 'integer' },
    layout_audit_lines: { type: 'integer' },
    kernel_requests: { type: 'array', items: { type: 'string' } },
    new_cross_domain_bugs: { type: 'array', items: { type: 'object', properties: { owner: { type: 'string' }, problem: { type: 'string' } }, required: ['owner', 'problem'] } },
    notes: { type: 'string' },
  },
  required: ['domain', 'fixed', 'not_fixed', 'tests', 'specs', 'leak_audit_lines', 'layout_audit_lines', 'kernel_requests', 'new_cross_domain_bugs', 'notes'],
}

const prompt = (d) => `You own the **${d}** mechanics domain of a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; Git Bash or PowerShell). Goal: a household environment that behaves like a real house, with honest REALISTIC perception (an agent learns only what a camera-and-sensors robot could). Other owners work concurrently on their domains.

READ FIRST: ${ROOT}/docs/KERNEL_API.md final sections "v2.6 perception refinements", "v2.7 perception/layout fixes" and "v2.8 perception hardening (red-team findings)"; ${ROOT}/docs/PERCEPTION.md; your docs/domains/${d}.md.

WORK LIST (do all of it):
1. RED-TEAM LEAKS in your code: ${ROOT}/catalog/analysis/perception_redteam.json key domain."${d}" (if present) -- independently confirmed ways an agent recovers ids, identities or hidden values through your messages, refusals, durations, observers or inspectors. Fix each at the root (generic wording that does not name a hidden identity/value; durations quantized so they do not reveal hidden quantities or identities; estimates drawn once, not per call; percepts consistent across look-alike members). Also scan your own code for the same patterns (messages using labels/types/hidden fields of things the robot has not identified; refusals naming owners or hidden flags; per-call random redraws) and fix those too. The "kernel" key lists kernel/core leaks already fixed in v2.8 -- adapt if your code relied on the old behaviour.
2. CROSS-DOMAIN BUGS reported against you: ${ROOT}/catalog/analysis/round4_bugs.json key "${d}" (if present).
3. ADOPT v2.6-v2.8: env.step in realistic mode no longer returns "evaluation"/"reward" (use env.evaluate() in tests); refusal categories privacy/authority/approval become "permission" for agents; file-level "field_tiers" (incl. "room") for fields you write onto other domains' entities or rooms; ctx.perceived(..., exact=[...]) / details["_perceived"] for instrument readings; derived_from for portions; group "kind"/"candidates"/"label_fields"; knowledge of things the robot carries survives; blank labels stay unidentified; content fields (liquid, contents, product...) are legible only on labelled items. Remove workarounds the kernel now covers.
4. GREEN: \`python -m unittest tests/test_${d}.py\` normally and with HOUSE_SIM_STRICT=1; \`python -m house_sim --check tasks/authored/${d}__examples.json --workers 2\` and (if it exists) \`python -m house_sim --check tasks/authored/${d}__perception.json --workers 2\` must pass 100% on all 5 seeds. Known failures to fix if yours: cleaning test_floor_soil_and_waste_helpers_bind_reachable_targets ("cleaning.waste: no room for glass_jar"); textiles test_every_washer_gets_a_dryer_unless_a_stack_would_hide_a_wall_mount...; specs cleaning T-7e93003b0dcefa-01 (seed 97), safety X-bc4e8d373e62c5-01 (seed 0) and T-70a9e6a7a83f64-01 (seed 97).
5. AUDITS: \`python tools/perception_audit.py 30 80 --verbose\` -- no lines attributed to your types/observers/actions; \`python tools/layout_audit.py 20 --domain ${d} --verbose\` -- no lines attributed to your furnishers (report others' in new_cross_domain_bugs).
6. Update docs/domains/${d}.md ("What the robot can perceive" section and anything else that changed).

YOU MAY EDIT ONLY: house_sim/systems/${d}/**, house_sim/data/types/${d}.json, tests/test_${d}.py, docs/domains/${d}.md, tasks/authored/${d}__examples.json, tasks/authored/${d}__perception.json. Never edit kernel/core/generator/tasks/tools/eval/other domains; report kernel needs in kernel_requests (precise) and others' bugs in new_cross_domain_bugs. Keep your package importable after every edit (do not set HOUSE_SIM_DOMAINS).

Return the structured result.`

phase('Fix')
const results = await parallel(DOMAINS.map(d => () => agent(prompt(d), { label: `fix4:${d}`, phase: 'Fix', schema: RESULT })))
const done = results.filter(Boolean)
return {
  summary: done.map(r => ({ domain: r.domain, fixed: r.fixed.length, not_fixed: r.not_fixed, tests: r.tests, specs: r.specs, leaks: r.leak_audit_lines, layout: r.layout_audit_lines })),
  kernel_requests: done.flatMap(r => r.kernel_requests.map(k => `${r.domain}: ${k}`)),
  new_cross_domain_bugs: done.flatMap(r => r.new_cross_domain_bugs.map(b => ({ reporter: r.domain, ...b }))),
  notes: done.map(r => `${r.domain}: ${r.notes}`),
  missing: DOMAINS.filter((d, i) => !results[i]),
}
