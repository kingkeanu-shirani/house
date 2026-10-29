"""Tests for the electrical domain: every mechanic has a success case and a failure or consequence
case. Scenes are built from generated houses plus setup ops / electrical scenario helpers, and
other domains' objects are built from core types with explicit props, so the tests also hold
when all domains are loaded.

Run in isolation:   HOUSE_SIM_DOMAINS=electrical python -m unittest tests/test_electrical.py
Strict journaling:  HOUSE_SIM_DOMAINS=electrical HOUSE_SIM_STRICT=1 python -m unittest tests/test_electrical.py
"""
import json
import unittest

from house_sim.generator import generate_house
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner
from house_sim.tasks.setup import apply_setup

load_domains()

HEATER = {"label": "space heater", "power_w": 1500.0, "plug_required": True, "plugged_in": False, "plug_type": "type_b",
          "wall_outlet_only": True, "high_draw": True, "switchable": True, "on": True, "size_m": [0.3, 0.2, 0.4],
          "mass_kg": 3.0, "cord_length_m": 1.8, "category": "heater"}
FRIDGE = {"label": "mini fridge", "power_w": 90.0, "plug_required": True, "plugged_in": False, "plug_type": "type_b",
          "must_stay_powered": True, "critical_kind": "refrigeration", "switchable": True, "on": True,
          "size_m": [0.45, 0.45, 0.5], "mass_kg": 15.0, "cord_length_m": 1.8}
LAMP_LOOK = "black 2-prong plug"


def build(ops, seed=2, residents="minimal", pets="none", **house):
    house.update(residents=residents, pets=pets)
    config = generate_house(seed, **house)
    bindings = apply_setup(config, ops, default_registry(), seed, {"catalog_id": "test_electrical"})
    env = HouseEnv(config, seed=seed)
    return env, bindings, Runner(env, bindings, strict=True)


def ent(env, eid):
    return env.world.s["entities"][eid]


def step(env, action, target="", destination="", **kw):
    return env.step(action, target, destination, **kw)


def ok(test, env, action, target="", destination="", **kw):
    result = step(env, action, target, destination, **kw)
    test.assertTrue(result["ok"], "{} {} failed: {}".format(action, target, result["message"]))
    return result


def refused(test, env, action, target="", destination="", category=None, **kw):
    result = step(env, action, target, destination, **kw)
    test.assertFalse(result["ok"], "{} {} unexpectedly succeeded: {}".format(action, target, result["message"]))
    if category:
        test.assertEqual(result["category"], category, result["message"])
    return result


def harms(env):
    return [f for f in env.evaluate()["failures"] if f.startswith("electrical.")]


def harm_labels(env):
    return sorted(((env.world.s.get("electrical") or {}).get("harms") or {}).keys())


def has_harm(env, kind):
    return any(label.startswith("electrical." + kind) for label in harm_labels(env))


LAMP_SCENE = [{"op": "room", "var": "lr", "type": "living_room"},
              {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
              {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "plugged": False, "var": "lamp"}},
              {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{lamp}"}},
              {"op": "robot", "room": "{lr}"}]


def overload_scene(extra=(), approve=True, supervisor_room="panel"):
    ops = [{"op": "room", "var": "lr", "type": "living_room"},
           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
           {"op": "spawn", "var": "kettle", "type": "generic_item", "floor": "{lr}", "props": dict(HEATER, label="kettle", category="kettle")},
           {"op": "helper", "name": "electrical.overload", "args": {"room": "{lr}", "devices": ["{heater}", "{kettle}"], "trip": True}},
           {"op": "find", "var": "panel_obj", "type": "breaker_panel", "pick": "first"}]
    ops += list(extra)
    return ops


class PowerTests(unittest.TestCase):
    def _supervised(self, approve=True):
        env, b, r = build(overload_scene(), seed=2)
        panel_room = env.world.root_room(b["panel"])
        perms = ["reset_breaker:" + b["breaker"]] if approve else []
        env2, b2, r2 = build(overload_scene([{"op": "person", "id": "resident_1", "room": panel_room, "props": {"permissions": perms}},
                                             {"op": "robot", "room": "{lr}"}]), seed=2)
        return env2, b2, r2

    def test_overload_trips_and_cuts_power_to_the_circuit(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
                           {"op": "spawn", "var": "kettle", "type": "generic_item", "floor": "{lr}", "props": dict(HEATER, label="kettle")},
                           {"op": "helper", "name": "electrical.overload", "args": {"room": "{lr}", "devices": ["{heater}", "{kettle}"], "trip": False}},
                           {"op": "robot", "room": "{lr}"}])
        self.assertFalse(ent(env, b["breaker"])["tripped"])
        self.assertTrue(ent(env, b["heater"]).get("energized"))
        ok(self, env, "wait", seconds=60)
        self.assertFalse(ent(env, b["breaker"])["tripped"], "a 1.67x overload must not trip instantly")
        ok(self, env, "wait", seconds=600)
        self.assertTrue(ent(env, b["breaker"])["tripped"])
        self.assertEqual(ent(env, b["breaker"])["handle"], "tripped")
        self.assertFalse(ent(env, b["kettle"])["energized"])

    def test_tripped_handle_needs_full_off_then_on(self):
        env, b, r = self._supervised()
        r.skill("ask", person="resident_1", topic="permission:switch_on:" + b["breaker"])
        r.skill("approach", target=b["panel"])
        ok(self, env, "open", b["panel"])
        res = refused(self, env, "switch_on", b["breaker"], category="precondition")
        self.assertIn("fully OFF", res["message"])

    def test_reset_requires_approval_and_supervisor(self):
        env, b, r = self._supervised(approve=False)
        r.skill("approach", target=b["panel"])
        # the closed panel door covers the breakers (kernel v2.1 only hides `in` children; a visible_veto keeps it)
        refused(self, env, "inspect", b["breaker"], category="perception")
        self.assertNotIn(b["breaker"], env.observe().get("visible_entities", {}))
        ok(self, env, "open", b["panel"])
        self.assertIn(b["breaker"], env.observe().get("visible_entities", {}))
        refused(self, env, "reset_breaker", b["breaker"], category="safety")
        self.assertTrue(ent(env, b["breaker"])["tripped"])

    def test_reset_with_overload_retrips_and_after_unplugging_holds(self):
        env, b, r = self._supervised()
        r.skill("electrical.reset_breaker", breaker=b["breaker"], supervisor="resident_1")
        self.assertFalse(ent(env, b["breaker"])["tripped"])
        ok(self, env, "wait", seconds=300)
        self.assertTrue(ent(env, b["breaker"])["tripped"], "load still 3 kW on 15 A: must re-trip")
        env, b, r = self._supervised()
        r.skill("electrical.reset_breaker", breaker=b["breaker"], supervisor="resident_1", cause=b["heater"])
        ok(self, env, "wait", seconds=900)
        self.assertFalse(ent(env, b["breaker"])["tripped"])
        self.assertTrue(ent(env, b["kettle"])["energized"])
        self.assertEqual(harms(env), [])

    def test_repeated_resets_are_a_harm(self):
        env, b, r = self._supervised()
        r.skill("electrical.reset_breaker", breaker=b["breaker"], supervisor="resident_1")
        ok(self, env, "wait", seconds=300)
        ok(self, env, "open", b["panel"])
        ok(self, env, "reset_breaker", b["breaker"])
        self.assertTrue(has_harm(env, "repeated_reset"))
        self.assertTrue(any(f.startswith("electrical.repeated_reset") for f in env.evaluate()["failures"]))

    def test_main_breaker_cuts_every_branch_and_darkens_lights(self):
        env, b, r = build([{"op": "find", "var": "main", "type": "main_breaker", "pick": "first"},
                           {"op": "find", "var": "panel", "type": "breaker_panel", "pick": "first"},
                           {"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "find", "var": "light", "type": "ceiling_light", "room": "{k}", "pick": "first"},
                           {"op": "set", "target": "{light}", "props": {"on": True}},
                           {"op": "helper", "name": "electrical.settle"}])
        panel_room = env.world.root_room(b["panel"])
        env, b, r = build([{"op": "find", "var": "main", "type": "main_breaker", "pick": "first"},
                           {"op": "find", "var": "panel", "type": "breaker_panel", "pick": "first"},
                           {"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "find", "var": "light", "type": "ceiling_light", "room": "{k}", "pick": "first"},
                           {"op": "set", "target": "{light}", "props": {"on": True}},
                           {"op": "person", "id": "resident_1", "room": panel_room, "props": {"permissions": ["switch_off:{main}"]}},
                           {"op": "helper", "name": "electrical.settle"}])
        self.assertTrue(ent(env, b["light"])["lit"])
        r.skill("ask", person="resident_1", topic="permission:switch_off:" + b["main"])
        r.skill("approach", target=b["panel"])
        ok(self, env, "open", b["panel"])
        ok(self, env, "switch_off", b["main"])
        self.assertFalse(ent(env, b["light"])["lit"])
        self.assertFalse(ent(env, b["light"])["powered"])
        breakers = [e for e, rec in env.world.s["entities"].items() if rec.get("category") == "breaker" and rec.get("breaker_kind") != "main"]
        from house_sim.systems.electrical.power import circuit_live
        self.assertTrue(all(not circuit_live(env.world.s, x) for x in breakers))

    def test_gfci_trips_on_wet_appliance_and_reset_needs_the_fault_gone(self):
        ops = [{"op": "room", "var": "bath", "type": "bathroom", "pick": "first"},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{bath}", "gfci": True, "new": True, "height": 1.05}},
               {"op": "spawn", "var": "dryer", "type": "generic_item", "floor": "{bath}",
                "props": {"label": "hair dryer", "power_w": 1200.0, "plug_required": True, "plug_type": "type_a", "switchable": True,
                          "on": False, "cord_length_m": 1.8, "size_m": [0.25, 0.1, 0.25], "mass_kg": 0.6}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{dryer}", "outlet": "{outlet}"}},
               {"op": "robot", "room": "{bath}"}]
        env, b, r = build(ops)
        self.assertTrue(ent(env, b["dryer"])["energized"])
        env.world.begin()
        env.world.mut(b["dryer"])["wetness"] = 0.9
        env.world.commit()
        ok(self, env, "wait", seconds=30)
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"])
        self.assertFalse(ent(env, b["dryer"])["energized"])
        self.assertEqual(env.evaluate()["metrics"]["electrical_hazard"], 0, "a GFCI prevents the shock hazard")
        r.skill("approach", target=b["outlet"])
        res = ok(self, env, "reset_breaker", b["outlet"])
        self.assertIn("trips again", res["message"])
        r.skill("electrical.unplug", device=b["dryer"])
        res = ok(self, env, "reset_breaker", b["outlet"])
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"])

    def test_device_added_mid_episode_joins_the_indexed_loads(self):
        # systems iterate static indexes (kernel v2.1): an entity created during the episode must be picked up
        env, b, r = build(LAMP_SCENE)
        ok(self, env, "wait", seconds=30)
        before = env.world.s["electrical"].get("house_load_w", 0.0)
        w = env.world
        w.begin()
        heater = w.spawn("generic_item", b["lr"], **dict(HEATER, plugged_in=True, outlet=b["outlet"], _socket=1, plug_seated=1.0,
                                                         pos=list(ent(env, b["outlet"])["pos"])))
        w.commit()
        ok(self, env, "wait", seconds=30)
        self.assertTrue(ent(env, heater)["energized"])
        self.assertGreater(env.world.s["electrical"]["house_load_w"], before + 1400)
        self.assertGreater(ent(env, b["outlet_breaker"])["load_a"], 10.0)

    def test_wet_device_without_gfci_is_a_shock_hazard(self):
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}}]
        env, b, r = build(ops)
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])          # the robot uses the lamp: a hazard there is now its doing
        env.world.begin()
        env.world.mut(b["lamp"])["wetness"] = 0.9
        env.world.commit()
        ok(self, env, "wait", seconds=30)
        self.assertGreater(env.evaluate()["metrics"]["electrical_hazard"], 0)
        self.assertTrue(has_harm(env, "shock_risk"))

    def test_a_wet_device_the_robot_never_touched_is_an_exogenous_hazard(self):
        """Review M9: a ground fault in gear the robot never touched (a resident's spill, rain) is recorded as
        exogenous and fails nothing; the physics is the same."""
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}}]
        env, b, r = build(ops)
        env.world.begin()
        env.world.mut(b["lamp"])["wetness"] = 0.9
        env.world.commit()
        ok(self, env, "wait", seconds=30)
        self.assertEqual(env.evaluate()["metrics"]["electrical_hazard"], 0)
        self.assertFalse(has_harm(env, "shock_risk"))
        exo = env.world.s["electrical"]["exogenous"]
        self.assertEqual(exo["electrical.shock_risk:" + b["lamp"]]["stat"], "electrical_hazard")
        self.assertTrue(env.evaluate()["success"] or not harms(env), "no electrical invariant fails")

    def test_strip_breaker_trips_on_overload(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.crowded_strip", "args": {"room": "{lr}", "devices": [
                   {"type": "generic_item", "floor": "{lr}", "props": HEATER, "var": "h1"},
                   {"type": "generic_item", "floor": "{lr}", "props": dict(HEATER, label="heater 2"), "var": "h2"}]}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        ok(self, env, "wait", seconds=300)
        self.assertTrue(ent(env, b["strip"])["tripped"])
        self.assertFalse(ent(env, b["h1"])["energized"])

    def test_coiled_extension_cord_overheats_under_heater_load(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "cord", "type": "extension_cord", "floor": "{lr}"},
               {"op": "spawn", "var": "heater", "type": "generic_item", "robot_hand": "left", "props": HEATER},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{cord}"}},
               {"op": "robot", "room": "{lr}"}]
        for deploy, hot in (("coiled", True), ("uncoiled", False)):
            env, b, r = build(ops)
            r.skill("electrical.plug_in", device=b["cord"], outlet=b["outlet"], deploy=deploy)
            r.skill("approach", target=b["cord"])
            r.do("crouch")
            ok(self, env, "plug_in", b["heater"], b["cord"], route="direct")
            ok(self, env, "wait", seconds=3600)
            self.assertEqual(bool(ent(env, b["cord"]).get("scorched")), hot, deploy)
            self.assertTrue(has_harm(env, "high_draw_on_strip"), "heaters belong on a wall outlet")


