"""Behavioural evidence reuse (docs/design/VERIFY_HARDENING.md 22.7, 21.1; WP10).

usage: python tools/evidence_replay.py [--changed FILES ...] [--since REV] [--workers N] [--out DIR] [--evidence FILE]
                                       [--ids ID ...] [--today YYYY-MM-DD] [--no-write]

Evidence is reused while the behaviour it was measured on is unchanged. Every changed file falls into one class
(house_sim/data/verify/components.json; classify):

  spec        that spec runs full (its spec hash changed; nothing to replay).
  simulation  (kernel world, engine, physics, camera, realism, the domain systems, skills, generator, setup, type data)
              every record whose code footprint meets the change is replayed: the reference on every seed of V in the
              evaluation mode and the idle run on the first seed, with fine ticks. Every digest identical: the record
              is `carried` (verdict kept, carried_from set). Any digest different: `stale`, the spec runs full.
  evaluator   (ledger, evaluation, speech, reports, perceive, verify, battery, strength, escapes, data/verify, data/speech)
              a replay cannot see a change of judgment: nothing is carried; the records that used the changed component
              are `stale` without a replay.
  harness     (house_sim/eval, the viewer, the leaderboard, tools, docs, tests) nothing.

The run fingerprint (run_fingerprint): per seed of V in the evaluation mode the digest of the reference trace (commands,
arguments, refusals, step results), the state digests after every top-level step and after settle, the digest of the
observation index (perceived fields and tiers), the idle projection digest on the first seed, the build digest (built
config and generator fingerprint); plus the spec hash. Its `footprint` lists the project modules the run executed
(footprint_of: a profile hook on function entry; about 15-30 % on the replay, which is why it is measured once, at the
full run, and only on the first seed).

Statuses (replay_status): `current` (the record's fingerprint is the code's), `carried` (replayed identical, within the
14-day staleness limit counted from its last full run), `overdue` (carried past the limit: dataset releases wait, 22.7),
`stale` (with its reason). The result is written to DIR/evidence_replay.json (default reports/evidence_replay.json) and
printed as a table; exit status 0 when nothing is stale. Each record's `evidence` block is updated in its file (status,
carried_from, replays, stale_since; --no-write leaves the file alone): what `house_sim.eval.splits` and the harness read
(21.3: a carried record counts as current until its staleness limit). The gate's G11 uses the same statuses on its own
store.

Stdlib only. No network.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMPONENTS_FILE = ROOT / "house_sim" / "data" / "verify" / "components.json"
EVIDENCE_FILE = "reports/verification.jsonl"
STALENESS_DAYS = 14                 # 22.7: a carried record counts as current for 14 days after its last full run
RELEASE_STALE_SHARE = 0.10          # 22.7: a commit making more than 10 % of the catalog stale is gated as a release
MISS_LIMIT = 0.01                   # 22.7: above 1 % replay misses over 14 days the replay adds per-family runs
_COMPONENTS = {}


def components(path=None):
    """house_sim/data/verify/components.json (read once)."""
    key = str(path or COMPONENTS_FILE)
    if key not in _COMPONENTS:
        with open(key, encoding="utf-8") as stream:
            _COMPONENTS[key] = json.load(stream)
    return _COMPONENTS[key]


def _matches(rel, patterns):
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def classify(rel, table=None):
    """The change class of a file: spec, evaluator, simulation or harness (in that order of precedence after the harness
    folders, which hold no simulation code)."""
    table = table or components()
    classes = table["classes"]
    rel = str(rel).replace("\\", "/")
    for name in ("spec", "evaluator", "harness", "simulation"):
        if _matches(rel, classes.get(name) or ()):
            return name
    return "harness"


def changed_components(files, table=None):
    """The evaluator components the changed files belong to."""
    table = table or components()
    out = set()
    for rel in files:
        rel = str(rel).replace("\\", "/")
        for name, row in table["components"].items():
            if _matches(rel, row.get("files") or ()):
                out.add(name)
    return sorted(out)


def _uses(spec, record, row):
    """Whether a record's runs exercised a component (components.json "when")."""
    when = row.get("when", "always")
    if when == "always":
        return True
    if when == "never":
        return False
    text = json.dumps(spec, sort_keys=True, default=str)
    for key in when.get("spec_keys") or ():
        if '"{}"'.format(key) in text:
            return True
    for form in when.get("predicates") or ():
        if '"{}"'.format(form) in text:
            return True
    actions = set()
    for trial in (record or {}).get("trials") or ():
        ref = trial.get("reference") if isinstance(trial, dict) else None
        actions.update((ref or {}).get("actions") or ())
    wanted = set(when.get("actions") or ())
    if wanted & actions or any('"{}"'.format(a) in text for a in wanted):
        return True
    if when.get("command_classes"):
        for run in ((record or {}).get("battery") or {}).get("runs") or ():
            if isinstance(run, dict) and str(run.get("family")) == "B12":
                return True
        comm = _comm_actions()
        if comm and (actions & comm or any('"{}"'.format(a) in text for a in comm)):
            return True
    return False


