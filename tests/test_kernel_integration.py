"""Regression tests for the phase-1 integration (INT): kernel requests of the domain owners and the kernel agents that
the integrator applied (catalog/analysis/phase1/INT.json). Each test fails on the tree before the change.

  TRAIL     the action trail names the true id in realistic episodes (climate, tools_vehicles)
  CAPTURE   camera.capture_key is public (people, K-FIX)
  HOLDER    phys.liquids.holds_liquid honours watertight, holes, open weave, soft sides and side openings (food,
            plumbing_liquids)
  FIXED     ctx.fixed_rng mixes in the episode nonce (K-LEAK)
  OWNER     a private thing's owner asking for it is no privacy violation (admin_inventory)
  LOOKS     the physics inspector keeps a domain's own `looks` (plumbing_liquids)
  FIRSTOBS  the reference's reset observation counts as observed (people)
  BATTERY   hold power follows the load word, not the exact mass (K-LEAK L3)

Scenes use core types (generic_item, ceiling_light), so the file runs with HOUSE_SIM_DOMAINS=core too.
"""
import copy
import random
import unittest

from house_sim.kernel import camera
from house_sim.kernel.engine import HouseEnv

COLS, ROWS = 12, 10


def config(entities=None, pos=(1, 1), people=None, robot=None):
    """A lit 6 x 5 m living room ("lab") with a lit hallway next door."""
    ents = {"light_1": {"type": "ceiling_light", "location": "lab", "pos": [COLS - 1, 0], "on": True, "mounted": True,
                        "mount_height_m": 2.4},
            "light_2": {"type": "ceiling_light", "location": "hall", "pos": [1, 1], "on": True, "mounted": True,
                        "mount_height_m": 2.4}}
    ents.update(copy.deepcopy(entities or {}))
    rob = {"room": "lab", "pos": list(pos)}
    rob.update(copy.deepcopy(robot or {}))
    return {"schema_version": 2, "start_room": "lab",
            "rooms": {"lab": {"size_m": [COLS * 0.5, ROWS * 0.5], "type": "living_room", "exits": ["hall"]},
                      "hall": {"size_m": [2.0, 3.0], "type": "hallway", "exits": ["lab"]}},
            "entities": ents, "people": copy.deepcopy(people or {}), "pets": {}, "robot": rob}


def thing(location="lab", size=(0.12, 0.12, 0.3), **props):
    out = {"type": "generic_item", "location": location, "size_m": list(size), "label": props.pop("label", "thing")}
    out.update(props)
    return out


def env_of(entities=None, perception="realistic", seed=0, task=None, **kw):
    return HouseEnv(config(entities, **kw), task=task, seed=seed, perception=perception, camera="room")


def handle_of(env, eid):
    obs = env.observe()
    handle = env.realism.handles.by_id.get(eid)
    return handle if handle in (obs.get("visible_entities") or {}) else None


class ActionTrailTests(unittest.TestCase):
    """TRAIL: state["action_log"] (hidden) names the true id the agent's handle stands for, so domain attribution
    that reads the trail (cleaning's cycles, electrical's and tools_vehicles' robot_acted_on, mechanisms' lighting)
    sees the robot's actions in realistic episodes too."""

    def test_the_trail_names_the_true_id_in_realistic_mode(self):
        for perception in ("realistic", "oracle"):
            env = env_of({"box_1": thing(pos=[2, 1], label="box")}, perception=perception)
            target = handle_of(env, "box_1") if perception == "realistic" else "box_1"
            self.assertIsNotNone(target)
            self.assertTrue(env.step("inspect", target)["ok"])
            entry = env.world.s["action_log"][-1]
            self.assertEqual((entry["action"], entry["target"], entry["ok"]), ("inspect", "box_1", True), perception)

    def test_a_refused_unknown_handle_is_logged_as_typed(self):
        env = env_of({"box_1": thing(pos=[2, 1], label="box")})
        result = env.step("inspect", "obj_00000000")
        self.assertFalse(result["ok"])
        self.assertEqual(env.world.s["action_log"][-1]["target"], "obj_00000000")


