"""Run every tests/test_*.py normally and with HOUSE_SIM_STRICT=1 (parallel), summarise results.

usage: python green_tests.py [jobs] [pattern] [--modes normal,strict] [--tests-dir DIR] [--json OUT]

One process per file and mode (HOUSE_SIM_DOMAINS unset). The summary goes to stdout and the per-file results to
tools/orchestration/green_tests.json (or --json OUT; the tier-1 gate reads the default file for its time estimates).
Exit status: 0 when every run passes, 1 when any fails (CI and the orchestrator read it), 2 when no test file matches.
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(path, strict, cwd):
    env = dict(os.environ)
    env.pop("HOUSE_SIM_DOMAINS", None)
    if strict:
        env["HOUSE_SIM_STRICT"] = "1"
    else:
        env.pop("HOUSE_SIM_STRICT", None)
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", os.path.relpath(path, cwd)], cwd=cwd, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stderr + p.stdout
    m = re.search(r"Ran (\d+) test", out)
    return {"file": os.path.basename(path), "strict": strict, "rc": p.returncode, "ran": int(m.group(1)) if m else 0,
            "secs": round(time.time() - t, 1), "tail": "" if p.returncode == 0 else out[-3000:]}


def main(argv):
    p = argparse.ArgumentParser(description="every test file, normal and strict, in parallel")
    p.add_argument("jobs", nargs="?", type=int, default=6)
    p.add_argument("pattern", nargs="?", default="test_*.py")
    p.add_argument("--modes", default="normal,strict", help="comma list of normal,strict")
    p.add_argument("--tests-dir", help="folder of the test files (default: <root>/tests); run from its parent")
    p.add_argument("--json", help="results file (default: tools/orchestration/green_tests.json)")
    args = p.parse_args(argv)
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
    out = args.json or os.path.join(os.path.dirname(os.path.abspath(__file__)), "green_tests.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=1)
    for r in bad:
        print("=" * 30, r["file"], "strict" if r["strict"] else "normal")
        print(r["tail"][-1500:])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
