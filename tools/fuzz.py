"""Random-action fuzzer: the simulator must never crash, never mutate state behind the journal, and replay
deterministically. It also reports which registered actions it drove to their success path.

usage: python tools/fuzz.py [houses] [steps_per_house] [--first N] [--workers N] [--json OUT] [--no-determinism]
                            [--replay N|all] [--camera room|geometric] [--perception oracle|realistic|mixed]
                            [--battery PCT] [--faults random|none] [--gen 1|2]
       python tools/fuzz.py --coverage [--specs GLOB] [--seeds S ...] [--explore N] [--limit N] [--workers N]
                            [--json OUT] [--no-determinism]

For each house (seeds first..first+houses-1, rotating dwelling/resident options) it plays `steps` random but
plausible commands: every registered action with arguments drawn from its parameter spec, biased toward things
near the robot (so actions actually succeed and the episode gets deep), interleaved with approaches, room moves
and waits. It runs with HOUSE_SIM_STRICT=1 (untracked mutations raise). Any exception other than a refused action
(ActionError) is a defect; they are deduplicated by (action, exception type, innermost project frame) and printed
with a repro (seed, opts, step, command). Determinism: every house's command log (or the first N with --replay N) is
replayed in a fresh process with a different PYTHONHASHSEED and the final state digests must match.

Per-action success coverage (REVIEW_REPORT H13, phases/p2_tests_gate.md T2): every run prints how many registered
actions succeeded at least once and lists the ones that never did; --json writes "actions" ({name: [succeeded,
tried]} for every registered action), "coverage" (registered, succeeded, never_succeeded, never_tried, noop_successes,
noop_only) and "refusals" (each never-succeeding action's most common refusal messages). A physical action that
"succeeds" without changing the world (HouseEnv.track_effect: no thing, room, body, person, pet or connection changed,
bookkeeping aside -- "It is already fully open") is tried, not succeeded; it is counted in noop_successes and listed in
noop_only when it never succeeded otherwise (phase-1 red team).

--coverage drives every registered action toward its success path instead of playing random houses: the house of
every authored spec (tasks/authored/*.json, --specs GLOB) is built on one seed (rotating 0, 1, 2, 41, 97 over the
specs, or --seeds), its reference plan and wrong plans run leniently (every step through the recorder, so their
successes count), then a targeted walk tries the actions nothing has driven to success yet (the spec's own domain
first) from the spec's setup and again after its reference: arguments ranked by the words they share with the action
(water_plant -> potted_plant_3), with generic repairs on a refusal (approach the target, pick it up, open it) and
--explore N commands per walk (default 40). Each spec's reference is replayed in the walk's process and must reach the
same digest, and every walk is replayed in a fresh process with another PYTHONHASHSEED (unless --no-determinism).
The JSON adds "specs" (per spec: seed, reference digest, actions succeeded) and the coverage fields above.

--camera geometric runs the robot's posed cameras (kernel v3.0; default: each env's own, room) and adds the camera's
looks and turns to the mix. --perception realistic plays the same privileged commands through realistic perception
(ids become handles; every step is observed, so the realistic renderer is fuzzed too, and the final digest includes
the realistic observation); "mixed" (the default with --camera geometric) alternates oracle and realistic houses.

--battery PCT starts the houses with an almost flat pack (kernel v3.0 robot body): at PCT %, PCT / 4 % or PCT / 20 %,
rotating by seed, so some packs run flat within a few hundred commands; even seeds off their dock (battery cuts,
"Powered down partway", PowerOff refusals, the stranded robot), odd seeds left seated where the house put them
(charging back up and waking at 3 %). --faults random schedules random robot faults per house
(body_faults.sample_fault_events: three robot.fault events within the first half hour, the first at 0 s, random kinds,
sides, severities and latent flags, some cleared later), seeded by the house seed so the replay draws the same ones;
failed arms drop what they hold, cameras and microphones fail, the network drops, the robot loses its pose. Both
options are part of every house's replay and default to --perception mixed (the realistic proprioception and the
sensor gates are rendered too). The tier-1 gate runs `--battery 5 --faults random` from BODY-FAULTS on and
`--coverage` from phase 1 on.
Exit status 1 if a defect or a nondeterministic replay was found (coverage is reported, never an exit condition).
"""
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HOUSE_SIM_STRICT", "1")

OPTS = [{}, {"dwelling": "apartment"}, {"dwelling": "house", "require": ["garage", "laundry_room"]},
        {"dwelling": "townhouse"}, {"residents": "minimal", "pets": "none"}, {"dwelling": "house", "require": ["basement", "patio"]}]
WORDS = ["on", "off", "in", "under", "behind", "left", "right", "both", "high", "low", "medium", "normal", "careful",
         "fast", "hot", "cold", "warm", "auto", "eco", "open", "closed", "yes", "no", "help", "done", "all", "1", "0"]
PHRASES = ["Dinner is ready.", "Please wait.", "Is this yours?", "I will tidy this up.", "Careful, the floor is wet.",
           "Can I move this?", "Where should this go?", "All done."]
SKIP = {"undo"}   # undo is exercised, but rarely (it replays inverses that the fuzzer cannot reason about)
SPEC_SEEDS = (0, 1, 2, 41, 97)         # --coverage: the verification seeds, rotated over the specs
EXPLORE_STEPS = 60                     # --coverage: commands per targeted walk (two walks per spec)
STOP_WORDS = {"the", "and", "for", "with", "into", "from", "that", "this", "its", "not", "are", "was", "one", "two",
              "out", "off", "all", "any", "can", "has", "have", "use", "used", "uses", "when", "then", "than", "per",
              "each", "only", "also", "may", "but", "who", "what", "where", "which", "else", "their", "them", "they",
              "you", "your", "robot", "action", "target", "entity", "optional", "default", "given", "returns"}


BATTERY_SHARES = (1.0, 0.25, 0.05)     # --battery PCT: the start level of a house is PCT x one of these (by seed)
FAULT_HORIZON_S = 1800.0                # --faults random: when the random faults start


