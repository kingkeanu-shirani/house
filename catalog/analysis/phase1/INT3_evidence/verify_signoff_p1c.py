"""Verify the drafted second signatures of a gate record against its trial records (INT2, phase-1 finish; adapted by
INT3 for the close-out record P1C: CLAIMS may be "-", golden rows are verified against the golden attribution, the
signer is INT3-verified).

usage: python verify_signoff_p1c.py PREV_DIR CUR_DIR DRAFT CLAIMS OUT REPORT [--base-rev REV] [--golden-before FILE]
                                    [--golden-after FILE] [--golden-attributed KEY,KEY,...] [--golden-why TEXT]

PREV_DIR / CUR_DIR: gate record folders (ADOPT, P1F). DRAFT: `tier1_gate.py signoff ADOPT P1F` output for CUR.
CLAIMS: the earlier P1 draft (catalog/analysis/phase1/P1_signoff_draft.json) whose rows carry the claimed classes
(passed [old, new] and the changed fields; STATUS.md: wrong-plan list only with every wrong plan still failing, or a
FAIL -> PASS fix of this phase, none PASS -> FAIL). OUT: the verified sign-off (house-gate-signoff-v1, only rows that
verify); REPORT: every row's class, evidence and verdict, the P1 claims checked against CUR, rows rejected.

Classes (computed from the two trial records, never from the draft):
  wrong-list   PASS -> PASS, only the wrong-plan list differs; every wrong plan of the new record ran and fails, the
               reference passes, the initial and idle states fail (the spec still discriminates); the cause is named:
               the spec was edited (spec_hash), another variant runs on that seed, or the plan names differ
  fix          FAIL -> PASS; the new trial passes in full (same conditions as above)
  regression   PASS -> FAIL (never signed here)
  both-fail    FAIL -> FAIL with another verdict (never signed here)
Edit rows: the new spec hash is the hash of the authored spec in the working tree, the spec differs from its
version at --base-rev, the edit's JSON paths are listed, and every own-mode trial of the spec passes in CUR.
Coverage rows: listed with the previous and new success counts from audits.json; signed only when every success the
previous record counted for the action changed nothing but bookkeeping (no-op) or the action never succeeded there.
"""
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def trials(gate, name):
    path = os.path.join(gate, name)
    return {(r["key"], r["seed"]): r for r in read_jsonl(path)} if os.path.exists(path) else {}


def wrong_ok(t):
    wrong = t.get("wrong") or []
    return bool(wrong) and all(w.get("fails") is True and w.get("ran") is True for w in wrong)


def full_pass(t):
    """The trial passes and every part of the verdict says so (the verifier's own conditions)."""
    problems = []
    if t.get("passed") is not True:
        problems.append("passed {}".format(t.get("passed")))
    if t.get("reference_passes") is not True:
        problems.append("reference_passes {}".format(t.get("reference_passes")))
    if t.get("initial_fails") is not True:
        problems.append("initial_fails {}".format(t.get("initial_fails")))
    if t.get("idle_fails") is not True:
        problems.append("idle_fails {}".format(t.get("idle_fails")))
    if not wrong_ok(t):
        problems.append("wrong plans {}".format([(w.get("name"), w.get("fails"), w.get("ran"), w.get("error_class"))
                                                  for w in t.get("wrong") or []]))
    if t.get("perception") == "realistic" and t.get("perceivable") is False:
        problems.append("not perceivable")
    if t.get("error"):
        problems.append("error {}".format(str(t.get("error"))[:120]))
    return problems


def wrong_cause(a, b):
    """Why the wrong-plan list differs while both trials pass."""
    names_a = [w.get("name") for w in a.get("wrong") or []]
    names_b = [w.get("name") for w in b.get("wrong") or []]
    causes = []
    if a.get("spec_hash") != b.get("spec_hash"):
        causes.append("spec edited (spec_hash changed): wrong plans re-authored")
    if a.get("variant") != b.get("variant"):
        causes.append("variant {} -> {} on this seed (K-LEAK's hashed variant rule)".format(a.get("variant"),
                                                                                           b.get("variant")))
    if not causes:
        if names_a != names_b:
            causes.append("plan names differ with the same spec and variant: {} -> {}".format(
                sorted(set(names_a) - set(names_b))[:3], sorted(set(names_b) - set(names_a))[:3]))
        else:
            flags = [(w.get("name"), wa.get("fails"), wa.get("ran"), w.get("fails"), w.get("ran"))
                     for wa, w in zip(a.get("wrong") or [], b.get("wrong") or [])
                     if (wa.get("fails"), wa.get("ran")) != (w.get("fails"), w.get("ran"))]
            causes.append("plan flags differ: {}".format(flags[:3]) if flags else "unexplained")
    return causes


