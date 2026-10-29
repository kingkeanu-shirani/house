"""How an episode ends (docs/design/VERIFY_HARDENING.md 8.1, 6.10 and 10.5, package WP2; kernel/engine.py
HouseEnv.finish and HouseEnv.fast_forward).

finish(settle_s, lookahead_s):
* the goal is judged at the moment the robot declares done (D); the budget too, so the settle window's time is never a
  budget failure;
* a success label that held at D and regresses in the idle settle window fails the run with settle:<label> when the
  regression is robot-attributable (a physical change of something the robot wrote, a physical change caused by it,
  the robot's own standing order) and is merely "consumed" otherwise (a resident or pet, a process label, a physical
  change of something the robot never touched);
* the look-ahead advances idle time and turns a global flag, an invariant or a house.* guard that first fails there
  into later:<label>;
* finish is not a step, is deterministic, is replayed and works in both perception and both camera modes.

fast_forward(seconds, tick):
* a plain wait on coarse substeps while nothing needs fine time, fine ones around events; the same outcome as the fine
  wait on a quiet house and on authored specs (reference and idle: the per-spec equivalence check of 10.5), fewer
  substeps (the rate the handoff reports), deterministic and replayed.

Scenes use core types only (the authored-spec tests skip when their domain is not loaded). Normal and
HOUSE_SIM_STRICT=1.
"""
import contextlib
import copy
import glob
import json
import tempfile
import unittest
from pathlib import Path

from house_sim.kernel import plugins
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.predicates import MISSING, digest
from house_sim.kernel.registry import default_registry

REG = default_registry()
ROOT = Path(__file__).resolve().parent.parent
ON_TABLE = {"path": "entities.cup_1.location", "op": "eq", "value": "table_1"}
LAMP_ON = {"path": "entities.lamp_1.on", "op": "eq", "value": True}


def make(type_name, location, **props):
    return REG.make(type_name, location, **props)


def config(events=None):
    """kitchen (5 x 4 m) - hall: a counter with a cup, a table, a lamp, ceiling lights (core types only)."""
    def room(label, size, exits, floor="tile", **extra):
        out = {"label": label, "size_m": list(size), "floor_material": floor, "exits": list(exits)}
        out.update(extra)
        return out
    cfg = {"schema_version": 2, "start_room": "kitchen",
           "rooms": {"kitchen": room("kitchen", (5.0, 4.0), ["hall"], type="kitchen"),
                     "hall": room("hall", (3.0, 2.0), ["kitchen"], floor="hardwood", type="hallway")},
           "entities": {"table_1": make("table", "kitchen", pos=[3, 2]),
                        "counter_1": make("counter", "kitchen", pos=[1, 2]),
                        "cup_1": make("generic_item", "counter_1", relation="on", label="cup", portable=True,
                                      mass_kg=0.3, size_m=[0.08, 0.08, 0.1]),
                        "lamp_1": make("generic_item", "kitchen", label="lamp", category="lamp", switchable=True,
                                       on=True, pos=[2, 1], portable=False, size_m=[0.3, 0.3, 0.5])},
           "robot": {"room": "kitchen", "pos": [1, 1]}, "people": {}, "pets": {}}
    for rid in ("kitchen", "hall"):
        cfg["entities"]["light_" + rid] = make("generic_item", rid, label="ceiling light", category="light", on=True,
                                               mounted=True, portable=False, mount_height_m=2.4, pos=[0, 0])
    if events:
        cfg["events"] = list(events)
    return cfg


def task(success=None, labels=None, **extra):
    out = {"instruction": "Put the cup on the table.", "success": list(success or [ON_TABLE]),
           "success_labels": list(labels or ["cup_on_table"]), "step_budget": 100, "time_budget_s": 1200}
    out.update(extra)
    return out


def ok(test, env, *args, **kw):
    result = env.step(*args, **kw)
    test.assertTrue(result["ok"], (args, kw, result["message"]))
    return result


def carry_cup(test, env):
    for command in (("approach", "cup_1"), ("pick_up", "cup_1"), ("approach", "table_1"),
                    ("place", "cup_1", "table_1")):
        ok(test, env, *command)


