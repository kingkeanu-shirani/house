"""Tests for kernel/sight.py (TIER1_CAMERA sections 4-7, 10.1 and 17; worked numbers for the 1.50 m eye of
TIER1_INTEGRATION 1.2 / conflict 4) and the registry's type "view" blocks.

The stage-A siblings (body.eye_height_m, phys.support.held_envelope and resting_size) are switched off for this module
so these tests check sight's own stand-ins; BridgeTests checks the delegation with fakes."""
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

from house_sim.kernel import geometry, sight
from house_sim.kernel.predicates import digest, root_room
from house_sim.kernel.registry import TypeRegistry, default_registry

ROOT = Path(__file__).resolve().parent.parent
BRIDGED = ("body.eye_height_m", "phys.support.held_envelope", "phys.support.resting_size")
_SAVED = {}


def setUpModule():
    for key in BRIDGED:
        _SAVED[key] = sight._BRIDGES.get(key, "missing")
        sight._BRIDGES[key] = None


def tearDownModule():
    for key, value in _SAVED.items():
        if value == "missing":
            sight._BRIDGES.pop(key, None)
        else:
            sight._BRIDGES[key] = value


class Scene(object):
    """A hand-built one-room state (cols x rows cells of 0.5 m) with the robot in it."""

    def __init__(self, cols=12, rows=12, rid="r"):
        self.rid = rid
        self.state = {"rooms": {rid: {"size_m": [cols * 0.5, rows * 0.5], "type": "living_room", "label": "room"}},
                      "entities": {}, "people": {}, "pets": {}, "connections": {}, "time_s": 5 * 3600,
                      "robot": {"room": rid, "pos": [0, 0], "posture": "standing", "facing_deg": 90.0,
                                "hands": {"left": None, "right": None}}}

    def add(self, eid, type_name, location=None, **props):
        self.state["entities"][eid] = default_registry().make(type_name, location or self.rid, **props)
        return eid

    def eye(self, c, r, posture="standing"):
        robot = self.state["robot"]
        robot["pos"], robot["posture"] = [c, r], posture
        return sight.Eye(self.rid, (c + 0.5) * 0.5, (r + 0.5) * 0.5, sight.eye_height_m(self.state), ("robot",))

    def vis(self, eid, c, r, posture="standing"):
        return sight.visible_fraction(self.state, eid, self.eye(c, r, posture))[0]


def head(rid, x, y, z, yaw, tilt=-10.0, hfov=69.0, vfov=42.0):
    return sight.Viewer(rid, x, y, z, yaw, tilt, hfov, vfov, 12.0, sight.HW["head"]["focal_px"], "head", ("robot",))


# ----- registry and view properties ---------------------------------------------------------------------------------
class RegistryViewBlockTests(unittest.TestCase):
    def _registry(self, types):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "extra" / "t.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"domain": "t", "types": types}), encoding="utf-8")
        return TypeRegistry(folder=folder, extra_files=[path])

    def test_view_blocks_stay_out_of_types_and_records_and_merge_along_is_a(self):
        reg = self._registry({
            "base": {"props": {"category": "thing"}, "view": {"porosity": 0.4, "faces": {"label": 0}, "doc": "why"}},
            "child": {"is_a": "base", "view": {"faces": {"rim": "all"}, "clear_below_m": 0.1}},
            "plain": {"is_a": "base"}})
        self.assertNotIn("view", reg.types["base"])
        self.assertNotIn("view", reg.make("child", "room"))
        self.assertNotIn("view", reg.resolve("child"))
        self.assertEqual(reg.view_props("child"), {"porosity": 0.4, "faces": {"label": 0, "rim": "all"},
                                                   "clear_below_m": 0.1})
        self.assertEqual(reg.view_props("plain"), {"porosity": 0.4, "faces": {"label": 0}})
        self.assertEqual(reg.view_props("unknown_type"), {})

    def test_a_view_block_must_be_an_object_and_other_keys_are_still_refused(self):
        with self.assertRaises(ValueError):
            self._registry({"bad": {"view": [1, 2]}})
        with self.assertRaises(ValueError):
            self._registry({"bad": {"looks": {}}})

    def test_core_view_blocks_use_only_known_keys_and_never_reach_records(self):
        # every loaded view block (core and the domains') uses only the keys sight reads and stays out of records; the
        # core types (data/types/core.json) also explain theirs ("doc"). Adoption round: domain blocks are the domains'
        # own to document (food's cartons carry none), so the "doc" pin covers the core file it was written for.
        reg = default_registry()
        self.assertTrue(reg.view)
        for name, block in reg.view.items():
            self.assertLessEqual(set(block) - {"doc"}, set(sight.VIEW_DEFAULTS), name)
            self.assertNotIn("view", reg.make(name, "room"), name)
        core = json.loads((ROOT / "house_sim" / "data" / "types" / "core.json").read_text(encoding="utf-8"))["types"]
        blocks = {name: spec["view"] for name, spec in core.items() if isinstance(spec, dict) and "view" in spec}
        self.assertTrue(blocks)
        for name, block in blocks.items():
            self.assertIn("doc", block, name)


class ViewPropsTests(unittest.TestCase):
    def setUp(self):
        self.s = Scene()

    def props(self, eid):
        return sight.view_props(self.s.state, eid)

    def test_category_rows_follow_the_nearest_name_not_the_misleading_parent(self):
        self.s.add("sofa", "sofa", pos=[0, 0])
        self.s.add("chair", "chair", pos=[3, 0])
        self.s.add("nightstand", "nightstand", pos=[6, 0])
        self.s.add("table", "table", pos=[0, 4])
        self.s.add("desk", "desk", pos=[4, 4])
        self.assertEqual(self.props("sofa")["clear_below_m"], 0.10)          # sofa is_a chair, but is a sofa
        self.assertEqual(self.props("sofa")["porosity"], 0.0)
        self.assertEqual((self.props("chair")["clear_below_m"], self.props("chair")["porosity"]), (0.42, 0.6))
        self.assertIsNone(self.props("nightstand")["slab_m"])                 # nightstand is_a table, but a cabinet
        self.assertEqual(self.props("nightstand")["opening"], "front")
        self.assertEqual((self.props("table")["slab_m"], self.props("table")["below_porosity"]), (0.04, 0.85))
        self.assertEqual(self.props("desk")["below_porosity"], 0.4)

    def test_type_blocks_then_record_views_override_the_category_row(self):
        self.s.add("crib", "crib", pos=[0, 0])
        self.s.add("bench", "bench", pos=[3, 0])
        self.s.add("lamp", "lamp", pos=[6, 0], view={"porosity": 0.2})
        self.assertEqual((self.props("crib")["porosity"], self.props("crib")["clear_below_m"]), (0.5, 0.15))
        self.assertEqual((self.props("bench")["slab_m"], self.props("bench")["porosity"]), (0.04, 0.0))
        self.assertEqual(self.props("lamp")["porosity"], 0.2)

    def test_materials_set_transparency_and_gloss_and_openings_default_by_portability(self):
        self.s.add("jar", "box", material="glass")
        self.s.add("steel", "box", material="stainless_steel")
        self.s.add("crate", "box")
        self.s.add("ward", "wardrobe", pos=[0, 0])
        self.s.add("drawer", "drawer", pos=[4, 0])
        self.s.add("thing", "generic_item")
        self.assertEqual(self.props("jar")["porosity"], 0.9)
        self.assertTrue(self.props("jar")["glossy"])
        self.assertTrue(self.props("steel")["glossy"])
        self.assertEqual(self.props("steel")["porosity"], 0.0)
        self.assertFalse(self.props("crate")["glossy"])
        self.assertEqual(self.props("crate")["opening"], "top")
        self.assertEqual(self.props("ward")["opening"], "front")
        self.assertEqual(self.props("drawer")["opening"], "top")
        self.assertEqual(self.props("thing")["opening"], "top")           # portable, no row
        self.assertIs(sight.view_props(self.s.state, "missing"), sight.VIEW_DEFAULTS)

    def test_domain_style_records_get_rows_from_their_type_name_without_a_registered_type(self):
        self.s.state["entities"]["fridge"] = {"type": "refrigerator", "is_a": ["cooking_appliance", "fixture"],
                                              "category": "appliance", "location": "r", "pos": [0, 0],
                                              "size_m": [0.84, 0.74, 1.78]}
        props = self.props("fridge")
        self.assertEqual((props["opening"], props["interior_light"], props["behind_gap_m"]), ("front", True, 0.08))


