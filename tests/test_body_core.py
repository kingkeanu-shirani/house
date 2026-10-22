"""The robot body in the core verbs (BODY-CORE + NAV-M5, TIER1_INTEGRATION 2.4 B5; TIER1_EMBODIMENT 10 items 16, 19-24).

What this file pins:

* lifts (TIER1_INTEGRATION 1.5): pick_up, receive and uninstall plan the lift, take the item with a grasp its size
  allows and enforce the derated capacity of the hands used (above the head, on a climbing aid, limp mode, a weak arm,
  the total carry); an overload is refused with today's text, counted, felt in realistic mode and drops nothing; a
  latent weak arm is found by the first lift it cannot take and the other arm succeeds;
* grasp size classes: an automatic pinch for small things, a power grip on them refused, too small refused, a paper
  slid to the edge of its surface (a hand) or refused without grip=edges (a parallel gripper);
* the held-load safety net (switched on): a load that outgrows its hand strains it and above 1.5 x slips out; a
  co-carried end and a towed cart count only the robot's share;
* sensors: look, inspect, read and peek need the colour cameras (both camera modes), look in a lit room relocalizes,
  listen and ask need the microphones (privacy mode: addressed speech only), speech through a failed speaker is
  inaudible and reveals the fault;
* speed: careful speed slows a walk once (2x, not 3x) and a physical leaf's own seconds 1.5x, nested leaves included
  and never twice; set_speed fast is refused while quiet or limp; stairs need a body that climbs them; face and peek
  use the body's guards;
* the setup ops robot (profile, profile_overrides, sensors, props) and robot_fault (at reset, at_s, until_s, choose);
* skills stop with PlanError("robot powered down"); both perception modes, the geometric camera, legacy states,
  determinism and the settlement budget with the safety net on.

The houses are built by hand (no furnishers), so the file passes with HOUSE_SIM_DOMAINS=core and with every domain
loaded.
"""
import copy
import time
import unittest

from house_sim.kernel import body
from house_sim.kernel.actions import CAMERA_FAULT_TEXT, IMAGING_OFF_TEXT, MIC_FAULT_TEXT, MIC_OFF_TEXT, action
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.errors import SpecError
from house_sim.kernel.plugins import ACTIONS
from house_sim.kernel.predicates import digest
from house_sim.kernel.realism import LEVELS
from house_sim.kernel.registry import default_registry
from house_sim.systems import core
from house_sim.tasks.runner import PlanError, Runner
from house_sim.tasks.setup import apply_setup

COLS = 16                               # the hall is 8 x 4 m: 16 x 8 cells, the doorway at [15, 0]


def thing(label, mass, size, pos=(2, 1), z=0.8, **extra):
    """A portable thing on a counter-height spot of the hall (z_m 0.8: within reach, no crouch)."""
    record = {"label": label, "location": "hall", "pos": list(pos), "z_m": z, "portable": True, "mass_kg": mass,
              "size_m": list(size)}
    record.update(extra)
    return record


def config(robot=None, entities=None, people=None, stairs=False, rooms=None, events=None):
    """A lit hall (8 x 4 m, robot at [1, 1]) and a lit kitchen behind a doorway (or 12 stairs up) at [15, 0]."""
    things = {"hall_light": {"label": "ceiling light", "location": "hall", "category": "light", "on": True,
                             "pos": [0, 7], "mounted": True},
              "kitchen_light": {"label": "ceiling light", "location": "kitchen", "category": "light", "on": True,
                                "pos": [7, 7], "mounted": True}}
    things.update(copy.deepcopy(entities or {}))
    layout = {"hall": {"size_m": [8.0, 4.0], "exits": ["kitchen"], "type": "hallway"},
              "kitchen": {"size_m": [4.0, 4.0], "exits": ["hall"], "floor": 1 if stairs else 0, "type": "kitchen"}}
    for rid, extra in (rooms or {}).items():
        layout[rid].update(extra)
    return {"schema_version": 2, "start_room": "hall", "rooms": layout,
            "connections": {"hall_kitchen": {"rooms": ["hall", "kitchen"], "kind": "stairs" if stairs else "doorway",
                                             "width_m": 0.9, "steps": 12,
                                             "cells": {"hall": [COLS - 1, 0], "kitchen": [0, 0]}}},
            "entities": things, "people": copy.deepcopy(people or {}), "pets": {}, "events": list(events or []),
            "robot": dict({"room": "hall", "pos": [1, 1]}, **copy.deepcopy(robot or {}))}


def make(robot=None, perception=None, camera="room", **kw):
    return HouseEnv(config(robot, **kw), perception=perception, camera=camera)


def robot(env):
    return env.world.s["robot"]


def ent(env, eid):
    return env.world.s["entities"][eid]


def handle(env, eid):
    return env.realism.to_handles(eid) if env.realism is not None else eid


def held(eid, hand="right", **record):
    """A record held by the robot in `hand` ("both" for two hands)."""
    return dict({"label": eid, "location": "robot", "relation": "held", "held_hand": hand, "portable": True}, **record)


def said(env):
    """The robot's last speech entry."""
    return [m for m in env.world.s["messages"] if m.get("speaker") == "robot"][-1]


def hands_of(eid, hand):
    return {"hands": {"left": eid if hand in ("left", "both") else None,
                      "right": eid if hand in ("right", "both") else None}}


class CoreTestCase(unittest.TestCase):
    def check(self, env, command, message, category):
        result = env.step(**command)
        self.assertEqual((result["ok"], result["message"], result["category"]), (False, message, category), command)
        return result

    def ok(self, env, *args, **kwargs):
        result = env.step(*args, **kwargs)
        self.assertTrue(result["ok"], (args, kwargs, result["message"]))
        return result

    def register(self, name, fn, **kw):
        """A test action, removed after the test."""
        ACTIONS.pop(name, None)
        action(name, **kw)(fn)
        self.addCleanup(ACTIONS.pop, name, None)

    def seconds(self, env, *args, **kwargs):
        before = env.world.s["time_s"]
        self.ok(env, *args, **kwargs)
        return round(env.world.s["time_s"] - before, 3)


# ----------------------------------------------------------------------
# lifts: pick_up (TIER1_INTEGRATION 1.5, TIER1_EMBODIMENT 4.7; test plan 20, 21)

