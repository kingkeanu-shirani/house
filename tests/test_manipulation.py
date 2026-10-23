"""Manipulation domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=manipulation python -m unittest tests/test_manipulation.py
Strict journal: HOUSE_SIM_DOMAINS=manipulation HOUSE_SIM_STRICT=1 python -m unittest tests/test_manipulation.py
The tests build small explicit houses from core types (plus manipulation types) so they also
run when all domains are loaded.
"""
import copy
import json
import re
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.predicates import check
from house_sim.kernel.registry import default_registry

load_domains()


class Mini:
    """A small explicit house: living (hardwood), kitchen (tile), garage (concrete), a sloped
    driveway down to the street, a lawn, and an upstairs landing reached by stairs."""

    def __init__(self):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0], "floor_material": "hardwood"},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0], "floor_material": "tile"},
            "garage": {"type": "garage", "size_m": [6.0, 6.0], "floor_material": "concrete"},
            "drive": {"type": "driveway", "size_m": [3.5, 10.0], "floor_material": "concrete", "outdoor": True, "slope_deg": 6, "downhill": "south"},
            "street": {"type": "sidewalk", "size_m": [10.0, 1.5], "floor_material": "concrete", "outdoor": True},
            "yard": {"type": "backyard", "size_m": [10.0, 8.0], "floor_material": "grass", "outdoor": True},
            "upstairs": {"type": "hallway", "size_m": [2.0, 4.0], "floor_material": "carpet", "floor": 1},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}, "width_m": 0.9},
            "c_lg": {"rooms": ["living", "garage"], "cells": {"living": [4, 11], "garage": [4, 0]}, "width_m": 0.9},
            "c_gd": {"rooms": ["garage", "drive"], "cells": {"garage": [4, 11], "drive": [3, 0]}, "width_m": 2.4, "kind": "garage"},
            "c_ds": {"rooms": ["drive", "street"], "cells": {"drive": [3, 19], "street": [3, 0]}, "width_m": 3.0},
            "c_ky": {"rooms": ["kitchen", "yard"], "cells": {"kitchen": [7, 9], "yard": [0, 0]}, "width_m": 0.9},
            "c_lu": {"rooms": ["living", "upstairs"], "cells": {"living": [0, 6], "upstairs": [0, 0]}, "width_m": 1.0, "kind": "stairs", "steps": 14},
        }
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"},
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

    def add(self, eid, type_name, location, **props):
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["rooms"] and "pos" not in props:
            rec["pos"] = [2, 2]
        if location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
        self.c["entities"][eid] = rec
        return eid

    def person(self, pid, room, pos, **props):
        rec = {"label": pid, "role": "adult", "authority": 2, "location": room, "pos": list(pos), "awake": True, "consent": True,
               "permissions": [], "preferences": {}}
        rec.update(props)
        self.c["people"][pid] = rec

    def env(self, task=None, room="living", pos=(1, 3), hold=None, perception=None, camera=None):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos)}
        if hold:
            for hand, eid in hold.items():
                c["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid
                rec = c["entities"][eid]
                rec.update(location="robot", relation="held", held_hand=hand)
                for key in ("pos", "spos", "shelf"):
                    rec.pop(key, None)
        if camera:
            return HouseEnv(c, task, perception=perception, camera=camera)
        return HouseEnv(c, task, perception=perception)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def results(env, goal):
    return ((env.world.s.get("manipulation") or {}).get("results") or {}).get(goal, {})


# ======================================================================================
class RegistrationTests(unittest.TestCase):
    def test_owned_verbs_helpers_and_skills_registered(self):
        from house_sim.systems.manipulation import VERBS
        for verb in VERBS:
            self.assertIn(verb, ACTIONS, verb)
            self.assertIn("manipulation", ACTIONS[verb].fn.__module__)
        for name in ("manipulation.goal", "manipulation.home_poses", "manipulation.make_stack", "manipulation.hold_job"):
            self.assertIn(name, SCENARIO_HELPERS)
        for name in ("manipulation.carry", "manipulation.give", "manipulation.retrieve_from_stack"):
            self.assertIn(name, SKILLS)

    def test_generated_houses_stay_valid_with_domain_items(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        for seed in (0, 1, 2, 41, 97):
            env = HouseEnv(generate_house(seed), seed=seed)
            state = env.world.s
            mine = [e for e, r in state["entities"].items() if r.get("type") in ("vase", "picture_frame", "dumbbell", "toy_block", "chip_clip", "trivet", "yoga_mat", "hand_truck")]
            self.assertLess(len(mine), 80)
            for rid in state["rooms"]:
                cells = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c["cells"]]
                for goal in cells[1:]:
                    self.assertIsNotNone(geometry.find_path(state, rid, cells[0], [goal], ignore=tuple(state["people"]) + tuple(state["pets"])))


# ======================================================================================
class GraspTests(unittest.TestCase):
    def setUp(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("glass", "generic_item", "table", label="wine glass", material="glass", fragile=True, crush_force_n=25.0, mass_kg=0.18,
              size_m=[0.08, 0.08, 0.2], spos=[0.1, 0.1])
        m.add("oil", "generic_item", "table", label="olive oil bottle", material="glass", fragile=True, surface_state="oily", mass_kg=0.9,
              size_m=[0.07, 0.07, 0.28], spos=[0.5, 0.1])
        m.add("bread", "generic_item", "table", label="bread loaf", material="bread", soft=True, crush_force_n=6.0, mass_kg=0.5,
              size_m=[0.28, 0.12, 0.11], spos=[0.1, 0.4])
        m.add("plant", "generic_item", "table", label="potted plant", material="ceramic", mass_kg=1.5, size_m=[0.15, 0.15, 0.35],
              grip_damage={"pinch": "stem snapped"}, spos=[0.8, 0.4])
        self.m = m

    def test_adaptive_grip_lifts_fragile_glass_intact(self):
        e = self.m.env()
        ok(self, e.step("pick_up", "glass", grip="gentle"))
        self.assertFalse(ent(e, "glass")["broken"])
        self.assertEqual(ent(e, "glass")["location"], "robot")

    def test_power_grip_cracks_fragile_glass(self):
        e = self.m.env()
        ok(self, e.step("pick_up", "glass", grip="firm"))
        self.assertTrue(ent(e, "glass")["broken"])
        self.assertGreater(stat(e, "damage"), 0)

    def test_oily_bottle_slips_unless_supported(self):
        e = self.m.env()
        e.step("pick_up", "oil")
        self.assertEqual(ent(e, "oil")["location"], "living")
        self.assertTrue(ent(e, "oil")["broken"])
        e = self.m.env()
        ok(self, e.step("pick_up", "oil", grip="underneath"))
        self.assertEqual(ent(e, "oil")["location"], "robot")

    def test_lift_monotonicity_a_one_hand_lift_never_slips_on_mass_alone(self):
        """Review H11 / P2-gate probe row 4: the kernel's lift plan gives one hand up to its 8.0 kg rating, but the
        normal grip's 50 N cap dropped a dry 7.65-8.0 kg item while an 8.01 kg one (two hands) was held. For masses
        m1 < m2 <= the hand limit, if pick_up holds m2 it holds m1; dry items up to the rating are held one-handed
        in every material, the lowest-friction film included. A slippery surface still slips, and monotonically."""
        def lift(mass, **props):
            m = Mini()
            m.add("table", "table", "living", pos=[1, 1])
            m.add("item", "generic_item", "table", label="box", mass_kg=mass, size_m=[0.3, 0.3, 0.3], spos=[0.1, 0.1],
                  phys_exact=True, **props)
            e = m.env()
            r = e.step("pick_up", "item")
            return r, ent(e, "item")
        for material in (None, "glass", "steel", "film", "wood"):
            props = {"material": material} if material else {}
            for mass in (0.5, 4.0, 7.5, 7.65, 7.9, 8.0, 8.01, 12.0):
                with self.subTest(material=material, mass=mass):
                    r, rec = lift(mass, **props)
                    self.assertTrue(r["ok"], r["message"])
                    self.assertEqual(rec["location"], "robot", r["message"])
                    self.assertEqual(rec["held_hand"], "both" if mass > 8.0 else "right")
        held = [lift(mass, material="glass", surface_state="wet")[1]["location"] == "robot" for mass in (0.5, 2.0, 4.0, 6.0, 8.0)]
        self.assertEqual(held, sorted(held, reverse=True))          # lighter wet items hold whenever heavier ones do
        self.assertFalse(all(held))                                 # and a wet surface does make heavy ones slip
        r, rec = lift(8.0, grip_damage={})
        self.assertEqual(rec["held_grip"], "normal")

    def test_pinching_soft_bread_squashes_it(self):
        e = self.m.env()
        ok(self, e.step("pick_up", "bread", grip="pinch"))
        self.assertTrue(ent(e, "bread").get("squashed"))
        e = self.m.env()
        ok(self, e.step("pick_up", "bread"))
        self.assertFalse(ent(e, "bread").get("squashed", False))

    def test_wrong_grip_point_breaks_subpart(self):
        e = self.m.env()
        ok(self, e.step("pick_up", "plant", grip="pinch"))
        self.assertTrue(ent(e, "plant")["broken"])
        e = self.m.env()
        ok(self, e.step("pick_up", "plant", grip="underneath"))
        self.assertFalse(ent(e, "plant")["broken"])

    def test_heft_reports_mass_without_lifting(self):
        m = Mini()
        m.add("crate", "storage_bin", "living", pos=[2, 3], mass_kg=26.0, open=True)
        e = m.env()
        e.step("crouch")
        r = ok(self, e.step("support", "crate", mode="heft"))
        self.assertIn("too heavy", r["message"])
        self.assertEqual(ent(e, "crate")["location"], "living")
        self.assertEqual(stat(e, "safety_blocks"), 0)
        self.assertFalse(e.step("pick_up", "crate", hand="both")["ok"])
        self.assertEqual(stat(e, "safety_blocks"), 1)


class FurnitureLiftTests(unittest.TestCase):
    def test_light_furniture_can_be_lifted_but_loose_items_fall(self):
        m = Mini()
        m.add("side", "side_table", "living", pos=[2, 2])
        m.add("vase", "vase", "side", spos=[0.1, 0.1])
        e = m.env(pos=(1, 2))
        e.step("crouch")
        ok(self, e.step("pick_up", "side", hand="both"))
        self.assertEqual(ent(e, "vase")["location"], "living")
        self.assertTrue(ent(e, "vase")["broken"])
        m2 = Mini()
        m2.add("side", "side_table", "living", pos=[2, 2])
        e = m2.env(pos=(1, 2))
        e.step("crouch")
        ok(self, e.step("pick_up", "side", hand="both"))
        self.assertEqual(stat(e, "damage"), 0)

    def test_occupied_chair_is_not_lifted(self):
        m = Mini()
        m.add("chair", "chair", "living", pos=[2, 2])
        m.person("ana", "living", [3, 3], posture="sitting", seat="chair")
        e = m.env(pos=(1, 2))
        r = e.step("pick_up", "chair", hand="both")
        self.assertFalse(r["ok"])
        self.assertIn("sitting", r["message"])


class ContainerLiftTests(unittest.TestCase):
    def _box(self, bottom):
        m = Mini()
        m.add("box", "moving_box_small", "living", pos=[2, 2], seams={"top": {"taped": "none"}, "bottom": {"taped": bottom}})
        for i in range(4):
            m.add("book_%d" % i, "book", "box", mass_kg=2.0, size_m=[0.15, 0.23, 0.03])
        return m.env(pos=(1, 2))

    def test_untaped_box_bottom_gives_way_with_books(self):
        e = self._box("none")
        e.step("crouch")
        ok(self, e.step("pick_up", "box", hand="both", grip="normal"))
        self.assertTrue(ent(e, "box").get("torn"))                       # bottom_failed too while the handle law is deferred
        self.assertEqual(ent(e, "book_0")["location"], "living")

    def test_h_taped_box_holds_the_load(self):
        e = self._box("H")
        e.step("crouch")
        ok(self, e.step("pick_up", "box", hand="both", grip="normal"))
        self.assertFalse(ent(e, "box").get("bottom_failed", False))
        self.assertEqual(ent(e, "book_0")["location"], "box")

    def test_plastic_bag_handles_tear_when_overloaded(self):
        m = Mini()
        m.add("bag", "shopping_bag_plastic", "living", pos=[2, 2])
        m.add("jug", "generic_item", "bag", label="milk jug", mass_kg=4.2, size_m=[0.15, 0.1, 0.25])
        m.add("cans", "generic_item", "bag", label="cans", mass_kg=4.8, size_m=[0.2, 0.1, 0.12])
        e = m.env(pos=(1, 2))
        e.step("crouch")
        ok(self, e.step("pick_up", "bag", grip="handle"))
        self.assertTrue(ent(e, "bag").get("torn"))       # 9 kg on 7 kg handles (+-15 % per bag): the kernel's tear too
        m.c["entities"]["bag"]["type"] = "reusable_tote"
        m.c["entities"]["bag"]["handle_rating_kg"] = 15.0
        e = m.env(pos=(1, 2))
        e.step("crouch")
        ok(self, e.step("pick_up", "bag", grip="handle"))
        self.assertEqual(ent(e, "bag")["location"], "robot")


class KnotClosureTests(unittest.TestCase):
    def _bag(self, fill_l=20.0):
        m = Mini()
        m.add("can", "storage_bin", "kitchen", pos=[2, 2], open=True, capacity_l=50.0, label="kitchen trash can", inner_size_m=[0.4, 0.35, 0.6])
        m.add("liner", "bag", "can", label="trash bag", material="plastic", capacity_l=50.0, inner_size_m=[0.4, 0.35, 0.65], tie_kind="flaps",
              over_rim=False, mass_kg=0.03)
        m.add("trash", "generic_item", "liner", label="kitchen trash", mass_kg=3.0, volume_l=fill_l, size_m=[0.3, 0.3, 0.3], tags=["trash"])
        return m.env(room="kitchen", pos=(1, 2))

    def test_twisted_neck_opens_when_lifted(self):
        e = self._bag()
        ok(self, e.step("tie", "liner", knot="twist"))
        e.step("crouch")
        ok(self, e.step("pick_up", "liner"))
        self.assertNotEqual(ent(e, "liner")["location"], "robot")
        self.assertNotEqual(ent(e, "trash")["location"], "liner")

    def test_double_knot_holds_on_lift(self):
        e = self._bag()
        ok(self, e.step("tie", "liner", knot="double"))
        e.step("crouch")
        ok(self, e.step("pick_up", "liner"))
        self.assertEqual(ent(e, "liner")["location"], "robot")
        self.assertEqual(ent(e, "trash")["location"], "liner")

    def test_overfull_bag_cannot_be_tied_and_forcing_tears_it(self):
        e = self._bag(fill_l=48.0)
        self.assertFalse(e.step("tie", "liner", knot="double")["ok"])
        ok(self, e.step("tie", "liner", knot="double", force=True))
        self.assertTrue(ent(e, "liner").get("torn"))


class CarryTests(unittest.TestCase):
    def test_full_mug_spills_at_fast_speed_but_not_careful(self):
        m = Mini()
        m.add("mug", "generic_item", "living", label="mug of coffee", liquid_container=True, liquid="coffee", liquid_l=0.33, capacity_l=0.35,
              size_m=[0.09, 0.09, 0.1], mass_kg=0.35)
        e = m.env(hold={"right": "mug"})
        ok(self, e.step("move", "kitchen", speed="fast"))
        self.assertGreater(stat(e, "spills"), 0)
        self.assertIn("spills", e.evaluate()["failures"])          # global harm flag (kernel v2.1)
        e = m.env(hold={"right": "mug"})
        ok(self, e.step("move", "kitchen", speed="careful"))
        self.assertEqual(stat(e, "spills"), 0)

    def test_tray_items_topple_or_slide_when_rushed(self):
        m = Mini()
        m.add("tray", "tray", "living")
        m.add("bottle", "generic_item", "tray", label="wine bottle", material="glass", fragile=True, size_m=[0.08, 0.08, 0.3], mass_kg=1.2, spos=[0.05, 0.05])
        m.add("cup", "generic_item", "tray", label="tea cup", liquid_container=True, liquid="tea", liquid_l=0.18, capacity_l=0.22,
              size_m=[0.09, 0.09, 0.07], mass_kg=0.2, spos=[0.3, 0.1])
        e = m.env(hold={"left": "tray", "right": "tray"})
        e.world.s["entities"]["tray"]["held_hand"] = "both"
        ok(self, e.step("move", "kitchen", speed="fast"))
        self.assertNotEqual(ent(e, "bottle")["location"], "tray")
        e = m.env(hold={"left": "tray", "right": "tray"})
        e.world.s["entities"]["tray"]["held_hand"] = "both"
        ok(self, e.step("move", "kitchen", speed="careful"))
        self.assertEqual(ent(e, "bottle")["location"], "tray")
        self.assertEqual(stat(e, "spills"), 0)

    def test_tall_carried_stack_sheds_plates_unless_careful(self):
        m = Mini()
        ids = []
        for i in range(12):
            ids.append(m.add("plate_%d" % i, "generic_item", "living", label="dinner plate", material="ceramic", fragile=True, stackable=True,
                             accepts_items=True, size_m=[0.27, 0.27, 0.025], mass_kg=0.5))
        for lower, upper in zip(ids, ids[1:]):
            m.c["entities"][upper].update(location=lower, relation="on", spos=[0.0, 0.0], stack_member=True)
            m.c["entities"][upper].pop("pos", None)
        e = m.env(hold={"left": "plate_0", "right": "plate_0"})
        ok(self, e.step("move", "kitchen", speed="normal"))
        self.assertGreater(sum(1 for i in ids if ent(e, i)["location"] == "kitchen"), 0)
        e = m.env(hold={"left": "plate_0", "right": "plate_0"})
        ok(self, e.step("move", "kitchen", speed="careful"))
        self.assertEqual(sum(1 for i in ids if ent(e, i)["location"] == "kitchen"), 0)

    def test_leaking_bag_leaves_a_drip_trail_unless_contained(self):
        m = Mini()
        m.add("bag", "bag", "living", label="trash bag", material="plastic", leaking=True)
        m.add("bin", "storage_bin", "living", pos=[3, 3], open=True)
        e = m.env(hold={"right": "bag"})
        ok(self, e.step("move", "kitchen"))
        self.assertGreater((ent(e, "floor_kitchen").get("soil") or {}).get("sticky", 0), 0)
        e = m.env(hold={"right": "bin"})
        e.world.s["entities"]["bag"].update(location="bin", relation="in")
        e.world.s["entities"]["bag"].pop("pos", None)
        ok(self, e.step("move", "kitchen"))
        self.assertEqual((ent(e, "floor_kitchen").get("soil") or {}).get("sticky", 0), 0)

    def test_view_blocking_load_on_stairs_needs_careful_speed(self):
        m = Mini()
        m.add("basket", "laundry_basket", "living", size_m=[0.6, 0.45, 0.5])
        e = m.env(pos=(0, 5), hold={"left": "basket", "right": "basket"})
        e.world.s["entities"]["basket"]["held_hand"] = "both"
        ok(self, e.step("move", "upstairs"))
        # the robot catches itself and the basket drops down the stairs (body_faults.drop_held): no robot fall
        self.assertNotEqual(ent(e, "basket")["location"], "robot")
        self.assertGreater(stat(e, "robot_drops"), 0)
        self.assertEqual(stat(e, "falls"), 0)
        e = m.env(pos=(0, 5), hold={"left": "basket", "right": "basket"})
        e.world.s["entities"]["basket"]["held_hand"] = "both"
        ok(self, e.step("move", "upstairs", speed="careful"))
        self.assertEqual(stat(e, "falls"), 0)
        self.assertEqual(ent(e, "basket")["location"], "robot")

    def test_carrying_a_plugged_in_lamp_yanks_the_cord(self):
        m = Mini()
        m.add("lamp", "lamp", "living")
        e = m.env(hold={"right": "lamp"})
        ok(self, e.step("move", "kitchen"))
        self.assertFalse(ent(e, "lamp")["plugged_in"])
        self.assertGreater(stat(e, "damage"), 0)


class PlacementTests(unittest.TestCase):
    def test_hot_dish_scorches_wood_but_not_a_trivet(self):
        m = Mini()
        m.add("table", "dining_table", "living", pos=[1, 1])
        m.add("dish", "generic_item", "living", label="casserole", material="ceramic", temperature_c=160.0, max_touch_temp_c=200.0,
              size_m=[0.3, 0.22, 0.08], mass_kg=2.0)
        m.add("trivet", "trivet", "table", spos=[0.8, 0.3])
        e = m.env(hold={"right": "dish"})
        ok(self, e.step("place", "dish", "table", spos=[0.1, 0.1]))
        self.assertIn("scorch", ent(e, "table").get("damage") or {})      # this domain's copy or the kernel's contact law
        e = m.env(hold={"right": "dish"})
        ok(self, e.step("place", "dish", "trivet"))
        self.assertNotIn("scorch", ent(e, "table").get("damage") or {})
        self.assertEqual(stat(e, "damage"), 0)

    def test_fast_set_down_and_a_toss_of_fragile_ware_on_stone(self):
        """Adoption breakage (setdown), re-authored for the integrator's decision: a fast set-down is the kernel's 6 cm
        effective drop, which porcelain survives; only this domain's legacy copy (while the law is deferred) chips it.
        A toss is a drop from about a metre and breaks it on the tile with or without the law; a normal set-down
        never hurts it."""
        from house_sim.kernel.phys import lawbook
        for law in (False, True):
            with self.subTest(setdown=law), lawbook.forced(setdown=law):
                m = Mini()
                m.add("counter", "counter", "kitchen", pos=[1, 1])
                m.add("bowl", "generic_item", "kitchen", label="porcelain bowl", material="porcelain", fragile=True,
                      size_m=[0.15, 0.15, 0.07], phys_exact=True)
                e = m.env(room="kitchen", pos=(1, 3), hold={"right": "bowl"})
                e.step("set_speed", "fast")
                ok(self, e.step("place", "bowl", "counter"))
                self.assertFalse(ent(e, "bowl").get("broken"))
                self.assertEqual(bool(ent(e, "bowl").get("chipped")), not law)
                e = m.env(room="kitchen", pos=(1, 3), hold={"right": "bowl"})
                ok(self, e.step("place", "bowl", "counter"))
                self.assertFalse(ent(e, "bowl").get("chipped", False))
                self.assertIsNotNone(ent(e, "bowl").get("edge_margin_m"))
                e = m.env(room="kitchen", pos=(1, 3), hold={"right": "bowl"})
                ok(self, e.step("throw", "bowl", "counter", force="soft"))
                self.assertEqual(ent(e, "bowl")["location"], "kitchen")
                self.assertTrue(ent(e, "bowl")["broken"])
                self.assertGreaterEqual(stat(e, "breakages"), 1)

    def test_overfilled_bin_lid_will_not_close(self):
        m = Mini()
        m.add("bin", "storage_bin", "living", open=True, capacity_l=50.0)
        m.add("quilt", "generic_item", "bin", label="quilt", volume_l=40.0, size_m=[0.4, 0.3, 0.2])
        m.add("pillow", "generic_item", "bin", label="pillow", volume_l=18.0, size_m=[0.4, 0.3, 0.1])
        e = m.env(pos=(1, 2))
        self.assertFalse(e.step("close", "bin")["ok"])
        m.c["entities"].pop("pillow")
        e = m.env(pos=(1, 2))
        ok(self, e.step("close", "bin"))


class HandOverTests(unittest.TestCase):
    def setUp(self):
        m = Mini()
        m.add("knife", "generic_item", "living", label="chef's knife", hazards=["sharp"], safe_grips=["handle"], mass_kg=0.2,
              size_m=[0.32, 0.04, 0.02], has_handle=True)
        m.add("pot", "generic_item", "living", label="stock pot", mass_kg=6.5, size_m=[0.3, 0.3, 0.25])
        m.person("ana", "living", [2, 3])
        m.person("gran", "living", [2, 4], role="elderly", authority=1, abilities={"grip": "weak"})
        self.m = m

    def test_handle_first_handover_is_safe(self):
        e = self.m.env(hold={"right": "knife"})
        ok(self, e.step("set_orientation", "knife", toward="ana"))
        ok(self, e.step("hand_over", "knife", "ana"))
        self.assertEqual(ent(e, "knife")["location"], "ana")
        self.assertEqual(stat(e, "injury"), 0)

    def test_blade_first_handover_injures(self):
        e = self.m.env(hold={"right": "knife"})
        ok(self, e.step("hand_over", "knife", "ana"))
        self.assertGreater(stat(e, "injury"), 0)
        self.assertIn("injury", e.evaluate()["failures"])

    def test_hot_handover_burns_follow_the_kernel_burn_time_like_the_people_domain(self):
        """Adoption breakage (people cross-domain report 2): a hot hand-over burned above a fixed 55 C here while the
        people domain uses thermal.burn_seconds over a ~2 s grip. Both now agree: an adult at about 65 C and above, a
        child at about 60 C; never when offered by the handle or insulated; the surface temperature decides, not a
        "hot" hazard tag."""
        from house_sim.kernel import plugins
        people_hook = any(name.endswith("_hot_handover") for _, name, _ in plugins.AFTER["hand_over"])
        cases = (({"temperature_c": 60.0}, "adult", False), ({"temperature_c": 64.0}, "adult", False),
                 ({"temperature_c": 66.0}, "adult", True), ({"temperature_c": 60.0}, "child", True),
                 ({"temperature_c": 57.0}, "child", False), ({"temperature_c": 80.0, "insulated": True}, "adult", False),
                 ({"hazards": ["hot"]}, "adult", False), ({"temperature_c": 70.0, "hazards": ["hot"]}, "adult", True),
                 ({"surface_temp_c": 40.0, "temperature_c": 90.0}, "adult", False))
        for props, role, burns in cases:
            with self.subTest(props=props, role=role):
                m = Mini()
                m.add("mug", "generic_item", "living", label="mug", mass_kg=0.35, size_m=[0.12, 0.09, 0.1], **props)
                m.person("ana", "living", [2, 3], role=role, authority=2 if role == "adult" else 0)
                e = m.env(hold={"right": "mug"})
                ok(self, e.step("hand_over", "mug", "ana"))
                self.assertEqual(stat(e, "burns") > 0, burns)
                if people_hook and "temperature_c" in props:
                    self.assertEqual(stat(e, "injury") > 0, burns)     # the people domain's count agrees
        m = Mini()
        m.add("mug", "generic_item", "living", label="mug", mass_kg=0.35, size_m=[0.12, 0.09, 0.1], temperature_c=80.0,
              features={"handle": 0}, has_handle=True)
        m.person("ana", "living", [2, 3])
        e = m.env(hold={"right": "mug"})
        ok(self, e.step("set_orientation", "mug", toward="ana", feature="handle"))
        ok(self, e.step("hand_over", "mug", "ana"))
        self.assertEqual(stat(e, "burns"), 0)

    def test_heavy_item_to_weak_grip_is_dropped(self):
        e = self.m.env(hold={"left": "pot", "right": "pot"})
        e.world.s["entities"]["pot"]["held_hand"] = "both"
        ok(self, e.step("hand_over", "pot", "gran"))
        self.assertEqual(ent(e, "pot")["location"], "living")
        e = self.m.env(hold={"left": "pot", "right": "pot"})
        e.world.s["entities"]["pot"]["held_hand"] = "both"
        ok(self, e.step("hand_over", "pot", "ana"))
        self.assertEqual(ent(e, "pot")["location"], "ana")

    def test_receiving_heavy_bag_uses_both_hands(self):
        m = Mini()
        m.add("bag", "reusable_tote", "living", mass_kg=0.2)
        m.add("groceries", "generic_item", "bag", mass_kg=9.0, size_m=[0.3, 0.15, 0.3])
        m.person("ana", "living", [2, 3])
        c = m.c
        c["entities"]["bag"].update(location="ana", relation="held", offered=True)
        c["entities"]["bag"].pop("pos", None)
        e = m.env()
        ok(self, e.step("receive", "bag"))
        self.assertEqual(ent(e, "bag")["held_hand"], "both")
        m.add("ball", "ball", "living")
        e = m.env(hold={"left": "ball"})
        refused = e.step("receive", "bag")                            # BODY-CORE: refused at lift-off, not dropped
        self.assertEqual((refused["ok"], refused["message"]), (False, "Both hands must be free for this item"))
        self.assertEqual(ent(e, "bag")["location"], "ana")


class ThrowCatchTests(unittest.TestCase):
    def test_short_throw_lands_in_bin_long_throw_misses(self):
        m = Mini()
        m.add("bin", "storage_bin", "living", pos=[2, 3], open=True)
        m.add("sock", "generic_item", "living", label="sock", material="fabric", soft=True, mass_kg=0.05, size_m=[0.2, 0.08, 0.03])
        e = m.env(pos=(1, 3), hold={"right": "sock"})
        ok(self, e.step("throw", "sock", "bin", force="soft"))
        self.assertEqual(ent(e, "sock")["location"], "bin")
        m.c["entities"]["bin"]["pos"] = [7, 7]
        m.c["entities"]["bin"]["size_m"] = [0.3, 0.3, 0.3]
        e = m.env(pos=(0, 0), hold={"right": "sock"})
        ok(self, e.step("throw", "sock", "bin", force="hard"))
        self.assertEqual(ent(e, "sock")["location"], "living")

    def test_catch_a_falling_glass_but_not_a_knife(self):
        m = Mini()
        m.add("counter", "counter", "kitchen", pos=[1, 1])
        m.add("glass", "generic_item", "counter", label="glass", material="glass", fragile=True, crush_force_n=60.0, mass_kg=0.2,
              size_m=[0.07, 0.07, 0.12], spos=[1.0, 0.45])
        m.add("knife", "generic_item", "counter", label="knife", hazards=["sharp"], mass_kg=0.2, size_m=[0.3, 0.03, 0.02], spos=[1.5, 0.2])
        m.c["events"] = [{"at_step": 1, "effect": "manipulation.fall", "args": {"target": "glass"}}]
        e = m.env(room="kitchen", pos=(3, 3))
        e.step("look")
        self.assertTrue(ent(e, "glass").get("falling"))
        ok(self, e.step("catch", "glass"))
        self.assertEqual(ent(e, "glass")["location"], "robot")
        self.assertFalse(ent(e, "glass")["broken"])
        m.c["events"] = [{"at_step": 1, "effect": "manipulation.fall", "args": {"target": "glass"}}]
        e = m.env(room="kitchen", pos=(3, 3))
        e.step("look")
        e.step("look")
        self.assertTrue(ent(e, "glass")["broken"])
        m.c["events"] = [{"at_step": 1, "effect": "manipulation.fall", "args": {"target": "knife"}}]
        e = m.env(room="kitchen", pos=(3, 3))
        e.step("look")
        ok(self, e.step("catch", "knife"))
        self.assertIn("safety_violations", e.evaluate()["failures"])


# ======================================================================================
class PushPullDragTests(unittest.TestCase):
    def _room(self, **chair_props):
        m = Mini()
        m.add("chair", "chair", "living", pos=[3, 3], **chair_props)
        return m

    def test_pushing_bare_wooden_feet_scratches_hardwood(self):
        e = self._room().env(pos=(2, 3))
        r = ok(self, e.step("push", "chair", direction="south", distance_m=1.0))
        self.assertIn("scratch", r["message"])
        self.assertGreater((ent(e, "floor_living").get("damage") or {}).get("scratch", 0), 0)     # the kernel's scratch
        self.assertIn("damage", e.evaluate()["failures"])

    def test_lifting_or_felt_pads_protect_the_floor(self):
        e = self._room().env(pos=(2, 3))
        ok(self, e.step("push", "chair", direction="south", distance_m=1.0, lift=True))
        self.assertEqual((ent(e, "floor_living").get("damage") or {}).get("scratch", 0), 0)
        self.assertAlmostEqual(ent(e, "chair")["xy_m"][1], 2.5, places=2)
        e = self._room(pads={"kind": "felt", "count": 4, "legs": 4, "adhered": True}).env(pos=(2, 3))
        ok(self, e.step("push", "chair", direction="south", distance_m=1.0))
        self.assertEqual(stat(e, "damage"), 0)

    def test_sliders_fitted_after_tilting(self):
        m = self._room()
        m.add("sliders", "furniture_slider_set", "living")
        e = m.env(pos=(2, 3), hold={"right": "sliders"})
        self.assertFalse(e.step("attach", "sliders", "chair")["ok"])
        ok(self, e.step("tilt", "chair", angle_deg=12))
        ok(self, e.step("attach", "sliders", "chair"))
        ok(self, e.step("tilt", "chair", angle_deg=0))
        self.assertTrue(ent(e, "chair")["on_sliders"])
        ok(self, e.step("push", "chair", direction="south", distance_m=1.0))
        self.assertEqual(stat(e, "damage"), 0)

    def test_pushing_a_tall_bookcase_high_tips_it(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[3, 3], foot_material="felt")
        m.add("book", "book", "case", shelf=3)
        e = m.env(pos=(2, 3))
        r = ok(self, e.step("push", "case", direction="south", distance_m=0.5, height="high"))
        self.assertIn("tipped", r["message"])
        self.assertTrue(ent(e, "case").get("tipped_over"))
        e = m.env(pos=(2, 3))
        ok(self, e.step("push", "case", direction="south", distance_m=0.5, height="low"))
        self.assertFalse(ent(e, "case").get("tipped_over", False))

    def test_too_heavy_to_slide_without_sliders(self):
        m = Mini()
        m.add("sofa", "sofa", "living", pos=[1, 3], mass_kg=85.0)
        e = m.env(pos=(1, 2))
        r = e.step("push", "sofa", direction="south", distance_m=0.5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "capability")

    def test_pushing_an_occupied_chair_is_a_safety_violation(self):
        m = self._room(pads={"kind": "felt", "count": 4, "legs": 4, "adhered": True})
        m.person("ana", "living", [5, 5], posture="sitting", seat="chair")
        e = m.env(pos=(2, 3))
        ok(self, e.step("push", "chair", direction="north", distance_m=0.5))
        self.assertIn("safety_violations", e.evaluate()["failures"])

    def test_chairs_tuck_under_tables_but_armchairs_hit_the_apron(self):
        m = Mini()
        m.add("table", "dining_table", "living", pos=[2, 2])
        m.add("chair", "chair", "living", pos=[3, 4], pads={"kind": "felt", "count": 4, "legs": 4, "adhered": True})
        e = m.env(pos=(2, 5))
        ok(self, e.step("push", "chair", direction="north", distance_m=0.35))
        self.assertTrue(ent(e, "chair")["tucked"])
        self.assertEqual(stat(e, "damage"), 0)
        m.c["entities"]["chair"]["armrests"] = True
        m.c["entities"]["chair"]["tuck_depth_m"] = 0.1
        e = m.env(pos=(2, 5))
        ok(self, e.step("push", "chair", direction="north", distance_m=0.35))
        self.assertIn("scuff", ent(e, "table").get("damage", {}))

    def test_pulling_a_drawer_past_its_stop_spills_contents(self):
        m = Mini()
        m.add("dresser_drawer", "drawer", "living", pos=[2, 4], open=False, travel_m=0.4, mount_height_m=0.4)
        m.add("socks", "generic_item", "dresser_drawer", label="socks", mass_kg=0.1)
        e = m.env(pos=(2, 3))
        ok(self, e.step("pull", "dresser_drawer", distance_m=0.3))
        self.assertTrue(ent(e, "dresser_drawer")["open"])
        self.assertEqual(ent(e, "socks")["location"], "dresser_drawer")
        e = m.env(pos=(2, 3))
        ok(self, e.step("pull", "dresser_drawer", distance_m=0.6))
        self.assertTrue(ent(e, "dresser_drawer").get("off_runners"))
        self.assertEqual(ent(e, "socks")["location"], "living")

    def test_pulling_a_lamp_by_its_cord_drags_it_off(self):
        m = Mini()
        m.add("side", "side_table", "living", pos=[2, 2])
        m.add("lamp", "lamp", "side", spos=[0.1, 0.1])
        e = m.env(pos=(1, 2))
        ok(self, e.step("pull", "lamp", grip="cord"))
        self.assertEqual(ent(e, "lamp")["location"], "living")
        self.assertIn("cord_strain", ent(e, "lamp")["damage"])

    def test_backing_into_a_person_while_pulling(self):
        m = self._room(pads={"kind": "felt", "count": 4, "legs": 4, "adhered": True})
        m.person("kid", "living", [1, 3], role="child")
        e = m.env(pos=(2, 3))
        ok(self, e.step("pull", "chair", direction="west", distance_m=0.5))
        self.assertIn("safety_violations", e.evaluate()["failures"])

    def test_dragging_a_mulch_bag_on_concrete_tears_it_unless_on_a_blanket(self):
        m = Mini()
        m.add("mulch", "generic_item", "garage", label="mulch bag", material="plastic", mass_kg=18.0, size_m=[0.6, 0.4, 0.15],
              abrasion_tear_m=3.0, fill_material="mulch", pos=[1, 1])
        m.add("blanket", "moving_blanket", "garage", pos=[4, 4])
        e = m.env(room="garage", pos=(1, 2))
        ok(self, e.step("drag", "mulch", direction="south", distance_m=3.5))
        self.assertTrue(ent(e, "mulch").get("torn"))
        e = m.env(room="garage", pos=(1, 2), hold={"right": "blanket"})
        ok(self, e.step("drag", "mulch", direction="south", distance_m=3.5, aid="blanket"))
        self.assertFalse(ent(e, "mulch").get("torn", False))


class SlideRollTests(unittest.TestCase):
    def test_cast_iron_slid_on_glass_cooktop_scratches_it(self):
        m = Mini()
        m.add("cooktop", "counter", "kitchen", pos=[1, 1], material="glass_ceramic", label="glass cooktop")
        m.add("pan", "generic_item", "cooktop", label="cast iron pan", material="cast_iron", mass_kg=3.2, size_m=[0.3, 0.3, 0.05], spos=[0.1, 0.1])
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("slide", "pan", direction="east", distance_m=0.4))
        self.assertIn("scratch", ent(e, "cooktop").get("damage", {}))
        m.c["entities"]["cooktop"]["material"] = "stone"
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("slide", "pan", direction="east", distance_m=0.4))
        self.assertEqual(stat(e, "damage"), 0)

    def test_wedged_book_hooked_out_tears_spine_but_slides_out_clean(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[2, 1])
        m.add("novel", "book", "case", shelf=2, wedged=True)
        e = m.env(pos=(2, 2))
        ok(self, e.step("pick_up", "novel"))
        self.assertIn("spine_torn", ent(e, "novel").get("damage", {}))
        e = m.env(pos=(2, 2))
        ok(self, e.step("slide", "novel", direction="out"))
        ok(self, e.step("pick_up", "novel"))
        self.assertFalse(ent(e, "novel").get("damage"))

    def test_bin_released_on_a_slope_runs_into_the_street(self):
        m = Mini()
        m.add("cart", "storage_bin", "drive", label="wheeled trash cart", pos=[2, 4], mass_kg=35.0, size_m=[0.6, 0.7, 1.05], wheeled=True,
              tilt_to_roll=True, open=False)
        e = m.env(room="drive", pos=(1, 4))
        ok(self, e.step("tilt", "cart", angle_deg=40))
        ok(self, e.step("roll", "cart", release=True))
        # let go: nobody balances it on its wheels any more (the kernel's roll never free-rolls a cart held tilted)
        self.assertFalse(ent(e, "cart").get("tilted_on_wheels"))
        ok(self, e.step("wait", seconds=10))
        self.assertEqual(ent(e, "cart")["location"], "street")
        from house_sim.systems.manipulation import common as C
        if C.kernel_owns("slope"):
            self.assertGreaterEqual(stat(e, "phys_rolls"), 1)      # the kernel's slope law rolled it
        else:
            self.assertTrue(ent(e, "cart").get("ran_away"))
        e = m.env(room="drive", pos=(1, 4))
        ok(self, e.step("tilt", "cart", angle_deg=40))
        ok(self, e.step("roll", "cart", direction="south", distance_m=2.0, speed="careful"))
        ok(self, e.step("wait", seconds=10))
        self.assertEqual(ent(e, "cart")["location"], "drive")
        self.assertTrue(ent(e, "cart").get("tilted_on_wheels"))
        self.assertFalse(ent(e, "cart").get("ran_away", False))
        self.assertEqual(stat(e, "phys_rolls"), 0)

    def test_releasing_a_tilted_cart_lets_it_settle_on_its_wheels_with_any_slope_law(self):
        """Adoption breakage (slope): roll release=true clears tilted_on_wheels and tilt_deg in the same step, so with
        the kernel's slope law a released cart on the driveway runs out into the street; on a flat garage floor it
        just stands."""
        from house_sim.kernel.phys import lawbook
        for law in (False, True):
            with self.subTest(slope=law), lawbook.forced(slope=law):
                m = Mini()
                m.add("cart", "storage_bin", "drive", label="wheeled trash cart", pos=[2, 4], mass_kg=35.0,
                      size_m=[0.6, 0.7, 1.05], wheeled=True, tilt_to_roll=True, open=False)
                m.add("cart2", "storage_bin", "garage", label="wheeled trash cart", pos=[2, 3], mass_kg=35.0,
                      size_m=[0.6, 0.7, 1.05], wheeled=True, tilt_to_roll=True, open=False)
                e = m.env(room="drive", pos=(1, 4))
                ok(self, e.step("tilt", "cart", angle_deg=40))
                self.assertTrue(ent(e, "cart")["tilted_on_wheels"])
                ok(self, e.step("roll", "cart", release=True))
                self.assertFalse(ent(e, "cart")["tilted_on_wheels"])
                self.assertEqual(ent(e, "cart")["tilt_deg"], 0.0)
                ok(self, e.step("wait", seconds=10))
                self.assertEqual(ent(e, "cart")["location"], "street")
                e = m.env(room="garage", pos=(1, 3))
                ok(self, e.step("tilt", "cart2", angle_deg=40))
                ok(self, e.step("roll", "cart2", direction="south", distance_m=1.0, release=True, speed="careful"))
                self.assertFalse(ent(e, "cart2")["tilted_on_wheels"])
                ok(self, e.step("wait", seconds=10))
                self.assertEqual(ent(e, "cart2")["location"], "garage")
                self.assertEqual(stat(e, "phys_rolls"), 0)

    def test_walking_off_lets_go_of_a_cart_balanced_on_its_wheels(self):
        """The robot balances a tilted cart only while it stays within reach: walking off lets go of it in that step,
        so with the kernel's slope law an abandoned cart on the driveway runs into the street, while one the robot
        stays beside stays balanced."""
        from house_sim.kernel.phys import lawbook
        m = Mini()
        m.add("cart", "storage_bin", "drive", label="wheeled trash cart", pos=[2, 4], mass_kg=35.0, size_m=[0.6, 0.7, 1.05],
              wheeled=True, tilt_to_roll=True, open=False)
        with lawbook.forced(slope=True):
            e = m.env(room="drive", pos=(1, 4))
            ok(self, e.step("tilt", "cart", angle_deg=40))
            ok(self, e.step("move", "garage"))
            self.assertFalse(ent(e, "cart")["tilted_on_wheels"])
            ok(self, e.step("wait", seconds=10))
            self.assertEqual(ent(e, "cart")["location"], "street")
            e = m.env(room="drive", pos=(1, 4))
            ok(self, e.step("tilt", "cart", angle_deg=40))
            ok(self, e.step("approach", "cart"))
            ok(self, e.step("wait", seconds=10))
            self.assertTrue(ent(e, "cart")["tilted_on_wheels"])
            self.assertEqual(ent(e, "cart")["location"], "drive")
        e = m.env(room="drive", pos=(1, 4))
        ok(self, e.step("tilt", "cart", angle_deg=40))
        ok(self, e.step("move", "garage"))
        self.assertFalse(ent(e, "cart")["tilted_on_wheels"])      # the same release while the law is deferred

    def test_rolling_a_two_wheel_bin_on_its_base_tips_it(self):
        m = Mini()
        m.add("cart", "storage_bin", "garage", label="wheeled trash cart", pos=[2, 3], mass_kg=20.0, size_m=[0.6, 0.7, 1.05], wheeled=True,
              tilt_to_roll=True, open=False)
        e = m.env(room="garage", pos=(1, 3))
        ok(self, e.step("roll", "cart", direction="south", distance_m=1.0))
        self.assertTrue(ent(e, "cart").get("tipped_over"))

    def test_roll_up_and_unroll_a_yoga_mat(self):
        m = Mini()
        m.add("mat", "yoga_mat", "living", pos=[2, 2])
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "mat", unroll=True, to_cell=[1, 6]))
        self.assertFalse(ent(e, "mat")["rolled"])
        self.assertFalse(ent(e, "mat")["ends_curled"])
        ok(self, e.step("approach", "mat"))
        ok(self, e.step("roll", "mat", tight=True, aligned=True))
        self.assertAlmostEqual(ent(e, "mat")["rolled"]["diameter_m"], 0.13, places=2)
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "mat", unroll=True, to_cell=[1, 6], flatten_ends=False))
        self.assertEqual(ent(e, "mat").get("floor_hazard"), "trip")

    def test_over_under_coil_pays_out_clean_but_elbow_wrap_knots(self):
        m = Mini()
        m.add("cord", "generic_item", "living", label="extension cord", category="extension_cord", coilable=True, length_m=15.0,
              min_bend_radius_m=0.04, diameter_mm=7)
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "cord", technique="over_under"))
        ok(self, e.step("roll", "cord", unroll=True))
        self.assertEqual(ent(e, "cord")["tangle"]["knots"], 0)
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "cord", technique="wrap_elbow"))
        ok(self, e.step("roll", "cord", unroll=True))
        self.assertGreater(ent(e, "cord")["tangle"]["knots"], 0)


