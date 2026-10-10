"""Group catalog entries into spec-authoring batches.

Batches follow catalog order and keep sections together where possible. Primitives,
variations, adaptability and appendix entries get their own (smaller) batches because
they need different scenario designs. Entries that already have an authored spec are
skipped. Writes catalog/analysis/batches/batch_NN.json and prints a summary.

usage: python tools/make_batches.py [target_size] [hard_target_size]
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def authored_ids():
    ids = set()
    for path in sorted((ROOT / "tasks/authored").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for spec in data.get("specs", []):
            ids.add(spec.get("catalog_id"))
    return ids


def main(argv):
    target = int(argv[0]) if argv else 70
    hard_target = int(argv[1]) if len(argv) > 1 else 40
    entries = load_jsonl(ROOT / "catalog/entries.jsonl")
    analysis = {}
    for path in sorted((ROOT / "catalog/analysis").glob("part_*.jsonl")):
        for record in load_jsonl(path):
            analysis[record["id"]] = record
    done = authored_ids()
    groups = {"task": [], "primitive": [], "variation": [], "adaptability": [], "appendix": []}
    for entry in entries:
        if entry["id"] in done:
            continue
        groups[entry["kind"]].append(entry)
    batches = []

    def pack(items, size, label):
        current, sections = [], []
        for entry in items:
            if current and len(current) >= size and entry["section"] != current[-1]["section"]:
                batches.append((label, current))
                current = []
            elif current and len(current) >= int(size * 1.2):
                batches.append((label, current))
                current = []
            current.append(entry)
        if current:
            if batches and batches[-1][0] == label and len(current) < max(8, size // 4):
                batches[-1] = (label, batches[-1][1] + current)
            else:
                batches.append((label, current))

    pack(groups["task"], target, "task")
    pack(groups["appendix"], hard_target, "appendix")
    pack(groups["adaptability"], hard_target, "adaptability")
    pack(groups["primitive"], hard_target, "primitive")
    pack(groups["variation"], hard_target, "variation")
    out = ROOT / "catalog/analysis/batches"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("batch_*.json"):
        old.unlink()
    summary = []
    for n, (label, items) in enumerate(batches):
        domains = Counter(analysis.get(e["id"], {}).get("domain", "?") for e in items)
        secondary = Counter(d for e in items for d in analysis.get(e["id"], {}).get("secondary", []) or [])
        record = {"batch": n, "kind": label, "ids": [e["id"] for e in items],
                  "sections": list(dict.fromkeys(e["section"] for e in items)),
                  "domains": dict(domains.most_common()), "secondary_domains": dict(secondary.most_common())}
        (out / "batch_{:02d}.json".format(n)).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        summary.append((n, label, len(items), record["sections"][0][:40], len(record["sections"])))
    for row in summary:
        print(row)
    print("batches", len(batches), "entries", sum(len(i) for _, i in batches), "already authored", len(done))


if __name__ == "__main__":
    main(sys.argv[1:])