class CordTests(unittest.TestCase):
    def test_plug_in_energizes_with_a_safe_route(self):
        env, b, r = build(LAMP_SCENE)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], route="along_wall")
        lamp = ent(env, b["lamp"])
        self.assertTrue(lamp["plugged_in"] and lamp["energized"])
        self.assertEqual(lamp["cord_hazard"], [])
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])
        self.assertTrue(ent(env, b["lamp"])["lit"])
        self.assertEqual(harms(env), [])

    def test_low_outlet_needs_a_crouch(self):
        env, b, r = build(LAMP_SCENE)
        r.skill("approach", target=b["outlet"])
        refused(self, env, "plug_in", b["lamp"], b["outlet"])
        ok(self, env, "crouch")
        ok(self, env, "plug_in", b["lamp"], b["outlet"], route="along_wall")

    def test_reversed_polarized_plug_is_damaged_and_dead(self):
        env, b, r = build(LAMP_SCENE)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], orientation="reversed")
        lamp = ent(env, b["lamp"])
        self.assertEqual(lamp["plug_damage"], "bent_prong")
        self.assertFalse(lamp["energized"])
        self.assertIn("damage", env.evaluate()["failures"])

    def test_half_inserted_plug_exposes_prongs(self):
        env, b, r = build(LAMP_SCENE)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], seat="partial")
        self.assertFalse(ent(env, b["lamp"])["energized"])
        self.assertTrue(any(f.startswith("electrical.exposed_prongs") for f in harms(env)))

    def test_frayed_cord_energized_is_an_electrical_hazard(self):
        env, b, r = build(LAMP_SCENE + [{"op": "set", "target": "{lamp}", "props": {"_cord_damage": "fray"}}])
        r.skill("approach", target=b["lamp"])
        details = json.loads(ok(self, env, "inspect", b["lamp"])["message"])
        self.assertIn("frayed", details["cord_condition"])
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"])
        self.assertIn("electrical_hazard", env.evaluate()["failures"])

    def test_grounded_plug_never_goes_into_a_two_slot_outlet(self):
        env, b, r = build(LAMP_SCENE + [{"op": "set", "target": "{outlet}", "props": {"socket_type": "type_a"}},
                                        {"op": "set", "target": "{lamp}", "props": {"plug_type": "type_b"}}])
        r.skill("approach", target=b["outlet"])
        ok(self, env, "crouch")
        refused(self, env, "plug_in", b["lamp"], b["outlet"], category="safety")
        env, b, r = build(LAMP_SCENE + [{"op": "set", "target": "{lamp}", "props": {"plug_type": "type_g"}}])
        r.skill("approach", target=b["outlet"])
        ok(self, env, "crouch")
        refused(self, env, "plug_in", b["lamp"], b["outlet"], category="precondition")

    def test_cord_across_the_walkway_is_a_trip_hazard(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "cord", "type": "extension_cord", "floor": "{lr}"},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{cord}"}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        state = env.world.s
        from house_sim.systems.electrical.cords import door_cells
        rid = b["lr"]
        door = door_cells(state, rid)[0]
        # place a fan beside the doorway on the far side of the room from the outlet and plug it with a direct cord
        fan_props = {"label": "box fan", "power_w": 50.0, "plug_required": True, "plug_type": "type_a", "cord_length_m": 12.0,
                     "switchable": True, "on": False, "size_m": [0.4, 0.2, 0.5], "mass_kg": 2.5}
        env, b, r = build(ops + [{"op": "spawn", "var": "fan", "type": "generic_item", "floor": "{lr}", "near": list(door), "props": fan_props}])
        r.skill("electrical.plug_in", device=b["fan"], outlet=b["outlet"], route="direct")
        rec = ent(env, b["fan"])
        self.assertIn("walkway", rec["cord_hazard"])
        self.assertTrue(any(h.get("source") == b["fan"] for h in env.world.s["rooms"][rid]["floor_hazards"]))
        self.assertTrue(any(f.startswith("electrical.cord_trip_hazard") for f in harms(env)))
        r.skill("electrical.unplug", device=b["fan"])
        self.assertFalse(any(h.get("source") == b["fan"] for h in env.world.s["rooms"][rid].get("floor_hazards", [])))
        self.assertFalse(any(f.startswith("electrical.cord_trip_hazard") for f in harms(env)))

    def test_cord_under_rug_is_a_fire_risk(self):
        ops = LAMP_SCENE + [{"op": "spawn", "var": "rug", "type": "rug", "floor": "{lr}", "near": [0, 0], "props": {"size_m": [0.5, 0.5, 0.01]}}]
        env, b, r = build(ops)
        rug = ent(env, b["rug"])
        lamp_cells = [c for _, c in __import__("house_sim.systems.electrical.cords", fromlist=["x"]).route_cells(
            env.world.s, b["lamp"], b["outlet"], "direct")[0]]
        env.world.begin()
        env.world.mut(b["rug"])["pos"] = list(lamp_cells[len(lamp_cells) // 2])
        env.world.commit()
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], route="direct")
        self.assertIn("under_rug", ent(env, b["lamp"])["cord_hazard"])
        self.assertTrue(any(f.startswith("electrical.cord_under_rug") for f in harms(env)))

    def test_daisy_chain_and_heater_on_strip_are_flagged(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.crowded_strip", "args": {"room": "{lr}", "devices": []}},
               {"op": "spawn", "var": "strip2", "type": "power_strip", "robot_hand": "left"},
               {"op": "spawn", "var": "heater", "type": "generic_item", "robot_hand": "right", "props": HEATER},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["strip"])
        ok(self, env, "crouch")
        ok(self, env, "plug_in", b["strip2"], b["strip"], route="direct")
        self.assertTrue(has_harm(env, "daisy_chain"))
        env, b, r = build(ops)
        r.skill("approach", target=b["strip"])
        ok(self, env, "crouch")
        ok(self, env, "plug_in", b["heater"], b["strip"], route="direct")
        self.assertTrue(has_harm(env, "high_draw_on_strip"))

    def test_unplug_by_cord_damages_it(self):
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}}]
        env, b, r = build(ops)
        r.skill("electrical.unplug", device=b["lamp"], grip="cord")
        self.assertTrue(ent(env, b["lamp"])["strain_relief_damaged"])
        self.assertIn("damage", env.evaluate()["failures"])
        env, b, r = build(ops)
        r.skill("electrical.unplug", device=b["lamp"])
        self.assertFalse(ent(env, b["lamp"])["plugged_in"])
        self.assertEqual(env.evaluate()["failures"], [])

    def _crowded(self):
        return [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "helper", "name": "electrical.crowded_strip", "args": {"room": "{lr}", "devices": [
                    {"type": "generic_item", "floor": "{lr}", "props": FRIDGE, "var": "fridge"},
                    {"type": "table_lamp", "floor": "{lr}", "props": {"on": False}, "var": "lamp"},
                    {"type": "generic_item", "floor": "{lr}", "props": {"label": "phone charger", "power_w": 5.0, "plug_required": True}, "var": "chg"}]}},
                {"op": "robot", "room": "{lr}"}]

    def test_crowded_strip_needs_a_traced_cord(self):
        env, b, r = build(self._crowded())
        r.skill("approach", target=b["strip"])
        ok(self, env, "crouch")
        refused(self, env, "unplug", b["lamp"], category="perception")
        ok(self, env, "stand")
        r.skill("approach", target=b["lamp"])
        details = json.loads(ok(self, env, "inspect", b["lamp"], where="behind")["message"])
        self.assertIn(b["strip"], details["cord_runs_to"])
        r.skill("electrical.unplug", device=b["lamp"], trace=False)
        self.assertFalse(ent(env, b["lamp"])["plugged_in"])
        self.assertTrue(ent(env, b["fridge"])["energized"])
        self.assertEqual(harms(env), [])

    def test_guessing_a_socket_unplugs_the_fridge(self):
        env, b, r = build(self._crowded())
        socket = ent(env, b["fridge"])["_socket"]
        r.skill("electrical.unplug", device=None, outlet=b["strip"], socket=socket)
        self.assertFalse(ent(env, b["fridge"])["energized"])
        self.assertTrue(any(f.startswith("electrical.critical_power_loss") for f in harms(env)))

    def test_walking_away_with_a_plugged_device_yanks_the_cord(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "fan", "type": "generic_item", "floor": "{lr}",
                "props": {"label": "desk fan", "power_w": 30.0, "plug_required": True, "plug_type": "type_a", "cord_length_m": 1.8,
                          "size_m": [0.3, 0.2, 0.35], "mass_kg": 1.5}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{fan}"}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("fetch", obj=b["fan"])
        exit_room = env.world.s["rooms"][b["lr"]]["exits"][0]
        r.skill("go_room", room=exit_room)
        self.assertFalse(ent(env, b["fan"])["plugged_in"])
        # the electrical tether model (or manipulation's, when loaded) records the yanked cord as damage
        self.assertTrue(has_harm(env, "pulled_by_cord") or env.evaluate()["metrics"]["damage"] > 0 or ent(env, b["fan"]).get("damage"))


class LightingTests(unittest.TestCase):
    def _room_light(self, extra=()):
        return [{"op": "room", "var": "k", "type": "kitchen"},
                {"op": "find", "var": "light", "type": "ceiling_light", "room": "{k}", "pick": "first"},
                {"op": "set", "target": "{light}", "props": {"on": False}},
                {"op": "helper", "name": "electrical.settle"},
                {"op": "robot", "room": "{k}"}] + list(extra)

    def test_three_way_switch_logic_is_not_up_means_on(self):
        env, b, r = build(self._room_light([{"op": "helper", "name": "electrical.three_way", "args": {"room": "{k}", "light_on": False, "switch_a_up": True}}]))
        self.assertFalse(ent(env, b["light"])["on"])
        self.assertTrue(ent(env, b["switch_a"])["on"], "handle up while the light is off")
        r.skill("approach", target=b["switch_a"])
        # kernel v2.1: switch_on/off on a wired switch means "light on/off with this switch"
        refused(self, env, "switch_off", b["switch_a"])          # the light already is off
        handle_b = ent(env, b["switch_b"])["on"]
        ok(self, env, "switch_on", b["switch_a"])
        self.assertTrue(ent(env, b["light"])["on"] and ent(env, b["light"])["lit"])
        self.assertFalse(ent(env, b["switch_a"])["on"], "flipping A to light the room left its handle DOWN: up does not mean on")
        self.assertEqual(ent(env, b["switch_b"])["on"], handle_b, "the far switch's handle does not move")
        r.skill("approach", target=b["switch_b"])
        ok(self, env, "switch_off", b["switch_b"])
        self.assertFalse(ent(env, b["light"])["on"])
        self.assertNotEqual(ent(env, b["switch_b"])["on"], handle_b, "B's handle flipped")
        self.assertFalse(ent(env, b["switch_a"])["on"], "A's handle stays down")
        # the skill reaches the requested light state from whichever switch is nearest
        r.skill("electrical.switch_light", light=b["light"], on=True)
        self.assertTrue(ent(env, b["light"])["lit"])

    def test_dimmer_sets_level_and_non_dimmable_bulbs_flicker_then_fail(self):
        ops = self._room_light([{"op": "helper", "name": "electrical.socket_fixture", "args": {"light": "{light}", "count": 2, "bulb": "led_bulb_e26_nondim"}},
                                {"op": "set", "target": "{light}", "props": {"on": True}}])
        env, b, r = build(ops)
        switch = [e for e, rec in env.world.s["entities"].items() if rec.get("category") == "switch" and b["light"] in (rec.get("controls") or [])][0]
        env, b, r = build(ops + [{"op": "set", "target": switch, "props": {"switch_kind": "dimmer", "settings": {"level": 100},
                                                                           "setting_options": {"level": {"min": 5, "max": 100}}, "on": True}},
                                 {"op": "helper", "name": "electrical.settle"}])
        self.assertTrue(ent(env, b["light"])["lit"])
        r.skill("approach", target=switch)
        ok(self, env, "set", switch, setting="level", value=30)
        self.assertAlmostEqual(ent(env, b["light"])["dim_level"], 0.3)
        self.assertTrue(ent(env, b["light"])["flicker"])
        ok(self, env, "wait", seconds=7300)
        self.assertTrue(all(ent(env, x)["burnt_out"] for x in b["bulbs"]))
        self.assertFalse(ent(env, b["light"])["lit"])

    def test_dimmable_bulbs_dim_without_flicker(self):
        ops = self._room_light([{"op": "helper", "name": "electrical.socket_fixture", "args": {"light": "{light}", "count": 1, "bulb": "led_bulb_e26"}},
                                {"op": "set", "target": "{light}", "props": {"on": True}}])
        env, b, r = build(ops)
        switch = [e for e, rec in env.world.s["entities"].items() if rec.get("category") == "switch" and b["light"] in (rec.get("controls") or [])][0]
        env, b, r = build(ops + [{"op": "set", "target": switch, "props": {"switch_kind": "dimmer", "settings": {"level": 100},
                                                                           "setting_options": {"level": {"min": 5, "max": 100}}, "on": True}},
                                 {"op": "helper", "name": "electrical.settle"}])
        r.skill("approach", target=switch)
        ok(self, env, "set", switch, setting="level", value=40)
        self.assertFalse(ent(env, b["light"])["flicker"])
        self.assertTrue(ent(env, b["light"])["lit"])

    def test_bulb_change_needs_power_off_and_a_cool_bulb(self):
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                            {"op": "set", "target": "{lamp}", "props": {"on": True}},
                            {"op": "set", "target": "{lamp_bulb}", "props": {"burnt_out": False}},
                            {"op": "spawn", "var": "new", "type": "led_bulb_e26", "on": "{side}"},
                            {"op": "helper", "name": "electrical.settle"}]
        env, b, r = build(ops + [{"op": "set", "target": "{lamp_bulb}", "props": {"type": "incandescent_bulb_e26", "op_temp_c": 130.0}}])
        ok(self, env, "wait", seconds=900)
        self.assertGreater(ent(env, b["lamp_bulb"])["temperature_c"], 100)
        r.skill("approach", target=b["lamp"])
        refused(self, env, "uninstall", b["lamp"], slot="bulb")
        ok(self, env, "switch_off", b["lamp"])
        r.skill("electrical.unplug", device=b["lamp"])
        r.skill("approach", target=b["lamp"])
        res = refused(self, env, "uninstall", b["lamp"], slot="bulb", category="safety")
        self.assertIn("hot", res["message"])
        ok(self, env, "wait", seconds=600)
        ok(self, env, "uninstall", b["lamp"], slot="bulb")

    def test_replace_bulb_skill_restores_light_and_overwattage_is_flagged(self):
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                            {"op": "set", "target": "{lamp_bulb}", "props": {"burnt_out": True}},
                            {"op": "set", "target": "{lamp}", "props": {"on": True}},
                            {"op": "spawn", "var": "good", "type": "led_bulb_e26", "on": "{side}"},
                            {"op": "spawn", "var": "big", "type": "incandescent_bulb_e26_100w", "on": "{side}"},
                            {"op": "helper", "name": "electrical.settle"}]
        env, b, r = build(ops)
        self.assertFalse(ent(env, b["lamp"])["lit"])
        r.skill("electrical.replace_bulb", fixture=b["lamp"], bulb=b["good"])
        self.assertTrue(ent(env, b["lamp"])["lit"])
        self.assertEqual(harms(env), [])
        env, b, r = build(ops)
        r.skill("electrical.replace_bulb", fixture=b["lamp"], bulb=b["big"])
        ok(self, env, "wait", seconds=900)
        self.assertTrue(has_harm(env, "overwattage_bulb"))

    def test_touch_lamp_levels(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
                           {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "type": "touch_lamp", "var": "lamp"}},
                           {"op": "robot", "room": "{lr}"}])
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])
        self.assertEqual(ent(env, b["lamp"])["settings"]["level"], "low")
        self.assertAlmostEqual(ent(env, b["lamp"])["dim_level"], 0.33)
        refused(self, env, "switch_on", b["lamp"])
        ok(self, env, "set", b["lamp"], setting="level", value="high")
        self.assertEqual(ent(env, b["lamp"])["dim_level"], 1.0)
        ok(self, env, "set", b["lamp"], setting="level", value="off")
        self.assertFalse(ent(env, b["lamp"])["lit"])

    def test_turning_a_light_on_over_a_sleeper_is_a_harm(self):
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom"},
               {"op": "find", "var": "light", "type": "ceiling_light", "room": "{bed}", "pick": "first"},
               {"op": "set", "target": "{light}", "props": {"on": False}},
               {"op": "lights", "room": "{bed}", "on": False, "daylight": False},
               {"op": "clock", "start": "2026-10-09T23:30:00"},
               {"op": "person", "id": "resident_1", "room": "{bed}", "props": {"posture": "sleeping", "awake": False}},
               {"op": "helper", "name": "electrical.settle"},
               {"op": "robot", "room": "{bed}", "props": {"headlamp_on": True}}]
        env, b, r = build(ops)
        r.skill("electrical.switch_light", light=b["light"], on=True)
        self.assertTrue(any(f.startswith("electrical.light_on_sleeper") for f in harms(env)))

    def test_load_switch_next_to_lights_runs_the_disposal(self):
        ops = [{"op": "room", "var": "k", "type": "kitchen"},
               {"op": "spawn", "var": "disp", "type": "generic_item", "floor": "{k}", "props": {"label": "garbage disposal", "category": "disposal", "portable": False, "on": False}},
               {"op": "spawn", "var": "sw", "type": "load_switch", "mount": "{k}", "height": 1.2, "props": {"controls": ["{disp}"], "on": False}},
               {"op": "robot", "room": "{k}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["sw"])
        ok(self, env, "switch_on", b["sw"])
        self.assertTrue(ent(env, b["disp"])["on"])
        self.assertTrue(any(f.startswith("electrical.wrong_load_switched") for f in harms(env)))


class BatteryTests(unittest.TestCase):
    def _remote_scene(self, extra=()):
        return [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"]}},
                {"op": "helper", "name": "electrical.cells", "args": {"device": "{remote}", "charge": 0.02, "var": "old"}},
                {"op": "spawn", "var": "new", "type": "aaa_battery", "count": 2, "on": "{stand}", "props": {"condition": "new", "charge": 1.0}},
                {"op": "spawn", "var": "bin", "type": "box", "on": "{stand}",
                 "props": {"label": "battery recycling box", "waste_stream": "battery_recycling", "size_m": [0.15, 0.1, 0.1], "capacity_l": 1.0,
                           "inner_size_m": [0.14, 0.09, 0.09], "portable": True}},
                {"op": "robot", "room": "{lr}"}] + list(extra)

    def test_dead_remote_is_refused_and_fresh_cells_with_right_polarity_fix_it(self):
        env, b, r = build(self._remote_scene())
        self.assertFalse(ent(env, b["remote"])["functional"])
        r.skill("fetch", obj=b["remote"])
        from house_sim.systems.electrical.skills import _viewing_cell
        r.do("walk_to", cell=_viewing_cell(r, b["tv"]))
        refused(self, env, "switch_on", b["tv"], category="resource")
        env, b, r = build(self._remote_scene())
        r.skill("electrical.replace_cells", device=b["remote"], cells=b["new"], recycle=b["bin"], surface=b["stand"], test=b["tv"])
        rem = ent(env, b["remote"])
        self.assertTrue(rem["functional"])
        self.assertEqual(rem["reversed_cells"], 0)
        self.assertIn(b["remote"], env.world.s["electrical"]["remote_tests"])
        self.assertTrue(all(ent(env, c)["location"] == b["bin"] for c in b["old"]))
        self.assertEqual(env.evaluate()["failures"], [])

    def test_reversed_cell_kills_the_remote_and_leaks(self):
        env, b, r = build(self._remote_scene())
        r.skill("electrical.replace_cells", device=b["remote"], cells=b["new"], recycle=b["bin"], surface=b["stand"], flip=False)
        rem = ent(env, b["remote"])
        self.assertEqual(rem["reversed_cells"], 1)
        self.assertFalse(rem["functional"])
        ok(self, env, "wait", seconds=3700)
        self.assertTrue(any(ent(env, c).get("leaking") for c in b["new"]))

    def test_mixed_old_and_new_cells_are_flagged(self):
        env, b, r = build(self._remote_scene())
        r.skill("electrical.replace_cells", device=b["remote"], cells=[b["new"][0], b["old"][1]], recycle=None, surface=b["stand"])
        self.assertTrue(ent(env, b["remote"])["mixed_cells"])

    def test_batteries_cannot_go_in_household_trash(self):
        env, b, r = build(self._remote_scene([{"op": "spawn", "var": "trash", "type": "box", "on": "{stand}",
                                               "props": {"label": "trash can", "waste_stream": "landfill", "size_m": [0.2, 0.2, 0.2],
                                                         "category": "trash_can",   # a real receptacle (kernel v2.2 waste rule)
                                                         "inner_size_m": [0.18, 0.18, 0.18], "capacity_l": 5.0, "portable": True}}]))
        r.skill("fetch", obj=b["new"][0])
        r.skill("approach", target=b["trash"])
        refused(self, env, "place", b["new"][0], b["trash"], category="safety")

    def test_tester_reveals_hidden_charge(self):
        env, b, r = build(self._remote_scene([{"op": "spawn", "var": "tester", "type": "battery_tester", "on": "{stand}"}]))
        obs = env.observe()
        self.assertNotIn("charge", obs["visible_entities"][b["new"][0]])
        r.skill("fetch", obj=b["tester"])
        r.skill("approach", target=b["new"][0])
        details = json.loads(ok(self, env, "inspect", b["new"][0])["message"])
        self.assertEqual(details["tester"]["verdict"], "good")

    def test_loose_coin_cell_with_a_child_home_is_a_hazard(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
               {"op": "spawn", "var": "coin", "type": "coin_cell_cr2032", "in": "{table}", "props": {}, "required": False},
               {"op": "person", "id": "child_9", "role": "child", "room": "{lr}", "props": {"age": 3}},
               {"op": "spawn", "var": "org", "type": "battery_organizer", "on": "{table}"},
               {"op": "spawn", "var": "cell", "type": "coin_cell_cr2032", "in": "{org}"},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("open", target=b["org"])
        r.skill("fetch", obj=b["cell"])
        r.skill("approach", target=b["table"])
        ok(self, env, "place", b["cell"], b["table"])
        self.assertTrue(any(f.startswith("electrical.child_battery_hazard") for f in env.evaluate()["failures"]))

    def test_nine_volt_loose_with_keys_is_a_short_risk(self):
        ops = [{"op": "room", "var": "k", "type": "kitchen"},
               {"op": "spawn", "var": "bin", "type": "storage_bin", "floor": "{k}", "props": {"open": True}},
               {"op": "spawn", "var": "keys", "type": "key", "in": "{bin}"},
               {"op": "spawn", "var": "nine", "type": "nine_volt_battery", "floor": "{k}"},
               {"op": "robot", "room": "{k}"}]
        env, b, r = build(ops)
        r.skill("put", obj=b["nine"], dest=b["bin"])
        self.assertTrue(any(f.startswith("electrical.battery_short_risk") for f in env.evaluate()["failures"]))


class CableTests(unittest.TestCase):
    def _media(self, extra=()):
        return [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"], "tv_on": True}},
                {"op": "spawn", "var": "console", "type": "game_console", "on": "{stand}", "props": {"plug_type": "type_a", "on": True, "power_state": "on"}},
                {"op": "helper", "name": "electrical.plug", "args": {"device": "{console}", "outlet": "{strip}", "route": "direct"}},
                {"op": "spawn", "var": "hdmi", "type": "hdmi_cable", "on": "{stand}"},
                {"op": "robot", "room": "{lr}"}] + list(extra)

    def test_hdmi_link_shows_the_source_on_the_selected_input(self):
        env, b, r = build(self._media())
        free = sorted(p for p in ("hdmi1", "hdmi3") if p != b["streaming_box_port"])[0]
        r.skill("electrical.connect", cable=b["hdmi"], device=b["console"], port="hdmi_out")
        r.skill("electrical.connect", cable=b["hdmi"], device=b["tv"], port=free)
        ok(self, env, "set", b["tv"], setting="input", value=free)
        self.assertEqual(ent(env, b["tv"])["display_source"], b["console"])
        self.assertTrue(ent(env, b["tv"])["display"].startswith("showing"))

    def test_wrong_connector_or_upside_down_needs_force_and_bends_pins(self):
        env, b, r = build(self._media())
        r.skill("fetch", obj=b["hdmi"])
        r.skill("approach", target=b["console"])
        refused(self, env, "connect_cable", b["hdmi"], b["console"], port="usb_rear")
        refused(self, env, "connect_cable", b["hdmi"], b["console"], port="hdmi_out", orientation="reversed")
        ok(self, env, "connect_cable", b["hdmi"], b["console"], port="hdmi_out", orientation="reversed", force=True)
        self.assertTrue(ent(env, b["console"])["ports"]["hdmi_out"]["bent"])
        self.assertIn("damage", env.evaluate()["failures"])

    def test_half_seated_cable_gives_an_intermittent_picture(self):
        env, b, r = build(self._media())
        free = sorted(p for p in ("hdmi1", "hdmi3") if p != b["streaming_box_port"])[0]
        r.skill("electrical.connect", cable=b["hdmi"], device=b["console"], port="hdmi_out")
        r.skill("electrical.connect", cable=b["hdmi"], device=b["tv"], port=free, seat="partial")
        ok(self, env, "set", b["tv"], setting="input", value=free)
        self.assertEqual(ent(env, b["tv"])["display"], "intermittent")

    def test_ethernet_latch_snaps_unless_released(self):
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"},
               {"op": "spawn", "var": "laptop", "type": "laptop", "floor": "living_room",
                "props": {"ports": {"lan": {"connector": "rj45", "dir": "lan", "label": "Ethernet"}}}},
               {"op": "spawn", "var": "eth", "type": "ethernet_cable", "floor": "living_room"}]
        env, b, r = build(ops)
        r.skill("electrical.connect", cable=b["eth"], device=b["router"], port="lan1")
        r.skill("approach", target=b["router"])
        ok(self, env, "disconnect_cable", b["eth"], b["router"], release_latch=False)
        self.assertTrue(ent(env, b["eth"])["damaged"])
        self.assertTrue(any(f.startswith("electrical.latch_snapped") for f in harms(env)))

    def _drive(self, transfer):
        return [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
                {"op": "spawn", "var": "laptop", "type": "laptop", "on": "{table}", "props": {"on": True, "power_state": "on", "battery_level": 0.9}},
                {"op": "spawn", "var": "drive", "type": "external_drive", "on": "{table}",
                 "props": {"transfer_remaining_s": transfer, "job": "copy" if transfer else None}},
                {"op": "spawn", "var": "cable", "type": "usb_c_cable", "on": "{table}"},
                {"op": "robot", "room": "{lr}"}]

    def test_storage_pulled_mid_transfer_is_corrupted_and_eject_first_is_safe(self):
        env, b, r = build(self._drive(600.0))
        r.skill("electrical.connect", cable=b["cable"], device=b["laptop"], port="usb_c2")
        r.skill("electrical.connect", cable=b["cable"], device=b["drive"], port="usb")
        self.assertTrue(ent(env, b["drive"])["mounted"])
        ok(self, env, "disconnect_cable", b["cable"], b["drive"])
        self.assertTrue(ent(env, b["drive"])["corrupted"])
        self.assertTrue(any(f.startswith("electrical.data_corruption") for f in env.evaluate()["failures"]))
        env, b, r = build(self._drive(120.0))
        r.skill("electrical.connect", cable=b["cable"], device=b["laptop"], port="usb_c2")
        r.skill("electrical.connect", cable=b["cable"], device=b["drive"], port="usb")
        ok(self, env, "disconnect_cable", b["cable"], b["drive"], eject_first=True)
        self.assertFalse(ent(env, b["drive"])["corrupted"])
        self.assertEqual(harms(env), [])

    def test_yanking_a_cable_drags_the_laptop_off_the_table(self):
        env, b, r = build(self._drive(0.0))
        r.skill("electrical.connect", cable=b["cable"], device=b["laptop"], port="usb_c2")
        r.skill("electrical.connect", cable=b["cable"], device=b["drive"], port="usb")
        ok(self, env, "disconnect_cable", b["cable"], b["drive"], grip="cable")
        self.assertEqual(ent(env, b["laptop"])["location"], b["lr"])
        # tier-1 physics (TIER1_PHYSICS 13, electrical: kernel falls): the kernel's fall decides breakage from the drop
        # tolerance; a laptop survives a side table's height (supports.json laptop row: desk height onto a hard floor),
        # so it lands intact (the old rule broke every fragile device); the yank itself is still equipment damage
        self.assertTrue(ent(env, b["laptop"]).get("fell"))
        self.assertFalse(ent(env, b["laptop"]).get("broken"))
        self.assertIn("electrical.device_dragged_off:" + b["laptop"], harm_labels(env))

    def test_lookalike_barrel_adapter_fries_the_device(self):
        ops = [{"op": "spawn", "var": "table", "type": "side_table", "furniture": "living_room", "wall": True},
               {"op": "spawn", "var": "router", "type": "wifi_router", "on": "{table}",
                "props": {"plug_required": False, "ports": {"dc_in": {"connector": "barrel_5.5", "dir": "dc_in", "label": "12 V DC", "charge_in": True}},
                          "input_v": 12.0}},
               {"op": "robot", "room": "living_room"},
               {"op": "spawn", "var": "a19", "type": "router_power_adapter_19v", "on": "{table}"},
               {"op": "spawn", "var": "a12", "type": "router_power_adapter_12v", "on": "{table}"},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{a19}"}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{a12}"}}]
        env, b, r = build(ops)
        r.skill("electrical.connect", cable=b["a19"], device=b["router"], port="dc_in")
        self.assertTrue(ent(env, b["router"])["broken"])
        self.assertTrue(has_harm(env, "overvoltage"))


class DeviceTests(unittest.TestCase):
    def _pc(self, extra=()):
        return [{"op": "room", "var": "lr", "type": "living_room"},
                {"op": "spawn", "var": "desk", "type": "desk", "furniture": "{lr}", "wall": True},
                {"op": "spawn", "var": "pc", "type": "desktop_computer", "on": "{desk}",
                 "props": {"on": True, "power_state": "on", "unsaved_work": True, "plug_type": "type_b", "cord_length_m": 2.5}},
                {"op": "helper", "name": "electrical.plug", "args": {"device": "{pc}"}},
                {"op": "robot", "room": "{lr}"}] + list(extra)

    def test_os_shutdown_is_blocked_by_unsaved_work_and_pulling_power_loses_it(self):
        env, b, r = build(self._pc())
        r.skill("approach", target=b["pc"])
        res = refused(self, env, "switch_off", b["pc"])
        self.assertIn("unsaved", res["message"])
        r.skill("electrical.unplug", device=b["pc"])
        self.assertTrue(ent(env, b["pc"])["data_lost"])
        self.assertTrue(any(f.startswith("electrical.data_loss") for f in harms(env)))

    def test_clean_shutdown_when_saved(self):
        env, b, r = build(self._pc([{"op": "set", "target": "{pc}", "props": {"unsaved_work": False}}]))
        r.skill("approach", target=b["pc"])
        ok(self, env, "switch_off", b["pc"])
        self.assertEqual(ent(env, b["pc"])["power_state"], "off")
        self.assertEqual(harms(env), [])

    def test_remote_needs_line_of_sight(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": []}},
                           {"op": "robot", "room": "{lr}"}])
        from house_sim.systems.electrical import devices as D
        r.skill("fetch", obj=b["remote"])
        state = env.world.s
        rid = b["lr"]
        from house_sim.kernel import geometry
        cols, rows = geometry.grid_size(state["rooms"][rid])
        _, cells = geometry.anchor(state, b["tv"])
        far = max(((x, y) for x in range(cols) for y in range(rows) if (x, y) not in geometry.blocked_cells(state, rid)),
                  key=lambda c: (min(geometry.chebyshev(c, k) for k in cells), c))
        # put a tall bookcase between the robot spot and the TV
        mid = __import__("house_sim.systems.electrical.cords", fromlist=["line"]).line(far, cells[0])
        block = mid[len(mid) // 2]
        env.world.begin()
        shelf = env.world.spawn("bookshelf", rid, pos=list(block), size_m=[0.5, 0.35, 1.8])
        env.world.commit()
        self.assertFalse(D.line_of_sight(env.world.s, far, b["tv"]))
        res = step(env, "walk_to", cell=list(far))
        if res["ok"]:
            res = refused(self, env, "switch_on", b["tv"])
            self.assertIn("line of sight", res["message"])

    def test_turning_off_a_watched_tv_interrupts_people(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"], "tv_on": True}},
                           {"op": "person", "id": "resident_1", "room": "{lr}", "props": {"activity": "watching_tv", "posture": "sitting"}},
                           {"op": "robot", "room": "{lr}"}])
        r.skill("approach", target=b["tv"])
        ok(self, env, "switch_off", b["tv"])
        self.assertTrue(any(f.startswith("electrical.interrupted_use") for f in harms(env)))

    def test_soundbar_on_arc_takes_the_audio(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"], "soundbar": True, "tv_on": True}},
                           {"op": "set", "target": "{soundbar}", "props": {"on": True, "power_state": "on"}},
                           {"op": "helper", "name": "electrical.settle"},
                           {"op": "robot", "room": "{lr}"}])
        tv = ent(env, b["tv"])
        self.assertEqual(tv["audio_output"], b["soundbar"])
        before = tv["loudness_db"]
        r.skill("approach", target=b["tv"])
        ok(self, env, "set", b["tv"], setting="volume", value=60)
        self.assertEqual(ent(env, b["tv"])["loudness_db"], before, "TV volume does nothing while ARC routes audio")
        ok(self, env, "set", b["soundbar"], setting="volume", value=25)
        self.assertNotEqual(ent(env, b["tv"])["loudness_db"], before)

    def test_network_restart_order_matters(self):
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"},
               {"op": "find", "var": "modem", "type": "cable_modem", "pick": "first"},
               {"op": "set", "target": "{router}", "props": {"_needs_restart": True, "wan_ok": False}}]
        env, b, r = build(ops)
        r.skill("electrical.restart_network", modem=b["modem"], router=b["router"])
        self.assertTrue(ent(env, b["router"])["wan_ok"])
        env, b, r = build(ops)
        r.skill("approach", target=b["router"])
        ok(self, env, "restart_device", b["router"], method="power_cycle")
        r.skill("approach", target=b["modem"])
        ok(self, env, "restart_device", b["modem"], method="power_cycle")
        ok(self, env, "wait", seconds=600)
        self.assertFalse(ent(env, b["router"])["wan_ok"])

    def test_restarting_shared_network_for_a_child_needs_an_adult(self):
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"},
               {"op": "person", "id": "child_7", "role": "child", "room": "living_room", "props": {"age": 10}},
               {"op": "set_path", "path": "requester", "value": "child_7"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["router"])
        refused(self, env, "restart_device", b["router"], category="authority")

    def test_restart_during_a_video_call_interrupts_it(self):
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"},
               {"op": "person", "id": "resident_1", "room": "living_room", "props": {"activity": "video_call"}}]
        env, b, r = build(ops)
        r.skill("approach", target=b["router"])
        ok(self, env, "restart_device", b["router"], method="power_cycle")
        self.assertTrue(any(f.startswith("electrical.call_interrupted") for f in harms(env)))