def robot_options(config, seed, battery=None, faults=None):
    """Apply --battery / --faults to a generated house config (in place): the pack at battery x BATTERY_SHARES[seed % 3]
    % (even seeds undocked), and with faults "random" three random robot fault events seeded by the house seed."""
    robot = config.setdefault("robot", {})
    if battery is not None:
        robot["battery"] = round(float(battery) * BATTERY_SHARES[seed % len(BATTERY_SHARES)], 6)
        if seed % 2 == 0:
            robot.update(docked=False, charging=False)
    if faults == "random":
        from house_sim.kernel import body_faults
        rng = random.Random("fuzz-faults:{}".format(seed))
        config["events"] = list(config.get("events") or []) + body_faults.sample_fault_events(
            rng, count=3, horizon_s=FAULT_HORIZON_S)
    return config


def _env(seed, opts, camera=None, perception=None, battery=None, faults=None):
    from house_sim.generator import generate_house
    from house_sim.kernel import plugins
    from house_sim.kernel.engine import HouseEnv
    plugins.load_domains()
    config = robot_options(generate_house(seed, **opts), seed, battery, faults)
    env = HouseEnv(config, seed=seed, trace=False, observe_on_step=False, camera=camera, perception=perception)
    env.reset()
    env.track_effect = True             # a success that changed nothing is not counted as one (_noop)
    return env


def _perception_of(mode, seed):
    """The perception mode of one house: oracle / realistic, or alternating by seed for "mixed"."""
    if mode == "mixed":
        return "realistic" if seed % 2 else "oracle"
    return mode or "oracle"


def _near_entities(state):
    from house_sim.kernel.predicates import root_room
    room = state["robot"]["room"]
    ids = [e for e in state["entities"] if root_room(state, e) == room]
    return sorted(ids)


def _value(rng, state, name, spec, near):
    kind = spec["type"]
    rooms = sorted(state["rooms"])
    people = sorted(state.get("people", {}))
    pets = sorted(state.get("pets", {}))
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    if "choices" in spec and spec["choices"]:
        return rng.choice(spec["choices"])
    if kind in ("entity", "target", "agent"):
        pool = (held * 3 + near) if rng.random() < 0.85 else sorted(state["entities"])
        if kind in ("target", "agent") and rng.random() < 0.15:
            pool = people + pets + rooms + pool
        return rng.choice(pool) if pool else "nothing"
    if kind == "room":
        here = state["robot"]["room"]
        adj = sorted({r for c in state.get("connections", {}).values() if here in c["rooms"] for r in c["rooms"] if r != here})
        return rng.choice(adj if adj and rng.random() < 0.8 else rooms)
    if kind == "person":
        return rng.choice(people) if people else "nobody"
    if kind == "pet":
        return rng.choice(pets) if pets else "nopet"
    if kind in ("float", "number"):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None or hi is not None:
            lo = float(lo if lo is not None else 0.0)
            hi = float(hi if hi is not None else lo + 100.0)
            return round(rng.uniform(lo, min(hi, lo + 3600.0)), 3)
        return rng.choice([0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 20.0, 60.0, 180.0, -1.0])
    if kind == "int":
        lo, hi = int(spec.get("min", 0)), int(spec.get("max", 10))
        return rng.randint(lo, max(lo, min(hi, lo + 20)))
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "list":
        return rng.choice([[], ["wet", "trip"], [rng.choice(near)] if near else []])
    if kind == "dict":
        return {}
    if kind == "cell":
        from house_sim.kernel import geometry
        cols, rows = geometry.grid_size(state["rooms"][state["robot"]["room"]])
        return [rng.randrange(cols), rng.randrange(rows)]
    if kind == "text":
        return rng.choice(PHRASES)
    if kind == "str":
        return rng.choice(WORDS + (near[:3] if near else []))
    return rng.choice(WORDS)


def _command(rng, env, names, stats):
    from house_sim.kernel.plugins import ACTIONS
    state = env.world.s
    near = _near_entities(state)
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    roll = rng.random()
    if roll < 0.16 and near:
        return {"action": "approach", "target": rng.choice(near)}
    if roll < 0.21:
        return {"action": "move", "target": _value(rng, state, "target", {"type": "room"}, near)}
    if roll < 0.24:
        return {"action": "wait", "seconds": rng.choice([30.0, 120.0, 600.0])}
    if roll < 0.32 and near and len(held) < 2:
        portable = [e for e in near if state["entities"][e].get("portable")]
        if portable:
            if rng.random() < 0.3:
                return {"action": rng.choice(["crouch", "stand"])}
            return {"action": "pick_up", "target": rng.choice(portable)}
    if roll < 0.40 and held:
        dest = [e for e in near if state["entities"][e].get("surface") or state["entities"][e].get("container")]
        return {"action": "place", "target": rng.choice(held), "destination": rng.choice(dest + [state["robot"]["room"]])}
    if roll < 0.44 and isinstance(state["robot"].get("view"), dict):
        # the geometric camera's own verbs (look's forms, turning to a thing when the face verb exists)
        if near and "face" in ACTIONS and rng.random() < 0.4:
            return {"action": "face", "target": rng.choice(near)}
        return {"action": "look", "target": rng.choice(["around", "forward", "down", "up", "left", "right", "behind"])}
    # actions that have worked before get picked more often (deeper episodes)
    weights = [1.0 + 4.0 * stats.get(n, (0, 0))[0] / (1.0 + stats.get(n, (0, 0))[1]) for n in names]
    name = rng.choices(names, weights)[0]
    spec = ACTIONS[name]
    cmd = {"action": name}
    for pname, pspec in spec.params.items():
        if pspec.get("optional") and rng.random() < 0.5:
            continue
        cmd[pname] = _value(rng, state, pname, pspec, near)
    if name == "wait":
        cmd["seconds"] = min(float(cmd.get("seconds", 60.0) or 60.0), 900.0)
    return cmd


def _step(env, cmd):
    args = dict(cmd)
    if env.realism is not None:
        args = env.realism.to_handles(args)      # privileged ids -> the handles a realistic agent would use
    action = args.pop("action")
    target = args.pop("target", "")
    destination = args.pop("destination", "")
    result = env.step(action, target if isinstance(target, str) else json.dumps(target),
                      destination if isinstance(destination, str) else json.dumps(destination), **args)
    if env.realism is not None:
        result["observation"] = env.observe()    # the realistic renderer runs every step
    return result


