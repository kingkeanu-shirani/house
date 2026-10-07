"""Lossless source accounting. Classification never implies implementation."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "catalog").is_dir():
    ROOT = Path(__file__).resolve().parent / "resources"

APPENDIX_HEADINGS = {
    "Emergency, Safety, and First Aid", "Delicate Electronics and IT Maintenance",
    "Advanced Culinary and Kitchen", "Pet and Bio-Care", "Physical Human Assistance",
    "Emergency response (must-have). Detection and alerting are covered well, but active response is thin.",
    "Doors, visitors, and communication (must-have).",
    "Reaching, climbing, and fixing. Tool handling stops at basic hand tools, and there's no height access.",
    "Messy, nasty, and hazardous.", "The robot itself.", "Cooking gaps.",
}
ADAPT_HEADINGS = {
    "Physics and Reflexive Edge Cases", "Advanced Delicate Handling",
    "Resourcefulness and Optimization", "Social, Contextual, and Aesthetic Intelligence",
    "Security and Trust Delegation", "Who's asking, and should I? (must-have)",
    "Emergencies (must-have)", "Feedback and recovery (must-have)",
    "Standing orders and watching", "Is it okay? (judging state)",
    "Improvising physical fixes", "New devices and new homes", "Reading the room",
    "Constraints beyond time", "Pets",
}


def parse_catalog(path):
    """Keep every nonblank physical line as a heading or an entry.

    IDs are content/section hashes plus duplicate occurrence, not line numbers.
    Original text includes fused heading prefixes; normalized text is separate.
    Byte spans are over the unchanged UTF-8/CRLF source, including line endings.
    """
    raw = Path(path).read_bytes()
    entries, headings, occurrences = [], [], Counter()
    section, mode, offset = "Unsectioned", "task", 0
    for number, data in enumerate(raw.splitlines(keepends=True), 1):
        start, offset = offset, offset + len(data)
        original = data.decode("utf-8").rstrip("\r\n")
        stripped = original.strip()
        if not stripped:
            continue
        fused = None
        if stripped == "ADAPTABILITY TASKS":
            mode, section = "adaptability", "General adaptability"
            headings.append({"source_line": number, "original_text": original,
                             "byte_start": start, "byte_end": offset})
            continue
        if mode != "adaptability":
            fused = re.match(r"^(.+?)- (.+)$", stripped) if not stripped.startswith("- ") else None
        is_heading = (stripped in ADAPT_HEADINGS if mode == "adaptability" else
                      stripped in APPENDIX_HEADINGS or
                      (not stripped.startswith("- ") and fused is None and
                       not stripped.endswith(".") and len(stripped) < 100))
        if is_heading:
            section = stripped
            if section == "Emergency, Safety, and First Aid":
                mode = "appendix"
            headings.append({"source_line": number, "original_text": original,
                             "byte_start": start, "byte_end": offset})
            continue
        if fused:
            section, text = fused.groups()
        else:
            text = stripped[2:] if stripped.startswith("- ") else stripped
        kind = mode
        if mode != "adaptability":
            if section.startswith("Human-assistance primitives") or text.startswith("Primitives:"):
                kind = "primitive"
            elif section.startswith("Situational variations") or text.startswith("Situational variations."):
                kind = "variation"
        content_hash = hashlib.sha256((kind + "\0" + section + "\0" + text).encode()).hexdigest()[:14]
        occurrences[content_hash] += 1
        prefix = {"task": "T", "appendix": "X", "adaptability": "A", "primitive": "P", "variation": "V"}[kind]
        eid = "{}-{}-{:02d}".format(prefix, content_hash, occurrences[content_hash])
        incomplete = kind == "adaptability" and text == "intentionally left out, and where everything belongs."
        entries.append({
            "id": eid, "kind": kind, "section": section, "original_text": original,
            "text": text, "source_line": number, "byte_start": start, "byte_end": offset,
            "source": "catalog/raw/household_catalog.txt", "fused_heading": bool(fused),
            "duplicate_group": hashlib.sha256(text.casefold().encode()).hexdigest()[:14],
            "integrity": "INCOMPLETE" if incomplete else "COMPLETE",
            "reconstruction_note": ("Possibly the end of a tidy-up instruction distinguishing intentional arrangements from clutter; original opening unavailable. Do not invent it." if incomplete else ""),
        })
    accounted = {x["source_line"] for x in entries + headings}
    nonblank = {n for n, line in enumerate(raw.splitlines(), 1) if line.strip()}
    if accounted != nonblank or len(accounted) != len(entries) + len(headings):
        raise ValueError("Catalog source accounting failed")
    return entries, {"source_sha256": hashlib.sha256(raw).hexdigest(),
                     "source_bytes": len(raw), "nonblank_lines": len(nonblank),
                     "entries": len(entries), "headings": headings,
                     "counts_by_kind": dict(Counter(e["kind"] for e in entries)),
                     "sections": sorted({e["section"] for e in entries})}


def write_catalog(root=ROOT):
    entries, manifest = parse_catalog(root / "catalog/raw/household_catalog.txt")
    write_jsonl(root / "catalog/entries.jsonl", entries)
    (root / "catalog/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entries, manifest


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


if __name__ == "__main__":
    _, summary = write_catalog()
    print(json.dumps({k: v for k, v in summary.items() if k not in ("headings", "sections")}, indent=2))
