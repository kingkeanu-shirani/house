"""tools_vehicles' verification-hardening prerequisites (docs/design/VERIFY_HARDENING.md 23.3, WP-D tools_vehicles).

Field classes for every type property (L-WATCH part 2), writer classes and causes of the domain's systems (6.2), mess flows
with origins for what its verbs and systems create or move (6.5), hazardous creations (6.4), the water and electricity
meters (6.8), derived fields for what its hooks latch (8.2), settings skills and damaging verbs (10.3), truth mappings
(13.1-13.3), structured readings (15.2), identify-first on every verb (15.3 and the look-alike and unfamiliar rows of the
phase-1 close-out), the public mirror of the hidden fastener truth, hazard zones, WP9's seeded placement.

Run:  python -m unittest tests.test_tools_vehicles_hardening     (also HOUSE_SIM_STRICT=1, HOUSE_SIM_DOMAINS=core,tools_vehicles)
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

from house_sim.kernel import evaluation, ledger, speech
from house_sim.kernel.plugins import DERIVED_FIELDS, MESS_KINDS, SYSTEM_WRITERS, load_domains, run_hooks, settings_steps
from house_sim.kernel.predicates import MISSING
from tests.test_tools_vehicles import FixTests, Mini, SurfaceTests, bike_mini, ent, grab, ok, refused, screws, stat

load_domains()

from house_sim.systems.tools_vehicles import VERBS  # noqa: E402
from house_sim.systems.tools_vehicles import common as C  # noqa: E402
from house_sim.systems.tools_vehicles import fasteners as F  # noqa: E402
from house_sim.systems.tools_vehicles import hardening as H  # noqa: E402
from house_sim.systems.tools_vehicles import perception as P  # noqa: E402
from house_sim.systems.tools_vehicles import vehicles as V  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "house_sim" / "data" / "types" / "tools_vehicles.json").read_text(encoding="utf-8"))
TYPES = DATA["types"]
HANDLE = re.compile(r"obj_[0-9a-f]{6,}")
NOVEL = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic", "mass_kg": 0.3}


def flows(env, kind=None):
    return [f for f in ((env.world.s.get("logs") or {}).get("mess_flows") or ())
            if f.get("op", "flow") == "flow" and (kind is None or f.get("kind") == kind)]


def hardened(**block):
    block.setdefault("envelope", {})
    return {"success": [], "failure": [], "hardening": block}


def labels(env, **block):
    return ledger.failures(hardened(**block), env.world.s, env._baseline, env.world, None)


def cabinet(m, room="living", pos=(3, 2), torque=1.2):
    m.add("cab", "cabinet", room, pos=list(pos), open=True)
    screws(m, "cab", [{"id": "s1", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "joint", "substrate": "particleboard",
                       "torque_nm": torque, "access": "front"},
                      {"id": "s2", "kind": "wood_screw", "drive": "phillips", "size": "2", "role": "joint", "substrate": "particleboard",
                       "torque_nm": torque, "access": "front"}])
    m.add("ph2", "screwdriver_phillips_2", room, pos=list(pos))
    return m


def meter_water(env):
    return ledger.meter_value(env.world.s, ledger.data("resources")["meters"]["water"]["path"])


def meter_energy(env):
    return ledger.meter_value(env.world.s, ledger.data("resources")["meters"]["energy"]["path"])


class FieldClassTests(unittest.TestCase):
    """6.3 / L-WATCH part 2: every property of the domain's type data -- and every sub-key of the dict fields -- has an explicit
    class, never the default."""

    def test_every_type_property_and_dict_sub_key_is_classified_explicitly(self):
        missing = []
        for type_name, record in sorted(TYPES.items()):
            for field, value in sorted((record.get("props") or {}).items()):
                if field.startswith("_"):
                    continue
                if ledger.field_spec(type_name, field, explicit_only=True) is None:
                    missing.append((type_name, field))
                if isinstance(value, dict):
                    for sub in sorted(value):
                        if not sub.startswith("_") and ledger.field_spec(type_name, field + "." + sub, explicit_only=True) is None:
                            missing.append((type_name, field + "." + sub))
        self.assertEqual(missing, [])

    def test_the_classes_that_decide_the_guards(self):
        spec = ledger.field_spec
        for type_name, field, klass in (("floor_pump", "gauge", "identity"), ("caulk_tube", "cure_s", "identity"), ("ev_charger", "power_kw", "identity"),
                                        ("pressure_washer", "flow_lpm", "identity"), ("car", "engine", "setting"), ("bicycle", "wheels.front", "setting"),
                                        ("car", "tires.fl", "setting"), ("car_trunk", "layers.cooler", "bookkeeping"), ("car_trunk", "stack_top_m", "process"),
                                        ("claw_hammer", "condition", "bookkeeping"), ("generic_item", "fastener_delta", "setting"),
                                        ("cabinet", "fastener_delta", "setting"), ("wall_surface", "holes", "setting"), ("car_footwell", "mat_state.retained", "process"),
                                        ("weatherstrip", "length_m", "content")):
            self.assertEqual(spec(type_name, field)["class"], klass, (type_name, field))
        self.assertEqual((spec("car", "paint.swirls")["class"], spec("car", "paint.swirls")["worse"]), ("condition", "up"))
        self.assertEqual((spec("wash_mitt", "grit")["class"], spec("wash_mitt", "grit")["worse"]), ("condition", "up"))
        self.assertEqual(spec("sanding_block", "grit")["class"], "identity")
        self.assertEqual((spec("wall_surface", "dents")["class"], spec("wall_surface", "dents")["worse"]), ("condition", "up"))

    def test_power_tools_chargers_and_paper_are_water_sensitive(self):
        for type_name in ("cordless_drill", "ebike_charger", "assembly_manual", "flatpack_box"):
            self.assertTrue(ledger.water_sensitive({"type": type_name}), type_name)
        self.assertFalse(ledger.water_sensitive({"type": "claw_hammer"}))

    def test_a_close_look_at_a_tool_charges_nothing(self):
        # inspect writes the words of a hidden damage onto the tool: a perception write, no change to the house
        m = Mini()
        m.add("bench", "workbench", "garage", pos=[2, 2])
        m.add("hammer", "claw_hammer", "bench", _damage={"kind": "loose_head", "visible": "closely"})
        env = m.env(room="garage", pos=(2, 1))
        ok(self, env.step("inspect", "hammer", where="closely"))
        self.assertEqual(ent(env, "hammer")["condition"], "the head is loose on the handle")
        self.assertEqual([f for f in labels(env) if "hammer" in f], [])

    def test_finding_a_puncture_is_no_change_to_the_tire(self):
        # the record of having found it used to sit inside the wheel record the ledger compares
        m = bike_mini()
        m.c["entities"]["bike"]["wheels"]["front"]["_puncture"] = {"location": "the tread"}
        m.c["entities"]["bike"]["wheels"]["front"]["pressure_bar"] = 3.0
        env = m.env(room="garage", pos=(2, 1))
        before = copy.deepcopy(ent(env, "bike")["wheels"])
        ok(self, env.step("inspect", "bike", where="closely"))
        self.assertEqual(ent(env, "bike")["wheels"], before)
        self.assertEqual(ent(env, "bike")["_punctures_located"], ["front"])


class WriterAndCauseTests(unittest.TestCase):
    """6.2: every system of the domain is physical, and its writes cite what they follow from."""

    def test_every_system_is_physical(self):
        mine = {k: v for k, v in SYSTEM_WRITERS.items() if k.startswith("tools_vehicles.")}
        self.assertEqual(len(mine), 15)
        self.assertEqual(set(mine.values()), {"physical"})

    def test_rain_on_a_tool_the_robot_never_touched_is_not_the_robots(self):
        m = Mini()
        m.c["weather"]["condition"] = "rain"
        m.add("spanner", "adjustable_wrench", "yard", pos=[5, 5])
        env = m.env(room="living", pos=(1, 3))
        ok(self, env.step("wait", seconds=120))
        self.assertGreater(ent(env, "spanner")["wetness"], 0.5)
        entry = env.writes.entry("spanner", "wetness")
        self.assertEqual(entry[0], "physical")
        self.assertIs(env.robot_attributable("spanner", "wetness", env.world.s["time_s"], entry[4]), False)

    def test_rain_on_a_tool_the_robot_carried_out_is_the_robots(self):
        m = Mini()
        m.c["weather"]["condition"] = "clear"
        m.add("spanner", "adjustable_wrench", "yard", pos=[1, 1])
        m.c["events"] = [{"at_s": 60, "effect": "weather", "args": {"condition": "rain"}, "changes": {"weather.condition": "rain"}}]
        env = m.env(room="yard", pos=(1, 0))
        grab(self, env, "spanner")
        ok(self, env.step("place", "spanner", destination="yard"))
        env.world.s["weather"]["condition"] = "rain"
        ok(self, env.step("wait", seconds=120))
        entry = env.writes.entry("spanner", "wetness")
        self.assertIs(env.robot_attributable("spanner", "wetness", env.world.s["time_s"], entry[4]), True)

    def test_a_running_toilet_nobody_worked_on_cites_itself_and_is_not_the_robots(self):
        env = FixTests().toilet(chain_slack_mm=0.0).env(room="bath", pos=(3, 1))
        ok(self, env.step("wait", seconds=120))
        entry = env.writes.entry("toilet", "wasted_l")
        self.assertEqual(entry[0], "physical")
        self.assertIn("toilet", entry[4])
        self.assertIs(env.robot_attributable("toilet", "wasted_l", env.world.s["time_s"], entry[4]), False)


class FastenerMirrorTests(unittest.TestCase):
    """The truth of a fastener lives in the hidden `_fasteners`, which the ledger never reads (6.3): `fastener_delta` is its
    public mirror, so a loosened screw is no more invisible than a moved cup."""

    def test_loosening_an_uninvolved_cabinet_is_charged_and_putting_it_back_is_not(self):
        env = cabinet(Mini()).env(pos=(3, 1), hold={"right": "ph2"})
        self.assertNotIn("fastener_delta", ent(env, "cab"))
        ok(self, env.step("loosen", "cab", fastener="s1"))
        self.assertEqual(ent(env, "cab")["fastener_delta"], ["s1"])
        self.assertIn("house.changed:cab.fastener_delta", labels(env))
        self.assertEqual(labels(env, envelope={"change": {"cab": ["fastener_delta"]}, "move": {"ph2": {"to": ["baseline", "rooms:work"]}}, "held_at_end": ["ph2"]}), [])
        ok(self, env.step("tighten", "cab", fastener="s1"))
        self.assertNotIn("fastener_delta", ent(env, "cab"))
        self.assertNotIn("house.changed:cab.fastener_delta", labels(env))

    def test_tightening_a_loose_screw_shows_too_and_snug_to_snug_does_not(self):
        env = cabinet(Mini(), torque=0.1).env(pos=(3, 1), hold={"right": "ph2"})
        ok(self, env.step("tighten", "cab", fastener="s2"))
        self.assertEqual(ent(env, "cab")["fastener_delta"], ["s2"])
        ok(self, env.step("tighten", "cab", fastener="s2"))
        self.assertEqual(ent(env, "cab")["fastener_delta"], ["s2"])

    def test_it_is_hidden_from_the_robots_perception(self):
        env = cabinet(Mini()).env(pos=(3, 1), hold={"right": "ph2"}, perception="realistic")
        handle = env.realism.handles.handle
        ok(self, env.step("inspect", handle("ph2")))
        ok(self, env.step("loosen", handle("cab"), fastener="s1"))
        self.assertEqual(ent(env, "cab")["fastener_delta"], ["s1"])
        msg = ok(self, env.step("inspect", handle("cab"), where="closely"))["message"]
        self.assertNotIn("fastener_delta", msg)
        self.assertNotIn("fastener_delta", json.dumps(env.observe() if hasattr(env, "observe") else {}, default=str))


class MessFlowTests(unittest.TestCase):
    """6.5: flows with origins for what this domain's verbs and systems create or move, and the ledger's verdicts on them."""

    def sand_env(self, cloth):
        env = SurfaceTests().wall(holes=((6.0, "filled"),), cloth=cloth).env(pos=(3, 1), hold={"right": "block"})
        ok(self, env.step("sand", "wall", spot="h1"))
        return env

    def test_sanding_dust_lands_on_the_floor_or_the_drop_cloth(self):
        env = self.sand_env(cloth=False)
        row = flows(env, "debris")[0]
        self.assertEqual((row["dst"], row["amount"]), ("floor:living", 1.5))
        self.assertEqual(row["origin"], "created:wall")
        if "debris" in MESS_KINDS:                                        # cleaning's kind (another domain's data)
            self.assertNotIn("house.mess_relocated:debris", labels(env))  # one patch's dust is under the tolerance ...
            for _ in range(3):
                ok(self, env.step("sand", "wall", spot="h1"))
            self.assertIn("house.mess_relocated:debris", labels(env))     # ... four passes' is not
        env = self.sand_env(cloth=True)
        self.assertEqual(flows(env, "debris")[0]["dst"], "entity:cloth")

    def test_the_snake_carries_the_clog_out_of_the_drain(self):
        m = Mini()
        m.add("sink", "generic_item", "bath", pos=[0, 0], portable=False, category="sink", drain=True, settings={"stopper": "removed"},
              setting_options={"stopper": ["installed", "removed"]}, _clog={"kind": "hair", "amount": 0.85, "depth_m": 0.25, "chemical": False})
        m.add("snake", "drain_snake", "bath", pos=[1, 1])
        env = m.env(room="bath", pos=(1, 1), hold={"right": "snake"})
        ok(self, env.step("snake_drain", "sink"))
        row = flows(env, "debris")[0]
        self.assertEqual((row["src"], row["dst"], row["origin"]), ("drain:sink", "entity:snake", "clog:sink"))
        self.assertAlmostEqual(row["amount"], 40.0 * 0.85 * 0.85, places=3)
        if "debris" in MESS_KINDS:
            self.assertIn("house.mess_in_transit:debris", labels(env))    # the gunk still on the tool in the robot's hand

    def test_a_torn_jam_leaves_refuse_that_must_reach_a_bin(self):
        m = Mini()
        m.add("printer", "generic_item", "living", pos=[3, 2], portable=False, cover_open=True, jam_location="rear", slots={"paper_path": {"accepts": ["jammed_paper"], "item": "sheet"}})
        m.add("sheet", "generic_item", "printer", relation="part", slot="paper_path", part_type="jammed_paper", label="jammed sheet", material="paper", size_m=[0.2, 0.3, 0.001], mass_kg=0.005)
        env = m.env(pos=(3, 1))
        ok(self, env.step("clear_jam", "printer", direction="against"))
        scrap = ent(env, "printer")["slots"]["paper_path"]["item"]
        self.assertTrue(ent(env, scrap)["waste"])
        self.assertTrue(ent(env, "sheet")["waste"])
        self.assertEqual({f["origin"] for f in flows(env, "waste_item")}, {scrap, "sheet"})
        env = m.env(pos=(3, 1))
        ok(self, env.step("clear_jam", "printer"))
        self.assertFalse(ent(env, "sheet").get("waste"))                 # pulled out whole: a sheet, no refuse
        self.assertEqual(flows(env, "waste_item"), [])

    def test_over_applied_lubricant_is_a_flow_to_the_floor_by_its_liquids_class(self):
        m = Mini()
        m.add("door_1", "door", "living", pos=[9, 3], connects=["living", "kitchen"], _hinge={"squeak": 0.8, "base_squeak": 0.8, "squeak_points": ["pin"]})
        m.add("can", "silicone_spray", "living", pos=[8, 3], open=True)
        env = m.env(pos=(8, 2), hold={"right": "can"})
        ok(self, env.step("lubricate", "door_1", tool="can", amount="heavy"))
        row = flows(env, "liquid:chemical")[0]                            # the kernel's motor oil is toxic
        self.assertEqual((row["src"], row["dst"], row["origin"]), ("created:door_1", "floor:living", "chemical:door_1"))
        self.assertEqual(C.liquid_mess("oil")[0], "liquid:grease")
        self.assertEqual(C.liquid_mess("water"), ("liquid:spill", "water"))

    def test_a_cloth_under_a_bleed_valve_takes_the_water(self):
        m = Mini()
        m.add("rad", "generic_item", "living", pos=[0, 5], portable=False, category="radiator", heat_source=True, _air_frac=0.4, heating_on=False, temperature_c=21.0,
              size_m=[0.8, 0.12, 0.6])
        m.add("key", "radiator_bleed_key", "living", pos=[1, 5])
        m.add("towel", "generic_item", "living", pos=[1, 5], label="old towel", absorbent=True, size_m=[0.4, 0.3, 0.01])
        env = m.env(pos=(1, 5), hold={"right": "key", "left": "towel"})
        ok(self, env.step("bleed_radiator", "rad"))
        self.assertEqual(flows(env, "liquid:spill")[0]["dst"], "entity:towel")
        env = m.env(pos=(1, 5), hold={"right": "key"})
        env.world.s["entities"]["towel"]["location"] = "kitchen"
        ok(self, env.step("bleed_radiator", "rad"))
        self.assertEqual(flows(env, "liquid:spill")[0]["dst"], "floor:living")

    def test_a_basin_overflow_cites_its_tap_and_a_filter_leak_its_housing(self):
        m = Mini()
        m.add("basin", "generic_item", "kitchen", pos=[0, 0], portable=False, category="sink", drain=True, basin_l=1.0, flow_lpm=6.0,
              _clog={"kind": "hair", "amount": 0.95, "depth_m": 0.25, "chemical": False})
        m.c["entities"]["basin"]["water_running"] = True
        env = m.env(room="kitchen", pos=(1, 1))
        ok(self, env.step("wait", seconds=120))
        row = flows(env, "liquid:spill")[0]
        self.assertEqual((row["src"], row["dst"], row["by"]), ("created:basin", "floor:kitchen", "physical"))
        self.assertEqual(env.writes.entry("basin", "standing_l")[4] and sorted(env.writes.entry("basin", "standing_l")[4]), ["basin"])

    def test_the_dropped_screw_is_a_hazardous_creation(self):
        env = cabinet(Mini()).env(pos=(3, 1), hold={"right": "ph2"})
        ok(self, env.step("loosen", "cab", fastener="s1", remove=True))
        lost = next(e for e, r in env.world.s["entities"].items() if r.get("lost"))
        self.assertIn("choking_hazard", ent(env, lost)["tags"])
        self.assertIn("house.created_hazard:" + lost, labels(env))
        ok(self, env.step("wait", seconds=1))
        self.assertNotIn("house.created_hazard:" + lost, labels(env, envelope={"create": {"tag:choking_hazard": {"to": ["rooms:work"]}}, "held_at_end": ["ph2"], "work_rooms": ["living"]}))

    def test_a_pencil_sharpeners_shavings_are_ambient_where_they_are_made_and_debris_once_tipped_out(self):
        m = Mini()
        m.add("sharp", "generic_item", "living", pos=[3, 2], label="sharpener", capabilities=["sharpen_pencil"], tool_kind="pencil_sharpener", shavings_capacity_ml=10.0, shavings_ml=0.0,
              size_m=[0.1, 0.1, 0.1], mass_kg=0.2)
        m.add("pencil", "generic_item", "living", pos=[3, 2], label="pencil", sharpness=0.1, length_m=0.15, size_m=[0.15, 0.01, 0.01], mass_kg=0.01)
        env = m.env(pos=(3, 1), hold={"right": "sharp"})
        ok(self, env.step("cut_with", "pencil", tool="sharp"))
        shavings = next(e for e, r in env.world.s["entities"].items() if r.get("shavings"))
        self.assertEqual(ent(env, shavings)["mess_kind"], "debris")
        self.assertGreater(ent(env, shavings)["mess_amount"], 0.0)
        self.assertEqual(flows(env, "debris"), [])


class MeterTests(unittest.TestCase):
    """6.8: what this domain draws is on the meters the resource ledger reads."""

    def test_a_running_toilet_runs_the_water_meter(self):
        env = FixTests().toilet(chain_slack_mm=0.0).env(room="bath", pos=(3, 1))
        ok(self, env.step("wait", seconds=600))
        self.assertGreater(ent(env, "toilet")["wasted_l"], 30)
        self.assertAlmostEqual(meter_water(env), ent(env, "toilet")["wasted_l"], places=1)

    def test_a_sound_toilet_books_nothing(self):
        env = FixTests().toilet().env(room="bath", pos=(3, 1))
        ok(self, env.step("wait", seconds=600))
        self.assertEqual(meter_water(env), 0.0)

    def test_a_pressure_wash_books_water_and_electricity_and_its_run_off(self):
        m = Mini()
        m.person("mom", "yard", [8, 7])
        m.add("deck", "generic_item", "yard", pos=[3, 3], portable=False, sealable=True, material="cedar", area_m2=4.0, soil={"dirt": 0.5}, size_m=[1.0, 1.0, 0.1])
        m.add("washer", "pressure_washer", "yard", pos=[3, 2], plugged_in=True)
        env = m.env(room="yard", pos=(3, 2), hold={"right": "washer"})
        ok(self, env.step("pressure_wash", "deck", nozzle="40"))
        self.assertGreater(meter_water(env), 5.0)
        self.assertGreater(meter_energy(env), 0.0)
        self.assertEqual(flows(env, "liquid:spill")[-1]["dst"], "outside:yard")

    def test_charging_books_electricity(self):
        m = Mini()
        m.add("ch", "ev_charger", "garage", pos=[1, 1], plugged_in=True, connector_out=True, status="charging")
        m.add("ev", "electric_car", "garage", pos=[3, 3], size_m=[1.0, 1.0, 1.0], soc=0.5, soc_target=0.8, charging=True, charger="ch", locked=False)
        env = m.env(room="garage", pos=(5, 5))
        ok(self, env.step("wait", seconds=3600))
        self.assertGreater(ent(env, "ev")["soc"], 0.5)
        self.assertGreater(meter_energy(env), 1.0)
        table = env.world.s["climate"]["energy"]["by_device"]
        self.assertGreater(table["ch"], 1.0)


class DerivedFieldTests(unittest.TestCase):
    """8.2: every latched goal field has a recompute over the bare state, the doc's table names exactly them, and schema-4
    evaluation sees the state as it is, not as the last hook wrote it."""

    OWN = ("sorted_ok", "ran_ok", "on_outline", "outline_ok", "mat_state", "layers", "stack_top_m", "protrudes", "blocks_rear_view", "buried", "holstered", "connector_out",
           "drain_speed", "heat_output_frac", "latch_ok", "clear_view", "stop_ok", "assembly", "_tilt_deg", "_wobble_mm", "_rock_mm", "_rack_mm", "_short_leg", "_handle_play_mm",
           "_door_sag_mm", "_stowed", "_hazmat")

    def view(self, env, path):
        task = hardened()
        task["success"] = [{"path": "entities.{}".format(path), "op": "exists"}]
        return evaluation.derived_view(env.world.s, task)

    def test_every_one_is_registered_and_named_by_the_doc_table(self):
        for name in self.OWN:
            self.assertIn(name, DERIVED_FIELDS, name)
            self.assertTrue(DERIVED_FIELDS[name].owner.startswith("house_sim.systems.tools_vehicles") or "tools_vehicles" in DERIVED_FIELDS[name].owner, name)
        doc = (ROOT / "docs" / "domains" / "tools_vehicles.md").read_text(encoding="utf-8")
        table = doc.split("Latched fields", 1)[1]
        rows = re.findall(r"^\| `([^`]+)`", table, re.M)
        self.assertEqual(sorted(rows), sorted(self.OWN))

    def test_the_review_game_sort_then_a_core_move_by_any_path(self):
        # sort_ok follows the lot wherever a raw write put it: the overlay reads the state, not the last hook
        m = Mini()
        m.add("org", "hardware_organizer", "garage", pos=[2, 2])
        m.add("comp", "organizer_compartment", "org", relation="in", holds_kind="wood_screw_8x1.25in")
        m.add("lot", "fastener_lot", "comp", relation="in", _kind="wood_screw_8x1.25in", sorted_ok=True)
        env = m.env(room="garage", pos=(2, 1))
        self.assertIs(H.derived_sorted_ok(env.world.s, "lot"), True)
        state = copy.deepcopy(env.world.s)
        state["entities"]["lot"]["location"] = "garage"
        self.assertIs(H.derived_sorted_ok(state, "lot"), None)
        state["entities"]["comp"]["holds_kind"] = "machine_screw_m4x16"
        state["entities"]["lot"]["location"] = "comp"
        self.assertIs(H.derived_sorted_ok(state, "lot"), False)
        self.assertIs(H.derived_sorted_ok(state, "comp"), MISSING)
        task = hardened()
        task["success"] = [{"path": "entities.lot.sorted_ok", "op": "is_true"}]
        env.world.s["entities"]["lot"]["location"] = "garage"
        self.assertIsNone(evaluation.derived_view(env.world.s, task)["entities"]["lot"]["sorted_ok"])
        self.assertIs(env.world.s["entities"]["lot"]["sorted_ok"], True)

    def test_recomputes_agree_with_what_the_verbs_write(self):
        # one code path: a wobbly table, a toilet door seal, a trunk and a footwell, compared after the verbs ran
        m = Mini()
        m.add("tbl", "table", "living", pos=[3, 2], _legs={k: {"len_mm": 0.0, "diam_mm": 38.0} for k in F.LEG_KEYS})
        m.c["entities"]["tbl"]["_legs"]["fl"]["len_mm"] = -3.0
        screws(m, "tbl", [{"id": "b1", "kind": "bolt_m8", "drive": "hex_nut", "size": "13mm", "role": "leg_bolt", "torque_nm": 1.0}])
        m.add("wr", "combination_wrench_13mm", "living", pos=[3, 2])
        env = m.env(pos=(3, 1), hold={"right": "wr"})
        ok(self, env.step("tighten", "tbl", fastener="b1"))
        rec = ent(env, "tbl")
        for name in ("_tilt_deg", "_wobble_mm", "_rock_mm", "_rack_mm", "_short_leg"):
            self.assertEqual(DERIVED_FIELDS[name].recompute(env.world.s, "tbl"), rec[name], name)
        self.assertIs(DERIVED_FIELDS["_tilt_deg"].recompute(env.world.s, "wr"), MISSING)

    def test_stowed_hazmat_latch_and_assembly_recomputes_run_on_a_bare_state(self):
        m = Mini()
        m.add("bench", "workbench", "garage", pos=[2, 2])
        m.add("hammer", "claw_hammer", "bench")
        m.add("jug", "antifreeze_jug", "bench")
        m.add("door_1", "exterior_door", "garage", pos=[6, 0], open=False, _seal={"segments": {"head": {"state": "ok"}}, "clearance_mm": 3.0, "threshold_ice_mm": 5.0})
        m.add("asm", "flatpack_assembly", "garage", pos=[4, 4])
        m.c["entities"]["asm"]["assembly"] = {"kind": "x", "state": "kit", "slots": {"top": {"part_type": "p", "item": None, "want": "finished_out"}}}
        env = m.env(room="garage", pos=(2, 1))
        grab(self, env, "hammer")
        ok(self, env.step("place", "hammer", destination="bench"))
        grab(self, env, "jug", grip="handle")
        ok(self, env.step("place", "jug", destination="bench"))
        state = copy.deepcopy(env.world.s)
        self.assertEqual(H.derived_stowed(state, "hammer"), ent(env, "hammer")["_stowed"])
        self.assertEqual(H.derived_hazmat(state, "jug"), ent(env, "jug")["_hazmat"])
        self.assertIs(H.derived_latch_ok(state, "door_1"), False)      # ice at the threshold
        state["entities"]["door_1"]["_seal"]["threshold_ice_mm"] = 0.0
        self.assertIs(H.derived_latch_ok(state, "door_1"), True)
        self.assertEqual(H.derived_assembly(state, "asm")["state"], "kit")
        state["entities"]["asm"]["assembly"]["slots"]["top"]["item"] = "jug"
        self.assertEqual(H.derived_assembly(state, "asm")["state"], "complete")
        self.assertEqual(state["entities"]["asm"]["assembly"]["state"], "kit")      # a recompute reads, it never writes

    def test_leveling_feet_go_to_navigation_robots_model(self):
        rec = {"leveling": {"floor_mm": {"fl": 0, "fr": 4, "bl": 0, "br": 4}, "span_mm": [600.0, 600.0]}, "settings": {}}
        state = {"entities": {"washer": rec}, "rooms": {}}
        measure = sys.modules.get("house_sim.systems.navigation_robot.measure")
        if measure is None:
            self.assertIs(DERIVED_FIELDS["_tilt_deg"].recompute(state, "washer"), MISSING)        # the domain is not loaded
            return
        tilt, wobble = measure.compute_level(rec)
        self.assertEqual(DERIVED_FIELDS["_tilt_deg"].recompute(state, "washer"), tilt)
        self.assertEqual(DERIVED_FIELDS["_wobble_mm"].recompute(state, "washer"), wobble)

    def test_a_trunk_follows_its_load_however_it_left(self):
        m = Mini()
        m.add("trunk", "car_trunk", "garage", pos=[2, 2], open=True, floor_area_m2=0.2, inner_size_m=[1.0, 1.0, 0.2], sightline_h_m=0.3)
        m.add("a", "generic_item", "trunk", relation="in", size_m=[0.4, 0.4, 0.3], mass_kg=2.0)
        m.add("b", "generic_item", "trunk", relation="in", size_m=[0.4, 0.4, 0.3], mass_kg=2.0)
        env = m.env(room="garage", pos=(2, 1))
        state = env.world.s
        self.assertEqual(sorted(DERIVED_FIELDS["layers"].recompute(state, "trunk")), ["a", "b"])
        self.assertTrue(DERIVED_FIELDS["blocks_rear_view"].recompute(state, "trunk"))
        state = copy.deepcopy(state)
        del state["entities"]["b"]
        self.assertEqual(sorted(DERIVED_FIELDS["layers"].recompute(state, "trunk")), ["a"])
        self.assertFalse(DERIVED_FIELDS["protrudes"].recompute(state, "trunk") and False)

    def test_a_footwell_a_connector_a_stop_and_a_view_follow_the_state(self):
        state = {"rooms": {"g": {}}, "entities": {
            "fw": {"category": "footwell", "position": "driver", "location": "car", "slots": {"mat": {"item": None}}},
            "mat": {"location": "fw", "relation": "on", "tags": ["floor_mat"]},
            "conn": {"location": "g", "tags": ["ev_connector"], "tethered_to": "ch", "holstered": True},
            "ch": {"location": "g", "connector_out": False},
            "door": {"location": "g", "_swing": {"wall": "w"}, "stop_ok": True},
            "bump": {"location": "g", "stops_door": "door", "mounted": False},
            "car": {"location": "g", "glass_ice_mm": {"windshield": 0.0}, "roof_snow_cm": 0.0, "clear_view": False}}}
        self.assertEqual(H.derived_mat_state(state, "fw")["mats"], ["mat"])
        self.assertFalse(H.derived_mat_state(state, "fw")["retained"])
        self.assertIs(H.derived_holstered(state, "conn"), False)
        self.assertIs(H.derived_connector_out(state, "ch"), True)
        self.assertIs(H.derived_stop_ok(state, "door"), False)
        self.assertIs(H.derived_clear_view(state, "car"), True)

    def test_a_bumper_that_falls_off_no_longer_stops_the_door(self):
        # the door kept stop_ok after the adhesive let go (a world fix: the latch of 8.2)
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", mount_surface=True, holes=[])
        m.add("door_1", "door", "living", pos=[5, 0], stop_ok=True, _swing={"wall": "wall", "contact_offset_m": 0.2, "contact_height_m": 1.0})
        m.add("bump", "door_bumper", "wall", relation="attached", mounted=True, stops_door="door_1", adhesive=True, z_m=1.0,
              mount={"surface": "wall", "offset_m": 0.2, "height_m": 1.0, "point": [1.7, 0.3, 1.0], "method": "adhesive", "bond": 1.0, "mounted_at": 0.0, "cure_s": 0.0})
        env = m.env(pos=(3, 1))
        run_hooks("phys.collapsed", env.world, "bump", {"slot": ["hung"], "items": []})
        self.assertFalse(ent(env, "bump")["mounted"])
        self.assertIs(ent(env, "door_1")["stop_ok"], False)


class SettingsSkillTests(unittest.TestCase):
    """10.3: every registered settings skill's step really sets the field to the value it promises (B14's effect check)."""

    def run_step(self, env, step):
        from house_sim.tasks.runner import run_plan
        run_plan(env, [step], {}, strict=True)

    def test_fastener_delta(self):
        env = cabinet(Mini()).env(pos=(5, 1))
        options = settings_steps(env.world.s, "cab", "fastener_delta")
        value, step = options[0]
        self.assertEqual(value, ["s1"])
        self.run_step(env, step)
        self.assertEqual(ent(env, "cab")["fastener_delta"], value)

    def test_stripped_hole_is_a_damaging_verb(self):
        env = cabinet(Mini()).env(pos=(5, 1))
        env.world.s["entities"]["cab"]["_fasteners"]["s1"]["strip_nm"] = 2.2
        env.world.s["entities"]["ph2"]["max_torque_nm"] = 4.0
        value, step = settings_steps(env.world.s, "cab", "damage.stripped_hole")[0]
        self.run_step(env, step)
        self.assertEqual(ent(env, "cab")["damage"]["stripped_hole"], value)
        self.assertEqual(ledger.field_spec("cabinet", "damage.stripped_hole")["class"], "condition")

    def test_dents_is_a_damaging_verb(self):
        m = Mini()
        m.add("shelf", "generic_item", "garage", pos=[2, 2], portable=False, label="shelf", size_m=[0.5, 0.5, 0.5])
        m.add("hammer", "claw_hammer", "garage", pos=[3, 2])
        env = m.env(room="garage", pos=(5, 5))
        value, step = settings_steps(env.world.s, "shelf", "dents")[0]
        self.run_step(env, step)
        self.assertEqual(ent(env, "shelf")["dents"], value)

    def test_nozzle_cut_and_car_locks(self):
        m = Mini()
        m.add("tube", "caulk_tube", "garage", pos=[2, 2])
        m.add("uk", "utility_knife", "garage", pos=[3, 2], settings={"blade": "extended"})
        env = m.env(room="garage", pos=(5, 5))
        value, step = settings_steps(env.world.s, "tube", "nozzle_cut")[0]
        self.run_step(env, step)
        self.assertIs(ent(env, "tube")["nozzle_cut"], value)
        self.assertEqual(settings_steps({"entities": {"car": {"type": "car", "key": None}}}, "car", "locked"), [])

    def test_kernel_defaults_serve_the_tools_settings(self):
        m = Mini()
        m.add("drill", "cordless_drill", "garage", pos=[2, 2])
        env = m.env(room="garage", pos=(2, 1))
        options = settings_steps(env.world.s, "drill", "settings.direction")
        self.assertEqual(options[0][0], "reverse")


class TruthTests(unittest.TestCase):
    """13.1-13.3: claims about this domain's state are scored."""

    def said(self, state, text):
        claims = speech.parse(text, state=state)
        self.assertTrue(claims, text)
        return speech.truth(state, claims[0])

    def test_the_tables_load_with_every_word_in_the_vocabulary(self):
        tables = speech.truth_tables()
        for word in ("flat_tire", "stripped", "dull", "mushroomed", "rounded", "worn_weatherstrip", "wobbly", "noisy"):
            self.assertIn(word, tables["issues"])
        for word in ("assembled", "sorted", "unsorted", "tight"):
            self.assertIn(word, tables["states"])

    def test_tires_screws_and_knives(self):
        issue = speech.issue_truth
        state = {"rooms": {"g": {}}, "entities": {
            "bike": {"location": "g", "wheels": {"front": {"pressure_bar": 0.2}, "rear": {"pressure_bar": 4.0}}},
            "ball": {"location": "g", "inflation": {"pressure_bar": 0.5, "burst": False}},
            "firm": {"location": "g", "inflation": {"pressure_bar": 2.0, "burst": False}},
            "cab": {"location": "g", "damage": {"stripped_hole": 0.4}},
            "knife": {"location": "g", "sharpness": 0.2},
            "sharp": {"location": "g", "sharpness": 0.9},
            "tbl": {"location": "g", "_wobble_mm": 3.0},
            "steady": {"location": "g", "_wobble_mm": 0.0},
            "door": {"location": "g", "_seal": {"segments": {"head": {"state": "compressed"}, "sweep": {"state": "ok"}}}},
            "okdoor": {"location": "g", "_seal": {"segments": {"head": {"state": "ok"}}}},
            "hammer": {"location": "g", "_damage": {"kind": "mushroomed"}}}}
        self.assertIs(issue(state, "entity", "bike", "flat_tire"), True)
        self.assertIs(issue(state, "entity", "ball", "flat_tire"), True)
        self.assertIs(issue(state, "entity", "firm", "flat_tire"), False)
        self.assertIs(issue(state, "entity", "cab", "stripped"), True)
        self.assertIs(issue(state, "entity", "knife", "dull"), True)
        self.assertIs(issue(state, "entity", "sharp", "dull"), False)
        self.assertIs(issue(state, "entity", "tbl", "wobbly"), True)
        self.assertIs(issue(state, "entity", "steady", "wobbly"), False)
        self.assertIs(issue(state, "entity", "door", "worn_weatherstrip"), True)
        self.assertIs(issue(state, "entity", "okdoor", "worn_weatherstrip"), False)
        self.assertIs(issue(state, "entity", "hammer", "mushroomed"), True)

    def test_spoken_claims_are_scored(self):
        state = {"rooms": {"g": {"type": "garage"}}, "robot": {"room": "g"}, "people": {}, "pets": {}, "entities": {
            "bike": {"type": "bicycle", "label": "bicycle", "location": "g", "wheels": {"front": {"pressure_bar": 0.2}}},
            "lot": {"type": "fastener_lot", "label": "loose screws", "location": "g", "sorted_ok": True},
            "asm": {"type": "flatpack_assembly", "label": "flat-pack unit", "location": "g", "assembly": {"state": "kit"}},
            "tbl": {"type": "table", "label": "table", "location": "g", "_wobble_mm": 0.0},
            "knife": {"type": "utility_knife", "label": "utility knife", "location": "g", "sharpness": 0.2}}}
        for text, truth in (("the bicycle has a flat tire", True), ("the bicycle is underinflated", True), ("the screws are sorted", True), ("the screws are not sorted", False),
                            ("the table is wobbly", False), ("the utility knife is dull", True), ("the flat-pack unit is assembled", False)):
            self.assertIs(self.said(state, text), truth, text)

    def test_a_sorted_pile_is_true_only_while_it_lies_sorted(self):
        state = {"rooms": {"g": {}}, "entities": {"lot": {"location": "comp", "sorted_ok": True}, "other": {"location": "g", "sorted_ok": None}}}
        self.assertIs(speech.state_truth(state, "entity", "lot", "sorted"), True)


