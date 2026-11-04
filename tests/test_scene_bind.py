"""mechanisms.scene_bind: the bindings the flagship scene needs, computed from a generated house's geometry.

The default seeds are the pilot's; HOUSE_SCENE_SEEDS (comma separated) widens the run, for the 20-seed list of the design.
"""
import copy
import os
import unittest

from house_sim.generator import generate_house
from house_sim.kernel import geometry
from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.setup import apply_setup

RESIDENTS = [{"id": "resident_1", "role": "adult", "age": 41, "label": "Avery", "has_phone": True, "reachable_by_phone": True},
             {"id": "child_1", "role": "child", "age": 9, "label": "Hayden"}]
HOUSE = {"gen": 2, "dwelling": "house", "floors": 1, "bedrooms": 3, "bathrooms": 2,
         "require": ["dining_room", "laundry_room", "pantry", "office", "porch"], "residents": RESIDENTS, "pets": "none",
         "start": "2026-10-09T17:00:00", "region": "us"}
OPS = [{"op": "helper", "name": "appliances_cooking.kitchen", "args": {"unit": "F", "timer": False}},
       {"op": "helper", "name": "mechanisms.scene_bind", "args": {"mitts": "{mitts}", "sums": {"t_x": [3, 4]}}}]
SPEC = {"catalog_id": "scene-bind-test", "schema": 4}
SEEDS = [int(s) for s in os.environ.get("HOUSE_SCENE_SEEDS", "13,1,3,8,21,34").split(",")]
NO_WASHER_VALVES_SEED = 104068503     # its washer sits in a bathroom, with valves whose feeds list is empty


def build(seed, ops=None):
    config = generate_house(seed, **HOUSE)
    return config, apply_setup(config, ops or OPS, default_registry(), seed, SPEC)


def view(config):
    state = dict(config)
    state.setdefault("robot", {"room": None, "hands": {}, "pos": None})
    return state


class SceneBind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_domains()
        cls.built = {seed: build(seed) for seed in SEEDS}

    def test_cells_are_free_and_apart(self):
        for seed, (config, b) in self.built.items():
            state = view(config)
            room = "laundry_room_1"
            blocked = set(map(tuple, geometry.blocked_cells(state, room)))
            _, swing = geometry.door_swing(state, b["ldoor"])
            swing = {tuple(c) for c in swing}
            self.assertEqual(b["swing_room"], room, seed)
            self.assertIn(tuple(b["swing_cell"]), swing, seed)
            for name in ("swing_cell", "leak_cell", "lfar"):
                self.assertNotIn(tuple(b[name]), blocked, (seed, name))
            self.assertNotIn(tuple(b["leak_cell"]), swing, seed)
            self.assertNotIn(tuple(b["lfar"]), swing, seed)
            self.assertNotEqual(b["leak_cell"], b["swing_cell"], seed)
            cols, rows = geometry.grid_size(config["rooms"][room])
            for cell in (b["leak_cell"], b["lfar"], b["swing_cell"]):
                self.assertTrue(0 <= cell[0] < cols and 0 <= cell[1] < rows, (seed, cell))
            self.assertNotIn(tuple(b["seep_cell"]), set(map(tuple, geometry.blocked_cells(state, "hallway_1"))), seed)

    def test_valves_feed_the_washer(self):
        for seed, (config, b) in self.built.items():
            E = config["entities"]
            self.assertEqual(E[b["washer"]].get("location"), "laundry_room_1", seed)
            for name, line in (("cvalve", "cold"), ("hvalve", "hot")):
                self.assertEqual(E[b[name]].get("type"), "shutoff_valve", seed)
                self.assertEqual(E[b[name]].get("line"), line, seed)
                self.assertIn(b["washer"], E[b[name]].get("feeds") or [], seed)
                self.assertEqual(E[b[name]].get("location"), "laundry_room_1", seed)

    def test_spill_keeps_the_kitchen_doors_connected(self):
        for seed, (config, b) in self.built.items():
            state = view(config)
            room = "kitchen_1"
            cols, rows = geometry.grid_size(config["rooms"][room])
            blocked = set(map(tuple, geometry.blocked_cells(state, room)))
            x0, y0 = b["spill_cell"]
            patch = {(x0 + dx, y0 + dy) for dx in (0, 1) for dy in (0, 1)}
            self.assertFalse(patch & blocked, seed)
            doors = sorted(tuple(c["cells"][room]) for c in config["connections"].values() if room in c.get("cells", {}))
            self.assertFalse(patch & set(doors), seed)
            seen, queue = {doors[0]}, [doors[0]]
            while queue:
                x, y = queue.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (x + dx, y + dy)
                    if 0 <= n[0] < cols and 0 <= n[1] < rows and n not in blocked and n not in patch and n not in seen:
                        seen.add(n)
                        queue.append(n)
            self.assertTrue(set(doors) <= seen, seed)

    def test_kit_is_remembered_on_a_shelf_and_moves_to_a_surface(self):
        for seed, (config, b) in self.built.items():
            self.assertEqual(config["entities"][b["kit_holder"]].get("location"), "utility_closet_1", seed)
            self.assertTrue(b["kit_targets"], seed)
            self.assertNotIn(b["kit_holder"], b["kit_targets"], seed)
            for holder in b["kit_targets"]:
                self.assertTrue(config["entities"][holder].get("surface"), (seed, holder))
                self.assertIn(config["entities"][holder].get("location"), ("office_1", "bedroom_1", "pantry_1"), (seed, holder))
                ops = [{"op": "move", "target": b["broom"], "to": holder}, {"op": "move", "target": b["pan"], "to": holder}]
                apply_setup(copy.deepcopy(config), ops, default_registry(), seed, SPEC)

    def test_sums_and_mitts(self):
        for seed, (config, b) in self.built.items():
            self.assertEqual(b["t_x"], 7)
            self.assertEqual(config["entities"][b["mitts"]].get("location"), b["mitts_home"], seed)

    def test_pure(self):
        seed = SEEDS[0]
        config, first = build(seed)
        before = copy.deepcopy(config)
        again = apply_setup(config, OPS[1:], default_registry(), seed, SPEC, bindings={"mitts": first["mitts"]})
        for key in ("ldoor", "swing_cell", "lfar", "leak_cell", "spill_cell", "seep_cell", "kit_targets", "cvalve", "hvalve", "washer", "mdoor"):
            self.assertEqual(again[key], first[key], key)
        self.assertEqual(config["entities"], before["entities"])
        self.assertEqual(config["people"], before["people"])

    def test_refuses_a_house_without_the_scene(self):
        with self.assertRaises(SpecError):
            build(NO_WASHER_VALVES_SEED)


if __name__ == "__main__":
    unittest.main()