class LiftTests(CoreTestCase):
    def test_ordinary_lifts_keep_todays_rule_and_an_overload_is_refused_counted_and_drops_nothing(self):
        env = make(entities={"mug": thing("mug", 0.4, [0.09, 0.09, 0.1]),
                             "box": thing("box", 12.0, [0.4, 0.3, 0.3], pos=(1, 2)),
                             "crate": thing("crate", 25.0, [0.5, 0.4, 0.4], pos=(2, 2))})
        self.ok(env, "pick_up", "mug")
        self.assertEqual((ent(env, "mug")["held_hand"], robot(env)["hands"]["right"]), ("right", "mug"))
        self.ok(env, "place", "mug", cell=[2, 0])
        self.ok(env, "pick_up", "box")                                   # 12 kg: over one hand, both hands
        self.assertEqual((ent(env, "box")["held_hand"], robot(env)["hands"]), ("both", {"left": "box", "right": "box"}))
        self.ok(env, "place", "box", cell=[0, 2])
        result = self.check(env, {"action": "pick_up", "target": "crate"}, body.OVERLOAD_TEXT, "safety")
        stats = env.world.s["stats"]
        self.assertEqual((stats["overload_attempts"], stats["safety_blocks"]), (1, 1))     # survives the rollback
        self.assertIn("overload_s", robot(env)["body_log"])
        self.assertEqual([e for e in env.world.s["event_log"] if e.get("kind") == "overload"][-1]["ratio_bucket"],
                         "slight")
        self.assertEqual((ent(env, "crate")["location"], robot(env)["hands"]), ("hall", {"left": None, "right": None}))
        self.assertEqual(stats.get("robot_drops", 0), 0)
        self.assertFalse(result["ok"])

    def test_an_overload_is_felt_in_realistic_mode(self):
        env = make(entities={"crate": thing("crate", 25.0, [0.5, 0.4, 0.4])}, perception="realistic")
        record = ent(env, "crate")
        self.assertLess(env.realism.knowledge.level("crate", record, "mass_kg"), LEVELS["touch"])
        result = env.step("pick_up", handle(env, "crate"))
        self.assertEqual((result["ok"], result["message"], result["category"]), (False, body.OVERLOAD_TEXT, "safety"))
        self.assertGreaterEqual(env.realism.knowledge.level("crate", record, "mass_kg"), LEVELS["touch"])

    def test_derates_above_the_head_on_a_climbing_aid_and_in_limp_mode(self):
        high = {"jar": thing("jar", 6.0, [0.2, 0.2, 0.25], z=1.75), "keg": thing("keg", 13.0, [0.4, 0.3, 0.4],
                                                                               pos=(2, 2), z=1.75)}
        env = make(entities=high)
        self.ok(env, "pick_up", "jar")                  # 6 kg above the head: one hand takes 4.8 kg, so both hands
        self.assertEqual(ent(env, "jar")["held_hand"], "both")
        self.ok(env, "place", "jar", cell=[0, 2])
        self.check(env, {"action": "pick_up", "target": "keg"}, body.OVERLOAD_TEXT, "safety")     # 13 > 12 kg
        stool = {"stool": {"label": "step stool", "location": "hall", "pos": [0, 0], "standing_height_m": 0.4},
                 "pack": thing("pack", 5.0, [0.3, 0.2, 0.2])}
        on_aid = make({"standing_on": "stool"}, entities=stool)
        self.ok(on_aid, "pick_up", "pack")              # on a stool a hand takes 4 kg: both hands
        self.assertEqual(ent(on_aid, "pack")["held_hand"], "both")
        limp = make({"battery": 4.0}, entities={"tub": thing("tub", 9.0, [0.4, 0.3, 0.3]),
                                                "drum": thing("drum", 11.0, [0.4, 0.4, 0.4], pos=(2, 2))})
        self.assertTrue(body.limp(limp.world.s))
        self.check(limp, {"action": "pick_up", "target": "drum"}, body.LIMP_LIFT_TEXT, "capability")
        self.assertEqual(limp.world.s["stats"].get("overload_attempts", 0), 0)    # the limp derate is no overload
        self.ok(limp, "pick_up", "tub")
        self.assertEqual(ent(limp, "tub")["held_hand"], "both")

    def test_the_total_carry_counts_what_the_other_hand_holds(self):
        things = {"tote": held("tote", "right", mass_kg=8.0, size_m=[0.4, 0.3, 0.3]),
                  "bag": thing("bag", 6.0, [0.3, 0.2, 0.3])}
        env = make(dict(hands_of("tote", "right"), max_mass_kg=12.0), entities=things)
        self.check(env, {"action": "pick_up", "target": "bag"}, body.OVERLOAD_TEXT, "safety")     # 14 > 12 kg
        roomy = make(hands_of("tote", "right"), entities=things)
        self.ok(roomy, "pick_up", "bag")
        self.assertEqual(robot(roomy)["hands"], {"left": "bag", "right": "tote"})

    def test_a_latent_weak_arm_is_found_by_the_first_lift_and_the_other_arm_lifts(self):
        weak = {"fault_state": {"arm_weak_right": {"kind": "arm_weak", "part": "right", "severity": 0.5,
                                                   "latent": True}}}
        env = make(weak, entities={"kettlebell": thing("kettlebell", 6.0, [0.2, 0.2, 0.25])})
        self.assertFalse(robot(env)["fault_state"]["arm_weak_right"]["detected"])
        self.check(env, {"action": "pick_up", "target": "kettlebell", "hand": "right"},
                   body.WEAK_ARM_TEXT.format("right"), "capability")
        record = robot(env)["fault_state"]["arm_weak_right"]
        self.assertEqual((record["detected"], record["latent"]), (True, False))           # it now knows
        self.assertEqual(env.world.s["stats"]["overload_attempts"], 1)
        self.check(env, {"action": "pick_up", "target": "kettlebell", "hand": "right"}, body.OVERLOAD_TEXT, "safety")
        self.ok(env, "pick_up", "kettlebell", hand="left")
        self.assertEqual(ent(env, "kettlebell")["held_hand"], "left")

    def test_a_known_weak_arm_hands_the_lift_to_the_arm_that_can_take_it(self):
        """Stage-B review (body, medium): with a detected weak right arm, pick_up without a hand still chose the right
        hand, was refused as an overload and counted (overload_attempts 2 after a retry) although the free left hand,
        or both hands, could take the load. Without a hand named, the plan takes the hand the robot believes can lift
        it; a latent weakness is still found by the first lift (the robot believes the arm is fine)."""
        def weak(latent, severity=0.5, side="right"):
            return {"fault_state": {"arm_weak_" + side: {"kind": "arm_weak", "part": side, "severity": severity,
                                                         "latent": latent}}}
        env = make(weak(False), entities={"pot": thing("pot", 6.0, [0.25, 0.25, 0.2])})
        self.ok(env, "pick_up", "pot")
        self.assertEqual(ent(env, "pot")["held_hand"], "left")
        self.assertNotIn("overload_attempts", env.world.s["stats"])
        both = make({"fault_state": dict(weak(False)["fault_state"], **weak(False, side="left")["fault_state"])},
                    entities={"pot": thing("pot", 6.0, [0.25, 0.25, 0.2])})
        self.ok(both, "pick_up", "pot")                               # 4 kg a hand, 10 kg with both
        self.assertEqual(ent(both, "pot")["held_hand"], "both")
        latent = make(weak(True), entities={"pot": thing("pot", 6.0, [0.25, 0.25, 0.2])})
        self.check(latent, {"action": "pick_up", "target": "pot"}, body.WEAK_ARM_TEXT.format("right"), "capability")
        self.ok(latent, "pick_up", "pot")                             # now known: the left hand takes it
        self.assertEqual(ent(latent, "pot")["held_hand"], "left")
        self.assertEqual(latent.world.s["stats"]["overload_attempts"], 1)