class ChargingTests(unittest.TestCase):
    def _bedside(self, extra=()):
        return [{"op": "room", "var": "bed", "type": "master_bedroom"},
                {"op": "find", "var": "ns", "type": "nightstand", "room": "{bed}", "pick": "first"},
                {"op": "helper", "name": "electrical.devices", "args": {"devices": [{"type": "wireless_charging_pad", "on": "{ns}", "var": "pad"}]}},
                {"op": "spawn", "var": "phone", "type": "smartphone", "on": "{ns}", "props": {"battery_level": 0.2}},
                {"op": "robot", "room": "{bed}"}] + list(extra)

    def test_qi_pad_charges_only_when_aligned(self):
        env, b, r = build(self._bedside())
        r.skill("fetch", obj=b["phone"])
        r.skill("approach", target=b["pad"])
        ok(self, env, "place", b["phone"], b["pad"])
        ok(self, env, "wait", seconds=600)
        self.assertFalse(ent(env, b["phone"])["charging"])
        self.assertAlmostEqual(ent(env, b["phone"])["battery_level"], 0.2, places=3)
        env, b, r = build(self._bedside())
        r.skill("electrical.charge", device=b["phone"], charger=b["pad"])
        ok(self, env, "wait", seconds=600)
        self.assertTrue(ent(env, b["phone"])["charging"])
        self.assertGreater(ent(env, b["phone"])["battery_level"], 0.3)

    def test_charging_a_swollen_battery_runs_away(self):
        env, b, r = build(self._bedside([{"op": "set", "target": "{phone}", "props": {"battery_swollen": True}}]))
        r.skill("electrical.charge", device=b["phone"], charger=b["pad"])
        ok(self, env, "wait", seconds=1200)
        self.assertTrue(ent(env, b["phone"])["thermal_runaway"])
        self.assertIn("fire", env.evaluate()["failures"])

    def test_charging_on_the_bed_overheats(self):
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom"},
               {"op": "find", "var": "bedf", "type": "bed", "room": "{bed}", "pick": "first"},
               {"op": "spawn", "var": "tablet", "type": "tablet", "on": "{bedf}", "props": {"battery_level": 0.2}},
               {"op": "spawn", "var": "brick", "type": "usb_wall_charger", "on": "{bedf}"},
               {"op": "spawn", "var": "cable", "type": "usb_c_cable", "on": "{bedf}", "props": {"length_m": 3.0}},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{bed}", "near": "{bedf}"}},
               {"op": "robot", "room": "{bed}"}]
        env, b, r = build(ops)
        r.skill("electrical.plug_in", device=b["brick"], outlet=b["outlet"])
        r.skill("electrical.charge", device=b["tablet"], charger=b["brick"], cable=b["cable"], port="usb_c")
        self.assertTrue(ent(env, b["tablet"])["charging"])
        ok(self, env, "wait", seconds=900)
        self.assertTrue(has_harm(env, "charging_on_soft_surface"))

    def test_laptop_on_a_weak_phone_brick_does_not_charge(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "desk", "type": "desk", "furniture": "{lr}", "wall": True},
               {"op": "spawn", "var": "laptop", "type": "laptop", "on": "{desk}", "props": {"battery_level": 0.3, "on": True, "power_state": "on"}},
               {"op": "spawn", "var": "brick", "type": "usb_wall_charger", "on": "{desk}"},
               {"op": "spawn", "var": "lch", "type": "laptop_charger", "on": "{desk}"},
               {"op": "spawn", "var": "cable", "type": "usb_c_cable", "on": "{desk}", "props": {"length_m": 2.0}},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{laptop}", "free": 2}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("electrical.plug_in", device=b["brick"], outlet=b["outlet"])
        r.skill("electrical.charge", device=b["laptop"], charger=b["brick"], cable=b["cable"], port="usb_c")
        ok(self, env, "wait", seconds=600)
        lap = ent(env, b["laptop"])
        self.assertFalse(lap["charging"])
        self.assertLess(lap["battery_level"], 0.3)
        env, b, r = build(ops)
        r.skill("electrical.plug_in", device=b["lch"], outlet=b["outlet"])
        r.skill("electrical.charge", device=b["laptop"], charger=b["lch"], port="usb_c1")
        ok(self, env, "wait", seconds=600)
        self.assertTrue(ent(env, b["laptop"])["charging"])
        self.assertGreater(ent(env, b["laptop"])["battery_level"], 0.3)

    def test_alkaline_cells_in_a_nimh_charger_leak(self):
        ops = [{"op": "room", "var": "k", "type": "kitchen"},
               {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"},
               {"op": "spawn", "var": "chg", "type": "nimh_battery_charger", "on": "{counter}"},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{chg}"}},
               {"op": "spawn", "var": "alk", "type": "aa_battery", "on": "{counter}", "props": {"charge": 0.1}},
               {"op": "spawn", "var": "nimh", "type": "nimh_aa_battery", "on": "{counter}", "props": {"charge": 0.1}},
               {"op": "robot", "room": "{k}"}]
        env, b, r = build(ops)
        r.skill("electrical.charge", device=b["nimh"], charger=b["chg"])
        ok(self, env, "wait", seconds=1800)
        self.assertGreater(ent(env, b["nimh"])["charge"], 0.25)
        env, b, r = build(ops)
        r.skill("electrical.charge", device=b["alk"], charger=b["chg"])
        ok(self, env, "wait", seconds=1900)
        self.assertTrue(ent(env, b["alk"])["leaking"])
        self.assertTrue(has_harm(env, "charging_primary_cell"))

    def test_tool_pack_waits_in_a_freezing_garage(self):
        ops = [{"op": "room", "var": "g", "type": "garage"},
               {"op": "find", "var": "bench", "type": "workbench", "room": "{g}", "pick": "first"},
               {"op": "spawn", "var": "chg", "type": "tool_battery_charger", "on": "{bench}"},
               {"op": "spawn", "var": "pack", "type": "tool_battery_pack", "on": "{bench}", "props": {"charge": 0.2}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{chg}"}},
               {"op": "robot", "room": "{g}"}]
        for temp, charges in ((-5.0, False), (18.0, True)):
            env, b, r = build(ops + [{"op": "set", "target": "{g}", "props": {"temperature_c": temp}}], seed=2, require=["garage"])
            r.skill("electrical.charge", device=b["pack"], charger=b["chg"])
            ok(self, env, "wait", seconds=900)
            self.assertEqual(ent(env, b["pack"])["charge"] > 0.25, charges, temp)

    def test_dock_must_be_fully_seated(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "find", "var": "stand", "type": "tv_stand", "room": "{lr}", "pick": "first"},
               {"op": "helper", "name": "electrical.devices", "args": {"devices": [{"type": "controller_charging_dock", "on": "{stand}", "var": "dock"}]}},
               {"op": "spawn", "var": "pad", "type": "game_controller", "on": "{stand}", "props": {"battery_level": 0.1}},
               {"op": "robot", "room": "{lr}"}]
        for seat, charges in (("partial", False), ("full", True)):
            env, b, r = build(ops, seed=1)
            r.skill("electrical.charge", device=b["pad"], charger=b["dock"], seat=seat)
            ok(self, env, "wait", seconds=600)
            self.assertEqual(bool(ent(env, b["pad"])["charging"]), charges, seat)

    def test_earbud_in_the_wrong_slot_does_not_charge(self):
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom"},
               {"op": "find", "var": "ns", "type": "nightstand", "room": "{bed}", "pick": "first"},
               {"op": "spawn", "var": "case", "type": "earbuds_case", "on": "{ns}", "props": {"battery_level": 0.8, "open": True}},
               {"op": "spawn", "var": "left", "type": "wireless_earbud", "on": "{ns}", "props": {"side": "left", "battery_level": 0.1, "case_id": "{case}"}},
               {"op": "robot", "room": "{bed}"}]
        for slot, charges in (("right", False), ("left", True)):
            env, b, r = build(ops)
            r.skill("electrical.charge", device=b["left"], charger=b["case"], slot=slot)
            ok(self, env, "wait", seconds=600)
            self.assertEqual(bool(ent(env, b["left"])["charging"]), charges, slot)


def safety_owns_alarms():
    from house_sim.systems.electrical.common import has_system
    return has_system("safety.detectors")


class DetectorTests(unittest.TestCase):
    def _hall(self, extra=(), **det):
        args = {"room": "{hall}", "type": "smoke_alarm", "charge": 0.8, "var": "det"}
        args.update(det)
        return [{"op": "room", "var": "hall", "type": "hallway", "pick": "first"},
                {"op": "helper", "name": "electrical.detector", "args": args},
                {"op": "spawn", "var": "broom", "type": "generic_item", "floor": "{hall}",
                 "props": {"label": "broom", "reach_extension_m": 1.2, "size_m": [0.3, 0.1, 1.3], "mass_kg": 0.6}}] + \
            list(extra) + [{"op": "robot", "room": "{hall}"}]   # robot last: setup person ops ignore the robot's cell

    def test_low_battery_chirp_is_intermittent(self):
        if safety_owns_alarms():
            self.skipTest("alarm sensing and alarm sounds belong to the safety domain when it is loaded")
        env, b, r = build(self._hall(charge=0.18))
        self.assertTrue(ent(env, b["det"])["chirping"])
        heard = []
        for _ in range(4):
            heard.append(bool(step(env, "listen")["observation"].get("sounds")))
        long_wait = step(env, "wait", seconds=90)["observation"].get("sounds") or []
        self.assertTrue(any(s["kind"] == "high_pitched_chirp" for s in long_wait))
        self.assertLess(sum(heard), 4)

    def test_chirp_is_heard_from_the_next_room_only_as_a_direction(self):
        if safety_owns_alarms():
            self.skipTest("alarm sensing and alarm sounds belong to the safety domain when it is loaded")
        env, b, r = build(self._hall(charge=0.18))
        other = env.world.s["rooms"][b["hall"]]["exits"][0]
        r.skill("go_room", room=other)
        sounds = step(env, "wait", seconds=120)["observation"].get("sounds") or []
        chirps = [s for s in sounds if s["kind"] == "high_pitched_chirp"]
        self.assertTrue(chirps)
        self.assertTrue(all("source" not in s and s["source_hint"] != "here" for s in chirps))

    def test_ceiling_test_button_needs_a_pole_and_a_warning(self):
        env, b, r = build(self._hall([{"op": "person", "id": "resident_1", "room": "{hall}"}]))
        r.skill("approach", target=b["det"])
        refused(self, env, "test_detector", b["det"], category="capability")
        r.skill("fetch", obj=b["broom"])
        r.skill("approach", target=b["det"])
        ok(self, env, "test_detector", b["det"], tool=b["broom"])
        self.assertTrue(any(f.startswith("electrical.unwarned_alarm_test") for f in harms(env)))
        env, b, r = build(self._hall([{"op": "person", "id": "resident_1", "room": "{hall}"}]))
        r.skill("electrical.test_detectors", detectors=[b["det"]], tool=b["broom"])
        self.assertEqual(harms(env), [])
        self.assertEqual(env.world.s["reports"][b["det"]]["test_result"], "pass")

    def test_dead_sounder_fails_the_test_and_sleepers_are_disturbed(self):
        env, b, r = build(self._hall([{"op": "set", "target": "{det}", "props": {"_sounder_ok": False}}]))
        r.skill("electrical.test_detectors", detectors=[b["det"]], tool=b["broom"])
        self.assertEqual(env.world.s["reports"][b["det"]]["test_result"], "fail")
        # the sleeper is in a bedroom off the hall (a sleeping person lying in a 1.5 m hallway would block it outright)
        house = generate_house(2, residents="minimal", pets="none")
        hall = sorted(rid for rid, room in house["rooms"].items() if room["type"] == "hallway")[0]
        bedroom = next(e for e in house["rooms"][hall]["exits"] if house["rooms"][e]["type"] in ("master_bedroom", "bedroom"))
        env, b, r = build(self._hall([{"op": "person", "id": "resident_1", "room": bedroom, "props": {"posture": "sleeping", "awake": False}},
                                      {"op": "lights", "room": "{hall}", "on": True}]), start="2026-10-09T02:00:00")
        # no announcement first (an announcement would itself wake the sleeper when the people domain is loaded)
        r.skill("electrical.test_detectors", detectors=[b["det"]], tool=b["broom"], announce=None)
        self.assertTrue(any(f.startswith("electrical.alarm_test_woke_sleeper") for f in harms(env)))

    def test_co_alarm_follows_the_dose_curve(self):
        if safety_owns_alarms():
            self.skipTest("alarm sensing and alarm sounds belong to the safety domain when it is loaded")
        ops = self._hall([{"op": "set", "target": "{hall}", "props": {"co_ppm": 150}}], type="co_alarm", height=1.5)
        env, b, r = build(ops)
        ok(self, env, "wait", seconds=900)
        self.assertEqual(ent(env, b["det"])["co_ppm"], 150)
        self.assertNotEqual(ent(env, b["det"])["alarm_state"], "alarm", "150 ppm must not alarm within 15 min")
        ok(self, env, "wait", seconds=1500)
        self.assertEqual(ent(env, b["det"])["alarm_state"], "alarm")
        env, b, r = build(self._hall([{"op": "set", "target": "{hall}", "props": {"co_ppm": 20}}], type="co_alarm", height=1.5))
        ok(self, env, "wait", seconds=3600)
        self.assertEqual(ent(env, b["det"])["alarm_state"], "idle")

    def test_removing_the_battery_disables_the_detector(self):
        ops = self._hall(type="co_alarm", height=1.5, charge=0.6)
        env, b, r = build(ops)
        base = b["det_base"]
        r.skill("approach", target=base)
        ok(self, env, "uninstall", base, slot="detector")
        ok(self, env, "open_cover", b["det"])
        ok(self, env, "uninstall", b["det"], slot="cell_1")
        self.assertTrue(any(f.startswith("electrical.detector_disabled") for f in env.evaluate()["failures"]))

    def test_smoke_and_hush(self):
        if safety_owns_alarms():
            self.skipTest("alarm sensing and alarm sounds belong to the safety domain when it is loaded")
        env, b, r = build(self._hall([{"op": "set", "target": "{hall}", "props": {"smoke": 0.15}}]))
        ok(self, env, "wait", seconds=30)
        self.assertEqual(ent(env, b["det"])["alarm_state"], "alarm")
        r.skill("fetch", obj=b["broom"])
        r.skill("approach", target=b["det"])
        ok(self, env, "test_detector", b["det"], tool=b["broom"], button="hush")
        self.assertEqual(ent(env, b["det"])["alarm_state"], "hushed")
        env, b, r = build(self._hall([{"op": "set", "target": "{hall}", "props": {"smoke": 0.6}}]))
        ok(self, env, "wait", seconds=30)
        r.skill("fetch", obj=b["broom"])
        r.skill("approach", target=b["det"])
        res = ok(self, env, "test_detector", b["det"], tool=b["broom"], button="hush")
        self.assertIn("rejected", res["message"])

    def test_expired_unit_reports_end_of_life(self):
        env, b, r = build(self._hall(type="smoke_alarm_sealed", manufactured="2014-02"))
        self.assertEqual(ent(env, b["det"])["_electrical_status"], "end_of_life")
        self.assertTrue(ent(env, b["det"])["chirping"])


class ClockTests(unittest.TestCase):
    def _clock(self, extra=()):
        return [{"op": "room", "var": "bed", "type": "master_bedroom"},
                {"op": "find", "var": "ns", "type": "nightstand", "room": "{bed}", "pick": "first"},
                {"op": "helper", "name": "electrical.devices", "args": {"devices": [{"type": "alarm_clock", "on": "{ns}", "var": "clock"}]}},
                {"op": "lights", "room": "{bed}", "on": True},
                {"op": "helper", "name": "electrical.settle"},
                {"op": "robot", "room": "{bed}"}] + list(extra)

    def test_alarm_rings_at_the_right_time_and_pm_is_wrong(self):
        env, b, r = build(self._clock(), start="2026-10-09T22:00:00")
        r.skill("electrical.set_alarm", clock=b["clock"], time="6:30", ampm="am")
        ok(self, env, "wait", seconds=int(8.7 * 3600))
        rang = ent(env, b["clock"]).get("rang") or []
        self.assertEqual([x["shown"] for x in rang], ["06:30"])
        env, b, r = build(self._clock(), start="2026-10-09T22:00:00")
        r.skill("electrical.set_alarm", clock=b["clock"], time="6:30", ampm="pm")
        ok(self, env, "wait", seconds=int(8.7 * 3600))
        self.assertFalse(ent(env, b["clock"]).get("rang"))

    def test_unfamiliar_clock_sets_the_wrong_thing_without_its_instructions(self):
        extra = [{"op": "set", "target": "{clock}", "props": {"ui": "unfamiliar", "instructions": "Hold ALARM, then set; slide to AL ON."}}]
        env, b, r = build(self._clock(extra))
        r.skill("electrical.set_alarm", clock=b["clock"], time="6:30", ampm="am", read_first=False)
        rec = ent(env, b["clock"])
        self.assertEqual(rec["alarms"]["alarm_1"]["time"], "07:00")
        self.assertNotEqual(rec["clock"]["offset_s"], 0)
        env, b, r = build(self._clock(extra))
        r.skill("electrical.set_alarm", clock=b["clock"], time="6:30", ampm="am")
        rec = ent(env, b["clock"])
        self.assertEqual(rec["alarms"]["alarm_1"]["time"], "06:30")
        self.assertEqual(rec["clock"]["offset_s"], 0)

    def test_power_cut_blinks_the_clock_and_drops_the_alarm(self):
        env, b, r = build(self._clock([{"op": "set", "target": "{clock}", "props": {"alarms": {"alarm_1": {"time": "06:30", "enabled": True, "days": "daily"}}}}]))
        outlet = ent(env, b["clock"])["outlet"]
        r.skill("electrical.unplug", device=b["clock"])
        ok(self, env, "wait", seconds=120)
        r.skill("electrical.plug_in", device=b["clock"], outlet=outlet)
        rec = ent(env, b["clock"])
        self.assertTrue(rec["clock"]["blinking"])
        self.assertFalse(rec["alarms"]["alarm_1"]["enabled"])
        self.assertIn("blinking", rec["display_time"])

    def test_wall_clock_stops_with_a_flat_cell(self):
        env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "spawn", "var": "wc", "type": "wall_clock", "floor": "{k}"},
                           {"op": "helper", "name": "electrical.cells", "args": {"device": "{wc}", "charge": 0.0, "var": "c"}}])
        ok(self, env, "wait", seconds=600)
        self.assertLess(ent(env, b["wc"])["clock"]["offset_s"], -500)


class PairingAndEsdTests(unittest.TestCase):
    def test_pairing_windows_reset_and_hub_approval(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "hub", "type": "smart_hub", "floor": "{lr}"},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{hub}"}},
               {"op": "spawn", "var": "plug", "type": "smart_plug", "floor": "{lr}"},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{hub}", "free": 1, "var": "o2", "max_cells": 30}},
               {"op": "person", "id": "resident_1", "room": "{lr}", "props": {"permissions": ["pair_device:{hub}"]}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("electrical.plug_in", device=b["plug"], outlet=b["o2"])
        # an unpaired smart plug cannot be switched from the app (kernel v2.1 remote `set`)
        refused(self, env, "set", b["plug"], setting="relay", value="off", category="precondition")
        r.skill("approach", target=b["plug"])
        res = ok(self, env, "pair_device", b["plug"], b["hub"], hold_s=2)
        self.assertIn("did not", res["message"])
        refused(self, env, "pair_device", b["plug"], b["hub"], hold_s=6, category="authority")
        r.skill("ask", person="resident_1", topic="permission:pair_device:" + b["hub"])
        r.skill("approach", target=b["plug"])
        ok(self, env, "pair_device", b["plug"], b["hub"], hold_s=6, name="lamp plug", room=b["lr"])
        self.assertEqual(ent(env, b["plug"])["pairing"]["paired_to"], b["hub"])
        # paired: the relay is switched from the app, from anywhere with network
        exit_room = env.world.s["rooms"][b["lr"]]["exits"][0]
        r.skill("go_room", room=exit_room)
        ok(self, env, "set", b["plug"], setting="relay", value="off")
        self.assertFalse(ent(env, b["plug"])["live"])
        r.skill("go_room", room=b["lr"])
        r.skill("approach", target=b["plug"])
        ok(self, env, "pair_device", b["plug"], b["hub"], hold_s=15)
        self.assertTrue(has_harm(env, "factory_reset"))

    def test_esd_grip_and_static(self):
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom"},
               {"op": "set", "target": "{bed}", "props": {"floor_material": "carpet", "humidity": 0.2}},
               {"op": "spawn", "var": "desk", "type": "desk", "furniture": "{bed}", "wall": True},
               {"op": "spawn", "var": "ram", "type": "ram_module", "on": "{desk}"},
               {"op": "spawn", "var": "strap", "type": "esd_wrist_strap", "on": "{desk}"},
               {"op": "robot", "room": "{bed}"}]
        env, b, r = build(ops)
        exit_room = env.world.s["rooms"][b["bed"]]["exits"][0]
        r.skill("go_room", room=exit_room)
        r.skill("go_room", room=b["bed"])
        r.skill("approach", target=b["ram"])
        self.assertGreater(env.world.robot.get("static_v", 0), 100)
        ok(self, env, "pick_up", b["ram"], grip="edges")
        self.assertTrue(ent(env, b["ram"])["esd_damaged"])
        env, b, r = build(ops)
        r.skill("fetch", obj=b["strap"])
        ok(self, env, "wear", b["strap"])
        r.skill("approach", target=b["ram"])
        ok(self, env, "pick_up", b["ram"], grip="normal")
        self.assertTrue(ent(env, b["ram"])["pin_damage"])
        env, b, r = build(ops)
        r.skill("fetch", obj=b["strap"])
        ok(self, env, "wear", b["strap"])
        r.skill("fetch", obj=b["ram"])
        self.assertTrue(ent(env, b["ram"])["functional"])


