# Authoring task specs for catalog entries

One spec per catalog entry, in `tasks/authored/<file>.json` as `{"specs": [...]}`. Read first:
`docs/KERNEL_API.md`, `docs/CONVENTIONS.md`, `house_sim/tasks/spec.py`, `house_sim/tasks/setup.py`,
`house_sim/tasks/runner.py`, `house_sim/tasks/skills.py`, the relevant `docs/domains/*.md`, and the domain example
files `tasks/authored/*__examples.json` (all passing; copy their patterns).

Tools:
- `python tools/show_entries.py batch N` — your entries with their analysis records (interpretation, mechanics,
  success, plausible-wrong behaviors, protected side effects, conditional branches, user-sim needs).
- `python -m house_sim --check FILE --workers 2 [--ids ID ...] [--seeds 0]` — verify (all 5 seeds by default).
- `python tools/show_house.py SEED [room ...]` — ASCII room grids; `python -m house_sim --describe ACTION`.

## Non-negotiable rules

1. **Test what the entry says, in its section's context.** "Clean sink" under Bathrooms is a bathroom sink with soap
   scum/toothpaste; under Kitchen cleaning it is food/grease. Never substitute an easier proxy. If the needed mechanic
   does not exist, do not fake it: leave the entry out and report the gap.
2. **Success = observable end state produced by real mechanics** (locations, soil, wetness, temperatures, settings,
   cycle states, reports matching ground truth, speech to the right person, approvals obtained, memory entries...).
   Never a keyword, never a flag that the robot can set directly.
3. **Don't make it worse:** add `side_effects` (`no_moves_except`, `no_damage`, `no_new_soil_except`,
   `unchanged_fields`), `preserve` paths and `failure` predicates for what the entry's analysis lists under protect.
   Global harms (damage, injury, spills, collisions, falls, breakages, fire, flood, privacy/safety/authority
   violations...) already fail every task.
4. **Randomize what matters** with setup ops: which object, where, how dirty/full/hot, distractors and look-alikes,
   who asks. Conditional entries ("when appropriate", "if needed", "safely", "when instructed") MUST use `variants`
   so that both branches occur on the 5 verification seeds (seeds 0,1,2,41,97 -> variant = seed % len(variants));
   a policy that always does (or never does) the action must fail some seed.
5. **Wrong plans (1-3) must be plausible mistakes** from the analysis `wrong` list (wrong item, wrong method/tool,
   wrong order, skipped step, unsafe shortcut, acting without permission, moving the mess elsewhere) and must fail for
   that reason. A wrong plan that crashes or does nothing is not acceptable evidence.
6. **Idle must fail:** waiting out the whole time budget must not satisfy the goal (events and systems keep running).
7. **Reference plan** uses skills/actions through `env.step`; it must pass on all 5 seeds without any refused step.
   Keep budgets realistic (`budget.steps`, `budget.time_s`).
8. **People and approvals:** risk-3 work requires the approval/presence mechanics; vague or preference-dependent
   commands put hidden intent in the user simulator (`user_sim` op) and score questions (`questions`:
   `min_necessary`, `max_unnecessary`). Asking when not needed must cost; guessing when ambiguous must fail some seed.
9. **Subjective entries** ("look nice", "comfortable", "presentable") use a `rubric` of weighted programmatic proxies
   with a threshold, calibrated so a sloppy-but-plausible plan fails and the reference passes.
10. **Phrasings:** `instruction` plus at least 3 `phrasings` (original wording, a natural variant, a casual variant;
    for vague/relative commands keep the vagueness).
11. Set `risk_level` (0-3), `fidelity` (what the verified mechanics actually decide: "S", "G" or "P-approx") and
    `target_fidelity` (from the analysis). Be honest: fidelity is what the simulator checks, not what the real skill
    needs.

## Special kinds

- **primitive** entries (action vocabulary): a micro-scenario where the verb is required and correct use is checked;
  wrong plans use a near-miss verb/parameter. Cognitive primitives: Plan/Sequence -> order constraints via
  `milestones` + `order`; Prioritize -> `deadlines` or order by urgency; Compare/Categorize/Identify/Recognize/Read/
  Count/Measure/Weigh -> `report` of a ground-truth value that must be discovered; Monitor/Detect -> a standing order or
  timely report after an event; Verify task completion -> a first attempt that silently fails (e.g. door bounces open)
  so success needs inspect + redo; Ask for clarification -> question scoring; Wait/Stop/Resume -> timing and stop
  semantics.
- **variation** entries (randomization axes): variants spanning the axis values where each value changes what the robot
  must do (e.g. door-handle types need different actions; floor materials need different cleaning tools; children
  present changes safety rules). A policy that ignores the axis must fail at least one seed.
- **adaptability** entries (vague/judgment commands): hidden intent in the user simulator, inspection-driven state,
  rubrics where subjective; success must depend on interpreting the situation correctly across variants.
- **appendix** entries (compound paragraphs): every clause must be exercised by at least one variant; combine several
  clauses per episode when there are more clauses than seeds allow.
- The INCOMPLETE fragment ("intentionally left out, and where everything belongs.") is implemented against its
  documented reconstruction: tidy items to where they belong while leaving intentionally-left-out items in place.

## Templates

Near-duplicate entries (e.g. the same chore in several sections) may share a template in
`tasks/templates/<your_file>.json` (`{"templates": {"<prefix>.name": {... "{{param}}" ...}}}`, names prefixed with your
file stem) and use `"extends"` + `"params"`; each entry still gets its own spec, context and phrasings.

## Setup placement notes (v2.3)

- `spawn floor` never cuts a hazard-free route or stands in a door swing; to stage a deliberate obstruction (e.g.
  "clear the toys blocking the hallway") pass `"obstruct": true` and usually an explicit `"cell"`.
- `person`/`pet` accept `"near": "<entity id or [x, y]>"`; they are never placed in doorways or chokepoints.
- Setup failures raise SpecError with the reason: if a helper needs a room the generated house may lack, add
  `house.require` (e.g. `["garage"]`, `["laundry_room"]`, `["patio"]`).
- `python tools/show_house.py SEED room ...` shows the grid; `python tools/layout_audit.py` checks generator health.

## Gaps

If an entry needs a mechanic that does not exist (or exists but cannot decide success honestly), do not fake it
and do not edit simulator code. Leave the entry out of your file and report a gap: the entry id, the owning domain
(from the analysis record), and precisely what is missing (state fields, verbs, systems, scenario helpers), with
what a correct implementation must make observable. Owners implement gaps between authoring waves.

## Perception-centric entries (realistic mode)

Entries where perceiving IS the skill must set `"perception": "realistic"` (see docs/PERCEPTION.md): Identify /
Recognize / Detect / Inspect / Compare / Count / Measure / Weigh / Read primitives, every "Check / Verify / Detect /
Report / Identify ..." entry, judging-state entries ("Is this still good?", "Is it dry yet?", "Is that pan still
hot?"), find/search entries with look-alikes, and anything about medicines or labels. Then:

- Plans still name true ids (the runner translates them to handles); the env shows the agent only perceived values.
- The reference must gather the information with real actions (approach + inspect / read / a measuring tool / ask)
  before acting or reporting; the verifier fails it if a value it reports never appeared in what it observed.
- At least one wrong plan must act on glance-level belief (pick the look-alike, report a bucket where a measurement
  is needed, trust a dry-looking surface, skip reading the label) and fail on some seed.
- Use look-alike groups and distractors (setup `looks_like`, label_text, novel objects) so a policy that does not
  look properly fails.
- Other entries stay in oracle mode (no field needed).