# ----- conventions, constants and placement ------------------------------------------------------------------------
class ConventionTests(unittest.TestCase):
    def test_angles_rounding_and_optics(self):
        self.assertEqual(sight.grid_deg(0), 270.0)          # north is -y
        self.assertEqual(sight.grid_deg(90), 0.0)
        self.assertEqual(sight.grid_deg(180), 90.0)
        self.assertEqual(sight.wrap_deg(190), -170.0)
        self.assertEqual(sight.wrap_deg(-180), 180.0)
        self.assertEqual(sight.wrap_deg(540), 180.0)
        self.assertEqual([sight.quarter(f) for f in (0.0, 0.124, 0.125, 0.374, 0.6, 0.874, 0.875, 1.3)],
                         [0.0, 0.0, 0.25, 0.25, 0.5, 0.75, 1.0, 1.0])
        self.assertEqual(round(sight.HW["head"]["focal_px"]), 931)
        self.assertEqual(round(sight.HW["wrist"]["focal_px"]), 337)
        self.assertEqual(round(sight.HW["depth"]["focal_px"]), 447)
        self.assertNotIn("height_m", sight.HW["head"])       # eye heights belong to the body profile

    def test_eye_heights_follow_the_profile_table_posture_fall_and_aid(self):
        s = Scene()
        robot = s.state["robot"]
        for posture, z in (("standing", 1.5), ("crouching", 0.95), ("kneeling", 0.6), ("sitting", 0.95),
                           ("dancing", 1.5)):
            robot["posture"] = posture
            self.assertEqual(sight.eye_height_m(s.state), z, posture)
        robot["fallen"] = True
        self.assertEqual(sight.eye_height_m(s.state), 0.3)
        self.assertEqual(sight.eye_height_m(s.state, "kneeling"), 0.6)
        robot["fallen"] = False
        robot["posture"] = "standing"
        s.state["entities"]["stool"] = {"location": "r", "standing_height_m": 0.48}
        robot["standing_on"] = "stool"
        self.assertEqual(sight.eye_height_m(s.state), 1.98)
        s.state["entities"]["stool"].pop("standing_height_m")
        self.assertEqual(sight.eye_height_m(s.state), 1.9)       # 0.4 m when the aid has no standing height
        robot["body_profile"] = "humanoid_compact"
        self.assertEqual(sight.eye_height_m(s.state, "standing"), 1.2)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.s = Scene()
        self.st = self.s.state

    def box(self, eid):
        b = sight.entity_box(self.st, eid)
        return (b.x0, b.y0, b.x1, b.y1, b.z0, b.z1)

    def test_room_located_footprints_with_rotation_and_continuous_positions(self):
        self.s.add("ward", "wardrobe", pos=[4, 0])
        self.s.add("table", "table", pos=[0, 4], rot=90)
        self.s.add("bin", "box", pos=[8, 8], xy_m=[4.1, 4.2])
        self.s.add("cab", "upper_cabinet", pos=[0, 0], mounted=True, mount_height_m=1.4)
        self.assertEqual(self.box("ward"), (2.0, 0.0, 3.2, 0.6, 0.0, 2.0))
        self.assertEqual(self.box("table"), (0.0, 2.0, 0.8, 3.2, 0.0, 0.75))
        self.assertEqual(self.box("bin")[:2], (4.1, 4.2))
        self.assertEqual(self.box("cab")[4:], (1.4, 2.1))
        self.assertIsNone(sight.entity_box(self.st, "nothing"))

    def test_surface_positions_use_spos_with_the_people_axis_swap_and_centre_by_default(self):
        self.s.add("table", "table", pos=[2, 2])                               # x 1.0-2.2, y 1.0-1.8
        self.s.add("cup", "generic_item", "table", spos=[0.2, 0.3], size_m=[0.1, 0.1, 0.12])
        self.s.add("plate", "generic_item", "table", size_m=[0.2, 0.2, 0.02])
        self.assertEqual(self.box("cup"), (1.2, 1.3, 1.3, 1.4, 0.75, 0.87))
        self.assertEqual(self.box("plate")[:4], (1.5, 1.3, 1.7, 1.5))
        self.st["entities"]["table"]["rot"] = 90                              # x 1.0-1.8, y 1.0-2.2
        self.assertEqual(self.box("cup")[:4], (1.3, 1.2, 1.4, 1.3))

    def test_containers_pack_rows_left_to_right_by_hash_from_the_opening_back(self):
        self.s.add("cab", "cabinet", pos=[4, 0], open=True, size_m=[0.8, 0.6, 0.9])   # front faces south (+y)
        for k in range(4):
            self.s.add("item%d" % k, "generic_item", "cab", relation="in", size_m=[0.3, 0.2, 0.1])
        boxes = {k: sight.entity_box(self.st, "item%d" % k) for k in range(4)}
        rows = sorted({round(b.y1, 3) for b in boxes.values()})
        self.assertEqual(rows, [0.4, 0.6])                        # two rows: 0.6 at the opening, 0.4 behind it
        front = [k for k, b in boxes.items() if b.y1 == 0.6]
        self.assertEqual(len(front), 2)                           # 0.3 + 0.3 fit the 0.8 m width, a third does not
        self.assertEqual(sorted(boxes[k].x0 for k in front), [2.0, 2.3])
        order = sorted(range(4), key=lambda k: __import__("hashlib").blake2b(
            ("item%d" % k).encode(), digest_size=8).hexdigest())
        self.assertEqual(sorted(front), sorted(order[:2]))
        self.assertTrue(all(b.z0 == 0.05 for b in boxes.values()))          # the kernel's inner floor
        self.s.add("pinned", "generic_item", "cab", relation="in", size_m=[0.1, 0.1, 0.1], inner_spos=[0.6, 0.4])
        b = sight.entity_box(self.st, "pinned")
        self.assertEqual((b.x0, b.y0, b.x1, b.y1), (2.6, 0.1, 2.7, 0.2))      # front-left corner 0.6 across, 0.4 back

    def test_shelves_under_behind_hung_part_held_and_agents(self):
        self.s.add("shelf", "bookshelf", pos=[0, 0], shelf_heights=[0.05, 0.6, 1.2])
        self.s.add("book", "book", "shelf", relation="in", shelf=2)
        self.assertEqual(sight.entity_box(self.st, "book").z0, 1.2)
        self.s.add("bed", "bed", pos=[6, 4])                                  # x 3.0-4.6, y 2.0-4.0
        self.s.add("toy", "generic_item", "bed", relation="under", size_m=[0.1, 0.1, 0.1])
        self.assertEqual(self.box("toy"), (3.75, 2.95, 3.85, 3.05, 0.0, 0.1))
        self.s.add("hooks", "coat_hooks", pos=[0, 10], mounted=True, mount_height_m=1.6, hang_height_m=0.05)
        self.s.add("coat", "generic_item", "hooks", relation="hung", size_m=[0.5, 0.2, 0.9])
        b = sight.entity_box(self.st, "coat")
        self.assertAlmostEqual(geometry.height_m(self.st, "coat"), 1.65)
        self.assertEqual((b.z0, b.z1), (0.75, 1.65))                         # hangs below the hook line
        self.s.add("panel", "cabinet", pos=[10, 0], size_m=[0.4, 0.2, 0.6], mounted=True, mount_height_m=1.2)
        self.s.add("knob", "generic_item", "panel", relation="part", size_m=[0.05, 0.05, 0.05])
        b = sight.entity_box(self.st, "knob")
        self.assertTrue(1.2 <= b.z0 and b.z1 <= 1.8 and 5.0 < b.x0 < 5.4)
        self.st["robot"].update(pos=[2, 6], facing_deg=0.0)
        self.s.add("mug", "generic_item", "robot", size_m=[0.1, 0.1, 0.1])
        self.st["robot"]["hands"]["right"] = "mug"
        b = sight.entity_box(self.st, "mug")
        self.assertAlmostEqual((b.x0 + b.x1) / 2.0, 1.25 + 0.35, places=3)  # 0.35 m ahead of the body axis
        self.assertAlmostEqual((b.z0 + b.z1) / 2.0, 1.0, places=3)           # centred on the hand
        self.st["people"]["p1"] = {"location": "r", "pos": [5, 5], "posture": "standing", "facing_deg": 0.0}
        b = sight.entity_box(self.st, "p1")
        self.assertEqual((b.x0, b.y0, b.z1), (2.5, 2.5, 1.67))

    def test_fronts_follow_explicit_views_walls_corners_centre_and_parents(self):
        self.s.add("ward", "wardrobe", pos=[4, 0])
        self.s.add("south", "cabinet", pos=[4, 10])
        self.s.add("west", "cabinet", pos=[0, 5], rot=90)
        self.s.add("corner", "wardrobe", pos=[0, 0])                         # 1.2 wide along x: the north wall
        self.s.add("middle", "cabinet", pos=[2, 5])
        self.s.add("centre", "box", xy_m=[2.775, 2.85], pos=[5, 5], size_m=[0.45, 0.3, 0.3])
        self.s.add("explicit", "cabinet", pos=[8, 8], view={"front_deg": 45})
        self.s.add("cup", "generic_item", "ward", yaw_deg=30)
        f = lambda eid: sight.front_deg(self.st, eid)      # noqa: E731
        self.assertEqual((f("ward"), f("south"), f("west"), f("corner")), (90.0, 270.0, 0.0, 90.0))
        self.assertAlmostEqual(f("middle"), math.degrees(math.atan2(3.0 - 2.8, 3.0 - 1.4)) % 360, places=1)
        self.assertEqual(f("centre"), 90.0)
        self.assertEqual(f("explicit"), 45.0)
        self.assertEqual(f("cup"), 120.0)
        self.st["robot"]["facing_deg"] = 10.0
        self.s.add("held", "generic_item", "robot")
        self.assertEqual(f("held"), 190.0)

    def test_sample_counts_and_the_side_facing_the_eye(self):
        self.s.add("ring", "generic_item", size_m=[0.02, 0.02, 0.01], pos=[1, 1])
        self.s.add("mug", "generic_item", size_m=[0.1, 0.1, 0.12], pos=[2, 1])
        self.s.add("crate", "box", pos=[4, 4], size_m=[0.4, 0.4, 0.4])          # x 2.0-2.4, y 2.0-2.4
        self.assertEqual(sight.sample_points(self.st, "ring"), [(0.51, 0.51, 0.01)])
        self.assertEqual(len(sight.sample_points(self.st, "mug")), 2)
        east = sight.sample_points(self.st, "crate", ("r", 5.0, 2.2, 1.5))
        self.assertEqual(len(east), 5)
        self.assertEqual([round(p[0], 2) for p in east[2:]], [2.38, 2.38, 2.02])      # near pair east, far edge west
        self.assertTrue(all(p[2] == 0.36 for p in east[2:]))                          # at 0.9 h
        north = sight.sample_points(self.st, "crate", ("r", 2.2, 0.1, 1.5))
        self.assertEqual([round(p[1], 2) for p in north[2:]], [2.02, 2.02, 2.38])
        self.st["people"]["kid"] = {"location": "r", "pos": [8, 8], "posture": "standing", "role": "child", "age": 5}
        head_z, torso_z = [p[2] for p in sight.sample_points(self.st, "kid")]
        self.assertEqual(head_z, 0.95)
        self.assertLess(torso_z, head_z)