def classify(a, b):
    pa, pb = a.get("passed") is True, b.get("passed") is True
    if pa and pb:
        return "wrong-list"
    if not pa and pb:
        return "fix"
    if pa and not pb:
        return "regression"
    return "both-fail"


def spec_index(root):
    """{catalog id: (file, spec)} of the authored specs in a tree (first occurrence)."""
    out = {}
    folder = os.path.join(root, "tasks", "authored")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            data = json.load(f)
        for spec in data.get("specs", []) if isinstance(data, dict) else data:
            out.setdefault(spec.get("catalog_id"), (name, spec))
    return out


def git_spec(rev, name, cid):
    text = subprocess.run(["git", "show", "{}:tasks/authored/{}".format(rev, name)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout
    if not text:
        return None
    data = json.loads(text)
    for spec in data.get("specs", []) if isinstance(data, dict) else data:
        if spec.get("catalog_id") == cid:
            return spec
    return None


def json_paths(a, b, prefix=""):
    """Leaf paths where two JSON values differ (lists compared item by item)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += json_paths(a.get(k, "<absent>"), b.get(k, "<absent>"), "{}.{}".format(prefix, k) if prefix else k)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += json_paths(x, y, "{}[{}]".format(prefix, i))
        return out
    return [] if a == b else [prefix]


def main():
    prev_dir, cur_dir, draft_path, claims_path, out_path, report_path = sys.argv[1:7]
    base_rev = sys.argv[sys.argv.index("--base-rev") + 1] if "--base-rev" in sys.argv else "HEAD"
    sys.path.insert(0, ROOT)
    from house_sim.tasks.verify import spec_hash
    draft = json.load(open(draft_path, encoding="utf-8"))
    claims = json.load(open(claims_path, encoding="utf-8")) if claims_path != "-" else {"rows": []}
    golden = golden_context()
    recs = {mode: (trials(prev_dir, "specs_{}.jsonl".format(mode)), trials(cur_dir, "specs_{}.jsonl".format(mode)))
            for mode in ("own", "realistic")}
    specs = spec_index(ROOT)
    signed, rejected, report = [], [], []
    by_class = Counter()
    for row in draft["rows"]:
        if row["kind"] == "verdict":
            prev, cur = recs[row["mode"]]
            a, b = prev.get((row["key"], row["seed"])), cur.get((row["key"], row["seed"]))
            if a is None or b is None:
                rejected.append(dict(row_key(row), why="trial missing in a record"))
                continue
            cls = classify(a, b)
            problems = full_pass(b) if cls in ("wrong-list", "fix") else ["{}: never signed here".format(cls)]
            evidence = {"passed": [a.get("passed"), b.get("passed")], "fields": row.get("fields"),
                        "variant": [a.get("variant"), b.get("variant")],
                        "spec_edited": a.get("spec_hash") != b.get("spec_hash"),
                        "wrong_new": [[w.get("name"), w.get("fails"), w.get("ran"), w.get("reasons")]
                                      for w in b.get("wrong") or []]}
            if cls == "wrong-list":
                evidence["cause"] = wrong_cause(a, b)
                if row.get("fields") != ["wrong"]:
                    problems.append("fields beyond the wrong list: {}".format(row.get("fields")))
                if "unexplained" in evidence["cause"]:
                    problems.append("wrong list differs for no visible reason")
            if cls == "fix":
                evidence["old"] = full_pass(a)
            entry = dict(row_key(row), digest=row["digest"], **{"class": cls})
            by_class[(row["mode"], cls, not problems)] += 1
            report.append(dict(entry, evidence=evidence, problems=problems))
            if problems:
                rejected.append(dict(entry, why="; ".join(problems)))
            else:
                signed.append(dict(entry, evidence=short_evidence(cls, evidence)))
        elif row["kind"] == "edit":
            key = row["key"]
            name, spec = specs.get(key, (None, None))
            problems = []
            now = spec_hash(spec) if spec is not None else None
            if now != row["spec_hash"]:
                problems.append("spec hash {} is not the authored spec's {}".format(row["spec_hash"][:12],
                                                                                    (now or "-")[:12]))
            old = git_spec(base_rev, name, key) if name else None
            paths = json_paths(old, spec) if old is not None else ["<new spec>"]
            if not paths:
                problems.append("no difference from {}".format(base_rev))
            own = [t for (k, s), t in recs["own"][1].items() if k == key]
            bad = ["seed {}: {}".format(t["seed"], "; ".join(full_pass(t))) for t in own if full_pass(t)]
            if len(own) != 5:
                problems.append("{} own-mode trials (5 expected)".format(len(own)))
            problems += bad
            entry = {"kind": "edit", "key": key, "spec_hash": row["spec_hash"], "file": name}
            by_class[("edit", "edit", not problems)] += 1
            report.append(dict(entry, edited_paths=paths[:40], problems=problems))
            if problems:
                rejected.append(dict(entry, why="; ".join(problems)))
            else:
                signed.append(dict(entry, evidence="edited vs {}: {}{}; own mode 5/5 seeds pass".format(
                    base_rev, ", ".join(paths[:8]), " (+{} more)".format(len(paths) - 8) if len(paths) > 8 else "")))
        elif row["kind"] == "coverage":
            report.append(dict(row, problems=["coverage rows are judged separately (see the report)"]))
            rejected.append(dict(row, why="coverage: judged separately"))
        elif row["kind"] == "golden":
            problems, evidence = check_golden(row, golden)
            entry = {"kind": "golden", "key": row["key"], "digest": row["digest"]}
            by_class[("golden", row.get("change"), not problems)] += 1
            report.append(dict(entry, change=row.get("change"), evidence=evidence, problems=problems))
            if problems:
                rejected.append(dict(entry, why="; ".join(problems)))
            else:
                signed.append(dict(entry, evidence=evidence))
        else:
            report.append(dict(row, problems=["{} rows are not signed by this verifier".format(row["kind"])]))
            rejected.append(dict(row, why="{}: not signed by this verifier (the main session decides)".format(
                row["kind"])))
    # the P1 draft's claims checked against the final record
    claim_rows = []
    claim_counts = Counter()
    final = {(r["key"], r.get("seed"), r.get("mode")): r for r in report if r.get("kind") == "verdict"}
    final_edits = {r["key"]: r for r in report if r.get("kind") == "edit"}
    for row in claims["rows"]:
        if row["kind"] == "edit":
            now = final_edits.get(row["key"])
            if now is None:
                status = "no longer an edited spec in the final record" if row["key"] not in specs else \
                    "not an edit row of the final record"
            elif now["spec_hash"] != row["spec_hash"]:
                status = "edited again since P1 (new spec hash): P1's signature would not apply; verified under " \
                         "the final row" + ("" if not now["problems"] else " -- REJECTED there")
            else:
                status = "holds" if not now["problems"] else "claim does not hold: " + "; ".join(now["problems"])
            claim_counts[status.split(":")[0]] += 1
            claim_rows.append({"kind": "edit", "key": row["key"], "claimed": "spec edit", "status": status})
            continue
        claimed = "wrong-list" if row.get("passed") == [True, True] else \
            "fix" if row.get("passed") == [False, True] else \
            "regression" if row.get("passed") == [True, False] else "both-fail"
        now = final.get((row["key"], row["seed"], row["mode"]))
        if now is None:
            prev, cur = recs[row["mode"]]
            b = cur.get((row["key"], row["seed"]))
            status = "no verdict change vs ADOPT in the final record (nothing to sign)" if b is not None else \
                "trial missing in the final record"
        elif now["class"] != claimed:
            status = "claim does not hold: final class {} (claimed {})".format(now["class"], claimed)
        elif claimed in ("both-fail", "regression"):
            status = "class holds ({}), not signed: the trial fails in the final record".format(claimed)
        elif now["digest"] != row["digest"]:
            status = "class holds, verdict digest changed since P1 ({} -> {}): P1's signature would not apply" \
                     "".format(row["digest"], now["digest"]) + (" -- REJECTED in the final record: " +
                                                               "; ".join(now["problems"]) if now["problems"] else "")
        else:
            status = "holds" if not now["problems"] else "claim does not hold: " + "; ".join(now["problems"])
        claim_counts[status.split(" (")[0].split(":")[0]] += 1
        claim_rows.append({"kind": "verdict", "key": row["key"], "seed": row["seed"], "mode": row["mode"],
                           "claimed": claimed, "status": status})
    out = {"format": draft["format"], "gate": draft["gate"], "prev": draft["prev"], "signed_by": "INT3-verified",
           "date": draft.get("date"),
           "about": "Second signatures of gate record {} vs {}, drafted by `tier1_gate.py signoff` and verified row by "
                    "row against the two records' trials by INT3 (phase-1 close-out): only rows whose class verifies are "
                    "in rows; the rest are in rejected with the reason. The main session reviews it and commits it as "
                    "tools/orchestration/gate_policy/signoffs/{}.json (only the committed version counts)."
                    "".format(draft["gate"], draft["prev"], draft["gate"]),
           "rows": signed, "rejected": rejected,
           "summary": {"signed": Counter(r["kind"] + ":" + r.get("class", r["kind"]) + ":" + r.get("mode", "")
                                         for r in signed),
                       "rejected": len(rejected),
                       "p1_claims": dict(claim_counts)}}
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=False)
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"rows": report, "p1_claims": claim_rows,
                   "counts": {"{}|{}|{}".format(*k): v for k, v in sorted(by_class.items(), key=str)}}, f, indent=1)
    print("signed:", dict(out["summary"]["signed"]))
    print("rejected:", len(rejected))
    for r in rejected[:40]:
        print("  REJECTED", r.get("key"), r.get("seed"), r.get("mode"), r.get("class", r.get("kind")), "--",
              str(r.get("why"))[:300])
    print("P1 claims:", dict(claim_counts))
    for r in claim_rows:
        if r["status"] != "holds":
            print("  CLAIM", r["key"], r.get("seed"), r.get("mode"), r["claimed"], "--", r["status"][:260])


def golden_context():
    """--golden-before / --golden-after (room_golden.json files) and --golden-attributed (entry keys the A/B on frozen
    trees attributed to an owner's reported class) with --golden-why (the attribution)."""
    def arg(name):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    out = {"before": None, "after": None, "attributed": set(), "why": arg("--golden-why") or ""}
    for key in ("before", "after"):
        path = arg("--golden-" + key)
        if path:
            out[key] = json.load(open(path, encoding="utf-8"))
    if arg("--golden-attributed"):
        out["attributed"] = set(arg("--golden-attributed").split(","))
    return out


def golden_digests(data):
    """{entry key: digest} as the gate computes it (tier1_gate.golden_entries)."""
    sys.path.insert(0, os.path.join(ROOT, "tools", "orchestration"))
    import tier1_gate
    return tier1_gate.golden_entries(data)


def check_golden(row, golden):
    """A golden row verifies when the entry is one the A/B attributed, its digest is the re-recorded file's, and the
    entry changed only in the digests the attribution names: a run's reset / step state and observation digests (its
    actions, ok flags and seconds unchanged), a spec's final state digest (its evaluation, steps, time and pass
    unchanged)."""
    problems = []
    key = row["key"]
    if golden["after"] is None or golden["before"] is None:
        return ["golden files not given"], ""
    after = golden_digests(golden["after"])
    if after.get(key) != row["digest"]:
        problems.append("digest {} is not the re-recorded entry's {}".format(row["digest"], after.get(key)))
    if key not in golden["attributed"]:
        problems.append("entry not attributed by the A/B")
    section, _, name = key.partition("/")
    a = (golden["before"].get(section) or {}).get(name)
    b = (golden["after"].get(section) or {}).get(name)
    detail = ""
    if section == "runs" and isinstance(a, dict) and isinstance(b, dict):
        changed = set()
        for x, y in zip([a.get("reset")] + list(a.get("steps") or []), [b.get("reset")] + list(b.get("steps") or [])):
            changed |= {f for f in set(x or {}) | set(y or {}) if (x or {}).get(f) != (y or {}).get(f)}
        if len(a.get("steps") or []) != len(b.get("steps") or []):
            problems.append("step count changed")
        if not changed <= {"state", "obs"}:
            problems.append("run fields beyond the state / obs digests changed: {}".format(sorted(changed)))
        if a.get("problems") != b.get("problems"):
            problems.append("run problems changed")
        detail = "run: only reset/step {} digests changed ({} steps; actions, ok flags and seconds unchanged)".format(
            "/".join(sorted(changed)), len(b.get("steps") or []))
    elif section == "specs" and isinstance(a, dict) and isinstance(b, dict):
        changed = sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f))
        if changed != ["state"]:
            problems.append("spec fields beyond the final state digest changed: {}".format(changed))
        if b.get("passes") is not True:
            problems.append("the reference does not pass")
        detail = "spec: only the final state digest changed (passes, steps {}, time {} s, evaluation unchanged)".format(
            b.get("steps"), b.get("time_s"))
    else:
        problems.append("entry added or removed (not a re-record of an existing entry)")
    return problems, "{}; {}".format(detail, golden["why"]) if golden["why"] else detail


def row_key(row):
    return {k: row[k] for k in ("kind", "key", "seed", "mode") if k in row}


def short_evidence(cls, ev):
    if cls == "fix":
        return "FAIL -> PASS: new trial passes in full (reference passes, initial and idle states fail, every wrong " \
               "plan ran and fails); before: " + "; ".join(ev.get("old") or [])[:200]
    return "PASS -> PASS, wrong-plan list only; every wrong plan ran and fails; cause: " + "; ".join(ev["cause"])


if __name__ == "__main__":
    main()
