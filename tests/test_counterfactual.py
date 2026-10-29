"""The idle counterfactual (VERIFY_HARDENING 6.2, WP1): projections of the house an idle robot left alone, their
checkpoints and brackets, divergence with tolerances and interpolated meters, idle rollouts (deterministic, the same
in both perception modes and both camera modes), and the ledger charging only what diverges from idle."""
import copy
import json
import unittest

from house_sim.kernel import counterfactual, ledger
from house_sim.kernel.counterfactual import Projections, diverges, interval_for, project, rollout, value_at
from house_sim.kernel.engine import HouseEnv
from house_sim.world import make_house, object_record


def house():
    c = make_house(3, 1, 3)
    c["goal"] = None
    c["entities"]["cup"] = object_record("cup", "room_1", portable=True, mass_kg=0.3, size_m=[0.1, 0.1, 0.1],
                                         pos=[1, 1])
    c["entities"]["lamp"] = object_record("table lamp", "room_2", category="light", switchable=True, on=False,
                                          pos=[2, 2], size_m=[0.2, 0.2, 0.4])
    c["entities"]["box"] = object_record("box", "room_1", portable=True, accepts_items=True, openable=True,
                                         open=False, mass_kg=1, capacity_l=20, container=True, size_m=[0.3, 0.3, 0.3])
    return c


def hardened(**block):
    task = {"success": [{"path": "entities.cup.location", "op": "eq", "value": "room_2"}], "step_budget": 400,
            "time_budget_s": 7200}
    block.setdefault("envelope", {})
    task["hardening"] = block
    return task


def checkpoint(t, ents=None, meters=None, agents=None, rooms=None, mess=None):
    return {"t": t, "ents": ents or {}, "rooms": rooms or {}, "mess": mess or {}, "meters": meters or {},
            "agents": agents or {}}


class ProjectionTests(unittest.TestCase):
    def test_a_projection_holds_placements_watched_fields_rooms_meters_and_agents(self):
        env = HouseEnv(house(), None, 3)
        state = env.world.s
        state.setdefault("climate", {})["energy"] = {"kwh": 1.5, "gas_kwh": 0.25}
        p = project(state)
        self.assertEqual(p["t"], float(state["time_s"]))
        self.assertEqual(set(p["ents"]), set(state["entities"]))
        cup = p["ents"]["cup"]
        self.assertEqual(cup["p"], ["room_1", state["entities"]["cup"]["relation"], None])     # portable
        self.assertEqual(p["ents"]["lamp"]["p"], ["room_2", [8, 8]])                       # fixed: pos in 0.25 m
        self.assertIn("on", p["ents"]["lamp"]["f"])                                         # a setting
        self.assertNotIn("label", cup["f"])                                                 # identity: not kept
        self.assertIn("broken", cup["f"])                                                   # condition
        self.assertNotIn("temperature_c", cup["f"])                                         # process
        self.assertNotIn("location", cup["f"])                                              # placement is "p"
        self.assertEqual(p["meters"]["energy"], 1.5)
        self.assertEqual(p["meters"]["gas"], 0.25)
        self.assertEqual(p["meters"]["water"], 0.0)                                         # no meter yet
        self.assertEqual(set(p["rooms"]), set(state["rooms"]))
        self.assertEqual(p["agents"]["resident_1"]["room"], "room_1")
        self.assertIn("room_2", p["agents"]["resident_1"]["reach"])
        json.dumps(p)                                                                       # JSON-safe

    def test_projection_is_a_pure_function_of_the_state(self):
        env = HouseEnv(house(), None, 3)
        a = project(env.world.s)
        b = project(copy.deepcopy(env.world.s))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(env.world.s, HouseEnv(house(), None, 3).world.s)                    # nothing written