def _comm_actions():
    try:
        with open(ROOT / "house_sim" / "data" / "verify" / "actions.json", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return set()
    classes = data.get("classes") if isinstance(data, dict) else None
    return set((classes or {}).get("COMM") or ())


def record_components(spec, record=None, table=None):
    """The evaluator components a record's runs exercised (22.7; `speech` for a spec with COMM commands or `said`
    predicates, ...). What a change of that component makes stale."""
    table = table or components()
    return sorted(name for name, row in table["components"].items() if _uses(spec, record, row))


# ----------------------------------------------------------------------------------------------------------------
# the run fingerprint

def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _index_digest(env):
    """The digest of the observation index (perceived fields and tiers, 15.2) of an episode."""
    from house_sim.tasks import perceive
    idx = perceive.index(env)
    rows = {"{}|{}".format(*key): [[o.t, o.level, repr(o.shown)] for o in value] for key, value in idx.fields.items()}
    views = {str(key): [[v.t, repr(v.identified)] for v in value] for key, value in idx.views.items()}
    return _digest({"fields": rows, "views": views})


class _Footprint:
    """The project modules a run executes: a profile hook on function entry, recording each code file once."""

    def __init__(self):
        self.files = set()
        self.root = str(ROOT).replace("\\", "/") + "/"

    def __enter__(self):
        seen = self.files

        def hook(frame, event, arg):
            if event == "call":
                seen.add(frame.f_code.co_filename)
        self._old = sys.getprofile()
        sys.setprofile(hook)
        return self

    def __exit__(self, *exc):
        sys.setprofile(self._old)
        return False

    def modules(self):
        out = set()
        for name in self.files:
            name = name.replace("\\", "/")
            if name.startswith(self.root):
                rel = name[len(self.root):]
                if rel.startswith("house_sim/"):
                    out.add(rel)
        return sorted(out)


def run_fingerprint(spec, seeds=None, gen=None, footprint=False):
    """The behaviour a record's evidence was measured on (22.7): {"digest", "spec_hash", "seeds": {seed: {...}}, "idle"
    (the first seed's projection digest), "build_generator", "footprint" (with footprint=True, the first seed's modules)}.
    A seed that does not replay gives an "error" entry, whose digest differs from any clean run's."""
    from house_sim.generator import disk_cache
    from house_sim.tasks import seeds as seeds_mod
    from house_sim.tasks import verify
    from house_sim.tasks.runner import build
    mode = verify.EVAL_GEOM
    perception, camera = verify.mode_settings(mode)
    seeds = list(seeds) if seeds else seeds_mod.coverage(spec)["seeds"]
    out = {"spec_hash": verify.spec_hash(spec), "mode": mode, "seeds": {}, "build_generator": disk_cache.fingerprint(),
           "verification": list(seeds)}
    modules = None
    for i, seed in enumerate(seeds):
        row = {}
        try:
            config, task, oracle = build(spec, seed, camera=camera, perception=perception, gen=gen)
            row["build"] = verify.digest(config)
            row["task"] = verify.digest(task)
            idle_env = verify._hard_env(config, task, seed, mode, record=False)
            projections, idle_rec = verify.idle_rollout(idle_env, task)
            if i == 0:
                out["idle"] = projections.digest()
            steps = []

            def snapshot(k, runner, _env=None):
                steps.append(_state_of(runner))

            if footprint and i == 0:
                with _Footprint() as fp:
                    ref, env, runner = verify.reference_run(config, task, oracle, seed, mode, projections,
                                                            snapshot=snapshot)
                modules = fp.modules()
            else:
                ref, env, runner = verify.reference_run(config, task, oracle, seed, mode, projections, snapshot=snapshot)
            trace = [{k: e.get(k) for k in ("top", "action", "target", "destination", "args", "ok", "category", "t0", "t1")}
                     for e in runner.trace or ()]
            row.update(trace=_digest(trace), steps=_digest(steps), final=verify.digest(env.world.s),
                       observations=_index_digest(env), success=ref.get("success"),
                       refusals=_digest(ref.get("refusals") or []))
        except Exception as exc:        # noqa: BLE001  (a replay that raises differs from the clean run it replays)
            row["error"] = "{}: {}".format(type(exc).__name__, str(exc)[:200])
        out["seeds"][str(seed)] = row
    if modules is not None:
        out["footprint"] = modules
    out["digest"] = _digest({k: out.get(k) for k in ("spec_hash", "seeds", "idle", "build_generator")})
    return out


def _state_of(runner):
    from house_sim.tasks import verify
    env = getattr(runner, "env", None)
    return verify.digest(env.world.s) if env is not None else None


def footprint_meets(footprint, changed):
    """True when a change meets a record's code footprint: a changed module it executed, or a change outside code the
    footprint can name (type data, a generator file): data files and the generator are read by every run. Without a
    footprint: True (replay it)."""
    if not footprint:
        return True
    modules = set(footprint)
    for rel in changed:
        rel = str(rel).replace("\\", "/")
        if not rel.endswith(".py") or rel.startswith("house_sim/generator/") or rel in modules:
            return True
    return False


# ----------------------------------------------------------------------------------------------------------------
# statuses

def _days_between(earlier, later):
    try:
        return (date.fromisoformat(str(later)[:10]) - date.fromisoformat(str(earlier)[:10])).days
    except ValueError:
        return None


def replay_status(record, spec, current_fingerprint, today=None, replay=None, changed=(), changed_classes=None):
    """{"status": current | carried | overdue | stale, "why", "evidence": block} of one record.

    current: the record's fingerprint is the code's. Otherwise, by the class of the change (changed: the files changed
    since the record): an evaluator change that meets a component the record used is stale without a replay; a simulation
    change compares `replay` (run_fingerprint now, or None when the footprint does not meet the change) with the record's
    evidence.run_fingerprint; a harness-only change keeps the record. A carried record is overdue after 14 days."""
    from house_sim.tasks.verify import spec_hash
    today = today or datetime.now(timezone.utc).date().isoformat()
    evidence = dict(record.get("evidence") or {})

    def stale(why):
        evidence.update(status="stale", stale_since=evidence.get("stale_since") or today)
        return {"status": "stale", "why": why, "evidence": evidence}
    if spec is None or record.get("spec_hash") != spec_hash(spec):
        return stale("the spec changed since the record")
    if record.get("fingerprint") == current_fingerprint and not evidence.get("carried_from"):
        evidence["status"] = "current"
        return {"status": "current", "why": None, "evidence": evidence}
    classes = changed_classes or {}
    table = components()
    used = set(evidence.get("components") or record_components(spec, record, table))
    hit = sorted(used & set(changed_components(classes.get("evaluator") or (), table)))
    if classes.get("evaluator") and (hit or not changed_components(classes["evaluator"], table)):
        return stale("evaluator change in component(s) {}".format(", ".join(hit) or "unmapped"))
    last_full = evidence.get("last_full") or str(record.get("date") or today)
    if classes.get("simulation") and footprint_meets(evidence.get("footprint"), classes["simulation"]):
        then = evidence.get("run_fingerprint")
        if replay is None or not then:
            return stale("no run fingerprint to compare with")
        if replay.get("digest") != then:
            return stale("the replay's digests differ from the record's (seeds {})".format(
                ", ".join(_differing_seeds(evidence.get("run_seeds"), replay.get("seeds"))) or "?"))
    evidence.update(carried_from=evidence.get("carried_from") or record.get("fingerprint"), last_full=last_full,
                    replays=list(evidence.get("replays") or []) + [today])
    age = _days_between(last_full, today)
    if age is None or age > STALENESS_DAYS:
        evidence["status"] = "overdue"
        return {"status": "overdue", "why": "last full run {} ({} days ago, limit {})".format(last_full, age,
                                                                                               STALENESS_DAYS),
                "evidence": evidence}
    evidence["status"] = "carried"
    return {"status": "carried", "why": None, "evidence": evidence}


def _differing_seeds(then, now):
    then, now = then or {}, now or {}
    return sorted(s for s in set(then) | set(now) if then.get(s) != now.get(s))


def counts_as_current(record, today=None):
    """21.3: a record at the current fingerprint, or carried by the replay tier inside its staleness limit (the evidence
    block `status` the replay wrote). What splits and the harness read."""
    evidence = record.get("evidence") or {}
    if not evidence.get("carried_from"):
        return False
    today = today or datetime.now(timezone.utc).date().isoformat()
    age = _days_between(evidence.get("last_full"), today)
    return evidence.get("status") == "carried" and age is not None and age <= STALENESS_DAYS


def release_needed(stale, total):
    """22.7: a commit making more than 10 % of the catalog stale is gated as a release."""
    return bool(total) and len(stale) / float(total) > RELEASE_STALE_SHARE


def rolling_due(records, today=None, limit=None, days=STALENESS_DAYS):
    """The rolling full run's next specs (22.7): every record whose last full run is older than `days`, oldest first,
    then (when `limit` leaves room) the next oldest, so every spec is re-run at least every 14 days. [catalog id]."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    rows = []
    for record in records:
        evidence = record.get("evidence") or {}
        last = evidence.get("last_full") or record.get("date") or ""
        age = _days_between(last, today)
        rows.append((-(age if age is not None else 10 ** 6), str(last), record.get("catalog_id"), age))
    rows.sort()
    overdue = [r[2] for r in rows if r[3] is None or r[3] >= days]
    others = [r[2] for r in rows if not (r[3] is None or r[3] >= days)]
    picked = overdue + others
    return picked[:limit] if limit is not None else overdue


def replay_misses(carried, fresh):
    """The replay-miss audit (22.7): {catalog id: what changed} between a carried record and the rolling run's new full
    result on the same spec: its verdict, a gate, or a family's outcomes. {"misses", "rate", "added_runs"}: above 1 % of
    the carried specs the replay adds one run per applicable family on the first seed."""
    misses = {}
    for cid, old in carried.items():
        new = fresh.get(cid)
        if new is None:
            continue
        what = []
        if old.get("verdict") != new.get("verdict"):
            what.append("verdict {} -> {}".format(old.get("verdict"), new.get("verdict")))
        for gate, value in (old.get("gates") or {}).items():
            if (new.get("gates") or {}).get(gate) != value:
                what.append("gate {} {} -> {}".format(gate, value, (new.get("gates") or {}).get(gate)))
        before, after = _family_outcomes(old), _family_outcomes(new)
        for family in sorted(set(before) | set(after)):
            if before.get(family) != after.get(family):
                what.append("family {} {} -> {}".format(family, before.get(family), after.get(family)))
        if what:
            misses[cid] = what
    audited = sum(1 for cid in carried if cid in fresh)
    rate = len(misses) / float(audited) if audited else 0.0
    return {"misses": misses, "audited": audited, "rate": rate, "added_runs": rate > MISS_LIMIT}


def _family_outcomes(record):
    out = {}
    for run in (record.get("battery") or {}).get("runs") or ():
        if isinstance(run, dict):
            row = out.setdefault(str(run.get("family")), {})
            row[str(run.get("outcome"))] = row.get(str(run.get("outcome")), 0) + 1
    return out


def stamp_evidence(record, spec, fingerprint=None, today=None):
    """The evidence block a fresh full run gets (21.3): run fingerprint, last full run, footprint and components."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    seeds = (record.get("seeds") or {}).get("verification") or None
    fp = fingerprint if fingerprint is not None else run_fingerprint(spec, seeds, footprint=True)
    return {"run_fingerprint": fp.get("digest"), "run_seeds": fp.get("seeds"), "carried_from": None, "replays": [],
            "stale_since": None, "last_full": today, "footprint": fp.get("footprint"), "status": "current",
            "components": record_components(spec, record)}


# ----------------------------------------------------------------------------------------------------------------
# the command

def read_records(path):
    """The evidence records v2 of a JSONL file ({catalog id: last record}); legacy records are not this tool's."""
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("format", "")).endswith("/2") and row.get("catalog_id"):
            out[row["catalog_id"]] = row
    return out


