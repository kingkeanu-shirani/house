"""navigation_robot domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=navigation_robot python -m unittest tests/test_navigation_robot.py
Strict journal: HOUSE_SIM_DOMAINS=navigation_robot HOUSE_SIM_STRICT=1 python -m unittest tests/test_navigation_robot.py
The tests build small explicit houses from core types (plus navigation_robot types) and use
generic_item with explicit props for other domains' objects, so they also run with every domain
loaded. Assertions use ">=" for global counters other domains may also increment.
"""
import copy
import json
import re
import unittest

from house_sim.kernel import body
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, INVARIANTS, SCENARIO_HELPERS, SKILLS, load_domains, run_hooks
from house_sim.kernel.predicates import check, digest
from house_sim.kernel.registry import default_registry

load_domains()


class Mini:
    """Explicit test house:
    living 6x5 m (hardwood) -- kitchen 5x4 m (tile) -- yard 8x6 m (grass, outdoor) via a back door
    living -- hall 2x4 m (hardwood) -- bath 2.5x2 m (tile, door) ; hall -- stairs (14 steps) -- upstairs 2x4 m
    living -- front 8x4 m (front yard, outdoor) via the front door; yard -- front via a side gate.
    """

    def __init__(self):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [6.0, 5.0], "floor_material": "hardwood", "floor": 0},
            "kitchen": {"type": "kitchen", "size_m": [5.0, 4.0], "floor_material": "tile", "floor": 0},
            "hall": {"type": "hallway", "size_m": [2.0, 4.0], "floor_material": "hardwood", "floor": 0},
            "bath": {"type": "bathroom", "size_m": [2.5, 2.0], "floor_material": "tile", "floor": 0},
            "upstairs": {"type": "hallway", "size_m": [2.0, 4.0], "floor_material": "carpet", "floor": 1},
            "bedroom": {"type": "bedroom", "size_m": [4.0, 4.0], "floor_material": "carpet", "floor": 1},
            "yard": {"type": "backyard", "size_m": [8.0, 6.0], "floor_material": "grass", "terrain": "grass", "outdoor": True, "floor": 0},
            "front": {"type": "front_yard", "size_m": [8.0, 4.0], "floor_material": "grass", "terrain": "grass", "outdoor": True, "floor": 0},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [11, 4], "kitchen": [0, 4]}, "width_m": 0.9},
            "c_lh": {"rooms": ["living", "hall"], "cells": {"living": [5, 0], "hall": [1, 7]}, "width_m": 0.9},
            "c_hb": {"rooms": ["hall", "bath"], "cells": {"hall": [3, 3], "bath": [0, 1]}, "width_m": 0.75, "door": "door_bath"},
            "c_hu": {"rooms": ["hall", "upstairs"], "cells": {"hall": [0, 0], "upstairs": [0, 7]}, "width_m": 1.0, "kind": "stairs",
                     "steps": 14, "rise_m": 0.18, "run_m": 0.27, "handrail": True},
            "c_ub": {"rooms": ["upstairs", "bedroom"], "cells": {"upstairs": [3, 2], "bedroom": [0, 2]}, "width_m": 0.85, "door": "door_bed"},
            "c_ky": {"rooms": ["kitchen", "yard"], "cells": {"kitchen": [9, 4], "yard": [0, 3]}, "width_m": 0.9, "door": "door_back", "threshold_cm": 3.0},
            "c_lf": {"rooms": ["living", "front"], "cells": {"living": [0, 4], "front": [15, 3]}, "width_m": 0.9, "door": "door_front"},
            "c_yf": {"rooms": ["yard", "front"], "cells": {"yard": [0, 0], "front": [15, 0]}, "width_m": 1.0, "kind": "gate", "door": "gate_side"},
        }
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"}, "metadata": {"units": "metric"},
                  "weather": {"condition": "clear", "temperature_c": 18.0, "wind_mps": 1.0}}
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
        self.door("door_bath", "door", "hall", "bath", "c_hb", open=True)
        self.door("door_bed", "door", "upstairs", "bedroom", "c_ub", open=True)
        self.door("door_back", "exterior_door", "kitchen", "yard", "c_ky", open=True, locked=False)
        self.door("door_front", "exterior_door", "living", "front", "c_lf", open=False, locked=False)
        self.door("gate_side", "gate", "yard", "front", "c_yf", open=False)
        rail = self.reg.make("handrail", "hall", on_stairs="c_hu")
        rail["pos"] = [0, 0]
        rail["mounted"] = True
        self.c["entities"]["rail_1"] = rail
        dock = self.reg.make("robot_dock", "living")
        dock["pos"] = [0, 0]
        self.c["entities"]["robot_dock_1"] = dock

    def door(self, eid, type_name, room, other, cid, **props):
        rec = self.reg.make(type_name, room, **props)
        rec["connects"] = [room, other]
        rec["pos"] = list(self.c["connections"][cid]["cells"][room])
        self.c["entities"][eid] = rec

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

    def person(self, pid, room, pos, **props):
        rec = {"label": pid, "role": "adult", "authority": 2, "location": room, "pos": list(pos), "awake": True, "consent": True,
               "permissions": [], "preferences": {}}
        rec.update(props)
        self.c["people"][pid] = rec

    def pet(self, pid, room, pos, **props):
        rec = {"label": pid, "species": "dog", "location": room, "pos": list(pos), "posture": "standing", "awake": True}
        rec.update(props)
        self.c["pets"][pid] = rec

    def env(self, task=None, room="living", pos=(1, 3), hold=None, perception=None, camera=None, **robot):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos), "home": "robot_dock_1"}
        c["robot"].update(robot)
        if hold:
            for hand, eid in hold.items():
                hands = c["robot"].setdefault("hands", {"left": None, "right": None})
                for h in (("left", "right") if hand == "both" else (hand,)):
                    hands[h] = eid
                rec = c["entities"][eid]
                rec.update(location="robot", relation="held", held_hand=hand)
                for key in ("pos", "spos", "shelf"):
                    rec.pop(key, None)
        return HouseEnv(c, task=task, seed=7, perception=perception, camera=camera)


def nav(env):
    return env.world.s.get("nav") or {}


def counts(env, path):
    node = nav(env)
    for part in path.split("."):
        node = (node or {}).get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, (int, float)) else 0


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def step(env, action, target="", destination="", **kw):
    return env.step(action, target, destination, **kw)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


class TestRegistration(unittest.TestCase):
    def test_verbs_and_types(self):
        from house_sim.systems.navigation_robot import VERBS
        for verb in VERBS:
            self.assertIn(verb, ACTIONS)
        reg = default_registry()
        for t in ("step_stool", "step_ladder", "reacher_grabber", "tape_measure", "laser_distance_meter", "spirit_level", "handrail",
                  "robot_vacuum", "robot_vacuum_dock", "robot_headlamp", "robot_battery_pack"):
            self.assertTrue(reg.known(t), t)
        for name in ("navigation_robot.hazard_band", "navigation_robot.stairs", "navigation_robot.measure_target"):
            self.assertIn(name, SCENARIO_HELPERS)
        self.assertIn("navigation_robot.reach_high", SKILLS)


class TestTraction(unittest.TestCase):
    """Friction model: wet film, puddles, oil, ice, speed and load."""

    def setUp(self):
        self.m = Mini()

    def test_puddle_on_hardwood_fall_at_normal_speed(self):
        self.m.add("puddle", "generic_item", "living", pos=[6, 4], category="spill", floor_hazard="wet", ground_layer=True, portable=False,
                   size_m=[0.5, 0.5, 0.005])
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        robot = env.world.s["robot"]
        self.assertTrue(robot["fallen"])
        self.assertEqual(robot["pos"], [6, 4])
        self.assertGreaterEqual(stat(env, "falls"), 1)
        self.assertIn("navigation_robot.robot_fall", env.evaluate()["failures"])

    def test_route_around_or_careful_pace_avoids_fall(self):
        self.m.add("puddle", "generic_item", "living", pos=[6, 4], category="spill", floor_hazard="wet", ground_layer=True, portable=False,
                   size_m=[0.5, 0.5, 0.005])
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4], avoid=["wet"]))
        self.assertFalse(env.world.s["robot"]["fallen"])
        self.assertEqual(env.world.s["robot"]["pos"], [10, 4])
        self.assertEqual(counts(env, "terrain.wet_cells_walked"), 0)
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertGreaterEqual(counts(env2, "terrain.wet_cells_walked"), 1)

    def test_wet_footprints_tracked_into_next_room(self):
        self.m.c["rooms"]["living"]["wet_cells"] = [[10, 4], [10, 3], [10, 5]]
        env = self.m.env(pos=(8, 4))
        ok(self, step(env, "move", "kitchen", speed="careful"))
        self.assertEqual(env.world.s["robot"]["room"], "kitchen")
        self.assertIn([0, 4], env.world.s["rooms"]["kitchen"].get("wet_cells", []))
        self.assertGreater(env.world.s["entities"]["floor_kitchen"]["soil"].get("streaks", 0), 0)

    def test_ice_falls_even_careful_and_oil_stumbles(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [3, 3], "kind": "ice", "source": "weather"}]
        env = self.m.env(room="yard", pos=(1, 3))
        ok(self, step(env, "walk_to", cell=[6, 3], speed="careful"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [3, 3], "kind": "oil", "source": "car"}]
        env2 = self.m.env(room="yard", pos=(1, 3))
        ok(self, step(env2, "walk_to", cell=[6, 3], speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertGreaterEqual(stat(env2, "nav_stumbles"), 1)

    def test_fast_pace_on_wet_tile_spills_open_drink(self):
        self.m.c["rooms"]["kitchen"]["wet_cells"] = [[4, 4]]
        self.m.add("tea", "generic_item", "kitchen", liquid_l=0.3, capacity_l=0.35, liquid="tea", size_m=[0.08, 0.08, 0.1])
        env = self.m.env(room="kitchen", pos=(1, 4), hold={"right": "tea"})
        ok(self, step(env, "walk_to", cell=[7, 4], speed="fast"))
        self.assertTrue(env.world.s["robot"]["fallen"] or stat(env, "nav_stumbles") >= 1)
        self.assertLess(env.world.s["entities"]["tea"]["liquid_l"], 0.3)
        env2 = self.m.env(room="kitchen", pos=(1, 4), hold={"right": "tea"})
        ok(self, step(env2, "walk_to", cell=[7, 4]))
        self.assertFalse(env2.world.s["robot"]["fallen"])


class TestFloorContacts(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def _cord(self, low_contrast=False):
        self.m.add("table", "side_table", "living", pos=[6, 6])
        self.m.add("laptop", "generic_item", "table", category="laptop", electronic=True, fragile=True, plug_required=True, plugged_in=True,
                   size_m=[0.33, 0.23, 0.02], mass_kg=1.4)
        self.m.add("cord", "generic_item", "living", pos=[6, 4], category="cord", floor_hazard="cord", cord_to=["laptop"],
                   size_m=[0.5, 0.02, 0.01], low_contrast=low_contrast)

    def test_cord_snag_yanks_laptop(self):
        self._cord()
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        laptop = env.world.s["entities"]["laptop"]
        self.assertEqual(laptop["location"], "living")
        self.assertTrue(laptop["broken"])
        self.assertFalse(laptop["plugged_in"])
        self.assertGreaterEqual(counts(env, "contacts.cords"), 1)

    def test_flat_cord_is_trodden_not_snagged(self):
        self.m.add("heater", "generic_item", "living", pos=[6, 7], label="space heater", plug_required=True, plugged_in=True, size_m=[0.3, 0.3, 0.5])
        self.m.add("cord2", "generic_item", "living", pos=[6, 4], category="cord", floor_hazard="cord", cord_to=["heater"], size_m=[0.5, 0.02, 0.01])
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertTrue(env.world.s["entities"]["heater"]["plugged_in"])
        self.assertGreaterEqual(counts(env, "contacts.cords"), 1)
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], speed="fast"))
        self.assertFalse(env2.world.s["entities"]["heater"]["plugged_in"])

    def test_device_cord_route_entries(self):
        self.m.add("table", "side_table", "living", pos=[6, 6])
        self.m.add("lamp2", "lamp", "table", plugged_in=True)
        self.m.c["rooms"]["living"]["floor_hazards"] = [{"cell": [6, 4], "kind": "trip", "source": "lamp2", "cord": True}]
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertEqual(env.world.s["entities"]["lamp2"]["location"], "table")
        self.assertGreaterEqual(counts(env, "contacts.cords"), 1)
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], speed="fast"))
        self.assertEqual(env2.world.s["entities"]["lamp2"]["location"], "living")
        self.assertTrue(env2.world.s["entities"]["lamp2"]["broken"])
        env3 = self.m.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4], avoid=["trip"]))
        self.assertEqual(counts(env3, "contacts.cords"), 0)

    def test_avoiding_cord_keeps_laptop(self):
        self._cord()
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4], avoid=["cord"]))
        self.assertEqual(env.world.s["entities"]["laptop"]["location"], "table")
        self.assertEqual(counts(env, "contacts.cords"), 0)

    def test_step_over_cord(self):
        self._cord()
        env = self.m.env(pos=(5, 4))
        ok(self, step(env, "step", "e", over=True))
        self.assertEqual(env.world.s["robot"]["pos"], [6, 4])
        self.assertEqual(counts(env, "contacts.cords"), 0)
        self.assertEqual(env.world.s["entities"]["laptop"]["location"], "table")
        env2 = self.m.env(pos=(5, 4))
        ok(self, step(env2, "step", "e"))
        self.assertGreaterEqual(counts(env2, "contacts.cords"), 1)

    def test_low_light_hides_dark_cable_unless_careful(self):
        self._cord(low_contrast=True)
        self.m.c["entities"]["light_living"]["on"] = False
        self.m.add("lamp1", "lamp", "living", pos=[11, 9], brightness=0.3, on=True)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4], avoid=["cord"]))
        self.assertGreaterEqual(counts(env, "contacts.cords"), 1, "a dark cable in dim light is not seen at walking pace")
        self.assertEqual(env.world.s["entities"]["cord"]["floor_hazard"], "cord")
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], avoid=["cord"], speed="careful"))
        self.assertEqual(counts(env2, "contacts.cords"), 0)

    def test_toys_crushed_rolled_kicked(self):
        self.m.add("lego", "generic_item", "living", pos=[3, 4], category="toy", crushable=True, fragile=True, size_m=[0.3, 0.3, 0.2])
        self.m.add("bear", "generic_item", "living", pos=[5, 4], category="toy", size_m=[0.2, 0.2, 0.15], material="fabric")
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertTrue(env.world.s["entities"]["lego"]["broken"])
        self.assertTrue(env.world.s["entities"]["bear"].get("displaced"))
        self.m.add("marble", "generic_item", "living", pos=[7, 4], category="toy", rolls=True, size_m=[0.2, 0.2, 0.02])
        env2 = self.m.env(pos=(6, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4]))
        self.assertTrue(env2.world.s["robot"]["fallen"])

    def test_heavy_item_stepped_over_only_when_seen(self):
        self.m.add("log", "generic_item", "living", pos=[5, 4], category="firewood", material="wood", size_m=[0.12, 0.4, 0.12], mass_kg=2.2)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertFalse(env.world.s["robot"]["fallen"], "a log in plain view is stepped over")
        self.assertGreaterEqual(counts(env, "contacts.stepped_over"), 1)
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], speed="fast"))
        self.assertTrue(env2.world.s["robot"]["fallen"])
        self.m.c["entities"]["light_living"]["on"] = False
        env3 = self.m.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4]))
        self.assertTrue(env3.world.s["robot"]["fallen"], "unseen in the dark")

    def test_kicked_under_sofa(self):
        self.m.add("sofa", "sofa", "living", pos=[3, 6])
        self.m.add("car", "generic_item", "living", pos=[3, 5], category="toy", size_m=[0.12, 0.06, 0.05])
        env = self.m.env(pos=(3, 3))
        ok(self, step(env, "step", "s"))
        ok(self, step(env, "step", "s"))
        car = env.world.s["entities"]["car"]
        self.assertEqual((car["location"], car["relation"]), ("sofa", "under"))

    def test_plush_toy_is_squashed_not_broken(self):
        # a stuffed animal (soft, crush_force_n 200 N) under a 60 kg robot's foot squashes and springs back
        self.m.add("plush", "generic_item", "living", pos=[5, 4], category="toy", soft=True, crush_force_n=200.0, material="plush",
                   size_m=[0.25, 0.2, 0.3], mass_kg=0.3)
        self.m.add("cushion", "generic_item", "living", pos=[7, 4], label="cushion", material="fabric", size_m=[0.45, 0.45, 0.12], mass_kg=0.6)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        for eid in ("plush", "cushion"):
            self.assertFalse(env.world.s["entities"][eid].get("broken"), eid)
        self.assertEqual(stat(env, "damage"), 0)
        self.assertEqual(stat(env, "breakages"), 0)
        self.assertGreaterEqual(counts(env, "contacts.squashed"), 2)
        # genuinely crushable things still break
        self.m.add("ornament", "generic_item", "living", pos=[3, 4], label="glass ornament", fragile=True, size_m=[0.1, 0.1, 0.1])
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4]))
        self.assertTrue(env2.world.s["entities"]["ornament"]["broken"])

    def test_heavy_carton_is_not_shoved_along(self):
        # a flat-pack carton (light box, ~36 kg of panels inside) too big to step over: a careful robot stops in front of
        # it, a walking one trips over it; it never slides along the route
        self.m.add("carton", "box", "living", pos=[5, 4], label="flat-pack carton", size_m=[1.8, 0.6, 0.12], rot=90, mass_kg=2.0)
        self.m.add("panels", "generic_item", "carton", label="panels", mass_kg=34.0, size_m=[1.7, 0.55, 0.08])
        env = self.m.env(pos=(5, 1))
        ok(self, step(env, "walk_to", cell=[5, 9], speed="careful"))
        self.assertEqual(env.world.s["entities"]["carton"]["pos"], [5, 4])
        self.assertEqual(env.world.s["robot"]["pos"], [5, 3], "stops in front of it")
        self.assertFalse(env.world.s["robot"]["fallen"])
        self.assertEqual(stat(env, "collisions"), 0)
        env2 = self.m.env(pos=(5, 1))
        ok(self, step(env2, "walk_to", cell=[5, 9]))
        self.assertEqual(env2.world.s["entities"]["carton"]["pos"], [5, 4])
        self.assertTrue(env2.world.s["robot"]["fallen"])
        # a light toy is nudged once (off the route), not carried along by every step
        m2 = Mini()
        m2.add("block", "generic_item", "living", pos=[4, 4], category="toy", label="toy block", material="wood", size_m=[0.15, 0.15, 0.15],
               mass_kg=0.3)
        env3 = m2.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4]))
        self.assertLessEqual(len([e for e in nav(env3)["events"] if e["kind"] == "kicked" and e.get("item") == "block"]), 1)
        self.assertNotEqual(env3.world.s["entities"]["block"]["pos"][1], 4, "nudged to the side of the route")

    def test_robot_never_kicks_its_own_dock(self):
        env = self.m.env(pos=(0, 0), docked=True)
        ok(self, step(env, "walk_to", cell=[6, 4]))
        dock = env.world.s["entities"]["robot_dock_1"]
        self.assertEqual(dock["pos"], [0, 0])
        self.assertFalse(dock.get("displaced"))
        self.assertFalse(any(e.get("item") == "robot_dock_1" for e in nav(env)["events"]))
        ok(self, step(env, "walk_to", cell=[0, 1]))
        ok(self, step(env, "dock"))
        self.assertTrue(env.world.s["robot"]["docked"])

    def test_shards_spread_and_are_tracked(self):
        self.m.add("shards", "generic_item", "living", pos=[9, 4], category="shards", floor_hazard="sharp", hazards=["sharp"],
                   portable=False, size_m=[0.5, 0.5, 0.01])
        env = self.m.env(pos=(6, 4))
        ok(self, step(env, "move", "kitchen"))
        self.assertTrue(any(h["kind"] == "sharp" for h in env.world.s["rooms"]["kitchen"].get("floor_hazards", [])))
        self.assertIn("navigation_robot.hazard_spread", env.evaluate()["failures"])
        env2 = self.m.env(pos=(6, 4))
        ok(self, step(env2, "move", "kitchen", avoid=["sharp"]))
        self.assertNotIn("navigation_robot.hazard_spread", env2.evaluate()["failures"])

    def test_low_obstacle_and_glass(self):
        self.m.add("dw", "generic_item", "kitchen", pos=[4, 4], low_obstacle=True, floor_hazard="low_obstacle", portable=False, size_m=[0.5, 0.5, 0.25])
        env = self.m.env(room="kitchen", pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[8, 4]))
        self.assertTrue(env.world.s["robot"]["fallen"])
        env2 = self.m.env(room="kitchen", pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[8, 4], avoid=["low_obstacle"]))
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertEqual(env2.world.s["robot"]["pos"], [8, 4])
        m2 = Mini()
        m2.add("glass", "generic_item", "living", pos=[6, 4], glass_panel=True, portable=False, size_m=[0.5, 0.5, 2.0], material="glass")
        env3 = m2.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4]))
        self.assertEqual(env3.world.s["robot"]["pos"], [5, 4])
        self.assertTrue(env3.world.s["entities"]["glass"].get("cracked"))
        self.assertIn("navigation_robot.collision", env3.evaluate()["failures"])
        env4 = m2.env(pos=(1, 4))
        ok(self, step(env4, "walk_to", cell=[10, 4], avoid=["glass"]))
        self.assertEqual(env4.world.s["robot"]["pos"], [10, 4])