def _final_digest(env):
    from house_sim.kernel.predicates import digest
    if env.realism is None:
        return digest(env.world.s)
    return digest({"state": env.world.s, "observation": env.observe()})


def _frame(tb):
    frames = [f for f in traceback.extract_tb(tb) if "house_sim" in f.filename]
    f = frames[-1] if frames else traceback.extract_tb(tb)[-1]
    return "{}:{} ({})".format(os.path.relpath(f.filename, ROOT), f.lineno, f.name)


def _noop(env, action, ok):
    """True for a successful physical action that changed nothing in the world but bookkeeping (env.track_effect:
    HouseEnv.last_step_effect; phase-1 red team: open_valve "It is already fully open" counted as driven to success).
    Perception, communication, cognitive and control actions succeed by what they tell or record."""
    if not ok:
        return False
    from house_sim.kernel.plugins import ACTIONS
    spec = ACTIONS.get(str(action).strip().lower())
    effect = getattr(env, "last_step_effect", None)
    return spec is not None and spec.category == "physical" and effect is not None and not effect


def _tally(stats, action, ok, message=None, refusals=None, noop=False, noops=None):
    """Count one command of `action` (stats: {action: [succeeded, tried]}; refusals: {action: Counter of messages}). A
    no-op success (noop) counts as tried, not succeeded, and is counted in noops ({action: n})."""
    good, tries = stats.get(action, (0, 0))
    stats[action] = [good + (1 if ok and not noop else 0), tries + 1]
    if noop and noops is not None:
        noops[action] = noops.get(action, 0) + 1
    if not ok and refusals is not None and message:
        refusals.setdefault(action, Counter())[str(message)[:90]] += 1


def run_house(job):
    seed, opts, steps = job[:3]
    camera = job[3] if len(job) > 3 else None
    perception = job[4] if len(job) > 4 else None
    battery = job[5] if len(job) > 5 else None
    faults = job[6] if len(job) > 6 else None
    from house_sim.kernel.plugins import ACTIONS
    rng = random.Random("fuzz:{}".format(seed))
    defects, log = [], []
    try:
        env = _env(seed, opts, camera, perception, battery, faults)
    except Exception as exc:  # noqa: BLE001
        return {"seed": seed, "opts": opts, "camera": camera, "perception": perception, "battery": battery,
                "faults": faults,
                "defects": [{"step": -1, "command": "generate/reset", "error": "{}: {}".format(type(exc).__name__, exc),
                             "where": _frame(exc.__traceback__), "trace": traceback.format_exc()[-2500:]}],
                "log": [], "digest": None, "ok": 0, "stats": {}, "refusals": {}, "noops": {}}
    names = sorted(n for n in ACTIONS if n not in SKIP)
    ok, stats, refusals, noops = 0, {}, {}, {}
    for i in range(steps):
        cmd = _command(rng, env, names, stats)
        log.append(cmd)
        try:
            result = _step(env, cmd)
            ok += 1 if result.get("ok") else 0
            _tally(stats, cmd["action"], result.get("ok"), result.get("message"), refusals,
                   _noop(env, cmd["action"], result.get("ok")), noops)
        except Exception as exc:  # noqa: BLE001
            defects.append({"step": i, "command": cmd, "error": "{}: {}".format(type(exc).__name__, str(exc)[:300]),
                            "where": _frame(exc.__traceback__), "trace": traceback.format_exc()[-2500:]})
            log.pop()
            try:
                env.world.rollback()
            except Exception:  # noqa: BLE001
                pass
            if len(defects) >= 8:
                break
    try:
        final = _final_digest(env)
    except Exception as exc:  # noqa: BLE001
        final = None
        defects.append({"step": steps, "command": "digest", "error": "{}: {}".format(type(exc).__name__, exc), "where": _frame(exc.__traceback__), "trace": ""})
    return {"seed": seed, "opts": opts, "camera": camera, "perception": perception, "battery": battery,
            "faults": faults, "defects": defects, "log": log, "digest": final, "ok": ok, "stats": stats,
            "refusals": {k: dict(v) for k, v in refusals.items()}, "noops": noops}


def replay(seed, opts, log, camera=None, perception=None, battery=None, faults=None):
    env = _env(seed, opts, camera, perception, battery, faults)
    for cmd in log:
        try:
            _step(env, cmd)
        except Exception:  # noqa: BLE001
            env.world.rollback()
    return _final_digest(env)


# ----------------------------------------------------------------------------------------------------------------
# --coverage: spec-seeded walks toward every action's success path

class Recorder:
    """Wraps an environment's step / observe / phase_done so that every top-level call is (1) counted per action
    (successes and tries), (2) logged in its privileged form (ids, not realistic handles) for an exact replay, and
    (3) checked: an exception other than a refusal is a defect (the world is rolled back and the caller sees a
    refusal, so a plan or walk goes on). Nested calls (the step's own observation) are neither logged nor counted."""

    def __init__(self, env, stats, refusals, defects, where, noops=None):
        self.env, self.stats, self.refusals, self.defects, self.where = env, stats, refusals, defects, where
        self.noops = noops if noops is not None else {}
        self.calls, self.depth = [], 0
        self._step, self._observe, self._phase_done = env.step, env.observe, env.phase_done
        env.step, env.observe, env.phase_done = self.step, self.observe, self.phase_done

    def detach(self):
        """Restore the environment's own methods (before a final digest, whose observation is not part of the log)."""
        self.env.step, self.env.observe, self.env.phase_done = self._step, self._observe, self._phase_done
        return self.calls

    def _privileged(self, value):
        realism = self.env.realism
        return realism.from_handles(value) if realism is not None else value

    def _defect(self, what, exc):
        self.defects.append({"where": _frame(exc.__traceback__), "command": what, "context": self.where,
                             "error": "{}: {}".format(type(exc).__name__, str(exc)[:300]),
                             "trace": traceback.format_exc()[-2500:]})
        try:
            self.env.world.rollback()
        except Exception:  # noqa: BLE001
            pass

    def step(self, action, target="", destination="", **kwargs):
        if self.depth:
            return self._step(action, target, destination, **kwargs)
        call = ["step", action, self._privileged(target), self._privileged(destination), self._privileged(kwargs)]
        self.depth += 1
        try:
            result = self._step(action, target, destination, **kwargs)
        except Exception as exc:  # noqa: BLE001  (a defect: recorded, rolled back, seen as a refusal)
            self._defect({"action": action, "target": call[2], "destination": call[3], "args": call[4]}, exc)
            return {"ok": False, "message": "fuzz: defect ({})".format(type(exc).__name__), "category": "defect",
                    "observation": None}
        finally:
            self.depth -= 1
        self.calls.append(call)
        noop = _noop(self.env, action, result.get("ok"))
        _tally(self.stats, str(action).strip().lower(), result.get("ok"), result.get("message"), self.refusals, noop,
               self.noops)
        if noop:
            result = dict(result, _noop=True)       # the walker keeps trying to drive it to a real success
        return result

    def observe(self):
        if self.depth:
            return self._observe()
        self.depth += 1
        try:
            obs = self._observe()
        finally:
            self.depth -= 1
        self.calls.append(["observe"])
        return obs

    def phase_done(self):
        if self.depth:
            return self._phase_done()
        self.depth += 1
        try:
            result = self._phase_done()
        finally:
            self.depth -= 1
        self.calls.append(["phase_done"])
        return result


