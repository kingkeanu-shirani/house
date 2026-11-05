"""The climate domain's verification-hardening prerequisites (VERIFY_HARDENING section 25 "WP-D climate", 23.3) and its
phase-2 fixes: field classes (L-WATCH), causes for physical writes, derived fields for what the hooks keep, the reservoirs'
mess flows, the energy meter, settings skills and damaging verbs, truth mappings, structured readings, hazard zones, harm
labels an expectation can name, and one identify-first answer for unfamiliar things on every climate verb.

Scenes reuse tests/test_climate.py's House (core types plus climate types, other domains' things as generic_item), so the
tests run the same with all domains or HOUSE_SIM_DOMAINS=core,climate, and with HOUSE_SIM_STRICT=1.
"""
import copy
import json
import pathlib
import unittest

from house_sim.kernel import ledger, speech
from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import ACTIONS, DERIVED_FIELDS, MESS_KINDS, SKILLS, SYSTEM_WRITERS, SYSTEMS, load_domains, settings_steps
from house_sim.kernel.registry import default_registry

from tests.test_climate import FanTests, HeaterTests, House, VentAndFilterTests, ent, hold, near, no, ok, room, wait

load_domains()

from house_sim.systems.climate import airclean, attribution as A, fans as FN, hardening as HD, model as MD  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TYPES = ROOT / "house_sim" / "data" / "types" / "climate.json"
WATCH = ROOT / "house_sim" / "data" / "verify" / "watch" / "climate.json"
CLASSES = ("placement", "setting", "condition", "identity", "content", "process", "bookkeeping")
IDENTIFY = "You cannot tell what it is yet"


def _types():
    return json.loads(TYPES.read_text(encoding="utf-8"))["types"]


def renv(h, seed=0, camera=None):
    return HouseEnv(copy.deepcopy(h.c), None, seed, perception="realistic", camera=camera)


def agent_step(env, action, target="", destination="", **args):
    """env.step with true ids translated to the handles an agent would use."""
    conv = lambda v: env.realism.handles.handle(v) if isinstance(v, str) and v in env.world.s["entities"] else v  # noqa: E731
    return env.step(action, conv(target), conv(destination), **{k: conv(v) for k, v in args.items()})


def attributable(env, eid, field):
    return env.robot_attributable(eid, field)


# ---------------------------------------------------------------------------------------------- field classes (6.3)


class FieldClasses(unittest.TestCase):
    def test_every_property_of_the_climate_types_is_classified_by_the_climate_watch_file(self):
        """L-WATCH part 2 for climate, and stricter: by the domain's own file, not a kernel name pattern."""
        missing = []
        for type_name, record in sorted(_types().items()):
            for field in sorted(record.get("props") or {}):
                if field.startswith("_"):
                    continue
                spec = ledger.field_spec(type_name, field, explicit_only=True)
                if spec is None or spec.get("source") not in ("domain_type", "domain_field"):
                    missing.append((type_name, field, spec and spec.get("source")))
        self.assertEqual(missing, [])

    def test_the_watch_file_uses_known_classes_and_names_real_types(self):
        data = json.loads(WATCH.read_text(encoding="utf-8"))
        reg = default_registry()

        def class_of(spec):
            return spec if isinstance(spec, str) else spec.get("class")
        for name, spec in data["fields"].items():
            self.assertIn(class_of(spec), CLASSES, name)
        for type_name, fields in data["types"].items():
            if type_name in _types() or type_name in ("window",):
                self.assertTrue(reg.known(type_name), type_name)
            for name, spec in fields.items():
                self.assertIn(class_of(spec), CLASSES, (type_name, name))
        for type_name in data["water_sensitive"]:
            self.assertIn(type_name, _types())

    def test_what_the_model_derives_is_process_and_what_the_robot_sets_or_breaks_is_watched(self):
        # the as-if references' false alarms: what follows a setting is not itself a changed setting
        for type_name, field in (("thermostat", "hold_until"), ("thermostat", "hold_until_s"), ("thermostat", "hold_ends_at_period"),
                                 ("thermostat", "active_period"), ("thermostat", "readings"), ("ultrasonic_humidifier", "tank_seated"),
                                 ("humidifier_tank", "mineral_ppm"), ("dehumidifier", "intake_blocked"), ("hvac_filter", "open"),
                                 ("hvac_filter", "openable"), ("thermostat", "setting_options.heat_setpoint"), ("box_fan", "cord_length_needed_m")):
            self.assertEqual(ledger.field_class(type_name, field), "process", (type_name, field))
        for type_name, field in (("thermostat", "settings.mode"), ("thermostat", "settings.weekday_wake"), ("supply_register", "settings.damper"),
                                 ("furnace", "on"), ("furnace", "cover_open"), ("furnace", "slots.filter"), ("hvac_filter", "shrink_wrapped"),
                                 ("hvac_filter", "filter_spec.arrow"), ("box_fan", "plugged_in")):
            self.assertEqual(ledger.field_class(type_name, field), "setting", (type_name, field))
        for type_name, field, worse in (("furnace", "fault", "true"), ("furnace", "heat_exchanger_stressed", "true"), ("ceiling_fan", "motor_damaged", "true"),
                                        ("ceiling_fan", "wobble", "true"), ("hvac_filter", "reversed", "true"), ("air_purifier", "layer_order_ok", "false"),
                                        ("box_fan", "cord_crosses_walkway", "true"), ("hvac_filter", "dirtiness", "up")):
            spec = ledger.field_spec(type_name, field)
            self.assertEqual((spec["class"], spec["worse"]), ("condition", worse), (type_name, field))
        self.assertEqual(ledger.field_class("furnace", "heating_capacity_w"), "identity")
        self.assertEqual(ledger.field_class("furnace", "starts"), "bookkeeping")

    def test_a_filter_fit_and_a_little_dust_are_no_damage(self):
        spec = ledger.field_spec("purifier_filter", "bypass_gap_mm")
        self.assertFalse(ledger.worse(spec, ledger.MISSING, 4.0))       # the correct cartridge's 4 mm
        self.assertTrue(ledger.worse(spec, 0.0, 12.0))
        spec = ledger.field_spec("hvac_filter", "dirtiness")
        self.assertFalse(ledger.worse(spec, 0.1, 0.1001))
        self.assertTrue(ledger.worse(spec, 0.1, 0.5))

    def test_a_bucket_of_condensate_is_a_load_and_a_tank_of_water_is_content(self):
        spec = ledger.field_spec("dehumidifier_bucket", "liquid_l")
        self.assertEqual((spec["class"], spec.get("guard")), ("content", "mess"))
        spec = ledger.field_spec("humidifier_tank", "liquid_l")
        self.assertEqual((spec["class"], spec.get("guard")), ("content", None))

    def test_what_climate_writes_on_windows_is_classified(self):
        self.assertEqual(ledger.field_class("window", "covered"), "process")
        self.assertEqual(ledger.field_class("sash_window", "condensation"), "process")
        self.assertEqual(ledger.field_class("window", "rain_in_l"), "bookkeeping")

    def test_gear_is_water_sensitive_unless_rated_or_washable(self):
        reg = default_registry()

        def sensitive(type_name):
            rec = dict(reg.resolve(type_name))
            rec.update(type=type_name, is_a=reg.ancestors(type_name))
            return ledger.water_sensitive(rec)
        for type_name in ("box_fan", "ceramic_heater", "smart_thermostat", "dehumidifier", "tower_air_purifier", "hygrometer", "hvac_filter", "purifier_hepa_filter"):
            self.assertTrue(sensitive(type_name), type_name)
        for type_name in ("purifier_prefilter", "outdoor_condenser", "humidifier_tank", "dehumidifier_bucket"):
            self.assertFalse(sensitive(type_name), type_name)