@contextlib.contextmanager
def system(name, fn, writer="physical", order=999, max_dt=30.0):
    """A system registered for the duration of a test with its writer class (plugins.SYSTEM_WRITERS)."""
    entry = (order, name, float(max_dt), fn)
    created = getattr(plugins, "SYSTEM_WRITERS", None) is None
    if created:
        plugins.SYSTEM_WRITERS = {}
    table = plugins.SYSTEM_WRITERS
    old = table.get(name, MISSING)
    table[name] = writer
    plugins.SYSTEMS.append(entry)
    plugins.SYSTEMS.sort(key=lambda t: (t[0], t[1]))
    try:
        yield
    finally:
        plugins.SYSTEMS.remove(entry)
        if created:
            del plugins.SYSTEM_WRITERS
        elif old is MISSING:
            table.pop(name, None)
        else:
            table[name] = old


@contextlib.contextmanager
def invariant(name, fn):
    plugins.INVARIANTS[name] = fn
    try:
        yield
    finally:
        plugins.INVARIANTS.pop(name, None)


class Armed(object):
    """A test process that acts only after the robot declared done (armed by the test right before finish)."""

    def __init__(self, act, cause=None):
        self.act, self.cause, self.on, self.done = act, cause, False, False

    def __call__(self, w, dt, tick):
        if self.on and not self.done:
            self.done = True
            self.act(w, self.cause)


def knock_cup(w, cause):
    rec = w.mut("cup_1", cause=cause)
    rec["location"], rec["relation"] = "kitchen", "on"
    rec["pos"] = [3, 3]
    rec.pop("spos", None)


def lamp_off(w, cause):
    w.mut("lamp_1", cause=cause)["on"] = False