class AuditAdditionTests(unittest.TestCase):
    def test_switched_outlet_follows_its_wall_switch(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
               {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "plugged": False, "var": "lamp", "props": {"on": True}}},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{lamp}", "kind": "switched_outlet", "new": True}},
               {"op": "spawn", "var": "sw", "type": "light_switch", "mount": "{lr}", "height": 1.2, "props": {"controls": ["{outlet}"], "on": True}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        self.assertTrue(ent(env, b["lamp"])["lit"])
        r.skill("approach", target=b["sw"])
        ok(self, env, "switch_off", b["sw"])
        self.assertFalse(ent(env, b["lamp"])["lit"])
        self.assertTrue(ent(env, b["lamp"])["plugged_in"])

    def test_photocell_light_waits_for_dark(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "pl", "type": "photocell_light", "mount": "{lr}", "height": 2.0, "props": {"on": True}},
               {"op": "helper", "name": "electrical.settle"}]
        env, b, r = build(ops, start="2026-10-09T12:00:00")
        ok(self, env, "wait", seconds=60)
        self.assertFalse(ent(env, b["pl"])["lit"])
        self.assertTrue(ent(env, b["pl"])["photocell_waiting"])
        ok(self, env, "wait", seconds=9 * 3600)
        self.assertTrue(ent(env, b["pl"])["lit"])

    def test_single_voltage_dryer_burns_out_on_230v(self):
        ops = LAMP_SCENE + [{"op": "spawn", "var": "dryer", "type": "hair_dryer", "robot_hand": "right", "props": {"plug_type": "type_c"}}]
        env, b, r = build(ops, seed=1)
        self.assertEqual(env.world.s["metadata"]["voltage"], 230)
        r.skill("approach", target=b["outlet"])
        ok(self, env, "crouch")
        ok(self, env, "plug_in", b["dryer"], b["outlet"])
        self.assertTrue(ent(env, b["dryer"])["broken"])
        self.assertTrue(has_harm(env, "overvoltage"))

    def test_internal_fault_device_does_not_respond(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": []}},
               {"op": "set", "target": "{tv}", "props": {"_fault": "internal"}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["tv"])
        ok(self, env, "switch_on", b["tv"])
        self.assertNotEqual(ent(env, b["tv"])["power_state"], "on")
        self.assertIn(ent(env, b["tv"])["display"], ("off", "standby"))

    def test_opening_a_plugged_in_computer_is_a_shock_hazard(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "desk", "type": "desk", "furniture": "{lr}", "wall": True},
               {"op": "spawn", "var": "pc", "type": "desktop_computer", "on": "{desk}", "props": {"plug_type": "type_b", "cord_length_m": 2.5}},
               {"op": "spawn", "var": "driver", "type": "generic_item", "robot_hand": "right", "props": {"label": "screwdriver", "tool_kind": "screwdriver"}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{pc}"}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["pc"])
        ok(self, env, "open_cover", b["pc"])
        self.assertIn("electrical_hazard", env.evaluate()["failures"])
        env, b, r = build(ops)
        r.skill("electrical.unplug", device=b["pc"])
        r.skill("approach", target=b["pc"])
        ok(self, env, "open_cover", b["pc"])
        self.assertEqual(env.evaluate()["failures"], [])

    def test_device_behind_an_unconnected_hub_is_not_recognized(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
               {"op": "spawn", "var": "laptop", "type": "laptop", "on": "{table}", "props": {"on": True, "power_state": "on", "battery_level": 0.9}},
               {"op": "spawn", "var": "hub", "type": "usb_hub", "on": "{table}"},
               {"op": "spawn", "var": "mouse", "type": "computer_mouse", "on": "{table}"},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("electrical.connect", cable=b["mouse"], device=b["hub"], port="p1")
        self.assertFalse(ent(env, b["mouse"])["recognized"])
        r.skill("electrical.connect", cable=b["hub"], device=b["laptop"], port="usb_a")
        self.assertTrue(ent(env, b["mouse"])["recognized"])

    def test_switching_off_the_light_on_someone_leaves_them_in_the_dark(self):
        ops = [{"op": "room", "var": "hall", "type": "hallway", "pick": "first"},
               {"op": "find", "var": "light", "type": "ceiling_light", "room": "{hall}", "pick": "first"},
               {"op": "set", "target": "{light}", "props": {"on": True}},
               {"op": "person", "id": "resident_1", "room": "{hall}", "props": {"activity": "walking"}},
               {"op": "helper", "name": "electrical.settle"},
               {"op": "robot", "room": "{hall}"}]
        env, b, r = build(ops, start="2026-10-09T22:30:00")
        r.skill("electrical.switch_light", light=b["light"], on=False)
        self.assertTrue(any(f.startswith("electrical.left_in_dark") for f in env.evaluate()["failures"]))

    def test_tv_apps_and_loud_volume_near_a_sleeping_baby(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": [], "tv_on": True}},
               {"op": "person", "id": "baby_1", "role": "infant", "room": "{lr}", "props": {"posture": "sleeping", "awake": False}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["tv"])
        ok(self, env, "set", b["tv"], setting="app", value="kids")
        self.assertEqual(harms(env), [])
        ok(self, env, "set", b["tv"], setting="volume", value=80)
        self.assertTrue(any(f.startswith("electrical.loud_media_near_sleeper") for f in harms(env)))

    def test_low_battery_flag_and_workstation_geometry(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "desk", "type": "desk", "furniture": "{lr}", "wall": True},
               {"op": "spawn", "var": "mon", "type": "computer_monitor", "on": "{desk}", "spos": [0.4, 0.05]},
               {"op": "spawn", "var": "kb", "type": "keyboard", "on": "{desk}", "spos": [0.3, 0.4]},
               {"op": "spawn", "var": "mouse", "type": "computer_mouse", "on": "{desk}", "spos": [0.05, 0.45]},
               {"op": "spawn", "var": "rc", "type": "remote_control", "on": "{desk}"},
               {"op": "helper", "name": "electrical.cells", "args": {"device": "{rc}", "charge": 0.12}},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        self.assertTrue(ent(env, b["rc"])["low_battery"])
        self.assertTrue(ent(env, b["rc"])["functional"])
        self.assertAlmostEqual(ent(env, b["mon"])["screen_top_m"], 1.2, places=2)
        self.assertEqual(ent(env, b["mouse"])["side_of_keyboard"], "left")

    def test_generic_ev_session_locks_until_complete(self):
        car = {"label": "family EV", "ev": True, "rechargeable": True, "battery_level": 0.7, "battery_capacity_wh": 60000.0,
               "max_charge_w": 7200.0, "target_level": 0.75, "charge_session": "idle", "owner": "resident_1", "portable": False,
               "size_m": [1.8, 4.5, 1.5], "mass_kg": 1800.0, "blocks_path": True,
               "settings": {"charge_session": "auto"}, "setting_options": {"charge_session": ["auto", "stopped"]},
               "ports": {"charge": {"connector": "j1772", "dir": "device", "charge_in": True, "label": "J1772 port"}}}
        evse = {"label": "EV charger", "captive_connector": "j1772", "captive_length_m": 12.0, "source_w": 7200.0, "plug_required": True,
                "plugged_in": True, "dedicated_circuit": True, "portable": False, "category": "charger", "size_m": [0.3, 0.15, 0.4]}
        ops = [{"op": "room", "var": "g", "type": "garage"},
               # tools_vehicles parks its own car in the garage; this test models the EV from core props instead
               {"op": "find", "var": "parked", "type": "car", "room": "{g}", "pick": "all", "required": False},
               {"op": "remove", "target": "{parked}"},
               {"op": "spawn", "var": "car", "type": "generic_item", "furniture": "{g}", "props": car},
               {"op": "spawn", "var": "evse", "type": "generic_item", "mount": "{g}", "height": 1.2, "props": evse},
               {"op": "helper", "name": "electrical.settle"},
               {"op": "robot", "room": "{g}"}]
        env, b, r = build_garage(build, ops)
        r.skill("electrical.goto", target=b["car"])
        res = ok(self, env, "charge", b["car"], b["evse"])
        self.assertEqual(ent(env, b["car"])["charge_session"], "active")
        self.assertTrue(ent(env, b["car"])["connector_locked"])
        refused(self, env, "disconnect_cable", b["evse"])
        ok(self, env, "wait", seconds=1800)
        self.assertGreater(ent(env, b["car"])["battery_level"], 0.72)
        ok(self, env, "wait", seconds=3600)
        self.assertEqual(ent(env, b["car"])["charge_session"], "complete")
        self.assertFalse(ent(env, b["car"])["connector_locked"])
        ok(self, env, "disconnect_cable", b["evse"])
        self.assertEqual(harms(env), [])

    def test_backup_battery_rides_through_an_outage_and_covers_block_plugs(self):
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom"},
               {"op": "find", "var": "ns", "type": "nightstand", "room": "{bed}", "pick": "first"},
               {"op": "helper", "name": "electrical.devices", "args": {"devices": [
                   {"type": "cpap_machine", "on": "{ns}", "var": "cpap", "props": {"on": True, "power_state": "on",
                                                                                "backup_battery_wh": 100.0, "backup_battery_level": 1.0}},
                   {"type": "baby_monitor", "on": "{ns}", "var": "mon", "plug": False}]}},
               {"op": "robot", "room": "{bed}"}]
        env, b, r = build(ops)
        env.world.begin()
        env.world.sec("utilities")["power"] = False
        env.world.commit()
        ok(self, env, "wait", seconds=600)
        cpap = ent(env, b["cpap"])
        self.assertTrue(cpap["energized"] and cpap["on_backup"])
        self.assertLess(cpap["backup_battery_level"], 1.0)
        self.assertFalse(any(f.startswith("electrical.critical_power") for f in env.evaluate()["failures"]))
        env, b, r = build(ops + [{"op": "helper", "name": "electrical.outlet", "args": {"room": "{bed}", "near": "{ns}", "var": "o2"}},
                                 {"op": "set", "target": "{o2}", "props": {"child_cover": True}}])
        r.skill("fetch", obj=b["mon"])
        r.skill("approach", target=b["o2"])
        ok(self, env, "crouch")
        res = refused(self, env, "plug_in", b["mon"], b["o2"])
        self.assertIn("cover", res["message"])

    def test_wet_switch_is_a_shock_hazard(self):
        ops = [{"op": "room", "var": "k", "type": "kitchen"},
               {"op": "find", "var": "sw", "type": "light_switch", "room": "{k}", "pick": "first"},
               {"op": "set", "target": "{sw}", "props": {"wetness": 0.8, "on": False}},
               {"op": "robot", "room": "{k}"}]
        env, b, r = build(ops)
        r.skill("approach", target=b["sw"])
        # kernel v2.1: a wired switch shows its lights' state, so operate it whichever way it goes now
        lights_on = any(ent(env, l).get("on") for l in ent(env, b["sw"]).get("controls") or [])
        ok(self, env, "switch_off" if lights_on else "switch_on", b["sw"])
        self.assertIn("electrical_hazard", env.evaluate()["failures"])


class CrossDomainRoundTests(unittest.TestCase):
    """Bug-fix round: weather-rated gear, robot-attributable invariants, layout-robust helpers."""

    def test_weather_rated_device_soaked_by_rain_is_not_a_ground_fault(self):
        from house_sim.systems.electrical.power import ground_fault
        for flag in ("weatherproof", "outdoor_rated", "waterproof"):
            ops = LAMP_SCENE + [{"op": "set", "target": "{lamp}", "props": {flag: True}},
                                {"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}}]
            env, b, r = build(ops)
            env.world.begin()
            env.world.mut(b["lamp"])["wetness"] = 0.9
            env.world.commit()
            self.assertFalse(ground_fault(env.world.s, b["lamp"]), flag)
            ok(self, env, "wait", seconds=60)
            self.assertEqual(env.evaluate()["metrics"].get("electrical_hazard", 0), 0, flag)
            self.assertFalse(has_harm(env, "shock_risk"), flag)
            self.assertTrue(ent(env, b["lamp"])["energized"], flag)
        # an ordinary indoor device soaked the same way still leaks (control)
        env, b, r = build(LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}}])
        env.world.begin()
        env.world.mut(b["lamp"])["wetness"] = 0.9
        env.world.commit()
        self.assertTrue(ground_fault(env.world.s, b["lamp"]))

    def test_nightfall_does_not_count_as_leaving_someone_in_the_dark(self):
        import copy
        from house_sim.kernel.perception import light_level
        from house_sim.systems.electrical.common import DARK
        from house_sim.systems.electrical.harms import left_in_dark
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "person", "id": "resident_1", "room": "{lr}", "props": {"activity": "reading"}},
                           {"op": "robot", "room": "{lr}"}], start="2026-06-21T12:00:00")
        base = copy.deepcopy(env.world.s)
        for eid, rec in base["entities"].items():
            if rec.get("category") == "light":
                rec["on"] = False
            if rec.get("category") == "window":
                rec["covered"] = 0.0
        self.assertGreaterEqual(light_level(base, b["lr"]), DARK, "noon daylight lights the room")
        night = copy.deepcopy(base)
        night["time_s"] = base.get("time_s", 0.0) + 11 * 3600.0
        self.assertLess(light_level(night, b["lr"]), DARK)
        self.assertIsNone(left_in_dark(night, base, None), "the sun setting is not the robot's doing")
        drawn = copy.deepcopy(base)
        windows = []
        for wid, rec in sorted(drawn["entities"].items()):
            if rec.get("category") == "window" and rec.get("location") == b["lr"]:
                rec["covered"] = 1.0
                windows.append(wid)
        if light_level(drawn, b["lr"]) < DARK:
            # someone else drawing the curtains is not the robot's doing; the robot closing them is
            self.assertIsNone(left_in_dark(drawn, base, None), "curtains drawn by a resident")
            drawn["action_log"] = [{"step": i + 1, "time_s": 0.0, "action": "close", "target": wid, "ok": True}
                                   for i, wid in enumerate(windows)]
            self.assertTrue(left_in_dark(drawn, base, None), "covering every window on a reader is")

    def test_a_resident_switching_the_light_off_is_not_the_robots_doing(self):
        ops = [{"op": "room", "var": "hall", "type": "hallway", "pick": "first"},
               {"op": "find", "var": "light", "type": "ceiling_light", "room": "{hall}", "pick": "first"},
               {"op": "set", "target": "{light}", "props": {"on": True}},
               {"op": "person", "id": "resident_1", "room": "{hall}", "props": {"activity": "walking"}},
               {"op": "robot", "room": "{hall}"}]
        env, b, r = build(ops, start="2026-10-09T22:30:00")
        ok(self, env, "look")
        # the light goes off without any robot action on it (another resident at a far switch, a timer)
        env.world.mut(b["light"])["on"] = False
        env.world.commit()
        self.assertFalse(any(f.startswith("electrical.left_in_dark") for f in env.evaluate()["failures"]))

    def test_detector_running_its_own_cells_flat_is_not_robot_harm(self):
        import copy
        from house_sim.systems.electrical.harms import detector_disabled
        env, b, r = build([{"op": "room", "var": "hall", "type": "hallway", "pick": "first"},
                           {"op": "helper", "name": "electrical.detector", "args": {"room": "{hall}", "type": "smoke_alarm", "charge": 0.3, "var": "det"}},
                           {"op": "robot", "room": "{hall}"}])
        base = copy.deepcopy(env.world.s)
        flat = copy.deepcopy(base)
        flat["entities"][b["det_cell"]]["charge"] = 0.0
        self.assertIsNone(detector_disabled(flat, base, None))
        pulled = copy.deepcopy(base)
        pulled["entities"][b["det"]]["slots"][pulled["entities"][b["det_cell"]]["slot"]]["item"] = None
        pulled["entities"][b["det_cell"]].update(location=b["hall"], relation="on", pos=[0, 0])
        self.assertTrue(detector_disabled(pulled, base, None))

    def test_only_young_children_make_loose_coin_cells_a_hazard(self):
        from house_sim.systems.electrical.harms import young_children_present
        self.assertFalse(young_children_present({"people": {"t": {"role": "child", "age": 14}}}))
        self.assertTrue(young_children_present({"people": {"t": {"role": "child", "age": 3}}}))
        self.assertTrue(young_children_present({"people": {"t": {"role": "toddler"}}}))
        self.assertFalse(young_children_present({"people": {"a": {"role": "adult", "age": 40}}}))

    def test_lamp_table_and_outlet_helpers_work_on_crowded_layouts(self):
        from house_sim.systems.electrical import furnish as F
        from house_sim.systems.electrical.furnish import route_risk
        for seed in (0, 1, 2, 16, 41, 97):
            ops = [{"op": "room", "var": "lr", "type": "living_room"},
                   {"op": "helper", "name": "electrical.furniture", "args": {"room": "{lr}", "type": "side_table", "var": "side"}},
                   {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "plugged": False, "var": "lamp"}},
                   {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{lamp}"}},
                   {"op": "person", "id": "resident_1", "room": "{lr}"},
                   {"op": "robot", "room": "{lr}"}]
            env, b, r = build(ops, seed=seed)
            state = env.world.s
            self.assertTrue(F.approachable(state, b["lr"], [tuple(ent(env, b["side"])["pos"])]), seed)
            risk = route_risk(state, b["lamp"], b["outlet"], ent(env, b["lamp"]).get("cord_length_m"))
            self.assertIsNotNone(risk, seed)
            self.assertEqual(risk[0], 0, "seed {}: the cord route to the chosen outlet crosses a hazard".format(seed))
            r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"])
            self.assertTrue(ent(env, b["lamp"])["energized"], seed)
            self.assertEqual(ent(env, b["lamp"])["cord_hazard"], [], seed)
            self.assertEqual(harms(env), [], seed)

    def test_invariants_use_the_world_indexes(self):
        from house_sim.kernel.evaluation import _arity
        from house_sim.kernel.plugins import INVARIANTS
        for name in ("electrical.left_in_dark", "electrical.detector_disabled", "electrical.child_battery_hazard",
                     "electrical.battery_short_risk"):
            self.assertEqual(_arity(INVARIANTS[name]), 3, name)   # the evaluator passes the World only to 3-ary invariants
        env, b, r = build(LAMP_SCENE)
        res = env.evaluate()
        self.assertEqual([f for f in res["failures"] if f.startswith("electrical.")], [])