def replay_calls(env, calls):
    """Replay logged calls (Recorder.calls) on a fresh environment: ids become handles again exactly as the runner and
    the walker made them (realism.to_handles issues them), so the replay reaches the same state."""
    realism = env.realism
    if realism is not None:
        env.raw_results = True
    for call in calls:
        if call[0] == "observe":
            env.observe()
        elif call[0] == "phase_done":
            env.phase_done()
        else:
            _, action, target, destination, kwargs = call
            if realism is not None:
                target, destination, kwargs = (realism.to_handles(target), realism.to_handles(destination),
                                               realism.to_handles(kwargs))
            try:
                env.step(action, target, destination, **kwargs)
            except Exception:  # noqa: BLE001  (the defect is reported by the original run)
                env.world.rollback()


def load_spec_list(pattern=None):
    """[(file name, index, spec)] of tasks/authored/*.json (or the files matching --specs GLOB), in file order."""
    out = []
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        name = Path(path).name
        if pattern and not Path(name).match(pattern):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for i, spec in enumerate(data.get("specs", []) if isinstance(data, dict) else data):
            out.append((name, i, spec))
    return out


def _spec_env(spec, seed):
    """(env, oracle) of one spec on one seed in its own perception and camera mode (the house a verifier builds)."""
    from house_sim.kernel import plugins
    from house_sim.tasks.runner import build, make_env
    plugins.load_domains()
    config, task, oracle = build(spec, seed)
    env = make_env(config, task, seed)
    env.track_effect = True             # a success that changed nothing is not counted as one (_noop)
    return env, oracle


def _tokens(text, stop=STOP_WORDS):
    return {t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if len(t) > 2 and t not in stop}


def _action_words(spec):
    """Words an action shares with the things it acts on: its name (weight 3) and its doc (weight 1)."""
    return _tokens(spec.name), _tokens(spec.doc) - _tokens(spec.name)


def _entity_words(state, eid):
    rec = state["entities"].get(eid) or {}
    return _tokens(eid) | _tokens(rec.get("type")) | _tokens(rec.get("category")) | _tokens(rec.get("name"))


def _ranked_entities(state, name_words, doc_words, pname, near, held, limit=4):
    """Entities for one parameter, best first: shared words with the action's name (x3), its doc (x1) and the
    parameter's name (x2), held (+2) and in the robot's room (+1); ties by id."""
    pwords = _tokens(pname, ()) - {"target"}
    scored = []
    for eid in state["entities"]:
        words = _entity_words(state, eid)
        score = 3 * len(words & name_words) + len(words & doc_words) + 2 * len(words & pwords)
        score += (2 if eid in held else 0) + (1 if eid in near else 0)
        if score > 1:
            scored.append((-score, eid))
    ranked = [eid for _, eid in sorted(scored)]
    return ranked[:limit] or (held + near)[:limit]


def _targeted_command(rng, env, name):
    """A command for `name` with arguments ranked toward its success (_ranked_entities) and its main target (the
    first entity, person or pet argument), or None."""
    from house_sim.kernel.plugins import ACTIONS
    state = env.world.s
    spec = ACTIONS[name]
    near = _near_entities(state)
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    name_words, doc_words = _action_words(spec)
    cmd, target = {"action": name}, None
    for pname, pspec in spec.params.items():
        if pspec.get("optional") and pname not in ("target", "destination", "tool") and rng.random() < 0.7:
            continue
        if pspec["type"] in ("entity", "target", "agent") and "choices" not in pspec:
            pool = _ranked_entities(state, name_words, doc_words, pname, near, held)
            if pspec["type"] in ("target", "agent") and not pool:
                pool = sorted(state.get("people", {})) + sorted(state.get("pets", {}))
            if not pool:
                continue
            value = pool[0] if rng.random() < 0.6 else rng.choice(pool)
            if pname == "destination" and value == target and len(pool) > 1:
                value = pool[1]
            cmd[pname] = value
        else:
            cmd[pname] = _value(rng, state, pname, pspec, near)
        value = cmd.get(pname)
        if target is None and isinstance(value, str) and (value in state["entities"] or value in
                                                          state.get("people", {}) or value in state.get("pets", {})):
            target = value
    if name == "wait":
        cmd["seconds"] = min(float(cmd.get("seconds", 60.0) or 60.0), 900.0)
    return cmd, target


APPROACH_WORDS = ("too far", "reach", "approach", "closer", "not near", "not next to", "next to it", "go to",
                  "not in this room", "isn't here", "is not here", "not here", "not visible", "can't see",
                  "cannot see", "not in view")
OPEN_WORDS = ("is closed", "are closed", "open it", "open the", "is shut", "lid is")
HOLD_RE = re.compile(r"\bhold (?:the |a |an |some |one of )?([a-z][a-z0-9 \-]*?)(?: first| to | before|$|[.;,(])")
NOT_A_RE = re.compile(r"\b(?:is not|isn't|not) (?:a|an|the|any) ([a-z][a-z0-9 \-]*?)(?: or |$|[.;,(])")
USE_RE = re.compile(r"\b(?:needs?|requires?|use|with) (?:a|an|the) ([a-z][a-z0-9 \-]*?)(?: first| to |$|[.;,(])")
WALK_ERRORS = ("PlanError", "SpecError")       # a routing skill that gives up: not a defect


