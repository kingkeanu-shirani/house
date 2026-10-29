"""Run every tests/test_*.py normally and with HOUSE_SIM_STRICT=1 (parallel), summarise results.

usage: python green_tests.py [jobs] [pattern] [--modes normal,strict] [--tests-dir DIR] [--json OUT]
       python green_tests.py --perf [pattern] [--attempts N] [--modes normal,strict] [--tests-dir DIR] [--json OUT]

One process per file and mode (HOUSE_SIM_DOMAINS unset). The summary goes to stdout and the per-file results to
tools/orchestration/green_tests.json (or --json OUT; the tier-1 gate reads the default file for its time estimates).
Exit status: 0 when every run passes, 1 when any fails (CI and the orchestrator read it), 2 when no test file matches.

Wall-clock bounds are not part of the correctness suite (phase-1 red team, K-GATE2): under parallel load they fail for
reasons that are not regressions. The parallel runs set HOUSE_SIM_PERF_TESTS=0 (every bound then skips with "wall-clock
bound off"; an explicit HOUSE_SIM_PERF_TESTS in the caller's environment is kept), and `--perf` is the perf job: the
bounds alone, one process at a time, HOUSE_SIM_PERF_TESTS=1, each bound passing when one of --attempts runs (default
3) meets it -- the tolerance for a machine that is busy for a moment. A bound is a test method whose body reads
HOUSE_SIM_PERF_TESTS (perf_tests). The tier-1 gate runs the same job in G7, alone after its benchmarks.
"""
import argparse
import ast
import concurrent.futures as cf
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
PERF_ENV = "HOUSE_SIM_PERF_TESTS"
ATTEMPTS = 3


def run(path, strict, cwd):
    env = dict(os.environ)
    env.pop("HOUSE_SIM_DOMAINS", None)
    env.setdefault(PERF_ENV, "0")          # the correctness suite: wall-clock bounds run in the perf job (--perf)
    if strict:
        env["HOUSE_SIM_STRICT"] = "1"
    else:
        env.pop("HOUSE_SIM_STRICT", None)
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", os.path.relpath(path, cwd)], cwd=cwd, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stderr + p.stdout
    m = re.search(r"Ran (\d+) test", out)
    s = re.search(r"skipped=(\d+)", out)
    return {"file": os.path.basename(path), "strict": strict, "rc": p.returncode, "ran": int(m.group(1)) if m else 0,
            "skipped": int(s.group(1)) if s else 0, "secs": round(time.time() - t, 1),
            "tail": "" if p.returncode == 0 else out[-3000:]}


def perf_tests(files):
    """{file name: [test ids]} of the wall-clock bounds of test files: test methods of a TestCase class whose body
    reads HOUSE_SIM_PERF_TESTS (the guard every bound carries). Ids are "<tests folder>.<module>.<Class>.<method>"."""
    out = {}
    for path in sorted(files):
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except (OSError, SyntaxError, ValueError):
            continue
        module = "{}.{}".format(os.path.basename(os.path.dirname(os.path.abspath(path))),
                                os.path.splitext(os.path.basename(path))[0])
        ids = []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test") and \
                        any(isinstance(n, ast.Constant) and n.value == PERF_ENV for n in ast.walk(item)):
                    ids.append("{}.{}.{}".format(module, node.name, item.name))
        if ids:
            out[os.path.basename(path)] = ids
    return out


