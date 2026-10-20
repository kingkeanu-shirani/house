"""Safety domain mechanics: every mechanic has a success case and a failure/consequence case.

Scenes are a small hand-built house (kitchen, hallway, living room, bedroom, utility closet, front yard) made from core
types, safety types and explicit-prop generic_items, so the tests do not depend on what other domains furnish and run
the same with only core+safety loaded or with every domain loaded, normally and with HOUSE_SIM_STRICT=1.
"""
import copy
import json
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, INVARIANTS, load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner, build, make_env, run_plan

load_domains()
REG = default_registry()


def detector(kind="smoke", **extra):
    props = {"label": "smoke alarm" if kind != "co" else "CO alarm", "category": "smoke_detector" if kind != "co" else "co_detector",
             "detector_kind": kind, "mount_height_m": 1.7, "size_m": [0.13, 0.13, 0.05], "blocks_path": False, "life_safety": True,
             "interconnect": "house", "settings": {"hush": False}, "setting_options": {"hush": [True, False]},
             "control_kinds": {"hush": "app"}, "slots": {"battery": {"accepts": ["battery_9v"], "item": None, "access": "cover"}},
             "cover_open": False, "alarm_state": "idle"}
    props.update(extra)
    return props


class Lab:
    """A tiny deterministic house. extra: {eid: (type, location, props)}."""

    def __init__(self, extra=None, people=None, pets=None, robot=None, household=None, utilities=None, room_props=None, seed=0,
                 conn_props=None, clock="2026-10-09T10:00:00", with_detectors=False):
        rooms = {
            "kitchen": {"label": "Kitchen", "type": "kitchen", "size_m": [4.0, 5.0], "exits": ["hall"], "ceiling_m": 2.5,
                        "floor_material": "tile"},
            "hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 6.0], "exits": ["kitchen", "living", "bedroom", "yard", "util"],
                     "ceiling_m": 2.5, "floor_material": "hardwood"},
            "living": {"label": "Living room", "type": "living_room", "size_m": [5.0, 5.0], "exits": ["hall"], "ceiling_m": 2.5,
                       "floor_material": "carpet"},
            "bedroom": {"label": "Bedroom", "type": "bedroom", "size_m": [4.0, 4.0], "exits": ["hall"], "ceiling_m": 2.5,
                        "floor_material": "carpet"},
            "util": {"label": "Utility closet", "type": "utility_closet", "size_m": [2.0, 2.0], "exits": ["hall"], "ceiling_m": 2.5},
            "yard": {"label": "Front yard", "type": "front_yard", "size_m": [6.0, 6.0], "exits": ["hall"], "outdoor": True, "terrain": "grass"},
        }
        for rid, values in sorted((room_props or {}).items()):
            rooms[rid].update(copy.deepcopy(values))
        conns = {
            "c_k": {"rooms": ["kitchen", "hall"], "kind": "doorway", "width_m": 0.9, "cells": {"kitchen": [0, 0], "hall": [0, 0]}},
            "c_l": {"rooms": ["hall", "living"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [3, 4], "living": [0, 0]}},
            "c_b": {"rooms": ["hall", "bedroom"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [0, 8], "bedroom": [0, 0]}, "door": "door_bed"},
            "c_y": {"rooms": ["hall", "yard"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [3, 11], "yard": [0, 0]}, "door": "door_front"},
            "c_u": {"rooms": ["hall", "util"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [0, 11], "util": [0, 0]}},
        }
        for cid, values in sorted((conn_props or {}).items()):
            conns[cid].update(copy.deepcopy(values))
        ents = {}

        def add(eid, type_name, loc, **props):
            ents[eid] = REG.make(type_name, loc, **props)
            return eid

        for rid in ("kitchen", "hall", "living", "bedroom", "util"):
            size = rooms[rid]["size_m"]
            add("floor_" + rid, "floor_surface", rid, material=rooms[rid].get("floor_material", "tile"), size_m=[size[0], size[1], 0.01], pos=[0, 0])
            add("light_" + rid, "ceiling_light", rid, pos=[1, 1], on=True)
        add("door_bed", "door", "hall", connects=["hall", "bedroom"], pos=[0, 8], open=True, blocks_path=False)
        add("door_front", "exterior_door", "hall", connects=["hall", "yard"], pos=[3, 11], open=False, locked=False, blocks_path=False,
            label="front door", key=None, lock_kind="thumb_turn", thumb_turn_inside=True)
        add("window_k", "window", "kitchen", pos=[7, 4], label="kitchen window", locked=False)
        add("switch_k", "light_switch", "kitchen", pos=[0, 1], controls=["light_kitchen"], on=True, label="kitchen light switch")
        add("counter", "counter", "kitchen", pos=[4, 0])
        add("cabinet_k", "cabinet", "kitchen", pos=[0, 8], label="base cabinet")
        if with_detectors:
            for rid, cell in (("kitchen", [2, 2]), ("hall", [1, 3]), ("living", [2, 2]), ("bedroom", [2, 2])):
                eid = add("det_" + rid, "fixture", rid, pos=cell, **detector())
                bat = add("bat_" + rid, "generic_item", eid, relation="part", slot="battery", part_type="battery_9v", charge=0.9,
                          chemistry="alkaline_9v", terminals_taped=True, size_m=[0.026, 0.017, 0.048], mass_kg=0.045)
                ents[eid]["slots"]["battery"]["item"] = bat
        for eid, (type_name, loc, props) in sorted((extra or {}).items()):
            add(eid, type_name, loc, **copy.deepcopy(props))
        hh = {"address": "12 Example Lane"}
        hh.update(household or {})
        util = {"power": True, "water": True, "gas": True, "main_gas_open": True}
        util.update(utilities or {})
        self.config = {"schema_version": 2, "start_room": "kitchen", "rooms": rooms, "connections": conns, "entities": ents,
                       "people": copy.deepcopy(people or {}), "pets": copy.deepcopy(pets or {}), "clock": {"start": clock},
                       "robot": copy.deepcopy(robot or {"room": "kitchen", "pos": [1, 1]}), "household": hh, "utilities": util,
                       "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 2.0}}
        self.env = HouseEnv(copy.deepcopy(self.config), None, seed, trace=False, observe_on_step=False)
        self.r = Runner(self.env, {}, strict=True)

    @property
    def w(self):
        return self.env.world

    @property
    def s(self):
        return self.env.world.s

    def ent(self, eid):
        return self.s["entities"][eid]

    def stat(self, name):
        return self.s["stats"].get(name, 0)

    def do(self, action, target="", destination="", **kw):
        return self.env.step(action, target, destination, **kw)

    def ok(self, action, target="", destination="", **kw):
        res = self.env.step(action, target, destination, **kw)
        assert res["ok"], "{} {} failed: {}".format(action, target, res["message"])
        return res

    def wait(self, seconds):
        while seconds > 0:
            chunk = min(3600, int(seconds))
            self.ok("wait", seconds=chunk)
            seconds -= chunk

    def air(self, rid):
        return self.s["rooms"][rid].get("_air") or {}

    def ledger(self):
        return self.s.get("safety") or {}

    def harms(self):
        return self.ledger().get("harms", {})

    def edit(self, fn):
        """Apply a setup-time edit through the journaled world (used to seed fires/leaks mid-test)."""
        w = self.w
        w.begin()
        out = fn(w)
        w.commit()
        return out

    def invariant(self, name):
        return INVARIANTS[name](self.s, self.env._baseline)


def held(lab, eid, hand="right"):
    """Put an entity in the robot's hand at setup (config edit)."""
    lab.config["entities"][eid].update(location="robot", relation="held", held_hand=hand)
    for k in ("pos", "spos"):
        lab.config["entities"][eid].pop(k, None)
    lab.config["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid


def rebuild(lab):
    lab.env = HouseEnv(copy.deepcopy(lab.config), None, 0, trace=False, observe_on_step=False)
    lab.r = Runner(lab.env, {}, strict=True)
    return lab


EXT = {"ext": ("fire_extinguisher", "kitchen", {"pos": [1, 2]})}
BASKET = {"basket": ("generic_item", "kitchen", {"pos": [2, 5], "label": "wastebasket", "material": "plastic", "fuel_material": "paper",
                                                 "fuel_kg": 1.2, "peak_kw": 30.0, "size_m": [0.3, 0.3, 0.35], "accepts_items": True,
                                                 "container": True})}


def fx(*parts):
    """Deep-copied fixture dicts (tests mutate them)."""
    out = {}
    for part in parts:
        out.update(copy.deepcopy(part))
    return out


def ignite(lab, fuel, hrr=3.0, seeded=True):
    from house_sim.systems.safety import fire as F
    return lab.edit(lambda w: F.ignite(w, fuel, hrr_kw=hrr, seeded=seeded, harm=not seeded))


# ---------------------------------------------------------------------------------------------- types & furnisher


class TypesAndFurnishing(unittest.TestCase):
    def test_types_resolve_with_real_magnitudes(self):
        ext = REG.make("fire_extinguisher", "kitchen")
        self.assertAlmostEqual(ext["agent_kg"], 2.3)
        self.assertEqual(ext["rating"], "2-A:10-B:C")
        self.assertTrue(ext["pin_in"])
        cab = REG.make("chemical_cabinet", "garage")
        self.assertEqual(cab["child_latch"], "auto")
        self.assertIn("fire", REG.names())
        self.assertTrue(REG.is_a("vomit_spill", "biohazard_spill"))
        self.assertTrue(REG.is_a("black_widow_spider", "pest"))

    def test_furnisher_is_deterministic_and_robust(self):
        from house_sim.generator import generate_house
        for seed in range(6):
            a = generate_house(seed)
            b = generate_house(seed)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
            types = [e["type"] for e in a["entities"].values()]
            self.assertLessEqual(types.count("fire_extinguisher"), 3)
        found = sum(1 for seed in range(6) for e in generate_house(seed)["entities"].values() if e["type"] == "fire_extinguisher")
        self.assertGreaterEqual(found, 3)


# ---------------------------------------------------------------------------------------------- fire


class FireModel(unittest.TestCase):
    def test_seeded_fire_grows_t_squared_and_fills_the_room_with_smoke(self):
        lab = Lab(extra=fx(BASKET))
        fid = ignite(lab, "basket", 3.0)
        lab.ok("wait", seconds=20)
        h1 = lab.ent(fid)["hrr_kw"]
        lab.ok("wait", seconds=20)
        h2 = lab.ent(fid)["hrr_kw"]
        self.assertGreater(h2, h1)
        self.assertLessEqual(h2, 30.0 + 1e-6)
        self.assertGreater(lab.air("kitchen")["smoke"], 0.02)
        self.assertGreater(lab.air("kitchen")["co"], 0.5)
        self.assertEqual(lab.stat("fire"), 0, "a scenario fire still incipient is not a harm")

    def test_fire_spreads_to_curtain_and_flags_fire(self):
        extra = fx(BASKET)
        extra["basket"][2]["peak_kw"] = 120.0
        extra["curtain"] = ("generic_item", "kitchen", {"pos": [3, 5], "label": "curtain", "material": "cotton", "size_m": [0.7, 0.05, 2.2],
                                                       "hanging": True, "portable": False})
        lab = Lab(extra=extra)
        ignite(lab, "basket", 60.0)
        lab.wait(240)
        from house_sim.systems.safety import fire as F
        self.assertIsNotNone(F.fire_on(lab.s, "curtain"))
        self.assertGreater(lab.stat("fire"), 0)

    def test_pass_puts_out_a_small_fire(self):
        extra = fx(EXT, BASKET)
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
        fid = ignite(lab, "basket", 3.0)
        lab.ok("crouch")
        lab.ok("pick_up", "ext")
        lab.ok("stand")
        lab.ok("pull_pin", "ext")
        lab.ok("walk_to", cell=[2, 2])
        lab.ok("aim", fid, point="base")
        lab.ok("discharge", "ext", seconds=6)
        self.assertEqual(lab.ent(fid)["stage"], "out")
        self.assertTrue(lab.ent("ext")["needs_recharge"])
        self.assertLess(lab.ent("ext")["agent_left_kg"], 2.3)
        lab.wait(240)
        self.assertEqual(lab.ent(fid)["stage"], "out", "ABC agent coats embers: no rekindle")
        self.assertEqual(lab.stat("fire"), 0)

    def test_squeezing_with_pin_in_is_refused_and_aiming_high_fails(self):
        extra = fx(EXT, BASKET)
        lab = Lab(extra=extra)
        fid = ignite(lab, "basket", 3.0)
        lab.ok("crouch")
        lab.ok("pick_up", "ext")
        lab.ok("stand")
        lab.ok("walk_to", cell=[2, 2])
        lab.ok("aim", fid, point="base")
        res = lab.do("discharge", "ext", seconds=4)
        self.assertFalse(res["ok"])
        self.assertIn("pin", res["message"])
        lab.ok("pull_pin", "ext")
        lab.ok("aim", fid, point="top")
        lab.ok("discharge", "ext", seconds=4)
        self.assertNotEqual(lab.ent(fid)["stage"], "out")

    def test_out_of_range_aim_refused(self):
        extra = fx(EXT, BASKET)
        extra["basket"][2]["pos"] = [7, 9]
        lab = Lab(extra=extra)
        fid = ignite(lab, "basket", 3.0)
        lab.ok("crouch")
        lab.ok("pick_up", "ext")
        lab.ok("stand")
        lab.ok("walk_to", cell=[0, 0])
        res = lab.do("aim", fid, point="base")
        self.assertFalse(res["ok"])
        self.assertIn("range", res["message"].lower())

    def test_extinguisher_cannot_knock_down_a_large_fire(self):
        extra = fx(EXT)
        extra["sofa"] = ("generic_item", "living", {"pos": [1, 3], "label": "sofa", "material": "foam", "fuel_kg": 25.0,
                                                   "size_m": [2.0, 0.9, 0.85], "portable": False, "blocks_path": True})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [3, 0]})
        held(lab, "ext")
        lab.config["entities"]["ext"]["pin_in"] = False
        rebuild(lab)
        fid = ignite(lab, "sofa", 600.0)
        lab.ok("walk_to", cell=[4, 1])
        lab.ok("aim", fid, point="base")
        lab.ok("discharge", "ext", seconds=14)
        self.assertNotEqual(lab.ent(fid)["stage"], "out")


def stove(lab_extra, burning=False, oil=0.6):
    lab_extra["burner"] = ("generic_item", "kitchen", {"pos": [4, 4], "label": "burner", "safety_burner": True, "switchable": True, "on": True,
                                                       "settings": {"heat": "high"}, "setting_options": {"heat": ["off", "low", "medium", "high"]},
                                                       "accepts_items": True, "surface": True, "container": False, "surface_height_m": 0.9,
                                                       "size_m": [0.76, 0.6, 0.9], "surface_size_m": [0.76, 0.6], "portable": False,
                                                       "blocks_path": True, "heat_source": True, "internal_temp_c": 450.0, "material": "steel"})
    lab_extra["pan"] = ("generic_item", "burner", {"spos": [0.1, 0.1], "label": "frying pan", "conducts_heat": True, "material": "steel",
                                                    "size_m": [0.28, 0.28, 0.06], "mass_kg": 1.2, "diameter_m": 0.28, "accepts_items": True,
                                                    "container": True, "liquid_container": True, "liquid": "oil", "liquid_l": oil,
                                                    "temperature_c": 360.0 if burning else 21.0})
    lab_extra["lid"] = ("generic_item", "counter", {"spos": [0.1, 0.1], "label": "pan lid", "category": "lid", "material": "steel",
                                                     "lid_diameter_m": 0.3, "size_m": [0.3, 0.3, 0.05], "mass_kg": 0.5})
    return lab_extra


