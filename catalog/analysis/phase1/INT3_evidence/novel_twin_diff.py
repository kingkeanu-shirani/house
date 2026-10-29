"""Show the full rendering difference of two unfamiliar twins in the auditor's battery (one seed, one camera).

usage: python novel_twin_diff.py ROOT SEED CAMERA TYPE_A TYPE_B
"""
import os
import sys
import warnings

root, seed, camera, ta, tb = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tools"))
os.chdir(root)
warnings.simplefilter("ignore")
from house_sim.kernel import plugins  # noqa: E402
plugins.load_domains()
import perception_audit as PA  # noqa: E402

runs = {}
for type_name in (ta, tb):
    props = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "grey", "material": "plastic"}
    setup = [PA._first_room(seed),
             {"op": "spawn", "var": "x", "type": type_name, "floor": "{lr}", "eid": "probe_twin_1", "props": props},
             {"op": "robot", "room": "{lr}", "near": "{x}"},
             {"op": "lights", "room": "{lr}", "on": True}]
    if camera == "geometric":
        setup.append({"op": "camera", "face": "{x}", "tilt": -60})
    env, _ = PA._probe_env(setup, seed, camera)
    runs[type_name] = PA._twin_battery(env, "probe_twin_1", camera, True, PA.NOVEL_OPERATE)
for left, right in zip(runs[ta], runs[tb]):
    if left[:3] == right[:3]:
        continue
    print("at", left[0])
    a, b = left[1], right[1]
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                print("   ", k, ":", a.get(k, "<absent>"), "|", b.get(k, "<absent>"))
    else:
        print("   ", a, "|", b)
    print("    unresolved", left[2], right[2])
