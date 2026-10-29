# Gate policy files (main session only)

The tier-1 gate (`tools/orchestration/tier1_gate.py`) reads four kinds of policy files from this folder. They are the
main session's: the gate reads only their **committed** version (`git show HEAD:<path>` of the project the running gate
script belongs to; never an isolated copy's, never `--root`'s), so an agent's working-tree edit counts for nothing, and
G10 fails any package that edits them (a policy package -- the gate tool's, which defines their format, and the main
session's merged records -- excepted; the files are protected paths, so `--shared-tree` never attributes them to a
peer). `python tools/orchestration/tier1_gate.py policy` prints what the gate reads, and whether the working tree differs.

**Bootstrap.** Before a policy file is first committed (the phase-1 close-out added `rules.json` and `perf_tests.json`),
the gate reads the project's working-tree draft and says `NOT COMMITTED`: every record and comparison made with it is
INCOMPLETE (no hand-over). The main session commits them before any record is made.

## rules.json: the gate's rules (K-GATE3)

Every rule that decides a verdict. Until the phase-1 close-out they were literals of the gate script, and the independent
verifier lowered G7's perf budget by editing two lines of it (catalog/analysis/phase1/finish_VER.json). The script now
holds the mechanism only, and is itself pinned: every record's `meta.json` `"gate"` lists the blob hashes of the gate
code that made it (every `.py` file of `tools/orchestration/` and `tools/py39_lint.py`, LF-normalised) and of every file
under `tools/orchestration/`, with the commit it is judged against. Check GATE fails a record made by code that differs
from that commit's -- unless its package is a gate-tool package (`"gate_tool": true`) and the main session signed a row
`{"kind": "gate-change", "digest": "<code digest>"}` (the digest is the record's `gate.digest`).

```json
{"format": "house-gate-rules-v1",
 "seeds": [0, 1, 2, 41, 97],                      "verification seeds (fewer narrow a record: never a hand-over)",
 "domains": ["admin_inventory", "..."],           "G1b's isolated runs (HOUSE_SIM_DOMAINS=<domain>)",
 "kernel_tests": ["test_kernel*.py", "..."],      "G1b's HOUSE_SIM_DOMAINS=core runs",
 "stage_order": ["A0", "A", "...", "P1", "P1C", "P2-S1", "...", "P2", "D1", "D2", "D3"],
 "default_prev": {"P1C": "P1F", "P2-S1": "P1C", "...": "..."},   "a stage's default previous gate (a record id)",
 "substage_base": {"P2-S1": "P1C", "...": "..."},                 "the hand-over base of a sub-stage's packages",
 "substages": {"P2-S1": ["P2-WP1", "..."], "...": []},           "packages that run in parallel in one sub-stage",
 "running_substages": ["P1C"],      "the sub-stages running now: --shared-tree attributes files to their peers only",
 "features": {"golden": {"stage": "B1", "introducer": "PHYS-INT", "what": "..."}},   "required after their stage",
 "extra_tools": {"physics": {"tool": "tools/physics_audit.py", "introducer": "PHYS-INT"}},
 "bench": {"default": [1.05, "prev"], "budgeted": ["substep_ms", "wait_1h_s"], "reported": ["day_s"],
           "tolerance": 1e-9},
 "perf": {"attempts": 3},                         "G7's perf job: a bound passes when one of N attempts meets it",
 "gen2_timing": {"max_ratio": 1.2},               "G9: generation-2 houses at most 20 % slower than generation 1",
 "scope": {"protected": ["tools/orchestration/*", ".github/*", "tests/data/room_golden.json",
                         "tests/data/gen1_digests.json"],
           "policy_files": ["tools/orchestration/gate_policy/*"]},
 "packages": {"<id>": {"stage": "P2-S1", "files": ["house_sim/kernel/world.py", "..."],
                       "allow": ["state"], "codes": ["P2"], "extras": ["physics"], "inert": [],
                       "bench": [1.15, "prev"], "requires": [], "aka": [], "note": "...",
                       "policy": false, "gate_tool": false, "members": ["..."]}}}
```

A package: `files` -- globs (fnmatch: `*` crosses `/`) of the files it may create or edit (G10; its
`catalog/analysis/tier1/handoff_<id>.md` and gate folder are always its own); `allow` -- the difference classes it may
change against its previous gate: `fingerprint` (the action fingerprint), `state` (the state digests, reference steps
and time), `obs` (the reference's final observation digest), `verdict` (each change still needs a reviewed row citing
one of `codes` and a signature), `layout` (layout_audit counts), `gen1` (generation-1 house digests); `bench` --
[factor, reference]: G7's budgeted metrics may not exceed reference x factor (`"prev"` or a gate id; of several packages
the strictest applies, with none the default); `extras` -- G9's extras; `inert` -- module globs that must import without
registering anything (G1c); `requires` -- features required besides those of the earlier stages; `aka` -- aliases;
`policy` -- may change these policy files; `gate_tool` -- owns the gate tool (above); `members` -- for a union, the
packages whose handoffs and gate records are its own (without it: the sub-stage of its stage, if it is not a member).
The review codes in a handoff table are found by the pattern `(?:[A-Z]-)?R<n>` and the literal codes of the table.

A malformed rules file stops the gate (no package table, no verdict). The phase-1 close-out transcribed the script's
values exactly (tests/test_gate_tool.py `CloseOutGateTests` pins their digests) and added:

