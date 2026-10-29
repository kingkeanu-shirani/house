"""Tier-1 physics: liquids, carry dynamics, timed processes, wind and rain (docs/design/TIER1_PHYSICS.md 5.7, 5.8,
5.10, 6.2, 6.3 and the test plan of 12.4; package PHYS-FLUIDS).

The physics modules of the other stage-A packages (props and lawbook of PHYS-DATA; support, statics and impact of
PHYS-STATICS) are replaced by the small fakes below, which implement their frozen contracts (TIER1_PHYSICS 7.1) over
explicit record fields and the material rows of section 4.3. These tests therefore exercise this package alone; the
integration with the real modules is PHYS-INT's (tests/test_physics_settle.py). test_physics_thermal.py reuses the
fakes.
"""
import json
import math
import os
import subprocess
import sys
import time
import unittest
from collections import namedtuple
from unittest import mock

from house_sim.kernel import plugins
from house_sim.kernel.phys import environment, liquids, processes, thermal
from house_sim.kernel.world import World, clone

INF = float("inf")
G = 9.81

# ----- fakes of the sibling packages ---------------------------------------------------------------------------------

Phys = namedtuple("Phys", (
    "cls", "mass_kg", "size_m", "shape", "com_frac", "brittle", "break_mode", "rigid", "fragile", "drop_tol_m",
    "crush_kg", "hard",
    "mu", "waterproof", "absorb_l", "softness", "wsens", "combustible", "c_p", "heat_limit_c", "melt_c", "shock_k",
    "rating_kg", "shelf_rating_kg", "hang_kg", "handle_kg", "rim_m", "mouth_frac", "opening_m", "rollable", "floats",
    "anchored", "secured", "drag_cd"), defaults=(None,))

# TIER1_PHYSICS 4.3 rows the tests use: c_p, brittle (props' break_mode for the non-shattering ones), waterproof,
# absorption L/kg, softness, heat limit, melt, shock K, water sensitivity, mu, rigid
CLASSES = {
    "glass": (840, True, True, 0.0, 0.0, 150, INF, 45, 0.0, 0.4, True),
    "borosilicate": (830, True, True, 0.0, 0.0, 450, INF, 160, 0.0, 0.4, True),
    "ceramic": (850, True, True, 0.0, 0.0, 1000, INF, 80, 0.0, 0.5, True),
    "stone": (790, "chip", True, 0.0, 0.0, 300, INF, 150, 0.05, 0.6, True),
    "enameled_metal": (500, "chip", True, 0.0, 0.0, 500, INF, 150, 0.0, 0.4, True),
    "steel": (500, False, True, 0.0, 0.0, 800, INF, INF, 0.1, 0.4, True),
    "cast_iron": (460, False, True, 0.0, 0.0, 800, INF, 150, 0.3, 0.45, True),
    "aluminum": (900, False, True, 0.0, 0.0, 500, 660, INF, 0.0, 0.4, True),
    "plastic": (1500, False, True, 0.0, 0.1, 80, 120, INF, 0.0, 0.45, True),
    "film": (1900, False, True, 0.0, 0.3, 80, 110, INF, 0.0, 0.3, False),
    "wax": (2500, False, True, 0.0, 0.1, 45, 50, INF, 0.0, 0.4, True),
    "rubber": (1900, False, True, 0.0, 0.6, 200, INF, INF, 0.0, 0.9, False),
    "cotton": (1300, False, False, 2.5, 0.8, 200, INF, INF, 0.3, 0.6, False),
    "paper": (1300, False, False, 2.0, 0.2, 200, INF, INF, 1.0, 0.5, False),
    "cardboard": (1300, False, False, 1.5, 0.15, 200, INF, INF, 0.9, 0.55, True),
    "wood": (1700, False, False, 0.05, 0.05, 60, INF, INF, 0.5, 0.5, True),
    "engineered_wood": (1600, False, False, 0.15, 0.0, 60, INF, INF, 0.9, 0.5, True),
    "cork": (2000, False, False, 0.1, 0.4, 120, INF, INF, 0.2, 0.6, True),
    "electronics": (1000, "crack", False, 0.0, 0.0, 60, INF, INF, 1.0, 0.4, True),
    "foam": (1400, False, False, 10.0, 0.9, 100, 180, INF, 0.3, 0.8, False),
    "granular": (800, False, False, 0.3, 0.5, INF, INF, INF, 0.5, 0.6, False),
}
# TIER1_PHYSICS 4.4 rows: rho, viscosity cP, freezing point, floor hazard, film mm
LIQUIDS = {"water": (1000, 1.0, 0.0, "wet", 1.5), "coffee": (1000, 1.0, 0.0, "wet", 1.5),
           "milk": (1030, 2.0, -0.5, "wet", 1.5), "soup": (1030, 10.0, -1.0, "wet", 2.0),
           "oil": (920, 60.0, -5.0, "slip", 2.5), "honey": (1420, 10000.0, None, "sticky", 3.0)}
FLOORS = {"tile": {"c_rr": 0.01, "absorbs": False, "mu": 0.35, "film_mm": None},
          "hardwood": {"c_rr": 0.01, "absorbs": False, "mu": 0.3, "film_mm": None},
          "carpet": {"c_rr": 0.06, "absorbs": True, "mu": 0.6, "film_mm": 3.0},
          "concrete": {"c_rr": 0.01, "absorbs": False, "mu": 0.55, "film_mm": None},
          "grass": {"c_rr": 0.10, "absorbs": True, "mu": 0.7, "film_mm": None}}


class FakeProps(object):
    """props (PHYS-DATA): material class from the record's material, record overrides on top, no variation."""
    MATERIALS = {"classes": {name: {"c_p": row[0]} for name, row in CLASSES.items()}}
    LIQUIDS = {"min_spill_l": {"value": 0.005}}

    def material_class(self, state_or_none, record):
        return record.get("material", "plastic")

    def props(self, state, eid):
        return self.props_of(state["entities"][eid])

    def props_of(self, rec):
        cls = self.material_class(None, rec)
        c_p, brittle, wp, absorb, soft, heat, melt, shock, wsens, mu, rigid = CLASSES[cls]
        mass = float(rec.get("mass_kg", 0.4))
        shape = rec.get("shape", "box")
        return Phys(
            cls=cls, mass_kg=mass, size_m=list(rec.get("size_m") or [0.1, 0.1, 0.1]), shape=shape, com_frac=0.5,
            brittle=brittle is True, break_mode="shatter" if brittle is True else brittle or None, rigid=rigid, fragile=bool(rec.get("fragile")), drop_tol_m=INF, crush_kg=INF, hard=1.0,
            mu=rec.get("friction_mu", mu), waterproof=rec.get("waterproof", wp),
            absorb_l=rec.get("absorb_l_per_kg", absorb) * mass, softness=soft, wsens=wsens, combustible=False, c_p=c_p,
            heat_limit_c=rec.get("heat_limit_c", heat), melt_c=rec.get("melt_c", melt),
            shock_k=rec.get("thermal_shock_c", shock), rating_kg=None, shelf_rating_kg=None, hang_kg=None,
            handle_kg=rec.get("handle_rating_kg"), rim_m=rec.get("rim_m", 0.0), mouth_frac=rec.get("mouth_frac", 1.0),
            opening_m=None, floats=False, anchored=bool(rec.get("anchored")), secured=bool(rec.get("secured")),
            drag_cd=rec.get("drag_cd"),
            rollable=shape == "sphere" or (shape == "cylinder" and rec.get("orientation") == "on_side"))

    def liquid_props(self, name):
        rho, visc, freeze, hazard, film = LIQUIDS.get(name, LIQUIDS["water"])
        return {"rho": rho, "visc_cp": visc, "freeze_c": freeze, "floor_hazard": hazard, "film_mm": film}

    def floor_props(self, state, rid, cell=None):
        return dict(FLOORS[state["rooms"][rid].get("floor_material", "tile")])


class FakeLawbook(object):
    """lawbook (PHYS-DATA): the set of active laws; everything else deferred."""

    def __init__(self, active=()):
        self.on = set(active)

    def active(self, law_id):
        return law_id in self.on


class FakeSupport(object):
    """support (PHYS-STATICS): the support graph of 5.1, subtree masses and the carry geometry (a hand 0.8 m up:
    not humanoid_v1's 1.0 m, so the tests see that liquids reads it from support)."""
    CARRY = {"hand_z_m": 0.8, "hip_z_m": 0.7, "front_m": 0.3}

    def support_of(self, state, eid):
        entities = state["entities"]
        rec = entities.get(eid)
        if rec is None:
            return None
        loc, relation = rec.get("location"), rec.get("relation", "on")
        if loc in entities:
            if relation == "in":
                return loc, ("in",)
            if relation == "on":
                return loc, ("surface",)
            return (loc, ("hung",)) if relation == "hung" else None
        if loc in state["rooms"]:
            return loc, ("floor", tuple(rec["pos"]) if rec.get("pos") else None)
        if loc == "robot" or loc in state.get("people", {}):
            return loc, ("held",)
        return None

    def children_map(self, state):
        cmap = {}
        for eid in sorted(state["entities"]):
            cmap.setdefault(state["entities"][eid].get("location"), []).append(eid)
        return cmap

    def subtree_mass(self, state, eid, cmap=None):
        cmap = self.children_map(state) if cmap is None else cmap
        rec = state["entities"][eid]
        total = float(rec.get("mass_kg", 0.0)) + float(rec.get("liquid_l", 0.0))
        return total + sum(self.subtree_mass(state, child, cmap) for child in cmap.get(eid, ()))


class FakeStatics(object):
    """statics (PHYS-STATICS): tilt, rolling resistance and edge margin of a placed item."""

    def support_tilt_deg(self, state, eid):
        loc = state["entities"][eid]["location"]
        holder = state["entities"].get(loc) or state["rooms"].get(loc) or {}
        return float(holder.get("tilt_deg", holder.get("slope_deg", 0.0)))

    def rolling_resistance(self, state, eid):
        loc = state["entities"][eid]["location"]
        return FLOORS[state["rooms"][loc].get("floor_material", "tile")]["c_rr"] if loc in state["rooms"] else 0.01

    def edge_margin_m(self, state, eid):
        rec = state["entities"][eid]
        top = state["entities"].get(rec["location"])
        if top is None or "spos" not in rec:
            return None
        sw, sd = top["size_m"][:2]
        cx, cy = rec["spos"][0] + rec["size_m"][0] / 2.0, rec["spos"][1] + rec["size_m"][1] / 2.0
        return min(cx, sw - cx, cy, sd - cy)


class FakeImpact(object):
    """impact (PHYS-STATICS): consequences recorded in a per-step list; breaks, damage, falls, tips and the statics
    timers (PROCESS_HANDLERS) as the frozen contract describes them, plus a call log for assertions."""

    def __init__(self):
        self.calls = []
        self.lists = {}
        self.PROCESS_HANDLERS = {"creep": self._creep_fire}

    def consequences(self, w):
        return self.lists.setdefault((id(w), w.s.get("steps")), [])

    def robot_caused(self, w, eid):
        return bool(w.s["entities"][eid].get("_robot_moved"))

    def _who(self, w, eid, by):
        return by if by is not None else ("robot" if self.robot_caused(w, eid) else "exogenous")

    def break_entity(self, w, eid, cause, by=None):
        self.calls.append(("break", eid, cause))
        rec = w.mut(eid)
        if rec.get("broken"):
            return False
        rec["broken"] = True
        rec["condition"] = "broken"
        self.consequences(w).append({"kind": "phys.break", "target": eid, "by": self._who(w, eid, by),
                                     "note": "{} broke".format(eid)})
        return True

    def damage(self, w, eid, kind, severity, detail="", by=None):
        self.calls.append(("damage", eid, kind, round(severity, 4)))
        damage = dict(w.ent(eid).get("damage") or {})
        if severity > damage.get(kind, 0.0):
            damage[kind] = round(severity, 4)
            w.mut(eid)["damage"] = damage
            return True
        return False

    def fall(self, w, eid, height_m=None, cause="fall", at=None, onto=None, by=None):
        self.calls.append(("fall", eid, cause, height_m, at))
        rec = w.mut(eid)
        rid, cell = at if at is not None else processes.floor_cell(w.s, eid)
        rec["location"], rec["relation"], rec["pos"] = rid, "on", list(cell or (0, 0))
        rec.pop("spos", None)
        return {"fell": True}

    def tip_over(self, w, eid, cause, direction=None, by=None):
        self.calls.append(("tip", eid, cause))
        rec = w.mut(eid)
        rec["orientation"], rec["tipped_over"] = "on_side", True
        return {"tipped": True}

    def _creep_fire(self, w, eid, args):
        self.calls.append(("creep", eid, args))
        return []