# ----------------------------------------------------------------------
# grasp size classes (TIER1_EMBODIMENT 4.8)

class GraspTests(CoreTestCase):
    def things(self):
        return {"bead": thing("bead", 0.002, [0.01, 0.01, 0.01]), "pin": thing("pin", 0.001, [0.0015] * 3, pos=(1, 2)),
                "letter": thing("letter", 0.005, [0.21, 0.297, 0.0002], pos=(2, 2))}

    def test_small_things_are_pinched_and_too_small_ones_refused(self):
        env = make(entities=self.things())
        self.check(env, {"action": "pick_up", "target": "bead", "grip": "normal"}, body.POWER_TOO_SMALL_TEXT,
                   "capability")
        self.check(env, {"action": "pick_up", "target": "pin"}, body.TOO_SMALL_TEXT, "capability")
        result = self.ok(env, "pick_up", "bead")
        self.assertIn("pinch grasp", result["message"])
        self.assertEqual(ent(env, "bead")["held_grip"], "pinch")     # every domain keeps the planner's grip (adoption)
        self.ok(env, "place", "bead", cell=[2, 0])
        self.assertTrue(self.ok(env, "pick_up", "bead", grip="pinch")["message"].startswith("Picked up bead"))

    def test_a_paper_is_slid_to_the_edge_by_a_hand_and_needs_edges_with_a_parallel_gripper(self):
        env = make(entities=self.things())
        before = env.world.s["time_s"]
        result = self.ok(env, "pick_up", "letter")
        self.assertIn("slid it to the edge of the surface to lift it", result["message"])
        self.assertAlmostEqual(env.world.s["time_s"] - before, 2.0 + 3.0)
        self.assertEqual(ent(env, "letter")["held_grip"], "edges")
        jaws = make({"body_profile": "mobile_manipulator"}, entities=self.things())
        self.check(jaws, {"action": "pick_up", "target": "letter"}, body.THIN_FLAT_TEXT, "capability")
        self.ok(jaws, "pick_up", "letter", grip="edges")
        self.ok(jaws, "pick_up", "bead", grip="normal")          # a parallel gripper wraps 5 mm and up

    def test_a_refused_grasp_teaches_the_size_in_realistic_mode(self):
        env = make(entities=self.things(), perception="realistic")
        record = ent(env, "pin")
        result = env.step("pick_up", handle(env, "pin"))
        self.assertEqual((result["ok"], result["message"]), (False, body.TOO_SMALL_TEXT))
        self.assertGreaterEqual(env.realism.knowledge.level("pin", record, "size_m"), LEVELS["touch"])


# ----------------------------------------------------------------------
# receive and uninstall lift like pick_up

class ReceiveUninstallTests(CoreTestCase):
    def offered(self, eid, mass, size, **extra):
        return dict({"label": eid, "location": "p1", "relation": "held", "portable": True, "mass_kg": mass,
                     "size_m": size, "offered": True}, **extra)

    def people(self):
        return {"p1": {"label": "resident", "role": "adult", "location": "hall", "pos": [2, 1]}}

    def test_receive_takes_a_large_item_with_both_hands_within_capacity(self):
        things = {"parcel": self.offered("parcel", 12.0, [0.5, 0.4, 0.3]),
                  "safe": self.offered("safe", 30.0, [0.5, 0.5, 0.5]), "ring": self.offered("ring", 0.01, [0.012] * 3)}
        env = make(people=self.people(), entities=things)
        self.check(env, {"action": "receive", "target": "safe"}, body.OVERLOAD_TEXT, "safety")
        self.assertEqual((ent(env, "safe")["location"], env.world.s["stats"]["overload_attempts"]), ("p1", 1))
        self.ok(env, "receive", "parcel")
        self.assertEqual((ent(env, "parcel")["held_hand"], robot(env)["hands"]), ("both", {"left": "parcel",
                                                                                         "right": "parcel"}))
        busy = make(hands_of("mug", "right"), people=self.people(),
                    entities=dict(things, mug=held("mug", "right", mass_kg=0.3, size_m=[0.09, 0.09, 0.1])))
        self.check(busy, {"action": "receive", "target": "parcel"}, core.LIFT_BOTH_HANDS, "capability")
        result = self.ok(busy, "receive", "ring")
        self.assertIn("pinch grasp", result["message"])
        self.assertEqual(robot(busy)["hands"]["left"], "ring")
        fallen = make({"fallen": True, "posture": "lying"}, people=self.people(), entities=things)
        self.check(fallen, {"action": "receive", "target": "ring"}, "The robot has fallen; recover first", "capability")

    def device(self, part_mass, part_size):
        return {"grill": {"label": "grill", "location": "hall", "pos": [2, 1], "z_m": 0.0, "size_m": [0.5, 0.5, 1.0],
                          "slots": {"tank": {"accepts": ["propane_tank"], "item": "tank"}}},
                "tank": {"label": "tank", "location": "grill", "relation": "part", "slot": "tank", "portable": True,
                         "part_type": "propane_tank", "mass_kg": part_mass, "size_m": part_size}}

    def test_uninstall_brings_a_heavy_part_out_into_both_hands(self):
        env = make(entities=self.device(17.0, [0.3, 0.3, 0.45]))
        self.ok(env, "uninstall", "grill", slot="tank")
        self.assertEqual((ent(env, "tank")["held_hand"], robot(env)["hands"]), ("both", {"left": "tank",
                                                                                       "right": "tank"}))
        self.ok(env, "install", "tank", "grill", slot="tank")
        self.assertEqual(robot(env)["hands"], {"left": None, "right": None})
        mug = held("mug", "left", mass_kg=0.3, size_m=[0.09, 0.09, 0.1])
        busy = make(hands_of("mug", "left"), entities=dict(self.device(17.0, [0.3, 0.3, 0.45]), mug=mug))
        self.check(busy, {"action": "uninstall", "target": "grill", "slot": "tank"}, core.LIFT_BOTH_HANDS, "capability")
        light = make(hands_of("mug", "left"), entities=dict(self.device(1.0, [0.2, 0.1, 0.1]), mug=mug))
        self.ok(light, "uninstall", "grill", slot="tank", hand="left")     # the preferred hand is busy: the other one
        self.assertEqual(robot(light)["hands"], {"left": "mug", "right": "tank"})
        heavy = make(entities=self.device(28.0, [0.4, 0.4, 0.6]))
        self.check(heavy, {"action": "uninstall", "target": "grill", "slot": "tank"}, body.OVERLOAD_TEXT, "safety")
        self.assertEqual(ent(heavy, "grill")["slots"]["tank"]["item"], "tank")


