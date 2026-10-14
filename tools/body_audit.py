"""Robot-body audit of the authored reference plans (TIER1_EMBODIMENT 8.1 M0, TIER1_INTEGRATION 2.3 BODY-MODEL).

usage: python tools/body_audit.py --baseline [--out FILE] [--seeds 0 1 2 41 97] [--workers N] [--spec-files GLOB]
       python tools/body_audit.py [--against BASELINE] [--out FILE] [...]          (check mode)

Runs every authored spec's reference plan (tasks/authored/*.json, each in its own perception mode, on the
verification seeds) and records, per spec and seed, what the robot body model will act on:

  min_battery      the lowest battery gauge (%) after any step, and the final one
  picks            every executed pick_up, receive and uninstall (nested ones included): the item, its subtree mass
                   (core's current rule), its height, its largest and smallest dimension, the grip and hand asked
                   for, the hands today's rule uses, and the body model's derated capacity for those hands
  careful          physical steps walked or done at careful speed
  undocked waits   seconds spent in wait while not docked (total and longest)
  battery paths    specs whose predicates read the robot battery

and lists the hits the body packages must clear before they land (BODY-CORE, TIER1_INTEGRATION 2.4):

  over_capacity     a reference pick heavier than the derated capacity of the hands it used
  power_grip_small  an explicit power grip (normal, firm, handle, two_hand, underneath) on an item under the gripper's
                    power size (15 mm for a hand)
  tiny              a pick of an item under the gripper's pinch size (2 mm)
  auto_pinch        (informational) items under the power size picked without a grip: the planner will pinch them

--baseline records the facts (exit 1 only if a reference run failed to run). Check mode records them again, lists
the differences from --against and exits 1 when any over_capacity, power_grip_small or tiny hit remains or a run
failed. The JSON goes to --out (default: <temp>/house_ai_tier1/body_audit/<baseline|check>.json for a full run; a run
narrowed by --spec-files or --seeds defaults to <baseline|check>_partial.json, so a quick check never replaces a full
baseline). The baseline the body packages compare against is the B0 gate's record,
catalog/analysis/tier1/gate/B0/body_audit.json (the gate's G9 writes it there): BODY-CORE runs
``--against catalog/analysis/tier1/gate/B0/body_audit.json``.
"""
import argparse
import fnmatch
import json
import os
import sys
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEEDS = (0, 1, 2, 41, 97)
PICK_ACTIONS = ("pick_up", "receive", "uninstall")
LANDING_HITS = ("over_capacity", "power_grip_small", "tiny")
FORMAT = "house-body-audit-v1"
_TRACE = {"picks": None}


# ----------------------------------------------------------------------
# tracing inside a worker

def _item_of(ctx, action):
    """The entity an executed pick puts in the hand(s)."""
    state = ctx.w.s
    if action != "uninstall":
        return ctx.args.get("target")
    device = state["entities"].get(ctx.args.get("target") or "") or {}
    slots = device.get("slots") or {}
    slot = ctx.args.get("slot") or next((s for s in sorted(slots) if slots[s].get("item")), None)
    return (slots.get(slot) or {}).get("item")


def _pick_facts(ctx, action):
    from house_sim.kernel import body, geometry
    from house_sim.systems.core import subtree_mass
    state = ctx.w.s
    item = _item_of(ctx, action)
    record = state["entities"].get(item or "")
    if record is None:
        return None
    mass = subtree_mass(ctx.w, item)
    size = [float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:3]]
    grip, asked = ctx.args.get("grip"), ctx.args.get("hand")
    two = action == "pick_up" and (asked == "both" or grip == "two_hand" or bool(record.get("two_handed")) or
                                   mass > float(body.get(state, "max_hand_kg")) or max(size[:2]) > 0.7)
    hand = "both" if two else (ctx.free_hand(asked) or asked or "right")
    gripper = body.gripper(state, "right" if hand == "both" else hand)
    largest, smallest = max(size) * 1000.0, min(size) * 1000.0
    cap = body.capacity_kg(state, hand, item)
    return {"action": action, "item": item, "type": record.get("type"), "mass_kg": round(mass, 4),
            "height_m": round(geometry.height_m(state, item), 3), "largest_mm": round(largest, 2),
            "smallest_mm": round(smallest, 3), "grip": grip, "hand_asked": asked, "hands": hand,
            "cap_kg": round(cap, 4), "over_capacity": mass > cap + 1e-9,
            "power_grip_small": grip in body.POWER_GRIPS and largest < gripper["min_power_mm"],
            "tiny": largest < gripper["min_pinch_mm"],
            "auto_pinch": grip is None and gripper["min_pinch_mm"] <= largest < gripper["min_power_mm"]}


