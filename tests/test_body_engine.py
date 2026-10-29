"""The robot body wired into the engine (BODY-ENGINE, TIER1_INTEGRATION 2.4 B4; TIER1_EMBODIMENT 10 items 10-18).

What this file pins:

* normalize materializes the body (profile values, legacy faults, the arms_ok mirror, the pack temperature, the power
  state) and the engine registers the body once (system kernel.body 72, invariant robot_stranded, event effects);
* settlement per leaf: every action costs its activity power over its own seconds plus its walking work, nested
  leaves pay for themselves and the parent only for its own seconds, legacy ActionSpec.energy is extra work, waits
  are integrated by the body system (awake, then parked standby; the robot_keep_awake hooks hold it awake);
* truncation: a walk the battery cannot finish stops at the affordable prefix, ok=True with the "powered down"
  message, the robot powers down and is stranded; every action but wait is then refused (PowerOff);
* the partial-commit rule (a chain that powers down keeps what happened), the hidden buffer and the atomic energy
  refusal, chunk independence of waits and charging, quiet mode and robot_sound, the event effects, the Ctx helpers,
  physics reading the body (robot_caps, the carry geometry), both perception modes, the geometric camera, save /
  load / replay, legacy states and the performance budgets of TIER1_INTEGRATION 3.4.

The houses are built by hand (no furnishers, no docks), so the file passes with HOUSE_SIM_DOMAINS=core and with every
domain loaded (where it also shows that navigation_robot no longer drains the battery a second time).
"""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from house_sim.kernel import body, body_faults, camera
from house_sim.kernel.actions import IMAGING_OFF_TEXT, CAMERA_FAULT_TEXT, MIC_FAULT_TEXT, MIC_OFF_TEXT, \
    MIC_PRIVACY_TEXT, PRECISION_TEXT, ActionSpec, action
from house_sim.kernel.engine import SUBSTEP_S, HouseEnv, Tick, actions_fingerprint
from house_sim.kernel.errors import ActionError
from house_sim.kernel.phys import forces, support
from house_sim.kernel.plugins import ACTIONS, AFTER, HOOKS, INVARIANTS, SYSTEMS, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.schema import ROBOT_DEFAULTS

# the domains load once here, outside any test's mock.patch.dict of a plugin table: loaded inside one, their
# registrations would be dropped when the patch restores the table (BODY-FAULTS request 1)
load_domains()

WH = 100.0 / 2400.0                     # % of the humanoid_v1 pack per Wh
P_AWAKE, P_LOOK, P_PHYSICAL, P_MOTION, P_STANDBY = 115.0, 120.0, 155.0, 70.0, 20.0
PER_M = {"careful": 0.264 + 70.0 / 0.4 / 3600.0, "normal": 0.288 + 70.0 / 0.8 / 3600.0}
COLS = 40                               # the hall is 20 m long: 40 cells in a row, the doorway at its far end


def config(robot=None, hall=(20.0, 2.0), stairs=False, entities=None, people=None, events=None, rooms=None):
    """A lit hall (20 x 2 m, robot at [0, 0]) and a lit kitchen behind a doorway (or 12 stairs up) at the hall's far
    end [39, 0]."""
    cols = int(hall[0] / 0.5)
    things = {"hall_light": {"label": "ceiling light", "location": "hall", "category": "light", "on": True,
                             "pos": [0, 3], "mounted": True},
              "kitchen_light": {"label": "ceiling light", "location": "kitchen", "category": "light", "on": True,
                                "pos": [7, 7], "mounted": True}}
    things.update(copy.deepcopy(entities or {}))
    layout = {"hall": {"size_m": list(hall), "exits": ["kitchen"]},
              "kitchen": {"size_m": [4.0, 4.0], "exits": ["hall"], "floor": 1 if stairs else 0}}
    for rid, extra in (rooms or {}).items():
        layout[rid].update(extra)
    return {"schema_version": 2, "start_room": "hall", "rooms": layout,
            "connections": {"hall_kitchen": {"rooms": ["hall", "kitchen"], "kind": "stairs" if stairs else "doorway",
                                             "width_m": 0.9, "steps": 12,
                                             "cells": {"hall": [cols - 1, 0], "kitchen": [0, 0]}}},
            "entities": things, "people": copy.deepcopy(people or {}), "pets": {}, "events": list(events or []),
            "robot": dict({"room": "hall", "pos": [0, 0]}, **copy.deepcopy(robot or {}))}


def make(robot=None, **kw):
    perception = kw.pop("perception", None)
    camera_mode = kw.pop("camera", "room")
    return HouseEnv(config(robot, **kw), perception=perception, camera=camera_mode)


def battery_for(meters, speed="careful"):
    """The battery level (%) that affords exactly `meters` of walking at `speed` (a nearly empty pack walks careful)."""
    return round(100.0 * PER_M[speed] * meters / 2400.0, 9)


def robot(env):
    return env.world.s["robot"]


class BodyTestCase(unittest.TestCase):
    def register(self, name, fn, **kw):
        """A test action, removed after the test."""
        ACTIONS.pop(name, None)
        action(name, **kw)(fn)
        self.addCleanup(ACTIONS.pop, name, None)

    def hooks(self, **named):
        """Replace these hook lists for one test: name=[fn, ...]."""
        patcher = mock.patch.dict(HOOKS, {name: [(100, "test." + name, fn) for fn in fns] for name, fns in named.items()})
        patcher.start()
        self.addCleanup(patcher.stop)


# ----------------------------------------------------------------------
# registration, ActionSpec attributes, normalize

class RegistrationTests(BodyTestCase):
    def test_the_engine_registers_the_body_once(self):
        entries = [s for s in SYSTEMS if s[1] == "kernel.body"]
        self.assertEqual(len(entries), 1)
        order, _, max_dt, fn = entries[0]
        self.assertEqual((order, max_dt, fn), (72, 30.0, body.system_tick))
        self.assertIs(INVARIANTS["robot_stranded"], body.robot_stranded)
        for name, fn in (("robot.battery", body.ev_battery), ("robot.network", body.ev_network),
                         ("robot.fault", body_faults.ev_fault), ("robot.clear_fault", body_faults.ev_clear_fault)):
            self.assertEqual([h[2] for h in HOOKS["event:" + name]], [fn], name)
        self.assertFalse(body.register_plugins())               # idempotent (test_kernel reloads the engine)
        self.assertEqual(len([s for s in SYSTEMS if s[1] == "kernel.body"]), 1)

    def test_action_spec_body_attributes_have_defaults_and_stay_out_of_the_fingerprint(self):
        self.register("_t_plain", lambda ctx: "ok", duration=1.0)
        spec = ACTIONS["_t_plain"]
        self.assertEqual((spec.energy, spec.arms, spec.idle, spec.power_off_ok, spec.noise_db, spec.timing),
                         (None, None, False, False, None, "fixed"))
        wait = ACTIONS["wait"]
        self.assertEqual((wait.idle, wait.power_off_ok), (True, True))
        self.assertNotIn("noise_db", spec.describe())
        before = actions_fingerprint()
        spec.noise_db, spec.arms, spec.timing = 70.0, 2, "speed"
        self.assertEqual(actions_fingerprint(), before)
        self.assertEqual(spec.describe()["noise_db"], 70.0)
        spec.noise_db = lambda ctx: 60.0
        self.assertEqual(spec.describe()["noise_db"], "depends on arguments")
        for bad in ({"arms": 3}, {"timing": "slow"}, {"noise_db": "loud"}, {"arms": True}):
            with self.assertRaises(ValueError):
                ActionSpec("_t_bad", lambda ctx: "", {}, 1.0, None, 0.0, "physical", False, None, False, "", False, **bad)
        # positional construction (as test_kernel does) still works
        legacy = ActionSpec("_t_legacy", lambda ctx: "", {}, 1.0, 0.0, 0.0, "physical", False, None, False, "", False)
        self.assertEqual((legacy.energy, legacy.idle), (0.0, False))


