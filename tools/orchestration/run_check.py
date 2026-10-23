"""Orchestrator-run spec checks with a hash-chained check log (REVIEW_REPORT H15 ORCHESTRATOR-VERIFY, 4.6; P4 A3).

Agents never self-certify: the workflow (or the main session) runs this tool itself after every authoring or review
step, and the batch's verdict is what the tool recorded, not the agent's own `verified_passing` list.

usage:
  python tools/orchestration/run_check.py run FILE [FILE ...] | --all [--ids ID ...] [--seeds S ...] [--workers N]
             [--perception oracle|realistic] [--camera room|geometric] [--gen 1|2]
             [--claimed ID ... | --claimed-file FILE] [--role ROLE] [--agent LABEL] [--batch LABEL]
             [--log PATH] [--json OUT] [--allowlist]
  python tools/orchestration/run_check.py verify [--log PATH] [--rerun N] [--workers N]
  python tools/orchestration/run_check.py show [--log PATH] [--last N]

`run` verifies every spec of the files (house_sim.tasks.verify.verify_many: initial, idle, reference and wrong plans
on every seed, each spec in its own perception and camera mode unless forced, exactly what `python -m house_sim
--check` does), then appends one entry to the check log (default catalog/review/check_log/orchestrator.jsonl):

  {"format", "seq", "prev" (the previous entry's digest), "date", "role", "agent", "batch",
   "files": [{"path", "sha256", "specs"}], "options": {"seeds", "perception", "camera", "gen", "ids"},
   "code": {"code_fingerprint", "harness_fingerprint", "actions_fingerprint"}, "python", "platform",
   "results": [{"catalog_id", "file", "spec_hash", "verdict": "pass" | "fail", "failing_seeds", "reasons"}],
   "passing", "failing", "claimed", "mismatch": {"claimed_not_passing", "passing_not_claimed"}, "wall_s",
   "workers", "digest"}

`digest` is the sha256 of the entry's canonical JSON without it, and `prev` chains every entry to the one before,
so an entry edited, removed or inserted after the fact breaks the chain (`verify`). The chain is evidence of what
the tool recorded, not of who ran it: `verify --rerun N` re-runs the last N entries whose files and code are
unchanged and fails when a verdict differs, which is what makes a fabricated entry useless. The last line printed is
a one-line JSON summary (log, seq, digest, passing, failing, mismatch) for a workflow agent to return verbatim.
Exit status: 0 when every checked spec passes and the claimed list (when given) matches; 1 otherwise; 2 on usage.
--allowlist (CI's spec sample; phase-1 red team): a spec whose every failing seed is excused by an entry of the tier-1
gate's committed allowlist (tools/orchestration/gate_policy/known_failing.json, read from git HEAD like the gate does)
is listed under "allowlisted" and does not fail the run, so a red CI means a new regression; "failing" still lists
it.

The per-domain check log of docs/design/VERIFY_HARDENING.md 12.4 (written by --check / --verify themselves) lives in
the same folder under its own file names; this log records the orchestrator's runs.

Stdlib only; Python 3.9+. No network.
"""
import argparse
import glob
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = "catalog/review/check_log/orchestrator.jsonl"
FORMAT = "house-check-log-v1"
LOCK_WAIT_S = 120.0
TRIAL_FLAGS = ("initial_fails", "idle_fails", "reference_passes", "wrong_fails", "wrong_ran")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical(entry):
    """The bytes an entry's digest covers: its JSON without "digest", keys sorted, no spaces."""
    body = {k: v for k, v in entry.items() if k != "digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")


def entry_digest(entry):
    return hashlib.sha256(canonical(entry)).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rel(path, root=ROOT):
    path = Path(path).resolve()
    try:
        return path.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_log(path):
    """Every entry of a check log, in order ([] when it does not exist). A line that is not JSON is returned as
    {"_unreadable": line} so that verify reports it."""
    entries = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    entries.append({"_unreadable": line[:200]})
    except OSError:
        pass
    return entries


class LogLock:
    """An exclusive lock file beside the log (O_EXCL), so concurrent runs append to one unbroken chain."""

    def __init__(self, log_path, wait_s=LOCK_WAIT_S):
        self.path = Path(str(log_path) + ".lock")
        self.wait_s = wait_s

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() - start > self.wait_s:
                    raise SystemExit("check log is locked ({}); remove the lock file if no run is active".format(
                        self.path))
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


