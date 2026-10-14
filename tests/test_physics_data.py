"""Tier-1 physics data layer (PHYS-DATA): the data files, property resolution, per-instance variation, liquids,
floors and the lawbook (docs/design/TIER1_PHYSICS.md 4 and 12.1, TIER1_INTEGRATION.md 2.3).

The tests use only core types or hand-made records, so they also pass with HOUSE_SIM_DOMAINS=core; DataAnchorTests
check the anchors on the loaded domains' shipped types and skip a type a core-only run lacks. Domain tables the data
copies (manipulation's friction and floor tables, cleaning's soil kinds) are read from source with ast, without
importing a domain."""
import ast
import json
import math
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from house_sim.kernel import plugins
from house_sim.kernel.phys import lawbook, props as P
from house_sim.kernel.registry import BASE_PROPS, default_registry
from house_sim.kernel.schema import normalize

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "house_sim" / "data" / "physics"

# portable types whose bulk density (mass / bounding box) is legitimately below 5 kg/m3: hollow, inflated or
# soft-sided things whose mass is the wrapper alone
LIGHT_EXEMPT = {
    "balloon", "beach_ball", "exercise_ball",                                     # inflated
    "donation_bag", "shopping_bag_plastic", "plastic_yard_bag", "garment_bag", "laundry_bag", "leaf_bag",
    "gift_bag", "paper_grocery_bag", "mesh_laundry_bag", "reusable_shopping_bag", "poly_mailer", "grocery_bag",
    "multipack", "paper_towel_pack", "toilet_paper_pack",                         # soft-sided, mass of the wrapper
    "air_pillows", "bubble_wrap", "mesh_food_cover", "paper_coffee_filter",       # hollow shapes
}
# data gaps owned by the domains (TIER1_INTEGRATION 6, "mass/size"): fixed in the adoption round
OWNER_GAPS = {
    "shred_pile": "admin_inventory", "tights": "textiles_laundry", "styrofoam_block": "cleaning",
    "small_parts": "manipulation", "jigsaw_blade": "tools_vehicles", "cigarette_butt": "cleaning",
    "twist_tie": "cleaning",
}
ABSTRACT_FURNITURE = {"furniture", "surface_furniture", "storage_furniture", "openable_storage"}   # never spawned


def module_table(relpath, name):
    """A literal module-level table of a domain source file, read with ast (no import, no registration)."""
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def rec(**fields):
    """A plain record on BASE_PROPS defaults (type generic_item unless given)."""
    out = dict(BASE_PROPS, type="generic_item", is_a=["portable_object", "object"], portable=True)
    out.update(fields)
    return out


def tiny_state(rooms=None, entities=None):
    rooms = rooms or {"kitchen": {"size_m": [4.0, 5.0], "floor_material": "tile", "exits": ["den"]},
                      "den": {"size_m": [3.5, 4.0], "floor_material": "carpet", "exits": ["kitchen"]}}
    config = {"schema_version": 2, "start_room": sorted(rooms)[0], "rooms": rooms, "entities": entities or {}}
    return normalize(config, default_registry())


class DataFilesTests(unittest.TestCase):
    def test_every_class_row_has_every_column_with_the_right_kind(self):
        columns = set(P.MATERIALS["columns"])
        optional = {"compressible", "mu_wet", "wet_strength", "floats"}
        modes = {"shatter", "chip", "crack", "bruise", None}
        for name, row in P.MATERIALS["classes"].items():
            self.assertTrue(columns - optional <= set(row), name)
            self.assertTrue(set(row) - {"source", "anchor"} <= columns, name)
            self.assertIn(row["break_mode"], modes, name)
            self.assertEqual(row["brittle"], row["break_mode"] == "shatter", name)
            self.assertEqual(row["break_mode"] is None, row["drop_tol_m"] is None, name)
            for key in ("rho", "abs", "soft", "wsens"):
                self.assertGreaterEqual(row[key], 0, name)
            self.assertTrue(0 <= row["soft"] <= 1 and 0 <= row["wsens"] <= 1, name)
            self.assertTrue(row["source"], name)

    def test_aliases_and_category_fallbacks_name_real_classes(self):
        classes = set(P.MATERIALS["classes"])
        for material, entry in P.MATERIALS["aliases"].items():
            cls = entry if isinstance(entry, str) else entry["class"]
            self.assertTrue(cls is None or cls in classes, material)
            if not isinstance(entry, str):
                self.assertTrue(entry["source"], material)
        for key, cls in P.MATERIALS["category_material"].items():
            self.assertIn(cls, classes, key)
        for level in ("types", "nouns", "categories", "tags"):
            for name, row in P.SUPPORTS[level].items():
                self.assertTrue(row.get("source"), name)
                if "material" in row:
                    self.assertTrue(P.class_known(row["material"]), name)
                if "shape" in row:
                    self.assertIn(row["shape"], P.SHAPES, name)

    def test_floor_liquid_and_law_rows_are_complete(self):
        floor_columns = set(P.FLOORS["columns"])
        for name, row in P.FLOORS["floors"].items():
            self.assertEqual(set(row) - {"source"}, floor_columns, name)
        for alias, target in P.FLOORS["aliases"].items():
            self.assertIn(target, P.FLOORS["floors"], alias)
        liquid_columns = set(P.LIQUIDS["columns"])
        self.assertEqual(set(P.LIQUIDS["default"]), liquid_columns)
        for name, row in P.LIQUIDS["liquids"].items():
            self.assertTrue(set(row) - {"source"} <= liquid_columns, name)
            self.assertTrue(row["rho"] > 0 and row["visc_cp"] > 0 and row["film_mm"] > 0, name)
            self.assertIn(row["floor_hazard"], ("wet", "slip", "sticky"), name)
        for alias, target in P.LIQUIDS["aliases"].items():
            self.assertIn(target if isinstance(target, str) else target["liquid"], P.LIQUIDS["liquids"], alias)

    def test_spill_residues_are_cleaning_soil_kinds(self):
        soils = set(module_table("house_sim/systems/cleaning/model.py", "SOILS"))
        for name, row in P.LIQUIDS["liquids"].items():
            if row.get("residue") is not None:
                self.assertIn(row["residue"], soils, name)

    def test_tables_copied_from_manipulation_are_unchanged(self):
        """TIER1_PHYSICS 4.3 and 4.5: friction, floor hardness, scratch and foot tables come from manipulation."""
        common = "house_sim/systems/manipulation/common.py"
        to_class = {"metal": "steel", "fabric": "cotton"}
        for material, mu in module_table(common, "FRICTION").items():
            self.assertEqual(P.MATERIALS["classes"][to_class.get(material, material)]["mu"], mu, material)
        floors = P.FLOORS["floors"]
        for table, column in (("FLOOR_HARDNESS", "impact_hardness"), ("FLOOR_SCRATCH", "scratch"),
                              ("FLOOR_FRICTION", "mu")):
            for floor, value in module_table(common, table).items():
                self.assertEqual(floors[P.floor_kind(floor)][column], value, (table, floor))
        kinds = P.FLOORS["feet"]["kinds"]
        for foot, value in module_table(common, "FOOT_SCRATCH").items():
            self.assertEqual(kinds[{"metal": "steel", "fabric": "cotton", "felt": "felt"}.get(foot, foot)]["scratch"],
                             value, foot)
        self.assertEqual(dict(P.MATERIALS["surface_state_mu"]["values"]), module_table(common, "SURFACE_STATE_MU"))

    def test_setup_py_ships_the_five_tier1_data_globs(self):
        tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
        globs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "package_data":
                globs = ast.literal_eval(node.value)["house_sim"]
        for pattern in ("data/physics/*.json", "data/robot/*.json", "data/calendar/*.json", "data/vocab/*.json",
                        "data/generator/*.json"):
            self.assertIn(pattern, globs)

    def test_data_files_are_read_only_and_load_within_budget(self):
        with self.assertRaises(TypeError):
            P.MATERIALS["classes"]["glass"]["drop_tol_m"] = 9.0
        with self.assertRaises(TypeError):
            P.SUPPORTS["types"]["bookshelf"] = {}
        names = ("materials.json", "liquids.json", "floors.json", "supports.json")
        best = min(_timed(lambda: [P._load(name) for name in names] + [lawbook._load()]) for _ in range(5))
        self.assertLess(best, 0.060, "data load {:.1f} ms (budget 20 ms, test bound 3x)".format(best * 1e3))

    def test_data_files_use_lf_line_endings(self):
        for path in sorted(DATA.glob("*.json")):
            self.assertNotIn(b"\r\n", path.read_bytes(), path.name)