def write_back(path, rows):
    """Update each record's `evidence` block in its JSONL file ({catalog id: status row}); other lines stay as they are."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    out, changed = [], 0
    for line in lines:
        text = line.strip()
        try:
            row = json.loads(text) if text else None
        except ValueError:
            row = None
        status = rows.get(row.get("catalog_id")) if isinstance(row, dict) and \
            str(row.get("format", "")).endswith("/2") else None
        if status is not None and row.get("evidence") != status.get("evidence"):
            row["evidence"] = status.get("evidence")
            line = json.dumps(row, sort_keys=True, default=str)
            changed += 1
        out.append(line)
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(out) + "\n")
    return changed


def changed_since(rev, root=ROOT):
    res = subprocess.run(["git", "-C", str(root), "diff", "--name-only", rev], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise SystemExit("git diff failed: " + res.stderr.strip())
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def split_classes(files):
    out = {}
    for rel in files:
        out.setdefault(classify(rel), []).append(str(rel).replace("\\", "/"))
    return out


def _replay_job(job):
    spec, seeds = job
    from house_sim.kernel.plugins import load_domains
    load_domains()
    return spec.get("catalog_id"), run_fingerprint(spec, seeds)


def run(evidence=None, changed=None, workers=1, today=None, ids=None, specs=None, root=ROOT, current=None):
    """The replay tier over an evidence file: {"rows": {catalog id: status row}, "classes", "stale", "release", ...}."""
    from house_sim.tasks.verify import is_hardened
    records = read_records(evidence or Path(root) / EVIDENCE_FILE)
    if specs is None:
        from house_sim.eval.common import load_authored
        specs = load_authored(root)[0]
    if current is None:
        from house_sim import audit
        current = audit.fingerprint()
    classes = split_classes(changed or ())
    rows, jobs = {}, []
    for cid, record in sorted(records.items()):
        if ids and cid not in ids:
            continue
        spec = specs.get(cid)
        if spec is None or not is_hardened(spec):
            rows[cid] = {"status": "stale", "why": "no schema-4 spec with this id", "evidence": record.get("evidence")}
            continue
        needs = classes.get("simulation") and footprint_meets((record.get("evidence") or {}).get("footprint"),
                                                              classes["simulation"])
        if needs and not classes.get("evaluator"):
            jobs.append((spec, (record.get("seeds") or {}).get("verification")))
        else:
            rows[cid] = replay_status(record, spec, current, today, None, changed or (), classes)
    if jobs:
        if workers and workers > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                replays = dict(pool.map(_replay_job, jobs))
        else:
            replays = dict(_replay_job(job) for job in jobs)
        for spec, _ in jobs:
            cid = spec["catalog_id"]
            rows[cid] = replay_status(records[cid], spec, current, today, replays.get(cid), changed or (), classes)
    stale = sorted(c for c, r in rows.items() if r["status"] == "stale")
    return {"format": "house_ai.evidence_replay/1", "date": today or datetime.now(timezone.utc).date().isoformat(),
            "classes": classes, "rows": rows, "stale": stale,
            "carried": sorted(c for c, r in rows.items() if r["status"] == "carried"),
            "current": sorted(c for c, r in rows.items() if r["status"] == "current"),
            "overdue": sorted(c for c, r in rows.items() if r["status"] == "overdue"),
            "release": release_needed(stale, len(specs)), "catalog": len(specs)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Behavioural evidence reuse (VERIFY_HARDENING 22.7)")
    p.add_argument("--changed", nargs="*", help="the changed files (default: git diff against --since, default HEAD)")
    p.add_argument("--since", default="HEAD", help="the revision the records' code was at (default HEAD)")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out", help="output folder (default reports)")
    p.add_argument("--evidence", help="evidence file (default {})".format(EVIDENCE_FILE))
    p.add_argument("--ids", nargs="*")
    p.add_argument("--today")
    p.add_argument("--no-write", action="store_true", help="leave the evidence file's records alone")
    args = p.parse_args(argv)
    from house_sim.kernel.plugins import load_domains
    load_domains()
    started = time.perf_counter()
    changed = args.changed if args.changed is not None else changed_since(args.since)
    result = run(args.evidence, changed, args.workers, args.today, set(args.ids) if args.ids else None)
    result["wall_s"] = round(time.perf_counter() - started, 1)
    if not args.no_write:
        result["records_updated"] = write_back(args.evidence or ROOT / EVIDENCE_FILE, result["rows"])
    out = Path(args.out or ROOT / "reports")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "evidence_replay.json", "w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=1, sort_keys=True, default=str)
        stream.write("\n")
    for cid, row in sorted(result["rows"].items()):
        print("{:9} {} {}".format(row["status"], cid, row.get("why") or ""))
    print("changed: {}; {} carried, {} current, {} overdue, {} stale{}".format(
        ", ".join("{} {}".format(len(v), k) for k, v in sorted(result["classes"].items())) or "nothing",
        len(result["carried"]), len(result["current"]), len(result["overdue"]), len(result["stale"]),
        "; a RELEASE (more than 10 % of the catalog stale: verify overnight on a branch, 22.7)"
        if result["release"] else ""))
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    os.environ.setdefault("HOUSE_SIM_WORKER", "0")
    sys.exit(main())
