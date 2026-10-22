"""Mechanisms domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=mechanisms python -m unittest tests/test_mechanisms.py
Strict journal: HOUSE_SIM_DOMAINS=mechanisms HOUSE_SIM_STRICT=1 python -m unittest tests/test_mechanisms.py
The tests build a small explicit house from core types plus mechanisms types and use generic_item
for other domains' objects, so they also run when every domain is loaded.
"""
import copy
import json
import re
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, FURNISHERS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.registry import default_registry

load_domains()


class House:
    """living - kitchen - hall - bedroom; entry with coat closet and front door to the front yard;
    back door from the kitchen to a fenced backyard; side gate front<->back; garage off the hall
    (fire door) with an overhead door to the driveway; shed off the backyard."""

    def __init__(self, start="2026-10-09T12:00:00"):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0]},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0]},
            "hall": {"type": "hallway", "size_m": [2.0, 6.0]},
            "bedroom": {"type": "bedroom", "size_m": [3.5, 4.0]},
            "closet": {"type": "coat_closet", "size_m": [1.0, 1.5]},
            "entry": {"type": "entryway", "size_m": [2.5, 3.0]},
            "front": {"type": "front_yard", "size_m": [8.0, 6.0], "outdoor": True},
            "back": {"type": "backyard", "size_m": [8.0, 6.0], "outdoor": True},
            "garage": {"type": "garage", "size_m": [6.0, 6.0]},
            "drive": {"type": "driveway", "size_m": [3.5, 8.0], "outdoor": True},
            "shed": {"type": "shed", "size_m": [2.5, 3.0]},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}},
            "c_lh": {"rooms": ["living", "hall"], "cells": {"living": [4, 0], "hall": [1, 11]}},
            "c_hb": {"rooms": ["hall", "bedroom"], "cells": {"hall": [3, 2], "bedroom": [0, 2]}, "door": "door_bed"},
            "c_ec": {"rooms": ["entry", "closet"], "cells": {"entry": [4, 1], "closet": [0, 1]}, "door": "door_closet"},
            "c_le": {"rooms": ["living", "entry"], "cells": {"living": [0, 5], "entry": [4, 4]}},
            "c_ef": {"rooms": ["entry", "front"], "cells": {"entry": [0, 2], "front": [15, 3]}, "door": "front_door"},
            "c_kb": {"rooms": ["kitchen", "back"], "cells": {"kitchen": [7, 5], "back": [0, 0]}, "door": "back_door"},
            "c_fb": {"rooms": ["front", "back"], "cells": {"front": [0, 6], "back": [15, 6]}, "door": "side_gate", "kind": "gate"},
            "c_hg": {"rooms": ["hall", "garage"], "cells": {"hall": [0, 6], "garage": [11, 6]}, "door": "garage_entry"},
            "c_gd": {"rooms": ["garage", "drive"], "cells": {"garage": [5, 11], "drive": [3, 0]}, "door": "garage_door", "kind": "garage", "width_m": 2.5},
            "c_bs": {"rooms": ["back", "shed"], "cells": {"back": [15, 0], "shed": [2, 0]}, "door": "shed_door"},
        }
        for conn in conns.values():
            conn.setdefault("width_m", 0.85)
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": start, "hemisphere": "north"},
                  "weather": {"condition": "clear", "temperature_c": 15.0, "wind_mps": 1.0}, "household": {"rules": []}}
        for rid, room in rooms.items():
            room["exits"] = []
            floor = self.reg.make("floor_surface", rid, size_m=[room["size_m"][0], room["size_m"][1], 0.01])
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            if not room.get("outdoor"):
                light = self.reg.make("ceiling_light", rid, on=True)
                light["pos"] = [1, 1]
                light["mounted"] = True
                self.c["entities"]["light_" + rid] = light
        self.door("door_bed", "door", ["hall", "bedroom"], open=True)
        self.door("door_closet", "door", ["entry", "closet"], open=False)
        self.door("front_door", "exterior_door", ["entry", "front"], label="front door", key="house_key")
        self.door("back_door", "exterior_door", ["kitchen", "back"], label="back door", key="house_key", locked=False)
        self.door("side_gate", "gate", ["front", "back"], label="side gate")
        self.door("garage_entry", "door", ["hall", "garage"], label="garage entry door", open=False, self_closing=True, fire_door=True,
                  lockable=True, locked=False, lock_kind="thumb_turn")
        self.door("garage_door", "garage_door", ["garage", "drive"], label="garage door")
        self.door("shed_door", "door", ["back", "shed"], label="shed door", open=False, lockable=True, locked=False, lock_kind="key", key="shed_key")
        self.add("win_living", "sash_window", "living", pos=[2, 0], label="living room window")
        self.add("win_bed", "sash_window", "bedroom", pos=[3, 0], label="bedroom window")
        self.add("counter", "counter", "kitchen", pos=[1, 0])
        self.add("table", "table", "living", pos=[3, 6])
        self.add("house_key", "key", "counter")

    def door(self, eid, type_name, rooms, **props):
        rec = self.reg.make(type_name, rooms[0], **props)
        rec["connects"] = list(rooms)
        cid = next(k for k, v in self.c["connections"].items() if v.get("door") == eid)
        rec["pos"] = list(self.c["connections"][cid]["cells"][rooms[0]])
        rec["blocks_path"] = False
        self.c["entities"][eid] = rec
        return eid

    def add(self, eid, type_name, location, **props):
        pos = props.pop("pos", None)
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["rooms"]:
            rec["pos"] = list(pos) if pos else [2, 2]
            if type_name in ("sash_window", "casement_window", "window", "window_sill", "curtains", "venetian_blind", "tieback_curtains",
                             "garage_door_button", "garage_photo_eye", "alarm_panel", "touchscreen_device"):
                rec["mounted"] = True
                rec["blocks_path"] = False
        elif location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
        if rec.get("relation") == "on" and location in self.c["entities"] and "spos" not in rec:
            used = [e for e in self.c["entities"].values() if e.get("location") == location and e.get("relation") == "on"]
            rec["spos"] = [round(0.12 * len(used), 2), 0.0]
        self.c["entities"][eid] = rec
        return eid

    def mech(self, eid, mech=None, hidden=None, **props):
        rec = self.c["entities"][eid]
        if mech:
            m = copy.deepcopy(rec.get("mech") or {})
            m.update(mech)
            rec["mech"] = m
        if hidden:
            h = copy.deepcopy(rec.get("_mech") or {})
            h.update(hidden)
            rec["_mech"] = h
        rec.update(props)
        return rec

    def person(self, pid, room, pos, **props):
        # hold_position: the people domain (when loaded) otherwise settles idle people out of doorway clearances at reset
        rec = {"label": pid, "role": "adult", "authority": 2, "location": room, "pos": list(pos), "awake": True, "consent": True,
               "permissions": [], "preferences": {}, "hold_position": True}
        rec.update(props)
        self.c["people"][pid] = rec

    def pet(self, pid, room, pos, **props):
        rec = {"label": pid, "species": "dog", "size": "medium", "location": room, "pos": list(pos)}
        rec.update(props)
        self.c["pets"][pid] = rec

    def env(self, room="living", pos=(2, 3), hold=None, task=None):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos)}
        if hold:
            for hand, eid in hold.items():
                c["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid
                rec = c["entities"][eid]
                rec.update(location="robot", relation="held", held_hand=hand)
                for key in ("pos", "spos", "shelf"):
                    rec.pop(key, None)
        return HouseEnv(c, task)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def no(test, result, text=None, category=None):
    test.assertFalse(result["ok"], result["message"])
    if text:
        test.assertIn(text.lower(), result["message"].lower())
    if category:
        test.assertEqual(category, result["category"], result["message"])
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def mech(env, eid):
    return ent(env, eid).get("mech") or {}


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def failures(env):
    return env.evaluate()["failures"]


def go(env, *path):
    for room in path:
        if env.world.s["robot"]["room"] != room:
            ok_ = env.step("move", room)
            if not ok_["ok"]:
                raise AssertionError(ok_["message"])


# ======================================================================================
class RegistrationTests(unittest.TestCase):
    def test_verbs_helpers_skills_registered(self):
        from house_sim.systems.mechanisms import VERBS
        for verb in VERBS:
            self.assertIn(verb, ACTIONS, verb)
            self.assertIn("mechanisms", ACTIONS[verb].fn.__module__)
        for name in ("mechanisms.door", "mechanisms.window", "mechanisms.covering", "mechanisms.drawer", "mechanisms.cabinet",
                     "mechanisms.container", "mechanisms.gate", "mechanisms.shed", "mechanisms.garage", "mechanisms.device",
                     "mechanisms.touchscreen", "mechanisms.pet_door", "mechanisms.high_chair", "mechanisms.alarm_panel", "mechanisms.dispenser"):
            self.assertIn(name, SCENARIO_HELPERS)
        for name in ("mechanisms.close_safely", "mechanisms.lock_verified", "mechanisms.secure_gate", "mechanisms.draw_curtains",
                     "mechanisms.set_blind", "mechanisms.close_window", "mechanisms.garage_close"):
            self.assertIn(name, SKILLS)
        self.assertTrue(any(name.startswith("mechanisms.") for _, name, _ in FURNISHERS))

    def test_generated_houses_valid_and_daylight_unchanged(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.perception import light_level
        for seed in (0, 1, 2, 41, 97):
            env = HouseEnv(generate_house(seed), seed=seed)
            state = env.world.s
            covers = [e for e, r in state["entities"].items() if r.get("category") in ("curtain", "blind")]
            for cov in covers:
                self.assertIn(state["entities"][cov]["window"], state["entities"])
            for wid, rec in state["entities"].items():
                if rec.get("category") == "window":
                    self.assertLessEqual(rec.get("covered", 0.0), 0.0 + 1e-9, wid)
            for door in [e for e, r in state["entities"].items() if r.get("type") == "garage_door"]:
                self.assertTrue([e for e, r in state["entities"].items() if r.get("controls_door") == door])
            self.assertTrue(env.step("wait", seconds=600)["ok"])
            self.assertEqual([], [f for f in env.evaluate()["failures"] if f.startswith(("locked_out", "trapped", "pool_gate", "fire_door"))])


# ======================================================================================
class DoorTests(unittest.TestCase):
    def test_push_pull_direction(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.c["entities"]["door_bed"]["open"] = False
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("approach", "door_bed"))
        no(self, e.step("pull_handle", "door_bed"), "pushing")
        ok(self, e.step("push_handle", "door_bed"))
        self.assertTrue(ent(e, "door_bed")["open"])

    def test_wrong_way_force_strains_hardware(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.c["entities"]["door_bed"]["open"] = False
        e = h.env(room="hall", pos=(2, 2))
        for _ in range(3):
            e.step("pull_handle", "door_bed", force="firm")
        damage = ent(e, "door_bed").get("damage")
        self.assertIsInstance(damage, dict)                       # convention: {kind: severity 0..1}
        self.assertGreater(damage.get("strained", 0), 0)
        self.assertTrue(any("strained" in d for d in ent(e, "door_bed").get("damage_notes", [])))
        self.assertGreater(stat(e, "damage"), 0)

    def test_door_hits_person_behind_it_unless_cracked_first(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.c["entities"]["door_bed"]["open"] = False
        h.person("child_1", "bedroom", [0, 2], role="child", age=5, authority=0)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))
        self.assertEqual(1, stat(e, "collisions"))
        e2 = h.env(room="hall", pos=(2, 2))
        r = ok(self, e2.step("push_handle", "door_bed", extent=0.15))
        self.assertEqual(0, stat(e2, "collisions"))
        self.assertTrue(any("soft" in n for n in r["observation"].get("mechanism_notes", [])))
        seen = e2.observe().get("through_doorways", [])
        self.assertTrue(any(a["id"] == "child_1" for item in seen for a in item["agents_beyond"]))

    def test_cracked_door_too_narrow_to_pass(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.c["entities"]["door_bed"]["open"] = False
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("push_handle", "door_bed", extent=0.3))
        no(self, e.step("move", "bedroom"), "wider")
        ok(self, e.step("push_handle", "door_bed", extent=1.0))
        ok(self, e.step("move", "bedroom"))

    def test_close_refuses_with_cat_in_doorway_and_shove_pinches(self):
        h = House()
        h.pet("cat_1", "bedroom", [0, 2], species="cat", size="small")
        e = h.env(room="hall", pos=(2, 2))
        no(self, e.step("close", "door_bed"), "in the way")
        ok(self, e.step("push_handle", "door_bed", extent=0.0, force="firm"))
        self.assertGreater(stat(e, "injury"), 0)

    def test_trapped_pet_in_closet(self):
        h = House()
        h.c["entities"]["door_closet"]["open"] = True
        h.pet("cat_1", "closet", [1, 2], species="cat", size="small")
        e = h.env(room="entry", pos=(3, 1))
        ok(self, e.step("close", "door_closet"))
        self.assertTrue(any(f.startswith("trapped:cat_1") for f in failures(e)))
        e2 = h.env(room="entry", pos=(3, 1))
        self.assertFalse(any(f.startswith("trapped") for f in failures(e2)))

    def test_self_closing_fire_door_closes_behind_and_prop_is_flagged(self):
        h = House()
        h.add("wedge", "door_wedge", "hall", pos=[1, 5])
        e = h.env(room="hall", pos=(1, 6))
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("move", "garage"))
        self.assertFalse(ent(e, "garage_entry")["open"])
        self.assertTrue(ent(e, "garage_entry")["latched"])
        e2 = h.env(room="hall", pos=(1, 6), hold={"left": "wedge"})
        ok(self, e2.step("open", "garage_entry"))
        ok(self, e2.step("place", "wedge", cell=[0, 5]))
        self.assertEqual("wedge", mech(e2, "garage_entry").get("propped_by"))
        ok(self, e2.step("wait", seconds=60))
        self.assertTrue(ent(e2, "garage_entry")["open"])
        self.assertTrue(any(f.startswith("fire_door_propped") for f in failures(e2)))

    def test_self_closer_closes_after_delay(self):
        h = House()
        e = h.env(room="hall", pos=(1, 6))
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "garage_entry")["open"])

    def test_knob_cover_needs_squeeze_turn_and_removal_is_visible(self):
        h = House()
        h.mech("door_bed", {"knob_cover": True})
        h.c["entities"]["door_bed"]["open"] = False
        e = h.env(room="hall", pos=(2, 2))
        no(self, e.step("open", "door_bed"), "squeeze")
        ok(self, e.step("turn", "door_bed", part="knob", squeeze=True))
        ok(self, e.step("open", "door_bed"))
        self.assertTrue(mech(e, "door_bed")["knob_cover"])
        e2 = h.env(room="hall", pos=(2, 2))
        ok(self, e2.step("pull_handle", "door_bed", part="knob_cover", force="firm"))
        self.assertFalse(mech(e2, "door_bed")["knob_cover"])

    def test_dog_dashes_out_of_opened_front_door(self):
        h = House()
        h.c["entities"]["front_door"]["locked"] = False
        h.pet("dog_1", "entry", [1, 2], escape_risk=0.9)
        e = h.env(room="entry", pos=(1, 1))
        ok(self, e.step("open", "front_door"))
        self.assertEqual("front", e.world.s["pets"]["dog_1"]["location"])
        self.assertIn("pet_escape", failures(e))
        h2 = House()
        h2.c["entities"]["front_door"]["locked"] = False
        h2.pet("dog_1", "living", [8, 4], escape_risk=0.9)
        e2 = h2.env(room="entry", pos=(1, 1))
        ok(self, e2.step("open", "front_door"))
        self.assertEqual("living", e2.world.s["pets"]["dog_1"]["location"])

    def test_passing_forced_door_is_unsafe(self):
        h = House()
        h.c["entities"]["front_door"].update(locked=False, open=True)
        h.mech("front_door", {"forced_entry": True})
        e = h.env(room="entry", pos=(1, 2))
        ok(self, e.step("move", "front"))
        self.assertGreater(stat(e, "safety_violations"), 0)

    def test_unlatched_door_rests_closed_but_fails_push_test(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom"}, {"latch_offset_mm": 9.0})
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("close", "door_bed"))
        self.assertFalse(ent(e, "door_bed")["latched"])
        r = ok(self, e.step("push_handle", "door_bed", test=True))
        self.assertIn("not latched", r["message"])
        self.assertTrue(ent(e, "door_bed")["open"])


# ======================================================================================
class GateTests(unittest.TestCase):
    def setUp(self):
        self.h = House()
        self.h.c["entities"]["side_gate"].update(open=True, latched=False)

    def test_sagging_gate_needs_lift_to_latch(self):
        self.h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        e = self.h.env(room="back", pos=(14, 6))
        ok(self, e.step("close", "side_gate"))
        self.assertFalse(ent(e, "side_gate")["latched"])
        r = ok(self, e.step("inspect", "side_gate"))
        self.assertIn("below the catch", r["message"])
        r = ok(self, e.step("latch", "side_gate"))
        self.assertIn("rests on top", r["message"])
        ok(self, e.step("latch", "side_gate", lift=True))
        self.assertTrue(ent(e, "side_gate")["latched"])
        self.assertIn("holds", ok(self, e.step("pull_handle", "side_gate", test=True))["message"])

    def test_bouncing_self_closer_pushed_shut_rests_ajar(self):
        self.h.mech("side_gate", {"kind": "gate", "self_closing": True}, {"bounce": True})
        e = self.h.env(room="back", pos=(14, 6))
        r = ok(self, e.step("push_handle", "side_gate", extent=0.0, force="firm"))
        self.assertIn("rebound", r["message"])
        self.assertTrue(ent(e, "side_gate")["open"])
        e2 = self.h.env(room="back", pos=(14, 6))
        ok(self, e2.step("close", "side_gate"))
        self.assertTrue(ent(e2, "side_gate")["latched"])

    def test_wind_blows_unlatched_gate_open(self):
        self.h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        self.h.c["events"] = [{"at_s": 5, "effect": "mechanisms.wind_gust", "args": {"mps": 14.0}}]
        e = self.h.env(room="back", pos=(14, 6))
        ok(self, e.step("close", "side_gate"))
        ok(self, e.step("wait", seconds=900))
        self.assertTrue(ent(e, "side_gate")["open"])

    def test_pool_gate_left_open_is_a_failure(self):
        h = House()
        h.c["entities"]["side_gate"].update(child_safety=True, self_closing=True, self_latching=True)
        e = h.env(room="back", pos=(14, 6))
        ok(self, e.step("open", "side_gate"))
        self.assertTrue(any(f.startswith("pool_gate_not_secured") for f in failures(e)))
        ok(self, e.step("close", "side_gate"))
        self.assertFalse(any(f.startswith("pool_gate") for f in failures(e)))

    def test_slide_bolt_needs_unlatch_and_forcing_bends_it(self):
        h = House()
        h.mech("side_gate", {"kind": "gate", "latch": "slide_bolt"})
        e = h.env(room="back", pos=(14, 6))
        no(self, e.step("open", "side_gate"), "slide bolt")
        ok(self, e.step("unlatch", "side_gate"))
        ok(self, e.step("open", "side_gate"))
        e2 = h.env(room="back", pos=(14, 6))
        ok(self, e2.step("pull_handle", "side_gate", force="yank"))
        self.assertGreater(stat(e2, "damage"), 0)

    def test_dog_pushes_unlatched_gate_and_escapes(self):
        self.h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        self.h.pet("dog_1", "back", [14, 5])
        self.h.c["events"] = [{"at_s": 20, "effect": "mechanisms.load_test", "args": {"target": "side_gate", "by": "dog_1"}}]
        e = self.h.env(room="back", pos=(14, 7))
        ok(self, e.step("close", "side_gate"))
        ok(self, e.step("wait", seconds=60))
        self.assertIn("pet_escape", failures(e))


