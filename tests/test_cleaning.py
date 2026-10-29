"""Cleaning domain mechanics: each mechanic has a success case and a failure/consequence case.

Most scenes are a small hand-built house (kitchen, hall, bathroom, carpeted living room, backyard) made with
registry.make from core types, cleaning types and explicit-prop generic_items, so the tests do not depend on what
other domains furnish and run the same with only core+cleaning loaded or with every domain loaded. A few tests go
through the spec machinery (generated houses + cleaning helpers) and the furnisher.
"""
import copy
import unittest

from house_sim.kernel.engine import HouseEnv
from house_sim.kernel.plugins import load_domains
from house_sim.kernel.registry import default_registry
from house_sim.tasks.runner import Runner, build


def odor_level(rec, kind=None):
    """Odor intensity: CONVENTIONS 5 dict {kind: intensity} (a legacy float reads as one kind)."""
    value = rec.get("odor")
    if isinstance(value, dict):
        return float(value.get(kind, 0.0)) if kind else max([float(v) for v in value.values()] or [0.0])
    return float(value or 0.0)


# ----------------------------------------------------------------------------------------------- scene builder

def _soil_cells(cells):
    agg = {}
    for part in cells.values():
        for kind, level in part.items():
            agg[kind] = max(agg.get(kind, 0.0), level)
    return agg


class Lab:
    """A tiny deterministic house. `extra` adds entities: {eid: (type, location, props)}."""

    def __init__(self, kitchen_floor="tile", hall_floor="sealed_wood", living_floor="carpet", counter="granite",
                 extra=None, props=None, people=None, household=None, floor_cells=None, clock="2026-10-09T10:00:00", seed=0,
                 robot=None, build=None, room_props=None, perception="oracle", camera=None, notices=False):
        load_domains()
        reg = default_registry()
        set_props = props
        rooms = {
            "kitchen": {"label": "Kitchen", "type": "kitchen", "size_m": [4.0, 4.0], "exits": ["hall"]},
            "hall": {"label": "Hall", "type": "hallway", "size_m": [2.0, 4.0], "exits": ["kitchen", "bath", "yard", "living"]},
            "bath": {"label": "Bathroom", "type": "bathroom", "size_m": [2.5, 3.0], "exits": ["hall"]},
            "living": {"label": "Living room", "type": "living_room", "size_m": [4.0, 4.0], "exits": ["hall"]},
            "yard": {"label": "Backyard", "type": "backyard", "size_m": [6.0, 6.0], "exits": ["hall"], "outdoor": True,
                     "terrain": "grass"},
        }
        for rid, values in sorted((room_props or {}).items()):
            rooms[rid].update(copy.deepcopy(values))
        connections = {
            "c1": {"rooms": ["kitchen", "hall"], "kind": "doorway", "width_m": 0.9, "cells": {"kitchen": [0, 7], "hall": [0, 0]}},
            "c2": {"rooms": ["hall", "bath"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [3, 7], "bath": [0, 0]}},
            "c3": {"rooms": ["hall", "yard"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [3, 0], "yard": [0, 0]}},
            "c4": {"rooms": ["hall", "living"], "kind": "doorway", "width_m": 0.9, "cells": {"hall": [0, 4], "living": [7, 0]}},
        }
        ents = {}

        def add(eid, type_name, loc, **props):
            ents[eid] = reg.make(type_name, loc, **props)
            return eid

        floors = {"kitchen": kitchen_floor, "hall": hall_floor, "bath": "tile", "living": living_floor, "yard": "grass"}
        for rid, mat in sorted(floors.items()):
            size = rooms[rid]["size_m"]
            props = {"material": mat, "size_m": [size[0], size[1], 0.01], "pos": [0, 0]}
            if mat in ("sealed_wood", "hardwood"):
                props.update(material="hardwood", finish="sealed_wood")
            if rid == "yard":
                props["outdoor"] = True
            cells = (floor_cells or {}).get(rid)
            if cells:
                props["_cells"] = copy.deepcopy(cells)
                props["soil"] = _soil_cells(cells)
            add("floor_" + rid, "floor_surface", rid, **props)
            if rid != "yard":
                add("light_" + rid, "ceiling_light", rid, pos=[0, 0], on=True)
        add("counter", "counter", "kitchen", pos=[2, 0], finish=counter)
        add("sink", "generic_item", "kitchen", label="kitchen sink", portable=False, water_source=True, drain=True, container=True,
            accepts_items=True, liquid_container=True, liquid_l=0.0, capacity_l=20.0, inner_size_m=[0.7, 0.45, 0.25],
            size_m=[0.8, 0.5, 0.9], material="stainless", sink_zone="kitchen", pos=[0, 0], blocks_path=True, clearance_m=0.0)
        add("table", "table", "kitchen", pos=[3, 4], finish="sealed_wood")
        add("can", "kitchen_trash_can", "kitchen", pos=[7, 7])
        add("liner1", "trash_liner_13gal", "can", relation="part", slot="liner", opened=True, folded=False, seated=True,
            rim_folded=True)
        ents["can"]["slots"]["liner"]["item"] = "liner1"
        add("recycling", "recycling_bin", "kitchen", pos=[6, 7])
        add("cloth", "microfiber_cloth", "kitchen", pos=[1, 3])
        add("cloth2", "microfiber_cloth", "kitchen", pos=[1, 3])
        add("apc", "all_purpose_cleaner", "kitchen", pos=[1, 4])
        add("broom", "broom", "kitchen", pos=[1, 5])
        add("pan", "dustpan", "kitchen", pos=[1, 5])
        add("sponge", "sponge", "kitchen", pos=[1, 6])
        add("soap", "dish_soap", "kitchen", pos=[1, 6])
        add("plate", "generic_item", "kitchen", pos=[1, 2], label="plate", material="ceramic", faces=["top", "bottom"],
            size_m=[0.26, 0.26, 0.02], mass_kg=0.5, category="dish",
            soil_faces={"top": {"food": 0.6, "grease": 0.4}, "bottom": {"food": 0.2}}, soil={"food": 0.6, "grease": 0.4})
        add("toilet", "generic_item", "bath", label="toilet", toilet=True, portable=False, blocks_path=True, pos=[1, 3],
            size_m=[0.4, 0.7, 0.4], material="porcelain", faces=["bowl", "seat_top", "seat_underside"], liquid_l=6.0)
        add("trash_cart", "trash_cart", "yard", pos=[4, 1])
        add("recycling_cart", "recycling_cart", "yard", pos=[5, 1])
        for eid, (type_name, loc, props) in sorted((extra or {}).items()):
            add(eid, type_name, loc, **copy.deepcopy(props))
        if build:
            build(ents, add)
        for eid, values in sorted((set_props or {}).items()):
            ents[eid].update(copy.deepcopy(values))
        hh = {"waste_rules": {}, "quiet_hours": [22.0, 7.0], "hard_water": False}
        hh.update(household or {})
        self.config = {"schema_version": 2, "start_room": "kitchen", "rooms": rooms, "clock": {"start": clock},
                       "connections": connections, "entities": ents, "people": copy.deepcopy(people or {}), "pets": {},
                       "robot": copy.deepcopy(robot or {"room": "kitchen", "pos": [1, 1]}), "household": hh}
        if notices:
            self.config["notices"] = []          # the tier-1 notice bus (kernel/timeline.py), on as for a schema-3 spec
        self.env = HouseEnv(copy.deepcopy(self.config), None, seed, trace=False, observe_on_step=False, perception=perception,
                            camera=camera)
        self.r = Runner(self.env, {}, strict=True)

    # ----- shortcuts
    @property
    def w(self):
        return self.env.world

    def ent(self, eid):
        return self.env.world.s["entities"][eid]

    def stat(self, name):
        return self.env.world.s["stats"].get(name, 0)

    def sec(self):
        return self.env.world.s.get("cleaning", {})

    def log(self, kind):
        return [e for e in self.sec().get("log", []) if e.get("kind") == kind or e.get("event") == kind]

    def step(self, action, target="", destination="", **kw):
        return self.env.step(action, target, destination, **kw)

    def do(self, action, **kw):
        return self.r.do(action, **kw)

    def hold(self, *items):
        return self.r.skill("cleaning.hold", items=list(items))

    def go(self, target):
        return self.r.skill("approach", target=target)

    def soil(self, eid, kind):
        return float((self.ent(eid).get("soil") or {}).get(kind, 0.0))

    def damage(self, eid):
        return dict(self.ent(eid).get("damage") or {})

    def wait(self, seconds):
        return self.do("wait", seconds=int(seconds))


def with_vacuum(type_name="upright_vacuum", room="living", pos=(1, 2), attachment=None, filter_type="vacuum_filter_foam",
                mode=None, eid="vac"):
    """Build hook: a vacuum with its bin/bag and filter fitted (and an attachment when asked)."""
    def build(ents, add):
        add(eid, type_name, room, pos=list(pos))
        slots = ents[eid].get("slots") or {}
        if "bin" in slots:
            add(eid + "_bin", "vacuum_bin", eid, relation="part", slot="bin")
            slots["bin"]["item"] = eid + "_bin"
        if "bag" in slots:
            add(eid + "_bag", "vacuum_bag", eid, relation="part", slot="bag", model=ents[eid].get("bag_model", "C"))
            slots["bag"]["item"] = eid + "_bag"
        if "filter" in slots:
            add(eid + "_filter", filter_type, eid, relation="part", slot="filter")
            slots["filter"]["item"] = eid + "_filter"
        if attachment:
            add(eid + "_tool", attachment, eid, relation="part", slot="tool")
            slots["tool"]["item"] = eid + "_tool"
        if mode:
            ents[eid].setdefault("settings", {})["mode"] = mode
    return build


def spec_scene(setup, seed=0, house=None):
    spec = {"catalog_id": "test-cleaning", "house": house or {"residents": "minimal", "pets": "none"}, "setup": setup,
            "success": [{"path": "time_s", "op": "gte", "value": 0}], "reference": []}
    config, task, oracle = build(spec, seed)
    env = HouseEnv(config, task, seed, trace=False, observe_on_step=False)
    b = oracle["bindings"]
    return env, b, Runner(env, b, strict=True)


class Smoke(unittest.TestCase):
    def test_lab_builds_and_waits(self):
        lab = Lab()
        self.assertTrue(lab.step("wait", seconds=60)["ok"])
        lab.hold("cloth")
        lab.go("counter")
        self.assertTrue(lab.step("wipe", "counter", tool="cloth")["ok"])


JAR = ("generic_item", "counter", {"relation": "on", "spos": [0.3, 0.2], "label": "glass jar", "size_m": [0.1, 0.1, 0.15],
                                   "mass_kg": 0.4, "material": "glass"})


class Wiping(unittest.TestCase):
    def lab(self, **kw):
        props = {"counter": {"soil": {"crumbs": 0.5}}}
        props.update(kw.pop("props", {}))
        return Lab(props=props, **kw)

    def test_crumbs_wiped_into_a_dustpan_stay_off_the_floor(self):
        lab = self.lab()
        lab.hold("cloth", "pan")
        lab.go("counter")
        lab.do("wipe", target="counter", tool="cloth", into="pan")
        lab.do("wipe", target="counter", tool="cloth", into="pan")
        self.assertLess(lab.soil("counter", "crumbs"), 0.05)
        self.assertLess(lab.soil("floor_kitchen", "crumbs"), 0.02)

    def test_crumbs_wiped_without_a_catch_end_up_on_the_floor(self):
        lab = self.lab()
        lab.hold("cloth")
        lab.go("counter")
        lab.do("wipe", target="counter", tool="cloth")
        lab.do("wipe", target="counter", tool="cloth")
        self.assertGreater(lab.soil("floor_kitchen", "crumbs"), 0.05)

    def test_lifting_items_cleans_underneath(self):
        lab = self.lab(extra={"jar": JAR}, props={"counter": {"soil": {"dust": 0.5}}})
        lab.hold("jar")
        lab.r.skill("cleaning.stash_all")
        lab.hold("cloth")
        lab.go("counter")
        for _ in range(3):
            lab.do("wipe", target="counter", tool="cloth")
        self.assertLess(lab.soil("counter", "dust"), 0.05)

    def test_wiping_around_items_leaves_soil_under_them(self):
        lab = self.lab(extra={"jar": JAR}, props={"counter": {"soil": {"dust": 0.5}}})
        lab.hold("cloth")
        lab.go("counter")
        for _ in range(3):
            lab.do("wipe", target="counter", tool="cloth")
        self.assertGreaterEqual(lab.soil("counter", "dust"), 0.3)
        visible = lab.env.observe()
        lab.hold("jar")
        self.assertGreaterEqual(lab.soil("counter", "dust"), 0.3)

    def test_cleaner_dwell_lifts_grease_better_than_a_plain_damp_wipe(self):
        plain = self.lab(props={"counter": {"soil": {"grease": 0.6}}})
        plain.hold("cloth")
        plain.go("counter")
        plain.do("wipe", target="counter", tool="cloth")
        product = self.lab(props={"counter": {"soil": {"grease": 0.6}}})
        product.hold("apc", "cloth")
        product.go("counter")
        product.do("apply_cleaner", target="counter", tool="apc")
        product.wait(60)
        product.do("wipe", target="counter", tool="cloth")
        self.assertLess(product.soil("counter", "grease"), plain.soil("counter", "grease") - 0.1)

    def test_vinegar_etches_marble_but_stone_cleaner_does_not(self):
        extra = {"vinegar": ("cleaning_vinegar", "kitchen", {"pos": [1, 4]}), "stone": ("stone_cleaner", "kitchen", {"pos": [1, 4]})}
        bad = self.lab(counter="marble", extra=extra, props={"counter": {"soil": {"sticky": 0.4}}})
        bad.hold("vinegar", "cloth")
        bad.go("counter")
        bad.do("apply_cleaner", target="counter", tool="vinegar")
        bad.do("wipe", target="counter", tool="cloth")
        self.assertTrue(any("etch" in k for k in bad.damage("counter")), bad.damage("counter"))
        good = self.lab(counter="marble", extra=extra, props={"counter": {"soil": {"sticky": 0.4}}})
        good.hold("stone", "cloth")
        good.go("counter")
        good.do("apply_cleaner", target="counter", tool="stone")
        good.do("wipe", target="counter", tool="cloth")
        self.assertEqual(good.damage("counter"), {})
        self.assertEqual(good.stat("damage"), 0)


class Chemistry(unittest.TestCase):
    EXTRA = {"bleach": ("bleach", "kitchen", {"pos": [1, 4]}), "vinegar": ("cleaning_vinegar", "kitchen", {"pos": [1, 4]}),
             "ammonia": ("ammonia_cleaner", "kitchen", {"pos": [1, 4]}), "dis": ("disinfectant_spray", "kitchen", {"pos": [1, 4]})}

    def test_bleach_after_vinegar_on_the_same_surface_releases_gas(self):
        lab = Lab(extra=self.EXTRA)
        lab.hold("vinegar", "bleach")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="vinegar")
        lab.step("apply_cleaner", "counter", tool="bleach")
        self.assertGreater(lab.stat("gas_exposure"), 0)
        self.assertGreater(lab.sec().get("gas_events", 0), 0)

    def test_bleach_alone_and_a_water_rinse_is_fine(self):
        lab = Lab(extra=self.EXTRA)
        lab.hold("bleach", "cloth")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="bleach")
        lab.wait(300)
        lab.do("wipe", target="counter", tool="cloth")
        self.assertEqual(lab.stat("gas_exposure"), 0)

    def test_mixing_bleach_and_ammonia_in_a_bucket_gasses_the_room(self):
        lab = Lab(extra=dict(self.EXTRA, bucket=("mop_bucket", "kitchen", {"pos": [2, 5], "liquid": "water", "liquid_l": 6.0})))
        lab.hold("bleach", "ammonia")
        lab.go("bucket")
        lab.do("apply_cleaner", target="bucket", tool="bleach", amount_ml=60)
        lab.step("apply_cleaner", "bucket", tool="ammonia", amount_ml=60)
        self.assertGreater(lab.stat("gas_exposure"), 0)

    def test_disinfectant_kept_wet_for_its_contact_time_kills_bacteria(self):
        lab = Lab(extra=self.EXTRA, props={"counter": {"contamination": {"bacteria": 0.8}}})
        lab.hold("dis")
        lab.go("counter")
        lab.do("disinfect", target="counter", tool="dis")
        lab.wait(660)
        self.assertLess(lab.ent("counter")["contamination"]["bacteria"], 0.01)

    def test_disinfectant_wiped_off_immediately_does_not_sanitize(self):
        lab = Lab(extra=self.EXTRA, props={"counter": {"contamination": {"bacteria": 0.8}}})
        lab.hold("dis", "cloth")
        lab.go("counter")
        lab.do("disinfect", target="counter", tool="dis")
        lab.do("wipe", target="counter", tool="cloth")
        lab.do("dry", target="counter", tool="cloth2") if "cloth2" in lab.w.held_items() else None
        lab.wait(660)
        self.assertGreater(lab.ent("counter")["contamination"]["bacteria"], 0.05)

    def test_soil_shields_bacteria_from_disinfectant(self):
        lab = Lab(extra=self.EXTRA, props={"counter": {"contamination": {"bacteria": 0.8}, "soil": {"food": 0.5, "grease": 0.4}}})
        lab.hold("dis")
        lab.go("counter")
        lab.do("disinfect", target="counter", tool="dis")
        lab.wait(660)
        self.assertGreater(lab.ent("counter")["contamination"]["bacteria"], 0.05)

    def test_products_used_are_logged_for_constraint_tasks(self):
        lab = Lab(extra=self.EXTRA)
        lab.hold("bleach")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="bleach")
        self.assertIn("bleach", lab.sec()["products_used"])
        self.assertIn("bleach", lab.sec()["products_on"]["counter"])


PAN = {"label": "nonstick frying pan", "material": "nonstick", "size_m": [0.28, 0.28, 0.05], "mass_kg": 1.0, "category": "cookware",
       "faces": ["inside", "outside"], "soil_faces": {"inside": {"burnt": 0.5}}, "soil": {"burnt": 0.5}}


class AbrasionAndZones(unittest.TestCase):
    def test_scouring_pad_scratches_nonstick(self):
        lab = Lab(extra={"fpan": ("generic_item", "counter", dict(PAN, relation="on", spos=[0.5, 0.2])),
                         "pad": ("scouring_pad", "kitchen", {"pos": [1, 4]})})
        lab.hold("pad")
        lab.go("fpan")
        lab.do("scrub", target="fpan", tool="pad", face="inside")
        self.assertTrue(lab.damage("fpan"), "nonstick should be scratched")
        self.assertGreater(lab.stat("damage"), 0)

    def test_non_scratch_sponge_is_safe_on_nonstick(self):
        lab = Lab(extra={"fpan": ("generic_item", "counter", dict(PAN, relation="on", spos=[0.5, 0.2])),
                         "soft": ("non_scratch_sponge", "kitchen", {"pos": [1, 4], "wetness": 0.6})})
        lab.hold("soft")
        lab.go("fpan")
        lab.do("scrub", target="fpan", tool="soft", face="inside")
        self.assertEqual(lab.damage("fpan"), {})

    def test_toilet_cloth_carried_to_the_kitchen_cross_contaminates(self):
        lab = Lab(props={"toilet": {"contamination": {"bacteria": 0.9}, "soil_faces": {"seat_top": {"urine": 0.3}}, "soil": {"urine": 0.3}}})
        lab.hold("cloth")
        lab.go("toilet")
        lab.do("wipe", target="toilet", tool="cloth", face="seat_top")
        lab.go("counter")
        lab.do("wipe", target="counter", tool="cloth")
        self.assertGreater(lab.sec()["zone_violations"], 0)
        self.assertGreater(lab.ent("counter").get("contamination", {}).get("bacteria", 0.0), 0.05)

    def test_separate_cloths_per_zone_keep_the_counter_clean(self):
        lab = Lab(props={"toilet": {"contamination": {"bacteria": 0.9}, "soil_faces": {"seat_top": {"urine": 0.3}}, "soil": {"urine": 0.3}}})
        lab.hold("cloth")
        lab.go("toilet")
        lab.do("wipe", target="toilet", tool="cloth", face="seat_top")
        lab.r.skill("cleaning.stash_all")
        lab.hold("cloth2")
        lab.go("counter")
        lab.do("wipe", target="counter", tool="cloth2")
        self.assertEqual(lab.sec().get("zone_violations", 0), 0)
        self.assertLess(lab.ent("counter").get("contamination", {}).get("bacteria", 0.0), 0.01)


LAMP = ("generic_item", "table", {"relation": "on", "spos": [0.2, 0.2], "label": "table lamp", "bulb_type": "halogen", "on": True,
                                  "switchable": True, "category": "light", "size_m": [0.25, 0.25, 0.5], "mass_kg": 1.5,
                                  "material": "metal", "soil": {"dust": 0.4}})
FAN = ("generic_item", "living", {"label": "ceiling fan", "rotating": True, "on": True, "switchable": True, "mounted": True,
                                  "height_m": 2.4, "pos": [4, 4], "size_m": [1.2, 1.2, 0.3], "blade_pitch": 12,
                                  "material": "sealed_wood", "soil": {"dust": 0.5}})
PC = ("generic_item", "table", {"relation": "on", "spos": [0.6, 0.2], "label": "desktop computer", "fans": True, "vents": True,
                                "electronic": True, "water_safe": False, "size_m": [0.2, 0.45, 0.45], "mass_kg": 8.0,
                                "material": "plastic", "soil": {"dust": 0.6}})


class Dusting(unittest.TestCase):
    def test_microfiber_captures_dust(self):
        lab = Lab(props={"table": {"soil": {"dust": 0.5}}})
        lab.hold("cloth")
        lab.go("table")
        lab.do("dust", target="table", tool="cloth")
        self.assertLess(lab.soil("table", "dust"), 0.15)
        self.assertLess(lab.soil("floor_kitchen", "dust"), 0.05)

    def test_feather_duster_flicks_dust_onto_the_floor(self):
        lab = Lab(extra={"feather": ("feather_duster", "kitchen", {"pos": [1, 4]})}, props={"table": {"soil": {"dust": 0.5}}})
        lab.hold("feather")
        lab.go("table")
        lab.do("dust", target="table", tool="feather")
        self.assertGreater(lab.soil("floor_kitchen", "dust"), 0.0)

    def test_hot_halogen_bulb_burns_but_a_cooled_one_does_not(self):
        hot = Lab(extra={"lamp": LAMP})
        hot.hold("cloth")
        hot.go("lamp")
        hot.do("dust", target="lamp", tool="cloth")
        self.assertGreater(hot.stat("burns"), 0)
        cool = Lab(extra={"lamp": LAMP})
        cool.go("lamp")
        cool.do("switch_off", target="lamp")
        cool.wait(900)
        cool.hold("cloth")
        cool.go("lamp")
        cool.do("dust", target="lamp", tool="cloth")
        self.assertEqual(cool.stat("burns"), 0)

    def test_dusting_a_spinning_fan_bends_a_blade(self):
        lab = Lab(extra={"fan": FAN, "duster": ("extendable_duster", "living", {"pos": [3, 3]})})
        lab.hold("duster")
        lab.go("fan")
        res = lab.step("dust", "fan", tool="duster")
        self.assertIn("blade_bent", lab.damage("fan"))
        self.assertIn("flung", res["message"])

    def test_fan_switched_off_and_stopped_dusts_cleanly(self):
        lab = Lab(extra={"fan": FAN, "duster": ("extendable_duster", "living", {"pos": [3, 3]})})
        lab.go("fan")
        lab.do("switch_off", target="fan")
        lab.wait(120)
        lab.hold("duster")
        lab.go("fan")
        lab.do("dust", target="fan", tool="duster")
        self.assertNotIn("blade_bent", lab.damage("fan"))
        self.assertLess(lab.soil("fan", "dust"), 0.25)

    def test_compressed_air_needs_fans_held_and_an_upright_can(self):
        lab = Lab(extra={"pc": PC, "air": ("compressed_air_can", "kitchen", {"pos": [1, 4]})})
        lab.hold("air")
        lab.go("pc")
        lab.do("dust", target="pc", tool="air", inverted=True)
        self.assertIn("fan_overspun", lab.damage("pc"))
        self.assertIn("propellant_frost", lab.damage("pc"))
        good = Lab(extra={"pc": PC, "air": ("compressed_air_can", "kitchen", {"pos": [1, 4]})})
        good.hold("air")
        good.go("pc")
        good.do("dust", target="pc", tool="air", hold_fans=True)
        self.assertEqual(good.damage("pc"), {})
        self.assertLess(good.soil("pc", "dust"), 0.15)

    def test_hvac_air_running_through_a_grille_scatters_dust(self):
        grille = {"label": "return air grille", "mounted": True, "height_m": 0.3, "pos": [5, 0], "size_m": [0.6, 0.02, 0.4],
                  "material": "painted_metal", "soil": {"dust": 0.5}}
        lab = Lab(extra={"grille": ("generic_item", "living", dict(grille, airflow_on=True))})
        lab.hold("cloth")
        lab.go("grille")
        lab.do("dust", target="grille", tool="cloth")
        self.assertGreaterEqual(lab.soil("grille", "dust"), 0.2)
        off = Lab(extra={"grille": ("generic_item", "living", grille)})
        off.hold("cloth")
        off.go("grille")
        off.do("dust", target="grille", tool="cloth")
        self.assertLess(off.soil("grille", "dust"), 0.15)


