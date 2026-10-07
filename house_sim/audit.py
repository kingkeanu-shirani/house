"""Evidence-based catalog coverage; unsupported requirements remain explicit."""
import csv
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .catalog import ROOT, read_jsonl, write_jsonl
from .compiler import compile_specs
from .engine import HouseEnv
from .predicates import digest
from .reference import solve, wrong
from .scenarios import build


def fingerprint(root=ROOT):
    paths = sorted((root / "house_sim").glob("*.py")) + sorted((root / "house_sim/data").glob("*.json"))
    return digest({str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in paths})


def verify(root=ROOT, seeds=(0, 1, 2, 41, 97), emit=print):
    entries, specs, manifest = compile_specs(root)
    implementation = fingerprint(root)
    evidence, total = [], sum(s["family"] is not None for s in specs)
    started = time.monotonic()
    for spec in specs:
        if not spec["family"]: continue
        record = {"catalog_id": spec["catalog_id"], "implementation_hash": implementation,
                  "spec_hash": digest(spec), "fidelity": "S", "trials": [], "passed": False}
        for seed in seeds:
            trial = {"seed": seed, "passed": False}
            try:
                config, task, oracle = build(spec, seed)
                trial["initial_hash"] = digest(config)
                trial["task_hash"] = digest(task)
                env = HouseEnv(config, task, seed)
                initial_passes = env.task_complete()
                env.step("wait", seconds=min(task["time_budget_s"], 600))
                idle_passes = env.task_complete()
                env = HouseEnv(config, task, seed)
                reference = solve(env, oracle)
                bad = HouseEnv(config, task, seed)
                wrong_result = wrong(bad, oracle)
                trial.update(initial_fails=not initial_passes, idle_fails=not idle_passes,
                             reference_passes=reference["success"], wrong_fails=not wrong_result["success"],
                             reference_steps=reference["steps"], reference_state_hash=digest(env.privileged_state()),
                             wrong_state_hash=digest(bad.privileged_state()))
                trial["passed"] = all(trial[k] for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails"))
                if not trial["passed"]:
                    trial["failure_details"] = {"reference": reference, "wrong": wrong_result}
            except Exception as exc:
                trial["error"] = type(exc).__name__ + ": " + str(exc)
            record["trials"].append(trial)
        record["passed"] = bool(record["trials"]) and all(t["passed"] for t in record["trials"])
        evidence.append(record)
        if len(evidence) % 100 == 0:
            write_jsonl(root / "reports/verification.jsonl", evidence)
            emit("Verified {}/{} candidate entries; {} passed all seeds".format(len(evidence), total, sum(e["passed"] for e in evidence)), flush=True)
    write_jsonl(root / "reports/verification.jsonl", evidence)
    run = {"generated_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": round(time.monotonic() - started, 3),
           "implementation_hash": implementation, "source_sha256": manifest["source_sha256"],
           "seeds": list(seeds), "candidate_entries": total, "passed_entries": sum(e["passed"] for e in evidence),
           "failed_entries": [e["catalog_id"] for e in evidence if not e["passed"]]}
    (root / "reports/verification_run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    summary = report(root)
    emit(json.dumps(summary, indent=2))
    return run


def report(root=ROOT):
    entries = read_jsonl(root / "catalog/entries.jsonl")
    specs = {s["catalog_id"]: s for s in read_jsonl(root / "tasks/specs.jsonl")}
    evidence_path = root / "reports/verification.jsonl"
    evidence = {r["catalog_id"]: r for r in read_jsonl(evidence_path)} if evidence_path.exists() else {}
    current_hash = fingerprint(root)
    rows = []
    for entry in entries:
        spec = specs[entry["id"]]
        proof = evidence.get(entry["id"], {})
        status, reason = spec["status"], spec["reason"]
        verified = bool(proof.get("passed")) and proof.get("implementation_hash") == current_hash and proof.get("spec_hash") == digest(spec)
        if verified:
            status, reason = "DONE", "Reference passes and idle/wrong baselines fail for all recorded seeds; bounded symbolic fidelity only."
        elif spec["family"]:
            status = "STUB"
            reason = "Missing, stale, or failed verification; not counted complete."
        rows.append({"id": entry["id"], "kind": entry["kind"], "section": entry["section"],
                     "text": entry["text"], "source_line": entry["source_line"], "status": status,
                     "fidelity": "S" if verified else "unverified", "target_fidelity": spec["target_fidelity"],
                     "family": spec["family"] or "", "parameters": json.dumps(spec["parameters"], sort_keys=True),
                     "risk_level": spec["risk_level"], "risk_review": "heuristic triage; runtime guards apply",
                     "world": verified, "actions": verified, "spec": bool(spec["family"]), "verification": verified,
                     "seeds": ";".join(str(t["seed"]) for t in proof.get("trials", [])) if verified else "",
                     "reason": reason, "physical_limit": "No geometry/physics validation" if spec["target_fidelity"] != "S" else "Symbolic only"})
    with (root / "coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    statuses = Counter(r["status"] for r in rows)
    task_rows = [r for r in rows if r["kind"] in ("task", "adaptability", "appendix")]
    summary = {"total_entries": len(rows), "catalog_sections": len({r["section"] for r in rows}),
               "statuses": dict(statuses), "executable_task_entries": sum(r["status"] == "DONE" for r in task_rows),
               "task_requirement_entries": len(task_rows),
               "coverage_percent_all_entries": round(100 * statuses["DONE"] / len(rows), 2),
               "fidelity_done": dict(Counter(r["fidelity"] for r in rows if r["status"] == "DONE")),
               "full_catalog_complete": all(r["status"] == "DONE" for r in rows),
               "implementation_hash": current_hash}
    (root / "reports/coverage_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sections = defaultdict(Counter)
    for row in rows: sections[row["section"]][row["status"]] += 1
    lines = ["# Coverage report", "", "Catalog representation is complete. Implementation coverage is partial.", "",
             "DONE means verified only within the documented symbolic scenario distribution.", "",
             "| Section | DONE S | STUB | TODO | BLOCKED |", "|---|---:|---:|---:|---:|"]
    for section, counts in sections.items():
        lines.append("| {} | {} | {} | {} | {} |".format(section.replace("|", "/"), counts["DONE"], counts["STUB"], counts["TODO"], counts["BLOCKED"]))
    lines.extend(["", "## STUB and BLOCKED entries", ""])
    lines.extend("- {} — {}: {} ({})".format(r["id"], r["status"], r["text"], r["reason"]) for r in rows if r["status"] in ("STUB", "BLOCKED"))
    lines.extend(["", "All TODO entries and reasons are retained in coverage.csv and reports/pending.jsonl."])
    (root / "reports/COVERAGE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_jsonl(root / "reports/pending.jsonl", [r for r in rows if r["status"] != "DONE"])
    return summary


def demo(root=ROOT, emit=print):
    entries = {e["id"]: e for e in read_jsonl(root / "catalog/entries.jsonl")}
    specs = read_jsonl(root / "tasks/specs.jsonl")
    with (root / "coverage.csv").open(encoding="utf-8") as stream:
        done = {r["id"] for r in csv.DictReader(stream) if r["status"] == "DONE"}
    seen, records = set(), []
    for spec in specs:
        section = entries[spec["catalog_id"]]["section"]
        if section in seen or spec["catalog_id"] not in done: continue
        seen.add(section)
        config, task, oracle = build(spec, 701)
        env = HouseEnv(config, task, 701)
        result = solve(env, oracle)
        if not result["success"]: raise RuntimeError("Held-out demo failed: " + spec["catalog_id"])
        path = root / "demos/replays" / (spec["catalog_id"] + ".json")
        env.save_replay(path)
        replayed = HouseEnv.replay(path)
        if not replayed.task_complete(): raise RuntimeError("Replay failed")
        records.append({"section": section, "catalog_id": spec["catalog_id"], "seed": 701,
                        "replay": str(path.relative_to(root)), "evaluation": result})
    sections = {e["section"] for e in entries.values()}
    result = {"demonstrated_sections": len(seen), "total_sections": len(sections),
              "unimplemented_sections": sorted(sections - seen), "scenarios": records}
    (root / "demos/manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    emit("Demonstrated and replay-verified {}/{} sections; missing sections are explicit in demos/manifest.json.".format(len(seen), len(sections)))
    return result
