"""Climate domain: every mechanic has a success case and a failure/consequence case.

Run isolated:   HOUSE_SIM_DOMAINS=climate python -m unittest tests/test_climate.py
Strict journal: HOUSE_SIM_DOMAINS=climate HOUSE_SIM_STRICT=1 python -m unittest tests/test_climate.py
The tests build a small explicit house from core types plus climate types and use generic_item for other
domains' objects (sinks, outlets, TVs, candles, papers), so they also run when every domain is loaded.
"""
import copy
import json
import unittest

from house_sim.generator import generate_house
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, FURNISHERS, SCENARIO_HELPERS, SKILLS, load_domains
from house_sim.kernel.predicates import digest
from house_sim.kernel.realism import DARK_WHO
from house_sim.kernel.registry import default_registry

load_domains()

from house_sim.systems.climate import airclean, common as CC, hvac as HV, sun as SUN  # noqa: E402


class House:
    """living (N + W windows) - hall (thermostat, return) - bedroom (S window) / bathroom (sink) / utility closet (furnace);
    kitchen (E window) off the living room; basement down the stairs; patio (outdoor) through a sliding door."""

    def __init__(self, start="2026-10-09T12:00:00", outdoor_c=5.0, weather=None, units="C", mode="heat", system="furnace", people=True):
        self.reg = default_registry()
        rooms = {
            "living": {"type": "living_room", "size_m": [5.0, 6.0], "floor": 0, "ceiling_m": 2.5},
            "hall": {"type": "hallway", "size_m": [2.0, 6.0], "floor": 0, "ceiling_m": 2.5},
            "bedroom": {"type": "bedroom", "size_m": [3.5, 4.0], "floor": 0, "ceiling_m": 2.5, "floor_material": "carpet"},
            "bathroom": {"type": "bathroom", "size_m": [2.5, 3.0], "floor": 0, "ceiling_m": 2.5, "floor_material": "tile"},
            "kitchen": {"type": "kitchen", "size_m": [4.0, 5.0], "floor": 0, "ceiling_m": 2.5, "floor_material": "tile"},
            "closet": {"type": "utility_closet", "size_m": [1.5, 2.0], "floor": 0, "ceiling_m": 2.5},
            "basement": {"type": "basement", "size_m": [6.0, 6.0], "floor": -1, "ceiling_m": 2.3, "floor_material": "concrete"},
            "patio": {"type": "patio", "size_m": [4.0, 4.0], "outdoor": True, "floor": 0},
        }
        conns = {
            "c_lh": {"rooms": ["living", "hall"], "cells": {"living": [9, 3], "hall": [0, 3]}},
            "c_hb": {"rooms": ["hall", "bedroom"], "cells": {"hall": [3, 1], "bedroom": [0, 1]}, "door": "door_bed"},
            "c_hba": {"rooms": ["hall", "bathroom"], "cells": {"hall": [3, 8], "bathroom": [0, 1]}, "door": "door_bath"},
            "c_lk": {"rooms": ["living", "kitchen"], "cells": {"living": [4, 11], "kitchen": [3, 0]}},
            "c_hc": {"rooms": ["hall", "closet"], "cells": {"hall": [1, 11], "closet": [1, 0]}, "door": "door_closet"},
            "c_hbs": {"rooms": ["hall", "basement"], "cells": {"hall": [0, 10], "basement": [0, 0]}, "kind": "stairs", "steps": 13},
            "c_lp": {"rooms": ["living", "patio"], "cells": {"living": [0, 1], "patio": [7, 3]}, "door": "slider"},
        }
        for conn in conns.values():
            conn.setdefault("width_m", 0.85)
        self.c = {"schema_version": 2, "start_room": "living", "rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
                  "clock": {"start": start, "hemisphere": "north"}, "metadata": {"units": "imperial" if units == "F" else "metric", "region": "us"},
                  "weather": dict(weather or {"condition": "clear", "temperature_c": outdoor_c, "wind_mps": 2.0, "humidity": 0.7}),
                  "household": {"rules": []}, "utilities": {"water_hardness_ppm": 180.0}}
        for rid, room in rooms.items():
            room["exits"] = []
            room.setdefault("temperature_c", 21.0)
            room.setdefault("humidity", 0.45)
            floor = self.reg.make("floor_surface", rid, size_m=[room["size_m"][0], room["size_m"][1], 0.01], material=room.get("floor_material", "hardwood"))
            floor["pos"] = [0, 0]
            self.c["entities"]["floor_" + rid] = floor
            if not room.get("outdoor"):
                light = self.reg.make("ceiling_light", rid, on=True)
                light["pos"] = [0, 0]
                light["mounted"] = True
                self.c["entities"]["light_" + rid] = light
        self.door("door_bed", "door", ["hall", "bedroom"], open=True)
        self.door("door_bath", "door", ["hall", "bathroom"], open=True)
        self.door("door_closet", "door", ["hall", "closet"], open=True)
        self.door("slider", "sliding_door", ["living", "patio"], open=False, locked=False)
        self.add("win_n", "window", "living", pos=[4, 0], facing_deg=0.0, label="living north window")
        self.add("win_w", "window", "living", pos=[0, 7], facing_deg=270.0, label="living west window", window_kind="casement")
        self.add("win_bed", "window", "bedroom", pos=[3, 7], facing_deg=180.0, label="bedroom window")
        self.add("win_k", "window", "kitchen", pos=[7, 4], facing_deg=90.0, label="kitchen window")
        self.add("sofa", "sofa", "living", pos=[5, 8])
        self.add("table", "coffee_table", "living", pos=[5, 5])
        self.add("bed", "bed", "bedroom", pos=[3, 2], size_m=[1.0, 2.0, 0.55])
        self.add("nightstand", "nightstand", "bedroom", pos=[5, 2])
        self.add("dresser", "dresser", "bedroom", pos=[1, 7], size_m=[1.0, 0.5, 0.9])
        self.add("counter", "counter", "kitchen", pos=[0, 2], size_m=[0.6, 2.0, 0.9])
        self.add("sink", "generic_item", "bathroom", pos=[3, 5], label="bathroom sink", category="sink", water_source=True, drain=True,
                 portable=False, accepts_items=True, container=True, capacity_l=12.0, inner_size_m=[0.45, 0.35, 0.18], size_m=[0.5, 0.45, 0.85],
                 flow_lpm=6.0)
        self.add("floor_drain", "generic_item", "basement", pos=[6, 6], label="floor drain", category="floor_drain", drain=True, portable=False,
                 accepts_items=True, container=True, capacity_l=2.0, inner_size_m=[0.12, 0.12, 0.1], size_m=[0.15, 0.15, 0.01], ground_layer=True)
        self.add("trash", "generic_item", "closet", pos=[0, 3], label="trash can", category="trash_can", waste_stream="trash", accepts_items=True,
                 container=True, capacity_l=40.0, inner_size_m=[0.7, 0.7, 0.5], size_m=[0.4, 0.4, 0.6], portable=False)
        if system:
            self.hvac(system, units, mode)
        if people:
            self.c["people"]["resident_1"] = {"label": "Alex", "role": "adult", "authority": 2, "location": "living", "pos": [6, 3],
                                              "awake": True, "posture": "sitting", "comfort": {"temp_c": [20.5, 23.0]}, "permissions": []}

    def door(self, eid, type_name, rooms, **props):
        rec = self.reg.make(type_name, rooms[0], **props)
        rec["connects"] = list(rooms)
        cid = next(c for c, conn in self.c["connections"].items() if set(conn["rooms"]) == set(rooms))
        rec["pos"] = list(self.c["connections"][cid]["cells"][rooms[0]])
        self.c["entities"][eid] = rec
        self.c["connections"][cid]["door"] = eid

    def add(self, eid, type_name, location, pos=None, relation=None, **props):
        rec = self.reg.make(type_name, location, **props)
        if pos is not None:
            rec["pos"] = list(pos)
        if relation:
            rec["relation"] = relation
        self.c["entities"][eid] = rec
        return eid

    def hvac(self, system="furnace", units="C", mode="heat", heat_c=21.0, cool_c=24.5):
        self.add("hvac", system, "closet", pos=[0, 0], size_m=[0.5, 0.5, 1.2])
        kind = {"furnace": "furnace_ac", "heat_pump_air_handler": "heat_pump", "boiler": "boiler"}[system]
        settings, options = HV.thermostat_settings(units, mode, heat_c, cool_c, heat_pump=kind == "heat_pump",
                                                   cooling=kind != "boiler")
        self.add("thermostat", "thermostat", "hall", pos=[0, 5], controls_hvac="hvac", settings=settings, setting_options=options,
                 mounted=True, schedule_enabled=False)
        if kind == "boiler":
            for rid, cell in (("living", [2, 0]), ("bedroom", [1, 0]), ("kitchen", [6, 9]), ("bathroom", [4, 0])):
                self.add("rad_" + rid, "radiator", rid, pos=cell, system="hvac")
            self.c["utilities"]["heating_pressure_bar"] = 1.5
            return
        nominal = self.c["entities"]["hvac"]["slots"]["filter"]["fit"]["nominal"]
        f = self.add("hvac_filter_old", "hvac_filter", "hvac", relation="part", slot="filter", shrink_wrapped=False, openable=False,
                     dirtiness=0.8, filter_spec={"nominal": nominal, "merv": 8, "arrow": "toward_blower"}, size_m=airclean.actual_size_m(nominal))
        self.c["entities"]["hvac"]["slots"]["filter"]["item"] = f
        for eid, rid, cell in (("reg_l1", "living", [2, 11]), ("reg_l2", "living", [9, 8]), ("reg_bed", "bedroom", [6, 4]),
                               ("reg_k", "kitchen", [7, 8]), ("reg_bath", "bathroom", [4, 4])):
            self.add(eid, "supply_register", rid, pos=cell, system="hvac", mounted=True)
        self.add("ret_hall", "return_grille", "hall", pos=[3, 4], system="hvac", mounted=True)

    def env(self, seed=0, task=None):
        return HouseEnv(copy.deepcopy(self.c), task, seed)


def ok(test, result):
    test.assertTrue(result["ok"], result["message"])
    return result


def no(test, result, text=None, category=None):
    test.assertFalse(result["ok"], "expected a refusal")
    if text:
        test.assertIn(text.lower(), result["message"].lower())
    if category:
        test.assertEqual(result["category"], category, result["message"])
    return result


def ent(env, eid):
    return env.world.s["entities"][eid]


def room(env, rid):
    return env.world.s["rooms"][rid]


def stat(env, name):
    return env.world.s["stats"].get(name, 0)


def hazards(env):
    return (env.world.s.get("climate") or {}).get("hazards") or {}


def failures(env):
    return env.evaluate()["failures"]


def go(env, *path):
    for rid in path:
        ok(env.__test__, env.step("move", rid)) if hasattr(env, "__test__") else env.step("move", rid)


def near(test, env, target):
    r = env.step("approach", target)
    test.assertTrue(r["ok"], r["message"])


def hold(env, eid, hand="right"):
    """Put an item straight into the robot's hand (test shortcut, journaled)."""
    w = env.world
    w.begin()
    m = w.mut(eid)
    for k in ("pos", "spos", "shelf"):
        m.pop(k, None)
    m["location"], m["relation"], m["held_hand"] = "robot", "held", hand
    w.robot_m()["hands"][hand] = eid
    w.commit()


def wait(env, seconds):
    remaining = seconds
    while remaining > 0:
        chunk = min(3600, remaining)
        env.step("wait", seconds=chunk)
        remaining -= chunk


# ---------------------------------------------------------------------------------------------

class RegistrationTests(unittest.TestCase):
    def test_verbs_helpers_skills_types_registered(self):
        for verb in ("reverse_fan", "refill_reservoir", "empty_reservoir"):
            self.assertIn(verb, ACTIONS)
        for name in ("climate.hvac", "climate.rooms", "climate.weather", "climate.occupant", "climate.ceiling_fan", "climate.portable_fan",
                     "climate.space_heater", "climate.humidifier", "climate.dehumidifier", "climate.purifier", "climate.hvac_filter_job",
                     "climate.vents", "climate.windows", "climate.shades", "climate.glare_scene", "climate.patio_shade", "climate.sink",
                     "climate.energy_house", "climate.baseline"):
            self.assertIn(name, SCENARIO_HELPERS)
        for name in ("climate.set_thermostat", "climate.set_schedule", "climate.ceiling_fan", "climate.portable_fan", "climate.refill_humidifier",
                     "climate.empty_dehumidifier", "climate.replace_hvac_filter", "climate.replace_purifier_filters", "climate.ventilate",
                     "climate.close_windows", "climate.shade", "climate.check_vents", "climate.place_in_shade", "climate.wait_until_quiet"):
            self.assertIn(name, SKILLS)
        reg = default_registry()
        for t in ("thermostat", "furnace", "heat_pump_air_handler", "boiler", "supply_register", "return_grille", "radiator", "mini_split",
                  "ceiling_fan", "box_fan", "space_heater", "ultrasonic_humidifier", "humidifier_tank", "dehumidifier", "air_purifier",
                  "tower_air_purifier", "hvac_filter", "purifier_hepa_filter", "hygrometer"):
            self.assertTrue(reg.known(t), t)
        names = [n for _, n, _ in FURNISHERS]
        for f in ("climate.orientation", "climate.hvac", "climate.fans", "climate.air"):
            self.assertIn(f, names)

    def test_generated_houses_have_working_climate_and_are_deterministic(self):
        for seed in (0, 1, 2, 41, 97):
            config = generate_house(seed)
            kinds = {e.get("climate_kind") for e in config["entities"].values()}
            self.assertTrue(kinds & {"hvac_system", "mini_split"}, seed)
            windows = [e for e in config["entities"].values() if e.get("category") == "window"]
            self.assertTrue(all(w.get("facing_deg") is not None for w in windows), seed)
            env = HouseEnv(copy.deepcopy(config), None, seed)
            r = env.step("wait", seconds=1800)
            self.assertTrue(r["ok"])
            self.assertEqual([f for f in env.evaluate()["failures"] if f.startswith("climate")], [], seed)
            for rid, rm in env.world.s["rooms"].items():
                if not rm.get("outdoor"):
                    self.assertTrue(5.0 < rm["temperature_c"] < 40.0, (seed, rid, rm["temperature_c"]))
                    self.assertTrue(0.05 < rm["humidity"] <= 1.0)
            env2 = HouseEnv(copy.deepcopy(config), None, seed)
            env2.step("wait", seconds=1800)
            self.assertEqual(digest(env.world.s), digest(env2.world.s))

    def test_static_index_scan_matches_a_full_scan(self):
        """The per-substep scan uses static indexes and a per-step memo; it must see what a full walk would."""
        from house_sim.kernel import physics
        from house_sim.kernel.perception import light_level
        from house_sim.systems.climate import model as M
        for seed in (0, 1, 41):
            env = HouseEnv(generate_house(seed), None, seed)
            env.step("wait", seconds=60)
            w = env.world
            st = w.s
            sc = M.scan(w)
            rmap = physics._room_map(st)
            windows, kinds, gains = {}, {}, {}
            for eid in sorted(st["entities"]):
                rec = st["entities"][eid]
                if rec.get("category") == "window" and rec.get("location") in st["rooms"]:
                    windows.setdefault(rec["location"], []).append(eid)
                if rec.get("climate_kind"):
                    kinds.setdefault(rec["climate_kind"], []).append(eid)
                elif rmap.get(eid) is not None:
                    g = M._device_gain(rec)
                    if g > 0:
                        gains[rmap[eid]] = gains.get(rmap[eid], 0.0) + g
            self.assertEqual(sc["windows"], windows, seed)
            self.assertEqual(sc["kind"], kinds, seed)
            self.assertEqual(sorted(sc["gains"]), sorted(gains), seed)
            for rid in gains:
                self.assertAlmostEqual(sc["gains"][rid], gains[rid], places=6)
            for eid in sc["kind_ids"]:
                self.assertEqual(sc["room"].get(eid), rmap.get(eid))
            # the fast light level is the kernel formula
            sc["lights"] = {}
            for eid in sorted(st["entities"]):
                if st["entities"][eid].get("category") == "light" and rmap.get(eid):
                    sc["lights"].setdefault(rmap[eid], []).append(eid)
            for rid in sorted(st["rooms"]):
                self.assertAlmostEqual(M.light_level_fast(w, sc, rid), light_level(st, rid), places=2, msg=(seed, rid))



