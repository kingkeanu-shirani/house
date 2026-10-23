"""Leaf-path patterns that differ between two golden dumps, restricted to the scripted runs (runs/*.json), which do not
depend on spec variants: python golden_runs_diff.py BEFORE AFTER"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, r"<home>\OneDrive\Desktop\house_ai")
ID = re.compile(r"^(?:\d+|[a-z][a-z0-9_]*_\d+|obj_[0-9a-f]{8,16}|r\d+|p\d+)$")


def leaves(value, path=()):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    elif isinstance(value, list) and value and all(isinstance(v, (dict, list)) for v in value):
        for i, v in enumerate(value):
            yield from leaves(v, path + (str(i),))
    else:
        yield path, value


def pattern(path):
    return ".".join("*" if ID.match(str(p)) else str(p) for p in path)


before, after = sys.argv[1], sys.argv[2]
counts, ex, types = Counter(), {}, defaultdict(Counter)
folder_b, folder_a = os.path.join(before, "runs"), os.path.join(after, "runs")
for name in sorted(os.listdir(folder_b)):
    a = json.load(open(os.path.join(folder_b, name), encoding="utf-8"))
    b = json.load(open(os.path.join(folder_a, name), encoding="utf-8"))
    la, lb = dict(leaves(a)), dict(leaves(b))
    for path in set(la) | set(lb):
        if la.get(path, "<absent>") != lb.get(path, "<absent>"):
            pat = pattern(path)
            counts[pat] += 1
            ex.setdefault(pat, "{} {}: {!r} -> {!r}".format(name, ".".join(map(str, path)), la.get(path, "<absent>"),
                                                            lb.get(path, "<absent>"))[:200])
            if len(path) > 1 and path[0] == "entities":
                rec = (b.get("entities") or {}).get(path[1]) or (a.get("entities") or {}).get(path[1]) or {}
                if isinstance(rec, dict):
                    types[pat][rec.get("type")] += 1
for pat, n in counts.most_common():
    print("{:6d}  {}  {}".format(n, pat, dict(types[pat].most_common(4)) if types[pat] else ""))
    print("          ", ex[pat])
