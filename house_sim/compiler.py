"""Conservative mappings from catalog entries to implemented scenario families.

Unmatched commands remain visible requirements. A keyword match is a candidate,
not DONE: verification and source-specific exclusions are required for coverage.
"""
import json
import re
from collections import Counter

from .catalog import ROOT, write_catalog, write_jsonl

COMPLEX = re.compile(r"\b(without|according|supervis|appropriat|safely|safe |when |where |if |before |after |under |behind |around |except |only |while |and |or |upstairs|downstairs|high |ceiling|self-clean|chemical|medicine|medication|knife|knives|grill|oven|burner|stove|grease|mold|mildew|grout|drain|scum|filter|seals|waterproof|dry-clean)", re.I)


def noun_count(text):
    return 2 if re.search(r"\b(items|objects|clothes|clothing|dishes|books|toys|shoes|towels|sheets|blankets|cups|plates|bowls|tools|supplies|keys|remotes|papers|bins|containers|cushions|plants)\b", text, re.I) else 1


def classify(entry):
    text, kind = entry["text"], entry["kind"]
    plain = text.rstrip(".").strip()
    if entry["integrity"] == "INCOMPLETE": return None, {}, "Source instruction is truncated; full intent cannot be reconstructed reliably."
    if kind == "variation": return None, {}, "Variation requirement retained; needs an axis-specific behavioral test, not just metadata."
    if kind == "primitive": return None, {}, "Action vocabulary requirement retained; catalog-level primitive conformance not yet audited."
    if kind == "appendix": return None, {}, "Compound appendix requirement needs decomposition and scenario-specific verification."
    if kind == "adaptability":
        mapping = {
            "Find my keys.": ("find", {"subject": "keys", "count": 1}),
            "Give me that.": ("clarify", {"subject": "cup", "count": 1}),
            "Put it over there.": ("clarify_destination", {"subject": "cup", "count": 1}),
            "Clean quietly.": ("quiet_clean", {"subject": "floor", "count": 1}),
            "Water anything that needs water.": ("water", {"subject": "plants", "count": 2}),
            "If it starts raining, close the windows.": ("rain_watch", {"subject": "windows", "count": 2}),
            "Tell me when the laundry’s done.": ("laundry_watch", {"subject": "laundry", "count": 1}),
        }
        for command, (family, params) in mapping.items():
            if text.startswith("“" + command + "”"):
                return family, params, ""
        return None, {}, "Requires a concrete intent model and source-specific success/side-effect tests; generic proxies are not credited."
    if COMPLEX.search(plain):
        return None, {}, "Context, method, timing, or hazard constraint is not fully grounded by an implemented family."
    params = {"count": noun_count(plain)}
    m = re.fullmatch(r"(Clean|Wipe|Scrub|Dust|Sweep|Vacuum|Mop|Wash|Rinse|Dry|Disinfect|Polish) (.+)", plain)
    if m:
        action, subject = m.groups()
        if action in ("Wash", "Rinse") and re.search(r"\b(window|windows|walls|floor|floors|deck|patio|driveway|walkway|porch|steps|exterior|furniture|shelves|counter|countertop|sink|bathtub)\b", subject, re.I):
            return None, {}, "Fixed-surface washing needs a water-delivery/runoff model; cannot be represented by moving the surface to a sink."
        # Large-area/whole-room and automatic equipment-cleaning tasks need a
        # multi-surface state, not one arbitrarily named cleanable object.
        if re.search(r"\b(room|bedroom|bathroom|kitchen|garage|shed|house|area|entryway|workspace|closet|pantry|freezer|washer|dryer|dishwasher|toilet|cage|aquarium|vehicle)\b", subject, re.I) and not re.search(r"\b(floor|table|counter|handle|exterior|window|deck|walkway|patio|driveway|porch|steps|shelves)\b", subject, re.I):
            return None, {}, "Whole-room/equipment cleaning requires all relevant surfaces or a specific procedure."
        if re.search(r"(hands|pet|dog|bird|fish|soil|growth)", subject, re.I):
            return None, {}, "Biological or contextual cleaning requirements need a dedicated scenario."
        return "clean", {**params, "subject": subject, "method": action.lower()}, ""
    m = re.fullmatch(r"(Fold|Unfold|Flatten|Coil|Uncoil|Tighten) (.+)", plain)
    if m:
        return "transform", {**params, "subject": m[2], "method": m[1].lower()}, ""
    m = re.fullmatch(r"(Open|Close|Lock|Unlock) (.+)", plain)
    if m:
        return "access", {**params, "subject": m[2], "method": m[1].lower()}, ""
    m = re.fullmatch(r"(?:Turn|Switch) (.+) (on|off)", plain)
    if m:
        return "switch", {**params, "subject": m[1], "method": "switch_" + m[2]}, ""
    m = re.fullmatch(r"(Start|Pause) (?:a |the )?(washer|washing machine|dryer|dishwasher)", plain, re.I)
    if m:
        return "appliance", {"count": 1, "subject": m[2].lower().replace("washing machine", "washer"), "method": m[1].lower()}, ""
    m = re.fullmatch(r"(?:Water) (.+)", plain)
    if m and re.search(r"(plants|pots|seedlings|flowers|shrubs)", m[1], re.I):
        return "water", {**params, "subject": m[1]}, ""
    m = re.fullmatch(r"(?:Fill|Refill) (.+)", plain)
    if m and re.search(r"(water|watering can|ice trays)", m[1], re.I):
        return "fill", {**params, "subject": m[1]}, ""
    m = re.fullmatch(r"(?:Count|Inventory) (.+)", plain)
    if m:
        return "inventory", {"subject": m[1], "count": 1}, ""
    m = re.fullmatch(r"(?:Report) (.+)", plain)
    if m:
        return "report", {"subject": m[1], "count": 1}, ""
    m = re.fullmatch(r"(?:Check|Verify) (.+?)(?: (?:are|is))? (closed|locked|off|open)", plain)
    if m:
        return "verify_state", {**params, "subject": m[1], "field": {"closed": "open", "locked": "locked", "off": "on", "open": "open"}[m[2]]}, ""
    m = re.fullmatch(r"Sort (.+) by (type|category|owner|color|material|size)", plain)
    if m:
        return "sort", {"subject": m[1], "count": 4, "field": m[2]}, ""
    m = re.fullmatch(r"Pick up (.+)", plain)
    if m and not re.search(r"(trash|waste|litter|debris|glass|branches|clutter|floor|table|shelf|drawer|cabinet|container|two hands)", m[1], re.I):
        return "pickup", {"subject": m[1], "count": 1}, ""
    m = re.fullmatch(r"Pick up an object from (?:the |a )?(floor|table|shelf|inside a drawer|inside a cabinet|inside a container)", plain)
    if m:
        return "pickup", {"subject": "object", "count": 1, "source": m[1]}, ""
    m = re.fullmatch(r"(?:Store|Retrieve) (.+)", plain)
    if m:
        return "relocate", {**params, "subject": m[1], "mode": plain.split()[0].lower()}, ""
    m = re.fullmatch(r"Put (.+) (?:away|into (.+)|in (.+)|on (.+))", plain)
    if m:
        destination = next((x for x in m.groups()[1:] if x), "storage")
        return "relocate", {**params, "subject": m[1], "destination_label": destination, "mode": "store"}, ""
    m = re.fullmatch(r"(?:Load|Unload) (?:a |the )?(dishwasher|washing machine|washer|dryer)", plain)
    if m:
        return "relocate", {"subject": "dishes" if m[1] == "dishwasher" else "clothing", "count": 2,
                            "destination_label": m[1] if plain.startswith("Load") else "storage", "mode": "load" if plain.startswith("Load") else "unload"}, ""
    m = re.fullmatch(r"(?:Bring|Fetch) (.+)", plain)
    if m and not re.search(r"\b(inside|outside|back|out|in|from|to|promptly)\b", m[1], re.I):
        return "relocate", {**params, "subject": m[1], "mode": "retrieve"}, ""
    return None, {}, "No fully grounded executable family yet; entry retained with its original wording."