# ----- the viewshed and the relation rules (spec 17.1; worked numbers for the 1.50 m eye) ----------------------------
class OcclusionTests(unittest.TestCase):
    def test_under_bed_needs_kneeling_far_enough_back(self):
        # 5 cm toy 0.5 m inside the bed's near (west) edge, clearance 0.25 m: visible iff d_near >= 2.5 (ez - 0.25):
        # kneeling 0.875 m, crouching 1.75 m, standing 3.125 m (conflict 4)
        s = Scene(20, 8)
        s.add("bed", "bed", pos=[8, 2])                                          # west edge x = 4.0
        s.add("toy", "generic_item", "bed", relation="under", spos=[0.475, 0.975], size_m=[0.05, 0.05, 0.05])
        seen = {}
        for posture in ("kneeling", "crouching", "standing"):
            seen[posture] = [round(4.0 - (c + 0.5) * 0.5, 2) for c in range(8) if s.vis("toy", c, 3, posture)]
        self.assertEqual(min(seen["kneeling"]), 1.25)          # 0.75 m is too close
        self.assertEqual(min(seen["crouching"]), 2.25)         # 1.75 m sits exactly on the edge: blocked
        self.assertEqual(min(seen["standing"]), 3.25)
        self.assertNotIn(1.25, seen["standing"])

    def test_wardrobe_top_hides_a_low_box_up_close_but_not_from_across_the_room(self):
        # top 2.0 m, eye 1.5, box 0.3 m behind the front edge: hidden while h < 0.5 x 0.3 / d_edge
        s = Scene(12, 12)
        s.add("ward", "wardrobe", pos=[4, 0])                                    # front edge y = 0.6
        s.add("box", "generic_item", "ward", spos=[0.5, 0.25], size_m=[0.1, 0.05, 0.1])
        self.assertEqual(s.vis("box", 5, 2), 0.0)               # d_edge 0.65: needs 0.23 m
        self.assertEqual(s.vis("box", 5, 3), 0.0)               # 1.15 m: needs 0.13 m
        self.assertGreater(s.vis("box", 5, 7), 0.0)             # 3.15 m: needs 0.048 m

    def test_a_sofa_hides_a_floor_toy_behind_it_but_not_a_tall_lamp(self):
        s = Scene(10, 10)
        s.add("sofa", "sofa", pos=[3, 0])                                        # against the north wall, top 0.85
        s.add("toy", "generic_item", "sofa", relation="behind", size_m=[0.1, 0.1, 0.1])
        s.add("lamp", "generic_item", "sofa", relation="behind", size_m=[0.1, 0.1, 0.95])
        for c, r in ((4, 2), (4, 4), (4, 7), (0, 0), (8, 0)):
            self.assertEqual(s.vis("toy", c, r), 0.0)            # in front of it or beside its narrow gap
            self.assertGreater(s.vis("lamp", c, r), 0.0)
        box = sight.entity_box(s.state, "toy")
        self.assertGreaterEqual(box.y0, 0.0)                    # the sofa stands far enough off the wall for it
        self.assertEqual(box.y1, 0.1)
        s.state["entities"]["sofa"]["pos"] = [3, 2]              # pulled out into the room (y 1.0-1.9)
        self.assertEqual(s.vis("toy", 4, 5), 0.0)
        self.assertEqual(s.vis("toy", 1, 0), 1.0)                # walking round behind it

    def test_items_under_a_table_stay_visible_and_a_ray_under_a_table_then_over_a_box_passes(self):
        s = Scene(12, 4)
        s.add("table", "table", pos=[2, 1])                                      # x 1.0-2.2, slab 0.71-0.75
        s.add("box", "box", pos=[5, 1], size_m=[0.45, 0.45, 0.3])               # x 2.5-2.95
        s.add("ball", "generic_item", "table", relation="under", size_m=[0.2, 0.2, 0.2])
        self.assertEqual(s.vis("ball", 0, 1), 0.0)              # 0.75 m from the edge the top hides its centre
        self.assertGreater(s.vis("ball", 11, 1), 0.0)           # from across the room (over the box)
        self.assertGreater(s.vis("ball", 0, 1, "kneeling"), 0.0)
        eye = s.eye(0, 1, "kneeling")
        geom = sight.room_geom(s.state, "r")
        vs = sight.viewshed(geom, eye)
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs, eye, (4.25, 0.75, 0.35)), 0.85)  # C20
        self.assertEqual(sight.point_transmission(s.state, geom, vs, eye, (4.25, 0.75, 0.1)), 0.0)          # box

    def test_an_open_cabinet_is_seen_into_from_its_front_only(self):
        s = Scene(10, 10)
        s.add("cab", "cabinet", pos=[4, 0], open=True)
        s.add("cup", "generic_item", "cab", relation="in", size_m=[0.08, 0.08, 0.1])
        self.assertEqual(s.vis("cup", 4, 4), 1.0)
        self.assertEqual(s.vis("cup", 9, 1), 0.0)               # beside it: outside the 75-degree front cone
        s.state["entities"]["cab"]["open"] = False
        self.assertEqual(s.vis("cup", 4, 4), 0.0)
        s.state["entities"]["cab"]["material"] = "glass"        # closed glass doors: seen through at 0.9
        self.assertEqual(s.vis("cup", 4, 4), 1.0)

    def test_a_taller_front_item_hides_the_back_row_except_from_the_side(self):
        s = Scene(10, 10)
        s.add("cab", "cabinet", pos=[4, 0], open=True, size_m=[0.8, 0.6, 0.9])
        s.add("front", "generic_item", "cab", relation="in", size_m=[0.2, 0.1, 0.3], inner_spos=[0.3, 0.0])
        s.add("back", "generic_item", "cab", relation="in", size_m=[0.2, 0.1, 0.1], inner_spos=[0.3, 0.3])
        for c, r in ((4, 3), (4, 8)):
            self.assertEqual(s.vis("front", c, r), 1.0)
            self.assertEqual(s.vis("back", c, r), 0.0)
        self.assertGreater(s.vis("back", 7, 3), 0.0)
        s.state["entities"]["front"]["inner_spos"] = [0.0, 0.0]          # moved aside
        self.assertGreater(s.vis("back", 4, 8), 0.0)

    def test_a_top_open_box_hides_a_short_item_at_its_bottom(self):
        s = Scene(10, 10)
        s.add("counter", "counter", pos=[2, 0], size_m=[1.0, 0.6, 0.9])
        s.add("bin", "box", "counter", open=True, size_m=[0.4, 0.3, 0.3], spos=[0.3, 0.15])    # rim 1.2 m
        s.add("low", "generic_item", "bin", relation="in", size_m=[0.05, 0.05, 0.05], inner_spos=[0.2, 0.2])
        s.add("tall", "generic_item", "bin", relation="in", size_m=[0.05, 0.05, 0.35], inner_spos=[0.1, 0.2])
        self.assertEqual(s.vis("low", 3, 2), 0.0)
        self.assertEqual(s.vis("tall", 3, 2), 1.0)
        self.assertEqual(s.vis("tall", 3, 2, "kneeling"), 0.0)          # the eye is below the rim
        s.add("crate", "box", pos=[6, 6], open=True, size_m=[0.45, 0.45, 0.3])          # on the floor: rim 0.3
        s.add("sock", "generic_item", "crate", relation="in", size_m=[0.05, 0.05, 0.05], inner_spos=[0.2, 0.0])
        s.add("shoe", "generic_item", "crate", relation="in", size_m=[0.05, 0.05, 0.05], inner_spos=[0.2, 0.35])
        self.assertEqual(sight.entity_box(s.state, "sock").y0, 3.0)     # the front faces the room centre (north)
        self.assertEqual(s.vis("sock", 6, 8), 1.0)                       # the far side, looking down from 0.9 m
        self.assertEqual(s.vis("shoe", 6, 8), 0.0)                       # tucked against the near wall
        self.assertEqual(s.vis("sock", 6, 9, "kneeling"), 0.0)          # too shallow from 0.6 m

    def test_porous_occluders_people_pets_and_the_robot_attenuate(self):
        s = Scene(12, 4)
        s.add("chair", "chair", pos=[3, 1])                                      # x 1.5-2.0, porous 0.42-0.9 m
        geom = sight.room_geom(s.state, "r")
        eye = s.eye(0, 1, "kneeling")                                            # (0.25, 0.75, 0.6)
        vs = sight.viewshed(geom, eye)
        low, high = (5.25, 0.75, 0.6), (5.25, 0.75, 1.0)
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs, eye, low), 0.6)
        s.state["people"]["p1"] = {"location": "r", "pos": [5, 1], "posture": "standing"}
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs, eye, low), 0.6 * 0.3)
        s.state["pets"]["dog"] = {"location": "r", "pos": [4, 1], "species": "dog", "size": "large"}
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs, eye, low), 0.6 * 0.3 * 0.5)
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs, eye, high), 0.6 * 0.3)  # over the dog
        other = sight.Eye("r", 4.25, 0.75, 1.55, ("p1",))                        # a person's eyes: the robot occludes
        s.state["robot"].update(pos=[6, 1], posture="standing")
        vs2 = sight.viewshed(geom, other)
        self.assertAlmostEqual(sight.point_transmission(s.state, geom, vs2, other, (0.25, 0.75, 1.2)), 0.4)
        robot_eye = ("r", 0.25, 0.75, 1.5, ("robot",))
        self.assertEqual(sight.visible_fraction(s.state, "p1", robot_eye), (1.0, (2.75, 0.75, 1.55)))
        s.state["people"]["p2"] = {"location": "r", "pos": [3, 1], "posture": "standing"}
        frac, best = sight.visible_fraction(s.state, "p1", robot_eye)
        self.assertEqual(frac, 0.25)                                    # head and torso behind p2 (0.3 each)
        self.assertEqual(best[2], 1.55)

    def test_open_door_leaves_and_hook_occluders_block_their_cells(self):
        s = Scene(8, 8)
        s.state["rooms"]["h"] = {"size_m": [3.0, 4.0], "type": "hallway"}
        s.state["connections"]["c1"] = {"rooms": ["r", "h"], "cells": {"r": [0, 3], "h": [5, 3]}, "door": "d1"}
        s.add("d1", "door", "r", pos=[0, 3], connects=["r", "h"], swing_into="r", open=False)
        geom = sight.room_geom(s.state, "r")
        eye = s.eye(1, 0)
        target = (0.75, 3.75, 1.0)                                      # behind the leaf's diagonal cell
        self.assertEqual(sight.point_transmission(s.state, geom, sight.viewshed(geom, eye), eye, target), 1.0)
        s.state["entities"]["d1"]["open"] = True
        geom2 = sight.room_geom(s.state, "r")
        self.assertNotEqual(geom.sig, geom2.sig)
        target = (0.75, 2.75 + 2.0, 1.0)
        far = s.eye(1, 1)
        self.assertEqual(sight.point_transmission(s.state, geom2, sight.viewshed(geom2, far), far, target), 0.0)
        layer = {"porosity": 0.6}

        def hooks(name, *args):
            if name != "view_occluders":
                return []
            return [[([(c, 4) for c in range(8)], 0.0, 2.5, layer["porosity"], "smoke_layer")]]   # one hook's answer
        with mock.patch.object(sight, "run_hooks", side_effect=hooks):
            geom3 = sight.room_geom(s.state, "r")                       # a smoke layer across row 4 (y 2.0-2.5)
            vs3 = sight.viewshed(geom3, far)
            self.assertEqual(sight.point_transmission(s.state, geom3, vs3, far, (3.25, 1.75, 1.0)), 1.0)
            self.assertAlmostEqual(sight.point_transmission(s.state, geom3, vs3, far, (3.25, 3.25, 1.0)), 0.6)
            layer["porosity"] = 0.2                                     # dense: solid
            geom4 = sight.room_geom(s.state, "r")
            self.assertNotEqual(geom4.sig, geom3.sig)
            self.assertEqual(sight.point_transmission(s.state, geom4, sight.viewshed(geom4, far), far,
                                                      (3.25, 3.25, 1.0)), 0.0)

    def test_an_eye_on_a_cell_boundary_keeps_finite_slopes(self):
        # a device camera can sit exactly on a grid line; the first crossed cell then starts at distance 0
        s = Scene(8, 4)
        s.add("tall", "cabinet", pos=[1, 0], size_m=[0.5, 0.5, 2.0])            # x 0.5-1.0
        geom = sight.room_geom(s.state, "r")
        for eye in (("r", 1.0, 0.25, 1.5), ("r", 0.0, 0.0, 1.0), ("r", 2.0, 1.0, 0.3)):
            vs = sight.viewshed(geom, eye)
            for data in vs.data:
                for lo, hi, _, _ in data or ():
                    self.assertTrue(math.isfinite(hi) and (lo == float("-inf") or math.isfinite(lo)))
        eye = ("r", 1.0, 0.25, 1.5)
        vs = sight.viewshed(geom, eye)
        self.assertEqual(sight.point_transmission(s.state, geom, vs, eye, (0.25, 0.25, 1.0)), 0.0)
        self.assertEqual(sight.point_transmission(s.state, geom, vs, eye, (2.75, 0.25, 1.0)), 1.0)

    def test_visible_fraction_rounds_to_quarters_and_rejects_other_rooms(self):
        s = Scene(12, 4)
        s.add("crate", "box", pos=[8, 1], size_m=[0.4, 0.4, 0.8])
        s.add("wall", "box", pos=[4, 1], size_m=[0.45, 0.45, 0.6])           # hides the lower part
        frac, best = sight.visible_fraction(s.state, "crate", s.eye(0, 1, "kneeling"))
        self.assertIn(frac, (0.25, 0.5, 0.75))
        self.assertGreater(best[2], 0.6)
        s.state["rooms"]["other"] = {"size_m": [2.0, 2.0]}
        self.assertEqual(sight.visible_fraction(s.state, "crate", ("other", 0.25, 0.25, 1.5)), (0.0, None))
        s.state["robot"]["hands"]["left"] = "mug"
        s.add("mug", "generic_item", "robot")
        self.assertEqual(sight.visible_fraction(s.state, "mug", s.eye(0, 1))[0], 1.0)   # held: always, to the robot


