"""Evidence-based catalog coverage, held-out demos and reports."""
import csv
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .catalog import ROOT, read_jsonl, write_jsonl
from .compiler import compile_specs
from .kernel.predicates import digest
from .tasks.verify import SEEDS, spec_hash, verify_many

FIDELITY_NOTE = {"S": "Symbolic state and predicates", "G": "Geometric grid/box checks (reach, clearance, fit, stacking, stairs)",
                 "P-approx": "Approximate lumped physical model (liquids, heat, cloth/knot topology); not rigid-body or fluid simulation"}


def fingerprint(root=ROOT):
    source = Path(__file__).resolve().parent
    # simulation code and data only: evaluation tooling (house_sim/eval) does not change outcomes
    paths = sorted(p for p in source.rglob("*.py") if "legacy_v1" not in p.parts and "__pycache__" not in p.parts
                   and "eval" not in p.relative_to(source).parts)
    paths += sorted(p for p in (source / "data").rglob("*.json"))
    templates = root / "tasks" / "templates"
    if templates.is_dir():
        paths += sorted(templates.glob("*.json"))
    return digest({p.relative_to(source.parent).as_posix(): p.read_text(encoding="utf-8") for p in paths})


def harness_fingerprint():
    """Digest of the evaluation tooling (house_sim/eval/**): recorded with agent runs so results scored by different
    harness code are never mixed, while simulation evidence (fingerprint()) stays valid across harness edits."""
    source = Path(__file__).resolve().parent / "eval"
    paths = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".html") and "__pycache__" not in p.parts)
    return digest({p.relative_to(source.parent).as_posix(): p.read_text(encoding="utf-8") for p in paths})


EVIDENCE = "reports/verification.jsonl"
EVIDENCE_RUN = "reports/verification_run.json"
# a run that forces a mode (perception, camera or house generation) for every spec is a check of the specs under
# that mode, not evidence of the specs as authored: its records go here and never replace the catalog's evidence
FORCED_EVIDENCE = "reports/verification_forced.jsonl"
FORCED_RUN = "reports/verification_forced_run.json"


def _evidence(root, path=EVIDENCE):
    path = root / path
    return {r["catalog_id"]: r for r in read_jsonl(path)} if path.exists() else {}


