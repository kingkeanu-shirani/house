"""Attribute golden dump differences: per path pattern, the files (runs / specs) and the entity types involved.

usage: python golden_attrib.py BEFORE AFTER [--patterns P1,P2]
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

ID = re.compile(r"^(?:\d+|[a-z][a-z0-9_]*_\d+|obj_[0-9a-f]+|r\d+|p\d+|resident_\d+|conn_.*)$")


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
    out = []
    for i, p in enumerate(path):
        if i >= 1 and path[i - 1] in ("entities", "visible_entities", "people", "pets", "rooms", "connections"):
            out.append("*")
        elif ID.match(str(p)):
            out.append("*")
        else:
            out.append(str(p))
    return ".".join(out)


def load_dir(root):
    out = {}
    for sub in ("runs", "specs"):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out[sub + "/" + name] = json.load(f)
    return out


def main():
    before, after = load_dir(sys.argv[1]), load_dir(sys.argv[2])
    only = None
    if "--patterns" in sys.argv:
        only = set(sys.argv[sys.argv.index("--patterns") + 1].split(","))
    files, types = defaultdict(Counter), defaultdict(Counter)
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name, {}), after.get(name, {})
        la, lb = dict(leaves(a)), dict(leaves(b))
        for path in set(la) | set(lb):
            if la.get(path, "<absent>") == lb.get(path, "<absent>"):
                continue
            pat = pattern(path)
            if only and pat not in only:
                continue
            files[pat][name.split(".json")[0].replace("_final_state", "").replace("_reset_state", "")
                       .replace("_final_obs", "").replace("_reset_obs", "")] += 1
            src = b if path[0] in ("entities",) else None
            if src is not None and len(path) > 1:
                rec = (b.get("entities", {}).get(path[1]) or a.get("entities", {}).get(path[1]) or {})
                types[pat][rec.get("type")] += 1
    for pat in sorted(files, key=lambda p: -sum(files[p].values())):
        print("{:6d}  {}".format(sum(files[pat].values()), pat))
        fl = files[pat]
        print("        files ({}): {}".format(len(fl), ", ".join(sorted(fl))[:600]))
        if types[pat]:
            print("        types: {}".format(dict(types[pat].most_common(40))))


main()