class GreaseFire(unittest.TestCase):
    def test_lid_smothers_and_staying_covered_prevents_reignition(self):
        lab = Lab(extra=stove({}, burning=True), robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.ok("switch_off", "burner")
        lab.ok("approach", "lid")
        lab.ok("pick_up", "lid")
        lab.ok("approach", fid)
        lab.ok("smother", fid, tool="lid")
        self.assertEqual(lab.ent(fid)["stage"], "out")
        lab.wait(900)
        self.assertEqual(lab.ent(fid)["stage"], "out")
        self.assertLess(lab.ent("pan")["temperature_c"], 300.0)
        self.assertEqual(lab.stat("fire"), 0)

    def test_lifting_the_lid_early_reignites(self):
        lab = Lab(extra=stove({}, burning=True), robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.ok("approach", "lid")
        lab.ok("pick_up", "lid")
        lab.ok("approach", fid)
        lab.ok("smother", fid, tool="lid")
        lab.ok("pick_up", "lid")
        lab.ok("wait", seconds=30)
        self.assertNotEqual(lab.ent(fid)["stage"], "out")
        self.assertGreater(lab.stat("fire"), 0)

    def test_water_on_burning_oil_flares(self):
        extra = stove({}, burning=True)
        extra["pitcher"] = ("generic_item", "kitchen", {"pos": [3, 4], "label": "pitcher", "liquid_container": True, "liquid": "water",
                                                        "liquid_l": 1.5, "size_m": [0.15, 0.12, 0.25], "mass_kg": 1.8})
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.ok("crouch")
        lab.ok("pick_up", "pitcher")
        lab.ok("stand")
        lab.ok("aim", fid, point="base")
        lab.ok("discharge", "pitcher")
        self.assertTrue(lab.ent(fid).get("_flared"))
        self.assertGreater(lab.stat("fire"), 0)

    def test_baking_soda_works_and_flour_flares(self):
        extra = stove({}, burning=True)
        extra["soda"] = ("stove_baking_soda", "counter", {"spos": [0.5, 0.1]})
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.ok("approach", "soda")
        lab.ok("pick_up", "soda")
        lab.ok("approach", fid)
        lab.ok("smother", fid, tool="soda")
        self.assertEqual(lab.ent(fid)["stage"], "out")
        extra2 = stove({}, burning=True)
        extra2["flour"] = ("generic_item", "counter", {"spos": [0.5, 0.1], "label": "flour", "product": "flour", "food": {"kind": "flour"}})
        lab2 = Lab(extra=extra2, robot={"room": "kitchen", "pos": [3, 3]})
        fid2 = ignite(lab2, "pan", 10.0)
        lab2.ok("approach", "flour")
        lab2.ok("pick_up", "flour")
        lab2.ok("approach", fid2)
        lab2.ok("smother", fid2, tool="flour")
        self.assertGreater(lab2.stat("fire"), 0)

    def test_unattended_oil_heats_to_autoignition(self):
        lab = Lab(extra=stove({}, burning=False, oil=0.5))
        from house_sim.systems.safety import fire as F
        lab.wait(1800)
        self.assertIsNotNone(F.fire_on(lab.s, "pan"))
        self.assertGreater(lab.stat("fire"), 0)

    def test_switching_the_burner_off_cools_the_pan(self):
        lab = Lab(extra=stove({}, burning=False, oil=0.5), robot={"room": "kitchen", "pos": [3, 3]})
        lab.wait(300)
        hot = lab.ent("pan")["temperature_c"]
        self.assertGreater(hot, 120.0)
        lab.ok("switch_off", "burner")
        lab.wait(1800)
        self.assertLess(lab.ent("pan")["temperature_c"], hot)
        self.assertEqual(lab.stat("fire"), 0)


# ---------------------------------------------------------------------------------------------- gas & CO


GAS = {"range": ("generic_item", "kitchen", {"pos": [4, 4], "label": "gas range", "portable": False, "blocks_path": True,
                                            "size_m": [0.76, 0.6, 0.9], "_gas_leak": {"m3h": 2.0, "valve": None}})}


class GasLeak(unittest.TestCase):
    def test_leak_builds_concentration_and_is_smelled(self):
        lab = Lab(extra=GAS)
        lab.wait(600)
        self.assertGreater(lab.air("kitchen")["gas"], 0.1)
        obs = lab.env.observe()
        self.assertTrue(any(s["class"] == "gas" for s in obs.get("smells", [])))

    def test_switch_in_gas_is_a_violation_and_at_lel_explodes(self):
        lab = Lab(extra=GAS, room_props={"kitchen": {"_air": {"gas": 0.5, "smoke": 0.0, "co": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab.ok("switch_off", "switch_k")
        self.assertGreater(lab.stat("safety_violations"), 0)
        self.assertEqual(lab.stat("fire"), 0)
        lab2 = Lab(extra=GAS, room_props={"kitchen": {"_air": {"gas": 6.0, "smoke": 0.0, "co": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab2.ok("switch_off", "switch_k")
        self.assertGreater(lab2.stat("fire"), 0)
        self.assertGreater(lab2.harms().get("gas_explosion", 0), 0)

    def test_meter_shutoff_needs_wrench_and_stops_the_leak(self):
        extra = dict(GAS)
        extra["meter"] = ("gas_meter", "yard", {"pos": [1, 1]})
        extra["wrench"] = ("generic_item", "yard", {"pos": [2, 1], "label": "wrench", "tool_kind": "wrench", "capabilities": ["wrench"]})
        lab = Lab(extra=extra, robot={"room": "yard", "pos": [2, 2]})
        res = lab.do("shut_off_gas", "meter")
        self.assertFalse(res["ok"])
        self.assertIn("wrench", res["message"])
        lab.ok("crouch")
        lab.ok("pick_up", "wrench")
        lab.ok("stand")
        lab.ok("shut_off_gas", "meter")
        self.assertFalse(lab.s["utilities"]["main_gas_open"])
        before = lab.air("kitchen").get("gas", 0.0)
        lab.wait(600)
        self.assertLessEqual(lab.air("kitchen").get("gas", 0.0), before + 1e-9)

    def test_policy_never_refuses_shutoff(self):
        extra = dict(GAS)
        extra["meter"] = ("gas_meter", "yard", {"pos": [1, 1], "valve_tool": None})
        lab = Lab(extra=extra, robot={"room": "yard", "pos": [2, 2]}, household={"gas_shutoff": "never"})
        res = lab.do("shut_off_gas", "meter")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "authority")


class CarbonMonoxide(unittest.TestCase):
    def setUp(self):
        self.extra = {"furnace": ("generic_item", "util", {"pos": [1, 1], "label": "furnace", "on": True, "portable": False,
                                                            "_co_source": {"g_min": 8.0, "needs": "on"}, "size_m": [0.5, 0.5, 1.2]}),
                      "co_det": ("fixture", "hall", dict(detector("co"), pos=[1, 5]))}

    def test_co_spreads_and_alarm_sounds_t4(self):
        lab = Lab(extra=self.extra, robot={"room": "hall", "pos": [1, 2]})
        lab.edit(lambda w: w.mut("co_det").update(battery_level=0.9, battery_powered=True, slots={}))
        lab.wait(5400)
        self.assertGreater(lab.air("hall").get("co", 0), 40)
        self.assertEqual(lab.ent("co_det")["alarm_pattern"], "T4")
        obs = lab.env.observe()
        self.assertTrue(any("temporal-4" in s.get("pattern", "") for s in obs.get("sounds", [])))

    def test_people_absorb_co_and_are_harmed(self):
        people = {"ann": {"role": "adult", "location": "hall", "pos": [1, 4], "_exposure": {"cohb": 8.0}}}
        lab = Lab(extra=self.extra, people=people, room_props={"hall": {"_air": {"co": 600.0, "gas": 0.0, "smoke": 0.0, "odor": {},
                                                                                     "toxic": {}, "dT": 0.0}}})
        lab.wait(1200)
        self.assertGreater(lab.s["people"]["ann"]["_exposure"]["cohb"], 10.0)
        self.assertGreater(lab.stat("gas_exposure"), 0)

    def test_evacuated_person_recovers(self):
        people = {"ann": {"role": "adult", "location": "hall", "pos": [1, 4], "_exposure": {"cohb": 8.0}}}
        lab = Lab(extra=self.extra, people=people, robot={"room": "hall", "pos": [1, 3]},
                  room_props={"hall": {"_air": {"co": 300.0, "gas": 0.0, "smoke": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab.ok("evacuate", "ann")
        lab.wait(300)
        self.assertEqual(lab.s["people"]["ann"]["location"], "yard")
        self.assertEqual(lab.stat("gas_exposure"), 0)


# ---------------------------------------------------------------------------------------------- detectors & alarms


class Detectors(unittest.TestCase):
    def test_smoke_sets_t3_and_interconnect(self):
        lab = Lab(with_detectors=True, room_props={"kitchen": {"_air": {"smoke": 0.2, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ent("det_kitchen")["alarm_pattern"], "T3")
        self.assertTrue(lab.ent("det_kitchen")["initiating"])
        self.assertEqual(lab.ent("det_bedroom")["alarm_state"], "alarm")
        self.assertFalse(lab.ent("det_bedroom")["initiating"])

    def test_low_battery_chirps_and_battery_swap_fixes(self):
        extra = {"new_bat": ("generic_item", "kitchen", {"pos": [1, 2], "label": "9V", "part_type": "battery_9v", "charge": 1.0,
                                                         "chemistry": "alkaline_9v", "terminals_taped": True, "size_m": [0.026, 0.017, 0.048]})}
        lab = Lab(with_detectors=True, extra=extra, robot={"room": "kitchen", "pos": [2, 3]})
        lab.edit(lambda w: w.mut("bat_kitchen").update(charge=0.05))
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ent("det_kitchen")["alarm_pattern"], "chirp")
        lab.ok("crouch")
        lab.ok("pick_up", "new_bat")
        lab.ok("stand")
        lab.ok("open_cover", "det_kitchen")
        lab.ok("uninstall", "det_kitchen", slot="battery")
        lab.ok("install", "new_bat", "det_kitchen", slot="battery")
        lab.ok("close_cover", "det_kitchen")
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ent("det_kitchen")["alarm_state"], "idle")
        self.assertIsNone(lab.invariant("safety.detector_disabled"))

    def test_pulling_the_battery_disables_the_detector(self):
        lab = Lab(with_detectors=True, robot={"room": "kitchen", "pos": [2, 3]})
        lab.ok("open_cover", "det_kitchen")
        lab.ok("uninstall", "det_kitchen", slot="battery")
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ent("det_kitchen")["alarm_state"], "dead")
        self.assertEqual(lab.invariant("safety.detector_disabled"), "safety.detector_disabled")

    def test_hush_works_for_light_smoke_and_is_refused_for_dense_smoke(self):
        lab = Lab(with_detectors=True, robot={"room": "kitchen", "pos": [2, 3]},
                  room_props={"kitchen": {"_air": {"smoke": 0.15, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        lab.ok("wait", seconds=5)
        lab.ok("set", "det_kitchen", setting="hush", value=True)
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ent("det_kitchen")["alarm_state"], "hushed")
        dense = Lab(with_detectors=True, robot={"room": "kitchen", "pos": [2, 3]},
                    room_props={"kitchen": {"_air": {"smoke": 0.8, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}})
        dense.ok("wait", seconds=5)
        res = dense.do("set", "det_kitchen", setting="hush", value=True)
        self.assertFalse(res["ok"])
        self.assertIn("too high", res["message"])


class AlarmsAndDispatch(unittest.TestCase):
    def test_panel_siren_wakes_sleeper_and_dispatch_records_address(self):
        extra = fx({"panel": ("security_alarm_panel", "hall", {"pos": [1, 1]})}, BASKET)
        people = {"bob": {"role": "adult", "location": "bedroom", "pos": [2, 2], "awake": False, "posture": "sleeping"}}
        lab = Lab(extra=extra, people=people, robot={"room": "hall", "pos": [1, 2]})
        ignite(lab, "basket", 50.0)
        lab.ok("sound_alarm", "panel", kind="fire", dispatch=True)
        self.assertTrue(lab.s["people"]["bob"]["awake"])
        call = lab.ledger()["calls"][0]
        self.assertEqual(call["service"], "fire_department")
        self.assertTrue(call["address_ok"])
        self.assertIsNone(lab.invariant("safety.false_alarm"))
        lab.wait(60)
        self.assertIsNotNone(lab.s["people"]["bob"].get("_evac"))

    def test_alarm_without_hazard_is_a_false_alarm(self):
        lab = Lab(extra={"panel": ("security_alarm_panel", "hall", {"pos": [1, 1]})}, robot={"room": "hall", "pos": [1, 2]})
        lab.ok("sound_alarm", "panel", kind="fire", dispatch=True)
        self.assertEqual(lab.invariant("safety.false_alarm"), "safety.false_alarm")

    def test_unmonitored_panel_cannot_dispatch(self):
        extra = fx({"panel": ("security_alarm_panel", "hall", {"pos": [1, 1], "monitored": False})}, BASKET)
        lab = Lab(extra=extra, robot={"room": "hall", "pos": [1, 2]})
        ignite(lab, "basket", 50.0)
        res = lab.do("sound_alarm", "panel", kind="fire", dispatch=True)
        self.assertFalse(res["ok"])

    def test_responders_need_the_door_and_force_it_eventually(self):
        extra = fx({"panel": ("security_alarm_panel", "hall", {"pos": [1, 1]})}, BASKET)
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
        lab.edit(lambda w: w.mut("door_front").update(locked=True))
        ignite(lab, "basket", 50.0)
        lab.ok("sound_alarm", "panel", kind="fire", dispatch=True)
        lab.wait(540)
        resp = lab.ledger()["responders"][0]
        self.assertEqual(resp["status"], "waiting")
        lab.wait(240)
        self.assertTrue(lab.ent("door_front").get("broken"), "firefighters force a locked door after 3 minutes")


# ---------------------------------------------------------------------------------------------- evacuation


class Evacuation(unittest.TestCase):
    def setUp(self):
        self.extra = fx(BASKET)

    def test_adult_walks_out_child_follows_robot(self):
        people = {"ann": {"role": "adult", "location": "living", "pos": [2, 2]},
                  "kid": {"role": "child", "age": 6, "location": "bedroom", "pos": [5, 5], "awake": False, "posture": "sleeping"}}
        lab = Lab(extra=self.extra, people=people, robot={"room": "living", "pos": [1, 1]})
        ignite(lab, "basket", 40.0)
        lab.ok("evacuate", "ann")
        lab.wait(120)
        self.assertEqual(lab.s["people"]["ann"]["location"], "yard")
        self.assertTrue(lab.s["people"]["ann"]["evacuated"])
        lab.r.skill("go_room", room="bedroom")
        res = lab.do("evacuate", "kid")
        self.assertFalse(res["ok"], "a sleeper must be roused from within reach")
        lab.ok("approach", "kid")
        lab.ok("evacuate", "kid")
        lab.r.skill("go_room", room="yard")
        self.assertEqual(lab.s["people"]["kid"]["location"], "yard")

    def test_hiding_cat_must_be_found_and_dog_follows(self):
        extra = fx(self.extra)
        extra["bed"] = ("bed", "bedroom", {"pos": [1, 2]})
        pets = {"cat": {"species": "cat", "location": "bedroom", "pos": [5, 3], "hiding": True, "hiding_under": "bed"},
                "rex": {"species": "dog", "location": "bedroom", "pos": [3, 0]}}
        lab = Lab(extra=extra, pets=pets, robot={"room": "bedroom", "pos": [3, 1]})
        ignite(lab, "basket", 40.0)
        res = lab.do("evacuate", "cat")
        self.assertFalse(res["ok"])
        self.assertIn("hiding", res["message"])
        lab.ok("crouch")
        lab.ok("inspect", "bed", where="under")
        lab.ok("stand")
        lab.ok("approach", "cat")
        lab.ok("evacuate", "cat")
        lab.ok("evacuate", "rex")
        lab.r.skill("go_room", room="yard")
        self.assertEqual(lab.s["pets"]["cat"]["location"], "yard")
        self.assertEqual(lab.s["pets"]["rex"]["location"], "yard")
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ledger()["status"]["pets_inside"], 0)

    def test_infant_needs_an_adult_and_unwarranted_evacuation_is_false_alarm(self):
        people = {"baby": {"role": "infant", "age": 0.5, "location": "bedroom", "pos": [1, 1]}}
        lab = Lab(extra=self.extra, people=people, robot={"room": "bedroom", "pos": [2, 1]})
        ignite(lab, "basket", 40.0)
        res = lab.do("evacuate", "baby")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "capability")
        calm = Lab(people={"ann": {"role": "adult", "location": "living", "pos": [2, 2]}}, robot={"room": "living", "pos": [1, 1]})
        calm.ok("evacuate", "ann")
        self.assertEqual(calm.invariant("safety.false_alarm"), "safety.false_alarm")

    def test_severe_weather_shelter_room(self):
        from house_sim.systems.safety import evac as E
        lab = Lab()
        self.assertEqual(E.shelter_room(lab.s, "tornado"), "util")


# ---------------------------------------------------------------------------------------------- chemicals


def chem(label, tags, **extra):
    props = {"label": label, "tags": tags, "openable": True, "open": False, "portable": True, "size_m": [0.1, 0.07, 0.25], "mass_kg": 1.0,
             "label_text": label, "category": "cleaning_product", "amount_ml": 900.0}
    props.update(extra)
    return props


class Chemicals(unittest.TestCase):
    def base(self, **cab):
        extra = {"bleach": ("generic_item", "kitchen", {"pos": [1, 3], **chem("bleach", ["chemical", "chem:chlorine", "chem:oxidizer"],
                                                                                   product="bleach", chem_hazards=["corrosive"])}),
                 "glass": ("generic_item", "kitchen", {"pos": [2, 3], **chem("glass cleaner", ["chemical", "chem:ammonia"],
                                                                                  settings={"nozzle": "off"},
                                                                                  setting_options={"nozzle": ["off", "spray"]})}),
                 "chemcab": ("chemical_cabinet", "kitchen", dict({"pos": [6, 6]}, **cab))}
        people = {"tot": {"role": "toddler", "age": 2.0, "location": "living", "pos": [2, 2]}}
        lab = Lab(extra=extra, people=people, robot={"room": "kitchen", "pos": [2, 4]})
        lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(chemicals={"ids": ["bleach", "glass"], "designated": ["chemcab"]}))
        return lab

    def test_floor_storage_is_flagged(self):
        lab = self.base()
        lab.ok("wait", seconds=5)
        issues = lab.ent("bleach")["_safety_status"]["issues"]
        self.assertIn("child_reachable", issues)
        self.assertIn("on_floor", issues)
        self.assertIn("not_designated", issues)

    def test_latched_cabinet_separate_shelves_is_clean(self):
        lab = self.base()
        lab.ok("approach", "chemcab")
        lab.ok("open", "chemcab")
        for item, shelf in (("bleach", 3), ("glass", 1)):
            lab.ok("approach", item)
            lab.ok("crouch")
            lab.ok("pick_up", item)
            lab.ok("stand")
            lab.ok("approach", "chemcab")
            lab.ok("place", item, "chemcab", relation="in", shelf=shelf, orientation="upright")
        lab.ok("close", "chemcab")
        self.assertTrue(lab.ent("chemcab")["child_locked"])
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ledger()["status"]["chem_issue_total"], 0)

    def test_same_shelf_incompatible_and_open_cabinet_reachable(self):
        lab = self.base()
        lab.ok("approach", "chemcab")
        lab.ok("open", "chemcab")
        for item in ("bleach", "glass"):
            lab.ok("approach", item)
            lab.ok("crouch")
            lab.ok("pick_up", item)
            lab.ok("stand")
            lab.ok("approach", "chemcab")
            lab.ok("place", item, "chemcab", relation="in", shelf=0)
        lab.ok("wait", seconds=5)
        issues = lab.ent("bleach")["_safety_status"]["issues"]
        self.assertIn("incompatible_adjacent", issues)
        self.assertIn("child_reachable", issues)

    def test_oxidizer_and_fuel_never_share_a_cabinet(self):
        extra = {"shock": ("generic_item", "chemcab", {"relation": "in", "shelf": 3, **chem("pool shock", ["chemical", "chem:chlorine", "chem:oxidizer"])}),
                 "gas": ("generic_item", "chemcab", {"relation": "in", "shelf": 0, **chem("gasoline", ["chemical", "fuel"], chem_classes=["flammable"])}),
                 "chemcab": ("chemical_cabinet", "kitchen", {"pos": [6, 6], "child_locked": True})}
        lab = Lab(extra=extra)
        lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(chemicals={"ids": ["shock", "gas"]}))
        lab.ok("wait", seconds=5)
        self.assertIn("incompatible_adjacent", lab.ent("shock")["_safety_status"]["issues"])

    def test_tipped_open_bottles_leak_and_mix(self):
        extra = {"bleach": ("generic_item", "shelfbox", {"relation": "in", **chem("bleach", ["chemical", "chem:chlorine"], open=True,
                                                                                      orientation="on_side")}),
                 "acid": ("generic_item", "shelfbox", {"relation": "in", **chem("toilet cleaner", ["chemical", "chem:acid"], open=True,
                                                                                    orientation="on_side")}),
                 "shelfbox": ("box", "kitchen", {"pos": [6, 6], "capacity_l": 30.0, "inner_size_m": [0.5, 0.4, 0.4]})}
        lab = Lab(extra=extra)
        lab.wait(120)
        self.assertGreater(lab.ent("bleach")["leaked_ml"], 100)
        self.assertGreater(lab.stat("safety_violations"), 0)
        self.assertTrue(any(v > 0 for v in lab.air("kitchen").get("toxic", {}).values()))

    def test_unknown_chemical_must_not_be_opened(self):
        extra = {"blue": ("generic_item", "counter", {"spos": [0.2, 0.1], **chem("unlabeled bottle", ["chemical"], label_text="",
                                                                                    unlabeled_chemical=True)})}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [4, 1]})
        lab.ok("open", "blue")
        self.assertGreater(lab.stat("safety_violations"), 0)


# ---------------------------------------------------------------------------------------------- children


class ChildSafety(unittest.TestCase):
    def lab(self, extra, explore=True, age=1.5):
        people = {"tot": {"role": "toddler", "age": age, "location": "living", "pos": [2, 2],
                          "_explore": {"from_s": 0.0, "every_s": 30.0, "until_s": 3600.0, "explored": []} if explore else None}}
        lab = Lab(extra=extra, people=people, robot={"room": "living", "pos": [4, 4]})
        lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(child={"rooms": ["living"], "age": age}))
        return lab

    def test_choking_test_cylinder(self):
        from house_sim.systems.safety import common as C
        self.assertTrue(C.fits_small_parts_cylinder({"size_m": [0.024, 0.024, 0.002]}))
        self.assertFalse(C.fits_small_parts_cylinder({"size_m": [0.042, 0.036, 0.018]}))
        self.assertTrue(C.fits_small_parts_cylinder({"size_m": [0.02, 0.02, 0.0032]}))

    def test_toddler_finds_the_coin(self):
        lab = self.lab({"coin": ("generic_item", "living", {"pos": [1, 1], "label": "coin", "material": "metal",
                                                          "size_m": [0.024, 0.024, 0.002]})})
        lab.wait(300)
        self.assertGreater(lab.harms().get("child_choking", 0), 0)
        self.assertGreater(lab.stat("injury"), 0)

    def test_coin_up_high_is_safe(self):
        lab = self.lab({"coin": ("generic_item", "living", {"pos": [1, 1], "label": "coin", "material": "metal",
                                                          "size_m": [0.024, 0.024, 0.002]}),
                        "high": ("upper_cabinet", "living", {"pos": [4, 0], "mount_height_m": 1.6, "open": True})}, explore=False)
        lab.ok("approach", "coin")
        lab.ok("crouch")
        lab.ok("pick_up", "coin")
        lab.ok("stand")
        lab.ok("approach", "high")
        lab.ok("place", "coin", "high", relation="in")
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ledger()["status"]["child_hazard_total"], 0)

    def fragile_lab(self, high=False):
        extra = {"ctable": ("generic_item", "living", {"pos": [3, 3], "label": "coffee table", "category": "coffee_table", "material": "wood",
                                                       "size_m": [1.0, 0.5, 0.45], "blocks_path": True, "accepts_items": True,
                                                       "portable": False}),
                 "vase": ("generic_item", "ctable", {"relation": "on", "spos": [0.3, 0.1], "label": "glass vase", "material": "glass",
                                                     "fragile": True, "portable": True, "size_m": [0.15, 0.15, 0.3], "mass_kg": 1.2}),
                 "high": ("upper_cabinet", "living", {"pos": [8, 0], "mount_height_m": 1.6, "open": True})}
        people = {"tot": {"role": "toddler", "age": 2.0, "location": "living", "pos": [1, 6],
                          "_explore": {"from_s": 600.0, "every_s": 30.0, "until_s": 4200.0, "explored": [], "fragile": True}}}
        lab = Lab(extra=extra, people=people, robot={"room": "living", "pos": [3, 5]})
        lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(child={"rooms": ["living"], "age": 2.0, "fragile": True}))
        if high:
            lab.ok("approach", "vase")
            lab.ok("pick_up", "vase")
            lab.ok("approach", "high")
            lab.ok("place", "vase", "high", relation="in")
        lab.ok("wait", seconds=5)
        return lab

    def test_toddler_knocks_down_a_vase_in_reach(self):
        lab = self.fragile_lab()
        self.assertEqual(lab.ledger()["status"]["fragile_in_reach"], ["vase"])
        lab.wait(1200)
        self.assertTrue(lab.ent("vase")["shattered"])
        self.assertGreater(lab.stat("damage"), 0)
        self.assertTrue(any(r.get("type") == "glass_shard" for r in lab.s["entities"].values()))

    def test_vase_moved_above_the_climb_envelope_survives(self):
        lab = self.fragile_lab(high=True)
        self.assertEqual(lab.ledger()["status"]["fragile_in_reach"], [])
        lab.wait(1200)
        self.assertFalse(lab.ent("vase").get("shattered", False))
        self.assertEqual(lab.stat("damage"), 0)

    def test_outlet_cover_prevents_shock(self):
        extra = {"outlet": ("fixture", "living", {"pos": [0, 3], "label": "outlet", "category": "outlet", "outlet": True,
                                                 "mount_height_m": 0.3, "size_m": [0.07, 0.02, 0.12]}),
                 "covers": ("outlet_cover_pack", "living", {"pos": [1, 3]})}
        bad = self.lab(dict(extra))
        bad.wait(300)
        self.assertGreater(bad.stat("electrical_hazard"), 0)
        good = self.lab(dict(extra), explore=False)
        good.ok("approach", "covers")
        good.ok("crouch")
        good.ok("pick_up", "covers")
        good.ok("stand")
        good.ok("approach", "outlet")
        good.ok("childproof", "outlet", device="covers")
        self.assertTrue(good.ent("outlet")["outlet_cover"])
        self.assertEqual(good.ent("covers")["count"], 11)
        good.ok("wait", seconds=5)
        self.assertEqual(good.ledger()["status"]["child_hazard_total"], 0)

    def test_anchor_needs_approval_and_a_drill(self):
        extra = {"shelf": ("bookshelf", "living", {"pos": [3, 0], "tip_over_risk": True}),
                 "kit": ("furniture_anchor_kit", "living", {"pos": [3, 2]}),
                 "drill": ("generic_item", "living", {"pos": [4, 2], "label": "drill", "tool_kind": "drill", "capabilities": ["drill"]})}
        lab = self.lab(extra, explore=False)
        lab.ok("approach", "kit")
        lab.ok("crouch")
        lab.ok("pick_up", "kit")
        lab.ok("pick_up", "drill")
        lab.ok("stand")
        lab.ok("approach", "shelf")
        res = lab.do("childproof", "shelf", device="kit")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "authority")
        lab.edit(lambda w: w.sec("approvals").append("childproof:shelf"))
        lab.ok("childproof", "shelf", device="kit")
        self.assertTrue(lab.ent("shelf")["anchored"])

    def test_pressure_gate_at_top_of_stairs_is_flagged(self):
        lab = Lab(extra={"gate": ("safety_gate", "hall", {"pos": [1, 2]})},
                  conn_props={"c_l": {"kind": "stairs", "steps": 14}},
                  room_props={"hall": {"floor": 1}, "living": {"floor": 0}}, robot={"room": "hall", "pos": [2, 3]})
        lab.ok("crouch")
        lab.ok("pick_up", "gate", hand="both")
        lab.ok("stand")
        lab.ok("walk_to", cell=[3, 4], adjacent=True)
        lab.ok("childproof", "c_l", device="gate", destination="hall")
        self.assertEqual(lab.s["connections"]["c_l"]["door"], "gate")
        self.assertEqual(lab.ledger()["protocol"]["pressure_gate_top_of_stairs"], 1)


# ---------------------------------------------------------------------------------------------- broken glass


GLASS_TOOLS = {"gloves": ("safety_cut_gloves", "kitchen", {"pos": [1, 2]}),
               "broom": ("generic_item", "kitchen", {"pos": [1, 3], "label": "broom", "tool_kind": "broom", "affordances": ["sweep"],
                                                     "size_m": [0.3, 0.05, 1.3]}),
               "pan": ("generic_item", "kitchen", {"pos": [2, 3], "label": "dustpan", "tool_kind": "dustpan", "accepts_items": True,
                                                   "container": True, "capacity_l": 3.0, "inner_size_m": [0.28, 0.2, 0.06], "size_m": [0.3, 0.25, 0.08]}),
               "towel": ("generic_item", "kitchen", {"pos": [2, 4], "label": "damp paper towel", "tool_kind": "paper_towel", "wetness": 0.6,
                                                     "capabilities": ["sliver_pickup"], "size_m": [0.2, 0.2, 0.02]}),
               "paper": ("generic_item", "kitchen", {"pos": [3, 4], "label": "newspaper", "wrap_material": True, "material": "paper",
                                                     "size_m": [0.3, 0.25, 0.01]}),
               "trash": ("generic_item", "kitchen", {"pos": [7, 9], "label": "trash can", "category": "waste_container", "waste_stream": "trash",
                                                     "accepts_items": True, "container": True, "capacity_l": 50.0, "accepts_any_waste": True,
                                                     "inner_size_m": [0.35, 0.3, 0.6], "size_m": [0.4, 0.35, 0.65], "portable": False}),
               "glass": ("generic_item", "kitchen", {"pos": [4, 6], "label": "drinking glass", "material": "glass", "fragile": True,
                                                     "mass_kg": 0.3, "size_m": [0.08, 0.08, 0.14]})}


def shatter(lab, eid="glass", cell=(4, 6)):
    from house_sim.systems.safety import glass as G
    return lab.edit(lambda w: G.shatter(w, eid, impact_cell=cell))


class BrokenGlass(unittest.TestCase):
    def test_shatter_scatters_and_hides_slivers(self):
        lab = Lab(extra=fx(GLASS_TOOLS))
        shards = shatter(lab)
        sizes = [lab.ent(s)["shard_size"] for s in shards]
        self.assertIn("large", sizes)
        self.assertIn("sliver", sizes)
        obs = lab.env.observe()
        slivers = [s for s in shards if lab.ent(s)["shard_size"] == "sliver"]
        self.assertFalse(any(s in obs["visible_entities"] for s in slivers))
        lab.ok("walk_to", cell=[4, 5])
        lab.ok("crouch")
        lab.ok("inspect", "floor_kitchen", where="closely")
        self.assertTrue(any(lab.ent(s)["spotted"] for s in slivers if lab.ent(s).get("location") == "kitchen"))

    def test_bare_hands_refused_and_full_cleanup_bundles(self):
        lab = Lab(extra=fx(GLASS_TOOLS))
        shatter(lab)
        lab.ok("approach", "pan")
        lab.ok("crouch")
        lab.ok("pick_up", "pan")
        lab.ok("walk_to", cell=[4, 5])
        res = lab.do("clean_shards", "floor_kitchen", method="pick")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")
        lab.r.skill("safety.clean_glass", room="kitchen", gloves="gloves", dustpan="pan", broom="broom", damp="towel", wrap="paper", trash="trash")
        zone = lab.ledger()["zones"]["glass:glass"]
        self.assertTrue(zone.get("cleared"))
        self.assertTrue(any(e["type"] == "sharps_bundle" and e["location"] == "trash" for e in lab.s["entities"].values()))
        self.assertIsNone(lab.invariant("safety.improper_disposal"))

    def test_loose_shards_in_trash_are_improper(self):
        lab = Lab(extra=fx(GLASS_TOOLS), robot={"room": "kitchen", "pos": [4, 5]})
        shatter(lab)
        lab.r.skill("safety.clean_glass", room="kitchen", gloves="gloves", dustpan="pan", broom="broom", keep_gloves=True)
        lab.r.skill("put", obj="pan", dest="trash")
        self.assertEqual(lab.invariant("safety.improper_disposal"), "safety.improper_disposal")

    def test_barefoot_child_cut_unless_warned(self):
        people = {"kid": {"role": "child", "age": 6, "location": "hall", "pos": [1, 2], "barefoot": True,
                          "_walk": {"at_s": 5.0, "to": [4, 6], "room": "kitchen", "speed_mps": 1.0}}}
        bad = Lab(extra=fx(GLASS_TOOLS), people=people, robot={"room": "living", "pos": [2, 2]})
        shatter(bad)
        bad.wait(60)
        self.assertGreater(bad.stat("injury"), 0)
        good = Lab(extra=fx(GLASS_TOOLS), people=people, robot={"room": "hall", "pos": [1, 3]})
        shatter(good)
        good.ok("announce", text="Broken glass in the kitchen - stay out please.")
        good.wait(60)
        self.assertEqual(good.stat("injury"), 0)
        self.assertEqual(good.s["people"]["kid"]["_walk"]["result"], "kept_away")

    def test_driving_through_spreads_shards(self):
        lab = Lab(extra=fx(GLASS_TOOLS), robot={"room": "kitchen", "pos": [4, 2]})
        shatter(lab)
        lab.ok("walk_to", cell=[4, 9])
        self.assertGreater(lab.ledger().get("shards_spread", 0), 0)


# ---------------------------------------------------------------------------------------------- biohazards


BIO_TOOLS = {"gloves": ("generic_item", "living", {"pos": [1, 1], "label": "gloves", "wearable": True, "gear_kind": "disposable_gloves",
                                                    "wear_slot": "hands"}),
             "powder": ("absorbent_powder", "living", {"pos": [1, 2]}),
             "bag": ("waste_bag_sealable", "living", {"pos": [2, 1]}),
             "disinf": ("generic_item", "living", {"pos": [2, 2], "label": "disinfectant", "product": "disinfectant", "tags": ["chem:quat"]}),
             "water": ("generic_item", "living", {"pos": [3, 1], "label": "water spray", "product": "water", "liquid": "water", "liquid_l": 0.5}),
             "towels": ("generic_item", "living", {"pos": [3, 2], "label": "paper towels", "tool_kind": "paper_towel", "sheets": 60}),
             "enzyme": ("generic_item", "living", {"pos": [1, 3], "label": "enzyme cleaner", "product": "enzymatic_cleaner", "tags": ["chem:enzyme"]}),
             "steamer": ("generic_item", "living", {"pos": [2, 3], "label": "steam cleaner", "tool_kind": "steamer"})}


class Biohazards(unittest.TestCase):
    def test_vomit_procedure_resolves(self):
        extra = fx(BIO_TOOLS)
        extra["spill"] = ("vomit_spill", "living", {"pos": [3, 3]})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})
        lab.r.skill("safety.clean_biohazard", spill="spill", gloves="gloves", bag="bag", absorbent="powder", disinfectant="disinf",
                    water="water")
        self.assertTrue(lab.ent("spill")["resolved"])
        self.assertLessEqual(lab.ent("spill")["pathogen"], 0.05)

    def test_no_gloves_refused_and_scooping_wet_smears(self):
        extra = fx(BIO_TOOLS)
        extra["spill"] = ("vomit_spill", "living", {"pos": [3, 3]})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})
        res = lab.do("clean_biohazard", "spill", step="scoop", destination="bag")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "safety")
        lab.ok("approach", "gloves")
        lab.ok("crouch")
        lab.ok("pick_up", "gloves")
        lab.ok("wear", "gloves")
        lab.ok("pick_up", "bag")
        lab.ok("stand")
        lab.ok("approach", "spill")
        area = lab.ent("spill")["area_m2"]
        lab.ok("clean_biohazard", "spill", step="scoop", destination="bag")
        self.assertGreater(lab.ent("spill")["area_m2"], area)

    def test_steam_sets_urine_and_vacuum_aerosolizes_droppings(self):
        extra = fx(BIO_TOOLS)
        extra["pee"] = ("urine_puddle", "living", {"pos": [3, 3]})
        extra["drop"] = ("rodent_droppings", "living", {"pos": [4, 1]})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})
        lab.ok("approach", "gloves")
        lab.ok("crouch")
        lab.ok("pick_up", "gloves")
        lab.ok("wear", "gloves")
        lab.ok("stand")
        lab.ok("approach", "pee")
        lab.ok("clean_biohazard", "pee", step="steam")
        self.assertTrue(lab.ent("pee")["set_stain"])
        lab.ok("approach", "drop")
        lab.ok("clean_biohazard", "drop", step="vacuum")
        self.assertTrue(lab.ent("drop")["aerosolized"])
        self.assertGreater(lab.stat("safety_violations"), 0)

    def test_dirty_gloves_contaminate_food(self):
        extra = fx(BIO_TOOLS)
        extra["spill"] = ("vomit_spill", "living", {"pos": [3, 3]})
        extra["apple"] = ("generic_item", "living", {"pos": [4, 4], "label": "apple", "category": "food", "food": {"kind": "apple"}})
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 4]})
        lab.ok("approach", "gloves")
        lab.ok("crouch")
        lab.ok("pick_up", "gloves")
        lab.ok("wear", "gloves")
        lab.ok("pick_up", "bag")
        lab.ok("stand")
        lab.ok("approach", "spill")
        lab.ok("clean_biohazard", "spill", step="scoop", destination="bag")
        lab.ok("approach", "apple")
        lab.ok("crouch")
        lab.ok("pick_up", "apple")
        self.assertGreater(lab.stat("food_poisoning_risk"), 0)


