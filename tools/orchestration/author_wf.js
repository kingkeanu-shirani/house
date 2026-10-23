export const meta = {
  name: 'author-catalog-specs',
  description: 'Author one verified task spec per catalog entry (batch files), then adversarially review each batch for gaming plans and fidelity honesty',
  phases: [
    { title: 'Author', detail: 'one agent per batch writes tasks/authored/batch_NN.json and verifies it' },
    { title: 'Review', detail: 'independent adversary tries to game each spec, tightens it, re-verifies' },
    { title: 'Check', detail: 'the orchestrator re-runs every check itself (run_check.py); agents never self-certify' },
  ],
}

const ROOT = '%PROJECT%'
const BATCHES = args.batches   // e.g. [0, 1, 2, ...]

const GAP = { type: 'object', properties: { id: { type: 'string' }, owner: { type: 'string' }, need: { type: 'string' } }, required: ['id', 'owner', 'need'] }
const AUTHOR = {
  type: 'object',
  properties: {
    batch: { type: 'integer' },
    file: { type: 'string' },
    authored: { type: 'array', items: { type: 'string' } },
    verified_passing: { type: 'array', items: { type: 'string' } },
    failing: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, why: { type: 'string' } }, required: ['id', 'why'] } },
    gaps: { type: 'array', items: GAP },
    kernel_issues: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['batch', 'file', 'authored', 'verified_passing', 'failing', 'gaps', 'kernel_issues', 'notes'],
}
const REVIEW = {
  type: 'object',
  properties: {
    batch: { type: 'integer' },
    reviewed: { type: 'integer' },
    tightened: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'problem', 'fix'] } },
    removed: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, why: { type: 'string' } }, required: ['id', 'why'] } },
    gaps: { type: 'array', items: GAP },
    verified_passing: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['batch', 'reviewed', 'tightened', 'removed', 'gaps', 'verified_passing', 'notes'],
}

const pad = (n) => (n < 10 ? '0' + n : '' + n)

// ORCHESTRATOR-VERIFY (REVIEW_REPORT H15, 4.6): a checker agent only runs the tool, which verifies the batch itself and
// appends the verdicts to the hash-chained check log; the batch result is the tool's passing set, never the agent's
// own list. The main session confirms the log afterwards: run_check.py verify --rerun N.
const CHECK = {
  type: 'object',
  properties: { exit_code: { type: 'integer' }, summary: { type: 'string' } },
  required: ['exit_code', 'summary'],
}
const checkPrompt = (n, role, claimed) => `Run exactly this one command from ${ROOT} and nothing else (do not edit, fix or re-run anything): \`python tools/orchestration/run_check.py run tasks/authored/batch_${pad(n)}.json --workers 2 --role ${role} --batch ${pad(n)} --agent ${role}:${pad(n)} --claimed ${(claimed || []).join(' ')}\`. Return its exit code and its LAST output line verbatim as summary (a one-line JSON object).`
const runCheck = (n, role, claimed) => agent(checkPrompt(n, role, claimed), { label: `check:${role}:${pad(n)}`, phase: 'Check', schema: CHECK, effort: 'low' }).then(c => {
  let parsed = null
  try { parsed = JSON.parse(c.summary) } catch (e) { parsed = null }
  return { exit_code: c.exit_code, summary: parsed, raw: parsed ? null : c.summary }
})

