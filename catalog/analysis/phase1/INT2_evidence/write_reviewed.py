"""gate/<ID>/reviewed.json (the package's first signature) from the verified sign-off: one row per (spec, mode, class)
with the seeds that verified, code P1.

usage: python write_reviewed.py VERIFIED OUT
"""
import json
import sys
from collections import defaultdict

verified = json.load(open(sys.argv[1], encoding="utf-8"))
groups = defaultdict(list)
for row in verified["rows"]:
    if row.get("kind") != "verdict":
        continue
    groups[(row["key"], row["mode"], row["class"], row.get("evidence", ""))].append(row["seed"])
DECISION = {
    "wrong-list": "accepted (INT2, verified on the trial records): PASS before and after; only the wrong-plan list "
                  "differs and every wrong plan of the new record ran and fails, the reference passes and the initial "
                  "and idle states fail -- ",
    "fix": "accepted (INT2, verified on the trial records): FAIL -> PASS by a phase-1 fix; the new trial passes in "
           "full -- ",
}
rows = []
for (key, mode, cls, evidence), seeds in sorted(groups.items()):
    rows.append({"catalog_id": key, "seeds": sorted(seeds), "mode": mode, "code": "P1",
                 "decision": DECISION[cls] + evidence})
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
    json.dump(rows, f, indent=1)
print(len(rows), "reviewed rows ->", sys.argv[2])