class FakeLayer(object):
    """The five sibling modules, installed into this package's modules for one test."""
    DEPENDENCIES = {processes: ("props", "statics", "impact"), liquids: ("props", "support", "impact"),
                    thermal: ("props", "lawbook", "impact"), environment: ("props", "lawbook", "support", "impact")}

    def __init__(self, active_laws=()):
        self.props = FakeProps()
        self.lawbook = FakeLawbook(active_laws)
        self.support = FakeSupport()
        self.statics = FakeStatics()
        self.impact = FakeImpact()

    def install(self, test):
        for module, names in sorted(self.DEPENDENCIES.items(), key=lambda kv: kv[0].__name__):
            for name in names:
                patcher = mock.patch.object(module, name, getattr(self, name))
                patcher.start()
                test.addCleanup(patcher.stop)
        return self


class Hooks(object):
    """Register kernel hooks for one test and remove them afterwards."""

    def __init__(self, test):
        self.test = test

    def add(self, name, fn):
        plugins.HOOKS[name].append((0, "test." + name, fn))
        self.test.addCleanup(lambda: plugins.HOOKS[name].remove((0, "test." + name, fn)))
        return fn


# ----- worlds --------------------------------------------------------------------------------------------------------

def room(floor="tile", size=(4.0, 4.0), **extra):
    out = {"size_m": list(size), "floor_material": floor, "temperature_c": 21.0, "humidity": 0.45}
    out.update(extra)
    return out


def item(location, **fields):
    """An entity record: an on-relation item with the registry's base fields unless given."""
    rec = {"type": fields.pop("type", "generic_item"), "is_a": [], "location": location, "relation": "on",
           "material": "plastic", "mass_kg": 0.4, "size_m": [0.1, 0.1, 0.1], "temperature_c": 21.0, "wetness": 0.0,
           "capacity_l": 0.0, "portable": True}
    rec.update(fields)
    return rec


def world(entities, rooms=None, robot=None, time_s=0.0, weather=None):
    state = {"rooms": rooms or {"kitchen": room()}, "entities": {k: dict(v) for k, v in entities.items()},
             "robot": robot or {"room": "kitchen", "pos": [1, 1], "hands": {"left": None, "right": None},
                                "speed_mode": "normal"},
             "people": {}, "pets": {}, "time_s": time_s, "steps": 1, "stats": {}, "event_log": [],
             "utilities": {"indoor_temp_c": 21.0}, "weather": weather or {}}
    w = World(state)
    w.begin()
    return w


def mug(location, liters=0.32, **fields):
    """A 350 ml ceramic mug of coffee: 9 x 9 x 10 cm, 9 cm deep inside."""
    rec = {"material": "ceramic", "mass_kg": 0.35, "size_m": [0.09, 0.09, 0.10], "inner_size_m": [0.08, 0.08, 0.09],
           "capacity_l": 0.35, "liquid_l": liters, "liquid": "coffee", "shape": "cylinder"}
    rec.update(fields)
    return item(location, **rec)


def table(location="kitchen", material="wood", pos=(2, 2), **fields):
    return item(location, material=material, mass_kg=30.0, size_m=[1.2, 0.8, 0.75], pos=list(pos), portable=False,
                accepts_items=True, **fields)


def advance(w, seconds, step=30.0):
    """Run the physics.processes system over `seconds` in substeps of at most 30 s, as the engine does."""
    left = seconds
    while left > 1e-9:
        dt = min(step, left)
        w.s["time_s"] = round(w.s["time_s"] + dt, 3)
        processes.run(w, dt, 0)
        left -= dt


def puddles(w):
    return sorted(e for e, r in w.s["entities"].items() if r.get("_phys_spill"))


def hide_domain_hooks(test):
    """These are tests of the kernel layer alone, on Worlds without a type registry: hide the domains' hooks for one
    test (and restore them), so the result does not depend on whether another test in the same process loaded every
    domain (cleaning's phys.spill_sink turns a kernel puddle into a cleaning spill entity, which needs a registry;
    outdoor_plants' phase-1 observation: "World has no type registry" in a one-process run of every weather test)."""
    for entries in list(plugins.HOOKS.values()):
        kept = [e for e in entries if not str(e[1]).startswith("house_sim.systems.")]
        if len(kept) != len(entries):
            saved = list(entries)
            entries[:] = kept
            test.addCleanup(entries.__setitem__, slice(None), saved)


class PhysTest(unittest.TestCase):
    active_laws = ()

    def setUp(self):
        self.fake = FakeLayer(self.active_laws).install(self)
        self.hooks = Hooks(self)
        hide_domain_hooks(self)

    def assertJournaled(self, w, fn, *args, **kwargs):
        """fn's writes all go through the World journal: rolling back restores the state exactly."""
        w.begin()
        before = clone(w.s)
        result = fn(*args, **kwargs)
        after = clone(w.s)
        w.rollback()
        self.assertEqual(w.s, before)
        w.s.clear()
        w.s.update(after)
        w.begin()
        return result

    def records(self, w):
        return self.fake.impact.consequences(w)


# ----- quantities and spill volumes ----------------------------------------------------------------------------------

class QuantityTests(PhysTest):
    def test_mass_fill_headroom_and_frozen_fraction(self):
        rec = mug("kitchen", liters=0.3, liquid="milk")
        self.assertAlmostEqual(liquids.liquid_mass(rec), 0.3 * 1.03)
        self.assertAlmostEqual(liquids.liquid_mass(dict(rec, liquid_density=1.2)), 0.36)
        self.assertAlmostEqual(liquids.fill_frac(rec), 0.3 / 0.35)
        self.assertAlmostEqual(liquids.headroom_m(rec), (1 - 0.3 / 0.35) * 0.09)
        self.assertAlmostEqual(liquids.headroom_m(item("kitchen", size_m=[0.1, 0.1, 0.2], capacity_l=1.0,
                                                      liquid_l=0.5)), 0.5 * 0.18)       # 0.9 x the outer height
        self.assertEqual(liquids.headroom_m(item("kitchen")), 0.0)
        self.assertEqual(liquids.fill_frac(item("kitchen")), 0.0)
        self.assertAlmostEqual(liquids.free_liquid_l(dict(rec, ice_frac=0.25)), 0.225)

    def test_container_closed_covers_lids_caps_closures_and_pumps(self):
        self.assertFalse(liquids.container_closed(mug("kitchen")))
        for extra in ({"sealed": True}, {"lid_on": True}, {"cap": {"on": True}}, {"openable": True, "open": False},
                      {"zipper": {"closed_frac": 1.0}}, {"closure": {"holds": True}}, {"pump": {}},
                      {"covered": True, "cover_sealed": True}):
            self.assertTrue(liquids.container_closed(dict(mug("kitchen"), **extra)), extra)
        self.assertFalse(liquids.container_closed(dict(mug("kitchen"), cap={"on": True, "loose": True})))
        self.assertFalse(liquids.container_closed(dict(mug("kitchen"), openable=True, open=True)))

    def test_on_side_spill_wide_mouth_loses_all_narrow_neck_keeps_below_the_neck(self):
        cup = mug("kitchen", liters=0.3)
        self.assertEqual(liquids.tip_spill_l(cup, "on_side"), 0.3)
        self.assertEqual(liquids.tip_spill_l(cup, "upright"), 0.0)
        bottle = item("kitchen", size_m=[0.08, 0.08, 0.3], capacity_l=1.0, liquid_l=0.8, mouth_frac=0.3)
        self.assertAlmostEqual(liquids.tip_spill_l(bottle, "on_side"), 0.8 - 1.0 * 0.7 / 2)
        self.assertEqual(liquids.tip_spill_l(bottle, "upside_down"), 0.8)
        self.assertAlmostEqual(liquids.tip_spill_l(bottle, "on_side", mouth=0.8), 0.8)    # props' value wins
        self.assertEqual(liquids.tip_spill_l(dict(bottle, cap={"on": True}), "upside_down"), 0.0)
        quarter_full = dict(bottle, liquid_l=0.25)
        self.assertEqual(liquids.tip_spill_l(quarter_full, "on_side"), 0.0)

    def test_tilt_spill_begins_at_twice_the_headroom_over_the_diameter(self):
        cup = mug("kitchen", liters=0.3)
        headroom, diameter = liquids.headroom_m(cup), 0.09
        onset = math.degrees(math.atan(2 * headroom / diameter))
        self.assertEqual(liquids.tilt_spill_l(cup, onset - 0.5), 0.0)
        self.assertGreater(liquids.tilt_spill_l(cup, onset + 2.0), 0.0)
        losses = [liquids.tilt_spill_l(cup, a) for a in (10, 20, 30, 45, 60, 80, 89)]
        self.assertEqual(losses, sorted(losses))
        self.assertEqual(liquids.tilt_spill_l(cup, 120), 0.3)
        self.assertEqual(liquids.tilt_spill_l(cup, -onset - 5), liquids.tilt_spill_l(cup, onset + 5))
        bottle = item("kitchen", size_m=[0.08, 0.08, 0.3], capacity_l=1.0, liquid_l=0.9, mouth_frac=0.3)
        self.assertAlmostEqual(liquids.tilt_spill_l(bottle, 89.0), 0.9 - 0.35)   # the neck keeps what it keeps lying
        self.assertEqual(liquids.tilt_spill_l(dict(cup, lid_on=True), 60), 0.0)

    def test_slosh_is_manipulations_model(self):
        cup = mug("kitchen", liters=0.32)
        headroom = (1 - 0.32 / 0.35) * 0.09
        area = 0.35e-3 / (0.10 * 0.9)
        amplitude = 0.010 * 1.7 * math.sqrt(0.09 / 0.08)
        self.assertAlmostEqual(liquids.slosh_spill_l(cup, 1.7), round((amplitude - headroom) * area * 600.0, 4))
        self.assertEqual(liquids.slosh_spill_l(cup, 0.5), 0.0)
        self.assertEqual(liquids.slosh_spill_l(dict(cup, lid_on=True), 1.7), 0.0)
        self.assertEqual(liquids.slosh_spill_l(dict(cup, ice_frac=1.0), 1.7), 0.0)

    def test_pour_rate_ratios_for_honey_and_oil(self):
        water = item("kitchen", liquid="water", capacity_l=1.0, liquid_l=1.0, mouth_frac=0.3)
        self.assertAlmostEqual(liquids.pour_rate_lps(water), 0.25)
        self.assertAlmostEqual(liquids.pour_rate_lps(dict(water, mouth_frac=0.9)), 2.0)
        self.assertAlmostEqual(liquids.pour_rate_lps(dict(water, spout=True)), 0.5)
        self.assertAlmostEqual(liquids.pour_rate_lps(water) / liquids.pour_rate_lps(dict(water, liquid="honey")),
                               math.sqrt(10000 / 20.0), places=6)                          # about 22x slower
        self.assertAlmostEqual(liquids.pour_rate_lps(water) / liquids.pour_rate_lps(dict(water, liquid="oil")),
                               math.sqrt(60 / 20.0), places=6)                             # about 1.7x slower
        self.assertAlmostEqual(liquids.pour_rate_lps(dict(water, liquid="soup")), 0.25)    # under 20 cP: unslowed
        with self.assertRaises(ValueError):
            liquids.pour_rate_lps(water, opening="firehose")

    def test_hold_capacity_of_containers_and_rimmed_supports(self):
        w = world({"tray": item("kitchen", pos=[0, 0], size_m=[0.4, 0.3, 0.02], rim_m=0.02),
                   "board": item("kitchen", pos=[1, 0], size_m=[0.4, 0.3, 0.02]),
                   "jug": item("kitchen", pos=[2, 0], capacity_l=2.0),
                   "box": item("kitchen", pos=[3, 0], material="cardboard", capacity_l=20.0)})
        self.assertAlmostEqual(liquids.hold_capacity_l(w.s, "tray"), 0.4 * 0.3 * 0.02 * 1000)
        self.assertIsNone(liquids.hold_capacity_l(w.s, "board"))
        self.assertEqual(liquids.hold_capacity_l(w.s, "jug"), 2.0)
        self.assertTrue(liquids.holds_liquid(w.s, "jug"))
        self.assertFalse(liquids.holds_liquid(w.s, "box"))          # cardboard is not waterproof: it soaks
        w.s["entities"]["jug"]["perforated"] = True
        self.assertFalse(liquids.holds_liquid(w.s, "jug"))

    def test_evaporation_is_cleanings_formula(self):
        self.assertAlmostEqual(liquids.evaporation_mm_s(), 0.2 / 3600 * (1.6 - 1.4 * 0.45))
        self.assertAlmostEqual(liquids.evaporation_mm_s(31.0, 0.45) / liquids.evaporation_mm_s(), 1.4)
        self.assertAlmostEqual(liquids.evaporation_mm_s(airflow=True) / liquids.evaporation_mm_s(), 2.5)
        windy = liquids.evaporation_mm_s(outdoor=True, wind_mps=20.0, sun=True) / liquids.evaporation_mm_s()
        self.assertAlmostEqual(windy, (1.5 + 0.3 * 8.0) * 3.0)
        self.assertGreater(liquids.evaporation_mm_s(humidity=1.0), 0.0)