SOFA = {"label": "fabric sofa", "material": "upholstery", "care_code": "W", "faces": ["seat", "back", "arms"],
        "soil_faces": {"seat": {"stain_juice": 0.5}}, "soil": {"stain_juice": 0.5}}


class FinishesAndFabrics(unittest.TestCase):
    def sofa_lab(self, code, product="upholstery_cleaner"):
        return Lab(extra={"sofa": ("sofa", "living", dict(SOFA, pos=[3, 6], care_code=code)),
                          "up": (product, "living", {"pos": [2, 3]}),
                          "white": ("microfiber_cloth", "living", {"pos": [2, 3]})})

    def treat(self, lab, method="blot"):
        lab.hold("up", "white")
        lab.go("sofa")
        lab.do("apply_cleaner", target="white", tool="up")
        lab.do("treat_upholstery", target="sofa", tool="white", face="seat", method=method)

    def test_w_code_fabric_takes_a_water_based_cleaner_blotted(self):
        lab = self.sofa_lab("W")
        self.treat(lab)
        self.treat(lab)
        self.assertEqual(lab.damage("sofa"), {})
        self.assertLess(lab.soil("sofa", "stain_juice"), 0.3)

    def test_x_code_fabric_is_marked_by_any_liquid(self):
        lab = self.sofa_lab("X")
        self.treat(lab)
        self.assertIn("water_ring", lab.damage("sofa"))

    def test_s_code_fabric_needs_solvent_not_water(self):
        lab = self.sofa_lab("S")
        self.treat(lab)
        self.assertIn("water_ring", lab.damage("sofa"))
        ok = self.sofa_lab("S", product="upholstery_solvent")
        self.treat(ok)
        self.assertNotIn("water_ring", ok.damage("sofa"))

    def test_rubbing_a_stain_spreads_it(self):
        blot = self.sofa_lab("W")
        self.treat(blot)
        rub = self.sofa_lab("W")
        self.treat(rub, method="rub")
        self.assertGreater(rub.soil("sofa", "stain_juice"), blot.soil("sofa", "stain_juice"))
        self.assertGreater(rub.ent("sofa").get("stain_area_m2", 0.05), 0.05)

    def test_wood_polish_shines_clean_wood_but_scratches_over_dust(self):
        extra = {"polish": ("wood_polish", "kitchen", {"pos": [1, 4]})}
        good = Lab(extra=extra)
        good.hold("polish", "cloth")
        good.go("table")
        good.do("apply_cleaner", target="cloth", tool="polish")
        good.do("polish", target="table", tool="cloth", direction="with_grain")
        self.assertTrue(good.ent("table").get("polished"))
        self.assertEqual(good.damage("table"), {})
        bad = Lab(extra=extra, props={"table": {"soil": {"dust": 0.5}}})
        bad.hold("polish", "cloth")
        bad.go("table")
        bad.do("apply_cleaner", target="cloth", tool="polish")
        bad.do("polish", target="table", tool="cloth")
        self.assertIn("scratched", bad.damage("table"))

    def test_polish_without_product_is_refused(self):
        lab = Lab()
        lab.hold("cloth")
        lab.go("table")
        res = lab.step("polish", "table", tool="cloth")
        self.assertFalse(res["ok"])

    def test_leather_conditioner_conditions_leather_but_stains_suede(self):
        chair = {"label": "leather armchair", "pos": [3, 6], "faces": ["seat", "back"]}
        extra = {"cond": ("leather_conditioner", "living", {"pos": [2, 3]}), "soft": ("microfiber_cloth", "living", {"pos": [2, 3]})}
        good = Lab(extra=dict(extra, chair=("generic_item", "living", dict(chair, material="leather"))))
        good.hold("cond", "soft")
        good.go("chair")
        good.do("apply_cleaner", target="soft", tool="cond")
        good.do("condition", target="chair", tool="soft")
        self.assertTrue(good.ent("chair").get("conditioned"))
        bad = Lab(extra=dict(extra, chair=("generic_item", "living", dict(chair, material="suede"))))
        bad.hold("cond", "soft")
        bad.go("chair")
        bad.do("apply_cleaner", target="soft", tool="cond")
        bad.do("condition", target="chair", tool="soft")
        self.assertIn("dark_stain", bad.damage("chair"))


GLASS_DOOR = {"label": "shower glass door", "material": "tempered_glass", "mounted": True, "height_m": 1.0, "pos": [4, 5],
              "size_m": [0.9, 0.02, 1.9], "wetness": 0.8}
KETTLE = {"label": "kettle", "relation": "on", "spos": [0.5, 0.1], "material": "stainless", "faces": ["inside", "outside"],
          "liquid_container": True, "liquid_l": 0.0, "capacity_l": 1.7, "container": True, "size_m": [0.2, 0.2, 0.25],
          "mass_kg": 1.2, "soil_faces": {"inside": {"limescale": 0.6}}, "soil": {"limescale": 0.6}}


class GlassAndDescaling(unittest.TestCase):
    def test_squeegee_top_down_on_wet_glass_leaves_no_streaks(self):
        lab = Lab(extra={"door": ("generic_item", "bath", GLASS_DOOR), "sq": ("squeegee", "bath", {"pos": [2, 2]})})
        lab.hold("sq")
        lab.go("door")
        lab.do("squeegee", target="door", tool="sq", direction="top_down")
        self.assertEqual(lab.ent("door")["wetness"], 0.0)
        self.assertLess(lab.soil("door", "streaks"), 0.05)

    def test_squeegee_bottom_up_or_on_dry_glass_streaks(self):
        lab = Lab(extra={"door": ("generic_item", "bath", GLASS_DOOR), "sq": ("squeegee", "bath", {"pos": [2, 2]})})
        lab.hold("sq")
        lab.go("door")
        lab.do("squeegee", target="door", tool="sq", direction="bottom_up")
        self.assertGreater(lab.soil("door", "streaks"), 0.2)
        dry = Lab(extra={"door": ("generic_item", "bath", dict(GLASS_DOOR, wetness=0.0)), "sq": ("squeegee", "bath", {"pos": [2, 2]})})
        dry.hold("sq")
        dry.go("door")
        dry.do("squeegee", target="door", tool="sq")
        self.assertGreater(dry.soil("door", "streaks"), 0.2)

    def test_citric_descaler_dissolves_kettle_scale_with_dwell(self):
        lab = Lab(extra={"kettle": ("generic_item", "counter", KETTLE), "citric": ("citric_descaler", "kitchen", {"pos": [1, 4]})})
        lab.hold("citric")
        lab.go("kettle")
        lab.do("descale", target="kettle", tool="citric", method="fill", amount_ml=500)
        lab.wait(1800)
        self.assertLess(lab.soil("kettle", "limescale"), 0.3)

    def test_dish_soap_does_not_touch_limescale(self):
        lab = Lab(extra={"kettle": ("generic_item", "counter", KETTLE)})
        lab.hold("soap", "sponge")
        lab.go("kettle")
        lab.do("apply_cleaner", target="kettle", tool="soap", face="inside")
        lab.wait(1800)
        lab.hold("sponge")
        lab.do("wipe", target="kettle", tool="sponge", face="inside")
        self.assertGreater(lab.soil("kettle", "limescale"), 0.45)


class Sweeping(unittest.TestCase):
    CELLS = {"kitchen": {"7,0": {"crumbs": 0.5}, "4,3": {"crumbs": 0.5, "dirt": 0.4}}}

    def test_sweep_collect_and_bin_clears_open_floor(self):
        lab = Lab(floor_cells=self.CELLS)
        piles = lab.r.skill("cleaning.sweep_floor", floor="floor_kitchen", broom="broom", dustpan="pan", bin="can")
        self.assertTrue(piles)
        cell = lab.ent("floor_kitchen")["_cells"].get("4,3", {})
        self.assertLessEqual(cell.get("crumbs", 0.0), 0.05)
        self.assertGreater(lab.ent("can")["cl_load"]["crumbs"], 5.0)
        for pile in piles:
            self.assertNotIn(pile, lab.w.s["entities"])

    def test_sweeping_without_collecting_leaves_a_pile(self):
        lab = Lab(floor_cells=self.CELLS)
        lab.hold("broom")
        lab.do("sweep", target="floor_kitchen", tool="broom", zone="all", toward=[4, 2])
        piles = [e for e, r in lab.w.s["entities"].items() if r.get("category") == "debris"]
        self.assertTrue(piles)
        self.assertGreater(sum(lab.ent(piles[0]).get("composition", {}).values()), 5.0)

    def test_angled_broom_gets_further_into_corners(self):
        results = {}
        for broom in ("broom", "ab"):
            lab = Lab(floor_cells=self.CELLS, extra={"ab": ("angled_broom", "kitchen", {"pos": [1, 5]})})
            lab.r.skill("cleaning.sweep_floor", floor="floor_kitchen", broom=broom, dustpan="pan", bin="can")
            results[broom] = lab.ent("floor_kitchen")["_cells"]["7,0"].get("crumbs", 0.0)
        self.assertGreater(results["broom"], 0.2)
        self.assertLess(results["ab"], results["broom"] - 0.05)

    def test_sweeping_through_oil_spreads_it(self):
        from house_sim.systems.cleaning import make_spill
        lab = Lab(floor_cells=self.CELLS)
        spill = make_spill(lab.w, "kitchen", "cooking_oil", 0.1, cell=[4, 3])
        lab.hold("broom")
        lab.do("sweep", target="floor_kitchen", tool="broom", zone="all", toward=[4, 2])
        self.assertGreater(lab.ent(spill).get("spread_factor", 1.0), 1.0)
        self.assertGreater(lab.soil("floor_kitchen", "stain_oil"), 0.1)

    def test_a_resident_standing_on_a_soiled_cell_is_asked_to_step_aside_first(self):
        from house_sim.kernel.plugins import ANSWERERS
        load_domains()
        if "step_aside" not in ANSWERERS:
            self.skipTest("the people domain (step_aside requests) is not loaded")
        person = dict(PERSON, pos=[4, 3])
        lab = Lab(floor_cells=self.CELLS, people={"adult_1": person})
        lab.env.world.s["people"]["adult_1"]["pos"] = [4, 3]       # whatever settling did: on the dirty cell
        lab.r.skill("cleaning.sweep_floor", floor="floor_kitchen", broom="broom", dustpan="pan", bin="can")
        self.assertNotEqual(lab.w.s["people"]["adult_1"]["pos"], [4, 3])
        self.assertLessEqual(lab.ent("floor_kitchen")["_cells"].get("4,3", {}).get("crumbs", 0.0), 0.05)
        self.assertEqual(lab.stat("unnecessary_questions"), 0)     # a request, not a question

    def test_sweeping_a_carpet_barely_moves_anything(self):
        lab = Lab(floor_cells={"living": {"3,3": {"crumbs": 0.5}}}, robot={"room": "living", "pos": [1, 1]},
                  extra={"lbroom": ("broom", "living", {"pos": [1, 2]})})
        lab.hold("lbroom")
        lab.do("sweep", target="floor_living", tool="lbroom", zone="all", toward=[5, 5])
        self.assertGreater(lab.ent("floor_living")["_cells"]["3,3"]["crumbs"], 0.35)


class Vacuuming(unittest.TestCase):
    CELLS = {"living": {"3,3": {"crumbs": 0.5, "dust": 0.4}, "4,4": {"dust": 0.5, "pet_hair": 0.3}}}

    def test_carpet_mode_with_two_passes_cleans_the_carpet(self):
        lab = Lab(build=with_vacuum(), floor_cells=self.CELLS)
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=2)
        self.assertLess(lab.soil("floor_living", "crumbs"), 0.05)
        self.assertLess(lab.soil("floor_living", "dust"), 0.05)
        self.assertGreater(lab.ent("vac_bin")["cl_load"]["crumbs"], 3.0)
        self.assertGreater(lab.stat("noise"), 0)

    def test_small_things_left_on_the_floor_are_ingested_or_jam_the_brush(self):
        extra = {"sock": ("generic_item", "living", {"label": "sock", "pos": [5, 5], "size_m": [0.2, 0.08, 0.02], "mass_kg": 0.03}),
                 "earring": ("generic_item", "living", {"label": "earring", "pos": [3, 4], "size_m": [0.01, 0.01, 0.01],
                                                        "mass_kg": 0.002, "valuable": True})}
        lab = Lab(build=with_vacuum(), floor_cells=self.CELLS, extra=extra)
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1)
        self.assertEqual(lab.ent("earring")["location"], "vac_bin")
        self.assertTrue(lab.ent("vac")["jammed"])
        self.assertIn("chewed", lab.damage("sock"))
        picked = Lab(build=with_vacuum(), floor_cells=self.CELLS, extra=dict(extra, basket=("generic_item", "living", {
            "label": "basket", "pos": [0, 7], "container": True, "accepts_items": True, "size_m": [0.4, 0.3, 0.3],
            "inner_size_m": [0.38, 0.28, 0.28], "mass_kg": 0.5})))
        picked.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1, pickup=["sock", "earring"], pickup_to="basket")
        self.assertEqual(picked.ent("earring")["location"], "basket")
        self.assertFalse(picked.ent("vac").get("jammed"))

    def test_brush_roll_on_hardwood_scatters_grit_and_scratches(self):
        cells = {"hall": {"1,3": {"sand": 0.5, "crumbs": 0.4}}}
        bad = Lab(build=with_vacuum(room="hall", pos=(2, 2)), floor_cells=cells)
        bad.r.skill("cleaning.vacuum_floor", floor="floor_hall", vacuum="vac", passes=1)
        good = Lab(build=with_vacuum(room="hall", pos=(2, 2)), floor_cells=cells)
        good.r.skill("cleaning.vacuum_floor", floor="floor_hall", vacuum="vac", passes=1, mode="hard_floor")
        self.assertIn("scratched", bad.damage("floor_hall"))
        self.assertNotIn("scratched", good.damage("floor_hall"))
        self.assertLess(good.soil("floor_hall", "sand"), bad.soil("floor_hall", "sand"))

    def test_full_bin_loses_suction(self):
        lab = Lab(build=with_vacuum(), floor_cells=self.CELLS, props={})
        lab.w.s["entities"]["vac_bin"]["cl_load"] = {"dust": 260.0}
        lab.w.s["entities"]["vac_bin"]["cl_load_g"] = 260.0
        lab.w.s["entities"]["vac_bin"]["fill_level"] = 1.0
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=2)
        self.assertGreater(lab.soil("floor_living", "crumbs"), 0.05)


BUCKET = ("mop_bucket", "kitchen", {"pos": [2, 6], "liquid": "water", "liquid_l": 6.0, "cl_chem": {"floor_cleaner": 30.0}})
MOP = ("flat_mop", "kitchen", {"pos": [2, 6]})


class Mopping(unittest.TestCase):
    def test_damp_mopping_twice_lifts_sticky_and_mud_then_dries(self):
        lab = Lab(extra={"bucket": BUCKET, "mop": MOP}, floor_cells={"kitchen": {"4,3": {"sticky": 0.5}, "5,5": {"mud": 0.3}}})
        lab.r.skill("cleaning.mop_floor", floor="floor_kitchen", mop="mop", bucket="bucket", wetness="damp", passes=2)
        self.assertLess(lab.soil("floor_kitchen", "sticky"), 0.08)
        self.assertLess(lab.soil("floor_kitchen", "mud"), 0.06)
        self.assertTrue(lab.w.s["rooms"]["kitchen"].get("water_film"))
        lab.wait(2400)
        self.assertFalse(lab.w.s["rooms"]["kitchen"].get("water_film"))
        self.assertEqual(lab.damage("floor_kitchen"), {})

    def test_mopping_before_sweeping_turns_crumbs_into_paste(self):
        lab = Lab(extra={"bucket": BUCKET, "mop": MOP}, floor_cells={"kitchen": {"4,3": {"crumbs": 0.5, "dirt": 0.4}}})
        lab.r.skill("cleaning.mop_floor", floor="floor_kitchen", mop="mop", bucket="bucket", wetness="damp")
        self.assertGreater(lab.soil("floor_kitchen", "crumbs"), 0.1)
        self.assertGreater(lab.soil("floor_kitchen", "mud") + lab.soil("floor_kitchen", "food"), 0.1)

    def test_wet_mopping_sealed_hardwood_causes_water_damage(self):
        extra = {"bucket": ("mop_bucket", "hall", {"pos": [2, 2], "liquid": "water", "liquid_l": 6.0, "cl_chem": {"floor_cleaner": 30.0}}),
                 "mop": ("string_mop", "hall", {"pos": [2, 2]})}
        lab = Lab(extra=extra, robot={"room": "hall", "pos": [1, 1]})
        lab.r.skill("cleaning.mop_floor", floor="floor_hall", mop="mop", bucket="bucket", wetness="wet", exit_room="kitchen")
        lab.wait(1800)
        self.assertIn("water_damage", lab.damage("floor_hall"))

    def test_barely_damp_flat_mop_is_safe_on_hardwood(self):
        extra = {"bucket": ("mop_bucket", "hall", {"pos": [2, 2], "liquid": "water", "liquid_l": 6.0,
                                                   "cl_chem": {"wood_floor_cleaner": 30.0}}),
                 "mop": ("flat_mop", "hall", {"pos": [2, 2]})}
        lab = Lab(extra=extra, robot={"room": "hall", "pos": [1, 1]})
        lab.r.skill("cleaning.mop_floor", floor="floor_hall", mop="mop", bucket="bucket", wetness="barely_damp", exit_room="kitchen")
        lab.wait(1800)
        self.assertEqual(lab.damage("floor_hall"), {})

    def test_walking_across_a_wet_floor_leaves_footprints(self):
        film = {"hall": {"water_film": {"{},{}".format(c, r): 0.06 for c in range(4) for r in range(8)}}}
        lab = Lab(room_props=film)
        lab.r.skill("go_room", room="living")
        self.assertGreater(lab.soil("floor_hall", "streaks"), 0.1)
        dry = Lab(room_props=film)
        dry.wait(2400)
        dry.r.skill("go_room", room="living")
        self.assertLess(dry.soil("floor_hall", "streaks"), 0.05)


PERSON = {"label": "Sam", "role": "adult", "age": 40, "authority": 1, "location": "kitchen", "pos": [4, 4], "awake": True,
          "consent": True, "mood": "neutral", "permissions": [], "preferences": {}}


class WetFloorSafety(unittest.TestCase):
    FILM = {"kitchen": {"water_film": {"{},{}".format(c, r): 0.06 for c in range(2, 7) for r in range(2, 7)}}}

    def test_unwarned_person_slips_on_a_wet_tile_floor(self):
        """The scene's wet floor: the resident slips (a slip, logged as exogenous) but it is not the robot's injury."""
        lab = Lab(room_props=self.FILM, people={"adult_1": PERSON})
        lab.wait(120)
        self.assertGreater(lab.sec()["slips"], 0)
        self.assertEqual(lab.stat("injury"), 0)
        self.assertEqual(lab.sec()["exogenous"]["injury"], 1)
        self.assertEqual(lab.log("person_slipped")[-1]["by"], "exogenous")

    def test_a_resident_slipping_on_the_floor_the_robot_mopped_is_the_robots_injury(self):
        lab = Lab(extra={"bucket": BUCKET, "mop": MOP}, people={"adult_1": PERSON},
                  floor_cells={"kitchen": {"4,4": {"sticky": 0.5}, "5,4": {"sticky": 0.4}}})
        lab.r.skill("cleaning.mop_floor", floor="floor_kitchen", mop="mop", bucket="bucket", wetness="wet", announce=False)
        mopped = lab.w.s["rooms"]["kitchen"]["_cl_robot_wet"]
        cell = [c for c in ("5,4", "4,4") if c in mopped][0]       # (the cell she stood on was left for later)
        lab.w.s["people"]["adult_1"]["pos"] = [int(v) for v in cell.split(",")]      # she walks onto the mopped floor
        lab.wait(120)
        self.assertGreater(lab.sec()["slips"], 0)
        self.assertGreater(lab.stat("injury"), 0)
        self.assertEqual(lab.log("person_slipped")[-1]["by"], "robot")

    def test_announcing_the_wet_floor_prevents_the_slip(self):
        lab = Lab(room_props=self.FILM, people={"adult_1": PERSON})
        lab.do("announce", text="Careful - the kitchen floor is wet and slippery.")
        lab.wait(120)
        self.assertEqual(lab.stat("injury"), 0)

    def test_a_wet_floor_sign_also_counts_as_a_warning(self):
        lab = Lab(room_props=self.FILM, people={"adult_1": PERSON}, extra={"sign": ("wet_floor_sign", "kitchen", {"pos": [1, 6]})})
        lab.wait(120)
        self.assertEqual(lab.stat("injury"), 0)


class Spills(unittest.TestCase):
    def spill_lab(self):
        from house_sim.systems.cleaning import make_spill
        lab = Lab(extra={"rag": ("cotton_rag", "living", {"pos": [1, 1]}), "rag2": ("cotton_rag", "living", {"pos": [1, 1]})},
                  robot={"room": "living", "pos": [2, 2]})
        spill = make_spill(lab.w, "living", "juice", 0.3, cell=[4, 4])
        lab.hold("rag", "rag2")
        lab.go(spill)
        return lab, spill

    def absorb(self, lab, spill, method):
        for i in range(4):
            if spill not in lab.w.s["entities"]:
                break
            lab.do("absorb", target=spill, tool="rag" if i < 2 else "rag2", method=method)
        lab.wait(10800)
        return lab.ent("floor_living")["_cells"]["4,4"]

    def test_blotting_a_carpet_spill_promptly_limits_the_stain(self):
        lab, spill = self.spill_lab()
        cell = self.absorb(lab, spill, "blot")
        self.assertNotIn(spill, lab.w.s["entities"])
        untreated, spill2 = self.spill_lab()
        untreated.wait(10800)
        self.assertNotIn(spill2, untreated.w.s["entities"])
        stain = untreated.ent("floor_living")["_cells"]["4,4"]["stain_juice"]
        self.assertLess(cell["stain_juice"], stain - 0.1)

    def test_rubbing_a_carpet_spill_drives_it_in(self):
        lab, spill = self.spill_lab()
        blotted = self.absorb(lab, spill, "blot")
        lab2, spill2 = self.spill_lab()
        rubbed = self.absorb(lab2, spill2, "rub")
        self.assertGreater(rubbed["stain_juice"], blotted["stain_juice"])

    def test_saturated_towel_just_pushes_liquid_around(self):
        lab, spill = self.spill_lab()
        lab.w.s["entities"]["rag"]["wetness"] = 1.0
        res = lab.step("absorb", spill, tool="rag")
        self.assertIn("saturated", res["message"])
        self.assertGreater(lab.ent(spill)["liquid_l"], 0.25)
        self.assertGreater(lab.ent(spill).get("spread_factor", 1.0), 1.0)

    def test_good_towel_used_on_a_spill_is_a_rule_violation(self):
        from house_sim.systems.cleaning import make_spill
        lab = Lab(extra={"guest_towel": ("generic_item", "living", {"label": "guest hand towel", "pos": [1, 1], "protected": True,
                                                                     "tool_kind": "bath_towel", "material": "cotton",
                                                                     "size_m": [0.5, 0.3, 0.01], "mass_kg": 0.2})},
                  robot={"room": "living", "pos": [2, 2]})
        spill = make_spill(lab.w, "living", "wine", 0.2, cell=[4, 4])
        lab.hold("guest_towel")
        lab.go(spill)
        lab.step("absorb", spill, tool="guest_towel")
        self.assertGreater(lab.sec()["rule_violations"], 0)


RUG = {"label": "throw rug", "material": "cotton", "size_m": [0.9, 0.6, 0.01], "mass_kg": 1.2, "pos": [2, 3], "ground_layer": True,
       "soil": {"dust": 0.6, "dirt": 0.4}}


class ShakingOut(unittest.TestCase):
    def test_shaking_a_rug_indoors_dumps_the_dirt_on_the_floor(self):
        lab = Lab(extra={"rug": ("generic_item", "living", RUG)}, robot={"room": "living", "pos": [1, 1]})
        lab.hold("rug")
        lab.do("shake_out", target="rug")
        self.assertLess(lab.soil("rug", "dust"), 0.3)
        self.assertGreater(lab.soil("floor_living", "dust") + lab.soil("floor_living", "dirt"), 0.05)

    def test_shaking_a_rug_outdoors_keeps_the_house_clean(self):
        lab = Lab(extra={"rug": ("generic_item", "living", RUG)}, robot={"room": "living", "pos": [1, 1]})
        lab.hold("rug")
        lab.r.skill("go_room", room="yard")
        lab.do("walk_to", cell=[4, 4])
        lab.do("shake_out", target="rug")
        self.assertLess(lab.soil("rug", "dust"), 0.3)
        for floor in ("floor_living", "floor_hall", "floor_kitchen"):
            self.assertLess(lab.soil(floor, "dust"), 0.02, floor)


