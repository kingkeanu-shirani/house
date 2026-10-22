"""Robot-body audit of the authored reference plans (TIER1_EMBODIMENT 8.1 M0, TIER1_INTEGRATION 2.3 BODY-MODEL).

usage: python tools/body_audit.py --baseline [--out FILE] [--seeds 0 1 2 41 97] [--workers N] [--spec-files GLOB]
       python tools/body_audit.py [--against BASELINE] [--out FILE] [...]          (check mode)

Runs every authored spec's reference plan (tasks/authored/*.json, each in its own perception mode, on the
verification seeds) and records, per spec and seed, what the robot body model will act on:

  min_battery      the lowest battery gauge (%) after any step, and the final one
  picks            every executed pick_up, receive and uninstall (nested ones included): the item, its subtree mass,
                   its height, its largest and smallest dimension, the grip and hand asked for, the hands physics'
                   lift plan uses (forces.lift_plan, with its ok / reason) and the derated capacity of those hands
                   (body.capacity_kg as check_lift enforces it, and believed=True as the planner sees it)
  careful          physical steps walked or done at careful speed
  undocked waits   seconds spent in wait while not docked (total and longest)
  battery paths    specs whose predicates read the robot battery
  body             what the body did during the run (STAGE-B-FIX B9): overload refusals (stats.overload_attempts),
                   dropped items (robot_drops), energy refusals, power-downs and the energy drawn (energy_wh_used);
                   per trial, per task ("per_task": mean and largest energy over the seeds, event counts) and in the
                   summary

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
    """What the body model acts on for one executed pick, read from the kernel's own rules (STAGE-B-FIX B9): the hands
    from physics' lift plan (forces.lift_plan: two hands for a heavy, wide or two-handed item, else the free hand the
    robot believes can take it), the item's subtree mass from that plan (support.subtree_mass) and the derated
    capacity of those hands from body.capacity_kg -- as enforced (check_lift: a latent weak arm at its true
    strength) and as the robot believes it (believed=True: the strength the planner chose the hands by)."""
    from house_sim.kernel import body, geometry
    from house_sim.kernel.phys import forces
    state = ctx.w.s
    item = _item_of(ctx, action)
    record = state["entities"].get(item or "")
    if record is None:
        return None
    size = [float(v) for v in (record.get("size_m") or [0.1, 0.1, 0.1])[:3]]
    grip, asked = ctx.args.get("grip"), ctx.args.get("hand")
    wanted = "both" if action == "pick_up" and (asked == "both" or grip == "two_hand") else \
        (asked if asked in body.HANDS else None)
    plan = forces.lift_plan(ctx.w, item, hands=wanted)
    mass = float(plan["mass_kg"])
    used = list(plan["hands"])
    hand = "both" if len(used) == 2 else (used[0] if used else (asked if asked in body.HANDS else "right"))
    gripper = body.gripper(state, "right" if hand == "both" else hand)
    largest, smallest = max(size) * 1000.0, min(size) * 1000.0
    cap = body.capacity_kg(state, hand, item)
    believed = body.capacity_kg(state, hand, item, believed=True)
    return {"action": action, "item": item, "type": record.get("type"), "mass_kg": round(mass, 4),
            "height_m": round(geometry.height_m(state, item), 3), "largest_mm": round(largest, 2),
            "smallest_mm": round(smallest, 3), "grip": grip, "hand_asked": asked, "hands": hand,
            "plan_ok": bool(plan["ok"]), "plan_reason": plan["reason"],
            "cap_kg": round(cap, 4), "cap_believed_kg": round(believed, 4), "over_capacity": mass > cap + 1e-9,
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


BODY_STATS = ("overload_attempts", "robot_drops", "energy_refusals", "power_downs")


def _body_counts(state):
    """The body bookkeeping of a state: its stats (overload refusals, drops, energy refusals, power-downs) and the
    energy drawn (robot.energy_wh_used, body.settle_energy)."""
    from house_sim.kernel import body
    stats = state.get("stats") or {}
    out = {name: int(stats.get(name, 0) or 0) for name in BODY_STATS}
    out["energy_wh"] = float(body.get(state, "energy_wh_used") or 0.0)
    return out


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
            start = _body_counts(env.world.s)
            _trace_steps(env, trial)
            _TRACE["picks"] = trial["picks"]
            try:
                run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
            finally:
                _TRACE["picks"] = None
            state = env.world.s
            end = _body_counts(state)
            trial.update(final_battery=round(body.gauge(state), 4), steps=state.get("steps", 0),
                         time_s=state.get("time_s", 0.0),
                         # what the body did during the reference run (STAGE-B-FIX B9): lifts refused as overloads,
                         # items dropped, energy drawn and energy refusals, power-downs
                         overload_refusals=end["overload_attempts"] - start["overload_attempts"],
                         robot_drops=end["robot_drops"] - start["robot_drops"],
                         energy_refusals=end["energy_refusals"] - start["energy_refusals"],
                         power_downs=end["power_downs"] - start["power_downs"],
                         energy_wh=round(end["energy_wh"] - start["energy_wh"], 4))
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
    per_task = per_task_body(ran)
    summary = {"specs": len(specs), "trials": len(trials), "errors": len(trials) - len(ran), "picks": picks,
               "counts": {name: len(items) for name, items in hits.items()},
               "min_battery": lowest and {"pct": lowest["min_battery"], "catalog_id": lowest["catalog_id"],
                                          "seed": lowest["seed"]},
               "most_undocked_wait_s": longest and {"s": longest["waits_undocked_s"],
                                                    "catalog_id": longest["catalog_id"], "seed": longest["seed"]},
               "careful_files": sorted({t["file"] for t in ran if t["careful_steps"]}),
               "body": {"overload_refusals": sum(t.get("overload_refusals", 0) for t in ran),
                        "robot_drops": sum(t.get("robot_drops", 0) for t in ran),
                        "energy_refusals": sum(t.get("energy_refusals", 0) for t in ran),
                        "power_downs": sum(t.get("power_downs", 0) for t in ran),
                        "energy_wh_total": round(sum(t.get("energy_wh", 0.0) for t in ran), 3),
                        "most_energy": max(({"catalog_id": cid, "energy_wh_max": row["energy_wh_max"]}
                                            for cid, row in per_task.items()),
                                           key=lambda r: (r["energy_wh_max"], r["catalog_id"]), default=None)}}
    events = []
    for t in ran:
        for name in ("overload_refusals", "robot_drops", "energy_refusals", "power_downs"):
            if t.get(name):
                events.append({"catalog_id": t["catalog_id"], "seed": t["seed"], "kind": name, "count": t[name]})
    hits.update(undocked_waits_over_1h=waits, battery_paths=battery_paths, body_events=events,
                errors=[{"catalog_id": t["catalog_id"], "seed": t["seed"], "error": t["error"]}
                        for t in trials if "error" in t])
    return summary, hits


def per_task_body(trials):
    """{catalog_id: {"seeds", "energy_wh_mean", "energy_wh_max", "overload_refusals", "robot_drops",
    "energy_refusals", "power_downs"}} over the trials that ran (the per-task body report of STAGE-B-FIX B9)."""
    out = {}
    for t in trials:
        row = out.setdefault(t["catalog_id"], {"seeds": 0, "energy_wh": [], "overload_refusals": 0, "robot_drops": 0,
                                               "energy_refusals": 0, "power_downs": 0})
        row["seeds"] += 1
        row["energy_wh"].append(float(t.get("energy_wh", 0.0)))
        for name in ("overload_refusals", "robot_drops", "energy_refusals", "power_downs"):
            row[name] += int(t.get(name, 0) or 0)
    for row in out.values():
        values = row.pop("energy_wh")
        row["energy_wh_mean"] = round(sum(values) / len(values), 4) if values else 0.0
        row["energy_wh_max"] = round(max(values), 4) if values else 0.0
    return dict(sorted(out.items()))


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
    moved, body_moved = [], []
    for key in sorted(set(before) & set(after)):
        a, b = before[key].get("min_battery"), after[key].get("min_battery")
        if a is not None and b is not None and abs(a - b) >= 0.5:
            moved.append({"catalog_id": key[0], "seed": key[1], "min_battery": [a, b]})
        # body events that appeared or went away (a baseline from before B9 has none recorded: not compared)
        for name in ("overload_refusals", "robot_drops", "energy_refusals", "power_downs"):
            x, y = before[key].get(name), after[key].get(name)
            if x is not None and y is not None and x != y:
                body_moved.append({"catalog_id": key[0], "seed": key[1], "kind": name, "count": [x, y]})
    return {"new_hits": new, "min_battery_moved": moved, "body_events_changed": body_moved,
            "missing": sorted(set(before) - set(after)), "added": sorted(set(after) - set(before))}


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
    per_task = per_task_body([t for t in trials if "error" not in t])
    from house_sim.kernel.engine import actions_fingerprint
    record = {"format": FORMAT, "mode": mode, "seeds": list(args.seeds), "spec_files": args.spec_files,
              "partial": partial, "actions_fingerprint": actions_fingerprint()[:12], "summary": summary, "hits": hits,
              "per_task": per_task, "trials": trials}
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
    b = summary["body"]
    print("  body during the reference runs: overload refusals {}, drops {}, energy refusals {}, power-downs {}, "
          "energy {} Wh in all{}".format(
              b["overload_refusals"], b["robot_drops"], b["energy_refusals"], b["power_downs"], b["energy_wh_total"],
              "; most per task {catalog_id} {energy_wh_max} Wh".format(**b["most_energy"]) if b["most_energy"] else ""))
    for event in hits["body_events"][:20]:
        print("  body: {catalog_id} seed {seed}: {kind} {count}".format(**event))
    for name in LANDING_HITS:
        for hit in hits[name][:20]:
            print("  {}: {catalog_id} seed {seed} {item} ({type}) {mass_kg} kg, cap {cap_kg} kg, hands {hands}, "
                  "grip {grip}, {largest_mm} mm".format(name, **hit))
    for error in hits["errors"][:20]:
        print("  ERROR {catalog_id} seed {seed}: {error}".format(**error))
    if "compare" in record:
        diff = record["compare"]
        print("  vs {}: new hits {}, lowest battery moved in {} trials, body events changed in {}".format(
            args.against, {k: len(v) for k, v in diff["new_hits"].items()}, len(diff["min_battery_moved"]),
            len(diff["body_events_changed"])))
    print("  written to {}".format(out))
    if summary["errors"]:
        return 1
    if mode == "check" and any(counts[name] for name in LANDING_HITS):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
