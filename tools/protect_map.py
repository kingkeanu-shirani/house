"""Draft protect map of a catalog entry (docs/design/VERIFY_HARDENING.md 7.4; WP8).

usage:
  python tools/protect_map.py T-d990c58b123a5e-01            # every protect item with its candidate references
  python tools/protect_map.py T-d990c58b123a5e-01 --json     # the draft as JSON (lint.draft_protect_map)
  python tools/protect_map.py --coverage                     # the rule table's coverage over every analysis record

Each analysis protect item (catalog/analysis/part_*.jsonl) is mapped to candidate guard references by the
deterministic rule table house_sim/data/verify/protect_rules.json (regular expressions to references of the 7.3
grammar, and the protections.restore revocations lights and doors need). Candidates are drafts: the author confirms,
narrows ({binding} / .field) or replaces each one, waives an item with a reason, and lint L-PROTECT checks the result.
Nothing is written into a spec. draft(catalog_id) is what `--draft-hardening` (house_sim/tasks/verify.py) embeds.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.tasks import lint  # noqa: E402


def draft(catalog_id):
    """The draft protect map of an entry (lint.draft_protect_map), or None when it has no analysis record."""
    record = lint.analysis_record(catalog_id)
    if record is None:
        return None
    return lint.draft_protect_map(catalog_id, record)


def coverage():
    """{"records", "items", "covered", "multi", "share", "by_rule": {rule id: items}}: how many analysis protect
    items get at least one candidate (the 7.4 prototype measured 72.9 % with 30 rules)."""
    lint.analysis_record("")            # loads the table
    items = covered = multi = 0
    by_rule = {}
    records = lint._ANALYSIS or {}
    for cid in sorted(records):
        for item in records[cid].get("protect") or ():
            if not isinstance(item, str):
                continue
            items += 1
            got = lint.classify_protect_item(item)
            if got["by"]:
                covered += 1
                if len(got["by"]) > 1:
                    multi += 1
            for rule in got["rules"]:
                by_rule[rule] = by_rule.get(rule, 0) + 1
    return {"records": len(records), "items": items, "covered": covered, "multi": multi,
            "share": round(covered / float(items), 4) if items else 0.0,
            "by_rule": dict(sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0])))}


def render(result):
    lines = ["{}  {} protect item(s), {} with candidates".format(result["catalog_id"], result["items"],
                                                                 result["covered"])]
    for row in result["protect_map"]:
        lines.append("  - {}".format(row["item"]))
        if row["by"]:
            lines.append("      by: {}".format(", ".join(row["by"])))
            lines.append("      rules: {}".format(", ".join(row["rules"])))
        else:
            lines.append("      TODO: {}".format(row.get("todo")))
        if row.get("restore"):
            lines.append("      needs protections.restore: {}".format(", ".join(row["restore"])))
        if row.get("waive_hint"):
            lines.append("      or waive: {}".format(row["waive_hint"]))
    if result.get("restore"):
        lines.append("  protections.restore: {}".format(json.dumps(result["restore"])))
    lines.append("  ({})".format(result["note"]))
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if argv == ["--coverage"]:
        print(json.dumps(coverage(), indent=1))
        return 0
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__)
        return 2
    result = draft(argv[0])
    if result is None:
        print("no analysis record for {}".format(argv[0]), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1, ensure_ascii=False) if as_json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
