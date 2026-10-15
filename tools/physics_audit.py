"""Physics audit (kernel v3.0, TIER1_PHYSICS 11-12, TIER1_INTEGRATION 2.4 PHYS-INT).

usage: python tools/physics_audit.py [--data] [--bench] [--out FILE] [--seeds 0 1 2 41 97] [--workers N]
                                     [--spec-files GLOB]

--data   the data layer and the authored specs:
           materials        every material string of the type registry maps to a class (0 unmapped, else exit 1)
           laws             every law with a settle order in laws.json has a registered function (else exit 1)
           gaps             per domain (type file): portable types still at the default mass (0.4 kg) or size
                            (0.1 m cube), and implausible bulk densities (outside 5-9000 kg/m3)
           tier_overrides   every domain field_tiers entry (file level) and type perception block that gives a
                            physics threshold the kernel hides (core.json field_tiers) a perceivable tier
           row_derived      containers whose opening or nest group comes from a supports.json row rather than the
                            type's own fields, and types whose flotation a row decides (PHYS-DATA's review list)
           reference runs   every authored spec's reference plan in its own perception mode on the seeds: every
                            physics consequence (phys.* event) it causes, and the physics lint warnings of its setup
                            (tasks/setup.py physics_problems); runs that fail to execute exit 1
--bench  cost budgets (TIER1_INTEGRATION 3.4): data load, props() per call, settle with nothing touched, settle per
         action over random walks on generated houses (p50, p99), the 64-consequence worst case, physics.processes idle,
         physics.environment windy (wind law forced on), kernel thermal amortized per substep. A measure over 3x its
         budget exits 1 (the tests' rule, so the audit is not flaky on a busy machine).

Without --data or --bench both run. The JSON record goes to --out (default <temp>/house_ai_tier1/physics_audit.json);
the summary is printed. Stdlib only; deterministic apart from the timings.
"""
import argparse
import fnmatch
import glob
import json
import os
import random
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEEDS = (0, 1, 2, 41, 97)
FORMAT = "house-physics-audit-v1"
DEFAULT_MASS_KG, DEFAULT_SIZE = 0.4, [0.1, 0.1, 0.1]
DENSITY_RANGE = (5.0, 9000.0)
# budgets (ms unless noted) and the factor a measure may reach before the audit fails
BUDGETS = {"data_load_ms": 20.0, "props_us": 5.0, "settle_idle_ms": 0.02, "settle_action_p50_ms": 1.0,
           "settle_worst_ms": 10.0, "processes_idle_ms": 0.02, "environment_windy_ms": 0.3, "thermal_substep_ms": 0.5}
FAIL_FACTOR = 3.0
_EVENTS = []


def _quiet():
    import warnings
    warnings.simplefilter("ignore")


# ----------------------------------------------------------------------------------------------------------------
# --data

def hidden_thresholds():
    """The physics fields core.json's file-level field_tiers hides."""
    with open(ROOT / "house_sim" / "data" / "types" / "core.json", encoding="utf-8") as handle:
        data = json.load(handle)
    return set((data.get("field_tiers") or {}).get("hidden") or []) - {"contents_from"}