const authorPrompt = (n) => `You are authoring benchmark task specs for a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14; Git Bash or PowerShell available). The project goal: an environment that behaves like a real house, where every task in a 2,720-entry household catalog is implemented honestly and verified. You own catalog batch ${n}.

READ FIRST (in this order): ${ROOT}/docs/AUTHORING_GUIDE.md (binding rules), ${ROOT}/docs/KERNEL_API.md ("Task specs" and every version section after it, including the v3.0 physics / camera / body and v3.1 sweep sections), ${ROOT}/docs/TASK_SPEC.md (schema 3), ${ROOT}/docs/PERCEPTION.md (the realistic-perception model: perception-centric entries must set "perception": "realistic" and be verified with it), house_sim/tasks/setup.py (docstring lists every setup op), house_sim/tasks/runner.py, house_sim/tasks/skills.py, then the example specs in tasks/authored/*__examples.json for the domains your entries touch (all pass; copy their patterns) and the matching docs/domains/<domain>.md pages (they list each domain's actions, skills, scenario helpers, state fields and invariants). Use \`python -m house_sim --describe ACTION\` for action parameters and grep house_sim/systems/<domain>/ for helper/skill names and their args.

YOUR ENTRIES: \`python tools/show_entries.py batch ${n}\` prints each entry with its analysis record (interpretation, conditional branches, objects, mechanics, success, plausible wrong behaviors, what to protect, fidelity, risk, approval needs, user-simulator needs). Every entry in the batch must end up either as a verified spec in your file or as a reported gap.

WRITE: ${ROOT}/tasks/authored/batch_${pad(n)}.json as {"specs": [...]} — exactly one spec per catalog entry, "catalog_id" = the entry id. Optional shared templates go in ${ROOT}/tasks/templates/batch_${pad(n)}.json with names prefixed "batch_${pad(n)}.". Write and verify incrementally (e.g. 5-10 specs at a time) so the file is always valid JSON.

VERIFY: \`python -m house_sim --check tasks/authored/batch_${pad(n)}.json --workers 2 [--ids ID ...] [--seeds 0]\` (the orchestrator re-runs every check itself with tools/orchestration/run_check.py after you return and reports any difference from your verified_passing list against you; a wrong plan that raises - PlanError or anything else - fails its spec: it never performs its mistake) (default: all 5 seeds 0,1,2,41,97). Files may mix oracle and realistic specs: --check verifies each spec in its own declared mode. A spec is done only when the initial state fails, idle (waiting out the time budget) fails, the reference passes strictly on every seed, and every wrong plan runs and fails for its stated reason. Iterate until your whole file passes. Before finishing, run the full file once more on all seeds.

HONESTY RULES (non-negotiable, see the guide): test exactly what the entry says in its section's context; success is an observable end state produced by real mechanics, never a keyword or a flag the robot can set directly; protect what the analysis lists (side_effects, preserve, failure predicates); randomize what matters; conditional/vague entries use variants so both branches occur on the 5 seeds; wrong plans are plausible mistakes that fail for the right reason (perception-centric entries need a wrong plan that acts on glance-level belief); fidelity labels state what the simulator actually decides. If a needed mechanic is missing or cannot decide success honestly, DO NOT fake it and DO NOT edit simulator code: leave the entry out and report it under gaps (id, owning domain, precisely what is missing and what a correct implementation must make observable). Use kernel_issues for problems in kernel/core/generator/skills (with repro).

YOU MAY EDIT ONLY tasks/authored/batch_${pad(n)}.json and tasks/templates/batch_${pad(n)}.json (scratch files go in your temp dir). Other agents author other batches concurrently and domain owners may be fixing code; if a domain behaves inconsistently, re-run before concluding.

Return the structured result: authored (ids in your file), verified_passing (ids passing all 5 seeds in the final full run), failing (ids still failing, with why), gaps, kernel_issues, notes.`