class ProjectionsTests(unittest.TestCase):
    def setUp(self):
        self.idle = Projections([checkpoint(600, {"cup": {"p": ["room_2", "on", None], "f": {"on": True}, "s": 0}},
                                            meters={"energy": 2.0}),
                                 checkpoint(0, {"cup": {"p": ["room_1", "on", None], "f": {"on": False}, "s": 0}},
                                            meters={"energy": 1.0}),
                                 checkpoint(1200, {}, meters={"energy": 2.0})])

    def test_checkpoints_are_sorted_and_bracket_any_time(self):
        self.assertEqual(self.idle.times, [0.0, 600.0, 1200.0])
        lo, hi = self.idle.bracket(300)
        self.assertEqual((lo["t"], hi["t"]), (0, 600))
        lo, hi = self.idle.bracket(600)
        self.assertIs(lo, hi)
        self.assertEqual(self.idle.bracket(-5)[0]["t"], 0)
        self.assertEqual(self.idle.bracket(99999)[1]["t"], 1200)

    def test_a_checkpoint_at_the_same_time_replaces_the_earlier_one(self):
        self.idle.add(checkpoint(600, {}, meters={"energy": 9.0}))
        self.assertEqual(len(self.idle), 3)
        self.assertEqual(value_at(self.idle, ("meter", "energy"), 600), 9.0)

    def test_round_trip_and_digest(self):
        raw = json.loads(json.dumps(self.idle.to_dict()))
        again = Projections.from_dict(raw)
        self.assertEqual(again.digest(), self.idle.digest())
        self.assertEqual(again.times, self.idle.times)
        with self.assertRaises(ValueError):
            Projections.from_dict({"checkpoints": []})
        other = Projections([checkpoint(0, {}, meters={"energy": 1.0})])
        self.assertNotEqual(other.digest(), self.idle.digest())

    def test_divergence_needs_both_bracketing_checkpoints_to_differ(self):
        key = ("field", "cup", "on")
        self.assertFalse(diverges((key, False), self.idle, 300))        # equal to the earlier checkpoint
        self.assertFalse(diverges((key, True), self.idle, 300))         # equal to the later one
        self.assertTrue(diverges((key, "other"), self.idle, 300))
        place = ("place", "cup")
        self.assertFalse(diverges((place, ("room_2", "on", None)), self.idle, 600))
        self.assertTrue(diverges((place, ("room_3", "on", None)), self.idle, 600))
        self.assertFalse(diverges((("exists", "cup"), True), self.idle, 300))
        self.assertTrue(diverges((("exists", "cup"), True), self.idle, 1200))     # gone in idle by then
        self.assertTrue(diverges((key, False), None, 300))                        # no rollout: nothing to compare

    def test_meters_are_interpolated_with_a_tolerance(self):
        meter = ("meter", "energy")
        self.assertAlmostEqual(value_at(self.idle, meter, 300), 1.5)
        self.assertFalse(diverges((meter, 1.5005), self.idle, 300))
        self.assertTrue(diverges((meter, 1.6), self.idle, 300))
        self.assertTrue(diverges((meter, 1.0), self.idle, 300))          # the earlier checkpoint is not enough
        self.assertFalse(diverges((meter, 1.6), self.idle, 300, tol=0.2))

    def test_numbers_compare_within_the_tolerance(self):
        idle = Projections([checkpoint(0, {"t": {"p": None, "f": {"setpoint_c": 20.0}, "s": 0}})])
        key = ("field", "t", "setpoint_c")
        self.assertFalse(diverges((key, 20.005), idle, 0, tol=0.01))
        self.assertTrue(diverges((key, 20.5), idle, 0, tol=0.01))

    def test_a_number_between_the_bracketing_checkpoints_does_not_diverge(self):
        # a process passes every value between two checkpoints (a plant's health falling from 0.9 to 0.8): the value
        # at t differs from both without diverging -- with WP2-fix's "link unknown" charged, this rule keeps such
        # fields from charging the robot for time passing (also lists of numbers, element by element)
        def plant(health, size):
            return {"plant": {"p": None, "f": {"health": health, "size_m": size}, "s": 0}}
        idle = Projections([checkpoint(0, plant(0.9, [1.0, 2.0])), checkpoint(300, plant(0.8, [1.2, 2.0]))])
        key = ("field", "plant", "health")
        self.assertFalse(diverges((key, 0.85), idle, 150))
        self.assertFalse(diverges((key, 0.7995), idle, 150, tol=0.001))
        self.assertTrue(diverges((key, 0.6), idle, 150))
        self.assertTrue(diverges((key, 0.95), idle, 150))
        size = ("field", "plant", "size_m")
        self.assertFalse(diverges((size, [1.1, 2.0]), idle, 150))
        self.assertTrue(diverges((size, [1.1, 2.5]), idle, 150))
        self.assertTrue(diverges((key, "wilted"), idle, 150))                  # not a number: differs from both

    def test_unknown_keys_are_an_error(self):
        with self.assertRaises(KeyError):
            diverges((("nonsense", "x"), 1), self.idle, 0)

    def test_the_interval_is_300_s_or_a_96th_of_the_budget(self):
        self.assertEqual(interval_for(3600), 300.0)
        self.assertEqual(interval_for(96 * 600), 600.0)
        self.assertEqual(interval_for(0), 300.0)


