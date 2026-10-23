"""Leaf differences between two gen_dump.py folders, grouped by path pattern (ids and indexes become *), with the
entity types involved: python gen_diff.py BEFORE AFTER"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

ID = re.compile(r"^(?:\d+|[a-z][a-z0-9_]*_\d+|r\d+|p\d+|resident_\d+|conn_.*)$")


def leaves(value, path=()):
    if isinstance(value, dict):
        if not value:
            yield path, {}
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for i, v in enumerate(value):
            yield from leaves(v, path + (str(i),))
    else:
        yield path, value


def pattern(path):
    return ".".join("*" if ID.match(str(p)) else str(p) for p in path)


before, after = sys.argv[1], sys.argv[2]
counts, types, examples = Counter(), defaultdict(Counter), defaultdict(list)
for name in sorted(os.listdir(before)):
    a = json.load(open(os.path.join(before, name), encoding="utf-8"))
    b = json.load(open(os.path.join(after, name), encoding="utf-8"))
    la, lb = dict(leaves(a)), dict(leaves(b))
    for path in sorted(set(la) | set(lb), key=str):
        if la.get(path, "<absent>") == lb.get(path, "<absent>"):
            continue
        pat = pattern(path)
        counts[pat] += 1
        if path and path[0] == "entities" and len(path) > 1:
            ent = b.get("entities", {}).get(path[1]) or a.get("entities", {}).get(path[1]) or {}
            types[pat][ent.get("type")] += 1
        if len(examples[pat]) < 2:
            examples[pat].append("{} {}: {!r} -> {!r}".format(name, ".".join(map(str, path)), la.get(path, "<absent>"),
                                                             lb.get(path, "<absent>")))
for pat, n in counts.most_common():
    print("{:6d}  {}  {}".format(n, pat, dict(types[pat].most_common(6)) if types[pat] else ""))
    for ex in examples[pat]:
        print("          ", ex[:220])