def _timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


class RegistryCoverageTests(unittest.TestCase):
    """TIER1_PHYSICS 12.1 over every registry type (the domains loaded in this process)."""

    @staticmethod
    def records_or_none():
        reg = default_registry()
        return [(name, reg.make(name, "kitchen")) for name in reg.names()]

    @classmethod
    def setUpClass(cls):
        reg = default_registry()
        cls.records = {name: reg.make(name, "kitchen") for name in reg.names()}
        cls.phys = {name: P.props_of(record) for name, record in cls.records.items()}

    def test_every_material_string_maps_to_a_class(self):
        unmapped = sorted({r.get("material") for r in self.records.values() if not P.class_known(r.get("material"))})
        self.assertEqual(unmapped, [])

    def test_every_type_yields_finite_positive_size_and_portable_types_a_positive_mass(self):
        for name, phys in self.phys.items():
            self.assertTrue(all(math.isfinite(v) and v > 0 for v in phys.size_m), name)
            self.assertTrue(math.isfinite(phys.mass_kg) and phys.mass_kg >= 0, name)
            if self.records[name].get("portable"):
                self.assertGreater(phys.mass_kg, 0, name)

    def test_bulk_density_is_plausible_or_explained(self):
        bad = []
        for name, phys in self.phys.items():
            record = self.records[name]
            if not (record.get("portable") or record.get("category") == "furniture") or name in ABSTRACT_FURNITURE:
                continue
            if not record.get("portable") and phys.mass_kg == 0:
                continue                          # built-in furniture (closets): part of the building, no own mass
            w, d, h = phys.size_m
            rho = phys.mass_kg / (w * d * h)
            if not 5.0 <= rho <= 9000.0 and name not in LIGHT_EXEMPT and name not in OWNER_GAPS:
                bad.append((name, round(rho, 1)))
        self.assertEqual(bad, [])

    def test_every_type_resolves_a_well_formed_record(self):
        classes = set(P.MATERIALS["classes"])
        for name, p in self.phys.items():
            self.assertIn(p.cls, classes, name)
            self.assertIn(p.shape, P.SHAPES, name)
            self.assertTrue(0 <= p.softness <= 1 and 0 <= p.com_frac <= 1 and 0 < p.mouth_frac <= 1, name)
            for value in (p.drop_tol_m, p.crush_kg, p.heat_limit_c, p.melt_c, p.shock_k):
                self.assertGreater(value, 0, name)
            for value in (p.rating_kg, p.shelf_rating_kg, p.hang_kg, p.handle_kg):
                self.assertTrue(value is None or value >= 0, name)

    def test_rated_supports_resolve_their_ratings(self):
        shelves = {"bookshelf": 25, "shelf_unit": 90, "closet_shelving": 25, "cabinet": 30, "upper_cabinet": 20,
                   "filing_cabinet": 30, "wardrobe": 40, "closet": 40}
        surfaces = {"wall_shelf": 12, "side_table": 20, "nightstand": 15, "coffee_table": 50, "desk": 70,
                    "dining_table": 100, "tv_stand": 45, "counter": 200, "chair": 113, "dresser": 40,
                    "window_sill": 10, "table": 50}
        checked = 0
        for table, field in ((shelves, "shelf_rating_kg"), (surfaces, "rating_kg")):
            for name, kg in table.items():
                if name in self.phys:
                    self.assertEqual(getattr(self.phys[name], field), kg, name)
                    checked += 1
        self.assertGreaterEqual(checked, 15)      # the core types alone carry 15 of them
        for name, phys in self.phys.items():      # every type that can hold shelves of items is rated per shelf
            if self.records[name].get("shelf_heights") and self.records[name].get("category") == "furniture":
                self.assertIsNotNone(phys.shelf_rating_kg, name)

    def test_types_without_a_material_fall_back_by_category(self):
        expect = {"book": "paper", "towel": "cotton", "furniture": "engineered_wood", "food": "organic",
                  "light": "electronics"}
        for name, record in self.records.items():
            category = record.get("category")
            if category in expect and record.get("material") == "plastic" and \
                    not P._declares_material(name, tuple(record["is_a"])) and name not in P.SUPPORTS["types"]:
                self.assertEqual(self.phys[name].cls, expect[category], name)


class AnchorTests(unittest.TestCase):
    """The behaviour anchors of TIER1_PHYSICS 4.3-4.6 that the data must produce."""

    def cls(self, name):
        return P.MATERIALS["classes"][name]

    def test_drop_anchors(self):
        tile, carpet = P.FLOORS["floors"]["tile"]["impact_hardness"], P.FLOORS["floors"]["carpet"]["impact_hardness"]
        glass = P.props_of(rec(material="glass"))
        self.assertGreater(0.9 * tile, glass.drop_tol_m)              # a tumbler from counter height onto tile breaks
        self.assertLessEqual(0.3 * tile, glass.drop_tol_m)            # ... from 0.3 m it survives
        self.assertLessEqual(0.9 * carpet, glass.drop_tol_m)          # ... and onto carpet
        self.assertAlmostEqual(P.props_of(rec(material="crystal")).drop_tol_m, 0.25)   # stemware
        plate = P.props_of(rec(material="ceramic"))
        self.assertGreater(0.75 * tile, plate.drop_tol_m)
        self.assertLess(0.75 * carpet, plate.drop_tol_m)
        phone = P.props_of(rec(material="pcb", category="phone"))
        self.assertLessEqual(1.0 * tile, phone.drop_tol_m)            # phones survive 1 m
        egg = P.props_of(rec(type="hen_egg", is_a=["food_item"], material="organic"))
        self.assertAlmostEqual(egg.drop_tol_m, 0.03)
        self.assertEqual(egg.break_mode, "shatter")

    def test_thermal_shock_anchors(self):
        self.assertTrue(40 <= self.cls("glass")["shock"] <= 50)
        self.assertEqual(self.cls("borosilicate")["shock"], 160)
        self.assertEqual(self.cls("tempered_glass")["shock"], 150)
        boiling_into_cold_jar = 100 - 4
        self.assertGreater(boiling_into_cold_jar, P.props_of(rec(material="glass")).shock_k)
        self.assertLess(boiling_into_cold_jar, P.props_of(rec(material="pyrex")).shock_k)

    def test_absorption_anchors(self):
        towel = P.props_of(rec(material="cotton", mass_kg=0.5, size_m=[0.4, 0.3, 0.05]))    # a folded bath towel
        self.assertTrue(2.5 * 0.5 <= towel.absorb_l <= 3.0 * 0.5)
        for material in ("sponge", "cellulose"):
            sponge = P.props_of(rec(material=material, mass_kg=0.02))
            self.assertTrue(10 * 0.02 <= sponge.absorb_l <= 20 * 0.02, material)
        towel_roll = P.props_of(rec(type="paper_towel_roll", material="paper", mass_kg=0.25, size_m=[0.15, 0.15, 0.28]))
        self.assertTrue(5 * 0.25 <= towel_roll.absorb_l <= 8 * 0.25)
        self.assertAlmostEqual(P.props_of(rec(material="microfiber", mass_kg=1.0, size_m=[0.4, 0.4, 0.1])).absorb_l, 4.0)
        self.assertAlmostEqual(P.props_of(rec(material="pva", mass_kg=1.0, size_m=[0.4, 0.4, 0.1])).absorb_l, 12.0)

    def test_finish_and_melt_anchors(self):
        self.assertEqual(P.props_of(rec(material="wood")).heat_limit_c, 60)
        self.assertEqual(P.props_of(rec(material="quartz")).heat_limit_c, 150)
        self.assertGreaterEqual(P.props_of(rec(material="granite")).heat_limit_c, 300)
        self.assertGreaterEqual(P.props_of(rec(material="tile")).heat_limit_c, 300)
        self.assertEqual(P.props_of(rec(material="wax")).melt_c, 50)
        self.assertEqual(P.props_of(rec(material="crayon")).melt_c, 55)
        self.assertEqual(P.props_of(rec(material="steel")).melt_c, math.inf)
        blanket = P.props_of(rec(material="fiberglass"))
        self.assertFalse(blanket.combustible)
        self.assertGreaterEqual(blanket.heat_limit_c, 500)

    def test_cardboard_and_soap_columns(self):
        self.assertAlmostEqual(P.props_of(rec(material="cardboard")).wet_strength, 0.4)
        self.assertAlmostEqual(P.props_of(rec(material="soap")).mu_wet, 0.1)

    def test_support_rating_and_people_anchors(self):
        types = P.SUPPORTS["types"]
        self.assertEqual(types["climbing_aid"]["rating_kg"], 90)     # ANSI A14.2 type III, 200 lb
        self.assertEqual(types["chair"]["rating_kg"], 113)           # BIFMA 250 lb
        self.assertEqual(types["upper_cabinet"]["shelf_rating_kg"], 20)
        roles = P.SUPPORTS["people"]["roles"]
        self.assertEqual((roles["adult"]["lift_kg"], roles["teen"]["lift_kg"], roles["elderly"]["lift_kg"]), (23, 15, 10))
        self.assertEqual((roles["adult"]["push_n"], roles["teen"]["push_n"], roles["elderly"]["push_n"]), (200, 120, 80))
        self.assertEqual(roles["child"]["lift_kg"], 0)
        fillers = P.SUPPORTS["fillers"]
        self.assertEqual({k: fillers[k]["props"]["mass_kg"] for k in ("books", "cans", "boxes", "towels")},
                         {"books": 1.0, "cans": 0.4, "boxes": 3.0, "towels": 0.5})

    def test_liquid_anchors_support_the_pour_rate_law(self):
        water, honey, oil = (P.liquid_props(n)["visc_cp"] for n in ("water", "honey", "oil"))
        rate = lambda visc: min(1.0, math.sqrt(20.0 / visc))       # TIER1_PHYSICS 5.7
        self.assertAlmostEqual(rate(water) / rate(honey), 22.4, delta=0.5)
        self.assertAlmostEqual(rate(water) / rate(oil), 1.7, delta=0.1)
        self.assertEqual(P.LIQUIDS["min_spill_l"]["value"], 0.005)