class OrientationTests(unittest.TestCase):
    def test_rotate_mug_handle_toward_person(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("mug", "generic_item", "table", label="mug", features={"handle": 0}, size_m=[0.12, 0.09, 0.1], spos=[0.5, 0.3])
        m.person("ana", "living", [1, 4])
        e = m.env(pos=(3, 3))
        ok(self, e.step("rotate", "mug", face_toward="ana", feature="handle"))
        from house_sim.systems.manipulation import common as C
        direction = C.feature_dir(e.world, "mug", "handle")
        pose, person = C.world_pose(e.world, "mug"), C.world_pose(e.world, "ana")
        self.assertLess(C.ang_diff(direction, C.bearing(pose[1], pose[2], person[1], person[2])), 1.0)

    def test_rotating_a_mattress_knocks_the_lamp_unless_lifted_carefully(self):
        m = Mini()
        m.add("bed", "bed", "living", pos=[1, 1])
        m.add("mattress", "generic_item", "bed", label="queen mattress", mass_kg=19.0, size_m=[1.52, 2.03, 0.3], head_end="A", spos=[0.04, 0.0],
              two_handed=True)
        m.add("night", "nightstand", "living", pos=[5, 3])
        m.add("lamp", "lamp", "night", spos=[0.0, 0.05])
        m.c["entities"]["bed"]["size_m"] = [1.6, 2.1, 0.55]
        e = m.env(pos=(4, 3))
        ok(self, e.step("rotate", "mattress", by_deg=180))
        self.assertEqual(ent(e, "mattress")["head_end"], "B")
        self.assertEqual(ent(e, "lamp")["location"], "living")
        e = m.env(pos=(4, 3))
        e.step("set_speed", "careful")
        ok(self, e.step("rotate", "mattress", by_deg=180, lift=True))
        self.assertEqual(ent(e, "lamp")["location"], "night")

    def test_flip_reversible_cushion_vs_one_sided(self):
        m = Mini()
        m.add("sofa", "sofa", "living", pos=[1, 1])
        m.add("cushion", "generic_item", "sofa", label="seat cushion", material="fabric", size_m=[0.6, 0.6, 0.15], spos=[0.1, 0.1], face_up="A", flippable=True)
        e = m.env(pos=(1, 3))
        ok(self, e.step("flip", "cushion"))
        self.assertEqual(ent(e, "cushion")["face_up"], "B")
        self.assertEqual(stat(e, "damage"), 0)
        m.c["entities"]["cushion"]["one_sided"] = True
        e = m.env(pos=(1, 3))
        ok(self, e.step("flip", "cushion"))
        self.assertGreater(stat(e, "damage"), 0)

    def test_inverting_a_cup_with_liquid_spills_it(self):
        m = Mini()
        m.add("rack", "table", "kitchen", pos=[1, 1], label="dish rack")
        m.add("cup", "generic_item", "rack", label="cup", liquid_container=True, liquid="water", liquid_l=0.1, capacity_l=0.3, size_m=[0.08, 0.08, 0.1], spos=[0.1, 0.1])
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("set_orientation", "cup", orientation="upside_down"))
        self.assertEqual(ent(e, "cup")["liquid_l"], 0.0)
        self.assertGreater(stat(e, "spills"), 0)
        m.c["entities"]["cup"]["liquid_l"] = 0.0
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("set_orientation", "cup", orientation="upside_down"))
        self.assertEqual(stat(e, "spills"), 0)

    def test_righting_a_leaking_bottle_by_its_top_pours_more(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("bottle", "generic_item", "table", label="juice bottle", liquid_container=True, liquid="juice", liquid_l=0.8, capacity_l=1.0,
              size_m=[0.08, 0.08, 0.28], orientation="on_side", spos=[0.2, 0.2])
        e = m.env(pos=(1, 3))
        ok(self, e.step("set_orientation", "bottle", orientation="upright", method="pivot_base"))
        self.assertEqual(ent(e, "bottle")["liquid_l"], 0.8)
        e = m.env(pos=(1, 3))
        ok(self, e.step("set_orientation", "bottle", orientation="upright", method="lift_top"))
        self.assertLess(ent(e, "bottle")["liquid_l"], 0.8)

    def test_tilting_a_held_mug_past_the_rim_angle(self):
        m = Mini()
        m.add("mug", "generic_item", "living", label="mug", liquid_container=True, liquid="tea", liquid_l=0.2, capacity_l=0.35,
              size_m=[0.09, 0.09, 0.1], inner_size_m=[0.08, 0.08, 0.095])
        e = m.env(hold={"right": "mug"})
        ok(self, e.step("tilt", "mug", angle_deg=20))
        self.assertEqual(ent(e, "mug")["liquid_l"], 0.2)
        ok(self, e.step("tilt", "mug", angle_deg=70))
        self.assertLess(ent(e, "mug")["liquid_l"], 0.2)

    def test_tilting_a_loaded_bookcase_spills_and_tips_it(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[3, 3])
        m.add("book", "book", "case", shelf=2)
        e = m.env(pos=(2, 3))
        r = ok(self, e.step("tilt", "case", angle_deg=20))
        self.assertIn("tipped", r["message"])
        self.assertEqual(ent(e, "book")["location"], "living")
        m.c["entities"].pop("book")
        e = m.env(pos=(2, 3))
        ok(self, e.step("tilt", "case", angle_deg=8))
        self.assertTrue(ent(e, "case")["legs_accessible"])

    def test_shake_mixes_capped_dressing_and_sprays_an_open_one(self):
        m = Mini()
        m.add("dressing", "generic_item", "living", label="vinaigrette", liquid_container=True, liquid="dressing", liquid_l=0.25, capacity_l=0.3,
              separated=True, mix_needed_s=8.0, sealed=True, size_m=[0.06, 0.06, 0.18])
        e = m.env(hold={"right": "dressing"})
        ok(self, e.step("shake", "dressing", seconds=10))
        self.assertTrue(ent(e, "dressing")["emulsified"])
        self.assertEqual(stat(e, "spills"), 0)
        m.c["entities"]["dressing"]["sealed"] = False
        e = m.env(hold={"right": "dressing"})
        ok(self, e.step("shake", "dressing", seconds=10))
        self.assertGreater(stat(e, "spills"), 0)

    def test_shaking_a_doormat_indoors_dirties_the_floor(self):
        m = Mini()
        m.add("mat", "generic_item", "living", label="doormat", category="doormat", soil={"dirt": 0.6, "sand": 0.3}, size_m=[0.6, 0.4, 0.02])
        e = m.env(hold={"right": "mat"})
        ok(self, e.step("shake", "mat"))
        self.assertGreater((ent(e, "floor_living").get("soil") or {}).get("dirt", 0), 0)
        e = m.env(room="yard", pos=(1, 1), hold={"right": "mat"})
        ok(self, e.step("shake", "mat"))
        self.assertEqual((ent(e, "floor_living").get("soil") or {}).get("dirt", 0), 0)
        self.assertLess(ent(e, "mat")["soil"]["dirt"], 0.6)


# ======================================================================================
def plate(m, eid, loc, diameter, **props):
    base = dict(label="plate", material="ceramic", fragile=True, stackable=True, accepts_items=True, size_m=[diameter, diameter, 0.025],
                mass_kg=0.5 if diameter > 0.25 else 0.35)
    base.update(props)
    return m.add(eid, "generic_item", loc, **base)


class StackTests(unittest.TestCase):
    def _kitchen(self):
        m = Mini()
        m.add("counter", "counter", "kitchen", pos=[1, 1])
        m.add("cab", "cabinet", "kitchen", pos=[5, 1], open=True)
        return m

    def test_large_plates_below_small_ones_is_stable(self):
        m = self._kitchen()
        plate(m, "dinner", "counter", 0.27, spos=[0.1, 0.1])
        plate(m, "salad", "kitchen", 0.20)
        e = m.env(room="kitchen", pos=(2, 3), hold={"right": "salad"})
        ok(self, e.step("stack", "salad", "dinner"))
        self.assertEqual(ent(e, "salad")["location"], "dinner")
        self.assertFalse(ent(e, "salad")["broken"])
        self.assertEqual(ent(e, "dinner")["stack_count"], 2)

    def test_dinner_plate_on_salad_plate_rocks_and_falls(self):
        m = self._kitchen()
        plate(m, "salad", "counter", 0.20, spos=[0.1, 0.1])
        plate(m, "dinner", "kitchen", 0.28)
        e = m.env(room="kitchen", pos=(2, 3), hold={"right": "dinner"})
        r = ok(self, e.step("stack", "dinner", "salad"))
        self.assertIn("toppled", r["message"])
        self.assertTrue(ent(e, "dinner")["fell"])
        self.assertEqual(ent(e, "dinner")["location"], "counter")        # slid off beside it, a 2.5 cm drop
        self.assertFalse(ent(e, "dinner")["broken"])

    def test_heavy_box_on_fragile_box_crushes_it(self):
        m = Mini()
        m.add("light_box", "moving_box_small", "living", pos=[2, 2], open=False, max_load_kg=6.0, fragile=True, material="cardboard",
              drop_tolerance_m=5.0)
        m.add("heavy_box", "generic_item", "living", label="crate of books", pos=[4, 4], mass_kg=14.0, size_m=[0.4, 0.3, 0.3])   # no wider: no topple
        e = m.env(pos=(1, 2), hold={"left": "heavy_box", "right": "heavy_box"})
        e.world.s["entities"]["heavy_box"]["held_hand"] = "both"
        ok(self, e.step("stack", "heavy_box", "light_box"))
        self.assertIn("crushed", ent(e, "light_box")["damage"])          # the kernel's crush: cardboard gives way, no break

    def test_stack_taller_than_the_shelf_gap_is_refused(self):
        m = self._kitchen()
        m.add("bowl0", "generic_item", "cab", label="bowl", stackable=True, accepts_items=True, size_m=[0.2, 0.2, 0.2], shelf=0)
        m.add("bowl1", "generic_item", "kitchen", label="bowl", stackable=True, accepts_items=True, size_m=[0.2, 0.2, 0.2])
        e = m.env(room="kitchen", pos=(5, 3), hold={"right": "bowl1"})
        r = e.step("stack", "bowl1", "bowl0")
        self.assertFalse(r["ok"])
        self.assertIn("shelf above", r["message"])
        m.c["entities"]["bowl1"].update(nest_group="glass_bowl", nest_step_m=0.03)
        m.c["entities"]["bowl0"].update(nest_group="glass_bowl", nest_step_m=0.03)
        e = m.env(room="kitchen", pos=(5, 3), hold={"right": "bowl1"})
        ok(self, e.step("stack", "bowl1", "bowl0"))
        self.assertTrue(ent(e, "bowl1")["nested"])

    def test_marginal_stack_topples_when_bumped(self):
        m = Mini()
        m.add("shelf", "table", "living", pos=[1, 1])
        ids = []
        for i in range(4):
            ids.append(m.add("bin_%d" % i, "storage_bin", "living", pos=[4, 4], size_m=[0.3, 0.2, 0.25], open=False, mass_kg=1.0))
        m.c["entities"]["bin_0"].update(location="shelf", relation="on", spos=[0.1, 0.1])
        m.c["entities"]["bin_0"].pop("pos")
        e = m.env(pos=(1, 3))
        for eid in ids[1:]:
            e.world.s["entities"][eid].update(location="robot", relation="held", held_hand="right")
            e.world.s["entities"][eid].pop("pos", None)
            e.world.s["robot"]["hands"]["right"] = eid
            ok(self, e.step("stack", eid, "bin_0"))
        self.assertTrue(ent(e, "bin_0")["stack_marginal"])
        e.world.s["events"] = [{"at_s": e.world.s["time_s"] + 5, "effect": "manipulation.bump", "args": {"target": "bin_0"}}]
        e.step("wait", seconds=10)
        self.assertNotEqual(ent(e, "bin_3")["location"], "bin_2")

    def test_unstack_top_down_and_extract_from_middle(self):
        m = self._kitchen()
        plate(m, "p0", "counter", 0.27, spos=[0.1, 0.1])
        plate(m, "p1", "kitchen", 0.27)
        plate(m, "p2", "kitchen", 0.27)
        from house_sim.tasks.setup import SetupCtx
        m.c["entities"]["p1"].pop("pos")
        m.c["entities"]["p2"].pop("pos")
        for lower, upper in (("p0", "p1"), ("p1", "p2")):
            m.c["entities"][upper].update(location=lower, relation="on", spos=[0.0, 0.0], stack_member=True)
        e = m.env(room="kitchen", pos=(2, 3))
        ok(self, e.step("unstack", "p0"))
        self.assertEqual(ent(e, "p2")["location"], "robot")
        e = m.env(room="kitchen", pos=(2, 3))
        r = ok(self, e.step("extract", "p1"))
        self.assertIn("toppled", r["message"])
        self.assertTrue(ent(e, "p2")["broken"])
        e = m.env(room="kitchen", pos=(2, 3))
        ok(self, e.step("extract", "p1", method="lift_stack"))
        self.assertEqual(ent(e, "p2")["location"], "p0")
        self.assertFalse(ent(e, "p2")["broken"])


class InsertTests(unittest.TestCase):
    def _pc(self):
        m = Mini()
        m.add("desk", "desk", "living", pos=[1, 1])
        m.add("pc", "generic_item", "desk", label="desktop PC", mass_kg=8.0, size_m=[0.2, 0.45, 0.45], spos=[0.1, 0.1],
              receptacles={"dimm_a": {"accepts": ["ram"], "item": None, "opening_m": [0.14, 0.01], "key": "notch_left", "seat_force_n": 35.0,
                                      "max_force_n": 120.0, "latch": True}})
        m.add("ram", "generic_item", "living", label="RAM stick", part_type="ram", tags=["ram"], size_m=[0.133, 0.031, 0.004], mass_kg=0.03)
        return m

    def test_ram_seats_and_latches_when_keyed_and_pressed(self):
        e = self._pc().env(pos=(1, 3), hold={"right": "ram"})
        ok(self, e.step("insert", "ram", "pc", orientation="notch_left", force_n=50))
        self.assertTrue(ent(e, "ram")["inserted"]["latched"])
        self.assertEqual(stat(e, "damage"), 0)

    def test_reversed_ram_is_refused_gently_and_cracks_when_forced(self):
        e = self._pc().env(pos=(1, 3), hold={"right": "ram"})
        self.assertFalse(e.step("insert", "ram", "pc", orientation="notch_right", force_n=20)["ok"])
        ok(self, e.step("insert", "ram", "pc", orientation="notch_right", force_n=110))
        self.assertTrue(ent(e, "ram")["inserted"]["jammed"])
        self.assertGreater(stat(e, "damage"), 0)

    def test_too_little_force_leaves_it_half_seated(self):
        e = self._pc().env(pos=(1, 3), hold={"right": "ram"})
        ok(self, e.step("insert", "ram", "pc", orientation="notch_left", force_n=15))
        self.assertFalse(ent(e, "ram")["seated"])
        self.assertTrue(ent(e, "ram")["protruding"])

    def test_book_into_a_full_row(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[2, 1])
        for i in range(28):
            m.add("b%d" % i, "book", "case", shelf=1)
        m.add("new", "book", "living")
        e = m.env(pos=(2, 2), hold={"right": "new"})
        r = ok(self, e.step("insert", "new", "case", shelf=1))
        self.assertIn("tight", r["message"])
        self.assertTrue(ent(e, "new")["wedged"])
        for i in range(28, 30):
            m.add("b%d" % i, "book", "case", shelf=1)
        e = m.env(pos=(2, 2), hold={"right": "new"})
        self.assertFalse(e.step("insert", "new", "case", shelf=1)["ok"])


class PackTests(unittest.TestCase):
    def _scene(self):
        m = Mini()
        m.add("box", "moving_box_medium", "living", pos=[2, 2], open=True)
        m.add("glass", "generic_item", "living", label="wine glass", material="glass", fragile=True, hollow=True, mass_kg=0.2,
              size_m=[0.08, 0.08, 0.2], volume_l=1.2, max_load_kg=2.0, drop_tolerance_m=0.1)
        m.add("books", "generic_item", "living", label="stack of books", material="paper", mass_kg=7.0, size_m=[0.3, 0.23, 0.2], volume_l=14.0)
        m.add("paper", "generic_item", "living", label="crumpled packing paper", void_fill=True, volume_l=55.0, size_m=[0.4, 0.4, 0.3], mass_kg=0.6)
        m.add("bubble", "generic_item", "living", label="bubble wrap roll", wrap_material="bubble_wrap", length_m=10.0, width_m=0.6, mass_kg=0.5,
              size_m=[0.6, 0.1, 0.1])
        return m

    def test_heavy_packed_onto_fragile_crushes_it(self):
        e = self._scene().env(pos=(1, 2), hold={"right": "glass", "left": "books"})
        ok(self, e.step("pack", "glass", "box"))
        ok(self, e.step("pack", "books", "box"))
        self.assertTrue(ent(e, "glass")["broken"])

    def test_heavy_first_fragile_wrapped_and_filled_survives_transport(self):
        e = self._scene().env(pos=(1, 2), hold={"right": "bubble", "left": "glass"})
        ok(self, e.step("wrap", "glass", tool="bubble", layers=2, stuff_hollows=True))
        self.assertGreaterEqual(ent(e, "glass")["wrapped"]["coverage"], 0.95)
        e.world.s["entities"]["books"].update(location="robot", relation="held", held_hand="right")
        e.world.s["entities"]["bubble"].update(location="living", relation="on", pos=[4, 4])
        e.world.s["entities"]["bubble"].pop("held_hand", None)
        e.world.s["robot"]["hands"]["right"] = "books"
        e.world.s["entities"]["books"].pop("pos", None)
        ok(self, e.step("pack", "books", "box"))
        ok(self, e.step("pack", "glass", "box"))
        e.world.s["entities"]["paper"].update(location="robot", relation="held", held_hand="right")
        e.world.s["entities"]["paper"].pop("pos", None)
        e.world.s["robot"]["hands"]["right"] = "paper"
        ok(self, e.step("pack", "paper", "box"))
        from house_sim.systems.manipulation.stacking import transport_test, void_fraction
        self.assertLess(void_fraction(e.world, "box"), 0.2)
        self.assertEqual(transport_test(e.world, "box", 0.3), [])

    def test_unwrapped_loose_glass_breaks_in_transport(self):
        e = self._scene().env(pos=(1, 2), hold={"right": "glass"})
        ok(self, e.step("pack", "glass", "box"))
        from house_sim.systems.manipulation.stacking import transport_test
        self.assertEqual(transport_test(e.world, "box", 0.3), ["glass"])

    def test_unpack_takes_the_top_item_and_refuses_buried_ones(self):
        e = self._scene().env(pos=(1, 2), hold={"right": "books", "left": "glass"})
        ok(self, e.step("pack", "books", "box"))
        ok(self, e.step("pack", "glass", "box"))
        e.step("crouch")
        self.assertFalse(e.step("unpack", "books")["ok"])
        ok(self, e.step("unpack", "box"))
        self.assertEqual(ent(e, "glass")["location"], "robot")


class ArrangeAlignGoalTests(unittest.TestCase):
    def _shelf_task(self):
        m = Mini()
        m.add("shelf", "table", "living", pos=[1, 1], size_m=[1.2, 0.4, 0.9], label="spice shelf")
        for i in range(4):
            m.add("jar%d" % i, "generic_item", "living", label="spice jar", size_m=[0.06, 0.06, 0.1], features={"label": 90}, pos=[4, 4])
        goal = {"kind": "row", "items": ["jar0", "jar1", "jar2", "jar3"], "max_dev_m": 0.01, "max_gap_std_m": 0.01,
                "facing": {"feature": "label", "deg": 90, "tol_deg": 20}}
        m.c["manipulation"] = {"goals": {"row": goal}, "jobs": {}}
        return m

    def test_evenly_spaced_row_with_labels_out_passes(self):
        m = self._shelf_task()
        task = {"success": [{"path": "manipulation.results.row.ok", "op": "is_true", "value": True}]}
        e = m.env(task=task, pos=(2, 2))
        for i in range(4):
            e.world.s["entities"]["jar%d" % i].update(location="robot", relation="held", held_hand="right")
            e.world.s["entities"]["jar%d" % i].pop("pos", None)
            e.world.s["robot"]["hands"]["right"] = "jar%d" % i
            ok(self, e.step("arrange", "jar%d" % i, "shelf", spos=[0.1 + i * 0.25, 0.3], yaw_deg=0))
        self.assertTrue(results(e, "row")["ok"], results(e, "row"))
        self.assertTrue(e.evaluate()["success"])

    def test_clustered_or_backward_row_fails(self):
        m = self._shelf_task()
        e = m.env(pos=(2, 2))
        for i, x in enumerate([0.1, 0.18, 0.5, 0.95]):
            e.world.s["entities"]["jar%d" % i].update(location="robot", relation="held", held_hand="right")
            e.world.s["entities"]["jar%d" % i].pop("pos", None)
            e.world.s["robot"]["hands"]["right"] = "jar%d" % i
            ok(self, e.step("arrange", "jar%d" % i, "shelf", spos=[x, 0.3], yaw_deg=180 if i == 2 else 0))
        res = results(e, "row")
        self.assertFalse(res["ok"])
        self.assertIn("jar2", res["facing_wrong"])

    def test_arrange_relative_to_plate_from_a_diners_seat(self):
        m = Mini()
        m.add("table", "dining_table", "living", pos=[1, 1])
        m.add("chair", "chair", "living", pos=[2, 3])
        plate(m, "dish", "table", 0.27, setting_role="plate", spos=[0.615, 0.55])
        m.add("fork", "generic_item", "living", label="fork", setting_role="fork", size_m=[0.03, 0.19, 0.01], pos=[6, 6])
        m.add("knife", "generic_item", "living", label="knife", setting_role="knife", size_m=[0.03, 0.22, 0.01], features={"blade_edge": 180}, pos=[6, 6])
        m.c["manipulation"] = {"goals": {"setting": {"kind": "settings", "table": "table", "seats": [{"chair": "chair", "id": "s1"}],
                                                      "require": ["plate", "fork", "knife"]}}, "jobs": {}}
        e = m.env(pos=(4, 3), hold={"left": "fork", "right": "knife"})
        ok(self, e.step("arrange", "fork", relative_to="dish", side="left", frame="chair", gap_m=0.03))
        ok(self, e.step("arrange", "knife", relative_to="dish", side="right", frame="chair", gap_m=0.03, facing="dish", feature="blade_edge"))
        e.step("wait", seconds=1)
        self.assertTrue(results(e, "setting")["ok"], results(e, "setting"))
        e = m.env(pos=(4, 3), hold={"left": "fork", "right": "knife"})
        ok(self, e.step("arrange", "fork", relative_to="dish", side="right", frame="chair", gap_m=0.03))
        ok(self, e.step("arrange", "knife", relative_to="dish", side="left", frame="chair", gap_m=0.03))
        e.step("wait", seconds=1)
        self.assertFalse(results(e, "setting")["ok"])

    def test_align_rug_parallel_to_sofa_and_center_vase(self):
        m = Mini()
        m.add("sofa", "sofa", "living", pos=[1, 1], yaw_deg=0, rot=0)
        m.add("rug", "rug", "living", pos=[1, 4], yaw_deg=90, rot=90, size_m=[1.4, 1.0, 0.01])
        m.add("table", "coffee_table", "living", pos=[6, 6])
        m.add("vase", "vase", "table", spos=[0.05, 0.05])
        m.c["manipulation"] = {"goals": {"rug": {"kind": "straight", "item": "rug", "ref": "sofa", "tol_deg": 2},
                                         "vase": {"kind": "centered", "item": "vase", "ref": "surface", "tol_m": 0.02}}, "jobs": {}}
        e = m.env(pos=(2, 3))
        e.step("wait", seconds=1)
        self.assertFalse(results(e, "rug")["ok"])
        ok(self, e.step("align", "rug", reference="sofa", mode="parallel"))
        self.assertTrue(results(e, "rug")["ok"], results(e, "rug"))
        ok(self, e.step("approach", "table"))
        ok(self, e.step("align", "vase", mode="center"))
        self.assertTrue(results(e, "vase")["ok"])

    def test_poses_goal_from_recorded_home(self):
        from house_sim.tasks.setup import apply_setup
        m = Mini()
        m.add("chair", "chair", "living", pos=[3, 3])
        cfg = copy.deepcopy(m.c)
        apply_setup(cfg, [{"op": "helper", "name": "manipulation.home_poses", "args": {"ids": ["chair"], "goal": "home"}},
                          {"op": "helper", "name": "manipulation.displace", "args": {"ids": ["chair"], "min_cells": 2}}],
                    default_registry(), 0, {"catalog_id": "test"})
        cfg["robot"] = {"room": "living", "pos": [1, 3]}
        e = HouseEnv(cfg)
        e.step("wait", seconds=1)
        self.assertFalse(results(e, "home")["ok"])
        self.assertEqual(e.world.s["memory"]["home_pose:chair"]["value"]["cell"], [3, 3])
        from house_sim.tasks.runner import Runner
        r = Runner(e, {})
        r.skill("manipulation.restore_poses", items=["chair"])
        e.step("wait", seconds=1)
        self.assertTrue(results(e, "home")["ok"], results(e, "home"))
        self.assertEqual(stat(e, "damage"), 0)

    def test_free_area_zone_and_walkway_goals(self):
        m = Mini()
        m.add("counter", "counter", "kitchen", pos=[1, 1])
        m.add("toaster", "generic_item", "counter", label="toaster", size_m=[0.3, 0.2, 0.2], spos=[0.8, 0.1])
        m.add("box", "storage_bin", "kitchen", pos=[0, 4], open=False)
        m.c["manipulation"] = {"goals": {"room": {"kind": "free_area", "surface": "counter", "w": 1.0, "d": 0.5},
                                         "zone": {"kind": "zone_clear", "room": "kitchen", "rect_m": [0.0, 1.8, 1.0, 2.6]},
                                         "walk": {"kind": "walkway", "room": "kitchen"}}, "jobs": {}}
        e = m.env(room="kitchen", pos=(1, 3))
        e.step("wait", seconds=1)
        self.assertFalse(results(e, "room")["ok"])
        self.assertFalse(results(e, "zone")["ok"])
        ok(self, e.step("slide", "toaster", to_spos=[1.65, 0.1]))
        ok(self, e.step("push", "box", direction="south", distance_m=1.0, lift=True))
        self.assertTrue(results(e, "room")["ok"])
        self.assertTrue(results(e, "zone")["ok"])
        self.assertTrue(results(e, "walk")["ok"])


# ======================================================================================
def jar_with_cap(m, jar="jar", cap="cap", loc="table", **cap_props):
    m.add(jar, "generic_item", loc, label="jar", material="glass", liquid_container=True, liquid="water", liquid_l=0.4, capacity_l=0.5,
          size_m=[0.09, 0.09, 0.14], spos=[0.1, 0.1], thread_on_body=True)
    thread = {"hand": "right", "turns_to_seat": 1.5, "engaged_turns": 1.5, "torque_nm": 1.2, "spec_nm": 1.2, "max_nm": 4.0, "mate": jar}
    thread.update(cap_props.pop("thread", {}))
    m.add(cap, "generic_item", jar, label="lid", relation="attached", thread=thread, size_m=[0.08, 0.08, 0.015], mass_kg=0.02, **cap_props)


class ConnectorTests(unittest.TestCase):
    def _vac(self):
        m = Mini()
        m.add("wand", "generic_item", "living", label="vacuum wand", mount={"accepts": ["vac32"]}, size_m=[0.04, 0.04, 0.8], mass_kg=0.5)
        m.add("tool", "generic_item", "living", label="crevice tool", size_m=[0.04, 0.04, 0.25], mass_kg=0.08,
              connector={"type": "push_click", "mates": ["vac32"], "engage_force_n": 25.0, "release": "button", "break_force_n": 60.0})
        return m

    def test_attach_with_enough_force_latches_and_survives_a_tug(self):
        m = self._vac()
        m.c["events"] = [{"at_step": 2, "effect": "manipulation.pull_test", "args": {"target": "wand"}}]
        e = m.env(hold={"right": "tool"})
        ok(self, e.step("attach", "tool", "wand", force_n=35))
        e.step("look")
        self.assertEqual(ent(e, "tool")["location"], "wand")
        self.assertTrue(ent(e, "tool")["connector"]["engaged"])

    def test_half_pushed_attachment_falls_off_in_use(self):
        m = self._vac()
        m.c["events"] = [{"at_step": 2, "effect": "manipulation.pull_test", "args": {"target": "wand"}}]
        e = m.env(hold={"right": "tool"})
        ok(self, e.step("attach", "tool", "wand", force_n=10))
        e.step("look")
        self.assertEqual(ent(e, "tool")["location"], "living")

    def test_detach_needs_the_release_button(self):
        m = self._vac()
        e = m.env(hold={"right": "tool"})
        ok(self, e.step("attach", "tool", "wand", force_n=35))
        self.assertFalse(e.step("detach", "tool")["ok"])
        ok(self, e.step("detach", "tool", release=True))
        self.assertEqual(stat(e, "damage"), 0)
        ok(self, e.step("attach", "tool", "wand", force_n=35))
        ok(self, e.step("detach", "tool", force_n=80))
        self.assertGreater(stat(e, "damage"), 0)

    def test_buttons_started_one_hole_off_are_misfastened(self):
        m = Mini()
        m.add("shirt", "generic_item", "living", label="shirt", material="fabric", fasteners={"buttons": {"count": 6, "engaged": False}})
        e = m.env(pos=(2, 3))
        ok(self, e.step("fasten", "shirt", which="buttons", start=1))
        self.assertTrue(ent(e, "shirt")["misfastened"])
        ok(self, e.step("unfasten", "shirt", which="buttons"))
        ok(self, e.step("fasten", "shirt", which="buttons", start=0))
        self.assertFalse(ent(e, "shirt")["misfastened"])


class ThreadTests(unittest.TestCase):
    def _scene(self, **cap_props):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        jar_with_cap(m, **cap_props)
        return m

    def test_untwist_removes_cap_and_twist_reseals(self):
        e = self._scene().env(pos=(1, 3))
        ok(self, e.step("untwist", "jar"))
        self.assertEqual(ent(e, "cap")["location"], "robot")
        self.assertFalse(ent(e, "jar")["sealed"])
        ok(self, e.step("twist", "cap", "jar", direction="cw", turns=1.6, torque_nm=1.2))
        self.assertTrue(ent(e, "jar")["sealed"])

    def test_overtightening_cracks_the_cap(self):
        e = self._scene().env(pos=(1, 3))
        ok(self, e.step("twist", "cap", direction="cw", turns=1.0))
        self.assertTrue(ent(e, "cap")["broken"])
        self.assertFalse(ent(e, "jar")["sealed"])

    def test_cross_threaded_cap_does_not_seal(self):
        e = self._scene().env(pos=(1, 3))
        ok(self, e.step("untwist", "jar"))
        ok(self, e.step("twist", "cap", "jar", direction="cw", turns=1.6, aligned=False))
        self.assertFalse(ent(e, "jar")["sealed"])

    def test_child_resistant_cap_needs_press_and_turn(self):
        e = self._scene(thread={"child_resistant": True}).env(pos=(1, 3))
        self.assertFalse(e.step("untwist", "jar")["ok"])
        ok(self, e.step("untwist", "jar", press=True))

    def test_shaken_soda_sprays_unless_vented(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        jar_with_cap(m, jar="soda", cap="soda_cap")
        m.c["entities"]["soda"].update(carbonated=True, agitation=0.8, liquid="soda", label="soda bottle")
        e = m.env(pos=(1, 3))
        ok(self, e.step("untwist", "soda"))
        self.assertGreater(stat(e, "spills"), 0)
        e = m.env(pos=(1, 3))
        ok(self, e.step("untwist", "soda", vent=True))
        self.assertEqual(stat(e, "spills"), 0)

    def test_live_bulb_is_not_unscrewed(self):
        m = Mini()
        m.add("fixture", "generic_item", "living", label="table lamp", on=True, portable=False, size_m=[0.3, 0.3, 0.5], accepts_items=True)
        m.add("bulb", "generic_item", "fixture", label="light bulb", category="bulb", relation="attached", fragile=True,
              thread={"hand": "right", "turns_to_seat": 2.0, "engaged_turns": 2.0, "torque_nm": 0.3, "spec_nm": 0.3})
        e = m.env(pos=(1, 2))
        r = e.step("untwist", "bulb")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")

    def test_screw_leg_seats_or_wobbles_and_unscrew_needs_support(self):
        m = Mini()
        m.add("sofa", "sofa", "living", pos=[1, 1])
        m.add("leg", "generic_item", "living", label="sofa leg", material="wood", size_m=[0.05, 0.05, 0.15],
              thread={"hand": "right", "turns_to_seat": 6.0, "engaged_turns": 0.0, "spec_nm": 2.0, "max_nm": 8.0})
        e = m.env(pos=(1, 3), hold={"right": "leg"})
        ok(self, e.step("screw_together", "leg", "sofa", turns=6.0))
        self.assertTrue(ent(e, "leg")["joined"]["seated"])
        e = m.env(pos=(1, 3), hold={"right": "leg"})
        ok(self, e.step("screw_together", "leg", "sofa", turns=6.0, aligned=False))
        self.assertTrue(ent(e, "leg")["joined"]["wobble"])
        m2 = Mini()
        m2.add("jar2", "generic_item", "living", label="blender jar", mass_kg=1.0, size_m=[0.15, 0.15, 0.3], accepts_items=True)
        m2.add("base", "generic_item", "jar2", label="blade base", relation="attached", size_m=[0.12, 0.12, 0.05],
               thread={"hand": "right", "turns_to_seat": 1.0, "engaged_turns": 1.0, "torque_nm": 1.0, "spec_nm": 1.0})
        e = m2.env(pos=(1, 2))
        r = ok(self, e.step("unscrew_apart", "base", support=False))
        self.assertIn("turned with it", r["message"])
        ok(self, e.step("unscrew_apart", "base", support=True))
        self.assertEqual(ent(e, "base")["location"], "robot")


class ClipZipSnapVelcroTests(unittest.TestCase):
    def test_clip_needs_a_rolled_top_and_full_coverage(self):
        m = Mini()
        m.add("chips", "generic_item", "living", label="chip bag", category="food_bag", opening_width_m=0.2, flexible_opening=True, size_m=[0.2, 0.08, 0.3])
        m.add("small", "small_clip", "living")
        m.add("big", "chip_clip", "living", pos=[3, 3])
        e = m.env(pos=(1, 2), hold={"right": "small"})
        ok(self, e.step("clip", "small", "chips"))
        self.assertFalse(ent(e, "chips")["sealed"])
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "chips"))
        m_big = ent(e, "big")
        m_big["clip"]["jaw_width_m"] = 0.21
        e.world.s["entities"]["big"].update(location="robot", relation="held", held_hand="right")
        e.world.s["entities"]["big"].pop("pos", None)
        e.world.s["robot"]["hands"]["right"] = "big"
        ok(self, e.step("clip", "big", "chips"))
        self.assertTrue(ent(e, "chips")["sealed"])

    def test_unclipping_held_papers_scatters_them(self):
        m = Mini()
        m.add("doc", "generic_item", "living", label="report", category="papers", sheets=20, thickness_mm=2.0)
        m.add("clipper", "chip_clip", "living")
        e = m.env(pos=(1, 2), hold={"right": "clipper"})
        ok(self, e.step("clip", "clipper", "doc"))
        e.world.s["entities"]["doc"].update(location="robot", relation="held", held_hand="left")
        e.world.s["entities"]["doc"].pop("pos", None)
        e.world.s["robot"]["hands"]["left"] = "doc"
        ok(self, e.step("unclip", "clipper"))
        self.assertTrue(ent(e, "doc")["order_lost"])

    def test_overfull_suitcase_needs_compression_and_crushes_fragile(self):
        m = Mini()
        m.add("case", "generic_item", "living", label="suitcase", container=True, accepts_items=True, openable=True, open=True, capacity_l=40.0,
              inner_size_m=[0.5, 0.3, 0.25], size_m=[0.55, 0.35, 0.27], zipper={"closed_frac": 0.0}, soft_sided=True)
        m.add("clothes", "generic_item", "case", label="clothes", volume_l=36.0, size_m=[0.45, 0.3, 0.2], soft=True)
        m.add("bottle", "generic_item", "case", label="perfume", material="glass", fragile=True, volume_l=7.0, size_m=[0.06, 0.06, 0.12])
        e = m.env(pos=(1, 2))
        self.assertFalse(e.step("zip", "case")["ok"])
        ok(self, e.step("zip", "case", compress=True))
        self.assertEqual(ent(e, "case")["zipper"]["closed_frac"], 1.0)
        self.assertTrue(ent(e, "bottle")["broken"])
        e = m.env(pos=(1, 2))
        ok(self, e.step("zip", "case", force=True))
        self.assertTrue(ent(e, "case")["zipper"]["teeth_split"])

    def test_zipper_snags_unless_fabric_held_taut(self):
        m = Mini()
        m.add("bag", "generic_item", "living", label="duffel", container=True, accepts_items=True, capacity_l=40.0, zipper={"closed_frac": 0.0},
              loose_lining=True)
        e = m.env(pos=(1, 2))
        ok(self, e.step("zip", "bag", taut=False))
        self.assertTrue(ent(e, "bag")["zipper"]["snagged"])
        e = m.env(pos=(1, 2))
        ok(self, e.step("zip", "bag"))
        self.assertEqual(ent(e, "bag")["zipper"]["closed_frac"], 1.0)

    def test_snaps_off_by_one_and_matching_snap_lid(self):
        m = Mini()
        m.add("cover", "generic_item", "living", label="duvet cover", material="fabric", snaps={"count": 4, "pairs": {}})
        e = m.env(pos=(1, 2))
        ok(self, e.step("snap", "cover", stud=0, socket=1))
        self.assertEqual(ent(e, "cover")["misaligned_snaps"], 1)
        ok(self, e.step("unsnap", "cover", stud=0))
        ok(self, e.step("snap", "cover", stud=0, socket=0))
        self.assertEqual(ent(e, "cover")["misaligned_snaps"], 0)
        m.add("tub", "generic_item", "living", label="food container", lid_fit="rect_1l", container=True, accepts_items=True, pos=[3, 3])
        m.add("lid_ok", "generic_item", "living", label="lid", lid_fit="rect_1l", corners=4, pos=[4, 4])
        m.add("lid_bad", "generic_item", "living", label="lid", lid_fit="round_1l", corners=1, pos=[4, 4])
        e = m.env(pos=(2, 3), hold={"right": "lid_bad"})
        ok(self, e.step("snap", "lid_bad", "tub"))
        self.assertFalse(ent(e, "tub")["sealed"])
        e = m.env(pos=(2, 3), hold={"right": "lid_ok"})
        ok(self, e.step("snap", "lid_ok", "tub", corners=4))
        self.assertTrue(ent(e, "tub")["sealed"])

    def test_velcro_overlap_and_quiet_peel(self):
        m = Mini()
        m.add("strap", "velcro_strap", "living")
        m.c["events"] = [{"at_step": 2, "effect": "manipulation.pull_test", "args": {"target": "strap"}}]
        e = m.env(pos=(1, 2))
        ok(self, e.step("velcro", "strap", overlap_frac=0.4))
        e.step("look")
        self.assertFalse(ent(e, "strap")["velcro"]["engaged"])
        e = m.env(pos=(1, 2))
        ok(self, e.step("velcro", "strap", overlap_frac=1.0))
        e.step("look")
        self.assertTrue(ent(e, "strap")["velcro"]["engaged"])
        noise0 = stat(e, "noise")
        ok(self, e.step("unvelcro", "strap", speed="slow"))
        self.assertLess(stat(e, "noise") - noise0, 0.1)
        ok(self, e.step("velcro", "strap"))
        noise1 = stat(e, "noise")
        ok(self, e.step("unvelcro", "strap", speed="fast", method="shear"))
        self.assertGreater(stat(e, "noise") - noise1, 0.3)
        self.assertGreater(stat(e, "damage"), 0)


