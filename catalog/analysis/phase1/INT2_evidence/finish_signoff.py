"""Add the verified test-removal rows (G1b quiet tests) to the verified sign-off and write the final files.

usage: python finish_signoff.py VERIFIED QUIET OUT
"""
import json
import sys
from collections import Counter

verified = json.load(open(sys.argv[1], encoding="utf-8"))
quiet = json.load(open(sys.argv[2], encoding="utf-8"))
added = []
for q in quiet:
    ok = len(q["new_skipped"]) >= int(q["skipped_now"] or 0) - int(q["skipped_before"] or 0) - \
        (1 if q["module"] == "tests.test_perception" and q["mode"] == "core" else 0)
    row = {"kind": "test-removal", "test": q["module"], "mode": q["mode"],
           "evidence": "G1b {} run: skipped {} -> {} (counts only: ADOPT's tests.json has no per-test ids); every test "
                       "skipped now that is not in tests/{} at 42533e2 (ADOPT's base) is new in phase 1 and skips "
                       "because it needs other domains loaded: {}; the tests skipped that existed then: {}".format(
                           q["mode"], q["skipped_before"], q["skipped_now"], q["file"],
                           ", ".join(t.split(".", 2)[-1] for t in q["new_skipped"]),
                           ", ".join(t.split(".", 2)[-1] for t in q["old_skipped"]) or "none")}
    if q["module"] == "tests.test_perception" and q["mode"] == "core":
        row["evidence"] += " (one more skip is a wall-clock bound moved to G7's perf job, which ran it)"
    if ok and len(q["old_skipped"]) <= int(q["skipped_before"] or 0):
        added.append(row)
    else:
        verified.setdefault("rejected", []).append(dict(row, why="the skip increase is not explained by new tests"))
verified["rows"] = verified["rows"] + added
verified["summary"]["signed"] = dict(Counter(r["kind"] + ":" + r.get("class", r["kind"]) + ":" + r.get("mode", "")
                                             for r in verified["rows"]))
verified["summary"]["rejected"] = len(verified.get("rejected") or [])
with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as f:
    json.dump(verified, f, indent=1)
print(verified["summary"])
