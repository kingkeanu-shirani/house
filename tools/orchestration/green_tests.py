"""Run every tests/test_*.py normally and with HOUSE_SIM_STRICT=1 (parallel), summarise results.

usage: python green_tests.py [jobs] [pattern] [--modes normal,strict] [--tests-dir DIR] [--json OUT]
                             [--registry FILE|committed|none]
       python green_tests.py --perf [pattern] [--attempts N] [--modes normal,strict] [--tests-dir DIR] [--json OUT]

One process per file and mode (HOUSE_SIM_DOMAINS unset), through the tier-1 gate's `_unittest` worker (the report and
exit status of `python -m unittest`, plus every test's outcome). The summary goes to stdout and the per-file results
to tools/orchestration/green_tests.json (or --json OUT; the tier-1 gate reads the default file for its time
estimates). Exit status: 0 when every run passes, 1 when any fails (CI and the orchestrator read it), 2 when no test
file matches.

Wall-clock bounds are not part of the correctness verdict (phase-1 red team, K-GATE2), but their bodies are part of the
correctness suite (phase-1 close-out, K-GATE3; tools/orchestration/wallclock.py): the parallel runs set
HOUSE_SIM_PERF_TESTS=0 (an explicit value in the caller's environment is kept) and the worker runs every perf-marked
test relaxed -- its body runs, its timing cannot fail (a counting clock, or the wall_clock_bound helper recording
instead of asserting). A run fails when a perf-marked test still skips its body ("wall-clock bound off") without the
registry's dated exception, and the whole run fails when a test file of the project carries a perf mark the committed
registry (tools/orchestration/gate_policy/perf_tests.json; --registry) does not list. `--perf` is the perf job: the
bounds alone, one process at a time, HOUSE_SIM_PERF_TESTS=1, each bound passing when one of --attempts runs (default 3)
meets it -- the tolerance for a machine that is busy for a moment. The tier-1 gate runs the same job in G7, alone after
its benchmarks.
"""
import argparse
import concurrent.futures as cf
import glob
import importlib.util
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
_WALLCLOCK = []