class NormalizeTests(BodyTestCase):
    def test_every_kernel_field_is_materialized_from_the_profile(self):
        env = make()
        state = env.world.s
        materialized = sorted(n for n, e in body.robot_fields().items() if e["materialize"])
        self.assertTrue(set(materialized) <= set(state["robot"]), set(materialized) - set(state["robot"]))
        for key, value in ROBOT_DEFAULTS.items():
            self.assertEqual(state["robot"][key], value, key)
        self.assertEqual((robot(env)["mass_kg"], robot(env)["battery_wh"], robot(env)["power_state"],
                          robot(env)["battery_temp_c"], robot(env)["battery_min_pct"], robot(env)["body_profile"]),
                         (60.0, 2400.0, "on", 21.0, 100.0, "humanoid_v1"))
        self.assertNotIn("facing_deg", state["robot"])          # camera fields only in geometric mode
        compact = make({"body_profile": "humanoid_compact", "max_hand_kg": 5.0})
        self.assertEqual((robot(compact)["mass_kg"], robot(compact)["battery_wh"], robot(compact)["max_mass_kg"],
                          robot(compact)["max_hand_kg"]), (35.0, 1000.0, 10.0, 5.0))

    def test_legacy_faults_and_setup_faults_are_normalized(self):
        env = make({"faults": ["water_ingress", {"kind": "test", "time_s": 12.6}],
                    "fault_state": {"arm_weak_left": {"kind": "arm_weak", "part": "left", "latent": True}}})
        r = robot(env)
        self.assertEqual(r["faults"][0], {"kind": "water_ingress", "time_s": None, "legacy": True})
        self.assertEqual(r["faults"][1], {"kind": "test", "time_s": 12.6})
        self.assertEqual(r["arm_health"], {"left": 0.5, "right": 1.0})
        self.assertEqual(r["arms_ok"], {"left": True, "right": True})
        record = r["fault_state"]["arm_weak_left"]
        self.assertEqual((record["latent"], record["detected"], record["source"]), (True, False, "setup"))

    def test_arms_ok_mirrors_arm_health_and_free_hand_skips_a_failed_arm(self):
        seen = []
        self.register("_t_hand", lambda ctx: seen.append(ctx.free_hand()) or "ok", duration=0.5, category="cognitive")
        env = make({"arms_ok": {"left": False, "right": True}})
        self.assertEqual(robot(env)["arm_health"], {"left": 0.0, "right": 1.0})
        env.step("_t_hand")
        env2 = make({"arm_health": {"left": 1.0, "right": 0.0}})
        self.assertEqual(robot(env2)["arms_ok"], {"left": True, "right": False})
        env2.step("_t_hand")
        env3 = make()
        env3.world.s["robot"]["arms_ok"] = {"left": True, "right": False}  # a domain that still writes only arms_ok
        env3.step("_t_hand")
        self.assertEqual(seen, ["right", "left", "left"])

    def test_the_pack_starts_at_the_start_room_temperature(self):
        self.assertEqual(robot(make(rooms={"hall": {"temperature_c": 4.0}}))["battery_temp_c"], 4.0)
        outdoor = HouseEnv(dict(config(rooms={"hall": {"outdoor": True}}), weather={"condition": "clear",
                                                                                     "temperature_c": -6.0}),
                           camera="room")
        self.assertEqual(robot(outdoor)["battery_temp_c"], -6.0)

    def test_a_robot_that_starts_flat_is_off_but_not_stranded(self):
        env = make({"battery": 0.0})
        self.assertEqual((robot(env)["power_state"], robot(env)["battery_min_pct"]), ("off", 0.0))
        self.assertNotIn("robot_stranded", env.evaluate()["failures"])
        result = env.step("look")
        self.assertEqual((result["ok"], result["message"], result["category"]), (False, body.POWER_OFF_TEXT, "capability"))
        self.assertTrue(env.step("wait", seconds=30)["ok"])
        self.assertNotIn("robot_stranded", env.evaluate()["failures"])


# ----------------------------------------------------------------------
# settlement (test plan 10)

