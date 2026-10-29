"""Room-mode golden: state and observation digests of a fixed script and of reference plans
(docs/design/TIER1_INTEGRATION.md 3.5).

usage:
  python tools/golden.py record --by ID --classes CLASS[,CLASS...] [--new] [--workers N] [--file F]
  python tools/golden.py check [--all] [--workers N] [--file F]
  python tools/golden.py dump --out DIR [--all] [--workers N] [--file F]
  python tools/golden.py diff BEFORE AFTER [--limit N] [--examples N]

What tests/data/room_golden.json pins (tests/test_room_golden.py checks it; G8 of the tier-1 gate runs that test):

* runs: seeds 0-3, oracle and realistic perception, camera "room" passed explicitly whenever HouseEnv takes a camera
  argument (from CAM-RUNTIME on), so the default flip of D3 cannot change it. For each run the state and observation
  digests after reset and after every step of the seed's fixed 12-action script (look, approach, pick_up, a careful
  move, approach, place, open, close, inspect, read, wait 600, wait 3600), each step's ok flag and its duration. The
  script is chosen once, when the file is created (`record --new`), from the generated house: the first targets for
  which every step succeeds in oracle mode. The commands are stored in the file and replayed as they are, so later
  packages replay exactly the same commands (true ids, translated to handles in realistic mode like the task runner).
* specs: the final state digest, steps, time and evaluation of the reference plan of every authored spec on seed 0 in
  its own perception mode (lenient runner: a refused step is counted, never raised), with the room camera; a spec
  that declares "camera": "geometric" runs with its own camera instead (recorded as "camera", exempt from the
  room-mode state checks: its reference would only fail with the room camera). The test checks the 20
  `fixed_specs`; with HOUSE_SIM_GOLDEN_ALL=1 it checks all of them.
* invariants the test asserts on every run: room-mode states have no `camera_log` section and no robot `view` or
  `facing_deg`, and `look` lasts 1 s.

`record` re-runs everything and rewrites the digests (a new file also chooses the scripts and the fixed specs) and
appends {"by", "classes", "date"} to `history`. It refuses to pin a reference plan that fails (phase-1 red team: the
golden pinned T-7d4006bda247ba-01's scorch as the expected outcome) unless the main session accepted it: the spec is on
the gate's committed allowlist (tools/orchestration/gate_policy/known_failing.json, kind "spec", covering seed 0 in its
own mode) or the committed golden (git HEAD) already pins it known-failing. A recorder cannot excuse a failure itself
(K-GATE2: FIX2's --known-failing pinned any failing reference its recorder named); --known-failing is an
acknowledgement checked against those two and refused otherwise. Such an entry is stored with "known_failing": true
(the history entry lists them), and once its reference passes, `check` reports it as an expected change (to
re-record), not as a difference. A package records only when every changed path belongs to its
reviewed classes (3.5): `dump` before the first edit, `dump` again before hand-over, then `diff BEFORE AFTER` prints the
leaf differences grouped by path pattern (entity, room, person and handle ids and list indexes become `*`, so a
pattern reads like `entities.*._robot_moved` or `obs.visible_entities.*.state`).

Stdlib only; deterministic (the digests are house_sim.kernel.predicates.digest of states and observations).
"""
import argparse
import copy
import datetime
import glob
import inspect
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN_FILE = ROOT / "tests" / "data" / "room_golden.json"
FORMAT = "house-room-golden-v1"
SEEDS = (0, 1, 2, 3)
MODES = ("oracle", "realistic")
SPEC_SEED = 0
FIXED_SPEC_COUNT = 20
# the perception files whose first spec joins the first spec of every examples file in the fixed set
FIXED_PERCEPTION_FILES = ("manipulation__perception.json", "pets__perception.json",
                          "plumbing_liquids__perception.json", "safety__perception.json")
SCRIPT_WAITS = (600, 3600)
ID_SEGMENT = re.compile(r"^(?:\d+|[a-z][a-z0-9_]*_\d+|obj_[0-9a-f]{8,16}|r\d+|p\d+)$")
KEYED = ("entities", "rooms", "people", "pets", "connections", "visible_entities")