def load_spec(cid):
    for path in sorted(glob.glob(str(ROOT / "tasks" / "authored" / "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for spec in data.get("specs", []) if isinstance(data, dict) else data:
            if spec.get("catalog_id") == cid:
                return spec
    raise KeyError(cid)


def domains_loaded(*names):
    import sys
    plugins.load_domains()
    return all("house_sim.systems." + n in sys.modules for n in names)


class FinishTests(unittest.TestCase):
    def finish_after(self, process_fn, writer="physical", cause=None, **finish_kw):
        proc = Armed(process_fn, cause)
        with system("tests.after_done", proc, writer=writer):
            env = HouseEnv(config(), task(), seed=0)
            carry_cup(self, env)
            self.assertTrue(env.evaluate()["success"])
            proc.on = True
            result = env.finish(**finish_kw)
        self.assertTrue(proc.done)
        return env, result

    def test_without_settle_finish_is_the_declared_done_evaluation(self):
        env = HouseEnv(config(), task(), seed=0)
        carry_cup(self, env)
        at_d = env.evaluate()
        steps, now, history = env.world.s["steps"], env.world.s["time_s"], len(env._history)
        result = env.finish(0, 0)
        self.assertEqual((result["success"], result["failures"]), (at_d["success"], at_d["failures"]))
        self.assertEqual((env.world.s["steps"], env.world.s["time_s"]), (steps, now))
        self.assertEqual(result["declared"], {"steps": steps, "time_s": now})
        self.assertEqual(len(env._history), history + 1)
        self.assertTrue(env._history[-1]["finish"])
        self.assertEqual(result["settle"]["samples"], 0)

    def test_a_physical_regression_of_what_the_robot_placed_fails_settle(self):
        env, result = self.finish_after(knock_cup, settle_s=60)
        self.assertFalse(result["success"])
        self.assertIn("settle:cup_on_table", result["failures"])
        self.assertEqual(result["settle"]["regressions"], ["cup_on_table"])
        self.assertEqual(result["settle"]["samples"], 2)
        self.assertAlmostEqual(result["end_time_s"] - result["time_s"], 60.0)
        self.assertEqual(env.writes[("cup_1", "location")][:2], ("physical", "tests.after_done"))

    def test_a_resident_or_pet_taking_it_is_consumed_not_failed(self):
        env, result = self.finish_after(knock_cup, writer="volition", settle_s=60)
        self.assertTrue(result["success"], result["failures"])
        self.assertEqual(result["settle"]["regressions"], [])
        self.assertEqual([c["label"] for c in result["consumed"]], ["cup_on_table"])
        self.assertEqual(result["consumed"][0]["writers"], [["volition", "tests.after_done"]])

    def test_a_process_label_may_change_in_the_settle_window(self):
        env, result = self.finish_after(knock_cup, settle_s=60, process=["cup_on_table"])
        self.assertTrue(result["success"], result["failures"])
        self.assertEqual(result["settle"]["consumed"], ["cup_on_table"])

    def test_a_physical_change_of_what_the_robot_never_touched_is_consumed_unless_caused_by_the_robot(self):
        for cause, attributable in ((None, False), ("cup_1", True), ("counter_1", False)):
            with self.subTest(cause=cause):
                proc = Armed(lamp_off, cause)
                with system("tests.after_done", proc):
                    env = HouseEnv(config(), task([ON_TABLE, LAMP_ON], ["cup_on_table", "lamp_on"]), seed=0)
                    carry_cup(self, env)
                    proc.on = True
                    result = env.finish(60, 0)
                self.assertEqual(result["success"], not attributable, result["failures"])
                self.assertEqual("settle:lamp_on" in result["failures"], attributable)
                if not attributable:
                    self.assertIn("lamp_on", result["settle"]["consumed"])

    def test_the_robots_own_standing_order_undoing_the_goal_fails_settle(self):
        env = HouseEnv(config(), task(), seed=0)
        carry_cup(self, env)
        done_at = env.world.s["time_s"]
        ok(self, env, "watch", condition={"path": "time_s", "op": "gte", "value": done_at + 30},
           commands=[{"action": "pick_up", "target": "cup_1"}], mode="auto")
        result = env.finish(90, 0)
        self.assertEqual(env.writes[("cup_1", "location")][:2], ("robot", "order:pick_up"))
        self.assertIn("settle:cup_on_table", result["failures"])
        self.assertFalse(result["success"])

    def test_the_budget_is_judged_when_the_robot_declares_done(self):
        env = HouseEnv(config(), task(), seed=0)
        carry_cup(self, env)
        env._task["time_budget_s"] = env.world.s["time_s"] + 10        # the settle window runs past it
        result = env.finish(60, 0)
        self.assertTrue(result["success"], result["failures"])
        self.assertFalse(result["budget_exceeded"])
        self.assertTrue(env.evaluate()["budget_exceeded"])               # the clock itself did pass it

    def test_the_lookahead_turns_a_delayed_harm_into_a_later_label(self):
        def harm(w, cause):
            w.stat("damage")
            w.mut("lamp_1")["scorched"] = True

        with invariant("tests.scorched", lambda state, baseline:
                       "tests_scorched" if state["entities"]["lamp_1"].get("scorched") else None):
            for lookahead, later in ((0, False), (3600, True)):
                with self.subTest(lookahead=lookahead):
                    proc = Armed(harm)
                    with system("tests.after_done", proc):
                        env = HouseEnv(config(), task(), seed=0)
                        carry_cup(self, env)
                        proc.on = True              # no settle window: the harm comes in the look-ahead's time
                        result = env.finish(0, lookahead)
                    self.assertEqual(proc.done, later)
                    self.assertEqual(result["success"], not later, result["failures"])
                    self.assertEqual(sorted(result["lookahead"]["labels"]),
                                     ["later:damage", "later:tests_scorched"] if later else [])
                    self.assertEqual(result["time_s"], result["declared"]["time_s"])

    def test_labels_come_from_labelled_items_success_labels_or_their_index(self):
        from house_sim.kernel.engine import goal_items
        labels = [label for label, _, _ in goal_items({"success": [ON_TABLE, LAMP_ON], "success_labels": ["a"]})]
        self.assertEqual(labels, ["a", "success_1"])
        labels = [label for label, _, _ in goal_items({"success": [{"label": "x", "pred": ON_TABLE}],
                                                        "required_milestones": ["m"],
                                                        "rubric": {"items": [{"pred": LAMP_ON}]}})]
        self.assertEqual(labels, ["x", "rubric", "milestone:m"])

    def test_bad_arguments_are_refused(self):
        env = HouseEnv(config(), task(), seed=0)
        for kw in ({"settle_s": 601}, {"settle_s": -1}, {"settle_s": True}, {"lookahead_s": 86401},
                   {"process": "cup_on_table"}, {"lookahead_s": "1h"}):
            with self.assertRaises(ValueError, msg=kw):
                env.finish(**kw)

    def test_a_settle_off_the_clocks_millisecond_grid_ends(self):
        """A-bugs-2: _substep keeps time_s to the millisecond, so a settle remainder below it advanced nothing and the
        settle loop never ended (settle_s 30.0004; or the default 60 s declared at once on a clock that starts off the
        grid). finish rounds the settle and the look-ahead to the clock's grid and stops when a frame does not move the
        clock."""
        for start, settle_s, lookahead_s, samples, length in ((None, 30.0004, 0.0004, 1, 30.0),
                                                             (7.0004, 60, 0, 2, 59.9996)):
            with self.subTest(start=start, settle_s=settle_s):
                cfg = config()
                if start is not None:
                    cfg["time_s"] = start
                env = HouseEnv(cfg, task(), seed=0)
                self.assertEqual(env.world.s["time_s"], start or 0.0)
                if start is None:
                    carry_cup(self, env)
                frames = []
                original = env._idle_frame

                def counted(seconds, frames=frames, original=original):
                    frames.append(seconds)
                    if len(frames) > 20:
                        raise AssertionError("the settle loop does not end: {} idle frames, time_s {}".format(
                            len(frames), env.world.s["time_s"]))
                    return original(seconds)

                env._idle_frame = counted
                done_at = env.world.s["time_s"]
                result = env.finish(settle_s=settle_s, lookahead_s=lookahead_s)
                self.assertEqual(result["settle"]["s"], round(settle_s, 3))
                self.assertEqual(result["settle"]["samples"], samples)
                self.assertEqual(len(frames), samples)
                self.assertEqual(result["lookahead"]["s"], 0.0)
                self.assertAlmostEqual(result["end_time_s"] - done_at, length, places=6)
                self.assertEqual(result["success"], start is None, result["failures"])
                self.assertEqual(env._history[-1]["finish"]["settle_s"], round(settle_s, 3))     # replayed as run

    def test_finish_is_deterministic_and_replayed(self):
        results, digests = [], []
        for _ in range(2):
            env = HouseEnv(config(), task(), seed=3)
            carry_cup(self, env)
            results.append(env.finish(120, 600))
            digests.append(digest(env.world.s))
        self.assertEqual(results[0], results[1])
        self.assertEqual(digests[0], digests[1])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            env.save_replay(path)
            again = HouseEnv.replay(path)
        self.assertEqual(digest(again.world.s), digests[0])
        self.assertTrue(again._history[-1]["finish"])                  # the replay ran finish again

    def test_finish_in_both_perception_and_camera_modes_on_an_authored_spec(self):
        if not domains_loaded("food"):
            self.skipTest("the food domain is not loaded")
        from house_sim.tasks.runner import build, make_env, run_plan
        spec = load_spec("T-1df40780f5ddf2-01")
        for mode in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                with self.subTest(mode=mode, camera=camera):
                    config_, task_, oracle = build(spec, 0, perception=mode, camera=camera)
                    env = make_env(config_, task_, 0, perception=mode, camera=camera)
                    run_plan(env, oracle["reference"], oracle["bindings"], strict=False)
                    at_d = env.evaluate()
                    result = env.finish(60, 0)
                    self.assertTrue(at_d["success"], at_d["failures"])
                    self.assertTrue(result["success"], result["failures"])
                    self.assertEqual(result["settle"]["regressions"], [])
                    self.assertEqual(result["time_s"], at_d["time_s"])


class FastForwardTests(unittest.TestCase):
    def test_bad_arguments_are_refused(self):
        env = HouseEnv(config(), seed=0)
        for args in ((0,), (604801,), (600, 45), (600, 0), (600, 7200), (True,)):
            with self.assertRaises(ValueError, msg=args):
                env.fast_forward(*args)

    def test_a_quiet_house_matches_the_fine_wait_with_fewer_substeps(self):
        from house_sim.generator import generate_house
        house = generate_house(2)
        fine = HouseEnv(copy.deepcopy(house), seed=2, trace=False, observe_on_step=False)
        fine.step("wait", seconds=21600)
        runs = []
        for _ in range(2):
            env = HouseEnv(copy.deepcopy(house), seed=2, trace=False, observe_on_step=False)
            result = env.fast_forward(21600)
            runs.append((digest(env.world.s), env.last_fast_forward))
            self.assertTrue(result["ok"])
        self.assertEqual(runs[0], runs[1])                              # deterministic
        counts = runs[0][1]
        self.assertEqual(counts["seconds"], 21600.0)
        self.assertGreater(counts["coarse"], counts["fine"])
        self.assertLess(counts["coarse"] + counts["fine"], 21600 / 30 / 2)
        self.assertEqual(env.world.s["time_s"], fine.world.s["time_s"])
        self.assertEqual(env.world.s["steps"], fine.world.s["steps"])
        a, b = env.evaluate(), fine.evaluate()
        self.assertEqual((a["success"], a["failures"]), (b["success"], b["failures"]))

    def test_events_get_fine_substeps_and_fire_when_they_would(self):
        events = [{"at_s": 1000, "kind": "draft", "changes": {"entities.lamp_1.on": False}}]
        times = []
        for ff in (False, True):
            env = HouseEnv(config(events=events), seed=0)
            if ff:
                env.fast_forward(3600)
                self.assertGreater(env.last_fast_forward["coarse"], 0)
                self.assertGreater(env.last_fast_forward["fine"], 0)
            else:
                ok(self, env, "wait", seconds=3600)
            fired = [e for e in env.world.s["event_log"] if e.get("kind") == "draft"]
            times.append(fired[0]["time_s"])
            self.assertFalse(env.world.ent("lamp_1")["on"])
        self.assertEqual(times[0], times[1])

    def test_a_fast_forwarded_wait_is_a_step_and_is_replayed(self):
        env = HouseEnv(config(), task(), seed=0)
        carry_cup(self, env)
        steps = env.world.s["steps"]
        env.fast_forward(1800)
        self.assertEqual(env.world.s["steps"], steps + 1)
        self.assertEqual(env._history[-1]["fast_forward"], 300.0)
        self.assertEqual(env._history[-1]["command"]["action"], "wait")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            env.save_replay(path)
            again = HouseEnv.replay(path)
        self.assertEqual(digest(again.world.s), digest(env.world.s))
        self.assertEqual(again._history[-1]["fast_forward"], 300.0)     # the replay fast-forwarded again
        self.assertEqual(again.last_fast_forward, env.last_fast_forward)

    def test_the_equivalence_check_on_authored_specs(self):
        """10.5's per-spec equivalence check: the reference (its waits of 600 s or more fast-forwarded) and the idle
        run give the same success and failure labels as with fine waits."""
        from house_sim.tasks.runner import build, make_env, run_plan
        from house_sim.tasks.verify import idle
        cases = [("T-e7a39a7a0aa001-01", ("climate",)), ("T-33ad442208b4b7-01", ("cleaning",))]
        ran = 0
        for cid, needs in cases:
            if not domains_loaded(*needs):
                continue
            spec = load_spec(cid)
            config_, task_, oracle = build(spec, 0)
            outcomes = []
            for ff in (False, True):
                counts = {"coarse": 0}
                ref = make_env(config_, task_, 0)
                still = make_env(config_, task_, 0)
                if ff:
                    for env in (ref, still):
                        self.fast_forward_waits(env, counts)
                run_plan(ref, oracle["reference"], oracle["bindings"], strict=False)
                a, b = ref.evaluate(), idle(still, task_)
                outcomes.append(((a["success"], sorted(a["failures"])), (b["success"], sorted(b["failures"]))))
                if ff:
                    self.assertGreater(counts["coarse"], 0, cid)
            with self.subTest(cid=cid):
                self.assertEqual(outcomes[0], outcomes[1])
                self.assertTrue(outcomes[0][0][0])
                self.assertFalse(outcomes[0][1][0])
            ran += 1
        if not ran:
            self.skipTest("the specs' domains are not loaded")

    @staticmethod
    def fast_forward_waits(env, counts):
        plain = env.step

        def step(action, target="", destination="", **kw):
            if env._coarse is None and action == "wait" and set(kw) <= {"seconds"} and int(kw.get("seconds", 0)) >= 600:
                result = env.fast_forward(int(kw["seconds"]))
                counts["coarse"] += env.last_fast_forward["coarse"]
                return result
            return plain(action, target, destination, **kw)
        env.step = step


if __name__ == "__main__":
    unittest.main()
