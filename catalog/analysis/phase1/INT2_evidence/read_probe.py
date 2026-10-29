"""Run the perception auditor's read probe alone over every look-alike group (timing and findings).

usage: python read_probe.py ROOT [camera] [group ...]
"""
import os
import sys
import time
import warnings

root = sys.argv[1]
camera = sys.argv[2] if len(sys.argv) > 2 else "room"
only = set(sys.argv[3:])
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tools"))
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel import plugins  # noqa: E402
plugins.load_domains()
from house_sim.kernel.registry import default_registry  # noqa: E402
import perception_audit as PA  # noqa: E402

reg = default_registry()
t0 = time.time()
n = 0
for group, types in sorted(reg.lookalike_types().items()):
    if len(types) < 2 or (only and group not in only):
        continue
    leaks, errors = PA._probe_reads(0, camera, group, sorted(types))
    n += 1
    for leak in leaks:
        print("LEAK", leak[2])
    for err in errors:
        print("ERROR", err[:300])
print("{} groups in {:.1f} s".format(n, time.time() - t0))
