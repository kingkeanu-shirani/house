"""Attribution of the state-digest changes between two gate records (P2-S2 merge gate: "WP1 / WP3 / WP4 / WP7
changed no schema 2/3 outcome; only WP2's logs change digests", VERIFY_HARDENING section 25).

For every trial (own and forced-realistic mode) whose state digests differ between PREV and CUR, re-run the trial on
the working tree (verify.run_trial with capture), take the reference, idle and wrong-plan final states, remove WP2's
new logs (logs.gone, logs.mess_flows) and compare their digests with PREV's. A trial is "attributed" when every
changed digest is restored by removing those logs and its steps / time are unchanged.

usage: python state_attrib.py PREV_DIR CUR_DIR OUT.json [--workers N]
"""
import copy
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
if not os.path.exists(os.path.join(ROOT, "house_sim")):
    ROOT = r"<home>/OneDrive/Desktop/house_ai"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "orchestration"))
WP2_LOGS = ("gone", "mess_flows")


def read_jsonl(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[(r["key"], r["seed"])] = r
    return out


def changed_fields(a, b):
    out = [k for k in ("reference_state_hash", "idle_state_hash", "initial_hash", "task_hash", "reference_steps",
                       "reference_time_s") if a.get(k) != b.get(k)]
    wa, wb = a.get("wrong") or [], b.get("wrong") or []
    if len(wa) != len(wb) or any(x.get("state_hash") != y.get("state_hash") for x, y in zip(wa, wb)):
        out.append("wrong.state_hash")
    return out


def stripped(state):
    s = copy.deepcopy(state)
    logs = s.get("logs")
    if isinstance(logs, dict):
        for k in WP2_LOGS:
            logs.pop(k, None)
    return s


def job(args):
    key, seed, mode, spec, prev = args
    os.chdir(ROOT)
    from house_sim.kernel.plugins import load_domains
    from house_sim.kernel.predicates import digest
    from house_sim.tasks import verify
    load_domains()
    capture = {}
    raw = verify.run_trial(spec, seed, "realistic" if mode == "realistic" else None, capture=capture)
    out = {"key": key, "seed": seed, "mode": mode, "problems": []}
    ref, idle, wrongs = capture.get("reference"), capture.get("idle"), capture.get("wrong") or []
    if ref is not None:
        full, strip = digest(ref.world.s), digest(stripped(ref.world.s))
        if full != prev.get("reference_state_hash") and strip != prev.get("reference_state_hash"):
            out["problems"].append("reference state differs beyond logs.gone / mess_flows")
        out["reference_logs"] = {k: len((ref.world.s.get("logs") or {}).get(k) or ()) for k in WP2_LOGS}
    if idle is not None:
        full, strip = digest(idle.world.s), digest(stripped(idle.world.s))
        if full != prev.get("idle_state_hash") and strip != prev.get("idle_state_hash"):
            out["problems"].append("idle state differs beyond logs.gone / mess_flows")
    pw = prev.get("wrong") or []
    if len(pw) == len(wrongs):
        for w, env in zip(pw, wrongs):
            if env is None or w.get("state_hash") is None:
                continue
            if digest(env.world.s) != w["state_hash"] and digest(stripped(env.world.s)) != w["state_hash"]:
                out["problems"].append("wrong plan {!r} state differs beyond logs.gone / mess_flows".format(
                    w.get("name")))
    else:
        out["problems"].append("wrong-plan count differs")
    for k in ("initial_hash", "task_hash", "reference_steps", "reference_time_s"):
        if raw.get(k) != prev.get(k):
            out["problems"].append("{} differs".format(k))
    if raw.get("passed") != prev.get("passed"):
        out["problems"].append("verdict differs")
    return out


def main():
    prev_dir, cur_dir, out_path = sys.argv[1:4]
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 3
    import tier1_gate
    specs = {key: spec for key, _f, _o, spec in tier1_gate.collect_specs(Path(ROOT))}
    jobs, summary = [], {}
    for mode in ("own", "realistic"):
        a = read_jsonl(os.path.join(prev_dir, "specs_{}.jsonl".format(mode)))
        b = read_jsonl(os.path.join(cur_dir, "specs_{}.jsonl".format(mode)))
        diff = {k: changed_fields(a[k], b[k]) for k in sorted(set(a) & set(b)) if changed_fields(a[k], b[k])}
        obs = sorted(k for k in set(a) & set(b) if a[k].get("reference_obs_hash") != b[k].get("reference_obs_hash"))
        summary[mode] = {"trials": len(b), "state_changed": len(diff), "obs_changed": len(obs),
                         "obs_changed_keys": ["{}#{}".format(*k) for k in obs][:40],
                         "fields": {}}
        for k, fields in diff.items():
            for f in fields:
                summary[mode]["fields"][f] = summary[mode]["fields"].get(f, 0) + 1
            jobs.append((k[0], k[1], mode, specs[k[0]], a[k]))
    with Pool(workers) as pool:
        rows = pool.map(job, jobs, chunksize=1)
    bad = [r for r in rows if r["problems"]]
    out = {"summary": summary, "rerun": len(rows), "attributed": len(rows) - len(bad), "not_attributed": bad,
           "rows": rows}
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(summary, indent=1)[:3000])
    print("re-run", len(rows), "attributed", len(rows) - len(bad), "not attributed", len(bad))
    for r in bad[:30]:
        print("  ", r["key"], r["seed"], r["mode"], r["problems"])


if __name__ == "__main__":
    main()
