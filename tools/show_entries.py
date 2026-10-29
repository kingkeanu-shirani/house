"""Print catalog entries with their analysis records.

usage:
  python tools/show_entries.py batch 7          # every entry of catalog/analysis/batches/batch_07.json
  python tools/show_entries.py ids T-abc-01 ... # specific ids
  python tools/show_entries.py section "Laundry"
Add --brief for one line per entry, --no-protect to leave out the draft protect map. Also lists which
docs/domains/*.md pages the entries need.

Beside each entry's analysis record it prints the draft protect map (docs/design/VERIFY_HARDENING.md 5.4 item 5,
7.4): every protect item with its candidate guard references from house_sim/data/verify/protect_rules.json
(house_sim.tasks.lint.draft_protect_map; tools/protect_map.py ID for one entry). Candidates are drafts the author
confirms, narrows or replaces; items without one need an authored predicate or a waiver.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load():
    entries = {}
    for line in (ROOT / "catalog/entries.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            entries[e["id"]] = e
    analysis = {}
    for path in sorted((ROOT / "catalog/analysis").glob("part_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                analysis[r["id"]] = r
    return entries, analysis


def protect_lines(eid, record):
    """The draft protect map of one entry, indented under its analysis record (VERIFY_HARDENING 7.4)."""
    from house_sim.tasks import lint
    draft = lint.draft_protect_map(eid, record)
    lines = ["  draft protect_map ({} of {} item(s) with candidates; drafts, not checked):".format(
        draft["covered"], draft["items"])]
    for row in draft["protect_map"]:
        refs = ", ".join(row["by"]) if row["by"] else "TODO: an authored fail: / never: predicate, or waive"
        lines.append("    {} -> {}".format(json.dumps(row["item"], ensure_ascii=False), refs))
        if row.get("restore"):
            lines.append("      needs protections.restore {}".format(json.dumps(row["restore"])))
    return lines


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    brief = "--brief" in argv
    protect = "--no-protect" not in argv
    argv = [a for a in argv if a not in ("--brief", "--no-protect")]
    entries, analysis = load()
    if not argv:
        print(__doc__)
        return 1
    mode = argv[0]
    if mode == "batch":
        batch = json.loads((ROOT / "catalog/analysis/batches/batch_{:02d}.json".format(int(argv[1]))).read_text(encoding="utf-8"))
        ids = batch["ids"]
    elif mode == "ids":
        ids = argv[1:]
    elif mode == "section":
        ids = [i for i, e in entries.items() if e["section"] == argv[1]]
    else:
        print(__doc__)
        return 1
    domains = Counter()
    for eid in ids:
        e, a = entries[eid], analysis.get(eid, {})
        domains[a.get("domain")] += 1
        for d in a.get("secondary", []) or []:
            domains[d] += 0.5
        if brief:
            print("{} | {} | {} | owner={}".format(eid, e["section"], e["text"], a.get("domain")))
            continue
        print("=" * 100)
        print("{}  [{}]  section: {}".format(eid, e["kind"], e["section"]))
        print("TEXT:", e["text"])
        for key in ("domain", "secondary", "interpretation", "conditional", "objects", "agents", "mechanics", "success",
                    "wrong", "protect", "fidelity", "risk", "approval", "compound", "user_sim", "notes"):
            if a.get(key) not in (None, [], ""):
                print("  {}: {}".format(key, json.dumps(a[key], ensure_ascii=False) if not isinstance(a[key], str) else a[key]))
        if protect and a.get("protect"):
            for line in protect_lines(eid, a):
                print(line)
    print("-" * 100)
    print("Domain pages to read (weight):", ", ".join("docs/domains/{}.md ({:g})".format(d, n) for d, n in domains.most_common() if d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