# ----- pouring and overflow ------------------------------------------------------------------------------------------

class PourTests(PhysTest):
    def kitchen(self, **jar):
        jar_rec = item("table", material="glass", mass_kg=0.4, size_m=[0.1, 0.1, 0.15], capacity_l=1.0, liquid_l=0.0,
                       temperature_c=21.0)
        jar_rec.update(jar)
        return world({"table": table(), "jar": jar_rec,
                      "pitcher": item("robot", capacity_l=2.0, liquid_l=2.0, liquid="water", temperature_c=21.0)},
                     robot={"room": "kitchen", "pos": [1, 1], "hands": {"right": "pitcher"}, "speed_mode": "normal"})

    def test_transfer_overflow_becomes_a_spill_where_the_destination_stands(self):
        w = self.kitchen()
        delivered, spilled = self.assertJournaled(w, liquids.transfer, w, "pitcher", "jar", 1.6, by="robot")
        self.assertEqual((delivered, spilled), (1.0, 0.6))
        self.assertEqual(w.s["entities"]["jar"]["liquid_l"], 1.0)
        self.assertAlmostEqual(w.s["entities"]["pitcher"]["liquid_l"], 0.4)
        table_film = 1.2 * 0.8 * 1.5                       # the wood table takes a 1.5 mm film (1.44 L) first
        self.assertAlmostEqual(w.s["entities"]["table"]["wetness"], 0.6 / table_film, places=4)
        self.assertEqual(puddles(w), [])
        self.assertEqual(w.s["stats"], {"phys_spills": 1, "spills": 1})
        record = self.records(w)[-1]
        self.assertEqual((record["kind"], record["target"], record["by"]), ("phys.overflow", "table", "robot"))
        self.assertEqual(record["note"], "jar overflowed")
        self.assertEqual(w.s["event_log"][-1]["kind"], "phys.overflow")
        self.assertEqual(w.s["entities"]["jar"]["contents_from"], "pitcher")

    def test_a_cavity_pools_its_pool_l_not_its_storage_capacity(self):
        """Phase-1 finish (appliances_cooking's request, INT2): an opened oven pooled a kernel pour up to its 75 L
        storage capacity_l. pool_l is what it holds before the liquid runs over its lip; 0 holds nothing."""
        w = self.kitchen(capacity_l=75.0, pool_l=1.0)
        self.assertEqual(liquids.hold_capacity_l(w.s, "jar"), 1.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 1.6), (1.0, 0.6))
        self.assertEqual(w.s["entities"]["jar"]["liquid_l"], 1.0)
        w = self.kitchen(capacity_l=75.0, pool_l=0)
        self.assertIsNone(liquids.hold_capacity_l(w.s, "jar"))
        self.assertFalse(liquids.holds_liquid(w.s, "jar"))
        self.assertEqual(liquids.hold_capacity_l(self.kitchen().s, "jar"), 1.0)        # no pool_l: capacity_l

    def test_a_domain_hook_takes_liquid_arriving_in_a_holder_first(self):
        """Phase-1 finish (outdoor_plants' request, INT2): a pour or a spill into a plant pot pooled liquid_l the
        domain's soil model never read. phys.liquid_arrives takes its share before the kernel pools the rest; pour_in
        counts it delivered, the spill walk counts it caught."""
        seen = []

        def soil(w, eid, liters, liquid, source, by):
            if eid != "jar":
                return None
            seen.append((eid, liters, liquid, source, by))
            return min(liters, 0.25)
        self.hooks.add("phys.liquid_arrives", soil)
        w = self.kitchen()
        self.assertEqual(self.assertJournaled(w, liquids.transfer, w, "pitcher", "jar", 0.5, by="robot"), (0.5, 0.0))
        self.assertAlmostEqual(w.s["entities"]["jar"]["liquid_l"], 0.25)          # the hook kept the other 0.25 L
        self.assertEqual(seen, [("jar", 0.5, "water", "pitcher", "robot")])
        w = self.kitchen()
        liquids.spill(w, 0.2, "water", onto="jar", source="pitcher", by="robot")
        self.assertEqual(w.s["entities"]["jar"]["liquid_l"], 0.0)                 # all 0.2 L taken by the hook
        self.assertEqual(puddles(w), [])
        self.assertEqual(liquids.arrives(w, "table", 1.0, "water"), 0.0)          # not the hook's: 0, nothing taken

    def test_a_wetted_hook_sees_the_wetness_before_the_arrival(self):
        """Phase-1 close-out (electrical's request): the kernel writes the new wetness before phys.wetted runs, so a
        hook adding up arrivals since a thing was last dry could not tell that it had dried in between.
        liquids.wetness_before(w, eid) is the wetness before the arrival, inside the hook (nested calls too), for
        every kernel arrival (wet, rain, a wet item's seep); None outside one."""
        seen = []

        def gear(w, eid, liters, liquid, source):
            seen.append((eid, liquids.wetness_before(w, eid), w.s["entities"][eid].get("wetness")))
            if eid == "plate":
                liquids.wet(w, "jar", 0.0001, liquid)           # an arrival inside an arrival
                seen.append(("plate after", liquids.wetness_before(w, "plate"), liquids.wetness_before(w, "jar")))
        self.hooks.add("phys.wetted", gear)
        w = world({"table": table(), "plate": item("table", size_m=[0.25, 0.25, 0.02], wetness=0.2),
                   "jar": item("table", size_m=[0.1, 0.1, 0.15], wetness=0.0)})
        liquids.wet(w, "plate", 0.005, "water")
        new = w.s["entities"]["plate"]["wetness"]
        self.assertGreater(new, 0.2)
        self.assertEqual(seen[0], ("plate", 0.2, new))
        self.assertEqual(seen[1][0], "jar")
        self.assertEqual(seen[2], ("plate after", 0.2, None))          # the inner call is over: the outer one again
        self.assertIsNone(liquids.wetness_before(w, "plate"))
        seen[:] = []
        w = yard({"letter": item("patio", pos=[1, 1], material="paper", mass_kg=0.005, size_m=[0.21, 0.297, 0.0001],
                                 wetness=0.9)})
        environment.rain_wet(w, "letter", 5.0, 30.0)
        self.assertEqual(seen, [("letter", 0.9, 1.0)])

    def test_pouring_from_a_half_frozen_bottle_leaves_the_ice_behind(self):
        """Adoption-round request (plumbing_liquids 1): _drain lowered liquid_l but kept ice_frac, so pouring the
        free half of a half-frozen bottle turned half of its ice into liquid."""
        w = self.kitchen()
        w.s["entities"]["pitcher"].update(liquid_l=0.8, ice_frac=0.5)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.6), (0.4, 0.0))      # only the free 0.4 L flows
        pitcher = w.s["entities"]["pitcher"]
        self.assertAlmostEqual(pitcher["liquid_l"], 0.4)
        self.assertEqual(pitcher["ice_frac"], 1.0)                                    # the 0.4 L of ice stays
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.1), (0.0, 0.0))
        w.s["entities"]["pitcher"].update(liquid_l=1.0, ice_frac=0.25)
        liquids.transfer(w, "pitcher", "jar", 0.5)
        self.assertEqual(w.s["entities"]["pitcher"]["ice_frac"], 0.5)                 # 0.25 L of ice in 0.5 L
        w.s["entities"]["jar"].update(liquid_l=0.0)
        w.s["entities"]["pitcher"].update(liquid_l=0.3, ice_frac=0.0)
        liquids.transfer(w, "pitcher", "jar", 0.3)
        self.assertEqual((w.s["entities"]["pitcher"]["liquid_l"], w.s["entities"]["pitcher"]["ice_frac"]), (0.0, 0.0))

    def test_overflow_past_the_film_reaches_the_floor_and_exogenous_pours_are_not_harm(self):
        w = self.kitchen(capacity_l=0.2)
        w.s["entities"]["pitcher"].update(liquid_l=2.0)
        delivered, spilled = liquids.transfer(w, "pitcher", "jar", 2.0, by="exogenous")
        self.assertEqual((delivered, spilled), (0.2, 1.8))
        [puddle] = puddles(w)
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], 1.8 - 1.2 * 0.8 * 1.5, places=4)
        self.assertEqual(w.s["entities"][puddle]["pos"], [2, 2])           # under the table's anchor cell
        self.assertEqual(w.s["stats"], {"phys_spills": 1})

    def test_pouring_mixes_temperature_by_heat_capacity_and_names_the_mixture(self):
        w = self.kitchen(liquid_l=0.2, liquid="milk", temperature_c=5.0, material="borosilicate")
        w.s["entities"]["pitcher"].update(liquid="coffee", temperature_c=80.0)
        liquids.transfer(w, "pitcher", "jar", 0.3)
        jar = w.s["entities"]["jar"]
        body, milk, coffee = 0.4 * 830, 0.2 * 1.03 * 3930, 0.3 * 1.0 * 4186
        expected = (body * 5.0 + milk * 5.0 + coffee * 80.0) / (body + milk + coffee)
        self.assertAlmostEqual(jar["temperature_c"], round(expected, 3))
        self.assertEqual(jar["liquid"], "coffee")
        self.assertAlmostEqual(jar["liquid_l"], 0.5)

    def test_boiling_soup_cracks_a_cold_soda_lime_jar_but_not_borosilicate(self):
        self.fake.lawbook.on.add("thermal_shock")
        w = self.kitchen(temperature_c=4.0)
        w.s["entities"]["pitcher"].update(liquid="soup", temperature_c=100.0)
        delivered, spilled = liquids.transfer(w, "pitcher", "jar", 0.5)
        self.assertTrue(w.s["entities"]["jar"]["broken"])                  # 96 K x 0.6 (upshock) = 58 K > 45 K
        self.assertEqual((delivered, spilled), (0.0, 0.5))
        self.assertIn(("break", "jar", "thermal_shock"), self.fake.impact.calls)
        w = self.kitchen(temperature_c=4.0, material="borosilicate")
        w.s["entities"]["pitcher"].update(liquid="soup", temperature_c=100.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.5), (0.5, 0.0))
        self.assertFalse(w.s["entities"]["jar"].get("broken"))             # 96 K < 160 K

    def test_hot_drinks_never_crack_ordinary_mugs_and_the_pour_check_waits_for_its_law(self):
        """Review finding: a pour shock-checked at coupling 1.0 cracked half the 21 degC mugs filled at 100 degC,
        and ran although thermal_shock is deferred. Hot into a colder vessel is an upshock (0.6); cold into a hot
        one stays 1.0; nothing is checked while the law is deferred."""
        w = self.kitchen(temperature_c=4.0)                                 # law deferred: no check at all
        w.s["entities"]["pitcher"].update(liquid="soup", temperature_c=100.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.5), (0.5, 0.0))
        self.assertFalse(w.s["entities"]["jar"].get("broken"))
        self.fake.lawbook.on.add("thermal_shock")
        w = self.kitchen(material="ceramic", temperature_c=21.0)            # boiling water into a mug
        w.s["entities"]["pitcher"].update(temperature_c=100.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.3), (0.3, 0.0))      # 79 K x 0.6 = 47 K < 80 K
        self.assertFalse(w.s["entities"]["jar"].get("broken"))
        w = self.kitchen(material="glass", temperature_c=21.0)              # hot coffee into a drinking glass
        w.s["entities"]["pitcher"].update(liquid="coffee", temperature_c=85.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.3), (0.3, 0.0))      # 64 K x 0.6 = 38 K < 45 K
        w = self.kitchen(material="glass", temperature_c=90.0)              # cold water into a hot glass
        w.s["entities"]["pitcher"].update(temperature_c=10.0)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.3), (0.0, 0.3))      # 80 K x 1.0 > 45 K
        self.assertTrue(w.s["entities"]["jar"]["broken"])

    def test_perforated_and_non_holding_destinations_pass_everything_on(self):
        w = self.kitchen(perforated=True)
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.5), (0.0, 0.5))
        self.assertEqual(w.s["entities"]["jar"].get("liquid_l"), 0.0)
        self.assertGreater(w.s["entities"]["table"]["wetness"], 0.0)
        w = self.kitchen(capacity_l=0.0, size_m=[0.25, 0.25, 0.02])        # a plate
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 0.05), (0.0, 0.05))
        self.assertAlmostEqual(w.s["entities"]["jar"]["wetness"], round(0.05 / (0.0625 * 1.5), 4))   # film 94 ml

    def test_provenance_follows_the_origin_and_a_faucet_pour_keeps_the_temperature(self):
        w = self.kitchen()
        w.s["entities"]["pitcher"]["contents_from"] = "carton"
        w.s["entities"]["carton"] = item("kitchen", pos=[0, 0], capacity_l=1.0)
        liquids.transfer(w, "pitcher", "jar", 0.2)
        self.assertEqual(w.s["entities"]["jar"]["contents_from"], "carton")
        liquids.transfer(w, "pitcher", "jar", 2.0)                   # emptied: the pitcher forgets its origin
        self.assertNotIn("contents_from", w.s["entities"]["pitcher"])
        w = self.kitchen(temperature_c=30.0)
        self.assertEqual(liquids.pour_in(w, "jar", 0.4, "water"), (0.4, 0.0))
        self.assertEqual(w.s["entities"]["jar"]["temperature_c"], 30.0)
        self.assertNotIn("contents_from", w.s["entities"]["jar"])

    def test_mouth_fraction_from_a_declared_opening(self):
        bottle = item("kitchen", size_m=[0.08, 0.08, 0.3], capacity_l=1.0, liquid_l=0.9, opening_m=0.02)
        self.assertAlmostEqual(liquids.mouth_frac(bottle), 0.25)
        self.assertAlmostEqual(liquids.pour_rate_lps(dict(bottle, liquid="water")), 0.25)       # a narrow neck
        self.assertAlmostEqual(liquids.tip_spill_l(bottle, "on_side"), 0.9 - 0.75 / 2)
        self.assertEqual(liquids.mouth_frac(dict(bottle, mouth_frac=1.7)), 1.0)

    def test_frozen_liquid_does_not_pour_and_nothing_pours_into_itself(self):
        w = self.kitchen()
        w.s["entities"]["pitcher"]["ice_frac"] = 0.75
        self.assertEqual(liquids.transfer(w, "pitcher", "jar", 2.0), (0.5, 0.0))
        self.assertEqual(liquids.transfer(w, "jar", "jar", 0.1), (0.0, 0.0))

    def test_overflow_law_spills_the_excess_and_returns_its_records(self):
        w = self.kitchen(liquid_l=1.3)
        records = liquids.law_overflow(w, "jar")
        self.assertEqual(w.s["entities"]["jar"]["liquid_l"], 1.0)
        self.assertEqual([r["kind"] for r in records], ["phys.overflow"])
        self.assertNotRegex(records[0]["note"], r"\d")
        self.assertEqual(liquids.law_overflow(w, "jar"), [])
        self.assertEqual(liquids.overflow(w, "table"), 0.0)                 # not a holder


