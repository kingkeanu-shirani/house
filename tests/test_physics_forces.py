"""Tier-1 physics forces and containment (PHYS-DATA): robot capacities, lift plans, pushing, friction, scratching and
containment fit (docs/design/TIER1_PHYSICS.md 5.6 and 12.2, TIER1_INTEGRATION.md 1.2, 1.5 and 1.15).

Subtree masses come from support.subtree_mass and damage goes through impact.damage (PHYS-STATICS); these tests
substitute small local fakes for both, so they do not depend on that package. Only core types are used, so they also
pass with HOUSE_SIM_DOMAINS=core."""
import math
import types
import unittest
from unittest import mock

from house_sim.kernel import geometry
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.phys import contain, forces, props as P
from house_sim.kernel.registry import BASE_PROPS, default_registry
from house_sim.kernel.schema import normalize
from house_sim.kernel.world import World
from house_sim.systems.core import subtree_mass as core_subtree_mass

G = 9.81


def fake_mass(state, eid):
    """Subtree mass as core computes it today (mass + liquid x density over the item and its descendants)."""
    return core_subtree_mass(World(state), eid)


def rec(**fields):
    out = dict(BASE_PROPS, type="generic_item", is_a=["portable_object", "object"], portable=True)
    out.update(fields)
    return out


def world(entities=None, rooms=None, people=None, robot=None):
    rooms = rooms or {"living": {"size_m": [5.0, 5.0], "floor_material": "hardwood", "exits": ["den"]},
                      "den": {"size_m": [4.0, 4.0], "floor_material": "carpet", "exits": ["living", "patio"]},
                      "patio": {"size_m": [4.0, 4.0], "floor_material": "stone", "exits": ["den"]}}
    config = {"schema_version": 2, "start_room": "living", "rooms": rooms, "entities": entities or {},
              "people": people or {}, "robot": robot or {}}
    return World(normalize(config, default_registry()), default_registry())


class RobotCapsTests(unittest.TestCase):
    def test_fallback_is_humanoid_v1_under_the_integration_names(self):
        caps = forces.robot_caps(world())
        self.assertEqual(caps, {"max_hand_kg": 8.0, "max_mass_kg": 20.0, "push_n": 220.0, "pull_n": 180.0,
                                "grip_n": 150.0, "pinch_n": 30.0, "wrist_nm": 6.0})

    def test_the_body_profile_is_the_only_source(self):
        # BODY-ENGINE (TIER1_INTEGRATION 1.2): robot_caps delegates to the body; the materialized ratings and the
        # profile (with profile_overrides) count, a force field on the robot record does not
        w = world(robot={"max_hand_kg": 4.0, "push_n": 120, "grip_n": "strong",
                         "profile_overrides": {"push_n": 140.0, "wrist_nm": 5.0}})
        caps = forces.robot_caps(w, "anything")
        self.assertEqual((caps["max_hand_kg"], caps["push_n"], caps["wrist_nm"], caps["grip_n"], caps["pinch_n"]),
                         (4.0, 140.0, 5.0, 150.0, 30.0))


LIFT_MESSAGES = {"over_capacity": "Object and contents exceed carrying capacity; get help or slide it",
                 2: "Both hands must be free for this item", 1: "No free hand; place something first"}