# ----------------------------------------------------------------------
# the held-load safety net (TIER1_EMBODIMENT 4.7) and load shares

class SafetyNetTests(CoreTestCase):
    def setUp(self):
        def fill(ctx):
            ctx.w.mut(ctx.args["target"])["mass_kg"] = ctx.args["kg"]
            return "filled"
        self.register("_t_fill", fill, params={"target": "entity", "kg": "number"}, duration=1.0)
        self.register("_t_hold", lambda ctx: "held on", duration=1.0)

    def test_the_safety_net_is_on(self):
        self.assertTrue(body.HOLD_SAFETY_NET)

    def test_a_load_that_outgrows_its_hand_strains_then_slips(self):
        bucket = held("bucket", "right", mass_kg=7.0, size_m=[0.3, 0.3, 0.3])
        env = make(hands_of("bucket", "right"), entities={"bucket": bucket})
        self.assertNotIn("Straining", self.ok(env, "_t_fill", "bucket", kg=7.9)["message"])
        result = self.ok(env, "_t_fill", "bucket", kg=10.0)
        self.assertIn("Straining to hold bucket", result["message"])
        self.assertEqual(robot(env)["hands"]["right"], "bucket")
        result = self.ok(env, "_t_fill", "bucket", kg=13.0)                  # 1.625 x one hand's 8 kg
        self.assertIn("Lost hold of bucket", result["message"])
        self.assertEqual((robot(env)["hands"]["right"], ent(env, "bucket")["location"]), (None, "hall"))
        self.assertEqual(env.world.s["stats"]["robot_drops"], 1)
        self.assertEqual(robot(env)["_held_kg"], 0.0)

    def test_with_the_handle_law_a_bag_past_its_handle_rating_tears_instead_of_slipping(self):
        """Adoption-round request (cleaning 8): the safety net runs before the settle pass, so with the handle law on
        an overloaded liner (12 kg of trash, a 5 kg handle) slipped out whole instead of tearing. The net now leaves
        it to the handle law: the handle tears, the trash falls, the robot keeps the torn bag."""
        from house_sim.kernel.phys import lawbook
        def scene():
            bag = held("bag", "right", mass_kg=0.1, size_m=[0.4, 0.3, 0.6], container=True, accepts_items=True,
                       handle_rating_kg=5.0, material="film")
            trash = {"type": "generic_item", "label": "trash", "location": "bag", "relation": "in", "mass_kg": 4.0,
                     "size_m": [0.3, 0.2, 0.3]}
            return make(hands_of("bag", "right"), entities={"bag": bag, "trash": trash})
        env = scene()
        with lawbook.forced(handle=True):
            result = self.ok(env, "_t_fill", "trash", kg=12.0)
        self.assertNotIn("Lost hold", result["message"])
        self.assertEqual(robot(env)["hands"]["right"], "bag")
        self.assertEqual(ent(env, "trash")["location"], "hall")                  # through the torn bag
        self.assertTrue(any(e["kind"] == "phys.tear" for e in env.world.s["event_log"]), env.world.s["event_log"][-5:])
        deferred = scene()
        result = self.ok(deferred, "_t_fill", "trash", kg=12.0)                 # the law deferred: the net as before
        self.assertIn("Lost hold of bag", result["message"])

    def test_a_held_load_is_not_dropped_by_limp_mode_or_a_climbing_aid(self):
        """Stage-B review (body, high): the safety net applied the lift-off derates (limp x0.5, climbing aid x0.5) to
        what the hands already held, so crossing 5 % on the gauge or stepping onto a stool dropped and broke a 6.5 kg
        pot (robot-caused breakage). Those derates limit new lifts; a closed grip holds what the arm can hold."""
        pot = thing("ceramic pot", 6.5, [0.25, 0.25, 0.2], type="pot", material="ceramic")
        env = make({"battery": 5.03}, entities={"pot": pot})
        self.ok(env, "pick_up", "pot")
        for cell in ([10, 1], [2, 1], [10, 1]):
            result = self.ok(env, "walk_to", cell=cell)
            self.assertNotIn("Lost hold", result["message"])
        self.assertTrue(body.limp(env.world.s))
        self.assertEqual(robot(env)["hands"]["right"], "pot")
        self.assertFalse(ent(env, "pot").get("broken"))
        self.assertNotIn("robot_drops", env.world.s["stats"])
        self.assertEqual(env.evaluate()["failures"], [])
        # new lifts still feel limp mode: a second 6.5 kg pot is refused
        self.assertLess(body.capacity_kg(env.world.s, "left"), 6.5)
        self.assertEqual(body.hold_capacity_kg(env.world.s, "right"), 8.0)
        stool = {"label": "step stool", "location": "hall", "pos": [2, 2], "standing_height_m": 0.23}
        perched = make(dict(hands_of("pot", "right"), standing_on="stool"),
                       entities={"pot": held("pot", "right", mass_kg=6.5, size_m=[0.25, 0.25, 0.2],
                                             material="ceramic"), "stool": stool})
        result = self.ok(perched, "_t_hold")
        self.assertNotIn("Lost hold", result["message"])
        self.assertEqual(robot(perched)["hands"]["right"], "pot")

    def test_a_dropped_load_falls_from_where_the_hands_are(self):
        """Stage-B review (body, low): safety-net and slip drops fell from the standing hand height whatever the
        posture or the aid the robot stood on."""
        bucket = held("bucket", "right", mass_kg=7.0, size_m=[0.3, 0.3, 0.3])
        heights = {}
        for posture, extra in (("standing", {}), ("kneeling", {}), ("standing", {"standing_on": "stool"})):
            entities = {"bucket": dict(bucket),
                        "stool": {"label": "step stool", "location": "hall", "pos": [1, 1], "standing_height_m": 0.23}}
            env = make(dict(hands_of("bucket", "right"), posture=posture, **extra), entities=entities)
            expected = body.hand_drop_m(env.world.s)
            self.ok(env, "_t_fill", "bucket", kg=13.0)
            fall = [e for e in env.world.s["event_log"] if e["kind"] == "phys.fall" and e["target"] == "bucket"]
            self.assertEqual(fall[0]["height_m"], expected, (posture, extra))
            heights[(posture, bool(extra))] = expected
        self.assertEqual(heights[("standing", False)], 1.0)
        self.assertEqual(heights[("kneeling", False)], 0.4)               # 1.0 x 0.60 / 1.50
        self.assertEqual(heights[("standing", True)], 1.23)

    def test_a_co_carried_end_and_a_towed_cart_count_only_the_robots_share(self):
        people = {"p1": {"label": "resident", "role": "adult", "location": "hall", "pos": [3, 1]}}
        dresser = held("dresser", "both", mass_kg=40.0, size_m=[1.0, 0.5, 1.2], co_held_by=["robot", "p1"])
        env = make(hands_of("dresser", "both"), people=people, entities={"dresser": dresser})
        result = self.ok(env, "_t_hold")
        self.assertNotIn("Lost hold", result["message"])
        self.assertEqual((robot(env)["_held_kg"], robot(env)["hands"]["left"]), (20.0, "dresser"))
        self.assertEqual(body.load_share(env.world.s, "dresser"), 0.5)
        alone = make(hands_of("dresser", "both"), entities={"dresser": dict(dresser, co_held_by=None)})
        self.assertIn("Lost hold of dresser", self.ok(alone, "_t_hold")["message"])        # 40 kg is 2 x 20 kg
        cart = held("cart", "both", mass_kg=60.0, size_m=[0.6, 0.7, 1.0], towed=True)
        towing = make(hands_of("cart", "both"), entities={"cart": cart})
        self.assertNotIn("Lost hold", self.ok(towing, "_t_hold")["message"])
        self.assertAlmostEqual(robot(towing)["_held_kg"], 60.0 * body.TOWED_SHARE)
        self.assertAlmostEqual(body.carried_kg(towing.world), 60.0 * body.TOWED_SHARE)