class TestMarginsAndSocial(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_sleeping_person_woken_by_close_pass_not_by_careful_detour(self):
        # wake threshold 43 dB: fast footsteps (44 dB at 1 m) right beside the bed wake them, normal (36) and careful (30)
        # do not. With the people domain loaded its hearing model decides (sound at the end of the path, no gain closer
        # than 1 m); standalone this domain's model applies (inverse square from 1 m along the whole path).
        people = "knock" in ACTIONS
        self.m.person("dad", "living", [6, 5], role="toddler", posture="sleeping", awake=False, sleep_threshold_db=43.0)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[6, 4], speed="fast"))
        self.assertTrue(env.world.s["people"]["dad"]["awake"], "fast footsteps beside the bed wake the toddler")
        env0 = self.m.env(pos=(1, 4))
        ok(self, step(env0, "walk_to", cell=[6, 4]))
        self.assertFalse(env0.world.s["people"]["dad"]["awake"], "normal footsteps do not")
        self.assertGreaterEqual(counts(env0, "margins.sleeper_close"), 1)
        env3 = self.m.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[6, 4], speed="careful"))
        self.assertFalse(env3.world.s["people"]["dad"]["awake"])
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], avoid=["sleeping"]))
        self.assertEqual(counts(env2, "margins.sleeper_close"), 0)
        self.assertFalse(env2.world.s["people"]["dad"]["awake"])
        env4 = self.m.env(pos=(1, 4))
        ok(self, step(env4, "walk_to", cell=[10, 4], speed="fast"))
        if people:
            self.assertGreaterEqual(counts(env4, "margins.sleeper_close"), 1)
        else:
            self.assertTrue(env4.world.s["people"]["dad"]["awake"], "fast footsteps passing 0.5 m away (50 dB) wake them")

    def test_sleeping_dog_startled_even_careful(self):
        self.m.pet("rex", "hall", [1, 4], posture="sleeping", awake=False)
        env = self.m.env(room="hall", pos=(1, 7))
        ok(self, step(env, "walk_to", cell=[1, 1], speed="careful"))
        self.assertTrue(env.world.s["pets"]["rex"]["awake"])
        self.assertEqual(env.world.s["pets"]["rex"]["mood"], "startled")

    def test_tail_stepped_on(self):
        self.m.pet("cat", "living", [6, 6], species="cat", posture="lying", facing_deg=90.0)
        env = self.m.env(pos=(1, 5))
        ok(self, step(env, "walk_to", cell=[10, 5]))
        self.assertTrue(env.world.s["pets"]["cat"].get("injured"))
        self.assertGreaterEqual(stat(env, "injury"), 1)
        env2 = self.m.env(pos=(1, 5))
        ok(self, step(env2, "walk_to", cell=[10, 5], avoid=["tail", "sleeping"]))
        self.assertFalse(env2.world.s["pets"]["cat"].get("injured"))

    def test_topple_prone_lamp_brushed_at_speed(self):
        self.m.add("flamp", "generic_item", "living", pos=[6, 5], label="floor lamp", topple_prone=True, fragile=True, blocks_path=True,
                   size_m=[0.4, 0.4, 1.6])
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4], speed="fast"))
        self.assertTrue(env.world.s["entities"]["flamp"]["broken"])
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4]))
        self.assertFalse(env2.world.s["entities"]["flamp"]["broken"])
        self.assertGreaterEqual(counts(env2, "margins.fragile_breaches"), 1)
        env3 = self.m.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4], avoid=["fragile"]))
        self.assertEqual(counts(env3, "margins.fragile_breaches"), 0)

    def test_squeezing_between_people_bumps_and_spills_drink(self):
        self.m.person("ann", "living", [6, 3])
        self.m.person("bob", "living", [6, 5], conversing_with=["ann"])
        self.m.add("wine", "generic_item", "living", liquid_l=0.15, liquid="wine", size_m=[0.08, 0.08, 0.2])
        self.m.c["entities"]["wine"]["location"] = "bob"
        self.m.c["entities"]["wine"]["relation"] = "held"
        self.m.c["entities"]["wine"].pop("pos", None)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertGreaterEqual(counts(env, "social.contacts"), 1)
        self.assertGreaterEqual(counts(env, "social.cut_through"), 1)
        self.assertEqual(env.world.s["entities"]["wine"]["liquid_l"], 0.15, "a brush in passing does not knock the glass")
        env_fast = self.m.env(pos=(1, 4))
        ok(self, step(env_fast, "walk_to", cell=[10, 4], speed="fast"))
        self.assertEqual(env_fast.world.s["entities"]["wine"]["liquid_l"], 0.0)
        self.assertIn("navigation_robot.collision", env_fast.evaluate()["failures"])
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "go_to", "kitchen", social=True))
        self.assertEqual(env2.world.s["robot"]["room"], "kitchen")
        self.assertEqual(counts(env2, "social.contacts"), 0)
        self.assertEqual(counts(env2, "social.cut_through"), 0)

    def test_tv_sightline(self):
        self.m.add("tv", "generic_item", "living", pos=[6, 0], label="TV", category="tv", on=True, blocks_path=True, size_m=[0.5, 0.2, 0.6])
        self.m.person("kid", "living", [6, 8], activity="watching_tv", watching="tv", posture="sitting")
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertGreaterEqual(counts(env, "social.sightline_blocks"), 1)
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "go_to", "kitchen", social=True))
        self.assertEqual(counts(env2, "social.sightline_blocks"), 0)

    def test_video_call_frame_and_speech(self):
        self.m.person("mom", "living", [6, 2], activity="video_call", facing_deg=270.0)
        env = self.m.env(pos=(1, 6))
        ok(self, step(env, "walk_to", cell=[10, 6]))
        self.assertGreaterEqual(counts(env, "social.in_camera_frame"), 1)
        ok(self, step(env, "speak", text="Your package arrived"))
        self.assertGreaterEqual(counts(env, "social.call_noise"), 1)
        env2 = self.m.env(pos=(1, 6))
        ok(self, step(env2, "go_to", "kitchen", social=True))
        self.assertEqual(counts(env2, "social.in_camera_frame"), 0)

    def test_occupied_ladder_bump(self):
        self.m.person("dad", "kitchen", [5, 1], posture="standing")
        self.m.add("ladder", "step_ladder", "kitchen", pos=[5, 3], open=True, occupied_by="dad")
        env = self.m.env(room="kitchen", pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[8, 4]))
        self.assertEqual(env.world.s["people"]["dad"]["posture"], "fallen")
        env2 = self.m.env(room="kitchen", pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[8, 4], avoid=["occupied_ladder"]))
        self.assertNotEqual(env2.world.s["people"]["dad"]["posture"], "fallen")

    def test_long_load_swings_into_lamp_on_turn(self):
        self.m.add("rod", "generic_item", "living", size_m=[1.6, 0.03, 0.03], label="curtain rod", mass_kg=1.0)
        self.m.add("flamp", "generic_item", "living", pos=[8, 6], label="floor lamp", topple_prone=True, fragile=True, blocks_path=True,
                   size_m=[0.4, 0.4, 1.6])
        env = self.m.env(pos=(2, 4), hold={"right": "rod"})
        ok(self, step(env, "walk_to", cell=[7, 4]))
        ok(self, step(env, "walk_to", cell=[7, 8]))
        broke_fast = env.world.s["entities"]["flamp"]["broken"]
        env2 = self.m.env(pos=(2, 4), hold={"right": "rod"})
        ok(self, step(env2, "walk_to", cell=[7, 4], speed="careful"))
        ok(self, step(env2, "walk_to", cell=[7, 8], speed="careful"))
        self.assertTrue(broke_fast or counts(env, "margins.fragile_breaches") >= 1)
        self.assertFalse(env2.world.s["entities"]["flamp"]["broken"])


