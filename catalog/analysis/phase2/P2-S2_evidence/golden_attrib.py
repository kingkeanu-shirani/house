"""Independent attribution of the re-recorded room-golden spec entries (P2-S2 merge gate).

For each spec entry whose final state digest differs between HEAD's tests/data/room_golden.json and the working
tree's, re-run the reference on the merged tree (tools/golden.run_spec, keep=True) and compare:
  * the digest of the full final state with the working golden's entry (the re-record is current);
  * the digest of the final state with WP2's new logs (logs.gone, logs.mess_flows) put back as HEAD had them with
    HEAD's golden entry (the change is those logs alone).
usage: python golden_attrib.py OUT.json
"""
import copy
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(ROOT)

import golden  # noqa: E402
from house_sim.kernel.plugins import load_domains  # noqa: E402
from house_sim.kernel.predicates import digest  # noqa: E402

load_domains()
head = json.loads(subprocess.run(["git", "show", "HEAD:tests/data/room_golden.json"], capture_output=True, text=True,
                                 encoding="utf-8", cwd=ROOT).stdout)
work = json.load(open(os.path.join(ROOT, "tests", "data", "room_golden.json"), encoding="utf-8"))
specs = {key: spec for key, _f, spec in golden.collect_specs()}
changed = sorted(k for k in set(head["specs"]) | set(work["specs"]) if head["specs"].get(k) != work["specs"].get(k))
rows = []
for key in changed:
    a, b = head["specs"].get(key), work["specs"].get(key)
    res = golden.run_spec(specs[key], keep=True)
    state = res.pop("full")
    logs = state.get("logs") or {}
    variants = {}
    for name, edit in (("drop gone+flows", lambda s: [s["logs"].pop(k, None) for k in ("gone", "mess_flows")]),
                       ("gone+flows None", lambda s: s["logs"].update(gone=None, mess_flows=None)),
                       ("drop gone", lambda s: s["logs"].pop("gone", None)),
                       ("gone None", lambda s: s["logs"].update(gone=None))):
        s = copy.deepcopy(state)
        s.setdefault("logs", {})
        edit(s)
        variants[name] = digest(s)
    match = [n for n, d in variants.items() if d == (a or {}).get("state")]
    rows.append({"key": key, "current_equals_work": res["state"] == (b or {}).get("state"),
                 "fields_changed": sorted(f for f in set(a or {}) | set(b or {}) if (a or {}).get(f) != (b or {}).get(f)),
                 "passes": res["passes"], "steps": [a.get("steps"), b.get("steps"), res["steps"]],
                 "gone": [g.get("entity") for g in logs.get("gone") or ()][:8],
                 "mess_flows": len(logs.get("mess_flows") or ()),
                 "head_digest_after_strip": match})
    print(key, rows[-1]["current_equals_work"], rows[-1]["fields_changed"], match, rows[-1]["gone"][:3],
          rows[-1]["mess_flows"])
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
    json.dump({"changed": changed, "rows": rows,
               "all_attributed": all(r["current_equals_work"] and r["head_digest_after_strip"] and
                                     r["fields_changed"] == ["state"] and r["passes"] for r in rows)}, f, indent=1)
print("all attributed:", all(r["current_equals_work"] and r["head_digest_after_strip"] and
                             r["fields_changed"] == ["state"] and r["passes"] for r in rows))
