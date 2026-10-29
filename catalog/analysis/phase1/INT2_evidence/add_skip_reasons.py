"""Append each new skipped test's skip reason (from skip_reasons.txt) to its test-removal row's evidence.

usage: python add_skip_reasons.py VERIFIED SKIP_REASONS
"""
import json
import re
import sys

path, reasons_path = sys.argv[1:3]
verified = json.load(open(path, encoding="utf-8"))
reasons = {}
for line in open(reasons_path, encoding="utf-8"):
    m = re.search(r"\((tests\.test_\w+)\.(\w+)\.(\w+)\) \.\.\. skipped ['\"](.*)['\"]$", line.strip())
    if m:
        reasons.setdefault(m.group(1), []).append("{}: '{}'".format(m.group(3), m.group(4)))
    elif "skipped" in line and "|" in line:
        # docstring-first lines (unittest -v prints the first docstring line): keep the reason alone
        m = re.search(r"skipped ['\"](.*)['\"]$", line.strip())
        dom = line.split("|")[0].strip()
        if m and dom == "core":
            reasons.setdefault("tests.test_perception", []).append("'{}'".format(m.group(1)))
for row in verified["rows"]:
    if row.get("kind") == "test-removal" and reasons.get(row["test"]):
        row["evidence"] += "; skip reasons in that run (verified): " + "; ".join(sorted(set(reasons[row["test"]])))
with open(path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(verified, f, indent=1)
print(sum(1 for r in verified["rows"] if r.get("kind") == "test-removal" and "skip reasons" in r["evidence"]),
      "test-removal rows carry their skip reasons")