# ---------------------------------------------------------------------------------------------- writers, causes (6.2)


class WritersAndCauses(unittest.TestCase):
    def test_every_climate_system_writes_as_physical(self):
        names = [name for _, name, _, _ in SYSTEMS if name.startswith("climate.")]
        self.assertEqual(sorted(names), ["climate.house", "climate.meter", "climate.slow"])
        for name in names:
            self.assertEqual(SYSTEM_WRITERS.get(name, "physical"), "physical", name)

    def heater_scene(self, on):
        h = HeaterTests().house(curtain_close=True)
        h.c["entities"]["heater"]["on"] = on
        h.c["entities"]["laundry"]["pos"] = [6, 6]
        return h

    def test_a_scorched_thing_by_a_heater_the_robot_switched_on_is_linked_to_the_robot(self):
        env = self.heater_scene(False).env()
        for rid in ("hall", "bedroom"):
            ok(self, env.step("move", rid))
        near(self, env, "heater")
        ok(self, env.step("switch_on", "heater"))
        wait(env, 600)
        self.assertTrue(ent(env, "laundry").get("scorched"))
        self.assertIs(attributable(env, "laundry", "scorched"), True)

    def test_a_scorched_thing_by_the_household_heater_is_not_the_robots(self):
        env = self.heater_scene(True).env()
        wait(env, 600)
        self.assertTrue(ent(env, "laundry").get("scorched"))
        self.assertIs(attributable(env, "laundry", "scorched"), False)       # was None ("link unknown", charged)

    def rain_scene(self, window_open):
        h = House(outdoor_c=12.0, weather={"condition": "rain", "temperature_c": 12.0, "humidity": 0.9, "wind_mps": 6.0, "wind_from_deg": 0.0, "rain_mm_h": 8.0})
        h.c["entities"]["win_n"].update(open=window_open, locked=False)
        return h

    def test_rain_through_a_window_follows_from_who_opened_it(self):
        env = self.rain_scene(True).env()
        wait(env, 1800)
        self.assertGreater(ent(env, "win_n")["rain_in_l"], 0.25)
        self.assertIs(attributable(env, "win_n", "rain_in_l"), False)
        env = self.rain_scene(False).env()
        near(self, env, "win_n")
        ok(self, env.step("open", "win_n"))
        wait(env, 1800)
        self.assertGreater(ent(env, "win_n")["rain_in_l"], 0.25)
        self.assertIs(attributable(env, "win_n", "rain_in_l"), True)

    def test_condensate_on_the_floor_cites_the_unit_and_its_hose(self):
        h = House(outdoor_c=16.0, system=None, weather={"condition": "cloudy", "temperature_c": 16.0, "humidity": 0.85, "wind_mps": 1.0})
        h.c["rooms"]["basement"].update(humidity=0.72, temperature_c=18.0)
        h.add("dehu", "dehumidifier", "basement", pos=[5, 4], on=True, settings={"target_rh": 50, "fan": "high", "drain": "hose"})
        h.add("hose_end", "drain_hose", "basement", pos=[6, 6], tethered_to="dehu")
        h.c["entities"]["dehu"]["hose"] = "hose_end"
        env = h.env()
        wait(env, 600)
        self.assertTrue(room(env, "basement").get("wet_cells"))
        self.assertEqual(sorted(env.writes.causes("rooms:basement", "wet_cells")), ["dehu", "hose_end"])
        self.assertIs(env.robot_attributable("rooms:basement", "wet_cells"), False)

    def paper_scene(self, speed):
        h = FanTests().house(control="remote", speed=speed)
        h.add("letter", "generic_item", "table", relation="on", spos=[0.5, 0.3], label="letter", category="paper", mass_kg=0.01, size_m=[0.2, 0.3, 0.001])
        h.c["entities"]["fan"]["pos"] = [5, 5]
        return h

    def test_a_paper_blown_off_cites_the_fans_blowing_there(self):
        env = self.paper_scene("high").env()
        wait(env, 120)
        self.assertEqual(ent(env, "letter")["location"], "living")
        self.assertIn("fan", env.writes.causes("letter", "location"))
        self.assertIs(attributable(env, "letter", "location"), False)        # the household's fan: not the robot's
        env = self.paper_scene("off").env()
        near(self, env, "remote")
        ok(self, env.step("pick_up", "remote"))
        ok(self, env.step("set", "fan", setting="speed", value="high"))
        wait(env, 120)
        self.assertEqual(ent(env, "letter")["location"], "living")
        self.assertIs(attributable(env, "letter", "location"), True)

    def test_a_paper_set_back_is_no_longer_scattered(self):
        env = self.paper_scene("high").env()
        wait(env, 120)
        self.assertTrue(ent(env, "letter")["scattered"])
        near(self, env, "remote")
        ok(self, env.step("pick_up", "remote"))
        ok(self, env.step("set", "fan", setting="speed", value="off"))
        wait(env, 120)
        hold(env, "letter")
        near(self, env, "table")
        ok(self, env.step("place", "letter", "table"))
        self.assertNotIn("scattered", ent(env, "letter"))

    def test_a_frozen_coil_cites_the_system_and_the_registers(self):
        h = VentAndFilterTests().hot_house()
        for r in ("reg_l1", "reg_l2", "reg_bed", "reg_k"):
            h.c["entities"][r]["settings"]["damper"] = 0.0
        env = h.env()
        wait(env, 2 * 3600)
        self.assertEqual(ent(env, "hvac")["fault"], "coil_frozen")
        causes = env.writes.causes("hvac", "fault")
        for eid in ("hvac", "reg_l1", "thermostat", "hvac_filter_old"):
            self.assertIn(eid, causes)
        self.assertIs(attributable(env, "hvac", "fault"), False)
        h = VentAndFilterTests().hot_house()
        for r in ("reg_l1", "reg_l2", "reg_bed"):
            h.c["entities"][r]["settings"]["damper"] = 0.0
        env = h.env()
        ok(self, env.step("move", "kitchen"))
        near(self, env, "reg_k")
        ok(self, env.step("set", "reg_k", setting="damper", value=0.0))
        wait(env, 2 * 3600)
        self.assertEqual(ent(env, "hvac")["fault"], "coil_frozen")
        self.assertIs(attributable(env, "hvac", "fault"), True)

    def test_a_schedule_change_is_not_the_robots_unless_it_touched_the_thermostat(self):
        h = House(start="2026-10-09T08:29:00")
        h.c["entities"]["thermostat"]["schedule_enabled"] = True
        h.c["entities"]["thermostat"]["settings"]["weekday_leave"]["heat"] = 18.0
        env = h.env()
        wait(env, 300)
        self.assertEqual(ent(env, "thermostat")["settings"]["heat_setpoint"], 18.0)
        self.assertIs(attributable(env, "thermostat", "settings"), False)

    def test_every_physical_write_of_a_compared_field_reports_a_cause(self):
        """The census's rule for this domain (6.2): a harm scene exercising the humidifier, the rain, the fan and the
        heater; every write by a climate system of a field the ledger compares carries a cause."""
        h = self.heater_scene(True)
        h.add("hum", "ultrasonic_humidifier", "bedroom", pos=[2, 2], on=True, settings={"output": "high", "target_rh": 70})
        h.add("tank", "humidifier_tank", "hum", relation="part", slot="tank", liquid_l=3.0, liquid="water")
        h.c["entities"]["hum"]["slots"]["tank"]["item"] = "tank"
        h.add("stand", "nightstand", "bedroom", pos=[3, 2])
        h.add("purifier", "air_purifier", "bedroom", pos=[0, 5], on=True, settings={"mode": "high", "filter_indicator": "ok"}, filter_hours=4379.9)
        h.add("cart", "purifier_filter", "purifier", relation="part", slot="cartridge", shrink_wrapped=False, openable=False, dirtiness=0.2)
        h.c["entities"]["purifier"]["slots"]["cartridge"]["item"] = "cart"
        h.c["weather"].update(condition="rain", rain_mm_h=8.0, wind_mps=6.0, wind_from_deg=0.0)
        h.c["entities"]["win_n"].update(open=True, locked=False)
        env = h.env()
        wait(env, 1800)
        missing = []
        state = env.world.s
        for (rid, field), (cls, name, _) in sorted(env.writes.items(), key=str):
            if field is None or not str(name).startswith("climate.") or field.startswith("_") or rid not in state["entities"]:
                continue
            rec = state["entities"][rid]
            names = [field]
            if isinstance(rec.get(field), dict):
                names = [field + "." + str(k) for k in rec[field]] or names
            charged = any(ledger.field_class(rec.get("type"), n) not in ("process", "bookkeeping") for n in names)
            if charged and not env.writes.causes(rid, field):
                missing.append((rid, field, name))
        self.assertEqual(missing, [])


