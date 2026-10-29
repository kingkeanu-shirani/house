"""Wall-clock bounds: the timing assertions of the unit suite, kept out of its verdict but not out of its coverage.

A wall-clock bound fails under parallel load for reasons that are not regressions (phase-1 red team), so it is asserted
only in the perf job (`green_tests.py --perf`, the tier-1 gate's G7: alone, best of 3 attempts). Until the phase-1
close-out (K-GATE3) the correctness runs (HOUSE_SIM_PERF_TESTS=0) skipped such a test entirely, and a failing
correctness test disguised as a bound (it skipped with the bound's message) kept CI green. Now:

* **Every bound runs its body in the correctness runs; only the timing is relaxed.** The gate's `_unittest` worker
  (which the gate's G1 / G1b / G8 and green_tests.py's correctness runs use) runs each perf-marked test with
  HOUSE_SIM_PERF_TESTS=relaxed, so the legacy guard (`if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
  self.skipTest("wall-clock bound off ...")`) lets it through, and, for a test with that guard, under a counting clock
  (`time.perf_counter`, `monotonic`, `process_time`, `time` and their `_ns` forms advance 1 us per call), so every
  elapsed time it measures is tiny and only its functional assertions can fail. A test written with
  `wall_clock_bound(self, elapsed, limit)` below keeps the real clock: the helper asserts the bound in the perf job and
  records it (HOUSE_SIM_PERF_RECORD, JSON lines) otherwise.
* **Every bound is registered.** A test method that reads HOUSE_SIM_PERF_TESTS or calls `wall_clock_bound` is a
  perf mark (perf_marks: by AST, with the sha256 of its source); the committed registry
  tools/orchestration/gate_policy/perf_tests.json (main session only, read as committed) lists every mark, so a new
  one is visible in review. An unregistered mark fails the correctness run (green_tests.py, the gate's G1).
* **A bound that skips its body is a skip.** A perf-marked test that still skips with the bound's message in a
  correctness run (it never ran its body) fails the run, unless its registry entry carries a dated `skip` exception
  pinned to the exact source of the method (`source_sha256`) -- for a bound a counting clock cannot keep (a relative
  bound such as "the new step is faster than the old one"); the gate then also requires the perf job to have run it.

Stdlib only; imported by test modules (`from tools.orchestration.wallclock import wall_clock_bound`), so it imports
nothing else of the project.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PERF_ENV = "HOUSE_SIM_PERF_TESTS"                 # unset or "1": the perf job; "0": correctness; "relaxed": see above
PLAN_ENV = "HOUSE_SIM_PERF_PLAN"                  # JSON file {test id: "count" | "helper" | "skip"} for the worker
RECORD_ENV = "HOUSE_SIM_PERF_RECORD"              # JSON-lines file the helper appends its relaxed measurements to
RELAXED = "relaxed"
SKIP_REASON = "wall-clock bound off"              # the skip message of the legacy guard
HELPER = "wall_clock_bound"
REGISTRY = "tools/orchestration/gate_policy/perf_tests.json"
REGISTRY_FORMAT = "house-gate-perf-tests-v1"
CLOCKS = ("perf_counter", "monotonic", "process_time", "time")
TICK_S = 1e-6
ROOT = Path(__file__).resolve().parent.parent.parent


# ----------------------------------------------------------------------------------------------------------------
# the helper a bound calls instead of assertLess(elapsed, limit)

def enforced():
    """True in the perf job (HOUSE_SIM_PERF_TESTS unset or "1"): the bound is asserted."""
    return os.environ.get(PERF_ENV, "1") not in ("0", RELAXED)


def wall_clock_bound(test, elapsed, limit, what="wall-clock bound"):
    """The timing assertion of a wall-clock bound: `elapsed <= limit`, asserted in the perf job and recorded (not
    asserted) in the correctness runs, where every other assertion of the test runs as usual."""
    if enforced():
        test.assertLessEqual(elapsed, limit, "{}: {:.6g} > {:.6g}".format(what, elapsed, limit))
        return True
    path = os.environ.get(RECORD_ENV)
    if path:
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"test": test.id(), "what": what, "elapsed": elapsed, "limit": limit,
                                    "over": elapsed > limit}, sort_keys=True) + "\n")
        except OSError:
            pass
    return elapsed <= limit


# ----------------------------------------------------------------------------------------------------------------
# perf marks: found by AST, registered in the committed registry

def method_source_sha(lines, node):
    """sha256 of a test method's source (its decorators included, trailing blanks stripped, LF): the exact code a
    registry `skip` exception names."""
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    text = "\n".join(line.rstrip() for line in lines[start - 1:node.end_lineno])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_switch(node):
    return isinstance(node, ast.Constant) and node.value == PERF_ENV


def _kind(item):
    """"guard" when a test method reads HOUSE_SIM_PERF_TESTS itself (os.environ.get / os.getenv / os.environ[...] /
    `in os.environ`), "helper" when it calls wall_clock_bound only, None when it is no bound (a test that only sets or
    removes the switch for a child process is none)."""
    guard = helper = False
    for n in ast.walk(item):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name == HELPER:
                helper = True
            elif name in ("get", "getenv") and n.args and _is_switch(n.args[0]):
                guard = True
        elif isinstance(n, ast.Subscript):
            index = n.slice.value if type(n.slice).__name__ == "Index" else n.slice      # ast.Index before 3.9
            guard = guard or _is_switch(index)
        elif isinstance(n, ast.Compare) and _is_switch(n.left) and any(isinstance(op, (ast.In, ast.NotIn))
                                                                       for op in n.ops):
            guard = True
    return "guard" if guard else ("helper" if helper else None)


def perf_marks(files):
    """{file name: {test id: {"kind", "source_sha256"}}} of the wall-clock bounds of test files: the test methods of
    a class that read HOUSE_SIM_PERF_TESTS or call wall_clock_bound. Ids are "<tests folder>.<module>.<Class>.
    <method>", as unittest prints them."""
    out = {}
    for path in sorted(str(f) for f in files):
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
        except (OSError, SyntaxError, ValueError):
            continue
        lines = source.splitlines()
        module = "{}.{}".format(os.path.basename(os.path.dirname(os.path.abspath(path))),
                                os.path.splitext(os.path.basename(path))[0])
        marks = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    kind = _kind(item)
                    if kind:
                        marks["{}.{}.{}".format(module, node.name, item.name)] = {
                            "kind": kind, "source_sha256": method_source_sha(lines, item)}
        if marks:
            out[os.path.basename(path)] = marks
    return out


