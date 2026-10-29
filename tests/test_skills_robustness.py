"""Core reference skills (house_sim/tasks/skills.py), VERIFY_HARDENING 25 package WP11.

  BINDING   a skill parameter that names a thing, room, person or pet and arrives unbound (None, empty, not a string)
            or names nothing in the house stops the plan with PlanError, never TypeError / KeyError (review H10: the
            fetch TypeError of T-5ad61b6881821b-01 seed 502 aborted the trial; F11: put into an unknown room raised
            KeyError); a lenient (wrong) plan goes on with its next step
  PUT       put opens a closed destination: unlocks it with the held key, frees a hand when opening needs one (a
            two-handed load), opens the closed containers around it; close=True leaves them as found
  WINDERS   go_room descends winder stairs carefully unless a hand on a usable rail and lit treads guide it; a rail the
            robot knows to be loose guides nothing (stairs.plan's light touch)
  SURVEY    the canonical survey of the budget's explore allowance (VERIFY_HARDENING 17): every room nearest first,
            look around in each; with places, the search baseline's walk (open, inspect, no stop at the item);
            deterministic, independent of the order given, best-effort unless strict

Every scenario runs in both perception modes and, where the camera matters, both camera modes. Each BINDING, PUT
(unlock, free hand, close), WINDERS (loose rail) and SURVEY test fails on the tree before WP11.
"""
import copy
import json
import sys
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import SKILLS, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import PlanError, Runner

load_domains()

MODES = (("oracle", "room"), ("realistic", "room"), ("oracle", "geometric"), ("realistic", "geometric"))


def loaded(domain):
    return any(name.startswith("house_sim.systems." + domain) for name in sys.modules)


def known(*types):
    reg = default_registry()
    return all(reg.known(t) for t in types)


class Home:
    """A small explicit house: kitchen 5x4 m -- hall 2x4 m -- living 5x4 m on the ground floor; the hall's stairs (14
    steps, a handrail) up to the landing (floor 1); a study behind a locked door off the living room (its key is
    nowhere in the house). Every room is lit. The kitchen has a closed cabinet with a drawer inside, a locked
    chemical cabinet (key_1), a closed toy box, a mug and a two-handed laundry basket. Core types only (the handrail
    is navigation_robot's), so the file also runs with HOUSE_SIM_DOMAINS=core."""

    def __init__(self, winders=0):
        self.reg = reg = default_registry()
        rooms = {
            "kitchen": {"type": "kitchen", "size_m": [5.0, 4.0], "floor_material": "tile", "floor": 0},
            "hall": {"type": "hallway", "size_m": [2.0, 4.0], "floor_material": "hardwood", "floor": 0},
            "living": {"type": "living_room", "size_m": [5.0, 4.0], "floor_material": "hardwood", "floor": 0},
            "landing": {"type": "hallway", "size_m": [2.0, 4.0], "floor_material": "carpet", "floor": 1},
            "study": {"type": "office", "size_m": [3.0, 3.0], "floor_material": "carpet", "floor": 0},
        }
        conns = {
            "c_kh": {"rooms": ["kitchen", "hall"], "cells": {"kitchen": [9, 4], "hall": [0, 4]}, "width_m": 0.9},
            "c_hl": {"rooms": ["hall", "living"], "cells": {"hall": [3, 4], "living": [0, 4]}, "width_m": 0.9},
            "c_hu": {"rooms": ["hall", "landing"], "cells": {"hall": [0, 0], "landing": [0, 7]}, "width_m": 1.0,
                     "kind": "stairs", "steps": 14, "rise_m": 0.18, "run_m": 0.27, "handrail": True,
                     "winders": winders},
            "c_ls": {"rooms": ["living", "study"], "cells": {"living": [9, 1], "study": [0, 1]}, "width_m": 0.85,
                     "door": "door_study"},
        }
        self.c = {"schema_version": 2, "start_room": "kitchen", "rooms": rooms, "connections": conns, "entities": {},
                  "people": {}, "pets": {}, "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"},
                  "metadata": {"units": "metric"},
                  "weather": {"condition": "clear", "temperature_c": 18.0, "wind_mps": 1.0}}
        for rid, room in rooms.items():
            room["exits"] = []
            floor = reg.make("floor_surface", rid, material=room["floor_material"],
                             size_m=[room["size_m"][0], room["size_m"][1], 0.01])
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            self.add("light_" + rid, "ceiling_light", rid, pos=[1, 1], on=True, mounted=True)
        door = reg.make("door", "living", open=False, locked=True, lockable=True, key="key_study", lock_kind="key")
        door["connects"] = ["living", "study"]
        door["pos"] = [9, 1]
        self.c["entities"]["door_study"] = door
        if known("handrail"):
            self.add("rail_1", "handrail", "hall", pos=[0, 0], on_stairs="c_hu", mounted=True)
        self.add("cab", "cabinet", "kitchen", pos=[2, 0], open=False)
        self.add("drawer_1", "drawer", "cab", open=False)
        self.add("chem", "cabinet", "kitchen", pos=[6, 0], open=False, lockable=True, locked=True, key="key_1",
                 lock_kind="key", label="chemical cabinet")
        self.add("toybox", "toy_box", "kitchen", pos=[4, 6], open=False)
        self.add("mug", "generic_item", "kitchen", pos=[2, 4], label="mug", size_m=[0.12, 0.09, 0.1])
        self.add("key_1", "key", "kitchen", pos=[3, 5])
        self.add("basket", "laundry_basket", "kitchen", pos=[6, 4], mass_kg=1.2, size_m=[0.4, 0.3, 0.2])

    def add(self, eid, type_name, location, **props):
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
        self.c["entities"][eid] = rec
        return eid

    def env(self, room="kitchen", pos=(1, 3), hold=None, perception="oracle", camera="room", **robot):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos)}
        c["robot"].update(robot)
        for hand, eid in (hold or {}).items():
            hands = c["robot"].setdefault("hands", {"left": None, "right": None})
            for h in (("left", "right") if hand == "both" else (hand,)):
                hands[h] = eid
            rec = c["entities"][eid]
            rec.update(location="robot", relation="held", held_hand=hand)
            for key in ("pos", "spos", "shelf"):
                rec.pop(key, None)
        return HouseEnv(c, task=None, seed=7, perception=perception, camera=camera)


