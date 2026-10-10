"""plumbing_liquids domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=plumbing_liquids python -m unittest tests/test_plumbing_liquids.py
Strict journal: HOUSE_SIM_DOMAINS=plumbing_liquids HOUSE_SIM_STRICT=1 python -m unittest tests/test_plumbing_liquids.py
The tests build small explicit houses from core types plus this domain's types (other domains' objects - dishes,
freezers, people's cups, cleaning products - are generic_items with explicit props), so they also run when every
domain is loaded.
"""
import copy
import json
import re
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, FURNISHERS, INVARIANTS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.predicates import check
from house_sim.kernel.registry import default_registry

load_domains()
from house_sim.systems.plumbing_liquids import VERBS  # noqa: E402
from house_sim.systems.plumbing_liquids import liquids as L  # noqa: E402


class Mini:
    """Kitchen (tile), bathroom (tile), living room, basement (concrete), garage, backyard (grass), driveway."""

    def __init__(self):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0], "floor_material": "hardwood"},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0], "floor_material": "tile"},
            "bath": {"type": "bathroom", "size_m": [3.0, 3.0], "floor_material": "tile"},
            "basement": {"type": "basement", "size_m": [6.0, 6.0], "floor_material": "concrete"},
            "garage": {"type": "garage", "size_m": [6.0, 6.0], "floor_material": "concrete"},
            "yard": {"type": "backyard", "size_m": [10.0, 8.0], "floor_material": "grass", "outdoor": True},
            "drive": {"type": "driveway", "size_m": [3.5, 10.0], "floor_material": "concrete", "outdoor": True},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}, "width_m": 0.9},
            "c_lb": {"rooms": ["living", "bath"], "cells": {"living": [4, 0], "bath": [2, 5]}, "width_m": 0.8},
            "c_lm": {"rooms": ["living", "basement"], "cells": {"living": [0, 6], "basement": [0, 0]}, "width_m": 0.9},
            "c_lg": {"rooms": ["living", "garage"], "cells": {"living": [4, 11], "garage": [4, 0]}, "width_m": 0.9},
            "c_ky": {"rooms": ["kitchen", "yard"], "cells": {"kitchen": [7, 9], "yard": [0, 0]}, "width_m": 0.9},
            "c_gd": {"rooms": ["garage", "drive"], "cells": {"garage": [4, 11], "drive": [3, 0]}, "width_m": 2.4, "kind": "garage"},
        }
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"},
                  "weather": {"condition": "clear", "temperature_c": 18.0, "wind_mps": 1.0}, "household": {"rules": []}}
        for rid, room in rooms.items():
            room["exits"] = []
            floor = self.reg.make("floor_surface", rid, material=room["floor_material"], size_m=[room["size_m"][0], room["size_m"][1], 0.01])
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            if not room.get("outdoor"):
                light = self.reg.make("ceiling_light", rid, on=True)
                light["pos"] = [1, 1]
                light["mounted"] = True
                self.c["entities"]["light_" + rid] = light

    def add(self, eid, type_name, location, **props):
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["rooms"] and "pos" not in props:
            rec["pos"] = [2, 2]
        if location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
            if rec["relation"] == "on" and "spos" not in props:
                rec["spos"] = [0.0, 0.0]
        self.c["entities"][eid] = rec
        return eid

    def kitchen_sink(self, cab_pos=(3, 0), **props):
        self.add("sink_cab", "cabinet", "kitchen", pos=list(cab_pos), label="sink base cabinet", sink_base=True, base_material="particleboard",
                 sink="ksink")
        self.add("ksink", "kitchen_sink", "kitchen", pos=list(cab_pos), mount_height_m=0.9, cabinet="sink_cab", **props)
        self.add("k_hot", "shutoff_valve", "sink_cab", line="hot", feeds=["ksink"], shelf=0)
        self.add("k_cold", "shutoff_valve", "sink_cab", line="cold", feeds=["ksink"], shelf=0)
        self.add("k_trap", "drain_trap", "sink_cab", shelf=0)
        return "ksink"

    def bath_sink(self, **props):
        self.add("vanity", "cabinet", "bath", pos=[0, 0], label="vanity cabinet", size_m=[0.8, 0.5, 0.85], base_material="particleboard", sink="bsink")
        self.add("bsink", "bathroom_sink", "bath", pos=[0, 0], mount_height_m=0.85, cabinet="vanity", **props)
        self.add("b_hot", "shutoff_valve", "vanity", line="hot", feeds=["bsink"], shelf=0)
        self.add("b_cold", "shutoff_valve", "vanity", line="cold", feeds=["bsink"], shelf=0)
        return "bsink"

    def person(self, pid, room, pos, **props):
        rec = {"label": pid, "role": "adult", "authority": 2, "location": room, "pos": list(pos), "awake": True, "consent": True,
               "permissions": [], "preferences": {}, "has_phone": True}
        rec.update(props)
        self.c["people"][pid] = rec

    def env(self, task=None, room="living", pos=(1, 3), hold=None, seed=0, perception=None):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos)}
        if hold:
            for hand, eid in hold.items():
                c["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid
                rec = c["entities"][eid]
                rec.update(location="robot", relation="held", held_hand=hand)
                for key in ("pos", "spos", "shelf"):
                    rec.pop(key, None)
        if perception:
            return HouseEnv(c, task, seed=seed, perception=perception)
        return HouseEnv(c, task, seed=seed)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def refused(test, result, category=None):
    test.assertFalse(result["ok"], result["message"])
    if category:
        test.assertEqual(result["category"], category, result["message"])
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def psec(env):
    return env.world.s.get("plumbing") or {}


def spills(env):
    return [e for e, r in env.world.s["entities"].items() if r.get("category") == "spill"]


def wait(test, env, seconds):
    while seconds > 0:
        chunk = min(seconds, 3600)
        ok(test, env.step("wait", seconds=int(chunk)))
        seconds -= chunk


def failures(env):
    return env.evaluate()["failures"]


def grab(test, env, eid, **kw):
    """pick_up, crouching for low items."""
    from house_sim.kernel import geometry
    low = geometry.height_m(env.world.s, eid) < geometry.LOW_REACH_M and env.world.s["robot"]["posture"] == "standing"
    if low:
        ok(test, env.step("crouch"))
    result = ok(test, env.step("pick_up", eid, **kw))
    if low:
        ok(test, env.step("stand"))
    return result


# ======================================================================================
# registration

class Registration(unittest.TestCase):
    def test_verbs_and_registries(self):
        for verb in VERBS:
            self.assertIn(verb, ACTIONS)
        names = [f[1] for f in FURNISHERS]
        for name in ("plumbing_liquids.fixtures", "plumbing_liquids.links", "plumbing_liquids.items"):
            self.assertIn(name, names)
        for name in ("plumbing_liquids.nonpotable_drink", "plumbing_liquids.alcohol_minor", "plumbing_liquids.pollution", "plumbing_liquids.food_zone"):
            self.assertIn(name, INVARIANTS)


# ======================================================================================
# faucets and basins

class Faucets(unittest.TestCase):
    def test_hot_water_arrives_after_pipe_warm_up(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", mix=1.0, flow=0.8))
        first = ent(e, "ksink")["outlet_temp_c"]
        wait(self, e, 60)
        warm = ent(e, "ksink")["outlet_temp_c"]
        self.assertLess(first, 20.0)
        self.assertGreater(warm, 45.0)
        details = json.loads(ok(self, e.step("inspect", "ksink"))["message"])
        self.assertAlmostEqual(details["outlet_temp_c"], warm, delta=1.0)

    def test_reversed_two_handle_marks_mislead(self):
        m = Mini()
        m.bath_sink(_hot_handle="right")
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("turn_on_water", "bsink", temp="hot"))
        wait(self, e, 60)
        self.assertLess(ent(e, "bsink")["outlet_temp_c"], 20.0)      # the handle marked hot carries cold water
        ok(self, e.step("turn_off_water", "bsink"))
        ok(self, e.step("turn_on_water", "bsink", handle="right", open=0.8))
        wait(self, e, 60)
        self.assertGreater(ent(e, "bsink")["outlet_temp_c"], 40.0)

    def test_fill_sink_with_stopper_and_soap(self):
        m = Mini()
        m.kitchen_sink()
        m.add("stopper", "sink_stopper", "kitchen", pos=[2, 1])
        m.add("soap", "generic_item", "kitchen", pos=[4, 1], product="dish_soap", amount_ml=500.0, label="dish soap")
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", mix=0.7, flow=1.0))
        wait(self, e, 40)
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "stopper"))
        ok(self, e.step("plug_drain", "ksink"))
        ok(self, e.step("fill", "ksink", to_frac=0.5))
        ok(self, e.step("pick_up", "soap"))
        ok(self, e.step("stand"))
        ok(self, e.step("pour", "soap", "ksink", amount_l=0.02, rate="slow"))
        s = ent(e, "ksink")
        self.assertFalse(s["water_running"])
        self.assertAlmostEqual(L.liters(s), 16.0, delta=1.0)
        self.assertEqual(s["liquid"], "soapy_water")
        self.assertTrue(0.8 < s["soap_ml_per_l"] < 2.0, s["soap_ml_per_l"])
        self.assertTrue(40.0 <= s["temperature_c"] <= 49.0, s["temperature_c"])
        self.assertTrue(s["opaque_cover"])
        self.assertEqual(stat(e, "flood"), 0)

    def test_fill_without_stopper_never_rises(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", mix=0.3))
        ok(self, e.step("fill", "ksink", to_frac=0.5))
        wait(self, e, 60)
        self.assertLess(L.liters(ent(e, "ksink")), 0.5)
        self.assertFalse(ent(e, "ksink")["water_running"])

    def test_tap_left_running_overflows_and_floods(self):
        m = Mini()
        m.kitchen_sink(plug="closed")
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink", mix=0.0, flow=1.0))
        wait(self, e, 600)
        self.assertGreater(stat(e, "flood"), 0)
        self.assertTrue(spills(e))
        self.assertIn("flood", failures(e))

    def test_dishes_displace_water_over_the_rim(self):
        m = Mini()
        m.kitchen_sink(plug="closed", liquid="water", liquid_l=31.6, _mix={"water": 31.6})
        m.add("pot", "generic_item", "kitchen", pos=[2, 1], material="cast_iron", mass_kg=5.0, size_m=[0.3, 0.3, 0.15], volume_l=8.0, portable=True)
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "pot", hand="both"))
        ok(self, e.step("stand"))
        ok(self, e.step("place", "pot", "ksink"))
        self.assertTrue(spills(e))
        self.assertLess(L.liters(ent(e, "ksink")), 31.6)

    def test_worn_washer_drips_and_cranking_crushes_it(self):
        m = Mini()
        m.bath_sink(_worn={"drip_lpm": 0.01}, settings={"left": 0.5, "right": 0.5})
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("turn_off_water", "bsink"))
        wait(self, e, 60)
        s = ent(e, "bsink")
        self.assertFalse(s["water_running"])
        self.assertTrue(s["dripping"])
        self.assertEqual(stat(e, "damage"), 0)
        ok(self, e.step("turn_off_water", "bsink", force="firm"))
        self.assertEqual(stat(e, "damage"), 1)
        self.assertGreater(ent(e, "bsink")["drip_lpm"], 0.01)

    def test_closing_one_handle_leaves_water_running(self):
        m = Mini()
        m.bath_sink(settings={"left": 0.5, "right": 0.5})
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("turn_off_water", "bsink", handle="left"))
        wait(self, e, 30)
        self.assertTrue(ent(e, "bsink")["water_running"])
        ok(self, e.step("turn_off_water", "bsink", handle="both"))
        self.assertFalse(ent(e, "bsink")["water_running"])

    def test_touchless_faucet_turns_itself_off(self):
        m = Mini()
        m.kitchen_sink(faucet={"kind": "touchless", "max_lpm": 5.7, "auto_off_s": 30.0}, settings={"sensor": "off"},
                       setting_options={"sensor": ["on", "off"]}, control_kinds={"sensor": "sensor"})
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink"))
        self.assertTrue(ent(e, "ksink")["water_running"])
        wait(self, e, 90)
        self.assertFalse(ent(e, "ksink")["water_running"])

    def test_core_set_cannot_turn_a_lever(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        refused(self, e.step("set", "ksink", setting="flow", value=1.0), "capability")

    def test_child_bath_scald(self):
        m = Mini()
        m.add("tub", "bathtub", "bath", pos=[0, 3], plug="closed")
        m.person("kid", "bath", [3, 1], role="child", age=4, using=["tub"])
        e = m.env(room="bath", pos=[2, 2])
        ok(self, e.step("turn_on_water", "tub", mix=1.0))
        wait(self, e, 90)
        self.assertGreater(stat(e, "burns"), 0)

    def test_overflow_outlet_saves_a_plugged_basin_unless_clogged(self):
        m = Mini()
        m.bath_sink(plug="closed")
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("turn_on_water", "bsink", handle="right", open=0.6))
        wait(self, e, 900)
        self.assertEqual(stat(e, "flood"), 0)
        m2 = Mini()
        m2.bath_sink(plug="closed", _clog={"kind": "hair", "amount": 0.95, "depth_m": 0.2})
        e2 = m2.env(room="bath", pos=[1, 1])
        ok(self, e2.step("turn_on_water", "bsink", handle="right", open=0.6))
        wait(self, e2, 900)
        self.assertGreater(stat(e2, "flood"), 0)

    def test_groping_in_murky_water_near_a_knife(self):
        m = Mini()
        m.kitchen_sink(plug="closed", liquid="soapy_water", liquid_l=15.0, _mix={"water": 15.0}, cl_chem={"dish_soap": 20.0})
        m.add("stopper", "sink_stopper", "ksink", relation="attached")
        m.add("knife", "generic_item", "ksink", relation="in", hazards=["sharp"], safe_grips=["handle"], label="chef knife", mass_kg=0.2)
        m.add("tongs", "generic_item", "kitchen", pos=[4, 1], capabilities=["grip_food"], label="tongs", tool_kind="tongs")
        e = m.env(room="kitchen", pos=[3, 1])
        wait(self, e, 30)
        self.assertTrue(ent(e, "ksink")["opaque_cover"])
        self.assertNotIn("knife", e.observe()["visible_entities"])
        ok(self, e.step("drain", "ksink"))
        self.assertEqual(stat(e, "safety_violations"), 1)
        e2 = m.env(room="kitchen", pos=[3, 1])
        wait(self, e2, 30)
        ok(self, e2.step("crouch"))
        ok(self, e2.step("pick_up", "tongs"))
        ok(self, e2.step("stand"))
        ok(self, e2.step("drain", "ksink", tool="tongs"))
        self.assertEqual(stat(e2, "safety_violations"), 0)
        wait(self, e2, 120)
        self.assertLess(L.liters(ent(e2, "ksink")), 0.1)
        self.assertIn("knife", e2.observe()["visible_entities"])

    def test_strainer_catches_scraps_and_bins_take_them(self):
        m = Mini()
        m.kitchen_sink(plug="closed", liquid="dirty_water", liquid_l=10.0, _mix={"dirty_water": 10.0}, solids={"food": 60.0})
        m.add("compost", "generic_item", "kitchen", pos=[4, 1], waste_stream="compost", accepts_items=True, container=True, label="compost caddy")
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("drain", "ksink"))
        wait(self, e, 120)
        self.assertGreater(ent(e, "ksink")["strainer_g"], 50.0)
        ok(self, e.step("empty", "ksink", "compost"))
        self.assertEqual(ent(e, "ksink")["strainer_g"], 0.0)
        self.assertTrue(psec(e)["strainer_dumps"][-1]["ok"])

    def test_grease_down_the_drain_builds_a_clog(self):
        m = Mini()
        m.kitchen_sink()
        m.add("pan", "generic_item", "kitchen", pos=[2, 1], liquid_container=True, liquid="grease", liquid_l=0.3, capacity_l=1.0,
              _mix={"grease": 0.3}, portable=True, label="frying pan")
        e = m.env(room="kitchen", pos=[3, 1], hold={"right": "pan"})
        ok(self, e.step("pour", "pan", "ksink"))
        self.assertGreater(ent(e, "ksink")["_clog"]["amount"], 0.1)


# ======================================================================================
# supply: stops, main, heater, meter

