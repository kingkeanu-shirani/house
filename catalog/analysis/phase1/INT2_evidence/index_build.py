"""Cost of a declared-reads static-index build inside a busy step (journal and quiet writes present), tree at ROOT.

usage: python index_build.py ROOT
"""
import os
import sys
import time
import warnings

root = sys.argv[1]
sys.path.insert(0, root)
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel import plugins  # noqa: E402
plugins.load_domains()
from house_sim.kernel.engine import HouseEnv  # noqa: E402

env = HouseEnv(generate_house(2), seed=0)
w = env.world
ids = sorted(w.s["entities"])
best = []
for rep in range(5):
    w.begin()
    for eid in ids[:20]:
        w.mut(eid)                                   # a busy step: journaled entities ...
    for eid in ids[20:400]:
        w.set_quiet(eid, "temperature_c", 21.5)      # ... and kernel thermal's quiet temperature writes
    t = time.perf_counter()
    for i in range(20):
        w.static_index("bench:{}:{}".format(rep, i), lambda r: r.get("category") == "tool" or "tool" in (r.get("is_a") or ()),
                       reads=("category", "is_a"))
    best.append((time.perf_counter() - t) / 20 * 1000.0)
    w.rollback()
print("{} entities: declared-reads build inside a busy step {:.3f} ms (best of 5)".format(len(ids), min(best)))