def _quiet_imports():
    import warnings
    warnings.simplefilter("ignore")
    from house_sim.kernel.plugins import load_domains
    load_domains()


# ----- environments -----------------------------------------------------------------------------------------------

def env_kwargs():
    """camera="room" whenever HouseEnv takes a camera argument (CAM-RUNTIME adds it), so the golden never depends on
    the default camera mode."""
    from house_sim.kernel.engine import HouseEnv
    return {"camera": "room"} if "camera" in inspect.signature(HouseEnv.__init__).parameters else {}


def house_env(seed, perception):
    from house_sim.generator import generate_house
    from house_sim.kernel.engine import HouseEnv
    return HouseEnv(generate_house(seed=seed), None, seed, perception=perception, **env_kwargs())


def spec_env(config, task, seed, perception, camera=None):
    """The verifier's make_env (fast: no trace; observations each step only in realistic mode) with the room camera,
    or with camera="geometric" for a spec that declares it (spec_camera)."""
    from house_sim.kernel.engine import HouseEnv
    kwargs = env_kwargs()
    if camera and kwargs:
        kwargs["camera"] = camera
    return HouseEnv(copy.deepcopy(config), copy.deepcopy(task), seed, trace=False,
                    observe_on_step=perception == "realistic", perception=perception, **kwargs)


def spec_camera(task):
    """The camera a spec's reference plan runs with: "room", unless the spec declares "camera": "geometric" (its setup
    and reference are written for the posed cameras, so the room camera would only pin a failing run; food's adoption
    request 5). Such specs are recorded with their camera and are exempt from the room-mode state checks."""
    return "geometric" if (task or {}).get("camera") == "geometric" else "room"


def do(env, command):
    """One step of a stored command (true ids; handles in realistic mode, as the task runner does). Returns the
    step result."""
    cmd = dict(command)
    action = cmd.pop("action")
    target, destination = cmd.pop("target", ""), cmd.pop("destination", "")
    realism = env.realism
    if realism is not None:
        target, destination = realism.to_handles(target), realism.to_handles(destination)
        cmd = realism.to_handles(cmd)
    return env.step(action, target, destination, **cmd)


# ----- the fixed script -------------------------------------------------------------------------------------------

def _visible(env, eid):
    return env.perception.visible(env.world.s, eid)


def _here(env):
    state = env.world.s
    room = state["robot"]["room"]
    return [e for e in sorted(state["entities"]) if env.world.root_room(e) == room and _visible(env, e)]


def _harmless(rec):
    return not (rec.get("hazards") or rec.get("private") or rec.get("off_limits") or rec.get("requires_approval")
                or (rec.get("risk") or 0) >= 2 or rec.get("sentimental"))


def _snapshot(env):
    from house_sim.kernel.world import clone
    return clone(env.world.s)


def _restore(env, saved):
    from house_sim.kernel.world import World, clone
    env.world = World(clone(saved), env.registry)


def _tries(env, commands):
    """Do the commands on a copy of the env's world; True when every one succeeds (the env is restored)."""
    saved = _snapshot(env)
    try:
        return all(env.step(c["action"], c.get("target", ""), c.get("destination", ""),
                            **{k: v for k, v in c.items() if k not in ("action", "target", "destination")})["ok"]
                   for c in commands)
    finally:
        _restore(env, saved)


def _commit(env, commands, script):
    for c in commands:
        result = env.step(c["action"], c.get("target", ""), c.get("destination", ""),
                          **{k: v for k, v in c.items() if k not in ("action", "target", "destination")})
        if not result["ok"]:
            raise RuntimeError("script step refused while choosing: {} -> {}".format(c, result["message"]))
        script.append(c)