class RolloutTests(unittest.TestCase):
    def _env(self, perception="oracle", camera=None, events=()):
        c = house()
        c["events"] = [copy.deepcopy(e) for e in events]
        return HouseEnv(c, hardened(), 3, perception=perception, camera=camera)

    def test_checkpoints_at_the_start_every_interval_and_the_end(self):
        env = self._env()
        idle = rollout(env, 1000, interval_s=300)
        self.assertEqual(idle.times, [0.0, 300.0, 600.0, 900.0, 1000.0])
        self.assertEqual(idle.meta["interval_s"], 300.0)
        samples = []
        rollout(self._env(), 600, interval_s=300, on_sample=lambda e, p: samples.append(p["t"]))
        self.assertEqual(samples, [0.0, 300.0, 600.0])

    def test_rollouts_are_deterministic(self):
        events = [{"at_s": 400, "changes": {"entities.lamp.on": True}}]
        a = rollout(self._env(events=events), 900, interval_s=300)
        b = rollout(self._env(events=events), 900, interval_s=300)
        self.assertEqual(a.digest(), b.digest())
        self.assertFalse(value_at(a, ("field", "lamp", "on"), 300))
        self.assertTrue(value_at(a, ("field", "lamp", "on"), 600))

    def test_one_rollout_serves_both_perception_modes_and_both_cameras(self):
        digests = set()
        for perception in ("oracle", "realistic"):
            for camera in ("room", "geometric"):
                digests.add(rollout(self._env(perception, camera), 900, interval_s=300).digest())
        self.assertEqual(len(digests), 1)

    def test_every_idle_wait_is_a_whole_number_of_seconds_and_none_is_refused(self):
        # WP2-fix's request: rollout passed float seconds to wait, which refuses them ("seconds must be an integer"),
        # so the clock crawled one refused step per second
        env = self._env()
        calls = []
        step = env.step

        def spy(action, *args, **kwargs):
            result = step(action, *args, **kwargs)
            calls.append((action, kwargs.get("seconds"), result["ok"]))
            return result
        env.step = spy
        idle = rollout(env, 1000, interval_s=300)
        self.assertEqual(idle.times, [0.0, 300.0, 600.0, 900.0, 1000.0])
        self.assertEqual(calls, [("wait", 300, True), ("wait", 300, True), ("wait", 300, True), ("wait", 100, True)])

    def test_an_idle_wait_that_does_not_advance_is_an_error(self):
        class Stuck(object):
            def __init__(self, env):
                self.world = env.world

            def step(self, *args, **kwargs):
                return {"ok": False}
        with self.assertRaises(RuntimeError):
            rollout(Stuck(self._env()), 600, interval_s=300)


class _StubWorld(object):
    """A real world whose env reference has no writer log (the section-25 stub decides) but may carry an idle
    rollout; everything else is the world's."""

    def __init__(self, world, idle=None):
        class Env(object):
            pass
        self._world = world
        self.env = Env()
        self.env.idle = idle

    def _env_ref(self):
        return self.env

    def __getattr__(self, name):
        return getattr(self._world, name)


class LedgerAgainstIdleTests(unittest.TestCase):
    """The ledger charges an unattributed change only when it diverges from idle (the WP2 stub), and never one the
    writer log attributes to volition (a scheduled event)."""

    def _event_run(self):
        events = [{"at_s": 100, "changes": {"entities.lamp.on": True}}]
        c = house()
        c["events"] = events
        task = hardened(protections={"restore": ["category:light"]})
        idle = rollout(HouseEnv(copy.deepcopy(c), task, 3), 600, interval_s=300)
        env = HouseEnv(copy.deepcopy(c), task, 3)
        env.step("wait", seconds=600)
        self.assertTrue(env.world.s["entities"]["lamp"]["on"])
        return task, idle, env

    def test_a_change_the_idle_house_makes_too_is_not_charged(self):
        task, idle, env = self._event_run()
        state, base = env.world.s, env._baseline
        self.assertIn("house.changed:lamp.on", ledger.failures(task, state, base))            # stub, no idle: charged
        self.assertNotIn("house.changed:lamp.on", ledger.failures(task, state, base, idle=idle))

    def test_the_writer_log_never_charges_a_scheduled_event(self):
        task, idle, env = self._event_run()
        if getattr(env, "writes", None) is None:
            self.skipTest("WP2's writer log is not in this tree")
        self.assertEqual(env.writes[("lamp", "on")][0], "volition")
        self.assertNotIn("house.changed:lamp.on", env.evaluate()["failures"])                 # without any idle

    def test_a_robot_change_the_idle_house_never_makes_is_charged(self):
        c = house()
        task = hardened(protections={"restore": ["category:light"]})
        idle = rollout(HouseEnv(copy.deepcopy(c), task, 3), 600, interval_s=300)
        env = HouseEnv(copy.deepcopy(c), task, 3)
        self.assertTrue(env.step("go_to", "room_2")["ok"])
        self.assertTrue(env.step("approach", "lamp")["ok"])
        self.assertTrue(env.step("switch_on", "lamp")["ok"])
        labels = ledger.failures(task, env.world.s, env._baseline, env.world, idle)
        self.assertIn("house.changed:lamp.on", labels)
        self.assertIn("house.changed:lamp.on", ledger.failures(task, env.world.s, env._baseline, None, idle))

    def test_env_idle_is_read_when_evaluate_gets_none(self):
        from house_sim.kernel import evaluation
        task, idle, env = self._event_run()
        state, base = env.world.s, env._baseline
        args = (task, state, base, {}, state["steps"])
        self.assertIn("house.changed:lamp.on", evaluation.evaluate(*args, world=_StubWorld(env.world))["failures"])
        self.assertNotIn("house.changed:lamp.on", evaluation.evaluate(*args, world=_StubWorld(env.world, idle))["failures"])
        self.assertNotIn("house.changed:lamp.on", evaluation.evaluate(*args, world=_StubWorld(env.world), idle=idle)["failures"])


if __name__ == "__main__":
    unittest.main()