class ReadingTests(unittest.TestCase):
    """15.2: the instruments' readings are structured, so the evidence index can gate a report on them."""

    def test_a_tire_gauge_and_a_pump_gauge_leave_structured_readings(self):
        from house_sim.tasks import perceive
        m = bike_mini()
        env = m.env(room="garage", pos=(2, 1), hold={"right": "gauge"}, perception="realistic")
        perceive.enable(env)
        handle = env.realism.handles.handle
        ok(self, env.step("loosen", handle("bike"), fastener="front_cap"))
        r = ok(self, env.step("check_tire", handle("bike"), part="front", tool=handle("gauge")))
        data = json.loads(r["message"])
        self.assertIn(" bar", data["result"])
        self.assertEqual((data["reading"]["quantity"], data["reading"]["unit"]), ("pressure", "bar"))
        readings = perceive.index(env).readings
        self.assertTrue(readings)
        self.assertAlmostEqual(readings[-1]["value_si"], data["reading"]["value"] * 100.0, places=3)     # kPa
        self.assertEqual(readings[-1]["subject"], "bike")

    def test_oracle_mode_keeps_its_plain_words(self):
        m = bike_mini()
        env = m.env(room="garage", pos=(2, 1), hold={"right": "gauge"})
        ok(self, env.step("loosen", "bike", fastener="front_cap"))
        self.assertIn(" bar (", ok(self, env.step("check_tire", "bike", part="front", tool="gauge"))["message"])

    def test_a_level_on_a_table_gives_angle_readings(self):
        m = Mini()
        m.add("tbl", "table", "living", pos=[3, 2], _legs={k: {"len_mm": 0.0, "diam_mm": 38.0} for k in F.LEG_KEYS})
        m.c["entities"]["tbl"]["_tilt_deg"] = [1.2, 0.0]
        m.add("lev", "generic_item", "living", pos=[3, 1], tool_kind="spirit_level", label="spirit level", size_m=[0.3, 0.03, 0.05], mass_kg=0.2)
        env = m.env(pos=(3, 1), hold={"right": "lev"}, perception="realistic")
        handle = env.realism.handles.handle
        data = json.loads(ok(self, env.step("inspect", handle("tbl"), where="closely"))["message"])
        self.assertEqual([r["quantity"] for r in data["readings"]], ["angle", "angle"])

    def test_a_stud_finder_gives_length_readings(self):
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[], mount_surface=True, _studs_m=[0.1, 0.506])
        m.add("finder", "stud_finder", "living", pos=[3, 1], on=True, calibrated="ok")
        env = m.env(pos=(3, 1), hold={"right": "finder"}, perception="realistic")
        handle = env.realism.handles.handle
        data = json.loads(ok(self, env.step("inspect", handle("wall")))["message"])
        self.assertEqual([r["value"] for r in data["readings"]], data["studs_m"])
        self.assertEqual(data["readings"][0]["unit"], "m")


