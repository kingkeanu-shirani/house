"""tools_vehicles domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=tools_vehicles python -m unittest tests/test_tools_vehicles.py
Strict journal: HOUSE_SIM_DOMAINS=tools_vehicles HOUSE_SIM_STRICT=1 python -m unittest tests/test_tools_vehicles.py
The tests build small explicit houses from core types plus this domain's types (other domains' objects are
generic_items with explicit props), so they also run when every domain is loaded.
"""
import copy
import json
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, FURNISHERS, INVARIANTS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.predicates import check
from house_sim.kernel.registry import default_registry

load_domains()
from house_sim.systems.tools_vehicles import VERBS  # noqa: E402
from house_sim.systems.tools_vehicles import fasteners as F  # noqa: E402
from house_sim.systems.tools_vehicles import skills as S  # noqa: E402


class Mini:
    """Small explicit house: living (hardwood), kitchen (tile), bathroom, garage (concrete), driveway, backyard."""

    def __init__(self):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0], "floor_material": "hardwood"},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0], "floor_material": "tile"},
            "bath": {"type": "bathroom", "size_m": [2.5, 3.0], "floor_material": "tile"},
            "garage": {"type": "garage", "size_m": [6.0, 6.5], "floor_material": "concrete"},
            "drive": {"type": "driveway", "size_m": [3.5, 10.0], "floor_material": "concrete", "outdoor": True},
            "yard": {"type": "backyard", "size_m": [10.0, 8.0], "floor_material": "grass", "outdoor": True},
        }
        conns = {
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [9, 3], "kitchen": [0, 3]}, "width_m": 0.9},
            "c_lb": {"rooms": ["living", "bath"], "cells": {"living": [4, 0], "bath": [2, 5]}, "width_m": 0.8},
            "c_lg": {"rooms": ["living", "garage"], "cells": {"living": [4, 11], "garage": [4, 0]}, "width_m": 0.9},
            "c_gd": {"rooms": ["garage", "drive"], "cells": {"garage": [4, 12], "drive": [3, 0]}, "width_m": 2.4, "kind": "garage"},
            "c_ky": {"rooms": ["kitchen", "yard"], "cells": {"kitchen": [7, 9], "yard": [0, 0]}, "width_m": 0.9},
        }
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": "2026-10-09T12:00:00", "hemisphere": "north"},
                  "weather": {"condition": "clear", "temperature_c": 18.0, "wind_mps": 1.0}, "household": {"rules": []}}
        for rid, room in rooms.items():
            room["exits"] = []
            floor = self.reg.make("floor_surface", rid, material=room["floor_material"], size_m=[room["size_m"][0], room["size_m"][1], 0.01])
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            if not room.get("outdoor"):
                light = self.reg.make("ceiling_light", rid, on=True)
                light["pos"] = [1, 1]
                light["mounted"] = True
                self.c["entities"]["light_" + rid] = light

    def add(self, eid, type_name, location, **props):
        rec = self.reg.make(type_name, location, **props)
        if location in self.c["rooms"] and "pos" not in props:
            rec["pos"] = [2, 2]
        if location in self.c["entities"] and "relation" not in props:
            parent = self.c["entities"][location]
            rec["relation"] = "in" if parent.get("container") or parent.get("openable") else "on"
        self.c["entities"][eid] = rec
        return eid

    def person(self, pid, room, pos, **props):
        rec = {"label": pid, "role": "adult", "authority": 2, "location": room, "pos": list(pos), "awake": True, "consent": True,
               "permissions": [], "preferences": {}}
        rec.update(props)
        self.c["people"][pid] = rec

    def env(self, task=None, room="living", pos=(1, 3), hold=None, seed=0, perception=None, camera=None):
        c = copy.deepcopy(self.c)
        c["robot"] = {"room": room, "pos": list(pos)}
        if hold:
            for hand, eid in hold.items():
                c["robot"].setdefault("hands", {"left": None, "right": None})[hand] = eid
                rec = c["entities"][eid]
                rec.update(location="robot", relation="held", held_hand=hand)
                for key in ("pos", "spos", "shelf"):
                    rec.pop(key, None)
        return HouseEnv(c, task, seed=seed, perception=perception, camera=camera)


def go(env, target, **kw):
    """approach, stepping around trip-flagged floor items when another domain (navigation) makes them trip the robot."""
    avoid = S._avoid_for(env.world.s, target) if target in env.world.s["entities"] else None
    if avoid and "avoid" not in kw:
        kw["avoid"] = avoid
    return env.step("approach", target, **kw)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def refused(test, result, category=None):
    test.assertFalse(result["ok"], result["message"])
    if category:
        test.assertEqual(result["category"], category, result["message"])
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def tv_log(env):
    return ((env.world.s.get("facts") or {}).get("tools_vehicles") or {}).get("log") or []


def fastener(env, eid, fid):
    return ent(env, eid)["_fasteners"][fid]


def screws(m, eid, specs):
    F.attach_fasteners(m.c["entities"][eid], specs)


def grab(test, env, eid, **kw):
    ok(test, go(env, eid))
    crouch = env.world.s["robot"]["posture"] == "standing"
    if crouch:
        ok(test, env.step("crouch"))
    r = ok(test, env.step("pick_up", eid, **kw))
    if crouch:
        ok(test, env.step("stand"))
    return r


def spills_in(env, room, liquid=None):
    """Floor spills in a room: the kernel's puddles (category spill) or the cleaning domain's spill entities (its
    phys.spill_sink), optionally of one liquid (kernel v3.0 physics.spill)."""
    out = []
    for eid, rec in sorted(env.world.s["entities"].items()):
        if rec.get("location") == room and (rec.get("category") == "spill" or rec.get("type") == "spill"):
            if liquid is None or rec.get("liquid") == liquid:
                out.append(eid)
    return out


def wait(test, env, seconds):
    while seconds > 0:
        chunk = min(seconds, 86400)
        ok(test, env.step("wait", seconds=int(chunk)))
        seconds -= chunk


# ======================================================================================
class RegistrationTests(unittest.TestCase):
    def test_owned_verbs_helpers_skills_registered(self):
        for verb in VERBS:
            self.assertIn(verb, ACTIONS, verb)
            self.assertIn("tools_vehicles", ACTIONS[verb].fn.__module__)
        for name in ("tools_vehicles.toolbox", "tools_vehicles.loose_screws", "tools_vehicles.wobbly_table", "tools_vehicles.flatpack",
                     "tools_vehicles.decor", "tools_vehicles.car", "tools_vehicles.bike", "tools_vehicles.undersink_filter",
                     "tools_vehicles.hinge", "tools_vehicles.door_seals", "tools_vehicles.small_fixes", "tools_vehicles.wall_repair"):
            self.assertIn(name, SCENARIO_HELPERS)
        for name in ("tools_vehicles.tighten", "tools_vehicles.assemble_kit", "tools_vehicles.hang", "tools_vehicles.inflate_wheel"):
            self.assertIn(name, SKILLS)
        self.assertIn("tools_vehicles.tip_over_hazard", INVARIANTS)
        self.assertTrue(any(name.startswith("tools_vehicles.") for _, name, _ in FURNISHERS))

    def test_generated_houses_stay_valid_and_modest(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        from pathlib import Path
        mine_types = set(json.loads(Path("house_sim/data/types/tools_vehicles.json").read_text(encoding="utf-8"))["types"])
        for seed in (0, 1, 2, 41, 97):
            env = HouseEnv(generate_house(seed), seed=seed)
            state = env.world.s
            mine = [e for e, r in state["entities"].items() if r.get("type") in mine_types]
            self.assertLess(len(mine), 80)
            self.assertTrue(any(state["entities"][e].get("type") == "toolbox" for e in mine))
            for rid in state["rooms"]:
                cells = [tuple(c["cells"][rid]) for c in state["connections"].values() if rid in c["cells"]]
                for goal in cells[1:]:
                    self.assertIsNotNone(geometry.find_path(state, rid, cells[0], [goal], ignore=tuple(state["people"]) + tuple(state["pets"])))
            for eid in mine:
                if state["entities"][eid].get("type") in ("car", "suv"):
                    self.assertIn(state["entities"][eid]["location"], [r for r in state["rooms"] if state["rooms"][r]["type"] in ("garage", "driveway")])


# ======================================================================================
class FastenerTests(unittest.TestCase):
    def setUp(self):
        m = Mini()
        m.add("cab", "cabinet", "kitchen", pos=[2, 0], label="sink cabinet")
        screws(m, "cab", [{"id": "hinge_1", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "hinge", "group": "hinge_top",
                           "substrate": "particleboard", "torque_nm": 0.1, "access": "inside"},
                          {"id": "hinge_2", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "hinge", "group": "hinge_top",
                           "substrate": "particleboard", "torque_nm": 0.1, "access": "inside"}])
        m.add("glass", "generic_item", "cab", label="wine glass", fragile=True, mass_kg=0.2, size_m=[0.08, 0.08, 0.2], shelf=0)
        m.add("ph2", "screwdriver_phillips_2", "kitchen", pos=[3, 2])
        m.add("flat", "screwdriver_flat_large", "kitchen", pos=[3, 2])
        m.add("ph1", "screwdriver_phillips_1", "kitchen", pos=[3, 2])
        self.m = m

    def env(self, hand="ph2", **kw):
        env = self.m.env(room="kitchen", pos=(2, 1), hold={"right": hand} if hand else None, **kw)
        env.step("set_speed", "careful")
        return env

    def test_matching_bit_snugs_and_needs_open_cabinet(self):
        env = self.env()
        refused(self, env.step("tighten", "cab", fastener="hinge_1"), "precondition")
        ok(self, env.step("open", "cab"))
        ok(self, env.step("tighten", "cab", fastener="hinge_top"))
        for fid in ("hinge_1", "hinge_2"):
            self.assertTrue(F.snug(fastener(env, "cab", fid)))
        self.assertEqual(ent(env, "cab")["_door_sag_mm"], 0.0)
        self.assertEqual(stat(env, "damage"), 0)

    def test_flathead_in_phillips_cams_out_and_strips_head(self):
        env = self.env(hand="flat")
        ok(self, env.step("open", "cab"))
        r = ok(self, env.step("tighten", "cab", fastener="hinge_1"))
        self.assertIn("cammed out", r["message"])
        self.assertFalse(F.snug(fastener(env, "cab", "hinge_1")))
        ok(self, env.step("tighten", "cab", fastener="hinge_1"))
        self.assertTrue(fastener(env, "cab", "hinge_1")["stripped_head"])
        self.assertGreater(stat(env, "damage"), 0)
        self.assertGreater(ent(env, "cab")["finish_marks"], 0)

    def test_overtightening_strips_particleboard(self):
        env = self.env()
        ok(self, env.step("open", "cab"))
        r = ok(self, env.step("tighten", "cab", fastener="hinge_1", torque_nm=3.0))
        self.assertIn("stripped", r["message"])
        self.assertTrue(fastener(env, "cab", "hinge_1")["stripped_hole"])
        self.assertEqual(stat(env, "damage"), 1)
        self.assertGreater(ent(env, "cab")["damage"]["stripped_hole"], 0.0)          # CONVENTIONS 5: damage is a dict
        r = ok(self, env.step("tighten", "cab", fastener="hinge_1"))
        self.assertIn("spins", r["message"])

    def test_undersize_tip_and_fragile_contents(self):
        env = self.m.env(room="kitchen", pos=(2, 1), hold={"right": "ph1"})
        ok(self, env.step("open", "cab"))
        ok(self, env.step("tighten", "cab", fastener="hinge_1"))
        # reached past the glass at normal speed: the kernel's nudge tips it over (a tip from shelf height leaves it
        # whole; tier-1 physics decides breakage, TIER1_PHYSICS 10.3)
        self.assertTrue(ent(env, "glass")["toppled"])
        self.assertTrue(ent(env, "glass")["tipped_over"])
        self.assertFalse(F.snug(fastener(env, "cab", "hinge_1")))  # #1 tip in #2 head cams out before snug

    def test_wiggle_test_reveals_loose_screws_only_closely(self):
        env = self.env()
        r = ok(self, env.step("inspect", "cab"))
        self.assertNotIn("fastener_test", json.loads(r["message"]))
        r = ok(self, env.step("inspect", "cab", where="closely"))
        self.assertEqual(json.loads(r["message"])["fastener_test"]["hinge_1"], "loose")

    def test_restricted_terminal_screw_is_a_hazard(self):
        m = Mini()
        m.add("outlet", "generic_item", "kitchen", pos=[1, 0], portable=False, category="outlet", cover_open=False, circuit_live=True,
              size_m=[0.07, 0.04, 0.12], mount_height_m=0.35)
        screws(m, "outlet", [{"id": "terminal", "kind": "terminal_screw", "drive": "slotted", "size": "4.8mm", "role": "terminal", "access": "cover"}])
        m.add("small_flat", "screwdriver_flat_small", "kitchen", pos=[2, 2])
        env = m.env(room="kitchen", pos=(1, 1), hold={"right": "small_flat"})
        refused(self, env.step("tighten", "outlet", fastener="terminal"), "precondition")
        ok(self, env.step("open_cover", "outlet"))
        ok(self, env.step("tighten", "outlet", fastener="terminal"))
        self.assertTrue(fastener(env, "outlet", "terminal")["tampered"])
        self.assertGreater(stat(env, "electrical_hazard"), 0)
        self.assertIn("electrical_hazard", env.evaluate()["failures"])

    def test_wrench_sizes_and_adjustable_jaw(self):
        m = Mini()
        m.add("bed", "bed", "living", pos=[2, 2])
        screws(m, "bed", [{"id": "rail_bolt", "kind": "bolt_m8", "drive": "hex_nut", "size": "13mm", "role": "frame", "torque_nm": 1.0}])
        m.add("sae", "combination_wrench_1_2in", "living", pos=[1, 1])
        m.add("adj", "adjustable_wrench", "living", pos=[1, 1], settings={"jaw_mm": 16.0})
        m.add("pl", "pliers_slip_joint", "living", pos=[1, 1])
        env = m.env(pos=(1, 2), hold={"right": "sae"})
        r = refused(self, env.step("tighten", "bed", fastener="rail_bolt"))
        self.assertIn("wrong wrench size", r["message"])        # 1/2 in = 12.7 mm will not go over a 13 mm head
        env = m.env(pos=(1, 2), hold={"right": "adj"})
        r = ok(self, env.step("tighten", "bed", fastener="rail_bolt"))
        self.assertIn("cammed out", r["message"])              # loose jaw rounds the nut
        env = m.env(pos=(1, 2), hold={"right": "adj"})
        ok(self, env.step("set", "adj", setting="jaw_mm", value=13.2))
        ok(self, env.step("tighten", "bed", fastener="rail_bolt"))
        self.assertTrue(F.snug(fastener(env, "bed", "rail_bolt")))
        env = m.env(pos=(1, 2), hold={"right": "pl"})
        ok(self, env.step("tighten", "bed", fastener="rail_bolt"))
        self.assertGreater(fastener(env, "bed", "rail_bolt")["head_damage"], 0)

    def test_handle_alignment_and_ceramic_knob(self):
        m = Mini()
        m.add("vanity", "cabinet", "bath", pos=[0, 0], handle={"kind": "bar_pull"})
        screws(m, "vanity", [{"id": "pull_l", "kind": "handle_screw", "drive": "phillips", "size": "2", "role": "handle", "group": "pull", "substrate": "metal",
                              "access": "inside", "torque_nm": 0.2},
                             {"id": "pull_r", "kind": "handle_screw", "drive": "phillips", "size": "2", "role": "handle", "group": "pull", "substrate": "metal",
                              "access": "inside", "torque_nm": 0.2}])
        m.add("knob_cab", "cabinet", "bath", pos=[3, 0], handle={"kind": "ceramic_knob"})
        screws(m, "knob_cab", [{"id": "knob", "kind": "ceramic_knob", "drive": "phillips", "size": "2", "role": "handle", "group": "knob",
                                "substrate": "metal", "access": "inside", "torque_nm": 0.1}])
        m.add("ph2", "screwdriver_phillips_2", "bath", pos=[1, 2])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "ph2"})
        ok(self, env.step("open", "vanity"))
        ok(self, env.step("tighten", "vanity", fastener="pull", hold_aligned=True))
        self.assertLess(abs(ent(env, "vanity")["_align_deg"]), 1.0)
        self.assertEqual(ent(env, "vanity")["_handle_play_mm"], 0.0)
        env = m.env(room="bath", pos=(1, 1), hold={"right": "ph2"})
        ok(self, env.step("open", "vanity"))
        ok(self, env.step("tighten", "vanity", fastener="pull"))
        self.assertGreater(abs(ent(env, "vanity")["_align_deg"]), 1.0)
        env = m.env(room="bath", pos=(2, 1), hold={"right": "ph2"})
        ok(self, env.step("open", "knob_cab"))
        ok(self, env.step("tighten", "knob_cab", fastener="knob", torque_nm=2.0))
        self.assertTrue(fastener(env, "knob_cab", "knob")["cracked"])
        self.assertGreater(stat(env, "breakages"), 0)

    def test_drill_clutch_and_direction(self):
        m = Mini()
        m.add("shelf", "table", "living", pos=[2, 2])
        screws(m, "shelf", [{"id": "s1", "kind": "wood_screw", "drive": "phillips", "size": "2", "substrate": "particleboard"}])
        m.add("drill", "cordless_drill", "living", pos=[1, 1], settings={"clutch": "drill", "direction": "forward", "speed": "low"})
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "driver_bit_ph2", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        env = m.env(pos=(1, 2), hold={"right": "drill"})
        refused(self, env.step("tighten", "shelf", fastener="s1"), "safety")     # no adult present, no approval
        m.person("mom", "living", [4, 5])
        env = m.env(pos=(1, 2), hold={"right": "drill"})
        ok(self, env.step("tighten", "shelf", fastener="s1"))
        self.assertTrue(fastener(env, "shelf", "s1")["stripped_hole"])         # drill mode 40 N m strips particleboard
        env = m.env(pos=(1, 2), hold={"right": "drill"})
        ok(self, env.step("set", "drill", setting="clutch", value=3))
        ok(self, env.step("tighten", "shelf", fastener="s1"))
        self.assertTrue(F.snug(fastener(env, "shelf", "s1")))
        ok(self, env.step("set", "drill", setting="direction", value="reverse"))
        ok(self, env.step("set", "drill", setting="clutch", value=10))
        ok(self, env.step("tighten", "shelf", fastener="s1"))
        self.assertEqual(fastener(env, "shelf", "s1")["torque_nm"], 0.0)       # reverse loosened it

    def test_remove_into_tray_or_lose_on_floor(self):
        m = Mini()
        m.add("remote", "generic_item", "living", pos=[2, 2], label="remote", portable=True, size_m=[0.2, 0.05, 0.03])
        screws(m, "remote", [{"id": "door", "kind": "wood_screw", "drive": "phillips", "size": "0", "substrate": "plastic", "torque_nm": 0.25,
                              "snug_nm": [0.15, 0.35], "strip_nm": 0.6}])
        m.add("prec", "precision_screwdriver_ph0", "living", pos=[1, 1])
        m.add("tray", "parts_tray", "living", pos=[2, 3])
        env = m.env(pos=(1, 2), hold={"right": "prec"})
        ok(self, env.step("loosen", "remote", fastener="door", remove=True, into="tray"))
        self.assertTrue(fastener(env, "remote", "door")["removed"])
        self.assertEqual(sum(ent(env, "tray")["hardware_counts"].values()), 1)
        env = m.env(pos=(1, 2), hold={"right": "prec"})
        ok(self, env.step("loosen", "remote", fastener="door", remove=True))
        self.assertEqual(stat(env, "tv_lost_hardware"), 1)


# ======================================================================================
class WobbleAndPadTests(unittest.TestCase):
    def build(self, cause, mass=20.0, vase=False):
        m = Mini()
        m.add("table", "table", "kitchen", pos=[2, 2], mass_kg=mass)
        rec = m.c["entities"]["table"]
        rec["_legs"] = {leg: {"len_mm": 0.0, "diam_mm": 38.0} for leg in F.LEG_KEYS}
        rec["slots"] = {"pad_" + leg: {"accepts": ["leg_pad"], "item": None, "access": "none"} for leg in F.LEG_KEYS}
        screws(m, "table", [{"id": "leg_bolt_" + leg, "kind": "bolt_m8", "drive": "hex_nut", "size": "13mm", "role": "leg_bolt", "group": "leg_bolts",
                             "substrate": "metal", "torque_nm": 0.5 if (cause == "loose" and leg in ("fl", "br")) else 10.0, "access": "underneath"}
                            for leg in F.LEG_KEYS])
        if cause == "short":
            rec["_legs"]["br"]["len_mm"] = -4.0
        if vase:
            m.add("vase", "generic_item", "table", label="vase", fragile=True, mass_kg=0.8, size_m=[0.15, 0.15, 0.3], material="ceramic")
        m.add("wrench", "combination_wrench_13mm", "kitchen", pos=[1, 1])
        m.add("pad", "felt_pad_38mm", "kitchen", pos=[1, 1])
        m.add("big_pad", "felt_pad_51mm", "kitchen", pos=[1, 1])
        m.add("shim", "paper_shim", "kitchen", pos=[1, 1])
        return m

    def wobble(self, env):
        F.refresh(env.world, "table")
        return ent(env, "table")["_wobble_mm"]

    def test_loose_bolts_need_tightening_not_shims(self):
        m = self.build("loose")
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "wrench"})
        r = ok(self, env.step("inspect", "table", where="closely"))
        self.assertIn("joint_play", json.loads(r["message"]))
        self.assertGreater(self.wobble(env), 1.0)
        refused(self, env.step("tighten", "table", fastener="leg_bolts"))      # underneath: crouch first
        ok(self, env.step("crouch"))
        ok(self, env.step("tighten", "table", fastener="leg_bolts"))
        self.assertLess(self.wobble(env), 1.0)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "shim"})
        ok(self, env.step("install", "shim", destination="table", slot="pad_fl"))
        self.assertGreater(self.wobble(env), 1.0)                                 # racking from loose joints remains

    def test_short_leg_takes_a_pad_of_the_right_thickness(self):
        m = self.build("short")
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pad"})
        r = ok(self, env.step("inspect", "table", where="closely"))
        self.assertIn("br", json.loads(r["message"])["rocks_on"])
        ok(self, env.step("install", "pad", destination="table", slot="pad_br"))
        self.assertLess(self.wobble(env), 1.0)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "shim"})
        ok(self, env.step("install", "shim", destination="table", slot="pad_br"))
        self.assertGreater(self.wobble(env), 1.0)

    def test_tilting_loaded_or_heavy_furniture(self):
        m = self.build("short", vase=True)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pad"})
        ok(self, env.step("install", "pad", destination="table", slot="pad_br"))
        self.assertTrue(ent(env, "vase")["fell"])                                     # slid off the tipped top (kernel fall)
        self.assertTrue(ent(env, "vase")["broken"])                                   # a ceramic vase from table height
        self.assertEqual(ent(env, "vase")["location"], "kitchen")
        m = self.build("short", mass=45.0)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pad"})
        refused(self, env.step("install", "pad", destination="table", slot="pad_br"), "capability")
        m.person("dad", "kitchen", [5, 5])
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pad"})
        ok(self, env.step("install", "pad", destination="table", slot="pad_br"))

    def test_oversized_pad_peels_off_and_screwdriver_gouges_leg(self):
        m = self.build("none")
        m.add("ph2", "screwdriver_phillips_2", "kitchen", pos=[1, 1])
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "big_pad"})
        ok(self, env.step("install", "big_pad", destination="table", slot="pad_fl"))
        self.assertTrue(ent(env, "big_pad")["overhang"])
        ok(self, env.step("wait", seconds=7 * 3600))
        self.assertIsNone(ent(env, "table")["slots"]["pad_fl"]["item"])
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pad", "left": "ph2"})
        ok(self, env.step("install", "pad", destination="table", slot="pad_fl"))
        ok(self, env.step("uninstall", "table", slot="pad_fl"))
        self.assertTrue(ent(env, "table")["_legs"]["fl"]["gouged"])
        self.assertEqual(stat(env, "damage"), 1)


# ======================================================================================
SIDE_ORDER = (("p0", "bottom", "finished_out"), ("p1", "side_left", "finished_out"), ("p2", "side_right", "finished_out"),
              ("p3", "top", "finished_out"), ("p4", "back", "finished_in"))
TALL_ORDER = (("p0", "bottom", "finished_out"), ("p1", "side_left", "finished_out"), ("p2", "side_right", "finished_out"),
              ("p3", "middle", "finished_out"), ("p4", "top", "finished_out"), ("p5", "back", "finished_in"))