def preliminary_risk(text):
    if re.search(r"(ladder|medicat|infant|baby|bath|lifting|stand up|electrical|breaker|power tool|gas|fire |burning|pool|knife|knives|hot |oil|intimate|medical|choking|EpiPen)", text, re.I): return 3
    if re.search(r"(fragile|heavy|pet|glass|clean|wash|sharp|child)", text, re.I): return 2
    return 1


def target_fidelity(text):
    if re.search(r"(pour|fold|cut|slice|liquid|knot|untangl|cloth|catch|thermal|boil|fry)", text, re.I): return "P"
    if re.search(r"(carry|carrying|door|stairs|stack|reach|navigate|clearance|fit|move|align)", text, re.I): return "G"
    return "S"


def compile_specs(root=ROOT):
    entries, manifest = write_catalog(root)
    specs = []
    for entry in entries:
        family, params, reason = classify(entry)
        command = entry["text"].split("”", 1)[0].lstrip("“") if entry["kind"] == "adaptability" else entry["text"]
        specs.append({"schema_version": 1, "catalog_id": entry["id"], "family": family,
                      "parameters": params, "instruction": command,
                      "phrasings": [command, "Please " + command[0].lower() + command[1:], "Could you " + command[0].lower() + command[1:].rstrip(".") + "?"],
                      "generator": {"name": family, "seeded": True, "parameters": params} if family else None,
                      "predicate_template": family if family else None,
                      "risk_level": preliminary_risk(entry["text"]), "target_fidelity": target_fidelity(entry["text"]),
                      "fidelity": "S" if family else "unimplemented",
                      "status": "STUB" if family else ("BLOCKED" if entry["integrity"] == "INCOMPLETE" else "TODO"),
                      "reason": reason or "Candidate mapping; must pass verification before coverage is DONE.",
                      "step_budget": 180, "time_budget_s": 1800,
                      "scope": "Bounded symbolic scenarios; no claim of continuous geometry, physical manipulation, perception, or unrestricted language understanding."})
    write_jsonl(root / "tasks/specs.jsonl", specs)
    write_jsonl(root / "tasks/id_to_family.jsonl", [{"catalog_id": s["catalog_id"], "family": s["family"], "parameters": s["parameters"]} for s in specs])
    return entries, specs, manifest


if __name__ == "__main__":
    entries, specs, manifest = compile_specs()
    print(json.dumps({"entries": len(entries), "candidate_entries": sum(s["family"] is not None for s in specs), "families": dict(Counter(s["family"] or "unimplemented" for s in specs))}, indent=2))