def build_script(seed):
    """The seed's 12-command script, chosen in oracle mode from the generated house: the first (sorted) targets for
    which every step succeeds."""
    env = house_env(seed, "oracle")
    state = env.world.s
    entities = state["entities"]
    script = []
    _commit(env, [{"action": "look"}], script)
    item = None
    for eid in _here(env):
        rec = entities[eid]
        if not (rec.get("portable") and rec.get("relation") == "on" and rec.get("location") in entities
                and _harmless(rec) and float(rec.get("mass_kg", 0.4) or 0.0) <= 3.0):
            continue
        steps = [{"action": "approach", "target": eid}, {"action": "pick_up", "target": eid}]
        if _tries(env, steps):
            _commit(env, steps, script)
            item = eid
            break
    if item is None:
        raise RuntimeError("seed {}: no portable item to pick up".format(seed))
    room = state["robot"]["room"]
    for exit_room in sorted(state["rooms"][room].get("exits", [])):
        step = {"action": "move", "target": exit_room, "speed": "careful"}
        if not _tries(env, [step]):
            continue
        saved = _snapshot(env)
        _commit(env, [step], script)
        chosen = None
        surfaces = [e for e in _here(env) if entities_of(env)[e].get("accepts_items") and
                    entities_of(env)[e].get("surface") and not entities_of(env)[e].get("portable")]
        openables = [e for e in _here(env) if entities_of(env)[e].get("openable") and
                     not entities_of(env)[e].get("open") and not entities_of(env)[e].get("locked") and
                     "connects" not in entities_of(env)[e] and _harmless(entities_of(env)[e])]
        for sid in surfaces:
            for oid in openables:
                steps = [{"action": "approach", "target": sid}, {"action": "place", "target": item, "destination": sid},
                         {"action": "open", "target": oid}, {"action": "close", "target": oid}]
                if _tries(env, steps):
                    chosen = steps
                    break
            if chosen:
                break
        if chosen:
            _commit(env, chosen, script)
            break
        _restore(env, saved)
        script.pop()
    else:
        raise RuntimeError("seed {}: no exit with a surface and an openable to use".format(seed))
    sid, oid = script[-4]["target"], script[-2]["target"]
    for tid in (sid, oid, item):
        step = {"action": "inspect", "target": tid}
        if _tries(env, [step]):
            _commit(env, [step], script)
            break
    else:
        raise RuntimeError("seed {}: nothing to inspect".format(seed))
    for rid in [item] + _here(env):
        step = {"action": "read", "target": rid}
        if _tries(env, [step]):
            _commit(env, [step], script)
            break
    else:
        raise RuntimeError("seed {}: nothing readable".format(seed))
    for seconds in SCRIPT_WAITS:
        _commit(env, [{"action": "wait", "seconds": seconds}], script)
    return script


def entities_of(env):
    return env.world.s["entities"]


# ----- runs -------------------------------------------------------------------------------------------------------

def _room_mode_problems(state):
    """Room-mode states carry no camera state (TIER1_INTEGRATION 3.5)."""
    problems = []
    if "camera_log" in state:
        problems.append("state has a camera_log section")
    robot = state.get("robot") or {}
    for key in ("view", "facing_deg"):
        if key in robot:
            problems.append("robot has {}".format(key))
    return problems


def run_script(seed, perception, script, keep=False):
    """Replay a stored script: digests after reset and after every step, each step's ok flag and duration, and the
    room-mode problems seen. keep: also the reset and final states and observations (for dump)."""
    from house_sim.kernel.predicates import digest
    env = house_env(seed, perception)
    obs = env.observe()
    state = env.world.s
    out = {"reset": {"state": digest(state), "obs": digest(obs)}, "steps": [], "problems": _room_mode_problems(state)}
    if keep:
        out["full"] = {"reset_state": copy.deepcopy(state), "reset_obs": copy.deepcopy(obs)}
    for command in script:
        before = float(state["time_s"])
        result = do(env, command)
        state = env.world.s
        seconds = round(float(state["time_s"]) - before, 3)
        out["steps"].append({"action": command["action"], "ok": bool(result["ok"]), "seconds": seconds,
                             "state": digest(state), "obs": digest(result["observation"])})
        if command["action"] == "look" and seconds != 1.0:
            out["problems"].append("look lasted {} s".format(seconds))
        for problem in _room_mode_problems(state):
            if problem not in out["problems"]:
                out["problems"].append(problem)
    if keep:
        out["full"].update(final_state=copy.deepcopy(state), final_obs=copy.deepcopy(result["observation"]))
    return out


