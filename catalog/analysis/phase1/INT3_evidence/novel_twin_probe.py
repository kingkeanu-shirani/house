"""Run the perception auditor's unfamiliar-twin probe alone with a given type list (both cameras).

usage: python novel_twin_probe.py ROOT TYPE [TYPE ...]
"""
import os
import sys
import warnings

root = sys.argv[1]
types = sys.argv[2:]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tools"))
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel import plugins  # noqa: E402
plugins.load_domains()
import perception_audit as PA  # noqa: E402

for camera in ("room", "geometric"):
    for seed in (0, 1):
        leaks, errors = PA._probe_twins(seed, camera, "novel", types, True)
        print(camera, "seed", seed, "leaks", len(leaks), "errors", len(errors))
        for leak in leaks[:8]:
            print("  LEAK", str(leak[2])[:300])
        for err in errors[:4]:
            print("  ERROR", err[:300])