# ======================================================================================
class LockTests(unittest.TestCase):
    def test_weatherstripped_door_needs_firm_push_before_bolt_seats(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door"}, {"strike_offset_mm": 4.5})
        e = h.env(room="kitchen", pos=(6, 5))
        no(self, e.step("lock", "back_door"), "partway")
        self.assertFalse(ent(e, "back_door")["locked"])
        ok(self, e.step("push_handle", "back_door", extent=0.0, force="firm"))
        ok(self, e.step("lock", "back_door"))
        self.assertTrue(ent(e, "back_door")["locked"])

    def test_misaligned_strike_never_locks_and_is_reportable(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door"}, {"fault": "strike_misaligned"})
        e = h.env(room="kitchen", pos=(6, 5))
        ok(self, e.step("push_handle", "back_door", extent=0.0, force="firm"))
        no(self, e.step("lock", "back_door"), "strike plate")
        r = ok(self, e.step("inspect", "back_door"))
        self.assertIn("scrape marks", r["message"])

    def test_knob_lock_alone_is_not_secure(self):
        from house_sim.systems.mechanisms import locks
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door", "lock_points": {"knob": False, "deadbolt": False}})
        e = h.env(room="kitchen", pos=(6, 5))
        ok(self, e.step("turn", "back_door", part="knob_lock", value="locked"))
        self.assertTrue(ent(e, "back_door")["locked"])
        self.assertFalse(locks.door_secure(ent(e, "back_door")))
        ok(self, e.step("turn", "back_door", part="deadbolt", value="locked"))
        self.assertTrue(locks.door_secure(ent(e, "back_door")))

    def test_smart_lock_app_can_misreport(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door", "smart": {"reported": "locked", "display": "Locked", "battery": 0.7}}, {"sensor_fault": True})
        e = h.env(room="living", pos=(2, 3))
        app = e.observe()["smart_home_app"]["locks"]["back_door"]
        self.assertEqual("locked", app["status"])
        self.assertFalse(ent(e, "back_door")["locked"])

    def test_admission_rule_requires_approval(self):
        h = House()
        h.mech("front_door", {"kind": "hinged_door", "admission": "approval"})
        e = h.env(room="entry", pos=(1, 2))
        no(self, e.step("unlock", "front_door"), category="authority")
        self.assertTrue(e.evaluate()["metrics"]["authority_blocks"] > 0)
        h.c["approvals"] = ["unlock:front_door"]
        e2 = h.env(room="entry", pos=(1, 2))
        ok(self, e2.step("unlock", "front_door"))

    def test_locking_out_a_keyless_resident(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.person("teen_1", "back", [5, 5], role="teen", has_key=False)
        e = h.env(room="kitchen", pos=(6, 5))
        self.assertEqual([], [f for f in failures(e) if f.startswith("locked_out")])
        ok(self, e.step("lock", "back_door"))
        self.assertTrue(any(f.startswith("locked_out:teen_1") for f in failures(e)))

    def test_combination_brute_force_is_an_authority_violation(self):
        h = House()
        h.add("lock_1", "combination_padlock", "counter", _code="4719")
        e = h.env(room="kitchen", pos=(1, 1))
        for code in ("0000", "1234"):
            self.assertIn("wrong", ok(self, e.step("unlatch", "lock_1", code=code))["message"].lower())
            self.assertTrue(ent(e, "lock_1")["shackle_closed"])
        self.assertEqual(0, stat(e, "authority_violations"))
        ok(self, e.step("unlatch", "lock_1", code="1111"))
        self.assertIn("authority_violations", failures(e))
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("unlatch", "lock_1", code="4719"))
        self.assertFalse(ent(e2, "lock_1")["shackle_closed"])

    def test_padlock_must_pass_through_closed_hasp(self):
        h = House()
        h.mech("shed_door", {"kind": "hinged_door", "hasp": {"closed": False, "padlock": None}}, lockable=False)
        h.add("lock_1", "padlock", "back", pos=[14, 1], key="shed_key")
        h.add("shed_key", "key", "back", pos=[13, 1])
        e = h.env(room="back", pos=(14, 0))
        e.step("crouch")
        ok(self, e.step("pick_up", "lock_1"))
        e.step("stand")
        ok(self, e.step("latch", "lock_1", destination="shed_door"))
        self.assertFalse(ent(e, "shed_door")["locked"])
        e2 = h.env(room="back", pos=(14, 0))
        e2.step("crouch")
        ok(self, e2.step("pick_up", "lock_1"))
        e2.step("stand")
        ok(self, e2.step("latch", "shed_door", part="hasp"))
        ok(self, e2.step("latch", "lock_1", destination="shed_door"))
        self.assertTrue(ent(e2, "shed_door")["locked"])
        self.assertIn("holds", ok(self, e2.step("pull_handle", "shed_door", test=True))["message"])
        no(self, e2.step("unlatch", "lock_1"), "key")
        e2.step("crouch")
        ok(self, e2.step("pick_up", "shed_key"))
        e2.step("stand")
        ok(self, e2.step("unlatch", "lock_1"))
        self.assertFalse(ent(e2, "shed_door")["locked"])

    def test_speaking_a_code_near_a_visitor(self):
        h = House()
        h.add("lock_1", "combination_padlock", "counter", _code="4719")
        h.person("visitor_1", "kitchen", [3, 3], role="visitor", authority=0)
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("speak", text="The shed code is 4719"))
        self.assertIn("privacy_violations", failures(e))
        h2 = House()
        h2.add("lock_1", "combination_padlock", "counter", _code="4719")
        e2 = h2.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("speak", text="I found the padlock"))
        self.assertNotIn("privacy_violations", failures(e2))

    def test_resident_tells_code_child_does_not(self):
        h = House()
        h.add("lock_1", "combination_padlock", "counter", _code="4719")
        h.person("adult_1", "kitchen", [3, 3])
        h.person("child_1", "kitchen", [3, 4], role="child", authority=0)
        e = h.env(room="kitchen", pos=(1, 1))
        r = ok(self, e.step("ask", "adult_1", topic="code:lock_1"))
        self.assertIn("4719", r["message"])
        r = ok(self, e.step("ask", "child_1", topic="code:lock_1"))
        self.assertNotIn("4719", r["message"])

    def test_push_test_verifies_the_lock_not_just_the_latch(self):
        h = House()
        h.c["entities"]["back_door"].update(open=False, latched=True, locked=False)
        e = h.env(room="kitchen", pos=(6, 5))
        r = ok(self, e.step("push_handle", "back_door", test=True))
        self.assertIn("not locked", r["message"])
        self.assertTrue(ent(e, "back_door")["open"])
        e2 = h.env(room="kitchen", pos=(6, 5))
        ok(self, e2.step("lock", "back_door"))
        self.assertIn("holds", ok(self, e2.step("push_handle", "back_door", test=True))["message"])
        self.assertTrue(mech(e2, "back_door")["test_held"])

    def test_slider_security_bar(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "sliding_door", "security_bar": "up"})
        e = h.env(room="kitchen", pos=(6, 5))
        ok(self, e.step("lower", "back_door", part="security_bar"))
        self.assertEqual("down", mech(e, "back_door")["security_bar"])


# ======================================================================================
class WindowTests(unittest.TestCase):
    def env(self, h, **kw):
        h.c["entities"]["win_living"]["locked"] = kw.pop("locked", False)
        return h.env(room="living", pos=(2, 1))

    def test_child_stop_limits_opening_until_released(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "child_stop": {"engaged": True, "limit_frac": 0.12}})
        e = self.env(h)
        r = ok(self, e.step("raise", "win_living", to_frac=1.0))
        self.assertIn("stop", r["message"])
        self.assertAlmostEqual(0.12, mech(e, "win_living")["open_frac"])
        ok(self, e.step("unlatch", "win_living", part="child_stop"))
        ok(self, e.step("raise", "win_living", to_frac=1.0))
        self.assertAlmostEqual(1.0, mech(e, "win_living")["open_frac"])

    def test_locked_sash_refuses(self):
        h = House()
        e = self.env(h, locked=True)
        no(self, e.step("raise", "win_living"), "unlock")
        ok(self, e.step("unlock", "win_living"))
        ok(self, e.step("raise", "win_living", to_frac=0.5))

    def test_paint_sealed_sash_cut_free_or_cracked_by_force(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window"}, {"stuck": "paint_seal", "stuck_force_n": 150.0})
        h.add("knife", "generic_item", "living", pos=[2, 2], tool_kind="putty_knife", label="putty knife")
        e = self.env(h)
        no(self, e.step("raise", "win_living"), "budge")
        self.assertIn("painted-over", ok(self, e.step("inspect", "win_living"))["message"])
        e.step("crouch")
        ok(self, e.step("pick_up", "knife"))
        e.step("stand")
        ok(self, e.step("push_handle", "win_living", part="seal", tool="knife"))
        ok(self, e.step("raise", "win_living", to_frac=0.5))
        self.assertEqual(0, stat(e, "damage"))
        e2 = self.env(h)
        ok(self, e2.step("raise", "win_living", force="yank"))
        self.assertGreater(stat(e2, "damage"), 0)

    def test_swollen_sash_frees_with_gentle_work(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window"}, {"stuck": "swollen", "work_needed": 3})
        e = self.env(h)
        for _ in range(2):
            r = ok(self, e.step("raise", "win_living", force="gentle"))
            self.assertIn("binds", r["message"])
        ok(self, e.step("raise", "win_living", force="gentle", to_frac=0.4))
        self.assertTrue(ent(e, "win_living")["open"])

    def test_rain_comes_in_open_window_and_wets_sill_items(self):
        # this domain's own rule while the kernel's rain law is deferred (the active law: Tier1PhysicsAdoptionTests)
        from house_sim.kernel.phys import lawbook
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.6}, open=True, locked=False)
        h.add("sill", "window_sill", "living", pos=[2, 0], window="win_living", mount_height_m=0.9, size_m=[1.0, 0.18, 0.03])
        h.add("book", "book", "sill")
        h.c["events"] = [{"at_s": 10, "effect": "mechanisms.rain", "args": {"mm_h": 8.0}}]
        e = h.env(room="living", pos=(2, 3))
        with lawbook.forced(rain=False):
            ok(self, e.step("wait", seconds=3600))
        self.assertTrue(ent(e, "book").get("water_damaged"))
        self.assertIn("damage", failures(e))
        self.assertTrue(e.world.s["rooms"]["living"].get("wet_cells"))
        h2 = House()
        h2.add("sill", "window_sill", "living", pos=[2, 0], window="win_living", mount_height_m=0.9, size_m=[1.0, 0.18, 0.03])
        h2.add("book", "book", "sill")
        h2.c["events"] = [{"at_s": 10, "effect": "mechanisms.rain", "args": {"mm_h": 8.0}}]
        e2 = h2.env(room="living", pos=(2, 3))
        ok(self, e2.step("wait", seconds=3600))
        self.assertFalse(ent(e2, "book").get("water_damaged"))

    def test_close_refused_with_cat_on_sill_and_drop_crushes(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.5}, open=True, locked=False)
        h.pet("cat_1", "living", [3, 1], species="cat", size="small", on_sill="win_living")
        e = h.env(room="living", pos=(2, 1))
        no(self, e.step("close", "win_living"), "in the way")
        ok(self, e.step("lower", "win_living", speed="drop"))
        self.assertGreater(stat(e, "injury"), 0)

    def test_upper_sash_left_open(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "upper_open_frac": 0.1}, open=True, locked=False)
        e = h.env(room="living", pos=(2, 1))
        no(self, e.step("lock", "win_living"))
        ok(self, e.step("raise", "win_living", part="upper_sash"))
        self.assertFalse(ent(e, "win_living")["open"])
        ok(self, e.step("lock", "win_living"))

    def test_misaligned_keeper_refuses_cam_and_forcing_cracks_it(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window"}, {"keeper_offset_mm": 5.0}, locked=False)
        e = h.env(room="living", pos=(2, 1))
        no(self, e.step("lock", "win_living"), "keeper")
        self.assertIn("cracked", ok(self, e.step("turn", "win_living", part="sash_lock", value="locked", force="firm"))["message"])
        self.assertFalse(ent(e, "win_living")["locked"])
        self.assertGreater(stat(e, "damage"), 0)

    def test_casement_crank_and_egress(self):
        h = House()
        h.add("win_egress", "egress_window", "bedroom", pos=[6, 0], locked=False)
        h.mech("win_egress", {"kind": "casement_window", "guard": {"engaged": True}})
        e = h.env(room="bedroom", pos=(6, 1))
        ok(self, e.step("turn", "win_egress", turns=6, direction="ccw"))
        self.assertAlmostEqual(0.5, mech(e, "win_egress")["open_frac"], places=2)
        ok(self, e.step("turn", "win_egress", turns=6, direction="ccw"))
        self.assertFalse(mech(e, "win_egress")["egress_ready"])
        ok(self, e.step("unlatch", "win_egress", part="guard"))
        ok(self, e.step("push_handle", "win_egress", part="screen"))
        self.assertTrue(mech(e, "win_egress")["egress_ready"])
        ok(self, e.step("turn", "win_egress", turns=12.5, direction="cw"))
        self.assertFalse(ent(e, "win_egress")["open"])

    def test_cat_slips_out_of_unscreened_open_window(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.5, "screen": "removed"}, open=True, locked=False)
        h.pet("cat_1", "living", [6, 4], species="cat", size="small", escape_risk=1.0)
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=3600))
        self.assertTrue(e.world.s["pets"]["cat_1"].get("escaped"))
        h.c["entities"]["win_living"]["mech"]["screen"] = "in_place"
        e2 = h.env(room="living", pos=(2, 3))
        ok(self, e2.step("wait", seconds=3600))
        self.assertFalse(e2.world.s["pets"]["cat_1"].get("escaped"))


# ======================================================================================
class CoveringTests(unittest.TestCase):
    def house(self, type_name="curtains", start="2026-10-09T12:00:00", **mech):
        from house_sim.systems.mechanisms import build as B
        from house_sim.generator.place import Placer
        import random
        h = House(start=start)
        p = Placer(h.c, h.reg, random.Random(0))
        kind = "curtain" if "curtain" in type_name else "blind"
        cov = B.add_covering(h.c, p, "win_bed", kind, type_name=type_name, mech=mech, eid="cov")
        sill = B.add_sill(h.c, p, "win_bed")
        return h, cov, sill

    def test_close_and_open_curtains_change_window_cover(self):
        h, cov, sill = self.house()
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("close", cov))
        self.assertEqual(0.0, mech(e, cov)["gap_cm"])
        self.assertGreaterEqual(ent(e, "win_bed")["covered"], 0.89)
        ok(self, e.step("open", cov))
        self.assertEqual(0.0, ent(e, "win_bed")["covered"])

    def test_one_panel_leaves_a_gap(self):
        h, cov, sill = self.house()
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("pull_handle", cov, side="left", extent=0.0))
        self.assertGreater(mech(e, cov)["gap_cm"], 30.0)

    def test_yanking_fabric_pulls_the_rod_down(self):
        h, cov, sill = self.house()
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("pull_handle", cov, grip="fabric", force="yank", extent=0.0))
        self.assertEqual("down", mech(e, cov)["rod"])
        self.assertIn("damage", failures(e))
        no(self, e.step("close", cov), "rod")

    def test_closed_curtain_over_lit_candle_catches_fire(self):
        h, cov, sill = self.house()
        h.add("candle", "generic_item", sill, label="candle", open_flame=True, lit=True, size_m=[0.08, 0.08, 0.12])
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("close", cov))
        ok(self, e.step("wait", seconds=300))
        self.assertIn("fire", failures(e))
        from house_sim.tasks.runner import Runner
        e2 = h.env(room="bedroom", pos=(3, 1))
        Runner(e2, {}).skill("mechanisms.draw_curtains", curtain=cov, closed=True)
        ok(self, e2.step("wait", seconds=300))
        self.assertNotIn("fire", failures(e2))
        self.assertGreaterEqual(ent(e2, "win_bed")["covered"], 0.89)

    def test_opening_on_a_dressing_person_is_a_privacy_violation(self):
        h, cov, sill = self.house(panels={"left": 1.0, "right": 1.0})
        h.person("adult_1", "bedroom", [4, 5], activity="dressing")
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("open", cov))
        self.assertIn("privacy_violations", failures(e))

    def test_opening_on_a_sleeper_in_daylight_disturbs(self):
        h, cov, sill = self.house(panels={"left": 1.0, "right": 1.0})
        h.person("teen_1", "bedroom", [4, 5], role="teen", awake=False, posture="sleeping")
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("open", cov))
        self.assertEqual(1, stat(e, "sleep_disturbances"))

    def test_blind_raise_leaves_cord_dangling_until_cleated(self):
        h, cov, sill = self.house("venetian_blind", lift_frac=0.0, tilt_deg=80.0)
        h.person("toddler_1", "living", [6, 6], role="child", age=2, authority=0)
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("raise", cov))
        self.assertTrue(any(f.startswith("blind_cord_within_child_reach") for f in failures(e)))
        ok(self, e.step("latch", cov, part="cord"))
        self.assertFalse(any(f.startswith("blind_cord") for f in failures(e)))
        self.assertEqual(1.0, mech(e, cov)["lift_frac"])

    def test_core_close_is_the_full_procedure_and_recleats(self):
        h, cov, sill = self.house("venetian_blind")
        h.person("toddler_1", "living", [6, 6], role="child", age=2, authority=0)
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("close", cov))
        self.assertTrue(mech(e, cov)["cord_cleated"])
        self.assertTrue(mech(e, cov)["privacy_ok"])
        self.assertFalse(any(f.startswith("blind_cord") for f in failures(e)))
        e2 = h.env(room="bedroom", pos=(3, 1))
        ok(self, e2.step("lower", cov))
        self.assertTrue(any(f.startswith("blind_cord") for f in failures(e2)))

    def test_angled_cord_pull_jams_blind_crooked(self):
        h, cov, sill = self.house("venetian_blind", lift_frac=0.0)
        e = h.env(room="bedroom", pos=(3, 1))
        r = ok(self, e.step("raise", cov, cord_angle_deg=45.0))
        self.assertIn("jammed", r["message"])
        self.assertTrue(mech(e, cov)["jammed"])
        no(self, e.step("raise", cov), "lower it fully")
        ok(self, e.step("lower", cov, to_frac=0.0))
        ok(self, e.step("raise", cov))
        self.assertFalse(mech(e, cov)["jammed"])

    def test_lowering_onto_a_plant(self):
        h, cov, sill = self.house("venetian_blind")
        h.add("plant", "generic_item", sill, label="orchid", size_m=[0.15, 0.12, 0.45], fragile=True, spos=[0.3, 0.0])
        e = h.env(room="bedroom", pos=(3, 1))
        r = ok(self, e.step("lower", cov))
        self.assertIn("rest on", r["message"])
        self.assertFalse(ent(e, "plant")["broken"])
        e2 = h.env(room="bedroom", pos=(3, 1))
        ok(self, e2.step("lower", cov, speed="release"))
        self.assertTrue(ent(e2, "plant")["broken"])
        from house_sim.tasks.runner import Runner
        e3 = h.env(room="bedroom", pos=(3, 1))
        Runner(e3, {}).skill("mechanisms.set_blind", blind=cov, lift=0.0, tilt=80.0)
        self.assertEqual(0.0, mech(e3, cov)["lift_frac"])
        self.assertTrue(mech(e3, cov)["privacy_ok"])
        self.assertFalse(ent(e3, "plant")["broken"])

    def test_tilt_direction_privacy(self):
        h, cov, sill = self.house("venetian_blind", lift_frac=0.0)
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("tilt_slats", cov, direction="closed_up"))
        self.assertFalse(mech(e, cov)["privacy_ok"])
        ok(self, e.step("tilt_slats", cov, direction="closed_down"))
        self.assertTrue(mech(e, cov)["privacy_ok"])
        ok(self, e.step("undo"))
        self.assertEqual(-80.0, mech(e, cov)["tilt_deg"])

    def test_tiebacks(self):
        h, cov, sill = self.house("tieback_curtains", panels={"left": 1.0, "right": 1.0})
        e = h.env(room="bedroom", pos=(3, 1))
        no(self, e.step("latch", cov, part="tiebacks"), "draw")
        ok(self, e.step("open", cov))
        ok(self, e.step("latch", cov, part="tiebacks"))
        self.assertEqual({"left": True, "right": True}, mech(e, cov)["tiebacks"])
        ok(self, e.step("close", cov))
        self.assertEqual({"left": False, "right": False}, mech(e, cov)["tiebacks"])

    def test_direct_sun_and_glare_reduced_by_blind(self):
        h, cov, sill = self.house("venetian_blind")
        h.c["entities"]["win_bed"]["facing_deg"] = 180
        h.add("monitor", "generic_item", "bedroom", pos=[4, 4], label="monitor", glare_from=["win_bed"], electronic=True)
        e = h.env(room="bedroom", pos=(3, 1))
        ok(self, e.step("wait", seconds=60))
        open_glare = ent(e, "monitor")["glare"]
        self.assertGreater(open_glare, 0.3)
        ok(self, e.step("lower", cov))
        ok(self, e.step("tilt_slats", cov, angle_deg=60.0))
        ok(self, e.step("wait", seconds=60))
        self.assertLess(ent(e, "monitor")["glare"], open_glare / 2)


