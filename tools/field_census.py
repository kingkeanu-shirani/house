"""Field census for the house ledger's field classes (VERIFY_HARDENING 6.3, lint L-WATCH; WP1).

usage: python tools/field_census.py [--ids ID ...] [--files NAME ...] [--seeds 0 1] [--workers N] [--limit N]
                                    [--out PATH] [--quiet]
       python tools/field_census.py --unclassified [--domain D ...] [--json]
       python tools/field_census.py --check CENSUS_JSON
       python tools/field_census.py --reclassify CENSUS_JSON

Census: runs every authored spec's idle check, reference and wrong plans (tasks/authored/*.json) on the seeds
(default 0 and 1, the first verification seeds) and lists every entity field written, with the entity types it was
written on, the writer classes seen and, for physical writes, whether the writer reported a cause (WP2's writer log;
without it the census tells robot-window steps from waits: a field changed in an idle run or inside a wait step is
"physical?" -- a physical or volition write --, one changed in an action step "robot?"). It proposes a class for
every field without an explicit one: written only by physical writers in idle -> process; written by robot actions
-> setting (or condition / content by name); and prints the fields it saw written that have no explicit class (the
error part of L-WATCH). --out writes the census JSON (default catalog/analysis/phase2/field_census.json).

--unclassified: every property name of the type data (house_sim/data/types/<domain>.json) that is classified neither
explicitly (the domain's house_sim/data/verify/watch/<domain>.json, per type or per field) nor by a kernel name or
pattern (house_sim/data/verify/watch_fields.json): the warning part of L-WATCH until the domain's WP-D package lands.

--check: exit 1 when a field the census JSON lists as written has no explicit class (L-WATCH part 1).
--reclassify: recompute the census JSON's class columns (proposed, explicit, unclassified_written) after the field
classes changed, without running anything again.

Fields starting with "_" are never watched and never listed. Nothing is written except --out.
"""
import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUT = ROOT / "catalog" / "analysis" / "phase2" / "field_census.json"
WAIT_ACTIONS = ("wait", "run_pending")


def authored_specs(ids=None, files=None):
    """(file name, spec) of every authored spec, optionally restricted to catalog ids or file names."""
    out = []
    for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
        if files and path.stem not in files and path.name not in files:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for spec in data.get("specs", []) if isinstance(data, dict) else data:
            if ids and spec.get("catalog_id") not in ids:
                continue
            out.append((path.stem, spec))
    return out


class _Recorder(object):
    """Captures the journaled changes of every step of one environment (wrapping its World's commit) with the
    action the step ran."""

    def __init__(self, env, run):
        self.env, self.run, self.action = env, run, None
        self.rows = []
        world = env.world
        original = world.commit

        def commit():
            self.rows.append((self.action, world.changes()))
            return original()
        world.commit = commit
        step = env.step

        def stepped(action, *args, **kwargs):
            self.action = str(action).strip().lower() if isinstance(action, str) else action
            try:
                return step(action, *args, **kwargs)
            finally:
                self.action = None
        env.step = stepped


def _writer_class(env, eid, field, run, action):
    writes = getattr(env, "writes", None)
    if writes is not None and hasattr(writes, "get"):
        found = writes.get((eid, field))
        if found is not None:
            cls = found[0] if isinstance(found, (list, tuple)) else found
            cause = None
            causes = getattr(env, "write_causes", None)
            if causes is not None and hasattr(causes, "get"):
                cause = causes.get((eid, field))
            return cls, cause
    if run == "idle" or action in WAIT_ACTIONS or action is None:
        return "physical?", None
    return "robot?", None


