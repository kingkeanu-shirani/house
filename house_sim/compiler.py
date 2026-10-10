"""Merge the catalog with authored task specs into tasks/specs.jsonl.

Authored specs live in tasks/authored/*.json as {"specs": [spec, ...]} (one spec per
catalog entry, keyed by catalog_id). An authored spec is only a CANDIDATE; coverage
becomes DONE after verification evidence passes for the current implementation.
Entries without a spec remain TODO with an explicit reason.
"""
import json
from collections import Counter
from pathlib import Path

from .catalog import ROOT, write_catalog, write_jsonl
from .kernel.errors import SpecError
from .tasks.spec import check_structure, expand

AUTHORED = ROOT / "tasks" / "authored"


def load_authored(root=ROOT):
    folder = root / "tasks" / "authored"
    specs, sources = {}, {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("specs", []) if isinstance(data, dict) else data
        for spec in items:
            cid = spec.get("catalog_id")
            if cid in specs:
                raise SpecError("{} authored twice ({} and {})".format(cid, sources[cid], path.name))
            specs[cid] = spec
            sources[cid] = path.name
    return specs, sources


def compile_specs(root=ROOT):
    entries, manifest = write_catalog(root)
    by_id = {e["id"]: e for e in entries}
    authored, sources = load_authored(root)
    unknown = sorted(set(authored) - set(by_id))
    if unknown:
        raise SpecError("Authored specs reference unknown catalog ids: " + ", ".join(unknown[:10]))
    records = []
    for entry in entries:
        spec = authored.get(entry["id"])
        record = {"catalog_id": entry["id"], "kind": entry["kind"], "section": entry["section"], "text": entry["text"],
                  "source_line": entry["source_line"], "integrity": entry["integrity"], "source_file": sources.get(entry["id"])}
        if spec is None:
            record.update(status="BLOCKED" if entry["integrity"] == "INCOMPLETE" else "TODO",
                          reason="No authored, verified scenario yet; entry retained verbatim.", spec=None,
                          family=None, title=None, risk_level=None, fidelity="unimplemented", target_fidelity=None)
        else:
            try:
                expanded = expand(spec)
                problems = check_structure(expanded)
            except SpecError as exc:
                expanded, problems = spec, [str(exc)]
            record.update(status="INVALID" if problems else "CANDIDATE",
                          reason="; ".join(problems) if problems else "Authored; must pass verification before DONE.",
                          spec=spec, family=expanded.get("family"), title=expanded.get("title"),
                          risk_level=expanded.get("risk_level"), fidelity=expanded.get("fidelity", "S"),
                          target_fidelity=expanded.get("target_fidelity", expanded.get("fidelity", "S")),
                          reconstructed=entry["integrity"] == "INCOMPLETE")
        records.append(record)
    write_jsonl(root / "tasks/specs.jsonl", records)
    write_jsonl(root / "tasks/id_to_family.jsonl", [{"catalog_id": r["catalog_id"], "family": r["family"], "title": r["title"],
                                                     "status": r["status"], "source_file": r["source_file"]} for r in records])
    return entries, records, manifest


if __name__ == "__main__":
    entries, records, manifest = compile_specs()
    print(json.dumps({"entries": len(entries), "statuses": dict(Counter(r["status"] for r in records))}, indent=2))
