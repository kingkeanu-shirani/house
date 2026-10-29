"""Verify the drafted second signatures of gate record P2-S2 (packages P2-S1+P2-S2) against its previous record P1C,
row by row, from the two records' trials and the room golden (the P2-S2 merge gate; adapted from
catalog/analysis/phase1/INT3_evidence/verify_signoff_p1c.py).

usage: python verify_signoff_p2s2.py PREV_DIR CUR_DIR DRAFT OUT REPORT --golden-before FILE --golden-after FILE
                                     --golden-attrib FILE [--golden-why TEXT] [--base-rev REV]

PREV_DIR / CUR_DIR: gate record folders (P1C, P2-S2). DRAFT: `tier1_gate.py signoff P1C P2-S2 --package
P2-S1+P2-S2` output. OUT: the verified sign-off (house-gate-signoff-v1, signed_by "P2-S2-merge-verified": only the
rows that verify); REPORT: every row's class, evidence and verdict.

Classes (computed from the two trial records, never from the draft):
  wrong-list   PASS -> PASS, only the wrong-plan list differs; every wrong plan of the new record ran and fails, the
               reference passes, the initial and idle states fail (the spec still discriminates)
  fix          FAIL -> PASS; the new trial passes in full
  regression   PASS -> FAIL (never signed here)
  both-fail    FAIL -> FAIL with another verdict (never signed here)
Golden rows: the entry is one golden_attrib.py attributed (re-run on the merged tree: the current final state digest
equals the re-recorded entry, and the same state with WP2's logs.gone removed has HEAD's digest), its digest is the
re-recorded file's entry digest (tier1_gate.golden_entries), only the final state digest changed, the reference passes.
Edit rows: as INT3 (the spec's new hash is the working tree's, it differs from --base-rev, 5/5 own-mode trials pass).
Coverage, gate-change and other rows are not signed here (the main session decides).
"""
import json
import os
import subprocess
import sys
from collections import Counter

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
    problems = []
    for field, want in (("passed", True), ("reference_passes", True), ("initial_fails", True), ("idle_fails", True)):
        if t.get(field) is not want:
            problems.append("{} {}".format(field, t.get(field)))
    if not wrong_ok(t):
        problems.append("wrong plans {}".format([(w.get("name"), w.get("fails"), w.get("ran"), w.get("error_class"))
                                                  for w in t.get("wrong") or []]))
    if t.get("perception") == "realistic" and t.get("perceivable") is False:
        problems.append("not perceivable")
    if t.get("error"):
        problems.append("error {}".format(str(t.get("error"))[:120]))
    return problems


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
    out = {}
    folder = os.path.join(root, "tasks", "authored")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
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


def arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def golden_entries(data):
    sys.path.insert(0, os.path.join(ROOT, "tools", "orchestration"))
    import tier1_gate
    return tier1_gate.golden_entries(data)


def check_golden(row, ctx):
    problems = []
    key = row["key"]
    after = golden_entries(ctx["after"])
    if after.get(key) != row["digest"]:
        problems.append("digest {} is not the re-recorded entry's {}".format(row["digest"], after.get(key)))
    section, _, name = key.partition("/")
    attrib = ctx["attrib"].get(name) if section == "specs" else None
    if attrib is None:
        problems.append("entry not attributed by golden_attrib.py")
    else:
        if not attrib.get("current_equals_work"):
            problems.append("the merged tree's final state digest is not the re-recorded one")
        if not attrib.get("head_digest_after_strip"):
            problems.append("removing logs.gone does not restore HEAD's digest")
    a = (ctx["before"].get(section) or {}).get(name)
    b = (ctx["after"].get(section) or {}).get(name)
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return problems + ["entry added or removed (not a re-record)"], ""
    changed = sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f))
    if changed != ["state"]:
        problems.append("spec fields beyond the final state digest changed: {}".format(changed))
    if b.get("passes") is not True:
        problems.append("the reference does not pass")
    gone = (attrib or {}).get("gone") or []
    detail = ("spec: only the final state digest changed (passes, steps {}, time {} s, evaluation unchanged); the "
              "merged tree's reference reproduces the entry and, without logs.gone ({} destroyed: {}), HEAD's digest "
              "(golden_attrib.py)").format(b.get("steps"), b.get("time_s"), len(gone), ", ".join(gone[:4]))
    return problems, detail + ("; " + ctx["why"] if ctx["why"] else "")


