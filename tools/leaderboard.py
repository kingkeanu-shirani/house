"""Thin wrapper around house_sim.eval.leaderboard.

    python tools/leaderboard.py RUN_DIR [RUN_DIR ...] [--out reports] [--allow-mixed] [--verify] [--allow-incomplete] [...]
    python tools/leaderboard.py --verify-submission RUN_DIR [--allow-incomplete] [--no-write]

Same as `python -m house_sim --leaderboard ...` / `--verify-submission ...`; bare RUN_DIRs mean --leaderboard. Rows are
labelled and ranked by evaluation config (privileged, perception, done protocol, caps, options, completeness); only
verdicts made in-process (--verify) or signed with this machine's verifier key count as verified (docs/EVAL_HARNESS.md,
"Leaderboard and submission verification")."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from house_sim.eval.leaderboard import main  # noqa: E402

argv = sys.argv[1:]
if argv and not any(a in ("--leaderboard", "--verify-submission") for a in argv) and not argv[0].startswith("-"):
    argv = ["--leaderboard"] + argv
raise SystemExit(main(argv))