class SettlementTests(BodyTestCase):
    def test_every_action_costs_its_power_over_its_seconds(self):
        env = make()
        env.step("look")
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_LOOK / 3600.0, places=9)
        self.assertAlmostEqual(robot(env)["battery"], 100.0 - P_LOOK / 3600.0 * WH, places=8)
        self.assertAlmostEqual(env.world.s["stats"]["energy"], 100.0 - robot(env)["battery"], places=8)
        env = make()
        self.assertTrue(env.step("speak", text="hello")["ok"])         # 2 s of speech: communication power
        self.assertAlmostEqual(robot(env)["energy_wh_used"], 2 * P_LOOK / 3600.0, places=9)
        env = make({"hands": {"left": None, "right": "bag"}},
                   entities={"bag": {"label": "bag", "location": "robot", "relation": "held", "held_hand": "right",
                                     "portable": True, "mass_kg": 5.0, "size_m": [0.3, 0.2, 0.3]}})
        env.step("look")
        # holding adds 3 W/kg of the load the robot feels (review L3, INT): 5 kg in one 8 kg hand reads "heavy", whose
        # felt load is 0.75 x 8 = 6 kg (proprio.felt_load_kg), so 18 W
        self.assertAlmostEqual(robot(env)["energy_wh_used"], (P_LOOK + 18.0) / 3600.0, places=9)
        self.assertEqual((robot(env)["_held_kg"], robot(env)["_hand_kg"]), (5.0, {"right": ["bag", 5.0]}))

    def test_a_walk_pays_its_work_and_the_hotel_load_once(self):
        env = make()
        self.assertTrue(env.step("move", "kitchen")["ok"])
        meters = 39 * 0.5 + 1.0
        self.assertEqual(env.world.s["time_s"], meters / 0.8)          # core's durations, to the bit
        expected = meters * 0.288 + P_MOTION * (meters / 0.8) / 3600.0
        self.assertAlmostEqual(robot(env)["energy_wh_used"], expected, places=8)
        self.assertAlmostEqual(100.0 - robot(env)["battery"], expected * WH, places=8)   # nobody drains it twice
        self.assertEqual((robot(env)["_activity"], robot(env)["_idle_s"], robot(env)["standby"]), ("active", 0.0, False))

    def test_legacy_energy_is_extra_work(self):
        self.register("_t_scrub", lambda ctx: "scrubbed", duration=3.6, energy=0.02)
        env = make()
        env.step("_t_scrub")
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_PHYSICAL * 3.6 / 3600.0 + 0.02 * 24.0, places=9)

    def test_a_nested_chain_pays_each_leg_once_and_the_parent_only_its_own_seconds(self):
        def chain(ctx):
            for cell in ctx.args["cells"]:
                ctx.env.perform({"action": "walk_to", "cell": cell}, ctx)
            return "walked"
        self.register("_t_chain", chain, params={"cells": "list"}, duration=0.5)
        legs = [[10, 0], [20, 1]]
        alone = make()
        for cell in legs:
            self.assertTrue(alone.step("walk_to", cell=cell)["ok"])
        env = make()
        result = env.step("_t_chain", cells=legs)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(robot(env)["pos"], [20, 1])
        self.assertAlmostEqual(env.world.s["time_s"], alone.world.s["time_s"] + 0.5, places=9)
        self.assertAlmostEqual(robot(env)["energy_wh_used"],
                               robot(alone)["energy_wh_used"] + P_PHYSICAL * 0.5 / 3600.0, places=9)

    def test_waits_are_awake_for_a_minute_then_parked(self):
        env = make()
        env.step("wait", seconds=600)
        self.assertAlmostEqual(robot(env)["energy_wh_used"], (P_AWAKE * 60 + P_STANDBY * 540) / 3600.0, places=7)
        self.assertEqual((robot(env)["standby"], robot(env)["_idle_s"]), (True, 60.0))
        env.step("look")                                                # any action wakes it
        self.assertEqual((robot(env)["standby"], robot(env)["_idle_s"]), (False, 0.0))

    def test_a_keep_awake_hook_holds_off_standby(self):
        self.hooks(robot_keep_awake=[lambda w: True])
        env = make()
        env.step("wait", seconds=600)
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_AWAKE * 600 / 3600.0, places=7)
        self.assertFalse(robot(env)["standby"])

    def test_a_refused_step_costs_an_idle_second(self):
        env = make()
        result = env.step("move", "nowhere")
        self.assertFalse(result["ok"])
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_AWAKE / 3600.0, places=9)


# ----------------------------------------------------------------------
# truncation (11), partial commit (12), the buffer and the energy refusal (13)

class TruncationTests(BodyTestCase):
    def test_a_walk_the_battery_cannot_finish_stops_at_the_prefix_and_strands_the_robot(self):
        env = make({"battery": battery_for(8.0)})
        result = env.step("move", "kitchen")
        self.assertTrue(result["ok"], result["message"])
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(8.0)), result["message"])
        self.assertIn("Battery critical: walking at careful speed", result["message"])
        r = robot(env)
        self.assertEqual((r["room"], r["pos"], r["power_state"], r["battery"], r["posture"]),
                         ("hall", [16, 0], "off", 0.0, "crouching"))
        self.assertEqual(env.world.s["time_s"], 8.0 / 0.4)
        stats = env.world.s["stats"]
        self.assertEqual((stats["power_downs"], stats["power_downs_off_dock"], stats["invalid"]), (1, 1, 0))
        self.assertIn("robot_stranded", env.evaluate()["failures"])
        self.assertEqual(r["body_log"]["power_down_s"], 0.0)
        self.assertIn("power_down", [e["kind"] for e in env.world.s["event_log"]])
        self.assertEqual(env.world.s["messages"][-1]["text"], "Robot powered down: battery depleted")
        for command in ({"action": "look"}, {"action": "move", "target": "kitchen"}, {"action": "speak", "text": "hi"},
                        {"action": "set_quiet", "target": "on"}, {"action": "crouch"}):
            refused = env.step(**command)
            self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                             (False, body.POWER_OFF_TEXT, "capability"), command)
        self.assertTrue(env.step("wait", seconds=60)["ok"])
        self.assertEqual(robot(env)["battery"], 0.0)                    # nothing drains while off
        with self.assertRaises(ActionError) as caught:                 # powered down the robot senses nothing
            env.affordances("hall_light")                               # (BODY-FAULTS, TIER1_INTEGRATION 1.3)
        self.assertEqual(str(caught.exception), "That is not visible here")

    def test_approach_and_walk_to_are_cut_the_same_way(self):
        box = {"box": {"label": "box", "location": "hall", "pos": [39, 1], "portable": True, "mass_kg": 1.0}}
        env = make({"battery": battery_for(5.0)}, entities=box)
        result = env.step("approach", "box")
        self.assertTrue(result["ok"])
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(5.0)), result["message"])
        self.assertEqual((robot(env)["pos"], robot(env)["power_state"]), ([10, 0], "off"))
        env = make({"battery": battery_for(3.0)})
        result = env.step("walk_to", cell=[30, 0])
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(3.0)), result["message"])
        self.assertEqual((robot(env)["pos"], robot(env)["power_state"]), ([6, 0], "off"))

    def test_the_robot_never_starts_a_flight_it_cannot_finish(self):
        env = make({"battery": round(10.0 * WH, 9)}, stairs=True)       # 10 Wh: the hall yes, 14.4 Wh of stairs no
        result = env.step("move", "kitchen")
        self.assertTrue(result["ok"])
        self.assertEqual((robot(env)["room"], robot(env)["pos"], robot(env)["power_state"]), ("hall", [39, 0], "off"))
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(19.5)), result["message"])
        full = make(stairs=True)
        self.assertTrue(full.step("move", "kitchen")["ok"])
        self.assertEqual(robot(full)["room"], "kitchen")
        self.assertAlmostEqual(full.world.s["time_s"], 20.5 / 0.8 + 0.8 * 12, places=9)
        self.assertAlmostEqual(robot(full)["energy_wh_used"],
                               20.5 * 0.288 + 12 * 1.2 + P_MOTION * (20.5 / 0.8 + 9.6) / 3600.0, places=8)

    def test_a_domain_stop_before_the_battery_cut_keeps_the_robot_on_and_the_message_true(self):
        # B4 merge gate: ctx.motion cuts the walk at 8 m and walk_to returns the battery-cut text; an after-hook then
        # stops the robot after 3 m with ctx.cut_motion (as navigation's traverse does at a blocker or a fall). The
        # robot pays for 3 m and stays on, so the message must not claim a power-down that never came
        def stop_early(ctx, result):
            ctx.w.robot_m()["pos"] = [6, 0]
            ctx.cut_motion(3.0, "stopped")
        patcher = mock.patch.dict(AFTER, {"walk_to": list(AFTER.get("walk_to", [])) + [(999, "test.stop", stop_early)]})
        patcher.start()
        self.addCleanup(patcher.stop)
        for perception in (None, "realistic"):
            env = make({"battery": battery_for(8.0)}, perception=perception)
            result = env.step("walk_to", cell=[30, 0])
            self.assertTrue(result["ok"], result["message"])
            self.assertTrue(result["message"].startswith(body.STOPPED_FIRST_TEXT.format(3.0) + " | "), result["message"])
            self.assertNotIn("powered down", result["message"])
            r = robot(env)
            self.assertEqual((r["pos"], r["power_state"]), ([6, 0], "on"))
            self.assertNotIn("robot_stranded", env.evaluate()["failures"])
            # 3 m of careful walking work plus the hotel load over the step's seconds (the 8 m prefix's 20 s)
            self.assertEqual(env.world.s["time_s"], 8.0 / 0.4)
            self.assertAlmostEqual(r["energy_wh_used"], 3.0 * 0.264 + P_MOTION * 20.0 / 3600.0, places=8)
            self.assertGreater(body.gauge(env.world.s), 0.0)
        # an affordable walk the hook stops says what was walked, not the arrival core rendered before the hook ran
        # (adoption requests navigation_robot 3, safety 8); a battery cut nobody stopped keeps the cut text
        stopped = make().step("walk_to", cell=[30, 0])["message"]
        self.assertTrue(stopped.startswith(body.STOPPED_FIRST_TEXT.format(3.0)), stopped)
        self.assertNotIn("Walked to [30, 0]", stopped)
        patcher.stop()
        result = make({"battery": battery_for(8.0)}).step("walk_to", cell=[30, 0])
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(8.0)), result["message"])
        self.assertTrue(make().step("walk_to", cell=[30, 0])["message"].startswith("Walked to [30, 0]"))   # not cut
        patcher.start()
        self.assertEqual(body.motion_result(type("C", (), {"data": {}})(), {"a": 1}), {"a": 1})

    def test_thresholds_are_crossed_with_events_and_breaches(self):
        env = make({"battery": 20.05})
        env.step("move", "kitchen")                                     # about 0.27 %: low and nothing else
        log = robot(env)["body_log"]
        self.assertIn("battery_low_s", log)
        self.assertNotIn("battery_reserve_s", log)
        self.assertIn("battery_low", [e["kind"] for e in env.world.s["event_log"]])
        env = make({"battery": 15.05})
        env.step("move", "kitchen")
        self.assertEqual(env.world.s["stats"]["battery_reserve_breaches"], 1)
        self.assertLess(robot(env)["battery_min_pct"], 15.0)