def main():
    prev_dir, cur_dir, draft_path, out_path, report_path = sys.argv[1:6]
    base_rev = arg("--base-rev") or "HEAD"
    sys.path.insert(0, ROOT)
    from house_sim.tasks.verify import spec_hash
    draft = json.load(open(draft_path, encoding="utf-8"))
    attrib_rows = json.load(open(arg("--golden-attrib"), encoding="utf-8"))["rows"]
    ctx = {"before": json.load(open(arg("--golden-before"), encoding="utf-8")),
           "after": json.load(open(arg("--golden-after"), encoding="utf-8")),
           "attrib": {r["key"]: r for r in attrib_rows}, "why": arg("--golden-why") or ""}
    recs = {mode: (trials(prev_dir, "specs_{}.jsonl".format(mode)), trials(cur_dir, "specs_{}.jsonl".format(mode)))
            for mode in ("own", "realistic")}
    specs = spec_index(ROOT)
    signed, rejected, report = [], [], []
    by_class = Counter()
    for row in draft["rows"]:
        kind = row["kind"]
        if kind == "verdict":
            prev, cur = recs[row["mode"]]
            a, b = prev.get((row["key"], row["seed"])), cur.get((row["key"], row["seed"]))
            entry = {"kind": "verdict", "key": row["key"], "seed": row["seed"], "mode": row["mode"],
                     "digest": row["digest"]}
            if a is None or b is None:
                rejected.append(dict(entry, why="trial missing in a record"))
                continue
            cls = classify(a, b)
            problems = full_pass(b) if cls in ("wrong-list", "fix") else ["{}: never signed here".format(cls)]
            if not row.get("reviewed_by_package"):
                problems.append("no reviewed row of the package (first signature missing)")
            entry["class"] = cls
            by_class[(row["mode"], cls, not problems)] += 1
            report.append(dict(entry, fields=row.get("fields"), detail=row.get("detail"), problems=problems,
                               passed=[a.get("passed"), b.get("passed")]))
            if problems:
                rejected.append(dict(entry, why="; ".join(problems)))
            else:
                signed.append(dict(entry, evidence="{}: new trial passes in full".format(cls)))
        elif kind == "edit":
            key = row["key"]
            name, spec = specs.get(key, (None, None))
            problems = []
            now = spec_hash(spec) if spec is not None else None
            if now != row["spec_hash"]:
                problems.append("spec hash is not the authored spec's")
            old = git_spec(base_rev, name, key) if name else None
            if old == spec:
                problems.append("no difference from {}".format(base_rev))
            own = [t for (k, s), t in recs["own"][1].items() if k == key]
            problems += ["seed {}: {}".format(t["seed"], "; ".join(full_pass(t))) for t in own if full_pass(t)]
            if len(own) != 5:
                problems.append("{} own-mode trials (5 expected)".format(len(own)))
            entry = {"kind": "edit", "key": key, "spec_hash": row["spec_hash"], "file": name}
            by_class[("edit", "edit", not problems)] += 1
            report.append(dict(entry, problems=problems))
            (rejected.append(dict(entry, why="; ".join(problems))) if problems else
             signed.append(dict(entry, evidence="edited vs {}; own mode 5/5 seeds pass".format(base_rev))))
        elif kind == "golden":
            problems, evidence = check_golden(row, ctx)
            if not row.get("reviewed_by_package"):
                problems.append("no reviewed golden row of the package (first signature missing)")
            entry = {"kind": "golden", "key": row["key"], "digest": row["digest"]}
            by_class[("golden", row.get("change"), not problems)] += 1
            report.append(dict(entry, change=row.get("change"), evidence=evidence, problems=problems))
            (rejected.append(dict(entry, why="; ".join(problems))) if problems else
             signed.append(dict(entry, evidence=evidence)))
        else:
            report.append(dict(row, problems=["{} rows are not signed by this verifier".format(kind)]))
            rejected.append(dict(row, why="{}: not signed by this verifier (the main session decides)".format(kind)))
    out = {"format": draft["format"], "gate": draft["gate"], "prev": draft["prev"], "signed_by": "P2-S2-merge-verified",
           "date": draft.get("date"),
           "about": "Second signatures of gate record {} vs {} (packages P2-S1+P2-S2), drafted by `tier1_gate.py signoff "
                    "{} {} --package P2-S1+P2-S2` and verified row by row against the two records' trials and the room "
                    "golden by the P2-S2 merge gate (catalog/analysis/phase2/P2-S2_evidence/verify_signoff_p2s2.py): "
                    "only rows that verify are in rows; the rest are in rejected with the reason. The main session "
                    "reviews it and commits it as tools/orchestration/gate_policy/signoffs/{}.json (only the "
                    "committed version counts).".format(draft["gate"], draft["prev"], draft["prev"], draft["gate"],
                                                         draft["gate"]),
           "rows": signed, "rejected": rejected,
           "summary": {"signed": dict(Counter(r["kind"] + ":" + r.get("class", "") + ":" + r.get("mode", "")
                                              for r in signed)),
                       "rejected": len(rejected)}}
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"rows": report, "counts": {"{}|{}|{}".format(*k): v for k, v in sorted(by_class.items(), key=str)}},
                  f, indent=1)
    print("signed:", out["summary"]["signed"])
    print("rejected:", len(rejected))
    for r in rejected[:60]:
        print("  REJECTED", r.get("kind"), r.get("key"), r.get("seed"), r.get("mode"), r.get("class", ""), "--",
              str(r.get("why"))[:300])


if __name__ == "__main__":
    main()