class LosAndGeomTests(unittest.TestCase):
    def test_los_is_exact_against_boxes_and_agents(self):
        s = Scene(12, 4)
        s.add("ward", "wardrobe", pos=[4, 0], size_m=[1.0, 0.6, 2.0])          # x 2.0-3.0, y 0-0.6
        s.add("chair", "chair", pos=[8, 0])
        s.add("table", "table", pos=[0, 2], size_m=[1.0, 1.0, 0.75])
        self.assertEqual(sight.los(s.state, "r", (0.5, 0.3, 1.0), (4.0, 0.3, 1.0)), 0.0)
        self.assertEqual(sight.los(s.state, "r", (0.5, 0.3, 2.2), (4.0, 0.3, 2.2)), 1.0)         # over the top
        self.assertAlmostEqual(sight.los(s.state, "r", (3.5, 0.3, 0.6), (5.5, 0.3, 0.6)), 0.6)  # through the chair
        self.assertEqual(sight.los(s.state, "r", (0.5, 1.5, 0.75), (2.0, 1.5, 1.2)), 1.0)       # from the table top
        self.assertEqual(sight.los(s.state, "r", (3.5, 0.3, 1.0), (0.5, 0.3, 1.0), ignore=("ward",)), 1.0)
        s.state["people"]["p1"] = {"location": "r", "pos": [10, 0], "posture": "standing"}
        self.assertAlmostEqual(sight.los(s.state, "r", (3.6, 0.25, 0.6), (5.75, 0.25, 0.6)), 0.6 * 0.3)

    def test_the_signature_tracks_occluders_not_unrelated_small_things_and_caches_by_content(self):
        s = Scene(8, 8)
        s.add("ward", "wardrobe", pos=[0, 0])
        s.add("pen", "generic_item", pos=[5, 5], size_m=[0.14, 0.01, 0.01])
        sig = sight.occ_sig(s.state, "r")
        s.state["entities"]["pen"]["pos"] = [6, 6]
        self.assertEqual(sight.occ_sig(s.state, "r"), sig)
        self.assertIs(sight.room_geom(s.state, "r"), sight.room_geom(s.state, "r"))
        s.state["entities"]["ward"]["pos"] = [2, 0]
        self.assertNotEqual(sight.occ_sig(s.state, "r"), sig)
        s.add("tv", "box", "ward", size_m=[0.9, 0.2, 0.55])                    # tall and wide on a surface
        geom = sight.room_geom(s.state, "r")
        self.assertIn("tv", {b[7] for b in geom.boxes})
        s.add("note", "generic_item", "ward", size_m=[0.1, 0.1, 0.01])
        self.assertNotIn("note", {b[7] for b in sight.room_geom(s.state, "r").boxes})

    def test_a_world_mutation_counter_memoizes_the_signature(self):
        s = Scene(8, 8)
        s.add("ward", "wardrobe", pos=[0, 0])

        class FakeWorld(object):
            mutation_count = 1
            calls = 0

            def static_index(self, name, predicate):
                FakeWorld.calls += 1
                return sorted(e for e, r in s.state["entities"].items() if predicate(r))
        world = FakeWorld()
        sig = sight.occ_sig(s.state, "r", world)
        s.state["entities"]["ward"]["pos"] = [3, 3]                    # an unjournaled edit: still memoized
        self.assertEqual(sight.occ_sig(s.state, "r", world), sig)
        self.assertEqual(FakeWorld.calls, 1)
        world.mutation_count = 2
        self.assertNotEqual(sight.occ_sig(s.state, "r", world), sig)


# ----- frames, viewers and coverage (17.2, 17.11) ---------------------------------------------------------------------
class FrameTests(unittest.TestCase):
    def test_in_frame_by_bearing_tilt_and_range(self):
        v = head("r", 0.0, 0.0, 1.5, 0.0)
        self.assertTrue(sight.in_frame(v, (3.0, math.tan(math.radians(30)) * 3.0, 1.3)))
        self.assertFalse(sight.in_frame(v, (3.0, math.tan(math.radians(50)) * 3.0, 1.3)))      # bearing 50
        self.assertTrue(sight.in_frame(v._replace(yaw=50.0), (3.0, math.tan(math.radians(50)) * 3.0, 1.3)))
        self.assertFalse(sight.in_frame(v, (-3.0, 0.0, 1.5)))
        shelf = (2.0, 0.0, 2.6)                                              # elevation +28.8
        self.assertFalse(sight.in_frame(v, shelf))
        self.assertTrue(sight.in_frame(v._replace(tilt=20.0), shelf))
        self.assertFalse(sight.in_frame(v, (12.5, 0.0, 1.5)))
        self.assertFalse(sight.in_frame(v, (0.01, 0.0, 1.5)))
        self.assertTrue(sight.in_frame(v._replace(tilt=-90.0, vfov=60.0), (0.0, 0.0, 0.5)))

    def test_the_low_row_sees_the_floor_from_0_7_to_3_3_m_with_a_1_5_m_eye(self):
        # tilt -45 with a 42-degree frame spans -66 to -24 degrees: floor (z 0.02) from 1.48 / tan 66 = 0.66 m to
        # 1.48 / tan 24 = 3.32 m; the level look (tilt -10, down to -31) sees the floor only from 2.46 m
        low, level = head("r", 0.0, 0.0, 1.5, 0.0, -45.0), head("r", 0.0, 0.0, 1.5, 0.0, -10.0)
        self.assertFalse(sight.in_frame(low, (0.6, 0.0, 0.02)))
        self.assertTrue(sight.in_frame(low, (0.7, 0.0, 0.02)))
        self.assertTrue(sight.in_frame(low, (3.3, 0.0, 0.02)))
        self.assertFalse(sight.in_frame(low, (3.4, 0.0, 0.02)))
        self.assertFalse(sight.in_frame(level, (2.4, 0.0, 0.02)))
        self.assertTrue(sight.in_frame(level, (2.5, 0.0, 0.02)))

    def test_bearings_are_relative_and_positive_to_the_right(self):
        v = head("r", 1.0, 1.0, 1.5, 90.0)                                     # facing south
        self.assertEqual(sight.bearing_elev(v, (0.0, 2.0, 1.5)), (45.0, 0.0))    # south-west: right of south
        self.assertEqual(sight.bearing_elev(v, (2.0, 2.0, 0.5)), (-45.0, -35.26))
        self.assertEqual(sight.bearing_elev(v, (1.0, 0.0, 1.5))[0], 180.0)

    def test_sees_entity_needs_the_frame_and_the_line_of_sight(self):
        s = Scene(12, 12)
        s.add("vase", "box", pos=[8, 6], size_m=[0.3, 0.3, 0.4])
        eye = s.eye(2, 2)
        bearing = math.degrees(math.atan2(3.15 - 1.25, 4.15 - 1.25))
        forward = head("r", eye.x, eye.y, eye.z, bearing - 50.0)
        self.assertEqual(sight.sees_entity(s.state, forward, "vase"), 0.0)
        self.assertEqual(sight.sees_entity(s.state, forward._replace(yaw=bearing), "vase"), 1.0)
        s.add("ward", "wardrobe", pos=[5, 4], size_m=[1.0, 1.0, 2.0])
        self.assertEqual(sight.sees_entity(s.state, forward._replace(yaw=bearing), "vase"), 0.0)

    def test_an_l_shape_from_shelving_is_not_covered_from_one_viewpoint_but_is_from_two(self):
        s = Scene(8, 6)
        for r in range(4):
            s.add("shelf%d" % r, "bookshelf", pos=[4, r], size_m=[0.5, 0.5, 1.8])
        blocked = set(geometry.blocked_cells(s.state, "r", include_agents=False))
        free = [(c, r) for r in range(6) for c in range(8) if (c, r) not in blocked]

        def coverage(cells):
            seen = set()
            for c, r in cells:
                eye = s.eye(c, r)
                seen.add((c, r))                       # the camera counts the robot's own cell
                for yaw in range(0, 360, 60):
                    for tilt in (-10.0, -45.0):
                        seen |= sight.view_cells(s.state, head("r", eye.x, eye.y, eye.z, float(yaw), tilt))
            return sum(1 for cell in free if cell in seen) / float(len(free))
        self.assertLess(coverage([(1, 5)]), 0.9)
        self.assertGreaterEqual(coverage([(1, 5), (6, 4)]), 0.9)

    def test_people_and_device_viewers(self):
        s = Scene(12, 12)
        s.add("bed", "bed", pos=[0, 0])
        people = s.state["people"]
        people["mom"] = {"location": "r", "pos": [6, 6], "posture": "standing", "facing_deg": 0.0}
        people["kid"] = {"location": "r", "pos": [7, 6], "posture": "sitting", "role": "child", "age": 5}
        people["grandpa"] = {"location": "r", "pos": [1, 1], "posture": "lying", "in_bed": "bed"}
        people["fallen"] = {"location": "r", "pos": [9, 9], "posture": "fallen"}
        self.assertEqual(sight.person_eye(s.state, "mom")[2], 1.55)
        self.assertEqual(sight.person_eye(s.state, "kid")[2], round(0.95 * 1.15 / 1.55, 3))
        self.assertEqual(sight.person_eye(s.state, "grandpa")[2], 0.75)
        self.assertEqual(sight.person_eye(s.state, "fallen")[2], 0.2)
        self.assertEqual(sight.agent_top(s.state, "grandpa"), 0.8)
        aware, attend = sight.person_viewer(s.state, "mom"), sight.person_viewer(s.state, "mom", attention=True)
        side = (3.25 + 0.3, 3.25 + 1.7, 1.5)                              # 80 degrees off her facing
        self.assertTrue(sight.in_frame(aware, side))
        self.assertFalse(sight.in_frame(attend, side))
        self.assertEqual(aware.ignore, ("mom",))
        s.add("cam", "generic_item", pos=[11, 6], size_m=[0.1, 0.1, 0.1],
              view={"camera": {"hfov_deg": 78, "range_m": 4.0, "facing_deg": 180.0, "height_m": 2.0}})
        v = sight.device_viewer(s.state, "cam")
        self.assertEqual((v.kind, v.yaw, v.z, v.range_m, v.ignore), ("device", 180.0, 2.0, 4.0, ("cam",)))
        self.assertAlmostEqual(v.vfov, 2 * math.degrees(math.atan(math.tan(math.radians(39)) * 9 / 16)), places=6)
        self.assertIsNone(sight.device_viewer(s.state, "bed"))
        s.state["pets"]["cat"] = {"location": "r", "pos": [2, 2], "species": "cat"}
        s.state["pets"]["dog"] = {"location": "r", "pos": [3, 2], "species": "dog", "size": "small"}
        s.state["pets"]["fish"] = {"location": "r", "pos": [4, 2], "species": "fish", "view": {"height_m": 0.05}}
        self.assertEqual([sight.agent_top(s.state, p) for p in ("cat", "dog", "fish")], [0.3, 0.35, 0.05])