class PartialCommitTests(BodyTestCase):
    def setUp(self):
        def chain(ctx):
            for cell in ctx.args["cells"]:
                ctx.env.perform({"action": "walk_to", "cell": cell}, ctx)
            return "walked"
        self.register("_t_legs", chain, params={"cells": "list"}, duration=0.5)

    def test_a_chain_that_powers_down_keeps_what_happened(self):
        env = make({"battery": battery_for(8.0)})
        result = env.step("_t_legs", cells=[[10, 0], [30, 0], [35, 0]])
        self.assertTrue(result["ok"], result["message"])
        self.assertTrue(result["message"].startswith("Powered down partway: " + body.POWER_OFF_TEXT), result["message"])
        self.assertEqual(result["category"], "")
        r = robot(env)
        self.assertEqual((r["room"], r["power_state"], r["battery"]), ("hall", "off", 0.0))
        self.assertTrue(10 < r["pos"][0] < 16 and r["pos"][1] == 0, r["pos"])    # leg 1 kept, leg 2 cut short
        self.assertEqual(env.world.s["stats"]["invalid"], 0)
        walked = (10 + (r["pos"][0] - 10)) * 0.5
        self.assertAlmostEqual(env.world.s["time_s"], walked / 0.4 + 0.2, places=6)   # the nested time only
        self.assertIn("robot_stranded", env.evaluate()["failures"])
        self.assertEqual(env.world.s["facts"]["last_action"]["action"], "_t_legs")
        self.assertEqual(env._undo, [])

    def test_a_partial_commit_lasts_whole_seconds_in_realistic_mode(self):
        env = make({"battery": battery_for(8.0)}, perception="realistic")
        result = env.step("_t_legs", cells=[[10, 0], [30, 0], [35, 0]])
        self.assertTrue(result["ok"])
        self.assertTrue(result["message"].startswith("Powered down partway: "), result["message"])
        self.assertEqual(env.world.s["time_s"], float(int(env.world.s["time_s"])))

    def test_a_refusal_before_any_power_down_still_rolls_back(self):
        env = make()
        result = env.step("_t_legs", cells=[[10, 0], [99, 0]])
        self.assertFalse(result["ok"])
        self.assertEqual(robot(env)["pos"], [0, 0])
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_AWAKE / 3600.0, places=9)   # the idle second only
        self.assertEqual(env.world.s["stats"]["invalid"], 1)


class ShortfallTests(BodyTestCase):
    def setUp(self):
        self.register("_t_work", lambda ctx: "worked", params={"seconds": "number"},
                      duration=lambda ctx: ctx.args["seconds"])

    def test_a_shortfall_within_the_hidden_buffer_completes_then_powers_down(self):
        env = make({"battery": 0.05})                                    # 1.2 Wh; the job needs 2.6 Wh
        result = env.step("_t_work", seconds=60)
        self.assertTrue(result["ok"])
        self.assertIn(body.POWERED_DOWN_NOTE, result["message"])
        self.assertEqual((robot(env)["power_state"], robot(env)["battery"]), ("off", 0.0))

    def test_a_larger_shortfall_is_an_atomic_refusal_that_is_still_counted(self):
        env = make({"battery": 0.05})
        result = env.step("_t_work", seconds=600)                         # 25.8 Wh: more than the 12 Wh buffer
        self.assertEqual((result["ok"], result["message"], result["category"]),
                         (False, body.ENERGY_TEXT, "capability"))
        self.assertEqual((env.world.s["stats"]["energy_refusals"], env.world.s["stats"]["invalid"]), (1, 1))
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_AWAKE / 3600.0, places=9)
        self.assertEqual(robot(env)["power_state"], "on")

    def test_a_flat_but_switched_on_pack_is_cut_off_by_the_bms(self):
        env = make()
        env.world.s["robot"]["battery"] = 0.0                            # a level written directly (legacy domain code)
        result = env.step("approach", "hall_light")
        self.assertEqual((result["ok"], result["message"]), (False, "Robot cannot move"))
        self.assertEqual(robot(env)["power_state"], "off")              # the BMS cut it off in the idle second
        self.assertEqual(env.world.s["stats"]["power_downs"], 1)


# ----------------------------------------------------------------------
# chunk independence (14), charging, the pack window