class CaptureKeyTests(unittest.TestCase):
    def test_capture_key_is_public_and_keys_one_private_moment(self):
        """CAPTURE: the people domain keys its intrusions by the camera's capture key (one privacy violation per
        private moment in both camera modes); the former private name stays as an alias."""
        person = {"activity": "dressing", "activity_start_s": 3600.0}
        self.assertEqual(camera.capture_key(person, "resident_1"), "resident_1:dressing:3600")
        self.assertIs(camera._capture_key, camera.capture_key)
        self.assertEqual(camera.capture_key({"activity": "bathing"}, "p2"), "p2:bathing:-")


class LiquidHolderTests(unittest.TestCase):
    """HOLDER: the kernel's holder rule agrees with plumbing_liquids' verbs (review L3: a bread box, a colander, a wire
    egg basket and dishwasher racks pooled poured or spilled liquid)."""

    def state(self, **fields):
        rec = {"type": "generic_item", "location": "lab", "relation": "on", "material": "ceramic", "mass_kg": 0.5,
               "size_m": [0.3, 0.3, 0.2], "capacity_l": 3.0, "portable": True}
        rec.update(fields)
        return {"entities": {"x": rec}, "rooms": {"lab": {}}}

    def test_only_liquid_tight_open_holders_pool_liquid(self):
        from house_sim.kernel.phys import liquids
        holds = lambda **f: liquids.holds_liquid(self.state(**f), "x")
        self.assertTrue(holds())                                         # a ceramic bowl
        self.assertTrue(holds(material="wicker", watertight=True))       # a lined basket: explicit watertight
        for fields in ({"material": "wire"}, {"material": "nylon_coated_steel"}, {"holes_mm": 4.0},
                       {"perforated": True}, {"watertight": False}, {"soft_sided": True}, {"porous": True},
                       {"view": {"opening": "front"}}, {"view": {"opening": "side"}}, {"material": "rattan"},
                       {"open_shelving": True, "material": "metal"}):
            self.assertFalse(holds(**fields), fields)
        self.assertTrue(holds(view={"opening": "top"}))
        self.assertTrue(liquids.drains_through({"material": "wire"}))
        self.assertFalse(liquids.drains_through({"material": "wire", "watertight": True}))
        self.assertFalse(liquids.drains_through({"watertight": False}))   # leaks out of the front, not through

    def test_liquid_poured_into_an_open_weave_runs_through_to_the_floor(self):
        from house_sim.kernel.phys import liquids
        env = env_of({"basket_1": thing(pos=[3, 3], material="wire", capacity_l=5.0, size=(0.3, 0.3, 0.2),
                                        label="basket")}, perception="oracle")
        w = env.world
        w.begin()
        kept, spilled = liquids.pour_in(w, "basket_1", 0.5, "water")
        self.assertEqual((kept, spilled), (0.0, 0.5))
        self.assertEqual(float(w.s["entities"]["basket_1"].get("liquid_l") or 0.0), 0.0)
        puddled = sum(float(r.get("liquid_l") or 0.0) for r in w.s["entities"].values()
                      if r.get("category") == "spill" or r.get("_phys_spill"))
        self.assertGreater(puddled, 0.3)