# ----- spills, wetting and absorbing ---------------------------------------------------------------------------------

class SpillTests(PhysTest):
    def test_droplets_do_nothing(self):
        w = world({})
        self.assertIsNone(liquids.spill(w, 0.004, at=("kitchen", (1, 1))))
        self.assertEqual((w.s["entities"], w.s["stats"], w.s["event_log"]), ({}, {}, []))

    def test_kernel_puddle_fields_area_wet_cells_and_growth(self):
        w = world({})
        sid = self.assertJournaled(w, liquids.spill, w, 0.3, "oil", at=("kitchen", (2, 3)), by="robot")
        rec = w.s["entities"][sid]
        self.assertEqual((rec["category"], rec["liquid"], rec["floor_hazard"], rec["pos"]), ("spill", "oil", "slip",
                                                                                              [2, 3]))
        self.assertAlmostEqual(rec["area_m2"], 0.3 / 2.5)                  # V / film thickness (oil 2.5 mm)
        self.assertEqual(w.s["rooms"]["kitchen"]["wet_cells"], [[2, 3]])
        self.assertEqual(liquids.spill(w, 0.2, "oil", at=("kitchen", (2, 3))), sid)
        self.assertAlmostEqual(w.s["entities"][sid]["liquid_l"], 0.5)
        self.assertAlmostEqual(w.s["entities"][sid]["area_m2"], 0.2)
        self.assertEqual(w.s["stats"], {"phys_spills": 1, "spills": 1})    # growing a puddle is not a new spill
        other = liquids.spill(w, 0.2, "water", at=("kitchen", (2, 3)))
        self.assertNotEqual(other, sid)
        self.assertEqual(w.s["rooms"]["kitchen"]["wet_cells"], [[2, 3]])

    def test_puddles_on_carpet_soak_to_three_millimetres(self):
        w = world({}, rooms={"den": room("carpet")}, robot={"room": "den", "pos": [0, 0], "hands": {}})
        sid = liquids.spill(w, 0.3, "water", at="den")
        self.assertTrue(w.s["entities"][sid]["on_carpet"])
        self.assertAlmostEqual(w.s["entities"][sid]["area_m2"], 0.1)
        self.assertEqual(w.s["entities"][sid]["pos"], [0, 0])              # the robot's cell in its own room

    def test_spill_sink_hook_owns_the_puddle(self):
        seen = []
        self.hooks.add("phys.spill_sink", lambda w, rid, cell, liquid, liters, source, onto:
                       seen.append((rid, cell, liquid, liters)) or "domain_spill")
        w = world({})
        self.assertEqual(liquids.spill(w, 0.1, "milk", at=("kitchen", (0, 0))), "domain_spill")
        self.assertEqual(seen, [("kitchen", [0, 0], "milk", 0.1)])
        self.assertEqual(puddles(w), [])
        self.assertEqual(w.s["stats"]["phys_spills"], 1)

    def test_a_spill_sink_can_say_a_portion_grew_its_spill(self):
        """Adoption-round request (cleaning 3): a sink that returns (id, new) is counted once per new spill, not once
        per portion; a plain id still counts as new."""
        made = []

        def sink(w, rid, cell, liquid, liters, source, onto):
            new = not made
            made.append(liters)
            return ("domain_spill", new)
        self.hooks.add("phys.spill_sink", sink)
        w = world({})
        self.assertEqual(liquids.spill(w, 0.1, "milk", at=("kitchen", (0, 0))), "domain_spill")
        self.assertEqual(liquids.spill(w, 0.2, "milk", at=("kitchen", (0, 0))), "domain_spill")
        self.assertEqual(made, [0.1, 0.2])
        self.assertEqual(w.s["stats"]["phys_spills"], 1)

    def test_spill_onto_runs_down_the_support_chain(self):
        w = world({"table": table(), "saucer": item("table", material="ceramic", size_m=[0.15, 0.15, 0.02],
                                                    rim_m=0.015),
                   "cup": mug("saucer", liters=0.0)})
        saucer_cap = 0.15 * 0.15 * 0.015 * 1000
        table_film = 1.2 * 0.8 * 1.5
        sid = liquids.spill(w, 2.0, "coffee", onto="saucer", source="cup")
        self.assertAlmostEqual(w.s["entities"]["saucer"]["liquid_l"], round(saucer_cap, 4))
        self.assertEqual(w.s["entities"]["table"]["wetness"], 1.0)
        self.assertAlmostEqual(w.s["entities"][sid]["liquid_l"], 2.0 - saucer_cap - table_film, places=4)
        self.assertEqual(len([r for r in self.records(w) if r["kind"] == "phys.spill"]), 1)

    def test_wet_film_and_soak_capacities_and_the_wetted_hook(self):
        calls = []
        self.hooks.add("phys.wetted",
                       lambda w, eid, liters, liquid, source: calls.append((eid, liters, liquid, source)))
        w = world({"towel": item("kitchen", pos=[0, 0], material="cotton", mass_kg=0.5),
                   "board": item("kitchen", pos=[1, 0], size_m=[0.4, 0.25, 0.02])})
        self.assertAlmostEqual(liquids.wet(w, "towel", 2.0, "water", source="x"), 1.25)   # 2.5 L/kg x 0.5 kg
        self.assertEqual((w.s["entities"]["towel"]["wetness"], w.s["entities"]["towel"]["moisture"]), (1.0, 1.0))
        self.assertAlmostEqual(liquids.wet(w, "board", 1.0, "water"), 0.15)                # 0.1 m2 x 1.5 mm
        self.assertEqual(w.s["entities"]["board"]["wetness"], 1.0)
        self.assertNotIn("moisture", w.s["entities"]["board"])
        self.assertEqual(calls, [("towel", 2.0, "water", "x"), ("board", 1.0, "water", None)])

    def test_finished_wood_takes_a_film_rather_than_soaking(self):
        w = world({"table": table()})
        self.assertEqual(liquids.absorb_capacity_l(self.fake.props.props(w.s, "table")), 0.0)
        self.assertAlmostEqual(liquids.wet(w, "table", 0.1, "water"), 0.1)
        self.assertAlmostEqual(w.s["entities"]["table"]["wetness"], 0.1 / 1.44, places=4)

    def test_a_towel_absorbs_at_most_two_and_a_half_litres_per_kilogram(self):
        w = world({"towel": item("robot", material="cotton", mass_kg=0.5)},
                  robot={"room": "kitchen", "pos": [1, 1], "hands": {"right": "towel"}})
        sid = liquids.spill(w, 3.0, "water", at=("kitchen", (2, 2)))
        self.assertAlmostEqual(self.assertJournaled(w, liquids.absorb, w, "towel", sid), 1.25)
        self.assertAlmostEqual(w.s["entities"][sid]["liquid_l"], 1.75)
        self.assertEqual(w.s["entities"]["towel"]["wetness"], 1.0)
        self.assertEqual(liquids.absorb(w, "towel", sid), 0.0)
        w.s["entities"]["towel"]["wetness"] = 0.0
        self.assertAlmostEqual(liquids.absorb(w, "towel", liters=0.4), 0.4)    # an amount with no source

    def test_soaking_up_a_puddle_removes_it_and_its_wet_cell(self):
        w = world({"sponge": item("kitchen", pos=[0, 0], material="foam", mass_kg=0.05)})
        sid = liquids.spill(w, 0.3, "water", at=("kitchen", (2, 2)))
        self.assertAlmostEqual(liquids.absorb(w, "sponge", sid), 0.3)
        self.assertNotIn(sid, w.s["entities"])
        self.assertEqual(w.s["rooms"]["kitchen"]["wet_cells"], [])
        self.assertEqual(liquids.absorb(w, "sponge", liters=1.0), 0.2)     # 10 L/kg x 50 g, 0.3 already held