def runner(env, bindings=None, strict=True):
    return Runner(env, bindings or {}, strict=strict)


def ents(env):
    return env.world.s["entities"]


def actions(env):
    """The (action, args) the robot carried out, from the hidden action trail."""
    return [(row.get("action"), row) for row in env.world.s.get("action_log") or []]


class BindingTests(unittest.TestCase):
    """BINDING: a missing or unknown binding is a PlanError (the plan cannot be carried out on this seed)."""

    UNBOUND = [("go_room", {"room": None}), ("approach", {"target": None}), ("reveal", {"obj": None}),
               ("fetch", {"obj": None}), ("put", {"obj": None, "dest": "cab"}), ("put", {"obj": "mug", "dest": None}),
               ("give", {"obj": "mug", "person": None}), ("ask", {"person": None, "topic": "x"}),
               ("open", {"target": None}), ("close", {"target": None}), ("switch", {"target": None}),
               ("set", {"target": None, "setting": "mode", "value": "on"}), ("use", {"action": None, "target": "cab"}),
               ("wait", {"seconds": None}), ("goto", {"target": None}), ("goto", {"target": ["kitchen"]}),
               ("approach", {"target": {"cell": [1, 1]}}), ("fetch", {"obj": 17}), ("survey", {"rooms": [None]}),
               ("survey", {"places": [None]})]
    UNKNOWN = [("put", {"obj": "mug", "dest": "garage_9"}), ("ask", {"person": "ghost", "topic": "x"}),
               ("tell", {"person": "ghost", "kind": "status"}), ("give", {"obj": "mug", "person": "ghost"}),
               ("fetch", {"obj": "ghost_cup"}), ("open", {"target": "ghost_box"}), ("set", {"target": "ghost",
               "setting": "mode", "value": 1}), ("goto", {"target": "attic_9"}), ("survey", {"rooms": ["attic_9"]})]

    def test_an_unbound_parameter_is_a_plan_error_naming_the_skill_and_parameter(self):
        for perception in ("oracle", "realistic"):
            for name, args in self.UNBOUND:
                with self.subTest(perception=perception, skill=name, args=args):
                    r = runner(Home().env(perception=perception))
                    with self.assertRaises(PlanError) as caught:
                        r.skill(name, **args)
                    self.assertIn(name, str(caught.exception))
                    self.assertEqual(getattr(caught.exception, "category", None), "argument")

    def test_an_id_naming_nothing_in_the_house_is_a_plan_error(self):
        for perception in ("oracle", "realistic"):
            for name, args in self.UNKNOWN:
                with self.subTest(perception=perception, skill=name, args=args):
                    r = runner(Home().env(perception=perception))
                    with self.assertRaises(PlanError) as caught:
                        r.skill(name, **args)
                    bad = [v for v in args.values() if isinstance(v, str) and ("ghost" in v or v.endswith("_9"))]
                    bad += [v for v in args.get("rooms") or [] if isinstance(v, str)]
                    self.assertTrue(any(repr(v) in str(caught.exception) for v in bad), str(caught.exception))
                    self.assertEqual(getattr(caught.exception, "category", None), "argument")

    def test_the_review_case_fetch_of_a_none_binding(self):
        """review H10: fetch {blocker} with blocker None built "No such entity " + None (TypeError) and aborted the
        whole trial; now the strict reference stops with PlanError and a lenient wrong plan carries on."""
        plan = [{"skill": "fetch", "obj": "{blocker}"}, {"do": "wait", "seconds": 5}]
        for perception in ("oracle", "realistic"):
            env = Home().env(perception=perception)
            with self.assertRaises(PlanError) as caught:
                runner(env, {"blocker": None}).run(plan)
            self.assertEqual(str(caught.exception), "fetch: obj is unbound (None): the binding names nothing")
            env = Home().env(perception=perception)
            start = env.world.s["time_s"]
            lenient = runner(env, {"blocker": None}, strict=False)
            lenient.run(plan)
            self.assertGreaterEqual(env.world.s["time_s"] - start, 5.0, "the step after the failed skill still runs")

    def test_the_verifier_records_a_plan_error_not_a_crash(self):
        """At trial level: a reference whose binding names nothing (save_state of an absent field) fails as a plan,
        with no TypeError or KeyError anywhere in the trial record."""
        from house_sim.tasks import verify
        spec = {"catalog_id": "T-wp11-binding-01", "title": "fetch what is not there", "risk_level": 0,
                "house": {"residents": "minimal", "pets": "none"}, "instruction": "Bring the cup.",
                "phrasings": ["Bring the cup.", "Fetch the cup.", "Get me the cup."],
                "success": [{"path": "robot.docked", "op": "is_true", "value": True}],
                "budget": {"steps": 20, "time_s": 600},
                "reference": [{"save_state": "cup", "path": "robot.no_such_field"}, {"skill": "fetch", "obj": "{cup}"}],
                "wrong": [{"name": "waits", "plan": [{"do": "wait", "seconds": 30}]}]}
        trial = verify.run_trial(spec, 0)
        text = json.dumps(trial, default=str)
        self.assertFalse(trial["passed"])
        self.assertNotIn("TypeError", text)
        self.assertNotIn("KeyError", text)
        self.assertIn("fetch: obj is unbound", text)

    def test_optional_and_remote_parameters_keep_their_meaning(self):
        """None stays the default where a skill has one (tell without a person, use without a target, drop_all to
        the floor), and a report over a remote channel may name a contact who does not live in the house."""
        env = Home().env()
        lenient = runner(env, strict=False)
        lenient.skill("tell", person="landlord_away", channel="phone", kind="status", subject="mug", state="clean")
        lenient.skill("tell", person=None, kind="status", subject="mug", state="clean")
        lenient.skill("use", action="wait", target=None, seconds=1)
        lenient.skill("fetch", obj="mug")
        lenient.skill("drop_all", dest=None)
        self.assertEqual(ents(env)["mug"]["location"], "kitchen")

    def test_an_unreachable_target_is_not_reported_as_caution(self):
        """A target nothing can reach (walled in by furniture) was reported as 'No route ... that avoids stepping on
        sharp or fragile things' (held-out seed 502 of T-9615ae9f7570b4-01): the message now says no route exists at
        all; a target fenced by broken glass keeps the caution message."""
        home = Home()
        for i, cell in enumerate(([6, 5], [7, 5], [8, 5], [6, 6], [8, 6], [6, 7], [7, 7], [8, 7])):
            home.add("crate_%d" % i, "box", "kitchen", pos=cell, size_m=[0.45, 0.45, 0.5], mass_kg=30.0, portable=False,
                     blocks_path=True)
        home.add("tape", "generic_item", "kitchen", pos=[7, 6], label="tape", size_m=[0.08, 0.04, 0.08])
        env = home.env()
        with self.assertRaises(PlanError) as caught:
            runner(env).skill("approach", target="tape")
        self.assertIn("at all", str(caught.exception))
        self.assertEqual(caught.exception.category, "route")
        home = Home()
        home.add("tape", "generic_item", "kitchen", pos=[7, 6], label="tape", size_m=[0.08, 0.04, 0.08])
        for i, cell in enumerate(([6, 5], [7, 5], [8, 5], [6, 6], [8, 6], [6, 7], [7, 7], [8, 7], [5, 4], [6, 4],
                                  [7, 4], [8, 4], [9, 4], [5, 5], [9, 5], [5, 6], [9, 6], [5, 7], [9, 7])):
            home.add("shard_%d" % i, "generic_item", "kitchen", pos=cell, label="glass shard", size_m=[0.03, 0.03, 0.005],
                     sharp=True, floor_hazard="sharp")
        env = home.env()
        with self.assertRaises(PlanError) as caught:
            runner(env).skill("approach", target="tape")
        self.assertIn("avoids stepping on sharp", str(caught.exception))
        self.assertEqual(caught.exception.category, "route")

    def test_bound_values_behave_as_before(self):
        """The check costs nothing on a bound plan: fetch then put mug into the open cabinet, identical digests in
        two runs."""
        digests = []
        for _ in range(2):
            env = Home().env()
            r = runner(env)
            r.skill("put", obj="mug", dest="cab")
            self.assertEqual(ents(env)["mug"]["location"], "cab")
            digests.append(digest(env.world.s))
        self.assertEqual(digests[0], digests[1])