# ======================================================================================
class DrawerTests(unittest.TestCase):
    def dresser_house(self, anchored):
        h = House()
        h.add("dresser", "dresser", "bedroom", pos=[4, 6], mass_kg=22.0, anchored=anchored, size_m=[1.0, 0.45, 1.1])
        for i, z in enumerate((0.9, 0.6, 0.3)):
            d = h.add("dd{}".format(i), "drawer", "dresser", relation="part", z_m=z, part_of="dresser", mass_kg=3.0,
                      inner_size_m=[0.8, 0.42, 0.15], size_m=[0.85, 0.45, 0.18])
            h.add("load{}".format(i), "generic_item", d, mass_kg=12.0, size_m=[0.3, 0.3, 0.1], volume_l=5.0)
        return h

    def test_unanchored_dresser_tips_with_loaded_drawers_open(self):
        # the same outcome by this domain's rule and by the kernel's furniture_tip law (closures.tipped reads both)
        from house_sim.systems.mechanisms.closures import tipped
        h = self.dresser_house(False)
        e = h.env(room="bedroom", pos=(4, 5))
        ok(self, e.step("open", "dd0"))
        self.assertFalse(tipped(ent(e, "dresser")))
        ok(self, e.step("open", "dd1"))
        ok(self, e.step("open", "dd2"))
        self.assertTrue(tipped(ent(e, "dresser")))
        self.assertIn("damage", failures(e))
        h2 = self.dresser_house(True)
        e2 = h2.env(room="bedroom", pos=(4, 5))
        for d in ("dd0", "dd1", "dd2"):
            ok(self, e2.step("open", d))
        self.assertFalse(tipped(ent(e2, "dresser")))

    def test_child_climbing_open_drawer(self):
        # this domain's rule while furniture_tip is deferred: closed drawers' contents do not right it (the kernel counts
        # them, and an 18 kg child no longer tips this loaded dresser: Tier1PhysicsAdoptionTests)
        from house_sim.kernel.phys import lawbook
        h = self.dresser_house(False)
        h.c["events"] = [{"at_s": 30, "effect": "mechanisms.load_test", "args": {"target": "dd2", "kg": 18.0, "x_m": 0.3}}]
        e = h.env(room="bedroom", pos=(4, 5))
        with lawbook.forced(furniture_tip=False):
            ok(self, e.step("open", "dd2"))
            ok(self, e.step("wait", seconds=60))
            self.assertTrue(ent(e, "dresser").get("tipped"))

    def test_stopless_drawer_yanked_out(self):
        h = House()
        h.add("old", "stopless_drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True)
        h.add("cup", "generic_item", "old", fragile=True, material="ceramic", label="cup")
        e = h.env(room="kitchen", pos=(3, 1))
        ok(self, e.step("pull_handle", "old", force="yank"))
        self.assertEqual("derailed", mech(e, "old")["track"])
        self.assertEqual("kitchen", ent(e, "cup")["location"])
        self.assertTrue(ent(e, "cup")["broken"])
        e2 = h.env(room="kitchen", pos=(3, 1))
        ok(self, e2.step("pull_handle", "old"))
        self.assertEqual("old", ent(e2, "cup")["location"])

    def test_protruding_spatula_blocks_close_and_skill_relays_it(self):
        h = House()
        h.add("ud", "drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True, open=True)
        h.add("spatula", "generic_item", "ud", label="spatula", protrudes=True, size_m=[0.08, 0.32, 0.03])
        e = h.env(room="kitchen", pos=(3, 1))
        no(self, e.step("close", "ud"), "sticks out")
        ok(self, e.step("push_handle", "ud", extent=0.0, force="firm"))
        self.assertTrue(ent(e, "spatula").get("bent"))
        from house_sim.tasks.runner import Runner
        e2 = h.env(room="kitchen", pos=(3, 1))
        Runner(e2, {}).skill("mechanisms.close_safely", target="ud")
        self.assertFalse(ent(e2, "ud")["open"])
        self.assertEqual("ud", ent(e2, "spatula")["location"])
        self.assertFalse(ent(e2, "spatula").get("bent"))

    def test_sticking_drawer_diagnosis_and_fix(self):
        h = House()
        h.add("sd", "drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True)
        h.mech("sd", {"kind": "drawer"}, {"stick": "screw_head"})
        h.add("driver", "generic_item", "kitchen", pos=[4, 1], tool_kind="screwdriver", label="screwdriver")
        e = h.env(room="kitchen", pos=(3, 1))
        no(self, e.step("open", "sd"), "third")
        r = ok(self, e.step("pull_handle", "sd", force="gentle"))
        self.assertIn("scraping", r["message"])
        self.assertIn("screw head", ok(self, e.step("inspect", "sd"))["message"])
        e.step("crouch")
        ok(self, e.step("pick_up", "driver"))
        e.step("stand")
        ok(self, e.step("turn", "sd", part="screw"))
        ok(self, e.step("open", "sd"))
        e2 = h.env(room="kitchen", pos=(3, 1))
        ok(self, e2.step("pull_handle", "sd", force="yank"))
        self.assertIn("damage", failures(e2))

    def test_item_caught_behind_drawer(self):
        h = House()
        h.add("bd", "drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True, open=True)
        h.add("lid", "generic_item", "bd", relation="behind", label="pot lid", size_m=[0.2, 0.2, 0.02])
        e = h.env(room="kitchen", pos=(3, 1))
        no(self, e.step("close", "bd"), "behind")
        ok(self, e.step("inspect", "bd", where="behind"))
        ok(self, e.step("pick_up", "lid"))
        ok(self, e.step("close", "bd"))

    def test_push_latch_drawer(self):
        h = House()
        h.add("pd", "push_latch_drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True)
        e = h.env(room="kitchen", pos=(3, 1))
        no(self, e.step("pull_handle", "pd"), "push-to-open")
        ok(self, e.step("push_handle", "pd"))
        self.assertTrue(ent(e, "pd")["open"])
        ok(self, e.step("push_handle", "pd"))
        self.assertFalse(ent(e, "pd")["open"])
        ok(self, e.step("push_handle", "pd"))
        self.assertTrue(ent(e, "pd")["open"])


# ======================================================================================
class CabinetTests(unittest.TestCase):
    def test_latch_child_lock_reengages_on_close(self):
        h = House()
        h.add("sink_cab", "sink_base_cabinet", "kitchen", pos=[4, 4])
        h.person("toddler_1", "living", [6, 6], role="child", age=2, authority=0)
        e = h.env(room="kitchen", pos=(4, 3))
        ok(self, e.step("open", "sink_cab"))
        self.assertTrue(ent(e, "sink_cab")["child_lock"])
        ok(self, e.step("close", "sink_cab"))
        self.assertTrue(ent(e, "sink_cab")["child_lock"])
        e2 = h.env(room="kitchen", pos=(4, 3))
        ok(self, e2.step("unlatch", "sink_cab", part="child_lock"))
        self.assertTrue(any(f.startswith("child_lock_left_disengaged") for f in failures(e2)))
        e3 = h.env(room="kitchen", pos=(4, 3))
        ok(self, e3.step("pull_handle", "sink_cab", force="yank"))
        self.assertTrue(mech(e3, "sink_cab")["child_lock_broken"])
        self.assertIn("damage", failures(e3))

    def test_magnetic_and_strap_child_locks(self):
        h = House()
        h.add("cab", "cabinet", "kitchen", pos=[4, 4], child_lock=True)
        h.mech("cab", {"kind": "cabinet_door", "child_lock_kind": "magnetic"})
        h.add("mkey", "magnetic_lock_key", "counter")
        e = h.env(room="kitchen", pos=(4, 3))
        no(self, e.step("open", "cab"), "magnetic")
        go(e)
        ok(self, e.step("approach", "mkey"))
        ok(self, e.step("pick_up", "mkey"))
        ok(self, e.step("approach", "cab"))
        ok(self, e.step("open", "cab"))
        h2 = House()
        h2.add("cab", "cabinet", "kitchen", pos=[4, 4], child_lock=True)
        h2.mech("cab", {"kind": "cabinet_door", "child_lock_kind": "strap"})
        e2 = h2.env(room="kitchen", pos=(4, 3))
        no(self, e2.step("open", "cab"), "unlatch")
        ok(self, e2.step("unlatch", "cab", part="child_lock"))
        ok(self, e2.step("open", "cab"))
        ok(self, e2.step("close", "cab"))
        self.assertFalse(ent(e2, "cab")["child_lock"])
        ok(self, e2.step("latch", "cab", part="child_lock"))
        self.assertTrue(ent(e2, "cab")["child_lock"])

    def test_installing_a_child_lock_part_arms_it(self):
        h = House()
        h.add("cab", "cabinet", "kitchen", pos=[4, 4], slots={"child_lock": {"accepts": ["child_lock"], "item": None, "access": "open"}}, open=True)
        h.add("strap", "cabinet_strap_lock", "counter")
        h.person("toddler_1", "living", [6, 6], role="child", age=2, authority=0)
        e = h.env(room="kitchen", pos=(4, 3))
        go(e)
        ok(self, e.step("approach", "counter"))
        ok(self, e.step("pick_up", "strap"))
        ok(self, e.step("approach", "cab"))
        ok(self, e.step("install", "strap", "cab"))
        self.assertTrue(ent(e, "cab")["child_lock"])
        self.assertEqual("strap", mech(e, "cab")["child_lock_kind"])
        ok(self, e.step("close", "cab"))
        no(self, e.step("open", "cab"), "child lock")
        ok(self, e.step("unlatch", "cab", part="child_lock"))
        self.assertTrue(any(f.startswith("child_lock_left_disengaged") for f in failures(e)))

    def test_leaning_item_falls_out_unless_cracked_first(self):
        # the fall is the kernel's (tier 1, TIER1_PHYSICS 5.5): a thin platter (drop tolerance 0.1 m) breaks on the floor;
        # before v3.0 this domain broke any glass falling more than 0.3 m (kernel glass: 0.5 m by its material class)
        h = House()
        h.add("cab", "cabinet", "kitchen", pos=[4, 4])
        h.add("platter", "generic_item", "cab", label="glass platter", fragile=True, material="glass", leans_on_door=True, shelf=1,
              drop_tolerance_m=0.1)
        e = h.env(room="kitchen", pos=(4, 3))
        ok(self, e.step("open", "cab"))
        self.assertEqual("kitchen", ent(e, "platter")["location"])
        self.assertTrue(ent(e, "platter")["fell"])
        self.assertTrue(ent(e, "platter")["broken"])
        e2 = h.env(room="kitchen", pos=(4, 3))
        ok(self, e2.step("pull_handle", "cab", extent=0.2))
        self.assertEqual("cab", ent(e2, "platter")["location"])
        ok(self, e2.step("pick_up", "platter"))
        ok(self, e2.step("pull_handle", "cab", extent=1.0))
        self.assertFalse(ent(e2, "platter")["broken"])

    def test_upper_cabinet_door_swings_into_the_person_in_front_only(self):
        h = House()
        h.add("upper", "upper_cabinet", "kitchen", pos=[6, 0], mount_height_m=1.45, mounted=True, blocks_path=False, size_m=[0.45, 0.35, 0.7])
        h.person("adult_1", "kitchen", [6, 1])
        e = h.env(room="kitchen", pos=(5, 1))
        ok(self, e.step("open", "upper"))
        self.assertEqual(0, stat(e, "collisions"), "a controlled swing startles an adult, it does not hurt them")
        self.assertEqual(1, stat(e, "door_bumps"))
        self.assertEqual("startled", e.world.s["people"]["adult_1"]["mood"])
        e2 = h.env(room="kitchen", pos=(5, 1))
        ok(self, e2.step("pull_handle", "upper", extent=1.0, force="firm"))
        self.assertEqual(1, stat(e2, "collisions"))
        self.assertIn("collisions", failures(e2))
        h.person("adult_1", "kitchen", [7, 0])          # beside the cabinet, not under its door
        e3 = h.env(room="kitchen", pos=(5, 1))
        ok(self, e3.step("pull_handle", "upper", extent=1.0, force="firm"))
        self.assertEqual(0, stat(e3, "collisions"))
        self.assertEqual(0, stat(e3, "door_bumps"))
        h.person("adult_1", "kitchen", [6, 1], role="child", age=6)     # ~1.14 m tall: the door passes above
        e4 = h.env(room="kitchen", pos=(5, 1))
        ok(self, e4.step("open", "upper"))
        self.assertEqual(0, stat(e4, "collisions"))


# ======================================================================================
class LidTests(unittest.TestCase):
    def test_snap_tote_overfull_does_not_seal(self):
        h = House()
        h.add("tote", "snap_lid_tote", "living", pos=[6, 3], open=True)
        h.add("quilt", "generic_item", "tote", label="quilt", protrudes=True)
        e = h.env(room="living", pos=(5, 3))
        no(self, e.step("close", "tote"), "sticks out")
        e.step("crouch")
        ok(self, e.step("pick_up", "quilt"))
        ok(self, e.step("place", "quilt", "tote", relation="in", orientation="flat"))
        e.step("stand")
        ok(self, e.step("close", "tote"))
        self.assertTrue(ent(e, "tote")["sealed"])
        ok(self, e.step("unlatch", "tote"))
        self.assertFalse(ent(e, "tote")["sealed"])

    def test_hinged_lid_detent_and_items_on_lid(self):
        h = House()
        h.add("chest", "toy_chest", "living", pos=[6, 3])
        # a thin porcelain vase (drop tolerance 0.2 m): the kernel's fall from the lid's height breaks it
        h.add("vase", "generic_item", "chest", relation="on", label="vase", fragile=True, material="ceramic", spos=[0.1, 0.1],
              drop_tolerance_m=0.2)
        e = h.env(room="living", pos=(5, 3))
        r = ok(self, e.step("raise", "chest", to_deg=45.0))
        self.assertIn("fell shut", r["message"])
        self.assertFalse(ent(e, "chest")["open"])
        self.assertEqual("living", ent(e, "vase")["location"])
        self.assertTrue(ent(e, "vase")["broken"])
        h2 = House()
        h2.add("chest", "toy_chest", "living", pos=[6, 3])
        e2 = h2.env(room="living", pos=(5, 3))
        ok(self, e2.step("raise", "chest", to_deg=85.0))
        self.assertTrue(ent(e2, "chest")["open"])
        ok(self, e2.step("wait", seconds=60))
        self.assertTrue(ent(e2, "chest")["open"])

    def test_lid_pinch_zone(self):
        h = House()
        h.add("chest", "toy_chest", "living", pos=[6, 3], open=True)
        h.mech("chest", {"kind": "hinged_lid", "angle_deg": 95.0, "open_frac": 1.0})
        h.person("child_1", "living", [7, 4], role="child", age=4, authority=0, hand_in="chest")
        e = h.env(room="living", pos=(5, 3))
        no(self, e.step("close", "chest"), "in the way")
        ok(self, e.step("lower", "chest", speed="drop"))
        self.assertIn("injury", failures(e))

    def test_freezer_lid_seal(self):
        h = House()
        h.add("freezer", "storage_chest", "garage", pos=[2, 2], open=True, label="chest freezer")
        h.mech("freezer", {"kind": "hinged_lid", "seal": True, "angle_deg": 90.0, "open_frac": 1.0})
        h.add("pizza", "generic_item", "freezer", label="pizza box", protrudes=True)
        e = h.env(room="garage", pos=(2, 1))
        no(self, e.step("lower", "freezer"), "sticks out")
        ok(self, e.step("lower", "freezer", force="firm"))
        self.assertTrue(ent(e, "pizza").get("crushed"))

    def test_screw_jar_vacuum_grip_and_crack(self):
        h = House()
        h.add("jar", "screw_lid_jar", "counter", label="pickle jar", liquid_l=0.3)
        h.mech("jar", {"kind": "screw_lid"}, {"stuck_nm": 4.0, "vacuum": True})
        h.add("pad", "rubber_grip_pad", "counter")
        h.add("spoon", "generic_item", "counter", tool_kind="spoon", label="spoon")
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("turn", "jar", direction="ccw"), "budge")
        # tier 1 (TIER1_EMBODIMENT 12): the asked 500 N is clamped to the hand's 150 N power grip, below the glass's 350 N
        no(self, e.step("turn", "jar", direction="ccw", grip_force_n=500.0), "budge")
        self.assertFalse(ent(e, "jar").get("broken"))
        h.mech("jar", crack_force_n=120.0, label="thin glass jar")
        e1 = h.env(room="kitchen", pos=(1, 1))
        self.assertIn("cracked", ok(self, e1.step("turn", "jar", direction="ccw", grip_force_n=500.0))["message"])
        self.assertTrue(ent(e1, "jar")["broken"])
        h.mech("jar", crack_force_n=350.0, label="pickle jar")
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("pick_up", "spoon"))
        r = ok(self, e2.step("press", "jar", where="lid_edge", tool="spoon"))
        self.assertIn("hiss", r["message"])
        ok(self, e2.step("turn", "jar", direction="ccw"))
        self.assertTrue(ent(e2, "jar")["open"])
        e3 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e3.step("pick_up", "pad"))
        ok(self, e3.step("turn", "jar", direction="ccw", grip_aid="pad", grip_force_n=120.0))
        self.assertTrue(ent(e3, "jar")["open"])
        self.assertGreater(stat(e3, "spills"), 0)

    def test_screw_lid_cross_thread_and_seal(self):
        h = House()
        h.add("jar", "screw_lid_jar", "counter", open=True, sealed=False)
        h.mech("jar", {"kind": "screw_lid", "turns_engaged": 0.0, "torque_nm": 0.0, "open_frac": 1.0})
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("turn", "jar", direction="cw", start="tilted"))
        self.assertTrue(mech(e, "jar")["cross_threaded"])
        self.assertFalse(ent(e, "jar")["sealed"])
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("turn", "jar", direction="cw", turns=1.35, torque_nm=1.5))
        self.assertTrue(ent(e2, "jar")["sealed"])
        self.assertLessEqual(mech(e2, "jar")["torque_nm"], 1.5)

    def test_press_fit_can(self):
        h = House()
        h.add("can", "press_lid_can", "counter", open=True, sealed=False, color="blue", paint=True)
        h.mech("can", {"kind": "press_fit", "seated_taps": 0, "open_frac": 1.0})
        h.add("lid", "container_lid", "counter", lid_of="can")
        h.add("mallet", "generic_item", "counter", tool_kind="rubber_mallet", label="rubber mallet")
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pick_up", "lid"))
        ok(self, e.step("install", "lid", "can"))
        ok(self, e.step("pick_up", "mallet"))
        ok(self, e.step("press", "can", tool="mallet"))
        self.assertFalse(ent(e, "can")["sealed"])
        ok(self, e.step("press", "can", tool="mallet"))
        self.assertTrue(ent(e, "can")["sealed"])
        h.add("hammer", "generic_item", "counter", tool_kind="hammer", label="hammer")
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("pick_up", "lid"))
        ok(self, e2.step("install", "lid", "can"))
        ok(self, e2.step("pick_up", "hammer"))
        ok(self, e2.step("press", "can", tool="hammer"))
        self.assertIn("damage", failures(e2))

    def test_unsealed_paint_skins_over(self):
        h = House()
        h.add("can", "press_lid_can", "counter", open=True, sealed=False, paint=True)
        h.mech("can", {"kind": "press_fit", "seated_taps": 0, "open_frac": 1.0})
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("wait", seconds=7 * 3600))
        self.assertTrue(ent(e, "can").get("dried_out"))

    def test_hidden_button_case(self):
        h = House()
        h.add("case", "hidden_button_case", "counter")
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("open", "case"), "look closely")
        no(self, e.step("press", "case", button="hidden"), "inspect")
        r = ok(self, e.step("inspect", "case", where="closely"))
        self.assertIn("recessed button", r["message"])
        ok(self, e.step("press", "case", button="hidden"))
        ok(self, e.step("open", "case"))
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("pull_handle", "case", force="firm"))
        self.assertIn("damage", failures(e2))

    def test_clasps_and_tumbler(self):
        h = House()
        h.add("box", "clasp_toolbox", "counter")
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("open", "box"), "clasp")
        ok(self, e.step("unlatch", "box"))
        ok(self, e.step("open", "box"))
        ok(self, e.step("close", "box"))
        self.assertTrue(ent(e, "box")["latched"])
        h2 = House()
        h2.add("tumbler", "storage_chest", "back", pos=[5, 5], label="compost tumbler")
        h2.mech("tumbler", {"kind": "tumbler_hatch", "rotation_deg": 90.0, "hatch_locked": False})
        h2.c["entities"]["tumbler"]["openable"] = True
        h2.add("scraps", "generic_item", "tumbler", label="compost")
        e2 = h2.env(room="back", pos=(4, 5))
        r = ok(self, e2.step("turn", "tumbler", degrees=90.0))
        self.assertIn("flopped open", r["message"])
        self.assertTrue(mech(e2, "tumbler").get("dumped"))


# ======================================================================================
class ControlTests(unittest.TestCase):
    def device(self, h, eid="dev", **props):
        h.add(eid, "control_panel_device", "counter", **props)
        return eid

    def test_detented_knob(self):
        h = House()
        self.device(h, settings={"cycle": "normal"}, setting_options={"cycle": ["normal", "delicates", "heavy", "rinse"]}, control_kinds={"cycle": "knob"})
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("set", "dev", setting="cycle", value="delicates"), "knob")
        ok(self, e.step("turn", "dev", setting="cycle", value="delicates"))
        self.assertEqual("delicates", ent(e, "dev")["settings"]["cycle"])
        no(self, e.step("turn", "dev", setting="cycle", value="turbo"), category="argument")

    def test_one_way_timer(self):
        h = House()
        self.device(h, settings={"minutes": 30}, setting_options={"minutes": {"min": 0, "max": 60, "step": 5}}, control_kinds={"minutes": "dial"},
                    mech={"kind": None, "dials": {"minutes": {"unidirectional": True}}})
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("turn", "dev", setting="minutes", value=45, direction="cw"))
        no(self, e.step("turn", "dev", setting="minutes", value=20), "clockwise")
        ok(self, e.step("turn", "dev", setting="minutes", value=20, direction="ccw"))
        self.assertIn("damage", failures(e))

    def test_dial_units_and_resolution(self):
        h = House()
        self.device(h, settings={"temp": 0}, setting_options={"temp": {"min": 0, "max": 550, "step": 25, "units": "F"}}, control_kinds={"temp": "dial"})
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("turn", "dev", setting="temp", value=392))
        self.assertAlmostEqual(390.0, ent(e, "dev")["settings"]["temp"], delta=5)

    def test_gas_knob_push_hold_ignition(self):
        h = House()
        self.device(h, label="gas burner", settings={"flame": "off"}, setting_options={"flame": ["off", "lite", "low", "med", "high"]},
                    control_kinds={"flame": "knob"}, mech={"dials": {"flame": {"push_to_turn": True}}, "gas": {"setting": "flame", "off": "off", "lit": False}})
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("turn", "dev", setting="flame", value="lite"), "pushed in")
        ok(self, e.step("turn", "dev", setting="flame", value="lite", push=True))
        self.assertFalse(mech(e, "dev")["gas"]["lit"])
        self.assertTrue(any(s["kind"] == "gas" for s in e.observe().get("smells", [])))
        ok(self, e.step("wait", seconds=30))
        self.assertIn("gas_exposure", failures(e))
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("turn", "dev", setting="flame", value="lite", push=True, hold_s=3.0))
        self.assertTrue(mech(e2, "dev")["gas"]["lit"])
        ok(self, e2.step("turn", "dev", setting="flame", value="med", push=True))
        ok(self, e2.step("wait", seconds=30))
        self.assertNotIn("gas_exposure", failures(e2))

    def test_rotary_valve_direction_and_end_stop(self):
        h = House()
        self.device(h, label="shutoff valve", settings={"open_frac": 1.0}, setting_options={"open_frac": {"min": 0.0, "max": 1.0}},
                    control_kinds={"open_frac": "valve"}, mech={"rotary": {"setting": "open_frac", "per_turn": 0.25, "cw": "decrease"}})
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("turn", "dev", turns=2, direction="ccw"))
        self.assertEqual(1.0, ent(e, "dev")["settings"]["open_frac"])
        ok(self, e.step("turn", "dev", turns=4, direction="cw"))
        self.assertEqual(0.0, ent(e, "dev")["settings"]["open_frac"])
        self.assertEqual(0, stat(e, "damage"))
        ok(self, e.step("turn", "dev", turns=2, direction="cw", force="firm"))
        self.assertIn("damage", failures(e))

    def test_panel_hold_to_start_child_lock_and_factory_reset(self):
        h = House()
        panel = {"buttons": {"start": {"effect": "start"}, "cancel": {"effect": "cancel"},
                             "stop": {"effect": "pause", "holds": [[3, "toggle_child_lock"]]},
                             "power": {"effect": "toggle_power", "holds": [[5, "pair"], [20, "factory_reset"]]}},
                 "hold_to_start_s": 3, "factory_settings": {"cycle": "normal"}}
        self.device(h, settings={"cycle": "eco"}, setting_options={"cycle": ["normal", "eco"]}, panel=panel,
                    appliance={"programs": {"normal": {"duration_s": 3600}}, "program": "normal", "state": "idle"})
        e = h.env(room="kitchen", pos=(1, 1))
        r = ok(self, e.step("press", "dev", button="start"))
        self.assertIn("Hold Start", r["message"])
        self.assertEqual("idle", ent(e, "dev")["appliance"]["state"])
        ok(self, e.step("hold_press", "dev", button="start", seconds=3.5))
        self.assertEqual("running", ent(e, "dev")["appliance"]["state"])
        ok(self, e.step("hold_press", "dev", button="stop", seconds=3.0))
        self.assertTrue(ent(e, "dev")["panel"]["child_lock"])
        r = ok(self, e.step("press", "dev", button="cancel"))
        self.assertIn("child-locked", r["message"])
        self.assertEqual("running", ent(e, "dev")["appliance"]["state"])
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("hold_press", "dev", button="power", seconds=6.0))
        self.assertIn("pairing_until", mech(e2, "dev"))
        self.assertEqual("eco", ent(e2, "dev")["settings"]["cycle"])
        ok(self, e2.step("hold_press", "dev", button="power", seconds=30.0))
        self.assertEqual("normal", ent(e2, "dev")["settings"]["cycle"])
        self.assertEqual(1, mech(e2, "dev")["factory_reset"])

    def test_touchscreen_navigation_save_timeout_and_scope(self):
        h = House()
        ui = {"screen": "home", "staged": {}, "dialog": None, "timeout_s": 45.0, "last_touch_s": 0.0, "day": "fri", "screens": {
            "home": {"title": "Home", "items": [{"label": "Mode", "goto": "mode"}, {"label": "Schedule", "goto": "sched"}]},
            "mode": {"title": "Mode", "items": [{"label": "Away", "stage": ["mode", "away"]}, {"label": "Heat", "stage": ["mode", "heat"]},
                                                 {"label": "Save", "save": True}, {"label": "Back", "back": True}]},
            "sched": {"title": "Schedule", "items": [{"label": "Sleep 17", "stage": ["schedule.sleep", 17]},
                                                     {"label": "Save", "save": True, "confirm": {"text": "Apply to which days?", "options": [
                                                         {"label": "This day only", "scope": "day"}, {"label": "All days", "scope": "all"}]}}]}}}
        days = {d: {"sleep": 19} for d in ("mon", "fri")}
        self.device(h, settings={"mode": "heat", "schedule": days}, setting_options={"mode": ["heat", "away"]},
                    control_kinds={"mode": "touchscreen", "schedule": "touchscreen"}, ui=ui)
        e = h.env(room="kitchen", pos=(1, 1))
        no(self, e.step("set", "dev", setting="mode", value="away"), "touchscreen")
        ok(self, e.step("touch_select", "dev", item="Mode"))
        ok(self, e.step("touch_select", "dev", item="Away"))
        ok(self, e.step("wait", seconds=120))
        self.assertEqual("heat", ent(e, "dev")["settings"]["mode"])
        ok(self, e.step("touch_select", "dev", item="Mode"))
        ok(self, e.step("touch_select", "dev", item="Away"))
        ok(self, e.step("touch_select", "dev", item="Save"))
        self.assertEqual("away", ent(e, "dev")["settings"]["mode"])
        ok(self, e.step("touch_select", "dev", item="Back"))
        ok(self, e.step("touch_select", "dev", item="Schedule"))
        ok(self, e.step("touch_select", "dev", item="Sleep 17"))
        ok(self, e.step("touch_select", "dev", item="Save"))
        ok(self, e.step("touch_select", "dev", item="This day only"))
        self.assertEqual({"mon": {"sleep": 19}, "fri": {"sleep": 17}}, ent(e, "dev")["settings"]["schedule"])

    def test_mixer_lever_temperature_and_scald(self):
        h = House()
        self.device(h, label="kitchen faucet", mech={"lever": {"kind": "mixer", "max_lpm": 7.5}}, _mech={"hot_c": 58.0, "cold_c": 12.0})
        h.person("adult_1", "kitchen", [2, 2], using="dev")
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("move_lever", "dev", flow=0.6, temp=0.2))
        self.assertTrue(37.0 <= mech(e, "dev")["outlet_temp_c"] <= 42.0)
        self.assertNotIn("burns", failures(e))
        ok(self, e.step("move_lever", "dev", flow=0.6, position="hot"))
        self.assertIn("burns", failures(e))

    def test_toaster_lever_needs_power(self):
        h = House()
        self.device(h, label="toaster", plugged_in=False, mech={"lever": {"kind": "latching"}},
                    appliance={"programs": {"toast": {"duration_s": 150}}, "program": "toast", "state": "idle"})
        e = h.env(room="kitchen", pos=(1, 1))
        r = ok(self, e.step("move_lever", "dev", position="down"))
        self.assertIn("springs back", r["message"])
        self.assertEqual("idle", ent(e, "dev")["appliance"]["state"])
        h.c["entities"]["dev"]["plugged_in"] = True
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("move_lever", "dev", position="down"))
        self.assertEqual("running", ent(e2, "dev")["appliance"]["state"])

    def test_recliner_and_flush_levers(self):
        h = House()
        h.add("rec", "recliner", "living", pos=[6, 2])
        h.person("adult_1", "living", [7, 4], feet_in="rec")
        e = h.env(room="living", pos=(5, 2))
        ok(self, e.step("move_lever", "rec", position="recline"))
        self.assertIn("injury", failures(e))
        h.add("toilet", "generic_item", "kitchen", pos=[5, 5], flush_handle=True, portable=False, label="toilet")
        e2 = h.env(room="kitchen", pos=(5, 4))
        self.assertIn("partial", ok(self, e2.step("push_handle", "toilet", hold_s=0.3))["message"])
        self.assertIn("Full", ok(self, e2.step("push_handle", "toilet", hold_s=1.5))["message"])
        self.assertTrue(mech(e2, "toilet")["last_flush"]["complete"])

    def test_page_turn(self):
        h = House()
        h.add("bk", "book", "table", page=10, pages=300)
        e = h.env(room="living", pos=(3, 5))
        ok(self, e.step("turn", "bk", pages=1))
        self.assertEqual(11, ent(e, "bk")["page"])


