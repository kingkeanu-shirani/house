"""Validate an exported dataset directory or JSONL file (thin wrapper around house_sim.eval.dataset; docs/DATASET.md).

usage: python tools/validate_dataset.py PATH [--no-deep | --allow-static] [--json OUT] [--quiet]
Same as: python -m house_sim --validate-dataset PATH [...]. Exit status 0 when valid, 1 when not.
Checks schema, splits (no test seeds, seeds on the split's schedule), leaks (true entity ids, underscore / secret /
never-tier fields), content hashes, duplicates, the manifest and that a dataset directory holds nothing the manifest
does not list. A record whose environment cannot be rebuilt fails unless --allow-static accepts static checks for it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.eval import dataset  # noqa: E402


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip())
        return 2
    return dataset.main(["--validate-dataset"] + list(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