class LiftPlanMatchesCorePickUpTests(unittest.TestCase):
    """TIER1_PHYSICS 12.2: lift_plan uses core pick_up's exact thresholds. Every case builds a lit kitchen, puts the
    item on a table within reach, asks lift_plan, then runs today's core pick_up and compares."""

    CASES = [
        ("light", {"mass_kg": 1.0}, {}, {}),
        ("at the hand limit", {"mass_kg": 8.0}, {}, {}),
        ("just over the hand limit", {"mass_kg": 8.01}, {}, {}),
        ("wide", {"mass_kg": 1.0, "size_m": [0.71, 0.3, 0.1]}, {}, {}),
        ("exactly 0.7 m", {"mass_kg": 1.0, "size_m": [0.7, 0.3, 0.1]}, {}, {}),
        ("two_handed", {"mass_kg": 1.0, "two_handed": True}, {}, {}),
        ("hand both", {"mass_kg": 1.0}, {}, {"hand": "both"}),
        ("grip two_hand", {"mass_kg": 1.0}, {}, {"grip": "two_hand"}),
        ("at the total limit", {"mass_kg": 20.0}, {}, {}),
        ("over the total limit", {"mass_kg": 20.01}, {}, {}),
        ("heavy, one hand busy", {"mass_kg": 9.0}, {"hold": "right"}, {}),
        ("light, both hands busy", {"mass_kg": 1.0}, {"hold": "both"}, {}),
        ("asked hand busy", {"mass_kg": 1.0}, {"hold": "left"}, {"hand": "left"}),
        ("asked hand free", {"mass_kg": 1.0}, {"hold": "left"}, {"hand": "right"}),
        ("heavy, left arm failed", {"mass_kg": 9.0}, {"robot": {"arms_ok": {"left": False, "right": True}}}, {}),
        ("light, right arm failed", {"mass_kg": 1.0}, {"robot": {"arms_ok": {"left": True, "right": False}}}, {}),
        ("asked arm failed", {"mass_kg": 1.0}, {"robot": {"arms_ok": {"left": True, "right": False}}},
         {"hand": "right"}),
        ("liquid counts", {"mass_kg": 5.0, "capacity_l": 5.0, "liquid_l": 4.0, "liquid": "water"}, {}, {}),
        ("dense liquid counts", {"mass_kg": 4.0, "capacity_l": 5.0, "liquid_l": 3.0, "liquid_density": 1.42}, {}, {}),
        ("contents count", {"mass_kg": 7.0, "accepts_items": True, "container": True, "capacity_l": 20.0},
         {"inside": 2.0}, {}),
        ("smaller hands", {"mass_kg": 5.0}, {"robot": {"max_hand_kg": 4.0}}, {}),
        ("smaller total", {"mass_kg": 16.0}, {"robot": {"max_mass_kg": 15.0}}, {}),
    ]

    def env_for(self, item, setup):
        entities = {"table": {"type": "table", "location": "kitchen", "pos": [1, 1], "accepts_items": True},
                    "lamp": {"type": "ceiling_light", "location": "kitchen", "on": True},
                    "item": dict({"type": "generic_item", "location": "table", "relation": "on",
                                  "size_m": [0.3, 0.3, 0.3]}, **item)}
        robot = dict(setup.get("robot", {}))
        hold = setup.get("hold")
        if hold:
            robot["hands"] = {"left": None, "right": None}
            for hand in (("left", "right") if hold == "both" else (hold,)):
                entities["held_" + hand] = {"type": "generic_item", "location": "robot", "relation": "held",
                                            "held_hand": hand}
                robot["hands"][hand] = "held_" + hand
        if setup.get("inside"):
            entities["inner"] = {"type": "generic_item", "location": "item", "relation": "in",
                                 "mass_kg": setup["inside"], "size_m": [0.1, 0.1, 0.1]}
        config = {"schema_version": 2, "start_room": "kitchen",
                  "rooms": {"kitchen": {"size_m": [4.0, 4.0], "floor_material": "tile"}},
                  "entities": entities, "robot": robot}
        env = HouseEnv(config)
        self.assertTrue(env.step("approach", "table")["ok"])
        return env

    def test_every_case_matches_core(self):
        for name, item, setup, args in self.CASES:
            with self.subTest(name):
                env = self.env_for(item, setup)
                hands = "both" if args.get("grip") == "two_hand" else args.get("hand")
                with mock.patch.object(forces, "_subtree_mass", fake_mass):
                    plan = forces.lift_plan(env.world, "item", hands=hands)
                result = env.step("pick_up", "item", **args)
                self.assertEqual(plan["ok"], result["ok"], (plan, result["message"]))
                self.assertAlmostEqual(plan["mass_kg"], core_subtree_mass(env.world, "item"))
                if result["ok"]:
                    # core accepted the lift; a domain's grip physics may then let it slip (manipulation: the 8 kg
                    # cube in a one-hand friction grip), which body_faults.drop_held reports after " | "
                    self.assertEqual(result["message"].split(" | ")[0], "Picked up item")
                    record = env.world.ent("item")
                    if record["location"] == "robot":     # a domain's grip physics may drop it again at once
                        self.assertEqual(record["held_hand"], "both" if len(plan["hands"]) == 2 else plan["hands"][0])
                else:
                    key = "over_capacity" if plan["reason"] == "over_capacity" else (2 if plan["hands"] == ["left", "right"] else 1)
                    self.assertEqual(result["message"], LIFT_MESSAGES[key])

    def test_the_case_list_covers_every_outcome(self):
        """A guard on the table above: each reason and each hand count appears."""
        reasons = set()
        for name, item, setup, args in self.CASES:
            env = self.env_for(item, setup)
            with mock.patch.object(forces, "_subtree_mass", fake_mass):
                plan = forces.lift_plan(env.world, "item", hands="both" if args.get("grip") == "two_hand" else args.get("hand"))
            reasons.add((plan["reason"], len(plan["hands"])))
        self.assertTrue({(None, 1), (None, 2), ("over_capacity", 2), ("hands_busy", 2), ("hands_busy", 0),
                         ("arm_failed", 2), ("arm_failed", 0)} <= reasons, reasons)