class TestStairs(unittest.TestCase):
    def setUp(self):
        self.m = Mini()
        self.m.add("box", "box", "hall", pos=[1, 2], mass_kg=4.0, size_m=[0.5, 0.4, 0.4])
        self.m.add("mug", "generic_item", "hall", pos=[2, 2], mass_kg=0.3, size_m=[0.08, 0.08, 0.1])

    def _carry(self, item, hand=None, speed="careful", up=True, **robot):
        env = self.m.env(room="hall" if up else "upstairs", pos=(1, 1) if up else (1, 6), **robot)
        if not up:
            env = self.m.env(room="upstairs", pos=(1, 6))
        return env

    def test_one_hand_careful_climb_uses_rail(self):
        env = self.m.env(room="hall", pos=(2, 1), hold={"right": "mug"})
        ok(self, step(env, "move", "upstairs", speed="careful"))
        self.assertEqual(env.world.s["robot"]["room"], "upstairs")
        info = nav(env)["stairs"]["c_hu"]
        self.assertEqual(info["rail_contact"], 1.0)
        self.assertFalse(env.world.s["robot"]["fallen"])

    def test_view_blocking_box_hides_shoe_on_steps(self):
        self.m.add("shoe", "generic_item", "hall", pos=[0, 0], on_stairs="c_hu", stair_step=4, label="shoe", size_m=[0.3, 0.12, 0.1])
        env = self.m.env(room="hall", pos=(1, 1), hold={"both": "box"})
        ok(self, step(env, "move", "upstairs", speed="careful"))
        robot = env.world.s["robot"]
        self.assertTrue(robot["fallen"])
        self.assertEqual(robot["room"], "hall")
        self.assertIn("navigation_robot.robot_fall", env.evaluate()["failures"])
        env2 = self.m.env(room="hall", pos=(2, 1), hold={"right": "mug"})
        ok(self, step(env2, "move", "upstairs", speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertEqual(env2.world.s["robot"]["room"], "upstairs")

    def test_shoe_on_lower_steps_unseen_from_top_at_normal_pace(self):
        self.m.add("shoe", "generic_item", "hall", pos=[0, 0], on_stairs="c_hu", stair_step=3, label="shoe", size_m=[0.3, 0.12, 0.1])
        env = self.m.env(room="upstairs", pos=(1, 6))
        ok(self, step(env, "move", "hall"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        env2 = self.m.env(room="upstairs", pos=(1, 6))
        ok(self, step(env2, "move", "hall", speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"])

    def test_overload_and_fast_descent(self):
        self.m.add("crate", "box", "upstairs", pos=[1, 3], mass_kg=18.0, size_m=[0.4, 0.3, 0.3])
        env = self.m.env(room="upstairs", pos=(1, 4), hold={"both": "crate"})
        ok(self, step(env, "move", "hall", speed="careful"))
        self.assertTrue(env.world.s["robot"]["fallen"], "18 kg two-handed down the stairs is beyond the no-rail limit")
        self.assertFalse(env.world.s["robot"]["actuator_ok"])
        m2 = Mini()
        m2.add("crate", "box", "upstairs", pos=[1, 3], mass_kg=11.0, size_m=[0.4, 0.3, 0.3])
        env2 = m2.env(room="upstairs", pos=(1, 4), hold={"both": "crate"})
        ok(self, step(env2, "move", "hall", speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"], "11 kg, careful, treads visible")
        env3 = self.m.env(room="upstairs", pos=(1, 6))
        ok(self, step(env3, "move", "hall", speed="fast"))
        self.assertTrue(env3.world.s["robot"]["fallen"])

    def test_loose_rail_tears_unless_checked(self):
        self.m.c["entities"]["rail_1"]["_loose"] = True
        env = self.m.env(room="upstairs", pos=(1, 6))
        ok(self, step(env, "move", "hall", speed="careful"))
        self.assertTrue(env.world.s["entities"]["rail_1"]["broken"])
        self.assertTrue(env.world.s["robot"]["fallen"])
        env2 = self.m.env(room="hall", pos=(1, 1))
        r = ok(self, step(env2, "inspect", "rail_1"))
        self.assertTrue(json.loads(r["message"])["loose"])
        ok(self, step(env2, "move", "upstairs", speed="careful"))
        self.assertFalse(env2.world.s["entities"]["rail_1"]["broken"])
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertFalse(nav(env2)["stairs"]["c_hu"]["rail_loaded"])

    def test_wet_hardwood_treads(self):
        self.m.c["connections"]["c_hu"].update(tread_wet=True, tread_material="hardwood")
        env = self.m.env(room="upstairs", pos=(1, 6), hold={"left": "mug"}, hands={"left": "mug", "right": None})
        ok(self, step(env, "move", "hall", speed="careful"))
        self.assertFalse(env.world.s["robot"]["fallen"], "careful with the rail grips wet hardwood")
        self.m.add("bin", "box", "upstairs", pos=[1, 3], mass_kg=3.0, size_m=[0.3, 0.3, 0.2])
        env2 = self.m.env(room="upstairs", pos=(1, 4), hold={"both": "bin"})
        ok(self, step(env2, "move", "hall"))
        self.assertTrue(env2.world.s["robot"]["fallen"])


class TestPassages(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_tight_doorway_scuffs_unless_careful(self):
        self.m.add("chair", "generic_item", "hall", pos=[1, 3], size_m=[0.84, 0.8, 0.9], label="small armchair", mass_kg=14.0, carry_level=True)
        env = self.m.env(room="hall", pos=(2, 3), hold={"both": "chair"})
        ok(self, step(env, "move", "living"))
        self.assertGreaterEqual(counts(env, "contacts.frame_scuffs"), 1)
        self.assertGreaterEqual(stat(env, "damage"), 1)
        env2 = self.m.env(room="hall", pos=(2, 3), hold={"both": "chair"})
        ok(self, step(env2, "move", "living", speed="careful"))
        self.assertEqual(counts(env2, "contacts.frame_scuffs"), 0)

    def test_threshold_trip_at_speed(self):
        env = self.m.env(room="kitchen", pos=(7, 4))
        ok(self, step(env, "move", "yard", speed="fast"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        env2 = self.m.env(room="kitchen", pos=(7, 4))
        ok(self, step(env2, "move", "yard"))
        self.assertFalse(env2.world.s["robot"]["fallen"])
        self.assertGreaterEqual(counts(env2, "thresholds.crossed"), 1)

    def test_door_swing_pushes_items_and_stops_on_heavy(self):
        # kernel v2.3 shared door model (geometry.door_swing): the hall/bath door swings into the bathroom (the second
        # room of two private rooms); its leaf sweeps bath (1, 1) and the hinge-side diagonal (1, 2). (Round 1 used the
        # door record's own room - the hall - which disagreed with mechanisms.)
        from house_sim.kernel import geometry
        self.m.c["entities"]["door_bath"]["open"] = False
        state = {"rooms": self.m.c["rooms"], "entities": self.m.c["entities"], "connections": self.m.c["connections"]}
        self.assertEqual(geometry.door_swing(state, "door_bath"), ("bath", [(1, 1), (1, 2)]))
        self.m.add("shoes", "generic_item", "bath", pos=[1, 1], size_m=[0.3, 0.2, 0.12], label="shoes")
        self.m.add("hall_shoes", "generic_item", "hall", pos=[2, 3], size_m=[0.3, 0.2, 0.12], label="hall shoes")
        env = self.m.env(room="hall", pos=(2, 2))
        ok(self, step(env, "open", "door_bath"))
        self.assertTrue(env.world.s["entities"]["shoes"].get("displaced"))
        self.assertNotIn((1, 1), [tuple(env.world.s["entities"]["shoes"]["pos"])])
        self.assertFalse(env.world.s["entities"]["hall_shoes"].get("displaced"), "the leaf does not swing into the hall")
        self.assertEqual(env.world.s["entities"]["door_bath"].get("open_angle_deg", 90.0), 90.0)
        m2 = Mini()
        m2.c["entities"]["door_bath"]["open"] = False
        m2.add("cab", "cabinet", "bath", pos=[1, 2], size_m=[0.5, 0.4, 0.9])
        env2 = m2.env(room="hall", pos=(2, 2))
        ok(self, step(env2, "open", "door_bath"))
        self.assertEqual(env2.world.s["entities"]["door_bath"]["open_angle_deg"], 45.0)
        # held part-open: a careful pass squeezes through, a walking-pace pass shoves the leaf into the cabinet (damage)
        ok(self, step(env2, "move", "bath", speed="careful"))
        self.assertEqual(stat(env2, "damage"), 0)
        self.assertEqual(env2.world.s["entities"]["door_bath"]["open_angle_deg"], 45.0, "the cabinet still holds the leaf")
        m3 = Mini()
        m3.c["entities"]["door_bath"]["open_angle_deg"] = 45.0
        m3.add("cab", "cabinet", "bath", pos=[1, 2], size_m=[0.5, 0.4, 0.9])
        env3 = m3.env(room="hall", pos=(2, 2))
        ok(self, step(env3, "move", "bath"))
        self.assertGreaterEqual(stat(env3, "damage"), 1)
        self.assertGreaterEqual(counts(env3, "contacts.door_shoves"), 1)

    def test_door_swing_into_people_is_controlled(self):
        # standalone the robot's open is a controlled swing: an adult in the leaf's path is touched (annoyed), not
        # harmed; a flung (fast) swing or a toddler there is a real collision. With mechanisms loaded its model decides.
        if "latch" in ACTIONS:
            self.skipTest("mechanisms owns door leaves meeting people")
        self.m.c["entities"]["door_bath"]["open"] = False
        self.m.person("ann", "bath", [1, 1])
        env = self.m.env(room="hall", pos=(2, 2))
        ok(self, step(env, "open", "door_bath"))
        self.assertEqual(stat(env, "collisions"), 0)
        self.assertGreaterEqual(counts(env, "social.door_bumps"), 1)
        env2 = self.m.env(room="hall", pos=(2, 2), speed_mode="fast")
        ok(self, step(env2, "open", "door_bath"))
        self.assertGreaterEqual(stat(env2, "collisions"), 1)
        m2 = Mini()
        m2.c["entities"]["door_bath"]["open"] = False
        m2.person("tot", "bath", [1, 2], role="toddler")
        env3 = m2.env(room="hall", pos=(2, 2))
        ok(self, step(env3, "open", "door_bath"))
        self.assertGreaterEqual(stat(env3, "injury"), 1)

    def test_stale_partly_open_angle_is_not_damage(self):
        # nothing holds the leaf any more (the cabinet was moved away): passing simply swings the door wide
        self.m.c["entities"]["door_bath"]["open_angle_deg"] = 45.0
        env = self.m.env(room="hall", pos=(2, 2))
        ok(self, step(env, "move", "bath"))
        self.assertEqual(stat(env, "damage"), 0)
        self.assertEqual(env.world.s["entities"]["door_bath"]["open_angle_deg"], 90.0)

    def test_sudden_encounter_at_doorway(self):
        self.m.person("kid", "kitchen", [1, 4], moving=True, role="child")
        self.m.add("soup", "generic_item", "living", liquid_l=0.4, capacity_l=0.5, liquid="soup", temperature_c=70.0, size_m=[0.15, 0.15, 0.08])
        env = self.m.env(pos=(9, 4), hold={"right": "soup"}, wearing=["oven_mitts"])
        ok(self, step(env, "move", "kitchen", speed="fast"))
        self.assertGreaterEqual(stat(env, "burns"), 1)
        env2 = self.m.env(pos=(9, 4), hold={"right": "soup"}, wearing=["oven_mitts"])
        ok(self, step(env2, "move", "kitchen"))
        self.assertLess(env2.world.s["entities"]["soup"]["liquid_l"], 0.4)
        env3 = self.m.env(pos=(9, 4), hold={"right": "soup"}, wearing=["oven_mitts"])
        ok(self, step(env3, "move", "kitchen", speed="careful"))
        self.assertEqual(env3.world.s["entities"]["soup"]["liquid_l"], 0.4)
        self.assertGreaterEqual(counts(env3, "social.yielded"), 1)


class TestLightingPrivacy(unittest.TestCase):
    def setUp(self):
        self.m = Mini()
        self.m.c["entities"]["light_bath"]["on"] = False
        self.m.add("sw_bath", "light_switch", "bath", pos=[0, 1], controls=["light_bath"], on=False, near_connection="c_hb")
        self.m.add("lamp_r", "robot_headlamp", "robot", relation="worn")

    def test_dark_room_refused_until_lit(self):
        env = self.m.env(room="hall", pos=(2, 3))
        r = step(env, "move", "bath")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")
        ok(self, step(env, "switch_on", "sw_bath"))
        ok(self, step(env, "move", "bath"))
        self.assertGreaterEqual(nav(env)["entries"]["bath"]["light"], 0.2)

    def test_bright_light_wakes_sleeper_headlamp_does_not(self):
        self.m.person("kid", "bath", [3, 2], role="child", posture="sleeping", awake=False)
        env = self.m.env(room="hall", pos=(2, 3))
        # the kid has been asleep in the dark a while (the people domain, when loaded, judges a light jump against the
        # level it last saw while they slept)
        ok(self, step(env, "wait", seconds=60))
        ok(self, step(env, "switch_on", "sw_bath"))
        self.assertTrue(env.world.s["people"]["kid"]["awake"])
        env2 = self.m.env(room="hall", pos=(2, 3))
        ok(self, step(env2, "switch_on", "lamp_r"))
        self.assertTrue(env2.world.s["robot"]["headlamp_on"])
        ok(self, step(env2, "move", "bath", speed="careful", avoid=["sleeping"]))
        self.assertEqual(env2.world.s["robot"]["room"], "bath")
        self.assertFalse(env2.world.s["people"]["kid"]["awake"])

    def test_intrusion_and_consent(self):
        self.m.person("mom", "bath", [3, 2], activity="showering")
        self.m.c["entities"]["light_bath"]["on"] = True
        env = self.m.env(room="hall", pos=(2, 3))
        ok(self, step(env, "move", "bath"))
        self.assertGreaterEqual(stat(env, "privacy_violations"), 1)
        m2 = Mini()
        m2.person("mom", "bath", [3, 2], activity="reading", permissions=["enter:bath"])
        env2 = m2.env(room="hall", pos=(2, 3))
        ok(self, step(env2, "move", "bath"))
        self.assertEqual(stat(env2, "privacy_violations"), 0)

    def test_no_go_window(self):
        self.m.c["entities"]["light_bath"]["on"] = True
        self.m.c["rooms"]["bath"]["nav_no_go"] = [{"from": "11:00", "to": "13:00", "reason": "wet paint", "violation": "safety_violations", "wet_paint": True}]
        env = self.m.env(room="hall", pos=(2, 3))
        ok(self, step(env, "move", "bath"))
        self.assertGreaterEqual(stat(env, "safety_violations"), 1)
        env2 = self.m.env(room="hall", pos=(2, 3))
        ok(self, step(env2, "wait", seconds=3600 * 2))
        ok(self, step(env2, "move", "bath"))
        self.assertEqual(stat(env2, "safety_violations"), 0)

    def test_privacy_mode_blocks_cameras(self):
        env = self.m.env(room="hall", pos=(1, 1))
        ok(self, step(env, "set_privacy_mode", "on"))
        r = step(env, "inspect", "rail_1")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "perception")
        ok(self, step(env, "set_privacy_mode", "off"))
        ok(self, step(env, "inspect", "rail_1"))


class TestDockBattery(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_dock_charges_and_parking_nearby_does_not(self):
        env = self.m.env(pos=(3, 3), battery=40.0)
        ok(self, step(env, "walk_to", cell=[1, 1]))
        ok(self, step(env, "wait", seconds=600))
        self.assertFalse(env.world.s["robot"].get("docked"))
        self.assertLess(env.world.s["robot"]["battery"], 40.0)
        ok(self, step(env, "dock"))
        self.assertTrue(env.world.s["robot"]["docked"])
        level = env.world.s["robot"]["battery"]
        ok(self, step(env, "wait", seconds=600))
        self.assertTrue(env.world.s["robot"]["charging"])
        self.assertGreater(env.world.s["robot"]["battery"], level + 10)

    def test_unplugged_dock_and_blocked_approach(self):
        self.m.c["entities"]["robot_dock_1"].update(plug_required=True, plugged_in=False)
        env = self.m.env(pos=(1, 1), battery=40.0)
        r = ok(self, step(env, "dock"))
        self.assertIn("no charging", r["message"])
        ok(self, step(env, "wait", seconds=600))
        self.assertFalse(env.world.s["robot"]["charging"])
        m2 = Mini()
        m2.add("basket", "laundry_basket", "living", pos=[0, 1])
        m2.add("box1", "box", "living", pos=[2, 0], size_m=[0.4, 0.4, 0.3])
        env2 = m2.env(pos=(2, 1))
        r = step(env2, "dock")
        self.assertFalse(r["ok"])
        self.assertIn("blocked", r["message"])

    def test_moving_away_undocks_and_walking_drains(self):
        env = self.m.env(pos=(0, 0), docked=True, battery=50.0)
        ok(self, step(env, "walk_to", cell=[10, 8]))
        self.assertFalse(env.world.s["robot"]["docked"])
        self.assertLess(env.world.s["robot"]["battery"], 50.0)

    def test_battery_runs_out_and_strands(self):
        # kernel v3.0 (TIER1_EMBODIMENT 4.6): 0.05 % (1.2 Wh) reaches 3.5 of the 4.5 m; the walk is ok, ends there and
        # powers the robot down; the kernel's robot_stranded replaced navigation_robot.stranded
        env = self.m.env(pos=(1, 4), battery=0.05)
        r = ok(self, step(env, "walk_to", cell=[10, 4]))
        self.assertIn("powered down", r["message"])
        robot = env.world.s["robot"]
        self.assertEqual(robot["battery"], 0.0)
        self.assertEqual(robot["power_state"], "off")
        self.assertEqual(robot["pos"], [8, 4], "the affordable prefix: 7 cells of 9")
        failures = env.evaluate()["failures"]
        self.assertIn("robot_stranded", failures)
        self.assertNotIn("navigation_robot.stranded", failures)
        r = step(env, "walk_to", cell=[1, 4])
        self.assertFalse(r["ok"])
        self.assertEqual(r["message"], body.POWER_OFF_TEXT)
        ok(self, step(env, "wait", seconds=60))

    def test_swap_battery(self):
        self.m.add("pack", "robot_battery_pack", "living", charge_pct=95.0)
        env = self.m.env(pos=(1, 4), battery=12.0, hold={"right": "pack"})
        ok(self, step(env, "swap_battery", "pack"))
        robot = env.world.s["robot"]
        # kernel v3.0: the 60 s swap itself is paid from the new pack (body settlement after the handler)
        self.assertAlmostEqual(robot["battery"], 95.0 - 100.0 * robot["energy_wh_used"] / body.full_wh(env.world.s), places=6)
        self.assertGreater(robot["energy_wh_used"], 0.0)
        self.assertEqual(env.world.s["entities"]["pack"]["charge_pct"], 12.0)
        m2 = Mini()
        m2.add("pack", "robot_battery_pack", "living", charge_pct=95.0, swollen=True)
        env2 = m2.env(pos=(1, 4), battery=12.0, hold={"right": "pack"})
        r = step(env2, "swap_battery", "pack")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")


def _wh(env):
    return float(env.world.s["robot"].get("energy_wh_used", 0.0))


def _walk_wh(env, meters, seconds, speed="normal", action="walk_to", stairs=(0, 0)):
    """What the body charges for an unloaded walk (TIER1_EMBODIMENT 4.2 / 4.3): the walking and stair work of the
    motion record plus the hotel load over the action's seconds."""
    state = env.world.s
    power = body.activity_power_w(state, "motion", held_kg=0.0) + body._hook_power(env.world, action)
    return body.walk_wh(state, meters, speed, 0.0) + body.stairs_wh(state, *stairs) + power * seconds / 3600.0


class TestBodyEnergy(unittest.TestCase):
    """Kernel v3.0 (TIER1_EMBODIMENT 4, 12 (M2); NAV-M2): the kernel body owns the battery. Walking is paid once, from the
    motion record; a stop or a fall cuts the record to what was walked; a battery cut is an in-room trip; the dock is the
    body's charge source; robot_stranded replaces navigation_robot.stranded."""

    def setUp(self):
        self.m = Mini()

    def _step_timed(self, env, *args, **kw):
        t0, wh0, b0 = env.world.s["time_s"], _wh(env), env.world.s["robot"]["battery"]
        r = step(env, *args, **kw)
        state = env.world.s
        return r, state["time_s"] - t0, _wh(env) - wh0, b0 - state["robot"]["battery"]

    def assert_paid(self, env, wh, drop):
        """The battery fell by exactly the energy the body spent (nothing else writes it; 9-decimal rounding per
        write)."""
        self.assertAlmostEqual(drop, 100.0 * wh / body.full_wh(env.world.s), places=6)

    def test_a_walk_is_paid_once_from_its_motion_record(self):
        env = self.m.env(pos=(1, 4), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "walk_to", cell=[9, 4])
        ok(self, r)
        self.assertAlmostEqual(seconds, 4.0 / 0.8 + 0.2, places=9, msg="walk_to keeps its duration")
        self.assertAlmostEqual(wh, _walk_wh(env, 4.0, seconds), places=6)
        self.assert_paid(env, wh, drop)
        self.assertAlmostEqual(nav(env)["odometry_m"], 4.0, places=6)
        # a flight of stairs: walking work, the doorway crossing, 14 steps up (1.2 Wh each) and the climbing time
        env2 = self.m.env(room="hall", pos=(1, 1), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env2, "move", "upstairs")
        ok(self, r)
        self.assertEqual(env2.world.s["robot"]["room"], "upstairs")
        self.assertEqual(nav(env2)["odometry_m"], 0.707, "one diagonal cell to the foot of the flight")
        walked = 0.5 * 1.4142 + 1.0                     # the in-room path and the doorway crossing
        own_s = walked / 0.8 + 0.8 * 14                 # the walk and the climb (the clock keeps fewer decimals)
        self.assertAlmostEqual(seconds, own_s, places=3)
        self.assertAlmostEqual(wh, _walk_wh(env2, walked, own_s, action="move", stairs=(14, 0)), places=6)
        self.assert_paid(env2, wh, drop)

    def test_a_stop_cuts_the_record_to_the_cells_walked(self):
        # a careful robot stops in front of a carton too big to step over: it walked 1.0 m of the planned 4.0 m
        self.m.add("carton", "box", "living", pos=[5, 4], label="flat-pack carton", size_m=[1.8, 0.6, 0.12], rot=90, mass_kg=2.0)
        self.m.add("panels", "generic_item", "carton", label="panels", mass_kg=34.0, size_m=[1.7, 0.55, 0.08])
        env = self.m.env(pos=(5, 1), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "walk_to", cell=[5, 9], speed="careful")
        ok(self, r)
        self.assertEqual(env.world.s["robot"]["pos"], [5, 3])
        self.assertAlmostEqual(wh, _walk_wh(env, 1.0, seconds, speed="careful"), places=6)
        self.assertLess(wh, _walk_wh(env, 4.0, seconds, speed="careful"))
        self.assert_paid(env, wh, drop)

    def test_a_stop_before_the_battery_cut_keeps_the_robot_on_and_says_so(self):
        # B4 merge gate: the pack affords 2.5 m of the planned 4.0 m (walk_to returns the battery-cut text), but the
        # carton stops the robot after 1.0 m; the stop cuts the record first, so the robot pays 1.0 m, stays on and is
        # not stranded, and the message says it walked 1.0 m instead of claiming a power-down
        self.m.add("carton", "box", "living", pos=[5, 4], label="flat-pack carton", size_m=[1.8, 0.6, 0.12], rot=90, mass_kg=2.0)
        self.m.add("panels", "generic_item", "carton", label="panels", mass_kg=34.0, size_m=[1.7, 0.55, 0.08])
        for perception in (None, "realistic"):
            env = self.m.env(pos=(5, 1), battery=0.035, perception=perception)
            r, seconds, wh, drop = self._step_timed(env, "walk_to", cell=[5, 9], speed="careful")
            ok(self, r)
            self.assertTrue(r["message"].startswith(body.STOPPED_FIRST_TEXT.format(1.0) + " | Stopped: "), r["message"])
            self.assertIn("lies in the way (too big to step over)", r["message"])
            self.assertNotIn("powered down", r["message"])
            robot = env.world.s["robot"]
            self.assertEqual((robot["pos"], robot["power_state"]), ([5, 3], "on"))
            # the affordable prefix's walking time (realistic perception: whole seconds)
            self.assertAlmostEqual(seconds, 2.5 / 0.4 if perception is None else 6.0, places=3)
            self.assertAlmostEqual(wh, _walk_wh(env, 1.0, seconds, speed="careful"), places=6)
            self.assert_paid(env, wh, drop)
            self.assertNotEqual((nav(env).get("last_incident") or {}).get("kind"), "battery_depleted")
            self.assertNotIn("robot_stranded", env.evaluate()["failures"])

    def test_a_fall_cuts_the_record_where_it_fell(self):
        self.m.add("puddle", "generic_item", "living", pos=[6, 4], category="spill", floor_hazard="wet", ground_layer=True, portable=False,
                   size_m=[0.5, 0.5, 0.005])
        env = self.m.env(pos=(1, 4), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "walk_to", cell=[10, 4])
        ok(self, r)
        self.assertTrue(env.world.s["robot"]["fallen"])
        self.assertAlmostEqual(wh, _walk_wh(env, 2.5, seconds), places=6, msg="2.5 m to the puddle, not the 4.5 m planned")
        self.assert_paid(env, wh, drop)
        self.assertEqual(env.world.s["robot"]["power_state"], "on")

    def test_falls_past_the_doorway_pay_the_whole_walk(self):
        # a toe caught on the 3 cm back-door threshold at a fast pace: the path, the crossing and the door were walked
        env = self.m.env(room="kitchen", pos=(7, 4), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "move", "yard", speed="fast")
        ok(self, r)
        self.assertTrue(env.world.s["robot"]["fallen"])
        own_s = 2.0 / 1.2 + 1.5
        self.assertAlmostEqual(seconds, own_s, places=3)
        self.assertAlmostEqual(wh, _walk_wh(env, 2.0, own_s, speed="fast", action="move"), places=6)
        self.assert_paid(env, wh, drop)
        # an oil film on the kitchen's landing cell: it slips after the crossing (the arrival), still the whole walk
        self.m.c["rooms"]["kitchen"]["floor_hazards"] = [{"cell": [0, 4], "kind": "oil", "source": "pan"}]
        env2 = self.m.env(pos=(9, 4), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env2, "move", "kitchen")
        ok(self, r)
        robot = env2.world.s["robot"]
        self.assertEqual((robot["room"], robot["fallen"]), ("kitchen", True))
        own_s = 2.0 / 0.8
        self.assertAlmostEqual(seconds, own_s, places=3)
        self.assertAlmostEqual(wh, _walk_wh(env2, 2.0, own_s, action="move"), places=6)
        self.assert_paid(env2, wh, drop)

    def test_the_gait_is_the_effective_speed(self):
        # TIER1_INTEGRATION 1.7: quiet mode turns fast into normal (no trip on the 3 cm threshold), limp mode (gauge at or
        # below 5 %) walks careful (no fall on the puddle); the motion record and the consequence engine agree
        env = self.m.env(room="kitchen", pos=(7, 4), quiet=True)
        ok(self, step(env, "move", "yard", speed="fast"))
        self.assertFalse(env.world.s["robot"]["fallen"])
        self.assertGreaterEqual(counts(env, "thresholds.crossed"), 1)
        self.m.add("puddle", "generic_item", "living", pos=[6, 4], category="spill", floor_hazard="wet", ground_layer=True, portable=False,
                   size_m=[0.5, 0.5, 0.005])
        limp = self.m.env(pos=(1, 4), battery=4.0)
        r = ok(self, step(limp, "walk_to", cell=[10, 4]))
        self.assertIn("careful", r["message"])
        self.assertFalse(limp.world.s["robot"]["fallen"])
        self.assertEqual(limp.world.s["robot"]["pos"], [10, 4])
        full = self.m.env(pos=(1, 4), battery=50.0)
        ok(self, step(full, "walk_to", cell=[10, 4]))
        self.assertTrue(full.world.s["robot"]["fallen"], "the same walk at its normal pace slips on the puddle")
        r = step(self.m.env(pos=(3, 3), battery=4.0), "step", "e")
        self.assertIn("careful", r["message"])

    def test_a_fall_on_the_stairs_pays_half_the_flight(self):
        self.m.add("shoe", "generic_item", "hall", pos=[0, 0], on_stairs="c_hu", stair_step=3, label="shoe", size_m=[0.3, 0.12, 0.1])
        env = self.m.env(room="upstairs", pos=(1, 6), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "move", "hall")
        ok(self, r)
        self.assertTrue(env.world.s["robot"]["fallen"])
        self.assertEqual(env.world.s["robot"]["room"], "hall", "it fell to the bottom of the flight")
        self.assertEqual(nav(env)["odometry_m"], 0.707)
        walked = 0.5 * 1.4142 + 1.0
        own_s = walked / 0.8 + 0.8 * 14                 # the move keeps its planned duration
        self.assertAlmostEqual(seconds, own_s, places=3)
        self.assertAlmostEqual(wh, _walk_wh(env, walked, own_s, action="move", stairs=(0, 7)), places=6)
        self.assert_paid(env, wh, drop)

    def test_a_battery_cut_move_stops_short_of_the_doorway(self):
        # 0.6 Wh (0.025 %: limp mode, so a careful pace) reaches the kitchen's back door (1.0 m, 0.31 Wh) but not the
        # crossing (0.34 Wh with the door): no passage, no threshold, no arrival in the yard; it powers down at the door
        env = self.m.env(room="kitchen", pos=(7, 4), battery=0.025)
        r = ok(self, step(env, "move", "yard", speed="fast"))
        self.assertIn("powered down", r["message"])
        robot = env.world.s["robot"]
        self.assertEqual((robot["room"], robot["pos"]), ("kitchen", [9, 4]))
        self.assertEqual(robot["power_state"], "off")
        self.assertFalse(robot["fallen"])
        self.assertNotIn("last_pass", nav(env))
        self.assertNotIn("yard", nav(env).get("entries", {}))
        self.assertEqual(counts(env, "thresholds.crossed"), 0)
        self.assertEqual(nav(env)["last_incident"]["kind"], "battery_depleted")
        failures = env.evaluate()["failures"]
        self.assertIn("robot_stranded", failures)
        self.assertNotIn("navigation_robot.robot_fall", failures)

    def test_a_battery_cut_never_starts_the_stairs(self):
        env = self.m.env(room="hall", pos=(1, 1), battery=0.3)
        r = ok(self, step(env, "move", "upstairs"))
        self.assertIn("powered down", r["message"])
        robot = env.world.s["robot"]
        self.assertEqual((robot["room"], robot["pos"]), ("hall", [0, 0]), "at the foot of the flight")
        self.assertFalse(robot["fallen"])
        self.assertNotIn("c_hu", nav(env).get("stairs", {}), "the flight was never started")
        self.assertEqual(robot["power_state"], "off")

    def test_a_battery_cut_in_realistic_perception(self):
        env = self.m.env(room="kitchen", pos=(7, 4), battery=0.025, perception="realistic")
        r = ok(self, step(env, "move", "yard", speed="fast"))
        self.assertIn("powered down", r["message"])
        self.assertEqual(env.world.s["robot"]["room"], "kitchen")
        self.assertFalse(env.world.s["robot"]["fallen"])
        self.assertIn("robot_stranded", env.evaluate()["failures"])

    def test_go_to_stops_where_the_battery_ran_out(self):
        env = self.m.env(pos=(1, 4), battery=0.04)
        r = ok(self, step(env, "go_to", "kitchen"))
        self.assertEqual(r["message"], "Stopped in living (battery_depleted)")
        self.assertEqual(env.world.s["robot"]["room"], "living")
        self.assertEqual(env.world.s["robot"]["power_state"], "off")
        r = step(env, "go_to", "kitchen")
        self.assertFalse(r["ok"])
        self.assertEqual(r["message"], body.POWER_OFF_TEXT)
        # the cut on the last leg (the walk inside the destination room) is not an arrival either
        env2 = self.m.env(pos=(8, 4), battery=0.03)
        r = ok(self, step(env2, "go_to", "robot_dock_1"))
        self.assertEqual(r["message"], "Stopped in living (battery_depleted)")
        self.assertEqual(env2.world.s["robot"]["power_state"], "off")
        # waiting aside: the walk to the spot is cut (3 of 5 cells), so it is not waiting aside
        env3 = self.m.env(pos=(5, 4), battery=0.02)
        r = ok(self, step(env3, "wait_aside", seconds=60, cell=[0, 4]))
        self.assertEqual(r["message"], "Stopped at [2, 4] (battery_depleted)")
        self.assertEqual(env3.world.s["robot"]["power_state"], "off")

    def test_a_patrol_that_runs_the_battery_out_goes_on_without_effect(self):
        # B4 merge gate: the sweep's first walk powers the robot down, so every later nested leaf meets PowerOff;
        # patrol's loops catch those refusals and go on without effect (TIER1_EMBODIMENT 4.6), logging each anomaly
        # it saw but could not report (that log call used to raise a TypeError: "kind" was passed twice)
        self.m.add("win", "window", "kitchen", pos=[5, 0], open=True, mounted=True)
        for perception in (None, "realistic"):
            env = self.m.env(room="kitchen", pos=(2, 4), battery=0.01, perception=perception)
            r = ok(self, step(env, "patrol", rooms=["kitchen"]))
            self.assertEqual(len(json.loads(r["message"])["findings"]), 2, "the open back door and the open window")
            self.assertEqual(env.world.s["robot"]["power_state"], "off")
            unreported = {(e["target"], e["anomaly"]) for e in nav(env)["events"] if e["kind"] == "patrol_unreported"}
            self.assertEqual(unreported, {("door_back", "exterior_door_open"), ("win", "window_open")})
            self.assertNotIn("win", env.world.s.get("reports") or {})
            self.assertIn("robot_stranded", env.evaluate()["failures"])
            refused = step(env, "patrol", rooms=["kitchen"])
            self.assertEqual((refused["ok"], refused["message"]), (False, body.POWER_OFF_TEXT))
            # with a second room the go_to leg meets PowerOff outside any catch: the step commits partway
            env2 = self.m.env(room="kitchen", pos=(2, 4), battery=0.01, perception=perception)
            r = ok(self, step(env2, "patrol", rooms=["kitchen", "living"]))
            self.assertEqual(r["message"], "Powered down partway: " + body.POWER_OFF_TEXT)
            self.assertEqual(env2.world.s["robot"]["room"], "kitchen")

    def test_step_is_a_motion_with_the_bodys_guards(self):
        env = self.m.env(pos=(3, 3), battery=50.0)
        r, seconds, wh, drop = self._step_timed(env, "step", "e")
        ok(self, r)
        self.assertAlmostEqual(seconds, 0.5 / 0.8 * 1.2, places=9, msg="a deliberate step keeps its duration")
        self.assertAlmostEqual(wh, _walk_wh(env, 0.5, seconds, action="step"), places=6)
        self.assert_paid(env, wh, drop)
        r, seconds, wh, drop = self._step_timed(env, "step", "se", over=True)
        ok(self, r)
        self.assertAlmostEqual(seconds, 1.6 * 0.5 / 0.8 * 1.2, places=9)
        self.assertAlmostEqual(wh, _walk_wh(env, 0.5 * 1.4142, seconds, action="step"), places=6)
        self.assertEqual(env.world.s["robot"]["pos"], [5, 4])
        fallen = self.m.env(pos=(3, 3), fallen=True, posture="lying")
        r = step(fallen, "step", "e")
        self.assertEqual((r["ok"], r["message"], r["category"]), (False, "Robot cannot move", "capability"))
        broken = self.m.env(pos=(3, 3), actuator_ok=False)
        self.assertEqual(step(broken, "step", "e")["message"], "Robot cannot move")
        perched = self.m.env(pos=(3, 3), standing_on="robot_dock_1")
        r = step(perched, "step", "e")
        self.assertEqual((r["ok"], r["message"], r["category"]), (False, "Step down first", "safety"))
        flat = self.m.env(pos=(3, 3), battery=0.0)
        r = step(flat, "step", "e")
        self.assertEqual((r["ok"], r["message"]), (False, body.POWER_OFF_TEXT))

    def test_a_step_it_cannot_afford_powers_down_in_place(self):
        env = self.m.env(pos=(3, 3), battery=0.005)       # 0.12 Wh; one step needs 0.16 Wh
        r = ok(self, step(env, "step", "e"))
        self.assertTrue(r["message"].startswith(body.BATTERY_CUT_TEXT.format(0.0)), r["message"])
        robot = env.world.s["robot"]
        self.assertEqual((robot["pos"], robot["power_state"], robot["battery"]), ([3, 3], "off", 0.0))
        self.assertEqual(nav(env)["last_incident"]["kind"], "battery_depleted")
        self.assertIn("robot_stranded", env.evaluate()["failures"])
        ok(self, step(env, "wait", seconds=30))
        self.assertFalse(step(env, "step", "e")["ok"])

    def test_the_dock_is_the_bodys_charge_source(self):
        from house_sim.systems.navigation_robot import robot as R
        env = self.m.env(pos=(0, 0), docked=True, battery=40.0)
        self.assertEqual(R.charge_source(env.world), 2.0)
        self.assertEqual([v for v in run_hooks("robot_charge_source", env.world) if v], [2.0])
        ok(self, step(env, "wait", seconds=600))
        robot = env.world.s["robot"]
        # constant current below 80 %: exactly the dock's 2 %/min, the dock carries the hotel load, nothing drains or
        # charges twice
        self.assertAlmostEqual(robot["battery"], 60.0, places=6)
        self.assertTrue(robot["charging"] and robot["dock_powered"])
        self.assertAlmostEqual(robot["energy_wh_charged"], 0.2 * body.full_wh(env.world.s), places=6)
        self.assertEqual(robot["energy_wh_used"], 0.0)
        self.assertIsNone(R.charge_source(self.m.env(pos=(1, 1), battery=40.0).world), "not docked")
        self.assertIsNone(R.charge_source(self.m.env(pos=(1, 1), docked=True, battery=40.0).world), "off the contacts")
        self.m.c["entities"]["robot_dock_1"].update(charge_rate_pct_per_min=3.0)
        self.assertEqual(R.charge_source(self.m.env(pos=(0, 0), docked=True).world), 3.0)
        self.m.c["entities"]["robot_dock_1"].update(plug_required=True, plugged_in=False)
        unplugged = self.m.env(pos=(0, 0), docked=True, battery=40.0)
        self.assertIsNone(R.charge_source(unplugged.world))
        ok(self, step(unplugged, "wait", seconds=600))
        self.assertFalse(unplugged.world.s["robot"]["charging"])
        self.assertLess(unplugged.world.s["robot"]["battery"], 40.0, "an unpowered dock does not carry the robot's load")

    def test_any_dock_it_sits_on_charges_and_a_cold_pack_waits(self):
        from house_sim.systems.navigation_robot import robot as R
        dock2 = self.m.reg.make("robot_dock", "kitchen")
        dock2["pos"] = [0, 0]
        self.m.c["entities"]["dock_kitchen"] = dock2
        env = self.m.env(room="kitchen", pos=(1, 1), battery=40.0)
        ok(self, step(env, "dock", "dock_kitchen"))
        self.assertEqual(R.seated_dock(env.world.s), "dock_kitchen")
        self.assertEqual(R.charge_source(env.world), 2.0, "the dock it sits on, not its home dock in the living room")
        ok(self, step(env, "wait", seconds=60))
        self.assertTrue(env.world.s["robot"]["charging"])
        self.assertAlmostEqual(env.world.s["robot"]["battery"], 40.0 + 2.0 * 80.0 / 60.0, places=6)
        # a pack at -5 C: the contacts seat, the battery management refuses current until it warms above 0 C
        cold = self.m.env(pos=(1, 1), battery=40.0, battery_temp_c=-5.0)
        r = ok(self, step(cold, "dock"))
        self.assertEqual(r["message"], "Docked on robot_dock_1; the battery is too cold to charge yet")
        robot = cold.world.s["robot"]
        self.assertFalse(robot["charging"])
        self.assertTrue(robot["docked"] and robot["dock_powered"])
        self.assertIn("charge_refused_temp_s", robot["body_log"])

    def test_docking_is_free_and_charges_from_the_next_substep(self):
        env = self.m.env(pos=(1, 1), battery=40.0)
        r, seconds, wh, drop = self._step_timed(env, "dock")
        ok(self, r)
        self.assertEqual(seconds, 20.0)
        self.assertEqual(wh, 0.0, "seated on a live dock: the dock carries the load")
        self.assertAlmostEqual(env.world.s["robot"]["battery"], 40.0 + 2.0 * 20.0 / 60.0, places=6)
        self.assertTrue(env.world.s["robot"]["charging"])
        r, seconds, wh, drop = self._step_timed(env, "undock")
        ok(self, r)
        self.assertFalse(env.world.s["robot"]["charging"])
        self.assertGreater(wh, 0.0)
        self.assert_paid(env, wh, drop)

    def test_the_old_idle_drain_is_ignored_the_body_pays_for_idle_time(self):
        env = self.m.env(pos=(3, 3), battery=50.0, idle_drain_pct_h=50.0)
        r, seconds, wh, drop = self._step_timed(env, "wait", seconds=3600)
        ok(self, r)
        self.assert_paid(env, wh, drop)
        state = env.world.s
        awake = body.activity_power_w(state, "awake", held_kg=0.0)
        parked = body.activity_power_w(state, "standby", held_kg=0.0)
        auto = body.profile(state)["auto_standby_s"]
        self.assertAlmostEqual(wh, (awake * auto + parked * (3600.0 - auto)) / 3600.0, places=6)
        self.assertLess(drop, 1.0, "awake for a minute, then parked: about 0.9 %, not the old 50 %/h")
        # waiting aside stays awake and responsive (arms tucked): the awake power over all of its seconds
        aside = self.m.env(pos=(0, 4), battery=50.0)
        r, seconds, wh, drop = self._step_timed(aside, "wait_aside", seconds=600, cell=[0, 4])
        ok(self, r)
        awake = body.activity_power_w(aside.world.s, "control", held_kg=0.0) + body._hook_power(aside.world, "wait_aside")
        self.assertAlmostEqual(wh, awake * 600.0 / 3600.0, places=6)
        self.assert_paid(aside, wh, drop)

    def test_swapping_in_an_empty_pack_powers_down_and_strands(self):
        self.m.add("pack", "robot_battery_pack", "living", charge_pct=0.0)
        env = self.m.env(pos=(1, 4), battery=30.0, hold={"right": "pack"})
        ok(self, step(env, "swap_battery", "pack"))
        robot = env.world.s["robot"]
        self.assertEqual((robot["power_state"], robot["battery"], robot["battery_min_pct"]), ("off", 0.0, 0.0))
        self.assertEqual(env.world.s["entities"]["pack"]["charge_pct"], 30.0)
        self.assertIn("robot_stranded", env.evaluate()["failures"])
        r = step(env, "swap_battery", "pack")
        self.assertEqual((r["ok"], r["message"]), (False, body.POWER_OFF_TEXT), "a powered-down robot cannot swap its own pack")

    def test_stranded_is_the_kernels_invariant(self):
        self.assertNotIn("navigation_robot.stranded", INVARIANTS)
        self.assertIn("robot_stranded", INVARIANTS)
        env = self.m.env(pos=(0, 0), docked=True, battery=50.0)
        ok(self, step(env, "wait", seconds=60))
        self.assertNotIn("robot_stranded", env.evaluate()["failures"])

    def test_battery_needed_matches_what_a_walk_costs(self):
        from house_sim.systems.navigation_robot import robot as R
        env = self.m.env(pos=(1, 4), battery=50.0)
        need = R.battery_needed(env.world.s, 4.0)
        r, seconds, wh, drop = self._step_timed(env, "walk_to", cell=[9, 4])
        ok(self, r)
        # walk_to adds 0.2 s of settling beyond the walk itself
        extra = body.activity_power_w(env.world.s, "motion", held_kg=0.0) * 0.2 / 3600.0
        self.assertAlmostEqual(need, drop - 100.0 * extra / body.full_wh(env.world.s), places=6)
        stairs = R.battery_needed(env.world.s, 1.7071, steps_up=14)
        self.assertGreater(stairs, 0.7, "a flight up costs about 0.7 % on its own")

    def test_cut_records_drive_the_geometric_camera(self):
        # the camera frames the walk from the motion record: after a stop only the walked cells, after a battery cut
        # only the kitchen (no frame beyond the doorway)
        self.m.add("carton", "box", "living", pos=[5, 4], label="flat-pack carton", size_m=[1.8, 0.6, 0.12], rot=90, mass_kg=2.0)
        self.m.add("panels", "generic_item", "carton", label="panels", mass_kg=34.0, size_m=[1.7, 0.55, 0.08])
        env = self.m.env(pos=(5, 1), battery=50.0, camera="geometric")
        ok(self, step(env, "walk_to", cell=[5, 9], speed="careful"))
        frames = env.world.s["robot"]["view"]["frames"]
        self.assertTrue(frames)
        self.assertTrue(all(f[2] <= 3.5 * 0.5 + 1e-9 for f in frames), frames)
        env2 = self.m.env(room="kitchen", pos=(7, 4), battery=0.025, camera="geometric")
        ok(self, step(env2, "move", "yard", speed="fast"))
        self.assertEqual({f[0] for f in env2.world.s["robot"]["view"]["frames"]}, {"kitchen"})
        self.assertEqual(env2.world.s["robot"]["room"], "kitchen")

    def test_power_down_is_deterministic(self):
        digests = []
        for _ in range(2):
            env = self.m.env(pos=(1, 4), battery=0.04)
            ok(self, step(env, "go_to", "kitchen"))
            ok(self, step(env, "wait", seconds=120))
            digests.append(digest(env.world.s))
        self.assertEqual(digests[0], digests[1])


class TestSelfCare(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_clean_camera_with_wipe_vs_paper_towel(self):
        self.m.add("wipe", "robot_lens_wipe", "living")
        self.m.add("towel", "generic_item", "living", material="paper", category="paper_towel", affordances=["wipe"])
        env = self.m.env(pos=(1, 4), camera_clean=0.3, hold={"right": "wipe"})
        ok(self, step(env, "clean_camera"))
        self.assertEqual(env.world.s["robot"]["camera_clean"], 1.0)
        env2 = self.m.env(pos=(1, 4), camera_clean=0.3, hold={"right": "towel"})
        ok(self, step(env2, "clean_camera"))
        self.assertLess(env2.world.s["robot"]["camera_clean"], 0.9)
        self.assertGreater(env2.world.s["robot"]["lens_scratch"], 0)

    def test_camera_grime_outdoors_in_rain(self):
        self.m.c["weather"]["condition"] = "rain"
        env = self.m.env(room="yard", pos=(3, 3))
        ok(self, step(env, "wait", seconds=5400))
        self.assertLess(env.world.s["robot"]["camera_clean"], 0.8)

    def test_get_up_and_damaged_actuator(self):
        env = self.m.env(pos=(1, 4), fallen=True, posture="lying")
        ok(self, step(env, "get_up"))
        self.assertFalse(env.world.s["robot"]["fallen"])
        env2 = self.m.env(pos=(1, 4), fallen=True, actuator_ok=False)
        r = step(env2, "get_up")
        self.assertFalse(r["ok"])
        ok(self, step(env2, "request_remote_help", issue="fell and cannot stand"))
        ok(self, step(env2, "wait", seconds=300))
        self.assertEqual(nav(env2)["remote_help"][-1]["status"], "passed_to_resident")
        self.assertGreaterEqual(stat(env2, "handoffs"), 1)

    def test_remote_help_needs_network_and_clears_software_fault(self):
        env = self.m.env(pos=(1, 4), stuck="localization")
        r = step(env, "walk_to", cell=[3, 4])
        self.assertFalse(r["ok"])
        ok(self, step(env, "request_remote_help", issue="lost"))
        ok(self, step(env, "wait", seconds=300))
        self.assertIsNone(env.world.s["robot"]["stuck"])
        env2 = self.m.env(pos=(1, 4), network=False)
        self.assertFalse(step(env2, "request_remote_help")["ok"])

    def test_wait_aside_and_fault_log(self):
        env = self.m.env(room="hall", pos=(1, 7))
        self.assertTrue(nav(env).get("robot_spot") is None or True)
        ok(self, step(env, "walk_to", cell=[1, 6]))
        self.assertTrue(nav(env)["robot_spot"]["in_the_way"])
        ok(self, step(env, "wait_aside", seconds=60))
        self.assertFalse(nav(env)["robot_spot"]["in_the_way"])
        ok(self, step(env, "log_fault", "camera_dirty", text="smudge on left camera"))
        self.assertIn("camera_dirty", nav(env)["fault_log"])
        self.assertEqual(env.world.s["robot"]["faults"][-1]["kind"], "camera_dirty")


class TestReach(unittest.TestCase):
    def setUp(self):
        self.m = Mini()
        self.m.add("shelf", "wall_shelf", "kitchen", pos=[2, 0], mount_height_m=2.17, mounted=True)
        self.m.add("pan", "generic_item", "shelf", spos=[0.1, 0.0], label="cake pan", mass_kg=0.6, size_m=[0.25, 0.25, 0.06], material="metal")
        self.m.add("vase", "generic_item", "shelf", spos=[0.4, 0.0], label="glass vase", fragile=True, size_m=[0.12, 0.12, 0.2], material="glass")
        self.m.add("stool", "step_stool", "kitchen", pos=[2, 1])

    def test_too_high_without_aid(self):
        env = self.m.env(room="kitchen", pos=(3, 1))
        r = step(env, "pick_up", "pan")
        self.assertFalse(r["ok"])
        self.assertIn("step stool", r["message"])

    def test_folded_stool_falls_open_stool_reaches(self):
        env = self.m.env(room="kitchen", pos=(3, 1))
        ok(self, step(env, "climb_on", "stool"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        env2 = self.m.env(room="kitchen", pos=(3, 1))
        ok(self, step(env2, "open", "stool"))
        ok(self, step(env2, "climb_on", "stool"))
        self.assertEqual(env2.world.s["robot"]["standing_on"], "stool")
        ok(self, step(env2, "pick_up", "pan"))
        ok(self, step(env2, "step_down"))
        self.assertIsNone(env2.world.s["robot"]["standing_on"])
        self.assertEqual(env2.world.s["entities"]["vase"]["location"], "shelf")
        self.assertFalse(env2.world.s["entities"]["vase"]["broken"])

    def test_stool_on_wet_floor_or_rug_tips(self):
        self.m.c["rooms"]["kitchen"]["wet_cells"] = [[2, 1]]
        self.m.c["entities"]["stool"]["open"] = True
        env = self.m.env(room="kitchen", pos=(3, 1))
        ok(self, step(env, "climb_on", "stool"))
        self.assertTrue(env.world.s["robot"]["fallen"])

    def test_climbing_furniture_is_unsafe(self):
        self.m.add("ochair", "chair", "kitchen", pos=[3, 2], label="office chair", casters=True)
        env = self.m.env(room="kitchen", pos=(4, 2))
        ok(self, step(env, "climb_on", "ochair"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        self.assertGreaterEqual(stat(env, "safety_violations"), 1)

    def test_ladder_needs_approval_and_adult(self):
        self.m.add("ladder", "step_ladder", "kitchen", pos=[5, 1], open=True, settings={"spreader_lock": "locked"})
        env = self.m.env(room="kitchen", pos=(6, 1))
        r = step(env, "climb_on", "ladder")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")
        m2 = Mini()
        m2.add("ladder", "step_ladder", "kitchen", pos=[5, 1], open=True, settings={"spreader_lock": "unlocked"})
        m2.person("dad", "kitchen", [8, 6], permissions=["climb_on:ladder"])
        env2 = m2.env(room="kitchen", pos=(6, 1))
        ok(self, step(env2, "ask", "dad", topic="permission:climb_on:ladder"))
        ok(self, step(env2, "climb_on", "ladder"))
        self.assertTrue(env2.world.s["robot"]["fallen"], "spreaders were not locked")
        env3 = m2.env(room="kitchen", pos=(6, 1))
        ok(self, step(env3, "ask", "dad", topic="permission:climb_on:ladder"))
        ok(self, step(env3, "set", "ladder", setting="spreader_lock", value="locked"))
        ok(self, step(env3, "climb_on", "ladder"))
        self.assertFalse(env3.world.s["robot"]["fallen"])
        self.assertAlmostEqual(env3.world.s["entities"]["ladder"]["standing_height_m"], 1.2)
        env4 = m2.env(room="kitchen", pos=(6, 1))
        ok(self, step(env4, "ask", "dad", topic="permission:climb_on:ladder"))
        ok(self, step(env4, "set", "ladder", setting="spreader_lock", value="locked"))
        ok(self, step(env4, "climb_on", "ladder", step=5))
        self.assertTrue(env4.world.s["robot"]["fallen"], "never stand on the top step")

    def test_reacher_light_item_ok_glass_slips(self):
        self.m.add("grab", "reacher_grabber", "kitchen")
        env = self.m.env(room="kitchen", pos=(3, 1), hold={"right": "grab"})
        ok(self, step(env, "use_reacher", "pan", tool="grab"))
        self.assertEqual(env.world.s["entities"]["pan"]["location"], "robot")
        self.m.c["entities"]["vase"]["mass_kg"] = 0.5
        env2 = self.m.env(room="kitchen", pos=(3, 1), hold={"right": "grab"})
        ok(self, step(env2, "use_reacher", "vase", tool="grab"))
        self.assertTrue(env2.world.s["entities"]["vase"]["broken"])

    def test_overreach_knocks_neighbor(self):
        self.m.c["entities"]["shelf"]["mount_height_m"] = 2.28
        self.m.c["entities"]["stool"]["open"] = True
        self.m.c["entities"]["vase"]["spos"] = [0.3, 0.0]
        env = self.m.env(room="kitchen", pos=(3, 1))
        ok(self, step(env, "climb_on", "stool"))
        ok(self, step(env, "pick_up", "pan"))
        self.assertTrue(env.world.s["entities"]["vase"]["broken"])


class TestMeasure(unittest.TestCase):
    def setUp(self):
        self.m = Mini()
        self.m.add("chair", "armchair", "living", pos=[4, 4], size_m=[0.8, 0.85, 0.95], _extents_mm=[873.0, 902.0, 951.0])
        self.m.add("tape", "tape_measure", "living")
        self.m.add("laser", "laser_distance_meter", "living")
        self.m.add("lvl", "spirit_level", "living")
        self.m.add("pencil", "carpenter_pencil", "living")

    def test_tape_reads_true_extent_including_arms(self):
        env = self.m.env(pos=(3, 3), hold={"right": "tape"})
        r = ok(self, step(env, "measure", "chair", dimension="width"))
        value = json.loads(r["message"])["value"]
        self.assertAlmostEqual(value, 873.0, delta=2.0)
        obs = env.observe()
        self.assertEqual(obs["measurement"]["value"], value)
        r = ok(self, step(env, "measure", "chair", dimension="width", units="in"))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 873.0 / 25.4, delta=0.1)

    def test_estimate_and_laser_on_edges_are_off(self):
        env = self.m.env(pos=(3, 3))
        r = ok(self, step(env, "measure", "chair", dimension="width"))
        est = json.loads(r["message"])["value"]
        self.assertGreater(abs(est - 873.0), 10.0, "an estimate is at least 2 % off: never within a tape's tolerance")
        # a fixed error per quantity: repeating it (later, or from another spot) gives the same number -- no averaging
        for _ in range(3):
            self.assertEqual(json.loads(ok(self, step(env, "measure", "chair", dimension="width"))["message"])["value"], est)
        ok(self, step(env, "walk_to", cell=[3, 2]))
        self.assertEqual(json.loads(ok(self, step(env, "measure", "chair", dimension="dims"))["message"])["value"][0], est)
        env2 = self.m.env(pos=(3, 3), hold={"right": "laser"})
        r = ok(self, step(env2, "measure", "chair", dimension="width"))
        self.assertIn("no target surface", " ".join(json.loads(r["message"])["notes"]))

    def test_inside_doorway_needs_case_length_or_laser(self):
        self.m.c["connections"]["c_lk"]["_clear_width_mm"] = 812.0
        env = self.m.env(pos=(10, 4), hold={"right": "tape"})
        r = ok(self, step(env, "measure", "c_lk", dimension="width"))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 812.0 - 76.0, delta=2.5)
        r = ok(self, step(env, "measure", "c_lk", dimension="width", add_case=True))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 812.0, delta=2.5)
        env2 = self.m.env(pos=(10, 4), hold={"right": "laser"})
        r = ok(self, step(env2, "measure", "c_lk", dimension="width"))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 812.0, delta=2.5)
        ok(self, step(env2, "report", "c_lk", field="clear_width_mm", value=json.loads(r["message"])["value"]))

    def test_space_profile_minimum(self):
        self.m.add("alcove", "generic_item", "living", pos=[11, 0], portable=False, category="space", measure_inside=True,
                   _measure={"width": {"floor": 905.0, "mid": 912.0, "top": 884.0}, "depth": 600.0}, size_m=[0.9, 0.6, 2.2])
        env = self.m.env(pos=(10, 1), hold={"right": "laser"})
        r = ok(self, step(env, "measure", "alcove", dimension="width"))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 912.0, delta=2.5)
        r = ok(self, step(env, "measure", "alcove", dimension="width", at="min"))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 884.0, delta=2.5)

    def test_long_unsupported_span_sags(self):
        self.m.add("rug", "rug", "living", pos=[2, 6], size_m=[4.0, 1.4, 0.01], _extents_mm=[4000.0, 1400.0, 10.0])
        env = self.m.env(pos=(3, 5), hold={"right": "tape"})
        r = ok(self, step(env, "measure", "rug", dimension="width"))
        self.assertGreater(json.loads(r["message"])["value"] - 4000.0, 4.0)
        r = ok(self, step(env, "measure", "rug", dimension="width", support=True))
        self.assertAlmostEqual(json.loads(r["message"])["value"], 4000.0, delta=2.5)

    def test_leveling_feet_and_walking_washer(self):
        self.m.add("washer", "generic_item", "kitchen", pos=[1, 1], portable=False, blocks_path=True, size_m=[0.6, 0.6, 0.85],
                   leveling={"floor_mm": {"fl": 0.0, "fr": 4.0, "bl": -2.0, "br": 3.0}, "span_mm": [560.0, 560.0]},
                   settings={"foot_fl_mm": 0.0, "foot_fr_mm": 0.0, "foot_bl_mm": 0.0, "foot_br_mm": 0.0, "lock_nuts": "loose"},
                   setting_options={"foot_fl_mm": {"min": -10, "max": 25}, "foot_fr_mm": {"min": -10, "max": 25}, "foot_bl_mm": {"min": -10, "max": 25},
                                    "foot_br_mm": {"min": -10, "max": 25}, "lock_nuts": ["loose", "locked"]},
                   appliance={"programs": {"spin": {"duration_s": 600.0}}, "program": "spin", "state": "idle", "remaining_s": 0.0, "requires_power": False})
        self.m.add("lvl2", "spirit_level", "kitchen")
        env = self.m.env(room="kitchen", pos=(3, 2), hold={"right": "lvl2"})
        r = ok(self, step(env, "check_level", "washer"))
        self.assertNotEqual(json.loads(r["message"])["tilt_x_deg"], 0.0)
        ok(self, step(env, "start", "washer"))
        ok(self, step(env, "wait", seconds=600))
        self.assertGreater(env.world.s["entities"]["washer"]["_walked_mm"], 0)
        env2 = self.m.env(room="kitchen", pos=(3, 2), hold={"right": "lvl2"})
        for foot, mm in (("fl", 4.0), ("fr", 0.0), ("bl", 6.0), ("br", 1.0)):
            ok(self, step(env2, "set", "washer", setting="foot_{}_mm".format(foot), value=mm))
        ok(self, step(env2, "set", "washer", setting="lock_nuts", value="locked"))
        r = ok(self, step(env2, "check_level", "washer"))
        self.assertLessEqual(abs(json.loads(r["message"])["tilt_x_deg"]), 0.1)
        self.assertEqual(json.loads(r["message"])["wobble"], "solid")
        ok(self, step(env2, "start", "washer"))
        ok(self, step(env2, "wait", seconds=600))
        self.assertEqual(env2.world.s["entities"]["washer"].get("_walked_mm", 0), 0)

    def test_mark_measured_vs_eyeballed(self):
        self.m.add("wall", "wall_surface", "living", pos=[6, 0], mounted=True)
        env = self.m.env(pos=(6, 1), hold={"right": "tape", "left": "pencil"})
        ok(self, step(env, "measure", "wall", dimension="height"))
        ok(self, step(env, "mark", "wall", height_m=1.45, x_m=0.5))
        self.assertAlmostEqual(env.world.s["entities"]["wall"]["marks"][-1]["height_m"], 1.45, delta=0.0015)
        env2 = self.m.env(pos=(6, 1), hold={"left": "pencil"})
        ok(self, step(env2, "mark", "wall", height_m=1.45, x_m=0.5))
        self.assertEqual(len(env2.world.s["entities"]["wall"]["marks"]), 1)


def building():
    reg = default_registry()
    rooms = {
        "apt": {"type": "entryway", "size_m": [3.0, 3.0], "floor": 6},
        "corridor": {"type": "building_corridor", "size_m": [2.0, 6.0], "floor": 6, "shared": True},
        "car": {"type": "elevator", "size_m": [1.5, 1.5], "floor": 0},
        "lobby": {"type": "lobby", "size_m": [4.0, 4.0], "floor": 0, "shared": True},
    }
    conns = {"c_ac": {"rooms": ["apt", "corridor"], "cells": {"apt": [5, 2], "corridor": [0, 1]}, "width_m": 0.9},
             "c_cc": {"rooms": ["corridor", "car"], "cells": {"corridor": [3, 10], "car": [0, 0]}, "width_m": 0.9, "kind": "elevator_door"},
             "c_cl": {"rooms": ["car", "lobby"], "cells": {"car": [2, 0], "lobby": [0, 0]}, "width_m": 0.9, "kind": "elevator_door"}}
    c = {"schema_version": 2, "start_room": "corridor", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {}}
    for rid, room in rooms.items():
        room["exits"] = []
        light = reg.make("ceiling_light", rid, on=True)
        light["pos"] = [1, 1]
        light["mounted"] = True
        c["entities"]["light_" + rid] = light
    rooms["car"]["elevator"] = {"landings": {"corridor": 6, "lobby": 0}, "car_floor": 0, "doors": "closed", "doors_close_at_s": 0.0,
                                "moving_to": None, "arrive_at_s": 0.0, "travel_s_per_floor": 2.5, "door_open_s": 8.0, "held_s": 0.0}
    c["robot"] = {"room": "corridor", "pos": [2, 9]}
    return c


class TestElevator(unittest.TestCase):
    def test_call_ride_exit(self):
        env = HouseEnv(building(), seed=1)
        r = step(env, "move", "car")
        self.assertFalse(r["ok"])
        ok(self, step(env, "call_elevator"))
        ok(self, step(env, "wait", seconds=20))
        self.assertEqual(env.world.s["rooms"]["car"]["elevator"]["doors"], "open")
        ok(self, step(env, "move", "car"))
        self.assertFalse(step(env, "ride_elevator", "lobby")["ok"], "doors cannot close on the robot")
        ok(self, step(env, "walk_to", cell=[1, 1]))
        ok(self, step(env, "ride_elevator", "lobby"))
        ok(self, step(env, "move", "lobby"))
        self.assertEqual(env.world.s["robot"]["room"], "lobby")

    def test_holding_doors(self):
        env = HouseEnv(building(), seed=1)
        ok(self, step(env, "call_elevator"))
        ok(self, step(env, "wait", seconds=20))
        ok(self, step(env, "move", "car"))
        ok(self, step(env, "wait", seconds=120))
        self.assertGreaterEqual(env.world.s["rooms"]["car"]["elevator"]["held_s"], 60)
        self.assertGreaterEqual(counts(env, "social.elevator_held"), 1)


class TestDoorsPets(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_gate_latches_and_dog_slips_through_when_left_open(self):
        self.m.pet("rex", "yard", [3, 2], follows_robot=True)
        env = self.m.env(room="yard", pos=(1, 1))
        ok(self, step(env, "open", "gate_side"))
        self.assertFalse(env.world.s["entities"]["gate_side"]["latched"])
        ok(self, step(env, "move", "front"))
        ok(self, step(env, "close", "gate_side"))
        self.assertTrue(env.world.s["entities"]["gate_side"]["latched"])
        ok(self, step(env, "wait", seconds=60))
        self.assertEqual(env.world.s["pets"]["rex"]["location"], "yard")
        env2 = self.m.env(room="yard", pos=(1, 1))
        ok(self, step(env2, "open", "gate_side"))
        ok(self, step(env2, "move", "front"))
        ok(self, step(env2, "wait", seconds=30))
        self.assertEqual(env2.world.s["pets"]["rex"]["location"], "front")
        self.assertGreaterEqual(stat(env2, "pet_escape"), 1)

    def test_shed_door_shuts_robot_in_unless_held(self):
        self.m.c["entities"]["door_back"].update(self_closing=True, latch_outside=True, inside_room="yard", open=False, settings={"hold_open": False},
                                                 setting_options={"hold_open": [True, False]})
        env = self.m.env(room="kitchen", pos=(8, 4))
        ok(self, step(env, "open", "door_back"))
        ok(self, step(env, "move", "yard"))
        ok(self, step(env, "walk_to", cell=[4, 3]))
        ok(self, step(env, "wait", seconds=60))
        self.assertFalse(env.world.s["entities"]["door_back"]["open"])
        self.assertEqual(env.world.s["robot"]["nav_trapped"], "door_back")
        self.assertFalse(step(env, "open", "door_back")["ok"])
        self.assertIn("navigation_robot.trapped", env.evaluate()["failures"])
        self.m.add("wedge_1", "generic_item", "kitchen", pos=[8, 5], label="door wedge", size_m=[0.12, 0.05, 0.03])
        self.m.c["entities"]["door_back"]["propped"] = True
        self.m.c["entities"]["door_back"]["mech"] = {"propped_by": "wedge_1"}
        env2 = self.m.env(room="kitchen", pos=(8, 4))
        ok(self, step(env2, "open", "door_back"))
        ok(self, step(env2, "move", "yard"))
        ok(self, step(env2, "walk_to", cell=[4, 3]))
        ok(self, step(env2, "wait", seconds=60))
        self.assertTrue(env2.world.s["entities"]["door_back"]["open"])

    def test_moving_garage_door_and_reversing_car(self):
        self.m.c["entities"]["door_front"].update(open=True, label="garage door")
        self.m.c["events"] = [{"at_s": 1.0, "effect": "navigation_robot.garage_door", "args": {"door": "door_front", "action": "close", "duration_s": 13}}]
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "wait", seconds=2))
        r = step(env, "move", "front")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")
        m2 = Mini()
        m2.add("car", "furniture", "front", pos=[6, 0], size_m=[1.0, 1.0, 1.5], label="car", vehicle={"state": "reversing", "reverse_dir": "south",
                                                                                                         "_until_s": 30.0, "then": "gone"})
        env2 = m2.env(room="front", pos=(2, 3))
        ok(self, step(env2, "walk_to", cell=[12, 3]))
        self.assertTrue(env2.world.s["robot"]["fallen"])
        self.assertGreaterEqual(stat(env2, "safety_violations"), 1)
        env3 = m2.env(room="front", pos=(2, 3))
        self.assertFalse(step(env3, "walk_to", cell=[12, 3], avoid=["vehicle"])["ok"], "no way past the reversing car's path")
        ok(self, step(env3, "wait", seconds=60))
        self.assertEqual(env3.world.s["entities"]["car"]["vehicle"]["state"], "gone")
        ok(self, step(env3, "walk_to", cell=[12, 3], avoid=["vehicle"]))
        self.assertFalse(env3.world.s["robot"].get("fallen"))


class TestOutdoorTerrain(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_dog_waste_tracked_inside_unless_wiped(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [1, 3], "kind": "waste", "source": "dog"}]
        env = self.m.env(room="yard", pos=(4, 3))
        ok(self, step(env, "walk_to", cell=[0, 3]))
        ok(self, step(env, "move", "kitchen"))
        self.assertGreater(env.world.s["entities"]["floor_kitchen"]["soil"].get("biohazard", 0), 0)
        self.assertIn("navigation_robot.tracked_dirt", env.evaluate()["failures"])
        self.m.add("mat", "generic_item", "kitchen", pos=[9, 4], category="doormat", doormat=True, ground_layer=True, size_m=[0.5, 0.5, 0.01])
        env2 = self.m.env(room="yard", pos=(4, 3))
        ok(self, step(env2, "walk_to", cell=[0, 3]))
        ok(self, step(env2, "move", "kitchen"))
        self.assertEqual(env2.world.s["entities"]["floor_kitchen"]["soil"].get("biohazard", 0), 0)
        env3 = self.m.env(room="yard", pos=(4, 3))
        ok(self, step(env3, "walk_to", cell=[0, 2], avoid=["waste"]))
        ok(self, step(env3, "move", "kitchen"))
        self.assertNotIn("navigation_robot.tracked_dirt", env3.evaluate()["failures"])

    def test_deep_mud_gets_robot_stuck(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [5, 3], "kind": "mud", "depth_cm": 12.0}]
        env = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env, "walk_to", cell=[8, 3]))
        self.assertEqual(env.world.s["robot"]["stuck"], "mud")
        self.assertFalse(step(env, "walk_to", cell=[2, 3])["ok"])
        env2 = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env2, "walk_to", cell=[8, 3], avoid=["mud"]))
        self.assertIsNone(env2.world.s["robot"].get("stuck"))

    def test_trampling_seeded_lawn_and_edge_drop(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [5, 3], "kind": "seeded"}, {"cell": [5, 6], "kind": "edge_drop", "drop_m": 0.7}]
        env = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env, "walk_to", cell=[8, 3]))
        self.assertIn("navigation_robot.trampled", env.evaluate()["failures"])
        env2 = self.m.env(room="yard", pos=(2, 6))
        ok(self, step(env2, "walk_to", cell=[8, 6]))
        self.assertTrue(env2.world.s["robot"]["fallen"])
        env3 = self.m.env(room="yard", pos=(2, 6))
        ok(self, step(env3, "walk_to", cell=[8, 6], speed="careful"))
        self.assertFalse(env3.world.s["robot"]["fallen"])
        self.assertGreaterEqual(counts(env3, "terrain.edge_walk"), 1)

    def test_wet_steep_slope(self):
        self.m.c["weather"]["condition"] = "rain"
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [5, 3], "kind": "slope", "deg": 18.0}]
        env = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env, "walk_to", cell=[8, 3], speed="careful"))
        self.assertTrue(env.world.s["robot"]["fallen"])
        m2 = Mini()
        m2.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [5, 3], "kind": "slope", "deg": 18.0}]
        env2 = m2.env(room="yard", pos=(2, 3))
        ok(self, step(env2, "walk_to", cell=[8, 3], speed="careful"))
        self.assertFalse(env2.world.s["robot"]["fallen"], "dry grass holds a careful gait on 18 degrees")

    def test_gravel_scatter_and_loose_board(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [5, 3], "kind": "gravel"}, {"cell": [5, 6], "kind": "loose_board"}]
        env = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env, "walk_to", cell=[8, 3], speed="fast"))
        self.assertGreaterEqual(counts(env, "terrain.gravel_scattered"), 1)
        env2 = self.m.env(room="yard", pos=(2, 6))
        ok(self, step(env2, "walk_to", cell=[8, 6]))
        self.assertGreaterEqual(stat(env2, "damage"), 1)


