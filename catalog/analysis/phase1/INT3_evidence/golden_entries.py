"""Golden entries (runs/<seed>/<mode>, specs/<key>, meta/<field>) that differ between two room_golden.json files, with
the fields that differ inside each.

usage: python golden_entries.py BEFORE AFTER
"""
import json
import sys

a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
out = []
for section in ("runs", "specs"):
    for key in sorted(set(a.get(section, {})) | set(b.get(section, {}))):
        x, y = a.get(section, {}).get(key), b.get(section, {}).get(key)
        if x == y:
            continue
        if isinstance(x, dict) and isinstance(y, dict):
            fields = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
            detail = []
            for f in fields:
                if isinstance(x.get(f), list) and isinstance(y.get(f), list):
                    diff = [i for i, (p, q) in enumerate(zip(x[f], y[f])) if p != q]
                    detail.append("{}[{} of {} items]".format(f, len(diff), len(y[f])))
                else:
                    detail.append(f)
            out.append("{}/{}: {}".format(section, key, ", ".join(detail)))
        else:
            out.append("{}/{}: {}".format(section, key, "added" if x is None else "removed" if y is None else "changed"))
for key in sorted(set(a) | set(b)):
    if key in ("runs", "specs", "history"):
        continue
    if a.get(key) != b.get(key):
        out.append("meta/{}: changed".format(key))
print("\n".join(out))
print(len(out), "entries differ")