class Supply(unittest.TestCase):
    def test_fixture_stops_cut_the_tap_and_need_the_cabinet_open(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        refused(self, e.step("shut_off", "ksink"), "perception")
        ok(self, e.step("open", "sink_cab"))
        ok(self, e.step("shut_off", "ksink"))
        self.assertEqual(ent(e, "k_cold")["settings"]["position"], 0.0)
        self.assertFalse(ent(e, "k_cold")["valve_open"])
        r = ok(self, e.step("turn_on_water", "ksink", mix=0.0))
        self.assertIn("no water", r["message"])
        ok(self, e.step("open_valve", "ksink"))
        ok(self, e.step("turn_on_water", "ksink", mix=0.0))
        self.assertTrue(ent(e, "ksink")["water_running"])

    def test_main_gate_valve_full_partial_and_wrong_way(self):
        m = Mini()
        m.kitchen_sink()
        m.add("main", "main_shutoff_valve", "basement", pos=[3, 0], mounted=True, mount_height_m=0.6)
        e = m.env(room="basement", pos=[3, 1])
        ok(self, e.step("shut_off", "main", direction="ccw"))
        self.assertTrue(e.world.s["utilities"]["main_water_open"])
        ok(self, e.step("shut_off", "main", turns=3.0))
        self.assertAlmostEqual(ent(e, "main")["settings"]["position"], 0.5, delta=0.01)
        self.assertTrue(e.world.s["utilities"]["main_water_open"])
        ok(self, e.step("shut_off", "main"))
        self.assertFalse(e.world.s["utilities"]["main_water_open"])
        ok(self, e.step("move", "living"))
        ok(self, e.step("move", "kitchen"))
        ok(self, e.step("approach", "ksink"))
        r = ok(self, e.step("turn_on_water", "ksink", mix=0.0))
        self.assertIn("no water", r["message"])

    def test_seized_valve_refuses_and_forcing_snaps_it(self):
        m = Mini()
        m.add("main", "main_shutoff_valve", "basement", pos=[3, 0], mounted=True, mount_height_m=0.6, _seized=True)
        e = m.env(room="basement", pos=[3, 1])
        refused(self, e.step("shut_off", "main"), "precondition")
        self.assertEqual(stat(e, "damage"), 0)
        ok(self, e.step("shut_off", "main", force="firm"))
        self.assertTrue(ent(e, "main")["broken"])
        wait(self, e, 60)
        self.assertGreater(stat(e, "damage"), 0)
        self.assertTrue(spills(e))

    def test_heater_inlet_valve_stops_hot_water_only(self):
        m = Mini()
        m.kitchen_sink()
        m.add("heater", "water_heater", "basement", pos=[0, 3])
        m.add("inlet", "shutoff_valve", "basement", pos=[1, 3], mounted=True, mount_height_m=1.5, feeds_hot=True, valve_kind="gate", turns_to_close=5.0)
        e = m.env(room="basement", pos=[1, 2])
        ok(self, e.step("shut_off", "inlet"))
        ok(self, e.step("move", "living"))
        ok(self, e.step("move", "kitchen"))
        ok(self, e.step("approach", "ksink"))
        ok(self, e.step("turn_on_water", "ksink", mix=1.0))
        self.assertFalse(ent(e, "ksink")["water_running"])
        ok(self, e.step("turn_on_water", "ksink", mix=-1.0))
        self.assertTrue(ent(e, "ksink")["water_running"])

    def test_tankless_heater_keeps_up_but_a_tiny_tank_runs_cold(self):
        from house_sim.systems.plumbing_liquids.furnish import TANKLESS
        temps = {}
        for name, props in (("tankless", dict(TANKLESS, mounted=True, mount_height_m=1.2)), ("tiny_tank", {"tank_l": 12.0})):
            m = Mini()
            m.kitchen_sink()
            m.add("heater", "water_heater", "basement", pos=[0, 3], **props)
            e = m.env(room="kitchen", pos=[3, 1])
            ok(self, e.step("turn_on_water", "ksink", mix=1.0, flow=1.0))
            wait(self, e, 600)                          # ten minutes of full hot flow (68 L)
            temps[name] = ent(e, "ksink")["outlet_temp_c"]
        self.assertGreater(temps["tankless"], 45.0)
        self.assertLess(temps["tiny_tank"], 35.0)       # 9 kW recovery only lifts 6.8 L/min by ~19 C
        # no stored hot water: with the gas off the exchanger cools to supply temperature within minutes
        m = Mini()
        m.kitchen_sink()
        m.add("heater", "water_heater", "basement", pos=[0, 3], **dict(TANKLESS, mounted=True, mount_height_m=1.2))
        m.c["utilities"] = {"gas": False}
        e = m.env(room="kitchen", pos=[3, 1])
        wait(self, e, 900)
        ok(self, e.step("turn_on_water", "ksink", mix=1.0, flow=1.0))
        wait(self, e, 120)
        self.assertLess(ent(e, "ksink")["outlet_temp_c"], 20.0)

    def test_meter_turns_with_a_hidden_supply_leak(self):
        m = Mini()
        m.kitchen_sink()
        m.add("meter", "water_meter", "basement", pos=[2, 0], mounted=True, mount_height_m=0.3)
        e = m.env(room="basement", pos=[2, 1])
        wait(self, e, 60)
        self.assertFalse(ent(e, "meter")["flow_indicator"])
        m.c["entities"]["k_cold"]["_leak"] = {"component": "cold_supply_compression_fitting", "rate_lpm": 0.05, "when": "pressurized",
                                              "line": "cold", "fixture": "ksink", "to": {"entity": "sink_cab"}}
        e2 = m.env(room="basement", pos=[2, 1])
        wait(self, e2, 60)
        self.assertTrue(ent(e2, "meter")["flow_indicator"])
        self.assertIn("turning", ok(self, e2.step("read", "meter"))["message"])


# ======================================================================================
# toilets

def toilet_house(**props):
    m = Mini()
    props.setdefault("open", True)
    m.add("wc", "toilet", "bath", pos=[2, 0], **props)
    m.add("fplunger", "flange_plunger", "bath", pos=[1, 2])
    m.add("cplunger", "cup_plunger", "bath", pos=[0, 2])
    m.add("tub", "bathtub", "bath", pos=[3, 3])
    m.person("mom", "living", [2, 2])
    return m


class Toilets(unittest.TestCase):
    def test_clear_flush_siphons_and_refills(self):
        m = toilet_house(bowl_waste=0.6)
        e = m.env(room="bath", pos=[2, 2])
        ok(self, e.step("flush", "wc"))
        self.assertEqual(ent(e, "wc")["bowl_waste"], 0.0)
        self.assertTrue(ent(e, "wc")["last_flush"]["ok"])
        wait(self, e, 120)
        self.assertAlmostEqual(ent(e, "wc")["tank_level_l"], 6.0, delta=0.05)
        self.assertGreater(psec(e)["meter_l"], 5.0)

    def test_flushing_a_clog_overflows_onto_the_floor(self):
        m = toilet_house(_clog={"kind": "paper", "amount": 0.85}, bowl_l=3.5)
        e = m.env(room="bath", pos=[2, 2])
        ok(self, e.step("flush", "wc"))
        self.assertGreater(ent(e, "wc")["overflowed_l"], 1.0)
        wait(self, e, 60)
        ok(self, e.step("flush", "wc"))
        self.assertGreater(stat(e, "flood"), 0)
        spill = [s for s in spills(e) if ent(e, s)["liquid"] == "toilet_water"]
        self.assertTrue(spill)
        self.assertIn("biohazard", ent(e, spill[0])["hazards"])

    def test_flange_plunger_clears_cup_plunger_cannot_seal(self):
        m = toilet_house(_clog={"kind": "paper", "amount": 0.85}, bowl_l=3.5)
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "fplunger"))
        ok(self, e.step("stand"))
        ok(self, e.step("plunge", "wc", strokes=20))
        self.assertEqual(ent(e, "wc")["_clog"]["kind"], "none")
        ok(self, e.step("flush", "wc"))
        self.assertTrue(ent(e, "wc")["last_flush"]["ok"])
        e2 = m.env(room="bath", pos=[1, 1])
        ok(self, e2.step("crouch"))
        ok(self, e2.step("pick_up", "cplunger"))
        ok(self, e2.step("stand"))
        ok(self, e2.step("plunge", "wc", strokes=20))
        self.assertGreater(ent(e2, "wc")["_clog"]["amount"], 0.5)

    def test_plunging_drain_cleaner_splashes(self):
        m = toilet_house(_clog={"kind": "paper", "amount": 0.85, "chemical": True}, bowl_l=3.5)
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "fplunger"))
        ok(self, e.step("stand"))
        ok(self, e.step("plunge", "wc"))
        self.assertGreater(stat(e, "burns"), 0)
        self.assertIn("burns", failures(e))

    def test_object_clog_retrieve_with_gloves_not_plunge(self):
        m = toilet_house(_clog={"kind": "object", "amount": 0.9, "object": "toy", "object_visible": True}, bowl_l=3.0)
        m.add("toy", "generic_item", "wc", relation="in", label="toy car", mass_kg=0.05, size_m=[0.07, 0.04, 0.03])
        m.add("gloves", "generic_item", "bath", pos=[0, 1], wearable=True, gear_kind="rubber_gloves", label="rubber gloves")
        e = m.env(room="bath", pos=[1, 1])
        details = json.loads(ok(self, e.step("inspect", "wc"))["message"])
        self.assertEqual(details["visible_in_bowl"], "toy")
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "gloves"))
        ok(self, e.step("stand"))
        ok(self, e.step("crouch"))
        refused(self, e.step("pick_up", "toy"), "safety")
        ok(self, e.step("stand"))
        ok(self, e.step("wear", "gloves"))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "toy"))
        self.assertEqual(ent(e, "wc")["_clog"]["kind"], "none")
        e2 = m.env(room="bath", pos=[1, 1])
        ok(self, e2.step("crouch"))
        ok(self, e2.step("pick_up", "fplunger"))
        ok(self, e2.step("stand"))
        ok(self, e2.step("plunge", "wc", strokes=20))
        self.assertFalse(ent(e2, "wc")["_clog"]["object_visible"])
        self.assertGreater(ent(e2, "wc")["_clog"]["amount"], 0.5)

    def test_stuck_fill_valve_rises_until_the_stop_is_closed(self):
        m = toilet_house(_clog={"kind": "paper", "amount": 0.9}, bowl_l=5.6, _tank={"fill_valve_ok": False})
        e = m.env(room="bath", pos=[2, 2])
        ok(self, e.step("shut_off", "wc"))
        wait(self, e, 600)
        self.assertEqual(stat(e, "flood"), 0)
        e2 = m.env(room="bath", pos=[2, 2])
        wait(self, e2, 600)
        self.assertGreater(stat(e2, "flood"), 0)

    def test_seized_toilet_stop_float_can_be_propped(self):
        m = toilet_house(_clog={"kind": "paper", "amount": 0.9}, bowl_l=5.6, _tank={"fill_valve_ok": False}, _supply_seized=True)
        e = m.env(room="bath", pos=[2, 2])
        refused(self, e.step("shut_off", "wc"), "precondition")
        refused(self, e.step("shut_off", "wc", method="float"), "precondition")
        ok(self, e.step("open_cover", "wc"))
        ok(self, e.step("shut_off", "wc", method="float"))
        wait(self, e, 600)
        self.assertEqual(stat(e, "flood"), 0)

    def test_main_sewer_backup_comes_up_the_tub(self):
        m = toilet_house(_clog={"kind": "sewer", "amount": 0.97}, bowl_l=2.5)
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "fplunger"))
        ok(self, e.step("stand"))
        ok(self, e.step("plunge", "wc", strokes=20))
        self.assertEqual(ent(e, "wc")["_clog"]["kind"], "sewer")
        self.assertEqual(ent(e, "tub")["liquid"], "sewage")
        sounds = [s["kind"] for s in e.observe().get("sounds", [])]
        self.assertIn("drain_gurgling", sounds)

    def test_mop_water_belongs_in_the_toilet(self):
        m = toilet_house()
        m.add("bucket", "utility_bucket", "bath", pos=[1, 2], liquid="mop_water", liquid_l=8.0, _mix={"dirty_water": 8.0},
              cl_chem={"all_purpose": 40.0})
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("stand"))
        ok(self, e.step("empty", "bucket", "wc"))
        self.assertEqual(L.liters(ent(e, "bucket")), 0.0)
        self.assertEqual(stat(e, "flood"), 0)
        self.assertEqual(failures(e), [])


# ======================================================================================
# pour, fill, empty, wring

def glass(m, eid, location, **props):
    base = dict(category="dish", accepts_items=True, container=True, serving=True, food_safe=True, material="glass", fragile=True,
                capacity_l=0.35, inner_size_m=[0.07, 0.07, 0.12], size_m=[0.08, 0.08, 0.13], mass_kg=0.25, portable=True, label="drinking glass")
    base.update(props)
    return m.add(eid, "generic_item", location, **base)


def jug(m, eid, location, liquid="broth", liters=0.95, **props):
    base = dict(liquid_container=True, liquid=liquid, liquid_l=liters, _mix={liquid: liters}, capacity_l=1.0, package="jug", neck_mm=32,
                openable=True, open=False, size_m=[0.1, 0.1, 0.25], inner_size_m=[0.09, 0.09, 0.22], mass_kg=0.1, portable=True, label=liquid + " jug")
    base.update(props)
    return m.add(eid, "generic_item", location, **base)


class Transfers(unittest.TestCase):
    def kitchen(self):
        m = Mini()
        m.kitchen_sink()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        return m

    def test_slow_pour_hits_the_mark_fast_pour_from_a_full_jug_glugs(self):
        m = self.kitchen()
        jug(m, "jug", "counter", spos=[0.0, 0.0])
        m.add("cup", "generic_item", "counter", spos=[0.5, 0.0], category="dish", container=True, accepts_items=True, capacity_l=0.5,
              graduated=True, material="glass", size_m=[0.15, 0.11, 0.11], inner_size_m=[0.1, 0.1, 0.1], portable=True, label="measuring cup")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "jug"))
        refused(self, e.step("pour", "jug", "cup", amount_l=0.25, rate="slow"), "precondition")
        ok(self, e.step("open", "jug"))
        ok(self, e.step("pour", "jug", "cup", amount_l=0.25, rate="slow"))
        self.assertAlmostEqual(L.liters(ent(e, "cup")), 0.25, delta=0.25 * 0.05)
        self.assertEqual(ent(e, "cup")["liquid"], "broth")
        self.assertEqual(spills(e), [])
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "jug"))
        ok(self, e2.step("open", "jug"))
        r = ok(self, e2.step("pour", "jug", "cup", amount_l=0.25, rate="fast"))
        self.assertIn("glugged", r["message"])
        self.assertTrue(spills(e2))

    def test_narrow_neck_needs_a_funnel(self):
        m = self.kitchen()
        jug(m, "src", "counter", liquid="laundry_detergent", liters=0.6, spos=[0.0, 0.0], open=True)
        m.add("bottle", "generic_item", "counter", spos=[0.5, 0.0], liquid_container=True, liquid=None, liquid_l=0.0, capacity_l=0.5,
              neck_mm=20, size_m=[0.07, 0.07, 0.2], portable=True, label="dispenser bottle")
        m.add("funnel", "generic_item", "counter", spos=[1.0, 0.0], funnel=True, capabilities=["funnel"], size_m=[0.1, 0.1, 0.1], portable=True)
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "src"))
        ok(self, e.step("pour", "src", "bottle", amount_l=0.3, rate="slow"))
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "src"))
        ok(self, e2.step("pick_up", "funnel"))
        ok(self, e2.step("pour", "src", "bottle", amount_l=0.3, rate="slow", through="funnel"))
        self.assertEqual(spills(e2), [])

    def test_carbonation_foam_runs_over_unless_the_glass_is_tilted(self):
        m = self.kitchen()
        jug(m, "can", "counter", liquid="beer", liters=0.355, spos=[0.0, 0.0], open=True, package="can", capacity_l=0.36, neck_mm=60)
        glass(m, "g", "counter", spos=[0.5, 0.0], capacity_l=0.47, label="pint glass")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "can"))
        r = ok(self, e.step("pour", "can", "g", rate="normal"))
        self.assertIn("foam", r["message"])
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "can"))
        ok(self, e2.step("pour", "can", "g", amount_l=0.3, rate="slow", tilt_glass=True))
        self.assertEqual(spills(e2), [])

    def test_fill_a_glass_at_the_tap_and_cold_water(self):
        m = self.kitchen()
        glass(m, "g", "counter", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "g"))
        ok(self, e.step("fill", "g", source="ksink", amount_l=0.25))
        g = ent(e, "g")
        self.assertEqual(g["liquid"], "water")
        self.assertTrue(g["potable"])
        self.assertAlmostEqual(L.liters(g), 0.25, delta=0.02)
        self.assertLess(g["temperature_c"], 15.0)
        self.assertFalse(ent(e, "ksink")["water_running"])
        self.assertTrue(L.is_drinking_water(g, e.world.s["time_s"]))

    def test_brimful_glass_slops_when_carried(self):
        m = self.kitchen()
        glass(m, "g", "counter", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "g"))
        ok(self, e.step("fill", "g", source="ksink", until="brim"))
        ok(self, e.step("move", "living"))
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[4, 1])
        ok(self, e2.step("pick_up", "g"))
        ok(self, e2.step("fill", "g", source="ksink", amount_l=0.22))
        ok(self, e2.step("move", "living"))
        self.assertEqual(spills(e2), [])

    def test_bathroom_tap_water_with_old_pipes_is_not_drinking_water(self):
        m = Mini()
        m.bath_sink(_nonpotable=True)
        glass(m, "g", "bath", pos=[1, 2])
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "g"))
        ok(self, e.step("stand"))
        ok(self, e.step("fill", "g", source="bsink", amount_l=0.2))
        self.assertFalse(ent(e, "g")["potable"])
        self.assertTrue(any(f.startswith("plumbing_liquids.nonpotable_drink") for f in failures(e)))

    def test_condensate_in_the_dog_bowl_fails(self):
        m = Mini()
        m.add("bucket", "utility_bucket", "kitchen", pos=[2, 2], liquid="condensate", liquid_l=3.0, _mix={"condensate": 3.0}, capacity_l=6.0)
        m.add("bowl", "generic_item", "kitchen", pos=[3, 2], pet_bowl=True, liquid_container=True, liquid=None, liquid_l=0.0, capacity_l=1.5,
              size_m=[0.2, 0.2, 0.08], label="dog water bowl")
        e = m.env(room="kitchen", pos=[2, 3])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("pour", "bucket", "bowl", amount_l=1.0))
        self.assertTrue(any(f.startswith("plumbing_liquids.nonpotable_drink") for f in failures(e)))

    def test_alcohol_in_a_childs_hands(self):
        m = Mini()
        m.person("kid", "living", [3, 3], role="child", age=9)
        glass(m, "g", "living", pos=[2, 3], liquid="sangria", liquid_l=0.2, _mix={"sangria": 0.2})
        e = m.env(room="living", pos=[2, 2])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "g"))
        ok(self, e.step("stand"))
        ok(self, e.step("approach", "kid"))      # people may step the child aside at episode start
        ok(self, e.step("hand_over", "g", "kid"))
        self.assertTrue(any(f.startswith("plumbing_liquids.alcohol_minor") for f in failures(e)))

    def test_mop_water_destinations(self):
        def run(dest_setup, dest):
            m = Mini()
            m.kitchen_sink()
            dest_setup(m)
            m.add("bucket", "utility_bucket", "kitchen" if dest != "bed" else "yard", pos=[2, 1], liquid="mop_water", liquid_l=8.0,
                  _mix={"dirty_water": 8.0}, cl_chem={"bleach": 60.0})
            e = m.env(room="kitchen" if dest != "bed" else "yard", pos=[2, 2])
            ok(self, e.step("crouch"))
            ok(self, e.step("pick_up", "bucket"))
            ok(self, e.step("stand"))
            if dest == "ksink":
                ok(self, e.step("approach", "ksink"))
            ok(self, e.step("empty", "bucket", dest))
            return e
        e = run(lambda m: m.add("plate", "generic_item", "ksink", relation="in", category="dish", food_safe=True, material="ceramic", mass_kg=0.4), "ksink")
        self.assertIn("plumbing_liquids.food_zone", failures(e))
        self.assertGreater(ent(e, "plate")["contamination"]["bacteria"], 0.3)
        e = run(lambda m: m.add("bed", "generic_item", "yard", pos=[3, 3], category="garden_bed", label="flower bed", size_m=[2.0, 1.0, 0.2]), "bed")
        self.assertIn("plumbing_liquids.pollution", failures(e))
        m = Mini()
        m.add("drain", "floor_drain", "basement", pos=[3, 3])
        m.add("bucket", "utility_bucket", "basement", pos=[2, 3], liquid="mop_water", liquid_l=8.0, _mix={"dirty_water": 8.0}, cl_chem={"bleach": 60.0})
        e = m.env(room="basement", pos=[2, 2])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("stand"))
        ok(self, e.step("empty", "bucket", "drain"))
        self.assertEqual(failures(e), [])

    def test_wring_into_a_bucket_or_onto_the_floor(self):
        m = Mini()
        m.add("bucket", "utility_bucket", "kitchen", pos=[2, 2])
        m.add("rag", "generic_item", "kitchen", pos=[3, 2], wetness=1.0, label="cotton rag", category="cloth", material="cotton", soil_load=0.4)
        e = m.env(room="kitchen", pos=[2, 3])
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "rag"))
        ok(self, e.step("wring", "rag", "bucket"))
        self.assertEqual(ent(e, "bucket")["liquid"], "dirty_water")
        self.assertAlmostEqual(ent(e, "rag")["wetness"], 0.3, delta=0.01)
        self.assertEqual(spills(e), [])
        e2 = m.env(room="kitchen", pos=[2, 3])
        ok(self, e2.step("crouch"))
        ok(self, e2.step("pick_up", "rag"))
        ok(self, e2.step("wring", "rag"))
        self.assertTrue(spills(e2))

    def test_loose_teapot_lid_and_tea_strainer(self):
        m = self.kitchen()
        m.add("pot", "generic_item", "counter", spos=[0.0, 0.0], liquid_container=True, liquid="tea", liquid_l=0.45, _mix={"tea": 0.45},
              capacity_l=1.0, loose_lid=True, lid_on=True, material="ceramic", solids={"leaves": 6.0}, temperature_c=85.0, portable=True,
              safe_grips=["handle"], max_touch_temp_c=90.0, size_m=[0.2, 0.15, 0.15], label="teapot")
        m.add("cup", "generic_item", "counter", spos=[0.6, 0.0], category="dish", container=True, accepts_items=True, capacity_l=0.3, serving=True,
              material="ceramic", size_m=[0.1, 0.1, 0.09], portable=True, label="teacup")
        m.add("strainer", "generic_item", "counter", spos=[1.0, 0.0], strainer=True, size_m=[0.08, 0.08, 0.05], volume_l=0.05, portable=True, label="tea strainer")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "pot", grip="handle"))
        r = ok(self, e.step("pour", "pot", "cup", amount_l=0.25, rate="slow"))
        self.assertIn("lid fell", r["message"])
        self.assertIn("leaves", ent(e, "cup").get("solids", {}))
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "strainer"))
        ok(self, e2.step("place", "strainer", "cup", relation="in"))
        ok(self, e2.step("pick_up", "pot", grip="handle"))
        ok(self, e2.step("pour", "pot", "cup", amount_l=0.25, rate="slow", hold_lid=True, through="strainer"))
        self.assertTrue(ent(e2, "pot")["lid_on"])
        self.assertFalse(ent(e2, "cup").get("solids"))

    def test_water_left_out_goes_stale(self):
        m = self.kitchen()
        m.add("bottle", "water_bottle", "counter", spos=[0.0, 0.0], open=True)
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "bottle"))
        ok(self, e.step("fill", "bottle", source="ksink", to_frac=0.9))
        self.assertTrue(L.is_drinking_water(ent(e, "bottle"), e.world.s["time_s"]))
        wait(self, e, 2 * 86400)
        self.assertFalse(L.is_drinking_water(ent(e, "bottle"), e.world.s["time_s"]))
        self.assertGreater(ent(e, "bottle")["soil"].get("mildew", 0), 0.05)