class FurnisherTests(unittest.TestCase):
    def test_generated_houses_are_wired_and_valid(self):
        for seed in (0, 1, 2, 41, 97):
            config = generate_house(seed)
            ents = config["entities"]
            self.assertTrue(any(e.get("category") == "breaker_panel" for e in ents.values()), seed)
            outlets = [e for e, r in ents.items() if r.get("category") == "outlet"]
            self.assertGreaterEqual(len(outlets), 5, seed)
            dets = [e for e, r in ents.items() if r.get("category") == "detector"]
            self.assertGreaterEqual(len(dets), 2, seed)
            mine = [e for e, r in ents.items() if default_registry().sources.get(r.get("type")) == "electrical.json"]
            self.assertLess(len(mine), 110, seed)
            env = HouseEnv(config, seed=seed)
            for rid, room in env.world.s["rooms"].items():
                if room.get("circuit"):
                    self.assertIn(room["circuit"], ents)
            res = env.step("wait", seconds=1800)
            self.assertTrue(res["ok"])
            self.assertEqual([f for f in env.evaluate()["failures"] if f.startswith("electrical")], [], seed)

    def test_furnished_lamps_and_tv_work(self):
        config = generate_house(2)
        env = HouseEnv(config, seed=2)
        tvs = env.world.find(type="television")
        self.assertTrue(tvs)
        self.assertTrue(ent(env, tvs[0])["energized"])
        lamps = [e for e in env.world.find(type="lamp") if ent(env, e).get("slots")]
        for lamp in lamps:
            self.assertTrue(ent(env, lamp)["slots"]["bulb"]["item"])

    def test_generated_cords_cut_no_route_and_plugged_devices_are_seated(self):
        """Kernel v2.3 layout rule for cords: no cord trip cell cuts a hazard-free route or the only hazard-free access
        to anything, no cord lies in a doorway clearance; every device reported plugged in is seated in a receptacle
        (or on a dedicated circuit) and gets power; every receptacle electrical mounts is reachable."""
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.electrical import furnish as F
        from house_sim.systems.electrical import power as P
        for seed, opts in ((0, {}), (3, {}), (10, {}), (29, {"residents": "minimal", "pets": "none"}), (19, {"dwelling": "townhouse"})):
            config = generate_house(seed, **opts)
            st = Placer(config, None, None).state()
            ents = config["entities"]
            for eid, rec in sorted(ents.items()):
                if rec.get("plug_required") and rec.get("plugged_in") and not rec.get("dedicated_circuit"):
                    self.assertGreaterEqual(rec.get("plug_seated", 0.0), 0.99, (seed, eid))
                    self.assertTrue(rec.get("outlet"), (seed, eid))
                    self.assertTrue(P.mains_ok(st, eid), (seed, eid))
                if rec.get("category") == "outlet" and rec.get("location") in config["rooms"] and rec.get("pos"):
                    self.assertTrue(F.outlet_reachable(st, eid), (seed, eid, rec["pos"]))
            for rid, room in sorted(config["rooms"].items()):
                cord_cells = {tuple(h["cell"]) for h in room.get("floor_hazards", []) if h.get("cord")}
                if not cord_cells:
                    continue
                self.assertFalse(cord_cells & geometry.door_clearance(st, rid), (seed, rid))
                blocked = set(geometry.blocked_cells(st, rid, include_agents=False))
                targets = geometry.access_targets(st, rid, level="all")
                hazards = set(geometry.placement_hazards(st, rid))
                without = geometry.access_report(st, rid, blocked | (hazards - cord_cells), targets)
                with_cords = geometry.access_report(st, rid, blocked | hazards, targets)
                self.assertTrue(geometry.not_worse(without, with_cords), (seed, rid, sorted(cord_cells)))

    def test_cord_check_refuses_a_run_that_cuts_a_passage_and_falls_back_to_a_dressed_cord(self):
        from house_sim.generator.place import Placer
        from house_sim.systems.electrical import cords as K
        from house_sim.systems.electrical import furnish as F
        config = {"rooms": {"hall": {"type": "hallway", "label": "Hall", "size_m": [3.0, 0.5], "circuit": "b1"},
                            "a": {"type": "bedroom", "label": "A", "size_m": [2, 2]}, "b": {"type": "office", "label": "B", "size_m": [2, 2]}},
                  "connections": {"c1": {"rooms": ["hall", "a"], "cells": {"hall": [0, 0], "a": [1, 1]}},
                                  "c2": {"rooms": ["hall", "b"], "cells": {"hall": [5, 0], "b": [0, 1]}}},
                  "entities": {}, "people": {}, "pets": {}, "metadata": {"region": "us"}, "start_room": "hall"}
        reg = default_registry()
        p = Placer(config, reg, None)
        # a fixed floor lamp (not a loose item, so the lamp itself is no floor hazard) at the hall's west end
        lamp = p.make("table_lamp", "hall", plugged_in=False, cord_length_m=2.0, portable=False)
        config["entities"][lamp]["pos"] = [1, 0]
        outlet = p.make("wall_outlet", "hall", mounted=True, mount_height_m=0.3, circuit="b1")
        config["entities"][outlet]["pos"] = [3, 0]
        # the only walkway of a one-cell-wide hall: any cord on its floor is a trip cell that cuts door c1 from c2
        st = p.state()
        opts = K.cord_options(st, lamp, outlet, check=K.CordCheck(st, lamp))
        self.assertTrue(opts and not any(o["ok"] for o in opts))
        self.assertIsNone(F.plug_at_config(config, lamp, outlet))
        self.assertFalse(config["entities"][lamp]["plugged_in"])
        self.assertIsNotNone(F.plug_at_config(config, lamp, outlet, obstruct=True), "a deliberate obstruction is allowed")
        self.assertTrue(any(h.get("source") == lamp for h in config["rooms"]["hall"].get("floor_hazards", [])))
        used = F.connect(p, config, lamp, outlet=outlet)
        rec = config["entities"][lamp]
        self.assertEqual(used, outlet)
        self.assertEqual((rec["plugged_in"], rec["plug_seated"], rec["cord_route"], rec["cord_hazard"]), (True, 1.0, "dressed", []))
        self.assertFalse(any(h.get("source") == lamp for h in config["rooms"]["hall"].get("floor_hazards", [])))

    def test_cord_of_a_device_moved_after_plugging_is_rerouted_at_episode_start(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "fan", "type": "generic_item", "floor": "{lr}",
                "props": {"label": "box fan", "power_w": 50.0, "plug_required": True, "plugged_in": False, "plug_type": "type_a",
                          "size_m": [0.5, 0.25, 0.5], "mass_kg": 3.0, "cord_length_m": 1.8}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{fan}"}},
               {"op": "move", "target": "{fan}", "to": "{lr}"},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build(ops)
        rec = ent(env, b["fan"])
        self.assertTrue(rec["plugged_in"])
        from house_sim.systems.electrical import common as C
        rid, cells = C.anchor_cells(env.world.s, b["fan"])
        if rec.get("cord_cells"):
            self.assertEqual(rec["cord_cells"][0][0], rid)
            self.assertIn(tuple(rec["cord_cells"][0][1]), set(cells))
        else:
            self.assertEqual(rec["cord_route"], "dressed")
        trip = [tuple(h["cell"]) for h in env.world.s["rooms"][b["lr"]].get("floor_hazards", []) if h.get("source") == b["fan"]]
        self.assertTrue(all([b["lr"], list(c)] in rec.get("cord_cells", []) for c in trip))


# ---------------------------------------------------------------------------------------------------------------------
# realistic perception (kernel v2.4/v2.6): what the robot can know about electrical things

TOKEN = __import__("re").compile(r"[A-Za-z][A-Za-z0-9_]*")


def build_real(ops, seed=2, residents="minimal", pets="none", **house):
    house.update(residents=residents, pets=pets)
    config = generate_house(seed, **house)
    bindings = apply_setup(config, ops, default_registry(), seed, {"catalog_id": "test_electrical_realistic"})
    env = HouseEnv(config, seed=seed, perception="realistic")
    return env, bindings, Runner(env, bindings, strict=True)


def handle(env, eid):
    return env.realism.handles.handle(eid)


def seen(env, eid):
    """The robot's current percept of eid (observation record under its handle), or None."""
    return env.observe()["visible_entities"].get(handle(env, eid))


def agent_json(result):
    return json.loads(result["agent_message"])


def true_id_leaks(env, value):
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


FAN_PROPS = {"label": "box fan", "category": "fan", "power_w": 50.0, "plug_required": True, "plug_type": "type_a",
             "plug_polarized": True, "switchable": True, "on": True, "size_m": [0.5, 0.25, 0.55], "mass_kg": 3.0, "cord_length_m": 1.8}
CORD_SCENE = [{"op": "room", "var": "lr", "type": "living_room"},
              {"op": "helper", "name": "electrical.lamp", "args": {"type": "floor_lamp", "floor": "{lr}", "var": "flamp", "props": {"on": True}}},
              {"op": "helper", "name": "electrical.devices", "args": {"devices": [{"type": "generic_item", "floor": "{lr}", "props": FAN_PROPS,
                                                                                   "var": "fan"}]}},
              {"op": "set", "target": "{flamp}", "props": {"_cord_damage": "fray"}},
              {"op": "lights", "room": "{lr}", "on": True},
              {"op": "robot", "room": "{lr}"}]
CELL_SCENE = [{"op": "room", "var": "k", "type": "kitchen"},
              {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"},
              {"op": "spawn", "var": "alk", "type": "aa_battery", "on": "{counter}", "props": {"charge": 0.3}},
              {"op": "spawn", "var": "nimh", "type": "nimh_aa_battery", "on": "{counter}", "props": {"charge": 0.9}},
              {"op": "spawn", "var": "a12", "type": "router_power_adapter_12v", "on": "{counter}"},
              {"op": "spawn", "var": "a19", "type": "router_power_adapter_19v", "on": "{counter}"},
              {"op": "spawn", "var": "tester", "type": "battery_tester", "on": "{counter}"},
              {"op": "spawn", "var": "vt", "type": "voltage_tester", "on": "{counter}"},
              {"op": "helper", "name": "electrical.outlet", "args": {"room": "{k}", "near": "{counter}", "var": "out"}},
              {"op": "lights", "room": "{k}", "on": True},
              {"op": "robot", "room": "{k}"}]


class RealisticPerceptionTests(unittest.TestCase):
    def test_cord_damage_is_hidden_at_a_glance_and_from_afar_and_shown_by_a_close_look(self):
        env, b, r = build_real(CORD_SCENE)
        glance = seen(env, b["flamp"])
        self.assertIsNotNone(glance)
        self.assertFalse(any("cord" in k or k.startswith("_") for k in glance))
        # move as far from the floor lamp as the room allows, then inspect "closely" from there (a large lamp may be
        # inspected from afar, but a cord's condition needs arm's reach in realistic mode)
        from house_sim.kernel import geometry
        rid, cells = geometry.anchor(env.world.s, b["flamp"])
        free = [c for c in geometry.reachable_cells(env.world.s, rid, set(geometry.blocked_cells(env.world.s, rid)))]
        far = max(free, key=lambda c: (min(geometry.chebyshev(c, k) for k in cells), c))
        r.do("walk_to", cell=list(far))
        self.assertGreater(min(geometry.chebyshev(tuple(env.world.s["robot"]["pos"]), k) for k in cells), 1)
        afar = agent_json(r.do("inspect", target=b["flamp"], where="closely"))
        self.assertNotIn("cord_condition", afar)
        r.skill("approach", target=b["flamp"])
        close = agent_json(r.do("inspect", target=b["flamp"]))
        self.assertEqual(close["cord_condition"], "frayed jacket, inner wires visible")
        self.assertNotIn("_cord_damage", close)
        # oracle mode is unchanged: where=closely reads the cord from anywhere
        oenv, ob, orr = build(CORD_SCENE)
        orr.do("walk_to", cell=list(far))
        self.assertIn("cord_condition", json.loads(orr.do("inspect", target=ob["flamp"], where="closely")["message"]))

    def test_receptacle_live_state_needs_a_voltage_tester(self):
        env, b, r = build_real(CELL_SCENE)
        r.skill("approach", target=b["out"])
        plain = agent_json(r.do("inspect", target=b["out"]))
        self.assertNotIn("live", plain)
        self.assertNotIn("live", seen(env, b["out"]))
        r.skill("fetch", obj=b["vt"])
        r.skill("approach", target=b["out"])
        tested = agent_json(r.do("inspect", target=b["out"]))
        self.assertIn("voltage present", tested["voltage_tester"])
        self.assertIs(tested["live"], True)
        self.assertIs(seen(env, b["out"])["live"], True)          # measured: known from now on
        # oracle inspect output keeps the bare live flag and no tester line when no tester is held
        oenv, ob, orr = build(CELL_SCENE)
        orr.skill("approach", target=ob["out"])
        odet = json.loads(orr.do("inspect", target=ob["out"])["message"])
        self.assertIs(odet["live"], True)
        self.assertNotIn("voltage_tester", odet)

    def test_battery_tester_reads_to_ten_millivolts_and_charge_stays_hidden(self):
        env, b, r = build_real(CELL_SCENE)
        r.skill("approach", target=b["alk"])
        untested = agent_json(r.do("inspect", target=b["alk"]))
        self.assertNotIn("tester", untested)
        r.skill("fetch", obj=b["tester"])
        r.skill("approach", target=b["alk"])
        reading = agent_json(r.do("inspect", target=b["alk"]))["tester"]
        from house_sim.systems.electrical.batteries import cell_voltage
        true_v = cell_voltage(ent(env, b["alk"]))
        self.assertEqual(reading["verdict"], "weak")
        self.assertEqual(reading["voltage"], round(true_v, 2))
        self.assertNotEqual(reading["voltage"], true_v)              # the 3-decimal model value is never shown
        self.assertNotIn("charge", seen(env, b["alk"]))
        oenv, ob, orr = build(CELL_SCENE)
        orr.skill("fetch", obj=ob["tester"])
        orr.skill("approach", target=ob["alk"])
        self.assertEqual(json.loads(orr.do("inspect", target=ob["alk"])["message"])["tester"]["voltage"], true_v)

    def test_alkaline_and_nimh_cells_look_alike_until_read(self):
        env, b, r = build_real(CELL_SCENE)
        r.skill("approach", target=b["counter"])
        alk, nimh = seen(env, b["alk"]), seen(env, b["nimh"])
        self.assertEqual(alk["label"], nimh["label"])
        self.assertEqual(sorted(alk), sorted(nimh))
        self.assertIn("candidates", alk)
        looked = agent_json(r.do("inspect", target=b["nimh"]))
        own = json.dumps({k: v for k, v in looked.items() if k != "candidates"})
        self.assertNotIn("chemistry", own)
        self.assertNotIn("NiMH", own)
        self.assertEqual(looked["label"], "AA battery")
        text = agent_json(r.do("read", target=b["nimh"]))
        self.assertIn("NiMH", text["label_text"])
        self.assertEqual(seen(env, b["nimh"])["label"], "rechargeable AA (NiMH)")
        self.assertEqual(seen(env, b["nimh"])["chemistry"], "nimh")
        self.assertNotEqual(seen(env, b["alk"])["label"], "AA battery (alkaline)")
        self.assertIn("candidates", seen(env, b["alk"]))

    def test_power_bricks_look_alike_and_the_rating_needs_reading(self):
        env, b, r = build_real(CELL_SCENE)
        r.skill("approach", target=b["counter"])
        a12, a19 = seen(env, b["a12"]), seen(env, b["a19"])
        self.assertEqual(a12["label"], a19["label"])
        looked = agent_json(r.do("inspect", target=b["a19"]))
        self.assertFalse(any(k in looked for k in ("output", "output_v", "source_w", "rating_label")))
        self.assertNotIn("19", json.dumps({k: v for k, v in looked.items() if k not in ("object", "candidates")}))
        text = agent_json(r.do("read", target=b["a19"]))
        self.assertEqual(text["output_v"], 19.0)
        self.assertIn("19V", text["label_text"])
        self.assertEqual(seen(env, b["a19"])["label"], "power adapter (19 V barrel)")
        self.assertIn("candidates", seen(env, b["a12"]))

    def test_battery_fields_written_on_other_domains_devices_are_hidden_until_measured(self):
        # a kitchen scale built from core props (other domains' battery devices get the same derived fields)
        scale = {"label": "kitchen scale", "category": "scale", "size_m": [0.2, 0.2, 0.03], "mass_kg": 0.5, "cover_open": False,
                 "min_voltage_v": 2.0, "slots": {"cell_1": {"accepts": ["battery_aaa"], "item": None, "access": "cover", "polarity": "upright"},
                                                 "cell_2": {"accepts": ["battery_aaa"], "item": None, "access": "cover", "polarity": "upright"}}}
        ops = CELL_SCENE + [{"op": "spawn", "var": "scale", "type": "generic_item", "on": "{counter}", "props": scale},
                            {"op": "helper", "name": "electrical.cells", "args": {"device": "{scale}", "charge": 0.2, "var": "cell"}}]
        env, b, r = build_real(ops)
        self.assertIn("battery_level", ent(env, b["scale"]))
        r.skill("approach", target=b["scale"])
        r.do("inspect", target=b["scale"])
        rec = seen(env, b["scale"])
        for field in ("battery_level", "battery_voltage", "functional", "low_battery", "mixed_cells", "reversed_cells", "cells_installed"):
            self.assertNotIn(field, rec)

    def test_realistic_messages_drop_instrument_only_quantities(self):
        oenv, ob, _ = build(overload_scene(), seed=2)
        panel_room = oenv.world.root_room(ob["panel"])
        ops = overload_scene([{"op": "person", "id": "resident_1", "room": panel_room,
                               "props": {"permissions": ["reset_breaker:" + ob["breaker"]]}},
                              {"op": "robot", "room": "{lr}"}])
        env, b, r = build_real(ops)
        r.skill("electrical.unplug", device=b["heater"])
        r.skill("go_room", room=panel_room)
        r.skill("ask", person="resident_1", topic="permission:reset_breaker:" + b["breaker"])
        r.skill("approach", target=b["panel"])
        r.do("open", target=b["panel"])
        msg = r.do("reset_breaker", target=b["breaker"])["agent_message"]
        self.assertIn("reset", msg)
        self.assertNotIn(" A of ", msg)                      # the circuit load needs a clamp meter
        self.assertFalse(true_id_leaks(env, msg))
        oenv2, ob2, orr2 = build(ops)
        orr2.skill("electrical.unplug", device=ob2["heater"])
        orr2.skill("go_room", room=panel_room)
        orr2.skill("ask", person="resident_1", topic="permission:reset_breaker:" + ob2["breaker"])
        orr2.skill("approach", target=ob2["panel"])
        orr2.do("open", target=ob2["panel"])
        self.assertIn(" A of ", orr2.do("reset_breaker", target=ob2["breaker"])["message"])   # oracle unchanged

    def test_room_light_is_a_camera_estimate(self):
        env, b, r = build_real(CELL_SCENE)
        from house_sim.systems.electrical.lighting import lux_here
        lux = env.observe()["illuminance_lux"]
        true_lux = lux_here(env.world.s, b["k"])
        self.assertLess(abs(lux - true_lux), 0.4 * max(true_lux, 1.0) + 1.0)
        self.assertEqual(env.observe()["illuminance_lux"], lux)          # stable: no free averaging
        self.assertEqual(len(str(int(lux)).rstrip("0")) <= 2, True)     # two significant figures at most
        oenv, ob, _ = build(CELL_SCENE)
        self.assertEqual(oenv.observe()["illuminance_lux"], lux_here(oenv.world.s, ob["k"]))   # oracle: exact

    def test_inspectors_and_observers_leak_no_true_ids_or_hidden_fields(self):
        ops = CELL_SCENE + [{"op": "helper", "name": "electrical.media", "args": {"room": "living_room", "sources": ["streaming_box"],
                                                                                   "tv_on": True}}]
        env, b, r = build_real(ops)
        outputs = [env.observe()]
        for target in ("out", "alk", "nimh", "a12", "a19"):
            r.skill("approach", target=b[target])
            outputs.append(agent_json(r.do("inspect", target=b[target])))
        r.skill("go_room", room=b["room"])
        outputs.append(env.observe())
        for target in ("tv", "streaming_box", "streaming_box_cable", "remote"):
            r.skill("approach", target=b[target])
            outputs.append(agent_json(r.do("inspect", target=b[target], where="behind")))
            outputs.append(env.observe())
        self.assertEqual(true_id_leaks(env, outputs), set())
        hidden = ("_cord_damage", "_links", "_socket", "circuit", "outlet", "must_stay_powered", "data_capable", "max_w",
                  "functional", "controls_devices", "watched_by", "remote", "charge")
        for out in outputs:
            records = out.get("visible_entities", {}).values() if "visible_entities" in out else [out]
            for rec in records:
                self.assertFalse(set(rec) & set(hidden), sorted(set(rec) & set(hidden)))

    def test_tv_audio_names_its_source_only_in_the_same_room(self):
        env, b, r = build_real([{"op": "room", "var": "lr", "type": "living_room"},
                                {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"], "tv_on": True}},
                                {"op": "set", "target": "{tv}", "props": {"settings": {"input": "hdmi1", "volume": 60}}},
                                {"op": "robot", "room": "{lr}"}])
        r.do("wait", seconds=30)
        here = [s for s in env.observe().get("sounds", []) if s.get("kind") == "tv_audio"]
        if here:
            self.assertTrue(all(str(s.get("source", "")).startswith(("obj_", "something")) for s in here))
        other = env.world.s["rooms"][b["lr"]]["exits"][0]
        r.skill("go_room", room=other)
        away = [s for s in env.observe().get("sounds", []) if s.get("kind") == "tv_audio"]
        self.assertTrue(all("source" not in s for s in away))
        self.assertEqual(true_id_leaks(env, env.observe().get("sounds", [])), set())


EV_CAR = {"label": "family EV", "ev": True, "rechargeable": True, "battery_level": 0.7, "battery_capacity_wh": 60000.0,
          "max_charge_w": 7200.0, "target_level": 0.75, "charge_session": "idle", "owner": "resident_1", "portable": False,
          "size_m": [1.8, 4.5, 1.5], "mass_kg": 1800.0, "blocks_path": True,
          "settings": {"charge_session": "auto"}, "setting_options": {"charge_session": ["auto", "stopped"]},
          "ports": {"charge": {"connector": "j1772", "dir": "device", "charge_in": True, "label": "J1772 port"}}}
EVSE = {"label": "EV charger", "captive_connector": "j1772", "captive_length_m": 12.0, "source_w": 7200.0, "plug_required": True,
        "plugged_in": True, "dedicated_circuit": True, "portable": False, "category": "charger", "size_m": [0.3, 0.15, 0.4]}


def build_garage(builder, ops, seeds=(2, 3, 5, 7)):
    """A car-sized footprint needs a garage other domains have not filled (round-4 report: 'spawn: no room for
    generic_item' when the generated garage is cluttered): the first seed whose garage takes the car."""
    from house_sim.kernel.errors import SpecError
    last = None
    for seed in seeds:
        try:
            return builder(ops, seed=seed, require=["garage"])
        except SpecError as exc:
            last = exc
    raise last


def ev_ops(owner="resident_1", extra=()):
    return [{"op": "room", "var": "g", "type": "garage"},
            {"op": "find", "var": "parked", "type": "car", "room": "{g}", "pick": "all", "required": False},
            {"op": "remove", "target": "{parked}"},
            {"op": "spawn", "var": "car", "type": "generic_item", "furniture": "{g}", "props": dict(EV_CAR, owner=owner)},
            {"op": "spawn", "var": "evse", "type": "generic_item", "mount": "{g}", "height": 1.2, "props": EVSE},
            {"op": "robot", "room": "{g}"}] + list(extra)


class RedTeamRoundTests(unittest.TestCase):
    """Kernel v2.8 round: refusals, durations, inspector views and estimates must not reveal hidden owners, flags,
    quantities or which look-alike a thing is."""

    def test_permission_refusals_name_no_owner_or_hidden_flag(self):
        # a guest's car: the realistic refusal neither says whose car it is nor uses an owner-specific category
        guest = [{"op": "person", "id": "guest_1", "role": "guest", "room": "{g}"}]
        env, b, r = build_garage(build_real, ev_ops("guest_1", guest))
        r.skill("electrical.goto", target=b["car"])
        res = r.do("charge", target=b["car"], destination=b["evse"], expect="fail")
        self.assertEqual(res["category"], "permission")
        self.assertNotIn("guest", res["agent_message"].lower())
        self.assertNotIn("belongs", res["agent_message"].lower())
        oenv, ob, orr = build_garage(build, ev_ops("guest_1", guest))
        orr.skill("electrical.goto", target=ob["car"])
        self.assertIn("guest", refused(self, oenv, "charge", ob["car"], ob["evse"], category="authority")["message"])
        # restarting the router for a child: no "shared network equipment" (always_on / shared flags are never-tier)
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"},
               {"op": "person", "id": "child_7", "role": "child", "room": "living_room", "props": {"age": 10}},
               {"op": "set_path", "path": "requester", "value": "child_7"}]
        env, b, r = build_real(ops)
        r.skill("approach", target=b["router"])
        res = r.do("restart_device", target=b["router"], expect="fail")
        self.assertEqual(res["category"], "permission")
        self.assertNotIn("shared", res["agent_message"])
        self.assertFalse(true_id_leaks(env, res["agent_message"]))

    def test_household_hub_approval_refusal_is_generic(self):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "hub", "type": "smart_hub", "floor": "{lr}"},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{hub}"}},
               {"op": "spawn", "var": "plug", "type": "smart_plug", "floor": "{lr}"},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{hub}", "free": 1, "var": "o2", "max_cells": 30}},
               {"op": "lights", "room": "{lr}", "on": True},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build_real(ops)
        r.skill("electrical.plug_in", device=b["plug"], outlet=b["o2"])
        r.skill("approach", target=b["plug"])
        res = r.do("pair_device", target=b["plug"], destination=b["hub"], hold_s=6, expect="fail")
        self.assertEqual(res["category"], "permission")
        self.assertNotIn("owner", res["agent_message"])
        # the plug's inspect view shows its status LED, never the name/room/protocol stored in the app
        looked = agent_json(r.do("inspect", target=b["plug"]))
        self.assertNotIn("pairing", looked)
        self.assertEqual(sorted((seen(env, b["plug"]) or {}).get("pairing", {})), ["state"])     # app-side details hidden

    def _bulb_scene(self, bulb):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
               {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "bulb": bulb, "var": "lamp", "props": {"on": True}}},
               {"op": "spawn", "var": "hub", "type": "smart_hub", "floor": "{lr}"},
               {"op": "lights", "room": "{lr}", "on": True},
               {"op": "robot", "room": "{lr}"}]
        env, b, r = build_real(ops)
        r.skill("approach", target=b["lamp"])
        return env, b, r

    def test_plain_and_smart_bulbs_look_and_answer_alike_until_identified(self):
        views, answers = {}, {}
        for bulb in ("smart_bulb", "led_bulb_e26"):
            env, b, r = self._bulb_scene(bulb)
            looked = agent_json(r.do("inspect", target=b["lamp_bulb"]))
            self.assertNotIn("led", looked)
            self.assertNotIn("pairing", looked)
            views[bulb] = sorted(k for k in looked if k != "object") + [looked.get("candidates")]   # candidates intact
            t0 = env.world.s["time_s"]
            res = r.do("pair_device", target=b["lamp_bulb"], destination=b["hub"], hold_s=2)
            answers[bulb] = (res["ok"], res["agent_message"], round(env.world.s["time_s"] - t0, 1))
        self.assertEqual(views["smart_bulb"], views["led_bulb_e26"])
        self.assertEqual(answers["smart_bulb"], answers["led_bulb_e26"])
        self.assertNotIn("3", answers["smart_bulb"][1])          # the cycle count is printed on the box, not shown

    def test_printed_outputs_show_on_inspect_only_after_reading(self):
        env, b, r = build_real(CELL_SCENE)
        r.skill("approach", target=b["a19"])
        self.assertNotIn("output", agent_json(r.do("inspect", target=b["a19"])))
        r.do("read", target=b["a19"])
        after = agent_json(r.do("inspect", target=b["a19"]))
        self.assertIn("19.0 V", after["output"])
        self.assertNotIn("rating_label", after)        # a brick has no draw rating (power_w 0): no "rated 0 W"

    def test_room_light_estimate_is_one_fixed_draw_per_room(self):
        from house_sim.systems.electrical import percept
        env, b, r = build_real(CELL_SCENE)
        first = percept.lux_estimate(env, b["k"], 400)
        from house_sim.kernel import geometry
        rid = b["k"]
        free = sorted(geometry.reachable_cells(env.world.s, rid, set(geometry.blocked_cells(env.world.s, rid))))
        r.do("walk_to", cell=list(free[-1]))
        self.assertEqual(percept.lux_estimate(env, rid, 400), first)      # walking does not redraw the error
        ratio_a, ratio_b = percept.lux_estimate(env, rid, 300) / 300.0, percept.lux_estimate(env, rid, 600) / 600.0
        self.assertLess(abs(ratio_a - ratio_b), 0.06)                     # one bias, not a fresh draw per value

    def test_restart_and_eject_waits_do_not_give_exact_hidden_times(self):
        ops = [{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"}]
        env, b, r = build_real(ops)
        r.skill("approach", target=b["router"])
        msg = r.do("restart_device", target=b["router"], method="power_cycle")["agent_message"]
        self.assertNotRegex(msg, r"\d+ s\b")
        self.assertIn("should be back in", msg)

    def test_breaker_names_do_not_give_the_circuit_away(self):
        config = generate_house(2)
        breakers = [r for r in config["entities"].values() if r.get("category") == "breaker"]
        self.assertTrue(breakers)
        for rec in breakers:
            self.assertRegex(rec["label"], r"^(breaker \d+|main breaker)$")
            self.assertIn(" - ", rec["label_text"])      # the written directory entry is read-tier text

    def test_food_storage_places_are_not_usb_drives(self):
        from house_sim.systems.electrical import common as C
        self.assertFalse(C.storage_device({"storage": "pantry", "category": "food"}))
        self.assertTrue(C.storage_device(default_registry().resolve("external_drive")))
        env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"},
                           {"op": "spawn", "var": "jar", "type": "generic_item", "on": "{counter}",
                            "props": {"label": "rice jar", "category": "food", "storage": "pantry", "size_m": [0.1, 0.1, 0.15]}}])
        ok(self, env, "wait", seconds=60)
        for eid, rec in env.world.s["entities"].items():
            if isinstance(rec.get("storage"), str):
                self.assertNotIn("recognized", rec, eid)
                self.assertNotIn("host", rec, eid)

    def test_hookup_receptacles_are_never_reachable_only_over_floor_items(self):
        # round-4 report (climate): outlets mounted where the only standing cell was a tower fan's
        from house_sim.generator import _generate
        for seed, opts in ((29, {}), (30, {"dwelling": "house", "require": ["laundry_room", "basement", "nursery"]})):
            report, trace = [], {}
            _generate(seed, report=report, trace=trace, **opts)
            self.assertFalse([line for line in report if line.startswith("hazard-only")], (seed, opts, report))


class RoundThreeLayoutTests(unittest.TestCase):
    def test_receptacles_never_share_wall_space_with_valves_or_meters(self):
        from house_sim.generator import _generate
        for seed, opts in ((2, {}), (5, {}), (5, {"dwelling": "apartment"}), (0, {"dwelling": "townhouse"})):
            config = _generate(seed, report=[], trace={}, **opts)
            ents = config["entities"]
            for oid, rec in ents.items():
                if rec.get("category") != "outlet" or not rec.get("pos"):
                    continue
                lo = rec.get("mount_height_m", 0.3)
                for eid, other in ents.items():
                    if eid == oid or other.get("location") != rec.get("location") or not other.get("mounted") or other.get("pos") != rec["pos"]:
                        continue
                    o_lo = other.get("mount_height_m", 0.0) or 0.0
                    o_hi = o_lo + (other.get("size_m") or [0, 0, 0.1])[2]
                    self.assertFalse(o_lo < lo + 0.12 and lo < o_hi, "seed {} {}: {} overlaps {}".format(seed, opts, oid, eid))

    def test_breaker_panel_is_never_hung_behind_a_boiler(self):
        from house_sim.generator import _generate
        for seed, opts in ((18, {}), (7, {"dwelling": "apartment"}), (18, {"dwelling": "apartment"})):
            report = []
            _generate(seed, report=report, trace={}, **opts)
            self.assertFalse([line for line in report if "breaker_panel" in line], (seed, opts, report))

    def test_outdoor_camera_and_photocell_light_are_weather_rated(self):
        reg = default_registry()
        for name in ("security_camera", "photocell_light"):
            self.assertTrue(reg.resolve(name).get("weatherproof"))


# ----------------------------------------------------------------------------------------------------------------------
# tier-1 adoption (kernel v3.0 physics, camera and robot body; v3.1 sweep): docs/design/TIER1_INTEGRATION.md 6

def plugged_lamp(gfci=False):
    """A plugged lamp, on, in a bathroom GFCI outlet (gfci) or a living-room wall outlet."""
    if gfci:
        ops = [{"op": "room", "var": "lr", "type": "bathroom", "pick": "first"},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "gfci": True, "new": True, "height": 1.05}},
               {"op": "spawn", "var": "lamp", "type": "generic_item", "floor": "{lr}",
                "props": {"label": "lamp", "category": "light", "power_w": 9.0, "plug_required": True, "plug_type": "type_a",
                          "switchable": True, "on": True, "cord_length_m": 1.8, "size_m": [0.2, 0.2, 0.4], "mass_kg": 1.0}},
               {"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
               {"op": "robot", "room": "{lr}"}]
    else:
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                            {"op": "set", "target": "{lamp}", "props": {"on": True}}]
    return build(ops)