class ChunkTests(BodyTestCase):
    def test_one_long_wait_equals_many_short_ones(self):
        one = make()
        one.step("wait", seconds=3600)
        many = make()
        for _ in range(120):
            many.step("wait", seconds=30)
        self.assertEqual(one.world.s["time_s"], many.world.s["time_s"])
        self.assertAlmostEqual(robot(one)["battery"], robot(many)["battery"], delta=1e-3)
        self.assertAlmostEqual(robot(one)["battery"], 100.0 - (P_AWAKE * 60 + P_STANDBY * 3540) / 3600.0 * WH,
                               delta=1e-6)

    def test_charging_is_cc_then_cv_and_chunk_independent(self):
        self.hooks(robot_charge_source=[lambda w: 2.0])
        flat = make({"battery": 0.0})
        self.assertEqual(robot(flat)["power_state"], "off")
        flat.step("wait", seconds=2400)                                   # 0 to 80 % in 40 min at 2 %/min
        self.assertEqual((robot(flat)["battery"], robot(flat)["power_state"], robot(flat)["charging"]),
                         (80.0, "on", True))
        self.assertAlmostEqual(robot(flat)["energy_wh_charged"], 1920.0, places=6)
        self.assertIn("power_up", [e["kind"] for e in flat.world.s["event_log"]])
        one = make({"battery": 70.0})
        one.step("wait", seconds=1800)
        many = make({"battery": 70.0})
        for _ in range(60):
            many.step("wait", seconds=30)
        self.assertAlmostEqual(robot(one)["battery"], robot(many)["battery"], delta=1e-3)
        tau = (100.0 - 80.0) / 2.0                                        # minutes; the rate is continuous at 80 %
        self.assertAlmostEqual(robot(one)["battery"], 100.0 - 20.0 * 2.718281828459045 ** (-(30.0 - 5.0) / tau),
                               delta=1e-6)
        one.step("wait", seconds=1800)                                    # past 99.9 % the charger stops: full
        self.assertEqual((robot(one)["battery"], robot(one)["charging"]), (100.0, False))
        self.assertAlmostEqual(robot(one)["energy_wh_used"], 0.0)          # the dock carries the load

    def test_a_frozen_pack_does_not_charge(self):
        self.hooks(robot_charge_source=[lambda w: 2.0])
        env = make({"battery": 50.0, "battery_temp_c": -5.0}, rooms={"hall": {"temperature_c": -5.0}})
        env.step("wait", seconds=60)
        self.assertEqual(robot(env)["battery"], 50.0)
        self.assertIn("charge_refused_temp_s", robot(env)["body_log"])
        full = make({"battery": 100.0, "battery_temp_c": -5.0}, rooms={"hall": {"temperature_c": -5.0}})
        full.step("look")
        full.step("wait", seconds=60)                                     # a full pack needs no charging current
        self.assertNotIn("charge_refused_temp_s", robot(full)["body_log"])
        self.assertEqual(robot(full)["energy_wh_used"], 0.0)              # seated on a live source: the dock pays


# ----------------------------------------------------------------------
# quiet mode and robot_sound (17)

class QuietTests(BodyTestCase):
    def test_quiet_mode_refuses_loud_actions_and_robot_sound_reports_them(self):
        heard = []
        self.hooks(robot_sound=[lambda w, db, kind, room, cell: heard.append((db, kind, room, cell))])
        self.register("_t_loud", lambda ctx: "whirr", duration=2.0, noise_db=70.0)
        self.register("_t_soft", lambda ctx: "hum", duration=2.0, noise_db=40)
        self.register("_t_varies", lambda ctx: "it depends", params={"db": "number"}, duration=1.0,
                      noise_db=lambda ctx: ctx.args["db"])
        env = make()
        self.assertTrue(env.step("_t_loud")["ok"])
        self.assertEqual(heard, [(70.0, "_t_loud", "hall", [0, 0])])
        self.assertTrue(env.step("set_quiet", "on")["ok"])
        result = env.step("_t_loud")
        self.assertEqual((result["ok"], result["message"], result["category"]), (False, body.QUIET_TEXT, "social"))
        self.assertEqual(env.world.s["stats"]["social_blocks"], 1)
        self.assertTrue(env.step("_t_soft")["ok"])
        refused = env.step("_t_varies", db=80.0)                           # a callable level, judged after the handler
        self.assertEqual((refused["ok"], refused["message"]), (False, body.QUIET_TEXT))
        self.assertTrue(env.step("_t_varies", db=50.0)["ok"])
        self.assertEqual([h[1] for h in heard], ["_t_loud", "_t_soft", "_t_varies"])

    def test_nested_leaves_are_guarded_and_heard_too(self):
        heard = []
        self.hooks(robot_sound=[lambda w, db, kind, room, cell: heard.append(kind)])
        self.register("_t_loud", lambda ctx: "whirr", duration=2.0, noise_db=70.0)
        self.register("_t_wrap", lambda ctx: ctx.env.perform({"action": "_t_loud"}, ctx), duration=0.5)
        env = make()
        self.assertTrue(env.step("_t_wrap")["ok"])
        self.assertEqual(heard, ["_t_loud"])
        env.step("set_quiet", "on")
        self.assertEqual(env.step("_t_wrap")["message"], body.QUIET_TEXT)


# ----------------------------------------------------------------------
# event effects

class EventEffectTests(BodyTestCase):
    def test_the_battery_event_sets_the_gauge_and_can_strand_the_robot(self):
        env = make(events=[{"at_s": 60, "effect": "robot.battery", "args": {"level": 50}}])
        env.step("wait", seconds=60)
        self.assertEqual((robot(env)["battery"], robot(env)["battery_min_pct"]), (50.0, 50.0))
        env = make(events=[{"at_s": 30, "effect": "robot.battery", "args": {"level": 0}}])
        env.step("wait", seconds=60)
        self.assertEqual(robot(env)["power_state"], "off")
        self.assertIn("robot_stranded", env.evaluate()["failures"])

    def test_network_and_fault_events(self):
        env = make(events=[{"at_s": 30, "effect": "robot.network", "args": {"ok": False, "until_s": 120}},
                           {"at_s": 30, "effect": "robot.fault", "args": {"kind": "arm_weak", "part": "left",
                                                                           "latent": True}},
                           {"at_s": 90, "effect": "robot.clear_fault", "args": {"kind": "arm_weak", "part": "left"}}])
        env.step("wait", seconds=60)
        r = robot(env)
        self.assertEqual((r["network"], r["arm_health"]["left"]), (False, 0.5))
        self.assertTrue(r["fault_state"]["arm_weak_left"]["latent"])
        env.step("wait", seconds=90)
        r = robot(env)
        self.assertEqual((r["network"], r["arm_health"]["left"]), (True, 1.0))
        self.assertEqual(r["fault_state"]["arm_weak_left"]["cleared_s"], 90.0)
        self.assertIsNotNone(r["fault_state"]["network_down"]["cleared_s"])


# ----------------------------------------------------------------------
# Ctx helpers and the core guards

