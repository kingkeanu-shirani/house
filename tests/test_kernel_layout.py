"""Kernel round 5 (K-GEO) regressions: geometry access checks, avoid aliases, concealed agents' hazards, generator
repair and structure, outdoor light access, and the perception audit's leak rules.

Run: python -m unittest tests/test_kernel_layout.py   (also with HOUSE_SIM_STRICT=1)
"""
import json
import random
import sys
import unittest
from pathlib import Path

from house_sim.generator import _generate, generate_house
from house_sim.generator import repair
from house_sim.generator.place import Placer
from house_sim.kernel import geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import HOOKS
from house_sim.world import make_house, object_record

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import perception_audit  # noqa: E402


def _room(cols, rows, kind="kitchen", **extra):
    return dict({"size_m": [cols * geometry.CELL, rows * geometry.CELL], "type": kind, "label": kind.title(),
                 "outdoor": False, "exits": []}, **extra)


def _conn(a, cell_a, b, cell_b):
    return {"rooms": [a, b], "cells": {a: list(cell_a), b: list(cell_b)}, "door": None, "kind": "doorway", "width_m": 0.85}


def _block(location, pos, label="counter", **extra):
    return dict({"label": label, "location": location, "pos": list(pos), "size_m": [0.5, 0.5, 0.9], "blocks_path": True,
                 "category": label}, **extra)


def _config(rooms, connections, entities=None, people=None, pets=None, robot_room=None):
    first = robot_room or sorted(rooms)[0]
    for conn in connections.values():
        a, b = conn["rooms"]
        rooms[a]["exits"].append(b)
        rooms[b]["exits"].append(a)
    return {"rooms": rooms, "connections": connections, "entities": entities or {}, "people": people or {},
            "pets": pets or {}, "robot": {"room": first, "pos": None}, "start_room": first}


def _narrow_kitchen(**agents):
    """6x4 kitchen with doorways at (0,1) and (5,2) and counters at (2,1),(2,2): column 2 is passable only at (2,0)
    and (2,3). Keyword agents: name=(group, pos)."""
    rooms = {"kitchen": _room(6, 4), "hall": _room(4, 4, "hallway"), "dining": _room(4, 4, "dining_room")}
    conns = {"conn_dining__kitchen": _conn("kitchen", (5, 2), "dining", (0, 1)),
             "conn_hall__kitchen": _conn("kitchen", (0, 1), "hall", (3, 1))}
    entities = {"counter_1": _block("kitchen", (2, 1)), "counter_2": _block("kitchen", (2, 2))}
    people, pets = {}, {}
    for aid, (group, pos) in agents.items():
        target = people if group == "people" else pets
        target[aid] = {"label": aid, "location": "kitchen", "pos": list(pos) if pos else None, "posture": "standing"}
        if group == "pets":
            target[aid]["species"] = "dog"
    return _config(rooms, conns, entities, people, pets, robot_room="hall")


def _door_to_door(state, avoid=()):
    return geometry.find_path(state, "kitchen", (0, 1), [(5, 2)], avoid_kinds=tuple(avoid))