# ----- acuity, text and faces (17.3, 17.4, 17.7) ----------------------------------------------------------------------
class AcuityTests(unittest.TestCase):
    def test_the_head_camera_ranges_of_a_10_cm_object(self):
        focal = sight.HW["head"]["focal_px"]
        rng = lambda px: 0.1 * focal / px      # noqa: E731
        self.assertAlmostEqual(rng(sight.PX["detect_sure"]), 3.88, places=2)
        self.assertAlmostEqual(rng(sight.PX["detect_min"]), 9.31, places=2)
        self.assertAlmostEqual(rng(sight.PX["recognize"]), 2.91, places=2)
        self.assertAlmostEqual(rng(sight.PX["identify"]), 1.455, places=2)
        self.assertAlmostEqual(rng(sight.PX["inspect"]), 0.582, places=3)
        self.assertGreaterEqual(sight.px_on_target(0.04, 1.0, focal), sight.PX["detect_sure"])      # 4 cm at 1 m
        self.assertLess(sight.px_on_target(0.04, 3.0, focal), sight.PX["detect_sure"])
        self.assertTrue(sight.inspect_capable(sight.px_on_target(1.0, 5.8, focal), 0.5))
        self.assertFalse(sight.inspect_capable(sight.px_on_target(1.0, 5.9, focal), 0.5))

    def test_small_items_at_the_robots_feet_by_posture(self):
        # a 2 cm item on the floor in the next cell (conflict 4, measured to the floor: 11.8 px p 0.13; about 17 px
        # p 0.53; about 24 px sure). Its sample is its top centre, 2 cm up, which adds a fraction of a pixel.
        s = Scene()
        s.add("earring", "generic_item", pos=[3, 2], xy_m=[1.74, 1.24], size_m=[0.02, 0.02, 0.02])   # 0.5 m away
        focal = sight.HW["head"]["focal_px"]
        got = {}
        for posture in ("standing", "crouching", "kneeling"):
            eye = s.eye(2, 2, posture)
            (point,) = sight.sample_points(s.state, "earring", eye)
            px = sight.px_on_target(0.02, math.dist((eye.x, eye.y, eye.z), point), focal)
            got[posture] = (px, sight.detect_probability(px))
        self.assertAlmostEqual(got["standing"][0], 11.8, delta=0.2)
        self.assertAlmostEqual(got["standing"][1], 0.13, delta=0.015)
        self.assertAlmostEqual(got["crouching"][0], 17.4, delta=0.3)
        self.assertAlmostEqual(got["crouching"][1], 0.53, delta=0.02)
        self.assertEqual(got["kneeling"][1], 1.0)
        self.assertEqual(sight.acuity_level(got["kneeling"][0], 0.8), "detect")

    def test_factors_levels_and_the_detection_band(self):
        self.assertEqual((sight.k_lens(1.0), sight.k_lens(0.0)), (1.0, 0.25))
        self.assertAlmostEqual(sight.k_lens(0.3), 0.3175)
        self.assertEqual((sight.k_light(0.05), sight.k_light(0.35), sight.k_light(0.9)), (0.0, 1.0, 1.0))
        self.assertAlmostEqual(sight.k_light(0.125), 0.5)
        self.assertEqual((sight.k_motion(), sight.k_motion("careful"), sight.k_motion("fast")), (1.0, 1.0, 0.55))
        self.assertEqual(sight.k_haze({}, 5.0), 1.0)
        self.assertAlmostEqual(sight.k_haze({"smoke": 0.5, "fog": True}, 2.0), math.exp(-(0.3 + 0.3) * 2.0))
        self.assertAlmostEqual(sight.k_haze({"smoke_density": 1.0}, 1.0), math.exp(-0.6))
        self.assertEqual(sight.k_contrast("black", "charcoal", 0.3), 0.5)
        self.assertEqual(sight.k_contrast("black", "charcoal", 0.5), 1.0)
        self.assertEqual(sight.k_contrast("white", "black", 0.1), 1.0)
        self.assertAlmostEqual(sight.effective_px(100, visible=0.25, lens=0.5, light=0.5), 12.5)
        level = sight.acuity_level
        self.assertEqual(level(70, 0.5), "identify")
        self.assertEqual(level(70, 0.15), "recognize")
        self.assertEqual(level(70, 0.5, backlit=True), "recognize")
        self.assertEqual(level(70, 0.5, sun_cone=True), "detect")
        self.assertEqual(level(12, 0.5), "detect")
        self.assertIsNone(level(12, 0.5, detect_px=sight.PX["detect_oracle"]))
        self.assertIsNone(level(70, 0.04))
        self.assertEqual((sight.detect_probability(10), sight.detect_probability(24)), (0.0, 1.0))
        self.assertAlmostEqual(sight.detect_probability(17), 0.5)
        self.assertAlmostEqual(sight.detect_probability(17, clutter=6), 0.35)

    def test_background_colours_and_ir_shapes(self):
        s = Scene()
        s.state["rooms"]["r"]["floor_material"] = "slate"
        s.add("cable", "generic_item", pos=[1, 1], color="black")
        s.add("table", "table", pos=[4, 4], color="white")
        s.add("cup", "generic_item", "table")
        self.assertEqual(sight.background_color(s.state, "cable"), "dark")
        self.assertEqual(sight.background_color(s.state, "cup"), "white")
        s.state["rooms"]["r"]["floor_color"] = "beige"
        self.assertEqual(sight.background_color(s.state, "cable"), "beige")
        self.assertEqual(sight.k_contrast("black", "dark", 0.2), 0.5)
        # pets and people (adoption request pets 5): their colour against the furniture they are on, else the floor
        cat = {"location": "r", "pos": [2, 2], "species": "cat", "coat_color": "black"}
        s.state["pets"]["cat_1"] = cat
        del s.state["rooms"]["r"]["floor_color"]
        self.assertEqual(sight.agent_background_color(s.state, cat), "dark")
        from house_sim.kernel import camera
        self.assertEqual(camera._contrast(s.state, "cat_1", 0.2), 0.5)              # a black cat on slate at dusk
        self.assertEqual(camera._contrast(s.state, "cat_1", 0.6), 1.0)              # in good light
        self.assertEqual(sight.agent_background_color(s.state, dict(cat, on="table")), "white")
        s.state["pets"]["cat_1"]["coat_color"] = None
        self.assertEqual(camera._contrast(s.state, "cat_1", 0.2), 1.0)              # no colour: no penalty
        self.assertEqual(camera._contrast(s.state, "nobody", 0.2), 1.0)
        self.assertTrue(sight.ir_shape(0.5, 3.0, 10.0, 0.0))
        self.assertFalse(sight.ir_shape(0.3, 3.0, 10.0, 0.0))       # under the 0.4 m floor (C11)
        self.assertFalse(sight.ir_shape(0.5, 4.5, 10.0, 0.0))       # past the IR range
        self.assertFalse(sight.ir_shape(0.5, 3.0, 50.0, 0.0))       # outside the depth field
        self.assertFalse(sight.ir_shape(0.5, 3.0, 10.0, 0.1))       # lit: the RGB camera's job

    def test_text_sizes_and_read_ranges(self):
        s = Scene()
        s.add("jar", "box", view={"text_m": {"label_text": 0.006}})
        s.add("doc", "book")
        self.assertEqual(sight.text_m(s.state, "jar", "label_text"), 0.006)
        self.assertEqual(sight.text_m(s.state, "doc", "label_text"), 0.004)
        self.assertEqual(sight.text_m(s.state, "doc", "text"), 0.003)
        self.assertIsNone(sight.text_m(s.state, "doc", "mass_kg"))
        self.assertAlmostEqual(sight.read_range_m(0.004), 0.372, places=3)
        self.assertAlmostEqual(sight.read_range_m(0.004, sight.HW["wrist"]["focal_px"]), 0.135, places=3)
        self.assertAlmostEqual(sight.read_range_m(0.03), 2.79, places=2)
        head_f, wrist_f = sight.HW["head"]["focal_px"], sight.HW["wrist"]["focal_px"]
        self.assertFalse(sight.readable(0.004, 0.6, head_f, 0.8))            # a 4 mm label from 0.6 m
        self.assertTrue(sight.readable(0.004, 0.12, wrist_f, 0.8))            # ... the wrist camera within reach
        self.assertTrue(sight.readable(0.03, 2.5, head_f, 0.8))               # a 3 cm sign from 2.5 m
        self.assertFalse(sight.readable(0.03, 2.5, head_f, 0.2))              # too dark
        self.assertFalse(sight.readable(0.03, 1.0, head_f, 0.8, camera_clean=0.3))     # dirty lens (17.7)
        self.assertFalse(sight.readable(0.03, 2.5, head_f, 0.8, motion=0.55))


class FaceTests(unittest.TestCase):
    def setUp(self):
        self.s = Scene(10, 10)
        self.st = self.s.state

    def test_label_faces_are_seen_from_their_side_or_from_above(self):
        self.s.add("parcel", "box", pos=[4, 4], size_m=[0.4, 0.4, 0.3], label_facing="front")
        front = sight.front_deg(self.st, "parcel")
        rad = math.radians(front)
        cx, cy = 2.2, 2.2
        ahead = head("r", cx + 2 * math.cos(rad), cy + 2 * math.sin(rad), 1.5, 0.0)
        behind = head("r", cx - 2 * math.cos(rad), cy - 2 * math.sin(rad), 1.5, 0.0)
        self.assertEqual(sight.faces_seen(self.st, ahead, "parcel"), ["label"])
        self.assertEqual(sight.faces_seen(self.st, behind, "parcel"), [])
        self.st["entities"]["parcel"]["label_facing"] = "top"
        self.assertEqual(sight.faces_seen(self.st, behind, "parcel"), ["label"])
        self.assertEqual(sight.faces_seen(self.st, behind._replace(z=0.3), "parcel"), [])
        self.s.add("lid", "box", "parcel", size_m=[0.4, 0.3, 0.2])            # covers 75 % of the top
        self.assertEqual(sight.faces_seen(self.st, behind, "parcel"), [])

    def test_faces_against_walls_and_neighbours_are_blocked_and_bottoms_hidden_while_resting(self):
        self.s.add("parcel", "box", pos=[4, 0], size_m=[0.4, 0.4, 0.3], label_facing="back",
                   view={"faces": {"base": "down", "rim": "all"}})
        everywhere = [head("r", x, y, z, 0.0) for x, y, z in ((2.2, 3.0, 1.5), (2.2, -1.0, 1.5), (2.2, 0.2, 0.0))]
        for viewer in everywhere:
            self.assertEqual(sight.faces_seen(self.st, viewer, "parcel"), ["rim"])      # back to the north wall
        self.s.add("table", "table", pos=[0, 6])
        self.s.add("a", "box", "table", spos=[0.0, 0.2], size_m=[0.2, 0.2, 0.2], view={"faces": {"label": 0}})
        self.s.add("b", "box", "table", spos=[0.22, 0.2], size_m=[0.2, 0.2, 0.2])
        self.assertEqual(sight.front_deg(self.st, "a"), 0.0)                # the table's front: away from the wall
        east = head("r", 3.0, 3.3, 1.5, 180.0)
        self.assertEqual(sight.faces_seen(self.st, east, "a"), [])          # its labelled side touches b
        self.st["entities"]["b"]["spos"] = [0.5, 0.2]
        self.assertEqual(sight.faces_seen(self.st, east, "a"), ["label"])

    def test_gated_faces(self):
        self.s.add("parcel", "box", label_facing="front")
        self.s.add("crate", "box")
        self.assertEqual(sight.gated_face(self.st, "parcel", "recipient"), "label")
        self.assertIsNone(sight.gated_face(self.st, "parcel", "markings"))
        self.assertIsNone(sight.gated_face(self.st, "crate", "label_text"))
        with mock.patch.dict(default_registry().perception, {"box": {"faces": {"label_text": "rim"}}}):
            self.assertEqual(sight.gated_face(self.st, "crate", "label_text"), "rim")