# ------------------------------------------------------------------------------------------------- derived (8.2)


class DerivedFields(unittest.TestCase):
    def test_a_filter_out_of_its_slot_has_no_reversed_or_gap_on_record(self):
        env = House().env()
        state = env.world.s
        self.assertIs(DERIVED_FIELDS["reversed"].recompute(state, "hvac_filter_old"), False)
        self.assertEqual(DERIVED_FIELDS["bypass_gap_mm"].recompute(state, "hvac_filter_old"), 0.0)
        for field in ("reversed", "bypass_gap_mm"):
            self.assertTrue(DERIVED_FIELDS[field].applies(ent(env, "hvac_filter_old")))
        for rid in ("hall", "closet"):
            ok(self, env.step("move", rid))
        near(self, env, "hvac")
        ok(self, env.step("switch_off", "hvac"))
        ok(self, env.step("open_cover", "hvac"))
        ok(self, env.step("uninstall", "hvac", slot="filter"))
        self.assertIs(DERIVED_FIELDS["reversed"].recompute(env.world.s, "hvac_filter_old"), HD.MISSING)
        self.assertIs(DERIVED_FIELDS["bypass_gap_mm"].recompute(env.world.s, "hvac_filter_old"), HD.MISSING)
        ok(self, env.step("install", "hvac_filter_old", "hvac", slot="filter", orientation="upside_down"))
        self.assertIs(DERIVED_FIELDS["reversed"].recompute(env.world.s, "hvac_filter_old"), True)

    def test_the_recomputes_agree_with_the_hooks_records(self):
        h = House(system=None)
        h.add("purifier", "tower_air_purifier", "bedroom", pos=[6, 6])
        for slot, t in (("layer_1", "purifier_prefilter"), ("layer_2", "purifier_hepa_filter"), ("layer_3", "purifier_carbon_filter")):
            eid = slot + "_f"
            h.add(eid, t, "bedroom", pos=[5, 5] if slot == "layer_1" else [5, 6] if slot == "layer_2" else [4, 5], shrink_wrapped=False, openable=False)
        env = h.env()
        for rid in ("hall", "bedroom"):
            ok(self, env.step("move", rid))
        near(self, env, "purifier")
        ok(self, env.step("open_cover", "purifier"))
        for slot in ("layer_1", "layer_2", "layer_3"):
            eid = slot + "_f"
            hold(env, eid)
            ok(self, env.step("install", eid, "purifier", slot=slot))
            rec = ent(env, eid)
            self.assertEqual(DERIVED_FIELDS["bypass_gap_mm"].recompute(env.world.s, eid), rec["bypass_gap_mm"])
            self.assertEqual(DERIVED_FIELDS["reversed"].recompute(env.world.s, eid), rec["reversed"])
        self.assertIs(ent(env, "purifier")["layer_order_ok"], False)      # hepa before carbon
        self.assertIs(DERIVED_FIELDS["layer_order_ok"].recompute(env.world.s, "purifier"), False)
        ok(self, env.step("uninstall", "purifier", slot="layer_2"))
        self.assertIs(DERIVED_FIELDS["layer_order_ok"].recompute(env.world.s, "purifier"), False)
        ok(self, env.step("place", "layer_2_f"))
        ok(self, env.step("uninstall", "purifier", slot="layer_3"))
        self.assertIs(DERIVED_FIELDS["layer_order_ok"].recompute(env.world.s, "purifier"), True)

    def test_a_humidifier_tank_seated_follows_the_slot(self):
        h = House(system=None)
        h.add("hum", "ultrasonic_humidifier", "living", pos=[6, 7])
        h.add("tank", "humidifier_tank", "hum", relation="part", slot="tank", liquid_l=1.0)
        h.c["entities"]["hum"]["slots"]["tank"]["item"] = "tank"
        env = h.env()
        self.assertIs(DERIVED_FIELDS["tank_seated"].recompute(env.world.s, "hum"), True)
        near(self, env, "hum")
        ok(self, env.step("uninstall", "hum", slot="tank"))
        self.assertIs(DERIVED_FIELDS["tank_seated"].recompute(env.world.s, "hum"), False)

    def test_a_cord_and_a_placement_read_the_room_as_it_is_now(self):
        h = House(system=None)
        h.add("fan", "box_fan", "living", pos=[4, 4], outlet=None)
        env = h.env()
        ok(self, env.step("walk_to", cell=[2, 2]))
        hold(env, "fan")
        ok(self, env.step("place", "fan", "", cell=[2, 3]))
        state = env.world.s
        self.assertIs(DERIVED_FIELDS["cord_under_rug"].recompute(state, "fan"), False)
        rid, cells, _ = FN.cord_route(state, "fan")
        route = [c for c in cells if c != FN.device_cell(state, "fan")[1]]
        self.assertTrue(route, "the scene needs a cord run")
        # a rug laid over the run later: the record never saw it, the derived fields do
        rug = copy.deepcopy(h.reg.make("rug", rid, size_m=[0.5, 0.5, 0.01]))
        rug["pos"] = list(route[0])
        env.world.begin()
        env.world.create("rug_late", rug)
        env.world.commit()
        state = env.world.s
        self.assertFalse(ent(env, "fan").get("cord_under_rug"))
        self.assertIs(DERIVED_FIELDS["cord_under_rug"].recompute(state, "fan"), True)
        self.assertIn("cord_under_rug", DERIVED_FIELDS["placement_issues"].recompute(state, "fan"))

    def test_the_latched_fields_table_names_only_fields_with_a_recompute(self):
        from house_sim.tasks import lint
        text = (ROOT / "docs" / "domains" / "climate.md").read_text(encoding="utf-8")
        section = text[text.index("Latched fields"):]
        rows = [line for line in section.splitlines() if line.startswith("| `")]
        self.assertGreaterEqual(len(rows), 7)
        latched = lint.latched_fields(reload=True)
        for line in rows:
            name = line.split("`")[1]
            self.assertIn(name, latched)
            self.assertIn(name, DERIVED_FIELDS, name)