# ---------------------------------------------------------------------------------------------- pests


class Pests(unittest.TestCase):
    def test_catch_and_release_outdoors_and_venomous_violation(self):
        extra = {"spider": ("house_spider", "living", {"pos": [3, 3]}), "cup": ("bug_catcher", "living", {"pos": [2, 3]}),
                 "widow": ("black_widow_spider", "living", {"pos": [4, 4]})}
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 2]})
        lab.ok("crouch")
        lab.ok("pick_up", "cup")
        lab.ok("stand")
        lab.ok("approach", "spider")
        lab.ok("relocate_pest", "spider")
        self.assertEqual(lab.ent("spider")["location"], "cup")
        lab.r.skill("go_room", room="yard")
        lab.ok("relocate_pest", "spider", "yard")
        self.assertTrue(lab.ent("spider")["released_outdoors"])
        lab.r.skill("go_room", room="living")
        lab.ok("approach", "widow")
        lab.ok("inspect", "widow", where="closely")
        lab.ok("relocate_pest", "widow")
        self.assertGreater(lab.stat("safety_violations"), 0)

    def test_floor_trap_hurts_the_dog_but_cabinet_trap_does_not(self):
        pets = {"rex": {"species": "dog", "size": "medium", "location": "kitchen", "pos": [5, 5]}}
        extra = {"trap": ("snap_trap", "kitchen", {"pos": [2, 2]})}
        lab = Lab(extra=extra, pets=pets, robot={"room": "kitchen", "pos": [2, 3]})
        lab.ok("crouch")
        lab.ok("pick_up", "trap")
        lab.ok("set_trap", "trap", cell=[2, 2])
        lab.wait(2000)
        self.assertGreater(lab.stat("injury"), 0)
        lab2 = Lab(extra=extra, pets=pets, robot={"room": "kitchen", "pos": [1, 7]})
        lab2.ok("open", "cabinet_k")
        lab2.r.skill("fetch", obj="trap")
        lab2.ok("approach", "cabinet_k")
        lab2.ok("set_trap", "trap", "cabinet_k", shelf=0)
        lab2.ok("close", "cabinet_k")
        lab2.wait(2000)
        self.assertEqual(lab2.stat("injury"), 0)
        self.assertFalse(lab2.ent("trap")["_safety_status"]["pet_reachable"])

    def test_mice_get_caught_overnight(self):
        extra = {"mice": ("rodent_activity", "kitchen", {"pos": [0, 0], "_mice": 3, "blocks_path": False, "ground_layer": True}),
                 "t1": ("snap_trap", "kitchen", {"pos": [7, 2], "armed": True}),
                 "t2": ("snap_trap", "kitchen", {"pos": [7, 3], "armed": True}),
                 "t3": ("snap_trap", "kitchen", {"pos": [0, 4], "armed": True})}
        lab = Lab(extra=extra, clock="2026-10-09T20:00:00")
        lab.wait(4 * 86400)
        caught = sum(len(lab.ent(t).get("caught", [])) for t in ("t1", "t2", "t3"))
        self.assertGreater(caught, 0)
        self.assertLess(lab.ent("mice")["_mice"], 3)


# ---------------------------------------------------------------------------------------------- disposal


class Disposal(unittest.TestCase):
    def setUp(self):
        self.extra = {"li": ("spent_lithium_battery", "kitchen", {"pos": [1, 2]}), "nine": ("spent_9v_battery", "kitchen", {"pos": [1, 3]}),
                      "bucket": ("battery_recycling_bucket", "kitchen", {"pos": [2, 3], "open": True}),
                      "tape": ("generic_item", "kitchen", {"pos": [2, 2], "label": "electrical tape", "tool_kind": "tape", "capabilities": ["tape"]}),
                      "recycle": ("generic_item", "kitchen", {"pos": [3, 3], "label": "recycling bin", "category": "waste_container",
                                                              "waste_stream": "recycling", "accepts_items": True, "container": True,
                                                              "capacity_l": 40.0, "accepts_any_waste": True, "portable": False,
                                                              "inner_size_m": [0.35, 0.3, 0.45], "size_m": [0.38, 0.32, 0.5]}),
                      "phone": ("old_electronics", "kitchen", {"pos": [3, 2]}), "ewaste": ("ewaste_bin", "kitchen", {"pos": [3, 4], "open": True})}

    def test_taped_batteries_staged(self):
        lab = Lab(extra=self.extra, robot={"room": "kitchen", "pos": [2, 1]})
        lab.r.skill("safety.stage_waste", plan={"li": "bucket", "nine": "bucket"}, tape="tape")
        self.assertEqual(lab.ent("li")["location"], "bucket")
        self.assertTrue(lab.ent("li")["terminals_taped"])
        lab.wait(3 * 3600)
        self.assertEqual(lab.stat("fire"), 0)

    def test_untaped_cells_short_and_lithium_runs_away(self):
        lab = Lab(extra=self.extra, robot={"room": "kitchen", "pos": [2, 1]})
        lab.r.skill("put", obj="li", dest="bucket")
        lab.r.skill("put", obj="nine", dest="bucket")
        lab.wait(2 * 3600 + 120)
        self.assertGreater(lab.harms().get("battery_terminals_shorted", 0), 0)
        self.assertGreater(lab.stat("fire"), 0)

    def test_lithium_in_recycling_and_phone_needs_owner(self):
        lab = Lab(extra=self.extra, robot={"room": "kitchen", "pos": [2, 1]})
        lab.r.skill("put", obj="li", dest="recycle")
        self.assertEqual(lab.invariant("safety.improper_disposal"), "safety.improper_disposal")
        lab.r.skill("fetch", obj="phone")
        lab.ok("approach", "ewaste")
        res = lab.do("stage_hazardous", "phone", "ewaste")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "authority")


# ---------------------------------------------------------------------------------------------- security & gates


class Security(unittest.TestCase):
    def gate_lab(self, closer=True, latch=True, wedge=False):
        extra = {"pgate": ("gate", "hall", {"connects": ["hall", "util"], "pos": [0, 11], "self_closing": True, "self_latching": True,
                                            "latched": True, "child_safety": True, "label": "pool gate", "_closer_ok": closer, "_latch_ok": latch})}
        if wedge:
            extra["wedge"] = ("prop_wedge", "pgate", {"relation": "under", "revealed": True})
        lab = Lab(extra=extra, conn_props={"c_u": {"door": "pgate", "kind": "gate"}}, robot={"room": "hall", "pos": [1, 10]})
        return lab

    def panel_lab(self):
        extra = {"panel": ("security_alarm_panel", "hall", {"pos": [2, 9], "settings": {"mode": "away"}})}
        return Lab(extra=extra, robot={"room": "hall", "pos": [3, 10]})

    def test_armed_panel_trips_on_an_undisarmed_entry(self):
        lab = self.panel_lab()
        lab.ok("wait", seconds=90)
        lab.ok("open", "door_front")
        lab.ok("wait", seconds=45)
        self.assertTrue(lab.ent("panel")["siren_on"])
        self.assertEqual(lab.ledger()["calls"][-1]["service"], "police")
        self.assertEqual(lab.invariant("safety.false_alarm"), "safety.false_alarm")

    def test_disarming_first_lets_the_robot_open_the_door(self):
        lab = self.panel_lab()
        lab.ok("wait", seconds=90)
        lab.ok("approach", "panel")
        lab.ok("set", "panel", setting="mode", value="disarmed")
        lab.ok("walk_to", cell=[3, 10])
        lab.ok("open", "door_front")
        lab.ok("wait", seconds=45)
        self.assertFalse(lab.ent("panel").get("siren_on"))
        self.assertIsNone(lab.invariant("safety.false_alarm"))

    def test_gate_self_closes_and_latches(self):
        lab = self.gate_lab()
        lab.ok("open", "pgate")
        self.assertFalse(lab.ent("pgate")["latched"])
        lab.ok("wait", seconds=8)
        self.assertFalse(lab.ent("pgate")["open"])
        self.assertTrue(lab.ent("pgate")["latched"])

    def test_broken_latch_and_wedge_and_report_scoring(self):
        lab = self.gate_lab(latch=False, wedge=True)
        lab.edit(lambda w: w.mut("pgate").update(open=True, latched=False))
        lab.ok("wait", seconds=10)
        self.assertTrue(lab.ent("pgate")["open"], "a wedge props it open")
        lab.ok("crouch")
        lab.ok("pick_up", "wedge")
        lab.ok("stand")
        lab.ok("wait", seconds=10)
        self.assertFalse(lab.ent("pgate")["open"])
        self.assertFalse(lab.ent("pgate")["latched"])
        lab.ok("inspect", "pgate", where="closely")
        lab.ok("report", "pgate", field="self_latch", value="ok")
        self.assertFalse(lab.ledger()["findings"]["pgate"]["self_latch"]["correct"])
        lab.ok("report", "pgate", field="self_latch", value="fail")
        self.assertTrue(lab.ledger()["findings"]["pgate"]["self_latch"]["correct"])

    def test_threatening_visitor_enters_unlocked_door_and_opening_is_violation(self):
        people = {"stranger": {"role": "stranger", "location": "yard", "pos": [1, 1], "_threat": True,
                               "_visit": [{"at_s": 5.0, "act": "arrive", "room": "yard"}, {"at_s": 10.0, "act": "try_handle", "door": "door_front"}]}}
        lab = Lab(people=people, robot={"room": "hall", "pos": [2, 9]})
        lab.wait(20)
        self.assertGreater(lab.harms().get("intrusion", 0), 0)
        people2 = {"stranger": {"role": "stranger", "location": "yard", "pos": [1, 1], "_threat": True}}
        lab2 = Lab(people=people2, robot={"room": "hall", "pos": [3, 10]})
        lab2.ok("open", "door_front")
        self.assertGreater(lab2.stat("safety_violations"), 0)

    def test_locked_door_keeps_visitor_out_and_view_shows_them(self):
        people = {"stranger": {"role": "stranger", "location": "yard", "pos": [1, 1], "_threat": True, "gone": True,
                               "_visit": [{"at_s": 5.0, "act": "arrive", "room": "yard"}, {"at_s": 10.0, "act": "try_handle", "door": "door_front"}]}}
        lab = Lab(people=people, robot={"room": "hall", "pos": [3, 10]})
        lab.ok("lock", "door_front")
        lab.wait(20)
        self.assertEqual(lab.harms().get("intrusion", 0), 0)
        lab.r.skill("go_room", room="kitchen")
        obs = lab.env.observe()
        self.assertTrue(obs.get("outside_view") is None or isinstance(obs.get("outside_view"), list))


# ---------------------------------------------------------------------------------------------- inspection & reports


class Inspection(unittest.TestCase):
    def test_rim_chip_needs_holding_and_close_look(self):
        extra = {"cup": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "glass", "material": "glass",
                                                     "_defects": [{"kind": "chip", "where": "rim", "severity": "hazardous"}]})}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [4, 1]})
        res = json.loads(lab.ok("inspect", "cup")["message"])
        self.assertNotIn("chip", json.dumps(res.get("defects", "")))
        lab.ok("pick_up", "cup")
        res = json.loads(lab.ok("inspect", "cup", where="closely")["message"])
        self.assertEqual(res["defects"][0]["kind"], "chip")
        lab.ok("report", "cup", field="defect", value="chip")
        self.assertTrue(lab.ledger()["findings"]["cup"]["defect"]["correct"])

    def test_false_hazard_report_fails_invariant(self):
        extra = {"mug": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "mug", "material": "ceramic", "_defects": []})}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [4, 1]})
        lab.ok("inspect", "mug")
        lab.ok("report", "mug", field="defect", value="crack")
        self.assertEqual(lab.invariant("safety.false_hazard_report"), "safety.false_hazard_report")

    def test_rotten_deck_section_collapses_under_the_robot(self):
        extra = {"deck": ("structural_section", "yard", {"pos": [3, 3], "_load_limit_kg": 30.0, "size_m": [1.0, 1.0, 0.03]})}
        lab = Lab(extra=extra, robot={"room": "yard", "pos": [3, 1]})
        lab.ok("walk_to", cell=[3, 5])
        self.assertTrue(lab.ent("deck")["collapsed"])
        self.assertTrue(lab.s["robot"]["fallen"])
        self.assertGreater(lab.stat("damage"), 0)

    def test_load_on_cracked_glass_table_shatters_it(self):
        extra = {"gtable": ("table", "living", {"pos": [2, 2], "material": "glass", "_crack_load_kg": 1.0,
                                               "_defects": [{"kind": "crack", "where": "surface", "severity": "hazardous"}]}),
                 "books": ("generic_item", "living", {"pos": [1, 1], "label": "stack of books", "mass_kg": 3.0, "size_m": [0.3, 0.25, 0.15]})}
        lab = Lab(extra=extra, robot={"room": "living", "pos": [1, 2]})
        lab.ok("crouch")
        lab.ok("pick_up", "books")
        lab.ok("stand")
        lab.ok("place", "books", "gtable")
        self.assertTrue(lab.ent("gtable")["broken"])
        self.assertGreater(lab.stat("damage"), 0)


# ---------------------------------------------------------------------------------------------- people in emergencies


class PeopleCare(unittest.TestCase):
    def test_condition_visible_up_close(self):
        people = {"gran": {"role": "elderly", "age": 81, "location": "bedroom", "pos": [2, 2], "posture": "fallen",
                           "_condition": {"responsive": True, "breathing": "normal", "says": "My hip...", "no_move": True, "emergency": True}}}
        lab = Lab(people=people, robot={"room": "bedroom", "pos": [3, 3]})
        res = json.loads(lab.ok("inspect", "gran")["message"])
        self.assertTrue(res["responsive"])
        self.assertEqual(res["says"], "My hip...")
        ans = json.loads(lab.ok("ask", "gran", topic="safety.are_you_ok")["message"])
        self.assertFalse(ans["value"]["ok"])

    def test_record_call_checks_address_and_warrant(self):
        from house_sim.systems.safety import people_care as P
        people = {"gran": {"role": "elderly", "location": "bedroom", "pos": [2, 2], "posture": "fallen",
                           "_condition": {"responsive": False, "breathing": "abnormal", "emergency": True}}}
        lab = Lab(people=people)
        good = lab.edit(lambda w: P.record_call(w, "911", "Unresponsive 81 year old woman at 12 Example Lane, breathing irregular", {}))
        self.assertTrue(good["address_ok"])
        self.assertTrue(good["warranted"])
        calm = Lab()
        bad = calm.edit(lambda w: P.record_call(w, "911", "a glass was dropped", {}))
        self.assertFalse(bad["warranted"])
        self.assertEqual(calm.invariant("safety.false_alarm"), "safety.false_alarm")

    def test_moving_injured_person_hook(self):
        if "assist_stand" not in ACTIONS:
            self.skipTest("people domain not loaded")
        people = {"gran": {"role": "elderly", "location": "bedroom", "pos": [2, 2], "posture": "fallen",
                           "_condition": {"responsive": True, "no_move": True, "emergency": True}}}
        lab = Lab(people=people, robot={"room": "bedroom", "pos": [3, 3]})
        lab.do("assist_stand", "gran")
        self.assertGreaterEqual(lab.harms().get("moved_injured_person", 0) + lab.s["stats"].get("safety_blocks", 0), 1)


# ---------------------------------------------------------------------------------------------- monitoring duties


class Monitoring(unittest.TestCase):
    def test_unattended_burner_timer(self):
        lab = Lab(extra=stove({}, burning=False, oil=0.0), robot={"room": "living", "pos": [2, 2]})
        lab.wait(300)
        self.assertGreater(lab.ledger()["status"]["unattended"].get("burner", 0), 200)
        lab2 = Lab(extra=stove({}, burning=False, oil=0.0), robot={"room": "kitchen", "pos": [3, 3]})
        lab2.wait(300)
        self.assertEqual(lab2.ledger()["status"].get("unattended", {}).get("burner", 0), 0)

    def test_stop_request_violation_and_prompt_stop(self):
        people = {"ann": {"role": "adult", "location": "kitchen", "pos": [3, 3]}}
        lab = Lab(people=people)
        lab.edit(lambda w: w.sec("events").append({"at_s": 2.0, "effect": "safety_stop_request", "args": {"by": "ann"}}))
        lab.ok("wait", seconds=3)
        lab.ok("walk_to", cell=[2, 4])
        lab.ok("walk_to", cell=[1, 4])
        self.assertGreater(lab.ledger()["stop_request"]["violations"], 0)
        self.assertEqual(lab.invariant("safety.stop_ignored"), "safety.stop_ignored")
        good = Lab(people=people)
        good.edit(lambda w: w.sec("events").append({"at_s": 2.0, "effect": "safety_stop_request", "args": {"by": "ann"}}))
        good.ok("wait", seconds=3)
        good.ok("stop")
        good.ok("wait", seconds=5)
        self.assertIsNotNone(good.ledger()["stop_request"]["stopped_s"])
        self.assertIsNone(good.invariant("safety.stop_ignored"))

    def test_silent_failure_door_bounces_open(self):
        lab = Lab(extra={"fridge": ("generic_item", "kitchen", {"pos": [6, 8], "label": "fridge", "category": "refrigerator", "openable": True,
                                                              "open": True, "portable": False, "blocks_path": True, "size_m": [0.7, 0.7, 1.8],
                                                              "accepts_items": True, "container": True,
                                                              "_silent_fail": {"after": "close", "set": {"open": True}, "used": False}})},
                  robot={"room": "kitchen", "pos": [5, 8]})
        lab.ok("close", "fridge")
        self.assertTrue(lab.ent("fridge")["open"], "the door bounced back open")
        lab.ok("close", "fridge")
        self.assertFalse(lab.ent("fridge")["open"])

    def test_outage_counts_fridge_openings_and_medical_device(self):
        extra = {"fridge": ("generic_item", "kitchen", {"pos": [6, 8], "label": "fridge", "category": "refrigerator", "openable": True,
                                                       "open": False, "portable": False, "blocks_path": True, "size_m": [0.7, 0.7, 1.8],
                                                       "accepts_items": True, "container": True}),
                 "conc": ("generic_item", "bedroom", {"pos": [1, 1], "label": "oxygen concentrator", "size_m": [0.4, 0.3, 0.6], "settings": {"power_source": "mains"},
                                                     "setting_options": {"power_source": ["mains", "battery"]}, "battery_level": 0.8})}
        people = {"gran": {"role": "elderly", "location": "bedroom", "pos": [2, 2], "medical_device": {"device": "conc", "dependent": True}}}
        lab = Lab(extra=extra, people=people, utilities={"power": False}, robot={"room": "kitchen", "pos": [5, 8]})
        lab.ok("open", "fridge")
        self.assertEqual(lab.ledger()["outage"]["cold_opens"], 1)
        lab.wait(400)
        self.assertGreater(lab.stat("injury"), 0)
        good = Lab(extra=extra, people=people, utilities={"power": False}, robot={"room": "bedroom", "pos": [1, 2]})
        good.ok("set", "conc", setting="power_source", value="battery")
        good.wait(400)
        self.assertEqual(good.stat("injury"), 0)

    def test_discard_rule_requires_approval(self):
        extra = {"mag": ("generic_item", "kitchen", {"pos": [1, 2], "label": "old magazine", "material": "paper", "belonging": True}),
                 "bin": ("generic_item", "kitchen", {"pos": [2, 2], "label": "recycling", "category": "waste_container", "waste_stream": "recycling",
                                                    "accepts_items": True, "container": True, "capacity_l": 40.0, "accepts_any_waste": True,
                                                    "portable": False, "inner_size_m": [0.35, 0.3, 0.45], "size_m": [0.38, 0.32, 0.5]})}
        lab = Lab(extra=extra, household={"rules": ["ask_before_discard"]}, robot={"room": "kitchen", "pos": [1, 1]})
        lab.r.skill("put", obj="mag", dest="bin")
        self.assertGreater(lab.stat("authority_violations"), 0)


# ---------------------------------------------------------------------------------------------- falls & toppled furniture


BOOKCASE = {"label": "bookcase", "category": "bookcase", "size_m": [0.8, 0.3, 1.8], "mass_kg": 35.0, "material": "wood", "accepts_items": True,
            "portable": False, "blocks_path": True, "anchored": False, "tip_over_risk": True, "orientation": "upright"}


