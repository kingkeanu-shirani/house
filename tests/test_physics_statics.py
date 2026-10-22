"""Tier-1 physics, statics layer (kernel/phys/support.py, statics.py, impact.py; docs/design/TIER1_PHYSICS.md 5.1-5.5,
6.4 and the test plan 12.3).

The data layer (props, PHYS-DATA) is real: it hands over before this package. The fluids layer (liquids.py and
processes.py, PHYS-FLUIDS) hands over after it, so the tests replace those two collaborators with small local fakes,
as they do forces.friction (contact_mu reads the item's Phys.mu instead). Every scenario runs on a World whose
journal is rolled back at the end and compared with the starting state: a write that skipped the journal fails.
"""
import contextlib
import math
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from house_sim.kernel import geometry, plugins
from house_sim.kernel.phys import impact, statics, support
from house_sim.kernel.registry import default_registry
from house_sim.kernel.world import World, clone

REG = default_registry()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----- fixtures -------------------------------------------------------------------------------------------------------

def house(floor="tile", cols=8, rows=8, slope=None):
    """A one-room state (cols x rows cells of 0.5 m) with a floor entity; the robot stands at [4, 4], step 1, 100 s."""
    room = {"label": "room", "type": "kitchen", "size_m": [cols * 0.5, rows * 0.5], "floor_material": floor,
            "exits": [], "outdoor": False}
    if slope is not None:
        room["slope_deg"] = slope
    state = {"rooms": {"room": room}, "entities": {}, "people": {}, "pets": {}, "connections": {},
             "robot": {"room": "room", "pos": [4, 4], "hands": {"left": None, "right": None}, "speed_mode": "normal",
                       "posture": "standing"},
             "steps": 1, "time_s": 100.0, "stats": {}, "event_log": [], "logs": {"moves": []}}
    state["entities"]["floor_room"] = REG.make("floor_surface", "room", material=floor, pos=[0, 0], ground_layer=True,
                                               size_m=[cols * 0.5, rows * 0.5, 0.01])
    return state


# Household items the core type data does not define (their types belong to domains, which a kernel test must not
# need: G1b runs it with HOUSE_SIM_DOMAINS=core), as a core base type plus the physical fields the scenarios rely on.
# Sizes and masses are those of ordinary items (a 250 g tumbler, a 1.25 kg wine bottle, a 190 g phone).
DISH = dict(container=True, accepts_items=True, category="dish")
KINDS = {
    "drinking_glass": ("portable_object", dict(DISH, material="glass", size_m=[0.08, 0.08, 0.13], mass_kg=0.25,
                                               capacity_l=0.35, fragile=True)),
    "wine_glass": ("portable_object", dict(DISH, material="glass", size_m=[0.09, 0.09, 0.2], mass_kg=0.2,
                                           capacity_l=0.35, fragile=True)),
    "mug": ("portable_object", dict(DISH, material="ceramic", size_m=[0.12, 0.09, 0.1], mass_kg=0.35, capacity_l=0.35,
                                    fragile=True)),
    "cereal_bowl": ("portable_object", dict(DISH, material="ceramic", size_m=[0.16, 0.16, 0.065], mass_kg=0.35,
                                            capacity_l=0.7, fragile=True)),
    "dinner_plate": ("portable_object", dict(material="ceramic", size_m=[0.27, 0.27, 0.025], mass_kg=0.6, surface=True,
                                             accepts_items=True, surface_size_m=[0.26, 0.26], category="dish",
                                             fragile=True)),
    "wine_bottle": ("portable_object", dict(material="glass", size_m=[0.075, 0.075, 0.3], mass_kg=1.25, capacity_l=0.75,
                                            category="food")),
    "vase": ("portable_object", dict(material="glass", size_m=[0.12, 0.12, 0.3], mass_kg=0.9, drop_tolerance_m=0.12,
                                     category="decor")),
    "water_bottle": ("portable_object", dict(material="stainless_steel", size_m=[0.075, 0.075, 0.26], mass_kg=0.35,
                                             capacity_l=0.75, openable=True, container=True)),
    "cardboard_box": ("portable_object", dict(material="cardboard", size_m=[0.4, 0.3, 0.3], mass_kg=0.4, capacity_l=36.0,
                                              container=True, accepts_items=True)),
    "smartphone": ("portable_object", dict(material="plastic", size_m=[0.075, 0.155, 0.008], mass_kg=0.19,
                                           category="phone")),
    "orange": ("portable_object", dict(material="organic", size_m=[0.075, 0.075, 0.075], mass_kg=0.2, shape="sphere",
                                       category="food")),
    "apple": ("portable_object", dict(material="organic", size_m=[0.08, 0.08, 0.08], mass_kg=0.18, shape="sphere",
                                      category="food")),
    "bath_towel": ("portable_object", dict(material="fabric", size_m=[0.4, 0.35, 0.15], mass_kg=0.55, category="towel")),
    "raincoat": ("portable_object", dict(material="fabric", size_m=[0.3, 0.25, 0.1], mass_kg=0.6, category="garment")),
    "grocery_bag": ("bag", dict(material="canvas", size_m=[0.42, 0.22, 0.38], mass_kg=0.15, capacity_l=25.0)),
    "towel_bar": ("towel_hook", dict(material="steel", size_m=[0.6, 0.08, 0.05], max_load_kg=8.0)),
    "adhesive_hook_small": ("key_hook", dict(hang_slots=1, adhesive="stretch_release", hardware_kind="adhesive_hook",
                                             hook_rating_kg=0.45, mass_kg=0.005, size_m=[0.03, 0.02, 0.04])),
    "fence": ("fixture", dict(category="fence", size_m=[1.0, 0.1, 1.8])),
    "tree": ("fixture", dict(category="plant", size_m=[0.4, 0.4, 6.0], mass_kg=800.0, blocks_path=True)),
}


def record(type_name, location, **props):
    """A record of a core type, or of a KINDS item; exact thresholds unless the test asks for variation."""
    base, fields = KINDS.get(type_name, (type_name, {}))
    values = dict(fields, label=type_name.replace("_", " ")) if type_name in KINDS else {}
    values.update(props)
    values.setdefault("phys_exact", True)
    return REG.make(base, location, **values)


def put(state, eid, type_name, location, **props):
    """Add an entity to a state before its World starts."""
    state["entities"][eid] = record(type_name, location, **props)
    return eid


def add(w, eid, type_name, location, **props):
    """Add an entity through the World journal (after the scenario started)."""
    w.create(eid, record(type_name, location, **props))
    return eid


def hold(state, eid, hands=("right",)):
    """Put eid in the robot's hands."""
    rec = state["entities"][eid]
    rec.update(location="robot", relation="held", held_hand="both" if len(hands) == 2 else hands[0])
    for hand in hands:
        state["robot"]["hands"][hand] = eid


def kinds(state):
    return [entry["kind"] for entry in state["event_log"]]


class FakeLiquids(object):
    """liquids.py stand-in: the closure rule and tip spill of TIER1_PHYSICS 5.7; records what physics releases."""

    def __init__(self):
        self.released = []

    @staticmethod
    def container_closed(rec):
        return bool(rec.get("sealed") or rec.get("lid_on") or (rec.get("openable") and not rec.get("open")))

    @staticmethod
    def tip_spill_l(rec, orientation):
        liters = float(rec.get("liquid_l") or 0.0)
        if orientation == "upside_down":
            return liters
        mouth = float(rec.get("mouth_frac", 1.0))
        keep = float(rec.get("capacity_l") or 0.0) * (1.0 - mouth) / 2.0 if mouth < 0.6 else 0.0
        return max(0.0, liters - keep)

    def deposit_below(self, w, eid, liters, liquid, source=None, by=None):
        self.released.append((eid, round(liters, 4), liquid, by))


class FakeProcesses(object):
    """processes.py stand-in: records queued checks and started processes; roll_accel is set per test."""

    def __init__(self):
        self.queue, self.started, self.accel = [], [], 0.0

    def queue_add(self, w, due_s, kind, eid, **args):
        self.queue.append((round(float(due_s), 3), kind, eid, args))

    def queued(self, state, kind, eid):
        return [entry for entry in self.queue if entry[1] == kind and entry[2] == eid]

    def start(self, w, kind, eid, **values):
        self.started.append((kind, eid))
        return True

    def roll_accel(self, state, eid):
        return self.accel


@contextlib.contextmanager
def hooked(name, fn):
    """Register a kernel hook for the duration of a test (and leave the plugin tables as they were)."""
    existed = name in plugins.HOOKS
    entry = (100, "tests." + fn.__name__, fn)
    plugins.HOOKS[name].append(entry)
    try:
        yield
    finally:
        plugins.HOOKS[name].remove(entry)
        if not existed and not plugins.HOOKS[name]:
            del plugins.HOOKS[name]


