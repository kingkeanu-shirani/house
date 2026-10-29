"""The reference run (golden.run_spec, seed 0) of one spec in the tree at ROOT; prints one entity field at the end.

usage: python one_spec_field.py ROOT SPEC_ID ENTITY FIELD
"""
import os
import sys
import warnings

root, sid, eid, field = sys.argv[1:5]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tools"))
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel.plugins import load_domains  # noqa: E402
load_domains()
import golden  # noqa: E402

for key, name, spec in golden.collect_specs():
    if key == sid:
        out = golden.run_spec(spec, keep=True)
        rec = out["full"]["entities"].get(eid) or {}
        print(sid, eid, field, rec.get(field), "state", out["state"][:12], "passes", out["passes"])
        break