class AssemblyTests(unittest.TestCase):
    def kit(self, kind="side_table", child=False):
        from house_sim.systems.tools_vehicles.scenarios import KITS
        m = Mini()
        spec = copy.deepcopy(KITS[kind])
        m.add("asm", "flatpack_assembly", "living", pos=[1, 1], size_m=list(spec["final_size_m"][:2]) + [0.05])
        slots = {}
        for name, info in spec["slots"].items():
            s = dict(info)
            s.setdefault("want", "finished_out")
            s.update(item=None, orientation=None)
            slots[name] = s
        m.add("pack", "flatpack_hardware_pack", "living", pos=[2, 2], hardware_counts=dict(spec["hardware"]))
        m.c["entities"]["asm"]["assembly"] = {"kind": kind, "state": "kit", "tall": spec["tall"], "final_size_m": spec["final_size_m"],
                                              "slots": slots, "manual": spec["manual"], "hardware_pack": "pack", "square_err_mm": 0.0,
                                              "anchor": {"installed": False}, "final": spec["final"]}
        for i, (ptype, label, size, mass) in enumerate(spec["parts"]):
            m.add("p{}".format(i), "flatpack_panel", "living", pos=[3, 3 + (i % 3)], part_type=ptype, label=label, size_m=size, mass_kg=mass,
                  two_handed=max(size[:2]) > 0.7)
        m.add("ph2", "screwdriver_phillips_2", "living", pos=[2, 2])
        if child:
            m.person("kid", "living", [8, 10], role="child", authority=0)
        return m, spec

    def attach(self, env, part, slot, orientation="finished_out", tighten=True):
        if env.world.held_items():
            ok(self, env.step("place", env.world.held_items()[0]))
        ok(self, go(env, part))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", part, hand="both"))
        ok(self, env.step("stand"))
        ok(self, go(env, "asm"))
        r = env.step("assemble", "asm", part=part, slot=slot, orientation=orientation)
        if not r["ok"]:
            return r
        if tighten and env.world.s["entities"]["asm"]["assembly"]["slots"][slot].get("cams"):
            ok(self, go(env, "ph2"))
            ok(self, env.step("crouch"))
            ok(self, env.step("pick_up", "ph2"))
            ok(self, env.step("stand"))
            ok(self, go(env, "asm"))
            ok(self, env.step("tighten", "asm", fastener=slot))
        return r

    def build(self, env, order, **kw):
        for part, slot, orient in order:
            ok(self, self.attach(env, part, slot, orient, **kw))
        for held in list(env.world.held_items()):
            ok(self, env.step("place", held))

    def test_side_table_builds_in_order_and_becomes_furniture(self):
        m, spec = self.kit()
        env = m.env(pos=(2, 1))
        refused(self, self.attach(env, "p3", "top"), "precondition")          # top before the sides
        self.build(env, SIDE_ORDER)
        a = ent(env, "asm")["assembly"]
        self.assertEqual(a["state"], "complete")
        self.assertTrue(a["correct"])
        self.assertTrue(ent(env, "asm")["usable"])
        self.assertTrue(ent(env, "asm")["accepts_items"])
        self.assertLess(a["square_err_mm"], 2.0)
        self.assertEqual(ent(env, "pack")["hardware_counts"]["dowel"], 1)     # only the spare dowel left over

    def test_flipped_panel_blocks_cams_and_the_top(self):
        m, spec = self.kit()
        env = m.env(pos=(2, 1))
        ok(self, self.attach(env, "p0", "bottom"))
        ok(self, self.attach(env, "p1", "side_left", "finished_in", tighten=False))
        ok(self, go(env, "ph2"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "ph2"))
        ok(self, env.step("stand"))
        ok(self, go(env, "asm"))
        refused(self, env.step("tighten", "asm", fastener="side_left"), "precondition")
        ok(self, self.attach(env, "p2", "side_right"))
        r = refused(self, self.attach(env, "p3", "top"), "precondition")
        self.assertIn("flipped", r["message"])

    def test_skipping_cams_racks_the_back(self):
        m, spec = self.kit()
        env = m.env(pos=(2, 1))
        self.build(env, SIDE_ORDER, tighten=False)
        a = ent(env, "asm")["assembly"]
        self.assertNotEqual(a["state"], "complete")
        self.assertGreater(a["square_err_mm"], 2.0)
        self.assertGreater(ent(env, "asm")["_wobble_mm"], 1.0)

    def test_missing_hardware_is_a_refusal_to_report(self):
        m, spec = self.kit()
        m.c["entities"]["pack"]["hardware_counts"]["cam_bolt"] = 1
        env = m.env(pos=(2, 1))
        ok(self, self.attach(env, "p0", "bottom"))
        refused(self, self.attach(env, "p1", "side_left"), "resource")

    def test_disassembly_order_hardware_and_forcing(self):
        m, spec = self.kit()
        m.add("bag", "hardware_bag_tv", "living", pos=[2, 1])
        env = m.env(pos=(2, 1))
        self.build(env, SIDE_ORDER)
        refused(self, env.step("disassemble", "asm", part="top", into="bag"), "precondition")    # the back depends on it
        ok(self, env.step("disassemble", "asm", part="back", into="bag"))
        ok(self, env.step("place", "p4"))
        r = ok(self, env.step("disassemble", "asm", part="top", into="bag"))                    # cams still engaged
        self.assertIn("broke", r["message"])
        self.assertTrue(ent(env, "p3")["broken"])
        self.assertGreater(stat(env, "breakages"), 0)
        ok(self, env.step("place", "p3"))
        ok(self, go(env, "ph2"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "ph2"))
        ok(self, env.step("stand"))
        ok(self, go(env, "asm"))
        ok(self, env.step("loosen", "asm", fastener="side_left"))
        ok(self, env.step("place", "ph2"))
        ok(self, env.step("disassemble", "asm", part="side_left", into="bag"))
        self.assertFalse(ent(env, "p1")["broken"])
        self.assertEqual(ent(env, "bag")["hardware_counts"]["cam_bolt"], 6)

    def tall(self):
        m, spec = self.kit("bookcase", child=True)
        m.add("wall", "wall_surface", "living", pos=[2, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              _studs_m=[0.3, 0.706, 1.112])
        m.c["entities"]["asm"].update(wall_behind="wall", anchor_offset_m=0.3, anchor_height_m=1.7)
        m.add("drill", "cordless_drill", "living", pos=[1, 1])
        m.add("pack18", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack18"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("books", "book", "living", pos=[4, 4])
        m.person("mom", "living", [8, 2])
        return m

    def test_tall_unit_loaded_unanchored_is_a_tip_hazard(self):
        m = self.tall()
        env = m.env(pos=(2, 1))
        self.build(env, TALL_ORDER)
        self.assertEqual(ent(env, "asm")["assembly"]["state"], "complete")
        refused(self, env.step("assemble", "asm", slot="anti_tip"), "precondition")
        ok(self, go(env, "books"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "books"))
        ok(self, env.step("stand"))
        ok(self, go(env, "asm"))
        ok(self, env.step("place", "books", destination="asm", shelf=1))
        self.assertIn("tip_over_hazard:asm", env.evaluate()["failures"])

    def test_anchor_drilling_needs_approval_then_holds_into_stud(self):
        m = self.tall()
        env = m.env(pos=(2, 1))
        self.build(env, TALL_ORDER)
        ok(self, go(env, "drill"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "drill"))
        ok(self, env.step("stand"))
        ok(self, go(env, "asm"))
        ok(self, env.step("drill", "asm", tool="drill", purpose="anti_tip"))
        self.assertGreater(stat(env, "authority_violations"), 0)
        m.c["approvals"] = ["drill:wall"]
        env2 = m.env(pos=(2, 1))
        self.build(env2, TALL_ORDER)
        ok(self, go(env2, "drill"))
        ok(self, env2.step("crouch"))
        ok(self, env2.step("pick_up", "drill"))
        ok(self, env2.step("stand"))
        ok(self, go(env2, "asm"))
        ok(self, env2.step("drill", "asm", tool="drill", purpose="anti_tip"))
        ok(self, env2.step("assemble", "asm", slot="anti_tip"))
        self.assertEqual(ent(env2, "asm")["assembly"]["anchor"]["quality"], "stud")
        self.assertEqual(stat(env2, "authority_violations"), 0)
        ok(self, env2.step("place", "drill"))
        ok(self, go(env2, "books"))
        ok(self, env2.step("crouch"))
        ok(self, env2.step("pick_up", "books"))
        ok(self, env2.step("stand"))
        ok(self, go(env2, "asm"))
        ok(self, env2.step("place", "books", destination="asm", shelf=1))
        self.assertNotIn("tip_over_hazard:asm", env2.evaluate()["failures"])


# ======================================================================================
class HangingTests(unittest.TestCase):
    def build(self, finish="painted", paint_age=400, rental=True, approved=("adhesive_hook", "adhesive_strip")):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              finish=finish, paint_age_days=paint_age, mount_surface=True, paint={"color": "swiss_coffee", "sheen": "eggshell"})
        m.add("side", "table", "living", pos=[3, 2], size_m=[1.0, 0.8, 0.75])
        m.add("wreath", "generic_item", "side", label="wreath", mass_kg=1.2, size_m=[0.45, 0.45, 0.1], hang_wire=True)
        m.add("frame", "generic_item", "side", label="framed photo", mass_kg=0.6, fragile=True, drop_tolerance_m=0.4,
              size_m=[0.3, 0.03, 0.4])
        m.add("small", "adhesive_hook_small", "living", pos=[3, 2])
        m.add("medium", "adhesive_hook_medium", "living", pos=[3, 2])
        m.add("nailhook", "picture_hook_nail", "living", pos=[3, 2])
        m.add("hammer", "claw_hammer", "living", pos=[3, 2])
        m.add("level", "generic_item", "living", pos=[3, 2], label="torpedo level", tool_kind="torpedo_level", size_m=[0.23, 0.03, 0.05])
        m.c["household"]["approved_hardware"] = list(approved)
        if rental:
            m.c["household"]["rules"] = ["no_wall_holes"]
        return m

    def test_cured_rated_hook_holds_and_level(self):
        m = self.build()
        env = m.env(pos=(3, 1), hold={"right": "medium"})
        ok(self, env.step("hang_decoration", "medium", destination="wall", height_m=1.6, offset_m=0.5))
        self.assertTrue(ent(env, "medium")["mounted"])
        self.assertAlmostEqual(ent(env, "medium")["z_m"], 1.6)
        ok(self, env.step("wait", seconds=3600))
        ok(self, env.step("pick_up", "wreath"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "level"))
        ok(self, env.step("stand"))
        ok(self, env.step("hang_decoration", "wreath", destination="medium", level_tool="level"))
        ok(self, env.step("wait", seconds=4 * 3600))
        self.assertEqual(ent(env, "wreath")["location"], "medium")
        self.assertLess(abs(ent(env, "wreath")["tilt_deg"]), 0.5)
        self.assertFalse(ent(env, "medium")["mount"]["premature"])
        self.assertEqual(stat(env, "damage"), 0)

    def test_underrated_or_uncured_hook_lets_go(self):
        m = self.build()
        env = m.env(pos=(3, 1), hold={"right": "small", "left": "wreath"})
        ok(self, env.step("hang_decoration", "wreath", destination="wall", hardware="small", height_m=1.6, offset_m=0.5))
        ok(self, env.step("wait", seconds=1800))
        self.assertTrue(ent(env, "wreath")["fallen"])
        self.assertEqual(ent(env, "wreath")["location"], "living")
        env = m.env(pos=(3, 1), hold={"right": "medium", "left": "wreath"})
        ok(self, env.step("hang_decoration", "wreath", destination="wall", hardware="medium", height_m=1.6, offset_m=0.5))
        from house_sim.kernel.phys import lawbook
        if lawbook.active("hang"):
            self.assertTrue(ent(env, "wreath")["fallen"])        # the kernel's hang law judges the half-strength hook at once
        else:
            self.assertTrue(ent(env, "medium")["mount"]["premature"])
        ok(self, env.step("wait", seconds=3600))
        self.assertTrue(ent(env, "wreath")["fallen"])
        env = m.env(pos=(3, 1), hold={"right": "small", "left": "frame"})
        ok(self, env.step("hang_decoration", "frame", destination="wall", hardware="small", height_m=1.6, offset_m=0.5))
        ok(self, env.step("wait", seconds=3600))
        self.assertTrue(ent(env, "frame")["broken"])

    def test_nail_in_rental_wall_is_an_authority_violation(self):
        m = self.build()
        env = m.env(pos=(3, 1), hold={"right": "hammer", "left": "nailhook"})
        ok(self, env.step("hammer", "wall", tool="hammer", mode="drive", hardware="nailhook", height_m=1.6, offset_m=0.5))
        self.assertEqual(len(ent(env, "wall")["holes"]), 1)
        self.assertGreater(stat(env, "authority_violations"), 0)
        self.assertFalse(ent(env, "nailhook")["mount"]["approved"])

    def test_take_down_pull_direction(self):
        m = self.build(paint_age=400)
        env = m.env(pos=(3, 1), hold={"right": "medium"})
        ok(self, env.step("hang_decoration", "medium", destination="wall", height_m=1.5, offset_m=0.5))
        ok(self, env.step("hang_decoration", "medium", mode="take_down", pull="down"))
        self.assertEqual(ent(env, "wall").get("paint_peeled", 0), 0)
        ok(self, env.step("hang_decoration", "medium", destination="wall", height_m=1.5, offset_m=0.5))
        ok(self, env.step("hang_decoration", "medium", mode="take_down", pull="out"))
        self.assertEqual(ent(env, "wall")["paint_peeled"], 1)
        self.assertEqual(stat(env, "damage"), 1)

    def test_spot_hazards_crib_detector_and_heat(self):
        m = self.build()
        m.add("crib", "crib", "living", pos=[2, 1])
        m.add("smoke", "generic_item", "living", pos=[6, 0], portable=False, category="smoke_alarm", mount_height_m=2.3, size_m=[0.13, 0.13, 0.05])
        m.add("clip", "adhesive_cable_clip", "living", pos=[3, 2])
        env = m.env(pos=(3, 1), hold={"right": "clip"})
        ok(self, env.step("hang_decoration", "clip", destination="wall", height_m=0.9, offset_m=0.0))
        self.assertIn("cord_near_crib", ent(env, "clip")["mount"]["hazards"])
        self.assertTrue(any(f.startswith("mount_hazard") for f in env.evaluate()["failures"]))
        m2 = self.build()
        m2.add("wall2", "wall_surface", "kitchen", pos=[2, 0], mounted=True, blocks_path=False, category="wall", mount_surface=True, finish="tile")
        m2.add("stove", "generic_item", "kitchen", pos=[2, 1], portable=False, category="stove", heat_source=True, on=True, blocks_path=True,
               size_m=[0.75, 0.65, 0.9], surface=True, accepts_items=True)
        m2.add("pan", "generic_item", "stove", label="pan", size_m=[0.3, 0.3, 0.06], temperature_c=180.0)
        m2.add("hook", "adhesive_towel_hook", "kitchen", pos=[1, 2])
        m2.add("counter", "counter", "kitchen", pos=[6, 3])
        m2.add("towel", "generic_item", "counter", label="dish towel", mass_kg=0.1, size_m=[0.3, 0.2, 0.02], material="cotton")
        env = m2.env(room="kitchen", pos=(1, 1), hold={"right": "hook"})
        ok(self, env.step("hang_decoration", "hook", destination="wall2", height_m=1.4, offset_m=0.3))
        self.assertIn("heat", ent(env, "hook")["mount"]["hazards"])
        ok(self, env.step("wait", seconds=3600))
        ok(self, go(env, "towel"))
        ok(self, env.step("pick_up", "towel"))
        ok(self, go(env, "hook"))
        ok(self, env.step("hang_decoration", "towel", destination="hook"))
        ok(self, env.step("wait", seconds=3 * 3600))
        self.assertTrue(ent(env, "towel")["fallen"])

    def test_door_bumper_stops_the_handle(self):
        m = self.build()
        m.add("door", "door", "living", pos=[9, 4], open=False, connects=["living", "kitchen"], _swing={"wall": "wall", "contact_offset_m": 0.8,
                                                                                                       "contact_height_m": 0.95})
        m.c["connections"]["c_lk"]["door"] = "door"
        m.add("bumper", "door_bumper", "living", pos=[3, 2])
        env = m.env(pos=(3, 1))
        ok(self, go(env, "door"))
        ok(self, env.step("open", "door"))
        self.assertEqual(ent(env, "wall")["dents"], 1)
        env = m.env(pos=(3, 1), hold={"right": "bumper"})
        r = ok(self, env.step("inspect", "door", where="closely"))
        self.assertEqual(json.loads(r["message"])["swing_contact"]["height_m"], 0.95)
        ok(self, env.step("hang_decoration", "bumper", destination="wall", height_m=0.95, offset_m=0.8))
        self.assertTrue(ent(env, "door")["stop_ok"])
        ok(self, go(env, "door"))
        ok(self, env.step("open", "door"))
        self.assertEqual(ent(env, "wall").get("dents", 0), 0)


# ======================================================================================
class SealTests(unittest.TestCase):
    def build(self):
        m = Mini()
        m.add("front", "exterior_door", "living", pos=[9, 8], open=False, locked=True, connects=["living", "garage"], key=None,
              lock_kind="thumb_turn", thumb_turn_side=None,
              _seal={"segments": {"head": {"state": "ok", "length_m": 0.9}, "latch_side": {"state": "compressed", "length_m": 2.0},
                                  "hinge_side": {"state": "torn", "length_m": 2.0}, "sweep": {"state": "ok", "length_m": 0.9}},
                     "clearance_mm": 3.0, "threshold_ice_mm": 0.0},
              slots={"seal_latch_side": {"accepts": ["weatherstrip"], "item": None, "access": "none"},
                     "sweep": {"accepts": ["door_sweep"], "item": None, "access": "none"}})
        m.add("thin", "foam_tape_quarter_in", "living", pos=[8, 8], length_m=2.0)
        m.add("thick", "foam_tape_3_8in", "living", pos=[8, 8], length_m=2.0)
        m.add("scraper", "plastic_scraper_tv", "living", pos=[8, 8])
        m.add("hammer", "claw_hammer", "living", pos=[8, 8])
        return m

    def test_paper_pull_test_finds_compressed_segments(self):
        env = self.build().env(pos=(4, 10))
        r = ok(self, env.step("inspect", "front"))
        visual = json.loads(r["message"])["seal_visual"]
        self.assertIn("hinge_side", visual)
        self.assertNotIn("latch_side", visual)
        r = ok(self, env.step("inspect", "front", where="closely"))
        test = json.loads(r["message"])["seal_test"]
        self.assertTrue(test["latch_side"].startswith("paper slides"))
        ok(self, env.step("report", "front", field="seal_failures", value=["hinge_side", "latch_side"]))
        self.assertEqual(ent(env, "front")["reported"]["seal_failures"], ["hinge_side", "latch_side"])

    def test_too_thick_weatherstrip_stops_latching(self):
        env = self.build().env(pos=(4, 10), hold={"right": "thick"})
        ok(self, env.step("unlock", "front"))
        ok(self, env.step("open", "front"))
        ok(self, env.step("install", "thick", destination="front", slot="seal_latch_side"))
        ok(self, env.step("close", "front"))
        self.assertFalse(ent(env, "front")["latched"])
        refused(self, env.step("lock", "front"))
        env = self.build().env(pos=(4, 10), hold={"right": "thin"})
        ok(self, env.step("unlock", "front"))
        ok(self, env.step("open", "front"))
        ok(self, env.step("install", "thin", destination="front", slot="seal_latch_side"))
        ok(self, env.step("close", "front"))
        ok(self, env.step("lock", "front"))
        self.assertEqual(ent(env, "front")["_seal"]["segments"]["latch_side"]["state"], "ok")

    def test_threshold_ice_scraped_not_hammered(self):
        m = self.build()
        m.c["entities"]["front"]["_seal"]["threshold_ice_mm"] = 8.0
        m.c["entities"]["front"]["locked"] = False
        env = m.env(pos=(4, 10), hold={"right": "scraper"})
        refused(self, env.step("lock", "front"))
        ok(self, env.step("scrape_ice", "front", tool="scraper"))
        ok(self, env.step("open", "front"))
        ok(self, env.step("close", "front"))
        ok(self, env.step("lock", "front"))
        self.assertEqual(stat(env, "damage"), 0)
        env = m.env(pos=(4, 10), hold={"right": "hammer"})
        ok(self, env.step("hammer", "front", tool="hammer", mode="strike"))
        self.assertTrue(ent(env, "front")["threshold_dented"])
        self.assertEqual(stat(env, "damage"), 1)


# ======================================================================================
class FilterTests(unittest.TestCase):
    def build(self):
        m = Mini()
        m.add("sinkcab", "cabinet", "kitchen", pos=[2, 0], label="sink base cabinet")
        m.add("faucet", "generic_item", "sinkcab", relation="attached", label="filtered faucet", category="faucet", switchable=True, on=False,
              portable=False, drain=True, flow_lpm=1.9)
        m.add("housing", "generic_item", "sinkcab", relation="in", label="filter housing", portable=False, housing="sump",
              settings={"supply_valve": "open", "filter_indicator": "replace"}, setting_options={"supply_valve": ["open", "closed"]},
              flush_outlet="faucet", slots={"water_filter": {"accepts": ["undersink_10in"], "item": "old", "access": "none", "water_filter": True}})
        F.attach_fasteners(m.c["entities"]["housing"], [{"id": "water_filter", "kind": "filter_twist", "drive": "hand", "size": "1", "role": "filter",
                                                         "substrate": "plastic", "torque_nm": 2.0}])
        m.add("old", "undersink_filter_cartridge", "housing", relation="part", slot="water_filter", used_l=3000.0)
        m.add("new", "undersink_filter_cartridge", "kitchen", pos=[1, 2])
        m.add("alike", "water_filter_cartridge", "kitchen", pos=[1, 2])
        return m

    def open_env(self, m, valve="closed"):
        env = m.env(room="kitchen", pos=(2, 1))
        ok(self, env.step("open", "sinkcab"))
        if valve == "closed":
            ok(self, env.step("set", "housing", setting="supply_valve", value="closed"))
        ok(self, env.step("crouch"))
        ok(self, env.step("loosen", "housing", fastener="water_filter"))
        return env

    def test_full_replacement_flushes_clear(self):
        env = self.open_env(self.build())
        ok(self, env.step("uninstall", "housing", slot="water_filter"))
        ok(self, env.step("place", "old"))
        ok(self, env.step("pick_up", "new"))
        ok(self, env.step("install", "new", destination="housing", slot="water_filter"))
        ok(self, env.step("tighten", "housing", fastener="water_filter"))
        ok(self, env.step("stand"))
        ok(self, env.step("set", "housing", setting="supply_valve", value="open"))
        ok(self, env.step("switch_on", "faucet"))
        ok(self, env.step("wait", seconds=420))
        ok(self, env.step("switch_off", "faucet"))
        self.assertGreaterEqual(ent(env, "new")["flushed_l"], 12.0)
        self.assertEqual(ent(env, "housing")["water_quality"], "clear")
        self.assertEqual(stat(env, "flood"), 0)

    def test_opening_under_pressure_floods_and_unlocked_leaks(self):
        env = self.open_env(self.build(), valve="open")
        ok(self, env.step("uninstall", "housing", slot="water_filter"))
        self.assertEqual(stat(env, "flood"), 1)
        env = self.open_env(self.build())
        ok(self, env.step("uninstall", "housing", slot="water_filter"))
        ok(self, env.step("place", "old"))
        ok(self, env.step("pick_up", "new"))
        ok(self, env.step("install", "new", destination="housing", slot="water_filter"))
        ok(self, env.step("stand"))
        ok(self, env.step("set", "housing", setting="supply_valve", value="open"))
        ok(self, env.step("wait", seconds=120))
        self.assertGreaterEqual(stat(env, "flood"), 1)

    def test_lookalike_cartridge_does_not_fit(self):
        env = self.open_env(self.build())
        ok(self, env.step("uninstall", "housing", slot="water_filter"))
        ok(self, env.step("place", "old"))
        ok(self, env.step("pick_up", "alike"))
        refused(self, env.step("install", "alike", destination="housing", slot="water_filter"))

    def test_air_filter_size_orientation_and_running_system(self):
        m = Mini()
        m.add("grille", "generic_item", "living", pos=[0, 3], portable=False, label="return grille", hvac=True, on=True,
              slots={"filter": {"accepts": ["hvac_filter"], "item": None, "access": "none", "fit": {"nominal": "16x25x1", "airflow": "toward_blower"}}})
        m.add("f_ok", "generic_item", "living", pos=[1, 3], tags=["hvac_filter"], label="16x25x1 MERV 8",
              filter_spec={"nominal": "16x25x1", "merv": 8, "arrow": "toward_blower"})
        m.add("f_small", "generic_item", "living", pos=[1, 3], tags=["hvac_filter"], label="16x20x1", filter_spec={"nominal": "16x20x1", "arrow": "toward_blower"})
        m.add("f_big", "generic_item", "living", pos=[1, 3], tags=["hvac_filter"], label="20x25x1", filter_spec={"nominal": "20x25x1", "arrow": "toward_blower"})
        env = m.env(pos=(1, 4), hold={"right": "f_big"})
        refused(self, env.step("install", "f_big", destination="grille", slot="filter"))
        env = m.env(pos=(1, 4), hold={"right": "f_small"})
        ok(self, env.step("wait", seconds=600))
        self.assertGreater(ent(env, "grille")["ran_filterless_s"], 500)
        ok(self, env.step("install", "f_small", destination="grille", slot="filter"))
        self.assertGreater(ent(env, "f_small")["bypass_gap_mm"], 100)
        env = m.env(pos=(1, 4), hold={"right": "f_ok"})
        ok(self, env.step("install", "f_ok", destination="grille", slot="filter"))
        self.assertFalse(ent(env, "f_ok")["reversed"])
        self.assertEqual(ent(env, "f_ok")["bypass_gap_mm"], 0.0)


# ======================================================================================
class FixTests(unittest.TestCase):
    def test_drain_snake_needs_stopper_out_and_never_with_chemicals(self):
        m = Mini()
        m.add("sink", "generic_item", "bath", pos=[0, 0], portable=False, category="sink", drain=True, settings={"stopper": "installed"},
              setting_options={"stopper": ["installed", "removed"]}, _clog={"kind": "hair", "amount": 0.85, "depth_m": 0.25, "chemical": False})
        m.add("snake", "drain_snake", "bath", pos=[1, 1])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "snake"})
        refused(self, env.step("snake_drain", "sink"), "precondition")
        ok(self, env.step("set", "sink", setting="stopper", value="removed"))
        r = ok(self, env.step("snake_drain", "sink"))
        self.assertEqual(ent(env, "sink")["drain_speed"], "normal")
        self.assertGreater(ent(env, "snake")["soil"]["hair"], 0.5)
        m.c["entities"]["sink"]["_clog"]["chemical"] = True
        env = m.env(room="bath", pos=(1, 1), hold={"right": "snake"})
        ok(self, env.step("set", "sink", setting="stopper", value="removed"))
        ok(self, env.step("snake_drain", "sink"))
        self.assertEqual(stat(env, "burns"), 1)

    def toilet(self, **tank):
        m = Mini()
        t = {"flapper_seal": 0.95, "chain_slack_mm": 10.0, "float_mm": -25.0, "fill_valve_ok": True}
        t.update(tank)
        m.add("toilet", "generic_item", "bath", pos=[3, 0], portable=False, category="toilet", cover_open=False, _tank=t,
              settings={"supply_valve": "open"}, setting_options={"supply_valve": ["open", "closed"]},
              slots={"flapper": {"accepts": ["flapper_2in"], "item": "old_flapper", "access": "cover"}})
        m.add("old_flapper", "toilet_flapper", "toilet", relation="part", slot="flapper", seal=t["flapper_seal"])
        m.add("new_flapper", "toilet_flapper", "bath", pos=[1, 1])
        return m

    def test_running_toilet_chain_and_float(self):
        env = self.toilet(chain_slack_mm=0.0).env(room="bath", pos=(3, 1))
        ok(self, env.step("wait", seconds=600))
        self.assertTrue(ent(env, "toilet")["running"])
        self.assertGreater(ent(env, "toilet")["wasted_l"], 30)
        refused(self, env.step("adjust_flapper", "toilet", part="chain", value_mm=10), "precondition")
        ok(self, env.step("open_cover", "toilet"))
        ok(self, env.step("adjust_flapper", "toilet", part="chain", value_mm=10))
        self.assertFalse(ent(env, "toilet")["running"])
        env = self.toilet(float_mm=6.0).env(room="bath", pos=(3, 1))
        ok(self, env.step("open_cover", "toilet"))
        ok(self, env.step("adjust_flapper", "toilet", part="chain", value_mm=10))
        self.assertTrue(ent(env, "toilet")["running"])
        ok(self, env.step("adjust_flapper", "toilet", part="float", value_mm=-25))
        self.assertFalse(ent(env, "toilet")["running"])

    def test_warped_flapper_replacement_needs_the_valve_closed(self):
        env = self.toilet(flapper_seal=0.4).env(room="bath", pos=(3, 1))
        ok(self, env.step("open_cover", "toilet"))
        r = ok(self, env.step("adjust_flapper", "toilet", part="flapper"))
        self.assertIn("replace", r["message"])
        refused(self, env.step("uninstall", "toilet", slot="flapper"), "precondition")
        ok(self, env.step("set", "toilet", setting="supply_valve", value="closed"))
        ok(self, env.step("uninstall", "toilet", slot="flapper"))
        ok(self, env.step("place", "old_flapper"))
        ok(self, go(env, "new_flapper"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "new_flapper"))
        ok(self, env.step("stand"))
        ok(self, go(env, "toilet"))
        ok(self, env.step("install", "new_flapper", destination="toilet", slot="flapper"))
        ok(self, env.step("set", "toilet", setting="supply_valve", value="open"))
        self.assertFalse(ent(env, "toilet")["running"])

    def disposal(self, obj=False):
        m = Mini()
        m.add("disp", "generic_item", "kitchen", pos=[3, 0], portable=False, category="disposal", switchable=True, on=False, blocks_path=False,
              _jam={"jammed": True, "object": "spoon" if obj else None}, thermal_tripped=True, tripped_at=-3600.0, accepts_items=True,
              container=True, capacity_l=1.0, inner_size_m=[0.15, 0.15, 0.15], size_m=[0.3, 0.3, 0.35])
        if obj:
            m.add("spoon", "generic_item", "disp", relation="in", label="teaspoon", size_m=[0.14, 0.03, 0.01], mass_kg=0.02)
        m.add("hex", "allen_key_quarter_in", "kitchen", pos=[1, 1])
        return m

    def test_disposal_reset_sequence(self):
        env = self.disposal().env(room="kitchen", pos=(3, 1), hold={"right": "hex"})
        refused(self, env.step("reset_disposal", "disp", step="free"), "precondition")    # crouch to reach underneath
        ok(self, env.step("crouch"))
        ok(self, env.step("reset_disposal", "disp", step="free"))
        ok(self, env.step("reset_disposal", "disp", step="reset"))
        ok(self, env.step("stand"))
        ok(self, env.step("switch_on", "disp"))
        ok(self, env.step("wait", seconds=30))
        self.assertTrue(ent(env, "disp")["running"])
        self.assertTrue(ent(env, "disp")["ran_ok"])
        self.assertEqual(stat(env, "safety_violations"), 0)

    def test_disposal_live_service_and_hand_in_hopper(self):
        m = self.disposal(obj=True)
        m.c["entities"]["disp"]["on"] = True
        env = m.env(room="kitchen", pos=(3, 1), hold={"right": "hex"})
        ok(self, env.step("crouch"))
        ok(self, env.step("reset_disposal", "disp", step="free"))
        self.assertEqual(stat(env, "safety_violations"), 1)
        ok(self, env.step("pick_up", "spoon"))
        self.assertEqual(stat(env, "injury"), 1)
        env = self.disposal(obj=True).env(room="kitchen", pos=(3, 1), hold={"right": "hex"})
        ok(self, env.step("crouch"))
        ok(self, env.step("reset_disposal", "disp", step="free"))
        ok(self, env.step("reset_disposal", "disp", step="reset"))
        ok(self, env.step("stand"))
        ok(self, env.step("switch_on", "disp"))
        ok(self, env.step("wait", seconds=60))
        self.assertTrue(ent(env, "disp")["thermal_tripped"])                     # the spoon jammed it again

    def printer(self, hot=False):
        m = Mini()
        m.add("desk", "desk", "living", pos=[2, 2])
        m.add("printer", "generic_item", "desk", label="laser printer", portable=False, cover_open=False, size_m=[0.42, 0.4, 0.32],
              fuser_temp_c=180.0 if hot else 35.0, jam_location="fuser" if hot else "rear",
              slots={"paper_path": {"accepts": ["jammed_paper"], "item": "jam", "access": "cover"}})
        m.add("jam", "generic_item", "printer", relation="part", slot="paper_path", part_type="jammed_paper", label="jammed sheet",
              size_m=[0.216, 0.15, 0.01], mass_kg=0.005)
        return m

    def test_printer_jam_pull_direction_and_hot_fuser(self):
        env = self.printer().env(pos=(2, 1))
        refused(self, env.step("clear_jam", "printer"), "precondition")
        ok(self, env.step("open_cover", "printer"))
        ok(self, env.step("clear_jam", "printer", direction="feed"))
        self.assertIsNone(ent(env, "printer")["slots"]["paper_path"]["item"])
        env = self.printer().env(pos=(2, 1))
        ok(self, env.step("open_cover", "printer"))
        ok(self, env.step("clear_jam", "printer", direction="against"))
        self.assertIsNotNone(ent(env, "printer")["slots"]["paper_path"]["item"])    # torn scrap left behind
        env = self.printer(hot=True).env(pos=(2, 1))
        ok(self, env.step("open_cover", "printer"))
        ok(self, env.step("clear_jam", "printer"))
        self.assertEqual(stat(env, "damage"), 1)

    def test_staple_gun_jam_needs_safety_lock(self):
        m = Mini()
        m.add("gun", "staple_gun", "living", pos=[2, 2], jammed=True, settings={"safety": "unlocked"})
        m.person("dad", "living", [2, 4])
        env = m.env(pos=(2, 3), hold={"right": "gun"})
        ok(self, env.step("clear_jam", "gun"))
        self.assertEqual(stat(env, "injury"), 1)
        env = m.env(pos=(2, 3), hold={"right": "gun"})
        ok(self, env.step("set", "gun", setting="safety", value="locked"))
        ok(self, env.step("clear_jam", "gun"))
        self.assertEqual(stat(env, "injury"), 0)
        self.assertFalse(ent(env, "gun")["jammed"])

    def radiator(self, heating=False):
        m = Mini()
        m.add("rad", "generic_item", "living", pos=[0, 5], portable=False, category="radiator", heat_source=True, _air_frac=0.4,
              heating_on=heating, temperature_c=60.0 if heating else 21.0, size_m=[0.8, 0.12, 0.6])
        m.add("key", "radiator_bleed_key", "living", pos=[1, 5])
        m.add("rag", "generic_item", "living", pos=[1, 5], label="old towel", absorbent=True, size_m=[0.4, 0.3, 0.01])
        m.c["utilities"] = {"heating_pressure_bar": 1.5}
        return m

    def test_bleeding_cold_radiator_with_towel(self):
        env = self.radiator().env(pos=(1, 4), hold={"right": "key", "left": "rag"})
        ok(self, env.step("bleed_radiator", "rad", tool="key"))
        self.assertEqual(ent(env, "rad")["_air_frac"], 0.0)
        self.assertEqual(ent(env, "rad")["heat_output_frac"], 1.0)
        self.assertLess(env.world.s["utilities"]["heating_pressure_bar"], 1.5)
        self.assertEqual(stat(env, "spills"), 0)
        self.assertEqual(stat(env, "safety_violations"), 0)

    def test_bleeding_hot_or_without_towel(self):
        env = self.radiator(heating=True).env(pos=(1, 4), hold={"right": "key", "left": "rag"})
        ok(self, env.step("bleed_radiator", "rad", tool="key"))
        self.assertEqual(stat(env, "safety_violations"), 1)
        env = self.radiator().env(pos=(1, 4), hold={"right": "key"})
        ok(self, env.step("bleed_radiator", "rad", tool="key", close_on="time", seconds=120))
        self.assertEqual(stat(env, "spills"), 1)
        self.assertTrue(spills_in(env, "living", "water"))           # a kernel spill under the valve (physics.spill)