class TeamLiftTests(unittest.TestCase):
    def setUp(self):
        people = {"ann": {"role": "adult", "location": "living"}, "bea": {"role": "adult", "location": "living"},
                  "kid": {"role": "child", "location": "living"}, "gus": {"role": "elderly", "location": "living"},
                  "ted": {"role": "teen", "location": "living"},
                  "away": {"role": "adult", "location": "den"},
                  "sleepy": {"role": "adult", "location": "living", "awake": False},
                  "no": {"role": "adult", "location": "living", "consent": False},
                  "cane": {"role": "adult", "location": "living", "abilities": {"mobility": "cane"}},
                  "strong": {"role": "adult", "location": "living", "lift_kg": 40},
                  "mover": {"role": "adult", "location": "living", "max_lift_kg": 30, "push_n": 300}}
        self.w = world({"chest": {"type": "generic_item", "location": "living", "mass_kg": 30.0,
                                  "size_m": [0.8, 0.5, 0.5]}}, people=people)

    def plan(self, helpers):
        with mock.patch.object(forces, "_subtree_mass", fake_mass):
            return forces.lift_plan(self.w, "chest", helpers=helpers)

    def test_present_awake_consenting_helpers_enable_a_team_lift(self):
        self.assertEqual(self.plan(())["reason"], "over_capacity")
        plan = self.plan(["ann"])                                   # 20 + 23 >= 30
        self.assertEqual((plan["ok"], plan["helpers"], plan["hands"]), (True, ["ann"], ["left", "right"]))
        self.assertEqual(self.plan(["ann", "ann"])["helpers"], ["ann"])

    def test_who_does_not_count(self):
        for pid in ("kid", "away", "sleepy", "no", "cane", "nobody"):
            self.assertEqual(self.plan([pid])["reason"], "over_capacity", pid)
        self.assertEqual(self.plan(["ted"])["helpers"], ["ted"])          # 20 + 15 >= 30
        self.assertEqual(self.plan(["gus"])["helpers"], ["gus"])           # 20 + 10 = 30: exactly enough
        self.w.s["entities"]["chest"]["mass_kg"] = 30.5
        self.assertEqual(self.plan(["gus"])["reason"], "over_capacity")

    def test_own_capacity_wins_over_the_role(self):
        self.w.s["entities"]["chest"]["mass_kg"] = 58.0
        self.assertEqual(self.plan(["strong"])["helpers"], ["strong"])    # 20 + 40
        self.assertEqual(self.plan(["mover"])["reason"], "over_capacity")  # 20 + 30 (manipulation's max_lift_kg)
        self.assertEqual(self.plan(["mover", "ann", "kid"])["helpers"], ["mover", "ann"])

    def test_roles_without_a_row_help_by_their_age(self):
        """Adoption-round request (people K-HELPER-ROLES): toddlers, guests, visitors, contractors and couriers fell
        back to the adult row whatever their age. A toddler has its own row; other roles take their age band."""
        people = self.w.s["people"]
        people.update({"tot": {"role": "toddler", "location": "living", "age": 2},
                       "guest_old": {"role": "guest", "location": "living", "age": 78},
                       "guest_kid": {"role": "visitor", "location": "living", "age": 9},
                       "courier": {"role": "courier", "location": "living", "age": 30},
                       "contractor": {"role": "contractor", "location": "living"}})
        self.assertEqual(self.plan(["tot"])["reason"], "over_capacity")
        self.assertEqual(self.plan(["guest_kid"])["reason"], "over_capacity")      # a 9-year-old: a child, 0 kg
        self.assertEqual(self.plan(["guest_old"])["helpers"], ["guest_old"])       # 78: elderly, 20 + 10 = 30
        self.w.s["entities"]["chest"]["mass_kg"] = 40.0
        self.assertEqual(self.plan(["guest_old"])["reason"], "over_capacity")
        self.assertEqual(self.plan(["courier"])["helpers"], ["courier"])           # 30: adult, 20 + 23 >= 40
        self.assertEqual(self.plan(["contractor"])["helpers"], ["contractor"])     # no age: the default role
        self.assertEqual(forces.max_push_n(self.w, ["tot", "guest_kid"]), forces.max_push_n(self.w))

    def test_helpers_never_rescue_busy_hands(self):
        self.w.s["robot"]["hands"]["left"] = "chest"
        self.assertEqual(self.plan(["ann"])["reason"], "hands_busy")

    def test_max_push_adds_helpers_push(self):
        w = self.w
        self.assertEqual(forces.max_push_n(w), 220.0)
        self.assertEqual(forces.max_push_n(w, ["ann", "bea"]), 620.0)
        self.assertEqual(forces.max_push_n(w, ["ted", "gus", "kid", "away", "sleepy", "no", "cane"]), 420.0)
        self.assertEqual(forces.max_push_n(w, ["mover"]), 520.0)