# ----- open spills, drips and leaks ----------------------------------------------------------------------------------

class OpenSpillTests(PhysTest):
    def test_a_tipped_open_cup_spills_onto_its_support_and_a_capped_bottle_does_not(self):
        w = world({"table": table(), "cup": mug("table", liters=0.3, orientation="on_side"),
                   "bottle": item("table", capacity_l=1.0, liquid_l=1.0, orientation="on_side",
                                  cap={"on": True})})
        records = liquids.law_open_spill(w, "cup")
        self.assertEqual(w.s["entities"]["cup"]["liquid_l"], 0.0)
        self.assertAlmostEqual(w.s["entities"]["table"]["wetness"], 0.3 / 1.44, places=4)
        self.assertEqual([(r["kind"], r["target"], r["note"]) for r in records],
                         [("phys.spill", "table", "cup spilled")])
        self.assertEqual(liquids.law_open_spill(w, "bottle"), [])

    def test_a_tilted_tray_spills_from_the_cups_on_it(self):
        w = world({"tray": item("kitchen", pos=[0, 0], size_m=[0.5, 0.35, 0.03], rim_m=0.02, tilt_deg=40.0),
                   "cup": mug("tray", liters=0.3)})
        lost = liquids.tilt_spill_l(w.s["entities"]["cup"], 40.0)
        self.assertGreater(lost, 0.0)
        liquids.law_open_spill(w, "cup")
        self.assertAlmostEqual(w.s["entities"]["cup"]["liquid_l"], round(0.3 - lost, 4))
        self.assertAlmostEqual(w.s["entities"]["tray"]["liquid_l"], lost)   # the rim pools it

    def umbrella(self, support, **fields):
        return item(support, material="film", mass_kg=0.4, size_m=[0.1, 0.1, 0.8], **fields)

    def test_a_drip_on_a_tray_pools_and_then_overflows_its_rim(self):
        w = world({"tray": item("kitchen", pos=[2, 2], size_m=[0.4, 0.3, 0.02], rim_m=0.02),
                   "umbrella": self.umbrella("tray")})
        self.assertTrue(liquids.drip_start(w, "umbrella", 12.0, until_l=3.0))
        self.assertTrue(w.s["entities"]["umbrella"]["dripping"])
        advance(w, 600)
        self.assertAlmostEqual(w.s["entities"]["tray"]["liquid_l"], 2.0)   # 12 L/h for 10 min, all pooled
        self.assertEqual(puddles(w), [])
        advance(w, 600)
        self.assertAlmostEqual(w.s["entities"]["tray"]["liquid_l"], 2.4)   # rim capacity 0.12 m2 x 2 cm
        [puddle] = puddles(w)
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], 0.6, places=3)
        self.assertEqual(w.s["entities"][puddle]["pos"], [2, 2])
        self.assertFalse(processes.is_active(w.s, "dripping", "umbrella"))  # the 3 L budget is spent
        self.assertNotIn("dripping", w.s["entities"]["umbrella"])
        self.assertNotIn("_phys_dripping", w.s["entities"]["umbrella"])
        self.assertEqual(w.s["stats"], {"phys_spills": 1})                 # one new puddle, not one per portion

    def test_a_drip_on_a_towel_passes_through_once_it_is_saturated(self):
        w = world({"towel": item("kitchen", pos=[1, 2], material="cotton", mass_kg=0.2, size_m=[0.5, 0.3, 0.01]),
                   "umbrella": self.umbrella("towel")})
        liquids.drip_start(w, "umbrella", 6.0, until_l=1.0)
        advance(w, 300)
        self.assertAlmostEqual(w.s["entities"]["towel"]["wetness"], 1.0)   # 0.5 L in 5 min: just saturated
        self.assertEqual(puddles(w), [])
        advance(w, 300)
        [puddle] = puddles(w)
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], 0.5, places=3)

    def test_a_drip_on_wood_leaves_a_ring_after_ten_minutes(self):
        w = world({"table": table(), "pot": item("table", material="ceramic", size_m=[0.2, 0.2, 0.2],
                                                 capacity_l=2.0, liquid_l=1.5, liquid="water")})
        liquids.drip_start(w, "pot", 0.2)
        self.assertEqual(processes.queued(w.s, "wet_contact", "pot")[0][0], 600.0)   # wet from the first drop
        advance(w, 570)
        self.assertNotIn("damage", w.s["entities"]["table"])
        advance(w, 60)
        self.assertEqual(w.s["entities"]["table"]["damage"], {"ring_stain": 0.3})
        self.assertEqual([r["kind"] for r in self.records(w)], ["phys.ring"])
        advance(w, 600)
        self.assertEqual(w.s["entities"]["table"]["damage"], {"ring_stain": 0.5})   # it deepens while still wet
        released = 0.2 * 1230 / 3600
        pending = processes.params(w.s, "dripping", "pot")["buf_l"]
        self.assertAlmostEqual(1.5 - w.s["entities"]["pot"]["liquid_l"] + pending, released, places=6)
        self.assertAlmostEqual(w.s["entities"]["table"]["wetness"] * 1.2 * 0.8 * 1.5, released - pending, delta=0.001)

    def test_drips_never_clobber_a_domains_dripping_flag_and_need_water(self):
        w = world({"faucet": item("kitchen", pos=[0, 0], dripping=True, wetness=1.0, size_m=[0.3, 0.3, 0.1])})
        self.assertTrue(liquids.drip_start(w, "faucet", 1.0))
        liquids.drip_stop(w, "faucet")
        self.assertTrue(w.s["entities"]["faucet"]["dripping"])
        w = world({"dry": item("kitchen", pos=[0, 0])})
        self.assertFalse(liquids.drip_start(w, "dry", 1.0))
        self.assertFalse(liquids.drip_start(w, "dry", 0.0, until_l=1.0))
        self.assertIsNone(processes.section(w.s))

    def test_a_wet_item_drips_until_only_damp(self):
        w = world({"coat": item("kitchen", pos=[1, 1], material="cotton", mass_kg=1.0, wetness=0.9)})
        liquids.drip_start(w, "coat", 2.0)
        advance(w, 1800)
        self.assertAlmostEqual(w.s["entities"]["coat"]["wetness"], 0.5)     # half its 2 L shed after an hour's half
        advance(w, 1830)
        self.assertAlmostEqual(w.s["entities"]["coat"]["wetness"], liquids.DAMP_WETNESS)
        [puddle] = puddles(w)
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], 2.0, places=3)   # (0.9 - 0.1) x 2.5 L
        self.assertFalse(processes.is_active(w.s, "dripping", "coat"))

    def test_a_leak_empties_a_cracked_container_and_needs_liquid(self):
        w = world({"jug": item("kitchen", pos=[3, 3], capacity_l=2.0, liquid_l=0.3, liquid="water")})
        self.assertTrue(liquids.leak_start(w, "jug", l_per_h=0.6))
        self.assertTrue(w.s["entities"]["jug"]["leaking"])
        advance(w, 1800)
        self.assertEqual(w.s["entities"]["jug"]["liquid_l"], 0.0)
        self.assertNotIn("leaking", w.s["entities"]["jug"])
        [puddle] = puddles(w)
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], 0.3, places=4)
        self.assertFalse(liquids.leak_start(w, "jug"))

    def test_a_drip_or_leak_started_by_the_robot_is_the_robots(self):
        """Adoption request outdoor_plants 2: a drip had no `by`, so a pot the robot over-watered drained as an
        exogenous drip (watering is no structural change the settle marks). drip_start / leak_start take by= and
        every portion they deposit carries it; without it, the per-portion rule stays (exogenous here)."""
        for by, robots in ((None, 0), ("robot", 1), ("exogenous", 0)):
            w = world({"pot": item("kitchen", pos=[2, 2], material="ceramic", size_m=[0.2, 0.2, 0.2], capacity_l=2.0,
                                   liquid_l=0.5, liquid="water"),
                       "jug": item("kitchen", pos=[4, 4], capacity_l=2.0, liquid_l=0.3, liquid="water")})
            self.assertTrue(liquids.drip_start(w, "pot", 6.0, by=by))
            self.assertTrue(liquids.leak_start(w, "jug", l_per_h=0.6, by=by))
            advance(w, 1800)
            self.assertEqual(len(puddles(w)), 2)
            self.assertEqual(w.s["stats"].get("phys_spills"), 2)
            self.assertEqual(w.s["stats"].get("spills", 0), 2 * robots, by)
            self.assertEqual(sorted({r["by"] for r in self.records(w) if r["kind"] == "phys.spill"}),
                             ["robot" if robots else "exogenous"])


# ----- carry dynamics ------------------------------------------------------------------------------------------------