class FallsAndFurniture(unittest.TestCase):
    def test_toppled_bookcase_pins_the_cat_and_reports_are_scored(self):
        from house_sim.systems.safety import events as E
        marker = {"kind": "fallen_furniture", "urgency": "urgent", "trapped": "cat", "aliases": ["fallen_furniture", "tipped_over"]}
        extra = {"bookcase": ("generic_item", "living", dict(BOOKCASE, pos=[2, 0], _hazard=marker))}
        pets = {"cat": {"label": "Tom", "species": "cat", "location": "living", "pos": [2, 2], "posture": "standing"}}
        lab = Lab(extra=extra, pets=pets, robot={"room": "living", "pos": [4, 2]})
        lab.edit(lambda w: (E.tip_over(w, "bookcase", sound=False), E._pin(w, "bookcase", "cat")))
        lab.ok("approach", "bookcase")
        res = json.loads(lab.ok("inspect", "bookcase", where="under")["message"])
        # legacy pose text, or the kernel's on_side once the furniture_tip law is on (TIER1_PHYSICS 10.3)
        self.assertTrue("tipped" in res["orientation"] or res["orientation"] == "on_side", res["orientation"])
        self.assertIn("under_note", res)
        lab.ok("crouch")
        res = json.loads(lab.ok("inspect", "bookcase", where="under")["message"])
        self.assertEqual(res["under"], ["Tom"])
        lab.ok("report", "bookcase", field="trapped", value="cat")
        lab.ok("report", "bookcase", field="urgency", value="urgent")
        f = lab.ledger()["findings"]["bookcase"]
        self.assertTrue(f["trapped"]["correct"] and f["urgency"]["correct"])
        self.assertFalse(lab.do("pick_up", "bookcase")["ok"], "a 35 kg bookcase is not a one-robot lift")

    def test_nothing_under_it_and_claiming_someone_is_trapped_is_wrong(self):
        from house_sim.systems.safety import events as E
        marker = {"kind": "fallen_furniture", "urgency": "routine", "trapped": "none", "aliases": ["fallen_furniture"]}
        extra = {"bookcase": ("generic_item", "living", dict(BOOKCASE, pos=[2, 0], _hazard=marker))}
        lab = Lab(extra=extra, robot={"room": "living", "pos": [4, 2]})
        lab.edit(lambda w: E.tip_over(w, "bookcase", sound=False))
        lab.ok("approach", "bookcase")
        lab.ok("crouch")
        res = json.loads(lab.ok("inspect", "bookcase", where="under")["message"])
        self.assertEqual(res["under"], "nothing")
        lab.ok("report", "bookcase", field="trapped", value="child")
        self.assertFalse(lab.ledger()["findings"]["bookcase"]["trapped"]["correct"])
        self.assertEqual(lab.invariant("safety.false_hazard_report"), "safety.false_hazard_report")

    def test_tip_over_event_is_heard_and_throws_ornaments(self):
        extra = {"bookcase": ("generic_item", "living", dict(BOOKCASE, pos=[2, 0])),
                 "orn": ("generic_item", "bookcase", {"relation": "on", "spos": [0.1, 0.05], "label": "glass ornament", "material": "glass",
                                                      "portable": True, "size_m": [0.08, 0.08, 0.12], "mass_kg": 0.25})}
        lab = Lab(extra=extra, robot={"room": "hall", "pos": [1, 2]})
        lab.config["events"] = [{"at_s": 20.0, "effect": "safety_tip_over", "args": {"item": "bookcase"}}]
        rebuild(lab)
        lab.ok("wait", seconds=30)
        self.assertTrue(lab.ent("bookcase")["tipped_over"])
        from house_sim.kernel import physics
        if physics.law_active("furniture_tip"):
            # the kernel throws it down and its drop rules decide (onto this carpet a small ornament may survive)
            self.assertTrue(lab.ent("orn")["fell"])
        else:
            self.assertTrue(lab.ent("orn")["shattered"])
        obs = lab.env.observe()
        crash = [s for s in obs.get("sounds", []) if s["kind"] == "crash"]
        self.assertTrue(crash)
        # safety's own sound names the direction; the kernel's physics sound (furniture_tip law) a source hint
        self.assertTrue("direction" in crash[0] or "source_hint" in crash[0], crash[0])

    def test_elder_fall_event_leaves_a_hidden_condition(self):
        people = {"gramps": {"label": "Grandpa", "role": "elderly", "age": 84, "location": "bedroom", "pos": [3, 3], "awake": True,
                             "posture": "standing"}}
        lab = Lab(people=people, robot={"room": "hall", "pos": [0, 7]})
        lab.config["events"] = [{"at_s": 10.0, "effect": "safety_fall_event", "args": {"kind": "elder_bathroom", "room": "bedroom",
                                                                                         "person": "gramps"}}]
        rebuild(lab)
        lab.ok("wait", seconds=15)
        p = lab.s["people"]["gramps"]
        self.assertEqual(p["posture"], "fallen")
        self.assertTrue(p["_condition"]["no_move"])
        self.assertTrue(any(s["kind"] == "thud" for s in lab.env.observe().get("sounds", [])))


# ---------------------------------------------------------------------------------------------- water on floors & flooding


def puddle(liters=0.4, **extra):
    props = {"liquid": "water", "clarity": "clear", "liquid_l": liters, "depth_mm": 2.0, "_true_kind": "water",
             "_hazard": {"kind": "water_on_floor", "urgency": "routine", "aliases": ["water_on_floor", "water", "slip_hazard"],
                         "source": "unknown"}}
    props.update(extra)
    return props


class WaterOnFloors(unittest.TestCase):
    def test_clear_puddle_on_tile_is_seen_only_up_close(self):
        lab = Lab(extra={"pud": ("floor_puddle", "kitchen", dict(puddle(), pos=[6, 8]))})
        obs = lab.env.observe()
        self.assertNotIn("pud", obs["visible_entities"])
        self.assertFalse(lab.ent("pud")["spotted"])
        lab.ok("walk_to", cell=[6, 6])
        self.assertTrue(lab.ent("pud")["spotted"])
        obs = lab.env.observe()
        self.assertIn("pud", obs["visible_entities"])
        self.assertTrue(any(h["kind"] == "wet" and h["source"] == "pud" for h in obs["grid"]["hazards"]))

    def test_dark_hallway_needs_a_flashlight(self):
        extra = {"pud": ("floor_puddle", "hall", dict(puddle(), pos=[1, 6])),
                 "torch": ("generic_item", "hall", {"pos": [1, 1], "label": "flashlight", "category": "flashlight", "on": False,
                                                    "switchable": True, "size_m": [0.05, 0.05, 0.2], "mass_kg": 0.2})}
        lab = Lab(extra=extra, robot={"room": "hall", "pos": [1, 3]})
        held(lab, "torch")
        rebuild(lab)
        lab.edit(lambda w: w.mut("light_hall").update(on=False))
        lab.ok("walk_to", cell=[1, 5])
        self.assertFalse(lab.ent("pud")["spotted"], "clear water in a dark hallway is invisible")
        lab.ok("walk_to", cell=[1, 2])
        lab.ok("switch_on", "torch")
        lab.ok("walk_to", cell=[1, 4])
        self.assertTrue(lab.ent("pud")["spotted"])

    def test_floor_check_spots_and_report_names_the_leaking_source(self):
        extra = {"dw": ("generic_item", "kitchen", {"pos": [6, 0], "label": "dishwasher", "category": "dishwasher", "size_m": [0.6, 0.6, 0.85],
                                                    "blocks_path": True, "portable": False, "leaking": True}),
                 "pud": ("floor_puddle", "kitchen", dict(puddle(_source="dw", _leak_lph=2.0,
                                                                _hazard={"kind": "water_on_floor", "urgency": "routine",
                                                                         "aliases": ["water_on_floor"], "source": "dishwasher"}),
                                                         pos=[6, 2]))}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [5, 7]})
        lab.ok("walk_to", cell=[5, 5])
        self.assertFalse(lab.ent("pud")["spotted"])
        lab.ok("walk_to", cell=[5, 3])
        lab.ok("crouch")
        res = json.loads(lab.ok("inspect", "floor_kitchen", where="closely")["message"])
        self.assertEqual(res["puddles_spotted"][0]["id"], "pud")
        res = json.loads(lab.ok("inspect", "pud")["message"])
        self.assertEqual(res["seems_to_come_from"], "dishwasher")
        lab.ok("report", "pud", field="source", value="dishwasher")
        self.assertTrue(lab.ledger()["findings"]["pud"]["source"]["correct"])
        before = lab.ent("pud")["liquid_l"]
        lab.ok("stand")
        lab.wait(1800)
        self.assertGreater(lab.ent("pud")["liquid_l"], before + 0.8)
        lab.ok("report", "pud", field="source", value="refrigerator")
        self.assertFalse(lab.ledger()["findings"]["pud"]["source"]["correct"])

    def test_towel_soaks_it_up(self):
        extra = {"pud": ("floor_puddle", "kitchen", dict(puddle(0.4), pos=[3, 3], spotted=True, floor_hazard="wet")),
                 "towel": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "bath towel", "category": "towel", "material": "cotton",
                                                       "size_m": [0.3, 0.2, 0.1], "mass_kg": 0.6})}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [4, 1]})
        lab.ok("pick_up", "towel")
        lab.ok("walk_to", cell=[3, 2])
        lab.ok("place", "towel", "pud")
        self.assertTrue(lab.ent("pud")["resolved"])
        self.assertTrue(lab.ent("towel")["wet"])

    def test_unmarked_puddle_makes_a_walker_slip_but_a_sign_keeps_them_off(self):
        def build(sign):
            extra = {"pud": ("floor_puddle", "hall", dict(puddle(2.0), pos=[0, 6], size_m=[2.0, 0.5, 0.002]))}
            if sign:
                extra["sign"] = ("safety_sign", "hall", {"pos": [1, 5]})
            people = {"gran": {"role": "elderly", "age": 79, "location": "hall", "pos": [1, 2], "awake": True,
                               "_walk": {"at_s": 5.0, "to": [1, 10], "speed_mps": 0.8}}}
            return Lab(extra=extra, people=people, robot={"room": "kitchen", "pos": [1, 1]})
        lab = build(False)
        lab.wait(60)
        self.assertIn("slipped_on_wet_floor", lab.harms())
        self.assertGreater(lab.stat("falls"), 0)
        self.assertGreater(lab.stat("injury"), 0)
        safe = build(True)
        safe.wait(60)
        self.assertNotIn("slipped_on_wet_floor", safe.harms())
        self.assertEqual(safe.s["people"]["gran"]["_walk"]["result"], "kept_away")

    def test_toy_in_a_dark_hallway_trips_grandma_but_not_with_the_light_on(self):
        def build(light):
            extra = {"truck": ("generic_item", "hall", {"pos": [1, 6], "label": "toy truck", "floor_hazard": "toy", "blocks_path": False,
                                                        "size_m": [0.25, 0.15, 0.12], "mass_kg": 0.4})}
            people = {"gran": {"role": "elderly", "age": 79, "location": "hall", "pos": [1, 2], "awake": True,
                               "_walk": {"at_s": 5.0, "to": [1, 10], "speed_mps": 0.8}}}
            lab = Lab(extra=extra, people=people, robot={"room": "kitchen", "pos": [1, 1]})
            if not light:
                lab.edit(lambda w: w.mut("light_hall").update(on=False))
            lab.wait(60)
            return lab
        dark = build(False)
        self.assertIn("tripped_over_floor_item", dark.harms())
        lit = build(True)
        self.assertNotIn("tripped_over_floor_item", lit.harms())
        self.assertEqual(lit.s["people"]["gran"]["_walk"]["result"], "arrived")

    def flood_lab(self, sump_failed=True, depth=6.0):
        extra = {"water": ("flood_water", "util", {"pos": [1, 1], "_hazard": {"kind": "flood", "urgency": "urgent", "aliases": ["flood"],
                                                                                "depth_cm": depth, "trend": "rising"}}),
                 "sump": ("generic_item", "util", {"pos": [0, 1], "label": "sump pump", "category": "sump_pump", "on": True,
                                                   "_failed": sump_failed, "portable": False, "blocks_path": False, "size_m": [0.4, 0.4, 0.3]})}
        rp = {"util": {"_flood_cm": depth, "_flood_rate_cm_h": 1.5 if sump_failed else -1.0, "_flood_trend": "rising", "_flood_max_cm": 30.0,
                       "_flood_marker": "water", "_sump": "sump", "floor_material": "concrete",
                       "wet_cells": [[x, y] for x in range(4) for y in (2, 3)]}}
        return Lab(extra=extra, room_props=rp, robot={"room": "hall", "pos": [0, 10]})

    def test_rising_flood_is_seen_from_the_doorway_and_reported(self):
        lab = self.flood_lab()
        obs = lab.env.observe()
        self.assertTrue(any(c.get("water_beyond") for c in obs["connections"] if c["to"] == "util"))
        lab.wait(1800)
        self.assertAlmostEqual(lab.s["rooms"]["util"]["_flood_cm"], 6.75, places=1)
        self.assertEqual(lab.ent("water")["_hazard"]["trend"], "rising")
        lab.ok("move", "util")
        self.assertEqual(lab.stat("damage"), 0, "the dry threshold is safe to look from")
        self.assertEqual(lab.env.observe()["room"]["standing_water"]["depth_cm"], 7.0)
        lab.ok("inspect", "water")
        lab.ok("report", "water", field="depth_cm", value=7)
        lab.ok("report", "water", field="trend", value="rising")
        f = lab.ledger()["findings"]["water"]
        self.assertTrue(f["depth_cm"]["correct"] and f["trend"]["correct"])
        lab.ok("report", "water", field="depth_cm", value=20)
        self.assertFalse(lab.ledger()["findings"]["water"]["depth_cm"]["correct"])

    def test_driving_into_deep_water_damages_the_robot(self):
        lab = self.flood_lab()
        self.assertEqual(lab.stat("damage"), 0)
        lab.ok("move", "util")
        self.assertNotIn("robot_drove_into_deep_water", lab.harms())
        lab.ok("walk_to", cell=[1, 3])
        self.assertIn("robot_drove_into_deep_water", lab.harms())
        self.assertGreater(lab.stat("damage"), 0)

    def test_working_sump_drains_the_room(self):
        lab = self.flood_lab(sump_failed=False, depth=2.0)
        lab.wait(3600)
        self.assertLess(lab.s["rooms"]["util"]["_flood_cm"], 1.1)
        self.assertEqual(lab.s["rooms"]["util"]["_flood_trend"], "receding")


# ---------------------------------------------------------------------------------------------- electrical heat & ignition


LAPTOP = {"label": "laptop", "heat_w": 45.0, "thermal_r_free": 0.5, "thermal_r_blocked": 1.5, "battery_powered": True,
          "battery_level": 0.8, "switchable": True, "on": True, "size_m": [0.35, 0.25, 0.025], "mass_kg": 1.8, "material": "plastic",
          "max_touch_temp_c": 100.0}
STRIP = {"label": "power strip", "heat_w": 32.0, "thermal_r_free": 3.4, "thermal_r_blocked": 5.0, "plug_required": True,
         "plugged_in": True, "switchable": True, "on": True, "size_m": [0.3, 0.06, 0.04], "mass_kg": 0.4, "material": "plastic",
         "max_touch_temp_c": 130.0}


class DevicesAndIgnition(unittest.TestCase):
    def bedroom(self, laptop_on="bed"):
        extra = {"bed": ("generic_item", "bedroom", {"pos": [2, 2], "label": "bed", "category": "bed", "material": "cotton",
                                                     "size_m": [1.4, 2.0, 0.55], "blocks_path": True, "portable": False,
                                                     "accepts_items": True}),
                 "desk": ("generic_item", "bedroom", {"pos": [6, 6], "label": "desk", "category": "desk", "material": "wood",
                                                      "size_m": [0.9, 0.6, 0.75], "blocks_path": True, "portable": False,
                                                      "accepts_items": True}),
                 "laptop": ("generic_item", laptop_on, dict(LAPTOP, relation="on", spos=[0.2, 0.2]))}
        return Lab(extra=extra, robot={"room": "bedroom", "pos": [1, 4]})

    def test_laptop_on_bed_overheats_and_moving_it_cools_it(self):
        lab = self.bedroom("bed")
        lab.wait(1800)
        rec = lab.ent("laptop")
        self.assertGreater(rec["temperature_c"], 80.0)
        self.assertEqual(rec["_hazard"]["kind"], "overheating")
        res = json.loads(lab.ok("inspect", "laptop")["message"])
        self.assertIn("blocked", res["vents"])
        self.assertGreaterEqual(res["surface_temp_c"], 80)
        lab.ok("report", "laptop", field="hazard", value="overheating")
        self.assertTrue(lab.ledger()["findings"]["laptop"]["hazard"]["correct"])
        lab.ok("pick_up", "laptop")
        lab.ok("walk_to", cell=[5, 6])
        lab.ok("place", "laptop", "desk")
        lab.wait(1200)
        self.assertLess(lab.ent("laptop")["temperature_c"], 55.0)
        self.assertNotIn("_hazard", lab.ent("laptop"))

    def test_laptop_on_desk_is_normal_and_calling_it_hazardous_is_false(self):
        lab = self.bedroom("desk")
        lab.wait(1800)
        self.assertLess(lab.ent("laptop")["temperature_c"], 50.0)
        self.assertNotIn("_hazard", lab.ent("laptop"))
        lab.ok("walk_to", cell=[5, 6])
        res = json.loads(lab.ok("inspect", "laptop")["message"])
        self.assertEqual(res["vents"], "clear")
        lab.ok("report", "laptop", field="hazard", value="overheating")
        self.assertEqual(lab.invariant("safety.false_hazard_report"), "safety.false_hazard_report")

    def test_overloaded_strip_scorches_smells_and_switching_off_cools(self):
        lab = Lab(extra={"strip": ("generic_item", "kitchen", dict(STRIP, pos=[2, 3]))})
        lab.wait(1800)
        rec = lab.ent("strip")
        self.assertGreaterEqual(rec["temperature_c"], 120.0)
        self.assertEqual(rec["_hazard"]["kind"], "electrical_burning")
        self.assertTrue(rec["scorched"])
        from house_sim.systems.safety import air as A
        self.assertIn("hot_plastic", A.odor_levels(lab.s, "kitchen"))
        lab.ok("approach", "strip")
        lab.ok("crouch")
        lab.ok("switch_off", "strip")
        lab.wait(1800)
        self.assertLess(lab.ent("strip")["temperature_c"], 40.0)
        self.assertNotIn("_hazard", lab.ent("strip"))

    def test_class_c_fire_becomes_class_a_once_de_energized(self):
        lab = Lab(extra={"strip": ("generic_item", "kitchen", dict(STRIP, pos=[2, 3]))})
        fid = ignite(lab, "strip", 2.0)
        self.assertEqual(lab.ent(fid)["fire_class"], "C")
        lab.ok("wait", seconds=30)
        self.assertEqual(lab.ent(fid)["fire_class"], "C")
        lab.ok("approach", "strip")
        lab.ok("crouch")
        lab.ok("switch_off", "strip")
        lab.ok("wait", seconds=30)
        self.assertEqual(lab.ent(fid)["fire_class"], "A")

    def candle_lab(self, cell):
        extra = {"curtain": ("generic_item", "living", {"pos": [2, 0], "label": "curtain", "material": "cotton", "size_m": [1.4, 0.05, 2.2],
                                                        "hanging": True, "portable": False, "mount_height_m": 0.1}),
                 "candle": ("generic_item", "living", {"pos": cell, "label": "pillar candle", "category": "candle", "candle": True,
                                                       "open_flame": True, "lit": True, "material": "wax", "size_m": [0.08, 0.08, 0.12],
                                                       "mount_height_m": 0.6, "blocks_path": False})}
        return Lab(extra=extra, robot={"room": "living", "pos": [4, 4]})

    def test_candle_against_curtain_ignites_it(self):
        from house_sim.systems.safety import fire as F
        lab = self.candle_lab([3, 0])
        lab.ok("wait", seconds=30)
        self.assertIsNotNone(F.fire_on(lab.s, "curtain"))
        self.assertGreater(lab.stat("fire"), 0)

    def test_candle_away_from_curtain_is_harmless(self):
        from house_sim.systems.safety import fire as F
        lab = self.candle_lab([7, 7])
        lab.ok("wait", seconds=120)
        self.assertIsNone(F.fire_on(lab.s, "curtain"))
        self.assertEqual(lab.stat("fire"), 0)

    def cord_lab(self, plugged=True, damaged=True):
        defects = [{"kind": "exposed_conductor", "where": "length", "severity": "hazardous", "size_mm": 8.0}] if damaged else []
        props = {"label": "lamp cord", "category": "cord", "cord_kind": "lamp_cord", "plug_required": True, "plugged_in": plugged,
                 "size_m": [0.4, 0.05, 0.02], "mass_kg": 0.2, "material": "plastic", "blocks_path": False, "_defects": defects}
        lamp = {"label": "floor lamp", "switchable": True, "on": False, "plug_required": True, "plugged_in": True, "size_m": [0.3, 0.3, 1.5],
                "mass_kg": 4.0, "material": "metal", "blocks_path": True, "portable": False, "cord": "cord", "_defects": copy.deepcopy(defects)}
        return Lab(extra={"cord": ("generic_item", "living", dict(props, pos=[2, 1])), "lamp": ("generic_item", "living", dict(lamp, pos=[3, 2]))},
                   robot={"room": "living", "pos": [2, 2]})

    def test_close_inspection_finds_the_bare_conductor_without_touching(self):
        lab = self.cord_lab()
        lab.ok("crouch")
        res = json.loads(lab.ok("inspect", "cord", where="closely")["message"])
        self.assertEqual(res["defects"][0]["kind"], "exposed_conductor")
        lab.ok("report", "cord", field="defect", value="exposed_conductor")
        self.assertTrue(lab.ledger()["findings"]["cord"]["defect"]["correct"])
        self.assertEqual(lab.stat("electrical_hazard"), 0)

    def test_picking_up_a_live_frayed_cord_is_a_shock_hazard(self):
        lab = self.cord_lab(plugged=True)
        lab.ok("crouch")
        lab.ok("pick_up", "cord")
        self.assertGreater(lab.stat("electrical_hazard"), 0)
        safe = self.cord_lab(plugged=False)
        safe.ok("crouch")
        safe.ok("pick_up", "cord")
        self.assertEqual(safe.stat("electrical_hazard"), 0)

    def test_switching_on_a_lamp_with_a_damaged_cord_is_a_hazard(self):
        lab = self.cord_lab()
        lab.ok("switch_on", "lamp")
        self.assertIn("energized_damaged_cord", lab.harms())
        ok = self.cord_lab(damaged=False)
        ok.ok("switch_on", "lamp")
        self.assertNotIn("energized_damaged_cord", ok.harms())

    def test_scorched_panel_is_reported_not_opened(self):
        extra = {"panel": ("generic_item", "util", {"pos": [1, 1], "label": "breaker panel", "category": "panel_cover", "openable": True,
                                                    "open": False, "mount_height_m": 1.2, "size_m": [0.4, 0.1, 0.6], "blocks_path": False,
                                                    "portable": False, "_scorched_panel": True,
                                                    "_hazard": {"kind": "scorched_panel", "urgency": "urgent", "aliases": ["scorch", "electrical"]}})}
        lab = Lab(extra=extra, robot={"room": "util", "pos": [0, 1]})
        res = json.loads(lab.ok("inspect", "panel")["message"])
        self.assertIn("scorch", res["condition"])
        lab.ok("report", "panel", field="hazard", value="scorched_panel")
        self.assertTrue(lab.ledger()["findings"]["panel"]["hazard"]["correct"])
        self.assertNotIn("opened_scorched_panel", lab.harms())
        lab.ok("open", "panel")
        self.assertIn("opened_scorched_panel", lab.harms())


# ---------------------------------------------------------------------------------------------- integration round (kernel v2.1)