def _outcomes(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tests") if isinstance(data, dict) and isinstance(data.get("tests"), dict) else None
    except (OSError, ValueError):
        return None


def run_perf(ids_by_file, modes, attempts, cwd, work, base_env=None, timeout=1800):
    """The perf job: every bound of `ids_by_file` ({file name: [test ids]}) alone, one process per file and mode at
    a time (HOUSE_SIM_PERF_TESTS=1; strict mode with HOUSE_SIM_STRICT=1), through the tier-1 gate's _unittest worker
    (per-test outcomes); the bounds that did not pass are run again, up to `attempts` runs in all. A bound passes when
    one run meets it (ok, or skipped for a reason of its own). Returns one row per file and mode: {"file", "mode",
    "ids", "failed" (every attempt failed), "attempts" {id: runs}, "secs", "tail" (the last failing run's report)}."""
    os.makedirs(work, exist_ok=True)
    rows = []
    script = os.path.join(HERE, "tier1_gate.py")
    for name in sorted(ids_by_file):
        ids = sorted(set(ids_by_file[name]))
        for mode in modes:
            env = dict(base_env if base_env is not None else os.environ)
            env.pop("HOUSE_SIM_DOMAINS", None)
            env[PERF_ENV] = "1"
            if mode == "strict":
                env["HOUSE_SIM_STRICT"] = "1"
            else:
                env.pop("HOUSE_SIM_STRICT", None)
            remaining, used, tail_text = list(ids), {}, ""
            start = time.time()
            for attempt in range(1, max(1, attempts) + 1):
                if not remaining:
                    break
                out = os.path.join(work, "perf_{}_{}_{}.json".format(name[:-3], mode, attempt))
                try:
                    os.remove(out)
                except OSError:
                    pass
                try:
                    proc = subprocess.run([sys.executable, script, "_unittest", out] + remaining, cwd=cwd, env=env,
                                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                                          timeout=timeout)
                    report = proc.stderr + proc.stdout
                except subprocess.TimeoutExpired:
                    report = "TIMEOUT after {} s".format(timeout)
                outcomes = _outcomes(out) or {}
                for tid in remaining:
                    used[tid] = attempt
                done = {t for t in remaining if outcomes.get(t) in ("ok", "skip", "xfail")}
                if len(done) < len(remaining):
                    tail_text = report[-2000:]
                remaining = [t for t in remaining if t not in done]
            rows.append({"file": name, "mode": mode, "ids": ids, "failed": sorted(remaining), "attempts": used,
                         "secs": round(time.time() - start, 1), "tail": tail_text if remaining else ""})
    return rows


def main_perf(args, files, modes, cwd):
    bounds = perf_tests(files)
    if not bounds:
        print("no wall-clock bounds in the matching test files")
        return 0
    work = tempfile.mkdtemp(prefix="green_tests_perf_")
    try:
        rows = run_perf(bounds, modes, args.attempts, cwd, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = [(r["file"], r["mode"], t) for r in rows for t in r["failed"]]
    for r in rows:
        retried = sum(1 for n in r["attempts"].values() if n > 1)
        print("{:4} {:32} {:6} bounds={:3} retried={:2} failed={:2} {:6}s".format(
            "FAIL" if r["failed"] else "ok", r["file"], r["mode"], len(r["ids"]), retried, len(r["failed"]), r["secs"]))
    print("TOTAL wall-clock bounds: {} x {} mode(s), best of {} attempt(s) alone: {} failing".format(
        sum(len(v) for v in bounds.values()), len(modes), args.attempts, len(failed)))
    for file, mode, tid in failed:
        print("  FAIL {} ({})".format(tid, mode))
    for r in rows:
        if r["failed"]:
            print("=" * 30, r["file"], r["mode"])
            print(r["tail"][-1500:])
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rows, f, indent=1)
    return 1 if failed else 0


def main(argv):
    p = argparse.ArgumentParser(description="every test file, normal and strict, in parallel")
    p.add_argument("positional", nargs="*", metavar="[jobs] [pattern]",
                   help="parallel processes (default 6; the perf job runs one at a time) and a file pattern "
                        "(default test_*.py)")
    p.add_argument("--modes", default="normal,strict", help="comma list of normal,strict")
    p.add_argument("--tests-dir", help="folder of the test files (default: <root>/tests); run from its parent")
    p.add_argument("--json", help="results file (default: tools/orchestration/green_tests.json; --perf: none)")
    p.add_argument("--perf", action="store_true", help="the perf job: only the wall-clock bounds, alone, with retries")
    p.add_argument("--attempts", type=int, default=ATTEMPTS, help="--perf: runs per bound before it fails (default 3)")
    args = p.parse_args(argv)
    args.jobs, args.pattern = 6, "test_*.py"
    rest = list(args.positional)
    if rest and rest[0].isdigit():
        args.jobs = int(rest.pop(0))
    if rest:
        args.pattern = rest.pop(0)
    if rest:
        p.error("unexpected arguments: " + " ".join(rest))
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes or any(m not in ("normal", "strict") for m in modes):
        print("--modes takes normal and/or strict")
        return 2
    tests_dir = os.path.abspath(args.tests_dir) if args.tests_dir else os.path.join(ROOT, "tests")
    cwd = os.path.dirname(tests_dir)
    files = sorted(glob.glob(os.path.join(tests_dir, args.pattern)))
    if not files:
        print("no test files match {} in {}".format(args.pattern, tests_dir))
        return 2
    if args.perf:
        return main_perf(args, files, modes, cwd)
    runs = [(f, m == "strict") for f in files for m in modes]
    with cf.ThreadPoolExecutor(max(1, args.jobs)) as ex:
        results = list(ex.map(lambda a: run(a[0], a[1], cwd), runs))
    bad = [r for r in results if r["rc"]]
    for r in results:
        print("{:4} {:32} strict={!s:5} ran={:4} {:6}s".format("FAIL" if r["rc"] else "ok", r["file"], r["strict"],
                                                              r["ran"], r["secs"]))
    normal = [r for r in results if not r["strict"]] or results
    print("TOTAL tests ({} run):".format("normal" if any(not r["strict"] for r in results) else "strict"),
          sum(r["ran"] for r in normal), "failures:", len(bad))
    if os.environ.get(PERF_ENV, "0") == "0":
        print("wall-clock bounds off in these runs ({}=0): {} bound(s) in the matching files; run them alone with "
              "--perf".format(PERF_ENV, sum(len(v) for v in perf_tests(files).values())))
    out = args.json or os.path.join(os.path.dirname(os.path.abspath(__file__)), "green_tests.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=1)
    for r in bad:
        print("=" * 30, r["file"], "strict" if r["strict"] else "normal")
        print(r["tail"][-1500:])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
