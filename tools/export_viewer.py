"""Export a self-contained offline HTML replay viewer for one episode (thin wrapper).

    python tools/export_viewer.py SOURCE [--out PATH] [--view both|oracle|perceived] [--private] [--verify-spec]

Same as ``python -m house_sim --viewer SOURCE ...``; see docs/VIEWER.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_sim.eval.viewer import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(["--viewer"] + args if args and not args[0].startswith("-") else args))