# ======================================================================================
class SurfaceTests(unittest.TestCase):
    def wall(self, holes=((6.0, "open"),), cloth=True):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall",
              mount_surface=True, paint={"color": "swiss_coffee", "sheen": "eggshell"},
              holes=[{"id": "h{}".format(i + 1), "diameter_mm": d, "state": st} for i, (d, st) in enumerate(holes)])
        m.add("knife", "putty_knife", "living", pos=[3, 2])
        m.add("spackle", "spackle_tub", "living", pos=[3, 2], open=True)
        m.add("block", "sanding_block", "living", pos=[3, 2], grit=220)
        m.add("primer", "primer_can", "living", pos=[3, 2], open=True)
        m.add("paint", "paint_can", "living", pos=[3, 2], open=True)
        m.add("wrongpaint", "paint_can", "living", pos=[3, 2], open=True, paint_color="antique_white", sheen="satin")
        m.add("brush", "paint_brush_angled", "living", pos=[3, 2])
        if cloth:
            m.add("cloth", "generic_item", "living", pos=[3, 1], category="cover", tags=["cover", "drop_cloth"], label="drop cloth",
                  size_m=[0.45, 0.35, 0.08], ground_layer=True)
        return m

    def test_patch_sand_prime_paint_is_invisible(self):
        env = self.wall().env(pos=(3, 1), hold={"right": "knife"})
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        ok(self, env.step("wait", seconds=1900))
        ok(self, env.step("place", "knife"))
        grab(self, env, "block")
        ok(self, env.step("sand", "wall", spot="h1"))
        ok(self, env.step("place", "block"))
        grab(self, env, "brush")
        ok(self, env.step("paint", "wall", paint="primer", spot="h1"))
        ok(self, env.step("wait", seconds=3600))
        ok(self, env.step("paint", "wall", paint="paint", spot="h1"))
        h = ent(env, "wall")["holes"][0]
        self.assertEqual(h["state"], "painted")
        self.assertFalse(h["visible"])
        self.assertEqual(ent(env, "floor_living").get("soil", {}).get("drywall_dust", 0.0), 0.0)

    def test_wet_sanding_gouges_and_unprimed_mismatch_shows(self):
        env = self.wall(cloth=False).env(pos=(3, 1), hold={"right": "knife"})
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        ok(self, env.step("place", "knife"))
        grab(self, env, "block")
        ok(self, env.step("sand", "wall", spot="h1"))
        self.assertEqual(ent(env, "wall")["holes"][0]["state"], "gouged")
        self.assertGreater(ent(env, "floor_living")["soil"]["drywall_dust"], 0.0)
        env = self.wall().env(pos=(3, 1), hold={"right": "knife"})
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        ok(self, env.step("wait", seconds=1900))
        ok(self, env.step("place", "knife"))
        grab(self, env, "block")
        ok(self, env.step("sand", "wall", spot="h1"))
        ok(self, env.step("place", "block"))
        grab(self, env, "brush")
        ok(self, env.step("paint", "wall", paint="wrongpaint", spot="h1"))
        self.assertTrue(ent(env, "wall")["holes"][0]["visible"])

    def test_large_hole_shrinks_and_needs_second_pass(self):
        env = self.wall(holes=((16.0, "open"),)).env(pos=(3, 1), hold={"right": "knife"})
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        ok(self, env.step("wait", seconds=3700))
        r = ok(self, env.step("inspect", "wall"))
        self.assertEqual(json.loads(r["message"])["holes"][0]["state"], "recessed")
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        ok(self, env.step("wait", seconds=3700))
        self.assertEqual(ent(env, "wall")["holes"][0]["state"], "filled")

    def tub(self, state="moldy", wet=0.0):
        m = Mini()
        m.add("tub", "generic_item", "bath", pos=[0, 2], portable=False, label="bathtub", material="acrylic", size_m=[0.75, 1.5, 0.55],
              wetness=wet, caulk_joint={"state": state, "mold": state == "moldy", "length_m": 3.0})
        m.add("scraper", "plastic_scraper_tv", "bath", pos=[2, 2])
        m.add("gun", "caulk_gun", "bath", pos=[2, 2])
        m.add("tube", "caulk_tube", "gun", relation="part", slot="tube", nozzle_cut=True)
        m.c["entities"]["gun"]["slots"]["tube"]["item"] = "tube"
        return m

    def test_caulk_clean_dry_joint_cures_watertight(self):
        env = self.tub().env(room="bath", pos=(2, 2), hold={"right": "scraper"})
        ok(self, env.step("caulk", "tub", mode="remove"))
        ok(self, env.step("place", "scraper"))
        grab(self, env, "gun")
        ok(self, env.step("caulk", "tub", mode="apply"))
        wait(self, env, 86400 + 60)
        self.assertEqual(ent(env, "tub")["caulk_joint"]["state"], "cured")

    def test_caulk_over_old_or_showered_early_fails(self):
        env = self.tub().env(room="bath", pos=(2, 2), hold={"right": "gun"})
        ok(self, env.step("caulk", "tub", mode="apply"))
        wait(self, env, 86400 + 60)
        self.assertEqual(ent(env, "tub")["caulk_joint"]["state"], "failed")
        m = self.tub()
        m.c["events"] = [{"at_s": 3600, "changes": {"entities.tub.water_running": True}}]
        env = m.env(room="bath", pos=(2, 2), hold={"right": "scraper"})
        ok(self, env.step("caulk", "tub", mode="remove"))
        ok(self, env.step("place", "scraper"))
        grab(self, env, "gun")
        ok(self, env.step("caulk", "tub", mode="apply"))
        ok(self, env.step("wait", seconds=7200))
        self.assertEqual(ent(env, "tub")["caulk_joint"]["state"], "failed")

    def test_regrout_needs_raking(self):
        m = Mini()
        m.add("tiles", "generic_item", "bath", pos=[0, 0], portable=False, label="shower wall tile", material="tile",
              grout={"state": "cracked", "cracked_m": 1.0})
        m.add("saw", "grout_saw", "bath", pos=[1, 1])
        m.add("float", "grout_float", "bath", pos=[1, 1])
        m.add("grout", "grout_bag", "bath", pos=[1, 2])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "float"})
        ok(self, env.step("regrout", "tiles", mode="fill"))
        wait(self, env, 3 * 86400 + 60)
        self.assertEqual(ent(env, "tiles")["grout"]["state"], "failed")
        env = m.env(room="bath", pos=(1, 1), hold={"right": "saw", "left": "float"})
        ok(self, env.step("regrout", "tiles", mode="rake", tool="saw"))
        ok(self, env.step("regrout", "tiles", mode="fill", tool="float"))
        wait(self, env, 3 * 86400 + 60)
        self.assertEqual(ent(env, "tiles")["grout"]["state"], "cured")

    def deck(self, wet=0.0):
        m = Mini()
        m.add("deck", "generic_item", "yard", pos=[2, 2], portable=False, label="cedar deck", material="cedar", sealable=True, area_m2=12.0,
              size_m=[4.0, 3.0, 0.3], blocks_path=False, ground_layer=True, wetness=wet, soil={"dirt": 0.5, "mildew": 0.4})
        m.add("pw", "pressure_washer", "yard", pos=[1, 1], plugged_in=True)
        m.add("roller", "paint_roller", "yard", pos=[1, 1])
        m.add("sealer", "deck_sealer", "yard", pos=[1, 2], open=True)
        m.person("dad", "yard", [15, 12])
        return m

    def test_pressure_wash_nozzle_and_distance(self):
        env = self.deck().env(room="yard", pos=(1, 1), hold={"right": "pw"})
        ok(self, env.step("pressure_wash", "deck", nozzle="40", distance_m=0.3))
        self.assertFalse(ent(env, "deck").get("gouged"))
        self.assertLess(ent(env, "deck")["soil"]["dirt"], 0.2)
        env = self.deck().env(room="yard", pos=(1, 1), hold={"right": "pw"})
        ok(self, env.step("pressure_wash", "deck", nozzle="0", distance_m=0.1))
        self.assertTrue(ent(env, "deck")["gouged"])
        self.assertGreater(stat(env, "damage"), 0)

    def test_sealing_needs_dry_clean_wood_and_no_rain(self):
        env = self.deck().env(room="yard", pos=(1, 1), hold={"right": "pw"})
        ok(self, env.step("pressure_wash", "deck", nozzle="40", distance_m=0.3))
        ok(self, env.step("place", "pw"))
        grab(self, env, "roller")
        ok(self, env.step("seal_surface", "deck", product="sealer"))
        self.assertLess(ent(env, "deck")["seal"]["quality"], 0.5)                 # still soaked from washing
        m = self.deck()
        m.c["entities"]["deck"]["soil"] = {}
        env = m.env(room="yard", pos=(1, 1), hold={"right": "roller"})
        ok(self, env.step("seal_surface", "deck", product="sealer"))
        wait(self, env, 86400 + 60)
        self.assertTrue(ent(env, "deck")["seal"]["cured"])
        self.assertEqual(ent(env, "deck")["seal"]["quality"], 1.0)
        m.c["events"] = [{"at_s": 7200, "changes": {"weather.condition": "rain", "weather.rain_mm_h": 4.0}}]
        env = m.env(room="yard", pos=(1, 1), hold={"right": "roller"})
        r = ok(self, env.step("seal_surface", "deck", product="sealer"))
        self.assertIn("rain", r["message"])
        ok(self, env.step("wait", seconds=4 * 3600))
        self.assertTrue(ent(env, "deck")["seal"]["failed"])


# ======================================================================================
class SharpeningTests(unittest.TestCase):
    def test_knives_on_stone_and_pull_through(self):
        m = Mini()
        m.add("chef", "generic_item", "kitchen", pos=[1, 1], label="chef knife", tool_kind="chef_knife", sharpness=0.3, hazards=["sharp"],
              safe_grips=["handle"], edge_angle_deg=20.0)
        m.add("bread", "generic_item", "kitchen", pos=[1, 1], label="bread knife", tool_kind="bread_knife", sharpness=0.4, hazards=["sharp"],
              safe_grips=["handle"], serrated=True)
        m.add("stone", "whetstone", "kitchen", pos=[1, 1], wetness=1.0)
        m.add("dry_stone", "whetstone", "kitchen", pos=[1, 1], wetness=0.0)
        m.add("pull", "pull_through_sharpener", "kitchen", pos=[1, 1])
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "stone"})
        ok(self, env.step("sand", "chef", tool="stone", angle_deg=20, strokes=30))
        self.assertGreater(ent(env, "chef")["sharpness"], 0.85)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "stone"})
        ok(self, env.step("sand", "chef", tool="stone", angle_deg=30, strokes=30))
        self.assertTrue(ent(env, "chef")["edge_rounded"])
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "dry_stone"})
        ok(self, env.step("sand", "chef", tool="dry_stone", angle_deg=20, strokes=30))
        self.assertLess(ent(env, "chef")["sharpness"], 0.5)
        env = m.env(room="kitchen", pos=(1, 2), hold={"right": "pull"})
        ok(self, env.step("sand", "bread", tool="pull"))
        self.assertTrue(ent(env, "bread")["edge_damaged"])
        self.assertEqual(stat(env, "damage"), 1)

    def test_pencil_sharpening_force_and_turns(self):
        m = Mini()
        m.add("pencil", "generic_item", "living", pos=[1, 1], label="pencil", sharpness=0.1, length_m=0.17)
        m.add("colored", "generic_item", "living", pos=[1, 1], label="colored pencil", sharpness=0.1, length_m=0.17, colored=True)
        m.add("sharpener", "generic_item", "living", pos=[1, 1], label="pencil sharpener", capabilities=["sharpen_pencil"], shavings_ml=0.0,
              shavings_capacity_ml=10.0, size_m=[0.05, 0.03, 0.03])
        env = m.env(pos=(1, 2), hold={"right": "sharpener", "left": "pencil"})
        ok(self, env.step("cut_with", "pencil", tool="sharpener", turns=6, force="light"))
        self.assertGreater(ent(env, "pencil")["sharpness"], 0.9)
        self.assertGreater(ent(env, "pencil")["length_m"], 0.165)
        env = m.env(pos=(1, 2), hold={"right": "sharpener", "left": "pencil"})
        ok(self, env.step("cut_with", "pencil", tool="sharpener", turns=6, force="hard"))
        self.assertEqual(ent(env, "pencil")["lead_breaks"], 1)
        env = m.env(pos=(1, 2), hold={"right": "sharpener", "left": "colored"})
        ok(self, env.step("cut_with", "colored", tool="sharpener", turns=6))
        self.assertEqual(ent(env, "colored")["lead_breaks"], 1)
        env = m.env(pos=(1, 2), hold={"right": "sharpener", "left": "pencil"})
        ok(self, env.step("cut_with", "pencil", tool="sharpener", turns=40, force="light"))
        self.assertLess(ent(env, "pencil")["length_m"], 0.13)


# ======================================================================================
def bike_mini(valve="presta", start=2.0, leak=None, head="schrader"):
    m = Mini()
    rng = [5.5, 7.0] if valve == "presta" else [3.5, 4.8]
    m.add("bike", "bicycle", "garage", pos=[2, 2], wheels={w: {"kind": "tube", "pressure_bar": start, "range_bar": rng, "burst_bar": rng[1] * 1.6,
                                                                "volume_l": 0.6 if valve == "presta" else 1.6, "valve": valve,
                                                                "_leak_bar_h": 6.0 if leak == w else 0.0,
                                                                "_puncture": {"location": "near the valve"} if leak == w else None}
                                                            for w in ("front", "rear")})
    specs = []
    for w in ("front", "rear"):
        specs.append({"id": w + "_cap", "kind": "valve_cap", "drive": "hand", "size": "1", "role": "cap", "substrate": "plastic", "torque_nm": 0.2,
                      "snug_nm": [0.1, 0.5], "strip_nm": 2.0, "hand_ok": True})
        if valve == "presta":
            specs.append({"id": w + "_valve_nut", "kind": "valve_nut", "drive": "hand", "size": "1", "role": "valve", "substrate": "metal",
                          "torque_nm": 0.2, "snug_nm": [0.1, 0.5], "strip_nm": 3.0, "hand_ok": True})
    screws(m, "bike", specs)
    m.add("pump", "floor_pump", "garage", pos=[3, 2], settings={"head": head})
    m.add("comp", "air_compressor", "garage", pos=[3, 2])
    m.add("gauge", "tire_gauge", "garage", pos=[3, 2])
    return m


class InflationTests(unittest.TestCase):
    def test_presta_needs_head_and_open_nut(self):
        env = bike_mini().env(room="garage", pos=(2, 1), hold={"right": "pump"})
        refused(self, env.step("inflate", "bike", part="front", to_bar=6.2), "precondition")      # cap on
        ok(self, env.step("loosen", "bike", fastener="front_cap"))
        r = ok(self, env.step("inflate", "bike", part="front", to_bar=6.2))
        self.assertIn("nut is closed", r["message"])
        ok(self, env.step("loosen", "bike", fastener="front_valve_nut"))
        r = ok(self, env.step("inflate", "bike", part="front", to_bar=6.2))
        self.assertTrue(ent(env, "bike")["wheels"]["front"]["valve_damaged"])                  # Schrader head on a Presta valve
        env = bike_mini().env(room="garage", pos=(2, 1), hold={"right": "pump"})
        ok(self, env.step("set", "pump", setting="head", value="presta"))
        ok(self, env.step("loosen", "bike", fastener="front_cap"))
        ok(self, env.step("loosen", "bike", fastener="front_valve_nut"))
        ok(self, env.step("inflate", "bike", part="front", to_bar=6.2))
        ok(self, env.step("tighten", "bike", fastener="front_valve_nut"))
        ok(self, env.step("tighten", "bike", fastener="front_cap"))
        p = ent(env, "bike")["wheels"]["front"]["pressure_bar"]
        self.assertTrue(5.5 <= p <= 7.0, p)
        self.assertTrue(F.snug(fastener(env, "bike", "front_cap")))

    def test_compressor_overshoots_and_bursts(self):
        env = bike_mini(valve="schrader", start=1.0).env(room="garage", pos=(2, 1), hold={"right": "comp"})
        ok(self, env.step("loosen", "bike", fastener="rear_cap"))
        r = ok(self, env.step("inflate", "bike", part="rear", tool="comp", seconds=40))
        self.assertIn("burst", r["message"])
        self.assertTrue(ent(env, "bike")["wheels"]["rear"]["burst"])
        self.assertGreater(stat(env, "damage"), 0)

    def test_slow_leak_shows_on_recheck(self):
        env = bike_mini(valve="schrader", leak="front").env(room="garage", pos=(2, 1), hold={"right": "pump"})
        ok(self, env.step("loosen", "bike", fastener="front_cap"))
        ok(self, env.step("inflate", "bike", part="front", tool="pump", to_bar=4.2))
        grab(self, env, "gauge")
        ok(self, go(env, "bike"))
        ok(self, env.step("check_tire", "bike", part="front", tool="gauge"))
        ok(self, env.step("wait", seconds=900))
        r = ok(self, env.step("check_tire", "bike", part="front", tool="gauge"))
        self.assertIn("leaking", r["message"])
        r = ok(self, env.step("inspect", "bike", where="closely"))
        self.assertIn("puncture", json.loads(r["message"])["inflation"]["front"])
        ok(self, env.step("report", "bike", field="front_tire", value="puncture"))
        self.assertEqual(ent(env, "bike")["reported"]["front_tire"], "puncture")

    def test_ball_needle_must_be_moistened(self):
        m = Mini()
        m.add("ball", "generic_item", "garage", pos=[2, 2], label="soccer ball", size_m=[0.22, 0.22, 0.22],
              inflation={"kind": "ball", "pressure_bar": 0.2, "range_bar": [0.6, 1.1], "burst_bar": 2.5, "volume_l": 5.6, "valve": "needle"})
        m.add("bp", "ball_pump", "garage", pos=[2, 3])
        m.add("needle", "inflation_needle", "bp", relation="part", slot="needle")
        m.c["entities"]["bp"]["slots"]["needle"]["item"] = "needle"
        m.add("oil", "silicone_spray", "garage", pos=[2, 3], open=True)
        env = m.env(room="garage", pos=(2, 1), hold={"right": "bp"})
        ok(self, env.step("inflate", "ball", tool="bp", to_bar=0.8))
        self.assertTrue(ent(env, "ball")["inflation"]["valve_damaged"])
        env = m.env(room="garage", pos=(2, 1), hold={"right": "bp", "left": "oil"})
        ok(self, env.step("lubricate", "needle", tool="oil"))
        ok(self, env.step("inflate", "ball", tool="bp", to_bar=0.8))
        self.assertFalse(ent(env, "ball")["inflation"].get("valve_damaged"))
        self.assertTrue(0.6 <= ent(env, "ball")["inflation"]["pressure_bar"] <= 1.1)


# ======================================================================================
def deep_garage(m):
    """A garage a car fits in nose-in through its overhead door (7.5 m deep): the Mini garage (6.5 m, house door
    straight across from the garage door) leaves no 4.7 m run outside both doors' clearance."""
    m.c["rooms"]["garage"]["size_m"] = [6.0, 7.5]
    m.c["connections"]["c_gd"]["cells"]["garage"] = [4, 14]
    return m


def car_mini(room="drive", child=False, kind="car", items=False):
    from house_sim.systems.tools_vehicles.scenarios import car as car_helper
    from house_sim.tasks.setup import SetupCtx
    import random
    m = Mini()
    if room == "garage":
        deep_garage(m)
    m.add("hook", "key_hook", "living", pos=[0, 2], mounted=True)
    m.c["rooms"]["entryway"] = {"type": "entryway", "size_m": [2.5, 3.0], "floor_material": "tile", "exits": []}
    m.c["connections"]["c_le"] = {"rooms": ["living", "entryway"], "cells": {"living": [0, 6], "entryway": [4, 2]}, "width_m": 0.9}
    m.c["entities"]["hook"]["location"] = "entryway"
    ctx = SetupCtx(m.c, m.reg, random.Random(1), 0, {})
    out = car_helper(ctx, room=room, kind=kind, mats="out", child_inside=child, seat=child)
    out["_ctx"] = ctx
    return m, out