# ------------------------------------------------------------------------------------------------ mess flows (6.5)


class ReservoirFlows(unittest.TestCase):
    def scene(self, liquid, dest=True, cat="toilet", outdoors=False):
        h = House(system=None)
        h.add("pail", "generic_item", "table", relation="on", label="pail", category="pail", liquid_container=True, liquid=liquid, liquid_l=2.0, capacity_l=8.0,
              size_m=[0.25, 0.25, 0.25], mass_kg=0.8)
        if dest:
            h.add("basin", "generic_item", "living", pos=[6, 5], label=cat, category=cat, portable=False, drain=True, size_m=[0.4, 0.4, 0.4])
        return h

    def pour(self, h, dest="basin"):
        env = h.env()
        near(self, env, "pail")
        ok(self, env.step("pick_up", "pail"))
        if dest:
            near(self, env, dest)
        return env

    def flows(self, env):
        return [f for f in ((env.world.s.get("logs") or {}).get("mess_flows") or ()) if str(f.get("origin", "")).startswith("reservoir:")]

    def test_mop_water_poured_down_a_toilet_or_a_sink_is_logged_there(self):
        if "liquid:mop_water" not in MESS_KINDS:
            self.skipTest("plumbing_liquids registers the liquid kinds")
        for cat in ("toilet", "sink"):
            env = self.pour(self.scene("mop_water", cat=cat))
            ok(self, env.step("empty_reservoir", "pail", destination="basin"))
            found = self.flows(env)
            self.assertEqual(len(found), 1, found)
            self.assertEqual((found[0]["kind"], found[0]["src"], found[0]["dst"], found[0]["amount"]),
                             ("liquid:mop_water", "entity:pail", "entity:basin", 2.0))
            self.assertEqual(found[0]["origin"], "reservoir:pail")

    def test_the_ledger_reads_the_pour(self):
        if "liquid:mop_water" not in MESS_KINDS:
            self.skipTest("plumbing_liquids registers the liquid kinds")
        env = self.pour(self.scene("mop_water", cat="toilet"))
        ok(self, env.step("empty_reservoir", "pail", destination="basin"))
        labels = ledger.failures({"hardening": {}}, env.world.s, env._baseline, world=env)
        self.assertEqual([x for x in labels if "mess" in x or "wrong_sink" in x], [])
        env = self.pour(self.scene("mop_water", cat="sink"))
        ok(self, env.step("empty_reservoir", "pail", destination="basin"))
        labels = ledger.failures({"hardening": {}}, env.world.s, env._baseline, world=env)
        self.assertIn("house.wrong_sink:liquid:mop_water", labels)

    def test_mop_water_poured_on_the_ground_is_logged_outside(self):
        if "liquid:mop_water" not in MESS_KINDS:
            self.skipTest("plumbing_liquids registers the liquid kinds")
        h = self.scene("mop_water", dest=False)
        h.c["entities"]["slider"].update(open=True, locked=False)
        env = h.env()
        near(self, env, "pail")
        ok(self, env.step("pick_up", "pail"))
        ok(self, env.step("move", "patio"))
        ok(self, env.step("empty_reservoir", "pail"))
        found = self.flows(env)
        self.assertEqual([(f["kind"], f["dst"]) for f in found], [("liquid:mop_water", "outside:patio")])

    def test_clean_water_and_condensate_are_no_mess_anywhere(self):
        for liquid in ("water", "condensate", "distilled_water"):
            env = self.pour(self.scene(liquid))
            ok(self, env.step("empty_reservoir", "pail", destination="basin"))
            self.assertEqual(self.flows(env), [])

    def test_a_bleach_bottle_poured_on_the_ground_is_mess_there_and_consumption_in_a_drain(self):
        if "liquid:chemical" not in MESS_KINDS:
            self.skipTest("plumbing_liquids registers the liquid kinds")
        h = self.scene("bleach", dest=False)
        h.c["entities"]["pail"].update(category="cleaning_product")
        h.c["entities"]["slider"].update(open=True, locked=False)
        env = h.env()
        near(self, env, "pail")
        ok(self, env.step("pick_up", "pail"))
        ok(self, env.step("move", "patio"))
        ok(self, env.step("empty_reservoir", "pail"))
        self.assertEqual([(f["kind"], f["src"], f["dst"]) for f in self.flows(env)], [("liquid:chemical", "created:pail", "outside:patio")])
        h = self.scene("bleach")
        h.c["entities"]["pail"].update(category="cleaning_product")
        env = self.pour(h)
        ok(self, env.step("empty_reservoir", "pail", destination="basin"))
        self.assertEqual(self.flows(env), [])


