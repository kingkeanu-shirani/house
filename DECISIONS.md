# Decisions

- The user message and supplied house_env v2 are the existing specification. The
  placeholder in the brief is not an additional missing input.
- Stay on standard-library Python. The original contract explicitly excludes physics
  dependencies for now; G/P fidelity is future work, never relabeled as S success.
- Preserve exact original lines and UTF-8 source bytes. Normalize a separate text field.
  Multi-command appendix paragraphs remain compound entries and are not silently
  split or credited as complete when only one clause is implemented.
- Catalog metadata and executable coverage are distinct. No keyword-only or generic
  `task_done` switch may count as a solution. Each supported family uses observable
  state changes and protected invariants; unsupported semantics remain visible.
- A scripted user simulator represents residents; all communication is simulated.
  No emails, calls, purchases, door controls, or real-world robot actions occur.
- Package as a new project archive because the request adds an installable repository.
  The original source attachment is retained verbatim in catalog/raw.

- DONE denotes a verified bounded S-level scenario for a source entry, not real-world
  task competence. The full brief remains incomplete, and the final report must show it.
- Fixed-surface washing is excluded from the current family: moving a floor/window
  into a sink to make a script pass would be an invalid substitute.
- Scene generation retains section context and grounded transformation materials.
  Context variants still need broader behavioral variation beyond this first release.
- The observation-only baseline and privileged task oracles have separate modules
  and explicit labels. Neither is described as a trained LLM.
- Runtime was tested on Linux/Python 3.12.14; source syntax was additionally checked
  against Python 3.9. Windows and a Python 3.9 interpreter were not available here.

## v2 decisions (2026-10-09)

- Scope changed by the user: build a realistic household simulation and incorporate every catalog entry, with
  no limits on changes. The v0.3 code is archived (archive/v0.3.0_baseline.zip); v1 scenario families are retired.
- Still standard-library Python (original contract; Windows + Python 3.14 has no reliable physics wheels, and the
  user's spec named no engine). Fidelity labels: S (symbolic), G (grid geometry: reach, clearance, paths, fit,
  stacking, stairs), P-approx (lumped physical models: heat, liquids, cloth/knot topology). Full rigid-body,
  deformable or fluid simulation (true P) is not claimed.
- One authored, data-only spec per catalog entry (tasks/authored), not regex families. Templates and variants keep
  near-duplicates consistent while each entry is verified separately.
- Mechanics are split into 16 domains (docs/DOMAINS.md) with explicit type/verb ownership (docs/CONVENTIONS.md) so
  they can be built in parallel without collisions.
- Refused unsafe/private/unauthorized attempts fail a task by default (strict_blocks), so an agent cannot "probe"
  its way through guards.
- The truncated adaptability fragment is implemented against a documented reconstruction and flagged as such.
- Git is not installed on this machine, so milestones are archived as zip snapshots instead of commits.
- Layout realism is enforced by invariants on generated houses rather than by hoping every furnisher is careful:
  a placement may never make access worse (doorways stay mutually reachable, everything that was reachable stays
  reachable, the robot can always leave its dock), loose items never sit on the only hazard-free route, and a
  final repair pass plus an audit tool keep this true as domains evolve.
- Things a robot cannot reach are allowed when that is how real homes are (an outlet behind the fridge it feeds);
  operable fixtures (windows, switches, valves, panels) are protected.