class AgentAccessTests(unittest.TestCase):
    """G1: placements count people and pets already in the room as obstacles in their access checks, so a placed
    agent or item never walls off a doorway together with them (the strict reference route stays open)."""

    def test_agent_cell_does_not_wall_off_doorway_with_another_agent(self):
        config = _narrow_kitchen(resident_1=("people", (2, 0)), dog_1=("pets", None))
        placer = Placer(config, None, random.Random(0))
        cell = placer.agent_cell("kitchen", near=(2, 3))
        self.assertIsNotNone(cell)
        config["pets"]["dog_1"]["pos"] = cell
        state = placer.state()
        self.assertNotEqual(tuple(cell), (2, 3))
        self.assertIsNotNone(_door_to_door(state), "the dog at {} walls the doorways off with the resident".format(cell))

    def test_free_floor_cell_for_an_agent_keeps_strict_route(self):
        config = _narrow_kitchen(resident_1=("people", (2, 0)))
        state = Placer(config, None, random.Random(0)).state()
        cell = geometry.free_floor_cell(state, "kitchen", near=(2, 3), keep_access=True)
        self.assertIsNotNone(cell)
        state["pets"]["cat_1"] = {"location": "kitchen", "pos": cell, "species": "cat"}
        self.assertIsNotNone(_door_to_door(state))

    def test_loose_item_does_not_wall_off_doorway_with_an_agent(self):
        config = _narrow_kitchen(resident_1=("people", (2, 0)))
        config["entities"]["snap_trap_1"] = {"label": "snap trap", "location": "kitchen", "portable": True,
                                             "floor_hazard": "sharp", "size_m": [0.15, 0.1, 0.05]}
        state = Placer(config, None, random.Random(0)).state()
        record = config["entities"]["snap_trap_1"]
        cell = geometry.free_floor_cell(state, "kitchen", near=(2, 3), ignore=("snap_trap_1",), record=record, keep_access=True)
        self.assertIsNotNone(cell)
        record["pos"] = cell
        # core go_room/approach (tasks.skills.safe_avoid strict) never step on sharp things and treat agents as walls
        self.assertIsNotNone(_door_to_door(state, avoid=("sharp", "fragile", "sleeping", "person_down")))

    def test_furniture_placement_counts_agents(self):
        config = _narrow_kitchen(resident_1=("people", (2, 0)))
        placer = Placer(config, None, random.Random(0))
        check = placer._connectivity("kitchen", "all")
        self.assertFalse(check((2, 3), 1, 1), "a cabinet at (2,3) walls the doorways off with the resident")
        self.assertTrue(check((4, 0), 1, 1))


class LyingAvoidTests(unittest.TestCase):
    """G2: avoid kind "lying" (the kind realistic observations show) matches the agent markers sleeping/person_down."""

    def _state(self):
        # 5x5 bedroom: resident lying at (2,1) (marker person_down), a domain-written sleeping ring cell at (2,3);
        # domain path_blocked hooks may add a keep-out ring around the lying resident (rows 0-2), row 4 stays open
        rooms = {"bedroom": _room(5, 5, "bedroom")}
        rooms["bedroom"]["floor_hazards"] = [{"cell": [2, 3], "kind": "sleeping", "source": "cat_1"}]
        config = _config(rooms, {}, people={"resident_1": {"location": "bedroom", "pos": [2, 1], "posture": "lying"}})
        return config

    def test_find_path_lying_matches_person_down_and_sleeping(self):
        state = self._state()
        self.assertIn(("person_down", "resident_1"), geometry.hazard_cells(state, "bedroom")[(2, 1)])
        for avoid in (("lying",), ["lying", "wet"], ("sleeping", "person_down")):
            path = geometry.find_path(state, "bedroom", (0, 1), [(4, 1)], avoid_kinds=avoid, ignore=("resident_1",))
            self.assertIsNotNone(path, avoid)
            self.assertNotIn((2, 1), path, avoid)
            self.assertNotIn((2, 3), path, avoid)
        # without avoid the route goes straight through
        self.assertIn((2, 1), geometry.find_path(state, "bedroom", (0, 1), [(4, 1)], ignore=("resident_1",)))

    def test_path_blocked_hooks_see_the_aliases(self):
        seen = []

        def spy(state, rid, avoid_kinds):
            seen.append(tuple(avoid_kinds))
            return ()
        entry = (999, "tests.spy", spy)
        HOOKS["path_blocked"].append(entry)
        try:
            geometry.find_path(self._state(), "bedroom", (0, 1), [(4, 1)], avoid_kinds=["lying"])
        finally:
            HOOKS["path_blocked"].remove(entry)
        self.assertTrue(seen)
        self.assertIn("sleeping", seen[-1])
        self.assertIn("person_down", seen[-1])
        self.assertEqual(geometry.expand_avoid(["wet"]), ("wet",))


def _fixture():
    c = make_house(3, 1, 3)
    c["goal"] = None
    return c