# ----------------------------------------------------------------------
# sensors: cameras, microphones, speaker, localization (test plan 22; TIER1_INTEGRATION 1.3, 1.15)

class SensorTests(CoreTestCase):
    def things(self):
        return {"note": thing("note", 0.01, [0.1, 0.1, 0.01], label_text="Back at five")}

    def test_look_inspect_and_read_need_the_colour_cameras(self):
        # privacy mode: navigation_robot's own inspect / read guard answers first with its wording when that domain
        # is loaded (an adoption-round item), so privacy mode is checked on look here
        self.check(make({"privacy_mode": True}, entities=self.things()), {"action": "look"}, IMAGING_OFF_TEXT,
                   "perception")
        cases = ((dict(camera_on=False), IMAGING_OFF_TEXT, "perception"),
                 (dict(privacy_policy={"camera_off": ["hallway"], "when": "always"}), IMAGING_OFF_TEXT, "perception"),
                 (dict(sensors={"camera": False}), CAMERA_FAULT_TEXT, "capability"))
        for robot_cfg, message, category in cases:
            env = make(robot_cfg, entities=self.things())
            self.check(env, {"action": "look"}, message, category)
            self.check(env, {"action": "inspect", "target": "note"}, message, category)
            self.check(env, {"action": "read", "target": "note"}, message, category)
        env = make(entities=self.things())
        self.assertEqual(self.seconds(env, "look"), 1.0)
        self.ok(env, "inspect", "note")
        self.assertIn("Back at five", self.ok(env, "read", "note")["message"])
        geometric = make({"privacy_mode": True}, camera="geometric", entities=self.things())
        self.check(geometric, {"action": "look", "target": "around"}, IMAGING_OFF_TEXT, "perception")
        self.check(geometric, {"action": "peek", "target": "note", "where": "closely"}, IMAGING_OFF_TEXT, "perception")

    def test_look_in_a_lit_room_finds_a_lost_pose_again(self):
        lost = {"fault_state": {"localization_lost": {"kind": "localization_lost"}}}
        env = make(lost)
        self.assertFalse(robot(env)["localized"])
        self.check(env, {"action": "walk_to", "cell": [3, 1]}, body.LOCALIZATION_TEXT, "capability")
        result = self.ok(env, "look")
        self.assertIn("Relocalized", result["message"])
        self.assertTrue(robot(env)["localized"])
        self.assertIsNotNone(robot(env)["fault_state"]["localization_lost"]["cleared_s"])
        self.assertIn("relocalized_s", robot(env)["body_log"])
        self.ok(env, "walk_to", cell=[3, 1])
        dark = make(lost)
        dark.world.s["entities"]["hall_light"]["on"] = False
        self.assertIn("Too dark to relocalize", self.ok(dark, "look")["message"])
        self.assertFalse(robot(dark)["localized"])

    def messages(self, env):
        env.world.s["messages"].extend([
            {"step": 0, "time_s": 0.0, "speaker": "p1", "kind": "speech", "text": "Lovely weather", "room": "hall"},
            {"step": 0, "time_s": 0.0, "speaker": "p1", "kind": "speech", "text": "Robot, stop", "to": "robot",
             "room": "hall"}])

    def test_listen_and_ask_need_the_microphones_and_privacy_mode_hears_only_addressed_speech(self):
        people = {"p1": {"label": "resident", "role": "adult", "location": "hall", "pos": [3, 1]}}
        for robot_cfg, message in ((dict(mic_on=False), MIC_OFF_TEXT), (dict(sensors={"mics": False}), MIC_FAULT_TEXT)):
            env = make(robot_cfg, people=people)
            self.check(env, {"action": "listen"}, message, "capability")
            self.check(env, {"action": "ask", "target": "p1", "topic": "anything"}, message, "capability")
        env = make(people=people)
        self.messages(env)
        heard = self.ok(env, "listen")["message"]
        self.assertIn("Lovely weather", heard)
        self.assertIn("Robot, stop", heard)
        private = make({"privacy_mode": True}, people=people)
        self.messages(private)
        heard = self.ok(private, "listen")["message"]
        self.assertNotIn("Lovely weather", heard)
        self.assertIn("Robot, stop", heard)
        self.ok(private, "ask", "p1", topic="anything")                    # an answer is addressed speech

    def test_speech_through_a_failed_speaker_is_inaudible_and_reveals_the_fault(self):
        people = {"p1": {"label": "resident", "role": "adult", "location": "hall", "pos": [3, 1]}}
        env = make(people=people)
        self.ok(env, "speak", text="Hello")
        entry = said(env)
        self.assertEqual((entry["audible"], entry["audience"]), (True, ["p1"]))
        broken = make({"fault_state": {"speaker_failed": {"kind": "speaker_failed", "latent": True}}}, people=people)
        self.assertFalse(robot(broken)["fault_state"]["speaker_failed"]["detected"])
        self.ok(broken, "speak", text="Hello")                              # the robot cannot tell beforehand
        entry = said(broken)
        self.assertEqual((entry["audible"], entry["audience"]), (False, []))
        self.assertTrue(robot(broken)["fault_state"]["speaker_failed"]["detected"])   # no echo heard