def audit_registry():
    from house_sim.kernel.phys import props
    from house_sim.kernel.registry import default_registry
    reg = default_registry()
    unmapped, gaps = defaultdict(list), defaultdict(lambda: defaultdict(list))
    row_derived = defaultdict(list)
    for name in sorted(reg.types):
        record = reg.make(name, "probe_room")
        domain = Path(reg.sources.get(name, "?")).stem
        material = record.get("material")
        if isinstance(material, str) and not props.class_known(material):
            unmapped[domain].append("{} ({})".format(name, material))
        if record.get("portable"):
            if record.get("mass_kg") == DEFAULT_MASS_KG and "mass_kg" not in (reg.types[name].get("props") or {}):
                gaps[domain]["default_mass"].append(name)
            size = record.get("size_m")
            if size == DEFAULT_SIZE:
                gaps[domain]["default_size"].append(name)
            try:
                volume = float(size[0]) * float(size[1]) * float(size[2])
                density = float(record.get("mass_kg", 0.0)) / volume if volume > 0 else 0.0
            except (TypeError, ValueError, IndexError):
                density = 0.0
            if density and not DENSITY_RANGE[0] <= density <= DENSITY_RANGE[1] and props.material_class(None, record) != "intangible":
                gaps[domain]["bulk_density"].append("{} ({:.0f} kg/m3)".format(name, density))
        container = float(record.get("capacity_l", 0) or 0) > 0 or record.get("container") is True
        if container:
            phys = props.props_of(record)
            own_opening = record.get("opening_m") is not None or record.get("opening_width_m") is not None or \
                record.get("mouth_frac") is not None
            if not own_opening and (phys.opening_m is not None or phys.mouth_frac < 1.0):
                row_derived["opening"].append(name)
            if not record.get("nest_group") and phys.nest_group:
                row_derived["nest_group"].append("{} ({})".format(name, phys.nest_group))
        part = props._type_part(record)
        if part.row_floats is not None and "floats" not in record:
            row_derived["floats"].append("{} ({})".format(name, part.row_floats))
    return ({k: sorted(v) for k, v in sorted(unmapped.items())},
            {d: {k: sorted(v) for k, v in sorted(kinds.items())} for d, kinds in sorted(gaps.items())},
            {k: sorted(v) for k, v in sorted(row_derived.items())})


def tier_overrides():
    """Domain file-level field_tiers and type perception blocks that make a hidden physics threshold perceivable."""
    hidden = hidden_thresholds()
    out = []
    for path in sorted(glob.glob(str(ROOT / "house_sim" / "data" / "types" / "*.json"))):
        domain = Path(path).stem
        if domain == "core":
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        blocks = [("field_tiers", data.get("field_tiers") or {})]
        blocks += [("type " + name, (spec or {}).get("perception") or {}) for name, spec in sorted(
            (data.get("types") or {}).items()) if isinstance(spec, dict)]
        for where, block in blocks:
            for tier in ("glance", "inspect", "read", "touch", "count"):
                for field in block.get(tier) or []:
                    if field in hidden:
                        out.append({"domain": domain, "where": where, "field": field, "tier": tier})
            measure = block.get("measure") or {}
            for field in (measure if isinstance(measure, dict) else {f: None for f in measure}):
                if field in hidden:
                    out.append({"domain": domain, "where": where, "field": field, "tier": "measure"})
    return out


def check_laws():
    from house_sim.kernel import physics
    from house_sim.kernel.phys import lawbook
    physics.register_laws()
    registered = {law_id for _, law_id, _ in lawbook.laws()}
    ordered = {law_id for law_id, row in lawbook.LAWS.items() if row["order"] is not None}
    return sorted(ordered - registered), {law_id: row["state"] for law_id, row in sorted(lawbook.LAWS.items())}


def collect_specs(pattern=None):
    out = []
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        name = Path(path).name
        if pattern and not fnmatch.fnmatchcase(name, pattern):
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for i, spec in enumerate(data.get("specs", []) if isinstance(data, dict) else data):
            out.append((spec.get("catalog_id") or "{}#{}".format(name, i), name, spec))
    return out


def _record_event(original):
    def emit(w, kind, target, **data):
        _EVENTS.append({"kind": kind, "target": target, "by": data.get("by"), "cause": data.get("cause"),
                        "outcome": data.get("outcome"), "time_s": w.s.get("time_s"), "step": w.s.get("steps")})
        return original(w, kind, target, **data)
    return emit


