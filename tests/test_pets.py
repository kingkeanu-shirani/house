"""Pets domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=pets python -m unittest tests/test_pets.py
Strict journal: HOUSE_SIM_DOMAINS=pets HOUSE_SIM_STRICT=1 python -m unittest tests/test_pets.py
The tests build a small explicit house from core types plus pets types, and use generic_item (with explicit props) for
other domains' objects (sinks, bins, toilets, fans, vacuums), so they also run when every domain is loaded.
"""
import copy
import json
import re
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, FURNISHERS, INVARIANTS, OWNERS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.registry import default_registry
from house_sim.systems.pets import common as C
from house_sim.systems.pets import model as M

load_domains()

PET_VERBS = ["feed", "give_treat", "play_fetch", "brush_pet", "wipe_paws", "bathe_pet", "dry_pet", "scoop_litter", "refill_litter",
             "change_litter", "bag_waste", "attach_leash", "remove_leash", "walk_pet", "crate", "uncrate", "give_pet_medication",
             "check_pet", "calm_pet", "feed_fish", "change_aquarium_water", "collect_eggs"]


class House:
    """living - kitchen - (back door) - fenced backyard; living - hall - bedroom / laundry / bathroom; living - entry -
    (front door) - front yard (street side); entry - coat closet (door); front <-> back side gate."""

    def __init__(self, start="2026-10-09T10:00:00", weather=None):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0]},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0]},
            "hall": {"type": "hallway", "size_m": [2.0, 6.0]},
            "bedroom": {"type": "bedroom", "size_m": [3.5, 4.0]},
            "laundry": {"type": "laundry_room", "size_m": [2.5, 3.0]},
            "bath": {"type": "bathroom", "size_m": [2.5, 3.0]},
            "closet": {"type": "coat_closet", "size_m": [1.0, 1.5]},
            "entry": {"type": "entryway", "size_m": [2.5, 3.0]},
            "front": {"type": "front_yard", "size_m": [8.0, 6.0], "outdoor": True, "terrain": "grass"},
            "back": {"type": "backyard", "size_m": [8.0, 6.0], "outdoor": True, "terrain": "grass"},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}},
            "c_lh": {"rooms": ["living", "hall"], "cells": {"living": [4, 0], "hall": [1, 11]}},
            "c_hb": {"rooms": ["hall", "bedroom"], "cells": {"hall": [3, 2], "bedroom": [0, 2]}, "door": "door_bed"},
            "c_hl": {"rooms": ["hall", "laundry"], "cells": {"hall": [3, 6], "laundry": [0, 2]}},
            "c_hba": {"rooms": ["hall", "bath"], "cells": {"hall": [0, 6], "bath": [4, 2]}, "door": "door_bath"},
            "c_ec": {"rooms": ["entry", "closet"], "cells": {"entry": [4, 1], "closet": [0, 1]}, "door": "door_closet"},
            "c_le": {"rooms": ["living", "entry"], "cells": {"living": [0, 5], "entry": [4, 4]}},
            "c_ef": {"rooms": ["entry", "front"], "cells": {"entry": [0, 2], "front": [15, 3]}, "door": "front_door"},
            "c_kb": {"rooms": ["kitchen", "back"], "cells": {"kitchen": [7, 5], "back": [0, 0]}, "door": "back_door"},
            "c_fb": {"rooms": ["front", "back"], "cells": {"front": [0, 6], "back": [15, 6]}, "door": "side_gate", "kind": "gate"},
        }
        for conn in conns.values():
            conn.setdefault("width_m", 0.85)
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": start, "hemisphere": "north"},
                  "weather": weather or {"condition": "clear", "temperature_c": 15.0, "wind_mps": 1.0}, "household": {"rules": []},
                  "robot": {"room": "living", "pos": [5, 6]}}
        for rid, room in rooms.items():
            room["exits"] = []
            floor = self.reg.make("floor_surface", rid, size_m=[room["size_m"][0], room["size_m"][1], 0.01], outdoor=bool(room.get("outdoor")))
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            if not room.get("outdoor"):
                light = self.reg.make("ceiling_light", rid, on=True)
                light["pos"] = [1, 1]
                light["mounted"] = True
                self.c["entities"]["light_" + rid] = light
        self.door("door_bed", "door", ["hall", "bedroom"], open=True)
        self.door("door_bath", "door", ["hall", "bath"], open=True)
        self.door("door_closet", "door", ["entry", "closet"], open=False)
        self.door("front_door", "exterior_door", ["entry", "front"], label="front door", locked=False)
        self.door("back_door", "exterior_door", ["kitchen", "back"], label="back door", locked=False)
        self.door("side_gate", "gate", ["front", "back"], label="side gate")
        self.add("counter", "counter", "kitchen", pos=[2, 0])
        self.add("cabinet", "cabinet", "kitchen", pos=[2, 8])
        self.add("table", "table", "living", pos=[6, 9])
        self.add("sofa", "sofa", "living", pos=[1, 1])
        self.add("bed", "bed", "bedroom", pos=[3, 4])
        self.add("hooks", "coat_hooks", "entry", pos=[2, 0], mounted=True, blocks_path=False)
        self.add("shelf", "shelf_unit", "laundry", pos=[3, 4])
        self.add("sink", "generic_item", "kitchen", pos=[6, 0], label="kitchen sink", water_source=True, drain=True, portable=False,
                 accepts_items=True, container=True, capacity_l=30.0, mount_height_m=0.85, mounted=True)
        self.add("trash", "generic_item", "kitchen", pos=[7, 9], label="kitchen trash can", category="trash_can", waste_stream="trash", portable=False,
                 accepts_items=True, container=True, capacity_l=50.0, size_m=[0.4, 0.3, 0.65])
        self.add("outdoor_trash", "generic_item", "front", pos=[2, 2], label="outdoor trash bin", category="trash_can", waste_stream="trash", portable=False,
                 accepts_items=True, container=True, capacity_l=240.0, size_m=[0.6, 0.7, 1.05])
        self.add("recycling", "generic_item", "front", pos=[4, 2], label="recycling bin", category="recycling_bin", waste_stream="recycling", portable=False,
                 accepts_items=True, container=True, capacity_l=60.0)
        self.add("toilet", "generic_item", "bath", pos=[1, 1], label="toilet", category="toilet", toilet=True, portable=False,
                 accepts_items=True, container=True, capacity_l=6.0)
        self.add("log", "pet_care_log", "counter")
        self.c["people"]["resident_1"] = {"label": "Sam", "role": "adult", "authority": 2, "location": "living", "pos": [8, 8], "consent": True,
                                         "awake": True, "permissions": [], "preferences": {}, "has_phone": True, "reachable_by_phone": True}

    def door(self, eid, type_name, rooms, **props):
        rec = self.reg.make(type_name, rooms[0], **props)
        rec["connects"] = list(rooms)
        cid = next(k for k, v in self.c["connections"].items() if v.get("door") == eid)
        rec["pos"] = list(self.c["connections"][cid]["cells"][rooms[0]])
        rec["blocks_path"] = False
        self.c["entities"][eid] = rec
        return eid

    def add(self, eid, type_name, location, **props):
        pos = props.pop("pos", None)
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["rooms"]:
            rec["pos"] = list(pos) if pos else [2, 2]
        elif location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
        if rec.get("relation") == "on" and location in self.c["entities"] and "spos" not in rec:
            used = [e for e in self.c["entities"].values() if e.get("location") == location and e.get("relation") == "on"]
            rec["spos"] = [round(0.25 * len(used), 2), 0.0]
        self.c["entities"][eid] = rec
        return eid

    def pet(self, pid, species="dog", room="living", pos=None, **props):
        record = {"species": species, "label": pid.replace("_", " ").title(), "location": room, "pos": list(pos or [5, 5])}
        behaviors = props.pop("behaviors", {})
        record.update(props)
        M.ensure_record(record, pid)
        record["behaviors"].update(behaviors)
        record["escape_risk"] = C.behavior(record, "door_dash")
        self.c["pets"][pid] = record
        return pid

    def env(self, robot_room=None, robot_pos=None, hold=None, perception="oracle"):
        c = copy.deepcopy(self.c)
        if robot_room:
            c["robot"]["room"] = robot_room
        if robot_pos:
            c["robot"]["pos"] = list(robot_pos)
        for hand, eid in (hold or {}).items():
            c["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid
            rec = c["entities"][eid]
            rec.update(location="robot", relation="held", held_hand=hand)
            for key in ("pos", "spos", "shelf"):
                rec.pop(key, None)
        return HouseEnv(c, None, seed=0, perception=perception)


def step(env, action, target="", destination="", **kw):
    return env.step(action, target, destination, **kw)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def harms(env):
    return C.harms(env.world.s)


def pet(env, pid):
    return env.world.s["pets"][pid]


def ent(env, eid):
    return env.world.s["entities"][eid]


def near(env, target):
    return env.step("approach", target)


# ----------------------------------------------------------------------

class RegistrationTests(unittest.TestCase):
    def test_owned_verbs_registered_and_only_those(self):
        mine = sorted(n for n in ACTIONS if OWNERS.get(("action", n), "").startswith("house_sim.systems.pets"))
        self.assertEqual(sorted(PET_VERBS), mine)

    def test_types_load_with_stem_pets(self):
        reg = default_registry()
        names = [n for n in reg.types if reg.sources[n] == "pets.json"]
        self.assertGreater(len(names), 90)
        for name in ("dog_food_bowl", "pet_water_fountain", "litter_box", "leash", "aquarium", "reptile_enclosure", "bird_cage", "chicken_coop"):
            self.assertIn(name, names)

    def test_namespaced_registrations(self):
        self.assertTrue(any(n.startswith("pets.") for n in SKILLS))
        self.assertTrue(any(n.startswith("pets.") for n in SCENARIO_HELPERS))
        for name in INVARIANTS:
            if OWNERS.get(("invariant", name), "").startswith("house_sim.systems.pets"):
                self.assertTrue(name.startswith("pets."))
        self.assertTrue(any(f[1] == "pets.household" for f in FURNISHERS))


class FeedingTests(unittest.TestCase):
    def setUp(self):
        self.h = House(start="2026-10-09T07:20:00")
        h = self.h
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 6], size="medium", hunger=0.9, _diet={"portion_g": 120.0})
        h.add("mat", "feeding_mat", "kitchen", pos=[5, 8])
        h.add("dog_bowl", "dog_food_bowl", "mat", owner="dog_1")
        h.add("kibble", "dog_kibble_bag", "kitchen", pos=[3, 4], open=True)
        h.add("catfood", "cat_kibble_bag", "kitchen", pos=[3, 5], open=True)
        h.add("scale", "generic_item", "counter", tool_kind="kitchen_scale", capacity_g=5000.0, label="kitchen scale")
        h.add("scoop", "pet_food_scoop", "counter")
        self.key = "2026-10-09:breakfast"
        h.c["pets"]["dog_1"]["meals_given"] = {self.key: 0}

    def _serve(self, env, **kw):
        near(env, "dog_bowl")
        return step(env, "feed", "dog_bowl", food="kibble", **kw)

    def test_scale_portion_counts_meal_and_logs(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        r = ok(self, self._serve(env, amount=120, unit="g", measure="scale"))
        self.assertIn("120", r["message"])
        served = pet(env, "dog_1")["served_g"]["dog_kibble"]
        self.assertAlmostEqual(served, 120.0, delta=1.5)
        self.assertEqual(pet(env, "dog_1")["meals_given"][self.key], 1)
        self.assertIn("dog_1:breakfast", ent(env, "log")["logged"])
        self.assertIn("Pet care log", ent(env, "log")["text"])

    def test_heaped_scoop_overserves(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble", "left": "scoop"})
        ok(self, self._serve(env, amount=1, unit="scoop", measure="heaped"))
        self.assertGreater(pet(env, "dog_1")["served_g"]["dog_kibble"], 237 * 0.43 * 1.2)

    def test_level_scoop_is_close(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble", "left": "scoop"})
        ok(self, self._serve(env, amount=1, unit="scoop", measure="level"))
        self.assertAlmostEqual(pet(env, "dog_1")["served_g"]["dog_kibble"], 237 * 0.43, delta=237 * 0.43 * 0.04)

    def test_double_feeding_flagged(self):
        self.h.c["pets"]["dog_1"]["meals_given"] = {self.key: 1}
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        r = ok(self, self._serve(env, amount=120, unit="g", measure="scale"))
        self.assertIn("already been fed", r["message"])
        self.assertTrue(pet(env, "dog_1")["double_fed"])
        self.assertEqual(harms(env).get("double_fed"), 1)

    def test_eyeballing_needs_no_scale_but_is_inaccurate(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        ok(self, self._serve(env, amount=120, unit="g", measure="eyeball"))
        served = pet(env, "dog_1")["served_g"]["dog_kibble"]
        self.assertTrue(84 <= served <= 156)

    def test_wrong_food_is_flagged_and_eaten_wrong(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "catfood"})
        near(env, "dog_bowl")
        ok(self, step(env, "feed", "dog_bowl", food="catfood", amount=60, unit="g", measure="scale"))
        self.assertEqual(pet(env, "dog_1")["wrong_diet_served"], 1)
        step(env, "wait", seconds=300)
        self.assertGreater(pet(env, "dog_1")["wrong_food_g"], 30)

    def test_dog_eats_its_serving(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        ok(self, self._serve(env, amount=120, unit="g", measure="scale"))
        step(env, "wait", seconds=300)
        self.assertAlmostEqual(pet(env, "dog_1")["intake_g"]["dog_kibble"], 120, delta=3)
        self.assertLess(pet(env, "dog_1")["hunger"], 0.6)

    def test_overflow_spills_onto_floor(self):
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        r = ok(self, self._serve(env, amount=900, unit="g", measure="scale"))
        self.assertIn("overflowed", r["message"])
        self.assertTrue(any(e.get("spilled") for e in env.world.s["entities"].values()))
        self.assertGreater(env.world.s["stats"]["spills"], 0)

    def test_unopened_bag_refused(self):
        self.h.c["entities"]["kibble"]["open"] = False
        env = self.h.env("kitchen", [4, 7], hold={"right": "kibble"})
        r = self._serve(env, amount=100, unit="g", measure="scale")
        self.assertFalse(r["ok"])
        self.assertIn("Open", r["message"])


class SeparateMealsTests(unittest.TestCase):
    def _house(self, cat_bowl_high):
        h = House(start="2026-10-09T07:20:00")
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 6], size="large", hunger=0.9, behaviors={"steals_food": 0.9})
        h.pet("cat_1", "cat", room="kitchen", pos=[1, 6], hunger=0.9)
        h.add("dog_bowl", "dog_food_bowl", "kitchen", pos=[5, 8], owner="dog_1", food_g={"dog_kibble": 200.0},
              food_meta={"dog_kibble": {"wet": False, "served_s": 0.0, "density": 0.43}})
        if cat_bowl_high:
            h.add("cat_shelf", "wall_shelf", "kitchen", pos=[0, 9], mount_height_m=1.6, mounted=True, blocks_path=False)
            loc = "cat_shelf"
        else:
            h.add("cat_mat", "feeding_mat", "kitchen", pos=[6, 8])
            loc = "cat_mat"
        h.add("cat_bowl", "cat_food_bowl", loc, owner="cat_1", food_g={"cat_wet": 85.0},
              food_meta={"cat_wet": {"wet": True, "served_s": 0.0, "density": 1.0}})
        return h

    def test_side_by_side_dog_steals(self):
        env = self._house(False).env("living")
        step(env, "wait", seconds=900)
        self.assertGreater(pet(env, "dog_1")["intake_g"].get("cat_wet", 0), 1.0)

    def test_raised_shelf_keeps_cat_food_safe(self):
        env = self._house(True).env("living")
        step(env, "wait", seconds=900)
        self.assertNotIn("cat_wet", pet(env, "dog_1")["intake_g"])
        self.assertGreater(pet(env, "cat_1")["intake_g"].get("cat_wet", 0), 20)

    def test_wet_food_goes_stale_and_spoils(self):
        h = self._house(True)
        h.c["pets"]["cat_1"]["hunger"] = 0.0
        h.c["pets"]["cat_1"]["_meal_left_g"] = 0.0
        env = h.env("living")
        step(env, "wait", seconds=1900)
        self.assertEqual(ent(env, "cat_bowl").get("stale_wet"), ["cat_wet"])
        step(env, "wait", seconds=3 * 3600)
        self.assertTrue(ent(env, "cat_bowl").get("ants"))

    def test_bag_waste_discards_leftovers(self):
        env = self._house(False).env("kitchen", [6, 7])
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "cat_bowl"))
        ok(self, step(env, "stand"))
        near(env, "trash")
        ok(self, step(env, "bag_waste", "cat_bowl", destination="trash"))
        self.assertEqual(ent(env, "cat_bowl")["food_g"], {})


class StorageTests(unittest.TestCase):
    def test_opened_can_spoils_on_counter_not_in_fridge(self):
        h = House()
        h.add("can_a", "cat_wet_food_can", "counter")
        h.add("fridge", "openable_storage", "kitchen", pos=[0, 6], label="refrigerator", internal_temp_c=4.0, open=True,
              inner_size_m=[0.6, 0.55, 0.5], capacity_l=300.0)
        h.add("can_b", "cat_wet_food_can", "fridge", opened=True, opened_s=0.0, covered=True, temperature_c=4.0)
        env = h.env("kitchen", [2, 1])
        ok(self, step(env, "open", "can_a"))
        self.assertTrue(ent(env, "can_a")["opened"])
        step(env, "wait", seconds=4 * 3600 + 120)
        self.assertTrue(ent(env, "can_a").get("spoiled"))
        self.assertFalse(ent(env, "can_b").get("spoiled"))

    def test_lid_covers_can(self):
        h = House()
        h.add("can_a", "cat_wet_food_can", "counter", opened=True, open=True, accepts_items=True, surface=True, surface_size_m=[0.09, 0.09])
        h.add("lid", "pet_food_can_lid", "counter")
        env = h.env("kitchen", [2, 1])
        ok(self, step(env, "pick_up", "lid"))
        ok(self, step(env, "place", "lid", "can_a"))
        self.assertTrue(ent(env, "can_a")["covered"])

    def test_dog_raids_open_bag_but_not_airtight_bin(self):
        h = House()
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 6], size="large")
        h.add("bag", "dog_kibble_bag", "kitchen", pos=[5, 8], open=True)
        h.add("bin", "pet_food_bin", "kitchen", pos=[6, 8])
        h.add("bag2", "dog_kibble_bag", "bin")
        env = h.env("living")
        step(env, "wait", seconds=1200)
        self.assertGreater(pet(env, "dog_1").get("raided_g", 0), 100)
        self.assertEqual(ent(env, "bag2")["remaining_g"], 6800.0)
        self.assertEqual(harms(env).get("food_raid"), 1)

    def test_automatic_feeder_dispenses_on_schedule(self):
        h = House(start="2026-10-09T06:55:00")
        h.pet("cat_1", "cat", room="living", pos=[3, 3])
        h.add("feeder", "automatic_pet_feeder", "kitchen", pos=[1, 8], owner="cat_1", hopper_g=40.0)
        env = h.env("living")
        step(env, "wait", seconds=600)
        self.assertLess(ent(env, "feeder")["hopper_g"], 40.0)
        self.assertIn("Dispensed", ent(env, "feeder")["display"])

    def test_empty_feeder_hopper_shows_empty(self):
        h = House(start="2026-10-09T06:55:00")
        h.add("feeder", "automatic_pet_feeder", "kitchen", pos=[1, 8], hopper_g=0.0)
        env = h.env("living")
        step(env, "wait", seconds=600)
        self.assertEqual(ent(env, "feeder")["display"], "HOPPER EMPTY")


class TreatTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        self.h.pet("dog_1", "dog", room="living", pos=[5, 5], size="small")
        self.h.add("jar", "pet_treat_jar", "table", open=True)
        self.h.add("grapes", "generic_item", "table", label="bunch of grapes", food={"kind": "grapes", "quantity": 300, "unit": "g"},
                   toxin="grapes", size_m=[0.2, 0.12, 0.08], mass_kg=0.3)

    def test_treats_counted_within_limit(self):
        env = self.h.env("living", [5, 6], hold={"right": "jar"})
        ok(self, step(env, "give_treat", "dog_1", treat="jar", count=2))
        self.assertEqual(pet(env, "dog_1")["treats_today"], 2)
        self.assertEqual(ent(env, "jar")["count"], 38)
        self.assertNotIn("treat_over_limit", harms(env))

    def test_over_limit_and_no_treat_diet(self):
        self.h.c["pets"]["dog_1"]["diet"]["no_treats"] = True
        env = self.h.env("living", [5, 6], hold={"right": "jar"})
        ok(self, step(env, "give_treat", "dog_1", treat="jar", count=5))
        self.assertTrue(pet(env, "dog_1")["treat_over_limit"])
        self.assertEqual(harms(env).get("diet_violation"), 1)

    def test_grapes_poison_and_fail_any_task(self):
        env = self.h.env("living", [5, 6], hold={"right": "grapes"})
        ok(self, step(env, "give_treat", "dog_1", treat="grapes"))
        self.assertEqual(pet(env, "dog_1")["toxic_ingestions"], 1)
        self.assertIn("pet_poisoned", env.evaluate()["failures"])
        step(env, "wait", seconds=7 * 3600)
        self.assertIn("vomiting", pet(env, "dog_1")["signs"])

    def test_trade_makes_relaxed_dog_drop_but_not_guarding_dog(self):
        self.h.add("ball", "fetch_ball", "dog_1", relation="held")
        env = self.h.env("living", [5, 6], hold={"right": "jar"})
        ok(self, step(env, "give_treat", "dog_1", treat="jar", trade=True))
        self.assertEqual(ent(env, "ball")["location"], "living")
        h2 = House()
        h2.pet("dog_1", "dog", room="living", pos=[5, 5], _guard_intensity=0.9)
        h2.add("ball", "fetch_ball", "dog_1", relation="held")
        h2.add("jar", "pet_treat_jar", "table", open=True)
        env2 = h2.env("living", [5, 6], hold={"right": "jar"})
        r = ok(self, step(env2, "give_treat", "dog_1", treat="jar", trade=True))
        self.assertIn("guarding", r["message"])
        self.assertEqual(ent(env2, "ball")["location"], "dog_1")


class WaterTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        self.h.add("wb", "dog_water_bowl", "kitchen", pos=[5, 8], liquid_l=1.0, biofilm=0.5, debris=0.4, _water_age_s=40 * 3600.0, _last_l=1.0)

    def test_top_up_keeps_slime_but_emptying_resets(self):
        env = self.h.env("living")
        env.world.s["entities"]["wb"]["liquid_l"] = 1.5
        step(env, "wait", seconds=1)
        rec = ent(env, "wb")
        self.assertGreater(rec["biofilm"], 0.45)
        self.assertGreater(rec["_water_age_s"], 20 * 3600)
        self.assertGreater(rec["debris"], 0.2)
        env.world.s["entities"]["wb"]["liquid_l"] = 0.0
        step(env, "wait", seconds=1)
        self.assertEqual(ent(env, "wb")["debris"], 0.0)
        env.world.s["entities"]["wb"]["liquid_l"] = 1.5
        step(env, "wait", seconds=1)
        self.assertLess(ent(env, "wb")["_water_age_s"], 10)

    def test_biofilm_grows_and_scrubbing_removes_it(self):
        from house_sim.systems.pets import water as W
        self.h.c["entities"]["wb"]["biofilm"] = 0.0
        env = self.h.env("living")
        step(env, "wait", seconds=86400)
        step(env, "wait", seconds=86400)
        self.assertGreater(ent(env, "wb")["biofilm"], 0.5)

        class Spec:
            name = "scrub"

        class Ctx:
            pass
        ctx = Ctx()
        ctx.w, ctx.args, ctx.spec = env.world, {"target": "wb"}, Spec()
        env.world.begin()
        W._scrubbed(ctx, None)
        env.world.commit()
        self.assertLess(ent(env, "wb")["biofilm"], 0.05)

    def test_pet_drinks_and_debris_rises(self):
        self.h.c["entities"]["wb"].update(biofilm=0.0, debris=0.0, _water_age_s=0.0)
        self.h.pet("dog_1", "dog", room="kitchen", pos=[4, 6], water_deficit_ml=500.0)
        env = self.h.env("living")
        step(env, "wait", seconds=120)
        self.assertLess(ent(env, "wb")["liquid_l"], 1.0)
        self.assertGreater(ent(env, "wb")["debris"], 0.0)
        self.assertLess(pet(env, "dog_1")["water_deficit_ml"], 300)

    def test_cat_refuses_water_beside_litter_box(self):
        h = House()
        h.pet("cat_1", "cat", room="laundry", pos=[1, 1], water_deficit_ml=60.0)
        h.add("cwb", "cat_water_bowl", "laundry", pos=[3, 1], liquid_l=0.4, _last_l=0.4)
        h.add("box", "litter_box", "laundry", pos=[3, 2])
        env = h.env("living")
        step(env, "wait", seconds=300)
        self.assertGreater(ent(env, "cwb")["liquid_l"], 0.399)   # only evaporation (a drink would take >= 25 ml)
        h.c["entities"]["box"]["pos"] = [1, 4]
        h.c["entities"]["shelf"]["pos"] = [3, 5]
        env2 = h.env("living")
        step(env2, "wait", seconds=300)
        self.assertLess(ent(env2, "cwb")["liquid_l"], 0.4)

    def test_no_water_leads_to_dehydration_harm(self):
        h = House()
        h.pet("dog_1", "dog", room="bedroom", pos=[1, 1], size="small")
        env = h.env("living")
        for _ in range(30):
            step(env, "wait", seconds=3600)
        self.assertTrue(pet(env, "dog_1").get("dehydrated_severe"))
        self.assertIn("pet_dehydrated", env.evaluate()["failures"])

    def test_carrying_overfull_bowl_spills(self):
        h = House()
        h.add("wb", "dog_water_bowl", "living", pos=[5, 5], liquid_l=1.85, _last_l=1.85)
        h.add("wb2", "dog_water_bowl", "living", pos=[6, 5], liquid_l=1.3, _last_l=1.3)
        env = h.env("living", [5, 6], hold={"right": "wb"})
        ok(self, step(env, "move", "kitchen"))
        self.assertLess(ent(env, "wb")["liquid_l"], 1.85)
        self.assertGreater(env.world.s["stats"]["spills"], 0)
        env2 = h.env("living", [5, 6], hold={"right": "wb2"})
        ok(self, step(env2, "move", "kitchen"))
        self.assertEqual(ent(env2, "wb2")["liquid_l"], 1.3)


class FountainTests(unittest.TestCase):
    def _env(self, level, temp=20.0):
        h = House()
        h.add("f", "pet_water_fountain", "kitchen", pos=[5, 8], liquid_l=level, _last_l=level, liquid_temp_c=temp)
        h.add("filt", "fountain_filter", "f", relation="part", slot="filter", installed_s=-40 * 86400.0)
        h.c["entities"]["f"]["slots"]["filter"]["item"] = "filt"
        return h.env("kitchen", [5, 7])

    def test_flowing_between_lines_and_filter_due(self):
        env = self._env(1.4)
        step(env, "wait", seconds=60)
        self.assertTrue(ent(env, "f")["flowing"])
        self.assertTrue(ent(env, "f")["filter_due"])

    def test_dry_run_damages_pump(self):
        env = self._env(0.3)
        step(env, "wait", seconds=400)
        self.assertFalse(ent(env, "f")["pump_ok"])
        self.assertIn("damage", env.evaluate()["failures"])

    def test_overfill_splashes_and_wets_floor(self):
        env = self._env(2.0)
        step(env, "wait", seconds=120)
        self.assertGreater(ent(env, "f")["overflowed_l"], 0)
        self.assertTrue(env.world.s["rooms"]["kitchen"].get("wet_cells"))

    def test_hot_water_warps_pump(self):
        env = self._env(1.4, temp=60.0)
        step(env, "wait", seconds=30)
        self.assertTrue(ent(env, "f").get("pump_damaged"))

    def test_switching_pump_off_stops_flow(self):
        env = self._env(1.4)
        ok(self, step(env, "switch_off", "f"))
        step(env, "wait", seconds=30)
        self.assertFalse(ent(env, "f")["flowing"])

    def test_new_filter_installed_is_not_due(self):
        env = self._env(1.4)
        h = House()
        ok(self, step(env, "open", "f"))
        ok(self, step(env, "uninstall", "f", slot="filter"))
        ok(self, step(env, "place", "filt", "kitchen"))
        env.world.s["entities"]["newf"] = env.registry.make("fountain_filter", "robot", relation="held", held_hand="right")
        env.world.s["robot"]["hands"]["right"] = "newf"
        ok(self, step(env, "install", "newf", "f", slot="filter"))
        step(env, "wait", seconds=30)
        self.assertFalse(ent(env, "f")["filter_due"])


class LitterTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        h = self.h
        h.add("box", "litter_box", "laundry", pos=[2, 4], clumps=3, feces=1, _buried=2, litter_kg=10.0, home="laundry", home_pos=[2, 4])
        h.add("scoop", "litter_scoop", "laundry", pos=[1, 4])
        h.add("roll", "poop_bag_roll", "laundry", pos=[3, 3])

    def test_thorough_scoop_gets_buried_clumps(self):
        env = self.h.env("laundry", [2, 3], hold={"right": "scoop"})
        r = ok(self, step(env, "scoop_litter", "box", destination="roll", tool="scoop"))
        self.assertEqual((ent(env, "box")["clumps"], ent(env, "box")["_buried"], ent(env, "box")["feces"]), (0, 0, 0))
        bag = env.world.s["robot"]["hands"]["left"]
        self.assertEqual(ent(env, bag)["contents"]["litter_clumps"], 5)
        self.assertIn("Scooped 5", r["message"])

    def test_litter_odor_is_a_dict_and_observable(self):
        self.h.c["entities"]["box"]["odor"] = 0.6          # legacy float from an older writer
        env = self.h.env("laundry", [2, 3], hold={"right": "scoop"})
        ok(self, step(env, "scoop_litter", "box", destination="roll", tool="scoop"))
        odor = ent(env, "box")["odor"]
        self.assertIsInstance(odor, dict)
        self.assertLess(odor.get("litter", 0.0), 0.6)
        step(env, "wait", seconds=600)
        env.observe()                                         # every domain's smell observer reads the dict
        self.assertIsInstance(ent(env, "box")["odor"], dict)

    def test_quick_scoop_leaves_buried(self):
        env = self.h.env("laundry", [2, 3], hold={"right": "scoop"})
        ok(self, step(env, "scoop_litter", "box", destination="roll", tool="scoop", thorough=False))
        self.assertEqual(ent(env, "box")["_buried"], 2)

    def test_abnormal_output_noticed(self):
        self.h.c["entities"]["box"]["_abnormal"] = {"blood": True}
        env = self.h.env("laundry", [2, 3], hold={"right": "scoop"})
        r = ok(self, step(env, "scoop_litter", "box", destination="roll", tool="scoop"))
        self.assertIn("blood tinged", r["message"])
        self.assertIn("blood_tinged_clumps", ent(env, "box")["observed_abnormal"])

    def test_flushing_litter_clogs_toilet(self):
        h = self.h
        h.add("box2", "litter_box", "bath", pos=[2, 2], clumps=2)
        env = h.env("bath", [2, 1], hold={"right": "scoop"})
        ok(self, step(env, "scoop_litter", "box2", destination="toilet", tool="scoop"))
        self.assertTrue(ent(env, "toilet")["clogged"])
        self.assertEqual(harms(env).get("litter_flushed"), 1)

    def test_tied_bag_goes_in_trash_not_recycling(self):
        env = self.h.env("laundry", [2, 3], hold={"right": "scoop"})
        ok(self, step(env, "scoop_litter", "box", destination="roll", tool="scoop"))
        bag = env.world.s["robot"]["hands"]["left"]
        ok(self, step(env, "bag_waste", bag, tie=True))
        self.assertTrue(ent(env, bag)["tied"])
        ok(self, step(env, "place", "scoop", "laundry"))
        for room in ("hall", "living", "entry"):
            ok(self, step(env, "move", room))
        ok(self, step(env, "approach", "front_door"))
        ok(self, step(env, "open", "front_door"))
        ok(self, step(env, "move", "front"))
        ok(self, step(env, "approach", "recycling"))
        r = step(env, "place", bag, "recycling", relation="in")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")
        ok(self, step(env, "approach", "outdoor_trash"))
        ok(self, step(env, "place", bag, "outdoor_trash", relation="in"))

    def test_refill_to_depth_and_pour_high_spills(self):
        self.h.add("lbag", "cat_litter_bag", "laundry", pos=[1, 1], open=True)
        self.h.c["entities"]["box"]["litter_kg"] = 4.0
        env = self.h.env("laundry", [2, 3], hold={"right": "lbag"})
        ok(self, step(env, "refill_litter", "box", litter="lbag", depth_cm=6.0))
        from house_sim.systems.pets.litter import depth_cm
        self.assertAlmostEqual(depth_cm(ent(env, "box")), 6.0, delta=0.1)
        env2 = self.h.env("laundry", [2, 3], hold={"right": "lbag"})
        ok(self, step(env2, "refill_litter", "box", litter="lbag", depth_cm=6.0, pour="high"))
        self.assertEqual(harms(env2).get("litter_dust"), 1)
        self.assertGreater(ent(env2, "floor_laundry")["soil"].get("cat_litter", 0), 0)

    def _cat_needs_to_go(self, h):
        h.pet("cat_1", "cat", room="laundry", pos=[1, 1], bladder=0.99, _litter_pref="clumping_clay_unscented")
        return h.env("living")

    def test_cat_uses_acceptable_box(self):
        self.h.c["entities"]["box"].update(clumps=0, feces=0, _buried=0)
        env = self._cat_needs_to_go(self.h)
        step(env, "wait", seconds=300)
        self.assertEqual(ent(env, "box")["clumps"], 1)
        self.assertEqual(pet(env, "cat_1")["accidents"], 0)

    def test_dirty_or_mixed_box_causes_house_soiling(self):
        self.h.c["entities"]["box"].update(clumps=6)
        env = self._cat_needs_to_go(self.h)
        step(env, "wait", seconds=300)
        self.assertEqual(pet(env, "cat_1")["accidents"], 1)
        self.assertEqual(harms(env).get("house_soiling"), 1)
        h2 = House()
        h2.add("box", "litter_box", "laundry", pos=[2, 4], litter_kg=10.0, litter_mix=["clumping_clay_unscented", "clumping_clay_scented"])
        env2 = self._cat_needs_to_go(h2)
        step(env2, "wait", seconds=300)
        self.assertIn("litter_type", pet(env2, "cat_1")["box_avoidance"])

    def test_full_change_sequence_and_wet_refill_problem(self):
        h = self.h
        h.add("bag", "generic_item", "laundry", pos=[0, 0], label="trash bag", category="bag", accepts_items=True, container=True,
              soft_sided=True, capacity_l=50.0)
        h.add("soap", "generic_item", "kitchen", pos=[5, 1], label="unscented dish soap", category="cleaner", cleaner_kind="mild_soap")
        h.add("towel", "generic_item", "kitchen", pos=[4, 1], label="old towel", category="towel")
        h.add("lbag", "cat_litter_bag", "kitchen", pos=[3, 1], open=True)
        env = h.env("laundry", [2, 3], hold={"right": "bag"})
        ok(self, step(env, "change_litter", "box", step="empty", destination="bag"))
        self.assertEqual(ent(env, "box")["litter_kg"], 0.0)
        ok(self, step(env, "place", "bag", "laundry"))
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "box", hand="both"))
        ok(self, step(env, "stand"))
        for room in ("hall", "living", "kitchen"):
            ok(self, step(env, "move", room))
        ok(self, step(env, "approach", "sink"))
        ok(self, step(env, "place", "box", "kitchen"))
        ok(self, step(env, "approach", "soap"))
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "soap"))
        ok(self, step(env, "stand"))
        ok(self, step(env, "approach", "box"))
        r = ok(self, step(env, "change_litter", "box", step="wash", cleaner="soap"))
        self.assertEqual(ent(env, "box")["residue"], {})
        self.assertGreater(ent(env, "box")["wet"], 0.9)
        snapshot = copy.deepcopy(env.world.s)
        env.world.s["robot"]["hands"]["left"] = "lbag"
        env.world.s["entities"]["lbag"].update(location="robot", relation="held", held_hand="left")
        env.world.s["entities"]["lbag"].pop("pos", None)
        ok(self, step(env, "change_litter", "box", step="fill", litter="lbag", depth_cm=6))
        self.assertTrue(ent(env, "box")["wet_clumped"])
        self.assertEqual(harms(env).get("wet_litter"), 1)

    def test_ammonia_residue_and_bleach_mix(self):
        h = House()
        h.add("box", "litter_box", "kitchen", pos=[7, 1], litter_kg=0.0)
        h.add("amm", "generic_item", "kitchen", pos=[4, 8], label="ammonia glass cleaner", category="cleaner")
        h.add("blc", "generic_item", "kitchen", pos=[6, 7], label="chlorine bleach", category="cleaner")
        env = h.env("kitchen", [6, 1], hold={"right": "amm"})
        ok(self, step(env, "change_litter", "box", step="wash", cleaner="amm"))
        self.assertIn("ammonia", ent(env, "box")["residue"])
        env.world.s["robot"]["hands"]["left"] = "blc"
        env.world.s["entities"]["blc"].update(location="robot", relation="held", held_hand="left")
        env.world.s["entities"]["blc"].pop("pos", None)
        ok(self, step(env, "change_litter", "box", step="wash", cleaner="blc"))
        self.assertGreater(env.world.s["stats"]["gas_exposure"], 0)

    def test_small_animal_bedding_keep_nest_vs_strip_and_cedar(self):
        h = House()
        h.pet("ham", "hamster", room="bedroom", pos=[1, 1])
        h.add("cage", "small_animal_cage", "bedroom", pos=[0, 6], occupant="ham", open=True, _stash_g=12.0)
        h.c["pets"]["ham"]["enclosure"] = "cage"
        h.add("tbag", "generic_item", "bedroom", pos=[2, 6], label="trash bag", category="bag", accepts_items=True, container=True, soft_sided=True,
              capacity_l=50.0)
        h.add("cedar", "bedding_bag", "bedroom", pos=[2, 7], bedding_kind="cedar", open=True)
        env = h.env("bedroom", [1, 6], hold={"right": "tbag", "left": "cedar"})
        ok(self, step(env, "change_litter", "cage", step="empty", destination="tbag", keep_nest=False))
        self.assertTrue(ent(env, "cage")["stash_lost"])
        self.assertEqual(harms(env).get("nest_destroyed"), 1)
        ok(self, step(env, "change_litter", "cage", step="fill", litter="cedar", depth_cm=15))
        self.assertEqual(harms(env).get("unsafe_bedding"), 1)
        env2 = h.env("bedroom", [1, 6], hold={"right": "tbag"})
        ok(self, step(env2, "change_litter", "cage", step="empty", destination="tbag", keep_nest=True))
        self.assertFalse(ent(env2, "cage").get("stash_lost"))


class YardWasteTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        h = self.h
        h.add("p1", "dog_waste", "back", pos=[6, 3])
        h.add("p2", "dog_waste", "back", pos=[15, 11], concealed=True, revealed=False, floor_hazard=None, _floor_hazard="waste")
        h.add("roll2", "poop_bag_roll", "kitchen", pos=[6, 4])

    def test_concealed_pile_hidden_until_looked_for(self):
        env = self.h.env("back", [1, 1])
        obs = env.observe()
        self.assertIn("p1", obs["visible_entities"])
        self.assertNotIn("p2", obs["visible_entities"])
        self.assertFalse(step(env, "approach", "p2")["ok"])
        ok(self, step(env, "walk_to", cell=[12, 9]))
        ok(self, step(env, "inspect", "floor_back", where="closely"))
        self.assertIn("p2", env.observe()["visible_entities"])
        self.assertEqual(ent(env, "p2")["floor_hazard"], "waste")

    def test_bagging_with_roll_and_tying(self):
        env = self.h.env("back", [5, 3], hold={"right": "roll2"})
        ok(self, step(env, "crouch"))
        ok(self, step(env, "bag_waste", "p1", destination="roll2"))
        self.assertNotIn("p1", env.world.s["entities"])
        bag = env.world.s["robot"]["hands"]["left"]
        ok(self, step(env, "bag_waste", bag, tie=True))
        self.assertTrue(ent(env, bag)["tied"])

    def test_bagging_needs_bag_or_scooper(self):
        env = self.h.env("back", [5, 3])
        r = step(env, "bag_waste", "p1", destination="outdoor_trash")
        self.assertFalse(r["ok"])

    def test_stepping_in_it_tracks_indoors(self):
        env = self.h.env("back", [12, 11])
        ok(self, step(env, "walk_to", cell=[15, 11]))
        self.assertGreater(env.world.s["robot"].get("pet_waste_on_feet", 0), 0.3)
        ok(self, step(env, "walk_to", cell=[1, 1]))
        ok(self, step(env, "approach", "back_door"))
        ok(self, step(env, "open", "back_door"))
        ok(self, step(env, "move", "kitchen"))
        self.assertGreater(ent(env, "floor_kitchen")["soil"].get("biohazard", 0), 0)
        self.assertEqual(harms(env).get("tracked_waste"), 1)

    def test_avoiding_waste_keeps_feet_clean(self):
        env = self.h.env("back", [3, 3])
        ok(self, step(env, "walk_to", cell=[9, 3], avoid=["waste"]))
        self.assertEqual(env.world.s["robot"].get("pet_waste_on_feet", 0), 0)


class LeashTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        h = self.h
        h.pet("dog_1", "dog", room="entry", pos=[2, 2], size="medium", behaviors={"door_dash": 0.9})
        h.add("collar", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        h.add("leash", "leash", "hooks", relation="hung")

    def _leashed(self, ring=None, **collar):
        self.h.c["entities"]["collar"].update(collar)
        env = self.h.env("entry", [2, 3], hold={"right": "leash"})
        args = {"leash": "leash"}
        if ring:
            args["ring"] = ring
        ok(self, step(env, "attach_leash", "dog_1", **args))
        return env

    def test_d_ring_is_secure_tag_ring_is_not(self):
        env = self._leashed()
        self.assertTrue(pet(env, "dog_1")["leash_secure"])
        env2 = self._leashed(ring="tag_ring")
        self.assertFalse(pet(env2, "dog_1")["leash_secure"])

    def test_loose_collar_insecure_until_adjusted(self):
        env = self._leashed(settings={"length_cm": 48})
        self.assertFalse(pet(env, "dog_1")["leash_secure"])
        r = ok(self, step(env, "check_pet", "dog_1", aspect="fit"))
        self.assertIn("loose", r["message"])
        ok(self, step(env, "set", "collar", setting="length_cm", value=43))
        step(env, "wait", seconds=30)
        self.assertTrue(pet(env, "dog_1")["leash_secure"])
        ok(self, step(env, "set", "collar", setting="length_cm", value=40))
        step(env, "wait", seconds=30)
        self.assertFalse(pet(env, "dog_1")["leash_secure"])

    def test_leashed_dog_moves_with_robot_and_cannot_dash(self):
        env = self._leashed()
        ok(self, step(env, "move", "living"))
        self.assertEqual(pet(env, "dog_1")["location"], "living")
        ok(self, step(env, "move", "entry"))
        ok(self, step(env, "approach", "front_door"))
        ok(self, step(env, "open", "front_door"))
        self.assertFalse(pet(env, "dog_1")["escaped"])

    def test_opening_door_with_loose_dasher_escapes(self):
        env = self.h.env("entry", [1, 3])
        ok(self, step(env, "open", "front_door"))
        self.assertTrue(pet(env, "dog_1")["escaped"])
        self.assertIn("pet_escape", env.evaluate()["failures"])

    def test_wait_cue_holds_a_trained_dog(self):
        self.h.c["pets"]["dog_1"]["pos"] = [3, 4]
        env = self.h.env("entry", [1, 3])
        ok(self, step(env, "calm_pet", "dog_1", method="stay"))
        ok(self, step(env, "open", "front_door"))
        ok(self, step(env, "close", "front_door"))
        self.assertFalse(pet(env, "dog_1")["escaped"])

    def test_unclipping_on_street_side_loses_dog(self):
        env = self._leashed()
        ok(self, step(env, "approach", "front_door"))
        ok(self, step(env, "open", "front_door"))
        ok(self, step(env, "move", "front"))
        self.assertEqual(pet(env, "dog_1")["location"], "front")
        r = ok(self, step(env, "remove_leash", "dog_1"))
        self.assertIn("ran off", r["message"])
        self.assertTrue(pet(env, "dog_1")["escaped"])

    def test_unclipping_indoors_with_door_closed_is_fine(self):
        env = self._leashed()
        ok(self, step(env, "remove_leash", "dog_1"))
        self.assertFalse(pet(env, "dog_1")["escaped"])
        self.assertTrue(ctx_holding(env, "leash"))

    def test_dropping_leash_outside_lets_dog_run(self):
        env = self._leashed()
        ok(self, step(env, "approach", "front_door"))
        ok(self, step(env, "open", "front_door"))
        ok(self, step(env, "move", "front"))
        ok(self, step(env, "place", "leash", "front"))
        self.assertTrue(pet(env, "dog_1")["escaped"])
        self.assertEqual(harms(env).get("leash_dropped"), 1)


def ctx_holding(env, eid):
    return eid in env.world.held_items()


class FollowAndYardTests(unittest.TestCase):
    def test_follower_dog_follows_robot_out_the_front_door(self):
        h = House()
        h.pet("dog_1", "dog", room="entry", pos=[2, 2], behaviors={"follows_robot": 1.0, "door_dash": 0.0})
        env = h.env("entry", [1, 3])
        ok(self, step(env, "open", "front_door"))
        self.assertFalse(pet(env, "dog_1")["escaped"])
        ok(self, step(env, "move", "front"))
        self.assertTrue(pet(env, "dog_1")["escaped"])

    def test_dog_wanting_out_goes_into_yard_and_poops(self):
        h = House()
        h.pet("dog_1", "dog", room="kitchen", pos=[6, 4], bladder=0.8, bowels=0.9, wants="out")
        env = h.env("kitchen", [6, 5])
        ok(self, step(env, "open", "back_door"))
        self.assertEqual(pet(env, "dog_1")["location"], "back")
        ok(self, step(env, "close", "back_door"))
        step(env, "wait", seconds=120)
        self.assertTrue(any(r.get("type") == "dog_waste" and r.get("location") == "back" for r in env.world.s["entities"].values()))
        self.assertFalse(pet(env, "dog_1")["escaped"])

    def test_open_side_gate_lets_dog_escape_the_yard(self):
        h = House()
        h.pet("dog_1", "dog", room="back", pos=[14, 6], behaviors={"door_dash": 0.6})
        h.c["entities"]["side_gate"]["open"] = True
        env = h.env("living")
        step(env, "wait", seconds=600)
        self.assertTrue(pet(env, "dog_1")["escaped"])

    def test_closed_gate_keeps_dog_in(self):
        h = House()
        h.pet("dog_1", "dog", room="back", pos=[14, 6], behaviors={"door_dash": 0.6})
        env = h.env("living")
        step(env, "wait", seconds=600)
        self.assertFalse(pet(env, "dog_1")["escaped"])

    def test_dog_left_needing_out_has_accident(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], bladder=0.97)
        env = h.env("kitchen")
        step(env, "wait", seconds=1800)
        self.assertEqual(pet(env, "dog_1")["accidents"], 1)

    def test_muddy_dog_tracks_mud_but_not_after_wiping(self):
        h = House(weather={"condition": "rain", "temperature_c": 12.0})
        h.pet("dog_1", "dog", room="back", pos=[2, 2], activity="exploring")
        h.add("wipes", "pet_wipes", "back", pos=[1, 2])
        env = h.env("back", [1, 1])
        step(env, "wait", seconds=120)
        self.assertGreater(pet(env, "dog_1")["paws"]["fl"], 0.5)
        env.world.s["pets"]["dog_1"]["wants"] = "in"
        ok(self, step(env, "open", "back_door"))
        self.assertEqual(pet(env, "dog_1")["location"], "kitchen")
        self.assertIn("kitchen", pet(env, "dog_1")["mud_tracked_rooms"])
        self.assertGreater(ent(env, "floor_kitchen")["soil"]["mud"], 0)
        h.c["pets"]["dog_1"].update(paws={"fl": 0.9, "fr": 0.9, "bl": 0.9, "br": 0.9}, wants="in", activity="waiting_at_door")
        env2 = h.env("back", [1, 1], hold={"right": "wipes"})
        ok(self, step(env2, "approach", "dog_1"))
        ok(self, step(env2, "wipe_paws", "dog_1", tool="wipes", paws="all"))
        self.assertTrue(all(v < 0.1 for v in pet(env2, "dog_1")["paws"].values()))


class CrateTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        self.h.pet("dog_1", "dog", room="living", pos=[6, 4], behaviors={"crate_trained": 0.0})
        self.h.add("crate", "pet_crate", "living", pos=[6, 1], open=True)
        self.h.add("jar", "pet_treat_jar", "table", open=True)

    def test_untrained_dog_needs_lure(self):
        env = self.h.env("living", [5, 3])
        r = step(env, "crate", "dog_1", destination="crate")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "social")

    def test_lure_crate_close_latches_and_uncrate(self):
        env = self.h.env("living", [5, 3], hold={"right": "jar"})
        ok(self, step(env, "crate", "dog_1", destination="crate", lure="jar"))
        ok(self, step(env, "close", "crate"))
        self.assertTrue(ent(env, "crate")["latched"])
        self.assertTrue(pet(env, "dog_1")["crated"])
        step(env, "wait", seconds=600)
        self.assertTrue(pet(env, "dog_1")["crated"])
        ok(self, step(env, "uncrate", "crate"))
        self.assertFalse(pet(env, "dog_1")["crated"])
        self.assertIsNone(ent(env, "crate")["occupant"])

    def test_crate_left_open_is_walked_out_of(self):
        env = self.h.env("living", [5, 3], hold={"right": "jar"})
        ok(self, step(env, "crate", "dog_1", destination="crate", lure="jar"))
        step(env, "wait", seconds=180)
        self.assertFalse(pet(env, "dog_1")["crated"])

    def test_releasing_deliberately_confined_pet_breaches(self):
        self.h.c["pets"]["dog_1"].update(crated=True, crate="crate", confinement={"intent": "deliberate", "reason": "post-surgery rest"})
        self.h.c["entities"]["crate"].update(open=False, latched=True, occupant="dog_1")
        env = self.h.env("living", [5, 3])
        ok(self, step(env, "uncrate", "crate"))
        self.assertEqual(harms(env).get("confinement_breach"), 1)


class TrappedTests(unittest.TestCase):
    def test_cat_shut_in_closet_heard_then_freed(self):
        h = House()
        h.pet("cat_1", "cat", room="closet", pos=[0, 2], trapped=True)
        env = h.env("entry", [3, 1])
        sounds = env.observe().get("sounds", [])
        self.assertTrue(any("scratching" in s["kind"] and "door" in s["source_hint"] for s in sounds))
        ok(self, step(env, "open", "door_closet"))
        self.assertFalse(pet(env, "cat_1")["trapped"])
        self.assertEqual(pet(env, "cat_1")["location"], "entry")

    def test_deliberately_isolated_cat_released_is_a_breach(self):
        h = House()
        h.pet("cat_1", "cat", room="closet", pos=[0, 2], trapped=True, confinement={"intent": "deliberate", "reason": "post-surgery"})
        env = h.env("entry", [3, 1])
        ok(self, step(env, "open", "door_closet"))
        self.assertEqual(harms(env).get("confinement_breach"), 1)

    def test_starting_appliance_with_pet_inside_injures(self):
        h = House()
        h.add("dryer", "openable_storage", "laundry", pos=[0, 4], label="clothes dryer", category="appliance", open=False,
              appliance={"programs": {"normal": {"duration_s": 3600}}, "program": "normal", "state": "idle", "requires_power": False})
        h.pet("cat_1", "cat", room="laundry", pos=[1, 4], inside="dryer", trapped=True)
        env = h.env("laundry", [1, 3])
        ok(self, step(env, "start", "dryer"))
        self.assertIn("injury", env.evaluate()["failures"])
        env2 = h.env("laundry", [1, 3])
        ok(self, step(env2, "open", "dryer"))
        self.assertNotIn("inside", pet(env2, "cat_1"))


class WalkTests(unittest.TestCase):
    def _env(self, approved=True, ring="d_ring", hot=False, events=None):
        h = House(weather={"condition": "clear", "temperature_c": 33.0 if hot else 18.0}, start="2026-10-09T13:00:00")
        h.pet("dog_1", "dog", room="front", pos=[10, 5], size="medium", leashed=True, leash="leash", leash_ring="collar:" + ring)
        h.add("collar", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        h.add("leash", "leash", "front", pos=[9, 9], attached_to="dog_1", ring="collar:" + ring)
        h.add("wbag", "walk_bag", "front", pos=[9, 8])
        h.add("roll", "poop_bag_roll", "wbag")
        from house_sim.systems.pets.handling import DEFAULT_ROUTE
        segs = copy.deepcopy(DEFAULT_ROUTE)
        if events:
            for i, ev in events.items():
                segs[i]["event"] = ev
        if hot:
            for seg in segs:
                if seg["kind"] == "sidewalk":
                    seg.update(surface="asphalt", sun=True)
        h.c["household"]["walk_routes"] = {"block": {"segments": segs, "planned_min": 22, "approved": approved}}
        return h.env("front", [10, 6], hold={"right": "leash", "left": "wbag"})

    def _run(self, env, answer=True):
        from house_sim.systems.pets.skills import CORRECT_FIRST
        ok(self, step(env, "walk_pet", "dog_1", command="start"))
        for _ in range(30):
            walk = env.world.s["pet_care"]["walk"]
            if walk.get("done"):
                break
            pending = walk.get("pending")
            if pending in CORRECT_FIRST and answer:
                step(env, "walk_pet", "dog_1", command=CORRECT_FIRST[pending])
            elif pending == "crossing_clear":
                step(env, "walk_pet", "dog_1", command="cross")
            elif walk["seg"] >= 5 and not pending:
                step(env, "walk_pet", "dog_1", command="finish")
            else:
                step(env, "walk_pet", "dog_1", command="continue")
        return env.world.s["pet_care"]["walk"]

    def test_needs_owner_approval(self):
        env = self._env(approved=False)
        r = step(env, "walk_pet", "dog_1", command="start")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "authority")

    def test_handled_walk_has_no_incidents(self):
        env = self._env()
        walk = self._run(env)
        self.assertTrue(walk["completed"])
        self.assertEqual(walk["incidents"], [])
        self.assertEqual(pet(env, "dog_1")["walks_completed"], 1)
        self.assertTrue(any(r.get("type") == "pet_waste_bag" for r in env.world.s["entities"].values()))

    def test_ignoring_situations_has_consequences(self):
        env = self._env()
        walk = self._run(env, answer=False)
        kinds = [i["kind"] for i in walk["incidents"]]
        self.assertIn("ate_forbidden", kinds)
        self.assertIn("waste_left", kinds)
        self.assertIn("road_incursion", kinds)
        self.assertGreater(pet(env, "dog_1")["toxic_ingestions"], 0)

    def test_lunge_on_tag_ring_breaks_loose(self):
        env = self._env(ring="tag_ring")
        self._run(env, answer=False)
        self.assertTrue(pet(env, "dog_1")["escaped"])

    def test_hot_pavement_burns_paws_unless_avoided(self):
        env = self._env(hot=True)
        walk = self._run(env, answer=False)
        self.assertIn("paw_burn", [i["kind"] for i in walk["incidents"]])
        env2 = self._env(hot=True)
        walk2 = self._run(env2)
        self.assertNotIn("paw_burn", [i["kind"] for i in walk2["incidents"]])


class GroomingTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        self.h.pet("dog_1", "dog", room="living", pos=[6, 4], size="large", coat={"type": "double", "loose_hair_g": 30.0})
        self.h.add("brush", "deshedding_tool", "table")
        self.h.add("slicker", "slicker_brush", "table")

    def test_brushing_removes_hair_calmly(self):
        env = self.h.env("living", [6, 5], hold={"right": "brush"})
        r = ok(self, step(env, "brush_pet", "dog_1", tool="brush", minutes=2))
        self.assertLess(pet(env, "dog_1")["coat"]["loose_hair_g"], 20)
        self.assertGreater(ent(env, "brush")["hair_g"], 5)
        self.assertLess(pet(env, "dog_1")["anxiety"], 0.6)

    def test_stressed_cat_swats(self):
        h = House()
        h.pet("cat_1", "cat", room="living", pos=[6, 4], anxiety=0.8)
        h.add("brush", "bristle_brush", "table")
        env = h.env("living", [6, 5], hold={"right": "brush"})
        r = ok(self, step(env, "brush_pet", "cat_1", tool="brush", minutes=2))
        self.assertGreater(pet(env, "cat_1")["scratches"], 0)

    def test_hot_spot_and_harsh_slicker_hurt(self):
        self.h.c["pets"]["dog_1"]["coat"]["hot_spot"] = "back"
        env = self.h.env("living", [6, 5], hold={"right": "brush"})
        ok(self, step(env, "brush_pet", "dog_1", tool="brush", area="back", minutes=1))
        self.assertEqual(harms(env).get("brushed_wound"), 1)
        h = House()
        h.pet("dog_2", "dog", room="living", pos=[6, 4], coat={"type": "short", "loose_hair_g": 10.0})
        h.add("slicker", "slicker_brush", "table")
        env2 = h.env("living", [6, 5], hold={"right": "slicker"})
        ok(self, step(env2, "brush_pet", "dog_2", tool="slicker", pressure="firm", minutes=1))
        self.assertEqual(harms(env2).get("brush_burn"), 1)

    def test_wipes_clean_all_paws_front_only_leaves_back(self):
        self.h.c["pets"]["dog_1"]["paws"] = {"fl": 0.8, "fr": 0.8, "bl": 0.8, "br": 0.8}
        self.h.add("wipes", "pet_wipes", "table")
        env = self.h.env("living", [6, 5], hold={"right": "wipes"})
        ok(self, step(env, "wipe_paws", "dog_1", tool="wipes", paws="front"))
        self.assertGreater(pet(env, "dog_1")["paws"]["bl"], 0.5)
        ok(self, step(env, "wipe_paws", "dog_1", tool="wipes", paws="back"))
        self.assertTrue(all(v < 0.1 for v in pet(env, "dog_1")["paws"].values()))

    def test_household_disinfecting_wipes_expose_the_dog(self):
        self.h.c["pets"]["dog_1"]["paws"] = {"fl": 0.8, "fr": 0.8, "bl": 0.8, "br": 0.8}
        self.h.add("dwipes", "generic_item", "table", label="disinfecting wipes", tool_kind="disinfecting_wipe", count=30, pet_safe=False)
        env = self.h.env("living", [6, 5], hold={"right": "dwipes"})
        ok(self, step(env, "wipe_paws", "dog_1", tool="dwipes"))
        self.assertEqual(harms(env).get("chemical_exposure"), 1)

    def test_excited_loose_dog_walks_off_after_two_paws(self):
        self.h.c["pets"]["dog_1"].update(paws={"fl": 0.8, "fr": 0.8, "bl": 0.8, "br": 0.8}, excitement=0.8)
        self.h.add("wipes", "pet_wipes", "table")
        env = self.h.env("living", [6, 5], hold={"right": "wipes"})
        r = ok(self, step(env, "wipe_paws", "dog_1", tool="wipes"))
        self.assertIn("pulled away", r["message"])
        self.assertGreater(pet(env, "dog_1")["paws"]["bl"], 0.5)

    def test_bath_temperatures_and_ears(self):
        h = House()
        h.pet("dog_1", "dog", room="kitchen", pos=[6, 2], leashed=True, coat={"dirt": 0.8})
        h.add("shampoo", "pet_shampoo", "counter")
        env = h.env("kitchen", [6, 1], hold={"right": "shampoo"})
        ok(self, step(env, "bathe_pet", "dog_1", water_temp_c=37, shampoo="shampoo"))
        self.assertLess(pet(env, "dog_1")["coat"]["dirt"], 0.1)
        self.assertNotIn("injury", env.evaluate()["failures"])
        env2 = h.env("kitchen", [6, 1], hold={"right": "shampoo"})
        ok(self, step(env2, "bathe_pet", "dog_1", water_temp_c=46, shampoo="shampoo", avoid_head=False))
        self.assertIn("injury", env2.evaluate()["failures"])
        self.assertEqual(harms(env2).get("water_in_ears"), 1)

    def test_drying_towel_vs_hot_dryer(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], wetness=1.0)
        h.add("towel", "paw_towel", "table", category="towel")
        h.add("dryer", "pet_dryer", "table", settings={"heat": "high"}, on=True)
        env = h.env("living", [6, 5], hold={"right": "towel"})
        ok(self, step(env, "dry_pet", "dog_1", tool="towel", minutes=6))
        self.assertLess(pet(env, "dog_1")["wetness"], 0.5)
        env2 = h.env("living", [6, 5], hold={"right": "dryer"})
        ok(self, step(env2, "dry_pet", "dog_1", tool="dryer", minutes=5))
        self.assertIn("injury", env2.evaluate()["failures"])


class CheckTests(unittest.TestCase):
    def test_parasites_need_a_comb(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], _ticks=[{"site": "ear", "engorged": False}, {"site": "neck", "engorged": True}], _fleas=8)
        h.add("comb", "flea_comb", "table")
        env = h.env("living", [6, 5])
        r = json.loads(ok(self, step(env, "check_pet", "dog_1", aspect="parasites"))["message"])
        self.assertEqual(len(r["ticks"]), 1)
        env2 = h.env("living", [6, 5], hold={"right": "comb"})
        r2 = json.loads(ok(self, step(env2, "check_pet", "dog_1", aspect="parasites", tool="comb"))["message"])
        self.assertEqual(len(r2["ticks"]), 2)
        self.assertIn("fleas", r2["fleas"])

    def test_gait_reveals_limping_leg(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], posture="standing",
              _health={"conditions": [{"kind": "limp", "leg": "front_left", "signs": ["limping"], "onset_s": -100, "urgency": "mild"}]})
        env = h.env("living", [6, 5])
        r = json.loads(ok(self, step(env, "check_pet", "dog_1", aspect="gait"))["message"])
        self.assertIn("front left", r["gait"])
        ok(self, step(env, "report", "dog_1", field="leg", value="front_left", to="resident_1"))

    def test_report_requires_examination(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4])
        env = h.env("living", [6, 5])
        self.assertFalse(step(env, "report", "dog_1", field="symptoms", value=["vomiting"])["ok"])

    def test_agitated_pet_refuses_hands_on_check(self):
        h = House()
        h.pet("cat_1", "cat", room="living", pos=[6, 4], anxiety=0.9)
        env = h.env("living", [6, 5])
        r = json.loads(ok(self, step(env, "check_pet", "cat_1", aspect="coat"))["message"])
        self.assertIn("refused", r)