class PhysCase(unittest.TestCase):
    def setUp(self):
        self.liquids, self.processes = FakeLiquids(), FakeProcesses()
        for target, name, value in ((impact, "_liquids", lambda: self.liquids),
                                    (impact, "_processes", lambda: self.processes),
                                    (statics, "roll_accel_mps2", self.processes.roll_accel),
                                    (support, "contact_mu", lambda state, eid: support.phys(state, eid).mu)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def world(self, state):
        """A World on state with an open journal; the test's end rolls it back and checks nothing escaped it."""
        before = clone(state)
        w = World(state, REG)
        w.begin()

        def check():
            json.dumps(state, allow_nan=False)            # JSON-safe: no inf / nan / tuples-only values left
            w.rollback()
            self.assertEqual(state, before, "a physics write bypassed the World journal")
        self.addCleanup(check)
        return w


# ----- support graph, masses and loads --------------------------------------------------------------------------------

class SupportGraphTests(PhysCase):
    def test_support_of_names_every_slot(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "book", "book", "shelf", relation="in", shelf=3)
        put(s, "box", "cardboard_box", "room", pos=[5, 5])
        put(s, "toy", "book", "box", relation="in")
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "mug", "mug", "table", relation="on", spos=[0.1, 0.1])
        put(s, "bar", "towel_bar", "room", pos=[7, 2], mounted=True, mount_height_m=1.2)
        put(s, "towel", "bath_towel", "bar", relation="hung")
        put(s, "knob", "book", "table", relation="attached")
        put(s, "lost", "book", "table", relation="under")
        put(s, "phone", "smartphone", "room", pos=[1, 1])
        put(s, "keys", "book", "robot")
        hold(s, "keys")
        s["people"]["ann"] = {"location": "room", "pos": [6, 6]}
        put(s, "cup", "mug", "ann", relation="held")
        put(s, "hat", "book", "robot", relation="worn")
        self.assertEqual(support.support_of(s, "book"), ("shelf", ("shelf", 3)))
        self.assertEqual(support.support_of(s, "toy"), ("box", ("in",)))
        self.assertEqual(support.support_of(s, "mug"), ("table", ("surface",)))
        self.assertEqual(support.support_of(s, "towel"), ("bar", ("hung",)))
        self.assertEqual(support.support_of(s, "phone"), ("room", ("floor", (1, 1))))
        self.assertEqual(support.support_of(s, "keys"), ("robot", ("held",)))
        self.assertEqual(support.support_of(s, "cup"), ("ann", ("held",)))
        for rigid in ("knob", "lost", "bar", "hat", "floor_room"):
            self.assertEqual(support.support_of(s, rigid), ("room", ("floor", (0, 0))) if rigid == "floor_room" else None)
        self.assertIsNone(support.support_of(s, "missing"))

    def test_children_map_slots_and_incremental_moves(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        for name, shelf in (("b3", 3), ("b1", 1), ("a1", 1), ("b0", 0)):
            put(s, name, "book", "shelf", relation="in", shelf=shelf)
        cmap = support.children_map(s)
        self.assertEqual(cmap["shelf"], ["a1", "b0", "b1", "b3"])
        self.assertEqual(list(support.slots(s, "shelf", cmap)), [("shelf", 0), ("shelf", 1), ("shelf", 3)])
        self.assertEqual(support.slot_children(s, "shelf", ("shelf", 1), cmap), ["a1", "b1"])
        support.move_child(cmap, "b3", "shelf", "room")
        self.assertNotIn("b3", cmap["shelf"])
        self.assertEqual(cmap["room"], sorted(cmap["room"]))
        self.assertIn("b3", cmap["room"])
        support.move_child(None, "b3", "room", "shelf")             # no map: nothing to keep

    def test_top_support_and_descendants_follow_the_world_order(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "tray", "tray", "table", relation="on", spos=[0.1, 0.1])
        for name in ("c", "a", "b"):
            put(s, name, "mug", "tray", relation="on")
        put(s, "spoon", "book", "a", relation="in")
        put(s, "dresser", "dresser", "room", pos=[4, 0])
        put(s, "drawer", "cardboard_box", "dresser", relation="part")
        put(s, "sock", "book", "drawer", relation="in")
        self.assertEqual(support.top_support(s, "spoon"), "table")
        self.assertEqual(support.top_support(s, "sock"), "dresser")
        self.assertEqual(support.top_support(s, "table"), "table")
        w = World(s, REG)
        for eid in ("table", "tray", "dresser"):
            self.assertEqual(support.descendants(s, eid), w.descendants(eid))

    def test_subtree_mass_reproduces_core_exactly_and_uses_liquid_densities(self):
        s = house()
        put(s, "crate", "cardboard_box", "room", pos=[2, 2], mass_kg=0.7)
        put(s, "jug", "water_bottle", "crate", relation="in", mass_kg=0.35, liquid_l=0.6)
        put(s, "oil", "water_bottle", "crate", relation="in", mass_kg=0.3, liquid_l=0.5, liquid_density=0.92)
        put(s, "cap", "book", "jug", relation="on", mass_kg=0.013)
        w = World(s, REG)
        core_rule = 0.0                       # systems.core.subtree_mass before tier 1 (water is 1.0 kg/L)
        for item in ["crate"] + w.descendants("crate"):
            rec = s["entities"][item]
            core_rule += float(rec.get("mass_kg", 0.0)) + float(rec.get("liquid_l", 0.0)) * float(rec.get("liquid_density", 1.0))
        self.assertEqual(support.subtree_mass(s, "crate"), core_rule)
        self.assertEqual(support.subtree_mass(s, "crate", support.children_map(s)), core_rule)
        s["entities"]["jug"]["liquid"] = "milk"                        # liquids.json: 1030 kg/m3
        from house_sim.kernel.phys import props
        milk = props.liquid_props("milk")["rho"] / 1000.0
        self.assertGreater(milk, 1.0)
        self.assertAlmostEqual(support.subtree_mass(s, "crate"), core_rule + 0.6 * (milk - 1.0), places=9)
        self.assertEqual(support.subtree_mass(s, "nothing"), 0.0)

    def test_loads_on_slots_and_above_items_with_the_packing_hook(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "a", "book", "shelf", relation="in", shelf=1, mass_kg=2.0)
        put(s, "b", "book", "shelf", relation="in", shelf=1, mass_kg=3.0)
        put(s, "c", "book", "b", relation="on", mass_kg=0.5)
        put(s, "d", "book", "shelf", relation="in", shelf=2, mass_kg=9.0)
        self.assertAlmostEqual(support.load_on(s, "shelf", ("shelf", 1)), 5.5)
        self.assertAlmostEqual(support.load_above(s, "b"), 0.5)

        def packed_above(state, eid):
            return ["d"] if eid == "a" else []
        with hooked("phys.items_above", packed_above):
            self.assertAlmostEqual(support.load_above(s, "a"), 9.0)
        self.assertEqual(support.load_above(s, "a"), 0.0)

    def test_ratings_come_from_props_and_only_fixed_slots_fail(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "tray", "tray", "table", relation="on")
        put(s, "bar", "towel_bar", "room", pos=[7, 2], mounted=True)
        put(s, "bag", "grocery_bag", "room", pos=[5, 5])
        self.assertEqual(support.rating(s, "shelf", ("shelf", 2)), 25.0)          # particleboard bookshelf per shelf
        self.assertEqual(support.rating(s, "table", ("surface",)), 50.0)
        self.assertEqual(support.rating(s, "bar", ("hung",)), 8.0)               # towel bar: max_load_kg is a hang rating
        self.assertIsNone(support.rating(s, "tray", ("surface",)))                # a portable support at rest never fails
        self.assertIsNone(support.rating(s, "bag", ("in",)))
        self.assertIsNone(support.rating(s, "room", ("floor", (0, 0))))
        self.assertIsNone(support.rating(s, "robot", ("held",)))
        s["entities"]["table"]["max_load_kg"] = 33.0
        self.assertEqual(support.rating(s, "table", ("surface",)), 33.0)

    def test_baseline_load_is_memoised_per_episode_baseline(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "a", "book", "shelf", relation="in", shelf=1, mass_kg=30.0)
        env = SimpleNamespace(_baseline=clone(s))
        self.assertEqual(support.baseline_load(env, "shelf", ("shelf", 1)), 30.0)
        env._baseline["entities"]["a"]["mass_kg"] = 99.0                 # memo: the baseline is fixed per episode
        self.assertEqual(support.baseline_load(env, "shelf", ("shelf", 1)), 30.0)
        env._baseline = clone(env._baseline)                               # a new episode recomputes
        self.assertEqual(support.baseline_load(env, "shelf", ("shelf", 1)), 99.0)
        self.assertEqual(support.baseline_load(env, "shelf", ("shelf", 4)), 0.0)
        self.assertEqual(support.baseline_load(env, "new_shelf", ("shelf", 1)), 0.0)
        self.assertEqual(support.baseline_load(None, "shelf", ("shelf", 1)), 0.0)

    def test_resting_size_and_composite_centre_of_mass(self):
        s = house()
        put(s, "bottle", "wine_bottle", "room", pos=[1, 1])
        self.assertEqual(support.resting_size(s, "bottle"), [0.075, 0.075, 0.3])
        s["entities"]["bottle"]["orientation"] = "on_side"
        self.assertEqual(support.resting_size(s, "bottle"), [0.3, 0.075, 0.075])
        put(s, "shelf", "bookshelf", "room", pos=[2, 0], com_frac=0.5)
        empty = support.com_height(s, "shelf")
        self.assertAlmostEqual(empty, 0.9)                                 # 0.5 x 1.8 m
        put(s, "top", "book", "shelf", relation="in", shelf=4, mass_kg=20.0, size_m=[0.3, 0.25, 0.2])
        high = support.com_height(s, "shelf")
        expected = (30.0 * 0.9 + 20.0 * (1.65 + 0.1)) / 50.0                # heavy books on the top shelf raise it
        self.assertAlmostEqual(high, expected)
        s["entities"]["top"]["shelf"] = 0
        self.assertLess(support.com_height(s, "shelf"), empty)
        put(s, "jar", "water_bottle", "room", pos=[6, 6], mass_kg=0.25, liquid_l=0.75, com_frac=0.5)
        rec = s["entities"]["jar"]
        expected = (0.25 * 0.5 * 0.26 + 0.75 * 0.26 / 2.0) / 1.0           # a full container: liquid centred at h / 2
        self.assertAlmostEqual(support.com_height(s, "jar"), expected)
        rec["liquid_l"] = 0.375
        self.assertLess(support.com_height(s, "jar"), expected)

    def test_location_cycles_end_the_recursion(self):
        """Review finding: subtree_mass, descendants and top_support guard against location cycles in malformed
        states, but com_height and the held-load helpers raised RecursionError."""
        s = house()
        put(s, "a", "book", "b", relation="on", mass_kg=1.0)
        put(s, "b", "book", "a", relation="on", mass_kg=2.0)
        self.assertEqual(support.subtree_mass(s, "b"), 3.0)
        self.assertEqual(support.top_support(s, "a"), "b")
        cmap = support.children_map(s)
        for eid in ("a", "b"):
            self.assertTrue(math.isfinite(support.com_height(s, eid)))
            self.assertTrue(math.isfinite(statics.tip_accel_g(s, eid)))
            self.assertTrue(math.isfinite(support._load_top(s, eid, 1.0, cmap)))
            mass, moment = support._load_moment(s, eid, 1.0, cmap)
            self.assertEqual(mass, 3.0)                                          # each body counted once
            self.assertTrue(math.isfinite(moment))

    def test_held_envelope_matches_the_camera_carry_model(self):
        s = house()
        self.assertIsNone(support.held_envelope(s))
        put(s, "basket", "laundry_basket", "room", size_m=[0.6, 0.4, 0.3], mass_kg=0.8)
        put(s, "shirt", "book", "basket", relation="in", mass_kg=1.2, size_m=[0.3, 0.3, 0.05])
        hold(s, "basket", ("left", "right"))
        env = support.held_envelope(s)
        # TIER1_INTEGRATION 1.6: bottom 0.85 (hip carry), top 1.15, front 0.35 + 0.4 / 2 = 0.55 m
        self.assertEqual((env["bottom_m"], env["top_m"], env["front_m"]), (0.85, 1.15, 0.55))
        self.assertEqual((env["width_m"], env["depth_m"], env["height_m"]), (0.6, 0.4, 0.3))
        self.assertTrue(env["two_handed"] and env["blocks_view"])
        self.assertEqual(env["items"], ["basket", "shirt"])
        self.assertAlmostEqual(env["mass_kg"], 2.0)
        self.assertTrue(0.85 < env["com_m"] < 1.15)
        s["entities"]["basket"]["carry_level"] = True                     # a tray carried level never blocks the view
        tray = support.held_envelope(s)
        self.assertFalse(tray["blocks_view"])
        self.assertAlmostEqual(tray["bottom_m"], 1.0 - 0.15)

    def test_held_envelope_one_hand_loads_and_things_on_top(self):
        s = house()
        put(s, "tray", "tray", "room", size_m=[0.5, 0.35, 0.03], carry_level=True)
        put(s, "glass", "drinking_glass", "tray", relation="on")
        hold(s, "tray", ("right",))
        put(s, "mug", "mug", "room")
        hold(s, "mug", ("left",))
        env = support.held_envelope(s)
        self.assertFalse(env["two_handed"])
        self.assertFalse(env["blocks_view"])
        self.assertAlmostEqual(env["top_m"], 1.0 - 0.015 + 0.03 + 0.13)       # the glass stands on the tray
        self.assertAlmostEqual(env["bottom_m"], 1.0 - 0.05)                    # the mug hangs lower than the tray
        self.assertAlmostEqual(env["width_m"], 0.5 + 0.12)                     # loads in two hands sit side by side
        self.assertAlmostEqual(env["front_m"], 0.35 + 0.35 / 2.0)
        self.assertEqual(env["items"], ["glass", "mug", "tray"])

    def test_anchor_cells(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "mug", "mug", "table", relation="on")
        self.assertEqual(support.anchor_cells(s, "mug", "room"), [(0, 4), (0, 5), (1, 4), (1, 5), (2, 4), (2, 5)])
        self.assertEqual(support.anchor_cells(s, "mug", "elsewhere"), [])


# ----- statics -----------------------------------------------------------------------------------------------------------

class StaticsTests(PhysCase):
    def test_wine_glass_tips_at_035_g_and_a_mug_does_not(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        # TIER1_PHYSICS 5.4 anchors: an empty wine glass (b 7 cm, z 11 cm) tips at 0.32 g; a mug (b 8, z 5) needs 0.8 g
        put(s, "wine", "wine_glass", "table", relation="on", spos=[0.5, 0.3], size_m=[0.07, 0.07, 0.1833], com_frac=0.6)
        put(s, "mug", "mug", "table", relation="on", spos=[0.2, 0.3], size_m=[0.08, 0.08, 0.1])
        self.assertAlmostEqual(statics.tip_accel_g(s, "wine"), 0.318, places=3)
        self.assertAlmostEqual(statics.tip_accel_g(s, "mug"), 0.8, places=6)
        w = self.world(s)
        self.assertTrue(impact.knock(w, "wine", 0.35, direction=90.0)["tipped"])
        self.assertEqual(s["entities"]["wine"]["orientation"], "on_side")
        self.assertEqual(impact.knock(w, "mug", 0.35, direction=90.0),
                         {"tipped": False, "slid_m": 0.0, "fell": False, "broke": False})
        self.assertNotIn("orientation", s["entities"]["mug"])

    def test_tip_threshold_uses_the_base_along_the_push(self):
        s = house()
        put(s, "book", "book", "room", pos=[1, 1], size_m=[0.15, 0.03, 0.23], com_frac=0.5)
        self.assertAlmostEqual(statics.tip_accel_g(s, "book"), 0.03 / 0.23)
        self.assertAlmostEqual(statics.tip_accel_g(s, "book", axis=0.0), 0.15 / 0.23)
        self.assertAlmostEqual(statics.tip_accel_g(s, "book", axis=90.0), 0.03 / 0.23)
        s["entities"]["book"]["rot"] = 90                                  # turned a quarter: the axes swap
        self.assertAlmostEqual(statics.tip_accel_g(s, "book", axis=0.0), 0.03 / 0.23)
        self.assertEqual(statics.footprint_wd(s, "book"), (0.03, 0.15))

    def test_edge_margin_overhang_and_fall_direction(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])                      # top 1.2 x 0.8 m
        put(s, "plate", "dinner_plate", "table", relation="on", spos=[0.9, 0.3])
        self.assertAlmostEqual(statics.edge_margin_m(s, "plate"), 1.2 - (0.9 + 0.135))
        self.assertEqual(statics.overhang_frac(s, "plate"), 0.0)
        s["entities"]["plate"]["spos"] = [1.0, 0.3]                       # sticks out 7 cm, its centre still on it
        self.assertAlmostEqual(statics.edge_margin_m(s, "plate"), 1.2 - 1.135)
        self.assertAlmostEqual(statics.overhang_frac(s, "plate"), 0.07 / 0.27)
        s["entities"]["plate"]["spos"] = [1.1, 0.3]
        self.assertLess(statics.edge_margin_m(s, "plate"), 0)
        self.assertAlmostEqual(statics.overhang_frac(s, "plate"), (1.1 + 0.27 - 1.2) / 0.27)
        off = math.degrees(math.atan2(0.3 + 0.135 - 0.4, 1.1 + 0.135 - 0.6))           # off the +x edge
        self.assertAlmostEqual(statics.overhang_dir_deg(s, "plate"), off)
        s["entities"]["table"]["rot"] = 90                                                # turned with the table
        self.assertAlmostEqual(statics.overhang_dir_deg(s, "plate"), off + 90.0)
        self.assertIsNone(statics.edge_margin_m(s, "table"))

    def test_slope_verdicts(self):
        s = house()
        put(s, "tray", "tray", "room", pos=[2, 2], tilt_deg=30.0)
        put(s, "mug", "mug", "tray", relation="on", size_m=[0.08, 0.08, 0.1])       # tan 30 = 0.58 > mu 0.5
        put(s, "vase", "vase", "tray", relation="on", size_m=[0.08, 0.08, 0.4])      # b / 2z = 0.2 < 0.58
        self.assertEqual(statics.slope_verdict(s, "mug"), "slide")
        self.assertEqual(statics.slope_verdict(s, "vase"), "tip")
        s["entities"]["tray"]["tilt_deg"] = 10.0                                     # tan 10 = 0.18: both stay
        self.assertIsNone(statics.slope_verdict(s, "mug"))
        self.assertEqual(statics.slope_verdict(s, "vase"), None)
        self.processes.accel = 0.4
        self.assertEqual(statics.slope_verdict(s, "mug"), "roll")
        s["entities"]["mug"]["secured"] = True
        self.assertIsNone(statics.slope_verdict(s, "mug"))
        flat = house(slope=None)
        put(flat, "mug", "mug", "room", pos=[1, 1])
        self.assertIsNone(statics.slope_verdict(flat, "mug"))

    def test_only_free_standing_things_move(self):
        s = house(slope=12.0)
        put(s, "fence", "fence", "room", pos=[0, 0])
        put(s, "tree", "tree", "room", pos=[3, 3], rooted=True)
        put(s, "shelf", "wall_shelf", "room", pos=[5, 0], mounted=True)
        put(s, "bin", "laundry_basket", "room", pos=[6, 6])
        put(s, "bookcase", "bookshelf", "room", pos=[2, 6])
        put(s, "cart", "book", "room", pos=[6, 2], portable=False, wheeled=True, category="cart")
        self.assertFalse(statics.free_standing(s, "fence"))
        self.assertFalse(statics.free_standing(s, "tree"))
        self.assertFalse(statics.free_standing(s, "shelf"))
        self.assertTrue(statics.free_standing(s, "bin"))
        self.assertTrue(statics.free_standing(s, "bookcase"))
        self.assertTrue(statics.free_standing(s, "cart"))                                # on wheels it can roll away
        s["entities"]["bookcase"]["anchored"] = True
        self.assertFalse(statics.free_standing(s, "bookcase"))
        for eid in ("fence", "tree", "shelf"):
            self.assertIsNone(statics.slope_verdict(s, eid))

    def test_rolling_resistance(self):
        s = house(floor="carpet")
        put(s, "ball", "orange", "room", pos=[1, 1])
        put(s, "bed", "bed", "room", pos=[4, 0])
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "a", "orange", "bed", relation="on")
        put(s, "b", "orange", "table", relation="on")
        self.assertEqual(statics.rolling_resistance(s, "ball"), 0.06)              # floors.json carpet
        self.assertEqual(statics.rolling_resistance(s, "b"), statics.C_RR_HARD)
        s["entities"]["bed"]["softness"] = 0.8
        self.assertEqual(statics.rolling_resistance(s, "a"), statics.C_RR_SOFT)

    def test_stack_rules(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "p1", "dinner_plate", "table", relation="on", spos=[0.1, 0.1])
        put(s, "p2", "dinner_plate", "p1", relation="on")
        put(s, "big", "tray", "p2", relation="on", size_m=[0.5, 0.35, 0.03])         # > 1.25 x the plate: rocks off
        put(s, "cup", "mug", "big", relation="on")
        self.assertEqual(statics.stack_base(s, "cup"), "p1")
        self.assertEqual(statics.stack_chain(s, "p1"), ["p1", "p2", "big", "cup"])
        self.assertEqual(statics.stack_verdict(s, "p1"), [("big", "topple"), ("cup", "topple")])
        self.assertEqual(statics.stack_verdict(s, "table"), [])                       # a fixed support is not a stack
        del s["entities"]["cup"], s["entities"]["big"]
        self.assertEqual(statics.stack_verdict(s, "p1"), [])
        s["entities"]["p2"]["stack_offset_m"] = [0.09, 0.0]                           # > 0.3 x 0.27 m off centre
        self.assertEqual(statics.stack_verdict(s, "p1"), [("p2", "topple")])
        s["entities"]["p2"].pop("stack_offset_m")
        # slenderness: height / base > 4 is wobbly (0.48 m on 0.1 m), > 6 topples from the middle up (0.64 m)
        put(s, "b1", "book", "table", relation="on", spos=[0.6, 0.4], size_m=[0.1, 0.1, 0.16])
        put(s, "b2", "book", "b1", relation="on", size_m=[0.1, 0.1, 0.16])
        put(s, "b3", "book", "b2", relation="on", size_m=[0.1, 0.1, 0.16])
        self.assertEqual(statics.stack_verdict(s, "b1"), [("b1", "wobbly"), ("b2", "wobbly"), ("b3", "wobbly")])
        self.assertTrue(statics.stability(s, "b2")["wobbly"])
        self.assertFalse(statics.stability(s, "p2")["wobbly"])
        put(s, "b4", "book", "b3", relation="on", size_m=[0.1, 0.1, 0.16])
        self.assertEqual(statics.stack_verdict(s, "b1"), [("b3", "topple"), ("b4", "topple")])
        s["entities"]["b2"]["secured"] = True                                         # fastened: the chain ends below it
        self.assertEqual(statics.stack_chain(s, "b1"), ["b1"])

    def test_nested_bowls_and_the_shelf_gap(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "bowl1", "cereal_bowl", "shelf", relation="in", shelf=1, nest_group="bowls")
        put(s, "bowl2", "cereal_bowl", "bowl1", relation="on", nest_group="bowls", size_m=[0.15, 0.15, 0.065])
        self.assertAlmostEqual(statics.stack_height_m(s, ["bowl1", "bowl2"]), 0.02 + 0.065)
        self.assertEqual(statics.stack_verdict(s, "bowl1"), [])
        self.assertAlmostEqual(statics.clearance_above_m(s, "bowl1"), 0.85 - 0.45 - 0.02)
        put(s, "b1", "book", "shelf", relation="in", shelf=2, size_m=[0.25, 0.2, 0.2])
        put(s, "b2", "book", "b1", relation="on", size_m=[0.25, 0.2, 0.2])         # 0.4 m > the 0.38 m gap
        self.assertEqual(statics.stack_verdict(s, "b1"), [("b2", "topple")])
        self.assertIsNone(statics.clearance_above_m(s, "shelf"))

    def test_a_thin_flat_base_is_part_of_the_surface(self):
        """Adoption-round request (electrical 1): the oversize rule toppled a 0.155 m phone centred on a 0.10 m, 12 mm
        Qi pad (and a watch on its puck). An item centred over a base 15 mm thin or less rests on it and the surface
        around; off the pad's footprint, or on a plate (25 mm), the rules apply as before."""
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "pad", "generic_item", "table", relation="on", spos=[0.2, 0.2], size_m=[0.1, 0.1, 0.012])
        put(s, "phone", "generic_item", "pad", relation="on", size_m=[0.075, 0.155, 0.008])
        self.assertEqual(statics.stack_verdict(s, "pad"), [])
        s["entities"]["phone"]["stack_offset_m"] = [0.0, 0.06]                         # its centre beyond the pad
        self.assertEqual(statics.stack_verdict(s, "pad"), [("phone", "topple")])
        s["entities"]["phone"].pop("stack_offset_m")
        s["entities"]["pad"]["size_m"] = [0.1, 0.1, 0.025]                             # as thick as a plate
        self.assertEqual(statics.stack_verdict(s, "pad"), [("phone", "topple")])

    def test_a_small_item_anywhere_on_a_larger_one_stays(self):
        """Adoption-round request (textiles_laundry 1): the centre-offset rule (0.3 of the lower footprint) toppled a
        receipt lying near the edge of a 0.5 m tray. A member wholly on the one below stays; one that reaches past it
        and is far enough off centre still topples."""
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "tray", "tray", "table", relation="on", spos=[0.1, 0.1], size_m=[0.5, 0.35, 0.03])
        put(s, "receipt", "generic_item", "tray", relation="on", size_m=[0.08, 0.12, 0.002], stack_offset_m=[0.2, 0.0])
        self.assertEqual(statics.stack_verdict(s, "tray"), [])                         # 0.2 + 0.04 <= 0.25
        s["entities"]["receipt"]["stack_offset_m"] = [0.23, 0.0]                       # reaches 2 cm past the rim
        self.assertEqual(statics.stack_verdict(s, "tray"), [("receipt", "topple")])

    def test_the_gap_above_an_oven_rack_is_to_the_next_rack(self):
        """Adoption-round request (appliances_cooking 1): an empty rack position is no shelf. In a cavity whose shelves
        are removable racks, the room above a rack reaches the next position a rack takes, else the cavity top; fixed
        shelf boards keep the next board (test_nested_bowls_and_the_shelf_gap)."""
        s = house()
        put(s, "oven", "generic_item", "room", pos=[2, 0], portable=False, container=True, accepts_items=True,
            size_m=[0.76, 0.66, 0.55], inner_size_m=[0.62, 0.46, 0.4], shelf_heights=[0.03, 0.09, 0.15, 0.21, 0.27, 0.33])
        rack = dict(size_m=[0.6, 0.42, 0.02], surface=True, accepts_items=True, portable=True, relation="in")
        put(s, "rack_a", "generic_item", "oven", shelf=1, **rack)
        put(s, "rack_b", "generic_item", "oven", shelf=3, **rack)
        put(s, "dish", "generic_item", "rack_b", relation="on", size_m=[0.3, 0.2, 0.08])
        self.assertAlmostEqual(statics.clearance_above_m(s, "rack_b"), 0.4 - 0.21)
        self.assertAlmostEqual(statics.clearance_above_m(s, "rack_a"), 0.21 - 0.09 - statics.SHELF_GAP_MARGIN_M)
        self.assertEqual(statics.stack_verdict(s, "rack_b"), [])
        put(s, "rack_c", "generic_item", "oven", shelf=4, **rack)                       # a rack 6 cm above
        self.assertAlmostEqual(statics.clearance_above_m(s, "rack_b"), 0.27 - 0.21 - statics.SHELF_GAP_MARGIN_M)
        self.assertEqual(statics.stack_verdict(s, "rack_b"), [("dish", "topple")])

    def test_stability_report(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "wine", "wine_glass", "table", relation="on", spos=[1.1, 0.3], size_m=[0.07, 0.07, 0.1833], com_frac=0.6)
        report = statics.stability(s, "wine")
        self.assertEqual(set(report), {"overhang", "tip_g", "slide_g", "rolls", "wobbly"})
        self.assertEqual(report["tip_g"], round(0.07 / (2 * 0.6 * 0.1833), 4))
        self.assertEqual(report["slide_g"], 0.4)
        self.assertEqual(report["overhang"], 0.0)
        self.assertFalse(report["rolls"] or report["wobbly"])
        s["entities"]["wine"]["spos"] = [1.16, 0.3]
        self.assertAlmostEqual(statics.stability(s, "wine")["overhang"], round(0.03 / 0.07, 4))
        s["entities"]["wine"]["orientation"] = "on_side"
        self.assertEqual(statics.stability(s, "wine")["tip_g"], round(0.07 / 0.07, 4))      # lying: z = d / 2

    def test_dresser_with_three_open_drawers_and_a_27_kg_pull(self):
        s = house()
        put(s, "dresser", "dresser", "room", pos=[2, 0])                 # 50 kg, 0.5 m deep
        drawers = [(6.0, 0.17, 0.3), (6.0, 0.17, 0.5), (6.0, 0.17, 0.7)]   # 4 kg drawer + 2 kg clothes, 0.17 m out

        def open_drawers(state, furniture_id):
            return drawers if furniture_id == "dresser" else []
        with hooked("phys.tip_loads", open_drawers):
            righting = (50.0 - 18.0) * statics.G * 0.25
            loads = 18.0 * statics.G * 0.17
            self.assertAlmostEqual(statics.tip_margin_nm(s, "dresser"), righting - loads)
            # ASTM F2057-23: 60 lb (27 kg) hanging on an open drawer front 0.4 m out tips an unanchored dresser
            pulled = statics.tip_margin_nm(s, "dresser", extra=[(27.0, 0.4)])
            self.assertAlmostEqual(pulled, righting - loads - 27.0 * statics.G * 0.4)
            self.assertLess(pulled, 0)
            self.assertAlmostEqual(statics.tip_margin_nm(s, "dresser", pull_n=150.0, pull_h_m=0.9),
                                   righting - loads - 135.0)
            s["entities"]["dresser"]["anchored"] = True
            self.assertEqual(statics.tip_margin_nm(s, "dresser", extra=[(27.0, 0.4)]), statics.INF)

    def test_overload_limits_sagging_baseline_and_staging(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "load", "book", "shelf", relation="in", shelf=2, mass_kg=24.0)
        status = statics.overload(s, "shelf", ("shelf", 2))
        self.assertEqual((status["rating"], status["limit"], status["fails"], status["sagging"]), (25.0, 25.0, False, True))
        s["entities"]["load"]["mass_kg"] = 26.0
        self.assertTrue(statics.overload(s, "shelf", ("shelf", 2))["fails"])
        env = SimpleNamespace(_baseline=clone(s))                          # the scene put 26 kg there itself
        status = statics.overload(s, "shelf", ("shelf", 2), env)
        self.assertEqual((status["limit"], status["fails"]), (26.5, False))
        s["entities"]["shelf"]["_phys_staged"] = True                      # staged: the true rating, no allowance
        self.assertTrue(statics.overload(s, "shelf", ("shelf", 2), env)["fails"])
        s["entities"]["shelf"]["phys_creep_s"] = 700.0                     # ... except while its creep is pending
        self.assertTrue(statics.creep_pending(s, "shelf"))
        self.assertFalse(statics.overload(s, "shelf", ("shelf", 2), env)["fails"])
        s["time_s"] = 700.0
        self.assertTrue(statics.overload(s, "shelf", ("shelf", 2), env)["fails"])
        self.assertIsNone(statics.overload(s, "shelf", ("floor", (0, 0))))

    def test_handle_status_and_wet_cardboard(self):
        s = house()
        put(s, "bag", "grocery_bag", "room", handle_rating_kg=7.0, mass_kg=0.15)
        put(s, "cans", "book", "bag", relation="in", mass_kg=6.5)
        self.assertIsNone(statics.handle_status(s, "bag"))                 # resting: the handles carry nothing
        hold(s, "bag")
        status = statics.handle_status(s, "bag")
        self.assertAlmostEqual(status["contents_kg"], 6.5)
        self.assertAlmostEqual(status["ratio"], 6.5 / 7.0)
        put(s, "box", "cardboard_box", "room", handle_rating_kg=15.0, wetness=0.6)
        hold(s, "box", ("left",))
        self.assertAlmostEqual(statics.handle_status(s, "box")["limit"], 6.0)      # wet board keeps 40 %

    def test_padding_and_crush_limits(self):
        s = house()
        put(s, "glass", "drinking_glass", "room", pos=[1, 1])
        self.assertEqual(statics.crush_limit_kg(s, "glass"), 15.0)
        s["entities"]["glass"]["wrapped"] = {"coverage": 1.0, "padding_mm": 10.0}
        self.assertEqual(statics.crush_limit_kg(s, "glass"), 30.0)          # x (1 + min(1.5, 10 / 10))
        self.assertEqual(statics.padding_mm(s["entities"]["glass"]), 10.0)
        self.assertAlmostEqual(statics.padding_factor(s["entities"]["glass"]), 1.0 + 10.0 / 3.0)
        s["entities"]["glass"]["hollow"] = True                              # an unstuffed hollow halves the padding
        self.assertEqual(statics.padding_mm(s["entities"]["glass"]), 5.0)
        put(s, "plate", "dinner_plate", "room", pos=[2, 2], pack_orientation="on_edge")
        self.assertEqual(statics.crush_limit_kg(s, "plate"), 30.0)           # ceramic 10 kg x3 on edge
        s["entities"]["plate"]["in_divider"] = True
        self.assertEqual(statics.padding_mm(s["entities"]["plate"]), 6.0)
        put(s, "box", "cardboard_box", "room", pos=[3, 3])
        self.assertEqual(statics.crush_limit_kg(s, "box"), 15.0)
        s["entities"]["box"]["wetness"] = 0.7
        self.assertAlmostEqual(statics.crush_limit_kg(s, "box"), 6.0)
        put(s, "towel", "bath_towel", "room", pos=[4, 4])
        self.assertIsNone(statics.crush_limit_kg(s, "towel"))