def flat_marks(marks_by_file):
    """{test id: {"file", "kind", "source_sha256"}} of perf_marks' result."""
    return {tid: dict(info, file=name) for name, marks in marks_by_file.items() for tid, info in marks.items()}


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def registry_entries(data, today=None):
    """{"bounds": {test id: entry}, "invalid": [(entry, why)]} of a registry file's data. An entry is {"test", "owner",
    "skip"?: {"source_sha256", "expires": "YYYY-MM-DD", "reason"}}; a malformed entry is reported and registers
    nothing."""
    out = {"bounds": {}, "invalid": []}
    if not isinstance(data, dict) or data.get("format") != REGISTRY_FORMAT:
        if data is not None:
            out["invalid"].append((str(data)[:120], "not a {} file".format(REGISTRY_FORMAT)))
        return out
    for entry in data.get("bounds") or []:
        if not isinstance(entry, dict) or not entry.get("test") or not entry.get("owner"):
            out["invalid"].append((str(entry)[:160], "needs test and owner"))
            continue
        skip = entry.get("skip")
        if skip is not None and (not isinstance(skip, dict) or not skip.get("source_sha256") or
                                 not skip.get("reason") or
                                 not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(skip.get("expires")))):
            out["invalid"].append((str(entry)[:160], "a skip exception needs source_sha256, expires (YYYY-MM-DD) "
                                                     "and reason"))
            continue
        out["bounds"][str(entry["test"])] = entry
    return out


def skip_excused(entry, mark, today=None):
    """True when a registry entry's dated skip exception still covers the mark (unexpired, same source)."""
    skip = (entry or {}).get("skip")
    if not skip or not mark:
        return False
    return str(skip.get("expires")) >= (today or _today()) and skip.get("source_sha256") == mark.get("source_sha256")