def _harvest(recorder, table):
    env = recorder.env
    for action, changes in recorder.rows:
        for change in changes:
            path = change.get("path") or ""
            parts = path.split(".")
            if len(parts) < 3 or parts[0] != "entities":
                continue
            eid, field = parts[1], parts[2]
            if field.startswith("_"):
                continue
            sub = parts[3] if len(parts) > 3 and not parts[3].startswith("_") else None
            key = field + "." + sub if sub is not None and _is_dict_field(env, eid, field) else field
            record = env.world.s["entities"].get(eid) or env._baseline.get("entities", {}).get(eid) or {}
            type_name = record.get("type") or "?"
            cls, cause = _writer_class(env, eid, field, recorder.run, action)
            row = table.setdefault(key, {"types": {}, "writers": {}, "runs": {}, "caused": 0, "uncaused": 0})
            row["types"][type_name] = row["types"].get(type_name, 0) + 1
            row["writers"][cls] = row["writers"].get(cls, 0) + 1
            row["runs"][recorder.run] = row["runs"].get(recorder.run, 0) + 1
            if cls in ("physical", "physical?"):
                if cause:
                    row["caused"] += 1
                else:
                    row["uncaused"] += 1


def _is_dict_field(env, eid, field):
    """Whether the field is a dict on the entity (its sub-keys are watched one by one)."""
    record = env.world.s["entities"].get(eid) or env._baseline.get("entities", {}).get(eid) or {}
    return isinstance(record.get(field), dict)


def census_spec(args):
    """The census table of one spec over the seeds: {field: {types, writers, runs, caused, uncaused}} plus errors."""
    name, spec, seeds = args
    from house_sim.tasks import verify
    from house_sim.tasks.runner import build, make_env, run_plan
    table, errors = {}, []
    for seed in seeds:
        try:
            config, task, oracle = build(spec, seed)
        except Exception as exc:  # noqa: BLE001  (a spec that does not build is the verifier's business)
            errors.append("{} seed {}: build: {}: {}".format(spec.get("catalog_id"), seed, type(exc).__name__, exc))
            continue
        runs = [("idle", None), ("reference", oracle["reference"])] + \
            [("wrong", item) for item in oracle["wrong"]]
        for run, plan in runs:
            env = make_env(config, task, seed)
            recorder = _Recorder(env, run)
            try:
                if run == "idle":
                    verify.idle(env, task)
                elif run == "reference":
                    run_plan(env, plan, oracle["bindings"], strict=False)
                else:
                    verify.run_wrong_plan(env, plan.get("plan", []), oracle["bindings"])
            except Exception as exc:  # noqa: BLE001  (record what ran; the verifier judges the plan)
                errors.append("{} seed {} {}: {}: {}".format(spec.get("catalog_id"), seed, run, type(exc).__name__,
                                                             str(exc)[:200]))
            _harvest(recorder, table)
    return name, spec.get("catalog_id"), table, errors


def merge(tables):
    out = {}
    for table in tables:
        for field, row in table.items():
            dest = out.setdefault(field, {"types": {}, "writers": {}, "runs": {}, "caused": 0, "uncaused": 0,
                                          "specs": 0})
            for key in ("types", "writers", "runs"):
                for k, v in row[key].items():
                    dest[key][k] = dest[key].get(k, 0) + v
            dest["caused"] += row["caused"]
            dest["uncaused"] += row["uncaused"]
            dest["specs"] += 1
    return out


def propose(field, row):
    """A proposed class for a written field: physical-only (idle or waits) -> process; robot writes -> setting,
    or condition / content when the kernel's name rules say so."""
    from house_sim.kernel import ledger
    writers = set(row["writers"])
    robot = any(w.startswith("robot") for w in writers)
    kernel = ledger._kernel_spec(field)
    if kernel is not None:
        return kernel["class"]
    if not robot:
        return "process"
    return "setting"


def explicit_class(field, types):
    """The explicit class of a written field over the types it was written on (None if any type lacks one)."""
    from house_sim.kernel import ledger
    found = set()
    for type_name in types:
        spec = ledger.field_spec(type_name, field, explicit_only=True)
        if spec is None:
            return None
        found.add(spec["class"])
    return sorted(found)


