"""Robot body model (kernel/body.py, body_faults.py, proprio.py): TIER1_EMBODIMENT sections 3-5 and the library
parts of its test plan (section 10, items 1-9, the settlement and truncation math of 10-14, and the fault, fall,
water and proprioception items of 25-33).

The body calls two physics functions that land in other tier-1 packages (support.subtree_mass and impact.fall);
these tests replace them with small local fakes so they do not depend on those packages.
"""
import copy
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from house_sim.kernel import body, body_faults, geometry, plugins, proprio
from house_sim.kernel.registry import default_registry
from house_sim.kernel.schema import ROBOT_DEFAULTS, normalize
from house_sim.kernel.world import World
from house_sim.world import make_house, object_record

REG = default_registry()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_subtree_mass(state, eid, cmap=None):
    """Physics' subtree-mass rule (mass plus liquid x density over the item and everything in or on it)."""
    entities, total, stack = state["entities"], 0.0, [eid]
    while stack:
        current = stack.pop()
        record = entities[current]
        total += float(record.get("mass_kg", 0.0)) + float(record.get("liquid_l", 0.0)) * float(record.get("liquid_density", 1.0))
        stack.extend(sorted(e for e, r in entities.items() if r.get("location") == current))
    return total


class FakeImpact:
    """Stands in for kernel/phys/impact.fall: lands the item on the robot's cell and records the call."""

    def __init__(self):
        self.calls = []

    def fall(self, w, eid, height_m=None, cause="fall", at=None, onto=None, by=None):
        room, cell = at
        self.calls.append({"eid": eid, "height_m": height_m, "cause": cause, "at": (room, list(cell)), "by": by})
        record = w.mut(eid)
        record.update(location=room, pos=list(cell), relation="on")
        record.pop("held_hand", None)
        return {"cell": list(cell), "room": room, "height_m": height_m, "cause": cause, "by": by}


class FakeSpec:
    def __init__(self, name="look", category="perception", energy=None, idle=False, arms=None, noise_db=None,
                 power_off_ok=False):
        self.name, self.category, self.energy, self.idle, self.arms = name, category, energy, idle, arms
        self.noise_db, self.power_off_ok = noise_db, power_off_ok


class FakeEnv:
    def __init__(self, w, seed=0, mode="covered", sensors=("symbolic", "proprioception")):
        self.world, self.seed, self._advance_mode, self.sensors = w, seed, mode, list(sensors)
        self.realism = None


class FakeCtx:
    def __init__(self, w, spec=None, args=None, seed=0):
        self.w, self.spec, self.args = w, spec or FakeSpec(), dict(args or {})
        self.env = FakeEnv(w, seed)
        self.data, self.notes, self.extra_time = {}, [], 0.0

    def note(self, text):
        self.notes.append(text)

    def spend(self, seconds):
        self.extra_time += seconds


class Tick:
    def __init__(self, env):
        self.env = env


def world(profile_name=None, materialize=True, **robot):
    """A normalized 3-room house (kitchen room_1, bedroom room_2, bathroom room_3; robot in room_1 at [5, 0]).
    materialize=True writes the body fields as schema.normalize will from BODY-ENGINE on; False keeps a legacy
    robot record."""
    config = make_house(3, 1, 3)
    config["goal"] = None
    cfg = {k: v for k, v in config["robot"].items() if k != "max_mass_kg"}
    if profile_name:
        cfg["body_profile"] = profile_name
    cfg.update(robot)
    config["robot"] = cfg
    state = normalize(config, REG)
    if materialize:
        state["robot"] = body.apply_profile(state["robot"], state)
    state.update(steps=1, time_s=0.0, stats={}, event_log=[], messages=[])
    w = World(state, REG)
    w.begin()
    return w


def add_item(w, eid, location, **props):
    record = object_record(eid, location, **dict({"portable": True}, **props))
    if location in w.s["rooms"] and "pos" not in record:
        record["pos"] = [1, 1]
    w.s["entities"][eid] = record
    return record


def hold(w, eid, hand="right", **props):
    record = add_item(w, eid, "robot", relation="held", held_hand=hand, **props)
    hands = ("left", "right") if hand == "both" else (hand,)
    for h in hands:
        w.s["robot"]["hands"][h] = eid
    return record


def advance(w, env, seconds, dt=30.0):
    """Run the body system over `seconds` in substeps of dt, as the engine does."""
    remaining = seconds
    while remaining > 1e-9:
        step = min(dt, remaining)
        w.s["time_s"] = round(w.s["time_s"] + step, 3)
        body.system_tick(w, step, Tick(env))
        remaining -= step


class PhysicsFakes(unittest.TestCase):
    def setUp(self):
        self.impact = FakeImpact()
        for patcher in (mock.patch.object(body, "_children_map", lambda state: None),
                        mock.patch.object(body, "_subtree_mass", fake_subtree_mass),
                        mock.patch.object(body_faults, "_impact", lambda: self.impact)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def hooks(self, **table):
        """Temporarily register hooks: name=[fn, ...]."""
        patcher = mock.patch.dict(plugins.HOOKS, {name: [(100, "test." + name, fn) for fn in fns]
                                                  for name, fns in table.items()})
        patcher.start()
        self.addCleanup(patcher.stop)


# ----------------------------------------------------------------------
# 1. profiles, registry, apply_profile

class Profiles(PhysicsFakes):
    def test_every_profile_resolves_with_all_keys_and_documented_sources(self):
        data = body._data()
        self.assertEqual(body.profile_names(), ["humanoid_compact", "humanoid_v1", "mobile_manipulator"])
        documented = " ".join(data["sources"]).replace(",", " ").replace(".", " ").split()
        for key in body.PROFILE_KEYS:
            self.assertIn(key, documented, "profiles.json sources say nothing about " + key)
        for name in body.profile_names():
            merged = body.profile({"robot": {"body_profile": name}})
            self.assertEqual(set(merged["eye_height_m"]), set(body.POSTURE_KEYS))

    def test_inherits_deep_merges_and_overrides_apply_for_one_episode(self):
        mobile = body.profile({"robot": {"body_profile": "mobile_manipulator"}})
        self.assertEqual(mobile["power_w"]["stance"]["standing"], 5)
        self.assertEqual(mobile["power_w"]["stance"]["sitting"], 5)          # inherited from humanoid_v1
        self.assertEqual(mobile["power_w"]["hotel_awake"], 70)
        self.assertFalse(mobile["stairs"])
        compact = body.profile({"robot": {"body_profile": "humanoid_compact"}})
        self.assertEqual((compact["max_hand_kg"], compact["battery_wh"], compact["speeds_mps"]["normal"]), (4.0, 1000.0, 0.8))
        tuned = {"robot": {"profile_overrides": {"power_w": {"hotel_awake": 100}}}}
        self.assertEqual(body.activity_power_w(tuned, "awake", held_kg=0), 145)
        self.assertEqual(body.profile({"robot": {}})["power_w"]["hotel_awake"], 70)   # the base stays untouched

    def test_unknown_or_broken_profiles_are_refused(self):
        with self.assertRaises(ValueError):
            body.profile({"robot": {"body_profile": "hovercraft"}})
        with self.assertRaises(ValueError):
            body.profile({"robot": {"profile_overrides": {"grippers": {"left": "tentacle"}}}})
        broken = {"profiles": {"bare": {"mass_kg": 1.0}, "loop_a": {"inherits": "loop_b"},
                               "loop_b": {"inherits": "loop_a"}}, "grippers": {}}
        with mock.patch.object(body, "_data", lambda: broken), mock.patch.dict(body._MERGED, clear=True):
            with self.assertRaises(ValueError) as caught:
                body.profile({"robot": {"body_profile": "bare"}})
            self.assertIn("lacks", str(caught.exception))
            with self.assertRaises(ValueError) as caught:
                body.profile({"robot": {"body_profile": "loop_a"}})
            self.assertIn("cycle", str(caught.exception))

    def test_apply_profile_materializes_today_defaults_and_explicit_fields_win(self):
        w = world()
        robot = body.apply_profile({}, w.s)
        for key, value in ROBOT_DEFAULTS.items():
            self.assertEqual(robot[key], value, key)
        self.assertEqual((robot["mass_kg"], robot["battery_wh"], robot["power_state"], robot["battery_temp_c"]),
                         (60.0, 2400.0, "on", 21.0))
        self.assertEqual(robot["grippers"], {"left": "hand", "right": "hand"})
        explicit = body.apply_profile({"body_profile": "humanoid_compact", "max_hand_kg": 5.0, "battery": 0.0}, w.s)
        self.assertEqual((explicit["max_hand_kg"], explicit["max_mass_kg"]), (5.0, 10.0))
        self.assertEqual((explicit["power_state"], explicit["battery_min_pct"]), ("off", 0.0))
        legacy = body.apply_profile({"arms_ok": {"left": False, "right": True}}, w.s)
        self.assertEqual(legacy["arm_health"], {"left": 0.0, "right": 1.0})
        self.assertEqual(legacy["arms_ok"], {"left": False, "right": True})

    def test_start_temperature_follows_the_start_room(self):
        w = world()
        w.s["rooms"]["room_1"]["temperature_c"] = 4.0
        robot = body.apply_profile({"room": "room_1"}, w.s)
        self.assertEqual(robot["battery_temp_c"], 4.0)


class Registry(PhysicsFakes):
    def tearDown(self):
        body.ROBOT_FIELDS.pop("test_field", None)

    def test_registration_is_unique_but_identical_redeclaration_is_a_no_op(self):
        body.register_robot_field("test_field", 1, "tests", "exact", "a test field")
        body.register_robot_field("test_field", 1, "tests", "exact", "same field, other words")
        with self.assertRaises(ValueError):
            body.register_robot_field("test_field", 2, "tests", "exact", "conflict")
        with self.assertRaises(ValueError):
            body.register_robot_field("other_field", 0, "tests", "secret", "unknown tier")
        self.assertEqual(body.robot_fields()["test_field"]["owner"], "tests")

    def test_get_falls_back_to_declared_defaults_on_legacy_states(self):
        w = world(materialize=False)
        state = w.s
        self.assertNotIn("mass_kg", state["robot"])
        self.assertEqual((body.get(state, "mass_kg"), body.get(state, "power_state")), (60.0, "on"))
        self.assertEqual(body.get(state, "battery_temp_c"), 21.0)
        first = body.get(state, "sensors")
        first["camera"] = False
        self.assertTrue(body.get(state, "sensors")["camera"])       # mutable defaults are fresh copies
        self.assertIsNone(body.get(state, "no_such_field"))
        self.assertEqual(body.get(state, "no_such_field", 3), 3)
        state["robot"]["battery"] = 0.0
        self.assertEqual(body.get(state, "power_state"), "off")

    def test_kernel_camera_navigation_and_food_fields_are_declared_with_their_tiers(self):
        fields = body.robot_fields()
        expect = {"battery": "gauge", "battery_health": "hidden", "fault_state": "detected", "facing_deg": "exact",
                  "view": "hidden", "lens_scratch": "hidden", "nav_foot_soil": "bucket", "hand_contamination": "hidden",
                  "camera_on": "exact", "privacy_policy": "exact", "battery_reserve_pct": "exact"}
        for name, tier in expect.items():
            self.assertEqual(fields[name]["tier"], tier, name)
        self.assertEqual(fields["facing_deg"]["owner"], "camera")
        self.assertFalse(fields["facing_deg"]["materialize"])
        self.assertTrue(all(f["tier"] == "hidden" for n, f in fields.items() if n.startswith("_")))

    def test_importing_the_body_registers_nothing_in_the_plugin_tables(self):
        code = ("import sys; from house_sim.kernel import plugins\n"
                "before = {k: len(v) for k, v in vars(plugins).items() if isinstance(v, (dict, list))}\n"
                "from house_sim.kernel import body, body_faults, proprio\n"
                "after = {k: len(v) for k, v in vars(plugins).items() if isinstance(v, (dict, list))}\n"
                "assert before == after, (before, after)\n"
                "assert 'house_sim.kernel.phys' not in sys.modules\n")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])