# ----- consequences ------------------------------------------------------------------------------------------------------

class FallAndBreakTests(PhysCase):
    def tumbler(self, floor="tile", **props):
        s = house(floor=floor)
        put(s, "counter", "counter", "room", pos=[0, 0], surface_height_m=0.9)
        put(s, "glass", "drinking_glass", "counter", relation="on", spos=[0.2, 0.2], **props)
        return s

    def test_tumbler_from_counter_height(self):
        # TIER1_PHYSICS 4.3 anchor and 12.3: soda-lime from 0.9 m onto tile breaks; carpet or 0.3 m survives
        s = self.tumbler()
        w = self.world(s)
        out = impact.fall(w, "glass")
        self.assertEqual((out["height_m"], out["outcome"]), (0.9, "broke"))
        rec = s["entities"]["glass"]
        self.assertTrue(rec["broken"] and rec["shattered"] and rec["fell"])
        self.assertEqual((rec["condition"], rec["floor_hazard"], rec["location"]), ("broken", "sharp", "room"))
        self.assertIn("sharp", rec["hazards"])
        self.assertEqual(rec["handled_with"], ["cut_resistant_gloves", "rubber_gloves"])
        self.assertEqual(rec["damage"], {"broken": 1.0})
        self.assertEqual(kinds(s), ["phys.break", "phys.fall"])
        self.assertEqual((s["stats"]["phys_falls"], s["stats"]["phys_breaks"]), (1, 1))
        self.assertNotIn("breakages", s["stats"])                             # nobody put it there: exogenous
        carpet = self.tumbler(floor="carpet")
        self.assertIsNone(impact.fall(self.world(carpet), "glass")["outcome"])  # 0.9 x 0.35 = 0.32 m < 0.5 m
        low = self.tumbler()
        self.assertIsNone(impact.fall(self.world(low), "glass", height_m=0.3)["outcome"])
        padded = self.tumbler(wrapped={"coverage": 1.0, "padding_mm": 6.0})
        out = impact.fall(self.world(padded), "glass")
        self.assertAlmostEqual(out["h_eff_m"], 0.3)                            # 0.9 / (1 + 6 / 3)
        self.assertIsNone(out["outcome"])

    def test_break_modes_follow_the_material(self):
        s = house()
        put(s, "egg", "book", "room", pos=[1, 1], material="eggshell", size_m=[0.045, 0.045, 0.06])
        put(s, "phone", "smartphone", "room", pos=[1, 2], material="pcb")
        put(s, "slab", "book", "room", pos=[1, 3], material="granite", mass_kg=2.0)
        put(s, "orange", "orange", "room", pos=[1, 4])
        put(s, "pan", "book", "room", pos=[1, 5], material="cast_iron", mass_kg=2.5)
        put(s, "jar", "drinking_glass", "room", pos=[1, 6], wrapped={"coverage": 1.0, "padding_mm": 0.0,
                                                                       "material": "newspaper"})
        w = self.world(s)
        self.assertEqual(impact._impact(w, "egg", 0.1, "test", "exogenous"), "broke")
        egg = s["entities"]["egg"]
        self.assertTrue(egg["shattered"])                                      # visibly broken, but not sharp
        self.assertNotIn("sharp", egg.get("hazards") or [])
        self.assertEqual(impact._impact(w, "phone", 1.5, "test", "exogenous"), "broke")
        self.assertNotIn("shattered", s["entities"]["phone"])                 # a dead device looks intact
        self.assertIsNone(impact._impact(w, "phone", 0.9, "test", "exogenous"))
        self.assertEqual(impact._impact(w, "slab", 2.0, "test", "exogenous"), "chipped")
        self.assertFalse(s["entities"]["slab"]["broken"])
        self.assertAlmostEqual(s["entities"]["slab"]["damage"]["chipped"], 2.0 / 1.5 - 1.0, places=4)
        self.assertEqual(impact._impact(w, "orange", 1.5, "test", "exogenous"), "bruised")
        self.assertIsNone(impact._impact(w, "pan", 5.0, "test", "exogenous"))     # cast iron never breaks
        self.assertEqual(impact._impact(w, "jar", 1.0, "test", "exogenous"), "broke")
        self.assertNotIn("shattered", s["entities"]["jar"])                    # an opaque wrap hides the pieces
        self.assertIn("sharp", s["entities"]["jar"]["hazards"])
        self.assertTrue(impact.impact(w, "egg", 1.0, "again") is False)       # already broken

    def test_break_entity_is_idempotent_releases_liquid_and_attributes(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "jug", "drinking_glass", "table", relation="on", liquid="milk", liquid_l=0.3)
        seen = []

        def broke(w, eid, info):
            seen.append((eid, info["cause"], info["by"], info["mode"]))
        w = self.world(s)
        with hooked("phys.broke", broke):
            self.assertTrue(impact.break_entity(w, "jug", "dropped", by="robot"))
            self.assertFalse(impact.break_entity(w, "jug", "dropped", by="robot"))
        self.assertEqual(seen, [("jug", "dropped", "robot", "shatter")])
        self.assertEqual(self.liquids.released, [("jug", 0.3, "milk", "robot")])
        self.assertEqual(s["entities"]["jug"]["liquid_l"], 0.0)
        self.assertNotIn("floor_hazard", s["entities"]["jug"])                # still on the table
        self.assertEqual((s["stats"]["breakages"], s["stats"]["damage"], s["stats"]["phys_breaks"]), (1, 1, 1))
        self.assertFalse(impact.break_entity(w, "ghost", "x"))

    def test_landing_spot_toward_the_overhang_and_in_front_of_a_wall(self):
        s = house()
        put(s, "table", "table", "room", pos=[3, 0])                 # 1.2 x 0.8 m against the y = 0 wall: cells x 3-5, y 0-1
        put(s, "left", "mug", "table", relation="on", spos=[-0.03, 0.55])       # sticks out of the -x end, row 1
        put(s, "right", "mug", "table", relation="on", spos=[1.12, 0.05])       # sticks out of the +x end, row 0
        put(s, "plain", "mug", "table", relation="on", spos=[0.54, 0.35])
        point = statics.room_point(s, "left")
        self.assertAlmostEqual(point[0], 1.5 - 0.03 + 0.06)
        self.assertAlmostEqual(point[1], 0.55 + 0.045)
        self.assertEqual(impact.landing_cell(s, "left", "room", prefer=statics.overhang_dir_deg(s, "left")), [2, 1])
        self.assertEqual(impact.landing_cell(s, "right", "room", prefer=statics.overhang_dir_deg(s, "right")), [6, 0])
        self.assertEqual(impact.landing_cell(s, "plain", "room"), [4, 2])       # in front of its middle
        put(s, "chair", "chair", "room", pos=[4, 2], blocks_path=True)           # furniture takes cells
        self.assertEqual(impact.landing_cell(s, "plain", "room"), [3, 2])
        self.assertEqual(impact.landing_cell(s, "left", "room", prefer=180.0), [2, 1])
        put(s, "cabinet", "cabinet", "room", pos=[1, 1], blocks_path=True)        # 2 x 2 cells beside the table
        self.assertEqual(impact.landing_cell(s, "left", "room", prefer=180.0), [1, 0])   # the free cell nearest it
        w = self.world(s)
        out = impact.fall(w, "right")
        self.assertEqual((out["room"], out["cell"]), ("room", [6, 0]))
        out = impact.fall(w, "plain")                                              # inside the top: no direction
        self.assertEqual(out["cell"], [3, 2])

    def test_robot_drop_lands_at_its_cell_and_onto_a_bed_softly(self):
        s = house()
        put(s, "glass", "drinking_glass", "room")
        hold(s, "glass", ("right",))
        put(s, "bed", "bed", "room", pos=[0, 0])
        w = self.world(s)
        out = impact.fall(w, "glass", height_m=1.0, cause="slip", at=("room", [4, 4]), by="robot")
        self.assertEqual((out["cell"], out["outcome"], out["struck"]), ([4, 4], "broke", ["robot"]))
        self.assertIsNone(s["robot"]["hands"]["right"])
        self.assertEqual(s["entities"]["glass"]["pos"], [4, 4])
        self.assertEqual((s["stats"]["breakages"], s["logs"]["moves"][-1]["by"]), (1, "gravity"))
        soft = house()
        put(soft, "bed", "bed", "room", pos=[0, 0])
        put(soft, "shelf", "wall_shelf", "room", pos=[0, 0], mounted=True, mount_height_m=1.5)
        put(soft, "tumbler", "drinking_glass", "shelf", relation="on", spos=[0.1, 0.05])
        put(soft, "vase", "vase", "shelf", relation="on", spos=[0.4, 0.05])     # declares a 0.12 m drop tolerance
        w = self.world(soft)
        out = impact.fall(w, "tumbler", onto="bed")
        self.assertAlmostEqual(out["height_m"], 1.5 + 0.03 - 0.55)            # from the shelf top to the mattress
        # supports.json bed: a 15 cm mattress catches it (h x 1 mm / 151 mm), far below hardness 0.15's 0.147 m
        self.assertAlmostEqual(out["h_eff_m"], round(out["height_m"] * 0.001 / 0.151, 4))
        self.assertIsNone(out["outcome"])
        rec = soft["entities"]["tumbler"]
        self.assertEqual((rec["location"], rec["relation"], rec["spos"]), ("bed", "on", [0.0, 0.0]))
        self.assertNotIn("pos", rec)
        self.assertIsNone(impact.fall(w, "vase", onto="bed")["outcome"])         # a vase falling onto a bed survives
        self.assertEqual(impact.fall(w, "vase", height_m=1.0, at=("room", [5, 5]))["outcome"], "broke")   # tile

    def test_an_item_falls_once_per_step_and_struck_agents_are_reported(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 0])                    # in the corner, its long side on the y = 0 wall
        put(s, "book", "book", "table", relation="on", spos=[0.3, 0.3])
        s["people"]["ann"] = {"location": "room", "pos": [1, 2]}         # right in front of its middle
        seen = []
        w = self.world(s)
        with hooked("phys.fell", lambda w_, eid, info: seen.append((eid, info["struck"], info["cell"]))):
            first = impact.fall(w, "book")
            self.assertEqual(impact.fall(w, "book"), {"fell": False})          # it is down: nowhere to fall
        self.assertEqual(first["struck"], ["ann"])
        self.assertEqual(seen, [("book", ["ann"], [1, 2])])
        again = impact.fall(w, "book", height_m=0.5)                           # someone drops it: it lands in place
        self.assertEqual((again["fell"], again["cell"]), (True, [1, 2]))
        self.assertEqual(impact.fall(w, "book", height_m=0.5), {"fell": False})   # the same drop twice in a step
        w.set_scalar("steps", 2)                                               # a new step: the ledger starts over
        self.assertTrue(impact.fall(w, "book", height_m=0.5)["fell"])
        self.assertEqual(impact.fall(w, "ghost"), {"fell": False})

    def test_soft_landings_catch_light_things_and_bottom_out_under_heavy_ones(self):
        """Review finding: h_eff = h x hardness made carpet too harsh above table height (a mug or plate from 1.5 m
        always broke, an egg broke on grass from 0.3 m). A soft landing stops a light item over its cushion
        (h x 1 mm / (1 mm + cushion_m)) until its cushion_j is used up; the rest lands at the floor's hardness."""
        def drop(floor, type_name, height, **props):
            s = house(floor=floor)
            put(s, "x", type_name, "room", pos=[2, 2], **props)
            hold(s, "x", ("right",))
            return impact.fall(self.world(s), "x", height_m=height, at=("room", [4, 4]), by="robot")
        for name in ("mug", "dinner_plate", "drinking_glass", "wine_glass"):
            self.assertIsNone(drop("carpet", name, 1.5)["outcome"], name)          # a hand-height drop on carpet
            self.assertEqual(drop("tile", name, 1.5)["outcome"], "broke", name)
        out = drop("carpet", "mug", 1.5)
        caught = 3.0 / (0.35 * 9.81)                                               # carpet cushion: 3 J
        self.assertAlmostEqual(out["h_eff_m"], round(caught * 0.001 / 0.013 + (1.5 - caught) * 0.35, 4), places=4)
        egg = dict(material="eggshell", mass_kg=0.057, size_m=[0.06, 0.045, 0.045])   # a hen's egg, core types only
        self.assertIsNone(drop("grass", "book", 1.0, **egg)["outcome"])            # a thick lawn catches an egg
        self.assertEqual(drop("carpet", "book", 1.0, **egg)["outcome"], "broke")   # carpet from counter height
        self.assertEqual(drop("tile", "book", 0.3, **egg)["outcome"], "broke")
        heavy = drop("carpet", "book", 1.0, material="cardboard", mass_kg=10.0)
        caught = 3.0 / (10.0 * 9.81)                                               # a 10 kg box bottoms it out
        self.assertAlmostEqual(heavy["h_eff_m"], round(caught * 0.001 / 0.013 + (1.0 - caught) * 0.35, 4), places=4)
        self.assertGreater(heavy["h_eff_m"], 0.33)
        for floor in ("tile", "hardwood", "vinyl", "carpet", "rug", "grass", "soil"):
            land = impact.landing(house(floor=floor), "room", (1, 1))
            for mass in (0.05, 0.5, 5.0, 50.0):
                for height in (0.1, 0.75, 1.5, 3.0):
                    self.assertLessEqual(impact.effective_drop_m(height, land, mass), height * land.hardness + 1e-12)
            if floor in ("tile", "hardwood", "vinyl"):
                self.assertEqual(impact.effective_drop_m(1.0, land, 0.3), land.hardness)       # hard: linear

    def test_heavy_drops_dent_and_crack_hard_floors(self):
        s = house()
        put(s, "box", "cardboard_box", "room", mass_kg=5.0)
        hold(s, "box", ("left", "right"))
        w = self.world(s)
        impact.fall(w, "box", height_m=0.9, at=("room", [4, 4]), by="robot")
        damage = s["entities"]["floor_room"]["damage"]                          # 5 kg x 9.81 x 0.9 m = 44 J
        self.assertEqual(sorted(damage), ["cracked", "dent"])
        self.assertAlmostEqual(damage["dent"], round(5.0 * 9.81 * 0.9 / 100.0, 4))
        self.assertEqual(s["stats"]["damage"], 2)                               # each new kind of damage counts
        for floor, mass, rug in (("carpet", 5.0, False), ("tile", 2.0, False), ("tile", 5.0, True), ("hardwood", 3.5, False)):
            other = house(floor=floor)
            put(other, "box", "cardboard_box", "room", mass_kg=mass)
            if rug:
                put(other, "rug", "rug", "room", pos=[3, 3], ground_layer=True)
            impact.fall(self.world(other), "box", height_m=0.9, at=("room", [4, 4]))
            marks = sorted(other["entities"]["floor_room"].get("damage") or {})
            self.assertEqual(marks, ["dent"] if floor == "hardwood" else [], (floor, mass, rug))

    def test_falling_containers_spill_scatter_and_jolt(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "bowl", "cereal_bowl", "table", relation="on", liquid="soup", liquid_l=0.5, capacity_l=0.7)
        put(s, "basket", "laundry_basket", "table", relation="on", spos=[0.6, 0.2])
        put(s, "apple", "apple", "basket", relation="in")
        put(s, "box", "cardboard_box", "table", relation="on", spos=[0.2, 0.5], openable=True, open=False)
        put(s, "plate", "dinner_plate", "box", relation="in")
        w = self.world(s)
        impact.fall(w, "bowl")
        self.assertEqual(self.liquids.released, [("bowl", 0.5, "soup", "exogenous")])
        self.assertEqual(s["entities"]["bowl"]["liquid_l"], 0.0)
        impact.fall(w, "basket")                                               # open: its contents scatter
        self.assertEqual(s["entities"]["apple"]["location"], "room")
        self.assertTrue(s["entities"]["apple"]["fell"])
        impact.fall(w, "box")                                                  # closed: the plate rides the impact
        self.assertEqual(s["entities"]["plate"]["location"], "box")
        self.assertTrue(s["entities"]["plate"]["broken"])                     # 0.75 m on tile > 0.45 m ceramic

    def test_tall_things_land_on_their_side(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "bottle", "wine_bottle", "table", relation="on", sealed=True)
        put(s, "book", "book", "table", relation="on", spos=[0.5, 0.5])
        w = self.world(s)
        impact.fall(w, "bottle")
        impact.fall(w, "book")
        self.assertEqual(s["entities"]["bottle"]["orientation"], "on_side")
        self.assertNotIn("orientation", s["entities"]["book"])                # a flat book lands flat