class PropsResolutionTests(unittest.TestCase):
    def test_record_beats_row_beats_class(self):
        self.assertEqual(P.props_of(rec(material="glass")).drop_tol_m, 0.5)                          # class
        self.assertEqual(P.props_of(rec(material="glass", type="laptop_computer")).drop_tol_m, 0.9)   # row
        self.assertEqual(P.props_of(rec(material="glass", type="laptop_computer", drop_tolerance_m=0.2)).drop_tol_m,
                         0.2)                                                                      # record
        self.assertEqual(P.props_of(rec(material="glass", crush_kg=4)).crush_kg, 4)
        self.assertEqual(P.props_of(rec(material="plastic", max_stack_load_kg=6)).crush_kg, 6)       # alias
        self.assertEqual(P.props_of(rec(material="wood", friction_mu=0.2)).mu, 0.2)
        self.assertEqual(P.props_of(rec(material="steel", specific_heat=460.0)).c_p, 460.0)

    def test_unknown_material_and_bad_values_fall_back_safely(self):
        p = P.props_of(rec(material="unobtainium", mass_kg="heavy", size_m=[0.2, None, -1], drop_tolerance_m=True))
        self.assertEqual(p.cls, "plastic")
        self.assertEqual(p.mass_kg, 0.0)
        self.assertEqual(p.size_m, [0.2, 0.1, 0.1])
        self.assertEqual(p.drop_tol_m, P.MATERIALS["classes"]["plastic"]["drop_tol_m"])

    def test_undeclared_material_uses_the_row_then_the_category(self):
        book = rec(category="book")                                   # generic_item declares no material
        self.assertEqual(P.material_class(None, book), "paper")
        scale = rec(type="kitchen_scale", is_a=["portable_object", "object"], category="kitchen_tool")
        self.assertEqual(P.material_class(None, scale), "electronics")   # row material
        chair = dict(rec(type="chair", is_a=["surface_furniture", "furniture", "object"], category="seat"),
                     material="plastic", portable=False)
        self.assertEqual(P.material_class(None, chair), "plastic")    # the chain declares wood: an explicit override

    def test_material_class_accepts_an_entity_id(self):
        state = tiny_state(entities={"cup": {"type": "generic_item", "location": "kitchen", "material": "china"}})
        self.assertEqual(P.material_class(state, "cup"), "porcelain")

    def test_rows_match_type_then_is_a_then_noun_then_category_then_tags(self):
        bottle = P.props_of(rec(type="wine_bottle", material="glass"))
        self.assertEqual((bottle.shape, bottle.mouth_frac, bottle.com_frac), ("cylinder", 0.3, 0.45))
        bin_ = P.props_of(rec(type="kitchen_trash_can", is_a=["waste_container", "portable_object"]))
        self.assertEqual(bin_.shape, "box")                           # is_a row beats the noun "can"
        bud = P.props_of(rec(type="bud_vase", is_a=["vase"], material="glass"))
        self.assertEqual(bud.mouth_frac, 0.3)                         # type row beats the noun "vase" (0.6)
        self.assertEqual(P.props_of(rec(type="tennis_ball", category="ball")).shape, "sphere")
        top = P.props_of(rec(type="side_table", material="glass", tags=["glass_top"]))
        self.assertEqual((top.cls, top.rating_kg), ("tempered_glass", 20))   # type rating beats the tag's 25
        self.assertEqual(P.props_of(rec(type="patio_table", material="glass", tags=["glass_top"])).rating_kg, 25)

    def test_tempered_glass(self):
        self.assertEqual(P.material_class(None, rec(type="sliding_door", material="glass")), "tempered_glass")
        self.assertEqual(P.material_class(None, rec(material="glass", tempered=True)), "tempered_glass")
        self.assertEqual(P.material_class(None, rec(material="glass")), "glass")

    def test_rating_aliases(self):
        stool = rec(type="kitchen_step_stool", is_a=["step_stool", "climbing_aid", "portable_object"], rated_kg=113.0)
        self.assertEqual((P.props_of(stool).rating_kg, P.props_of(stool).hang_kg), (113.0, None))
        self.assertEqual(P.props_of(rec(type="step_stool", is_a=["climbing_aid"])).rating_kg, 90)
        hook = rec(type="adhesive_hook_small", is_a=["hanging_hardware", "portable_object"], rated_kg=0.23)
        self.assertEqual((P.props_of(hook).hang_kg, P.props_of(hook).rating_kg), (0.23, None))
        anchor = rec(rated_kg={"drywall": 4.5, "masonry": 0.0})       # per-substrate dicts are not a rating
        self.assertIsNone(P.props_of(anchor).rating_kg)
        bar = rec(type="towel_bar", category="hooks", max_load_kg=8.0, portable=False)
        self.assertEqual((P.props_of(bar).hang_kg, P.props_of(bar).rating_kg, P.props_of(bar).handle_kg), (8.0, 8.0, None))
        liner = rec(type="trash_liner", category="liner", max_load_kg=10.0)
        self.assertEqual(P.props_of(liner).handle_kg, 10.0)          # trash liners stay as typed
        self.assertEqual(P.props_of(rec(type="paper_grocery_bag", handle_rating_kg=6.0, max_load_kg=9.0)).handle_kg, 6.0)
        self.assertEqual(P.props_of(rec(type="laundry_basket")).handle_kg, 15)
        self.assertEqual(P.props_of(rec(type="utility_bucket")).handle_kg, 25)       # noun row
        self.assertEqual(P.props_of(rec(shelf_load_kg=12.0, type="wall_shelf")).shelf_rating_kg, 12.0)
        self.assertEqual(P.props_of(rec(type="hand_truck", load_capacity_kg=150.0)).rating_kg, 150.0)

    def test_rod_rating_scales_with_length(self):
        rod = rec(type="closet_rod", category="hooks", portable=False, size_m=[1.0, 0.05, 0.05])
        self.assertAlmostEqual(P.props_of(rod).hang_kg, 25.0)
        self.assertAlmostEqual(P.props_of(dict(rod, size_m=[1.6, 0.05, 0.05])).hang_kg, 40.0)
        self.assertAlmostEqual(P.props_of(rec(type="curtain_rod", material="steel")).hang_kg, 8.0)
        hooks = rec(type="coat_hooks", category="hooks", portable=False, hang_slots=6)
        self.assertAlmostEqual(P.props_of(hooks).hang_kg, 6 * 7.0)                # each hook about 7 kg
        self.assertAlmostEqual(P.props_of(dict(hooks, hang_slots=1)).hang_kg, 7.0)
        self.assertAlmostEqual(P.props_of(dict(hooks, hang_slots=None)).hang_kg, 7.0)   # a single hook
        rack = rec(type="garden_tool_rack", category="hooks", portable=False, hang_slots=10)
        self.assertAlmostEqual(P.props_of(rack).hang_kg, 100.0)                  # review: 10 slots shared 10 kg
        self.assertAlmostEqual(P.props_of(rec(type="key_hook", category="hooks", hang_slots=6)).hang_kg, 12.0)
        self.assertAlmostEqual(P.props_of(dict(hooks, hang_load_kg=5.0)).hang_kg, 5.0)   # a record's own wins

    def test_heat_limits(self):
        cork = rec(type="trivet", material="cork", heat_resistant=True)
        self.assertEqual(P.props_of(cork).heat_limit_c, 400)
        steel = rec(material="steel", heat_resistant=True)
        self.assertEqual(P.props_of(steel).heat_limit_c, 800)        # never lowered
        mitt = rec(material="cotton", heat_rating_c=250.0, heat_resistant=True)
        self.assertEqual(P.props_of(mitt).heat_limit_c, 250.0)      # an explicit rating wins
        self.assertEqual(P.props_of(rec(material="plastic", surface_heat_limit_c=110)).heat_limit_c, 110)
        self.assertEqual(P.props_of(rec(material="glass", thermal_shock_c=90.0)).shock_k, 90.0)
        self.assertEqual(P.props_of(rec(material="plastic", melt_c=210)).melt_c, 210)

    def test_softness_and_strength_flags(self):
        self.assertEqual(P.props_of(rec(material="rubber", soft=True)).softness, 0.8)
        self.assertEqual(P.props_of(rec(material="rubber", soft=False)).softness, 0.1)
        self.assertEqual(P.props_of(rec(material="rubber", soft=True, softness=0.3)).softness, 0.3)
        self.assertEqual(P.props_of(rec(material="foam", softness=7)).softness, 1.0)
        fragile_steel = P.props_of(rec(material="steel", fragile=True))
        self.assertEqual((fragile_steel.drop_tol_m, fragile_steel.crush_kg, fragile_steel.break_mode), (0.5, 500.0, "crack"))
        fragile_cloth = P.props_of(rec(material="cotton", fragile=True))     # unlimited crush gets the default too
        self.assertEqual((fragile_cloth.drop_tol_m, fragile_cloth.crush_kg), (0.5, 10.0))
        self.assertEqual(P.props_of(rec(material="steel", hazards=["fragile"])).drop_tol_m, 0.5)
        self.assertEqual(P.props_of(rec(material="steel")).drop_tol_m, math.inf)
        self.assertEqual(P.props_of(rec(material="glass", fragile=True)).drop_tol_m, 0.5)   # finite: unchanged
        acrylic_glass = P.props_of(rec(material="glass", brittle=False))
        self.assertEqual((acrylic_glass.brittle, acrylic_glass.break_mode), (False, "crack"))
        self.assertTrue(P.props_of(rec(material="plastic")).rigid)
        self.assertFalse(P.props_of(rec(material="plastic", soft_sided=True)).rigid)
        self.assertFalse(P.props_of(rec(material="cotton")).rigid)
        self.assertTrue(P.props_of(rec(material="cotton", rigid=True)).rigid)
        self.assertFalse(P.props_of(rec(material="cotton", waterproof=False)).waterproof)
        self.assertTrue(P.props_of(rec(material="paper", waterproof=True)).waterproof)

    def test_geometry_derived_fields(self):
        bottle = rec(type="wine_bottle", material="glass", size_m=[0.075, 0.075, 0.3], mass_kg=0.5)
        upright, lying = P.props_of(bottle), P.props_of(dict(bottle, orientation="on_side"))
        self.assertEqual(lying.size_m, [0.3, 0.075, 0.075])          # height swaps with the longest footprint side
        self.assertEqual((upright.rollable, lying.rollable), (False, True))
        self.assertEqual(P.props_of(rec(type="tennis_ball", category="ball", material="rubber")).rollable, True)
        self.assertFalse(upright.floats)                             # a full or open glass bottle sinks
        capped = P.props_of(dict(bottle, capacity_l=0.75, cap={"on": True}))
        self.assertTrue(capped.floats)                               # an empty capped bottle floats
        self.assertFalse(P.props_of(dict(bottle, capacity_l=0.75, cap={"on": True}, liquid_l=0.75)).floats)  # full
        self.assertFalse(P.props_of(rec(material="steel", mass_kg=2.0, size_m=[0.1, 0.1, 0.1])).floats)
        self.assertTrue(P.props_of(rec(material="wood", mass_kg=0.1)).floats)     # wood floats, waterproof or not
        self.assertAlmostEqual(P.props_of(rec(material="cotton", mass_kg=2.0, size_m=[0.5, 0.4, 0.1])).absorb_l, 5.0)
        self.assertTrue(P.props_of(rec(material="wood", mounted=True)).anchored)
        self.assertTrue(P.props_of(rec(material="wood", anchored=True)).anchored)
        self.assertTrue(P.props_of(rec(secured=True)).secured)
        self.assertEqual(P.props_of(rec(type="cereal_bowl")).nest_group, "bowl")
        self.assertEqual(P.props_of(rec(type="cereal_bowl", nest_group="tall_bowl", nest_frac=0.5)).nest_frac, 0.5)
        self.assertTrue(P.props_of(rec(material="cotton")).compressible)
        self.assertFalse(P.props_of(rec(material="cotton", compressible=False)).compressible)

    def test_floats_uses_the_material_or_the_sealed_outline_not_the_bounding_box(self):
        """Review finding: floats compared the mass with the bounding box, so 311 of 397 portable metal, glass and
        ceramic types floated (a chef's knife is 667 kg/m3 of its box) while life rings sank (foam is not
        waterproof). Solid items float by their material; sealed hollow ones by their mass against their outline."""
        knife = rec(material="stainless_steel", mass_kg=0.22, size_m=[0.33, 0.05, 0.02])
        self.assertFalse(P.props_of(knife).floats)
        self.assertFalse(P.props_of(rec(material="steel", mass_kg=0.62, size_m=[0.33, 0.13, 0.03])).floats)   # hammer
        self.assertFalse(P.props_of(rec(material="glass", mass_kg=0.3, capacity_l=0.5)).floats)   # an open jar floods
        self.assertTrue(P.props_of(rec(material="glass", mass_kg=0.3, capacity_l=0.5, size_m=[0.09, 0.09, 0.12],
                                       shape="cylinder", lid_on=True)).floats)                      # a sealed empty one
        self.assertFalse(P.props_of(rec(material="cotton", mass_kg=0.5)).floats)        # a towel sinks once soaked
        self.assertTrue(P.props_of(rec(material="polypropylene", mass_kg=0.3)).floats)  # PP floats, PS and PVC sink
        self.assertFalse(P.props_of(rec(material="pvc", mass_kg=0.3)).floats)
        self.assertTrue(P.props_of(rec(material="cork", mass_kg=0.01)).floats)
        self.assertTrue(P.props_of(rec(material="wax", mass_kg=0.3)).floats)
        tennis = rec(type="tennis_ball", category="ball", material="rubber", mass_kg=0.058, size_m=[0.067] * 3)
        self.assertTrue(P.props_of(tennis).floats)                                     # hollow: 370 kg/m3
        solid = rec(type="bouncy_ball", category="ball", material="rubber", mass_kg=0.03, size_m=[0.035] * 3)
        self.assertFalse(P.props_of(solid).floats)                                     # solid rubber: 1340 kg/m3
        self.assertFalse(P.props_of(rec(type="hen_egg", material="organic", mass_kg=0.057,
                                        size_m=[0.06, 0.045, 0.045])).floats)          # a fresh egg sinks
        self.assertTrue(P.props_of(rec(type="apple", material="organic", mass_kg=0.18, size_m=[0.08] * 3)).floats)
        self.assertTrue(P.props_of(rec(material="steel", floats=True)).floats)         # a record may say so
        self.assertTrue(P.props_of(rec(type="pool_float", material="pvc", mass_kg=1.5, size_m=[1.5, 0.8, 0.2])).floats)
        buoy = P.props_of(rec(type="ring_buoy", category="flotation", material="foam", mass_kg=1.1,
                              size_m=[0.6, 0.6, 0.1]))
        self.assertTrue(buoy.floats and buoy.waterproof)
        self.assertLess(buoy.absorb_l, 0.1)                                            # closed-cell: next to nothing
        jacket = P.props_of(rec(type="life_jacket", category="flotation", material="nylon_foam", mass_kg=0.7))
        self.assertEqual(jacket.cls, "closed_cell_foam")
        self.assertTrue(jacket.floats)

    def test_absorption_is_capped_by_the_volume_and_upholstery(self):
        """Review finding: absorb_l was class L/kg x the whole mass: a fabric dining chair held 15 L, a 10 L
        sandbag 25 L, closed-cell foam soaked like a sponge."""
        chair = rec(type="chair", is_a=["surface_furniture", "furniture", "object"], category="seat",
                    material="fabric", mass_kg=6.0, size_m=[0.5, 0.5, 0.9], portable=False)
        self.assertAlmostEqual(P.props_of(chair).absorb_l, 6.0 * 0.3)                 # upholstered: cushion and cover
        self.assertAlmostEqual(P.props_of(dict(chair, material="wood")).absorb_l, 6.0 * 0.05)   # a wooden chair
        sand = rec(type="sand_bag", material="canvas", mass_kg=10.0, size_m=[0.3, 0.15, 0.22])
        self.assertAlmostEqual(P.props_of(sand).absorb_l, 2.5)                        # the sand's pores
        brick = rec(material="paper", mass_kg=2.5, size_m=[0.3, 0.21, 0.05])          # a ream of paper
        self.assertAlmostEqual(P.props_of(brick).absorb_l, 0.9 * 0.3 * 0.21 * 0.05 * 1000.0)   # its pores at most
        self.assertAlmostEqual(P.props_of(rec(material="cellulose", mass_kg=0.02, size_m=[0.11, 0.07, 0.04])).absorb_l,
                               0.9 * 0.11 * 0.07 * 0.04 * 1000.0)                      # 13.9x its mass: still 10-20x
        self.assertLess(P.props_of(rec(type="foam_play_mat", material="foam", mass_kg=1.5,
                                       size_m=[0.6, 0.6, 0.09])).absorb_l, 0.1)       # EVA
        for name, record in RegistryCoverageTests.records_or_none() or ():
            p = P.props_of(record)
            w, d, h = p.size_m
            self.assertLessEqual(p.absorb_l, 0.9 * w * d * h * 1000.0 + 1e-9, name)

    def test_crushable_follows_manipulations_max_load_rule(self):
        """TIER1_PHYSICS 5.3: only items that declare a crush or load limit, are fragile or crushable, get a
        crush_kg row or crumple by class (boxes, cartons, eggs) can be crushed; books and bins cannot."""
        self.assertFalse(P.props_of(rec(material="paper", category="book")).crushable)
        self.assertFalse(P.props_of(rec(material="plastic")).crushable)
        self.assertTrue(P.props_of(rec(material="cardboard")).crushable)
        self.assertTrue(P.props_of(rec(material="carton")).crushable)
        self.assertFalse(P.props_of(rec(material="cardboard", crushable=False)).crushable)   # opts out
        self.assertTrue(P.props_of(rec(material="ceramic", fragile=True)).crushable)
        self.assertTrue(P.props_of(rec(type="hen_egg", material="organic")).crushable)       # the egg row
        declared = P.props_of(rec(material="plastic", max_stack_load_kg=6))
        self.assertEqual((declared.crushable, declared.crush_kg), (True, 6))
        box = P.props_of(rec(material="cardboard", max_load_kg=25))      # a portable box: max_load bears on top
        self.assertEqual((box.crushable, box.crush_kg, box.handle_kg), (True, 25, 25))
        cap = P.props_of(rec(material="cotton", crushable=True))          # crushable without a class limit
        self.assertEqual((cap.crushable, cap.crush_kg), (True, 5.0))

    def test_foot_width_and_drag_rows(self):
        glass = P.props_of(rec(type="wine_glass", material="glass", size_m=[0.09, 0.09, 0.2]))
        self.assertEqual((glass.base_m, glass.com_frac, glass.drop_tol_m), (0.07, 0.55, 0.25))
        self.assertIsNone(P.props_of(rec(material="glass")).base_m)
        self.assertEqual(P.props_of(rec(base_m=0.05)).base_m, 0.05)
        self.assertEqual(P.props_of(rec(type="chair", material="wood")).drag_cd, 0.7)
        self.assertEqual(P.props_of(rec(type="shoe_rack", material="steel")).drag_cd, 0.7)    # the rack noun
        self.assertIsNone(P.props_of(rec(material="steel")).drag_cd)
        self.assertEqual(P.props_of(rec(drag_cd=0.9)).drag_cd, 0.9)
        # environment._cannot_move bounds the drag with the bluff-body Cd: rows may only lower it
        for level in ("types", "nouns", "categories", "tags"):
            for name, row in P.SUPPORTS[level].items():
                self.assertLessEqual(row.get("drag_cd", 0.0), 1.2, name)

    def test_openings(self):
        self.assertEqual(P.props_of(rec(opening_width_m=0.3)).opening_m, 0.3)
        self.assertEqual(P.props_of(rec(opening_m=[0.2, 0.1])).opening_m, (0.2, 0.1))
        self.assertEqual(P.props_of(rec(opening_m=[0.2, 0.1], opening_width_m=0.3)).opening_m, (0.2, 0.1))
        self.assertIsNone(P.props_of(rec(opening_m=-1, opening_width_m="wide")).opening_m)

    def test_hooks_refine_after_resolution_and_before_variation(self):
        calls = []

        def ripeness(record, phys):
            calls.append(phys["softness"])
            return {"softness": 0.9, "drop_tol_m": 2.0}
        ripeness.applies = lambda record: record.get("category") == "food"

        state = tiny_state(entities={"pear": {"type": "generic_item", "location": "kitchen", "category": "food",
                                              "material": "organic"},
                                     "fork": {"type": "generic_item", "location": "kitchen", "material": "steel"}})
        with mock.patch.dict(plugins.HOOKS, {"phys.props": [(100, "test.ripeness", ripeness)]}):
            pear, fork = P.props(state, "pear"), P.props(state, "fork")
        self.assertEqual(calls, [0.5])                               # the filter kept the hook off the fork
        self.assertEqual(pear.softness, 0.9)
        self.assertAlmostEqual(pear.drop_tol_m, 2.0 * P.vary(state, "pear", "drop"))
        self.assertEqual(fork.softness, 0.0)

        def silent(record, phys):
            return None
        with mock.patch.dict(plugins.HOOKS, {"phys.props": [(100, "test.silent", silent)]}):
            self.assertEqual(P.props(state, "fork"), fork)

        def bad(record, phys):
            return {"squishiness": 1}
        with mock.patch.dict(plugins.HOOKS, {"phys.props": [(100, "test.bad", bad)]}):
            with self.assertRaises(ValueError):
                P.props(state, "fork")

    def test_props_is_fast(self):
        state = tiny_state(entities={"bottle": {"type": "generic_item", "location": "kitchen", "material": "glass",
                                                "size_m": [0.075, 0.075, 0.3], "max_load_kg": 3}})
        P.props(state, "bottle")
        best = min(_timed(lambda: [P.props(state, "bottle") for _ in range(2000)]) for _ in range(5)) / 2000
        self.assertLess(best, 15e-6, "props() {:.2f} us (budget 5 us, test bound 3x)".format(best * 1e6))