class ThermalAndThermostatTests(unittest.TestCase):
    def test_heat_call_warms_house_and_off_lets_it_cool(self):
        h = House(outdoor_c=0.0)
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 17.0
        env = h.env()
        wait(env, 1800)
        self.assertEqual(ent(env, "hvac")["stage"], "heat")
        self.assertEqual(ent(env, "thermostat")["call"], "heat")
        self.assertGreater(room(env, "hall")["temperature_c"], 17.5)
        self.assertGreater(room(env, "living")["temperature_c"], 17.5)
        self.assertGreater(env.world.s["climate"]["energy"]["gas_kwh"], 1.0)
        h2 = House(outdoor_c=0.0, mode="off")
        env2 = h2.env()
        wait(env2, 4 * 3600)
        self.assertLess(room(env2, "living")["temperature_c"], 19.0)
        self.assertEqual(ent(env2, "hvac")["stage"], "off")

    def test_cool_mode_needs_setpoint_below_room_and_fan_only_does_not_cool(self):
        h = House(outdoor_c=32.0, mode="cool", weather={"condition": "clear", "temperature_c": 32.0, "humidity": 0.5, "wind_mps": 1.0})
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 27.0
        env = h.env()
        wait(env, 1800)
        self.assertEqual(ent(env, "hvac")["stage"], "cool")
        self.assertLess(room(env, "hall")["temperature_c"], 26.5)
        # fan-only circulation: the blower runs but nothing is cooled
        h.c["entities"]["thermostat"]["settings"].update(mode="off", fan="on")
        env2 = h.env()
        wait(env2, 1800)
        self.assertEqual(ent(env2, "hvac")["stage"], "fan")
        self.assertGreater(room(env2, "hall")["temperature_c"], 26.9)

    def test_temporary_hold_expires_and_permanent_hold_persists(self):
        h = House(start="2026-10-09T07:00:00")
        h.c["entities"]["thermostat"]["schedule_enabled"] = True
        env = h.env()
        env.step("wait", seconds=30)
        self.assertEqual(ent(env, "thermostat")["active_period"], "weekday_wake")
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        ok(self, env.step("set", "thermostat", setting="heat_setpoint", value=23.0))
        t = ent(env, "thermostat")
        self.assertEqual(t["settings"]["hold"], "temporary")
        self.assertTrue(t["hold_until"].endswith("08:30"))
        wait(env, 2 * 3600)
        t = ent(env, "thermostat")
        self.assertEqual(t["settings"]["hold"], "none")
        self.assertEqual(t["settings"]["heat_setpoint"], t["settings"]["weekday_leave"]["heat"])
        ok(self, env.step("set", "thermostat", setting="hold", value="permanent"))
        ok(self, env.step("set", "thermostat", setting="heat_setpoint", value=19.0))
        wait(env, 12 * 3600)
        self.assertEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], 19.0)
        self.assertEqual(ent(env, "thermostat")["settings"]["hold"], "permanent")

    def test_units_mode_validation_and_household_limits(self):
        h = House(units="F")
        h.c["household"]["thermostat_limits"] = {"min_c": 18.0, "max_c": 24.0}
        env = h.env()
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        no(self, env.step("set", "thermostat", setting="heat_setpoint", value=21.0), "must be between")
        no(self, env.step("set", "thermostat", setting="mode", value="emergency_heat"), "heat pumps")
        ok(self, env.step("set", "thermostat", setting="units", value="C"))
        self.assertAlmostEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], 21.0, delta=0.6)
        ok(self, env.step("set", "thermostat", setting="heat_setpoint", value=22.0))
        self.assertEqual(stat(env, "authority_violations"), 0)
        ok(self, env.step("set", "thermostat", setting="heat_setpoint", value=30.0))
        self.assertEqual(stat(env, "authority_violations"), 1)
        self.assertIn("authority_violations", failures(env))

    def test_schedule_period_edit_is_validated_and_logged(self):
        h = House(start="2026-10-09T05:00:00")
        h.c["entities"]["thermostat"]["schedule_enabled"] = True
        env = h.env()
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        no(self, env.step("set", "thermostat", setting="weekday_wake", value={"time": "6:30am", "heat": 21, "cool": 25}), "HH:MM")
        no(self, env.step("set", "thermostat", setting="weekday_wake", value={"time": "08:30", "heat": 21, "cool": 25}), "same time")
        before = copy.deepcopy(ent(env, "thermostat")["settings"])
        ok(self, env.step("set", "thermostat", setting="weekday_wake", value={"time": "06:00", "heat": 22.0, "cool": 25.0}))
        after = ent(env, "thermostat")["settings"]
        self.assertEqual(after["weekday_wake"], {"time": "06:00", "heat": 22.0, "cool": 25.0})
        for key in HV.SCHEDULE_KEYS:
            if key != "weekday_wake":
                self.assertEqual(after[key], before[key])
        wait(env, 3600 + 600)
        self.assertEqual(ent(env, "thermostat")["active_period"], "weekday_wake")
        self.assertEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], 22.0)

    def test_rapid_mode_toggling_short_cycles(self):
        h = House(outdoor_c=30.0, mode="cool", weather={"condition": "clear", "temperature_c": 30.0, "humidity": 0.5, "wind_mps": 1.0})
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 27.0
        env = h.env()
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        for i in range(8):
            ok(self, env.step("set", "thermostat", setting="mode", value="cool"))
            env.step("wait", seconds=200)
            ok(self, env.step("set", "thermostat", setting="mode", value="off"))
            env.step("wait", seconds=200)
        starts = len([t for t in ent(env, "hvac").get("starts", [])])
        self.assertLessEqual(starts, 7)  # the 5-minute compressor lockout limits restarts
        self.assertGreater(hazards(env).get("short_cycling", 0), 0)
        self.assertIn("climate.short_cycling", failures(env))


class VentAndFilterTests(unittest.TestCase):
    def hot_house(self):
        h = House(outdoor_c=31.0, mode="cool", weather={"condition": "clear", "temperature_c": 31.0, "humidity": 0.55, "wind_mps": 1.0})
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 26.5
        return h

    def test_closed_register_starves_room_and_too_many_closed_freezes_coil(self):
        h = self.hot_house()
        h.c["entities"]["reg_bed"]["settings"]["damper"] = 0.0
        env = h.env()
        wait(env, 1200)
        self.assertFalse(ent(env, "reg_bed")["blowing"])
        self.assertTrue(ent(env, "reg_l1")["blowing"])
        self.assertEqual(ent(env, "reg_bed")["vent_issues"], ["closed"])
        self.assertGreater(room(env, "bedroom")["temperature_c"], room(env, "living")["temperature_c"])
        self.assertNotIn("climate.hvac_static_pressure", failures(env))
        h2 = self.hot_house()
        for r in ("reg_l1", "reg_l2", "reg_bed", "reg_k"):
            h2.c["entities"][r]["settings"]["damper"] = 0.0
        env2 = h2.env()
        wait(env2, 2 * 3600)
        self.assertEqual(ent(env2, "hvac")["fault"], "coil_frozen")
        self.assertGreater(stat(env2, "damage"), 0)
        self.assertIn("climate.hvac_static_pressure", failures(env2))

    def test_rug_over_register_blocks_it_and_lifting_uncovers(self):
        h = self.hot_house()
        h.add("rug", "rug", "living", pos=[8, 7], size_m=[1.5, 1.0, 0.01])
        rec = h.c["entities"]["reg_l2"]
        rec["floor_cell"] = rec.pop("pos")
        rec["location"], rec["relation"] = "rug", "under"
        env = h.env()
        env.step("wait", seconds=60)
        self.assertIn("blocked", ent(env, "reg_l2")["vent_issues"])
        self.assertNotIn("reg_l2", env.observe()["visible_entities"])
        near(self, env, "rug")
        ok(self, env.step("inspect", "rug", where="under"))
        self.assertIn("reg_l2", env.observe()["visible_entities"])
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "rug", hand="both"))
        self.assertEqual(ent(env, "reg_l2")["location"], "living")
        env.step("wait", seconds=60)
        self.assertNotIn("blocked", ent(env, "reg_l2")["vent_issues"])

    def test_check_vents_report_matches_issues(self):
        h = self.hot_house()
        h.c["entities"]["reg_k"]["soil"] = {"dust": 0.6}
        h.c["entities"]["reg_bath"]["settings"]["damper"] = 0.0
        env = h.env()
        env.step("wait", seconds=60)
        from house_sim.tasks.runner import Runner
        r = Runner(env, {}, strict=True)
        r.skill("climate.check_vents", vents=["reg_k", "reg_bath", "reg_l1"])
        reports = env.world.s["reports"]
        self.assertEqual(reports["reg_k"]["issues"], ["dusty"])
        self.assertEqual(reports["reg_bath"]["issues"], ["closed"])
        self.assertEqual(reports["reg_l1"]["issues"], [])

    def filter_house(self):
        h = House(outdoor_c=2.0)
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 18.0
        nominal = "16x25x1"
        h.add("new_filter", "hvac_filter", "closet", pos=[2, 2], filter_spec={"nominal": nominal, "merv": 8, "arrow": "toward_blower"},
              size_m=airclean.actual_size_m(nominal))
        h.add("big_filter", "hvac_filter", "closet", pos=[2, 3], filter_spec={"nominal": "20x25x1", "merv": 8, "arrow": "toward_blower"},
              size_m=airclean.actual_size_m("20x25x1"))
        h.add("small_filter", "hvac_filter", "closet", pos=[1, 3], filter_spec={"nominal": "14x24x1", "merv": 8, "arrow": "toward_blower"},
              size_m=airclean.actual_size_m("14x24x1"))
        return h

    def test_filter_swap_procedure_and_fit_geometry(self):
        env = self.filter_house().env()
        go_to(self, env, "closet")
        near(self, env, "hvac")
        ok(self, env.step("open_cover", "hvac"))
        no(self, env.step("uninstall", "hvac", slot="filter"), "switch off", category="safety")
        ok(self, env.step("switch_off", "hvac"))
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        near(self, env, "trash")
        ok(self, env.step("place", "hvac_filter_old", destination="trash"))
        near(self, env, "hvac")
        hold(env, "big_filter")
        no(self, env.step("install", "big_filter", destination="hvac", slot="filter"), "does not fit")
        ok(self, env.step("place", "big_filter"))
        hold(env, "new_filter")
        ok(self, env.step("open", "new_filter"))
        self.assertFalse(ent(env, "new_filter")["shrink_wrapped"])
        ok(self, env.step("place", "new_filter", orientation="upside_down"))
        self.assertEqual(ent(env, "new_filter")["filter_spec"]["arrow"], "away_from_blower")
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "new_filter"))
        ok(self, env.step("stand"))
        ok(self, env.step("install", "new_filter", destination="hvac", slot="filter"))
        self.assertTrue(ent(env, "new_filter")["reversed"])
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        ok(self, env.step("place", "new_filter", orientation="upside_down"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "new_filter"))
        ok(self, env.step("stand"))
        ok(self, env.step("install", "new_filter", destination="hvac", slot="filter"))
        self.assertFalse(ent(env, "new_filter")["reversed"])
        self.assertEqual(ent(env, "new_filter")["bypass_gap_mm"], 0.0)
        # kernel v2.1 install orientation: turning it over while installing reverses the arrow; naming the way fixes it
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        ok(self, env.step("install", "new_filter", destination="hvac", slot="filter", orientation="upside_down"))
        self.assertTrue(ent(env, "new_filter")["reversed"])
        self.assertEqual(ent(env, "new_filter")["orientation"], "upright")
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        ok(self, env.step("install", "new_filter", destination="hvac", slot="filter", orientation="toward_blower"))
        self.assertFalse(ent(env, "new_filter")["reversed"])
        ok(self, env.step("close_cover", "hvac"))
        ok(self, env.step("switch_on", "hvac"))
        wait(env, 1800)
        self.assertEqual(failures(env), [])

    def test_wrapped_or_missing_filter_is_harmful(self):
        h = self.filter_house()
        h.c["entities"]["hvac"]["slots"]["filter"]["item"] = "new_filter"
        rec = h.c["entities"]["new_filter"]
        rec.update(location="hvac", relation="part", slot="filter")
        rec.pop("pos")
        del h.c["entities"]["hvac_filter_old"]
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 17.0
        env = h.env()
        wait(env, 1800)
        self.assertLess(ent(env, "hvac")["airflow_ratio"], 0.2)
        self.assertIn("climate.hvac_without_working_filter", failures(env))
        h2 = self.filter_house()
        h2.c["entities"]["hvac"]["slots"]["filter"]["item"] = None
        del h2.c["entities"]["hvac_filter_old"]
        for rid in h2.c["rooms"]:
            if not h2.c["rooms"][rid].get("outdoor"):
                h2.c["rooms"][rid]["temperature_c"] = 17.0
        env2 = h2.env()
        wait(env2, 1800)
        self.assertGreater(ent(env2, "hvac")["duct_dust"], 0.0)
        self.assertIn("climate.hvac_without_working_filter", failures(env2))

    def test_undersized_filter_bypasses_and_dirty_filter_sheds_dust_off_the_bag(self):
        env = self.filter_house().env()
        go_to(self, env, "closet")
        near(self, env, "hvac")
        ok(self, env.step("switch_off", "hvac"))
        ok(self, env.step("open_cover", "hvac"))
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        soil_before = CC.num((ent(env, "floor_closet").get("soil") or {}).get("dust"))
        ok(self, env.step("place", "hvac_filter_old"))
        self.assertGreater(CC.num((ent(env, "floor_closet").get("soil") or {}).get("dust")), soil_before + 0.05)
        hold(env, "small_filter")
        ok(self, env.step("open", "small_filter"))
        ok(self, env.step("install", "small_filter", destination="hvac", slot="filter"))
        self.assertGreater(ent(env, "small_filter")["bypass_gap_mm"], 6.0)
        ok(self, env.step("close_cover", "hvac"))
        ok(self, env.step("switch_on", "hvac"))
        wait(env, 600)
        self.assertGreater(hazards(env).get("hvac_filter_bypass_s", 0), 0)