class CrushCollapseTearTests(PhysCase):
    def test_crush_brittle_breaks_soft_crushes_and_boxes_pass_their_load(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "glass", "drinking_glass", "table", relation="on", spos=[0.1, 0.1])
        put(s, "pot", "book", "glass", relation="on", mass_kg=16.0)                     # 16 kg > 15 kg glass
        put(s, "box", "cardboard_box", "table", relation="on", spos=[0.6, 0.1], wetness=0.7)
        put(s, "tin", "book", "box", relation="on", mass_kg=10.0)                       # wet board: 6 kg
        put(s, "bread", "book", "box", relation="in", material="organic", mass_kg=0.5,   # crushes at 5 kg (food's
            crushable=True)                                                              # packaging row marks it)
        w = self.world(s)
        impact.crush_law(w, "glass")
        self.assertTrue(s["entities"]["glass"]["broken"])
        records = impact.crush_law(w, "box")
        self.assertAlmostEqual(s["entities"]["box"]["damage"]["crushed"], 10.0 / 6.0 - 1.0, places=4)
        self.assertGreater(s["entities"]["bread"]["damage"]["crushed"], 0.0)            # it bore the 10 kg next
        self.assertEqual([r["kind"] for r in records], ["phys.crush", "phys.crush"])
        self.assertEqual(impact.crush(w, "box", 50.0), [])                              # once per step
        self.assertEqual(impact.crush_law(w, "table"), [])

    def test_shelf_rated_25_kg_holds_24_collapses_at_26_and_cascades(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "low", "book", "shelf", relation="in", shelf=3, mass_kg=12.0)
        put(s, "top", "book", "shelf", relation="in", shelf=4, mass_kg=12.0)
        put(s, "more", "book", "shelf", relation="in", shelf=4, mass_kg=12.0, _robot_moved=True)
        w = self.world(s)
        self.assertEqual(impact.overload_law(w, "shelf"), [])                         # 24 kg: holds, sags
        self.assertTrue(s["entities"]["shelf"]["sagging"] and s["entities"]["shelf"]["_phys_sag"])
        w.mut("more")["mass_kg"] = 14.0                                                  # 26 kg, the robot's doing
        records = impact.overload_law(w, "shelf")
        shelf = s["entities"]["shelf"]
        # shelf 4 gives way onto shelf 3 (now 38 kg), which gives way onto shelf 2, and so on down to the floor
        self.assertEqual(shelf["collapsed_shelves"], [0, 1, 2, 3, 4])
        self.assertEqual({s["entities"][b]["location"] for b in ("low", "top", "more")}, {"room"})
        self.assertEqual(shelf["damage"]["collapsed"], 1.0)
        self.assertEqual([r["kind"] for r in records if r["kind"] == "phys.collapse"], ["phys.collapse"] * 5)
        self.assertEqual(s["stats"]["damage"], 1)                                        # the robot's: it added load
        self.assertNotIn("sagging", shelf)
        self.assertEqual(s["stats"]["phys_collapses"], 5)

    def test_a_collapsing_top_shelf_drops_its_items_onto_the_shelf_below(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])                 # shelves at 0.05 / 0.45 / 0.85 / 1.25 / 1.65 m
        put(s, "glass", "drinking_glass", "shelf", relation="in", shelf=4)
        put(s, "vase", "vase", "shelf", relation="in", shelf=4)              # its own 0.12 m drop tolerance
        put(s, "cup", "mug", "shelf", relation="in", shelf=3)
        w = self.world(s)
        moved = impact.collapse(w, "shelf", ("shelf", 4))
        self.assertEqual(moved, ["glass", "vase"])
        for name in ("glass", "vase"):
            rec = s["entities"][name]
            self.assertEqual((rec["location"], rec["shelf"], rec["relation"], rec["fell"]), ("shelf", 3, "in", True))
        self.assertFalse(s["entities"]["glass"].get("broken"))           # 0.4 m gap x wood 0.8 = 0.32 m < 0.5 m
        self.assertTrue(s["entities"]["vase"]["broken"])
        falls = [e for e in s["event_log"] if e["kind"] == "phys.fall"]
        self.assertEqual({(e["target"], e["landed_on"], e["height_m"]) for e in falls},
                         {("glass", "shelf", 0.4), ("vase", "shelf", 0.4)})
        self.assertEqual(impact.collapse(w, "shelf", ("shelf", 4)), [])                  # idempotent
        impact.collapse(w, "shelf", ("shelf", 3))                                         # the cascade goes on down
        self.assertEqual(s["entities"]["cup"]["shelf"], 2)
        self.assertEqual(s["entities"]["glass"]["shelf"], 2)                             # a second fall, from shelf 3

    def test_setup_overloaded_shelf_holds_its_baseline(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        put(s, "books", "book", "shelf", relation="in", shelf=2, mass_kg=30.0)
        env = SimpleNamespace(_baseline=clone(s))
        w = self.world(s)
        self.assertEqual(impact.overload_law(w, "shelf", env=env), [])
        self.assertTrue(s["entities"]["shelf"]["sagging"])                              # it visibly holds too much
        add(w, "one_more", "book", "shelf", relation="in", shelf=2, mass_kg=0.6)
        self.assertTrue(impact.overload_law(w, "shelf", env=env))                      # 30.6 > 30 + 0.5

    def test_staged_creep_collapses_on_time_unless_unloaded(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0], _phys_staged=True, phys_creep_s=700.0)
        put(s, "books", "book", "shelf", relation="in", shelf=2, mass_kg=27.0)
        env = SimpleNamespace(_baseline=clone(s))
        w = self.world(s)
        impact.schedule_creep(w, "shelf", ("shelf", 2), 700.0)
        self.assertEqual(self.processes.queue, [(700.0, "creep", "shelf", {"slot": ["shelf", 2]})])
        self.assertEqual(impact.overload_law(w, "shelf", env=env), [])                  # holds until its creep time
        w.set_scalar("time_s", 700.0)
        fired = impact.PROCESS_HANDLERS["creep"](w, "shelf", {"slot": ["shelf", 2]})
        self.assertEqual([r["kind"] for r in fired][-1], "phys.collapse")
        self.assertEqual(s["entities"]["shelf"]["collapsed_shelves"], [2])
        self.assertEqual((s["entities"]["books"]["location"], s["entities"]["books"]["shelf"]), ("shelf", 1))
        unloaded = house()
        put(unloaded, "shelf", "bookshelf", "room", pos=[2, 0], _phys_staged=True, phys_creep_s=700.0)
        put(unloaded, "books", "book", "shelf", relation="in", shelf=2, mass_kg=20.0)    # unloaded in time
        self.assertEqual(impact.creep_fire(self.world(unloaded), "shelf", {"slot": ["shelf", 2]}), [])
        self.assertNotIn("collapsed_shelves", unloaded["entities"]["shelf"])

    def test_surface_failures(self):
        s = house()
        put(s, "glass_table", "table", "room", pos=[0, 4], material="glass", max_load_kg=20.0)
        put(s, "anvil", "book", "glass_table", relation="on", mass_kg=21.0)
        put(s, "side", "side_table", "room", pos=[5, 5])
        put(s, "sack", "book", "side", relation="on", mass_kg=22.0)
        put(s, "wall_a", "wall_shelf", "room", pos=[2, 0], mounted=True, mount_height_m=1.2)
        put(s, "books_a", "book", "wall_a", relation="on", mass_kg=13.0)
        put(s, "wall", "wall_surface", "room", pos=[6, 0])
        put(s, "wall_b", "wall_shelf", "wall", relation="attached")
        put(s, "books_b", "book", "wall_b", relation="on", mass_kg=13.0)
        w = self.world(s)
        impact.overload_law(w, "glass_table")
        self.assertTrue(s["entities"]["glass_table"]["broken"])                         # a glass top breaks
        self.assertEqual(s["entities"]["anvil"]["location"], "room")
        impact.overload_law(w, "side")
        self.assertEqual(s["entities"]["side"]["damage"]["collapsed"], 1.0)
        self.assertEqual(s["entities"]["sack"]["location"], "room")
        self.assertEqual(impact.collapse(w, "side", ("surface",)), [])                  # idempotent
        impact.overload_law(w, "wall_a")
        self.assertEqual(s["entities"]["wall_a"]["location"], "room")                   # tore out of the wall
        self.assertEqual(s["entities"]["wall_a"]["damage"]["pulled_out"], 1.0)
        impact.overload_law(w, "wall_b")
        self.assertEqual(s["entities"]["wall"]["damage"]["hole"], 0.3)
        self.assertEqual(s["entities"]["books_b"]["location"], "room")
        self.assertIn("phys.pullout", kinds(s))

    def test_what_falls_or_tears_out_rests_where_it_lands(self):
        """Review finding: a fall kept z_m, and a pulled-out wall shelf kept mounted / mount_height_m, so
        geometry.height_m, support_of and sight still put them at their old height or on the wall."""
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "jar", "drinking_glass", "table", relation="on", spos=[0.1, 0.1], z_m=0.9)
        put(s, "shelf", "wall_shelf", "room", pos=[2, 0], mounted=True, mount_height_m=1.6, anchored=True)
        put(s, "books", "book", "shelf", relation="on", mass_kg=16.0, _robot_moved=True)    # 16 kg on a 12 kg shelf
        put(s, "hook", "adhesive_hook_small", "room", pos=[6, 0], mounted=True, mount_height_m=1.4)
        put(s, "keys", "book", "hook", relation="hung", mass_kg=1.0, size_m=[0.08, 0.03, 0.1])
        w = self.world(s)
        self.assertEqual(geometry.height_m(s, "jar"), 0.9)
        self.assertTrue(impact.fall(w, "jar")["fell"])
        self.assertNotIn("z_m", s["entities"]["jar"])
        self.assertEqual(geometry.height_m(s, "jar"), 0.0)
        self.assertEqual(support.support_of(s, "jar"), ("room", ("floor", tuple(s["entities"]["jar"]["pos"]))))
        impact.overload_law(w, "shelf")
        shelf = s["entities"]["shelf"]
        self.assertEqual((shelf["location"], shelf["fell"], shelf["damage"]["pulled_out"]), ("room", True, 1.0))
        for key in ("mounted", "mount_height_m", "anchored", "z_m"):
            self.assertNotIn(key, shelf)
        self.assertEqual(geometry.height_m(s, "shelf"), 0.0)
        self.assertEqual(support.support_of(s, "shelf"), ("room", ("floor", tuple(shelf["pos"]))))
        fell = [e for e in s["event_log"] if e["kind"] == "phys.fall" and e["target"] == "shelf"]
        self.assertEqual(fell[0]["height_m"], 1.6)                                 # it fell from the wall
        impact.hang_law(w, "hook")                                                  # 1 kg on a 0.45 kg adhesive hook
        hook = s["entities"]["hook"]
        self.assertEqual((hook["location"], hook["fell"]), ("room", True))
        self.assertNotIn("mount_height_m", hook)
        self.assertEqual(geometry.height_m(s, "hook"), 0.0)
        self.assertEqual(support.support_of(s, "hook")[1][0], "floor")

    def test_books_and_bins_never_crush_and_a_plate_stack_spreads_its_load(self):
        """Review finding: the class crush_kg applied to every item, so ten 0.5 kg books (4.5 kg on the bottom one,
        paper 5 kg x vary) crushed it, and 20 dinner plates broke the bottom one. TIER1_PHYSICS 5.3 keeps
        manipulation's max_load rule: only crushable items have a crush limit, and like stacks spread their load."""
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "book_0", "book", "table", relation="on", spos=[0.1, 0.1], mass_kg=0.5, phys_exact=False)
        for i in range(1, 20):
            put(s, "book_%d" % i, "book", "book_%d" % (i - 1), relation="on", mass_kg=0.5, phys_exact=False)
        put(s, "plate_0", "dinner_plate", "table", relation="on", spos=[0.6, 0.1])
        for i in range(1, 20):
            put(s, "plate_%d" % i, "dinner_plate", "plate_%d" % (i - 1), relation="on")
        put(s, "saucer", "dinner_plate", "table", relation="on", spos=[0.6, 0.5])
        put(s, "pot", "book", "saucer", relation="on", mass_kg=12.0, size_m=[0.3, 0.3, 0.15])   # a heavy pot
        self.assertIsNone(statics.crush_limit_kg(s, "book_0"))                        # paper books: no crush limit
        self.assertEqual(statics.crush_limit_kg(s, "plate_0"), 50.0)                  # ceramic 10 kg x5 under plates
        self.assertEqual(statics.crush_limit_kg(s, "saucer"), 10.0)                   # a point load: 10 kg
        put(s, "platter", "dinner_plate", "table", relation="on", spos=[0.1, 0.5])
        put(s, "big", "dinner_plate", "platter", relation="on", size_m=[0.4, 0.4, 0.025])   # wider than it
        self.assertEqual(statics.crush_limit_kg(s, "platter"), 10.0)                  # not a stack of its kind
        put(s, "crate", "generic_item", "table", relation="on", spos=[0.9, 0.5], material="cardboard",
            size_m=[0.4, 0.3, 0.3])
        put(s, "anvil", "generic_item", "crate", relation="on", mass_kg=20.0, size_m=[0.2, 0.2, 0.2])
        self.assertEqual(statics.crush_limit_kg(s, "crate"), 15.0)                    # generic_item is no kind
        w = self.world(s)
        self.assertEqual(impact.crush_law(w, "book_0"), [])
        self.assertEqual(impact.crush_law(w, "plate_0"), [])                          # 11.4 kg of plates on it
        self.assertFalse(s["entities"]["plate_0"].get("broken"))
        self.assertEqual([r["kind"] for r in impact.crush_law(w, "saucer")], ["phys.break"])   # 12 kg pot: breaks
        add(w, "bin", "book", "room", pos=[6, 6], material="plastic", mass_kg=0.8)
        self.assertIsNone(statics.crush_limit_kg(s, "bin"))                           # a plastic bin: never crushed
        w.mut("bin")["crushable"] = True                                              # unless it says it is
        self.assertEqual(statics.crush_limit_kg(s, "bin"), 30.0)

    def test_hangers_pull_out_detach_or_bend(self):
        s = house()
        put(s, "hook", "adhesive_hook_small", "room", pos=[1, 0], mounted=True, mount_height_m=1.5)
        put(s, "coat", "raincoat", "hook", relation="hung", mass_kg=0.6)                 # 0.6 > 0.45 kg
        put(s, "screw", "adhesive_hook_small", "room", pos=[3, 0], mounted=True, mount_height_m=1.5,
            adhesive=None, hardware_kind="hook", anchor="drywall_anchor", hook_rating_kg=7.0)
        put(s, "bike", "book", "screw", relation="hung", mass_kg=9.0)
        put(s, "bar", "towel_bar", "room", pos=[5, 0], mounted=True, mount_height_m=1.1)
        put(s, "towels", "bath_towel", "bar", relation="hung", mass_kg=9.0)
        put(s, "rack", "coat_hooks", "room", pos=[7, 0], mounted=True, mount_height_m=1.6)
        put(s, "hat", "raincoat", "rack", relation="hung", mass_kg=0.4)
        w = self.world(s)
        impact.hang_law(w, "hook")
        self.assertEqual((s["entities"]["coat"]["location"], s["entities"]["hook"]["location"]), ("room", "room"))
        self.assertTrue(s["entities"]["hook"]["fell"])
        impact.hang_law(w, "screw")
        self.assertEqual(s["entities"]["screw"]["damage"]["pulled_out"], 1.0)
        self.assertEqual(s["entities"]["bike"]["location"], "room")
        impact.hang_law(w, "bar")
        self.assertEqual(s["entities"]["bar"]["damage"]["collapsed"], 1.0)
        self.assertEqual(s["entities"]["bar"]["location"], "room")                      # bent, still on the wall
        self.assertNotIn("fell", s["entities"]["bar"])
        self.assertEqual(impact.hang_law(w, "rack"), [])
        self.assertEqual(s["entities"]["hat"]["location"], "rack")

    def test_handle_law_tears_overloaded_bags_and_queues_creep(self):
        s = house()
        put(s, "bag", "grocery_bag", "room", handle_rating_kg=7.0, _robot_moved=True)      # the robot picked it up
        put(s, "cans", "book", "bag", relation="in", mass_kg=8.0)
        put(s, "milk", "drinking_glass", "bag", relation="in", mass_kg=0.2)
        hold(s, "bag")
        w = self.world(s)
        records = impact.handle_law(w, "bag")
        self.assertTrue(s["entities"]["bag"]["torn"])
        self.assertEqual(s["entities"]["bag"]["damage"]["torn"], 1.0)
        self.assertEqual(s["entities"]["bag"]["location"], "robot")                      # it stays in the hand
        for item in ("cans", "milk"):
            self.assertEqual((s["entities"][item]["location"], s["entities"][item]["pos"]), ("room", [4, 4]))
        self.assertTrue(s["entities"]["milk"]["broken"])                                 # 1.0 m onto tile
        self.assertEqual(s["stats"]["breakages"], 1)                                      # the robot's doing
        self.assertEqual({r["kind"] for r in records}, {"phys.fall", "phys.break", "phys.dent", "phys.tear"})
        self.assertEqual(records[-1]["kind"], "phys.tear")
        self.assertEqual(impact.tear(w, "bag"), [])                                      # once
        creeping = house()
        put(creeping, "bag", "grocery_bag", "room", handle_rating_kg=7.0)
        put(creeping, "cans", "book", "bag", relation="in", mass_kg=6.5)                  # 93 % of the rating
        hold(creeping, "bag")
        w2 = self.world(creeping)
        self.assertEqual(impact.handle_law(w2, "bag"), [])
        impact.handle_law(w2, "bag")
        self.assertEqual(self.processes.queue, [(220.0, "handle_creep", "bag", {})])       # once, 120 s later
        w2.set_scalar("time_s", 220.0)
        self.assertEqual(impact.handle_creep_fire(w2, "bag", {})[-1]["kind"], "phys.tear")
        relieved = house()
        put(relieved, "bag", "grocery_bag", "room", handle_rating_kg=7.0)
        put(relieved, "cans", "book", "bag", relation="in", mass_kg=3.0)
        hold(relieved, "bag")
        self.assertEqual(impact.handle_creep_fire(self.world(relieved), "bag", {}), [])

    def test_orphans_fall_from_where_their_support_was(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "cake", "book", "table", relation="on", material="organic")
        put(s, "candle", "book", "cake", relation="on", mass_kg=0.05)
        w = self.world(s)
        w.destroy("cake")                                                                 # eaten, candle left behind
        records = impact.orphan_law(w, "candle")
        candle = s["entities"]["candle"]
        self.assertEqual((candle["location"], candle["relation"]), ("room", "on"))
        self.assertEqual(records[0]["kind"], "phys.fall")
        fall = [e for e in s["event_log"] if e["kind"] == "phys.fall"][0]
        self.assertAlmostEqual(fall["height_m"], 0.75 + 0.03)                             # the table top + the cake
        self.assertEqual(impact.orphan_law(w, "table"), [])


