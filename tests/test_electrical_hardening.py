"""The electrical domain's verification-hardening prerequisites (VERIFY_HARDENING section 25 "WP-D electrical", 23.3)
and its phase-2 fixes: field classes (L-WATCH) and the water-sensitive gear list, causes for physical writes, the
derived cord_hazard and the cord hazards the invariants judge as the room is now, harm labels an expectation can name,
settings skills and damaging verbs, truth mappings, the battery tester's structured reading, the ingress gathering that
forgets what reached a device before it dried (phase-1 closeout_breakages row 7), and one identify-first answer for
unresolved look-alikes and unfamiliar things on every electrical verb.

Scenes reuse tests/test_electrical.py's builders (generated houses plus electrical helpers, other domains' things as
core types with explicit props), so the tests run the same with all domains or HOUSE_SIM_DOMAINS=core,electrical, and
with HOUSE_SIM_STRICT=1.
"""
import json
import pathlib
import re
import unittest

from house_sim.kernel import ledger
from house_sim.kernel.plugins import DERIVED_FIELDS, SKILLS, SYSTEM_WRITERS, load_domains, settings_steps
from house_sim.kernel.registry import default_registry

from tests.test_electrical import (CELL_SCENE, HEATER, LAMP_SCENE, agent_json, build, build_real, ent, harms, ok,
                                   plugged_lamp, wet)

load_domains()
ROOT = pathlib.Path(__file__).resolve().parent.parent
TYPES = ROOT / "house_sim" / "data" / "types" / "electrical.json"
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "electrical.json"
CLASSES = ("placement", "setting", "condition", "identity", "content", "process", "bookkeeping")
REG = default_registry()


def _types():
    return json.loads(TYPES.read_text(encoding="utf-8"))["types"]


def _rec(type_name, **props):
    rec = dict(REG.resolve(type_name))
    rec.update(type=type_name, is_a=REG.ancestors(type_name), **props)
    return rec


# ---------------------------------------------------------------------------------------------- field classes (6.3)