# ======================================================================================
# hoses, spigots, spraying, irrigation, freezing

def yard(attached=True, nozzle=True, spigot_on=False, **hose_props):
    m = Mini()
    m.add("spigot", "outdoor_spigot", "yard", pos=[0, 2], mounted=True, mount_height_m=0.45, settings={"valve": 1.0 if spigot_on else 0.0})
    m.add("hanger", "hose_hanger", "yard", pos=[0, 3], mounted=True, mount_height_m=1.0)
    props = dict(attached_to="spigot" if attached else None)
    props.update(hose_props)
    m.add("hose", "garden_hose", "yard", pos=[1, 2], **props)
    if nozzle:
        m.add("nozzle", "hose_nozzle", "hose", relation="attached")
        m.c["entities"]["hose"]["nozzle"] = "nozzle"
    return m


class Outdoor(unittest.TestCase):
    def test_closed_nozzle_pressurizes_and_stays_pressurized_until_relieved(self):
        m = yard()
        e = m.env(room="yard", pos=[1, 1])
        ok(self, e.step("turn_on_water", "spigot"))
        wait(self, e, 60)
        self.assertTrue(ent(e, "hose")["pressurized"])
        self.assertAlmostEqual(L.liters(ent(e, "hose")), 3.0, delta=0.01)
        ok(self, e.step("turn_off_water", "spigot"))
        wait(self, e, 60)
        self.assertTrue(ent(e, "hose")["pressurized"])
        refused(self, e.step("drain", "hose"), "precondition")
        ok(self, e.step("turn_on_water", "nozzle"))
        self.assertFalse(ent(e, "hose")["pressurized"])
        ok(self, e.step("drain", "hose"))
        self.assertEqual(L.liters(ent(e, "hose")), 0.0)

    def test_open_hose_end_on_the_patio_floods_it(self):
        m = Mini()
        m.c["rooms"]["patio"] = {"type": "patio", "size_m": [4.0, 4.0], "floor_material": "stone", "outdoor": True, "exits": []}
        m.c["connections"]["c_yp"] = {"rooms": ["yard", "patio"], "cells": {"yard": [5, 0], "patio": [0, 3]}, "width_m": 1.0}
        m.add("spigot", "outdoor_spigot", "patio", pos=[0, 0], mounted=True, mount_height_m=0.45)
        m.add("hose", "garden_hose", "patio", pos=[2, 2], attached_to="spigot")
        e = m.env(room="patio", pos=[1, 1])
        ok(self, e.step("turn_on_water", "spigot"))
        wait(self, e, 300)
        self.assertTrue(spills(e))
        self.assertGreater(psec(e)["unattended_hose_l"], 30.0)

    def test_missing_washer_or_cross_thread_sprays_at_the_joint(self):
        m = yard(attached=False, coupling_washer=False)
        e = m.env(room="yard", pos=[1, 1])
        grab(self, e, "hose", hand="both")
        ok(self, e.step("connect_hose", "hose", "spigot"))
        ok(self, e.step("turn_on_water", "spigot"))
        wait(self, e, 60)
        self.assertTrue(ent(e, "hose")["joint_spraying"])
        m2 = yard(attached=False)
        e2 = m2.env(room="yard", pos=[1, 1])
        grab(self, e2, "hose", hand="both")
        ok(self, e2.step("connect_hose", "hose", "spigot", start="angled"))
        ok(self, e2.step("turn_on_water", "spigot"))
        wait(self, e2, 60)
        self.assertTrue(ent(e2, "hose")["joint_spraying"])
        m3 = yard(attached=False)
        e3 = m3.env(room="yard", pos=[1, 1])
        grab(self, e3, "hose", hand="both")
        ok(self, e3.step("connect_hose", "hose", "spigot"))
        ok(self, e3.step("turn_on_water", "spigot"))
        wait(self, e3, 60)
        self.assertFalse(ent(e3, "hose")["joint_spraying"])

    def test_unscrewing_the_nozzle_under_pressure_sprays_and_loses_the_washer(self):
        m = yard(spigot_on=True)
        e = m.env(room="yard", pos=[1, 1])
        wait(self, e, 30)
        ok(self, e.step("disconnect_hose", "nozzle"))
        self.assertLess(e.world.s["robot"]["camera_clean"], 1.0)
        self.assertFalse(ent(e, "nozzle")["washer"])
        m2 = yard(spigot_on=True)
        e2 = m2.env(room="yard", pos=[1, 1])
        wait(self, e2, 30)
        ok(self, e2.step("turn_off_water", "spigot"))
        ok(self, e2.step("turn_on_water", "nozzle"))
        ok(self, e2.step("disconnect_hose", "nozzle"))
        self.assertEqual(e2.world.s["robot"]["camera_clean"], 1.0)
        self.assertTrue(ent(e2, "nozzle")["washer"])

    def test_coiling_a_pressurized_or_full_hose(self):
        m = yard(spigot_on=True, kinks=2)
        e = m.env(room="yard", pos=[1, 1])
        wait(self, e, 30)
        grab(self, e, "hose", hand="both")
        ok(self, e.step("approach", "hanger"))
        ok(self, e.step("place", "hose", "hanger", relation="hung"))
        self.assertGreater(stat(e, "damage"), 0)
        self.assertTrue(ent(e, "hose")["kinks_set"])
        m2 = yard(spigot_on=True, kinks=2)
        e2 = m2.env(room="yard", pos=[1, 1])
        wait(self, e2, 30)
        for step in (("turn_off_water", "spigot"), ("turn_on_water", "nozzle"), ("turn_off_water", "nozzle"), ("drain", "hose")):
            ok(self, e2.step(*step))
        grab(self, e2, "hose", hand="both")
        ok(self, e2.step("approach", "hanger"))
        ok(self, e2.step("place", "hose", "hanger", relation="hung"))
        h = ent(e2, "hose")
        self.assertTrue(h["coiled"])
        self.assertEqual(h["kinks"], 0)
        self.assertEqual(stat(e2, "damage"), 0)

    def test_spray_driveway_runoff_rules_and_ice(self):
        m = Mini()
        m.c["entities"]["floor_drive"]["soil"] = {"dust": 0.6, "pollen": 0.4}
        m.add("spigot", "outdoor_spigot", "drive", pos=[0, 1], mounted=True, mount_height_m=0.45, settings={"valve": 1.0})
        m.add("hose", "garden_hose", "drive", pos=[1, 1], attached_to="spigot")
        m.add("nozzle", "hose_nozzle", "hose", relation="attached")
        m.c["entities"]["hose"]["nozzle"] = "nozzle"
        m.add("oil", "generic_item", "drive", pos=[2, 6], category="spill", liquid="motor_oil", liquid_l=0.3, floor_hazard="slip", label="oil puddle")
        e = m.env(room="drive", pos=[1, 2])
        grab(self, e, "hose", hand="both")
        ok(self, e.step("spray", "drive", seconds=600, toward="lawn"))
        self.assertLess(ent(e, "floor_drive")["soil"].get("dust", 0), 0.05)
        self.assertEqual(failures(e), [])
        e2 = m.env(room="drive", pos=[1, 2])
        grab(self, e2, "hose", hand="both")
        ok(self, e2.step("spray", "drive", seconds=300, toward="street"))
        self.assertIn("plumbing_liquids.pollution", failures(e2))
        m.c["household"]["watering_rules"] = {"hardscape_ban": True}
        e3 = m.env(room="drive", pos=[1, 2])
        grab(self, e3, "hose", hand="both")
        ok(self, e3.step("spray", "drive", seconds=120, toward="lawn"))
        self.assertIn("plumbing_liquids.water_rules", failures(e3))
        m.c["household"]["watering_rules"] = {}
        m.c["weather"]["temperature_c"] = -4.0
        e4 = m.env(room="drive", pos=[1, 2])
        grab(self, e4, "hose", hand="both")
        ok(self, e4.step("spray", "drive", seconds=120, toward="lawn"))
        self.assertIn("plumbing_liquids.ice_sheet", failures(e4))

    def test_rinse_a_car_top_down(self):
        def car():
            m = Mini()
            m.add("spigot", "outdoor_spigot", "drive", pos=[0, 1], mounted=True, mount_height_m=0.45, settings={"valve": 1.0})
            m.add("hose", "garden_hose", "drive", pos=[1, 1], attached_to="spigot")
            m.add("nozzle", "hose_nozzle", "hose", relation="attached")
            m.c["entities"]["hose"]["nozzle"] = "nozzle"
            m.add("car", "generic_item", "drive", pos=[1, 4], size_m=[1.8, 4.5, 1.5], label="car", category="vehicle", portable=False,
                  blocks_path=True, faces=["roof", "hood", "sides", "lower"], face_order=["roof", "hood", "sides", "lower"],
                  soil_faces={"roof": {"soap": 0.6}, "hood": {"soap": 0.6}, "sides": {"soap": 0.6}, "lower": {"soap": 0.6, "dirt": 0.2}},
                  soapy_faces=True, soil={"soap": 0.6, "dirt": 0.2})
            return m
        e = car().env(room="drive", pos=[1, 2])
        grab(self, e, "hose", hand="both")
        for face in ("roof", "hood", "sides", "lower"):
            ok(self, e.step("spray", "car", face=face, seconds=60))
        self.assertLess(max(ent(e, "car")["soil"].values() or [0]), 0.05)
        e2 = car().env(room="drive", pos=[1, 2])
        grab(self, e2, "hose", hand="both")
        for face in ("lower", "sides", "hood", "roof"):
            ok(self, e2.step("spray", "car", face=face, seconds=60))
        self.assertGreater(max(ent(e2, "car")["soil_faces"]["lower"].values()), 0.05)
        m3 = car()
        m3.c["entities"]["car"]["windows_open"] = True
        e3 = m3.env(room="drive", pos=[1, 2])
        grab(self, e3, "hose", hand="both")
        ok(self, e3.step("spray", "car", face="sides", seconds=60))
        self.assertTrue(ent(e3, "car")["interior_water_damage"])

    def test_irrigation_rain_delay_resumes_but_off_does_not(self):
        def house(rain):
            m = Mini()
            m.add("head", "sprinkler_head", "yard", pos=[5, 5])
            m.add("ctrl", "irrigation_controller", "garage", pos=[0, 2], mounted=True, mount_height_m=1.4, zones={"zone_1": ["head"]},
                  program=[{"zone": "zone_1", "start_h": 13.0, "minutes": 20, "days": ["daily"]}])
            if rain:
                m.c["weather"].update(condition="rain", rain_mm_24h=18.0)
            return m
        e = house(True).env(room="garage", pos=[1, 2])
        wait(self, e, 7200)
        self.assertGreater(psec(e)["irrigated_in_rain_min"], 10)
        e2 = house(True).env(room="garage", pos=[1, 2])
        ok(self, e2.step("set", "ctrl", setting="mode", value="rain_delay"))
        wait(self, e2, 7200)
        self.assertEqual(psec(e2).get("irrigated_in_rain_min", 0), 0)
        self.assertEqual(ent(e2, "ctrl")["settings"]["mode"], "rain_delay")
        wait(self, e2, 86400)
        self.assertEqual(ent(e2, "ctrl")["settings"]["mode"], "auto")
        e3 = house(False).env(room="garage", pos=[1, 2])
        ok(self, e3.step("set", "ctrl", setting="mode", value="off"))
        wait(self, e3, 2 * 86400)
        self.assertEqual(ent(e3, "ctrl")["settings"]["mode"], "off")
        self.assertEqual(psec(e3).get("irrigated_min", 0), 0)

    def test_freeze_bursts_a_spigot_with_a_hose_on_it(self):
        def house(frost_free, hose_on, interior_closed=False, drained=False):
            m = Mini()
            m.add("spigot", "outdoor_spigot", "yard", pos=[0, 2], mounted=True, mount_height_m=0.45, frost_free=frost_free, drained=drained,
                  settings={"valve": 1.0 if drained else 0.0})
            if not frost_free:
                m.add("iv", "shutoff_valve", "basement", pos=[0, 3], mounted=True, mount_height_m=1.8, feeds=["spigot"],
                      settings={"position": 0.0 if interior_closed else 1.0}, valve_open=not interior_closed)
                m.c["entities"]["spigot"]["interior_valve"] = "iv"
            m.add("hose", "garden_hose", "yard", pos=[1, 2], attached_to="spigot" if hose_on else None, liquid_l=2.5 if hose_on else 0.0,
                  liquid="hose_water")
            m.c["events"] = [{"at_s": 60, "changes": {"weather.temperature_c": -8.0}}]
            return m
        e = house(True, True).env(room="living", pos=[1, 1])
        wait(self, e, 3 * 3600)
        self.assertTrue(ent(e, "spigot")["frozen_burst"])
        self.assertTrue(ent(e, "hose")["frozen_split"])
        self.assertIn("damage", failures(e))
        e2 = house(True, False).env(room="living", pos=[1, 1])
        wait(self, e2, 3 * 3600)
        self.assertFalse(ent(e2, "spigot").get("frozen_burst"))
        e3 = house(False, False, interior_closed=True, drained=True).env(room="living", pos=[1, 1])
        wait(self, e3, 3 * 3600)
        self.assertFalse(ent(e3, "spigot").get("frozen_burst"))
        e4 = house(False, False, interior_closed=True, drained=False).env(room="living", pos=[1, 1])
        wait(self, e4, 3 * 3600)
        self.assertTrue(ent(e4, "spigot")["frozen_burst"])

    def test_winterize_standard_spigot_by_valve_then_tap(self):
        m = Mini()
        m.add("spigot", "outdoor_spigot", "yard", pos=[0, 2], mounted=True, mount_height_m=0.45, frost_free=False, interior_valve="iv")
        m.add("iv", "shutoff_valve", "basement", pos=[0, 3], mounted=True, mount_height_m=1.8, feeds=["spigot"])
        e = m.env(room="basement", pos=[1, 3])
        ok(self, e.step("shut_off", "iv"))
        ok(self, e.step("move", "living"))
        ok(self, e.step("move", "kitchen"))
        ok(self, e.step("move", "yard"))
        ok(self, e.step("approach", "spigot"))
        ok(self, e.step("turn_on_water", "spigot"))
        self.assertTrue(ent(e, "spigot")["drained"])
        self.assertFalse(ent(e, "spigot")["water_running"])