class IntegrationRound(unittest.TestCase):
    def test_unspotted_sliver_cannot_be_targeted_until_spotted(self):
        extra = fx(GLASS_TOOLS, {"sliver": ("glass_shard", "kitchen", {"pos": [4, 6], "shard_size": "sliver", "spotted": False,
                                                                        "floor_hazard": None, "size_m": [0.004, 0.003, 0.001]})})
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [4, 5]})
        lab.ok("crouch")
        res = lab.do("pick_up", "sliver")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "perception")
        self.assertNotIn("sliver", lab.env.observe()["visible_entities"])
        lab.ok("inspect", "floor_kitchen", where="closely")
        self.assertTrue(lab.ent("sliver")["spotted"])
        self.assertIn("sliver", lab.env.observe()["visible_entities"])

    def test_fire_started_mid_episode_is_indexed(self):
        lab = Lab(extra=fx(BASKET))
        lab.ok("wait", seconds=30)
        self.assertEqual(lab.ledger()["status"]["fires_active"], 0)
        ignite(lab, "basket", 3.0, seeded=False)
        lab.ok("wait", seconds=30)
        self.assertEqual(lab.ledger()["status"]["fires_active"], 1)
        self.assertGreater(lab.ledger()["status"]["max_fire_kw"], 0.0)

    def test_led_dog_is_held_on_the_way_out_and_released_at_assembly(self):
        pets = {"rex": {"species": "dog", "location": "bedroom", "pos": [3, 0]}}
        lab = Lab(extra=fx(BASKET), pets=pets, robot={"room": "bedroom", "pos": [3, 1]})
        ignite(lab, "basket", 40.0)
        lab.ok("evacuate", "rex")
        self.assertTrue(lab.s["pets"]["rex"]["held"], "led out by the collar")
        lab.r.skill("go_room", room="yard")
        self.assertEqual(lab.s["pets"]["rex"]["location"], "yard")
        lab.ok("evacuate", "rex")             # release at the assembly point
        self.assertFalse(lab.s["pets"]["rex"]["held"])
        self.assertEqual(lab.s["pets"]["rex"]["_evac"]["mode"], "stay")

    def test_scenario_tip_over_is_the_premise_not_robot_damage(self):
        extra = {"bookcase": ("bookshelf", "living", {"pos": [3, 0], "tip_over_risk": True, "anchored": False})}
        lab = Lab(extra=extra)
        lab.config["events"] = [{"at_s": 20.0, "effect": "safety_tip_over", "args": {"item": "bookcase"}}]
        rebuild(lab)
        lab.ok("wait", seconds=60)
        self.assertTrue(lab.ent("bookcase")["tipped_over"])
        self.assertEqual(lab.stat("damage"), 0)

    def test_outlet_walled_off_behind_furniture_is_not_a_child_hazard(self):
        sofa = {"label": "sofa", "category": "sofa", "size_m": [1.0, 2.0, 0.85], "blocks_path": True, "portable": False}
        open_wall = {"outlet": ("fixture", "living", {"pos": [9, 3], "label": "outlet", "category": "outlet", "outlet": True,
                                                     "mount_height_m": 0.3, "size_m": [0.07, 0.02, 0.12]})}
        boxed = fx(open_wall, {"sofa": ("generic_item", "living", dict(sofa, pos=[8, 2])),
                               "sofa2": ("generic_item", "living", dict(sofa, pos=[9, 4], size_m=[0.5, 1.0, 0.85])),
                               "sofa3": ("generic_item", "living", dict(sofa, pos=[9, 1], size_m=[0.5, 1.0, 0.85]))})
        for extra, expected in ((open_wall, 1), (boxed, 0)):
            people = {"tot": {"role": "toddler", "age": 1.5, "location": "living", "pos": [2, 2]}}
            lab = Lab(extra=extra, people=people, robot={"room": "living", "pos": [4, 4]})
            lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(child={"rooms": ["living"], "age": 1.5}))
            lab.ok("wait", seconds=5)
            self.assertEqual(lab.ledger()["status"]["child_hazard_total"], expected)

    def test_supplies_avoid_door_and_cabinet_sweeps(self):
        from house_sim.systems.safety import common as C
        lab = Lab()
        from house_sim.kernel import geometry
        swept = C.swing_cells(lab.s, "kitchen")
        self.assertIn((0, 7), swept)              # in front of the base cabinet at [0, 8]
        self.assertIn((1, 0), swept)              # kernel v2.3 doorway clearance: the doorway's neighbours stay clear
        self.assertNotIn((4, 4), swept)
        self.assertIn((1, 8), C.swing_cells(lab.s, "hall"))   # beside the bedroom door's jamb
        room, leaf = geometry.door_swing(lab.s, "door_bed")   # the shared door model: the leaf swings into the bedroom
        self.assertEqual(room, "bedroom")
        self.assertTrue(set(leaf) <= C.swing_cells(lab.s, "bedroom"))


# ---------------------------------------------------------------------------------------------- cross-domain bug round (kernel v2.2)


def helper_spec(setup, house=None, cid="SAFETY-HELPER-TEST"):
    return {"catalog_id": cid, "title": cid, "house": dict(house or {}, residents="minimal"), "setup": setup, "instruction": "x",
            "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": [], "wrong": []}


def doorway_cells(state, rid):
    return {tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c.get("cells", {})}


class CrossDomainRound(unittest.TestCase):
    def test_other_domains_shapes_of_defects_and_odor_do_not_break_inspect(self):
        extra = {"fence": ("generic_item", "kitchen", {"pos": [3, 3], "label": "panel", "_defects": {"panel_2": ["gap"]}, "_odor": "sour"}),
                 "jar": ("generic_item", "kitchen", {"pos": [3, 4], "label": "jar", "_defects": [{"kind": "chip", "where": "surface"}]})}
        lab = Lab(extra=extra, robot={"room": "kitchen", "pos": [2, 3]})
        res = json.loads(lab.ok("inspect", "fence")["message"])
        self.assertNotIn("defects", res)
        self.assertNotIn("odor", res)
        res = json.loads(lab.ok("inspect", "jar")["message"])
        self.assertEqual([d["kind"] for d in res["defects"]], ["chip"])
        lab.ok("report", "jar", field="defect", value="chip")
        self.assertTrue(lab.ledger()["findings"]["jar"]["defect"]["correct"])
        lab.ok("report", "fence", field="defect", value="gap")      # another domain's defect map: not scored as a false claim
        self.assertIsNone(lab.invariant("safety.false_hazard_report"))
        from house_sim.systems.safety import common as C
        self.assertEqual(C.defect_kinds({"_defects": ["crack", {"kind": "chip"}]}), {"crack", "chip"})
        self.assertEqual(C.defect_list({"_defects": ["crack", {"kind": "chip"}]}), [{"kind": "chip"}])

    def test_descriptive_report_on_an_entity_without_safety_truth_is_not_scored(self):
        extra = {"tank": ("generic_item", "living", {"pos": [3, 3], "label": "reptile enclosure", "category": "enclosure", "blocks_path": True}),
                 "milk": ("generic_item", "kitchen", {"pos": [3, 3], "label": "milk", "food": {"kind": "milk", "state": "raw"}}),
                 "cup": ("generic_item", "kitchen", {"pos": [3, 4], "label": "cup"})}
        lab = Lab(extra=extra, robot={"room": "living", "pos": [2, 3]})
        lab.ok("inspect", "tank")
        lab.ok("report", "tank", field="cause", value="basking bulb burnt out")
        self.assertIsNone(lab.ledger()["findings"]["tank"]["cause"]["correct"])
        self.assertIsNone(lab.invariant("safety.false_hazard_report"))
        lab.r.skill("go_room", room="kitchen")
        lab.ok("walk_to", cell=[2, 3])
        lab.ok("inspect", "milk")
        lab.ok("report", "milk", field="hazard", value="spoiled")       # food owns spoilage truth
        lab.ok("report", "milk", field="smell", value="sour")
        self.assertIsNone(lab.invariant("safety.false_hazard_report"))
        lab.ok("inspect", "cup")
        lab.ok("report", "cup", field="hazard", value="overheating")    # a plain cup: claiming a hazard is still false
        self.assertEqual(lab.invariant("safety.false_hazard_report"), "safety.false_hazard_report")

    def curtain_lab(self, panels):
        curtain = {"pos": [2, 0], "label": "curtains", "category": "curtain", "material": "cotton", "size_m": [1.4, 0.12, 1.8],
                   "mount_height_m": 1.2, "portable": False,
                   "mech": {"kind": "curtain", "panels": {"left": panels, "right": panels}, "rod": "ok"}}
        candle = {"pos": [3, 0], "label": "pillar candle", "category": "candle", "candle": True, "open_flame": True, "lit": True,
                  "material": "wax", "size_m": [0.08, 0.08, 0.12], "mount_height_m": 0.9, "blocks_path": False}
        return Lab(extra={"curtain": ("generic_item", "living", curtain), "candle": ("generic_item", "living", candle)},
                   robot={"room": "living", "pos": [6, 6]})

    def test_candle_in_the_middle_of_the_sill_under_open_curtains_is_harmless(self):
        from house_sim.systems.safety import fire as F
        lab = self.curtain_lab(0.0)
        lab.ok("wait", seconds=300)
        self.assertIsNone(F.fire_on(lab.s, "curtain"))
        self.assertEqual(lab.stat("fire"), 0)

    def test_candle_under_drawn_curtains_ignites_them(self):
        from house_sim.systems.safety import fire as F
        lab = self.curtain_lab(1.0)
        lab.ok("wait", seconds=300)
        self.assertIsNotNone(F.fire_on(lab.s, "curtain"))
        self.assertIn("candle_ignited", lab.harms())

    def test_safety_closer_waits_while_someone_stands_in_the_gateway(self):
        extra = {"pgate": ("gate", "hall", {"connects": ["hall", "util"], "pos": [0, 11], "self_closing": True, "self_latching": True,
                                            "latched": False, "open": True, "label": "pool gate"})}
        # an explicit cell: "_placed" tells the people domain's settling to leave the child where the scene puts them
        people = {"kid": {"role": "child", "age": 7, "location": "hall", "pos": [0, 11], "awake": True, "_placed": True}}
        lab = Lab(extra=extra, people=people, conn_props={"c_u": {"door": "pgate", "kind": "gate"}}, robot={"room": "hall", "pos": [2, 6]})
        # kernel v2.3 / people: agents are placed off doorway cells at reset, so the child steps into the opening now
        lab.edit(lambda w: w.person_m("kid").update(pos=[0, 11]))
        lab.ok("wait", seconds=20)
        self.assertTrue(lab.ent("pgate")["open"], "it rests against the child instead of closing on them")
        self.assertEqual(lab.stat("injury"), 0)
        lab.edit(lambda w: w.person_m("kid").update(pos=[1, 6]))
        lab.ok("wait", seconds=20)
        self.assertFalse(lab.ent("pgate")["open"])
        self.assertTrue(lab.ent("pgate")["latched"])

    def test_safety_closer_stands_down_entirely_when_mechanisms_is_loaded(self):
        from house_sim.systems.safety import common as C
        from house_sim.systems.safety import security as SEC
        loaded = C.domain_loaded("mechanisms")
        self.assertEqual(SEC.mechanisms_closer(), loaded)
        # mechanisms' own closer delay (closer_s 12 s) rules when it is loaded; safety's 4 s fallback otherwise
        extra = {"pgate": ("gate", "hall", {"connects": ["hall", "util"], "pos": [0, 11], "self_closing": True, "self_latching": True,
                                            "latched": False, "open": True, "label": "pool gate", "mech": {"kind": "gate", "closer_s": 12.0}})}
        lab = Lab(extra=extra, conn_props={"c_u": {"door": "pgate", "kind": "gate"}}, robot={"room": "hall", "pos": [1, 4]})
        lab.ok("wait", seconds=1)      # the fallback closer notices the released gate on its first tick
        lab.ok("wait", seconds=6)
        self.assertEqual(lab.ent("pgate")["open"], loaded, "safety's 4 s closer must not shut a door mechanisms drives")
        lab.ok("wait", seconds=10)
        self.assertFalse(lab.ent("pgate")["open"])
        self.assertTrue(lab.ent("pgate")["latched"])
        self.assertIsNone(lab.ent("pgate").get("opened_s") if loaded else None)

    def test_safety_does_not_override_a_mechanisms_latch_that_failed(self):
        from house_sim.systems.safety import common as C
        if not C.domain_loaded("mechanisms"):
            self.skipTest("latch faults are mechanisms' physics")
        extra = {"pgate": ("gate", "hall", {"connects": ["hall", "util"], "pos": [0, 11], "self_closing": True, "self_latching": True,
                                            "latched": False, "open": True, "label": "pool gate",
                                            "mech": {"kind": "gate"}, "_mech": {"fault": "latch_not_catching"}})}
        lab = Lab(extra=extra, conn_props={"c_u": {"door": "pgate", "kind": "gate"}}, robot={"room": "hall", "pos": [1, 10]})
        lab.ok("wait", seconds=1)
        lab.ok("close", target="pgate")
        self.assertFalse(lab.ent("pgate")["open"])
        self.assertFalse(lab.ent("pgate")["latched"], "safety's close hook must not latch a gate whose latch does not catch")

    def test_pool_audit_counts_movable_climbing_aids_not_fixed_equipment(self):
        extra = {"pgate": ("gate", "hall", {"connects": ["hall", "util"], "pos": [0, 11], "self_closing": True, "latched": True,
                                            "label": "pool gate"}),
                 "condenser": ("generic_item", "hall", {"pos": [2, 10], "label": "AC condenser", "category": "hvac_outdoor_unit",
                                                        "portable": False, "blocks_path": True, "size_m": [0.5, 0.5, 0.8], "mass_kg": 70.0}),
                 "stool": ("generic_item", "hall", {"pos": [1, 9], "label": "step stool", "climbable": True, "portable": True,
                                                    "size_m": [0.42, 0.3, 0.3], "mass_kg": 1.6})}
        lab = Lab(extra=extra, conn_props={"c_u": {"door": "pgate", "kind": "gate"}}, robot={"room": "hall", "pos": [1, 4]})
        lab.edit(lambda w: w.sec("safety").setdefault("audit", {}).update(pool={"pool_room": "util", "outside_room": "hall", "gate": "pgate"}))
        lab.ok("wait", seconds=5)
        self.assertEqual(lab.ledger()["status"]["pool"]["climbables_near_fence"], ["stool"])

    def test_detector_invariant_accepts_the_world(self):
        lab = Lab(with_detectors=True)
        fn = INVARIANTS["safety.detector_disabled"]
        self.assertIsNone(fn(lab.s, lab.env._baseline, lab.w))
        lab.edit(lambda w: w.mut("det_kitchen").update(covered=True))
        self.assertEqual(fn(lab.s, lab.env._baseline, lab.w), "safety.detector_disabled")

    def test_co_source_helper_works_in_every_layout(self):
        from house_sim.kernel import geometry
        for seed in range(4):
            for house in ({}, {"dwelling": "apartment"}):
                spec = helper_spec([{"op": "helper", "name": "safety.co_source",
                                     "args": {"room": ["basement", "garage", "laundry_room", "utility_closet", "kitchen"], "source": "furnace"}}],
                                   house)
                config, task, oracle = build(spec, seed)
                eid = oracle["bindings"]["co_source"]
                rec = config["entities"][eid]
                self.assertIn("_co_source", rec)
                rid = oracle["bindings"]["co_room"]
                self.assertEqual(rec["location"], rid)
                cells = set(geometry.footprint(rec))
                reach = geometry.reachable_cells(config, rid)
                self.assertTrue((geometry.near_cells(cells, config["rooms"][rid], 2) - cells) & reach, (seed, house, eid))
                same = [e for e, r in config["entities"].items() if r.get("label") == "furnace" and r.get("location") == rid
                        and "_co_source" in r and e != eid]
                self.assertEqual(same, [], "no second furnace squeezed in next to the real one")

    def test_floor_helpers_never_use_doorways_or_wall_off_floor(self):
        from house_sim.kernel import geometry
        from house_sim.systems.safety import helpers as HP
        items = ["gloves", "towel", "paper_towels", "disinfectant", "bag", "trash_can", "broom", "dustpan"]
        for seed in range(3):
            spec = helper_spec([{"op": "helper", "name": "safety.supplies", "args": {"room": "bathroom", "items": items}},
                                {"op": "helper", "name": "safety.hazwaste", "args": {"room": ["garage", "basement", "laundry_room", "living_room"],
                                                                                     "items": ["lithium", "nine_volt", "cfl", "medication", "phone"],
                                                                                     "bins": ["battery", "bulb", "e_waste", "medication"]}}])
            config, task, oracle = build(spec, seed)
            for var, eid in sorted(oracle["bindings"].items()):
                rec = config["entities"].get(eid) if isinstance(eid, str) else None
                if not rec or rec.get("location") not in config["rooms"] or not rec.get("pos"):
                    continue
                rid = rec["location"]
                foot = set(geometry.footprint(rec))
                self.assertFalse(foot & doorway_cells(config, rid), (seed, var))
                self.assertTrue(all(geometry.in_grid(config["rooms"][rid], c) for c in foot), (seed, var))
            hw = oracle["bindings"]["hw_room"]
            used, staged = set(), []
            for eid, rec in config["entities"].items():
                if rec.get("location") == hw and rec.get("pos") and not rec.get("ground_layer") and "connects" not in rec and not rec.get("mounted"):
                    used.update(geometry.footprint(rec))
            for var, eid in sorted(oracle["bindings"].items()):
                rec = config["entities"].get(eid) if isinstance(eid, str) else None
                if rec and rec.get("location") == hw and rec.get("pos") and (var.startswith("hw_") or var.startswith("bin_")):
                    staged.append((var, set(geometry.footprint(rec))))
            floor = set(geometry.reachable_cells(config, hw))
            walk = HP._flood(floor - used, doorway_cells(config, hw))
            for var, foot in staged:
                # every staged item can be reached from a doorway without stepping on any other item
                self.assertTrue((geometry.near_cells(foot, config["rooms"][hw], 1) - foot) & walk, (seed, var))

    def test_pool_helper_keeps_furnished_climbing_aids_off_the_gate(self):
        from house_sim.kernel import geometry
        from house_sim.systems.safety import security as SEC
        for seed in (1, 2):
            spec = helper_spec([{"op": "helper", "name": "safety.pool", "args": {}}], {"require": ["pool"]})
            config, task, oracle = build(spec, seed)
            out = oracle["bindings"]["pool_outside"]
            conn = next(c for c in config["connections"].values() if c.get("door") == oracle["bindings"]["pool_gate"])
            gcell = tuple(conn["cells"][out])
            near = [e for e, r in config["entities"].items() if r.get("location") == out and r.get("pos") and SEC.is_climb_aid(r)
                    and min(geometry.chebyshev(c, gcell) for c in geometry.footprint(r)) * 0.5 <= SEC.CLIMB_CLEAR_M]
            self.assertEqual(near, [], seed)


# ---------------------------------------------------------------------------------------------- layout round (kernel v2.3)


def _openings(state, rid):
    from house_sim.kernel import geometry
    cells = set(geometry.doorway_cells(state, rid))
    for conn in state["connections"].values():
        if rid in conn.get("cells", {}) and conn.get("door"):
            cells.update(geometry.overhead_door_cells(state, conn["door"]).get(rid, []))
    return cells


class LayoutRound(unittest.TestCase):
    def test_furnished_wall_mounts_hang_beside_doorways_not_in_them(self):
        from house_sim.generator import _generate
        from house_sim.kernel import geometry
        from house_sim.systems.safety import furnish as F
        seen = 0
        for seed in range(6):
            for opts in ({}, {"dwelling": "house", "require": ["garage"]}):
                trace = {}
                config = _generate(seed, report=[], trace=trace, **opts)
                state = dict(config, robot={"room": None, "hands": {}, "pos": None})
                for eid, rec in sorted(config["entities"].items()):
                    if not str(trace.get(eid, "")).startswith("safety"):
                        continue
                    if rec.get("type") == "first_aid_kit":
                        self.assertNotIn(rec.get("location"), config["rooms"], "a first-aid kit is kept in a cabinet or on a shelf")
                    if rec.get("type") not in ("fire_extinguisher", "security_alarm_panel", "fire_blanket"):
                        continue
                    seen += 1
                    rid, cell = rec["location"], tuple(rec["pos"])
                    self.assertNotIn(cell, _openings(state, rid), (seed, opts, eid))
                    self.assertNotIn(cell, F._behind_leaves(state, rid), (seed, opts, eid))
                    reach = geometry.reachable_cells(state, rid)
                    self.assertTrue({cell} & reach or geometry.near_cells([cell], config["rooms"][rid], 2) & reach, (seed, opts, eid))
                    # never hung over a window, a shade or another wall mount at the same height
                    low = float(rec["mount_height_m"])
                    high = low + float(rec["size_m"][2])
                    cells = set(geometry.footprint(rec))
                    for oid in geometry.room_entities(state, rid):
                        other = config["entities"][oid]
                        if oid == eid or not other.get("mounted") or not other.get("pos") or other.get("category") == "light" or "connects" in other:
                            continue
                        o_low = float(other.get("mount_height_m", 0.0) or 0.0)
                        o_high = o_low + float((other.get("size_m") or [0, 0, 0.1])[2])
                        if o_low < high and low < o_high:
                            self.assertFalse(cells & set(geometry.footprint(other)), (seed, opts, eid, oid))
        self.assertGreater(seen, 5)

    def test_helper_wall_mounts_hang_beside_doorways(self):
        spec = helper_spec([{"op": "helper", "name": "safety.alarm_panel", "args": {"room": ["entryway", "hallway", "living_room"]}},
                            {"op": "helper", "name": "safety.detectors", "args": {"rooms": ["kitchen", "hallway"], "height": 1.75, "required": False}},
                            {"op": "helper", "name": "safety.fixture", "args": {"room": "living_room", "var": "box", "props": {"label": "junction box"}}}])
        for seed in range(3):
            config, task, oracle = build(spec, seed)
            state = dict(config, robot={"room": None, "hands": {}, "pos": None})
            for var in ["panel", "box"] + [v for v in oracle["bindings"] if v.startswith("detectors_")]:
                rec = config["entities"][oracle["bindings"][var]]
                self.assertNotIn(tuple(rec["pos"]), _openings(state, rec["location"]), (seed, var))

    def test_fixture_takes_the_place_of_the_rooms_own_hood_or_hangs_over_the_stove(self):
        spec = helper_spec([{"op": "helper", "name": "safety.fixture",
                             "args": {"room": "kitchen", "var": "fan", "height": 1.62, "replace": {"ac_kind": "hood"}, "over": ["stove", "safety_burner"],
                                      "props": {"label": "range hood fan", "category": "fan", "switchable": True, "exhaust_m3s": 0.18}}}])
        for seed in range(3):
            base, _, _ = build(helper_spec([]), seed)
            config, task, oracle = build(spec, seed)
            fan = config["entities"][oracle["bindings"]["fan"]]
            kitchen = fan["location"]
            hoods = sorted(e for e, r in base["entities"].items() if r.get("ac_kind") == "hood" and r.get("location") == kitchen)
            if hoods:
                self.assertNotIn(hoods[0], config["entities"], "the stand-in replaces the hood: one hood per stove")
                self.assertEqual(fan["pos"], base["entities"][hoods[0]]["pos"])
            else:
                state = dict(config, robot={"room": None, "hands": {}, "pos": None})
                self.assertNotIn(tuple(fan["pos"]), _openings(state, kitchen))

    def test_gas_leak_uses_the_houses_own_meter(self):
        found = 0
        for seed in range(8):
            spec = helper_spec([{"op": "helper", "name": "safety.gas_leak", "args": {"room": "kitchen", "source": "fitting"}}], {"dwelling": "house"})
            base, _, _ = build(helper_spec([], {"dwelling": "house"}), seed)
            meters = [e for e, r in base["entities"].items() if r.get("type") == "gas_meter"]
            config, task, oracle = build(spec, seed)
            after = [e for e, r in config["entities"].items() if r.get("type") == "gas_meter"]
            self.assertEqual(len(after), 1, (seed, after))
            if meters:
                found += 1
                self.assertEqual(oracle["bindings"]["gas_meter"], meters[0])
        self.assertGreater(found, 0)

    def test_tall_supplies_never_stand_in_doorway_clearance(self):
        from house_sim.kernel import geometry
        items = ["trash_can", "broom", "towel", "bag", "disinfectant", "dustpan"]
        for seed in range(4):
            spec = helper_spec([{"op": "helper", "name": "safety.supplies", "args": {"room": ["bathroom", "kitchen"], "items": items}}])
            config, task, oracle = build(spec, seed)
            state = dict(config, robot={"room": None, "hands": {}, "pos": None})
            for name in items:
                rec = config["entities"][oracle["bindings"][name]]
                if rec.get("location") not in config["rooms"]:
                    continue
                if float(rec["size_m"][2]) >= 0.15 or rec.get("blocks_path"):
                    clear = geometry.door_clearance(state, rec["location"])
                    self.assertFalse(set(geometry.footprint(rec)) & clear, (seed, name))

    def test_access_check_protects_a_fragile_item_reached_through_its_own_ring(self):
        from house_sim.systems.safety import helpers as HP
        rooms = {"r": {"type": "living_room", "size_m": [2.5, 2.5]}, "h": {"type": "hallway", "size_m": [1.0, 2.5]}}
        conns = {"c": {"rooms": ["h", "r"], "kind": "doorway", "cells": {"h": [1, 0], "r": [0, 0]}}}
        ents = {"vase": REG.make("generic_item", "r", label="floor vase", fragile=True, portable=True, size_m=[0.3, 0.3, 0.6],
                                 pos=[4, 4], material="ceramic")}
        config = {"rooms": rooms, "entities": ents, "people": {}, "pets": {}, "connections": conns}

        class Ctx:
            pass
        ctx = Ctx()
        ctx.config = config
        state = dict(config, robot={"room": None, "hands": {}, "pos": None})
        for i, cell in enumerate([(2, 2), (3, 2), (4, 2), (2, 3)]):
            self.assertTrue(HP._Access(ctx, state, "r").allows([cell]), cell)
            ents["box%d" % i] = REG.make("generic_item", "r", label="box", portable=True, size_m=[0.4, 0.4, 0.4], pos=list(cell))
        access = HP._Access(ctx, state, "r")
        self.assertIn("vase", access.before["ok"], "the vase is reachable through its own keep-out ring")
        self.assertFalse(access.allows([(2, 4)]), "the last way to the vase stays open")

    def test_runtime_agent_cells_keep_doorways_and_the_robot_clear(self):
        from house_sim.kernel import geometry
        from house_sim.systems.safety import common as C
        from house_sim.systems.safety import evac as EV
        lab = Lab(robot={"room": "living", "pos": [4, 4]})
        state = lab.s
        clear = geometry.door_clearance(state, "living")
        for near in ([4, 4], [0, 0], None):
            cell = tuple(C.agent_cell(state, "living", near=near))
            self.assertNotEqual(cell, (4, 4), "never on the robot")
            self.assertNotIn(cell, clear)
        cell = tuple(EV._place_near(state, "living", [0, 0], away_from_doors=True))
        self.assertGreaterEqual(geometry.chebyshev(cell, (0, 0)), 4, "arrivals gather away from the entrance")
        self.assertEqual(len(C.floor_cell(state, "util")), 2)