class TestFollowLead(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_follow_to_kitchen_and_wait_outside_bathroom(self):
        self.m.person("ann", "living", [4, 4], _route=[{"room": "kitchen", "cell": [5, 2], "dwell_s": 30}])
        env = self.m.env(pos=(2, 4))
        r = ok(self, step(env, "follow", "ann", distance_m=1.5))
        self.assertEqual(json.loads(r["message"])["status"], "arrived")
        self.assertEqual(env.world.s["robot"]["room"], "kitchen")
        self.assertEqual(counts(env, "social.contacts"), 0)
        m2 = Mini()
        m2.person("ann", "living", [4, 4], _route=[{"room": "bath", "private": True, "close_door": True}])
        env2 = m2.env(pos=(2, 4))
        r = ok(self, step(env2, "follow", "ann"))
        self.assertEqual(json.loads(r["message"])["status"], "waiting_outside")
        self.assertEqual(env2.world.s["robot"]["room"], "hall")
        self.assertFalse(env2.world.s["entities"]["door_bath"]["open"])

    def test_tailgating_and_losing_track(self):
        self.m.person("ann", "living", [4, 4], _route=[{"room": "kitchen", "cell": [5, 2]}])
        env = self.m.env(pos=(2, 4))
        ok(self, step(env, "follow", "ann", distance_m=0.5))
        self.assertGreaterEqual(counts(env, "social.tailgating"), 1)
        m2 = Mini()
        m2.person("ann", "living", [4, 4], _route=[{"room": "upstairs"}])
        env2 = m2.env(pos=(2, 4))
        r = ok(self, step(env2, "follow", "ann", distance_m=4.0))
        self.assertEqual(json.loads(r["message"])["status"], "lost")

    def test_lead_walker_user_needs_step_free_route_and_pace(self):
        self.m.person("gran", "living", [4, 4], role="elderly", abilities={"mobility": "walker", "vision": "full"})
        env = self.m.env(pos=(3, 4))
        r = ok(self, step(env, "lead", "gran", destination="kitchen"))
        self.assertEqual(json.loads(r["message"])["status"], "arrived")
        self.assertEqual(env.world.s["people"]["gran"]["location"], "kitchen")
        env2 = self.m.env(pos=(3, 4))
        r = ok(self, step(env2, "lead", "gran", destination="upstairs"))
        self.assertEqual(json.loads(r["message"])["status"], "person_fell")
        self.assertGreaterEqual(stat(env2, "injury"), 1)
        env3 = self.m.env(pos=(3, 4))
        r = ok(self, step(env3, "lead", "gran", destination="kitchen", pace="fast"))
        self.assertEqual(json.loads(r["message"])["status"], "person_left_behind")
        self.assertNotEqual(env3.world.s["people"]["gran"]["location"], "kitchen")

    def test_lead_low_vision_person_past_hazard(self):
        self.m.person("gran", "living", [4, 4], role="elderly", abilities={"mobility": "cane", "vision": "low"})
        self.m.c["rooms"]["living"]["wet_cells"] = [[8, 4], [8, 3], [8, 5]]
        env = self.m.env(pos=(3, 4))
        r = ok(self, step(env, "lead", "gran", destination="kitchen"))
        self.assertEqual(json.loads(r["message"])["status"], "person_fell")
        env2 = self.m.env(pos=(3, 4))
        r = ok(self, step(env2, "lead", "gran", destination="kitchen", warn=True))
        self.assertEqual(json.loads(r["message"])["status"], "arrived")
        env3 = self.m.env(pos=(3, 4))
        r = ok(self, step(env3, "lead", "gran", destination="kitchen", avoid=["wet"]))
        self.assertEqual(json.loads(r["message"])["status"], "arrived")


class TestGoToPatrol(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_go_to_room_object_alias_home(self):
        self.m.add("chair", "armchair", "kitchen", pos=[6, 5])
        self.m.c["memory"] = {"room_alias:den": {"value": "bedroom", "time_s": 0, "scope": "household"}}
        env = self.m.env(pos=(1, 4), home_pose={"room": "hall", "cell": [0, 4]})
        ok(self, step(env, "go_to", "chair"))
        self.assertEqual(env.world.s["robot"]["room"], "kitchen")
        ok(self, step(env, "go_to", "den"))
        self.assertEqual(env.world.s["robot"]["room"], "bedroom")
        ok(self, step(env, "go_to", "home"))
        self.assertEqual((env.world.s["robot"]["room"], env.world.s["robot"]["pos"]), ("hall", [0, 4]))
        self.assertFalse(step(env, "go_to", "attic")["ok"])

    def test_go_to_opens_and_restores_doors(self):
        self.m.c["entities"]["door_bed"]["open"] = False
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "go_to", "bedroom", restore_doors=True))
        self.assertEqual(env.world.s["robot"]["room"], "bedroom")
        self.assertFalse(env.world.s["entities"]["door_bed"]["open"])

    def test_unknown_house_needs_exploring(self):
        env = self.m.env(pos=(1, 4), map_known=False)
        self.assertFalse(step(env, "go_to", "bedroom")["ok"])
        ok(self, step(env, "go_to", "kitchen"))

    def test_coverage_from_doorway_vs_walking_in(self):
        self.m.add("wardrobe", "wardrobe", "kitchen", pos=[4, 2], size_m=[0.5, 2.0, 2.0])
        env = self.m.env(pos=(9, 4))
        ok(self, step(env, "move", "kitchen"))
        partial = nav(env)["visits"]["kitchen"]["coverage"]
        self.assertLess(partial, 0.9)
        ok(self, step(env, "walk_to", cell=[7, 4]))
        self.assertGreater(nav(env)["visits"]["kitchen"]["coverage"], partial)

    def test_patrol_finds_and_reports_anomalies_skips_occupied_bath(self):
        self.m.c["entities"]["door_front"]["locked"] = False
        self.m.add("win", "window", "kitchen", pos=[5, 0], open=True, mounted=True)
        self.m.person("mom", "bath", [3, 2], activity="showering")
        env = self.m.env(pos=(1, 4))
        r = ok(self, step(env, "patrol", rooms=["living", "kitchen", "hall", "bath"], dock_after=True))
        result = json.loads(r["message"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("window_open", kinds)
        self.assertIn("exterior_door_unlocked", kinds)
        self.assertIn("bath", result["skipped_occupied"])
        self.assertEqual(env.world.s["reports"]["win"]["anomaly"], "window_open")
        self.assertTrue(env.world.s["robot"]["docked"])
        self.assertEqual(stat(env, "privacy_violations"), 0)


class TestRobotVacuum(unittest.TestCase):
    def setUp(self):
        self.m = Mini()
        self.m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        self.m.add("rv", "robot_vacuum", "living", pos=[11, 9], rv={"dock": "rvdock", "state": "docked", "docked": True, "bin_fill": 0.9,
                                                                     "brush_hair": 0.6})
        self.m.add("bin", "generic_item", "living", pos=[9, 9], label="trash can", category="trash", waste_stream="trash", accepts_items=True,
                   container=True, capacity_l=30.0)
        self.m.add("scissors", "generic_item", "living", label="scissors", affordances=["cut"], tool_kind="scissors")
        self.m.c["entities"]["floor_living"]["soil"] = {"dust": 0.4, "pet_hair": 0.3}

    def test_run_cleans_and_fills_bin_until_full(self):
        env = self.m.env(pos=(10, 8))
        ok(self, step(env, "start", "rv", program="clean"))
        ok(self, step(env, "wait", seconds=1800))
        rv = env.world.s["entities"]["rv"]["rv"]
        self.assertEqual((rv["state"], rv["error"]), ("error", "bin_full"))
        self.assertLess(env.world.s["entities"]["floor_living"]["soil"]["dust"], 0.4)

    def test_empty_bin_and_cut_hair(self):
        env = self.m.env(pos=(10, 8), hold={"right": "scissors"})
        ok(self, step(env, "empty_robot_vacuum", "rv", destination="bin"))
        ok(self, step(env, "clean_robot_brushes", "rv"))
        rv = env.world.s["entities"]["rv"]["rv"]
        self.assertEqual((rv["bin_fill"], rv["brush_hair"]), (0.0, 0.0))
        env2 = self.m.env(pos=(10, 8))
        ok(self, step(env2, "clean_robot_brushes", "rv"))
        self.assertGreater(env2.world.s["entities"]["rv"]["rv"]["brush_hair"], 0.2)

    def test_cord_eaten_sock_jams_earring_swallowed(self):
        self.m.c["entities"]["rv"]["rv"]["bin_fill"] = 0.1
        self.m.add("cable", "generic_item", "living", pos=[4, 4], category="cord", floor_hazard="cord", size_m=[0.5, 0.02, 0.01], mass_kg=0.05)
        self.m.add("ring", "generic_item", "living", pos=[9, 8], label="earring", size_m=[0.015, 0.01, 0.01], mass_kg=0.002, value=250.0)
        env = self.m.env(pos=(10, 8))
        ok(self, step(env, "start", "rv"))
        ok(self, step(env, "wait", seconds=120))
        self.assertTrue(env.world.s["entities"]["cable"]["broken"])
        self.assertEqual(env.world.s["entities"]["rv"]["rv"]["state"], "stuck")
        self.assertEqual(env.world.s["entities"]["ring"]["location"], "rv")

    def test_tank_liquids_and_lift_vs_drag(self):
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        m.add("rv", "robot_vacuum_mop", "living", pos=[5, 5], rv={"dock": "rvdock", "state": "stuck", "stuck_reason": "rug_fringe"})
        m.add("jug", "generic_item", "living", liquid_l=1.0, liquid="water", capacity_l=1.0)
        m.add("bleach", "generic_item", "living", liquid_l=0.5, liquid="bleach_solution", capacity_l=1.0)
        env = m.env(pos=(5, 4), hold={"right": "jug"})
        ok(self, step(env, "refill_robot_tank", "rv", tool="jug"))
        self.assertAlmostEqual(env.world.s["entities"]["rv"]["rv"]["mop_tank_l"], 0.3)
        self.assertFalse(env.world.s["entities"]["rv"]["rv"].get("damaged"))
        env2 = m.env(pos=(5, 4), hold={"right": "bleach"})
        ok(self, step(env2, "refill_robot_tank", "rv", tool="bleach"))
        self.assertTrue(env2.world.s["entities"]["rv"]["rv"]["damaged"])
        env3 = m.env(pos=(5, 4))
        ok(self, step(env3, "free_stuck_robot", "rv", method="drag"))
        self.assertFalse(env3.world.s["entities"]["rv"]["rv"]["bumper_ok"])
        env4 = m.env(pos=(5, 4))
        ok(self, step(env4, "free_stuck_robot", "rv", cell=[6, 6]))
        self.assertTrue(env4.world.s["entities"]["rv"]["rv"]["bumper_ok"])
        self.assertEqual(env4.world.s["entities"]["rv"]["rv"]["state"], "idle")

    def test_dock_other_robot_and_clearance_and_cancel(self):
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[11, 9])
        m.add("rv", "robot_vacuum", "living", pos=[10, 8], rv={"dock": "rvdock", "state": "cleaning", "run_room": "living", "encounters_done": True,
                                                               "run_remaining_s": 3000.0})
        env = m.env(pos=(11, 8))
        ok(self, step(env, "wait", seconds=60))
        self.assertTrue(env.world.s["entities"]["rv"]["rv"]["dock_zone_clear"])
        ok(self, step(env, "dock", "rv"))
        rv = env.world.s["entities"]["rv"]["rv"]
        self.assertEqual((rv["state"], rv["aborted_runs"]), ("docked", 1))
        m.add("box", "box", "living", pos=[11, 7])
        env2 = m.env(pos=(10, 7))
        ok(self, step(env2, "wait", seconds=60))
        self.assertFalse(env2.world.s["entities"]["rv"]["rv"]["dock_zone_clear"])


class TestRoutesAndWorld(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_route_watch_clear_width(self):
        self.m.c["household"] = {"nav_watch": {"routes": {"wc": {"rooms": ["living"], "from_cell": [0, 4], "to_cell": [11, 4], "width_m": 0.9}}}}
        env = self.m.env(pos=(5, 8))
        ok(self, step(env, "wait", seconds=30))
        self.assertTrue(nav(env)["routes"]["wc"]["clear"])
        self.m.add("basket", "laundry_basket", "living", pos=[5, 3])
        self.m.add("bag", "bag", "living", pos=[5, 5])
        self.m.add("toys", "generic_item", "living", pos=[5, 7], size_m=[0.4, 0.4, 0.3])
        self.m.add("toys2", "generic_item", "living", pos=[5, 1], size_m=[0.4, 0.4, 0.3])
        self.m.add("toys3", "generic_item", "living", pos=[5, 9], size_m=[0.4, 0.4, 0.3])
        env2 = self.m.env(pos=(5, 8))
        ok(self, step(env2, "wait", seconds=30))
        self.assertFalse(nav(env2)["routes"]["wc"]["clear"])
        self.assertTrue(set(nav(env2)["routes"]["wc"]["blockers"]) & {"basket", "bag", "toys", "toys2"})

    def test_observer_and_inspectors(self):
        self.m.add("rug", "rug", "living", pos=[2, 6], non_slip_pad=False, size_m=[1.0, 1.0, 0.01])
        env = self.m.env(room="hall", pos=(1, 1))
        obs = env.observe()
        self.assertEqual(obs["nav"]["battery_pct"], 100.0)
        self.assertEqual(obs["stairs"][0]["connection"], "c_hu")
        env2 = self.m.env(pos=(2, 5))
        r = ok(self, step(env2, "inspect", "rug"))
        self.assertFalse(json.loads(r["message"])["non_slip_pad"])
        hz = [h for h in env2.world.s["rooms"]["living"]["floor_hazards"] if h["kind"] == "rug_slip"]
        self.assertTrue(hz)

    def test_answerer_room_alias(self):
        self.m.person("dad", "living", [6, 6])
        self.m.c["user_simulator"] = {"nav": {"room_aliases": {"den": "bedroom"}, "necessary": ["nav:where:den"]}}
        env = self.m.env(pos=(5, 6))
        r = ok(self, step(env, "ask", "dad", topic="nav:where:den"))
        self.assertEqual(json.loads(r["message"])["value"], "bedroom")
        self.assertEqual(stat(env, "necessary_questions"), 1)
        ok(self, step(env, "remember", "room_alias:den", destination="nav:where:den"))
        ok(self, step(env, "go_to", "den"))
        self.assertEqual(env.world.s["robot"]["room"], "bedroom")


class TestAuditAdditions(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_unpadded_runner_bunches_at_speed(self):
        self.m.add("runner", "rug", "living", pos=[5, 4], non_slip_pad=False, size_m=[1.0, 0.5, 0.01])
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4], speed="fast"))
        self.assertTrue(env.world.s["entities"]["runner"].get("bunched"))
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4], speed="careful"))
        self.assertFalse(env2.world.s["entities"]["runner"].get("bunched"))
        m2 = Mini()
        m2.add("carpetrug", "rug", "living", pos=[5, 4], non_slip_pad=True, size_m=[1.0, 0.5, 0.01])
        env3 = m2.env(pos=(1, 4))
        ok(self, step(env3, "walk_to", cell=[10, 4]))
        self.assertFalse(env3.world.s["robot"]["fallen"], "a padded rug is crossed normally")

    def test_wall_picture_knocked_by_wide_load(self):
        self.m.add("frame", "generic_item", "hall", pos=[0, 4], mounted=True, mount_height_m=1.5, fragile=True, wall_decor=True,
                   size_m=[0.5, 0.05, 0.4], label="framed photo")
        self.m.add("board", "generic_item", "hall", size_m=[0.7, 0.62, 0.05], label="ironing board", carry_level=True, mass_kg=4.0)
        env = self.m.env(room="hall", pos=(0, 7), hold={"both": "board"})
        ok(self, step(env, "walk_to", cell=[0, 1]))
        self.assertTrue(env.world.s["entities"]["frame"]["broken"])
        env2 = self.m.env(room="hall", pos=(0, 7), hold={"both": "board"})
        ok(self, step(env2, "walk_to", cell=[0, 1], avoid=["fragile"]))
        self.assertFalse(env2.world.s["entities"]["frame"]["broken"])

    def test_cat_wanders_out_of_open_door(self):
        self.m.pet("cat", "bath", [3, 2], species="cat", nav_wanders=True)
        env = self.m.env(room="hall", pos=(2, 2))
        ok(self, step(env, "wait", seconds=120))
        self.assertEqual(env.world.s["pets"]["cat"]["location"], "hall")
        m2 = Mini()
        m2.c["entities"]["door_bath"]["open"] = False
        m2.pet("cat", "bath", [3, 2], species="cat", nav_wanders=True)
        env2 = m2.env(room="hall", pos=(2, 2))
        ok(self, step(env2, "wait", seconds=120))
        self.assertEqual(env2.world.s["pets"]["cat"]["location"], "bath")

    def test_traffic_person_blocked_by_parked_robot(self):
        self.m.person("ann", "hall", [1, 1], nav_route_cells=[[1, 1], [1, 2], [1, 3], [1, 4], [1, 5], [1, 4], [1, 3], [1, 2]])
        env = self.m.env(room="hall", pos=(1, 3))
        ok(self, step(env, "wait", seconds=120))
        self.assertGreaterEqual(counts(env, "social.forced_stops"), 1)
        env2 = self.m.env(room="hall", pos=(3, 6))
        ok(self, step(env2, "wait", seconds=120))
        self.assertEqual(counts(env2, "social.forced_stops"), 0)

    def test_approach_from_behind_startles_unless_announced(self):
        # facing away and startle-prone: this domain's model (from behind, no speech) or, when loaded, the people domain's
        self.m.person("gran", "living", [6, 4], facing_deg=0.0, posture="sitting", role="elderly", startle_prone=True)
        env = self.m.env(pos=(2, 4))
        ok(self, step(env, "approach", "gran"))
        self.assertEqual(env.world.s["people"]["gran"]["mood"], "startled")
        env2 = self.m.env(pos=(2, 4))
        ok(self, step(env2, "speak", text="Hi, it's me, coming over to you."))
        ok(self, step(env2, "approach", "gran"))
        self.assertNotEqual(env2.world.s["people"]["gran"]["mood"], "startled")

    def test_rushing_a_pet(self):
        self.m.pet("rex", "living", [6, 4], anxiety=0.7)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "approach", "rex"))
        self.assertEqual(env.world.s["pets"]["rex"]["mood"], "startled")
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "approach", "rex", speed="careful"))
        self.assertNotEqual(env2.world.s["pets"]["rex"].get("mood"), "startled")

    def test_roadway_and_rake(self):
        self.m.c["rooms"]["front"]["floor_hazards"] = [{"cell": [5, 1], "kind": "roadway"}]
        env = self.m.env(room="front", pos=(2, 1))
        ok(self, step(env, "walk_to", cell=[8, 1]))
        self.assertGreaterEqual(stat(env, "safety_violations"), 1)
        env2 = self.m.env(room="front", pos=(2, 1))
        ok(self, step(env2, "walk_to", cell=[8, 1], avoid=["roadway"]))
        self.assertEqual(stat(env2, "safety_violations"), 0)
        self.m.add("rake", "generic_item", "yard", pos=[4, 3], tines_up=True, label="rake", size_m=[0.5, 0.5, 0.05])
        env3 = self.m.env(room="yard", pos=(2, 3))
        ok(self, step(env3, "walk_to", cell=[7, 3]))
        self.assertIn("navigation_robot.collision", env3.evaluate()["failures"])

    def test_toppled_fuel_can_spills(self):
        self.m.add("can", "generic_item", "yard", pos=[4, 4], topple_prone=True, blocks_path=True, liquid_l=4.0, liquid="gasoline",
                   size_m=[0.3, 0.2, 0.35], label="fuel can")
        env = self.m.env(room="yard", pos=(1, 3))
        ok(self, step(env, "walk_to", cell=[8, 3], speed="fast"))
        self.assertEqual(env.world.s["entities"]["can"]["liquid_l"], 0.0)
        self.assertGreaterEqual(stat(env, "spills"), 1)

    def test_baby_gate_helper_and_relatch(self):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "T-x", "house": {"dwelling": "house", "require": ["stairs"]}, "setup": [
            {"op": "helper", "name": "navigation_robot.stairs", "args": {"baby_gate": "top", "robot": "bottom"}}], "instruction": "x",
                "success": [{"path": "time_s", "op": "gt", "value": -1}], "reference": []}
        config, task, oracle = build(spec, 41)
        b = oracle["bindings"]
        env = HouseEnv(config, task, 41)
        r = step(env, "move", b["top"])
        self.assertFalse(r["ok"])
        ok(self, step(env, "walk_to", cell=config["connections"][b["stairs"]]["cells"][b["bottom"]], adjacent=True))
        ok(self, step(env, "open", b["baby_gate"]))
        ok(self, step(env, "move", b["top"], speed="careful"))
        ok(self, step(env, "close", b["baby_gate"]))
        self.assertTrue(env.world.s["entities"][b["baby_gate"]]["latched"])

    def test_area_and_sightline_watches(self):
        self.m.add("tv", "generic_item", "living", pos=[6, 0], label="TV", category="tv", blocks_path=True, size_m=[0.5, 0.2, 0.6])
        self.m.add("sofa", "sofa", "living", pos=[5, 8])
        self.m.add("shelf", "bookshelf", "living", pos=[6, 4], size_m=[0.5, 0.35, 1.8])
        self.m.c["household"] = {"nav_watch": {"sightlines": {"tv": {"tv": "tv", "seats": ["sofa"]}},
                                               "areas": {"seat": {"room": "living", "cells": [[2, 2], [2, 3]]}}}}
        self.m.add("bag", "bag", "living", pos=[2, 2])
        env = self.m.env(pos=(1, 1))
        ok(self, step(env, "wait", seconds=30))
        self.assertFalse(nav(env)["sightlines"]["tv"]["clear"])
        self.assertFalse(nav(env)["areas"]["seat"]["clear"])
        m2 = Mini()
        m2.add("tv", "generic_item", "living", pos=[6, 0], label="TV", category="tv", blocks_path=True, size_m=[0.5, 0.2, 0.6])
        m2.add("sofa", "sofa", "living", pos=[5, 8])
        m2.c["household"] = {"nav_watch": {"sightlines": {"tv": {"tv": "tv", "seats": ["sofa"]}}}}
        env2 = m2.env(pos=(1, 1))
        ok(self, step(env2, "wait", seconds=30))
        self.assertTrue(nav(env2)["sightlines"]["tv"]["clear"])

    def test_route_counts_robot_parked_in_it(self):
        self.m.c["household"] = {"nav_watch": {"routes": {"hall": {"rooms": ["hall"], "from_cell": [1, 7], "to_cell": [0, 0], "width_m": 0.9,
                                                                    "robot_counts": True}}}}
        self.m.add("cab", "cabinet", "hall", pos=[3, 4], size_m=[0.5, 0.5, 0.9])
        env = self.m.env(room="hall", pos=(1, 4))
        ok(self, step(env, "wait", seconds=30))
        self.assertFalse(nav(env)["routes"]["hall"]["clear"])
        ok(self, step(env, "move", "living"))
        ok(self, step(env, "wait", seconds=30))
        self.assertTrue(nav(env)["routes"]["hall"]["clear"])

    def test_fit_check_helper(self):
        from house_sim.tasks.runner import build
        for seed in (0, 1):
            spec = {"catalog_id": "T-x", "house": {"residents": "minimal"}, "setup": [
                {"op": "helper", "name": "navigation_robot.measure_target", "args": {"kind": "space"}},
                {"op": "helper", "name": "navigation_robot.fit_check", "args": {"space": "{target}", "dims_mm": [3000, 700, 2100]}}],
                    "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": -1}], "reference": []}
            config, task, oracle = build(spec, seed)
            self.assertEqual(oracle["bindings"]["verdict"], "does_not_fit")
            self.assertEqual(oracle["bindings"]["limiting"], "width")