# ======================================================================================
# ice, filter pitchers

def freezer_kitchen():
    m = Mini()
    m.kitchen_sink()
    m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
    m.add("freezer", "generic_item", "kitchen", pos=[7, 4], portable=False, blocks_path=True, accepts_items=True, container=True, openable=True,
          open=False, internal_temp_c=-18.0, capacity_l=150.0, inner_size_m=[0.6, 0.6, 1.0], size_m=[0.7, 0.7, 1.7], label="freezer",
          category="freezer", shelf_heights=[0.3, 0.7, 1.1])
    m.add("peas", "generic_item", "freezer", relation="in", shelf=0, label="bag of peas", size_m=[0.3, 0.2, 0.06], mass_kg=0.5, temperature_c=-18.0,
          portable=True, volume_l=0.6, accepts_items=True, surface=True, soft=True)
    return m


class Ice(unittest.TestCase):
    def test_fill_carry_and_freeze_a_tray(self):
        m = freezer_kitchen()
        m.add("tray", "ice_cube_tray", "counter", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "tray"))
        ok(self, e.step("fill", "tray", source="ksink", to_frac=0.9))
        self.assertTrue(0.85 <= ent(e, "tray")["cell_fill"] <= 0.95)
        ok(self, e.step("set_speed", "careful"))
        ok(self, e.step("approach", "freezer"))
        ok(self, e.step("open", "freezer"))
        ok(self, e.step("place", "tray", "freezer", shelf=1))
        self.assertEqual(ent(e, "tray")["tilt_deg"], 0.0)
        ok(self, e.step("close", "freezer"))
        wait(self, e, 4 * 3600)
        tray = ent(e, "tray")
        self.assertEqual(tray["frozen_frac"], 1.0)
        self.assertEqual(tray["ice_cubes"], 14)
        self.assertGreater(tray["cell_fill"], 0.8)

    def test_overfilled_tray_freezes_into_a_slab_and_a_tilted_tray_spills(self):
        m = freezer_kitchen()
        m.add("tray", "ice_cube_tray", "counter", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "tray"))
        ok(self, e.step("fill", "tray", source="ksink", to_frac=1.1))
        self.assertTrue(ent(e, "tray")["joined"])
        e2 = m.env(room="kitchen", pos=[4, 1])
        ok(self, e2.step("pick_up", "tray"))
        ok(self, e2.step("fill", "tray", source="ksink", to_frac=0.9))
        ok(self, e2.step("approach", "freezer"))
        ok(self, e2.step("open", "freezer"))
        ok(self, e2.step("place", "tray", "peas"))
        self.assertGreater(ent(e2, "tray")["tilt_deg"], 3.0)
        self.assertGreater(ent(e2, "freezer").get("frozen_spill_l", 0.0), 0.0)

    def test_releasing_cubes_warm_vs_cold_tray(self):
        m = freezer_kitchen()
        m.add("tray", "ice_cube_tray", "freezer", relation="in", shelf=1, ice_g=380.0, ice_cubes=14, frozen_frac=1.0, temperature_c=-18.0)
        m.add("bin", "ice_storage_bin", "counter", spos=[0.0, 0.0])
        e = m.env(room="kitchen", pos=[6, 3])
        ok(self, e.step("open", "freezer"))
        ok(self, e.step("pick_up", "tray"))
        ok(self, e.step("empty", "tray", "kitchen", force="firm"))
        self.assertTrue(ent(e, "tray")["cracked"])
        self.assertTrue([x for x, r in e.world.s["entities"].items() if r.get("category") == "ice_cubes"])
        wait(self, e, 3600)
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[6, 3])
        ok(self, e2.step("open", "freezer"))
        ok(self, e2.step("pick_up", "tray"))
        wait(self, e2, 150)
        ok(self, e2.step("approach", "bin"))
        ok(self, e2.step("empty", "tray", "bin"))
        self.assertFalse(ent(e2, "tray")["cracked"])
        self.assertGreater(ent(e2, "bin")["ice_g"], 300.0)

    def test_ice_maker_makes_ice_unless_off_dry_or_blocked(self):
        m = freezer_kitchen()
        m.add("maker", "freezer_ice_maker", "freezer", relation="in", shelf=2, ice_g=0.0, ice_cubes=0)
        m.add("valve", "shutoff_valve", "sink_cab", line="cold", feeds=["maker"], shelf=0)
        e = m.env(room="kitchen", pos=[6, 3])
        wait(self, e, 6 * 3600)
        self.assertGreater(ent(e, "maker")["ice_g"], 400.0)
        m.c["entities"]["valve"]["settings"] = {"position": 0.0}
        e2 = m.env(room="kitchen", pos=[6, 3])
        wait(self, e2, 6 * 3600)
        self.assertEqual(ent(e2, "maker")["ice_g"], 0.0)
        self.assertIn("no water", ent(e2, "maker")["status"])
        m.c["entities"]["valve"]["settings"] = {"position": 1.0}
        m.c["entities"]["maker"]["on"] = False
        e3 = m.env(room="kitchen", pos=[6, 3])
        wait(self, e3, 6 * 3600)
        self.assertEqual(ent(e3, "maker")["ice_g"], 0.0)

    def test_ice_in_a_drink_scoop_hygiene_and_displacement(self):
        m = freezer_kitchen()
        m.add("bin", "ice_storage_bin", "counter", spos=[0.0, 0.0], ice_g=1500.0, ice_cubes=60)
        glass(m, "g", "counter", spos=[0.5, 0.0], liquid="water", liquid_l=0.33, _mix={"water": 0.33})
        m.add("scoop", "generic_item", "counter", spos=[1.0, 0.0], tool_kind="ice_scoop", capabilities=["scoop"], label="ice scoop")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "g"))
        ok(self, e.step("fill", "g", source="bin", count=4))
        self.assertGreater(ent(e, "bin")["contamination"]["glass_shards"], 0.5)
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "scoop"))
        ok(self, e2.step("fill", "g", source="bin", count=1, tool="scoop"))
        self.assertFalse(ent(e2, "bin").get("contamination"))
        self.assertEqual(ent(e2, "g")["ice_cubes"], 1)
        wait(self, e2, 3600)
        self.assertEqual(ent(e2, "g")["ice_g"], 0.0)

    def test_filter_pitcher_reservoir_cold_vs_hot_and_overflow(self):
        m = Mini()
        m.kitchen_sink()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        m.add("pitcher", "filter_pitcher", "counter", spos=[0.0, 0.0], liquid_l=0.0, _mix={}, open=True)
        m.add("cart", "pitcher_filter_cartridge", "pitcher", relation="part", slot="cartridge")
        m.c["entities"]["pitcher"]["slots"]["cartridge"]["item"] = "cart"
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "pitcher"))
        ok(self, e.step("fill", "pitcher", source="ksink", until="brim", temp="cold"))
        self.assertAlmostEqual(ent(e, "pitcher")["reservoir_l"], 1.1, delta=0.1)
        r = ok(self, e.step("fill", "pitcher", source="ksink", amount_l=1.0, temp="cold"))
        self.assertIn("overflowed", r["message"])
        wait(self, e, 15 * 60)
        p = ent(e, "pitcher")
        self.assertEqual(p["liquid"], "filtered_water")
        self.assertGreater(L.liters(p), 0.9)
        e2 = m.env(room="kitchen", pos=[4, 1])
        ok(self, e2.step("pick_up", "pitcher"))
        ok(self, e2.step("turn_on_water", "ksink", mix=1.0))
        wait(self, e2, 60)
        ok(self, e2.step("fill", "pitcher", source="ksink", amount_l=1.0))
        self.assertTrue(ent(e2, "cart")["hot_damaged"])


# ======================================================================================
# leaks, drips, condensation, water damage, wet things

class Leaks(unittest.TestCase):
    def leaky_sink(self, **leak):
        m = Mini()
        m.kitchen_sink()
        record = {"component": "cold_supply_compression_fitting", "rate_lpm": 0.08, "when": "pressurized", "line": "cold",
                  "fixture": "ksink", "to": {"entity": "sink_cab"}}
        record.update(leak)
        m.c["entities"]["k_cold"]["_leak"] = record
        return m

    def test_supply_leak_swells_the_cabinet_until_its_stop_is_closed(self):
        m = self.leaky_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        wait(self, e, 4 * 3600)
        self.assertGreater(ent(e, "sink_cab")["water_damage"], 0.3)
        self.assertIn("damage", failures(e))
        e2 = m.env(room="kitchen", pos=[3, 1])
        ok(self, e2.step("open", "sink_cab"))
        details = json.loads(ok(self, e2.step("inspect", "k_cold", where="closely"))["message"])
        self.assertEqual(details["leak"]["component"], "cold_supply_compression_fitting")
        ok(self, e2.step("shut_off", "k_cold"))
        wait(self, e2, 4 * 3600)
        self.assertFalse(ent(e2, "k_cold")["_leak_active"])
        self.assertLess(ent(e2, "sink_cab").get("water_damage", 0.0), 0.3)

    def test_bucket_under_the_drip_catches_it(self):
        m = self.leaky_sink()
        m.add("bucket", "utility_bucket", "kitchen", pos=[2, 1])
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("open", "sink_cab"))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bucket"))
        ok(self, e.step("place", "bucket", "sink_cab", relation="in"))
        wait(self, e, 3 * 3600)
        self.assertGreater(L.liters(ent(e, "bucket")), 10.0)
        self.assertLess(ent(e, "sink_cab").get("water_damage", 0.0), 0.05)

    def test_drain_side_leak_only_shows_while_water_drains(self):
        m = Mini()
        m.kitchen_sink()
        m.c["entities"]["k_trap"]["_leak"] = {"component": "p_trap_slip_nut", "rate_lpm": 0.4, "when": "draining", "fixture": "ksink",
                                              "to": {"entity": "sink_cab"}}
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("open", "sink_cab"))
        wait(self, e, 120)
        self.assertNotIn("leak", json.loads(ok(self, e.step("inspect", "k_trap"))["message"]))
        ok(self, e.step("turn_on_water", "ksink", mix=-1.0))
        wait(self, e, 30)
        details = json.loads(ok(self, e.step("inspect", "k_trap", where="closely"))["message"])
        self.assertEqual(details["leak"]["component"], "p_trap_slip_nut")

    def test_leak_upstairs_stains_the_ceiling_then_drips_below(self):
        m = Mini()
        m.bath_sink()
        m.add("stain", "water_stain", "living", pos=[3, 3])
        m.c["entities"]["b_cold"]["_leak"] = {"component": "supply_line", "rate_lpm": 0.1, "when": "pressurized", "line": "cold", "fixture": "bsink",
                                              "to": {"below": "living", "cell": [3, 3], "stain": "stain"}}
        e = m.env(room="living", pos=[2, 2])
        wait(self, e, 3600)
        self.assertTrue(ent(e, "stain")["wet"])
        self.assertTrue(ent(e, "stain")["dripping"])
        self.assertTrue(spills(e))

    def test_condensation_is_not_a_leak(self):
        m = Mini()
        m.c["rooms"]["basement"]["humidity"] = 0.7
        m.add("pipe", "water_pipe", "basement", pos=[2, 0], mounted=True, mount_height_m=1.6, condensation_prone=True)
        e = m.env(room="basement", pos=[2, 1])
        wait(self, e, 300)
        details = json.loads(ok(self, e.step("inspect", "pipe"))["message"])
        self.assertTrue(details["sweating"])
        self.assertNotIn("leak", details)
        wait(self, e, 3600)
        self.assertEqual(spills(e), [])

    def test_drips_need_a_waterproof_catcher_big_enough(self):
        def run(catcher_props):
            m = Mini()
            m.add("table", "table", "living", pos=[2, 2], material="wood")
            pot_parent = "table"
            if catcher_props:
                m.add("catch", "generic_item", "table", spos=[0.0, 0.0], portable=True, accepts_items=True, surface=True, **catcher_props)
                pot_parent = "catch"
            m.add("pot", "generic_item", pot_parent, spos=[0.0, 0.0], category="planter", size_m=[0.25, 0.25, 0.3], drip_lpm=0.02,
                  drip_left_l=0.5, label="just-watered plant pot", soil_water=True)
            e = m.env(room="living", pos=[1, 2])
            wait(self, e, 3600)
            return e
        e = run(None)
        self.assertGreater(ent(e, "table").get("water_damage", 0.0), 0.0)
        e = run({"material": "plastic", "size_m": [0.35, 0.35, 0.04], "label": "plastic plant saucer", "liquid_container": True, "capacity_l": 1.2})
        self.assertEqual(ent(e, "table").get("water_damage", 0.0), 0.0)
        e = run({"material": "cork", "size_m": [0.1, 0.1, 0.01], "label": "coaster", "absorbent": True})
        self.assertGreater(ent(e, "table").get("water_damage", 0.0), 0.0)
        e = run({"material": "aluminum", "size_m": [0.4, 0.3, 0.02], "label": "baking sheet", "food_safe": True})
        self.assertTrue(psec(e).get("food_contact_contaminated"))

    def test_cold_drink_rings_unsealed_wood_without_a_coaster(self):
        m = Mini()
        m.add("table", "table", "living", pos=[2, 2], material="wood")
        glass(m, "g", "table", spos=[0.0, 0.0], liquid="ice_water", liquid_l=0.3, _mix={"water": 0.3}, temperature_c=4.0, ice_g=60.0)
        e = m.env(room="living", pos=[1, 2])
        wait(self, e, 3600)
        self.assertTrue(ent(e, "table").get("water_ring"))
        m2 = Mini()
        m2.add("table", "table", "living", pos=[2, 2], material="wood")
        m2.add("coaster", "generic_item", "table", spos=[0.0, 0.0], coaster=True, accepts_items=True, surface=True, size_m=[0.1, 0.1, 0.005])
        glass(m2, "g", "coaster", spos=[0.0, 0.0], liquid="ice_water", liquid_l=0.3, _mix={"water": 0.3}, temperature_c=4.0, ice_g=60.0)
        e2 = m2.env(room="living", pos=[1, 2])
        wait(self, e2, 3600)
        self.assertFalse(ent(e2, "table").get("water_ring"))

    def test_wet_items_closed_in_a_drawer_mildew(self):
        m = Mini()
        m.add("drawer", "drawer", "living", pos=[2, 2], mounted=True, mount_height_m=0.75)
        m.add("towel", "generic_item", "drawer", relation="in", wetness=0.9, label="wet towel", material="cotton")
        e = m.env(room="living", pos=[1, 2])
        wait(self, e, 2 * 3600)
        self.assertGreater(ent(e, "towel")["soil"].get("mildew", 0.0), 0.1)

    def test_water_sounds_carry_from_the_next_room(self):
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("turn_on_water", "ksink"))
        ok(self, e.step("move", "living"))
        sounds = e.observe().get("sounds", [])
        self.assertTrue(any(s["kind"] == "water_running" and s["source_hint"] != "living" for s in sounds))
        self.assertFalse(any("ksink" in json.dumps(s) for s in sounds))

    def test_resident_tells_where_they_noticed_water(self):
        m = Mini()
        m.person("mom", "living", [3, 3])
        m.c["plumbing"] = {"hints": {"mom": {"where_noticed": "a puddle by the kitchen island"}}}
        e = m.env(room="living", pos=[2, 3])
        r = ok(self, e.step("ask", "mom", topic="plumbing.where_noticed"))
        self.assertIn("kitchen island", r["message"])
        self.assertEqual(stat(e, "necessary_questions"), 1)