class PushTests(unittest.TestCase):
    def setUp(self):
        self.w = world({
            "sofa": {"type": "sofa", "location": "living", "pos": [0, 0]},
            "dresser": {"type": "dresser", "location": "living", "pos": [3, 3], "mass_kg": 100.0},
            "felt_chair": {"type": "chair", "location": "living", "pos": [0, 4], "mass_kg": 10.0,
                           "pads": {"count": 4, "legs": 4, "kind": "felt"}},
            "half_felt": {"type": "chair", "location": "living", "pos": [2, 4], "mass_kg": 10.0,
                          "pads": {"count": 2, "legs": 4, "kind": "felt"}},
            "slid": {"type": "chair", "location": "den", "pos": [0, 0], "mass_kg": 10.0, "on_sliders": True},
            "cart": {"type": "generic_item", "location": "den", "pos": [3, 3], "mass_kg": 10.0, "wheeled": True},
            "bench": {"type": "bench", "location": "den", "pos": [0, 3], "mass_kg": 10.0, "foot_material": "rubber"},
            "anchored": {"type": "dresser", "location": "living", "pos": [3, 0], "anchored": True},
            "table": {"type": "table", "location": "patio", "pos": [0, 0], "accepts_items": True},
            "toolbox": {"type": "generic_item", "location": "table", "relation": "on", "mass_kg": 5.0,
                        "material": "steel"},
            "held": {"type": "generic_item", "location": "robot", "relation": "held", "held_hand": "right"}},
            robot={"hands": {"left": None, "right": "held"}})

    def need(self, eid, floor_id=None):
        with mock.patch.object(forces, "_subtree_mass", fake_mass):
            return forces.push_needed_n(self.w, eid, floor_id)

    def test_furniture_anchors(self):
        self.assertAlmostEqual(self.need("sofa"), 0.3 * 60 * G)               # 177 N
        self.assertLessEqual(self.need("sofa"), forces.max_push_n(self.w))     # the robot moves it
        self.assertAlmostEqual(self.need("dresser"), 0.3 * 100 * G)           # 294 N
        self.assertGreater(self.need("dresser"), forces.max_push_n(self.w))    # refused alone
        self.assertEqual(self.need("anchored"), math.inf)

    def test_feet_and_aids(self):
        self.assertAlmostEqual(self.need("felt_chair"), 0.3 * 0.5 * 10 * G)   # felt halves friction
        self.assertAlmostEqual(self.need("half_felt"), 0.3 * 10 * G)          # two pads of four do nothing
        self.assertAlmostEqual(self.need("slid"), 0.6 * 0.5 * 10 * G)         # sliders on carpet
        self.assertAlmostEqual(self.need("cart"), 0.06 * 10 * G)              # casters on carpet roll
        self.assertAlmostEqual(self.need("bench"), 1.0 * 10 * G)              # rubber feet capped at mu 1.0
        self.w.s["entities"]["slid"]["location"] = "living"
        self.assertAlmostEqual(self.need("slid"), 0.3 * 0.3 * 10 * G)         # sliders on hardwood
        self.w.s["entities"]["mat"] = dict(default_registry().make("rug", "living"), pos=[4, 4])
        self.assertAlmostEqual(self.need("slid", "mat"), 0.6 * 0.5 * 10 * G)     # a rug as the given floor

    def test_rugs_and_supports(self):
        self.w.s["entities"]["floor_rug"] = dict(default_registry().make("rug", "living"), pos=[0, 0])
        self.assertAlmostEqual(self.need("sofa"), 0.6 * 60 * G)               # the sofa stands on the rug
        self.assertAlmostEqual(self.need("dresser"), 0.3 * 100 * G)           # the dresser does not
        self.assertAlmostEqual(self.need("toolbox"), forces.friction(self.w.ent("toolbox"), self.w.ent("table")) * 5 * G)
        with self.assertRaises(ValueError):
            self.need("held")

    def test_foot_kinds(self):
        ent = self.w.ent
        self.assertEqual([forces.foot_kind(ent(e)) for e in ("felt_chair", "slid", "cart", "bench", "sofa", "half_felt")],
                         ["felt", "slider", "caster", "rubber", "wood", "wood"])
        self.assertEqual(forces.foot_kind(rec(foot_material="metal")), "steel")
        self.assertEqual(forces.foot_kind(rec(material="leather")), "wood")      # legs under upholstery
        self.assertEqual(forces.foot_kind(rec(material="glass")), "glass")

    def test_pushing_high_tips_before_sliding(self):
        w = world({"case": {"type": "bookshelf", "location": "patio", "pos": [0, 0], "size_m": [0.9, 0.30, 1.8]},
                   "fixed": {"type": "bookshelf", "location": "patio", "pos": [2, 0], "size_m": [0.9, 0.30, 1.8],
                             "anchored": True},
                   "trolley": {"type": "bookshelf", "location": "patio", "pos": [0, 3], "size_m": [0.9, 0.30, 1.8],
                               "casters": True}})
        self.assertTrue(forces.tips_when_pushed(w, "case", 0.38))              # mu 0.4 (stone) x h > 0.15 m
        self.assertFalse(forces.tips_when_pushed(w, "case", 0.37))
        self.assertFalse(forces.tips_when_pushed(w, "fixed", 1.8))
        self.assertFalse(forces.tips_when_pushed(w, "trolley", 1.8))            # rolls away instead
        w.s["entities"]["case"]["size_m"] = [0.9, 0.6, 1.8]                     # a deeper case needs a higher push
        self.assertFalse(forces.tips_when_pushed(w, "case", 0.7))
        self.assertTrue(forces.tips_when_pushed(w, "case", 0.8))

    def test_the_base_counts_along_the_push(self):
        """Adoption-round request (manipulation 8): b is the footprint's extent along the push, not always the narrow
        side: the 0.9 x 0.3 m bookcase pulled forward (along y) tips at 0.38 m, pushed sideways (along x) only above
        mu h > 0.45 m, i.e. 1.13 m; a case turned 90 degrees swaps the two."""
        w = world({"case": {"type": "bookshelf", "location": "patio", "pos": [0, 0], "size_m": [0.9, 0.30, 1.8]},
                   "turned": {"type": "bookshelf", "location": "patio", "pos": [3, 0], "size_m": [0.9, 0.30, 1.8],
                              "rot": 90}})
        for direction in ("south", "north", 90.0, (0, 1)):
            self.assertTrue(forces.tips_when_pushed(w, "case", 0.38, direction), direction)
        for direction in ("east", "west", 0.0, (-1, 0)):
            self.assertFalse(forces.tips_when_pushed(w, "case", 1.1, direction), direction)
            self.assertTrue(forces.tips_when_pushed(w, "case", 1.14, direction), direction)
        self.assertTrue(forces.tips_when_pushed(w, "turned", 0.38, "east"))
        self.assertFalse(forces.tips_when_pushed(w, "turned", 1.1, "south"))
        diagonal = 0.9 * math.cos(math.radians(45)) + 0.3 * math.sin(math.radians(45))      # 0.85 m along 45 degrees
        self.assertFalse(forces.tips_when_pushed(w, "case", diagonal / 0.8 - 0.01, 45.0))
        self.assertTrue(forces.tips_when_pushed(w, "case", diagonal / 0.8 + 0.01, 45.0))
        with self.assertRaises(ValueError):
            forces.tips_when_pushed(w, "case", 1.0, "up")