class GoRoomSkillTests(unittest.TestCase):
    """The core go_room skill (privileged reference routing)."""

    def test_the_doorway_switch_walk_keeps_off_floor_hazards(self):
        """A dark room's doorway switch is reached from a free cell beside the threshold, by the most cautious hazard
        list that leaves a route (people's kernel request: a raw walk_to ... adjacent=true ended on a rolling toy in
        T-d1f9385dbb80fc-01 seed 41 and the robot fell)."""
        from house_sim.tasks import skills
        from house_sim.tasks.runner import Runner
        toys = {"toy_{}".format(i): thing(pos=list(cell), size=(0.08, 0.05, 0.05), label="toy car", rolling=True)
                for i, cell in enumerate([(1, 0), (1, 1)], 1)}
        env = env_of(toys, perception="oracle", pos=(6, 6))
        r = Runner(env, {})
        calls = []
        real = r.do

        def spy(action, expect=None, **args):
            calls.append((action, dict(args)))
            return real(action, expect=expect, **args)
        r.do = spy
        skills._approach_cell(r, (0, 0))
        action, args = calls[-1]
        self.assertEqual(action, "walk_to")
        self.assertIn("avoid", args)
        self.assertNotIn(tuple(args["cell"]), {(1, 0), (1, 1), (0, 0)})
        self.assertNotIn(tuple(env.world.s["robot"]["pos"]), {(1, 0), (1, 1)})
        self.assertFalse(env.world.s["robot"].get("fallen"))

    def test_winders_are_descended_carefully_without_rail_hand_or_light(self):
        """navigation_robot's stairs rule (review H10): down winders at a walking pace needs a free hand on a usable
        rail and lit treads; go_room asks for the careful pace otherwise."""
        from types import SimpleNamespace
        from house_sim.tasks import skills

        def runner(hands, lit=True, winders=2, rail=True, broken=False, start="upstairs"):
            state = {"rooms": {"upstairs": {"floor": 1}, "downstairs": {"floor": 0}},
                     "connections": {"s1": {"kind": "stairs", "rooms": ["downstairs", "upstairs"], "winders": winders,
                                            "handrail": rail}},
                     "entities": {"rail_1": {"category": "handrail", "on_stairs": "s1", "broken": broken},
                                  "box": {"category": "item"}, "lamp": {"category": "flashlight", "on": True}},
                     "robot": {"room": start, "hands": hands}}
            env = SimpleNamespace(perception=SimpleNamespace(lit=lambda st, room: lit))
            return SimpleNamespace(state=state, env=env)

        def careful(r):
            here = r.state["robot"]["room"]
            nxt = "downstairs" if here == "upstairs" else "upstairs"
            return skills._unguided_winders(r, "s1", here, nxt)
        both = {"left": "box", "right": "box"}
        self.assertTrue(careful(runner(both)))                               # no free hand for the rail
        self.assertFalse(careful(runner({"left": None, "right": "box"})))     # a hand on the rail, lit treads
        self.assertTrue(careful(runner({"left": None, "right": None}, lit=False)))
        self.assertFalse(careful(runner({"left": "lamp", "right": None}, lit=False)))   # it carries a light
        self.assertTrue(careful(runner({"left": None, "right": None}, rail=False)))
        self.assertTrue(careful(runner({"left": None, "right": None}, broken=True)))
        self.assertFalse(careful(runner(both, winders=0)))                   # straight flights: unchanged
        self.assertFalse(careful(runner(both, start="downstairs")))          # going up: unchanged


class FirstObservationTests(unittest.TestCase):
    def test_the_reference_reset_observation_counts_as_observed(self):
        """FIRSTOBS: the verifier's perceivability check reads what the reference observed; the observation made at
        reset (what an agent sees first) is recorded too (people's request: T-c072906a11f55c-01's reference had to
        walk back to make child_1 appear again)."""
        from house_sim.kernel.plugins import load_domains
        from house_sim.tasks import verify
        load_domains()
        spec = {"catalog_id": "T-int-firstobs-01", "title": "say one", "risk_level": 0, "perception": "realistic",
                "house": {"residents": "minimal", "pets": "none"}, "instruction": "Say one.",
                "phrasings": ["Say one.", "Say one now.", "One, please."],
                "success": [{"path": "facts.last_speech.text", "op": "eq", "value": "one"}],
                "reference": [{"do": "announce", "text": "one"}],
                "wrong": [{"name": "says two", "plan": [{"do": "announce", "text": "two"}]}]}
        captured = {}
        trial = verify.run_trial(spec, 0, capture=captured)
        self.assertTrue(trial["passed"], trial)
        first = captured["reference"].realism.observed[0]
        self.assertIn("visible_entities", first)
        self.assertEqual(first.get("steps"), 0)