def _wrap(spec):
    original = spec.fn

    def traced(ctx):
        picks = _TRACE["picks"]
        facts = _pick_facts(ctx, spec.name) if picks is not None else None
        result = original(ctx)
        if facts is not None:
            picks.append(facts)
        return result
    spec.fn = traced


def _worker_init():
    os.environ.setdefault("HOUSE_SIM_WORKER", "1")
    from house_sim.kernel.plugins import ACTIONS, load_domains
    load_domains()
    for name in PICK_ACTIONS:
        if name in ACTIONS:
            _wrap(ACTIONS[name])


def _trace_steps(env, trial):
    """Wrap env.step: battery after each step, careful physical steps, undocked waits; picks keep their step's
    outcome (an after-hook can still refuse a step whose handler succeeded)."""
    from house_sim.kernel import body
    from house_sim.kernel.plugins import ACTIONS
    original = env.step

    def step(action, target="", destination="", **kwargs):
        state = env.world.s
        robot = state["robot"]
        name = action.strip().lower() if isinstance(action, str) else ""
        spec = ACTIONS.get(name)
        if name == "wait" and not robot.get("docked"):
            seconds = kwargs.get("seconds", 1)
            seconds = float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 0.0
            trial["waits_undocked_s"] = round(trial["waits_undocked_s"] + seconds, 3)
            trial["longest_wait_undocked_s"] = max(trial["longest_wait_undocked_s"], seconds)
        careful = spec is not None and spec.category == "physical" and \
            (kwargs.get("speed") or robot.get("speed_mode")) == "careful"
        mark = len(trial["picks"])
        result = original(action, target, destination, **kwargs)
        for pick in trial["picks"][mark:]:
            pick["step_ok"] = bool(result["ok"])
        if careful and result["ok"]:
            trial["careful_steps"].append(name)
        trial["min_battery"] = round(min(trial["min_battery"], body.gauge(env.world.s)), 4)
        return result
    env.step = step


def _run_spec(job):
    key, file_name, spec, seeds = job
    from house_sim.kernel import body
    from house_sim.tasks.runner import build, make_env, run_plan
    rows = []
    for seed in seeds:
        trial = {"catalog_id": key, "file": file_name, "seed": seed, "picks": [], "careful_steps": [],
                 "waits_undocked_s": 0.0, "longest_wait_undocked_s": 0.0, "min_battery": 100.0}
        try:
            config, task, oracle = build(spec, seed)
            trial["perception"] = task.get("perception") or "oracle"
            env = make_env(config, task, seed, perception=trial["perception"])
            trial["min_battery"] = round(body.gauge(env.world.s), 4)
            _trace_steps(env, trial)
            _TRACE["picks"] = trial["picks"]
            try:
                run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
            finally:
                _TRACE["picks"] = None
            state = env.world.s
            trial.update(final_battery=round(body.gauge(state), 4), steps=state.get("steps", 0),
                         time_s=state.get("time_s", 0.0))
        except Exception as exc:  # recorded and reported: an audit that cannot run a reference plan fails
            trial["error"] = "{}: {}".format(type(exc).__name__, exc)
            trial["trace"] = traceback.format_exc().splitlines()[-4:]
        rows.append(trial)
    return rows