class FrictionTests(unittest.TestCase):
    def test_single_records_are_measured_on_finished_wood(self):
        self.assertAlmostEqual(forces.friction(rec(material="wood")), 0.55)
        self.assertAlmostEqual(forces.friction(rec(material="rubber")), 0.9)
        self.assertAlmostEqual(forces.friction(rec(material="glass"), rec(material="wood")), 0.4)
        self.assertAlmostEqual(forces.friction(rec(material="rubber"), rec(material="glass")), 0.9 * 0.4 / 0.55)
        self.assertAlmostEqual(forces.friction(rec(material="wood", friction_mu=0.2)), 0.2)

    def test_surface_states(self):
        self.assertAlmostEqual(forces.friction(rec(material="wood", wetness=0.5)), 0.55 * 0.4)
        self.assertAlmostEqual(forces.friction(rec(material="wood", wetness=0.15)), 0.55 * 0.6)
        self.assertAlmostEqual(forces.friction(rec(material="wood"), rec(material="wood", surface_state="oily")), 0.55 * 0.1)
        self.assertAlmostEqual(forces.friction(rec(material="wood", wetness=0.5), wet=False), 0.55)
        self.assertAlmostEqual(forces.friction(rec(material="wood"), wet=True), 0.55 * 0.4)
        self.assertAlmostEqual(forces.friction(rec(material="wood"), wet="greasy"), 0.55 * 0.15)
        self.assertAlmostEqual(forces.friction(rec(material="soap")), 0.5)
        self.assertAlmostEqual(forces.friction(rec(material="soap", wetness=0.6)), 0.1)
        self.assertAlmostEqual(forces.friction(rec(material="wood"), rec(material="soap"), wet="soapy"), 0.1)
        with self.assertRaises(ValueError):
            forces.friction(rec(), wet="sticky")

    def test_clamped(self):
        self.assertEqual(forces.friction(rec(material="rubber"), rec(material="rubber")), 1.0)
        self.assertEqual(forces.friction(rec(friction_mu=0.01)), 0.02)


