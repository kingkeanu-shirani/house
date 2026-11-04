"""The flagship episode's kernel fixes (work package WP1; DESIGN 4.1 K1-K4 and V1, docs/KERNEL_API.md "v3.2 flagship
fixes"). What this file pins:

* K1: a guard with no dot in its name (report:contradiction:<s>) ends as that label; it never raises out of env.step.
  The sequence is a robot's natural one, "a leak here", then "no leak in this room", through a real run_episode;
* K2: clean_shards takes the house's own dustpan (a record with "dustpan": true and no tool_kind), and only when a
  dustpan is what it asks for;
* K3: broken glass swept into a receiver does not wedge the robot: it walks, puts the pan down, lifts it again and
  empties it, with bare grippers. The bare-hand rule still holds for everything else (see HeldShardsTests);
* K4: a thing the robot was never shown reads "against something", "met something", "swung into something", never "the
  something";
* V1: the viewer's re-run (viewer._live_env) starts like HouseEnv.replay: the Mind against the new Realism, the timeline
  and the writer log, so a prior-knowledge episode gives the same robot view and the same ledger.

The module runs with a temporary HOME/USERPROFILE, so the user's real ~/.house_ai is never read or written.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sim.eval import agents as A  # noqa: E402
from house_sim.eval import run as R  # noqa: E402
from house_sim.eval import viewer  # noqa: E402
from house_sim.kernel import ledger  # noqa: E402
from house_sim.kernel.engine import HouseEnv  # noqa: E402
from house_sim.kernel.plugins import load_domains  # noqa: E402
from house_sim.kernel.predicates import digest  # noqa: E402
from house_sim.kernel.registry import default_registry  # noqa: E402
from house_sim.systems.safety import glass as G  # noqa: E402
from house_sim.tasks.runner import build, make_env  # noqa: E402

load_domains()
REG = default_registry()
_SAVED = {}


def setUpModule():
    _SAVED["home"] = tempfile.mkdtemp(prefix="flagship_fixes_home_")
    for key in ("HOME", "USERPROFILE", "HOUSE_AI_FORBID_NETWORK"):
        _SAVED["env:" + key] = os.environ.get(key)
    os.environ["HOME"] = os.environ["USERPROFILE"] = _SAVED["home"]     # never the user's real ~/.house_ai
    os.environ["HOUSE_AI_FORBID_NETWORK"] = "1"


def tearDownModule():
    for key in ("HOME", "USERPROFILE", "HOUSE_AI_FORBID_NETWORK"):
        value = _SAVED.get("env:" + key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(_SAVED["home"], ignore_errors=True)


def hardened_spec(catalog_id="T-flagship-fixes-01"):
    """A schema-4 episode (the ledger runs) in a generated house, with prior knowledge of the map."""
    return {"catalog_id": catalog_id, "title": "lights on at the wall switch", "schema": 4, "risk_level": 0,
            "house": {"residents": "minimal", "pets": "none"},
            "setup": [{"op": "room", "var": "living", "type": "living_room", "pick": "first"},
                      {"op": "lights", "room": "{living}", "on": False},
                      {"op": "find", "var": "switch", "type": "light_switch", "room": "{living}", "pick": "first"},
                      {"op": "robot", "room": "{living}"},
                      {"op": "prior_knowledge", "map": "all"}],
            "instruction": "Wait in the living room.", "phrasings": ["Wait in the living room."],
            "success": [{"label": "here", "pred": {"path": "robot.room", "op": "eq", "value": "{living}"}}],
            "envelope": {"work_rooms": ["{living}"]}, "budget": {"steps": 20, "time_s": 240},
            "reference": [{"skill": "approach", "target": "{switch}"}, {"do": "switch_on", "target": "{switch}"}]}


# ---------------------------------------------------------------------------------------------- K1


class LeakThenNoLeak(A.Agent):
    """Says there is a leak at the first thing it sees, then that it found no leak in this room, then done."""
    name = "leak_then_no_leak"

    def __init__(self):
        self.commands = []

    def reset(self, instruction, observation, action_catalog):
        placed = sorted(h for h, r in observation["visible_entities"].items() if r.get("pos") and not r.get("mounted"))
        self.commands = [{"action": "report", "kind": "finding", "subject": placed[0], "issue": "leak"},
                         {"action": "report", "kind": "none_found", "issue": "leak", "scope": observation["room_id"]}]

    def act(self, observation, last_result):
        return self.commands.pop(0) if self.commands else {"action": "done"}


class ContradictionGuardTests(unittest.TestCase):
    def test_two_contradicting_reports_end_as_the_label_through_a_real_run_episode(self):
        spec = hardened_spec()
        episode = {"catalog_id": spec["catalog_id"], "split": "dev", "seed": 1, "seed_tag": "dev:1", "phrasing": None,
                   "perception": "realistic"}
        record = R.run_episode(spec, episode, LeakThenNoLeak())
        self.assertEqual(record["end_reason"], "done")          # not sim_error: the kernel raised nothing
        self.assertEqual(record["steps"], 2)
        labels = [f for f in record["failures"] if f.startswith("report:")]
        self.assertEqual(len(labels), 1, record["failures"])
        subject = labels[0].split(":", 2)[2]
        self.assertIn(subject, build(spec, 1)[0]["entities"])   # report:contradiction:<a real id of this house>
        self.assertFalse(record["success"])

    def test_the_label_names_the_subject(self):
        spec = hardened_spec()
        config, task, plans = build(spec, 1)
        switch = plans["bindings"]["switch"]
        env = make_env(config, task, 1)
        room = env.world.s["robot"]["room"]
        for args in ({"kind": "finding", "subject": switch, "issue": "leak"},
                     {"kind": "none_found", "issue": "leak", "scope": room}):
            result = env.step("report", **args)        # raised IndexError out of env.step (the evaluation inside it)
            self.assertTrue(result["ok"], result["message"])
        failures = ledger.failures(task, env.world.s, env._baseline, env.world, env.idle)
        self.assertIn("report:contradiction:{}".format(switch), failures)
        self.assertIn("report:contradiction:{}".format(switch), env.evaluate()["failures"])
        self.assertEqual(ledger.Finding("report", "contradiction:x").label, "report:contradiction:x")


# ---------------------------------------------------------------------------------------------- K2, K3

BROOM, PAN = "broom_1", "pan_1"     # ids shaped like a generated house's, unlike the plain words of a message


def glass_house(pan="dustpan", broom="broom", extra=None):
    """A lit tiled kitchen with a counter holding a broom and a dustpan and a glass about to break by it; a hallway with
    a trash can. The robot stands at the counter, bare-handed. extra: {eid: (type, location, props)}."""
    ents = {}

    def add(eid, type_name, loc, **props):
        ents[eid] = REG.make(type_name, loc, **props)

    rooms = {"kitchen": {"label": "Kitchen", "type": "kitchen", "size_m": [4.0, 5.0], "exits": ["hall"], "ceiling_m": 2.5,
                         "floor_material": "tile"},
             "hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 6.0], "exits": ["kitchen"], "ceiling_m": 2.5,
                      "floor_material": "hardwood"}}
    for rid, room in rooms.items():
        add("floor_" + rid, "floor_surface", rid, material=room["floor_material"], size_m=room["size_m"] + [0.01], pos=[0, 0])
        add("light_" + rid, "ceiling_light", rid, pos=[1, 1], on=True)
    add("counter", "counter", "kitchen", pos=[4, 0])
    add(BROOM, broom, "counter", relation="on")
    add(PAN, pan, "counter", relation="on")
    add("glass", "generic_item", "kitchen", pos=[4, 4], label="drinking glass", material="glass", fragile=True,
        mass_kg=0.3, size_m=[0.08, 0.08, 0.14])
    add("trash", "generic_item", "hall", pos=[1, 2], label="trash can", category="waste_container", waste_stream="trash",
        accepts_items=True, container=True, capacity_l=50.0, accepts_any_waste=True, inner_size_m=[0.35, 0.3, 0.6],
        size_m=[0.4, 0.35, 0.65], portable=False)
    for eid, (type_name, loc, props) in sorted((extra or {}).items()):
        add(eid, type_name, loc, **copy.deepcopy(props))
    return {"schema_version": 2, "start_room": "kitchen", "rooms": rooms,
            "connections": {"c_k": {"rooms": ["kitchen", "hall"], "kind": "doorway", "width_m": 0.9,
                                    "cells": {"kitchen": [0, 0], "hall": [0, 0]}}},
            "entities": ents, "people": {}, "pets": {}, "clock": {"start": "2026-10-09T10:00:00"},
            "robot": {"room": "kitchen", "pos": [4, 2]}, "household": {"address": "12 Example Lane"},
            "utilities": {"power": True, "water": True, "gas": True, "main_gas_open": True},
            "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 2.0}}


class GlassCase(unittest.TestCase):
    PERCEPTIONS = ("oracle", "realistic")

    def make(self, perception, **kw):
        env = HouseEnv(glass_house(**kw), None, 3, perception=perception, trace=False, observe_on_step=True)
        env.observe()
        return env

    def ids(self, env):
        """Entity id -> what a command names it by (the handle under realistic perception)."""
        if env.realism is None:
            return lambda eid: eid
        return env.realism.handles.handle

    def ok(self, env, action, *args, **kw):
        result = env.step(action, *args, **kw)
        self.assertTrue(result["ok"], "{} {}: {}".format(action, args, result["message"]))
        return result

    def break_glass(self, env):
        w = env.world
        w.begin()
        shards = G.shatter(w, "glass", impact_cell=(4, 4))
        w.commit()
        env.observe()
        return shards

    def pick_tools(self, env):
        h = self.ids(env)
        self.ok(env, "pick_up", h(BROOM))
        self.ok(env, "pick_up", h(PAN))

    def swept(self, env):
        """The robot's sweep: broom and pan in hand, beside the glass. Returns the pieces in the pan."""
        h = self.ids(env)
        self.break_glass(env)
        self.pick_tools(env)
        self.ok(env, "walk_to", cell=[4, 3])
        self.ok(env, "clean_shards", "kitchen", method="sweep", radius=1.5, destination=h(PAN))
        inside = env.world.descendants(PAN)
        self.assertTrue(inside)
        self.assertEqual(env.world.s["entities"][PAN]["shard_load"], len(inside))
        return inside