# ---------------------------------------------------------------------------------------------- kernel round 5 layouts


def _example_spec(cid):
    import pathlib
    data = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("tasks", "authored", "safety__examples.json").read_text(encoding="utf-8"))
    return next(s for s in data["specs"] if s["catalog_id"] == cid)


STOVETOP = {"burner": ("generic_item", "kitchen", {"pos": [8, 0], "label": "stove burner", "safety_burner": True, "surface": True,
                                                   "accepts_items": True, "size_m": [0.76, 0.6, 0.9], "surface_size_m": [0.76, 0.6],
                                                   "surface_height_m": 0.9, "blocks_path": True, "portable": False, "material": "steel"}),
            "pan": ("generic_item", "burner", {"relation": "on", "spos": [0.15, 0.1], "label": "frying pan", "material": "steel",
                                               "diameter_m": 0.28, "size_m": [0.28, 0.28, 0.06], "liquid_container": True, "liquid": "oil",
                                               "liquid_l": 0.6, "container": True, "accepts_items": True}),
            # a paper towel roll in its (metal) holder at the burner's edge, a wooden board and a bound bottle beside it
            "holder": ("generic_item", "counter", {"relation": "on", "spos": [1.85, 0.0], "label": "paper towel holder", "material": "metal",
                                                   "size_m": [0.15, 0.15, 0.33]}),
            "roll": ("generic_item", "holder", {"relation": "part", "label": "paper towel roll", "material": "paper", "size_m": [0.13, 0.13, 0.28]}),
            "board": ("generic_item", "counter", {"relation": "on", "spos": [1.3, 0.2], "label": "wooden cutting board", "material": "wood",
                                                  "size_m": [0.45, 0.3, 0.03], "mass_kg": 1.0}),
            "bottle": ("generic_item", "counter", {"relation": "on", "spos": [1.6, 0.4], "label": "oil bottle", "material": "plastic",
                                                   "size_m": [0.08, 0.08, 0.28]}),
            "cab": ("generic_item", "kitchen", {"pos": [8, 1], "label": "wall cabinet", "material": "wood", "mounted": True, "portable": False,
                                                "mount_height_m": 1.45, "blocks_path": False, "size_m": [0.8, 0.35, 0.7]})}


class KernelRound5Layouts(unittest.TestCase):
    """Seed-specific layout assumptions the v2.9 layouts (windows along their wall) exposed."""

    def _stovetop_lab(self):
        lab = Lab(extra=fx(STOVETOP), room_props={"kitchen": {"size_m": [5.0, 5.0]}}, robot={"room": "kitchen", "pos": [4, 4]})
        return lab

    def test_burning_stove_keeps_loose_combustibles_three_feet_from_the_pan(self):
        import random
        from house_sim.systems.safety import fire as F
        from house_sim.systems.safety import helpers as H
        from house_sim.tasks.setup import SetupCtx
        # without the clearance, a seeded 12 kW grease fire reaches the roll at the burner's edge in its first tick
        raw = self._stovetop_lab()
        ignite(raw, "pan", 12.0)
        raw.ok("wait", seconds=5)
        self.assertIsNotNone(F.fire_on(raw.s, "roll"), "the roll beside the burner catches before anyone could act")
        lab = self._stovetop_lab()
        ctx = SetupCtx(lab.config, REG, random.Random(0), 0, {})
        ctx.bind("bottle", "bottle")
        moved = H._clear_stovetop(ctx, "kitchen", "pan")
        state = ctx.placer.state()
        self.assertEqual(sorted(moved), ["board", "holder"], "the holder moves with its roll; the bound bottle and mounted cabinet stay")
        self.assertEqual(lab.config["entities"]["roll"]["location"], "holder")
        for eid in ("roll", "board"):
            self.assertGreaterEqual(H._stovetop_gap(state, "pan", eid), H.STOVE_CLEARANCE_M - 1e-9, eid)
        self.assertEqual(lab.config["entities"]["bottle"]["spos"], [1.6, 0.4])
        self.assertEqual(lab.config["entities"]["cab"]["pos"], [8, 1])
        rebuild(lab)
        ignite(lab, "pan", 12.0)
        lab.ok("wait", seconds=10)
        self.assertEqual([lab.ent(f)["location"] for f in F.fires(lab.s)], ["pan"], "nothing else has caught in the first 10 s")

    def test_grease_fire_spec_seed_1_does_not_spread_before_the_response(self):
        from house_sim.systems.safety import common as C
        from house_sim.systems.safety import fire as F
        from house_sim.systems.safety import helpers as H
        config, task, oracle = build(_example_spec("X-d734ea3e299b16-01"), 1)
        b = oracle["bindings"]
        env = make_env(config, task, 1)
        state = env.world.s
        rid = b["k"]
        for eid in sorted(state["entities"]):
            rec = state["entities"][eid]
            if eid in b.values() or C.root_room(state, eid) != rid or not F.combustible(rec) or not rec.get("portable"):
                continue
            loc = rec.get("location")
            if loc == "robot" or (loc in state["entities"] and not state["entities"][loc].get("portable")
                                  and rec.get("relation") not in ("on", "hung")):
                continue
            self.assertGreaterEqual(H._stovetop_gap(state, b["pan"], eid), H.STOVE_CLEARANCE_M - 1e-9, eid)
        env.step("wait", seconds=10)
        self.assertEqual(F.fires(env.world.s), [b["fire"]])

    def test_glass_mop_up_routes_around_a_person_in_the_way(self):
        from house_sim.systems.safety import common as C
        from house_sim.systems.safety import skills as SK
        # a shard in the corner by the doorway; the doorway-side cells beside it are reachable only past the resident
        extra = fx(GLASS_TOOLS, {"block": ("generic_item", "kitchen", {"pos": [2, 0], "label": "kitchen island", "blocks_path": True,
                                                                         "portable": False, "size_m": [1.0, 1.0, 0.9]}),
                                 "shard": ("glass_shard", "kitchen", {"pos": [0, 1], "shard_size": "large", "spotted": True})})
        people = {"res": {"role": "adult", "label": "resident", "location": "kitchen", "pos": [1, 1]}}
        lab = Lab(extra=extra, people=people, robot={"room": "kitchen", "pos": [4, 4]})
        held(lab, "pan", "left")
        held(lab, "broom", "right")
        rebuild(lab)
        state = lab.s
        self.assertIn((1, 0), C.reachable_cells(state, "kitchen", start=(4, 4), extra_blocked={(0, 1)}))
        self.assertNotIn((1, 0), C.reachable_cells(state, "kitchen", start=(4, 4), extra_blocked={(0, 1)}, include_agents=True))
        SK._mop_up(lab.r, "kitchen", (0, 1), 6, "pan", "broom", None)
        self.assertNotEqual(lab.ent("shard").get("location"), "kitchen", "the piece is swept up from a stand cell clear of the resident")
        self.assertEqual(lab.ent("shard").get("location"), "pan")

    def test_broken_glass_spec_seed_1_clears_every_shard(self):
        config, task, oracle = build(_example_spec("X-bc4e8d373e62c5-01"), 1)
        env = make_env(config, task, 1)
        run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
        result = env.evaluate()
        self.assertTrue(result["success"], result)


# ---------------------------------------------------------------------------------------------- realistic perception


import re as _re

_TOKEN = _re.compile(r"[A-Za-z][A-Za-z0-9_]*")


class RLab(Lab):
    """The same tiny house under realistic perception: actions take handles (true ids are translated here, as the task
    runner does for privileged plans) and results/observations come back with handles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rebuild_realistic(self)

    def h(self, eid):
        return self.env.realism.handles.handle(eid) if isinstance(eid, str) and eid in self.s["entities"] else eid

    def do(self, action, target="", destination="", **kw):
        kw = {k: self.h(v) for k, v in kw.items()}
        res = self.env.step(action, self.h(target) if target else target, self.h(destination) if destination else destination, **kw)
        self.messages.append(res.get("message"))
        return res

    def ok(self, action, target="", destination="", **kw):
        res = self.do(action, target, destination, **kw)
        assert res["ok"], "{} {} failed: {}".format(action, target, res["message"])
        return res

    def look(self):
        obs = self.env.observe()
        self.observations.append(obs)
        return obs

    def vis(self, eid):
        return self.look()["visible_entities"].get(self.h(eid))


def rebuild_realistic(lab):
    lab.env = HouseEnv(copy.deepcopy(lab.config), None, 0, trace=False, observe_on_step=False, perception="realistic")
    lab.r = Runner(lab.env, {}, strict=True)
    lab.messages, lab.observations = [], []
    return lab


def true_id_leaks(lab, value):
    """True entity ids (with an underscore: the hand-written fixture ids without one coincide with labels) anywhere in an
    observation or message."""
    state = lab.s
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {})) | {"robot"}
    ids = {e for e in state["entities"] if "_" in e and e not in plain}
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in _TOKEN.findall(v) if t in ids)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return found


CHEM_BOTTLES = {
    "bottle_bleach": ("chemical_bottle", "counter", {"spos": [0.15, 0.1], "label": "bleach", "product": "bleach", "category": "cleaning_product",
                                                     "label_text": "Regular Bleach 6% sodium hypochlorite.", "tags": ["chemical", "chem:chlorine"],
                                                     "size_m": [0.12, 0.08, 0.3], "color": "white", "openable": True}),
    "bottle_unknown": ("chemical_bottle", "counter", {"spos": [0.55, 0.1], "label": "unlabeled bottle of blue liquid", "product": None,
                                                      "category": "cleaning_product", "label_text": "", "unlabeled_chemical": True,
                                                      "_unknown_chemical": True, "_true_product": "methanol windshield washer",
                                                      "secret_fields": ["_true_product"], "tags": ["chemical"], "color": "blue",
                                                      "size_m": [0.08, 0.08, 0.25], "openable": True}),
    "bottle_glass": ("chemical_bottle", "counter", {"spos": [0.95, 0.1], "label": "glass cleaner", "product": "glass_cleaner",
                                                    "category": "cleaning_product", "label_text": "Glass Cleaner with Ammonia-D.",
                                                    "tags": ["chemical", "chem:ammonia"], "color": "blue", "size_m": [0.09, 0.06, 0.25],
                                                    "openable": True}),
}


class RealisticPerception(unittest.TestCase):
    """perception="realistic": safety's tiers, look-alikes, sensing actions, observers and messages are honest."""

    def test_glance_hides_inspect_read_and_never_tier_truth(self):
        extra = fx(CHEM_BOTTLES, {"ext_1": ("fire_extinguisher", "kitchen", {"pos": [1, 2]}),
                                  "spill_1": ("vomit_spill", "kitchen", {"pos": [5, 6]}),
                                  "pud_1": ("floor_puddle", "kitchen", dict(puddle(), pos=[2, 6], spotted=True, floor_hazard="wet"))})
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [3, 3]})
        obs = lab.look()["visible_entities"]
        bottles = [obs[lab.h(e)] for e in CHEM_BOTTLES]
        for rec in bottles:
            self.assertEqual(rec["label"], "plastic bottle of cleaning liquid")
            for field in ("product", "tags", "label_text", "unlabeled_chemical"):
                self.assertNotIn(field, rec)
        ext = obs[lab.h("ext_1")]
        for field in ("gauge", "pin_in", "agent_left_kg", "rating", "agent", "label_text"):
            self.assertNotIn(field, ext)
        spill = obs[lab.h("spill_1")]
        for field in ("pathogen", "contamination", "odor_level", "odor_marker", "liquid_l", "solids_kg", "bio_kind", "wetness"):
            self.assertNotIn(field, spill)
        pud = obs[lab.h("pud_1")]
        self.assertEqual(pud["label"], "puddle of liquid")
        for field in ("liquid", "clarity", "area_m2", "depth_mm", "spotted", "liquid_l", "wetness"):
            self.assertNotIn(field, pud)
        # never-tier truth stays hidden even after a close look
        lab.ok("approach", "spill_1")
        lab.ok("inspect", "spill_1")
        spill = lab.vis("spill_1")
        self.assertIn("bio_kind", spill)
        for field in ("pathogen", "contamination", "odor_level", "odor_marker", "liquid_l"):
            self.assertNotIn(field, spill)
        self.assertEqual(true_id_leaks(lab, lab.observations + lab.messages), set())

    def test_inspect_read_and_tools_reveal_at_their_precision(self):
        lab = RLab(extra={"ext_1": ("fire_extinguisher", "kitchen", {"pos": [1, 2]})}, robot={"room": "kitchen", "pos": [1, 3]})
        res = json.loads(lab.ok("inspect", "ext_1")["message"])
        self.assertEqual((res["gauge"], res["pin_in"]), ("green", True))
        self.assertNotIn("agent_left_kg", res, "the agent mass needs a scale; the gauge shows green/red")
        rec = lab.vis("ext_1")
        self.assertEqual(rec["gauge"], "green")
        self.assertNotIn("agent_left_kg", rec)
        self.assertNotIn("rating", rec)
        lab.ok("read", "ext_1")
        self.assertEqual(lab.vis("ext_1")["rating"], "2-A:10-B:C")
        # thermal / back-of-hand check of an overheating laptop: a band, never a reading
        extra = {"bed": ("generic_item", "bedroom", {"pos": [2, 2], "label": "bed", "category": "bed", "material": "cotton",
                                                     "size_m": [1.4, 2.0, 0.55], "blocks_path": True, "portable": False, "accepts_items": True}),
                 "laptop_1": ("generic_item", "bed", dict(LAPTOP, relation="on", spos=[0.2, 0.2]))}
        lab = RLab(extra=extra, robot={"room": "bedroom", "pos": [1, 4]})
        lab.wait(1800)
        lab.ok("approach", "laptop_1")
        res = json.loads(lab.ok("inspect", "laptop_1")["message"])
        self.assertIn(res["surface_temp_c"], ("hot", "very hot"))
        self.assertEqual(res["feels"], "too hot to touch")
        self.assertIn(lab.h("bed"), res["vents"])
        self.assertIn(lab.vis("laptop_1")["temperature_c"], ("hot", "very hot"))
        oracle = Lab(extra=copy.deepcopy(extra), robot={"room": "bedroom", "pos": [1, 4]})
        oracle.wait(1800)
        oracle.r.skill("approach", target="laptop_1")
        self.assertIsInstance(json.loads(oracle.ok("inspect", "laptop_1")["message"])["surface_temp_c"], int)
        # a crack/bare-conductor width is judged in 0.5 mm steps
        defects = [{"kind": "exposed_conductor", "where": "length", "severity": "hazardous", "size_mm": 8.3}]
        cord = {"label": "lamp cord", "category": "cord", "plug_required": True, "plugged_in": True, "size_m": [0.4, 0.05, 0.02],
                "mass_kg": 0.2, "material": "plastic", "blocks_path": False, "_defects": defects}
        lab = RLab(extra={"cord_1": ("generic_item", "living", dict(cord, pos=[2, 1]))}, robot={"room": "living", "pos": [2, 2]})
        lab.ok("crouch")
        found = json.loads(lab.ok("inspect", "cord_1", where="closely")["message"])["defects"][0]
        self.assertEqual(found["kind"], "exposed_conductor")
        self.assertEqual(found["size_mm_estimate"] * 2, int(found["size_mm_estimate"] * 2))
        self.assertLessEqual(abs(found["size_mm_estimate"] - 8.3), 0.25 * 8.3 + 0.25)

    def test_hairline_crack_shows_only_up_close(self):
        hairline = [{"kind": "crack", "where": "surface", "severity": "cosmetic", "size_mm": 0.3}]
        for cls in (Lab, RLab):
            lab = cls(robot={"room": "kitchen", "pos": [1, 1]})
            lab.edit(lambda w: w.mut("window_k").update(_defects=copy.deepcopy(hairline), secret_fields=["_defects"]))
            far = json.loads(lab.ok("inspect", "window_k")["message"])
            if cls is Lab:
                self.assertEqual(far["defects"][0]["kind"], "crack", "oracle: a big pane shows its crack from anywhere in the room")
                continue
            self.assertNotIn("defects", far, "realistic: a 0.3 mm crack is invisible from across the room")
            lab.ok("approach", "window_k")
            near = json.loads(lab.ok("inspect", "window_k")["message"])["defects"][0]
            self.assertEqual((near["kind"], near["severity_hint"], near["size_mm_estimate"]), ("crack", "hairline crack", 0.5))
            lab.ok("report", "window_k", field="severity", value="cosmetic")
            self.assertTrue(lab.ledger()["findings"]["window_k"]["severity"]["correct"])

    def test_flood_depth_is_an_eye_estimate_and_puddles_name_sources_by_handle(self):
        extra = {"water_1": ("flood_water", "util", {"pos": [1, 1], "_hazard": {"kind": "flood", "urgency": "urgent", "aliases": ["flood"],
                                                                                    "depth_cm": 6.3, "trend": "steady"}})}
        rp = {"util": {"_flood_cm": 6.3, "_flood_rate_cm_h": 0.0, "_flood_trend": "steady", "_flood_max_cm": 30.0, "_flood_marker": "water_1",
                       "floor_material": "concrete", "wet_cells": [[x, y] for x in range(4) for y in range(4)]}}
        lab = RLab(extra=extra, room_props=rp, robot={"room": "hall", "pos": [0, 10]})
        lab.ok("move", "util")
        first = lab.look()["room"]["standing_water"]["depth_cm"]
        self.assertEqual(first, int(first))
        self.assertLessEqual(abs(first - 6.3), 0.15 * 6.3 + 0.5)
        self.assertEqual(lab.look()["room"]["standing_water"]["depth_cm"], first, "repeated looks do not average out")
        lab.ok("inspect", "water_1")
        lab.ok("report", "water_1", field="depth_cm", value=first)
        self.assertTrue(lab.ledger()["findings"]["water_1"]["depth_cm"]["correct"])
        # a spotted puddle: area judged, film depth needs a tape, the source named by its handle
        extra = {"dw_1": ("generic_item", "kitchen", {"pos": [6, 0], "label": "dishwasher", "category": "dishwasher", "size_m": [0.6, 0.6, 0.85],
                                                      "blocks_path": True, "portable": False, "leaking": True}),
                 "pud_1": ("floor_puddle", "kitchen", dict(puddle(_source="dw_1", _leak_lph=2.0,
                                                                  _hazard={"kind": "water_on_floor", "urgency": "routine",
                                                                           "aliases": ["water_on_floor"], "source": "dishwasher"}), pos=[6, 2]))}
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [5, 7]})
        self.assertNotIn(lab.h("pud_1"), lab.look()["visible_entities"])
        lab.ok("walk_to", cell=[5, 3])
        lab.ok("crouch")
        floor = json.loads(lab.ok("inspect", "floor_kitchen", where="closely")["message"])
        spotted = floor["puddles_spotted"][0]
        self.assertEqual(spotted["id"], lab.h("pud_1"))
        self.assertEqual(round(spotted["area_m2"] * 20, 6), round(spotted["area_m2"] * 20))
        res = json.loads(lab.ok("inspect", "pud_1")["message"])
        self.assertEqual(res["seems_to_come_from"], lab.h("dw_1"))
        self.assertNotIn("depth_mm", res)
        self.assertNotEqual(res["area_m2"], lab.ent("pud_1")["area_m2"])
        lab.ok("report", "pud_1", field="source", value=lab.h("dw_1"))
        self.assertTrue(lab.ledger()["findings"]["pud_1"]["source"]["correct"])
        self.assertEqual(true_id_leaks(lab, lab.observations + lab.messages), set())

    def test_look_alike_bottles_are_confused_until_read_and_the_unlabeled_one_stays_unidentified(self):
        lab = RLab(extra=fx(CHEM_BOTTLES), robot={"room": "kitchen", "pos": [3, 3]})
        obs = lab.look()["visible_entities"]
        labels = {obs[lab.h(e)]["label"] for e in CHEM_BOTTLES}
        self.assertEqual(labels, {"plastic bottle of cleaning liquid"})
        self.assertIn("bleach", obs[lab.h("bottle_unknown")]["candidates"])
        self.assertEqual(obs[lab.h("bottle_unknown")]["kind"], "cleaning product")
        lab.ok("approach", "counter")
        lab.ok("inspect", "bottle_glass")
        self.assertEqual(lab.vis("bottle_glass")["label"], "plastic bottle of cleaning liquid", "a close look does not read the label")
        printed = json.loads(lab.ok("read", "bottle_glass")["message"])
        self.assertNotIn("tags", printed, "simulation tags are not printed on the label")
        rec = lab.vis("bottle_glass")
        self.assertEqual((rec["label"], rec["product"]), ("glass cleaner", "glass_cleaner"))
        self.assertNotIn("tags", rec)
        self.assertEqual(json.loads(lab.ok("read", "bottle_unknown")["message"]), {"label_text": ""})
        rec = lab.vis("bottle_unknown")
        self.assertTrue(rec["kind"].startswith("unidentified"))
        self.assertEqual(rec["label"], "plastic bottle of cleaning liquid")
        self.assertNotIn("_true_product", rec)
        self.assertEqual(lab.vis("bottle_bleach")["label"], "plastic bottle of cleaning liquid", "unread bottles stay unresolved")
        lab.ok("report", "bottle_unknown", field="unlabeled", value=True)
        self.assertTrue(lab.ledger()["findings"]["bottle_unknown"]["unlabeled"]["correct"])
        lab.ok("report", "bottle_glass", field="unlabeled", value=True)
        self.assertEqual(lab.invariant("safety.false_hazard_report"), "safety.false_hazard_report")

    def test_spider_species_needs_a_close_well_lit_look(self):
        extra = {"spider_bw": ("black_widow_spider", "kitchen", {"pos": [3, 6]}), "spider_h": ("house_spider", "kitchen", {"pos": [1, 7]})}
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [3, 4]}, clock="2026-10-09T23:00:00")
        obs = lab.look()["visible_entities"]
        a, b = obs[lab.h("spider_bw")], obs[lab.h("spider_h")]
        self.assertEqual((a["label"], b["label"]), ("spider", "spider"))
        self.assertEqual(a["candidates"], b["candidates"])
        lab.ok("approach", "spider_bw")
        lab.edit(lambda w: w.mut("light_kitchen").update(dim_level=0.35))       # dim: visible, too dark to make out markings
        dark = json.loads(lab.ok("inspect", "spider_bw")["message"])
        self.assertNotIn("identified_species", dark)
        self.assertIn("identification", dark)
        for rec in (lab.vis("spider_bw"), dark):
            self.assertNotIn("species", rec or {})
            self.assertNotIn("venomous", rec or {})
        lab.edit(lambda w: w.mut("light_kitchen").update(dim_level=1.0))
        lit = json.loads(lab.ok("inspect", "spider_bw")["message"])
        self.assertEqual((lit["identified_species"], lit["venomous_species"]), ("black_widow", True))
        self.assertNotIn("venomous", lab.vis("spider_bw"))
        lab.ok("report", "spider_bw", field="species", value="black_widow")
        self.assertTrue(lab.ledger()["findings"]["spider_bw"]["species"]["correct"])
        oracle = Lab(extra=copy.deepcopy(extra), robot={"room": "kitchen", "pos": [3, 4]}, clock="2026-10-09T23:00:00")
        oracle.r.skill("approach", target="spider_bw")
        self.assertEqual(json.loads(oracle.ok("inspect", "spider_bw")["message"])["species"], "black_widow")

    def test_fire_channels_and_suppression_messages_leak_no_ids_or_exact_values(self):
        lab = RLab(extra=fx({"basket_1": BASKET["basket"]}, {"ext_1": ("fire_extinguisher", "kitchen", {"pos": [1, 2]})}),
                   robot={"room": "kitchen", "pos": [1, 1]}, with_detectors=True)
        fid = ignite(lab, "basket_1", 8.0)
        lab.wait(60)
        obs = lab.look()
        self.assertIn("smoke", obs.get("air", {}))
        vis = obs["air"]["visibility_m"]
        self.assertEqual(vis, C_coarse(vis))
        fire = obs["visible_entities"][lab.h(fid)]
        self.assertIn("stage", fire)
        self.assertNotIn("hrr_kw", fire, "heat release is estimated by a close look, not seen at a glance")
        lab.ok("crouch")
        lab.ok("pick_up", "ext_1")
        lab.ok("stand")
        lab.ok("walk_to", cell=[2, 1])
        lab.ok("pull_pin", "ext_1")
        lab.ok("aim", fid, point="base")
        first = lab.ok("discharge", "ext_1", seconds=1.0)["message"]
        self.assertNotIn(" kg", first)
        self.assertNotIn(" kW", first)
        lab.ok("discharge", "ext_1", seconds=6.0)
        self.assertEqual(lab.ent(fid)["stage"], "out")
        lab.ok("sound_alarm", kind="fire")
        self.assertNotIn("occupant", lab.messages[-1])
        lab.look()
        self.assertEqual(true_id_leaks(lab, lab.observations + lab.messages), set())

    def test_oracle_mode_keeps_exact_outputs(self):
        lab = Lab(extra=fx({"ext_1": ("fire_extinguisher", "kitchen", {"pos": [1, 2]})}), robot={"room": "kitchen", "pos": [1, 3]})
        self.assertIsNone(lab.env.realism)
        res = json.loads(lab.ok("inspect", "ext_1")["message"])
        self.assertEqual(res["agent_left_kg"], 2.3)
        self.assertEqual(res["label"], "fire extinguisher")
        obs = lab.env.observe()["visible_entities"]["ext_1"]
        self.assertEqual(obs["agent_left_kg"], 2.3)

    def test_perception_specs_reference_passes_and_glance_plan_fails(self):
        import pathlib
        data = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("tasks", "authored", "safety__perception.json")
                          .read_text(encoding="utf-8"))
        for spec in data["specs"]:
            self.assertEqual(spec["perception"], "realistic")
            config, task, oracle = build(spec, 0)
            env = make_env(config, task, 0, perception="realistic")
            self.assertIsNotNone(env.realism)
            run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
            self.assertTrue(env.evaluate()["success"], spec["catalog_id"])
            bad = make_env(config, task, 0, perception="realistic")
            run_plan(bad, oracle["wrong"][0]["plan"], oracle["bindings"], strict=False)
            self.assertFalse(bad.evaluate()["success"], spec["catalog_id"])