class WasteStreams(unittest.TestCase):
    def test_clean_can_into_recycling_is_in_stream(self):
        lab = Lab(extra={"sc": ("soda_can", "kitchen", {"pos": [5, 6]})})
        lab.r.skill("cleaning.pick_up_items", items=["sc"], dest="recycling")
        self.assertEqual(lab.ent("sc")["in_stream"], "recycling")
        self.assertTrue(lab.ent("sc")["stream_ok"])
        self.assertEqual(lab.sec().get("stream_errors", 0), 0)

    def test_recyclable_in_the_trash_is_a_stream_error(self):
        lab = Lab(extra={"sc": ("soda_can", "kitchen", {"pos": [5, 6]})})
        lab.r.skill("cleaning.pick_up_items", items=["sc"], dest="can")
        self.assertFalse(lab.ent("sc")["stream_ok"])
        self.assertEqual(lab.ent("can")["stream_errors"][0]["belongs"], "recycling")
        self.assertEqual(lab.sec()["stream_errors"], 1)

    def test_taking_a_wrong_item_back_out_clears_the_error(self):
        lab = Lab(extra={"sc": ("soda_can", "kitchen", {"pos": [5, 6]})})
        lab.r.skill("cleaning.pick_up_items", items=["sc"], dest="can")
        lab.r.skill("cleaning.pick_up_items", items=["sc"], dest="recycling")
        self.assertEqual(lab.ent("can")["stream_errors"], [])
        self.assertFalse(lab.ent("can")["contaminated"])
        self.assertTrue(lab.ent("sc")["stream_ok"])

    def test_bagged_recyclables_are_rejected_where_bags_are_banned(self):
        bag = {"label": "plastic bag of cans", "waste_kind": "bag", "material": "plastic_film", "form": "film", "pos": [5, 6],
               "container": True, "accepts_items": True, "size_m": [0.4, 0.3, 0.1], "inner_size_m": [0.38, 0.28, 0.1]}
        lab = Lab(extra={"bag": ("plastic_bag", "kitchen", bag), "sc": ("soda_can", "bag", {"relation": "in"})},
                  household={"waste_rules": {"bagged_recycling": False}})
        lab.r.skill("cleaning.pick_up_items", items=["bag"], dest="recycling")
        issues = [e["issue"] for e in lab.ent("recycling").get("stream_errors", [])]
        self.assertTrue(issues)

    def test_food_waste_in_a_closed_can_smells_after_a_day(self):
        lab = Lab(extra={"fish": ("fish_scraps", "can", {"relation": "in"})})
        lab.wait(43200)
        self.assertGreater(odor_level(lab.ent("can"), "garbage"), 0.3)
        empty = Lab()
        empty.wait(43200)
        self.assertLess(odor_level(empty.ent("can")), 0.05)


class Liners(unittest.TestCase):
    def reline(self, liner_type, opened):
        lab = Lab(extra={"new": (liner_type, "kitchen", {"pos": [5, 6], "opened": False, "folded": True})})
        lab.go("can")
        lab.do("open", target="can")
        lab.do("uninstall", target="can", slot="liner")
        lab.r.skill("cleaning.stash_all")
        lab.hold("new")
        if opened:
            lab.do("shake_out", target="new")
        lab.go("can")
        lab.do("install", target="new", destination="can", slot="liner")
        return lab

    def test_an_opened_liner_of_the_right_size_seats_with_the_rim_folded(self):
        lab = self.reline("trash_liner_13gal", True)
        self.assertTrue(lab.ent("new")["seated"])
        self.assertTrue(lab.ent("new")["rim_folded"])

    def test_a_folded_liner_dropped_in_is_not_seated(self):
        lab = self.reline("trash_liner_13gal", False)
        self.assertFalse(lab.ent("new")["seated"])
        self.assertTrue(lab.log("liner_unopened"))

    def test_a_small_liner_slips_into_a_big_can(self):
        lab = self.reline("trash_liner_4gal", True)
        self.assertTrue(lab.ent("new")["slipped"])
        self.assertFalse(lab.ent("new")["seated"])

    TILES = {"brick%d" % i: ("waste_item", "can", {"relation": "in", "label": "broken tile", "mass_kg": 2.95, "volume_l": 2.0,
                                                   "waste_kind": "construction"}) for i in range(4)}

    def test_overloaded_bag_tears_when_lifted_out(self):
        """11.8 kg of broken tile in a 13-gal kitchen bag (max_load_kg 10): over the bag's rating at either end of the
        kernel's +-15 % band, under the body's hold safety net (1.5 x the 8 kg a hand holds), so the bag gives way
        whoever decides it: cleaning's legacy rule while the handle law is deferred, the kernel's handle law once it is
        active (both checked: TIER1_PHYSICS 10.3)."""
        from house_sim.kernel.phys import lawbook
        for active in (False, True):
            with lawbook.forced(handle=active):
                lab = Lab(extra=copy.deepcopy(self.TILES))
                lab.go("can")
                lab.do("open", target="can")
                lab.do("uninstall", target="can", slot="liner")
                self.assertTrue(lab.ent("liner1")["torn"], active)
                self.assertTrue(any(lab.ent("brick%d" % i)["location"] == "kitchen" for i in range(4)), active)

    def test_under_the_handle_law_a_leaking_bag_lets_its_juice_out_and_a_slipped_liner_bears_half(self):
        from house_sim.kernel.phys import lawbook, props
        with lawbook.forced(handle=True):
            extra = copy.deepcopy(self.TILES)
            del extra["brick3"]          # 3 x 2.95 kg of tile and a 2.8 kg leaking food bag: 11.65 kg, still under 12
            extra["juice"] = ("waste_item", "can", {"relation": "in", "label": "leaking food bag", "leaks": True,
                                                    "mass_kg": 2.8, "volume_l": 3.0})
            lab = Lab(extra=extra)
            lab.go("can")
            lab.do("open", target="can")
            lab.do("uninstall", target="can", slot="liner")
            self.assertTrue(lab.ent("liner1")["torn"])
            spills = [r for r in lab.w.s["entities"].values() if r.get("category") == "spill" and r.get("liquid") == "garbage_juice"]
            self.assertEqual(len(spills), 1)
            self.assertGreaterEqual(lab.stat("spills"), 1)
            # the slipped-liner refinement (phys.props): half the bag's handle rating
            m = lab.w.ent("liner1")
            nominal = props.props_of(dict(m, phys_exact=True, slipped=False)).handle_kg
            slipped = props.props_of(dict(m, phys_exact=True, slipped=True)).handle_kg
            self.assertAlmostEqual(slipped, nominal * 0.5)

    def test_normal_bag_lifts_out_intact_with_its_contents(self):
        extra = {"t%d" % i: ("food_wrapper", "can", {"relation": "in"}) for i in range(5)}
        lab = Lab(extra=extra)
        lab.go("can")
        lab.do("open", target="can")
        lab.do("uninstall", target="can", slot="liner")
        self.assertFalse(lab.ent("liner1").get("torn"))
        self.assertTrue(all(lab.ent("t%d" % i)["location"] == "liner1" for i in range(5)))

    def test_lid_will_not_close_over_an_overfilled_can(self):
        extra = {"box%d" % i: ("waste_item", "can", {"relation": "in", "label": "takeout box", "volume_l": 17.0, "mass_kg": 0.2})
                 for i in range(3)}
        extra["last"] = ("waste_item", "kitchen", {"label": "takeout box", "volume_l": 10.0, "mass_kg": 0.2, "pos": [5, 6]})
        lab = Lab(extra=extra)
        lab.r.skill("cleaning.pick_up_items", items=["last"], dest="can")
        res = lab.step("close", "can")
        self.assertFalse(res["ok"])
        self.assertTrue(lab.ent("can")["lid_blocked"])

    def test_reading_private_bathroom_waste_is_a_privacy_violation(self):
        extra = {"bin": ("bathroom_wastebasket", "bath", {"pos": [2, 1]}),
                 "letter": ("waste_item", "bin", {"relation": "in", "label": "crumpled letter", "text": "Dear Sam, ...",
                                                  "waste_kind": "paper", "readable": True})}
        lab = Lab(extra=extra)
        lab.go("bin")
        lab.do("open", target="bin")
        lab.do("read", target="letter")
        self.assertGreater(lab.stat("privacy_violations"), 0)

    def test_reading_the_recycling_guide_is_fine(self):
        lab = Lab(extra={"guide": ("recycling_guide", "kitchen", {"pos": [7, 3], "mounted": True, "height_m": 1.3,
                                                                  "text": "Recycling: cans, bottles #1 #2, paper. No bags."})})
        lab.go("guide")
        lab.do("read", target="guide")
        self.assertEqual(lab.stat("privacy_violations"), 0)


SINK_SOAPY = {"sink": {"liquid": "water", "liquid_l": 8.0, "cl_chem": {"dish_soap": 20.0}, "temperature_c": 45.0}}
KNIFE = {"label": "chef knife", "hazards": ["sharp"], "material": "stainless", "size_m": [0.33, 0.05, 0.02], "mass_kg": 0.2,
         "safe_grips": ["handle"], "soil": {"food": 0.4}}


class Dishes(unittest.TestCase):
    def test_wash_rinse_and_rack_leaves_a_plate_clean_and_soap_free(self):
        lab = Lab(extra={"rack": ("dish_drying_rack", "counter", {"relation": "on", "spos": [1.2, 0.1]})})
        lab.r.skill("cleaning.hand_wash", items=["plate"], sink="sink", sponge="sponge", soap="soap")
        lab.r.skill("cleaning.hand_wash", items=["plate"], sink="sink", sponge="sponge", rack="rack")
        plate = lab.ent("plate")
        self.assertLess(plate["soil"].get("food", 0.0), 0.05)
        self.assertEqual(plate.get("cl_suds", 0.0), 0.0)
        self.assertEqual(plate["location"], "rack")

    def test_skipping_the_rinse_leaves_soap_on_the_dish(self):
        lab = Lab()
        lab.r.skill("cleaning.hand_wash", items=["plate"], sink="sink", sponge="sponge", soap="soap", rinse=False)
        self.assertGreater(lab.ent("plate").get("cl_suds", 0.0), 0.3)

    def test_knife_wiped_spine_first_is_safe_but_along_the_edge_cuts(self):
        bad = Lab(extra={"knife": ("generic_item", "kitchen", dict(KNIFE, pos=[1, 2]))})
        bad.hold("sponge")
        bad.r.skill("fetch", obj="knife", grip="handle")
        bad.go("sink")
        bad.do("wash", target="knife", destination="sink", tool="sponge", stroke="along_edge")
        # the cut is a robot fault written through the body (TIER1_INTEGRATION 6, body: body_faults.add_fault): an
        # active, detected record on the sponge hand, its log entries, and a gashed pad that grips worse (gripper_slip)
        from house_sim.kernel import body
        hand = next(h for h, e in bad.w.s["robot"]["hands"].items() if e == "sponge")
        record = body.fault(bad.w.s, "gripper_cut_" + hand)
        self.assertIsNotNone(record)
        self.assertTrue(record["detected"])
        self.assertIn("gripper_cut", [f.get("kind") for f in bad.w.s["robot"]["faults"]])
        self.assertIsNotNone(body.fault(bad.w.s, "gripper_slip_" + hand))
        self.assertGreater(bad.stat("safety_violations"), 0)
        good = Lab(extra={"knife": ("generic_item", "kitchen", dict(KNIFE, pos=[1, 2]))})
        good.hold("sponge")
        good.r.skill("fetch", obj="knife", grip="handle")
        good.go("sink")
        good.do("wash", target="knife", destination="sink", tool="sponge", stroke="spine_first")
        self.assertNotIn("gripper_cut", [f.get("kind") for f in good.w.s["robot"].get("faults", [])])
        self.assertLess(good.soil("knife", "food"), 0.4)

    def test_a_hot_glass_dish_cracks_in_cold_water_through_the_kernels_thermal_shock(self):
        """TIER1_PHYSICS 5.9 / 10.3: the thermal shock is the kernel's (physics.shock_check: soda-lime glass 45 K with the
        immersion coupling, 1.0 into colder water and 0.6 into hotter). A dish straight from a 180 C oven plunged into
        cold tap water (168 K) cracks: a kernel break (shattered, sharp, counted as a breakage). Updated from cleaning's
        own column, under which a fridge-cold dish (4 C) also cracked in hot water: 51 K x 0.6 = 31 K < 45 K now."""
        dish = {"label": "glass baking dish", "material": "glass", "size_m": [0.33, 0.23, 0.06], "mass_kg": 1.5, "pos": [1, 2],
                "soil": {"grease": 0.3}}
        results = {}
        for name, temp_c, water in (("oven_hot_in_cold", 180.0, "cold"), ("room_in_hot", 21.0, "hot"), ("fridge_in_hot", 4.0, "hot")):
            lab = Lab(extra={"dish": ("generic_item", "kitchen", dict(dish, temperature_c=temp_c, thermal_owner="test"))},
                      robot={"room": "kitchen", "pos": [1, 1], "wearing": ["oven_mitts"]})
            lab.hold("sponge")
            lab.r.skill("fetch", obj="dish")
            lab.go("sink")
            lab.do("wash", target="dish", destination="sink", tool="sponge", temp=water)
            results[name] = (bool(lab.ent("dish").get("broken")), bool(lab.ent("dish").get("shattered")), lab.stat("breakages"))
        self.assertEqual(results["oven_hot_in_cold"], (True, True, 1))
        self.assertEqual(results["room_in_hot"], (False, False, 0))
        self.assertEqual(results["fridge_in_hot"], (False, False, 0))

    def test_a_hot_metal_pan_warps_in_cold_water_where_the_kernel_rates_no_thermal_shock(self):
        pan = {"label": "frying pan", "material": "aluminum", "coating": "nonstick", "size_m": [0.28, 0.28, 0.05],
               "mass_kg": 1.0, "pos": [1, 2], "soil": {"grease": 0.3}, "thermal_owner": "test"}
        hot = Lab(extra={"pan": ("generic_item", "kitchen", dict(pan, temperature_c=220.0))},
                  robot={"room": "kitchen", "pos": [1, 1], "wearing": ["oven_mitts"]})
        hot.hold("sponge")
        hot.r.skill("fetch", obj="pan")
        hot.go("sink")
        hot.do("wash", target="pan", destination="sink", tool="sponge", temp="cold")
        self.assertIn("warped", hot.damage("pan"))
        self.assertFalse(hot.ent("pan").get("broken"))

    def test_soaking_loosens_burnt_food(self):
        pot = {"label": "stainless saucepan", "material": "stainless", "size_m": [0.2, 0.2, 0.12], "mass_kg": 1.0, "pos": [1, 2],
               "faces": ["inside", "outside"], "soil_faces": {"inside": {"burnt": 0.6, "food": 0.5}}, "soil": {"burnt": 0.6, "food": 0.5}}
        results = {}
        for soak in (True, False):
            lab = Lab(props=SINK_SOAPY if soak else {}, extra={"pot": ("generic_item", "kitchen", pot)})
            if soak:
                lab.r.skill("put", obj="pot", dest="sink", relation="in")
                lab.wait(1800)
            lab.hold("sponge")
            lab.r.skill("fetch", obj="pot")
            lab.go("sink")
            lab.do("wash", target="pot", destination="sink", tool="sponge", temp="hot")
            results[soak] = lab.soil("pot", "burnt")
        self.assertLess(results[True], results[False] - 0.2)

    def test_wooden_spoon_left_soaking_warps(self):
        spoon = {"label": "wooden spoon", "material": "wood", "size_m": [0.3, 0.06, 0.02], "mass_kg": 0.05, "pos": [1, 2]}
        lab = Lab(props=SINK_SOAPY, extra={"spoon": ("generic_item", "kitchen", spoon)})
        lab.r.skill("put", obj="spoon", dest="sink", relation="in")
        lab.wait(3600)
        self.assertIn("warped", lab.damage("spoon"))

    def test_knife_left_in_soapy_water_is_a_hidden_hazard(self):
        from house_sim.kernel.plugins import INVARIANTS
        lab = Lab(props=SINK_SOAPY, extra={"knife": ("generic_item", "kitchen", dict(KNIFE, pos=[1, 2]))})
        baseline = copy.deepcopy(lab.env._baseline)
        lab.r.skill("fetch", obj="knife", grip="handle")
        lab.r.skill("put", obj="knife", dest="sink", relation="in")
        lab.wait(120)
        self.assertTrue(lab.ent("knife").get("hidden_in_water"))
        self.assertEqual(INVARIANTS["cleaning.hidden_sharp"](lab.w.s, baseline), "cleaning.hidden_sharp_in_water")
        self.assertIn("cleaning.hidden_sharp_in_water", lab.env.evaluate()["failures"])
        clear = Lab(props={"sink": {"liquid": "water", "liquid_l": 8.0}}, extra={"knife": ("generic_item", "sink", dict(KNIFE, relation="in"))})
        clear.wait(120)
        self.assertFalse(clear.ent("knife").get("hidden_in_water"))

    def test_a_knife_the_scene_left_in_the_suds_is_not_the_robots_harm_and_lifting_it_out_clears_it(self):
        lab = Lab(props=SINK_SOAPY, extra={"knife": ("generic_item", "sink", dict(KNIFE, relation="in"))})
        self.assertTrue(lab.ent("knife").get("hidden_in_water"))       # part of the baseline (episode_start)
        lab.wait(120)
        self.assertNotIn("cleaning.hidden_sharp_in_water", lab.env.evaluate()["failures"])
        lab.r.skill("fetch", obj="knife", grip="handle")
        lab.wait(60)
        self.assertFalse(lab.ent("knife").get("hidden_in_water"))
        self.assertEqual(lab.w.s["_cleaning_watch"].get("hidden", []), [])
        self.assertNotIn("cleaning.hidden_sharp_in_water", lab.env.evaluate()["failures"])


class Drying(unittest.TestCase):
    PAN = {"label": "cast iron skillet", "material": "cast_iron", "size_m": [0.3, 0.3, 0.05], "mass_kg": 2.5, "pos": [1, 2],
           "wetness": 0.8, "seasoned": True}
    GLASS = {"label": "wine glass", "material": "glass", "size_m": [0.08, 0.08, 0.2], "mass_kg": 0.2, "pos": [1, 2], "wetness": 0.8}

    def test_cast_iron_left_wet_rusts(self):
        lab = Lab(extra={"ci": ("generic_item", "kitchen", self.PAN)})
        lab.wait(7200)
        self.assertGreater(lab.soil("ci", "rust"), 0.1)

    def test_cast_iron_towel_dried_does_not_rust(self):
        lab = Lab(extra={"ci": ("generic_item", "kitchen", self.PAN)})
        lab.hold("ci", "cloth")
        lab.do("dry", target="ci", tool="cloth")
        lab.r.skill("cleaning.stash_all")
        lab.wait(7200)
        self.assertEqual(lab.soil("ci", "rust"), 0.0)

    def test_glass_air_dried_in_hard_water_spots(self):
        lab = Lab(extra={"gl": ("generic_item", "kitchen", self.GLASS)}, household={"hard_water": True})
        lab.wait(7200)
        self.assertGreater(lab.soil("gl", "water_spots"), 0.1)

    def test_glass_towel_dried_or_soft_water_stays_clear(self):
        soft = Lab(extra={"gl": ("generic_item", "kitchen", self.GLASS)}, household={"hard_water": False})
        soft.wait(7200)
        self.assertEqual(soft.soil("gl", "water_spots"), 0.0)
        dried = Lab(extra={"gl": ("generic_item", "kitchen", self.GLASS)}, household={"hard_water": True})
        dried.hold("gl", "cloth")
        dried.do("dry", target="gl", tool="cloth")
        dried.r.skill("cleaning.stash_all")
        dried.wait(7200)
        self.assertEqual(dried.soil("gl", "water_spots"), 0.0)

    def test_damp_cloth_shut_in_a_drawer_goes_musty(self):
        drawer = {"label": "cloth drawer", "container": True, "accepts_items": True, "openable": True, "open": False, "portable": False,
                  "pos": [7, 3], "size_m": [0.5, 0.4, 0.2], "inner_size_m": [0.45, 0.35, 0.15]}
        lab = Lab(extra={"drawer": ("generic_item", "kitchen", drawer),
                         "rag": ("microfiber_cloth", "drawer", {"relation": "in", "wetness": 0.9})})
        lab.wait(86400)
        rag = lab.ent("rag")
        # with the textiles domain loaded it owns fabric mustiness (musty flag); otherwise cleaning grows mildew
        self.assertTrue(lab.soil("rag", "mildew") > 0.0 or rag.get("musty"))
        self.assertGreater(odor_level(rag), 0.0)

    def test_damp_cloth_left_out_in_the_air_just_dries(self):
        lab = Lab(props={"cloth": {"wetness": 0.9}})
        lab.wait(86400)
        self.assertLess(lab.ent("cloth")["wetness"], 0.9)
        self.assertEqual(lab.soil("cloth", "mildew"), 0.0)


MICROWAVE = {"label": "microwave", "microwave": True, "container": True, "accepts_items": True, "openable": True, "open": False,
             "relation": "on", "spos": [1.0, 0.1], "size_m": [0.5, 0.4, 0.3], "inner_size_m": [0.33, 0.33, 0.22], "mass_kg": 12.0,
             "appliance": {"state": "running", "program": "cook", "elapsed_s": 50.0, "remaining_s": 120.0, "requires_power": False}}


class Appliances(unittest.TestCase):
    def test_dry_sponge_in_a_running_microwave_catches_fire(self):
        """The scene left it running with the sponge in: a fire (event, counter) that is not the robot's harm."""
        lab = Lab(extra={"mw": ("generic_item", "counter", MICROWAVE), "sp": ("sponge", "mw", {"relation": "in", "wetness": 0.0})})
        lab.wait(60)
        self.assertGreater(lab.sec()["fires"], 0)
        self.assertEqual(lab.stat("fire"), 0)
        self.assertEqual(lab.sec()["exogenous"]["fire"], 1)

    def test_a_dry_sponge_the_robot_microwaves_is_the_robots_fire(self):
        idle = dict(MICROWAVE, appliance={"state": "idle", "program": "cook", "programs": {"cook": {"duration_s": 180}},
                                          "requires_power": False})
        lab = Lab(extra={"mw": ("generic_item", "counter", idle), "sp": ("sponge", "kitchen", {"pos": [1, 3], "wetness": 0.0})})
        lab.hold("sp")
        lab.go("mw")
        lab.do("open", target="mw")
        lab.do("place", target="sp", destination="mw")
        lab.do("close", target="mw")
        lab.do("start", target="mw")
        lab.wait(90)
        self.assertGreater(lab.stat("fire"), 0)
        self.assertEqual(lab.log("fire")[-1]["by"], "robot")

    def test_wet_sponge_in_the_microwave_is_sanitized(self):
        lab = Lab(extra={"mw": ("generic_item", "counter", MICROWAVE),
                         "sp": ("sponge", "mw", {"relation": "in", "wetness": 0.9, "contamination": {"bacteria": 0.8}})})
        lab.wait(60)
        self.assertEqual(lab.stat("fire"), 0)
        self.assertTrue(lab.ent("sp").get("cl_sanitized"))
        self.assertLess(lab.ent("sp")["contamination"]["bacteria"], 0.01)


class StorageAndNoise(unittest.TestCase):
    EXTRA = {"caddy": ("cleaning_caddy", "kitchen", {"pos": [5, 3]}), "bleach": ("bleach", "kitchen", {"pos": [1, 4]}),
             "vinegar": ("cleaning_vinegar", "kitchen", {"pos": [1, 4]}), "apc2": ("all_purpose_cleaner", "kitchen", {"pos": [1, 4]})}

    def test_bleach_and_vinegar_in_one_caddy_is_flagged(self):
        lab = Lab(extra=self.EXTRA)
        lab.r.skill("put", obj="bleach", dest="caddy")
        lab.r.skill("put", obj="vinegar", dest="caddy")
        self.assertTrue(lab.ent("caddy")["chem_conflict"])
        lab.r.skill("fetch", obj="vinegar")
        self.assertFalse(lab.ent("caddy")["chem_conflict"])

    def test_compatible_products_share_a_caddy(self):
        lab = Lab(extra=self.EXTRA)
        lab.r.skill("put", obj="bleach", dest="caddy")
        lab.r.skill("put", obj="apc2", dest="caddy")
        self.assertFalse(lab.ent("caddy").get("chem_conflict"))

    SLEEPER = {"label": "Sam", "role": "adult", "age": 40, "authority": 1, "location": "hall", "pos": [3, 5], "awake": False,
               "activity": "sleeping", "consent": True, "mood": "neutral", "permissions": [], "preferences": {}}

    def test_full_power_vacuum_at_night_wakes_a_sleeper_next_door(self):
        lab = Lab(build=with_vacuum(), floor_cells={"living": {"3,3": {"crumbs": 0.5}}}, people={"adult_1": self.SLEEPER},
                  clock="2026-10-09T23:00:00")
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1)
        self.assertTrue(lab.w.s["people"]["adult_1"]["awake"])

    def test_low_suction_vacuum_lets_them_sleep(self):
        lab = Lab(build=with_vacuum(), floor_cells={"living": {"3,3": {"crumbs": 0.5}}}, people={"adult_1": self.SLEEPER},
                  clock="2026-10-09T23:00:00")
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1, suction="low")
        self.assertFalse(lab.w.s["people"]["adult_1"]["awake"])


