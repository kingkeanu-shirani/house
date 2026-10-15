"""Verify every authored spec, or list the schema-2 plans that wait on hidden state.

usage: python tools/verify.py                         python -m house_sim --verify (writes the verification evidence)
       python tools/verify.py --privileged-waits [--json]
              the migration debt of TIER1_SWEEP 6.6 / 19.3 (M-WAITS): every schema-2 spec whose reference or wrong
              plans use wait_until (it polls the true state, which no robot can see), with its owning domain, the
              number of such steps per plan kind and the paths they poll. Schema-3 specs cannot have them: their
              wait_until compiles to an agent wait(until=...) that the environment refuses when the condition is not
              perceivable. The verifier also records the reference steps executed per trial ("privileged_waits").
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _paths(value, found):
    if isinstance(value, dict):
        for key in ("path", "ref"):
            if isinstance(value.get(key), str):
                found.add(value[key])
        for item in value.values():
            _paths(item, found)
    elif isinstance(value, list):
        for item in value:
            _paths(item, found)
    return found


def _waits(plan, out):
    """The wait_until steps of a plan (nested foreach / repeat bodies included)."""
    for step in plan or ():
        if not isinstance(step, dict):
            continue
        if "wait_until" in step:
            out.append(step)
        for key in ("steps",):
            if isinstance(step.get(key), list):
                _waits(step[key], out)
    return out


def privileged_waits():
    """[{catalog_id, file, domain, reference, wrong, paths}] for every schema-2 spec with wait_until in a reference or
    wrong plan (reference / wrong: the number of such steps)."""
    from house_sim.tasks.spec import expand, schema_of
    rows = []
    for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for spec in data.get("specs", []) if isinstance(data, dict) else data:
            try:
                full = expand(spec)
            except Exception:  # noqa: BLE001  (a broken spec is the verifier's business)
                continue
            if schema_of(full) >= 3:
                continue
            scopes = [full] + [v for v in full.get("variants", []) or [] if isinstance(v, dict)]
            reference, wrong = [], []
            for scope in scopes:
                _waits(scope.get("reference", []), reference)
                for item in scope.get("wrong", []) or []:
                    _waits((item or {}).get("plan", []), wrong)
            if not reference and not wrong:
                continue
            polled = set()
            for step in reference + wrong:
                _paths(step["wait_until"], polled)
            rows.append({"catalog_id": spec.get("catalog_id"), "file": path.name, "domain": path.name.split("__")[0],
                         "reference": len(reference), "wrong": len(wrong), "paths": sorted(polled)})
    return rows


def main(argv):
    if "--privileged-waits" in argv:
        rows = privileged_waits()
        if "--json" in argv:
            print(json.dumps(rows, indent=1))
            return 0
        print("{} schema-2 spec(s) whose plans wait on hidden state (migration M-WAITS, by domain):".format(
            len(rows)))
        for row in sorted(rows, key=lambda r: (r["domain"], r["catalog_id"] or "")):
            print("  {:<18} {:<20} reference {} / wrong {} step(s): {}".format(
                row["domain"], row["catalog_id"], row["reference"], row["wrong"],
                ", ".join(row["paths"][:4]) + (" ..." if len(row["paths"]) > 4 else "")))
        return 0
    from house_sim.cli import main as cli_main
    return cli_main(["--verify"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