class KnotTests(unittest.TestCase):
    def test_bundle_with_twine_and_untie_methods(self):
        m = Mini()
        m.add("news", "generic_item", "living", label="newspaper stack", size_m=[0.3, 0.4, 0.15], mass_kg=4.0)
        m.add("twine", "twine_spool", "living")
        e = m.env(pos=(1, 2), hold={"right": "twine"})
        ok(self, e.step("tie", "news", tool="twine", knot="square"))
        self.assertTrue(ent(e, "news")["bundled"])
        self.assertLess(ent(e, "twine")["length_m"], 50.0)

    def test_pulling_a_bow_by_its_ends_makes_a_hard_knot(self):
        m = Mini()
        m.add("shoe", "generic_item", "living", label="sneaker", laces=True, knot={"type": "bow", "releasable": True})
        e = m.env(pos=(1, 2))
        ok(self, e.step("untie", "shoe", method="pull_ends"))
        self.assertEqual(ent(e, "shoe")["knot"]["type"], "hard_knot")
        e = m.env(pos=(1, 2))
        ok(self, e.step("untie", "shoe", method="pull_tail"))
        self.assertIsNone(ent(e, "shoe")["knot"])

    def test_cutting_a_lace_that_must_be_kept_damages_it(self):
        m = Mini()
        m.add("shoe", "generic_item", "living", label="sneaker", laces=True, knot={"type": "double", "tight": True}, _cut_ok=False)
        m.add("scissors", "generic_item", "living", label="scissors", affordances=["cut"], hazards=["sharp"], safe_grips=["handle"])
        e = m.env(pos=(1, 2), hold={"right": "scissors"})
        ok(self, e.step("untie", "shoe", method="cut", tool="scissors"))
        self.assertGreater(stat(e, "damage"), 0)
        e = m.env(pos=(1, 2))
        r = ok(self, e.step("untie", "shoe", method="pick_apart"))
        self.assertEqual(stat(e, "damage"), 0)

    def test_untangle_cables_by_picking_not_pulling(self):
        m = Mini()
        for c in ("hdmi", "usb", "power"):
            m.add(c, "generic_item", "living", label=c + " cable", category="cable", coilable=True, length_m=2.0)
        from house_sim.tasks.setup import apply_setup
        apply_setup(m.c, [{"op": "helper", "name": "manipulation.tangle", "args": {"ids": ["hdmi", "usb", "power"], "crossings": 5, "knots": 1}}],
                    default_registry(), 0, {"catalog_id": "t"})
        e = m.env(pos=(1, 2))
        ok(self, e.step("untie", "hdmi", method="pull_ends"))
        self.assertTrue(ent(e, "usb")["tangle"]["tight"])
        e = m.env(pos=(1, 2))
        for _ in range(3):
            ok(self, e.step("untie", "hdmi", method="pick_apart"))
        self.assertEqual(ent(e, "power")["tangle"]["crossings"], 0)
        self.assertEqual(ent(e, "power")["tangle"]["knots"], 0)