def wallclock():
    """tools/orchestration/wallclock.py (perf marks, the registry, the relax plan), loaded once from beside this
    script."""
    if not _WALLCLOCK:
        spec = importlib.util.spec_from_file_location("wallclock_for_green_tests", os.path.join(HERE, "wallclock.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _WALLCLOCK.append(module)
    return _WALLCLOCK[0]


def _outcomes(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tests") if isinstance(data, dict) and isinstance(data.get("tests"), dict) else None
    except (OSError, ValueError):
        return None


def run(path, strict, cwd, plan_path=None, work=None, excused=()):
    """One correctness run of a test file through the gate's _unittest worker. The row: file, strict, rc, ran,
    skipped, secs, outcomes (the ids of every outcome that is not "ok"), bounds_skipped (perf-marked tests that
    skipped their body without a registry exception: the run fails) and tail."""
    env = dict(os.environ)
    env.pop("HOUSE_SIM_DOMAINS", None)
    env.setdefault(PERF_ENV, "0")          # the correctness suite: wall-clock bounds run relaxed, asserted in --perf
    if plan_path:
        env[wallclock().PLAN_ENV] = plan_path
    if strict:
        env["HOUSE_SIM_STRICT"] = "1"
    else:
        env.pop("HOUSE_SIM_STRICT", None)
    work = work or tempfile.gettempdir()
    out_json = os.path.join(work, "{}_{}.json".format(os.path.basename(path)[:-3], "strict" if strict else "normal"))
    try:
        os.remove(out_json)
    except OSError:
        pass
    t = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, "tier1_gate.py"), "_unittest", out_json,
                        os.path.relpath(path, cwd)], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = p.stderr + p.stdout
    # the counts of this run's own report: the last "Ran N tests" and verdict line of stderr (a test's own output may
    # quote another run's report before them)
    ran = re.findall(r"^Ran (\d+) tests?", p.stderr, re.M)
    verdict = re.findall(r"^(?:OK|FAILED)\b.*$", p.stderr, re.M)
    s = re.search(r"skipped=(\d+)", verdict[-1]) if verdict else None
    outcomes = _outcomes(out_json) or {}
    quiet = {}
    for tid, outcome in sorted(outcomes.items()):
        if outcome != "ok":
            quiet.setdefault(outcome, []).append(tid)
    skipped_bounds = [tid for tid in quiet.get("perf", []) if tid not in excused]
    rc = p.returncode
    if skipped_bounds and not rc:
        rc = 3                             # a perf-marked test that never ran its body is no pass (K-GATE3)
        out += "\nperf-marked test(s) skipped their body in a correctness run (no registry exception): {}\n".format(
            ", ".join(skipped_bounds))
    return {"file": os.path.basename(path), "strict": strict, "rc": rc, "ran": int(ran[-1]) if ran else 0,
            "skipped": int(s.group(1)) if s else 0, "secs": round(time.time() - t, 1), "outcomes": quiet,
            "bounds_skipped": skipped_bounds, "tail": "" if rc == 0 else out[-3000:]}


def perf_tests(files):
    """{file name: [test ids]} of the wall-clock bounds of test files: test methods of a TestCase class whose body
    reads HOUSE_SIM_PERF_TESTS (the legacy guard) or calls wallclock.wall_clock_bound. Ids are "<tests folder>.
    <module>.<Class>.<method>"."""
    return {name: sorted(marks) for name, marks in wallclock().perf_marks(files).items()}


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
            env.pop(wallclock().PLAN_ENV, None)
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


def bound_policy(files, registry, tests_dir):
    """(plan, excused ids, unregistered ids, note) of the correctness runs: the perf marks of `files`, the registry
    (--registry: "committed" -- the project's committed registry, the default for the project's own tests folder --,
    "none", or a registry file) and the relax plan the worker follows (wallclock.perf_plan)."""
    wc = wallclock()
    marks = wc.flat_marks(wc.perf_marks(files))
    if registry is None:
        registry = "committed" if os.path.abspath(tests_dir) == os.path.join(ROOT, "tests") else "none"
    if registry == "none":
        return wc.perf_plan(marks, {}), set(), [], "no registry (--registry none)"
    if registry == "committed":
        data, note = wc.committed_registry(ROOT)
    else:
        try:
            with open(registry, encoding="utf-8") as f:
                data, note = json.load(f), "registry file " + registry
        except (OSError, ValueError) as exc:
            data, note = None, "unreadable registry {} ({})".format(registry, exc)
    bounds = wc.registry_entries(data)["bounds"]
    plan = wc.perf_plan(marks, bounds)
    return plan, {t for t, m in plan.items() if m == "skip"}, wc.unregistered(marks, bounds), note


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
    p.add_argument("--registry", help="the wall-clock bound registry: 'committed' (default for the project's tests "
                                      "folder), 'none' (default elsewhere) or a registry file")
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
    # the registry and the plan cover every test file of the folder (an unregistered mark fails the run whatever
    # pattern selected the files)
    plan, excused, unregistered, note = bound_policy(sorted(glob.glob(os.path.join(tests_dir, "test_*.py"))),
                                                     args.registry, tests_dir)
    work = tempfile.mkdtemp(prefix="green_tests_")
    try:
        plan_path = os.path.join(work, "perf_plan.json")
        with open(plan_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(plan, f, indent=1, sort_keys=True)
        runs = [(f, m == "strict") for f in files for m in modes]
        with cf.ThreadPoolExecutor(max(1, args.jobs)) as ex:
            results = list(ex.map(lambda a: run(a[0], a[1], cwd, plan_path, work, excused), runs))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    bad = [r for r in results if r["rc"]]
    for r in results:
        print("{:4} {:32} strict={!s:5} ran={:4} {:6}s".format("FAIL" if r["rc"] else "ok", r["file"], r["strict"],
                                                              r["ran"], r["secs"]))
    normal = [r for r in results if not r["strict"]] or results
    print("TOTAL tests ({} run):".format("normal" if any(not r["strict"] for r in results) else "strict"),
          sum(r["ran"] for r in normal), "failures:", len(bad))
    if os.environ.get(PERF_ENV, "0") == "0":
        print("wall-clock bounds relaxed in these runs ({}=0: bodies run, timing not asserted): {} bound(s), {} with a "
              "registry skip exception; assert them alone with --perf (registry: {})".format(
                  PERF_ENV, len(plan), len(excused), note))
    if unregistered:
        print("UNREGISTERED wall-clock bound(s) (a perf mark the registry {} does not list; the main session registers "
              "it after review): {}".format(wallclock().REGISTRY, ", ".join(unregistered)))
    out = args.json or os.path.join(os.path.dirname(os.path.abspath(__file__)), "green_tests.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=1)
    for r in bad:
        print("=" * 30, r["file"], "strict" if r["strict"] else "normal")
        print(r["tail"][-1500:])
    return 1 if bad or unregistered else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