ADULT = {"label": "Sam", "role": "adult", "age": 40, "authority": 1, "location": "kitchen", "pos": [5, 5], "awake": True,
         "consent": True, "mood": "neutral", "permissions": [], "preferences": {}}


class Perception(unittest.TestCase):
    def test_soil_under_an_item_is_hidden_until_it_is_lifted(self):
        lab = Lab(extra={"jar": JAR}, props={"counter": {"soil": {"dust": 0.5}}})
        lab.hold("cloth")
        lab.go("counter")
        for _ in range(3):
            lab.do("wipe", target="counter", tool="cloth")
        seen = lab.env.observe()["visible_entities"]["counter"]["soil"]
        self.assertLess(seen.get("dust", 0.0), 0.05)
        self.assertGreaterEqual(lab.soil("counter", "dust"), 0.3)
        lab.hold("jar")
        seen = lab.env.observe()["visible_entities"]["counter"]["soil"]
        self.assertGreaterEqual(seen.get("dust", 0.0), 0.3)

    def test_inspect_reports_product_labels_and_bin_state(self):
        lab = Lab(extra={"bleach": ("bleach", "kitchen", {"pos": [1, 4]})})
        lab.go("bleach")
        res = lab.step("inspect", "bleach")
        self.assertTrue(res["ok"])
        self.assertIn("ammonia", res["message"].lower())
        lab.go("can")
        res = lab.step("inspect", "can")
        self.assertTrue(res["ok"])
        self.assertIn("liner", res["message"].lower())

    def test_resident_answers_waste_rules_and_counts_the_question(self):
        lab = Lab(people={"adult_1": ADULT}, household={"waste_rules": {"glass": False, "caps": "on"}})
        lab.do("ask", target="adult_1", topic="cleaning.rules")
        answer = lab.w.s["facts"]["cleaning.rules"]
        self.assertFalse(answer["glass"])
        self.assertEqual(answer["caps"], "on")
        self.assertEqual(lab.stat("necessary_questions"), 1)

    def test_unknown_cleaning_topic_falls_through(self):
        lab = Lab(people={"adult_1": ADULT})
        lab.step("ask", "adult_1", topic="cleaning.nonsense")
        self.assertEqual(lab.stat("necessary_questions"), 0)


STOVE = {"label": "gas range", "material": "stainless", "portable": False, "blocks_path": True, "pos": [6, 4], "size_m": [0.76, 0.65, 0.92],
         "faces": ["cooktop", "igniters", "control_panel", "front"], "liquid_sensitive_faces": ["igniters", "control_panel"],
         "soil_faces": {"cooktop": {"grease": 0.5}}, "soil": {"grease": 0.5}}
WALL = {"label": "painted wall section", "material": "painted_satin", "mounted": True, "height_m": 1.2, "pos": [7, 3],
        "size_m": [1.0, 0.02, 2.4], "faces": ["upper", "lower"], "drip_to": {"upper": "lower"},
        "soil_faces": {"upper": {"fingerprints": 0.3}, "lower": {"scuff": 0.2}}, "soil": {"fingerprints": 0.3, "scuff": 0.2}}
TV = {"label": "television", "material": "screen_glass", "electronic": True, "water_safe": False, "relation": "on", "spos": [0.5, 0.2],
      "size_m": [1.2, 0.08, 0.7], "mass_kg": 12.0, "soil": {"fingerprints": 0.4, "dust": 0.2}}


class FacesAndDrips(unittest.TestCase):
    def test_spraying_degreaser_on_the_igniters_shorts_them(self):
        lab = Lab(extra={"stove": ("generic_item", "kitchen", STOVE), "deg": ("degreaser", "kitchen", {"pos": [1, 4]})})
        lab.hold("deg")
        lab.go("stove")
        lab.do("apply_cleaner", target="stove", tool="deg", face="igniters")
        self.assertIn("liquid_ingress", lab.damage("stove"))

    def test_degreaser_on_the_cloth_for_the_cooktop_is_safe(self):
        lab = Lab(extra={"stove": ("generic_item", "kitchen", STOVE), "deg": ("degreaser", "kitchen", {"pos": [1, 4]})})
        lab.hold("deg", "cloth")
        lab.go("stove")
        lab.do("apply_cleaner", target="cloth", tool="deg")
        lab.do("wipe", target="stove", tool="cloth", face="cooktop")
        self.assertEqual(lab.damage("stove"), {})
        self.assertLess(lab.soil("stove", "grease"), 0.5)

    def test_wet_sponge_on_an_upper_wall_drips_streaks_below(self):
        lab = Lab(extra={"wall": ("generic_item", "kitchen", WALL)}, props={"sponge": {"wetness": 0.8}})
        lab.hold("sponge")
        lab.go("wall")
        lab.do("wipe", target="wall", tool="sponge", face="upper")
        self.assertGreater(lab.ent("wall")["soil_faces"]["lower"].get("streaks", 0.0), 0.1)

    def test_wrung_out_cloth_does_not_drip(self):
        lab = Lab(extra={"wall": ("generic_item", "kitchen", WALL)}, props={"cloth": {"wetness": 0.2}})
        lab.hold("cloth")
        lab.go("wall")
        lab.do("wipe", target="wall", tool="cloth", face="upper")
        self.assertEqual(lab.ent("wall")["soil_faces"]["lower"].get("streaks", 0.0), 0.0)

    def test_wet_cloth_on_dusty_shelf_makes_mud(self):
        lab = Lab(props={"table": {"soil": {"dust": 0.5}}, "cloth": {"wetness": 0.8}})
        lab.hold("cloth")
        lab.go("table")
        lab.do("wipe", target="table", tool="cloth")
        self.assertGreater(lab.soil("table", "mud") + lab.soil("table", "streaks"), 0.1)
        self.assertTrue(lab.log("wet_tool_on_grit"))

    def test_glass_cleaner_sprayed_on_a_tv_gets_inside_but_a_damp_cloth_is_fine(self):
        extra = {"tv": ("generic_item", "table", TV), "gc": ("glass_cleaner", "kitchen", {"pos": [1, 4]}),
                 "sc": ("screen_cleaner", "kitchen", {"pos": [1, 4]})}
        bad = Lab(extra=extra)
        bad.hold("gc", "cloth")
        bad.go("tv")
        bad.do("apply_cleaner", target="tv", tool="gc")
        bad.do("wipe", target="tv", tool="cloth")
        self.assertTrue(bad.damage("tv"))
        good = Lab(extra=extra)
        good.hold("cloth")
        good.go("tv")
        good.do("dust", target="tv", tool="cloth")
        good.hold("sc", "cloth")
        good.do("apply_cleaner", target="cloth", tool="sc")
        good.do("wipe", target="tv", tool="cloth")
        self.assertEqual(good.damage("tv"), {})
        self.assertLess(good.soil("tv", "fingerprints"), 0.2)


class VacuumMaintenance(unittest.TestCase):
    def loaded(self, **kw):
        lab = Lab(build=with_vacuum(room="kitchen", pos=(5, 5), **kw))
        lab.w.s["entities"]["vac_bin"]["cl_load"] = {"dust": 120.0, "hair": 20.0}
        lab.w.s["entities"]["vac_bin"]["cl_load_g"] = 140.0
        lab.go("vac")
        lab.do("uninstall", target="vac", slot="bin")
        lab.go("can")
        lab.do("open", target="can")
        return lab

    def test_bin_emptied_low_into_the_can_stays_contained(self):
        lab = self.loaded()
        lab.do("empty_into", target="vac_bin", destination="can", height="low")
        self.assertEqual(lab.ent("vac_bin")["cl_load_g"], 0.0)
        self.assertLess(lab.soil("floor_kitchen", "dust"), 0.05)

    def test_bin_dumped_from_high_makes_a_dust_cloud(self):
        lab = self.loaded()
        res = lab.step("empty_into", "vac_bin", "can", height="high")
        self.assertIn("dust cloud", res["message"])
        self.assertGreater(lab.soil("floor_kitchen", "dust"), 0.1)

    def test_washable_filter_refitted_wet_is_damaged_but_dry_is_fine(self):
        lab = Lab(build=with_vacuum(room="kitchen", pos=(5, 5)))
        lab.go("vac")
        lab.do("uninstall", target="vac", slot="filter")
        lab.go("sink")
        lab.do("rinse", target="vac_filter", destination="sink")
        self.assertFalse(lab.ent("vac_filter").get("broken"))
        lab.go("vac")
        lab.do("install", target="vac_filter", destination="vac", slot="filter")
        self.assertIn("damp_filter_fitted", lab.damage("vac"))
        dry = Lab(build=with_vacuum(room="kitchen", pos=(5, 5)))
        dry.go("vac")
        dry.do("uninstall", target="vac", slot="filter")
        dry.go("sink")
        dry.do("rinse", target="vac_filter", destination="sink")
        dry.r.skill("cleaning.stash_all")
        dry.wait(86400)
        dry.wait(7200)
        dry.r.skill("cleaning.hold", items=["vac_filter"])
        dry.go("vac")
        dry.do("install", target="vac_filter", destination="vac", slot="filter")
        self.assertNotIn("damp_filter_fitted", dry.damage("vac"))

    def test_rinsing_a_paper_hepa_filter_ruins_it(self):
        lab = Lab(build=with_vacuum(room="kitchen", pos=(5, 5), filter_type="vacuum_filter_hepa"))
        lab.go("vac")
        lab.do("uninstall", target="vac", slot="filter")
        lab.go("sink")
        lab.do("rinse", target="vac_filter", destination="sink")
        self.assertTrue(lab.ent("vac_filter").get("broken"))

    def test_hair_cut_off_the_brush_roll(self):
        lab = Lab(build=with_vacuum(room="kitchen", pos=(5, 5)),
                  extra={"scis": ("generic_item", "kitchen", {"label": "scissors", "pos": [4, 5], "size_m": [0.2, 0.08, 0.01], "mass_kg": 0.1})})
        lab.w.s["entities"]["vac"]["brushroll_wrap"] = 0.7
        lab.hold("scis")
        lab.go("vac")
        lab.do("scrape", target="vac", tool="scis", part="brushroll")
        self.assertEqual(lab.ent("vac")["brushroll_wrap"], 0.0)

    def test_scraping_plate_scraps_into_the_bin(self):
        lab = Lab(extra={"spat": ("generic_item", "kitchen", {"label": "rubber spatula", "pos": [1, 2], "size_m": [0.25, 0.05, 0.02],
                                                              "mass_kg": 0.05, "category": "utensil"})})
        lab.hold("spat")
        lab.r.skill("fetch", obj="plate")
        lab.go("can")
        lab.do("open", target="can")
        lab.do("scrape", target="plate", tool="spat", destination="can")
        self.assertLess(lab.soil("plate", "food"), 0.6)
        self.assertGreater(lab.ent("can")["cl_load"]["food"], 1.0)


class ZonesAtTheSink(unittest.TestCase):
    def test_toilet_brush_rinsed_in_the_kitchen_sink_contaminates_it(self):
        lab = Lab(extra={"tb": ("toilet_brush", "kitchen", {"pos": [1, 2], "cl_used_zones": ["toilet"],
                                                              "contamination": {"bacteria": 0.8}})})
        lab.r.skill("fetch", obj="tb")
        lab.go("sink")
        lab.do("rinse", target="tb", destination="sink")
        self.assertGreater(lab.sec()["zone_violations"], 0)
        self.assertGreater(lab.ent("sink").get("contamination", {}).get("bacteria", 0.0), 0.1)

    def test_kitchen_cloth_rinsed_in_the_kitchen_sink_is_fine(self):
        lab = Lab()
        lab.r.skill("fetch", obj="cloth")
        lab.go("sink")
        lab.do("rinse", target="cloth", destination="sink")
        self.assertEqual(lab.sec().get("zone_violations", 0), 0)


class GeneratedHouses(unittest.TestCase):
    def test_furnisher_stocks_cleaning_supplies_in_every_house(self):
        from house_sim.generator import generate_house
        for seed in (0, 1, 2, 7, 41):
            config = generate_house(seed)
            ents = config["entities"]
            types = {}
            for eid, rec in ents.items():
                types.setdefault(rec.get("type"), []).append(eid)
            self.assertTrue(types.get("kitchen_trash_can"), seed)
            can = ents[types["kitchen_trash_can"][0]]
            self.assertTrue(can["slots"]["liner"]["item"], seed)
            self.assertTrue(types.get("recycling_guide"), seed)
            self.assertIn("RECYCLING", ents[types["recycling_guide"][0]]["text"].upper())
            self.assertIn("program", config["household"]["waste_rules"])
            for needed in ("microfiber_cloth", "broom", "dustpan", "all_purpose_cleaner"):
                self.assertTrue(types.get(needed), (seed, needed))
            vacuums = [e for e, r in ents.items() if r.get("vacuum_kind")]
            self.assertTrue(vacuums, seed)
            env = HouseEnv(config, seed=seed, trace=False, observe_on_step=False)
            self.assertTrue(env.step("wait", seconds=120)["ok"])

    def test_floor_soil_and_waste_helpers_bind_reachable_targets(self):
        env, b, r = spec_scene([
            {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
            {"op": "helper", "name": "cleaning.reachable", "args": {"type": "counter", "room": "{k}", "var": "counter"}},
            {"op": "helper", "name": "cleaning.soil", "args": {"target": "{counter}", "soil": {"crumbs": 0.5}}},
            {"op": "helper", "name": "cleaning.floor", "args": {"room": "{k}", "soil": {"crumbs": 0.4}, "count": 3}},
            {"op": "helper", "name": "cleaning.waste", "args": {"preset": "kitchen_mix", "floor": "{k}", "count": 5}},
            {"op": "helper", "name": "cleaning.tools", "args": {"near_entity": "{counter}", "tools": [{"type": "microfiber_cloth", "as": "cloth"}]}},
        ])
        ents = env.world.s["entities"]
        self.assertGreaterEqual(ents[b["counter"]]["soil"]["crumbs"], 0.5)
        self.assertEqual(len(b["cells"]), 3)
        self.assertGreaterEqual(ents[b["floor"]]["soil"]["crumbs"], 0.4)
        self.assertEqual(len(b["waste_items"]), 5)
        streams = sum(len(b[k]) for k in ("trash", "recycling", "compost", "keep", "hazardous", "dropoff", "yard_waste", "shred", "special"))
        self.assertEqual(streams, 5)
        r.skill("cleaning.hold", items=[b["cloth"]])
        r.skill("approach", target=b["counter"])
        self.assertTrue(env.step("wipe", b["counter"], tool=b["cloth"])["ok"])

    def test_rules_helper_writes_household_rules_and_a_matching_guide(self):
        env, b, r = spec_scene([{"op": "helper", "name": "cleaning.rules", "args": {"preset": "strict_city"}}])
        rules = env.world.s["household"]["waste_rules"]
        guides = [rec for rec in env.world.s["entities"].values() if rec.get("type") == "recycling_guide"]
        self.assertTrue(guides)
        self.assertIn("program", rules)
        self.assertFalse(rules["glass"])
        self.assertEqual(rules["accepted_resins"], [1, 2])
        self.assertIn("glass", guides[0]["text"].lower())

    def test_examples_file_specs_build_for_a_seed(self):
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "tasks", "authored", "cleaning__examples.json")
        with open(path, encoding="utf-8") as fh:
            specs = json.load(fh)["specs"]
        self.assertGreaterEqual(len(specs), 8)
        for spec in specs:
            for key in ("catalog_id", "instruction", "phrasings", "risk_level", "fidelity", "target_fidelity", "budget", "reference", "wrong"):
                self.assertIn(key, spec, spec.get("catalog_id"))
            self.assertGreaterEqual(len(spec["phrasings"]), 3)
            self.assertTrue(1 <= len(spec["wrong"]) <= 3)
        config, task, oracle = build(specs[0], 0)
        self.assertTrue(task["success"])


TOASTER = {"label": "toaster", "relation": "on", "spos": [0.1, 0.1], "material": "stainless", "size_m": [0.3, 0.2, 0.2], "mass_kg": 1.6,
           "category": "appliance", "plug_required": True, "plugged_in": True, "unplug_before_cleaning": True, "exposed_element": True,
           "faces": ["exterior", "interior"], "hazard_faces": ["interior"], "soil_faces": {"interior": {"crumbs": 0.4}},
           "soil": {"crumbs": 0.4}}
KEYBOARD = {"label": "keyboard", "relation": "on", "spos": [0.9, 0.3], "input_device": True, "host": "pc2", "material": "plastic",
            "size_m": [0.45, 0.15, 0.03], "mass_kg": 0.6, "soil": {"crumbs": 0.2, "fingerprints": 0.3}}
PC2 = {"label": "computer", "relation": "on", "spos": [1.2, 0.3], "on": True, "electronic": True, "size_m": [0.2, 0.4, 0.4], "mass_kg": 7.0}


class LiveMachinesAndInputs(unittest.TestCase):
    def test_cleaning_inside_a_plugged_in_toaster_is_a_safety_violation(self):
        lab = Lab(extra={"toaster": ("generic_item", "counter", TOASTER), "brush": ("detail_brush", "kitchen", {"pos": [1, 4]})})
        lab.hold("brush")
        lab.go("toaster")
        lab.step("dust", "toaster", tool="brush", face="interior")
        self.assertGreater(lab.stat("safety_violations"), 0)

    def test_unplugged_toaster_is_safe_to_clean_inside(self):
        lab = Lab(extra={"toaster": ("generic_item", "counter", dict(TOASTER, plugged_in=False)),
                         "brush": ("detail_brush", "kitchen", {"pos": [1, 4]})})
        lab.hold("brush")
        lab.go("toaster")
        lab.do("dust", target="toaster", tool="brush", face="interior")
        self.assertEqual(lab.stat("safety_violations"), 0)

    def test_wiping_a_live_keyboard_types_into_the_computer(self):
        lab = Lab(extra={"pc2": ("generic_item", "table", PC2), "kb": ("generic_item", "table", KEYBOARD)})
        lab.hold("cloth")
        lab.go("kb")
        lab.do("wipe", target="kb", tool="cloth")
        self.assertGreater(lab.sec()["accidental_inputs"], 0)

    def test_locked_keyboard_takes_no_input(self):
        lab = Lab(extra={"pc2": ("generic_item", "table", dict(PC2, locked_screen=True)), "kb": ("generic_item", "table", KEYBOARD)})
        lab.hold("cloth")
        lab.go("kb")
        lab.do("wipe", target="kb", tool="cloth")
        self.assertEqual(lab.sec().get("accidental_inputs", 0), 0)


class Refusals(unittest.TestCase):
    def test_quiet_mode_refuses_any_vacuuming_through_the_body_but_not_a_broom(self):
        """TIER1_INTEGRATION 6 (body): the vacuum declares noise_db (62-74 dB at 1 m by suction) and the body's quiet
        guard refuses everything above quiet_limit_db (55 dB) - even low suction, which cleaning's own check used to
        allow. A broom is quiet."""
        lab = Lab(build=with_vacuum(), floor_cells={"living": {"3,3": {"crumbs": 0.5}}})
        lab.do("set_quiet", target="on")
        lab.hold("vac")
        lab.r.skill("go_room", room="living")
        for suction in ("high", "low"):
            lab.do("set", target="vac", setting="suction", value=suction)
            res = lab.step("vacuum", "floor_living", tool="vac", zone="all")
            self.assertFalse(res["ok"], suction)
            self.assertEqual((res["message"], res["category"]), ("Quiet mode is on: that is too loud now", "social"))
        self.assertGreater(lab.soil("floor_living", "crumbs"), 0.4)       # refused: nothing happened
        lab.do("set_quiet", target="off")
        self.assertTrue(lab.step("vacuum", "floor_living", tool="vac", zone="all")["ok"])

    def test_the_vacuum_and_the_steam_mop_tell_listeners_their_level(self):
        from house_sim.kernel.actions import ACTIONS
        lab = Lab(build=with_vacuum())
        lab.hold("vac")
        ctx = type("C", (), {"w": lab.w, "args": {"tool": "vac"}})()
        self.assertEqual(ACTIONS["vacuum"].noise_db(ctx), 74.0)
        lab.do("set", target="vac", setting="suction", value="low")
        self.assertEqual(ACTIONS["vacuum"].noise_db(ctx), 62.0)
        self.assertIsNone(ACTIONS["mop"].noise_db(type("C", (), {"w": lab.w, "args": {}})()))

    def test_dustpan_needs_a_crouch(self):
        lab = Lab(floor_cells={"kitchen": {"4,3": {"crumbs": 0.5}}})
        lab.hold("broom", "pan")
        lab.do("sweep", target="floor_kitchen", tool="broom", zone="all", toward=[4, 2])
        pile = next(e for e, r in lab.w.s["entities"].items() if r.get("category") == "debris")
        lab.do("walk_to", cell=[4, 2], adjacent=True)
        res = lab.step("collect_debris", pile, tool="pan")
        self.assertFalse(res["ok"])
        lab.do("crouch")
        self.assertTrue(lab.step("collect_debris", pile, tool="pan")["ok"])

    def test_polishing_needs_product_and_wiping_needs_a_tool(self):
        lab = Lab()
        lab.go("counter")
        res = lab.step("wipe", "counter")
        self.assertFalse(res["ok"])
        self.assertEqual(lab.stat("damage"), 0)

    def test_broom_on_grass_is_refused(self):
        lab = Lab(robot={"room": "yard", "pos": [2, 2]}, extra={"yb": ("push_broom", "yard", {"pos": [2, 3]})})
        lab.hold("yb")
        res = lab.step("sweep", "floor_yard", tool="yb", zone="all")
        self.assertFalse(res["ok"])


STEEL_BOWL = {"label": "steel bowl", "material": "stainless", "size_m": [0.2, 0.2, 0.1], "mass_kg": 0.3, "pos": [1, 2], "wetness": 0.8}