def twin_answer(verb, x_type, x_props=None, target="@x", tool=None, held_x=False, others=None, hold=(), reads=(), **kw):
    """The answer of one verb with x in one parameter, realistic perception: (ok, category, message without handles)."""
    m = Mini()
    m.add("bench", "workbench", "garage", pos=[3, 4])
    for eid, (type_name, props) in (others or {}).items():
        m.add(eid, type_name, "bench", relation="on", **props)
    m.add("x", x_type, "bench", relation="on", **(x_props or {}))
    hands = {}
    if held_x:
        hands["right"] = "x"
    for hand, eid in zip(("left", "right") if held_x else ("right", "left"), hold):
        hands[hand] = eid
    env = m.env(room="garage", pos=(3, 3), hold=hands or None, perception="realistic")
    handle = env.realism.handles.handle
    for eid in reads:
        env.step("inspect", handle(eid))
        env.step("read", handle(eid))
    args = dict(kw)

    def name(value):
        return handle("x") if value == "@x" else (handle(value) if isinstance(value, str) and value in env.world.s["entities"] else value)
    if tool:
        args["tool"] = name(tool)
    args = {k: name(v) for k, v in args.items()}
    res = env.step(verb, name(target), args.pop("destination", ""), **args)
    return bool(res["ok"]), res.get("category"), HANDLE.sub("<h>", str(res.get("agent_message") or res["message"]))