# ----------------------------------------------------------------------
# 2-6. power, energy, temperature, charging, thresholds

class Energy(PhysicsFakes):
    def test_activity_power_matches_the_spec_rates(self):
        state = world().s
        self.assertEqual(body.activity_power_w(state, "awake"), 115)
        self.assertEqual(body.activity_power_w(state, "standby"), 20)
        self.assertEqual(body.activity_power_w(state, "awake", held_kg=5) - 115, 15)
        self.assertEqual(body.activity_power_w(state, "physical", arms=1, held_kg=2), 161)
        self.assertEqual(body.activity_power_w(state, "motion"), 70)
        self.assertEqual(body.activity_power_w(state, "off"), 0.0)
        self.assertAlmostEqual(100 * 115 / 2400, 4.79, places=2)        # %/h awake idle
        self.assertAlmostEqual(20 * 60 / 2400 * 100, 50.0)               # 60 h of parked standby = 50 %
        state["robot"]["posture"] = "crouching"
        self.assertEqual(body.activity_power_w(state, "awake"), 130)
        state["robot"]["standing_on"] = "stool"
        self.assertEqual(body.activity_power_w(state, "standby"), 67)    # balancing on an aid even when parked
        with self.assertRaises(ValueError):
            body.activity_power_w(state, "dancing")

    def test_walking_costs_about_0_312_wh_per_metre(self):
        state = world().s
        for speed, v in (("normal", 0.8), ("careful", 0.4)):
            per_m = body.walk_wh(state, 1.0, speed, 0) + 70 / v / 3600
            self.assertAlmostEqual(per_m, 0.312 if speed == "normal" else 0.313, places=3)
        self.assertAlmostEqual(body.walk_wh(state, 10, "normal", 40), 5.76)   # (1 + 40/40) x 0.288 x 10
        self.assertAlmostEqual(body.stairs_wh(state, 12, 0), 14.4)
        self.assertAlmostEqual(body.stairs_wh(state, 0, 10), 4.8)

    def test_gauge_equals_battery_when_warm_and_the_cold_strands_earlier(self):
        w = world()
        w.s["robot"]["battery"] = 57.25
        self.assertEqual(body.gauge(w.s), 57.25)
        self.assertEqual((body.temp_factor(20), body.temp_factor(0), body.temp_factor(-10), body.temp_factor(-60)),
                         (1.0, 0.85, 0.7, 0.5))
        w.s["robot"].update(battery=35.0, battery_temp_c=-10.0)
        self.assertAlmostEqual(body.cutoff_soc(w.s), 30.0)
        self.assertAlmostEqual(body.available_wh(w.s), 120.0)
        body.spend_wh(w, 500.0)
        self.assertEqual(w.s["robot"]["battery"], 30.0)
        self.assertEqual(w.s["robot"]["power_state"], "off")
        self.assertEqual(body.gauge(w.s), 0.0)
        w.s["robot"]["battery_temp_c"] = 20.0                          # the pack warms up: the charge comes back
        self.assertAlmostEqual(body.gauge(w.s), 30.0)

    def test_spending_tracks_energy_minimum_and_never_raises_a_cold_battery(self):
        w = world()
        pct = body.spend_wh(w, 240.0)
        robot = w.s["robot"]
        self.assertAlmostEqual(pct, 10.0)
        self.assertEqual((robot["battery"], robot["battery_min_pct"], robot["energy_wh_used"]), (90.0, 90.0, 240.0))
        self.assertAlmostEqual(w.s["stats"]["energy"], 10.0)
        robot.update(battery=20.0, battery_temp_c=-10.0)              # below the cold cutoff of 30 %
        body.spend_wh(w, 1.0)
        self.assertEqual(robot["battery"], 20.0)

    def test_thresholds_events_body_log_and_undocked_reserve_breaches(self):
        w = world()
        w.s["robot"]["battery"] = 21.0
        w.s["time_s"] = 50.0
        body.spend_wh(w, 0.07 * 2400)                                   # 21 % -> 14 %
        kinds = [e["kind"] for e in w.s["event_log"]]
        self.assertEqual(kinds, ["battery_low", "battery_reserve"])
        log = w.s["robot"]["body_log"]
        self.assertEqual((log["battery_low_s"], log["battery_reserve_s"]), (50.0, 50.0))
        self.assertNotIn("battery_critical_s", log)
        self.assertEqual(w.s["stats"]["battery_reserve_breaches"], 1)
        self.assertEqual(w.s["robot"]["battery_min_pct"], 14.0)
        self.assertEqual(body.battery_state(w.s), "reserve")
        docked = world(docked=True, battery=16.0)
        body.spend_wh(docked, 0.02 * 2400)
        self.assertNotIn("battery_reserve_breaches", docked.s["stats"])
        self.assertIn("battery_reserve_s", docked.s["robot"]["body_log"])
        override = world(battery=26.0, battery_reserve_pct=25.0)
        body.spend_wh(override, 0.02 * 2400)
        self.assertEqual(override.s["stats"]["battery_reserve_breaches"], 1)

    def test_limp_mode_and_battery_states(self):
        w = world(battery=5.0)
        self.assertTrue(body.limp(w.s))
        self.assertEqual(body.battery_state(w.s), "critical")
        self.assertEqual(body.effective_speed(w.s, "fast"), ("careful", "Battery critical: walking at careful speed"))
        w.s["robot"].update(battery=50.0, legs_health=0.5)
        self.assertTrue(body.limp(w.s))
        self.assertEqual(body.effective_speed(w.s, None)[0], "careful")
        w.s["robot"]["legs_health"] = 1.0
        self.assertFalse(body.limp(w.s))
        self.assertEqual(body.battery_state(w.s), "ok")
        w.s["robot"]["power_state"] = "off"
        self.assertEqual(body.battery_state(w.s), "depleted")

    def test_power_down_crouches_counts_logs_and_sends_the_dying_gasp(self):
        w = world()
        w.s["time_s"] = 120.0
        self.assertTrue(body.power_down(w, "battery"))
        robot = w.s["robot"]
        self.assertEqual((robot["power_state"], robot["posture"], robot["standby"]), ("off", "crouching", False))
        self.assertEqual((w.s["stats"]["power_downs"], w.s["stats"]["power_downs_off_dock"]), (1, 1))
        self.assertEqual(robot["body_log"]["power_down_s"], 120.0)
        self.assertEqual(w._power_down_step, 1)
        gasp = w.s["messages"][-1]
        self.assertEqual((gasp["speaker"], gasp["text"], gasp["audience"]),
                         ("robot_system", "Robot powered down: battery depleted", ["resident_1"]))
        self.assertFalse(body.power_down(w, "battery"))                 # already off: nothing twice
        self.assertEqual(w.s["stats"]["power_downs"], 1)
        quiet = world(docked=True, network=False)
        body.power_down(quiet, "battery")
        self.assertNotIn("power_downs_off_dock", quiet.s["stats"])
        self.assertEqual(quiet.s["messages"], [])

    def test_power_down_on_a_climbing_aid_is_a_fall(self):
        w = world()
        add_item(w, "stool", "room_1", standing_height_m=0.25, rated_kg=136)
        w.s["robot"]["standing_on"] = "stool"
        hold(w, "mug", mass_kg=0.3)
        body.power_down(w, "battery")
        robot = w.s["robot"]
        self.assertTrue(robot["fallen"])
        self.assertEqual((robot["posture"], robot["standing_on"], w.s["stats"]["falls"]), ("lying", None, 1))
        self.assertEqual(self.impact.calls[0]["height_m"], 1.25)        # carry height + the stool

    def test_power_guard_and_quiet_guard(self):
        w = world()
        body.power_guard(w.s, FakeSpec())
        w.s["robot"]["power_state"] = "off"
        with self.assertRaises(body.PowerOff) as caught:
            body.power_guard(w.s, FakeSpec())
        self.assertEqual((str(caught.exception), caught.exception.category), (body.POWER_OFF_TEXT, "capability"))
        self.assertTrue(caught.exception.kernel_access)
        body.power_guard(w.s, FakeSpec(name="wait", power_off_ok=True))
        loud = FakeSpec(name="vacuum", category="physical", noise_db=70)
        body.quiet_guard(w.s, loud)
        w.s["robot"]["quiet"] = True
        with self.assertRaises(body.BodyRefusal) as caught:
            body.quiet_guard(w.s, loud)
        self.assertEqual(caught.exception.category, "social")
        body.quiet_guard(w.s, loud, FakeCtx(w))                            # numeric levels are checked before the handler
        variable = FakeSpec(name="drill", category="physical", noise_db=lambda ctx: 90 if ctx.args.get("hammer") else 50)
        body.quiet_guard(w.s, variable)                                     # callable levels are checked after it
        body.quiet_guard(w.s, variable, FakeCtx(w, args={"hammer": False}))
        with self.assertRaises(body.BodyRefusal):
            body.quiet_guard(w.s, variable, FakeCtx(w, args={"hammer": True}))