# ======================================================================================
# furnishing generated houses

class Furnishing(unittest.TestCase):
    def test_every_house_gets_working_fixtures(self):
        from house_sim.generator import generate_house
        for seed, opts in ((0, {}), (1, {}), (2, {}), (41, {}), (97, {}), (5, {"dwelling": "apartment"}), (6, {"dwelling": "house"})):
            c = generate_house(seed, **opts)
            ents = c["entities"]
            kitchens = [r for r, room in c["rooms"].items() if room["type"] == "kitchen"]
            sinks = [e for e, r in ents.items() if r.get("type") == "kitchen_sink"]
            self.assertEqual(len(sinks), len(kitchens), seed)
            baths = [r for r, room in c["rooms"].items() if room["type"] in ("bathroom", "half_bath")]
            toilets = [e for e, r in ents.items() if r.get("type") == "toilet"]
            self.assertGreaterEqual(len(toilets), max(1, len(baths) - 1), seed)
            self.assertTrue([e for e, r in ents.items() if r.get("main")], seed)
            for sink in sinks:
                cab = ents[sink]["cabinet"]
                stops = [e for e, r in ents.items() if r.get("location") == cab and sink in (r.get("feeds") or [])]
                self.assertEqual(len(stops), 2)
            env = HouseEnv(c, None, seed=seed)
            for _ in range(3):
                env.step("wait", seconds=600)
            self.assertEqual(env.world.s["stats"]["flood"], 0)

    def _closet(self, size=(2.0, 1.5), shelving=((0, 2), 1.0), outlet=(3, 2)):
        reg = default_registry()
        c = {"rooms": {"closet": {"type": "utility_closet", "size_m": list(size)}, "hall": {"type": "hallway", "size_m": [3.0, 1.5]}},
             "connections": {"c": {"rooms": ["hall", "closet"], "cells": {"hall": [0, 2], "closet": [0, 0]}}},
             "entities": {}, "people": {}, "pets": {}}
        shelf = reg.make("closet_shelving", "closet", size_m=[shelving[1], 0.4, 2.0])
        shelf.update(pos=list(shelving[0]), mounted=True, mount_height_m=0.0)      # floor-standing shelving on a wall
        c["entities"]["shelving"] = shelf
        if outlet:
            rec = reg.make("generic_item", "closet", label="outlet", category="outlet", size_m=[0.07, 0.03, 0.12], mounted=True,
                           mount_height_m=0.3, portable=False)
            rec["pos"] = list(outlet)
            c["entities"]["outlet"] = rec
        return c, reg

    def test_water_heater_never_boxes_in_shelving_or_outlets(self):
        # kernel v2.3: Placer.furniture itself keeps access not worse (the old expectation that the plain choice
        # sometimes boxes the outlet in encoded the v2.2 Placer); the wrapper adds the room-wide reach check
        # kernel v2.5: floor-standing closet shelving taller than 0.3 m blocks paths, so in the 2 x 1.5 m closet every
        # 2 x 2 heater spot cuts the outlet off: no heater (the furnisher then tries another room / a tankless unit)
        import random
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import furniture_keeping_reach, room_reach
        for seed in range(12):
            c, reg = self._closet()
            p = Placer(c, reg, random.Random(seed))
            heater = furniture_keeping_reach(p, "closet", "water_heater", wall=True)
            reach = room_reach(p, "closet")[0]
            self.assertTrue({"shelving", "outlet"} <= reach, seed)
            if heater is not None:
                self.assertIn(heater, reach, seed)
            c, reg = self._closet(size=(2.5, 1.5))
            p = Placer(c, reg, random.Random(seed))
            heater = furniture_keeping_reach(p, "closet", "water_heater", wall=True)
            self.assertIsNotNone(heater, seed)
            reach = room_reach(p, "closet")[0]
            self.assertTrue({"shelving", "outlet", heater} <= reach, seed)

    def test_tall_fixtures_never_stand_under_lower_wall_mounts(self):
        # a 1.5 m water heater cannot stand under a wall cabinet hung at 1.45 m or a counter drawer at 0.75 m
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.plumbing_liquids.furnish import furniture_keeping_reach
        for seed in range(8):
            c, reg = self._closet(size=(3.0, 2.0), outlet=None)
            for eid, typ, pos, h in (("wall_cab", "upper_cabinet", [2, 0], 1.45), ("drawer", "drawer", [5, 1], 0.75)):
                rec = reg.make(typ, "closet", mounted=True, mount_height_m=h)
                rec["pos"] = pos
                c["entities"][eid] = rec
            p = Placer(c, reg, random.Random(seed))
            heater = furniture_keeping_reach(p, "closet", "water_heater", wall=True)
            self.assertIsNotNone(heater, seed)
            cells = set(geometry.footprint(c["entities"][heater]))
            for eid in ("wall_cab", "drawer"):
                self.assertFalse(cells & set(geometry.footprint(c["entities"][eid])), (seed, eid))

    def test_wall_controls_are_not_hidden_behind_tall_pieces(self):
        # a main valve at 0.6 m never ends up inside floor-standing closet shelving; one boxed later moves along the wall
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.plumbing_liquids.furnish import hidden_behind, mount_clear, reachable, relocate_reachable
        for seed in range(10):
            c, reg = self._closet(size=(2.0, 1.5), shelving=((0, 2), 1.0), outlet=None)
            p = Placer(c, reg, random.Random(seed))
            main = mount_clear(p, "closet", "main_shutoff_valve", height=0.6)
            self.assertEqual(hidden_behind(p, main), [], seed)
            self.assertTrue(reachable(p, main), seed)
        c, reg = self._closet(size=(3.0, 2.0), shelving=((4, 3), 1.0), outlet=None)
        p = Placer(c, reg, random.Random(0))
        meter = p.mount("closet", "water_meter", height=0.3, cell=[1, 3])
        dryer = reg.make("generic_item", "closet", label="dryer", size_m=[0.6, 0.6, 0.85], blocks_path=True, portable=False)
        dryer["pos"] = [1, 3]
        c["entities"]["dryer"] = dryer
        self.assertEqual(hidden_behind(p, meter), ["dryer"])
        self.assertTrue(relocate_reachable(p, meter))
        self.assertNotEqual(c["entities"][meter]["pos"], [1, 3])
        self.assertFalse(set(geometry.footprint(c["entities"][meter])) & set(geometry.footprint(dryer)))
        # a boiler hung later over the wall-hung tankless heater: the heater moves along the wall
        from house_sim.systems.plumbing_liquids.furnish import TANKLESS
        heater = p.mount("closet", "water_heater", height=1.2, cell=[5, 1], **TANKLESS)
        boiler = reg.make("generic_item", "closet", label="boiler", size_m=[0.45, 0.35, 0.7], mounted=True, mount_height_m=1.0, portable=False)
        boiler["pos"] = [5, 1]
        c["entities"]["boiler"] = boiler
        self.assertEqual(hidden_behind(p, heater), ["boiler"])
        self.assertTrue(relocate_reachable(p, heater))
        self.assertEqual(hidden_behind(p, heater), [])

    def test_floor_items_stay_out_of_door_sweeps_or_move_elsewhere(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import door_sweep_cells, place_floor_item, reachable
        # a 1 m closet: every free cell is in the door's sweep, so the bucket goes to the garage
        c, reg = self._closet(size=(1.0, 1.0), shelving=((1, 1), 0.5), outlet=None)
        c["rooms"]["garage"] = {"type": "garage", "size_m": [4.0, 4.0]}
        c["connections"]["g"] = {"rooms": ["hall", "garage"], "cells": {"hall": [5, 0], "garage": [0, 0]}}
        p = Placer(c, reg, random.Random(0))
        bucket = place_floor_item(p, ["closet", "garage"], "utility_bucket")
        self.assertIsNotNone(bucket)
        rec = c["entities"][bucket]
        self.assertEqual(rec["location"], "garage")
        self.assertNotIn(tuple(rec["pos"]), door_sweep_cells(c, "garage"))
        self.assertTrue(reachable(p, bucket))
        self.assertIsNone(place_floor_item(p, ["closet"], "watering_can"))     # nowhere clear: not placed at all

    def test_generated_fixtures_drains_and_floor_items_are_usable(self):
        import random
        from house_sim.generator import generate_house
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.plumbing_liquids.furnish import FLOOR_TYPES, door_sweep_cells, reachable
        reg = default_registry()
        for seed, opts in ((7, {"dwelling": "townhouse"}), (9, {}), (10, {}), (24, {"dwelling": "townhouse"}), (30, {}),
                           (35, {"dwelling": "townhouse"}), (8, {"dwelling": "apartment"}), (14, {"dwelling": "townhouse"})):
            c = generate_house(seed, **opts)
            p = Placer(c, reg, random.Random(0))
            for eid, rec in sorted(c["entities"].items()):
                rid = rec.get("location")
                if rid not in c["rooms"] or not rec.get("pos"):
                    continue
                kind = rec.get("type")
                if kind in ("toilet", "bathtub", "shower_stall", "utility_sink", "water_heater", "outdoor_spigot", "kitchen_sink",
                            "bathroom_sink") or kind in FLOOR_TYPES:
                    self.assertTrue(reachable(p, eid), (seed, eid))
                if kind in FLOOR_TYPES:
                    self.assertFalse(set(geometry.footprint(rec)) & door_sweep_cells(c, rid), (seed, eid))
                if kind == "floor_drain":
                    blocked = geometry.blocked_cells(p.state(), rid, include_agents=False)
                    self.assertFalse([cell for cell in geometry.footprint(rec) if cell in blocked], (seed, eid))
                    self.assertTrue(reachable(p, eid), (seed, eid))
                if kind == "utility_sink":
                    self.assertTrue(rec.get("blocks_path"), (seed, eid))     # a laundry tub on legs: later furniture keeps it reachable


    def test_appliance_links_feed_only_real_appliances_once(self):
        from house_sim.generator import generate_house
        for seed, opts in ((2, {"residents": "minimal", "pets": "none"}), (5, {"residents": "minimal", "pets": "none"}),
                           (22, {"residents": "minimal", "pets": "none", "dwelling": "townhouse"}), (41, {})):
            c = generate_house(seed, **opts)
            ents = c["entities"]
            fed = {}
            for eid, rec in sorted(ents.items()):
                if rec.get("valve_kind"):
                    for target in rec.get("feeds") or []:
                        fed.setdefault((target, rec.get("line")), []).append(eid)
            for (target, line), valves in sorted(fed.items()):
                trec = ents[target]
                self.assertNotIn(trec.get("category"), ("appliance_part", "detergent"), (seed, target))
                if trec.get("type") in ("dishwasher", "dishwasher_drawer", "refrigerator", "top_freezer_refrigerator"):
                    self.assertEqual(len(valves), 1, (seed, target, line, valves))
            for hose, rec in sorted(ents.items()):
                if rec.get("type") == "appliance_supply_hose":
                    parent = ents[rec["location"]]
                    self.assertNotIn(parent.get("category"), ("appliance_part", "detergent"), (seed, hose))
            # the sink-base shelf holds only the stops, the trap and small things: the disposal hangs from the sink
            for disp, rec in sorted(ents.items()):
                if rec.get("type") == "garbage_disposal":
                    self.assertEqual(rec["relation"], "part", seed)
                    self.assertEqual(ents[rec["location"]]["slots"]["disposal"]["item"], disp)
                    self.assertNotIn("shelf", rec)

    def test_disposal_is_hidden_behind_the_closed_sink_base_and_serviced_with_it_open(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import add_disposal
        m = Mini()
        m.kitchen_sink()
        p = Placer(m.c, m.reg, random.Random(0))
        disp = add_disposal(p, "sink_cab", "ksink")
        e = m.env(room="kitchen", pos=[3, 2])
        self.assertFalse(e.perception.visible(e.world.s, disp))
        ok(self, e.step("open", "sink_cab"))
        self.assertTrue(e.perception.visible(e.world.s, disp))
        refused(self, e.step("pick_up", disp))          # installed: it is not a loose item on the shelf
        self.assertEqual(ent(e, "ksink")["disposal"], disp)

    def test_watering_can_never_chokes_a_crowded_shed(self):
        from house_sim.generator import _generate
        from house_sim.kernel import geometry
        for seed, opts in ((7, {}), (7, {"residents": "minimal", "pets": "none"}), (22, {"residents": "minimal", "pets": "none"})):
            report, trace = [], {}
            c = _generate(seed, report=report, trace=trace, **opts)
            mine = [line for line in report if any(trace.get(i, "").startswith("plumbing_liquids") for i in
                                                   __import__("re").findall(r"([a-z][a-z0-9_]*_\d+)", line))]
            self.assertEqual(mine, [], (seed, opts))
            for eid, rec in sorted(c["entities"].items()):
                if rec.get("type") in ("watering_can", "utility_bucket", "flange_plunger") and rec.get("location") in c["rooms"]:
                    self.assertFalse(set(geometry.footprint(rec)) & geometry.door_clearance(
                        {"rooms": c["rooms"], "entities": c["entities"], "connections": c["connections"]}, rec["location"]), (seed, eid))

    def test_laundry_valves_share_a_box_and_utility_sink_stops_sit_under_the_basin(self):
        # round-3 report: small laundry rooms filled with utility equipment before the washer. Both washer valves share
        # one wall cell (kernel v2.5 keeps a valve's front cell clear), a laundry tub is kept only while a washer spot
        # remains, and its stops sit under its basin rather than on wall space of their own
        from house_sim.generator import generate_house
        reg = default_registry()
        for name in ("irrigation_controller", "outdoor_spigot"):
            self.assertTrue(reg.make(name, "yard").get("weatherproof"), name)
        tubs = 0
        for seed in range(12):
            c = generate_house(seed, dwelling="house", require=["laundry_room"], residents="minimal", pets="none")
            ents = c["entities"]
            for rid in [r for r, room in c["rooms"].items() if room["type"] == "laundry_room"]:
                washer_valves = [e for e, r in ents.items() if r.get("location") == rid and r.get("serves_label") == "washing machine"]
                self.assertLessEqual(len({tuple(ents[v]["pos"]) for v in washer_valves}), 1, seed)
                for sink in [e for e, r in ents.items() if r.get("location") == rid and r.get("type") == "utility_sink"]:
                    tubs += 1
                    stops = [e for e, r in ents.items() if sink in (r.get("feeds") or [])]
                    self.assertEqual(len(stops), 2, seed)
                    for stop in stops:
                        self.assertEqual(ents[stop]["pos"], ents[sink]["pos"], seed)
                        self.assertLess(ents[stop]["mount_height_m"], 0.5, seed)
        self.assertGreater(tubs, 0)

    def test_spigot_moves_with_its_hanger_when_something_stands_in_front_of_the_hanger(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import hidden_behind, keep_controls_reachable
        reg = default_registry()
        c = {"rooms": {"yard": {"type": "backyard", "size_m": [4.0, 3.0], "outdoor": True}, "hall": {"type": "hallway", "size_m": [3.0, 1.5]}},
             "connections": {"c": {"rooms": ["hall", "yard"], "cells": {"hall": [0, 2], "yard": [0, 0]}}},
             "entities": {}, "people": {}, "pets": {}}
        spigot = reg.make("outdoor_spigot", "yard")
        spigot["pos"] = [5, 0]
        hanger = reg.make("hose_hanger", "yard", mounted=True, mount_height_m=1.0)
        hanger["pos"] = [5, 0]
        barrow = reg.make("generic_item", "yard", label="wheelbarrow", size_m=[0.6, 0.6, 1.2], portable=True, mass_kg=15.0)
        barrow["pos"] = [5, 0]
        c["entities"].update(outdoor_spigot_1=spigot, hose_hanger_1=hanger, wheelbarrow_1=barrow)
        p = Placer(c, reg, random.Random(0))
        self.assertEqual(hidden_behind(p, "hose_hanger_1", loose=True), ["wheelbarrow_1"])
        self.assertEqual(hidden_behind(p, "hose_hanger_1"), [])              # a loose item alone does not move a valve
        keep_controls_reachable(c, random.Random(0), {}, reg)
        self.assertNotEqual(c["entities"]["outdoor_spigot_1"]["pos"], [5, 0])
        self.assertEqual(c["entities"]["hose_hanger_1"]["pos"], c["entities"]["outdoor_spigot_1"]["pos"])
        self.assertEqual(hidden_behind(Placer(c, reg, random.Random(0)), "hose_hanger_1", exempt=("outdoor_spigot_1",), loose=True), [])


# ======================================================================================
# refills, drinks service, pressure, electrics, appliance hoses, slopes

class Service(unittest.TestCase):
    def counter_house(self):
        m = Mini()
        m.kitchen_sink()
        m.add("counter", "counter", "kitchen", pos=[5, 0], size_m=[2.0, 0.6, 0.9])
        return m

    def test_wrong_product_and_undiluted_soap_in_a_foaming_pump(self):
        m = self.counter_house()
        jug(m, "softener", "counter", liquid="fabric_softener", liters=1.0, spos=[0.0, 0.0], open=True, neck_mm=40)
        m.add("tank", "generic_item", "counter", spos=[0.5, 0.0], liquid_container=True, liquid="laundry_detergent", liquid_l=0.1,
              _mix={"laundry_detergent": 0.1}, capacity_l=0.6, fill_line_l=0.55, product_tag="laundry_detergent", neck_mm=50,
              size_m=[0.15, 0.1, 0.15], label="auto-dose detergent tank")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "softener"))
        ok(self, e.step("pour", "softener", "tank", amount_l=0.3, rate="slow"))
        self.assertTrue(ent(e, "tank")["product_mismatch"])
        m2 = self.counter_house()
        jug(m2, "refill", "counter", liquid="hand_soap", liters=1.0, spos=[0.0, 0.0], open=True, neck_mm=40)
        m2.add("pump", "generic_item", "counter", spos=[0.5, 0.0], liquid_container=True, liquid=None, liquid_l=0.0, capacity_l=0.25,
               fill_line_l=0.24, product_tag="foaming_hand_soap", foaming=True, neck_mm=35, size_m=[0.08, 0.08, 0.19], label="foaming soap pump")
        e2 = m2.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "refill"))
        ok(self, e2.step("pour", "refill", "pump", amount_l=0.2, rate="slow"))
        self.assertTrue(ent(e2, "pump")["clogged"])

    def test_pouring_for_someone_who_covered_their_glass(self):
        m = self.counter_house()
        m.person("guest", "kitchen", [6, 2])
        jug(m, "pitcher", "counter", liquid="water", liters=1.0, spos=[0.0, 0.0], open=True, neck_mm=80)
        glass(m, "g", "counter", spos=[0.5, 0.0], owner="guest", covered_by="guest")
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "pitcher"))
        ok(self, e.step("pour", "pitcher", "g", amount_l=0.2))
        self.assertIn("plumbing_liquids.unwanted_pour", failures(e))

    def test_shaken_soda_sprays_unless_it_settles(self):
        m = self.counter_house()
        jug(m, "soda", "counter", liquid="soda", liters=0.5, spos=[0.0, 0.0], open=False, package="bottle", fizz=1.0)
        e = m.env(room="kitchen", pos=[5, 1])
        ok(self, e.step("pick_up", "soda"))
        r = ok(self, e.step("open", "soda"))
        self.assertTrue(spills(e))
        e2 = m.env(room="kitchen", pos=[5, 1])
        ok(self, e2.step("pick_up", "soda"))
        wait(self, e2, 300)
        ok(self, e2.step("open", "soda"))
        self.assertEqual(spills(e2), [])

    def test_hot_coffee_is_a_hot_hazard_children_are_not_handed(self):
        m = self.counter_house()
        m.person("kid", "kitchen", [6, 2], role="child", age=6)
        m.add("mug", "generic_item", "counter", spos=[0.0, 0.0], category="dish", container=True, accepts_items=True, capacity_l=0.35,
              serving=True, material="ceramic", liquid="coffee", liquid_l=0.25, _mix={"coffee": 0.25}, temperature_c=78.0, safe_grips=["handle"],
              max_touch_temp_c=90.0, size_m=[0.12, 0.09, 0.1], label="mug of coffee")
        e = m.env(room="kitchen", pos=[5, 1])
        wait(self, e, 30)
        self.assertIn("hot", ent(e, "mug")["hazards"])
        ok(self, e.step("pick_up", "mug", grip="handle"))
        ok(self, e.step("approach", "kid"))
        refused(self, e.step("hand_over", "mug", "kid"), "safety")

    def test_reaching_into_water_next_to_a_live_power_strip(self):
        m = Mini()
        m.add("strip", "generic_item", "living", pos=[3, 3], plug_required=True, plugged_in=True, category="power_strip", label="power strip",
              size_m=[0.3, 0.06, 0.04])
        m.add("toy", "generic_item", "living", pos=[3, 4], label="toy", size_m=[0.1, 0.1, 0.1])
        m.c["rooms"]["living"]["wet_cells"] = [[3, 4]]
        e = m.env(room="living", pos=[2, 4])
        grab(self, e, "toy")
        self.assertGreater(stat(e, "electrical_hazard"), 0)
        # a drip or a mop film (0.02 mm) wets the floor but is not standing water
        m.c["rooms"]["living"]["water_film"] = {"3,4": 0.02}
        e = m.env(room="living", pos=[2, 4])
        grab(self, e, "toy")
        self.assertEqual(stat(e, "electrical_hazard"), 0)
        # a puddle (spill entity, 0.6 L over 0.35 m2 = 1.7 mm) is
        del m.c["rooms"]["living"]["water_film"]
        m.c["rooms"]["living"]["wet_cells"] = []
        m.add("puddle", "generic_item", "living", pos=[3, 4], category="spill", liquid="water", liquid_l=0.6, area_m2=0.35, floor_hazard="wet",
              portable=False, size_m=[0.5, 0.5, 0.002], mass_kg=0.0)
        e = m.env(room="living", pos=[2, 4])
        grab(self, e, "toy")
        self.assertGreater(stat(e, "electrical_hazard"), 0)

    def test_appliance_hoses_strain_when_pulled_and_spray_when_unscrewed_live(self):
        m = Mini()
        m.c["rooms"]["laundry"] = {"type": "laundry_room", "size_m": [3.0, 3.0], "floor_material": "tile", "exits": []}
        m.c["connections"]["c_kl"] = {"rooms": ["kitchen", "laundry"], "cells": {"kitchen": [7, 0], "laundry": [0, 2]}, "width_m": 0.9}
        m.add("light_laundry", "ceiling_light", "laundry", pos=[1, 1], mounted=True, on=True)
        m.add("washer", "generic_item", "laundry", pos=[2, 0], portable=False, blocks_path=True, label="washing machine", category="washer",
              size_m=[0.6, 0.6, 0.85], mass_kg=70.0)
        m.add("wv", "shutoff_valve", "laundry", pos=[1, 0], mounted=True, mount_height_m=1.1, line="cold", feeds=["washer"])
        m.add("hose", "appliance_supply_hose", "washer", relation="behind", _defect="bulge", slack_m=0.3)
        e = m.env(room="laundry", pos=[1, 1])
        self.assertNotIn("hose", e.observe()["visible_entities"])
        ok(self, e.step("inspect", "washer", where="behind"))
        details = json.loads(ok(self, e.step("inspect", "hose", where="closely"))["message"])
        self.assertIn("bulging", details["condition"])
        ok(self, e.step("disconnect_hose", "hose"))
        wait(self, e, 120)
        self.assertGreater(stat(e, "flood"), 0)
        from house_sim.systems.plumbing_liquids import leaks as K
        e2 = m.env(room="laundry", pos=[1, 1])
        K.strain_hoses(e2.world, "washer", "test")
        wait(self, e2, 120)
        self.assertTrue(ent(e2, "hose")["strained"])
        self.assertTrue(spills(e2))

    def test_driveway_rinse_top_down_vs_bottom_up(self):
        def house():
            m = Mini()
            cells = {"{},{}".format(c, r): {"dust": 0.6} for c in range(7) for r in range(20)}
            m.c["entities"]["floor_drive"]["_cells"] = cells
            m.c["entities"]["floor_drive"]["soil"] = {"dust": 0.6}
            m.add("spigot", "outdoor_spigot", "drive", pos=[0, 1], mounted=True, mount_height_m=0.45, settings={"valve": 1.0})
            m.add("hose", "garden_hose", "drive", pos=[1, 1], attached_to="spigot")
            return m
        top = [[c, r] for r in range(20) for c in range(7)]
        e = house().env(room="drive", pos=[1, 2])
        grab(self, e, "hose")
        ok(self, e.step("spray", "drive", cells=top, seconds=900, toward="lawn"))
        clean_top = max(max(p.values()) for p in ent(e, "floor_drive")["_cells"].values() if p)
        e2 = house().env(room="drive", pos=[1, 2])
        grab(self, e2, "hose")
        ok(self, e2.step("spray", "drive", cells=list(reversed(top)), seconds=900, toward="lawn"))
        clean_bottom = max(max(p.values()) for p in ent(e2, "floor_drive")["_cells"].values() if p)
        self.assertLess(clean_top, clean_bottom)

    def test_dishwater_leaves_a_greasy_ring(self):
        m = Mini()
        m.kitchen_sink(plug="closed", liquid="dirty_water", liquid_l=12.0, _mix={"dirty_water": 12.0}, cl_soil_g=30.0)
        e = m.env(room="kitchen", pos=[3, 1])
        ok(self, e.step("drain", "ksink"))
        wait(self, e, 180)
        self.assertGreater(ent(e, "ksink")["soil"].get("grease", 0), 0.1)

    def test_filling_at_the_tap_wets_the_outside(self):
        m = self.counter_house()
        m.add("bottle", "water_bottle", "counter", spos=[0.0, 0.0], open=True)
        e = m.env(room="kitchen", pos=[4, 1])
        ok(self, e.step("pick_up", "bottle"))
        ok(self, e.step("fill", "bottle", source="ksink", to_frac=0.9))
        # the stream wets the outside (0.3); with every domain loaded the cleaning domain's drying runs during the
        # fill itself, so a little has evaporated by the time the action ends
        self.assertGreaterEqual(ent(e, "bottle")["wetness"], 0.25)

    def test_rain_comes_in_through_an_open_window(self):
        m = Mini()
        m.add("win", "window", "living", pos=[0, 2], mounted=True, open=True, locked=False)
        m.add("sill", "generic_item", "living", pos=[1, 2], label="window sill", portable=False, size_m=[0.3, 0.3, 0.02],
              _leak={"component": "open_window", "rate_lpm": 0.3, "when": "rain", "window": "win", "to": {"room": "living", "cell": [1, 2]}})
        m.c["weather"]["condition"] = "rain"
        e = m.env(room="living", pos=[2, 2])
        wait(self, e, 600)
        self.assertTrue(spills(e))
        m.c["entities"]["win"]["open"] = False
        e2 = m.env(room="living", pos=[2, 2])
        wait(self, e2, 600)
        self.assertEqual(spills(e2), [])


