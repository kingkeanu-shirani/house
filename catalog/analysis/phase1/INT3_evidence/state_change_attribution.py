"""Attribute the G2 state-digest changes of record P1C vs P1F: for every own-mode trial, whether its initial state
changed (initial_hash) and whether its house holds a household robot of navigation_robot's (robot_vacuum,
robot_vacuum_mop, robot_mower: the types whose label_text the close-out added). Run from the project root.

usage: python state_change_attribution.py PREV_DIR CUR_DIR
"""
import json
import os
import sys
import warnings
from collections import Counter

sys.path.insert(0, os.getcwd())
warnings.simplefilter("ignore")
from house_sim.tasks.runner import build  # noqa: E402
sys.path.insert(0, os.path.join(os.getcwd(), "tools", "orchestration"))
import tier1_gate as T  # noqa: E402

ROBOTS = {"robot_vacuum", "robot_vacuum_mop", "robot_mower"}


def load(path):
    return {(r["key"], r["seed"]): r for r in (json.loads(line) for line in open(path, encoding="utf-8") if line.strip())}


prev, cur = (load(os.path.join(d, "specs_own.jsonl")) for d in sys.argv[1:3])
specs = {key: spec for key, _, _, spec in T.collect_specs(T.Path(os.getcwd()))}
table = Counter()
odd = []
for (key, seed), b in sorted(cur.items()):
    a = prev.get((key, seed)) or {}
    changed = a.get("initial_hash") != b.get("initial_hash")
    config, _task, _oracle = build(specs[key], seed)
    robot = any(r.get("type") in ROBOTS for r in config["entities"].values())
    table[(changed, robot)] += 1
    if changed != robot:
        odd.append((key, seed, changed, robot))
for (changed, robot), n in sorted(table.items()):
    print("initial state changed {:5}  household robot in the house {:5}: {} trials".format(str(changed), str(robot), n))
print("trials where the two disagree:", odd[:20])