def append_entry(log_path, entry):
    """Chain `entry` to the log's last entry (seq, prev), seal it with its digest and append it (LF, one line)."""
    log_path = Path(log_path)
    with LogLock(log_path):
        entries = read_log(log_path)
        last = entries[-1] if entries else None
        entry = dict(entry)
        entry["seq"] = (int(last.get("seq", len(entries) - 1)) + 1) if isinstance(last, dict) and \
            "_unreadable" not in last else len(entries)
        entry["prev"] = last.get("digest") if isinstance(last, dict) else None
        entry["digest"] = entry_digest(entry)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    return entry


def verify_chain(entries):
    """Problems of a log's chain: unreadable lines, digests that do not match their entry, broken prev links and
    sequence gaps. [] when intact."""
    problems, prev = [], None
    for i, entry in enumerate(entries):
        if "_unreadable" in entry:
            problems.append("line {}: not JSON".format(i + 1))
            prev = None
            continue
        if entry.get("digest") != entry_digest(entry):
            problems.append("entry {} (seq {}): digest does not match its content (edited after it was written)".format(
                i + 1, entry.get("seq")))
        if entry.get("prev") != prev:
            problems.append("entry {} (seq {}): prev does not chain to the entry before (removed, inserted or "
                            "reordered entries)".format(i + 1, entry.get("seq")))
        if entry.get("seq") != i:
            problems.append("entry {}: seq {} where {} was expected".format(i + 1, entry.get("seq"), i))
        prev = entry.get("digest")
    return problems


def load_files(paths):
    """[(file record, [(spec, file name)])] for the given spec files ({"specs": [...]} or a list)."""
    out = []
    for path in paths:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        specs = data.get("specs", []) if isinstance(data, dict) else data
        out.append(({"path": rel(path), "sha256": file_sha(path), "specs": len(specs)},
                    [(spec, Path(path).name) for spec in specs]))
    return out


def trial_reasons(trial):
    """One short reason for a failing trial (what --check and audit print)."""
    if trial.get("error"):
        return "seed {}: {}".format(trial.get("seed"), str(trial["error"])[:160])
    parts = [k for k in TRIAL_FLAGS if not trial.get(k)]
    detail = (trial.get("wrong_not_run") or [None])[0] or trial.get("reference_error") or \
        trial.get("reference_failures") or trial.get("perceivability_gaps") or trial.get("dialogue_lint") or ""
    return "seed {}: {} {}".format(trial.get("seed"), ",".join(parts), str(detail)[:200]).strip()


def code_fingerprints():
    from house_sim import audit
    from house_sim.kernel.engine import actions_fingerprint
    out = {"actions_fingerprint": actions_fingerprint()}
    try:
        out["code_fingerprint"] = audit.fingerprint()
        out["harness_fingerprint"] = audit.harness_fingerprint()
    except Exception as exc:  # noqa: BLE001  (recorded, never hidden)
        out["fingerprint_error"] = "{}: {}".format(type(exc).__name__, exc)
    return out


def check(paths, seeds=None, workers=None, perception=None, camera=None, gen=None, ids=None):
    """Verify the specs of `paths` now. Returns (files, results, options): per-spec verdicts in file order."""
    sys.path.insert(0, str(ROOT))
    from house_sim.tasks.verify import SEEDS, verify_many
    files = load_files(paths)
    specs = [(spec, name) for _, items in files for spec, name in items]
    if ids:
        wanted = set(ids)
        specs = [(spec, name) for spec, name in specs if spec.get("catalog_id") in wanted]
    seeds = tuple(seeds) if seeds else SEEDS
    kwargs = {"seeds": seeds, "workers": workers, "perception": perception, "camera": camera}
    if gen is not None:
        kwargs["gen"] = gen
    records = verify_many([spec for spec, _ in specs], **kwargs) if specs else []
    results = []
    for (spec, name), record in zip(specs, records):
        failing = [t.get("seed") for t in record.get("trials", []) if not t.get("passed")]
        results.append({"catalog_id": record.get("catalog_id"), "file": name, "spec_hash": record.get("spec_hash"),
                        "verdict": "pass" if record.get("passed") else "fail", "failing_seeds": failing,
                        "reasons": [trial_reasons(t) for t in record.get("trials", []) if not t.get("passed")][:5]})
    options = {"seeds": list(seeds), "perception": perception, "camera": camera, "gen": gen,
               "ids": sorted(ids) if ids else None}
    return [f for f, _ in files], results, options