class CarryTests(PhysTest):
    def carrying(self, holder, *riders, **robot):
        entities = {"held": holder}
        entities.update(riders)
        bot = {"room": "kitchen", "pos": [1, 2], "hands": {"right": "held"}, "speed_mode": "normal"}
        bot.update(robot)
        return world(entities, robot=bot)

    def test_a_full_mug_spills_at_fast_carry_speed_and_not_careful(self):
        w = self.carrying(mug("robot", liters=0.32))
        self.assertEqual(liquids.carry(w, {"speed": "careful"}), [])
        self.assertEqual(w.s["entities"]["held"]["liquid_l"], 0.32)
        records = self.assertJournaled(w, liquids.carry, w, {"speed": "fast"})
        lost = 0.32 - w.s["entities"]["held"]["liquid_l"]
        self.assertGreater(lost, liquids.droplet_l())
        [puddle] = puddles(w)
        self.assertEqual(w.s["entities"][puddle]["pos"], [1, 2])          # the robot's cell
        self.assertAlmostEqual(w.s["entities"][puddle]["liquid_l"], lost)
        self.assertEqual([(r["kind"], r["by"]) for r in records], [("phys.spill", "robot")])
        self.assertEqual(w.s["stats"]["spills"], 1)

    def test_carry_motion_hooks_multiply_the_gait_factor(self):
        self.hooks.add("phys.carry_motion", lambda w, travel: 1.8 if travel.get("stairs_up") else 1.0)
        w = self.carrying(mug("robot"))
        self.assertAlmostEqual(liquids.carry_motion(w, {"speed": "normal", "stairs_up": 12}), 1.8)
        self.assertAlmostEqual(liquids.carry_motion(w, {}), 1.0)            # the robot's speed_mode
        w.s["robot"]["speed_mode"] = "fast"
        self.assertAlmostEqual(liquids.carry_motion(w, {}), 1.7)

    def test_an_upright_tall_item_on_a_carried_board_tips_and_a_rolling_one_falls(self):
        board = item("robot", size_m=[0.5, 0.35, 0.02], material="wood")
        vase = item("held", size_m=[0.06, 0.06, 0.3], spos=[0.1, 0.1])          # threshold 2.8 x 0.06 / 0.3 = 0.56
        bottle = item("held", size_m=[0.07, 0.07, 0.3], shape="cylinder", spos=[0.3, 0.1])
        plate = item("held", size_m=[0.25, 0.25, 0.02], spos=[0.0, 0.1])
        w = self.carrying(board, ("vase", vase), ("bottle", bottle), ("plate", plate))
        liquids.carry(w, {"speed": "normal"})
        self.assertEqual(w.s["entities"]["vase"]["orientation"], "on_side")
        self.assertIn(("fall", "bottle", "carry", 0.8, ("kitchen", (1, 2))),   # support.CARRY hand_z_m
                      self.fake.impact.calls)
        self.assertEqual(w.s["entities"]["bottle"]["location"], "kitchen")
        self.assertEqual(w.s["entities"]["vase"]["location"], "held")    # a box shape does not roll
        self.assertNotIn("orientation", w.s["entities"]["plate"])        # flat and wide: stays

    def test_a_rimmed_tray_keeps_what_tips_and_pools_what_sloshes(self):
        tray = item("robot", size_m=[0.5, 0.35, 0.03], rim_m=0.02)
        glass = item("held", size_m=[0.07, 0.07, 0.15], shape="cylinder", capacity_l=0.4, liquid_l=0.39,
                     liquid="water", inner_size_m=[0.065, 0.065, 0.14])
        secured = item("held", size_m=[0.05, 0.05, 0.4], secured=True)
        w = self.carrying(tray, ("glass", glass), ("candle", secured))
        liquids.carry(w, {"speed": "fast"})
        self.assertEqual(w.s["entities"]["glass"]["orientation"], "on_side")    # 2.8 x 0.07 / 0.15 = 1.31 < 1.7
        self.assertEqual(w.s["entities"]["glass"]["location"], "held")          # the rim keeps it on the tray
        self.assertGreater(w.s["entities"]["held"]["liquid_l"], 0.0)
        self.assertNotIn("orientation", w.s["entities"]["candle"])
        self.assertEqual(puddles(w), [])

    def test_no_travel_or_nothing_held_does_nothing(self):
        w = self.carrying(mug("robot"))
        self.assertEqual(liquids.carry(w, None), [])
        w.s["robot"]["hands"] = {}
        self.assertEqual(liquids.carry(w, {"speed": "fast"}), [])
        self.assertEqual(w.s["entities"]["held"]["liquid_l"], 0.32)


# ----- the process queue ---------------------------------------------------------------------------------------------

class ProcessTests(PhysTest):
    def test_no_section_until_the_first_process_and_idle_runs_change_nothing(self):
        w = world({"jar": item("kitchen", pos=[0, 0])})
        before = clone(w.s)
        processes.run(w, 30.0, 0)
        self.assertEqual(w.s, before)
        self.assertEqual(w.journal_keys(), [])

    def test_the_queue_fires_in_due_then_sequence_order(self):
        w = world({"a": item("kitchen", pos=[0, 0]), "b": item("kitchen", pos=[1, 0])})
        self.assertEqual(processes.queue_add(w, 60, "creep", "b", slot=["surface"]), 0)
        processes.queue_add(w, 30, "creep", "a", slot=["shelf", 1])
        processes.queue_add(w, 60, "creep", "a", slot=["surface"])
        self.assertEqual([e[:4] for e in w.s["physics"]["queue"]],
                         [[30.0, 1, "creep", "a"], [60.0, 0, "creep", "b"], [60.0, 2, "creep", "a"]])
        self.assertEqual(len(processes.queued(w.s, "creep", "a")), 2)
        advance(w, 30)
        self.assertEqual(self.fake.impact.calls, [("creep", "a", {"slot": ["shelf", 1]})])
        advance(w, 30)
        self.assertEqual([c[1] for c in self.fake.impact.calls], ["a", "b", "a"])
        self.assertEqual(w.s["physics"]["queue"], [])

    def test_cancel_unknown_kinds_and_destroyed_entities(self):
        w = world({"a": item("kitchen", pos=[0, 0])})
        processes.queue_add(w, 10, "creep", "a")
        self.assertEqual(processes.cancel(w, "creep", "a"), 1)
        self.assertEqual(processes.cancel(w, "creep", "a"), 0)
        with self.assertRaises(ValueError):
            processes.queue_add(w, 10, "teleport", "a")
        with self.assertRaises(ValueError):
            processes.start(w, "teleport", "a")
        processes.queue_add(w, 10, "creep", "a")
        w.destroy("a")
        advance(w, 30)
        self.assertEqual(self.fake.impact.calls, [])

    def test_a_cascade_of_due_checks_is_capped_per_substep(self):
        w = world({"a": item("kitchen", pos=[0, 0])})
        for i in range(processes.MAX_DUE_PER_RUN + 10):
            processes.queue_add(w, 1, "creep", "a")
        advance(w, 30)
        self.assertEqual(len(self.fake.impact.calls), processes.MAX_DUE_PER_RUN)
        advance(w, 30)
        self.assertEqual(len(self.fake.impact.calls), processes.MAX_DUE_PER_RUN + 10)

    def test_active_processes_end_when_their_entity_is_gone(self):
        w = world({"jug": item("kitchen", pos=[0, 0], capacity_l=1.0, liquid_l=1.0)})
        liquids.leak_start(w, "jug")
        self.assertEqual(processes.active(w.s, "leaking"), ("jug",))
        w.destroy("jug")
        advance(w, 30)
        self.assertEqual(processes.active(w.s, "leaking"), ())
        self.assertEqual(w.s["physics"]["active"], {})
        self.assertFalse(processes.stop(w, "leaking", "jug"))

    def test_process_writes_are_journaled(self):
        w = world({"table": table(), "pot": item("table", capacity_l=2.0, liquid_l=1.5)})
        self.assertJournaled(w, liquids.drip_start, w, "pot", 1.0)
        w.s["time_s"] = 700.0
        self.assertJournaled(w, processes.run, w, 30.0, 0)


class RollingTests(PhysTest):
    def test_a_ball_on_a_tilted_tray_rolls_to_the_edge_and_falls_off(self):
        tray = item("table", size_m=[0.5, 0.4, 0.02], spos=[0.1, 0.1], tilt_deg=10.0, rim_m=0.0)
        ball = item("tray", shape="sphere", size_m=[0.08, 0.08, 0.08], spos=[0.21, 0.16])
        w = world({"table": table(), "tray": tray, "ball": ball})
        self.assertTrue(processes.start(w, "rolling", "ball"))
        self.assertEqual(w.s["entities"]["ball"]["in_motion"], "rolling")
        p = processes.params(w.s, "rolling", "ball")
        accel = 5.0 / 7.0 * G * (math.sin(math.radians(10)) - 0.01 * math.cos(math.radians(10)))
        self.assertAlmostEqual(p["a"], round(accel, 4))
        self.assertAlmostEqual(p["d"], 0.2)                                 # its centre is 0.2 m from the near edge
        advance(w, 0.5, step=0.5)
        self.assertTrue(processes.is_active(w.s, "rolling", "ball"))
        advance(w, 1.0, step=1.0)
        self.assertFalse(processes.is_active(w.s, "rolling", "ball"))
        self.assertEqual(w.s["entities"]["ball"]["location"], "kitchen")
        self.assertNotIn("in_motion", w.s["entities"]["ball"])
        event = [e for e in w.s["event_log"] if e["kind"] == "phys.roll"][-1]
        self.assertEqual((event["ended"], event["runaway"]), ("fell_off", False))
        self.assertAlmostEqual(event["speed_mps"], math.sqrt(2 * accel * 0.2), places=3)   # 0.68 m/s: not a runaway
        self.assertIn(("fall", "ball", "rolled_off", None, None), self.fake.impact.calls)

    def test_a_cart_on_a_sloped_driveway_runs_away_to_the_downhill_edge(self):
        w = world({"cart": item("drive", pos=[2, 1], casters=True, mass_kg=12.0, size_m=[0.6, 0.4, 0.9])},
                  rooms={"drive": room("concrete", size=(4.0, 8.0), slope_deg=4.0, downhill="south", outdoor=True)},
                  robot={"room": "drive", "pos": [0, 0], "hands": {}})
        self.assertTrue(processes.start(w, "rolling", "cart"))
        self.assertAlmostEqual(processes.params(w.s, "rolling", "cart")["d"], (15 - 1 + 0.5) * 0.5)
        advance(w, 30)
        self.assertEqual(w.s["entities"]["cart"]["pos"], [2, 15])
        event = [e for e in w.s["event_log"] if e["kind"] == "phys.roll"][-1]
        self.assertEqual((event["ended"], event["runaway"]), ("floor_edge", True))

    def test_a_floor_roll_follows_the_rooms_tilt_direction(self):
        w = world({"ball": item("drive", pos=[1, 3], shape="sphere", size_m=[0.2, 0.2, 0.2])},
                  rooms={"drive": room("concrete", size=(6.0, 4.0), slope_deg=5.0, tilt_dir_deg=0.0, outdoor=True)},
                  robot={"room": "drive", "pos": [0, 0], "hands": {}})
        self.assertEqual(processes.downhill_deg(w.s, "ball"), 0.0)
        self.assertTrue(processes.start(w, "rolling", "ball"))
        self.assertAlmostEqual(processes.params(w.s, "rolling", "ball")["d"], (11 - 1 + 0.5) * 0.5)
        advance(w, 30)
        self.assertEqual(w.s["entities"]["ball"]["pos"], [11, 3])
        [record] = self.records(w)
        self.assertEqual((record["kind"], record["note"], record["runaway"]), ("phys.roll", "ball rolled away", True))

    def test_items_stay_put_below_rolling_resistance_or_when_chocked(self):
        w = world({"ball": item("lawn", pos=[1, 1], shape="sphere"),
                   "cart": item("lawn", pos=[2, 1], casters=True, chocked=True)},
                  rooms={"lawn": room("grass", slope_deg=4.0, outdoor=True)},
                  robot={"room": "lawn", "pos": [0, 0], "hands": {}})
        self.assertFalse(processes.start(w, "rolling", "ball"))           # tan 4 deg = 0.07 < grass 0.10
        self.assertFalse(processes.start(w, "rolling", "cart"))
        self.assertIsNone(processes.section(w.s))


# ----- wind and rain -------------------------------------------------------------------------------------------------

def yard(entities, weather=None, **extra):
    return world(entities, rooms={"patio": room("concrete", size=(6.0, 6.0), outdoor=True, **extra),
                                  "porch": room("wood", outdoor=True, type="porch"),
                                  "kitchen": room()},
                 robot={"room": "kitchen", "pos": [0, 0], "hands": {}}, weather=weather)


def napkin(location, **fields):
    """A paper napkin: 4 g, 40 x 40 cm, 2 mm folded (lifts at about 3.5 m/s)."""
    rec = {"material": "paper", "mass_kg": 0.004, "size_m": [0.4, 0.4, 0.002]}
    rec.update(fields)
    return item(location, **rec)