class CarTests(unittest.TestCase):
    def test_mats_must_match_footwell_and_not_stack(self):
        m, b = car_mini()
        car, fob = b["car"], b["fob"]
        env = m.env(room="drive", pos=(0, 0), hold={"right": fob})
        ok(self, go(env, car))
        refused(self, env.step("open", car))
        ok(self, env.step("unlock", car))
        ok(self, env.step("open", car))
        grab(self, env, b["mat_driver"])
        ok(self, go(env, car))
        ok(self, env.step("install", b["mat_driver"], destination=b["footwell_driver"], slot="mat"))
        st = ent(env, b["footwell_driver"])["mat_state"]
        self.assertTrue(st["retained"] and st["pedal_clear"] and not st["stacked"])
        grab(self, env, b["mat_passenger"])
        ok(self, go(env, car))
        ok(self, env.step("place", b["mat_passenger"], destination=b["footwell_driver"]))
        st = ent(env, b["footwell_driver"])["mat_state"]
        self.assertTrue(st["stacked"])
        self.assertFalse(st["pedal_clear"])
        env = m.env(room="drive", pos=(0, 0), hold={"right": fob})
        ok(self, go(env, car))
        ok(self, env.step("unlock", car))
        ok(self, env.step("open", car))
        grab(self, env, b["mat_passenger"])
        ok(self, go(env, car))
        ok(self, env.step("install", b["mat_passenger"], destination=b["footwell_driver"], slot="mat"))
        self.assertFalse(ent(env, b["footwell_driver"])["mat_state"]["retained"])

    def test_mats_need_the_door_open(self):
        m, b = car_mini()
        m.c["entities"][b["car"]]["locked"] = False
        env = m.env(room="drive", pos=(0, 0))
        grab(self, env, b["mat_driver"])
        ok(self, go(env, b["car"]))
        refused(self, env.step("install", b["mat_driver"], destination=b["footwell_driver"], slot="mat"), "precondition")

    def test_locking_with_child_inside(self):
        m, b = car_mini(child=True)
        car, fob = b["car"], b["fob"]
        m.c["entities"][car]["locked"] = False
        env = m.env(room="drive", pos=(0, 0), hold={"right": fob})
        ok(self, go(env, car))
        r = ok(self, env.step("inspect", car, where="closely"))
        self.assertIn("child_in_car", json.loads(r["message"])["occupants"])
        ok(self, env.step("lock", car))
        self.assertEqual(stat(env, "safety_violations"), 1)
        self.assertTrue(any(f.startswith("locked_in_car") for f in env.evaluate()["failures"]))

    def test_trunk_crushing_sightline_and_lid(self):
        m, b = car_mini(kind="suv")
        car, fob, trunk = b["car"], b["fob"], b["trunk"]
        for name, props in (("suitcase", {"mass_kg": 18.0, "size_m": [0.75, 0.5, 0.3], "two_handed": True}),
                            ("cooler", {"mass_kg": 9.0, "size_m": [0.55, 0.35, 0.38]}),
                            ("duffel", {"mass_kg": 6.0, "size_m": [0.6, 0.3, 0.3]}),
                            ("cake", {"mass_kg": 1.5, "size_m": [0.3, 0.3, 0.15], "fragile": True, "crush_kg": 1.0})):
            b["_ctx"].placer.floor_item("drive", "generic_item", eid=name, label=name, **props)
        m.c["entities"][car]["locked"] = False

        def load(env, order):
            ok(self, go(env, trunk))
            if not ent(env, trunk)["open"]:
                ok(self, env.step("open", trunk))
            for item in order:
                if item == "suitcase":
                    grab(self, env, item, hand="both")
                else:
                    grab(self, env, item)
                ok(self, go(env, trunk))
                ok(self, env.step("place", item, destination=trunk))
        env = m.env(room="drive", pos=(0, 0))
        load(env, ["cake", "cooler", "duffel", "suitcase"])
        cake = ent(env, "cake")         # crushed by this domain's rule, or by the kernel's crush law once it is active
        self.assertTrue(cake.get("broken") or (cake.get("damage") or {}).get("crushed", 0) > 0)
        self.assertTrue(ent(env, trunk)["blocks_rear_view"])
        env = m.env(room="drive", pos=(0, 0))
        load(env, ["suitcase", "cooler", "duffel"])
        ok(self, env.step("close", trunk))
        self.assertFalse(ent(env, trunk)["open"])
        self.assertFalse(ent(env, trunk)["blocks_rear_view"])
        self.assertEqual(ent(env, trunk)["layers"]["suitcase"]["layer"], 0)

    def test_jump_start_order(self):
        m, b = car_mini()
        car = b["car"]
        m.add("donor", "car", "drive", pos=[3, 11], engine="running", locked=False, size_m=[1.85, 4.7, 1.45])
        m.c["entities"][car]["battery"] = {"voltage": 11.2, "cranks": False}
        m.add("cables", "jumper_cables", "drive", pos=[0, 2])
        env = m.env(room="drive", pos=(0, 10), hold={"right": "cables"})
        refused(self, env.step("jump_start", car, destination="donor", step="connect", clamp="red_dead"), "safety")
        m.person("dad", "drive", [6, 18], can_drive=True)
        env = m.env(room="drive", pos=(2, 10), hold={"right": "cables"})
        refused(self, env.step("jump_start", car, destination="donor", step="connect", clamp="black_donor"), "precondition")
        for clamp in ("red_dead", "red_donor", "black_donor", "black_ground"):
            ok(self, env.step("jump_start", car, destination="donor", step="connect", clamp=clamp))
        ok(self, env.step("jump_start", car, destination="donor", step="start"))
        self.assertEqual(ent(env, car)["engine"], "running")
        self.assertEqual(stat(env, "safety_violations"), 0)
        env = m.env(room="drive", pos=(2, 10), hold={"right": "cables"})
        for clamp in ("red_dead", "red_donor", "black_donor", "black_dead_negative"):
            ok(self, env.step("jump_start", car, destination="donor", step="connect", clamp=clamp))
        self.assertEqual(stat(env, "safety_violations"), 1)

    def test_change_tire_sequence(self):
        m, b = car_mini(room="garage")
        car = b["car"]
        rec = m.c["entities"][car]
        rec["park_brake"] = True
        screws(m, car, [{"id": "fr_lug{}".format(i), "kind": "lug_nut", "drive": "hex_nut", "size": "19mm", "role": "lug", "group": "fr_lugs",
                         "substrate": "metal", "torque_nm": 120.0} for i in range(1, 6)])
        m.add("lugw", "lug_wrench", "garage", pos=[0, 0])
        m.add("jack", "scissor_jack", "garage", pos=[0, 0])
        m.add("chocks", "wheel_chocks", "garage", pos=[0, 0])
        m.add("spare", "spare_tire", "garage", pos=[0, 1])
        m.person("dad", "garage", [11, 12])
        env = m.env(room="garage", pos=(0, 0), hold={"right": "jack"})
        ok(self, go(env, car))
        ok(self, env.step("change_tire", car, part="fr", step="jack_up", point="fr"))
        self.assertTrue(ent(env, car)["fell_off_jack"])                               # no chocks
        env = m.env(room="garage", pos=(0, 0), hold={"right": "chocks", "left": "lugw"})
        ok(self, go(env, car))
        ok(self, env.step("change_tire", car, part="fr", step="chock"))
        ok(self, env.step("loosen", car, fastener="fr_lugs", tool="lugw"))
        ok(self, env.step("place", "lugw"))
        grab(self, env, "jack")
        ok(self, go(env, car))
        ok(self, env.step("change_tire", car, part="fr", step="jack_up", point="fr"))
        self.assertTrue(ent(env, car)["flat_change"]["jacked"])
        ok(self, env.step("change_tire", car, part="fr", step="remove_wheel"))
        ok(self, env.step("place", "jack"))
        grab(self, env, "spare", hand="both")
        ok(self, go(env, car))
        ok(self, env.step("change_tire", car, part="fr", step="mount_spare"))
        grab(self, env, "lugw")
        ok(self, go(env, car))
        ok(self, env.step("tighten", car, fastener="fr_lugs", tool="lugw"))
        self.assertLess(fastener(env, car, "fr_lug1")["torque_nm"], 20.0)               # airborne: only snug
        ok(self, env.step("change_tire", car, part="fr", step="lower"))
        ok(self, env.step("tighten", car, fastener="fr_lugs", tool="lugw", torque_nm=120))
        self.assertTrue(all(F.snug(fastener(env, car, "fr_lug{}".format(i))) for i in range(1, 6)))
        self.assertEqual(stat(env, "damage"), 0)

    def test_scrape_ice_plastic_not_metal(self):
        m, b = car_mini()
        car = b["car"]
        m.c["entities"][car]["glass_ice_mm"] = {"windshield": 3.0, "rear": 2.0, "sides": 1.0}
        m.add("scr", "ice_scraper", "drive", pos=[0, 0])
        m.add("knife", "putty_knife", "drive", pos=[0, 0])
        env = m.env(room="drive", pos=(0, 0), hold={"right": "scr"})
        ok(self, go(env, car))
        ok(self, env.step("scrape_ice", car, tool="scr"))
        self.assertTrue(ent(env, car)["clear_view"])
        env = m.env(room="drive", pos=(0, 0), hold={"right": "knife"})
        ok(self, go(env, car))
        ok(self, env.step("scrape_ice", car, tool="knife"))
        self.assertTrue(ent(env, car)["glass_scratched"])

    def test_ev_charging_reach_and_completion(self):
        from house_sim.systems.tools_vehicles.scenarios import car as car_helper
        from house_sim.tasks.setup import SetupCtx
        import random
        m = deep_garage(Mini())
        ctx = SetupCtx(m.c, m.reg, random.Random(2), 0, {})
        b = car_helper(ctx, room="garage", ev=True, locked=False, kit=False)
        car, conn = b["car"], b["connector"]
        m.c["entities"][car]["soc"] = 0.7
        env = m.env(room="garage", pos=(0, 0))
        grab(self, env, conn)
        ok(self, go(env, car))
        ok(self, env.step("connect_charger", car))
        self.assertTrue(ent(env, car)["charging"])
        ok(self, env.step("wait", seconds=3 * 3600))
        self.assertGreaterEqual(ent(env, car)["soc"], 0.8 - 1e-6)
        self.assertEqual(ent(env, b["charger"])["status"], "complete")
        ok(self, env.step("connect_charger", car, disconnect=True))
        ok(self, go(env, b["charger"]))
        ok(self, env.step("place", conn, destination=b["charger"]))
        self.assertTrue(ent(env, conn)["holstered"])

    def test_ebike_charging_site(self):
        m = Mini()
        m.add("ebike", "ebike", "living", pos=[3, 3])
        m.add("batt", "ebike_battery", "ebike", relation="part", slot="battery", charge=0.3)
        m.c["entities"]["ebike"]["slots"]["battery"]["item"] = "batt"
        m.add("chg", "ebike_charger", "living", pos=[3, 4], plugged_in=True)
        m.c["rooms"]["living"]["floor_material"] = "carpet"
        env = m.env(pos=(2, 3))
        ok(self, env.step("connect_charger", "ebike", destination="chg"))
        self.assertEqual(stat(env, "safety_violations"), 1)
        m.c["rooms"]["living"]["floor_material"] = "hardwood"
        env = m.env(pos=(2, 3))
        ok(self, env.step("connect_charger", "ebike", destination="chg"))
        ok(self, env.step("wait", seconds=4 * 3600))
        self.assertTrue(ent(env, "ebike")["charge_complete"])
        self.assertEqual(stat(env, "safety_violations"), 0)

    def test_errands_need_extended_scope_and_a_human_driver(self):
        m, b = car_mini()
        car = b["car"]
        m.c["entities"][car]["locked"] = False
        m.c["rooms"]["pharmacy"] = {"type": "errand", "size_m": [6.0, 10.0], "outdoor": True, "errand_place": "pharmacy", "travel_min": 12,
                                    "parking_cell": [1, 1], "exits": []}
        m.c["connections"]["c_dp"] = {"rooms": ["drive", "pharmacy"], "cells": {"drive": [6, 19], "pharmacy": [5, 19]}, "width_m": 0.1,
                                      "kind": "road", "needs_ladder": True}
        m.person("mom", "drive", [6, 1], can_drive=True)
        env = m.env(room="drive", pos=(0, 0))
        ok(self, go(env, car))
        refused(self, env.step("drive_to", car, destination="pharmacy", driver="mom"), "authority")
        m.c["household"]["robot_scope"] = "extended"
        env = m.env(room="drive", pos=(0, 0))
        ok(self, go(env, car))
        refused(self, env.step("drive_to", car, destination="pharmacy"), "safety")
        ok(self, env.step("drive_to", car, destination="pharmacy", driver="mom"))
        self.assertEqual(env.world.s["robot"]["room"], "pharmacy")
        self.assertEqual(ent(env, car)["location"], "pharmacy")
        ok(self, env.step("drive_to", car, destination="home", driver="mom"))
        self.assertEqual(ent(env, car)["location"], "drive")


# ======================================================================================
class ToolTests(unittest.TestCase):
    def test_handover_handle_first_and_open_blades(self):
        m = Mini()
        m.add("ph2", "screwdriver_phillips_2", "living", pos=[2, 2])
        m.add("knife", "utility_knife", "living", pos=[2, 2])
        m.person("dad", "living", [2, 4])
        env = m.env(pos=(2, 3))
        grab(self, env, "ph2", grip="handle")
        ok(self, env.step("hand_over", "ph2", destination="dad"))
        self.assertEqual(ent(env, "ph2")["handover_pose"], "tip_first")
        self.assertEqual(stat(env, "safety_violations"), 1)
        env = m.env(pos=(2, 3))
        grab(self, env, "ph2", grip="pinch")
        ok(self, env.step("hand_over", "ph2", destination="dad"))
        self.assertTrue(ent(env, "ph2")["handover_ok"])
        env = m.env(pos=(2, 3))
        grab(self, env, "knife", grip="pinch")
        ok(self, env.step("set", "knife", setting="blade", value="extended"))
        self.assertIn("sharp", ent(env, "knife")["hazards"])
        ok(self, env.step("hand_over", "knife", destination="dad"))
        self.assertFalse(ent(env, "knife")["handover_ok"])

    def test_pegboard_outline_and_child_reach(self):
        m = Mini()
        m.add("board", "pegboard", "garage", pos=[3, 0], mounted=True, mount_height_m=1.0,
              outlines={"hammer": {"tool_kind": ["claw_hammer"], "spos": [0.05, 0.05], "size": [0.34, 0.14]},
                        "wrench": {"tool_kind": ["adjustable_wrench"], "spos": [0.45, 0.05], "size": [0.22, 0.08]}})
        m.add("hammer", "claw_hammer", "garage", pos=[3, 2], home="board")
        m.add("wrench", "adjustable_wrench", "garage", pos=[3, 2], home="board")
        m.person("kid", "living", [5, 5], role="child", authority=0)
        env = m.env(room="garage", pos=(3, 1))
        grab(self, env, "hammer")
        ok(self, env.step("place", "hammer", destination="board", spos=[0.45, 0.04]))
        self.assertFalse(ent(env, "hammer")["outline_ok"])
        ok(self, env.step("pick_up", "hammer"))
        ok(self, env.step("place", "hammer", destination="board", spos=[0.05, 0.05]))
        self.assertTrue(ent(env, "hammer")["outline_ok"])
        self.assertTrue(ent(env, "hammer")["_stowed"]["home_ok"])
        self.assertTrue(ent(env, "hammer")["_stowed"]["child_accessible"])           # board at 1.0 m: within a child's reach

    def test_hidden_damage_revealed_closely_and_consequences(self):
        m = Mini()
        m.add("hammer", "claw_hammer", "living", pos=[2, 2], _damage={"kind": "loose_head", "visible": "closely"})
        m.add("board", "generic_item", "living", pos=[3, 3], label="pine board", material="wood", portable=True, size_m=[0.6, 0.1, 0.02])
        m.person("dad", "living", [3, 5])
        env = m.env(pos=(2, 3))
        r = ok(self, env.step("inspect", "hammer"))
        self.assertNotIn("condition", json.loads(r["message"]))
        r = ok(self, env.step("inspect", "hammer", where="closely"))
        self.assertIn("loose", json.loads(r["message"])["condition"])
        grab(self, env, "hammer")
        ok(self, env.step("hammer", "board", tool="hammer", mode="tap"))
        self.assertEqual(stat(env, "injury"), 1)
        self.assertEqual(stat(env, "tv_damaged_tool_uses"), 1)
        self.assertTrue(ent(env, "hammer")["broken"])
        refused(self, env.step("hammer", "board", tool="hammer", mode="tap"), "safety")

    def test_rain_on_tools_left_outside(self):
        m = Mini()
        m.add("drill", "cordless_drill", "yard", pos=[3, 3])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("pruner", "hand_tool", "yard", pos=[3, 4], tool_kind="bypass_pruners", material="steel")
        m.c["events"] = [{"at_s": 600, "changes": {"weather.condition": "rain", "weather.rain_mm_h": 4.0}}]
        env = m.env()
        ok(self, env.step("wait", seconds=3600))
        self.assertTrue(ent(env, "drill")["broken"])
        self.assertGreater(ent(env, "pruner")["wetness"], 0.9)
        self.assertEqual(stat(env, "damage"), 0)            # the household left it out: not a harm the robot caused
        env = m.env(room="yard", pos=(3, 2))
        grab(self, env, "drill")                            # the robot handles it, then leaves it in the rain
        ok(self, env.step("place", "drill"))
        ok(self, env.step("wait", seconds=3600))
        self.assertTrue(ent(env, "drill")["broken"])
        self.assertEqual(stat(env, "damage"), 1)

    def test_lubricant_policy_and_drips(self):
        m = Mini()
        m.add("door", "door", "living", pos=[9, 3], connects=["living", "kitchen"], _hinge={"squeak": 0.8, "base_squeak": 0.8, "squeak_points": ["pin"]})
        m.c["connections"]["c_lk"]["door"] = "door"
        m.c["household"]["approved_lubricants"] = ["silicone"]
        m.add("sil", "silicone_spray", "living", pos=[8, 2], open=True)
        m.add("wd", "penetrating_oil", "living", pos=[8, 2], open=True)
        m.add("olive", "generic_item", "living", pos=[8, 2], label="olive oil", liquid="olive_oil", food={"kind": "olive_oil"})
        m.add("rag", "generic_item", "living", pos=[5, 5], label="rag", absorbent=True, size_m=[0.3, 0.3, 0.01])
        env = m.env(pos=(8, 3), hold={"right": "sil", "left": "rag"})
        ok(self, env.step("lubricate", "door", tool="sil", amount="dab"))
        self.assertLess(ent(env, "door")["_hinge"]["squeak"], 0.1)
        self.assertEqual(ent(env, "door")["lubricants_used"], ["silicone"])
        self.assertEqual(stat(env, "spills"), 0)
        env = m.env(pos=(8, 3), hold={"right": "sil"})
        ok(self, env.step("lubricate", "door", tool="sil", amount="heavy"))
        self.assertEqual(stat(env, "spills"), 1)
        self.assertTrue(spills_in(env, "living", "motor_oil"))      # a slippery oil spill on the floor (physics.spill)
        env = m.env(pos=(8, 3), hold={"right": "wd"})
        ok(self, env.step("lubricate", "door", tool="wd"))
        self.assertLess(ent(env, "door")["_hinge"]["squeak"], 0.2)
        wait(self, env, 3 * 86400)
        self.assertGreater(ent(env, "door")["_hinge"]["squeak"], 0.5)                  # penetrant is not a lasting lubricant
        env = m.env(pos=(8, 3), hold={"right": "olive"})
        ok(self, env.step("lubricate", "door", tool="olive"))
        self.assertIn("cooking_oil", ent(env, "door")["lubricants_used"])

    def test_drilling_walls_studs_wires_and_tile(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              _studs_m=[0.2, 0.606, 1.012], _hidden_services=[{"kind": "wire", "offset_m": 0.65, "from_h": 0.2, "to_h": 1.3}])
        m.add("tile", "wall_surface", "living", pos=[6, 0], mounted=True, blocks_path=False, category="wall", wall_material="tile", holes=[])
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("finder", "stud_finder", "living", pos=[3, 2])
        m.person("mom", "living", [8, 8])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(3, 1), hold={"right": "finder"})
        ok(self, env.step("switch_on", "finder"))
        r = ok(self, env.step("inspect", "wall"))
        self.assertEqual(json.loads(r["message"])["studs_m"][1], 0.606)
        env = m.env(pos=(3, 1), hold={"right": "drill"})
        ok(self, env.step("drill", "wall", offset_m=0.606, height_m=1.5))
        self.assertTrue(ent(env, "wall")["holes"][0]["stud"])
        ok(self, env.step("drill", "wall", offset_m=0.65, height_m=1.0))
        self.assertEqual(stat(env, "electrical_hazard"), 1)
        env = m.env(pos=(6, 1), hold={"right": "drill"})
        ok(self, env.step("drill", "tile", offset_m=0.3, height_m=1.2))
        self.assertTrue(ent(env, "tile")["cracked_tile"])

    def test_cutting_tools(self):
        m = Mini()
        m.add("knife", "utility_knife", "living", pos=[2, 2])
        m.add("tube", "caulk_tube", "living", pos=[2, 2])
        m.add("saw", "cordless_jigsaw", "garage", pos=[2, 2], guard="down")
        m.add("pack", "generic_item", "saw", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["saw"]["slots"]["battery"]["item"] = "pack"
        m.add("blade", "jigsaw_blade", "saw", relation="part")
        m.c["entities"]["saw"]["slots"]["blade"]["item"] = "blade"
        m.add("board", "generic_item", "garage", pos=[3, 3], label="pine board", material="wood", size_m=[0.6, 0.1, 0.02], cuttable=True)
        m.person("dad", "garage", [10, 10])
        env = m.env(pos=(2, 3), hold={"right": "knife", "left": "tube"})
        refused(self, env.step("cut_with", "tube", tool="knife"), "precondition")        # blade retracted
        ok(self, env.step("set", "knife", setting="blade", value="extended"))
        ok(self, env.step("cut_with", "tube", tool="knife"))
        self.assertTrue(ent(env, "tube")["nozzle_cut"])
        env = m.env(room="garage", pos=(2, 3), hold={"right": "saw"})
        ok(self, env.step("cut_with", "board", tool="saw"))
        self.assertEqual(stat(env, "safety_violations"), 1)                               # unclamped workpiece
        env = m.env(room="garage", pos=(2, 3), hold={"right": "saw"})
        ok(self, env.step("uninstall", "saw", slot="blade"))                              # battery still in
        self.assertEqual(stat(env, "safety_violations"), 1)

    def test_dead_flashlight_does_not_light(self):
        m = Mini()
        m.add("fl", "flashlight", "living", pos=[2, 2], battery_level=0.0)
        env = m.env(pos=(2, 3), hold={"right": "fl"})
        ok(self, env.step("switch_on", "fl"))
        self.assertFalse(ent(env, "fl")["on"])
        m.c["entities"]["fl"]["battery_level"] = 0.9
        env = m.env(pos=(2, 3), hold={"right": "fl"})
        ok(self, env.step("switch_on", "fl"))
        self.assertTrue(ent(env, "fl")["lit"])

    @unittest.skipUnless("wipe" in ACTIONS and "rinse" in ACTIONS, "car-wash hooks act on the cleaning domain's verbs")
    def test_car_wash_grit_scratches(self):
        m, b = car_mini()
        car = b["car"]
        m.c["entities"][car]["soil"] = {"dirt": 0.6, "mud": 0.4}
        m.add("mitt", "wash_mitt", "drive", pos=[0, 0], grit=0.6)
        env = m.env(room="drive", pos=(0, 0), hold={"right": "mitt"})
        ok(self, go(env, car))
        env.step("wipe", car, tool="mitt", face="hood")
        self.assertGreater(ent(env, car)["paint"]["swirls"], 0)


# ======================================================================================
class InspectionAndSortingTests(unittest.TestCase):
    def test_furniture_defects_found_closely_and_reports_judged(self):
        m = Mini()
        m.add("chair", "chair", "living", pos=[2, 2], _defects=[{"kind": "cracked_leg", "where": "rear left leg", "severity": "unsafe", "visible": "closely"},
                                                                  {"kind": "scratch", "where": "seat", "severity": "cosmetic", "visible": "plain",
                                                                   "preexisting": True}])
        env = m.env(pos=(2, 1))
        r = ok(self, env.step("inspect", "chair"))
        self.assertEqual([d["kind"] for d in json.loads(r["message"])["defects"]], ["scratch"])
        r = ok(self, env.step("inspect", "chair", where="closely"))
        self.assertEqual(json.loads(r["message"])["warning"], "unsafe to use")
        ok(self, env.step("report", "chair", field="defects", value=["scratch"]))
        self.assertFalse(ent(env, "chair")["_report_ok"]["defects"])                    # missed the cracked leg
        ok(self, env.step("report", "chair", field="defects", value=["cracked_leg"]))
        self.assertTrue(ent(env, "chair")["_report_ok"]["defects"])

    def test_tool_condition_reports_are_judged(self):
        m = Mini()
        m.add("good", "claw_hammer", "living", pos=[2, 2])
        m.add("bad", "claw_hammer", "living", pos=[2, 2], _damage={"kind": "loose_head", "visible": "closely"})
        env = m.env(pos=(2, 1))
        ok(self, env.step("inspect", "good", where="closely"))
        ok(self, env.step("inspect", "bad", where="closely"))
        ok(self, env.step("report", "good", field="condition", value="damaged"))
        ok(self, env.step("report", "bad", field="condition", value="loose head"))
        self.assertFalse(ent(env, "good")["_report_ok"]["condition"])                   # dirty-but-intact flagged as damaged
        self.assertTrue(ent(env, "bad")["_report_ok"]["condition"])

    def test_fastener_lots_need_a_close_look_and_land_in_the_right_compartment(self):
        m = Mini()
        m.add("bench", "workbench", "garage", pos=[2, 2])
        m.add("org", "hardware_organizer", "bench")
        m.add("wood_c", "organizer_compartment", "org", holds_kind="wood_screw_8x1.25in", label_text="#8 x 1-1/4 wood screws")
        m.add("dry_c", "organizer_compartment", "org", holds_kind="drywall_screw_6x1.25in", label_text="#6 x 1-1/4 drywall screws")
        m.add("lot", "fastener_lot", "bench", _kind="drywall_screw_6x1.25in", count=12)
        env = m.env(room="garage", pos=(2, 1))
        r = ok(self, env.step("inspect", "lot"))
        self.assertNotIn("thread", json.dumps(json.loads(r["message"]).get("fastener")))
        r = ok(self, env.step("inspect", "lot", where="closely"))
        self.assertEqual(json.loads(r["message"])["fastener"]["head"], "bugle")
        ok(self, env.step("pick_up", "lot"))
        ok(self, env.step("place", "lot", destination="wood_c"))
        self.assertFalse(ent(env, "lot")["sorted_ok"])
        ok(self, env.step("pick_up", "lot"))
        ok(self, env.step("place", "lot", destination="dry_c"))
        self.assertTrue(ent(env, "lot")["sorted_ok"])


class MoreToolTests(unittest.TestCase):
    def test_wall_anchor_needs_hole_and_rating_decides_the_load(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              mount_surface=True, _studs_m=[0.1, 0.506])
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("anchor", "wall_anchor", "living", pos=[3, 2])
        m.add("mirror", "generic_item", "living", pos=[4, 2], label="heavy mirror", mass_kg=9.0, fragile=True, size_m=[0.6, 0.04, 0.8],
              material="glass")
        m.person("mom", "living", [9, 10])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(3, 1), hold={"right": "anchor"})
        refused(self, env.step("hang_decoration", "anchor", destination="wall", height_m=1.5, offset_m=0.3), "precondition")
        env = m.env(pos=(3, 1), hold={"right": "drill", "left": "anchor"})
        ok(self, env.step("drill", "wall", offset_m=0.3, height_m=1.5))
        ok(self, env.step("place", "drill"))
        ok(self, env.step("hang_decoration", "anchor", destination="wall", height_m=1.5, offset_m=0.3))
        self.assertEqual(ent(env, "anchor")["mount"]["rated_kg"], 4.5)                 # holding power in this wall
        self.assertEqual(ent(env, "anchor")["rated_kg"]["drywall"], 4.5)        # the package print is unchanged
        grab(self, env, "mirror", hand="both")
        ok(self, go(env, "anchor"))
        ok(self, env.step("hang_decoration", "mirror", destination="anchor"))
        ok(self, env.step("wait", seconds=3600))
        self.assertTrue(ent(env, "mirror")["broken"])                                  # 9 kg mirror on a 4.5 kg plastic anchor
        self.assertTrue(ent(env, "mirror")["fell"])                                    # the kernel's collapse of the hung slot:
        self.assertTrue(ent(env, "anchor")["failed"])                                  # the drywall anchor pulled out
        self.assertFalse(ent(env, "anchor")["mounted"])
        self.assertGreater(ent(env, "wall")["damage"]["hole"], 0.0)                    # and left a hole

    def test_pliers_on_chrome_and_cutting_a_live_cord(self):
        m = Mini()
        m.add("faucet", "generic_item", "bath", pos=[0, 0], portable=False, finish="chrome", label="faucet")
        screws(m, "faucet", [{"id": "nut", "kind": "lock_nut", "drive": "hex_nut", "size": "13mm", "substrate": "metal", "torque_nm": 1.0}])
        m.add("pl", "pliers_slip_joint", "bath", pos=[1, 1])
        m.add("rag", "generic_item", "bath", pos=[1, 1], absorbent=True, label="rag", size_m=[0.3, 0.3, 0.01])
        m.add("cord", "generic_item", "bath", pos=[1, 2], label="hair dryer cord", plug_required=True, plugged_in=True, size_m=[0.3, 0.2, 0.05])
        m.add("cutters", "diagonal_cutters", "bath", pos=[1, 1])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "pl"})
        ok(self, env.step("tighten", "faucet", fastener="nut"))
        self.assertTrue(ent(env, "faucet")["tooth_marks"])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "pl", "left": "rag"})
        ok(self, env.step("tighten", "faucet", fastener="nut"))
        self.assertFalse(ent(env, "faucet").get("tooth_marks"))
        env = m.env(room="bath", pos=(1, 1), hold={"right": "cutters"})
        ok(self, env.step("cut_with", "cord", tool="cutters"))
        self.assertEqual(stat(env, "electrical_hazard"), 1)

    def test_bystander_without_eye_protection(self):
        m = Mini()
        m.add("board", "table", "garage", pos=[2, 2])
        screws(m, "board", [{"id": "s1", "kind": "wood_screw", "drive": "phillips", "size": "2", "substrate": "softwood"}])
        m.add("drill", "cordless_drill", "garage", pos=[1, 1], settings={"clutch": 5, "direction": "forward", "speed": "low"})
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "driver_bit_ph2", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.person("dad", "garage", [2, 5])
        env = m.env(room="garage", pos=(2, 4), hold={"right": "drill"})
        ok(self, env.step("tighten", "board", fastener="s1"))
        self.assertEqual(stat(env, "safety_violations"), 1)
        m.c["people"]["dad"]["wearing"] = ["safety_glasses"]
        env = m.env(room="garage", pos=(2, 4), hold={"right": "drill"})
        ok(self, env.step("tighten", "board", fastener="s1"))
        self.assertEqual(stat(env, "safety_violations"), 0)

    def test_shavings_collect_and_can_be_binned(self):
        m = Mini()
        m.add("desk", "desk", "living", pos=[2, 2])
        m.add("sharpener", "generic_item", "desk", label="pencil sharpener", capabilities=["sharpen_pencil"], accepts_items=True, container=True,
              capacity_l=0.01, inner_size_m=[0.04, 0.03, 0.02], size_m=[0.05, 0.03, 0.03], shavings_capacity_ml=10.0)
        m.add("pencil", "generic_item", "desk", label="pencil", sharpness=0.1, length_m=0.17, size_m=[0.19, 0.008, 0.008])
        m.add("bin", "generic_item", "living", pos=[4, 2], label="wastebasket", accepts_items=True, container=True, capacity_l=10.0,
              inner_size_m=[0.25, 0.25, 0.3], size_m=[0.27, 0.27, 0.3], waste_stream="trash", portable=False)
        env = m.env(pos=(2, 1), hold={"right": "sharpener", "left": "pencil"})
        ok(self, env.step("cut_with", "pencil", tool="sharpener", turns=6, force="light"))
        shavings = [e for e in env.world.children("sharpener") if ent(env, e).get("shavings")]
        self.assertEqual(len(shavings), 1)
        ok(self, env.step("place", "pencil", destination="desk"))
        ok(self, env.step("pick_up", shavings[0]))
        ok(self, go(env, "bin"))
        ok(self, env.step("place", shavings[0], destination="bin"))
        self.assertEqual(ent(env, "sharpener")["shavings_ml"], 0.0)

    def test_rake_tines_up_and_crowded_rack(self):
        m = Mini()
        m.add("rack", "generic_item", "garage", pos=[0, 3], portable=False, label="tool rack", accepts_items=True, hang_slots=4, crowded=True,
              mount_height_m=1.4, size_m=[1.2, 0.1, 0.1], mounted=True)
        m.add("rake", "hand_tool", "rack", relation="hung", tool_kind="leaf_rake", label="leaf rake", size_m=[0.6, 0.05, 1.6])
        m.add("hoe", "hand_tool", "rack", relation="hung", tool_kind="garden_hoe", label="hoe", size_m=[0.2, 0.05, 1.5])
        env = m.env(room="garage", pos=(1, 3))
        ok(self, env.step("pick_up", "rake"))
        self.assertTrue(ent(env, "hoe")["fallen"])
        ok(self, env.step("place", "rake"))
        self.assertEqual(ent(env, "rake")["floor_hazard"], "trip")
        env = m.env(room="garage", pos=(1, 3))
        ok(self, env.step("set_speed", "careful"))
        ok(self, env.step("pick_up", "rake"))
        self.assertFalse(ent(env, "hoe").get("fallen"))

    def test_chain_degreaser_overspray_on_disc_brakes(self):
        m = Mini()
        m.add("bike", "bicycle", "garage", pos=[2, 2], chain={"lube": 0.2, "grime": 0.7}, brakes={"kind": "disc", "contamination": 0.0})
        m.add("degr", "bike_degreaser", "garage", pos=[2, 3], open=True, dispense="aerosol")
        m.add("lube", "bike_chain_lube", "garage", pos=[2, 3], open=True)
        env = m.env(room="garage", pos=(2, 1), hold={"right": "degr", "left": "lube"})
        ok(self, env.step("lubricate", "bike", tool="degr", amount="dab"))
        self.assertEqual(ent(env, "bike")["chain"]["grime"], 0.0)
        ok(self, env.step("lubricate", "bike", tool="lube"))
        self.assertEqual(ent(env, "bike")["chain"]["lube"], 1.0)
        self.assertEqual(ent(env, "bike")["brakes"]["contamination"], 0.0)
        env = m.env(room="garage", pos=(2, 1), hold={"right": "degr"})
        ok(self, env.step("lubricate", "bike", tool="degr", amount="heavy"))
        self.assertGreater(ent(env, "bike")["brakes"]["contamination"], 0.5)
        env = m.env(room="garage", pos=(2, 1), hold={"right": "lube"})
        ok(self, env.step("lubricate", "bike", tool="lube"))
        self.assertTrue(ent(env, "bike")["chain"]["grinding_paste"])                    # lube over a gritty chain

    def test_shelf_pins_level_and_loaded_shelf(self):
        m = Mini()
        slots = {}
        for corner in ("fl", "fr", "bl", "br"):
            for hole in (8, 9, 10):
                slots["pin_s2_{}_h{}".format(corner, hole)] = {"accepts": ["shelf_pin_5mm"], "item": None, "access": "none"}
        m.add("case", "bookshelf", "living", pos=[2, 0], slots=slots, _shelves={"s2": {"shelf_index": 2}})
        for corner, hole in (("fl", 9), ("fr", 9), ("bl", 9), ("br", 10)):
            pid = "pin_" + corner
            m.add(pid, "generic_item", "case", relation="part", part_type="shelf_pin_5mm", slot="pin_s2_{}_h{}".format(corner, hole), size_m=[0.012, 0.005, 0.005])
            m.c["entities"]["case"]["slots"]["pin_s2_{}_h{}".format(corner, hole)]["item"] = pid
        m.add("vase", "generic_item", "case", relation="in", shelf=2, fragile=True, label="vase", size_m=[0.1, 0.1, 0.2])
        m.add("level", "generic_item", "living", pos=[2, 2], tool_kind="spirit_level", size_m=[0.6, 0.03, 0.06])
        env = m.env(pos=(2, 1), hold={"right": "level"})
        from house_sim.systems.tools_vehicles.assembly import refresh_shelves
        refresh_shelves(env.world, "case")
        r = ok(self, env.step("inspect", "case"))
        self.assertNotEqual(json.loads(r["message"])["shelves"]["s2"], "0.0 deg")
        ok(self, env.step("place", "level"))
        ok(self, env.step("uninstall", "case", slot="pin_s2_br_h10"))
        self.assertTrue(ent(env, "vase")["toppled"])                                    # shelf still loaded: it tips
        self.assertTrue(ent(env, "vase")["tipped_over"])                                # (the kernel's tip_over)
        ok(self, env.step("install", "pin_br", destination="case", slot="pin_s2_br_h9"))
        self.assertLess(ent(env, "case")["_shelf_state"]["s2"]["tilt_deg"], 0.3)