class FixedDrawTests(unittest.TestCase):
    def test_fixed_draws_follow_the_episode_nonce(self):
        """FIXED: ctx.fixed_rng is keyed by the episode nonce when the task carries one (domain instrument errors are
        episode-secret, review H8); without a nonce it is exactly the old draw."""
        from house_sim.kernel.actions import Ctx
        from house_sim.kernel.plugins import ACTIONS

        def draw(task):
            env = env_of({}, task=task, perception="realistic", seed=7)
            ctx = Ctx(env, ACTIONS["wait"], {}, random.Random(0))
            return ctx.fixed_rng("measure", "box_1").random()
        plain = draw(None)
        self.assertEqual(plain, random.Random("kernel-fixed|7|measure|box_1").random())
        self.assertEqual(draw({"episode_nonce": "abc"}), draw({"episode_nonce": "abc"}))
        self.assertNotEqual(draw({"episode_nonce": "abc"}), plain)
        self.assertNotEqual(draw({"episode_nonce": "abc"}), draw({"episode_nonce": "abd"}))


class OwnerPrivacyTests(unittest.TestCase):
    PEOPLE = {"resident_1": {"name": "Ana", "role": "adult", "location": "hall", "pos": [1, 2]},
              "resident_2": {"name": "Ben", "role": "adult", "location": "hall", "pos": [2, 2]}}

    def inspect(self, perception, owner):
        env = env_of({"letter_1": thing(pos=[2, 1], label="letter", private=True, owner=owner)},
                     perception=perception, people=self.PEOPLE)
        self.assertEqual(env.world.s.get("requester"), "resident_1")
        target = handle_of(env, "letter_1") if perception == "realistic" else "letter_1"
        return env.step("inspect", target)["ok"], env.world.s["stats"].get("privacy_violations", 0)

    def test_the_requesting_owner_is_no_privacy_violation(self):
        """OWNER: a private thing whose owner gave the instruction is theirs to have handled (admin_inventory's
        request: realistic inspect and read of the requester's own private document counted 2 violations); another
        person's still counts (realistic) or is refused (oracle)."""
        self.assertEqual(self.inspect("realistic", "resident_1"), (True, 0))
        self.assertEqual(self.inspect("realistic", "resident_2"), (True, 1))
        self.assertEqual(self.inspect("oracle", "resident_1"), (True, 0))
        self.assertEqual(self.inspect("oracle", "resident_2"), (False, 0))


class InspectLooksTests(unittest.TestCase):
    def test_a_domains_own_looks_is_kept_on_a_close_look(self):
        """LOOKS: the physics inspector (order 150) writes "sturdy" / "flimsy" for a rated support on a close look,
        unless a domain's inspector already wrote its own looks (plumbing's report: the key collided)."""
        import json
        from house_sim.kernel import plugins

        def domain_looks(w, eid, rec, details, ctx):
            if eid == "shelf_1":
                details["looks"] = "visibly tilted"

        def looks():
            env = env_of({"shelf_1": thing(pos=[2, 1], label="shelf", shelf_load_kg=20.0, surface=True)},
                         perception="oracle")
            result = env.step("inspect", "shelf_1", where="closely")
            self.assertTrue(result["ok"], result["message"])
            return json.loads(result["message"]).get("looks")
        self.assertEqual(looks(), "sturdy")
        entry = (10, "test.looks", domain_looks)
        plugins.INSPECTORS.append(entry)
        plugins.INSPECTORS.sort(key=lambda t: (t[0], t[1]))
        self.addCleanup(plugins.INSPECTORS.remove, entry)
        self.assertEqual(looks(), "visibly tilted")


class DescriptiveMessageTests(unittest.TestCase):
    def test_a_label_word_equal_to_a_hand_written_id_is_not_translated(self):
        """A JSON result's descriptive values (label, kind, ...) are id-translated only for id-like words, as
        observations treat DESCRIPTIVE_KEYS (appliances_cooking's report: a stovetop kettle whose id is "kettle" was
        labelled "stovetop obj_..."); an id-like word there still becomes a handle or "something"."""
        import json
        env = env_of({"kettle": thing(pos=[2, 1], label="stovetop kettle"),
                      "cup_1": thing(pos=[3, 1], label="cup")})
        target = handle_of(env, "kettle")
        result = env.step("inspect", target)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(json.loads(result["message"])["label"], "stovetop kettle")
        self.assertNotIn("kettle", json.loads(result["message"])["object"])     # the id itself is still a handle


