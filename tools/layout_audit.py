"""Sweep generated houses and report layout problems the final repair pass could not fix.

usage: python tools/layout_audit.py [n_seeds] [--first N] [--json OUT] [--verbose] [--domain NAME]

Generates n_seeds houses (default 40) for each configuration below (random, apartment, house+garage+patio,
townhouse with residents/pets) and prints problem counts by kind and by the furnisher that created the entity
involved (from the generator's trace), plus examples. Exit status 1 if any problem was found.
"""
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from house_sim.generator import _generate  # noqa: E402

CONFIGS = [
    {},
    {"dwelling": "apartment"},
    {"dwelling": "house", "require": ["garage", "patio"]},
    {"dwelling": "townhouse"},
    {"residents": "minimal", "pets": "none"},
    {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]},
]


def main(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 40
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    domain = argv[argv.index("--domain") + 1] if "--domain" in argv else None
    verbose = "--verbose" in argv
    kinds, owners, examples = Counter(), Counter(), defaultdict(list)
    houses, start, rows = 0, time.perf_counter(), []
    for opts in CONFIGS:
        for seed in range(first, first + n):
            report, trace = [], {}
            rooms = set()
            try:
                rooms = set(_generate(seed, report=report, trace=trace, **opts)["rooms"])
            except Exception as exc:  # noqa: BLE001
                report = ["crash: {}: {}".format(type(exc).__name__, exc)]
            houses += 1
            for line in report:
                kind = line.split(" ", 1)[0] if not line.startswith(("door clearance", "robot dock", "hazard-only", "hidden mount", "mount overlap")) else \
                    " ".join(line.split(" ", 2)[:2])
                blamed = line.split(" sealed by ", 1)[1] if " sealed by " in line else line
                ids = [i for i in re.findall(r"'?([a-z][a-z0-9_]*_\d+)'?", blamed) if i not in rooms and not i.startswith("conn_")]
                owner = sorted({trace.get(i, "core/structure") for i in ids} or {"?"})
                owner_s = ",".join(owner)
                if domain and not any(o.startswith(domain) for o in owner) and not line.startswith("crash"):
                    continue
                kinds[kind] += 1
                owners[(kind, owner_s)] += 1
                rows.append({"seed": seed, "opts": opts, "problem": line, "owner": owner_s})
                if len(examples[(kind, owner_s)]) < 3:
                    examples[(kind, owner_s)].append("seed {} {}: {}".format(seed, json.dumps(opts), line))
    elapsed = time.perf_counter() - start
    print("houses: {}  ({:.2f} s/house)  problems: {}".format(houses, elapsed / max(1, houses), sum(kinds.values())))
    for kind, count in kinds.most_common():
        print("  {:6d}  {}".format(count, kind))
    print("by kind and furnisher:")
    for (kind, owner), count in owners.most_common():
        print("  {:6d}  {:22s} {}".format(count, kind, owner))
        if verbose:
            for ex in examples[(kind, owner)]:
                print("            " + ex)
    if out:
        Path(out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 1 if kinds else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