class SystemTick(PhysicsFakes):
    def charge_source(self, rate):
        self.hooks(robot_charge_source=[lambda w: rate])

    def test_idle_drain_is_awake_then_parked_and_chunk_independent(self):
        w = world()
        env = FakeEnv(w, mode="idle")
        advance(w, env, 30)
        self.assertFalse(w.s["robot"]["standby"])
        advance(w, env, 30)
        self.assertTrue(w.s["robot"]["standby"])
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"], 115 * 60 / 3600, places=6)
        coarse, fine = world(), world()
        advance(coarse, FakeEnv(coarse, mode="idle"), 3600, dt=30)
        advance(fine, FakeEnv(fine, mode="idle"), 3600, dt=10)
        expected = 100 - (115 * 60 + 20 * 3540) / 3600 / 24
        self.assertAlmostEqual(coarse.s["robot"]["battery"], expected, delta=1e-3)
        self.assertAlmostEqual(coarse.s["robot"]["battery"], fine.s["robot"]["battery"], delta=1e-3)

    def test_covered_advances_do_not_drain_and_keep_awake_suppresses_standby(self):
        w = world()
        advance(w, FakeEnv(w, mode="covered"), 600)
        self.assertEqual(w.s["robot"]["battery"], 100.0)
        awake = world()
        self.hooks(robot_keep_awake=[lambda w: True])
        advance(awake, FakeEnv(awake, mode="idle"), 600)
        self.assertFalse(awake.s["robot"]["standby"])
        self.assertAlmostEqual(awake.s["robot"]["energy_wh_used"], 115 * 600 / 3600, places=5)

    def test_cutoff_inside_a_substep_gets_its_exact_time(self):
        w = world(battery=0.01, standby=True, _idle_s=60.0)            # 0.24 Wh left, parked at 20 W: 43.2 s
        w.s["time_s"] = 1000.0
        advance(w, FakeEnv(w, mode="idle"), 60)
        robot = w.s["robot"]
        self.assertEqual(robot["power_state"], "off")
        self.assertAlmostEqual(robot["body_log"]["power_down_s"], 1043.2, places=3)
        self.assertEqual(robot["battery"], 0.0)
        self.assertEqual(w.s["stats"]["power_downs_off_dock"], 1)

    def test_seven_day_undocked_wait_strands_and_sixty_hours_parked_uses_half(self):
        w = world(standby=True, _idle_s=60.0)
        advance(w, FakeEnv(w, mode="idle"), 60 * 3600)
        self.assertAlmostEqual(w.s["robot"]["battery"], 50.0, delta=1e-3)
        advance(w, FakeEnv(w, mode="idle"), 5 * 24 * 3600)
        self.assertEqual(w.s["robot"]["power_state"], "off")
        self.assertEqual(body.robot_stranded(w.s, world().s), "robot_stranded")

    def test_cc_cv_charging_matches_a_one_second_euler_reference(self):
        self.charge_source(2.0)
        w = world(battery=50.0)
        advance(w, FakeEnv(w), 3600)
        level = 50.0
        for _ in range(3600):
            rate = 2.0 if level < 80 else 2.0 * (100 - level) / 20
            level = min(100.0, level + rate / 60)
        self.assertAlmostEqual(w.s["robot"]["battery"], level, delta=1e-3)
        self.assertTrue(w.s["robot"]["charging"])
        self.assertAlmostEqual(w.s["robot"]["energy_wh_charged"], (w.s["robot"]["battery"] - 50.0) * 24, places=4)

    def test_charging_takes_forty_minutes_to_eighty_percent_and_tapers_after(self):
        self.charge_source(2.0)
        w = world(battery=0.0)
        advance(w, FakeEnv(w), 40 * 60)
        self.assertAlmostEqual(w.s["robot"]["battery"], 80.0, places=6)
        advance(w, FakeEnv(w), 30 * 60)
        self.assertAlmostEqual(w.s["robot"]["battery"], 99.0, delta=0.01)
        advance(w, FakeEnv(w), 30 * 60)
        self.assertEqual((w.s["robot"]["battery"], w.s["robot"]["charging"]), (100.0, False))

    def test_a_powered_down_robot_wakes_at_three_percent_on_a_live_dock(self):
        self.charge_source(2.0)
        w = world(battery=0.0)
        self.assertEqual(w.s["robot"]["power_state"], "off")
        advance(w, FakeEnv(w), 60)
        self.assertEqual(w.s["robot"]["power_state"], "off")              # 2 %
        advance(w, FakeEnv(w), 30)
        robot = w.s["robot"]
        self.assertEqual((robot["power_state"], robot["standby"], robot["battery"]), ("on", True, 3.0))
        self.assertEqual(w.s["event_log"][-1]["kind"], "power_up")

    def test_the_pack_temperature_window_refuses_and_halves_charging(self):
        self.charge_source(2.0)
        cold = world(battery=50.0)
        cold.s["rooms"]["room_1"]["temperature_c"] = -5.0
        cold.s["robot"]["battery_temp_c"] = -5.0
        self.assertEqual(body.charge_rate(cold), 0.0)
        advance(cold, FakeEnv(cold), 60)
        self.assertEqual(cold.s["robot"]["battery"], 50.0)
        self.assertIn("charge_refused_temp_s", cold.s["robot"]["body_log"])
        cool = world(battery=50.0)
        cool.s["robot"]["battery_temp_c"] = 5.0
        self.assertEqual(body.charge_rate(cool), 1.0)
        hot = world(battery=50.0)
        hot.s["robot"]["battery_temp_c"] = 46.0
        self.assertEqual(body.charge_rate(hot), 0.0)
        self.assertEqual(body.charge_rate(world(battery=50.0)), 2.0)

    def test_docked_on_a_live_source_the_dock_carries_the_idle_load(self):
        self.charge_source(2.0)
        w = world(battery=100.0, docked=True)
        advance(w, FakeEnv(w, mode="idle"), 3600)
        self.assertEqual((w.s["robot"]["battery"], w.s["robot"]["energy_wh_used"]), (100.0, 0.0))

    def test_pack_temperature_relaxes_and_overheating_is_a_fault_until_it_cools(self):
        w = world(_activity="active")
        advance(w, FakeEnv(w), 1800)
        self.assertAlmostEqual(w.s["robot"]["battery_temp_c"], 27 - 6 * math.exp(-1), delta=0.02)
        attic = world(_activity="active")
        attic.s["rooms"]["room_1"]["temperature_c"] = 58.0
        advance(attic, FakeEnv(attic), 4 * 3600)
        self.assertIsNotNone(body.fault(attic.s, "battery_overheat"))
        self.assertTrue(attic.s["robot"]["fault_state"]["battery_overheat"]["detected"])
        self.assertEqual(body.mobile_problem(attic.s), (body.OVERHEAT_TEXT, "capability"))
        self.assertEqual(body.manip_problem(attic.s), (body.OVERHEAT_TEXT, "capability"))
        attic.s["rooms"]["room_1"]["temperature_c"] = 20.0
        advance(attic, FakeEnv(attic), 4 * 3600)
        self.assertIsNone(body.fault(attic.s, "battery_overheat"))
        self.assertIsNotNone(attic.s["robot"]["fault_state"]["battery_overheat"]["cleared_s"])

    def test_the_network_window_restores_the_connection(self):
        w = world()
        body.ev_network(w, {"ok": False, "until_s": 90.0})
        self.assertEqual((w.s["robot"]["network"], w.s["robot"]["network_until_s"]), (False, 90.0))
        advance(w, FakeEnv(w), 60)
        self.assertFalse(w.s["robot"]["network"])
        advance(w, FakeEnv(w), 30)
        self.assertTrue(w.s["robot"]["network"])
        self.assertNotIn("network_until_s", w.s["robot"])
        self.assertIsNone(body.fault(w.s, "network_down"))
        body.ev_network(w, {"ok": False})
        body.ev_network(w, {"ok": True})
        self.assertTrue(w.s["robot"]["network"])

    def test_the_system_writes_nothing_once_the_robot_is_settled(self):
        w = world()
        advance(w, FakeEnv(w), 6 * 3600)                                # pack at room temperature, nothing to do
        w.begin()
        advance(w, FakeEnv(w), 120)
        self.assertEqual(w.journal_keys(), [])


class EventsAndInvariant(PhysicsFakes):
    def test_robot_battery_event_sets_the_gauge(self):
        w = world()
        body.ev_battery(w, {"level": 12})
        self.assertEqual(w.s["robot"]["battery"], 12.0)
        self.assertEqual([e["kind"] for e in w.s["event_log"]], ["battery_low", "battery_reserve"])
        body.ev_battery(w, {"level": 0})
        self.assertEqual(w.s["robot"]["power_state"], "off")
        body.ev_battery(w, {"level": 50})
        self.assertEqual(w.s["robot"]["power_state"], "on")
        w.s["robot"]["battery_temp_c"] = -10.0
        body.ev_battery(w, {"level": 50})
        self.assertAlmostEqual(body.gauge(w.s), 50.0)

    def test_robot_stranded_needs_an_off_dock_power_down_or_an_undocked_dead_robot(self):
        baseline = world().s
        w = world()
        self.assertIsNone(body.robot_stranded(w.s, baseline))
        body.power_down(w, "battery")
        self.assertEqual(body.robot_stranded(w.s, baseline), "robot_stranded")
        outage = world(docked=True)
        body.power_down(outage, "battery")
        self.assertIsNone(body.robot_stranded(outage.s, baseline))
        dead_from_start = world(battery=0.0)
        self.assertIsNone(body.robot_stranded(dead_from_start.s, dead_from_start.s))

    def test_swap_pack_returns_the_old_pack_wakes_and_clears_a_degraded_pack(self):
        w = world(battery=0.0)
        body_faults.add_fault(w, "battery_degraded", severity=0.6, latent=True)
        old = body.swap_pack(w, 85.0)
        self.assertEqual(old, {"charge_pct": 0.0, "capacity_wh": 2400.0, "health": 0.6, "temp_c": 21.0})
        robot = w.s["robot"]
        self.assertEqual((robot["battery"], robot["battery_health"], robot["power_state"]), (85.0, 1.0, "on"))
        self.assertIsNone(body.fault(w.s, "battery_degraded"))
        small = world()
        body.swap_pack(small, 0.0, capacity_wh=1000.0, health=0.9, temp_c=30.0)
        self.assertEqual((small.s["robot"]["battery_wh"], small.s["robot"]["power_state"]), (1000.0, "off"))


# ----------------------------------------------------------------------
# settlement and motion