class PutTests(unittest.TestCase):
    """PUT: a closed destination is opened, whatever keeps it closed."""

    def each(self, check):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                check(perception, camera)

    def test_put_opens_a_closed_container(self):
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera)
            runner(env).skill("put", obj="mug", dest="toybox")
            self.assertEqual((ents(env)["mug"]["location"], ents(env)["mug"]["relation"]), ("toybox", "in"))
            self.assertTrue(ents(env)["toybox"]["open"])
        self.each(check)

    def test_put_opens_the_closed_containers_around_the_destination(self):
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera)
            runner(env).skill("put", obj="mug", dest="drawer_1")
            self.assertEqual(ents(env)["mug"]["location"], "drawer_1")
            self.assertTrue(ents(env)["cab"]["open"] and ents(env)["drawer_1"]["open"])
        self.each(check)

    def test_put_unlocks_a_locked_destination_with_the_key_it_holds(self):
        """Before WP11 put opened a locked destination without unlocking it: 'It is locked; unlock it first'."""
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera, hold={"left": "key_1"})
            runner(env).skill("put", obj="mug", dest="chem", relation="in")
            self.assertEqual(ents(env)["mug"]["location"], "chem")
            self.assertFalse(ents(env)["chem"]["locked"])
            self.assertTrue(ents(env)["chem"]["open"])
        self.each(check)

    def test_a_locked_destination_without_the_key_is_a_plan_error(self):
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera)
            with self.assertRaises(PlanError):
                runner(env).skill("put", obj="mug", dest="chem", relation="in")
            self.assertTrue(ents(env)["chem"]["locked"])
        self.each(check)

    def test_put_frees_a_hand_to_open_the_destination(self):
        """A two-handed load and a closed lid: before WP11 'You need a free hand to operate it'. Now the basket is
        set down, the lid opened, the basket picked up again with both hands and put in."""
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera, hold={"both": "basket"})
            runner(env).skill("put", obj="basket", dest="toybox")
            self.assertEqual(ents(env)["basket"]["location"], "toybox")
            self.assertTrue(ents(env)["toybox"]["open"])
            hands = env.world.s["robot"]["hands"]
            self.assertEqual((hands["left"], hands["right"]), (None, None))
            picks = [row for name, row in actions(env) if name == "pick_up"]
            self.assertEqual(len(picks), 1, "picked up again once, after the lid was open")
        self.each(check)

    def test_close_true_leaves_the_destination_as_found(self):
        def check(perception, camera):
            env = Home().env(perception=perception, camera=camera, hold={"left": "key_1"})
            runner(env).skill("put", obj="mug", dest="chem", relation="in", close=True)
            self.assertEqual(ents(env)["mug"]["location"], "chem")
            self.assertFalse(ents(env)["chem"]["open"])
            self.assertTrue(ents(env)["chem"]["locked"])
            env = Home().env(perception=perception, camera=camera)
            runner(env).skill("put", obj="mug", dest="drawer_1", close=True)
            self.assertEqual(ents(env)["mug"]["location"], "drawer_1")
            self.assertFalse(ents(env)["drawer_1"]["open"] or ents(env)["cab"]["open"])
        self.each(check)

    def test_close_false_keeps_the_legacy_behaviour(self):
        env = Home().env()
        runner(env).skill("put", obj="mug", dest="cab")
        self.assertTrue(ents(env)["cab"]["open"])
        self.assertNotIn("close", [name for name, _ in actions(env)])

    def test_put_is_deterministic(self):
        def check(perception, camera):
            runs = []
            for _ in range(2):
                env = Home().env(perception=perception, camera=camera, hold={"both": "basket"})
                runner(env).skill("put", obj="basket", dest="toybox", close=True)
                runs.append(digest(env.world.s))
            self.assertEqual(runs[0], runs[1])
        self.each(check)


