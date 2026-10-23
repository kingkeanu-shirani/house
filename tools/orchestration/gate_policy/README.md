# Gate policy files (main session only)

The tier-1 gate (`tools/orchestration/tier1_gate.py`) reads two kinds of policy files from this folder. They are the
main session's: the gate reads only their **committed** version (`git show HEAD:<path>`; in an isolated copy, only
when unchanged since its base commit), so an agent's working-tree edit counts for nothing, and G10 fails any package
that edits them (K-GATE, which defines their format, excepted).

## known_failing.json: the dated allowlist

The gate's absolute rules: every test passes (G1, G1b, G8) and every authored spec passes in its own mode on every
seed (G2-ABS, new specs included). The only exception is an unexpired, well-formed entry here:

```json
{"kind": "test", "test": "tests.test_mind.BudgetTests", "modes": ["normal"], "owner": "PEOPLE-IO",
 "expires": "2026-11-05", "reason": "wall-clock bound under load; moved behind an opt-in flag by then"}
{"kind": "spec", "catalog_id": "T-c49fadcea69182-01", "seeds": "all", "mode": "own", "owner": "cleaning",
 "expires": "2026-10-29", "reason": "wrong plan aborts before its mistake (phase 1 K-GATE); being fixed"}
```

A test entry names a test id, its class or its module (`tests.test_x.Class.test_name` as unittest prints it);
`modes` defaults to every mode. A spec entry's `mode` defaults to `own`. From the day after `expires` the entry
excuses nothing; a malformed entry (no owner, no reason, a date not `YYYY-MM-DD`) is reported and ignored.
`python tools/orchestration/tier1_gate.py policy` prints what the gate reads.

## signoffs/<gate id>.json: the second signature

A verdict change (G2/G3), an edited spec (`spec_hash` changed) or an action the coverage fuzz no longer drives to
success passes only with two signatures: the package's reviewed row (its handoff table, `gate/<ID>/reviewed.json` or
`--reviewed FILE`, bound to the spec files the package owns) and a row of `signoffs/<ID>.json` committed by the main
session, naming the exact new verdict digest, the new spec hash or the action:

```json
{"format": "house-gate-signoff-v1", "gate": "ADOPT_cleaning", "prev": "ADOPT", "signed_by": "main session",
 "date": "2026-10-23",
 "rows": [{"kind": "verdict", "key": "T-...-01", "seed": 0, "mode": "own", "digest": "<16 hex>"},
          {"kind": "edit", "key": "T-...-01", "spec_hash": "<sha256>"},
          {"kind": "coverage", "action": "brew"}]}
```

`python tools/orchestration/tier1_gate.py signoff [PREV] ID --by NAME --out FILE` drafts it from the two records
(every row, with the package's review status, the codes and the change); review each row, delete what you do not
accept, save it here as `<ID>.json` and commit it. A signature names one verdict: if the verdict changes again, it
no longer applies.