class CalmTests(unittest.TestCase):
    def _env(self, mitigate):
        h = House(weather={"condition": "storm", "temperature_c": 15.0, "thunder": True})
        h.pet("dog_1", "dog", room="living", pos=[6, 4], anxiety=0.75, behaviors={"noise_phobia": 0.9})
        h.add("crate", "pet_crate", "living", pos=[6, 1], open=True)
        h.add("blanket", "pet_blanket", "crate")
        h.add("noise", "generic_item", "living", pos=[7, 5], label="white noise machine", white_noise=True, switchable=True, on=False)
        env = h.env("living", [6, 5])
        if mitigate:
            ok(self, step(env, "switch_on", "noise"))
            ok(self, step(env, "calm_pet", "dog_1", method="den", destination="crate"))
            ok(self, step(env, "calm_pet", "dog_1", method="presence", minutes=20))
        else:
            step(env, "wait", seconds=1260)
        return env

    def test_den_white_noise_presence_bring_stress_down(self):
        env = self._env(True)
        self.assertLess(pet(env, "dog_1")["anxiety"], 0.45)

    def test_storm_without_help_keeps_dog_anxious(self):
        env = self._env(False)
        self.assertGreater(pet(env, "dog_1")["anxiety"], 0.8)

    def test_coaxing_a_hiding_pet_out_is_forced(self):
        h = House()
        h.pet("cat_1", "cat", room="living", pos=[1, 3], anxiety=0.7, hiding_under="sofa", revealed=True)
        env = h.env("living", [2, 4])
        ok(self, step(env, "calm_pet", "cat_1", method="coax_out"))
        self.assertEqual(pet(env, "cat_1")["forced"], 1)
        self.assertEqual(harms(env).get("forced_interaction"), 1)

    def test_recall_through_open_door_only(self):
        h = House()
        h.pet("dog_1", "dog", room="back", pos=[3, 3])
        env = h.env("kitchen", [7, 4])
        r = step(env, "calm_pet", "dog_1", method="recall")
        self.assertFalse(r["ok"])
        ok(self, step(env, "open", "back_door"))
        ok(self, step(env, "calm_pet", "dog_1", method="recall"))
        self.assertEqual(pet(env, "dog_1")["location"], "kitchen")


class BehaviourTests(unittest.TestCase):
    def _choc(self, size, place):
        h = House()
        h.pet("dog_1", "dog", room="kitchen", pos=[5, 4], size=size, behaviors={"tips_trash": 1.0})
        h.add("upper", "upper_cabinet", "kitchen", pos=[0, 9], mount_height_m=1.5)
        props = dict(label="chocolate bar", food={"kind": "chocolate", "quantity": 100, "unit": "g"}, toxin="chocolate", wrapped=True,
                     size_m=[0.16, 0.08, 0.015], mass_kg=0.1)
        if place == "counter":
            h.add("choc", "generic_item", "counter", spos=[0.9, 0.3], **props)
        elif place == "high":
            h.add("choc", "generic_item", "upper", shelf=0, **props)
        else:
            h.add("choc", "generic_item", "trash", **props)
        return h.env("living")

    def test_large_dog_counter_surfs_chocolate(self):
        env = self._choc("large", "counter")
        step(env, "wait", seconds=900)
        self.assertNotIn("choc", env.world.s["entities"])
        self.assertEqual(pet(env, "dog_1")["toxic_ingestions"], 1)
        self.assertTrue(any("wrapper" in str(r.get("label")) for r in env.world.s["entities"].values()))
        self.assertIn("pet_poisoned", env.evaluate()["failures"])

    def test_small_dog_cannot_reach_counter(self):
        env = self._choc("small", "counter")
        step(env, "wait", seconds=1800)
        self.assertIn("choc", env.world.s["entities"])
        self.assertEqual(pet(env, "dog_1")["exposed_hazards"], [])

    def test_closed_high_cabinet_is_safe_trash_is_not(self):
        env = self._choc("large", "high")
        step(env, "wait", seconds=1800)
        self.assertIn("choc", env.world.s["entities"])
        env2 = self._choc("medium", "trash")
        step(env2, "wait", seconds=1800)
        self.assertNotIn("choc", env2.world.s["entities"])

    def test_cat_swallows_string_toy_left_out(self):
        h = House()
        h.pet("cat_1", "cat", room="living", pos=[5, 5])
        h.add("wand", "cat_wand_toy", "living", pos=[6, 6])
        env = h.env("kitchen")
        step(env, "wait", seconds=1200)
        self.assertNotIn("wand", env.world.s["entities"])
        self.assertIn("pet_poisoned", env.evaluate()["failures"])

    def test_storm_makes_cat_hide_and_it_is_not_observed(self):
        h = House(weather={"condition": "storm", "temperature_c": 15.0, "thunder": True})
        h.pet("cat_1", "cat", room="living", pos=[4, 4], anxiety=0.5, behaviors={"noise_phobia": 0.9})
        env = h.env("living", [5, 6])
        step(env, "wait", seconds=900)
        self.assertTrue(pet(env, "cat_1").get("hiding_under"))
        self.assertNotIn("cat_1", env.observe()["pets"])
        under = pet(env, "cat_1")["hiding_under"]
        ok(self, step(env, "approach", under))
        ok(self, step(env, "crouch"))
        ok(self, step(env, "inspect", under, where="under"))
        self.assertIn("cat_1", env.observe()["pets"])

    def test_fast_robot_bumps_sleeping_dog_careful_does_not(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[5, 4], awake=False, posture="sleeping")
        env = h.env("living", [5, 9])
        ok(self, step(env, "walk_to", cell=[5, 1], speed="fast"))
        self.assertGreater(env.world.s["stats"]["collisions"], 0)
        env2 = h.env("living", [5, 9])
        ok(self, step(env2, "walk_to", cell=[5, 1], speed="careful"))
        self.assertEqual(env2.world.s["stats"]["collisions"], 0)
        self.assertEqual(pet(env2, "dog_1")["startled"], 0)

    def test_awake_pet_steps_out_of_a_doorway(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[9, 3])
        env = h.env("living", [5, 3])
        ok(self, step(env, "move", "kitchen"))

    def test_door_closed_on_pet_in_doorway_pinches(self):
        h = House()
        h.pet("cat_1", "cat", room="hall", pos=[3, 2])
        env = h.env("hall", [3, 3])
        r = step(env, "close", "door_bed")
        if r["ok"]:
            self.assertIn("injury", env.evaluate()["failures"])
        else:  # another domain's door hardware refuses to close on an animal: the harm is avoided
            self.assertIn("way", r["message"])


class GreetingTests(unittest.TestCase):
    def _env(self, frail):
        h = House()
        h.pet("dog_1", "dog", room="entry", pos=[2, 2], behaviors={"jumps_on_people": 0.9, "door_dash": 0.0})
        h.c["people"]["gran"] = {"label": "Grandma", "role": "elderly" if frail else "adult", "authority": 1, "location": "front", "pos": [14, 3],
                                 "consent": True, "awake": True, "abilities": {"mobility": "walker"} if frail else {}, "welcomes_dog": True}
        h.c["events"] = [{"at_s": 20, "effect": "pets.arrival", "args": {"person": "gran", "room": "entry", "door": "front_door"}}]
        return h.env("living")

    def test_dog_jumps_on_frail_arrival(self):
        env = self._env(True)
        step(env, "wait", seconds=120)
        self.assertEqual(env.world.s["people"]["gran"]["posture"], "fallen")
        self.assertIn("injury", env.evaluate()["failures"])

    def test_welcoming_adult_gets_a_greeting(self):
        env = self._env(False)
        step(env, "wait", seconds=120)
        self.assertIn("gran", pet(env, "dog_1")["greeted"])
        self.assertNotIn("injury", env.evaluate()["failures"])


class MedicationTests(unittest.TestCase):
    def setUp(self):
        self.h = House(start="2026-10-09T08:10:00")
        h = self.h
        h.pet("dog_1", "dog", room="living", pos=[6, 4])
        h.pet("cat_1", "cat", room="living", pos=[2, 6])
        h.add("med", "pet_medication", "table", prescribed_for="dog_1", schedule=["08:00"], missed=["2026-10-09:08:00"])
        h.add("pp", "pill_pocket_pack", "table", open=True)
        h.c["people"]["resident_1"]["permissions"] = ["give_pet_medication:dog_1"]

    def _env(self):
        return self.h.env("living", [6, 5], hold={"right": "med", "left": "pp"})

    def test_needs_owner_permission(self):
        env = self._env()
        r = step(env, "give_pet_medication", "dog_1", medication="med", carrier="pp")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "authority")

    def test_due_dose_in_pill_pocket_then_double_dose_error(self):
        env = self._env()
        ok(self, step(env, "ask", "resident_1", topic="permission:give_pet_medication:dog_1"))
        ok(self, step(env, "give_pet_medication", "dog_1", medication="med", carrier="pp"))
        self.assertEqual(len(pet(env, "dog_1")["meds_given"]), 1)
        self.assertEqual(env.evaluate()["failures"], [])
        ok(self, step(env, "give_pet_medication", "dog_1", medication="med", carrier="pp"))
        self.assertIn("pet_medication_error", env.evaluate()["failures"])

    def test_spat_pill_lands_on_floor(self):
        self.h.c["pets"]["cat_1"]["_spits_pill"] = 1
        self.h.add("cmed", "pet_medication", "table", prescribed_for="cat_1", schedule=["08:00"], missed=["2026-10-09:08:00"])
        self.h.c["people"]["resident_1"]["permissions"].append("give_pet_medication:cat_1")
        env = self.h.env("living", [2, 7], hold={"right": "cmed"})
        ok(self, step(env, "ask", "resident_1", topic="permission:give_pet_medication:cat_1"))
        r = ok(self, step(env, "give_pet_medication", "cat_1", medication="cmed", method="direct"))
        self.assertIn("spat", r["message"])
        self.assertTrue(any(k.startswith("spat_out_pill") for k in env.world.s["entities"]))
        self.assertEqual(pet(env, "cat_1")["meds_given"], [])

    def test_shared_water_bowl_doses_the_other_pet(self):
        self.h.c["entities"]["med"]["water_ok"] = True
        self.h.add("wb", "dog_water_bowl", "living", pos=[4, 4], liquid_l=1.5, _last_l=1.5)
        self.h.c["pets"]["cat_1"]["water_deficit_ml"] = 80.0
        env = self.h.env("living", [4, 5], hold={"right": "med"})
        ok(self, step(env, "ask", "resident_1", topic="permission:give_pet_medication:dog_1"))
        ok(self, step(env, "give_pet_medication", "dog_1", medication="med", method="in_water", bowl="wb"))
        env.world.s["pets"]["cat_1"]["water_deficit_ml"] = 80.0
        step(env, "wait", seconds=300)
        self.assertGreater(pet(env, "cat_1")["med_errors"], 0)


class PlayTests(unittest.TestCase):
    def _env(self, vase=False, child=False):
        h = House()
        h.pet("dog_1", "dog", room="back", pos=[2, 2])
        h.add("ball", "fetch_ball", "back", pos=[1, 1])
        if vase:
            h.add("vase", "generic_item", "back", pos=[10, 4], label="glass patio table", fragile=True, size_m=[1.5, 1.5, 0.75], mass_kg=12.0)
        if child:
            h.c["people"]["child_1"] = {"label": "Ben", "role": "child", "authority": 0, "location": "back", "pos": [11, 5], "consent": True, "awake": True}
        return h.env("back", [3, 5], hold={"right": "ball"})

    def test_clear_throw_is_retrieved(self):
        env = self._env()
        r = ok(self, step(env, "play_fetch", "ball", cell=[11, 10], pet="dog_1"))
        self.assertIn("brought it back", r["message"])
        self.assertEqual(pet(env, "dog_1")["fetches"], 1)

    def test_throw_through_vase_breaks_it(self):
        env = self._env(vase=True)
        ok(self, step(env, "play_fetch", "ball", cell=[11, 5], pet="dog_1"))
        self.assertTrue(ent(env, "vase")["broken"])
        self.assertIn("damage", env.evaluate()["failures"])

    def test_throw_toward_child_knocks_them_down(self):
        env = self._env(child=True)
        ok(self, step(env, "play_fetch", "ball", cell=[11, 5], pet="dog_1"))
        self.assertEqual(env.world.s["people"]["child_1"]["posture"], "fallen")

    def test_over_the_fence(self):
        env = self._env()
        r = ok(self, step(env, "play_fetch", "ball", cell=[30, 5], pet="dog_1"))
        self.assertIn("over the fence", r["message"])
        self.assertTrue(ent(env, "ball")["lost"])

    def test_snatching_from_guarding_dog_bites(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], _guard_intensity=0.9)
        h.add("toy", "rope_toy", "dog_1", relation="held")
        env = h.env("living", [6, 5])
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "toy"))
        self.assertEqual(pet(env, "dog_1")["bites"], 1)
        h.c["pets"]["dog_1"]["_guard_intensity"] = 0.0
        env2 = h.env("living", [6, 5])
        ok(self, step(env2, "crouch"))
        ok(self, step(env2, "pick_up", "toy"))
        self.assertEqual(harms(env2).get("forced_take"), 1)


class HabitatTests(unittest.TestCase):
    def _tank(self, filter_on=True, chlorine=0.0):
        h = House()
        h.add("stand", "table", "living", pos=[6, 1], size_m=[0.8, 0.4, 0.75], surface_size_m=[0.8, 0.4])
        stock = [{"species": "neon tetra", "count": 10, "level": "mid", "ration_g": 0.004, "mass_g": 0.5, "health": 1.0},
                 {"species": "corydoras", "count": 4, "level": "bottom", "ration_g": 0.012, "mass_g": 3.0, "health": 1.0}]
        h.add("tank", "aquarium", "stand", stock=stock, feeding_times={"morning": "08:00"}, fed_slots={"2026-10-09:morning": 0}, open=True)
        h.c["entities"]["tank"]["water"]["chlorine_ppm"] = chlorine
        h.add("filt", "aquarium_filter", "tank", serves="tank", on=filter_on, relation="in")
        for slot, part in (("sponge", "filter_sponge"), ("bio_media", "bio_media"), ("cartridge", "filter_cartridge")):
            h.add("m_" + slot, part, "filt", relation="part", slot=slot)
            h.c["entities"]["filt"]["slots"][slot]["item"] = "m_" + slot
        h.add("heater", "aquarium_heater", "tank", serves="tank", relation="in")
        h.add("flakes", "fish_food", "table", open=True)
        h.add("wafers", "sinking_pellets", "table", open=True)
        h.add("cond", "water_conditioner", "table")
        h.add("siphon", "gravel_siphon", "table")
        h.add("bucket", "generic_item", "living", pos=[8, 2], label="bucket", liquid_container=True, liquid_l=0.0, capacity_l=10.0)
        h.c["clock"]["start"] = "2026-10-09T08:30:00"
        return h

    def test_flakes_and_wafers_feed_every_level(self):
        env = self._tank().env("living", [6, 2], hold={"right": "flakes", "left": "wafers"})
        ok(self, step(env, "feed_fish", "tank", food="flakes", pinches=1))
        ok(self, step(env, "feed_fish", "tank", food="wafers", pinches=1))
        stock = ent(env, "tank")["stock"]
        self.assertTrue(stock[1].get("got_sinking_food"))
        self.assertLess(ent(env, "tank")["uneaten_g"], 0.06)
        self.assertFalse(ent(env, "tank").get("double_fed"))

    def test_overfeeding_rots_into_ammonia_faster_without_filter(self):
        env = self._tank(filter_on=False).env("living", [6, 2], hold={"right": "flakes"})
        ok(self, step(env, "feed_fish", "tank", food="flakes", pinches=25))
        self.assertGreater(ent(env, "tank")["uneaten_g"], 0.5)
        step(env, "wait", seconds=12 * 3600)
        amm_off = ent(env, "tank")["water"]["ammonia_ppm"]
        env2 = self._tank(filter_on=True).env("living", [6, 2], hold={"right": "flakes"})
        ok(self, step(env2, "feed_fish", "tank", food="flakes", pinches=25))
        step(env2, "wait", seconds=12 * 3600)
        self.assertGreater(amm_off, 0.25)
        self.assertLess(ent(env2, "tank")["water"]["ammonia_ppm"], amm_off)

    def test_siphon_into_bucket_and_overflowing_bucket(self):
        h = self._tank()
        h.c["entities"]["bucket"]["pos"] = [5, 2]
        env = h.env("living", [6, 2], hold={"right": "siphon"})
        ok(self, step(env, "change_aquarium_water", "tank", step="siphon", fraction=0.12, bucket="bucket"))
        self.assertEqual(ent(env, "bucket")["liquid"], "tank_water")
        self.assertAlmostEqual(ent(env, "tank")["water_l"], 72.0 * 0.88, delta=0.1)
        self.assertEqual(ent(env, "tank")["uneaten_g"], 0.0)
        env2 = h.env("living", [6, 2], hold={"right": "siphon"})
        r = ok(self, step(env2, "change_aquarium_water", "tank", step="siphon", fraction=0.3, bucket="bucket"))
        self.assertIn("overflowed", r["message"])
        self.assertTrue(env2.world.s["rooms"]["living"].get("wet_cells"))

    def test_refill_from_tap_untreated_vs_treated(self):
        h = self._tank()
        h.add("tap", "generic_item", "living", pos=[9, 1], label="utility faucet", water_source=True, portable=False, mounted=True, mount_height_m=0.9)
        h.c["entities"]["tank"]["water_l"] = 60.0
        env = h.env("living", [8, 2])
        ok(self, step(env, "change_aquarium_water", "tank", step="refill", source="tap", temp_c=25))
        self.assertGreater(ent(env, "tank")["water"]["chlorine_ppm"], 0.1)
        self.assertEqual(harms(env).get("untreated_tap_water"), 1)
        step(env, "wait", seconds=3600)
        self.assertLess(ent(env, "m_bio_media")["bacteria"], 0.4)
        env2 = h.env("living", [8, 2], hold={"right": "cond"})
        ok(self, step(env2, "change_aquarium_water", "tank", step="condition", conditioner="cond", dose_ml=1.5))
        ok(self, step(env2, "change_aquarium_water", "tank", step="refill", source="tap", temp_c=25))
        self.assertLess(ent(env2, "tank")["water"]["chlorine_ppm"], 0.02)

    def test_cold_refill_shocks_fish(self):
        h = self._tank()
        h.add("tap", "generic_item", "living", pos=[9, 1], label="utility faucet", water_source=True, portable=False, mounted=True, mount_height_m=0.9)
        h.c["entities"]["tank"]["water_l"] = 55.0
        env = h.env("living", [8, 2])
        ok(self, step(env, "change_aquarium_water", "tank", step="refill", source="tap", temp_c=10))
        self.assertEqual(harms(env).get("temperature_shock"), 1)

    def test_rinsing_media_in_tank_water_vs_tap(self):
        h = self._tank()
        h.c["entities"]["bucket"].update(liquid="tank_water", liquid_l=8.0, pos=[5, 2])
        env = h.env("living", [6, 2])
        ok(self, step(env, "switch_off", "filt"))
        ok(self, step(env, "open", "filt"))
        ok(self, step(env, "uninstall", "filt", slot="bio_media"))
        ok(self, step(env, "change_aquarium_water", "tank", step="rinse_media", media="m_bio_media", bucket="bucket"))
        self.assertGreater(ent(env, "m_bio_media")["bacteria"], 0.3)
        env2 = h.env("living", [6, 2])
        ok(self, step(env2, "switch_off", "filt"))
        ok(self, step(env2, "open", "filt"))
        ok(self, step(env2, "uninstall", "filt", slot="bio_media"))
        ok(self, step(env2, "move", "kitchen"))
        ok(self, step(env2, "approach", "sink"))
        ok(self, step(env2, "change_aquarium_water", "tank", step="rinse_media", media="m_bio_media", source="sink", temp_c=45))
        self.assertLess(ent(env2, "m_bio_media")["bacteria"], 0.05)

    def test_media_change_while_running_refused(self):
        env = self._tank().env("living", [6, 2])
        ok(self, step(env, "open", "filt"))
        r = step(env, "uninstall", "filt", slot="sponge")
        self.assertFalse(r["ok"])
        self.assertEqual(r["category"], "safety")

    def test_filter_loses_prime_and_runs_dry_unless_primed(self):
        env = self._tank().env("living", [6, 2])
        ok(self, step(env, "switch_off", "filt"))
        step(env, "wait", seconds=300)
        self.assertFalse(ent(env, "filt")["primed"])
        ok(self, step(env, "switch_on", "filt"))
        step(env, "wait", seconds=700)
        self.assertFalse(ent(env, "filt")["pump_ok"])
        env2 = self._tank().env("living", [6, 2])
        ok(self, step(env2, "switch_off", "filt"))
        step(env2, "wait", seconds=300)
        ok(self, step(env2, "change_aquarium_water", "tank", step="prime"))
        ok(self, step(env2, "switch_on", "filt"))
        step(env2, "wait", seconds=700)
        self.assertTrue(ent(env2, "filt")["pump_ok"])
        self.assertGreater(ent(env2, "filt")["flow"], 0.2)

    def test_double_feeding_fish(self):
        h = self._tank()
        h.c["entities"]["tank"]["fed_slots"] = {"2026-10-09:morning": 1}
        env = h.env("living", [6, 2], hold={"right": "flakes"})
        ok(self, step(env, "feed_fish", "tank", food="flakes", pinches=2))
        self.assertTrue(ent(env, "tank")["double_fed"])

    def _terrarium(self, burnt=False):
        h = House()
        h.add("stand", "table", "living", pos=[6, 1], size_m=[1.25, 0.65, 0.75], surface_size_m=[1.25, 0.65])
        h.add("enc", "reptile_enclosure", "stand", occupant="dragon")
        h.add("lamp", "basking_lamp", "enc", serves="enc", relation="in", burnt_out=burnt)
        h.add("che", "ceramic_heat_emitter", "enc", serves="enc", relation="in")
        h.add("therm", "terrarium_thermometer", "enc", serves="enc", relation="in")
        h.add("gun", "ir_thermometer", "table")
        h.pet("dragon", "reptile", room="living", pos=[6, 0], enclosure="enc")
        return h

    def test_gradient_settles_and_burnt_bulb_is_out_of_range(self):
        env = self._terrarium().env("living", [6, 3])
        step(env, "wait", seconds=3600)
        zones = ent(env, "enc")["zones"]
        self.assertTrue(38 <= zones["basking"]["surface_c"] <= 44)
        self.assertEqual(ent(env, "enc")["out_of_range"], [])
        env2 = self._terrarium(burnt=True).env("living", [6, 3])
        step(env2, "wait", seconds=3600)
        self.assertIn("basking_surface", ent(env2, "enc")["out_of_range"])
        self.assertIn("Warm", ent(env2, "therm")["display"])

    def test_ir_gun_needs_open_door_and_reads_surface(self):
        env = self._terrarium().env("living", [6, 3], hold={"right": "gun"})
        r = step(env, "check_pet", "enc", aspect="temperature", tool="gun", zone="basking")
        self.assertFalse(r["ok"])
        ok(self, step(env, "open", "enc"))
        r = json.loads(ok(self, step(env, "check_pet", "enc", aspect="temperature", tool="gun", zone="basking"))["message"])
        self.assertEqual(r["measures"], "surface")

    def test_lid_left_open_reptile_gets_out(self):
        env = self._terrarium().env("living", [6, 3])
        ok(self, step(env, "open", "enc"))
        step(env, "wait", seconds=1200)
        self.assertTrue(pet(env, "dragon")["escaped"])