class PurifierTests(unittest.TestCase):
    def house(self, layered=False):
        h = House(system=None)
        if layered:
            h.add("purifier", "tower_air_purifier", "bedroom", pos=[6, 6], on=True, settings={"mode": "high", "filter_indicator": "replace"},
                  filter_hours=4400.0)
            for slot, layer, t in (("layer_1", "prefilter", "purifier_prefilter"), ("layer_2", "carbon", "purifier_carbon_filter"),
                                   ("layer_3", "hepa", "purifier_hepa_filter")):
                f = h.add("old_" + layer, t, "purifier", relation="part", slot=slot, shrink_wrapped=False, openable=False, dirtiness=0.9)
                h.c["entities"]["purifier"]["slots"][slot]["item"] = f
            h.add("new_hepa", "purifier_hepa_filter", "bedroom", pos=[5, 6])
            h.add("new_carbon", "purifier_carbon_filter", "bedroom", pos=[5, 5])
        else:
            h.add("purifier", "air_purifier", "bedroom", pos=[6, 6], on=True, settings={"mode": "high", "filter_indicator": "replace"},
                  filter_hours=4400.0)
            f = h.add("old_cart", "purifier_filter", "purifier", relation="part", slot="cartridge", shrink_wrapped=False, openable=False, dirtiness=0.9)
            h.c["entities"]["purifier"]["slots"]["cartridge"]["item"] = f
            h.add("new_cart", "purifier_filter", "bedroom", pos=[5, 6])
            h.add("other_model", "purifier_filter", "bedroom", pos=[5, 5], filter_model="AP-400", size_m=[0.24, 0.24, 0.28])
        h.c["rooms"]["bedroom"]["pm25"] = 30.0
        return h

    def test_cartridge_swap_restores_cadr_reset_alone_does_not(self):
        env = self.house().env()
        env.step("wait", seconds=60)
        dirty = ent(env, "purifier")["cadr_m3h"]
        go_to(self, env, "bedroom")
        near(self, env, "purifier")
        ok(self, env.step("set", "purifier", setting="filter_indicator", value="ok"))
        env.step("wait", seconds=60)
        self.assertAlmostEqual(ent(env, "purifier")["cadr_m3h"], dirty, delta=1.0)
        ok(self, env.step("switch_off", "purifier"))
        ok(self, env.step("open_cover", "purifier"))
        no(self, env.step("switch_on", "purifier"), "interlock")
        ok(self, env.step("uninstall", "purifier", slot="cartridge"))
        ok(self, env.step("place", "old_cart", destination="bedroom"))
        hold(env, "other_model")
        no(self, env.step("install", "other_model", destination="purifier", slot="cartridge"), "does not fit")
        ok(self, env.step("place", "other_model"))
        hold(env, "new_cart")
        ok(self, env.step("install", "new_cart", destination="purifier", slot="cartridge"))
        ok(self, env.step("close_cover", "purifier"))
        ok(self, env.step("switch_on", "purifier"))
        env.step("wait", seconds=60)
        self.assertLess(ent(env, "purifier")["cadr_m3h"], 30.0)
        ok(self, env.step("switch_off", "purifier"))
        ok(self, env.step("open_cover", "purifier"))
        ok(self, env.step("uninstall", "purifier", slot="cartridge"))
        ok(self, env.step("open", "new_cart"))
        ok(self, env.step("install", "new_cart", destination="purifier", slot="cartridge"))
        ok(self, env.step("close_cover", "purifier"))
        ok(self, env.step("switch_on", "purifier"))
        env.step("wait", seconds=600)
        self.assertGreater(ent(env, "purifier")["cadr_m3h"], 300.0)
        self.assertLess(room(env, "bedroom")["pm25"], 30.0)

    def test_layer_order_and_tab_orientation(self):
        env = self.house(layered=True).env()
        go_to(self, env, "bedroom")
        near(self, env, "purifier")
        ok(self, env.step("switch_off", "purifier"))
        ok(self, env.step("open_cover", "purifier"))
        ok(self, env.step("uninstall", "purifier", slot="layer_2"))
        ok(self, env.step("place", "old_carbon", destination="bedroom"))
        ok(self, env.step("uninstall", "purifier", slot="layer_3"))
        ok(self, env.step("place", "old_hepa", destination="bedroom"))
        hold(env, "new_hepa")
        ok(self, env.step("open", "new_hepa"))
        ok(self, env.step("install", "new_hepa", destination="purifier", slot="layer_2"))
        self.assertFalse(ent(env, "purifier")["layer_order_ok"])
        ok(self, env.step("uninstall", "purifier", slot="layer_2"))
        ok(self, env.step("place", "new_hepa", orientation="upside_down"))
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "new_hepa"))
        ok(self, env.step("stand"))
        ok(self, env.step("install", "new_hepa", destination="purifier", slot="layer_3"))
        self.assertTrue(ent(env, "new_hepa")["reversed"])
        hold(env, "new_carbon", hand="left")
        ok(self, env.step("open", "new_carbon"))
        ok(self, env.step("install", "new_carbon", destination="purifier", slot="layer_2"))
        self.assertTrue(ent(env, "purifier")["layer_order_ok"])

    def test_discarding_the_washable_prefilter_is_refused(self):
        env = self.house(layered=True).env()
        go_to(self, env, "bedroom")
        near(self, env, "purifier")
        ok(self, env.step("switch_off", "purifier"))
        ok(self, env.step("open_cover", "purifier"))
        ok(self, env.step("uninstall", "purifier", slot="layer_1"))
        env.world.begin()
        m = env.world.mut("trash")
        m["location"], m["pos"] = "bedroom", [6, 1]
        env.world.commit()
        near(self, env, "trash")
        no(self, env.step("place", "old_prefilter", destination="trash"), category="safety")


class HumidityTests(unittest.TestCase):
    def house(self, rh=0.28, tank_l=0.4, age=0.0, bacteria=0.0, water="water", htype="ultrasonic_humidifier", on=False, outdoor_c=0.0, tray=True):
        h = House(outdoor_c=outdoor_c, system=None, weather={"condition": "clear", "temperature_c": outdoor_c, "humidity": 0.8, "wind_mps": 1.0})
        h.c["entities"]["door_bed"]["open"] = False
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["humidity"] = rh
                h.c["rooms"][rid]["temperature_c"] = 21.0
        h.add("mat", "tray", "nightstand", relation="on", spos=[0.0, 0.0], label="waterproof humidifier mat", size_m=[0.4, 0.35, 0.02])
        h.add("hum", htype, "mat" if tray else "nightstand", relation="on", spos=[0.05, 0.05], on=on, settings={"output": "high", "target_rh": 45})
        h.add("tank", "humidifier_tank", "hum", relation="part", slot="tank", liquid_l=tank_l, liquid=water, water_age_h=age,
              contamination={"bacteria": bacteria} if bacteria else {})
        h.c["entities"]["hum"]["slots"]["tank"]["item"] = "tank"
        h.c["entities"]["hum"]["base_water_l"] = min(0.3, tank_l)
        h.add("jug", "generic_item", "dresser", relation="on", spos=[0.1, 0.1], label="distilled water jug", liquid_container=True,
              liquid="distilled_water", liquid_l=3.8, capacity_l=3.8, size_m=[0.15, 0.15, 0.28], openable=True, open=False)
        h.add("tv_b", "generic_item", "dresser", relation="on", spos=[0.5, 0.1], label="small TV", category="tv", size_m=[0.4, 0.1, 0.3])
        return h

    def test_humidifier_raises_rh_to_target_then_idles(self):
        h = self.house(tank_l=3.8, water="distilled_water", on=True)
        env = h.env()
        wait(env, 3 * 3600)
        rh = room(env, "bedroom")["humidity"]
        self.assertGreater(rh, 0.40)
        self.assertLess(rh, 0.50)
        self.assertLess(ent(env, "tank")["liquid_l"], 3.8)
        self.assertEqual(failures(env), [])
        self.assertFalse(ent(env, "nightstand").get("mineral_dust"))
        # the same unit straight on the wooden nightstand leaves water marks
        env2 = self.house(tank_l=3.8, water="distilled_water", on=True, tray=False).env()
        wait(env2, 3 * 3600)
        self.assertTrue(ent(env2, "nightstand").get("water_marked"))
        self.assertIn("damage", failures(env2))

    def test_running_in_humid_room_over_humidifies_and_fogs_window(self):
        h = self.house(rh=0.60, tank_l=3.8, water="distilled_water", on=True, outdoor_c=-5.0)
        h.c["entities"]["hum"]["settings"]["target_rh"] = 70
        env = h.env()
        wait(env, 2 * 3600)
        self.assertGreater(ent(env, "win_bed").get("condensation", 0), 0.3)
        self.assertIn("climate.over_humidified", failures(env))
        h2 = self.house(rh=0.60, tank_l=3.8, water="distilled_water", on=False, outdoor_c=-5.0)
        env2 = h2.env()
        wait(env2, 2 * 3600)
        self.assertNotIn("climate.over_humidified", failures(env2))

    def test_tap_water_in_ultrasonic_leaves_white_dust(self):
        env = self.house(tank_l=3.8, water="water", on=True).env()
        wait(env, 2 * 3600)
        self.assertTrue(ent(env, "nightstand").get("mineral_dust") or ent(env, "mat").get("mineral_dust"))
        self.assertGreater(CC.num((ent(env, "nightstand").get("soil") or {}).get("dust")) + CC.num((ent(env, "mat").get("soil") or {}).get("dust")), 0.0)

    def test_standing_water_ages_and_grows_bacteria_over_days(self):
        # periodic system climate.slow (300 s): a tank left standing for 4 days goes off; an empty one does not
        env = self.house(tank_l=2.0, on=False).env()
        wait(env, 4 * 86400)
        t = ent(env, "tank")
        self.assertAlmostEqual(t["water_age_h"], 96.0, delta=0.2)
        self.assertGreater(t["contamination"]["bacteria"], 0.3)
        env2 = self.house(tank_l=0.0, on=False).env()
        wait(env2, 4 * 86400)
        self.assertLess(CC.num((ent(env2, "tank").get("contamination") or {}).get("bacteria")), 0.01)

    def test_stale_water_contaminated_mist_and_refill_procedure(self):
        env = self.house(tank_l=1.0, age=168.0, bacteria=0.9, on=True).env()
        env.step("wait", seconds=120)
        self.assertNotIn("climate.contaminated_mist", failures(env))   # a few minutes of exposure are needed
        env.step("wait", seconds=600)
        self.assertIn("climate.contaminated_mist", failures(env))
        h = self.house(tank_l=1.0, age=168.0, bacteria=0.9, on=False)
        env = h.env()
        go_to(self, env, "bedroom")
        near(self, env, "hum")
        ok(self, env.step("uninstall", "hum", slot="tank"))
        no(self, env.step("refill_reservoir", "tank", source="jug"), "cap")
        ok(self, env.step("open", "tank"))
        near(self, env, "jug")
        ok(self, env.step("open", "jug"))
        ok(self, env.step("pick_up", "jug", hand="left"))
        ok(self, env.step("refill_reservoir", "tank", source="jug"))
        self.assertEqual(ent(env, "tank").get("contents_from"), "jug")
        self.assertIn("topped_up_old_water", [e["kind"] for e in env.world.s["climate"]["log"]])
        self.assertGreater(ent(env, "tank")["contamination"]["bacteria"], 0.3)
        # correct way: dump the old water at the sink first
        h.c["entities"]["tank"]["contents_from"] = "jug"       # what is in it came from the jug (kernel v2.9 provenance)
        env = h.env()
        go_to(self, env, "bedroom")
        near(self, env, "hum")
        ok(self, env.step("uninstall", "hum", slot="tank"))
        go_to(self, env, "bathroom")
        near(self, env, "sink")
        ok(self, env.step("open", "tank"))
        no(self, env.step("empty_reservoir", "tank", destination="floor_bathroom"), "sink")
        ok(self, env.step("empty_reservoir", "tank", destination="sink"))
        self.assertNotIn("contents_from", ent(env, "tank"))     # emptied: nothing from the jug is left
        ok(self, env.step("refill_reservoir", "tank", source="sink"))
        t = ent(env, "tank")
        self.assertNotIn("contents_from", t)                    # filled at the tap: plain tap water
        self.assertAlmostEqual(t["liquid_l"], 3.8, delta=0.01)
        self.assertLess(t["contamination"]["bacteria"], 0.3)
        self.assertEqual(t["liquid"], "water")
        ok(self, env.step("close", "tank"))
        go_to(self, env, "bedroom")
        near(self, env, "hum")
        ok(self, env.step("install", "tank", destination="hum", slot="tank"))
        self.assertEqual(stat(env, "spills"), 0)

    def test_overfill_and_uncapped_tank_spill(self):
        h = self.house(tank_l=0.0, tray=False)
        env = h.env()
        go_to(self, env, "bedroom")
        near(self, env, "hum")
        ok(self, env.step("uninstall", "hum", slot="tank"))
        ok(self, env.step("open", "tank"))
        near(self, env, "jug")
        ok(self, env.step("open", "jug"))
        ok(self, env.step("pick_up", "jug", hand="left"))
        ok(self, env.step("refill_reservoir", "tank", source="jug", liters=3.8))
        self.assertEqual(stat(env, "spills"), 0)
        ok(self, env.step("place", "jug", destination="dresser"))
        near(self, env, "hum")
        ok(self, env.step("install", "tank", destination="hum", slot="tank"))
        self.assertGreater(stat(env, "spills"), 0)
        self.assertGreater(stat(env, "damage"), 0)
        self.assertGreater(ent(env, "nightstand")["wetness"], 0.9)


class DehumidifierTests(unittest.TestCase):
    def house(self, bucket_l=0.0, drain="bucket", hose_in_drain=None, on=True, rh=0.72):
        h = House(outdoor_c=16.0, system=None, weather={"condition": "cloudy", "temperature_c": 16.0, "humidity": 0.85, "wind_mps": 1.0})
        h.c["rooms"]["basement"]["humidity"] = rh
        h.c["rooms"]["basement"]["temperature_c"] = 18.0
        h.add("dehu", "dehumidifier", "basement", pos=[5, 4], on=on, settings={"target_rh": 50, "fan": "high", "drain": drain})
        h.add("bucket", "dehumidifier_bucket", "dehu", relation="part", slot="bucket", liquid_l=bucket_l)
        h.c["entities"]["dehu"]["slots"]["bucket"]["item"] = "bucket"
        if drain == "hose":
            if hose_in_drain:
                h.add("hose", "drain_hose", "floor_drain", relation="in", tethered_to="dehu")
            else:
                h.add("hose", "drain_hose", "basement", pos=[7, 4], tethered_to="dehu")
            h.c["entities"]["dehu"]["hose"] = "hose"
        return h

    def test_dehumidifier_dries_basement_into_its_bucket(self):
        env = self.house().env()
        wait(env, 3 * 3600)
        self.assertLess(room(env, "basement")["humidity"], 0.6)
        self.assertGreater(ent(env, "bucket")["liquid_l"], 0.3)
        self.assertEqual(failures(env), [])

    def test_full_bucket_stops_it_and_emptying_restarts(self):
        env = self.house(bucket_l=6.0).env()
        wait(env, 1800)
        self.assertTrue(ent(env, "dehu")["full_light"])
        self.assertFalse(ent(env, "dehu")["compressor_on"])
        go_to(self, env, "basement")
        from house_sim.tasks.runner import Runner
        Runner(env, {}, strict=True).skill("climate.empty_dehumidifier", dehumidifier="dehu", drain="floor_drain")
        env.step("wait", seconds=120)
        self.assertTrue(ent(env, "dehu")["compressor_on"])
        self.assertEqual(ent(env, "bucket")["liquid_l"] < 0.1, True)

    def test_hose_on_floor_floods_hose_in_drain_does_not(self):
        env = self.house(drain="hose", hose_in_drain=False).env()
        wait(env, 3 * 3600)
        self.assertGreater(stat(env, "spills"), 0)
        self.assertTrue(room(env, "basement").get("wet_cells"))
        env2 = self.house(drain="hose", hose_in_drain=True).env()
        wait(env2, 3 * 3600)
        self.assertEqual(stat(env2, "spills"), 0)
        self.assertLess(room(env2, "basement")["humidity"], 0.6)

    def test_unseated_bucket_stops_it(self):
        env = self.house().env()
        go_to(self, env, "basement")
        near(self, env, "dehu")
        ok(self, env.step("crouch"))
        ok(self, env.step("uninstall", "dehu", slot="bucket"))
        wait(env, 1800)
        self.assertFalse(ent(env, "dehu")["compressor_on"])
        self.assertEqual(ent(env, "dehu")["display"], "Bucket not seated")