# ----------------------------------------------------------------------
# speed, stairs and the camera verbs' guards (test plan 16, 19, 23)

class SpeedTests(CoreTestCase):
    def test_careful_speed_does_not_stretch_a_commanded_duration(self):
        """Stage-B review (body, medium): careful speed multiplied every physical leaf's own seconds, so a 30 s
        hold_steady lasted 46.5 s while its message said 30 s. A verb whose duration the command states declares
        timing="commanded" (the domains' hold, game, blend verbs: adoption round) and keeps it."""
        self.register("_t_hold_for", lambda ctx: "held", params={"hold_s": "number"},
                      duration=lambda ctx: float(ctx.args["hold_s"]), category="physical", timing="commanded")
        self.register("_t_fiddle", lambda ctx: "fiddled", duration=4.0, category="physical")
        normal, careful = make(), make({"speed_mode": "careful"})
        self.assertEqual(self.seconds(normal, "_t_hold_for", hold_s=30.0), 30.0)
        self.assertEqual(self.seconds(careful, "_t_hold_for", hold_s=30.0), 30.0)
        self.assertEqual(self.seconds(careful, "_t_fiddle"), 6.0)         # a motion still slows by half
        self.assertEqual(body.careful_seconds(careful.world.s, ACTIONS["_t_hold_for"], 30.0), 30.0)

    def test_careful_speed_slows_a_walk_once_and_a_fixed_action_by_half(self):
        box = {"box": thing("box", 2.0, [0.3, 0.2, 0.2])}
        self.assertEqual(self.seconds(make(entities=box), "walk_to", cell=[9, 1]), 4.0 / 0.8 + 0.2)
        careful = make({"speed_mode": "careful"}, entities=box)
        self.assertEqual(self.seconds(careful, "walk_to", cell=[9, 1]), 4.0 / 0.4 + 0.2)    # 2x, not 3x
        self.assertEqual(self.seconds(careful, "walk_to", cell=[1, 1]), 4.0 / 0.4 + 0.2)
        self.assertEqual(self.seconds(careful, "pick_up", "box"), 3.0)                      # 2 s x 1.5

    def test_a_walk_at_the_requested_speed_is_not_slowed_by_the_robot_wide_setting(self):
        careful = make({"speed_mode": "careful"})
        self.assertEqual(self.seconds(careful, "walk_to", cell=[9, 1], speed="normal"), 4.0 / 0.8 + 0.2)

    def test_nested_leaves_take_their_careful_time_once(self):
        def chain(ctx):
            ctx.env.perform({"action": "pick_up", "target": "box"}, ctx)
            ctx.env.perform({"action": "walk_to", "cell": [5, 1]}, ctx)
            return "done"
        self.register("_t_chain", chain, duration=1.0, category="physical")
        box = {"box": thing("box", 2.0, [0.3, 0.2, 0.2])}
        self.assertEqual(self.seconds(make(entities=box), "_t_chain"), 1.0 + 2.0 + (2.0 / 0.8 + 0.2))
        careful = make({"speed_mode": "careful"}, entities=box)
        # own 1 s x 1.5, the nested pick_up 2 s x 1.5, the nested walk at careful speed (timing "speed": not again)
        self.assertEqual(self.seconds(careful, "_t_chain"), 1.5 + 3.0 + (2.0 / 0.4 + 0.2))
        self.assertEqual(body.careful_seconds(careful.world.s, ACTIONS["_t_chain"], 10.0, 4.0), 4.0 + 6.0 * 1.5)
        self.assertEqual(body.careful_seconds(careful.world.s, ACTIONS["look"], 10.0), 10.0)    # not physical
        self.assertEqual(body.careful_seconds(careful.world.s, ACTIONS["move"], 10.0), 10.0)    # timing "speed"

    def test_the_motion_verbs_are_timed_by_their_speed(self):
        for name in ("move", "approach", "walk_to"):
            self.assertEqual(ACTIONS[name].timing, "speed", name)
        for name in ("step", "go_to", "follow", "lead", "patrol"):
            if name in ACTIONS:                         # navigation_robot's (NAV-M5), loaded with every domain
                self.assertEqual(ACTIONS[name].timing, "speed", name)
        self.assertEqual((ACTIONS["pick_up"].timing, ACTIONS["face"].timing), ("fixed", "fixed"))

    def test_set_speed_fast_is_refused_while_quiet_or_limp(self):
        self.check(make({"quiet": True}), {"action": "set_speed", "target": "fast"}, body.QUIET_FAST_TEXT,
                   "precondition")
        self.check(make({"legs_health": 0.5}), {"action": "set_speed", "target": "fast"}, body.LEGS_FAST_TEXT,
                   "precondition")
        self.check(make({"battery": 4.0}), {"action": "set_speed", "target": "fast"}, body.LIMP_FAST_TEXT,
                   "precondition")
        self.ok(make({"quiet": True}), "set_speed", "careful")
        env = make({"speed_mode": "fast"})
        self.assertIn("normal speed while quiet", self.ok(env, "set_quiet", "on")["message"])
        self.assertEqual(robot(env)["speed_mode"], "fast")                   # the user's setting stays
        result = self.ok(env, "walk_to", cell=[9, 1])
        self.assertIn("Quiet mode: walking at normal speed", result["message"])
        self.ok(env, "set_quiet", "off")
        self.assertEqual(self.seconds(env, "walk_to", cell=[1, 1]), round(4.0 / 1.2 + 0.2, 3))

    def test_weak_legs_and_a_critical_battery_walk_carefully(self):
        weak = make({"legs_health": 0.5})
        result = weak.step("walk_to", cell=[9, 1])
        self.assertIn("Weak legs: walking at careful speed", result["message"])
        self.assertEqual(round(weak.world.s["time_s"], 3), 4.0 / 0.4 + 0.2)
        self.assertEqual(core._travel_seconds(type("C", (), {"w": weak.world, "args": {"speed": "fast"}})(), 2.0), 5.0)
        wheels = make({"body_profile": "mobile_manipulator"})
        self.assertEqual(self.seconds(wheels, "walk_to", cell=[9, 1]), round(4.0 / 0.6 + 0.2, 3))   # its own speed
        self.assertEqual(core.SPEEDS, {"careful": 0.4, "normal": 0.8, "fast": 1.2})

    def test_stairs_need_a_body_that_climbs_them(self):
        self.check(make({"body_profile": "mobile_manipulator", "pos": [COLS - 1, 0]}, stairs=True),
                   {"action": "move", "target": "kitchen"}, body.NO_STAIRS_TEXT, "capability")
        self.check(make({"legs_health": 0.5, "pos": [COLS - 1, 0]}, stairs=True),
                   {"action": "move", "target": "kitchen"}, body.WEAK_LEGS_STAIRS_TEXT, "capability")
        self.ok(make({"pos": [COLS - 1, 0]}, stairs=True), "move", "kitchen")
        self.ok(make({"body_profile": "mobile_manipulator", "pos": [COLS - 1, 0]}), "move", "kitchen")   # no stairs

    def test_face_and_peek_use_the_bodys_guards(self):
        box = {"box": thing("box", 2.0, [0.3, 0.2, 0.2])}
        self.check(make({"fallen": True, "posture": "lying"}, camera="geometric"),
                   {"action": "face", "target": "north"}, core.CANNOT_MOVE, "capability")
        self.check(make({"actuator_ok": False}, camera="geometric"), {"action": "face", "target": "north"},
                   core.CANNOT_MOVE, "capability")
        stool = {"stool": {"label": "step stool", "location": "hall", "pos": [0, 0], "standing_height_m": 0.4}}
        self.check(make({"standing_on": "stool"}, camera="geometric", entities=stool),
                   {"action": "face", "target": "north"}, "Step down first", "safety")
        lost = {"fault_state": {"localization_lost": {"kind": "localization_lost"}}}
        self.ok(make(lost, camera="geometric"), "face", "north")             # turning in place needs no pose
        self.check(make({"actuator_ok": False}, camera="geometric", entities=box),
                   {"action": "peek", "target": "box", "where": "closely"}, "Actuator fault; request human assistance",
                   "capability")
        self.check(make({"arm_health": {"left": 0.0, "right": 0.0}}, camera="geometric", entities=box),
                   {"action": "peek", "target": "box", "where": "closely"}, "Both arms have failed; request help",
                   "capability")
        self.check(make({"sensors": {"camera": False}}, camera="geometric", entities=box),
                   {"action": "peek", "target": "box", "where": "closely"}, CAMERA_FAULT_TEXT, "capability")


