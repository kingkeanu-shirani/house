"""Add the verified test-removal rows of record P1C to the verified sign-off and write the final file (INT3).

The record's G1 lists the tests of the previous record (P1F) that are gone (per-test ids on both sides). A row is
signed only when the test existed at BASE (P1F's tree), is absent at HEAD and in the working tree, and a named
replacement exists in the same file and ran and passed in the record's G1 in the same mode.

usage: python finish_signoff_p1c.py VERIFIED CUR_DIR BASE OUT
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
# removed test -> (replacement, why): K-GATE3 renamed both when the correctness runs began to relax the bounds
# instead of turning them off (closeout_K-GATE3.json finding 4; tools/orchestration/wallclock.py)
REPLACED = {
    "tests.test_gate_tool.SecondPassGateTests.test_the_gates_correctness_runs_turn_the_bounds_off":
        ("tests.test_gate_tool.SecondPassGateTests.test_the_gates_correctness_runs_relax_the_bounds",
         "K-GATE3 replaced 'the gate's correctness runs turn the bounds off' by 'relax the bounds' (the same setup; it "
         "now also checks the relax plan -- count for a guarded bound, skip for the registry's dated exception -- and "
         "the py39 lint job in G1)"),
    "tests.test_gate_tool.SecondPassGateTests.test_the_worker_marks_bounds_and_the_perf_job_retries_them":
        ("tests.test_gate_tool.SecondPassGateTests.test_the_worker_relaxes_bounds_and_the_perf_job_retries_them",
         "K-GATE3 replaced 'the worker marks bounds (outcome perf) and the perf job retries them' by 'the worker "
         "relaxes bounds (their bodies run under a counting clock) and the perf job retries them' (the same fixture "
         "file of bounds)"),
}


def method_in(text, test_id):
    return bool(re.search(r"def {}\(".format(re.escape(test_id.split(".")[-1])), text))


def main():
    verified_path, cur_dir, base, out_path = sys.argv[1:5]
    verified = json.load(open(verified_path, encoding="utf-8"))
    tests = json.load(open(os.path.join(cur_dir, "tests.json"), encoding="utf-8"))
    sys.path.insert(0, os.path.join(ROOT, "tools", "orchestration"))
    import tier1_gate as T
    prev = json.load(open(os.path.join(os.path.dirname(cur_dir), json.load(open(os.path.join(
        cur_dir, "meta.json"), encoding="utf-8"))["prev"], "tests.json"), encoding="utf-8"))
    bad, _excused = T.test_regressions(prev, tests)
    added, rejected = [], []
    for check, file, mode, test, what in bad:
        row = {"kind": "test-removal", "test": test, "mode": mode}
        problems = []
        if test not in REPLACED:
            problems.append("no replacement named for it")
        src_base = subprocess.run(["git", "show", "{}:tests/{}".format(base, file)], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8").stdout
        src_head = subprocess.run(["git", "show", "HEAD:tests/{}".format(file)], cwd=ROOT, capture_output=True,
                                  text=True, encoding="utf-8").stdout
        src_now = open(os.path.join(ROOT, "tests", file), encoding="utf-8").read()
        if not method_in(src_base, test):
            problems.append("not in tests/{} at {}".format(file, base))
        if method_in(src_head, test) or method_in(src_now, test):
            problems.append("still defined (HEAD or working tree)")
        replacement, why = REPLACED.get(test, (None, ""))
        run = next((r for r in tests.get(check) or [] if r.get("file") == file and r.get("mode") == mode), {})
        failing = {t for k, v in (run.get("outcomes") or {}).items() for t in v}
        if replacement and not (method_in(src_now, replacement) and replacement in (run.get("ids") or []) and
                                replacement not in failing and not run.get("rc")):
            problems.append("replacement {} did not run and pass in {} {}".format(replacement, check, mode))
        if problems:
            rejected.append(dict(row, why="; ".join(problems)))
            continue
        row["evidence"] = "{} {}: {} ({}); removed at {} -> HEAD (in tests/{} at {}, not at HEAD), the replacement " \
                          "{} ran and passed in this record's {} {} run ({} tests, rc 0)".format(
                              check, what, why, "renamed", base, file, base, replacement.split(".")[-1], check, mode,
                              run.get("ran"))
        added.append(row)
    verified["rows"] = verified["rows"] + added
    verified.setdefault("rejected", []).extend(rejected)
    verified["summary"]["signed"] = dict(Counter(r["kind"] + ":" + r.get("class", r["kind"]) + ":" + r.get("mode", "")
                                                 for r in verified["rows"]))
    verified["summary"]["rejected"] = len(verified.get("rejected") or [])
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(verified, f, indent=1)
    print(verified["summary"])
    for r in rejected:
        print("REJECTED", r)


if __name__ == "__main__":
    main()