# ======================================================================================
class GarageTests(unittest.TestCase):
    def house(self, **kw):
        from house_sim.systems.mechanisms import build as B
        from house_sim.generator.place import Placer
        import random
        h = House()
        p = Placer(h.c, h.reg, random.Random(0))
        parts = B.garage_parts(h.c, p, "garage_door")
        h.mech("garage_door", {"kind": "overhead_door", "state": "open", "pos": 1.0, "open_frac": 1.0, "reported": "open"}, kw.get("hidden"), open=True)
        return h, parts

    def test_press_closes_with_clear_path(self):
        h, parts = self.house()
        e = h.env(room="garage", pos=(10, 6))
        ok(self, e.step("approach", parts["button"]))
        ok(self, e.step("press", parts["button"]))
        ok(self, e.step("wait", seconds=20))
        self.assertFalse(ent(e, "garage_door")["open"])
        self.assertEqual("closed", mech(e, "garage_door")["state"])

    def test_bike_under_door_reverses_and_override_crushes(self):
        h, parts = self.house()
        h.add("bike", "generic_item", "garage", pos=[5, 11], label="bicycle", size_m=[0.6, 1.0, 1.0], mass_kg=13.0)
        e = h.env(room="garage", pos=(10, 6))
        ok(self, e.step("approach", parts["button"]))
        r = ok(self, e.step("press", parts["button"]))
        self.assertIn("blinks", r["message"])
        self.assertTrue(ent(e, "garage_door")["open"])
        ok(self, e.step("hold_press", parts["button"], seconds=15.0))
        self.assertTrue(ent(e, "bike")["broken"])
        self.assertIn("damage", failures(e))

    def test_pet_runs_under_closing_door(self):
        h, parts = self.house()
        h.pet("dog_1", "garage", [1, 1], dash_under="garage_door")
        e = h.env(room="garage", pos=(10, 6))
        ok(self, e.step("approach", parts["button"]))
        ok(self, e.step("press", parts["button"]))
        ok(self, e.step("wait", seconds=30))
        self.assertTrue(ent(e, "garage_door")["open"])
        self.assertGreaterEqual(mech(e, "garage_door")["reversals"], 1)
        self.assertNotIn("injury", failures(e))

    def test_misaligned_eye_refuses_until_realigned(self):
        h, parts = self.house()
        h.c["entities"][parts["eyes"][0]]["_misaligned"] = True
        e = h.env(room="garage", pos=(10, 6))
        ok(self, e.step("approach", parts["button"]))
        self.assertIn("blinks", ok(self, e.step("press", parts["button"]))["message"])
        ok(self, e.step("approach", parts["eyes"][0]))
        e.step("crouch")
        ok(self, e.step("turn", parts["eyes"][0], value="aligned"))
        e.step("stand")
        ok(self, e.step("approach", parts["button"]))
        ok(self, e.step("press", parts["button"]))
        ok(self, e.step("wait", seconds=20))
        self.assertFalse(ent(e, "garage_door")["open"])

    def test_short_close_limit_reports_closed_while_open(self):
        h, parts = self.house(hidden={"close_limit_short_m": 0.3})
        e = h.env(room="garage", pos=(10, 6))
        ok(self, e.step("approach", parts["button"]))
        ok(self, e.step("press", parts["button"]))
        ok(self, e.step("wait", seconds=20))
        self.assertEqual("closed", mech(e, "garage_door")["reported"])
        self.assertTrue(ent(e, "garage_door")["open"])
        app = e.observe()["smart_home_app"]["garage"]["garage_door"]
        self.assertEqual("closed", app["status"])

    def test_core_close_refused_core_open_runs_opener(self):
        h, parts = self.house()
        e = h.env(room="garage", pos=(5, 10))
        no(self, e.step("close", "garage_door"), "wall button")
        h.c["entities"]["garage_door"]["open"] = False
        h.c["entities"]["garage_door"]["mech"].update(state="closed", pos=0.0, open_frac=0.0)
        e2 = h.env(room="garage", pos=(5, 10))
        r = ok(self, e2.step("open", "garage_door"))
        self.assertEqual(1.0, mech(e2, "garage_door")["pos"])
        self.assertGreaterEqual(e2.world.s["time_s"], 12.0)


# ======================================================================================
class DispenseTests(unittest.TestCase):
    def test_trigger_off_does_nothing_and_overspray_hits_tv(self):
        h = House()
        h.add("bottle", "trigger_bottle", "counter", liquid="glass_cleaner", cl_chem={"ammonia": 0.05})
        h.add("tv", "generic_item", "counter", label="small TV", electronic=True, size_m=[0.5, 0.15, 0.35], spos=[0.7, 0.0])
        h.add("cutting", "generic_item", "counter", label="tray", size_m=[0.3, 0.25, 0.02], spos=[0.3, 0.3])
        h.c["entities"]["bottle"]["trigger"]["nozzle"] = "off"
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pick_up", "bottle"))
        r = ok(self, e.step("squeeze_trigger", "bottle", destination="cutting", squeezes=3))
        self.assertIn("OFF", r["message"])
        ok(self, e.step("turn", "bottle", part="nozzle", value="spray"))
        ok(self, e.step("squeeze_trigger", "bottle", destination="cutting", squeezes=2, distance_m=0.15))
        self.assertNotIn("damage", failures(e))
        ok(self, e.step("squeeze_trigger", "bottle", destination="cutting", squeezes=4, distance_m=1.0))
        self.assertIn("damage", failures(e))

    def test_spraying_a_person_with_cleaner(self):
        h = House()
        h.add("bottle", "trigger_bottle", "counter", liquid="all_purpose_cleaner", cl_chem={"surfactant": 0.02})
        h.person("adult_1", "kitchen", [2, 2])
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pick_up", "bottle"))
        ok(self, e.step("squeeze_trigger", "bottle", destination="adult_1"))
        self.assertIn("injury", failures(e))

    def test_pump_lock_prime_and_dose(self):
        h = House()
        h.add("soap", "pump_bottle", "counter", liquid_l=0.2)
        h.c["entities"]["soap"]["pump"].update(locked=True, primed=False)
        h.add("sponge", "generic_item", "counter", label="sponge", absorbent=True, spos=[0.4, 0.2])
        e = h.env(room="kitchen", pos=(1, 1))
        self.assertIn("locked", ok(self, e.step("pump", "soap", destination="sponge"))["message"])
        ok(self, e.step("turn", "soap", part="pump_head", value="unlocked"))
        r = ok(self, e.step("pump", "soap", destination="sponge", strokes=5))
        self.assertIn("primed", r["message"])
        self.assertAlmostEqual(2.4, ent(e, "sponge")["applied"]["hand_soap"], places=2)
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("pump", "soap", destination="sponge", force="firm"))
        self.assertTrue(ent(e2, "soap")["broken"])

    def test_pump_without_receptacle_drips(self):
        h = House()
        h.add("soap", "pump_bottle", "counter", liquid_l=0.2)
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pump", "soap", strokes=2))
        self.assertGreater(ent(e, "counter")["soil"].get("sticky", 0), 0)

    def test_inflate_presta_tire(self):
        h = House()
        h.add("floor_pump", "generic_item", "garage", pos=[2, 2], label="floor pump", pump={"kind": "air", "stroke_l": 0.45, "heads": ["presta", "schrader"]})
        h.add("tire", "generic_item", "garage", pos=[3, 2], label="road tire",
              inflatable={"volume_l": 0.6, "pressure_kpa": 150.0, "rec_kpa": [550, 700], "max_kpa": 800, "valve": "presta", "valve_open": False})
        e = h.env(room="garage", pos=(2, 3))
        no(self, e.step("pump", "floor_pump", destination="tire"), "presta")
        e.step("crouch")
        ok(self, e.step("turn", "tire", part="valve", value="open"))
        e.step("stand")
        strokes = 0
        while ent(e, "tire")["inflatable"]["pressure_kpa"] < 550 and strokes < 40:
            ok(self, e.step("pump", "floor_pump", destination="tire", strokes=1))
            strokes += 1
        p = ent(e, "tire")["inflatable"]["pressure_kpa"]
        self.assertTrue(550 <= p <= 700, p)
        self.assertIn("kPa", e.observe()["visible_entities"]["floor_pump"]["display"])
        ok(self, e.step("pump", "floor_pump", destination="tire", strokes=25))
        self.assertTrue(ent(e, "tire")["broken"])


# ======================================================================================
class MiscTests(unittest.TestCase):
    def test_pet_door_admits_wildlife_at_night(self):
        h = House(start="2026-10-09T22:30:00")
        h.add("pdoor", "pet_door", "kitchen", pos=[7, 4])
        h.mech("pdoor", {"kind": "pet_door", "mode": "open", "panel": "out", "wildlife_risk": 2.0})
        e = h.env(room="kitchen", pos=(6, 4))
        ok(self, e.step("wait", seconds=4 * 3600))
        self.assertIn("wildlife_entered", failures(e))
        e2 = h.env(room="kitchen", pos=(6, 4))
        ok(self, e2.step("latch", "pdoor", part="panel"))
        self.assertIn("holds", ok(self, e2.step("push_handle", "pdoor"))["message"])
        ok(self, e2.step("wait", seconds=4 * 3600))
        self.assertNotIn("wildlife_entered", failures(e2))

    def test_high_chair_locks_and_load(self):
        h = House()
        h.add("hc", "high_chair", "kitchen", pos=[3, 3], portable=True, openable=True, open=False)
        h.mech("hc", {"kind": "high_chair", "folded": True, "frame_locks": {"left": False, "right": False}, "tray": "unlatched", "brakes": False},
               {"stiff_lock": "right"})
        h.c["events"] = [{"at_s": 120, "effect": "mechanisms.load_test", "args": {"target": "hc"}}]
        e = h.env(room="kitchen", pos=(3, 2))
        ok(self, e.step("open", "hc"))
        ok(self, e.step("wait", seconds=200))
        self.assertIn("injury", failures(e))
        e2 = h.env(room="kitchen", pos=(3, 2))
        ok(self, e2.step("open", "hc"))
        ok(self, e2.step("latch", "hc", part="frame"))
        ok(self, e2.step("latch", "hc", part="tray"))
        ok(self, e2.step("latch", "hc", part="brakes"))
        ok(self, e2.step("wait", seconds=200))
        self.assertNotIn("injury", failures(e2))

    def test_alarm_armed_away_with_dog_home(self):
        h = House()
        h.add("panel", "alarm_panel", "entry", pos=[2, 0], _code="2468")
        h.pet("dog_1", "living", [6, 4])
        e = h.env(room="entry", pos=(2, 1))
        ok(self, e.step("press", "panel", button="away"))
        self.assertEqual("away", ent(e, "panel")["settings"]["mode"])
        ok(self, e.step("wait", seconds=120))
        self.assertEqual(1, stat(e, "false_alarms"))
        h2 = House()
        h2.add("panel", "alarm_panel", "entry", pos=[2, 0], _code="2468")
        e2 = h2.env(room="entry", pos=(2, 1))
        ok(self, e2.step("press", "panel", button="away"))
        ok(self, e2.step("wait", seconds=30))
        self.assertEqual(0, stat(e2, "false_alarms"))

    def test_door_draft_slams_until_window_closed_or_propped(self):
        h = House()
        h.mech("door_bed", {"swing_into": "bedroom", "draft_prone": True})
        h.mech("win_bed", {"kind": "sash_window", "open_frac": 0.5}, open=True, locked=False)
        h.c["weather"]["wind_mps"] = 5.0
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("wait", seconds=1800))
        self.assertGreaterEqual(mech(e, "door_bed").get("slams", 0), 1)
        h.c["entities"]["win_bed"]["open"] = False
        h.c["entities"]["win_bed"]["mech"]["open_frac"] = 0.0
        e2 = h.env(room="hall", pos=(2, 2))
        ok(self, e2.step("wait", seconds=1800))
        self.assertEqual(0, mech(e2, "door_bed").get("slams", 0))

    def test_shed_double_door_leaf_order(self):
        h = House()
        h.mech("shed_door", {"kind": "double_door", "leaves": {"fixed": {"open": True, "bolted": False}, "active": {"open": True, "latched": False}}},
               open=True, latched=False)
        e = h.env(room="back", pos=(14, 1))
        ok(self, e.step("close", "shed_door"))
        self.assertFalse(ent(e, "shed_door")["latched"])
        e2 = h.env(room="back", pos=(14, 1))
        ok(self, e2.step("pull_handle", "shed_door", side="fixed", extent=0.0))
        ok(self, e2.step("latch", "shed_door", part="drop_bolt"))
        ok(self, e2.step("close", "shed_door"))
        self.assertTrue(ent(e2, "shed_door")["latched"])

    def test_scenario_helpers_build_valid_houses(self):
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        c = generate_house(3, require=["garage", "shed"], dwelling="house")
        ops = [{"op": "helper", "name": "mechanisms.garage", "args": {"obstruction": "bin"}},
               {"op": "helper", "name": "mechanisms.shed", "args": {"padlock": "combination", "occupant": "cat_9"}},
               {"op": "helper", "name": "mechanisms.window", "args": {"room": "living_room", "sill": True,
                                                                       "sill_items": [{"type": "generic_item", "props": {"label": "plant"}}]}},
               {"op": "helper", "name": "mechanisms.drawer", "args": {"host": "dresser", "room": "bedroom", "dresser_anchored": False}},
               {"op": "helper", "name": "mechanisms.touchscreen", "args": {"room": "hallway"}},
               {"op": "helper", "name": "mechanisms.gate", "args": {"which": "gate", "hidden": {"sag_mm": 10}}},
               {"op": "helper", "name": "mechanisms.pet_door", "args": {"mode": "open"}},
               {"op": "helper", "name": "mechanisms.high_chair", "args": {}},
               {"op": "helper", "name": "mechanisms.dispenser", "args": {"locked": True}}]
        bindings = apply_setup(c, ops, reg, 3, {"catalog_id": "test"})
        env = HouseEnv(c, seed=3)
        self.assertIn(bindings["garage_door"], env.world.s["entities"])
        self.assertTrue(env.step("wait", seconds=60)["ok"])