def collect_specs():
    """[(key, file, spec)] for every tasks/authored/*.json spec in file order (the tier-1 gate's keys)."""
    out, seen = [], defaultdict(int)
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        specs = data.get("specs", []) if isinstance(data, dict) else data
        for i, spec in enumerate(specs):
            key = spec.get("catalog_id") or "{}#{}".format(Path(path).name, i)
            seen[key] += 1
            if seen[key] > 1:
                key = "{}#{}".format(key, seen[key])
            out.append((key, Path(path).name, spec))
    return out


def choose_fixed_specs(specs):
    """The first spec of every examples file plus the first of FIXED_PERCEPTION_FILES (20 with 16 domains)."""
    firsts = OrderedDict()
    for key, name, _ in specs:
        firsts.setdefault(name, key)
    chosen = [key for name, key in firsts.items() if name.endswith("__examples.json")]
    chosen += [firsts[name] for name in FIXED_PERCEPTION_FILES if name in firsts]
    return chosen[:FIXED_SPEC_COUNT]


def run_spec(spec, seed=SPEC_SEED, keep=False):
    """The reference plan of a spec on one seed in its own perception mode (lenient: refusals are counted), with the
    room camera unless the spec declares the geometric one (spec_camera)."""
    from house_sim.kernel.predicates import digest
    from house_sim.tasks.runner import PlanError, build, run_plan
    config, task, oracle = build(spec, seed)
    mode = task.get("perception") or "oracle"
    camera = spec_camera(task)
    env = spec_env(config, task, seed, mode, camera=camera)
    out = {"seed": seed, "perception": mode}
    if camera != "room":
        out["camera"] = camera
    try:
        runner = run_plan(env, oracle["reference"], oracle["bindings"], strict=False)
        out["refusals"] = len(runner.failures)
    except PlanError as exc:
        out["error"] = str(exc)[:200]
    result = env.evaluate()
    state = env.world.s
    out.update(state=digest(state), steps=state["steps"], time_s=state["time_s"], passes=bool(result["success"]))
    out["problems"] = _room_mode_problems(state) if camera == "room" else []
    if keep:
        out["full"] = copy.deepcopy(state)
    return out


def _spec_job(args):
    key, spec, keep = args
    _quiet_imports()
    try:
        return key, run_spec(spec, keep=keep)
    except Exception as exc:  # noqa: BLE001  (recorded: a crash is a difference like any other)
        return key, {"error": "{}: {}".format(type(exc).__name__, exc)[:300]}


def _run_job(args):
    seed, mode, script, keep = args
    _quiet_imports()
    return (seed, mode), run_script(seed, mode, script, keep=keep)