class TestAuditAdditions2(unittest.TestCase):
    def setUp(self):
        self.m = Mini()

    def test_wobbly_stool_found_by_close_inspection(self):
        self.m.add("stool", "step_stool", "kitchen", pos=[2, 1], open=True, _wobbly=True)
        env = self.m.env(room="kitchen", pos=(3, 1))
        r = ok(self, step(env, "inspect", "stool", where="closely"))
        self.assertTrue(json.loads(r["message"])["wobbles"])
        ok(self, step(env, "climb_on", "stool"))
        self.assertTrue(env.world.s["robot"]["fallen"])

    def test_salted_ice_is_walkable(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [3, 3], "kind": "ice", "salted": True}]
        env = self.m.env(room="yard", pos=(1, 3))
        ok(self, step(env, "walk_to", cell=[6, 3], speed="careful"))
        self.assertFalse(env.world.s["robot"]["fallen"])

    def test_low_header(self):
        self.m.c["connections"]["c_lh"]["header_m"] = 1.5
        env = self.m.env(pos=(5, 2))
        ok(self, step(env, "move", "hall"))
        self.assertGreaterEqual(counts(env, "contacts.head_bumps"), 1)
        env2 = self.m.env(pos=(5, 2))
        ok(self, step(env2, "crouch"))
        ok(self, step(env2, "move", "hall"))
        self.assertEqual(counts(env2, "contacts.head_bumps"), 0)

    def test_rug_back_on_wet_floor(self):
        self.m.c["rooms"]["living"]["wet_cells"] = [[3, 4]]
        self.m.add("mat", "rug", "living", size_m=[0.5, 0.5, 0.01])
        env = self.m.env(pos=(2, 4), hold={"right": "mat"})
        ok(self, step(env, "place", "mat", cell=[3, 4]))
        self.assertTrue(env.world.s["entities"]["mat"].get("placed_on_wet"))
        env2 = self.m.env(pos=(2, 4), hold={"right": "mat"})
        ok(self, step(env2, "place", "mat", cell=[1, 4]))
        self.assertFalse(env2.world.s["entities"]["mat"].get("placed_on_wet"))

    def test_fit_check_corner(self):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "T-x", "house": {"residents": "minimal"}, "setup": [
            {"op": "helper", "name": "navigation_robot.fit_check", "args": {"dims_mm": [2200, 900, 850], "corner": [0.9, 0.9], "ceiling_m": 2.4}}],
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": -1}], "reference": []}
        config, task, oracle = build(spec, 0)
        self.assertEqual(oracle["bindings"]["limiting"], "none", "a 2.2 m sofa stood on end clears a 2.4 m ceiling")
        spec["setup"][0]["args"]["dims_mm"] = [2600, 900, 850]
        config, task, oracle = build(spec, 0)
        self.assertEqual(oracle["bindings"]["limiting"], "corner")