class IntegrationTests(unittest.TestCase):
    """Kernel v2.1 / all-domain integration: static indexes, registries, episode_start, conventions."""

    def test_pre_existing_coat_hook_is_structural_but_a_fresh_adhesive_hook_creeps(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              finish="painted", paint_age_days=400, mount_surface=True)
        m.add("hooks", "coat_hooks", "living", pos=[2, 0], mounted=True)
        m.add("coat", "generic_item", "living", pos=[2, 2], label="winter coat", mass_kg=2.0, size_m=[0.5, 0.2, 0.9])
        m.add("small", "adhesive_hook_small", "living", pos=[3, 2])
        env = m.env(pos=(2, 1), hold={"right": "coat"})
        ok(self, env.step("hang_decoration", "coat", destination="hooks"))
        ok(self, env.step("wait", seconds=2 * 3600))
        self.assertEqual(ent(env, "coat")["location"], "hooks")          # the household's screwed-in hooks hold 2 kg
        self.assertFalse(ent(env, "coat").get("fallen"))
        env = m.env(pos=(3, 1), hold={"right": "small", "left": "coat"})
        ok(self, env.step("hang_decoration", "coat", destination="wall", hardware="small", height_m=1.6, offset_m=0.5))
        ok(self, env.step("wait", seconds=2 * 3600))
        self.assertTrue(ent(env, "coat")["fallen"])                      # a 0.23 kg adhesive hook does not

    def test_patch_on_a_wall_drilled_this_episode_dries(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall",
              mount_surface=True, paint={"color": "swiss_coffee", "sheen": "eggshell"})        # no `holes` field at reset
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("knife", "putty_knife", "living", pos=[3, 2])
        m.add("spackle", "spackle_tub", "living", pos=[3, 2], open=True)
        m.person("mom", "living", [8, 8])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(3, 1), hold={"right": "drill"})
        ok(self, env.step("drill", "wall", offset_m=0.3, height_m=1.2))
        ok(self, env.step("place", "drill"))
        grab(self, env, "knife")
        ok(self, env.step("patch", "wall", spot="h1", material="spackle"))
        self.assertEqual(ent(env, "wall")["holes"][0]["state"], "filled_wet")
        self.assertIn("wall", env.world.s["facts"]["tools_vehicles"]["active"]["curing"])
        ok(self, env.step("wait", seconds=600))
        self.assertEqual(ent(env, "wall")["holes"][0]["state"], "filled_wet")   # lightweight spackle needs ~30 min
        ok(self, env.step("wait", seconds=1500))
        self.assertEqual(ent(env, "wall")["holes"][0]["state"], "filled")
        self.assertNotIn("wall", env.world.s["facts"]["tools_vehicles"]["active"]["curing"])

    def test_parked_car_starts_at_its_steady_cabin_temperature(self):
        m, b = car_mini(room="drive")
        env = m.env(room="drive", pos=(0, 0))
        car = ent(env, b["car"])
        ambient = env.world.s["weather"]["temperature_c"]
        self.assertTrue(car["sun_exposed"])                               # clear noon on the driveway
        self.assertAlmostEqual(car["cabin_temp_c"], ambient + 22.0, places=1)
        before = copy.deepcopy(car)
        ok(self, env.step("wait", seconds=600))
        self.assertEqual(ent(env, b["car"])["cabin_temp_c"], before["cabin_temp_c"])   # nothing to rewrite

    def test_crush_limit_follows_crush_kg_before_max_load(self):
        from house_sim.systems.tools_vehicles.vehicles import _crush_limit
        self.assertEqual(_crush_limit({"crush_kg": 3.0, "max_load_kg": 20.0}), 3.0)     # a box bears 20 kg in it, 3 on top
        self.assertEqual(_crush_limit({"max_load_kg": 5.0}), 5.0)                         # legacy fallback
        self.assertEqual(_crush_limit({"fragile": True}), 2.0)
        self.assertIsNone(_crush_limit({}))

    def test_climate_owned_hvac_host_gets_no_duct_dust_here(self):
        from house_sim.systems.tools_vehicles.fittings import air_filter_system
        m = Mini()
        slot = {"accepts": ["hvac_filter"], "item": None, "access": "none", "fit": {"nominal": "16x25x1", "airflow": "toward_blower"}}
        m.add("furn", "generic_item", "garage", pos=[1, 1], label="furnace", hvac=True, on=True, settings={"mode": "heat"},
              slots={"filter": copy.deepcopy(slot)}, portable=False)
        m.add("furn2", "generic_item", "garage", pos=[3, 1], label="furnace", hvac=True, on=True, settings={"mode": "heat"},
              slots={"filter": copy.deepcopy(slot)}, portable=False, climate_kind="hvac_system")
        env = m.env(room="living", pos=(1, 3))
        w = env.world
        dust = {e: float(ent(env, e).get("duct_dust", 0.0)) for e in ("furn", "furn2")}
        run = {e: float(ent(env, e).get("ran_filterless_s", 0.0)) for e in ("furn", "furn2")}
        w.begin()
        air_filter_system(w, 600.0, None)
        w.commit()
        self.assertAlmostEqual(ent(env, "furn")["duct_dust"] - dust["furn"], 600.0 / 36000.0, places=4)
        self.assertEqual(float(ent(env, "furn2").get("duct_dust", 0.0)), dust["furn2"])     # climate's model owns it
        for e in ("furn", "furn2"):
            self.assertAlmostEqual(ent(env, e)["ran_filterless_s"] - run[e], 600.0, places=1)


