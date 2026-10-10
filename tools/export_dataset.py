"""Export the trajectory / preference-pair dataset (thin wrapper around house_sim.eval.dataset; docs/DATASET.md).

usage: python tools/export_dataset.py OUT_DIR [--formats sft,pairs_trajectory,pairs_step,critique] [--split train|dev]
                                      [--seeds-per-entry N] [--seeds S1,S2] [--max-pairs N] [--kinds K1,K2]
                                      [--domains D1,D2] [--sections "S1;S2"] [--catalog-ids ID1,ID2]
                                      [--obs compact|full] [--workers N] [--verify-now] [--overwrite] [--json OUT]
                                      [--quiet]
Same as: python -m house_sim --export-dataset OUT_DIR [...]. The test split and test seeds are always refused.
OUT_DIR must be missing or empty; --overwrite replaces a previous export there (its manifest.json and every
<format>.jsonl are deleted first). house_sim/eval/splits.py is required (exit 2 when it cannot be imported).
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
    return dataset.main(["--export-dataset"] + list(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