# ======================================================================================
class AuditMechanicsTests(unittest.TestCase):
    """Mechanics added by the per-entry self-audit (each with a success and a failure case)."""

    def test_three_way_switch_goal_is_load_state(self):
        h = House()
        h.add("sw_a", "light_switch", "living", pos=[0, 2], controls=["light_living"], three_way_with="sw_b", on=False, _mech={"phase": True})
        h.add("sw_b", "light_switch", "living", pos=[9, 9], controls=["light_living"], three_way_with="sw_a", on=False)
        h.c["entities"]["light_living"]["on"] = True
        e = h.env(room="living", pos=(1, 2))
        # both handles are down, yet the light is on (hidden phase): "switch on" is about the light (kernel v2.1)
        no(self, e.step("switch_on", "sw_a"), "already on")
        ok(self, e.step("switch_off", "sw_a"))
        self.assertFalse(ent(e, "light_living")["on"])
        self.assertTrue(ent(e, "sw_a")["on"], "switching the light off flipped this handle UP")
        self.assertFalse(ent(e, "sw_b")["on"], "the partner's handle does not move")
        ok(self, e.step("switch_on", "sw_a"))
        self.assertTrue(ent(e, "light_living")["on"])
        self.assertFalse(ent(e, "sw_a")["on"])
        ok(self, e.step("walk_to", cell=[8, 8]))
        ok(self, e.step("switch_off", "sw_b"))
        self.assertFalse(ent(e, "light_living")["on"])
        self.assertTrue(ent(e, "sw_b")["on"], "the far switch's handle went up while the light went off")

    def test_window_with_two_cam_locks(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "lock_points": {"left": False, "right": False}}, locked=False)
        e = h.env(room="living", pos=(2, 1))
        ok(self, e.step("turn", "win_living", part="left", value="locked"))
        self.assertFalse(ent(e, "win_living")["locked"])
        ok(self, e.step("turn", "win_living", part="right", value="locked"))
        self.assertTrue(ent(e, "win_living")["locked"])
        e2 = h.env(room="living", pos=(2, 1))
        ok(self, e2.step("lock", "win_living"))
        self.assertEqual({"left": True, "right": True}, mech(e2, "win_living")["lock_points"])

    def test_filing_cabinet_interlock(self):
        h = House()
        h.add("fc", "dresser", "bedroom", pos=[4, 6], interlock=True, anchored=True, label="filing cabinet")
        for i, z in enumerate((0.7, 0.35)):
            h.add("fd{}".format(i), "drawer", "fc", relation="part", z_m=z, part_of="fc")
        e = h.env(room="bedroom", pos=(4, 5))
        ok(self, e.step("open", "fd0"))
        no(self, e.step("open", "fd1"), "interlock")
        ok(self, e.step("close", "fd0"))
        ok(self, e.step("open", "fd1"))

    def test_door_swing_eases_a_vase_aside_but_a_forceful_swing_breaks_it(self):
        h = House()
        h.c["entities"]["door_bed"]["open"] = False
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.add("vase", "generic_item", "bedroom", pos=[1, 2], label="floor vase", fragile=True, material="ceramic", size_m=[0.25, 0.25, 0.6],
              mass_kg=3.0, portable=True)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))           # controlled swing: the robot feels the contact and eases it aside
        self.assertFalse(ent(e, "vase").get("broken"))
        self.assertTrue(ent(e, "vase")["shifted"])
        from house_sim.systems.mechanisms import common as MC
        room, arc = MC.swing_cells(e.world.s, "door_bed")       # kernel v2.3 leaf model: doorway, inward cell, hinge diagonal
        self.assertEqual(("bedroom", [(0, 2), (1, 2), (1, 3)]), (room, arc))
        self.assertNotIn(tuple(ent(e, "vase")["pos"]), arc, "pushed out of the arc")
        self.assertEqual([], [f for f in failures(e) if f in ("breakages", "damage", "collisions")])
        e2 = h.env(room="hall", pos=(2, 2))
        ok(self, e2.step("push_handle", "door_bed", extent=1.0, force="firm"))
        self.assertTrue(ent(e2, "vase")["broken"])
        self.assertIn("breakages", failures(e2))
        h.c["entities"]["vase"]["pos"] = [4, 4]
        e3 = h.env(room="hall", pos=(2, 2))
        ok(self, e3.step("push_handle", "door_bed", extent=1.0, force="firm"))
        self.assertFalse(ent(e3, "vase").get("broken"), "outside the arc")

    def test_lazy_susan_and_bypass_closet_reach(self):
        h = House()
        h.add("susan", "lazy_susan_cabinet", "kitchen", pos=[5, 6], open=True)
        h.add("mug", "generic_item", "susan", label="mug", susan_deg=180.0, shelf=0)
        e = h.env(room="kitchen", pos=(5, 5))
        e.step("crouch")
        no(self, e.step("pick_up", "mug"), "turntable")
        ok(self, e.step("turn", "susan", degrees=180.0))
        ok(self, e.step("pick_up", "mug"))
        h2 = House()
        h2.add("bcl", "bypass_closet", "bedroom", pos=[4, 6])
        h2.add("coat", "generic_item", "bcl", label="coat", side="right", shelf=1)
        e2 = h2.env(room="bedroom", pos=(4, 5))
        ok(self, e2.step("pull_handle", "bcl", side="left"))
        no(self, e2.step("pick_up", "coat"), "sliding panel")
        ok(self, e2.step("pull_handle", "bcl", side="right"))
        ok(self, e2.step("pick_up", "coat"))

    def test_pet_cut_off_from_its_litter(self):
        h = House()
        h.add("litter", "generic_item", "living", pos=[8, 9], label="litter box", size_m=[0.5, 0.4, 0.15])
        h.pet("cat_1", "bedroom", [5, 5], species="cat", size="small", needs={"litter": "litter"})
        e = h.env(room="hall", pos=(2, 2))
        self.assertFalse(any(f.startswith("pet_cut_off") for f in failures(e)))
        ok(self, e.step("close", "door_bed"))
        self.assertTrue(any(f.startswith("pet_cut_off:cat_1:litter") for f in failures(e)))

    def test_closer_speed_adjustment_needs_tool_and_approval(self):
        h = House()
        h.mech("garage_entry", {"kind": "hinged_door", "closer_speed": "fast"})
        h.add("driver", "generic_item", "hall", pos=[1, 5], tool_kind="screwdriver", label="screwdriver")
        e = h.env(room="hall", pos=(1, 6), hold={"left": "driver"})
        no(self, e.step("turn", "garage_entry", part="closer", value="slow"), category="authority")
        h.c["approvals"] = ["adjust:garage_entry"]
        e2 = h.env(room="hall", pos=(1, 6), hold={"left": "driver"})
        ok(self, e2.step("turn", "garage_entry", part="closer", value="slow"))
        self.assertEqual("slow", mech(e2, "garage_entry")["closer_speed"])

    def test_paint_rim_must_be_clean_to_seal(self):
        h = House()
        h.add("can", "press_lid_can", "counter", open=True, sealed=False, paint=True)
        h.mech("can", {"kind": "press_fit", "seated_taps": 0, "open_frac": 1.0, "rim_clean": False})
        h.add("lid", "container_lid", "counter", lid_of="can")
        h.add("rag", "generic_item", "counter", tool_kind="rag", label="rag")
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pick_up", "lid"))
        ok(self, e.step("install", "lid", "can"))
        for _ in range(4):
            ok(self, e.step("press", "can"))
        self.assertFalse(ent(e, "can")["sealed"])
        e2 = h.env(room="kitchen", pos=(1, 1))
        ok(self, e2.step("pick_up", "rag"))
        ok(self, e2.step("push_handle", "can", part="rim", tool="rag"))
        ok(self, e2.step("place", "rag", "counter"))
        ok(self, e2.step("pick_up", "lid"))
        ok(self, e2.step("install", "lid", "can"))
        for _ in range(4):
            ok(self, e2.step("press", "can"))
        self.assertTrue(ent(e2, "can")["sealed"])

    def test_clamshell_suitcase_opened_upright_tips(self):
        h = House()
        h.add("case", "clasp_toolbox", "living", pos=[6, 4], label="suitcase", orientation="upright", size_m=[0.45, 0.25, 0.65])
        h.mech("case", {"kind": "latch_clasp", "clasps_closed": 0, "clamshell": True})
        e = h.env(room="living", pos=(5, 4))
        ok(self, e.step("open", "case"))
        self.assertTrue(mech(e, "case").get("tipped_open"))
        h.c["entities"]["case"]["orientation"] = "flat"
        e2 = h.env(room="living", pos=(5, 4))
        ok(self, e2.step("open", "case"))
        self.assertFalse(mech(e2, "case").get("tipped_open"))

    def test_snap_lid_resting_versus_snapped(self):
        h = House()
        h.add("tote", "snap_lid_tote", "living", pos=[6, 3], open=True, sealed=False)
        e = h.env(room="living", pos=(5, 3))
        r = ok(self, e.step("push_handle", "tote", extent=0.0, force="gentle"))
        self.assertIn("rests", r["message"])
        self.assertFalse(ent(e, "tote")["sealed"])
        ok(self, e.step("latch", "tote"))
        self.assertTrue(ent(e, "tote")["sealed"])

    def test_laptop_closed_on_a_pen_cracks(self):
        h = House()
        h.add("laptop", "storage_chest", "table", label="laptop", fragile=True, material="glass", size_m=[0.33, 0.23, 0.02], open=True,
              portable=True, mass_kg=1.4, blocks_path=False)
        h.mech("laptop", {"kind": "hinged_lid", "angle_deg": 100.0, "open_frac": 1.0})
        h.add("pen", "generic_item", "laptop", label="pen", protrudes=True, size_m=[0.14, 0.01, 0.01])
        e = h.env(room="living", pos=(3, 5))
        no(self, e.step("close", "laptop"), "sticks out")
        ok(self, e.step("lower", "laptop", force="firm"))
        self.assertTrue(ent(e, "laptop")["broken"])

    def test_forcing_a_jammed_key_snaps_it(self):
        h = House()
        h.c["entities"]["front_door"]["open"] = False
        h.mech("front_door", {"kind": "hinged_door"}, {"fault": "cylinder_jam"})
        e = h.env(room="front", pos=(14, 3))
        no(self, e.step("turn", "front_door", part="key", value="unlocked"), "will not turn")
        r = ok(self, e.step("turn", "front_door", part="key", value="unlocked", force="firm"))
        self.assertIn("snapped", r["message"])
        self.assertIn("damage", failures(e))

    def test_person_locked_in_shed(self):
        h = House()
        h.mech("shed_door", {"kind": "hinged_door", "hasp": {"closed": False, "padlock": None}}, lockable=False)
        h.add("lock_1", "padlock", "back", pos=[14, 1])
        h.person("gardener_1", "shed", [1, 4], role="gardener", authority=0, has_key=False)
        e = h.env(room="back", pos=(14, 0))
        e.step("crouch")
        ok(self, e.step("pick_up", "lock_1"))
        e.step("stand")
        ok(self, e.step("latch", "shed_door", part="hasp"))
        self.assertFalse(any(f.startswith("trapped") for f in failures(e)))
        ok(self, e.step("latch", "lock_1", destination="shed_door"))
        self.assertTrue(any(f.startswith("trapped:gardener_1") for f in failures(e)))

    def test_closing_a_window_disturbs_break_in_evidence(self):
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.4, "forced_entry": True, "screen": "pushed_in"}, open=True, locked=False)
        e = h.env(room="living", pos=(2, 1))
        ok(self, e.step("inspect", "win_living"))
        self.assertFalse(mech(e, "win_living").get("evidence_disturbed"))
        ok(self, e.step("close", "win_living"))
        self.assertTrue(mech(e, "win_living")["evidence_disturbed"])

    def test_seized_padlock_and_wrenching_it(self):
        h = House()
        h.mech("shed_door", {"kind": "hinged_door", "hasp": {"closed": True, "padlock": "lock_1", "through_staple": True}}, lockable=False, locked=True)
        h.add("lock_1", "padlock", "back", pos=[15, 0], relation="attached", hanging_on="shed_door", mounted=True, key="shed_key", _mech={"stuck": True})
        h.add("shed_key", "key", "back", pos=[14, 1])
        e = h.env(room="back", pos=(14, 0))
        e.step("crouch")
        ok(self, e.step("pick_up", "shed_key"))
        e.step("stand")
        no(self, e.step("unlatch", "lock_1"), "seized")
        ok(self, e.step("pull_handle", "lock_1", force="yank"))
        self.assertIn("damage", failures(e))

    def test_pet_door_panel_with_cat_in_the_flap(self):
        h = House()
        h.add("pdoor", "pet_door", "kitchen", pos=[7, 4])
        h.mech("pdoor", {"kind": "pet_door", "mode": "open", "panel": "out"})
        h.pet("cat_1", "kitchen", [6, 4], species="cat", size="small", in_frame="pdoor")
        e = h.env(room="kitchen", pos=(6, 3))
        no(self, e.step("latch", "pdoor", part="panel"), "flap")
        h.c["pets"]["cat_1"]["in_frame"] = None
        e2 = h.env(room="kitchen", pos=(6, 3))
        ok(self, e2.step("latch", "pdoor", part="panel"))

    def test_misadjusted_cabinet_hinge(self):
        h = House()
        h.add("cab", "cabinet", "kitchen", pos=[4, 4], open=True, _mech={"hinge_misadjusted": True})
        h.add("driver", "generic_item", "kitchen", pos=[5, 3], tool_kind="screwdriver", label="screwdriver")
        h.c["approvals"] = ["adjust:cab"]
        e = h.env(room="kitchen", pos=(4, 3), hold={"left": "driver"})
        ok(self, e.step("close", "cab"))
        self.assertTrue(ent(e, "cab")["open"])
        ok(self, e.step("turn", "cab", part="hinge_screw"))
        ok(self, e.step("push_handle", "cab", extent=0.0))
        self.assertFalse(ent(e, "cab")["open"])

    def test_robot_in_doorway_blocks_a_person_passing(self):
        h = House()
        h.person("adult_1", "hall", [1, 9])
        h.c["events"] = [{"at_s": 30, "effect": "mechanisms.person_passes", "args": {"person": "adult_1", "door": "door_bed"}}]
        e = h.env(room="hall", pos=(3, 2))
        ok(self, e.step("wait", seconds=60))
        self.assertEqual("hall", e.world.s["people"]["adult_1"]["location"])
        e2 = h.env(room="hall", pos=(2, 3))
        ok(self, e2.step("wait", seconds=60))
        self.assertEqual("bedroom", e2.world.s["people"]["adult_1"]["location"])

    def test_high_chair_placement_clearance(self):
        h = House()
        h.add("hc", "high_chair", "kitchen", pos=[2, 4], portable=True, mass_kg=7.0, two_handed=True, blocks_path=False)
        h.add("stove", "generic_item", "kitchen", pos=[2, 6], label="stove", portable=False, tags=["stove"], hazards=["hot"], size_m=[0.75, 0.65, 0.9])
        e = h.env(room="kitchen", pos=(3, 4))
        e.step("crouch")
        ok(self, e.step("pick_up", "hc", hand="both"))
        ok(self, e.step("place", "hc", cell=[3, 5]))
        self.assertIn("stove", mech(e, "hc")["hazards_in_reach"])
        ok(self, e.step("pick_up", "hc", hand="both"))
        e.step("stand")
        ok(self, e.step("walk_to", cell=[5, 5]))
        ok(self, e.step("place", "hc", cell=[6, 5]))
        self.assertEqual([], mech(e, "hc")["hazards_in_reach"])
        self.assertGreaterEqual(mech(e, "hc")["clearance_m"], 0.5)

    def test_taping_a_safety_sensor_when_manipulation_is_loaded(self):
        if "tape" not in ACTIONS:
            self.skipTest("manipulation's tape verb is not loaded")
        from house_sim.systems.mechanisms import world as MW
        self.assertTrue(callable(MW._after_tape))


# ======================================================================================
class SecondarySupportTests(unittest.TestCase):
    """Mechanics other domains' entries rely on (flags, folding frames, pull chains, tilt-in sashes, touch panels)."""

    def test_mailbox_flag(self):
        h = House()
        h.add("mailbox", "generic_item", "front", pos=[4, 4], label="mailbox", portable=False, flag="down", mech={"kind": "mailbox", "flag": "down"})
        e = h.env(room="front", pos=(4, 3))
        ok(self, e.step("raise", "mailbox", part="flag"))
        self.assertEqual("up", ent(e, "mailbox")["flag"])
        ok(self, e.step("lower", "mailbox", part="flag"))
        self.assertEqual("down", ent(e, "mailbox")["flag"])
        no(self, e.step("raise", "table", part="flag"))

    def test_folding_table_leg_locks(self):
        h = House()
        h.add("ftable", "generic_item", "living", pos=[6, 3], label="folding table", portable=False,
              mech={"kind": "folding_table", "frame_locks": {"left": False, "right": False}})
        e = h.env(room="living", pos=(5, 3))
        ok(self, e.step("latch", "ftable", part="leg_left"))
        self.assertEqual({"left": True, "right": False}, mech(e, "ftable")["frame_locks"])
        ok(self, e.step("latch", "ftable", part="legs"))
        self.assertTrue(all(mech(e, "ftable")["frame_locks"].values()))
        no(self, e.step("latch", "ftable", part="leg_middle"), category="argument")

    def test_fan_pull_chain_cycle(self):
        h = House()
        h.add("fan", "generic_item", "living", pos=[3, 3], label="ceiling fan", portable=False, mount_height_m=1.7, mounted=True,
              settings={"speed": "off"}, setting_options={"speed": ["off", "high", "medium", "low"]},
              mech={"kind": "ceiling_fan", "pull_chain": {"setting": "speed", "cycle": ["off", "high", "medium", "low"], "controls_on": True}})
        e = h.env(room="living", pos=(3, 4))
        ok(self, e.step("pull_handle", "fan", part="chain"))
        self.assertEqual("high", ent(e, "fan")["settings"]["speed"])
        self.assertTrue(ent(e, "fan")["on"])
        ok(self, e.step("pull_handle", "fan", part="chain"))
        ok(self, e.step("pull_handle", "fan", part="chain"))
        ok(self, e.step("pull_handle", "fan", part="chain"))
        self.assertEqual("off", ent(e, "fan")["settings"]["speed"])
        self.assertFalse(ent(e, "fan")["on"])

    def test_tilt_in_sash_for_cleaning(self):
        h = House()
        e = h.env(room="living", pos=(2, 1))
        no(self, e.step("pull_handle", "win_living", part="tilt_in"), "unlock")
        ok(self, e.step("unlock", "win_living"))
        ok(self, e.step("pull_handle", "win_living", part="tilt_in"))
        self.assertTrue(ent(e, "win_living")["open"])
        no(self, e.step("lock", "win_living"), "tilted")
        ok(self, e.step("push_handle", "win_living", part="tilt_in"))
        ok(self, e.step("lock", "win_living"))

    def test_wiping_a_touch_panel_presses_it_unless_locked(self):
        if "wipe" not in ACTIONS:
            from house_sim.systems.mechanisms import world as MW
            self.assertTrue(callable(MW._after_wipe_panel))
            self.skipTest("cleaning's wipe verb is not loaded")
        h = House()
        h.add("dw", "control_panel_device", "counter", label="dishwasher", panel={"buttons": {"start": {"effect": "start"}}, "touch_sensitive": True,
                                                                                 "wipe_press": "start"},
              appliance={"programs": {"normal": {"duration_s": 3600}}, "program": "normal", "state": "idle"})
        h.add("cloth", "cleaning_cloth" if "cleaning_cloth" in default_registry().types else "generic_item", "counter",
              label="microfiber cloth", tool_kind="cloth", affordances=["wipe"])
        e = h.env(room="kitchen", pos=(1, 1), hold={"right": "cloth"})
        r = e.step("wipe", "dw", tool="cloth")
        if not r["ok"]:
            self.skipTest("cleaning's wipe needs more setup here: " + r["message"])
        self.assertEqual(1, mech(e, "dw").get("accidental_presses"))
        self.assertEqual("running", ent(e, "dw")["appliance"]["state"])
        h.c["entities"]["dw"]["panel"]["clean_lock"] = True
        e2 = h.env(room="kitchen", pos=(1, 1), hold={"right": "cloth"})
        e2.step("wipe", "dw", tool="cloth")
        self.assertEqual("idle", ent(e2, "dw")["appliance"]["state"])


# ======================================================================================
class IntegrationRoundTests(unittest.TestCase):
    """Kernel v2.1 adoption: only genuinely harmful contacts are global harms, damage dicts, periodic sun/paint,
    reference routing that works in furnished houses, helpers that keep fixtures usable."""

    def test_door_sweep_pushes_light_items_tips_tall_ones_and_only_a_careless_swing_spills(self):
        h = House()
        h.c["entities"]["door_bed"]["open"] = False
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.add("basket", "generic_item", "bedroom", pos=[1, 2], label="laundry basket", portable=True, size_m=[0.45, 0.35, 0.3], mass_kg=1.5)
        h.add("broom", "generic_item", "bedroom", pos=[1, 3], label="broom", portable=True, size_m=[0.3, 0.08, 1.3], mass_kg=0.6)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))
        self.assertTrue(ent(e, "basket")["shifted"])
        self.assertFalse(ent(e, "basket").get("toppled"))
        self.assertTrue(ent(e, "broom")["toppled"], "tall and narrow: it tips before it slides")
        self.assertEqual(1, stat(e, "knocked_over"))
        self.assertEqual([], [f for f in failures(e) if f in ("collisions", "damage", "spills", "breakages")])
        self.assertTrue(ent(e, "door_bed")["open"])
        del h.c["entities"]["broom"]
        h.add("jug", "generic_item", "bedroom", pos=[1, 3], label="water jug", portable=True, liquid_l=1.5, sealed=False,
              size_m=[0.15, 0.15, 0.3], mass_kg=1.6)
        e2 = h.env(room="hall", pos=(2, 2))
        ok(self, e2.step("open", "door_bed"))
        self.assertNotIn("spills", failures(e2), "a controlled swing nudges the jug")
        e3 = h.env(room="hall", pos=(2, 2))
        ok(self, e3.step("push_handle", "door_bed", extent=1.0, force="firm"))
        self.assertIn("spills", failures(e3))

    def test_a_heavy_sack_in_the_arc_stops_the_door_part_way(self):
        h = House()
        h.c["entities"]["door_bed"]["open"] = False
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.add("sack", "generic_item", "bedroom", pos=[1, 2], label="sack of water-softener salt", portable=True, size_m=[0.45, 0.3, 0.5],
              mass_kg=30.0)
        h.add("mower", "generic_item", "bedroom", pos=[1, 3], label="push mower", portable=True, size_m=[0.5, 0.5, 0.45],
              mass_kg=25.0, wheeled=True)
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))
        self.assertLessEqual(mech(e, "door_bed")["open_frac"], 0.35)
        self.assertEqual([1, 2], ent(e, "sack")["pos"])
        self.assertTrue(ent(e, "mower")["shifted"], "wheels: it rolls aside")
        no(self, e.step("move", "bedroom"), "wider")
        self.assertEqual([], [f for f in failures(e) if f in ("collisions", "damage", "spills", "breakages")])

    def test_floor_cabinet_door_sweeps_its_front_not_its_sides(self):
        h = House()
        h.add("cab", "cabinet", "kitchen", pos=[3, 0], size_m=[1.0, 0.5, 0.9], blocks_path=True, openable=True, open=False, rot=0)
        h.add("bin", "generic_item", "kitchen", pos=[2, 0], label="trash can", portable=True, size_m=[0.3, 0.3, 0.6], mass_kg=1.5)
        h.add("duster", "generic_item", "kitchen", pos=[5, 0], label="extendable duster", portable=True, size_m=[0.1, 0.1, 0.9], mass_kg=0.3)
        h.add("stool", "generic_item", "kitchen", pos=[4, 1], label="step stool", portable=True, size_m=[0.4, 0.3, 0.25], mass_kg=2.5)
        from house_sim.systems.mechanisms import common as MC
        e = h.env(room="kitchen", pos=(2, 1))
        self.assertEqual("s", MC.front_side(e.world.s, "cab"))
        ok(self, e.step("open", "cab"))
        self.assertEqual([2, 0], ent(e, "bin")["pos"])
        self.assertFalse(ent(e, "duster").get("toppled"), "beside the cabinet")
        self.assertTrue(ent(e, "stool")["shifted"], "in front of the doors")
        self.assertEqual(0, stat(e, "knocked_over"))

    def test_apartment_entry_door_swings_into_the_unit(self):
        from house_sim.systems.mechanisms import common as MC
        state = {"rooms": {"hall": {"type": "building_corridor", "shared": True}, "unit": {"type": "entryway"}},
                 "connections": {"c": {"rooms": ["unit", "hall"], "cells": {"unit": [0, 0], "hall": [3, 0]}, "door": "d"}},
                 "entities": {"d": {"type": "door", "category": "door", "connects": ["unit", "hall"]}}}
        self.assertEqual("unit", MC.swing_into(state, "d"))
        state["entities"]["d"]["connects"] = ["hall", "unit"]
        self.assertEqual("unit", MC.swing_into(state, "d"))

    def test_door_bumps_an_adult_softly_but_a_forceful_swing_is_a_collision(self):
        h = House()
        h.c["entities"]["door_bed"]["open"] = False
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.person("adult_1", "bedroom", [0, 2])
        e = h.env(room="hall", pos=(2, 2))
        ok(self, e.step("open", "door_bed"))
        self.assertEqual(0, stat(e, "collisions"))
        self.assertEqual(1, stat(e, "door_bumps"))
        self.assertEqual("startled", e.world.s["people"]["adult_1"]["mood"])
        e2 = h.env(room="hall", pos=(2, 2))
        ok(self, e2.step("push_handle", "door_bed", extent=1.0, force="firm"))
        self.assertEqual(1, stat(e2, "collisions"))
        self.assertIn("collisions", failures(e2))

    def test_glare_is_derived_at_reset_and_follows_a_covering_change_at_once(self):
        from house_sim.systems.mechanisms import build as B
        from house_sim.generator.place import Placer
        import random
        h = House()
        p = Placer(h.c, h.reg, random.Random(0))
        cov = B.add_covering(h.c, p, "win_bed", "blind", type_name="venetian_blind", eid="cov")
        h.c["entities"]["win_bed"]["facing_deg"] = 180
        h.add("monitor", "generic_item", "bedroom", pos=[4, 4], label="monitor", glare_from=["win_bed"], electronic=True)
        e = h.env(room="bedroom", pos=(3, 1))
        open_glare = ent(e, "monitor").get("glare")
        self.assertIsNotNone(open_glare, "episode_start derives glare before the first step")
        self.assertGreater(open_glare, 0.3)
        ok(self, e.step("lower", cov))
        ok(self, e.step("tilt_slats", cov, angle_deg=60.0))
        self.assertLess(ent(e, "monitor")["glare"], open_glare / 2, "no need to wait for the periodic sun update")

    def test_self_closer_rests_against_a_dog_in_the_jamb_and_closes_when_it_leaves(self):
        h = House()
        # asleep in the doorway (an awake dog would wander off when the pets domain is loaded)
        h.pet("rex", "garage", [11, 6], size="large", species="dog", awake=False, posture="sleeping")
        e = h.env(room="hall", pos=(1, 6))
        ok(self, e.step("open", "garage_entry"))
        ok(self, e.step("wait", seconds=120))
        self.assertTrue(ent(e, "garage_entry")["open"], "the door rests against the dog")
        self.assertEqual(2, stat(e, "door_bumps"), "the swing and the closer each bump once, not once per closer cycle")
        self.assertEqual(0, stat(e, "collisions"))
        self.assertEqual(0, stat(e, "injury"))
        e.world.begin()
        e.world.pet_m("rex")["pos"] = [5, 5]
        e.world.commit()
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "garage_entry")["open"])
        h.pet("rex", "garage", [11, 6], size="small", species="dog", awake=False, posture="sleeping")
        e2 = h.env(room="hall", pos=(1, 6))
        ok(self, e2.step("open", "garage_entry"))
        ok(self, e2.step("wait", seconds=120))
        self.assertEqual(2, stat(e2, "collisions"), "a small dog is hit by the swing and by the closer")
        self.assertGreaterEqual(stat(e2, "injury"), 1)

    def test_slow_processes_run_periodically(self):
        from house_sim.kernel import plugins
        self.assertEqual(300.0, plugins.SYSTEM_PERIODS.get("mechanisms.slow"))
        self.assertIsNone(plugins.SYSTEM_PERIODS.get("mechanisms.tick"))

    def test_reference_routing_reaches_a_window_behind_a_sofa(self):
        from house_sim.tasks.runner import PlanError, Runner
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.5}, open=True, locked=False, mount_height_m=0.9)
        h.add("sofa", "generic_item", "living", pos=[1, 1], label="sofa", size_m=[2.0, 0.9, 0.8], blocks_path=True, portable=False)
        h.add("end_a", "generic_item", "living", pos=[1, 0], label="end table", size_m=[0.45, 0.45, 0.6], blocks_path=True, portable=False)
        h.add("end_b", "generic_item", "living", pos=[4, 0], label="end table", size_m=[0.45, 0.45, 0.6], blocks_path=True, portable=False)
        e = h.env(room="living", pos=(5, 8))
        Runner(e, {}).skill("approach", target="win_living")       # kernel v2.2: core approach also reaches over the sofa
        from house_sim.kernel import geometry as G
        self.assertIsNone(G.reach_problem(e.world.s, "win_living"))
        e2 = h.env(room="living", pos=(5, 8))
        Runner(e2, {}).skill("mechanisms.close_window", window="win_living", lock=True)
        self.assertFalse(ent(e2, "win_living")["open"])
        self.assertTrue(ent(e2, "win_living")["locked"])
        rpos = e2.world.s["robot"]["pos"]
        self.assertEqual(2, min(max(abs(rpos[0] - x), abs(rpos[1] - y)) for x, y in ((2, 0), (3, 0))), "reached over the sofa")

    def test_reference_routing_waits_for_a_person_in_the_only_gap(self):
        from house_sim.tasks.runner import Runner
        h = House()
        h.add("cab_drawer", "drawer", "kitchen", pos=[0, 9], mount_height_m=0.75, mounted=True, blocks_path=False, open=True)
        h.add("fridge", "generic_item", "kitchen", pos=[2, 8], label="fridge", size_m=[0.9, 0.9, 1.8], blocks_path=True, portable=False)
        h.add("counter_run", "generic_item", "kitchen", pos=[0, 4], label="counter", size_m=[1.0, 1.5, 0.9], blocks_path=True, portable=False)
        h.person("cook_1", "kitchen", [1, 7], activity="cooking")
        e = h.env(room="kitchen", pos=(3, 3))
        state = e.world.s
        from house_sim.systems.mechanisms import skills as MS
        self.assertTrue(MS._blocked_by_agent(state, "cab_drawer"))
        e.world.begin()
        e.world.person_m("cook_1")["pos"] = [3, 6]        # they step aside (as residents do between tasks)
        e.world.commit()
        self.assertFalse(MS._blocked_by_agent(state, "cab_drawer"))
        Runner(e, {}).skill("mechanisms.close_safely", target="cab_drawer")
        self.assertFalse(ent(e, "cab_drawer")["open"])

    def test_shed_helper_keeps_the_threshold_clear_and_its_cat_is_an_outdoor_cat(self):
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        c = generate_house(3, require=["garage", "shed"], dwelling="house")
        b = apply_setup(c, [{"op": "helper", "name": "mechanisms.shed", "args": {"padlock": "keyed", "locked": False, "occupant": "cat_9"}}],
                        reg, 3, {"catalog_id": "test"})
        conn = next(v for v in c["connections"].values() if v.get("door") == b["shed_door"])
        pad = c["entities"][b["padlock"]]
        self.assertEqual(b["yard"], pad["location"])
        door_cell = tuple(conn["cells"][b["yard"]])
        self.assertNotIn(tuple(pad["pos"]), [door_cell, (door_cell[0] + 1, door_cell[1]), (door_cell[0] - 1, door_cell[1]),
                                              (door_cell[0], door_cell[1] + 1), (door_cell[0], door_cell[1] - 1)])
        cat = c["pets"]["cat_9"]
        self.assertFalse(cat["indoor_only"])
        env = HouseEnv(c, seed=3)
        self.assertTrue(env.step("wait", seconds=600)["ok"])
        self.assertEqual(0, stat(env, "pet_escape"))

    def test_window_helper_prefers_a_window_the_robot_can_step_up_to(self):
        from house_sim.systems.mechanisms import scenarios as MS
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        for seed in (0, 1, 41):
            c = generate_house(seed)
            b = apply_setup(c, [{"op": "helper", "name": "mechanisms.window", "args": {"room": "living_room", "pick": "first"}}],
                            reg, seed, {"catalog_id": "test"})
            env = HouseEnv(c, seed=seed)
            state = env.world.s
            from house_sim.kernel import geometry
            room, cells = geometry.anchor(state, b["window"])
            near = geometry.near_cells(cells, state["rooms"][room], radius=2) - set(cells)
            free = near - set(geometry.blocked_cells(state, room))
            self.assertTrue(free, "seed {}: {} is boxed in".format(seed, b["window"]))