def _find_entity(state, phrase, near, exclude=(), portable=False):
    """The entity whose id, type, category or name shares the most words with `phrase` (in the robot's room first,
    then by id), or None."""
    words = _tokens(phrase, ())
    if not words:
        return None
    best = None
    for eid, rec in state["entities"].items():
        if eid in exclude or (portable and not rec.get("portable")):
            continue
        overlap = len(_entity_words(state, eid) & words)
        if overlap:
            key = (-overlap, 0 if eid in near else 1, eid)
            if best is None or key < best[0]:
                best = (key, eid)
    return best[1] if best else None


def _repair(env, cmd, target, message):
    """Generic repairs of a refusal, read from its message: ([(skill, args) to run first], retry command, its
    target) or None. Go to the target ("out of arm's reach", "not visible here": the runner's reveal and approach
    skills, across rooms and into closed containers), fetch the thing to hold ("Hold the sponge first": the best-matching
    portable thing; "Hold it first": the target), open it ("is closed"), or swap the target for the best-matching thing
    ("That is not an envelope")."""
    from house_sim.kernel.plugins import ACTIONS
    state = env.world.s
    near = _near_entities(state)
    held = [v for v in state["robot"].get("hands", {}).values() if v]
    text = str(message or "").lower()
    m = HOLD_RE.search(text) or USE_RE.search(text)
    if m:
        phrase = m.group(1).strip()
        thing = target if phrase in ("it", "them", "this", "that", "one") else _find_entity(
            state, phrase, near, exclude=held, portable=True)
        if thing and thing not in held and thing in state["entities"]:
            pre = [("drop", held[0])] if len(held) >= 2 else []
            return pre + [("fetch", thing)], cmd, target
    m = NOT_A_RE.search(text)
    if m and target:
        swap = _find_entity(state, m.group(1).strip(), near, exclude=(target,))
        if swap:
            new = {k: (swap if v == target else v) for k, v in cmd.items()}
            return [("goto", swap)], new, swap
    if target and any(w in text for w in APPROACH_WORDS):
        return [("goto", target)], cmd, target
    if target and "open" in ACTIONS and target in state["entities"] and any(w in text for w in OPEN_WORDS):
        return [("open", target)], cmd, target
    return None


def _prepare(runner, how, thing):
    """One preparation through the task runner's own routing skills (privileged routing, every change through
    env.step, so the recorder counts and logs it): goto (reveal it from closed containers, then walk to it across
    rooms), fetch (go and pick it up), open, drop (put a held thing down here)."""
    state = runner.state
    if how == "goto":
        if thing in state["entities"]:
            runner.skill("reveal", obj=thing)
        runner.skill("approach", target=thing)
    elif how == "fetch":
        runner.skill("fetch", obj=thing)
    elif how == "open":
        runner.skill("approach", target=thing)
        runner.do("open", target=thing)
    elif how == "drop":
        runner.do("place", target=thing)


def _walk(rng, env, focus, budget, recorder, defects=None, repairs=3):
    """A targeted walk: up to `budget` recorded commands trying the actions of `focus` (each once per round, in a
    shuffled order, until it succeeds once) with ranked arguments: go to the target first (across rooms, out of closed
    containers) and repair a refusal up to `repairs` times (_repair). A routing skill that raises anything but a
    PlanError / SpecError is a defect of the skill code (recorded in `defects`)."""
    from house_sim.kernel.plugins import ACTIONS
    from house_sim.tasks.runner import Runner
    runner = Runner(env, {}, strict=False)
    start = len(recorder.calls)
    order = [name for name in focus if name in ACTIONS]

    def spent():
        return len(recorder.calls) - start

    def prepare(how, thing):
        try:
            _prepare(runner, how, thing)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in WALK_ERRORS and defects is not None:
                defects.append({"where": _frame(exc.__traceback__), "command": {"action": "skill " + how,
                                                                                "target": thing},
                                "context": recorder.where, "error": "{}: {}".format(type(exc).__name__,
                                                                                    str(exc)[:300]),
                                "trace": traceback.format_exc()[-2500:], "kind": "skill"})

    while spent() < budget and order:
        for name in list(order):
            if spent() >= budget:
                break
            try:
                cmd, target = _targeted_command(rng, env, name)
            except Exception:  # noqa: BLE001  (argument drawing only reads state; a bad record is skipped)
                order.remove(name)
                continue
            state = env.world.s
            held = [v for v in state["robot"].get("hands", {}).values() if v]
            if target and target not in held and target not in _near_entities(state):
                prepare("goto", target)
            result = runner.do(cmd["action"], **{k: v for k, v in cmd.items() if k != "action"})
            for _ in range(repairs):
                if result.get("ok") or spent() >= budget:
                    break
                try:
                    fix = _repair(env, cmd, target, result.get("message"))
                except Exception:  # noqa: BLE001
                    fix = None
                if fix is None:
                    break
                pre, cmd, target = fix
                for how, thing in pre:
                    prepare(how, thing)
                result = runner.do(cmd["action"], **{k: v for k, v in cmd.items() if k != "action"})
            if result.get("ok") and not result.get("_noop") and name in order:
                order.remove(name)          # covered in this walk: the budget goes to the others
        rng.shuffle(order)


def _domain_first(rng, focus, domain):
    """Focus actions owned by the spec's domain first (its setup staged that domain's things), then the rest; each
    group shuffled by the walk's own random stream so the specs of one domain try different actions first."""
    from house_sim.kernel.plugins import OWNERS
    mine = [a for a in focus if ".systems.{}.".format(domain) in str(OWNERS.get(("action", a), ""))]
    rest = [a for a in focus if a not in mine]
    rng.shuffle(mine)
    rng.shuffle(rest)
    return mine + rest


