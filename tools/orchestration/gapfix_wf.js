export const meta = {
  name: 'gap-fix-round',
  description: 'Domain owners implement the mechanics spec authors reported missing (gaps), with tests and docs, keeping existing specs green',
  phases: [{ title: 'Implement', detail: 'one agent per domain with gaps' }],
}

const ROOT = '%PROJECT%'
const DOMAINS = args.domains   // domains that have gaps in catalog/analysis/gaps_open.json

const RESULT = {
  type: 'object',
  properties: {
    domain: { type: 'string' },
    implemented: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, how: { type: 'string' } }, required: ['id', 'how'] } },
    not_implemented: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, why: { type: 'string' } }, required: ['id', 'why'] } },
    tests: { type: 'object', properties: { count: { type: 'integer' }, passed: { type: 'boolean' }, strict_passed: { type: 'boolean' } }, required: ['count', 'passed', 'strict_passed'] },
    specs_rechecked: { type: 'object', properties: { files: { type: 'array', items: { type: 'string' } }, all_pass: { type: 'boolean' } }, required: ['files', 'all_pass'] },
    new_cross_domain_bugs: { type: 'array', items: { type: 'object', properties: { owner: { type: 'string' }, problem: { type: 'string' } }, required: ['owner', 'problem'] } },
    kernel_requests: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['domain', 'implemented', 'not_implemented', 'tests', 'specs_rechecked', 'new_cross_domain_bugs', 'kernel_requests', 'notes'],
}

const prompt = (d) => `You own the **${d}** mechanics domain of a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; Git Bash or PowerShell). Project goal: a household environment that behaves like a real house, with every catalog task implemented honestly. Spec authors could not implement some catalog entries because mechanics were missing; those are your gaps. Other owners work concurrently on their domains.

READ: ${ROOT}/docs/KERNEL_API.md (incl. "v2.3"), ${ROOT}/docs/CONVENTIONS.md, ${ROOT}/docs/AUTHORING_GUIDE.md, your ${ROOT}/docs/domains/${d}.md and code.

INPUT: ${ROOT}/catalog/analysis/gaps_open.json key "${d}": gap reports {id, batch, need}. \`python tools/show_entries.py ids <id ...>\` shows each entry with its analysis record (mechanics, success, wrong behaviors, protect...).

FOR EACH GAP: implement the missing mechanics so that a spec author can build an honest spec: state that changes only through real processes, actions/skills/scenario helpers with clear parameters, invariants for harms, observation of what a robot could perceive. Prefer general mechanisms that serve many entries over one-off hacks. Add unit tests (tests/test_${d}.py) proving the mechanic decides success and that the plausible wrong behaviors from the analysis are caught. Document new actions/helpers/fields in docs/domains/${d}.md (authors read it). If a gap is not genuinely yours or needs kernel changes, put it in not_implemented / kernel_requests / new_cross_domain_bugs with specifics. If a requested mechanic is unrealistic or the entry is better served by existing mechanics, say how in not_implemented (an author will use your note).

KEEP GREEN: \`python -m unittest tests/test_${d}.py\` (normal and HOUSE_SIM_STRICT=1); \`python -m house_sim --check tasks/authored/${d}__examples.json --workers 2\`; and every authored batch file that uses your domain must still pass — find them with \`grep -l "${d}" tasks/authored/batch_*.json\` and re-check those you could affect (\`python -m house_sim --check FILE --workers 2\`); report the files and whether all pass. Layout audit for your furnishers must stay clean (\`python tools/layout_audit.py 10 --domain ${d}\`).

YOU MAY EDIT ONLY: house_sim/systems/${d}/**, house_sim/data/types/${d}.json, tests/test_${d}.py, docs/domains/${d}.md, tasks/authored/${d}__examples.json. Never edit kernel/core/generator/tasks/tools/other domains or batch spec files.

Return the structured result.`

phase('Implement')
const results = await parallel(DOMAINS.map(d => () => agent(prompt(d), { label: `gap:${d}`, phase: 'Implement', schema: RESULT })))
const done = results.filter(Boolean)
return {
  summary: done.map(r => ({ domain: r.domain, implemented: r.implemented.length, not_implemented: r.not_implemented, tests: r.tests, specs: r.specs_rechecked })),
  implemented: done.flatMap(r => r.implemented.map(x => ({ domain: r.domain, ...x }))),
  not_implemented: done.flatMap(r => r.not_implemented.map(x => ({ domain: r.domain, ...x }))),
  new_cross_domain_bugs: done.flatMap(r => r.new_cross_domain_bugs.map(b => ({ reporter: r.domain, ...b }))),
  kernel_requests: done.flatMap(r => r.kernel_requests.map(k => `${r.domain}: ${k}`)),
  missing: DOMAINS.filter((d, i) => !results[i]),
}
