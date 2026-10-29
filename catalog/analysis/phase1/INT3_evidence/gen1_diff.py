"""Leaf-level differences between two gen1_dump.py folders: per changed house the entry, and per path pattern
(entity ids -> <type>) the count.

usage: python gen1_diff.py BEFORE AFTER [OUT]
"""
import json
import os
import sys
from collections import Counter


def leaves(value, path=()):
    if isinstance(value, dict):
        if not value:
            yield path, {}
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    else:
        yield path, value


def main():
    a_dir, b_dir = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    ia = json.load(open(os.path.join(a_dir, "index.json"), encoding="utf-8"))
    ib = json.load(open(os.path.join(b_dir, "index.json"), encoding="utf-8"))
    patterns = Counter()
    lines = []
    changed = 0
    for ea, eb in zip(ia, ib):
        if ea["digest"] == eb["digest"]:
            continue
        changed += 1
        ha = json.load(open(os.path.join(a_dir, ea["file"]), encoding="utf-8"))
        hb = json.load(open(os.path.join(b_dir, eb["file"]), encoding="utf-8"))
        la, lb = dict(leaves(ha)), dict(leaves(hb))
        paths = []
        for p in sorted(set(la) | set(lb), key=str):
            if la.get(p, "<absent>") != lb.get(p, "<absent>"):
                if len(p) > 1 and p[0] == "entities":
                    rec = hb.get("entities", {}).get(p[1]) or ha.get("entities", {}).get(p[1]) or {}
                    pat = "entities.<{}>.{}".format(rec.get("type"), ".".join(str(x) for x in p[2:]))
                else:
                    pat = ".".join(str(x) for x in p)
                patterns[pat] += 1
                paths.append(pat)
        lines.append("seed {} house {}: {} -> {}; {}".format(ea["seed"], json.dumps(ea["house"], sort_keys=True),
                                                            ea["digest"][:12], eb["digest"][:12],
                                                            ", ".join(sorted(set(paths)))))
    lines.append("")
    lines.append("{} of {} entries changed; leaf paths: {}".format(changed, len(ia), dict(patterns.most_common())))
    text = "\n".join(lines)
    print(text)
    if out:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")


main()