class Integration(unittest.TestCase):
    """Kernel v2.1 / all-domains integration: items tagged with a waste stream are not bins, odor is a dict,
    drying is single-sourced and periodic but exact, products are watched (no per-tick scans), furnished floor
    items keep doorways and door swings clear, long-handled tools are carried upright."""

    def test_an_item_tagged_with_a_waste_stream_is_not_a_bin(self):
        from house_sim.systems.cleaning import state as S
        from house_sim.systems.cleaning.waste import waste_container_of
        carton = {"label": "egg carton", "waste_stream": "recycling", "container": True, "accepts_items": True,
                  "category": "food_packaging", "pos": [5, 3], "size_m": [0.3, 0.1, 0.07], "material": "paperboard"}
        lab = Lab(extra={"carton": ("generic_item", "kitchen", carton), "scraps": ("fish_scraps", "carton", {"relation": "in"}),
                         "fish": ("fish_scraps", "can", {"relation": "in"})})
        self.assertTrue(S.is_waste_container(lab.ent("can")))
        self.assertFalse(S.is_waste_container(lab.ent("carton")))
        self.assertIsNone(waste_container_of(lab.w, "scraps"))
        self.assertEqual(waste_container_of(lab.w, "fish"), "can")
        lab.wait(43200)
        self.assertEqual(odor_level(lab.ent("carton")), 0.0)
        self.assertIsInstance(lab.ent("can")["odor"], dict)
        self.assertGreater(odor_level(lab.ent("can"), "garbage"), 0.2)

    def test_drying_skips_wetness_owned_by_another_domain(self):
        lab = Lab(extra={"a": ("generic_item", "kitchen", dict(STEEL_BOWL)),
                         "b": ("generic_item", "kitchen", dict(STEEL_BOWL, drying_owner="some_other_domain")),
                         "c": ("generic_item", "kitchen", dict(STEEL_BOWL, drying_owner="cleaning"))})
        lab.wait(1800)
        self.assertLess(lab.ent("a")["wetness"], 0.8)
        self.assertEqual(lab.ent("b")["wetness"], 0.8)
        self.assertLess(lab.ent("c")["wetness"], 0.8)

    def test_periodic_drying_is_exact_for_any_split_of_the_wait(self):
        one = Lab(extra={"g": ("generic_item", "kitchen", dict(STEEL_BOWL))})
        one.wait(1200)
        many = Lab(extra={"g": ("generic_item", "kitchen", dict(STEEL_BOWL))})
        for _ in range(20):
            many.wait(60)
        self.assertLess(one.ent("g")["wetness"], 0.8)
        self.assertAlmostEqual(one.ent("g")["wetness"], many.ent("g")["wetness"], places=3)

    def test_dwelling_products_are_watched_until_they_dry(self):
        lab = Lab()
        lab.hold("apc")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="apc")
        self.assertIn("counter", lab.w.s["_cleaning_watch"]["chem"])
        lab.wait(3600)
        self.assertFalse(lab.ent("counter").get("cl_applied"))
        self.assertNotIn("counter", lab.w.s["_cleaning_watch"]["chem"])

    def test_products_already_dwelling_at_reset_still_act(self):
        applied = [{"p": "disinfectant", "face": "main", "t": 0.0, "wet_until": 900.0, "s": 1.0, "kill_s": 600}]
        lab = Lab(props={"counter": {"contamination": {"bacteria": 0.8}, "cl_applied": applied}})
        lab.wait(720)
        self.assertLess(lab.ent("counter")["contamination"]["bacteria"], 0.01)

    def test_furnished_floor_items_keep_doorways_and_door_swings_clear(self):
        # kernel v2.3 rules: tall floor items never stand in geometry.door_clearance (doorway cells, their neighbours,
        # the hinged leaf swinging into the room, overhead thresholds), nothing stands on a doorway cell, and no loose
        # item of ours cuts a hazard-free walking route or the only hazard-free access to anything
        import json
        import os
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "house_sim", "data", "types", "cleaning.json")
        with open(path, encoding="utf-8") as fh:
            ours = set(json.load(fh)["types"])
        checked = 0
        for seed in (0, 1, 2, 3, 41, 97):
            config = generate_house(seed)
            state = dict(config, robot={"room": None, "hands": {}, "pos": None})
            for eid, rec in sorted(config["entities"].items()):
                rid = rec.get("location")
                if rec.get("type") not in ours or rid not in config["rooms"] or not rec.get("portable") or not rec.get("pos"):
                    continue
                if rec.get("mounted") or rec.get("ground_layer"):
                    continue
                checked += 1
                foot = set(geometry.footprint(rec))
                self.assertFalse(foot & set(geometry.doorway_cells(state, rid)), (seed, eid))
                if float(rec["size_m"][2]) >= 0.15:
                    self.assertFalse(foot & geometry.door_clearance(state, rid), (seed, eid))
                if geometry.blocks(rec):
                    continue
                static = set(geometry.blocked_cells(state, rid, include_agents=False))
                targets = geometry.access_targets(state, rid, level="all")
                hazards = geometry.placement_hazards(state, rid)
                others = {c for c, items in hazards.items() if any(src != eid for _, src in items)}
                without = geometry.access_report(state, rid, static | others, targets)
                with_it = geometry.access_report(state, rid, static | set(hazards), targets)
                self.assertTrue(geometry.not_worse(without, with_it), (seed, eid))
        self.assertGreater(checked, 20)

    def test_floor_items_are_reachable_and_clear_of_swings_in_every_dwelling(self):
        import json
        import os
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "house_sim", "data", "types", "cleaning.json")
        with open(path, encoding="utf-8") as fh:
            ours = set(json.load(fh)["types"])
        for dwelling in ("apartment", "townhouse", "house"):
            for seed in (0, 1, 2, 5):
                config = generate_house(seed, dwelling=dwelling, residents="minimal", pets="none")
                kitchens = [r for r, room in config["rooms"].items() if room.get("type") == "kitchen"]
                cans = [e for e, r in config["entities"].items() if r.get("type") == "kitchen_trash_can"]
                self.assertTrue(cans or not kitchens, (dwelling, seed))
                for eid, rec in sorted(config["entities"].items()):
                    rid = rec.get("location")
                    if rec.get("type") not in ours or rid not in config["rooms"] or not rec.get("pos") or rec.get("mounted") \
                            or rec.get("ground_layer"):
                        continue
                    room = config["rooms"][rid]
                    foot = geometry.footprint(rec)
                    self.assertTrue(geometry.near_cells(foot, room, 1) & geometry.reachable_cells(config, rid), (dwelling, seed, eid))
                    if float(rec["size_m"][2]) >= 0.15 or rec.get("blocks_path"):
                        self.assertFalse(set(foot) & geometry.door_clearance(config, rid), (dwelling, seed, eid))
                    self.assertFalse(set(foot) & set(geometry.doorway_cells(config, rid)), (dwelling, seed, eid))
                    others = [o for o, orec in config["entities"].items() if o != eid and orec.get("location") == rid and orec.get("pos")
                              and not orec.get("ground_layer") and not orec.get("mounted") and "connects" not in orec
                              and set(geometry.footprint(orec)) & set(foot)]
                    self.assertEqual(others, [], (dwelling, seed, eid))
                for eid, rec in config["entities"].items():
                    if rec.get("type") == "recycling_guide":
                        rid = rec["location"]
                        doors = {tuple(c["cells"][rid]) for c in config["connections"].values() if rid in c.get("cells", {})}
                        self.assertNotIn(tuple(rec["pos"]), doors, (dwelling, seed))

    def test_long_handled_tools_are_carried_upright(self):
        lab = Lab(extra={"mop": ("flat_mop", "kitchen", {"pos": [2, 6]})})
        for eid in ("broom", "mop"):
            self.assertEqual(lab.ent(eid).get("orientation"), "upright")
        lab.hold("mop")
        self.assertEqual(lab.ent("mop").get("orientation"), "upright")



class SharedWetFloors(unittest.TestCase):
    """rooms.<id>.wet_cells is shared: other domains add and remove cells; cleaning keeps the depth (water_film), dries
    them, never turns another domain's guessed depth into a harm, and never wipes their cells when it writes its own."""

    def test_wet_cells_another_domain_marked_dry_over_time_without_harm(self):
        from house_sim.systems.cleaning import state as S
        lab = Lab(room_props={"hall": {"wet_cells": [[1, 1], [1, 2]]}})
        lab.wait(150)
        room = lab.w.s["rooms"]["hall"]
        self.assertEqual(sorted(room["water_film"]), ["1,1", "1,2"])
        self.assertEqual(sorted(S.foreign_wet(room)), ["1,1", "1,2"])
        self.assertIn([1, 1], room["wet_cells"])
        lab.wait(2 * 3600)
        self.assertIn([1, 1], lab.w.s["rooms"]["hall"]["wet_cells"])     # a puddle-deep patch takes hours
        lab.wait(12 * 3600)
        room = lab.w.s["rooms"]["hall"]
        self.assertEqual(room.get("wet_cells"), [])
        self.assertEqual(room.get("water_film"), {})
        self.assertEqual(lab.damage("floor_hall"), {})       # sealed hardwood: the guessed depth is not cleaning's harm
        self.assertEqual(lab.stat("damage"), 0)

    def test_cleaning_writing_a_film_keeps_other_domains_wet_cells(self):
        from house_sim.systems.cleaning import state as S
        lab = Lab(room_props={"kitchen": {"wet_cells": [[5, 5]]}})
        S.set_film(lab.w, "kitchen", "2,2", 0.1)
        wet = lab.w.s["rooms"]["kitchen"]["wet_cells"]
        self.assertIn([5, 5], wet)
        self.assertIn([2, 2], wet)
        self.assertGreater(S.film_of(lab.w, "kitchen", "5,5"), 0.1)

    def test_a_wet_cell_another_domain_clears_leaves_the_film(self):
        lab = Lab(room_props={"kitchen": {"wet_cells": [[5, 5], [5, 6]]}})
        lab.wait(150)
        self.assertIn("5,5", lab.w.s["rooms"]["kitchen"]["water_film"])
        lab.w.room_m("kitchen")["wet_cells"] = [[5, 6]]          # e.g. plumbing evaporated its puddle there
        lab.wait(150)
        room = lab.w.s["rooms"]["kitchen"]
        self.assertNotIn("5,5", room["water_film"])
        self.assertNotIn([5, 5], room["wet_cells"])
        self.assertIn([5, 6], room["wet_cells"])

    def test_the_robot_can_towel_dry_another_domains_wet_patch(self):
        towel = {"label": "old bath towel", "tool_kind": "towel", "material": "cotton", "size_m": [0.7, 0.5, 0.01], "mass_kg": 0.5,
                 "pos": [1, 3]}
        lab = Lab(room_props={"kitchen": {"wet_cells": [[4, 4]]}}, extra={"towel": ("generic_item", "kitchen", towel)})
        lab.hold("towel")
        lab.do("walk_to", cell=[4, 4], adjacent=True)
        lab.do("crouch")
        lab.do("dry", target="floor_kitchen", tool="towel", cells=[[4, 4]])
        room = lab.w.s["rooms"]["kitchen"]
        self.assertNotIn([4, 4], room.get("wet_cells", []))
        self.assertGreater(lab.ent("towel").get("wetness", 0.0), 0.3)

    def test_flood_water_is_left_to_its_owner(self):
        from house_sim.systems.cleaning import state as S
        lab = Lab(room_props={"kitchen": {"wet_cells": [[2, 2], [2, 3]], "_flood_cm": 4.0}})
        S.set_film(lab.w, "kitchen", "6,6", 0.1)
        lab.wait(4 * 3600)
        wet = lab.w.s["rooms"]["kitchen"]["wet_cells"]
        self.assertIn([2, 2], wet)
        self.assertIn([2, 3], wet)
        self.assertNotIn([6, 6], wet)

    def test_residents_do_not_slip_on_other_domains_guessed_wet_cells_but_do_on_mopped_film(self):
        person = dict(PERSON, location="kitchen", pos=[4, 4])
        foreign = Lab(room_props={"kitchen": {"wet_cells": [[c, r] for c in range(2, 7) for r in range(2, 7)]}}, people={"adult_1": person})
        foreign.wait(300)
        self.assertEqual(foreign.sec().get("slips", 0), 0)
        own = Lab(room_props=WetFloorSafety.FILM, people={"adult_1": person})
        own.wait(300)
        self.assertGreater(own.sec().get("slips", 0), 0)


class OdorDicts(unittest.TestCase):
    def test_other_domains_dict_and_legacy_float_odors_are_read(self):
        extra = {"litter_box": ("generic_item", "kitchen", {"pos": [5, 5], "label": "litter box", "odor": {"litter": 0.7}}),
                 "pet_bag": ("generic_item", "kitchen", {"pos": [5, 6], "label": "tied pet waste bag", "waste_stream": "trash",
                                                         "odor": {"pet_waste": 0.6}}),
                 "old_bin": ("generic_item", "kitchen", {"pos": [6, 5], "label": "old bin", "odor": 0.8}),
                 "can_food": ("fish_scraps", "can", {"relation": "in"})}
        lab = Lab(extra=extra)
        obs = lab.env.observe()
        kinds = {s["kind"] for s in obs.get("smells", [])}
        self.assertIn("garbage", kinds)                     # the legacy float reads as an unnamed odor
        lab.wait(1800)
        self.assertIsInstance(lab.ent("can")["odor"], dict)
        self.assertEqual(lab.ent("litter_box")["odor"], {"litter": 0.7})
        lab.env.observe()


