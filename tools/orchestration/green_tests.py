"""Run every tests/test_*.py normally and with HOUSE_SIM_STRICT=1 (parallel), summarise results.

usage: python green_tests.py [jobs] [pattern]
"""
import concurrent.futures as cf
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
pattern = sys.argv[2] if len(sys.argv) > 2 else "test_*.py"
files = sorted(glob.glob(os.path.join(ROOT, "tests", pattern)))


def run(path, strict):
    env = dict(os.environ)
    env.pop("HOUSE_SIM_DOMAINS", None)
    if strict:
        env["HOUSE_SIM_STRICT"] = "1"
    else:
        env.pop("HOUSE_SIM_STRICT", None)
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "unittest", os.path.relpath(path, ROOT)], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stderr + p.stdout
    m = re.search(r"Ran (\d+) test", out)
    return {"file": os.path.basename(path), "strict": strict, "rc": p.returncode, "ran": int(m.group(1)) if m else 0,
            "secs": round(time.time() - t, 1), "tail": "" if p.returncode == 0 else out[-3000:]}


with cf.ThreadPoolExecutor(jobs) as ex:
    results = list(ex.map(lambda a: run(*a), [(f, s) for f in files for s in (False, True)]))
bad = [r for r in results if r["rc"]]
for r in results:
    print("{:4} {:32} strict={!s:5} ran={:4} {:6}s".format("FAIL" if r["rc"] else "ok", r["file"], r["strict"], r["ran"], r["secs"]))
print("TOTAL tests (normal run):", sum(r["ran"] for r in results if not r["strict"]), "failures:", len(bad))
out = os.path.join(os.path.dirname(__file__), "green_tests.json")
json.dump(results, open(out, "w", encoding="utf-8"), indent=1)
for r in bad:
    print("=" * 30, r["file"], "strict" if r["strict"] else "normal")
    print(r["tail"][-1500:])