# ----- light and glare (17.5, 17.6) ---------------------------------------------------------------------------------
class LightTests(unittest.TestCase):
    def test_acuity_light_asks_the_boost_hooks_only_below_saturation(self):
        # K-CAM (C9): light_at hooks answer boosts (>= 0); once the hook-free light reaches 0.36, k_light is exactly 1
        # and every light bar of section 7 is met, so the per-substep watch need not ask the domains' hooks
        s = Scene(6, 6)
        calls = []

        def hooks(name, *args):
            if name == "light_at":
                calls.append(args[1:])
                return [0.2]
            return []
        p = (1.25, 1.25, 1.0)
        with mock.patch.object(sight, "run_hooks", side_effect=hooks):
            for ambient, want, asked in ((1.0, 1.0, False), (0.36, 0.36, False), (0.3, 0.5, True), (0.0, 0.2, True)):
                del calls[:]
                got = sight.acuity_light(s.state, "r", p, (), ambient)
                full = sight.light_at(s.state, "r", p, (), ambient)
                self.assertEqual(got, want, ambient)
                self.assertEqual(bool(calls[:-1]), asked, ambient)          # light_at itself asked once more
                self.assertEqual(sight.k_light(got), sight.k_light(full), ambient)
                for px in (12.0, 40.0, 100.0):
                    self.assertEqual(sight.acuity_level(px, got), sight.acuity_level(px, full), (ambient, px))
        self.assertEqual(sight.k_light(sight.LIGHT_SATURATED), 1.0)

    def test_robot_beams_reach_their_rated_distances_inside_their_cones_only(self):
        torch = sight.make_beam((0.0, 0.0, 1.5), 0.0, 0.0, 1.8, 15.0)
        self.assertAlmostEqual(sight.beam_light(torch, (3.0, 0.0, 1.5)), 0.2)
        self.assertLess(sight.beam_light(torch, (3.5, 0.0, 1.5)), 0.2)
        self.assertEqual(sight.beam_light(torch, (3.0, 1.0, 1.5)), 0.0)        # 18 degrees off the axis
        lamp = sight.make_beam((0.0, 0.0, 1.5), 0.0, -30.0, 0.45, 30.0)
        self.assertAlmostEqual(sight.beam_light(lamp, (1.5 * math.cos(math.radians(30)), 0.0,
                                                       1.5 - 1.5 * math.sin(math.radians(30)))), 0.2)
        led = sight.make_beam((0.0, 0.0, 0.5), 0.0, 0.0, 0.12, 45.0)
        self.assertGreaterEqual(sight.beam_light(led, (0.585, 0.0, 0.5)), 0.35)
        self.assertAlmostEqual(sight.beam_light(led, (0.0, 0.0, 0.5)), 0.12 / 0.2 ** 2)       # within 0.2 m

    def test_the_robots_beams_come_from_a_lit_flashlight_and_the_headlamp(self):
        s = Scene()
        s.add("torch", "flashlight", "robot", on=False)
        s.state["robot"]["hands"]["right"] = "torch"
        self.assertEqual(sight.robot_beams(s.state, (1, 1, 1.5), 0.0, 0.0), [])
        s.state["entities"]["torch"]["on"] = True
        s.state["robot"]["headlamp_on"] = True
        beams = sight.robot_beams(s.state, (1, 1, 1.5), 0.0, -10.0)
        self.assertEqual([(b.power, b.half_deg) for b in beams], [(1.8, 15.0), (0.45, 30.0)])
        # the worn headlamp's own view.beam wins over the kernel default (navigation_robot's adoption request 4)
        s.state["entities"]["lamp"] = {"type": "generic_item", "location": "robot", "relation": "worn",
                                       "gear_kind": "headlamp", "view": {"beam": {"power": 0.6, "half_deg": 25.0}}}
        beams = sight.robot_beams(s.state, (1, 1, 1.5), 0.0, -10.0)
        self.assertEqual([(b.power, b.half_deg) for b in beams], [(1.8, 15.0), (0.6, 25.0)])
        s.state["entities"]["lamp"]["relation"] = "held"                       # carried, not worn: the default
        beams = sight.robot_beams(s.state, (1, 1, 1.5), 0.0, -10.0)
        self.assertEqual([(b.power, b.half_deg) for b in beams], [(1.8, 15.0), (0.45, 30.0)])

    def test_light_is_shaded_under_inside_and_behind_furniture(self):
        s = Scene(10, 10)
        s.add("table", "table", pos=[2, 2])
        s.add("ward", "wardrobe", pos=[6, 0], open=True)
        s.add("sofa", "sofa", pos=[0, 8])                                     # against the south wall
        s.state["entities"]["fridge"] = {"type": "refrigerator", "is_a": ["cooking_appliance"], "location": "r",
                                         "category": "appliance", "pos": [0, 0], "size_m": [0.84, 0.74, 1.78],
                                         "openable": True, "open": True}
        at = lambda p: sight.light_at(s.state, "r", p, ambient=0.8)       # noqa: E731
        self.assertEqual(at((1.5, 1.4, 0.2)), 0.24)                       # under the table: 0.3
        self.assertEqual(at((1.5, 1.4, 0.8)), 0.8)                        # on it
        self.assertEqual(at((3.4, 0.3, 1.0)), 0.48)                       # inside the open wardrobe: 0.6
        self.assertEqual(at((0.4, 0.4, 1.0)), 0.8)                        # an open, powered fridge is lit inside
        s.state["utilities"] = {"power": False}
        self.assertEqual(at((0.4, 0.4, 1.0)), 0.48)
        self.assertEqual(at((1.0, 4.97, 0.3)), 0.32)                      # the behind gap: 0.4
        self.assertEqual(at((5.0, 2.5, 0.0)), 0.8)
        with mock.patch.object(sight, "run_hooks", side_effect=lambda name, *a: [0.3, None] if name == "light_at"
                               else []):
            self.assertEqual(sight.light_at(s.state, "r", (5.0, 2.5, 0.0), ambient=0.0), 0.3)
        torch = sight.make_beam((5.0, 4.5, 1.5), 270.0, 0.0, 1.8, 15.0)
        self.assertEqual(sight.light_at(s.state, "r", (5.0, 2.0, 1.5), beams=[torch], ambient=0.0), 0.288)
        dark = sight.light_at(s.state, "r", (5.0, 2.5, 0.0))
        self.assertEqual(dark, 0.0)                                       # no windows, no lights, 05:00

    def test_bright_sources_fall_back_to_windows_and_lit_lights(self):
        s = Scene(8, 8)
        s.state["time_s"] = 5 * 3600                                       # noon (the episode starts at 07:00)
        s.add("win", "window", pos=[7, 3], rot=90, mounted=True, mount_height_m=0.9, covered=0.25)
        s.add("light", "ceiling_light", pos=[3, 3], mounted=True, mount_height_m=2.4, on=True)
        s.add("off", "ceiling_light", pos=[5, 5], mounted=True, mount_height_m=2.4, on=False)
        sources = {src["id"]: src for src in sight.bright_sources(s.state, "r")}
        self.assertEqual(sorted(sources), ["light", "win"])
        self.assertEqual((sources["win"]["x"], sources["win"]["z"], sources["win"]["radius_m"]), (4.0, 1.5, 0.5))
        self.assertAlmostEqual(sources["win"]["level"], 0.75)
        self.assertEqual(sources["light"]["level"], 0.8)
        with mock.patch.object(sight, "run_hooks", side_effect=lambda name, *a: [[{"x": 0, "y": 0, "z": 2,
                               "radius_m": 0.1, "level": 1.0, "kind": "sun"}]] if name == "bright_sources" else []):
            self.assertEqual([src["kind"] for src in sight.bright_sources(s.state, "r")], ["sun"])


class GlareTests(unittest.TestCase):
    def test_sun_in_frame_needs_the_sun_hook_and_turning_away_ends_it(self):
        s = Scene(8, 8)
        sun = (math.cos(math.radians(20)), 0.0, math.sin(math.radians(20)))
        hooks = lambda name, *a: [None, sun] if name == "sun_beam" else []      # noqa: E731
        v = head("r", 0.5, 2.0, 1.5, 0.0, 0.0)
        target_near_sun, target_off = (3.0, 2.0, 2.4), (3.0, 4.0, 1.5)
        self.assertFalse(sight.glare(s.state, v, target_near_sun, light=0.8)["sun"])      # no climate: no sun
        with mock.patch.object(sight, "run_hooks", side_effect=hooks):
            g = sight.glare(s.state, v, target_near_sun, light=0.8)
            self.assertTrue(g["sun"] and g["sun_cone"])
            g = sight.glare(s.state, v, target_off, light=0.8)
            self.assertTrue(g["sun"])
            self.assertFalse(g["sun_cone"])
            self.assertFalse(sight.glare(s.state, v._replace(yaw=180.0), target_near_sun, light=0.8)["sun"])
            self.assertFalse(sight.glare(s.state, v._replace(yaw=60.0), target_near_sun, light=0.8)["sun"])
            s.state["robot"]["lens_scratch"] = 1.0                       # a scratched lens flares 20 degrees wider
            self.assertTrue(sight.glare(s.state, v._replace(yaw=60.0), target_near_sun, light=0.8)["sun"])

    def test_a_silhouette_against_a_bright_window_is_backlit(self):
        s = Scene(8, 8)
        s.add("win", "window", pos=[7, 3], rot=90, mounted=True, mount_height_m=0.9, covered=0.0)
        v = head("r", 0.25, 2.0, 1.5, 0.0, 0.0)
        person = (2.0, 2.0, 1.4)
        self.assertTrue(sight.glare(s.state, v, person, radius_m=0.25, light=0.3)["backlit"])
        self.assertFalse(sight.glare(s.state, v, person, radius_m=0.25, light=0.9)["backlit"])     # lit well
        self.assertFalse(sight.glare(s.state, v, (2.0, 0.2, 1.4), radius_m=0.25, light=0.3)["backlit"])
        s.state["entities"]["win"]["covered"] = 1.0
        self.assertFalse(sight.glare(s.state, v, person, radius_m=0.25, light=0.3)["backlit"])

    def test_glossy_surfaces_mirror_a_light_into_the_camera_only_at_the_mirror_angle(self):
        s = Scene(12, 8)
        s.add("table", "table", pos=[2, 4], size_m=[1.0, 1.0, 0.75])          # x 1.0-2.0, y 2.0-3.0
        s.add("pane", "box", "table", material="glass", size_m=[0.3, 0.3, 0.01])     # centred, top 0.76
        s.add("light", "ceiling_light", pos=[4, 5], mounted=True, mount_height_m=2.4, on=True)   # x 2.0-2.3
        light_x = 2.15
        rise = 2.475 - 0.76
        mirror = sight.Viewer("r", 1.5 - (light_x - 1.5) * (1.5 - 0.76) / rise, 2.5 + 0.0, 1.5, 0.0, -30.0, 69.0,
                              42.0, 12.0, 931.2, "head", ("robot",))
        self.assertTrue(sight.specular(s.state, mirror, "pane"))
        self.assertFalse(sight.specular(s.state, mirror._replace(x=0.25), "pane"))
        s.state["entities"]["pane"]["material"] = "wood"
        self.assertFalse(sight.specular(s.state, mirror, "pane"))