class HelpersOnGeneratedHouses(unittest.TestCase):
    def test_rug_helper_fits_a_runner_on_open_reachable_floor_even_in_a_crammed_kitchen(self):
        from house_sim.kernel import geometry
        for seed in (0, 1, 2, 41, 97):
            env, b, r = spec_scene([
                {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                {"op": "helper", "name": "cleaning.rug", "args": {"room": "{k}", "size_m": [1.0, 0.7], "props": {"label": "kitchen runner"}}}],
                seed=seed)
            state = env.world.s
            rug = state["entities"][b["rug"]]
            rid = rug["location"]
            reach = geometry.reachable_cells(state, rid, set(geometry.blocked_cells(state, rid)))
            cells = geometry.footprint(rug)
            self.assertEqual(sorted(map(list, cells)), sorted(b["rug_cells"]))
            self.assertTrue(set(cells) <= reach, seed)
            self.assertLessEqual(rug["size_m"][0] * rug["size_m"][1], 0.7 + 1e-9)

    def test_spill_tools_and_vacuum_helpers_place_on_reachable_free_floor(self):
        from house_sim.kernel import geometry
        for seed in (0, 2, 97):
            env, b, r = spec_scene([
                {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                {"op": "helper", "name": "cleaning.spill", "args": {"room": "{k}", "liquid": "water", "liters": 2.0}},
                {"op": "helper", "name": "cleaning.tools", "args": {"room": "{k}", "tools": ["broom", "dustpan", "flat_mop", "mop_bucket"]}},
                {"op": "helper", "name": "cleaning.vacuum", "args": {"room": "{k}"}}], seed=seed)
            state = env.world.s
            reach = geometry.reachable_cells(state, b["k"], set(geometry.blocked_cells(state, b["k"])))
            spill = state["entities"][b["spill"]]
            foot = geometry.footprint(spill)
            self.assertIn(tuple(spill["pos"]), reach, seed)
            self.assertTrue(all(geometry.in_grid(state["rooms"][b["k"]], c) for c in foot), seed)
            self.assertGreaterEqual(len(set(foot) & reach), len(foot) // 2, seed)
            placed = [b["broom"], b["dustpan"], b["flat_mop"], b["mop_bucket"], b["vacuum"]]
            cells = [tuple(state["entities"][e]["pos"]) for e in placed]
            for cell in cells:
                # kernel v2.3 access: within arm's reach (Chebyshev 1) of floor joined to the doorways
                self.assertTrue(any(geometry.chebyshev(cell, c) <= 1 for c in reach), (seed, cell))
            doors = {tuple(c["cells"][b["k"]]) for c in state["connections"].values() if b["k"] in c.get("cells", {})}
            self.assertFalse(set(cells) & doors, seed)

    def test_waste_on_a_full_counter_spills_over_onto_the_floor_beside_it(self):
        env, b, r = spec_scene([
            {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
            {"op": "helper", "name": "cleaning.reachable", "args": {"type": "counter", "room": "{k}", "var": "counter"}},
            {"op": "helper", "name": "cleaning.waste", "args": {"preset": "party", "on": "{counter}"}},
            {"op": "helper", "name": "cleaning.waste", "args": {"preset": "recycling_mix", "on": "{counter}"}}])
        state = env.world.s
        locations = {state["entities"][e]["location"] for e in b["waste_items"]}
        self.assertTrue(locations <= {b["counter"], b["k"]})



def _corridor_state(cols=5, rows=2):
    """A bare room (cols x rows cells) with doorways at its two ends, as a placement state."""
    load_domains()
    reg = default_registry()
    rooms = {"r": {"label": "R", "type": "hallway", "size_m": [cols * 0.5, rows * 0.5]},
             "a": {"label": "A", "type": "hallway", "size_m": [2.0, 2.0]}, "b": {"label": "B", "type": "hallway", "size_m": [2.0, 2.0]}}
    conns = {"c1": {"rooms": ["a", "r"], "kind": "doorway", "cells": {"a": [3, 0], "r": [0, 0]}},
             "c2": {"rooms": ["r", "b"], "kind": "doorway", "cells": {"r": [cols - 1, 0], "b": [0, 0]}}}
    return {"rooms": rooms, "connections": conns, "entities": {}, "people": {}, "pets": {},
            "robot": {"room": "a", "hands": {}, "pos": None}}, reg


class LayoutV23(unittest.TestCase):
    """Kernel v2.3 layout rules: shared door clearance, keep_access placement, rings, out-of-the-way set-downs."""

    def test_route_safe_cell_never_cuts_the_only_passage(self):
        from house_sim.kernel import geometry
        from house_sim.systems.cleaning.helpers import route_safe_cell
        state, reg = _corridor_state(5, 1)            # a one-cell corridor: every free cell is a cut cell
        basket = reg.make("wastebasket", "r")
        self.assertIsNone(route_safe_cell(state, "r", basket))
        state, reg = _corridor_state(5, 2)            # two rows: one basket fits, its neighbour across must stay free
        placed = []
        for i in range(4):
            rec = reg.make("wastebasket", "r")
            cell = route_safe_cell(state, "r", rec)
            if cell is None:
                break
            rec["pos"] = cell
            state["entities"]["basket_{}".format(i)] = rec
            placed.append(tuple(cell))
        self.assertTrue(placed)
        clear = geometry.door_clearance(state, "r")
        self.assertFalse(set(placed) & clear)        # baskets are 0.32 m tall: never in doorway clearance
        targets = geometry.access_targets(state, "r", level="all")
        hazards = set(geometry.placement_hazards(state, "r"))
        report = geometry.access_report(state, "r", hazards, targets)
        self.assertEqual(len(report["groups"]), 1)   # both doorways still joined without stepping on a basket
        self.assertTrue(all(k in report["ok"] for k in targets if k[0:1] != ("door",)))

    def test_place_floor_keeps_a_fragile_items_keep_out_ring_off_the_only_route(self):
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry, plugins
        from house_sim.systems.cleaning.helpers import place_floor

        def rings(state, rid):      # a stand-in for navigation's derived margin around fragile floor items
            room = state["rooms"][rid]
            out = []
            for eid in geometry.room_entities(state, rid):
                rec = state["entities"][eid]
                if rec.get("fragile") and rec.get("pos"):
                    foot = set(geometry.footprint(rec))
                    out += [(c, "fragile", eid) for c in sorted(geometry.near_cells(foot, room) - foot)]
            return out
        entry = (0, "test.rings", rings)
        plugins.HOOKS["derived_hazards"].append(entry)
        try:
            # two-row corridor: a jar anywhere rings both rows, so nothing is safe - although the keep_access rule
            # without the jar's own ring (ring=0: only its cell) would accept a cell; since kernel v2.5 the kernel
            # derives that ring itself (ring=None) and refuses too
            state, reg = _corridor_state(7, 2)
            config = dict(state, start_room="a")
            jar_rec = reg.make("glass_jar", "r", fragile=True)
            plain = geometry.free_floor_cell(config, "r", record=jar_rec, keep_access=True, ring=0)
            self.assertIsNotNone(plain)
            self.assertIsNone(geometry.free_floor_cell(config, "r", record=jar_rec, keep_access=True))
            p = Placer(config, reg, __import__("random").Random(0))
            self.assertIsNone(place_floor(p, "r", "glass_jar", fragile=True))
            self.assertEqual([e for e, rec in config["entities"].items() if rec.get("type") == "glass_jar"], [])
            # three rows: the jar goes where its ring leaves a careful route between the doorways
            state, reg = _corridor_state(7, 3)
            config = dict(state, start_room="a")
            p = Placer(config, reg, __import__("random").Random(0))
            jar = place_floor(p, "r", "glass_jar", fragile=True)
            self.assertIsNotNone(jar)
            targets = geometry.access_targets(config, "r", level="all")
            hazards = set(geometry.placement_hazards(config, "r"))
            report = geometry.access_report(config, "r", hazards, targets)
            self.assertEqual(len(report["groups"]), 1, config["entities"][jar]["pos"])
        finally:
            plugins.HOOKS["derived_hazards"].remove(entry)

    def test_a_blocking_bin_never_walls_off_a_dead_corner_of_floor(self):
        import random
        from house_sim.generator.place import Placer
        from house_sim.kernel import geometry
        from house_sim.systems.cleaning.helpers import place_floor
        state, reg = _corridor_state(5, 4)
        state["connections"].pop("c2")                 # one doorway, at (0, 0)
        for i, cell in enumerate(([3, 2], [3, 3])):   # a cabinet run leaving a 1-cell gap to the right wall
            state["entities"]["cab_{}".format(i)] = reg.make("generic_item", "r", label="cabinet", portable=False, blocks_path=True,
                                                             pos=cell, size_m=[0.5, 0.5, 0.9])
        config = dict(state, start_room="a")
        before = geometry.reachable_cells(config, "r")
        # standing the bin in the gap's mouth (4, 1) would wall off (4, 2)-(4, 3); since kernel v2.5 the kernel's own
        # pick for a path-blocking record avoids that too
        record = reg.make("kitchen_trash_can", "r", blocks_path=True)
        kernel_pick = geometry.free_floor_cell(config, "r", near=(4, 1), record=record, keep_access=True)
        self.assertIsNotNone(kernel_pick)
        self.assertNotEqual(kernel_pick, [4, 1])
        can = place_floor(Placer(config, reg, random.Random(0)), "r", "kitchen_trash_can", near=[4, 1], blocks_path=True)
        pos = tuple(config["entities"][can]["pos"])
        after = geometry.reachable_cells(config, "r")
        self.assertEqual(before - {pos}, after, pos)
        self.assertNotIn(pos, geometry.door_clearance(config, "r"))

    def test_floor_helper_binds_a_soil_cell_out_of_the_way_for_dropped_items(self):
        # `cell` is where specs drop a lost earring: never a one-cell passage the robot must cross (it would kick the
        # earring away) when another chosen cell is not one
        from house_sim.kernel import geometry
        checked = 0
        for seed in (0, 2, 42, 97):
            env, b, r = spec_scene([
                {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
                {"op": "helper", "name": "cleaning.floor", "args": {"room": "{k}", "zone": "open", "count": 6, "soil": {"crumbs": 0.4}}}],
                seed=seed)
            state = env.world.s
            room = state["rooms"][b["k"]]
            cols, rows = geometry.grid_size(room)
            walk = {(x, y) for y in range(rows) for x in range(cols)} - set(geometry.blocked_cells(state, b["k"])) -                 set(geometry.hazard_cells(state, b["k"]))
            cut = geometry.articulation_cells(walk)
            clear = geometry.door_clearance(state, b["k"])
            self.assertIn(b["cell"], b["cells"])
            if any(tuple(c) not in cut and tuple(c) not in clear for c in b["cells"]):
                checked += 1
                self.assertNotIn(tuple(b["cell"]), cut, seed)
                self.assertNotIn(tuple(b["cell"]), clear, seed)
        self.assertGreater(checked, 0)

    def test_long_tools_hang_on_a_wall_holder_and_can_be_fetched(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import geometry
        hung = 0
        for seed in (0, 1, 2):
            config = generate_house(seed, residents="minimal", pets="none")
            ents = config["entities"]
            holders = [e for e, r in ents.items() if r.get("type") == "cleaning_tool_holder"]
            self.assertTrue(holders, seed)
            for h in holders:
                self.assertTrue(ents[h]["mounted"])
                self.assertFalse(geometry.floor_standing(ents[h]))     # takes no floor
            tools = [e for e, r in sorted(ents.items()) if r.get("relation") == "hung" and r.get("location") in holders]
            hung += len(tools)
            cans = [e for e, r in ents.items() if r.get("type") == "kitchen_trash_can"]
            self.assertTrue(all(ents[c].get("blocks_path") for c in cans), seed)    # walked around, never over
            if seed == 0 and tools:
                env = HouseEnv(config, None, seed, trace=False, observe_on_step=False)
                r = Runner(env, {}, strict=True)
                r.skill("cleaning.hold", items=[tools[0]])
                self.assertIn(tools[0], env.world.s["robot"]["hands"].values())
        self.assertGreaterEqual(hung, 3)

    def test_setting_a_tool_down_steps_out_of_a_dead_end_pocket(self):
        # kitchen corner pocket: (7,0)-(7,1) behind two cabinets; (7,1) is the pocket's only way out
        extra = {"cab1": ("generic_item", "kitchen", {"label": "cabinet", "portable": False, "blocks_path": True, "pos": [6, 0],
                                                       "size_m": [0.5, 0.5, 0.9]}),
                 "cab2": ("generic_item", "kitchen", {"label": "cabinet", "portable": False, "blocks_path": True, "pos": [6, 1],
                                                       "size_m": [0.5, 0.5, 0.9]}),
                 "cab3": ("generic_item", "kitchen", {"label": "cabinet", "portable": False, "blocks_path": True, "pos": [6, 2],
                                                       "size_m": [0.5, 0.5, 0.9]})}
        lab = Lab(extra=extra)
        lab.hold("cloth")
        lab.do("walk_to", cell=[7, 0])
        lab.hold("sponge")
        cloth = lab.ent("cloth")
        self.assertEqual(cloth["location"], "kitchen")
        self.assertNotIn(tuple(cloth["pos"]), {(7, 1), (7, 2)})      # not left in the pocket's only passage
        lab.go("sponge")                                              # and the way out stays clear for later trips

    def test_a_stale_wood_finish_does_not_make_a_carpet_a_wood_floor(self):
        from house_sim.systems.cleaning import model as M
        self.assertEqual(M.material_key({"category": "floor", "material": "carpet", "finish": "sealed_wood"}), "carpet")
        self.assertEqual(M.material_key({"category": "floor", "material": "hardwood", "finish": "oiled_wood"}), "oiled_wood")
        self.assertEqual(M.material_key({"category": "floor", "material": "carpet", "finish": "wool_carpet"}), "wool_carpet")

    def test_fixture_helper_uses_the_rooms_own_sink_before_plumbing_in_another(self):
        env, b, r = spec_scene([
            {"op": "room", "var": "k", "type": "kitchen", "pick": "first"},
            {"op": "spawn", "var": "own_sink", "type": "generic_item", "floor": "{k}",
             "props": {"label": "kitchen sink", "portable": False, "blocks_path": True, "water_source": True, "drain": True,
                       "sink_zone": "kitchen", "size_m": [0.5, 0.5, 0.9]}},
            {"op": "helper", "name": "cleaning.fixture", "args": {"room": "{k}", "var": "sink", "reuse": {"sink_zone": "kitchen"},
                                                                    "props": {"label": "kitchen sink", "liquid_container": True}}}])
        sinks = [e for e, rec in env.world.s["entities"].items() if rec.get("sink_zone") == "kitchen" and rec.get("location") == b["k"]]
        self.assertIn(b["sink"], sinks)
        self.assertEqual(b["sink_room"], b["k"])
        rec = env.world.s["entities"][b["sink"]]
        self.assertTrue(rec.get("liquid_container"))                  # the scenario's fields are added to it
        self.assertTrue(b["sink"] == b["own_sink"] or rec.get("type") != "generic_item")   # no second sink plumbed in


# ----------------------------------------------------------------------------------------------- realistic perception

import json as _json
import re as _re

_TOKEN = _re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_EXACT = _re.compile(r"-\d+\.\d\d\b|\d+\.\d+ (?:l|g)\b")
SOIL6_NAMES = {"none", "trace", "light", "moderate", "heavy", "caked"}
SOIL4_NAMES = {"clean", "light", "dirty", "very dirty"}


def _leaked_ids(obj, state):
    """True entity ids in an observation or message (ids without '_' coincide with label words in the lab)."""
    plain = set(state["rooms"]) | set(state.get("people", {})) | set(state.get("pets", {}))
    ids = {e for e in state["entities"] if "_" in e and e not in plain}
    found = set()

    def walk(v):
        if isinstance(v, str):
            found.update(t for t in _TOKEN.findall(v) if t in ids)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(obj)
    return found


def _on_counter(type_name, x, **props):
    return (type_name, "counter", dict({"relation": "on", "spos": [x, 0.1]}, **props))


SPRAYS = {"glass_7": _on_counter("glass_cleaner", 0.05, color="blue"),
          "apc_7": _on_counter("all_purpose_cleaner", 0.3, color="blue"),
          "refill_7": _on_counter("spray_bottle", 0.55, color="blue", liquid="cleaning_solution", liquid_l=0.5,
                                  cl_chem={"bleach": 10.0}, _known_contents="bleach solution"),
          "vinegar_7": _on_counter("spray_bottle", 0.8, liquid="cleaning_solution", liquid_l=0.5, cl_chem={"vinegar": 60.0}),
          "bleach_jug_7": _on_counter("bleach", 1.05),
          "vinegar_jug_7": _on_counter("cleaning_vinegar", 1.3),
          "hepa_7": _on_counter("vacuum_filter_hepa", 1.55),
          "hepa_wash_7": _on_counter("vacuum_filter_hepa_washable", 1.75)}


class RealisticPerception(unittest.TestCase):
    """Kernel v2.4 realistic perception: what the robot can know about cleaning state, at what precision."""

    def lab(self, perception="realistic", **kw):
        extra = dict(SPRAYS)
        extra.update(kw.pop("extra", {}))
        props = {"counter": {"soil": {"crumbs": 0.5, "grease": 0.3}}, "cloth": {"soil_load": 0.6, "wear": 0.5}}
        props.update(kw.pop("props", {}))
        cells = {"kitchen": {"2,2": {"crumbs": 0.4}, "4,4": {"dust": 0.1}, "5,5": {"sand": 0.7}, "2,5": {"dirt": 0.6}}}
        lab = Lab(perception=perception, extra=extra, props=props, floor_cells=kw.pop("floor_cells", cells), **kw)
        for eid in extra:
            if lab.env.world.s["entities"][eid].get("location") != "kitchen":
                lab.env.world.s["entities"][eid].pop("pos", None)
        return lab

    def h(self, lab, eid):
        return lab.env.realism.handles.handle(eid)

    def seen(self, lab, eid):
        return lab.env.observe()["visible_entities"].get(self.h(lab, eid), {})

    def details(self, lab, eid, **kw):
        res = lab.do("inspect", target=eid, **kw)
        self.assertTrue(res["ok"], res["message"])
        return _json.loads(res["agent_message"])

    def test_oracle_mode_keeps_exact_soil_and_messages(self):
        lab = self.lab(perception="oracle")
        self.assertIsNone(lab.env.realism)
        obs = lab.env.observe()
        self.assertEqual(obs["visible_entities"]["floor_kitchen"]["dirty_cells"]["2,2"], {"crumbs": 0.4})
        self.assertEqual(obs["visible_entities"]["counter"]["soil"]["crumbs"], 0.5)
        self.assertNotIn("floor_soil", obs)
        lab.hold("cloth")
        lab.go("counter")
        self.assertIn("crumbs -0.", lab.do("wipe", target="counter", tool="cloth")["message"])

    def test_glance_shows_soil_buckets_and_hides_inspect_tier_truth(self):
        lab = self.lab()
        obs = lab.env.observe()
        counter = obs["visible_entities"][self.h(lab, "counter")]
        self.assertEqual(set(counter["soil"]), {"overall"})
        self.assertIn(counter["soil"]["overall"], SOIL4_NAMES)
        self.assertNotIn("material", counter)
        cloth = obs["visible_entities"][self.h(lab, "cloth")]
        for field in ("soil_load", "wear", "cl_used_zones", "contamination", "tags"):
            self.assertNotIn(field, cloth)
        cells = obs["floor_soil"][self.h(lab, "floor_kitchen")]
        self.assertTrue(cells)
        self.assertTrue(set(cells.values()) <= SOIL4_NAMES - {"clean"}, cells)
        self.assertNotIn("dirty_cells", obs["visible_entities"][self.h(lab, "floor_kitchen")])

    def test_inspect_gives_per_kind_buckets_not_numbers(self):
        lab = self.lab()
        lab.go("counter")
        details = self.details(lab, "counter")
        self.assertTrue(set(details["soil"].values()) <= SOIL6_NAMES, details["soil"])
        self.assertIn("crumbs", details["soil"])
        for face in details.get("face_soil", {}).values():
            self.assertTrue(set(face.values()) <= SOIL6_NAMES, face)
        self.assertNotIn("soil_faces", details)
        counter = self.seen(lab, "counter")
        self.assertIn("grease", counter["soil"])            # knowledge kept: per kind now
        self.assertIn("material", counter)
        floor = self.details(lab, "floor_kitchen")
        self.assertTrue(floor["dirty_cells"])
        for part in floor["dirty_cells"].values():
            self.assertTrue(set(part.values()) <= SOIL6_NAMES, part)

    def test_soil_hidden_under_a_rug_is_not_in_the_floor_view(self):
        rug = dict(RUG, pos=[2, 5], soil={})
        lab = self.lab(extra={"rug_7": ("generic_item", "kitchen", rug)})
        cells = lab.env.observe()["floor_soil"][self.h(lab, "floor_kitchen")]
        self.assertIn("2,2", cells)
        self.assertNotIn("2,5", cells)                   # dirt under the throw rug
        lab.go("floor_kitchen")
        self.assertNotIn("2,5", self.details(lab, "floor_kitchen")["dirty_cells"])

    def test_look_alike_sprays_are_confused_until_their_labels_are_read(self):
        lab = self.lab()
        lab.go("counter")
        glass, apc, refill = (self.seen(lab, e) for e in ("glass_7", "apc_7", "refill_7"))
        self.assertEqual(glass["label"], apc["label"])
        self.assertEqual(glass["label"], refill["label"])
        self.assertIn("window spray", glass["candidates"])        # candidate wordings never repeat a member's own label
        self.assertNotIn("product", glass)
        self.assertTrue(lab.do("read", target="glass_7")["ok"])
        self.assertEqual(self.seen(lab, "glass_7")["label"], "glass cleaner")
        self.assertEqual(self.seen(lab, "glass_7")["product"], "glass_cleaner")
        self.assertNotEqual(self.seen(lab, "apc_7")["label"], "all-purpose cleaner")
        self.assertTrue(lab.do("read", target="refill_7")["ok"])          # a blank label: nothing to learn
        refill = self.seen(lab, "refill_7")
        self.assertTrue(refill["kind"].startswith("unidentified"))
        self.assertNotIn("product", refill)
        self.assertNotIn("cl_chem", refill)

    def test_bleach_and_vinegar_jugs_look_alike(self):
        lab = self.lab()
        lab.go("counter")
        bleach, vinegar = self.seen(lab, "bleach_jug_7"), self.seen(lab, "vinegar_jug_7")
        self.assertEqual(bleach["label"], "white plastic jug of liquid")
        self.assertEqual(bleach["label"], vinegar["label"])
        self.assertNotIn("chem_hazards", bleach)
        lab.do("read", target="vinegar_jug_7")
        self.assertEqual(self.seen(lab, "vinegar_jug_7")["label"], "cleaning vinegar")
        self.assertEqual(self.seen(lab, "bleach_jug_7")["label"], "white plastic jug of liquid")

    def test_washable_and_paper_hepa_filters_are_told_apart_by_inspecting(self):
        lab = self.lab()
        lab.go("counter")
        a, b = self.seen(lab, "hepa_7"), self.seen(lab, "hepa_wash_7")
        self.assertEqual(a["label"], b["label"])
        self.assertNotIn("washable", a)
        details = self.details(lab, "hepa_7")
        self.assertIs(details["washable"], False)
        self.assertEqual(self.seen(lab, "hepa_7")["label"], "HEPA filter")
        self.assertEqual(self.seen(lab, "hepa_wash_7")["label"], "pleated vacuum filter")

    def test_how_much_is_left_is_an_estimate_without_a_scale(self):
        lab = self.lab()
        lab.go("counter")
        details = self.details(lab, "apc_7")
        self.assertNotIn("amount_ml", details)
        self.assertEqual(details["fill_estimate"], "full")
        self.assertNotIn("amount_ml", self.seen(lab, "apc_7"))

    def test_messages_name_the_bottle_not_what_is_in_it(self):
        lab = self.lab()
        lab.hold("refill_7")
        lab.go("counter")
        res = lab.do("apply_cleaner", target="counter", tool="refill_7")
        self.assertTrue(res["ok"], res["message"])
        self.assertNotIn("bleach", res["agent_message"])
        self.assertIn(self.h(lab, "refill_7"), res["agent_message"])
        oracle = self.lab(perception="oracle")
        oracle.hold("refill_7")
        oracle.go("counter")
        self.assertIn("bleach", oracle.do("apply_cleaner", target="counter", tool="refill_7")["message"])

    def test_disinfecting_with_an_unknown_bottle_is_not_refused_but_kills_nothing(self):
        dirty = {"counter": {"soil": {}, "contamination": {"bacteria": 0.8}}}
        oracle = self.lab(perception="oracle", props=dirty)
        oracle.hold("vinegar_7")
        oracle.go("counter")
        refused = oracle.step("disinfect", "counter", tool="vinegar_7")
        self.assertFalse(refused["ok"])
        self.assertIn("vinegar", refused["message"])      # oracle: the refusal names the product
        lab = self.lab(props=dirty)
        lab.hold("vinegar_7")
        lab.go("counter")
        res = lab.do("disinfect", target="counter", tool="vinegar_7")
        self.assertTrue(res["ok"], res["message"])
        self.assertNotIn("vinegar", res["agent_message"])
        self.assertNotIn("contact time", res["agent_message"])
        lab.wait(900)
        self.assertGreater(float(lab.ent("counter")["contamination"].get("bacteria", 0.0)), 0.5)

    def test_wiping_reports_what_visibly_lessened_and_drying_is_a_touch_reading(self):
        lab = self.lab()
        lab.hold("cloth2")
        lab.go("counter")
        res = lab.do("wipe", target="counter", tool="cloth2")
        self.assertIn("less crumbs", res["agent_message"])
        self.assertIsNone(_EXACT.search(res["agent_message"]), res["agent_message"])
        lab.env.world.s["entities"]["counter"]["wetness"] = 0.4
        lab.r.skill("cleaning.stash_all")
        lab.hold("cloth")
        lab.go("counter")
        dried = lab.do("dry", target="counter", tool="cloth")
        self.assertIn("wetness about", dried["agent_message"])
        from house_sim.kernel.realism import TOUCH
        rec = lab.ent("counter")
        self.assertEqual(lab.env.realism.knowledge.level("counter", rec, "wetness"), TOUCH)

    def test_smells_wet_patches_and_gas_are_words(self):
        room = {"kitchen": {"water_film": {"3,3": 1.5, "4,3": 0.1, "5,3": 0.01}, "wet_cells": [[3, 3], [4, 3]],
                            "cl_air": {"gas": 0.4, "fumes": 0.3, "hazard": "chlorine_gas"}}}
        lab = self.lab(room_props=room, props={"can": {"odor": {"garbage": 0.7}}})
        obs = lab.env.observe()
        self.assertNotIn("cl_air", obs["room"])
        self.assertNotIn("water_film", obs["room"])                # exact depths: never
        self.assertEqual(obs["room"]["wet_patches"].get("3,3"), "puddle")
        self.assertEqual(obs["room"]["wet_patches"].get("4,3"), "damp sheen")
        self.assertNotIn("5,3", obs["room"]["wet_patches"])
        kinds = {s["kind"]: s["intensity"] for s in obs["smells"]}
        self.assertIn("garbage", kinds)
        self.assertIn("chlorine_gas", kinds)
        self.assertTrue(all(isinstance(v, str) for v in kinds.values()), kinds)
        lab.go("can")
        details = self.details(lab, "can")
        self.assertIn(details["smell"]["garbage"], ("faint", "noticeable", "strong"))
        self.assertNotIn("odor", details)
        self.assertNotIn("odor", self.seen(lab, "can"))

    def test_waste_streams_are_read_off_the_label_and_carts_look_alike(self):
        lab = self.lab(robot={"room": "yard", "pos": [3, 3]})
        carts = [self.seen(lab, e) for e in ("trash_cart", "recycling_cart")]
        self.assertEqual(carts[0]["label"], carts[1]["label"])
        self.assertNotIn("waste_stream", carts[1])
        lab.go("recycling_cart")
        self.assertNotIn("waste_stream", self.details(lab, "recycling_cart"))
        self.assertTrue(lab.do("read", target="recycling_cart")["ok"])
        seen = self.seen(lab, "recycling_cart")
        self.assertEqual(seen["label"], "recycling cart")
        # (the lab's kitchen bin has the id "recycling", so the kernel shows that word as the bin's handle)
        self.assertEqual(lab.env.realism.from_handles(seen["waste_stream"]), "recycling")
        self.assertNotIn("stream_errors", seen)

    def test_vacuum_reports_a_rattle_not_which_small_thing_it_swallowed(self):
        extra = {"earring_7": ("generic_item", "living", {"label": "earring", "pos": [3, 4], "size_m": [0.01, 0.01, 0.01],
                                                          "mass_kg": 0.002, "valuable": True})}
        lab = Lab(perception="realistic", build=with_vacuum(), floor_cells=Vacuuming.CELLS, extra=extra)
        lab.r.skill("cleaning.vacuum_floor", floor="floor_living", vacuum="vac", passes=1)
        self.assertEqual(lab.ent("earring_7")["location"], "vac_bin")
        message = lab.r.last["agent_message"]
        self.assertIn("rattled", message)
        self.assertNotIn(self.h(lab, "earring_7"), message)
        self.assertNotIn("earring_7", message)

    def test_a_spill_is_a_puddle_until_inspected_and_its_volume_is_never_shown(self):
        spill = {"liquid": "bleach", "label": "bleach spill", "pos": [3, 3], "liquid_l": 0.2, "area_m2": 0.13}
        lab = self.lab(extra={"spill_7": ("spill", "kitchen", spill)})
        seen = self.seen(lab, "spill_7")
        self.assertEqual(seen["label"], "puddle of liquid")
        self.assertNotIn("liquid", seen)
        self.assertNotIn("liquid_l", seen)
        lab.go("spill_7")
        details = self.details(lab, "spill_7")
        self.assertEqual(details["label"], "bleach spill")
        self.assertNotIn("liquid_l", details)
        self.assertEqual(self.seen(lab, "spill_7")["label"], "bleach spill")

    def test_observations_and_messages_leak_no_true_ids_or_exact_values(self):
        lab = self.lab(props={"can": {"odor": {"garbage": 0.7}, "fill_level": 0.55}})
        state = lab.env.world.s
        self.assertEqual(_leaked_ids(lab.env.observe(), state), set())
        messages = []
        for step in (("approach", {"target": "counter"}), ("inspect", {"target": "counter"}), ("read", {"target": "apc_7"}),
                     ("inspect", {"target": "apc_7"}), ("inspect", {"target": "floor_kitchen"}), ("approach", {"target": "can"}),
                     ("inspect", {"target": "can"})):
            res = lab.do(step[0], **step[1])
            self.assertTrue(res["ok"], res["message"])
            messages.append(res["agent_message"])
            obs = lab.env.observe()
            self.assertEqual(_leaked_ids(obs, state), set(), step)
        lab.hold("cloth2", "pan")
        lab.go("counter")
        messages.append(lab.do("wipe", target="counter", tool="cloth2", into="pan")["agent_message"])
        self.assertEqual(_leaked_ids(messages, state), set())
        for message in messages:
            self.assertIsNone(_EXACT.search(message), message)
            self.assertNotIn("0.55", message)
            self.assertNotIn("0.7", message)

    # ----- v2.8 hardening: refusals, durations and defaults must not identify a bottle or reveal hidden amounts

    def test_degreasing_after_an_unread_bottle_is_not_refused_in_realistic_mode(self):
        oracle = self.lab(perception="oracle")
        oracle.hold("vinegar_7", "sponge")
        oracle.go("counter")
        oracle.do("apply_cleaner", target="counter", tool="vinegar_7")
        self.assertFalse(oracle.step("degrease", "counter", tool="sponge")["ok"])     # oracle: "apply a degreaser"
        lab = self.lab()
        lab.hold("vinegar_7", "sponge")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="vinegar_7")
        res = lab.do("degrease", target="counter", tool="sponge")
        self.assertTrue(res["ok"], res["message"])
        self.assertNotIn("vinegar", res["agent_message"])

    def test_polishing_with_a_cloth_loaded_from_an_unread_bottle_is_not_refused(self):
        oracle = self.lab(perception="oracle")
        oracle.hold("vinegar_7", "cloth2")
        oracle.go("table")
        oracle.do("apply_cleaner", target="cloth2", tool="vinegar_7")
        self.assertFalse(oracle.step("polish", "table", tool="cloth2")["ok"])
        lab = self.lab()
        lab.hold("vinegar_7", "cloth2")
        lab.go("table")
        lab.do("apply_cleaner", target="cloth2", tool="vinegar_7")
        res = lab.do("polish", target="table", tool="cloth2")
        self.assertTrue(res["ok"], res["message"])
        self.assertFalse(lab.ent("table").get("polished"))
        self.assertEqual(lab.ent("cloth2").get("cl_chem"), {})
        cond = lab.do("condition", target="table", tool="cloth2")
        self.assertTrue(cond["ok"], cond["message"])
        self.assertFalse(lab.ent("table").get("conditioned"))

    def test_rinsing_with_a_cloth_that_still_holds_cleaner_smears_it_instead_of_refusing(self):
        lab = self.lab(props={"cloth2": {"wetness": 0.6, "cl_chem": {"dish_soap": 8.0}}})
        lab.hold("cloth2")
        lab.go("counter")
        res = lab.do("rinse", target="counter", tool="cloth2")
        self.assertTrue(res["ok"], res["message"])
        self.assertTrue(lab.ent("counter").get("_chem_residue"))
        oracle = self.lab(perception="oracle", props={"cloth2": {"wetness": 0.6, "cl_chem": {"dish_soap": 8.0}}})
        oracle.hold("cloth2")
        oracle.go("counter")
        self.assertFalse(oracle.step("rinse", "counter", tool="cloth2")["ok"])

    def test_an_unread_concentrate_is_not_dosed_by_its_label(self):
        extra = {"fc_7": _on_counter("floor_cleaner", 1.95), "bucket_7": ("mop_bucket", "kitchen", {"pos": [2, 3], "liquid_l": 4.0, "liquid": "water"})}
        lab = self.lab(extra=extra)
        lab.hold("fc_7")
        lab.go("bucket_7")
        unread = lab.do("apply_cleaner", target="bucket_7", tool="fc_7")
        self.assertTrue(unread["ok"], unread["message"])
        self.assertNotIn("30 ml", unread["agent_message"])           # 7.5 ml/l x 4 l would name the product
        self.assertNotIn("floor cleaner", unread["agent_message"])
        lab2 = self.lab(extra=extra)
        lab2.hold("fc_7")
        lab2.do("read", target="fc_7")
        lab2.go("bucket_7")
        read = lab2.do("apply_cleaner", target="bucket_7", tool="fc_7")
        self.assertIn("30 ml of floor cleaner", read["agent_message"])

    def test_blotting_time_does_not_depend_on_the_hidden_volume_of_a_puddle(self):
        times = []
        for liters in (0.05, 0.4):
            spill = {"liquid": "water", "label": "water spill", "pos": [3, 3], "liquid_l": liters, "area_m2": 0.13}
            lab = self.lab(extra={"spill_7": ("spill", "kitchen", spill)})
            lab.hold("cloth2")
            lab.go("spill_7")
            before = lab.w.s["time_s"]
            self.assertTrue(lab.do("absorb", target="spill_7", tool="cloth2")["ok"])
            times.append(round(lab.w.s["time_s"] - before, 3))
        self.assertEqual(times[0], times[1])

    def test_inspecting_writes_no_exact_cleaning_bookkeeping_into_later_observations(self):
        props = {"plate": {"oiled_t": 1234.5, "conditioned_t": 55.0, "moisture_since": 77.0, "spinning_until": 99.0, "washed_t": 12.0,
                           "moisture_inner": 0.42, "stain_area_m2": 0.077, "internal_dust": 0.31, "drain_debris_g": 12.3}}
        lab = self.lab(props=props)
        lab.go("plate")
        details = self.details(lab, "plate")
        self.assertEqual(details.get("dust_inside"), "moderate")
        seen = self.seen(lab, "plate")
        for field in ("soil_faces", "oiled_t", "conditioned_t", "moisture_since", "spinning_until", "washed_t", "moisture_inner",
                      "stain_area_m2", "internal_dust", "drain_debris_g"):
            self.assertNotIn(field, seen)
            self.assertNotIn(field, details)
        self.assertTrue(set(seen["soil"].values()) <= SOIL6_NAMES, seen["soil"])

    def test_a_private_basket_inspected_looks_like_any_basket_and_a_closed_can_hides_its_liner(self):
        extra = {"basket_7": ("bathroom_wastebasket", "kitchen", {"pos": [3, 2], "open": True}),
                 "basket_8": ("wastebasket", "kitchen", {"pos": [4, 2], "private_contents": False})}
        lab = self.lab(extra=extra)
        lab.go("basket_7")
        a = self.details(lab, "basket_7")
        lab.go("basket_8")
        b = self.details(lab, "basket_8")
        self.assertEqual(type(a.get("contents")), type(b.get("contents")))
        self.assertNotIn("private", _json.dumps(a))
        self.assertEqual(lab.stat("privacy_violations"), 0)
        lab.go("can")
        self.assertNotIn("liner", self.details(lab, "can"))          # lid closed
        oracle = self.lab(perception="oracle", extra=extra)
        oracle.go("basket_7")
        self.assertIn("private", oracle.do("inspect", target="basket_7")["message"])

    def test_wet_floor_hazards_show_only_where_water_is_visible(self):
        rug = dict(RUG, pos=[2, 5], soil={})
        room = {"kitchen": {"water_film": {"3,3": 1.5, "4,3": 0.01, "2,5": 1.2}, "wet_cells": [[3, 3], [4, 3], [2, 5]]}}
        lab = self.lab(room_props=room, extra={"rug_7": ("generic_item", "kitchen", rug)})
        wet = {tuple(h["cell"]) for h in lab.env.observe()["grid"]["hazards"] if h.get("kind") == "wet"}
        self.assertIn((3, 3), wet)
        self.assertNotIn((4, 3), wet)          # a thin damp film: slippery, but a camera does not see it
        self.assertNotIn((2, 5), wet)          # water under the throw rug
        oracle = self.lab(perception="oracle", room_props=room, extra={"rug_7": ("generic_item", "kitchen", rug)})
        self.assertIn([4, 3], [h["cell"] for h in oracle.env.observe()["grid"]["hazards"] if h.get("kind") == "wet"])

    def test_unresolved_look_alikes_look_and_inspect_the_same(self):
        lab = self.lab()
        lab.go("counter")
        sprays, jugs = ("glass_7", "apc_7", "refill_7", "vinegar_7"), ("bleach_jug_7", "vinegar_jug_7")
        seen = {e: self.seen(lab, e) for e in sprays + jugs}
        for group in (sprays, jugs):
            self.assertEqual(len({tuple(sorted(seen[e])) for e in group}), 1, group)     # same fields at a glance
        looks = {e: self.details(lab, e) for e in sprays + jugs}
        for group in (sprays, jugs):
            self.assertEqual(len({tuple(sorted(looks[e])) for e in group}), 1, group)    # same fields when inspected
            self.assertEqual(len({tuple(looks[e]["candidates"]) for e in group}), 1, group)
        self.assertNotIn("liquid_l", looks["refill_7"])
        self.assertEqual(looks["bleach_jug_7"]["label"], looks["vinegar_jug_7"]["label"])
        self.assertEqual(lab.ent("bleach_jug_7")["size_m"], lab.ent("vinegar_jug_7")["size_m"])

    def test_items_tagged_with_a_waste_stream_are_not_bins(self):
        extra = {"cell_7": ("generic_item", "kitchen", {"label": "AA battery", "pos": [3, 2], "size_m": [0.05, 0.015, 0.015],
                                                        "waste_stream": "hazardous", "mass_kg": 0.02})}
        for mode in ("oracle", "realistic"):
            lab = self.lab(perception=mode, extra=extra)
            lab.go("cell_7")
            details = _json.loads(lab.do("inspect", target="cell_7")["message"])
            for key in ("fill_level", "lid_blocked", "stream_label", "waste_stream", "liner"):
                self.assertNotIn(key, details, mode)


# ----------------------------------------------------------------------------------------------- tier-1 adoption

BOARD = ("generic_item", "counter", {"relation": "on", "spos": [0.0, 0.0], "label": "bread board", "size_m": [1.0, 0.5, 0.03],
                                     "mass_kg": 2.0, "material": "wood"})
CANDLESTICK = ("generic_item", "counter", {"relation": "on", "spos": [1.3, 0.2], "label": "glass candlestick",
                                           "size_m": [0.03, 0.03, 0.3], "mass_kg": 0.2, "material": "glass"})


class Tier1Adoption(unittest.TestCase):
    """TIER1_INTEGRATION 6 (cleaning): physics, camera, body and sweep integration."""

    def test_kernel_floor_spills_become_cleaning_spills_that_grow_per_cell_and_dry(self):
        from house_sim.kernel.phys import liquids
        lab = Lab()
        sid = liquids.spill(lab.w, 0.3, "juice", at=("kitchen", [3, 3]), cause="spill")
        rec = lab.ent(sid)
        self.assertEqual((rec["type"], rec["liquid"], rec["pos"]), ("spill", "juice", [3, 3]))
        self.assertEqual(liquids.spill(lab.w, 0.2, "juice", at=("kitchen", [3, 3]), cause="spill"), sid)   # grows there
        self.assertAlmostEqual(lab.ent(sid)["liquid_l"], 0.5)
        other = liquids.spill(lab.w, 0.2, "milk", at=("kitchen", [3, 3]), cause="spill")
        self.assertNotEqual(other, sid)
        lab.wait(3600)
        self.assertLess(lab.ent(sid)["liquid_l"], 0.5)          # cleaning's spill system dries it like any other

    def test_a_cloth_swept_across_a_crowded_counter_knocks_over_what_stands_slender_unless_it_works_around(self):
        from house_sim.systems.cleaning import engine as E
        for around in (False, True):
            lab = Lab(extra={"board": BOARD, "candle": CANDLESTICK}, props={"counter": {"soil": {"dust": 0.4}}})
            self.assertTrue(E.crowded(lab.w, "counter"))
            lab.hold("cloth")
            lab.go("counter")
            res = lab.step("wipe", "counter", tool="cloth", around=around)
            self.assertTrue(res["ok"], res["message"])
            if around:
                self.assertIsNone(lab.ent("candle").get("orientation"))
                self.assertEqual(lab.stat("collisions"), 0)
            else:
                self.assertEqual(lab.ent("candle").get("orientation"), "on_side")      # the kernel's knock tipped it
                self.assertIn("candle tipped over", res["message"])
                self.assertEqual(lab.stat("collisions"), 1)
            self.assertEqual(lab.ent("board").get("location"), "counter")              # a heavy flat board does not move

    def test_canonical_questions_reach_the_cleaning_answerer(self):
        from house_sim.kernel import dialogue
        peel = ("generic_item", "counter", {"relation": "on", "spos": [0.5, 0.1], "label": "banana peel", "waste_kind": "food",
                                            "size_m": [0.15, 0.05, 0.03], "mass_kg": 0.1})
        lab = Lab(people={"adult_1": ADULT}, household={"waste_rules": {"glass": False, "caps": "on"}},
                  extra={"bleach": ("bleach", "kitchen", {"pos": [1, 4]}), "peel": peel})
        S, st = dialogue.Subject, lab.w.s
        cases = {(S("word", None, ("recycling",)), "how"): ("cleaning.how", "cleaning.rules"),
                 (S("word", None, ("vacuum",)), "how"): ("cleaning.how", "cleaning.quiet_hours"),
                 (S("word", None, ("cloth",)), "how"): ("cleaning.how", "cleaning.cloth_colors"),
                 (S("entity", "bleach"), "which"): ("cleaning.product", "cleaning.product:bleach"),
                 (S("entity", "counter"), "how"): ("cleaning.how", "cleaning.finish:counter"),
                 (S("entity", "can"), "how"): ("cleaning.how", "cleaning.rules"),
                 (S("entity", "peel"), "keep"): ("cleaning.keep", "cleaning.keep:peel")}
        for (subject, aspect), expected in cases.items():
            self.assertEqual(dialogue.find_route(st, "adult_1", subject, aspect, None, None), expected, subject)
        res = lab.step("ask", "adult_1", aspect="how", about="recycling")
        self.assertTrue(res["ok"], res["message"])
        self.assertFalse(lab.w.s["facts"]["cleaning.rules"]["glass"])
        self.assertEqual(lab.stat("necessary_questions"), 1)

    def test_the_robots_grippers_are_wiped_washed_and_dried_through_the_body(self):
        from house_sim.kernel import body
        lab = Lab()
        body.soil_hand(lab.w, "left", "grease", 0.5)
        lab.hold("cloth")
        hand = next(h for h, item in lab.w.s["robot"]["hands"].items() if item == "cloth")
        other = "left" if hand == "right" else "right"
        body.soil_hand(lab.w, other, "grease", 0.5)
        res = lab.step("wipe", "robot_hand_" + other, tool="cloth")
        self.assertTrue(res["ok"], res["message"])
        self.assertNotIn(other, lab.w.s["robot"].get("hand_soil") or {})
        lab.hold("cloth", "plate")
        res = lab.step("wipe", "robot_hand", tool="cloth")          # both grippers: one holds a plate
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], "Put down what that hand holds first")
        lab.go("counter")
        lab.do("place", target="plate", destination="counter")
        lab.do("place", target="cloth", destination="counter")
        lab.go("sink")
        self.assertTrue(lab.step("wash", "robot_hand", destination="sink")["ok"])
        self.assertEqual(lab.w.s["robot"]["hand_soil"], {"left": {"water": 0.6}, "right": {"water": 0.6}})
        lab.hold("cloth2")
        self.assertTrue(lab.step("dry", "robot_hand", tool="cloth2")["ok"])
        self.assertEqual(lab.w.s["robot"].get("hand_soil") or {}, {})

    def test_idle_probes_are_exact(self):
        from house_sim.generator import generate_house
        from house_sim.kernel import plugins
        from house_sim.kernel.predicates import digest
        from house_sim.systems.cleaning import make_spill
        from house_sim.systems.cleaning import systems as Y
        probes = (Y.chemistry_idle, Y.soak_idle, Y.drying_idle, Y.spills_idle, Y.waste_idle, Y.air_idle)

        def busy():
            lab = Lab(people={"adult_1": PERSON}, props={"cloth": {"wetness": 0.8}},
                      extra={"scrap": ("generic_item", "can", {"relation": "in", "label": "chicken bones", "waste_kind": "food",
                                                              "food": True, "size_m": [0.1, 0.1, 0.05], "mass_kg": 0.2})})
            make_spill(lab.w, "kitchen", "milk", 0.2, cell=[4, 4])
            return lab.env

        def run(on, make):
            saved = plugins.IDLE_PROBES[0]
            plugins.IDLE_PROBES[0] = on
            try:
                env = make()
                skipped = 0
                for seconds in (600, 2 * 3600, 30, 9 * 3600, 3 * 3600):
                    skipped += sum(int(p(env.world)) for p in probes)
                    self.assertTrue(env.step("wait", seconds=seconds)["ok"])
                return digest(env.world.s), skipped
            finally:
                plugins.IDLE_PROBES[0] = saved

        cases = [busy, lambda: Lab().env] + [lambda s=s: HouseEnv(generate_house(s), seed=s, trace=False) for s in (0, 2)]
        skipped_total = 0
        for make in cases:
            on, skipped = run(True, make)
            off, _ = run(False, make)
            self.assertEqual(on, off)
            skipped_total += skipped
        self.assertGreater(skipped_total, 0)

    def test_dust_settles_on_things_left_untouched_faster_in_the_open_than_shut_away(self):
        from house_sim.systems.cleaning import history
        self.assertAlmostEqual(history.dust_after(60), 0.045)
        self.assertLess(history.dust_after(60, enclosed=True), history.dust_after(60) / 5.0)
        vase = ("generic_item", "counter", {"relation": "on", "spos": [0.5, 0.1], "label": "vase", "size_m": [0.1, 0.1, 0.2],
                                            "mass_kg": 0.4, "material": "ceramic"})
        lab = Lab(extra={"vase": vase})
        self.assertAlmostEqual(history.settle_dust(lab.w, "vase", 90), 0.0675)
        self.assertAlmostEqual(lab.soil("vase", "dust"), 0.0675)
        config = {"entities": {"a": {"soil": {}}, "box": {"openable": True, "open": False}, "b": {"location": "box"}}}
        self.assertGreater(history.settle_dust(config, "a", 30), 5 * history.settle_dust(config, "b", 30))

    def test_what_the_robot_can_shake_out_follows_its_body(self):
        from house_sim.kernel import body
        from house_sim.systems.cleaning import floors
        lab = Lab()
        kg, length = floors.shake_limits(lab.w.s)
        self.assertAlmostEqual(kg, body.capacity_kg(lab.w.s, "both") * 0.3)
        self.assertAlmostEqual(length, float(body.get(lab.w.s, "height_m")))
        heavy = dict(RUG, mass_kg=round(kg + 1.0, 2))
        lab = Lab(extra={"rug": ("generic_item", "living", heavy)}, robot={"room": "living", "pos": [1, 1]})
        lab.hold("rug")
        res = lab.step("shake_out", "rug")
        self.assertFalse(res["ok"])
        self.assertEqual(res["category"], "capability")

    def test_a_resident_slipping_is_heard_as_a_person_down_notice(self):
        lab = Lab(room_props=WetFloorSafety.FILM, people={"adult_1": PERSON}, notices=True)
        lab.wait(120)
        self.assertGreater(lab.sec()["slips"], 0)
        self.assertIn(("person_down", "kitchen"), [(n["kind"], n.get("room")) for n in lab.w.s["notices"]])
        quiet = Lab(room_props=WetFloorSafety.FILM, people={"adult_1": PERSON})          # no notice bus: nothing stored
        quiet.wait(120)
        self.assertIsNone(quiet.w.s.get("notices"))

    def test_a_microwave_fire_is_noticed_as_smoke_and_scattered_trash_as_a_clatter(self):
        from house_sim.systems.cleaning import waste
        lab = Lab(extra={"mw": ("generic_item", "counter", MICROWAVE), "sp": ("sponge", "mw", {"relation": "in", "wetness": 0.0}),
                         "tin": ("generic_item", "can", {"relation": "in", "label": "tin can", "size_m": [0.08, 0.08, 0.1],
                                                         "mass_kg": 0.05, "material": "steel"})}, notices=True)
        lab.wait(60)
        self.assertGreater(lab.sec()["fires"], 0)        # the scene's fire (not the robot's harm) is still noticed
        kinds = [n["kind"] for n in lab.w.s["notices"]]
        self.assertIn("smoke", kinds)
        waste.scatter(lab.w, "can", "dog")
        self.assertIn("clatter", [n["kind"] for n in lab.w.s["notices"]])
        self.assertEqual(lab.ent("tin")["location"], "kitchen")

    def test_materials_without_a_cleaning_row_use_the_kernels_material_class(self):
        from house_sim.systems.cleaning import model as M
        self.assertEqual(M.material_key({"material": "bamboo"}), "wood")
        self.assertEqual(M.material_key({"material": "pyrex"}), "glass")
        self.assertEqual(M.material_key({"material": "tin"}), "metal")
        self.assertEqual(M.material_key({"material": "marble"}), "marble")       # an own row wins

    def test_geometric_camera_a_label_turned_against_its_neighbour_reads_only_once_lifted(self):
        """Cleaning products print product and hazards on the label face (perception "faces"): with the label pressed
        against the next bottle neither the head nor the wrist reads it until the bottle is held; in room mode reading
        ignores faces."""
        extra = {"jug_a": _on_counter("bleach", 0.3), "jug_b": _on_counter("ammonia_cleaner", 0.44, label_facing="left"),
                 "jug_c": _on_counter("cleaning_vinegar", 0.58)}
        lab = Lab(extra=extra, camera="geometric")
        lab.go("counter")
        res = lab.step("read", "jug_a")
        self.assertTrue(res["ok"], res["message"])
        self.assertIn("chem_hazards", _json.loads(res["message"]))         # an ordinary bottle: product and hazards read
        res = lab.step("read", "jug_b")
        self.assertFalse(res["ok"])
        self.assertNotIn("jug_b", lab.w.s.get("facts", {}).get("read", {}))
        lab.hold("jug_b")
        res = lab.step("read", "jug_b")
        self.assertTrue(res["ok"], res["message"])
        self.assertIn("120 ml per 4 litres", _json.loads(res["message"])["label_text"])
        room = Lab(extra=extra)
        room.go("counter")
        self.assertTrue(room.step("read", "jug_b")["ok"])

    def test_unread_look_alikes_disinfect_with_the_same_words_and_time(self):
        """Round-5 red team: the disinfect branch must not tell an unread bottle by its true product."""
        import re
        rp = RealisticPerception()
        seen = set()
        for bottle in ("glass_7", "apc_7", "refill_7", "vinegar_7"):
            lab = rp.lab()
            lab.go("counter")
            lab.hold(bottle)
            start = lab.w.s["time_s"]
            res = lab.env.step("disinfect", rp.h(lab, "counter"), tool=rp.h(lab, bottle))
            self.assertTrue(res["ok"], res["message"])
            seen.add((re.sub(r"obj_[0-9a-f]+", "H", res.get("agent_message") or res["message"]),
                      round(lab.w.s["time_s"] - start, 3)))
        self.assertEqual(len(seen), 1, seen)

    def test_other_domains_products_show_how_full_they_are_from_their_own_amounts(self):
        """Round-4: a closed product without amount_ml or litres used to inspect as "empty" whatever it held."""
        extra = {"paint_7": ("generic_item", "kitchen", {"label": "paint can", "product": "paint", "amount_l": 0.5,
                                                          "pos": [1, 2], "size_m": [0.17, 0.17, 0.19], "mass_kg": 1.3}),
                 "lube_7": ("generic_item", "kitchen", {"label": "lubricant can", "product": "lubricant", "amount": 0.3,
                                                         "pos": [1, 2], "size_m": [0.06, 0.06, 0.2], "mass_kg": 0.3}),
                 "seed_7": ("generic_item", "kitchen", {"label": "grass seed bag", "product": "grass_seed",
                                                         "pos": [1, 2], "size_m": [0.3, 0.1, 0.4], "mass_kg": 2.0})}
        rp = RealisticPerception()
        lab = rp.lab(extra=extra)
        lab.go("paint_7")
        words = {e: rp.details(lab, e).get("fill_estimate") for e in extra}
        self.assertEqual(words, {"paint_7": "full", "lube_7": "about a third left", "seed_7": None})

    def test_smells_inside_closed_or_sealed_containers_are_weaker_and_textiles_keep_their_own(self):
        """Round-4: a musty thing shut in a drawer smells half as strong, sealed away not at all, and a soured towel is
        textiles_laundry's musty_laundry smell, not a second mildew smell from this observer."""
        def smells(**where):
            drawer = {"label": "drawer", "container": True, "accepts_items": True, "openable": True, "open": False,
                      "portable": False, "pos": [6, 1], "size_m": [0.5, 0.5, 0.2], "inner_size_m": [0.45, 0.45, 0.15]}
            drawer.update(where.pop("drawer", {}))
            rag = {"label": "musty rag", "relation": "in", "odor": {"mildew": 0.8}, "size_m": [0.3, 0.3, 0.01], "mass_kg": 0.05}
            rag.update(where)
            lab = Lab(extra={"drawer_7": ("generic_item", "kitchen", drawer), "rag_7": ("generic_item", "drawer_7", rag)})
            return {s["kind"]: s["intensity"] for s in lab.env.observe().get("smells", [])}
        self.assertEqual(smells(drawer={"open": True}).get("mildew"), 0.8)
        self.assertEqual(smells().get("mildew"), 0.4)
        self.assertNotIn("mildew", smells(drawer={"sealed": True}))
        self.assertNotIn("mildew", smells(drawer={"open": True}, textile=True))

    def test_geometric_camera_shows_a_wet_floor_hazard_only_where_a_frame_looked(self):
        film = {"kitchen": {"water_film": {"5,5": 1.5}, "wet_cells": [[5, 5]]}}
        seen = {}
        for facing in (180.0, 45.0):
            lab = Lab(room_props=film, perception="realistic", camera="geometric",
                      robot={"room": "kitchen", "pos": [1, 1], "facing_deg": facing})
            lab.do("look", target="forward")
            seen[facing] = [h["cell"] for h in lab.env.observe()["grid"]["hazards"] if h.get("kind") == "wet"]
        self.assertEqual(seen, {180.0: [], 45.0: [[5, 5]]})
        room = Lab(room_props=film, perception="realistic", robot={"room": "kitchen", "pos": [1, 1], "facing_deg": 180.0})
        self.assertIn([5, 5], [h["cell"] for h in room.env.observe()["grid"]["hazards"] if h.get("kind") == "wet"])


# ----------------------------------------------------------------------------------------------- phase 1 (stabilise)

SCENE_FUMES = {"kitchen": {"cl_air": {"fumes": 2.0}}}


class Phase1Review(unittest.TestCase):
    """Independent review 2026-10-22 and the adoption round's open items (docs/domains/cleaning.md section 16)."""

    def _hold_and(self, lab, item, action, **args):
        lab.hold(item)
        return lab.do(action, target=item, **args)

    # --- the spray_bottle look-alike group (adopt_status breakage: shake and heft told the members apart) ----------
    def _spray_twins(self):
        twins = {"glass": ("glass_cleaner", {}),
                 "refill": ("spray_bottle", {"liquid": "cleaning_solution", "liquid_l": 0.75, "cl_chem": {"all_purpose": 30.0}})}
        if default_registry().known("trigger_bottle"):
            twins["trigger"] = ("trigger_bottle", {"liquid_l": 0.75})       # mechanisms' member of the group
        return twins

    def test_every_unread_spray_bottle_shakes_hefts_and_inspects_alike(self):
        from house_sim.kernel.actions import ACTIONS
        from house_sim.kernel.phys import liquids
        hands = "shake" in ACTIONS and "support" in ACTIONS          # manipulation's verbs (not loaded in isolation)
        seen = {}
        for name, (type_name, props) in self._spray_twins().items():
            lab = Lab(perception="realistic", extra={"probe": _on_counter(type_name, 0.3, color="blue", **props)})
            lab.ent("probe").pop("pos", None)
            self.assertTrue(liquids.container_closed(lab.ent("probe")), name)   # a trigger head: closed unless squeezed
            lab.hold("probe")
            felt = ()
            if hands:
                shake = lab.do("shake", target="probe")["agent_message"]
                heft = lab.do("support", target="probe", mode="heft")["agent_message"]
                felt = (shake.replace(lab.env.realism.handles.handle("probe"), "it"), heft)
            details = _json.loads(lab.do("inspect", target="probe")["agent_message"])
            seen[name] = (felt, sorted(details), details.get("fill_estimate"), lab.ent("probe").get("liquid_l"))
        first = seen.pop("glass")
        if hands:
            self.assertIn("liquid sloshes inside", first[0][0])
        for name, values in seen.items():
            self.assertEqual(values[:3], first[:3], name)
            self.assertEqual(values[3], 0.75, name)        # nothing sprayed out of a closed refill

    def test_a_product_bottle_gets_lighter_and_emptier_as_it_is_used_and_a_scene_amount_shows(self):
        lab = Lab()
        full = dict(lab.ent("apc"))
        lab.hold("apc")
        lab.go("counter")
        lab.do("apply_cleaner", target="counter", tool="apc", amount_ml=300)
        rec = lab.ent("apc")
        self.assertAlmostEqual(rec["amount_ml"], full["amount_ml"] - 300.0)
        self.assertAlmostEqual(rec["mass_kg"], full["mass_kg"] - 0.3, places=3)
        self.assertAlmostEqual(rec["fill_level"], (full["amount_ml"] - 300.0) / full["amount_ml"], places=3)
        empty = Lab(props={"apc": {"amount_ml": 0.0}})
        self.assertEqual(empty.ent("apc")["fill_level"], 0.0)                       # synced at episode start
        self.assertAlmostEqual(empty.ent("apc")["mass_kg"], full["mass_kg"] - full["amount_ml"] / 1000.0, places=3)

    def test_unread_bottles_answer_alike_when_blotting_applying_and_emptying(self):
        """Found by a verb sweep over each group's members as the same unread entity: a trigger bottle (no product
        field) 'blotted' a counter where a product was refused, an unread granular bottle took a paste's 20 s, and a
        closed refill poured its liquid into the sink when emptied."""
        members = [("all_purpose_cleaner", {}), ("spray_bottle", {"liquid": "cleaning_solution", "liquid_l": 0.75,
                                                                  "cl_chem": {"all_purpose": 30.0}})]
        if default_registry().known("trigger_bottle"):
            members.append(("trigger_bottle", {"liquid_l": 0.75}))
        rows = []
        for type_name, props in members:
            lab = Lab(perception="realistic", extra={"probe": _on_counter(type_name, 0.3, **props)})
            lab.ent("probe").pop("pos", None)
            lab.r.strict = False
            lab.hold("probe")
            lab.go("counter")
            blot = lab.do("absorb", target="counter", tool="probe")
            empty = lab.do("empty_into", target="probe", destination="sink")
            rows.append((blot["ok"], blot["category"], empty["ok"], lab.ent("probe").get("liquid_l") in (None, 0.75)))
        self.assertTrue(all(row == rows[0] for row in rows), rows)
        self.assertFalse(rows[0][0])
        self.assertTrue(rows[0][3])                     # a closed bottle keeps its liquid when tipped out
        named = Lab()
        named.r.strict = False
        named.hold("apc")
        named.go("counter")
        self.assertEqual(named.do("absorb", target="counter", tool="apc")["category"], "capability")
        seconds = {}
        for type_name in ("oil_absorbent", "cream_cleanser"):
            lab = Lab(perception="realistic", extra={"probe": _on_counter(type_name, 0.3)})
            lab.ent("probe").pop("pos", None)
            lab.hold("probe")
            lab.go("counter")
            before = lab.w.s["time_s"]
            lab.do("apply_cleaner", target="counter", tool="probe")
            seconds[type_name] = lab.w.s["time_s"] - before
        self.assertEqual(seconds["oil_absorbent"], seconds["cream_cleanser"])

    def test_look_alike_groups_share_size_heft_and_full_amount(self):
        reg = default_registry()
        self.assertEqual(reg.lookalike_warnings, [])
        with open("house_sim/data/types/cleaning.json", encoding="utf-8") as f:
            cleaning = set(_json.load(f)["types"])
        groups = reg.lookalike_groups()
        for name, types in sorted(reg.lookalike_types().items()):
            own = [t for t in types if t in cleaning]
            if len(types) < 2 or not own:
                continue
            props = []
            for t in own:
                p = dict(reg.resolve(t))
                if p.get("liquid_container") and not p.get("amount_ml"):
                    # a refill bottle (litres in liquid_l): compared full, as a product bottle comes
                    p["mass_kg"] = round(p["mass_kg"] + p["capacity_l"], 3)
                    p["amount_ml"], p["fill_level"] = p["capacity_l"] * 1000.0, 1.0
                props.append(p)
            # what a look, a lift or a shake can tell before the resolving look; the material is what an inspection
            # tells for inspect-resolved groups, so it must match only where a label decides
            keys = ["size_m", "mass_kg", "amount_ml", "fill_level", "openable", "trigger"]
            if groups[name].get("resolve") == "read":
                keys.append("material")
            for key in keys:
                self.assertEqual(len({_json.dumps(p.get(key)) for p in props}), 1, (name, key, own))
            labels = {str(p.get("label")).lower() for p in props} | set(own)
            for candidate in groups[name].get("candidates") or ():
                self.assertNotIn(candidate.lower(), labels, name)
        self.assertIn("deck_cleaner", reg.lookalike_types()["plastic_jug"])
        self.assertEqual(reg.lookalike_types()["squeeze_bottle"], ["dish_soap"])

    # --- harms are the robot's only when it caused them (review P1 C8: exogenous harms charged to the robot) -------
    def test_vinegar_the_scene_left_on_marble_etches_it_but_is_not_the_robots_damage(self):
        applied = [{"p": "vinegar", "face": "main", "t": 0.0, "wet_until": 600.0, "s": 1.0}]
        lab = Lab(counter="marble", props={"counter": {"cl_applied": applied}})
        lab.wait(300)
        self.assertIn("etched", lab.damage("counter"))
        self.assertEqual(lab.stat("damage"), 0)
        self.assertEqual(lab.sec()["exogenous"]["damage"], 1)
        mine = Lab(counter="marble", extra={"vin": ("cleaning_vinegar", "kitchen", {"pos": [1, 4]})})
        mine.hold("vin")
        mine.go("counter")
        mine.do("apply_cleaner", target="counter", tool="vin", method="spray")
        mine.wait(300)
        self.assertIn("etched", mine.damage("counter"))
        self.assertEqual(mine.stat("damage"), 1)

    def test_a_pet_licking_the_scenes_toxic_spill_is_logged_but_one_the_robot_caused_is_its_injury(self):
        from house_sim.systems.cleaning import floors
        pets = {"rex": {"species": "dog", "label": "Rex", "location": "kitchen", "pos": [5, 5], "posture": "standing"}}
        for robot in (False, True):
            lab = Lab()
            lab.w.s["pets"] = copy.deepcopy(pets)
            floors.make_spill(lab.w, "kitchen", "antifreeze", 0.3, cell=[5, 5], robot=robot)
            lab.wait(300)
            entry = lab.log("pet_licked_toxic_spill")[-1]
            self.assertEqual(entry["by"], "robot" if robot else "exogenous")
            self.assertEqual(lab.stat("injury"), 1 if robot else 0)

    def test_fumes_the_robot_did_not_make_are_an_exogenous_exposure(self):
        scene = Lab(room_props=SCENE_FUMES, people={"adult_1": PERSON})
        scene.wait(60)
        self.assertEqual(scene.log("fume_exposure")[-1]["by"], "exogenous")
        self.assertEqual(scene.stat("gas_exposure"), 0)
        mine = Lab(people={"adult_1": PERSON}, extra={"bl": ("bleach", "kitchen", {"pos": [1, 4]}),
                                                      "am": ("ammonia_cleaner", "kitchen", {"pos": [1, 4]})})
        mine.hold("bl")
        mine.go("counter")
        mine.do("apply_cleaner", target="counter", tool="bl", method="spray")
        mine.hold("am")
        mine.go("counter")
        mine.do("apply_cleaner", target="counter", tool="am", method="spray")
        self.assertGreater(mine.stat("gas_exposure"), 0)
        self.assertIn("cleaning.toxic_gas_released", mine.env.evaluate()["failures"])

    def test_a_reaction_outside_any_robot_action_is_not_the_robots_toxic_gas(self):
        from house_sim.systems.cleaning import state as S
        lab = Lab(people={"adult_1": PERSON})
        S.gas_event(lab.w, "kitchen", "chloramine", "toilet")
        self.assertEqual(lab.stat("gas_exposure"), 0)
        self.assertEqual(lab.sec()["exogenous"]["gas_events"], 1)
        self.assertNotIn("cleaning.toxic_gas_released", lab.env.evaluate()["failures"])

    def test_a_knife_a_resident_drops_into_their_suds_later_is_not_the_robots_hidden_sharp(self):
        lab = Lab(props=SINK_SOAPY, extra={"knife": ("generic_item", "kitchen", dict(KNIFE, pos=[1, 2]))})
        knife = lab.w.s["entities"]["knife"]
        knife.update(location="sink", relation="in")
        knife.pop("pos", None)
        lab.wait(120)
        self.assertTrue(lab.ent("knife").get("hidden_in_water"))
        self.assertNotIn("cleaning.hidden_sharp_in_water", lab.env.evaluate()["failures"])

    def test_standing_water_the_scene_left_on_hardwood_damages_it_but_the_robots_mop_water_is_its_damage(self):
        film = {"hall": {"water_film": {"{},{}".format(c, r): 3.0 for c in range(2) for r in range(3)}}}
        scene = Lab(room_props=film, robot={"room": "kitchen", "pos": [1, 1]})
        scene.wait(1800)
        self.assertIn("water_damage", scene.damage("floor_hall"))
        self.assertEqual(scene.stat("damage"), 0)
        extra = {"bucket": ("mop_bucket", "hall", {"pos": [2, 2], "liquid": "water", "liquid_l": 6.0, "cl_chem": {"floor_cleaner": 30.0}}),
                 "mop": ("string_mop", "hall", {"pos": [2, 2]})}
        mine = Lab(extra=extra, robot={"room": "hall", "pos": [1, 1]})
        mine.r.skill("cleaning.mop_floor", floor="floor_hall", mop="mop", bucket="bucket", wetness="wet", exit_room="kitchen")
        mine.wait(1800)
        self.assertIn("water_damage", mine.damage("floor_hall"))
        self.assertEqual(mine.stat("damage"), 1)

    def test_a_pot_boiling_over_is_the_robots_spill_only_when_it_set_it_up(self):
        from house_sim.systems.cleaning import systems
        pot = {"label": "pot", "liquid_container": True, "container": True, "accepts_items": True, "liquid": "water",
               "liquid_l": 2.2, "capacity_l": 3.0, "max_fill_l": 2.0, "temperature_c": 99.0, "relation": "on",
               "spos": [1.0, 0.1], "size_m": [0.25, 0.25, 0.15], "mass_kg": 1.0}
        for robot in (False, True):
            lab = Lab(extra={"pot": ("generic_item", "counter", dict(pot))})
            if robot:
                lab.w.s["entities"]["pot"]["_robot_moved"] = True        # the robot filled it and set it on the heat
            systems.boil_over(lab.w, "pot")
            self.assertEqual(lab.stat("spills"), 1 if robot else 0)
            self.assertEqual(lab.log("boil_over")[-1]["by"], "robot" if robot else "exogenous")

    def test_superheated_water_that_erupts_lands_on_the_floor_instead_of_vanishing(self):
        mug = {"label": "mug", "liquid_container": True, "container": True, "liquid": "water", "liquid_l": 0.3,
               "capacity_l": 0.35, "superheated": True, "temperature_c": 45.0, "relation": "on", "spos": [1.0, 0.1],
               "size_m": [0.09, 0.09, 0.1], "mass_kg": 0.3}
        lab = Lab(extra={"mug": ("generic_item", "counter", mug)})
        lab.hold("mug")
        self.assertEqual(lab.stat("burns"), 1)
        spilled = sum(float(r.get("liquid_l", 0.0)) for r in lab.w.s["entities"].values() if r.get("category") == "spill")
        self.assertAlmostEqual(lab.ent("mug")["liquid_l"] + spilled, 0.3, places=3)
        self.assertGreater(spilled, 0.1)

    # --- stream verdicts hold only while the item is in that stream (review H2: do, then undo) ------------------------
    def test_a_sorted_item_tipped_back_out_of_its_bin_is_no_longer_sorted(self):
        lab = Lab(extra={"soda": ("soda_can", "kitchen", {"pos": [5, 6]})}, household={"waste_rules": {"cans": True}})
        lab.hold("soda")
        lab.go("recycling")
        lab.do("place", target="soda", destination="recycling")
        self.assertEqual((lab.ent("soda")["in_stream"], lab.ent("soda")["stream_ok"]), ("recycling", True))
        filled = lab.ent("recycling")["fill_level"]
        from house_sim.kernel.phys import impact
        impact.tip_over(lab.w, "recycling", "knocked over", by="exogenous")     # a dog, a bump: any route out counts
        lab.wait(30)
        self.assertEqual(lab.ent("soda")["location"], "kitchen")
        self.assertNotIn("in_stream", lab.ent("soda"))
        self.assertNotIn("stream_ok", lab.ent("soda"))
        self.assertLess(lab.ent("recycling")["fill_level"], filled)
        self.assertEqual(lab.w.s["_cleaning_watch"].get("stream"), [])

    # --- kernel request cleaning-3 adopted: a portion that grows a spill is not a new spill ------------------------------
    def test_a_portion_that_grows_an_existing_spill_is_not_counted_again(self):
        from house_sim.kernel.phys import liquids
        lab = Lab()
        sid = liquids.spill(lab.w, 0.3, "juice", at=("kitchen", [3, 3]), cause="spill")
        self.assertEqual(lab.stat("phys_spills"), 1)
        self.assertEqual(liquids.spill(lab.w, 0.2, "juice", at=("kitchen", [3, 3]), cause="spill"), sid)
        self.assertEqual(lab.stat("phys_spills"), 1)
        liquids.spill(lab.w, 0.2, "milk", at=("kitchen", [3, 3]), cause="spill")
        self.assertEqual(lab.stat("phys_spills"), 2)


UNFAMILIAR = {"novel": True, "size_m": [0.2, 0.15, 0.25], "color": "gray", "material": "plastic"}
# things that are told apart by what they are (generic items, so the scenes build with only core and cleaning loaded)
UF_LAMP = {"label": "table lamp", "category": "light", "bulb_type": "incandescent", "on": True, "mass_kg": 2.0}
UF_KETTLE = {"label": "kettle", "liquid_container": True, "container": True, "openable": True, "open": False, "liquid": "water",
          "liquid_l": 1.0, "capacity_l": 1.7, "mass_kg": 1.1}
UF_JUG = {"label": "bleach jug", "liquid_container": True, "liquid": "chlorine_bleach", "liquid_l": 1.0, "capacity_l": 1.9,
       "mass_kg": 0.2}
UF_REMOTE = {"label": "remote control", "category": "remote", "mass_kg": 0.12}


class Phase1Finish(unittest.TestCase):
    """Phase 1 finish (catalog/analysis/phase1/breakages.json cleaning rows, red_leaks findings 5 and 8, the phase-1
    twin sweeps; docs/domains/cleaning.md section 17): unfamiliar things, the refill spray bottle and unidentified
    puddles tell nothing about what they are, and the time systems' fast paths change no result."""

    def _inspector_details(self, lab, eid, sensors=()):
        """cleaning.details' own output (before the kernel's NOVEL_FIELDS filter, which would hide a leak here)."""
        import random
        from house_sim.kernel.actions import ACTIONS, Ctx
        from house_sim.systems.cleaning import perception
        lab.env.sensors = list(sensors)
        ctx = Ctx(lab.env, ACTIONS["inspect"], {"target": eid}, random.Random(0))
        details = {}
        perception.inspect_details(lab.w, eid, lab.ent(eid), details, ctx)
        return details

    def test_an_unfamiliar_lamp_gets_no_bulb_heat_from_inspection(self):
        """Review I4 (electrical's request): the inspector added bulb_hot (and bulb_temp_c with the thermal sensor) to
        any light by its category, so an unidentified unfamiliar lamp inspected unlike every other unfamiliar thing."""
        for sensors in ((), ("thermal",)):
            lab = Lab(perception="realistic", extra={"lamp": _on_counter("generic_item", 0.3, **dict(UF_LAMP, **UNFAMILIAR)),
                                                     "kettle": _on_counter("generic_item", 0.6, **dict(UF_KETTLE, **UNFAMILIAR)),
                                                     "known": _on_counter("generic_item", 0.9, **UF_LAMP)})
            for eid in ("lamp", "kettle", "known"):
                lab.ent(eid).pop("pos", None)
            lamp = self._inspector_details(lab, "lamp", sensors)
            self.assertFalse({"bulb_hot", "bulb_temp_c", "bulb_temperature"} & set(lamp), (sensors, lamp))
            self.assertEqual(sorted(lamp), sorted(self._inspector_details(lab, "kettle", sensors)), sensors)
            self.assertIn("bulb_hot", self._inspector_details(lab, "known", sensors))     # a lamp it knows: as before
            oracle = Lab(extra={"lamp": _on_counter("generic_item", 0.3, **dict(UF_LAMP, **UNFAMILIAR))})
            oracle.ent("lamp").pop("pos", None)
            self.assertIn("bulb_hot", self._inspector_details(oracle, "lamp", sensors))  # oracle mode unchanged

    def test_an_unfamiliar_thing_is_neither_emptied_nor_emptied_into(self):
        """Phase-1 red team novel sweep: empty_into judged the true record of an unidentified unfamiliar target or
        destination ('Emptied ...; poured a lot' for a bleach bottle, 'Open the lid of <thing> first' for a kettle)."""
        from house_sim.kernel.actions import NOVEL_OPERATE_MESSAGE
        rows = {}
        for name, props in (("jug", UF_JUG), ("kettle", UF_KETTLE), ("remote", UF_REMOTE)):
            out = []
            for role in ("target", "destination"):
                lab = Lab(perception="realistic", extra={"thing": _on_counter("generic_item", 0.3, **dict(props, **UNFAMILIAR))})
                lab.ent("thing").pop("pos", None)
                lab.r.strict = False
                handle = lab.env.realism.handles.handle
                if role == "target":
                    lab.hold("thing")
                    lab.go("sink")
                    res = lab.step("empty_into", handle("thing"), handle("sink"))
                else:
                    lab.hold("pan")
                    lab.go("counter")
                    res = lab.step("empty_into", handle("pan"), handle("thing"))
                out.append((res["ok"], res["category"], res["message"]))
            rows[name] = out
        for name, out in rows.items():
            self.assertEqual(out, [(False, "precondition", NOVEL_OPERATE_MESSAGE)] * 2, name)
        known = Lab(extra={"jug": _on_counter("generic_item", 0.3, label="jug", liquid_container=True, liquid="water",
                                              liquid_l=0.5, capacity_l=1.0, size_m=[0.15, 0.1, 0.25], mass_kg=0.2)})
        known.ent("jug").pop("pos", None)
        known.hold("jug")
        known.go("sink")
        self.assertTrue(known.do("empty_into", target="jug", destination="sink")["ok"])   # a thing it knows: as before
        self.assertEqual(float(known.ent("jug").get("liquid_l") or 0.0), 0.0)

    def test_the_refill_spray_bottle_comes_full_and_hefts_like_its_look_alikes(self):
        """Review L4 residual (red_leaks 8): the plain spray_bottle came empty (0.1 kg, 0 l), so a lift told it from
        the full product sprays before any label was read ('Feels light' vs 'Feels medium')."""
        from house_sim.kernel.actions import ACTIONS
        reg = default_registry()
        refill, product = reg.resolve("spray_bottle"), reg.resolve("glass_cleaner")
        self.assertEqual(refill["liquid_l"], refill["capacity_l"])
        self.assertAlmostEqual(refill["mass_kg"] + refill["liquid_l"], product["mass_kg"])
        if "support" not in ACTIONS:
            return                              # manipulation's heft (not loaded in isolation)
        felt = {}
        for type_name in ("glass_cleaner", "spray_bottle"):
            lab = Lab(perception="realistic", extra={"probe": _on_counter(type_name, 0.3, color="blue")})
            lab.ent("probe").pop("pos", None)
            lab.hold("probe")
            felt[type_name] = lab.step("support", lab.env.realism.handles.handle("probe"), mode="heft")["message"]
        self.assertEqual(felt["spray_bottle"], felt["glass_cleaner"])

    def test_every_unread_spray_bottle_inspects_closely_and_afterwards_alike(self):
        """Phase-1 twin sweep: plumbing's inspector felt the temperature of a vessel holding litres (the refill, the
        trigger bottle) on a close look and granted that touch, so those members showed vessel_feel and, from then on,
        the kernel's 'feel'; a product's bottle (an amount, not litres) showed neither."""
        reg = default_registry()
        members = [t for t in ("glass_cleaner", "mildew_remover", "spray_bottle", "trigger_bottle") if reg.known(t)]
        battery = [("inspect", {}), ("inspect", {"where": "closely"}), ("inspect", {"where": "label"}), ("inspect", {}),
                   ("pick_up", {}), ("inspect", {"where": "closely"}), ("inspect", {})]
        for camera in (None, "geometric"):
            rows = {}
            for type_name in members:
                lab = Lab(perception="realistic", camera=camera, extra={"probe": _on_counter(type_name, 0.3, color="blue")})
                lab.ent("probe").pop("pos", None)
                lab.go("counter")
                handle = lab.env.realism.handles.handle("probe")
                out = []
                for verb, kw in battery:
                    res = lab.step(verb, handle, **kw)
                    out.append((verb, res["ok"], str(res["message"]).replace(handle, "<it>")))
                rows[type_name] = out
            first = rows[members[0]]
            self.assertTrue(all(ok for _, ok, _ in first), (camera, first))
            self.assertNotIn("vessel_feel", "".join(m for _, _, m in first))
            for type_name in members[1:]:
                self.assertEqual(rows[type_name], first, (camera, type_name))

    def test_every_unread_spray_bottle_reads_in_the_same_form(self):
        """Phase-1 close-out (finish_breakages.json, the spray_bottle row; mechanisms' trigger_bottle): every member
        of the group carries the same printed field (label_text; blank on a plain bottle, never an invented label), so
        read and read_aloud answer every unread member in the same form (ok, category) -- the trigger bottle, with no
        printed field, was refused 'nothing legible' alone, which named the plain bottle before it was read."""
        from house_sim.kernel.plugins import ACTIONS, load_domains
        load_domains()
        reg = default_registry()
        members = sorted(reg.lookalike_types()["spray_bottle"])
        self.assertEqual([], [t for t in members if not isinstance(reg.resolve(t).get("label_text"), str)])
        verbs = [v for v in ("read", "read_aloud") if v in ACTIONS]       # read_aloud is people's
        for camera in (None, "geometric"):
            rows = {}
            for type_name in members:
                lab = Lab(perception="realistic", camera=camera, extra={"probe": _on_counter(type_name, 0.3, color="blue")})
                lab.ent("probe").pop("pos", None)
                lab.go("counter")
                handle = lab.env.realism.handles.handle("probe")
                out = []
                for verb in verbs:
                    unread = lab.env.realism.unresolved("probe")
                    res = lab.step(verb, handle)
                    if unread:
                        out.append((verb, res["ok"], res["category"]))
                rows[type_name] = (out, reg.resolve(type_name)["label_text"] == "")
            for type_name, (out, blank) in rows.items():
                self.assertEqual(("read", True, ""), out[0], (camera, type_name))
                if blank:       # a blank label leaves it unread: the plain members also answer read_aloud alike
                    self.assertEqual(rows["spray_bottle"][0], out, (camera, type_name))

    def test_route_probing_an_unidentified_spill_does_not_tell_its_liquid(self):
        """Review L4 / FIX2 residual: a spill whose liquid the robot cannot identify keeps routes out of it for any of
        wet / slip / sticky whatever its true floor_hazard -- through the kernel's hazard filter, every path_blocked
        hook and cleaning's visible_floor_hazard veto (which judges room wet cells only, never a spill's kind)."""
        from house_sim.kernel import geometry
        from house_sim.kernel.plugins import HOOKS
        from house_sim.systems.cleaning import floors, perception
        rows = {}
        for liquid in ("chlorine_bleach", "laundry_detergent_he", "cooking_oil", "water"):
            lab = Lab(perception="realistic")
            sid = floors.make_spill(lab.w, "kitchen", liquid, 1.2, cell=[3, 3])
            rec = lab.ent(sid)
            self.assertTrue(lab.env.realism.unresolved(sid), liquid)
            obs = lab.env.observe()
            spill_h = lab.env.realism.handles.peek(sid)
            shown = sorted({h["kind"] for h in obs["grid"]["hazards"] if h.get("source") == spill_h})
            cells = sorted(tuple(c) for c in geometry.footprint(rec))
            vetoes = [fn(lab.w.s, "kitchen", c, rec["floor_hazard"], sid)
                      for _, _, fn in HOOKS.get("visible_floor_hazard", []) for c in cells]
            self.assertEqual(set(vetoes), {None}, liquid)
            self.assertIsNone(perception.wet_floor_seen(lab.w.s, "kitchen", cells[0], rec["floor_hazard"], sid))
            row = [shown, rec["floor_hazard"] in ("wet", "slip", "sticky")]
            for avoid in (("slip",), ("wet",), ("sticky",), ("trip",)):
                kinds, blocked = lab.env.realism.route_hazards(lab.w.s, "kitchen", avoid)
                hooked = set()
                for _, _, fn in HOOKS.get("path_blocked", []):
                    args = (lab.w.s, "kitchen", geometry.expand_avoid(kinds))
                    got = fn(*(args + ((),))) if geometry._hook_takes_sources(fn) else fn(*args)
                    hooked |= {tuple(c) for c in (got or ())}
                row.append((avoid, sorted(blocked), sorted(hooked)))
            route = lab.step("walk_to", cell=[3, 6], avoid=["slip"])
            row.append((route["ok"], route["message"]))
            rows[liquid] = row
            self.assertEqual(row[2][1], cells, liquid)          # avoid slip: the whole puddle is kept out of
        kinds = {liquid: rows[liquid][0] for liquid in rows}
        self.assertEqual(set(map(tuple, kinds.values())), {("wet",)}, kinds)
        first = rows["chlorine_bleach"][2:]
        for liquid, row in rows.items():
            self.assertEqual(row[2:], first, liquid)

    # --- the time systems' fast paths (P1 gate G7): exact, so every result is unchanged ---------------------------
    def test_soak_skips_inert_holders_exactly_and_a_quiet_house_is_idle(self):
        from house_sim.generator import generate_house
        from house_sim.kernel.predicates import digest
        from house_sim.systems.cleaning import systems as Y
        bowl = _on_counter("generic_item", 0.6, label="bowl", liquid_container=True, liquid="water", liquid_l=0.8,
                           capacity_l=1.0, size_m=[0.15, 0.15, 0.06], mass_kg=0.3)
        quiet = Lab(extra={"bowl": bowl})
        self.assertEqual(Y.soak_containers(quiet.w), ["bowl"])     # a bowl of water holds litres...
        self.assertTrue(Y.soak_idle(quiet.w))   # ...but nothing in it soaks, dissolves or boils over
        self.assertFalse(Y.soak_idle(Lab(props=SINK_SOAPY).w))     # a basin of soapy water can soak things
        env = HouseEnv(generate_house(1), seed=1, trace=False)
        self.assertTrue(Y.soak_idle(env.world))  # every domain loaded: product bottles, pet bowls, a refill bottle

        def run(fast):
            saved = Y._inert
            if not fast:
                Y._inert = lambda rec: False
            try:
                extra = {"knife": ("generic_item", "sink", dict(KNIFE, relation="in")),
                         "bottle": _on_counter("bleach", 0.3), "bowl": bowl,
                         "kettle": _on_counter("generic_item", 0.9, label="kettle", liquid_container=True, container=True,
                         liquid="water", liquid_l=1.9, capacity_l=2.0, max_fill_l=1.7, temperature_c=99.0,
                         size_m=[0.2, 0.2, 0.25], mass_kg=1.0)}
                lab = Lab(props=SINK_SOAPY, extra=extra)
                lab.ent("knife").pop("pos", None)
                lab.ent("bowl").pop("pos", None)
                lab.ent("kettle").pop("pos", None)
                lab.ent("bottle").pop("pos", None)
                for seconds in (60, 900, 30, 3600):
                    lab.wait(seconds)
                return digest(lab.w.s), lab.log("boil_over"), lab.ent("knife").get("hidden_in_water")
            finally:
                Y._inert = saved
        fast, full = run(True), run(False)
        self.assertEqual(fast, full)
        self.assertTrue(fast[1] and fast[2])    # the kettle boiled over and the knife hid under the suds either way

    def test_waste_finds_food_from_the_scraps_up_exactly_as_a_walk_of_every_bin(self):
        from house_sim.kernel.predicates import digest
        from house_sim.systems.cleaning import systems as Y

        def walk(w, holders):
            ents = w.s["entities"]
            out = {}
            for holder in holders:
                stack, found = [holder], []
                while stack:
                    parent = stack.pop()
                    for eid in sorted(e for e, r in ents.items() if r.get("location") == parent):
                        found.append(eid)
                        stack.append(eid)
                for eid in found:
                    if ents[eid].get("waste_kind") == "food_scrap":
                        ftype = ents[eid].get("food_type", "plant")
                        old = out.get(holder, (0.0, False))
                        out[holder] = (max(old[0], Y.FOOD_RATES.get(ftype, 0.3)),
                                       old[1] or ftype in ("plant", "fruit", "vegetable", "peel"))
            return out

        def run(fast):
            saved = Y._food_inside
            if not fast:
                Y._food_inside = walk
            try:
                bagged = {"opened": True, "accepts_items": True, "container": True, "portable": True}
                extra = {"bones": ("chicken_bones", "can", {"relation": "in"}),
                         "peel": ("fruit_peel", "can", {"relation": "in"}),
                         "bag": ("trash_liner_13gal", "kitchen", dict(bagged, pos=[5, 5])),       # a bag left out
                         "fish": ("fish_scraps", "bag", {"relation": "in"}),
                         "caddy": ("compost_caddy", "counter", {"relation": "on", "spos": [1.5, 0.1]}),
                         "grounds": ("coffee_grounds", "caddy", {"relation": "in"}),
                         "cart_bag": ("trash_liner_13gal", "trash_cart", dict(bagged, relation="in")),
                         "meat": ("meat_scraps", "cart_bag", {"relation": "in"})}
                lab = Lab(extra=extra, clock="2026-10-09T19:00:00",
                          household={"raccoons": True})
                lab.w.s["pets"] = {"rex": {"species": "dog", "label": "Rex", "location": "kitchen", "pos": [6, 5],
                                           "posture": "standing"}}
                lab.ent("trash_cart")["open"] = True
                for seconds in (600, 1800, 3 * 3600, 20 * 3600):
                    lab.wait(seconds)
                return digest(lab.w.s), odor_level(lab.ent("can"), "garbage"), lab.log("scatter")
            finally:
                Y._food_inside = saved
        fast, full = run(True), run(False)
        self.assertEqual(fast[0], full[0])
        self.assertGreater(fast[1], 0.0)


if __name__ == "__main__":
    unittest.main()