# ---------------------------------------------------------------------------------------------- the energy meter (6.8)


class EnergyMeter(unittest.TestCase):
    def test_a_lit_fixture_without_a_rating_is_on_the_meter(self):
        h = House(system=None)
        rec = h.c["entities"]["light_living"]
        self.assertNotIn("power_w", rec)
        self.assertTrue(rec.get("on"))
        env = h.env()
        wait(env, 3600)
        by = env.world.s["climate"]["energy"]["by_device"]
        self.assertAlmostEqual(by.get("light_living", 0.0), MD.LIGHT_W / 1000.0, delta=0.002)
        h.c["entities"]["light_living"]["on"] = False
        h.c["entities"]["light_living"]["lit"] = False
        env = h.env()
        wait(env, 3600)
        self.assertEqual(env.world.s["climate"]["energy"]["by_device"].get("light_living", 0.0), 0.0)

    def test_a_light_draws_its_bulbs_when_known_and_nothing_burnt_out(self):
        self.assertEqual(MD._light_draw({"_bulb_w": 9.0, "dim_level": 1.0}), 9.0)
        self.assertEqual(MD._light_draw({"power_w": 60.0, "dim_level": 0.5}), 30.0)
        self.assertEqual(MD._light_draw({}), MD.LIGHT_W)
        self.assertEqual(MD._light_draw({"burnt_out": True, "power_w": 60.0}), 0.0)

    def test_a_chargers_load_is_on_the_meter_on_top_of_its_own_draw(self):
        h = House(system=None)
        h.add("charger", "generic_item", "living", pos=[1, 9], label="charger", category="charger", plug_required=True, plugged_in=True,
              standby_w=1.0, size_m=[0.06, 0.06, 0.03])
        env = h.env()
        rec = dict(ent(env, "charger"), _load_w=50.0)
        self.assertEqual(MD._other_draw(env.world, "charger", rec), 51.0)
        self.assertTrue(MD._power_candidate({"_load_w": 50.0}))

    def test_a_climate_device_is_booked_once(self):
        h = House(system=None)
        h.add("fan", "box_fan", "living", pos=[4, 4], on=True, settings={"speed": "high", "oscillate": False, "aim_deg": 0.0})
        env = h.env()
        wait(env, 3600)
        self.assertAlmostEqual(env.world.s["climate"]["energy"]["by_device"]["fan"], 0.075, delta=0.002)

    def test_the_meter_stays_idle_in_a_house_with_nothing_metered(self):
        self.assertFalse(MD._metered({"category": "chair"}))
        self.assertTrue(MD._metered({"category": "light"}))
        self.assertTrue(MD._metered({"_load_w": 0.0}))


# --------------------------------------------------------------------------------------------- settings skills (10.3)


class SettingsSkills(unittest.TestCase):
    def run_step(self, env, step):
        from house_sim.tasks.runner import Runner
        step = dict(step)
        name = step.pop("skill")
        runner = Runner(env, {})
        SKILLS[name](runner, **step)

    def check(self, env, eid, field, sub=None):
        options = settings_steps(env.world.s, eid, field)
        self.assertTrue(options, field)
        value, step = options[0]
        self.run_step(env, step)
        rec = ent(env, eid)
        got = rec
        for part in field.split("."):
            got = got.get(part) if isinstance(got, dict) else None
        self.assertEqual(got, value, field)

    def test_numeric_and_controlled_settings_have_probe_steps_that_work(self):
        h = House()
        h.add("heater", "ceramic_heater", "living", pos=[6, 8])
        h.add("hum", "ultrasonic_humidifier", "living", pos=[6, 7])
        h.add("rad", "radiator", "living", pos=[3, 0])
        env = h.env()
        for eid, field in (("reg_l2", "settings.damper"), ("thermostat", "settings.heat_setpoint"), ("thermostat", "settings.cool_setpoint"),
                           ("heater", "settings.thermostat"), ("heater", "settings.aim_deg"), ("hum", "settings.target_rh"), ("rad", "settings.trv")):
            self.check(env, eid, field)

    def test_a_mini_split_takes_its_settings_by_remote(self):
        h = House(system=None)
        h.add("split", "mini_split", "living", pos=[2, 0], remote="remote", mounted=True)
        h.add("remote", "generic_item", "table", relation="on", spos=[0.1, 0.1], label="remote", category="remote", remote_kind="ir",
              battery_powered=True, battery_level=1.0, size_m=[0.05, 0.02, 0.15], mass_kg=0.08)
        env = h.env()
        self.check(env, "split", "settings.setpoint")
        self.check(env, "split", "settings.fan")

    def test_a_remote_fan_and_a_filter_wrap(self):
        h = FanTests().house(control="remote", speed="off")
        h.add("spare", "hvac_filter", "table", relation="on")
        env = h.env()
        self.check(env, "fan", "settings.speed")
        self.check(env, "fan", "settings.light")
        self.assertEqual(settings_steps(env.world.s, "spare", "shrink_wrapped")[0][0], False)
        self.check(env, "spare", "shrink_wrapped")
        self.assertEqual(settings_steps(env.world.s, "spare", "shrink_wrapped"), [])

    def test_damaging_verbs_reach_their_condition(self):
        if "press" not in ACTIONS:
            self.skipTest("mechanisms' press pulls the chains")
        h = FanTests().house(control="pull_chain", speed="off")
        h.c["entities"]["fan"]["mount_height_m"] = 1.7
        env = h.env()
        self.check(env, "fan", "motor_damaged")
        self.assertTrue(ent(env, "fan")["wobble"])
        self.assertEqual(settings_steps(env.world.s, "fan", "motor_damaged"), [])

    def test_a_fitted_filter_goes_in_backwards(self):
        env = House().env()
        self.assertEqual(settings_steps(env.world.s, "hvac_filter_old", "reversed")[0][0], True)
        self.check(env, "hvac_filter_old", "reversed")
        self.assertEqual(settings_steps(env.world.s, "hvac_filter_old", "reversed"), [])