class TipKnockSetdownTests(PhysCase):
    def test_a_tipped_item_lies_toward_the_push_and_falls_when_it_sticks_out(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "wine", "wine_glass", "table", relation="on", spos=[1.12, 0.3], size_m=[0.07, 0.07, 0.1833],
            com_frac=0.6, liquid="wine", liquid_l=0.1, capacity_l=0.35, friction_mu=0.6)
        self.assertAlmostEqual(statics.tip_accel_g(s, "wine"), 0.4256, places=4)       # the wine lowers its COM
        w = self.world(s)
        self.assertFalse(impact.knock(w, "wine", 0.35, direction=0.0)["tipped"])
        out = impact.knock(w, "wine", 0.45, direction=0.0, cause="pet")                 # toward the near (+x) edge
        self.assertTrue(out["tipped"] and out["fell"] and out["broke"])
        self.assertEqual(self.liquids.released[0], ("wine", 0.1, "wine", "exogenous"))
        self.assertEqual(s["entities"]["wine"]["location"], "room")
        inside = house()
        put(inside, "table", "table", "room", pos=[0, 4])
        put(inside, "wine", "wine_glass", "table", relation="on", spos=[0.5, 0.3], size_m=[0.07, 0.07, 0.1833],
            com_frac=0.6)
        put(inside, "coaster", "book", "wine", relation="on", mass_kg=0.02, size_m=[0.06, 0.06, 0.005])
        w2 = self.world(inside)
        out = impact.tip_over(w2, "wine", "bump", direction=180.0)
        self.assertEqual((out["tipped"], out["fell"], out["broke"]), (True, False, False))
        self.assertEqual(inside["entities"]["wine"]["spos"], [round(0.57 - 0.1833, 4), 0.3])   # extends toward -x
        coaster = inside["entities"]["coaster"]
        self.assertEqual((coaster["location"], coaster["orientation"]), ("table", "on_side"))
        self.assertEqual(impact.tip_over(w2, "wine", "again"), {"tipped": False, "fell": False, "broke": False,
                                                                "struck": [], "direction": None})

    def test_furniture_tips_forward_throwing_its_contents_at_the_people_in_front(self):
        s = house()
        put(s, "case", "bookshelf", "room", pos=[2, 0])                                   # against the y = 0 wall
        put(s, "book", "book", "case", relation="in", shelf=3)
        put(s, "clock", "book", "case", relation="on")
        s["people"]["kid"] = {"location": "room", "pos": [2, 3]}
        put(s, "cabinet", "cabinet", "room", pos=[6, 0], openable=True, open=False)
        put(s, "jar", "drinking_glass", "cabinet", relation="in", shelf=1)
        w = self.world(s)
        out = impact.tip_over(w, "case", "pulled")
        self.assertEqual((out["direction"], out["struck"]), (90.0, ["kid"]))
        self.assertEqual(s["entities"]["book"]["location"], "room")
        self.assertGreater(s["entities"]["book"]["pos"][1], 0)                            # in front of the case
        self.assertEqual(s["entities"]["clock"]["location"], "room")
        impact.tip_over(w, "cabinet", "pulled")
        self.assertEqual(s["entities"]["jar"]["location"], "cabinet")                     # closed doors hold it
        self.assertEqual(s["stats"]["phys_tips"], 2)

    def test_knock_slides_slippery_things_off_the_edge(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "puck", "book", "table", relation="on", spos=[1.12, 0.3], size_m=[0.1, 0.1, 0.02],
            friction_mu=0.2, mass_kg=0.3)                                # centre 3 cm from the edge, slides 3.7 cm
        d = impact.slide_distance_m(0.5, 0.2)
        self.assertAlmostEqual(d, 0.5 * 0.3 * 9.81 * 0.01 + (0.3 * 9.81 * 0.1) ** 2 / (2 * 0.2 * 9.81))
        self.assertEqual(impact.slide_distance_m(0.1, 0.2), 0.0)
        w = self.world(s)
        out = impact.knock(w, "puck", 0.5, direction=0.0)
        self.assertAlmostEqual(out["slid_m"], round(d, 4))
        self.assertTrue(out["fell"])
        self.assertEqual(kinds(s)[:2], ["phys.slide", "phys.fall"])
        floor_item = house()
        put(floor_item, "puck", "book", "room", pos=[1, 1], size_m=[0.1, 0.1, 0.02], friction_mu=0.2)
        self.assertEqual(impact.knock(self.world(floor_item), "puck", 0.5)["slid_m"], 0.0)

    def test_setdown_egg_on_stone(self):
        # TIER1_PHYSICS 5.5: an egg (0.03 m) survives a normal set-down on stone and breaks on a fast one
        def egg_on_stone(speed, **props):
            s = house()
            put(s, "counter", "counter", "room", pos=[0, 0])                               # stone top, hardness 1.0
            put(s, "egg", "book", "counter", relation="on", material="eggshell", size_m=[0.045, 0.045, 0.06], **props)
            return impact.setdown(self.world(s), "egg", speed=speed)
        self.assertEqual(egg_on_stone("normal"), {"h_eff_m": 0.02, "outcome": None})
        self.assertEqual(egg_on_stone("fast")["outcome"], "broke")
        self.assertEqual(egg_on_stone("careful"), {"h_eff_m": 0.0, "outcome": None})
        self.assertIsNone(egg_on_stone("fast", wrapped={"coverage": 1.0, "padding_mm": 3.0})["outcome"])

    def test_setdown_drops_at_the_effective_speed(self):
        """TIER1_INTEGRATION 1.7: the set-down drop consumes body.effective_speed (quiet: fast becomes normal; limp
        or weak legs: careful), with the robot's speed mode when no speed is given."""
        def egg_on_stone(speed=None, **robot):
            s = house()
            s["robot"].update(robot)
            put(s, "counter", "counter", "room", pos=[0, 0])
            put(s, "egg", "book", "counter", relation="on", material="eggshell", size_m=[0.045, 0.045, 0.06])
            return impact.setdown(self.world(s), "egg", speed=speed)
        self.assertEqual(egg_on_stone(speed_mode="fast")["outcome"], "broke")             # the robot's own mode
        self.assertEqual(egg_on_stone(speed_mode="fast", quiet=True), {"h_eff_m": 0.02, "outcome": None})
        self.assertEqual(egg_on_stone("fast", quiet=True), {"h_eff_m": 0.02, "outcome": None})
        self.assertEqual(egg_on_stone("fast", legs_health=0.5), {"h_eff_m": 0.0, "outcome": None})   # limp: careful
        self.assertEqual(impact.effective_speed(house(), "sideways"), "sideways")          # unknown: as asked