def _pool_map(fn, jobs, workers):
    if workers <= 1 or len(jobs) <= 1:
        return [fn(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, jobs, chunksize=max(1, len(jobs) // (workers * 4))))


def default_workers():
    return max(1, min(6, (os.cpu_count() or 2) - 1))


def compute(golden, spec_keys=None, workers=1, keep=False):
    """Run the golden's scripts and the reference plans of spec_keys (None: every spec). Returns (runs, specs) with
    runs keyed "seed/mode"."""
    runs = {}
    jobs = [(int(seed), mode, golden["scripts"][str(seed)], keep) for seed in SEEDS for mode in MODES]
    for (seed, mode), result in _pool_map(_run_job, jobs, workers):
        runs["{}/{}".format(seed, mode)] = result
    specs = collect_specs()
    wanted = None if spec_keys is None else set(spec_keys)
    jobs = [(key, spec, keep) for key, _, spec in specs if wanted is None or key in wanted]
    results = dict(_pool_map(_spec_job, jobs, workers))
    return runs, results


# ----- the golden file --------------------------------------------------------------------------------------------

def load(path=GOLDEN_FILE):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(golden, path=GOLDEN_FILE):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(golden, handle, indent=1, sort_keys=True)
        handle.write("\n")


def _strip(result):
    return {k: v for k, v in result.items() if k != "full"}


def allowlisted_failing(keys):
    """The catalog ids among `keys` that the tier-1 gate's committed allowlist excuses on the golden's seed in their
    own mode (tier1_gate.load_known_failing, read from the project's commits)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tier1_gate_for_golden", str(ROOT / "tools" / "orchestration" /
                                                                               "tier1_gate.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    allow = gate.load_known_failing(gate.policy_root())
    return {k for k in keys if gate.spec_allowed(allow, str(k).split("#")[0], SPEC_SEED, "own")}


def committed_known_failing(path=GOLDEN_FILE):
    """The spec keys the committed golden file (git HEAD of the project) pins with "known_failing": failing
    references the main session accepted by committing them (K-GATE2). Empty when the file is outside the project,
    not committed or git is unavailable."""
    import subprocess
    try:
        rel = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return set()
    try:
        show = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:" + rel], capture_output=True)
    except OSError:
        return set()
    if show.returncode != 0:
        return set()
    try:
        data = json.loads(show.stdout.decode("utf-8"))
    except ValueError:
        return set()
    return {k for k, v in (data.get("specs") or {}).items() if isinstance(v, dict) and v.get("known_failing")}


def failing_references(specs, allowlisted=None, committed=()):
    """(refused, known) keys of specs whose reference fails: refused unless the main session accepted the failure --
    allowlisted (the gate's committed allowlist) or pinned known-failing in the committed golden (`committed`)."""
    failing = sorted(k for k, v in specs.items() if not v.get("passes"))
    excused = set(committed) | set(allowlisted(failing) if allowlisted is not None and failing else ())
    return [k for k in failing if k not in excused and k.split("#")[0] not in excused], \
        [k for k in failing if k in excused or k.split("#")[0] in excused]


def record(by, classes, new=False, workers=1, path=GOLDEN_FILE, known_failing=()):
    """Re-run everything and rewrite the digests; a new file (or --new) also chooses the scripts and fixed specs. A
    failing reference is refused (SystemExit) unless the gate's committed allowlist excuses it or the committed golden
    already pins it known-failing; known_failing (--known-failing) names are checked against the same two and refused
    otherwise. Accepted failing references are stored with "known_failing": true."""
    _quiet_imports()
    if new or not Path(path).exists():
        specs = collect_specs()
        golden = {"format": FORMAT, "camera": "room", "seeds": list(SEEDS), "modes": list(MODES),
                  "spec_seed": SPEC_SEED, "history": [],
                  "scripts": {str(seed): build_script(seed) for seed in SEEDS},
                  "fixed_specs": choose_fixed_specs(specs)}
    else:
        golden = load(path)
    committed = committed_known_failing(path)
    named = [k for k in known_failing if k not in committed and not allowlisted_failing([k])]
    if named:
        raise SystemExit("golden: --known-failing {} is neither on the gate's committed allowlist nor pinned "
                         "known-failing in the committed golden; a recorder cannot excuse a failure itself (ask the "
                         "main session for a dated allowlist entry)".format(", ".join(named)))
    runs, specs = compute(golden, None, workers)
    refused, known = failing_references(specs, allowlisted_failing, committed)
    if refused:
        raise SystemExit("golden: refusing to pin failing reference plan(s) as the expected outcome: {} (fix them, or "
                         "ask the main session for a dated entry on the gate's committed allowlist)".format(
                             ", ".join(refused)))
    golden["runs"] = {key: _strip(value) for key, value in sorted(runs.items())}
    golden["specs"] = {key: dict(_strip(value), **({"known_failing": True} if key in known else {}))
                       for key, value in sorted(specs.items())}
    entry = {"by": by, "classes": list(classes), "date": datetime.date.today().isoformat()}
    if known:
        entry["known_failing"] = list(known)
    golden["history"].append(entry)
    save(golden, path)
    return golden


def compare(golden, runs, specs):
    """Human-readable differences between the file and fresh results ([] when equal), plus room-mode problems."""
    out = []
    for key in sorted(runs):
        want, got = golden["runs"].get(key), runs[key]
        for problem in got.get("problems", []):
            out.append("run {}: {}".format(key, problem))
        if want is None:
            out.append("run {}: not in the golden file".format(key))
            continue
        for part in ("state", "obs"):
            if want["reset"][part] != got["reset"][part]:
                out.append("run {} reset: {} digest differs".format(key, part))
        if len(want["steps"]) != len(got["steps"]):
            out.append("run {}: {} steps, golden {}".format(key, len(got["steps"]), len(want["steps"])))
        for i, (a, b) in enumerate(zip(want["steps"], got["steps"]), 1):
            fields = [f for f in ("ok", "seconds", "state", "obs") if a.get(f) != b.get(f)]
            if fields:
                out.append("run {} step {} ({}): {} differ{}".format(
                    key, i, b["action"], ", ".join(fields),
                    "" if "ok" not in fields else " (ok {} -> {})".format(a.get("ok"), b.get("ok"))))
    for key in sorted(specs):
        want, got = golden["specs"].get(key), specs[key]
        for problem in got.get("problems", []):
            out.append("spec {}: {}".format(key, problem))
        if want is None:
            out.append("spec {}: not in the golden file".format(key))
            continue
        if want.get("known_failing") and not want.get("passes") and got.get("passes"):
            continue        # a known-failing reference now passes: an expected change (expected_changes), re-record
        fields = [f for f in ("state", "steps", "time_s", "passes", "refusals", "error", "camera")
                  if want.get(f) != got.get(f)]
        if fields:
            out.append("spec {}: {} differ{}".format(key, ", ".join(fields), "" if "passes" not in fields else
                                                     " (passes {} -> {})".format(want.get("passes"), got.get("passes"))))
    return out


def expected_changes(golden, specs):
    """Known-failing references (pinned with "known_failing") that pass now: the fix the entry waited for."""
    return ["spec {}: known-failing reference now passes (expected change: re-record the golden)".format(key)
            for key in sorted(specs) if (golden["specs"].get(key) or {}).get("known_failing")
            and not golden["specs"][key].get("passes") and specs[key].get("passes")]


def check(all_specs=False, workers=1, path=GOLDEN_FILE):
    _quiet_imports()
    golden = load(path)
    keys = None if all_specs else golden["fixed_specs"]
    runs, specs = compute(golden, keys, workers)
    return compare(golden, runs, specs)


# ----- dump and diff ----------------------------------------------------------------------------------------------

def dump(out_dir, all_specs=False, workers=1, path=GOLDEN_FILE):
    """Write the reset and final states and observations of every run, and the final state of every checked spec, as
    JSON files under out_dir (plus index.json with their digests)."""
    _quiet_imports()
    golden = load(path)
    keys = None if all_specs else golden["fixed_specs"]
    runs, specs = compute(golden, keys, workers, keep=True)
    out = Path(out_dir)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "specs").mkdir(parents=True, exist_ok=True)
    index = {"runs": {}, "specs": {}}
    for key, result in sorted(runs.items()):
        stem = key.replace("/", "_")
        for part, value in sorted(result.pop("full").items()):
            _write(out / "runs" / "{}_{}.json".format(stem, part), value)
        index["runs"][key] = result
    for key, result in sorted(specs.items()):
        full = result.pop("full", None)
        if full is not None:
            _write(out / "specs" / "{}.json".format(key.replace("#", "_")), full)
        index["specs"][key] = result
    _write(out / "index.json", index)
    return index


def _write(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=0)


def pattern(path):
    """A leaf path with ids and indexes replaced by * (entities.mug_1.fell -> entities.*.fell)."""
    parts = path.split(".")
    out = []
    for i, part in enumerate(parts):
        keyed = i > 0 and parts[i - 1] in KEYED
        out.append("*" if keyed or ID_SEGMENT.match(part) else part)
    return ".".join(out)


def leaf_diffs(before, after, prefix=""):
    """(path, before, after) for every differing leaf; lists of equal length are compared item by item."""
    missing = object()
    out = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b), key=str):
                sub = path + "." + str(key) if path else str(key)
                walk(a.get(key, missing), b.get(key, missing), sub)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, "{}.{}".format(path, i))
        elif a != b:
            out.append((path, None if a is missing else a, None if b is missing else b))
    walk(before, after, prefix)
    return out