VARY_SCRIPT = r"""
import json, sys
from house_sim.kernel.phys import props as P
state = {"rooms": {"kitchen": {"size_m": [4.0, 5.0]}, "den": {"size_m": [3.5, 4.0]}},
         "entities": {"cup_%d" % i: {"type": "mug"} for i in range(5)}}
print(json.dumps([[P.vary(state, "cup_%d" % i, law) for law in P.VARIED] for i in range(5)]))
"""


class VaryTests(unittest.TestCase):
    def setUp(self):
        self.state = tiny_state(entities={"cup_{}".format(i): {"type": "generic_item", "location": "kitchen",
                                                               "material": "glass"} for i in range(400)})

    def test_factors_are_within_15_percent_and_spread(self):
        values = [P.vary(self.state, eid, law) for eid in self.state["entities"] for law in P.VARIED]
        self.assertTrue(all(0.85 <= v < 1.15 for v in values))
        self.assertLess(min(values), 0.87)
        self.assertGreater(max(values), 1.13)
        self.assertAlmostEqual(sum(values) / len(values), 1.0, delta=0.01)
        self.assertNotEqual(P.vary(self.state, "cup_1", "drop"), P.vary(self.state, "cup_1", "crush"))

    def test_phys_exact_and_unknown_laws(self):
        self.state["entities"]["cup_1"]["phys_exact"] = True
        self.assertEqual(P.vary(self.state, "cup_1", "drop"), 1.0)
        self.assertEqual(P.props(self.state, "cup_1").drop_tol_m, 0.5)
        with self.assertRaises(ValueError):
            P.vary(self.state, "cup_2", "breakage")

    def test_props_scales_finite_thresholds_only(self):
        p = P.props(self.state, "cup_3")
        nominal = P.props_of(self.state["entities"]["cup_3"])
        self.assertAlmostEqual(p.drop_tol_m, nominal.drop_tol_m * P.vary(self.state, "cup_3", "drop"))
        self.assertAlmostEqual(p.heat_limit_c, nominal.heat_limit_c * P.vary(self.state, "cup_3", "heat"))
        self.assertAlmostEqual(p.shock_k, nominal.shock_k * P.vary(self.state, "cup_3", "shock"))
        self.assertEqual((p.melt_c, p.rating_kg, p.mu, p.size_m), (math.inf, None, nominal.mu, nominal.size_m))

    def test_variation_is_a_pure_function_of_the_house(self):
        before = [P.vary(self.state, "cup_7", law) for law in P.VARIED]
        copy = json.loads(json.dumps(self.state))
        self.assertEqual([P.vary(copy, "cup_7", law) for law in P.VARIED], before)
        other = json.loads(json.dumps(self.state))
        other["rooms"]["den"]["size_m"] = [3.0, 4.0]                 # another house: other thresholds
        self.assertNotEqual([P.vary(other, "cup_7", law) for law in P.VARIED], before)

    def test_factors_are_independent_between_siblings_and_laws(self):
        """Review finding: u = crc32(salt|eid|type|law) is linear over GF(2), so keys of equal length differ by a
        constant XOR mask: mug_1 and mug_2 correlated at -0.975 over houses, drop and heat of one mug at -0.885."""
        def corr(xs, ys):
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            return sxy / math.sqrt(sxx * syy)
        houses = [{"rooms": {"kitchen": {"size_m": [3.0 + 0.01 * i, 4.0]}, "den": {"size_m": [3.5, 4.0]}},
                   "entities": {"mug_{}".format(k): {"type": "mug"} for k in (1, 2, 3)}} for i in range(400)]
        drop = {k: [P.vary(h, "mug_{}".format(k), "drop") for h in houses] for k in (1, 2, 3)}
        heat = [P.vary(h, "mug_1", "heat") for h in houses]
        hang = [P.vary(h, "mug_1", "hang") for h in houses]
        for xs, ys in ((drop[1], drop[2]), (drop[2], drop[3]), (drop[1], drop[3]), (drop[1], heat),
                       (drop[1], hang)):
            self.assertLess(abs(corr(xs, ys)), 0.2)
        weakest = [min(range(1, 4), key=lambda k: drop[k][i]) for i in range(len(houses))]
        self.assertEqual(sorted(set(weakest)), [1, 2, 3])                   # any sibling can be the weak one

    def test_variation_is_stable_across_processes(self):
        runs = []
        for seed in ("0", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run([sys.executable, "-c", VARY_SCRIPT], cwd=str(ROOT), env=env, capture_output=True,
                                 text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            runs.append(json.loads(out.stdout))
        self.assertEqual(runs[0], runs[1])
        state = {"rooms": {"kitchen": {"size_m": [4.0, 5.0]}, "den": {"size_m": [3.5, 4.0]}},
                 "entities": {"cup_{}".format(i): {"type": "mug"} for i in range(5)}}
        self.assertEqual(runs[0], [[P.vary(state, "cup_{}".format(i), law) for law in P.VARIED] for i in range(5)])


def registry_or_skip(test, *names):
    """The registry records of names (the domains loaded in this process); skips the test when one is missing, so
    the file still passes with HOUSE_SIM_DOMAINS=core."""
    reg = default_registry()
    missing = [name for name in names if name not in reg.types]
    if missing:
        test.skipTest("types of an unloaded domain: {}".format(", ".join(missing)))
    return reg


class DataAnchorTests(unittest.TestCase):
    """The anchors of TIER1_PHYSICS 4.3-5.10 on the shipped type data with the default +-15 % variation over many
    instances (TIER1_DECISIONS: variation is on). A "survives" anchor holds for the whole band and a "breaks" anchor
    too; "about" anchors are nominal. The class-level AnchorTests above pin the formulas with phys_exact."""

    N = 120

    def instances(self, type_name, floor="tile", **props):
        """A state with N instances of a registry type (ids x_0 .. x_N-1) and their Phys."""
        reg = registry_or_skip(self, type_name)
        entities = {"x_{}".format(i): dict(reg.make(type_name, "kitchen", **props)) for i in range(self.N)}
        rooms = {"kitchen": {"size_m": [4.0, 5.0], "floor_material": floor, "exits": []}}
        state = {"rooms": rooms, "entities": entities}
        return state, [P.props(state, eid) for eid in sorted(entities)]

    def tolerances(self, type_name):
        return [p.drop_tol_m for p in self.instances(type_name)[1]]

    def test_drop_anchors_hold_for_every_instance(self):
        tile = P.FLOORS["floors"]["tile"]["impact_hardness"]
        carpet = P.FLOORS["floors"]["carpet"]["impact_hardness"]
        phone = self.tolerances("smartphone")
        self.assertTrue(all(1.0 * tile < t for t in phone), min(phone))          # phones survive 1 m
        self.assertGreater(max(phone), 1.0 / 0.85)                                  # variation is still on
        tumbler = self.tolerances("drinking_glass")
        self.assertTrue(all(0.9 * tile > t > 0.3 * tile for t in tumbler))         # counter height breaks, 0.3 m not
        self.assertTrue(all(0.9 * carpet < t for t in tumbler))                    # ... and carpet survives
        stemware = self.tolerances("wine_glass")
        self.assertTrue(all(0.3 * tile > t > 0.2 * tile for t in stemware))        # stemware breaks from about 0.25 m
        for name in ("mug", "dinner_plate"):
            dish = self.tolerances(name)
            self.assertTrue(all(0.75 * tile > t > 0.75 * carpet for t in dish), name)   # table height: tile, carpet
        laptop = self.tolerances("laptop_computer")
        self.assertTrue(all(0.75 * tile < t for t in laptop))                     # survives desk height

    def test_soft_landings_hold_for_every_instance(self):
        """Mugs and plates dropped from hand height (1.5 m) onto carpet survive; eggs break on hard floors from any
        height and on carpet from counter height, but a thick lawn often catches one from 1 m (impact.effective_drop_m
        with floors.json's cushion columns)."""
        from house_sim.kernel.phys import impact
        land = {name: impact.Landing(P.FLOORS["floors"][name]["impact_hardness"], P.FLOORS["floors"][name]["cushion_m"],
                                     P.FLOORS["floors"][name]["cushion_j"]) for name in ("tile", "carpet", "grass")}
        for name in ("mug", "dinner_plate", "drinking_glass"):
            state, phys = self.instances(name)
            mass = phys[0].mass_kg
            survive = [p.drop_tol_m > impact.effective_drop_m(1.5, land["carpet"], mass) for p in phys]
            self.assertGreaterEqual(sum(survive) / float(len(survive)), 0.9, name)     # usually survive carpet
            self.assertTrue(all(p.drop_tol_m < impact.effective_drop_m(0.75, land["tile"], mass) for p in phys),
                            name)                                                       # table height onto tile breaks
        state, eggs = self.instances("hen_egg")
        mass = eggs[0].mass_kg
        self.assertTrue(all(p.drop_tol_m < impact.effective_drop_m(0.3, land["tile"], mass) for p in eggs))
        self.assertTrue(all(p.drop_tol_m < impact.effective_drop_m(1.0, land["carpet"], mass) for p in eggs))
        on_lawn = [p.drop_tol_m > impact.effective_drop_m(1.0, land["grass"], mass) for p in eggs]
        self.assertTrue(0.3 <= sum(on_lawn) / float(len(on_lawn)) <= 0.9)              # often, not always

    def test_egg_setdown_anchor_holds_for_every_egg(self):
        stone = P.FLOORS["floors"]["stone"]["impact_hardness"]
        eggs = self.tolerances("hen_egg")
        self.assertTrue(all(0.02 * stone < t < 0.06 * stone for t in eggs))      # normal set-down ok, fast breaks

    def test_thermal_shock_anchors_hold_for_every_jar_and_mug(self):
        from house_sim.kernel.phys import thermal
        coupling = thermal.liquid_coupling(4.0, 100.0)                             # hot soup into a cold jar
        for name in ("screw_lid_jar", "glass_jar"):
            limits = [p.shock_k for p in self.instances(name)[1]]
            self.assertTrue(all(96.0 * coupling > k for k in limits), name)        # boiling soup cracks a 4 C jar
        canning = [p.shock_k for p in self.instances("mason_jar")[1]]               # heat-treated canning glass:
        self.assertTrue(all(69.0 * thermal.liquid_coupling(21.0, 90.0) < k for k in canning))   # a hot fill holds
        limits = [p.shock_k for p in self.instances("mug")[1]]
        self.assertTrue(all(79.0 * thermal.liquid_coupling(21.0, 100.0) < k for k in limits))   # tea never cracks
        self.assertTrue(all(80.0 * thermal.liquid_coupling(90.0, 10.0) < k for k in limits))    # nor a cold rinse
        bakeware = [p.shock_k for p in self.instances("casserole_dish")[1]]          # tempered glass bakeware:
        self.assertTrue(all(216.0 * thermal.COUPLING_OVEN < k for k in bakeware))    # fridge-cold into a 220 C oven
        press = [p.shock_k for p in self.instances("french_press")[1]]               # borosilicate coffee glass
        self.assertTrue(all(79.0 * thermal.COUPLING_IMMERSION < k for k in press))   # boiling water in any way
        boro = P.props_of(rec(material="pyrex"))
        self.assertLess(96.0 * thermal.COUPLING_IMMERSION, boro.shock_k * 0.85)     # borosilicate survives either way

    def test_stemware_and_lamps_tip_at_their_anchors(self):
        from house_sim.kernel.phys import statics
        reg = registry_or_skip(self, "wine_glass", "table_lamp", "mug", "table")
        state = {"rooms": {"kitchen": {"size_m": [4.0, 5.0], "floor_material": "tile", "exits": []}},
                 "entities": {"table": dict(reg.make("table", "kitchen", pos=[1, 1])),
                              "glass": dict(reg.make("wine_glass", "table", relation="on", spos=[0.4, 0.3])),
                              "lamp": dict(reg.make("table_lamp", "table", relation="on", spos=[0.1, 0.1])),
                              "mug": dict(reg.make("mug", "table", relation="on", spos=[0.8, 0.3]))}}
        self.assertAlmostEqual(statics.tip_accel_g(state, "glass"), 0.32, delta=0.01)   # an empty wine glass
        self.assertAlmostEqual(statics.tip_accel_g(state, "lamp"), 0.5, delta=0.05)     # a table lamp, about 0.5 g
        self.assertGreater(statics.tip_accel_g(state, "mug"), 0.5)                      # a mug slides first
        state["entities"]["glass"]["orientation"] = "on_side"                            # lying: its whole length
        self.assertGreater(statics.tip_accel_g(state, "glass"), 0.5)

    def test_shelf_ratings_spread_in_the_documented_band(self):
        ratings = [p.shelf_rating_kg for p in self.instances("bookshelf")[1]]
        self.assertTrue(all(25.0 * 0.85 <= r < 25.0 * 1.15 for r in ratings))      # nominal 25 kg, +-15 %
        self.assertTrue(min(ratings) < 22.0 and max(ratings) > 28.0)

    def test_a_three_to_four_kg_outdoor_chair_slides_at_about_9_mps(self):
        from house_sim.kernel.phys import environment
        from house_sim.kernel.world import World
        reg = registry_or_skip(self, "outdoor_chair")
        state = {"rooms": {"patio": {"size_m": [6.0, 6.0], "floor_material": "concrete", "exits": [], "outdoor": True}},
                 "entities": {"chair": dict(reg.make("outdoor_chair", "patio", pos=[2, 2]))},
                 "robot": {"room": "patio", "pos": [0, 0], "hands": {"left": None, "right": None}},
                 "people": {}, "pets": {}, "steps": 1, "time_s": 0.0, "stats": {}, "event_log": []}
        moved = None
        for tenth in range(50, 130):
            w = World(state)
            w.begin()
            effect = environment.wind_effect(w, "chair", tenth / 10.0, 0.0)["effect"]
            w.rollback()
            if effect:
                moved = (tenth / 10.0, effect)
                break
        self.assertIsNotNone(moved)
        self.assertEqual(moved[1], "slide")
        self.assertAlmostEqual(moved[0], 9.0, delta=0.6)                            # TIER1_PHYSICS 12.4
        self.assertEqual(environment.drag_cd(state["entities"]["chair"]), 0.7)     # the open-frame row, no override

    def test_a_hot_mug_may_ring_a_wood_finish_but_never_marks_a_plastic_tray(self):
        """Review finding (contact heat): a mug of 75-85 C coffee scorched every wooden table and melted plastic
        trays. The mug's foot is cooler than its coffee (thermal.contact_temp_c), a wood finish gets a white heat ring
        rather than a scorch, and a household plastic is rated about 100 C as a support finish."""
        from house_sim.kernel.phys import thermal
        reg = registry_or_skip(self, "mug", "tray", "table", "desk", "nightstand")
        n = 40
        for support, coffee_c, marks in (("tray", 100.0, False), ("table", 85.0, True), ("desk", 85.0, True),
                                         ("nightstand", 85.0, True), ("table", 60.0, False)):
            entities = {}
            for i in range(n):
                entities["s_{}".format(i)] = dict(reg.make(support, "kitchen", pos=[i % 8, i // 8]))
                entities["m_{}".format(i)] = dict(reg.make("mug", "s_{}".format(i), relation="on", liquid_l=0.3,
                                                           liquid="coffee", temperature_c=coffee_c))
            state = {"rooms": {"kitchen": {"size_m": [4.0, 5.0], "floor_material": "tile", "exits": [],
                                           "temperature_c": 21.0}},
                     "entities": entities, "robot": {"room": "kitchen"}, "people": {}, "pets": {}}
            kinds = []
            for i in range(n):
                contact = thermal.contact_temp_c(state, "m_{}".format(i))
                self.assertAlmostEqual(contact, 21.0 + 0.75 * (coffee_c - 21.0), places=6)
                p = P.props(state, "s_{}".format(i))
                if contact > p.heat_limit_c:
                    kinds.append(thermal.contact_mark(p.cls, contact))
            if marks:
                self.assertTrue(kinds, support)                                      # a hot mug may mark wood ...
                self.assertEqual(set(kinds), {"ring_stain"}, support)               # ... as a white ring, never a scorch
            else:
                self.assertEqual(kinds, [], (support, coffee_c))                    # boiling water: no tray is marked

    def test_light_outdoor_things_stay_put_on_most_daytime_hours(self):
        """Review finding: with the 10 m airport gust as a sustained force a 0.6 kg watering can wandered on two
        thirds of daytime hours and a wheelbarrow on one winter hour in five. With the wind at the item
        (environment.object_wind_mps) over a year of the generator's weather (region us, 09:00-18:00) neither moves
        on more than a few percent of hours; a napkin left out still blows away on many of them."""
        import datetime
        from house_sim.generator import climate
        from house_sim.kernel.phys import environment
        from house_sim.kernel.world import World
        reg = registry_or_skip(self, "watering_can", "wheelbarrow")
        state = {"rooms": {"yard": {"size_m": [8.0, 8.0], "floor_material": "grass", "exits": [], "outdoor": True,
                                    "type": "backyard"}},
                 "entities": {"can": dict(reg.make("watering_can", "yard", pos=[2, 2])),
                              "barrow": dict(reg.make("wheelbarrow", "yard", pos=[5, 5])),
                              "napkin": dict(rec(location="yard", pos=[1, 6], material="paper", mass_kg=0.004,
                                                 size_m=[0.4, 0.4, 0.002]))},
                 "robot": {"room": "yard", "pos": [0, 0], "hands": {"left": None, "right": None}},
                 "people": {}, "pets": {}, "steps": 1, "time_s": 0.0, "stats": {}, "event_log": []}
        key, moved, hours = climate.seed_key(3), {"can": 0, "barrow": 0, "napkin": 0}, 0
        start = datetime.datetime(2023, 1, 1, 9)
        for day in range(0, 365, 2):
            for hour in range(9, 19, 3):
                weather = climate.weather_at(key, "us", start + datetime.timedelta(days=day, hours=hour - 9))
                sustained, gust = environment.object_wind_mps(state, "can", weather)
                hours += 1
                for eid in moved:
                    w = World(state)
                    w.begin()
                    if environment.wind_effect(w, eid, sustained, 0.0, gust_mps=gust)["effect"]:
                        moved[eid] += 1
                    w.rollback()
        self.assertLess(moved["can"] / float(hours), 0.03, moved)
        self.assertLess(moved["barrow"] / float(hours), 0.01, moved)
        self.assertGreater(moved["napkin"] / float(hours), 0.2, moved)

    def test_wide_necked_containers_take_what_fits_their_neck(self):
        from house_sim.kernel.phys import contain
        reg = registry_or_skip(self, "baby_bottle", "blender_jar")
        bottle = reg.make("baby_bottle", "kitchen")
        self.assertEqual(P.props_of(bottle).mouth_frac, 0.8)                       # a 4 cm neck on a 5 cm inside
        scoop = rec(size_m=[0.035, 0.035, 0.03], volume_l=0.02)
        self.assertTrue(contain.opening_ok(scoop, bottle))
        self.assertEqual(P.props_of(reg.make("blender_jar", "kitchen")).mouth_frac, 1.0)


class LiquidAndFloorTests(unittest.TestCase):
    def test_liquid_rows_aliases_and_default(self):
        milk = P.liquid_props("milk")
        self.assertEqual((milk["name"], milk["rho"], milk["residue"]), ("milk", 1030, "protein"))
        self.assertEqual(P.liquid_props("Rainwater")["name"], "water")
        olive = P.liquid_props("olive_oil")
        self.assertEqual((olive["name"], olive["rho"], olive["floor_hazard"]), ("oil", 910, "slip"))
        for unknown in ("plasma", None, ""):
            self.assertEqual(P.liquid_props(unknown)["name"], "default")
            self.assertEqual(P.liquid_props(unknown)["rho"], 1000)
        self.assertTrue(P.liquid_props("gasoline")["flammable"] is True and P.liquid_props("oil")["flammable"] == "hot")
        P.liquid_props("water")["rho"] = 1                            # a fresh dict: the data is untouched
        self.assertEqual(P.liquid_props("water")["rho"], 1000)

    def test_every_liquid_named_by_the_type_files_resolves(self):
        names = {r.get("liquid") for r in (default_registry().resolve(t) for t in default_registry().names())}
        for name in sorted(n for n in names if isinstance(n, str)):
            if name != "food":
                self.assertNotEqual(P.liquid_props(name)["name"], "default", name)

    def test_floor_of_a_room(self):
        state = tiny_state(entities={"floor_kitchen": {"type": "floor_surface", "location": "kitchen",
                                                       "material": "marble"}})
        self.assertEqual(P.floor_props(state, "kitchen")["material"], "marble")    # the floor entity wins
        self.assertEqual(P.floor_props(state, "den")["material"], "carpet")        # room floor_material
        state["rooms"]["den"].pop("floor_material")
        self.assertEqual(P.floor_props(state, "den")["material"], "default")
        state["rooms"]["den"]["terrain"] = "lawn"
        den = P.floor_props(state, "den")
        self.assertEqual((den["material"], den["mu"], den["soft"]), ("grass", 0.7, True))
        self.assertEqual(P.floor_kind("Granite"), "stone")
        self.assertEqual(P.floor_kind(None), "default")

    def test_a_rug_softens_a_hard_floor_under_its_footprint(self):
        state = tiny_state(entities={"rug": {"type": "rug", "location": "kitchen", "pos": [1, 1]},
                                     "den_rug": {"type": "rug", "location": "den", "pos": [0, 0]}})
        under = P.floor_props(state, "kitchen", [2, 2])               # a 2.0 x 1.4 m rug covers cells 1-4 x 1-3
        self.assertEqual((under["material"], under["rug"], under["impact_hardness"]), ("rug", "rug", 0.45))
        beside = P.floor_props(state, "kitchen", [0, 0])
        self.assertEqual((beside["material"], beside["rug"]), ("tile", None))
        self.assertEqual(P.floor_props(state, "den", [0, 0])["material"], "carpet")   # a rug adds nothing to carpet
        self.assertEqual(P.rug_at(state, "kitchen", (4, 3)), "rug")
        self.assertIsNone(P.rug_at(state, "kitchen", (5, 3)))


class LawbookTests(unittest.TestCase):
    ACTIVE = {"orphan", "overflow", "setdown", "drip", "creep"}

    def test_states_at_landing_follow_tier1_physics_10_2(self):
        self.assertEqual({law for law in lawbook.LAWS if lawbook.active(law)}, self.ACTIVE)
        self.assertEqual(len(lawbook.LAWS), 23)
        for law_id, row in lawbook.LAWS.items():
            self.assertEqual(row["duplicated_by"] == (), law_id in self.ACTIVE, law_id)
        self.assertEqual(set(lawbook.LAWS["furniture_tip"]["duplicated_by"]),
                         {"mechanisms", "manipulation", "safety", "climate"})

    def test_settle_order_follows_tier1_physics_6_1(self):
        order = {law: row["order"] for law, row in lawbook.LAWS.items() if row["order"] is not None}
        self.assertEqual(order, {"orphan": 10, "overflow": 20, "overload": 30, "hang": 30, "crush": 40, "handle": 45,
                                 "edge": 50, "slope": 50, "stack": 55, "furniture_tip": 60, "open_spill": 70,
                                 "contact_heat": 80, "wet_contact": 85, "thermal_shock": 90, "phase": 95})

    def test_registration_order_and_duplicates(self):
        with mock.patch.dict(lawbook._REGISTERED, clear=True):
            def crush(w, eid, ctx):
                return None

            def overload(w, eid, ctx):
                return None
            def hang(w, eid, ctx):
                return None
            lawbook.law("crush", 40)(crush)
            lawbook.law("overload", 30)(overload)
            lawbook.law("hang", 30)(hang)
            lawbook.law("crush", 40)(crush)                           # the same function again: a no-op
            self.assertEqual(lawbook.laws(), [(30, "hang", hang), (30, "overload", overload), (40, "crush", crush)])
            with self.assertRaises(ValueError):
                lawbook.law("crush", 40)(lambda w, eid, ctx: None)
            with self.assertRaises(ValueError):
                lawbook.law("hang", 31)
            with self.assertRaises(ValueError):
                lawbook.law("hang", True)
            with self.assertRaises(KeyError):
                lawbook.law("levitation", 50)
            lawbook.law("carry", 100)(overload)                       # laws without a settle order take any
        self.assertNotIn("crush", lawbook._REGISTERED)

    def test_forced_overrides_nest_and_restore(self):
        self.assertFalse(lawbook.active("crush"))
        with lawbook.forced(crush="active", orphan=False):
            self.assertTrue(lawbook.active("crush"))
            self.assertFalse(lawbook.active("orphan"))
            with lawbook.forced(crush=False):
                self.assertFalse(lawbook.active("crush"))
            self.assertTrue(lawbook.active("crush"))
        self.assertFalse(lawbook.active("crush"))
        self.assertTrue(lawbook.active("orphan"))
        with self.assertRaises(RuntimeError):
            with lawbook.forced(carry=True):
                raise RuntimeError("boom")
        self.assertFalse(lawbook.active("carry"))
        with self.assertRaises(ValueError):
            with lawbook.forced(crush="sometimes"):
                pass
        with self.assertRaises(KeyError):
            lawbook.active("levitation")


INERT_SCRIPT = r"""
import json
from house_sim.kernel import plugins
def sizes():
    out = {}
    for name in ("ACTIONS", "SYSTEMS", "FURNISHERS", "OBSERVERS", "INSPECTORS", "INVARIANTS", "ANSWERERS"):
        out[name] = len(getattr(plugins, name))
    for name in ("BEFORE", "AFTER", "HOOKS"):
        out[name] = sum(len(v) for v in getattr(plugins, name).values())
    return out
before = sizes()
from house_sim.kernel.phys import contain, forces, lawbook, props
print(json.dumps([before, sizes()]))
"""


class ImportInertTests(unittest.TestCase):
    def test_importing_the_data_layer_registers_nothing(self):
        out = subprocess.run([sys.executable, "-c", INERT_SCRIPT], cwd=str(ROOT), capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        before, after = json.loads(out.stdout)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