class UnrecordedMotionTests(BodyTestCase):
    """Stage-B review (body, high): domain verbs (people guide, manipulation drag / co-carry, outdoor_plants mow, ...)
    wrote the robot's room and cell without ctx.motion, so a fallen, legs-failed, unlocalized or wheeled robot guided
    someone upstairs, and the walk and the stairs cost no walking energy. The kernel now makes such a walk the leaf's
    motion record (body.unrecorded_motion) and refuses it when the body could not have walked; a verb that puts the
    robot on a climbing aid needs a body that climbs."""

    def setUp(self):
        def escort(ctx):                       # a domain verb that moves the robot itself (no ctx.motion)
            r = ctx.w.robot_m()
            r["room"], r["pos"] = "kitchen", [2, 2]
            return "escorted"

        def shuffle(ctx):
            ctx.w.robot_m()["pos"] = [10, 0]
            return "shuffled"

        def perch(ctx):
            r = ctx.w.robot_m()
            r["pos"], r["standing_on"], r["posture"] = [1, 0], "stool", "standing"
            return "perched"
        self.register("_t_escort", escort, duration=30.0, category="physical")
        self.register("_t_shuffle", shuffle, duration=12.0, category="physical")
        self.register("_t_perch", perch, duration=3.0, category="physical")

    def test_the_walk_is_paid_for_like_a_walk(self):
        for stairs in (False, True):
            env = make(stairs=stairs)
            result = env.step("_t_escort")
            self.assertTrue(result["ok"], result["message"])
            used = robot(env)["energy_wh_used"]
            walk = body.walk_wh(env.world.s, 19.5 + 1.0 + 2.0 * 2 ** 0.5, "normal", 0.0)
            self.assertGreater(used, walk, stairs)                    # the walking work, not only the hotel load
            if stairs:
                self.assertGreater(used, body.stairs_wh(env.world.s, 12, 0))
        same = make()
        self.assertTrue(same.step("_t_shuffle")["ok"])
        self.assertGreater(robot(same)["energy_wh_used"], body.walk_wh(same.world.s, 5.0, "normal", 0.0))

    def test_a_body_that_could_not_walk_is_refused_and_rolled_back(self):
        cases = (({"fallen": True, "posture": "lying"}, False, "The robot has fallen; recover first"),
                 ({"legs_health": 0.0}, False, body.LEGS_FAILED_TEXT),
                 ({"actuator_ok": False}, False, "Actuator fault; request human assistance"),
                 ({"localized": False}, False, body.LOCALIZATION_TEXT),
                 ({"body_profile": "mobile_manipulator"}, True, body.NO_STAIRS_TEXT))
        for props, stairs, text in cases:
            env = make(props, stairs=stairs)
            result = env.step("_t_escort")
            self.assertEqual((result["ok"], result["message"]), (False, text), props)
            self.assertEqual((robot(env)["room"], robot(env)["pos"]), ("hall", [0, 0]), props)
        wheeled = make({"body_profile": "mobile_manipulator"})               # no stairs: the wheeled base rolls there
        self.assertTrue(wheeled.step("_t_escort")["ok"])

    def test_a_wheeled_base_cannot_climb_onto_an_aid(self):
        stool = {"stool": {"label": "step stool", "location": "hall", "pos": [1, 0], "standing_height_m": 0.23}}
        legged = make(entities=stool)
        self.assertTrue(legged.step("_t_perch")["ok"])
        self.assertEqual(robot(legged)["standing_on"], "stool")
        wheeled = make({"body_profile": "mobile_manipulator"}, entities=stool)
        result = wheeled.step("_t_perch")
        self.assertEqual((result["ok"], result["message"], result["category"]),
                         (False, body.NO_CLIMB_TEXT, "capability"))
        self.assertIsNone(robot(wheeled).get("standing_on"))