class ConcealedAgentHazardTests(unittest.TestCase):
    """G3: people/pets that are concealed and not revealed add no agent-sourced grid hazards."""

    def _hazard_sources(self, env):
        return {h.get("source") for h in env.observe()["grid"]["hazards"]}

    def test_concealed_lying_person_has_no_grid_hazard(self):
        for mode in ("oracle", "realistic"):
            env = HouseEnv(_fixture(), perception=mode, seed=3)
            person = env.world.s["people"]["resident_1"]
            person.update(posture="lying", concealed=True)
            self.assertNotIn("resident_1", self._hazard_sources(env), mode)
            person["revealed"] = True
            self.assertIn("resident_1", self._hazard_sources(env), mode)

    def test_concealed_pet_has_no_grid_hazard(self):
        c = _fixture()
        c["pets"] = {"cat_1": {"label": "Miso", "species": "cat", "location": "room_1", "posture": "sleeping"}}
        env = HouseEnv(c, seed=3)
        env.world.s["pets"]["cat_1"].update(posture="sleeping", concealed=True)
        self.assertNotIn("cat_1", self._hazard_sources(env))
        env.world.s["pets"]["cat_1"]["concealed"] = False
        self.assertIn("cat_1", self._hazard_sources(env))


class TallItemRepairTests(unittest.TestCase):
    """G4: the repair pass moves a tall loose item to a neighbour room before standing it in front of a wall mount."""

    def _config(self):
        # laundry 5x3: washers at (2,0),(2,2) leave (2,1) as the only way to the cabinet at (4,1); every other free
        # cell outside the doorway clearance is in front of a wall shelf below the ladder's top
        rooms = {"laundry": _room(5, 3, "laundry_room"), "hall": _room(4, 4, "hallway")}
        conns = {"conn_hall__laundry": _conn("laundry", (0, 1), "hall", (3, 1))}
        entities = {"washer_1": _block("laundry", (2, 0), "washer"), "washer_2": _block("laundry", (2, 2), "washer"),
                    "cabinet_1": _block("laundry", (4, 1), "cabinet"),
                    "step_ladder_1": {"label": "step ladder", "location": "laundry", "pos": [2, 1], "portable": True,
                                      "size_m": [0.5, 0.5, 1.2], "category": "ladder"}}
        for n, cell in enumerate([(1, 0), (1, 2), (3, 0), (3, 2), (4, 0), (4, 2)], 1):
            entities["shelf_{}".format(n)] = {"label": "wall shelf", "location": "laundry", "pos": list(cell), "mounted": True,
                                              "mount_height_m": 1.0, "size_m": [0.5, 0.3, 0.05], "category": "shelf"}
        return _config(rooms, conns, entities, robot_room="hall")

    def test_tall_item_overflows_instead_of_hiding_a_mount(self):
        config = self._config()
        placer = Placer(config, None, random.Random(0))
        hidden = placer._hidden_by("laundry", 1.2)
        report = []
        repair._fix_loose_items(config, placer, "laundry", report)
        ladder = config["entities"]["step_ladder_1"]
        if ladder["location"] == "laundry":
            self.assertNotIn(tuple(ladder["pos"]), hidden, "ladder stands in front of a wall shelf")
        else:
            self.assertEqual(ladder["location"], "hall")
            self.assertNotIn(tuple(ladder["pos"]), placer._hidden_by("hall", 1.2))
        self.assertEqual(report, [])

    def test_hiding_cell_still_used_when_nothing_else_exists(self):
        config = self._config()
        del config["connections"]["conn_hall__laundry"]
        config["rooms"]["laundry"]["exits"] = []
        config["connections"]["conn_x"] = _conn("laundry", (0, 1), "porch", (0, 0))
        config["rooms"]["porch"] = _room(2, 2, "porch", outdoor=True)    # no indoor neighbour to overflow into
        placer = Placer(config, None, random.Random(0))
        repair._fix_loose_items(config, placer, "laundry", None)
        ladder = config["entities"]["step_ladder_1"]
        self.assertEqual(ladder["location"], "laundry")
        self.assertNotEqual(tuple(ladder["pos"]), (2, 1))     # it no longer cuts the only route to the cabinet