class SealTests(unittest.TestCase):
    def test_zip_bag_with_crumbs_in_track_leaves_a_gap(self):
        m = Mini()
        m.add("zbag", "generic_item", "living", label="zip-top bag", zip_track=True, track_contaminated=True)
        e = m.env(pos=(1, 2))
        ok(self, e.step("seal", "zbag", clear_track=False))
        self.assertFalse(ent(e, "zbag")["sealed"])
        ok(self, e.step("seal", "zbag", clear_track=True))
        self.assertTrue(ent(e, "zbag")["sealed"])

    def test_gummed_envelope_needs_a_damp_not_soaked_sponge(self):
        m = Mini()
        m.add("env", "generic_item", "living", label="envelope", category="envelope", flap="gummed", accepts_items=True, container=True)
        m.add("sponge", "generic_item", "living", label="moistener sponge", wetness=0.4)
        e = m.env(pos=(1, 2))
        self.assertFalse(e.step("seal", "env")["ok"])
        m.c["entities"]["sponge"]["wetness"] = 1.0
        e = m.env(pos=(1, 2), hold={"right": "sponge"})
        ok(self, e.step("seal", "env", tool="sponge"))
        self.assertTrue(ent(e, "env")["warped"])
        m.c["entities"]["sponge"]["wetness"] = 0.4
        e = m.env(pos=(1, 2), hold={"right": "sponge"})
        ok(self, e.step("seal", "env", tool="sponge"))
        self.assertTrue(ent(e, "env")["sealed"])
        self.assertFalse(ent(e, "env").get("warped", False))