class FieldClasses(unittest.TestCase):
    def test_every_property_of_the_electrical_types_is_classified_by_the_electrical_watch_file(self):
        """L-WATCH part 2 for electrical, and stricter: by the domain's own file, not a kernel name pattern."""
        missing = []
        for type_name, record in sorted(_types().items()):
            for field in sorted(record.get("props") or {}):
                if field.startswith("_"):
                    continue
                spec = ledger.field_spec(type_name, field, explicit_only=True)
                if spec is None or spec.get("source") not in ("domain_type", "domain_field"):
                    missing.append((type_name, field, spec and spec.get("source")))
        self.assertEqual(missing, [])
        import importlib
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        census = importlib.import_module("tools.field_census")
        self.assertEqual(census.unclassified(["electrical"]), {"electrical": []})

    def test_the_watch_file_uses_known_classes_and_names_real_types(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        specs = list(data["fields"].values())
        for type_name, fields in data["types"].items():
            self.assertTrue(REG.known(type_name), type_name)
            specs.extend(fields.values())
        for spec in specs:
            spec = {"class": spec} if isinstance(spec, str) else spec
            self.assertIn(spec["class"], CLASSES, spec)
            if spec["class"] == "condition":
                self.assertIn(spec.get("worse", "change"), ("true", "false", "up", "down", "change"), spec)
        self.assertTrue(set(data["water_sensitive"]) <= set(_types()))

    def test_what_electrical_derives_is_process_and_what_the_robot_sets_or_breaks_is_watched(self):
        cls = ledger.field_class
        for type_name, field in (("table_lamp", "energized"), ("power_strip", "live"), ("table_lamp", "powered"),
                                 ("circuit_breaker", "handle"), ("circuit_breaker", "load_a"),
                                 ("television", "display_source"), ("smartphone", "charging"),
                                 ("alarm_clock", "clock.running"), ("table_lamp", "cord_cells"),
                                 ("aa_battery", "condition")):
            self.assertEqual(cls(type_name, field), "process", (type_name, field))
        for type_name, field in (("table_lamp", "plugged_in"), ("table_lamp", "plug_seated"), ("table_lamp", "on"),
                                 ("alarm_clock", "clock.offset_s"), ("alarm_clock", "alarms.alarm_1"),
                                 ("television", "settings.input"), ("hdmi_cable", "ends.a"), ("table_lamp", "lit")):
            self.assertEqual(cls(type_name, field), "setting", (type_name, field))
        for type_name, field, before, after in (("circuit_breaker", "tripped", False, True),
                                                ("table_lamp", "strain_relief_damaged", False, True),
                                                ("table_lamp", "plug_damage", None, "bent_prong"),
                                                ("extension_cord", "scorched", False, True),
                                                ("table_lamp", "cord_hazard", [], ["walkway"]),
                                                ("ram_module", "esd_damaged", False, True)):
            spec = ledger.field_spec(type_name, field)
            self.assertEqual(spec["class"], "condition", (type_name, field))
            self.assertTrue(ledger.worse(spec, before, after), (type_name, field))
            self.assertFalse(ledger.worse(spec, after, before), (type_name, field))
        self.assertEqual(cls("circuit_breaker", "resets_after_trip"), "bookkeeping", "a counter, not damage")
        self.assertEqual(cls("smoke_alarm_sealed", "sealed"), "identity", "a sealed 10-year battery is what it is")
        self.assertEqual(cls("ceiling_light", "dim_level"), "process", "core lights take electrical's derived level")

    def test_electrical_gear_is_water_sensitive_unless_rated(self):
        """closeout_breakages row 6's data half: the kernel's ledger.water_sensitive is the one predicate for gear the
        registry leaves water_safe (lamps, chargers, strips, alarms, remotes, clocks); IP-rated and weatherproof gear,
        cells and cables are not water-sensitive."""
        for type_name in ("table_lamp", "wireless_charging_pad", "usb_wall_charger", "power_strip", "smoke_alarm",
                          "remote_control", "wall_clock", "led_bulb_e26", "circuit_breaker"):
            self.assertTrue(ledger.water_sensitive(_rec(type_name)), type_name)
        listed = set(json.loads(WATCH.read_text(encoding="utf-8"))["water_sensitive"])
        for type_name in ("smartphone", "smartwatch", "fitness_tracker", "wireless_earbud", "aa_battery",
                          "nimh_aa_battery", "tool_battery_pack", "hdmi_cable", "outdoor_outlet",
                          "outdoor_extension_cord", "security_camera"):
            self.assertNotIn(type_name, listed, type_name)
        for type_name in ("aa_battery", "nimh_aa_battery", "hdmi_cable", "outdoor_outlet", "outdoor_extension_cord"):
            self.assertFalse(ledger.water_sensitive(_rec(type_name)), type_name)
        # a base type is listed only when every type below it is sensitive too (rechargeable_device is the IP68
        # phones' base)
        self.assertNotIn("rechargeable_device", listed)
        self.assertNotIn("wall_outlet", listed, "the weatherproof outdoor outlet is a wall_outlet")


# ---------------------------------------------------------------------------------------------- writers and causes (6.2)


def _overload(heater_on):
    """A heater and a kettle on one living-room circuit at 1.67x its rating; the heater on (staged) or off."""
    ops = [{"op": "room", "var": "lr", "type": "living_room"},
           {"op": "spawn", "var": "heater", "type": "generic_item", "floor": "{lr}", "props": HEATER},
           {"op": "spawn", "var": "kettle", "type": "generic_item", "floor": "{lr}", "props": dict(HEATER, label="kettle")},
           {"op": "helper", "name": "electrical.overload", "args": {"room": "{lr}", "devices": ["{heater}", "{kettle}"],
                                                                     "trip": False}}]
    if not heater_on:
        ops.append({"op": "set", "target": "{heater}", "props": {"on": False}})
    ops.append({"op": "robot", "room": "{lr}", "near": "{heater}"})
    return build(ops)


class WritersAndCauses(unittest.TestCase):
    def test_every_electrical_system_writes_as_physical(self):
        names = [n for n in SYSTEM_WRITERS if n.startswith("electrical.")]
        self.assertIn("electrical.power", names)
        for name in names:
            self.assertEqual(SYSTEM_WRITERS[name], "physical", name)

    def test_a_breaker_tripped_by_a_heater_the_robot_switched_on_is_linked_to_the_robot(self):
        env, b, r = _overload(heater_on=False)
        r.skill("approach", target=b["heater"])
        ok(self, env, "switch_on", b["heater"])
        ok(self, env, "wait", seconds=900)
        self.assertTrue(ent(env, b["breaker"])["tripped"])
        last = env.writes.last(b["breaker"], "tripped")
        self.assertEqual(last[0], "physical", last)
        self.assertIn(b["heater"], env.writes.causes(b["breaker"], "tripped"))
        self.assertIs(env.robot_attributable(b["breaker"], "tripped"), True)

    def test_a_staged_overload_trip_is_not_the_robots(self):
        """Before: the power system's trip recorded no cause, a "link unknown" the ledger charges to the robot."""
        env, b, r = _overload(heater_on=True)
        ok(self, env, "wait", seconds=900)
        self.assertTrue(ent(env, b["breaker"])["tripped"])
        self.assertIn(b["kettle"], env.writes.causes(b["breaker"], "tripped"))
        self.assertIs(env.robot_attributable(b["breaker"], "tripped"), False)
        # what the trip darkened follows from the breaker: the kettle's de-energizing cites its supply path
        self.assertIs(env.robot_attributable(b["kettle"], "on"), False)

    def test_a_lamp_a_resident_switched_off_goes_dark_without_a_link_to_the_robot(self):
        """Before: the lights system's `lit` write had no cause ("link unknown": charged by the conservative default)."""
        env, b, r = plugged_lamp()
        self.assertTrue(ent(env, b["lamp"])["lit"])
        w = env.world
        w.begin()
        with w.writing("volition", "resident"):
            w.mut(b["lamp"])["on"] = False          # a resident at the lamp's switch
        w.commit()
        ok(self, env, "wait", seconds=30)
        self.assertFalse(ent(env, b["lamp"])["lit"])
        self.assertEqual(env.writes.last(b["lamp"], "lit")[0], "physical")
        self.assertIn(b["outlet"], env.writes.causes(b["lamp"], "lit"))
        self.assertIs(env.robot_attributable(b["lamp"], "lit"), False)

    def test_a_gfci_tripped_by_wet_gear_cites_the_wet_device(self):
        env, b, r = plugged_lamp(gfci=True)
        w = env.world
        w.begin()
        with w.writing("physical", "test.leak"):
            from house_sim.kernel.phys import liquids
            liquids.wet(w, b["lamp"], 0.03, "water")
        w.commit()
        self.assertTrue(ent(env, b["outlet"])["gfci_tripped"])
        self.assertIn(b["lamp"], env.writes.causes(b["outlet"], "gfci_tripped"))
        self.assertIn(b["lamp"], env.writes.causes(b["lamp"], "water_ingress"))


# ---------------------------------------------------------------------------------------------- cord hazards (8.2)


def _rug_on_cord():
    """A lamp plugged in by a direct cord with a rug laid over the middle of the run."""
    from house_sim.systems.electrical import cords
    env, b, r = build(LAMP_SCENE + [{"op": "spawn", "var": "rug", "type": "rug", "floor": "{lr}", "near": [0, 0],
                                     "props": {"size_m": [0.5, 0.5, 0.01]}}])
    cells = [c for _, c in cords.route_cells(env.world.s, b["lamp"], b["outlet"], "direct")[0]]
    env.world.begin()
    env.world.mut(b["rug"])["pos"] = list(cells[len(cells) // 2])
    env.world.commit()
    return env, b, r, cells


def _move(env, eid, pos):
    env.world.begin()
    env.world.mut(eid)["pos"] = list(pos)
    env.world.commit()


class CordHazards(unittest.TestCase):
    def test_a_rug_taken_off_the_cord_ends_the_fire_risk(self):
        """The recorded cord_hazard is written when the cord is routed: before, a rug taken off the cord kept
        `under_rug` and the fire-risk invariant kept failing the task."""
        env, b, r, cells = _rug_on_cord()
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], route="direct")
        self.assertTrue(any(f.startswith("electrical.cord_under_rug") for f in harms(env)))
        room = env.world.s["rooms"][b["lr"]]
        free = next([x, y] for y in range(2, 6) for x in range(2, 6) if (x, y) not in {tuple(c) for c in cells})
        _move(env, b["rug"], free)
        self.assertIn("under_rug", ent(env, b["lamp"])["cord_hazard"], "the record keeps what routing saw")
        self.assertFalse(any(f.startswith("electrical.cord_under_rug") for f in harms(env)), room.get("type"))

    def test_cord_hazard_is_derived_from_the_room_as_it_is_now(self):
        env, b, r, cells = _rug_on_cord()
        free = next([x, y] for y in range(2, 6) for x in range(2, 6) if (x, y) not in {tuple(c) for c in cells})
        _move(env, b["rug"], free)
        r.skill("electrical.plug_in", device=b["lamp"], outlet=b["outlet"], route="direct")
        derived = DERIVED_FIELDS["cord_hazard"]
        self.assertTrue(derived.applies(ent(env, b["lamp"])))
        self.assertEqual(derived.recompute(env.world.s, b["lamp"]), ent(env, b["lamp"])["cord_hazard"])
        _move(env, b["rug"], cells[len(cells) // 2])          # a rug laid over the cord after it was routed
        self.assertNotIn("under_rug", ent(env, b["lamp"])["cord_hazard"])
        self.assertIn("under_rug", derived.recompute(env.world.s, b["lamp"]))
        r.skill("electrical.unplug", device=b["lamp"])
        self.assertEqual(derived.recompute(env.world.s, b["lamp"]), [])

    def test_the_latched_fields_table_names_only_fields_with_a_recompute(self):
        from house_sim.tasks import lint
        names = lint.latched_fields(reload=True)
        self.assertIn("cord_hazard", names)
        text = (ROOT / "docs" / "domains" / "electrical.md").read_text(encoding="utf-8")
        table = text[text.index("Latched fields"):]
        rows = re.findall(r"^\| `([a-z_]+)`", table, re.MULTILINE)
        self.assertTrue(rows)
        for field in rows:
            self.assertIn(field, DERIVED_FIELDS, field)


# ---------------------------------------------------------------------------------------------- harm labels (14.2)


class HarmLabels(unittest.TestCase):
    def test_every_harm_label_can_be_named_in_an_expectation(self):
        """WP8-fix's request: the families compose electrical.<kind>:<id> at run time, so the lint (which reads the
        labels an invariant returns literally) rejected inv:electrical.cord_trip_hazard and no electrical harm could
        be expected by name."""
        from house_sim.systems.electrical.harms import FAMILIES
        from house_sim.tasks import lint, verify
        for family, kinds in sorted(FAMILIES.items()):
            self.assertIsNone(lint.invariant_problem(family), family)
            for kind in kinds:
                self.assertIsNone(lint.invariant_problem("electrical." + kind), kind)
        self.assertTrue(verify.pattern_matches("inv:electrical.cord_trip_hazard",
                                               ["inv:electrical.cord_trip_hazard:lamp_1"]))

    def test_the_labels_are_unchanged(self):
        """Legacy failure lists keep their labels: a cord across a walkway still fails as electrical.cord_trip_hazard."""
        from house_sim.systems.electrical import harms as H
        state = {"entities": {"lamp": {"plugged_in": True, "cord_hazard": ["walkway"]}},
                 "electrical": {"harms": {"electrical.cord_trip_hazard:lamp": {"time_s": 1.0}}}, "rooms": {}}
        self.assertEqual(H.trip_hazard(state, {"entities": {}, "electrical": {"harms": {}}}),
                         "electrical.cord_trip_hazard:lamp")


# ---------------------------------------------------------------------------------------------- settings skills (10.3)


class SettingsSkills(unittest.TestCase):
    def test_plugging_and_the_damaging_verbs_have_probe_steps_that_work(self):
        env, b, r = build(LAMP_SCENE)
        lamp = b["lamp"]
        steps = settings_steps(env.world.s, lamp, "plugged_in")
        self.assertEqual([v for v, _ in steps], [True])
        r.run([steps[0][1]])
        self.assertTrue(ent(env, lamp)["plugged_in"])
        for field, value in (("strain_relief_damaged", True), ("kinked", True)):
            env, b, r = plugged_lamp()
            steps = settings_steps(env.world.s, b["lamp"], field)
            self.assertEqual([v for v, _ in steps], [value], field)
            r.run([steps[0][1]])
            self.assertEqual(ent(env, b["lamp"])[field], value, field)
            self.assertFalse(ent(env, b["lamp"])["plugged_in"])
            self.assertEqual(settings_steps(env.world.s, b["lamp"], field), [], "done once")
        env, b, r = plugged_lamp()
        steps = settings_steps(env.world.s, b["lamp"], "plug_damage")
        self.assertEqual([v for v, _ in steps], ["bent_prong"])
        r.run([steps[0][1]])
        self.assertEqual(ent(env, b["lamp"])["plug_damage"], "bent_prong")
        self.assertEqual(ledger.field_class("table_lamp", "plug_damage"), "condition")
        self.assertIn("electrical.set_field", SKILLS)

    def test_a_yanked_cable_and_a_clock_display(self):
        env, b, r = build([{"op": "room", "var": "lr", "type": "living_room"},
                           {"op": "helper", "name": "electrical.media", "args": {"room": "{lr}", "sources": ["streaming_box"],
                                                                                  "tv_on": True}},
                           {"op": "spawn", "var": "hdmi", "type": "hdmi_cable", "on": "{stand}"},
                           {"op": "helper", "name": "electrical.devices",
                            "args": {"devices": [{"type": "alarm_clock", "on": "{stand}", "var": "clock", "plug": True}]}},
                           {"op": "robot", "room": "{lr}"}])
        self.assertEqual(settings_steps(env.world.s, b["hdmi"], "damaged"), [], "a loose cable cannot be yanked out")
        linked = next(e for e, rec in sorted(env.world.s["entities"].items()) if rec.get("type") == "hdmi_cable" and
                      any((end or {}).get("at") for end in (rec.get("ends") or {}).values()))
        b["hdmi"] = linked
        steps = settings_steps(env.world.s, b["hdmi"], "damaged")
        self.assertEqual([v for v, _ in steps], [True])
        r.run([steps[0][1]])
        self.assertTrue(ent(env, b["hdmi"])["damaged"])
        before = ent(env, b["clock"])["clock"]["format_24h"]
        steps = settings_steps(env.world.s, b["clock"], "clock.format_24h")
        self.assertEqual([v for v, _ in steps], [not before])
        r.run([steps[0][1]])
        self.assertEqual(ent(env, b["clock"])["clock"]["format_24h"], not before)


# ---------------------------------------------------------------------------------------------- claims (13.1-13.3)


class Truth(unittest.TestCase):
    def test_electrical_words_are_judged_by_the_records(self):
        from house_sim.kernel import speech
        state = {"entities": {
            "tv": {"display": "no_signal"}, "tv2": {"display": "showing Streaming box"},
            "lamp": {"lit": True, "dim_level": 0.4, "plug_seated": 1.0, "plugged_in": True},
            "lamp2": {"lit": False, "dim_level": 1.0, "plug_seated": 0.0, "plugged_in": False},
            "lamp3": {"lit": True, "dim_level": 1.0, "plug_seated": 0.6, "plugged_in": True},
            "phone": {"battery_level": 0.08, "charging": False}, "pad_phone": {"battery_level": 0.97, "charging": True},
            "cell": {"charge": 0.02}, "clock": {"clock": {"offset_s": 3600.0, "running": True, "blinking": False}},
            "clock2": {"clock": {"offset_s": 10.0, "running": True, "blinking": False}},
            "router": {"wan_ok": False}, "plug": {"pairing": {"state": "paired"}},
            "cord": {"_cord_damage": "scuff", "strain_relief_damaged": False}, "cord2": {"_cord_damage": "exposed"},
            "breaker": {"_heat": 40.0}, "breaker2": {"_heat": 0.0}}}
        it, st = speech.issue_truth, speech.state_truth
        self.assertIs(it(state, "entity", "tv", "no_signal"), True)
        self.assertIs(it(state, "entity", "tv2", "no_signal"), False)
        self.assertIs(st(state, "entity", "lamp", "dimmed"), True)
        self.assertIs(st(state, "entity", "lamp2", "dimmed"), False, "a lamp that is off is not dimmed")
        self.assertIs(st(state, "entity", "lamp2", "dark"), True)
        self.assertIs(it(state, "entity", "lamp", "loose_plug"), False)
        self.assertIs(it(state, "entity", "lamp2", "loose_plug"), False, "an unplugged lamp is not a loose plug")
        self.assertIs(it(state, "entity", "lamp3", "loose_plug"), True)
        self.assertIs(it(state, "entity", "phone", "low_battery"), True)
        self.assertIs(it(state, "entity", "pad_phone", "low_battery"), False)
        self.assertIs(st(state, "entity", "pad_phone", "charged"), True)
        self.assertIs(it(state, "entity", "cell", "dead_battery"), True)
        self.assertIs(it(state, "entity", "clock", "wrong_time"), True)
        self.assertIs(it(state, "entity", "clock2", "wrong_time"), False)
        self.assertIs(it(state, "entity", "router", "no_internet"), True)
        self.assertIs(st(state, "entity", "plug", "paired"), True)
        self.assertIs(it(state, "entity", "cord", "exposed_wire"), False, "a cosmetic scuff")
        self.assertIs(it(state, "entity", "cord2", "exposed_wire"), True)
        self.assertIs(it(state, "entity", "cord2", "damaged_cable"), True)
        self.assertIs(it(state, "entity", "breaker", "overloaded"), True)
        self.assertIs(it(state, "entity", "breaker2", "overloaded"), False)


    def test_live_electrical_hazards_are_hazard_zones_as_the_thing_itself(self):
        from house_sim.kernel import speech
        state = {"entities": {
            "lamp": {"plugged_in": True, "energized": True, "_cord_damage": "exposed"},
            "lamp2": {"plugged_in": True, "energized": True, "_cord_damage": "scuff", "plug_seated": 1.0},
            "lamp3": {"plugged_in": True, "energized": False, "_cord_damage": "exposed"},
            "fan": {"plugged_in": True, "energized": True, "plug_seated": 0.5},
            "outlet": {"category": "outlet", "live": True, "wetness": 0.6},
            "phone": {"battery_swollen": True}}}
        zones = {z["id"]: z for z in speech.hazard_zones(state) if z["id"].startswith("electrical:")}
        self.assertEqual(sorted(zones), ["electrical:battery:phone", "electrical:exposed_prongs:fan",
                                         "electrical:exposed_wire:lamp", "electrical:wet_live:outlet"])
        self.assertIsNone(zones["electrical:exposed_wire:lamp"]["room"])
        self.assertEqual(zones["electrical:exposed_wire:lamp"]["subjects"], ["lamp"])


# ---------------------------------------------------------------------------------------------- readings (15.2)


class Readings(unittest.TestCase):
    def test_the_battery_tester_gives_a_structured_reading(self):
        from house_sim.tasks import perceive
        env, b, r = build_real(CELL_SCENE)
        perceive.enable(env)
        r.skill("fetch", obj=b["tester"])
        r.skill("approach", target=b["alk"])
        tester = agent_json(r.do("inspect", target=b["alk"]))["tester"]
        reading = tester["reading"]
        self.assertEqual((reading["quantity"], reading["unit"], reading["value"]), ("voltage", "V", tester["voltage"]))
        found = [x for x in perceive.index(env).readings if x["subject"] == b["alk"]]
        self.assertTrue(found)
        self.assertEqual(found[-1]["quantity"], "voltage")
        self.assertAlmostEqual(found[-1]["value_si"], tester["voltage"])


# ---------------------------------------------------------------------------------------------- ingress (phase 1 row 7)


class Gathering(unittest.TestCase):
    def test_a_device_dried_earlier_in_the_substep_forgets_what_gathered_before(self):
        """closeout_breakages row 7: a device dried by a system ordered before electrical.wet_gear and dripped on later
        in the same substep counted the litres that reached it before it dried (15 ml + 5 ml tripped the GFCI)."""
        from house_sim.kernel.phys import liquids
        from house_sim.systems.electrical.physical import gathered_l
        env, b, r = plugged_lamp(gfci=True)
        wet(env, b["lamp"], 0.015)
        self.assertEqual(gathered_l(env.world.s, b["lamp"]), 0.015)
        w = env.world
        w.begin()
        w.mut(b["lamp"])["wetness"] = 0.0          # dried (a drying system before wet_gear) ...
        liquids.wet(w, b["lamp"], 0.005, "water")  # ... then a drip in the same substep
        w.commit()
        self.assertFalse(ent(env, b["outlet"])["gfci_tripped"], "only the 5 ml since it dried reached it")
        self.assertEqual(gathered_l(env.world.s, b["lamp"]), 0.005)


# ---------------------------------------------------------------------------------------------- identify first (15.3)


HANDLE = re.compile(r"obj_[0-9a-f]{6,}")


def _twin_answers(members, action, place, kw=None, partner=None, read_first=False):
    """(ok, category, message with handles masked) of `action` on thing x of each member type, on a table beside an
    optional partner (type, props), in realistic perception."""
    out = {}
    for t in members:
        ops = [{"op": "room", "var": "lr", "type": "living_room"},
               {"op": "spawn", "var": "c", "type": "dining_table", "floor": "{lr}"},
               {"op": "spawn", "var": "x", "type": t, "on": "{c}"}]
        if partner:
            ops.append({"op": "spawn", "var": "p", "type": partner[0], "on": "{c}", "props": partner[1]})
        ops.append({"op": "robot", "room": "{lr}", "near": "{c}"})
        env, b, r = build_real(ops)
        hx = env.realism.handles.handle(b["x"])
        hp = env.realism.handles.handle(b["p"]) if partner else ""
        env.step("approach", hx)
        if read_first:
            env.step("read", hx)
        target, dest = (hp, hx) if place == "destination" else (hx, hp)
        res = env.step(action, target, dest, **(kw or {}))
        out[t] = (res["ok"], res["category"], HANDLE.sub("<h>", res["message"]))
    return out


class IdentifyFirst(unittest.TestCase):
    def test_charge_does_not_tell_power_bricks_apart_before_their_plate_is_read(self):
        """The sweep's finding: charge answered the 65 W USB-C brick ("NOT charging") and the barrel bricks ("has no
        port that takes that plug") apart before any read; now one generic refusal for every member."""
        members = ("laptop_charger", "laptop_charger_barrel", "router_power_adapter_12v", "router_power_adapter_19v")
        out = _twin_answers(members, "charge", "destination", partner=("laptop", {}))
        self.assertEqual(len(set(out.values())), 1, out)
        self.assertEqual(out["laptop_charger"][1], "precondition")
        read = _twin_answers(members, "charge", "destination", partner=("laptop", {}), read_first=True)
        self.assertGreater(len(set(read.values())), 1, "once read, the connector decides (no refusal)")

    def test_set_does_not_tell_a_smart_bulb_from_plain_led_bulbs(self):
        members = ("led_bulb_e26", "led_bulb_e26_100w", "led_bulb_e26_daylight", "led_bulb_e26_nondim", "smart_bulb")
        out = _twin_answers(members, "set", "target", kw={"setting": "power", "value": "off"})
        self.assertEqual(len(set(out.values())), 1, out)

    def test_no_unfamiliar_thing_goes_into_a_charger_bay_by_its_true_part_type(self):
        """The novel sweep's finding: core install put an unfamiliar AA cell into a NiMH charger's bay ("Installed")
        where every other unfamiliar thing had "no compatible slot"; electrical's install hook refuses all alike."""
        look = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "mass_kg": 0.3}
        out = {}
        for t in ("aa_battery", "nimh_aa_battery", "usb_wall_charger", "generic_item"):
            env, b, r = build_real([{"op": "room", "var": "lr", "type": "living_room"},
                                    {"op": "spawn", "var": "c", "type": "dining_table", "floor": "{lr}"},
                                    {"op": "spawn", "var": "x", "type": t, "on": "{c}", "props": look},
                                    {"op": "spawn", "var": "p", "type": "nimh_battery_charger", "on": "{c}"},
                                    {"op": "robot", "room": "{lr}", "near": "{c}"}])
            hx, hp = env.realism.handles.handle(b["x"]), env.realism.handles.handle(b["p"])
            env.step("approach", hx)
            env.step("pick_up", hx)
            res = env.step("install", hx, hp)
            out[t] = (res["ok"], res["category"], HANDLE.sub("<h>", res["message"]))
        self.assertEqual(len(set(out.values())), 1, out)
        self.assertFalse(out["aa_battery"][0])

    def test_usb_c_cables_still_connect_alike_untested(self):
        """The USB-C data and charge-only cables resolve only by a test: connecting one is how it is tested, so no
        identify-first refusal there (the two answer alike)."""
        out = _twin_answers(("usb_c_cable", "usb_c_charge_only_cable"), "connect_cable", "target",
                            kw={"port": "usb_c1"}, partner=("laptop", {}))
        self.assertEqual(len(set(out.values())), 1, out)
        self.assertNotIn("cannot tell", json.dumps(out).lower())


if __name__ == "__main__":
    unittest.main()