# ======================================================================================
# scenario helpers and reference skills on generated houses

class HelpersAndSkills(unittest.TestCase):
    def build(self, setup, seed=0, house=None):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "TEST-PLUMB", "house": house or {"residents": "minimal", "pets": "none"}, "setup": setup,
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
        config, task, oracle = build(spec, seed)
        return HouseEnv(config, task, seed=seed), oracle["bindings"]

    def run_skill(self, env, bindings, steps):
        from house_sim.tasks.runner import run_plan
        return run_plan(env, steps, bindings, strict=True)

    def test_fill_sink_skill_reaches_the_band(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.kitchen_sink"},
                             {"op": "spawn", "var": "stopper", "type": "sink_stopper", "on": "{counter}"},
                             {"op": "robot", "room": "{kitchen}"}], seed=2)
        self.run_skill(env, b, [{"skill": "plumbing_liquids.fill_sink", "sink": "{sink}", "stopper": "{stopper}", "target_c": 44.0, "level": 0.5}])
        s = ent(env, b["sink"])
        self.assertTrue(14.0 <= s["liquid_l"] <= 18.0)
        self.assertTrue(40.0 <= s["temperature_c"] <= 48.0)
        self.assertFalse(s["water_running"])

    def test_toilet_trouble_helper_and_unclog_skill(self):
        for seed in (0, 1):
            env, b = self.build([{"op": "helper", "name": "plumbing_liquids.toilet_trouble", "args": {"kind": "paper"}},
                                 {"op": "robot", "room": "{bathroom}"}], seed=seed)
            self.run_skill(env, b, [{"skill": "plumbing_liquids.unclog_toilet", "toilet": "{toilet}", "plunger": "{plunger}"}])
            self.assertEqual(ent(env, b["toilet"])["_clog"]["kind"], "none")
            self.assertEqual(stat(env, "flood"), 0)

    def test_hidden_leak_helper_hides_the_source(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.hidden_leak", "args": {"source": "kitchen_supply"}}], seed=1)
        self.assertNotIn(b["leak_source"], env.observe()["visible_entities"])
        self.assertTrue(ent(env, b["leak_source"])["_leak"])
        self.run_skill(env, b, [{"skill": "plumbing_liquids.isolate", "fixture": "{leak_fixture}"}])
        wait(self, env, 60)
        self.assertFalse(ent(env, b["leak_source"])["_leak_active"])

    def test_yard_hose_store_skill(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.yard_hose",
                              "args": {"spigot_on": True, "pressurized": True, "water_l": 3.0, "kinks": 2}},
                             {"op": "robot", "room": "{yard}"}], seed=1, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        self.run_skill(env, b, [{"skill": "plumbing_liquids.store_hose", "hose": "{hose}", "spigot": "{spigot}", "hanger": "{hanger}"}])
        h = ent(env, b["hose"])
        self.assertTrue(h["coiled"])
        self.assertEqual(h["kinks"], 0)
        self.assertFalse(h["pressurized"])
        self.assertEqual(stat(env, "damage"), 0)

    def test_serve_water_and_dump_skills(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.drinkware"},
                             {"op": "helper", "name": "plumbing_liquids.bathroom"},
                             {"op": "helper", "name": "plumbing_liquids.dirty_bucket", "args": {"liters": 6.0}},
                             {"op": "room", "var": "living", "type": "living_room"},
                             {"op": "person", "var": "req", "id": "resident_1", "role": "adult", "room": "{living}"},
                             # generated houses can stand a wastebasket / toilet brush in the bathroom door's sweep
                             # (mechanisms knocks them over when the door opens): this household keeps them clear
                             {"op": "helper", "name": "plumbing_liquids.clear_door_sweeps"}], seed=2)
        self.run_skill(env, b, [{"skill": "plumbing_liquids.serve_water", "person": "{req}", "vessel": "{glass}", "source": "{sink}", "amount_l": 0.2},
                                {"skill": "plumbing_liquids.dump", "container": "{bucket}", "dest": "{toilet}"}])
        self.assertEqual(ent(env, b["glass"])["location"], b["req"])
        self.assertEqual(L.liters(ent(env, b["bucket"])), 0.0)
        self.assertEqual(failures(env), [])

    def test_irrigation_helper_schedules_a_run(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.irrigation", "args": {"start_in_h": 0.5, "minutes": 20, "rain": False}}],
                            seed=1, house={"dwelling": "house", "residents": "minimal", "pets": "none"})
        wait(self, env, 3600)
        self.assertGreaterEqual(psec(env)["irrigation"]["zone_1"]["minutes"], 19.0)

    def test_water_service_and_close_valve_skill(self):
        env, b = self.build([{"op": "helper", "name": "plumbing_liquids.water_service", "args": {"valve_kind": "ball", "seized": False}}], seed=0)
        self.run_skill(env, b, [{"skill": "plumbing_liquids.close_valve", "valve": "{main}"}])
        self.assertFalse(env.world.s["utilities"]["main_water_open"])


# ======================================================================================
# kernel v2.1 integration: harms, conventions, cached indexes, periodic systems, reachable generated controls

