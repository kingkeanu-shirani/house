"""Merge catalog/analysis/part_*.jsonl with catalog entries and split them by owning domain.

Writes catalog/analysis/by_domain/<domain>.jsonl (owned entries) and <domain>__secondary.jsonl
(entries that use the domain without owning it), plus a summary on stdout.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ["manipulation", "mechanisms", "electrical", "plumbing_liquids", "climate", "appliances_cooking", "food",
           "cleaning", "textiles_laundry", "outdoor_plants", "pets", "people", "safety", "navigation_robot",
           "admin_inventory", "tools_vehicles"]


def main():
    entries = {}
    with (ROOT / "catalog/entries.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                e = json.loads(line)
                entries[e["id"]] = e
    analysis = {}
    problems = []
    for path in sorted((ROOT / "catalog/analysis").glob("part_*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                problems.append("{}:{} {}".format(path.name, n, exc))
                continue
            if record.get("id") in analysis:
                problems.append("duplicate " + record["id"])
            analysis[record["id"]] = record
    missing = sorted(set(entries) - set(analysis))
    extra = sorted(set(analysis) - set(entries))
    bad_domain = sorted(i for i, r in analysis.items() if r.get("domain") not in DOMAINS)
    owned, secondary = defaultdict(list), defaultdict(list)
    for eid, entry in entries.items():
        record = analysis.get(eid)
        if record is None:
            continue
        merged = {"id": eid, "kind": entry["kind"], "section": entry["section"], "text": entry["text"]}
        merged.update({k: v for k, v in record.items() if k != "id"})
        owned[record.get("domain")].append(merged)
        for other in record.get("secondary", []) or []:
            if other in DOMAINS and other != record.get("domain"):
                secondary[other].append({"id": eid, "section": entry["section"], "text": entry["text"], "owner": record.get("domain"),
                                         "mechanics": record.get("mechanics", [])})
    out = ROOT / "catalog/analysis/by_domain"
    out.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        with (out / (domain + ".jsonl")).open("w", encoding="utf-8", newline="\n") as stream:
            for record in owned.get(domain, []):
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        with (out / (domain + "__secondary.jsonl")).open("w", encoding="utf-8", newline="\n") as stream:
            for record in secondary.get(domain, []):
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {"entries": len(entries), "analyzed": len(analysis), "missing": len(missing), "extra": extra[:5],
               "bad_domain": bad_domain[:10], "problems": problems[:10],
               "owned": {d: len(owned.get(d, [])) for d in DOMAINS}, "secondary": {d: len(secondary.get(d, [])) for d in DOMAINS},
               "kinds_by_domain": {d: dict(Counter(r["kind"] for r in owned.get(d, []))) for d in DOMAINS}}
    print(json.dumps(summary, indent=1))
    if missing:
        print("MISSING:", " ".join(missing[:50]), file=sys.stderr)
    return 0 if not missing and not bad_domain and not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