class WindTests(PhysTest):
    def test_the_wind_gates_are_public(self):
        """Phase-1 finish (outdoor_plants' request, INT2): the domain's deferred wind copy gates on the kernel's own
        bounds; they are public now (the private names stay for older callers)."""
        self.assertIs(environment._cannot_move, environment.cannot_move)
        self.assertIs(environment._free_to_move, environment.free_to_move)
        w = yard({"chair": item("patio", pos=[1, 1], size_m=[0.55, 0.55, 0.85], mass_kg=4.0),
                  "fence": item("patio", pos=[0, 0], portable=False, category="fence", size_m=[1.0, 0.1, 1.8])})
        self.assertTrue(environment.free_to_move(w.s, "chair"))
        self.assertFalse(environment.free_to_move(w.s, "fence"))
        chair = w.s["entities"]["chair"]
        self.assertTrue(environment.cannot_move(chair, 1.0))
        self.assertFalse(environment.cannot_move(chair, 30.0))

    def test_direction_and_speed_from_the_weather(self):
        self.assertAlmostEqual(environment.wind_direction_deg({"wind_from_deg": 270}), 0.0)    # from west -> east
        self.assertAlmostEqual(environment.wind_direction_deg({"wind_from_deg": 0}), 90.0)     # from north -> south
        self.assertAlmostEqual(environment.wind_direction_deg({}), 0.0)
        self.assertEqual(environment.wind_speed_mps({"wind_mps": 4.0, "gust_mps": 7.5}), 7.5)
        self.assertEqual(environment.wind_speed_mps(None), 0.0)
        self.assertEqual(environment.rain_mm_h({"rain_mm_h": -1}), 0.0)

    def test_drag_lift_and_lift_speeds(self):
        chair = item("patio", size_m=[0.55, 0.55, 0.85])
        self.assertAlmostEqual(environment.wind_force_n(chair, 10.0), 0.5 * 1.2 * 1.2 * 0.55 * 0.85 * 100)
        self.assertAlmostEqual(environment.lift_speed_mps(napkin("patio")), 3.52, places=2)
        plate = item("patio", material="paper", mass_kg=0.013, size_m=[0.23, 0.23, 0.02])
        self.assertAlmostEqual(environment.lift_speed_mps(plate), 3.6, delta=0.1)
        self.assertEqual(environment.lift_speed_mps(chair), math.inf)
        self.assertEqual(environment.lift_speed_mps(dict(chair, wind_lift_mps=6.0)), 6.0)
        self.assertAlmostEqual(environment.lift_force_n(napkin("patio"), 3.52), 0.004 * G, places=4)
        on_side = item("patio", size_m=[0.1, 0.1, 0.4], orientation="on_side")
        self.assertAlmostEqual(environment.frontal_area_m2(on_side), 0.4 * 0.1)

    def test_fixtures_and_grounds_never_blow_away(self):
        """Review probe: wind_effect moved anything not explicitly fixed: on generated yards a floor slab slid and a
        fence tipped at 1 m/s, a lawn lifted, doors and gates tipped, walkways and hedges slid. Only portable things,
        things on wheels, free-standing furniture and covers move (statics.free_standing's rule)."""
        w = yard({"fence": item("patio", pos=[0, 0], portable=False, category="fence", size_m=[1.0, 0.1, 1.8]),
                  "lawn": item("patio", pos=[1, 1], portable=False, ground_layer=True, size_m=[3.0, 3.0, 0.05]),
                  "gate": item("patio", pos=[2, 0], portable=False, connects=["patio", "yard"],
                               size_m=[1.0, 0.05, 1.2]),
                  "hedge": item("patio", pos=[3, 3], portable=False, category="plant", mass_kg=25.0,
                                size_m=[2.0, 0.8, 1.6]),
                  "shrub": item("patio", pos=[4, 4], portable=True, rooted=True, size_m=[1.0, 1.0, 1.2]),
                  "bench": item("patio", pos=[1, 3], portable=False, category="furniture", mass_kg=3.0,
                                size_m=[0.55, 0.55, 0.85]),
                  "tarp": item("patio", pos=[2, 2], portable=False, category="cover", mass_kg=0.5,
                               size_m=[2.0, 2.0, 0.002])})
        for eid in ("fence", "lawn", "gate", "hedge", "shrub"):
            self.assertIsNone(environment.wind_effect(w, eid, 25.0, 0.0)["effect"], eid)
        self.assertEqual(environment.wind_effect(w, "bench", 25.0, 0.0)["effect"], "slide")   # free-standing
        self.assertEqual(environment.wind_effect(w, "tarp", 25.0, 90.0)["effect"], "lift")     # a cover

    def test_the_wind_at_an_item_follows_its_height_and_shelter(self):
        """Review finding: items felt the 10 m airport gust (1.5 x the mean) as a sustained force. At about 1 m in a
        suburban yard the mean is 0.4 of the 10 m open-terrain value and the gust 0.6 of the 10 m gust (ASCE 7
        exposure B against C); an elevated balcony 0.6 and 0.75; a room may state its own."""
        w = world({"chair": item("patio", pos=[1, 1]), "pot": item("balcony", pos=[1, 1]),
                   "lid": item("roof", pos=[1, 1])},
                  rooms={"patio": room(outdoor=True, type="patio"), "balcony": room(outdoor=True, type="balcony"),
                         "roof": room(outdoor=True, type="roof_terrace", wind_exposure=[0.7, 0.85])},
                  weather={"wind_mps": 10.0, "gust_mps": 15.0})
        self.assertEqual(environment.object_wind_mps(w.s, "chair"), (4.0, 9.0))
        self.assertEqual(environment.object_wind_mps(w.s, "pot"), (6.0, 11.25))
        sustained, gust = environment.object_wind_mps(w.s, "lid")
        self.assertAlmostEqual(sustained, 7.0)
        self.assertAlmostEqual(gust, 12.75)
        self.assertEqual(environment.object_wind_mps(w.s, "chair", {"wind_mps": 10.0}), (4.0, 9.0))  # 1.5 x mean
        self.assertEqual(environment.object_wind_mps(w.s, "chair", {}), (0.0, 0.0))

    def test_a_gust_lifts_or_tips_but_only_a_sustained_wind_slides(self):
        """A 3 s gust lifts a sheet off or tips a tall narrow thing over; a low box only slides under a sustained
        wind (a gust's brief shove is not modelled). Without a gust the behaviour is the single-speed rule."""
        w = yard({"box": item("patio", pos=[1, 1], mass_kg=1.0, size_m=[0.4, 0.3, 0.3]),
                  "pole": item("patio", pos=[3, 3], mass_kg=1.0, size_m=[0.1, 0.1, 1.0]),
                  "napkin": napkin("patio", pos=[4, 1])})
        slide_at = math.sqrt(0.45 * 1.0 * G / (0.5 * 1.2 * 1.2 * 0.4 * 0.3))       # about 7.8 m/s
        self.assertIsNone(environment.wind_effect(w, "box", 0.8 * slide_at, 0.0, gust_mps=2 * slide_at)["effect"])
        self.assertEqual(environment.wind_effect(w, "box", 1.1 * slide_at, 0.0, gust_mps=2 * slide_at)["effect"],
                         "slide")
        self.assertEqual(environment.wind_effect(w, "pole", 2.0, 0.0, gust_mps=9.0)["effect"], "tip")
        self.assertEqual(environment.wind_effect(w, "napkin", 1.0, 0.0, gust_mps=4.0)["effect"], "lift")
        self.assertIsNone(environment.wind_effect(w, "napkin", 1.0, 0.0, gust_mps=3.0)["effect"])

    def test_the_system_moves_free_standing_furniture_and_never_fixtures(self):
        """Review finding: run() only considered portable items and covers, so an outdoor chair or a wheeled bin
        never moved however strong the wind, while wind_effect (physics.wind_gust) moved fences and lawns. Both now
        use one rule. A storm (10 m mean 25 m/s, gusts 37.5) slides a 3 kg chair at 10 m/s at the chair."""
        self.fake.lawbook.on.add("wind")
        w = yard({"chair": item("patio", pos=[1, 1], portable=False, category="furniture", mass_kg=3.0,
                                size_m=[0.55, 0.55, 0.85], drag_cd=0.7),
                  "fence": item("patio", pos=[0, 5], portable=False, category="fence", size_m=[1.0, 0.1, 1.8]),
                  "lawn": item("patio", pos=[3, 3], portable=False, ground_layer=True, size_m=[2.0, 2.0, 0.05])},
                 weather={"wind_mps": 25.0, "gust_mps": 37.5, "wind_from_deg": 270})
        environment.run(w, 30.0, 0)
        self.assertTrue(w.s["entities"]["chair"].get("blown_away"))
        self.assertEqual((w.s["entities"]["fence"]["pos"], w.s["entities"]["lawn"]["pos"]), ([0, 5], [3, 3]))
        calm = yard({"chair": item("patio", pos=[1, 1], portable=False, category="furniture", mass_kg=3.0,
                                   size_m=[0.55, 0.55, 0.85], drag_cd=0.7)},
                    weather={"wind_mps": 8.0, "gust_mps": 12.0, "wind_from_deg": 270})
        environment.run(calm, 30.0, 0)                                              # a breezy day: it stays
        self.assertFalse(calm.s["entities"]["chair"].get("blown_away"))

    def test_a_napkin_blows_at_three_and_a_half_metres_a_second_and_a_weighted_one_does_not(self):
        w = yard({"table": table("patio", pos=[1, 1]), "napkin": napkin("table", spos=[0.2, 0.2]),
                  "held_down": napkin("table", spos=[0.7, 0.2]),
                  "stone": item("held_down", material="stone", mass_kg=0.2, size_m=[0.08, 0.08, 0.05])})
        self.assertIsNone(environment.wind_effect(w, "napkin", 2.8, 0.0)["effect"])
        # near its lift speed it presses on the table so lightly that it skids off from about 3.0 m/s
        # (manipulation's napkins go at 3 m/s); from 3.5 m/s it lifts
        result = self.assertJournaled(w, environment.wind_effect, w, "napkin", 3.6, 0.0)
        self.assertEqual(result["effect"], "lift")
        self.assertEqual(w.s["entities"]["napkin"]["location"], "patio")   # blown off the table (impact.fall)
        self.assertTrue(w.s["entities"]["napkin"]["blown_away"])
        self.assertGreater(result["moved_to"][1][0], 1)                    # downwind: east
        self.assertIsNone(environment.wind_effect(w, "held_down", 6.0, 0.0)["effect"])
        self.assertEqual(w.s["stats"], {"phys_blown": 1})
        self.assertEqual([r["kind"] for r in self.records(w)], ["phys.blown"])

    def test_a_three_kilo_chair_slides_at_about_nine_metres_a_second(self):
        # an open-frame chair: drag coefficient about 0.7 over its 0.55 x 0.85 m outline (Cd x A about 0.33 m2)
        chair = item("patio", pos=[1, 3], mass_kg=3.0, size_m=[0.55, 0.55, 0.85], drag_cd=0.7, friction_mu=0.55)
        w = yard({"chair": chair})
        self.assertIsNone(environment.wind_effect(w, "chair", 8.5, 0.0)["effect"])
        result = environment.wind_effect(w, "chair", 9.5, 0.0)
        self.assertEqual(result["effect"], "slide")
        self.assertEqual(w.s["entities"]["chair"]["pos"][1], 3)
        self.assertGreater(w.s["entities"]["chair"]["pos"][0], 1)
        weight = 3.0 * G
        self.assertLess(0.55 * weight, weight * 0.55 / 0.85)                 # it slides before it tips

    def test_tall_light_items_tip_and_secured_ones_hold(self):
        w = yard({"sign": item("patio", pos=[2, 2], mass_kg=1.0, size_m=[0.5, 0.05, 0.8], friction_mu=0.9),
                  "tied": item("patio", pos=[3, 2], mass_kg=1.0, size_m=[0.5, 0.05, 0.8], friction_mu=0.9,
                               secured=True)})
        self.assertEqual(environment.wind_effect(w, "sign", 4.0, 90.0)["effect"], "tip")
        self.assertEqual(w.s["entities"]["sign"]["orientation"], "on_side")
        self.assertIsNone(environment.wind_effect(w, "tied", 4.0, 90.0)["effect"])

    def test_held_hung_or_inside_items_do_not_move(self):
        w = yard({"box": item("patio", pos=[1, 1], capacity_l=10, accepts_items=True),
                  "inside": napkin("box", relation="in"), "flag": napkin("box", relation="hung")})
        for eid in ("inside", "flag"):
            self.assertIsNone(environment.wind_effect(w, eid, 20.0, 0.0)["effect"])

    def test_a_blown_item_keeps_going_until_it_rests_against_the_downwind_edge(self):
        w = yard({"napkin": napkin("patio", pos=[9, 4])})
        self.assertEqual(environment.wind_effect(w, "napkin", 5.0, 0.0)["effect"], "lift")
        self.assertEqual(w.s["entities"]["napkin"]["pos"], [11, 4])
        self.assertIsNone(environment.wind_effect(w, "napkin", 5.0, 0.0)["effect"])
        self.assertEqual(w.s["stats"], {"phys_blown": 1})

    def test_exposure_roofs_shelters_and_hooks(self):
        w = yard({"napkin": napkin("patio", pos=[0, 0]), "porch_napkin": napkin("porch", pos=[0, 0]),
                  "indoor": napkin("kitchen", pos=[0, 0]),
                  "bin": item("patio", pos=[2, 2], capacity_l=40, openable=True, open=False),
                  "trash": napkin("bin", relation="in"),
                  "under_table": napkin("patio", pos=[3, 3], relation="under"),
                  "covered_chair": item("patio", pos=[4, 4], covered=True)})
        exposed = {e for e in w.s["entities"] if environment.exposed(w.s, e)}
        self.assertEqual(exposed, {"napkin", "bin"})
        w.s["entities"]["bin"]["open"] = True
        self.assertTrue(environment.exposed(w.s, "trash"))
        self.hooks.add("phys.exposed", lambda state, eid: eid != "napkin")
        self.assertFalse(environment.exposed(w.s, "napkin"))


