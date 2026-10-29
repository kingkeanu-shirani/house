"""Combined spec files of a tree (as the tier-1 gate builds them): all authored specs, and the realistic ones.

usage: python make_spec_files.py ROOT OUT_DIR
Writes OUT_DIR/all_specs.json and OUT_DIR/realistic_specs.json; prints the counts.
"""
import importlib.util
import json
import os
import sys

root, out = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("gate_for_specs", os.path.join(root, "tools", "orchestration",
                                                                              "tier1_gate.py"))
gate = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.join(root, "tools", "orchestration"))
spec.loader.exec_module(gate)
specs = gate.collect_specs(gate.Path(root))
os.makedirs(out, exist_ok=True)
everything = [s for _, _, _, s in specs]
realistic = [s for key, _, _, s in specs if gate.own_mode(s) == "realistic"]
with open(os.path.join(out, "all_specs.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump({"specs": everything}, f)
with open(os.path.join(out, "realistic_specs.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump({"specs": realistic}, f)
print(len(everything), "specs;", len(realistic), "realistic")