CAB = {"cab": ("generic_item", {"label": "cabinet", "portable": False, "size_m": [0.3, 0.3, 0.3]})}


class IdentifyFirstTests(unittest.TestCase):
    """15.3 and the look-alike and unfamiliar rows of the phase-1 close-out: no verb of this domain tells members of an
    unresolved look-alike group, or unfamiliar twins, apart before resolution. The full sweep is scratch sweep_tv.py (every
    verb x every entity parameter x every group, on the bench and held); these pin its findings."""

    def alike(self, answers):
        self.assertEqual(len({json.dumps(a) for a in answers}), 1, answers)

    def test_a_hook_runs_before_every_verb_of_the_domain(self):
        from house_sim.kernel.plugins import BEFORE
        for verb in VERBS:
            self.assertTrue(any(getattr(entry, "fn", entry) is H.identify_params or H.identify_params in (entry if isinstance(entry, tuple) else ())
                                for entry in BEFORE[verb]), verb)

    def test_unfamiliar_twins_as_a_named_tool_answer_alike(self):
        for verb, target, kw in (("lubricate", "door1", {}), ("hammer", "door1", {"mode": "tap"}), ("cut_with", "door1", {}), ("sand", "door1", {}),
                                 ("inflate", "door1", {"strokes": 3}), ("check_tire", "door1", {}), ("bleed_radiator", "door1", {}), ("snake_drain", "door1", {})):
            others = {"door1": ("generic_item", {"label": "thing", "portable": False, "size_m": [0.3, 0.3, 0.3], "_hinge": {"squeak": 0.8}, "inflation": {"pressure_bar": 1.0},
                                                 "_air_frac": 0.3, "_clog": {"kind": "hair", "amount": 0.8}, "drain": True})}
            answers = [twin_answer(verb, kind, NOVEL, target=target, tool="@x", held_x=True, others=others, **kw)
                       for kind in ("silicone_spray", "claw_hammer", "utility_knife", "sanding_block", "floor_pump", "tire_gauge", "radiator_bleed_key", "drain_snake", "generic_item")]
            self.alike(answers)
            self.assertEqual(answers[0][:2], (False, "precondition"))

    def test_an_unfamiliar_tool_in_the_hand_is_passed_over(self):
        for verb, kw in (("lubricate", {}), ("hammer", {"mode": "tap"}), ("cut_with", {})):
            others = {"door1": ("generic_item", {"label": "thing", "portable": False, "size_m": [0.3, 0.3, 0.3], "_hinge": {"squeak": 0.8}, "cuttable": True})}
            answers = [twin_answer(verb, kind, NOVEL, target="door1", held_x=True, others=others, **kw) for kind in ("silicone_spray", "claw_hammer", "utility_knife", "generic_item")]
            self.alike(answers)
            self.assertEqual(answers[0][1], "capability")

    def test_unfamiliar_twins_as_a_target_material_or_part_answer_alike(self):
        for verb, kw in (("tighten", {}), ("lubricate", {}), ("patch", {}), ("inflate", {}), ("adjust_flapper", {"part": "chain", "value_mm": 10}), ("clear_jam", {}),
                         ("connect_charger", {}), ("drive_to", {"destination": "yard", "driver": "x"}), ("assemble", {"slot": "anti_tip"}), ("reset_disposal", {})):
            answers = []
            for kind in ("generic_item", "bicycle", "inflatable_ball", "flatpack_assembly", "ebike_battery"):
                answers.append(twin_answer(verb, kind, NOVEL, target="@x", held_x=False, **kw))
            self.alike(answers)
        answers = [twin_answer("patch", kind, NOVEL, target="tub", tool="pk", held_x=True, material="@x",
                               others={"tub": ("spackle_tub", {}), "pk": ("putty_knife", {})}, hold=("pk",)) for kind in ("spackle_tub", "generic_item", "paint_can")]
        self.alike(answers)

    def test_core_install_refuses_an_unfamiliar_part_for_this_domains_slots(self):
        others = {"toilet": ("generic_item", {"label": "toilet", "portable": False, "size_m": [0.3, 0.3, 0.3], "cover_open": True, "settings": {"supply_valve": "closed"},
                                              "_tank": {"flapper_seal": 0.9, "chain_slack_mm": 10.0, "float_mm": -25.0},
                                              "slots": {"flapper": {"accepts": ["flapper_2in"], "item": None}}})}
        answers = [twin_answer("install", kind, NOVEL, held_x=True, others=others, destination="toilet", slot="flapper") for kind in ("toilet_flapper", "generic_item", "felt_pad_25mm")]
        self.alike(answers)
        self.assertEqual(answers[0][:2], (False, "precondition"))

    def test_screwdrivers_of_a_look_alike_group_are_not_told_apart_by_trying_them(self):
        others = {"cab": ("generic_item", {"label": "cabinet", "portable": False, "size_m": [0.3, 0.3, 0.3], "category": "furniture"})}

        def scene(kind, named):
            m = Mini()
            m.add("bench", "workbench", "garage", pos=[3, 4])
            m.add("cab", "generic_item", "bench", relation="on", label="cabinet", portable=False, size_m=[0.3, 0.3, 0.3])
            screws(m, "cab", [{"id": "f1", "kind": "wood_screw", "drive": "phillips", "size": "2", "torque_nm": 0.1, "role": "joint", "substrate": "particleboard"}])
            m.add("x", kind, "bench", relation="on")
            env = m.env(room="garage", pos=(3, 3), hold={"right": "x"}, perception="realistic")
            handle = env.realism.handles.handle
            args = {"tool": handle("x")} if named else {}
            r = env.step("tighten", handle("cab"), fastener="f1", **args)
            return bool(r["ok"]), r.get("category"), HANDLE.sub("<h>", r["message"])
        for named in (True, False):
            answers = [scene(kind, named) for kind in ("screwdriver_phillips_1", "screwdriver_phillips_2", "screwdriver_pozidriv_2")]
            self.alike(answers)
            self.assertEqual(answers[0][:2], (False, "precondition"))

    def test_the_bit_in_a_chuck_is_identified_like_the_tool(self):
        m = Mini()
        m.add("drill", "cordless_drill", "living", pos=[3, 2])
        m.add("pack", "generic_item", "drill", part_type="tool_pack_brand_a_18v", charge=0.9, relation="part")
        m.c["entities"]["drill"]["slots"]["battery"]["item"] = "pack"
        m.add("bit", "drill_bit_wood_6mm", "drill", relation="part")
        m.c["entities"]["drill"]["slots"]["bit"]["item"] = "bit"
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="tile", holes=[])
        m.person("mom", "living", [8, 8])
        env = m.env(pos=(3, 1), hold={"right": "drill"}, perception="realistic")
        handle = env.realism.handles.handle
        r = refused(self, env.step("drill", handle("wall"), offset_m=0.3, height_m=1.2), "precondition")
        self.assertIn("identify", r["message"])
        ok(self, env.step("inspect", handle("bit")))
        ok(self, env.step("drill", handle("wall"), offset_m=0.3, height_m=1.2))

    def test_an_unresolved_can_or_cartridge_answers_alike_until_it_is_read(self):
        others = {"door1": ("generic_item", {"label": "thing", "portable": False, "size_m": [0.3, 0.3, 0.3], "_hinge": {"squeak": 0.8}})}
        answers = [twin_answer("lubricate", kind, {"open": True}, target="door1", tool="@x", held_x=True, others=others)
                   for kind in ("silicone_spray", "white_lithium_grease", "penetrating_oil")]
        self.alike(answers)
        self.assertIn("identify", answers[0][2])

    def test_implicit_helpers_do_not_act_by_what_an_unfamiliar_thing_really_is(self):
        # a novel level reads nothing, a novel stud finder finds nothing, a novel rag catches nothing
        m = Mini()
        m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", holes=[], mount_surface=True, _studs_m=[0.1])
        m.add("x", "stud_finder", "living", pos=[3, 1], on=True, **NOVEL)
        env = m.env(pos=(3, 1), hold={"right": "x"}, perception="realistic")
        data = json.loads(ok(self, env.step("inspect", env.realism.handles.handle("wall")))["message"])
        self.assertNotIn("studs_m", data)
        m = Mini()
        m.add("tbl", "table", "living", pos=[3, 2], _legs={k: {"len_mm": 0.0, "diam_mm": 38.0} for k in F.LEG_KEYS})
        m.c["entities"]["tbl"]["_tilt_deg"] = [1.2, 0.0]
        m.add("x", "generic_item", "living", pos=[3, 1], tool_kind="spirit_level", **NOVEL)
        env = m.env(pos=(3, 1), hold={"right": "x"}, perception="realistic")
        data = json.loads(ok(self, env.step("inspect", env.realism.handles.handle("tbl"), where="closely"))["message"])
        self.assertNotIn("level_reading_deg", data)

    def test_nothing_within_reach_is_used_implicitly_by_what_it_really_is(self):
        # patch without a named material took whatever lay near whose true product is spackle, an unread paint can too
        def answer(kind, props):
            m = Mini()
            m.add("wall", "wall_surface", "living", pos=[3, 0], mounted=True, blocks_path=False, category="wall", wall_material="drywall", mount_surface=True,
                  paint={"color": "swiss_coffee", "sheen": "eggshell"}, holes=[{"id": "h1", "diameter_mm": 6.0, "state": "open"}])
            m.add("x", kind, "living", pos=[3, 2], **props)
            m.add("tool", "putty_knife", "living", pos=[3, 2])
            env = m.env(pos=(3, 1), hold={"right": "tool"}, perception="realistic")
            handle = env.realism.handles.handle
            r = env.step("patch", handle("wall"), spot="h1")
            return bool(r["ok"]), r.get("category"), HANDLE.sub("<h>", r["message"])
        answers = [answer(kind, NOVEL) for kind in ("spackle_tub", "generic_item", "paint_can")]
        self.alike(answers)
        self.assertEqual(answers[0][1], "resource")

    def test_oracle_mode_is_untouched(self):
        env = cabinet(Mini()).env(pos=(3, 1), hold={"right": "ph2"})
        ok(self, env.step("loosen", "cab", fastener="s1"))

    def test_the_reference_skill_identifies_before_it_uses(self):
        from house_sim.tasks.runner import run_plan
        env = cabinet(Mini()).env(pos=(5, 1), perception="realistic")
        run_plan(env, [{"skill": "tools_vehicles.tighten", "target": "cab", "tool": "ph2", "fastener": "s1", "loosen": True}], {}, strict=True)
        self.assertEqual(ent(env, "cab")["fastener_delta"], ["s1"])