# ----------------------------------------------------------------------
# collection, summary, comparison

def collect_specs(pattern=None):
    """[(key, file, spec)] for tasks/authored/*.json in file order (keys as the tier-1 gate makes them)."""
    out, seen = [], {}
    for path in sorted((ROOT / "tasks" / "authored").glob("*.json")):
        if pattern and not fnmatch.fnmatch(path.name, pattern):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for i, spec in enumerate(data.get("specs", []) if isinstance(data, dict) else data):
            key = spec.get("catalog_id") or "{}#{}".format(path.name, i)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = "{}#{}".format(key, seen[key])
            out.append((key, path.name, spec))
    return out


def _hit(trial, pick):
    return {"catalog_id": trial["catalog_id"], "seed": trial["seed"], "item": pick["item"], "type": pick["type"],
            "mass_kg": pick["mass_kg"], "cap_kg": pick["cap_kg"], "hands": pick["hands"], "grip": pick["grip"],
            "largest_mm": pick["largest_mm"]}


def summarize(trials, specs):
    hits = {name: [] for name in LANDING_HITS + ("auto_pinch",)}
    picks = 0
    for trial in trials:
        for pick in trial.get("picks", []):
            if not pick.get("step_ok"):
                continue
            picks += 1
            for name in hits:
                if pick[name]:
                    hits[name].append(_hit(trial, pick))
    ran = [t for t in trials if "error" not in t]
    lowest = min(ran, key=lambda t: (t["min_battery"], t["catalog_id"], t["seed"]), default=None)
    longest = max(ran, key=lambda t: (t["waits_undocked_s"], t["catalog_id"]), default=None)
    waits = [{"catalog_id": t["catalog_id"], "seed": t["seed"], "undocked_wait_s": t["waits_undocked_s"]}
             for t in ran if t["waits_undocked_s"] >= 3600.0]
    waits.sort(key=lambda w: (-w["undocked_wait_s"], w["catalog_id"], w["seed"]))
    battery_paths = sorted(key for key, _, spec in specs
                           if '"robot.battery' in json.dumps(spec) or '"robot.power_state' in json.dumps(spec))
    summary = {"specs": len(specs), "trials": len(trials), "errors": len(trials) - len(ran), "picks": picks,
               "counts": {name: len(items) for name, items in hits.items()},
               "min_battery": lowest and {"pct": lowest["min_battery"], "catalog_id": lowest["catalog_id"],
                                          "seed": lowest["seed"]},
               "most_undocked_wait_s": longest and {"s": longest["waits_undocked_s"],
                                                    "catalog_id": longest["catalog_id"], "seed": longest["seed"]},
               "careful_files": sorted({t["file"] for t in ran if t["careful_steps"]})}
    hits.update(undocked_waits_over_1h=waits, battery_paths=battery_paths,
                errors=[{"catalog_id": t["catalog_id"], "seed": t["seed"], "error": t["error"]}
                        for t in trials if "error" in t])
    return summary, hits


def compare(baseline, trials):
    """Differences from a baseline record: new landing hits and trials whose lowest battery moved."""
    def keyed(rows):
        return {(t["catalog_id"], t["seed"]): t for t in rows}
    before, after = keyed(baseline.get("trials", [])), keyed(trials)
    old_hits = {name: {(h["catalog_id"], h["seed"], h["item"]) for h in baseline.get("hits", {}).get(name, [])}
                for name in LANDING_HITS}
    _, hits = summarize(trials, [])
    new = {name: [h for h in hits[name] if (h["catalog_id"], h["seed"], h["item"]) not in old_hits[name]]
           for name in LANDING_HITS}
    moved = []
    for key in sorted(set(before) & set(after)):
        a, b = before[key].get("min_battery"), after[key].get("min_battery")
        if a is not None and b is not None and abs(a - b) >= 0.5:
            moved.append({"catalog_id": key[0], "seed": key[1], "min_battery": [a, b]})
    return {"new_hits": new, "min_battery_moved": moved, "missing": sorted(set(before) - set(after)),
            "added": sorted(set(after) - set(before))}