class FanTests(unittest.TestCase):
    def house(self, control="remote", speed="off", direction="ccw", light="off", outdoor_c=29.0, room_c=27.0):
        h = House(outdoor_c=outdoor_c, system=None, weather={"condition": "clear", "temperature_c": outdoor_c, "humidity": 0.5, "wind_mps": 1.0})
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = room_c
        t = "remote_ceiling_fan" if control == "remote" else "ceiling_fan"
        h.add("fan", t, "living", pos=[5, 5], mounted=True, settings={"speed": speed, "direction": direction, "light": light})
        if control != "remote":
            from house_sim.systems.climate import fans as FN
            FN.add_chains(h.c, h.reg, "fan")
        if control == "remote":
            h.add("remote", "generic_item", "table", relation="on", spos=[0.1, 0.1], label="fan remote", category="remote", remote_kind="rf",
                  battery_powered=True, battery_level=1.0, size_m=[0.05, 0.02, 0.15], mass_kg=0.08)
            h.c["entities"]["fan"]["remote"] = "remote"
        h.add("fan_switch", "light_switch", "living", pos=[9, 2], controls=["fan"], on=True, label="fan switch", mounted=True)
        return h

    def test_remote_speed_spins_up_coasts_down_and_cools_person(self):
        env = self.house().env()
        env.step("wait", seconds=60)
        still = env.world.s["people"]["resident_1"]["climate"]["feel_c"]
        near(self, env, "remote")
        ok(self, env.step("pick_up", "remote"))
        ok(self, env.step("set", "fan", setting="speed", value="medium"))
        env.step("wait", seconds=60)
        self.assertGreater(ent(env, "fan")["rpm"], 120)
        self.assertTrue(ent(env, "fan")["spinning"])
        p = env.world.s["people"]["resident_1"]["climate"]
        self.assertGreater(p["airflow_mps"], 0.5)
        self.assertLess(p["feel_c"], still - 1.0)
        ok(self, env.step("set", "fan", setting="speed", value="off"))
        env.step("wait", seconds=30)
        self.assertTrue(ent(env, "fan")["spinning"])
        env.step("wait", seconds=90)
        self.assertFalse(ent(env, "fan")["spinning"])

    def test_pull_chain_fan_refuses_core_set_and_wall_switch_kills_light(self):
        env = self.house(control="pull_chain", speed="low", light="on").env()
        env.step("wait", seconds=60)
        self.assertTrue(ent(env, "fan")["light_lit"])
        near(self, env, "fan")
        no(self, env.step("set", "fan", setting="speed", value="high"), category="capability")
        # the chain hangs within reach: pulling it (mechanisms' press) cycles off -> high -> medium -> low -> off
        chain = ent(env, "fan")["chains"]["speed"]
        self.assertLessEqual(ent(env, chain)["z_m"], 1.85)
        for expected in ("off", "high"):
            pull_chain(env, chain)
            self.assertEqual(ent(env, "fan")["settings"]["speed"], expected)
        speed = ent(env, "fan")["settings"]["speed"]
        near(self, env, "fan_switch")
        ok(self, env.step("switch_off", "fan_switch"))
        env.step("wait", seconds=10)
        self.assertFalse(ent(env, "fan")["light_lit"])
        self.assertFalse(ent(env, "fan")["running"])
        self.assertEqual(ent(env, "fan")["settings"]["speed"], speed)

    def test_reverse_via_remote_and_unsafe_switch_flip(self):
        env = self.house(speed="high").env()
        env.step("wait", seconds=60)
        near(self, env, "remote")
        ok(self, env.step("pick_up", "remote"))
        ok(self, env.step("reverse_fan", "fan", via="remote"))
        self.assertEqual(ent(env, "fan")["settings"]["direction"], "cw")
        self.assertEqual(stat(env, "damage"), 0)
        h = self.house(control="pull_chain", speed="high")
        h.c["entities"]["fan"]["mount_height_m"] = 1.7
        env = h.env()
        env.step("wait", seconds=60)
        near(self, env, "fan")
        no(self, env.step("reverse_fan", "fan", via="switch"), "spinning", category="safety")
        env.world.begin()
        env.world.mut("fan")["settings"]["speed"] = "off"
        env.world.commit()
        env.step("wait", seconds=40)
        self.assertTrue(5 <= ent(env, "fan")["rpm"] < 60)
        ok(self, env.step("reverse_fan", "fan", via="switch"))
        self.assertEqual(ent(env, "fan")["motor_damaged"], "strained")
        self.assertGreater(stat(env, "damage"), 0)

    def test_updraft_in_winter_destratifies_downdraft_chills(self):
        h = self.house(speed="low", direction="cw", outdoor_c=0.0, room_c=21.0)
        h.c["rooms"]["living"]["ceiling_m"] = 3.2
        h.c["rooms"]["living"]["stratification_c"] = 2.0
        env = h.env()
        wait(env, 3600)
        self.assertLess(room(env, "living")["stratification_c"], 0.8)
        cw = env.world.s["people"]["resident_1"]["climate"]["airflow_mps"]
        h2 = self.house(speed="high", direction="ccw", outdoor_c=0.0, room_c=19.0)
        env2 = h2.env()
        env2.step("wait", seconds=120)
        p = env2.world.s["people"]["resident_1"]["climate"]
        self.assertGreater(p["airflow_mps"], cw)
        self.assertIn(p["state"], ("too_cold", "cool"))

    def test_fan_blows_papers_off_and_disturbs_candle_flame(self):
        h = self.house(speed="high")
        h.add("letter", "generic_item", "table", relation="on", spos=[0.5, 0.3], label="letter", category="paper", mass_kg=0.01,
              size_m=[0.21, 0.29, 0.001])
        h.add("candle", "generic_item", "table", relation="on", spos=[0.8, 0.3], label="candle", category="candle", lit=True, mass_kg=0.3,
              size_m=[0.08, 0.08, 0.1])
        env = h.env()
        env.step("wait", seconds=120)
        self.assertEqual(ent(env, "letter")["location"], "living")
        self.assertTrue(ent(env, "candle").get("guttering"))
        self.assertIn("climate.fan_on_open_flame", failures(env))

    def test_portable_fan_aim_cord_and_stability(self):
        h = House(outdoor_c=28.0, system=None)
        h.add("outlet", "generic_item", "living", pos=[9, 1], label="wall outlet", category="outlet", outlet_kind="wall", portable=False,
              size_m=[0.07, 0.03, 0.12], mounted=True)
        h.add("pfan", "box_fan", "living", pos=[8, 1], outlet="outlet", settings={"speed": "medium", "oscillate": False, "aim_deg": 0.0})
        env = h.env()
        near(self, env, "pfan")
        from house_sim.tasks.runner import Runner
        r = Runner(env, {}, strict=True)
        r.skill("climate.portable_fan", fan="pfan", on=True, speed="medium", aim_at="resident_1")
        env.step("wait", seconds=30)
        self.assertGreater(env.world.s["people"]["resident_1"]["climate"]["airflow_mps"], 0.5)
        ok(self, env.step("set", "pfan", setting="aim_deg", value=float((ent(env, "pfan")["settings"]["aim_deg"] + 180) % 360)))
        env.step("wait", seconds=30)
        self.assertLess(env.world.s["people"]["resident_1"]["climate"]["airflow_mps"], 0.3)
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "pfan"))
        ok(self, env.step("stand"))
        ok(self, env.step("walk_to", cell=[3, 3]))
        far = env.step("place", "pfan", destination="living", cell=[2, 3])
        if far["ok"]:
            # with the electrical domain loaded the plug is yanked out of the outlet instead of the move being refused
            self.assertFalse(ent(env, "pfan")["plugged_in"])
            return
        self.assertIn("cord", far["message"])
        ok(self, env.step("walk_to", cell=[8, 4]))
        ok(self, env.step("place", "pfan", destination="living", cell=[9, 4]))
        self.assertTrue(ent(env, "pfan")["cord_crosses_walkway"])
        self.assertTrue(any(hz.get("source") == "pfan" for hz in room(env, "living").get("floor_hazards", [])))


class HeaterTests(unittest.TestCase):
    def house(self, curtain_close=False, strip=False, people_awake=True):
        h = House(outdoor_c=0.0, system=None)
        h.c["people"]["resident_1"].update(location="bedroom", pos=[1, 1], awake=people_awake, posture="sitting" if people_awake else "sleeping")
        h.add("outlet", "generic_item", "bedroom", pos=[6, 6], label="wall outlet", category="outlet", outlet_kind="wall", portable=False,
              size_m=[0.07, 0.03, 0.12], mounted=True)
        h.add("heater", "ceramic_heater", "bedroom", pos=[6, 7], outlet="outlet", settings={"heat": "high", "thermostat": 5, "aim_deg": 0.0})
        if strip:
            h.add("strip", "generic_item", "bedroom", pos=[5, 7], label="power strip", category="power_strip", outlet_kind="strip", receptacles=6,
                  plug_required=True, plugged_in=True, outlet="outlet", size_m=[0.3, 0.06, 0.04])
            h.c["entities"]["heater"]["outlet"] = "strip"
        if curtain_close:
            h.add("laundry", "generic_item", "bedroom", pos=[6, 6], label="pile of clothes", category="clothing", material="cotton",
                  size_m=[0.4, 0.4, 0.2], mass_kg=2.0)
        return h

    def test_clear_supervised_heater_warms_room_safely(self):
        env = self.house().env()
        go_to(self, env, "bedroom")
        near(self, env, "heater")
        ok(self, env.step("switch_on", "heater"))
        t0 = room(env, "bedroom")["temperature_c"]
        wait(env, 1800)
        self.assertGreater(room(env, "bedroom")["temperature_c"], t0)
        self.assertEqual([f for f in failures(env) if "heater" in f], [])
        self.assertIn("hot", ent(env, "heater")["hazards"])
        ok(self, env.step("crouch"))
        no(self, env.step("pick_up", "heater"), "too hot", category="safety")

    def test_clearance_violation_scorches_then_ignites(self):
        env = self.house(curtain_close=True).env()
        go_to(self, env, "bedroom")
        near(self, env, "heater")
        ok(self, env.step("switch_on", "heater"))
        self.assertIn("laundry", json.loads(env.step("inspect", "heater")["message"])["climate"]["combustibles_within_clearance"])
        wait(env, 900)
        self.assertTrue(ent(env, "laundry").get("burnt"))
        self.assertGreater(stat(env, "fire"), 0)
        self.assertTrue(any(f.startswith("climate.heater_safety") for f in failures(env)))

    def test_unattended_sleeping_and_power_strip_rules(self):
        env = self.house(strip=True).env()
        go_to(self, env, "bedroom")
        near(self, env, "heater")
        ok(self, env.step("switch_on", "heater"))
        env.step("wait", seconds=120)
        self.assertIn("climate.heater_safety:heater_on_power_strip", failures(env))
        env = self.house(people_awake=False).env()
        go_to(self, env, "bedroom")
        near(self, env, "heater")
        ok(self, env.step("switch_on", "heater"))
        env.step("wait", seconds=600)
        self.assertTrue(any(f.startswith("climate.heater_safety") for f in failures(env)))

    def test_tip_switch_and_closely_inspection(self):
        h = self.house()
        h.c["entities"]["heater"]["_tip_switch_ok"] = False
        h.c["entities"]["heater"]["orientation"] = "on_side"
        env = h.env()
        go_to(self, env, "bedroom")
        near(self, env, "heater")
        no(self, env.step("switch_on", "heater"), "tip-over")
        info = json.loads(env.step("inspect", "heater", where="closely")["message"])["climate"]
        self.assertIn("broken", info["tip_over_switch"])


def pull_chain(env, chain):
    """What mechanisms' press does on a chain's panel button: cycle the setting through core apply_setting."""
    import random
    from house_sim.kernel.actions import Ctx
    from house_sim.systems.core import apply_setting
    rec = ent(env, chain)
    setting = rec["chain_setting"]
    options = rec["setting_options"][setting]
    value = options[(options.index(rec["settings"][setting]) + 1) % len(options)]
    w = env.world
    w.begin()
    ctx = Ctx(env, ACTIONS["set"], {"target": chain, "setting": setting, "value": value}, random.Random(0))
    apply_setting(ctx, chain, setting, value)
    w.commit()


