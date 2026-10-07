# Task specification

`tasks/specs.jsonl` contains one requirement record per catalog ID. A supported
record names a family, parameters, three command phrasings, a seeded generator,
predicate template, risk triage, budgets, target fidelity and current fidelity.
Unsupported records explicitly have no generator or predicate template.

`house_sim.scenarios.build(spec, seed)` materializes a concrete world and task.
Fully expanded sample bundles are in `tasks/examples/`; load them with
`py house_env.py --scenario tasks/examples/clean.json`.

A concrete task has this form:

```json
{
  "schema_version": 1,
  "catalog_id": "custom-example",
  "instruction": "Put the cup in the sink without moving the book.",
  "success": [{"path":"entities.cup.location","op":"eq","value":"sink"}],
  "failure": [{"path":"stats.damage","op":"gt","value":0}],
  "preserve": ["entities.book_1.location"],
  "step_budget": 100,
  "time_budget_s": 600
}
```

Paths use dot-separated dictionary keys. Atomic operators are `eq`, `ne`, `gt`,
`gte`, `lt`, `lte`, `in`, `contains`, and `exists`. Logical combinations are
`{"all":[...]}`, `{"any":[...]}` and `{"not":...}`. No Python evaluation occurs.
Missing paths fail ordinary predicates. All top-level success predicates must
hold; any failure predicate or changed protected property defeats success.

Room/object IDs use lowercase letters, digits and underscores. `hand` is
reserved. Structural cycles, disconnected layouts, invalid references and
invalid supported numeric fields are rejected before a run. Starting layouts
use schema version 1 for compatibility with the supplied source.

Catalog IDs hash kind, section and normalized text, with an occurrence suffix.
Duplicate wording remains separate, linked by a duplicate-group hash. Exact
source lines and byte spans remain in the normalized catalog; raw files preserve
the original bytes. Fused headings are normalized without rewriting the source.
The incomplete adaptability fragment is BLOCKED rather than silently completed.

Risk levels in requirement records are conservative keyword triage, not a
certified risk assessment. Runtime guards use actual object/context properties.
Rows whose target is G or P can be verified only as S in this release; the target
column does not assert an implemented fidelity upgrade.
