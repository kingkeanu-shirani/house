"""Dump every house tests/data/gen1_digests.json pins (defaults and spec houses) of the tree at ROOT, as JSON files
named by entry index, using the entry list of ENTRIES_ROOT's pinned file (default ROOT).

usage: python gen1_dump.py ROOT OUT [ENTRIES_ROOT]
"""
import json
import os
import sys
import warnings

root, out = sys.argv[1], sys.argv[2]
entries_root = sys.argv[3] if len(sys.argv) > 3 else root
sys.path.insert(0, root)
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel.plugins import load_domains  # noqa: E402
load_domains()
from house_sim.generator import generate_house  # noqa: E402
from house_sim.kernel.predicates import digest  # noqa: E402

with open(os.path.join(entries_root, "tests", "data", "gen1_digests.json"), encoding="utf-8") as f:
    data = json.load(f)
entries = data.get("defaults", []) + data.get("spec_houses", [])
os.makedirs(out, exist_ok=True)
index = []
for i, entry in enumerate(entries):
    house = generate_house(entry["seed"], **(entry.get("house") or {}))
    name = "house_{:03d}.json".format(i)
    index.append({"file": name, "seed": entry["seed"], "house": entry.get("house") or {}, "pinned": entry["digest"],
                  "digest": digest(house)})
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(house, f, indent=0, sort_keys=True, default=str)
with open(os.path.join(out, "index.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(index, f, indent=1)
print("dumped {} houses -> {}; {} equal to the pinned digest".format(
    len(entries), out, sum(1 for e in index if e["digest"] == e["pinned"])))