# ------------------------------------------------------------------------------------------- truth mappings (13)


class Truth(unittest.TestCase):
    def setUp(self):
        self.env = House().env()
        self.state = self.env.world.s

    def set(self, eid, **fields):
        self.state["entities"][eid].update(fields)

    def test_the_equipment_states_are_judged_by_the_records(self):
        self.set("hvac", stage="heat")
        self.assertIs(speech.state_truth(self.state, "entity", "hvac", "heating"), True)
        self.assertIs(speech.state_truth(self.state, "entity", "hvac", "cooling"), False)
        self.set("hvac", stage="cool")
        self.assertIs(speech.state_truth(self.state, "entity", "hvac", "cooling"), True)
        self.set("thermostat", call="heat")
        self.assertIs(speech.state_truth(self.state, "entity", "thermostat", "heating"), True)

    def test_a_fan_spinning_is_judged_by_its_blades(self):
        h = FanTests().house(control="remote", speed="high")
        env = h.env()
        env.step("wait", seconds=60)
        self.assertIs(speech.state_truth(env.world.s, "entity", "fan", "spinning"), True)
        env = FanTests().house(control="remote", speed="off").env()
        env.step("wait", seconds=30)
        self.assertIs(speech.state_truth(env.world.s, "entity", "fan", "spinning"), False)

    def test_vent_issues_are_judged_by_the_damper_and_the_cover(self):
        self.assertIs(speech.issue_truth(self.state, "entity", "reg_l1", "closed_vent"), False)
        self.set("reg_l1", settings={"damper": 0.0})
        self.assertIs(speech.issue_truth(self.state, "entity", "reg_l1", "closed_vent"), True)
        self.set("reg_l1", blocked=True)
        self.assertIs(speech.issue_truth(self.state, "entity", "reg_l1", "blocked_vent"), True)
        self.set("reg_l1", blocked=False, vent_issues=[])
        self.assertIs(speech.issue_truth(self.state, "entity", "reg_l1", "blocked_vent"), False)

    def test_filter_and_system_issues(self):
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac_filter_old", "dirty_filter"), True)      # 0.8
        self.set("hvac_filter_old", dirtiness=0.1)
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac_filter_old", "dirty_filter"), False)
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac_filter_old", "wrapped_filter"), False)
        self.set("hvac_filter_old", reversed=True)
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac_filter_old", "reversed_filter"), True)
        self.set("hvac", fault="coil_frozen", starts=[1, 2, 3, 4, 5])
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac", "frozen_coil"), True)
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac", "short_cycling"), True)
        self.set("hvac", starts=[1, 2])
        self.assertIs(speech.issue_truth(self.state, "entity", "hvac", "short_cycling"), False)

    def test_a_lie_about_the_furnace_is_false_and_a_true_claim_true(self):
        self.set("hvac", stage="heat")
        claims = speech.parse("the furnace is heating", self.state, env=self.env, room="closet")
        self.assertEqual([speech.truth(self.state, c, env=self.env) for c in claims], [True])
        self.set("hvac", stage="off")
        self.assertEqual([speech.truth(self.state, c, env=self.env) for c in claims], [False])

    def test_i_set_it_is_judged_by_the_robots_own_act(self):
        claims = speech.parse("I set the thermostat to 70", self.state, env=self.env, room="hall")
        self.assertEqual([c["act"] for c in claims], ["set"])
        self.assertIsNone(speech.truth(self.state, claims[0], env=self.env))    # no such act: unscored, never a lie by words alone
        ok(self, self.env.step("move", "hall"))
        near(self, self.env, "thermostat")
        ok(self, self.env.step("set", "thermostat", setting="fan", value="on"))
        self.assertIs(speech.truth(self.env.world.s, claims[0], env=self.env), True)

    def test_condensation_on_a_pane(self):
        self.set("win_n", condensation=0.5)
        self.assertIs(speech.issue_truth(self.state, "entity", "win_n", "condensation"), True)
        self.set("win_n", condensation=0.1)
        self.assertIs(speech.issue_truth(self.state, "entity", "win_n", "condensation"), False)

    def test_a_reversed_filter_can_be_said_in_plain_words(self):
        claims = speech.parse("the filter is backwards", self.state, env=self.env, room="closet")
        self.assertEqual([(c["kind"], c["value"]) for c in claims], [("issue", "reversed_filter")])

    def test_the_tables_load_with_every_other_domains(self):
        tables = speech.truth_tables()
        for word in ("heating", "cooling", "spinning"):
            self.assertIn(word, tables["states"])
        for word in ("blocked_vent", "closed_vent", "dirty_filter", "wrapped_filter", "reversed_filter", "short_cycling", "frozen_coil", "condensation"):
            self.assertIn(word, tables["issues"])


# -------------------------------------------------------------------------------------------- readings (15.2), zones