def _reference_job(args):
    key, spec, seeds = args
    _quiet()
    from house_sim.kernel.phys import observe
    if not getattr(observe.emit_consequence, "_audited", False):
        wrapped = _record_event(observe.emit_consequence)
        wrapped._audited = True
        observe.emit_consequence = wrapped
    from house_sim.tasks.runner import build, make_env, run_plan
    rows = []
    for seed in seeds:
        row = {"catalog_id": key, "seed": seed}
        del _EVENTS[:]
        try:
            config, task, oracle = build(spec, seed)
            row["lint"] = list(oracle["bindings"].get("_physics_warnings") or [])
            env = make_env(config, task, seed)
            runner = run_plan(env, oracle["reference"], oracle["bindings"], strict=False)
            row["refusals"] = len(runner.failures)
            row["passes"] = bool(env.evaluate()["success"])
        except Exception as exc:  # noqa: BLE001  (recorded: the audit reports runs that could not execute)
            row["error"] = "{}: {}".format(type(exc).__name__, exc)[:300]
            row["trace"] = traceback.format_exc().splitlines()[-4:]
        row["events"] = dict(sorted(Counter(e["kind"] for e in _EVENTS).items()))
        row["consequences"] = list(_EVENTS[:12])
        rows.append(row)
    return rows