class IntegrationV21(unittest.TestCase):
    def test_preexisting_leak_floods_but_is_not_a_robot_spill(self):
        m = Mini()
        m.add("pipe", "water_pipe", "basement", pos=[3, 0], mounted=True, mount_height_m=2.1,
              _leak={"component": "split_pipe", "rate_lpm": 2.0, "when": "always", "to": {"room": "basement", "cell": [3, 1]}})
        e = m.env(room="living", pos=[1, 3])
        wait(self, e, 600)
        self.assertTrue(spills(e))                         # the floor gets water
        self.assertGreater(stat(e, "flood"), 0)            # and it is a flood (>= 4 L from one source)
        self.assertEqual(stat(e, "spills"), 0)             # but the robot spilled nothing
        jug(m, "jug", "kitchen", liquid="milk", liters=0.8, pos=[2, 2], open=True)
        e2 = m.env(room="kitchen", pos=[2, 3])
        ok(self, e2.step("crouch"))
        ok(self, e2.step("pick_up", "jug"))
        ok(self, e2.step("pour", "jug", "kitchen", amount_l=0.3))
        self.assertGreater(stat(e2, "spills"), 0)
        self.assertIn("spills", failures(e2))

    def test_damage_is_a_dict_and_merges_legacy_lists(self):
        m = Mini()
        m.bath_sink(_worn={"drip_lpm": 0.01})
        m.c["entities"]["bsink"]["damage"] = ["old scratch"]
        e = m.env(room="bath", pos=[1, 1])
        ok(self, e.step("turn_on_water", "bsink", temp="cold"))
        ok(self, e.step("turn_off_water", "bsink", force="firm"))
        dmg = ent(e, "bsink")["damage"]
        self.assertIsInstance(dmg, dict)
        self.assertIn("washer_crushed", dmg)
        self.assertEqual(dmg.get("old scratch"), 1.0)
        self.assertTrue(0.0 < dmg["washer_crushed"] <= 1.0)

    def test_systems_use_cached_indexes_and_periods(self):
        from house_sim.kernel.plugins import SYSTEM_PERIODS, SYSTEMS
        names = [s[1] for s in SYSTEMS if s[1].startswith("plumbing_liquids.")]
        self.assertEqual(sorted(names), ["plumbing_liquids.census", "plumbing_liquids.slow", "plumbing_liquids.water"])
        self.assertEqual(SYSTEM_PERIODS["plumbing_liquids.slow"], 120.0)
        self.assertEqual(SYSTEM_PERIODS["plumbing_liquids.census"], 600.0)
        m = Mini()
        m.kitchen_sink()
        e = m.env(room="kitchen", pos=[3, 1])
        from house_sim.systems.plumbing_liquids import common as C
        from house_sim.systems.plumbing_liquids import supply as S
        self.assertEqual(C.kind_ids(e.world, "sink"), ["ksink"])
        self.assertEqual(S.valves(e.world), ["k_cold", "k_hot"])

    def test_liquid_other_domains_put_in_unmarked_things_is_tracked(self):
        m = Mini()
        m.add("vase", "generic_item", "living", pos=[2, 2], label="ceramic vase", material="ceramic", category="decor", portable=True)
        e = m.env(room="living", pos=[2, 3])
        self.assertNotIn("vase", L.holder_ids(e.world))
        # another domain fills it directly (no plumbing code involved): the census finds it within 10 minutes
        w = e.world
        w.begin()
        mv = w.mut("vase")
        mv.update(liquid="water", liquid_l=0.5, _mix={"water": 0.5}, filled_s=w.s["time_s"] - 3 * 86400.0, temperature_c=21.0)
        w.commit()
        wait(self, e, 900)
        self.assertIn("vase", L.holder_ids(e.world))
        wait(self, e, 1800)
        self.assertTrue(ent(e, "vase").get("stale"))       # the freshness process now covers it

    def test_fill_into_an_unmarked_vessel_registers_it_immediately(self):
        m = Mini()
        m.kitchen_sink()
        m.add("bowl", "generic_item", "kitchen", pos=[4, 1], label="decorative metal bowl", material="stainless_steel", category="decor",
              container=True, capacity_l=0.0, portable=True, size_m=[0.25, 0.25, 0.12], mass_kg=0.4, liquid_container=False)
        e = m.env(room="kitchen", pos=[3, 1])
        self.assertNotIn("bowl", L.holder_ids(e.world))
        w = e.world
        w.begin()
        L.add(w, "bowl", L.make_parcel("water", 1.0, temp_c=60.0))
        w.commit()
        self.assertIn("bowl", L.holder_ids(e.world))
        wait(self, e, 30)
        self.assertIn("hot", ent(e, "bowl").get("hazards", []))

    def test_episode_start_derives_tap_state_from_setup_settings(self):
        m = Mini()
        m.kitchen_sink(settings={"flow": 0.7, "mix": 0.0})
        e = m.env(room="kitchen", pos=[3, 1])
        self.assertTrue(ent(e, "ksink")["water_running"])
        self.assertGreater(ent(e, "ksink")["flow_lpm"], 4.0)

    def test_ice_maker_status_is_live_and_namespaced(self):
        m = freezer_kitchen()
        m.add("maker", "freezer_ice_maker", "freezer", relation="in", shelf=2, ice_g=0.0, ice_cubes=0)
        m.add("valve", "shutoff_valve", "sink_cab", line="cold", feeds=["maker"], shelf=0, settings={"position": 0.0})
        e = m.env(room="kitchen", pos=[6, 3])
        ok(self, e.step("open", "freezer"))
        details = json.loads(ok(self, e.step("inspect", "maker"))["message"])
        self.assertIn("no water", details["ice_maker"]["status"])
        wait(self, e, 300)
        self.assertIn("no water", ent(e, "maker")["_plumbing_liquids_status"])

    def test_generated_controls_stay_reachable(self):
        from house_sim.generator import generate_house
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import FLOOR_TYPES, REACH_TYPES, door_sweep_cells, reachable
        import random
        reg = default_registry()
        for seed in (1, 4, 11, 19):
            c = generate_house(seed)
            p = Placer(c, reg, random.Random(0))
            for eid, rec in sorted(c["entities"].items()):
                if rec.get("type") in REACH_TYPES and rec.get("mounted") and rec.get("location") in c["rooms"]:
                    self.assertTrue(reachable(p, eid), (seed, eid))
                if rec.get("type") in FLOOR_TYPES and rec.get("location") in c["rooms"]:
                    self.assertNotIn(tuple(rec["pos"]), door_sweep_cells(c, rec["location"]), (seed, eid))

    def test_helper_floor_items_are_reachable_and_out_of_door_sweeps(self):
        from house_sim.tasks.runner import build
        from house_sim.generator.place import Placer
        from house_sim.systems.plumbing_liquids.furnish import door_sweep_cells, reachable
        import random
        for seed in (0, 1, 2, 41, 97):
            spec = {"catalog_id": "TEST-PLUMB", "house": {"residents": "minimal", "pets": "none"},
                    "setup": [{"op": "helper", "name": "plumbing_liquids.dirty_bucket", "args": {"liters": 6.0}}],
                    "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
            config, task, oracle = build(spec, seed)
            bucket = oracle["bindings"]["bucket"]
            rec = config["entities"][bucket]
            self.assertTrue(reachable(Placer(config, default_registry(), random.Random(0)), bucket), seed)
            self.assertNotIn(tuple(rec["pos"]), door_sweep_cells(config, rec["location"]), seed)

    def test_burst_pipe_sprays_onto_open_floor(self):
        from house_sim.tasks.runner import build
        from house_sim.kernel import geometry
        for seed in (0, 10):      # seed 10: a mop bucket stands right under the generated pipe
            spec = {"catalog_id": "TEST-PLUMB", "house": {"residents": "minimal", "pets": "none"},
                    "setup": [{"op": "helper", "name": "plumbing_liquids.burst_pipe"}],
                    "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
            config, task, oracle = build(spec, seed)
            pipe = oracle["bindings"]["pipe"]
            to = config["entities"][pipe]["_leak"]["to"]
            under = [e for e, r in config["entities"].items() if e != pipe and r.get("location") == to["room"] and not r.get("ground_layer")
                     and not r.get("mounted") and tuple(to["cell"]) in geometry.footprint(r)]
            self.assertEqual(under, [], seed)
            env = HouseEnv(config, task, seed=seed)
            wait(self, env, 120)
            self.assertGreater(psec(env).get("floor_water", {}).get(pipe, 0.0), 10.0, seed)

    def test_sweating_pipe_owns_its_film_against_other_drying(self):
        m = Mini()
        m.c["rooms"]["basement"]["humidity"] = 0.7
        m.add("pipe", "water_pipe", "basement", pos=[2, 0], mounted=True, mount_height_m=1.6, condensation_prone=True)
        e = m.env(room="basement", pos=[2, 1])
        wait(self, e, 2 * 3600)
        pipe = ent(e, "pipe")
        self.assertTrue(pipe["sweating"])
        self.assertEqual(pipe["drying_owner"], "plumbing_liquids")
        self.assertGreaterEqual(pipe["wetness"], 0.4)             # cleaning's drying left the film alone
        w = e.world
        w.begin()
        w.room_m("basement")["humidity"] = 0.4
        w.commit()
        wait(self, e, 300)
        self.assertFalse(ent(e, "pipe")["sweating"])
        self.assertNotIn("drying_owner", ent(e, "pipe"))

    def test_clear_door_sweeps_helper_moves_loose_items_from_door_arcs(self):
        import random
        from house_sim.tasks.runner import build
        from house_sim.tasks.setup import SetupCtx
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.plumbing_liquids.furnish import door_sweep_cells, reachable
        spec = {"catalog_id": "TEST-PLUMB", "house": {"residents": "minimal", "pets": "none"},
                "setup": [{"op": "helper", "name": "plumbing_liquids.bathroom"}],
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
        for seed in (0, 1, 2, 41, 97):
            config, task, oracle = build(spec, seed)
            rid = oracle["bindings"]["bathroom"]
            # plant a wastebasket on a free floor cell of the bathroom's door sweep (layout-independent)
            state = Placer(config, default_registry(), random.Random(0)).state()
            busy = set(geometry.blocked_cells(state, rid)) | {c for e in geometry.room_entities(state, rid)
                                                              for c in geometry.footprint(state["entities"][e])
                                                              if not state["entities"][e].get("ground_layer")}
            doors = {tuple(c["cells"][rid]) for c in config["connections"].values() if rid in c.get("cells", {})}
            spots = sorted(c for c in door_sweep_cells(config, rid) - doors - busy if geometry.in_grid(config["rooms"][rid], c))
            if not spots:
                continue
            bin_rec = default_registry().make("generic_item", rid, label="planted wastebasket", size_m=[0.28, 0.22, 0.38], mass_kg=0.6,
                                              portable=True)
            bin_rec["pos"] = list(spots[0])
            config["entities"]["planted_bin"] = bin_rec
            ctx = SetupCtx(config, default_registry(), random.Random(seed), seed, spec)
            cleared = SCENARIO_HELPERS["plumbing_liquids.clear_door_sweeps"](ctx)["cleared_items"]
            self.assertIn("planted_bin", cleared, seed)
            for eid in cleared:
                self.assertTrue(reachable(ctx.placer, eid), (seed, eid))
            # no loose item 0.15 m or taller is left in any room's door clearance (leaves and thresholds included)
            for eid, rec in sorted(config["entities"].items()):
                if rec.get("location") not in config["rooms"] or not rec.get("pos") or rec.get("mounted") or rec.get("ground_layer"):
                    continue
                if geometry.blocks(rec) or "connects" in rec or rec.get("docked") or not rec.get("portable"):
                    continue
                if float((rec.get("size_m") or [0, 0, 0])[2]) >= 0.15:
                    self.assertFalse(set(geometry.footprint(rec)) & door_sweep_cells(config, rec["location"]), (seed, eid))
        # a sink base the plan opens: its doors sweep the floor around it (seed 97: trash can, stool, mop beside it)
        spec["setup"] = [{"op": "helper", "name": "plumbing_liquids.kitchen_sink"},
                         {"op": "helper", "name": "plumbing_liquids.clear_door_sweeps", "args": {"fronts": ["{sink_cabinet}"]}},
                         {"op": "robot", "near": "{sink_cabinet}"}]
        config, task, oracle = build(spec, 97)
        cab = oracle["bindings"]["sink_cabinet"]
        rid = config["entities"][cab]["location"]
        foot = set(geometry.footprint(config["entities"][cab]))
        around = {(x + dx, y + dy) for x, y in foot for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} - foot
        for eid, rec in config["entities"].items():
            if rec.get("location") == rid and rec.get("pos") and not rec.get("mounted") and not rec.get("blocks_path")                     and float((rec.get("size_m") or [0, 0, 0])[2]) >= 0.15 and rec.get("category") not in ("floor", "door"):
                self.assertFalse(set(geometry.footprint(rec)) & around, eid)
        env = HouseEnv(config, task, seed=97)
        ok(self, env.step("open", cab))
        self.assertEqual(stat(env, "collisions"), 0)


# ======================================================================================
# realistic perception (kernel v2.4): tiers, instruments, look-alikes, honest messages

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _true_ids(env):
    """Entity ids with an underscore (generated ids always have one; the kernel translates only those inside messages,
    and short fixture ids like "ksink" or "vanity" may coincide with words of labels)."""
    return {e for e in env.world.s["entities"] if "_" in e}


def _leaked_ids(env, value):
    ids = _true_ids(env)
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in TOKEN_RE.findall(v) if t in ids)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return found


class RealisticPerception(unittest.TestCase):
    def kitchen(self):
        m = Mini()
        m.kitchen_sink()
        m.c["entities"]["k_hot"]["label"] = "hot water stop"
        m.c["entities"]["k_cold"]["label"] = "cold water stop"
        return m

    def probe(self, m):
        m.add("probe_1", "generic_item", "kitchen", label="instant-read thermometer", tool_kind="thermometer", accuracy_c=0.5,
              size_m=[0.15, 0.03, 0.02], portable=True)

    def test_glance_shows_running_water_but_not_flow_temperature_or_hidden_truth(self):
        e = self.kitchen().env(room="kitchen", pos=[3, 1], perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("turn_on_water", h("ksink"), temp="hot", flow=1.0))
        ok(self, e.step("wait", seconds=40))
        rec = e.observe()["visible_entities"][h("ksink")]
        self.assertTrue(rec["water_running"])                     # a running tap is seen from the room
        for field in ("flow_lpm", "outlet_temp_c", "drip_lpm", "potable", "faucet", "plumb", "drain_capacity_lpm", "drain_speed",
                      "thermal_tau_s", "water_source", "_hot_run_s"):
            self.assertNotIn(field, rec)
        details = json.loads(ok(self, e.step("inspect", h("ksink")))["message"])
        self.assertEqual(details["stream"], "a steady stream")
        self.assertIn(details["stream_temp"], ("warm", "hot", "very hot"))     # the thermal camera gives a word
        for field in ("flow_lpm", "outlet_temp_c", "measured", "water_age_h"):
            self.assertNotIn(field, details)
        rec = e.observe()["visible_entities"][h("ksink")]
        self.assertNotIn("flow_lpm", rec)                         # still a measure-tier quantity after a look
        self.assertNotIn("outlet_temp_c", rec)
        # oracle mode keeps the exact numbers
        o = self.kitchen().env(room="kitchen", pos=[3, 1])
        self.assertIn("L/min", ok(self, o.step("turn_on_water", "ksink", temp="hot", flow=1.0))["message"])
        self.assertIn("flow_lpm", json.loads(ok(self, o.step("inspect", "ksink"))["message"]))

    def test_a_held_thermometer_measures_the_stream_a_hand_only_feels_it(self):
        m = self.kitchen()
        self.probe(m)
        e = m.env(room="kitchen", pos=[3, 1], hold={"left": "probe_1"}, perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("turn_on_water", h("ksink"), temp="hot", flow=1.0))
        ok(self, e.step("wait", seconds=40))
        truth = ent(e, "ksink")["outlet_temp_c"]
        d = json.loads(ok(self, e.step("inspect", h("ksink"), where="closely"))["message"])
        self.assertAlmostEqual(d["measured"]["outlet_temp_c"], truth, delta=1.0)
        self.assertNotIn("stream_feel", d)
        bare = self.kitchen().env(room="kitchen", pos=[3, 1], perception="realistic")
        hb = bare.realism.handles.handle
        ok(self, bare.step("turn_on_water", hb("ksink"), temp="hot", flow=1.0))
        ok(self, bare.step("wait", seconds=40))
        d = json.loads(ok(self, bare.step("inspect", hb("ksink"), where="closely"))["message"])
        self.assertNotIn("measured", d)
        self.assertIn(d["stream_feel"], ("warm", "hot", "very hot"))
        # a glass filled at the tap: the probe reads the water; its level is shown to the nearest 5 % after a look
        m = self.kitchen()
        self.probe(m)
        m.add("tumbler_1", "generic_item", "kitchen", label="drinking glass", category="dish", liquid_container=True, liquid=None,
              liquid_l=0.0, capacity_l=0.35, material="glass", portable=True, size_m=[0.08, 0.08, 0.12])
        e = m.env(room="kitchen", pos=[3, 1], hold={"left": "probe_1", "right": "tumbler_1"}, perception="realistic")
        h = e.realism.handles.handle
        msg = ok(self, e.step("fill", h("tumbler_1"), source=h("ksink"), to_frac=0.7, temp="cold"))["message"]
        self.assertIn("about", msg)
        self.assertIsNone(re.search(r"\d\.\d{3}", msg), msg)      # no exact liters
        d = json.loads(ok(self, e.step("inspect", h("tumbler_1"), where="closely"))["message"])
        self.assertAlmostEqual(d["measured"]["temperature_c"], ent(e, "tumbler_1")["temperature_c"], delta=1.0)
        self.assertAlmostEqual(d["fill_level"] * 20.0, round(d["fill_level"] * 20.0), places=6)

    def test_hot_and_cold_stops_look_alike_until_inspected(self):
        e = self.kitchen().env(room="kitchen", pos=[3, 1], perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("open", h("sink_cab")))
        vis = e.observe()["visible_entities"]
        hot, cold = vis[h("k_hot")], vis[h("k_cold")]
        self.assertEqual(hot["label"], cold["label"])
        self.assertEqual(hot["label"], "water shutoff valve")
        self.assertIn("candidates", hot)
        for field in ("line", "feeds", "settings"):
            self.assertNotIn(field, hot)
        ok(self, e.step("inspect", h("k_hot")))
        vis = e.observe()["visible_entities"]
        self.assertEqual(vis[h("k_hot")]["label"], "hot water stop")
        self.assertEqual(vis[h("k_hot")]["line"], "hot")
        self.assertEqual(vis[h("k_cold")]["label"], "water shutoff valve")    # the other stays unresolved
        # closing a fixture's stops does not say which one was hot, nor how far open a valve is
        msg = ok(self, e.step("shut_off", h("ksink")))["message"]
        for word in ("hot", "cold", "% open"):
            self.assertNotIn(word, msg)
        self.assertIn("reached its stop", msg)

    def test_flange_and_cup_plungers_are_confused_at_a_glance(self):
        m = Mini()
        m.add("cupp_1", "cup_plunger", "bath", pos=[1, 1])
        m.add("flange_1", "flange_plunger", "bath", pos=[2, 1])
        e = m.env(room="bath", pos=[1, 2], perception="realistic")
        h = e.realism.handles.handle
        vis = e.observe()["visible_entities"]
        cup, flange = vis[h("cupp_1")], vis[h("flange_1")]
        self.assertEqual(cup["label"], flange["label"])
        self.assertEqual(cup["candidates"], flange["candidates"])
        self.assertNotIn("plunger_kind", flange)
        ok(self, e.step("approach", h("flange_1")))
        ok(self, e.step("inspect", h("flange_1")))
        vis = e.observe()["visible_entities"]
        self.assertEqual(vis[h("flange_1")]["label"], "flange plunger")
        self.assertEqual(vis[h("flange_1")]["plunger_kind"], "flange")
        self.assertEqual(vis[h("cupp_1")]["label"], "rubber plunger")

    def test_wet_and_dry_ceiling_stains_need_a_close_look_or_a_meter(self):
        m = Mini()
        m.add("stain_new", "water_stain", "living", pos=[1, 1], wet=True, growing=True, ceiling_water_l=1.2, stain_m2=0.2)
        m.add("stain_old", "water_stain", "living", pos=[4, 4], wet=False, stain_m2=0.2)
        m.add("meter_1", "generic_item", "living", label="moisture meter", tool_kind="moisture_meter", size_m=[0.05, 0.02, 0.2], portable=True)
        e = m.env(room="living", pos=[2, 2], perception="realistic")
        h = e.realism.handles.handle
        vis = e.observe()["visible_entities"]
        new, old = vis[h("stain_new")], vis[h("stain_old")]
        self.assertEqual(new["label"], old["label"])
        self.assertEqual(sorted(new), sorted(old))               # nothing in the glance record tells them apart
        for field in ("wet", "growing", "ceiling_water_l", "dripping"):
            self.assertNotIn(field, new)
        dn = json.loads(ok(self, e.step("inspect", h("stain_new")))["message"])
        do = json.loads(ok(self, e.step("inspect", h("stain_old")))["message"])
        self.assertIn("active", dn["looks"])
        self.assertIn("old", do["looks"])
        for field in ("ceiling_water_l", "measured", "stain_m2"):
            self.assertNotIn(field, dn)
        me = m.env(room="living", pos=[2, 2], hold={"left": "meter_1"}, perception="realistic")
        hm = me.realism.handles.handle
        dn = json.loads(ok(self, me.step("inspect", hm("stain_new"), where="closely"))["message"])
        do = json.loads(ok(self, me.step("inspect", hm("stain_old"), where="closely"))["message"])
        self.assertGreater(dn["measured"]["moisture"], 0.3)
        self.assertLess(do["measured"]["moisture"], 0.15)

    def test_dry_looking_mat_is_damp_inside(self):
        m = Mini()
        m.add("mat_1", "generic_item", "living", pos=[2, 3], label="bath mat", material="cotton", wetness=0.02, moisture_inner=0.45,
              size_m=[0.6, 0.4, 0.03], portable=True)
        m.add("meter_1", "generic_item", "living", label="moisture meter", tool_kind="moisture_meter", size_m=[0.05, 0.02, 0.2], portable=True)
        e = m.env(room="living", pos=[2, 2], perception="realistic")
        h = e.realism.handles.handle
        self.assertEqual(e.observe()["visible_entities"][h("mat_1")]["wetness"], "looks dry")
        plain = json.loads(ok(self, e.step("inspect", h("mat_1")))["message"])
        self.assertNotIn("inner_feel", plain)
        pressed = json.loads(ok(self, e.step("inspect", h("mat_1"), where="closely"))["message"])
        self.assertEqual(pressed["inner_feel"], "wet")
        self.assertNotIn("moisture_inner", pressed)
        me = m.env(room="living", pos=[2, 2], hold={"left": "meter_1"}, perception="realistic")
        d = json.loads(ok(self, me.step("inspect", me.realism.handles.handle("mat_1"), where="closely"))["message"])
        self.assertAlmostEqual(d["measured"]["moisture_inner"], 0.45, delta=0.03)

    def test_actions_and_observers_leak_no_ids_or_exact_values(self):
        m = self.kitchen()
        m.add("rag_1", "generic_item", "kitchen", label="rag", material="cotton", wetness=0.9, portable=True, size_m=[0.3, 0.3, 0.01])
        m.add("toilet_1", "toilet", "bath", pos=[2, 0], open=True)
        e = m.env(room="kitchen", pos=[3, 1], hold={"right": "rag_1"}, perception="realistic")
        h = e.realism.handles.handle
        msgs = []
        for action, target, args in (("turn_on_water", h("ksink"), {"temp": "warm", "flow": 0.8}), ("wait", "", {"seconds": 30}),
                                     ("plug_drain", h("ksink"), {}), ("wring", h("rag_1"), {"destination": h("ksink")}),
                                     ("turn_off_water", h("ksink"), {}), ("drain", h("ksink"), {}),
                                     ("open", h("sink_cab"), {}), ("shut_off", h("k_hot"), {"turns": 1.0})):
            r = e.step(action, target, **args)
            msgs.append(r["message"])
            self.assertEqual(_leaked_ids(e, r["message"]), set(), r["message"])
            self.assertEqual(_leaked_ids(e, e.observe()), set())
        text = " | ".join(msgs)
        self.assertNotIn("L/min", text)
        self.assertNotIn("% open", text)
        self.assertIsNone(re.search(r"\d\.\d{3}", text), text)             # estimates ("about 0.10 L"), never exact liters
        self.assertIn("it still turns", text)
        ok(self, e.step("turn_on_water", h("ksink"), temp="cold", flow=0.5))
        ok(self, e.step("move", "living"))
        sounds = e.observe().get("sounds", [])
        self.assertTrue(sounds)
        self.assertEqual(_leaked_ids(e, sounds), set())
        for s in sounds:
            self.assertEqual(set(s), {"kind", "source_hint", "loudness"})
        # a clogged toilet: the flush and plunge reports say how the bowl behaves, not how much clog is left
        m = Mini()
        m.add("toilet_1", "toilet", "bath", pos=[2, 0], open=True, bowl_l=3.0, _clog={"kind": "paper", "amount": 0.85})
        m.add("flange_1", "flange_plunger", "bath", pos=[1, 1])
        e = m.env(room="bath", pos=[2, 1], hold={"left": "flange_1"}, perception="realistic")
        h = e.realism.handles.handle
        r = ok(self, e.step("flush", h("toilet_1")))
        self.assertIsNone(re.search(r"\d\.\d L", r["message"]), r["message"])
        r = ok(self, e.step("plunge", h("toilet_1"), strokes=5))
        self.assertNotIn("%", r["message"])
        self.assertNotIn("flange", r["message"])
        self.assertEqual(_leaked_ids(e, r["message"]), set())

    def test_generated_house_inspections_show_no_hidden_or_exact_measure_fields(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.realism import MEASURE
        reg = default_registry()
        mine = {t for t, src in getattr(reg, "sources", {}).items() if src == "plumbing_liquids.json"}
        for seed in (0, 4):
            config = generate_house(seed, dwelling="house")
            rooms = [r for kind in ("kitchen", "bathroom") for r in sorted(config["rooms"]) if config["rooms"][r].get("type") == kind][:2]
            checked = 0
            for rid in rooms:
                c = copy.deepcopy(config)
                c["robot"] = {"room": rid}
                e = HouseEnv(c, seed=seed, perception="realistic")
                realism = e.realism
                for handle in sorted(e.observe()["visible_entities"]):
                    eid = realism.handles.resolve(handle)
                    rec = e.world.s["entities"].get(eid) or {}
                    if rec.get("type") not in mine or not e.step("approach", handle)["ok"]:
                        continue
                    r = e.step("inspect", handle)
                    if not r["ok"]:
                        continue
                    checked += 1
                    self.assertEqual(_leaked_ids(e, r["message"]), set(), (seed, eid))
                    after = e.observe()["visible_entities"].get(handle, {})
                    rec = e.world.s["entities"][eid]
                    for field, value in after.items():
                        if field not in rec:
                            continue
                        tier = realism.tier(rec, field)
                        self.assertNotEqual(tier, "never", (seed, eid, field))
                        if tier == "measure" and isinstance(rec[field], float) and realism.knowledge.level(eid, rec, field) < MEASURE:
                            self.assertNotEqual(value, rec[field], (seed, eid, field))
            self.assertGreater(checked, 0, seed)


# ======================================================================================
# perception hardening (kernel v2.8, red-team round): names, clocks and fixed draws

def _jugs():
    """Two unread jugs that look alike (kernel group spray_bottle, read-resolved) holding liquids that pour at very
    different rates (bleach 1.0, fabric softener 0.25), and a bucket on the kitchen floor."""
    m = Mini()
    for eid, liquid in (("jug_a", "bleach"), ("jug_b", "fabric_softener")):
        m.add(eid, "generic_item", "kitchen", pos=[2, 2], label=liquid.replace("_", " ") + " jug", category="bottle", looks_like="spray_bottle",
              liquid_container=True, liquid=liquid, liquid_l=1.5, capacity_l=2.0, material="plastic", portable=True,
              size_m=[0.12, 0.1, 0.28], label_text=liquid.upper(), openable=True, open=True)
    m.add("bucket_1", "generic_item", "kitchen", pos=[3, 2], label="bucket", category="bucket", liquid_container=True, liquid=None,
          liquid_l=0.0, capacity_l=10.0, material="plastic", portable=True, size_m=[0.3, 0.3, 0.3])
    return m


class PerceptionHardening(unittest.TestCase):
    def pour(self, jug, perception="realistic", **args):
        e = _jugs().env(room="kitchen", pos=[3, 1], hold={"right": jug}, perception=perception)
        h = e.realism.handles.handle if perception == "realistic" else (lambda x: x)
        t0 = e.world.s["time_s"]
        r = ok(self, e.step("pour", h(jug), h("bucket_1"), **args))
        return e, h, r["message"].replace(h(jug), "<jug>").replace(h("bucket_1"), "<bucket>"), e.world.s["time_s"] - t0

    def test_pouring_from_an_unread_lookalike_names_no_liquid_and_takes_the_same_time(self):
        # red-team: "Poured a splash of chlorine_bleach into ..." and a pour duration that gave the viscosity away
        runs = [self.pour(jug, amount_l=0.05) for jug in ("jug_a", "jug_b")]
        (ea, _, ma, ta), (_, _, mb, tb) = runs
        self.assertEqual(ma, mb)
        self.assertEqual(ta, tb)
        self.assertEqual(ta, round(ta))                                  # whole seconds on the observation clock
        for (e, h, msg, _), jug in zip(runs, ("jug_a", "jug_b")):
            self.assertIn("of liquid", msg)
            for word in ("bleach", "softener", "fabric"):
                self.assertNotIn(word, msg)
            # what went into the bucket stays unnamed: neither the next glance nor an inspection names it
            self.assertNotIn("liquid", e.observe()["visible_entities"][h("bucket_1")])
            d = json.loads(ok(self, e.step("inspect", h("bucket_1")))["message"])
            self.assertNotIn("liquid", d)
            self.assertNotIn("liquid", e.observe()["visible_entities"][h("bucket_1")])
            # nor a puddle of it on the floor
            ok(self, e.step("pour", h(jug), "kitchen", amount_l=0.2))
            text = json.dumps(e.observe())
            for word in ("bleach", "softener"):
                self.assertNotIn(word, text)
            puddles = [k for k, v in e.observe()["visible_entities"].items() if v.get("kind") in ("puddle", "spill")]
            self.assertTrue(puddles)
            for k in puddles:
                d = ok(self, e.step("inspect", k))["message"]
                for word in ("bleach", "softener"):
                    self.assertNotIn(word, d)
        # emptied, the bucket forgets: whatever is put in afterwards is seen afresh
        self.assertTrue(ent(ea, "bucket_1").get("_unidentified_liquid"))
        ea.world.begin()
        L.take(ea.world, "bucket_1", L.liters(ent(ea, "bucket_1")))
        ea.world.commit()
        self.assertNotIn("_unidentified_liquid", ent(ea, "bucket_1"))
        self.assertNotIn("liquid", (ent(ea, "bucket_1").get("_perception") or {}).get("hidden", []))
        # oracle mode keeps the exact name, liters and physical duration
        _, _, mo_a, to_a = self.pour("jug_a", perception="oracle", amount_l=0.05)
        _, _, mo_b, to_b = self.pour("jug_b", perception="oracle", amount_l=0.05)
        self.assertIn("bleach", mo_a)
        self.assertIn("fabric_softener", mo_b)
        self.assertNotEqual(to_a, to_b)

    def test_an_identified_liquid_is_named_but_the_clock_gives_no_exact_volume(self):
        # red-team: (dt - 2) * 0.2 recovered the exact 0.837 L the message rounded to "about 0.85 L"
        m = Mini()
        m.add("pitcher_1", "water_pitcher", "kitchen", pos=[2, 2], liquid="water", liquid_l=0.837, open=True)
        m.add("bowl_1", "generic_item", "kitchen", pos=[3, 2], label="mixing bowl", category="dish", liquid_container=True, liquid=None,
              liquid_l=0.0, capacity_l=3.0, material="glass", portable=True, size_m=[0.3, 0.3, 0.15])
        e = m.env(room="kitchen", pos=[3, 1], hold={"right": "pitcher_1"}, perception="realistic")
        h = e.realism.handles.handle
        t0 = e.world.s["time_s"]
        r = ok(self, e.step("pour", h("pitcher_1"), h("bowl_1")))
        dt = e.world.s["time_s"] - t0
        self.assertIn("about 0.85 L of water", r["message"])
        self.assertEqual(dt, round(2.0 + 0.85 / 0.2))                    # from the estimate, in whole seconds
        self.assertNotAlmostEqual((dt - 2.0) * 0.2, 0.837, places=2)

    def test_inspect_hides_the_level_of_a_closed_opaque_jug_and_secret_levels(self):
        # round-4 reports: an inspect gave fill_frac 0.081 of an opaque refill jug whose liquid_l is a secret field
        m = Mini()
        m.add("refill_1", "generic_item", "kitchen", pos=[2, 2], label="soap refill jug", category="bottle", liquid_container=True,
              liquid="hand_soap", liquid_l=0.08, capacity_l=1.0, material="hdpe", openable=True, open=False, portable=True,
              size_m=[0.1, 0.08, 0.25], temperature_c=19.5)
        m.add("tumbler_1", "generic_item", "kitchen", pos=[3, 2], label="drinking glass", category="dish", liquid_container=True,
              liquid="water", liquid_l=0.2, capacity_l=0.35, material="glass", portable=True, size_m=[0.08, 0.08, 0.12],
              secret_fields=["liquid_l"])
        e = m.env(room="kitchen", pos=[2, 1], perception="realistic")
        h = e.realism.handles.handle
        d = json.loads(ok(self, e.step("inspect", h("refill_1")))["message"])
        for field in ("fill_level", "fill_frac", "liquid_temp_c", "liquid"):
            self.assertNotIn(field, d)
        d = json.loads(ok(self, e.step("inspect", h("tumbler_1")))["message"])
        self.assertNotIn("fill_level", d)
        o = m.env(room="kitchen", pos=[2, 1])
        d = json.loads(ok(self, o.step("inspect", "tumbler_1"))["message"])
        self.assertNotIn("fill_frac", d)                                 # ground truth stays out of the oracle view too
        self.assertIn("fill_frac", json.loads(ok(self, o.step("inspect", "refill_1"))["message"]))

    def test_an_instrument_reading_is_one_fixed_draw(self):
        m = Mini()
        m.kitchen_sink()
        m.add("probe_1", "generic_item", "kitchen", label="instant-read thermometer", tool_kind="thermometer", accuracy_c=0.5,
              size_m=[0.15, 0.03, 0.02], portable=True)
        e = m.env(room="kitchen", pos=[3, 1], hold={"left": "probe_1"}, perception="realistic")
        h = e.realism.handles.handle
        ok(self, e.step("turn_on_water", h("ksink"), temp="cold", flow=1.0))
        ok(self, e.step("wait", seconds=60))
        readings = set()
        for pos in ([3, 1], [2, 1], [4, 1]):
            e.world.s["robot"]["pos"] = list(pos)
            d = json.loads(ok(self, e.step("inspect", h("ksink"), where="closely"))["message"])
            readings.add(d["measured"]["outlet_temp_c"])
        self.assertEqual(len(readings), 1)                               # stepping around does not redraw the error

    def test_plungers_share_neutral_labels(self):
        # red-team: cleaning's rinse routed a "toilet plunger" label to a flush, telling the flange plunger apart
        reg = default_registry()
        for name in ("flange_plunger", "cup_plunger"):
            label = reg.make(name, "bath")["label"]
            self.assertNotIn("toilet", label)
            self.assertNotIn("sink", label)

    def test_a_light_frost_does_not_burst_a_spigot_or_split_a_hose(self):
        # round-4 report: an hour at -2 C burst a hose bib; real bibs need hours of hard frost (about -6 C)
        def house(temp):
            m = Mini()
            m.add("spigot", "outdoor_spigot", "yard", pos=[0, 2], mounted=True, mount_height_m=0.45, frost_free=True, settings={"valve": 0.0})
            m.add("hose", "garden_hose", "yard", pos=[1, 2], attached_to="spigot", liquid_l=2.5, liquid="hose_water")
            m.c["events"] = [{"at_s": 60, "changes": {"weather.temperature_c": temp}}]
            return m
        e = house(-2.0).env(room="living", pos=[1, 1])
        wait(self, e, 4500)
        self.assertFalse(ent(e, "spigot").get("frozen_burst"))
        self.assertFalse(ent(e, "hose").get("frozen_split"))
        self.assertNotIn("damage", failures(e))
        e = house(-8.0).env(room="living", pos=[1, 1])
        wait(self, e, 3 * 3600)
        self.assertTrue(ent(e, "spigot")["frozen_burst"])

    def test_laundry_rooms_keep_room_for_a_washer_and_a_dryer(self):
        # round-4: a tank heater and a laundry tub placed first left small laundry rooms without a dryer spot
        from house_sim.generator import generate_house
        for seed in (3, 5, 97):
            config = generate_house(seed, dwelling="apartment")
            ents = config["entities"]
            for rid, room in config["rooms"].items():
                if room["type"] != "laundry_room":
                    continue
                here = [r for r in ents.values() if r.get("location") == rid]
                if any(r.get("appliance_kind") == "washer" for r in here):
                    self.assertTrue(any(r.get("appliance_kind") == "dryer" for r in here), (seed, rid))

    def test_personal_bottles_name_their_owner_only_on_the_sticker(self):
        from house_sim.generator import generate_house
        config = generate_house(0, dwelling="house")
        bottles = [r for r in config["entities"].values() if r.get("type") == "water_bottle" and r.get("owner")]
        self.assertTrue(bottles)
        for rec in bottles:
            self.assertEqual(rec["label"], "water bottle")
            self.assertTrue(rec.get("name_label"))