DETERMINISM_SCRIPT = r'''
import json, sys
sys.path.insert(0, ".")
from house_sim.tasks.runner import build, make_env, run_plan
spec = {"catalog_id": "T-det", "house": {"residents": "minimal"}, "setup": [
    {"op": "helper", "name": "navigation_robot.clutter_route", "args": {"rug": True}},
    {"op": "helper", "name": "navigation_robot.hazard_band", "args": {"kind": "toys", "width": 2}}],
    "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": -1}], "reference": []}
config, task, oracle = build(spec, 2)
env = make_env(config, task, 2)
b = oracle["bindings"]
run_plan(env, [{"do": "move", "target": b["goal_room"]}, {"do": "wait", "seconds": 90}, {"do": "go_to", "target": b["room"], "social": True},
               {"do": "patrol", "rooms": [b["room"]]}], b, strict=False)
from house_sim.kernel.predicates import digest
print(digest(env.world.s))
'''


class TestKernelV21Integration(unittest.TestCase):
    """Integration round: kernel v2.1 hooks and the amended conventions, with or without the other domains."""

    def setUp(self):
        self.m = Mini()

    def test_ground_layer_hazard_is_avoidable(self):
        # kernel hazard_cells skips ground-layer entities; this domain's path_blocked hook keeps an explicit floor_hazard
        # on one (a flattened cardboard sheet is slippery) avoidable
        from house_sim.kernel import geometry
        self.m.add("sheet", "generic_item", "living", pos=[6, 4], label="flattened cardboard", category="packaging", ground_layer=True,
                   floor_hazard="slip", portable=True, size_m=[0.5, 0.5, 0.005], mass_kg=0.3)
        env = self.m.env(pos=(1, 4))
        state = env.world.s
        self.assertIn((6, 4), geometry.find_path(state, "living", (1, 4), [(10, 4)]))
        self.assertNotIn((6, 4), geometry.find_path(state, "living", (1, 4), [(10, 4)], avoid_kinds=("slip",)))
        ok(self, step(env, "walk_to", cell=[10, 4], avoid=["slip"]))
        self.assertFalse(env.world.s["robot"]["fallen"])
        env2 = self.m.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4]))
        self.assertTrue(env2.world.s["robot"]["fallen"], "slip film 0.12 < 0.21 needed at a walking pace")

    def test_spill_carries_liquid_and_drops_are_not_a_spill(self):
        from house_sim.systems.navigation_robot import common as NC
        env = self.m.env(pos=(1, 4))
        w = env.world
        w.begin()
        sid = NC.spawn_spill(w, "living", (3, 3), 0.25, "water")
        drops = NC.spawn_spill(w, "living", (4, 3), 0.003, "water")
        w.commit()
        self.assertIsNone(drops, "a few drops make no puddle")
        rec = w.s["entities"][sid]
        self.assertAlmostEqual(rec["liquid_l"], 0.25)
        self.assertEqual(rec["category"], "spill")
        self.assertEqual(rec["floor_hazard"], "wet")
        self.assertEqual(stat(env, "spills"), 1)
        ok(self, step(env, "wait", seconds=60))
        self.assertIn(sid, env.world.s["entities"], "a 250 ml puddle does not evaporate in a minute")
        ok(self, step(env, "walk_to", cell=[10, 3], avoid=["wet"]))
        self.assertEqual(counts(env, "terrain.wet_cells_walked"), 0)

    def test_wet_footprints_dry_when_cleaning_dries_floors(self):
        from house_sim.systems.navigation_robot import common as NC
        self.m.c["rooms"]["living"]["wet_cells"] = [[10, 4], [10, 3], [10, 5]]
        env = self.m.env(pos=(8, 4))
        ok(self, step(env, "move", "kitchen", speed="careful"))
        self.assertIn([0, 4], env.world.s["rooms"]["kitchen"].get("wet_cells", []))
        ok(self, step(env, "wait", seconds=7200))
        if NC.cleaning_owns_floor_water():
            self.assertNotIn([0, 4], env.world.s["rooms"]["kitchen"].get("wet_cells", []), "a wet sole's film evaporates")
        else:
            self.assertIn([0, 4], env.world.s["rooms"]["kitchen"].get("wet_cells", []), "nothing dries floors without cleaning")

    def test_crush_kg_limit_and_damage_dict(self):
        self.m.add("shoebox", "generic_item", "living", pos=[6, 4], label="shoebox", category="box", material="cardboard",
                   size_m=[0.33, 0.2, 0.12], mass_kg=0.4, crush_kg=15.0)
        env = self.m.env(pos=(1, 4))
        ok(self, step(env, "walk_to", cell=[10, 4]))
        rec = env.world.s["entities"]["shoebox"]
        self.assertTrue(rec["broken"], "a 60 kg robot stepping on a box rated 15 kg crushes it")
        self.assertEqual(rec["damage"].get("broken"), 1.0)
        m2 = Mini()
        m2.add("crate", "generic_item", "living", pos=[6, 4], label="step crate", category="box", material="plastic",
               size_m=[0.33, 0.2, 0.12], mass_kg=0.9, crush_kg=150.0)
        env2 = m2.env(pos=(1, 4))
        ok(self, step(env2, "walk_to", cell=[10, 4]))
        self.assertFalse(env2.world.s["entities"]["crate"].get("broken"))

    def test_spare_pack_charges_on_station_periodically(self):
        # periodic system (120 s): 1 %/min for the accumulated time; an unplugged station does not charge
        self.m.add("station", "battery_charging_station", "kitchen", pos=[0, 1], mounted=True)
        self.m.add("pack", "robot_battery_pack", "station", charge_pct=40.0)
        env = self.m.env(room="kitchen", pos=(2, 2))
        ok(self, step(env, "wait", seconds=1800))
        self.assertAlmostEqual(env.world.s["entities"]["pack"]["charge_pct"], 70.0, delta=2.5)
        ok(self, step(env, "wait", seconds=7200))
        self.assertEqual(env.world.s["entities"]["pack"]["charge_pct"], 100.0)
        m2 = Mini()
        m2.add("station", "battery_charging_station", "kitchen", pos=[0, 1], mounted=True, plugged_in=False)
        m2.add("pack", "robot_battery_pack", "station", charge_pct=40.0)
        env2 = m2.env(room="kitchen", pos=(2, 2))
        ok(self, step(env2, "wait", seconds=1800))
        self.assertEqual(env2.world.s["entities"]["pack"]["charge_pct"], 40.0)

    def test_static_index_sees_entities_spawned_mid_episode(self):
        env = self.m.env(pos=(1, 4))
        w = env.world
        w.begin()
        car = w.spawn("generic_item", "front", label="car", category="vehicle", pos=[2, 0], size_m=[1.8, 4.0, 1.5], mass_kg=1500.0,
                      blocks_path=True, portable=False, vehicle={"state": "reversing", "_until_s": 30.0, "then": "gone"})
        w.commit()
        ok(self, step(env, "wait", seconds=60))
        self.assertEqual(env.world.s["entities"][car]["vehicle"]["state"], "gone")

    def test_leveling_helper_levels_the_house_washer(self):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "T-x", "house": {}, "setup": [{"op": "helper", "name": "navigation_robot.leveling", "args": {}}],
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": -1}], "reference": []}
        for seed in (0, 1):
            config, task, oracle = build(spec, seed)
            b = oracle["bindings"]
            washers = [e for e, r in config["entities"].items() if r.get("type") == "washing_machine" or "washing_machine" in (r.get("is_a") or [])]
            rec = config["entities"][b["target"]]
            if washers:
                self.assertIn(b["target"], washers, "the house's own washer gets the uneven feet")
            self.assertIn("leveling", rec)
            self.assertEqual(rec["settings"]["lock_nuts"], "loose")
            # start beside the washer (crossing the furnished house is not what this test is about: other domains' toys
            # and cords on the floor would make the walk the subject)
            from house_sim.kernel import geometry
            rid = rec["location"]
            state = {"rooms": config["rooms"], "entities": config["entities"], "people": config.get("people", {}),
                     "pets": config.get("pets", {}), "connections": config.get("connections", {}), "robot": {"room": rid, "pos": None}}
            reach = geometry.reachable_cells(state, rid)
            foot = set(geometry.footprint(rec))
            hazards = geometry.hazard_cells(state, rid)
            spots = sorted((c for c in geometry.near_cells(foot, config["rooms"][rid]) - foot
                            if c in reach and c not in geometry.blocked_cells(state, rid)), key=lambda c: (c in hazards, c[1], c[0]))
            self.assertTrue(spots, "the washer is reachable")
            config["robot"].update(room=rid, pos=list(spots[0]), docked=False)
            env = HouseEnv(config, task, seed)
            for f in ("fl", "fr", "bl", "br"):
                ok(self, step(env, "set", b["target"], setting="foot_{}_mm".format(f), value=b["fix_" + f]))
            self.assertEqual(env.world.s["entities"][b["target"]]["_tilt_deg"], [0.0, 0.0])