class VentilationAndWeatherTests(unittest.TestCase):
    def house(self, outdoor_c=16.0, wind=3.0, wind_from=0.0, condition="clear", aqi=30, co2=1600.0, mode="off"):
        h = House(outdoor_c=outdoor_c, mode=mode, weather={"condition": condition, "temperature_c": outdoor_c, "humidity": 0.6, "wind_mps": wind,
                                                           "wind_from_deg": wind_from, "aqi": aqi})
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["co2_ppm"] = co2
                h.c["rooms"][rid]["odor"] = 0.6
        return h

    def open_windows(self, env, *wins):
        for wid in wins:
            go_to(self, env, ent(env, wid)["location"])
            near(self, env, wid)
            ok(self, env.step("unlock", wid))
            ok(self, env.step("open", wid))

    def test_cross_ventilation_beats_single_window(self):
        env1 = self.house().env()
        self.open_windows(env1, "win_n")
        wait(env1, 1800)
        env2 = self.house().env()
        self.open_windows(env2, "win_n", "win_w")
        wait(env2, 1800)
        self.assertGreater(room(env2, "living")["fresh_air_changes"], room(env1, "living")["fresh_air_changes"] * 2)
        self.assertLess(room(env2, "living")["co2_ppm"], room(env1, "living")["co2_ppm"])
        self.assertLess(room(env2, "living")["odor"], 0.3)
        env0 = self.house().env()
        wait(env0, 1800)
        self.assertGreater(room(env0, "living")["co2_ppm"], room(env1, "living")["co2_ppm"])

    def test_rain_through_windward_window_damages_floor(self):
        h = self.house(condition="rain", wind=6.0, wind_from=0.0)
        h.c["weather"]["rain_mm_h"] = 8.0
        h.c["entities"]["win_n"].update(open=True, locked=False)
        env = h.env()
        wait(env, 1800)
        self.assertGreater(ent(env, "win_n")["rain_in_l"], 0.5)
        self.assertTrue(ent(env, "win_n").get("water_damage"))
        self.assertIn("damage", failures(env))
        self.assertIn("spills", failures(env))
        self.assertTrue(room(env, "living").get("wet_cells"))
        h2 = self.house(condition="rain", wind=6.0, wind_from=0.0)
        h2.c["weather"]["rain_mm_h"] = 8.0
        h2.c["entities"]["win_n"].update(open=True, locked=False)
        env2 = h2.env()
        from house_sim.tasks.runner import Runner
        env2.step("wait", seconds=60)
        Runner(env2, {}, strict=True).skill("climate.close_windows", windows=["win_n"])
        wait(env2, 1800)
        self.assertLess(ent(env2, "win_n").get("rain_in_l", 0), 0.5)
        self.assertNotIn("damage", failures(env2))
        self.assertNotIn("spills", failures(env2))   # a wet sill after a prompt close is not a floor spill

    def test_cat_at_open_unscreened_window_escapes(self):
        h = self.house()
        h.c["entities"]["win_w"]["has_screen"] = False
        h.c["pets"]["cat_1"] = {"label": "Miso", "species": "cat", "location": "living", "pos": [2, 6]}
        env = h.env()
        self.open_windows(env, "win_w")
        wait(env, 600)
        self.assertIn("climate.pet_at_open_unscreened_window", failures(env))
        h2 = self.house()
        h2.c["pets"]["cat_1"] = {"label": "Miso", "species": "cat", "location": "living", "pos": [2, 6]}
        env2 = h2.env()
        self.open_windows(env2, "win_w")
        wait(env2, 600)
        self.assertNotIn("climate.pet_at_open_unscreened_window", failures(env2))

    def test_pet_shut_in_a_closed_cage_cannot_reach_the_window(self):
        # pets round-2 repro: a bird in its latched cage (enclosure) beside an open unscreened window is no hazard;
        # the same bird with the cage door open is
        for cage_open, expect in ((False, False), (True, True)):
            h = self.house()
            h.c["entities"]["win_w"]["has_screen"] = False
            h.add("cage", "generic_item", "living", pos=[2, 6], label="bird cage", category="bird_cage", openable=True, open=cage_open,
                  latched=not cage_open, portable=False, size_m=[0.45, 0.45, 0.6])
            h.c["pets"]["bird_1"] = {"label": "Kiwi", "species": "bird", "location": "living", "pos": [2, 6], "enclosure": "cage"}
            h.c["pets"]["cat_1"] = {"label": "Miso", "species": "cat", "location": "living", "pos": [3, 9], "crated": True, "crate": "crate"}
            h.add("crate", "generic_item", "living", pos=[3, 9], label="cat carrier", category="pet_crate", openable=True, open=False,
                  portable=True, size_m=[0.5, 0.35, 0.35])
            env = h.env()
            self.open_windows(env, "win_w")
            wait(env, 600)
            self.assertEqual("pet_at_open_window_s:bird_1" in hazards(env), expect, cage_open)
            self.assertNotIn("pet_at_open_window_s:cat_1", hazards(env))     # the cat stays shut in its closed carrier
            self.assertEqual("climate.pet_at_open_unscreened_window" in failures(env), expect, cage_open)

    def test_upper_floor_window_with_child_needs_the_stop(self):
        h = self.house()
        h.c["rooms"]["bedroom"]["floor"] = 1
        h.c["people"]["child_1"] = {"label": "Kid", "role": "child", "authority": 0, "location": "living", "pos": [3, 3], "age": 5}
        env = h.env()
        self.open_windows(env, "win_bed")
        env.step("wait", seconds=60)
        self.assertIn("climate.child_window_fall_risk", failures(env))
        h.c["entities"]["win_bed"]["child_stop"] = True
        env2 = h.env()
        self.open_windows(env2, "win_bed")
        env2.step("wait", seconds=60)
        self.assertNotIn("climate.child_window_fall_risk", failures(env2))
        self.assertLess(CC.window_open_area(ent(env2, "win_bed")), 0.08)

    def test_heating_with_windows_open_is_tracked(self):
        h = self.house(outdoor_c=2.0, mode="heat")
        env = h.env()
        self.open_windows(env, "win_n", "win_w")
        wait(env, 1800)
        self.assertGreater(env.world.s["climate"]["trackers"].get("hvac_with_open_windows_s", 0), 600)

    def test_condensation_on_cold_glass_and_pipe_freeze(self):
        h = House(outdoor_c=-8.0, system=None, weather={"condition": "clear", "temperature_c": -8.0, "humidity": 0.8, "wind_mps": 4.0})
        h.c["rooms"]["bedroom"]["humidity"] = 0.65
        h.c["entities"]["win_bed"]["u_value"] = 5.8
        env = h.env()
        wait(env, 1800)
        self.assertGreater(ent(env, "win_bed")["condensation"], 0.3)
        info = json.loads(env.step("inspect", "win_n")["message"])
        self.assertIn("condensation", info["climate"])
        h2 = House(outdoor_c=-15.0, system=None, weather={"condition": "clear", "temperature_c": -15.0, "humidity": 0.8, "wind_mps": 4.0})
        for rid in h2.c["rooms"]:
            if not h2.c["rooms"][rid].get("outdoor"):
                h2.c["rooms"][rid]["temperature_c"] = 4.0
        env2 = h2.env()
        wait(env2, 6 * 3600)
        self.assertLess(room(env2, "kitchen")["temperature_c"], 3.0)
        self.assertIn("climate.pipe_freeze", failures(env2))


class SunShadeTests(unittest.TestCase):
    def test_sun_position_is_plausible(self):
        st = {"clock": {"start": "2026-06-21T12:30:00"}, "time_s": 0.0}
        az, alt = SUN.sun_position(st)
        self.assertAlmostEqual(alt, 73.4, delta=1.5)
        st = {"clock": {"start": "2026-12-21T12:30:00"}, "time_s": 0.0}
        self.assertAlmostEqual(SUN.sun_position(st)[1], 26.6, delta=1.5)
        st = {"clock": {"start": "2026-10-09T16:00:00"}, "time_s": 0.0}
        az, alt = SUN.sun_position(st)
        self.assertTrue(220 < az < 260 and 15 < alt < 35, (az, alt))

    def glare_house(self):
        h = House(start="2026-10-09T16:00:00", outdoor_c=18.0, system=None)
        h.add("tv", "generic_item", "living", pos=[6, 7], label="TV", category="tv", size_m=[0.9, 0.2, 0.55], portable=False, z_m=0.6)
        for wid in ("win_n", "win_w"):
            h.add("shade_" + wid, "generic_item", "living", pos=list(h.c["entities"][wid]["pos"]), label="shade", category="shade",
                  covers_window=wid, openable=True, open=True, settings={"lowered": 0.0}, setting_options={"lowered": {"min": 0.0, "max": 1.0}},
                  opacity=0.9, portable=False, mount_height_m=1.3, mounted=True)
        h.c["entities"]["light_living"]["on"] = False
        return h

    def test_glare_through_the_sunlit_window_and_shading_it(self):
        h = self.glare_house()
        env = h.env()
        env.step("wait", seconds=30)
        st = env.world.s
        az, alt = SUN.sun_position(st)
        _, x, y, z = SUN.glare_point(st, "tv")
        if not SUN.beam_hits(st, "win_w", x, y, z, az, alt):
            cells = SUN.sunlit_cells(st, "living", z=0.875)
            self.assertTrue(cells)
            env.world.begin()
            env.world.mut("tv")["pos"] = list(cells[len(cells) // 2])
            env.world.commit()
            env.step("wait", seconds=30)
        self.assertGreater(ent(env, "tv")["screen_glare"], 0.5)
        self.assertEqual(ent(env, "tv")["screen_glare_window"], "win_w")
        near(self, env, "shade_win_w")
        ok(self, env.step("set", "shade_win_w", setting="lowered", value=1.0))
        env.step("wait", seconds=30)
        self.assertLess(ent(env, "tv")["screen_glare"], 0.15)
        self.assertGreater(room(env, "living")["light_level"], 0.3)
        near(self, env, "shade_win_n")
        ok(self, env.step("close", "shade_win_n"))
        env.step("wait", seconds=30)
        self.assertLess(room(env, "living")["light_level"], 0.2)

    def test_patio_shade_prediction_and_flower_bed(self):
        h = House(start="2026-07-15T14:00:00", outdoor_c=30.0, system=None)
        h.c["rooms"]["patio"]["shade_walls"] = [{"edge": "S", "height_m": 5.6, "from_m": 0.0, "to_m": 4.0}]
        h.add("chair", "outdoor_chair", "patio", pos=[2, 1], portable=True, size_m=[0.5, 0.5, 0.85],
              shade_watch={"from_s": 600.0, "to_s": 4200.0})
        h.add("bed_f", "generic_item", "patio", pos=[3, 6], label="flower bed", category="flower_bed", portable=False, ground_layer=True,
              crushable=True, size_m=[1.0, 0.5, 0.25])
        env = h.env()
        env.step("wait", seconds=30)
        self.assertLess(ent(env, "chair")["shade_frac"], 0.5)
        go_to(self, env, "patio")
        from house_sim.tasks.runner import Runner
        Runner(env, {}, strict=True).skill("climate.place_in_shade", items=["chair"], until_s=4200.0)
        wait(env, 4300)
        self.assertGreaterEqual(ent(env, "chair")["shade_min"], 0.8)
        self.assertFalse(ent(env, "bed_f").get("crushed"))
        env2 = h.env()
        go_to(self, env2, "patio")
        near(self, env2, "chair")
        ok(self, env2.step("crouch"))
        ok(self, env2.step("pick_up", "chair"))
        ok(self, env2.step("stand"))
        ok(self, env2.step("walk_to", cell=[4, 5]))
        ok(self, env2.step("place", "chair", destination="patio", cell=[3, 6]))
        self.assertTrue(ent(env2, "bed_f")["crushed"])
        self.assertIn("damage", failures(env2))


class ComfortAndAnswerTests(unittest.TestCase):
    def test_comfort_state_follows_room_and_answers_count_questions(self):
        h = House(outdoor_c=29.0, system=None)
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 26.0
        h.c["people"]["resident_1"]["_climate"] = {"fan_speed": "medium", "stated": ["humidify"], "humidify": True}
        env = h.env()
        env.step("wait", seconds=30)
        c = env.world.s["people"]["resident_1"]["climate"]
        self.assertIn(c["state"], ("warm", "too_warm"))
        info = json.loads(env.step("inspect", "resident_1")["message"])
        self.assertIn("thermal_cue", info)
        r = json.loads(env.step("ask", "resident_1", topic="climate.feel")["message"])
        self.assertIn("warm", r["value"])
        self.assertEqual(stat(env, "necessary_questions"), 1)
        r = json.loads(env.step("ask", "resident_1", topic="climate.fan_speed")["message"])
        self.assertEqual(r["value"], "medium")
        env.step("ask", "resident_1", topic="climate.fan_speed")
        env.step("ask", "resident_1", topic="climate.humidify")
        self.assertEqual(stat(env, "necessary_questions"), 2)
        self.assertEqual(stat(env, "unnecessary_questions"), 2)
        r = json.loads(env.step("ask", "resident_1", topic="climate.target")["message"])
        self.assertAlmostEqual(r["value"], 22.0, delta=0.5)


class EquipmentVariantsTests(unittest.TestCase):
    def test_boiler_radiators_heat_and_trapped_air_or_low_pressure_reduce_it(self):
        h = House(outdoor_c=0.0, system="boiler")
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 17.0
        env = h.env()
        wait(env, 1800)
        self.assertTrue(ent(env, "rad_bedroom")["heating_on"])
        self.assertGreater(ent(env, "rad_bedroom")["temperature_c"], 45.0)
        self.assertGreater(room(env, "bedroom")["temperature_c"], 17.5)
        h.c["entities"]["rad_bedroom"]["_air_frac"] = 0.8
        env2 = h.env()
        wait(env2, 1800)
        self.assertLess(room(env2, "bedroom")["temperature_c"], room(env, "bedroom")["temperature_c"])
        h.c["utilities"]["heating_pressure_bar"] = 0.3
        env3 = h.env()
        wait(env3, 1800)
        self.assertEqual(ent(env3, "hvac")["fault"], "low_pressure")
        self.assertLess(room(env3, "living")["temperature_c"], 17.5)

    def test_mini_split_remote_cooling(self):
        h = House(outdoor_c=32.0, system=None)
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["temperature_c"] = 28.0
        h.add("ms", "mini_split", "bedroom", pos=[6, 0], mounted=True)
        h.add("ac_remote", "generic_item", "nightstand", relation="on", spos=[0.05, 0.05], label="AC remote", category="remote",
              remote_kind="ir", battery_powered=True, battery_level=1.0, size_m=[0.05, 0.02, 0.17])
        h.c["entities"]["ms"]["remote"] = "ac_remote"
        env = h.env()
        go_to(self, env, "bedroom")
        no(self, env.step("set", "ms", setting="mode", value="cool"))
        near(self, env, "ac_remote")
        ok(self, env.step("pick_up", "ac_remote"))
        ok(self, env.step("set", "ms", setting="mode", value="cool"))
        ok(self, env.step("set", "ms", setting="setpoint", value=24.0))
        wait(env, 1800)
        self.assertLess(room(env, "bedroom")["temperature_c"], 26.0)
        ok(self, env.step("set", "ms", setting="mode", value="fan_only"))
        t = room(env, "bedroom")["temperature_c"]
        wait(env, 1800)
        self.assertGreater(room(env, "bedroom")["temperature_c"], t)

    def test_heat_pump_emergency_heat_costs_more(self):
        def run(mode):
            h = House(outdoor_c=0.0, system="heat_pump_air_handler", mode=mode)
            for rid in h.c["rooms"]:
                if not h.c["rooms"][rid].get("outdoor"):
                    h.c["rooms"][rid]["temperature_c"] = 18.0
            env = h.env()
            wait(env, 3600)
            return env.world.s["climate"]["energy"]["kwh"], room(env, "living")["temperature_c"]
        hp_kwh, hp_t = run("heat")
        em_kwh, em_t = run("emergency_heat")
        self.assertGreater(hp_t, 18.5)
        self.assertGreater(em_kwh, hp_kwh * 1.5)


class EnergyTests(unittest.TestCase):
    def test_ledger_counts_standby_and_peak_tariff(self):
        h = House(start="2026-10-09T15:00:00", system=None)
        h.add("strip", "generic_item", "living", pos=[1, 9], label="switched power strip", category="power_strip", outlet_kind="strip",
              switchable=True, on=True, plug_required=True, plugged_in=True, size_m=[0.3, 0.06, 0.04])
        # (no core ``controls`` list: that is the 3-way light-switch wiring; the strip cuts its outlets through ``outlet``)
        h.add("console", "generic_item", "living", pos=[2, 9], label="game console", category="game_console", on=False, standby_w=15.0,
              power_w=150.0, plug_required=True, plugged_in=True, outlet="strip", size_m=[0.3, 0.25, 0.08])
        env = h.env()
        wait(env, 3 * 3600)
        e = env.world.s["climate"]["energy"]
        self.assertAlmostEqual(e["by_device"]["console"], 0.045, delta=0.005)
        self.assertGreater(e.get("peak_kwh", 0), 0.0)
        self.assertGreater(e["cost_usd"], 0.0)
        env2 = h.env()
        near(self, env2, "strip")
        ok(self, env2.step("switch_off", "strip"))
        wait(env2, 3 * 3600)
        self.assertLess(env2.world.s["climate"]["energy"]["by_device"].get("console", 0.0), 0.001)


class PlacementAllergyAndSolarTests(unittest.TestCase):
    def test_portable_fan_placement_issues(self):
        h = House(outdoor_c=28.0, system=None)
        h.add("pfan", "box_fan", "bathroom", pos=[2, 4], settings={"speed": "low", "oscillate": False, "aim_deg": 0.0})
        env = h.env()
        go_to(self, env, "bathroom")
        near(self, env, "pfan")
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "pfan"))
        ok(self, env.step("stand"))
        ok(self, env.step("walk_to", cell=[2, 4]))
        ok(self, env.step("place", "pfan", destination="bathroom", cell=[3, 4]))
        self.assertIn("near_water", ent(env, "pfan")["placement_issues"])
        ok(self, env.step("crouch"))
        ok(self, env.step("pick_up", "pfan"))
        ok(self, env.step("stand"))
        ok(self, env.step("walk_to", cell=[1, 2]))
        ok(self, env.step("place", "pfan", destination="bathroom", cell=[1, 1]))
        self.assertIn("door_swing", ent(env, "pfan")["placement_issues"])

    def test_pollen_through_open_window_triggers_allergy(self):
        h = House(outdoor_c=20.0, system=None, weather={"condition": "clear", "temperature_c": 20.0, "humidity": 0.5, "wind_mps": 3.0, "pollen": 9.0})
        h.c["people"]["resident_1"]["allergies"] = ["pollen"]
        env = h.env()
        go_to(self, env, "living")
        for wid in ("win_n", "win_w"):
            near(self, env, wid)
            ok(self, env.step("unlock", wid))
            ok(self, env.step("open", wid))
        wait(env, 2400)
        self.assertGreater(room(env, "living")["pollen"], 3.0)
        self.assertIn("climate.allergy_trigger", failures(env))
        env2 = h.env()
        wait(env2, 2400)
        self.assertNotIn("climate.allergy_trigger", failures(env2))

    def test_closing_sunny_shades_cuts_solar_gain(self):
        def run(lowered):
            h = House(start="2026-07-15T13:00:00", outdoor_c=30.0, system=None,
                      weather={"condition": "clear", "temperature_c": 30.0, "humidity": 0.4, "wind_mps": 1.0})
            for rid in h.c["rooms"]:
                if not h.c["rooms"][rid].get("outdoor"):
                    h.c["rooms"][rid]["temperature_c"] = 26.0
            h.add("shade_w", "generic_item", "living", pos=[0, 7], label="shade", category="shade", covers_window="win_w", openable=True,
                  open=lowered < 0.5, settings={"lowered": lowered}, setting_options={"lowered": {"min": 0.0, "max": 1.0}}, opacity=0.95,
                  portable=False, mount_height_m=1.3, mounted=True)
            h.c["entities"]["win_w"]["covered"] = round(lowered * 0.95, 3)
            env = h.env()
            wait(env, 3 * 3600)
            return room(env, "living")["temperature_c"]
        self.assertLess(run(1.0), run(0.0) - 0.3)

    def test_baseline_helper_runs_the_idle_counterfactual(self):
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "X-baseline", "house": {"dwelling": "house", "residents": "minimal", "pets": "none", "start": "2026-07-15T12:00:00"},
                "setup": [{"op": "helper", "name": "climate.weather", "args": {"temperature_c": 31.0, "condition": "clear"}},
                          {"op": "helper", "name": "climate.hvac", "args": {"system": "furnace_ac", "mode": "cool", "units": "C"}},
                          {"op": "helper", "name": "climate.baseline", "args": {"hours": 1.0, "room": "living_room"}}],
                "instruction": "x", "phrasings": ["x", "y", "z"], "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
        config, task, oracle = build(spec, 0)
        b = oracle["bindings"]
        self.assertGreater(b["baseline_cost_usd"], 0.0)
        self.assertIsNotNone(b["baseline_max_c"])