# ----- carried loads (17.9) and the lidar ring ----------------------------------------------------------------------
class CarriedLoadTests(unittest.TestCase):
    def hold(self, s, eid, both=True, **props):
        s.add(eid, "box", "robot", **props)
        if both:
            s.state["entities"][eid]["held_hand"] = "both"
            s.state["robot"]["hands"] = {"left": eid, "right": eid}
        else:
            s.state["robot"]["hands"]["right"] = eid

    def test_a_basket_carried_in_both_hands_hides_the_floor_within_2_36_m(self):
        s = Scene()
        self.hold(s, "basket", size_m=[0.6, 0.4, 0.3])
        occ = sight.carried_occluder(s.state)
        self.assertEqual((occ["z_bottom"], occ["z_top"], occ["x_front"], occ["half_w"]), (0.85, 1.15, 0.55, 0.3))
        self.assertAlmostEqual(occ["half_angle_deg"], 28.61, places=2)
        self.assertAlmostEqual(occ["floor_hidden_m"], 1.5 * 0.55 / (1.5 - 1.15), places=3)     # 2.357 m
        self.assertEqual(occ["items"], ["basket"])
        self.assertTrue(sight.carried_hides(occ, 1.5, 0.0, 2.0, 0.0))
        self.assertFalse(sight.carried_hides(occ, 1.5, 0.0, 2.5, 0.0))
        self.assertFalse(sight.carried_hides(occ, 1.5, 35.0, 1.0, 0.0))
        self.assertFalse(sight.carried_hides(occ, 1.5, 0.0, 2.0, 1.2))          # above the line
        s.state["robot"]["posture"] = "crouching"                              # eye 0.95, below the load's top
        self.assertIsNone(sight.carried_occluder(s.state)["floor_hidden_m"])

    def test_small_one_hand_loads_and_level_trays_do_not_block_but_blocks_view_does(self):
        s = Scene()
        self.hold(s, "mug", both=False, size_m=[0.1, 0.1, 0.12])
        self.assertIsNone(sight.carried_occluder(s.state))
        s.state["entities"]["mug"]["blocks_view"] = True
        self.assertIsNotNone(sight.carried_occluder(s.state))
        s2 = Scene()
        s2.add("tray", "tray", "robot", held_hand="both")
        s2.state["robot"]["hands"] = {"left": "tray", "right": "tray"}
        s2.add("vase", "box", "tray", size_m=[0.3, 0.3, 0.5])
        self.assertIsNone(sight.carried_occluder(s2.state))
        s3 = Scene()
        self.hold(s3, "flat", size_m=[0.6, 0.4, 0.1])                        # too low to block on its own...
        self.assertIsNone(sight.carried_occluder(s3.state))
        s3.add("stack", "box", "flat", size_m=[0.5, 0.35, 0.2])               # ... until something rests on it
        occ = sight.carried_occluder(s3.state)
        self.assertEqual((occ["z_top"], sorted(occ["items"])), (1.15, ["flat", "stack"]))
        s4 = Scene()                                                          # one load per hand: side by side
        s4.add("bag", "box", "robot", size_m=[0.4, 0.2, 0.4], blocks_view=True)
        s4.add("can", "box", "robot", size_m=[0.3, 0.3, 0.3])
        s4.state["robot"]["hands"] = {"left": "bag", "right": "can"}
        occ = sight.carried_occluder(s4.state)
        self.assertEqual((occ["half_w"], occ["x_front"], occ["z_bottom"], occ["z_top"]), (0.35, 0.5, 0.8, 1.2))


class LidarTests(unittest.TestCase):
    def test_distances_to_walls_furniture_agents_and_out_through_open_doorways(self):
        s = Scene(8, 8)
        s.add("cab", "cabinet", pos=[5, 1], size_m=[0.8, 0.5, 0.9])             # x 2.5-3.3 in row 1
        s.add("table", "table", pos=[1, 4], size_m=[1.0, 1.0, 0.75])          # top only at 0.71 m: the beam passes
        eye = s.eye(1, 1)                                                      # (0.75, 0.75)
        ring = sight.lidar_ring(s.state, eye, 0.0)
        self.assertEqual(len(ring), 24)
        self.assertEqual(ring[0], 1.75)                                        # east: the cabinet
        self.assertEqual(ring[12], 0.75)                                       # west wall
        self.assertEqual(ring[18], 0.75)                                       # north wall (left of east)
        self.assertEqual(ring[6], 3.25)                                        # south wall past the table
        s.state["people"]["p1"] = {"location": "r", "pos": [1, 3], "posture": "standing"}
        self.assertEqual(sight.lidar_ring(s.state, eye, 0.0)[6], 0.75)
        s.state["connections"]["c1"] = {"rooms": ["r", "h"], "cells": {"r": [0, 1], "h": [3, 1]}, "door": None}
        self.assertIsNone(sight.lidar_ring(s.state, eye, 0.0)[12])
        turned = sight.lidar_ring(s.state, eye, 90.0)                        # sectors turn with the body
        self.assertEqual((turned[0], turned[6], turned[12], turned[18]), (0.75, None, 0.75, 1.75))


class ViewpointTests(unittest.TestCase):
    def test_best_viewpoints_are_the_nearest_cells_that_see_the_target(self):
        s = Scene(10, 6)
        for r in range(0, 5):
            s.add("shelf%d" % r, "bookshelf", pos=[5, r], size_m=[0.5, 0.5, 1.8])
        s.add("toy", "generic_item", pos=[7, 2], size_m=[0.2, 0.2, 0.2])
        s.state["robot"]["pos"] = [1, 2]
        cells = sight.best_viewpoints(s.state, "r", "toy", k=3)
        self.assertEqual(len(cells), 3)
        for c, r in cells:
            eye = sight.Eye("r", (c + 0.5) * 0.5, (r + 0.5) * 0.5, 1.5, ("robot",))
            self.assertGreaterEqual(sight.visible_fraction(s.state, "toy", eye)[0], 0.25)
        self.assertTrue(all(c >= 4 or r == 5 for c, r in cells))
        self.assertEqual(cells, sight.best_viewpoints(s.state, "r", "toy", k=3))
        self.assertEqual(sight.best_viewpoints(s.state, "r", "toy", k=1), cells[:1])
        self.assertEqual(sight.best_viewpoints(s.state, "r", "nothing"), [])


# ----- determinism, purity, bridges and performance (17.12, 17.14) --------------------------------------------------
_DIGEST_CODE = r"""
import json, sys, warnings
warnings.simplefilter("ignore")
from house_sim.engine import HouseEnv
from house_sim.kernel import sight
for key in ("body.eye_height_m", "phys.support.held_envelope", "phys.support.resting_size"):
    sight._BRIDGES[key] = None
from house_sim.kernel.predicates import root_room
st = HouseEnv(seed=3).world.s
cmap = sight.children_map(st)
out = []
for rid in sorted(st["rooms"])[:6]:
    geom = sight.room_geom(st, rid)
    eye = sight.Eye(rid, (geom.cols // 2 + 0.5) * 0.5, (geom.rows // 2 + 0.5) * 0.5, 1.5, ("robot",))
    vs = sight.viewshed(geom, eye)
    out.append([geom.sig, repr(vs.data)])
    for eid in sorted(e for e in st["entities"] if root_room(st, e) == rid):
        out.append([eid, sight.visible_fraction(st, eid, eye, geom, vs, cmap)])
    out.append(sight.lidar_ring(st, eye, 30.0, geom))
print(json.dumps(out, sort_keys=True))
"""


class DeterminismTests(unittest.TestCase):
    def _house(self, seed=3):
        from house_sim.engine import HouseEnv
        return HouseEnv(seed=seed).world.s

    def test_results_are_identical_across_hash_seeds_and_processes(self):
        outs = []
        for seed in ("1", "987"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-c", _DIGEST_CODE], cwd=str(ROOT), env=env, capture_output=True,
                                  text=True, timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertGreater(len(outs[0]), 1000)

    def test_caches_snapshots_and_wedges_never_change_results_and_nothing_writes_state(self):
        st = self._house()
        before = digest(st)
        rid = st["robot"]["room"]
        ids = sorted(e for e in st["entities"] if root_room(st, e) == rid)
        geom = sight.room_geom(st, rid)
        eye = sight.Eye(rid, (st["robot"]["pos"][0] + 0.5) * 0.5, (st["robot"]["pos"][1] + 0.5) * 0.5, 1.5,
                        ("robot",))
        warm = [sight.visible_fraction(st, e, eye, cmap=sight.children_map(st)) for e in ids]
        for cache in (sight._GEOM_CACHE.data, sight._VS_CACHE.data, sight._LAYOUT_CACHE.data, sight._TEMPLATES,
                      sight._VIEW_MEMO, sight._FRUSTA):
            cache.clear()
        cold = [sight.visible_fraction(st, e, eye) for e in ids]
        self.assertEqual(warm, cold)
        full = sight._compute_viewshed(geom, eye.x, eye.y, eye.z, None, 12.0)
        wedge = sight._compute_viewshed(geom, eye.x, eye.y, eye.z, (90.0, 34.5), 12.0)
        self.assertTrue(any(d is None for d in wedge.data))
        for i, data in enumerate(wedge.data):
            if data is not None:
                self.assertEqual(data, full.data[i])
                self.assertEqual(wedge.entry[i], full.entry[i])
        off = sight._compute_viewshed(geom, eye.x + 0.1, eye.y - 0.05, eye.z, None, 12.0)    # the generic walk
        self.assertEqual(len(off.data), len(full.data))
        viewer = head(rid, eye.x, eye.y, eye.z, 90.0)
        for e in ids[:30]:
            sight.sees_entity(st, viewer, e)
            sight.faces_seen(st, viewer, e)
            sight.specular(st, viewer, e)
        sight.view_cells(st, viewer)
        sight.lidar_ring(st, eye, 0.0)
        sight.bright_sources(st, rid)
        sight.carried_occluder(st)
        sight.best_viewpoints(st, rid, ids[0])
        self.assertEqual(digest(st), before)

    def test_importing_sight_registers_nothing(self):
        code = ("from house_sim.kernel import plugins as p\n"
                "tables = [p.ACTIONS, p.BEFORE, p.AFTER, p.SYSTEMS, p.FURNISHERS, p.OBSERVERS, p.HOOKS]\n"
                "before = [len(t) for t in tables]\n"
                "import house_sim.kernel.sight\n"
                "print([len(t) for t in tables] == before)\n")
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.stdout.strip(), "True", proc.stderr[-2000:])


class BridgeTests(unittest.TestCase):
    def test_the_stage_a_siblings_are_used_when_they_exist(self):
        s = Scene()
        s.add("crate", "box", pos=[2, 2], size_m=[0.4, 0.4, 0.4])
        fake_env = {"items": ["x"], "two_handed": True, "bottom_m": 0.85, "top_m": 1.25, "front_m": 0.6,
                    "width_m": 0.5, "depth_m": 0.5, "height_m": 0.4, "blocks_view": True}
        with mock.patch.dict(sight._BRIDGES, {"body.eye_height_m": lambda st: 1.4,
                                              "phys.support.held_envelope": lambda st: fake_env,
                                              "phys.support.resting_size": lambda st, eid: [1.0, 0.5, 0.25]}):
            self.assertEqual(sight.eye_height_m(s.state), 1.4)
            box = sight.entity_box(s.state, "crate")
            self.assertEqual((box.x1 - box.x0, box.y1 - box.y0, box.z1), (1.0, 0.5, 0.25))
            occ = sight.carried_occluder(s.state)
            self.assertEqual((occ["z_top"], occ["x_front"], occ["half_w"]), (1.25, 0.6, 0.25))
            self.assertAlmostEqual(occ["floor_hidden_m"], 1.4 * 0.6 / (1.4 - 1.25), places=3)
            fake_env["blocks_view"] = False
            self.assertIsNone(sight.carried_occluder(s.state))


class TwoRooms(Scene):
    """Scene's room "r" (cols x rows) with a hall "h" (hcols x hrows) behind its west wall: a doorway between r's cell
    (0, door_row) and the hall's cell `hall_cell` (east wall by default)."""

    def __init__(self, cols=12, rows=12, hcols=6, hrows=8, door_row=5, hall_cell=(5, 3), width=0.85):
        Scene.__init__(self, cols, rows)
        self.state["rooms"]["h"] = {"size_m": [hcols * 0.5, hrows * 0.5], "type": "hallway", "label": "hall"}
        self.state["connections"]["c"] = {"rooms": ["r", "h"], "kind": "doorway", "width_m": width,
                                          "cells": {"r": [0, door_row], "h": list(hall_cell)}}

    def door(self, open_=True, **props):
        self.add("d", "door", "h", pos=list(self.state["connections"]["c"]["cells"]["h"]), connects=["h", "r"],
                 open=open_, swing_into="h", **props)
        self.state["connections"]["c"]["door"] = "d"