class FakeImpact:
    def __init__(self):
        self.calls = []
        self.events = []

    @staticmethod
    def damage_map(value):
        return dict(value) if isinstance(value, dict) else {}

    def damage(self, w, eid, kind, severity, detail="", by=None, event=None, **payload):
        self.calls.append((eid, kind, round(severity, 6), by))
        self.events.append(event)
        w.mut(eid)["damage"] = dict(self.damage_map(w.ent(eid).get("damage")), **{kind: severity})
        return True

    @staticmethod
    def robot_caused(w, eid):
        return bool(w.ent(eid).get("_robot_moved"))


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.w = world({
            "floor_living": {"type": "floor_surface", "location": "living", "material": "hardwood"},
            "floor_patio": {"type": "floor_surface", "location": "patio", "material": "tile"},
            "rug": {"type": "rug", "location": "living", "pos": [0, 0]},
            "rack": {"type": "generic_item", "location": "living", "mass_kg": 10.0, "foot_material": "metal"},
            "padded": {"type": "generic_item", "location": "living", "mass_kg": 10.0, "foot_material": "metal",
                       "pads": {"count": 4, "legs": 4}},
            "trolley": {"type": "generic_item", "location": "living", "mass_kg": 10.0, "casters": True},
            "crate": {"type": "generic_item", "location": "living", "mass_kg": 1.0}})
        self.impact = FakeImpact()
        self.importer = types.SimpleNamespace(import_module=lambda name: self.impact)

    def scratch(self, floor, mover, metres=2.0, by="robot"):
        with mock.patch.object(forces, "_subtree_mass", fake_mass), mock.patch.object(forces, "importlib", self.importer):
            self.w.begin()
            return forces.scratch(self.w, floor, mover, metres, by=by)

    def test_metal_feet_scratch_hardwood_and_damage_accrues(self):
        self.assertAlmostEqual(self.scratch("floor_living", "rack"), 0.2)    # risk 1 x 1 x min(1, 10/4) = 1.0
        self.assertAlmostEqual(self.scratch("floor_living", "rack", 1.0, by="exogenous"), 0.1)
        self.assertEqual(self.impact.calls, [("floor_living", "scratch", 0.2, "robot"),
                                             ("floor_living", "scratch", 0.3, "exogenous")])
        self.w.mut("floor_living")["damage"] = {"scratch": 0.95}
        self.scratch("floor_living", "rack", 5.0)
        self.assertEqual(self.impact.calls[-1][2], 1.0)                        # capped at 1.0

    def test_what_leaves_no_scratch(self):
        for floor, mover in (("floor_living", "padded"), ("floor_living", "trolley"), ("rug", "rack"),
                             ("floor_patio", "crate")):
            self.assertEqual(self.scratch(floor, mover), 0.0, (floor, mover))
        self.assertEqual(self.scratch("floor_living", "rack", 0.0), 0.0)
        self.assertEqual(self.impact.calls, [])

    def test_grit_adds_half_the_floor_factor(self):
        self.assertEqual(self.scratch("floor_patio", "crate"), 0.0)             # 0.25 x 0.5 x 0.25 = 0.03
        self.w.ent("floor_living")["soil"] = {"sand": 0.3}
        self.assertAlmostEqual(self.scratch("floor_living", "crate", 1.0), (0.125 + 0.5) * 0.1)
        self.w.ent("floor_living")["soil"] = {"mud": 0.3}                       # mud counts half: below 0.2
        self.assertEqual(self.scratch("floor_living", "crate", 1.0), 0.0)

    def test_a_visible_scratch_is_one_phys_scratch_event_charged_to_the_drag(self):
        """TIER1_PHYSICS 7.7 and TIER1_INTEGRATION 1.11 list phys.scratch: with the real impact module a drag that
        marks the floor emits it (the sounds observer's scrape) and records one consequence of that kind."""
        from house_sim.kernel.phys import impact
        self.assertEqual(self.impact.events, [])
        with mock.patch.object(forces, "_subtree_mass", fake_mass):
            self.w.begin()
            self.assertAlmostEqual(forces.scratch(self.w, "floor_living", "rack", 2.0), 0.2)   # by None: the mover's
            [event] = [e for e in self.w.s["event_log"] if e["kind"] == "phys.scratch"]
            self.assertEqual((event["target"], event["by"], event["mover"], event["room"], event["damage"]),
                             ("floor_living", "exogenous", "rack", "living", "scratch"))
            self.assertEqual([r["kind"] for r in impact.consequences(self.w)], ["phys.scratch"])
            self.assertEqual(impact.consequences(self.w)[0]["note"], "floor_living was scratched")
            self.assertNotIn("damage", self.w.s.get("stats", {}))               # nobody's harm: the robot did not drag
            self.w.mut("rack")["_robot_moved"] = True                              # the robot moved it: its drag
            self.assertAlmostEqual(forces.scratch(self.w, "floor_living", "rack", 1.0), 0.1)
            events = [e for e in self.w.s["event_log"] if e["kind"] == "phys.scratch"]
            self.assertEqual([e["by"] for e in events], ["exogenous", "robot"])
            self.assertEqual(self.w.s["entities"]["floor_living"]["damage"]["scratch"], 0.3)
            self.assertEqual(forces.scratch(self.w, "floor_living", "padded", 2.0), 0.0)   # felt pads: no event
            self.assertEqual(len([e for e in self.w.s["event_log"] if e["kind"] == "phys.scratch"]), 2)
        with mock.patch.object(forces, "_subtree_mass", fake_mass), mock.patch.object(forces, "importlib", self.importer):
            self.w.begin()
            forces.scratch(self.w, "floor_living", "rack", 1.0)
        self.assertEqual(self.impact.events, ["phys.scratch"])                  # what forces asks impact for

    def test_damage_goes_through_impact(self):
        support = types.SimpleNamespace(subtree_mass=lambda state, eid: 3.5)
        with mock.patch.object(forces, "importlib", types.SimpleNamespace(import_module=lambda name: support)):
            self.assertEqual(forces._subtree_mass(self.w.s, "crate"), 3.5)
        names = []
        importer = types.SimpleNamespace(import_module=lambda name: names.append(name) or self.impact)
        with mock.patch.object(forces, "importlib", importer):
            self.w.begin()
            forces._add_damage(self.w, "floor_living", "scratch", 0.25, "test", "robot")
        self.assertEqual(names, ["house_sim.kernel.phys.impact"])
        self.assertEqual(self.impact.calls, [("floor_living", "scratch", 0.25, "robot")])