class WrapCoverTests(unittest.TestCase):
    def test_gift_wrap_coverage_price_tag_and_secrecy(self):
        m = Mini()
        m.add("gift", "generic_item", "living", label="ceramic mug (gift)", material="ceramic", size_m=[0.12, 0.09, 0.1], _recipient="kid",
              price_tag={"text": "$14.99"})
        m.add("paper", "gift_wrap_roll", "living")
        m.person("kid", "kitchen", [1, 1], role="child")
        e = m.env(pos=(1, 2), hold={"right": "paper"})
        ok(self, e.step("wrap", "gift", tool="paper"))
        self.assertTrue(ent(e, "gift")["price_tag_inside"])
        e = m.env(pos=(1, 2), hold={"right": "paper"})
        ok(self, e.step("remove_label", "gift"))
        scrap = e.world.s["robot"]["hands"]["left"]
        ok(self, e.step("place", scrap))
        ok(self, e.step("wrap", "gift", tool="paper"))
        self.assertEqual(ent(e, "gift")["wrapped"]["coverage"], 1.0)
        self.assertFalse(ent(e, "gift").get("price_tag_inside", False))
        self.assertFalse(ent(e, "gift").get("surprise_spoiled", False))
        m.c["people"]["kid"].update(location="living", pos=[3, 4])
        e = m.env(pos=(1, 2), hold={"right": "paper"})
        ok(self, e.step("wrap", "gift", tool="paper"))
        self.assertTrue(ent(e, "gift")["surprise_spoiled"])

    def test_small_sheet_leaves_gaps(self):
        m = Mini()
        m.add("gift", "gift_box", "living", open=False)
        m.add("sheet", "generic_item", "living", label="paper scrap", wrap_material="gift_paper", sheet_area_m2=0.1)
        e = m.env(pos=(1, 2), hold={"right": "sheet"})
        ok(self, e.step("wrap", "gift", tool="sheet"))
        self.assertLess(ent(e, "gift")["wrapped"]["coverage"], 0.95)

    def test_plastic_wrap_melts_on_hot_food_and_seals_cold_food(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("bowl", "generic_item", "table", label="leftover stew", category="leftovers", food={"kind": "stew"}, temperature_c=85.0,
              max_touch_temp_c=200.0, size_m=[0.2, 0.2, 0.1], spos=[0.1, 0.1])
        m.add("wrap", "generic_item", "kitchen", label="plastic wrap", wrap_material="plastic_wrap", length_m=30.0, width_m=0.3)
        e = m.env(room="kitchen", pos=(1, 3), hold={"right": "wrap"})
        ok(self, e.step("wrap", "bowl", tool="wrap"))
        self.assertTrue(ent(e, "bowl")["wrap_melted"])
        m.c["entities"]["bowl"]["temperature_c"] = 21.0
        e = m.env(room="kitchen", pos=(1, 3), hold={"right": "wrap"})
        ok(self, e.step("wrap", "bowl", tool="wrap"))
        self.assertTrue(ent(e, "bowl")["sealed"])

    def test_unwrapping_crumpled_paper_reveals_small_parts(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("crumple", "generic_item", "table", label="packing paper", packing_material=True, crumpled=True, openable=True, open=False,
              accepts_items=True, container=True, spos=[0.1, 0.1], size_m=[0.3, 0.3, 0.2])
        m.add("screws", "generic_item", "crumple", label="hardware bag", size_m=[0.08, 0.05, 0.02], relation="in")
        e = m.env(pos=(1, 3))
        self.assertNotIn("screws", e.observe()["visible_entities"])
        r = ok(self, e.step("unwrap", "crumple"))
        self.assertIn("screws", r["message"])
        self.assertIn("screws", e.observe()["visible_entities"])

    def test_cover_coverage_paint_event_and_tape_on_finish(self):
        m = Mini()
        m.add("dresser", "dresser", "living", pos=[1, 1])
        m.add("sheet", "plastic_sheeting", "living")
        m.add("small", "generic_item", "living", label="small towel", cover_area_m2=0.5)
        m.c["events"] = [{"at_step": 3, "effect": "manipulation.paint_event", "args": {"room": "living", "kind": "paint", "ids": ["dresser"]}}]
        e = m.env(pos=(1, 2), hold={"right": "sheet"})
        ok(self, e.step("cover", "dresser", tool="sheet", secure=["tape_on_cover"]))
        e.step("look")
        e.step("look")
        self.assertEqual((ent(e, "dresser").get("soil") or {}).get("paint", 0), 0)
        self.assertEqual(stat(e, "damage"), 0)
        e = m.env(pos=(1, 2), hold={"right": "small"})
        ok(self, e.step("cover", "dresser", tool="small", secure=["tape_on_finish"]))
        e.step("look")
        e.step("look")
        self.assertGreater(ent(e, "dresser")["soil"]["paint"], 0)
        self.assertGreater(stat(e, "damage"), 0)

    def test_unsecured_outdoor_cover_blows_off_in_a_gust(self):
        m = Mini()
        m.add("grill", "generic_item", "yard", label="grill", portable=False, size_m=[1.2, 0.6, 1.1], pos=[3, 3])
        m.add("tarp1", "portable_object", "yard", label="tarp", category="tarp", material="polyethylene",
              size_m=[0.4, 0.3, 0.08], mass_kg=1.2, volume_l=8.0, wind_lift_mps=6.0)
        m.c["events"] = [{"at_step": 2, "effect": "manipulation.gust", "args": {"mps": 9.0}}]
        e = m.env(room="yard", pos=(2, 3), hold={"right": "tarp1"})
        ok(self, e.step("cover", "grill", tool="tarp1"))
        e.step("look")
        self.assertFalse(ent(e, "grill")["covered"])
        e = m.env(room="yard", pos=(2, 3), hold={"right": "tarp1"})
        ok(self, e.step("cover", "grill", tool="tarp1", secure=["bungee", "clips"]))
        e.step("look")
        self.assertTrue(ent(e, "grill")["covered"])

    def test_uncovering_tips_pooled_rain_away_or_onto_cushions(self):
        m = Mini()
        m.add("seat", "generic_item", "yard", label="patio sofa", portable=False, size_m=[1.5, 0.8, 0.8], pos=[3, 3], material="fabric")
        m.add("cvr", "furniture_cover_sheet", "yard", cover_area_m2=4.0)
        e = m.env(room="yard", pos=(2, 3), hold={"right": "cvr"})
        ok(self, e.step("cover", "seat", tool="cvr", secure=["ties"]))
        e.world.s["entities"]["cvr"]["pooled_water_l"] = 3.0
        ok(self, e.step("uncover", "seat", direction="toward"))
        self.assertGreater(ent(e, "seat")["wetness"], 0.5)
        e = m.env(room="yard", pos=(2, 3), hold={"right": "cvr"})
        ok(self, e.step("cover", "seat", tool="cvr", secure=["ties"]))
        e.world.s["entities"]["cvr"]["pooled_water_l"] = 3.0
        ok(self, e.step("uncover", "seat", direction="away"))
        self.assertEqual(ent(e, "seat")["wetness"], 0.0)


class TapeLabelTests(unittest.TestCase):
    def test_h_taping_a_box_bottom_and_top(self):
        m = Mini()
        m.add("box", "moving_box_small", "living", pos=[2, 2], open=False)
        m.add("tape", "packing_tape", "living")
        e = m.env(pos=(1, 2), hold={"right": "tape"})
        ok(self, e.step("tape", "box", tool="tape", where="bottom_seam", pattern="h"))
        ok(self, e.step("tape", "box", tool="tape", where="top_seam", pattern="h"))
        self.assertEqual(ent(e, "box")["seams"]["bottom"]["taped"], "H")
        self.assertTrue(ent(e, "box")["sealed"])
        self.assertLess(ent(e, "tape")["length_m"], 50.0)

    def test_packing_tape_on_a_painted_wall_lifts_paint(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[0, 0], painted=True, material="paint")
        m.add("note", "generic_item", "living", label="note", material="paper", mass_kg=0.005, size_m=[0.1, 0.1, 0.001])
        m.add("pt", "packing_tape", "living", pos=[3, 3])
        m.add("blue", "painters_tape", "living", pos=[3, 3])
        e = m.env(pos=(1, 1), hold={"right": "pt", "left": "note"})
        ok(self, e.step("tape", "note", tool="pt", destination="wall", where="note"))
        ok(self, e.step("remove_tape", "wall", speed="slow"))
        self.assertGreater(stat(e, "damage"), 0)
        e = m.env(pos=(1, 1), hold={"right": "blue", "left": "note"})
        ok(self, e.step("tape", "note", tool="blue", destination="wall", where="note"))
        ok(self, e.step("remove_tape", "wall", speed="slow", angle_deg=170))
        self.assertEqual(stat(e, "damage"), 0)

    def test_label_on_wet_jar_peels_but_dry_jar_keeps_it(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("jar", "generic_item", "table", label="jar", material="glass", wetness=0.4, spos=[0.1, 0.1], size_m=[0.1, 0.1, 0.15])
        m.add("labels", "label_sheet", "kitchen")
        e = m.env(room="kitchen", pos=(1, 3), hold={"right": "labels"})
        ok(self, e.step("apply_label", "jar", text="Rice 2026-10-09", tool="labels"))
        e.step("wait", seconds=1200)
        self.assertIsNone(ent(e, "jar")["label_text"])
        m.c["entities"]["jar"]["wetness"] = 0.0
        e = m.env(room="kitchen", pos=(1, 3), hold={"right": "labels"})
        ok(self, e.step("apply_label", "jar", text="Rice 2026-10-09", tool="labels"))
        e.step("wait", seconds=1200)
        self.assertEqual(ent(e, "jar")["label_text"], "Rice 2026-10-09")

    def test_remove_label_soak_vs_scrape(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("jar", "generic_item", "table", label="jar", material="glass", label_text="Old pickles", spos=[0.1, 0.1])
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("remove_label", "jar", method="soak"))
        self.assertTrue(ent(e, "jar")["label_removed"])
        self.assertEqual((ent(e, "jar").get("soil") or {}).get("sticky", 0), 0)
        self.assertEqual(stat(e, "damage"), 0)
        e = m.env(room="kitchen", pos=(1, 3))
        ok(self, e.step("remove_label", "jar", method="scrape"))
        self.assertGreater(stat(e, "damage"), 0)


class SqueezeTearTests(unittest.TestCase):
    def test_box_flattens_only_after_the_tape_is_slit(self):
        m = Mini()
        m.add("box", "moving_box_small", "living", pos=[2, 2], open=False, seams={"top": {"taped": "strip"}, "bottom": {"taped": "H"}})
        m.add("cutter", "generic_item", "living", label="box cutter", affordances=["cut"], hazards=["sharp"], safe_grips=["handle"])
        e = m.env(pos=(1, 2), hold={"right": "cutter"})
        r = ok(self, e.step("squeeze", "box"))
        self.assertIn("springs back", r["message"])
        ok(self, e.step("remove_tape", "box", tool="cutter", where="all", depth_mm=3))
        ok(self, e.step("squeeze", "box"))
        self.assertTrue(ent(e, "box")["flattened"])
        self.assertLess(ent(e, "box")["size_m"][2], 0.01)

    def test_squeezing_a_box_that_still_holds_something_crushes_it(self):
        m = Mini()
        m.add("box", "moving_box_small", "living", pos=[2, 2], open=True)
        m.add("card", "generic_item", "box", label="warranty card", material="paper", crushable=True)
        m.add("can", "generic_item", "living", label="spray can", pressurized=True, pos=[3, 3])
        e = m.env(pos=(1, 2))
        r = ok(self, e.step("squeeze", "box"))
        self.assertIn("not empty", r["message"])
        self.assertTrue(ent(e, "card").get("crushed"))
        e = m.env(pos=(2, 3))
        r = e.step("squeeze", "can")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")

    def test_dispense_and_tear_a_sheet(self):
        m = Mini()
        m.add("paste", "generic_item", "living", label="toothpaste", dispenser=True, squeezable=True, liquid="toothpaste", liquid_l=0.075, capacity_l=0.075)
        m.add("foil", "generic_item", "living", label="foil roll", wrap_material="foil", length_m=10.0, width_m=0.3)
        e = m.env(pos=(1, 2), hold={"right": "paste"})
        r = ok(self, e.step("squeeze", "paste", amount_ml=0.5))
        self.assertAlmostEqual(ent(e, "paste")["liquid_l"], 0.0745, places=4)
        e = m.env(pos=(1, 2))
        ok(self, e.step("tear", "foil", length_m=0.4))
        sheet = [eid for eid in e.world.held_items()][0]
        self.assertAlmostEqual(ent(e, sheet)["sheet_area_m2"], 0.12, places=3)
        self.assertAlmostEqual(ent(e, "foil")["length_m"], 9.6, places=3)


# ======================================================================================
def _setup(m, ops):
    from house_sim.tasks.setup import apply_setup
    return apply_setup(m.c, ops, default_registry(), 0, {"catalog_id": "test"})


class JobTests(unittest.TestCase):
    def _co_carry(self):
        m = Mini()
        m.add("dresser", "dresser", "living", pos=[2, 2], mass_kg=40.0, size_m=[1.0, 0.5, 0.9])
        m.add("drawer_item", "generic_item", "dresser", label="sweater", relation="in")
        m.person("dad", "living", [4, 3], max_lift_kg=25.0)
        _setup(m, [{"op": "helper", "name": "manipulation.co_carry_job", "args": {"person": "dad", "item": "dresser", "route": ["kitchen"]}}])
        return m

    def test_co_carry_in_step_with_partner(self):
        e = self._co_carry().env(pos=(1, 2))
        ok(self, e.step("support", "dresser", mode="end"))
        ok(self, e.step("support", "dresser", mode="lift", height_m=0.75))
        ok(self, e.step("support", "dresser", mode="follow"))
        ok(self, e.step("support", "dresser", mode="lower"))
        job = e.world.s["manipulation"]["jobs"]["carry"]
        self.assertEqual(job["state"], "done")
        self.assertEqual(ent(e, "dresser")["location"], "kitchen")
        self.assertEqual(stat(e, "injury"), 0)

    def test_lifting_out_of_level_or_walking_off_alone(self):
        e = self._co_carry().env(pos=(1, 2))
        ok(self, e.step("support", "dresser", mode="end"))
        ok(self, e.step("support", "dresser", mode="lift", height_m=1.2))
        self.assertGreater(e.world.s["manipulation"]["jobs"]["carry"]["max_tilt_deg"], 10)
        self.assertEqual(ent(e, "drawer_item")["location"], "living")
        e = self._co_carry().env(pos=(1, 2))
        ok(self, e.step("support", "dresser", mode="end"))
        ok(self, e.step("support", "dresser", mode="lift", height_m=0.75))
        e.step("walk_to", cell=[9, 3])
        ok(self, e.step("move", "kitchen"))
        self.assertGreater(stat(e, "injury"), 0)
        self.assertEqual(e.world.s["manipulation"]["jobs"]["carry"]["state"], "failed")

    def _hold(self):
        m = Mini()
        m.add("shelf_board", "generic_item", "living", label="wall shelf board", material="wood", mass_kg=2.5, size_m=[0.8, 0.2, 0.02])
        m.person("ana", "living", [3, 3])
        _setup(m, [{"op": "helper", "name": "manipulation.hold_job", "args": {
            "person": "ana", "item": "shelf_board", "target": {"x": -0.04, "z": 1.45}, "start": {"x": 0.0, "z": 1.40},
            "needed_s": 60.0, "tol_m": 0.01, "mount_to": "living"}}])
        return m

    def test_holding_steady_with_corrections_in_the_workers_frame(self):
        e = self._hold().env(pos=(2, 3), hold={"right": "shelf_board"})
        ok(self, e.step("hold_steady", "shelf_board", duration_s=5))
        heard = [msg["text"] for msg in e.world.s["messages"] if msg["speaker"] == "ana"]
        self.assertTrue(heard and heard[-1].lower().startswith("up"))
        ok(self, e.step("hold_steady", "shelf_board", adjust="up", amount_m=0.05, frame="person", brace=True, duration_s=5))
        ok(self, e.step("hold_steady", "shelf_board", adjust="left", amount_m=0.04, frame="person", brace=True, duration_s=90))
        job = e.world.s["manipulation"]["jobs"]["hold"]
        self.assertEqual(job["state"], "done")
        self.assertTrue(ent(e, "shelf_board")["mounted"])

    def test_left_in_the_robots_frame_goes_the_wrong_way(self):
        e = self._hold().env(pos=(2, 3), hold={"right": "shelf_board"})
        ok(self, e.step("hold_steady", "shelf_board", adjust="up", amount_m=0.05, brace=True, duration_s=5))
        ok(self, e.step("hold_steady", "shelf_board", adjust="left", amount_m=0.04, frame="robot", brace=True, duration_s=90))
        job = e.world.s["manipulation"]["jobs"]["hold"]
        self.assertNotEqual(job["state"], "done")
        self.assertGreater(job["corrections"], 1)

    def test_letting_go_early_fails_the_job(self):
        e = self._hold().env(pos=(2, 3), hold={"right": "shelf_board"})
        ok(self, e.step("hold_steady", "shelf_board", duration_s=5))
        ok(self, e.step("place", "shelf_board", cell=[1, 4]))
        self.assertEqual(e.world.s["manipulation"]["jobs"]["hold"]["state"], "failed")

    def _light(self):
        m = Mini()
        m.add("torch", "flashlight", "living", on=True)
        m.add("valve_a", "generic_item", "kitchen", label="hot water valve", portable=False, pos=[2, 4])
        m.add("valve_b", "generic_item", "kitchen", label="trap nut", portable=False, pos=[3, 4])
        m.person("ana", "kitchen", [2, 3])
        _setup(m, [{"op": "helper", "name": "manipulation.light_job", "args": {
            "person": "ana", "item": "torch", "points": [{"eid": "valve_a", "until_s": 60}, {"eid": "valve_b", "until_s": 120}]}}])
        return m

    def test_following_the_work_point_with_the_light(self):
        e = self._light().env(room="kitchen", pos=(1, 3), hold={"right": "torch"})
        ok(self, e.step("hold_steady", "torch", aim_at="valve_a", duration_s=58))
        ok(self, e.step("hold_steady", "torch", aim_at="valve_b", duration_s=70))
        job = e.world.s["manipulation"]["jobs"]["light"]
        self.assertEqual(job["state"], "done")
        self.assertGreaterEqual(job["lit_frac"], 0.9)

    def test_light_left_on_the_first_spot_or_in_her_eyes(self):
        e = self._light().env(room="kitchen", pos=(1, 3), hold={"right": "torch"})
        ok(self, e.step("hold_steady", "torch", aim_at="valve_a", duration_s=130))
        self.assertLess(e.world.s["manipulation"]["jobs"]["light"]["lit_frac"], 0.9)
        e = self._light().env(room="kitchen", pos=(1, 3), hold={"right": "torch"})
        ok(self, e.step("hold_steady", "torch", aim_at="ana", duration_s=10))
        self.assertGreater(e.world.s["manipulation"]["jobs"]["light"]["glare_events"], 0)


class SystemTests(unittest.TestCase):
    def test_uncapped_marker_dries_out_capped_one_does_not(self):
        m = Mini()
        m.add("desk", "desk", "living", pos=[1, 1])
        for name in ("red", "blue"):
            m.add(name, "generic_item", "desk", label=name + " marker", ink_color=name, dries_uncapped_s=3600, sealed=True,
                  cap={"id": name + "_cap", "on": True, "loose": False}, size_m=[0.015, 0.015, 0.14], spos=[0.1 if name == "red" else 0.3, 0.1])
            m.add(name + "_cap", "generic_item", name, label=name + " cap", relation="attached", fits=name, snap_cap=True, seated=True)
        m.c["entities"]["red"].update(sealed=False, cap={"id": None, "on": False, "loose": False})
        m.c["entities"]["red_cap"].update(location="desk", relation="on", spos=[0.6, 0.1])
        e = m.env(pos=(1, 3))
        e.step("wait", seconds=4000)
        self.assertTrue(ent(e, "red")["dried_out"])
        self.assertFalse(ent(e, "blue").get("dried_out", False))

    def test_swapped_marker_cap_contaminates_the_tip(self):
        m = Mini()
        m.add("desk", "desk", "living", pos=[1, 1])
        m.add("red", "generic_item", "desk", label="red marker", ink_color="red", dries_uncapped_s=3600, size_m=[0.015, 0.015, 0.14], spos=[0.1, 0.1])
        m.add("blue_cap", "generic_item", "living", label="blue cap", fits="blue", snap_cap=True)
        e = m.env(pos=(1, 3), hold={"right": "blue_cap"})
        ok(self, e.step("snap", "blue_cap", "red"))
        self.assertTrue(ent(e, "red")["contaminated_tip"])
        self.assertFalse(ent(e, "red")["sealed"])

    def test_wind_lifts_unweighted_napkins_outdoors(self):
        m = Mini()
        m.add("ptable", "outdoor_table", "yard", pos=[3, 3])
        m.add("napkins", "generic_item", "ptable", label="paper napkins", material="paper", wind_lift_mps=3.0, hold_down_kg=0.2, spos=[0.1, 0.1],
              size_m=[0.2, 0.2, 0.03], mass_kg=0.05)
        m.add("napkins2", "generic_item", "ptable", label="paper napkins", material="paper", wind_lift_mps=3.0, hold_down_kg=0.2, spos=[0.5, 0.1],
              size_m=[0.2, 0.2, 0.03], mass_kg=0.05, accepts_items=True, surface=True)
        m.add("stone", "generic_item", "napkins2", label="napkin weight", mass_kg=0.4, size_m=[0.08, 0.08, 0.03], spos=[0.05, 0.05])
        m.c["weather"]["wind_mps"] = 5.0
        e = m.env(room="yard", pos=(2, 3))
        e.step("wait", seconds=60)
        self.assertTrue(ent(e, "napkins")["blown_away"])
        self.assertFalse(ent(e, "napkins2").get("blown_away", False))

    def test_observer_reports_a_crash_and_inspector_details(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("glass", "generic_item", "table", label="glass", material="glass", fragile=True, mass_kg=0.2, size_m=[0.07, 0.07, 0.12], spos=[0.1, 0.1])
        m.add("box", "moving_box_small", "living", pos=[3, 3], open=True)
        e = m.env(pos=(1, 3))
        r = e.step("pick_up", "glass", grip="firm")
        sounds = r["observation"].get("sounds") or []
        self.assertTrue(any(s["kind"] == "glass_breaking" for s in sounds))
        ok(self, e.step("approach", "box"))
        details = json.loads(ok(self, e.step("inspect", "box"))["message"])
        self.assertIn("packing", details)
        self.assertIn("seams", details)

    def test_answerer_knows_hidden_cut_permission(self):
        m = Mini()
        m.add("parcel", "generic_item", "living", label="parcel twine", _cut_ok=True, _ask_cut_ok=True)
        m.person("ana", "living", [2, 3])
        e = m.env(pos=(1, 3))
        r = ok(self, e.step("ask", "ana", topic="manip:cut_ok:parcel"))
        self.assertTrue(json.loads(r["message"])["value"])
        self.assertEqual(stat(e, "necessary_questions"), 1)
        ok(self, e.step("ask", "ana", topic="manip:cut_ok:parcel"))
        self.assertEqual(stat(e, "unnecessary_questions"), 1)


class SkillTests(unittest.TestCase):
    def test_retrieve_labelled_bin_from_the_middle_of_a_stack(self):
        from house_sim.tasks.runner import Runner
        m = Mini()
        for i, label in enumerate(["Camping", "Xmas ornaments", "Xmas lights"]):
            m.add("bin%d" % i, "storage_bin", "garage", pos=[3, 3], label_text=label, open=False, accepts_items=True, size_m=[0.6, 0.4, 0.32], mass_kg=4.0)
        m.add("orn", "glass_ornament", "bin1")
        _setup(m, [{"op": "helper", "name": "manipulation.make_stack", "args": {"items": ["bin0", "bin1", "bin2"]}}])
        m.person("ana", "living", [2, 3])
        e = m.env(room="garage", pos=(2, 3))
        r = Runner(e, {})
        r.skill("manipulation.retrieve_from_stack", target="bin1", staging_cells=[[6, 6], [7, 7]], deliver="ana")
        self.assertEqual(ent(e, "bin1")["location"], "ana")
        self.assertEqual(ent(e, "bin2")["location"], "bin0")
        self.assertFalse(ent(e, "orn")["broken"])
        self.assertEqual(stat(e, "damage"), 0)

    def test_give_skill_presents_handle_first(self):
        from house_sim.tasks.runner import Runner
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("scissors", "generic_item", "table", label="scissors", hazards=["sharp"], safe_grips=["handle"], spos=[0.2, 0.2], has_handle=True)
        m.person("ana", "living", [2, 3])
        e = m.env(room="kitchen", pos=(1, 3))
        Runner(e, {}).skill("manipulation.give", obj="scissors", person="ana")
        self.assertEqual(ent(e, "scissors")["location"], "ana")
        self.assertEqual(stat(e, "injury"), 0)



class NoiseGapRingTests(unittest.TestCase):
    def _plates(self, m, n=8):
        ids = [plate(m, "p%d" % i, "living", 0.27) for i in range(n)]
        for lower, upper in zip(ids, ids[1:]):
            m.c["entities"][upper].update(location=lower, relation="on", spos=[0.0, 0.0], stack_member=True)
            m.c["entities"][upper].pop("pos", None)
        return ids

    def test_carried_plate_stack_clinks_loudly_unless_carried_carefully(self):
        m = Mini()
        self._plates(m)
        e = m.env(hold={"left": "p0", "right": "p0"})
        e.world.s["entities"]["p0"]["held_hand"] = "both"
        ok(self, e.step("move", "kitchen", speed="normal"))
        self.assertGreaterEqual(e.world.s["manipulation"]["noise"]["peak_db"], 45.0)
        e = m.env(hold={"left": "p0", "right": "p0"})
        e.world.s["entities"]["p0"]["held_hand"] = "both"
        ok(self, e.step("move", "kitchen", speed="careful"))
        self.assertLess(e.world.s["manipulation"]["noise"]["peak_db"], 45.0)

    def test_fast_set_down_of_ceramics_on_stone_is_loud(self):
        m = Mini()
        m.add("counter", "table", "kitchen", pos=[1, 1], material="stone", label="stone counter")
        plate(m, "dish", "kitchen", 0.27)
        e = m.env(room="kitchen", pos=(1, 2), hold={"right": "dish"})
        ok(self, e.step("set_speed", "careful"))
        ok(self, e.step("place", "dish", "counter"))
        quiet = e.world.s["manipulation"]["noise"]["peak_db"]
        e = m.env(room="kitchen", pos=(1, 2), hold={"right": "dish"})
        ok(self, e.step("set_speed", "fast"))
        ok(self, e.step("place", "dish", "counter"))
        self.assertGreater(e.world.s["manipulation"]["noise"]["peak_db"], quiet + 15)
        self.assertLess(quiet, 45.0)

    def _fridge(self):
        m = Mini()
        m.add("fridge", "generic_item", "kitchen", label="refrigerator", category="appliance", portable=False, movable=True, mass_kg=90.0,
              size_m=[0.8, 0.7, 1.8], pos=[3, 0], foot_material="caster", feet="casters", water_line=True, plug_required=True,
              plugged_in=True, gap_behind_m=0.05, accepts_items=False)
        m.add("magnet", "generic_item", "fridge", relation="behind", label="photo magnet", material="plastic", size_m=[0.06, 0.04, 0.01],
              mass_kg=0.02, gap_depth_m=0.35, revealed=True)
        m.add("reacher", "generic_item", "kitchen", label="reacher grabber", category="reacher", reach_m=0.8, thickness_m=0.03,
              size_m=[0.8, 0.05, 0.05], mass_kg=0.3, pos=[3, 3])
        m.add("stick", "generic_item", "kitchen", label="yardstick", reach_m=0.9, thickness_m=0.008, size_m=[0.9, 0.03, 0.008],
              mass_kg=0.2, pos=[3, 3])
        return m

    def test_item_behind_fridge_needs_a_slim_tool(self):
        m = self._fridge()
        e = m.env(room="kitchen", pos=(3, 2))
        ok(self, e.step("crouch"))
        r = e.step("pick_up", "magnet")
        self.assertFalse(r["ok"])
        self.assertIn("too narrow", r["message"])
        e = m.env(room="kitchen", pos=(3, 2), hold={"right": "reacher"})
        ok(self, e.step("extract", "magnet", method="hook", tool="reacher"))
        self.assertEqual(ent(e, "magnet")["location"], "robot")
        self.assertEqual(ent(e, "fridge")["pos"], [3, 0])

    def test_bare_stick_pushes_item_further_back(self):
        m = self._fridge()
        e = m.env(room="kitchen", pos=(3, 2), hold={"right": "stick"})
        r = ok(self, e.step("extract", "magnet", method="hook", tool="stick"))
        self.assertIn("further back", r["message"])
        self.assertGreater(ent(e, "magnet")["gap_depth_m"], 0.35)
        self.assertEqual(ent(e, "magnet")["location"], "fridge")

    def test_pulling_the_fridge_out_kinks_its_water_line(self):
        m = self._fridge()
        e = m.env(room="kitchen", pos=(3, 2))
        ok(self, e.step("pull", "fridge", direction="south", distance_m=0.4))
        self.assertTrue(ent(e, "fridge").get("water_line_kinked"))
        self.assertIn("water_line_kinked", ent(e, "fridge")["damage"])
        self.assertGreater(stat(e, "damage"), 0)

    def _shower(self):
        m = Mini()
        m.add("rod", "generic_item", "living", label="shower curtain rod", portable=False, rings=12, size_m=[1.5, 0.05, 0.05], pos=[2, 1],
              mount_height_m=1.7, mounted=True)
        m.add("curtain", "generic_item", "rod", relation="attached", label="decorative curtain", material="polyester", grommets=12,
              hung_points=12, layer="outer")
        m.c["entities"]["rod"]["layers"] = {"outer": "curtain"}
        m.add("liner", "generic_item", "living", label="shower liner", category="shower_liner", material="plastic", grommets=12,
              size_m=[0.3, 0.2, 0.03], mass_kg=0.4)
        return m

    def test_liner_hangs_inside_on_every_grommet(self):
        m = self._shower()
        e = m.env(pos=(2, 2), hold={"right": "liner"})
        ok(self, e.step("attach", "liner", "rod"))
        rec = ent(e, "liner")
        self.assertEqual((rec["hung_points"], rec["layer"], rec["hem_inside_tub"], rec["sagging"]), (12, "inner", True, False))
        self.assertEqual(ent(e, "rod")["layers"], {"inner": "liner", "outer": "curtain"})

    def test_skipped_grommets_sag_and_wrong_layer_is_refused(self):
        m = self._shower()
        e = m.env(pos=(2, 2), hold={"right": "liner"})
        ok(self, e.step("attach", "liner", "rod", points=6))
        self.assertTrue(ent(e, "liner")["sagging"])
        e = m.env(pos=(2, 2), hold={"right": "liner"})
        self.assertFalse(e.step("attach", "liner", "rod", layer="outer")["ok"])
        e = m.env(pos=(2, 2))
        ok(self, e.step("detach", "curtain"))
        self.assertEqual(ent(e, "curtain")["location"], "robot")
        self.assertEqual(ent(e, "rod")["layers"], {})



class FixtureDetailTests(unittest.TestCase):
    def test_heavy_bike_pulls_a_light_hook_out(self):
        m = Mini()
        m.add("hook", "generic_item", "garage", label="bike hook", portable=False, mounted=True, mount_height_m=1.2, hang_slots=1,
              hang_height_m=0.0, hook_rating_kg=15.0, size_m=[0.05, 0.1, 0.1], pos=[1, 1])
        m.add("bike", "generic_item", "garage", label="e-bike", mass_kg=24.0, size_m=[1.7, 0.6, 1.0], pos=[2, 3])
        m.add("kid_bike", "generic_item", "garage", label="kids bike", mass_kg=8.0, size_m=[1.2, 0.5, 0.8], pos=[2, 3])
        e = m.env(room="garage", pos=(1, 2), hold={"left": "kid_bike", "right": "kid_bike"})
        e.world.s["entities"]["kid_bike"]["held_hand"] = "both"
        ok(self, e.step("place", "kid_bike", "hook", relation="hung"))
        self.assertEqual(ent(e, "kid_bike")["location"], "hook")
        e = m.env(room="garage", pos=(1, 2), hold={"left": "bike", "right": "bike"})
        e.world.s["entities"]["bike"]["held_hand"] = "both"
        e.world.robot["max_mass_kg"] = 30.0
        ok(self, e.step("place", "bike", "hook", relation="hung"))
        self.assertEqual(ent(e, "bike")["location"], "garage")
        self.assertIn("collapsed", ent(e, "hook")["damage"])            # the kernel's hang law: torn off the wall

    def test_rug_under_furniture_cannot_be_rotated_until_freed(self):
        m = Mini()
        m.add("rug", "generic_item", "living", label="area rug", category="rug", material="wool", size_m=[2.0, 1.5, 0.01], mass_kg=6.0,
              pos=[2, 4], portable=False, movable=True)
        m.add("sofa", "generic_item", "living", label="sofa", category="furniture", mass_kg=60.0, size_m=[2.0, 0.9, 0.85], pos=[2, 4], portable=False)
        e = m.env(pos=(1, 4))
        r = e.step("rotate", "rug", by_deg=180)
        self.assertFalse(r["ok"])
        self.assertIn("stands on the rug", r["message"])
        e = m.env(pos=(1, 4))
        e.world.s["entities"]["sofa"]["pos"] = [6, 9]
        e.world.s["entities"]["sofa"]["xy_m"] = [1.5, 2.25]
        ok(self, e.step("rotate", "rug", by_deg=180))

    def test_thin_card_must_be_slid_to_the_edge(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1], material="glass")
        m.add("card", "generic_item", "table", label="credit card", size_m=[0.085, 0.054, 0.001], mass_kg=0.005, lies_flush=True, spos=[0.3, 0.2])
        e = m.env(pos=(1, 2))
        self.assertFalse(e.step("pick_up", "card")["ok"])
        ok(self, e.step("slide", "card", direction="edge"))
        self.assertGreater(ent(e, "card")["overhang_m"], 0)
        ok(self, e.step("pick_up", "card"))

    def test_knife_carried_point_up_past_people_is_flagged(self):
        m = Mini()
        m.add("knife", "generic_item", "living", label="chef knife", hazards=["sharp"], safe_grips=["handle"], mass_kg=0.2, size_m=[0.3, 0.04, 0.02])
        m.person("ana", "kitchen", [1, 3])
        e = m.env(hold={"right": "knife"})
        e.world.s["entities"]["knife"]["held_grip"] = "handle"
        ok(self, e.step("move", "kitchen"))
        self.assertGreater(stat(e, "manip_sharp_exposed"), 0)
        e = m.env(hold={"right": "knife"})
        e.world.s["entities"]["knife"]["held_grip"] = "handle"
        ok(self, e.step("set_orientation", "knife", orientation="point_down"))
        ok(self, e.step("move", "kitchen"))
        self.assertEqual(stat(e, "manip_sharp_exposed"), 0)

    def test_brush_stored_on_its_bristles_bends_them(self):
        m = Mini()
        m.add("jar", "generic_item", "living", label="brush jar", container=True, accepts_items=True, capacity_l=1.0, inner_size_m=[0.1, 0.1, 0.25],
              size_m=[0.11, 0.11, 0.15], pos=[1, 2])
        m.add("brush", "generic_item", "living", label="paintbrush", bristles=True, size_m=[0.03, 0.02, 0.22], mass_kg=0.03)
        e = m.env(pos=(1, 3), hold={"right": "brush"})
        ok(self, e.step("set_orientation", "brush", orientation="upside_down"))
        ok(self, e.step("place", "brush", "jar"))
        self.assertIn("bristles_bent", ent(e, "brush")["damage"])
        e = m.env(pos=(1, 3), hold={"right": "brush"})
        ok(self, e.step("place", "brush", "jar"))
        self.assertFalse(ent(e, "brush").get("damage"))

    def test_cord_wrapped_round_a_hot_dryer_and_routing(self):
        m = Mini()
        m.add("dryer", "generic_item", "living", label="hair dryer", plug_required=True, plugged_in=False, cord_length_m=1.8, temperature_c=70.0,
              mass_kg=0.6, size_m=[0.25, 0.1, 0.25])
        e = m.env(hold={"right": "dryer"})
        ok(self, e.step("roll", "dryer", technique="wrap_body"))
        self.assertIn("cord_insulation_melted", ent(e, "dryer")["damage"])
        e = m.env(hold={"right": "dryer"})
        e.world.s["entities"]["dryer"]["temperature_c"] = 22.0
        ok(self, e.step("roll", "dryer", technique="wrap_body", tight=False))
        self.assertTrue(ent(e, "dryer")["cord_wrapped"]["ok"])
        m.add("ext", "generic_item", "living", label="extension cord", category="extension_cord", coilable=True, length_m=5.0, pos=[2, 2],
              coiled={"technique": "over_under", "loops": 6, "twist_per_loop": 0.0, "kinks": 0})
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "ext", unroll=True, route="across_walkway"))
        self.assertEqual(ent(e, "ext")["floor_hazard"], "trip")
        e = m.env(pos=(1, 2))
        ok(self, e.step("roll", "ext", unroll=True, route="along_wall"))
        self.assertIsNone(ent(e, "ext").get("floor_hazard"))



class RollingObjectTests(unittest.TestCase):
    def _counter(self):
        m = Mini()
        m.add("counter", "table", "kitchen", pos=[1, 1], material="stone", label="counter")
        m.add("orange", "generic_item", "counter", label="orange", round=True, mass_kg=0.2, size_m=[0.08, 0.08, 0.08], spos=[0.3, 0.2],
              drop_tolerance_m=0.5, fragile=True)
        m.c["events"] = [{"at_step": 1, "effect": "manipulation.roll_toward_edge", "args": {"target": "orange", "steps": 2}}]
        return m

    def test_rolling_orange_falls_unless_intercepted(self):
        e = self._counter().env(room="kitchen", pos=(1, 2))
        for _ in range(4):
            e.step("look")
        self.assertEqual(ent(e, "orange")["location"], "kitchen")
        e = self._counter().env(room="kitchen", pos=(1, 2))
        obs = ok(self, e.step("look"))
        ok(self, e.step("pick_up", "orange"))
        for _ in range(3):
            e.step("look")
        self.assertEqual(ent(e, "orange")["location"], "robot")
        self.assertNotIn("rolling", ent(e, "orange"))



class DemoAndRubricTests(unittest.TestCase):
    def test_process_goal_checks_the_order_effects_happened_in(self):
        m = Mini()
        m.add("jar", "generic_item", "living", label="jam jar", material="glass", size_m=[0.08, 0.08, 0.1], pos=[1, 2])
        m.person("ana", "living", [3, 3])
        _setup(m, [
            {"op": "helper", "name": "manipulation.demonstration",
             "args": {"person": "ana", "item": "jar", "steps": [{"action": "wrap", "layers": 2}, {"action": "apply_label", "text": "FRAGILE"}]}},
            {"op": "helper", "name": "manipulation.goal",
             "args": {"name": "proc", "kind": "process", "items": {"jar": [{"field": "wrapped.layers", "op": "gte", "value": 2},
                                                                          {"field": "label_text", "op": "eq", "value": "FRAGILE"}]}}}])
        e = m.env(pos=(1, 3))
        obs = e.observe()
        self.assertIn("demo", obs.get("demonstration", {}))
        rec = e.world.s["entities"]["jar"]
        e.world.s["entities"]["jar"]["label_text"] = "FRAGILE"
        e.step("look")
        e.world.s["entities"]["jar"]["wrapped"] = {"layers": 2, "coverage": 1.0}
        e.step("look")
        res = results(e, "proc")
        self.assertTrue(res["items"]["jar"]["done"])
        self.assertFalse(res["ok"])
        e = m.env(pos=(1, 3))
        e.world.s["entities"]["jar"]["wrapped"] = {"layers": 2, "coverage": 1.0}
        e.step("look")
        e.world.s["entities"]["jar"]["label_text"] = "FRAGILE"
        e.step("look")
        self.assertTrue(results(e, "proc")["ok"])

    def test_aesthetic_rubric_balance_and_tall_at_back(self):
        m = Mini()
        m.add("shelf", "table", "living", pos=[1, 1], size_m=[1.2, 0.4, 0.8], label="console")
        m.add("vase", "generic_item", "shelf", label="tall vase", size_m=[0.12, 0.12, 0.35], spos=[0.1, 0.05])
        m.add("bowl", "generic_item", "shelf", label="low bowl", size_m=[0.2, 0.2, 0.08], spos=[0.1, 0.18])
        m.add("frame", "generic_item", "shelf", label="photo frame", size_m=[0.15, 0.05, 0.2], spos=[0.3, 0.3])
        _setup(m, [{"op": "helper", "name": "manipulation.goal",
                    "args": {"name": "nice", "kind": "aesthetic", "surface": "shelf", "items": ["vase", "bowl", "frame"]}}])
        e = m.env(pos=(1, 2))
        e.step("look")
        res = results(e, "nice")
        self.assertFalse(res["ok"])
        self.assertTrue(any("lopsided" in p for p in res["problems"]))
        ok(self, e.step("arrange", "frame", "shelf", spos=[0.82, 0.1]))
        ok(self, e.step("arrange", "vase", "shelf", spos=[0.55, 0.22]))
        ok(self, e.step("arrange", "bowl", "shelf", spos=[0.25, 0.1]))
        self.assertTrue(results(e, "nice")["ok"], results(e, "nice"))



class AffordanceTests(unittest.TestCase):
    def _door(self):
        m = Mini()
        m.add("door", "generic_item", "living", label="bedroom door", portable=False, size_m=[0.8, 0.05, 2.0], pos=[3, 0],
              prop_need={"kind": "door", "force_n": 12.0})
        m.add("wedge", "generic_item", "living", label="rubber door wedge", material="rubber", wedge=True, mass_kg=0.15, size_m=[0.12, 0.04, 0.03])
        m.add("cup", "generic_item", "living", label="plastic cup", material="plastic", mass_kg=0.02, size_m=[0.08, 0.08, 0.1])
        m.add("laptop", "generic_item", "living", label="laptop", material="aluminum", mass_kg=1.4, value=1200, size_m=[0.32, 0.22, 0.02])
        m.c["events"] = [{"at_step": 2, "effect": "manipulation.bump", "args": {"target": "door"}}]
        return m

    def test_door_wedge_holds_but_a_cup_does_not(self):
        e = self._door().env(pos=(3, 1), hold={"right": "wedge"})
        ok(self, e.step("insert", "wedge", "door"))
        e.step("look")
        self.assertTrue(ent(e, "door")["propped"])
        e = self._door().env(pos=(3, 1), hold={"right": "cup"})
        ok(self, e.step("insert", "cup", "door"))
        e.step("look")
        self.assertFalse(ent(e, "door")["propped"])

    def test_laptop_as_doorstop_is_flagged(self):
        e = self._door().env(pos=(3, 1), hold={"right": "laptop"})
        r = ok(self, e.step("insert", "laptop", "door"))
        self.assertIn("valuable", r["message"])

    def test_shim_must_match_the_wobble_gap(self):
        m = Mini()
        m.add("tbl", "table", "living", pos=[1, 1], prop_need={"kind": "leg", "gap_mm": 4.0})
        m.add("card", "generic_item", "living", label="folded cardboard", material="cardboard", thickness_mm=4.5, size_m=[0.05, 0.04, 0.005], mass_kg=0.01)
        m.add("papers", "generic_item", "living", label="loose papers", material="paper", thickness_mm=4.0, loose_sheets=True, size_m=[0.2, 0.3, 0.004])
        e = m.env(pos=(1, 3), hold={"right": "card"})
        ok(self, e.step("insert", "card", "tbl"))
        self.assertTrue(ent(e, "tbl")["propped"])
        e = m.env(pos=(1, 3), hold={"right": "papers"})
        ok(self, e.step("insert", "papers", "tbl"))
        self.assertFalse(ent(e, "tbl")["propped"])

    def test_wet_paint_can_rings_the_papers_it_holds_down(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("doc", "generic_item", "table", label="papers", material="paper", accepts_items=True, surface=True, size_m=[0.21, 0.3, 0.002], spos=[0.2, 0.2])
        m.add("can", "generic_item", "living", label="paint can", material="metal", mass_kg=2.0, size_m=[0.17, 0.17, 0.19], soil={"paint": 0.5})
        e = m.env(pos=(1, 2), hold={"right": "can"})
        ok(self, e.step("place", "can", "doc"))
        self.assertIn("ring_stain", ent(e, "doc")["damage"])



class HouseholdDetailTests(unittest.TestCase):
    def test_towel_lifted_by_a_corner_trails_unless_gathered(self):
        m = Mini()
        m.add("towel", "generic_item", "living", label="bath towel", category="towel", material="cotton", size_m=[1.4, 0.7, 0.01], mass_kg=0.6, soft=True)
        e = m.env(pos=(1, 2))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "towel"))
        self.assertTrue(ent(e, "towel")["trailing"])
        self.assertGreater(stat(e, "manip_trailing"), 0)
        e = m.env(pos=(1, 2))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "towel", grip="scoop"))
        self.assertFalse(ent(e, "towel").get("trailing"))

    def test_unlatched_case_swings_open_when_lifted(self):
        m = Mini()
        m.add("case", "generic_item", "living", label="guitar case", container=True, openable=True, open=False, needs_latch=True, latched=False,
              accepts_items=True, capacity_l=60.0, inner_size_m=[1.0, 0.4, 0.12], size_m=[1.05, 0.42, 0.14], mass_kg=3.0, has_handle=True)
        m.add("guitar", "generic_item", "case", label="guitar", material="wood", fragile=True, mass_kg=2.5, size_m=[1.0, 0.38, 0.1], volume_l=20.0)
        e = m.env(pos=(1, 2))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "case", grip="handle"))
        self.assertNotEqual(ent(e, "guitar")["location"], "case")
        e = m.env(pos=(1, 2))
        e.world.s["entities"]["case"]["latched"] = True
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "case", grip="handle"))
        self.assertEqual(ent(e, "guitar")["location"], "case")

    def test_grabbing_a_grocery_bag_by_its_body_squashes_the_bread(self):
        m = Mini()
        m.add("bag", "bag", "living", label="grocery bag", material="paper", handle_rating_kg=8.0)
        m.add("bread", "generic_item", "bag", label="loaf of bread", soft=True, crush_force_n=15.0, mass_kg=0.5, size_m=[0.25, 0.12, 0.1], volume_l=2.0)
        e = m.env(pos=(1, 2))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bag", grip="normal"))
        self.assertTrue(ent(e, "bread").get("squashed"))
        e = m.env(pos=(1, 2))
        ok(self, e.step("crouch"))
        ok(self, e.step("pick_up", "bag", grip="handle"))
        self.assertFalse(ent(e, "bread").get("squashed"))

    def test_fast_set_down_cracks_eggs_in_the_bag(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("bag", "bag", "kitchen", label="grocery bag", material="paper", handle_rating_kg=8.0)
        m.add("eggs", "generic_item", "bag", label="loose eggs", fragile=True, drop_tolerance_m=0.03, mass_kg=0.7, size_m=[0.3, 0.1, 0.07],
              volume_l=2.0)
        e = m.env(room="kitchen", pos=(1, 2), hold={"right": "bag"})
        ok(self, e.step("set_speed", "fast"))
        ok(self, e.step("place", "bag", "table"))
        self.assertTrue(ent(e, "eggs")["broken"])
        e = m.env(room="kitchen", pos=(1, 2), hold={"right": "bag"})
        ok(self, e.step("set_speed", "careful"))
        ok(self, e.step("place", "bag", "table"))
        self.assertFalse(ent(e, "eggs").get("broken"))

    def test_shoe_pairs_goal(self):
        m = Mini()
        for i, xy in enumerate(([1.0, 1.0], [1.0, 1.5], [3.0, 3.0], [3.12, 3.0])):
            m.add("shoe%d" % i, "generic_item", "living", label="shoe", size_m=[0.1, 0.28, 0.1], pos=[int(xy[0] / 0.5), int(xy[1] / 0.5)], xy_m=xy)
        _setup(m, [{"op": "helper", "name": "manipulation.goal",
                    "args": {"name": "shoes", "kind": "pairs", "pairs": {"a": {"items": ["shoe0", "shoe1"]}, "b": {"items": ["shoe2", "shoe3"]}}}}])
        e = m.env(pos=(1, 2))
        e.step("look")
        res = results(e, "shoes")
        self.assertTrue(res["pairs"]["b"]["ok"])
        self.assertFalse(res["pairs"]["a"]["ok"])

    def test_unclasped_necklaces_stored_together_tangle(self):
        m = Mini()
        m.add("box", "generic_item", "living", label="jewelry box", container=True, accepts_items=True, capacity_l=1.0, inner_size_m=[0.2, 0.12, 0.05],
              size_m=[0.22, 0.14, 0.08], pos=[1, 2])
        m.add("n1", "generic_item", "box", label="silver necklace", category="necklace", mass_kg=0.01, size_m=[0.05, 0.03, 0.01], volume_l=0.01)
        m.add("n2", "generic_item", "living", label="gold necklace", category="necklace", mass_kg=0.01, size_m=[0.05, 0.03, 0.01], volume_l=0.01)
        e = m.env(pos=(1, 3), hold={"right": "n2"})
        ok(self, e.step("place", "n2", "box"))
        self.assertGreater(ent(e, "n2")["tangle"]["crossings"], 0)
        e = m.env(pos=(1, 3), hold={"right": "n2"})
        e.world.s["entities"]["n1"]["clasped"] = True
        e.world.s["entities"]["n2"]["clasped"] = True
        ok(self, e.step("place", "n2", "box"))
        self.assertFalse(ent(e, "n2").get("tangle"))

    def test_lopsided_plate_tree_tips(self):
        m = Mini()
        m.add("tree", "plate_tree", "garage", pos=[1, 1])
        for i in range(3):
            m.add("pl%d" % i, "weight_plate", "garage", mass_kg=15.0, pos=[3, 3])
        e = m.env(room="garage", pos=(1, 2), hold={"left": "pl0", "right": "pl0"})
        e.world.s["entities"]["pl0"]["held_hand"] = "both"
        ok(self, e.step("insert", "pl0", "tree", slot="peg_left_low"))
        self.assertFalse(ent(e, "tree").get("tipped_over"))
        e.world.s["entities"]["pl1"].update(location="robot", relation="held", held_hand="both")
        e.world.s["entities"]["pl1"].pop("pos", None)
        e.world.robot["hands"] = {"left": "pl1", "right": "pl1"}
        r = ok(self, e.step("insert", "pl1", "tree", slot="peg_left_high"))
        self.assertTrue(ent(e, "tree").get("tipped_over"), r["message"])

    def test_books_lean_without_a_bookend(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[1, 1])
        m.add("b1", "book", "living", size_m=[0.15, 0.23, 0.03], mass_kg=0.5)
        e = m.env(pos=(1, 2), hold={"right": "b1"})
        ok(self, e.step("insert", "b1", "case", shelf=1))
        self.assertGreater(ent(e, "b1")["lean_deg"], 15.0)

    def test_bike_needs_parking(self):
        m = Mini()
        m.add("bike", "generic_item", "garage", label="bicycle", wheeled=True, kickstand="up", mass_kg=12.0, size_m=[0.6, 1.7, 1.0], pos=[2, 2])
        m.add("car", "generic_item", "garage", label="car", category="car", portable=False, blocks_path=True, size_m=[1.8, 4.5, 1.5], pos=[8, 0])
        e = m.env(room="garage", pos=(1, 2))
        ok(self, e.step("roll", "bike", to_cell=[4, 2]))
        self.assertEqual(ent(e, "bike").get("orientation"), "on_side")
        e = m.env(room="garage", pos=(1, 2))
        ok(self, e.step("roll", "bike", to_cell=[4, 2], park="kickstand"))
        self.assertEqual(ent(e, "bike")["kickstand"], "down")
        e = m.env(room="garage", pos=(1, 2))
        ok(self, e.step("roll", "bike", to_cell=[7, 2], park="lean"))
        self.assertIn("paint_scratch", ent(e, "car").get("damage", {}))

    def test_shaking_a_deck_shuffles_it_and_flatpack_racks(self):
        m = Mini()
        m.add("deck", "generic_item", "living", label="deck of cards", category="deck", mass_kg=0.1, size_m=[0.06, 0.09, 0.02], order="sorted")
        e = m.env(hold={"right": "deck"})
        ok(self, e.step("shake", "deck", seconds=6))
        self.assertTrue(ent(e, "deck")["shuffled"])
        m.add("shelf", "generic_item", "living", label="flat-pack bookcase", category="furniture", weak_joints=True, mass_kg=18.0,
              size_m=[0.8, 0.3, 1.8], pos=[4, 4], portable=False, foot_material="felt")
        e = m.env(pos=(3, 4))
        ok(self, e.step("push", "shelf", direction="east", distance_m=0.25, height="high"))
        self.assertIn("racked_joints", ent(e, "shelf")["damage"])

    def test_inflatable_needs_open_valve_to_deflate(self):
        m = Mini()
        m.add("pool", "generic_item", "yard", label="inflatable pool", inflated=True, valve_open=False, mass_kg=2.0, size_m=[1.5, 1.5, 0.3],
              deflated_size_m=[0.4, 0.3, 0.1], pos=[2, 2])
        e = m.env(room="yard", pos=(1, 2))
        self.assertFalse(e.step("squeeze", "pool")["ok"])
        e.world.s["entities"]["pool"]["valve_open"] = True
        ok(self, e.step("squeeze", "pool"))
        self.assertFalse(ent(e, "pool")["inflated"])



class ImitationTests(unittest.TestCase):
    def test_same_fields_goal_copies_relevant_features_only(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        for i, spos in enumerate(([0.1, 0.1], [0.4, 0.1], [0.7, 0.1])):
            m.add("jar%d" % i, "generic_item", "table", label="jar", material="glass", size_m=[0.1, 0.1, 0.15], spos=spos)
        m.c["entities"]["jar0"].update(label_text="Flour", label_face="front", label_height_m=0.06)
        _setup(m, [{"op": "helper", "name": "manipulation.goal",
                    "args": {"name": "same", "kind": "same_fields", "exemplar": "jar0", "items": ["jar1", "jar2"],
                             "fields": ["label_face", "label_height_m"]}}])
        e = m.env(room="kitchen", pos=(1, 2))
        e.world.s["entities"]["jar1"].update(label_text="Sugar", label_face="front", label_height_m=0.065)
        e.world.s["entities"]["jar2"].update(label_text="Rice", label_face="back", label_height_m=0.06)
        e.step("look")
        res = results(e, "same")
        self.assertTrue(res["items"]["jar1"]["ok"])
        self.assertEqual(res["items"]["jar2"]["differs"], ["label_face"])


# ======================================================================================
class IntegrationRoundTests(unittest.TestCase):
    """Kernel v2.1 adoption and behaviour with every other domain loaded."""

    def test_sharp_tool_picked_by_handle_and_presented_is_safe_with_all_domains(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("scissors", "generic_item", "table", label="kitchen scissors", category="scissors", material="steel", hazards=["sharp"],
              safe_grips=["handle"], has_handle=True, mass_kg=0.12, size_m=[0.21, 0.08, 0.015], spos=[0.1, 0.1])
        m.person("ana", "living", [2, 3])
        e = m.env(pos=(1, 3))
        ok(self, e.step("pick_up", "scissors", grip="handle"))
        ok(self, e.step("set_orientation", "scissors", toward="ana"))
        self.assertEqual(ent(e, "scissors")["presented"], {"feature": "handle", "toward": "ana", "grip": "spine"})
        ok(self, e.step("hand_over", "scissors", "ana"))
        self.assertEqual(ent(e, "scissors")["location"], "ana")
        self.assertEqual(stat(e, "injury"), 0)
        self.assertEqual(stat(e, "safety_violations"), 0)
        # held by the (safe) handle and handed over as is: the blades point at the recipient
        e = m.env(pos=(1, 3))
        ok(self, e.step("pick_up", "scissors", grip="handle"))
        ok(self, e.step("hand_over", "scissors", "ana"))
        self.assertGreater(stat(e, "injury"), 0)

    def test_kernel_grip_memory_lets_a_sharp_item_be_carried_and_set_down(self):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("desk", "table", "living", pos=[1, 1])
        m.add("knife", "generic_item", "desk", label="chef knife", hazards=["sharp"], safe_grips=["handle"], mass_kg=0.2,
              size_m=[0.3, 0.04, 0.02], spos=[0.1, 0.1])
        e = m.env(pos=(1, 3))
        ok(self, e.step("pick_up", "knife", grip="handle"))
        self.assertEqual(ent(e, "knife")["held_grip"], "handle")
        ok(self, e.step("set_orientation", "knife", orientation="point_down"))
        ok(self, e.step("move", "kitchen"))
        ok(self, e.step("approach", "table"))
        ok(self, e.step("place", "knife", "table"))
        self.assertEqual(ent(e, "knife")["location"], "table")
        self.assertNotIn("held_grip", ent(e, "knife"))
        e = m.env(pos=(1, 3))
        self.assertFalse(e.step("pick_up", "knife", grip="normal")["ok"])

    def test_items_stacked_on_a_closed_bin_are_visible_and_unstackable(self):
        m = Mini()
        m.add("bin_a", "storage_bin", "living", pos=[2, 2], open=False, mass_kg=2.0, size_m=[0.5, 0.35, 0.3])
        m.add("inside", "generic_item", "bin_a", label="string lights", relation="in", size_m=[0.2, 0.2, 0.1])
        m.add("bin_b", "storage_bin", "bin_a", relation="on", open=False, mass_kg=1.5, size_m=[0.5, 0.35, 0.3], stack_member=True)
        e = m.env(pos=(1, 2))
        visible = e.observe().get("visible_entities") or {}
        self.assertIn("bin_b", visible)
        self.assertNotIn("inside", visible)
        ok(self, e.step("crouch"))
        ok(self, e.step("unstack", "bin_a"))
        self.assertEqual(ent(e, "bin_b")["location"], "robot")

    def test_crush_kg_is_the_crush_limit_and_max_load_kg_the_fallback(self):
        m = Mini()
        m.add("light_box", "moving_box_small", "living", pos=[2, 2], open=False, crush_kg=6.0, material="cardboard", drop_tolerance_m=5.0)
        m.add("heavy_box", "generic_item", "living", label="crate of books", pos=[4, 4], mass_kg=14.0, size_m=[0.4, 0.3, 0.3])   # no wider: no topple
        e = m.env(pos=(1, 2), hold={"left": "heavy_box", "right": "heavy_box"})
        e.world.s["entities"]["heavy_box"]["held_hand"] = "both"
        ok(self, e.step("stack", "heavy_box", "light_box"))
        self.assertIn("crushed", ent(e, "light_box")["damage"])
        m.c["entities"]["light_box"].pop("crush_kg")      # the box's own 30 kg rating applies again
        e = m.env(pos=(1, 2), hold={"left": "heavy_box", "right": "heavy_box"})
        e.world.s["entities"]["heavy_box"]["held_hand"] = "both"
        ok(self, e.step("stack", "heavy_box", "light_box"))
        self.assertNotIn("crushed", ent(e, "light_box").get("damage") or {})

    def test_timers_follow_fields_set_by_a_scenario_and_prune_the_watch_list(self):
        from house_sim.kernel import plugins
        self.assertEqual(plugins.SYSTEM_PERIODS.get("manipulation.timers"), 120.0)
        m = Mini()
        m.add("table", "table", "kitchen", pos=[1, 1])
        m.add("jar", "generic_item", "table", label="jar", material="glass", spos=[0.1, 0.1], size_m=[0.1, 0.1, 0.15],
              label_text="Flour", label_adhesion="poor", label_peels_at_s=300.0)
        e = m.env(room="kitchen", pos=(1, 3))
        self.assertIn("jar", e.world.s["manipulation"]["watch"])
        e.step("wait", seconds=200)
        self.assertEqual(ent(e, "jar")["label_text"], "Flour")
        e.step("wait", seconds=400)
        self.assertIsNone(ent(e, "jar")["label_text"])
        self.assertTrue(ent(e, "jar")["label_peeled"])
        self.assertNotIn("jar", e.world.s["manipulation"].get("watch", []))

    def test_time_systems_never_scan_every_entity(self):
        from house_sim.systems.manipulation import world as MW

        class CountingDict(dict):
            scans = 0

            def __iter__(self):
                CountingDict.scans += 1
                return dict.__iter__(self)

            def items(self):
                CountingDict.scans += 1
                return dict.items(self)

            def keys(self):
                CountingDict.scans += 1
                return dict.keys(self)

            def values(self):
                CountingDict.scans += 1
                return dict.values(self)

        m = Mini()
        for i in range(40):
            m.add("junk_%d" % i, "generic_item", "living", label="junk", pos=[i % 9, 4 + i // 9])
        m.add("marker", "generic_item", "living", label="marker", dries_uncapped_s=3600, pos=[1, 1])
        m.add("kite", "generic_item", "yard", label="paper kite", wind_lift_mps=4.0, pos=[5, 5], secured=True)
        e = m.env()
        w = e.world
        w.begin()
        w.s["entities"] = CountingDict(w.s["entities"])
        w.s["weather"]["wind_mps"] = 6.0
        MW.timers_system(w, 120.0, None)     # builds the static indexes once
        MW.wind_system(w, 30.0, None)
        CountingDict.scans = 0
        for _ in range(5):
            MW.falls_system(w, 30.0, None)
            MW.timers_system(w, 120.0, None)
            MW.wind_system(w, 30.0, None)
        self.assertEqual(CountingDict.scans, 0)
        self.assertGreater(ent(e, "marker")["uncapped_s"], 600)

    def test_furnished_floor_items_stay_out_of_door_swings(self):
        # kernel v2.3 door model: doorway cells + 4 neighbours, the leaf sweep of a door swinging into the room
        # (geometry.door_swing) and overhead-door thresholds; the old private rule (doorway + 8 neighbours) is gone
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        mine = {"toy_block", "toy_car", "doll", "stuffed_animal", "ball", "basketball", "soccer_ball", "frisbee", "beach_ball",
                "yoga_mat", "hand_truck", "moving_box_small", "toy_bin"}
        for seed, opts in ((0, {}), (1, {}), (2, {}), (41, {}), (97, {}), (21, {}), (16, {"dwelling": "house", "require": ["garage", "patio"]})):
            cfg = generate_house(seed, **opts)
            for eid, rec in sorted(cfg["entities"].items()):
                rid = rec.get("location")
                if rec.get("type") not in mine or rid not in cfg["rooms"]:
                    continue
                zone = geometry.door_clearance(cfg, rid)
                for door in [c.get("door") for c in cfg["connections"].values() if rid in c.get("cells", {}) and c.get("door")]:
                    srid, leaf = geometry.door_swing(cfg, door)
                    if srid == rid:
                        zone |= set(leaf)
                self.assertFalse(set(geometry.footprint(rec)) & zone, "{} {} {} at {}".format(seed, opts, eid, rec.get("pos")))

    def test_free_cell_is_reachable_and_door_zones_can_be_cleared(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.tasks.setup import SetupCtx
        m = Mini()
        # a counter run walls off the kitchen's top-right corner (cells x 5..7, y 0..1)
        for i, (x, y) in enumerate([(4, 0), (4, 1), (4, 2), (5, 2), (6, 2), (7, 2)]):
            m.add("counter_%d" % i, "generic_item", "kitchen", label="counter", portable=False, blocks_path=True, pos=[x, y],
                  size_m=[0.5, 0.5, 0.9], mass_kg=60.0)
        m.add("basket", "generic_item", "garage", label="laundry basket", pos=[4, 1], size_m=[0.5, 0.4, 0.3], mass_kg=1.5)
        ctx = SetupCtx(m.c, default_registry(), random.Random(0), 0, {})
        cell = SCENARIO_HELPERS["manipulation.free_cell"](ctx, room="kitchen", near=[6, 0])["cell"]
        self.assertFalse(5 <= cell[0] <= 7 and cell[1] <= 1, cell)
        out = SCENARIO_HELPERS["manipulation.clear_door_zones"](ctx, rooms=["garage"])
        self.assertEqual(out["moved_from_doors"], ["basket"])
        # kernel door clearance (v2.3): doorway cells + neighbours, door leaves, the overhead door's threshold run
        self.assertFalse(set(geometry.footprint(m.c["entities"]["basket"])) & geometry.door_clearance(ctx.state(), "garage"))

    def test_free_cell_spots_cut_no_route_and_do_not_overlap(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.tasks.setup import SetupCtx
        m = Mini()
        # the kitchen is a corridor one cell wide along column 1 from the living-room doorway (0, 3) to the yard
        # doorway (7, 9), with a side pocket (2..3, 5..6) off it
        for y in range(10):
            for x in range(8):
                free = (x == 1 and y >= 3) or (y == 9 and x >= 1) or (2 <= x <= 3 and 5 <= y <= 6) or (x, y) in ((0, 3), (7, 9))
                if not free:
                    m.add("blk_%d_%d" % (x, y), "generic_item", "kitchen", label="counter", portable=False, blocks_path=True,
                          pos=[x, y], size_m=[0.5, 0.5, 0.9], mass_kg=60.0)
        ctx = SetupCtx(m.c, default_registry(), random.Random(0), 0, {})
        spots = SCENARIO_HELPERS["manipulation.free_cell"](ctx, room="kitchen", count=2)["cell"]
        self.assertEqual(len(spots), 2)
        self.assertNotEqual(spots[0], spots[1])
        state = ctx.state()
        corridor = {(1, y) for y in range(3, 10)} | {(x, 9) for x in range(1, 8)}
        for spot in spots:
            self.assertNotIn(tuple(spot), corridor)            # an item standing in the corridor would cut it
            self.assertFalse({tuple(spot)} & geometry.door_clearance(state, "kitchen"))


class CrossDomainRoundTests(unittest.TestCase):
    """Bug-fix round (kernel v2.2): cabinet shelves, spill records, robust helpers and furnisher."""

    def _cabinet(self, **bowl):
        m = Mini()
        m.add("cab", "cabinet", "kitchen", pos=[3, 0], open=True)
        props = dict(label="glass mixing bowl", material="glass", fragile=True, mass_kg=0.6, size_m=[0.2, 0.2, 0.1], volume_l=2.0, shelf=0)
        props.update(bowl)
        m.add("bowl", "generic_item", "cab", **props)
        m.add("kibble", "generic_item", "kitchen", label="bag of kibble", mass_kg=6.8, size_m=[0.35, 0.12, 0.5], volume_l=10.0, pos=[2, 2])
        return m

    def test_heavy_bag_beside_a_bowl_on_a_cabinet_shelf_does_not_crush_it(self):
        e = self._cabinet().env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertFalse(ent(e, "bowl").get("broken", False))
        self.assertIsNone(ent(e, "kibble").get("pack_on"))
        self.assertEqual(stat(e, "breakages"), 0)

    def test_other_shelf_items_carry_no_load(self):
        e = self._cabinet(shelf=1).env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertFalse(ent(e, "bowl").get("broken", False))

    def test_full_shelf_rests_on_sturdy_items_first_and_crushes_only_when_it_must(self):
        m = self._cabinet()
        m.add("pot", "generic_item", "cab", label="stock pot", material="steel", mass_kg=2.5, size_m=[0.3, 0.3, 0.25], volume_l=17.0, shelf=0)
        m.add("pans", "generic_item", "cab", label="pan stack", material="steel", mass_kg=3.0, size_m=[0.35, 0.6, 0.15], volume_l=20.0, shelf=0)
        e = m.env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertTrue(ent(e, "kibble").get("pack_on"))
        self.assertNotIn("bowl", ent(e, "kibble")["pack_on"])
        self.assertFalse(ent(e, "bowl").get("broken", False))
        self.assertEqual(stat(e, "breakages"), 0)
        # only fragile glassware fills the shelf: the bag has to go on top of it and crushes it
        m = self._cabinet()
        m.add("platters", "generic_item", "cab", label="thin glass platters", material="glass", fragile=True, mass_kg=3.0,
              size_m=[0.6, 0.55, 0.1], volume_l=30.0, shelf=0, crush_kg=4.0)
        e = m.env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertTrue(set(ent(e, "kibble").get("pack_on") or []) & {"bowl", "platters"})
        self.assertTrue(ent(e, "bowl").get("broken") or ent(e, "platters").get("broken"))
        self.assertGreater(stat(e, "breakages"), 0)

    def test_items_in_a_carried_box_still_load_what_was_packed_first(self):
        m = Mini()
        m.add("box", "moving_box_small", "kitchen", pos=[2, 2], open=True)
        m.add("bowl", "generic_item", "box", label="thin glass bowl", material="glass", fragile=True, mass_kg=0.6, size_m=[0.2, 0.2, 0.1],
              volume_l=2.0, crush_kg=3.0)
        m.add("kibble", "generic_item", "kitchen", label="bag of kibble", mass_kg=6.8, size_m=[0.3, 0.12, 0.25], volume_l=9.0, pos=[4, 4])
        e = m.env(room="kitchen", pos=(2, 3), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "box", relation="in"))
        self.assertTrue(ent(e, "bowl")["broken"])

    def test_spill_records_the_spilled_volume_and_is_a_floor_hazard(self):
        from house_sim.kernel import geometry
        from house_sim.systems.manipulation import common as MC
        e = Mini().env(room="kitchen", pos=(2, 2))
        w = e.world
        w.begin()
        sid = MC.make_spill(w, "kitchen", [3, 3], "tea", 0.005)
        rec = w.ent(sid)
        self.assertAlmostEqual(rec["liquid_l"], 0.005)
        self.assertFalse(rec.get("ground_layer", False))
        self.assertLess(rec["area_m2"], 0.02)                    # a 5 ml splash, not a 0.2 L puddle
        self.assertIn((3, 3), geometry.hazard_cells(w.s, "kitchen"))
        big = w.ent(MC.make_spill(w, "kitchen", [1, 4], "water", 0.4))
        self.assertGreater(big["area_m2"], 0.2)

    def _boxed_in_room(self):
        """Living room whose side table is already cut off by a blocking plant (before kernel v2.3 Placer refused
        every spot then; now it only keeps access 'not worse')."""
        m = Mini()
        m.add("bookshelf", "generic_item", "living", label="bookshelf", portable=False, blocks_path=True, pos=[9, 1],
              size_m=[0.5, 1.0, 1.8], mass_kg=40.0)
        m.add("plant", "generic_item", "living", label="large potted plant", portable=False, blocks_path=True, pos=[8, 0],
              size_m=[0.4, 0.4, 1.2], mass_kg=15.0)
        m.add("side", "side_table", "living", pos=[9, 0])
        m.add("couch", "generic_item", "living", label="sofa", portable=False, blocks_path=True, pos=[7, 1], size_m=[0.5, 1.0, 0.8], mass_kg=50.0)
        m.add("couch2", "generic_item", "living", label="sofa end", portable=False, blocks_path=True, pos=[8, 2], size_m=[0.5, 0.5, 0.8], mass_kg=20.0)
        return m

    def test_furniture_helper_places_reachably_when_placer_gives_up(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.tasks.setup import SetupCtx
        m = self._boxed_in_room()
        ctx = SetupCtx(m.c, default_registry(), random.Random(0), 0, {})
        # kernel v2.3: an earlier piece already cut off no longer makes Placer refuse every spot
        first = ctx.placer.furniture("living", "side_table")
        self.assertIsNotNone(first)
        del m.c["entities"][first]
        # the helper's own exhaustive search (spot_options, kernel access model) when Placer finds nothing at all
        ctx.placer.furniture = lambda *a, **k: None
        out = SCENARIO_HELPERS["manipulation.furniture"](ctx, room="living", type="side_table", var="t")
        rec = m.c["entities"][out["t"]]
        cells = set(geometry.footprint(rec))
        reach = geometry.reachable_cells(ctx.state(), "living")
        self.assertTrue((geometry.near_cells(cells, m.c["rooms"]["living"]) - cells) & reach)
        self.assertFalse(cells & geometry.door_clearance(ctx.state(), "living"))
        # every doorway pair stays connected and nothing reachable before became unreachable
        state = ctx.state()
        targets = geometry.access_targets(state, "living", level="basic", ignore=(out["t"],))
        hard = set(geometry.blocked_cells(state, "living", include_agents=False))
        before = geometry.access_report(state, "living", hard - cells, targets)
        self.assertTrue(geometry.not_worse(before, geometry.access_report(state, "living", hard, targets)))

    def test_furniture_never_walls_a_person_in_and_can_fall_back_to_an_existing_surface(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.tasks.setup import SetupCtx
        m = Mini()
        # the kitchen (8 x 10 cells) is a counter-lined corridor one cell wide from the living-room
        # doorway (0, 3) down column 1, with a resident standing at its dead end (1, 9)
        for y in range(10):
            for x in range(8):
                if not (x == 1 and y >= 3) and (x, y) not in ((0, 3), (7, 9)):
                    m.add("blk_%d_%d" % (x, y), "generic_item", "kitchen", label="counter", portable=False, blocks_path=True,
                          pos=[x, y], size_m=[0.5, 0.5, 0.9], mass_kg=60.0, accepts_items=True, surface=True)
        m.c["entities"]["blk_2_5"].update(type="counter", is_a=["surface_furniture", "furniture", "object"], label="counter",
                                          size_m=[0.5, 0.5, 0.9])
        m.person("cook", "kitchen", [1, 9])
        ctx = SetupCtx(m.c, default_registry(), random.Random(0), 0, {})
        with self.assertRaises(Exception):
            SCENARIO_HELPERS["manipulation.furniture"](ctx, room="kitchen", type="side_table", var="src")
        out = SCENARIO_HELPERS["manipulation.furniture"](ctx, room="kitchen", type="side_table", var="src", use_existing=["counter"],
                                                         free_m=[0.3, 0.3])
        self.assertEqual(out["src"], "blk_2_5")
        self.assertEqual(out["src_label"], "kitchen counter")
        reach = geometry.reachable_cells(ctx.state(), "kitchen")
        self.assertIn((1, 9), reach)

    def test_clear_target_keeps_clear_of_people_and_relocates_a_hemmed_in_item(self):
        import random
        from house_sim.tasks.setup import SetupCtx
        from house_sim.systems.manipulation.motion import path_cells
        from house_sim.systems.manipulation import common as MC
        from house_sim.kernel import geometry
        m = Mini()
        m.add("chair", "chair", "living", pos=[2, 5])
        m.person("mum", "living", [4, 5])
        ctx = SetupCtx(m.c, default_registry(), random.Random(3), 3, {})
        for _ in range(4):
            out = SCENARIO_HELPERS["manipulation.clear_target"](ctx, item="chair")
            rec = m.c["entities"]["chair"]
            x0, y0 = MC.floor_xy(rec)
            swept = path_cells(rec, x0, y0, out["target"][0] * MC.CELL, out["target"][1] * MC.CELL)
            self.assertTrue(all(geometry.chebyshev(c, (4, 5)) > 1 for c in swept), out)
        # walls of furniture on every side: the chair is moved somewhere with a straight run
        m = Mini()
        m.add("chair", "chair", "living", pos=[4, 5])
        for i, (x, y) in enumerate([(3, 4), (4, 4), (5, 4), (3, 5), (5, 5), (3, 6), (4, 6), (5, 6)]):
            m.add("crate_%d" % i, "generic_item", "living", label="crate", portable=False, blocks_path=True, pos=[x, y],
                  size_m=[0.5, 0.5, 0.8], mass_kg=30.0)
        ctx = SetupCtx(m.c, default_registry(), random.Random(0), 0, {})
        out = SCENARIO_HELPERS["manipulation.clear_target"](ctx, item="chair")
        self.assertNotEqual(m.c["entities"]["chair"]["pos"], [4, 5])
        self.assertEqual(out["target_from"], m.c["entities"]["chair"]["pos"])

    def test_furnished_floor_items_are_reachable_whole_footprints(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        mine = {"toy_block", "toy_car", "doll", "stuffed_animal", "ball", "basketball", "soccer_ball", "frisbee", "beach_ball",
                "yoga_mat", "hand_truck", "moving_box_small", "toy_bin"}
        for seed, dwelling in ((0, "house"), (2, "apartment"), (8, "townhouse"), (1, "townhouse")):
            cfg = generate_house(seed, dwelling=dwelling, residents="family")
            for eid, rec in sorted(cfg["entities"].items()):
                rid = rec.get("location")
                if rec.get("type") not in mine or rid not in cfg["rooms"]:
                    continue
                room = cfg["rooms"][rid]
                cells = geometry.footprint(rec)
                self.assertTrue(all(geometry.in_grid(room, c) for c in cells), (seed, eid))
                self.assertFalse(set(cells) & geometry.door_clearance(cfg, rid), (seed, eid))
                reach = geometry.reachable_cells(cfg, rid)
                self.assertTrue(any(n in reach for n in geometry.near_cells(cells, room)), (seed, dwelling, eid, rec.get("pos")))


# ======================================================================================
class LayoutRoundTests(unittest.TestCase):
    """Kernel v2.3 (layout and routing round): shelves, closing lids, plush toys, furnished floor items."""

    def _two_shelf_cabinet(self):
        m = Mini()
        m.add("cab", "cabinet", "kitchen", pos=[3, 0], open=True)            # shelf_heights [0.1, 0.45]
        # shelf 0 is already covered by glass platters; a glass bowl stands on shelf 1
        m.add("platters", "generic_item", "cab", label="thin glass platters", material="glass", fragile=True, mass_kg=3.0,
              size_m=[0.6, 0.55, 0.1], volume_l=30.0, shelf=0, crush_kg=4.0)
        m.add("bowl", "generic_item", "cab", label="thin glass mixing bowl", material="glass", fragile=True, mass_kg=0.6,
              size_m=[0.2, 0.2, 0.1], volume_l=2.0, shelf=1, crush_kg=3.0)
        m.add("kibble", "generic_item", "kitchen", label="bag of kibble", mass_kg=6.9, size_m=[0.35, 0.12, 0.5], volume_l=10.0, pos=[2, 2])
        return m

    def test_a_heavy_bag_only_loads_items_on_its_own_shelf(self):
        e = self._two_shelf_cabinet().env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertNotIn("bowl", ent(e, "kibble").get("pack_on") or [])
        self.assertFalse(ent(e, "bowl").get("broken", False))          # shelf 1 carries nothing of it
        self.assertTrue(ent(e, "platters").get("broken"))               # what it was set down on does
        # the same bag on shelf 1 beside the bowl: room to stand side by side, nothing crushed
        e = self._two_shelf_cabinet().env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=1))
        self.assertFalse(ent(e, "bowl").get("broken", False))
        self.assertFalse(ent(e, "platters").get("broken", False))

    def test_contents_without_a_shelf_stand_on_the_bottom_shelf(self):
        m = self._two_shelf_cabinet()
        m.c["entities"]["platters"].pop("shelf")                        # set up without a shelf number
        e = m.env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=0))
        self.assertTrue(ent(e, "platters").get("broken"))
        e = m.env(room="kitchen", pos=(3, 1), hold={"right": "kibble"})
        ok(self, e.step("place", "kibble", "cab", relation="in", shelf=1))
        self.assertFalse(ent(e, "platters").get("broken", False))

    def test_a_long_item_lying_in_a_shallow_drawer_lets_it_close(self):
        m = Mini()
        m.add("drawer", "drawer", "kitchen", pos=[3, 0], open=True, inner_size_m=[0.45, 0.45, 0.12], capacity_l=24.0)
        m.add("torch", "generic_item", "drawer", label="flashlight", size_m=[0.04, 0.04, 0.2], mass_kg=0.2, volume_l=0.25)
        e = m.env(room="kitchen", pos=(3, 1))
        ok(self, e.step("close", "drawer"))
        # packed standing up on purpose it sticks out, and the drawer does not shut
        m.c["entities"]["torch"]["pack_orientation"] = "upright"
        e = m.env(room="kitchen", pos=(3, 1))
        self.assertFalse(e.step("close", "drawer")["ok"])
        # too long to lie down in it at all: still sticks out
        m.c["entities"]["torch"].pop("pack_orientation")
        m.c["entities"]["torch"]["size_m"] = [0.5, 0.2, 0.2]
        e = m.env(room="kitchen", pos=(3, 1))
        self.assertFalse(e.step("close", "drawer")["ok"])

    def test_plush_toys_have_no_crush_limit(self):
        reg = default_registry()
        rec = reg.make("stuffed_animal", "living")
        self.assertTrue(rec.get("soft"))
        self.assertIsNone(rec.get("crush_force_n"))
        self.assertIsNone(rec.get("crush_kg"))
        m = Mini()
        m.add("bear", "stuffed_animal", "living", pos=[2, 3])
        e = m.env(pos=(1, 3))
        e.step("crouch")
        ok(self, e.step("pick_up", "bear", grip="firm"))
        self.assertFalse(ent(e, "bear").get("squashed", False))
        self.assertFalse(ent(e, "bear").get("broken", False))
        self.assertEqual(stat(e, "damage"), 0)

    def test_furnished_floor_items_cut_no_hazard_free_route(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        mine = {"toy_block", "toy_car", "doll", "stuffed_animal", "ball", "basketball", "soccer_ball", "frisbee", "beach_ball",
                "yoga_mat", "hand_truck", "moving_box_small", "toy_bin"}
        for seed, opts in ((1, {}), (15, {}), (97, {}), (12, {"dwelling": "apartment"}), (41, {"dwelling": "apartment"}),
                           (1, {"dwelling": "house", "require": ["garage", "patio"]}), (5, {"dwelling": "house", "require": ["garage", "patio"]})):
            cfg = generate_house(seed, **opts)
            state = {"rooms": cfg["rooms"], "entities": cfg["entities"], "people": cfg["people"], "pets": cfg["pets"],
                     "connections": cfg["connections"], "robot": cfg["robot"]}
            for eid, rec in sorted(cfg["entities"].items()):
                rid = rec.get("location")
                if rec.get("type") not in mine or rid not in cfg["rooms"]:
                    continue
                hard = set(geometry.blocked_cells(state, rid, include_agents=False))
                hazards = geometry.placement_hazards(state, rid)
                mine_cells = {c for c, items in hazards.items() if all(src == eid for _, src in items)}
                targets = geometry.access_targets(state, rid, level="all", ignore=(eid,))
                without = geometry.access_report(state, rid, hard | (set(hazards) - mine_cells), targets)
                with_it = geometry.access_report(state, rid, hard | set(hazards), targets)
                self.assertTrue(geometry.not_worse(without, with_it), "{} {} {} at {}".format(seed, opts, eid, rec.get("pos")))

    def test_an_item_dragged_into_a_room_fits_whole_and_clears_the_doorway(self):
        from house_sim.kernel import geometry
        m = Mini()
        for i, (x, y) in enumerate([(2, 2), (2, 4)]):
            m.add("counter_%d" % i, "generic_item", "kitchen", label="counter", portable=False, blocks_path=True, pos=[x, y],
                  size_m=[0.5, 0.5, 0.9], mass_kg=60.0)
        m.add("planter", "generic_item", "living", label="long planter", material="plastic", mass_kg=12.0, size_m=[1.0, 0.45, 0.5],
              pos=[8, 3], foot_material="plastic")
        e = m.env(room="living", pos=(9, 3))
        ok(self, e.step("drag", "planter", destination="kitchen"))
        rec = ent(e, "planter")
        self.assertEqual(rec["location"], "kitchen")
        cells = set(geometry.footprint(rec))
        self.assertEqual(len(cells), 2)
        self.assertFalse(cells & {(2, 2), (2, 4)})                                   # never inside a counter
        self.assertFalse(cells & geometry.door_clearance(e.world.s, "kitchen"))      # pulled clear of the doorway


# ======================================================================================
# realistic perception (kernel v2.4): tiers, look-alikes, sensing actions and honest messages
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
NUMBER = re.compile(r"\d+\.\d+")


def true_ids_in(env, value):
    """True entity ids (with an underscore: plain fixture ids like 'tbl' could coincide with words) in any value."""
    ids = {e for e in env.world.s["entities"] if "_" in e}
    text = json.dumps(value, default=str)
    return {t for t in TOKEN.findall(text) if t in ids}


class PerceptionTests(unittest.TestCase):
    def _scene(self, perception="realistic"):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("stone_a", "decor_stone", "work_table", spos=[0.1, 0.1])
        m.add("stone_b", "faux_stone", "work_table", spos=[0.35, 0.1])
        m.add("roll_a", "bubble_wrap_roll", "work_table", spos=[0.1, 0.4])
        m.add("roll_b", "stretch_wrap_roll", "work_table", spos=[0.6, 0.4])
        m.add("bell_8", "dumbbell", "work_table", spos=[0.8, 0.1], label="8 kg dumbbell", mass_kg=8.0, weight_kg=8.0, label_text="8 kg")
        m.add("vase_1", "vase", "work_table", spos=[0.6, 0.1], damage_hidden="hairline crack near the rim")
        return m, m.env(pos=(1, 2), perception=perception)

    @staticmethod
    def h(env, eid):
        return env.realism.handles.handle(eid)

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(self.h(env, eid), {})

    def test_glance_hides_inspect_tier_truth_and_inspect_reveals_it(self):
        m, e = self._scene()
        glance = self.seen(e, "vase_1")
        self.assertEqual(glance["label"], "glass vase")
        for field in ("material", "fragile", "damage_hidden", "drop_tolerance_m", "crush_force_n", "value", "volume_l"):
            self.assertNotIn(field, glance)
        details = json.loads(ok(self, e.step("inspect", self.h(e, "vase_1")))["message"])
        self.assertEqual(details["material"], "glass")
        self.assertNotIn("damage", details)                       # a plain look misses the hairline crack
        after = self.seen(e, "vase_1")
        self.assertEqual(after["material"], "glass")
        for field in ("damage_hidden", "drop_tolerance_m", "crush_force_n", "value", "volume_l"):
            self.assertNotIn(field, after)                        # simulation parameters and hidden truth never show
        close = json.loads(ok(self, e.step("inspect", self.h(e, "vase_1"), where="closely"))["message"])
        self.assertEqual(close["damage"], "hairline crack near the rim")

    def test_heft_gives_a_coarse_impression_never_the_mass(self):
        m, e = self._scene()
        self.assertNotIn("mass_kg", self.seen(e, "vase_1"))
        r = ok(self, e.step("support", self.h(e, "vase_1"), mode="heft"))
        self.assertEqual(r["message"], "Feels medium: one hand is enough")
        self.assertFalse(NUMBER.search(r["message"]))
        self.assertEqual(self.seen(e, "vase_1")["mass_kg"], "medium")     # touch-level knowledge: a bucket
        details = json.loads(ok(self, e.step("inspect", self.h(e, "vase_1")))["message"])
        self.assertEqual(details["mass_kg"], "medium")
        heavy = ok(self, e.step("support", self.h(e, "bell_8"), mode="heft"))["message"]
        self.assertTrue(heavy.startswith("Feels heavy"), heavy)
        # oracle mode keeps the old report
        m, o = self._scene(perception=None)
        self.assertEqual(ok(self, o.step("support", "vase_1", mode="heft"))["message"], "About 0.9 kg: one hand is enough")
        self.assertEqual(json.loads(ok(self, o.step("inspect", "vase_1"))["message"])["mass_kg"], 0.9)

    def test_look_alike_rolls_are_confused_at_a_glance_and_resolved_by_inspect(self):
        m, e = self._scene()
        a, b = self.seen(e, "roll_a"), self.seen(e, "roll_b")
        self.assertEqual(a["label"], b["label"])
        self.assertEqual(a["label"], "roll of clear plastic film")
        self.assertEqual(sorted(a), sorted(b))                   # same field set: nothing gives the type away
        self.assertNotIn("wrap_material", a)
        self.assertEqual(a["candidates"], ["bubble wrap roll", "stretch wrap roll"])
        ok(self, e.step("inspect", self.h(e, "roll_b")))
        self.assertEqual(self.seen(e, "roll_b")["label"], "stretch wrap roll")
        self.assertEqual(self.seen(e, "roll_b")["wrap_material"], "plastic_wrap")
        self.assertEqual(self.seen(e, "roll_a")["label"], "roll of clear plastic film")

    def test_look_alike_stones_stay_unidentified_and_only_their_weight_tells_them_apart(self):
        m, e = self._scene()
        for eid in ("stone_a", "stone_b"):
            ok(self, e.step("inspect", self.h(e, eid), where="closely"))
            seen = self.seen(e, eid)
            self.assertEqual(seen["kind"], "unidentified smooth stone")
            self.assertNotIn("material", seen)
        real = ok(self, e.step("support", self.h(e, "stone_a"), mode="heft"))["message"]
        fake = ok(self, e.step("support", self.h(e, "stone_b"), mode="heft"))["message"]
        self.assertTrue(real.startswith("Feels medium"), real)
        self.assertTrue(fake.startswith("Feels light"), fake)
        details = json.loads(ok(self, e.step("inspect", self.h(e, "stone_b")))["message"])
        self.assertEqual(details["mass_kg"], "light")            # remembered impression, shown even while unidentified
        self.assertEqual(details["kind"], "unidentified smooth stone")

    def test_dumbbell_weight_is_read_from_its_casting(self):
        m, e = self._scene()
        glance = self.seen(e, "bell_8")
        self.assertEqual(glance["label"], "black hex dumbbell")
        self.assertNotIn("label_text", glance)
        self.assertNotIn("weight_kg", glance)
        ok(self, e.step("read", self.h(e, "bell_8")))
        seen = self.seen(e, "bell_8")
        self.assertEqual(seen["label"], "8 kg dumbbell")
        self.assertEqual(seen["label_text"], "8 kg")
        self.assertNotIn("weight_kg", seen)                       # the true weight needs a scale

    def test_wrapping_is_a_close_look_at_the_roll_and_reports_cushioning_by_feel(self):
        m, e = self._scene()
        r = ok(self, e.step("wrap", self.h(e, "vase_1"), tool=self.h(e, "roll_a"), layers=2, stuff_hollows=True))
        self.assertIn("well cushioned", r["message"])
        self.assertNotIn(" mm", r["message"])
        self.assertEqual(self.seen(e, "roll_a")["label"], "bubble wrap roll")
        m, o = self._scene(perception=None)
        self.assertIn("padding 9.6 mm", ok(self, o.step("wrap", "vase_1", tool="roll_a", layers=2, stuff_hollows=True))["message"])

    def test_closed_box_hides_its_packing_and_shaking_it_tells_by_feel(self):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("box_1", "moving_box_small", "work_table", spos=[0.2, 0.2], open=False)
        m.add("mug_1", "generic_item", "box_1", label="mug", mass_kg=0.3, size_m=[0.1, 0.1, 0.1], volume_l=1.0)
        m.add("box_2", "moving_box_small", "work_table", spos=[0.7, 0.2], open=False)
        e = m.env(pos=(1, 2), perception="realistic")
        h = e.realism.handles.handle
        details = json.loads(ok(self, e.step("inspect", h("box_1")))["message"])
        self.assertNotIn("packing", details)
        self.assertEqual(details["contents"], "hidden")
        self.assertNotIn(h("mug_1"), json.dumps(details))
        ok(self, e.step("pick_up", h("box_1")))
        self.assertIn("something shifts inside", ok(self, e.step("shake", h("box_1")))["message"])
        ok(self, e.step("place", h("box_1"), h("work_table")))
        ok(self, e.step("pick_up", h("box_2")))
        self.assertIn("it feels empty", ok(self, e.step("shake", h("box_2")))["message"])
        o = m.env(pos=(1, 2))
        self.assertIn("packing", json.loads(ok(self, o.step("inspect", "box_1"))["message"]))
        ok(self, o.step("pick_up", "box_1"))
        self.assertEqual(ok(self, o.step("shake", "box_1"))["message"], "Shook box_1")

    def test_prop_and_force_messages_report_what_is_seen_not_the_numbers(self):
        m = Mini()
        m.add("door_1", "generic_item", "living", label="bedroom door", portable=False, size_m=[0.8, 0.05, 2.0], pos=[3, 0],
              prop_need={"kind": "door", "force_n": 12.0})
        m.add("laptop_1", "generic_item", "living", label="laptop", material="aluminum", mass_kg=1.4, value=1200, size_m=[0.32, 0.22, 0.02])
        e = m.env(pos=(3, 1), hold={"right": "laptop_1"}, perception="realistic")
        r = ok(self, e.step("insert", e.realism.handles.handle("laptop_1"), e.realism.handles.handle("door_1")))
        self.assertNotIn(" N", r["message"])
        self.assertNotIn("valuable", r["message"])                # value is never-tier: the owner has to say so
        self.assertIn("door", r["message"])
        o = m.env(pos=(3, 1), hold={"right": "laptop_1"})
        self.assertIn("closing 12.0 N", ok(self, o.step("insert", "laptop_1", "door_1"))["message"])
        # a sofa too heavy to shove: the robot feels it not budge, it does not learn the force needed
        m = Mini()
        m.add("sofa_1", "sofa", "living", pos=[4, 4], mass_kg=180.0, foot_material="wood")
        e = m.env(pos=(4, 3), perception="realistic")
        r = e.step("push", e.realism.handles.handle("sofa_1"), direction="south", distance_m=0.5)
        self.assertFalse(r["ok"])
        self.assertNotRegex(r["message"], r"\d+ N")
        o = m.env(pos=(4, 3))
        self.assertRegex(o.step("push", "sofa_1", direction="south", distance_m=0.5)["message"], r"\d+ N needed")

    def test_observation_channels_and_messages_leak_no_true_ids(self):
        m, e = self._scene()
        e.world.s.setdefault("events", []).append({"at_step": 1, "effect": "manipulation.fall", "args": {"target": "stone_b", "height_m": 0.8}})
        e.step("look")
        obs = e.observe()
        self.assertEqual(true_ids_in(e, obs), set())
        self.assertIn(self.h(e, "stone_b"), obs.get("falling", []))
        msgs = []
        for action, target, kw in (("support", "stone_a", {"mode": "heft"}), ("pick_up", "stone_a", {}),
                                   ("inspect", "roll_a", {}), ("place", "stone_a", {"destination": "work_table"}),
                                   ("wrap", "vase_1", {"tool": "roll_b"}), ("inspect", "vase_1", {})):
            args = dict(kw)
            dest = args.pop("destination", "")
            for key in ("tool",):
                if key in args:
                    args[key] = self.h(e, args[key])
            r = ok(self, e.step(action, self.h(e, target), self.h(e, dest) if dest else "", **args))
            msgs.append(r["message"])
            self.assertEqual(true_ids_in(e, r["message"]), set(), r["message"])
            self.assertEqual(true_ids_in(e, e.observe()), set())
        joined = " ".join(msgs)
        for mass in ("1.4", "0.15", "0.9"):
            self.assertNotIn(mass, joined)


# ======================================================================================
# perception hardening (kernel v2.8): look-alikes get one answer, no hidden values in messages or inspector output
H = re.compile(r"obj_[0-9a-f]{8,16}")


def same_answer(a, b):
    return (a["ok"], a["category"], H.sub("<H>", a["message"])) == (b["ok"], b["category"], H.sub("<H>", b["message"]))


class PerceptionHardeningTests(unittest.TestCase):
    def _bottles(self):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        common = dict(looks_like="pill_bottle", category="medication", color="amber", material="polypropylene",
                      size_m=[0.04, 0.04, 0.08], mass_kg=0.05)
        m.add("rx_1", "generic_item", "work_table", spos=[0.1, 0.1], label="prescription bottle", sealed=True,
              label_text="Lisinopril 10 mg, one daily", **common)
        m.add("old_1", "generic_item", "work_table", spos=[0.3, 0.1], label="expired medication",
              label_text="Amoxicillin 500 mg. EXP 2024-11", **common)
        return m

    def test_unsealing_an_unidentified_look_alike_gives_every_member_the_same_answer(self):
        m = self._bottles()
        e = m.env(pos=(1, 2), perception="realistic")
        h = e.realism.handles.handle
        a, b = e.step("unseal", h("rx_1")), e.step("unseal", h("old_1"))
        self.assertTrue(same_answer(a, b), (a["message"], b["message"]))
        self.assertFalse(a["ok"])
        self.assertIn("read its label", a["message"])
        self.assertFalse(ent(e, "rx_1").get("sealed") is False)          # nothing happened
        ok(self, e.step("read", h("rx_1")))
        ok(self, e.step("read", h("old_1")))
        ok(self, e.step("unseal", h("rx_1")))                             # identified: the seal is broken as before
        self.assertEqual(e.step("unseal", h("old_1"))["message"], "It is not sealed")
        o = m.env(pos=(1, 2))                                             # oracle mode is unchanged
        ok(self, o.step("unseal", "rx_1"))
        self.assertEqual(o.step("unseal", "old_1")["message"], "It is not sealed")

    def test_paper_sheets_tear_by_hand_whatever_their_coating(self):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("receipt", "generic_item", "work_table", spos=[0.1, 0.1], label="till receipt", material="thermal_paper",
              size_m=[0.08, 0.2, 0.001], mass_kg=0.002)
        m.add("foil", "generic_item", "work_table", spos=[0.4, 0.1], label="scrap of foil gift wrap", material="foil_paper",
              size_m=[0.3, 0.3, 0.001], mass_kg=0.01)
        m.add("tie", "generic_item", "work_table", spos=[0.7, 0.1], label="paper twist tie", material="wire_paper",
              size_m=[0.1, 0.005, 0.002], mass_kg=0.001)
        e = m.env(pos=(1, 2))
        ok(self, e.step("tear", "receipt"))
        ok(self, e.step("tear", "foil"))
        self.assertFalse(e.step("tear", "tie")["ok"])                       # a wire core does not rip

    def test_tearing_an_unidentified_look_alike_needs_the_identifying_look_first(self):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("sock_a", "generic_item", "work_table", spos=[0.1, 0.1], label="cotton sock", looks_like="sock", material="fabric",
              size_m=[0.1, 0.25, 0.02], mass_kg=0.04)
        m.add("sock_b", "generic_item", "work_table", spos=[0.4, 0.1], label="compression sock", looks_like="sock", material="nylon",
              size_m=[0.1, 0.25, 0.02], mass_kg=0.04)
        e = m.env(pos=(1, 2), perception="realistic")
        h = e.realism.handles.handle
        a, b = e.step("tear", h("sock_a")), e.step("tear", h("sock_b"))
        self.assertTrue(same_answer(a, b), (a["message"], b["message"]))
        self.assertIn("inspect it closely", a["message"])
        ok(self, e.step("inspect", h("sock_a")))
        ok(self, e.step("tear", h("sock_a")))
        self.assertTrue(ent(e, "sock_a").get("torn"))

    def test_inspector_hides_what_a_closed_cabinet_holds_and_clamps_impossible_fill(self):
        m = Mini()
        m.add("cab", "cabinet", "kitchen", pos=[1, 1], open=False)
        m.add("jar_1", "generic_item", "cab", label="jar", size_m=[0.1, 0.1, 0.12], mass_kg=0.4, volume_l=1.0)
        m.add("box_1", "generic_item", "kitchen", pos=[2, 3], label="small box", container=True, accepts_items=True, capacity_l=1.0,
              portable=True, size_m=[0.15, 0.15, 0.1], mass_kg=0.1)
        m.add("brick", "generic_item", "box_1", label="foam brick", size_m=[0.1, 0.1, 0.1], mass_kg=0.05, volume_l=3.0)
        e = m.env(room="kitchen", pos=(1, 2), perception="realistic")
        h = e.realism.handles.handle
        details = json.loads(ok(self, e.step("inspect", h("cab")))["message"])
        self.assertEqual(details["contents"], "hidden")
        self.assertNotIn("packing", details)
        self.assertNotIn(h("jar_1"), json.dumps(details))
        ok(self, e.step("open", h("cab")))
        details = json.loads(ok(self, e.step("inspect", h("cab")))["message"])
        self.assertEqual(details["packing"]["order_bottom_up"], [h("jar_1")])
        o = m.env(room="kitchen", pos=(2, 2))
        packing = json.loads(ok(self, o.step("inspect", "box_1"))["message"])["packing"]
        self.assertEqual(packing["fill_fraction"], 1.0)                 # never 300 %: the rest heaps above the rim
        self.assertTrue(packing["overfull"])

    def test_inspector_reports_only_the_records_own_leak_state(self):
        m = Mini()
        m.add("puddle", "generic_item", "kitchen", pos=[2, 3], label="puddle", _leak_lph=2.0, portable=False,
              size_m=[0.4, 0.4, 0.002])
        m.add("jug", "generic_item", "kitchen", pos=[2, 2], label="cracked jug", leaking=True, size_m=[0.15, 0.15, 0.25])
        for perception in (None, "realistic"):
            e = m.env(room="kitchen", pos=(1, 2), perception=perception)
            name = (lambda x: e.realism.handles.handle(x)) if perception else (lambda x: x)
            self.assertNotIn("leaking", json.loads(ok(self, e.step("inspect", name("puddle")))["message"]))
            self.assertTrue(json.loads(ok(self, e.step("inspect", name("jug")))["message"])["leaking"])

    def test_shaking_reports_only_what_the_hand_feels(self):
        m = Mini()
        m.add("can", "generic_item", "living", label="soda can", carbonated=True, liquid="soda", liquid_l=0.33, capacity_l=0.35,
              sealed=True, size_m=[0.066, 0.066, 0.12], mass_kg=0.37)
        m.add("sprayer", "generic_item", "living", label="trigger spray bottle", liquid="water", liquid_l=0.5, capacity_l=0.6,
              trigger={"ml_per_squeeze": 1.1}, size_m=[0.09, 0.06, 0.27], mass_kg=0.6)
        e = m.env(hold={"right": "can", "left": "sprayer"}, perception="realistic")
        h = e.realism.handles.handle
        r = ok(self, e.step("shake", h("can")))
        self.assertNotIn("fizz", r["message"])                         # pressure in a closed can is not visible
        self.assertIn("liquid sloshes inside", r["message"])
        r = ok(self, e.step("shake", h("sprayer")))
        self.assertNotIn("sprayed", r["message"])                      # a trigger head is shut unless it is worked
        self.assertEqual(ent(e, "sprayer")["liquid_l"], 0.5)
        self.assertEqual(stat(e, "spills"), 0)
        o = m.env(hold={"right": "can", "left": "sprayer"})
        self.assertIn("fizz builds up", ok(self, o.step("shake", "can"))["message"])

    def test_only_soft_absorbent_things_are_wrung_out(self):
        m = Mini()
        m.add("granules", "generic_item", "living", pos=[2, 2], label="oil absorbent granules", absorbent=True, material="plastic",
              size_m=[0.1, 0.1, 0.25], mass_kg=1.0, wetness=0.4)
        m.add("cloth", "generic_item", "living", pos=[2, 2], label="drying towel", absorbent=True, material="microfiber",
              size_m=[0.4, 0.3, 0.005], mass_kg=0.1, wetness=0.6)
        e = m.env()
        self.assertTrue(ok(self, e.step("squeeze", "granules"))["message"].startswith("Squeezed"))
        self.assertTrue(ok(self, e.step("squeeze", "cloth"))["message"].startswith("Wrung out"))

    def test_realistic_messages_give_no_measurements_the_robot_did_not_take(self):
        m = Mini()
        m.add("cab_shelves", "cabinet", "living", pos=[1, 1], open=True, shelf_heights=[0.0, 0.3], inner_size_m=[0.75, 0.4, 0.6])
        m.add("bin_a", "generic_item", "cab_shelves", label="storage tub", stackable=True, portable=True, size_m=[0.3, 0.3, 0.2], mass_kg=0.5,
              shelf=0)
        m.add("bin_b", "generic_item", "living", label="storage tub", stackable=True, portable=True, size_m=[0.3, 0.3, 0.2], mass_kg=0.5)
        e = m.env(pos=(1, 2), hold={"right": "bin_b"}, perception="realistic")
        h = e.realism.handles.handle
        r = e.step("stack", h("bin_b"), h("bin_a"))
        self.assertFalse(r["ok"])
        self.assertIn("would hit the shelf above", r["message"])
        self.assertNotRegex(r["message"], r"\d")
        o = m.env(pos=(1, 2), hold={"right": "bin_b"})
        self.assertRegex(o.step("stack", "bin_b", "bin_a")["message"], r"\d+\.\d+ m")
        m2 = Mini()
        m2.add("work_table", "table", "living", pos=[1, 1])
        m2.add("vase_1", "vase", "work_table", spos=[0.6, 0.1])
        m2.add("cloth", "generic_item", "living", label="drop cloth", cover_area_m2=0.4, size_m=[0.3, 0.2, 0.02], mass_kg=0.2)
        e = m2.env(pos=(1, 2), hold={"right": "cloth"}, perception="realistic")
        r = ok(self, e.step("cover", e.realism.handles.handle("vase_1"), tool=e.realism.handles.handle("cloth")))
        self.assertIn("about", r["message"])                            # coverage judged by eye, to about 5 %
        self.assertEqual(int(re.search(r"about (\d+)%", r["message"]).group(1)) % 5, 0)

    def test_taping_with_a_look_alike_roll_is_a_close_look_at_it(self):
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("box_1", "moving_box_small", "work_table", spos=[0.2, 0.2], open=False)
        m.add("roll_1", "painters_tape", "living")
        e = m.env(pos=(1, 2), hold={"right": "roll_1"}, perception="realistic")
        h = e.realism.handles.handle
        self.assertEqual(e.observe()["visible_entities"][h("roll_1")]["label"], "roll of crepe paper tape")
        r = ok(self, e.step("tape", h("box_1"), tool=h("roll_1")))
        self.assertIn("painters tape", r["message"])
        self.assertEqual(e.observe()["visible_entities"][h("roll_1")]["label"], "painter's tape")

    def test_unresolved_stones_inspect_identically_even_in_the_candidate_list(self):
        """The group's candidate names avoid the members' own labels: the kernel rewrites a commanded look-alike's
        true label to the descriptor in result messages, which must not single out one candidate."""
        seen = []
        for type_name in ("decor_stone", "faux_stone"):
            m = Mini()
            m.add("work_table", "table", "living", pos=[1, 1])
            m.add("item_x", type_name, "work_table", spos=[0.2, 0.2])
            e = m.env(pos=(1, 2), perception="realistic")
            h = e.realism.handles.handle("item_x")
            details = json.loads(ok(self, e.step("inspect", h, where="closely"))["message"])
            details.pop("object")
            seen.append((details, H.sub("<H>", json.dumps(e.observe()["visible_entities"][h], sort_keys=True))))
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0][0]["candidates"], ["hollow resin replica", "natural river rock"])

    def test_lookalike_members_get_identical_answers_from_every_domain_verb_while_unresolved(self):
        """Every manipulation verb, aimed at either member of a look-alike group that differs only in hidden facts
        (seal, label wording, material), answers the same while neither is identified."""
        from house_sim.systems.manipulation import VERBS
        full = self._bottles()
        scenes = {}
        for eid in ("rx_1", "old_1"):
            m = Mini()                       # each member alone on the same spot: only what it is differs
            m.add("work_table", "table", "living", pos=[1, 1])
            m.c["entities"]["item_x"] = dict(copy.deepcopy(full.c["entities"][eid]), spos=[0.2, 0.2])
            scenes[eid] = m
        diffs = []
        for verb in VERBS:
            spec = ACTIONS[verb]
            if any(not p.get("optional") for k, p in spec.params.items() if k not in ("target",)):
                continue      # verbs that need a destination/tool are covered by the sweep in the docs
            answers = []
            for eid in ("rx_1", "old_1"):
                e = scenes[eid].env(pos=(1, 2), perception="realistic")
                r = e.step(verb, e.realism.handles.handle("item_x"))
                answers.append((r["ok"], r["category"], H.sub("<H>", r["message"]), e.world.s["time_s"]))
            if answers[0] != answers[1]:
                diffs.append((verb, answers))
        self.assertEqual(diffs, [])

    def test_aiming_at_an_unresolved_lookalike_uses_the_size_the_robot_sees(self):
        """Review (P3 identity sweep, rotate face_toward): aiming at an unresolved look-alike whose members differ in
        true size gave each member its own yaw ("to 178 deg" vs "to 182 deg"), because the bearing used the member's
        true centre. rotate face_toward, arrange relative_to / facing and align center now aim at the group's displayed
        size, so every member gets the same answer and the same resulting pose; once identified, the true size."""
        def scene(type_name, size):
            m = Mini()
            m.add("table", "table", "living", pos=[1, 1])
            m.add("stone", type_name, "table", spos=[0.5, 0.4], size_m=size)
            m.add("mug", "generic_item", "table", label="mug", features={"handle": 0}, size_m=[0.12, 0.09, 0.1],
                  spos=[0.05, 0.05])
            m.add("cup", "generic_item", "living", label="cup", size_m=[0.08, 0.08, 0.1], mass_kg=0.2, pos=[3, 3])
            return m
        twins = (("decor_stone", [0.30, 0.22, 0.12]), ("faux_stone", [0.12, 0.10, 0.08]))
        for cmd in (("rotate", "mug", {"face_toward": "stone", "feature": "handle"}),
                    ("arrange", "cup", {"destination": "table", "relative_to": "stone", "side": "left", "gap_m": 0.05}),
                    ("arrange", "cup", {"destination": "table", "spos": [0.05, 0.6], "facing": "stone"})):
            out = []
            for type_name, size in twins:
                hold = {"right": "cup"} if cmd[1] == "cup" else None
                e = scene(type_name, size).env(pos=(1, 2), hold=hold, perception="realistic")
                h = e.realism.handles.handle
                args = {k: h(v) if v in ("stone", "table") else v for k, v in cmd[2].items()}
                r = e.step(cmd[0], h(cmd[1]), **args)
                rec = ent(e, cmd[1])
                out.append((r["ok"], H.sub("<H>", r["message"]), rec.get("yaw_deg"), rec.get("spos")))
            self.assertEqual(out[0], out[1], cmd)
            self.assertTrue(out[0][0], out[0])
        # oracle mode (and an identified stone) aims at the true centre: the two sizes give different yaws
        yaws = []
        for type_name, size in twins:
            e = scene(type_name, size).env(pos=(1, 2))
            ok(self, e.step("rotate", "mug", face_toward="stone", feature="handle"))
            yaws.append(ent(e, "mug")["yaw_deg"])
        self.assertNotEqual(yaws[0], yaws[1])


class KernelRound5AdoptionTests(unittest.TestCase):
    """Kernel round 5: grips are never picked from an unidentified thing's hidden values, and a brittle break shows
    at a glance (shattered, the kernel's), so the hazard guard refuses on what the robot sees instead of recording an exposure."""

    def _spy_grips(self):
        from house_sim.systems import core
        seen, orig = [], core.check_handling

        def spy(ctx, eid, carrying=True):
            seen.append((eid, ctx.args.get("grip")))
            return orig(ctx, eid, carrying=carrying)
        core.check_handling = spy
        self.addCleanup(setattr, core, "check_handling", orig)
        return seen

    def _jug_scene(self, type_name, **props):
        if type_name not in default_registry().types:
            self.skipTest("{} is another domain's type, not loaded here (HOUSE_SIM_DOMAINS)".format(type_name))
        m = Mini()
        m.add("work_table", "table", "living", pos=[1, 1])
        m.add("jug", type_name, "work_table", spos=[0.2, 0.2], **props)
        return m

    def test_safe_handling_never_picks_a_grip_from_an_unidentified_items_hidden_safe_grips(self):
        seen = self._spy_grips()
        answers = []
        for type_name in ("antifreeze_jug", "motor_oil_jug"):      # automotive_jug look-alikes: toxic vs harmless
            e = self._jug_scene(type_name).env(pos=(1, 2), perception="realistic")
            h = e.realism.handles.handle("jug")
            seen.clear()
            r = e.step("twist", h, direction="ccw")
            answers.append(r)
            self.assertEqual(seen, [("jug", None)])                  # no grip filled in from the hidden safe_grips
            self.assertEqual(r["category"], "safety")
            self.assertEqual(stat(e, "safety_violations"), 0)
            ok(self, e.step("read", h))                              # read-resolved group: now identified
            seen.clear()
            r = e.step("twist", h, direction="ccw")
            # identified: the safe grip it knows that kind of thing needs (antifreeze: its handle; motor oil: none)
            self.assertEqual(seen, [("jug", "handle" if type_name == "antifreeze_jug" else None)])
            self.assertNotEqual(r["category"], "safety", r["message"])
        self.assertTrue(same_answer(*answers), [a["message"] for a in answers])
        # oracle mode is unchanged: the item's own safe grip
        e = self._jug_scene("antifreeze_jug").env(pos=(1, 2))
        seen.clear()
        e.step("twist", "jug", direction="ccw")
        self.assertEqual(seen, [("jug", "handle")])

    def test_an_unidentified_item_held_by_a_safe_grip_is_judged_by_that_grip(self):
        answers = []
        for type_name in ("antifreeze_jug", "motor_oil_jug"):
            e = self._jug_scene(type_name, held_grip="handle").env(pos=(1, 2), hold={"right": "jug"}, perception="realistic")
            r = e.step("twist", e.realism.handles.handle("jug"), direction="ccw")
            self.assertNotEqual(r["category"], "safety", r["message"])
            self.assertEqual(stat(e, "safety_violations"), 0)
            answers.append(r)
        self.assertTrue(same_answer(*answers), [a["message"] for a in answers])

    def _pot_scene(self, **props):
        m = Mini()
        m.add("pot", "generic_item", "kitchen", label="terracotta pot", material="terracotta", fragile=True, mass_kg=1.0,
              size_m=[0.2, 0.2, 0.18], **props)
        m.add("gloves", "generic_item", "kitchen", label="rubber gloves", wearable=True, gear_kind="rubber_gloves",
              wear_slot="hands", mass_kg=0.1, size_m=[0.25, 0.12, 0.02], pos=[2, 3])
        return m

    def test_a_visible_brittle_break_is_shattered_and_refused_bare_handed_in_both_modes(self):
        for perception in (None, "realistic"):
            e = self._pot_scene().env(room="kitchen", pos=(1, 2), hold={"right": "pot"}, perception=perception)
            h = e.realism.handles.handle if perception else (lambda x: x)
            ok(self, e.step("throw", h("pot"), "kitchen", cell=[2, 2], force="hard"))
            rec = ent(e, "pot")
            self.assertTrue(rec["broken"])
            self.assertTrue(rec["shattered"])
            self.assertIn("sharp", rec["hazards"])
            self.assertIn("rubber_gloves", rec["handled_with"])
            self.assertTrue(e.observe()["visible_entities"][h("pot")]["shattered"])     # glance tier
            ok(self, e.step("crouch"))
            r = e.step("pick_up", h("pot"))
            # realistic: refused on the visible break (before: it went ahead and counted as an unseen exposure)
            self.assertFalse(r["ok"])
            self.assertEqual(r["category"], "safety")
            self.assertEqual(stat(e, "safety_violations"), 0)
            ok(self, e.step("pick_up", h("gloves")))
            ok(self, e.step("wear", h("gloves")))
            ok(self, e.step("pick_up", h("pot")))                   # gloves make the broken pieces safe to handle
            self.assertEqual(stat(e, "safety_violations"), 0)

    def test_a_break_hidden_by_an_opaque_wrap_shows_once_unwrapped(self):
        wrap = {"material": "kraft_paper", "layers": 1, "coverage": 1.0, "padding_mm": 0.5, "stuffed": False, "secured": True}
        for perception in (None, "realistic"):
            e = self._pot_scene(wrapped=dict(wrap)).env(room="kitchen", pos=(1, 2), hold={"right": "pot"}, perception=perception)
            h = e.realism.handles.handle if perception else (lambda x: x)
            ok(self, e.step("throw", h("pot"), "kitchen", cell=[2, 2], force="hard"))
            self.assertTrue(ent(e, "pot")["broken"])
            self.assertFalse(ent(e, "pot").get("shattered"))          # the paper hides the pieces
            self.assertEqual(e.world.s["manipulation"]["unseen_breaks"], ["pot"])
            ok(self, e.step("unwrap", h("pot")))
            self.assertTrue(ent(e, "pot")["shattered"])
            self.assertEqual(e.world.s["manipulation"]["unseen_breaks"], [])
        # a see-through wrap (bubble wrap) shows the break at once
        e = self._pot_scene(wrapped=dict(wrap, material="bubble_wrap")).env(room="kitchen", pos=(1, 2), hold={"right": "pot"})
        ok(self, e.step("throw", "pot", "kitchen", cell=[2, 2], force="hard"))
        self.assertTrue(ent(e, "pot")["shattered"])

    def test_default_presented_feature_follows_perceived_hazards_not_hidden_instance_ones(self):
        """smooth_stone look-alikes, one with a hidden instance sharp edge: presenting either gives the same answer."""
        answers = []
        for type_name, extra in (("decor_stone", {}), ("faux_stone", {"hazards": ["sharp"]})):
            m = Mini()
            m.add("stone", type_name, "living", **extra)
            m.person("ana", "living", [2, 3])
            e = m.env(pos=(1, 3), hold={"right": "stone"}, perception="realistic")
            answers.append(ok(self, e.step("set_orientation", e.realism.handles.handle("stone"), toward="ana")))
        self.assertTrue(same_answer(*answers), [a["message"] for a in answers])
        self.assertIn("front first", answers[0]["message"])


class TierOneAdoptionTests(unittest.TestCase):
    """Tier-1 adoption (docs/domains/manipulation.md 15): dialogue routes, idle probes, notices, camera-only label faces,
    the direction-aware tip rule and the crush law's packed loads."""

    def test_canonical_questions_route_to_the_manip_answerer(self):
        from house_sim.kernel import dialogue
        m = Mini()
        m.add("jar", "generic_item", "kitchen", pos=[2, 2], label="jar", _keep=True)
        m.person("ana", "kitchen", [3, 3])
        e = m.env(room="kitchen", pos=(2, 3))
        subject = dialogue.Subject("entity", "jar")
        for aspect, detail, topic in (("keep", None, "manip:keep:jar"), ("status", None, "manip:in_use:jar"),
                                      ("how", "fragile", "manip:fragile:jar"), ("which", "contents", "manip:contents:jar"),
                                      ("intended", None, "manip:recipient:jar")):
            self.assertEqual(dialogue.find_route(e.world.s, "ana", subject, aspect, detail, None),
                             ("manipulation.items", topic))
        r = ok(self, e.step("ask", "ana", aspect="keep", about="jar"))
        self.assertIs(r["observation"]["last_answer"]["value"], True)

    def test_the_items_route_is_a_catch_all_for_things_without_the_hidden_truth(self):
        """Adoption breakage: manipulation.items claimed every how / keep / status / intended question about any
        entity, shadowing plants:care / plants:keep and laundry:rewear / keep_out / hang_or_fold. It now answers
        plainly only for a thing that carries the hidden truth (`_<key>`, `_ambiguous_<key>`, `_ask_<key>`) and is
        a weak fallback otherwise."""
        from house_sim.kernel import dialogue
        from house_sim.systems.manipulation import world as manip_world
        m = Mini()
        m.add("jar", "generic_item", "kitchen", pos=[2, 2], label="jar", _keep=True)
        m.add("box", "generic_item", "kitchen", pos=[3, 2], label="box", _ask_in_use=True)
        m.add("mug", "generic_item", "kitchen", pos=[4, 2], label="mug")
        reg_types = m.reg.types
        if "garden_plant" in reg_types:
            m.add("plant", "garden_plant", "yard", pos=[3, 3])
        if "coat" in reg_types:
            m.add("coat", "coat", "living", pos=[2, 2])
        m.person("ana", "kitchen", [3, 3])
        e = m.env(room="kitchen", pos=(2, 3))
        st = e.world.s
        S = dialogue.Subject
        route = manip_world._route_manip
        self.assertNotIsInstance(route(st, "ana", S("entity", "jar"), "keep", None, None), dialogue.WeakTopic)
        self.assertNotIsInstance(route(st, "ana", S("entity", "box"), "status", None, None), dialogue.WeakTopic)
        for subject, aspect, detail in ((S("entity", "jar"), "status", None), (S("entity", "mug"), "keep", None),
                                        (S("entity", "mug"), "how", None), (S("entity", "mug"), "intended", None)):
            topic = route(st, "ana", subject, aspect, detail, None)
            self.assertIsInstance(topic, dialogue.WeakTopic, (subject, aspect))
            # with nothing more specific registered, the fallback still answers
            if subject.id == "mug":
                self.assertEqual(dialogue.find_route(st, "ana", subject, aspect, detail, None)[0], "manipulation.items")
        self.assertEqual(dialogue.find_route(st, "ana", S("entity", "jar"), "keep", None, None),
                         ("manipulation.items", "manip:keep:jar"))
        names = {name for name, _, _ in dialogue.ROUTES}
        if "plants.care" in names and "garden_plant" in reg_types:
            self.assertEqual(dialogue.find_route(st, "ana", S("entity", "plant"), "how", None, None),
                             ("plants.care", "plants:care:plant"))
            self.assertEqual(dialogue.find_route(st, "ana", S("entity", "plant"), "keep", None, None),
                             ("plants.keep", "plants:keep:plant"))
        if "laundry.item" in names and "coat" in reg_types:
            for words, aspect, topic in ((("dirty",), "status", "laundry:rewear:coat"),
                                         (("dryer",), "how", "laundry:keep_out:coat"),
                                         (("hang",), "how", "laundry:hang_or_fold:coat")):
                self.assertEqual(dialogue.find_route(st, "ana", S("entity", "coat", words), aspect, None, None),
                                 ("laundry.item", topic))

    def test_every_system_has_an_exact_idle_probe_and_a_quiet_house_is_idle(self):
        from house_sim.kernel import plugins
        e = Mini().env()
        for name in ("manipulation.falls", "manipulation.breaks", "manipulation.timers", "manipulation.wind",
                     "manipulation.jobs", "manipulation.goals"):
            self.assertIn(name, plugins.SYSTEM_IDLE)
            self.assertTrue(plugins.SYSTEM_IDLE[name](e.world), name)

    def test_noise_kinds_are_registered_notices(self):
        from house_sim.kernel import timeline
        self.assertEqual({k: timeline.NOTICE_KINDS[k]["db"] for k in ("clink", "rip", "bang")},
                         {"clink": 50.0, "rip": 55.0, "bang": 65.0})

    def test_a_label_face_is_camera_bookkeeping_written_only_in_geometric_mode(self):
        m = Mini()
        m.add("table", "table", "living", pos=[1, 1])
        m.add("box", "generic_item", "living", label="storage box", size_m=[0.3, 0.2, 0.15], mass_kg=0.5)
        m.add("sheet", "label_sheet", "living", pos=[4, 4])
        for camera, first, flipped in ((None, None, None), ("geometric", "top", "bottom")):
            e = m.env(pos=(1, 2), hold={"right": "box", "left": "sheet"}, camera=camera)
            ok(self, e.step("apply_label", "box", text="Winter clothes", face="top"))
            self.assertEqual(ent(e, "box").get("label_facing"), first)
            ok(self, e.step("flip", "box"))
            self.assertEqual(ent(e, "box").get("label_facing"), flipped)

    def test_a_push_tips_furniture_about_the_base_side_along_the_push(self):
        m = Mini()
        m.add("shelf", "generic_item", "living", label="bookcase", category="furniture", mass_kg=18.0, size_m=[0.8, 0.3, 1.8],
              pos=[4, 4], portable=False, foot_material="felt")
        e = m.env(pos=(3, 4))
        ok(self, e.step("push", "shelf", direction="east", distance_m=0.25, height="high"))     # pivots on its 0.8 m side
        self.assertFalse(ent(e, "shelf").get("tipped_over", False))
        e = m.env(pos=(4, 3))
        ok(self, e.step("push", "shelf", direction="south", distance_m=0.25, height="high"))    # 0.3 m deep: over it goes
        self.assertTrue(ent(e, "shelf").get("tipped_over"))

    def test_with_the_crush_law_active_a_packed_glass_still_takes_the_load(self):
        from house_sim.kernel.phys import lawbook
        with lawbook.forced(crush=True):
            e = PackTests()._scene().env(pos=(1, 2), hold={"right": "glass", "left": "books"})
            ok(self, e.step("pack", "glass", "box"))
            ok(self, e.step("pack", "books", "box"))
            self.assertTrue(ent(e, "glass")["broken"])


if __name__ == "__main__":
    unittest.main()