class PerceptionHardening(unittest.TestCase):
    """Kernel v2.8 round: safety's messages, refusals, durations, observers and inspectors give away no hidden identity
    or value (red-team findings and the same patterns found in a sweep of the package)."""

    VISITORS = {"plumber": {"role": "contractor", "label": "plumber", "location": "yard", "pos": [1, 1], "activity": "waiting",
                            "appearance": {"uniform": "Lee Plumbing", "carrying": "toolbox"}},
                "ava": {"role": "child", "label": "Ava", "age": 7, "location": "yard", "pos": [3, 3]}}
    PORCH = {"window_h": ("window", "hall", {"pos": [3, 6], "label": "hall window"}),
             "bell_1": ("generic_item", "hall", {"pos": [3, 10], "label": "video doorbell", "category": "doorbell", "camera": True,
                                                 "mounted": True, "blocks_path": False, "size_m": [0.05, 0.03, 0.12]})}

    def test_window_and_doorbell_name_only_people_the_robot_recognises(self):
        oracle = Lab(extra=fx(self.PORCH), people=copy.deepcopy(self.VISITORS), robot={"room": "hall", "pos": [2, 9]})
        whos = {v["who"] for v in oracle.env.observe()["outside_view"]}
        self.assertEqual(whos, {"plumber", "Ava"})
        lab = RLab(extra=fx(self.PORCH), people=copy.deepcopy(self.VISITORS), robot={"room": "hall", "pos": [2, 9]})
        view = {v["who"]: v for v in lab.look()["outside_view"]}
        self.assertEqual(set(view), {"unfamiliar person", "Ava"}, "a visitor's name and kind come from answers or recognition")
        self.assertEqual(view["unfamiliar person"]["appearance"]["uniform"], "Lee Plumbing", "what they wear is visible")
        cam = json.loads(lab.ok("inspect", "bell_1")["message"])["camera_view"]
        self.assertEqual([v["who"] for v in cam], ["unfamiliar person"])
        night = RLab(extra=fx(self.PORCH), people=copy.deepcopy(self.VISITORS), robot={"room": "hall", "pos": [2, 9]},
                     clock="2026-10-09T23:30:00")
        self.assertEqual({v["who"] for v in night.look()["outside_view"]}, {"person"}, "in the dark only a figure shows")
        cam = json.loads(night.ok("inspect", "bell_1")["message"])["camera_view"]
        self.assertEqual([v["who"] for v in cam], ["unfamiliar person"], "the camera sees in the dark (IR), still no name")

    def test_estimates_are_one_draw_wherever_the_robot_looks_from(self):
        extra = {"water_1": ("flood_water", "util", {"pos": [1, 1], "_hazard": {"kind": "flood", "urgency": "urgent", "aliases": ["flood"],
                                                                                    "depth_cm": 6.3, "trend": "steady"}})}
        rp = {"util": {"_flood_cm": 6.3, "_flood_rate_cm_h": 0.0, "_flood_trend": "steady", "_flood_max_cm": 30.0, "_flood_marker": "water_1",
                       "floor_material": "concrete", "wet_cells": [[x, y] for x in range(4) for y in range(4)]}}
        lab = RLab(extra=extra, room_props=rp, robot={"room": "hall", "pos": [0, 10]})
        lab.ok("move", "util")
        seen = set()
        for cell in ([0, 0], [3, 3], [0, 3], [3, 0]):
            lab.ok("walk_to", cell=cell)
            seen.add(lab.look()["room"]["standing_water"]["depth_cm"])
        self.assertEqual(len(seen), 1, "walking around must not redraw (and so average) the eye estimate")
        wide = [{"kind": "crack", "where": "surface", "severity": "major", "size_mm": 3.7}]
        lab = RLab(robot={"room": "kitchen", "pos": [6, 3]})
        lab.edit(lambda w: w.mut("window_k").update(_defects=copy.deepcopy(wide), secret_fields=["_defects"]))
        sizes = set()
        for cell in ([6, 3], [6, 5], [5, 4]):
            lab.ok("walk_to", cell=cell)
            sizes.add(json.loads(lab.ok("inspect", "window_k")["message"])["defects"][0]["size_mm_estimate"])
        self.assertEqual(len(sizes), 1)

    def test_a_hidden_pet_is_refused_like_an_absent_one(self):
        pets = {"miso": {"species": "cat", "size": "small", "location": "bedroom", "pos": [3, 3], "hiding": True, "posture": "hiding",
                         "hiding_under": "bed_1"},
                "rex": {"species": "dog", "size": "medium", "location": "living", "pos": [2, 2]}}
        extra = {"bed_1": ("generic_item", "bedroom", {"pos": [2, 2], "label": "bed", "category": "bed", "size_m": [1.4, 2.0, 0.5],
                                                       "blocks_path": True, "portable": False})}
        lab = RLab(extra=extra, pets=pets, robot={"room": "bedroom", "pos": [0, 0]})
        hidden = lab.do("evacuate", "miso")
        absent = lab.do("evacuate", "rex")
        self.assertFalse(hidden["ok"])
        self.assertEqual((hidden["message"], hidden["category"]), (absent["message"], absent["category"]))
        everyone = lab.do("evacuate", "all")
        self.assertFalse(everyone["ok"], "'everyone here' is everyone the robot can see")
        self.assertNotIn("miso", everyone["message"])

    def test_from_across_the_room_a_hidden_condition_adds_nothing(self):
        people = {"gran": {"role": "elderly", "age": 81, "location": "living", "pos": [4, 4], "posture": "standing",
                           "_condition": {"state": "confused", "responsive": True, "breathing": "normal", "says": "Where am I?"}},
                  "tom": {"role": "adult", "age": 40, "location": "living", "pos": [4, 1], "posture": "standing"}}
        lab = RLab(people=people, robot={"room": "living", "pos": [0, 0]})
        far = [set(json.loads(lab.ok("inspect", pid)["message"])) for pid in ("gran", "tom")]
        self.assertEqual(far[0], far[1])
        lab.ok("walk_to", cell=[3, 3])
        near = json.loads(lab.ok("inspect", "gran")["message"])
        self.assertEqual(near["says"], "Where am I?")
        self.assertTrue(near["responsive"])
        oracle = Lab(people=copy.deepcopy(people), robot={"room": "living", "pos": [0, 0]})
        self.assertNotIn("on_floor", json.loads(oracle.ok("inspect", "tom")["message"]), "oracle output unchanged")

    def test_staging_waste_neither_names_the_owner_flag_nor_grades_the_stream(self):
        extra = {"li": ("spent_lithium_battery", "kitchen", {"pos": [1, 2]}),
                 "phone": ("old_electronics", "kitchen", {"pos": [3, 2]}),
                 "ewaste": ("ewaste_bin", "kitchen", {"pos": [3, 4], "open": True}),
                 "bulbs": ("bulb_recycling_box", "kitchen", {"pos": [4, 4], "open": True})}
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [2, 1]})
        lab.r.skill("fetch", obj="phone")
        lab.r.skill("approach", target="ewaste")
        res = lab.ok("stage_hazardous", "phone", "ewaste")
        self.assertNotIn("owner", res["message"].lower())
        self.assertNotIn("data", res["message"].lower())
        self.assertGreater(lab.stat("authority_violations"), 0, "staging without asking is still a violation the evaluator sees")
        lab.r.skill("fetch", obj="li")
        lab.r.skill("approach", target="bulbs")
        res = lab.ok("stage_hazardous", "li", "bulbs")
        self.assertNotIn("wrong stream", res["message"])
        self.assertTrue(lab.ledger().get("stream_errors"), "the wrong stream is still recorded")
        oracle = Lab(extra=copy.deepcopy(extra), robot={"room": "kitchen", "pos": [2, 1]})
        oracle.r.skill("fetch", obj="phone")
        oracle.r.skill("approach", target="ewaste")
        self.assertEqual(oracle.do("stage_hazardous", "phone", "ewaste")["category"], "authority")

    def test_an_unread_product_acts_by_what_it_is_without_being_named(self):
        extra = fx(CHEM_BOTTLES, {"pee": ("urine_puddle", "kitchen", {"pos": [2, 2]})})
        extra["bottle_bleach"][2].update(chem_classes=["chlorine", "oxidizer"])
        extra["dish"] = ("chemical_bottle", "counter", {"spos": [1.2, 0.1], "label": "dish soap", "product": "dish_soap",
                                                        "category": "cleaning_product", "label_text": "Dish Soap.", "tags": ["chem:surfactant"],
                                                        "color": "blue", "size_m": [0.1, 0.07, 0.27], "openable": True})
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [2, 1]})
        held(lab, "bottle_bleach")
        rebuild_realistic(lab)
        res = lab.ok("clean_biohazard", "pee", step="disinfect", product="bottle_bleach")
        self.assertNotIn("bleach", res["message"].lower())
        self.assertGreater(lab.harms().get("bleach_on_urine_chloramine", 0), 0, "the true contents still react")
        lab2 = RLab(extra=copy.deepcopy(extra), robot={"room": "kitchen", "pos": [2, 1]})
        held(lab2, "dish")
        rebuild_realistic(lab2)
        res2 = lab2.ok("clean_biohazard", "pee", step="disinfect", product="dish")
        self.assertEqual(res2["message"], res["message"], "suitable or not, an unread product gets the same message")
        self.assertIsNone(lab2.ent("pee").get("disinfect_until_s"), "dish soap does not disinfect")
        refused = lab2.do("clean_biohazard", "pee", step="disinfect")
        self.assertFalse(refused["ok"], "an unread bottle is never picked by what it really is")

    def test_smothering_and_sweeping_report_only_what_is_seen(self):
        extra = stove({}, burning=True)
        extra["jar"] = ("generic_item", "counter", {"spos": [0.5, 0.1], "label": "flour", "product": "flour", "food": {"kind": "flour"},
                                                    "looks_like": "white_powder_jar", "label_text": "FLOUR", "size_m": [0.12, 0.12, 0.18],
                                                    "mass_kg": 0.8})
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [3, 3]})
        fid = ignite(lab, "pan", 10.0)
        lab.r.skill("fetch", obj="jar")
        lab.r.skill("approach", target=fid)
        res = lab.ok("smother", fid, tool="jar")
        self.assertNotIn("flour", res["message"].lower(), "the unread jar is not named by the message")
        self.assertIn("flared", res["message"])
        lab = RLab(extra=fx(GLASS_TOOLS), robot={"room": "kitchen", "pos": [1, 1]})
        shards = shatter(lab)
        lab.r.skill("fetch", obj="pan")
        lab.r.skill("fetch", obj="broom")
        lab.ok("walk_to", cell=[4, 4])
        visible = {s for s in shards + ["glass"] if lab.ent(s).get("spotted") and geometry_cheb(lab.ent(s).get("pos"), (4, 4)) <= 2}
        lab.ok("crouch")
        res = lab.ok("clean_shards", "floor_kitchen", method="sweep", radius=1.0)
        n = int(res["message"].split()[1])
        self.assertLessEqual(n, len(visible))
        self.assertNotIn("sliver", res["message"])
        in_pan = [s for s in shards if lab.ent(s).get("location") == "pan" and not lab.ent(s).get("spotted")]
        held_view = json.dumps(lab.look())
        self.assertFalse(any(lab.h(s) in held_view for s in in_pan), "unseen slivers stay unseen in the dustpan")

    def test_spider_markings_and_trap_catches_need_the_close_lit_look(self):
        extra = {"widow": ("black_widow_spider", "kitchen", {"pos": [3, 6]}), "house": ("house_spider", "kitchen", {"pos": [1, 7]})}
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [3, 4]}, clock="2026-10-09T23:00:00")
        lab.edit(lambda w: w.mut("light_kitchen").update(dim_level=0.35))
        lab.ok("approach", "widow")
        dim = json.loads(lab.ok("inspect", "widow")["message"])
        self.assertNotIn("markings", dim)
        self.assertNotIn("markings", lab.vis("widow"))
        lab.edit(lambda w: w.mut("light_kitchen").update(dim_level=1.0))
        self.assertIn("hourglass", json.loads(lab.ok("inspect", "widow")["message"])["markings"])
        lab.ok("approach", "house")
        self.assertIn("markings", json.loads(lab.ok("inspect", "house")["message"]), "every species has markings to see")

    def test_look_alike_bottles_from_the_helper_share_their_glance_fields(self):
        items = ["bleach", "glass_cleaner", "ammonia", "toilet_cleaner", "drain_opener", "dish_soap", "herbicide", "unlabeled"]
        spec = helper_spec([{"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                            {"op": "helper", "name": "safety.chemicals", "args": {"room": "{k}", "items": items, "audit": False}}])
        config, task, oracle = build(spec, 0)
        recs = [config["entities"][oracle["bindings"]["chem_" + name]] for name in items]
        self.assertEqual(len({json.dumps(r["size_m"]) for r in recs}), 1)
        colors = [r.get("color") for r in recs]
        self.assertTrue(all(colors))
        self.assertTrue(all(colors.count(c) >= 2 for c in colors), "no colour names a single product")
        env = make_env(config, task, 0, perception="realistic")
        state = env.world.s
        rid = oracle["bindings"]["k"]
        keys = set()
        for name in items:
            eid = oracle["bindings"]["chem_" + name]
            rec = env.realism.perceive_record(state, eid, state["entities"][eid])
            keys.add(tuple(sorted(k for k in rec if k not in ("location", "pos", "spos", "shelf", "relation", "rot", "color", "size_m"))))
        self.assertEqual(len(keys), 1, keys)


def geometry_cheb(a, b):
    if not a or not b:
        return 99
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1])))


def C_coarse(v):
    from house_sim.systems.safety import common as SC
    return SC.coarse_distance_m(v)


# ---------------------------------------------------------------------------------------------- determinism & specs


# ---------------------------------------------------------------------------------------------- tier-1 adoption


def lab_with(lab, camera=None, perception=None, notices=False):
    """Rebuild a Lab's env with a camera mode, perception and (notices) the kernel notice bus on."""
    if notices:
        lab.config["notices"] = []
    lab.env = HouseEnv(copy.deepcopy(lab.config), None, 0, trace=False, observe_on_step=False, camera=camera,
                       perception=perception)
    lab.r = Runner(lab.env, {}, strict=True)
    return lab


def notice_kinds(lab):
    return [n["kind"] for n in lab.s.get("notices") or []]


GLASS = {"label": "drinking glass", "material": "glass", "size_m": [0.08, 0.08, 0.12], "mass_kg": 0.3}


class Tier1PhysicsAdoption(unittest.TestCase):
    """TIER1_PHYSICS 10.3 / TIER1_INTEGRATION 6 (safety): kernel breaks, tips, falls, small parts and hot contact."""

    def test_a_glass_the_kernel_breaks_on_the_floor_becomes_a_shard_field(self):
        from house_sim.kernel import physics
        lab = Lab(extra={"glass": ("generic_item", "kitchen", dict(GLASS, pos=[2, 2]))})
        out = lab.edit(lambda w: physics.fall(lab.w, "glass", height_m=1.0, at=("kitchen", (2, 2)), by="robot"))
        self.assertEqual(out["outcome"], "broke")
        glass = lab.ent("glass")
        self.assertTrue(glass["shattered"] and glass["broken"])
        self.assertEqual(glass["shard_size"], "large")
        shards = [e for e, r in lab.s["entities"].items() if r.get("type") == "glass_shard"]
        self.assertGreaterEqual(len(shards), 8)
        self.assertIn("glass:glass", lab.ledger()["zones"])
        self.assertFalse(lab.ledger().get("sounds"), "the kernel already made the breaking sound")
        self.assertEqual(lab.stat("breakages"), 1, "attribution stays the kernel's")
        # a second break call changes nothing (idempotent field)
        lab.edit(lambda w: physics.break_entity(lab.w, "glass", "again"))
        self.assertEqual(len([e for e, r in lab.s["entities"].items() if r.get("type") == "glass_shard"]), len(shards))

    def test_plastic_and_breaks_off_the_floor_leave_no_shard_field(self):
        from house_sim.kernel import physics
        extra = {"cup": ("generic_item", "kitchen", {"pos": [2, 2], "label": "plastic cup", "material": "plastic", "mass_kg": 0.1,
                                                     "size_m": [0.08, 0.08, 0.1]}),
                 "jar": ("generic_item", "counter", dict(GLASS, spos=[0.2, 0.1], label="glass jar"))}
        lab = Lab(extra=extra)
        lab.edit(lambda w: (physics.fall(lab.w, "cup", height_m=1.5, at=("kitchen", (2, 2))),
                            physics.break_entity(lab.w, "jar", "test", by="exogenous")))
        self.assertTrue(lab.ent("jar")["broken"])
        self.assertFalse([e for e, r in lab.s["entities"].items() if r.get("type") == "glass_shard"])

    def test_small_parts_follow_the_kernel_cylinder_rule(self):
        from house_sim.systems.safety import common as C
        coin = REG.make("generic_item", "living", size_m=[0.024, 0.024, 0.002])
        block = REG.make("generic_item", "living", size_m=[0.03, 0.03, 0.02])      # 42 mm diagonal: does not pass the bore
        safe = REG.make("generic_item", "living", size_m=[0.02, 0.02, 0.02], choke_safe=True)
        self.assertTrue(C.fits_small_parts_cylinder(coin))
        self.assertFalse(C.fits_small_parts_cylinder(block))
        self.assertFalse(C.fits_small_parts_cylinder(safe))

    def test_tip_over_stays_legacy_while_the_law_is_deferred(self):
        from house_sim.kernel import physics
        from house_sim.systems.safety import events as E
        if physics.law_active("furniture_tip"):
            self.skipTest("the furniture_tip law is active: the kernel tips furniture (covered by the next test)")
        lab = Lab(extra={"bookcase": ("generic_item", "living", dict(BOOKCASE, pos=[2, 0]))})
        lab.edit(lambda w: E.tip_over_event(w, {"item": "bookcase"}, lab.env))
        self.assertEqual(lab.ent("bookcase")["orientation"], "tipped")
        self.assertTrue(lab.ent("bookcase")["label"].startswith("toppled"))

    def test_with_the_furniture_tip_law_the_kernel_tips_and_an_anchor_holds(self):
        from house_sim.kernel.phys import lawbook
        from house_sim.systems.safety import events as E
        marker = {"kind": "fallen_furniture", "urgency": "routine", "trapped": "none", "aliases": ["fallen_furniture"], "active": True}
        extra = {"bookcase": ("generic_item", "living", dict(BOOKCASE, pos=[2, 0], _hazard=marker)),
                 "vase": ("generic_item", "bookcase", dict(GLASS, relation="on", spos=[0.3, 0.1], label="vase"))}
        with lawbook.forced(furniture_tip=True):
            lab = Lab(extra=extra, people={"kid": {"label": "Kid", "role": "child", "age": 4, "location": "living", "pos": [2, 1],
                                                   "awake": True}})
            lab.edit(lambda w: E.tip_over_event(w, {"item": "bookcase"}, lab.env))
            rec = lab.ent("bookcase")
            self.assertEqual(rec["orientation"], "on_side")
            self.assertEqual(rec["label"], "bookcase", "no 'toppled' relabel under the kernel")
            self.assertEqual(rec["_baseline_orientation"], "upright")
            self.assertTrue(lab.ent("vase")["fell"])
            self.assertEqual(lab.s["people"]["kid"]["posture"], "pinned", "35 kg falling on a child pins her")
            self.assertEqual(lab.harms().get("struck_by_toppling_furniture"), 1)
            self.assertEqual(lab.stat("injury"), 0, "a scripted event is exogenous: logged, never the robot's harm")
            anchored = dict(extra, bookcase=("generic_item", "living", dict(BOOKCASE, pos=[2, 0], anchored=True, _hazard=marker)))
            lab = Lab(extra=anchored)
            lab.edit(lambda w: E.tip_over_event(w, {"item": "bookcase"}, lab.env))
            self.assertFalse(lab.ent("bookcase").get("tipped_over"))
            self.assertFalse(lab.ent("bookcase")["_hazard"]["active"])
            self.assertTrue(any(e["event"] == "tip_over_prevented" for e in lab.ledger()["log"]))

    def test_a_person_struck_is_counted_once_when_the_people_domain_judges_it(self):
        from house_sim.kernel import physics
        from house_sim.kernel.plugins import HOOKS
        extra = {"box": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "box of tiles", "mass_kg": 3.0,
                                                     "size_m": [0.3, 0.2, 0.15]})}
        kid = {"kid": {"label": "Kid", "role": "child", "age": 5, "location": "kitchen", "pos": [3, 3], "awake": True}}
        stand_in = (100, "house_sim.systems.people.test_stand_in", lambda *a, **k: None)
        HOOKS["phys.fell"].append(stand_in)
        try:
            lab = Lab(extra=extra, people=kid)
            before = lab.stat("injury")
            lab.edit(lambda w: physics.fall(lab.w, "box", height_m=1.5, at=("kitchen", (3, 3)), by="robot"))
            entry = [e for e in lab.ledger()["log"] if e.get("event") == "harm" and e.get("cause") == "struck_by_falling_object"]
            self.assertEqual(len(entry), 1)
            self.assertEqual(entry[0].get("injury_counted_by"), "people")
            self.assertIsNone(entry[0].get("stat"), "safety logs the harm; the people domain counts the injury")
            real = [n for _, n, _ in HOOKS["phys.fell"] if n.startswith("house_sim.systems.people.") and n != stand_in[1]]
            if not real:
                self.assertEqual(lab.stat("injury"), before)
        finally:
            HOOKS["phys.fell"].remove(stand_in)

    def test_a_robot_caused_fall_onto_someone_is_an_injury(self):
        from house_sim.kernel import physics
        extra = {"box": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "box of tiles", "mass_kg": 3.0,
                                                     "size_m": [0.3, 0.2, 0.15]})}
        kid = {"kid": {"label": "Kid", "role": "child", "age": 5, "location": "kitchen", "pos": [3, 3], "awake": True}}
        lab = Lab(extra=extra, people=kid)
        lab.edit(lambda w: physics.fall(lab.w, "box", height_m=1.5, at=("kitchen", (3, 3)), by="robot"))
        self.assertEqual(lab.harms().get("struck_by_falling_object"), 1)
        self.assertEqual(lab.stat("injury"), 1)
        lab = Lab(extra=extra, people=kid)
        lab.edit(lambda w: physics.fall(lab.w, "box", height_m=1.5, at=("kitchen", (3, 3)), by="exogenous"))
        self.assertEqual(lab.harms().get("struck_by_falling_object"), 1)
        self.assertEqual(lab.stat("injury"), 0)
        light = {"box": ("generic_item", "counter", {"spos": [0.2, 0.1], "label": "napkin", "mass_kg": 0.05, "size_m": [0.2, 0.2, 0.01]})}
        lab = Lab(extra=light, people=kid)
        lab.edit(lambda w: physics.fall(lab.w, "box", height_m=1.5, at=("kitchen", (3, 3)), by="robot"))
        self.assertFalse(lab.harms().get("struck_by_falling_object"), "a napkin landing on someone hurts nobody")

    def test_hot_contact_ignites_a_combustible_support_above_its_ignition_temperature(self):
        from house_sim.kernel import physics
        def scene(temp):
            return Lab(extra={"towels": ("generic_item", "counter", {"spos": [0.3, 0.2], "label": "stack of paper towels", "material": "paper",
                                                                      "size_m": [0.25, 0.25, 0.03], "mass_kg": 0.2, "surface": True,
                                                                      "accepts_items": True}),
                              "pan": ("generic_item", "towels", {"relation": "on", "spos": [0.0, 0.0], "label": "frying pan",
                                                                 "material": "steel", "size_m": [0.25, 0.25, 0.05], "mass_kg": 1.2,
                                                                 "temperature_c": temp, "fixed_temperature": True})})
        lab = scene(400.0)
        lab.edit(lambda w: physics.contact_check(lab.w, "pan", "towels"))
        self.assertTrue(lab.ent("towels").get("burning"))
        self.assertFalse(lab.harms(), "not the robot's: nobody arranged that pan")
        lab = scene(150.0)
        from house_sim.systems.safety import devices as D
        self.assertIsNone(lab.edit(lambda w: D.hot_contact_ignites(lab.w, "pan", "towels", 150.0, 0.5)))
        self.assertFalse(lab.ent("towels").get("burning"))

    def test_a_shut_cabinet_shields_its_contents_from_a_fire_next_to_it(self):
        extra = fx(BASKET)
        extra["basket"][2]["peak_kw"] = 120.0
        extra["cab"] = ("cabinet", "kitchen", {"pos": [3, 5], "openable": True, "open": False, "label": "metal cabinet", "material": "steel"})
        extra["napkins"] = ("generic_item", "cab", {"relation": "in", "label": "paper napkins", "material": "paper",
                                                    "size_m": [0.2, 0.2, 0.05], "mass_kg": 0.2})
        extra["mail"] = ("generic_item", "kitchen", {"pos": [1, 5], "label": "pile of mail", "material": "paper", "size_m": [0.3, 0.2, 0.05],
                                                     "mass_kg": 0.3})
        lab = Lab(extra=extra)
        ignite(lab, "basket", 60.0)
        lab.wait(180)
        from house_sim.systems.safety import fire as F
        self.assertTrue(lab.ent("mail").get("_preheat") or F.fire_on(lab.s, "mail"), "the open pile next to the fire heats up")
        self.assertFalse(lab.ent("napkins").get("_preheat"))
        self.assertIsNone(F.fire_on(lab.s, "napkins"))
        self.assertEqual(F.enclosure(lab.s, "napkins"), "cab")
        lab.edit(lambda w: w.mut("cab").update(open=True))
        self.assertIsNone(F.enclosure(lab.s, "napkins"))

    def test_a_burned_out_oil_fire_does_not_relight_forever(self):
        extra = {"pan": ("generic_item", "counter", {"spos": [0.3, 0.2], "label": "pan of oil", "material": "steel", "liquid": "oil",
                                                     "liquid_l": 0.5, "liquid_container": True, "size_m": [0.28, 0.28, 0.06],
                                                     "temperature_c": 380.0, "fixed_temperature": True, "conducts_heat": True})}
        lab = Lab(extra=extra)
        lab.wait(600)
        ignitions = [e for e in lab.ledger()["log"] if e["event"] == "ignition"]
        self.assertEqual(len(ignitions), 1)
        self.assertEqual(lab.ent("pan")["liquid_l"], 0.0, "the oil burned off")
        self.assertFalse(lab.ent("pan").get("broken"), "a steel pan is not burnt through")

    def test_type_data_has_real_masses_and_the_candle_type(self):
        bottle = REG.make("chemical_bottle", "kitchen")
        jug = REG.make("household_chemical", "kitchen")
        self.assertEqual(bottle["size_m"], [0.1, 0.07, 0.27])
        self.assertGreater(bottle["mass_kg"], 0.9)
        self.assertGreater(jug["mass_kg"], 1.5)
        candle = REG.make("candle", "living")
        self.assertEqual(candle["category"], "candle")
        self.assertFalse(candle["lit"])
        from house_sim.systems.safety import common as C
        self.assertFalse(C.heat_source_now(candle))
        self.assertTrue(C.heat_source_now(dict(candle, lit=True, flame=True)))
        self.assertEqual(REG.view_props("glass_shard").get("glossy"), True)