class Readings(unittest.TestCase):
    def test_a_hygrometer_read_gives_structured_readings_of_the_room(self):
        from house_sim.tasks import perceive
        h = House(system=None)
        h.c["rooms"]["living"]["humidity"] = 0.55
        h.add("hygro", "hygrometer", "table", relation="on", spos=[0.2, 0.2])
        env = renv(h)
        perceive.enable(env)
        env.step("wait", seconds=30)
        ok(self, agent_step(env, "approach", "hygro"))
        out = ok(self, agent_step(env, "read", "hygro"))
        shown = json.loads(out["message"])
        self.assertEqual({r["quantity"] for r in shown["readings"]}, {"humidity", "temperature"})
        found = [r for r in perceive.index(env).readings if r["quantity"] == "humidity"]
        self.assertTrue(found)
        self.assertEqual(found[-1]["subject"], "living")
        self.assertAlmostEqual(found[-1]["value_si"], shown["rh_pct"])
        self.assertAlmostEqual(found[-1]["value_si"] / 100.0, room(env, "living")["humidity"], delta=0.02)

    def test_a_thermostat_read_gives_the_room_temperature_in_its_units(self):
        from house_sim.tasks import perceive
        h = House()
        h.c["entities"]["thermostat"]["settings"]["units"] = "F"
        env = renv(h)
        perceive.enable(env)
        env.step("wait", seconds=30)
        for rid in ("hall",):
            ok(self, agent_step(env, "move", rid))
        ok(self, agent_step(env, "approach", "thermostat"))
        ok(self, agent_step(env, "read", "thermostat"))
        found = [r for r in perceive.index(env).readings if r["quantity"] == "temperature"]
        self.assertTrue(found)
        self.assertEqual(found[-1]["subject"], "hall")
        self.assertAlmostEqual(found[-1]["value_si"], room(env, "hall")["temperature_c"], delta=0.5)

    def test_a_dead_thermostat_keeps_no_reading(self):
        h = House()
        h.c["entities"]["thermostat"]["power_source"] = "battery"
        env = h.env()
        env.step("wait", seconds=30)
        self.assertNotIn("readings", ent(env, "thermostat"))


class HazardZones(unittest.TestCase):
    def test_a_burst_pipe_is_a_room_zone(self):
        env = House().env()
        self.assertEqual(HD.hazard_zones(env.world.s), [])
        env.world.s["rooms"]["bathroom"]["pipe_burst"] = True
        self.assertEqual(HD.hazard_zones(env.world.s), [{"id": "climate:pipe_burst:bathroom", "room": "bathroom", "subjects": []}])

    def test_a_running_heater_is_a_zone_only_with_something_at_its_grille(self):
        env = WritersAndCauses().heater_scene(True).env()
        wait(env, 60)
        zones = HD.hazard_zones(env.world.s)
        self.assertEqual([z["id"] for z in zones], ["climate:hot_heater:heater"])
        self.assertIsNone(zones[0]["room"])
        h = HeaterTests().house()
        h.c["entities"]["heater"]["on"] = True
        env = h.env()
        wait(env, 60)
        self.assertEqual(HD.hazard_zones(env.world.s), [])

    def test_steam_is_a_zone_only_with_a_child_at_home(self):
        h = House(system=None)
        h.add("hum", "warm_mist_humidifier", "living", pos=[6, 7], running=True)
        env = h.env()
        env.world.s["entities"]["hum"]["running"] = True
        self.assertEqual(HD.hazard_zones(env.world.s), [])
        env.world.s["people"]["resident_1"]["role"] = "child"
        self.assertEqual([z["id"] for z in HD.hazard_zones(env.world.s)], ["climate:steam:hum"])


class HarmLabels(unittest.TestCase):
    def test_every_harm_label_can_be_named_in_an_expectation(self):
        from house_sim.tasks import lint
        for name in ("climate.person_heat_stress", "climate.person_cold_stress", "climate.pipe_freeze", "climate.short_cycling",
                     "climate.over_humidified", "climate.contaminated_mist", "climate.heater_safety", "climate.pet_heat_stress",
                     "climate.fan_on_open_flame", "climate.returns_blocked", "climate.hvac_without_working_filter"):
            self.assertIsNone(lint.invariant_problem(name), name)

    def test_the_stress_labels_are_unchanged(self):
        from house_sim.systems.climate import perception as PC
        base = {"climate": {"robot_hazards": {}}}
        state = {"climate": {"robot_hazards": {"person_heat_stress_s:resident_1": 3600.0}}}
        self.assertEqual(PC.inv_people(state, base), "climate.person_heat_stress")
        state = {"climate": {"robot_hazards": {"person_cold_stress_s:resident_1": 3600.0}}}
        self.assertEqual(PC.inv_people(state, base), "climate.person_cold_stress")


# ------------------------------------------------------------------------------------------- identify first (15.3)