class NavigationTimingTests(CoreTestCase):
    """NAV-M5: navigation_robot's walking verbs are timed by their speed (only with that domain loaded)."""

    def setUp(self):
        if "step" not in ACTIONS or "go_to" not in ACTIONS:
            self.skipTest("navigation_robot is not loaded")

    def test_a_careful_step_and_go_to_are_slowed_once(self):
        self.assertEqual(self.seconds(make(), "step", "e"), round(0.5 / 0.8 * 1.2, 3))
        self.assertEqual(self.seconds(make({"speed_mode": "careful"}), "step", "e"), round(0.5 / 0.4 * 1.2, 3))
        self.assertEqual(self.seconds(make({"body_profile": "mobile_manipulator"}), "step", "e"),
                         round(0.5 / 0.6 * 1.2, 3))                          # the profile's own walking speed
        walk = self.seconds(make({"speed_mode": "careful"}), "move", "kitchen")
        trip = self.seconds(make({"speed_mode": "careful"}), "go_to", "kitchen")
        self.assertAlmostEqual(trip, walk + 0.5, places=3)                    # not (walk + 0.5) x 1.5


# ----------------------------------------------------------------------
# setup ops (test plan 24; TIER1_INTEGRATION 1.13, TIER1_EMBODIMENT 5.8)

def built(ops, seed=0, cfg=None):
    cfg = copy.deepcopy(cfg or config())
    bindings = apply_setup(cfg, ops, default_registry(), seed, {"catalog_id": "T-body-core"})
    return cfg, bindings


class SetupTests(CoreTestCase):
    def test_the_robot_op_applies_the_profile_then_overrides_then_sensors_then_props(self):
        cfg, _ = built([{"op": "robot", "profile": "humanoid_compact", "profile_overrides": {"max_hand_kg": 5.0,
                                                                                            "push_n": 140.0},
                         "sensors": {"camera_on": False, "privacy_policy": {"mic_off": ["bathroom"]}},
                         "props": {"max_mass_kg": 9.0}}])
        env = HouseEnv(cfg, camera="room")
        r = robot(env)
        self.assertEqual((r["body_profile"], r["mass_kg"], r["battery_wh"], r["max_hand_kg"], r["max_mass_kg"]),
                         ("humanoid_compact", 35.0, 1000.0, 5.0, 9.0))
        self.assertEqual(body.limit(env.world.s, "push_n"), 140.0)
        self.assertEqual((r["camera_on"], r["privacy_policy"]), (False, {"mic_off": ["bathroom"]}))
        self.check(env, {"action": "look"}, IMAGING_OFF_TEXT, "perception")
        for bad in ({"op": "robot", "profile": "android"}, {"op": "robot", "profile_overrides": {"grippers": {"left": "claw"}}},
                    {"op": "robot", "profile_overrides": []}, {"op": "robot", "sensors": {"camera": False}},
                    {"op": "robot", "sensors": {"mic_on": "no"}},
                    {"op": "robot", "sensors": {"privacy_policy": {"camera_off": "bathroom"}}},
                    {"op": "robot", "sensor": {"camera_on": False}}):
            with self.assertRaises(SpecError, msg=bad):
                built([bad])

    def test_a_robot_fault_at_reset_is_applied_by_normalize(self):
        cfg, _ = built([{"op": "robot_fault", "kind": "arm_weak", "part": "left", "severity": 0.4, "latent": True}])
        env = HouseEnv(cfg, camera="room")
        record = robot(env)["fault_state"]["arm_weak_left"]
        self.assertEqual((record["latent"], record["detected"], record["source"], record["severity"]),
                         (True, False, "setup", 0.4))
        self.assertEqual(robot(env)["arm_health"]["left"], 0.4)
        cfg, _ = built([{"op": "robot_fault", "kind": "water_ingress"}])
        r = robot(HouseEnv(cfg, camera="room"))
        self.assertEqual((r["sensors"]["camera"], r["actuator_ok"]), (False, False))     # its onset consequences
        self.assertEqual(sorted(r["fault_state"]), ["actuator_failed", "camera_failed", "water_ingress"])

    def test_a_timed_fault_starts_at_at_s_and_ends_at_until_s(self):
        cfg, _ = built([{"op": "robot_fault", "kind": "camera_dirty", "severity": 0.3, "at_s": 60, "until_s": 600},
                        {"op": "robot_fault", "kind": "network_down", "until_s": 300}])
        env = HouseEnv(cfg, camera="room")
        self.assertEqual((robot(env)["camera_clean"], robot(env)["network"]), (1.0, False))
        self.ok(env, "wait", seconds=90)
        self.assertEqual(robot(env)["camera_clean"], 0.3)
        self.assertTrue(robot(env)["fault_state"]["camera_dirty"]["detected"])
        self.ok(env, "wait", seconds=300)
        self.assertTrue(robot(env)["network"])                                  # the network came back at 300 s
        self.ok(env, "wait", seconds=300)
        self.assertIsNotNone(robot(env)["fault_state"]["camera_dirty"]["cleared_s"])

    def test_a_chosen_fault_is_drawn_with_the_setup_rng_and_bound(self):
        options = ["camera_dirty", "network_down", "arm_weak:left", "none"]
        seen = {}
        for seed in range(12):
            cfg, bindings = built([{"op": "robot_fault", "var": "fault", "choose": options, "latent": False}], seed)
            again, rebound = built([{"op": "robot_fault", "var": "fault", "choose": options}], seed)
            self.assertEqual((bindings["fault"], bindings["fault_id"]), (rebound["fault"], rebound["fault_id"]))
            seen[bindings["fault"]] = bindings["fault_id"]
            states = (cfg["robot"].get("fault_state") or {})
            self.assertEqual(sorted(states), [] if bindings["fault"] == "none" else [bindings["fault_id"]])
        self.assertEqual(seen, {"camera_dirty": "camera_dirty", "network_down": "network_down",
                                "arm_weak:left": "arm_weak_left", "none": None})

    def test_robot_fault_validation(self):
        hold = {"op": "robot", "hold": {"left": "mug"}}
        mug = config(entities={"mug": thing("mug", 0.3, [0.09, 0.09, 0.1])})
        for bad in ({"op": "robot_fault", "kind": "gremlins"}, {"op": "robot_fault", "kind": "arm_weak"},
                    {"op": "robot_fault", "kind": "camera_dirty", "latent": True},
                    {"op": "robot_fault", "kind": "arm_weak", "part": "left", "severity": 1.5},
                    {"op": "robot_fault", "kind": "network_down", "at_s": 100, "until_s": 50},
                    {"op": "robot_fault", "kind": "camera_dirty", "choose": ["none"]},
                    {"op": "robot_fault", "choose": ["arm_weak:middle"]}, {"op": "robot_fault", "choose": []},
                    {"op": "robot_fault", "kind": "camera_dirty", "when": 3}):
            with self.assertRaises(SpecError, msg=bad):
                built([bad])
        with self.assertRaises(SpecError):
            built([hold, {"op": "robot_fault", "kind": "arm_failed", "part": "left"}], cfg=mug)
        with self.assertRaises(SpecError):
            built([{"op": "robot_fault", "kind": "arm_failed", "part": "left"}, hold], cfg=mug)
        cfg, _ = built([{"op": "robot_fault", "kind": "arm_failed", "part": "left"}, {"op": "robot",
                                                                                     "hold": {"right": "mug"}}], cfg=mug)
        self.assertEqual(robot(HouseEnv(cfg, camera="room"))["arm_health"]["left"], 0.0)