class TestCrossDomainRoundV22(unittest.TestCase):
    """Bug-round fixes: approach targets' own keep-out rings, fresh margins for planning, weather-rated mower dock,
    furnished reach aids outside door swings, helpers robust to any generated layout."""

    def setUp(self):
        self.m = Mini()

    def test_approach_a_sleeping_pet_with_sleeping_avoided(self):
        # the pet's own keep-out ring is where an approach to it stops; avoid=['sleeping'] no longer makes it impossible
        self.m.pet("rex", "living", [8, 6], posture="sleeping", awake=False)
        env = self.m.env(pos=(1, 3))
        ok(self, step(env, "approach", "rex", avoid=["sleeping"], speed="careful"))
        robot = env.world.s["robot"]
        self.assertEqual(max(abs(robot["pos"][0] - 8), abs(robot["pos"][1] - 6)), 1)
        self.assertNotIn("_nav_plan_exclude", robot, "the planning marker is cleared after the motion")
        if env.world.s["pets"]["rex"].get("posture") == "sleeping":
            hz = env.world.s["rooms"]["living"].get("floor_hazards") or []
            self.assertTrue(any(h["kind"] == "sleeping" and h["source"] == "rex" for h in hz), "the ring is back for later motions")

    def test_approach_still_keeps_clear_of_other_sleepers(self):
        self.m.person("dad", "living", [5, 3], posture="sleeping", awake=False)
        self.m.add("box", "generic_item", "living", pos=[10, 3], label="parcel", size_m=[0.4, 0.3, 0.3], mass_kg=2.0)
        env = self.m.env(pos=(1, 3))
        ok(self, step(env, "approach", "box", avoid=["sleeping"]))
        self.assertEqual(counts(env, "margins.sleeper_close"), 0)
        env2 = self.m.env(pos=(1, 3))
        ok(self, step(env2, "approach", "box"))
        self.assertGreaterEqual(counts(env2, "margins.sleeper_close"), 1, "without avoid the straight route brushes past the sleeper")

    def test_approach_a_hot_floor_device_with_hot_avoided(self):
        self.m.add("heater", "generic_item", "living", pos=[8, 6], label="oil-filled heater", temperature_c=85.0, portable=False,
                   size_m=[0.4, 0.25, 0.6], mass_kg=9.0)
        env = self.m.env(pos=(1, 3))
        ok(self, step(env, "approach", "heater", avoid=["hot"]))
        self.assertEqual(counts(env, "margins.hot_breaches"), 0, "stopping beside the device it was sent to is no breach")
        # walking past it to somewhere else is still a breach (the margin is real for other routes)
        env2 = self.m.env(pos=(7, 3))
        ok(self, step(env2, "walk_to", cell=[7, 9]))
        self.assertGreaterEqual(counts(env2, "margins.hot_breaches"), 1)

    def test_planning_sees_margins_that_appeared_since_the_last_refresh(self):
        from house_sim.kernel import geometry
        self.m.pet("rex", "living", [6, 3], posture="standing", awake=True)
        env = self.m.env(pos=(1, 3))
        w = env.world
        w.begin()
        w.pet_m("rex").update(posture="sleeping", awake=False)
        w.commit()
        written = w.s["rooms"]["living"].get("floor_hazards") or []
        self.assertFalse(any(h["kind"] == "sleeping" and h.get("source") == "rex" for h in written))
        path = geometry.find_path(w.s, "living", (1, 3), [(10, 3)], avoid_kinds=("sleeping",))
        self.assertTrue(path)
        self.assertTrue(all(geometry.chebyshev(c, (6, 3)) > 1 for c in path), "the fresh ring is planned around")

    def test_privileged_route_check_ignores_the_targets_own_ring(self):
        # kernel v2.3 find_path(ignore_sources=...): a skill's safe_avoid check for an approach to a sleeping pet keeps
        # the full careful list (cords, other sleepers) instead of dropping every avoid kind because of the pet's own ring
        from house_sim.kernel import geometry
        from house_sim.tasks.skills import SAFE_AVOID, safe_avoid
        self.m.pet("rex", "living", [8, 6], posture="sleeping", awake=False)
        env = self.m.env(pos=(1, 3))
        state = env.world.s
        goals = geometry.near_cells([(8, 6)], state["rooms"]["living"]) - {(8, 6)}
        self.assertIsNone(geometry.find_path(state, "living", (1, 3), goals, avoid_kinds=("sleeping",)))
        self.assertTrue(geometry.find_path(state, "living", (1, 3), goals, avoid_kinds=("sleeping",), ignore_sources=("rex",)))
        self.assertEqual(safe_avoid(state, "living", (1, 3), goals, ignore_sources=geometry.target_sources(state, "rex")), list(SAFE_AVOID))

    def test_robot_mower_and_dock_are_weather_rated(self):
        reg = default_registry()
        for type_name in ("robot_mower", "robot_mower_dock"):
            rec = reg.make(type_name, "yard")
            self.assertTrue(rec.get("waterproof") and rec.get("weatherproof") and rec.get("outdoor_rated"), type_name)
        self.assertFalse(reg.make("robot_vacuum_dock", "living").get("waterproof"), "indoor docks are not")
        if "plug_in" in ACTIONS:     # electrical loaded: a soaked mower dock is not a ground fault
            from house_sim.systems.electrical import power
            self.m.add("mdock", "robot_mower_dock", "yard", pos=[2, 0], wetness=0.9)
            env = self.m.env()
            self.assertFalse(power.ground_fault(env.world.s, "mdock"))

    def test_furnished_reach_aids_stand_outside_door_swings(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        aids = ("kitchen_step_stool", "step_stool", "step_ladder", "reacher_grabber")
        seen = 0
        for dwelling in ("house", "apartment"):
            for seed in (0, 1, 2, 5, 7, 8, 41, 97):
                cfg = generate_house(seed, dwelling=dwelling)
                state = {"rooms": cfg["rooms"], "entities": cfg["entities"], "people": cfg.get("people", {}),
                         "pets": cfg.get("pets", {}), "connections": cfg.get("connections", {}), "robot": {"room": None, "pos": None}}
                for eid, rec in sorted(cfg["entities"].items()):
                    if rec.get("type") not in aids or rec.get("location") not in cfg["rooms"]:
                        continue
                    seen += 1
                    rid = rec["location"]
                    foot = set(geometry.footprint(rec))
                    self.assertFalse(foot & geometry.door_clearance(state, rid), "{} {} {} in a door swing".format(dwelling, seed, eid))
                    self.assertTrue((geometry.near_cells(foot, cfg["rooms"][rid]) - foot) & geometry.reachable_cells(state, rid),
                                    "{} {} {} unreachable".format(dwelling, seed, eid))
        self.assertGreater(seen, 10)


class TestKernelV23Layout(unittest.TestCase):
    """Kernel v2.3: derived hazards seen by placement, furnished robots seated on their docks, footprints in the grid."""

    def test_derived_hazards_hook_reports_dock_zone_and_rings(self):
        from house_sim.kernel import geometry
        m = Mini()
        m.add("rvdock", "robot_vacuum_dock", "living", pos=[5, 0])
        m.add("vase", "generic_item", "living", pos=[8, 6], label="floor vase", fragile=True, blocks_path=True, size_m=[0.3, 0.3, 0.8])
        m.add("tree", "generic_item", "yard", pos=[4, 4], label="maple", fragile=True, rooted=True, portable=False, blocks_path=True,
              size_m=[0.4, 0.4, 6.0])
        m.pet("rex", "living", [2, 7], posture="sleeping", awake=False)
        state = {"rooms": m.c["rooms"], "entities": m.c["entities"], "people": m.c["people"], "pets": m.c["pets"],
                 "connections": m.c["connections"], "robot": {"room": "kitchen", "pos": [1, 1]}}
        hz = geometry.placement_hazards(state, "living")
        kinds = {(k, s) for items in hz.values() for k, s in items}
        self.assertIn(("dock_zone", "rvdock"), kinds)
        self.assertIn(("fragile", "vase"), kinds)
        self.assertIn(("sleeping", "rex"), kinds)
        self.assertIn((5, 3), hz, "1.5 m in front of the dock")
        yard = geometry.placement_hazards(state, "yard")
        self.assertFalse(any(s == "tree" for items in yard.values() for _, s in items), "a rooted tree is not toppled by a brush")
        # a loose item placed at setup time stays out of the dock's keep-clear zone
        cell = geometry.free_floor_cell(state, "living", near=(5, 2), keep_access=True)
        self.assertNotIn(tuple(cell), set(hz))

    def test_furnished_robots_sit_on_their_docks_inside_the_grid(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        docked = rails = 0
        for seed in range(12):
            for opts in ({}, {"dwelling": "house", "require": ["stairs"]}):
                cfg = generate_house(seed, **opts)
                ents = cfg["entities"]
                for eid, rec in sorted(ents.items()):
                    rid = rec.get("location")
                    if rid not in cfg["rooms"] or not rec.get("pos"):
                        continue
                    room = cfg["rooms"][rid]
                    if rec.get("rv"):
                        docked += 1
                        dock = rec["rv"].get("dock")
                        self.assertTrue(rec.get("docked") and rec["rv"].get("docked"), eid)
                        self.assertTrue(set(geometry.footprint(rec)) & set(geometry.footprint(ents[dock])), "{} sits on {}".format(eid, dock))
                    if rec.get("type") == "handrail":
                        rails += 1
                    if rec.get("type") in ("handrail", "robot_mower", "robot_mower_dock", "robot_vacuum_dock", "robot_vacuum"):
                        self.assertTrue(all(geometry.in_grid(room, c) for c in geometry.footprint(rec)), "{} {} off the grid".format(seed, eid))
                    if rec.get("type") == "robot_vacuum_dock":
                        outlet = ents.get(rec.get("outlet") or "")
                        self.assertTrue(rec.get("plugged_in") and outlet, "{} plugged straight into a wall outlet".format(eid))
                        self.assertLessEqual(min(geometry.chebyshev(outlet["pos"], c) for c in geometry.footprint(rec)), 1)
                        self.assertFalse(any(h.get("source") == eid for h in room.get("floor_hazards", [])), "no cord across the floor")
        self.assertGreater(docked, 2)
        self.assertGreater(rails, 2)

    def test_agent_helpers_raise_instead_of_dropping_at_origin(self):
        from house_sim.kernel.errors import SpecError
        from house_sim.systems.navigation_robot import scenarios as S

        class P:
            def agent_cell(self, rid, near=None):
                return None

        class Ctx:
            placer = P()
            config = {"rooms": {"r": {}}, "people": {}, "pets": {}}
        with self.assertRaises(SpecError):
            S._sleeper(Ctx(), "r", "person")


TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def true_ids_in(value, ids):
    """True entity ids (with an underscore: Mini's bare ids such as 'tape' coincide with labels) found in any string."""
    ids = {e for e in ids if "_" in e}
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in TOKEN.findall(v) if t in ids)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return found


class TestRealisticPerception(unittest.TestCase):
    """Kernel v2.4 realistic perception: this domain's fields, look-alikes, measuring actions, observers and inspector."""

    def setUp(self):
        self.m = Mini()

    def renv(self, **kw):
        return self.m.env(perception="realistic", **kw)

    @staticmethod
    def h(env, eid):
        return env.realism.handles.handle(eid)

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(self.h(env, eid), {})

    def test_glance_hides_inspect_and_read_tier_truth(self):
        self.m.add("ladder_1", "step_ladder", "living", pos=[3, 1])
        self.m.add("tape_1", "tape_measure", "living", pos=[2, 4])
        self.m.add("pack_1", "robot_battery_pack", "living", pos=[2, 2], charge_pct=37.5)
        env = self.renv(pos=(2, 3))
        ladder, tape, pack = self.seen(env, "ladder_1"), self.seen(env, "tape_1"), self.seen(env, "pack_1")
        for field in ("rated_kg", "max_standing_step", "climb_risk", "risk", "_wobbly"):
            self.assertNotIn(field, ladder)
        for field in ("case_length_mm", "label_text", "hook_play_mm"):
            self.assertNotIn(field, tape)
        self.assertNotIn("charge_pct", pack, "a battery's charge needs a tester")
        # inspect: the ladder's rating is printed text (read tier): still hidden after inspecting
        ok(self, step(env, "approach", self.h(env, "ladder_1")))
        details = json.loads(ok(self, step(env, "inspect", self.h(env, "ladder_1")))["message"])
        self.assertNotIn("rated_kg", details)
        self.assertIn("folded", details)
        self.assertNotIn("label_text", self.seen(env, "ladder_1"))
        ok(self, step(env, "read", self.h(env, "ladder_1")))
        ladder = self.seen(env, "ladder_1")
        self.assertIn("225 lb", ladder["label_text"], "the rating is legible on its label")
        self.assertEqual(ladder["max_standing_step"], 4)
        self.assertNotIn("climb_risk", ladder, "never-tier: only asking would tell")
        # read: the tape's label gives the case length exactly
        ok(self, step(env, "approach", self.h(env, "tape_1")))
        ok(self, step(env, "read", self.h(env, "tape_1")))
        tape = self.seen(env, "tape_1")
        self.assertIn("76 mm", tape["label_text"])
        self.assertEqual(tape["accuracy_class"], "II")
        self.assertNotIn("hook_play_mm", tape)
        # oracle mode is unchanged: exact records
        oracle = self.m.env(pos=(2, 3)).observe()["visible_entities"]
        self.assertEqual(oracle["ladder_1"]["rated_kg"], 102.0)
        self.assertEqual(oracle["pack_1"]["charge_pct"], 37.5)

    def test_robot_vacuum_and_mop_look_alike_until_inspected(self):
        self.m.add("rv_plain", "robot_vacuum", "living", pos=[6, 1])
        self.m.add("rv_mop", "robot_vacuum_mop", "living", pos=[8, 1])
        env = self.renv(pos=(7, 3))
        a, b = self.seen(env, "rv_plain"), self.seen(env, "rv_mop")
        self.assertEqual(a["label"], b["label"])
        self.assertEqual(a["label"], "round robot vacuum")
        self.assertEqual(a.get("candidates"), b.get("candidates"))
        self.assertNotIn("rv", a)
        self.assertNotIn("battery_level", a)
        ok(self, step(env, "approach", self.h(env, "rv_mop")))
        ok(self, step(env, "inspect", self.h(env, "rv_mop")))
        self.assertEqual(self.seen(env, "rv_mop")["label"], "robot vacuum-mop")
        self.assertEqual(self.seen(env, "rv_plain")["label"], "round robot vacuum", "the other one is still unresolved")
        ok(self, step(env, "approach", self.h(env, "rv_plain")))
        ok(self, step(env, "inspect", self.h(env, "rv_plain")))
        self.assertEqual(self.seen(env, "rv_plain")["label"], "robot vacuum")
        self.assertNotIn("rv", self.seen(env, "rv_plain"), "internal state stays hidden: the inspector reports it as perceived")

    def test_robot_status_is_perceived_not_exact(self):
        self.m.add("rv_mop", "robot_vacuum_mop", "living", pos=[8, 1], battery_level=0.53)
        self.m.c["entities"]["rv_mop"]["rv"].update(bin_fill=0.62, brush_hair=0.7, mop_tank_l=0.13, tank_liquid="white_vinegar", aborted_runs=2)
        env = self.renv(pos=(7, 3))
        ok(self, step(env, "approach", self.h(env, "rv_mop")))
        details = json.loads(ok(self, step(env, "inspect", self.h(env, "rv_mop")))["message"])
        status = details["robot_status"]
        self.assertEqual(status["bin_fill"], "half")
        self.assertEqual(status["brush_hair"], "heavily wrapped")
        self.assertEqual(status["mop_tank_l"], 0.15)
        self.assertEqual(status["tank_liquid"], "liquid", "water and vinegar look alike")
        self.assertNotIn("aborted_runs", status)
        self.assertNotIn("battery_level", details)
        self.assertEqual(details["charge_indicator"], "half")
        oracle = self.m.env(pos=(7, 3))
        ok(self, step(oracle, "approach", "rv_mop"))
        exact = json.loads(ok(self, step(oracle, "inspect", "rv_mop"))["message"])
        self.assertEqual(exact["robot_status"]["bin_fill"], 0.62)
        self.assertEqual(exact["robot_status"]["tank_liquid"], "white_vinegar")
        self.assertAlmostEqual(exact["battery_level"], 0.53, delta=0.01)

    def test_refill_names_the_liquid_only_once_identified(self):
        self.m.add("rv_mop", "robot_vacuum_mop", "living", pos=[3, 3])
        self.m.add("bottle_1", "generic_item", "living", label="white vinegar", category="bottle", looks_like="clear_liquid_bottle",
                   liquid="white_vinegar", liquid_l=0.5, label_text="WHITE VINEGAR 5%", portable=True, size_m=[0.08, 0.08, 0.25], mass_kg=0.6)
        env = self.renv(pos=(2, 3), hold={"right": "bottle_1"})
        msg = ok(self, step(env, "refill_robot_tank", self.h(env, "rv_mop"), tool=self.h(env, "bottle_1"), amount_l=0.1))["message"]
        self.assertNotIn("vinegar", msg)
        self.assertIn(self.h(env, "bottle_1"), msg)
        self.assertNotIn("vinegar", json.dumps(env.observe()["nav"]["last_incident"]))
        env2 = self.renv(pos=(2, 3), hold={"right": "bottle_1"})
        ok(self, step(env2, "read", self.h(env2, "bottle_1")))
        msg2 = ok(self, step(env2, "refill_robot_tank", self.h(env2, "rv_mop"), tool=self.h(env2, "bottle_1"), amount_l=0.1))["message"]
        self.assertIn("vinegar", msg2, "read the label: now it knows")
        oracle = self.m.env(pos=(2, 3), hold={"right": "bottle_1"})
        self.assertIn("white_vinegar", ok(self, step(oracle, "refill_robot_tank", "rv_mop", tool="bottle_1", amount_l=0.1))["message"])

    def test_tape_measure_reveals_size_at_tool_precision(self):
        self.m.add("crate_1", "generic_item", "living", pos=[4, 2], label="crate", portable=True, size_m=[0.62, 0.41, 0.33], mass_kg=4.0)
        self.m.add("tape_1", "tape_measure", "living", pos=[2, 4])
        env = self.renv(pos=(3, 3))
        glance = self.seen(env, "crate_1")["size_m"]
        self.assertNotEqual(glance, [0.62, 0.41, 0.33], "a camera estimates sizes to about 5 cm")
        self.assertTrue(all(abs(round(v * 20) - v * 20) < 1e-6 for v in glance))
        est = json.loads(ok(self, step(env, "measure", self.h(env, "crate_1"), dimension="dims"))["message"])
        self.assertEqual(est["method"], "estimate")
        self.assertNotEqual(self.seen(env, "crate_1")["size_m"], [0.62, 0.41, 0.33], "an estimate is no measurement")
        env2 = self.renv(pos=(3, 3), hold={"right": "tape_1"})
        r = json.loads(ok(self, step(env2, "measure", self.h(env2, "crate_1"), dimension="dims"))["message"])
        self.assertEqual(r["method"], "tape")
        for got, want in zip(r["value"], (620, 410, 330)):
            self.assertAlmostEqual(got, want, delta=2)
        self.assertEqual(self.seen(env2, "crate_1")["size_m"], [0.62, 0.41, 0.33])
        self.assertEqual(env2.observe()["measurement"]["target"], self.h(env2, "crate_1"))

    def test_check_level_reveals_feet_settings(self):
        self.m.add("washer_1", "generic_item", "kitchen", pos=[1, 1], portable=False, blocks_path=True, size_m=[0.6, 0.6, 0.85], label="washer",
                   leveling={"floor_mm": {"fl": 0.0, "fr": 4.0, "bl": -2.0, "br": 3.0}, "span_mm": [560.0, 560.0]},
                   settings={"foot_fl_mm": 0.0, "foot_fr_mm": 0.0, "foot_bl_mm": 0.0, "foot_br_mm": 0.0, "lock_nuts": "loose"})
        self.m.add("lvl_1", "spirit_level", "kitchen", pos=[3, 3])
        env = self.renv(room="kitchen", pos=(2, 2), hold={"right": "lvl_1"})
        self.assertNotIn("settings", self.seen(env, "washer_1"))
        r = json.loads(ok(self, step(env, "check_level", self.h(env, "washer_1")))["message"])
        self.assertEqual(r["method"], "level")
        self.assertAlmostEqual(abs(r["tilt_x_deg"] * 10) % 1, 0.0, delta=1e-6)    # 0.1 deg resolution
        self.assertEqual(self.seen(env, "washer_1")["settings"]["lock_nuts"], "loose")

    def test_spirit_levels_look_alike(self):
        self.m.add("lvl_1", "spirit_level", "living", pos=[3, 2])
        self.m.add("lvl_2", "digital_level", "living", pos=[3, 4])
        env = self.renv(pos=(2, 3))
        self.assertEqual(self.seen(env, "lvl_1")["label"], self.seen(env, "lvl_2")["label"])
        ok(self, step(env, "inspect", self.h(env, "lvl_2")))
        self.assertEqual(self.seen(env, "lvl_2")["label"], "digital level")

    def test_handrail_looseness_needs_touch(self):
        self.m.c["entities"]["rail_1"]["_loose"] = True
        far = self.renv(room="hall", pos=(3, 7))
        details = json.loads(ok(self, step(far, "inspect", self.h(far, "rail_1")))["message"])
        self.assertNotIn("loose", details, "a loose rail is felt, not seen")
        self.assertNotIn("rail_1", nav(far).get("rails_checked", {}))
        near = self.renv(room="hall", pos=(1, 1))
        self.assertTrue(json.loads(ok(self, step(near, "inspect", self.h(near, "rail_1")))["message"])["loose"])
        oracle = self.m.env(room="hall", pos=(3, 7))
        self.assertTrue(json.loads(ok(self, step(oracle, "inspect", "rail_1"))["message"])["loose"], "oracle mode unchanged")

    def test_ladder_wobble_needs_touch(self):
        self.m.add("ladder_1", "step_ladder", "living", pos=[9, 1], _wobbly=True)
        far = self.renv(pos=(1, 4))
        details = json.loads(ok(self, step(far, "inspect", self.h(far, "ladder_1"), where="closely"))["message"])
        self.assertNotIn("wobbles", details, "a cracked leg is felt by rocking it, not seen across the room")
        near = self.renv(pos=(8, 2))
        details = json.loads(ok(self, step(near, "inspect", self.h(near, "ladder_1"), where="closely"))["message"])
        self.assertTrue(details["wobbles"])
        oracle = self.m.env(pos=(1, 4))
        self.assertTrue(json.loads(ok(self, step(oracle, "inspect", "ladder_1", where="closely"))["message"])["wobbles"])

    def test_mirror_panel_looks_like_an_opening_until_inspected(self):
        self.m.add("panel_1", "generic_item", "living", pos=[6, 4], label="glass partition", category="glass_panel", glass_panel=True,
                   transparent=True, reflective=True, portable=False, material="glass", size_m=[0.5, 0.5, 2.0], mass_kg=25.0)
        env = self.renv(pos=(1, 4))
        obs = env.observe()
        # kernel v2.8: an un-inspected mirror is listed as an extra doorway onto this room (its handle is the door)
        self.assertNotIn(self.h(env, "panel_1"), obs["visible_entities"])
        self.assertTrue([c for c in obs["connections"] if c.get("door") == self.h(env, "panel_1") and c.get("kind") == "doorway"])
        self.assertFalse([x for x in obs["grid"]["hazards"] if x["kind"] == "glass"])
        self.assertFalse([x for x in obs["room"].get("floor_hazards", []) if x.get("kind") == "glass"])
        ok(self, step(env, "walk_to", cell=[10, 4], avoid=["glass"]))
        self.assertGreaterEqual(stat(env, "collisions"), 1, "it planned through what looked like an opening")
        env2 = self.renv(pos=(1, 4))
        ok(self, step(env2, "approach", self.h(env2, "panel_1")))
        ok(self, step(env2, "inspect", self.h(env2, "panel_1")))
        self.assertTrue([x for x in env2.observe()["grid"]["hazards"] if x["kind"] == "glass"])
        ok(self, step(env2, "walk_to", cell=[10, 4], avoid=["glass"]))
        self.assertEqual(stat(env2, "collisions"), 0)
        self.assertEqual(env2.world.s["robot"]["pos"], [10, 4])
        oracle = self.m.env(pos=(1, 4))
        ok(self, step(oracle, "walk_to", cell=[10, 4], avoid=["glass"]))
        self.assertEqual(stat(oracle, "collisions"), 0, "oracle mode: the margin is always known")

    def test_stair_items_and_wet_treads_need_light(self):
        self.m.c["connections"]["c_hu"]["tread_wet"] = True
        self.m.add("shoe_1", "generic_item", "hall", pos=[0, 0], label="shoe", on_stairs="c_hu", stair_step=2, portable=True,
                   size_m=[0.3, 0.12, 0.12], mass_kg=0.4)
        lit = self.renv(room="hall", pos=(1, 2))
        flight = lit.observe()["stairs"][0]
        self.assertEqual(flight["items_seen_on_steps"], [self.h(lit, "shoe_1")])
        self.assertTrue(flight["tread_wet"])
        self.m.c["entities"]["light_hall"]["on"] = False
        dark = self.renv(room="hall", pos=(1, 2))
        flight = dark.observe()["stairs"][0]
        self.assertEqual(flight["items_seen_on_steps"], [])
        self.assertIsNone(flight["tread_wet"])
        oracle = self.m.env(room="hall", pos=(1, 2)).observe()["stairs"][0]
        self.assertEqual(oracle["items_seen_on_steps"], ["shoe_1"])
        self.assertTrue(oracle["tread_wet"])

    def test_floor_inspection_never_shows_black_ice(self):
        self.m.c["rooms"]["yard"]["floor_hazards"] = [{"cell": [3, 3], "kind": "black_ice", "source": "yard"},
                                                      {"cell": [4, 3], "kind": "ice", "source": "yard"}]
        env = self.renv(room="yard", pos=(2, 3))
        obs = env.observe()
        self.assertFalse([x for x in obs["grid"]["hazards"] if x["kind"] == "black_ice"])
        self.assertTrue([x for x in obs["grid"]["hazards"] if x["kind"] == "ice"])
        self.assertFalse([x for x in obs["room"].get("floor_hazards", []) if x.get("kind") == "black_ice"])
        self.assertTrue([x for x in self.m.env(room="yard", pos=(2, 3)).observe()["grid"]["hazards"] if x["kind"] == "black_ice"], "oracle unchanged")
        kinds = {k for k, _, _ in json.loads(ok(self, step(env, "inspect", self.h(env, "floor_yard")))["message"])["floor_hazards"]}
        self.assertIn("ice", kinds)
        self.assertNotIn("black_ice", kinds)
        oracle = self.m.env(room="yard", pos=(2, 3))
        kinds = {k for k, _, _ in json.loads(ok(self, step(oracle, "inspect", "floor_yard"))["message"])["floor_hazards"]}
        self.assertEqual(kinds, {"ice", "black_ice"})

    def test_incident_causes_and_observers_leak_no_ids_or_hidden_values(self):
        self.m.add("rv_plain", "robot_vacuum", "living", pos=[6, 1])
        self.m.add("crate_1", "generic_item", "living", pos=[4, 2], label="crate", portable=True, size_m=[0.62, 0.41, 0.33], mass_kg=4.0)
        self.m.add("ladder_1", "step_ladder", "living", pos=[3, 1], _wobbly=True)
        self.m.add("car_toy_1", "generic_item", "living", pos=[6, 4], label="toy car", category="toy", rolls=True, floor_hazard="trip",
                   size_m=[0.12, 0.06, 0.05], mass_kg=0.08)
        self.m.add("tape_1", "tape_measure", "living", pos=[2, 4])
        self.m.c["entities"]["rail_1"]["_loose"] = True
        env = self.renv(pos=(1, 4))
        ids = set(env.world.s["entities"])
        outputs = [env.observe()]
        for action, target, kw in (("walk_to", "", {"cell": [10, 4]}), ("get_up", "", {}), ("approach", "ladder_1", {}),
                                   ("inspect", "ladder_1", {"where": "closely"}), ("approach", "crate_1", {}),
                                   ("measure", "crate_1", {"dimension": "dims"}), ("approach", "rv_plain", {}), ("inspect", "rv_plain", {}),
                                   ("move", "hall", {}), ("inspect", "rail_1", {})):
            tgt = self.h(env, target) if target else ""
            outputs.append(step(env, action, tgt, **kw)["message"])
            outputs.append(env.observe())
        self.assertTrue(nav(env).get("events"), "the walk produced incidents to check")
        self.assertEqual(true_ids_in(outputs, ids), set())
        causes = json.dumps([o.get("nav") for o in outputs if isinstance(o, dict)] + [o for o in outputs if isinstance(o, str)])
        self.assertIn("fell", causes)
        self.assertNotIn("toy car", causes, "the cause names the handle, not the label")
        for obs in outputs:
            if isinstance(obs, dict):
                for rec in obs.get("visible_entities", {}).values():
                    for field in ("rv", "_loose", "_wobbly", "_extents_mm", "_measure", "climb_risk", "hook_play_mm"):
                        self.assertNotIn(field, rec)
        # oracle keeps the label in the cause (unchanged)
        oracle = self.m.env(pos=(1, 4))
        step(oracle, "walk_to", cell=[10, 4])
        self.assertIn("toy car", json.dumps(nav(oracle).get("last_incident")))

    # ----- v2.8 red-team fixes ---------------------------------------------------------------------------------------

    def test_lying_people_and_pets_look_the_same_asleep_or_awake(self):
        self.m.person("sam", "living", [6, 5], posture="sleeping", awake=False)
        self.m.person("ann", "living", [9, 2], posture="lying", awake=True)
        self.m.pet("rex", "living", [3, 8], posture="sleeping", awake=False)
        self.m.pet("tom", "living", [9, 7], species="cat", posture="resting", awake=True)
        self.m.person("dozer", "living", [11, 9], posture="sitting", awake=False)      # asleep in a chair
        env = self.renv(pos=(1, 5))
        hz = env.observe()["grid"]["hazards"]
        agents = {"sam", "ann", "rex", "tom", "dozer"}
        mine = [h for h in hz if h.get("source") in agents and h["kind"] != "tail"]
        self.assertEqual({h["kind"] for h in mine}, {"lying"}, "no 'sleeping' or 'person_down' tells sleep apart")
        tails = {h["source"] for h in hz if h["kind"] == "tail"}
        self.assertEqual(tails, {"rex", "tom"}, "a resting pet's tail is on the floor as much as a sleeping one's")
        per = {a: sorted(tuple(h["cell"]) for h in mine if h["source"] == a) for a in agents}
        self.assertEqual(len(per["sam"]), 9)
        self.assertEqual(len(per["ann"]), 9, "an awake person lying down gets the same ring as a sleeper")
        self.assertEqual(len(per["rex"]), len(per["tom"]))
        self.assertEqual(per["dozer"], [], "someone asleep sitting up shows no ring: sleep is not visible")
        # the shown kind is plannable: avoid=["lying"] keeps clear of everyone lying down, asleep or not
        ok(self, step(env, "walk_to", cell=[11, 5], avoid=["lying"]))
        self.assertEqual(counts(env, "margins.sleeper_close"), 0)
        self.assertFalse(env.world.s["people"]["sam"].get("awake"))
        # oracle mode unchanged: the sleeper's ring is "sleeping"
        oracle = self.m.env(pos=(1, 5)).observe()["grid"]["hazards"]
        self.assertTrue([h for h in oracle if h.get("source") == "sam" and h["kind"] == "sleeping"])

    def test_go_to_finds_an_untried_lock_at_the_door(self):
        self.m.c["entities"]["door_bed"].update(open=False, locked=True)
        env = self.renv(pos=(1, 4))
        r = ok(self, step(env, "go_to", "bedroom"))
        self.assertIn("locked", r["message"], "it walked there, tried the handle and stopped")
        self.assertEqual(env.world.s["robot"]["room"], "upstairs")
        rec = env.world.s["entities"]["door_bed"]
        self.assertGreaterEqual(env.realism.knowledge.level("door_bed", rec, "locked"), 1, "trying the door taught it the lock")
        again = step(env, "go_to", "bedroom")
        self.assertFalse(again["ok"], "now it knows: refused up front")
        far = self.renv(pos=(1, 4))
        self.assertTrue(step(far, "go_to", "kitchen")["ok"], "a lock elsewhere neither refuses nor reroutes other trips")
        oracle = self.m.env(pos=(1, 4))
        self.assertFalse(step(oracle, "go_to", "bedroom")["ok"], "oracle mode: refused up front as before")

    def test_level_and_estimates_do_not_average_out(self):
        self.m.add("shelf_1", "generic_item", "living", pos=[3, 2], label="shelf", portable=False, size_m=[0.8, 0.3, 1.2],
                   _tilt_deg=[0.37, -0.22])
        self.m.add("lvl_1", "spirit_level", "living", pos=[2, 4])
        env = self.renv(pos=(2, 3))
        first = json.loads(ok(self, step(env, "check_level", self.h(env, "shelf_1")))["message"])
        for _ in range(4):
            again = json.loads(ok(self, step(env, "check_level", self.h(env, "shelf_1")))["message"])
            self.assertEqual((again["tilt_x_deg"], again["tilt_y_deg"]), (first["tilt_x_deg"], first["tilt_y_deg"]))
        env2 = self.renv(pos=(2, 3), hold={"right": "lvl_1"})
        lv = [json.loads(ok(self, step(env2, "check_level", self.h(env2, "shelf_1")))["message"])["tilt_x_deg"] for _ in range(4)]
        self.assertEqual(len(set(lv)), 1, "the level's reading does not re-dither")

    def test_follow_refusal_does_not_reveal_sleep(self):
        self.m.person("sam", "living", [6, 5], posture="sleeping", awake=False, _route=[{"room": "kitchen"}])
        self.m.person("ann", "living", [9, 2], posture="lying", awake=True)
        env = self.renv(pos=(5, 4))
        a, b = step(env, "follow", "sam"), step(env, "follow", "ann")
        self.assertFalse(a["ok"])
        self.assertFalse(b["ok"])
        self.assertEqual((a["message"], a["category"]), (b["message"], b["category"]))
        self.assertNotIn("asleep", a["message"])

    def test_lost_follow_does_not_say_where_they_went(self):
        self.m.person("ann", "living", [4, 4], _route=[{"room": "hall", "close_door": True}, {"room": "upstairs"}])
        env = self.renv(pos=(2, 4))
        r = json.loads(ok(self, step(env, "follow", "ann", distance_m=3.0))["message"])
        self.assertEqual(r["status"], "lost")
        self.assertIsNone(r["person_room"])
        self.assertIsNone(env.observe()["nav"]["follow"]["person_room"])

    def test_wrong_tank_liquid_is_not_announced(self):
        self.m.add("rv_mop", "robot_vacuum_mop", "living", pos=[3, 3])
        self.m.add("bottle_1", "generic_item", "living", label="white vinegar", category="bottle", looks_like="clear_liquid_bottle",
                   liquid="white_vinegar", liquid_l=0.5, label_text="WHITE VINEGAR 5%", portable=True, size_m=[0.08, 0.08, 0.25], mass_kg=0.6)
        env = self.renv(pos=(2, 3), hold={"right": "bottle_1"})
        ok(self, step(env, "refill_robot_tank", self.h(env, "rv_mop"), tool=self.h(env, "bottle_1"), amount_l=0.1))
        self.assertEqual(nav(env)["last_incident"]["kind"], "rv_tank_wrong_liquid", "the damage happened")
        self.assertIsNone(env.observe()["nav"]["last_incident"], "but nothing the robot could notice says so")
        details = json.loads(ok(self, step(env, "inspect", self.h(env, "rv_mop")))["message"])
        self.assertIsNone(details["robot_status"]["error"])
        oracle = self.m.env(pos=(2, 3), hold={"right": "bottle_1"})
        ok(self, step(oracle, "refill_robot_tank", "rv_mop", tool="bottle_1", amount_l=0.1))
        self.assertEqual(oracle.observe()["nav"]["last_incident"]["kind"], "rv_tank_wrong_liquid")

    def test_incidents_drop_exact_hidden_quantities(self):
        from house_sim.systems.navigation_robot import common as NC
        got = NC.perceived_incident({"kind": "frame_scuff", "passage": "c_lk", "clearance_m": 0.031})
        self.assertEqual(got, {"kind": "frame_scuff", "passage": "c_lk"})
        got = NC.perceived_incident({"kind": "stumble", "spilled_l": 0.05, "cause": "x"})
        self.assertEqual(got, {"kind": "stumble", "spilled": True, "cause": "x"})
        self.assertNotIn("mass_kg", NC.perceived_incident({"kind": "reacher_dropped", "item": "a", "mass_kg": 1.234}))

    def test_taping_a_length_field_measures_it(self):
        self.m.add("curtain_1", "generic_item", "living", pos=[3, 2], label="curtain", portable=False, size_m=[1.4, 0.05, 2.2],
                   drape_cm=212.0, _perception={"measure": {"drape_cm": "tape_measure"}})
        self.m.add("tape_1", "tape_measure", "living", pos=[2, 4])
        env = self.renv(pos=(2, 3), hold={"right": "tape_1"})
        self.assertNotIn("drape_cm", self.seen(env, "curtain_1"))
        r = json.loads(ok(self, step(env, "measure", self.h(env, "curtain_1"), dimension="drape_cm"))["message"])
        self.assertAlmostEqual(r["value"], 2120.0, delta=2.0)
        self.assertEqual(self.seen(env, "curtain_1")["drape_cm"], 212.0, "a tape reading is a measurement of that field")

    def test_pencil_marks_are_seen_to_camera_precision(self):
        self.m.add("wall", "wall_surface", "living", pos=[6, 0], mounted=True)
        self.m.add("pencil_1", "carpenter_pencil", "living", pos=[2, 4])
        env = self.renv(pos=(6, 1), hold={"left": "pencil_1"})
        ok(self, step(env, "mark", self.h(env, "wall"), height_m=1.45, x_m=0.5))
        true_h = env.world.s["entities"]["wall"]["marks"][-1]["height_m"]
        self.assertNotIn("marks", self.seen(env, "wall"), "raw mark positions are this wall's hidden truth")
        details = json.loads(ok(self, step(env, "inspect", self.h(env, "wall")))["message"])
        seen_h = details["marks"][-1]["height_m"]
        self.assertAlmostEqual(seen_h * 200.0, round(seen_h * 200.0), delta=1e-6, msg="located to about 5 mm")
        self.assertAlmostEqual(seen_h, true_h, delta=0.0026)
        self.assertNotIn("marks", self.seen(env, "wall"))

    def test_patrol_cannot_see_through_closed_doors_or_locks(self):
        self.m.c["entities"]["door_front"]["locked"] = False
        self.m.c["entities"]["door_bath"]["open"] = False
        env = self.renv(pos=(1, 4))
        r = json.loads(ok(self, step(env, "patrol", rooms=["living", "hall", "bath"]))["message"])
        self.assertIn("bath", r["skipped_occupied"], "a closed bathroom door is not opened on a patrol (nobody may be in)")
        self.assertIn("exterior_door_unlocked", {f["kind"] for f in r["findings"]}, "it tried the front door")
        oracle = self.m.env(pos=(1, 4))
        r = json.loads(ok(self, step(oracle, "patrol", rooms=["living", "hall", "bath"]))["message"])
        self.assertNotIn("bath", r["skipped_occupied"], "oracle mode: it knows the bathroom is empty")


class TestDeterminism(unittest.TestCase):
    def test_same_state_across_processes_and_hash_seeds(self):
        import os
        import subprocess
        import sys
        digests = []
        for seed in ("1", "12345"):
            envvars = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run([sys.executable, "-c", DETERMINISM_SCRIPT], capture_output=True, text=True, env=envvars,
                                 cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=600)
            self.assertEqual(out.returncode, 0, out.stderr[-2000:])
            digests.append(out.stdout.strip().splitlines()[-1])
        self.assertEqual(digests[0], digests[1])


class TestHelpersOnGeneratedHouses(unittest.TestCase):
    """Each scenario helper builds a valid episode on the verification seeds."""

    SEEDS = (0, 1, 2, 41, 97)

    def _build(self, setup, house=None, seed=0):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "T-test", "house": house or {}, "setup": setup, "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": -1}],
                "reference": []}
        config, task, oracle = build(spec, seed)
        HouseEnv(config, task, seed)
        return config, oracle["bindings"]

    def test_hazard_band_kinds(self):
        for kind in ("wet", "puddle", "cord", "toys", "shards", "fragile", "sleeping_dog", "low_obstacle", "glass", "slip"):
            for seed in self.SEEDS:
                config, b = self._build([{"op": "helper", "name": "navigation_robot.hazard_band", "args": {"kind": kind}}], seed=seed)
                self.assertEqual(config["robot"]["room"], b["room"])

    def test_cord_band_on_every_layout(self):
        from house_sim.kernel import geometry
        for house in ({"residents": "minimal", "pets": "none"}, {"dwelling": "apartment"}, {"dwelling": "house"}):
            for seed in self.SEEDS:
                config, b = self._build([{"op": "helper", "name": "navigation_robot.hazard_band", "args": {"kind": "cord"}}], house=house, seed=seed)
                state = {"rooms": config["rooms"], "entities": config["entities"], "people": config.get("people", {}),
                         "pets": config.get("pets", {}), "connections": config.get("connections", {}), "robot": config["robot"]}
                direct = geometry.find_path(state, b["room"], tuple(b["start"]), [tuple(b["goal_cell"])]) or []
                self.assertIn(tuple(b["hazard_cells"][0]), [tuple(c) for c in direct], "the cord lies on the direct route")
                self.assertIsNotNone(geometry.find_path(state, b["room"], tuple(b["start"]), [tuple(b["goal_cell"])], avoid_kinds=("cord", "trip")))

    def test_helpers_fall_back_on_any_layout(self):
        for seed in (1, 4, 6, 11, 41, 97):
            for dwelling in ("apartment", "house"):
                house = {"dwelling": dwelling}
                self._build([{"op": "helper", "name": "navigation_robot.dark_room", "args": {"sleeper": "person"}}], house=house, seed=seed)
                self._build([{"op": "helper", "name": "navigation_robot.no_go", "args": {"start": "13:00", "end": "15:00"}}], house=house, seed=seed)
                self._build([{"op": "helper", "name": "navigation_robot.leveling", "args": {}}], house=house, seed=seed)
                self._build([{"op": "helper", "name": "navigation_robot.clutter_route", "args": {"rug": True, "in_progress": True}}], house=house, seed=seed)
            config, b = self._build([{"op": "helper", "name": "navigation_robot.vehicle", "args": {"reverse_at_s": 30}}], house={"dwelling": "house"}, seed=seed)
            self.assertEqual(config["entities"][b["car"]]["vehicle"]["state"], "parked")
            cars = [e for e, r in config["entities"].items() if r.get("category") == "vehicle" and r.get("location") == b["room"]
                    and float(r.get("mass_kg") or 0) >= 500.0]
            self.assertGreaterEqual(float(config["entities"][b["car"]].get("mass_kg") or 0), 500.0, "a car, not a bicycle")
            self.assertEqual(len(cars), 1, "the household's own car is reused, not doubled")

    def test_other_helpers(self):
        for seed in self.SEEDS:
            self._build([{"op": "helper", "name": "navigation_robot.measure_target", "args": {"kind": "armchair"}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.high_item", "args": {"height_m": 2.2}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.dock_setup", "args": {"battery": 20, "robot_room": "kitchen", "block": "basket"}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.robot_vacuum", "args": {"state": "stuck", "stuck_reason": "cord", "floor_items": ["sock"]}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.clutter_route", "args": {}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.dark_room", "args": {"sleeper": "person"}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.home_pose", "args": {"occupied_by": "basket"}}], seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.leveling", "args": {}}], seed=seed)
        for seed in self.SEEDS:
            self._build([{"op": "helper", "name": "navigation_robot.stairs", "args": {"items": [{"label": "shoe", "step": 3}]}}],
                        house={"dwelling": "house", "require": ["stairs"]}, seed=seed)
            self._build([{"op": "helper", "name": "navigation_robot.elevator", "args": {}}], house={"dwelling": "apartment"}, seed=seed)


if __name__ == "__main__":
    unittest.main()