def diff(before_dir, after_dir, limit=200, examples=2):
    """Leaf differences between two dumps, grouped by path pattern. Returns the summary text."""
    before_dir, after_dir = Path(before_dir), Path(after_dir)
    groups = defaultdict(list)
    files = sorted(set(p.relative_to(before_dir) for p in before_dir.rglob("*.json") if p.name != "index.json") |
                   set(p.relative_to(after_dir) for p in after_dir.rglob("*.json") if p.name != "index.json"))
    changed_files = 0
    for rel in files:
        a, b = before_dir / rel, after_dir / rel
        if not a.exists() or not b.exists():
            groups["<file {}>".format("added" if b.exists() else "removed")].append((str(rel), None, None))
            continue
        with open(a, encoding="utf-8") as ha, open(b, encoding="utf-8") as hb:
            va, vb = json.load(ha), json.load(hb)
        prefix = "obs" if rel.name.endswith("_obs.json") else ""
        diffs = leaf_diffs(va, vb, prefix)
        if diffs:
            changed_files += 1
        for path, x, y in diffs:
            groups[pattern(path)].append(("{}:{}".format(rel.as_posix(), path), x, y))
    lines = ["{} file(s) compared, {} changed, {} path pattern(s)".format(len(files), changed_files, len(groups))]
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k))[:limit]:
        items = groups[key]
        lines.append("{:6d}  {}".format(len(items), key))
        for where, x, y in items[:examples]:
            lines.append("          {}: {} -> {}".format(where, _short(x), _short(y)))
    return "\n".join(lines)