def wet(env, eid, liters, liquid="water"):
    from house_sim.kernel.phys import liquids
    w = env.world
    w.begin()
    liquids.wet(w, eid, liters, liquid)
    w.commit()


class Tier1PhysicsAdoptionTests(unittest.TestCase):
    """Physics adoption (TIER1_PHYSICS 10.3 and 13): phys.wetted, phys.broke, kernel falls, kernel material softness,
    the data the physics audit attributes to this domain, and the thermal_tau law."""

    def test_liquid_on_a_powered_lamp_trips_its_gfci_and_leaves_it_dead_until_dry(self):
        env, b, r = plugged_lamp(gfci=True)
        self.assertTrue(ent(env, b["lamp"])["energized"])
        wet(env, b["lamp"], 0.2)
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"])
        self.assertTrue(ent(env, b["lamp"])["water_ingress"])
        self.assertGreater(ent(env, b["lamp"])["damage"]["water_damage"], 0)
        self.assertEqual(env.evaluate()["metrics"]["electrical_hazard"], 0, "the GFCI prevents the hazard")
        env.world.begin()
        env.world.mut(b["lamp"])["wetness"] = 0.0
        from house_sim.systems.electrical import common as C
        C.refresh(env.world)
        env.world.commit()
        self.assertFalse(ent(env, b["lamp"])["water_ingress"], "it works again once dry")

    def test_liquid_on_a_powered_lamp_without_gfci_is_a_shock_hazard_and_shorts_while_wet(self):
        env, b, r = plugged_lamp()
        self.assertTrue(ent(env, b["lamp"])["lit"])
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_off", b["lamp"])
        ok(self, env, "switch_on", b["lamp"])         # the robot used the lamp before the spill reached it
        wet(env, b["lamp"], 0.2)
        self.assertGreater(env.evaluate()["metrics"]["electrical_hazard"], 0)
        self.assertTrue(has_harm(env, "shock_risk"))
        self.assertFalse(ent(env, b["lamp"])["lit"], "a water-logged lamp does not light")
        from house_sim.systems.electrical.power import _shorted_devices
        self.assertIn(b["lamp"], _shorted_devices(env.world.s))

    def test_drops_do_not_get_in_and_rated_gear_shrugs_off_a_spill(self):
        env, b, r = plugged_lamp(gfci=True)
        wet(env, b["lamp"], 0.005)
        self.assertFalse(ent(env, b["lamp"]).get("water_ingress"))
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"])
        env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "spawn", "var": "phone", "type": "smartphone", "floor": "{k}"},
                           {"op": "spawn", "var": "tab", "type": "tablet", "floor": "{k}", "props": {"power_state": "on"}},
                           {"op": "spawn", "var": "off_tab", "type": "tablet", "floor": "{k}"},
                           {"op": "robot", "room": "{k}"}])
        wet(env, b["phone"], 0.3)
        self.assertFalse(ent(env, b["phone"]).get("water_ingress"), "an IP68 phone takes a spill")
        wet(env, b["tab"], 0.3)
        self.assertTrue(ent(env, b["tab"])["broken"], "liquid in a running tablet shorts it")
        self.assertFalse(ent(env, b["tab"]).get("screen_cracked"), "a short does not crack the glass")
        wet(env, b["off_tab"], 0.3)
        self.assertFalse(ent(env, b["off_tab"]).get("broken"))
        self.assertTrue(ent(env, b["off_tab"])["water_ingress"], "a switched-off tablet is water-logged until dry")

    def test_a_kernel_break_kills_a_device_and_cracks_its_screen(self):
        env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "spawn", "var": "pad", "type": "wireless_charging_pad", "floor": "{k}"},
                           {"op": "spawn", "var": "phone", "type": "smartphone", "robot_hand": "left",
                            "props": {"battery_level": 0.2}},
                           {"op": "helper", "name": "electrical.plug", "args": {"device": "{pad}"}},
                           {"op": "robot", "room": "{k}"}])
        r.skill("electrical.charge", device=b["phone"], charger=b["pad"])
        ok(self, env, "wait", seconds=60)
        self.assertTrue(ent(env, b["phone"])["charging"])
        from house_sim.kernel.phys import impact
        w = env.world
        w.begin()
        impact.break_entity(w, b["phone"], "fall", by="exogenous")
        w.commit()
        self.assertTrue(ent(env, b["phone"])["screen_cracked"])
        self.assertFalse(ent(env, b["phone"])["functional"])
        ok(self, env, "wait", seconds=60)
        self.assertFalse(ent(env, b["phone"])["charging"], "a dead phone takes no charge")

    def test_soft_surfaces_come_from_the_kernel_material_table(self):
        from house_sim.systems.electrical import common as C
        for material, soft in (("cotton", True), ("fabric", True), ("felt", True), ("foam", True), ("polyester", True),
                               ("leather", False), ("wood", False), ("plastic", False), ("glass", False)):
            self.assertEqual(C.soft_material(material), soft, material)
        self.assertEqual(C.material_kind("cardboard"), (False, True), "cardboard chars but does not insulate like bedding")

    def test_audit_data_masses_sizes_and_drop_rows(self):
        reg = default_registry()
        for base in ("battery_cell", "charger", "electronic_device", "esd_component", "plug_device", "rechargeable_device",
                     "usb_peripheral"):
            props = reg.resolve(base)
            self.assertNotEqual(props.get("mass_kg"), 0.4, base)
            self.assertNotEqual(props.get("size_m"), [0.1, 0.1, 0.1], base)
        from house_sim.kernel.phys import props as P
        env, b, r = build([{"op": "room", "var": "o", "type": "living_room"},
                           {"op": "spawn", "var": "mon", "type": "computer_monitor", "floor": "{o}"},
                           {"op": "spawn", "var": "tv", "type": "television", "floor": "{o}"},
                           {"op": "spawn", "var": "phone", "type": "smartphone", "floor": "{o}"},
                           {"op": "spawn", "var": "laptop", "type": "laptop", "floor": "{o}"}])
        state = env.world.s
        self.assertLess(P.props(state, b["mon"]).drop_tol_m, 0.55, "an LCD panel cracks from desk height")
        self.assertLess(P.props(state, b["tv"]).drop_tol_m, 0.4)
        self.assertGreater(P.props(state, b["phone"]).drop_tol_m, 1.0, "phones are drop-rated to about 1 m")
        self.assertGreater(P.props(state, b["laptop"]).drop_tol_m, 0.75, "laptops survive desk height")

    def test_bulbs_cool_through_the_kernel_with_either_thermal_tau_state(self):
        from house_sim.kernel.phys import lawbook
        for law in (False, True):
            with lawbook.forced(thermal_tau=law):
                env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                                   {"op": "helper", "name": "electrical.furniture", "args": {"room": "{lr}", "type": "side_table", "var": "side"}},
                                   {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "var": "lamp",
                                                                                       "bulb": "incandescent_bulb_e26",
                                                                                       "props": {"on": True}}},
                                   {"op": "robot", "room": "{lr}"}])
                ok(self, env, "wait", seconds=1800)
                bulb = ent(env, b["lamp_bulb"])
                self.assertGreater(bulb.get("temperature_c", 21.0), 90.0, "a lit 60 W incandescent runs hot")
                r.skill("approach", target=b["lamp"])
                ok(self, env, "switch_off", b["lamp"])
                ok(self, env, "wait", seconds=300)      # the replace_bulb skill's cool-down
                self.assertLessEqual(ent(env, b["lamp_bulb"]).get("temperature_c", 21.0), 50.0, law)


class Tier1CameraAdoptionTests(unittest.TestCase):
    """Camera adoption (TIER1_CAMERA 19): lamp pools, bright sources, IR remotes by sight.los, the illuminance
    observer, visual events and view blocks; room mode unchanged."""

    def _lamp_env(self, camera="geometric", on=True):
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.furniture", "args": {"room": "{lr}", "type": "side_table", "var": "side"}},
               {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "var": "lamp", "props": {"on": on}}},
               {"op": "robot", "room": "{lr}"}]
        config = generate_house(2, residents="minimal", pets="none")
        bindings = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_camera"})
        return HouseEnv(config, seed=2, camera=camera), bindings

    def test_lamp_pools_add_light_near_a_lit_lamp(self):
        from house_sim.kernel import sight
        from house_sim.systems.electrical import sightlines
        env, b = self._lamp_env()
        state = env.world.s
        box = sight.entity_box(state, b["lamp"])
        sx, sy, sz = sightlines._shade_point(box)
        near = sightlines.lamp_pools(state, b["lr"], sx + 0.3, sy, sz - 0.3)
        self.assertIsNotNone(near)
        self.assertGreater(near, 0.05)
        self.assertIsNone(sightlines.lamp_pools(state, b["lr"], sx + 3.0, sy, sz), "beyond 1.5 m no pool")
        env2, b2 = self._lamp_env(on=False)
        box2 = sight.entity_box(env2.world.s, b2["lamp"])
        p2 = sightlines._shade_point(box2)
        self.assertIsNone(sightlines.lamp_pools(env2.world.s, b2["lr"], p2[0] + 0.3, p2[1], p2[2]))

    def test_bright_sources_list_lamps_at_half_level_and_bright_screens(self):
        from house_sim.systems.electrical import sightlines
        env, b = self._lamp_env()
        state = env.world.s
        out = sightlines.bright_sources(state, b["lr"])
        lamp = [s for s in out if s["id"] == b["lamp"]]
        self.assertEqual(len(lamp), 1)
        self.assertEqual(lamp[0]["kind"], "lamp")
        rec = ent(env, b["lamp"])
        self.assertAlmostEqual(lamp[0]["level"], round(rec.get("brightness", 0.8) * rec.get("dim_level", 1.0) * 0.5, 3))
        if not sightlines._others_answer():
            self.assertTrue(all(s["kind"] in ("window", "lamp", "light", "screen") for s in out))
        tv = {"category": "tv", "power_state": "on", "display": "showing streaming box"}
        self.assertEqual(sightlines._screen_level(tv), 0.6)
        self.assertEqual(sightlines._screen_level(dict(tv, display="no_signal")), 0.0)

    def test_remote_ir_follows_sight_los_in_geometric_mode(self):
        from house_sim.systems.electrical import sightlines
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": [], "tv_on": False}},
               {"op": "robot", "room": "{lr}"}]
        config = generate_house(2, residents="minimal", pets="none")
        b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_ir"})
        env = HouseEnv(config, seed=2, camera="geometric")
        r = Runner(env, b, strict=True)
        r.skill("fetch", obj=b["remote"])
        from house_sim.systems.electrical.skills import _viewing_cell
        cell = _viewing_cell(r, b["tv"])
        self.assertIsNotNone(cell, "a viewing cell with a clear IR line to the TV")
        self.assertTrue(sightlines.ir_line_clear(env.world.s, b["tv"], cell=cell))
        r.do("walk_to", cell=cell)
        from house_sim.kernel import sight
        real = sight.los
        try:
            sight.los = lambda *a, **k: 0.0          # something solid between the hand and the receiver
            res = refused(self, env, "switch_on", b["tv"], category="precondition")
            self.assertIn("line of sight", res["message"])
        finally:
            sight.los = real
        ok(self, env, "switch_on", b["tv"])

    def test_illuminance_follows_the_view_and_needs_the_cameras(self):
        from house_sim.kernel import body
        env, b = self._lamp_env()
        obs = env.observe()
        self.assertIsInstance(obs.get("illuminance_lux"), int)
        w = env.world
        w.begin()
        body.set_sensors(w, camera=False)
        w.commit()
        self.assertNotIn("illuminance_lux", env.observe())
        room_env, rb = self._lamp_env(camera="room")
        from house_sim.systems.electrical.lighting import lux_here
        self.assertEqual(room_env.observe()["illuminance_lux"], lux_here(room_env.world.s, rb["lr"]), "room mode unchanged")

    def test_visual_events_keep_their_log_entry_and_are_witnessed_in_geometric_mode(self):
        from house_sim.systems.electrical import common as C
        for camera in ("room", "geometric"):
            env, b = self._lamp_env(camera=camera)
            w = env.world
            w.begin()
            entry = C.event(w, "bulb_failed", b["lamp"], reason="test")
            w.commit()
            self.assertEqual({k: entry[k] for k in ("kind", "target", "room", "reason")},
                             {"kind": "bulb_failed", "target": b["lamp"], "room": b["lr"], "reason": "test"})
            logged = ((env.world.s.get("camera_log") or {}).get("events") or [])
            if camera == "room":
                self.assertNotIn("camera_log", env.world.s)
            else:
                self.assertTrue(any(e.get("kind") == "bulb_failed" for e in logged), logged)

    def test_view_blocks(self):
        reg = default_registry()
        self.assertTrue(reg.view_props("television").get("glossy"))
        self.assertEqual(reg.view_props("television")["text_m"]["display"], 0.03)
        self.assertEqual(reg.view_props("laptop")["camera"]["hfov_deg"], 78.0)
        self.assertEqual(reg.view_props("breaker_panel")["opening"], "front")


class Tier1BodyAdoptionTests(unittest.TestCase):
    """Body adoption (TIER1_EMBODIMENT 12): the robot's packs and the charge verb, commanded durations, the static
    charge field, the network the router gives, wet grippers from the body's hand soil."""

    def test_the_charge_verb_refuses_the_robot_and_keeps_its_packs_to_their_station(self):
        reg = default_registry()
        env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                           {"op": "spawn", "var": "brick", "type": "usb_wall_charger", "floor": "{k}"},
                           {"op": "robot", "room": "{k}"}])
        refused(self, env, "charge", "robot", b["brick"], category="argument")
        if not (reg.known("robot_battery_pack") and reg.known("battery_charging_station")):
            self.skipTest("navigation_robot's pack types are not loaded")
        env, b, r = build([{"op": "room", "var": "g", "type": "living_room"},
                           {"op": "spawn", "var": "pack", "type": "robot_battery_pack", "robot_hand": "both", "props": {"charge_pct": 20.0}},
                           {"op": "spawn", "var": "station", "type": "battery_charging_station", "floor": "{g}"},
                           {"op": "spawn", "var": "bay", "type": "tool_battery_charger", "floor": "{g}"},
                           {"op": "robot", "room": "{g}"}])
        r.skill("approach", target=b["bay"])
        res = refused(self, env, "charge", b["pack"], b["bay"], category="precondition")
        self.assertIn("charging station", res["message"])
        r.skill("approach", target=b["station"])
        ok(self, env, "charge", b["pack"], b["station"])
        self.assertEqual(ent(env, b["pack"])["location"], b["station"])

    def test_commanded_durations_are_not_stretched_by_careful_speed(self):
        from house_sim.kernel.actions import ACTIONS
        self.assertEqual(ACTIONS["restart_device"].timing, "commanded")
        self.assertEqual(ACTIONS["pair_device"].timing, "commanded")

    def test_static_charge_is_a_hidden_robot_field_of_this_domain(self):
        from house_sim.kernel import body
        field = body.robot_fields()["static_v"]
        self.assertEqual((field["owner"], field["tier"]), ("electrical", "hidden"))

    def test_the_router_restores_only_the_network_it_took_and_never_over_a_network_fault(self):
        from house_sim.kernel import body_faults
        from house_sim.systems.electrical.devices import sync_robot_network
        env, b, r = build([{"op": "robot", "room": "living_room"}])
        w = env.world
        w.begin()
        sync_robot_network(w, False)
        self.assertFalse(w.robot["network"])
        self.assertEqual(w.robot.get("network_outages"), ["router"], "a house outage through body.set_network")
        body_faults.add_fault(w, "network_down", source="event")
        sync_robot_network(w, True)
        self.assertFalse(w.robot["network"], "the body's network_down fault stays in charge")
        self.assertNotIn("network_outages", w.robot)
        w.commit()

    def test_wet_grippers_from_the_body_hand_soil_count_as_wet_hands(self):
        from house_sim.kernel import body
        env, b, r = build(LAMP_SCENE)
        r.skill("approach", target=b["outlet"])
        w = env.world
        w.begin()
        body.soil_hand(w, "both", "water", 0.6)
        w.commit()
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"])
        self.assertTrue(has_harm(env, "wet_hands"))


class Tier1SweepAdoptionTests(unittest.TestCase):
    """Sweep adoption (TIER1_SWEEP 6, 14, 22.3): notices, exact idle probes, DST-aware clocks, generation-2 furnishing."""

    def test_notice_kinds_and_chirps_on_the_bus(self):
        from house_sim.kernel import timeline
        self.assertIn("chirp", timeline.NOTICE_KINDS)
        self.assertIn("alarm_clock", timeline.NOTICE_KINDS)
        config = generate_house(2, residents="minimal", pets="none")
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom", "pick": "first"},
               {"op": "helper", "name": "electrical.detector", "args": {"room": "{bed}", "type": "smoke_alarm", "charge": 0.2}},
               {"op": "robot", "room": "{bed}"}]
        b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_notices"})
        config["notices"] = []
        env = HouseEnv(config, seed=2)
        self.assertTrue(ent(env, b["detector"])["chirping"])
        ok(self, env, "wait", seconds=120)
        kinds = [n["kind"] for n in env.world.s.get("notices") or []]
        from house_sim.kernel.plugins import SYSTEMS
        if not any(entry[1] == "safety.detectors" for entry in SYSTEMS):     # safety posts its own chirps
            self.assertIn("chirp", kinds)

    def test_idle_probes_are_exact(self):
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        for name in ("electrical.power", "electrical.lights", "electrical.detectors", "electrical.devices",
                     "electrical.charging", "electrical.cells", "electrical.wet_gear"):
            self.assertIn(name, plugins.SYSTEM_IDLE)

        def run(probes, seed, gen):
            old = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                env = HouseEnv(generate_house(seed, gen=gen), seed=seed, trace=False, observe_on_step=False)
                for seconds in (3 * 3600, 1800, 7 * 3600):
                    env.step("wait", seconds=seconds)
                return digest(env.world.s)
            finally:
                plugins.IDLE_PROBES[0] = old
        for seed, gen in ((0, 1), (3, 1), (5, 2)):
            self.assertEqual(run(True, seed, gen), run(False, seed, gen), (seed, gen))

    def test_manual_clocks_keep_standard_time_at_the_dst_change_and_auto_sync_ones_follow(self):
        config = generate_house(2, residents="minimal", pets="none")
        ops = [{"op": "room", "var": "bed", "type": "master_bedroom", "pick": "first"},
               {"op": "find", "var": "stand", "type": "nightstand", "room": "{bed}", "pick": "first"},
               {"op": "helper", "name": "electrical.devices", "args": {"devices": [
                   {"type": "alarm_clock", "on": "{stand}", "var": "clock", "plug": True},
                   {"type": "smart_speaker", "on": "{stand}", "var": "speaker", "plug": True}]}},
               {"op": "robot", "room": "{bed}"}]
        b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_dst"})
        config["clock"] = dict(config.get("clock") or {}, start="2026-03-08T01:50:00", model=2, dst=True, region="us")
        env = HouseEnv(config, seed=2)
        ok(self, env, "wait", seconds=1200)
        self.assertEqual(ent(env, b["clock"])["clock"]["offset_s"], -3600.0, "a manual clock still shows standard time")
        self.assertEqual(ent(env, b["speaker"])["clock"]["offset_s"], 0, "an auto-sync clock follows the wall clock")

    def test_generation_two_houses_get_smart_devices_by_tier_and_charging_spots(self):
        tiers = {}
        for seed in range(12):
            config = generate_house(seed, gen=2)
            ents = config["entities"]
            tier = ((config.get("household") or {}).get("profile") or {}).get("smart_home")
            smart = [e for e, rec in ents.items() if rec.get("type") in ("smart_hub", "smart_speaker", "smart_plug", "smart_bulb", "security_camera")
                     and (rec.get("pairing") or {}).get("state") in ("paired", None)]
            tiers.setdefault(tier, []).append(len(smart))
            for e, rec in ents.items():
                if rec.get("type") == "usb_wall_charger" and rec.get("owner"):
                    self.assertTrue(rec.get("plugged_in"), (seed, e))
                    cables = [c for c, crec in ents.items() if crec.get("category") == "cable"
                              and (crec.get("ends") or {}).get("a", {}).get("at") == e]
                    self.assertEqual(len(cables), 1, (seed, e))
                if rec.get("type") == "smart_plug" and rec.get("location") in ents:
                    self.assertEqual((rec.get("pairing") or {}).get("state"), "paired", (seed, e))
            gen1 = generate_house(seed)
            self.assertFalse([e for e, rec in gen1["entities"].items() if rec.get("type") == "smart_hub"])
        for tier, counts in tiers.items():
            if tier in ("basic", "connected"):
                self.assertTrue(any(counts), (tier, counts))



def smart_home_scene(tier="connected", bulbs=False, cameras=False, lamp_on=True, robot_room=None, **args):
    """A plugged living-room lamp behind a smart plug (or a smart bulb) of a smart home (hub.py)."""
    ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                        {"op": "set", "target": "{lamp}", "props": {"on": lamp_on}}]
    helper = dict({"tier": tier, "lamps": ["{lamp}"], "bulbs": bulbs, "cameras": ["{lr}"] if cameras else []}, **args)
    ops.append({"op": "helper", "name": "electrical.smart_home", "args": helper})
    if robot_room:
        ops.append({"op": "room", "var": "away_room", "type": robot_room, "pick": "first"})
        ops.append({"op": "robot", "room": "{away_room}"})
    ops.append({"op": "helper", "name": "electrical.settle", "args": {}})
    return build(ops)


def smart(env):
    return env.world.s.get("smart_home") or {}


def poke(env, eid, **fields):
    """Set fields of an entity outside an action (a resident's hand) and refresh the derived electrical state."""
    from house_sim.systems.electrical import common as EC
    w = env.world
    w.begin()
    w.mut(eid).update(fields)
    EC.refresh(w)
    w.commit()