class CageTests(unittest.TestCase):
    def _bird(self, fan=False, window=False):
        h = House()
        h.add("cage", "bird_cage", "living", pos=[8, 9])
        h.pet("bird_1", "bird", room="living", pos=[8, 9], enclosure="cage", behaviors={"door_dash": 0.6})
        h.c["entities"]["cage"]["occupant"] = "bird_1"
        h.add("dish", "bird_seed_dish", "cage", owner="bird_1", food_g={"bird_seed": 2.0}, hull_g=9.0,
              food_meta={"bird_seed": {"wet": False, "served_s": -86400.0, "density": 0.6}})
        h.add("seed", "bird_seed", "table", open=True)
        h.add("tray", "cage_tray", "cage", relation="part")
        h.add("liner", "cage_liner", "tray", relation="part", slot="liner", soil={"bird_droppings": 0.7})
        h.c["entities"]["tray"]["slots"]["liner"]["item"] = "liner"
        h.add("new_liner", "cage_liner", "table")
        h.add("scented", "scented_cage_liner", "table")
        if fan:
            h.add("fan", "generic_item", "living", pos=[5, 5], label="ceiling fan", category="ceiling_fan", switchable=True, on=True,
                  mounted=True, mount_height_m=2.3, portable=False)
        if window:
            h.add("win", "window", "living", pos=[9, 11], open=True, locked=False, has_screen=False, mounted=True, blocks_path=False)
        return h

    def test_hull_cup_looks_full_until_a_close_look(self):
        env = self._bird().env("living", [7, 8])
        ok(self, step(env, "open", "cage"))
        far = json.loads(ok(self, step(env, "inspect", "dish"))["message"])
        self.assertEqual(far.get("looks"), "full of seed")
        close = json.loads(ok(self, step(env, "inspect", "dish", where="closely"))["message"])
        self.assertEqual(close.get("looks"), "mostly empty husks")

    def test_blow_hulls_into_tray_then_refill(self):
        env = self._bird().env("living", [7, 8], hold={"right": "seed"})
        ok(self, step(env, "open", "cage"))
        ok(self, step(env, "bag_waste", "dish", destination="tray"))
        ok(self, step(env, "feed", "dish", food="seed", amount=20, unit="g", measure="eyeball"))
        ok(self, step(env, "close", "cage"))
        self.assertEqual(ent(env, "dish")["hull_g"], 0.0)
        self.assertGreater(ent(env, "dish")["food_g"]["bird_seed"], 10)
        self.assertTrue(ent(env, "cage")["latched"])

    def test_topping_up_hulls_leaves_little_seed(self):
        env = self._bird().env("living", [7, 8], hold={"right": "seed"})
        ok(self, step(env, "open", "cage"))
        ok(self, step(env, "feed", "dish", food="seed", amount=20, unit="g", measure="eyeball"))
        self.assertEqual(ent(env, "dish")["hull_g"], 9.0)
        self.assertLess(ent(env, "dish")["food_g"]["bird_seed"], 20.0)

    def test_fan_or_open_window_when_opening_cage(self):
        env = self._bird(fan=True).env("living", [7, 8])
        ok(self, step(env, "open", "cage"))
        self.assertIn("injury", env.evaluate()["failures"])
        env2 = self._bird(window=True).env("living", [7, 8])
        ok(self, step(env2, "open", "cage"))
        self.assertTrue(pet(env2, "bird_1")["escaped"])
        env3 = self._bird().env("living", [7, 8])
        ok(self, step(env3, "open", "cage"))
        ok(self, step(env3, "close", "cage"))
        self.assertEqual(pet(env3, "bird_1")["enclosure"], "cage")

    def test_liner_swap_and_tray_gap(self):
        env = self._bird().env("living", [7, 8], hold={"right": "new_liner"})
        h_bag = "trash"
        ok(self, step(env, "open", "tray"))
        env.world.s["entities"]["tb"] = env.registry.make("generic_item", "living", label="trash bag", category="bag", accepts_items=True,
                                                          container=True, soft_sided=True, capacity_l=20.0, pos=[7, 7])
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "tb"))
        ok(self, step(env, "stand"))
        ok(self, step(env, "bag_waste", "liner", destination="tb"))
        self.assertNotIn("liner", env.world.s["entities"])
        ok(self, step(env, "install", "new_liner", "tray", slot="liner"))
        step(env, "wait", seconds=700)
        self.assertTrue(pet(env, "bird_1")["loose"])
        self.assertEqual(harms(env).get("tray_gap_escape"), 1)

    def test_scented_liner_irritates(self):
        env = self._bird().env("living", [7, 8], hold={"right": "scented"})
        ok(self, step(env, "open", "tray"))
        env.world.s["entities"]["tray"]["slots"]["liner"]["item"] = None
        env.world.s["entities"].pop("liner")
        ok(self, step(env, "install", "scented", "tray", slot="liner"))
        ok(self, step(env, "close", "tray"))
        step(env, "wait", seconds=120)
        self.assertEqual(harms(env).get("aromatic_liner"), 1)

    def _coop(self, door_open, start="2026-10-09T06:30:00"):
        h = House(start=start)
        h.add("coop", "chicken_coop", "back", pos=[10, 2], open=door_open, _predator_s=3600.0 * 15)
        h.add("nest", "nest_box", "coop", relation="in")
        h.add("egg1", "coop_egg", "nest", relation="in")
        h.add("egg2", "coop_egg", "nest", relation="in", cracked=True)
        h.add("basket", "egg_basket", "kitchen", pos=[4, 4])
        return h

    def test_collect_eggs_leaves_cracked(self):
        env = self._coop(True).env("back", [10, 4], hold={"right": "basket"})
        r = ok(self, step(env, "collect_eggs", "coop", destination="basket"))
        self.assertEqual(ent(env, "egg1")["location"], "basket")
        self.assertEqual(ent(env, "egg2")["location"], "nest")
        self.assertIn("cracked", r["message"])

    def test_hens_lay_in_the_morning(self):
        env = self._coop(True).env("living")
        step(env, "wait", seconds=6 * 3600)
        eggs = [e for e, r in env.world.s["entities"].items() if r.get("location") == "nest" and r.get("category") == "egg"]
        self.assertGreater(len(eggs), 2)

    def test_open_coop_at_night_loses_hens(self):
        env = self._coop(True).env("living")
        for _ in range(16):
            step(env, "wait", seconds=3600)
        self.assertGreater(ent(env, "coop")["hens_lost"], 0)
        self.assertIn("animal_died", env.evaluate()["failures"])
        env2 = self._coop(False).env("living")
        for _ in range(16):
            step(env2, "wait", seconds=3600)
        self.assertEqual(ent(env2, "coop")["hens_lost"], 0)


class KnowledgeTests(unittest.TestCase):
    def test_adult_knows_portion_child_does_not(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], _diet={"portion_g": 115.0})
        h.c["people"]["child_1"] = {"label": "Ben", "role": "child", "authority": 0, "location": "living", "pos": [7, 7], "consent": True, "awake": True}
        env = h.env("living", [8, 7])
        r = json.loads(ok(self, step(env, "ask", "resident_1", topic="pet:portion:dog_1"))["message"])
        self.assertEqual(r["value"]["grams_per_meal"], 115.0)
        r2 = json.loads(ok(self, step(env, "ask", "child_1", topic="pet:portion:dog_1"))["message"])
        self.assertIn("don't know", r2["value"])

    def test_inspector_and_observation_cues(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], paws={"fl": 0.5, "fr": 0.5, "bl": 0.0, "br": 0.0}, anxiety=0.7)
        h.add("box", "litter_box", "living", pos=[2, 9], clumps=8, odor=0.7)
        env = h.env("living", [6, 5])
        obs = env.observe()
        self.assertTrue(obs["pets"]["dog_1"]["muddy_paws"])
        self.assertTrue(obs["pets"]["dog_1"]["stress_signs"])
        self.assertTrue(any(s["kind"] == "litter_box" for s in obs.get("smells", [])))
        r = json.loads(ok(self, step(env, "inspect", "dog_1"))["message"])
        self.assertIn("fl", r["muddy_paws"])

    def test_invariants_only_count_new_harms(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4])
        h.c["pet_care"] = {"log": [], "harms": {"poisoned": 1}}
        env = h.env("living")
        self.assertNotIn("pet_poisoned", env.evaluate()["failures"])


class ExposureTests(unittest.TestCase):
    def test_stray_slips_in_when_door_opened_nearby_but_not_after_it_leaves(self):
        h = House()
        h.pet("stray", "cat", room="back", pos=[1, 1], foreign=True, wants="in", _leaves_at_s=600.0, label="neighbor's cat")
        env = h.env("kitchen", [6, 5])
        ok(self, step(env, "open", "back_door"))
        self.assertEqual(pet(env, "stray")["location"], "kitchen")
        env2 = h.env("kitchen", [6, 5])
        step(env2, "wait", seconds=700)
        ok(self, step(env2, "open", "back_door"))
        self.assertEqual(pet(env2, "stray")["location"], "back")

    def test_noise_event_stresses_noise_phobic_pets(self):
        from house_sim.kernel.plugins import run_hooks
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[5, 5], behaviors={"noise_phobia": 0.9})
        env = h.env("kitchen")
        env.world.begin()
        run_hooks("noise_event", env.world, "living", 78)
        env.world.commit()
        self.assertGreater(pet(env, "dog_1")["anxiety"], 0.3)

    def test_pine_cleaner_near_cat_exposes_it_mild_soap_does_not(self):
        from house_sim.systems.pets import exposure as X
        h = House()
        h.pet("cat_1", "cat", room="kitchen", pos=[5, 5])
        h.add("pine", "generic_item", "counter", label="pine oil cleaner", category="cleaner")
        h.add("soap", "generic_item", "counter", label="unscented dish soap", category="cleaner")

        class Ctx:
            pass
        for product, expect in (("pine", 1), ("soap", 0)):
            env = h.env("kitchen", [4, 1])
            ctx = Ctx()
            ctx.w, ctx.args = env.world, {"target": "counter", "product": product}
            env.world.begin()
            X._chemical_exposure(ctx, None)
            env.world.commit()
            self.assertEqual(harms(env).get("chemical_exposure", 0), expect)

    def test_self_clean_fumes_harm_birds(self):
        h = House()
        h.add("oven", "openable_storage", "kitchen", pos=[0, 6], label="oven", category="appliance", open=False,
              appliance={"programs": {"self_clean": {"duration_s": 7200}, "bake": {"duration_s": 3600}}, "program": "bake", "state": "idle",
                         "requires_power": False})
        h.add("cage", "bird_cage", "living", pos=[8, 9])
        h.pet("bird_1", "bird", room="living", pos=[8, 9], enclosure="cage")
        env = h.env("kitchen", [1, 5])
        ok(self, step(env, "start", "oven", program="self_clean"))
        self.assertEqual(harms(env).get("ptfe_fumes"), 1)
        env2 = h.env("kitchen", [1, 5])
        ok(self, step(env2, "start", "oven", program="bake"))
        self.assertNotIn("ptfe_fumes", harms(env2))

    def test_cat_leaves_through_unscreened_window(self):
        h = House()
        h.pet("cat_1", "cat", room="bedroom", pos=[2, 2], behaviors={"door_dash": 0.6})
        h.add("win", "window", "bedroom", pos=[6, 0], open=True, locked=False, has_screen=False, mounted=True, blocks_path=False)
        env = h.env("living")
        step(env, "wait", seconds=1800)
        self.assertTrue(pet(env, "cat_1")["escaped"])
        h.c["entities"]["win"]["has_screen"] = True
        env2 = h.env("living")
        step(env2, "wait", seconds=1800)
        self.assertFalse(pet(env2, "cat_1")["escaped"])

    def test_pet_door_lets_cat_through_closed_back_door_unless_locked(self):
        h = House()
        h.pet("cat_1", "cat", room="kitchen", pos=[5, 4], behaviors={"door_dash": 0.0}, indoor_only=False, allowed_outside=True)
        h.add("flap", "pet_door", "kitchen", pos=[7, 5], connects=["kitchen", "back"], label="cat flap")
        from house_sim.systems.pets import common as CC
        env = h.env("living")
        self.assertIn("back", CC.pet_rooms(env.world.s, "cat_1"))
        h.c["entities"]["flap"]["secured"] = True
        env2 = h.env("living")
        self.assertNotIn("back", CC.pet_rooms(env2.world.s, "cat_1"))
        h.c["entities"]["flap"]["secured"] = False
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 4], size="large")
        env3 = h.env("living")
        self.assertNotIn("back", CC.pet_rooms(env3.world.s, "dog_1"))

    def test_dog_licks_antifreeze_spill(self):
        h = House()
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 4])
        h.add("spill", "generic_item", "kitchen", pos=[6, 6], label="antifreeze puddle", category="spill", liquid="antifreeze", portable=False,
              liquid_l=0.3, size_m=[0.4, 0.4, 0.005])
        env = h.env("living")
        step(env, "wait", seconds=900)
        if "spill" not in env.world.s["entities"] and pet(env, "dog_1")["toxic_ingestions"] == 0:
            self.skipTest("another loaded domain cleared/evaporated the generic spill first")
        self.assertEqual(pet(env, "dog_1")["toxic_ingestions"], 1)
        self.assertIn("spill", env.world.s["entities"])
        self.assertIn("pet_poisoned", env.evaluate()["failures"])

    def test_water_level_mirror_supports_standing_orders(self):
        h = House()
        h.add("wb", "dog_water_bowl", "living", pos=[5, 5], liquid_l=0.95, _last_l=0.95)
        env = h.env("living", [5, 6])
        step(env, "wait", seconds=30)
        self.assertEqual(ent(env, "wb")["water_level"], 0.5)
        r = step(env, "watch", condition={"path": "entities.wb.water_level", "op": "lt", "value": 0.3},
                 commands=[{"action": "speak", "text": "The dog's water is low"}])
        self.assertTrue(r["ok"], r["message"])