class LawTests(PhysCase):
    def test_edge_law(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "plate", "dinner_plate", "table", relation="on", spos=[1.1, 0.3])
        put(s, "clamped", "dinner_plate", "table", relation="on", spos=[-0.2, 0.3], secured=True)
        put(s, "fine", "dinner_plate", "table", relation="on", spos=[0.5, 0.3])
        w = self.world(s)
        self.assertEqual(impact.edge_law(w, "plate")[-1]["kind"], "phys.fall")
        self.assertEqual(s["entities"]["plate"]["pos"][0], 3)                                # off the +x side
        self.assertEqual(impact.edge_law(w, "clamped"), [])
        self.assertEqual(impact.edge_law(w, "fine"), [])

    def test_slope_law(self):
        s = house()
        put(s, "tray", "tray", "room", pos=[2, 2], tilt_deg=30.0, tilt_dir_deg=0.0)
        put(s, "mug", "mug", "tray", relation="on", size_m=[0.08, 0.08, 0.1])
        put(s, "vase", "vase", "tray", relation="on", size_m=[0.08, 0.08, 0.4], spos=[0.2, 0.1])
        put(s, "ball", "orange", "tray", relation="on")
        w = self.world(s)
        self.assertEqual(impact.slope_law(w, "mug")[-1]["kind"], "phys.fall")                # slid off downhill
        self.assertEqual(s["entities"]["mug"]["pos"], [3, 2])
        self.assertEqual(impact.slope_law(w, "vase")[0]["kind"], "phys.tip")
        self.processes.accel = 1.2
        self.assertEqual(impact.slope_law(w, "ball")[0]["kind"], "phys.roll")
        self.assertEqual(self.processes.started, [("rolling", "ball")])
        self.assertEqual(s["stats"]["phys_rolls"], 1)
        self.assertEqual(impact.slope_law(w, "ball"), [])                                      # once per step

    def test_stack_law_topples_onto_the_table_and_marks_wobbly_stacks(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "p1", "dinner_plate", "table", relation="on", spos=[0.1, 0.1])
        put(s, "tray", "tray", "p1", relation="on", size_m=[0.5, 0.35, 0.03])
        put(s, "b1", "book", "table", relation="on", spos=[0.8, 0.5], size_m=[0.1, 0.1, 0.16])
        put(s, "b2", "book", "b1", relation="on", size_m=[0.1, 0.1, 0.16])
        put(s, "b3", "book", "b2", relation="on", size_m=[0.1, 0.1, 0.16])
        w = self.world(s)
        records = impact.stack_law(w, "tray")
        tray = s["entities"]["tray"]
        self.assertEqual((tray["location"], tray["orientation"], tray["tipped_over"]), ("table", "on_side", True))
        self.assertEqual([r["kind"] for r in records], ["phys.fall", "phys.tip"])
        self.assertEqual(records[0]["ids"], ["table", "tray"])
        self.assertEqual(impact.stack_law(w, "b3"), [])
        self.assertTrue(all(s["entities"][b]["wobbly"] and s["entities"][b]["_phys_wobble"] for b in ("b1", "b2", "b3")))
        w.destroy("b3")
        impact.stack_law(w, "b2")
        self.assertFalse(any("wobbly" in s["entities"][b] or "_phys_wobble" in s["entities"][b] for b in ("b1", "b2")))
        w.mut("b1")["wobbly"] = True                                   # a domain's own wobbly flag is left alone
        impact.stack_law(w, "b1")
        self.assertTrue(s["entities"]["b1"]["wobbly"])

    def test_furniture_tip_law_needs_front_loads(self):
        s = house()
        put(s, "dresser", "dresser", "room", pos=[2, 0])
        w = self.world(s)
        self.assertEqual(impact.furniture_tip_law(w, "dresser"), [])
        heavy = [(12.0, 0.3, 0.3)] * 3                                                         # full drawers fully out
        with hooked("phys.tip_loads", lambda state, fid: heavy):
            records = impact.furniture_tip_law(w, "dresser")
        self.assertEqual(records[0]["kind"], "phys.tip")
        self.assertTrue(s["entities"]["dresser"]["tipped_over"])


