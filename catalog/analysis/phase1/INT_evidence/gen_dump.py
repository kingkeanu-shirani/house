"""Dump generation-1 houses (the gen1_digests defaults: seeds 0-19, house {}) of the tree at ROOT to OUT as JSON.

usage: python gen_dump.py ROOT OUT [--gen 2]
"""
import json
import os
import sys
import warnings

root, out = sys.argv[1], sys.argv[2]
gen = int(sys.argv[sys.argv.index("--gen") + 1]) if "--gen" in sys.argv else None
sys.path.insert(0, root)
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel.plugins import load_domains  # noqa: E402
load_domains()
from house_sim.generator import generate_house  # noqa: E402

os.makedirs(out, exist_ok=True)
for seed in range(20):
    opts = {"gen": gen} if gen else {}
    house = generate_house(seed, **opts)
    with open(os.path.join(out, "house_{:02d}.json".format(seed)), "w", encoding="utf-8", newline="\n") as f:
        json.dump(house, f, indent=0, sort_keys=True, default=str)
print("dumped 20 houses ->", out)