# ----------------------------------------------------------------------
# skills, modes, legacy states, determinism, budget

class SkillTests(CoreTestCase):
    def test_a_skill_stops_when_the_robot_powers_down_on_the_way(self):
        per_m = 0.264 + 70.0 / 0.4 / 3600.0
        env = make({"battery": round(100.0 * per_m * 2.0 / 2400.0, 9)})
        runner = Runner(env, {}, strict=True)
        with self.assertRaises(PlanError) as caught:
            runner.skill("go_room", room="kitchen")
        self.assertEqual(str(caught.exception), "robot powered down")
        self.assertEqual(robot(env)["power_state"], "off")
        lenient = Runner(make({"battery": round(100.0 * per_m * 2.0 / 2400.0, 9)}), {}, strict=False)
        lenient.run([{"skill": "go_room", "room": "kitchen"}])         # a wrong plan goes on with its next step
        self.assertEqual(len(lenient.failures), 0)


class ModeAndLegacyTests(CoreTestCase):
    def test_the_same_script_is_deterministic_and_matches_in_both_perception_modes(self):
        things = {"box": thing("box", 12.0, [0.4, 0.3, 0.3]), "bead": thing("bead", 0.002, [0.01] * 3, pos=(1, 2))}

        def run(perception):
            env = make({"speed_mode": "careful"}, entities=things, perception=perception)
            results = []
            for command in (("pick_up", "box"), ("place", "box"), ("pick_up", "bead"), ("walk_to",), ("look",)):
                args = [handle(env, a) for a in command[1:]]
                kwargs = {"cell": [5, 2]} if command[0] == "walk_to" else {}
                if command[0] == "place":
                    kwargs = {"cell": [0, 2]}
                result = env.step(command[0], *args, **kwargs)
                results.append((result["ok"], result["category"]))
            return results, env.world.s["time_s"], digest(env.world.s["robot"]["hands"])
        oracle = run("oracle")
        self.assertEqual(oracle, run("oracle"))
        realistic = run("realistic")
        self.assertEqual([r[0] for r in realistic[0]], [r[0] for r in oracle[0]])
        self.assertTrue(all(ok for ok, _ in oracle[0]), oracle)

    def test_a_legacy_robot_record_without_the_body_fields_lifts_with_the_defaults(self):
        env = make(entities={"box": thing("box", 12.0, [0.4, 0.3, 0.3]), "crate": thing("crate", 25.0, [0.5, 0.4, 0.4],
                                                                                        pos=(2, 2))})
        legacy = {k: v for k, v in robot(env).items() if k in ("room", "pos", "hands", "battery", "posture")}
        env.world.s["robot"] = legacy
        self.ok(env, "pick_up", "box")
        self.assertEqual(ent(env, "box")["held_hand"], "both")
        self.ok(env, "place", "box", cell=[0, 2])
        self.check(env, {"action": "pick_up", "target": "crate"}, body.OVERLOAD_TEXT, "safety")


class PerformanceTests(CoreTestCase):
    def test_settlement_with_the_safety_net_while_carrying_stays_in_budget(self):
        from house_sim.kernel.actions import Ctx
        env = HouseEnv(seed=0, camera="room")
        state = env.world.s
        cups = sorted(e for e, r in state["entities"].items() if r.get("portable") and r.get("location") in
                      state["entities"] and not r.get("accepts_items"))[:2]
        env.world.begin()
        for hand, eid in zip(("left", "right"), cups):
            env.world.mut(eid).update(location="robot", relation="held", held_hand=hand)
            env.world.robot_m()["hands"][hand] = eid
        env.world.robot_m()["docked"] = False
        ctx = Ctx(env, ACTIONS["pick_up"], {"target": cups[0]}, None)
        best = None
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(100):
                body.settle_energy(env.world, ctx, 1.0, hold_check=True)
            took = (time.perf_counter() - start) / 100
            best = took if best is None else min(best, took)
        env.world.rollback()
        self.assertLess(best, 3 * 0.1e-3, best)                  # TIER1_INTEGRATION 3.4: 0.1 ms per leaf, 3x bound


if __name__ == "__main__":
    unittest.main()