class AttributionAndLedgerTests(PhysCase):
    def glass_at_edge(self, **props):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "glass", "wine_glass", "table", relation="on", spos=[1.12, 0.3], size_m=[0.07, 0.07, 0.1833],
            com_frac=0.6, **props)
        return s

    def test_the_same_bump_counts_breakages_only_when_the_robot_placed_the_glass(self):
        for robot_placed, expected in ((False, None), (True, 1)):
            s = self.glass_at_edge(**({"_robot_moved": True} if robot_placed else {}))
            w = self.world(s)
            out = impact.knock(w, "glass", 0.35, cause="physics.bump")
            self.assertTrue(out["broke"], robot_placed)
            self.assertEqual(s["stats"].get("breakages"), expected)
            self.assertEqual(s["stats"]["phys_breaks"], 1)
            self.assertEqual({e["by"] for e in s["event_log"]}, {"robot" if robot_placed else "exogenous"})

    def test_the_robot_arranged_it_its_supports_or_the_failing_load(self):
        # TIER1_DECISIONS (stage-B review): a sibling the robot moved under the same furniture does not count; the
        # item itself, a support it rests on, or (for a support failure) the load that made it fail does
        s = self.glass_at_edge()
        put(s, "book", "book", "table", relation="on", spos=[0.1, 0.1], _robot_moved=True)
        w = self.world(s)
        self.assertFalse(impact.robot_caused(w, "glass"))
        self.assertTrue(impact.robot_caused(w, "table", load=["book"]))
        self.assertFalse(impact.robot_caused(w, "floor_room"))
        self.assertFalse(impact.robot_caused(w, "ghost"))
        w.mut("table")["_robot_moved"] = True                            # the robot moved the table it stands on
        self.assertTrue(impact.robot_caused(w, "glass"))
        clean = self.glass_at_edge()
        w2 = self.world(clean)
        self.assertFalse(impact.robot_caused(w2, "glass"))
        impact._scratch(w2)["phase"] = "action"                         # the action-phase settle (PHYS-INT) sets this
        self.assertTrue(impact.robot_caused(w2, "glass"))
        self.assertEqual(impact._by(w2, "glass", "cat"), "exogenous")

    def test_damage_levels_never_drop_and_flag_once(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4], damage=["scratch"])
        w = self.world(s)
        self.assertTrue(impact.damage(w, "table", "scorch", 0.3, detail="hot pan", by="robot"))
        self.assertEqual(s["entities"]["table"]["damage"], {"scorch": 0.3, "scratch": 1.0})
        self.assertFalse(impact.damage(w, "table", "scorch", 0.2, by="robot"))
        self.assertTrue(impact.damage(w, "table", "scorch", 0.6, by="robot"))
        self.assertEqual(s["stats"]["damage"], 1)                       # the kind's first appearance only
        self.assertTrue(impact.damage(w, "table", "ring_stain", 2.0, by="exogenous"))
        self.assertEqual(s["entities"]["table"]["damage"]["ring_stain"], 1.0)
        self.assertEqual(s["stats"]["damage"], 1)
        self.assertEqual(impact.damage_map({"a": True, "b": 0.5}), {"a": 1.0, "b": 0.5})
        self.assertEqual(impact.damage_map([{"kind": "dent"}, "chip"]), {"dent": 1.0, "chip": 1.0})
        self.assertEqual(impact.damage_map(0.4), {"general": 0.4})
        self.assertEqual(impact.consequences(w)[0]["kind"], "phys.damage")

    def test_ledger_and_consequences_restart_each_step(self):
        s = house()
        w = self.world(s)
        self.assertTrue(impact.once(w, "x", "fall"))
        self.assertFalse(impact.once(w, "x", "fall"))
        self.assertTrue(impact.once(w, "x", "tip"))
        impact._record(w, "phys.fall", "x", "robot", "x fell")
        self.assertEqual(len(impact.consequences(w)), 1)
        w.set_scalar("steps", 2)
        self.assertEqual(impact.consequences(w), [])
        self.assertTrue(impact.once(w, "x", "fall"))
        w.phys_scratch = {"phase": "substep"}                 # PHYS-INT's World scratch, cleared by begin()
        self.assertTrue(impact.once(w, "x", "fall"))
        self.assertIn("ledger", w.phys_scratch)
        w.phys_scratch = {}
        self.assertTrue(impact.once(w, "x", "fall"))

    def test_ledger_and_consequences_restart_each_substep_of_a_long_advance(self):
        """Review finding: one wait of up to a week is one step, so a step-keyed ledger let an item tip once per wait.
        The scratch restarts with the simulated time: every substep is its own settle phase."""
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "glass", "wine_glass", "table", relation="on", spos=[0.5, 0.3], size_m=[0.07, 0.07, 0.1833],
            com_frac=0.6)
        w = self.world(s)
        self.assertTrue(impact.knock(w, "glass", 0.35, direction=90.0)["tipped"])     # the perturbation at 100 s
        self.assertFalse(impact.once(w, "glass", "tip"))
        w.mut("glass").pop("orientation")                                             # someone stands it up again
        w.mut("glass").pop("tipped_over")
        self.assertEqual(impact.knock(w, "glass", 0.35, direction=90.0)["tipped"], False)   # same phase: ledger
        w.set_scalar("time_s", 400.0)                                                 # a later substep of the wait
        self.assertEqual(impact.consequences(w), [])
        self.assertTrue(impact.knock(w, "glass", 0.35, direction=90.0)["tipped"])     # the repeat_s bump tips it again
        self.assertEqual([r["kind"] for r in impact.consequences(w)], ["phys.tip"])

    def test_notes_name_ids_and_carry_no_numbers(self):
        s = house()
        put(s, "shelf", "bookshelf", "room", pos=[2, 0])
        for i in range(6):
            put(s, "book_{}".format(i), "book", "shelf", relation="in", shelf=i % 5, mass_kg=9.0)
        put(s, "jar_1", "drinking_glass", "shelf", relation="in", shelf=4, mass_kg=17.0)
        w = self.world(s)
        records = impact.overload_law(w, "shelf")
        self.assertTrue(records)
        ids = set(s["entities"])
        for record in records:
            text = record["note"]
            for eid in sorted(ids, key=len, reverse=True):
                text = text.replace(eid, "")
            self.assertIsNone(re.search(r"\d", text), record["note"])
            self.assertTrue(set(record["ids"]) <= ids)

    def test_laws_match_the_lawbook_and_the_process_kinds(self):
        from house_sim.kernel.phys import lawbook
        import inspect
        for law_id, order, fn in impact.LAWS:
            self.assertEqual(lawbook.LAWS[law_id]["order"], order, law_id)
            self.assertEqual(str(inspect.signature(fn)), "(w, eid, env=None, cmap=None)", law_id)   # one settle call
        for fn in impact.PROCESS_HANDLERS.values():
            self.assertEqual(list(inspect.signature(fn).parameters), ["w", "eid", "args"])
        self.assertEqual(sorted(impact.PROCESS_HANDLERS), ["creep", "handle_creep"])