def perf_plan(marks, bounds, today=None):
    """{test id: mode} for the worker: "skip" for a mark with a valid skip exception (it keeps
    HOUSE_SIM_PERF_TESTS=0 and skips), "helper" for a mark that calls wall_clock_bound only (relaxed, real clock),
    "count" for every other mark (relaxed, counting clock). marks: flat_marks; bounds: registry_entries' bounds."""
    plan = {}
    for tid, mark in sorted(marks.items()):
        if skip_excused(bounds.get(tid), mark, today):
            plan[tid] = "skip"
        else:
            plan[tid] = "helper" if mark.get("kind") == "helper" else "count"
    return plan


def unregistered(marks, bounds):
    """The perf marks the registry does not list (a new bound: the main session registers it after review)."""
    return sorted(tid for tid in marks if tid not in bounds)


def committed_registry(root=None):
    """(data or None, note) of the registry as committed at git HEAD of `root` (default: this project); before the
    registry is first committed (the close-out's bootstrap), the working-tree file with a note saying so. The tier-1
    gate reads it through its own trusted policy reader instead (committed only)."""
    root = Path(root or ROOT)
    try:
        show = subprocess.run(["git", "-C", str(root), "show", "HEAD:" + REGISTRY], capture_output=True)
    except OSError:
        show = None
    if show is not None and show.returncode == 0:
        try:
            return json.loads(show.stdout.decode("utf-8")), "committed at HEAD"
        except ValueError as exc:
            return None, "unreadable committed registry ({})".format(exc)
    try:
        with open(root / REGISTRY, encoding="utf-8") as f:
            return json.load(f), "NOT COMMITTED: the working-tree registry (it counts once the main session commits it)"
    except (OSError, ValueError):
        return None, "no registry"


def load_plan(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def target_files(targets, cwd):
    """The test files of `python -m unittest` targets ("tests/test_x.py", "tests.test_x[.Class[.method]]")."""
    files = []
    for target in targets:
        path = Path(cwd) / target
        if target.endswith(".py") and path.is_file():
            files.append(path)
            continue
        parts = target.split(".")
        for n in range(len(parts), 0, -1):
            candidate = Path(cwd).joinpath(*parts[:n]).with_suffix(".py")
            if candidate.is_file():
                files.append(candidate)
                break
    return sorted(set(files))


# ----------------------------------------------------------------------------------------------------------------
# relaxing a bound in a correctness run

class CountingClock:
    """time.perf_counter & co. advancing TICK_S per call: every interval a bound measures is a few microseconds."""

    def __init__(self):
        self.t = time.perf_counter()

    def now(self):
        self.t += TICK_S
        return self.t

    def now_ns(self):
        return int(self.now() * 1e9)


def relax(test, mode):
    """Prepare one perf-marked test of a correctness run (mode from perf_plan) and return the function that undoes
    it. "count" and "helper": HOUSE_SIM_PERF_TESTS=relaxed (the legacy guard runs the body; the helper records);
    "count" also installs a counting clock in the time module and wherever the test's module bound the real
    functions by name (`from time import perf_counter`). "skip": nothing (the test skips as before)."""
    if mode not in ("count", "helper"):
        return lambda: None
    old_env = os.environ.get(PERF_ENV)
    os.environ[PERF_ENV] = RELAXED
    patched = []
    if mode == "count":
        clock = CountingClock()
        fakes = {}
        for name in CLOCKS:
            fakes[getattr(time, name)] = clock.now
            fakes[getattr(time, name + "_ns")] = clock.now_ns
            patched.append((time, name, getattr(time, name)))
            patched.append((time, name + "_ns", getattr(time, name + "_ns")))
        module = sys.modules.get(type(test).__module__)
        for key, value in list(vars(module).items()) if module is not None else ():
            try:
                fake = fakes.get(value)
            except TypeError:
                fake = None
            if fake is not None:
                patched.append((module, key, value))
        for owner, key, value in patched:
            setattr(owner, key, fakes[value])

    def undo():
        for owner, key, value in reversed(patched):
            setattr(owner, key, value)
        if old_env is None:
            os.environ.pop(PERF_ENV, None)
        else:
            os.environ[PERF_ENV] = old_env
    return undo