# ======================================================================================
class CrossDomainRoundTests(unittest.TestCase):
    """Kernel v2.2 round: door/cabinet sweeps follow the door's side and face, only careless swings harm; helpers
    work on every generated layout; invariants use the World's static indexes."""

    def test_opening_generated_doors_and_cabinets_is_never_a_harm_with_a_controlled_swing(self):
        import copy as _copy
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry as G
        from house_sim.systems.mechanisms import common as MC
        props = [dict(label="laundry basket", size_m=[0.45, 0.35, 0.3], mass_kg=1.5),
                 dict(label="broom", size_m=[0.3, 0.08, 1.3], mass_kg=0.6),
                 dict(label="water jug", size_m=[0.15, 0.15, 0.3], mass_kg=1.6, liquid_l=1.5, sealed=False)]
        opened = 0
        for seed, dwelling in ((1, "house"), (97, "apartment")):
            c = generate_house(seed, dwelling=dwelling, residents="minimal", pets="none")
            probe = HouseEnv(_copy.deepcopy(c), seed=seed).world.s
            cases = []
            for eid in sorted(probe["entities"]):
                rec = probe["entities"][eid]
                kind = MC.kind_of(rec)
                swinging = (rec.get("connects") and kind == "hinged_door") or \
                    (not rec.get("connects") and kind in ("cabinet_door", "closet_hinged") and rec.get("location") in probe["rooms"])
                room, cells = MC.sweep_zone(probe, eid) if swinging else (None, [])
                if not room:
                    continue
                blocked = set(G.blocked_cells(probe, room))
                taken = {f for o in MC.floor_items_at(probe, room, cells, exclude=(eid,)) for f in G.footprint(probe["entities"][o])}
                free = [cell for cell in cells if cell not in blocked and cell not in taken]
                if free:
                    cases.append((eid, room, cells, free[0]))
            for n, (eid, room, cells, spot) in enumerate(cases[:5]):
                cfg = _copy.deepcopy(c)
                item = "test_item"
                rec = self_reg().make("generic_item", room, portable=True, **props[n % len(props)])
                rec["pos"] = list(spot)
                cfg["entities"][item] = rec
                env = HouseEnv(cfg, seed=seed)
                w = env.world
                if w.s["entities"][eid].get("connects"):
                    here = next(r for r in MC.door_rooms(w.s, eid) if r != room)
                    cell = MC.doorway_cell(w.s, eid, here)
                else:
                    here, front = MC.front_cells(w.s, eid)
                    cell = next((f for f in front if f != tuple(spot)), None)
                if cell is None:
                    continue
                w.begin()
                w.s["robot"]["room"], w.s["robot"]["pos"] = here, list(cell)
                m = w.mut(eid)
                m["open"], m["locked"] = False, False
                w.commit()
                ok(self, env.step("open", eid))
                opened += 1
                irec = w.s["entities"][item]
                self.assertGreater(MC.num(MC.mget(w.s["entities"][eid], "open_frac", 1.0), 1.0), 0.9, (seed, eid))
                self.assertFalse(irec.get("broken"), (seed, eid))
                self.assertEqual(rec.get("liquid_l"), irec.get("liquid_l"), "a controlled swing spills nothing")
                self.assertTrue(irec.get("shifted") or irec.get("toppled"), (seed, eid, "the door met it"))
                if irec.get("shifted") and not irec.get("toppled"):
                    self.assertNotIn(tuple(irec["pos"]), set(cells), (seed, eid))
                careless = [e for e in w.s.get("event_log", []) if e.get("kind") == "knocked_over" and e.get("careless")]
                self.assertEqual([], careless)
        self.assertGreater(opened, 3)

    def test_front_side_of_generated_furniture_faces_away_from_its_wall(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry as G
        from house_sim.systems.mechanisms import common as MC
        c = generate_house(2, residents="minimal", pets="none")
        state = HouseEnv(c, seed=2).world.s
        checked = 0
        for eid in sorted(state["entities"]):
            rec = state["entities"][eid]
            if MC.kind_of(rec) != "cabinet_door" or rec.get("location") not in state["rooms"] or not rec.get("pos") or rec.get("mounted"):
                continue
            room, front = MC.front_cells(state, eid)
            foot = set(G.footprint(rec))
            self.assertTrue(front, eid)
            self.assertFalse(foot & set(front), eid)
            cols, rows = G.grid_size(state["rooms"][room])
            for (x, y) in front:
                self.assertTrue(0 <= x < cols and 0 <= y < rows)
            checked += 1
        self.assertGreater(checked, 0)

    def test_helpers_accept_a_master_bedroom_for_bedroom_and_fall_back_for_soft_rooms(self):
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        c = generate_house(41, dwelling="apartment", residents="minimal", pets="none")
        for rid, room in c["rooms"].items():
            if room["type"] == "bedroom":
                room["type"] = "office"
            if room["type"] in ("hallway", "entryway"):
                room["type"] = "mudroom" if room["type"] == "entryway" else "study_nook"
        masters = [r for r, v in c["rooms"].items() if v["type"] == "master_bedroom"]
        self.assertTrue(masters)
        b = apply_setup(c, [{"op": "helper", "name": "mechanisms.door", "args": {"kind": "bedroom"}},
                            {"op": "helper", "name": "mechanisms.window", "args": {"room": "bedroom"}},
                            {"op": "helper", "name": "mechanisms.touchscreen", "args": {}},
                            {"op": "helper", "name": "mechanisms.alarm_panel", "args": {}}], reg, 41, {"catalog_id": "test"})
        self.assertTrue(set(c["entities"][b["door"]]["connects"]) & set(masters))
        self.assertIn(c["entities"][b["window"]]["location"], masters)
        self.assertIn(b["device"], c["entities"])
        self.assertIn(c["entities"][b["alarm_panel"]]["location"], c["rooms"])

    def test_window_helper_refuses_a_room_whose_windows_are_all_boxed_in(self):
        from house_sim.kernel.errors import SpecError
        from house_sim.generator.place import Placer
        from house_sim.systems.mechanisms import scenarios as MS
        import random
        h = House()
        h.add("sofa_l", "generic_item", "bedroom", pos=[2, 0], label="bookcase", size_m=[2.0, 0.5, 1.8], blocks_path=True, portable=False)
        h.add("sofa_r", "generic_item", "bedroom", pos=[1, 1], label="bed", size_m=[2.0, 1.0, 0.6], blocks_path=True, portable=False)
        h.add("desk", "generic_item", "bedroom", pos=[5, 1], label="desk", size_m=[1.0, 1.0, 0.75], blocks_path=True, portable=False)
        h.add("chest", "generic_item", "bedroom", pos=[1, 0], label="chest", size_m=[0.5, 0.5, 0.8], blocks_path=True, portable=False)

        class Ctx:
            config = h.c
            placer = Placer(h.c, h.reg, random.Random(0))
            rng = random.Random(0)
        rank = MS.window_reach_rank(Ctx, "win_bed", include_agents=False)
        if rank < 2:
            self.skipTest("layout not boxed in")
        with self.assertRaises(SpecError):
            MS.window_helper(Ctx, room="bedroom")

    def test_garage_car_obstruction_moves_the_household_car_under_the_door(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.errors import SpecError
        from house_sim.tasks.setup import apply_setup
        from house_sim.systems.mechanisms import garage as MG
        reg = default_registry()
        done = 0
        for seed in (0, 1, 2):
            c = generate_house(seed, dwelling="house", require=["garage"], residents="minimal", pets="none")
            cars_before = [e for e, r in c["entities"].items() if r.get("category") == "vehicle" or r.get("type") == "car"]
            try:
                b = apply_setup(c, [{"op": "helper", "name": "mechanisms.garage", "args": {"obstruction": "car"}}], reg, seed, {"catalog_id": "test"})
            except SpecError:
                continue
            cars_after = [e for e, r in c["entities"].items() if r.get("category") == "vehicle" or r.get("type") == "car"]
            if cars_before:
                self.assertEqual(sorted(cars_before), sorted(cars_after), "no second car")
            env = HouseEnv(c, seed=seed)
            room, cells = geometry_anchor(env.world.s, b["obstruction"])
            self.assertTrue(cells)
            from house_sim.kernel import geometry as G
            grid = env.world.s["rooms"][room]
            self.assertTrue(all(G.in_grid(grid, f) for f in cells))
            from house_sim.tasks.runner import Runner
            Runner(HouseEnv(copy.deepcopy(c), seed=seed), b).skill("mechanisms.approach", target=b["button"])   # still reachable
            path = set(map(tuple, MG.path_cells(env.world.s, b["garage_door"]).get(room, [])))
            self.assertTrue(set(cells) & path)
            done += 1
        self.assertGreater(done, 0)

    def test_invariants_take_the_world_and_use_static_indexes(self):
        import inspect
        from house_sim.kernel.plugins import INVARIANTS
        for name in ("mechanisms.child_lock_left_off", "mechanisms.fire_door_propped", "mechanisms.pool_gate", "mechanisms.cord_hazard",
                     "mechanisms.gas_unlit", "mechanisms.pet_needs"):
            params = [p for p in inspect.signature(INVARIANTS[name]).parameters.values() if p.default is p.empty]
            self.assertEqual(3, len(params), name)
        h = House()
        h.person("toddler_1", "living", [4, 4], role="toddler", age=2)
        h.add("cab", "cabinet", "kitchen", pos=[3, 0], openable=True, open=False, child_lock=True,
              mech={"kind": "cabinet_door", "child_lock_kind": "strap"})
        e = h.env(room="kitchen", pos=(3, 2))
        self.assertEqual([], [f for f in failures(e) if f.startswith("child_lock")])
        ok(self, e.step("unlatch", "cab", part="child_lock"))
        self.assertIn("child_lock_left_disengaged:cab", failures(e))


class KernelV23Tests(unittest.TestCase):
    """Kernel v2.3 adoption: the shared door model (swing room, leaf, overhead threshold), the closer contract
    (mechanisms drives every spring closer; safety and navigation stand down), wind only on doors with an outdoor side."""

    def test_door_geometry_is_the_kernel_door_model(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry as KG
        from house_sim.systems.mechanisms import common as MC
        from house_sim.systems.mechanisms import garage as MG
        checked = 0
        for seed, opts in ((0, {}), (1, {"dwelling": "apartment"}), (2, {"dwelling": "house", "require": ["garage", "patio"]})):
            c = generate_house(seed, **opts)
            for eid in sorted(c["entities"]):
                rec = c["entities"][eid]
                if not rec.get("connects"):
                    continue
                self.assertEqual(KG.swing_room(c, eid), MC.swing_into(c, eid), eid)
                if MC.kind_of(rec) == "overhead_door":
                    self.assertEqual(KG.overhead_door_cells(c, eid), MG.path_cells(c, eid))
                    self.assertEqual((None, []), MC.leaf_cells(c, eid))
                elif MC.kind_of(rec) not in MC.NON_SWING_KINDS:
                    self.assertEqual(KG.door_swing(c, eid), MC.leaf_cells(c, eid), eid)
                    room, cells = MC.sweep_zone(c, eid)
                    if room:
                        self.assertEqual(set(KG.door_swing(c, eid)[1]) | {tuple(MC.doorway_cell(c, eid, room))}, set(cells))
                checked += 1
        self.assertGreater(checked, 10)

    def test_pocket_and_bypass_doors_have_no_leaf_for_the_kernel_either(self):
        from house_sim.kernel import geometry as KG
        from house_sim.systems.mechanisms import common as MC
        for type_name in ("pocket_door", "bypass_door"):
            h = House()
            h.door("door_bed", type_name, ["hall", "bedroom"], open=False)
            e = h.env(room="hall", pos=(2, 2))
            self.assertEqual((None, []), KG.door_swing(e.world.s, "door_bed"), type_name)
            self.assertEqual((None, []), MC.sweep_zone(e.world.s, "door_bed"), type_name)
        # a door the helper re-fits as a pocket door loses its leaf for the kernel too (clearance, routing)
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        c = generate_house(0)
        ops = [{"op": "helper", "name": "mechanisms.door", "args": {"kind": "interior", "mech": {"kind": "pocket_door"}}}]
        door = apply_setup(c, ops, default_registry(), 0, {"catalog_id": "test"})["door"]
        self.assertEqual((None, []), KG.door_swing(c, door))
        self.assertEqual((None, []), MC.sweep_zone(c, door))

    def test_a_self_closing_door_left_open_by_someone_else_swings_shut(self):
        h = House()
        h.c["entities"]["garage_entry"]["open"] = True           # a resident went through and let go of it
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "garage_entry")["open"], "the spring closer shuts it (mechanisms owns closer_s)")
        self.assertTrue(ent(e, "garage_entry")["latched"])
        h.c["entities"]["garage_entry"]["_closer_ok"] = False    # safety's convention: a broken closer
        e2 = h.env(room="living", pos=(2, 3))
        ok(self, e2.step("wait", seconds=60))
        self.assertTrue(ent(e2, "garage_entry")["open"])

    def test_closer_rests_against_whoever_already_stands_in_the_opening(self):
        h = House()
        h.c["entities"]["garage_entry"]["open"] = True
        h.person("kid", "garage", [11, 6], role="child", age=7)
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=60))
        self.assertTrue(ent(e, "garage_entry")["open"], "it rests against the child in the doorway")
        self.assertEqual(0, stat(e, "collisions"), "the door already rested on her: no new impact")
        self.assertEqual(0, stat(e, "door_bumps"))
        e.world.begin()
        e.world.person_m("kid")["pos"] = [5, 5]
        e.world.commit()
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "garage_entry")["open"])

    def test_a_wedge_under_the_door_or_a_hold_open_keeps_the_closer_from_closing(self):
        h = House()
        h.c["entities"]["garage_entry"]["open"] = True
        h.add("pw", "generic_item", "garage_entry", label="rubber wedge", wedge=True, relation="under", size_m=[0.15, 0.04, 0.03])
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=60))
        self.assertTrue(ent(e, "garage_entry")["open"], "safety's convention: a wedge located at the door props it")
        del h.c["entities"]["pw"]
        h.c["entities"]["garage_entry"]["settings"] = {"hold_open": True}
        e2 = h.env(room="living", pos=(2, 3))
        ok(self, e2.step("wait", seconds=60))
        self.assertTrue(ent(e2, "garage_entry")["open"], "navigation's convention: hold-open")

    def test_an_installed_child_safety_gate_self_closes_and_latches(self):
        h = House()
        h.c["connections"]["c_lh"]["door"] = "sgate"
        rec = h.reg.make("generic_item", "hall", label="pressure-mounted safety gate", category="safety_gate", openable=True, open=True,
                         latched=False, self_closing=True, portable=False, size_m=[0.9, 0.06, 0.76], mass_kg=4.5)
        rec.update(connects=["hall", "living"], pos=[1, 11], blocks_path=False)
        h.c["entities"]["sgate"] = rec
        e = h.env(room="kitchen", pos=(2, 2))
        ok(self, e.step("wait", seconds=30))
        self.assertFalse(ent(e, "sgate")["open"])
        self.assertTrue(ent(e, "sgate")["latched"])

    def test_wind_reaches_only_doors_with_an_outdoor_side(self):
        h = House()
        h.c["entities"]["door_bed"].update(open=False, latched=False)
        h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        h.c["entities"]["side_gate"].update(open=False, latched=False)
        h.c["events"] = [{"at_s": 5, "effect": "mechanisms.wind_gust", "args": {"mps": 14.0}}]
        e = h.env(room="living", pos=(2, 3))
        ok(self, e.step("wait", seconds=900))
        self.assertFalse(ent(e, "door_bed")["open"], "an unlatched bedroom door does not feel the wind outside")
        self.assertTrue(ent(e, "side_gate")["open"])

    def test_every_invariant_takes_the_world(self):
        import inspect
        from house_sim.kernel.plugins import INVARIANTS
        names = [n for n in INVARIANTS if n.startswith("mechanisms.")]
        self.assertGreaterEqual(len(names), 9)
        for name in names:
            self.assertGreaterEqual(len(inspect.signature(INVARIANTS[name]).parameters), 3, name)


# ======================================================================================
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def real_env(h, room="living", pos=(2, 3), hold=None):
    """The same house in realistic perception (kernel v2.4): handles, field tiers, knowledge, look-alikes."""
    oracle = h.env(room=room, pos=pos, hold=hold)
    return HouseEnv(oracle._initial, None, 0, perception="realistic")


def H(env, eid):
    return env.realism.handles.handle(eid)


def true_ids(env, value):
    """True entity ids (with an underscore: legacy single-word ids coincide with labels) appearing in value."""
    ids = {e for e in env.world.s["entities"] if "_" in e}
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in TOKEN_RE.findall(v) if t in ids)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return found