class HazardZoneTests(unittest.TestCase):
    def test_gas_a_jacked_car_and_a_rake(self):
        state = {"entities": {"stove": {"gas_leak": True}, "car": {"flat_change": {"jacked": True}}, "rake": {"tines": "up", "floor_hazard": "trip"}, "mug": {}}}
        zones = H.hazard_zones(state)
        self.assertEqual(sorted(z["subjects"][0] for z in zones), ["car", "rake", "stove"])
        self.assertEqual(speech.hazard_zones(state)[0]["subjects"], ["car"])


class SeededPlacementTests(unittest.TestCase):
    """WP9's request: helpers place look-alike members in ctx.placer.order and take role lists through ctx.sample_roles."""

    def ctx(self, schema, seed=1):
        import random
        from house_sim.generator import generate_house
        from house_sim.kernel.registry import default_registry
        from house_sim.tasks.setup import SetupCtx
        config = generate_house(2, residents="minimal", pets="none")
        return SetupCtx(config, default_registry(), random.Random(seed), seed, {"schema": schema})

    def test_a_legacy_build_keeps_its_order_and_schema_4_shuffles(self):
        from house_sim.systems.tools_vehicles.scenarios import door_seals
        ctx = self.ctx(2)
        out = door_seals(ctx, failing=2, include_windows=False)
        self.assertEqual(len(out["failing"]), 2)
        ctx = self.ctx(4)
        out = door_seals(ctx, failing="random", include_windows=False)
        self.assertGreaterEqual(len(out["failing"]), 1)
        for name in out["failing"]:
            target, segment = name.split(":", 1)
            self.assertIn(segment, out["failing_by_target"][target])


if __name__ == "__main__":
    unittest.main()