@unittest.skipUnless(loaded("navigation_robot") and known("handrail"), "the stairs model is navigation_robot's")
class WinderTests(unittest.TestCase):
    """WINDERS: go_room's pace down a winder flight."""

    def descend(self, home, perception, camera, hold=None, inspect_rail=False):
        env = home.env(room="hall", pos=(1, 1), perception=perception, camera=camera)
        r = runner(env)
        if inspect_rail:
            r.skill("approach", target="rail_1")
            r.do("inspect", target="rail_1")
        r.skill("go_room", room="landing")
        if hold:
            r.skill("fetch", obj=hold) if hold in ents(env) and ents(env)[hold]["location"] != "robot" else None
        r.skill("go_room", room="hall")
        return env

    def test_guided_winders_keep_the_walking_pace(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                env = self.descend(Home(winders=3), perception, camera)
                self.assertFalse(env.world.s["robot"].get("fallen"))
                self.assertEqual(env.world.s["nav"]["stairs"]["c_hu"]["speed"], "normal")

    def test_a_two_handed_load_down_winders_is_carried_carefully(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                home = Home(winders=3)
                home.add("tray", "generic_item", "landing", pos=[1, 3], label="tray", mass_kg=0.6,
                         size_m=[0.4, 0.3, 0.03])
                env = home.env(room="landing", pos=(1, 4), hold={"both": "tray"}, perception=perception,
                               camera=camera)
                runner(env).skill("go_room", room="hall")
                self.assertFalse(env.world.s["robot"].get("fallen"))
                self.assertEqual(env.world.s["robot"]["room"], "hall")
                self.assertEqual(env.world.s["nav"]["stairs"]["c_hu"]["speed"], "careful")

    def test_a_rail_known_to_be_loose_guides_nothing(self):
        """The robot inspected the rail and knows it is loose, so it only lets its hand slide along it (stairs.plan's
        light touch): a walking descent of winders is a fall. Before WP11 go_room walked down and fell."""
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                home = Home(winders=3)
                home.c["entities"]["rail_1"]["_loose"] = True
                env = self.descend(home, perception, camera, inspect_rail=True)
                self.assertIn("rail_1", env.world.s["nav"].get("rails_checked") or {})
                self.assertFalse(env.world.s["robot"].get("fallen"), (env.world.s["nav"].get("last_incident")))
                self.assertFalse(ents(env)["rail_1"].get("broken"))
                self.assertEqual(env.world.s["nav"]["stairs"]["c_hu"]["speed"], "careful")

    def test_straight_flights_and_climbing_are_unchanged(self):
        for winders, start, goal in ((0, "landing", "hall"), (3, "hall", "landing")):
            with self.subTest(winders=winders, start=start):
                home = Home(winders=winders)
                home.add("tray", "generic_item", start, pos=[1, 3], label="tray", mass_kg=0.6, size_m=[0.4, 0.3, 0.03])
                env = home.env(room=start, pos=(1, 4), hold={"both": "tray"})
                runner(env).skill("go_room", room=goal)
                self.assertEqual(env.world.s["nav"]["stairs"]["c_hu"]["speed"], "normal")

    def test_an_explicit_speed_is_the_callers(self):
        home = Home(winders=3)
        home.add("tray", "generic_item", "landing", pos=[1, 3], label="tray", mass_kg=0.6, size_m=[0.4, 0.3, 0.03])
        env = home.env(room="landing", pos=(1, 4), hold={"both": "tray"})
        runner(env, strict=False).skill("go_room", room="hall", speed="normal")
        self.assertTrue(env.world.s["robot"].get("fallen"), "a wrong plan that walks down keeps its mistake")


class SurveyTests(unittest.TestCase):
    """SURVEY: the canonical survey and the search baseline's walk."""

    def looks(self, env):
        return [row for name, row in actions(env) if name == "look"]

    def test_the_survey_looks_around_every_room_nearest_first(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                env = Home().env(perception=perception, camera=camera)
                out = runner(env).skill("survey", rooms=["living", "kitchen", "hall"])
                self.assertEqual(out["rooms"], ["kitchen", "hall", "living"])
                self.assertEqual(out["skipped"], {})
                self.assertEqual(env.world.s["robot"]["room"], "living")
                self.assertEqual(len(self.looks(env)), 3)
                self.assertEqual([row.get("target") for row in self.looks(env)], ["around"] * 3)
                if camera == "geometric":
                    # look around: six head frames, 5.4 s, and the floor of each room surveyed (camera_log.seen_cells)
                    self.assertEqual(sorted(env.world.s["camera_log"]["seen_cells"]), ["hall", "kitchen", "living"])

    def test_the_tour_does_not_depend_on_the_order_given(self):
        results = set()
        for rooms in (["living", "kitchen", "hall"], ["hall", "living", "kitchen"], ["kitchen", "living", "hall"]):
            env = Home().env()
            out = runner(env).skill("survey", rooms=rooms)
            results.add((tuple(out["rooms"]), digest(env.world.s)))
        self.assertEqual(len(results), 1)

    def test_the_default_survey_covers_every_indoor_room_and_skips_what_it_cannot_reach(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                env = Home().env(perception=perception, camera=camera)
                out = runner(env).skill("survey")
                self.assertEqual(sorted(out["rooms"]), ["hall", "kitchen", "landing", "living"])
                self.assertEqual(out["rooms"][0], "kitchen")
                self.assertEqual(sorted(out["skipped"]), ["study"], out["skipped"])
                self.assertFalse(env.world.s["robot"].get("fallen"))

    def test_strict_raises_on_a_room_it_cannot_reach(self):
        env = Home().env()
        with self.assertRaises(PlanError):
            runner(env).skill("survey", rooms=["kitchen", "study"], strict=True)

    def test_the_survey_is_deterministic(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                runs = []
                for _ in range(2):
                    env = Home().env(perception=perception, camera=camera)
                    out = runner(env).skill("survey", places=["chem", "drawer_1", "toybox", "not_in_this_house"])
                    runs.append((json.dumps(out, sort_keys=True), digest(env.world.s), env.world.s["time_s"]))
                self.assertEqual(runs[0], runs[1])

    def test_the_search_baseline_opens_and_inspects_every_place_without_stopping(self):
        for perception, camera in MODES:
            with self.subTest(perception=perception, camera=camera):
                home = Home()
                home.add("remote", "generic_item", "toybox", label="remote")
                env = home.env(perception=perception, camera=camera)
                out = runner(env).skill("survey", places=["toybox", "drawer_1", "chem", "not_in_this_house",
                                                          "living"])
                self.assertEqual(sorted(out["places"]), ["drawer_1", "toybox"])
                self.assertIn("chem", out["skipped"])
                self.assertNotIn("not_in_this_house", out["skipped"])
                self.assertIn("living", out["rooms"])
                self.assertTrue(ents(env)["toybox"]["open"] and ents(env)["cab"]["open"] and ents(env)["drawer_1"]["open"])
                inspected = [row.get("target") for name, row in actions(env) if name == "inspect"]
                self.assertTrue({"toybox", "drawer_1"} <= set(inspected), inspected)
                self.assertEqual(out["rooms"][-1], "living", "the kitchen places first, then the next room")

    def test_a_survey_stops_when_the_robot_is_down(self):
        env = Home().env(battery=0.0)
        out = runner(env).skill("survey", rooms=["kitchen", "hall"])
        self.assertEqual(out["rooms"], [])
        self.assertEqual(set(out["skipped"]), {"kitchen", "hall"})

    def test_survey_is_registered(self):
        self.assertIn("survey", SKILLS)


if __name__ == "__main__":
    unittest.main()