def _short(value, size=90):
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= size else text[:size - 3] + "..."


# ----- command line -----------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="re-run everything and rewrite the digests")
    rec.add_argument("--by", required=True, help="package id recording (history)")
    rec.add_argument("--classes", required=True, help="comma-separated difference classes reviewed (history)")
    rec.add_argument("--new", action="store_true", help="choose the scripts and the fixed specs again")
    rec.add_argument("--known-failing", default="",
                     help="comma-separated catalog ids whose failing reference you expect to pin; each must be on "
                          "the gate's committed allowlist or pinned known-failing in the committed golden")
    chk = sub.add_parser("check", help="compare fresh results with the file")
    chk.add_argument("--all", action="store_true", help="every spec, not only the fixed ones")
    dmp = sub.add_parser("dump", help="write full checkpoint states and observations")
    dmp.add_argument("--out", required=True)
    dmp.add_argument("--all", action="store_true", help="every spec, not only the fixed ones")
    for p in (rec, chk, dmp):
        p.add_argument("--workers", type=int, default=default_workers())
        p.add_argument("--file", default=str(GOLDEN_FILE))
    dif = sub.add_parser("diff", help="leaf differences between two dumps, grouped by path pattern")
    dif.add_argument("before")
    dif.add_argument("after")
    dif.add_argument("--limit", type=int, default=200)
    dif.add_argument("--examples", type=int, default=2)
    args = parser.parse_args(argv)
    if args.cmd == "record":
        golden = record(args.by, [c.strip() for c in args.classes.split(",") if c.strip()], args.new, args.workers,
                        Path(args.file), [c.strip() for c in args.known_failing.split(",") if c.strip()])
        print("recorded {} runs and {} specs into {}".format(len(golden["runs"]), len(golden["specs"]), args.file))
        return 0
    if args.cmd == "check":
        problems = check(args.all, args.workers, Path(args.file))
        print("\n".join(problems) if problems else "golden: equal")
        return 1 if problems else 0
    if args.cmd == "dump":
        index = dump(args.out, args.all, args.workers, Path(args.file))
        print("dumped {} runs and {} specs into {}".format(len(index["runs"]), len(index["specs"]), args.out))
        return 0
    print(diff(args.before, args.after, args.limit, args.examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