def coverage_pass1(job):
    """Pass 1 of --coverage for one spec: its reference and every wrong plan of the seed's variant, each in its own
    environment, run leniently through the recorder. Returns the episode logs, digests, stats and defects."""
    fname, index, spec, seed = job
    from house_sim.kernel.errors import SpecError
    from house_sim.tasks.runner import PlanError, Runner
    cid = spec.get("catalog_id") or "{}#{}".format(fname, index)
    stats, refusals, defects, episodes, noops = {}, {}, [], [], {}
    out = {"spec": cid, "file": fname, "index": index, "seed": seed, "episodes": episodes, "stats": stats,
           "refusals": {}, "defects": defects, "noops": noops}
    try:
        env, oracle = _spec_env(spec, seed)
    except Exception as exc:  # noqa: BLE001  (a spec that does not build is the verifier's business)
        out["error"] = "{}: {}".format(type(exc).__name__, str(exc)[:200])
        return out
    kinds = ["reference"] + ["wrong:{}".format(i) for i in range(len(oracle.get("wrong") or []))]
    for n, kind in enumerate(kinds):
        if n:
            env, oracle = _spec_env(spec, seed)
        plan = oracle["reference"] if kind == "reference" else (oracle["wrong"][n - 1] or {}).get("plan")
        recorder = Recorder(env, stats, refusals, defects, "{} seed {} {}".format(cid, seed, kind), noops)
        runner = Runner(env, oracle["bindings"], strict=False)
        stopped = None
        for step in plan or []:
            try:
                runner.run([step])
            except Exception as exc:  # noqa: BLE001  (a plan that stops: its steps so far count)
                stopped = "{}: {}".format(type(exc).__name__, str(exc)[:160])
                if not isinstance(exc, (PlanError, SpecError)):
                    # skill or runner code raised (the recorder catches the simulator's own exceptions): a defect
                    defects.append({"where": _frame(exc.__traceback__), "command": "plan step", "context":
                                    recorder.where, "error": stopped, "trace": traceback.format_exc()[-2500:],
                                    "kind": "plan"})
                break
        calls = recorder.detach()
        episodes.append({"kind": kind, "calls": calls, "digest": _final_digest(env), "stopped": stopped})
    out["refusals"] = {k: dict(v) for k, v in refusals.items()}
    return out


def coverage_pass2(job):
    """Pass 2 of --coverage for one spec: a targeted walk toward `focus` from the spec's setup, and one after
    replaying its reference (which must reach pass 1's digest)."""
    fname, index, spec, seed, ref_calls, ref_digest, focus, budget = job
    cid = spec.get("catalog_id") or "{}#{}".format(fname, index)
    domain = fname.split("__", 1)[0]
    stats, refusals, defects, episodes, mismatch, noops = {}, {}, [], [], None, {}
    for kind in ("setup", "after"):
        rng = random.Random("fuzz-coverage:{}:{}:{}".format(cid, seed, kind))
        order = _domain_first(rng, focus, domain)
        try:
            env, _ = _spec_env(spec, seed)
        except Exception as exc:  # noqa: BLE001
            return {"spec": cid, "file": fname, "index": index, "seed": seed, "error": "{}: {}".format(
                type(exc).__name__, str(exc)[:200]), "episodes": [], "stats": stats, "refusals": {}, "defects": [],
                "mismatch": None}
        prefix = []
        if kind == "after":
            replay_calls(env, ref_calls)
            got = _final_digest(env)
            if got != ref_digest:
                mismatch = {"spec": cid, "seed": seed, "first": ref_digest, "replay": got}
            # the digest's own observation (realistic perception) is part of this walk's history
            prefix = list(ref_calls) + ([["observe"]] if env.realism is not None else [])
        recorder = Recorder(env, stats, refusals, defects, "{} seed {} walk {}".format(cid, seed, kind), noops)
        _walk(rng, env, order, budget, recorder, defects)
        calls = recorder.detach()
        episodes.append({"kind": kind, "calls": prefix + calls, "digest": _final_digest(env)})
    return {"spec": cid, "file": fname, "index": index, "seed": seed, "episodes": episodes, "stats": stats,
            "refusals": {k: dict(v) for k, v in refusals.items()}, "defects": defects, "mismatch": mismatch,
            "noops": noops}