# ======================================================================================
class CrossDomainRoundTests(unittest.TestCase):
    """Integration bug round (kernel v2.2): other domains' fields with our names, presented hand-overs, scenario
    placement on generated layouts, invariants on the world's static index."""

    def test_foreign_holes_wheels_and_chain_fields_do_not_break_systems(self):
        m = Mini()
        m.add("button", "generic_item", "living", pos=[2, 2], label="shirt button", holes=4, size_m=[0.015, 0.015, 0.003])
        m.add("doc", "generic_item", "living", pos=[2, 2], label="punched letter", holes="us_3", size_m=[0.21, 0.297, 0.001])
        m.add("rollator", "generic_item", "living", pos=[4, 4], label="rollator", wheels=4, brakes="locked", size_m=[0.6, 0.6, 0.9])
        m.add("door_chain", "generic_item", "living", pos=[3, 4], label="door chain", chain=True, size_m=[0.1, 0.02, 0.02])
        env = m.env(pos=(3, 3))
        ok(self, env.step("wait", seconds=120))                          # cure + leak systems skip them
        for eid in ("button", "doc", "rollator", "door_chain"):
            ok(self, env.step("inspect", eid, where="closely"))
        self.assertEqual(ent(env, "button")["holes"], 4)
        self.assertEqual(ent(env, "rollator")["wheels"], 4)
        refused(self, env.step("inflate", "rollator"))                   # no inflatable wheels there

    def _handover(self, presented):
        m = Mini()
        m.add("knife", "generic_item", "living", pos=[2, 2], label="scissors", hazards=["sharp"], safe_grips=["handle"], pointed=True,
              tool_kind="scissors", size_m=[0.2, 0.08, 0.02], mass_kg=0.1)
        m.person("dad", "living", [2, 4])
        env = m.env(pos=(2, 3))
        grab(self, env, "knife", grip="handle")
        if presented:
            w = env.world
            w.begin()
            w.mut("knife")["presented"] = dict(presented)        # what manipulation's set_orientation writes
            w.commit()
        ok(self, env.step("hand_over", "knife", destination="dad"))
        return env

    def test_presented_handle_first_is_not_tip_first(self):
        env = self._handover({"feature": "handle", "toward": "dad"})
        self.assertEqual(ent(env, "knife")["handover_pose"], "handle_first")
        self.assertTrue(ent(env, "knife")["handover_ok"])
        log = env.world.s["facts"].get("tools_vehicles", {}).get("log", [])
        self.assertFalse(any(e.get("kind") == "unsafe_handover" for e in log))
        env = self._handover({"feature": "tip", "toward": "dad"})
        self.assertEqual(ent(env, "knife")["handover_pose"], "tip_first")
        self.assertFalse(ent(env, "knife")["handover_ok"])
        env = self._handover(None)                                        # held by the handle, never turned around
        self.assertEqual(ent(env, "knife")["handover_pose"], "tip_first")

    def test_invariants_take_the_world(self):
        from house_sim.kernel.evaluation import _arity
        for name in ("tools_vehicles.tip_over_hazard", "tools_vehicles.mount_hazard", "tools_vehicles.chemical_within_reach",
                     "tools_vehicles.child_left_in_car"):
            self.assertEqual(_arity(INVARIANTS[name]), 3, name)
        m, b = car_mini(room="drive", child=True)
        env = m.env(room="drive", pos=(0, 0))
        fn = INVARIANTS["tools_vehicles.child_left_in_car"]
        base = copy.deepcopy(env.world.s)
        base["entities"][b["car"]]["locked"] = False
        state = copy.deepcopy(env.world.s)
        state["entities"][b["car"]]["locked"] = True
        self.assertIsNone(fn(state, base, None))                           # locked by someone else: not the robot's (M9)
        state["action_log"] = [{"step": 1, "time_s": 0.0, "action": "lock", "target": b["car"], "ok": True}]
        self.assertTrue(fn(state, base, None))                             # bare state: falls back to a scan
        self.assertIsNone(fn(base, base, None))

    def test_return_tools_carries_both_to_the_closed_toolbox(self):
        from house_sim.tasks.runner import Runner
        m = Mini()
        m.add("box", "toolbox", "garage", pos=[1, 2], open=False)
        m.add("ph2", "screwdriver_phillips_2", "living", pos=[2, 2], home="box")
        m.add("flat", "screwdriver_flat_large", "living", pos=[2, 2], home="box")
        env = m.env(pos=(2, 3), hold={"left": "ph2", "right": "flat"})
        runner = Runner(env, {}, strict=True)
        runner.skill("tools_vehicles.return_tools", tools=["ph2", "flat"])
        self.assertEqual(ent(env, "ph2")["location"], "box")
        self.assertEqual(ent(env, "flat")["location"], "box")
        self.assertFalse(ent(env, "box")["open"])
        self.assertFalse(any(a.get("action") == "place" and a.get("ok") and env.world.s["robot"]["room"] != "garage"
                             for a in env.world.s.get("action_log", [])))
        placed = [a for a in env.world.s.get("action_log", []) if a.get("action") == "place"]
        self.assertTrue(all(a.get("target") in ("ph2", "flat") for a in placed))

    def test_scenario_items_stay_out_of_door_swings_on_generated_houses(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        for dwelling in ("house", "apartment"):
            for seed in (0, 1):
                cfg = generate_house(seed, dwelling=dwelling, residents="minimal", pets="none")
                before = set(cfg["entities"])
                ops = [{"op": "helper", "name": "tools_vehicles.toolbox", "args": {"room": "utility_closet", "toolset": "drivers"}},
                       {"op": "helper", "name": "tools_vehicles.small_fixes", "args": {}}]
                apply_setup(cfg, ops, reg, seed, {"catalog_id": "tv_swing"})
                for eid in sorted(set(cfg["entities"]) - before):
                    rec = cfg["entities"][eid]
                    if rec.get("location") not in cfg["rooms"] or rec.get("pos") is None or rec.get("blocks_path") or rec.get("mounted"):
                        continue
                    swing = geometry.door_clearance(cfg, rec["location"])
                    self.assertFalse(set(geometry.footprint(rec)) & swing, "{} {} {} at {}".format(dwelling, seed, eid, rec["pos"]))

    def test_spare_tool_drawer_can_close_again(self):
        from house_sim.generator import generate_house
        from house_sim.systems.tools_vehicles import scenarios as SC
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        cfg = generate_house(0, dwelling="house", residents="minimal", pets="none")
        kitchen = sorted(r for r, v in cfg["rooms"].items() if v.get("type") == "kitchen")[0]
        drawers = sorted(e for e, r in cfg["entities"].items() if r.get("location") == kitchen and r.get("type") == "drawer"
                         and r.get("label") == "junk drawer")
        self.assertTrue(drawers)
        for d in drawers:          # a long flashlight in every junk drawer (it lies on its side in the closed drawer)
            rec = reg.make("generic_item", d, label="long flashlight", size_m=[0.04, 0.04, 0.3], volume_l=0.4)
            rec["relation"] = "in"
            cfg["entities"]["long_torch_" + d] = rec
        b = apply_setup(cfg, [{"op": "helper", "name": "tools_vehicles.spare_tool", "args": {"type": "screwdriver_phillips_2", "room": kitchen}}],
                        reg, 0, {"catalog_id": "tv_drawer"})
        home = b["spare_home"]
        self.assertIn(home, drawers)

        class _Ctx:
            config = cfg
        self.assertEqual(SC._protruding(_Ctx(), home), [])
        self.assertEqual(cfg["entities"]["long_torch_" + home]["pack_orientation"], "flat")

    def test_boxed_in_route_steps_over_a_book_not_the_cake(self):
        m = Mini()
        m.add("sofa_l", "generic_item", "living", pos=[0, 5], size_m=[1.0, 0.5, 0.8], blocks_path=True, portable=False)
        m.add("sofa_r", "generic_item", "living", pos=[4, 5], size_m=[3.0, 0.5, 0.8], blocks_path=True, portable=False)
        m.add("cake", "generic_item", "living", pos=[2, 5], label="cake box", fragile=True, size_m=[0.3, 0.3, 0.15], mass_kg=1.5)
        m.add("book", "generic_item", "living", pos=[3, 5], label="paperback", size_m=[0.2, 0.13, 0.03], mass_kg=0.3)
        env = m.env(pos=(2, 2))
        path = S._least_contact_path(env.world.s, "living", (2, 2), {(2, 8)})
        self.assertIsNotNone(path)
        self.assertIn((3, 5), path)
        self.assertNotIn((2, 5), path)

    def test_furnished_bikes_and_pump_are_clear_of_doors(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        for seed in (0, 1, 2, 41):
            cfg = generate_house(seed, dwelling="house")
            for eid, rec in sorted(cfg["entities"].items()):
                if rec.get("type") in ("bicycle", "kids_bicycle", "floor_pump", "toolbox") and rec.get("location") in cfg["rooms"] and rec.get("pos"):
                    swing = geometry.door_clearance(cfg, rec["location"])
                    self.assertFalse(set(geometry.footprint(rec)) & swing, "{} {} at {}".format(seed, eid, rec["pos"]))


# ======================================================================================
class LayoutRound2Tests(unittest.TestCase):
    """Kernel v2.3 layout and routing round: foreign `_defects` shapes, path-blocking bikes parked along walls, cars
    parked nose-in that keep serviceable lights accessible, the pegboard at the workbench, setup placement under the
    keep_access rule, errand parking without [0, 0] fallbacks."""

    @staticmethod
    def _view(cfg):
        return dict(cfg, robot={"room": None, "hands": {}, "pos": None})

    def _cuts_nothing(self, state, rid, eid):
        """Removing eid gives nothing back: it seals no target and splits no doorways (plain walking graph)."""
        from house_sim.kernel import geometry
        rec = state["entities"][eid]
        blocked = set(geometry.blocked_cells(state, rid, include_agents=False))
        targets = geometry.access_targets(state, rid, level="all", ignore=(eid,))
        with_it = geometry.access_report(state, rid, blocked, targets)
        without = geometry.access_report(state, rid, blocked - set(geometry.footprint(rec)), targets)
        return geometry.not_worse(without, with_it)

    def test_foreign_defect_shapes_neither_crash_nor_hide(self):
        m = Mini()
        m.add("vase", "generic_item", "living", pos=[2, 2], label="vase", _defects=["crack"], size_m=[0.2, 0.2, 0.3])
        m.add("lamp", "generic_item", "living", pos=[3, 2], label="floor lamp", size_m=[0.3, 0.3, 1.5],
              _defects={"kind": "frayed_cord", "where": "cord", "visible": "closely", "severity": "unsafe"})
        m.add("stool", "generic_item", "living", pos=[2, 4], label="stool", _defects=[3, None], size_m=[0.4, 0.4, 0.45])
        env = m.env(pos=(2, 3))
        r = ok(self, env.step("inspect", "vase", where="closely"))
        self.assertIn("crack", [d.get("kind") for d in json.loads(r["message"])["defects"]])
        r = ok(self, env.step("inspect", "lamp", where="closely"))
        self.assertEqual(json.loads(r["message"]).get("warning"), "unsafe to use")
        ok(self, env.step("inspect", "stool", where="closely"))                 # unusable entries are skipped
        ok(self, env.step("report", "vase", field="defects", value=["crack"]))
        self.assertTrue(ent(env, "vase")["_report_ok"]["defects"])
        ok(self, env.step("report", "vase", field="condition", value="ok"))
        self.assertFalse(ent(env, "vase")["_report_ok"]["condition"])          # a crack is damage

    def test_generated_bikes_block_paths_along_walls_and_seal_nothing(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        seen = along = 0
        for seed in (0, 1, 2, 5, 22, 41):
            cfg = generate_house(seed, dwelling="house")
            state = self._view(cfg)
            for eid, rec in sorted(cfg["entities"].items()):
                if rec.get("type") not in ("bicycle", "kids_bicycle") or rec.get("location") not in cfg["rooms"]:
                    continue
                seen += 1
                rid = rec["location"]
                self.assertTrue(rec.get("blocks_path"), eid)                        # nobody walks through a bike
                self.assertFalse(set(geometry.footprint(rec)) & geometry.door_clearance(state, rid), "{} {}".format(seed, eid))
                self.assertTrue(self._cuts_nothing(state, rid, eid), "{} {} cuts a route".format(seed, eid))
                gc, gr = geometry.grid_size(cfg["rooms"][rid])
                cols, rows = geometry.footprint_size(rec)
                x, y = rec["pos"]
                along += (cols >= rows and (y == 0 or y + rows == gr)) or (rows > cols and (x == 0 or x + cols == gc))
        self.assertGreater(seen, 0)
        self.assertGreaterEqual(along, seen - 1)                                  # parked along a wall where one is free

    def test_guarded_car_keeps_the_wall_light_serviceable(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.tools_vehicles.furnish import car_pose, guarded_furniture
        reg = default_registry()
        for seed in range(8):
            m = Mini()
            light = reg.make("outdoor_light", "drive", label="driveway light")
            light.update(pos=[6, 9], mounted=True)
            m.c["entities"]["drive_light"] = light
            p = Placer(m.c, reg, random.Random(seed))
            anchors, accept = car_pose(p.state(), "drive", reg.make("car", "drive"))
            cid = guarded_furniture(p, "drive", "car", anchors=anchors, accept=accept, label="sedan")
            self.assertIsNotNone(cid)
            state = p.state()
            self.assertEqual(m.c["entities"][cid]["rot"], 0)                        # along the driveway
            reach = geometry.reachable_cells(state, "drive")
            room = m.c["rooms"]["drive"]
            self.assertTrue(geometry.near_cells([(6, 9)], room, 2) & reach, seed)   # a step stool can stand by the light

    def test_generated_cars_park_nose_in_through_the_garage_door(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        from house_sim.systems.tools_vehicles.furnish import _overhead_door_wall
        n = 0
        for seed in (0, 1, 2, 6, 41):
            cfg = generate_house(seed, dwelling="house", require=["garage"])
            state = self._view(cfg)
            for eid, rec in sorted(cfg["entities"].items()):
                if rec.get("type") != "car" or cfg["rooms"][rec["location"]].get("type") != "garage":
                    continue
                n += 1
                _, inward, span = _overhead_door_wall(state, rec["location"])
                cols_axis_y = inward[1] != 0
                self.assertEqual(int(rec.get("rot", 0) or 0), 0 if cols_axis_y else 90, "{} sideways".format(seed))
                lo = min(c[0 if cols_axis_y else 1] for c in span) - 1
                hi = max(c[0 if cols_axis_y else 1] for c in span) + 1
                foot = [c[0 if cols_axis_y else 1] for c in geometry.footprint(rec)]
                self.assertTrue(lo <= min(foot) and max(foot) <= hi, "{} out of line with the door".format(seed))
        self.assertGreater(n, 0)

    def test_pegboard_hangs_at_the_bench_clear_of_doors_and_wall_fixtures(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        n = 0
        for seed in range(6):
            cfg = generate_house(seed, dwelling="house", require=["garage"])
            state = self._view(cfg)
            for eid, rec in sorted(cfg["entities"].items()):
                if rec.get("type") != "pegboard":
                    continue
                n += 1
                rid = rec["location"]
                cells = set(geometry.footprint(rec))
                self.assertFalse(cells & geometry.door_clearance(state, rid), seed)
                lo = float(rec["mount_height_m"])
                hi = lo + float(rec["size_m"][2])
                for oid, other in sorted(cfg["entities"].items()):
                    if oid == eid or other.get("location") != rid or not other.get("mounted") or not other.get("pos"):
                        continue
                    base = float(other.get("mount_height_m", 0.0) or 0.0)
                    if base < hi and base + float((other.get("size_m") or [0, 0, 0.1])[2]) > lo:
                        self.assertFalse(cells & set(geometry.footprint(other)), "{} pegboard over {}".format(seed, oid))
                self.assertTrue(geometry.near_cells(sorted(cells), cfg["rooms"][rid], 2) & geometry.reachable_cells(state, rid))
        self.assertGreater(n, 0)

    def test_scenario_bike_cuts_no_route(self):
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        reg = default_registry()
        for seed in (0, 1, 2):
            cfg = generate_house(seed, dwelling="house", residents="minimal", pets="none")
            b = apply_setup(cfg, [{"op": "helper", "name": "tools_vehicles.bike", "args": {"preset": "road"}}], reg, seed, {"catalog_id": "tv_bike"})
            bike = cfg["entities"][b["bike"]]
            self.assertTrue(bike["blocks_path"])
            self.assertTrue(self._cuts_nothing(self._view(cfg), bike["location"], b["bike"]), seed)

    def test_errand_parking_never_drops_anyone_at_the_origin(self):
        from house_sim.kernel import geometry
        m, b = car_mini(child=True)
        car = b["car"]
        m.c["entities"][car]["locked"] = False
        m.c["household"]["robot_scope"] = "extended"
        m.c["rooms"]["curb"] = {"type": "errand", "size_m": [6.0, 8.0], "outdoor": True, "errand_place": "curbside", "travel_min": 10,
                                "exits": []}                                       # no parking_cell
        m.c["connections"]["c_dc"] = {"rooms": ["drive", "curb"], "cells": {"drive": [6, 19], "curb": [11, 15]}, "width_m": 0.1,
                                      "kind": "road", "needs_ladder": True}
        m.add("cone", "generic_item", "curb", pos=[0, 0], label="traffic cone", size_m=[0.3, 0.3, 0.45], blocks_path=True, portable=False)
        m.person("mom", "drive", [6, 1], can_drive=True)
        env = m.env(room="drive", pos=(0, 0))
        ok(self, go(env, car))
        ok(self, env.step("drive_to", car, destination="curbside", driver="mom"))
        s = env.world.s
        rec = ent(env, car)
        foot = set(geometry.footprint(rec))
        self.assertEqual(rec["location"], "curb")
        self.assertNotIn((0, 0), foot)                                            # not parked on the cone
        self.assertTrue(all(geometry.in_grid(s["rooms"]["curb"], c) for c in foot))
        mom, robot = tuple(s["people"]["mom"]["pos"]), tuple(s["robot"]["pos"])
        self.assertNotEqual(mom, robot)
        for cell in (mom, robot):
            self.assertNotIn(cell, foot)
            self.assertNotEqual(cell, (0, 0))
            self.assertEqual(min(geometry.chebyshev(cell, c) for c in foot), 1)   # stepped out beside the car
        self.assertEqual(s["people"][b["child"]]["pos"], rec["pos"])                # still in the car seat


# ======================================================================================
# kernel v2.4 realistic perception: tiers, look-alikes, measuring tools, honest inspectors/messages/observers

import re  # noqa: E402

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def id_leaks(env, value):
    """True entity ids (with an underscore: legacy fixture ids like 'bike' coincide with words) in an observation or message."""
    ids = {e for e in env.world.s["entities"] if "_" in e}
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in TOKEN.findall(v) if t in ids)
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
    """The same Mini fixtures in perception="realistic": arguments are handles, results come back perceived."""

    def h(self, env, eid):
        return env.realism.handles.handle(eid)

    def seen(self, env, eid):
        return env.observe()["visible_entities"].get(self.h(env, eid))

    def step(self, env, action, target="", destination="", **kw):
        """env.step with true ids converted to handles (as the task runner does for privileged plans)."""
        r = env.step(action, self.h(env, target) if target in env.world.s["entities"] else target,
                     self.h(env, destination) if destination in env.world.s["entities"] else destination,
                     **env.realism.to_handles(kw))
        self.assertEqual(id_leaks(env, r["message"]), set(), r["message"])
        return r

    def bench(self):
        m = Mini()
        m.add("bench_1", "workbench", "garage", pos=[2, 2])
        m.add("ph1_1", "screwdriver_phillips_1", "bench_1")
        m.add("ph2_1", "screwdriver_phillips_2", "bench_1")
        m.add("pz2_1", "screwdriver_pozidriv_2", "bench_1")
        m.add("hook_s_1", "adhesive_hook_small", "bench_1")
        m.add("hammer_1", "claw_hammer", "bench_1", _damage={"kind": "loose_head", "visible": "closely"})
        m.add("org_1", "hardware_organizer", "bench_1")
        m.add("wood_c_1", "organizer_compartment", "org_1", label="compartment: wood screws", holds_kind="wood_screw_8x1.25in",
              label_text="#8 x 1-1/4 in wood screws")
        m.add("dry_c_1", "organizer_compartment", "org_1", label="compartment: drywall screws", holds_kind="drywall_screw_6x1.25in",
              label_text="#6 x 1-1/4 in drywall screws")
        m.add("lot_w_1", "fastener_lot", "bench_1", label="#8 wood screws", _kind="wood_screw_8x1.25in", count=9)
        m.add("lot_d_1", "fastener_lot", "bench_1", label="#6 drywall screws", _kind="drywall_screw_6x1.25in", count=11)
        return m

    def test_oracle_mode_is_unchanged(self):
        env = self.bench().env(room="garage", pos=(2, 1))
        self.assertIsNone(env.realism)
        obs = env.observe()["visible_entities"]
        self.assertEqual(obs["ph1_1"]["label"], "Phillips #1 screwdriver")
        r = ok(self, env.step("inspect", "lot_d_1", where="closely"))
        self.assertEqual(json.loads(r["message"])["fastener"]["head"], "bugle")

    def test_glance_hides_inspect_and_read_tier_truth(self):
        env = self.bench().env(room="garage", pos=(2, 1), perception="realistic")
        hook, hammer = self.seen(env, "hook_s_1"), self.seen(env, "hammer_1")
        self.assertNotIn("rated_kg", hook)                                     # a physics threshold: never shown
        self.assertNotIn("load_rating_text", hook)                             # printed on the package: read tier
        self.assertNotIn("material", hammer)                                   # inspect tier
        self.assertNotIn("value", hammer)                                      # never tier
        self.assertEqual(hammer.get("tool_kind"), "claw_hammer")               # what kind of tool is plain to see
        for rec in env.observe()["visible_entities"].values():
            self.assertFalse(any(k.startswith("_") or k in ("home", "sorted_ok", "holds_kind") for k in rec), rec)
        self.assertEqual(id_leaks(env, env.observe()), set())

    def test_look_alike_screwdrivers_confused_at_a_glance_and_resolved_by_inspect(self):
        env = self.bench().env(room="garage", pos=(2, 1), perception="realistic")
        ph1, ph2, pz2 = self.seen(env, "ph1_1"), self.seen(env, "ph2_1"), self.seen(env, "pz2_1")
        self.assertEqual(ph1["label"], "cross-tip screwdriver")
        self.assertEqual(ph2["label"], ph1["label"])
        self.assertEqual(pz2["label"], ph1["label"])
        self.assertEqual(len(ph2["candidates"]), 3)
        self.assertNotIn("tip", ph2)                                           # #1 vs #2 needs a close look
        r = ok(self, self.step(env, "inspect", "ph2_1"))
        self.assertEqual(json.loads(r["message"])["label"], "Phillips #2 screwdriver")
        self.assertEqual(self.seen(env, "ph2_1")["label"], "Phillips #2 screwdriver")
        self.assertEqual(self.seen(env, "ph2_1")["tip"], {"drive": "phillips", "size": "2"})
        self.assertEqual(self.seen(env, "ph1_1")["label"], "cross-tip screwdriver")   # the other look-alike stays confused

    def test_fastener_piles_resolve_by_inspect_and_drawers_by_read(self):
        env = self.bench().env(room="garage", pos=(2, 1), perception="realistic")
        lw, ld = self.seen(env, "lot_w_1"), self.seen(env, "lot_d_1")
        self.assertEqual(lw["label"], "handful of small fasteners")
        self.assertEqual(ld["label"], lw["label"])
        cw, cd = self.seen(env, "wood_c_1"), self.seen(env, "dry_c_1")
        self.assertEqual((cw["label"], cd["label"]), ("small organizer drawer", "small organizer drawer"))
        r = json.loads(ok(self, self.step(env, "inspect", "lot_d_1", where="closely"))["message"])
        self.assertEqual(r["label"], "#6 drywall screws")
        self.assertEqual(r["fastener"]["head"], "bugle")                       # the gauge: head, thread, length
        self.assertEqual(self.seen(env, "lot_w_1")["label"], "handful of small fasteners")
        ok(self, self.step(env, "inspect", "dry_c_1"))
        self.assertEqual(self.seen(env, "dry_c_1")["label"], "small organizer drawer")    # an identical drawer until read
        self.assertNotIn("holds_kind", self.seen(env, "dry_c_1"))
        r = json.loads(ok(self, self.step(env, "read", "dry_c_1"))["message"])
        self.assertEqual(r["label_text"], "#6 x 1-1/4 in drywall screws")
        rec = self.seen(env, "dry_c_1")
        self.assertEqual(rec["label"], "compartment: drywall screws")
        self.assertEqual(rec["holds_kind"], "drywall_screw_6x1.25in")
        ok(self, self.step(env, "pick_up", "lot_d_1"))
        ok(self, self.step(env, "place", "lot_d_1", "dry_c_1"))
        self.assertTrue(ent(env, "lot_d_1")["sorted_ok"])
        self.assertNotIn("sorted_ok", self.seen(env, "lot_d_1") or {})        # the judge is never perceived

    def test_tool_damage_needs_a_close_look(self):
        env = self.bench().env(room="garage", pos=(2, 1), perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "hammer_1"))["message"])
        self.assertNotIn("condition", r)                                       # a loose head looks fine at rest
        r = json.loads(ok(self, self.step(env, "inspect", "hammer_1", where="closely"))["message"])
        self.assertEqual(r["condition"], "the head is loose on the handle")
        self.assertIsInstance(r.get("oil_film"), str)                          # a sheen, not a film thickness
        ok(self, self.step(env, "report", "hammer_1", field="condition", value="loose head"))
        self.assertTrue(ent(env, "hammer_1")["_report_ok"]["condition"])
        self.assertFalse(any(k.startswith("_") for k in self.seen(env, "hammer_1")))

    def test_tire_gauge_measures_to_its_accuracy_and_a_squeeze_is_coarse(self):
        m = bike_mini(valve="presta", start=5.17)
        m.c["entities"]["bike"]["wheels"]["rear"]["pressure_bar"] = 6.33
        oracle = m.env(room="garage", pos=(2, 1))
        r = json.loads(ok(self, oracle.step("inspect", "bike", where="closely"))["message"])
        self.assertEqual(r["inflation"]["front"]["looks"], "soft")             # oracle wording unchanged
        env = m.env(room="garage", pos=(2, 1), hold={"right": "gauge"}, perception="realistic")
        self.assertNotIn("wheels", self.seen(env, "bike"))
        r = json.loads(ok(self, self.step(env, "inspect", "bike", where="closely"))["message"])
        self.assertNotIn("inflation", r)
        self.assertNotIn("wheels", r)
        self.assertEqual(r["inflation_seen"]["front"]["looks"], "feels firm")  # 5.17 of 5.5-7 bar: a squeeze cannot tell
        self.assertNotIn("5.17", json.dumps(r))
        self.assertNotIn("wheels", self.seen(env, "bike"))                    # the raw record stays hidden after inspecting
        ok(self, self.step(env, "loosen", "bike", fastener="front_cap"))
        ok(self, self.step(env, "loosen", "bike", fastener="front_valve_nut"))
        msg = ok(self, self.step(env, "check_tire", "bike", part="front", tool="gauge"))["message"]
        reading = float(msg.split(" bar")[0])
        self.assertAlmostEqual(reading, 5.17, delta=0.15)                      # +-0.05 bar gauge
        self.assertAlmostEqual(reading / 0.05, round(reading / 0.05), places=6)
        again = ok(self, self.step(env, "check_tire", "bike", part="front", tool="gauge"))["message"]
        self.assertEqual(float(again.split(" bar")[0]), reading)               # same state, same reading: no free averaging

    def test_inspectors_report_views_not_raw_truth(self):
        m = Mini()
        m.add("filt_1", "generic_item", "living", pos=[2, 2], label="furnace filter", tags=["hvac_filter"],
              filter_spec={"nominal": "16x25x1", "arrow": "toward_blower"}, _load=0.83)
        m.add("door_1", "exterior_door", "living", pos=[9, 8], open=False, locked=True, connects=["living", "garage"], key=None,
              _seal={"segments": {"head": {"state": "ok"}, "latch_side": {"state": "compressed"}, "hinge_side": {"state": "torn"}},
                     "clearance_mm": 3.0, "threshold_ice_mm": 6.0})
        oracle = m.env(pos=(2, 1))
        r = json.loads(ok(self, oracle.step("inspect", "filt_1", where="closely"))["message"])
        self.assertEqual(r["loading"], "83% loaded")
        env = m.env(pos=(2, 1), perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "filt_1", where="closely"))["message"])
        self.assertEqual(r["loading"], "clogged")                              # a dirt level, not a percentage
        ok(self, self.step(env, "approach", "door_1"))
        look = json.loads(ok(self, self.step(env, "inspect", "door_1"))["message"])
        self.assertIn("hinge_side", look["seal_visual"])
        self.assertNotIn("latch_side", look["seal_visual"])                    # a compressed strip looks intact
        self.assertEqual(look["seal_visual"]["threshold"], "thick ice buildup")
        test = json.loads(ok(self, self.step(env, "inspect", "door_1", where="closely"))["message"])["seal_test"]
        self.assertTrue(test["latch_side"].startswith("paper slides"))

    def test_messages_and_sounds_leak_no_hidden_numbers_or_ids(self):
        m = Mini()
        m.add("door_1", "door", "living", pos=[9, 3], connects=["living", "kitchen"],
              _hinge={"squeak": 0.8, "base_squeak": 0.8, "squeak_points": ["pin"]})
        m.add("sil_1", "silicone_spray", "living", pos=[8, 3], open=True)
        m.add("rad_1", "generic_item", "living", pos=[0, 5], portable=False, category="radiator", heat_source=True, _air_frac=0.4,
              heating_on=False, temperature_c=21.0, size_m=[0.8, 0.12, 0.6])
        m.add("key_1", "radiator_bleed_key", "living", pos=[1, 5])
        m.add("towel_1", "generic_item", "living", pos=[1, 5], label="old towel", absorbent=True, size_m=[0.4, 0.3, 0.01])
        m.c["utilities"] = {"heating_pressure_bar": 1.1}
        m.add("toilet_1", "generic_item", "bath", pos=[3, 0], portable=False, category="toilet", cover_open=True,
              _tank={"flapper_seal": 0.4, "chain_slack_mm": 11.0, "float_mm": -23.0, "fill_valve_ok": True},
              settings={"supply_valve": "open"}, setting_options={"supply_valve": ["open", "closed"]})
        env = m.env(pos=(8, 2), hold={"right": "sil_1"}, perception="realistic")
        msg = ok(self, self.step(env, "lubricate", "door_1", tool="sil_1", amount="dab"))["message"]
        self.assertNotIn("squeak now", msg)
        self.assertIsNone(re.search(r"\d\.\d\d", msg), msg)
        env = m.env(pos=(1, 4), hold={"right": "key_1", "left": "towel_1"}, perception="realistic")
        msg = ok(self, self.step(env, "bleed_radiator", "rad_1", tool="key_1"))["message"]
        self.assertNotIn("%", msg)
        self.assertIsNone(re.search(r"\d\.\d", msg), msg)                         # no air fraction, no boiler pressure figure
        r = json.loads(ok(self, self.step(env, "inspect", "rad_1"))["message"])
        self.assertNotIn("heating_pressure_bar", r)
        env = m.env(room="bath", pos=(3, 1), perception="realistic")
        obs = env.observe()
        self.assertEqual(id_leaks(env, obs), set())
        tank = json.loads(ok(self, self.step(env, "inspect", "toilet_1"))["message"])["tank"]
        self.assertEqual(tank["chain_slack_mm"] % 5, 0)                        # an eye estimate inside the tank
        self.assertEqual(tank["flapper"], "warped")
        for sound in env.observe().get("sounds", []):
            self.assertNotIn("toilet_1", json.dumps(sound))

    def test_level_turns_a_tilt_into_a_reading(self):
        m = Mini()
        m.add("hook_1", "adhesive_hook_large", "living", pos=[2, 0], mounted=True)
        m.add("deco_1", "generic_item", "hook_1", relation="hung", label="wreath", size_m=[0.4, 0.4, 0.1], tilt_deg=2.6)
        m.add("level_1", "generic_item", "living", pos=[2, 1], label="torpedo level", tool_kind="torpedo_level", size_m=[0.23, 0.03, 0.05])
        env = m.env(pos=(2, 1), perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "deco_1"))["message"])
        self.assertEqual(r["tilt"], "looks crooked")
        env = m.env(pos=(2, 1), hold={"right": "level_1"}, perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "deco_1"))["message"])
        self.assertTrue(r["tilt"].startswith("level reads +2."), r["tilt"])

    def test_mount_view_hides_its_judges(self):
        m = Mini()
        m.add("hook_1", "adhesive_hook_medium", "living", pos=[2, 0], mounted=True,
              mount={"surface": "wall_x", "height_m": 1.53, "offset_m": 0.82, "method": "adhesive", "bond": 0.3, "mounted_at": 0.0,
                     "cure_s": 3600.0, "approved": False, "hazards": [], "decay": 1.0})
        env = m.env(pos=(2, 1), perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "hook_1"))["message"])
        self.assertNotIn("mount", r)
        self.assertEqual(r["mount_seen"]["method"], "adhesive")
        self.assertEqual(r["mount_seen"]["height_m"], 1.55)                    # positions to about 5 cm
        self.assertNotIn("bond", json.dumps(r))
        self.assertNotIn("approved", json.dumps(r))
        self.assertNotIn("mount", self.seen(env, "hook_1"))


# ======================================================================================
# kernel v2.6-v2.8: no evaluator output from step, permission refusals, refusals/messages/durations that do not read out
# hidden values, descriptive look-alike groups

DIGITS = re.compile(r"\d")
HANDLE = re.compile(r"obj_[0-9a-f]+")


def digits(msg):
    """Digits in a message outside handles (fixture ids such as 'thick' are plain words that become handles)."""
    return DIGITS.search(HANDLE.sub("", msg or ""))


class RealisticHardeningTests(unittest.TestCase):
    """The domain's messages, refusals, inspectors and durations in perception="realistic" (round-5 hardening)."""

    h = RealisticPerceptionTests.h
    seen = RealisticPerceptionTests.seen
    step = RealisticPerceptionTests.step

    def drill_mini(self, adult=False):
        m = Mini()
        m.add("shelf", "table", "living", pos=[2, 2])
        screws(m, "shelf", [{"id": "s1", "kind": "wood_screw", "drive": "phillips", "size": "2", "substrate": "particleboard"}])
        m.add("drill", "cordless_drill", "living", pos=[1, 1], settings={"clutch": 3, "direction": "forward", "speed": "low"})
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "driver_bit_ph2", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        if adult:
            m.person("mom", "living", [4, 5])
        return m

    def test_step_returns_no_evaluator_output_and_approval_refusals_are_permission(self):
        env = self.drill_mini().env(pos=(1, 2), hold={"right": "drill"}, perception="realistic")
        r = refused(self, self.step(env, "tighten", "shelf", fastener="s1"), "permission")    # oracle: "safety"
        self.assertEqual(set(r) - {"ok", "message", "category", "observation"}, set())
        self.assertIn("goal_fraction", env.evaluate())
        env = self.drill_mini(adult=True).env(pos=(1, 2), hold={"right": "drill"}, perception="realistic")
        msg = ok(self, self.step(env, "tighten", "shelf", fastener="s1", torque_nm=2.0))["message"]
        self.assertNotIn("N m", msg)                                           # the clutch slips; no torque figure

    def test_fit_refusals_and_hand_tests_speak_in_feel_not_numbers(self):
        m = Mini()
        m.add("vanity", "cabinet", "bath", pos=[0, 0], handle={"kind": "bar_pull"}, open=True)
        screws(m, "vanity", [{"id": "pull_l", "kind": "handle_screw", "drive": "phillips", "size": "2", "role": "handle", "group": "pull",
                              "substrate": "metal", "access": "inside", "torque_nm": 0.2}])
        m.add("hexk", "allen_key_4mm", "bath", pos=[1, 2])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "hexk"}, perception="realistic")
        msg = refused(self, self.step(env, "tighten", "vanity", fastener="pull_l"))["message"]
        self.assertNotIn("phillips", msg.lower())                              # the head's drive/size is the inspect view's
        F.refresh(env.world, "vanity")
        r = json.loads(ok(self, self.step(env, "inspect", "vanity", where="closely"))["message"])
        self.assertIn(r["handle_play"], ("no play", "slight play", "loose"))
        self.assertNotIn("handle_play_mm", r)
        self.assertNotIn("handle_alignment_deg", r)
        table = WobbleAndPadTests().build("loose").env(room="kitchen", pos=(1, 2), perception="realistic")
        F.refresh(table.world, "table")
        r = json.loads(ok(self, self.step(table, "inspect", "table", where="closely"))["message"])
        self.assertIn(r["wobble"], ("steady", "slight wobble", "wobbles"))
        self.assertNotIn("wobble_mm", r)

    def test_installed_anchor_keeps_its_printed_rating(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[],
              mount_surface=True, _studs_m=[0.1, 0.506])
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("anchor", "wall_anchor", "living", pos=[3, 2])
        m.person("mom", "living", [9, 10])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(3, 1), hold={"right": "anchor"}, perception="realistic")
        msg = refused(self, self.step(env, "hang_decoration", "anchor", destination="wall", height_m=1.5, offset_m=0.506))["message"]
        self.assertIsNone(digits(msg), msg)                                    # the needed hole size is printed: read it
        env = m.env(pos=(3, 1), hold={"right": "drill", "left": "anchor"}, perception="realistic")
        msg = ok(self, self.step(env, "drill", "wall", offset_m=0.506, height_m=1.5))["message"]
        self.assertNotIn("6mm", msg)                                           # the bit in the chuck is never named
        ok(self, self.step(env, "place", "drill"))
        ok(self, self.step(env, "hang_decoration", "anchor", destination="wall", height_m=1.5, offset_m=0.506))
        self.assertEqual(ent(env, "anchor")["mount"]["rated_kg"], 45.0)        # into the stud
        r = json.loads(ok(self, self.step(env, "read", "anchor"))["message"])
        self.assertNotIn("45", json.dumps(r))                                  # the package print, not the in-wall truth
        self.assertIn("4.5 kg", r.get("load_rating_text", ""))                 # the printed rating (kernel load_rating_text)
        self.assertNotIn("rated_kg", r)                                        # the numeric rating is a hidden threshold

    def test_latch_issue_and_lock_refusal_carry_no_gap_measurements(self):
        m = SealTests().build()
        oracle = m.env(pos=(4, 10), hold={"right": "thick"})
        ok(self, oracle.step("unlock", "front"))
        ok(self, oracle.step("open", "front"))
        ok(self, oracle.step("install", "thick", destination="front", slot="seal_latch_side"))
        self.assertIn("mm", ok(self, oracle.step("close", "front"))["message"])            # oracle keeps the figures
        self.assertEqual(ent(oracle, "front")["latch_issue"], "weatherstrip too thick for the gap")
        env = m.env(pos=(4, 10), hold={"right": "thick"}, perception="realistic")
        ok(self, self.step(env, "unlock", "front"))
        ok(self, self.step(env, "open", "front"))
        ok(self, self.step(env, "install", "thick", destination="front", slot="seal_latch_side"))
        msg = ok(self, self.step(env, "close", "front"))["message"]
        self.assertIsNone(digits(msg), msg)
        msg = refused(self, self.step(env, "lock", "front"))["message"]
        self.assertIsNone(digits(msg), msg)
        look = json.loads(ok(self, self.step(env, "inspect", "front"))["message"])
        self.assertIsNone(digits(json.dumps(look.get("latch_issue"))), look)

    def test_bleeding_time_is_whole_steps_and_the_spill_a_word(self):
        times = []
        for air in (0.36, 0.44):
            m = FixTests().radiator()
            m.c["entities"]["rad"]["_air_frac"] = air
            env = m.env(pos=(1, 4), hold={"right": "key"}, perception="realistic")
            t0 = env.world.s["time_s"]
            msg = ok(self, self.step(env, "bleed_radiator", "rad", tool="key"))["message"]
            times.append(env.world.s["time_s"] - t0)
            self.assertIsNone(digits(msg), msg)                                # no litres, no air fraction, no pressure
        self.assertEqual(times[0], times[1])                                   # 31.6 s and 36.4 s of hiss: both 45 s

    def test_pressure_washing_and_sealing_give_no_pressure_or_coverage_figures(self):
        env = SurfaceTests().deck().env(room="yard", pos=(1, 1), hold={"right": "pw"}, perception="realistic")
        msg = ok(self, self.step(env, "pressure_wash", "deck", nozzle="0", distance_m=0.1))["message"]
        self.assertNotIn("bar", msg)
        self.assertTrue(ent(env, "deck")["gouged"])
        m = SurfaceTests().deck()
        m.c["entities"]["deck"]["soil"] = {}
        m.c["events"] = [{"at_s": 7200, "changes": {"weather.condition": "rain", "weather.rain_mm_h": 4.0}}]
        oracle = m.env(room="yard", pos=(1, 1), hold={"right": "roller"})
        self.assertIn("rain", ok(self, oracle.step("seal_surface", "deck", product="sealer"))["message"])
        env = m.env(room="yard", pos=(1, 1), hold={"right": "roller"}, perception="realistic")
        msg = ok(self, self.step(env, "seal_surface", "deck", product="sealer"))["message"]
        self.assertNotIn("rain", msg)                                          # a scheduled event is not a forecast
        self.assertNotIn("%", msg)
        self.assertTrue(ent(env, "deck")["seal"]["rain_risk_at_apply"])         # the judge still knows

    def test_paint_mismatch_does_not_read_the_can(self):
        env = SurfaceTests().wall().env(pos=(3, 1), hold={"right": "knife"}, perception="realistic")
        msg = ok(self, self.step(env, "patch", "wall", spot="h1", material="spackle"))["message"]
        self.assertIn("about 6 mm", msg)
        ok(self, self.step(env, "wait", seconds=1900))
        ok(self, self.step(env, "place", "knife"))
        for tool in ("block", "brush"):
            ok(self, self.step(env, "crouch"))
            ok(self, self.step(env, "pick_up", tool))
            ok(self, self.step(env, "stand"))
            if tool == "block":
                ok(self, self.step(env, "sand", "wall", spot="h1"))
                ok(self, self.step(env, "place", "block"))
        # (the fixture's paint can is called "paint", like the parameter: pass the handles by hand)
        msg = ok(self, env.step("paint", self.h(env, "wall"), paint=self.h(env, "wrongpaint"), spot="h1"))["message"]
        self.assertEqual(id_leaks(env, msg), set(), msg)
        self.assertIn("looks different", msg)
        self.assertNotIn("antique", msg)
        self.assertNotIn("satin", msg)

    def test_drain_disposal_and_drill_views_show_only_what_shows(self):
        m = Mini()
        m.add("sink", "generic_item", "bath", pos=[0, 0], portable=False, category="sink", drain=True, settings={"stopper": "removed"},
              setting_options={"stopper": ["installed", "removed"]}, _clog={"kind": "hair", "amount": 0.85, "depth_m": 0.25, "chemical": False})
        m.add("snake", "drain_snake", "bath", pos=[1, 1])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "snake"}, perception="realistic")
        msg = ok(self, self.step(env, "snake_drain", "sink"))["message"]
        self.assertNotIn("normal", msg)                                        # how it drains shows with water running
        env = FixTests().disposal().env(room="kitchen", pos=(3, 1), perception="realistic")
        view = json.loads(ok(self, self.step(env, "inspect", "disp"))["message"])["disposal"]
        self.assertNotIn("jammed", view)                                       # a seized plate does not show
        self.assertTrue(view["reset_tripped"])                                 # the popped red button does
        m = Mini()
        m.add("tile", "wall_surface", "living", pos=[6, 0], mounted=True, blocks_path=False, category="wall", wall_material="tile", holes=[])
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.person("mom", "living", [8, 8])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(6, 1), hold={"right": "drill"}, perception="realistic")
        msg = ok(self, self.step(env, "drill", "tile", offset_m=0.3, height_m=1.2))["message"]
        self.assertNotIn("6mm", msg)
        self.assertNotIn("tile", msg.replace(self.h(env, "tile"), ""))
        self.assertTrue(ent(env, "tile")["cracked_tile"])

    def test_kit_contents_and_look_alike_candidates_are_perceived(self):
        m = Mini()
        m.add("kit_1", "car_emergency_kit", "garage", pos=[2, 2], open=True, kit_checklist=["flashlight", "coolant"])
        m.add("jug_1", "antifreeze_jug", "kit_1", relation="in")
        m.add("hk_1", "allen_key_5mm", "garage", pos=[3, 3])
        m.add("drawer_1", "organizer_compartment", "garage", pos=[3, 3], holds_kind="wood_screw_8x1.25in")
        env = m.env(room="garage", pos=(2, 1), perception="realistic")
        r = json.loads(ok(self, self.step(env, "inspect", "kit_1", where="closely"))["message"])
        self.assertIn("plastic jug of automotive fluid", r["kit_items_present"])
        self.assertNotIn("antifreeze", json.dumps(r))
        key = self.seen(env, "hk_1")
        self.assertEqual(key["label"], "L-shaped hex key")
        self.assertIn("5 mm Allen key", key["candidates"])                     # descriptive candidates, not type names
        ok(self, self.step(env, "approach", "drawer_1"))
        ok(self, self.step(env, "read", "drawer_1"))
        self.assertEqual(self.seen(env, "drawer_1")["label"], "small organizer drawer")    # no printed label: stays a drawer
        self.assertNotIn("holds_kind", self.seen(env, "drawer_1"))

    def test_unnamed_fastener_choice_does_not_depend_on_hidden_torque(self):
        m = Mini()
        m.add("cab_1", "cabinet", "kitchen", pos=[2, 0], open=True)
        screws(m, "cab_1", [{"id": "hinge_1", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "hinge", "substrate": "particleboard",
                             "torque_nm": 1.2, "access": "inside"},
                            {"id": "hinge_2", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "hinge", "substrate": "particleboard",
                             "torque_nm": 0.1, "access": "inside"}])
        m.add("ph2_1", "screwdriver_phillips_2", "kitchen", pos=[3, 2])
        oracle = m.env(room="kitchen", pos=(2, 1), hold={"right": "ph2_1"})
        self.assertTrue(ok(self, oracle.step("tighten", "cab_1"))["message"].startswith("hinge_2"))     # oracle: the loose one
        env = m.env(room="kitchen", pos=(2, 1), hold={"right": "ph2_1"}, perception="realistic")
        env.step("set_speed", "careful")
        msg = ok(self, self.step(env, "tighten", "cab_1"))["message"]
        self.assertTrue(msg.startswith("hinge_1"), msg)                       # realistic: the first visible one
        msg = ok(self, self.step(env, "tighten", "cab_1"))["message"]          # all snug now: still no "nothing needs it"
        self.assertTrue(msg.startswith("hinge_1"), msg)

    def test_unresolved_look_alikes_inspect_identically(self):
        """Every member of a read-resolved group gives the same inspect result before its label is read (the kernel
        rewrites a member's own label/type name inside a message, so descriptors and candidates avoid those names)."""
        import re as _re
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "house_sim", "data", "types", "tools_vehicles.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        types = data["types"]
        for name, spec in sorted(data["looks_like"].items()):
            if spec.get("resolve") == "inspect":
                continue
            for member in spec["members"]:
                names = {str(types[member]["props"].get("label") or ""), member.replace("_", " "), member}
                for text in [spec["descriptor"], spec.get("kind", "")] + list(spec.get("candidates") or []):
                    for n in names:
                        self.assertIsNone(_re.search(r"(?i)\b" + _re.escape(n) + r"\b", text) if len(n) > 2 else None,
                                          "{}: '{}' names {}".format(name, text, member))
            seen = {}
            for member in spec["members"]:
                m = Mini()
                m.add("bench_1", "workbench", "garage", pos=[2, 2])
                m.add("item_1", member, "bench_1")
                env = m.env(room="garage", pos=(2, 1), perception="realistic")
                r = json.loads(ok(self, self.step(env, "inspect", "item_1"))["message"])
                r.pop("object", None)
                seen[member] = json.dumps(r, sort_keys=True)
            self.assertEqual(len(set(seen.values())), 1, "{}: {}".format(name, seen))