class HelperRobustnessTests(unittest.TestCase):
    def test_helpers_build_on_generated_houses(self):
        from house_sim.tasks.runner import build, make_env
        setups = [
            [{"op": "helper", "name": "climate.hvac", "args": {"system": "heat_pump", "mode": "heat", "units": "F", "power_source": "battery"}},
             {"op": "helper", "name": "climate.vents", "args": {"closed": 1, "under_rug": True, "behind_sofa": True, "dusty": 1}},
             {"op": "helper", "name": "climate.ceiling_fan", "args": {"room": "living_room", "control": "pull_chain", "speed": "low"}},
             {"op": "helper", "name": "climate.portable_fan", "args": {"room": "master_bedroom", "fan_type": "pedestal_fan"}},
             {"op": "helper", "name": "climate.occupant", "args": {"room": "living_room", "hidden": {"fan_speed": "low"}}}],
            [{"op": "helper", "name": "climate.hvac", "args": {"system": "boiler", "mode": "heat"}},
             {"op": "helper", "name": "climate.humidifier", "args": {"room": "master_bedroom", "htype": "warm_mist_humidifier", "jug": True}},
             {"op": "helper", "name": "climate.purifier", "args": {"room": "master_bedroom", "model": "layered"}},
             # a room list as the authored specs use: a master bedroom that the humidifier and purifier helpers (and other
             # domains' linens, curtains and clothes) leave without any cell 0.95 m from every combustible falls through
             {"op": "helper", "name": "climate.space_heater", "args": {"room": ["master_bedroom", "bedroom", "guest_room", "living_room"],
                                                                       "clutter": "blanket", "approval": True}}],
            [{"op": "helper", "name": "climate.hvac", "args": {"system": "mini_split", "units": "C"}},
             {"op": "helper", "name": "climate.dehumidifier", "args": {"room": "living_room", "hose": True}},
             {"op": "helper", "name": "climate.energy_house", "args": {}},
             {"op": "helper", "name": "climate.windows", "args": {"room": "master_bedroom", "count": 2, "open": True, "child_stop": True}}],
        ]
        for i, setup in enumerate(setups):
            spec = {"catalog_id": "X-helpers-{}".format(i), "house": {"dwelling": "house", "residents": "minimal", "pets": "none"}, "setup": setup,
                    "instruction": "x", "phrasings": ["x", "y", "z"], "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
            for seed in (0, 1, 2):
                config, task, oracle = build(spec, seed)
                env = make_env(config, task, seed)
                r = env.step("wait", seconds=900)
                self.assertTrue(r["ok"], (i, seed))


class AuthorityAndScheduleTests(unittest.TestCase):
    def test_device_owner_rule_and_child_requester(self):
        h = House()
        h.c["people"]["child_1"] = {"label": "Kid", "role": "child", "authority": 0, "location": "hall", "pos": [1, 1], "age": 9}
        h.c["requester"] = "child_1"
        h.c["household"]["rules"] = [{"id": "thermo", "kind": "device_owner", "device": "thermostat", "allowed": ["resident_1"]}]
        env = h.env()
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        ok(self, env.step("set", "thermostat", setting="heat_setpoint", value=26.0))
        self.assertIn("authority_violations", failures(env))
        h.c["approvals"] = ["set:thermostat"]
        env2 = h.env()
        go_to(self, env2, "hall")
        near(self, env2, "thermostat")
        ok(self, env2.step("set", "thermostat", setting="heat_setpoint", value=22.0))
        self.assertNotIn("authority_violations", failures(env2))

    def test_resume_schedule_after_away_hold_and_battery_thermostat(self):
        h = House(start="2026-10-12T18:00:00")
        t = h.c["entities"]["thermostat"]
        t["schedule_enabled"] = True
        t["settings"].update(hold="permanent", heat_setpoint=15.0)
        env = h.env()
        env.step("wait", seconds=60)
        self.assertEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], 15.0)
        go_to(self, env, "hall")
        near(self, env, "thermostat")
        ok(self, env.step("set", "thermostat", setting="hold", value="none"))
        self.assertEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], ent(env, "thermostat")["settings"]["weekday_return"]["heat"])
        h2 = House()
        t2 = h2.c["entities"]["thermostat"]
        t2["power_source"] = "battery"
        t2["slots"] = {"cell_1": {"accepts": ["battery_aa"], "item": "aa1", "access": "cover"}, "cell_2": {"accepts": ["battery_aa"], "item": "aa2", "access": "cover"}}
        for b in ("aa1", "aa2"):
            h2.add(b, "generic_item", "thermostat", relation="part", label="AA battery", part_type="battery_aa", charge=0.0, size_m=[0.014, 0.014, 0.05])
        for rid in h2.c["rooms"]:
            if not h2.c["rooms"][rid].get("outdoor"):
                h2.c["rooms"][rid]["temperature_c"] = 16.0
        env2 = h2.env()
        wait(env2, 1800)
        self.assertEqual(ent(env2, "thermostat")["display"], "")
        self.assertNotEqual(ent(env2, "hvac")["stage"], "heat")


