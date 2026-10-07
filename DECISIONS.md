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