def default_out(mode, partial):
    """Default output file: <temp>/house_ai_tier1/body_audit/<mode>.json for a full run, <mode>_partial.json for a
    run narrowed by --spec-files or --seeds (a filtered run never overwrites the full one)."""
    name = mode + ("_partial" if partial else "") + ".json"
    return Path(tempfile.gettempdir()) / "house_ai_tier1" / "body_audit" / name


def main(argv=None):
    parser = argparse.ArgumentParser(description="Robot-body audit of the authored reference plans")
    parser.add_argument("--baseline", action="store_true", help="record the baseline (exit 0 unless a run failed)")
    parser.add_argument("--against", help="check mode: a baseline record to compare with")
    parser.add_argument("--out", help="JSON output file")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--spec-files", help="only spec files matching this glob, e.g. 'cleaning__*'")
    args = parser.parse_args(argv)
    mode = "baseline" if args.baseline else "check"
    partial = bool(args.spec_files) or sorted(args.seeds) != sorted(SEEDS)
    specs = collect_specs(args.spec_files)
    jobs = [(key, file_name, spec, tuple(args.seeds)) for key, file_name, spec in specs]
    if args.workers <= 1:
        _worker_init()
        results = [_run_spec(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_worker_init) as pool:
            results = list(pool.map(_run_spec, jobs, chunksize=2))
    trials = [row for rows in results for row in rows]
    summary, hits = summarize(trials, specs)
    from house_sim.kernel.engine import actions_fingerprint
    record = {"format": FORMAT, "mode": mode, "seeds": list(args.seeds), "spec_files": args.spec_files,
              "partial": partial, "actions_fingerprint": actions_fingerprint()[:12], "summary": summary, "hits": hits,
              "trials": trials}
    if args.against:
        record["compare"] = compare(json.loads(Path(args.against).read_text(encoding="utf-8")), trials)
    out = Path(args.out) if args.out else default_out(mode, partial)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    counts = summary["counts"]
    print("body audit ({}): {} specs x {} seeds = {} trials, {} errors".format(
        mode, summary["specs"], len(args.seeds), summary["trials"], summary["errors"]))
    print("  picks {}: over derated capacity {}, explicit power grip under the power size {}, under the pinch size {}, "
          "auto pinch {}".format(summary["picks"], counts["over_capacity"], counts["power_grip_small"], counts["tiny"],
                                 counts["auto_pinch"]))
    if summary["min_battery"]:
        print("  lowest battery {pct} % ({catalog_id} seed {seed})".format(**summary["min_battery"]))
    if summary["most_undocked_wait_s"]:
        print("  most time waiting undocked {:.1f} h ({catalog_id} seed {seed})".format(
            summary["most_undocked_wait_s"]["s"] / 3600.0, **summary["most_undocked_wait_s"]))
    print("  careful speed in {} spec files: {}".format(len(summary["careful_files"]),
                                                       ", ".join(summary["careful_files"])))
    print("  battery predicates: {}".format(", ".join(hits["battery_paths"]) or "none"))
    for name in LANDING_HITS:
        for hit in hits[name][:20]:
            print("  {}: {catalog_id} seed {seed} {item} ({type}) {mass_kg} kg, cap {cap_kg} kg, hands {hands}, "
                  "grip {grip}, {largest_mm} mm".format(name, **hit))
    for error in hits["errors"][:20]:
        print("  ERROR {catalog_id} seed {seed}: {error}".format(**error))
    if "compare" in record:
        diff = record["compare"]
        print("  vs {}: new hits {}, lowest battery moved in {} trials".format(
            args.against, {k: len(v) for k, v in diff["new_hits"].items()}, len(diff["min_battery_moved"])))
    print("  written to {}".format(out))
    if summary["errors"]:
        return 1
    if mode == "check" and any(counts[name] for name in LANDING_HITS):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