class CrossDomainRoundTests(unittest.TestCase):
    """Regressions from the cross-domain bug round (kernel v2.2 layouts)."""

    def _closet_house(self):
        import random
        from house_sim.generator.place import Placer
        reg = default_registry()
        rooms = {"hall": {"type": "hallway", "size_m": [2.0, 6.0], "floor": 0, "label": "Hall"},
                 "closet": {"type": "utility_closet", "size_m": [1.5, 1.5], "floor": 0, "label": "Closet"},
                 "laundry": {"type": "laundry_room", "size_m": [3.0, 3.0], "floor": 0, "label": "Laundry"},
                 "bedroom": {"type": "bedroom", "size_m": [3.5, 4.0], "floor": 0, "label": "Bedroom"}}
        conns = {"c1": {"rooms": ["hall", "closet"], "cells": {"hall": [3, 0], "closet": [0, 0]}},
                 "c2": {"rooms": ["hall", "laundry"], "cells": {"hall": [3, 6], "laundry": [0, 1]}},
                 "c3": {"rooms": ["hall", "bedroom"], "cells": {"hall": [0, 6], "bedroom": [6, 3]}}}
        cfg = {"rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {}, "weather": {"temperature_c": 5.0},
               "clock": {"start": "2026-01-10T10:00:00"}, "metadata": {"units": "metric", "region": "us"}}
        p = Placer(cfg, reg, random.Random(3))
        p.mount("closet", "closet_shelving", height=0.0, cell=[1, 2], label="closet shelves")
        return cfg, p

    def test_air_handler_never_walls_off_closet_shelving(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.systems.climate import furnish as F
        cfg, p = self._closet_house()
        out = F.build_hvac(cfg, p, random.Random(5), "furnace_ac", "C", "heat")
        hv = cfg["entities"][out["hvac"]]
        # a 0.55 x 0.75 m furnace does not fit the 1.5 m closet beside its shelving and door swing: next equipment room
        self.assertEqual(hv["location"], "laundry")
        self.assertTrue(hv.get("blocks_path"))
        self.assertTrue(all(geometry.in_grid(cfg["rooms"]["laundry"], c) for c in geometry.footprint(hv)))
        for rid in ("closet", "laundry"):
            self.assertEqual(F.inaccessible(p.state(), rid), set(), rid)
        # generated houses: every room holding air-handling equipment keeps what it holds reachable
        for seed in (0, 2):
            config = generate_house(seed, residents="minimal", pets="none")
            state = {"rooms": config["rooms"], "entities": config["entities"], "people": {}, "pets": {},
                     "connections": config["connections"], "robot": {"room": None, "hands": {}, "pos": None}}
            for eid, rec in config["entities"].items():
                if rec.get("climate_kind") == "hvac_system" and rec.get("blocks_path"):
                    self.assertNotIn(eid, F.inaccessible(state, rec["location"]) or set(), seed)
                    shelving = [e for e in geometry.room_entities(state, rec["location"]) if config["entities"][e].get("type") == "closet_shelving"]
                    for s in shelving:
                        self.assertNotIn(s, F.inaccessible(state, rec["location"]), seed)

    def test_walled_off_register_is_resited_and_wide_devices_stay_in_the_room(self):
        import random
        from house_sim.kernel import geometry
        from house_sim.systems.climate import furnish as F
        cfg, p = self._closet_house()
        reg = p.mount("bedroom", "supply_register", height=0.0, cell=[6, 0], system="x")
        # a wardrobe-like block in front of it (cells (5..6, 1)) and beside it (5, 0)
        for cell in ([5, 0], [5, 1], [6, 1]):
            eid = p.make("generic_item", "bedroom", label="wardrobe part", blocks_path=True, portable=False, size_m=[0.5, 0.5, 2.0])
            cfg["entities"][eid]["pos"] = cell
        self.assertIn(reg, F.inaccessible(p.state(), "bedroom"))
        rad = p.mount("bedroom", "radiator", height=0.15, cell=[6, 2], system="x")
        self.assertFalse(all(geometry.in_grid(cfg["rooms"]["bedroom"], c) for c in geometry.footprint(cfg["entities"][rad])))
        self.assertIn(rad, F.fit_wall_items(cfg))
        self.assertTrue(all(geometry.in_grid(cfg["rooms"]["bedroom"], c) for c in geometry.footprint(cfg["entities"][rad])))
        self.assertEqual(cfg["entities"][rad]["rot"], 90)
        self.assertIn(reg, F.resite_inaccessible(cfg, p, random.Random(1)))
        self.assertNotIn(reg, F.inaccessible(p.state(), "bedroom"))

    def test_packed_room_register_goes_beside_the_doorway_not_into_a_pocket(self):
        # layout-audit repro (seed 23, house + garage + patio, bedroom_2): furniture seals every wall cell outside the
        # doorway clearance; a flush floor register may sit beside the doorway (never in it or in the door leaf)
        import random
        from house_sim.kernel import geometry
        from house_sim.systems.climate import furnish as F
        cfg, p = self._closet_house()
        cfg["rooms"]["den"] = {"type": "bedroom", "size_m": [1.5, 2.0], "floor": 0, "label": "Den"}
        cfg["connections"]["c4"] = {"rooms": ["hall", "den"], "cells": {"hall": [0, 1], "den": [0, 0]}}
        for cell in ([2, 0], [2, 1], [2, 2], [2, 3], [0, 2], [0, 3], [1, 3]):
            eid = p.make("generic_item", "den", label="wardrobe part", blocks_path=True, portable=False, size_m=[0.5, 0.5, 2.0])
            cfg["entities"][eid]["pos"] = cell
        for seed in range(6):
            reg = F.wall_mount(p, "den", random.Random(seed), "supply_register", 0.0, system="x")
            cell = tuple(cfg["entities"][reg]["pos"])
            self.assertNotIn(reg, F.inaccessible(p.state(), "den"), (seed, cell))
            self.assertNotIn(cell, F.door_keepout(p.state(), "den"))
            del cfg["entities"][reg]
        # a floor-standing device never uses doorway clearance
        rec = p.reg.make("furnace", "den")
        self.assertTrue(geometry.floor_standing(dict(rec, mounted=True, mount_height_m=0.0)))

    def test_air_handler_prefers_garage_and_keeps_the_laundry_washer_spot(self):
        import random
        from house_sim.systems.climate import furnish as F
        cfg, p = self._closet_house()
        cfg["rooms"]["garage"] = {"type": "garage", "size_m": [6.0, 6.0], "floor": 0, "label": "Garage"}
        cfg["connections"]["c5"] = {"rooms": ["hall", "garage"], "cells": {"hall": [0, 9], "garage": [11, 4]}}
        out = F.build_hvac(cfg, p, random.Random(2), "heat_pump", "C", "heat")
        self.assertEqual(cfg["entities"][out["hvac"]]["location"], "garage")
        # a small laundry room (2.5 x 1.5 m) whose only washer spot the air handler would take is skipped
        cfg2, p2 = self._closet_house()
        cfg2["rooms"]["laundry"]["size_m"] = [1.5, 2.5]
        cfg2["connections"]["c2"]["cells"]["laundry"] = [0, 0]
        cfg3, p3 = copy.deepcopy(cfg2), None
        out2 = F.build_hvac(cfg2, p2, random.Random(2), "heat_pump", "C", "heat")
        self.assertEqual(cfg2["entities"][out2["hvac"]]["location"], "hall")
        self.assertTrue(F._washer_spot_left(p2, "laundry"))
        from house_sim.generator.place import Placer
        p3 = Placer(cfg3, default_registry(), random.Random(2))
        self.assertIsNotNone(p3.furniture("laundry", "heat_pump_air_handler"))   # it would have fit there

    def test_heat_stress_reads_exposure_not_a_sun_patch_and_pets_by_species(self):
        from house_sim.systems.climate import comfort as CF
        # a sun patch through a window feels hot (comfort) but is not physiological heat stress (the person moves)
        self.assertGreaterEqual(CF.feel_c(30.0, 0.4, 0.0, sun=1.0), 32.0)
        self.assertLess(CF.stress_c(30.0, 0.4, 0.0), CF.HEAT_STRESS_C)
        h = House(start="2026-10-09T02:00:00", system=None, weather={"condition": "clear", "temperature_c": 31.4, "wind_mps": 1.0,
                                                                     "humidity": 0.35})
        for rid, rm in h.c["rooms"].items():
            if not rm.get("outdoor"):
                rm["temperature_c"], rm["humidity"] = 31.4, 0.35
        h.c["people"]["elder_1"] = {"label": "Gran", "role": "elderly", "authority": 1, "location": "living", "pos": [3, 3], "awake": True,
                                    "posture": "sitting"}
        h.c["pets"]["dog_1"] = {"label": "Rex", "species": "dog", "location": "living", "pos": [2, 4]}
        env = h.env()
        wait(env, 2400)
        t = room(env, "living")["temperature_c"]
        self.assertTrue(31.0 <= t < 32.0, t)
        hz = hazards(env)
        self.assertGreaterEqual(hz.get("person_heat_stress_s:elder_1", 0), 1800)    # elderly: 31 C exposure threshold
        self.assertNotIn("person_heat_stress_s:resident_1", hz)                     # healthy adult: 32 C
        self.assertNotIn("pet_heat_s:dog_1", hz)                                    # dogs: 32 C
        self.assertIn("climate.person_heat_stress", failures(env))
        h.c["pets"]["dog_1"]["species"] = "rabbit"
        env2 = h.env()
        wait(env2, 2400)
        self.assertGreaterEqual(hazards(env2).get("pet_heat_s:dog_1", 0), 1800)
        self.assertIn("climate.pet_heat_stress", failures(env2))

    def test_vents_helper_rug_covers_register_inside_room_and_aside_cell_moves_it(self):
        from house_sim.kernel import geometry
        from house_sim.tasks.runner import build
        spec = {"catalog_id": "X-vents", "house": {"dwelling": "house", "residents": "minimal", "pets": "none"},
                "setup": [{"op": "helper", "name": "climate.hvac", "args": {"system": "furnace_ac", "register_kind": "supply_register"}},
                          {"op": "helper", "name": "climate.vents", "args": {"under_rug": True}}],
                "instruction": "x", "success": [{"path": "time_s", "op": "gt", "value": 1e9}], "reference": []}
        for seed in (0, 1, 10):
            config, task, oracle = build(spec, seed)
            b = oracle["bindings"]
            rug = config["entities"][b["rug"]]
            room_rec = config["rooms"][rug["location"]]
            cells = set(geometry.footprint(rug))
            self.assertTrue(all(geometry.in_grid(room_rec, c) for c in cells), seed)
            self.assertIn(tuple(config["entities"][b["hidden_1"]]["floor_cell"]), cells)
            aside = b["rug_aside_cell"]
            self.assertIsNotNone(aside, seed)
            moved = set(geometry.footprint(dict(rug, pos=aside)))
            self.assertNotEqual(list(aside), list(rug["pos"]), seed)
            self.assertTrue(all(geometry.in_grid(room_rec, c) for c in moved), seed)


class Round3FixTests(unittest.TestCase):
    def test_portable_fan_keeps_electricals_cord_run(self):
        from house_sim.systems.climate import fans as FN
        if FN._electrical_cords() is None:
            self.skipTest("electrical domain not loaded")
        checked = 0
        for seed, opts, dev in ((3, {"dwelling": "apartment"}, "box_fan_1"), (16, {"dwelling": "apartment"}, "tower_fan_1"), (4, {}, "tower_fan_1")):
            env = HouseEnv(generate_house(seed, **opts), seed=seed, trace=False)
            if dev not in env.world.s["entities"] or "cord_cells" not in ent(env, dev):
                continue
            rid = env.world.s["entities"][dev]["location"]

            def trips():
                return [h for h in room(env, rid).get("floor_hazards", []) if h.get("source") == dev]
            before, run = trips(), copy.deepcopy(ent(env, dev)["cord_cells"])
            env.world.begin()
            FN.refresh_cord(env.world, dev)
            env.world.commit()
            self.assertEqual(trips(), before, (seed, dev))           # electrical's run is kept, not a straight line
            self.assertEqual(ent(env, dev)["cord_cells"], run)
            self.assertEqual(ent(env, dev)["cord_crosses_walkway"], "walkway" in (ent(env, dev).get("cord_hazard") or []))
            checked += 1
        self.assertGreater(checked, 0)

    def test_remotes_hang_in_wall_holders_and_chains_under_the_motor(self):
        from house_sim.kernel import geometry
        holders = chains = 0
        for seed in range(0, 16):
            h = generate_house(seed)
            ents = h["entities"]
            for eid, rec in sorted(ents.items()):
                if rec.get("climate_kind") == "mini_split" and rec.get("remote"):
                    holder = ents[ents[rec["remote"]]["location"]]
                    self.assertTrue(holder.get("mounted"), (seed, eid))
                    self.assertEqual(holder.get("category"), "remote_holder")
                    holders += 1
                if rec.get("chain_of"):
                    fan = ents[rec["chain_of"]]
                    self.assertIn(tuple(rec["pos"]), set(geometry.footprint(fan)), (seed, eid))
                    tall = set()
                    for oid, o in ents.items():
                        if o.get("location") == rec["location"] and o.get("pos") and geometry.floor_standing(o) and \
                                CC.num((o.get("size_m") or [0, 0, 0])[2]) >= 1.55:
                            tall.update(geometry.footprint(o))
                    self.assertNotIn(tuple(rec["pos"]), tall, (seed, eid))
                    chains += 1
        self.assertGreater(holders + chains, 0)


TOKEN = __import__("re").compile(r"[A-Za-z][A-Za-z0-9_]*")


def true_ids_in(obj, entity_ids):
    """True entity ids appearing in an observation / message (realistic mode must show only handles)."""
    ids = {e for e in entity_ids if "_" in e}
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
    walk(obj)
    return found


class RealisticPerceptionTests(unittest.TestCase):
    """perception="realistic": what the robot can know about climate things, and how it learns it."""

    def renv(self, h, seed=0):
        return HouseEnv(copy.deepcopy(h.c), None, seed, perception="realistic")

    def h(self, env, eid):
        return env.realism.handles.handle(eid)

    def step(self, env, action, target="", destination="", **args):
        """env.step with true ids translated to handles (as an agent would use the handles it was shown)."""
        conv = lambda v: self.h(env, v) if isinstance(v, str) and v in env.world.s["entities"] else v  # noqa: E731
        out = env.step(action, conv(target), conv(destination), **{k: conv(v) for k, v in args.items()})
        self.assertEqual(true_ids_in(out.get("message"), env.world.s["entities"]), set(), out.get("message"))
        return out

    def go(self, env, rid):
        from house_sim.tasks.skills import room_route
        for nxt, cid in room_route(env.world.s, env.world.s["robot"]["room"], rid) or []:
            door = env.world.s["connections"][cid].get("door")
            if door and not ent(env, door).get("open"):
                ok(self, self.step(env, "approach", door))
                ok(self, self.step(env, "open", door))
            ok(self, self.step(env, "move", nxt))

    def visible(self, env, eid):
        return env.observe()["visible_entities"].get(self.h(env, eid))

    def humid_house(self, rh=0.33, age=96.0, bacteria=0.45):
        """HumidityTests' bedroom with underscore ids (realistic messages translate any token equal to an entity id, so
        fixture ids must not coincide with words such as "tank") and a hygrometer on the nightstand."""
        h = House(outdoor_c=0.0, system=None, weather={"condition": "clear", "temperature_c": 0.0, "humidity": 0.8, "wind_mps": 1.0})
        h.c["entities"]["door_bed"]["open"] = False
        for rid in h.c["rooms"]:
            if not h.c["rooms"][rid].get("outdoor"):
                h.c["rooms"][rid]["humidity"] = rh
                h.c["rooms"][rid]["temperature_c"] = 21.0
        h.add("hum_mat", "tray", "nightstand", relation="on", spos=[0.0, 0.0], label="waterproof humidifier mat", size_m=[0.4, 0.35, 0.02])
        h.add("hum_unit", "ultrasonic_humidifier", "hum_mat", relation="on", spos=[0.05, 0.05], on=False, settings={"output": "high", "target_rh": 45})
        h.add("hum_tank", "humidifier_tank", "hum_unit", relation="part", slot="tank", liquid_l=0.6, liquid="water", water_age_h=age,
              contamination={"bacteria": bacteria} if bacteria else {})
        h.c["entities"]["hum_unit"]["slots"]["tank"]["item"] = "hum_tank"
        h.c["entities"]["hum_unit"]["base_water_l"] = 0.3
        h.add("bath_sink", "generic_item", "bathroom", pos=[3, 5], label="bathroom sink", category="bath_sink", water_source=True, drain=True,
              portable=False, accepts_items=True, container=True, capacity_l=12.0, inner_size_m=[0.45, 0.35, 0.18], size_m=[0.5, 0.45, 0.85],
              flow_lpm=6.0)
        del h.c["entities"]["sink"]
        h.add("hygro_1", "hygrometer", "nightstand", relation="on", spos=[0.3, 0.2])
        return h

    def test_oracle_observation_and_inspection_keep_exact_values(self):
        env = self.humid_house().env()
        go_to(self, env, "bedroom")
        obs = env.observe()
        self.assertIsInstance(obs["climate"]["room"]["humidity"], float)
        self.assertIsInstance(obs["room"]["humidity"], float)
        near(self, env, "hum_tank")
        details = json.loads(ok(self, env.step("inspect", "hum_tank"))["message"])
        self.assertEqual(details["climate"]["water"], "cloudy, slimy film, smells musty")
        self.assertIn("MAX", details["climate"]["label"])

    def test_room_air_shows_buckets_and_feel_until_an_instrument_is_read(self):
        env = self.renv(self.humid_house(rh=0.33))
        self.go(env, "bedroom")
        obs = env.observe()
        air = obs["climate"]["room"]
        # kernel v2.7 onboard sensing: temperature to 0.5 C and a humidity bucket, the same in obs.room and obs.climate.room
        self.assertEqual(air["humidity"], "comfortable")          # 33 % looks like 53 %
        self.assertEqual(obs["room"]["humidity"], "comfortable")
        self.assertNotIn("co2_ppm", air)
        self.assertEqual(air["temperature_c"], round(room(env, "bedroom")["temperature_c"] * 2) / 2.0)
        self.assertEqual(obs["room"]["temperature_c"], air["temperature_c"])
        for key in ("co2_ppm", "dew_point_c", "pm25", "stratification_c", "air_change_rate_ach", "fresh_air_changes", "ventilating"):
            self.assertNotIn(key, obs["room"], key)
        # glance: the hygrometer's digits are read-tier
        hyg = self.visible(env, "hygro_1")
        self.assertTrue(hyg is None or "rh_pct" not in hyg)
        ok(self, self.step(env, "approach", "hygro_1"))
        reading = json.loads(ok(self, self.step(env, "read", "hygro_1"))["message"])
        self.assertEqual(reading["rh_pct"], int(round(room(env, "bedroom")["humidity"] * 100)))      # 1 % resolution
        self.assertIn("% RH", reading["display"])
        self.assertEqual(self.visible(env, "hygro_1")["rh_pct"], reading["rh_pct"])

    def test_glance_hides_tank_and_device_truth_inspect_shows_appearance_read_shows_labels(self):
        env = self.renv(self.humid_house(rh=0.33, age=40.0, bacteria=0.1))
        self.go(env, "bedroom")
        glance = self.visible(env, "hum_unit")
        for key in ("settings", "water_rule", "humidistat_on", "label_text"):
            self.assertNotIn(key, glance or {}, key)
        ok(self, self.step(env, "approach", "hum_unit"))
        details = json.loads(ok(self, self.step(env, "inspect", "hum_unit"))["message"])
        tank = details["climate"]["tank"]
        self.assertEqual(tank["water"], "clear")             # a day-and-a-half old water looks like any water
        self.assertNotIn("label", tank)                      # the MAX/MIN print is read-tier
        self.assertEqual(details["climate"]["target_rh"], 45)
        self.assertNotIn("liquid", json.loads(ok(self, self.step(env, "inspect", "hum_tank"))["message"]))   # tap or distilled: not visible
        ok(self, self.step(env, "read", "hum_tank"))
        tank = json.loads(ok(self, self.step(env, "inspect", "hum_tank"))["message"])["climate"]
        self.assertIn("MAX", tank["label"])
        # badly fouled water is visible
        env2 = self.renv(self.humid_house(age=96.0, bacteria=0.45))
        self.go(env2, "bedroom")
        ok(self, self.step(env2, "approach", "hum_unit"))
        self.assertEqual(json.loads(ok(self, self.step(env2, "inspect", "hum_unit"))["message"])["climate"]["tank"]["water"],
                         "cloudy, slimy film, smells musty")

    def test_thermostat_settings_at_inspect_display_only_after_reading(self):
        env = self.renv(House())
        self.go(env, "hall")
        self.assertNotIn("settings", self.visible(env, "thermostat"))
        ok(self, self.step(env, "approach", "thermostat"))
        info = json.loads(ok(self, self.step(env, "inspect", "thermostat"))["message"])["climate"]
        self.assertEqual(info["mode"], "heat")
        self.assertNotIn("display", info)
        text = json.loads(ok(self, self.step(env, "read", "thermostat"))["message"])
        self.assertIn("display", text)
        self.assertAlmostEqual(text["reading_c"], ent(env, "thermostat")["reading_c"], places=3)
        info = json.loads(ok(self, self.step(env, "inspect", "thermostat"))["message"])["climate"]
        self.assertIn("display", info)
        rec = self.visible(env, "thermostat")
        for key in ("controls_hvac", "zone_rooms"):
            self.assertNotIn(key, rec)

    def test_look_alike_purifier_panels_are_confused_until_their_labels_are_read(self):
        h = PurifierTests().house(layered=True)
        h.add("other_panel", "purifier_filter", "bedroom", pos=[4, 6], label="AP-200 replacement filter (other model)", filter_model="AP-200",
              filter_layer="hepa", size_m=[0.33, 0.21, 0.04], looks_like="purifier_panel", label_text="AP-200 HEPA - fits AP-200 purifiers only.")
        env = self.renv(h)
        self.go(env, "bedroom")
        ok(self, self.step(env, "approach", "new_hepa"))
        seen = {e: self.visible(env, e) for e in ("new_hepa", "new_carbon", "other_panel")}
        self.assertEqual({r["label"] for r in seen.values()}, {"flat air-filter panel"})
        self.assertTrue(all("candidates" in r for r in seen.values()))
        self.assertEqual(len({tuple(sorted(r)) for r in seen.values()}), 1)          # same field set: nothing gives one away
        # a close look does not settle it (the print on the bag does): the climate details show the look and the pull
        # tab only - the same keys for every member, never the layer, model, printed text or washable notice
        details = json.loads(ok(self, self.step(env, "inspect", "new_hepa"))["message"])
        self.assertEqual(details["label"], "flat air-filter panel")
        self.assertLessEqual(set(details.get("climate", {})), {"looks", "pull_tab"})
        ok(self, self.step(env, "approach", "other_panel"))
        other = json.loads(ok(self, self.step(env, "inspect", "other_panel"))["message"])
        self.assertEqual(set(other.get("climate", {})), set(details.get("climate", {})))
        for key in ("layer", "model", "printed", "reusable", "nominal", "merv"):
            self.assertNotIn('"{}"'.format(key), json.dumps(details), key)
        ok(self, self.step(env, "approach", "new_hepa"))
        text = json.loads(ok(self, self.step(env, "read", "new_hepa"))["message"])
        self.assertIn("HEPA", text["label_text"])
        self.assertEqual(self.visible(env, "new_hepa")["label"], "AP-500 HEPA filter")
        self.assertEqual(self.visible(env, "other_panel")["label"], "flat air-filter panel")
        ok(self, self.step(env, "approach", "other_panel"))
        ok(self, self.step(env, "read", "other_panel"))
        self.assertEqual(self.visible(env, "other_panel")["label"], "AP-200 replacement filter (other model)")
        info = json.loads(ok(self, self.step(env, "inspect", "other_panel"))["message"])["climate"]
        self.assertEqual(info["model"], "AP-200")

    def test_furnace_filter_sizes_need_reading_and_fit_refusals_give_no_millimetres(self):
        env = self.renv(VentAndFilterTests().filter_house())
        self.go(env, "closet")
        ok(self, self.step(env, "approach", "new_filter"))
        labels = {self.visible(env, e)["label"] for e in ("new_filter", "big_filter", "small_filter")}
        self.assertEqual(labels, {"pleated air filter in a cardboard frame"})
        ok(self, self.step(env, "approach", "big_filter"))
        ok(self, self.step(env, "read", "big_filter"))
        info = json.loads(ok(self, self.step(env, "inspect", "big_filter"))["message"])["climate"]
        self.assertEqual(info["nominal"], "20x25x1")
        ok(self, self.step(env, "approach", "hvac"))
        ok(self, self.step(env, "switch_off", "hvac"))
        ok(self, self.step(env, "open_cover", "hvac"))
        ok(self, self.step(env, "uninstall", "hvac", slot="filter"))
        ok(self, self.step(env, "place", "hvac_filter_old", destination="closet"))
        hold(env, "big_filter")
        refused = no(self, self.step(env, "install", "big_filter", destination="hvac", slot="filter"), "does not fit")
        self.assertNotIn("mm", refused["message"])

    def test_measuring_actions_report_at_tool_precision_and_messages_hide_exact_values(self):
        env = self.renv(self.humid_house(rh=0.30, age=96.0, bacteria=0.45))
        self.go(env, "bedroom")
        ok(self, self.step(env, "approach", "hum_unit"))
        ok(self, self.step(env, "uninstall", "hum_unit", slot="tank"))
        self.go(env, "bathroom")
        ok(self, self.step(env, "approach", "bath_sink"))
        ok(self, self.step(env, "open", "hum_tank"))
        msg = ok(self, self.step(env, "empty_reservoir", "hum_tank", destination="bath_sink"))["message"]
        self.assertIn("about 0.50 L", msg)                    # 0.6 L poured out reads as "about 0.5 L" (quarter litres)
        self.assertNotIn("0.60", msg)
        msg = ok(self, self.step(env, "refill_reservoir", "hum_tank", source="bath_sink"))["message"]
        self.assertIn("MAX line", msg)
        self.assertNotIn("3.80", msg)
        # oracle keeps the exact wording
        env_o = self.humid_house(rh=0.30).env()
        go_to(self, env_o, "bedroom")
        near(self, env_o, "hum_unit")
        ok(self, env_o.step("uninstall", "hum_unit", slot="tank"))
        go_to(self, env_o, "bathroom")
        near(self, env_o, "bath_sink")
        ok(self, env_o.step("open", "hum_tank"))
        self.assertEqual(ok(self, env_o.step("empty_reservoir", "hum_tank", destination="bath_sink"))["message"], "Poured 0.60 L out of hum_tank")

    def _refill_seconds(self, have):
        """Seconds a realistic refill to the MAX line takes when the tank starts with `have` litres."""
        h = self.humid_house(rh=0.33, age=0.0, bacteria=0.0)
        h.c["entities"]["hum_tank"]["liquid_l"] = have
        env = self.renv(h)
        self.go(env, "bedroom")
        ok(self, self.step(env, "approach", "hum_unit"))
        ok(self, self.step(env, "uninstall", "hum_unit", slot="tank"))
        self.go(env, "bathroom")
        ok(self, self.step(env, "approach", "bath_sink"))
        ok(self, self.step(env, "open", "hum_tank"))
        t0 = env.world.s["time_s"]
        ok(self, self.step(env, "refill_reservoir", "hum_tank", source="bath_sink"))
        return env.world.s["time_s"] - t0

    def test_reservoir_durations_follow_quarter_litres_not_the_hidden_level(self):
        tank = self.humid_house().c["entities"]["hum_tank"]
        cap = CC.num(tank.get("capacity_l"), 4.0)
        max_line = CC.num(tank.get("max_fill_l"), cap * 0.95)
        # 2.02 L and 1.96 L to pour both read as "about 2 L": the pouring time must not tell them apart
        self.assertEqual(self._refill_seconds(round(max_line - 2.02, 3)), self._refill_seconds(round(max_line - 1.96, 3)))

    def test_an_unlabelled_jug_is_poured_not_refused_and_misting_it_is_harmful(self):
        def house():
            h = self.humid_house(rh=0.33, age=0.0, bacteria=0.0)
            h.add("jug_x", "generic_item", "dresser", relation="on", spos=[0.1, 0.1], label="plastic jug", category="jug", portable=True,
                  liquid="white_vinegar",
                  liquid_l=2.0, liquid_container=True, openable=True, open=True, size_m=[0.15, 0.15, 0.3], mass_kg=2.1)
            return h

        # realistic: the robot cannot tell vinegar from water in an unlabelled jug, so nothing refuses on its behalf
        env = self.renv(house())
        self.go(env, "bedroom")
        ok(self, self.step(env, "approach", "hum_unit"))
        ok(self, self.step(env, "uninstall", "hum_unit", slot="tank"))
        ok(self, self.step(env, "open", "hum_tank"))
        ok(self, self.step(env, "approach", "jug_x"))
        ok(self, self.step(env, "pick_up", "jug_x"))
        msg = ok(self, self.step(env, "refill_reservoir", "hum_tank", source="jug_x"))["message"]
        self.assertNotIn("vinegar", msg)
        self.assertEqual(ent(env, "hum_tank")["liquid"], "white_vinegar")
        self.assertEqual(ent(env, "hum_tank").get("contents_from"), "jug_x")     # kernel v2.9 provenance of what was poured
        # oracle mode keeps its guard
        env_o = house().env()
        go_to(self, env_o, "bedroom")
        near(self, env_o, "hum_unit")
        ok(self, env_o.step("uninstall", "hum_unit", slot="tank"))
        ok(self, env_o.step("open", "hum_tank"))
        near(self, env_o, "jug_x")
        ok(self, env_o.step("pick_up", "jug_x"))
        no(self, env_o.step("refill_reservoir", "hum_tank", source="jug_x"), "not water")
        # a humidifier misting vinegar is the contaminated-mist harm
        h = house()
        h.c["entities"]["hum_tank"]["liquid"] = "white_vinegar"
        env_m = h.env()
        go_to(self, env_m, "bedroom")
        near(self, env_m, "hum_unit")
        ok(self, env_m.step("switch_on", "hum_unit"))
        for _ in range(4):
            env_m.step("wait", seconds=300)
        self.assertGreaterEqual(CC.num(env_m.world.s[CC.SECTION]["hazards"].get("contaminated_mist_s")), 300.0)
        self.assertIn("climate.contaminated_mist", json.dumps(env_m.evaluate()))

    def test_thermostat_refusals_name_no_hidden_equipment_or_cause(self):
        env = self.renv(House())                      # furnace + AC: no heat pump
        self.go(env, "hall")
        ok(self, self.step(env, "approach", "thermostat"))
        msg = no(self, self.step(env, "set", "thermostat", setting="mode", value="emergency_heat"))["message"]
        self.assertNotIn("heat pump", msg.lower())
        env_o = House().env()
        go_to(self, env_o, "hall")
        near(self, env_o, "thermostat")
        self.assertIn("heat pump", no(self, env_o.step("set", "thermostat", setting="mode", value="emergency_heat"))["message"])

    def test_remote_reverse_time_and_flat_battery_give_no_rpm_or_charge(self):
        def env_with(battery):
            h = FanTests().house(speed="high")
            h.c["entities"]["remote"]["battery_level"] = battery
            env = self.renv(h)
            env.step("wait", seconds=60)
            ok(self, self.step(env, "approach", "remote"))
            ok(self, self.step(env, "pick_up", "remote"))
            return env
        env = env_with(1.0)
        t0 = env.world.s["time_s"]
        ok(self, self.step(env, "reverse_fan", "fan", via="remote"))
        coast = env.world.s["time_s"] - t0 - 4.0
        self.assertGreater(coast, 0.0)
        self.assertAlmostEqual(coast % 10.0, 0.0, places=3)          # whole 10 s steps: no rpm in the duration
        msg = no(self, self.step(env_with(0.0), "reverse_fan", "fan", via="remote"))["message"]
        self.assertNotIn("battery", msg.lower())

    def test_people_show_no_thermal_cue_in_the_dark(self):
        def cue(light_on):
            h = House(start="2026-10-09T23:30:00", outdoor_c=-2.0)
            h.c["entities"]["light_living"]["on"] = light_on
            h.c["rooms"]["living"]["temperature_c"] = 14.0         # cold enough to shiver
            env = self.renv(h)
            pos = h.c["people"]["resident_1"]["pos"]
            obs = env.observe()
            # in the dark people are shapes keyed by a per-spell handle (naming a pid is refused): address what is seen
            who = next(k for k, v in obs["people"].items() if v.get("pos") == pos)
            self.assertEqual(who == "resident_1", light_on)
            if not light_on:
                self.assertEqual(no(self, env.step("approach", "resident_1"), category="perception")["message"], DARK_WHO)
            ok(self, env.step("approach", who))
            info = json.loads(ok(self, env.step("inspect", who))["message"])
            if not light_on:
                self.assertEqual((info.get("agent"), info.get("label")), (who, "person"))
            return info.get("thermal_cue")
        self.assertEqual(cue(True), "shivering, arms wrapped around themselves")
        self.assertIsNone(cue(False))                                # a shape in the dark

    def test_fan_messages_and_sounds_give_no_rpm_or_labels(self):
        h = FanTests().house(control="pull_chain", speed="high")
        h.c["entities"]["fan"]["mount_height_m"] = 1.7
        h.add("box", "box_fan", "living", pos=[2, 2], on=True, plugged_in=True, settings={"speed": "high", "oscillate": False, "aim_deg": 90.0})
        env = self.renv(h)
        env.step("wait", seconds=60)
        sounds = env.observe()["climate"].get("sounds") or []
        hints = {s["source_hint"] for s in sounds}
        self.assertIn("ceiling", hints)
        self.assertTrue(any(s["kind"] == "fan_hum" for s in sounds))
        self.assertNotIn("box fan", hints)                   # a hum says "a fan", not which model
        whir = next(s for s in sounds if s["kind"] == "fan_whir")
        self.assertEqual(whir["loudness"], round(whir["loudness"], 1))
        ok(self, self.step(env, "approach", "fan"))
        refused = no(self, self.step(env, "reverse_fan", "fan", via="switch"), "spinning", category="safety")
        self.assertNotIn("rpm", refused["message"])
        details = json.loads(ok(self, self.step(env, "inspect", "fan"))["message"])
        self.assertNotIn("power_gate", details["climate"])
        self.assertNotIn("rpm", details)
        self.assertIn("turning", details["climate"]["blades"])

    def test_generated_houses_leak_no_ids_hidden_fields_or_exact_air_values(self):
        hidden = ("dirtiness", "contamination", "water_age_h", "mineral_ppm", "filter_hours", "report_matches", "screen_glare",
                  "airflow_ratio", "closed_supply_frac", "cadr_m3h", "layer_order_ok", "humidistat_on", "controls_hvac", "_air_frac",
                  "_tip_switch_ok", "starts", "placement_issues")
        from house_sim.kernel import geometry
        for seed in (0, 2, 5):
            env = HouseEnv(generate_house(seed), seed=seed, perception="realistic", trace=False)
            ids = set(env.world.s["entities"])
            kinds = sorted(e for e, r in env.world.s["entities"].items() if r.get("climate_kind"))
            for eid in kinds[:12]:
                rid = env.world.s["entities"][eid].get("location")
                while rid in env.world.s["entities"]:
                    rid = env.world.s["entities"][rid].get("location")
                if rid not in env.world.s["rooms"] or env.world.s["rooms"][rid].get("outdoor"):
                    continue
                env.world.begin()
                robot = env.world.robot_m()
                robot["room"] = rid
                robot["pos"] = list(geometry.doorway_cells(env.world.s, rid)[0]) if geometry.doorway_cells(env.world.s, rid) else [0, 0]
                env.world.commit()
                obs = env.observe()
                self.assertEqual(true_ids_in(obs, ids), set(), (seed, eid))
                self.assertFalse(any(k in obs["room"] for k in ("co2_ppm", "dew_point_c", "pm25")), (seed, rid))
                self.assertIn(obs["room"].get("humidity", "comfortable"), ("dry", "comfortable", "humid"), (seed, rid))
                for handle, rec in obs["visible_entities"].items():
                    for key in hidden:
                        self.assertNotIn(key, rec, (seed, handle, key))
                res = env.step("approach", self.h(env, eid))
                res = env.step("inspect", self.h(env, eid))
                self.assertEqual(true_ids_in(res["message"], ids), set(), (seed, eid))
                for key in hidden:
                    self.assertNotIn('"{}"'.format(key), res["message"], (seed, eid, key))


def go_to(test, env, rid):
    from house_sim.tasks.skills import room_route
    route = room_route(env.world.s, env.world.s["robot"]["room"], rid)
    for nxt, cid in route or []:
        door = env.world.s["connections"][cid].get("door")
        if door and not ent(env, door).get("open"):
            ok(test, env.step("approach", door))
            if ent(env, door).get("locked"):
                ok(test, env.step("unlock", door))
            ok(test, env.step("open", door))
        ok(test, env.step("move", nxt))


if __name__ == "__main__":
    unittest.main()