class GuardTests(BodyTestCase):
    def check(self, env, command, message, category):
        result = env.step(**command)
        self.assertEqual((result["ok"], result["message"], result["category"]), (False, message, category), command)

    def test_the_motion_verbs_keep_their_wording(self):
        broken = make({"actuator_ok": False})
        self.check(broken, {"action": "move", "target": "kitchen"}, "Robot cannot move; dock, recover or hand off",
                   "capability")
        self.check(broken, {"action": "approach", "target": "hall_light"}, "Robot cannot move", "capability")
        self.check(broken, {"action": "walk_to", "cell": [3, 0]}, "Robot cannot move", "capability")
        fallen = make({"fallen": True, "posture": "lying"})
        self.check(fallen, {"action": "move", "target": "kitchen"}, "Robot cannot move; dock, recover or hand off",
                   "capability")
        on_aid = make({"standing_on": "stool"})
        self.check(on_aid, {"action": "walk_to", "cell": [3, 0]}, "Step down first", "safety")
        lost = make({"localized": False})
        self.check(lost, {"action": "move", "target": "kitchen"}, body.LOCALIZATION_TEXT, "capability")
        legs = make({"legs_health": 0.0})
        self.check(legs, {"action": "walk_to", "cell": [3, 0]}, body.LEGS_FAILED_TEXT, "capability")

    def test_posture_changes_need_a_working_body_but_no_pose(self):
        self.check(make({"actuator_ok": False}), {"action": "crouch"}, "Actuator fault; request human assistance",
                   "capability")
        self.check(make({"fallen": True}), {"action": "kneel"}, "The robot has fallen; recover first", "capability")
        on_aid = make({"standing_on": "stool"})
        self.check(on_aid, {"action": "crouch"}, "Step down first", "safety")
        self.assertTrue(on_aid.step("stand")["ok"])
        self.assertTrue(make({"localized": False}).step("crouch")["ok"])
        self.check(make({"legs_health": 0.0}), {"action": "crouch"}, body.LEGS_FAILED_TEXT, "capability")

    def test_ctx_obj_uses_the_body_with_todays_wording(self):
        cup = {"cup": {"label": "cup", "location": "hall", "pos": [1, 1], "portable": True, "mass_kg": 0.3,
                       "size_m": [0.08, 0.08, 0.1]}}
        self.check(make({"fallen": True}, entities=cup), {"action": "pick_up", "target": "cup"},
                   "The robot has fallen; recover first", "capability")
        self.check(make({"actuator_ok": False}, entities=cup), {"action": "pick_up", "target": "cup"},
                   "Actuator fault; request human assistance", "capability")
        hot = make({"fault_state": {"battery_overheat": {"kind": "battery_overheat"}}}, entities=cup)
        self.check(hot, {"action": "pick_up", "target": "cup"}, body.OVERHEAT_TEXT, "capability")
        self.assertTrue(make({"posture": "crouching"}, entities=cup).step("pick_up", "cup")["ok"])

    def test_the_body_view_is_read_only_and_bound_to_the_live_state(self):
        seen = {}

        def probe(ctx):
            view = ctx.body
            seen.update(cap=view.capacity_kg("right"), gauge=view.gauge(), carried=view.carried_kg(),
                        hear=view.can_hear(), names=dir(view))
            with self.assertRaises(AttributeError):
                view.spend_wh
            return "ok"
        self.register("_t_view", probe, duration=0.5, category="cognitive")
        env = make({"battery": 64.0})
        env.step("_t_view")
        self.assertEqual((seen["cap"], seen["gauge"], seen["carried"], seen["hear"]), (8.0, 64.0, 0.0, "full"))
        self.assertIn("capacity_kg", seen["names"])

    def test_imaging_hearing_grasp_precision_and_lift_helpers(self):
        calls = {}

        def imaging(ctx):
            ctx.require_imaging()
            return "seen"

        def hearing(ctx):
            calls["level"] = ctx.require_hearing(addressed_ok=ctx.args.get("addressed", True))
            return "heard"

        def grasp(ctx):
            calls["grip"] = ctx.require_grasp(ctx.args["target"], "right", ctx.args.get("grip"))
            return "grasped"

        def precise(ctx):
            ctx.require_precision("right", 5.0, "seating the connector")
            return "seated"

        def lift(ctx):
            ctx.check_lift(ctx.args["target"], "right")
            return "lifted"
        for name, fn, params in (("_t_imaging", imaging, None), ("_t_hearing", hearing, {"addressed": "?bool"}),
                                 ("_t_grasp", grasp, {"target": "entity", "grip": "?str"}),
                                 ("_t_precise", precise, None), ("_t_lift", lift, {"target": "entity"})):
            self.register(name, fn, params=params, duration=1.0)
        self.check(make({"privacy_mode": True}), {"action": "_t_imaging"}, IMAGING_OFF_TEXT, "perception")
        self.check(make({"camera_on": False}), {"action": "_t_imaging"}, IMAGING_OFF_TEXT, "perception")
        self.check(make({"sensors": {"camera": False}}), {"action": "_t_imaging"}, CAMERA_FAULT_TEXT, "capability")
        self.assertTrue(make().step("_t_imaging")["ok"])
        self.check(make({"mic_on": False}), {"action": "_t_hearing"}, MIC_OFF_TEXT, "capability")
        self.check(make({"sensors": {"mics": False}}), {"action": "_t_hearing"}, MIC_FAULT_TEXT, "capability")
        privacy = make({"privacy_mode": True})
        self.assertTrue(privacy.step("_t_hearing")["ok"])
        self.assertEqual(calls["level"], "addressed")
        self.check(privacy, {"action": "_t_hearing", "addressed": False}, MIC_PRIVACY_TEXT, "perception")
        things = {"pin": {"label": "pin", "location": "hall", "pos": [1, 1], "portable": True, "mass_kg": 0.001,
                          "size_m": [0.0015, 0.0015, 0.0015]},
                  "bead": {"label": "bead", "location": "hall", "pos": [1, 1], "portable": True, "mass_kg": 0.002,
                           "size_m": [0.01, 0.01, 0.01]},
                  "anvil": {"label": "anvil", "location": "hall", "pos": [1, 1], "portable": True, "mass_kg": 12.0,
                            "size_m": [0.3, 0.2, 0.2]}}
        env = make(entities=things)
        self.check(env, {"action": "_t_grasp", "target": "pin"}, body.TOO_SMALL_TEXT, "capability")
        self.check(env, {"action": "_t_grasp", "target": "bead", "grip": "normal"}, body.POWER_TOO_SMALL_TEXT,
                   "capability")
        result = env.step("_t_grasp", "bead")
        self.assertEqual((result["ok"], calls["grip"]), (True, "pinch"))
        self.assertIn("pinch grasp", result["message"])
        self.check(make({"camera_clean": 0.3}), {"action": "_t_precise"},
                   PRECISION_TEXT.format("seating the connector"), "capability")
        self.assertTrue(make().step("_t_precise")["ok"])
        self.check(env, {"action": "_t_lift", "target": "anvil"}, body.OVERLOAD_TEXT, "safety")
        self.assertEqual(env.world.s["stats"]["overload_attempts"], 1)      # survives the rollback
        self.assertIn("overload_s", robot(env)["body_log"])

    def test_a_refusal_that_revealed_the_size_teaches_it_in_realistic_mode(self):
        self.register("_t_grasp", lambda ctx: ctx.require_grasp(ctx.args["target"], "right", None) or "ok",
                      params={"target": "entity"}, duration=1.0)
        env = make(entities={"pin": {"label": "pin", "location": "hall", "pos": [1, 1], "portable": True,
                                     "mass_kg": 0.001, "size_m": [0.0015, 0.0015, 0.0015]}}, perception="realistic")
        handle = env.realism.to_handles("pin")
        result = env.step("_t_grasp", handle)
        self.assertEqual((result["ok"], result["message"]), (False, body.TOO_SMALL_TEXT))
        from house_sim.kernel.realism import LEVELS
        record = env.world.s["entities"]["pin"]
        self.assertGreaterEqual(env.realism.knowledge.level("pin", record, "size_m"), LEVELS["touch"])


# ----------------------------------------------------------------------
# physics reads the body

class PhysicsReadsTheBodyTests(BodyTestCase):
    def test_robot_caps_come_from_the_body(self):
        env = make()
        self.assertEqual(forces.robot_caps(env.world), {"max_hand_kg": 8.0, "max_mass_kg": 20.0, "push_n": 220.0,
                                                        "pull_n": 180.0, "grip_n": 150.0, "pinch_n": 30.0,
                                                        "wrist_nm": 6.0})
        compact = make({"body_profile": "humanoid_compact", "profile_overrides": {"push_n": 140.0}})
        caps = forces.robot_caps(compact.world)
        self.assertEqual((caps["max_hand_kg"], caps["max_mass_kg"], caps["push_n"], caps["pull_n"]),
                         (4.0, 10.0, 140.0, 100.0))
        on_aid = make({"standing_on": "stool"})
        self.assertEqual(forces.robot_caps(on_aid.world)["max_hand_kg"], 4.0)    # the platform derate
        one_arm = make({"arm_health": {"left": 1.0, "right": 0.0}})
        caps = forces.robot_caps(one_arm.world)
        self.assertEqual((caps["max_hand_kg"], caps["grip_n"]), (8.0, 150.0))
        parallel = make({"body_profile": "mobile_manipulator"})
        self.assertEqual(forces.robot_caps(parallel.world)["pinch_n"], 140.0)

    def test_the_carried_load_rides_at_the_profiles_carry_height(self):
        held = {"jar": {"label": "jar", "location": "robot", "relation": "held", "held_hand": "right", "portable": True,
                        "mass_kg": 1.0, "size_m": [0.1, 0.1, 0.2]}}
        hands = {"hands": {"left": None, "right": "jar"}}
        tall = make(dict(hands), entities=held)
        short = make(dict(hands, body_profile="humanoid_compact"), entities=held)
        self.assertAlmostEqual(support.held_envelope(tall.world.s)["bottom_m"], 1.0 - 0.1)
        self.assertAlmostEqual(support.held_envelope(short.world.s)["bottom_m"], 0.8 - 0.1)
        self.assertEqual(support.carry_geometry(short.world.s), {"hand_z_m": 0.8, "hip_z_m": 0.7, "front_m": 0.3})
        self.assertEqual(support.carry_geometry({"entities": {}}), support.CARRY)


