"""A/B the final states (reference, idle, every wrong plan) of trials on HEAD's tree and the merged tree; print every
differing path (P2-S2 merge gate: the 4 trials of T-7fe081a3de29f8-01 state_attrib.py could not attribute by
stripping logs alone).

usage: python diff_trials.py HEAD_TREE WORK_TREE OUT.json CID:SEED:MODE ...
"""
import json
import subprocess
import sys

HERE = r"<home>/AppData/Local/Temp/claude/C--Users-USER-OneDrive-Desktop-house-ai/e86e18f6-923d-4bcc-93e1-ba70c8993bc5/scratchpad/p2_P2-S2-merge"


def diff(x, y, p="", out=None):
    if out is None:
        out = []
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            diff(x.get(k, "<absent>"), y.get(k, "<absent>"), p + "." + str(k), out)
    elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (u, v) in enumerate(zip(x, y)):
            diff(u, v, p + "[%d]" % i, out)
    elif x != y:
        out.append(p)
    return out


def main():
    head, work, out_path = sys.argv[1:4]
    rows = []
    for item in sys.argv[4:]:
        cid, seed, mode = item.split(":")
        dumps = {}
        for name, tree in (("head", head), ("work", work)):
            path = "{}/ab_{}_{}_{}_{}.json".format(HERE, cid, seed, mode, name)
            subprocess.run([sys.executable, HERE + "/dump_trial.py", tree, cid, seed, mode, path], check=True,
                           capture_output=True)
            dumps[name] = json.load(open(path, encoding="utf-8"))
        a, b = dumps["head"], dumps["work"]
        paths = {"reference": diff(a["reference"], b["reference"]), "idle": diff(a["idle"], b["idle"])}
        for i, (x, y) in enumerate(zip(a["wrong"], b["wrong"])):
            paths["wrong[%d]" % i] = diff(x, y)
        allowed = all(p.startswith((".logs.gone", ".logs.mess_flows")) or p.endswith("._space_jn[1]")
                      for ps in paths.values() for p in ps)
        rows.append({"trial": item, "paths": paths, "only_wp2_logs_and_journal_cursor": allowed,
                     "passed": [a["passed"], b["passed"]]})
        print(item, allowed, {k: v[:6] for k, v in paths.items() if v})
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