class WindowFootprintTests(unittest.TestCase):
    """G5: windows run along their wall (rot 90 on west/east walls) and their footprint stays on the grid."""

    def _check(self, config):
        for eid, rec in sorted(config["entities"].items()):
            if rec.get("category") != "window" or "connects" in rec or rec.get("location") not in config["rooms"]:
                continue
            room = config["rooms"][rec["location"]]
            cols, rows = geometry.grid_size(room)
            cells = geometry.footprint(rec)
            self.assertTrue(all(geometry.in_grid(room, c) for c in cells), (eid, rec["location"], cells, (cols, rows)))
            on_wall = (all(c[0] == 0 for c in cells) or all(c[0] == cols - 1 for c in cells) or
                       all(c[1] == 0 for c in cells) or all(c[1] == rows - 1 for c in cells))
            self.assertTrue(on_wall, (eid, rec["location"], cells, (cols, rows)))

    def test_along_wall_orients_and_clamps(self):
        room = _room(6, 8, "office")          # the seed-29 office grid
        cases = {(5, 7): [(5, 6), (5, 7)], (5, 0): [(5, 0), (5, 1)], (0, 3): [(0, 3), (0, 4)], (2, 0): [(2, 0), (3, 0)],
                 (4, 7): [(4, 7), (5, 7)]}
        for pos, cells in cases.items():
            rec = geometry.along_wall(room, {"pos": list(pos), "size_m": [1.0, 0.1, 1.2], "mounted": True})
            self.assertEqual(sorted(geometry.footprint(rec)), cells, pos)
        square = geometry.along_wall(room, {"pos": [5, 7], "size_m": [0.3, 0.3, 0.15]})
        self.assertNotIn("rot", square)
        self.assertEqual(square["pos"], [5, 7])

    def test_repro_seed_29_office(self):
        config = generate_house(29)
        self._check(config)
        win = config["entities"]["window_14"]
        self.assertEqual(win["location"], "office_1")
        self.assertTrue(all(geometry.in_grid(config["rooms"]["office_1"], c) for c in geometry.footprint(win)))

    def test_windows_on_grid_across_seeds(self):
        for seed in range(6):
            self._check(generate_house(seed))


def _light_state(config):
    return {"rooms": config["rooms"], "entities": config["entities"], "connections": config.get("connections", {}),
            "people": {}, "pets": {}, "robot": {"room": None}}