# ----------------------------------------------------------------------
# perception modes and the geometric camera

class ModeTests(BodyTestCase):
    def test_realistic_perception_shows_the_body_refusals_unchanged(self):
        env = make({"battery": battery_for(4.0)}, perception="realistic")
        result = env.step("move", "kitchen")
        self.assertTrue(result["ok"])
        self.assertTrue(result["message"].startswith(body.BATTERY_CUT_TEXT.format(4.0)), result["message"])
        refused = env.step("look")
        self.assertEqual((refused["ok"], refused["message"], refused["category"]),
                         (False, body.POWER_OFF_TEXT, "capability"))
        self.register("_t_work", lambda ctx: "worked", duration=600.0)
        low = make({"battery": 0.05}, perception="realistic")
        self.assertEqual(low.step("_t_work")["message"], body.ENERGY_TEXT)

    def test_a_cut_walk_is_framed_by_the_geometric_camera(self):
        env = make({"battery": battery_for(8.0)}, camera="geometric")
        self.assertTrue(env.step("move", "kitchen")["ok"])
        frames = robot(env)["view"]["frames"]
        motion = [f for f in frames if f[7] == "motion"]
        self.assertTrue(motion)
        self.assertEqual(frames[-1][7], "idle")
        self.assertTrue(all(f[0] == "hall" for f in frames))
        self.assertEqual(robot(env)["power_state"], "off")

    def test_tracking_someone_keeps_the_robot_awake(self):
        people = {"p1": {"label": "resident", "role": "adult", "location": "hall", "pos": [3, 2]}}
        env = make(camera="geometric", people=people)
        env.world.s["robot"]["view"]["track"] = ["p1"]
        self.assertTrue(camera.keep_awake(env.world))
        env.step("wait", seconds=600)
        self.assertFalse(robot(env)["standby"])
        self.assertAlmostEqual(robot(env)["energy_wh_used"], P_AWAKE * 600 / 3600.0, places=7)


# ----------------------------------------------------------------------
# persistence, replay, legacy states (18)

class PersistenceTests(BodyTestCase):
    def test_save_load_and_replay_round_trip_through_a_power_down(self):
        def chain(ctx):
            for cell in ctx.args["cells"]:
                ctx.env.perform({"action": "walk_to", "cell": cell}, ctx)
            return "walked"
        env = make({"battery": battery_for(12.0)})
        self.assertTrue(env.step("walk_to", cell=[4, 0])["ok"])
        self.assertTrue(env.step("look")["ok"])
        self.assertTrue(env.step("move", "kitchen")["ok"])              # cut: powers down
        self.assertFalse(env.step("look")["ok"])
        self.assertTrue(env.step("wait", seconds=120)["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            state_file, replay_file = Path(tmp) / "state.json", Path(tmp) / "replay.json"
            env.save(state_file)
            env.save_replay(replay_file)
            loaded = HouseEnv.load(state_file)
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            for e in (env, loaded):
                e.step("look")
                e.step("wait", seconds=30)
            self.assertEqual(digest(loaded.world.s), digest(env.world.s))
            replayed = HouseEnv.replay(replay_file)
            self.assertEqual(robot(replayed)["power_state"], "off")

    def test_a_legacy_state_without_the_body_fields_still_runs(self):
        env = make()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            env.save(path)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            keep = set(ROBOT_DEFAULTS) | {"room", "pos", "hands"}
            for key in ("state", "baseline", "replay_start"):
                payload[key]["robot"] = {k: v for k, v in payload[key]["robot"].items() if k in keep}
            envelope["sha256"] = digest(payload)
            path.write_text(json.dumps(envelope), encoding="utf-8")
            legacy = HouseEnv.load(path)
        self.assertNotIn("power_state", robot(legacy))
        self.assertTrue(legacy.step("look")["ok"])
        self.assertTrue(legacy.step("move", "kitchen")["ok"])
        self.assertTrue(legacy.step("wait", seconds=120)["ok"])
        self.assertLess(robot(legacy)["battery"], 100.0)
        self.assertNotIn("robot_stranded", legacy.evaluate()["failures"])


# ----------------------------------------------------------------------
# performance budgets (TIER1_INTEGRATION 3.4, TIER1_EMBODIMENT 9; the tests fail at 3x the budget)

def best_of(fn, repeats=5, number=200):
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(number):
            fn()
        took = (time.perf_counter() - start) / number
        best = took if best is None else min(best, took)
    return best


class PerformanceTests(BodyTestCase):
    def test_body_system_settlement_and_motion_prefix_budgets(self):
        env = make()
        w = env.world
        w.begin()
        idle_env = type("E", (), {"_advance_mode": "idle"})()
        tick = Tick(env, 0)
        tick.env = idle_env
        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        per_substep = best_of(lambda: body.system_tick(w, SUBSTEP_S, tick), number=300)
        self.assertLess(per_substep, 3 * 0.02e-3, per_substep)
        self.assertLess(per_substep * 20160, 3 * 0.4)                      # a week-long wait
        covered = type("E", (), {"_advance_mode": "covered"})()
        tick.env = covered
        self.assertLess(best_of(lambda: body.system_tick(w, SUBSTEP_S, tick), number=300), 3 * 0.02e-3)
        segments = [0.5] * 40
        prefix = best_of(lambda: body.affordable_prefix(w.s, segments, "normal", 2.0), number=300)
        self.assertLess(prefix, 3 * 0.05e-3, prefix)
        w.rollback()

    def test_settlement_per_leaf_budget_while_carrying(self):
        from house_sim.kernel.actions import Ctx
        env = HouseEnv(seed=0, camera="room")
        state = env.world.s
        cups = sorted(e for e, r in state["entities"].items() if r.get("portable") and r.get("location") in
                      state["entities"] and not r.get("accepts_items"))[:2]
        env.world.begin()
        for hand, eid in zip(("left", "right"), cups):
            env.world.mut(eid).update(location="robot", relation="held", held_hand=hand)
            env.world.robot_m()["hands"][hand] = eid
        env.world.robot_m()["docked"] = False
        ctx = Ctx(env, ACTIONS["look"], {}, None)
        per_leaf = best_of(lambda: body.settle_energy(env.world, ctx, 1.0), number=100)
        env.world.rollback()
        # a wall-clock bound: asserted in the perf job, recorded in the correctness runs (CI's shared runners measured
        # 0.40-0.59 ms here; tools/orchestration/wallclock.py)
        from tools.orchestration.wallclock import wall_clock_bound
        wall_clock_bound(self, per_leaf, 3 * 0.1e-3, "settlement per leaf while carrying (s)")


if __name__ == "__main__":
    unittest.main()