def mismatch_of(results, claimed):
    if claimed is None:
        return None
    passing = {r["catalog_id"] for r in results if r["verdict"] == "pass"}
    claimed = set(claimed)
    return {"claimed_not_passing": sorted(claimed - passing), "passing_not_claimed": sorted(passing - claimed)}


def summary_line(entry, log_path):
    mismatch = entry.get("mismatch")
    unexcused = [c for c in entry["failing"] if c not in set(entry.get("allowlisted") or ())]
    return json.dumps({"log": rel(log_path), "seq": entry["seq"], "digest": entry["digest"],
                       "passing": len(entry["passing"]), "failing": entry["failing"],
                       "allowlisted": entry.get("allowlisted") or [],
                       "mismatch": mismatch, "ok": not unexcused and not (
                           mismatch and (mismatch["claimed_not_passing"] or mismatch["passing_not_claimed"]))},
                      sort_keys=True)


def allowlisted(results, perception=None, allow=None):
    """Catalog ids of failing results whose every failing seed the gate's committed allowlist excuses (kind "spec",
    mode "own", or "realistic" when the run forced realistic perception): tier1_gate.load_known_failing (`allow`: an
    already loaded allowlist)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tier1_gate_for_run_check", str(Path(__file__).resolve().parent /
                                                                                   "tier1_gate.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    if allow is None:
        allow = gate.load_known_failing(gate.policy_root())
    mode = "realistic" if perception == "realistic" else "own"
    return sorted(r["catalog_id"] for r in results if r["verdict"] != "pass" and r["failing_seeds"] and
                  all(gate.spec_allowed(allow, r["catalog_id"], seed, mode) for seed in r["failing_seeds"]))


def cmd_run(args):
    paths = sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))) if args.all else list(args.files or [])
    if not paths:
        print("run: name spec files or --all", file=sys.stderr)
        return 2
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        print("run: no such file: " + ", ".join(missing), file=sys.stderr)
        return 2
    claimed = None
    if args.claimed_file:
        with open(args.claimed_file, encoding="utf-8") as stream:
            data = json.load(stream)
        claimed = list(data.get("verified_passing", data) if isinstance(data, dict) else data)
    elif args.claimed is not None:
        claimed = list(args.claimed)
    sys.path.insert(0, str(ROOT))
    from house_sim.kernel.plugins import load_domains
    load_domains()
    start = time.perf_counter()
    files, results, options = check(paths, args.seeds, args.workers, args.perception, args.camera, args.gen, args.ids)
    entry = {"format": FORMAT, "date": now_iso(), "role": args.role, "agent": args.agent, "batch": args.batch,
             "files": files, "options": options, "code": code_fingerprints(),
             "python": platform.python_version(), "platform": platform.platform(),
             "results": results, "passing": sorted(r["catalog_id"] for r in results if r["verdict"] == "pass"),
             "failing": sorted(r["catalog_id"] for r in results if r["verdict"] != "pass"),
             "claimed": sorted(claimed) if claimed is not None else None,
             "mismatch": mismatch_of(results, claimed), "wall_s": round(time.perf_counter() - start, 1),
             "workers": args.workers}
    if args.allowlist:
        entry["allowlisted"] = allowlisted(results, args.perception)
    log_path = Path(args.log) if args.log else ROOT / DEFAULT_LOG
    entry = append_entry(log_path, entry)
    for r in results:
        print("{} {}{}{}".format("PASS" if r["verdict"] == "pass" else "FAIL", r["catalog_id"],
                                 "" if r["verdict"] == "pass" else "  " + "; ".join(r["reasons"][:2]),
                                 " (allowlisted)" if r["catalog_id"] in (entry.get("allowlisted") or ()) else ""))
    mismatch = entry["mismatch"]
    if mismatch and (mismatch["claimed_not_passing"] or mismatch["passing_not_claimed"]):
        print("MISMATCH with the claimed list: claimed but not passing {}; passing but not claimed {}".format(
            mismatch["claimed_not_passing"], mismatch["passing_not_claimed"]))
    if args.json:
        Path(args.json).write_text(json.dumps(entry, indent=1, sort_keys=True), encoding="utf-8")
    line = summary_line(entry, log_path)
    print(line)
    return 0 if json.loads(line)["ok"] else 1


def cmd_verify(args):
    log_path = Path(args.log) if args.log else ROOT / DEFAULT_LOG
    entries = read_log(log_path)
    problems = verify_chain(entries)
    print("check log {}: {} entr{}; chain {}".format(rel(log_path), len(entries), "y" if len(entries) == 1 else "ies",
                                                   "intact" if not problems else "BROKEN"))
    for p in problems:
        print("  " + p)
    changed = []
    for entry in entries:
        for f in entry.get("files") or []:
            path = ROOT / f["path"]
            now = file_sha(path) if path.is_file() else None
            if now != f.get("sha256"):
                changed.append((entry.get("seq"), f["path"]))
    for seq, path in changed[-20:]:
        print("  note: {} changed since entry {} checked it".format(path, seq))
    bad = bool(problems)
    if args.rerun:
        sys.path.insert(0, str(ROOT))
        from house_sim.kernel.plugins import load_domains
        load_domains()
        current = code_fingerprints()
        for entry in [e for e in entries if "_unreadable" not in e][-args.rerun:]:
            stale = [f["path"] for f in entry.get("files") or []
                     if not (ROOT / f["path"]).is_file() or file_sha(ROOT / f["path"]) != f.get("sha256")]
            if stale or (entry.get("code") or {}).get("code_fingerprint") != current.get("code_fingerprint"):
                print("  rerun seq {}: skipped ({})".format(entry.get("seq"), "files changed: " + ", ".join(stale)
                                                             if stale else "the code changed since"))
                continue
            opts = entry.get("options") or {}
            _, results, _ = check([ROOT / f["path"] for f in entry["files"]], opts.get("seeds"), args.workers,
                                  opts.get("perception"), opts.get("camera"), opts.get("gen"), opts.get("ids"))
            now = {r["catalog_id"]: r["verdict"] for r in results}
            then = {r["catalog_id"]: r["verdict"] for r in entry.get("results") or []}
            diff = sorted(k for k in set(now) | set(then) if now.get(k) != then.get(k))
            print("  rerun seq {}: {}".format(entry.get("seq"), "verdicts reproduce ({} specs)".format(len(now))
                                              if not diff else "VERDICTS DIFFER: " + ", ".join(diff[:20])))
            bad = bad or bool(diff)
    return 1 if bad else 0


def cmd_show(args):
    log_path = Path(args.log) if args.log else ROOT / DEFAULT_LOG
    entries = read_log(log_path)
    for entry in entries[-args.last:]:
        if "_unreadable" in entry:
            print("(unreadable line)")
            continue
        print("seq {} {} {} {} files {}: {} pass, {} fail{} digest {}".format(
            entry.get("seq"), entry.get("date"), entry.get("role") or "-", entry.get("batch") or "-",
            ",".join(f["path"] for f in entry.get("files") or []), len(entry.get("passing") or []),
            len(entry.get("failing") or []), "; claim mismatch" if entry.get("mismatch") and any(
                entry["mismatch"].values()) else "", str(entry.get("digest"))[:12]))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Orchestrator-run spec checks with a hash-chained check log")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="verify spec files now and append the result to the check log")
    r.add_argument("files", nargs="*")
    r.add_argument("--all", action="store_true", help="every tasks/authored/*.json")
    r.add_argument("--ids", nargs="+")
    r.add_argument("--seeds", nargs="+", type=int)
    r.add_argument("--workers", type=int)
    r.add_argument("--perception", choices=("oracle", "realistic"))
    r.add_argument("--camera", choices=("room", "geometric"))
    r.add_argument("--gen", type=int, choices=(1, 2))
    r.add_argument("--claimed", nargs="*", help="the ids the agent claims pass (compared with the result)")
    r.add_argument("--claimed-file", help="JSON list of claimed ids, or an object with verified_passing")
    r.add_argument("--role", help="what this check follows (author, review, fix, ci, ...)")
    r.add_argument("--agent", help="the label of the agent whose work is checked")
    r.add_argument("--batch", help="the batch or package label")
    r.add_argument("--log", help="check log (default: {})".format(DEFAULT_LOG))
    r.add_argument("--json", help="also write the full entry to this file")
    r.add_argument("--allowlist", action="store_true",
                   help="a failing spec excused on every failing seed by the gate's committed allowlist does not fail "
                        "the run (CI)")
    v = sub.add_parser("verify", help="check the log's hash chain (and re-run the last N entries)")
    v.add_argument("--log")
    v.add_argument("--rerun", type=int, default=0)
    v.add_argument("--workers", type=int)
    s = sub.add_parser("show", help="print the last entries")
    s.add_argument("--log")
    s.add_argument("--last", type=int, default=20)
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "show":
        return cmd_show(args)
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