def reference_runs(seeds, workers, pattern=None):
    jobs = [(key, spec, tuple(seeds)) for key, _, spec in collect_specs(pattern)]
    rows = []
    if workers <= 1:
        for job in jobs:
            rows.extend(_reference_job(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(_reference_job, jobs, chunksize=4):
                rows.extend(result)
    return rows


def data_audit(seeds, workers, pattern=None):
    _quiet()
    unmapped, gaps, row_derived = audit_registry()
    missing_laws, states = check_laws()
    rows = reference_runs(seeds, workers, pattern)
    with_physics = [r for r in rows if r.get("events")]
    linted = [r for r in rows if r.get("lint")]
    errors = [r for r in rows if r.get("error")]
    return {"unmapped_materials": unmapped, "unmapped_count": sum(len(v) for v in unmapped.values()),
            "unregistered_laws": missing_laws, "law_states": states, "gaps": gaps,
            "gap_counts": {d: {k: len(v) for k, v in kinds.items()} for d, kinds in gaps.items()},
            "tier_overrides": tier_overrides(), "row_derived": row_derived,
            "reference_runs": len(rows), "runs_with_consequences": with_physics, "runs_with_lint": linted,
            "run_errors": errors, "consequence_kinds": dict(sorted(sum((Counter(r["events"]) for r in rows),
                                                                      Counter()).items()))}


# ----------------------------------------------------------------------------------------------------------------
# --bench

def _best(fn, repeats=5, inner=20):
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        elapsed = (time.perf_counter() - start) / inner
        best = elapsed if best is None else min(best, elapsed)
    return best * 1000.0


def _percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _walk(env, rng, steps):
    """A deterministic random walk of ordinary verbs (approach, pick up, place, open, close, move, wait)."""
    state = env.world.s
    for _ in range(steps):
        visible = sorted(e for e in env.observe()["visible_entities"] if e in state["entities"])
        held = env.world.held_items()
        roll = rng.random()
        if held and roll < 0.3:
            targets = [e for e in visible if state["entities"][e].get("accepts_items") and e not in held]
            if targets:
                target = rng.choice(targets)
                env.step("approach", target)
                env.step("place", held[0], target)
                continue
        if roll < 0.55 and visible:
            target = rng.choice(visible)
            env.step("approach", target)
            env.step(rng.choice(["pick_up", "open", "close", "inspect"]), target)
        elif roll < 0.8:
            exits = state["rooms"][state["robot"]["room"]].get("exits") or []
            if exits:
                env.step("move", rng.choice(sorted(exits)))
        else:
            env.step("wait", seconds=rng.choice([30, 60, 300]))


def bench():
    _quiet()
    from house_sim.generator import generate_house
    from house_sim.kernel import engine
    from house_sim.kernel.phys import environment, lawbook, processes, props, settle, thermal
    out = {}
    start = time.perf_counter()
    for name in ("materials.json", "liquids.json", "floors.json", "supports.json"):
        props._load(name)
    lawbook._load()
    out["data_load_ms"] = (time.perf_counter() - start) * 1000.0
    env = engine.HouseEnv(generate_house(seed=0), seed=0, trace=False, observe_on_step=False)
    w = env.world
    ids = sorted(w.s["entities"])
    for eid in ids:
        props.props(w.s, eid)
    out["props_us"] = _best(lambda: [props.props(w.s, e) for e in ids], repeats=5, inner=3) * 1000.0 / len(ids)
    w.begin()
    out["settle_idle_ms"] = _best(lambda: settle.settle(env, "substep"))
    out["processes_idle_ms"] = _best(lambda: processes.run(w, 30.0, 0))
    for _ in range(60):
        thermal.thermal_step(w, 120.0, 0, setter=w.set_quiet)
    out["thermal_substep_ms"] = _best(lambda: thermal.thermal_step(w, 120.0, 0, setter=w.set_quiet), 5, 5) / 4.0
    w.rollback()
    # windy: every generated outdoor room on seeds 0-3 in a gale, the wind law forced on
    windy = []
    for seed in range(4):
        e = engine.HouseEnv(generate_house(seed=seed), seed=seed, trace=False, observe_on_step=False)
        e.world.s["weather"] = dict(e.world.s.get("weather") or {}, wind_mps=12.0, gust_mps=18.0)
        e.world.begin()
        with lawbook.forced(wind=True, rain=True):
            windy.append(_best(lambda: environment.run(e.world, 30.0, 0), 3, 3))
        e.world.rollback()
    out["environment_windy_ms"] = max(windy)
    # settle per action over random walks (the engine's own calls, timed)
    timings = {"action": [], "substep": []}
    original = settle.settle

    def timed(env_, phase, ctx=None, final=True):
        t = time.perf_counter()
        try:
            return original(env_, phase, ctx, final)
        finally:
            timings["action" if phase == "action" else "substep"].append((time.perf_counter() - t) * 1000.0)
    settle.settle = timed
    try:
        for seed in range(4):
            e = engine.HouseEnv(generate_house(seed=seed), seed=seed, trace=False, observe_on_step=True)
            _walk(e, random.Random("physics-audit:{}".format(seed)), 60)
    finally:
        settle.settle = original
    out["settle_action_p50_ms"] = _percentile(timings["action"], 0.5)
    out["settle_action_p99_ms"] = _percentile(timings["action"], 0.99)
    out["settle_substep_p50_ms"] = _percentile(timings["substep"], 0.5)
    out["settle_substep_p99_ms"] = _percentile(timings["substep"], 0.99)
    out["settle_calls"] = {k: len(v) for k, v in timings.items()}
    out["settle_worst_ms"], out["settle_worst_consequences"] = _worst_case()
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
    over = sorted(k for k, budget in BUDGETS.items() if out.get(k, 0.0) > FAIL_FACTOR * budget)
    out["above_budget"] = sorted(k for k, budget in BUDGETS.items() if budget < out.get(k, 0.0) <= FAIL_FACTOR * budget)
    return out, over


def _worst_case():
    """The worst-case settle pass: 70 glasses on a 1 kg-rated shelf that the overload law collapses. The collapse
    cascades shelf by shelf inside one consequence (each shelf gives way under the glasses from the one above), so the
    pass records far more than the 64 chained consequences of the design budget (about 350: 280 falls, 70 breaks, 4
    collapses): (ms, consequence records), the time of the settle pass only (best of 5)."""
    from house_sim.kernel.engine import HouseEnv
    from house_sim.kernel.phys import lawbook, settle
    from house_sim.kernel.registry import default_registry
    reg = default_registry()
    config = {"schema_version": 2, "start_room": "kitchen",
              "rooms": {"kitchen": {"label": "kitchen", "size_m": [5.0, 4.0], "floor_material": "tile", "exits": []}},
              "entities": {"shelf": reg.make("shelf_unit", "kitchen", pos=[3, 0], phys_exact=True, shelf_load_kg=1.0)},
              "robot": {"room": "kitchen", "pos": [1, 1]}, "people": {}, "pets": {}}
    env = HouseEnv(config, seed=0, trace=False, observe_on_step=False)
    w = env.world
    glass = dict(material="glass", mass_kg=0.05, size_m=[0.03, 0.03, 0.05], phys_exact=True, relation="in", shelf=3)

    times, counts = [], []

    def cascade():
        w.begin()
        with lawbook.forced(overload=True):
            settle.begin_action(w)
            for i in range(70):
                w.create("g{:02d}".format(i), reg.make("generic_item", "shelf", **glass))
            start = time.perf_counter()
            records = settle.settle(env, "action")    # timed: the settle pass only, not the setup or the rollback
            times.append((time.perf_counter() - start) * 1000.0)
            counts.append(len(records or ()))
        w.rollback()
    for _ in range(5):
        cascade()
    return min(times), max(counts)


# ----------------------------------------------------------------------------------------------------------------

def summary(record):
    lines = []
    data = record.get("data")
    if data:
        lines.append("materials: {} unmapped".format(data["unmapped_count"]))
        lines.append("laws: {} with a settle order unregistered{}".format(
            len(data["unregistered_laws"]), " ({})".format(", ".join(data["unregistered_laws"]))
            if data["unregistered_laws"] else ""))
        for domain, kinds in sorted(data["gap_counts"].items()):
            lines.append("gaps {}: {}".format(domain, ", ".join("{} {}".format(k, v) for k, v in sorted(kinds.items()))))
        lines.append("threshold tier overrides by domains: {}".format(len(data["tier_overrides"])))
        for row in data["tier_overrides"][:20]:
            lines.append("  {domain} {where}: {field} -> {tier}".format(**row))
        lines.append("row-derived: " + ", ".join("{} {}".format(k, len(v)) for k, v in sorted(data["row_derived"].items())))
        lines.append("reference runs: {}, with physics consequences: {}, with lint warnings: {}, errors: {}".format(
            data["reference_runs"], len(data["runs_with_consequences"]), len(data["runs_with_lint"]),
            len(data["run_errors"])))
        for row in data["runs_with_consequences"][:40]:
            lines.append("  {} seed {}: {}".format(row["catalog_id"], row["seed"], row["events"]))
        lines.append("consequence kinds: {}".format(data["consequence_kinds"]))
        for row in data["run_errors"][:10]:
            lines.append("  ERROR {} seed {}: {}".format(row["catalog_id"], row["seed"], row["error"]))
    bench_out = record.get("bench")
    if bench_out:
        for key in sorted(bench_out):
            if key in BUDGETS:
                lines.append("{:24s} {:10.4f}  (budget {}, fail above {})".format(
                    key, bench_out[key], BUDGETS[key], round(FAIL_FACTOR * BUDGETS[key], 4)))
            elif key not in ("settle_calls", "above_budget"):
                lines.append("{:24s} {:10.4f}".format(key, bench_out[key]) if isinstance(bench_out[key], float)
                             else "{:24s} {:>10}".format(key, bench_out[key]))
        lines.append("above budget (warning): {}".format(", ".join(bench_out.get("above_budget") or []) or "none"))
        lines.append("over the fail line ({}x budget): {}".format(
            FAIL_FACTOR, ", ".join(record.get("over_budget") or []) or "none"))
    lines.append("physics audit: {}".format("FAIL" if record["failed"] else "ok"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Physics audit (data layer, reference runs, cost budgets)")
    parser.add_argument("--data", action="store_true")
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--spec-files", help="only spec files matching this glob")
    args = parser.parse_args(argv)
    do_data, do_bench = args.data or not args.bench, args.bench or not args.data
    record = {"format": FORMAT, "seeds": args.seeds, "spec_files": args.spec_files}
    failed = False
    if do_data:
        data = data_audit(args.seeds, args.workers, args.spec_files)
        record["data"] = data
        failed = failed or bool(data["unmapped_count"] or data["unregistered_laws"] or data["run_errors"])
    if do_bench:
        bench_out, over = bench()
        record["bench"], record["over_budget"] = bench_out, over
        failed = failed or bool(over)
    record["failed"] = failed
    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "house_ai_tier1" / "physics_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    print(summary(record))
    print("record: {}".format(out))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