class HouseDustpanTests(GlassCase):
    def test_the_house_dustpan_sweeps(self):
        for perception in self.PERCEPTIONS:
            for pan in ("dustpan", "long_handled_dustpan"):
                with self.subTest(perception=perception, pan=pan):
                    env = self.make(perception, pan=pan)
                    self.assertTrue(env.world.s["entities"][PAN].get("dustpan"))
                    self.assertIsNone(env.world.s["entities"][PAN].get("tool_kind"))    # K2 is no type data change
                    self.swept(env)

    def test_only_a_request_for_a_dustpan_takes_it(self):
        env = self.make("oracle")
        self.pick_tools(env)
        w = env.world
        self.assertEqual(G._held_kind(w, "dustpan"), PAN)
        self.assertEqual(G._held_kind(w, "dustpan", "box", "bag", "container"), PAN)
        self.assertIsNone(G._held_kind(w, "box", "bag", "container"))
        self.assertIsNone(G._held_kind(w, "vacuum"))
        self.assertEqual(G._held_kind(w, "broom", "hand_brush", "sweep"), BROOM)

    def test_a_broom_alone_is_still_refused_for_a_sweep(self):
        env = self.make("oracle")
        self.break_glass(env)
        self.ok(env, "pick_up", BROOM)
        result = env.step("clean_shards", "kitchen", method="sweep", radius=1.5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "capability")
        self.assertIn("Hold a broom (or hand brush) and a dustpan", result["message"])