def verify(root=ROOT, seeds=SEEDS, emit=print, only=None, workers=None, perception=None, camera=None, gen=None):
    """Verify the authored candidate entries (tasks/verify.py verify_many) and write the evidence.

    Each spec runs in its own modes unless forced: perception ("oracle" | "realistic"), camera ("room" | "geometric")
    and gen (house generation 1 | 2; the M-GEN2 migration check, TIER1_SWEEP 19.3). A run without a forced mode is
    the catalog's evidence: reports/verification.jsonl and verification_run.json, then the coverage reports
    (report()). A forced run checks the specs under that mode instead: its records carry "forced" and go to
    reports/verification_forced.jsonl / verification_forced_run.json (merged with an earlier forced run of the same
    options when `only` narrows it), so the evidence the harness, the dataset exporter and the coverage count stays
    the evidence of the specs as authored. Returns the run record."""
    forced = {k: v for k, v in (("perception", perception), ("camera", camera), ("gen", gen)) if v is not None}
    entries, records, manifest = compile_specs(root)
    implementation = fingerprint(root)
    evidence_path, run_path = (FORCED_EVIDENCE, FORCED_RUN) if forced else (EVIDENCE, EVIDENCE_RUN)
    evidence = {}
    if only:
        evidence = _evidence(root, evidence_path)
        if forced:
            previous = _read_json(root / run_path) or {}
            if previous.get("forced") != forced:
                evidence = {}           # a different forced mode: never mix the records of two checks
    candidates = [r for r in records if r["status"] == "CANDIDATE" and (only is None or r["catalog_id"] in only or r["section"] in only or r.get("source_file") in only)]
    started = time.monotonic()
    def progress(i, total, result):
        if i % 100 == 0 or i == total:
            emit("Verified {}/{} candidate entries ({:.0f}s)".format(i, total, time.monotonic() - started), flush=True)
    results = verify_many([r["spec"] for r in candidates], seeds=seeds, workers=workers, progress=progress,
                          perception=perception, camera=camera, gen=gen)
    for record in results:
        record["implementation_hash"] = implementation
        if forced:
            record["forced"] = dict(forced)
        evidence[record["catalog_id"]] = record
    ordered = [evidence[r["catalog_id"]] for r in records if r["catalog_id"] in evidence]
    write_jsonl(root / evidence_path, ordered)
    run = {"generated_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": round(time.monotonic() - started, 3),
           "implementation_hash": implementation, "source_sha256": manifest["source_sha256"], "seeds": list(seeds),
           "candidate_entries": len(candidates), "passed_entries": sum(r["passed"] for r in results),
           "failed_entries": [r["catalog_id"] for r in results if not r["passed"]], "partial": only is not None}
    if forced:
        run["forced"] = dict(forced)
        run["evidence"] = evidence_path
    _write_text(root / run_path, json.dumps(run, indent=2) + "\n")
    if forced:
        emit("Forced check ({}): {}/{} candidate entries pass; records in {} (the catalog evidence {} and the "
             "coverage reports are unchanged)".format(", ".join("{}={}".format(k, v) for k, v in sorted(forced.items())),
                                                      run["passed_entries"], len(candidates), evidence_path, EVIDENCE))
        return run
    summary = report(root)
    emit(json.dumps(summary, indent=2))
    return run


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_text(path, text):
    """Reports are LF text on every platform (Path.write_text would write CRLF on Windows)."""
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def report(root=ROOT):
    records = read_jsonl(root / "tasks/specs.jsonl")
    entries = {e["id"]: e for e in read_jsonl(root / "catalog/entries.jsonl")}
    evidence = _evidence(root)
    current = fingerprint(root)
    rows = []
    for record in records:
        proof = evidence.get(record["catalog_id"], {})
        status, reason = record["status"], record["reason"]
        verified = False
        if record["status"] == "CANDIDATE":
            verified = bool(proof.get("passed")) and proof.get("implementation_hash") == current and proof.get("spec_hash") == spec_hash(record["spec"])
            if verified:
                status = "DONE"
                reason = "Reference passes; idle and every plausible-wrong plan fail on all seeds."
                if record.get("reconstructed"):
                    reason += " Implemented against the documented reconstruction of a truncated source line."
            else:
                status = "STUB"
                reason = "Authored but verification is missing, stale or failing: " + _why(proof, current, record)
        elif record["status"] == "INVALID":
            status = "STUB"
        fidelity = record.get("fidelity") if verified else "unverified"
        rows.append({"id": record["catalog_id"], "kind": record["kind"], "section": record["section"], "text": record["text"],
                     "source_line": record["source_line"], "status": status, "fidelity": fidelity,
                     "target_fidelity": record.get("target_fidelity") or "", "family": record.get("family") or "",
                     "title": record.get("title") or "", "risk_level": record.get("risk_level") if record.get("risk_level") is not None else "",
                     "world": verified, "actions": verified, "spec": record["status"] in ("CANDIDATE", "INVALID"), "verification": verified,
                     "seeds": ";".join(str(t["seed"]) for t in proof.get("trials", [])) if verified else "",
                     "reason": reason, "fidelity_note": FIDELITY_NOTE.get(fidelity, "Not implemented"),
                     "reconstructed": bool(record.get("reconstructed")), "source_file": record.get("source_file") or ""})
    with (root / "coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    statuses = Counter(r["status"] for r in rows)
    summary = {"total_entries": len(rows), "catalog_sections": len({r["section"] for r in rows}), "statuses": dict(statuses),
               "coverage_percent_all_entries": round(100 * statuses["DONE"] / len(rows), 2),
               "fidelity_done": dict(Counter(r["fidelity"] for r in rows if r["status"] == "DONE")),
               "by_kind": {k: dict(Counter(r["status"] for r in rows if r["kind"] == k)) for k in sorted({r["kind"] for r in rows})},
               "full_catalog_complete": all(r["status"] == "DONE" for r in rows), "implementation_hash": current}
    _write_text(root / "reports/coverage_summary.json", json.dumps(summary, indent=2) + "\n")
    sections = defaultdict(Counter)
    fidelity = defaultdict(Counter)
    for row in rows:
        sections[row["section"]][row["status"]] += 1
        if row["status"] == "DONE":
            fidelity[row["section"]][row["fidelity"]] += 1
    lines = ["# Coverage report", "", "DONE means verified within the documented simulated scenario distribution (5 seeds,",
             "reference passes; idle and plausible-wrong baselines fail). It is not evidence of real-robot competence.", "",
             "| Section | DONE | S | G | P-approx | STUB | TODO | BLOCKED |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for section, counts in sections.items():
        f = fidelity[section]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(section.replace("|", "/"), counts["DONE"], f["S"], f["G"], f["P-approx"],
                                                                          counts["STUB"], counts["TODO"], counts["BLOCKED"]))
    lines.extend(["", "## STUB and BLOCKED entries", ""])
    lines.extend("- {} — {}: {} ({})".format(r["id"], r["status"], r["text"], r["reason"]) for r in rows if r["status"] in ("STUB", "BLOCKED"))
    lines.extend(["", "TODO entries and reasons are retained in coverage.csv and reports/pending.jsonl."])
    _write_text(root / "reports/COVERAGE.md", "\n".join(lines) + "\n")
    write_jsonl(root / "reports/pending.jsonl", [r for r in rows if r["status"] != "DONE"])
    return summary


def _why(proof, current, record):
    if not proof:
        return "not yet verified"
    if proof.get("implementation_hash") != current:
        return "implementation changed since verification"
    if proof.get("spec_hash") != spec_hash(record["spec"]):
        return "spec changed since verification"
    reasons = []
    for trial in proof.get("trials", []):
        if trial.get("passed"):
            continue
        if trial.get("error"):
            reasons.append("seed {}: {}".format(trial["seed"], trial["error"][:160]))
        else:
            parts = [k for k in ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran") if not trial.get(k)]
            detail = trial.get("reference_error") or trial.get("reference_failures") or ""
            reasons.append("seed {}: {} {}".format(trial["seed"], ",".join(parts), str(detail)[:160]))
    return "; ".join(reasons[:2]) or "failed"


def demo(root=ROOT, emit=print, seed=701):
    """Held-out seed demo for one DONE entry per section, saved as replays and replay-verified."""
    from .kernel.engine import HouseEnv
    from .tasks.runner import build, run_plan
    with (root / "coverage.csv").open(encoding="utf-8") as stream:
        done = {r["id"] for r in csv.DictReader(stream) if r["status"] == "DONE"}
    records = read_jsonl(root / "tasks/specs.jsonl")
    sections = []
    seen, scenarios, failures = set(), [], []
    (root / "demos/replays").mkdir(parents=True, exist_ok=True)
    for record in records:
        if record["section"] not in sections:
            sections.append(record["section"])
        if record["section"] in seen or record["catalog_id"] not in done:
            continue
        try:
            config, task, oracle = build(record["spec"], seed)
            env = HouseEnv(config, task, seed)
            run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
            result = env.evaluate()
            if not result["success"]:
                failures.append(record["catalog_id"])
                continue
            path = root / "demos/replays" / (record["catalog_id"] + ".json")
            env.save_replay(path)
            if not HouseEnv.replay(path).task_complete():
                failures.append(record["catalog_id"])
                continue
        except Exception as exc:
            failures.append("{}: {}".format(record["catalog_id"], exc))
            continue
        seen.add(record["section"])
        scenarios.append({"section": record["section"], "catalog_id": record["catalog_id"], "seed": seed,
                          "replay": "demos/replays/{}.json".format(record["catalog_id"]),
                          "evaluation": {k: result[k] for k in ("success", "steps", "time_s", "score")}})
    result = {"demonstrated_sections": len(seen), "total_sections": len(sections),
              "missing_sections": [s for s in sections if s not in seen], "failures": failures, "scenarios": scenarios}
    _write_text(root / "demos/manifest.json", json.dumps(result, indent=2) + "\n")
    emit("Demonstrated and replay-verified {}/{} sections on held-out seed {}.".format(len(seen), len(sections), seed))
    return result