class MoreGeometryAndConsequenceTests(PhysCase):
    def test_open_containers_that_survive_a_fall_still_lose_their_liquid(self):
        s = house(floor="carpet")
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "pot", "book", "table", relation="on", spos=[0.1, 0.1], material="cast_iron", size_m=[0.25, 0.25, 0.12],
            capacity_l=2.0, liquid="water", liquid_l=1.0, mass_kg=2.0)
        put(s, "flask", "book", "table", relation="on", spos=[0.6, 0.1], material="steel", size_m=[0.08, 0.08, 0.3],
            capacity_l=1.0, liquid="water", liquid_l=0.9, mouth_frac=0.3)
        put(s, "jug", "book", "table", relation="on", spos=[0.9, 0.1], material="steel", size_m=[0.15, 0.15, 0.2],
            capacity_l=1.5, liquid="water", liquid_l=1.0, lid_on=True)
        w = self.world(s)
        for eid in ("pot", "flask", "jug"):
            self.assertIsNone(impact.fall(w, eid)["outcome"])
        released = {eid: liters for eid, liters, _, _ in self.liquids.released}
        self.assertEqual(released["pot"], 1.0)                               # a wide pot lands any way up: empty
        self.assertAlmostEqual(released["flask"], 0.9 - 1.0 * 0.7 / 2)       # on its side a narrow neck keeps some
        self.assertNotIn("jug", released)                                     # lidded
        self.assertEqual(s["entities"]["flask"]["orientation"], "on_side")
        self.assertAlmostEqual(s["entities"]["flask"]["liquid_l"], 0.35)

    def test_toppled_stack_members_land_inside_shelves_beside_floor_stacks_and_off_carried_trays(self):
        s = house()
        put(s, "case", "bookshelf", "room", pos=[2, 0])
        put(s, "b1", "book", "case", relation="in", shelf=1, size_m=[0.25, 0.2, 0.2])
        put(s, "b2", "book", "b1", relation="on", size_m=[0.25, 0.2, 0.2])                # 0.4 m in a 0.38 m gap
        put(s, "crate", "cardboard_box", "room", pos=[6, 6], size_m=[0.3, 0.3, 0.3])
        put(s, "drum", "book", "crate", relation="on", size_m=[0.5, 0.5, 0.3])            # > 1.25 x the crate
        put(s, "tray", "tray", "room", size_m=[0.5, 0.35, 0.03])
        put(s, "plate", "dinner_plate", "tray", relation="on")
        put(s, "platter", "tray", "plate", relation="on", size_m=[0.5, 0.35, 0.03])
        hold(s, "tray", ("left", "right"))
        w = self.world(s)
        impact.stack_law(w, "b2")
        b2 = s["entities"]["b2"]
        self.assertEqual((b2["location"], b2["relation"], b2["shelf"], b2["orientation"]), ("case", "in", 1, "on_side"))
        impact.stack_law(w, "drum")
        drum = s["entities"]["drum"]
        self.assertEqual((drum["location"], drum["orientation"]), ("room", "on_side"))
        self.assertEqual(geometry_cells(drum), [(6, 5)])                                   # beside the crate
        impact.stack_law(w, "platter")
        platter = s["entities"]["platter"]
        self.assertEqual(platter["location"], "room")                                      # off the carried tray
        self.assertEqual(s["event_log"][-2]["height_m"], 1.0 + 0.03 + 0.025)    # geometry: held items' base at 1.0 m

    def test_the_bottom_of_a_fixed_container_and_a_closet_with_rails_and_shelves(self):
        s = house()
        put(s, "bin", "cardboard_box", "room", pos=[1, 1], portable=False, container=True, max_load_kg=10.0)
        put(s, "bricks", "book", "bin", relation="in", mass_kg=11.0)
        put(s, "closet", "closet", "room", pos=[4, 0], hang_kg=30.0)
        put(s, "coat", "raincoat", "closet", relation="hung", mass_kg=5.0)
        put(s, "hats", "book", "closet", relation="in", shelf=1, mass_kg=2.0)
        w = self.world(s)
        records = impact.overload_law(w, "bin")
        self.assertEqual(s["entities"]["bin"]["damage"]["collapsed"], 1.0)
        self.assertEqual(s["entities"]["bricks"]["location"], "room")
        self.assertEqual(records[-1]["kind"], "phys.collapse")
        self.assertEqual(impact.collapse(w, "bin", ("in",)), [])                           # idempotent
        self.assertEqual(impact.overload_law(w, "closet"), [])                             # rails are the hang law's
        self.assertEqual(s["entities"]["coat"]["location"], "closet")

    def test_tears_in_a_persons_hands_and_a_carried_bucket(self):
        s = house()
        s["people"]["ann"] = {"location": "room", "pos": [6, 1]}
        put(s, "bag", "grocery_bag", "ann", relation="held", handle_rating_kg=6.0)
        put(s, "tins", "book", "bag", relation="in", mass_kg=7.0)
        put(s, "bucket", "book", "room", material="plastic", size_m=[0.3, 0.3, 0.3], capacity_l=10.0,
            liquid="water", liquid_l=9.0, handle_rating_kg=8.0, mass_kg=1.0)
        hold(s, "bucket")
        put(s, "sack", "grocery_bag", "room", pos=[3, 6])
        put(s, "rock", "book", "sack", relation="in", mass_kg=2.0)
        w = self.world(s)
        impact.handle_law(w, "bag")
        tins = s["entities"]["tins"]
        self.assertEqual((tins["location"], tins["pos"]), ("room", [6, 1]))
        self.assertEqual([e for e in s["event_log"] if e["kind"] == "phys.fall"][0]["height_m"], 0.9)
        impact.handle_law(w, "bucket")                                                       # 9 kg of water > 8 kg
        self.assertEqual(self.liquids.released, [("bucket", 9.0, "water", "exogenous")])
        self.assertEqual(s["entities"]["bucket"]["liquid_l"], 0.0)
        self.assertTrue(s["entities"]["bucket"]["torn"])
        impact.tear(w, "sack")                                                               # a resting sack splits
        self.assertEqual(s["entities"]["rock"]["location"], "room")

    def test_landing_fallbacks(self):
        s = house()
        put(s, "floor_two", "floor_surface", "room", pos=[0, 0], ground_layer=True)
        del s["entities"]["floor_room"]
        self.assertEqual(impact.floor_entity(s, "room"), "floor_two")
        s["rooms"]["hall"] = {"size_m": [2.0, 2.0], "floor_material": "tile", "exits": []}
        put(s, "elsewhere", "book", "hall", pos=[1, 1])
        self.assertEqual(impact.landing_cell(s, "elsewhere", "room"), [4, 3])                # next to the robot
        s["robot"]["room"] = "hall"
        self.assertEqual(impact.landing_cell(s, "elsewhere", "room"), [4, 3])                # next to the room centre
        put(s, "edge", "book", "room", pos=[0, 3])
        self.assertEqual(impact.landing_cell(s, "edge", "room", prefer=180.0), [0, 2])       # the line leaves the room
        put(s, "sofa", "sofa", "room", pos=[5, 5], rot=0)
        w = self.world(s)
        out = impact.fall(w, "edge", height_m=0.3, at=("room", [5, 5]))                    # someone drops it on a sofa cell
        self.assertNotIn(tuple(out["cell"]), set(geometry_cells(s["entities"]["sofa"])))
        self.assertIsNone(impact.floor_entity({"entities": {}, "rooms": {"x": {}}}, "x"))

    def test_setdown_on_the_floor_and_points_of_held_things(self):
        s = house()
        put(s, "egg", "book", "room", pos=[2, 2], material="eggshell", size_m=[0.045, 0.045, 0.06])
        put(s, "keys", "book", "robot")
        hold(s, "keys")
        w = self.world(s)
        self.assertIsNone(impact.setdown(w, "egg", speed="normal")["outcome"])            # 0.02 m x tile 1.0
        self.assertEqual(impact.setdown(w, "egg", speed="fast")["outcome"], "broke")
        self.assertEqual(statics.room_point(s, "keys"), (4.5 * 0.5, 4.5 * 0.5))
        self.assertEqual(impact.setdown(w, "keys"), {"h_eff_m": 0.0, "outcome": None})     # still in the hand

    def test_offsets_from_spots_turns_and_hung_loads(self):
        s = house()
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "p1", "dinner_plate", "table", relation="on", spos=[0.1, 0.1])
        put(s, "p2", "dinner_plate", "p1", relation="on", spos=[0.09, 0.0])               # its centre 0.09 m off
        self.assertEqual(statics.stack_verdict(s, "p1"), [("p2", "topple")])
        s["entities"]["p2"]["spos"] = [0.05, 0.0]
        self.assertEqual(statics.stack_verdict(s, "p1"), [])
        put(s, "board", "book", "room", pos=[1, 1], size_m=[0.1, 0.4, 0.02], yaw_deg=45.0)
        self.assertEqual(statics.world_yaw(s, "board"), 45.0)
        s["entities"]["board"]["orientation"] = "on_side"                                  # d > w: d swaps with h
        self.assertEqual(support.resting_size(s, "board"), [0.1, 0.02, 0.4])
        put(s, "rack", "coat_hooks", "room", pos=[7, 0], mounted=True, size_m=[1.0, 0.1, 0.1], hang_height_m=0.08,
            mass_kg=1.0, com_frac=0.5)
        put(s, "coat", "raincoat", "rack", relation="hung", mass_kg=1.0, size_m=[0.3, 0.25, 0.9])
        # the coat hangs from the hook line: base 0.08 - 0.9 below the rack's base, its centre 0.45 above that
        expected = (1.0 * 0.05 + 1.0 * (0.08 - 0.9 + 0.45 if False else max(0.0, 0.08 - 0.9) + 0.45)) / 2.0
        self.assertAlmostEqual(support.com_height(s, "rack"), expected)


def geometry_cells(record):
    from house_sim.kernel import geometry
    return geometry.footprint(record)


class ContactFrictionTests(unittest.TestCase):
    """support.contact_mu against the real data layer (forces.friction and floors.json, PHYS-DATA)."""

    def test_contact_friction_of_a_pair_and_on_a_floor(self):
        from house_sim.kernel.phys import forces
        s = house(floor="carpet")
        put(s, "table", "table", "room", pos=[0, 4])
        put(s, "mug", "mug", "table", relation="on")
        put(s, "ball", "orange", "room", pos=[1, 1])
        dry = support.contact_mu(s, "mug")
        self.assertEqual(dry, forces.friction(s["entities"]["mug"], s["entities"]["table"]))
        self.assertEqual(statics.slide_accel_g(s, "mug"), dry)
        s["entities"]["mug"]["wetness"] = 0.5                       # a wet mug slides sooner
        self.assertLess(support.contact_mu(s, "mug"), dry)
        self.assertEqual(support.contact_mu(s, "ball"), support.floor_at(s, "room", [1, 1])["mu"])


class DeterminismAndCostTests(PhysCase):
    SCRIPT = textwrap.dedent("""
        import json, sys, warnings
        warnings.simplefilter("ignore")
        sys.path.insert(0, {root!r})
        from house_sim.kernel.registry import default_registry
        from house_sim.kernel.world import World
        from house_sim.kernel.phys import impact
        from house_sim.kernel.predicates import digest
        reg = default_registry()
        s = {{"rooms": {{"room": {{"size_m": [4.0, 4.0], "floor_material": "tile", "exits": []}}}}, "entities": {{}},
             "people": {{}}, "pets": {{}}, "connections": {{}}, "steps": 1, "time_s": 0.0, "stats": {{}},
             "event_log": [], "logs": {{"moves": []}},
             "robot": {{"room": "room", "pos": [4, 4], "hands": {{"left": None, "right": None}}}}}}
        s["entities"]["shelf"] = reg.make("bookshelf", "room", pos=[2, 0])
        glass = {{"material": "glass", "size_m": [0.08, 0.08, 0.13], "fragile": True}}
        for i in range(12):
            s["entities"]["item_%d" % i] = reg.make("portable_object" if i % 3 else "book", "shelf", relation="in",
                                                  shelf=i % 5, mass_kg=4.0 + i, **(glass if i % 3 else {{}}))
        w = World(s, reg)
        impact.overload_law(w, "shelf")
        print(digest({{"entities": s["entities"], "stats": s["stats"], "log": s["event_log"]}}))
    """)

    def test_the_same_cascade_gives_the_same_state_in_other_processes(self):
        script = self.SCRIPT.format(root=ROOT)
        digests = set()
        for seed in ("1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1)

    @classmethod
    def setUpClass(cls):
        from house_sim.generator import generate_house
        from house_sim.kernel.schema import normalize
        cls.big = normalize(generate_house(2), REG)
        cls.big.update(steps=1, time_s=0.0, stats={}, event_log=[], logs={"moves": []})

    @staticmethod
    def best_ms(fn, repeats=5):
        best = None
        for _ in range(repeats):
            start = time.perf_counter()
            fn()
            elapsed = (time.perf_counter() - start) * 1000.0
            best = elapsed if best is None else min(best, elapsed)
        return best

    def test_cost_budgets_on_a_generated_house(self):
        state = clone(self.big)
        size = len(state["entities"])                    # about 930 with every domain, 220 with core only
        self.assertGreater(size, 100)
        # TIER1_PHYSICS 5.1: the children map is O(N), about 0.15 ms on 900 entities (tests fail at 3x a budget)
        self.assertLess(self.best_ms(lambda: support.children_map(state)), 0.45 * max(1.0, size / 900.0))
        cmap = support.children_map(state)
        furniture = sorted(e for e, r in state["entities"].items() if r.get("shelf_heights"))[0]
        # a typical action settle: every law on a loaded piece of furniture and its contents (about 0.25 ms for a
        # bookcase with 7 items; budget 1 ms per action)
        w = World(state, REG)
        w.begin()
        nodes = [furniture] + support.descendants(state, furniture, cmap)
        self.assertLess(self.best_ms(lambda: [fn(w, e, cmap=cmap) for _, _, fn in impact.LAWS for e in nodes]), 3.0)
        w.rollback()

    def test_a_64_consequence_cascade_stays_within_budget(self):
        def cascade():
            s = house(cols=12, rows=12)
            put(s, "shelf", "shelf_unit", "room", pos=[4, 0])
            for i in range(20):
                put(s, "glass_{}".format(i), "drinking_glass", "shelf", relation="in", shelf=3, mass_kg=4.6)
            w = World(s, REG)
            w.begin()
            start = time.perf_counter()
            impact.overload_law(w, "shelf", cmap=support.children_map(s))
            elapsed = (time.perf_counter() - start) * 1000.0
            return elapsed, len(impact.consequences(w))
        best, count = min(cascade() for _ in range(3))
        self.assertGreaterEqual(count, 60)
        self.assertLess(best, 30.0)                      # worst-case settle budget 10 ms, failing at 3x


if __name__ == "__main__":
    unittest.main()
