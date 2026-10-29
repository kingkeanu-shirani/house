"""The G1b tests that went quiet vs the previous record (count-based: the previous record has no per-test ids): for each
module, the tests skipped now, whether they existed at BASE_REV, and their skip reason.

usage: python quiet_tests.py PREV_DIR CUR_DIR BASE_REV OUT
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "orchestration"))
import tier1_gate as T  # noqa: E402

prev_dir, cur_dir, base, out_path = sys.argv[1:5]
prev = json.load(open(os.path.join(prev_dir, "tests.json"), encoding="utf-8"))
cur = json.load(open(os.path.join(cur_dir, "tests.json"), encoding="utf-8"))
bad, excused = T.test_regressions(prev, cur)
rows = []
for check, file, mode, test, what in bad:
    now = next(r for r in cur[check] if r["file"] == file and r["mode"] == mode)
    old = next((r for r in prev[check] if r["file"] == file and r["mode"] == mode), {})
    skipped = sorted((now.get("outcomes") or {}).get("skip") or [])
    src_now = open(os.path.join(ROOT, "tests", file), encoding="utf-8").read()
    src_base = subprocess.run(["git", "show", "{}:tests/{}".format(base, file)], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8").stdout
    tests = []
    for tid in skipped:
        method = tid.split(".")[-1]
        cls = tid.split(".")[-2]
        existed = bool(re.search(r"def {}\(".format(re.escape(method)), src_base))
        cls_existed = bool(re.search(r"class {}\(".format(re.escape(cls)), src_base))
        tests.append({"test": tid, "existed_at_base": existed, "class_existed_at_base": cls_existed})
    rows.append({"check": check, "file": file, "mode": mode, "module": test, "what": what,
                 "skipped_before": old.get("skipped"), "skipped_now": now.get("skipped"), "skipped_tests": tests,
                 "new_skipped": [t["test"] for t in tests if not t["existed_at_base"]],
                 "old_skipped": [t["test"] for t in tests if t["existed_at_base"]]})
with open(out_path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(rows, f, indent=1)
for r in rows:
    print(r["module"], r["mode"], r["what"], "| new skipped:", len(r["new_skipped"]), "| existed at base:",
          [t.split(".", 2)[-1] for t in r["old_skipped"]])