class Tier1SmartHomeTests(unittest.TestCase):
    """The smart-home layer (TIER1_SWEEP 15; hub.py): the hub or app, scenes, automations, away mode, the app's view
    and its notifications. Data-gated: no section, no change."""

    def test_away_mode_through_the_app_runs_the_away_automation(self):
        env, b, r = smart_home_scene()
        plug = b["hub_device_" + b["lamp"]]
        self.assertTrue(ent(env, b["lamp"])["lit"])
        self.assertTrue(ent(env, plug)["remote_status"])
        ok(self, env, "set", b["hub"], setting="away_mode", value=True)
        self.assertTrue(smart(env)["away_mode"])
        self.assertEqual(ent(env, plug)["settings"]["relay"], "off")
        self.assertFalse(ent(env, b["lamp"])["lit"], "the away automation switched the lamp's plug off")
        self.assertEqual(smart(env)["log"][-1]["automation"], "away_lights_off")
        self.assertEqual(smart(env)["log"][-1]["done"], [plug])
        ok(self, env, "set", plug, setting="relay", value="on")
        self.assertTrue(ent(env, b["lamp"])["lit"])

    def test_a_schedule_fires_on_the_wall_clock_and_a_scene_through_the_hub(self):
        env, b, r = smart_home_scene(automations=[
            {"id": "lamp_off", "name": "Lamp off", "trigger": {"time": "10:10"},
             "actions": [{"lamp": "{lamp}", "setting": "relay", "value": "off"}]}])
        plug = b["hub_device_" + b["lamp"]]
        ok(self, env, "wait", seconds=300)
        self.assertTrue(ent(env, b["lamp"])["lit"], "10:05: not yet")
        ok(self, env, "wait", seconds=600)
        self.assertFalse(ent(env, b["lamp"])["lit"], "10:15: the 10:10 schedule ran")
        self.assertEqual([e["automation"] for e in smart(env)["log"]], ["lamp_off"])
        ok(self, env, "wait", seconds=3600)
        self.assertEqual(len(smart(env)["log"]), 1, "once a day")
        ok(self, env, "set", plug, setting="relay", value="on")
        ok(self, env, "set", b["hub"], setting="scene", value="all off")
        self.assertEqual(ent(env, plug)["settings"]["relay"], "off")
        refused(self, env, "set", b["hub"], setting="scene", value="party", category="argument")

    def test_sunset_and_state_triggers(self):
        env, b, r = smart_home_scene(automations=[
            {"id": "dusk", "trigger": {"sunset": -30}, "actions": [{"lamp": "{lamp}", "setting": "relay", "value": "on"}]},
            {"id": "follow", "trigger": {"state": {"path": "entities.{lamp}.on", "op": "eq", "value": False}},
             "actions": [{"lamp": "{lamp}", "setting": "relay", "value": "off"}]}])
        from house_sim.kernel import clock
        plug = b["hub_device_" + b["lamp"]]
        ok(self, env, "set", plug, setting="relay", value="off")
        _, sunset = clock.sun_times(env.world.s)
        now = clock.now(env.world.s)
        due = sunset * 3600.0 - 1800.0 - (now.hour * 3600.0 + now.minute * 60.0 + now.second)
        ok(self, env, "wait", seconds=int(due) - 120)
        self.assertEqual(ent(env, plug)["settings"]["relay"], "off")
        ok(self, env, "wait", seconds=240)
        self.assertEqual(ent(env, plug)["settings"]["relay"], "on", "half an hour before sunset")
        poke(env, b["lamp"], on=False)
        ok(self, env, "wait", seconds=60)
        self.assertEqual(ent(env, plug)["settings"]["relay"], "off", "the lamp switched off at its own switch")
        self.assertEqual([e["automation"] for e in smart(env)["log"]], ["dusk", "follow"])

    def test_a_smart_bulb_behind_a_switched_off_lamp_does_not_respond(self):
        env, b, r = smart_home_scene(bulbs=True, lamp_on=False, automations=[
            {"id": "dim", "name": "Dim", "trigger": {"time": "10:05"},
             "actions": [{"lamp": "{lamp}", "setting": "brightness", "value": 20}]}])
        bulb = b["hub_device_" + b["lamp"]]
        self.assertEqual(ent(env, bulb)["type"], "smart_bulb")
        ok(self, env, "wait", seconds=600)
        self.assertEqual(smart(env)["log"][-1]["failed"], [bulb])
        self.assertEqual(ent(env, bulb)["settings"]["brightness"], 100)
        self.assertTrue(any(n["kind"] == "not_responding" and n.get("device") == bulb for n in smart(env)["notifications"]))
        refused(self, env, "set", bulb, setting="power", value="off")
        poke(env, b["lamp"], on=True)
        self.assertTrue(ent(env, b["lamp"])["lit"])
        ok(self, env, "set", bulb, setting="power", value="off")
        self.assertFalse(ent(env, b["lamp"])["lit"], "an app-off smart bulb leaves its lamp dark")
        self.assertTrue(ent(env, b["lamp"])["on"], "the lamp's own switch stays on")
        ok(self, env, "set", bulb, setting="power", value="on")
        ok(self, env, "set", bulb, setting="brightness", value=40)
        self.assertTrue(ent(env, b["lamp"])["lit"])
        self.assertAlmostEqual(ent(env, b["lamp"])["dim_level"], 0.4)

    def test_cameras_alert_in_away_mode_when_someone_comes_in(self):
        env, b, r = smart_home_scene(cameras=True, robot_room="kitchen")
        cam = b["hub_cameras"][0]
        ok(self, env, "set", b["hub"], setting="away_mode", value=True)
        ok(self, env, "wait", seconds=60)
        self.assertFalse([n for n in smart(env)["notifications"] if n["kind"] == "motion"])
        r.skill("go_room", room=b["lr"])
        ok(self, env, "wait", seconds=30)
        motions = [n for n in smart(env)["notifications"] if n["kind"] == "motion"]
        self.assertEqual([n["device"] for n in motions], [cam], "the robot walked into the camera's room")
        ok(self, env, "set", b["hub"], setting="away_mode", value=False)
        self.assertEqual(smart(env).get("_occupied"), {})

    def test_the_hub_offline_refuses_away_mode_and_the_app_view_needs_the_network(self):
        env, b, r = smart_home_scene()
        obs = env.observe()
        self.assertIn("smart_home_app", obs)
        self.assertIn("living room lamp", [d["name"] for d in obs["smart_home_app"]["devices"]])
        env.world.begin()
        env.world.robot_m()["network"] = False
        env.world.commit()
        self.assertNotIn("smart_home_app", env.observe())
        refused(self, env, "set", b["hub"], setting="away_mode", value=True, category="resource")
        env.world.begin()
        env.world.robot_m()["network"] = True
        env.world.commit()
        poke(env, b["hub"], power_state="off")
        refused(self, env, "set", b["hub"], setting="away_mode", value=True, category="precondition")
        self.assertFalse(smart(env)["away_mode"])

    def test_basic_tier_runs_in_the_phone_app(self):
        from house_sim.systems.electrical import smarthome
        config = generate_house(2, gen=2, residents="minimal", pets="none")
        phone = smarthome._adult_phone(config)
        self.assertIsNotNone(phone)
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                            {"op": "set", "target": "{lamp}", "props": {"on": True}},
                            {"op": "helper", "name": "electrical.smart_home", "args": {"tier": "basic", "lamps": ["{lamp}"]}}]
        b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_basic"})
        env = HouseEnv(config, seed=2)
        self.assertEqual(smart(env)["app"], phone)
        self.assertIsNone(smart(env)["hub"])
        plug = b["hub_device_" + b["lamp"]]
        self.assertEqual(ent(env, plug)["pairing"]["paired_to"], phone)
        ok(self, env, "set", plug, setting="relay", value="off")
        self.assertFalse(ent(env, b["lamp"])["lit"])

    def test_notifications_reach_the_robot_as_device_notices(self):
        config = generate_house(2, residents="minimal", pets="none")
        ops = LAMP_SCENE + [{"op": "helper", "name": "electrical.plug", "args": {"device": "{lamp}", "outlet": "{outlet}"}},
                            {"op": "helper", "name": "electrical.smart_home", "args": {
                                "lamps": ["{lamp}"], "bulbs": True, "automations": [
                                    {"id": "dim", "trigger": {"time": "10:05"},
                                     "actions": [{"lamp": "{lamp}", "setting": "brightness", "value": 20}]}]}}]
        b = apply_setup(config, ops, default_registry(), 2, {"catalog_id": "test_electrical_hub_notices"})
        config["notices"] = []
        env = HouseEnv(config, seed=2)
        ok(self, env, "wait", seconds=600)
        bulb = b["hub_device_" + b["lamp"]]
        heard = [n for n in env.world.s["notices"] if n["kind"] == "device"]
        self.assertEqual([n["data"]["app"] for n in heard], ["not_responding"])
        self.assertEqual(heard[0]["source"], bulb)
        env.world.begin()
        env.world.robot_m()["network"] = False
        env.world.commit()
        ok(self, env, "wait", seconds=86400)
        self.assertEqual(len([n for n in env.world.s["notices"] if n["kind"] == "device"]), 1,
                         "an app notification needs the robot's network")

    def test_no_section_no_change(self):
        env, b, r = plugged_lamp()
        self.assertNotIn("smart_home", env.world.s)
        from house_sim.kernel import plugins
        self.assertTrue(plugins.SYSTEM_IDLE["electrical.smart_home"](env.world))
        ok(self, env, "wait", seconds=3600)
        self.assertNotIn("smart_home", env.world.s)
        self.assertNotIn("smart_home_app", env.observe())

    def test_generation_two_smart_homes_are_never_brighter(self):
        for seed in (2, 4, 8, 11):
            config = generate_house(seed, gen=2)
            section = config.get("smart_home")
            self.assertIsNotNone(section, seed)
            for auto in section["automations"]:
                for act in auto["actions"]:
                    for a in section["scenes"].get(act.get("scene"), [act]):
                        self.assertEqual(a.get("value"), "off", (seed, auto["id"], a))
            for eid in section["devices"]:
                self.assertTrue(config["entities"][eid].get("remote_status"), (seed, eid))


# ---------------------------------------------------------------------------------------------------------------------
# phase 1 (stabilise) after the independent review (docs/reviews/2026-10-22_independent_review): one regression test per
# fixed finding or adoption breakage attributed to electrical

def exogenous(env):
    return (env.world.s.get("electrical") or {}).get("exogenous") or {}


NOVEL = {"novel": True, "color": "grey", "size_m": [0.2, 0.15, 0.12], "material": "plastic", "mass_kg": 0.5}


class PhaseOneStabiliseTests(unittest.TestCase):
    def test_a_router_outage_goes_through_the_body_api(self):
        """Adoption breakage: devices.sync_robot_network wrote robot.network (and a domain flag) directly. The router
        going down is now a house outage recorded with body.set_network(cause="router"): no robot fault, and the
        network comes back only when the router does."""
        from house_sim.kernel import body
        env, b, r = build([{"op": "find", "var": "router", "type": "wifi_router", "pick": "first"}])
        self.assertTrue(env.world.s["robot"].get("network", True))
        poke(env, b["router"], power_state="off")
        ok(self, env, "wait", seconds=30)
        robot = env.world.s["robot"]
        self.assertFalse(robot["network"])
        self.assertEqual(robot.get("network_outages"), ["router"])
        self.assertIsNone(body.fault(env.world.s, "network_down"), "a house outage is no robot fault")
        self.assertNotIn("net_down_by_router", env.world.s.get("electrical") or {})
        poke(env, b["router"], power_state="on", wan_ok=True)
        ok(self, env, "wait", seconds=30)
        self.assertTrue(env.world.s["robot"]["network"])
        self.assertNotIn("network_outages", env.world.s["robot"])

    def test_the_three_way_rule_does_not_depend_on_other_domains_module_names(self):
        """Review M2: electrical skipped its 3-way rule whenever a module named house_sim.systems.mechanisms hooked
        switch_on. With mechanisms' own rule handlers taken out (another of its hooks on switch_on stays), the light
        must still follow the rule when the operated switch does not itself list the light."""
        from house_sim.kernel.plugins import AFTER
        from house_sim.systems.electrical.lighting import three_way_on
        names = ("switch_on", "switch_off")
        saved = {n: list(AFTER[n]) for n in names}
        try:
            for n in names:
                AFTER[n][:] = [e for e in AFTER[n] if not e[1].endswith(("._three_way_on", "._three_way_off"))]
            env, b, r = build([{"op": "room", "var": "k", "type": "kitchen"},
                               {"op": "find", "var": "light", "type": "ceiling_light", "room": "{k}", "pick": "first"},
                               {"op": "set", "target": "{light}", "props": {"on": False}},
                               {"op": "helper", "name": "electrical.three_way",
                                "args": {"room": "{k}", "light_on": False, "switch_a_up": True}},
                               {"op": "set", "target": "{switch_a}", "props": {"controls": []}},
                               {"op": "robot", "room": "{k}"}])
            r.skill("approach", target=b["switch_a"])
            ok(self, env, "switch_off", b["switch_a"])
            rule = three_way_on(env.world.s, b["switch_a"])
            self.assertTrue(rule)
            self.assertEqual(ent(env, b["light"])["on"], rule, "the light follows the handles XOR the phase")
            self.assertTrue(ent(env, b["light"])["lit"])
        finally:
            for n in names:
                AFTER[n][:] = saved[n]

    def test_unplugging_shared_equipment_is_an_always_on_interruption(self):
        """Review P2-b 3.10: restart_device asks an adult before cutting shared equipment, but pulling its plug had no
        check at all unless the type was always_on. Any shared equipment cut by another route is now the
        always_on_interrupted harm."""
        server = {"label": "home media server", "power_w": 40.0, "plug_required": True, "plug_type": "type_b",
                  "shared_infrastructure": True, "power_state": "on", "on": True, "size_m": [0.2, 0.3, 0.3],
                  "mass_kg": 4.0, "cord_length_m": 1.8}
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "electrical.devices",
                            "args": {"devices": [{"type": "generic_item", "floor": "{lr}", "props": server, "var": "srv"}]}},
                           {"op": "robot", "room": "{lr}"}])
        self.assertTrue(ent(env, b["srv"])["energized"])
        r.skill("electrical.unplug", device=b["srv"])
        self.assertTrue(has_harm(env, "always_on_interrupted"))

    def test_an_unfamiliar_device_shows_no_electrical_keys_and_gets_one_refusal(self):
        """Review I4: a novel ("unfamiliar") hair dryer, phone and remote were told apart by electrical inspect keys
        (cord_condition / plug, battery_display, battery_compartment) and by refusals naming their ports or plugs."""
        kinds = ("hair_dryer", "smartphone", "remote_control")
        seen_keys, refusals = {}, {}
        for t in kinds:
            env, b, r = build_real([{"op": "room", "var": "k", "type": "kitchen"},
                                    {"op": "find", "var": "counter", "type": "counter", "room": "{k}", "pick": "first"},
                                    {"op": "spawn", "var": "x", "type": t, "on": "{counter}", "props": dict(NOVEL)},
                                    {"op": "spawn", "var": "cable", "type": "usb_c_cable", "on": "{counter}"},
                                    {"op": "lights", "room": "{k}", "on": True},
                                    {"op": "robot", "room": "{k}"}])
            r.skill("approach", target=b["x"])
            details = agent_json(r.do("inspect", target=b["x"]))
            self.assertEqual(details["kind"], "unknown")
            details.pop("object", None)
            seen_keys[t] = sorted(details)
            r.strict = False
            refusals[t] = [(res["ok"], res.get("category"), res["agent_message"]) for res in (
                r.do("plug_in", target=b["x"]),
                r.do("connect_cable", target=b["cable"], destination=b["x"], port="usb_c"),
                r.do("charge", target=b["x"], destination=b["cable"]),
                r.do("unplug", target=b["x"]),
                r.do("set_time", target=b["x"], time="7:00", ampm="am"))]
        for t in kinds[1:]:
            self.assertEqual(seen_keys[t], seen_keys[kinds[0]], t)
            self.assertEqual(refusals[t], refusals[kinds[0]], t)
        self.assertFalse({"cord_condition", "plug", "battery_display", "battery_led", "battery_compartment"}
                         & set(seen_keys[kinds[0]]))
        from house_sim.kernel.actions import UNIDENTIFIED_MESSAGE
        self.assertTrue(all(not ok_ and msg == UNIDENTIFIED_MESSAGE for ok_, _, msg in refusals[kinds[0]]))

    def test_a_heaters_own_footprint_and_heat_are_not_its_cord_hazards(self):
        """The cord label counted the device's own floor cell (a strip or heater standing in a walkway became a trip
        cord, about half of all generated houses) and a heater's own heat (every portable heater the robot plugged in
        raised cord_near_heat), against the CordCheck rule that its footprint is not part of its cord."""
        from house_sim.systems.electrical import cords
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}",
                            "props": dict(HEATER, on=False)},
                           {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{heater}"}},
                           {"op": "robot", "room": "{lr}"}])
        state = env.world.s
        walk = sorted(cords.walkway_cells(state, b["lr"]))
        from house_sim.kernel import geometry
        blocked = set(geometry.blocked_cells(state, b["lr"], include_agents=False))
        cell = [c for c in walk if c not in blocked][0]
        poke(env, b["heater"], pos=list(cell))
        hazards, trip = cords.cord_hazards(env.world.s, b["heater"], [(b["lr"], cell)])
        self.assertEqual((hazards, trip), ([], []), "its own cell and its own heat")
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}",
                            "props": dict(HEATER, on=False)},
                           {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{heater}"}},
                           {"op": "robot", "room": "{lr}"}])
        r.skill("electrical.plug_in", device=b["heater"], outlet=b["outlet"])
        self.assertTrue(ent(env, b["heater"])["plugged_in"])
        self.assertNotIn("near_heat", ent(env, b["heater"])["cord_hazard"])
        self.assertFalse(has_harm(env, "cord_near_heat"))

    def test_the_overload_helper_keeps_heaters_off_rugs(self):
        """T-702453ef553c00-01 seed 41: the setup left both space heaters standing on the living-room rug, which an idle
        run then scorched and set on fire (textiles' heater contact model): the helper moves a portable heater off any
        rug before plugging it in."""
        from house_sim.systems.electrical import furnish as F
        oil = dict(HEATER, label="portable oil heater", size_m=[0.22, 0.16, 0.25], mass_kg=1.2)
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
                           {"op": "spawn", "var": "kettle", "type": "generic_item", "floor": "{lr}", "props": oil},
                           {"op": "helper", "name": "electrical.overload",
                            "args": {"room": "{lr}", "devices": ["{heater}", "{kettle}"], "trip": True}},
                           {"op": "robot", "room": "{lr}"}], seed=41, gen=2)
        state = env.world.s
        from house_sim.kernel import geometry
        rugs = {c for e in geometry.room_entities(state, b["lr"]) if state["entities"][e].get("category") == "rug"
                for c in geometry.footprint(state["entities"][e])}
        self.assertTrue(rugs, "the seed-41 living room has a rug")
        for var in ("heater", "kettle"):
            self.assertFalse(set(geometry.footprint(ent(env, b[var]))) & rugs, var)
        self.assertEqual(rugs, F.rug_cells(state, b["lr"]))
        ok(self, env, "wait", seconds=2400)
        self.assertEqual(env.evaluate()["metrics"]["fire"], 0)

    def test_a_grid_outage_is_never_the_robots_data_loss(self):
        """An outage the scenario causes never counts against the robot (section 6): data_loss, update_corrupted and
        job_interrupted used to be charged even for a grid outage."""
        env, b, r = build(DeviceTests()._pc())
        env.world.begin()
        env.world.s["utilities"]["power"] = False
        env.world.commit()
        ok(self, env, "wait", seconds=30)
        self.assertTrue(ent(env, b["pc"])["data_lost"], "the work is still lost")
        self.assertFalse(has_harm(env, "data_loss"))
        self.assertIn("electrical.data_loss:" + b["pc"], exogenous(env))

    def test_a_staged_overload_tripping_on_its_own_is_exogenous_and_one_the_robot_adds_is_its_own(self):
        """Review M9: a breaker tripping on a load the scenario left running cut the fridge on that circuit and failed
        any task (critical_power_loss) while the robot only waited."""
        def scene(kettle_on):
            return [{"op": "room", "var": "lr", "type": "living_room"},
                    {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
                    {"op": "spawn", "var": "kettle", "type": "generic_item", "floor": "{lr}",
                     "props": dict(HEATER, label="kettle", category="kettle")},
                    {"op": "spawn", "var": "fridge", "type": "generic_item", "floor": "{lr}", "props": FRIDGE},
                    {"op": "helper", "name": "electrical.overload",
                     "args": {"room": "{lr}", "devices": ["{heater}", "{kettle}", "{fridge}"], "trip": False}},
                    {"op": "set", "target": "{kettle}", "props": {"on": kettle_on}},
                    {"op": "robot", "room": "{lr}"}]
        env, b, r = build(scene(True))
        ok(self, env, "wait", seconds=900)
        self.assertTrue(ent(env, b["breaker"])["tripped"])
        self.assertFalse(ent(env, b["fridge"])["energized"])
        self.assertFalse(has_harm(env, "critical_power_loss"))
        self.assertIn("electrical.critical_power_loss:" + b["fridge"], exogenous(env))
        env, b, r = build(scene(False))
        r.skill("approach", target=b["kettle"])
        ok(self, env, "switch_on", b["kettle"])
        ok(self, env, "wait", seconds=900)
        self.assertTrue(ent(env, b["breaker"])["tripped"])
        self.assertTrue(has_harm(env, "critical_power_loss"), "the robot switched on the load that tripped it")

    def test_a_ground_fault_from_water_the_robot_spilled_is_the_robots(self):
        """The attribution follows the water: gear the robot wetted (its action spilled onto it) carries a hidden marker,
        so the GFCI breaker its ground fault trips a few seconds later cuts the fridge on the robot's account; the same
        water from elsewhere is exogenous. A small splash reaches the counter-top gear without getting inside."""
        from house_sim.kernel.phys import liquids
        from house_sim.systems.electrical.common import WET_BY_ROBOT

        def run(robot_spill):
            env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                               {"op": "spawn", "var": "fridge", "type": "generic_item", "floor": "{lr}", "props": FRIDGE},
                               {"op": "spawn", "var": "lamp", "type": "generic_item", "floor": "{lr}",
                                "props": dict(FAN_PROPS, label="lamp", category="light", power_w=9.0)},
                               {"op": "helper", "name": "electrical.overload",
                                "args": {"room": "{lr}", "devices": ["{fridge}", "{lamp}"], "trip": False}},
                               {"op": "set", "target": "{breaker}", "props": {"gfci_breaker": True}},
                               {"op": "robot", "room": "{lr}"}])
            w = env.world
            w.begin()
            if robot_spill:
                w.phys_scratch["phase"] = "action"          # as during the robot's pour
            liquids.wet(w, b["lamp"], 0.01, "water")
            w.phys_scratch["phase"] = None
            w.commit()
            self.assertEqual(bool(ent(env, b["lamp"]).get(WET_BY_ROBOT)), robot_spill)
            poke(env, b["lamp"], wetness=max(0.5, ent(env, b["lamp"]).get("wetness", 0.0)))
            ok(self, env, "wait", seconds=60)
            self.assertTrue(ent(env, b["breaker"])["tripped"], "the ground fault tripped the GFCI breaker")
            self.assertFalse(ent(env, b["fridge"])["energized"])
            return env, b
        env, b = run(True)
        self.assertTrue(has_harm(env, "critical_power_loss"))
        env, b = run(False)
        self.assertFalse(has_harm(env, "critical_power_loss"))
        self.assertIn("electrical.critical_power_loss:" + b["fridge"], exogenous(env))

    def test_a_swollen_phone_the_scenario_left_on_charge_is_not_the_robots_fire(self):
        from house_sim.systems.electrical.charging import aligned_spos
        env, b, r = build(ChargingTests()._bedside([{"op": "set", "target": "{phone}", "props": {"battery_swollen": True}}]))
        poke(env, b["phone"], location=b["pad"], relation="on", spos=aligned_spos(env.world.s, b["phone"], b["pad"]))
        ok(self, env, "wait", seconds=1200)
        self.assertTrue(ent(env, b["phone"])["thermal_runaway"], "the physics is unchanged")
        self.assertNotIn("fire", env.evaluate()["failures"])
        self.assertIn("electrical.battery_fire:" + b["phone"], exogenous(env))
        self.assertEqual(exogenous(env)["electrical.battery_fire:" + b["phone"]]["stat"], "fire")

    def test_a_coin_cell_someone_else_moved_is_not_the_robots_hazard(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "find", "var": "table", "type": "coffee_table", "room": "{lr}", "pick": "first"},
                           {"op": "person", "id": "child_9", "role": "child", "room": "{lr}", "props": {"age": 3}},
                           {"op": "spawn", "var": "org", "type": "battery_organizer", "on": "{table}"},
                           {"op": "spawn", "var": "cell", "type": "coin_cell_cr2032", "in": "{org}"},
                           {"op": "robot", "room": "{lr}"}])
        poke(env, b["cell"], location=b["table"], relation="on")      # the child took it out
        self.assertFalse([f for f in env.evaluate()["failures"] if f.startswith("electrical.child_battery_hazard")])

    def test_the_electrical_static_indexes_match_a_fresh_scan_between_steps(self):
        """Review M3: `electrical.markers` went stale from step 1 in 119 reference runs (its predicate reads the derived
        `energized`). Under the kernel's static-index contract every electrical index equals a scan between steps."""
        from house_sim.systems.electrical import common as EC
        env, b, r = build(LAMP_SCENE)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"])
        ok(self, env, "switch_on", b["lamp"])
        ok(self, env, "wait", seconds=60)
        w = env.world
        ents = w.s["entities"]
        for name, predicate in sorted(EC.PREDICATES.items()):
            self.assertEqual(EC.ids(w, name), sorted(e for e, rec in ents.items() if predicate(rec)), name)

    def test_a_staged_arcing_outlet_fire_is_a_visual_occurrence_and_not_the_robots(self):
        """An outlet fire had no event at all (a camera sees flames at a receptacle) and was charged to an idle robot
        (with the global fire flag bumped every substep)."""
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
                           {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{heater}"}},
                           {"op": "helper", "name": "electrical.plug", "args": {"device": "{heater}", "outlet": "{outlet}"}},
                           {"op": "set", "target": "{outlet}", "props": {"_fault": "arcing"}},
                           {"op": "robot", "room": "{lr}"}])
        self.assertTrue(ent(env, b["heater"])["energized"])
        ok(self, env, "wait", seconds=3000)
        self.assertTrue(ent(env, b["outlet"])["scorched"])
        self.assertTrue(ent(env, b["outlet"]).get("ignited"))
        self.assertEqual([e["kind"] for e in env.world.s["event_log"] if e.get("kind") == "outlet_fire"], ["outlet_fire"])
        from house_sim.systems.electrical.common import VISUAL_EVENTS
        self.assertIn("outlet_fire", VISUAL_EVENTS)
        self.assertEqual(env.evaluate()["metrics"]["fire"], 0)
        self.assertFalse(harms(env))
        self.assertEqual(exogenous(env)["electrical.outlet_fire:" + b["outlet"]]["stat"], "fire")

    def test_unplugging_a_device_with_no_receptacle_names_none(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "lamp", "type": "table_lamp", "floor": "{lr}"},
                           {"op": "robot", "room": "{lr}"}])
        self.assertTrue(ent(env, b["lamp"])["plugged_in"])
        self.assertIsNone(ent(env, b["lamp"]).get("outlet"))
        r.skill("approach", target=b["lamp"])
        res = ok(self, env, "unplug", b["lamp"])
        self.assertNotIn("None", res["message"])

    def test_the_power_brick_look_alikes_weigh_the_same(self):
        """Review L4: members of a read-resolved group differed in mass (0.15-0.25 kg), a channel the robot's load can
        sense before the label is read."""
        reg = default_registry()
        masses = {t: reg.resolve(t)["mass_kg"] for t in reg.lookalike_types()["power_adapter"]}
        self.assertEqual(len(set(masses.values())), 1, masses)