class OutdoorLightAccessTests(unittest.TestCase):
    """G6: wall-mounted outdoor lights are kernel access targets, so core (and every Placer-based) furniture keeps them
    serviceable; the layout audit reports one that something seals."""

    def _outdoor_lights(self, state):
        for rid in sorted(state["rooms"]):
            if not state["rooms"][rid].get("outdoor"):
                continue
            for eid in geometry.room_entities(state, rid):
                rec = state["entities"][eid]
                if rec.get("category") == "light" and rec.get("mounted") and rec.get("pos"):
                    yield rid, eid

    def test_outdoor_wall_light_is_an_access_target(self):
        state = _light_state(_generate(14, residents="minimal", pets="none"))
        lights = list(self._outdoor_lights(state))
        self.assertTrue(lights)
        for rid, eid in lights:
            self.assertIn(eid, geometry.access_targets(state, rid, level="all"), (rid, eid))

    def test_layout_audit_reports_a_sealed_outdoor_light(self):
        # the seed-14 deck as it was: table and a chair box the light's wall corner in
        rooms = {"deck_1": _room(7, 9, "deck", outdoor=True), "living": _room(6, 6, "living_room")}
        conns = {"conn_deck_1__living": _conn("deck_1", (0, 0), "living", (5, 5))}
        entities = {"outdoor_light_2": {"label": "deck light", "location": "deck_1", "pos": [6, 0], "mounted": True,
                                        "mount_height_m": 2.2, "size_m": [0.3, 0.3, 0.15], "category": "light", "type": "outdoor_light"},
                    "outdoor_table_1": _block("deck_1", (4, 1), "table", size_m=[1.5, 1.0, 0.75]),
                    "outdoor_chair_2": _block("deck_1", (4, 0), "chair")}
        config = _config(rooms, conns, entities, robot_room="living")
        placer = Placer(config, None, random.Random(0))
        report = []
        repair._audit(config, placer, report)
        self.assertTrue([l for l in report if l.startswith("unreachable deck_1: outdoor_light_2")], report)
        sealers = [l for l in report if "outdoor_light_2" in l][0]
        self.assertIn("outdoor_table_1", sealers)

    def test_reported_driveway_lights_stay_serviceable(self):
        # domain pieces placed through Placer (cleaning_supplies' carts beside tools_vehicles' car) now see the light
        cases = [(21, {"dwelling": "townhouse"}), (7, {"residents": "minimal", "pets": "none"}),
                 (38, {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]})]
        for seed, opts in cases:
            state = _light_state(_generate(seed, **opts))
            for rid, eid in self._outdoor_lights(state):
                targets = geometry.access_targets(state, rid, level="all")
                rep = geometry.access_report(state, rid, set(geometry.blocked_cells(state, rid, include_agents=False)), targets)
                self.assertIn(eid, rep["ok"], (seed, opts, rid, eid))

    def test_core_outdoor_furniture_keeps_lights_serviceable(self):
        cases = [(14, {"residents": "minimal", "pets": "none"}),
                 (4, {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]})]
        for seed, opts in cases:
            trace, report = {}, []
            config = _generate(seed, trace=trace, report=report, **opts)
            state = _light_state(config)
            blocked_by_rid = {}
            for rid, eid in self._outdoor_lights(state):
                if rid not in blocked_by_rid:
                    blocked_by_rid[rid] = set(geometry.blocked_cells(state, rid, include_agents=False))
                targets = geometry.access_targets(state, rid, level="all")
                ok = geometry.access_report(state, rid, blocked_by_rid[rid], targets)["ok"]
                if eid in ok:
                    continue
                # if sealed at all, no core piece may be one of the sealers
                for other in geometry.room_entities(state, rid):
                    if trace.get(other) == "core_furniture" and geometry.blocks(config["entities"][other]):
                        trial = geometry.access_report(state, rid, blocked_by_rid[rid] - set(geometry.footprint(config["entities"][other])), targets)
                        self.assertNotIn(eid, trial["ok"], (seed, rid, eid, "sealed by", other))
            self.assertFalse([line for line in report if "outdoor_light" in line and "core" in line], report)
        # the reported seed: deck_1's light is reachable again
        config = _generate(14, residents="minimal", pets="none")
        state = _light_state(config)
        targets = geometry.access_targets(state, "deck_1", level="all")
        rep = geometry.access_report(state, "deck_1", set(geometry.blocked_cells(state, "deck_1", include_agents=False)), targets)
        for rid, eid in self._outdoor_lights(state):
            if rid == "deck_1":
                self.assertIn(eid, rep["ok"])


