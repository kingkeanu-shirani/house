# The check log (VERIFY_HARDENING 12.4)

Written by the tools, never by hand. `<domain>.jsonl`: one line per schema-4 `--check` / `--verify` of a spec
(`house_sim/tasks/checklog.py`). `orchestrator.jsonl`: the hash-chained log of `tools/orchestration/run_check.py`, the
orchestrator-run checks agents cannot self-certify. A reviewer session that appears here for a catalog id may not accept
that spec's escapes (docs/REVIEW.md).