class HeldShardsTests(GlassCase):
    """K3. The refusal was true of the pieces and false of the robot: it holds the pan by its handle."""

    def test_sweep_walk_put_down_and_empty_with_bare_grippers(self):
        for perception in self.PERCEPTIONS:
            with self.subTest(perception=perception):
                env = self.make(perception)
                h = self.ids(env)
                inside = self.swept(env)
                self.ok(env, "move", "hall")
                self.assertEqual(env.world.s["robot"]["room"], "hall")
                self.assertEqual(set(env.world.held_items()), {BROOM, PAN})
                self.ok(env, "place", h(BROOM), "hall")
                self.ok(env, "place", h(PAN), "hall")
                self.assertNotIn(PAN, env.world.held_items())
                self.assertEqual(env.world.s["entities"][PAN]["location"], "hall")
                self.ok(env, "approach", h("trash"))
                self.ok(env, "empty_into", h(PAN), h("trash"))
                self.assertEqual(env.world.descendants(PAN), [])
                for sid in inside:
                    self.assertEqual(env.world.s["entities"][sid]["location"], "trash")
                self.assertEqual(env.world.s["stats"].get("safety_violations", 0), 0)

    def test_empty_it_while_it_is_still_in_hand(self):
        for perception in self.PERCEPTIONS:
            with self.subTest(perception=perception):
                env = self.make(perception)
                h = self.ids(env)
                inside = self.swept(env)
                self.ok(env, "move", "hall")
                self.ok(env, "approach", h("trash"))
                self.ok(env, "empty_into", h(PAN), h("trash"))
                self.assertIn(PAN, env.world.held_items())
                self.assertEqual(env.world.s["entities"][inside[0]]["location"], "trash")

    def test_the_same_walk_without_any_new_field_on_the_pieces(self):
        env = self.make("oracle")
        inside = self.swept(env)
        fields = set(env.world.s["entities"][inside[0]])
        self.ok(env, "move", "hall")
        self.assertEqual(set(env.world.s["entities"][inside[0]]), fields)
        self.assertFalse(env.world.s["entities"][inside[0]].get("stowed"))

    def held_with(self, eid, type_name, props, shard_load=None):
        """A house whose robot holds a box that already holds `eid`, with or without a shard_load."""
        box_props = {"pos": [4, 3], "label": "box", "accepts_items": True, "container": True, "capacity_l": 5.0,
                     "inner_size_m": [0.28, 0.2, 0.1], "size_m": [0.3, 0.25, 0.12], "mass_kg": 0.2}
        if shard_load is not None:
            box_props["shard_load"] = shard_load
        env_cfg = glass_house(extra={"box": ("generic_item", "kitchen", box_props),
                                     eid: (type_name, "box", dict(props, relation="in"))})
        box = env_cfg["entities"]["box"]
        box.update(location="robot", relation="held", held_hand="right")
        box.pop("pos", None)
        env_cfg["robot"]["hands"] = {"left": None, "right": "box"}
        env = HouseEnv(env_cfg, None, 3, perception="oracle", trace=False, observe_on_step=True)
        env.observe()
        return env

    def assert_refused(self, env):
        result = env.step("move", "hall")
        self.assertFalse(result["ok"], result["message"])
        self.assertEqual(result["category"], "safety")
        self.assertEqual(env.world.s["robot"]["room"], "kitchen")

    def test_the_piece_itself_still_needs_its_grip(self):
        env = self.make("oracle", extra={"shard": ("glass_shard", "counter", {"relation": "on", "shard_size": "large"})})
        result = env.step("pick_up", "shard")
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "safety")
        env = self.make("realistic", extra={"shard": ("glass_shard", "counter", {"relation": "on", "shard_size": "large"})})
        result = env.step("pick_up", self.ids(env)("shard"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "safety")

    def test_a_knife_in_a_loaded_receiver_still_needs_its_grip(self):
        knife = {"label": "knife", "hazards": ["sharp"], "safe_grips": ["handle"], "size_m": [0.2, 0.03, 0.02]}
        self.assert_refused(self.held_with("knife", "generic_item", knife, shard_load=3))

    def test_glass_in_a_receiver_nothing_was_swept_into_still_needs_its_grip(self):
        self.assert_refused(self.held_with("shard", "glass_shard", {"shard_size": "large"}))
        self.assert_refused(self.held_with("shard", "glass_shard", {"shard_size": "large"}, shard_load=0))

    def test_glass_in_a_loaded_receiver_passes_and_the_rule_reads_the_receiver(self):
        env = self.held_with("shard", "glass_shard", {"shard_size": "large"}, shard_load=1)
        self.ok(env, "move", "hall")

    def test_a_loaded_pan_put_down_is_lifted_again(self):
        """Set down to free a hand, a loaded pan comes up by its handle like any pan: no safe grip for the pieces, no
        gloves. Otherwise the robot strands it wherever it put it."""
        for perception in self.PERCEPTIONS:
            for grip in (None, "handle"):
                with self.subTest(perception=perception, grip=grip):
                    env = self.make(perception)
                    h = self.ids(env)
                    inside = self.swept(env)
                    self.ok(env, "place", h(BROOM), "kitchen")
                    self.ok(env, "place", h(PAN), "kitchen")
                    self.ok(env, "crouch")
                    self.ok(env, "pick_up", h(PAN), **({"grip": grip} if grip else {}))
                    self.ok(env, "stand")
                    self.ok(env, "move", "hall")
                    self.assertIn(PAN, env.world.held_items())
                    self.assertEqual(env.world.s["entities"][inside[0]]["location"], PAN)
                    self.assertEqual(env.world.s["stats"].get("safety_violations", 0), 0)

    def lifting(self, eid, type_name, props, shard_load=None, perception="oracle"):
        """Robot beside a box on the floor that already holds `eid`, with or without a shard_load: the lift's result."""
        box_props = {"pos": [4, 3], "label": "box", "accepts_items": True, "container": True, "capacity_l": 5.0,
                     "inner_size_m": [0.28, 0.2, 0.1], "size_m": [0.3, 0.25, 0.12], "mass_kg": 0.2}
        if shard_load is not None:
            box_props["shard_load"] = shard_load
        env = self.make(perception, extra={"box": ("generic_item", "kitchen", box_props),
                                           eid: (type_name, "box", dict(props, relation="in"))})
        self.ok(env, "crouch")
        return env.step("pick_up", self.ids(env)("box"))

    def test_lifting_keeps_the_refusals(self):
        knife = {"label": "knife", "hazards": ["sharp"], "safe_grips": ["handle"], "size_m": [0.2, 0.03, 0.02]}
        result = self.lifting("knife", "generic_item", knife, shard_load=3)
        self.assertEqual((result["ok"], result["category"]), (False, "safety"), "a knife in a loaded receiver")
        for perception in self.PERCEPTIONS:
            for load in (None, 0):
                with self.subTest(perception=perception, shard_load=load):
                    result = self.lifting("shard", "glass_shard", {"shard_size": "large"}, shard_load=load, perception=perception)
                    self.assertEqual((result["ok"], result["category"]), (False, "safety"), "glass nothing was swept into")


# ---------------------------------------------------------------------------------------------- K4


def door_house(**crate):
    """A hallway and a dark-free bedroom behind a closed door that swings into the bedroom, a crate in its arc (40 kg
    unless crate says otherwise: its size_m and mass_kg choose how the door meets it)."""
    ents = {}

    def add(eid, type_name, loc, **props):
        ents[eid] = REG.make(type_name, loc, **props)

    rooms = {"hall": {"label": "Hallway", "type": "hallway", "size_m": [2.0, 6.0], "exits": ["bedroom"], "ceiling_m": 2.5},
             "bedroom": {"label": "Bedroom", "type": "bedroom", "size_m": [3.5, 4.0], "exits": ["hall"], "ceiling_m": 2.5}}
    for rid, room in rooms.items():
        add("floor_" + rid, "floor_surface", rid, size_m=room["size_m"] + [0.01], pos=[0, 0])
        add("light_" + rid, "ceiling_light", rid, pos=[1, 1], on=True)
    add("door_bed", "door", "hall", connects=["hall", "bedroom"], pos=[3, 2], open=False, blocks_path=False, label="bedroom door",
        mech={"swing_into": "bedroom"})
    add("crate_1", "generic_item", "bedroom", pos=[1, 2], label="crate", portable=True,
        **dict({"size_m": [0.5, 0.5, 0.6], "mass_kg": 40.0}, **crate))
    return {"schema_version": 2, "start_room": "hall", "rooms": rooms,
            "connections": {"c_hb": {"rooms": ["hall", "bedroom"], "cells": {"hall": [3, 2], "bedroom": [0, 2]}, "door": "door_bed", "width_m": 0.85}},
            "entities": ents, "people": {}, "pets": {}, "clock": {"start": "2026-10-09T12:00:00"},
            "robot": {"room": "hall", "pos": [2, 2]}, "household": {"rules": []},
            "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 1.0}}


class DoorMessageTests(unittest.TestCase):
    # how the door meets the crate -> (the crate, the note with the crate's name left out)
    MET = {"slides aside": ({"mass_kg": 1.0}, "The door met {} and pushed it aside"),
           "tips over": ({"mass_kg": 1.0, "size_m": [0.1, 0.1, 1.0]}, "The door swung into {} and knocked it over"),
           "low, shoved along": ({"mass_kg": 1.0, "size_m": [0.3, 0.3, 0.1]}, "The door met {} and pushed it aside")}

    def opened(self, perception, known=False, **crate):
        env = HouseEnv(door_house(**crate), None, 3, perception=perception, trace=False, observe_on_step=True)
        env.observe()
        door = env.realism.handles.handle("door_bed") if env.realism is not None else "door_bed"
        if known:
            env.realism.handles.handle("crate_1")
        # with navigation_robot loaded its own hook nudges a low item: the mechanisms line runs without that domain
        with mock.patch("house_sim.systems.mechanisms.common.navigation_loaded", return_value=False):
            result = env.step("open", door)
        notes = " ".join(str(n) for n in (result["observation"] or {}).get("mechanism_notes") or [])
        return env, "{} {}".format(result["message"], notes)

    def test_every_way_the_door_meets_a_thing_drops_the_article_for_the_robot(self):
        for name, (crate, note) in sorted(self.MET.items()):
            with self.subTest(met=name, shown="never"):
                env, text = self.opened("realistic", **crate)
                handle = env.realism.handles.by_id.get("crate_1")
                self.assertTrue(any(note.format(word) in text for word in ("something", handle) if word), text)
                self.assertNotIn("the something", text)
                self.assertNotIn("the {}".format(handle), text)
            with self.subTest(met=name, shown="has a handle"):
                env, text = self.opened("realistic", known=True, **crate)
                self.assertIn(note.format(env.realism.handles.by_id["crate_1"]), text)

    def test_every_way_the_door_meets_a_thing_keeps_the_real_name_in_oracle_mode(self):
        for name, (crate, note) in sorted(self.MET.items()):
            with self.subTest(met=name):
                _, text = self.opened("oracle", **crate)
                self.assertIn(note.format("the crate"), text)

    def test_a_thing_never_shown_is_something_with_no_article(self):
        _, text = self.opened("realistic")
        self.assertIn("against something", text)
        self.assertNotIn("the something", text)

    def test_a_thing_the_robot_has_a_handle_for_is_named_by_it(self):
        env, text = self.opened("realistic", known=True)
        handle = env.realism.handles.by_id["crate_1"]
        self.assertIn("against {}".format(handle), text)
        self.assertNotIn("the {}".format(handle), text)

    def test_the_oracle_wording_keeps_its_article(self):
        _, text = self.opened("oracle")
        self.assertIn("The door stops against the crate", text)


# ---------------------------------------------------------------------------------------------- the shut_off refusal


def laundry_house(robot_pos):
    """A lit laundry room: a washer on one wall, its two stops on the far wall, out of reach from the washer."""
    ents = {}

    def add(eid, type_name, loc, **props):
        ents[eid] = REG.make(type_name, loc, **props)

    rooms = {"laundry": {"label": "Laundry", "type": "laundry_room", "size_m": [4.0, 4.0], "exits": [], "ceiling_m": 2.5}}
    add("floor_laundry", "floor_surface", "laundry", size_m=[4.0, 4.0, 0.01], pos=[0, 0])
    add("light_laundry", "ceiling_light", "laundry", pos=[1, 1], on=True)
    add("washer_1", "top_load_washer", "laundry", pos=[0, 0])
    add("cold_1", "shutoff_valve", "laundry", pos=[7, 7], mounted=True, mount_height_m=1.0, blocks_path=False, line="cold",
        feeds=["washer_1"])
    add("hot_1", "shutoff_valve", "laundry", pos=[6, 7], mounted=True, mount_height_m=1.0, blocks_path=False, line="hot",
        feeds=["washer_1"])
    return {"schema_version": 2, "start_room": "laundry", "rooms": rooms, "connections": {}, "entities": ents, "people": {},
            "pets": {}, "clock": {"start": "2026-10-09T12:00:00"}, "robot": {"room": "laundry", "pos": list(robot_pos)},
            "household": {"rules": []}, "utilities": {"power": True, "water": True, "gas": True},
            "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 1.0}}


class ShutOffRefusalTests(unittest.TestCase):
    """shut_off on a fixture walks its stops: out of reach, the refusal is about a stop, not the fixture just approached.
    Only the words change: the category, the state and every other refusal are what they were."""
    KERNEL_TEXT = "Approach it first; it is out of arm's reach"

    def make(self, perception, pos):
        env = HouseEnv(laundry_house(pos), None, 3, perception=perception, trace=False, observe_on_step=True)
        env.observe()
        return env, (env.realism.handles.handle if env.realism is not None else (lambda eid: eid))

    def test_beside_the_washer_the_refusal_names_the_stop_not_the_washer(self):
        for perception in ("oracle", "realistic"):
            with self.subTest(perception=perception):
                env, h = self.make(perception, (2, 1))
                result = env.step("shut_off", h("washer_1"))
                self.assertFalse(result["ok"])
                self.assertEqual(result["category"], "capability")
                self.assertNotIn(self.KERNEL_TEXT, result["message"])
                self.assertIn("stop valve", result["message"])
                self.assertIn("arm's reach", result["message"])         # tools/fuzz.py's repair reads these words
                self.assertIn("approach", result["message"].lower())
                self.assertEqual(env.world.s["stats"]["invalid"], 1)
                self.assertTrue(env.world.s["entities"]["cold_1"].get("valve_open", True))

    def test_the_kernel_s_words_stay_where_they_are_true(self):
        for perception in ("oracle", "realistic"):
            with self.subTest(perception=perception, target="the washer, from across the room"):
                env, h = self.make(perception, (7, 6))
                result = env.step("shut_off", h("washer_1"))
                self.assertEqual((result["ok"], result["message"]), (False, self.KERNEL_TEXT))
            with self.subTest(perception=perception, target="the valve itself"):
                env, h = self.make(perception, (2, 1))
                result = env.step("shut_off", h("cold_1"))
                self.assertEqual((result["ok"], result["message"]), (False, self.KERNEL_TEXT))

    def test_within_reach_of_both_it_still_closes_them(self):
        env, h = self.make("oracle", (1, 1))
        for vid in ("cold_1", "hot_1"):
            env.world.begin()
            env.world.mut(vid)["pos"] = [2, 1]
            env.world.commit()
        result = env.step("shut_off", "washer_1")
        self.assertTrue(result["ok"], result["message"])
        self.assertFalse(env.world.s["entities"]["cold_1"]["valve_open"])
        self.assertFalse(env.world.s["entities"]["hot_1"]["valve_open"])


# ---------------------------------------------------------------------------------------------- V1


def prior_config():
    ents = {"light_1": {"type": "lamp", "location": "lab", "pos": [11, 0], "on": True},
            "light_2": {"type": "lamp", "location": "hall", "pos": [0, 0], "on": True},
            "table_1": {"type": "table", "location": "lab", "pos": [3, 3], "accepts_items": True},
            "jar_1": {"type": "generic_item", "label": "salt jar", "location": "table_1", "relation": "on",
                      "looks_like": "white_powder_jar", "label_text": "SALT", "size_m": [0.08, 0.08, 0.12]},
            "jar_2": {"type": "generic_item", "label": "sugar jar", "location": "table_1", "relation": "on",
                      "looks_like": "white_powder_jar", "label_text": "SUGAR", "size_m": [0.08, 0.08, 0.12]},
            "box_1": {"type": "box", "location": "hall", "pos": [1, 2], "accepts_items": True}}
    cfg = {"schema_version": 2, "start_room": "lab",
           "rooms": {"lab": {"size_m": [6.0, 4.0], "type": "living_room", "label": "Living room", "exits": ["hall"]},
                     "hall": {"size_m": [2.0, 3.0], "type": "hallway", "label": "Hall", "exits": ["lab"]}},
           "entities": ents, "people": {"ann": {"label": "Ann", "role": "adult", "location": "lab", "pos": [6, 6]}},
           "robot": {"room": "lab", "pos": [2, 3]}}
    from house_sim.tasks.setup import apply_setup
    ops = [{"op": "prior_knowledge", "map": "all", "seen": [{"entity": "jar_1", "level": "read"},
                                                             {"entity": "box_1", "level": "inspect"}]},
           {"op": "move", "target": "box_1", "to": "lab"}]
    apply_setup(cfg, ops, REG, 4, {"catalog_id": "test", "setup": ops})
    return cfg


class LiveEnvParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flagship_viewer_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def record(self, env, commands, name="episode"):
        self.recorded_messages = [env.step(**command)["message"] for command in commands]
        path = Path(self.tmp) / (name + ".json")
        env.save_replay(path)
        return path

    def live(self, path):
        episode = viewer.load_source(str(path), private=False)
        warnings = []
        env = viewer._live_env(episode, warnings, trace=False)
        self.assertEqual(warnings, [])
        results = [env.step(**dict(record["command"])) for record in episode["records"]]
        return env, results

    def prior_episode(self):
        task = {"catalog_id": "probe", "title": "probe", "instruction": "look", "success": [], "step_budget": 50,
                "time_budget_s": 600, "episode_nonce": "probe-nonce"}
        env = HouseEnv(prior_config(), task, 4, perception="realistic", trace=True, observe_on_step=True)
        env.observe()
        return env, [dict(action="look"), dict(action="recall", query="seen:"), dict(action="move", target="hall"),
                     dict(action="look"), dict(action="move", target="lab"), dict(action="recall", query="seen:")]

    def test_prior_knowledge_gives_the_replay_s_robot_memory_and_handles(self):
        recorded, commands = self.prior_episode()
        path = self.record(recorded, commands)
        replayed = HouseEnv.replay(path)
        live, results = self.live(path)
        for name, env in (("replay", replayed), ("viewer", live)):
            with self.subTest(env=name):
                self.assertEqual(env.mind.dump(), recorded.mind.dump())
                self.assertEqual(env.realism.dump(), recorded.realism.dump())
                self.assertEqual(digest(env.world.s), digest(recorded.world.s))
        # what the page shows of the robot's view: the jar it read yesterday, under the handle prior knowledge issued
        self.assertEqual(live.realism.knowledge.entity_level("jar_1", live.world.s["entities"]["jar_1"]),
                         recorded.realism.knowledge.entity_level("jar_1", recorded.world.s["entities"]["jar_1"]))
        self.assertEqual(live.observe(), recorded.observe())
        self.assertEqual([result["message"] for result in results], self.recorded_messages)

    def test_the_viewer_page_shows_no_divergence_on_a_prior_knowledge_episode(self):
        recorded, commands = self.prior_episode()
        path = self.record(recorded, commands)
        data = viewer.build_viewer_data(str(path), view="both")
        self.assertEqual(data["meta"]["warnings"], [])
        self.assertFalse(data["meta"].get("diverged_at"))

    def test_the_timeline_and_the_writer_log_run_on_the_re_run_s_world(self):
        recorded, commands = self.prior_episode()
        path = self.record(recorded, commands)
        episode = viewer.load_source(str(path), private=False)
        env = viewer._live_env(episode, [], trace=False)
        self.assertIs(env.world._env_ref(), env)
        self.assertEqual(env._timeline["robot_room"], env.world.s["robot"]["room"])
        for record in episode["records"]:
            env.step(**dict(record["command"]))
        self.assertEqual(env.writes.dump(), recorded.writes.dump())

    def test_a_hardened_episode_judges_the_same_in_the_viewer_as_in_the_replay(self):
        """The ledger reads the writer log: without it the viewer's frames charge what the replay does not."""
        spec = hardened_spec()
        config, task, plans = build(spec, 1)
        task = dict(task, episode_nonce="flagship-fixes")
        switch = plans["bindings"]["switch"]
        env = make_env(config, task, 1, fast=False, perception="realistic")
        handle = env.realism.handles.handle(switch) if env.realism is not None else switch
        path = self.record(env, [dict(action="approach", target=handle), dict(action="switch_on", target=handle)], "hardened")
        replayed = HouseEnv.replay(path)
        live, _ = self.live(path)
        self.assertEqual(digest(live.world.s), digest(replayed.world.s))
        self.assertEqual(live.writes.dump(), replayed.writes.dump())
        ours, theirs = live.evaluate(), replayed.evaluate()
        self.assertEqual(json.dumps(ours, sort_keys=True, default=str), json.dumps(theirs, sort_keys=True, default=str))


if __name__ == "__main__":
    unittest.main()