class PerceptionAuditTests(unittest.TestCase):
    """G7: the leak auditor flags ids destroyed during a step and id-keyed JSON in true-id order, and does not flag
    values Realism shows from what the robot remembers after its knowledge lapsed."""

    def _env(self):
        c = _fixture()
        c["entities"]["toy_block_7"] = object_record("wooden block", "room_1", portable=True, material="wood", size_m=[0.2, 0.2, 0.2])
        return HouseEnv(c, perception="realistic", seed=3)

    def test_destroyed_ids_in_messages_are_flagged(self):
        env = self._env()
        start = set(env.world.s["entities"]) | {"tomato_9"}
        leaks = []
        perception_audit.check_message(env, "inspect", None, "You merge tomato_9 into the pot", leaks, start_ids=start)
        self.assertIn("id", [kind for kind, _, _ in leaks])
        leaks = []
        perception_audit.check_message(env, "inspect", None, "You merge tomato_9 into the pot", leaks)
        self.assertEqual(leaks, [])       # not an entity now nor at the start of the step: just a word

    def test_id_keyed_json_in_true_id_order_is_flagged(self):
        env = self._env()
        handle = env.realism.handles.handle
        ids = sorted(e for e in env.world.s["entities"] if not e.startswith("light"))
        pair = next((a, b) for a in ids for b in ids if a < b and handle(a) > handle(b))
        leaks = []
        perception_audit.check_message(env, "survey", None, json.dumps({handle(pair[0]): 1, handle(pair[1]): 2}), leaks)
        self.assertIn("order", [kind for kind, _, _ in leaks])
        leaks = []
        perception_audit.check_message(env, "survey", None, json.dumps([handle(pair[0]), handle(pair[1]), "x"]), leaks)
        self.assertIn("order", [kind for kind, _, _ in leaks])
        leaks = []
        perception_audit.check_message(env, "survey", None, json.dumps({handle(pair[1]): 2, handle(pair[0]): 1}), leaks)
        self.assertEqual(leaks, [])       # handle order is the safe order

    def test_run_level_order_evidence(self):
        pairs = [("a_1", "b_1"), ("c_1", "d_1"), ("e_1", "f_1")]
        systematic = [("observer:x", "obs x.list", p, True) for p in pairs]
        self.assertEqual([l[0] for l in perception_audit.order_leaks(systematic)], ["order"])
        self.assertEqual(perception_audit.order_leaks(systematic[:1] * 5), [])       # one pair seen 5 times: a coin flip
        mixed = systematic + [("observer:x", "obs x.list", ("g_1", "h_1"), False)]     # seen out of true-id order
        self.assertEqual(perception_audit.order_leaks(mixed), [])
        self.assertTrue(perception_audit.order_leaks([("action:m", "survey message JSON (top)", ("a", "b", "c", "d"), True)]))

    def test_remembered_values_are_not_tier_leaks(self):
        from house_sim.kernel.realism import INSPECT
        env = self._env()
        state = env.world.s
        record = state["entities"]["toy_block_7"]
        env.realism.knowledge.grant("toy_block_7", record, INSPECT, ["material"])
        record["pos"] = [1, 1]          # displaced unseen: the placement signature changes, the material does not
        self.assertLess(env.realism.knowledge.level("toy_block_7", record, "material"), INSPECT)
        obs = env.observe()
        shown = obs["visible_entities"][env.realism.handles.handle("toy_block_7")]
        self.assertEqual(shown.get("material"), "wood")       # Realism shows the remembered value by design
        leaks = []
        perception_audit.check_obs(env, obs, leaks, "t")
        self.assertFalse([l for l in leaks if l[0] == "tier" and "toy_block_7.material" in l[2]], leaks)

    def test_tier_rule_still_flags_unlearned_or_changed_values(self):
        from house_sim.kernel.realism import INSPECT
        env = self._env()
        record = env.world.s["entities"]["toy_block_7"]
        handle = env.realism.handles.handle("toy_block_7")
        fake = {"visible_entities": {handle: {"material": "wood"}}, "grid": {"hazards": []}}
        leaks = []
        perception_audit.check_obs(env, fake, leaks, "never-learned")
        self.assertTrue([l for l in leaks if l[0] == "tier" and "toy_block_7.material" in l[2]])
        env.realism.knowledge.grant("toy_block_7", record, INSPECT, ["material"])
        record["material"] = "plastic"     # changed since it was learned: showing the new value is a leak
        fake = {"visible_entities": {handle: {"material": "plastic"}}, "grid": {"hazards": []}}
        leaks = []
        perception_audit.check_obs(env, fake, leaks, "changed")
        self.assertTrue([l for l in leaks if l[0] == "tier" and "toy_block_7.material" in l[2]])