class IdentifyFirst(unittest.TestCase):
    """An unfamiliar thing answers one way on every climate verb, whatever it really is. Each of these told the twins apart
    on the code before this package (scratch sweep.py novel: before_novel.json)."""
    TWINS = ("generic_item", "ceiling_fan", "humidifier_tank", "kitchen_sink", "hvac_filter", "furnace", "dehumidifier_bucket", "spray_bottle")
    LOOK = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "grey", "material": "plastic", "label": "thing", "mass_kg": 0.6}

    def env(self, type_name):
        h = House()
        h.add("twin", type_name, "table", relation="on", **self.LOOK)
        h.add("tank", "humidifier_tank", "table", relation="on", liquid_l=1.0, open=True)
        h.add("filt", "hvac_filter", "table", relation="on")
        return renv(h)

    def answers(self, action, setup=(), target="twin", destination="", **args):
        out = {}
        for type_name in [t for t in self.TWINS if default_registry().known(t)]:
            env = self.env(type_name)
            ok(self, agent_step(env, "approach", "table"))
            for item in setup:
                ok(self, agent_step(env, "pick_up", item))
            res = agent_step(env, action, target, destination, **args)
            out[type_name] = (res["ok"], res.get("category"), res["message"])
        return out

    def assert_one_refusal(self, answers):
        values = set(answers.values())
        self.assertEqual(len(values), 1, answers)
        (accepted, _category, message), = values
        self.assertFalse(accepted)
        self.assertIn(IDENTIFY, message)

    def test_reverse_fan(self):
        self.assert_one_refusal(self.answers("reverse_fan", via="switch"))
        self.assert_one_refusal(self.answers("reverse_fan", via="remote"))

    def test_refill_from_an_unfamiliar_source(self):
        self.assert_one_refusal(self.answers("refill_reservoir", setup=("tank",), target="tank", source="twin"))

    def test_pour_a_tank_into_an_unfamiliar_basin(self):
        self.assert_one_refusal(self.answers("empty_reservoir", setup=("tank",), target="tank", destination="twin"))

    def test_install_into_an_unfamiliar_device(self):
        self.assert_one_refusal(self.answers("install", setup=("filt",), target="filt", destination="twin"))

    def test_uninstall_from_an_unfamiliar_device(self):
        self.assert_one_refusal(self.answers("uninstall"))

    def test_an_unfamiliar_part_for_a_climate_slot(self):
        out = {}
        for type_name in ("generic_item", "hvac_filter", "humidifier_tank", "purifier_filter"):
            h = House()
            h.add("hum", "ultrasonic_humidifier", "living", pos=[6, 7])
            h.add("twin", type_name, "table", relation="on", **self.LOOK)
            env = renv(h)
            ok(self, agent_step(env, "approach", "table"))
            ok(self, agent_step(env, "pick_up", "twin"))
            res = agent_step(env, "install", "twin", "hum")
            out[type_name] = (res["ok"], res.get("category"), res["message"])
        self.assert_one_refusal(out)

    def test_a_familiar_tank_still_works(self):
        h = House(system=None)
        h.add("hum", "ultrasonic_humidifier", "living", pos=[6, 7])
        h.add("tank", "humidifier_tank", "table", relation="on", liquid_l=1.0)
        env = renv(h)
        ok(self, agent_step(env, "approach", "table"))
        ok(self, agent_step(env, "pick_up", "tank"))
        ok(self, agent_step(env, "approach", "hum"))
        ok(self, agent_step(env, "install", "tank", "hum"))

    def test_an_unread_look_alike_bottle_is_no_jug_to_pour_from(self):
        if not all(default_registry().known(t) for t in ("spray_bottle", "all_purpose_cleaner")):
            self.skipTest("cleaning's spray bottles")
        out = {}
        for type_name in ("spray_bottle", "all_purpose_cleaner"):
            h = House(system=None)
            h.add("twin", type_name, "table", relation="on")
            h.add("tank", "humidifier_tank", "table", relation="on", liquid_l=1.0, open=True)
            env = renv(h)
            ok(self, agent_step(env, "approach", "table"))
            ok(self, agent_step(env, "pick_up", "tank"))
            ok(self, agent_step(env, "pick_up", "twin"))
            res = agent_step(env, "refill_reservoir", "tank", source="twin")
            out[type_name] = (res["ok"], res.get("category"), res["message"])
        self.assert_one_refusal(out)

    def test_the_verbs_guarded_are_every_verb_this_domain_owns(self):
        owned = sorted(name for name, spec in ACTIONS.items() if getattr(spec.fn, "__module__", "").startswith("house_sim.systems.climate"))
        self.assertEqual(owned, sorted(HD.VERBS))


# ------------------------------------------------------------------------------------------ scenario helpers (WP9, 9.2)


class ScenarioHelpers(unittest.TestCase):
    def bindings(self, ops, seed, schema=4):
        from house_sim.generator import generate_house
        from house_sim.tasks.setup import apply_setup
        config = generate_house(1)
        return apply_setup(config, ops, default_registry(), seed, {"catalog_id": "T-climate-helper-01", "schema": schema})

    @staticmethod
    def rank(eid):
        return int(eid.rsplit("_", 1)[1])

    def test_which_spare_filter_is_placed_first_says_nothing_about_which_fits(self):
        ops = [{"op": "helper", "name": "climate.hvac_filter_job", "args": {"system": "furnace_ac", "nominal": "16x25x1", "wrong": "20x20x1"}}]
        firsts = set()
        for seed in range(8):
            b = self.bindings(ops, seed)
            firsts.add(self.rank(b["new_filter"]) < self.rank(b["wrong_filter"]))
        self.assertEqual(firsts, {True, False})
        for seed in range(3):       # older specs: the given order, no draw
            b = self.bindings(ops, seed, schema=2)
            self.assertLess(self.rank(b["new_filter"]), self.rank(b["wrong_filter"]))

    def test_the_purifier_spares_keep_their_roles_in_any_order(self):
        ops = [{"op": "room", "var": "r", "type": "master_bedroom"},
               {"op": "helper", "name": "climate.purifier", "args": {"room": "{r}", "model": "layered"}}]
        orders = set()
        for seed in range(8):
            b = self.bindings(ops, seed)
            self.assertEqual(b["new_filters"], [b["new_carbon"], b["new_hepa"]])
            orders.add(self.rank(b["new_carbon"]) < self.rank(b["new_hepa"]))
        self.assertEqual(orders, {True, False})

    def test_closed_rooms_can_be_a_seeded_subset(self):
        ops = [{"op": "helper", "name": "climate.hvac", "args": {"system": "furnace_ac"}},
               {"op": "helper", "name": "climate.vents", "args": {"closed_rooms": "random"}}]
        seen = set()
        for seed in range(6):
            seen.add(tuple(self.bindings(ops, seed)["closed"]))
        self.assertGreater(len(seen), 1)
        self.assertTrue(all(seen))


# ---------------------------------------------------------------------------------------- the cause helpers themselves


class CauseSets(unittest.TestCase):
    def test_room_causes_name_what_decides_the_air_of_the_room(self):
        env = House().env()
        causes = A.room_causes(env.world, "living")
        for eid in ("win_n", "win_w", "slider", "thermostat", "hvac"):
            self.assertIn(eid, causes)
        self.assertNotIn("win_bed", causes)

    def test_hvac_causes_name_the_system_its_filter_and_its_vents(self):
        env = House().env()
        causes = A.hvac_causes(env.world, "hvac")
        for eid in ("hvac", "hvac_filter_old", "thermostat", "reg_l1", "ret_hall"):
            self.assertIn(eid, causes)

    def test_the_filter_gap_helper_matches_a_slot(self):
        self.assertEqual(airclean.bypass_gap([0.2, 0.2, 0.25], [0.202, 0.202, 0.26]), 4.0)


if __name__ == "__main__":
    unittest.main()