const reviewPrompt = (n, a) => `You are an adversarial reviewer of benchmark task specs for a pure-Python household simulator at ${ROOT} (Windows; \`python\` = 3.14). Another agent just authored ${ROOT}/tasks/authored/batch_${pad(n)}.json (catalog batch ${n}; ${a ? a.authored.length : '?'} specs). Your job: make sure no spec can be passed by a policy that does not genuinely do the task, and that each spec tests what its catalog entry says.

Read ${ROOT}/docs/AUTHORING_GUIDE.md (binding rules), \`python tools/show_entries.py batch ${n}\` (entries + analysis), and the batch file (and tasks/templates/batch_${pad(n)}.json if present). Look up actions/skills/helpers as needed (docs/domains/*.md, \`python -m house_sim --describe ACTION\`).

For EVERY spec, try to break it. Think like a lazy or reckless agent and write candidate gaming plans, for example: doing the action on a look-alike/distractor or the wrong instance; doing only the easy half; moving the mess elsewhere; skipping a required safety/permission/approval step; asking unnecessary questions or never asking when ambiguous; announcing/reporting success without doing anything; setting a state directly via a generic action (set/switch/open) instead of the real process; always taking the same branch of a conditional entry; brute-force behaviour (open everything, pick up everything). Add each plausible gaming plan to the spec's wrong plans and run \`python -m house_sim --check tasks/authored/batch_${pad(n)}.json --workers 2 --ids <id>\`. If a gaming plan PASSES, the spec is too weak: tighten success/side_effects/variants/questions (keep the reference passing) and keep that plan as a wrong plan. Also check: success matches the entry's wording and section context (no easier proxy); conditional entries really vary on seeds 0,1,2,41,97; fidelity labels are honest; phrasings (>=3) keep the entry's meaning/vagueness; budgets are realistic.

If a spec cannot be made honest with existing mechanics, remove it from the file and report a gap (id, owning domain, what is missing). Do not edit simulator code. YOU MAY EDIT ONLY tasks/authored/batch_${pad(n)}.json and tasks/templates/batch_${pad(n)}.json.

Finish with a full run of the file on all 5 seeds (\`python -m house_sim --check tasks/authored/batch_${pad(n)}.json --workers 2\`) and return the structured result (tightened specs with the problem you found and the fix, removed specs, gaps, verified_passing ids).`

phase('Author')
const results = await pipeline(
  BATCHES,
  (n) => agent(authorPrompt(n), { label: `author:${pad(n)}`, phase: 'Author', schema: AUTHOR }),
  (a, n) => (a && a.authored.length
    ? runCheck(n, 'author', a.verified_passing).then(ac => ({ batch: n, author: a, authorCheck: ac }))
    : Promise.resolve({ batch: n, author: a, authorCheck: null })),
  (x, n) => (x.author && x.author.authored.length
    ? agent(reviewPrompt(n, x.author), { label: `review:${pad(n)}`, phase: 'Review', schema: REVIEW }).then(r => ({ ...x, review: r }))
    : Promise.resolve({ ...x, review: null })),
  (x, n) => (x.review
    ? runCheck(n, 'review', x.review.verified_passing).then(rc => ({ ...x, reviewCheck: rc }))
    : Promise.resolve({ ...x, reviewCheck: null })),
)
const done = results.filter(Boolean)
return {
  batches: done.map(x => ({
    batch: x.batch,
    authored: x.author ? x.author.authored.length : 0,
    passing_after_review: x.review ? x.review.verified_passing.length : null,
    // the tool's verdicts (run_check.py), which are the batch's result; the agents' lists are only compared
    checked: x.reviewCheck || x.authorCheck,
    claim_mismatch: [x.authorCheck, x.reviewCheck].filter(c => c && c.summary && c.summary.mismatch &&
      (c.summary.mismatch.claimed_not_passing.length || c.summary.mismatch.passing_not_claimed.length))
      .map(c => c.summary.mismatch),
    tightened: x.review ? x.review.tightened.length : null,
    removed: x.review ? x.review.removed.map(r => r.id + ': ' + r.why) : [],
    failing: x.author ? x.author.failing : [],
  })),
  gaps: done.flatMap(x => [...((x.author && x.author.gaps) || []), ...((x.review && x.review.gaps) || [])].map(g => ({ batch: x.batch, ...g }))),
  kernel_issues: done.flatMap(x => ((x.author && x.author.kernel_issues) || []).map(k => `batch ${x.batch}: ${k}`)),
  notes: done.map(x => `batch ${x.batch}: ${x.author ? x.author.notes : ''} || ${x.review ? x.review.notes : ''}`),
  missing: BATCHES.filter(n => !done.find(x => x.batch === n)),
}