def run_census(specs, seeds, workers=1, progress=None):
    jobs = [(name, spec, seeds) for name, spec in specs]
    tables, errors, done = [], [], 0
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for name, cid, table, errs in pool.map(census_spec, jobs, chunksize=1):
                tables.append(table)
                errors.extend(errs)
                done += 1
                if progress:
                    progress(done, len(jobs), cid)
    else:
        for job in jobs:
            name, cid, table, errs = census_spec(job)
            tables.append(table)
            errors.extend(errs)
            done += 1
            if progress:
                progress(done, len(jobs), cid)
    fields = merge(tables)
    return classify({"format": 1, "seeds": list(seeds), "specs": len(specs), "fields": fields,
                     "unclassified_written": [], "errors": errors})


def unclassified(domains=None):
    """{domain: [(type, field)]} of every type-data property without an explicit or kernel class."""
    from house_sim.kernel import ledger
    out = {}
    for path in sorted((ROOT / "house_sim" / "data" / "types").glob("*.json")):
        domain = path.stem
        if domains and domain not in domains:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        types = raw.get("types", {}) if isinstance(raw, dict) else {}
        items = types.items() if isinstance(types, dict) else [(t.get("name"), t) for t in types]
        rows = []
        for type_name, record in sorted(items, key=lambda kv: str(kv[0])):
            props = record.get("props", record) if isinstance(record, dict) else {}
            for field in sorted(props):
                if field.startswith("_"):
                    continue
                if ledger.field_spec(type_name, field, explicit_only=True) is None:
                    rows.append((type_name, field))
        out[domain] = rows
    return out


def classify(result):
    """Fill a census result's class columns from the current field classes."""
    rows = result.get("fields") or {}
    for field in sorted(rows):
        row = rows[field]
        row["proposed"] = propose(field, row)
        row["explicit"] = explicit_class(field, row.get("types") or {})
    result["unclassified_written"] = sorted(f for f, r in rows.items() if r["explicit"] is None)
    return result


def check(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    for field, row in sorted((data.get("fields") or {}).items()):
        if explicit_class(field, row.get("types") or {}) is None:
            problems.append(field)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--unclassified", action="store_true")
    parser.add_argument("--domain", nargs="+")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", metavar="CENSUS_JSON")
    parser.add_argument("--reclassify", metavar="CENSUS_JSON")
    args = parser.parse_args(argv)
    if args.reclassify:
        path = Path(args.reclassify)
        result = classify(json.loads(path.read_text(encoding="utf-8")))
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=1, sort_keys=True)
            handle.write("\n")
        print("{} written fields, {} without an explicit class -> {}".format(
            len(result["fields"]), len(result["unclassified_written"]), path))
        return 0
    if args.check:
        problems = check(args.check)
        for field in problems:
            print("L-WATCH: written field without an explicit class: " + field)
        print("{} written field(s) without an explicit class".format(len(problems)))
        return 1 if problems else 0
    if args.unclassified:
        found = unclassified(args.domain)
        if args.json:
            print(json.dumps({d: [list(r) for r in rows] for d, rows in found.items()}, indent=1))
        else:
            total = 0
            for domain, rows in found.items():
                names = sorted({f for _, f in rows})
                total += len(names)
                print("{}: {} field name(s), {} (type, field) pair(s) unclassified".format(domain, len(names),
                                                                                          len(rows)))
                if not args.quiet:
                    print("  " + ", ".join(names))
            print("total: {} field name(s)".format(total))
        return 0
    specs = authored_specs(args.ids, args.files)
    if args.limit:
        specs = specs[:args.limit]

    def progress(done, total, cid):
        if not args.quiet:
            print("[{}/{}] {}".format(done, total, cid), flush=True)
    result = run_census(specs, args.seeds, max(1, args.workers), progress)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print("{} specs, {} written fields, {} without an explicit class, {} errors -> {}".format(
        result["specs"], len(result["fields"]), len(result["unclassified_written"]), len(result["errors"]), out))
    if result["unclassified_written"]:
        print("unclassified written: " + ", ".join(result["unclassified_written"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
