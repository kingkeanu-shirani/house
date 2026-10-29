"""Dump the idle and wrong-plan final states of one trial on a tree (A/B helper of the P2-S2 merge gate).

usage: python dump_trial.py TREE CATALOG_ID SEED MODE(own|realistic) OUT.json
"""
import json
import os
import sys

tree, cid, seed, mode, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
sys.path.insert(0, tree)
os.chdir(tree)
from house_sim.kernel.plugins import load_domains  # noqa: E402
from house_sim.kernel.predicates import digest  # noqa: E402
from house_sim.tasks import verify  # noqa: E402
import glob  # noqa: E402

load_domains()
spec = None
for path in sorted(glob.glob(os.path.join(tree, "tasks", "authored", "*.json"))):
    data = json.load(open(path, encoding="utf-8"))
    for s in data.get("specs", []) if isinstance(data, dict) else data:
        if s.get("catalog_id") == cid:
            spec = s
            break
    if spec:
        break
capture = {}
raw = verify.run_trial(spec, seed, "realistic" if mode == "realistic" else None, capture=capture)
res = {"passed": raw.get("passed"), "idle": capture["idle"].world.s,
       "wrong": [e.world.s if e is not None else None for e in capture.get("wrong") or []],
       "reference": capture["reference"].world.s,
       "digests": {"idle": digest(capture["idle"].world.s)}}
with open(out, "w", encoding="utf-8") as f:
    json.dump(res, f, sort_keys=True, default=str)
print(raw.get("passed"), res["digests"])