def replay_episode_file(path):
    """Replay every episode of a JSON file ({"episodes": [{"file", "index", "seed", "calls"}]}) in this process and
    print their final digests as one JSON list (the cross-process determinism check of --coverage)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    specs = {}
    out = []
    for ep in data["episodes"]:
        key = (ep["file"], ep["index"])
        if key not in specs:
            with open(ROOT / "tasks" / "authored" / ep["file"], encoding="utf-8") as f:
                raw = json.load(f)
            specs[key] = (raw.get("specs", []) if isinstance(raw, dict) else raw)[ep["index"]]
        try:
            env, _ = _spec_env(specs[key], ep["seed"])
            replay_calls(env, ep["calls"])
            out.append(_final_digest(env))
        except Exception as exc:  # noqa: BLE001
            out.append("ERROR {}: {} at {}".format(type(exc).__name__, str(exc)[:200], _frame(exc.__traceback__)))
    print(json.dumps(out))


def _replay_chunks(chunks, workers, tag):
    """Run each chunk of episodes in a fresh process (another PYTHONHASHSEED per chunk); returns digests per chunk."""
    tmp = Path(tempfile.mkdtemp(prefix="house_fuzz_replay_"))
    try:
        def one(i_chunk):
            i, chunk = i_chunk
            path = tmp / "{}_{}.json".format(tag, i)
            path.write_text(json.dumps({"episodes": chunk}), encoding="utf-8")
            env = dict(os.environ, PYTHONHASHSEED=str(4242 + i))
            code = ("import sys; sys.path.insert(0, {root!r}); sys.argv=['x']; from tools.fuzz import "
                    "replay_episode_file; replay_episode_file({f!r})").format(root=str(ROOT), f=str(path))
            proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=7200)
            lines = proc.stdout.strip().splitlines()
            try:
                return json.loads(lines[-1])
            except (IndexError, ValueError):
                return ["ERROR " + proc.stderr[-400:]] * len(chunk)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(one, enumerate(chunks)))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def _merge(total, stats):
    for name, (good, tries) in stats.items():
        g, t = total.get(name, (0, 0))
        total[name] = [g + good, t + tries]


def coverage_report(stats, refusals, registered, noops=None):
    """(actions {name: [succeeded, tried]} for every registered action, coverage summary, refusal digest). Succeeded
    counts real successes only: a physical action that "succeeded" without changing the world (_noop) is listed in
    summary["noop_successes"] ({name: n}) and, when it never succeeded otherwise, in summary["noop_only"]."""
    actions = {name: list(stats.get(name, (0, 0))) for name in sorted(registered)}
    never = [n for n, (good, _) in actions.items() if good == 0]
    noops = {n: c for n, c in sorted((noops or {}).items()) if c}
    summary = {"registered": len(actions), "succeeded": len(actions) - len(never), "never_succeeded": never,
               "never_tried": [n for n in never if actions[n][1] == 0], "noop_successes": noops,
               "noop_only": [n for n in never if noops.get(n)]}
    digest = {n: Counter(refusals.get(n) or {}).most_common(3) for n in never if refusals.get(n)}
    return actions, summary, digest


def print_coverage(summary, actions, refusals, limit=60):
    print("actions: {} of {} registered actions succeeded at least once; {} never succeeded ({} never tried)".format(
        summary["succeeded"], summary["registered"], len(summary["never_succeeded"]), len(summary["never_tried"])))
    never = summary["never_succeeded"]
    if never:
        print("  never succeeded: " + ", ".join("{} ({} tried)".format(n, actions[n][1]) for n in never[:limit]) +
              (", ..." if len(never) > limit else ""))
    if summary.get("noop_only"):
        print("  succeeded only without changing anything (not counted): " + ", ".join(
            "{} ({}x)".format(n, summary["noop_successes"][n]) for n in summary["noop_only"][:limit]))


def main_coverage(argv):
    from house_sim.kernel.plugins import ACTIONS, load_domains
    load_domains()
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    pattern = argv[argv.index("--specs") + 1] if "--specs" in argv else None
    budget = int(argv[argv.index("--explore") + 1]) if "--explore" in argv else EXPLORE_STEPS
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    seeds = SPEC_SEEDS
    if "--seeds" in argv:
        rest = argv[argv.index("--seeds") + 1:]
        seeds = tuple(int(x) for x in rest[:next((i for i, x in enumerate(rest) if not x.lstrip("-").isdigit()),
                                                  len(rest))])
    specs = load_spec_list(pattern)[:limit] if limit else load_spec_list(pattern)
    jobs = [(fname, index, spec, seeds[k % len(seeds)]) for k, (fname, index, spec) in enumerate(specs)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        first = list(pool.map(coverage_pass1, jobs))
    stats, refusals, noops = {}, {}, Counter()
    for r in first:
        _merge(stats, r["stats"])
        noops.update(r.get("noops") or {})
        for name, msgs in r["refusals"].items():
            refusals.setdefault(name, Counter()).update(msgs)
    covered = {n for n, (good, _) in stats.items() if good}
    focus = sorted(n for n in ACTIONS if n not in covered)
    jobs2 = []
    for (fname, index, spec, seed), r in zip(jobs, first):
        ref = next((e for e in r["episodes"] if e["kind"] == "reference"), None)
        if ref is None:
            continue
        jobs2.append((fname, index, spec, seed, ref["calls"], ref["digest"], focus, budget))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        second = list(pool.map(coverage_pass2, jobs2))
    for r in second:
        _merge(stats, r["stats"])
        noops.update(r.get("noops") or {})
        for name, msgs in r["refusals"].items():
            refusals.setdefault(name, Counter()).update(msgs)
    seen = {}
    for r in first + second:
        for d in r.get("defects", []):
            action = d["command"].get("action") if isinstance(d["command"], dict) else d["command"]
            key = (action, d["error"].split(":")[0], d["where"])
            if key not in seen:
                seen[key] = dict(d, seed=r["seed"], spec=r["spec"], count=0)
            seen[key]["count"] += 1
    nondet = [r["mismatch"] for r in second if r.get("mismatch")]
    if "--no-determinism" not in argv:
        episodes = [dict(ep, file=r["file"], index=r["index"], seed=r["seed"], spec=r["spec"])
                    for r in second for ep in r["episodes"]]
        chunks = [episodes[i::max(1, workers)] for i in range(max(1, workers))]
        chunks = [c for c in chunks if c]
        for chunk, digests in zip(chunks, _replay_chunks(chunks, workers, "coverage")):
            for ep, got in zip(chunk, digests):
                if got != ep["digest"]:
                    nondet.append({"spec": ep["spec"], "seed": ep["seed"], "first": ep["digest"], "replay": got})
    actions, summary, digest = coverage_report(stats, refusals, ACTIONS, noops)
    commands = sum(t for _, t in stats.values())
    succeeded = sum(g for g, _ in stats.values())
    print("houses {}  commands {}  succeeded {} ({:.0%})  distinct defects {}  nondeterministic {}  coverage mode "
          "(specs {}, explore {})".format(len(first), commands, succeeded, succeeded / max(1, commands), len(seen),
                                          len(nondet), len(specs), budget))
    print_coverage(summary, actions, digest)
    for key, d in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
        print("- [{}x] {} -> {} at {}".format(d["count"], key[0], d["error"], d["where"]))
        print("    repro: spec {} seed {} ({}) command {}".format(d["spec"], d["seed"], d.get("context"),
                                                                  json.dumps(d["command"], default=str)[:300]))
    for n in nondet:
        print("- NONDETERMINISTIC replay: spec {} seed {} digest {} vs {}".format(n["spec"], n["seed"],
                                                                                str(n["first"])[:12], str(n["replay"])[:60]))
    if out:
        Path(out).write_text(json.dumps({
            "mode": "coverage", "defects": list(seen.values()), "nondeterministic": nondet, "actions": actions,
            "coverage": summary, "refusals": digest,
            "specs": [{"spec": r["spec"], "file": r["file"], "seed": r["seed"], "error": r.get("error"),
                       "reference_digest": next((e["digest"] for e in r["episodes"] if e["kind"] == "reference"), None),
                       "reference_stopped": next((e.get("stopped") for e in r["episodes"]
                                                  if e["kind"] == "reference"), None),
                       "succeeded": sorted(n for n, (g, _) in r["stats"].items() if g)} for r in first]},
            indent=1, default=str), encoding="utf-8")
    return 1 if seen or nondet else 0


def main(argv):
    if "--coverage" in argv:
        return main_coverage(argv)
    houses = int(argv[0]) if argv and argv[0].isdigit() else 12
    steps = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 300
    first = int(argv[argv.index("--first") + 1]) if "--first" in argv else 0
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else max(1, (os.cpu_count() or 2) - 2)
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    camera = argv[argv.index("--camera") + 1] if "--camera" in argv else None
    faults = argv[argv.index("--faults") + 1] if "--faults" in argv else None
    replay_n = argv[argv.index("--replay") + 1] if "--replay" in argv else "all"
    try:
        battery = float(argv[argv.index("--battery") + 1]) if "--battery" in argv else None
    except (IndexError, ValueError):
        battery = -1.0
    body_run = battery is not None or faults == "random"
    perception = argv[argv.index("--perception") + 1] if "--perception" in argv else \
        ("mixed" if camera == "geometric" or body_run else None)
    if camera not in (None, "room", "geometric") or perception not in (None, "oracle", "realistic", "mixed") or \
            faults not in (None, "random", "none") or (battery is not None and not 0.0 <= battery <= 100.0) or \
            not (replay_n == "all" or replay_n.isdigit()):
        print("--camera must be room or geometric; --perception oracle, realistic or mixed; --battery a % from 0 to "
              "100; --faults random or none; --replay a number or all")
        return 2
    gen = argv[argv.index("--gen") + 1] if "--gen" in argv else None
    if gen not in (None, "1", "2"):
        print("--gen must be 1 or 2 (generation-2 houses: electrical's phase-1 request 5)")
        return 2
    faults = None if faults == "none" else faults
    from house_sim.kernel.plugins import ACTIONS, load_domains
    load_domains()
    jobs = [(first + k, dict(OPTS[k % len(OPTS)], **({"gen": int(gen)} if gen else {})), steps, camera,
             _perception_of(perception, first + k) if perception else None, battery, faults) for k in range(houses)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_house, jobs))
    seen, rows = {}, []
    for r in results:
        for d in r["defects"]:
            key = (d["command"]["action"] if isinstance(d["command"], dict) else d["command"], d["error"].split(":")[0], d["where"])
            if key not in seen:
                seen[key] = dict(d, seed=r["seed"], opts=r["opts"], camera=r.get("camera"), perception=r.get("perception"),
                                 battery=r.get("battery"), faults=r.get("faults"), count=0)
            seen[key]["count"] += 1
    nondet = []
    if "--no-determinism" not in argv:
        # every house (the review found only the first 4 replayed, T9), in parallel fresh processes
        wanted = results if replay_n == "all" else results[:int(replay_n)]

        def replay_one(r):
            env = dict(os.environ, PYTHONHASHSEED=str(12345 + r["seed"]))
            code = ("import json,sys; sys.path.insert(0, {root!r}); sys.argv=['x']; from tools.fuzz import replay; "
                    "d=json.load(open({f!r})); print(replay(d['seed'], d['opts'], d['log'], d.get('camera'), "
                    "d.get('perception'), d.get('battery'), d.get('faults')))")
            # per process and variant: the gate runs the room, geometric and battery fuzz at the same time
            body_variant = r.get("battery") is not None or r.get("faults")
            variant = "{}{}".format(r.get("camera") or "env", "_body" if body_variant else "")
            tmp = ROOT / "tools" / ".fuzz_replay_{}_{}_{}.json".format(r["seed"], variant, os.getpid())
            tmp.write_text(json.dumps({"seed": r["seed"], "opts": r["opts"], "log": r["log"], "camera": r.get("camera"),
                                       "perception": r.get("perception"), "battery": r.get("battery"),
                                       "faults": r.get("faults")}), encoding="utf-8")
            try:
                proc = subprocess.run([sys.executable, "-c", code.format(root=str(ROOT), f=str(tmp))], env=env,
                                      capture_output=True, text=True, timeout=1800)
                return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "ERROR " + proc.stderr[-500:]
            finally:
                tmp.unlink()
        todo = [r for r in wanted if r["digest"] is not None and not r["defects"]]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            others = list(pool.map(replay_one, todo))
        for r, other in zip(todo, others):
            if other != r["digest"]:
                nondet.append({"seed": r["seed"], "opts": r["opts"], "camera": r.get("camera"), "perception": r.get("perception"),
                               "battery": r.get("battery"), "faults": r.get("faults"), "first": r["digest"], "replay": other})
    total_ok = sum(r["ok"] for r in results)
    total = sum(len(r["log"]) for r in results)
    stats, refusals, noops = {}, {}, Counter()
    for r in results:
        _merge(stats, r.get("stats") or {})
        noops.update(r.get("noops") or {})
        for name, msgs in (r.get("refusals") or {}).items():
            refusals.setdefault(name, Counter()).update(msgs)
    actions, summary, digest = coverage_report(stats, refusals, [n for n in ACTIONS], noops)
    print("houses {}  commands {}  succeeded {} ({:.0%})  distinct defects {}  nondeterministic {}{}{}".format(
        len(results), total, total_ok, total_ok / max(1, total), len(seen), len(nondet),
        "  camera {} perception {}".format(camera, perception or "oracle") if camera or perception else "",
        "  battery {} faults {}".format(battery, faults or "none") if battery is not None or faults else ""))
    print_coverage(summary, actions, digest, limit=25)
    for key, d in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
        print("- [{}x] {} -> {} at {}".format(d["count"], key[0], d["error"], d["where"]))
        print("    repro: seed {} opts {}{} step {} command {}".format(
            d["seed"], json.dumps(d["opts"]), "".join(" {} {}".format(k, d[k]) for k in ("camera", "perception", "battery",
                                                                                       "faults") if d.get(k) is not None),
            d["step"], json.dumps(d["command"])[:300]))
    for n in nondet:
        print("- NONDETERMINISTIC replay: seed {} opts {} digest {} vs {}".format(n["seed"], json.dumps(n["opts"]), n["first"][:12], n["replay"][:60]))
    if out:
        Path(out).write_text(json.dumps({"defects": list(seen.values()), "nondeterministic": nondet, "actions": actions,
                                         "coverage": summary, "refusals": digest}, indent=1, default=str),
                             encoding="utf-8")
    return 1 if seen or nondet else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