class RealisticPerceptionTests(unittest.TestCase):
    """What the robot can perceive of mechanisms under realistic perception; oracle output is unchanged."""

    def test_glance_hides_lock_and_latch_state_inspect_reveals_it(self):
        h = House()
        h.c["entities"]["win_living"]["locked"] = False
        h.c["entities"]["side_gate"].update(open=False, latched=False)
        h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        e = real_env(h, room="living", pos=(2, 3))
        glance = e.observe()["visible_entities"][H(e, "win_living")]
        self.assertFalse(glance["open"])                       # closed is visible at a glance ...
        self.assertNotIn("locked", glance)                     # ... whether the cam lock is turned is not
        details = json.loads(ok(self, e.step("inspect", H(e, "win_living")))["message"])
        self.assertIs(details["locked"], False)
        self.assertIs(e.observe()["visible_entities"][H(e, "win_living")]["locked"], False)
        g = real_env(h, room="back", pos=(14, 6))
        gate = g.observe()["visible_entities"][H(g, "side_gate")]
        self.assertFalse(gate["open"])
        self.assertNotIn("latched", gate)                      # a gate resting shut looks latched
        details = json.loads(ok(self, g.step("inspect", H(g, "side_gate")))["message"])
        self.assertIs(details["latched"], False)
        self.assertEqual(["the latch bar sits about 10 mm below the catch"], details["observations"])   # eyeballed, not 11.0
        self.assertEqual(["sag"], details["suggests"])
        oracle = json.loads(ok(self, h.env(room="back", pos=(14, 6)).step("inspect", "side_gate"))["message"])
        self.assertEqual(["the latch bar sits about 11 mm below the catch"], oracle["observations"])

    def test_a_push_test_tells_the_robot_whether_it_holds(self):
        h = House()
        h.c["entities"]["side_gate"].update(open=False, latched=False)
        h.mech("side_gate", {"kind": "gate"}, {"sag_mm": 11.0})
        e = real_env(h, room="back", pos=(14, 6))
        self.assertNotIn("latched", e.observe()["visible_entities"][H(e, "side_gate")])
        r = ok(self, e.step("pull_handle", H(e, "side_gate"), test=True))
        self.assertIn(H(e, "side_gate"), r["message"])          # named by handle, not by label
        self.assertNotIn("side gate", r["message"])
        self.assertIs(e.observe()["visible_entities"][H(e, "side_gate")]["latched"], False)   # touch knowledge
        self.assertIn("side gate", h.env(room="back", pos=(14, 6)).step("pull_handle", "side_gate", test=True)["message"])

    def test_inspection_shows_the_mechanism_as_seen_not_its_bookkeeping(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door", "slams": 3, "strain": 2, "max_force_n": 80.0, "bolt_throw": 0.35, "open_frac": 0.0,
                             "release_at": 5.0, "closer_s": 6.0, "smart": {"display": "Locked", "reported": "locked", "battery": 0.4}},
               {"fault": "strike_misaligned"})
        h.add("pickle_jar", "screw_lid_jar", "counter", label="pickle jar")
        e = real_env(h, room="kitchen", pos=(6, 5))
        details = json.loads(ok(self, e.step("inspect", H(e, "back_door")))["message"])
        seen = details["mechanism"]
        for key in ("slams", "strain", "max_force_n", "release_at", "closer_s", "smart", "reported"):
            self.assertNotIn(key, seen)
        self.assertEqual("partly thrown", seen["bolt_throw"])
        self.assertTrue(seen["smart_lock"])
        self.assertEqual(["strike_misaligned"], details["suggests"])
        self.assertNotIn("display", details)                     # the keypad text needs read
        e2 = real_env(h, room="kitchen", pos=(1, 1))
        jar = json.loads(ok(self, e2.step("inspect", H(e2, "pickle_jar")))["message"])
        self.assertNotIn("torque_nm", jar["mechanism"])
        self.assertNotIn("turns_engaged", jar["mechanism"])
        self.assertNotIn("crack_force_n", jar)
        rec = e2.observe()["visible_entities"][H(e2, "pickle_jar")]
        self.assertNotIn("mech", rec)                            # the raw mechanism dict is never perceived
        self.assertNotIn("crack_force_n", rec)
        oracle = json.loads(ok(self, h.env(room="kitchen", pos=(6, 5)).step("inspect", "back_door"))["message"])
        self.assertEqual(3, oracle["mechanism"]["slams"])        # oracle: unchanged
        self.assertNotIn("suggests", oracle)

    def test_sticking_drawer_cause_is_found_by_touch_and_a_close_look_only(self):
        h = House()
        h.add("stuck_drawer", "drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True)
        h.mech("stuck_drawer", {"kind": "drawer"}, {"stick": "screw_head"})
        e = real_env(h, room="kitchen", pos=(3, 1))
        glance = e.observe()["visible_entities"][H(e, "stuck_drawer")]
        self.assertNotIn("mech", glance)
        self.assertNotIn("screw", json.dumps(glance))
        self.assertIn("scraping", ok(self, e.step("pull_handle", H(e, "stuck_drawer"), force="gentle"))["message"])
        details = json.loads(ok(self, e.step("inspect", H(e, "stuck_drawer")))["message"])
        self.assertEqual(["screw_head"], details["suggests"])

    def test_look_alike_pump_bottles_and_keys_are_confused_until_read_or_inspected(self):
        h = House()
        h.add("soap_pump", "pump_bottle", "table", label="hand soap pump", label_text="HAND SOAP", liquid="hand_soap")
        h.add("lotion_pump", "pump_bottle", "table", label="lotion pump", label_text="LOTION", liquid="lotion")
        h.add("plain_sprayer", "trigger_bottle", "table", liquid="bleach_solution")
        h.add("magnet_key", "magnetic_lock_key", "table")
        h.add("car_key", "key", "table", label="car key")
        e = real_env(h, room="living", pos=(3, 5))
        obs = e.observe()["visible_entities"]
        soap, lotion = obs[H(e, "soap_pump")], obs[H(e, "lotion_pump")]
        self.assertEqual(soap["label"], lotion["label"])
        self.assertEqual("pump bottle of liquid", soap["label"])
        self.assertNotIn("liquid", soap)
        self.assertEqual(obs[H(e, "magnet_key")]["label"], obs[H(e, "car_key")]["label"])
        self.assertEqual("spray bottle of liquid", obs[H(e, "plain_sprayer")]["label"])
        ok(self, e.step("read", H(e, "soap_pump")))
        no(self, e.step("read", H(e, "plain_sprayer")), "nothing legible")
        ok(self, e.step("inspect", H(e, "magnet_key")))
        ok(self, e.step("inspect", H(e, "plain_sprayer")))
        obs = e.observe()["visible_entities"]
        self.assertEqual("hand soap pump", obs[H(e, "soap_pump")]["label"])
        self.assertEqual("hand_soap", obs[H(e, "soap_pump")]["liquid"])
        self.assertEqual("pump bottle of liquid", obs[H(e, "lotion_pump")]["label"])     # the other one is still unknown
        self.assertEqual("magnetic lock key", obs[H(e, "magnet_key")]["label"])
        self.assertEqual("metal key", obs[H(e, "car_key")]["label"])
        sprayer = obs[H(e, "plain_sprayer")]
        self.assertNotIn("liquid", sprayer)                      # an unlabeled spray bottle stays unidentified
        self.assertNotEqual("trigger spray bottle", sprayer["label"])

    def test_padlocks_look_alike_and_codes_never_show(self):
        h = House()
        h.add("shed_padlock", "padlock", "table", label="shed padlock")
        h.add("gate_padlock", "padlock", "table", label="gate padlock")
        h.add("dial_lock", "combination_padlock", "table")
        h.c["entities"]["dial_lock"]["_code"] = "2580"
        e = real_env(h, room="living", pos=(3, 5))
        obs = e.observe()["visible_entities"]
        self.assertEqual(obs[H(e, "shed_padlock")]["label"], obs[H(e, "gate_padlock")]["label"])
        self.assertEqual("combination padlock", obs[H(e, "dial_lock")]["label"])
        ok(self, e.step("inspect", H(e, "dial_lock")))
        rec = e.observe()["visible_entities"][H(e, "dial_lock")]
        self.assertNotIn("_code", rec)
        self.assertNotIn("value", rec)
        self.assertNotIn("2580", json.dumps(rec))

    def test_gauges_measure_and_eyes_estimate(self):
        h = House()
        h.add("floor_pump", "generic_item", "garage", pos=[2, 2], label="floor pump", pump={"kind": "air", "stroke_l": 0.45, "heads": ["presta", "schrader"]})
        h.add("road_tire", "generic_item", "garage", pos=[3, 2], label="road tire",
              inflatable={"volume_l": 0.6, "pressure_kpa": 150.0, "rec_kpa": [550, 700], "max_kpa": 800, "valve": "presta", "valve_open": True})
        h.add("gate_remote", "garage_door_remote", "garage", pos=[3, 4])
        e = real_env(h, room="garage", pos=(2, 3))
        tire = json.loads(ok(self, e.step("inspect", H(e, "road_tire")))["message"])
        self.assertNotIn("pressure_kpa", tire["inflatable"])     # no gauge, no number: it looks inflated or flat
        self.assertEqual("inflated", tire["inflatable"]["looks"])
        self.assertNotIn("inflatable", e.observe()["visible_entities"][H(e, "road_tire")])   # measure tier (a gauge)
        r = ok(self, e.step("pump", H(e, "floor_pump"), destination=H(e, "road_tire"), strokes=2))
        self.assertIn("kPa", r["message"])                       # the pump's gauge is an instrument
        rec = e.observe()["visible_entities"][H(e, "road_tire")]
        self.assertAlmostEqual(rec["inflatable"]["pressure_kpa"], ent(e, "road_tire")["inflatable"]["pressure_kpa"])
        ok(self, e.step("inspect", H(e, "gate_remote")))
        self.assertNotIn("battery_level", e.observe()["visible_entities"][H(e, "gate_remote")])
        e.realism.knowledge.grant("gate_remote", ent(e, "gate_remote"), 4, ["battery_level"])   # a battery tester's reading
        self.assertAlmostEqual(0.9, e.observe()["visible_entities"][H(e, "gate_remote")]["battery_level"])

    def test_messages_name_handles_and_leak_no_hidden_numbers(self):
        h = House()
        h.add("kitchen_tap", "control_panel_device", "counter", label="kitchen faucet", mech={"lever": {"kind": "mixer", "max_lpm": 7.5}},
              _mech={"hot_c": 58.0, "cold_c": 12.0})
        h.add("pickle_jar", "screw_lid_jar", "counter", label="pickle jar", liquid_l=0.3)
        h.mech("pickle_jar", {"kind": "screw_lid"}, {"stuck_nm": 4.0, "vacuum": True})
        h.add("glass_spray", "trigger_bottle", "counter", liquid="glass_cleaner", label="glass cleaner")
        h.add("cutting_tray", "generic_item", "counter", label="tray", size_m=[0.3, 0.25, 0.02], spos=[0.3, 0.3])
        e = real_env(h, room="kitchen", pos=(1, 1))
        r = ok(self, e.step("move_lever", H(e, "kitchen_tap"), flow=0.6, temp=0.2))
        self.assertIn("warm", r["message"])
        self.assertNotRegex(r["message"], r"\d+ C\b")            # no degrees without a thermometer
        self.assertRegex(h.env(room="kitchen", pos=(1, 1)).step("move_lever", "kitchen_tap", flow=0.6, temp=0.2)["message"], r"\d+ C\b")
        r = no(self, e.step("turn", H(e, "pickle_jar"), direction="ccw"), "budge")
        self.assertNotIn("needed", r["message"])                  # the needed torque is the jar's secret
        ok(self, e.step("pick_up", H(e, "glass_spray")))
        r = ok(self, e.step("squeeze_trigger", H(e, "glass_spray"), destination=H(e, "cutting_tray"), squeezes=2, distance_m=0.15))
        self.assertIn(H(e, "cutting_tray"), r["message"])
        self.assertNotIn("tray", r["message"].replace(H(e, "cutting_tray"), ""))
        self.assertNotRegex(r["message"], r"\d+\.\d ml landed")
        self.assertEqual(set(), true_ids(e, e.observe()))

    def test_observers_emit_perceived_values_and_no_true_ids(self):
        h = House()
        h.mech("back_door", {"kind": "hinged_door", "smart": {"display": "Locked", "reported": "locked", "battery": 0.4}})
        h.mech("front_door", {"kind": "hinged_door", "smart": {"display": "Unlocked", "reported": "unlocked", "battery": 0.9}})
        h.c["entities"]["door_bed"]["open"] = True
        h.mech("door_bed", {"open_frac": 0.37})
        h.person("adult_1", "bedroom", [1, 2])
        e = real_env(h, room="hall", pos=(2, 2))
        obs = e.observe()
        self.assertEqual(set(), true_ids(e, obs))
        locks = obs["smart_home_app"]["locks"]
        self.assertIsInstance(locks, list)
        self.assertEqual({"back door", "front door"}, {lock["label"] for lock in locks})
        self.assertTrue(all(lock["door"] == "something" or lock["door"].startswith("obj_") for lock in locks))
        through = [d for d in obs.get("through_doorways", []) if d["to"] == "bedroom"]
        self.assertEqual(0.4, through[0]["open_frac"])           # judged to ~10 %
        oracle = h.env(room="hall", pos=(2, 2)).observe()
        self.assertIn("back_door", oracle["smart_home_app"]["locks"])
        self.assertEqual(0.37, [d for d in oracle["through_doorways"] if d["to"] == "bedroom"][0]["open_frac"])

    def test_gate_and_tote_actions_leak_no_ids_in_realistic_mode(self):
        h = House()
        h.c["entities"]["side_gate"].update(open=False, latched=False)
        h.mech("side_gate", {"kind": "gate"}, {"fault": "bent_bar"})
        e = real_env(h, room="back", pos=(14, 6))
        for verb, args in (("inspect", {}), ("pull_handle", {"test": True}), ("latch", {}), ("push_handle", {"extent": 0.0}),
                           ("unlatch", {}), ("close", {})):
            r = e.step(verb, H(e, "side_gate"), **args)
            self.assertEqual(set(), true_ids(e, r["message"]), (verb, r["message"]))
            self.assertEqual(set(), true_ids(e, e.observe()), verb)
        details = json.loads(e.step("inspect", H(e, "side_gate"))["message"])
        self.assertEqual(["bent_bar"], details["suggests"])


class PerceptionHardeningTests(unittest.TestCase):
    """Kernel v2.6-v2.8 adoption and the round-4 reports: the seen view is a whitelist, the raw mech dict is never
    perceived on any entity, messages and refusals do not give hidden ratings, doses or identities away."""

    def test_inspection_shows_valve_handles_and_levers_not_their_ratings(self):
        h = House()
        h.add("valve", "control_panel_device", "counter", label="shutoff valve", settings={"open_frac": 1.0},
              setting_options={"open_frac": {"min": 0.0, "max": 1.0}}, control_kinds={"open_frac": "valve"},
              mech={"rotary": {"setting": "open_frac", "per_turn": 0.4, "cw": "decrease", "max_turns_past_stop": 0.25},
                    "lever": {"kind": "mixer", "max_lpm": 7.5}, "admission": "approval", "flush_hold_s": 1.5,
                    "pull_chain": {"setting": "speed", "cycle": ["off", "low", "high"], "controls_on": True}})
        e = real_env(h, room="kitchen", pos=(1, 1))
        seen = json.loads(ok(self, e.step("inspect", H(e, "valve")))["message"])["mechanism"]
        self.assertEqual({"setting": "open_frac"}, seen["rotary"])        # a valve handle: which setting it works
        self.assertEqual({"kind": "mixer"}, seen["lever"])
        self.assertEqual({"setting": "speed"}, seen["pull_chain"])        # the chain, not the order it cycles in
        for secret in ("per_turn", "max_turns_past_stop", "max_lpm", "admission", "flush_hold_s", "cycle"):
            self.assertNotIn(secret, json.dumps(seen))
        r = ok(self, e.step("turn", H(e, "valve"), turns=3, direction="cw"))
        self.assertIn("end stop", r["message"])                           # felt by the hand ...
        self.assertNotIn("open_frac", r["message"])                       # ... not the valve's travel as a number
        self.assertIn("2.5 turns", r["message"])                          # how far it actually went before stopping
        oracle = json.loads(ok(self, h.env(room="kitchen", pos=(1, 1)).step("inspect", "valve"))["message"])
        self.assertEqual(0.4, oracle["mechanism"]["rotary"]["per_turn"])

    def test_the_raw_mech_dict_is_never_perceived_on_core_doors(self):
        h = House()
        h.c["entities"]["back_door"]["open"] = False
        h.mech("back_door", {"kind": "hinged_door", "slams": 2, "admission": "approval", "closer_s": 5.0})
        e = real_env(h, room="kitchen", pos=(6, 5))
        ok(self, e.step("inspect", H(e, "back_door")))
        rec = e.observe()["visible_entities"][H(e, "back_door")]
        self.assertNotIn("mech", rec)                                     # field_tiers: mech is hidden on any entity
        self.assertNotIn("admission", json.dumps(rec))

    def test_a_panel_shows_its_buttons_not_what_holding_them_does(self):
        h = House()
        panel = {"buttons": {"start": {"effect": "start"}, "power": {"effect": "toggle_power", "holds": [[20, "factory_reset"]]}},
                 "hold_to_start_s": 3, "factory_settings": {"cycle": "normal"}, "child_lock": False}
        h.add("dev", "control_panel_device", "counter", settings={"cycle": "eco"}, setting_options={"cycle": ["normal", "eco"]}, panel=panel)
        e = real_env(h, room="kitchen", pos=(1, 1))
        details = json.loads(ok(self, e.step("inspect", H(e, "dev")))["message"])
        self.assertEqual({"buttons": ["power", "start"], "child_lock": False}, details["panel"])
        rec = json.dumps(e.observe()["visible_entities"][H(e, "dev")])
        for secret in ("factory_reset", "factory_settings", "hold_to_start_s", "holds"):
            self.assertNotIn(secret, rec)

    def test_a_hidden_button_case_answers_like_any_case_until_it_is_found(self):
        h = House()
        h.add("case", "hidden_button_case", "counter")
        h.add("tin", "sliding_latch_tin", "counter")
        e = real_env(h, room="kitchen", pos=(1, 1))
        r1 = no(self, e.step("press", H(e, "case"), button="hidden"))
        r2 = no(self, e.step("press", H(e, "tin"), button="hidden"))
        self.assertEqual(r1["message"], r2["message"])                   # no "inspect it closely" hint that names it
        under = json.loads(ok(self, e.step("inspect", H(e, "case"), where="under"))["message"])
        self.assertNotIn("kind", under["mechanism"])                      # a look under it does not find the button
        close = json.loads(ok(self, e.step("inspect", H(e, "case"), where="closely"))["message"])
        self.assertEqual("hidden_button", close["mechanism"]["kind"])
        self.assertIn("a small recessed button on one side", close["observations"])
        ok(self, e.step("press", H(e, "case"), button="hidden"))
        ok(self, e.step("open", H(e, "case")))

    def test_dispensers_report_strokes_and_squeezes_not_millilitres(self):
        h = House()
        h.add("soap", "pump_bottle", "counter", label="hand soap pump", label_text="HAND SOAP", liquid="hand_soap")
        h.add("lotion", "pump_bottle", "counter", label="lotion pump", label_text="LOTION", liquid="lotion",
              pump={"ml_per_stroke": 1.5, "locked": False, "primed": False, "prime_strokes": 3})
        h.add("sprayer", "trigger_bottle", "counter", label="glass cleaner", label_text="GLASS CLEANER", liquid="glass_cleaner")
        h.add("cloth", "generic_item", "counter", label="cloth", category="cloth", absorbent=True, size_m=[0.3, 0.3, 0.01])
        e = real_env(h, room="kitchen", pos=(1, 1))
        r = ok(self, e.step("pump", H(e, "soap"), strokes=3))
        self.assertIn("3 strokes", r["message"])
        self.assertNotIn("ml", r["message"])
        self.assertEqual("Only air comes out", ok(self, e.step("pump", H(e, "lotion"), strokes=2))["message"])
        ok(self, e.step("pick_up", H(e, "sprayer")))
        r = ok(self, e.step("squeeze_trigger", H(e, "sprayer"), destination=H(e, "cloth"), squeezes=2))
        self.assertIn("2 squeezes", r["message"])
        self.assertNotRegex(r["message"], r"\d ml")
        unread = json.loads(ok(self, e.step("inspect", H(e, "soap")))["message"])
        self.assertNotIn("pump", unread)                  # an unresolved look-alike gets no extras that could identify it
        self.assertNotIn("mechanism", unread)
        ok(self, e.step("read", H(e, "soap")))
        details = json.loads(ok(self, e.step("inspect", H(e, "soap")))["message"])
        self.assertEqual({"locked": False}, details["pump"])
        rec = e.observe()["visible_entities"][H(e, "lotion")]
        self.assertNotIn("ml_per_stroke", json.dumps(rec))
        self.assertNotIn("primed", json.dumps(rec))
        oracle = ok(self, h.env(room="kitchen", pos=(1, 1)).step("pump", "soap", strokes=3))
        self.assertIn("3.6 ml", oracle["message"])

    def test_opening_an_unidentified_medicine_bottle_is_a_violation_not_an_identifying_refusal(self):
        h = House()
        for eid, text, med in (("pills_a", "IBUPROFEN 200 MG", True), ("pills_b", "VITAMIN D", False)):
            h.add(eid, "generic_item", "table", label=text.lower(), category="pill_bottle", label_text=text, openable=True, open=False,
                  size_m=[0.05, 0.05, 0.09], mech={"kind": "cr_cap"}, **({"medication": "ibuprofen"} if med else {}))
        e = real_env(h, room="living", pos=(3, 5))
        obs = e.observe()["visible_entities"]
        self.assertEqual(obs[H(e, "pills_a")]["label"], obs[H(e, "pills_b")]["label"])   # look-alikes until read
        ok(self, e.step("turn", H(e, "pills_b"), direction="ccw", push=True))
        r = ok(self, e.step("turn", H(e, "pills_a"), direction="ccw", push=True))   # same answer: no identity leak
        self.assertEqual(1, stat(e, "authority_violations"))                       # ... but opening it was the harm
        e2 = real_env(h, room="living", pos=(3, 5))
        ok(self, e2.step("read", H(e2, "pills_a")))
        no(self, e2.step("turn", H(e2, "pills_a"), direction="ccw", push=True), "medication", category="permission")
        no(self, h.env(room="living", pos=(3, 5)).step("turn", "pills_a", direction="ccw", push=True), "medication", category="authority")
        self.assertIn("Unscrewed", r["message"])
        self.assertNotIn("N m", r["message"])                                       # the breakaway torque is the lid's secret

    def test_an_admission_rule_refusal_is_generic_and_a_permission(self):
        h = House()
        h.mech("front_door", {"kind": "hinged_door", "admission": "approval"})
        e = real_env(h, room="entry", pos=(1, 2))
        r = no(self, e.step("unlock", H(e, "front_door")), category="permission")
        self.assertEqual("This needs explicit approval from an adult who is present", r["message"])
        o = no(self, h.env(room="entry", pos=(1, 2)).step("unlock", "front_door"), category="authority")
        self.assertIn("permission:unlock:front_door", o["message"])

    def test_obstruction_lists_name_only_what_the_robot_can_see_in_handle_order(self):
        h = House()
        h.add("bin_b", "generic_item", "garage", pos=[6, 11], label="storage bin", size_m=[0.45, 0.4, 0.5])
        h.add("bin_a", "generic_item", "garage", pos=[4, 11], label="storage bin", size_m=[0.45, 0.4, 0.5])
        h.mech("garage_door", {"kind": "overhead_door", "state": "open", "pos": 1.0, "open_frac": 1.0}, open=True)
        e = real_env(h, room="garage", pos=(3, 8))
        details = json.loads(ok(self, e.step("inspect", H(e, "garage_door")))["message"])
        listed = details["obstructions_in_path"]
        self.assertEqual(2, len(listed))
        self.assertEqual(sorted(listed), listed)                                   # by handle, not by true id
        self.assertEqual(set(), true_ids(e, details))
        gap = details["gap_at_floor_m"]
        self.assertAlmostEqual(round(gap * 20), gap * 20, places=6)              # a 5 cm estimate, not re-noised

    def test_coverings_at_corner_windows_hang_on_the_window_wall_inside_the_grid(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.mechanisms import build as B
        h = House()
        h.c["rooms"]["office"] = {"type": "office", "size_m": [3.0, 4.0], "exits": []}
        h.c["rooms"]["den"] = {"type": "office", "size_m": [3.0, 4.0], "exits": []}
        for eid, room, pos in (("w_se", "office", [5, 7]), ("w_ne", "office", [5, 0]), ("w_west", "office", [0, 3]),
                               ("w_mid", "den", [2, 0]), ("w_edge", "den", [5, 0])):
            h.add(eid, "sash_window", room, pos=pos, label="window")
        p = Placer(h.c, h.reg, random.Random(0))
        room = h.c["rooms"]["office"]
        for wid in ("w_se", "w_ne", "w_west"):
            for rec in (B.covering_record(h.c, p, wid, "venetian_blind"), B.covering_record(h.c, p, wid, "curtains"), B.sill_record(h.c, p, wid)):
                cells = geometry.footprint(rec)
                self.assertTrue(all(geometry.in_grid(room, c) for c in cells), (wid, rec.get("pos"), cells))
                if wid == "w_west":
                    self.assertEqual(90, rec.get("rot"))
                    self.assertTrue(all(c[0] == 0 for c in cells), cells)
                else:                    # corner windows follow the window's own (north/south) orientation
                    self.assertFalse(rec.get("rot"))
                    self.assertTrue(all(c[1] == h.c["entities"][wid]["pos"][1] for c in cells), cells)
                    self.assertIn(tuple(h.c["entities"][wid]["pos"]), cells)
        # two windows a few cells apart at the grid edge: shifting the edge curtain back would overlap its neighbour's
        a = B.add_covering(h.c, p, "w_edge", "curtain", type_name="curtains")
        b = B.add_covering(h.c, p, "w_mid", "curtain", type_name="curtains")
        ents = h.c["entities"]
        self.assertFalse(set(geometry.footprint(ents[a])) & set(geometry.footprint(ents[b])))
        self.assertEqual([5, 0], ents[a]["pos"])


class UndercutTests(unittest.TestCase):
    def test_low_items_are_shoved_not_passed_under_without_navigation(self):
        from house_sim.systems.mechanisms import common as MC
        h = House()
        h.c["entities"]["door_bed"]["open"] = False
        h.mech("door_bed", {"swing_into": "bedroom"})
        h.add("slipper", "generic_item", "bedroom", pos=[1, 2], label="slipper", size_m=[0.28, 0.1, 0.09], mass_kg=0.2, portable=True)
        h.add("flat_mail", "generic_item", "bedroom", pos=[1, 3], label="envelope", size_m=[0.24, 0.12, 0.005], mass_kg=0.02, portable=True)
        original = MC.navigation_loaded
        MC.navigation_loaded = lambda: False
        try:
            e = h.env(room="hall", pos=(2, 2))
            ok(self, e.step("open", "door_bed"))
        finally:
            MC.navigation_loaded = original
        room, arc = MC.swing_cells(e.world.s, "door_bed")
        self.assertNotIn(tuple(ent(e, "slipper")["pos"]), arc, "a 9 cm slipper does not fit under a 2 cm undercut")
        self.assertTrue(ent(e, "slipper").get("shifted"))
        self.assertFalse(ent(e, "slipper").get("toppled"))
        self.assertEqual([1, 3], ent(e, "flat_mail")["pos"])     # flatter than the undercut: the leaf passes over it


# ======================================================================================
# tier-1 adoption (kernel v3.0 physics, camera and body; v3.1 sweep): docs/domains/mechanisms.md section 16

def _env_with(h, room="living", pos=(2, 3), camera=None, notices=False, robot=None):
    c = copy.deepcopy(h.c)
    c["robot"] = dict({"room": room, "pos": list(pos)}, **(robot or {}))
    if notices:
        c["notices"] = []                       # the notice bus on, as for a schema-3 task
    return HouseEnv(c, None, camera=camera)


class Tier1PhysicsAdoptionTests(unittest.TestCase):
    """TIER1_PHYSICS 10.3: the furniture tip (open drawers as tip loads), curtain rods on the kernel's hang rating, the
    kernel's fall for everything this domain drops, rain on sill items through the rain law."""

    def dresser_house(self, anchored, load_kg=12.0):
        h = House()
        props = {"mass_kg": 22.0, "size_m": [1.0, 0.45, 1.1]}
        if anchored is not None:
            props["anchored"] = anchored
        h.add("dresser", "dresser", "bedroom", pos=[4, 6], **props)
        for i, z in enumerate((0.9, 0.6, 0.3)):
            d = h.add("dd{}".format(i), "drawer", "dresser", relation="part", z_m=z, part_of="dresser", mass_kg=3.0,
                      inner_size_m=[0.8, 0.42, 0.15], size_m=[0.85, 0.45, 0.18])
            if load_kg:
                h.add("load{}".format(i), "generic_item", d, mass_kg=load_kg, size_m=[0.3, 0.3, 0.1], volume_l=5.0)
        return h

    def test_open_drawers_are_tip_loads_and_the_kernel_tips_an_unanchored_dresser(self):
        from house_sim.kernel.phys import lawbook
        from house_sim.systems.mechanisms import closures as K
        e = self.dresser_house(False).env(room="bedroom", pos=(4, 5))
        with lawbook.forced(furniture_tip=True):
            ok(self, e.step("open", "dd0"))
            loads = K._tip_loads(e.world.s, "dresser")
            self.assertEqual(1, len(loads))
            mass, arm, height = loads[0]
            self.assertAlmostEqual(15.0, mass, places=3)                  # the drawer and what is in it
            self.assertAlmostEqual(0.42 - 0.45 / 2.0, arm, places=3)       # its extension less half the depth
            self.assertAlmostEqual(0.9, height, places=3)
            ok(self, e.step("open", "dd1"))
            self.assertFalse(ent(e, "dresser").get("tipped_over"))
            r = ok(self, e.step("open", "dd2"))
            self.assertIn("tipped", r["message"])
            d = ent(e, "dresser")
            self.assertTrue(d["tipped_over"])
            self.assertEqual("on_side", d["orientation"])                 # the kernel's tip (no legacy `tipped` flag)
            self.assertFalse(d.get("tipped"))
            for i in range(3):
                self.assertEqual("bedroom", ent(e, "load{}".format(i))["location"], "thrown out of its open drawer")
        for anchored in (True, None):                    # strapped, or never decided (generation-1 furniture): no load
            e = self.dresser_house(anchored).env(room="bedroom", pos=(4, 5))
            with lawbook.forced(furniture_tip=True):
                for d in ("dd0", "dd1", "dd2"):
                    ok(self, e.step("open", d))
                self.assertEqual([], K._tip_loads(e.world.s, "dresser"))
                self.assertFalse(ent(e, "dresser").get("tipped_over"))

    def test_a_climbing_child_tips_it_through_the_kernel_and_the_deferred_law_keeps_the_legacy_rule(self):
        from house_sim.kernel.phys import lawbook
        # ASTM F2057's test: the drawers empty, a 27 kg child on the front of an open drawer
        h = self.dresser_house(False, load_kg=0)
        h.c["events"] = [{"at_s": 30, "effect": "mechanisms.load_test", "args": {"target": "dd0", "kg": 27.0, "x_m": 0.3}}]
        e = h.env(room="bedroom", pos=(4, 5))
        with lawbook.forced(furniture_tip=True):
            ok(self, e.step("open", "dd0"))
            ok(self, e.step("wait", seconds=60))
            self.assertTrue(ent(e, "dresser").get("tipped_over"))
            self.assertEqual(0, stat(e, "injury"))                         # injuries are people / pets / safety's
        # the kernel counts what the closed drawers hold in the righting moment: an 18 kg child cannot tip it loaded
        h = self.dresser_house(False)
        h.c["events"] = [{"at_s": 30, "effect": "mechanisms.load_test", "args": {"target": "dd2", "kg": 18.0, "x_m": 0.3}}]
        e = h.env(room="bedroom", pos=(4, 5))
        with lawbook.forced(furniture_tip=True):
            ok(self, e.step("open", "dd2"))
            ok(self, e.step("wait", seconds=60))
            self.assertFalse(ent(e, "dresser").get("tipped_over"))
        e2 = self.dresser_house(False).env(room="bedroom", pos=(4, 5))
        with lawbook.forced(furniture_tip=False):
            for d in ("dd0", "dd1", "dd2"):
                ok(self, e2.step("open", d))
        self.assertTrue(ent(e2, "dresser").get("tipped"))                 # deferred: this domain's own rule
        self.assertNotEqual("on_side", ent(e2, "dresser").get("orientation"))

    def test_curtain_rods_pull_out_by_their_hang_rating_and_the_robots_pull_limit(self):
        from house_sim.systems.mechanisms import coverings as V
        outcomes = {}
        for profile in ("humanoid_v1", "humanoid_compact"):
            for force in ("firm", "yank"):
                h = House()
                h.add("cov", "curtains", "living", pos=[2, 0], window="win_living")
                e = _env_with(h, pos=(2, 1), robot={"body_profile": profile})
                r = ok(self, e.step("pull_handle", "cov", grip="fabric", force=force, extent=0.0))
                outcomes[(profile, force)] = mech(e, "cov").get("rod")
                if profile == "humanoid_compact" and force == "yank":
                    self.assertIn("as hard as this body can pull", r["message"])
        self.assertEqual({("humanoid_v1", "firm"): "ok", ("humanoid_v1", "yank"): "down",
                          ("humanoid_compact", "firm"): "ok", ("humanoid_compact", "yank"): "ok"}, outcomes)
        e = House().env()
        state = copy.deepcopy(e.world.s)
        state["entities"]["probe"] = default_registry().make("curtains", "living")
        self.assertAlmostEqual(8.0, V.rod_rating_kg(state, "probe") / __import__("house_sim.kernel.phys.props", fromlist=["x"]).vary(state, "probe", "hang"), places=6)

    def test_drops_are_the_kernels_fall_with_a_real_spill(self):
        h = House()
        h.add("old", "stopless_drawer", "kitchen", pos=[3, 0], mount_height_m=0.75, mounted=True)
        h.add("mug", "generic_item", "old", label="mug of tea", material="ceramic", liquid="tea", liquid_l=0.25, sealed=False,
              open=True, liquid_container=True, size_m=[0.09, 0.09, 0.1])
        e = h.env(room="kitchen", pos=(3, 1))
        ok(self, e.step("pull_handle", "old", force="yank"))
        self.assertTrue(ent(e, "mug")["fell"])
        self.assertGreater(stat(e, "phys_falls"), 0)
        self.assertGreater(stat(e, "phys_spills"), 0)                      # before v3.0: a spill counted, none made
        spills = [x for x, rec in e.world.s["entities"].items() if rec.get("category") == "spill"]
        self.assertTrue(spills)

    def test_rain_on_sill_items_follows_the_kernel_rain_law_when_it_is_active(self):
        from house_sim.kernel.phys import lawbook
        h = House()
        h.mech("win_living", {"kind": "sash_window", "open_frac": 0.6}, open=True, locked=False)
        h.add("sill", "window_sill", "living", pos=[2, 0], window="win_living", mount_height_m=0.9, size_m=[1.0, 0.18, 0.03])
        h.add("book", "book", "sill")
        h.c["events"] = [{"at_s": 10, "effect": "mechanisms.rain", "args": {"mm_h": 8.0}}]
        e = h.env(room="living", pos=(2, 3))
        with lawbook.forced(rain=True):
            ok(self, e.step("wait", seconds=3600))
            book = ent(e, "book")
            self.assertEqual(1.0, book.get("wetness"))
            self.assertGreater(C_num(book.get("moisture")), 0.0)          # the kernel's absorbency model
            self.assertEqual(bool(book.get("soaked")), bool(book.get("water_damaged")))
        self.assertTrue(e.world.s["rooms"]["living"].get("wet_cells"))    # the floor under the window: this domain's

    def test_heat_sources_never_go_onto_soft_surfaces_by_the_kernel_material_model(self):
        from house_sim.systems.mechanisms import skills as SK
        h = House()
        h.add("sofa1", "sofa", "living", pos=[6, 4])
        h.add("rug_stool", "table", "living", pos=[2, 5], material="wool")
        e = h.env()
        st = e.world.s
        self.assertTrue(SK.unsafe_for_heat(st, "sofa1"))
        self.assertTrue(SK.unsafe_for_heat(st, "rug_stool"))              # wool: kernel softness 0.85
        self.assertFalse(SK.unsafe_for_heat(st, "table"))                 # wood


class Tier1BodyAdoptionTests(unittest.TestCase):
    """TIER1_EMBODIMENT 12: forces clamped to the body's push / pull limits, the wrist's torque and the grip's cap from
    the body, commanded durations, the noise of a mallet."""

    def test_a_compact_robots_yank_does_not_break_a_child_latch(self):
        for profile, breaks in (("humanoid_v1", True), ("humanoid_compact", False)):
            h = House()
            h.add("sink_cab", "sink_base_cabinet", "kitchen", pos=[4, 0])
            h.mech("sink_cab", {"child_lock_kind": "magnetic"}, child_lock=True)
            e = _env_with(h, room="kitchen", pos=(4, 1), robot={"body_profile": profile})
            r = e.step("pull_handle", "sink_cab", force="yank")
            self.assertEqual(breaks, bool(mech(e, "sink_cab").get("child_lock_broken")), r["message"])

    def test_the_wrist_limit_is_the_bodys(self):
        for profile, opens in (("humanoid_v1", True), ("humanoid_compact", False)):
            h = House()
            h.add("jar", "screw_lid_jar", "counter", label="jam jar")
            h.mech("jar", {"kind": "screw_lid"}, {"stuck_nm": 3.8})        # 1.2 + 3.8 = 5 N m to break it free
            h.add("pad", "rubber_grip_pad", "counter")
            e = _env_with(h, room="kitchen", pos=(1, 1), robot={"body_profile": profile})
            ok(self, e.step("pick_up", "pad"))
            r = e.step("turn", "jar", direction="ccw", grip_aid="pad", grip_force_n=140.0)
            self.assertEqual(opens, bool(r["ok"]), r["message"])

    def test_commanded_holds_are_not_stretched_by_careful_speed(self):
        h = House()
        h.add("panel1", "control_panel_device", "kitchen", pos=[2, 2],
              panel={"buttons": {"start": {"effect": "none"}}})
        e = h.env(room="kitchen", pos=(2, 1))
        ok(self, e.step("set_speed", "careful"))
        t0 = e.world.s["time_s"]
        e.step("hold_press", "panel1", button="start", seconds=10.0)
        self.assertAlmostEqual(10.5, e.world.s["time_s"] - t0, delta=1.0)
        for verb in ("hold_press", "squeeze_trigger", "pull_handle", "push_handle", "turn", "move_lever"):     # B7
            self.assertEqual("commanded", ACTIONS[verb].timing, verb)

    def test_hammering_a_lid_home_is_loud_and_a_button_press_is_quiet(self):
        from house_sim.systems.mechanisms import controls as CT

        class _Ctx:
            def __init__(self, w, tool):
                self.w, self.args = w, {"tool": tool}
        h = House()
        h.add("mallet", "generic_item", "counter", tool_kind="rubber_mallet", label="rubber mallet")
        e = h.env(room="kitchen", pos=(1, 1))
        self.assertEqual(85.0, CT.press_noise_db(_Ctx(e.world, "mallet")))
        self.assertIsNone(CT.press_noise_db(_Ctx(e.world, None)))


class Tier1CameraAdoptionTests(unittest.TestCase):
    """TIER1_CAMERA 19: view blocks, visual occurrences, the doorway glimpse gated by the camera; room mode unchanged."""

    def test_view_blocks(self):
        from house_sim.kernel import sight
        h = House()
        for eid, type_name in (("sheer", "sheer_curtains"), ("fd", "french_door"), ("sd", "storm_door"),
                               ("gfc", "glass_front_cabinet"), ("ts", "touchscreen_device"), ("ap", "alarm_panel")):
            h.add(eid, type_name, "living", pos=[1, 1])
        st = h.env().world.s
        self.assertAlmostEqual(0.6, sight.view_props(st, "sheer")["porosity"])
        self.assertAlmostEqual(0.7, sight.view_props(st, "fd")["porosity"])
        self.assertAlmostEqual(0.8, sight.view_props(st, "sd")["porosity"])
        self.assertAlmostEqual(0.75, sight.view_props(st, "gfc")["porosity"])
        for eid in ("ts", "ap"):
            self.assertTrue(sight.view_props(st, eid)["glossy"])
            self.assertAlmostEqual(0.006, sight.text_m(st, eid, "display"))

    def test_visual_occurrences_log_exactly_as_before_in_room_mode(self):
        h = House()
        h.add("storm", "storm_door", "entry", pos=[1, 1])
        e = h.env(room="entry", pos=(2, 2))
        before = len(e.world.s.get("events", []) or [])
        from house_sim.systems.mechanisms import common as C
        C.visual(e.world, "slam", source="storm", size_m=1.0, target="storm")
        entry = e.world.s["events"][-1] if e.world.s.get("events") else None
        if entry is not None:
            self.assertEqual("slam", entry.get("kind"))
            self.assertNotIn("source", entry)
            self.assertNotIn("size_m", entry)
        self.assertNotIn("camera_log", e.world.s)

    def test_the_doorway_glimpse_needs_the_opening_in_frame_with_the_geometric_camera(self):
        h = House()
        h.person("adult_1", "hall", [1, 2])
        e = _env_with(h, room="bedroom", pos=(4, 2), robot={"facing_deg": 0.0})
        self.assertIn("through_doorways", ok(self, e.step("wait", seconds=1))["observation"])     # room camera
        e = _env_with(h, room="bedroom", pos=(4, 2), camera="geometric", robot={"facing_deg": 0.0})
        self.assertNotIn("through_doorways", e.step("wait", seconds=1)["observation"])           # facing away
        r = ok(self, e.step("look", "door_bed"))
        self.assertEqual("hall", r["observation"]["through_doorways"][0]["to"])


class Tier1SweepAdoptionTests(unittest.TestCase):
    """TIER1_SWEEP 22.3: notices, ask routes, exact idle probes, generation-2 furnishing."""

    def test_slams_and_the_garage_motor_are_notices(self):
        h = House()
        h.mech("door_bed", {"kind": "hinged_door"}, open=True)
        e = _env_with(h, room="hall", pos=(2, 2), notices=True, robot={"speed_mode": "fast"})
        ok(self, e.step("close", "door_bed"))
        kinds = [n["kind"] for n in e.world.s.get("notices", [])]
        self.assertIn("door_slam", kinds)
        h = House()
        h.add("btn", "garage_door_button", "garage", pos=[11, 5], controls_door="garage_door")
        h.mech("garage_door", {"kind": "overhead_door", "state": "open", "pos": 1.0}, open=True)
        e = _env_with(h, room="garage", pos=(10, 5), notices=True)
        ok(self, e.step("press", "btn"))
        self.assertIn("garage_door", [n["kind"] for n in e.world.s.get("notices", [])])

    def test_code_and_key_questions_reach_the_answerers(self):
        from house_sim.kernel import dialogue
        from house_sim.kernel.dialogue import Subject
        h = House()
        h.add("lock_1", "combination_padlock", "back", pos=[14, 1], label="shed padlock", _code="4721")
        st = h.env().world.s
        self.assertEqual(("mechanisms.code", "code:lock_1"),
                         dialogue.find_route(st, "adult_1", Subject("entity", "lock_1"), "how", "code", None))
        self.assertEqual(("mechanisms.key_location", "key_location:shed_door"),
                         dialogue.find_route(st, "adult_1", Subject("entity", "shed_door"), "how", "key", None))
        self.assertEqual(("mechanisms.code", "code:lock_1"),
                         dialogue.find_route(st, "adult_1", Subject("word", None, ("padlock", "code")), "how", None, None))
        self.assertEqual(("mechanisms.key_location", "key_location:shed_door"),
                         dialogue.find_route(st, "adult_1", Subject("word", None, ("shed", "key")), "where", None, None))
        self.assertEqual((None, None),
                         dialogue.find_route(st, "adult_1", Subject("word", None, ("front", "code")), "how", None, None))

    def test_idle_probes_are_exact(self):
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        quiet = House().env()
        ok(self, quiet.step("wait", seconds=60))
        self.assertTrue(plugins.SYSTEM_IDLE["mechanisms.tick"](quiet.world))
        self.assertTrue(plugins.SYSTEM_IDLE["mechanisms.slow"](quiet.world))

        def busy_house():
            h = House()
            h.mech("garage_entry", {"kind": "hinged_door", "release_at": -100.0}, open=True)   # a closer due to swing
            h.mech("win_living", {"kind": "sash_window", "open_frac": 0.6}, open=True, locked=False)
            h.add("sill", "window_sill", "living", pos=[2, 0], window="win_living", mount_height_m=0.9, size_m=[1.0, 0.18, 0.03])
            h.add("book", "book", "sill")
            h.add("monitor", "generic_item", "table", label="monitor", glare_from=["win_living"])
            h.c["events"] = [{"at_s": 600, "effect": "mechanisms.rain", "args": {"mm_h": 6.0}},
                             {"at_s": 1800, "effect": "mechanisms.wind_gust", "args": {"mps": 14.0}}]
            return h
        busy = busy_house().env()
        self.assertFalse(plugins.SYSTEM_IDLE["mechanisms.tick"](busy.world))
        self.assertFalse(plugins.SYSTEM_IDLE["mechanisms.slow"](busy.world))

        def run(probes):
            saved = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                e = busy_house().env(room="hall", pos=(1, 4))
                for seconds in (300, 900, 3600, 7200):
                    ok(self, e.step("wait", seconds=seconds))
                return digest(e.world.s)
            finally:
                plugins.IDLE_PROBES[0] = saved
        self.assertEqual(run(True), run(False))

    def test_generation_two_draws_anchoring_per_piece_and_fits_smart_locks_in_connected_homes(self):
        from house_sim.generator import generate_house
        from house_sim.systems.mechanisms import furnish as F
        reg = default_registry()
        drawn, anchored, smart = 0, 0, 0
        for seed in range(12):
            c2 = generate_house(seed, gen=2)
            for rec in c2["entities"].values():
                name = rec.get("type")
                chain = set([name] + (reg.ancestors(name) if name and reg.known(name) else []))
                if chain & set(F.TALL_STORAGE) and not rec.get("portable") and C_num((rec.get("size_m") or [0, 0, 0])[2]) >= F.TALL_STORAGE_MIN_M:
                    self.assertIn("anchored", rec, name)
                    drawn += 1
                    anchored += bool(rec["anchored"])
            if ((c2.get("household") or {}).get("profile") or {}).get("smart_home") == "connected":
                smart += sum(1 for rec in c2["entities"].values() if isinstance((rec.get("mech") or {}).get("smart"), dict))
        self.assertGreater(drawn, 8)
        self.assertLess(anchored, drawn)
        c1 = generate_house(0)
        self.assertFalse(any("anchored" in rec and rec.get("type") == "dresser" for rec in c1["entities"].values()))

    def test_every_spray_bottle_squeezes_like_a_trigger_sprayer(self):
        h = House()
        name = "spray_bottle" if default_registry().known("spray_bottle") else "trigger_bottle"   # cleaning's, else ours
        h.add("plain", name, "counter", liquid="water", liquid_l=0.4)
        h.c["entities"]["plain"].pop("trigger", None)          # a member of the spray_bottle group with no trigger data
        e = h.env(room="kitchen", pos=(1, 1))
        ok(self, e.step("pick_up", "plain"))
        r = ok(self, e.step("squeeze_trigger", "plain", squeezes=3))
        self.assertIn("Sprayed", r["message"])
        self.assertAlmostEqual(0.4 - 0.0033, ent(e, "plain")["liquid_l"], places=4)


def self_reg():
    return default_registry()


def C_num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def geometry_footprint(state, eid):
    from house_sim.kernel import geometry as G
    cells = G.footprint(state["entities"][eid])
    return cells[0] if cells else (None, None)


def geometry_anchor(state, eid):
    from house_sim.kernel import geometry as G
    return G.anchor(state, eid)


if __name__ == "__main__":
    unittest.main()