class ContainmentTests(unittest.TestCase):
    def setUp(self):
        self.state = world({
            "bin": {"type": "generic_item", "location": "living", "accepts_items": True, "container": True,
                    "capacity_l": 20.0, "inner_size_m": [0.7, 0.5, 0.5], "size_m": [0.75, 0.55, 0.55]},
            "cabinet": {"type": "generic_item", "location": "living", "accepts_items": True, "container": True,
                        "capacity_l": 3.5, "inner_size_m": [0.5, 0.4, 0.3]},
            "bottle": {"type": "wine_bottle", "location": "living", "material": "glass", "accepts_items": True,
                       "container": True, "inner_size_m": [0.06, 0.06, 0.25], "size_m": [0.075, 0.075, 0.3]},
            "jar": {"type": "generic_item", "location": "living", "accepts_items": True, "container": True,
                    "inner_size_m": [0.09, 0.09, 0.15], "opening_m": 0.05},
            "slot": {"type": "generic_item", "location": "living", "accepts_items": True, "container": True,
                     "inner_size_m": [0.4, 0.3, 0.3], "opening_m": [0.3, 0.05]},
            "brick": {"type": "generic_item", "location": "bin", "relation": "in", "volume_l": 15.0,
                      "size_m": [0.3, 0.2, 0.2]}}).s

    def test_box_and_diagonal_fit(self):
        self.assertTrue(contain.box_fits([0.5, 0.3, 0.2], [0.7, 0.5, 0.5]))
        self.assertTrue(contain.box_fits([0.9, 0.03, 0.03], [0.7, 0.5, 0.5]))    # an umbrella along the diagonal
        self.assertFalse(contain.box_fits([0.9, 0.2, 0.2], [0.7, 0.5, 0.5]))     # too thick to tilt in
        self.assertFalse(contain.box_fits([1.1, 0.03, 0.03], [0.7, 0.5, 0.5]))   # longer than the diagonal
        self.assertTrue(contain.box_fits([2.0, 2.0, 2.0], None))
        for item, inner in (([0.3, 0.2, 0.1], [0.25, 0.35, 0.15]), ([0.3, 0.4, 0.1], [0.2, 0.2])):
            self.assertEqual(contain.box_fits(item, inner), geometry.fits_inside(item, inner))
        self.assertEqual(contain.fits(self.state, rec(size_m=[0.9, 0.2, 0.2]), "bin"), (False, "It does not fit inside"))

    def test_openings(self):
        pencil = rec(size_m=[0.007, 0.007, 0.19], shape="cylinder")
        cube = rec(size_m=[0.03, 0.03, 0.03])
        self.assertEqual(contain.fits(self.state, pencil, "bottle"), (True, None))   # 0.018 m neck (mouth_frac 0.3)
        self.assertEqual(contain.fits(self.state, cube, "bottle"), (False, "It does not fit through the opening"))
        ball, box = rec(size_m=[0.05, 0.05, 0.05], shape="sphere"), rec(size_m=[0.04, 0.04, 0.04])
        self.assertTrue(contain.fits(self.state, ball, "jar")[0])                     # round through a 50 mm mouth
        self.assertFalse(contain.fits(self.state, box, "jar")[0])                     # its diagonal is 57 mm
        wide_disc = rec(size_m=[0.045, 0.045, 0.01], shape="cylinder")                # passes edge-on: 46 mm
        self.assertTrue(contain.fits(self.state, wide_disc, "jar")[0])
        self.assertTrue(contain.fits(self.state, rec(size_m=[0.25, 0.04, 0.2]), "slot")[0])
        self.assertFalse(contain.fits(self.state, rec(size_m=[0.25, 0.06, 0.2]), "slot")[0])
        sock = rec(size_m=[0.25, 0.07, 0.2], material="cotton")                       # soft: squeezes 1.5x
        self.assertTrue(contain.fits(self.state, sock, "slot")[0])
        self.assertTrue(contain.opening_ok(cube, rec(inner_size_m=[0.02, 0.02, 0.02])))   # no opening data: box only

    def test_volume_today_and_with_nesting_and_compression(self):
        self.assertEqual(contain.fits(self.state, rec(volume_l=5.0, size_m=[0.2, 0.2, 0.2]), "bin"), (True, None))
        self.assertEqual(contain.fits(self.state, rec(volume_l=5.01, size_m=[0.2, 0.2, 0.2]), "bin"),
                         (False, "Not enough room inside"))
        self.assertTrue(contain.fits(self.state, rec(volume_l=19.0), "bin", count_existing=False)[0])
        self.assertTrue(contain.fits(self.state, "brick", "bin")[0])                 # already inside: counted once
        entities = self.state["entities"]
        for i in range(2):
            entities["bowl_{}".format(i)] = rec(type="cereal_bowl", location="cabinet", relation="in", volume_l=2.0)
        self.assertAlmostEqual(contain.nested_volume_l(self.state, "cabinet"), 2.0 + 0.3 * 2.0)
        self.assertTrue(contain.fits(self.state, rec(type="soup_bowl", volume_l=2.0), "cabinet")[0])  # 3.2 l of 3.5
        self.assertFalse(contain.fits(self.state, rec(type="mug", volume_l=1.0), "cabinet")[0])     # mugs do not nest
        self.assertFalse(contain.fits(self.state, rec(type="paper_cup", volume_l=1.0), "cabinet")[0])  # own group: 3.6
        entities["cup_0"] = rec(type="paper_cup", location="cabinet", relation="in", volume_l=0.8)       # 3.4 of 3.5
        self.assertTrue(contain.fits(self.state, rec(type="paper_cup", volume_l=0.25), "cabinet")[0])   # nests: + 0.1
        self.assertFalse(contain.fits(self.state, rec(type="paper_cup", volume_l=0.5), "cabinet")[0])   # + 0.2
        del entities["cup_0"]
        big = rec(type="mixing_bowl", volume_l=3.0)                                  # a bigger bowl becomes the outer one
        self.assertAlmostEqual(contain._effective_volume([("a", big), ("b", entities["bowl_0"]), ("c", entities["bowl_1"])]),
                               3.0 + 0.3 * 2.0 + 0.3 * 2.0)
        towel = rec(material="cotton", volume_l=5.0)                                 # softness 0.8: counts 60 %
        self.assertAlmostEqual(contain._effective_volume([("t", towel)]), 3.0)
        self.assertTrue(contain.fits(self.state, dict(towel, volume_l=8.0), "bin")[0])    # 15 + 4.8 <= 20
        self.assertFalse(contain.fits(self.state, dict(towel, compressible=False, volume_l=8.0), "bin")[0])

    def test_checks_run_in_order_and_accept_records(self):
        huge = rec(size_m=[1.0, 1.0, 1.0], volume_l=500.0)
        self.assertEqual(contain.fits(self.state, huge, "bin")[1], "It does not fit inside")
        self.assertEqual(contain.fits(self.state, huge, rec(capacity_l=1.0))[1], "Not enough room inside")
        self.assertEqual(contain.fits(self.state, huge, rec(capacity_l=0.0)), (True, None))

    def test_choking_cylinder(self):
        small = {"marble": rec(size_m=[0.016, 0.016, 0.016], shape="sphere"),
                 "aa_battery": rec(size_m=[0.0145, 0.0145, 0.0505], shape="cylinder"),
                 "coin": rec(size_m=[0.024, 0.024, 0.002]),
                 "brick_toy": rec(size_m=[0.032, 0.016, 0.011]),
                 "ball_at_limit": rec(size_m=[0.0317, 0.0317, 0.0317], shape="sphere")}
        large = {"golf_ball": rec(size_m=[0.0427, 0.0427, 0.0427], shape="sphere"),
                 "pencil": rec(size_m=[0.007, 0.007, 0.19], shape="cylinder"),
                 "cube_32mm": rec(size_m=[0.032, 0.032, 0.032]),
                 "default_size": rec(),
                 "spill": rec(size_m=[0.01, 0.01, 0.001], material="water")}
        for name, record in small.items():
            self.assertTrue(contain.choking_hazard(record), name)
        for name, record in large.items():
            self.assertFalse(contain.choking_hazard(record), name)


if __name__ == "__main__":
    unittest.main()