class Settlement(PhysicsFakes):
    def test_every_action_now_costs_its_power_over_its_seconds(self):
        w = world()
        body.settle_energy(w, FakeCtx(w, FakeSpec("look", "perception")), 1.0)
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"], 120 / 3600, places=6)
        w = world()
        body.settle_energy(w, FakeCtx(w, FakeSpec("say", "communication")), 2.0)
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"], 2 * 120 / 3600, places=6)
        w = world()
        hold(w, "pot", hand="both", mass_kg=4.0)
        body.settle_energy(w, FakeCtx(w, FakeSpec("stir", "physical"), {"target": "pot"}), 36.0)
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"], (115 + 80 + 12) * 36 / 3600, places=6)
        w = world()
        body.settle_energy(w, FakeCtx(w, FakeSpec("scrub", "physical", energy=0.02)), 3.6)
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"], 155 * 3.6 / 3600 + 0.48, places=6)
        self.assertEqual((w.s["robot"]["_activity"], w.s["robot"]["_idle_s"], w.s["robot"]["standby"]),
                         ("active", 0.0, False))

    def test_nothing_is_charged_for_waits_while_off_or_on_a_live_dock(self):
        w = world(_idle_s=30.0)
        hold(w, "bag", mass_kg=5.0)
        body.settle_energy(w, FakeCtx(w, FakeSpec("wait", "control", idle=True)), 600.0)
        self.assertEqual((w.s["robot"]["battery"], w.s["robot"]["_idle_s"]), (100.0, 30.0))
        self.assertEqual(w.s["robot"]["_held_kg"], 5.0)                  # the idle drain will pay for holding it
        off = world(battery=0.0)
        body.settle_energy(off, FakeCtx(off, FakeSpec("look")), 10.0)
        self.assertEqual(off.s["robot"]["energy_wh_used"], 0.0)
        self.hooks(robot_charge_source=[lambda w: 2.0])
        docked = world(docked=True)
        body.settle_energy(docked, FakeCtx(docked, FakeSpec("speak", "communication")), 30.0)
        self.assertEqual(docked.s["robot"]["energy_wh_used"], 0.0)

    def test_a_ten_metre_walk_drains_the_formula(self):
        w = world()
        ctx = FakeCtx(w, FakeSpec("walk_to", "physical"))
        path = [[5, r] for r in range(1, 8)] + [[4, 7], [3, 7], [2, 7], [1, 7], [0, 7], [0, 6], [0, 5], [0, 4],
                                                 [0, 3], [0, 2], [0, 1], [0, 0], [1, 0]]
        cells = body.motion(ctx, path, [5, 0])
        self.assertEqual(cells, path)
        record = ctx.data["motion"]
        self.assertEqual(record["meters"], 10.0)
        body.settle_energy(w, ctx, ctx.data["seconds"])
        self.assertAlmostEqual(w.s["robot"]["energy_wh_used"] / 10, 0.312, delta=0.312 * 0.05)

    def test_the_buffer_completes_then_powers_down_and_a_larger_shortfall_is_refused(self):
        w = world(battery=0.2)                                            # 4.8 Wh left
        ctx = FakeCtx(w, FakeSpec("scrub", "physical"))
        body.settle_energy(w, ctx, 200.0)                                 # 8.6 Wh: 3.8 Wh short, within 12 Wh
        self.assertEqual(w.s["robot"]["power_state"], "off")
        self.assertEqual(ctx.notes, [body.POWERED_DOWN_NOTE])
        w = world(battery=0.2)
        w.begin()
        env = FakeEnv(w)
        with self.assertRaises(body.BodyRefusal) as caught:
            body.settle_energy(w, FakeCtx(w, FakeSpec("scrub", "physical")), 3600.0)
        self.assertEqual((str(caught.exception), caught.exception.category), (body.ENERGY_TEXT, "capability"))
        self.assertEqual(w.s["stats"]["energy_refusals"], 1)
        w.rollback()                                                       # the engine rolls the step back ...
        self.assertNotIn("energy_refusals", w.s["stats"])
        caught.exception.apply(env)                                        # ... and re-applies the bookkeeping
        self.assertEqual(w.s["stats"]["energy_refusals"], 1)
        self.assertEqual(w.s["robot"]["battery"], 0.2)

    def test_settlement_drops_overloaded_items_and_notes_straining(self):
        w = world()
        hold(w, "bucket", mass_kg=1.0, liquid_l=12.0)                     # 13 kg in one hand: 1.6 x capacity
        ctx = FakeCtx(w, FakeSpec("fill", "physical"))
        body.settle_energy(w, ctx, 5.0)
        self.assertEqual(self.impact.calls[0]["cause"], "overload")
        self.assertEqual(w.s["robot"]["hands"]["right"], None)
        self.assertEqual((w.s["stats"]["robot_drops"], w.s["robot"]["_held_kg"]), (1, 0.0))
        w = world()
        hold(w, "bag", mass_kg=9.0)
        ctx = FakeCtx(w, FakeSpec("carry", "physical"))
        body.settle_energy(w, ctx, 5.0)
        self.assertEqual(ctx.notes, ["Straining to hold bag"])
        self.assertEqual(w.s["robot"]["_hand_kg"], {"right": ["bag", 9.0]})

    def test_a_settlement_builds_the_physics_children_map_once(self):
        calls = []
        with mock.patch.object(body, "_children_map", lambda state: calls.append(1)):
            w = world()
            hold(w, "kettle", hand="left", mass_kg=1.5, liquid_l=1.0)
            hold(w, "pan", hand="right", mass_kg=2.0)
            body.settle_energy(w, FakeCtx(w, FakeSpec("stir", "physical")), 10.0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(w.s["robot"]["_held_kg"], 4.5)
            calls.clear()
            body.settle_energy(world(), FakeCtx(world(), FakeSpec("look")), 1.0)
            self.assertEqual(calls, [])                                   # nothing held: no map at all

    def test_motion_reproduces_today_durations_and_records_the_walk(self):
        w = world()
        state = w.s
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        path = [[5, 1], [4, 2], [4, 3], [3, 4]]
        cells = body.motion(ctx, path, [5, 0], extra_s=1.5, connection="conn_room_1__room_2")
        self.assertEqual(cells, path)
        expected = (geometry.path_length_m([(5, 0)] + [tuple(c) for c in path]) + 1.0) / 0.8 + 1.5
        self.assertEqual(ctx.data["seconds"], expected)
        record = ctx.data["motion"]
        self.assertEqual((record["from_room"], record["to_room"], record["connection"], record["speed"]),
                         ("room_1", "room_2", "conn_room_1__room_2", "normal"))
        self.assertEqual((record["cut"], record["exhausts"], record["stairs_up"]), (None, False, 0))
        self.assertAlmostEqual(record["meters"], geometry.path_length_m([(5, 0)] + [tuple(c) for c in path]) + 1.0)
        self.assertEqual(state["robot"]["room"], "room_1")                 # the handler moves the robot, not motion()
        quiet = world(quiet=True)
        qctx = FakeCtx(quiet, FakeSpec("approach", "physical"), {"speed": "fast"})
        body.motion(qctx, [[5, 1]], [5, 0], extra_s=0.3)
        self.assertEqual((qctx.data["motion"]["speed"], qctx.data["seconds"]), ("normal", 0.5 / 0.8 + 0.3))
        self.assertEqual(qctx.notes, ["Quiet mode: walking at normal speed"])

    def test_stairs_are_derived_from_the_connection_and_timed(self):
        w = world()
        state = w.s
        state["rooms"]["room_2"]["floor"] = 1
        state["connections"]["conn_room_1__room_2"].update(kind="stairs", steps=12)
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        body.motion(ctx, [[5, 1]], [5, 0], connection="conn_room_1__room_2")
        record = ctx.data["motion"]
        self.assertEqual((record["stairs_up"], record["stairs_down"]), (12, 0))
        self.assertEqual(ctx.data["seconds"], (0.5 + 1.0) / 0.8 + 0.8 * 12)
        self.assertEqual(body.stairs_direction(state, state["connections"]["conn_room_1__room_2"], "room_2", "room_1"),
                         "down")
        self.assertIsNone(body.stairs_direction(state, state["connections"]["conn_room_2__room_3"], "room_2", "room_3"))

    def test_a_walk_the_battery_cannot_finish_is_cut_at_the_affordable_prefix(self):
        w = world()
        per_cell = 0.5 * (0.264 + 70 / 0.4 / 3600)                         # a nearly empty robot limps: careful speed
        w.s["robot"]["battery"] = round(100 * per_cell * 3.5 / 2400, 9)      # 3.5 cells' worth
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        path = [[5, r] for r in range(1, 8)]
        cells = body.motion(ctx, path, [5, 0], connection="conn_room_1__room_2")
        self.assertEqual(cells, [[5, 1], [5, 2], [5, 3]])
        self.assertEqual(ctx.notes, ["Battery critical: walking at careful speed"])
        record = ctx.data["motion"]
        self.assertEqual((record["cut"], record["exhausts"], record["connection"], record["to_room"], record["speed"]),
                         ("battery", True, None, "room_1", "careful"))
        self.assertEqual((record["meters"], ctx.data["seconds"], ctx.data["connection"]), (1.5, 1.5 / 0.4, None))
        self.assertEqual(body.BATTERY_CUT_TEXT.format(record["meters"]),
                         "Battery depleted after 1.5 m; the robot powered down")
        body.settle_energy(w, ctx, ctx.data["seconds"])
        self.assertEqual((w.s["robot"]["power_state"], w.s["robot"]["battery"]), ("off", 0.0))

    def test_the_robot_never_starts_a_flight_it_cannot_finish(self):
        w = world()
        state = w.s
        state["rooms"]["room_2"]["floor"] = 1
        state["connections"]["conn_room_1__room_2"].update(kind="stairs", steps=12)
        state["robot"]["battery"] = 0.5                                  # 12 Wh: the walk yes, 14.4 Wh of stairs no
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        cells = body.motion(ctx, [[5, 1], [5, 2]], [5, 0], connection="conn_room_1__room_2")
        self.assertEqual(cells, [[5, 1], [5, 2]])
        record = ctx.data["motion"]
        self.assertEqual((record["stairs_up"], record["to_room"], record["exhausts"]), (0, "room_1", True))
        k, ok = body.affordable_prefix(state, [0.5, 0.5], "normal", 0.0, stairs=(12, 0))
        self.assertEqual((k, ok), (2, False))
        self.assertEqual(body.affordable_prefix(state, [0.5, 0.5], "normal", 0.0, stairs=(5, 0)), (2, True))

    def test_cut_motion_keeps_only_what_was_walked(self):
        w = world()
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        body.motion(ctx, [[5, 1], [5, 2], [5, 3], [5, 4]], [5, 0], connection="conn_room_1__room_2")
        record = body.cut_motion(ctx, 1.2, "blocked")
        self.assertEqual(record["path"], [[5, 1], [5, 2]])
        self.assertEqual((record["meters"], record["cut"], record["exhausts"], record["connection"]),
                         (1.2, "blocked", False, None))
        self.assertAlmostEqual(record["seconds"], 1.5)
        self.assertIsNone(body.cut_motion(FakeCtx(w), 1.0, "blocked"))

    def test_motion_records_carry_the_load(self):
        w = world()
        hold(w, "crate", hand="both", mass_kg=12.0)
        ctx = FakeCtx(w, FakeSpec("move", "physical"))
        body.motion(ctx, [[5, 1], [5, 2]], [5, 0])
        self.assertEqual(ctx.data["motion"]["load_kg"], 12.0)
        self.assertAlmostEqual(body.motion_work_wh(w.s, ctx.data["motion"]), 1.0 * 0.288 * (1 + 12 / 40))


# ----------------------------------------------------------------------
# 7. capacity, carry and the lift check

class Capacity(PhysicsFakes):
    def layout(self):
        w = world()
        add_item(w, "wall_cabinet", "room_1", pos=[2, 0], mounted=True, mount_height_m=1.5, accepts_items=True,
                 portable=False, size_m=[0.5, 0.3, 0.6])
        add_item(w, "vase", "wall_cabinet", relation="in", mass_kg=3.0)
        add_item(w, "top_box", "room_1", pos=[3, 3], z_m=1.8, mass_kg=3.0)
        add_item(w, "pot", "room_1", pos=[2, 3], mass_kg=3.0)
        return w

    def test_derates_by_reach_height_platform_and_limp(self):
        w = self.layout()
        state = w.s
        state["robot"]["pos"] = [2, 2]
        self.assertEqual(body.capacity_kg(state, "right", "pot"), 8.0)              # an ordinary pick is unchanged
        self.assertAlmostEqual(body.capacity_kg(state, "right", "vase"), 4.8)       # over the counter (Chebyshev 2)
        state["robot"]["pos"] = [2, 1]
        self.assertEqual(body.capacity_kg(state, "right", "vase"), 8.0)             # close enough: no extra lever
        self.assertAlmostEqual(body.capacity_kg(state, "both", "top_box"), 12.0)    # above the head
        state["robot"]["standing_on"] = "stool"
        self.assertEqual(body.capacity_kg(state, "left"), 4.0)
        state["robot"].update(standing_on=None, battery=4.0)
        self.assertEqual(body.capacity_kg(state, "left"), 4.0)
        self.hooks(lift_capacity=[lambda state, hand, eid: 0.5 if eid == "pot" else None])
        state["robot"]["battery"] = 80.0
        self.assertEqual((body.capacity_kg(state, "right", "pot"), body.capacity_kg(state, "right")), (4.0, 8.0))

    def test_capacity_follows_arm_health_and_the_strongest_hand(self):
        w = world()
        w.s["robot"]["arm_health"] = {"left": 0.5, "right": 0.25}
        self.assertEqual((body.capacity_kg(w.s), body.capacity_kg(w.s, "both")), (4.0, 5.0))

    def test_an_overload_is_refused_counted_felt_and_survives_the_rollback(self):
        w = self.layout()
        w.s["time_s"] = 30.0
        self.assertIsNone(body.check_lift(w, "pot", "right", 7.9))
        with self.assertRaises(body.BodyRefusal) as caught:
            body.check_lift(w, "pot", 1, 12.0)
        exc = caught.exception
        self.assertEqual((str(exc), exc.category, exc.grants), (body.OVERLOAD_TEXT, "safety",
                                                                (("pot", "touch", ("mass_kg",)),)))
        self.assertEqual(w.s["stats"]["overload_attempts"], 1)
        self.assertEqual(w.s["event_log"][-1], {"time_s": 30.0, "step": 1, "kind": "overload", "target": "pot",
                                                "ratio_bucket": "moderate"})
        self.assertEqual(w.s["robot"]["body_log"]["overload_s"], 30.0)
        self.assertEqual(w.s["entities"]["pot"]["location"], "room_1")      # nothing dropped, nothing moved
        w.rollback()
        exc.apply(FakeEnv(w))
        self.assertEqual(w.s["stats"]["overload_attempts"], 1)
        self.assertIsNone(body.check_lift(w, "pot", "both", 12.0))

    def test_the_total_carried_load_is_limited(self):
        w = world(max_mass_kg=12.0)
        add_item(w, "pot", "room_1", mass_kg=7.0)
        hold(w, "kettle", hand="left", mass_kg=7.0)
        with self.assertRaises(body.BodyRefusal) as caught:
            body.check_lift(w, "pot", "right")
        self.assertEqual(caught.exception.category, "safety")
        self.assertEqual(body.hold_capacity_left(w, "right"), 5.0)
        self.assertEqual(body.hold_capacity_left(w, "left"), 1.0)
        free = world()
        hold(free, "kettle", hand="left", mass_kg=3.0)
        self.assertEqual((body.hold_capacity_left(free, "left"), body.hold_capacity_left(free, "right")), (5.0, 8.0))

    def test_a_latent_weak_arm_is_detected_by_the_first_heavy_lift(self):
        w = world()
        add_item(w, "pan", "room_1", mass_kg=6.0)
        body_faults.add_fault(w, "arm_weak", part="left", severity=0.5, latent=True)
        self.assertFalse(w.s["robot"]["fault_state"]["arm_weak_left"]["detected"])
        with self.assertRaises(body.BodyRefusal) as caught:
            body.check_lift(w, "pan", "left")
        self.assertEqual((str(caught.exception), caught.exception.category),
                         ("The left arm could not take the load (actuator weakness detected)", "capability"))
        self.assertTrue(w.s["robot"]["fault_state"]["arm_weak_left"]["detected"])
        self.assertEqual(w.s["stats"]["overload_attempts"], 1)
        self.assertIsNone(body.check_lift(w, "pan", "right"))
        with self.assertRaises(body.BodyRefusal) as caught:
            body.check_lift(w, "pan", "left")                                 # known now: a plain overload
        self.assertEqual(str(caught.exception), body.OVERLOAD_TEXT)
        self.assertEqual(w.s["stats"]["overload_attempts"], 2)

    def test_limp_mode_refuses_heavy_lifts_without_counting_an_overload(self):
        w = world(battery=4.0)
        add_item(w, "pan", "room_1", mass_kg=6.0)
        with self.assertRaises(body.BodyRefusal) as caught:
            body.check_lift(w, "pan", "right")
        self.assertEqual((str(caught.exception), caught.exception.category), (body.LIMP_LIFT_TEXT, "capability"))
        self.assertNotIn("overload_attempts", w.s["stats"])

    def test_stability_margin_and_total_mass(self):
        w = world()
        self.assertAlmostEqual(body.stability_margin(w.s, 0), 0.12)
        self.assertAlmostEqual(body.stability_margin(w.s, 8), 0.12 - 8 * 0.35 / 68)
        self.assertLess(body.stability_margin(w.s, 40, lever_m=0.5), 0)
        hold(w, "box", hand="both", mass_kg=10.0)
        add_item(w, "book", "box", relation="in", mass_kg=1.5)
        self.assertEqual((body.carried_kg(w), body.total_mass_kg(w)), (11.5, 71.5))
        self.assertEqual(body.held_hands(w.s), [("box", ("left", "right"))])


# ----------------------------------------------------------------------
# 8. grasp size, precision, friction, forces, soil

class Grippers(PhysicsFakes):
    def test_grasp_size_classes(self):
        state = world().s
        bead = {"size_m": [0.01, 0.01, 0.005], "relation": "on"}
        self.assertEqual(body.grasp_problem(state, bead, "right", None), (None, "pinch"))
        self.assertEqual(body.grasp_problem(state, bead, "right", "normal"), (body.POWER_TOO_SMALL_TEXT, None))
        self.assertEqual(body.grasp_problem(state, bead, "right", "pinch"), (None, None))
        dust = {"size_m": [0.001, 0.001, 0.001]}
        self.assertEqual(body.grasp_problem(state, dust, "right", "pinch"), (body.TOO_SMALL_TEXT, None))
        mug = {"size_m": [0.09, 0.09, 0.1]}
        self.assertEqual(body.grasp_problem(state, mug, "left", "firm"), (None, None))
        paper = {"size_m": [0.21, 0.297, 0.0001], "relation": "on", "location": "table_1"}
        self.assertEqual(body.grasp_problem(state, paper, "right", None), (None, "edges"))
        self.assertEqual(body.grasp_problem(state, paper, "right", "scoop"), (None, None))
        self.assertEqual(body.GRASP_AUTO["edges"][1], 3.0)
        held_paper = dict(paper, relation="in")
        self.assertEqual(body.grasp_problem(state, held_paper, "right", None), (None, None))

    def test_parallel_grippers_need_edges_or_scoop_for_flat_items_and_pinch_smaller(self):
        state = world("mobile_manipulator").s
        paper = {"size_m": [0.21, 0.297, 0.0001], "relation": "on"}
        self.assertEqual(body.grasp_problem(state, paper, "left", None), (body.THIN_FLAT_TEXT, None))
        self.assertEqual(body.grasp_problem(state, paper, "left", "edges"), (None, None))
        washer = {"size_m": [0.008, 0.008, 0.0015]}
        self.assertEqual(body.grasp_problem(state, washer, "left", "normal"), (None, None))
        self.assertEqual(body.grip_limit_n(state, "left", "pinch"), 140.0)

    def test_precision_degrades_with_the_lens_darkness_a_weak_arm_soil_and_speed(self):
        w = world()
        state = w.s
        self.assertAlmostEqual(body.precision_mm(state, "right"), 2.0)
        state["robot"]["camera_clean"] = 0.4
        self.assertAlmostEqual(body.precision_mm(state, "right"), 6.0)
        state["robot"]["camera_clean"] = 1.0
        with mock.patch("house_sim.kernel.perception.light_level", lambda s, rid: 0.1):
            self.assertAlmostEqual(body.precision_mm(state, "right"), 3.0)
            state["robot"]["headlamp_on"] = True
            self.assertAlmostEqual(body.precision_mm(state, "right"), 2.0)
        state["robot"]["arm_health"] = {"left": 1.0, "right": 0.5}
        self.assertAlmostEqual(body.precision_mm(state, "right"), 4.0)
        state["robot"]["hand_soil"] = {"right": {"grease": 1.0}}
        self.assertAlmostEqual(body.precision_mm(state, "right"), 6.0)
        state["robot"]["speed_mode"] = "fast"
        self.assertAlmostEqual(body.precision_mm(state, "right"), 9.0)
        state["robot"]["arm_health"]["right"] = 0.0
        self.assertAlmostEqual(body.precision_mm(state, "right"), 22.5)    # the 0.2 floor

    def test_grip_friction_soil_and_the_slip_fault(self):
        w = world()
        self.assertAlmostEqual(body.grip_mu(w.s, "right"), 0.8)
        body.soil_hand(w, "right", "grease", 0.4)
        self.assertAlmostEqual(body.slippery(w.s, "right"), 0.4)
        self.assertAlmostEqual(body.grip_mu(w.s, "right"), 0.64)
        self.assertIsNone(body.fault(w.s, "gripper_slip_right"))
        body.soil_hand(w, "both", "water", 0.4)                             # 0.4 + 0.2 crosses the slip level
        record = body.fault(w.s, "gripper_slip_right")
        self.assertEqual((record["source"], record["detected"]), ("soil", False))
        self.assertIsNone(body.fault(w.s, "gripper_slip_left"))
        self.assertAlmostEqual(body.grip_mu(w.s, "right"), 0.8 * 0.7 * 0.6)
        body.clean_hand(w, "right", kinds=["grease"])
        self.assertIsNone(body.fault(w.s, "gripper_slip_right"))
        self.assertEqual(w.s["robot"]["hand_soil"], {"left": {"water": 0.4}, "right": {"water": 0.4}})
        body.clean_hand(w, "both")
        self.assertEqual(w.s["robot"]["hand_soil"], {})

    def test_force_limits(self):
        w = world()
        self.assertEqual((body.limit(w.s, "push_n"), body.limit(w.s, "pull_n"), body.limit(w.s, "wrist_nm")),
                         (220.0, 180.0, 6.0))
        self.assertEqual((body.grip_limit_n(w.s, "right"), body.grip_limit_n(w.s, "right", "pinch")), (150.0, 30.0))
        w.s["robot"]["arm_health"] = {"left": 0.0, "right": 1.0}
        self.assertEqual(body.grip_limit_n(w.s, "left"), 0.0)
        with self.assertRaises(ValueError):
            body.limit(w.s, "kick_n")


# ----------------------------------------------------------------------
# 9. speed, stopping, noise, eye height

class Motion(PhysicsFakes):
    def test_stopping_distances(self):
        state = world().s
        self.assertAlmostEqual(body.stopping_distance_m(state, "careful"), 0.16)
        self.assertAlmostEqual(body.stopping_distance_m(state, "normal"), 0.40)
        self.assertAlmostEqual(body.stopping_distance_m(state, "fast"), 0.72)
        self.assertAlmostEqual(body.stopping_distance_m(state, "normal", mu=0.24), 0.51, places=2)
        self.assertEqual(body.stopping_distance_m(state, "normal", mu=0.0), float("inf"))

    def test_noise_table_and_quiet_mode(self):
        w = world()
        self.assertEqual([body.noise_db(w.s, "footsteps", m) for m in ("careful", "normal", "fast")], [30, 36, 44])
        self.assertEqual((body.noise_db(w.s, "speech"), body.noise_db(w.s, "actuator")), (60, 35))
        w.s["robot"]["quiet"] = True
        self.assertEqual(body.noise_db(w.s, "footsteps", "fast"), 32)        # fast becomes normal, then -4 dB
        self.assertEqual(body.noise_db(w.s, "speech"), 45)
        self.assertEqual(body.quiet_limit_db(w.s), 55)
        with self.assertRaises(ValueError):
            body.noise_db(w.s, "whistle")

    def test_eye_height_by_posture_aid_and_profile(self):
        w = world()
        state = w.s
        self.assertEqual(body.eye_height_m(state), 1.5)
        for posture, height in (("crouching", 0.95), ("kneeling", 0.6), ("sitting", 0.95)):
            state["robot"]["posture"] = posture
            self.assertEqual(body.eye_height_m(state), height)
        state["robot"].update(posture="lying", fallen=True)
        self.assertEqual(body.eye_height_m(state), 0.3)
        state["robot"].update(posture="standing", fallen=False, standing_on="stool")
        self.assertAlmostEqual(body.eye_height_m(state), 1.9)                # aid without a height: 0.4 m
        add_item(w, "stool", "room_1", standing_height_m=0.25)
        self.assertAlmostEqual(body.eye_height_m(state), 1.75)
        self.assertEqual(body.eye_height_m(world("humanoid_compact").s), 1.2)

    def test_speed_modes(self):
        w = world()
        self.assertEqual(body.effective_speed(w.s, None), ("normal", None))
        self.assertEqual(body.speed_mps(w.s, "fast"), 1.2)
        w.s["robot"]["quiet"] = True
        self.assertEqual(body.effective_speed(w.s, "fast"), ("normal", "Quiet mode: walking at normal speed"))
        self.assertEqual(body.effective_speed(w.s, "careful"), ("careful", None))
        with self.assertRaises(ValueError):
            body.effective_speed(w.s, "sprint")
        self.assertEqual(body.speed_mps(world("mobile_manipulator").s), 0.6)


# ----------------------------------------------------------------------
# sensors and capability problems (TIER1_INTEGRATION 1.3)

class Sensors(PhysicsFakes):
    def test_imaging_and_depth(self):
        w = world()
        state = w.s
        self.assertTrue(body.imaging(state) and body.depth_ok(state))
        state["robot"]["privacy_mode"] = True
        self.assertFalse(body.imaging(state))
        self.assertTrue(body.depth_ok(state))
        state["robot"]["privacy_mode"] = False
        body.set_sensors(w, camera=False)
        self.assertFalse(body.imaging(state))
        body.set_sensors(w, camera=True)
        body_faults.add_fault(w, "camera_failed")
        self.assertFalse(body.imaging(state))
        state["robot"]["power_state"] = "off"
        self.assertFalse(body.depth_ok(state))

    def test_room_policies_switch_sensors_off_always_or_when_occupied(self):
        w = world()
        state = w.s
        state["robot"]["room"] = "room_3"
        body.set_sensors(w, policy={"camera_off": ["bathroom"], "mic_off": ["bathroom"], "when": "occupied"})
        self.assertTrue(body.imaging(state))
        state["people"]["resident_1"]["location"] = "room_3"
        self.assertFalse(body.imaging(state))
        self.assertEqual(body.can_hear(state), "addressed")
        state["people"]["resident_1"]["location"] = "room_1"
        body.set_sensors(w, policy={"camera_off": ["bathroom"], "when": "always"})
        self.assertTrue(body.policy_off(state, "camera"))
        self.assertFalse(body.policy_off(state, "mic"))
        state["robot"]["room"] = "room_2"
        self.assertTrue(body.imaging(state))
        self.assertEqual(body.set_sensors(w, policy={})["privacy_policy"], None)
        for bad in ({"camera_off": "bathroom"}, {"when": "sometimes"}, {"lights_off": []}, ["bathroom"]):
            with self.assertRaises(ValueError):
                body.set_sensors(w, policy=bad)

    def test_hearing_levels(self):
        w = world()
        state = w.s
        self.assertEqual(body.can_hear(state), "full")
        state["robot"]["privacy_mode"] = True
        self.assertEqual(body.can_hear(state), "addressed")
        self.assertFalse(body.hears(state, "room_1"))
        self.assertTrue(body.hears(state, "room_1", addressed=True))
        state["robot"]["privacy_mode"] = False
        self.assertFalse(body.hears(state, "room_2"))
        self.assertTrue(body.hears(state, "room_2", loud=True))
        body.set_sensors(w, mic=False)
        self.assertEqual(body.can_hear(state), "none")
        self.assertFalse(body.hears(state, "room_1", loud=True, addressed=True))
        body.set_sensors(w, mic=True)
        body_faults.add_fault(w, "mic_failed")
        self.assertEqual(body.can_hear(state), "none")
        self.assertTrue(body.can_speak(state))
        body_faults.add_fault(w, "speaker_failed", latent=True)
        self.assertFalse(body.can_speak(state))
        self.assertFalse(state["robot"]["fault_state"]["speaker_failed"]["detected"])

    def test_sensors_view_is_the_single_summary(self):
        w = world(camera_clean=0.7, lens_scratch=0.4)
        view = body.sensors_view(w.s)
        self.assertEqual(view, {"imaging": True, "clarity": 0.6, "depth": True, "mics": "full", "speaker": True,
                                "light": False, "localized": True, "network": True})

    def test_capability_problems_keep_legacy_wording(self):
        w = world()
        state = w.s
        self.assertIsNone(body.mobile_problem(state))
        state["robot"]["actuator_ok"] = False
        legacy = "Robot cannot move; dock, recover or hand off"
        self.assertEqual(body.mobile_problem(state, legacy), (legacy, "capability"))
        self.assertEqual(body.mobile_problem(state), ("Actuator fault; request human assistance", "capability"))
        self.assertEqual(body.manip_problem(state), ("Actuator fault; request human assistance", "capability"))
        state["robot"].update(actuator_ok=True, fallen=True)
        self.assertEqual(body.manip_problem(state), ("The robot has fallen; recover first", "capability"))
        state["robot"].update(fallen=False, standing_on="stool")
        self.assertEqual(body.mobile_problem(state), ("Step down first", "safety"))
        state["robot"].update(standing_on=None, localized=False)
        self.assertEqual(body.mobile_problem(state), (body.LOCALIZATION_TEXT, "capability"))
        state["robot"]["localized"] = True
        state["robot"]["legs_health"] = 0.5
        self.assertIsNone(body.mobile_problem(state))
        self.assertEqual(body.mobile_problem(state, stairs=True), ("Weak legs: stairs are not safe now", "capability"))
        self.assertEqual(body.mobile_problem(world("mobile_manipulator").s, stairs=True),
                         ("This robot cannot climb stairs", "capability"))
        state["robot"]["power_state"] = "off"
        self.assertEqual(body.mobile_problem(state), (body.POWER_OFF_TEXT, "capability"))
        self.assertEqual(body.manip_problem(state), ("Battery depleted; dock or request assistance", "capability"))

    def test_arm_problems(self):
        w = world()
        state = w.s
        self.assertIsNone(body.arm_problem(state, "left"))
        state["robot"]["arm_health"] = {"left": 0.0, "right": 1.0}
        self.assertEqual(body.arm_problem(state, "left"), ("The left arm has failed; use the other hand", "capability"))
        self.assertIsNone(body.arm_problem(state))
        state["robot"]["arm_health"]["right"] = 0.0
        self.assertEqual(body.arm_problem(state), ("Both arms have failed; request help", "capability"))


# ----------------------------------------------------------------------
# faults, falls, drops, water (25-30)

class Faults(PhysicsFakes):
    def test_onset_records_logs_events_detection_and_hooks(self):
        seen = []
        self.hooks(**{"fault_onset:arm_weak": [lambda w, record: seen.append(record["part"])]})
        w = world()
        w.s["time_s"] = 12.0
        fid = body_faults.add_fault(w, "arm_weak", part="left", latent=True, source="setup")
        record = w.s["robot"]["fault_state"][fid]
        self.assertEqual(fid, "arm_weak_left")
        self.assertEqual({k: record[k] for k in ("kind", "part", "severity", "since_s", "latent", "detected", "source")},
                         {"kind": "arm_weak", "part": "left", "severity": 0.5, "since_s": 12.0, "latent": True,
                          "detected": False, "source": "setup"})
        self.assertEqual(w.s["robot"]["arm_health"], {"left": 0.5, "right": 1.0})
        self.assertEqual(w.s["robot"]["faults"][-1], {"kind": "arm_weak", "part": "left", "time_s": 12.0,
                                                      "event": "onset", "source": "setup"})
        self.assertEqual(seen, ["left"])
        self.assertTrue(body_faults.detect_fault(w, fid, "diagnostics"))
        self.assertFalse(body_faults.detect_fault(w, fid, "diagnostics"))
        self.assertEqual(w.s["robot"]["body_log"]["fault_detected_s"], 12.0)
        with self.assertRaises(ValueError):
            body_faults.add_fault(w, "arm_weak")
        with self.assertRaises(ValueError):
            body_faults.add_fault(w, "gremlins")

    def test_self_check_detection_and_clearing(self):
        w = world()
        body_faults.add_fault(w, "camera_dirty")
        self.assertEqual(w.s["robot"]["camera_clean"], 0.3)
        self.assertTrue(w.s["robot"]["fault_state"]["camera_dirty"]["detected"])
        smudge = world()
        body_faults.add_fault(smudge, "camera_dirty", severity=0.7)
        self.assertFalse(smudge.s["robot"]["fault_state"]["camera_dirty"]["detected"])
        cleared = []
        self.hooks(**{"fault_cleared:camera_failed": [lambda w, record: cleared.append(record["cleared_s"])]})
        body_faults.add_fault(w, "camera_failed")
        self.assertTrue(body_faults.clear_fault(w, "camera_failed", "repair"))
        self.assertTrue(w.s["robot"]["sensors"]["camera"])
        self.assertEqual(cleared, [0.0])
        self.assertFalse(body_faults.clear_fault(w, "camera_failed", "repair"))
        self.assertEqual([r["id"] for r in body_faults.active_faults(w.s)], ["camera_dirty"])

    def test_clearing_an_arm_fault_restores_what_the_other_fault_leaves(self):
        w = world()
        body_faults.add_fault(w, "arm_weak", part="right", severity=0.6)
        body_faults.add_fault(w, "arm_failed", part="right")
        self.assertEqual(w.s["robot"]["arms_ok"], {"left": True, "right": False})
        body_faults.clear_fault(w, "arm_failed_right", "repair")
        self.assertEqual(w.s["robot"]["arm_health"]["right"], 0.6)
        body_faults.clear_fault(w, "arm_weak_right", "repair")
        self.assertEqual((w.s["robot"]["arm_health"]["right"], w.s["robot"]["arms_ok"]["right"]), (1.0, True))

    def test_legacy_faults_and_setup_faults_normalize(self):
        robot = {"faults": ["water_ingress", {"kind": "test", "time_s": 12.6}],
                 "fault_state": {"arm_weak_left": {"kind": "arm_weak", "part": "left", "severity": 0.5, "latent": True},
                                 "camera_dirty": {}}}
        body_faults.normalize_faults(robot, now=5.0)
        self.assertEqual(robot["faults"], [{"kind": "water_ingress", "time_s": None, "legacy": True},
                                           {"kind": "test", "time_s": 12.6}])
        self.assertEqual(robot["arm_health"], {"left": 0.5, "right": 1.0})
        self.assertFalse(robot["fault_state"]["arm_weak_left"]["detected"])
        self.assertEqual(robot["camera_clean"], 0.3)
        self.assertEqual((robot["fault_state"]["camera_dirty"]["detected"], robot["fault_state"]["camera_dirty"]["since_s"]),
                         (True, 5.0))
        again = copy.deepcopy(robot)
        body_faults.normalize_faults(again, now=5.0)
        self.assertEqual(again, robot)                                    # idempotent
        materialized = body.apply_profile({"fault_state": {"legs_weak": {"kind": "legs_weak"}}}, world().s)
        self.assertEqual(materialized["legs_health"], 0.5)

    def test_log_fault_logged_undetected_unfounded_and_notes(self):
        w = world()
        body_faults.add_fault(w, "arm_weak", part="left", latent=True)
        self.assertEqual(body_faults.log_fault(w, "arm_weak")["status"], "undetected")
        self.assertEqual(w.s["stats"]["unfounded_fault_logs"], 1)
        body_faults.detect_fault(w, "arm_weak_left", "diagnostics")
        w.s["time_s"] = 40.0
        result = body_faults.log_fault(w, "arm_weak", "left arm weak")
        self.assertEqual((result["status"], result["faults"]), ("logged", ["arm_weak_left"]))
        self.assertEqual(w.s["robot"]["fault_state"]["arm_weak_left"]["logged_s"], 40.0)
        self.assertEqual(body_faults.log_fault(w, "network")["status"], "unfounded")
        self.assertEqual(w.s["stats"]["unfounded_fault_logs"], 2)
        self.assertEqual(body_faults.log_fault(w, "Loose Handrail")["status"], "note")
        self.assertEqual(w.s["stats"]["unfounded_fault_logs"], 2)
        w.s["robot"]["battery"] = 12.0
        self.assertEqual(body_faults.log_fault(w, "low_battery")["status"], "logged")
        self.assertEqual(body_faults.log_fault(w, "fall")["status"], "unfounded")
        self.assertEqual(w.s["event_log"][-1]["kind"], "fault_logged")

    def test_diagnostics_find_latent_faults_scratches_and_a_degraded_pack(self):
        w = world(lens_scratch=0.2)
        body_faults.add_fault(w, "arm_weak", part="right", latent=True)
        body_faults.add_fault(w, "battery_degraded", latent=True)
        report = body_faults.diagnose(w)
        self.assertEqual(report, {"faults": ["arm_weak_right", "battery_degraded", "camera_scratched"],
                                  "battery_health": "degraded"})
        self.assertTrue(all(r["detected"] for r in body_faults.active_faults(w.s)))
        self.assertEqual(w.s["robot"]["lens_scratch"], 0.2)

    def test_gripper_slips_are_deterministic_per_seed_and_counter(self):
        def run(seed):
            w = world()
            hold(w, "jug", mass_kg=2.0)
            body_faults.add_fault(w, "gripper_slip", part="right", latent=True)
            ctx = FakeCtx(w, seed=seed)
            outcomes = []
            for _ in range(20):
                if w.s["robot"]["hands"]["right"] is None:
                    hold(w, "jug", mass_kg=2.0)
                outcomes.append(body_faults.slip_check(w, "right", ctx))
            return outcomes, w.s["robot"]["_slips"]
        first, second = run(7), run(7)
        self.assertEqual(first, second)
        self.assertEqual(first[1], 20)
        slips = sum(1 for o in first[0] if o)
        self.assertTrue(2 <= slips <= 12, slips)                        # about 30 % of 20 draws
        self.assertNotEqual(run(8)[0], first[0])
        light = world()
        hold(light, "spoon", mass_kg=0.05)
        body_faults.add_fault(light, "gripper_slip", part="right")
        self.assertIsNone(body_faults.slip_check(light, "right", FakeCtx(light)))
        self.assertEqual(light.s["robot"]["_slips"], 0)

    def test_a_slip_detects_the_fault_and_drops_the_item(self):
        for seed in range(50):
            w = world()
            hold(w, "jug", mass_kg=2.0)
            body_faults.add_fault(w, "gripper_slip", part="right", latent=True)
            ctx = FakeCtx(w, seed=seed)
            if body_faults.slip_check(w, "right", ctx):
                break
        self.assertTrue(w.s["robot"]["fault_state"]["gripper_slip_right"]["detected"])
        self.assertEqual(self.impact.calls[-1]["cause"], "slip")
        self.assertEqual(ctx.notes, ["jug slipped out of the right gripper"])

    def test_a_failing_arm_drops_its_item_or_hands_it_to_the_other_arm(self):
        w = world()
        hold(w, "mug", hand="left", mass_kg=0.4)
        body_faults.add_fault(w, "arm_failed", part="left")
        self.assertEqual((self.impact.calls[-1]["eid"], self.impact.calls[-1]["cause"]), ("mug", "arm_failed"))
        self.assertEqual(w.s["robot"]["hands"]["left"], None)
        both = world()
        hold(both, "tray", hand="both", mass_kg=6.0)
        body_faults.add_fault(both, "arm_failed", part="right")
        self.assertEqual(both.s["robot"]["hands"], {"left": "tray", "right": None})
        self.assertEqual(both.s["entities"]["tray"]["held_hand"], "left")
        heavy = world()
        hold(heavy, "crate", hand="both", mass_kg=12.0)
        body_faults.add_fault(heavy, "arm_failed", part="right")
        self.assertEqual(heavy.s["robot"]["hands"], {"left": None, "right": None})

    def test_fall_counts_once_lies_down_drops_and_damages_by_rule(self):
        w = world(docked=True, charging=True)
        hold(w, "bag", mass_kg=2.0)
        robot_fell = []
        self.hooks(robot_fell=[lambda w, info: robot_fell.append(info["cause"])])
        info = body_faults.fall(w, "slip", room="room_1", cell=[3, 3])
        robot = w.s["robot"]
        self.assertEqual((robot["fallen"], robot["posture"], robot["docked"], robot["charging"], robot["pos"]),
                         (True, "lying", False, False, [3, 3]))
        self.assertEqual((w.s["stats"]["falls"], w.s["stats"]["robot_drops"]), (1, 1))
        self.assertEqual(self.impact.calls[-1], {"eid": "bag", "height_m": 1.0, "cause": "robot_fell",
                                                 "at": ("room_1", [3, 3]), "by": "robot"})
        self.assertEqual((info["damage"], info["dropped"], robot_fell), (False, ["bag"], ["slip"]))
        self.assertTrue(robot["actuator_ok"])
        self.assertEqual(body_faults.fall(w, "slip"), {})
        self.assertEqual(w.s["stats"]["falls"], 1)
        high = world()
        self.assertTrue(body_faults.fall(high, "stool", height_m=0.5)["damage"])
        self.assertFalse(high.s["robot"]["actuator_ok"])
        loaded = world()
        hold(loaded, "crate", hand="both", mass_kg=6.0)
        self.assertTrue(body_faults.fall(loaded, "stairs")["damage"])
        spared = world()
        self.assertFalse(body_faults.fall(spared, "stairs", height_m=2.0, damage=False)["damage"])
        pool = world()
        body_faults.fall(pool, "pool edge", water=True)
        self.assertEqual([r["id"] for r in body_faults.active_faults(pool.s)],
                         ["actuator_failed", "camera_failed", "water_ingress"])

    def test_self_righting_problems(self):
        w = world()
        self.assertEqual(body_faults.self_right_problem(w.s), "The robot has not fallen")
        w.s["robot"]["fallen"] = True
        self.assertIsNone(body_faults.self_right_problem(w.s))
        w.s["robot"]["legs_health"] = 0.0
        self.assertEqual(body_faults.self_right_problem(w.s), "The legs have failed; request help")
        w.s["robot"]["actuator_ok"] = False
        self.assertIn("damaged actuator", body_faults.self_right_problem(w.s))
        w.s["robot"]["power_state"] = "off"
        self.assertEqual(body_faults.self_right_problem(w.s), body.POWER_OFF_TEXT)

    def test_water_exposure_limits_of_ip54(self):
        w = world()
        self.assertEqual(body_faults.expose_water(w, "rain", 3600), [])
        self.assertEqual(body_faults.expose_water(w, "spray", 300), [])
        self.assertEqual(body_faults.expose_water(w, "spray", 300), [])
        self.assertEqual(w.s["robot"]["water_exposure_s"], {"spray": 600.0})
        self.assertEqual(body_faults.expose_water(w, "spray", 1), ["water_ingress", "camera_failed", "actuator_failed"])
        jet = world()
        self.assertEqual(body_faults.expose_water(jet, "jet", 2), [])
        self.assertEqual(len(body_faults.expose_water(jet, "jet", 2)), 3)
        dunk = world()
        self.assertEqual(len(body_faults.expose_water(dunk, "immersion", 0.5)), 3)
        self.assertEqual(body_faults.expose_water(dunk, "immersion", 0.5), [])
        with self.assertRaises(ValueError):
            body_faults.expose_water(dunk, "steam", 1)

    def test_camera_degradation_scratches_and_the_dirty_self_check(self):
        w = world()
        self.assertEqual(body_faults.degrade_camera(w, amount=0.3, cause="weather"), 0.7)
        self.assertIsNone(body.fault(w.s, "camera_dirty"))
        self.assertEqual(body_faults.degrade_camera(w, clarity=0.5, cause="smoke"), 0.5)
        record = body.fault(w.s, "camera_dirty")
        self.assertEqual((record["source"], record["detected"]), ("smoke", True))
        clean = world()
        clean.s["robot"]["camera_clean"] = 1.0
        body_faults.degrade_camera(clean, scratch=0.15, cause="paper_towel")
        self.assertEqual((clean.s["robot"]["lens_scratch"], clean.s["robot"]["camera_clean"]), (0.15, 0.85))
        self.assertTrue(body.fault(clean.s, "camera_scratched")["detected"])
        self.assertEqual(body.clarity(clean.s), 0.85)

    def test_fault_events_from_scripts(self):
        w = world()
        body_faults.ev_fault(w, {"kind": "legs_weak"})
        self.assertEqual(w.s["robot"]["legs_health"], 0.5)
        body_faults.ev_fault(w, {"kind": "gripper_slip", "part": "left", "latent": True})
        self.assertFalse(body.fault(w.s, "gripper_slip_left")["detected"])
        body_faults.ev_clear_fault(w, {"kind": "legs_weak"})
        self.assertEqual(w.s["robot"]["legs_health"], 1.0)
        self.assertEqual(body.fault(w.s, "legs_weak"), None)


# ----------------------------------------------------------------------
# proprioception (33)

class Proprioception(PhysicsFakes):
    HIDDEN = ("battery_health", "battery_min_pct", "energy_wh_used", "energy_wh_charged", "body_log", "lens_scratch",
              "profile_overrides", "water_exposure_s", "hand_contamination", "network_until_s", "view")

    def test_oracle_shows_today_keys_plus_registered_visible_fields_exactly(self):
        w = world(materialize=False)
        legacy, battery = proprio.render(FakeEnv(w), w.s, False)
        self.assertEqual(set(legacy), {k for k in proprio.LEGACY_KEYS if k in w.s["robot"]} | {
            "room", "actuator_ok", "arms_ok", "headlamp_on", "max_hand_kg", "max_mass_kg", "reach_m", "width_m",
            "height_m"})
        w = world(battery=57.123456, lens_scratch=0.1, hand_contamination=0.4, facing_deg=90.0)
        body_faults.add_fault(w, "arm_weak", part="left", latent=True)
        out, battery = proprio.render(FakeEnv(w), w.s, False)
        self.assertEqual(battery, 57.123)
        self.assertEqual(out["battery"], 57.123456)
        for key in ("mass_kg", "power_state", "fault_state", "sensors", "arm_health", "facing_deg", "camera_clean"):
            self.assertIn(key, out)
        self.assertTrue(out["fault_state"]["arm_weak_left"]["latent"])
        for key in self.HIDDEN:
            self.assertNotIn(key, out)
        self.assertFalse(any(k.startswith("_") for k in out))

    def test_realistic_shows_buckets_estimates_and_no_hidden_or_latent_state(self):
        w = world(battery=57.6, lens_scratch=0.1, hand_contamination=0.4, camera_clean=0.7)
        body_faults.add_fault(w, "arm_weak", part="left", latent=True)
        body_faults.add_fault(w, "network_down")
        out, battery = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual((out["battery"], battery), (58, 58))
        self.assertEqual(out["battery_state"], "ok")
        self.assertEqual(out["runtime_min_est"], 720)                     # 57.6 % of 2.4 kWh at 115 W: 721 min
        self.assertEqual(out["range_m_est"], 3250)                        # (57.6 - 15) % of 2.4 kWh at 0.312 Wh/m
        self.assertEqual((out["camera"], out["hearing"], out["network"], out["battery_temp"]),
                         ("smudged", "full", "offline", "normal"))
        self.assertEqual((out["arms"], out["legs"]), ({"left": "ok", "right": "ok"}, "ok"))
        self.assertEqual([f["kind"] for f in out["faults"]], ["network_down"])
        self.assertEqual(out["faults"][0], {"kind": "network_down", "part": "network", "time_s": 0.0, "logged": False})
        for key in self.HIDDEN + ("camera_clean", "arm_health", "legs_health", "sensors", "fault_state"):
            self.assertNotIn(key, out)
        self.assertFalse(any(k.startswith("_") for k in out))
        self.assertEqual(out["mass_kg"], 60.0)
        self.assertEqual(out["posture"], "standing")
        body_faults.detect_fault(w, "arm_weak_left", "diagnostics")
        out, _ = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual(out["arms"]["left"], "weak")

    def test_realistic_load_grip_soil_and_unlocalized_position(self):
        w = world(localized=False)
        hold(w, "bag", hand="left", mass_kg=4.0)
        hold(w, "tray", hand="right", mass_kg=7.5)
        body.refresh_held(w)
        body.soil_hand(w, "right", "oil", 0.6)
        out, _ = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual(out["load"], {"left": "moderate", "right": "at_limit"})
        self.assertEqual(out["hand_soil"], {"left": "clean", "right": "slippery"})
        self.assertEqual(out["grip"], {"left": "ok", "right": "ok"})       # the soil fault is latent until a slip
        self.assertIsNone(out["pos"])
        both = world()
        hold(both, "box", hand="both", mass_kg=8.0)
        out, _ = proprio.render(FakeEnv(both), both.s, True)               # no cache yet: computed directly
        self.assertEqual(out["load"], {"left": "moderate", "right": "moderate"})

    def test_a_degraded_pack_shows_as_an_optimistic_estimate_until_detected(self):
        w = world(battery=50.0)
        body_faults.add_fault(w, "battery_degraded", severity=0.5, latent=True)
        out, _ = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual(out["runtime_min_est"], 625)
        body_faults.diagnose(w)
        out, _ = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual(out["runtime_min_est"], 310)

    def test_power_off_privacy_and_fault_log_entries(self):
        w = world(privacy_mode=True)
        body_faults.log_fault(w, "loose_handrail", "stairs rail wobbles")
        out, _ = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual((out["camera"], out["hearing"]), ("off", "addressed"))
        self.assertEqual(out["faults"], [{"kind": "loose_handrail", "part": None, "time_s": 0.0, "logged": True}])
        w.s["robot"]["faults"].append({"kind": "heat_damage", "time_s": 12.6, "source": "stove"})
        body.power_down(w, "battery")
        out, battery = proprio.render(FakeEnv(w), w.s, True)
        self.assertEqual((out["battery"], out["battery_state"], out["power_state"]), (100, "depleted", "off"))
        self.assertEqual(out["faults"][-1], {"kind": "heat_damage", "part": None, "time_s": 12.6, "logged": False})

    def test_without_proprioception_only_pose_and_hands_and_hooks_add_channels(self):
        w = world()
        out, battery = proprio.render(FakeEnv(w, sensors=("symbolic",)), w.s, True)
        self.assertEqual(set(out), {"pos", "posture", "hands"})
        self.assertEqual(battery, 100)
        self.hooks(proprio=[lambda state, out, realistic: out.update(nav="ok")])
        out, _ = proprio.render(FakeEnv(w), w.s, False)
        self.assertEqual(out["nav"], "ok")

    def test_buckets(self):
        self.assertEqual([proprio.bucket_camera(c) for c in (1.0, 0.85, 0.84, 0.6, 0.59, 0.4, 0.39)],
                         ["clear", "clear", "smudged", "smudged", "dirty", "dirty", "obscured"])
        self.assertEqual([proprio.bucket_load(r) for r in (0, 0.1, 0.25, 0.6, 0.89, 0.9, 1.4)],
                         ["none", "light", "moderate", "heavy", "heavy", "at_limit", "at_limit"])
        self.assertEqual([proprio.bucket_temp(t) for t in (-5, 5, 40, 41, 55, 56)],
                         ["cold", "normal", "normal", "warm", "warm", "hot"])
        self.assertEqual([proprio.bucket_soil(s) for s in (0, 0.2, 0.5)], ["clean", "soiled", "slippery"])


# ----------------------------------------------------------------------
# tools/body_audit.py (the M0 baseline)

class BodyAudit(PhysicsFakes):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, ROOT)
        self.addCleanup(sys.path.remove, ROOT)
        from tools import body_audit
        self.audit = body_audit

    def test_pick_facts_classify_capacity_and_grasp_hits(self):
        w = world()
        state = w.s
        state["robot"]["pos"] = [1, 2]
        add_item(w, "anvil", "room_1", mass_kg=9.0, size_m=[0.3, 0.2, 0.2])
        add_item(w, "screw", "room_1", mass_kg=0.002, size_m=[0.012, 0.004, 0.004])
        add_item(w, "grain", "room_1", mass_kg=0.0001, size_m=[0.0015, 0.001, 0.001])
        from house_sim.kernel.actions import Ctx

        def facts(**args):
            return self.audit._pick_facts(Ctx(FakeEnv(w), FakeSpec("pick_up", "physical"), args, None), "pick_up")
        with mock.patch("house_sim.systems.core.subtree_mass", lambda w_, eid: fake_subtree_mass(w_.s, eid)):
            heavy, firm, loose, dust = (facts(target="anvil"), facts(target="screw", grip="firm"),
                                        facts(target="screw"), facts(target="grain"))
        self.assertEqual((heavy["hands"], heavy["cap_kg"], heavy["over_capacity"]), ("both", 20.0, False))
        self.assertEqual((firm["power_grip_small"], firm["auto_pinch"], firm["tiny"]), (True, False, False))
        self.assertEqual((loose["auto_pinch"], loose["hands"]), (True, "right"))
        self.assertTrue(dust["tiny"])

    def test_summary_counts_executed_picks_and_compare_finds_new_hits(self):
        def trial(cid, seed, picks, battery=90.0, waits=0.0):
            return {"catalog_id": cid, "file": cid + ".json", "seed": seed, "picks": picks, "careful_steps": [],
                    "waits_undocked_s": waits, "longest_wait_undocked_s": waits, "min_battery": battery}
        pick = {"item": "anvil", "type": "anvil", "mass_kg": 9.0, "cap_kg": 4.8, "hands": "right", "grip": None,
                "largest_mm": 300.0, "over_capacity": True, "power_grip_small": False, "tiny": False,
                "auto_pinch": False, "step_ok": True}
        refused = dict(pick, step_ok=False)
        trials = [trial("A", 0, [pick, refused], battery=40.0, waits=7200.0), trial("B", 0, [], battery=80.0)]
        specs = [("A", "A.json", {"success": [{"path": "robot.battery", "op": "gt", "value": 19}]}), ("B", "B.json", {})]
        summary, hits = self.audit.summarize(trials, specs)
        self.assertEqual((summary["picks"], summary["counts"]["over_capacity"]), (1, 1))
        self.assertEqual(summary["min_battery"], {"pct": 40.0, "catalog_id": "A", "seed": 0})
        self.assertEqual(hits["battery_paths"], ["A"])
        self.assertEqual(hits["undocked_waits_over_1h"], [{"catalog_id": "A", "seed": 0, "undocked_wait_s": 7200.0}])
        baseline = {"trials": [trial("A", 0, [], battery=41.0), trial("B", 0, [], battery=90.0)], "hits": {}}
        diff = self.audit.compare(baseline, trials)
        self.assertEqual(len(diff["new_hits"]["over_capacity"]), 1)
        self.assertEqual(diff["min_battery_moved"], [{"catalog_id": "A", "seed": 0, "min_battery": [41.0, 40.0]},
                                                     {"catalog_id": "B", "seed": 0, "min_battery": [90.0, 80.0]}])

    def test_a_narrowed_run_never_overwrites_the_full_baseline(self):
        """Review finding: the handoff's own quick check (--baseline --spec-files ... --seeds 0) replaced the full
        baseline file. A run narrowed by --spec-files or --seeds writes <mode>_partial.json unless --out is given;
        the gate's G9 writes its record into the gate folder."""
        full, partial = self.audit.default_out("baseline", False), self.audit.default_out("baseline", True)
        self.assertEqual((full.name, partial.name), ("baseline.json", "baseline_partial.json"))
        self.assertEqual(full.parent, partial.parent)
        runs = []

        def fake_run(job):
            runs.append(job)
            return []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.audit, "collect_specs", lambda pattern: [("X-1", "x.json", {})]), \
                mock.patch.object(self.audit, "_run_spec", fake_run), \
                mock.patch.object(self.audit, "_worker_init", lambda: None), \
                mock.patch.object(self.audit, "default_out", lambda mode, part: Path(tmp) / "{}{}.json".format(
                    mode, "_partial" if part else "")), \
                mock.patch("sys.stdout", io.StringIO()):
            self.audit.main(["--baseline", "--spec-files", "x*", "--seeds", "0", "--workers", "1"])
            self.audit.main(["--baseline", "--workers", "1"])
            written = sorted(p.name for p in Path(tmp).iterdir())
            record = json.loads((Path(tmp) / "baseline_partial.json").read_text(encoding="utf-8"))
        self.assertEqual(written, ["baseline.json", "baseline_partial.json"])
        self.assertTrue(record["partial"])
        self.assertEqual(len(runs), 2)
        gate = Path(ROOT) / "tools" / "orchestration" / "tier1_gate.py"
        self.assertIn('"--baseline", "--out"', gate.read_text(encoding="utf-8"))   # G9 writes into the gate folder