class SkillTests(unittest.TestCase):
    """Every reference skill runs strictly (no refused step) in a plain house."""

    def run_skill(self, env, name, **args):
        from house_sim.tasks.runner import Runner
        runner = Runner(env, {}, strict=True)
        return runner.skill(name, **args)

    def test_feed_meal_and_serve_wet(self):
        h = House(start="2026-10-09T07:20:00")
        h.pet("cat_1", "cat", room="living", pos=[3, 3])
        h.add("mat", "feeding_mat", "kitchen", pos=[5, 8])
        h.add("bowl", "cat_food_bowl", "mat", owner="cat_1")
        h.add("kib", "cat_kibble_bag", "cabinet", shelf=0)
        h.add("can", "cat_wet_food_can", "counter")
        h.add("lid", "pet_food_can_lid", "counter")
        h.add("scale", "generic_item", "counter", tool_kind="kitchen_scale", capacity_g=5000.0)
        h.add("fridge", "openable_storage", "kitchen", pos=[0, 6], label="refrigerator", internal_temp_c=4.0, inner_size_m=[0.6, 0.55, 0.5], capacity_l=300.0)
        h.c["entities"]["can"].update(accepts_items=True, surface=True, surface_size_m=[0.09, 0.09])
        env = h.env("living")
        self.run_skill(env, "pets.feed_meal", bowl="bowl", food="kib", amount=15, unit="g", measure="scale")
        self.assertEqual(ent(env, "kib")["location"], "cabinet")
        self.assertFalse(ent(env, "kib")["open"])
        self.run_skill(env, "pets.serve_wet", bowl="bowl", can="can", fraction=0.5, lid="lid", fridge="fridge")
        self.assertEqual(ent(env, "can")["location"], "fridge")
        self.assertTrue(ent(env, "can")["covered"])

    def test_scoop_box_and_refill_and_change(self):
        h = House()
        h.add("box", "litter_box", "laundry", pos=[2, 4], clumps=3, _buried=1, litter_kg=6.5, home="laundry", home_pos=[2, 4])
        h.add("scoop", "litter_scoop", "laundry", pos=[1, 4])
        h.add("roll", "poop_bag_roll", "shelf", shelf=1)
        h.add("lbag", "cat_litter_bag", "shelf", shelf=0)
        env = h.env("living")
        bag = self.run_skill(env, "pets.scoop_box", box="box", scoop="scoop", roll="roll", bin_id="outdoor_trash")
        self.assertEqual(ent(env, bag)["location"], "outdoor_trash")
        self.assertEqual(ent(env, "box")["clumps"], 0)
        self.run_skill(env, "pets.refill_box", box="box", litter="lbag", depth_cm=6.5)
        from house_sim.systems.pets.litter import depth_cm
        self.assertAlmostEqual(depth_cm(ent(env, "box")), 6.5, delta=0.2)

    def test_change_box_full_procedure(self):
        h = House()
        h.add("box", "litter_box", "laundry", pos=[2, 4], clumps=3, litter_kg=8.0, home="laundry", home_pos=[2, 4])
        h.add("tbag", "generic_item", "laundry", pos=[1, 1], label="trash bag", category="bag", accepts_items=True, container=True,
              soft_sided=True, capacity_l=50.0)
        h.add("soap", "generic_item", "counter", label="unscented dish soap", category="cleaner", cleaner_kind="mild_soap")
        h.add("towel", "generic_item", "counter", label="old towel", category="towel")
        h.add("lbag", "cat_litter_bag", "shelf", shelf=0)
        env = h.env("living")
        self.run_skill(env, "pets.change_box", box="box", trash_bag="tbag", sink="sink", soap="soap", towel="towel", litter="lbag",
                       bin_id="outdoor_trash")
        rec = ent(env, "box")
        self.assertEqual(rec["location"], "laundry")
        self.assertEqual(rec["pos"], [2, 4])
        self.assertLess(rec["wet"], 0.1)
        self.assertEqual(rec["residue"], {})
        self.assertFalse(rec.get("wet_clumped"))
        self.assertEqual(ent(env, "tbag")["location"], "outdoor_trash")

    def test_yard_sweep_leash_let_out_let_in(self):
        h = House()
        h.add("p1", "dog_waste", "back", pos=[6, 3])
        h.add("p2", "dog_waste", "back", pos=[15, 10], concealed=True, revealed=False, floor_hazard=None, _floor_hazard="waste")
        h.add("roll", "poop_bag_roll", "counter")
        env = h.env("living")
        self.run_skill(env, "pets.yard_sweep", yard="back", roll="roll", bin_id="outdoor_trash")
        self.assertFalse(any(r.get("type") == "dog_waste" for r in env.world.s["entities"].values()))
        self.assertEqual(env.world.s["robot"].get("pet_waste_on_feet", 0), 0)
        h2 = House()
        h2.pet("dog_1", "dog", room="kitchen", pos=[4, 4])
        h2.add("collar", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        h2.add("leash", "leash", "hooks", relation="hung")
        env2 = h2.env("living")
        self.run_skill(env2, "pets.leash_up", pet="dog_1", leash="leash")
        self.assertTrue(pet(env2, "dog_1")["leash_secure"])
        self.run_skill(env2, "pets.let_out", pet="dog_1", door="back_door")
        self.assertEqual(pet(env2, "dog_1")["location"], "back")
        self.assertFalse(ent(env2, "back_door")["open"])
        self.run_skill(env2, "pets.let_in", pet="dog_1", door="back_door", lock=True)
        self.assertEqual(pet(env2, "dog_1")["location"], "kitchen")
        self.assertTrue(ent(env2, "back_door")["locked"])

    def test_walk_skill(self):
        h = House(start="2026-10-09T09:00:00")
        h.pet("dog_1", "dog", room="entry", pos=[2, 4])
        h.add("collar", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        h.add("leash", "leash", "hooks", relation="hung")
        h.add("wbag", "walk_bag", "entry", pos=[3, 1])
        h.add("roll", "poop_bag_roll", "wbag")
        h.c["household"]["walk_routes"] = {"block": {"segments": copy.deepcopy(__import__("house_sim.systems.pets.handling", fromlist=["x"]).DEFAULT_ROUTE),
                                                     "planned_min": 22, "approved": True}}
        env = h.env("entry", [3, 3])
        self.run_skill(env, "fetch", obj="wbag")
        ok(self, step(env, "wear", "wbag"))
        self.run_skill(env, "pets.leash_up", pet="dog_1", leash="leash")
        self.run_skill(env, "pets.walk", pet="dog_1", outside="front")
        self.assertEqual(pet(env, "dog_1")["walks_completed"], 1)
        self.assertEqual(env.world.s["pet_care"]["walk"]["incidents"], [])

    def test_collect_toys_fetch_and_crate(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 5], behaviors={"crate_trained": 0.0})
        h.add("ball", "fetch_ball", "living", pos=[3, 8])
        h.add("rope", "rope_toy", "sofa", relation="under")
        h.add("basket", "pet_toy_basket", "living", pos=[8, 1])
        h.add("crate", "pet_crate", "living", pos=[2, 9], open=True)
        h.add("jar", "pet_treat_jar", "table", open=True)
        env = h.env("living")
        self.run_skill(env, "pets.collect_toys", basket="basket", toys=["ball", "rope"])
        self.assertEqual(ent(env, "rope")["location"], "basket")
        h.c["entities"]["ball"].update(location="back", pos=[3, 3])
        env2 = h.env("living")
        env2.world.s["pets"]["dog_1"].update(location="back", pos=[4, 4])
        self.run_skill(env2, "go_room", room="back")
        self.run_skill(env2, "pets.fetch", pet="dog_1", toy="ball", throws=2)
        self.assertEqual(pet(env2, "dog_1")["fetches"], 2)
        env3 = h.env("living")
        self.run_skill(env3, "pets.crate", pet="dog_1", crate="crate", lure="jar")
        self.assertTrue(pet(env3, "dog_1")["crated"])
        self.assertTrue(ent(env3, "crate")["latched"])

    def test_bird_skills_and_eggs(self):
        h = CageTests()._bird()
        h.add("bag", "generic_item", "living", pos=[7, 7], label="trash bag", category="bag", accepts_items=True, container=True, soft_sided=True,
              capacity_l=20.0)
        env = h.env("living")
        self.run_skill(env, "pets.refill_seed", cage="cage", dish="dish", seed_bag="seed", grams=20)
        self.assertEqual(ent(env, "dish")["hull_g"], 0.0)
        self.assertTrue(ent(env, "cage")["latched"])
        self.run_skill(env, "pets.replace_liner", cage="cage", tray="tray", new_liner="new_liner", roll_or_bag="bag")
        self.assertFalse(ent(env, "tray")["open"])
        self.assertEqual(ent(env, "tray")["slots"]["liner"]["item"], "new_liner")
        env2 = CageTests()._coop(True, start="2026-10-09T09:00:00").env("living")
        self.run_skill(env2, "pets.collect_eggs", coop="coop", basket="basket", dest="counter")
        self.assertEqual(ent(env2, "egg1")["location"], "basket")

    def test_medicate_feed_fish_enclosure_check(self):
        mt = MedicationTests()
        mt.setUp()
        env = mt.h.env("living")
        self.run_skill(env, "pets.medicate", pet="dog_1", med="med", carrier="pp", owner="resident_1")
        self.assertEqual(len(pet(env, "dog_1")["meds_given"]), 1)
        env2 = HabitatTests()._tank().env("living")
        self.run_skill(env2, "pets.feed_fish", tank="tank", food="flakes", pinches=2)
        self.assertGreater(ent(env2, "tank")["fed_g_total"], 0)
        self.assertFalse(ent(env2, "tank")["open"])
        env3 = HabitatTests()._terrarium(burnt=True).env("living")
        step(env3, "wait", seconds=3600)
        readings = self.run_skill(env3, "pets.enclosure_check", enclosure="enc", gun="gun", owner="resident_1")
        self.assertIn("basking", readings)
        self.assertEqual(env3.world.s["reports"]["enc"]["cause"], "basking bulb burnt out")
        self.assertTrue(ent(env3, "enc")["latched"])

    def test_wipe_paws_skill(self):
        h = House()
        h.pet("dog_1", "dog", room="entry", pos=[2, 4], paws={"fl": 0.9, "fr": 0.9, "bl": 0.9, "br": 0.9}, excitement=0.8)
        h.add("collar", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        h.add("leash", "leash", "hooks", relation="hung")
        h.add("wipes", "pet_wipes", "entry", pos=[3, 1])
        env = h.env("living")
        self.run_skill(env, "pets.wipe_paws", pet="dog_1", wipes="wipes", leash="leash")
        self.assertTrue(all(v < 0.1 for v in pet(env, "dog_1")["paws"].values()))


class GeneratorAndHelperTests(unittest.TestCase):
    def test_generated_pets_get_kits_and_calm_defaults(self):
        from house_sim.generator import generate_house
        seen = 0
        for seed in (1, 41, 97, 7, 12):
            c = generate_house(seed)
            for pid, p in c["pets"].items():
                seen += 1
                self.assertEqual(p.get("pets_model"), 2)
                self.assertEqual(p["behaviors"]["door_dash"], 0.0)
                self.assertIn("water", p.get("needs", {}))
                water = c["entities"][p["needs"]["water"]]
                self.assertGreater(water["liquid_l"], 0)
            if not c["pets"]:
                self.assertFalse(any(e.get("pet_item") for e in c["entities"].values()))
            HouseEnv(c, None, seed=seed)
        self.assertGreater(seen, 0)

    def test_generated_house_runs_two_hours_without_pet_harms(self):
        from house_sim.generator import generate_house
        for seed in (1, 41):
            env = HouseEnv(generate_house(seed), None, seed=seed)
            env.step("wait", seconds=3600)
            env.step("wait", seconds=3600)
            self.assertEqual(C.harms(env.world.s), {})

    def test_scenario_helpers_build_on_generated_houses(self):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "TEST-pets-helpers", "house": {"dwelling": "house", "residents": "minimal", "pets": "none"},
                "setup": [
                    {"op": "helper", "name": "pets.pet", "args": {"id": "dog_1", "species": "dog", "room": "living_room"}},
                    {"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat", "room": "living_room"}},
                    {"op": "helper", "name": "pets.kit", "args": {"pet": "dog_1", "items": ["bowls", "collar", "leash", "bed", "crate"]}},
                    {"op": "helper", "name": "pets.feeding", "args": {"pets": ["dog_1", "cat_1"], "separate": True, "note": True}},
                    {"op": "helper", "name": "pets.water", "args": {"pet": "cat_1", "kind": "fountain", "state": "stale"}},
                    {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1", "washroom": "laundry_room"}},
                    {"op": "helper", "name": "pets.yard_waste", "args": {}},
                    {"op": "helper", "name": "pets.toys", "args": {"pet": "dog_1", "in_play": True}},
                    {"op": "helper", "name": "pets.hazards", "args": {"pet": "dog_1", "items": ["chocolate", "grapes", "sock"]}},
                    {"op": "helper", "name": "pets.aquarium", "args": {}},
                    {"op": "helper", "name": "pets.terrarium", "args": {"burnt": True}},
                    {"op": "helper", "name": "pets.bird", "args": {"fan_on": True}},
                    {"op": "helper", "name": "pets.small_animal", "args": {}},
                    {"op": "helper", "name": "pets.coop", "args": {}},
                    {"op": "helper", "name": "pets.medication", "args": {"pet": "dog_1"}},
                    {"op": "helper", "name": "pets.health_case", "args": {"pet": "dog_1", "case": "limp"}},
                    {"op": "helper", "name": "pets.stressor", "args": {"pet": "dog_1", "cause": "storm"}},
                    {"op": "helper", "name": "pets.walk", "args": {"pet": "dog_1", "kit": True}},
                    {"op": "helper", "name": "pets.trapped", "args": {"pet": "cat_1", "where": "closet"}},
                    {"op": "helper", "name": "pets.arrival", "args": {"person": "guest_1", "role": "elderly", "frail": True}}],
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1}], "reference": []}
        for seed in (0, 1, 2):
            config, task, oracle = build(spec, seed)
            env = HouseEnv(config, task, seed=seed)
            env.step("wait", seconds=120)
            self.assertIn("dog_1", env.world.s["pets"])


class RobustPlacementTests(unittest.TestCase):
    """Scenario helpers and the furnisher on any generated layout (kernel v2.2 layouts, every dwelling)."""

    def _build(self, seed, setup, dwelling="house"):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "TEST-pets-robust", "house": {"dwelling": dwelling, "residents": "minimal", "pets": "none"},
                "setup": setup, "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1}], "reference": []}
        config, task, oracle = build(spec, seed)
        return config, task, oracle["bindings"]

    def test_food_cupboard_has_room_and_nothing_crushable(self):
        from house_sim.systems.pets import scenarios as SC
        setup = [{"op": "helper", "name": "pets.pet", "args": {"id": "dog_1", "species": "dog"}},
                 {"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                 {"op": "helper", "name": "pets.feeding", "args": {"pets": ["dog_1", "cat_1"], "wet_for": "cat_1"}}]
        for seed in (2, 41):        # seeds whose base cabinets came packed with bowls, pots and 20 valves
            config, task, b = self._build(seed, setup)
            cab = config["entities"][b["food_dog_1"]]["location"]
            self.assertIn(cab, config["entities"])
            inside = [e for e, r in config["entities"].items() if r.get("location") == cab]
            self.assertFalse([e for e in inside if SC._crushable(config["entities"][e])], "seed {}".format(seed))
            load = sum(float(config["entities"][e].get("mass_kg", 0) or 0) for e in inside
                       if config["entities"][e].get("relation") == "in" and int(config["entities"][e].get("shelf", 0) or 0) == 0)
            self.assertLessEqual(load, 30.0, "seed {}: shelf 0 holds {:.1f} kg".format(seed, load))
            # putting the food back after a meal neither crushes nor collapses anything
            env = HouseEnv(config, task, seed=seed)
            from house_sim.tasks.runner import run_plan
            run_plan(env, [{"skill": "pets.feed_meal", "bowl": b["bowl_dog_1"], "food": b["food_dog_1"], "amount": b["portion_dog_1"],
                            "unit": "g", "measure": "scale"}], b)
            stats = env.world.s["stats"]
            self.assertEqual((stats.get("breakages", 0), stats.get("damage", 0)), (0, 0), "seed {}".format(seed))

    def test_pets_are_placed_where_the_robot_walks_up_without_stepping_over_things(self):
        from house_sim.kernel import geometry
        from house_sim.tasks.skills import RELAXED_AVOID
        kinds = tuple(k for k in RELAXED_AVOID[0] if k not in ("sleeping", "tail", "dock_zone"))
        for seed in (41, 2, 97):
            config, task, b = self._build(seed, [{"op": "helper", "name": "pets.pet", "args": {
                "id": "dog_1", "species": "dog", "room": "kitchen", "posture": "sleeping", "awake": False}}])
            env = HouseEnv(config, task, seed=seed)
            st = env.world.s
            pet = st["pets"]["dog_1"]
            room = pet["location"]
            goals = geometry.near_cells([tuple(pet["pos"])], st["rooms"][room]) - {tuple(pet["pos"])}
            doors = [tuple(c["cells"][room]) for c in st["connections"].values() if room in c.get("cells", {})]
            self.assertTrue(any(d in goals or geometry.find_path(st, room, d, goals, avoid_kinds=kinds) is not None for d in doors),
                            "seed {}: dog at {} only reachable over floor items".format(seed, pet["pos"]))

    def test_helpers_fall_back_to_other_rooms_instead_of_crashing(self):
        from house_sim.kernel.errors import SpecError
        cases = [[{"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                  {"op": "helper", "name": "pets.trapped", "args": {"pet": "cat_1", "where": "appliance"}}],
                 [{"op": "helper", "name": "pets.terrarium", "args": {}}],
                 [{"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                  {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1", "washroom": "laundry_room"}}]]
        for setup in cases:
            for dwelling, seed in (("apartment", 0), ("apartment", 3), ("townhouse", 4), ("house", 3)):
                try:
                    config, task, b = self._build(seed, setup, dwelling)
                except SpecError as e:
                    self.assertNotIn("KeyError", str(e))
                    continue
                for key, eid in b.items():
                    if key in ("box", "enclosure", "appliance") and isinstance(eid, str):
                        self.assertIn(eid, config["entities"])

    def test_litter_helper_bins_are_receptacles_and_feeding_counters_have_room(self):
        from house_sim.systems.pets import scenarios as SC
        config, task, b = self._build(2, [{"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                                          {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1"}}])
        self.assertTrue(C.is_bin(config["entities"][b["outdoor_trash"]]), b["outdoor_trash"])   # seed 2: a spent HVAC filter sits in the garage
        setup = [{"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                 {"op": "helper", "name": "pets.feeding", "args": {"pets": ["cat_1"], "separate": True, "wet_for": "cat_1"}}]
        config, task, b = self._build(1, setup)      # seed 1: the first counter holds the microwave, a dish rack, a knife block...

        class _Ctx(object):
            pass
        ctx = _Ctx()
        ctx.config = config
        self.assertGreaterEqual(SC._free_area(ctx, b["counter"]), SC.MIN_FREE_M2)

    def test_helper_layouts_pass_the_generator_audit(self):
        """What the helpers add (ceramic bowls with navigation's keep-out ring, litter boxes, shelves, side tables, a
        stand-in dryer) cuts no hazard-free route, seals nothing and stands in no doorway clearance (the checks of
        generator/repair.py's final audit), also with a resident standing in a galley kitchen's corridor."""
        import re
        from house_sim.generator import generate_house, repair
        from house_sim.generator.place import Placer
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        cat = {"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}}
        cases = [("apartment", 7, [cat, {"op": "helper", "name": "pets.water", "args": {"pet": "cat_1", "near_litter": True}}]),
                 ("apartment", 4, [cat, {"op": "helper", "name": "pets.water", "args": {"pet": "cat_1"}}]),
                 ("house", 4, [cat, {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1", "washroom": "laundry_room", "trash_bag": True}}]),
                 ("townhouse", 1, [cat, {"op": "helper", "name": "pets.trapped", "args": {"pet": "cat_1", "where": "closet", "deliberate": True}}]),
                 ("apartment", 5, [{"op": "helper", "name": "pets.terrarium", "args": {}}]),
                 ("house", 4, [cat, {"op": "helper", "name": "pets.trapped", "args": {"pet": "cat_1", "where": "appliance"}}])]
        for dwelling, seed, setup in cases:
            base = generate_house(seed, dwelling=dwelling, residents="minimal", pets="none")
            before = []
            repair._audit(base, Placer(base, reg, None), before)
            config, task, b = self._build(seed, setup, dwelling)
            after = []
            repair._audit(config, Placer(config, reg, None), after)
            new = set(config["entities"]) - set(base["entities"])
            bad = [line for line in after if line not in before and set(re.findall(r"([a-z][a-z0-9_]*_\d+)", line)) & new]
            self.assertEqual(bad, [], "{} seed {}".format(dwelling, seed))

    def test_generated_pet_food_storage_is_within_shelf_ratings(self):
        from house_sim.generator import generate_house
        from house_sim.systems.pets import scenarios as SC
        for seed in (1, 41, 97, 7, 12):
            c = generate_house(seed)
            for eid, rec in c["entities"].items():
                if rec.get("type") not in ("dog_kibble_bag", "cat_kibble_bag", "cat_litter_bag"):
                    continue
                parent = c["entities"].get(rec.get("location"))
                if not parent or not parent.get("shelf_heights"):
                    continue
                rating = SC._shelf_rating(parent)
                shelf = rec.get("shelf", 0)
                load = sum(float(r.get("mass_kg", 0) or 0) for r in c["entities"].values()
                           if r.get("location") == rec["location"] and r.get("relation") == "in" and r.get("shelf", 0) == shelf)
                self.assertLessEqual(load, rating, "seed {}: {} overloads {}".format(seed, eid, rec["location"]))


class SettleTests(unittest.TestCase):
    """Kernel v2.3 layout round: awake pets never start on a doorway's clearance, a chokepoint or someone's only access,
    and pets that walk somewhere stop where they block nobody (common.standing_tiers / settle_spot / pet_cell_near)."""

    def test_awake_pet_steps_out_of_a_doorway_clearance_at_episode_start(self):
        from house_sim.kernel import geometry
        h = House()
        h.pet("dog_1", "dog", room="entry", pos=[1, 2])          # beside the front door (entry doorway [0, 2])
        h.pet("cat_1", "cat", room="living", pos=[8, 3], awake=False, posture="sleeping")   # beside the kitchen doorway
        h.pet("dog_2", "dog", room="living", pos=[4, 0])         # right in the hall doorway: a scenario's deliberate spot
        env = h.env("living", [5, 8])
        st = env.world.s
        dog = st["pets"]["dog_1"]
        self.assertNotIn(tuple(dog["pos"]), geometry.door_clearance(st, "entry"))
        self.assertEqual(C.standing_tiers(st, "entry", "dog_1").get(tuple(dog["pos"])), 0)
        self.assertEqual(st["pets"]["cat_1"]["pos"], [8, 3])     # asleep: stays put (the robot routes around it)
        self.assertEqual(st["pets"]["dog_2"]["pos"], [4, 0])

    def test_pet_in_the_only_gap_to_half_a_room_moves_aside(self):
        h = House()
        for x in range(10):                                      # a row of blocking furniture across the living room
            if x != 3:
                h.add("block_{}".format(x), "generic_item", "living", pos=[x, 7], label="bookcase", blocks_path=True,
                      portable=False, size_m=[0.5, 0.5, 1.2])
        h.pet("dog_1", "dog", room="living", pos=[3, 7])
        env = h.env("living", [8, 2])
        st = env.world.s
        self.assertNotEqual(st["pets"]["dog_1"]["pos"], [3, 7])
        from house_sim.kernel import geometry
        path = geometry.find_path(st, "living", (8, 2), [(6, 8)])
        self.assertIsNotNone(path)                               # the table behind the gap is reachable again
        # a pet kept on a stay cue holds its spot
        h2 = House()
        for x in range(10):
            if x != 3:
                h2.add("block_{}".format(x), "generic_item", "living", pos=[x, 7], blocks_path=True, portable=False, size_m=[0.5, 0.5, 1.2])
        h2.pet("dog_1", "dog", room="living", pos=[3, 7], hold_position=True)
        self.assertEqual(h2.env("living", [8, 2]).world.s["pets"]["dog_1"]["pos"], [3, 7])

    def test_a_pet_that_walks_to_a_door_stops_off_its_clearance(self):
        from house_sim.kernel import geometry
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[5, 8])
        env = h.env("living", [8, 8])
        w = env.world
        w.begin()
        C.move_pet(w, "dog_1", "entry", near=[0, 2], why="test")    # toward the front door
        w.commit()
        st = w.s
        pos = tuple(st["pets"]["dog_1"]["pos"])
        self.assertNotIn(pos, geometry.door_clearance(st, "entry"))
        self.assertLessEqual(geometry.chebyshev(pos, (0, 2)), 3)     # still by the door

    def test_a_pet_heading_for_its_bowl_or_a_call_ends_next_to_it(self):
        from house_sim.kernel import geometry
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[5, 8])
        env = h.env("entry", [2, 1])
        w = env.world
        w.begin()
        C.move_pet(w, "dog_1", "entry", near=[2, 1], why="test", mode="adjacent")      # called by the robot by the door
        w.commit()
        self.assertLessEqual(geometry.chebyshev(tuple(w.s["pets"]["dog_1"]["pos"]), (2, 1)), 1)
        w.begin()
        C.move_pet(w, "dog_1", "living", near=[3, 3], why="test", mode="exact")       # a scenario's chosen cell
        w.commit()
        self.assertEqual(w.s["pets"]["dog_1"]["pos"], [3, 3])

    def test_only_real_receptacles_count_as_bins(self):
        self.assertFalse(C.is_bin({"label": "spent HVAC filter", "category": "hvac_filter", "waste_stream": "trash", "portable": False}))
        self.assertTrue(C.is_bin({"label": "trash cart", "category": "trash_can", "waste_stream": "trash", "portable": False,
                                  "accepts_items": True, "container": True}))
        self.assertTrue(C.is_bin({"label": "toilet", "toilet": True}))


class IntegrationTests(unittest.TestCase):
    """Behaviour that depends on the other domains being present (kernel v2.1 integration round)."""

    def _leftovers(self, can_props):
        h = House()
        h.pet("cat_1", "cat", room="living", pos=[3, 3])
        h.add("cat_bowl", "cat_food_bowl", "kitchen", pos=[5, 8], owner="cat_1", food_g={"cat_wet": 40.0})
        h.add("pedal_can", "generic_item", "kitchen", pos=[2, 6], **can_props)
        env = h.env("kitchen", [5, 7])
        ok(self, step(env, "crouch"))
        ok(self, step(env, "pick_up", "cat_bowl"))
        ok(self, step(env, "stand"))
        near(env, "pedal_can")
        return env, step(env, "bag_waste", "cat_bowl", destination="pedal_can")

    def test_portable_pedal_can_is_a_bin_but_a_plain_box_is_not(self):
        # cleaning's kitchen_trash_can is a portable waste_container with a pedal lid
        env, r = self._leftovers({"label": "pedal trash can", "category": "waste_container", "is_a": ["waste_container", "carry_container"],
                                  "portable": True, "waste_stream": "trash", "accepts_items": True, "container": True, "openable": True,
                                  "open": False, "hands_free_operable": True, "capacity_l": 50.0, "size_m": [0.4, 0.3, 0.65]})
        ok(self, r)
        self.assertEqual(ent(env, "cat_bowl")["food_g"], {})
        self.assertTrue(ent(env, "pedal_can").get("has_food_waste"))
        env2, r2 = self._leftovers({"label": "cardboard box", "category": "box", "portable": True, "accepts_items": True, "container": True,
                                    "capacity_l": 20.0, "size_m": [0.4, 0.3, 0.3]})
        self.assertFalse(r2["ok"])
        self.assertGreater(ent(env2, "cat_bowl")["food_g"].get("cat_wet", 0), 39.0)   # not emptied into a storage box

    def test_static_index_sees_items_created_mid_episode(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], size="large", behaviors={"explores": 1.0})
        env = h.env("kitchen", [5, 5])
        step(env, "wait", seconds=120)                    # the agents' candidate lists are cached by now
        self.assertEqual(pet(env, "dog_1")["toxic_ingestions"], 0)
        rec = env.world.reg.make("generic_item", "living", label="dark chocolate bar", category="snack", food={"kind": "chocolate"},
                                 size_m=[0.15, 0.08, 0.02], mass_kg=0.1, portable=True)
        rec["pos"] = [7, 4]
        env.world.begin()
        env.world.create("choc", rec)
        env.world.commit()
        step(env, "wait", seconds=900)
        self.assertEqual(pet(env, "dog_1")["toxic_ingestions"], 1)
        self.assertNotIn("choc", env.world.s["entities"])

    def test_resting_pets_sit_up_and_lying_ones_keep_their_tail_hazard(self):
        rec = {"species": "dog", "location": "living"}
        M.ensure_record(rec, "dog_1")
        self.assertEqual(rec["posture"], "sitting")
        rec2 = {"species": "cat", "location": "living", "awake": False}
        M.ensure_record(rec2, "cat_1")
        self.assertEqual(rec2["posture"], "sleeping")

    def test_conventions_odor_and_damage_helpers(self):
        self.assertEqual(C.odor_level({"odor": 0.4}), 0.4)
        self.assertEqual(C.odor_level({"odor": {"litter": 0.7, "urine": 0.2}}), 0.7)
        self.assertEqual(C.odor_level({"odor": {"urine": 0.2}}, "litter"), 0.0)
        # writers produce the CONVENTIONS 5 dict (other kinds kept, a legacy float replaced by the named kind)
        self.assertTrue(C.DICT_ODOR)
        self.assertEqual(C.odor_with({"odor": {"urine": 0.2}}, "litter", 0.3), {"litter": 0.3, "urine": 0.2})
        self.assertEqual(C.odor_with({"odor": 0.5}, "litter", 0.3), {"litter": 0.3})
        self.assertEqual(C.odor_with({"odor": {"litter": 0.3}}, "litter", 0.0), {})
        self.assertEqual(C.damage_with({"damage": "torn_plush"}, "chewed", 0.4), {"chewed": 0.4, "torn_plush": 1.0})
        self.assertEqual(C.damage_with({"damage": {"chewed": 0.8}}, "chewed", 0.4), {"chewed": 0.8})
        self.assertEqual(C.damage_with({"damage": None}, "pump_warped", 2.0), {"pump_warped": 1.0})

    def test_dog_chewing_marks_damage_as_a_dict(self):
        h = House()
        h.pet("dog_1", "dog", room="living", pos=[6, 4], behaviors={"explores": 1.0, "chews": ["sock"], "steals_items": 1.0},
              stash_spots=["sofa"])
        h.add("sock", "generic_item", "living", pos=[7, 4], label="wool sock", category="clothing", chewable=True, pet_appeal=0.4,
              size_m=[0.25, 0.1, 0.02], mass_kg=0.05, damage={})
        env = h.env("kitchen", [5, 5])
        step(env, "wait", seconds=1200)
        sock = ent(env, "sock")
        self.assertTrue(sock.get("chewed"))
        self.assertIsInstance(sock["damage"], dict)
        self.assertGreater(sock["damage"].get("chewed", 0), 0)

    def _built(self, seed, setup):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "TEST-pets-integration", "house": {"residents": "minimal", "pets": "none"},
                "setup": [{"op": "room", "var": "kitchen", "type": "kitchen"}] + setup,
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1}], "reference": []}
        config, task, oracle = build(spec, seed)
        return config, oracle["bindings"]

    def test_helpers_never_wall_off_the_feeding_spots(self):
        """Galley kitchens (seeds 0, 41, 97) are one corridor: the dog, a resident and the bowls must leave the log and
        both bowls approachable from the doorway, and the robot must not start boxed in."""
        from house_sim.kernel import geometry
        setup = [{"op": "helper", "name": "pets.pet", "args": {"id": "dog_1", "species": "dog", "size": "large", "room": "{kitchen}"}},
                 {"op": "helper", "name": "pets.person", "args": {"id": "resident_1", "room": "{kitchen}"}},
                 {"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat", "room": "living_room"}},
                 {"op": "helper", "name": "pets.feeding", "args": {"pets": ["dog_1", "cat_1"], "room": "{kitchen}", "separate": True}}]
        for seed in (0, 41, 97):
            config, b = self._built(seed, setup)
            state = dict(config, time_s=0.0)
            room = b["kitchen"]
            doors = [tuple(c["cells"][room]) for c in config["connections"].values() if room in c.get("cells", {})]
            for target in (b["log"], b["bowl_dog_1"], b["bowl_cat_1"]):
                state["robot"] = dict(config["robot"], room=room)
                troom, cells = geometry.anchor(state, target)
                self.assertEqual(troom, room)
                goals = geometry.near_cells(cells, config["rooms"][room]) - set(cells)
                self.assertTrue(any(geometry.find_path(state, room, d, goals) is not None for d in doors),
                                "seed {}: {} walled off".format(seed, target))
            rroom, rpos = config["robot"]["room"], tuple(config["robot"]["pos"])
            rdoors = [tuple(c["cells"][rroom]) for c in config["connections"].values() if rroom in c.get("cells", {})]
            state["robot"] = dict(config["robot"])
            self.assertTrue(any(geometry.find_path(state, rroom, rpos, [d]) is not None or rpos == d for d in rdoors),
                            "seed {}: robot boxed in".format(seed))

    def test_door_swings_are_kept_clear_of_toppling_items(self):
        """Kernel v2.3 door model (geometry.door_clearance: doorway + 4 neighbours, hinged leaves swinging into the room,
        overhead thresholds): nothing tall the pets helpers put on the floor stands there."""
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        setup = [{"op": "helper", "name": "pets.pet", "args": {"id": "dog_1", "species": "dog"}},
                 {"op": "helper", "name": "pets.kit", "args": {"pet": "dog_1", "items": ["bowls", "bed", "carrier", "toys"]}},
                 {"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                 {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1"}}]
        for seed in (1, 2):
            base = set(generate_house(seed, residents="minimal", pets="none")["entities"])
            config, b = self._built(seed, setup)
            state = dict(config, time_s=0.0)
            for eid, rec in sorted(config["entities"].items()):
                if eid in base or rec.get("location") not in config["rooms"] or not rec.get("pos") or rec.get("mounted"):
                    continue
                if C.num((rec.get("size_m") or [0, 0, 0.1])[2], 0.1) < 0.15:
                    continue
                clearance = geometry.door_clearance(state, rec["location"])
                self.assertFalse(set(geometry.footprint(rec)) & clearance, "seed {}: {} in a doorway clearance".format(seed, eid))
            for conn in config["connections"].values():
                door = conn.get("door")
                rid, leaf = geometry.door_swing(state, door) if door else (None, [])
                for eid, rec in config["entities"].items():
                    if eid in base or rec.get("location") != rid or not rec.get("pos") or rec.get("mounted"):
                        continue
                    self.assertFalse(set(geometry.footprint(rec)) & set(leaf), "seed {}: {} in the swing of {}".format(seed, eid, door))