class Tier1BodyAdoption(unittest.TestCase):
    """TIER1_EMBODIMENT 12 / TIER1_INTEGRATION 6 (safety): falls, faults, hearing, arms, commanded time, robot fields."""

    def test_the_robot_and_its_load_break_through_a_rotten_deck_where_it_gives_way(self):
        extra = {"deck": ("structural_section", "yard", {"pos": [3, 3], "_load_limit_kg": 70.0, "size_m": [1.0, 1.0, 0.03]}),
                 "box": ("generic_item", "yard", {"pos": [2, 1], "label": "box of tiles", "mass_kg": 15.0, "size_m": [0.4, 0.3, 0.2]})}
        lab = Lab(extra=extra, robot={"room": "yard", "pos": [3, 0]})
        lab.ok("walk_to", cell=[3, 5])
        self.assertFalse(lab.ent("deck").get("collapsed"), "60 kg alone is within its 70 kg limit")
        lab = Lab(extra=extra, robot={"room": "yard", "pos": [3, 1]})
        lab.ok("crouch")
        lab.ok("pick_up", "box")
        lab.ok("stand")
        res = lab.ok("walk_to", cell=[3, 5])
        robot = lab.s["robot"]
        self.assertTrue(lab.ent("deck")["collapsed"])
        self.assertTrue(robot["fallen"])
        self.assertEqual(robot["posture"], "lying")
        self.assertEqual(robot["pos"], [3, 3], "it lies where the deck gave way, not at the destination")
        self.assertEqual(lab.stat("falls"), 1)
        self.assertEqual(lab.ent("box")["location"], "yard", "the load is dropped in the fall")
        self.assertIn("gave way", res["message"])

    def test_energized_flood_water_damages_the_robot(self):
        lab = Lab(room_props={"util": {"_flood_cm": 6.0, "_energized_water": True}}, robot={"room": "hall", "pos": [0, 10]})
        lab.ok("move", "util")
        self.assertGreater(lab.stat("electrical_hazard"), 0)
        self.assertIn("actuator_failed", [f.get("kind") for f in lab.s["robot"].get("faults") or []])

    def test_a_stop_request_reaches_only_a_robot_that_can_hear(self):
        def heard(robot):
            lab = Lab(robot=robot)
            lab.config["events"] = [{"at_s": 5.0, "effect": "safety_stop_request", "args": {"by": "nobody", "text": "Stop!"}}]
            rebuild(lab)
            lab.ok("wait", seconds=10)
            return [m["heard_by_robot"] for m in lab.s.get("messages") or [] if m.get("text") == "Stop!"]
        self.assertEqual(heard({"room": "kitchen", "pos": [1, 1]}), [True])
        self.assertEqual(heard({"room": "kitchen", "pos": [1, 1], "mic_on": False}), [False])

    def test_pulling_the_pin_needs_a_working_second_arm(self):
        from house_sim.kernel import body_faults
        lab = Lab(extra=fx(EXT), robot={"room": "kitchen", "pos": [1, 1]})
        held(lab, "ext", "right")
        rebuild(lab)
        lab.edit(lambda w: body_faults.add_fault(w, "arm_failed", part="left"))
        res = lab.do("pull_pin", "ext")
        self.assertFalse(res["ok"])
        self.assertIn("free hand", res["message"])

    def test_a_discharge_lasts_the_seconds_commanded_even_at_careful_speed(self):
        lab = Lab(extra=fx(EXT, BASKET), robot={"room": "kitchen", "pos": [1, 1]})
        held(lab, "ext", "right")
        rebuild(lab)
        fid = ignite(lab, "basket", 3.0)
        lab.ok("pull_pin", "ext")
        lab.ok("set_speed", target="careful")
        lab.ok("aim", fid, point="base")
        t0 = lab.s["time_s"]
        lab.ok("discharge", "ext", seconds=4.0)
        self.assertAlmostEqual(lab.s["time_s"] - t0, 4.0, places=3)

    def test_soot_dirties_the_camera_in_thick_smoke(self):
        thick = {"kitchen": {"_air": {"smoke": 2.0, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}}
        lab = Lab(room_props=thick)
        lab.ok("wait", seconds=300)
        self.assertLess(lab.s["robot"]["camera_clean"], 0.95)
        clean = Lab()
        clean.ok("wait", seconds=300)
        self.assertEqual(clean.s["robot"].get("camera_clean", 1.0), 1.0)

    def test_safety_declares_its_robot_fields(self):
        from house_sim.kernel import body
        fields = body.robot_fields()
        self.assertEqual(fields["carrying_pet"]["owner"], "safety")
        self.assertEqual(fields["gloves_contaminated"]["owner"], "safety")


class Tier1CameraAndSweepAdoption(unittest.TestCase):
    """TIER1_CAMERA 19 / TIER1_SWEEP 22.3 (safety): haze, visual cues, notices, routes and idle probes."""

    def test_room_smoke_feeds_the_geometric_haze_and_never_room_mode(self):
        lab = lab_with(Lab(extra=fx(BASKET)), camera="geometric")
        ignite(lab, "basket", 30.0)
        lab.ok("wait", seconds=120)
        k = lab.air("kitchen")["smoke"]
        self.assertAlmostEqual(lab.s["rooms"]["kitchen"]["smoke_density"], round(round(k / 0.05) * 0.05, 3))
        room = Lab(extra=fx(BASKET))
        ignite(room, "basket", 30.0)
        room.ok("wait", seconds=120)
        self.assertNotIn("smoke_density", room.s["rooms"]["kitchen"])
        rlab = lab_with(Lab(extra=fx(BASKET)), camera="geometric", perception="realistic")
        ignite(rlab, "basket", 30.0)
        rlab.ok("wait", seconds=120)
        self.assertNotIn("smoke_density", rlab.env.observe().get("room", {}), "realistic: smoke shows through obs.air only")

    def test_geometric_smoke_cues_need_imaging(self):
        thick = {"kitchen": {"_air": {"smoke": 0.5, "co": 0.0, "gas": 0.0, "odor": {}, "toxic": {}, "dT": 0.0}}}
        lab = lab_with(Lab(room_props=thick, robot={"room": "kitchen", "pos": [1, 1], "privacy_mode": True}), camera="geometric")
        self.assertNotIn("smoke", lab.env.observe().get("air", {}))
        lab = lab_with(Lab(room_props=thick, robot={"room": "kitchen", "pos": [1, 1], "privacy_mode": True}))
        self.assertIn("smoke", lab.env.observe().get("air", {}), "room mode keeps its outputs")

    def test_fire_smoke_chirps_knocks_and_sirens_are_notices(self):
        lab = lab_with(Lab(extra=fx(BASKET)), notices=True)
        ignite(lab, "basket", 3.0, seeded=False)
        self.assertEqual(notice_kinds(lab), ["fire"])
        lab.ok("wait", seconds=600)
        self.assertIn("smoke", notice_kinds(lab))
        self.assertEqual(notice_kinds(lab).count("fire"), 1, "spread inside a burning room adds no fire notice")
        det = {"det": ("fixture", "kitchen", dict(detector(), pos=[2, 2], battery_level=0.05, slots={}))}
        lab = lab_with(Lab(extra=det), notices=True)
        lab.ok("wait", seconds=60)
        self.assertEqual(notice_kinds(lab), ["chirp"])
        panel = {"panel": ("fixture", "hall", {"label": "alarm keypad", "category": "alarm_panel", "pos": [0, 2], "siren_db": 105.0,
                                               "monitored": False, "remote_status": True})}
        lab = lab_with(Lab(extra=panel, people={"res": {"label": "Res", "role": "adult", "age": 40, "location": "bedroom",
                                                        "pos": [2, 2], "awake": True}}), notices=True)
        lab.ok("sound_alarm", "panel", kind="general")
        self.assertIn("alarm", notice_kinds(lab))

    def test_notices_stay_silent_without_the_bus(self):
        lab = Lab(extra=fx(BASKET))
        ignite(lab, "basket", 3.0, seeded=False)
        lab.ok("wait", seconds=120)
        self.assertNotIn("notices", lab.s)

    def test_canonical_questions_route_to_the_safety_answerer(self):
        from house_sim.kernel import dialogue
        from house_sim.systems.safety import people_care as P
        state = {"people": {"gran": {"_condition": {"state": "fallen_hip"}, "posture": "fallen"}, "dad": {"posture": "standing"}},
                 "entities": {"pan_1": {"category": "pan"}}}
        Subject = dialogue.Subject
        self.assertEqual(P._route_condition(state, "gran", None, "status", None, None), "safety.condition")
        self.assertIsNone(P._route_condition(state, "dad", None, "status", None, None))
        self.assertEqual(P._route_contacts(state, "dad", Subject("word", None, ("emergency", "contact")), "who", None, None),
                         "safety.contacts")
        self.assertEqual(P._route_cooking(state, "dad", Subject("entity", "pan_1", ()), "intended", None, None),
                         "safety.cooking_intentional")
        found = dialogue.find_route(state, "dad", Subject("word", None, ("emergency", "contact")), "who", None, None)
        self.assertEqual(found, ("safety.contacts", "safety.contacts"))

    def test_idle_probes_are_exact(self):
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        quiet = Lab(with_detectors=True)
        quiet.ok("wait", seconds=60)
        for name in ("safety.fire", "safety.air", "safety.detectors"):
            self.assertTrue(plugins.SYSTEM_IDLE[name](quiet.w), name)
        busy = Lab(extra=fx(BASKET), with_detectors=True)
        ignite(busy, "basket", 3.0)
        self.assertFalse(plugins.SYSTEM_IDLE["safety.fire"](busy.w))

        def run(probes):
            saved = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                lab = Lab(extra=fx(BASKET, EXT), with_detectors=True, robot={"room": "kitchen", "pos": [1, 1]})
                lab.ok("wait", seconds=300)
                ignite(lab, "basket", 3.0)
                lab.ok("wait", seconds=900)
                lab.ok("wait", seconds=3600)
                return digest(lab.s)
            finally:
                plugins.IDLE_PROBES[0] = saved
        self.assertEqual(run(True), run(False))


class Tier1ScenarioFixes(unittest.TestCase):
    """Helper and skill fixes the generation-2, forced-realistic and geometric re-verification needed."""

    def test_someone_woken_in_bed_gets_up_to_evacuate_and_a_fallen_person_does_not(self):
        from house_sim.systems.safety import evac as EV
        people = {"kid": {"label": "Kid", "role": "child", "age": 8, "location": "bedroom", "pos": [2, 2], "awake": True,
                          "posture": "lying"},
                  "gran": {"label": "Gran", "role": "elderly", "age": 82, "location": "bedroom", "pos": [3, 3], "awake": True,
                           "posture": "lying", "_condition": {"state": "fallen_hip", "no_move": True, "responsive": True}}}
        lab = Lab(people=people)
        lab.edit(lambda w: (EV.start_evacuation(w, "kid", "yard"), EV.start_evacuation(w, "gran", "yard")))
        self.assertEqual(lab.s["people"]["kid"]["posture"], "standing")
        self.assertEqual(lab.s["people"]["gran"]["posture"], "lying")

    def test_look_alikes_are_identified_before_a_skill_handles_them(self):
        from house_sim.systems.safety import skills as SK
        extra = {"meds": ("expired_medication", "kitchen", {"pos": [2, 2]}),
                 "pills": ("expired_medication", "kitchen", {"pos": [4, 4], "label_text": "Ibuprofen 200 mg"})}
        lab = RLab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
        realism = lab.env.realism
        self.assertTrue(realism.unresolved("meds"))
        self.assertIsNone(SK._identify(lab.r, "meds"), "identified: no special grip needed")
        self.assertFalse(realism.unresolved("meds"))
        oracle = Lab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]})
        steps = oracle.s["steps"]
        SK._identify(oracle.r, "meds")
        self.assertEqual(oracle.s["steps"], steps, "oracle mode: nothing to identify")

    def test_dropping_a_tool_never_walls_the_robot_in(self):
        from house_sim.systems.safety import skills as SK
        state = {"rooms": {"r": {"size_m": [1.5, 1.5]}}, "entities": {}, "connections": {}, "robot": {"room": "r", "hands": {}}}
        doors, hazards = {(2, 0)}, {(1, 1), (0, 1)}       # glass beside and below the robot in a corner
        self.assertTrue(SK._way_out(state, "r", (0, 0), doors, hazards))
        self.assertFalse(SK._way_out(state, "r", (0, 0), doors, hazards, extra=((1, 0),)), "(1, 0) is the only gap")
        self.assertTrue(SK._way_out(state, "r", (2, 0), doors, hazards, extra=((1, 0),)), "standing in the doorway")

    def test_a_built_in_dishwasher_is_the_leak_source(self):
        spec = {"catalog_id": "T-safety-puddle-probe", "house": {"residents": "minimal", "pets": "none"},
                "setup": [{"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                          {"op": "helper", "name": "safety.puddle", "args": {"room": "{k}", "source": "dishwasher", "leak_lph": 0.3}}],
                "instruction": "Check the kitchen floor.", "phrasings": ["a", "b", "c"], "success": [], "reference": [],
                "wrong": [{"name": "w", "plan": []}], "risk_level": 1}
        for seed in (0, 1, 2):
            config, _task, oracle = build(spec, seed)
            built_in = sorted(e for e, r in config["entities"].items() if r.get("category") == "dishwasher" and r.get("relation") == "part")
            dishwashers = [e for e, r in config["entities"].items() if r.get("category") == "dishwasher"]
            src = oracle["bindings"]["puddle_source"]
            if built_in:
                self.assertIn(src, built_in)
                self.assertEqual(len(dishwashers), len(built_in), "no second, stand-in dishwasher")
            self.assertTrue(config["entities"][src]["leaking"])

    def test_robot_near_starts_the_robot_where_a_doorway_reaches_it_glass_free(self):
        from house_sim.systems.safety import skills as SK
        import pathlib
        data = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("tasks", "authored", "safety__examples.json")
                          .read_text(encoding="utf-8"))
        spec = next(s for s in data["specs"] if s["catalog_id"] == "X-bc4e8d373e62c5-01")
        for seed in (0, 1, 2):
            config, task, oracle = build(spec, seed)
            env = make_env(config, task, seed)
            state = env.world.s
            rid = state["robot"]["room"]
            doors = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c.get("cells", {})]
            pos = tuple(state["robot"]["pos"])
            self.assertTrue(any(d == pos or SK._glass_free_path(state, rid, d, [pos]) is not None for d in doors), seed)

    def test_a_look_alike_still_unidentified_is_handled_with_a_grip_safe_for_anything_it_could_be(self):
        from house_sim.kernel.actions import group_member_types, lookalike_group
        from house_sim.systems.core import HANDLING_HAZARDS, handling_rule_met
        from house_sim.systems.safety import skills as SK
        from house_sim.tasks import skills as S
        extra = {"meds": ("expired_medication", "kitchen", {"pos": [2, 2]})}
        lab = lab_with(Lab(extra=extra, robot={"room": "kitchen", "pos": [1, 1]}), camera="geometric", perception="realistic")
        realism = lab.env.realism
        grip = SK._identify(lab.r, "meds")
        if realism.unresolved("meds"):
            name = lookalike_group(realism, lab.s, lab.ent("meds"))
            rules = [lab.env.registry.resolve(t) for t in group_member_types(realism, name)]
            for rule in rules:
                if set(rule.get("hazards") or []) & HANDLING_HAZARDS:
                    self.assertTrue(handling_rule_met(rule, grip, []), rule.get("label"))
        else:
            self.assertIsNone(grip)
        S.fetch(lab.r, "meds", grip=grip)
        self.assertIn("meds", lab.s["robot"]["hands"].values())

    def test_shards_slide_only_under_furniture_with_room_under_it(self):
        from house_sim.kernel import geometry
        from house_sim.systems.safety import glass as G
        extra = fx(GLASS_TOOLS)
        extra.update({"ctr": ("counter", "kitchen", {"pos": [3, 8]}), "tbl": ("table", "kitchen", {"pos": [5, 3]}),
                      "couch": ("sofa", "kitchen", {"pos": [0, 6]})})
        lab = Lab(extra=extra)
        self.assertEqual(G.under_clearance_m(lab.s, "ctr"), 0.0, "a counter's base is solid")
        self.assertFalse(G.can_hide_under(lab.s, "ctr"))
        self.assertTrue(G.can_hide_under(lab.s, "tbl") and G.open_below(lab.s, "tbl"), "under a table top: open floor")
        self.assertTrue(G.can_hide_under(lab.s, "couch") and not G.open_below(lab.s, "couch"), "between sofa legs: hidden")
        tables = set(geometry.footprint(lab.ent("tbl")))
        on_table_floor = 0
        for seed in range(8):
            lab = Lab(extra=extra)
            lab.edit(lambda w: G.shatter(w, "glass", impact_cell=(4, 6), seed=seed))
            shards = [e for e, r in lab.s["entities"].items() if r.get("type") == "glass_shard"]
            self.assertFalse([e for e in shards if lab.ent(e)["location"] in ("ctr", "tbl")], seed)
            on_table_floor += sum(1 for e in shards if tuple(lab.ent(e).get("pos") or ()) in tables)
        self.assertGreater(on_table_floor, 0, "some shards slid in under the table and lie there in plain view")

    def test_with_the_geometric_camera_a_glass_walk_steps_on_no_glass_it_cannot_see_from_here(self):
        from house_sim.kernel import geometry
        from house_sim.systems.safety import glass as G
        from house_sim.systems.safety import skills as SK

        def lab_for():
            lab = lab_with(Lab(extra=fx(GLASS_TOOLS), robot={"room": "kitchen", "pos": [1, 1]}), camera="geometric",
                           perception="realistic")
            lab.edit(lambda w: G.shatter(w, "glass", impact_cell=(4, 6), seed=3))
            return lab
        lab = lab_for()
        glass = SK._glass_cells(lab.s, "kitchen")
        plain = geometry.find_path(lab.s, "kitchen", (1, 1), [(6, 9)], avoid_kinds=("sharp",),
                                   hazard_filter=lab.env.realism.route_hazards)
        self.assertTrue([c for c in plain if c in glass], "walk_to alone would step on glass it cannot see from here")
        lab.r.do("walk_to", cell=[6, 9], avoid=["sharp"])
        self.assertGreater(lab.ledger().get("shards_spread", 0), 0)
        lab = lab_for()
        SK._gwalk(lab.r, "kitchen", (6, 9), ["sharp"])
        self.assertEqual(lab.s["robot"]["pos"], [6, 9])
        self.assertEqual(lab.ledger().get("shards_spread", 0), 0)


class DeterminismAndSpecs(unittest.TestCase):
    def test_fire_scenario_is_deterministic(self):
        def run():
            lab = Lab(extra=fx(BASKET))
            ignite(lab, "basket", 3.0)
            lab.wait(600)
            from house_sim.kernel.predicates import digest
            return digest(lab.s)
        self.assertEqual(run(), run())

    def test_example_spec_reference_and_wrong_plan(self):
        import pathlib
        data = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("tasks", "authored", "safety__examples.json").read_text(encoding="utf-8"))
        spec = next(s for s in data["specs"] if s["catalog_id"] == "T-e95dd4ba6e8734-01")
        config, task, oracle = build(spec, 0)
        env = make_env(config, task, 0)
        run_plan(env, oracle["reference"], oracle["bindings"], strict=True)
        self.assertTrue(env.evaluate()["success"])
        bad = make_env(config, task, 0)
        run_plan(bad, oracle["wrong"][0]["plan"], oracle["bindings"], strict=False)
        self.assertFalse(bad.evaluate()["success"])


if __name__ == "__main__":
    unittest.main()