* **P1C** (stage P1C, previous gate P1F): the close-out record `record --id P1C --package P1C --prev P1F` -- the six
  domains of the round (`P1C:admin_inventory`, `P1C:electrical`, `P1C:manipulation`, `P1C:mechanisms` with cleaning's
  spray-bottle group data and test, `P1C:navigation_robot`, `P1C:pets`) and the gate tool (`K-GATE3`), the round's
  reports in `catalog/analysis/phase1/`; reviewed rows cite code `P1C`. `P1C-INT` is the close-out integrator's scope
  (non-domain fixes and re-records), outside P1C: `--package P1C+P1C-INT` joins it.
* **Phase 2** (docs/design/VERIFY_HARDENING.md section 25 and its order): `P2-S1` = WP1 (with the WP2 stub) WP2 WP3 WP4
  WP7 WP9 WP11 (packages `P2-WP<n>`, after P1C), `P2-S2` = WP5 WP8, `P2-S3` = WP6 and the 16 `P2-WPD:<domain>`,
  `P2-S4` = WP10, `P2-S5` the main session's R0 / exit-test record (no package), `P2-S6` the exit-test fixes in
  non-domain files, `P2-ALL` (alias `P2`, previous gate P1C) the union; each stage's previous gate is the one before.
  Reviewed rows cite code `P2`. WP1, WP3, WP4 (except WP2's log digests), WP6, WP7, WP8, WP9 and WP10 may change no
  verdict (section 25); WP2's G7 budget is 1.15 x (its 15 % attribution overhead, 6.2).

The main session moves `running_substages` when a sub-stage starts (`["P2-S1"]` for phase 2's first stage).

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
`modes` defaults to every mode. A spec entry's `mode` defaults to `own`. Kind `skip` excuses a test that was removed,
skipped or turned into an expected failure. From the day after `expires` the entry excuses nothing; a malformed entry
(no owner, no reason, a date not `YYYY-MM-DD`) is reported and ignored. The room golden's known-failing pins
(tools/golden.py) count only while an entry here excuses the spec on seed 0 in its own mode.

## perf_tests.json: the wall-clock bound registry (K-GATE3)

```json
{"format": "house-gate-perf-tests-v1",
 "bounds": [{"test": "tests.test_mind.BudgetTests.test_update_budget_on_a_generated_house", "owner": "...",
             "file": "test_mind.py"},
            {"test": "tests.test_physics_thermal.ThermalStepTests.test_faster_than_...", "owner": "...",
             "skip": {"source_sha256": "<sha256 of the method's source>", "expires": "2026-11-30", "reason": "..."}}]}
```

Every perf mark of the project -- a test method that reads `HOUSE_SIM_PERF_TESTS` or calls
`tools.orchestration.wallclock.wall_clock_bound` -- must be listed (an unregistered mark fails green_tests.py's
correctness run, CI and the gate's G1), so a new bound is visible in review. The correctness runs run every bound
relaxed: its body and functional assertions run, its timing cannot fail (a counting clock for a test that reads the
switch; the helper records instead of asserting). A bound that still skips its body counts as a skip and fails the run,
unless its entry carries a `skip` exception: unexpired and naming the sha256 of the method's exact source
(`wallclock.method_source_sha`: decorators included, trailing blanks stripped, LF), so an edited method loses it. A
skip exception is for a bound a counting clock cannot keep (a relative bound such as "the new step is faster than the
old one"); convert such a bound to `wall_clock_bound` and drop the exception.

## signoffs/<gate id>.json: the second signature

A verdict change (G2/G3), an edited spec (`spec_hash` changed), an action the coverage fuzz no longer drives to
success, a test removed or skipped on purpose, a re-recorded room-golden entry, or a record a gate-tool package made
with uncommitted gate code passes only with two signatures: the package's reviewed row (its handoff table,
`gate/<ID>/reviewed.json` or `--reviewed FILE`, bound to the files the package owns: a golden row only from a package
that owns tests/data/room_golden.json) and a row of `signoffs/<ID>.json` committed by the main session, naming the exact
new verdict digest, spec hash, action, test, golden entry digest or gate-code digest:

```json
{"format": "house-gate-signoff-v1", "gate": "ADOPT_cleaning", "prev": "ADOPT", "signed_by": "main session",
 "date": "2026-10-23",
 "rows": [{"kind": "verdict", "key": "T-...-01", "seed": 0, "mode": "own", "digest": "<16 hex>"},
          {"kind": "edit", "key": "T-...-01", "spec_hash": "<sha256>"},
          {"kind": "coverage", "action": "brew"},
          {"kind": "test-removal", "test": "tests.test_x.Class.test_y", "mode": "core"},
          {"kind": "golden", "key": "specs/T-...-01", "digest": "<16 hex>"},
          {"kind": "gate-change", "digest": "<sha256 of the gate code>"}]}
```

A reviewed golden row is `{"kind": "golden", "key": "<entry key or glob, e.g. specs/*>", "code": "<one of the
package's codes>"}` in the package's reviewed.json; entry keys are `runs/<seed>/<mode>`, `specs/<key>` and
`meta/<field>` (the history is no entry); the base is the previous record's entries, or the base commit's golden for a
previous record without them. `python tools/orchestration/tier1_gate.py signoff [PREV] ID --by NAME --out FILE` drafts
the file from the two records (every row, with the package's review status, the codes and the change); review each
row, delete what you do not accept, save it here as `<ID>.json` and commit it. A signature names one verdict, digest or
code: if it changes again, it no longer applies.