# ----------------------------------------------------------------------
# realistic perception (kernel v2.4)

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def true_ids(obj, env):
    """True entity ids (with an underscore: fixture ids like 'sink' coincide with words) found anywhere in obj."""
    ids = {e for e in env.world.s["entities"] if "_" in e and not e.startswith("floor_") and not e.startswith("light_")}
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
    walk(obj)
    return found


class RealisticPerceptionTests(unittest.TestCase):
    """perception="realistic": glance shows buckets, inspect/read/check reveal at their precision, look-alikes are
    confused until read, observers and messages leak no true ids or hidden values. Oracle mode is unchanged."""

    def setUp(self):
        self.h = House(start="2026-10-09T10:00:00")
        h = self.h
        h.pet("dog_1", "dog", room="kitchen", pos=[4, 6], size="medium")
        h.add("water_bowl_1", "dog_water_bowl", "kitchen", pos=[6, 8], owner="dog_1", liquid_l=1.6, _last_l=1.6, biofilm=0.5,
              debris=0.05, _water_age_s=50 * 3600.0)
        h.add("food_bowl_1", "dog_food_bowl", "kitchen", pos=[5, 8], owner="dog_1", food_g={"rx_renal_kibble": 300.0},
              food_meta={"rx_renal_kibble": {"wet": False, "density": 0.42}})
        h.add("kibble_bag_1", "dog_kibble_bag", "counter", open=True)
        h.add("rx_bag_1", "rx_kibble_bag", "counter", open=True)
        h.add("med_rex_1", "pet_medication", "counter", prescribed_for="dog_1", drug="carprofen 75 mg", label="carprofen 75 mg for Dog 1",
              label_text="Rx carprofen 75 mg - Dog 1 (dog). Give 1 tablet with food at 08:00.")
        h.add("med_cat_1", "pet_medication", "counter", prescribed_for="cat_9", drug="gabapentin 50 mg", label="gabapentin 50 mg for Tom",
              label_text="Rx gabapentin 50 mg - Tom (cat).")
        h.add("litter_box_1", "litter_box", "kitchen", pos=[1, 8], clumps=3, feces=1, litter_kg=10.0, odor={"litter": 0.62})
        h.add("scoop_1", "litter_scoop", "counter")
        h.add("food_scoop_1", "pet_food_scoop", "counter")
        h.add("roll_1", "poop_bag_roll", "kitchen", pos=[1, 6])

    def env(self, perception="realistic", **kw):
        kw.setdefault("robot_room", "kitchen")
        kw.setdefault("robot_pos", [4, 4])
        return self.h.env(perception=perception, **kw)

    @staticmethod
    def H(env, eid):
        return env.realism.handles.handle(eid)

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(self.H(env, eid), {})

    def test_glance_hides_inspect_tier_truth(self):
        env = self.env()
        bowl = self.seen(env, "water_bowl_1")
        self.assertEqual(bowl["liquid_l"], "full")                    # a fill bucket, not 1.6 l
        for field in ("biofilm", "debris", "water_level", "_water_age_s", "owner", "liquid_temp_c", "home_pos"):
            self.assertNotIn(field, bowl)
        food = self.seen(env, "food_bowl_1")
        self.assertNotIn("food_g", food)
        box = self.seen(env, "litter_box_1")
        for field in ("clumps", "feces", "odor", "litter_kg", "depth_cm", "_buried", "_abnormal"):
            self.assertNotIn(field, box)

    def test_inspect_reveals_slime_only_on_a_close_look_and_estimates_the_level(self):
        env = self.env()
        h = self.H(env, "water_bowl_1")
        ok(self, step(env, "approach", h))
        plain = json.loads(ok(self, step(env, "inspect", h))["message"])
        self.assertEqual(plain["water_looks"], "clear")
        self.assertNotIn("walls", plain)
        self.assertNotIn("water_level", plain)
        self.assertAlmostEqual(plain["level"], 1.6 / 1.9, delta=0.2)
        self.assertAlmostEqual(round(plain["level"] * 20), plain["level"] * 20, places=6)     # 5 % steps
        close = json.loads(ok(self, step(env, "inspect", h, where="closely"))["message"])
        self.assertEqual(close["walls"], "slimy film")
        rec = self.seen(env, "water_bowl_1")
        self.assertNotIn("biofilm", rec)                               # the raw slime value is never shown
        self.assertIsInstance(rec["liquid_l"], float)                  # an inspection estimate (to 0.1 l)
        self.assertEqual(rec["liquid_l"], round(rec["liquid_l"], 1))

    def test_food_in_a_bowl_looks_like_kibble_not_a_renal_diet(self):
        env = self.env()
        h = self.H(env, "food_bowl_1")
        ok(self, step(env, "approach", h))
        msg = ok(self, step(env, "inspect", h))["message"]
        details = json.loads(msg)
        self.assertEqual(set(details["food"]), {"kibble"})
        self.assertIn(details["food"]["kibble"], ("low", "half", "full"))
        self.assertNotIn("renal", msg)

    def test_check_pet_gives_tool_or_eye_precision_and_grants_knowledge(self):
        env = self.env()
        h = self.H(env, "litter_box_1")
        ok(self, step(env, "approach", h))
        self.assertNotIn("clumps", self.seen(env, "litter_box_1"))
        res = json.loads(ok(self, step(env, "check_pet", h, aspect="litter"))["message"])
        self.assertEqual(res["clumps_visible"], 3)                     # countable up close
        true_depth = 10.0 / (1.05 * 0.171 * 10.0)
        self.assertAlmostEqual(res["depth_cm"], true_depth, delta=1.5)
        self.assertEqual(res["depth_cm"] * 2, round(res["depth_cm"] * 2))     # judged to half a centimetre
        self.assertEqual(res["object"], h)
        self.assertEqual(self.seen(env, "litter_box_1")["clumps"], 3)  # the check granted inspect knowledge
        self.assertNotIn("depth_cm", self.seen(env, "litter_box_1"))   # exact depth needs a tape measure
        oracle = self.env(perception="oracle")
        step(oracle, "approach", "litter_box_1")
        exact = json.loads(ok(self, step(oracle, "check_pet", "litter_box_1", aspect="litter"))["message"])
        self.assertEqual(exact["depth_cm"], round(true_depth, 1))

    def test_scoop_message_counts_what_it_scooped_but_estimates_the_depth(self):
        env = self.env(hold={"right": "scoop_1"})
        h = self.H(env, "litter_box_1")
        ok(self, step(env, "approach", h))
        msg = ok(self, step(env, "scoop_litter", h, destination=self.H(env, "roll_1")))["message"]
        self.assertIn("3 clump(s) and 1 stool(s)", msg)
        self.assertIn("litter now about", msg)
        self.assertEqual(true_ids(msg, env), set())

    def test_dog_food_bags_confused_at_a_glance_and_resolved_by_reading(self):
        env = self.env(robot_pos=[2, 1])
        reg, rx = self.seen(env, "kibble_bag_1"), self.seen(env, "rx_bag_1")
        self.assertEqual(reg["label"], rx["label"])
        self.assertEqual(rx["label"], "bag of dry dog food")
        self.assertEqual(rx["candidates"], ["adult dog kibble", "prescription diet kibble"])      # descriptive group candidates
        self.assertEqual(rx["kind"], reg["kind"])
        self.assertNotIn("pet_food", rx)
        self.assertNotIn("label_text", rx)
        text = json.loads(ok(self, step(env, "read", self.H(env, "rx_bag_1")))["message"])
        self.assertIn("VETERINARY DIET", text["label_text"])
        rx = self.seen(env, "rx_bag_1")
        self.assertEqual(rx["label"], "bag of prescription diet kibble")
        self.assertEqual(rx["pet_food"]["kind"], "rx_renal_kibble")
        self.assertEqual(self.seen(env, "kibble_bag_1")["label"], "bag of dry dog food")     # still unresolved

    def test_serving_from_an_unread_bag_does_not_name_the_diet_or_exact_grams(self):
        env = self.env(robot_pos=[4, 7], hold={"right": "rx_bag_1", "left": "food_scoop_1"})
        bowl = self.H(env, "food_bowl_1")
        ok(self, step(env, "approach", bowl))
        msg = ok(self, step(env, "feed", bowl, food=self.H(env, "rx_bag_1"), amount=1, unit="scoop", measure="level"))["message"]
        self.assertRegex(msg, r"1(\.0)? level scoop\(s\) of kibble")
        self.assertNotIn("already been fed", msg)       # feeding history is learned from the log or by asking
        self.assertNotIn("renal", msg)
        self.assertNotRegex(msg, r"\d+ g\b")
        self.assertEqual(true_ids(msg, env), set())

    def test_pill_bottles_look_alike_until_the_label_is_read(self):
        env = self.env(robot_pos=[2, 1])
        rex, cat = self.seen(env, "med_rex_1"), self.seen(env, "med_cat_1")
        self.assertEqual(rex["label"], cat["label"])
        self.assertIn("candidates", rex)
        for field in ("prescribed_for", "drug", "toxin", "schedule"):
            self.assertNotIn(field, rex)
        ok(self, step(env, "read", self.H(env, "med_rex_1")))
        rex = self.seen(env, "med_rex_1")
        self.assertEqual(rex["prescribed_for"], "dog_1")
        self.assertEqual(rex["drug"], "carprofen 75 mg")
        self.assertNotIn("toxin", rex)                                 # never-tier: learned by asking
        self.assertNotEqual(self.seen(env, "med_cat_1")["label"], "gabapentin 50 mg for Tom")

    def test_unread_pill_bottles_get_one_refusal_and_a_read_one_is_named(self):
        # the dog's pills, the cat's pills and a person's prescription look alike: whatever is inside, an unread bottle
        # gets the same refusal (no "That is not a pet medication" for the person's bottle)
        self.h.add("rx_person_1", "generic_item", "counter", label="lisinopril 10 mg for Sam", looks_like="pill_bottle",
                   label_text="Rx lisinopril 10 mg - Sam.", category="medicine", size_m=[0.05, 0.05, 0.09], mass_kg=0.06)
        replies = set()
        for med in ("med_rex_1", "med_cat_1", "rx_person_1"):
            env = self.env(robot_pos=[4, 7], hold={"right": med})
            env.world.s.setdefault("approvals", []).append("give_pet_medication:dog_1")
            ok(self, step(env, "approach", self.H(env, "food_bowl_1")))
            r = step(env, "give_pet_medication", "dog_1", medication=self.H(env, med), method="in_food", bowl=self.H(env, "food_bowl_1"))
            self.assertFalse(r["ok"])
            replies.add((r["message"], r["category"]))
            self.assertEqual(env.world.s["pets"]["dog_1"].get("med_errors", 0), 0)
        self.assertEqual(len(replies), 1, replies)
        env = self.env(robot_pos=[3, 2], hold={"right": "med_rex_1"})
        env.world.s.setdefault("approvals", []).append("give_pet_medication:dog_1")
        ok(self, step(env, "read", self.H(env, "med_rex_1")))
        ok(self, step(env, "approach", self.H(env, "food_bowl_1")))      # what the robot read survives carrying it across the room
        msg = ok(self, step(env, "give_pet_medication", "dog_1", medication=self.H(env, "med_rex_1"), method="in_food",
                            bowl=self.H(env, "food_bowl_1")))["message"]
        self.assertIn("carprofen", msg)
        self.assertEqual(true_ids(msg, env), set())

    def test_litter_and_bedding_bags_resolve_only_by_reading(self):
        from house_sim.tasks.runner import build, make_env
        spec = {"catalog_id": "TEST", "title": "t", "perception": "realistic", "house": {"residents": "minimal", "pets": "none"},
                "setup": [{"op": "helper", "name": "pets.pet", "args": {"id": "cat_1", "species": "cat"}},
                          {"op": "helper", "name": "pets.litter", "args": {"pet": "cat_1", "spare_kind": "scented_clumping_clay"}},
                          {"op": "helper", "name": "pets.small_animal", "args": {}}],
                "instruction": "x", "phrasings": ["a", "b", "c"], "success": [{"path": "time_s", "op": "gte", "value": 0}],
                "budget": {"steps": 5, "time_s": 60}, "reference": [], "wrong": [], "risk_level": 0, "fidelity": "S", "target_fidelity": "S"}
        config, task, oracle = build(spec, 1)
        b = oracle["bindings"]
        env = make_env(config, task, 1, perception="realistic")
        st = env.world.s["entities"]
        self.assertIn("Clumping clay unscented", st[b["litter_bag"]]["label_text"])
        self.assertIn("Scented clumping clay", st[b["other_litter"]]["label_text"])
        self.assertIn("Cedar", st[b["cedar_bedding"]]["label_text"])
        realism = env.realism
        for a, z in ((b["litter_bag"], b["other_litter"]), (b["paper_bedding"], b["cedar_bedding"])):
            ia = realism.identity(a, st[a], 0)
            iz = realism.identity(z, st[z], 0)
            self.assertEqual(ia["label"], iz["label"])
            self.assertIn("candidates", ia)
            self.assertIn("candidates", realism.identity(z, st[z], 1))                    # inspecting is not enough
            self.assertEqual(realism.identity(z, st[z], 3)["label"], st[z]["label"])     # reading the label resolves it

    def test_observer_cues_and_smells_are_perceived_not_raw(self):
        self.h.c["pets"]["dog_1"].update(wants="out", activity="waiting_at_door")
        self.h.pet("cat_2", "cat", room="kitchen", pos=[7, 2], awake=False, posture="sleeping", activity="sleeping")
        env = self.env()
        obs = env.observe()
        self.assertEqual(obs["smells"], [{"kind": "litter_box", "intensity": "noticeable"}])
        dog = obs["pets"]["dog_1"]
        self.assertNotIn("wants", dog)
        self.assertIn("waiting by the door", dog["cues"])
        cat = obs["pets"]["cat_2"]
        self.assertEqual((cat["posture"], cat["activity"]), ("resting", "resting"))      # kernel v2.8: asleep reads as resting
        self.assertEqual(true_ids(obs, env), set())
        oracle = self.env(perception="oracle").observe()
        self.assertEqual(oracle["pets"]["dog_1"]["wants"], "out")
        self.assertEqual(oracle["smells"], [{"kind": "litter_box", "intensity": 0.62}])

    def test_trapped_pet_sound_points_at_the_door_without_naming_hidden_things(self):
        self.h.pet("cat_3", "cat", room="closet", pos=[0, 0], trapped=True)
        env = self.env(robot_room="entry", robot_pos=[2, 1])
        obs = env.observe()
        sound = next(s for s in obs["sounds"] if s["kind"] == "meowing_and_scratching")
        self.assertEqual(sound["through"], self.H(env, "door_closet"))
        self.assertNotIn("cat_3", obs.get("pets", {}))
        self.assertEqual(true_ids(obs, env), set())
        oracle = self.h.env("entry", [2, 1]).observe()
        self.assertNotIn("through", next(s for s in oracle["sounds"] if s["kind"] == "meowing_and_scratching"))

    def test_hidden_ground_truth_never_shown_after_inspection(self):
        self.h.add("toy_1", "squeaky_toy", "kitchen", pos=[3, 3])
        env = self.env(robot_pos=[3, 4])
        ok(self, step(env, "inspect", self.H(env, "toy_1")))
        toy = self.seen(env, "toy_1")
        for field in ("toy_owner", "toy_for", "owner"):
            self.assertNotIn(field, toy)
        ok(self, step(env, "approach", self.H(env, "water_bowl_1")))
        ok(self, step(env, "inspect", self.H(env, "water_bowl_1"), where="closely"))
        bowl = self.seen(env, "water_bowl_1")
        for field in ("biofilm", "debris", "_water_age_s", "water_level", "slosh_frac", "owner", "freshness"):
            self.assertNotIn(field, bowl)                             # a slimy bowl never reads "fresh"

    def test_oracle_inspect_is_unchanged(self):
        env = self.env(perception="oracle")
        self.assertIsNone(env.realism)
        step(env, "approach", "water_bowl_1")
        details = json.loads(ok(self, step(env, "inspect", "water_bowl_1", where="closely"))["message"])
        self.assertEqual(details["water_level"], round(1.6 / 1.9, 2))
        self.assertEqual(details["walls"], "slimy film")
        step(env, "approach", "food_bowl_1")
        details = json.loads(ok(self, step(env, "inspect", "food_bowl_1"))["message"])
        self.assertIn("rx_renal_kibble", details["food"])
        self.assertIsInstance(details["food"]["rx_renal_kibble"], float)

    def test_realistic_walk_leaks_no_true_ids(self):
        env = self.env(hold={"right": "scoop_1"})
        messages = []
        for target, action, kw in (("water_bowl_1", "approach", {}), ("water_bowl_1", "inspect", {"where": "closely"}),
                                   ("water_bowl_1", "check_pet", {"aspect": "bowl"}), ("litter_box_1", "approach", {}),
                                   ("litter_box_1", "inspect", {}), ("litter_box_1", "check_pet", {"aspect": "litter"}),
                                   ("litter_box_1", "scoop_litter", {"destination": "roll_1"}), ("dog_1", "approach", {}),
                                   ("dog_1", "check_pet", {"aspect": "general"})):
            args = {k: (self.H(env, v) if v in env.world.s["entities"] else v) for k, v in kw.items()}
            tgt = self.H(env, target) if target in env.world.s["entities"] else target
            r = ok(self, step(env, action, tgt, **args))
            messages.append(r["message"])
            self.assertEqual(true_ids(r["message"], env), set(), (action, r["message"]))
            self.assertEqual(true_ids(env.observe(), env), set(), action)
        general = json.loads(messages[-1])
        self.assertNotIn("food_left_g", general)
        self.assertIn(general["food_left"], ("none", "a few bits", "some", "plenty"))

    # ----- kernel v2.8 hardening sweep -------------------------------------------------------------------------

    def test_asleep_and_awake_lying_pets_are_one_percept(self):
        self.h.pet("cat_a", "cat", room="kitchen", pos=[5, 2], awake=False, posture="sleeping", activity="napping")
        self.h.pet("cat_b", "cat", room="kitchen", pos=[2, 4], awake=True, posture="lying", activity="resting")
        env = self.env()
        obs = env.observe()
        a, b = obs["pets"]["cat_a"], obs["pets"]["cat_b"]
        for view in (a, b):
            self.assertEqual((view["posture"], view["activity"]), ("resting", "resting"))
        self.assertEqual(sorted(a), sorted(b))
        kinds = {src: sorted({hz["kind"] for hz in obs["grid"]["hazards"] if hz.get("source") == src}) for src in ("cat_a", "cat_b")}
        self.assertEqual(kinds["cat_a"], kinds["cat_b"])
        self.assertNotIn("sleeping", json.dumps(obs))

    def test_a_hidden_pet_leaves_no_hazard_ring_to_give_it_away(self):
        self.h.pet("cat_h", "cat", room="living", pos=[1, 3], awake=False, posture="sleeping", hiding_under="sofa")
        obs = self.env(robot_room="living", robot_pos=[5, 6]).observe()
        self.assertNotIn("cat_h", obs["pets"])
        self.assertEqual([hz for hz in obs["grid"]["hazards"] if hz.get("source") == "cat_h"], [])
        self.h.c["pets"]["cat_h"]["revealed"] = True                    # once found under the sofa, its ring is shown
        obs = self.env(robot_room="living", robot_pos=[5, 6]).observe()
        self.assertTrue([hz for hz in obs["grid"]["hazards"] if hz.get("source") == "cat_h"])

    def test_eye_estimates_are_fixed_whatever_the_viewpoint(self):
        readings = set()
        for pos in ([2, 7], [0, 7], [2, 9]):
            env = self.env(robot_pos=pos)
            h = self.H(env, "litter_box_1")
            res = json.loads(ok(self, step(env, "check_pet", h, aspect="litter"))["message"])
            insp = json.loads(ok(self, step(env, "inspect", h))["message"])
            self.assertEqual(insp["litter_depth_cm"], res["depth_cm"])          # one judged depth everywhere
            readings.add(res["depth_cm"])
        self.assertEqual(len(readings), 1, readings)

    def test_instrument_readings_cannot_be_averaged_by_repeating(self):
        self.h.add("collar_1", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 43})
        self.h.c["entities"]["food_bowl_1"]["food_g"] = {}                   # nothing to wander off to eat between checks
        env = self.env(robot_pos=[4, 5])
        gaps = set()
        for _ in range(4):
            res = json.loads(ok(self, step(env, "check_pet", "dog_1", aspect="fit"))["message"])
            gaps.add(json.dumps(res["gear"], sort_keys=True))
        self.assertEqual(len(gaps), 1)
        true_gap = 43 - C.neck_cm(env.world.s["pets"]["dog_1"])
        slack = next(iter(json.loads(next(iter(gaps))).values()))["slack_cm"]
        self.assertLessEqual(abs(slack - true_gap), 0.35)

    def test_leash_message_reports_only_what_is_visible(self):
        self.h.add("collar_1", "dog_collar", "dog_1", relation="worn", settings={"length_cm": 60})       # far too loose
        self.h.add("leash_1", "leash", "counter")
        env = self.env(robot_pos=[4, 5], hold={"right": "leash_1"})
        msg = ok(self, step(env, "attach_leash", "dog_1", leash=self.H(env, "leash_1")))["message"]
        self.assertNotIn("loose", msg)
        self.assertNotIn("back out", msg)
        self.assertIn("fit not checked", msg)
        oracle = self.h.env("kitchen", [4, 5], hold={"right": "leash_1"})
        self.assertIn("back out", ok(self, step(oracle, "attach_leash", "dog_1", leash="leash_1"))["message"])

    def test_skim_note_does_not_reveal_buried_clumps(self):
        notes = set()
        for buried in (0, 3):
            self.h.c["entities"]["litter_box_1"]["_buried"] = buried
            env = self.env(robot_pos=[2, 7], hold={"right": "scoop_1"})
            msg = ok(self, step(env, "scoop_litter", self.H(env, "litter_box_1"), destination=self.H(env, "roll_1"), thorough=False))["message"]
            notes.add(msg.split(";", 1)[1] if ";" in msg else "")
        self.assertEqual(len(notes), 1, notes)

    def test_refill_works_from_the_judged_depth(self):
        from house_sim.systems.pets import litter as L
        self.h.add("litter_bag_1", "cat_litter_bag", "counter", open=True, litter_kind="clumping_clay_unscented")
        self.h.c["entities"]["litter_box_1"].update(clumps=0, feces=0, litter_kg=7.0, litter_kind="clumping_clay_unscented")
        env = self.env(robot_pos=[2, 7], hold={"right": "litter_bag_1"})
        h = self.H(env, "litter_box_1")
        box = env.world.s["entities"]["litter_box_1"]
        seen = L.seen_depth(type("Ctx", (), {"w": env.world, "env": env})(), "litter_box_1")
        want = L.kg_for_depth(box, 6.5) - L.kg_for_depth(box, seen)
        before = env.world.s["time_s"]
        ok(self, step(env, "refill_litter", h, litter=self.H(env, "litter_bag_1"), depth_cm=6.5))
        added = env.world.s["entities"]["litter_box_1"]["litter_kg"] - 7.0
        self.assertAlmostEqual(added, want * (1 - 0.003), places=3)
        self.assertEqual(env.world.s["time_s"] - before, round(env.world.s["time_s"] - before))   # whole seconds

    def test_spilled_food_and_a_spat_out_tablet_do_not_name_what_they_are(self):
        self.h.c["entities"]["food_bowl_1"]["food_g"] = {"rx_renal_kibble": 520.0}            # nearly full: the next scoop spills
        env = self.env(robot_pos=[4, 7], hold={"right": "rx_bag_1", "left": "food_scoop_1"})
        ok(self, step(env, "approach", self.H(env, "food_bowl_1")))
        ok(self, step(env, "feed", self.H(env, "food_bowl_1"), food=self.H(env, "rx_bag_1"), amount=2, unit="scoop", measure="level"))
        spill = [e for e in env.world.s["entities"] if e.startswith("spilled_pet_food")]
        self.assertTrue(spill)
        self.assertEqual(env.world.s["entities"][spill[0]]["label"], "spilled kibble")       # a pile carries no label
        ok(self, step(env, "approach", self.H(env, spill[0])))
        ok(self, step(env, "inspect", self.H(env, spill[0])))
        seen = self.seen(env, spill[0])
        self.assertEqual(seen["label"], "spilled kibble")
        self.assertNotIn("renal", json.dumps(seen))
        # the tablet a cat spits out is a portion of its bottle (kernel derived_from): until that bottle is read it shows
        # the bottle's look-alike percept, never a drug name
        self.h.pet("cat_s", "cat", room="kitchen", pos=[5, 5], _spits_pill=1, hold_position=True)
        env = self.env(robot_pos=[4, 4], hold={"right": "med_cat_1"})
        env.world.s.setdefault("approvals", []).append("give_pet_medication:cat_s")
        ok(self, step(env, "read", self.H(env, "med_cat_1")))
        ok(self, step(env, "approach", "cat_s"))
        msg = ok(self, step(env, "give_pet_medication", "cat_s", medication=self.H(env, "med_cat_1"), method="direct"))["message"]
        self.assertIn("spat the pill out", msg)
        pill = next(e for e in env.world.s["entities"] if e.startswith("spat_out_pill"))
        rec = env.world.s["entities"][pill]
        self.assertEqual(rec["derived_from"], "med_cat_1")
        self.assertIn("gabapentin", rec["label"])           # the world label is the same in both modes (no realistic-only label)
        self.assertEqual(true_ids(env.observe(), env), set())
        unread = env.realism.identity(pill, dict(rec, derived_from="med_rex_1"), 0)       # from a bottle never read
        self.assertIn("candidates", unread)
        self.assertNotIn("carprofen", json.dumps(unread))

    def test_scenario_props_written_by_pets_keep_their_hidden_fields_hidden(self):
        self.h.add("grapes_1", "generic_item", "counter", label="bunch of grapes", toxin="grapes", pet_appeal={"dog": 0.9}, chewable=True,
                   food={"kind": "grapes", "state": "raw", "quantity": 300, "unit": "g"}, size_m=[0.2, 0.12, 0.08])
        self.h.add("doll_1", "generic_item", "kitchen", pos=[3, 3], label="doll", category="toy", toy_owner="child", owner="child_1")
        self.h.add("wrapper_1", "generic_item", "kitchen", pos=[3, 5], label="chewed wrapper", evidence_of="chocolate bar",
                   toxin_evidence="chocolate", source_pet="dog_1")
        env = self.env(robot_pos=[3, 4])
        self.assertNotIn("evidence_of", self.seen(env, "wrapper_1"))        # what the wrapper held needs a closer look
        for eid in ("doll_1", "wrapper_1"):
            ok(self, step(env, "inspect", self.H(env, eid)))
        ok(self, step(env, "approach", self.H(env, "grapes_1")))
        ok(self, step(env, "inspect", self.H(env, "grapes_1")))
        grapes, doll, wrapper = (self.seen(env, e) for e in ("grapes_1", "doll_1", "wrapper_1"))
        for field in ("toxin", "pet_appeal", "chewable"):
            self.assertNotIn(field, grapes)
        self.assertNotIn("toy_owner", doll)
        self.assertEqual(wrapper.get("evidence_of"), "chocolate bar")       # seen on a close look
        for field in ("toxin_evidence", "source_pet"):
            self.assertNotIn(field, wrapper)

    def test_an_accident_does_not_name_the_pet_that_made_it(self):
        from house_sim.systems.pets import behavior as B
        env = self.env()
        B._accident(env.world, "dog_1", "urine")
        spot = next(e for e in env.world.s["entities"] if e.startswith("pet_accident"))
        self.assertEqual(env.world.s["entities"][spot]["label"], "urine spot")
        seen = env.observe()["visible_entities"][self.H(env, spot)]
        self.assertNotIn("source_pet", seen)
        self.assertNotIn("Dog 1", json.dumps(seen))

    def test_treat_messages_do_not_announce_toxins_diets_or_unread_jars(self):
        self.h.c["pets"]["dog_1"]["diet"]["no_treats"] = True
        self.h.add("jar_1", "pet_treat_jar", "counter", open=True, treat_species="cat", treat_kind="salmon_bites")
        self.h.add("grape_1", "generic_item", "counter", label="grape", toxin="grapes", food={"kind": "grapes", "state": "raw", "quantity": 5, "unit": "g"})
        for item in ("jar_1", "grape_1"):
            env = self.env(robot_pos=[4, 5], hold={"right": item})
            msg = ok(self, step(env, "give_treat", "dog_1", treat=self.H(env, item)))["message"]
            for word in ("toxic", "diet", "not made for", "salmon"):
                self.assertNotIn(word, msg)
            self.assertEqual(true_ids(msg, env), set())
        oracle = self.h.env("kitchen", [4, 5], hold={"right": "grape_1"})
        self.assertIn("toxic", ok(self, step(oracle, "give_treat", "dog_1", treat="grape_1"))["message"])

    def test_recalling_an_unseen_pet_gives_one_answer(self):
        replies = set()
        for case in ("closed", "far", "gone"):
            h = House()
            h.pet("dog_r", "dog", room="bedroom" if case != "far" else "front", pos=[1, 1], escaped=case == "gone")
            if case == "closed":
                h.c["entities"]["door_bed"]["open"] = False
            env = h.env("hall", [2, 3], perception="realistic")
            r = ok(self, step(env, "calm_pet", "dog_r", method="recall"))
            replies.add((r["message"], r["observation"]["time_s"]))
        self.assertEqual(len(replies), 1, replies)

    def test_check_aspects_examine_only_their_own_equipment(self):
        env = self.env(robot_pos=[2, 1])
        for aspect in ("nozzle", "bowl", "eggs", "fountain", "water"):
            r = step(env, "check_pet", self.H(env, "med_rex_1"), aspect=aspect)
            self.assertFalse(r["ok"])
            self.assertEqual(r["message"], "Nothing pet-related to check there")

    def test_group_words_never_contain_a_member_label_or_type_name(self):
        # the kernel rewrites a named look-alike's true label/type name in action messages to the group descriptor; if a
        # member's name sat inside the descriptor, kind or candidates, only that member's text would be mangled
        reg = default_registry()
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "house_sim", "data", "types", "pets.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        for name, group in data["looks_like"].items():
            words = [group["descriptor"], group.get("kind", "")] + list(group.get("candidates", []))
            for member in group["members"]:
                rec = reg.make(member, "x")
                for bad in {str(rec.get("label") or ""), member, member.replace("_", " ")}:
                    for text in words:
                        self.assertIsNone(re.search(r"(?i)\b" + re.escape(bad) + r"\b", text), (name, bad, text))
        msgs = set()
        for tname in ("fish_food", "sinking_pellets"):
            self.h.c["entities"].pop("tub_1", None)
            self.h.add("tub_1", tname, "counter", open=True)
            env = self.env(robot_pos=[2, 1])
            msg = json.loads(ok(self, step(env, "inspect", self.H(env, "tub_1")))["message"])
            msgs.add(json.dumps({k: msg.get(k) for k in ("label", "kind", "candidates")}, sort_keys=True))
        self.assertEqual(len(msgs), 1, msgs)

    def test_box_filled_from_an_unread_bag_does_not_identify_the_litter(self):
        looks = set()
        for kind in ("clumping_clay_unscented", "scented_clumping_clay"):
            self.h.c["entities"]["litter_box_1"].update(clumps=0, feces=0, litter_kg=8.0, litter_kind=kind)
            env = self.env(robot_pos=[2, 7])
            h = self.H(env, "litter_box_1")
            details = json.loads(ok(self, step(env, "inspect", h))["message"])
            self.assertEqual(details["litter_kind"], "clay")
            seen = self.seen(env, "litter_box_1")
            self.assertNotIn("litter_kind", seen)
            looks.add(json.dumps({k: v for k, v in details.items() if k != "object"}, sort_keys=True))
        self.assertEqual(len(looks), 1)

    def test_a_crate_lure_must_be_food(self):
        self.h.add("crate_1", "pet_crate", "kitchen", pos=[6, 4], open=True)
        self.h.c["pets"]["dog_1"]["behaviors"]["crate_trained"] = 0.0
        env = self.env(robot_pos=[5, 5], hold={"right": "med_rex_1"})
        r = step(env, "crate", "dog_1", destination=self.H(env, "crate_1"), lure=self.H(env, "med_rex_1"))
        self.assertFalse(r["ok"])
        self.assertIn("treat or a piece of food", r["message"])
        self.assertEqual(env.world.s["pets"]["dog_1"].get("ingested") or [], [])

    def test_food_shut_in_the_fridge_is_not_smelled(self):
        self.h.add("fridge_1", "generic_item", "kitchen", pos=[7, 4], label="fridge", openable=True, open=False, container=True,
                   accepts_items=True, internal_temp_c=4.0, portable=False, size_m=[0.7, 0.7, 1.8])
        self.h.add("can_1", "cat_wet_food_can", "fridge_1", opened=True, spoiled=True)
        env = self.env()
        self.assertNotIn("spoiled_pet_food", json.dumps(env.observe().get("smells", [])))
        self.assertIn("spoiled_pet_food", json.dumps(self.h.env("kitchen", [4, 4]).observe().get("smells", [])))


if __name__ == "__main__":
    unittest.main()