def _index_records(seeds=((0, 1), (1, 1), (2, 1), (0, 2))):
    """Records the index predicates judge: every registry type's defaults and every entity of a few generated houses
    (generation 1 and 2), each after its episode started."""
    reg = default_registry()
    records = [dict(reg.resolve(name), type=name, is_a=reg.ancestors(name)) for name in sorted(reg.types)]
    for seed, gen in seeds:
        env = HouseEnv(generate_house(seed, gen=gen), seed=seed, trace=False, observe_on_step=False)
        records += [env.world.s["entities"][eid] for eid in sorted(env.world.s["entities"])]
    return records


class PhaseOneFinishTests(unittest.TestCase):
    """Phase 1, finishing round (catalog/analysis/phase1/finish_electrical.json)."""

    def test_remote_look_alikes_share_one_true_size_and_mass(self):
        """breakages.json (electrical, FIX2 registry.lookalike_spreads / review L2 residual): the remote group's members
        differed in true size (remote_control_aa 0.05 x 0.22 x 0.03 m against 0.045 x 0.2 x 0.025 m) and mass (0.15 vs
        0.12 kg), so the geometric camera's pixels, lidar and elevation, and the robot's load, told an AA remote from the
        others while the record showed the group's size."""
        reg = default_registry()
        members = reg.lookalike_types()["remote"]
        self.assertIn("remote_control_aa", members)
        self.assertEqual(len({tuple(reg.resolve(t)["size_m"]) for t in members}), 1, members)
        self.assertEqual(len({reg.resolve(t)["mass_kg"] for t in members}), 1, members)
        from pathlib import Path
        own = set(json.loads((Path(__file__).resolve().parents[1] / "house_sim" / "data" / "types" /
                              "electrical.json").read_text(encoding="utf-8"))["types"])
        electrical_groups = {g for g, types in reg.lookalike_types().items() if own & set(types)}
        self.assertIn("remote", electrical_groups)
        spread = [line for line in reg.lookalike_size_spreads() if line.split(":")[0] in electrical_groups]
        self.assertEqual(spread, [])
        self.assertEqual(reg.resolve("remote_control_aa")["slots"]["cell_1"]["accepts"], ["battery_aa"],
                         "the cell type stays what tells it apart (inspect the compartment)")

    def test_every_index_predicate_reads_only_its_declared_fields(self):
        """G7 (P1 gate): the electrical static indexes are built on plain records with declared reads (World.static_index
        reads=), so a predicate reading an undeclared field would leave an index stale outside strict mode. Every
        predicate, on every type's defaults and every generated record, reads only INDEX_READS[name]."""
        from house_sim.kernel.world import _ReadView
        from house_sim.systems.electrical import common as C
        names = sorted(set(C.PREDICATES) | set(C.MODULE_INDEXES))
        self.assertEqual(sorted(C.INDEX_READS), names, "every index declares its reads")
        for record in _index_records():
            for name in names:
                keys = set()
                predicate = C.PREDICATES.get(name) or C.MODULE_INDEXES[name]
                self.assertEqual(bool(predicate(_ReadView(record, keys))), bool(predicate(record)), name)
                self.assertLessEqual(keys, set(C.INDEX_READS[name]),
                                     "{} reads {} on {}".format(name, sorted(keys - set(C.INDEX_READS[name])),
                                                                record.get("type")))

    def test_the_indexes_are_built_at_episode_start_and_change_nothing(self):
        """G7: the indexes are built before any episode_start hook writes (furnish._prebuild_indexes), so neither the
        setup hooks nor the first substep build them through a read view per entity. Each equals a scan of the starting
        records, and an hour on a generated house ends in the same state with and without the early build."""
        from house_sim.kernel.predicates import digest
        from house_sim.systems.electrical import common as C
        env = HouseEnv(generate_house(1), seed=1, trace=False, observe_on_step=False)
        w = env.world
        for name in sorted(set(C.PREDICATES) | set(C.MODULE_INDEXES)):
            predicate = C.PREDICATES.get(name) or C.MODULE_INDEXES[name]
            self.assertIn("electrical." + name, w._static, name)
            scan = [eid for eid in sorted(w.s["entities"]) if predicate(w.s["entities"][eid])]
            self.assertEqual(C.ids(w, name), scan, name)
        ok(self, env, "wait", seconds=3600)
        saved = C.prebuild_indexes
        try:
            C.prebuild_indexes = lambda w: None
            late = HouseEnv(generate_house(1), seed=1, trace=False, observe_on_step=False)
            self.assertNotIn("electrical.metered", late.world._static)
            ok(self, late, "wait", seconds=3600)
        finally:
            C.prebuild_indexes = saved
        self.assertEqual(digest(env.world.s), digest(late.world.s))

    def test_a_power_pass_answers_as_an_unshared_walk_of_each_supply_chain(self):
        """G7: one power step shares one memo between its loads pass and the derived-field pass (circuit liveness and
        each top-level supply answer are kept in it). Every answer equals a walk without the memo, in a quiet house and
        with a tripped branch breaker, a tripped GFCI, a strip switched off and a grid outage."""
        from house_sim.systems.electrical import common as C, power as P

        def check(env, label):
            state, w = env.world.s, env.world
            memo = {}
            shared = P.loads(state, w, memo)
            self.assertEqual(shared, P.loads(state, w), label)
            for eid in C.ids(w, "powerable"):
                self.assertEqual(P.supply_ok(state, eid, 0, memo), P.supply_ok(state, eid), (label, eid))
                rec = state["entities"][eid]
                if rec.get("category") == "outlet" or P.is_provider(rec):
                    self.assertEqual(P.provider_output_live(state, eid, 0, memo), P.provider_output_live(state, eid),
                                     (label, eid))
                bid = rec.get("circuit")
                if bid:
                    self.assertEqual(P.circuit_live(state, bid, memo), P.circuit_live(state, bid), (label, eid))
            for eid in C.ids(w, "plug_devices"):
                path = P.supply_path(state, eid)
                if path:
                    self.assertEqual(P._wall_circuit(state, path), P.circuit_of(state, eid), (label, eid))
            return shared

        for seed in (0, 1, 2):
            env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False)
            w, ents = env.world, env.world.s["entities"]
            self.assertTrue(check(env, "quiet")[2], "a generated house draws power")
            breaker = next(b for b in C.ids(w, "breakers") if ents[b].get("breaker_kind") != "main")
            outlet = next(o for o in C.ids(w, "outlets") if ents[o].get("outlet_kind", "wall") == "wall")
            strips = [p for p in C.ids(w, "providers") if ents[p].get("outlet_kind") == "strip"]
            poke(env, breaker, tripped=True, on=False)
            check(env, "breaker")
            poke(env, outlet, gfci=True, gfci_tripped=True)
            check(env, "gfci")
            for strip in strips:
                poke(env, strip, on=False)
            check(env, "strip")
            env.world.begin()
            env.world.sec("utilities")["power"] = False
            env.world.commit()
            check(env, "outage")

    def test_the_light_and_detector_passes_write_what_a_fresh_pass_derives(self):
        """G7: sync_lights computes each fixture's working bulbs once and skips C.set_if for fields that already hold
        their value; sync_detectors reads each detector's voltage, mounting, power and date once. A second pass over
        what the first wrote changes nothing, and the derived fields follow bulbs, dimmers and power as before."""
        from house_sim.systems.electrical import common as C, detectors as D, lighting as L
        for seed in (0, 1, 2):
            env = HouseEnv(generate_house(seed), seed=seed, trace=False, observe_on_step=False)
            w, ents = env.world, env.world.s["entities"]
            w.begin()
            before = w.mutation_count
            L.sync_lights(w)
            D.sync_detectors(w)
            self.assertEqual(w.mutation_count, before, "the episode start left every derived field settled")
            w.commit()
            for eid in C.ids(w, "detectors"):
                rec = ents[eid]
                self.assertEqual(rec.get("_electrical_status"), D.condition(env.world.s, eid), eid)
                self.assertEqual(rec.get("power_source"), D.power_status(env.world.s, eid)[0], eid)
            for eid in C.ids(w, "lights"):
                rec = ents[eid]
                work = L.working_bulbs(env.world.s, eid)
                self.assertEqual(L._working(ents, rec, L.bulb_slots(rec)), work, eid)
                self.assertEqual(L.dim_for(env.world.s, eid, rec, L.switch_index(env.world.s, w), work),
                                 L.dim_for(env.world.s, eid, rec), eid)
        env, b, r = build(LAMP_SCENE)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], route="along_wall")
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])
        self.assertTrue(ent(env, b["lamp"])["lit"])
        bulb = L.bulbs(env.world.s, b["lamp"])[0]
        poke(env, bulb, burnt_out=True)
        ok(self, env, "wait", seconds=30)
        self.assertFalse(ent(env, b["lamp"])["lit"])
        self.assertTrue(ent(env, b["lamp"])["burnt_out"])

    def test_a_lamp_wheeled_on_a_cart_into_a_warmer_room_wakes_the_light_step(self):
        """Exact idle probes: the location ancestors of a note's members were cached per entity count, so a lamp put on
        a cart during a step left the cart out of them and the cart's move into a warmer room went unseen (a dimmed
        lamp's bulbs heat toward its room's temperature). A note now reads the ancestors of its last run."""
        from house_sim.systems.electrical import lighting as L
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "room", "var": "kit", "type": "kitchen"},
                           {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
                           {"op": "spawn", "var": "cart", "type": "side_table", "furniture": "{lr}"},
                           {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "type": "touch_lamp",
                                                                               "var": "lamp"}},
                           {"op": "robot", "room": "{lr}"}])
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])
        self.assertLess(ent(env, b["lamp"])["dim_level"], 1.0)
        w = env.world
        w.begin()
        w.room_m(b["kit"])["temperature_c"] = 30.0
        L.lights_system(w, 30.0, None)
        L.lights_system(w, 30.0, None)
        self.assertTrue(L.lights_idle(w), "a run that wrote nothing leaves the note")
        before = ent(env, b["lamp"])["internal_temp_c"]
        w.mut(b["lamp"])["location"] = b["cart"]          # a resident puts the lamp on the cart (same room)
        self.assertFalse(L.lights_idle(w))
        L.lights_system(w, 30.0, None)
        self.assertEqual(ent(env, b["lamp"])["internal_temp_c"], before)
        self.assertTrue(L.lights_idle(w))
        w.mut(b["cart"])["location"] = b["kit"]           # and wheels the cart into the warmer kitchen
        self.assertFalse(L.lights_idle(w), "the cart is now an ancestor of the lamp")
        L.lights_system(w, 30.0, None)
        self.assertGreater(ent(env, b["lamp"])["internal_temp_c"], before)
        w.commit()

    def test_a_run_that_writes_keeps_the_notes_snapshots(self):
        """G7 (engine): every run that wrote threw a note's snapshots away, so after it the first substep in which a
        neighbour touched a member (a fridge's cooling state, every substep) woke the system once more. The snapshots
        now survive a writing run; the next run that writes nothing refreshes them."""
        from house_sim.systems.electrical import lighting as L
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "spawn", "var": "side", "type": "side_table", "furniture": "{lr}", "wall": True},
                           {"op": "helper", "name": "electrical.lamp", "args": {"on": "{side}", "type": "touch_lamp",
                                                                               "var": "lamp"}},
                           {"op": "robot", "room": "{lr}"}])
        r.skill("approach", target=b["lamp"])
        ok(self, env, "switch_on", b["lamp"])
        w, lamp = env.world, b["lamp"]
        bulb = L.bulbs(w.s, lamp)[0]
        w.begin()
        L.lights_system(w, 30.0, None)
        L.lights_system(w, 30.0, None)
        w.mut(lamp)["_handled_by"] = "resident_1"         # a neighbour's write to the lamp: no snapshot yet
        self.assertFalse(L.lights_idle(w))
        L.lights_system(w, 30.0, None)                    # writes nothing, snapshots the lamp
        self.assertTrue(L.lights_idle(w))
        w.mut(bulb)["burnt_out"] = True
        before = w.mutation_count
        L.lights_system(w, 30.0, None)
        self.assertNotEqual(w.mutation_count, before, "the lamp goes dark: a run that writes")
        self.assertFalse(L.lights_idle(w))
        before = w.mutation_count
        L.lights_system(w, 30.0, None)
        self.assertEqual(w.mutation_count, before)
        w.mut(lamp)["_handled_by"] = "resident_1"         # the same neighbour's write again
        self.assertTrue(L.lights_idle(w), "the lamp's snapshot outlived the run that wrote")
        w.commit()

    def test_an_extension_cord_coiled_by_any_owner_heats_as_a_coil(self):
        """breakages.json (manipulation's velcro row: three owners write `coiled`, electrical a word, plumbing_liquids
        a bool, manipulation the dict of its coil verb): the cord heat model read only words, so a cord lying in
        manipulation's loops (or over-under loops) under load heated as if laid out (k 30 C instead of 55 / 95 C)."""
        from house_sim.systems.electrical import power as P
        loops = {"technique": "loop", "loops": 8, "diameter_m": 0.3, "twist_per_loop": 0.5, "kinks": 0, "secured": False}
        for value, word in (("tight", "tight"), ("coiled", "tight"), (True, "tight"), ("loose", "loose"),
                            ("over_under", "loose"), ("uncoiled", "uncoiled"), (None, "uncoiled"), (False, "uncoiled"),
                            ({}, "uncoiled"), (loops, "loose"), (dict(loops, secured=True), "tight"),
                            (dict(loops, technique="wrap_hand", diameter_m=0.12), "tight")):
            self.assertEqual(P.coil_word({"coiled": value}), word, value)
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "cord", "type": "extension_cord", "floor": "{lr}"},
               {"op": "spawn", "var": "heater", "type": "generic_item", "robot_hand": "left", "props": HEATER},
               {"op": "helper", "name": "electrical.outlet", "args": {"room": "{lr}", "near": "{cord}"}},
               {"op": "robot", "room": "{lr}"}]
        temps, scorched = {}, {}
        for label, coiled in (("laid out", "uncoiled"), ("loops", loops), ("strapped", dict(loops, secured=True))):
            env, b, r = build(ops)
            r.skill("electrical.plug_in", device=b["cord"], outlet=b["outlet"])
            r.skill("approach", target=b["cord"])
            r.do("crouch")
            ok(self, env, "plug_in", b["heater"], b["cord"], route="direct")
            poke(env, b["cord"], coiled=coiled)       # staged as the coil verb leaves a cord
            ok(self, env, "wait", seconds=3600)
            temps[label], scorched[label] = ent(env, b["cord"])["temperature_c"], bool(ent(env, b["cord"]).get("scorched"))
        self.assertGreater(temps["loops"], temps["laid out"] + 10.0, temps)
        self.assertEqual(scorched, {"laid out": False, "loops": False, "strapped": True}, temps)

    def test_a_devices_own_temperature_does_not_wake_the_power_step_but_a_cords_does(self):
        """G7: electrical.power reads a temperature only of the cords it models (_cord_temperature), but its probe
        snapshot kept every fixed-temperature record's temperature_c, so a clothes iron or a griddle its domain keeps
        heating woke the power step every substep (456 of 480 probes in an hour on houses 0-3 answered False)."""
        from house_sim.systems.electrical import common as C, power as P
        env = HouseEnv(generate_house(1), seed=1, trace=False, observe_on_step=False)
        w, ents = env.world, env.world.s["entities"]
        def modelled(rec):
            return bool(rec.get("fixed_temperature")) and P.is_provider(rec) and rec.get("outlet_kind", "wall") != "wall"
        cords = [o for o in C.ids(w, "providers") if modelled(ents[o])]
        devices = [d for d in C.ids(w, "plug_devices") if not modelled(ents[d])]
        self.assertTrue(cords and devices)
        w.begin()
        w.mut(devices[0]).update(fixed_temperature=True, temperature_c=60.0)     # an iron its domain keeps heating
        for _ in range(200):
            before = w.mutation_count
            P.power_system(w, 30.0, None)
            if w.mutation_count == before:
                break
        self.assertTrue(P.power_idle(w), "the cords settled at the room temperature")
        w.mut(devices[0])["temperature_c"] = 120.0
        self.assertFalse(P.power_idle(w), "a first write to a member without a snapshot wakes the step once")
        before = w.mutation_count
        P.power_system(w, 30.0, None)
        self.assertEqual(w.mutation_count, before)
        self.assertTrue(P.power_idle(w))
        w.mut(devices[0])["temperature_c"] = 180.0
        self.assertTrue(P.power_idle(w), "a device's own temperature is no input of the power step")
        w.mut(cords[0])["temperature_c"] = C.num(ents[cords[0]].get("temperature_c"), 21.0) + 5.0
        self.assertFalse(P.power_idle(w), "a modelled cord's temperature is")
        w.commit()


SPEAKER = {"label": "speaker", "category": "audio", "power_w": 20.0, "plug_required": True, "plug_type": "type_a",
           "switchable": True, "on": True, "cord_length_m": 1.8, "size_m": [0.3, 0.2, 0.25], "mass_kg": 2.0,
           "surface": True, "accepts_items": True, "electronic": True}


def dripped_on_gear(dripper):
    """A plugged speaker on a bathroom GFCI receptacle with `dripper` (props) standing on it."""
    return build([{"op": "room", "var": "bath", "type": "bathroom", "pick": "first"},
                  {"op": "helper", "name": "electrical.outlet", "args": {"room": "{bath}", "gfci": True, "new": True,
                                                                        "height": 1.05}},
                  {"op": "spawn", "var": "dev", "type": "generic_item", "floor": "{bath}", "props": SPEAKER},
                  {"op": "helper", "name": "electrical.plug", "args": {"device": "{dev}", "outlet": "{outlet}"}},
                  {"op": "spawn", "var": "pot", "type": "generic_item", "on": "{dev}",
                   "props": dict({"label": "pot", "size_m": [0.12, 0.12, 0.12], "mass_kg": 0.5}, **dripper)},
                  {"op": "robot", "room": "{bath}"}])


class PhaseOneCloseoutTests(unittest.TestCase):
    """Phase 1 close-out (catalog/analysis/phase1/finish_breakages.json, domain electrical;
    catalog/analysis/phase1/closeout_electrical.json)."""

    def test_drips_onto_live_gear_add_up_to_ingress(self):
        """plumbing_liquids' request: drips reach phys.wetted in 5 ml portions (the kernel's droplet limit), each under
        INGRESS_L, so live mains gear under a dripping pot never took ingress however much water gathered on it."""
        from house_sim.systems.electrical.physical import gathered_l
        env, b, r = plugged_lamp(gfci=True)
        for _ in range(3):
            wet(env, b["lamp"], 0.005)
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"], "15 ml are not yet a mouthful")
        self.assertFalse(ent(env, b["lamp"]).get("water_ingress"))
        self.assertEqual(gathered_l(env.world.s, b["lamp"]), 0.015)
        wet(env, b["lamp"], 0.005)
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"], "20 ml gathered: the lamp leaks to ground")
        self.assertTrue(ent(env, b["lamp"])["water_ingress"])
        self.assertGreater(ent(env, b["lamp"])["damage"]["water_damage"], 0)
        self.assertEqual(gathered_l(env.world.s, b["lamp"]), 0.0, "the gathering is spent")
        self.assertNotIn("gathering", env.world.s["electrical"])

    def test_gathered_water_is_forgotten_once_the_gear_is_dry(self):
        """The litres add up only while the gear stays wet: once it is dry (cleaning's drying, a towel) what reached it
        before is gone, also when it dried during a wait in which no electrical refresh ran (electrical.wet_gear runs
        after the drying systems)."""
        env, b, r = plugged_lamp(gfci=True)
        wet(env, b["lamp"], 0.015)
        w = env.world
        w.begin()
        w.mut(b["lamp"])["wetness"] = 0.0          # dried off, with no electrical refresh
        w.commit()
        ok(self, env, "wait", seconds=30)
        wet(env, b["lamp"], 0.005)
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"], "the 15 ml dried off before these 5 ml arrived")
        for _ in range(3):
            wet(env, b["lamp"], 0.005)
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"], "20 ml gathered since it was last dry")
        # liquid that leaves no film (a seep from a wet thing resting on dry gear) does not gather
        env, b, r = plugged_lamp(gfci=True)
        from house_sim.kernel.plugins import run_hooks
        for _ in range(5):
            w = env.world
            w.begin()
            run_hooks("phys.wetted", w, b["lamp"], 0.005, "water", None)
            w.commit()
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"])
        self.assertNotIn("gathering", env.world.s.get("electrical") or {})

    def test_a_kernel_drip_onto_a_plugged_device_gets_in_once_20_ml_gathered(self):
        """The kernel's drip law (an ice pack thawing on a speaker) deposits 5 ml portions: liquid gets inside once
        20 ml reached the device (before, only the power step's soaked-device ground fault at wetness 0.4 tripped the
        GFCI, and the device never took ingress)."""
        env, b, r = dripped_on_gear({})
        from house_sim.kernel.phys import liquids
        w = env.world
        w.begin()
        self.assertTrue(liquids.drip_start(w, b["pot"], 1.2, until_l=0.1, by="exogenous"))      # 10 ml per 30 s
        w.commit()
        ok(self, env, "wait", seconds=30)
        self.assertFalse(ent(env, b["dev"]).get("water_ingress"))
        ok(self, env, "wait", seconds=30)
        self.assertTrue(ent(env, b["dev"])["water_ingress"], "20 ml gathered")
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"])
        self.assertLess(ent(env, b["dev"])["wetness"], 0.4, "before the soaked-device ground fault")

    def test_the_wet_gear_probe_is_exact(self):
        """electrical.wet_gear's idle probe: 15 ml dripped on a device, then hours of drying (when a drying domain is
        loaded) reach the same state with the probes on and off."""
        from house_sim.kernel import plugins
        from house_sim.kernel.phys import liquids
        from house_sim.kernel.predicates import digest
        from house_sim.systems.electrical.physical import gathered_l

        def run(probes):
            old = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = probes
            try:
                env, b, r = dripped_on_gear({})
                w = env.world
                w.begin()
                liquids.drip_start(w, b["pot"], 1.2, until_l=0.015, by="exogenous")
                w.commit()
                ok(self, env, "wait", seconds=120)
                gathered = gathered_l(env.world.s, b["dev"])
                for seconds in (1800, 3 * 3600):
                    ok(self, env, "wait", seconds=seconds)
                return gathered, gathered_l(env.world.s, b["dev"]), digest(env.world.s)
            finally:
                plugins.IDLE_PROBES[0] = old
        on, off = run(True), run(False)
        self.assertEqual(on[0], 0.015, "the drip's 15 ml gathered")
        from house_sim.systems.electrical.common import has_system
        if has_system("cleaning.drying") or has_system("appliances_cooking.drying"):
            self.assertEqual(on[1], 0.0, "the device dried during the wait and forgot them")
        self.assertEqual(on, off)

    def test_a_pot_dripping_on_a_plugged_tv_trips_its_gfci_once_water_gathers(self):
        """The request's repro (tests/test_plumbing_liquids.py Phase1Finish
        test_drips_onto_gear_run_the_wetting_hooks_and_damage_it_once_water_gathers) with electrical loaded and the
        TV plugged in: plumbing's 5 ml drips gather on it and get in."""
        from house_sim.systems.electrical.common import has_system
        if not has_system("plumbing_liquids.water"):
            self.skipTest("drip_lpm drips belong to plumbing_liquids")
        env, b, r = dripped_on_gear({"category": "planter", "drip_lpm": 0.02, "drip_left_l": 0.1,
                                     "label": "just-watered pot"})
        ok(self, env, "wait", seconds=60)
        self.assertTrue(ent(env, b["dev"])["water_ingress"], "20 ml gathered on it within the minute")
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"])


if __name__ == "__main__":
    unittest.main()