class RainTests(PhysTest):
    def test_rain_soaks_paper(self):
        calls = []
        self.hooks.add("phys.wetted", lambda w, eid, liters, liquid, source: calls.append(eid))
        w = yard({"letter": item("patio", pos=[1, 1], material="paper", mass_kg=0.005, size_m=[0.21, 0.297, 0.0001])})
        environment.rain_wet(w, "letter", 5.0, 30.0)
        rec = w.s["entities"]["letter"]
        self.assertAlmostEqual(rec["wetness"], 0.25)
        self.assertAlmostEqual(rec["moisture"], 5.0 * 0.21 * 0.297 * 30 / 3600 / 0.01, places=4)
        self.assertNotIn("soaked", rec)
        for _ in range(3):
            environment.rain_wet(w, "letter", 5.0, 30.0)
        rec = w.s["entities"]["letter"]
        self.assertEqual(rec["wetness"], 1.0)
        self.assertTrue(rec["soaked"])
        self.assertGreaterEqual(rec["damage"]["water_damage"], environment.SOAKED_MOISTURE)
        self.assertEqual(calls, ["letter", "letter"])            # fully wet, then soaked through

    def test_rain_fills_an_open_bucket_and_wets_a_chair(self):
        w = yard({"bucket": item("patio", pos=[1, 1], size_m=[0.3, 0.3, 0.35], capacity_l=10.0),
                  "chair": item("patio", pos=[2, 1], size_m=[0.5, 0.5, 0.9])})
        environment.rain_wet(w, "bucket", 10.0, 3600.0)
        self.assertAlmostEqual(w.s["entities"]["bucket"]["liquid_l"], 10.0 * 0.09)
        environment.rain_wet(w, "chair", 10.0, 60.0)
        self.assertAlmostEqual(w.s["entities"]["chair"]["wetness"], 0.5)
        self.assertNotIn("moisture", w.s["entities"]["chair"])

    def test_a_condition_word_without_a_rate_still_rains(self):
        """Adoption-round requests (admin_inventory 2, textiles_laundry 4): the rain law read only rain_mm_h, so the
        setup weather op's condition "rain" (and generation-1 weather) rained nothing once the law was active."""
        self.assertEqual(environment.rain_mm_h({"condition": "rain", "rain_mm_h": 6.0}), 6.0)
        self.assertEqual(environment.rain_mm_h({"condition": "rain"}), environment.RAIN_BY_CONDITION["rain"])
        self.assertEqual(environment.rain_mm_h({"condition": "rain", "rain_mm_h": 0.0}), 2.5)   # over a dry day
        self.assertEqual(environment.rain_mm_h({"condition": "storm"}), 15.0)
        for dry in ({}, None, {"condition": "clear"}, {"condition": "snow", "snow_cm_h": 1.0}, {"condition": 3}):
            self.assertEqual(environment.rain_mm_h(dry), 0.0, dry)


class EnvironmentSystemTests(PhysTest):
    active_laws = ("wind", "rain")

    def scene(self, **weather):
        return yard({"napkin": napkin("patio", pos=[1, 1]), "indoor": napkin("kitchen", pos=[1, 1]),
                     "porch_napkin": napkin("porch", pos=[1, 1]),
                     "towel": item("patio", pos=[3, 3], material="cotton", mass_kg=0.5, portable=True,
                                   size_m=[0.6, 0.4, 0.01]),
                     "bench": item("patio", pos=[4, 4], portable=False, mass_kg=20.0, size_m=[1.2, 0.4, 0.45])},
                    weather=weather)

    def test_wind_moves_exposed_light_items_only(self):
        w = self.scene(wind_mps=3.0, gust_mps=6.0, wind_from_deg=270)
        environment.run(w, 30.0, 0)
        self.assertTrue(w.s["entities"]["napkin"].get("blown_away"))
        self.assertFalse(w.s["entities"]["indoor"].get("blown_away"))
        self.assertFalse(w.s["entities"]["porch_napkin"].get("blown_away"))
        self.assertEqual(w.s["entities"]["bench"]["pos"], [4, 4])

    def test_rain_wets_every_exposed_item(self):
        w = self.scene(rain_mm_h=4.0)
        environment.run(w, 30.0, 0)
        for eid in ("napkin", "towel", "bench"):
            self.assertAlmostEqual(w.s["entities"][eid]["wetness"], 0.25, msg=eid)
        self.assertEqual(w.s["entities"]["indoor"]["wetness"], 0.0)
        self.assertEqual(w.s["entities"]["porch_napkin"]["wetness"], 0.0)

    def test_calm_and_deferred_weather_does_nothing(self):
        w = self.scene(wind_mps=2.0)
        before = clone(w.s)
        environment.run(w, 30.0, 0)
        self.assertEqual(w.s, before)
        self.fake.lawbook.on = set()
        w.s["weather"].update(wind_mps=20.0, rain_mm_h=10.0)
        environment.run(w, 30.0, 0)
        self.assertEqual(w.journal_keys(), [])

    def test_the_outdoor_set_follows_items_carried_outside(self):
        w = self.scene(wind_mps=8.0)
        self.assertNotIn("indoor", environment.outdoor_ids(w))
        w.mut("indoor").update(location="patio", pos=[5, 0])
        self.assertIn("indoor", environment.outdoor_ids(w))
        environment.run(w, 30.0, 0)
        self.assertTrue(w.s["entities"]["indoor"].get("blown_away"))

    def test_what_the_robot_holds_gets_wet_when_it_walks_out_into_the_rain(self):
        w = self.scene(rain_mm_h=4.0)
        w.s["entities"]["letter"] = item("robot", material="paper", mass_kg=0.005, size_m=[0.21, 0.297, 0.0001])
        w.s["robot"].update(hands={"right": "letter"})
        environment.run(w, 30.0, 0)
        self.assertEqual(w.s["entities"]["letter"]["wetness"], 0.0)
        w.robot_m()["room"] = "patio"
        environment.run(w, 30.0, 1)
        self.assertAlmostEqual(w.s["entities"]["letter"]["wetness"], 0.25)
        self.assertNotIn("blown_away", w.s["entities"]["letter"])           # held: the wind cannot take it


# ----- determinism and performance -----------------------------------------------------------------------------------

SCENARIO = """
import json, sys
sys.path[:0] = sys.argv[1:3]
import test_physics_fluids as T
from house_sim.kernel.phys import environment, liquids, processes
from house_sim.kernel.predicates import digest


class Cleanups(object):
    def addCleanup(self, fn):
        pass


T.FakeLayer(("wind", "rain")).install(Cleanups())
w = T.yard({"table": T.table("patio", pos=[1, 1]), "napkin": T.napkin("table", spos=[0.1, 0.1]),
            "pot": T.item("table", capacity_l=2.0, liquid_l=1.8, size_m=[0.2, 0.2, 0.2]),
            "bucket": T.item("patio", pos=[4, 4], capacity_l=1.0, size_m=[0.3, 0.3, 0.3]),
            "letter": T.item("patio", pos=[5, 5], material="paper", mass_kg=0.005, size_m=[0.21, 0.297, 0.0001])},
           weather={"wind_mps": 5.0, "rain_mm_h": 8.0, "wind_from_deg": 200})
liquids.drip_start(w, "pot", 1.5)
for i in range(240):
    w.s["time_s"] += 30.0
    environment.run(w, 30.0, i)
    processes.run(w, 30.0, i)
print(json.dumps([digest(w.s), sorted(w.s["entities"]), w.s["stats"]]))
"""


class DeterminismTests(PhysTest):
    def test_the_same_scenario_is_identical_across_processes_and_hash_seeds(self):
        here = os.path.dirname(os.path.abspath(__file__))
        outputs = []
        for seed in ("0", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run([sys.executable, "-c", SCENARIO, os.path.dirname(here), here], capture_output=True,
                                 text=True, env=env, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-2000:])
            outputs.append(json.loads(out.stdout.strip().splitlines()[-1]))
        self.assertEqual(outputs[0], outputs[1])
        digest_, ids, stats = outputs[0]
        self.assertTrue(any(i.startswith("spill") for i in ids))       # the scenario really did something
        self.assertGreaterEqual(stats.get("phys_blown", 0), 1)


class PerformanceTests(PhysTest):
    """Micro-benchmarks against TIER1_PHYSICS 11 (tests fail at 3x the budget so they are not flaky)."""
    active_laws = ("wind", "rain")

    def best_ms(self, fn, repeat=5, number=50):
        best = INF
        for _ in range(repeat):
            start = time.perf_counter()
            for _ in range(number):
                fn()
            best = min(best, (time.perf_counter() - start) / number * 1000.0)
        return best

    def test_processes_idle_and_with_ten_active(self):
        w = world({"table": table()})
        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        self.assertLess(self.best_ms(lambda: processes.run(w, 30.0, 0)), 3 * 0.02)
        for i in range(10):
            w.s["entities"]["pot%d" % i] = item("table", capacity_l=50.0, liquid_l=50.0)
            liquids.drip_start(w, "pot%d" % i, 0.01)
        self.assertEqual(len(processes.active(w.s, "dripping")), 10)
        self.assertLess(self.best_ms(lambda: processes.run(w, 30.0, 0), number=20), 3 * 0.2)

    def test_environment_windy_on_a_furnished_yard(self):
        entities = {"table%d" % i: table("patio", pos=[i % 10, i // 10]) for i in range(60)}
        entities.update(("napkin%d" % i, napkin("table%d" % (i % 60), spos=[0.1, 0.1], mass_kg=0.05))
                        for i in range(60))
        entities.update(("indoor%d" % i, item("kitchen", pos=[1, 1])) for i in range(700))
        w = world(entities, rooms={"patio": room("concrete", size=(10.0, 6.0), outdoor=True), "kitchen": room()},
                  weather={"wind_mps": 3.0})
        if os.environ.get("HOUSE_SIM_PERF_TESTS", "1") == "0":
            self.skipTest("wall-clock bound off (HOUSE_SIM_PERF_TESTS=0: CI runs the bounds alone)")
        self.assertLess(self.best_ms(lambda: environment.run(w, 30.0, 0), number=10), 3 * 0.3)
        self.fake.lawbook.on = set()
        self.assertLess(self.best_ms(lambda: environment.run(w, 30.0, 0)), 0.01)


if __name__ == "__main__":
    unittest.main()
