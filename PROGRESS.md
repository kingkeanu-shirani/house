# Progress

Started UTC: 2026-10-07T07:33:27.074933+00:00

- Source files preserved; current Python environment reviewed.
- Catalog and evaluation architecture in progress.

## Core milestone

- Preserved and indexed 2,719 entries across 105 sections.
- Built 19 symbolic scenario families and 53 guarded action names.
- Added interactive/headless CLIs, observation-only search baseline, limited language
  grammar, permissions, events, appliance cycles, undo, snapshots and replay.
- Initial catalog sweep found two nested-light transition errors; both repaired.
- Adversarial tests found label/ID ambiguity; natural language now asks rather than
  preferring an object whose ID happens to equal the shared label.
- Final verification, docs and packaging remain. No five-day completion is claimed.
- Checkpoint UTC: 2026-10-07T13:53:20.515155+00:00

## Verification and packaging milestone

- 35 regression tests pass, covering all 19 families and adversarial behavior.
- Tested optional offline wheel build/install and six CLI/export/save/replay paths.
- Tightened undo guards, private snapshot diffs, repeated-question scoring, carried
  liquid mass, fixed-surface cleaning and reward accounting.
- Each mapped entry is being rechecked on five seeds against the final source hash.
- Full-catalog implementation is still partial; remaining requirements are retained.
- Checkpoint UTC: 2026-10-07T14:09:19.489882+00:00
