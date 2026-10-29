"""The package's first signature for the re-recorded room-golden entries (P1C-INT owns tests/data/*): one reviewed row
{"kind": "golden", "key", "code": "P1C", "package": "P1C-INT", "decision"} per entry that changed between the committed
golden and the re-recorded one.

usage: python make_reviewed_golden.py BEFORE AFTER OUT
"""
import json
import sys

before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
WHY = ("re-recorded by INT3 after attribution: navigation_robot's phase-1 close-out type data (robot_vacuum, "
       "robot_vacuum_mop and robot_mower carry their own printed label_text; the read probe's robot_vacuum row). A/B "
       "on frozen trees: tools/golden.py dump --all of HEAD 1dbc6db with house_sim/data/types/navigation_robot.json "
       "reverted to P1F's is identical to P1F's dump (275 files), HEAD's differs in 18 files / 8 path patterns "
       "(entities.*.label_text, obs.visible_entities.*.label_text, and the realistic runs' handles re-salted by the "
       "initial-house digest); the close-out's code changes and INT3's kernel changes alone change no golden file. ")
rows = []
for section in ("runs", "specs"):
    for key in sorted(set(before.get(section, {})) | set(after.get(section, {}))):
        a, b = before.get(section, {}).get(key), after.get(section, {}).get(key)
        if a == b:
            continue
        if section == "runs":
            what = "only the reset / step state and observation digests changed; every step's action, ok flag and " \
                   "seconds unchanged"
        else:
            what = "only the reference's final state digest changed (label_text of a household robot); passes, " \
                   "steps, time and evaluation unchanged"
        rows.append({"kind": "golden", "key": "{}/{}".format(section, key), "code": "P1C", "package": "P1C-INT",
                     "decision": "accepted (INT3): " + WHY + what})
with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as f:
    json.dump(rows, f, indent=1)
print(len(rows), "golden rows ->", sys.argv[3])