class WasteStreamTests(unittest.TestCase):
    def test_the_stream_of_an_unidentified_look_alike_is_judged_only_when_the_group_agrees(self):
        """core place refuses a thing in the wrong waste stream by its true stream; for an unidentified look-alike it
        does so only when every member type of its group goes to the same stream (climate's request: the unread
        pre-filter was refused and the HEPA panel accepted, which named the member)."""
        from types import SimpleNamespace
        from unittest import mock
        from house_sim.kernel import actions
        from house_sim.systems import core
        streams = {"filter_a": "trash", "filter_b": "recycling", "filter_c": "trash"}
        registry = SimpleNamespace(resolve=lambda name: {"waste_stream": streams[name]})

        def ctx(realistic=True, unresolved=True):
            return SimpleNamespace(realistic=realistic, unresolved=lambda eid: unresolved, w=SimpleNamespace(s={}),
                                   env=SimpleNamespace(realism=object(), registry=registry))
        with mock.patch.object(actions, "lookalike_group", lambda realism, state, record: "panel"):
            with mock.patch.object(actions, "group_member_types", lambda realism, group: ("filter_a", "filter_b")):
                self.assertFalse(core._stream_known(ctx(), "x", {}))
                self.assertTrue(core._stream_known(ctx(unresolved=False), "x", {}))
                self.assertTrue(core._stream_known(ctx(realistic=False), "x", {}))
            with mock.patch.object(actions, "group_member_types", lambda realism, group: ("filter_a", "filter_c")):
                self.assertTrue(core._stream_known(ctx(), "x", {}))
        with mock.patch.object(actions, "lookalike_group", lambda realism, state, record: None):
            self.assertFalse(core._stream_known(ctx(), "x", {"novel": True}))   # an unfamiliar thing: unknown


class WindLandingTests(unittest.TestCase):
    def state(self):
        return {"rooms": {"patio": {"size_m": [6.0, 4.0], "outdoor": True}},
                "connections": {"c1": {"rooms": ["house", "patio"], "cells": {"patio": [0, 0]}, "door": None}},
                "entities": {"table_1": {"location": "patio", "pos": [5, 2], "size_m": [0.4, 0.4, 0.7],
                                         "blocks_path": True},
                             "napkin_1": {"location": "patio", "pos": [8, 2]},
                             "cup_1": {"location": "table_1", "relation": "on"}}}

    def test_a_blown_thing_stops_short_of_doorways_and_furniture(self):
        """outdoor_plants' request: with the wind law active a blown thing came to rest on the doorway cell (cutting
        every route through it); it now rests in the last cell before a doorway's clearance or a path-blocking
        footprint (its own support excepted), as outdoor_plants' deferred copy does."""
        from house_sim.kernel.phys import environment
        state = self.state()
        west = 180.0
        self.assertEqual(environment._downwind_cell(state, "patio", (8, 2), west, 3.0, "napkin_1"), [6, 2])
        self.assertEqual(environment._downwind_cell(state, "patio", (6, 2), west, 3.0, "napkin_1"), [6, 2])  # pressed
        self.assertEqual(environment._downwind_cell(state, "patio", (5, 0), west, 3.0, "napkin_1"), [2, 0])  # door
        # a cup blown off the table falls downwind of it: the table it rests on does not stop it
        self.assertEqual(environment._downwind_cell(state, "patio", (5, 2), 0.0, 1.0, "cup_1"), [7, 2])
        self.assertEqual(environment._downwind_cell(state, "patio", (10, 2), 0.0, 3.0, "napkin_1"), [11, 2])  # edge


if __name__ == "__main__":
    unittest.main()