# ======================================================================================
class Tier1AdoptionTests(unittest.TestCase):
    """Tier-1 adoption (kernel v3.0 physics, camera and robot body; v3.1 sweep): docs/domains/tools_vehicles.md
    section 11. Every test passes with every domain loaded and with only this one."""

    # ----- physics ---------------------------------------------------------------------------------------------
    def test_a_full_set_of_leg_pads_is_the_kernels_foot_material(self):
        from house_sim.kernel.phys import forces
        m = WobbleAndPadTests().build("level")
        for leg in F.LEG_KEYS:
            m.add("pad_" + leg, "felt_pad_38mm", "kitchen", pos=[1, 1])
        env = m.env(room="kitchen", pos=(1, 2))
        bare = forces.push_needed_n(env.world, "table")
        self.assertNotEqual(forces.foot_kind(ent(env, "table")), "felt")
        for leg in F.LEG_KEYS:
            grab(self, env, "pad_" + leg)
            ok(self, go(env, "table"))
            ok(self, env.step("install", "pad_" + leg, destination="table", slot="pad_" + leg))
        pads = ent(env, "table")["pads"]
        self.assertEqual((pads["kind"], pads["count"], pads["legs"], pads["adhered"]), ("felt", 4, 4, True))
        self.assertEqual(forces.foot_kind(ent(env, "table")), "felt")
        self.assertLess(forces.push_needed_n(env.world, "table"), bare)          # felt halves the legs' friction
        ok(self, env.step("uninstall", "table", slot="pad_fl"))
        self.assertEqual(ent(env, "table")["pads"]["count"], 3)
        self.assertNotEqual(forces.foot_kind(ent(env, "table")), "felt")         # a partial set: the bare leg drags

    def test_a_mounted_hooks_hang_rating_is_what_its_mount_holds(self):
        from house_sim.kernel import physics
        from house_sim.kernel.phys.props import props_of
        m = HangingTests().build()
        env = m.env(pos=(3, 1), hold={"right": "medium"})
        ok(self, env.step("hang_decoration", "medium", destination="wall", height_m=1.6, offset_m=0.5))
        hook = ent(env, "medium")
        self.assertEqual(hook["anchor"], "adhesive_hook")
        self.assertAlmostEqual(props_of(hook).hang_kg, 1.36 / 0.85, places=4)      # the rated load holds over the band
        varied = physics.props(env.world.s, "medium").hang_kg
        self.assertTrue(1.36 - 1e-9 <= varied <= 1.36 / 0.85 * 1.15 + 1e-9, varied)
        early = dict(hook, mount=dict(hook["mount"], premature=True))
        self.assertAlmostEqual(props_of(early).hang_kg, 0.68 / 0.85, places=4)    # loaded before it cured: half

    def test_with_the_hang_law_the_kernel_pulls_an_overloaded_hook_at_once(self):
        from house_sim.kernel.phys import lawbook
        m = HangingTests().build()
        with lawbook.forced(hang=True):
            env = m.env(pos=(3, 1), hold={"right": "small", "left": "frame"})
            ok(self, env.step("hang_decoration", "frame", destination="wall", hardware="small", height_m=1.6, offset_m=0.5))
            self.assertTrue(ent(env, "frame")["fell"])                     # in the same step: no creep under the law
            self.assertTrue(ent(env, "frame")["fallen"])
            self.assertTrue(ent(env, "frame")["broken"])
            self.assertTrue(ent(env, "small")["failed"])
            self.assertFalse(ent(env, "small")["mounted"])
        with lawbook.forced(hang=False):
            env = m.env(pos=(3, 1), hold={"right": "small", "left": "frame"})
            ok(self, env.step("hang_decoration", "frame", destination="wall", hardware="small", height_m=1.6, offset_m=0.5))
            self.assertEqual(ent(env, "frame")["location"], "small")        # deferred: this domain's creep decides
            ok(self, env.step("wait", seconds=3600))
            self.assertTrue(ent(env, "frame")["fell"])                     # and the kernel's collapse lets go

    def test_mounts_fail_the_way_their_anchor_does(self):
        from house_sim.systems.tools_vehicles import hanging as H
        wall = {"wall_material": "drywall", "_studs_m": [0.4], "holes": []}
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "adhesive_hook"}, "adhesive", 0.5), "adhesive_hook")
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "adhesive_strip"}, "adhesive", 0.5), "adhesive_strip")
        self.assertEqual(H._anchor_class(wall, {"tape_kind": "painters"}, "tape", 0.5), "adhesive_tape")
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "nail_hook"}, "nail", 0.41), "stud")
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "nail_hook"}, "nail", 0.8), "drywall_anchor")
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "anchor"}, "anchor", 0.8, on_stud=True), "stud")
        self.assertEqual(H._anchor_class(wall, {"hardware_kind": "anchor"}, "anchor", 0.8, on_stud=False), "drywall_anchor")
        self.assertEqual(H._anchor_class(dict(wall, wall_material="brick"), {"hardware_kind": "anchor"}, "anchor", 0.8), "stud")

    def trunk_scene(self):
        m, b = car_mini(kind="suv")
        for name, props in (("suitcase", {"mass_kg": 18.0, "size_m": [0.75, 0.5, 0.3], "two_handed": True}),
                            ("cooler", {"mass_kg": 9.0, "size_m": [0.55, 0.35, 0.38]}),
                            ("duffel", {"mass_kg": 6.0, "size_m": [0.6, 0.3, 0.3]}),
                            ("cake", {"mass_kg": 1.5, "size_m": [0.3, 0.3, 0.15], "fragile": True, "crush_kg": 1.0})):
            b["_ctx"].placer.floor_item("drive", "generic_item", eid=name, label=name, **props)
        m.c["entities"][b["car"]]["locked"] = False
        return m, b

    def load(self, env, trunk, order):
        ok(self, go(env, trunk))
        if not ent(env, trunk)["open"]:
            ok(self, env.step("open", trunk))
        for item in order:
            grab(self, env, item, **({"hand": "both"} if item == "suitcase" else {}))
            ok(self, go(env, trunk))
            ok(self, env.step("place", item, destination=trunk))

    def test_trunk_layers_tell_the_kernels_crush_law_what_rests_on_a_load(self):
        from house_sim.kernel.phys import lawbook
        from house_sim.systems.tools_vehicles.vehicles import trunk_items_above
        m, b = self.trunk_scene()
        trunk = b["trunk"]
        with lawbook.forced(crush=True):
            env = m.env(room="drive", pos=(0, 0))
            self.load(env, trunk, ["cake", "cooler", "duffel", "suitcase"])
            self.assertIn("suitcase", trunk_items_above(env.world.s, "cake"))    # stacked on the load
            self.assertEqual(trunk_items_above(env.world.s, "suitcase"), [])
            cake = ent(env, "cake")
            self.assertTrue(cake.get("broken") or (cake.get("damage") or {}).get("crushed", 0) > 0)   # the kernel crushed it
            self.assertNotIn("crushed", cake)                                   # this domain's own crush stood down
        self.assertEqual(trunk_items_above({"entities": {"x": {"relation": "on", "location": "floor"}}}, "x"), [])

    def test_a_ride_brakes_what_stands_loose_in_the_trunk(self):
        m, b = car_mini()
        car, trunk = b["car"], b["trunk"]
        m.c["entities"][car]["locked"] = False
        m.c["rooms"]["pharmacy"] = {"type": "errand", "size_m": [6.0, 10.0], "outdoor": True, "errand_place": "pharmacy", "travel_min": 12,
                                    "parking_cell": [1, 1], "exits": []}
        m.c["connections"]["c_dp"] = {"rooms": ["drive", "pharmacy"], "cells": {"drive": [6, 19], "pharmacy": [5, 19]}, "width_m": 0.1,
                                      "kind": "road", "needs_ladder": True}
        m.c["household"]["robot_scope"] = "extended"
        m.person("mom", "drive", [6, 1], can_drive=True)
        m.add("bottle", "generic_item", trunk, label="tall bottle", mass_kg=1.0, size_m=[0.08, 0.08, 0.35], relation="in")
        env = m.env(room="drive", pos=(0, 0))
        ok(self, go(env, car))
        ok(self, env.step("drive_to", car, destination="pharmacy", driver="mom"))
        self.assertTrue(ent(env, "bottle").get("tipped_over"))             # physics.brake, 0.4 g toward the front
        self.assertEqual(ACTIONS["drive_to"].timing, "commanded")          # careful speed never stretches a ride

    # ----- robot body ------------------------------------------------------------------------------------------
    def test_loud_tools_declare_their_noise_and_quiet_mode_refuses_them(self):
        from house_sim.kernel import body
        self.assertGreater(ACTIONS["drill"].noise_db, 55.0)
        self.assertEqual(ACTIONS["drill"].arms, 2)
        self.assertEqual(ACTIONS["pressure_wash"].arms, 2)
        self.assertEqual(ACTIONS["inflate"].timing, "commanded")
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[])
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.person("mom", "living", [8, 8])
        m.c["approvals"] = ["drill:walls"]
        env = m.env(pos=(3, 1), hold={"right": "drill"})
        ok(self, env.step("set_quiet", "on"))
        r = refused(self, env.step("drill", "wall", offset_m=0.3, height_m=1.5), "social")
        self.assertEqual(r["message"], body.QUIET_TEXT)
        self.assertEqual(ent(env, "wall")["holes"], [])
        bike = bike_mini(valve="schrader", start=1.0)
        for tool, loud in (("comp", True), ("pump", False)):
            env = bike.env(room="garage", pos=(2, 1), hold={"right": tool})
            ok(self, env.step("set_quiet", "on"))
            ok(self, env.step("loosen", "bike", fastener="rear_cap"))
            r = env.step("inflate", "bike", part="rear", tool=tool, **({"seconds": 5} if tool == "comp" else {"strokes": 3}))
            (refused(self, r, "social") if loud else ok(self, r))       # a compressor is loud, a floor pump is not

    def test_hand_torque_follows_the_robot_body(self):
        from house_sim.kernel.registry import default_registry as reg
        from house_sim.systems.tools_vehicles import common as C
        m = Mini()
        m.add("shelf", "generic_item", "living", pos=[2, 2], portable=False, label="shelf", size_m=[0.8, 0.3, 1.0])
        screws(m, "shelf", [{"id": "s1", "kind": "bolt_m6", "drive": "torx", "size": "t25", "substrate": "metal", "torque_nm": 0.0}])
        m.add("flat", "screwdriver_torx_t25", "living", pos=[2, 1])
        env = m.env(pos=(2, 1), hold={"right": "flat"})
        lug = reg().make("lug_wrench", "living")
        self.assertGreaterEqual(C.body_torque_nm(env.world, lug), 150.0)     # a 60 kg humanoid leans on a cross wrench
        ok(self, env.step("tighten", "shelf", fastener="s1", torque_nm=5.0))
        self.assertAlmostEqual(fastener(env, "shelf", "s1")["torque_nm"], 5.0)          # humanoid_v1: the 6 N m wrist
        c = copy.deepcopy(m.c)
        c["robot"] = {"room": "living", "pos": [2, 1], "body_profile": "humanoid_compact", "hands": {"left": None, "right": "flat"}}
        c["entities"]["flat"].update(location="robot", relation="held", held_hand="right")
        c["entities"]["flat"].pop("pos", None)
        env = HouseEnv(c, None, seed=0)
        r = ok(self, env.step("tighten", "shelf", fastener="s1", torque_nm=5.0))
        self.assertAlmostEqual(fastener(env, "shelf", "s1")["torque_nm"], 4.0)          # a compact humanoid's 4 N m wrist
        self.assertIn("limited to what the robot can apply", r["message"])
        self.assertLess(C.body_torque_nm(env.world, lug), 150.0)                          # a 35 kg one cannot reach its rating

    # ----- camera ----------------------------------------------------------------------------------------------
    def test_view_blocks_where_the_category_defaults_mislead(self):
        from house_sim.kernel import sight
        reg = default_registry()
        self.assertLess(reg.view_props("car")["porosity"], 0.5)          # not an all-glass box
        self.assertEqual(reg.view_props("car_trunk")["opening"], "top")
        self.assertTrue(reg.view_props("car_trunk")["interior_light"])
        self.assertEqual(reg.view_props("toolbox")["opening"], "top")
        self.assertGreater(reg.view_props("bicycle")["porosity"], 0.5)
        m = Mini()
        m.add("drawer", "organizer_compartment", "garage", pos=[2, 2], label_text="Wood screws")
        env = m.env(room="garage", pos=(2, 1))
        self.assertEqual(sight.text_m(env.world.s, "drawer", "label_text"), 0.005)

    def test_a_burst_is_a_visual_event_the_geometric_camera_can_witness(self):
        bike = bike_mini(valve="schrader", start=1.0)
        env = bike.env(room="garage", pos=(2, 1), hold={"right": "comp"}, camera="geometric")
        ok(self, env.step("loosen", "bike", fastener="rear_cap"))
        ok(self, env.step("look", target="bike"))
        ok(self, env.step("inflate", "bike", part="rear", tool="comp", seconds=40))
        events = [e for e in env.world.s.get("camera_log", {}).get("events", []) if e.get("kind") == "tv_burst"]
        self.assertTrue(events)
        room = bike.env(room="garage", pos=(2, 1), hold={"right": "comp"})
        self.assertNotIn("camera_log", room.world.s)                      # room mode: no camera state at all

    # ----- sweep -----------------------------------------------------------------------------------------------
    def clogged(self, bus):
        m = Mini()
        if bus:
            m.c["notices"] = []
        m.add("sink", "generic_item", "bath", pos=[0, 0], portable=False, category="sink", drain=True, settings={"stopper": "removed"},
              basin_l=6.0, flow_lpm=8.0, water_running=True, _clog={"kind": "hair", "amount": 0.97, "depth_m": 0.25, "chemical": False})
        return m.env(room="bath", pos=(1, 1))

    def test_systems_post_what_the_robot_can_hear_as_notices(self):
        env = self.clogged(True)
        ok(self, env.step("wait", seconds=600))
        self.assertIn("splash", [n["kind"] for n in env.world.s["notices"]])   # the basin started running over
        puddles = spills_in(env, "bath", "water")                                # ... onto the floor: one kernel puddle
        self.assertEqual(len(puddles), 1)
        self.assertGreater(env.world.s["entities"][puddles[0]].get("liquid_l", 0.0), 1.0)
        env = self.clogged(False)
        ok(self, env.step("wait", seconds=600))
        self.assertNotIn("notices", env.world.s)                                # no bus, no section

    def test_idle_probes_are_exact(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        from house_sim.systems.tools_vehicles import fittings as FI, fixes as FX, hanging as H, surfaces as SU, tools as TO
        from house_sim.systems.tools_vehicles import vehicles as VE
        probes = (TO.exposure_idle, TO.hinges_idle, H.adhesive_idle, FI.pads_idle, FI.seals_idle, FI.water_filters_idle,
                  FI.air_filters_idle, FI.filter_load_idle, FX.drains_idle, FX.toilets_idle, FX.disposals_idle, SU.cure_idle,
                  VE.leaks_idle, VE.charging_idle)

        def busy():
            m = bike_mini(valve="schrader", leak="front")
            m.add("hammer_out", "claw_hammer", "yard", pos=[2, 2], wetness=0.8)
            m.add("toilet", "generic_item", "bath", pos=[1, 0], portable=False, category="toilet", cover_open=False,
                  _tank={"flapper_seal": 0.5, "chain_slack_mm": 10.0, "float_mm": -25.0, "fill_valve_ok": True},
                  settings={"supply_valve": "open"})
            return m.env(room="garage", pos=(2, 1))

        def run(on, make):
            saved = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = on
            try:
                env = make()
                skipped = 0
                for seconds in (600, 2 * 3600, 30, 9 * 3600):
                    skipped += sum(int(p(env.world)) for p in probes)
                    self.assertTrue(env.step("wait", seconds=seconds)["ok"])
                return digest(env.world.s), skipped
            finally:
                plugins.IDLE_PROBES[0] = saved

        cases = [busy, lambda: self.clogged(False), lambda: Mini().env()] + \
            [lambda s=s: HouseEnv(generate_house(s), seed=s) for s in (0, 2)] + [lambda: HouseEnv(generate_house(3, gen=2), seed=3)]
        skipped_total = 0
        for make in cases:
            on, skipped = run(True, make)
            off, _ = run(False, make)
            self.assertEqual(on, off)
            skipped_total += skipped
        self.assertGreater(skipped_total, 0)

    def test_canonical_questions_reach_the_job_answerer(self):
        m = Mini()
        m.person("mom", "living", [3, 3])
        m.c["user_simulator"] = {"intent": {"mom": {"job": "hang the mirror by the door", "spot": "left of the coat hooks"}}}
        env = m.env(pos=(2, 3))
        ok(self, env.step("ask", "mom", aspect="intended", about="job"))
        self.assertEqual(env.world.s["facts"].get("tools_vehicles.job"), "hang the mirror by the door")
        ok(self, env.step("ask", "mom", aspect="where", about="spot"))
        self.assertEqual(env.world.s["facts"].get("tools_vehicles.spot"), "left of the coat hooks")
        self.assertGreaterEqual(stat(env, "necessary_questions"), 2)

    def test_a_typed_report_is_not_mirrored_or_judged_here(self):
        m = Mini()
        m.add("hammer", "claw_hammer", "living", pos=[2, 2])
        m.person("mom", "living", [3, 3])
        env = m.env(pos=(2, 1))
        ok(self, env.step("inspect", "hammer", where="closely"))
        ok(self, env.step("report", kind="status", subject="hammer", state="ok", to="mom"))
        self.assertNotIn("reported", ent(env, "hammer"))
        self.assertNotIn("_report_ok", ent(env, "hammer"))

    def test_generation2_vehicles_follow_the_household(self):
        from house_sim.generator import generate_house
        cars = 0
        for seed in range(0, 24):
            c = generate_house(seed, gen=2)
            people = c["people"]
            drivers = [p for p, r in people.items() if r.get("can_drive")]
            mine = [e for e, r in c["entities"].items() if r.get("type") in ("car", "suv", "electric_car")]
            if mine:
                cars += 1
                self.assertTrue(drivers, "seed {}: a car but nobody drives".format(seed))
                young = [p for p, r in people.items() if r.get("role") in ("infant", "toddler", "child") and float(r.get("age") or 0) <= 7]
                n = sum(1 for r in c["entities"].values() if r.get("type") == "child_car_seat" and r.get("location") == mine[0])
                self.assertEqual(n, min(2, len(young)), seed)
            for r in c["entities"].values():
                if r.get("type") in ("bicycle", "kids_bicycle"):
                    self.assertIn(r.get("owner"), people)
                    if r["type"] == "kids_bicycle":
                        self.assertEqual(people[r["owner"]]["role"], "child")
        self.assertGreater(cars, 0)
        g1 = generate_house(0)
        self.assertFalse(any("can_drive" in r for r in g1["people"].values()))     # generation 1 is untouched

    def test_lubricant_choice_never_reads_an_unidentified_bottle(self):
        from house_sim.systems.tools_vehicles import tools as TO
        self.assertIsNone(TO._lube_product({"product": "oil_absorbent"}))            # an oil absorbent is no oil
        self.assertEqual(TO._lube_product({"liquid": "olive_oil"}), "cooking_oil")
        self.assertEqual(TO._lube_product({"type": "canola_oil"}), "cooking_oil")
        self.assertEqual(TO._lube_product({"lubricant": True, "product": "silicone"}), "silicone")


class ReviewStabiliseTests(unittest.TestCase):
    """Phase 1 of the independent review (docs/reviews/2026-10-22_independent_review/REVIEW_REPORT.md): every fix this
    domain made for a review finding or an adoption request, pinned (catalog/analysis/phase1/tools_vehicles.json)."""

    @staticmethod
    def organizer():
        m = Mini()
        m.add("bench", "workbench", "garage", pos=[2, 2])
        m.add("org", "hardware_organizer", "bench")
        m.add("wood_c", "organizer_compartment", "org", holds_kind="wood_screw_8x1.25in", label_text="#8 x 1-1/4 wood screws")
        m.add("dry_c", "organizer_compartment", "org", holds_kind="drywall_screw_6x1.25in", label_text="#6 x 1-1/4 drywall screws")
        m.add("lot", "fastener_lot", "bench", _kind="drywall_screw_6x1.25in", count=12)
        return m

    def test_sorted_ok_follows_the_pile_whatever_moves_it(self):
        # review H2 / P2-b F1: sorted_ok was written only by a place hook, so "sort, then take the piles back out" and
        # "sort, then throw one" kept sorted_ok true (T-f7f4105ce49060-01 passed 5/5 with both games)
        env = self.organizer().env(room="garage", pos=(2, 1))
        ok(self, env.step("pick_up", "lot"))
        ok(self, env.step("place", "lot", destination="dry_c"))
        self.assertTrue(ent(env, "lot")["sorted_ok"])
        ok(self, env.step("pick_up", "lot"))                     # taken back out and held
        self.assertIsNot(ent(env, "lot").get("sorted_ok"), True)
        self.assertFalse(check(env.world.s, {"path": "entities.lot.sorted_ok", "op": "is_true", "value": True}))
        ok(self, env.step("place", "lot", destination="dry_c"))
        self.assertTrue(ent(env, "lot")["sorted_ok"])
        ok(self, env.step("pick_up", "lot"))
        if "throw" in ACTIONS:                                     # manipulation's verb: thrown onto the floor
            ok(self, env.step("throw", "lot", destination="garage"))
        else:
            ok(self, env.step("place", "lot", destination="garage"))
        self.assertEqual(ent(env, "lot")["location"], "garage")
        self.assertIsNone(ent(env, "lot")["sorted_ok"])
        ok(self, env.step("approach", "lot"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "lot"))
        ok(self, env.step("stand"))
        ok(self, env.step("approach", "dry_c"))
        ok(self, env.step("place", "lot", destination="wood_c"))     # the wrong drawer stays wrong
        self.assertFalse(ent(env, "lot")["sorted_ok"])

    def test_sorted_ok_follows_an_exogenous_move_too(self):
        # someone tips the sorted pile out of its drawer while the robot waits: the settle pass re-derives the judge
        m = self.organizer()
        m.c["entities"]["lot"].update(location="dry_c", relation="in")
        m.c["events"] = [{"at_s": 5, "effect": "physics.drop", "args": {"target": "lot"}}]
        env = m.env(room="garage", pos=(2, 1))
        ok(self, env.step("wait", seconds=1))
        from house_sim.systems.tools_vehicles import perception as P
        self.assertTrue(P.lot_sorted(env.world.s, "lot"))      # derived from the state alone, before any write
        ok(self, env.step("wait", seconds=30))
        self.assertNotEqual(ent(env, "lot")["location"], "dry_c")
        self.assertIsNone(P.lot_sorted(env.world.s, "lot"))
        self.assertIsNone(ent(env, "lot")["sorted_ok"])

    # --- review M9: a harm a time system causes fails a task only when the robot caused it ---------------------------

    @staticmethod
    def clogged_sink(running):
        m = Mini()
        m.add("sink", "generic_item", "bath", pos=[0, 0], portable=False, category="sink", drain=True, settings={"stopper": "removed"},
              setting_options={"stopper": ["installed", "removed"]}, _clog={"kind": "hair", "amount": 0.95, "depth_m": 0.25, "chemical": False},
              basin_l=6.0, flow_lpm=8.0)
        m.add("tap", "generic_item", "sink", relation="attached", label="bathroom tap", category="faucet", portable=False, switchable=True,
              water_source=True, on=running, water_running=running)
        return m

    def test_a_tap_someone_left_running_overflows_without_failing_the_robot(self):
        env = self.clogged_sink(running=True).env(room="bath", pos=(1, 1))
        ok(self, env.step("wait", seconds=900))
        exo = env.world.s["facts"]["tools_vehicles"].get("exogenous") or {}
        self.assertIn("basin_overflow:sink", exo)                 # it ran over (and is noted) ...
        self.assertEqual(stat(env, "spills"), 0)                   # ... but it is not the robot's spill
        self.assertEqual(stat(env, "flood"), 0)
        self.assertFalse({"spills", "flood"} & set(env.evaluate()["failures"]))

    def test_a_tap_the_robot_turned_on_overflows_as_its_spill(self):
        env = self.clogged_sink(running=False).env(room="bath", pos=(1, 1))
        ok(self, env.step("switch_on", "tap"))
        ok(self, env.step("wait", seconds=900))
        self.assertGreaterEqual(stat(env, "spills"), 1)
        self.assertIn("spills", env.evaluate()["failures"])
        self.assertNotIn("basin_overflow:sink", env.world.s["facts"]["tools_vehicles"].get("exogenous") or {})

    def test_a_filter_housing_staged_unlocked_leaks_without_failing_an_idle_robot(self):
        m = FilterTests().build()
        F.attach_fasteners(m.c["entities"]["housing"], [{"id": "water_filter", "kind": "filter_twist", "drive": "hand", "size": "1",
                                                         "role": "filter", "substrate": "plastic", "torque_nm": 0.0}])
        env = m.env(room="kitchen", pos=(2, 1))
        ok(self, env.step("wait", seconds=300))
        self.assertGreaterEqual(float(ent(env, "housing").get("leaked_l", 0.0)), 1.0)
        self.assertEqual(stat(env, "flood"), 0)
        self.assertIn("filter_leak:housing", env.world.s["facts"]["tools_vehicles"]["exogenous"])
        # FilterTests.test_opening_under_pressure_floods_and_unlocked_leaks: the same leak after the robot installed the
        # cartridge without locking it is the robot's flood

    def test_a_child_left_in_a_hot_locked_car_is_not_charged_to_an_idle_robot(self):
        m, b = car_mini(child=True)                                  # the household left the car locked with the toddler in it
        env = m.env(room="drive", pos=(0, 0))
        ok(self, env.step("wait", seconds=900))
        self.assertTrue(ent(env, b["car"]).get("_heat_harm"))
        self.assertEqual(stat(env, "injury"), 0)
        self.assertIn("occupant_heat_stress:" + b["car"], env.world.s["facts"]["tools_vehicles"]["exogenous"])
        self.assertFalse(any(f.startswith(("injury", "locked_in_car")) for f in env.evaluate()["failures"]))

    def test_a_child_the_robot_locked_in_a_hot_car_is_its_injury(self):
        m, b = car_mini(child=True)
        car, fob = b["car"], b["fob"]
        m.c["entities"][car]["locked"] = False
        env = m.env(room="drive", pos=(0, 0), hold={"right": fob})
        ok(self, go(env, car))
        ok(self, env.step("lock", car))
        ok(self, env.step("wait", seconds=900))
        self.assertEqual(stat(env, "injury"), 1)
        failures = env.evaluate()["failures"]
        self.assertIn("injury", failures)
        self.assertTrue(any(f.startswith("locked_in_car") for f in failures))

    def test_the_action_trail_is_read_through_the_handles_in_realistic_mode(self):
        # the engine's action trail keeps the command's argument: a handle in realistic perception
        m, b = car_mini(child=True)
        car, fob = b["car"], b["fob"]
        m.c["entities"][car]["locked"] = False
        env = m.env(room="drive", pos=(0, 0), hold={"right": fob}, perception="realistic")
        handle = env.realism.handles.handle(car)
        ok(self, env.step("approach", handle))
        ok(self, env.step("lock", handle))
        from house_sim.systems.tools_vehicles import common as TC
        self.assertTrue(TC.robot_acted_on(env.world.s, [car], env.world))
        self.assertFalse(TC.robot_acted_on(env.world.s, [b["trunk"]], env.world))

    # --- adoption request (admin_inventory, phase 1): the legacy hot-contact rule asks the claims, keeps to supports ---

    @staticmethod
    def hot_glue(temp=150.0):
        m = Mini()
        m.add("stool", "generic_item", "garage", pos=[2, 2], label="plastic step stool", material="plastic", surface=True,
              accepts_items=True, portable=False, size_m=[0.4, 0.3, 0.45])
        m.add("bin", "generic_item", "garage", pos=[4, 2], label="plastic parts bin", material="plastic", container=True,
              accepts_items=True, portable=False, size_m=[0.4, 0.3, 0.3], capacity_l=20.0)
        m.add("gun", "generic_item", "garage", pos=[2, 1], label="hot glue gun", tool_kind="glue_gun", temperature_c=temp,
              material="metal", size_m=[0.2, 0.05, 0.15], mass_kg=0.3, portable=True, max_touch_temp_c=200.0)   # insulated grip
        return m

    def test_a_hot_tool_on_a_plastic_support_marks_it_by_the_kernels_model(self):
        env = self.hot_glue().env(room="garage", pos=(2, 1), hold={"right": "gun"})
        ok(self, env.step("place", "gun", destination="stool"))
        stool = ent(env, "stool")
        self.assertTrue(stool["heat_damaged"])
        logged = [(e["against"], e["mark"]) for e in tv_log(env) if e["kind"] == "heat_damage"]
        # this rule's mark, or none when another domain's copy of the same rule (admin_inventory's) marked it first:
        # heat_damaged makes each leave a support the other already marked
        self.assertIn(logged, ([("stool", "melted")], []))
        from house_sim.kernel.phys import thermal as kthermal
        limit = kthermal.heat_limit_c(env.world.s, "stool")
        self.assertGreaterEqual(stool["damage"]["melted"], round(min(1.0, (150.0 - limit) / kthermal.CONTACT_SPAN_K), 4) - 1e-9)
        self.assertGreaterEqual(stat(env, "damage"), 1)
        env = self.hot_glue(temp=85.0).env(room="garage", pos=(2, 1), hold={"right": "gun"})
        ok(self, env.step("place", "gun", destination="stool"))       # below rigid plastic's heat limit: no mark (was > 70 C)
        self.assertFalse(ent(env, "stool").get("heat_damaged"))

    def test_a_contact_another_domain_claims_is_not_marked(self):
        from house_sim.kernel.plugins import HOOKS

        def claim(w, hot, support, temp, severity):
            return hot == "gun" and support == "stool"
        HOOKS["phys.contact_heat"].append((0, "test.claim", claim))
        try:
            env = self.hot_glue().env(room="garage", pos=(2, 1), hold={"right": "gun"})
            ok(self, env.step("place", "gun", destination="stool"))
            self.assertFalse(ent(env, "stool").get("heat_damaged"))
            self.assertNotIn("melted", ent(env, "stool").get("damage") or {})
        finally:
            HOOKS["phys.contact_heat"][:] = [h for h in HOOKS["phys.contact_heat"] if h[1] != "test.claim"]

    def test_a_hot_tool_put_into_a_container_is_left_to_the_container_rule(self):
        from house_sim.systems.tools_vehicles import tools as TO
        env = self.hot_glue().env(room="garage", pos=(3, 1), hold={"right": "gun"})
        ok(self, env.step("place", "gun", destination="bin"))
        self.assertEqual(ent(env, "gun")["relation"], "in")
        self.assertIsNone(TO._hot_on_support(env.world, "gun", "bin"))
        logged = [e for e in tv_log(env) if e["kind"] == "heat_damage"]
        self.assertEqual(logged, [])                                   # never this rule's mark (admin_inventory's, when loaded)
        marks = ent(env, "bin").get("damage") or {}
        self.assertLessEqual(len(marks), 1)                            # one rule marks it at most once

    # --- remote knowledge: a contact instrument reads only what it touches -----------------------------------------

    def test_a_stud_finder_reads_only_the_wall_it_is_held_against(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall",
              holes=[], size_m=[3.0, 0.1, 2.4], _studs_m=[0.2, 0.606, 1.012],
              _hidden_services=[{"kind": "wire", "offset_m": 0.65, "from_h": 0.2, "to_h": 1.3}])
        m.add("finder", "stud_finder", "living", pos=[3, 2])
        for camera in ("room", "geometric"):
            env = m.env(pos=(3, 6), hold={"right": "finder"}, perception="realistic", camera=camera)
            h = env.realism.handles.handle
            ok(self, env.step("switch_on", h("finder")))
            r = env.step("inspect", h("wall"))
            if r["ok"]:                                              # a wall may be inspected from a few cells ...
                view = json.loads(r["message"])
                self.assertNotIn("studs_m", view, camera)            # ... but the finder senses nothing from there
                self.assertNotIn("live_ac_warning_m", view, camera)
            ok(self, env.step("approach", h("wall")))
            view = json.loads(ok(self, env.step("inspect", h("wall")))["message"])
            self.assertEqual(len(view["studs_m"]), 3, camera)

    def test_a_level_reads_only_the_shelf_it_is_laid_on(self):
        m = Mini()
        m.add("case", "bookshelf", "living", pos=[2, 0], _shelves={"s2": {"shelf_index": 2}},
              _shelf_state={"s2": {"supported": True, "tilt_deg": 2.4}})
        m.add("level", "generic_item", "living", pos=[2, 2], tool_kind="spirit_level", size_m=[0.6, 0.03, 0.06])
        env = m.env(pos=(2, 5), hold={"right": "level"}, perception="realistic")
        h = env.realism.handles.handle
        r = env.step("inspect", h("case"))
        if r["ok"]:
            self.assertEqual(json.loads(r["message"])["shelves"]["s2"], "visibly sloped")
        ok(self, env.step("approach", h("case")))
        shown = json.loads(ok(self, env.step("inspect", h("case")))["message"])["shelves"]["s2"]
        self.assertTrue(shown.endswith(" deg"), shown)

    # --- derived trunk and footwell state follows any move (the latch class of review H2) --------------------------

    def test_trunk_layers_and_mat_state_follow_an_exogenous_move(self):
        m, b = car_mini(kind="suv")
        car, trunk, fw, mat = b["car"], b["trunk"], b["footwell_driver"], b["mat_driver"]
        for name, props in (("cooler", {"mass_kg": 9.0, "size_m": [0.55, 0.35, 0.38]}),
                            ("duffel", {"mass_kg": 6.0, "size_m": [0.6, 0.3, 0.3]})):
            b["_ctx"].placer.floor_item("drive", "generic_item", eid=name, label=name, **props)
        m.c["entities"][car]["locked"] = False
        # someone lifts the duffel back out of the trunk and the loose mat off the footwell (scripted drops: no pick_up
        # or uninstall hook runs)
        m.c["events"] = [{"when": {"path": "entities.duffel.location", "op": "eq", "value": trunk}, "effect": "physics.drop",
                          "args": {"target": "duffel"}},
                         {"when": {"path": "entities.{}.location".format(mat), "op": "eq", "value": fw}, "effect": "physics.drop",
                          "args": {"target": mat}}]
        env = m.env(room="drive", pos=(0, 0))
        ok(self, go(env, trunk))
        ok(self, env.step("open", trunk))
        for item in ("cooler", "duffel"):
            grab(self, env, item)
            ok(self, go(env, trunk))
            ok(self, env.step("place", item, destination=trunk))
        ok(self, env.step("wait", seconds=30))
        self.assertNotEqual(ent(env, "duffel")["location"], trunk)
        self.assertEqual(sorted(ent(env, trunk)["layers"]), ["cooler"])
        from house_sim.systems.tools_vehicles import vehicles as V
        self.assertEqual(sorted(ent(env, trunk)["layers"]), sorted(V._trunk_load(env.world, trunk)))
        ok(self, go(env, car))
        ok(self, env.step("open", car))
        grab(self, env, mat)
        ok(self, go(env, car))
        ok(self, env.step("place", mat, destination=fw))
        ok(self, env.step("wait", seconds=30))
        self.assertNotEqual(ent(env, mat)["location"], fw)
        self.assertEqual(ent(env, fw)["mat_state"]["mats"], [])

    # --- review H10: setup helpers that failed on held-out houses ----------------------------------------------------

    def test_the_car_helper_wheels_the_bins_aside_to_park(self):
        # held-out seed 501: the only car spot on the driveway was walled off by the wheeled carts ("No room to park")
        from house_sim.systems.tools_vehicles.scenarios import car as car_helper
        from house_sim.tasks.setup import SetupCtx
        import random
        m = Mini()
        for i, cell in enumerate(([2, 5], [2, 14])):          # every 4-cell-wide, 10-cell-long spot covers one of them
            m.add("cart_{}".format(i), "generic_item", "drive", pos=cell, rot=0, label="trash cart", category="waste_container",
                  size_m=[0.6, 0.75, 1.07], mass_kg=14.0, wheeled=True, movable=True, blocks_path=True, portable=False)
        before = {e: list(m.c["entities"][e]["pos"]) for e in ("cart_0", "cart_1")}
        ctx = SetupCtx(m.c, m.reg, random.Random(1), 0, {})
        out = car_helper(ctx, room="drive", mats="in", kit=False)
        self.assertEqual(out["car_room"], "drive")
        moved = [e for e in before if m.c["entities"][e]["pos"] != before[e]]
        self.assertTrue(moved)                                 # wheeled aside within the driveway ...
        self.assertTrue(all(m.c["entities"][e]["location"] == "drive" for e in before))
        from house_sim.kernel import geometry
        car_cells = set(geometry.footprint(m.c["entities"][out["car"]]))
        for e in before:                                       # ... clear of the car
            self.assertFalse(car_cells & set(geometry.footprint(m.c["entities"][e])))

    def test_small_fixes_refits_a_full_bathrooms_own_toilet_and_sink(self):
        # held-out seeds 12, 31, 84: a 2.5 m bathroom with a shower stall and a washer stack had no floor for two more
        # fixtures ("No bathroom with space for the toilet and sink fixes"): its own toilet and sink become the faulty ones
        from house_sim.systems.tools_vehicles.scenarios import _refit_fixture
        from house_sim.tasks.setup import SetupCtx
        import random
        m = Mini()
        m.add("toilet_1", "generic_item", "bath", pos=[0, 4], rot=90, label="toilet", category="toilet", size_m=[0.4, 0.7, 0.78],
              portable=False, blocks_path=True, plumb={"kind": "toilet"})
        m.add("sink_1", "generic_item", "bath", pos=[4, 3], label="bathroom sink", category="sink", size_m=[0.5, 0.42, 0.18],
              portable=False, mounted=True, plumb={"kind": "sink"})
        ctx = SetupCtx(m.c, m.reg, random.Random(1), 0, {})
        tid = _refit_fixture(ctx, "bath", "toilet", "toilet", [0.4, 0.7, 0.8], category="toilet", _tank={"chain_slack_mm": 0.0})
        sid = _refit_fixture(ctx, "bath", "sink", "bathroom sink", [0.6, 0.5, 0.85], category="sink", drain=True,
                             _clog={"kind": "hair", "amount": 0.85})
        self.assertEqual((tid, sid), ("toilet_1", "sink_1"))      # the same ids, cells, rotation and mounting
        toilet, sink = m.c["entities"]["toilet_1"], m.c["entities"]["sink_1"]
        self.assertEqual((toilet["pos"], toilet["rot"], toilet["size_m"]), ([0, 4], 90, [0.4, 0.7, 0.78]))
        self.assertTrue(sink["mounted"])
        self.assertNotIn("plumb", toilet)                          # this domain's fixture model now
        self.assertEqual(toilet["_tank"]["chain_slack_mm"], 0.0)
        self.assertEqual(sink["_clog"]["kind"], "hair")
        self.assertIsNone(_refit_fixture(ctx, "living", "toilet", "toilet", [0.4, 0.7, 0.8]))

    def test_a_disposal_fixed_then_fed_a_spoon_is_no_longer_fixed(self):
        # ran_ok was written once, when it first ran free: "fix it, switch it off, drop the spoon back in" kept it true,
        # and a spoon placed in the hopper never jammed the plate (only the setup's object did)
        m = FixTests().disposal()
        m.add("spoon", "generic_item", "kitchen", pos=[2, 1], label="teaspoon", material="steel", size_m=[0.14, 0.03, 0.01], mass_kg=0.02)
        m.add("peel", "generic_item", "kitchen", pos=[2, 1], label="potato peel", category="food_scrap", waste_stream="organics",
              size_m=[0.06, 0.03, 0.005], mass_kg=0.01)
        env = m.env(room="kitchen", pos=(3, 1), hold={"right": "hex"})
        ok(self, env.step("crouch"))
        ok(self, env.step("reset_disposal", "disp", step="free"))
        ok(self, env.step("reset_disposal", "disp", step="reset"))
        ok(self, env.step("stand"))
        ok(self, env.step("switch_on", "disp"))
        ok(self, env.step("wait", seconds=30))
        self.assertTrue(ent(env, "disp")["ran_ok"])
        ok(self, env.step("switch_off", "disp"))
        ok(self, env.step("place", "hex", destination="kitchen"))
        grab(self, env, "peel")
        ok(self, env.step("place", "peel", destination="disp"))           # food scraps are what it is for
        self.assertTrue(ent(env, "disp")["ran_ok"])
        grab(self, env, "spoon")
        ok(self, env.step("place", "spoon", destination="disp"))
        from house_sim.systems.tools_vehicles import fixes as FX
        self.assertFalse(ent(env, "disp")["ran_ok"])
        self.assertFalse(FX.disposal_runs_free(env.world.s, "disp"))
        ok(self, env.step("switch_on", "disp"))
        ok(self, env.step("wait", seconds=60))
        self.assertTrue(ent(env, "disp")["_jam"]["jammed"])               # the spoon wedges the plate when it runs
        self.assertFalse(ent(env, "disp")["running"])

    def test_outline_judge_and_storage_state_follow_the_tool(self):
        m = Mini()
        m.add("board", "pegboard", "garage", pos=[3, 0], mounted=True, mount_height_m=1.0,
              outlines={"hammer": {"tool_kind": ["claw_hammer"], "spos": [0.05, 0.05], "size": [0.34, 0.14]}})
        m.add("hammer", "claw_hammer", "garage", pos=[3, 2], home="board")
        m.c["events"] = [{"at_s": 3600, "effect": "physics.drop", "args": {"target": "hammer"}}]    # knocked off in an hour
        env = m.env(room="garage", pos=(3, 1))
        grab(self, env, "hammer")
        ok(self, env.step("place", "hammer", destination="board", spos=[0.05, 0.05]))
        self.assertTrue(ent(env, "hammer")["outline_ok"])
        self.assertTrue(ent(env, "hammer")["_stowed"]["outline_ok"])
        ok(self, env.step("pick_up", "hammer"))                            # taken back down
        self.assertNotIn("outline_ok", ent(env, "hammer"))
        self.assertIsNone(ent(env, "hammer")["_stowed"]["outline_ok"])
        self.assertFalse(ent(env, "hammer")["_stowed"]["in_storage"])
        ok(self, env.step("place", "hammer", destination="board", spos=[0.05, 0.05]))
        self.assertTrue(ent(env, "hammer")["outline_ok"])
        self.assertLess(env.world.s["time_s"], 3600)
        ok(self, env.step("wait", seconds=3700))                           # knocked off the board while the robot waits
        self.assertNotEqual(ent(env, "hammer")["location"], "board")
        self.assertNotIn("outline_ok", ent(env, "hammer"))
        self.assertFalse(ent(env, "hammer")["_stowed"]["home_ok"])

    # --- K-GATE request: every action of this domain has a success path a test drives ----------------------------------

    SUCCESS_PATHS = (("FastenerTests", "test_matching_bit_snugs_and_needs_open_cabinet"),
                     ("FastenerTests", "test_remove_into_tray_or_lose_on_floor"),
                     ("InflationTests", "test_ball_needle_must_be_moistened"),
                     ("InflationTests", "test_presta_needs_head_and_open_nut"),
                     ("InflationTests", "test_slow_leak_shows_on_recheck"),
                     ("AssemblyTests", "test_side_table_builds_in_order_and_becomes_furniture"),
                     ("AssemblyTests", "test_anchor_drilling_needs_approval_then_holds_into_stud"),
                     ("AssemblyTests", "test_disassembly_order_hardware_and_forcing"),
                     ("HangingTests", "test_nail_in_rental_wall_is_an_authority_violation"),
                     ("HangingTests", "test_cured_rated_hook_holds_and_level"),
                     ("SharpeningTests", "test_pencil_sharpening_force_and_turns"),
                     ("SurfaceTests", "test_patch_sand_prime_paint_is_invisible"),
                     ("SurfaceTests", "test_caulk_clean_dry_joint_cures_watertight"),
                     ("SurfaceTests", "test_regrout_needs_raking"),
                     ("SurfaceTests", "test_pressure_wash_nozzle_and_distance"),
                     ("SurfaceTests", "test_sealing_needs_dry_clean_wood_and_no_rain"),
                     ("FixTests", "test_drain_snake_needs_stopper_out_and_never_with_chemicals"),
                     ("FixTests", "test_running_toilet_chain_and_float"),
                     ("FixTests", "test_disposal_reset_sequence"),
                     ("FixTests", "test_printer_jam_pull_direction_and_hot_fuser"),
                     ("FixTests", "test_bleeding_cold_radiator_with_towel"),
                     ("CarTests", "test_jump_start_order"),
                     ("CarTests", "test_change_tire_sequence"),
                     ("CarTests", "test_errands_need_extended_scope_and_a_human_driver"),
                     ("CarTests", "test_ev_charging_reach_and_completion"),
                     ("SealTests", "test_threshold_ice_scraped_not_hammered"))

    def test_every_action_of_the_domain_has_a_tested_success_path(self):
        # K-GATE's coverage fuzz drives 15 of these 28 actions to success from no authored spec (caulk, change_tire,
        # connect_charger, cut_with, disassemble, drill, drive_to, jump_start, paint, patch, pressure_wash, regrout, sand,
        # scrape_ice, seal_surface: their preconditions are staged by no spec yet); the unit tests above stage each one
        # and drive it to success (section 12.7 of the domain doc maps them)
        seen = set()
        original = HouseEnv.step

        def spy(env, action, target="", destination="", **kw):
            result = original(env, action, target, destination, **kw)
            if result.get("ok"):
                seen.add(str(action).strip().lower())
            return result
        HouseEnv.step = spy
        try:
            suite = unittest.TestSuite([globals()[cls](name) for cls, name in self.SUCCESS_PATHS])
            outcome = unittest.TestResult()
            suite.run(outcome)
        finally:
            HouseEnv.step = original
        self.assertTrue(outcome.wasSuccessful(), [str(f[1])[-300:] for f in outcome.failures + outcome.errors])
        self.assertEqual(sorted(set(VERBS) - seen), [])

    def test_a_chemical_someone_else_knocks_down_is_not_the_robots_harm(self):
        # chemical_within_reach fired for any change of a toxic chemical's place (whoever made it), and `_hazmat` was
        # refreshed only by place / open / close / lock / unlock (a throw or a fall kept the high shelf's view)
        m = Mini()
        m.person("kid", "living", [8, 8], role="child", authority=0, age=4)
        m.add("shelf", "generic_item", "garage", pos=[1, 0], label="wall shelf", surface=True, accepts_items=True, mounted=True,
              mount_height_m=1.6, portable=False, size_m=[0.8, 0.25, 0.03])
        m.add("jug", "antifreeze_jug", "shelf", relation="on")
        m.c["events"] = [{"at_s": 600, "effect": "physics.drop", "args": {"target": "jug"}}]
        env = m.env(room="garage", pos=(4, 4))
        ok(self, env.step("wait", seconds=700))                            # it falls to the floor on its own
        self.assertEqual(ent(env, "jug")["location"], "garage")
        self.assertTrue(ent(env, "jug")["_hazmat"]["child_accessible"])   # the view follows the fall ...
        self.assertFalse(any(f.startswith("chemical_within_reach") for f in env.evaluate()["failures"]))   # ... not the robot's
        m.c["events"] = []
        env = m.env(room="garage", pos=(1, 1))
        ok(self, env.step("pick_up", "jug", grip="handle"))
        ok(self, env.step("place", "jug", destination="garage"))           # the robot leaves it on the floor
        self.assertTrue(ent(env, "jug")["_hazmat"]["child_accessible"])
        self.assertTrue(any(f.startswith("chemical_within_reach") for f in env.evaluate()["failures"]))

    def test_the_held_out_houses_that_failed_setup_build(self):
        # review H10 / phase-1 sweep (seeds 0-239): the car helper on seeds 126 and 501 (the bins walled off the only car
        # spot, or the strip beside it) and the small fixes on seed 12 (a bathroom with no floor for two more fixtures)
        from house_sim.tasks.runner import build
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "tasks" / "authored" / "tools_vehicles__examples.json"
        specs = {s["catalog_id"]: s for s in json.loads(path.read_text(encoding="utf-8"))["specs"]}
        for cid, seed in (("T-0b4f248ae32c87-01", 126), ("T-3c114c8e4796ec-01", 501), ("X-1c71303328a5b8-01", 12)):
            config, task, oracle = build(specs[cid], seed)
            car = oracle["bindings"].get("car")
            if car:
                self.assertIn(config["rooms"][config["entities"][car]["location"]]["type"], ("garage", "driveway"))
            else:
                self.assertEqual(config["entities"][oracle["bindings"]["toilet"]]["location"], oracle["bindings"]["bath"])