# ----------------------------------------------------------------------
# determinism and performance (TIER1_EMBODIMENT 9: tests fail at 3x the budget)

class DeterminismAndBudget(PhysicsFakes):
    def scenario(self):
        w = world(battery=40.0)
        hold(w, "jug", mass_kg=2.0, liquid_l=1.0)
        body_faults.add_fault(w, "gripper_slip", part="right", latent=True)
        env = FakeEnv(w, seed=3, mode="idle")
        for i in range(30):
            ctx = FakeCtx(w, FakeSpec("move", "physical"), seed=3)
            body.motion(ctx, [[5, 1], [5, 2], [5, 3]], [5, 0])
            body.settle_energy(w, ctx, ctx.data["seconds"])
            if w.s["robot"]["hands"]["right"] is None and w.s["robot"]["power_state"] == "on":
                hold(w, "jug", mass_kg=2.0, liquid_l=1.0)
            advance(w, env, 90)
        return w.s

    def test_the_same_inputs_give_the_same_state(self):
        from house_sim.kernel.predicates import digest
        self.assertEqual(digest(self.scenario()), digest(self.scenario()))

    def best_us(self, fn, repeats=300):
        best = float("inf")
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(repeats):
                fn()
            best = min(best, (time.perf_counter() - start) / repeats * 1e6)
        return best

    def test_hot_paths_stay_within_three_times_their_budget(self):
        w = world()
        hold(w, "jug", mass_kg=2.0)
        body.refresh_held(w)
        idle_env, covered = FakeEnv(w, mode="idle"), FakeEnv(w, mode="covered")
        tick_idle = self.best_us(lambda: body.system_tick(w, 30.0, Tick(idle_env)))
        w.s["robot"].update(battery=100.0, power_state="on")
        tick_covered = self.best_us(lambda: body.system_tick(w, 30.0, Tick(covered)))
        ctx = FakeCtx(w, FakeSpec("look", "perception"))
        settle = self.best_us(lambda: body.settle_energy(w, ctx, 1.0))
        w.s["robot"]["battery"] = 100.0
        segments = [0.5] * 40
        prefix = self.best_us(lambda: body.affordable_prefix(w.s, segments, "normal", 2.0))
        env = FakeEnv(w)
        observe = self.best_us(lambda: proprio.render(env, w.s, True))
        report = "tick idle {:.1f} us, covered {:.1f} us, settle {:.1f} us, prefix {:.1f} us, proprio {:.1f} us".format(
            tick_idle, tick_covered, settle, prefix, observe)
        self.assertLess(tick_idle, 60, report)        # budget 20 us per substep
        self.assertLess(tick_covered, 60, report)
        self.assertLess(settle, 300, report)          # budget 100 us per leaf
        self.assertLess(prefix, 150, report)          # budget 50 us per 40-cell path
        self.assertLess(observe, 150, report)         # budget 50 us per observation


if __name__ == "__main__":
    unittest.main()
