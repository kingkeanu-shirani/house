"""Probe the close-out kernel fixes for unfamiliar things on a tree (prints one line per case).

usage: python probe_novel.py ROOT
"""
import json
import os
import random
import sys
import warnings

root = sys.argv[1]
sys.path.insert(0, root)
os.chdir(root)
warnings.simplefilter("ignore")
from tests.test_kernel_redteam import env_of, floor_thing, handle_of  # noqa: E402
from house_sim.kernel.actions import ACTIONS, Ctx  # noqa: E402
from house_sim.kernel.errors import ActionError  # noqa: E402
from house_sim.systems.core import require_tool  # noqa: E402

LOOK = dict(label="thing", size_m=[0.2, 0.15, 0.25], novel=True, material="plastic", color="grey")


def thing(**props):
    out = dict(LOOK)
    out.update(props)
    return floor_thing((2, 1), **out)


def on_table(**props):
    rec = thing(**props)
    rec.pop("pos")
    rec.update(location="table_1", relation="on")
    return rec


TABLE = {"type": "table", "location": "lab", "pos": [2, 1], "accepts_items": True, "surface": True}
CUP = {"type": "generic_item", "location": "robot", "relation": "held", "held_hand": "right", "label": "cup",
       "portable": True, "size_m": [0.08, 0.08, 0.1], "mass_kg": 0.2}


def row(r):
    return (r["ok"], r["category"], str(r["message"])[:90])


print("== blank print")
for name, props in (("none", {}), ("blank_label", {"label_text": ""}), ("blank_text", {"text": "  "})):
    env = env_of({"thing_1": thing(**props)})
    h = handle_of(env, "thing_1")
    glance = sorted(env.observe()["visible_entities"][h])
    env.step("approach", h)
    ins = env.step("inspect", h)
    rd = env.step("read", h)
    print(name, "glance", glance == sorted(glance), len(glance), "inspect", sorted(json.loads(ins["message"])),
          "read", row(rd))

print("== print")
env = env_of({"thing_1": thing(label="fish flakes", label_text="Fish flakes - floating food for surface fish")})
h = handle_of(env, "thing_1")
env.step("approach", h)
print(row(env.step("read", h)))

print("== destination")
for name, props in (("plain", {}), ("bowl", {"container": True, "accepts_items": True, "capacity_l": 2.0}),
                    ("lidded", {"container": True, "accepts_items": True, "openable": True, "open": False}),
                    ("tray", {"surface": True, "accepts_items": True})):
    rows = []
    for relation in (None, "in", "on"):
        env = env_of({"thing_1": on_table(**props), "table_1": dict(TABLE), "cup_1": dict(CUP)},
                     robot={"hands": {"right": "cup_1", "left": None}})
        h = handle_of(env, "thing_1")
        cup = env.realism.handles.handle("cup_1")
        env.step("approach", h)
        kw = {"relation": relation} if relation else {}
        rows.append(row(env.step("place", cup, destination=h, **kw)))
    print(name, rows)

print("== tool")
for name, props in (("knife", {"affordances": ["cut"], "tool_kind": "knife"}), ("plain", {})):
    for perception in ("realistic", "oracle"):
        for named in (True, False):
            rec = dict(CUP, label="thing", novel=True, **props)
            env = env_of({"knife_1": rec}, perception=perception, robot={"hands": {"right": "knife_1", "left": None}})
            ctx = Ctx(env, ACTIONS["look"], {}, random.Random(0))
            try:
                out = require_tool(ctx, "cut", "knife_1" if named else None, consume=False)
            except ActionError as exc:
                out = (exc.category, str(exc))
            print(name, perception, "named" if named else "unnamed", out)

print("== perform")
for name, props in (("lidded", {"openable": True, "open": False, "container": True}), ("plain", {})):
    env = env_of({"thing_1": on_table(**props), "table_1": dict(TABLE)})
    env.step("approach", handle_of(env, "thing_1"))
    w = env.world
    w.begin()
    try:
        out = env.perform({"action": "open", "target": "thing_1"})
    except ActionError as exc:
        out = (exc.category, str(exc))
    w.rollback()
    print(name, out)

print("== peek inside (geometric)")
for name, props in (("lidded", {"openable": True, "open": False, "container": True}),
                    ("open_box", {"container": True, "accepts_items": True}), ("plain", {})):
    env = env_of({"thing_1": on_table(**props), "table_1": dict(TABLE)}, camera="geometric")
    env.step("look")
    h = handle_of(env, "thing_1")
    if h is None:
        print(name, "not in view")
        continue
    env.step("approach", h)
    rows = [row(env.step(a, h, where="inside")) for a in ("peek", "inspect")]
    print(name, rows)