class WallMountOrientationTests(unittest.TestCase):
    """Red-team follow-up to G5 (opt-in, generator unchanged): Placer.mount(along_wall=True) lays an oblong wall mount
    (wall cabinet, closet shelf, hook rail) along a wall of its cell with the whole footprint on the grid."""

    def test_wall_layouts_offer_each_wall_of_a_cell(self):
        from house_sim.generator.place import wall_layouts
        room = _room(2, 3, "coat_closet")         # every cell of a two-column closet is on two walls
        rec = {"size_m": [1.0, 0.35, 0.05], "mounted": True}

        def cells(options):
            return [sorted(geometry.footprint(dict(rec, pos=list(p), rot=r))) for p, r in options]
        corner = cells(wall_layouts(room, rec, (0, 0)))
        self.assertIn([(0, 0), (0, 1)], corner)                 # along the west wall
        self.assertIn([(0, 0), (1, 0)], corner)                 # along the north wall
        south = cells(wall_layouts(room, rec, (1, 2)))
        self.assertIn([(1, 1), (1, 2)], south)                  # east wall, moved back onto the grid
        self.assertIn([(0, 2), (1, 2)], south)                  # flush with the south wall
        for option in corner + south:
            self.assertTrue(all(geometry.in_grid(room, c) for c in option))
        self.assertEqual(wall_layouts(room, {"size_m": [0.3, 0.3, 0.1]}, (1, 2)), [((1, 2), None)])   # square: as is

    def test_placer_mounts_oblong_things_along_their_wall_on_request(self):
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        for seed in range(12):
            config = _config({"office": _room(6, 7, "office")}, {})
            placer = Placer(config, reg, random.Random(seed))
            eid = placer.mount("office", "upper_cabinet", height=1.45, along_wall=True)
            rec = config["entities"][eid]
            room = config["rooms"]["office"]
            cols, rows = geometry.grid_size(room)
            cells = geometry.footprint(rec)
            self.assertTrue(all(geometry.in_grid(room, c) for c in cells), (seed, rec.get("pos"), rec.get("rot")))
            xs, ys = {c[0] for c in cells}, {c[1] for c in cells}
            along = (xs in ({0}, {cols - 1}) and len(ys) > 1) or (ys in ({0}, {rows - 1}) and len(xs) > 1)
            self.assertTrue(along, (seed, cells))

    def test_default_mount_keeps_generated_layouts(self):
        # without along_wall the candidate cells, their order and the rng draws are exactly the old ones
        from house_sim.kernel.registry import default_registry
        reg = default_registry()
        for seed in range(6):
            config = _config({"office": _room(6, 7, "office")}, {})
            placer = Placer(config, reg, random.Random(seed))
            rec = config["entities"][placer.mount("office", "upper_cabinet", height=1.45)]
            self.assertNotEqual(rec.get("rot"), 90)              # the type's orientation is kept as given


class AuditFollowUpTests(unittest.TestCase):
    """Red-team follow-ups for tools/perception_audit.py: ids destroyed in an earlier step, descriptive words that equal
    a hand-written id, and person/pet ids in a dark observation."""

    def _env(self):
        c = _fixture()
        c["entities"]["toy_block_7"] = object_record("wooden block", "room_1", portable=True, material="wood", size_m=[0.2, 0.2, 0.2])
        c["people"]["resident_1"]["pos"] = [0, 1]
        return HouseEnv(c, perception="realistic", seed=3)

    def test_ids_destroyed_in_an_earlier_step_are_flagged(self):
        env = self._env()
        env.realism._ever.add("tomato_9")              # destroyed some steps ago
        leaks = []
        perception_audit.check_message(env, "inspect", None, "You see tomato_9", leaks)
        self.assertIn("id", [kind for kind, _, _ in leaks])
        leaks = []
        perception_audit.check_obs(env, {"sounds": ["a squish near tomato_9"], "visible_entities": {}, "grid": {"hazards": []}}, leaks, "t")
        self.assertIn("id", [kind for kind, _, _ in leaks])

    def test_descriptive_word_equal_to_a_hand_written_id_is_not_a_leak(self):
        env = self._env()
        self.assertIn("sink", env.world.s["entities"])
        handle = env.realism.handles.handle("sink")
        leaks = []
        perception_audit.check_obs(env, {"visible_entities": {handle: {"label": "sink", "kind": "sink"}}, "grid": {"hazards": []}}, leaks, "t")
        self.assertEqual([l for l in leaks if l[0] == "id"], [])
        leaks = []
        perception_audit.check_obs(env, {"visible_entities": {handle: {"label": "toy_block_7"}}, "grid": {"hazards": []}}, leaks, "t")
        self.assertIn("id", [kind for kind, _, _ in leaks])     # an id-shaped word still counts

    def test_person_ids_in_a_dark_observation_are_flagged(self):
        env = self._env()
        fake = {"dark": True, "people": {"resident_1": {"label": "person"}}, "visible_entities": {}, "grid": {"hazards": []}}
        leaks = []
        perception_audit.check_obs(env, fake, leaks, "dark")
        self.assertIn("id", [kind for kind, _, _ in leaks])
        fake["dark"] = False
        leaks = []
        perception_audit.check_obs(env, fake, leaks, "lit")
        self.assertEqual([l for l in leaks if l[0] == "id"], [])


if __name__ == "__main__":
    unittest.main()
