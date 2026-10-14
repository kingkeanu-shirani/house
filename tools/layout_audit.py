"""Sweep generated houses and report layout problems the final repair pass could not fix.

usage: python tools/layout_audit.py [n_seeds] [--first N] [--json OUT] [--verbose] [--domain NAME] [--gen 1|2]

Generates n_seeds houses (default 40) for each configuration below (random, apartment, house+garage+patio,
townhouse with residents/pets) and prints problem counts by kind and by the furnisher that created the entity
involved (from the generator's trace), plus examples. Exit status 1 if any problem was found.

--gen 2 audits generation-2 houses (docs/GENERATOR.md): the same configurations plus GEN2_CONFIGS (households with
an infant, a toddler, roommates and three generations, a winter start, a night start in a shoes-off region; most
houses have a porch with exterior steps). Two things are counted apart from the problems:
  * generation-2 notes: belongings and furnishings the generator skipped because their type is missing or nothing
    had room (it never forces them);
  * domain follow-ups: problems that involve only domain furnishers (no gen.* furnisher, no structure) and that the
    same house still shows when it is generated again without the gen.* furnishers: a generation-2 house is a new
    house to the domain furnishers, and their generation-1 placement rules show the same problem classes on
    generation-1 seeds the default audit does not cover (seeds 40-79). They are listed for the domains'
    generation-2 adoption (TIER1_INTEGRATION section 6) and do not fail this audit.
The exit status is 1 only for problems that involve the generator itself (a gen.* furnisher, the core furnisher, the
structure or a crash).
"""
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from house_sim.generator import _generate  # noqa: E402
from house_sim.kernel import plugins  # noqa: E402

CONFIGS = [
    {},
    {"dwelling": "apartment"},
    {"dwelling": "house", "require": ["garage", "patio"]},
    {"dwelling": "townhouse"},
    {"residents": "minimal", "pets": "none"},
    {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]},
]
GEN2_CONFIGS = [
    {"residents": {"composition": "couple_with_infant"}, "pets": "none"},
    {"residents": {"composition": "couple_with_toddler"}, "dwelling": "apartment"},
    {"residents": {"composition": "roommates"}, "dwelling": "house"},
    {"residents": {"composition": "multigenerational"}, "start": "2026-01-15T07:30:00"},
    {"region": "jp", "start": "2026-07-20T23:30:00"},
]
GENERATOR_OWNERS = ("core/structure", "core_furniture", "?")


def configs_for(gen):
    if gen >= 2:
        return [dict(o, gen=gen) for o in CONFIGS + GEN2_CONFIGS]
    return CONFIGS


def domain_only(owner, line):
    """A generation-2 problem that involves only domain furnishers (see the module doc)."""
    parts = owner.split(",")
    return not line.startswith("crash") and not any(p.startswith("gen.") for p in parts) and \
        any(p not in GENERATOR_OWNERS for p in parts)


def without_gen_furnishers(seed, opts):
    """The problem lines of the same house generated without the gen.* furnishers (a domain-only problem is the
    domains' own only if it is still there)."""
    saved = list(plugins.FURNISHERS)
    plugins.FURNISHERS[:] = [f for f in saved if not f[1].startswith("gen.")]
    try:
        report = []
        _generate(seed, report=report, **opts)
    finally:
        plugins.FURNISHERS[:] = saved
    return {line for line in report if not line.startswith("note ")}


def main(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 40
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    domain = argv[argv.index("--domain") + 1] if "--domain" in argv else None
    gen = int(argv[argv.index("--gen") + 1]) if "--gen" in argv else 1
    verbose = "--verbose" in argv
    kinds, owners, examples = Counter(), Counter(), defaultdict(list)
    notes, followups = Counter(), []
    houses, start, rows = 0, time.perf_counter(), []
    for opts in configs_for(gen):
        for seed in range(first, first + n):
            report, trace = [], {}
            rooms = set()
            try:
                rooms = set(_generate(seed, report=report, trace=trace, **opts)["rooms"])
            except Exception as exc:  # noqa: BLE001
                report = ["crash: {}: {}".format(type(exc).__name__, exc)]
            houses += 1
            baseline = None
            for line in report:
                if line.startswith("note "):
                    notes[re.sub(r" (of|for) [a-z]+_\d+$", "", line[5:])] += 1
                    continue
                kind = line.split(" ", 1)[0] if not line.startswith(("door clearance", "robot dock", "hazard-only", "hidden mount", "mount overlap")) else \
                    " ".join(line.split(" ", 2)[:2])
                blamed = line.split(" sealed by ", 1)[1] if " sealed by " in line else line
                ids = [i for i in re.findall(r"'?([a-z][a-z0-9_]*_\d+)'?", blamed) if i not in rooms and not i.startswith("conn_")]
                owner = sorted({trace.get(i, "core/structure") for i in ids} or {"?"})
                owner_s = ",".join(owner)
                if domain and not any(o.startswith(domain) for o in owner) and not line.startswith("crash"):
                    continue
                if gen >= 2 and domain_only(owner_s, line):
                    baseline = without_gen_furnishers(seed, opts) if baseline is None else baseline
                    if line in baseline:
                        followups.append({"seed": seed, "opts": opts, "problem": line, "owner": owner_s})
                        continue
                    owner_s += " (only with the gen.* furnishers)"
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
    if gen >= 2:
        print("domain follow-ups (domain furnishers only; not generator problems): {}".format(len(followups)))
        for row in followups:
            print("          {:30s} seed {} {}: {}".format(row["owner"], row["seed"], json.dumps(row["opts"]), row["problem"]))
        print("generation-2 notes (not problems): {}".format(sum(notes.values())))
        for text, count in notes.most_common():
            print("  {:6d}  {}".format(count, text))
    if out:
        Path(out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        if gen >= 2:
            Path(out).with_name(Path(out).stem + "_followups.json").write_text(json.dumps(followups, indent=1), encoding="utf-8")
    return 1 if kinds else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