# ----- doorway and window apertures (5.8; camera phase 3) ---------------------------------------------------------------
class ApertureTests(unittest.TestCase):
    def test_a_doorway_abuts_the_two_rooms_rigidly_across_the_shared_wall(self):
        s = TwoRooms()
        ap = sight.doorway_aperture(s.state, "c", "r")
        self.assertEqual((ap.other, ap.nx, ap.ny, ap.wall, ap.lo, ap.hi, ap.z0, ap.z1, ap.factor),
                         ("h", 1, 0, 0.0, 2.325, 3.175, 0.0, 2.0, 1.0))   # 0.85 m centred on r's cell (0, 5)
        self.assertEqual(sight.to_room(ap, 2.75, 1.75), (-0.25, 2.75))       # the hall's doorway cell: one cell west
        for p in ((0.3, 0.2), (2.9, 3.9), (1.0, 2.5)):
            x, y = sight.to_room(ap, *p)
            self.assertLess(x, 0.0)                                         # the whole hall lies behind the west wall
            back = sight.from_room(ap, x, y)
            self.assertAlmostEqual(back[0], p[0])
            self.assertAlmostEqual(back[1], p[1])
        self.assertEqual(ap.into, sight.Opening("h", -1, 0, 3.0, 1.325, 2.175, 0.0, 2.0))  # centred on (5, 3)
        # seen from the hall the same opening, the other way round
        back = sight.doorway_aperture(s.state, "c", "h")
        self.assertEqual((back.other, back.nx, back.wall, back.lo, back.hi), ("r", -1, 3.0, 1.325, 2.175))
        # a quarter turn: the hall's doorway on its north wall; its wall lands on r's wall, its inside behind it
        turned = TwoRooms(hall_cell=(2, 0))
        ap = sight.doorway_aperture(turned.state, "c", "r")
        self.assertEqual((ap.cos, ap.sin), (0, 1))
        wall_end = sight.to_room(ap, 1.25, 0.0)                              # the hall's north wall at the doorway
        self.assertAlmostEqual(wall_end[0], 0.0)
        inside = sight.to_room(ap, 1.25, 1.0)
        self.assertLess(inside[0], 0.0)
        self.assertEqual(sight.to_room_deg(ap, 90.0), 180.0)               # the hall's south is r's west

    def test_the_opening_is_clipped_to_both_walls_and_closed_by_an_opaque_door(self):
        s = TwoRooms(door_row=0, hall_cell=(0, 0))      # both doorways in a corner, the hall turned half round
        ap = sight.doorway_aperture(s.state, "c", "r")
        self.assertEqual((ap.cos, ap.lo, ap.hi), (-1, 0.0, 0.5))            # the walls overlap one cell only
        s = TwoRooms(door_row=0, hall_cell=(5, 0))      # both in a corner, both walls running south from it
        ap = sight.doorway_aperture(s.state, "c", "r")
        self.assertEqual((ap.cos, ap.lo, ap.hi), (1, 0.0, 0.675))           # clipped by r's corner only
        s = TwoRooms()
        s.door(open_=False)
        self.assertIsNone(sight.doorway_aperture(s.state, "c", "r"))
        self.assertEqual(sight.doorway_views(s.state, s.eye(3, 5)), [])
        s.state["entities"]["d"]["material"] = "glass"                      # a closed glazed door: its glass passes
        self.assertEqual(sight.doorway_aperture(s.state, "c", "r").factor, 0.9)
        s.state["entities"]["d"].update(open=True, size_m=[0.9, 0.05, 2.1])
        ap = sight.doorway_aperture(s.state, "c", "r")
        self.assertEqual((ap.factor, ap.z1, ap.door), (1.0, 2.1, "d"))
        s.state["connections"]["c"]["kind"] = "stairs"                      # no view between floors
        self.assertIsNone(sight.doorway_aperture(s.state, "c", "r"))
        s.state["connections"]["c"]["kind"] = "doorway"
        s.state["rooms"]["h"]["floor"], s.state["rooms"]["r"]["floor"] = 1, 0
        self.assertIsNone(sight.doorway_aperture(s.state, "c", "r"))

    def test_through_the_doorway_the_view_meets_both_rooms_occluders(self):
        s = TwoRooms()
        eye = s.eye(4, 5)                                                   # 2.25 m from the doorway, level with it
        ap = sight.doorway_views(s.state, eye)[0]
        target = (1.25, 1.75, 1.0)                                          # in the hall, straight through the doorway
        self.assertEqual(sight.through_transmission(s.state, ap, eye, target), 1.0)
        aside = (0.25, 0.25, 1.0)                                           # a corner of the hall: behind the wall
        self.assertEqual(sight.through_transmission(s.state, ap, eye, aside), 0.0)
        low = (2.75, 1.75, 2.4)                                             # above the 2 m doorway head line
        self.assertEqual(sight.through_transmission(s.state, ap, eye, low), 0.0)
        s.add("ward", "wardrobe", "h", pos=[3, 3], size_m=[0.5, 0.5, 2.0])  # in the hall, in the way
        geom = sight.room_geom(s.state, "h")
        self.assertIn("ward", {b[7] for b in geom.boxes})
        self.assertEqual(sight.through_transmission(s.state, ap, eye, target), 0.0)
        s.state["entities"]["ward"]["pos"] = [1, 6]
        self.assertEqual(sight.through_transmission(s.state, ap, eye, target), 1.0)
        s.add("crate", "box", "r", pos=[2, 5], size_m=[0.5, 0.5, 1.5])      # in r, between the eye and the doorway
        self.assertEqual(sight.through_transmission(s.state, ap, eye, target), 0.0)
        s.state["people"]["p"] = {"location": "h", "pos": [2, 3], "posture": "standing"}
        s.state["entities"]["crate"]["pos"] = [8, 8]
        self.assertAlmostEqual(sight.through_transmission(s.state, ap, eye, target), 0.3)   # a person attenuates

    def test_the_opening_viewshed_agrees_with_exact_segment_tests(self):
        # every free cell-centre target of the hall: the opening viewshed's answer equals an exact test of the segment
        # (through the opening, then los against every box of the hall from the wall on). The occluders fill whole
        # cells, where the viewshed's per-cell slope intervals are exact (its resolution is the cell, 5.2)
        s = TwoRooms(hcols=8, hrows=8, hall_cell=(7, 3))
        s.add("ward", "wardrobe", "h", pos=[4, 2], size_m=[0.5, 1.0, 2.0])
        s.add("crate", "box", "h", pos=[2, 5], size_m=[1.0, 0.5, 0.8])
        s.add("table", "table", "h", pos=[5, 5], size_m=[1.0, 1.0, 0.75])
        blocked = set(geometry.blocked_cells(s.state, "h", include_agents=False))
        checked = 0
        for c, r in ((2, 5), (6, 3), (5, 8)):
            eye = s.eye(c, r)
            ap = sight.doorway_views(s.state, eye)[0]
            eye_b = sight._eye_into(ap, eye)
            for cj in range(8):
                for ci in range(8):
                    if (ci, cj) in blocked:
                        continue
                    for z in (0.1, 0.6, 1.4):
                        checked += 1
                        p = ((ci + 0.5) * 0.5, (cj + 0.5) * 0.5, z)
                        got = sight.through_transmission(s.state, ap, eye, p)
                        found = sight._crossing(ap.into, eye_b.x, eye_b.y, eye_b.z, p[0], p[1], p[2])
                        if found is None or not (ap.into.lo <= found[1] <= ap.into.hi and found[2] <= 2.0):
                            want = 0.0
                        else:
                            t = found[0]
                            start = (eye_b.x + t * (p[0] - eye_b.x), eye_b.y + t * (p[1] - eye_b.y), found[2])
                            want = sight.los(s.state, "h", start, p)
                        self.assertAlmostEqual(got, want, msg=(c, r, ci, cj, z))
        self.assertEqual(checked, 522)

    def test_a_viewer_outside_a_window_sees_in_through_its_glass_not_its_wall_nor_its_coverings(self):
        s = Scene(12, 10)
        s.add("win", "window", pos=[5, 0], mounted=True, mount_height_m=0.9, size_m=[1.0, 0.1, 1.2], covered=0.0)
        s.state["people"]["near"] = {"location": "r", "pos": [5, 1], "posture": "standing"}
        s.state["people"]["aside"] = {"location": "r", "pos": [0, 1], "posture": "standing"}
        s.state["robot"]["pos"] = [10, 8]
        opening, factor = sight.aperture_opening(s.state, "win")
        self.assertEqual((opening.ny, opening.wall, opening.lo, opening.hi, opening.z0, opening.z1, factor),
                         (1, 0.0, 2.5, 3.5, 0.9, 2.1, 1.0))
        viewer = sight.aperture_viewer(s.state, "win")
        self.assertEqual((viewer.rid, viewer.x, viewer.y, viewer.z, viewer.yaw, viewer.kind),
                         ("r", 3.0, -3.0, 1.6, 90.0, "aperture:win"))
        self.assertEqual(sight.sees_entity(s.state, viewer, "near"), 1.0)
        self.assertEqual(sight.sees_entity(s.state, viewer, "aside"), 0.0)          # behind the wall
        s.state["entities"]["win"]["covered"] = 1.0                                     # blinds down: 5 % passes
        self.assertEqual(sight.aperture_opening(s.state, "win")[1], 0.05)
        self.assertEqual(sight.sees_entity(s.state, viewer, "near"), 0.0)
        self.assertIsNone(sight.aperture_viewer(s.state, "nothing"))


class PerformanceTests(unittest.TestCase):
    """Budgets of TIER1_CAMERA 16 / TIER1_INTEGRATION 3.4; the tests fail at 3x the budget."""

    @classmethod
    def setUpClass(cls):
        from house_sim.engine import HouseEnv
        cls.state = HouseEnv(seed=7).world.s

    def best_ms(self, fn, n=5):
        out = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            out.append(time.perf_counter() - t)
        return min(out) * 1000.0

    def test_viewsheds_lidar_and_warm_visibility_stay_in_budget(self):
        st = self.state
        indoor = outdoor = lidar = 0.0
        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        for rid in sorted(st["rooms"]):
            geom = sight.room_geom(st, rid)
            for c, r in ((0, 0), (geom.cols - 1, geom.rows - 1), (geom.cols // 2, geom.rows // 2)):
                x, y = (c + 0.5) * 0.5, (r + 0.5) * 0.5
                sight._compute_viewshed(geom, x, y, 1.5, None, 12.0)            # warm the ray templates
                ms = self.best_ms(lambda: sight._compute_viewshed(geom, x, y, 1.5, None, 12.0))
                if st["rooms"][rid].get("outdoor"):
                    outdoor = max(outdoor, ms)
                else:
                    indoor = max(indoor, ms)
            lidar = max(lidar, self.best_ms(lambda: sight.lidar_ring(st, (rid, x, y, 1.5), 0.0, geom)))
        self.assertLess(indoor, 3 * 0.3)
        self.assertLess(outdoor, 3 * 1.5)
        self.assertLess(lidar, 3 * 0.4)
        rid = st["robot"]["room"]
        geom = sight.room_geom(st, rid)
        eye = sight.Eye(rid, (st["robot"]["pos"][0] + 0.5) * 0.5, (st["robot"]["pos"][1] + 0.5) * 0.5, 1.5,
                        ("robot",))
        vs = sight.viewshed(geom, eye)
        cmap = sight.children_map(st)
        ids = sorted(e for e in st["entities"] if root_room(st, e) == rid)
        for e in ids:
            sight.visible_fraction(st, e, eye, geom, vs, cmap)
        per = self.best_ms(lambda: [sight.visible_fraction(st, e, eye, geom, vs, cmap) for e in ids]) / len(ids)
        self.assertLess(per, 3 * 0.05)


if __name__ == "__main__":
    unittest.main()
